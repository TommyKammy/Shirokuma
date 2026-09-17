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
from tools.local_experiment.warehouse_lifecycle.controller import Controller

UNKNOWN = {'replicas': None, 'backend_active': None}


def request_uuid(value):
    try:
        C.require(type(value) is str and str(uuid.UUID(value)) == value, 'SESSION_REQUEST_INVALID')
    except (ValueError, AttributeError):
        raise C.ExperimentError('SESSION_REQUEST_INVALID') from None
    return value


class SessionOwner:
    """One owner holds the run lock, tunnels, known UIDs and actual controller."""
    def __init__(self, plan, material, output, *, kube=None, forwarders=None,
                 client_factory=R.Client, controller_factory=Controller,
                 telemetry_factory=None, rpc_factory=None, port_check=R.available_ports,
                 announce=None):
        self.plan, self.material, self.output = plan, material, Path(output)
        self.objects = M.validate_plan(plan)
        C.directory(self.output)
        C.require(self.output.name == C.token(plan['run_token']), 'SESSION_OUTPUT_INVALID')
        self.token = plan['run_token']
        self.kube, self.forwards = kube or R.Kubernetes(), forwarders or R.Forwarders()
        self.client_factory, self.controller_factory = client_factory, controller_factory
        self.telemetry_factory, self.rpc_factory = telemetry_factory, rpc_factory
        self.port_check, self.announce = port_check, announce or (lambda value: None)
        self.done, self.controller, self.audit = False, None, None
        self.operation_error = False
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
        C.require(action in {'chill', 'wake'}, 'SESSION_ACTION_INVALID')
        request_uuid(request_id)
        # SessionRPC serializes handlers; this lock also protects direct callers.
        with self.handler_lock:
            C.require(self.context is not None, 'SESSION_NOT_PREPARED')
            # HTTP serving can use a worker thread. Carry the real open OTel root
            # context there instead of manufacturing a parent after the operation.
            try:
                return self.context.copy().run(self._handle, action, request_id)
            except BaseException:
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
        C.write(path, operation)  # Durable intent before state reads or mutation.
        self.receipt['session']['requests'].append({'request_id': request_id, 'action': action,
            'operation_id': operation_id, 'receipt': str(path.relative_to(self.output)), 'result': 'pending'})
        self.save()
        started, phase_started = time.monotonic(), False
        try:
            operation['before'] = self.state()
            C.write(path, operation)
            goal = {'replicas': 0 if action == 'chill' else 1, 'backend_active': action == 'wake'}
            if operation['before'] == goal:
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
            operation['result'] = 'failed' if phase_started else 'unknown'
            operation['after'] = copy.deepcopy(UNKNOWN)
            operation['failure_code'] = str(error) if isinstance(error, C.ExperimentError) else 'SESSION_OPERATION_FAILED'
            self.done = True
            self.receipt['session']['phase'] = 'failed'
            self.receipt['failure_code'] = operation['failure_code']
            if not isinstance(error, Exception):
                operation['duration_ms'] = max(0, int((time.monotonic() - started) * 1000))
                C.write(path, operation)
                self.save()
                raise
        operation['duration_ms'] = max(0, int((time.monotonic() - started) * 1000))
        C.write(path, operation)
        receipt_hash = C.digest(C.read(path))
        self.receipt['session']['requests'][-1].update(result=operation['result'], receipt_sha256=receipt_hash)
        self.save()
        return {key: copy.deepcopy(operation[key]) for key in
                ('schema_version', 'session_id', 'request_id', 'warehouse', 'action', 'result', 'before', 'after', 'operation_id')} | {
                    'receipt_sha256': receipt_hash}

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
            from tools.local_experiment.warehouse_lifecycle.telemetry import LifecycleTelemetry
            self.telemetry_factory = LifecycleTelemetry
        if self.rpc_factory is None:
            from tools.local_experiment.warehouse_lifecycle.session_rpc import SessionRPC
            self.rpc_factory = SessionRPC
        self.save()
        try:
            with self.telemetry_factory(self.output, self.token) as audit:
                self.audit = audit
                try:
                    self.provision()
                    with self.rpc_factory(self.token, self.output, self.handler,
                                          ttl_seconds=600, max_requests=16) as rpc:
                        self.announce({'result': 'ready', 'session_id': self.token, 'warehouse': 'XS'})
                        self.receipt['session']['server_result'] = rpc.serve(stop=lambda: self.done)
                        self.save()
                    C.require(self.receipt['session']['server_result'].get('result') == 'verified'
                              and self.receipt['session']['server_result'].get('error_codes') == [],
                              'SESSION_RPC_UNVERIFIED')
                    R.verify_controller(self.controller.receipt(), self.token)
                    C.require(self.done and not self.operation_error and self.receipt['session']['phase'] == 'woken'
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
            self.receipt['telemetry'] = audit.receipt()
            C.require(self.receipt['telemetry']['result'] == 'passed', 'TELEMETRY_UNVERIFIED')
            self.receipt.update(result='passed', pending=None)
            self.receipt['session']['phase'] = 'closed'
            self.save()
        except BaseException as error:
            self.receipt['result'] = 'failed' if isinstance(error, Exception) else 'interrupted'
            self.receipt.setdefault('failure_code', str(error) if isinstance(error, C.ExperimentError) else 'SESSION_FAILED')
            if self.audit is not None:
                self.receipt['telemetry'] = self.audit.receipt()
            self.save()
            raise
        return copy.deepcopy(self.receipt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('run',))
    parser.add_argument('--build-token', required=True)
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
            SessionOwner(plan, material, output, announce=announce).run()
        print(C.canonical({'result': 'passed', 'session_id': session_id}), flush=True)
        return 0
    except BaseException as error:
        print(C.canonical({'result': 'failed', 'session_id': session_id,
            'failure_code': str(error) if isinstance(error, C.ExperimentError) else 'SESSION_FAILED'}), flush=True)
        return 128 + error.signum if hasattr(error, 'signum') else 1


if __name__ == '__main__':
    raise SystemExit(main())
