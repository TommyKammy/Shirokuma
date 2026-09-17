#!/usr/bin/env python3
"""Short-lived owner for the fixed local XS CLI chill/wake experiment."""
import argparse
import copy
import contextvars
import json
from pathlib import Path
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.warehouse_lifecycle import run as R
from tools.local_experiment.warehouse_lifecycle import support as C, fixtures, manifest as M
from tools.local_experiment.warehouse_lifecycle.controller import Controller, identity
from tools.local_experiment.warehouse_lifecycle.client import QueryClient, CASES, expected_rows, SELECT

UNKNOWN = {'replicas': None, 'backend_active': None}


def request_uuid(value):
    try:
        C.require(type(value) is str and str(uuid.UUID(value)) == value, 'SESSION_REQUEST_INVALID')
    except (ValueError, AttributeError):
        raise C.ExperimentError('SESSION_REQUEST_INVALID') from None
    return value


def verify_query_controller(value, run_token):
    """Keep the seven-case verifier intact; add one separate caller proof."""
    C.require(type(value) is dict and type(value.get('client')) is dict, 'CALLER_UNVERIFIED')
    requests = value['client'].get('requests', [])
    C.require(type(requests) is list and len(requests) == 8
              and {r.get('case') for r in requests} == set(CASES) | {'xs_caller'}, 'CALLER_UNVERIFIED')
    original = copy.deepcopy(value)
    original['client']['requests'] = [r for r in requests if r['case'] != 'xs_caller']
    R.verify_controller(original, run_token)
    identities = value.get('identity')
    C.require(type(identities) is dict and set(identities) == {'initial', 'final'}
              and all(type(identities[k]) is dict and set(identities[k]) == {'xs', 's'}
                      for k in ('initial', 'final')), 'CALLER_IDENTITY_UNVERIFIED')
    initial = {size: identity(identities['initial'][size], replicas=1) for size in ('xs', 's')}
    final = {size: identity(identities['final'][size], replicas=1) for size in ('xs', 's')}
    C.require(initial['s'] == final['s']
              and initial['xs']['deployment_uid'] == final['xs']['deployment_uid']
              and initial['xs']['pod_uid'] != final['xs']['pod_uid'], 'CALLER_IDENTITY_UNVERIFIED')
    down = {'deployment_uid': initial['xs']['deployment_uid'], 'pod_uid': None, 'replicas': 0}
    scales = value['scale_operations']
    for record, before, after in zip(scales, (initial['xs'], down), (down, final['xs'])):
        C.require(record.get('size') == 'xs' and record.get('result') == 'verified'
                  and type(record.get('from_replicas')) is int and type(record.get('to_replicas')) is int
                  and record['from_replicas'] == before['replicas']
                  and record['to_replicas'] == after['replicas']
                  and record.get('before') == before and record.get('after') == after,
                  'CALLER_SCALE_UNVERIFIED')
    caller = value.get('caller') or {}
    row = next(r for r in requests if r['case'] == 'xs_caller')
    request_id = request_uuid(caller.get('request_id'))
    C.require(caller.get('result') == 'verified' and caller.get('stage') == 'complete'
              and caller.get('post_intents') == 1 and caller.get('audit_attempted') is True
              and caller.get('audit_verified') is True and caller.get('failure_code') is None
              and row.get('result') == 'success' and row.get('response_complete') is True
              and row.get('matched') is True and row.get('rows_observed') == 25
              and row.get('rows_sha256') == C.digest(expected_rows(run_token, 'xs'))
              and row.get('sql_sha256') == C.digest(SELECT.encode())
              and row.get('source') == 'shirokuma-wl-' + run_token + '-xs_caller-' + uuid.UUID(request_id).hex,
              'CALLER_UNVERIFIED')
    expected = {key: row[key] for key in
                ('case', 'query_id', 'rows_observed', 'rows_sha256', 'response_complete', 'matched')}
    C.require(caller.get('query') == expected | {'post_count': 1}, 'CALLER_UNVERIFIED')
    histories = value['client'].get('query_snapshots', [])
    xs = next((r for r in reversed(histories) if r['size'] == 'xs'), {})
    s = next((r for r in reversed(histories) if r['size'] == 's'), {})
    C.require(xs.get('complete') is True and s.get('complete') is True
              and xs.get('nonterminal_count') == s.get('nonterminal_count') == 0
              and {r['case'] for r in xs.get('rows', [])} == {'xs_after', 'xs_caller'}
              and {r['case'] for r in s.get('rows', [])} == {'s_before', 's_chilled', 's_after'}
              and all(r['state'] == 'FINISHED' for r in xs['rows'] + s['rows'])
              and any(r['query_id'] == row['query_id'] and r['case'] == 'xs_caller' for r in xs['rows'])
              and all(r['query_id'] != row['query_id'] for r in s['rows']), 'CALLER_UNVERIFIED')


