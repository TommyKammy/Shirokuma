import copy
import json
import socket
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from tools.local_experiment.warehouse_lifecycle import run as R


class ForwardingPortTests(unittest.TestCase):
    def test_closed_connection_allows_reattach_but_live_listener_does_not(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            port = listener.getsockname()[1]
            with self.assertRaisesRegex(R.C.ExperimentError, 'TUNNEL_PORT_COLLISION'):
                R.available_port(port)
            with socket.create_connection(('127.0.0.1', port)) as client:
                connection, _ = listener.accept()
                with connection:
                    connection.shutdown(socket.SHUT_RDWR)
                self.assertEqual(client.recv(1), b'')
        R.available_port(port)


class FakeForwards:
    def __init__(self):
        self.active = {}
    def attach(self, size, pod):
        self.active[size] = pod['metadata']['name']
    def close_one(self, size):
        self.active.pop(size, None)
    def close(self):
        self.active.clear()


class FakeAudit:
    failure = False
    def __init__(self, output, token):
        self.value = {'result': 'running', 'records': []}
    def __enter__(self):
        return self
    def decision(self, *args):
        self.value['records'].append(args)
        if self.failure:
            raise RuntimeError('delivery failed')
    def record_cleanup(self, cleanup):
        self.value['cleanup'] = copy.deepcopy(cleanup)
    def finish(self, receipt):
        assert receipt['result'] == 'verified' and receipt['cleanup']['verified']
        self.value['result'] = 'verified'
    def __exit__(self, exc_type, exc, tb):
        self.value['result'] = 'passed' if exc_type is None else 'failed'
    def receipt(self):
        return copy.deepcopy(self.value)


class FakeController:
    failure = False
    interrupt = False
    mutate = None
    def __init__(self, client, guard, scale, reconnect, progress, decision):
        self.token = client
        self.guard, self.scale, self.reconnect = guard, scale, reconnect
        self.progress, self.decision = progress, decision
        self.value = {'result': 'running', 'pending': 'initial'}
    def run(self):
        self.progress(self.value)
        self.guard()
        if self.mutate:
            self.mutate()
        if self.failure:
            raise KeyboardInterrupt() if self.interrupt else RuntimeError('client failed')
        self.decision('busy-defer', 'blocked', {}, 1)
        self.scale('xs', 0)
        self.decision('chill', 'success', {}, 1)
        self.scale('xs', 1)
        self.reconnect('xs')
        self.decision('wake', 'success', {}, 1)
        self.value = {'run_token': self.token, 'result': 'verified', 'pending': None,
            'busy_defer_verified': True, 'chill_verified': True, 'wake_verified': True,
            'scale_operations': [{'result': 'verified'} for _ in range(2)],
            'client': {'result': 'verified', 'pending': None, 'ledger': {'all_results_consumed': True,
                'pending_handles': 0, 'uncaptured_requests': 0},
                'requests': [{'response_complete': True} for _ in range(7)],
                'controls': [{'operation': name, 'result': 'verified'} for name in ('deactivate', 'activate')]}}
        self.progress(self.value)
    def receipt(self):
        return copy.deepcopy(self.value)


class FakeKube:
    def __init__(self):
        self.objects, self.deleted, self.created, self.patches = {}, [], [], []
        self.generation = 0
        self.unknown_kind = None
        self.partial_kind = None
    def get(self, obj):
        return copy.deepcopy(self.objects.get(R.key(obj)))
    def children(self, token):
        return [copy.deepcopy(row) for row in self.objects.values()
                if row['kind'] in ('Pod', 'ReplicaSet') and row['metadata']['labels'].get(R.M.OWNER) == token]
    def endpoints(self, service):
        return [copy.deepcopy(row) for row in self.objects.values() if row['kind'] == 'EndpointSlice'
                and row['metadata']['labels'].get('kubernetes.io/service-name') == service['metadata']['name']]
    def refresh_endpoints(self):
        for service in list(self.objects.values()):
            if service['kind'] != 'Service':
                continue
            size = service['metadata']['labels'][R.M.SIZE]
            name = service['metadata']['name'] + '-abcde'
            slice_key = 'EndpointSlice/' + name
            if slice_key not in self.objects:
                self.objects[slice_key] = {'apiVersion': 'discovery.k8s.io/v1', 'kind': 'EndpointSlice',
                    'metadata': {'name': name, 'namespace': R.C.NAMESPACE, 'uid': str(uuid.uuid4()), 'resourceVersion': '1',
                        'labels': {'kubernetes.io/service-name': service['metadata']['name'],
                                   'endpointslice.kubernetes.io/managed-by': 'endpointslice-controller.k8s.io'},
                        'ownerReferences': [{'apiVersion': 'v1', 'kind': 'Service', 'name': service['metadata']['name'],
                                             'uid': service['metadata']['uid'], 'controller': True}]},
                    'addressType': 'IPv4', 'ports': [{'name': size, 'protocol': 'TCP', 'port': R.PORTS[size]}], 'endpoints': []}
            pods = [p for p in self.objects.values() if p['kind'] == 'Pod' and 'generateName' in p['metadata']
                    and p['metadata']['labels'][R.M.SIZE] == size]
            self.objects[slice_key]['endpoints'] = [{'addresses': [pod['status']['podIP']], 'conditions': {'ready': True},
                'targetRef': {'kind': 'Pod', 'namespace': R.C.NAMESPACE, 'name': pod['metadata']['name'], 'uid': pod['metadata']['uid']}}
                for pod in pods]
    def dry_run(self, obj):
        pass
    def make_child(self, dep):
        size = dep['metadata']['labels'][R.M.SIZE]
        rs_name = dep['metadata']['name'] + '-123abc'
        rs_key = 'ReplicaSet/' + rs_name
        template = copy.deepcopy(dep['spec']['template'])
        template['metadata']['labels']['pod-template-hash'] = '123abc'
        if rs_key not in self.objects:
            selector = copy.deepcopy(dep['spec']['selector'])
            selector['matchLabels']['pod-template-hash'] = '123abc'
            rs = {'apiVersion': 'apps/v1', 'kind': 'ReplicaSet', 'metadata': {
                'name': rs_name, 'namespace': R.C.NAMESPACE, 'uid': str(uuid.uuid4()), 'resourceVersion': '1',
                'labels': copy.deepcopy(template['metadata']['labels']),
                'ownerReferences': [{'apiVersion': 'apps/v1', 'kind': 'Deployment', 'name': dep['metadata']['name'],
                                     'uid': dep['metadata']['uid'], 'controller': True}]},
                'spec': {'replicas': 1, 'selector': selector, 'template': copy.deepcopy(template)}}
            self.objects[rs_key] = rs
        rs = self.objects[rs_key]
        rs['spec']['replicas'] = dep['spec']['replicas']
        if not dep['spec']['replicas']:
            for name, row in list(self.objects.items()):
                if row['kind'] == 'Pod' and any(v['uid'] == rs['metadata']['uid'] for v in row['metadata'].get('ownerReferences', [])):
                    del self.objects[name]
            return
        self.generation += 1
        pod = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': dict(copy.deepcopy(template['metadata']),
            name=(rs_name + '-')[:58] + str(self.generation).zfill(5), generateName=rs_name + '-', namespace=R.C.NAMESPACE,
            uid=str(uuid.uuid4()), resourceVersion='1', ownerReferences=[{'apiVersion': 'apps/v1', 'kind': 'ReplicaSet',
                'name': rs_name, 'uid': rs['metadata']['uid'], 'controller': True}]), 'spec': template['spec'],
            'status': {'phase': 'Running', 'podIP': '10.42.0.' + str(100 + self.generation),
                       'containerStatuses': [{'name': size, 'ready': True, 'restartCount': 0}],
                       'conditions': [{'type': 'Ready', 'status': 'True'}]}}
        self.objects[R.key(pod)] = pod
    def create(self, obj):
        current = copy.deepcopy(obj)
        current['metadata'].update(uid=str(uuid.uuid4()), resourceVersion='1')
        if obj['kind'] == 'Service':
            current['spec']['clusterIP'] = '10.43.1.' + str(10 + len(self.objects))
        if obj['kind'] == 'Pod':
            current['status'] = {'phase': 'Running', 'containerStatuses': [
                {'name': row['name'], 'ready': True, 'restartCount': 0} for row in obj['spec']['containers']]}
        self.objects[R.key(obj)] = current
        self.created.append(R.key(obj))
        if obj['kind'] == 'Deployment':
            self.make_child(current)
        self.refresh_endpoints()
        if obj['kind'] == self.unknown_kind:
            raise TimeoutError('create response lost')
        return copy.deepcopy(current)
    def scale(self, current, expected, uid, replicas):
        patch = R.M.scale_patch(current, expected, uid, replicas)
        assert patch[0]['value'] == current['metadata']['uid']
        self.patches.append(patch)
        value = self.objects[R.key(expected)]
        value['spec']['replicas'] = replicas
        value['metadata']['resourceVersion'] = str(int(value['metadata']['resourceVersion']) + 1)
        self.make_child(value)
        self.refresh_endpoints()
        return copy.deepcopy(value)
    def delete(self, obj, current):
        actual = self.objects[R.key(obj)]
        assert actual['metadata']['uid'] == current['metadata']['uid']
        self.deleted.append((R.key(obj), actual['metadata']['uid']))
        del self.objects[R.key(obj)]
        for row in self.objects.values():
            refs = row['metadata'].get('ownerReferences', [])
            row['metadata']['ownerReferences'] = [ref for ref in refs if ref['uid'] != actual['metadata']['uid']]
    def logs(self, pod, component):
        return 'password=DO_NOT_RETAIN'


class LifecycleRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.token = uuid.uuid4().hex
        self.output = Path(self.temp.name).resolve() / self.token
        self.output.mkdir(mode=0o700)
        self.material = R.fixtures.create_fixtures(self.token, self.output)
        self.images = R.build.PINS['images']
        self.plan = {'schema_version': 1, 'run_token': self.token, 'build_token': uuid.uuid4().hex, 'images': self.images,
                     'objects': R.M.render(self.images, self.token, self.material)}
        self.kube, self.forwards = FakeKube(), FakeForwards()
        FakeController.failure = FakeController.interrupt = FakeAudit.failure = False
        FakeController.mutate = None
    def execute(self):
        return R.execute(self.plan, self.material, self.output, kube=self.kube, forwarders=self.forwards,
            client_factory=lambda token, ips: token, controller_factory=FakeController, telemetry_factory=FakeAudit,
            port_check=lambda: None)
    def saved(self):
        return json.loads((self.output / 'receipt.json').read_text())
    def test_full_scale_cycle_tracks_new_pod_uid_and_cleans_known_chain(self):
        value = self.execute()
        self.assertEqual(value['result'], 'passed')
        self.assertEqual([row['to'] for row in value['scales']], [0, 1])
        self.assertNotEqual(value['scales'][0]['old_pod_uid'], value['scales'][1]['new_pod_uid'])
        self.assertEqual(len(value['children']), 5)
        self.assertFalse(self.kube.objects)
        self.assertFalse(self.forwards.active)
        self.assertTrue(value['cleanup']['verified'])
        self.assertTrue(self.kube.deleted[0][0].startswith('Deployment/'))
    def test_client_failure_cleans_children_and_preserves_pending(self):
        FakeController.failure = True
        with self.assertRaises(RuntimeError):
            self.execute()
        value = self.saved()
        self.assertEqual(value['result'], 'failed')
        self.assertTrue(value['cleanup']['verified'])
        self.assertEqual(value['observations']['pending'], 'initial')
        self.assertFalse(self.kube.objects)
    def test_audit_failure_prevents_scale_but_still_cleans(self):
        FakeAudit.failure = True
        with self.assertRaises(RuntimeError):
            self.execute()
        self.assertFalse(self.kube.patches)
        self.assertTrue(self.saved()['cleanup']['verified'])
    def test_interrupt_preserves_result_and_cleans(self):
        FakeController.failure = FakeController.interrupt = True
        with self.assertRaises(KeyboardInterrupt):
            self.execute()
        self.assertEqual(self.saved()['result'], 'interrupted')
        self.assertTrue(self.saved()['cleanup']['verified'])
    def test_unknown_deployment_create_defers_all_dependencies(self):
        self.kube.unknown_kind = 'Deployment'
        with self.assertRaises(TimeoutError):
            self.execute()
        self.assertFalse(self.kube.deleted)
        self.assertFalse(self.saved()['cleanup']['verified'])
        self.assertIn('unknown-deployment-create', self.saved()['cleanup']['deferred'])
    def test_foreign_run_labeled_pod_stops_scale_and_cleanup(self):
        def mutate():
            dep = next(row for row in self.kube.objects.values() if row['kind'] == 'Deployment')
            pod = next(copy.deepcopy(row) for row in self.kube.objects.values() if row['kind'] == 'Pod' and 'generateName' in row['metadata'])
            pod['metadata']['uid'] = str(uuid.uuid4())
            pod['metadata']['name'] += '-foreign'
            pod['metadata']['ownerReferences'][0]['uid'] = str(uuid.uuid4())
            self.kube.objects[R.key(pod)] = pod
        FakeController.mutate = staticmethod(mutate)
        with self.assertRaisesRegex(R.C.ExperimentError, 'UNOWNED_LABELED_CHILD'):
            self.execute()
        self.assertFalse(self.kube.patches)
        self.assertFalse(self.saved()['cleanup']['verified'])
        self.assertFalse(self.kube.deleted)
    def test_recovery_keeps_original_receipt_and_uses_saved_plan(self):
        self.execute()
        R.C.write(self.output / 'plan.json', self.plan)
        original = (self.output / 'receipt.json').read_bytes()
        root = self.output.parent / 'state'
        root.mkdir(mode=0o700)
        (root / 'runs').mkdir(mode=0o700)
        dest = root / 'runs' / self.token
        self.output.rename(dest)
        with patch.object(R.C, 'STATE', root):
            result = R.recover(self.token, self.kube)
        self.assertTrue(result['cleanup']['verified'])
        self.assertEqual((dest / 'receipt.json').read_bytes(), original)

    def test_late_pod_keeps_shared_config_and_isolation_policies(self):
        original_delete = self.kube.delete
        spawned = []
        def delete(obj, current):
            if obj['kind'] == 'ReplicaSet' and not spawned:
                pod = next(copy.deepcopy(row) for row in self.kube.objects.values()
                           if row['kind'] == 'Pod' and 'generateName' in row['metadata'])
                pod['metadata']['uid'] = str(uuid.uuid4())
                pod['metadata']['name'] = pod['metadata']['generateName'] + 'late0'
                self.kube.objects[R.key(pod)] = pod
                spawned.append(pod)
            original_delete(obj, current)
        self.kube.delete = delete
        with self.assertRaisesRegex(R.C.ExperimentError, 'CLEANUP_UNVERIFIED'):
            self.execute()
        value = self.saved()
        self.assertIn('late-or-unrecorded-children', value['cleanup']['deferred'])
        self.assertTrue(all(not key.startswith(('Secret/', 'ConfigMap/', 'NetworkPolicy/'))
                            for key, uid in self.kube.deleted))
        self.assertFalse(value['cleanup']['verified'])

    def test_recovery_retry_carries_children_after_parent_was_removed(self):
        records = []
        for obj in self.plan['objects']:
            current = self.kube.create(obj)
            records.append({'key': R.key(obj), 'uid': current['metadata']['uid'],
                            'content_sha256': obj['metadata']['annotations'][R.M.HASH]})
        original = {'schema_version': 1, 'run_token': self.token, 'build_token': self.plan['build_token'],
                    'images': self.images, 'plan_sha256': R.C.digest(self.plan), 'result': 'failed',
                    'objects': records, 'children': [], 'endpoints': []}
        R.C.write(self.output / 'plan.json', self.plan)
        R.C.write(self.output / 'receipt.json', original)
        initial = (self.output / 'receipt.json').read_bytes()
        root = self.output.parent / 'state'
        root.mkdir(mode=0o700)
        (root / 'runs').mkdir(mode=0o700)
        dest = root / 'runs' / self.token
        self.output.rename(dest)
        delete = self.kube.delete
        blocked = []
        def fail_once(obj, current):
            if obj['kind'] == 'EndpointSlice' and not blocked:
                blocked.append(obj['metadata']['name'])
                raise RuntimeError('endpoint api temporarily unavailable')
            delete(obj, current)
        self.kube.delete = fail_once
        with patch.object(R.C, 'STATE', root):
            with self.assertRaisesRegex(R.C.ExperimentError, 'RECOVERY_UNVERIFIED'):
                R.recover(self.token, self.kube)
            first = (dest / 'recovery.json').read_bytes()
            self.assertFalse(any(row['kind'] == 'Deployment' for row in self.kube.objects.values()))
            result = R.recover(self.token, self.kube)
        self.assertTrue(result['cleanup']['verified'])
        self.assertFalse(self.kube.objects)
        self.assertEqual(result['attempt'], 2)
        self.assertEqual((dest / result['previous_recovery']['file']).read_bytes(), first)
        self.assertEqual((dest / 'receipt.json').read_bytes(), initial)

    def test_scale_zero_waits_for_terminal_deleting_pod_to_disappear(self):
        original_scale, original_children = self.kube.scale, self.kube.children
        pending = []
        def scale(current, expected, uid, replicas):
            old = next(copy.deepcopy(row) for row in self.kube.objects.values()
                       if row['kind'] == 'Pod' and row['metadata']['labels'].get(R.M.SIZE) == 'xs') if replicas == 0 else None
            result = original_scale(current, expected, uid, replicas)
            if replicas == 0:
                old['status']['phase'] = 'Succeeded'
                old['metadata']['deletionTimestamp'] = '2026-09-17T07:00:00Z'
                self.kube.objects[R.key(old)] = old
                pending.append([old, 2])
            return result
        def children(token):
            result = original_children(token)
            for item in pending:
                item[1] -= 1
                if item[1] == 0:
                    self.kube.objects.pop(R.key(item[0]), None)
            return result
        self.kube.scale, self.kube.children = scale, children
        self.assertEqual(self.execute()['result'], 'passed')
        self.assertFalse(self.kube.objects)


if __name__ == '__main__':
    unittest.main()
