#!/usr/bin/env python3
"""Run a disposable two-backend Gateway experiment in the dedicated lab."""
import argparse
from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import selectors
import signal
import socket
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.gateway_warehouse import support as C
from tools.local_experiment.gateway_warehouse import manifest as M
from tools.local_experiment.gateway_warehouse import fixtures, build, persistence
from tools.local_experiment.gateway_warehouse.client import Client, CASES
from tools.local_experiment.local_experiment import check_context

KINDS = {'NetworkPolicy': ('networking.k8s.io/v1', 'networkpolicies'),
         'ConfigMap': ('v1', 'configmaps'), 'Secret': ('v1', 'secrets'), 'Pod': ('v1', 'pods')}
PORTS = (18580, 18581, 18582)


def stamp():
    return datetime.now(timezone.utc).isoformat()


def identity(obj):
    return obj['kind'] + '/' + obj['metadata']['name']


def created_uid(current, expected):
    meta = current.get('metadata', {})
    uid = meta.get('uid')
    C.require(type(uid) is str and str(uuid.UUID(uid)) == uid, 'CREATE_UID_INVALID')
    C.require(current.get('apiVersion') == expected['apiVersion'] and current.get('kind') == expected['kind']
              and meta.get('name') == expected['metadata']['name'] and meta.get('namespace') == C.NAMESPACE
              and meta.get('labels', {}).get(M.OWNER) == expected['metadata']['labels'][M.OWNER]
              and meta.get('annotations', {}).get(M.HASH) == expected['metadata']['annotations'][M.HASH],
              'CREATE_IDENTITY_MISMATCH')
    return uid


class Kubernetes:
    def request(self, args, body=None, timeout=40):
        return C.command(['kubectl', '--context', C.CONTEXT, '-n', C.NAMESPACE, '--request-timeout=30s', *args],
                         body=C.canonical(body) if body is not None else None, timeout=timeout)

    def get(self, obj):
        raw = self.request(['get', obj['kind'], obj['metadata']['name'], '--ignore-not-found=true', '-o', 'json'])
        return json.loads(raw) if raw.strip() else None

    def dry_run(self, obj):
        self.request(['create', '--dry-run=server', '-f', '-', '-o', 'json'], obj)

    def create(self, obj):
        return json.loads(self.request(['create', '-f', '-', '-o', 'json'], obj))

    def delete(self, obj, current):
        version, resource = KINDS[obj['kind']]
        path = ('/api/' if version == 'v1' else '/apis/') + version + '/namespaces/' + C.NAMESPACE + '/' + resource + '/' + obj['metadata']['name']
        self.request(['delete', '--raw=' + path, '-f', '-'], {'apiVersion': 'v1', 'kind': 'DeleteOptions',
            'propagationPolicy': 'Orphan', 'gracePeriodSeconds': 15 if obj['kind'] == 'Pod' else 0,
            'preconditions': {'uid': current['metadata']['uid'], 'resourceVersion': current['metadata']['resourceVersion']}})

    def logs(self, pod, component):
        return self.request(['logs', pod['metadata']['name'], '-c', component, '--limit-bytes=1048576', '--tail=1200'])


def available_ports():
    for port in PORTS:
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', port))
            except OSError:
                raise C.ExperimentError('TUNNEL_PORT_COLLISION') from None