class SessionOwner:
    """One owner holds the run lock, tunnels, known UIDs and actual controller."""
    def __init__(self, plan, material, output, *, kube=None, forwarders=None,
                 client_factory=R.Client, controller_factory=Controller,
                 telemetry_factory=None, rpc_factory=None, port_check=R.available_ports,
                 announce=None, query_mode=False):
        C.require(type(query_mode) is bool, 'SESSION_MODE_INVALID')
        self.query_mode = query_mode
        self.plan, self.material, self.output = plan, material, Path(output)
        self.objects = M.validate_plan(plan)
        C.directory(self.output)
        C.require(self.output.name == C.token(plan['run_token']), 'SESSION_OUTPUT_INVALID')
        self.token = plan['run_token']
        self.kube, self.forwards = kube or R.Kubernetes(), forwarders or R.Forwarders()
        self.client_factory, self.controller_factory = client_factory, controller_factory
        if query_mode and client_factory is R.Client:
            self.client_factory = QueryClient
        self.telemetry_factory, self.rpc_factory = telemetry_factory, rpc_factory
        self.port_check, self.announce = port_check, announce or (lambda value: None)
        self.done, self.controller, self.audit = False, None, None
        self.operation_error = False
        self.interruption = None
        self.handler_lock = threading.Lock()
        self.context = None
        self.operations = self.output / 'operations'
        C.directory(self.operations, create=True)
        self.receipt = {'schema_version': 1, 'run_token': self.token, 'build_token': plan['build_token'],
            'images': plan['images'], 'plan_sha256': C.digest(plan), 'result': 'running',
            'observed_at': R.stamp(), 'objects': [], 'children': [], 'endpoints': [],
            'readiness': [], 'scales': [], 'observations': None, 'cleanup': None,
            'pending': None, 'telemetry': None, 'session': {'phase': 'starting', 'requests': [],
                'ttl_seconds': 600, 'max_requests': 16, 'server_result': None},
            'unobserved': ['production-concurrency', 'multi-worker-drain', 'tenant-isolation',
                           'ha', 'generic-crd-controller', 'persistent-session-daemon']}
        if query_mode:
            self.receipt['session']['query_mode'] = True
            self.receipt['unobserved'].extend(['cold-query-performance', 'authenticated-caller-identity',
                                              'unknown-query-execution-exactly-once'])
        self.lab = R.Lab(plan, self.receipt, self.save, self.kube, self.forwards)

    def save(self):
        C.write(self.output / 'receipt.json', self.receipt)

    def observe(self, value):
        self.receipt['observations'] = copy.deepcopy(value)
        self.save()

    def state(self):
        current = self.controller.checked_guard()
        self.controller.client.registry()  # Exact live registry, not cached active state alone.
        return {'replicas': current['xs']['replicas'],
                'backend_active': self.controller.client.active['xs']}

    def handler(self, action, request_id):
        C.require(action in ({'chill', 'query'} if self.query_mode else {'chill', 'wake'}), 'SESSION_ACTION_INVALID')
        request_uuid(request_id)
        # SessionRPC serializes handlers; this lock also protects direct callers.
        with self.handler_lock:
            C.require(self.context is not None, 'SESSION_NOT_PREPARED')
            # HTTP serving can use a worker thread. Carry the real open OTel root
            # context there instead of manufacturing a parent after the operation.
            try:
                return self.context.copy().run(self._handle, action, request_id)
            except BaseException as error:
                if not isinstance(error, Exception):
                    self.interruption = error
                self.operation_error, self.done = True, True
                raise

    def _handle(self, action, request_id):
        C.require(not self.done and self.controller is not None, 'SESSION_CLOSED')
        C.require(not any(row['request_id'] == request_id for row in self.receipt['session']['requests']),
                  'SESSION_REQUEST_REPLAY_WITHOUT_RPC')
        operation_id = str(uuid.uuid4())
        path = self.operations / (operation_id + '.json')
        operation = {'schema_version': 1, 'session_id': self.token, 'request_id': request_id,
            'warehouse': 'XS', 'action': action, 'operation_id': operation_id,
            'result': 'pending', 'before': copy.deepcopy(UNKNOWN), 'after': copy.deepcopy(UNKNOWN),
            'observed_at': R.stamp(), 'duration_ms': None, 'failure_code': None}
        if action == 'query':
            operation['query'] = None
        C.write(path, operation)  # Durable intent before state reads or mutation.
        self.receipt['session']['requests'].append({'request_id': request_id, 'action': action,
            'operation_id': operation_id, 'receipt': str(path.relative_to(self.output)), 'result': 'pending'})
        self.save()
        started, phase_started = time.monotonic(), False
        try:
            operation['before'] = self.state()
            C.write(path, operation)
            if action == 'query':
                C.require(operation['before'] == {'replicas': 0, 'backend_active': False},
                          'SESSION_QUERY_REQUIRES_CHILLED')
                phase_started = True
                operation['query'] = self.controller.query(request_id, audit=self.audit.query)
                self.observe(self.controller.receipt())
                operation['after'] = self.state()
                C.require(operation['after'] == {'replicas': 1, 'backend_active': True},
                          'SESSION_TARGET_UNVERIFIED')
                verify_query_controller(self.controller.receipt(), self.token)
                operation['result'] = 'verified'
                self.receipt['session']['phase'] = 'queried'
                self.done = True
            goal = {'replicas': 0 if action == 'chill' else 1, 'backend_active': action == 'wake'}
            if action == 'query':
                pass  # Caller success includes terminal history and its audit above.
            elif operation['before'] == goal:
                # A repeated chill (or wake while already prepared) has no writes,
                # but still rechecks UID/spec/registry before claiming no-op.
                operation.update(result='noop', after=self.state())
                C.require(operation['after'] == goal, 'SESSION_STATE_CHANGED')
            else:
                phase_started = True
                getattr(self.controller, action)()
                self.observe(self.controller.receipt())
                operation['after'] = self.state()
                C.require(operation['after'] == goal, 'SESSION_TARGET_UNVERIFIED')
                operation['result'] = 'verified'
                self.receipt['session']['phase'] = 'chilled' if action == 'chill' else 'woken'
                if action == 'wake':
                    self.done = True  # RPC drains this response before returning.
        except BaseException as error:
            operation['result'] = 'unknown' if action == 'query' else ('failed' if phase_started else 'unknown')
            if action == 'query':
                operation['query'] = None
            operation['after'] = copy.deepcopy(UNKNOWN)
            operation['failure_code'] = str(error) if isinstance(error, C.ExperimentError) else 'SESSION_OPERATION_FAILED'
            self.done = True
            self.receipt['session']['phase'] = 'failed'
            self.receipt['failure_code'] = operation['failure_code']
            if not isinstance(error, Exception):
                operation['duration_ms'] = max(0, int((time.monotonic() - started) * 1000))
                try:
                    C.write(path, operation)
                    self.save()
                finally:
                    raise error
        operation['duration_ms'] = max(0, int((time.monotonic() - started) * 1000))
        C.write(path, operation)
        receipt_hash = C.digest(C.read(path))
        self.receipt['session']['requests'][-1].update(result=operation['result'], receipt_sha256=receipt_hash)
        self.save()
        response = {key: copy.deepcopy(operation[key]) for key in
                ('schema_version', 'session_id', 'request_id', 'warehouse', 'action', 'result', 'before', 'after', 'operation_id')} | {
                    'receipt_sha256': receipt_hash}
        if action == 'query':
            response['query'] = copy.deepcopy(operation['query'])
        return response

    def provision(self):
        self.port_check()
        for obj in self.objects:
            C.require(self.kube.get(obj) is None, 'OBJECT_NAME_COLLISION')
            self.kube.dry_run(obj)
        for obj in self.objects:
            record = {'key': R.key(obj), 'uid': None, 'content_sha256': obj['metadata']['annotations'][M.HASH]}
            self.receipt['objects'].append(record)
            self.receipt['pending'] = {'operation': 'create', 'key': R.key(obj)}
            self.save()
            current = self.kube.create(obj)
            record['uid'] = R.created_uid(current, obj)
            self.save()
            M.assert_owned(current, obj, record['uid'])
            self.receipt['pending'] = None
            self.save()
        self.lab.wait_ready()
        addresses = {size: self.lab.current(service)['spec']['clusterIP']
                     for size, service in self.lab.services.items()}
        fixtures.backend_records(self.token, addresses)
        self.receipt['service_ips'] = addresses
        self.save()
        self.forwards.attach('gateway', self.lab.gateway)
        for size in ('xs', 's'):
            self.lab.reconnect(size)
        self.lab.guard()
        client = self.client_factory(self.token, addresses)
        self.controller = self.controller_factory(client, guard=self.lab.guard, scale=self.lab.scale,
            reconnect=self.lab.reconnect, progress=self.observe, decision=self.audit.decision)
        self.controller.prepare()
        self.observe(self.controller.receipt())
        C.require(self.receipt['observations']['result'] == 'prepared', 'SESSION_PREPARE_UNVERIFIED')
        self.receipt['session']['phase'] = 'prepared'
        self.save()
        self.context = contextvars.copy_context()

    def run(self):
        if self.telemetry_factory is None:
            if self.query_mode:
                from tools.local_experiment.warehouse_lifecycle.query_telemetry import LifecycleQueryTelemetry
                self.telemetry_factory = LifecycleQueryTelemetry
            else:
                from tools.local_experiment.warehouse_lifecycle.telemetry import LifecycleTelemetry
                self.telemetry_factory = LifecycleTelemetry
        if self.rpc_factory is None:
            from tools.local_experiment.warehouse_lifecycle.session_rpc import SessionRPC
            self.rpc_factory = SessionRPC
        self.save()
        interruption = None
        try:
            with self.telemetry_factory(self.output, self.token) as audit:
                self.audit = audit
                try:
                    self.provision()
                    options = {'ttl_seconds': 600, 'max_requests': 16}
                    if self.query_mode:
                        options['allow_query'] = True
                    with self.rpc_factory(self.token, self.output, self.handler, **options) as rpc:
                        self.announce({'result': 'ready', 'session_id': self.token, 'warehouse': 'XS'})
                        self.receipt['session']['server_result'] = rpc.serve(stop=lambda: self.done)
                        if self.interruption is not None:
                            raise self.interruption
                        self.save()
                    C.require(self.receipt['session']['server_result'].get('result') == 'verified'
                              and self.receipt['session']['server_result'].get('error_codes') == [],
                              'SESSION_RPC_UNVERIFIED')
                    (verify_query_controller if self.query_mode else R.verify_controller)(self.controller.receipt(), self.token)
                    C.require(self.done and not self.operation_error
                              and self.receipt['session']['phase'] == ('queried' if self.query_mode else 'woken')
                              and all(row['result'] in {'verified', 'noop'} and row.get('receipt_sha256')
                                      for row in self.receipt['session']['requests'])
                              and len(self.receipt['scales']) == 2
                              and [row['to'] for row in self.receipt['scales']] == [0, 1]
                              and self.receipt['scales'][0]['old_pod_uid'] != self.receipt['scales'][1]['new_pod_uid'],
                              'SESSION_WAKE_UNVERIFIED')
                    self.lab.wait_ready()
                    self.receipt.update(result='verified', pending=None)
                    self.save()
                except BaseException as error:
                    if not isinstance(error, Exception):
                        interruption = error
                    self.receipt['result'] = 'failed' if isinstance(error, Exception) else 'interrupted'
                    self.receipt.setdefault('failure_code', str(error) if isinstance(error, C.ExperimentError) else 'SESSION_FAILED')
                    self.save()
                    self.lab.diagnostics(self.output, self.material)
                    raise
                finally:
                    try:
                        self.forwards.close()
                    finally:
                        self.lab.cleanup()
                        audit.record_cleanup(self.receipt['cleanup'])
                        self.save()
                C.require(self.receipt['cleanup']['verified'], 'CLEANUP_UNVERIFIED')
                audit.finish(self.receipt)
            if interruption is not None:
                raise interruption
            self.receipt['telemetry'] = audit.receipt()
            C.require(self.receipt['telemetry']['result'] == 'passed', 'TELEMETRY_UNVERIFIED')
            self.receipt.update(result='passed', pending=None)
            self.receipt['session']['phase'] = 'closed'
            self.save()
        except BaseException as error:
            if interruption is None and not isinstance(error, Exception):
                interruption = error
            error = interruption or error
            self.receipt['result'] = 'failed' if isinstance(error, Exception) else 'interrupted'
            self.receipt.setdefault('failure_code', str(error) if isinstance(error, C.ExperimentError) else 'SESSION_FAILED')
            if self.audit is not None:
                self.receipt['telemetry'] = self.audit.receipt()
            if interruption is not None:
                try:
                    self.save()
                finally:
                    raise interruption
            self.save()
            raise
        return copy.deepcopy(self.receipt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('run',))
    parser.add_argument('--build-token', required=True)
    parser.add_argument('--query-mode', action='store_true', help='Chill, then wake for one fixed caller SELECT')
    args = parser.parse_args()
    session_id = None
    try:
        images = R.build.load_build(args.build_token)['images']
        session_id, output = C.new_output('runs')
        with C.run_lock(output), C.interruption_handlers():
            material = fixtures.create_fixtures(session_id, output)
            plan = {'schema_version': 1, 'run_token': session_id, 'build_token': args.build_token,
                    'images': images, 'objects': M.render(images, session_id, material)}
            M.validate_plan(plan)
            C.write(output / 'plan.json', plan)
            announce = lambda value: print(C.canonical(value), flush=True)
            announce({'result': 'starting', 'session_id': session_id,
                      'evidence': 'tmp/local-experiment/warehouse-lifecycle/runs/' + session_id})
            R.check_context()
            SessionOwner(plan, material, output, announce=announce, query_mode=args.query_mode).run()
        print(C.canonical({'result': 'passed', 'session_id': session_id}), flush=True)
        return 0
    except BaseException as error:
        print(C.canonical({'result': 'failed', 'session_id': session_id,
            'failure_code': str(error) if isinstance(error, C.ExperimentError) else 'SESSION_FAILED'}), flush=True)
        return 128 + error.signum if hasattr(error, 'signum') else 1


if __name__ == '__main__':
    raise SystemExit(main())
