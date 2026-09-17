import copy
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from tools.local_experiment.gateway_warehouse import run as R


@contextmanager
def no_tunnel(pod):
    yield


class FakeKube:
    def __init__(self):
        self.objects, self.deleted, self.created = {}, [], []
        self.unknown = False
        self.conflict = False

    def get(self, obj):
        return copy.deepcopy(self.objects.get(R.identity(obj)))

    def dry_run(self, obj):
        pass

    def create(self, obj):
        current = copy.deepcopy(obj)
        current['metadata'].update(uid=str(uuid.uuid4()), resourceVersion='1')
        if obj['kind'] == 'Pod':
            current['status'] = {'phase': 'Running', 'containerStatuses': [
                {'name': row['name'], 'ready': True, 'restartCount': 0} for row in obj['spec']['containers']]}
        self.objects[R.identity(obj)] = current
        self.created.append(R.identity(obj))
        if self.unknown and obj['kind'] == 'Pod':
            raise TimeoutError('unknown create response')
        return copy.deepcopy(current)

    def delete(self, obj, current):
        actual = self.objects[R.identity(obj)]
        assert current['metadata']['uid'] == actual['metadata']['uid']
        self.deleted.append(R.identity(obj))
        del self.objects[R.identity(obj)]

    def logs(self, pod, component):
        return 'password=DO_NOT_RETAIN credential OTHER_SECRET_VALUE'


class FakeClient:
    failure = False
    incomplete = False
    mutate = None

    def __init__(self, token):
        self.value = {'run_token': token, 'result': 'pending', 'pending': None, 'uncaptured_requests': 0,
            'query_cleanup_verified': False, 'cases': [], 'requests': [], 'readiness': [], 'history': None}

    def register_backends(self, progress=None, guard=None):
        guard()
        self.value['registration'] = {'result': 'verified', 'initial_registry_empty': True,
            'readback_verified': True, 'attempts': [{'result': 'confirmed', 'http_status': 200} for _ in range(2)]}
        self.value['unconfirmed_registrations'] = 0
        progress(self.receipt())

    def ready(self):
        self.value['readiness'].append({'ready': True})
        return {'ready': True}

    def run_cases(self, progress=None):
        self.value['pending'] = {'case': 'xs_before'}
        progress(self.receipt())
        if self.mutate:
            self.mutate()
        if self.failure:
            raise TimeoutError('OTHER_SECRET_VALUE')
        self.value['cases'] = [{'case': case, 'matched': True, 'response_complete': True,
                               'opposite_route_gets': 1} for case, _, _ in R.CASES]
        self.value['pending'] = None
        progress(self.receipt())

    def verify_history(self, progress=None):
        self.value.update(result='verified', query_cleanup_verified=True)
        self.value['history'] = {'verified': True, 'query_count': 6, 'backends': {'xs': [{}, {}, {}], 's': [{}, {}, {}]},
            'unknown_source_counts': {'xs': 0, 's': 0}, 'missing_source_counts': {'xs': 0, 's': 0}}
        self.value['requests'] = [{'response_complete': True} for _ in range(10)]
        if self.incomplete:
            self.value['uncaptured_requests'] = 1
        progress(self.receipt())

    def receipt(self):
        return copy.deepcopy(self.value)


class GatewayRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name).resolve() / 'output'
        self.output.mkdir(mode=0o700)
        self.token = uuid.uuid4().hex
        self.material = R.fixtures.create_fixtures(self.token, self.output)
        self.material['redaction_values'].append('OTHER_SECRET_VALUE')
        self.images = json.loads((R.C.HERE / 'inputs.lock.json').read_text())['images']
        self.plan = {'schema_version': 1, 'run_token': self.token, 'build_token': uuid.uuid4().hex,
                     'images': self.images, 'objects': R.M.render(self.images, self.token, self.material)}
        self.kube = FakeKube()
        self.db_calls = []
        FakeClient.failure = FakeClient.incomplete = False
        FakeClient.mutate = None

    def database(self, token, pod, uid, output, guard):
        self.assertEqual(guard()['metadata']['uid'], uid)
        self.db_calls.append(uid)
        return {'result': 'passed'}

    def execute(self):
        return R.execute(self.plan, self.material, self.output, kube=self.kube, tunnel_factory=no_tunnel,
                         client_factory=FakeClient, persistence_factory=self.database, port_check=lambda: None)

    def saved(self):
        return json.loads((self.output / 'receipt.json').read_text())

    def test_success_requires_observation_database_and_owned_cleanup(self):
        value = self.execute()
        self.assertEqual(value['result'], 'passed')
        self.assertEqual(len(value['readiness']), 4)
        self.assertEqual(len(self.db_calls), 1)
        self.assertEqual(len(self.kube.deleted), 4)
        self.assertTrue(self.kube.deleted[0].startswith('Pod/'))
        self.assertTrue(value['cleanup']['verified'])
        self.assertEqual(self.kube.objects, {})

    def test_client_failure_keeps_pending_evidence_and_redacts_logs(self):
        FakeClient.failure = True
        with self.assertRaises(TimeoutError):
            self.execute()
        value = self.saved()
        self.assertEqual(value['result'], 'failed')
        self.assertEqual(value['observations']['pending'], {'case': 'xs_before'})
        self.assertEqual(len(self.kube.deleted), 4)
        self.assertFalse(self.db_calls)
        for p in self.output.glob('diagnostic-*.json'):
            self.assertNotIn('OTHER_SECRET_VALUE', p.read_text())
            self.assertNotIn('DO_NOT_RETAIN', p.read_text())

    def test_incomplete_client_never_reaches_database_or_passes(self):
        FakeClient.incomplete = True
        with self.assertRaisesRegex(R.C.ExperimentError, 'CLIENT_INCOMPLETE'):
            self.execute()
        self.assertFalse(self.db_calls)
        self.assertEqual(self.saved()['result'], 'failed')
        self.assertEqual(len(self.kube.deleted), 4)

    def test_unknown_pod_create_retains_dependencies(self):
        self.kube.unknown = True
        with self.assertRaises(TimeoutError):
            self.execute()
        value = self.saved()
        self.assertFalse(value['cleanup']['verified'])
        self.assertEqual(len(value['cleanup']['deferred']), 1)
        self.assertFalse(self.kube.deleted)
        self.assertEqual(len(self.kube.objects), 4)

    def test_name_collision_does_not_touch_existing_objects(self):
        first = self.plan['objects'][0]
        self.kube.objects[R.identity(first)] = {'retained': True}
        with self.assertRaisesRegex(R.C.ExperimentError, 'OBJECT_NAME_COLLISION'):
            self.execute()
        self.assertFalse(self.kube.created)
        self.assertFalse(self.kube.deleted)

    def test_changed_pod_uid_blocks_database_and_dependency_deletion(self):
        def mutate():
            pod = self.kube.objects[R.identity(self.plan['objects'][-1])]
            pod['metadata']['uid'] = str(uuid.uuid4())
        FakeClient.mutate = staticmethod(mutate)
        with self.assertRaisesRegex(R.C.ExperimentError, 'OBJECT_IDENTITY_CONFLICT'):
            self.execute()
        self.assertFalse(self.kube.deleted)
        self.assertFalse(self.saved()['cleanup']['verified'])
        self.assertFalse(self.db_calls)

    def test_database_failure_is_not_hidden_by_successful_cleanup(self):
        def fail(*args, **kwargs):
            raise R.C.ExperimentError('RESTORE_MISMATCH')
        self.database = fail
        with self.assertRaisesRegex(R.C.ExperimentError, 'RESTORE_MISMATCH'):
            self.execute()
        self.assertEqual(self.saved()['failure_code'], 'RESTORE_MISMATCH')
        self.assertTrue(self.saved()['cleanup']['verified'])
        self.assertEqual(self.saved()['result'], 'failed')

    def test_replacement_during_tunnel_stops_before_registration(self):
        @contextmanager
        def replaced(pod):
            self.kube.objects[R.identity(pod)]['metadata']['uid'] = str(uuid.uuid4())
            yield
        with patch.object(FakeClient, 'register_backends') as register:
            with self.assertRaisesRegex(R.C.ExperimentError, 'OBJECT_IDENTITY_CONFLICT'):
                R.execute(self.plan, self.material, self.output, kube=self.kube, tunnel_factory=replaced,
                          client_factory=FakeClient, persistence_factory=self.database, port_check=lambda: None)
            register.assert_not_called()
        self.assertFalse(self.kube.deleted)
        self.assertFalse(self.saved()['cleanup']['verified'])

    def test_keyboard_interrupt_still_cleans_owned_objects(self):
        def interrupt(*args, **kwargs):
            raise KeyboardInterrupt()
        self.database = interrupt
        with self.assertRaises(KeyboardInterrupt):
            self.execute()
        self.assertEqual(self.saved()['result'], 'interrupted')
        self.assertTrue(self.saved()['cleanup']['verified'])

    def test_recovery_uses_original_saved_plan_and_keeps_receipt(self):
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


if __name__ == '__main__':
    unittest.main()