@contextmanager
def tunnel(pod):
    available_ports()
    process = subprocess.Popen(['kubectl', '--context', C.CONTEXT, '-n', C.NAMESPACE, 'port-forward',
        'pod/' + pod['metadata']['name'], *[str(port) + ':' + str(port) for port in PORTS], '--address=127.0.0.1'],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
        env={key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR', 'USER', 'LOGNAME') if key in os.environ})
    selector, data, deadline = selectors.DefaultSelector(), b'', time.monotonic() + 30
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        while time.monotonic() < deadline:
            C.require(process.poll() is None, 'TUNNEL_EXITED')
            if selector.select(timeout=0.5):
                data += os.read(process.stdout.fileno(), 4096)
                C.require(len(data) <= 8192, 'TUNNEL_OUTPUT_LIMIT')
                if all(('Forwarding from 127.0.0.1:' + str(port) + ' -> ' + str(port)).encode() in data for port in PORTS):
                    yield
                    return
        raise C.ExperimentError('TUNNEL_NOT_READY')
    finally:
        selector.close()
        C.stop_group(process, signal.SIGTERM, 5)
        process.stdout.close()


def wait_ready(kube, pod, uid):
    wanted = {container['name'] for container in pod['spec']['containers']}
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        current = kube.get(pod)
        C.require(current is not None, 'POD_DISAPPEARED')
        M.assert_owned(current, pod, uid)
        C.require(current.get('status', {}).get('phase') not in {'Failed', 'Succeeded'}, 'POD_TERMINATED')
        statuses = current.get('status', {}).get('containerStatuses', [])
        if len(statuses) == 4 and {item['name'] for item in statuses} == wanted and all(
                item.get('ready') is True and item.get('restartCount') == 0 for item in statuses):
            return {'observed_at': stamp(), 'pod_uid': uid, 'ready': sorted(wanted), 'restarts': 0}
        time.sleep(1)
    raise C.ExperimentError('POD_NOT_READY')


def cleanup(kube, receipt, objects, save):
    receipt['cleanup'] = {'verified': False, 'removed': [], 'conflicts': [], 'deferred': []}
    for obj in sorted(objects, key=lambda value: {'Pod': 0, 'Secret': 1, 'ConfigMap': 2, 'NetworkPolicy': 3}[value['kind']]):
        record = next((row for row in receipt['objects'] if row['key'] == identity(obj)), None)
        if record is None:
            continue
        if record.get('uid') is None:
            receipt['cleanup']['deferred'].append(identity(obj))
            save()
            if obj['kind'] == 'Pod':
                break
            continue
        try:
            current = kube.get(obj)
            if current is None:
                continue
            M.assert_owned(current, obj, record['uid'])
            kube.delete(obj, current)
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                current = kube.get(obj)
                if current is None:
                    break
                M.assert_owned(current, obj, record['uid'])
                time.sleep(0.25)
            else:
                raise C.ExperimentError('DELETE_UNVERIFIED')
            receipt['cleanup']['removed'].append({'key': identity(obj), 'uid': record['uid']})
        except Exception:
            receipt['cleanup']['conflicts'].append(identity(obj))
            save()
            if obj['kind'] == 'Pod':
                break
        save()
    receipt['cleanup']['verified'] = not receipt['cleanup']['conflicts'] and not receipt['cleanup']['deferred']
    save()


def redact(raw, material):
    # Synthetic runtime credentials are still excluded from diagnostic output.
    for secret in material.get('redaction_values', []):
        if type(secret) is str and len(secret) >= 8:
            raw = raw.replace(secret, '[REDACTED]')
    return re.sub(r'(?i)(password\s*[=:]\s*)[^\s,;]+', r'\1[REDACTED]', raw)


def verify_client(value, run_token):
    """Keep the runner's success boundary explicit across client API changes."""
    C.require(value.get('run_token') == run_token and value.get('result') == 'verified'
              and value.get('pending') is None and value.get('uncaptured_requests') == 0
              and value.get('query_cleanup_verified') is True, 'CLIENT_INCOMPLETE')
    registration = value.get('registration') or {}
    attempts = registration.get('attempts', [])
    C.require(registration.get('result') == 'verified' and registration.get('initial_registry_empty') is True
              and registration.get('readback_verified') is True and value.get('unconfirmed_registrations') == 0
              and len(attempts) == 2 and all(row.get('result') == 'confirmed' and row.get('http_status') == 200
                                          for row in attempts), 'CLIENT_REGISTRATION_INCOMPLETE')
    cases = value.get('cases', [])
    C.require([row.get('case') for row in cases] == [row[0] for row in CASES]
              and all(row.get('matched') is True and row.get('response_complete') is True for row in cases)
              and all(row.get('opposite_route_gets', 0) > 0 for row in cases if row.get('case') in {'xs_before', 's_before'}),
              'CLIENT_CASES_INCOMPLETE')
    history = value.get('history') or {}
    C.require(history.get('verified') is True and history.get('query_count') == 6
              and set(history.get('backends', {})) == {'xs', 's'}
              and all(len(rows) == 3 for rows in history['backends'].values())
              and history.get('unknown_source_counts') == {'xs': 0, 's': 0}
              and history.get('missing_source_counts') == {'xs': 0, 's': 0}, 'CLIENT_HISTORY_INCOMPLETE')
    requests = value.get('requests', [])
    C.require(len(requests) >= 10 and all(row.get('response_complete') is True for row in requests),
              'CLIENT_REQUESTS_INCOMPLETE')


def execute(plan, material, output, *, kube=None, tunnel_factory=tunnel, client_factory=Client,
            persistence_factory=None, port_check=available_ports):
    objects, kube = M.validate_plan(plan), kube or Kubernetes()
    receipt = {'schema_version': 1, 'run_token': plan['run_token'], 'build_token': plan['build_token'],
        'result': 'running', 'observed_at': stamp(), 'scope': 'isolated-gateway-two-trino-routing',
        'images': plan['images'], 'plan_sha256': C.digest(plan), 'objects': [], 'pending': None,
        'readiness': [], 'observations': None, 'database': None, 'cleanup': None,
        'unobserved': ['authentication', 'tenant-isolation', 'high-availability', 'autoscaling',
                       'idle-suspend-wake', 'cost-explain', 'production-readiness']}
    def save():
        C.write(output / 'receipt.json', receipt)
    def observe(value):
        receipt['observations'] = copy.deepcopy(value)
        save()
    save()
    try:
        port_check()
        for obj in objects:
            C.require(kube.get(obj) is None, 'OBJECT_NAME_COLLISION')
            kube.dry_run(obj)
        for obj in objects:
            record = {'key': identity(obj), 'uid': None, 'content_sha256': obj['metadata']['annotations'][M.HASH]}
            receipt['objects'].append(record)
            receipt['pending'] = {'operation': 'create', 'key': identity(obj)}
            save()
            current = kube.create(obj)
            record['uid'] = created_uid(current, obj)
            save()
            M.assert_owned(current, obj, record['uid'])
            receipt['pending'] = None
            save()
        pod, pod_uid = objects[-1], receipt['objects'][-1]['uid']
        def guard():
            current = kube.get(pod)
            C.require(current is not None, 'POD_DISAPPEARED')
            M.assert_owned(current, pod, pod_uid)
            return current
        receipt['readiness'].append(wait_ready(kube, pod, pod_uid))
        save()
        with tunnel_factory(pod):
            guard()
            client = client_factory(plan['run_token'])
            receipt['pending'] = {'operation': 'fixed-backend-registration'}
            save()
            client.register_backends(progress=observe, guard=guard)
            receipt['readiness'].append(client.ready())
            receipt['pending'] = {'operation': 'fixed-query-cases'}
            save()
            client.run_cases(progress=observe)
            receipt['pending'] = {'operation': 'backend-query-history-verification'}
            save()
            client.verify_history(progress=observe)
            observe(client.receipt())
            verify_client(receipt['observations'], plan['run_token'])
            receipt['pending'] = {'operation': 'database-backup-and-restore'}
            save()
            operation = persistence_factory or persistence.verify_backup_restore
            receipt['database'] = operation(plan['run_token'], pod, pod_uid, output, guard=guard)
            C.require(receipt['database'].get('result') == 'passed', 'DATABASE_VERIFICATION_FAILED')
            receipt['readiness'].append(client.ready())
            observe(client.receipt())
            verify_client(receipt['observations'], plan['run_token'])
            receipt['readiness'].append(wait_ready(kube, pod, pod_uid))
            receipt['pending'], receipt['result'] = None, 'verified'
            save()
    except BaseException as error:
        receipt['result'] = 'failed' if isinstance(error, Exception) else 'interrupted'
        receipt['failure_code'] = str(error) if isinstance(error, C.ExperimentError) else 'WAREHOUSE_EXPERIMENT_FAILED'
        receipt['diagnostics'] = []
        save()
        record = next((row for row in receipt['objects'] if row['key'] == identity(objects[-1])), None)
        if record and record.get('uid'):
            try:
                M.assert_owned(kube.get(objects[-1]), objects[-1], record['uid'])
                for component in [item['name'] for item in objects[-1]['spec']['containers']]:
                    raw = redact(kube.logs(objects[-1], component), material)
                    C.write(output / ('diagnostic-' + component + '.json'), {'pod_uid': record['uid'], 'redacted_log': raw})
                    receipt['diagnostics'].append({'component': component, 'sha256': C.digest(raw.encode())})
            except Exception:
                receipt['diagnostics'].append({'result': 'unavailable'})
        save()
        raise
    finally:
        cleanup(kube, receipt, objects, save)
        if receipt['result'] == 'verified':
            receipt['result'] = 'passed' if receipt['cleanup']['verified'] else 'failed'
        save()
    C.require(receipt['result'] == 'passed', 'CLEANUP_UNVERIFIED')
    return receipt


def recover(run_token, kube=None):
    output = C.STATE / 'runs' / C.token(run_token)
    with C.run_lock(output):
        plan, original = json.loads(C.read(output / 'plan.json')), json.loads(C.read(output / 'receipt.json'))
        C.require(plan['run_token'] == original['run_token'] == run_token and plan['build_token'] == original['build_token']
                  and original['images'] == plan['images'] and original['plan_sha256'] == C.digest(plan), 'RECOVERY_IDENTITY_MISMATCH')
        objects = M.validate_plan(plan)
        allowed = {identity(obj): obj for obj in objects}
        C.require(type(original['objects']) is list and len(original['objects']) <= 4
                  and len({row['key'] for row in original['objects']}) == len(original['objects'])
                  and all(row['key'] in allowed and row['content_sha256'] == allowed[row['key']]['metadata']['annotations'][M.HASH]
                          for row in original['objects']), 'RECOVERY_OBJECTS_INVALID')
        receipt = {'schema_version': 1, 'run_token': run_token, 'original_result': original['result'],
                   'objects': copy.deepcopy(original['objects']), 'cleanup': None}
        def save():
            C.write(output / 'recovery.json', receipt)
        cleanup(kube or Kubernetes(), receipt, objects, save)
        C.require(receipt['cleanup']['verified'], 'RECOVERY_UNVERIFIED')
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('plan', 'run', 'recover'))
    parser.add_argument('--build-token')
    parser.add_argument('--run-token')
    args = parser.parse_args()
    run_token = None
    try:
        if args.command == 'recover':
            C.require(args.run_token and not args.build_token, 'RECOVERY_ARGUMENTS')
            run_token = C.token(args.run_token)
            check_context()
            with C.interruption_handlers():
                recover(run_token)
        else:
            C.require(args.build_token and not args.run_token, 'RUN_ARGUMENTS')
            images = build.load_build(args.build_token)['images']
            run_token, output = C.new_output('runs')
            with C.run_lock(output), C.interruption_handlers():
                material = fixtures.create_fixtures(run_token, output)
                plan = {'schema_version': 1, 'run_token': run_token, 'build_token': args.build_token,
                        'images': images, 'objects': M.render(images, run_token, material)}
                M.validate_plan(plan)
                C.write(output / 'plan.json', plan)
                if args.command == 'run':
                    check_context()
                    execute(plan, material, output)
        print(C.canonical({'result': 'passed', 'mode': args.command, 'run_token': run_token,
                           'evidence': 'tmp/local-experiment/gateway-warehouse/runs/' + run_token}))
        return 0
    except BaseException as error:
        print(C.canonical({'result': 'failed', 'run_token': run_token,
                           'failure_code': str(error) if isinstance(error, C.ExperimentError) else 'WAREHOUSE_EXPERIMENT_FAILED'}))
        return 128 + error.signum if hasattr(error, 'signum') else 1


if __name__ == '__main__':
    raise SystemExit(main())
