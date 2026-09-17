#!/usr/bin/env python3
"""Owned Deployment lifecycle exercise; retained workloads are outside its scope."""
import argparse
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
from tools.local_experiment.warehouse_lifecycle import support as C, manifest as M, fixtures
from tools.local_experiment.warehouse_lifecycle.client import Client
from tools.local_experiment.warehouse_lifecycle.controller import Controller
from tools.local_experiment.gateway_warehouse import build
from tools.local_experiment.gateway_warehouse.run import redact
from tools.local_experiment.local_experiment import check_context

KINDS = {'NetworkPolicy': ('networking.k8s.io/v1', 'networkpolicies'),
         'ConfigMap': ('v1', 'configmaps'), 'Secret': ('v1', 'secrets'), 'Pod': ('v1', 'pods'),
         'Deployment': ('apps/v1', 'deployments'), 'ReplicaSet': ('apps/v1', 'replicasets'),
         'Service': ('v1', 'services'), 'EndpointSlice': ('discovery.k8s.io/v1', 'endpointslices')}
PORTS = {'gateway': 18580, 'xs': 18581, 's': 18582}


def stamp():
    return datetime.now(timezone.utc).isoformat()


def key(obj):
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


def object_ref(kind, name):
    return {'apiVersion': KINDS[kind][0], 'kind': kind, 'metadata': {'name': name, 'namespace': C.NAMESPACE}}


def verify_controller(value, run_token):
    C.require(value.get('run_token') == run_token and value.get('result') == 'verified'
              and value.get('pending') is None and all(value.get(name) is True for name in
                  ('busy_defer_verified', 'chill_verified', 'wake_verified')), 'CONTROLLER_UNVERIFIED')
    client = value.get('client') or {}
    C.require(client.get('result') == 'verified' and client.get('pending') is None
              and client.get('ledger', {}).get('all_results_consumed') is True
              and client['ledger'].get('pending_handles') == client['ledger'].get('uncaptured_requests') == 0,
              'CLIENT_UNVERIFIED')
    C.require(len(client.get('requests', [])) == 7 and all(row.get('response_complete') is True for row in client['requests'])
              and [row.get('operation') for row in client.get('controls', [])] == ['deactivate', 'activate']
              and all(row.get('result') == 'verified' for row in client['controls']), 'LIFECYCLE_CASES_INCOMPLETE')
    C.require(len(value.get('scale_operations', [])) == 2
              and all(row.get('result') == 'verified' for row in value['scale_operations']), 'CONTROLLER_SCALE_INCOMPLETE')


class Kubernetes:
    def request(self, args, body=None, timeout=40):
        return C.command(['kubectl', '--context', C.CONTEXT, '-n', C.NAMESPACE, '--request-timeout=30s', *args],
                         body=C.canonical(body) if body is not None else None, timeout=timeout)

    def get(self, obj):
        raw = self.request(['get', obj['kind'], obj['metadata']['name'], '--ignore-not-found=true', '-o', 'json'])
        return json.loads(raw) if raw.strip() else None

    def children(self, run_token):
        value = json.loads(self.request(['get', 'replicasets,pods', '-l', M.OWNER + '=' + run_token, '-o', 'json']))
        C.require(type(value.get('items')) is list and len(value['items']) <= 32, 'CHILD_INVENTORY_LIMIT')
        return value['items']

    def endpoints(self, service):
        value = json.loads(self.request(['get', 'endpointslices', '-l',
            'kubernetes.io/service-name=' + service['metadata']['name'], '-o', 'json']))
        C.require(type(value.get('items')) is list and len(value['items']) <= 4, 'ENDPOINT_INVENTORY_LIMIT')
        return value['items']

    def dry_run(self, obj):
        self.request(['create', '--dry-run=server', '-f', '-', '-o', 'json'], obj)

    def create(self, obj):
        return json.loads(self.request(['create', '-f', '-', '-o', 'json'], obj))

    def scale(self, current, expected, uid, replicas):
        patch = M.scale_patch(current, expected, uid, replicas)
        return json.loads(self.request(['patch', 'deployment', expected['metadata']['name'],
            '--type=json', '-p', C.canonical(patch), '-o', 'json']))

    def delete(self, obj, current):
        version, resource = KINDS[obj['kind']]
        path = ('/api/' if version == 'v1' else '/apis/') + version + '/namespaces/' + C.NAMESPACE + '/' + resource + '/' + obj['metadata']['name']
        self.request(['delete', '--raw=' + path, '-f', '-'], {'apiVersion': 'v1', 'kind': 'DeleteOptions',
            'propagationPolicy': 'Orphan', 'gracePeriodSeconds': 15 if obj['kind'] == 'Pod' else 0,
            'preconditions': {'uid': current['metadata']['uid'], 'resourceVersion': current['metadata']['resourceVersion']}})

    def logs(self, pod, component):
        return self.request(['logs', pod['metadata']['name'], '-c', component, '--limit-bytes=1048576', '--tail=800'])


