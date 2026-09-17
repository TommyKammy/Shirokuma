"""Offline orchestration tests: no Kubernetes, Docker, port, or SDK calls."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from tools.local_experiment.spark import run as R

C = R.C
TOKEN = 'a'*32
ENDPOINTS = [{'ip': '10.43.0.1', 'port': 443}, {'ip': '192.168.5.1', 'port': 55737}]


def actual(obj):
    obj = deepcopy(obj)
    obj['metadata'].update(uid=str(uuid.uuid4()), resourceVersion='10')
    return obj


def result(mode='success'):
    value = C.expected_output(TOKEN, mode)
    value['partitions'] = [{'partition_id': index, 'stage_id': 3, 'task_attempt_id': 7+index,
                            'attempt_number': 0, 'executor_id': '1', 'hostname': C.executor_name(TOKEN)}
                           for index in range(2)]
    return value


class Clock:
    def __init__(self):
        self.value = 0

    def now(self):
        return self.value

    def sleep(self, _):
        self.value += 100


class FakeKube:
    def __init__(self, mode='success'):
        self.mode, self.objects, self.created, self.deleted = mode, {}, [], []
        self.failure, self.mutate, self.no_executor, self.exit, self.running = None, None, False, None, False
        self.before_create = None
        self.pod_calls, self.phase_lag = 0, 0
        self.inventory_calls = 0
        self.late = None
        self.log = C.RESULT_PREFIX + json.dumps(result(mode))

    def preflight(self):
        return {'runtime': {'node_name': R.CONTEXT, 'node_uid': str(uuid.uuid4())},
                'image': {'manifest_digest': R.P.DIGEST}, 'api_endpoints': ENDPOINTS}

    def recovery_guard(self, _):
        pass

    def get(self, kind, name):
        return deepcopy(self.objects.get((kind, name)))

    def create(self, obj):
        if self.before_create:
            self.before_create(obj)
        key = (obj['kind'], obj['metadata']['name'])
        if self.failure == key:
            raise R.ExperimentError('CREATE_FAILED')
        value = actual(obj)
        self.objects[key] = value
        self.created.append(key)
        return deepcopy(value)

    def pods(self):
        self.pod_calls += 1
        driver = self.get('Pod', 'driver')
        if driver is None:
            return []
        driver['spec']['nodeName'] = R.CONTEXT
        driver['status'] = {'phase': 'Running' if self.running else 'Succeeded' if self.mode == 'success' else 'Failed',
                            'containerStatuses': [{'name': 'spark-driver', 'imageID': 'docker://'+R.P.DIGEST,
                              'state': {'running': {}} if self.running else {'terminated': {
                                'exitCode': self.exit if self.exit is not None else C.expected_exit_code(self.mode)}}}]}
        if self.pod_calls <= self.phase_lag:
            driver['status']['phase'] = 'Running'
        values = [driver]
        if not self.no_executor:
            executor = self.get('Pod', C.executor_name(TOKEN))
            if executor is None:
                executor = actual(C.executor_template(TOKEN))
                executor['metadata']['name'] = C.executor_name(TOKEN)
                executor['metadata']['ownerReferences'] = [{'apiVersion': 'v1', 'kind': 'Pod', 'name': 'driver',
                    'uid': driver['metadata']['uid'], 'controller': True, 'blockOwnerDeletion': True}]
            executor['spec'].update(nodeName=R.CONTEXT, hostname=C.executor_name(TOKEN))
            executor['status'] = {'phase': 'Running', 'containerStatuses': [{'imageID': R.P.DIGEST, 'state': {'running': {}}}]}
            values.append(executor)
            self.objects[('Pod', C.executor_name(TOKEN))] = deepcopy(executor)
        if self.mutate:
            self.mutate(values)
        return values

    def logs(self):
        return self.log

    def inventory(self):
        self.inventory_calls += 1
        values = [deepcopy(value) for (kind, _), value in self.objects.items() if kind != 'Namespace']
        if self.late and self.inventory_calls > 1:
            values.append(self.late)
        return values

    def delete(self, obj):
        key = (obj['kind'], obj['metadata']['name'])
        assert self.objects[key]['metadata']['uid'] == obj['metadata']['uid']
        self.deleted.append((*key, obj['metadata']['uid']))
        del self.objects[key]


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.output = self.root / TOKEN
        self.output.mkdir(mode=0o700)
        self.network = patch.object(R.P, 'command', side_effect=AssertionError('NO_RUNTIME_CALLS'))
        self.network.start()

    def tearDown(self):
        self.network.stop()
        self.temp.cleanup()

    def experiment(self, kube=None, mode='success'):
        clock = Clock()
        return R.Experiment(TOKEN, mode, self.output, kube or FakeKube(mode), clock=clock.now, sleep=clock.sleep)

    def test_success_observed_executor_oracle_and_all_known_uid_cleanup(self):
        kube = FakeKube(); exp = self.experiment(kube)
        value = exp.run()
        self.assertEqual(value['result'], 'passed')
        self.assertEqual(value['output']['groups'], C.oracle())
        self.assertEqual(len(value['known']), 15)
        self.assertEqual(len(kube.deleted), 15)
        self.assertFalse(kube.objects)
        self.assertEqual(value['pods']['executor']['name'], C.executor_name(TOKEN))
        self.assertEqual((self.output/'summary.json').stat().st_mode & 0o777, 0o600)

    def test_controlled_failure_requires_correct_oracle_and_exit17(self):
        value = self.experiment(mode='failure').run()
        self.assertEqual(value['result'], 'passed')
        self.assertEqual(value['output']['result'], 'controlled-failure')
        self.assertEqual(value['driver_exit_code'], 17)

    def test_wrong_exit_preserves_logs_and_cleans(self):
        kube = FakeKube(); kube.exit = 17
        exp = self.experiment(kube)
        with self.assertRaisesRegex(R.ExperimentError, 'DRIVER_EXIT_MISMATCH'):
            exp.run()
        self.assertTrue(exp.receipt['cleanup']['verified'])
        self.assertTrue((self.output/'driver-log.json').exists())
        self.assertTrue((self.output/'final-pods.json').exists())

    def test_forged_result_is_not_intentional_failure(self):
        kube = FakeKube('failure')
        wrong = result('failure'); wrong['groups'][0]['total'] += 1
        kube.log = C.RESULT_PREFIX+json.dumps(wrong)
        exp = self.experiment(kube, 'failure')
        with self.assertRaises(C.ContractError):
            exp.run()
        self.assertFalse(exp.receipt.get('workload_verified', False))
        self.assertTrue(exp.receipt['cleanup']['verified'])

    def test_namespace_collision_performs_no_create_or_delete(self):
        kube = FakeKube()
        ns = actual(C.render(TOKEN, 'success', api_endpoints=ENDPOINTS)[0])
        kube.objects[('Namespace', C.namespace(TOKEN))] = ns
        with self.assertRaisesRegex(R.ExperimentError, 'NAMESPACE_ALREADY_EXISTS'):
            self.experiment(kube).run()
        self.assertFalse(kube.created or kube.deleted)

    def test_unknown_namespace_create_has_durable_intent_and_no_cleanup(self):
        kube = FakeKube(); kube.failure = ('Namespace', C.namespace(TOKEN))
        def check(_):
            saved = json.loads((self.output/'summary.json').read_text())
            self.assertTrue(saved['creation_attempted'])
            self.assertEqual(saved['pending']['operation'], 'create')
        kube.before_create = check
        exp = self.experiment(kube)
        with self.assertRaises(R.ExperimentError):
            exp.run()
        self.assertEqual(exp.receipt['cleanup']['deferred'], 'NAMESPACE_UID_UNKNOWN')
        self.assertFalse(kube.deleted)

    def test_known_partial_create_failure_cleans_only_created(self):
        kube = FakeKube(); kube.failure = ('Role', 'spark-driver')
        exp = self.experiment(kube)
        with self.assertRaises(R.ExperimentError):
            exp.run()
        self.assertTrue(exp.receipt['cleanup']['verified'])
        self.assertEqual({x[:2] for x in kube.deleted}, set(kube.created))

    def test_no_executor_never_counts_driver_json_as_execution_evidence(self):
        kube = FakeKube(); kube.no_executor = True
        with self.assertRaisesRegex(R.ExperimentError, 'POD_EXECUTION_NOT_OBSERVED'):
            self.experiment(kube).run()

    def test_wrong_image_id_is_rejected(self):
        kube = FakeKube()
        kube.mutate = lambda pods: pods[0]['status']['containerStatuses'][0].update(imageID='docker://sha256:'+'0'*64)
        with self.assertRaisesRegex(R.ExperimentError, 'POD_IMAGE_ID_CONFLICT'):
            self.experiment(kube).run()

    def test_foreign_executor_owner_is_preserved(self):
        kube = FakeKube()
        def mutate(pods):
            pods[1]['metadata']['ownerReferences'][0]['uid'] = str(uuid.uuid4())
            kube.objects[('Pod', C.executor_name(TOKEN))] = deepcopy(pods[1])
        kube.mutate = mutate
        exp = self.experiment(kube)
        with self.assertRaisesRegex(R.ExperimentError, 'EXECUTOR_OWNER_CONFLICT'):
            exp.run()
        self.assertEqual(exp.receipt['cleanup']['deferred'], 'UNKNOWN_NAMESPACE_RESOURCE')
        self.assertFalse(kube.deleted)

    def test_namespace_uid_replacement_blocks_all_cleanup(self):
        kube = FakeKube()
        def mutate(_):
            kube.objects[('Namespace', C.namespace(TOKEN))]['metadata']['uid'] = str(uuid.uuid4())
        kube.mutate = mutate
        exp = self.experiment(kube)
        with self.assertRaisesRegex(R.ExperimentError, 'CLEANUP_NOT_VERIFIED'):
            exp.run()
        self.assertEqual(exp.receipt['cleanup']['deferred'], 'RESOURCE_UID_CONFLICT')
        self.assertFalse(kube.deleted)

    def test_unknown_resource_blocks_namespace_gc(self):
        kube = FakeKube()
        def mutate(_):
            kube.objects[('Secret', 'foreign')] = actual({'apiVersion': 'v1', 'kind': 'Secret',
                'metadata': {'name': 'foreign', 'namespace': C.namespace(TOKEN)}})
        kube.mutate = mutate
        exp = self.experiment(kube)
        with self.assertRaises(R.ExperimentError):
            exp.run()
        self.assertFalse(kube.deleted)
        self.assertEqual(exp.receipt['cleanup']['deferred'], 'UNKNOWN_NAMESPACE_RESOURCE')

    def test_late_foreign_endpointslice_blocks_namespace_gc(self):
        kube = FakeKube()
        kube.late = actual({'apiVersion': 'discovery.k8s.io/v1', 'kind': 'EndpointSlice',
            'metadata': {'name': 'foreign', 'namespace': C.namespace(TOKEN), 'ownerReferences': []}})
        exp = self.experiment(kube)
        with self.assertRaises(R.ExperimentError):
            exp.run()
        self.assertEqual(exp.receipt['cleanup']['deferred'], 'AUTOMATIC_ENDPOINT_CONFLICT')
        self.assertIn(('Namespace', C.namespace(TOKEN)), kube.objects)

    def test_original_cancellation_retained_after_cleanup(self):
        kube = FakeKube(); cancel = R.SESSION.SessionInterrupted(15)
        def stop(obj):
            if obj['kind'] == 'Role':
                raise cancel
        kube.before_create = stop
        exp = self.experiment(kube)
        with self.assertRaises(R.SESSION.SessionInterrupted) as caught:
            exp.run()
        self.assertIs(caught.exception, cancel)
        self.assertTrue(exp.receipt['cleanup']['verified'])
        self.assertEqual(exp.receipt['result'], 'failed')

    def test_timeout_is_not_success_and_keeps_diagnostics(self):
        kube = FakeKube(); kube.running = True
        exp = self.experiment(kube)
        with self.assertRaisesRegex(R.ExperimentError, 'WORKLOAD_TIMEOUT'):
            exp.run()
        self.assertTrue(exp.receipt['cleanup']['verified'])
        self.assertTrue((self.output/'driver-log.json').exists())

    def test_recovery_preserves_original_receipt_and_does_not_render_current_spec(self):
        kube = FakeKube(); exp = self.experiment(kube); exp.run()
        original = (self.output/'summary.json').read_bytes()
        with patch.object(R, 'STATE', self.root), patch.object(C, 'render', side_effect=AssertionError('NO_NEW_RENDER')):
            value = R.recover(TOKEN, kube)
        self.assertTrue(value['cleanup']['verified'])
        self.assertEqual((self.output/'summary.json').read_bytes(), original)

    def test_saved_plan_tampering_refuses_recovery(self):
        kube = FakeKube(); exp = self.experiment(kube); exp.run()
        plan = json.loads((self.output/'plan.json').read_text())
        plan[-1]['metadata']['namespace'] = 'shirokuma-dev'
        R.save(self.output/'plan.json', plan)
        with patch.object(R, 'STATE', self.root), self.assertRaisesRegex(R.ExperimentError, 'SAVED_PLAN_HASH_CONFLICT'):
            R.recover(TOKEN, kube)

    def test_saved_endpoint_records_have_fixed_scope_types_and_identity(self):
        exp = self.experiment(); exp.run()
        service = next(x for x in exp.receipt['known'] if x['kind'] == 'Service')
        endpoint = {'kind': 'EndpointSlice', 'name': 'driver-abc12', 'uid': str(uuid.uuid4()),
                    'resource_version': '10', 'service_uid': service['uid']}
        valid = deepcopy(exp.receipt)
        valid['automatic_endpoints'] = [endpoint]
        R.validate_saved(exp.plan, valid)
        mutations = [{'kind': 'Pod'}, {'kind': 'Endpoints'}, {'name': '../../driver'},
                     {'name': 'other-abc12'}, {'name': 'driver-abc123'}, {'uid': True},
                     {'uid': 'invalid'}, {'resource_version': 10}, {'resource_version': ''},
                     {'resource_version': '\u0661'}, {'resource_version': '1'*33},
                     {'service_uid': str(uuid.uuid4())}, {'extra': 'field'}]
        invalid = [None, {}, [endpoint, endpoint], [{'kind': 'EndpointSlice'}]]
        invalid += [[{**endpoint, **change}] for change in mutations]
        invalid += [[{**endpoint, 'name': 'driver-'+str(index).zfill(5), 'uid': str(uuid.uuid4())}
                     for index in range(9)]]
        for records in invalid:
            with self.subTest(records=records):
                receipt = deepcopy(exp.receipt); receipt['automatic_endpoints'] = records
                with self.assertRaisesRegex(R.ExperimentError, 'SAVED_ENDPOINT_IDENTITIES_INVALID'):
                    R.validate_saved(exp.plan, receipt)

    def test_recovery_rejects_forged_endpoint_before_runtime_guard_or_delete(self):
        kube = FakeKube(); exp = self.experiment(kube); exp.run()
        receipt = deepcopy(exp.receipt)
        service = next(x for x in receipt['known'] if x['kind'] == 'Service')
        receipt['automatic_endpoints'] = [{'kind': 'Pod', 'name': 'foreign', 'uid': str(uuid.uuid4()),
            'resource_version': '1', 'service_uid': service['uid']}]
        R.save(self.output/'summary.json', receipt)
        before = list(kube.deleted)
        with patch.object(R, 'STATE', self.root), patch.object(kube, 'recovery_guard') as guard:
            with self.assertRaisesRegex(R.ExperimentError, 'SAVED_ENDPOINT_IDENTITIES_INVALID'):
                R.recover(TOKEN, kube)
            guard.assert_not_called()
        self.assertEqual(kube.deleted, before)

    def test_unknown_namespace_old_pending_intent_cannot_be_verified_empty(self):
        exp = self.experiment()
        exp.receipt['pending'] = {'operation': 'create', 'kind': 'Namespace', 'name': C.namespace(TOKEN)}
        self.assertEqual(exp.cleanup()['deferred'], 'NAMESPACE_UID_UNKNOWN')

    def test_terminated_container_waits_for_pod_phase_to_catch_up(self):
        kube = FakeKube(); kube.phase_lag = 1
        value = self.experiment(kube).run()
        self.assertEqual(value['result'], 'passed')
        self.assertGreaterEqual(kube.pod_calls, 2)

    def test_namespace_replaced_before_child_create_is_refused(self):
        kube = FakeKube()
        original_create = kube.create
        def create(obj):
            value = original_create(obj)
            if obj['kind'] == 'Namespace':
                kube.objects[('Namespace', C.namespace(TOKEN))]['metadata']['uid'] = str(uuid.uuid4())
            return value
        kube.create = create
        exp = self.experiment(kube)
        with self.assertRaisesRegex(R.ExperimentError, 'RESOURCE_UID_CONFLICT'):
            exp.run()
        self.assertEqual(kube.created, [('Namespace', C.namespace(TOKEN))])
        self.assertFalse(kube.deleted)

    def test_first_cancellation_during_cleanup_is_not_lost(self):
        kube = FakeKube(); cancel = R.SESSION.SessionInterrupted(15)
        def delete(_):
            raise cancel
        kube.delete = delete
        exp = self.experiment(kube)
        with self.assertRaises(R.SESSION.SessionInterrupted) as caught:
            exp.run()
        self.assertIs(caught.exception, cancel)
        self.assertEqual(exp.receipt['result'], 'failed')
        self.assertFalse(exp.receipt['cleanup']['verified'])

    def test_automatic_endpoint_uid_saved_before_service_delete(self):
        kube = FakeKube()
        original_create, original_delete = kube.create, kube.delete
        def create(obj):
            value = original_create(obj)
            if obj['kind'] == 'Service':
                for kind, name in [('Endpoints', 'driver'), ('EndpointSlice', 'driver-abc12')]:
                    metadata = {'name': name, 'namespace': C.namespace(TOKEN),
                        'labels': {'kubernetes.io/service-name': 'driver'}}
                    if kind == 'EndpointSlice':
                        metadata['ownerReferences'] = [{'kind': 'Service', 'uid': value['metadata']['uid']}]
                    kube.objects[(kind, name)] = actual({'kind': kind, 'metadata': metadata})
            return value
        def delete(obj):
            if obj['kind'] == 'Service':
                saved = json.loads((self.output/'summary.json').read_text())
                self.assertEqual(len(saved['automatic_endpoints']), 2)
                # Even an orphan transition is safe only for the already saved UID.
                kube.objects[('EndpointSlice', 'driver-abc12')]['metadata'].pop('ownerReferences')
            return original_delete(obj)
        kube.create, kube.delete = create, delete
        value = self.experiment(kube).run()
        self.assertTrue(value['cleanup']['verified'])
        self.assertEqual(len(value['automatic_endpoints']), 2)
        self.assertEqual(len(kube.deleted), 17)
        self.assertFalse(kube.objects)


class BoundaryTests(unittest.TestCase):
    def test_real_observed_high_api_port_is_projected_exactly(self):
        service = {'metadata': {'name': 'kubernetes', 'namespace': 'default'},
            'spec': {'clusterIP': '10.43.0.1', 'ports': [{'port': 443, 'targetPort': 55737, 'protocol': 'TCP'}]}}
        endpoints = {'metadata': {'name': 'kubernetes', 'namespace': 'default'},
            'subsets': [{'addresses': [{'ip': '192.168.5.1'}], 'ports': [{'port': 55737, 'protocol': 'TCP'}]}]}
        self.assertEqual(R.api_addresses(service, endpoints), ENDPOINTS)
        endpoints['subsets'][0]['ports'][0]['port'] = 6443
        with self.assertRaisesRegex(R.ExperimentError, 'API_ENDPOINT_PORT_MISMATCH'):
            R.api_addresses(service, endpoints)

    def test_quantity_cpu_and_memory_keep_units(self):
        self.assertEqual(R.quantity('500m', cpu=True), .5)
        self.assertEqual(R.quantity('2048Mi'), R.quantity('2Gi'))
        with self.assertRaises(R.ExperimentError):
            R.quantity('NaN')

    def test_executor_hostpath_mount_is_not_allowed(self):
        pod = actual(C.executor_template(TOKEN))
        pod['metadata']['name'] = C.executor_name(TOKEN)
        pod['spec']['volumes'][0] = {'name': 'work', 'hostPath': {'path': '/'}}
        with self.assertRaisesRegex(R.ExperimentError, 'POD_VOLUME_CONFLICT'):
            R.validate_pod(pod, TOKEN, 'executor', driver_uid=str(uuid.uuid4()))

    def test_fixed_command_paths_and_uid_resource_version_delete(self):
        obj = actual(C.render(TOKEN, 'success', api_endpoints=ENDPOINTS)[0])
        with patch.object(R.P, 'command', return_value='{}') as command:
            R.Kube(TOKEN).delete(obj)
        args, kwargs = command.call_args
        self.assertIn('--context', args[0]); self.assertIn(R.CONTEXT, args[0])
        self.assertIn('--raw=/api/v1/namespaces/'+C.namespace(TOKEN), args[0])
        self.assertEqual(json.loads(kwargs['body'])['preconditions'],
                         {'uid': obj['metadata']['uid'], 'resourceVersion': '10'})


if __name__ == '__main__':
    unittest.main()