def available_port(port):
    with socket.socket() as probe:
        # Match the forwarding listener's address reuse: a closed previous
        # tunnel can leave TIME_WAIT connections without a live listener.
        # SO_REUSEPORT stays disabled, so an occupied listener still fails.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(('127.0.0.1', port))
        except OSError:
            raise C.ExperimentError('TUNNEL_PORT_COLLISION') from None


def available_ports():
    for port in PORTS.values():
        available_port(port)


class Forwarders:
    def __init__(self):
        self.processes = {}

    def close_one(self, size):
        process = self.processes.pop(size, None)
        if process is not None:
            C.stop_group(process, signal.SIGTERM, 5)
            process.stdout.close()

    def attach(self, size, pod):
        C.require(size in PORTS, 'TUNNEL_SCOPE_INVALID')
        self.close_one(size)
        port = PORTS[size]
        available_port(port)
        process = subprocess.Popen(['kubectl', '--context', C.CONTEXT, '-n', C.NAMESPACE, 'port-forward',
            'pod/' + pod['metadata']['name'], str(port) + ':' + str(port), '--address=127.0.0.1'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
            env={name: os.environ[name] for name in ('PATH', 'HOME', 'TMPDIR', 'USER', 'LOGNAME') if name in os.environ})
        self.processes[size] = process
        selector, data, deadline = selectors.DefaultSelector(), b'', time.monotonic() + 30
        selector.register(process.stdout, selectors.EVENT_READ)
        try:
            while time.monotonic() < deadline:
                C.require(process.poll() is None, 'TUNNEL_EXITED')
                if selector.select(timeout=0.5):
                    data += os.read(process.stdout.fileno(), 4096)
                    C.require(len(data) <= 8192, 'TUNNEL_OUTPUT_LIMIT')
                    if ('Forwarding from 127.0.0.1:' + str(port) + ' -> ' + str(port)).encode() in data:
                        return
            raise C.ExperimentError('TUNNEL_NOT_READY')
        finally:
            selector.close()

    def close(self):
        for size in list(self.processes):
            self.close_one(size)


class Lab:
    def __init__(self, plan, receipt, save, kube, forwards):
        self.plan, self.receipt, self.save, self.kube, self.forwards = plan, receipt, save, kube, forwards
        self.objects = M.validate_plan(plan)
        self.deployments = {size: next(obj for obj in self.objects if obj['kind'] == 'Deployment'
                            and obj['metadata']['name'].endswith('-' + size)) for size in ('xs', 's')}
        self.gateway = next(obj for obj in self.objects if obj['kind'] == 'Pod')
        self.services = {size: next(obj for obj in self.objects if obj['kind'] == 'Service'
                         and obj['metadata']['name'].endswith('-' + size)) for size in ('xs', 's')}
        self.desired = {'xs': 1, 's': 1}
        self.pods = {}

    def record(self, obj):
        value = next((row for row in self.receipt['objects'] if row['key'] == key(obj)), None)
        C.require(value and value.get('uid'), 'OBJECT_UID_UNKNOWN')
        return value

    def current(self, obj):
        value = self.kube.get(obj)
        C.require(value is not None, 'OBJECT_DISAPPEARED')
        M.assert_owned(value, self.expected(obj), self.record(obj)['uid'])
        return value

    def expected(self, obj, replicas=None):
        result = copy.deepcopy(obj)
        if obj['kind'] == 'Deployment':
            size = next(size for size, value in self.deployments.items() if value == obj)
            replicas = self.desired[size] if replicas is None else replicas
            C.require(type(replicas) is int and replicas in (0, 1), 'REPLICAS_INVALID')
            result['spec']['replicas'] = replicas
        return result

    def capture_children(self):
        items = self.kube.children(self.plan['run_token'])
        gateway_name = self.gateway['metadata']['name']
        replica_sets = [row for row in items if row['kind'] == 'ReplicaSet']
        pods = [row for row in items if row['kind'] == 'Pod' and row['metadata']['name'] != gateway_name]
        captured, all_children = {}, []
        for size, obj in self.deployments.items():
            current = self.current(obj)
            # A controller update may only change replicas through this journaled driver.
            C.require(current['spec']['replicas'] == self.desired[size], 'REPLICAS_CHANGED')
            dep_uid = self.record(obj)['uid']
            owned_sets = [row for row in replica_sets if any(owner.get('uid') == dep_uid
                          for owner in row['metadata'].get('ownerReferences', []))]
            owned_set_uids = {row['metadata']['uid'] for row in owned_sets}
            owned_pods = [row for row in pods if any(owner.get('uid') in owned_set_uids
                          for owner in row['metadata'].get('ownerReferences', []))]
            result = M.child_inventory(self.expected(obj), current, owned_sets, owned_pods, deployment_uid=dep_uid)
            captured[size] = result
            for child in result['children']:
                child = dict(child, size=size)
                all_children.append(child)
                previous = next((row for row in self.receipt['children'] if row['uid'] == child['uid']), None)
                if previous is None:
                    self.receipt['children'].append(child)
                else:
                    C.require(all(previous[k] == child[k] for k in ('kind', 'name', 'uid', 'parent_uid', 'template_hash', 'size')),
                              'CHILD_JOURNAL_CONFLICT')
            self.save()  # Child UIDs must be durable before any scale or parent deletion.
        C.require({row['uid'] for row in all_children} == {row['metadata']['uid'] for row in replica_sets + pods},
                  'UNOWNED_LABELED_CHILD')
        return captured

    def guard(self):
        for obj in self.objects:
            self.current(obj)
        captured = self.capture_children()
        result = {}
        for size, group in captured.items():
            pods = [row for row in group['children'] if row['kind'] == 'Pod']
            C.require(len(pods) == self.desired[size], 'BACKEND_POD_COUNT')
            self.pods[size] = object_ref('Pod', pods[0]['name']) if pods else None
            result[size] = {'deployment_uid': self.record(self.deployments[size])['uid'],
                           'pod_uid': pods[0]['uid'] if pods else None, 'replicas': self.desired[size]}
        self.capture_endpoints(ready=True)
        return result

    def capture_endpoints(self, *, ready=False):
        verified = []
        for size, service in self.services.items():
            record = next((row for row in self.receipt['objects'] if row['key'] == key(service)), None)
            if record is None:
                continue
            C.require(record.get('uid'), 'SERVICE_UID_UNKNOWN')
            current = self.kube.get(service)
            if current is None:
                continue  # Recovery may begin after this recorded parent was removed.
            M.assert_owned(current, service, record['uid'])
            slices = self.kube.endpoints(service)
            pod_uids = [row['uid'] for row in self.receipt['children'] if row['kind'] == 'Pod' and row['size'] == size]
            for item in slices:
                M.assert_endpoint(item, service, record['uid'], item['metadata']['uid'], pod_uids=pod_uids)
                value = {'kind': 'EndpointSlice', 'name': item['metadata']['name'], 'uid': item['metadata']['uid'],
                         'resource_version': item['metadata']['resourceVersion'], 'parent_uid': record['uid'], 'size': size}
                previous = next((row for row in self.receipt['endpoints'] if row['uid'] == value['uid']), None)
                if previous is None:
                    self.receipt['endpoints'].append(value)
                else:
                    C.require(all(previous[k] == value[k] for k in ('kind', 'name', 'uid', 'parent_uid', 'size')),
                              'ENDPOINT_JOURNAL_CONFLICT')
                self.save()
            if ready and self.desired[size] == 1:
                C.require(self.pods.get(size) is not None, 'ENDPOINT_POD_NOT_READY')
                owned_pod = self.kube.get(self.pods[size])
                known = next(row for row in self.receipt['children'] if row['kind'] == 'Pod'
                             and row['name'] == self.pods[size]['metadata']['name'])
                M.assert_child(owned_pod, self.deployments[size], known['parent_uid'], known['uid'])
                verified.append(M.validate_endpoints(service, current, record['uid'], slices, owned_pod))
            elif ready:
                C.require(all(item.get('endpoints') in (None, []) for item in slices), 'SUSPENDED_ENDPOINT_REMAINS')
        return verified

    def wait_ready(self, sizes=('xs', 's'), timeout=240):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            captured = self.capture_children()
            okay = True
            for size in sizes:
                group = captured[size]
                pods = [row for row in group['children'] if row['kind'] == 'Pod']
                okay = okay and len(pods) == self.desired[size]
                for pod in pods:
                    raw = self.kube.get(object_ref('Pod', pod['name']))
                    if raw is None and self.desired[size] == 0:
                        continue  # Expected deletion may complete between LIST and GET.
                    C.require(raw is not None and raw['metadata']['uid'] == pod['uid'], 'CHILD_UID_CHANGED')
                    statuses = raw.get('status', {}).get('containerStatuses', [])
                    if self.desired[size] == 1:
                        C.require(raw.get('status', {}).get('phase') not in {'Failed', 'Succeeded'}, 'BACKEND_POD_FAILED')
                    okay = okay and len(statuses) == 1 and all(row.get('ready') is True and row.get('restartCount') == 0 for row in statuses)
            gateway = self.current(self.gateway)
            statuses = gateway.get('status', {}).get('containerStatuses', [])
            C.require(gateway.get('status', {}).get('phase') not in {'Failed', 'Succeeded'}, 'GATEWAY_POD_FAILED')
            okay = okay and len(statuses) == 2 and all(row.get('ready') is True and row.get('restartCount') == 0 for row in statuses)
            if okay:
                try:
                    result = self.guard()
                except C.ExperimentError as error:
                    if str(error) not in {'ENDPOINT_SLICE_COUNT_INVALID', 'ENDPOINT_COUNT_INVALID',
                                          'ENDPOINT_POD_NOT_READY', 'ENDPOINT_TARGET_CONFLICT', 'SUSPENDED_ENDPOINT_REMAINS'}:
                        raise
                    time.sleep(0.25)
                    continue
                self.receipt['readiness'].append({'observed_at': stamp(), 'backends': result, 'gateway_uid': gateway['metadata']['uid']})
                self.save()
                return result
            time.sleep(0.5)
        raise C.ExperimentError('LIFECYCLE_NOT_READY')

    def scale(self, size, replicas):
        C.require(size == 'xs' and type(replicas) is int and replicas in (0, 1), 'SCALE_SCOPE_INVALID')
        before = self.guard()[size]
        C.require(before['replicas'] != replicas, 'SCALE_SEQUENCE_INVALID')
        obj = self.deployments[size]
        current = self.current(obj)
        self.receipt['pending'] = {'operation': 'scale', 'size': size, 'from': before['replicas'], 'to': replicas,
                                   'deployment_uid': before['deployment_uid'], 'resource_version': current['metadata']['resourceVersion']}
        self.save()
        result = self.kube.scale(current, self.expected(obj), before['deployment_uid'], replicas)
        M.assert_owned(result, self.expected(obj, replicas), before['deployment_uid'])
        C.require(result['spec']['replicas'] == replicas, 'SCALE_RESPONSE_MISMATCH')
        self.desired[size] = replicas
        self.receipt['scales'].append({'observed_at': stamp(), 'size': size, 'from': before['replicas'], 'to': replicas,
                                      'deployment_uid': before['deployment_uid'], 'old_pod_uid': before['pod_uid']})
        self.save()
        if replicas == 0:
            self.forwards.close_one(size)
        after = self.wait_ready()[size]
        C.require(replicas == 0 or after['pod_uid'] != self.receipt['scales'][-2]['old_pod_uid'], 'WAKE_REUSED_POD')
        self.receipt['scales'][-1]['new_pod_uid'] = after['pod_uid']
        self.receipt['pending'] = None
        self.save()
        return after

    def reconnect(self, size):
        C.require(size in ('xs', 's'), 'TUNNEL_SCOPE_INVALID')
        self.guard()
        C.require(self.pods[size] is not None, 'BACKEND_SUSPENDED')
        self.forwards.attach(size, self.pods[size])
        self.guard()

    def diagnostics(self, output, material):
        records = []
        candidates = [(self.gateway, None)] + [(object_ref('Pod', row['name']), row)
            for row in self.receipt['children'] if row['kind'] == 'Pod']
        for pod, child in candidates:
            try:
                current = self.kube.get(pod)
                if current is None:
                    continue
                if child:
                    M.assert_child(current, self.deployments[child['size']], child['parent_uid'], child['uid'])
                else:
                    M.assert_owned(current, pod, self.record(pod)['uid'])
                for component in current['spec']['containers']:
                    raw = redact(self.kube.logs(pod, component['name']), material)
                    path = output / ('diagnostic-' + pod['metadata']['name'] + '-' + component['name'] + '.json')
                    C.write(path, {'pod_uid': current['metadata']['uid'], 'redacted_log': raw})
                    records.append({'file': path.name, 'sha256': C.digest(raw.encode())})
            except Exception:
                records.append({'pod': pod['metadata']['name'], 'result': 'unavailable'})
        self.receipt['diagnostics'] = records
        self.save()

    def cleanup(self):
        state = self.receipt['cleanup'] = {'verified': False, 'removed': [], 'conflicts': [], 'deferred': []}
        self.save()
        # Capture controller-owned children while parent owner references still exist.
        created_deps = [obj for obj in self.deployments.values() if any(row['key'] == key(obj) for row in self.receipt['objects'])]
        if created_deps:
            if any(not next(row for row in self.receipt['objects'] if row['key'] == key(obj)).get('uid') for obj in created_deps):
                state['deferred'].append('unknown-deployment-create')
                self.save()
                return
            try:
                if len(created_deps) == 2 and all(self.kube.get(obj) is not None for obj in created_deps):
                    # Cleanup accepts either replica count after an uncertain patch response.
                    for size, obj in self.deployments.items():
                        current = self.kube.get(obj)
                        M.assert_owned(current, self.expected(obj, current['spec']['replicas']), self.record(obj)['uid'])
                        self.desired[size] = current['spec']['replicas']
                    self.capture_children()
                else:
                    # Partial creation: inspect each known parent separately before removal.
                    items = self.kube.children(self.plan['run_token'])
                    for obj in created_deps:
                        current = self.kube.get(obj)
                        if current is None:
                            continue
                        uid = self.record(obj)['uid']
                        sets = [row for row in items if row['kind'] == 'ReplicaSet' and any(
                            owner.get('uid') == uid for owner in row['metadata'].get('ownerReferences', []))]
                        set_uids = {row['metadata']['uid'] for row in sets}
                        pods = [row for row in items if row['kind'] == 'Pod' and any(
                            owner.get('uid') in set_uids for owner in row['metadata'].get('ownerReferences', []))]
                        group = M.child_inventory(self.expected(obj, current['spec']['replicas']), current, sets, pods, deployment_uid=uid)
                        size = next(size for size, expected in self.deployments.items() if expected == obj)
                        for child in group['children']:
                            if not any(row['uid'] == child['uid'] for row in self.receipt['children']):
                                self.receipt['children'].append(dict(child, size=size))
                    self.save()
                self.capture_endpoints()
            except Exception:
                state['conflicts'].append('child-inventory')
                self.save()
                return
        else:
            try:
                self.capture_endpoints()
            except Exception:
                state['conflicts'].append('endpoint-inventory')
                self.save()
                return
        # Parent first with Orphan, then pre-recorded RS, then pre-recorded Pods.
        candidates = [(obj, self.record(obj), False) for obj in created_deps]
        for kind in ('ReplicaSet', 'Pod'):
            for record in self.receipt['children']:
                if record['kind'] == kind:
                    candidates.append((object_ref(kind, record['name']), record, True))
        others = [obj for obj in self.objects if obj['kind'] != 'Deployment']
        order = {'Pod': 0, 'Service': 1, 'Secret': 2, 'ConfigMap': 3, 'NetworkPolicy': 4}
        for obj in sorted(others, key=lambda row: order[row['kind']]):
            record = next((row for row in self.receipt['objects'] if row['key'] == key(obj)), None)
            if record is not None:
                candidates.append((obj, record, False))
            if obj['kind'] == 'Service':
                size = next(size for size, expected in self.services.items() if expected == obj)
                for endpoint in self.receipt['endpoints']:
                    if endpoint['size'] == size:
                        candidates.append((object_ref('EndpointSlice', endpoint['name']), endpoint, True))
        dependencies_started = False
        for obj, record, child in candidates:
            if not child and obj['kind'] not in {'Deployment'} and not dependencies_started:
                dependencies_started = True
                gateway_record = next((row for row in self.receipt['objects'] if row['key'] == key(self.gateway)), None)
                remaining = self.kube.children(self.plan['run_token'])
                unexpected = [row for row in remaining if not (row['kind'] == 'Pod'
                    and row['metadata']['name'] == self.gateway['metadata']['name']
                    and gateway_record and row['metadata']['uid'] == gateway_record.get('uid'))]
                if unexpected:
                    state['deferred'].append('late-or-unrecorded-children')
                    self.save()
                    return  # Keep isolation policies and shared configuration around the unknown child.
            if not child and obj['kind'] in {'Secret', 'ConfigMap', 'NetworkPolicy'}:
                if any(self.kube.endpoints(service) for service in self.services.values()):
                    state['deferred'].append('late-or-unrecorded-endpoints')
                    self.save()
                    return
            if not record.get('uid'):
                state['deferred'].append(key(obj))
                self.save()
                return
            def validate(current):
                if child:
                    if obj['kind'] == 'EndpointSlice':
                        pod_uids = [row['uid'] for row in self.receipt['children'] if row['kind'] == 'Pod' and row['size'] == record['size']]
                        M.assert_endpoint(current, self.services[record['size']], record['parent_uid'], record['uid'],
                                          allow_orphan=True, pod_uids=pod_uids)
                    else:
                        M.assert_child(current, self.deployments[record['size']], record['parent_uid'], record['uid'], allow_orphan=True)
                else:
                    expected = self.expected(obj, current['spec']['replicas']) if obj['kind'] == 'Deployment' else obj
                    M.assert_owned(current, expected, record['uid'])
            try:
                current = self.kube.get(obj)
                if current is None:
                    continue
                validate(current)
                self.kube.delete(obj, current)
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    current = self.kube.get(obj)
                    if current is None:
                        break
                    validate(current)
                    time.sleep(0.25)
                else:
                    raise C.ExperimentError('DELETE_UNVERIFIED')
                state['removed'].append({'key': key(obj), 'uid': record['uid']})
            except Exception:
                state['conflicts'].append(key(obj))
                self.save()
                return  # Preserve all remaining dependencies on any uncertain deletion.
            self.save()
        # Reject late/unrecorded children rather than adopt by label after their parent is gone.
        if self.kube.children(self.plan['run_token']):
            state['deferred'].append('remaining-run-children')
        for service in self.services.values():
            if self.kube.endpoints(service):
                state['deferred'].append('remaining-service-endpoints')
        state['verified'] = not state['deferred'] and not state['conflicts']
        self.save()


def execute(plan, material, output, *, kube=None, forwarders=None, client_factory=Client,
            controller_factory=Controller, telemetry_factory=None, port_check=available_ports):
    if telemetry_factory is None:
        from tools.local_experiment.warehouse_lifecycle.telemetry import LifecycleTelemetry
        telemetry_factory = LifecycleTelemetry
    objects = M.validate_plan(plan)
    kube, forwards = kube or Kubernetes(), forwarders or Forwarders()
    receipt = {'schema_version': 1, 'run_token': plan['run_token'], 'build_token': plan['build_token'],
        'images': plan['images'], 'plan_sha256': C.digest(plan), 'result': 'running', 'observed_at': stamp(),
        'objects': [], 'children': [], 'endpoints': [], 'readiness': [], 'scales': [], 'observations': None,
        'cleanup': None, 'pending': None, 'telemetry': None,
        'unobserved': ['production-concurrency', 'multi-worker-drain', 'tenant-isolation', 'ha', 'generic-crd-controller']}
    def save():
        C.write(output / 'receipt.json', receipt)
    def observe(value):
        receipt['observations'] = copy.deepcopy(value)
        save()
    lab = Lab(plan, receipt, save, kube, forwards)
    save()
    audit = None
    try:
        with telemetry_factory(output, plan['run_token']) as audit:
            try:
                port_check()
                for obj in objects:
                    C.require(kube.get(obj) is None, 'OBJECT_NAME_COLLISION')
                    kube.dry_run(obj)
                for obj in objects:
                    record = {'key': key(obj), 'uid': None, 'content_sha256': obj['metadata']['annotations'][M.HASH]}
                    receipt['objects'].append(record)
                    receipt['pending'] = {'operation': 'create', 'key': key(obj)}
                    save()
                    current = kube.create(obj)
                    record['uid'] = created_uid(current, obj)
                    save()
                    M.assert_owned(current, obj, record['uid'])
                    receipt['pending'] = None
                    save()
                lab.wait_ready()
                service_ips = {}
                for size in ('xs', 's'):
                    service = next(obj for obj in objects if obj['kind'] == 'Service' and obj['metadata']['name'].endswith('-' + size))
                    service_ips[size] = lab.current(service)['spec']['clusterIP']
                fixtures.backend_records(plan['run_token'], service_ips)
                receipt['service_ips'] = service_ips
                save()
                forwards.attach('gateway', lab.gateway)
                for size in ('xs', 's'):
                    lab.reconnect(size)
                lab.guard()
                client = client_factory(plan['run_token'], service_ips)
                controller = controller_factory(client, guard=lab.guard, scale=lab.scale, reconnect=lab.reconnect,
                                                progress=observe, decision=audit.decision)
                receipt['pending'] = {'operation': 'fixed-lifecycle-cases'}
                save()
                controller.run()
                observe(controller.receipt())
                verify_controller(receipt['observations'], plan['run_token'])
                C.require(len(receipt['scales']) == 2 and [row['to'] for row in receipt['scales']] == [0, 1]
                          and receipt['scales'][0]['old_pod_uid'] != receipt['scales'][1]['new_pod_uid'], 'SCALE_EVIDENCE_INCOMPLETE')
                lab.wait_ready()
                receipt['pending'] = None
                receipt['result'] = 'verified'
                save()
            except BaseException as error:
                receipt['result'] = 'failed' if isinstance(error, Exception) else 'interrupted'
                receipt['failure_code'] = str(error) if isinstance(error, C.ExperimentError) else 'LIFECYCLE_EXPERIMENT_FAILED'
                save()
                lab.diagnostics(output, material)
                raise
            finally:
                forwards.close()
                lab.cleanup()
                audit.record_cleanup(receipt['cleanup'])
                save()
            C.require(receipt['cleanup']['verified'], 'CLEANUP_UNVERIFIED')
            audit.finish(receipt)
        receipt['telemetry'] = copy.deepcopy(audit.receipt())
        C.require(receipt['telemetry'].get('result') == 'passed', 'TELEMETRY_UNVERIFIED')
        receipt['result'] = 'passed'
        save()
    except BaseException as error:
        receipt['result'] = 'failed' if isinstance(error, Exception) else 'interrupted'
        receipt.setdefault('failure_code', str(error) if isinstance(error, C.ExperimentError) else 'LIFECYCLE_EXPERIMENT_FAILED')
        if audit is not None:
            receipt['telemetry'] = copy.deepcopy(audit.receipt())
        save()
        raise
    return receipt


def recover(run_token, kube=None):
    output = C.STATE / 'runs' / C.token(run_token)
    with C.run_lock(output):
        plan, original = json.loads(C.read(output / 'plan.json')), json.loads(C.read(output / 'receipt.json'))
        C.require(plan['run_token'] == original['run_token'] == run_token and plan['build_token'] == original['build_token']
                  and original['images'] == plan['images'] and original['plan_sha256'] == C.digest(plan), 'RECOVERY_IDENTITY_MISMATCH')
        objects = M.validate_plan(plan)
        allowed = {key(obj): obj for obj in objects}
        C.require(type(original['objects']) is list and len(original['objects']) <= len(objects)
                  and len({row['key'] for row in original['objects']}) == len(original['objects'])
                  and all(row['key'] in allowed and row['content_sha256'] == allowed[row['key']]['metadata']['annotations'][M.HASH]
                          for row in original['objects']), 'RECOVERY_OBJECTS_INVALID')
        receipt = {'schema_version': 1, 'run_token': run_token, 'original_result': original['result'],
                   'objects': copy.deepcopy(original['objects']), 'children': copy.deepcopy(original['children']),
                   'endpoints': copy.deepcopy(original['endpoints']), 'cleanup': None,
                   'plan_sha256': C.digest(plan), 'attempt': 1, 'previous_recovery': None}
        previous_path = output / 'recovery.json'
        if previous_path.exists():
            previous_bytes = C.read(previous_path)
            previous = json.loads(previous_bytes)
            C.require(previous.get('schema_version') == 1 and previous.get('run_token') == run_token
                      and previous.get('original_result') == original['result']
                      and previous.get('objects') == original['objects']
                      and previous.get('plan_sha256', C.digest(plan)) == C.digest(plan), 'RECOVERY_CHAIN_MISMATCH')
            # Original versions lacked plan_sha256; their complete primary object
            # list (UIDs + plan content hashes) still binds them to the original plan.
            for field in ('children', 'endpoints'):
                C.require(type(previous.get(field)) is list, 'RECOVERY_CHAIN_MISMATCH')
                for row in previous[field]:
                    match = next((value for value in receipt[field] if value['uid'] == row['uid']), None)
                    if match is None:
                        receipt[field].append(copy.deepcopy(row))
                    else:
                        C.require(all(match[name] == row[name] for name in ('kind', 'name', 'uid', 'parent_uid', 'size')),
                                  'RECOVERY_CHAIN_MISMATCH')
            attempt = previous.get('attempt', 1)
            C.require(type(attempt) is int and 1 <= attempt < 64, 'RECOVERY_ATTEMPT_LIMIT')
            receipt['attempt'] = attempt + 1
        C.require(type(receipt['children']) is list and len(receipt['children']) <= 32
                  and len({row['uid'] for row in receipt['children']}) == len(receipt['children']), 'RECOVERY_CHILDREN_INVALID')
        C.require(type(receipt['endpoints']) is list and len(receipt['endpoints']) <= 8
                  and len({row['uid'] for row in receipt['endpoints']}) == len(receipt['endpoints']), 'RECOVERY_ENDPOINTS_INVALID')
        primary = {row['key']: row for row in original['objects']}
        deployments = {size: next(obj for obj in objects if obj['kind'] == 'Deployment' and obj['metadata']['name'].endswith('-' + size))
                       for size in ('xs', 's')}
        services = {size: next(obj for obj in objects if obj['kind'] == 'Service' and obj['metadata']['name'].endswith('-' + size))
                    for size in ('xs', 's')}
        for row in receipt['children'] + receipt['endpoints']:
            C.require(row.get('size') in {'xs', 's'} and type(row.get('uid')) is str
                      and str(uuid.UUID(row['uid'])) == row['uid'], 'RECOVERY_CHILD_IDENTITY_INVALID')
        sets = {row['uid']: row for row in receipt['children'] if row['kind'] == 'ReplicaSet'}
        for row in receipt['children']:
            expected = deployments[row['size']]
            C.require(row['template_hash'] == expected['spec']['template']['metadata']['annotations'][M.TEMPLATE_HASH],
                      'RECOVERY_TEMPLATE_MISMATCH')
            if row['kind'] == 'ReplicaSet':
                C.require(row['parent_uid'] == primary.get(key(expected), {}).get('uid')
                          and re.fullmatch(re.escape(expected['metadata']['name']) + r'-[a-z0-9]{1,16}', row['name']),
                          'RECOVERY_CHILD_CHAIN_INVALID')
            else:
                parent = sets.get(row['parent_uid'])
                C.require(row['kind'] == 'Pod' and parent is not None and parent['size'] == row['size']
                          and re.fullmatch(re.escape((parent['name'] + '-')[:58]) + r'[a-z0-9]{5}', row['name']),
                          'RECOVERY_CHILD_CHAIN_INVALID')
        for row in receipt['endpoints']:
            expected = services[row['size']]
            C.require(row['kind'] == 'EndpointSlice' and row['parent_uid'] == primary.get(key(expected), {}).get('uid')
                      and re.fullmatch(re.escape(expected['metadata']['name']) + r'-[a-z0-9]{5}', row['name']),
                      'RECOVERY_ENDPOINT_CHAIN_INVALID')
        if previous_path.exists():
            archive_dir = output / 'recovery-attempts'
            C.directory(archive_dir, create=True)
            archive = archive_dir / (uuid.uuid4().hex + '.json')
            with os.fdopen(os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb') as stream:
                stream.write(previous_bytes)
                stream.flush()
                os.fsync(stream.fileno())
            receipt['previous_recovery'] = {'file': str(archive.relative_to(output)), 'sha256': C.digest(previous_bytes)}
        def save():
            C.write(output / 'recovery.json', receipt)
        Lab(plan, receipt, save, kube or Kubernetes(), Forwarders()).cleanup()
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
                           'evidence': 'tmp/local-experiment/warehouse-lifecycle/runs/' + run_token}))
        return 0
    except BaseException as error:
        print(C.canonical({'result': 'failed', 'run_token': run_token,
                           'failure_code': str(error) if isinstance(error, C.ExperimentError) else 'LIFECYCLE_EXPERIMENT_FAILED'}))
        return 128 + error.signum if hasattr(error, 'signum') else 1


if __name__ == '__main__':
    raise SystemExit(main())
