"""Offline orchestration tests: no Kubernetes, Docker, port, or SDK calls."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from tools.local_experiment.spark import run as R
from tools.local_experiment.spark_iceberg import remote_contract as RC

C = R.C
TOKEN = 'a'*32
ENDPOINTS = [{'ip': '10.43.0.1', 'port': 443}, {'ip': '192.168.5.1', 'port': 55737}]


def remote_inputs():
    return {'build_token': 'b'*32, 'image': RC.IMAGE,
            'table_uuid': '11111111-1111-1111-1111-111111111111',
            'initial_snapshot_id': 122,
            'table_location': 's3://shirokuma-lakehouse/l1/local_experiment/spark_'+TOKEN,
            'polaris_uri': RC.POLARIS_URI, 'polaris_realm': 'POLARIS', 'warehouse': 'shirokuma_l1',
            'scope': 'PRINCIPAL_ROLE:spark_'+TOKEN, 's3_endpoint': RC.S3_ENDPOINT,
            's3_region': 'us-east-1', 'data_egress': deepcopy(RC.DATA_EGRESS)}


def remote_result():
    inputs = remote_inputs(); location = inputs['table_location']
    return {'schema_version': 1, 'run_token': TOKEN, 'namespace': C.namespace(TOKEN), 'scope': RC.SCOPE,
            'mode': 'success', 'configured_image': RC.IMAGE, 'result': 'passed',
            'spark_version': C.VERSION, 'iceberg_version': RC.L.ICEBERG_VERSION,
            'master': 'k8s://https://kubernetes.default.svc:443', 'checks': dict.fromkeys(RC.CHECKS, True),
            'row_count': 32, 'dataframe_rows': RC.L.oracle_rows(), 'sql_rows': RC.L.oracle_rows(),
            'groups': RC.L.oracle_groups(), 'partitions': result()['partitions'], 'shuffle_observed': True,
            'table_identity': {'uuid': inputs['table_uuid'], 'location': location, 'comment': RC.marker(TOKEN),
                              'snapshot_id': 123, 'parent_snapshot_id': inputs['initial_snapshot_id'],
                              'metadata_location': location+'/metadata/v1.metadata.json',
                              'operation': 'append'},
            'data_files': [{'path': location+'/data/test.parquet', 'record_count': 32, 'file_size_in_bytes': 123}],
            'error_code': None, 'error_classes': []}


def redact(value):
    return value.replace('AWS_SENTINEL', '[redacted]').replace('JWT_SENTINEL', '[redacted]')


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


class RemoteKube(FakeKube):
    redactor = staticmethod(redact)

    def __init__(self, *, profile='iceberg-write', inputs=None, output=None):
        super().__init__()
        self.remote = R.remote_contract(profile)
        self.inputs = remote_inputs() if inputs is None else inputs
        self.log = 'AWS_SENTINEL JWT_SENTINEL\n'+self.remote.RESULT_PREFIX+json.dumps(
            remote_result() if output is None else output)
        self.remote_mutate = None

    def preflight(self):
        value = super().preflight()
        value['image'] = {'image': self.inputs['image'], 'image_id': self.inputs['image'].split('@')[1]}
        return value

    def create(self, obj):
        value = super().create(obj)
        if obj['kind'] == 'Secret':
            value.pop('keys')
            value['data'] = {'aws-credentials': 'AWS_SENTINEL', 'polaris-token': 'JWT_SENTINEL'}
            value['metadata']['annotations']['raw'] = 'JWT_SENTINEL'
            self.objects[('Secret', 'connection')] = deepcopy(value)
        return value

    def pods(self):
        values = super().pods()
        for pod in values:
            role = pod['metadata']['labels']['shirokuma.io/role']
            if role == 'executor':
                pod['spec'] = self.remote.expected_pod_spec(TOKEN, role, self.inputs)
                pod['spec'].update(nodeName=R.CONTEXT, hostname=C.executor_name(TOKEN))
            pod['status']['containerStatuses'][0]['imageID'] = 'docker://'+self.inputs['image'].split('@')[1]
            if self.remote_mutate:
                self.remote_mutate(pod)
            self.objects[('Pod', pod['metadata']['name'])] = deepcopy(pod)
        return values


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


class RemoteRunnerTests(unittest.TestCase):
    setUp = RunnerTests.setUp
    tearDown = RunnerTests.tearDown

    def experiment(self, kube=None):
        clock = Clock()
        return R.Experiment(TOKEN, 'success', self.output, kube or RemoteKube(), profile='iceberg-write',
                            profile_inputs=remote_inputs(), clock=clock.now, sleep=clock.sleep)

    def assert_no_secrets_saved(self):
        for path in self.output.rglob('*'):
            if path.is_file():
                with self.subTest(path=path.name):
                    self.assertNotIn('SENTINEL', path.read_text())

    def test_fixed_profile_dispatch_rejects_import_paths_and_benchmark_failure_mode(self):
        self.assertIs(R.remote_contract(), RC)
        self.assertIs(R.remote_contract('iceberg-write'), RC)
        for profile in ('os', 'tools.local_experiment.spark_benchmark.contract', '', None, True):
            with self.subTest(profile=profile), self.assertRaisesRegex(R.ExperimentError, 'PROFILE_INVALID'):
                R.remote_contract(profile)
        with self.assertRaisesRegex(R.ExperimentError, 'REMOTE_MODE_INVALID'):
            R.validate_profile('iceberg-benchmark', {}, 'failure')

    def test_remote_pass_uses_shared_lifecycle_without_saving_raw_secret_or_log(self):
        kube = RemoteKube(); exp = self.experiment(kube)
        receipt = exp.run()
        self.assertEqual(receipt['result'], 'passed')
        self.assertEqual(len(exp.plan), 16)
        self.assertEqual(len(receipt['known']), 17)
        self.assertEqual(len(kube.deleted), 17)
        self.assertFalse(kube.objects)
        self.assertEqual(receipt['output']['row_count'], 32)
        self.assertEqual(receipt['profile_inputs']['initial_snapshot_id'], 122)
        self.assertEqual(receipt['output']['table_identity']['parent_snapshot_id'], 122)
        for filename in ('plan.json', 'created-Secret-connection.json', 'cleanup-inventory-before.json'):
            value = json.loads((self.output/filename).read_text())
            obj = next(x for x in value if x['kind'] == 'Secret') if type(value) is list else value
            R.validate_secret(obj, TOKEN, live=filename != 'plan.json')
        self.assert_no_secrets_saved()

    def test_remote_failure_diagnostics_and_inventory_do_not_save_secret_values(self):
        kube = RemoteKube(); kube.exit = 17
        exp = self.experiment(kube)
        with self.assertRaisesRegex(R.ExperimentError, 'DRIVER_EXIT_MISMATCH'):
            exp.run()
        self.assertTrue(exp.receipt['cleanup']['verified'])
        self.assertTrue((self.output/'final-pods.json').exists())
        self.assertNotIn('diagnostic_output', exp.receipt)
        self.assertEqual(exp.receipt['diagnostic_errors']['driver-log.json'], 'DIAGNOSTIC_OUTPUT_NOT_FAILED')
        self.assert_no_secrets_saved()

    @staticmethod
    def failed_before_precreated_check():
        # Same partial observation shape as live fa34d22d..., with the new class-only diagnostic field.
        value = remote_result()
        value.update(result='failed', checks=dict.fromkeys(RC.CHECKS, False), row_count=None,
                     dataframe_rows=None, sql_rows=None, groups=None, partitions=None,
                     shuffle_observed=False, table_identity=None, data_files=None,
                     error_code='PRECREATED_IDENTITY_MISMATCH',
                     error_classes=['py4j.protocol.Py4JJavaError', 'java.lang.IllegalArgumentException'])
        value['checks']['classes_loaded'] = True
        return value

    def test_failed_driver_payload_is_diagnostic_only_even_without_executor(self):
        kube = RemoteKube(); kube.exit = 1; kube.no_executor = True
        failed = self.failed_before_precreated_check()
        kube.log = 'JWT_SENTINEL AWS_SENTINEL\n'+RC.RESULT_PREFIX+json.dumps(failed)
        exp = self.experiment(kube)
        with self.assertRaisesRegex(R.ExperimentError, 'DRIVER_EXIT_MISMATCH'):
            exp.run()
        self.assertEqual(exp.receipt['diagnostic_output'], failed)
        saved = json.loads((self.output/'summary.json').read_text())
        self.assertEqual(saved['diagnostic_output'], failed)
        self.assertEqual(saved['failure_code'], 'DRIVER_EXIT_MISMATCH')
        self.assertEqual(saved['result'], 'failed')
        self.assertNotIn('output', saved)
        self.assertFalse(saved.get('workload_verified', False))
        self.assertTrue(saved['cleanup']['verified'])
        self.assert_no_secrets_saved()

    def test_malformed_failed_diagnostic_does_not_replace_driver_error(self):
        kube = RemoteKube(); kube.exit = 1
        failed = self.failed_before_precreated_check(); failed['raw_error'] = 'JWT_SENTINEL'
        kube.log = RC.RESULT_PREFIX+json.dumps(failed)
        exp = self.experiment(kube)
        with self.assertRaisesRegex(R.ExperimentError, 'DRIVER_EXIT_MISMATCH'):
            exp.run()
        self.assertNotIn('diagnostic_output', exp.receipt)
        self.assertIn('driver-log.json', exp.receipt['diagnostic_errors'])
        self.assertEqual(exp.receipt['failure_code'], 'DRIVER_EXIT_MISMATCH')
        self.assertTrue(exp.receipt['cleanup']['verified'])
        self.assert_no_secrets_saved()

    def test_cancellation_survives_best_effort_failed_payload_diagnostics(self):
        kube = RemoteKube(); cancel = KeyboardInterrupt()
        kube.log = RC.RESULT_PREFIX+json.dumps(self.failed_before_precreated_check())
        def interrupted():
            raise cancel
        kube.pods = interrupted
        exp = self.experiment(kube)
        with self.assertRaises(KeyboardInterrupt) as caught:
            exp.run()
        self.assertIs(caught.exception, cancel)
        self.assertEqual(exp.receipt['diagnostic_output']['result'], 'failed')
        self.assertEqual(exp.receipt['failure_code'], 'KeyboardInterrupt')
        self.assertTrue(exp.receipt['cleanup']['verified'])
        self.assertFalse(exp.receipt.get('workload_verified', False))

    def test_failed_contract_output_with_exit_zero_cannot_pass(self):
        kube = RemoteKube(); value = remote_result()
        value.update(result='failed', error_code='SPARK_STOP_FAILED', error_classes=['builtins.RuntimeError'])
        kube.log = RC.RESULT_PREFIX+json.dumps(value)
        exp = self.experiment(kube)
        with self.assertRaisesRegex(R.ExperimentError, 'REMOTE_JOB_NOT_PASSED'):
            exp.run()
        self.assertFalse(exp.receipt.get('workload_verified', False))
        self.assertTrue(exp.receipt['cleanup']['verified'])

    def test_remote_snapshot_parent_must_bind_precreated_initial_snapshot(self):
        kube = RemoteKube(); value = remote_result()
        value['table_identity']['parent_snapshot_id'] = None
        kube.log = RC.RESULT_PREFIX+json.dumps(value)
        exp = self.experiment(kube)
        with self.assertRaises(C.ContractError):
            exp.run()
        self.assertFalse(exp.receipt.get('workload_verified', False))
        self.assertTrue(exp.receipt['cleanup']['verified'])

    def test_remote_initial_snapshot_can_be_absent_null_but_not_bool(self):
        inputs = remote_inputs(); inputs['initial_snapshot_id'] = None
        exp = R.Experiment(TOKEN, 'success', self.output, RemoteKube(), profile='iceberg-write', profile_inputs=inputs)
        self.assertIsNone(exp.receipt['profile_inputs']['initial_snapshot_id'])
        inputs['initial_snapshot_id'] = False
        with self.assertRaises(C.ContractError):
            R.Experiment(TOKEN, 'success', self.output, RemoteKube(), profile='iceberg-write', profile_inputs=inputs)

    def test_remote_missing_redactor_stops_before_runtime_or_namespace_create(self):
        kube = RemoteKube(); kube.redactor = None
        with patch.object(kube, 'preflight') as preflight:
            with self.assertRaisesRegex(R.ExperimentError, 'REDACTOR_REQUIRED'):
                self.experiment(kube).run()
            preflight.assert_not_called()
        self.assertFalse(kube.created)

    def test_remote_image_and_executor_secret_items_must_match_exactly(self):
        for mutation in ('image', 'items'):
            with self.subTest(mutation=mutation):
                kube = RemoteKube()
                def change(pod):
                    if pod['metadata']['labels']['shirokuma.io/role'] != 'executor':
                        return
                    if mutation == 'image':
                        pod['status']['containerStatuses'][0]['imageID'] = R.P.DIGEST
                    else:
                        pod['spec']['volumes'][-1]['secret']['items'].append({'key': 'polaris-token', 'path': 'polaris-token'})
                kube.remote_mutate = change
                exp = self.experiment(kube)
                with self.assertRaises(R.ExperimentError):
                    exp.run()
                self.assertFalse(exp.receipt.get('workload_verified', False))
                self.assert_no_secrets_saved()

    def test_remote_secret_replacement_is_preserved_and_values_not_saved(self):
        kube = RemoteKube(); original_inventory = kube.inventory
        def inventory():
            kube.objects[('Secret', 'connection')]['metadata']['uid'] = str(uuid.uuid4())
            return original_inventory()
        kube.inventory = inventory
        exp = self.experiment(kube)
        with self.assertRaisesRegex(R.ExperimentError, 'CLEANUP_NOT_VERIFIED'):
            exp.run()
        self.assertEqual(exp.receipt['cleanup']['deferred'], 'RESOURCE_UID_CONFLICT')
        self.assertFalse(kube.deleted)
        self.assert_no_secrets_saved()

    def test_remote_recover_needs_no_provider_and_keeps_original_receipt(self):
        kube = RemoteKube(); exp = self.experiment(kube); exp.run()
        before = (self.output/'summary.json').read_bytes()
        kube.redactor = None
        with patch.object(R, 'STATE', self.root), patch.object(RC, 'render', side_effect=AssertionError('NO_RENDER')):
            receipt = R.recover(TOKEN, kube)
        self.assertTrue(receipt['cleanup']['verified'])
        self.assertEqual((self.output/'summary.json').read_bytes(), before)
        self.assert_no_secrets_saved()

    def test_saved_remote_secret_payload_or_foreign_identity_refused(self):
        exp = self.experiment(); exp.run()
        for mutation in ('payload', 'name', 'keys', 'input'):
            plan, receipt = deepcopy(exp.plan), deepcopy(exp.receipt)
            secret = next(x for x in plan if x['kind'] == 'Secret')
            if mutation == 'payload':
                secret['data'] = {'polaris-token': 'JWT_SENTINEL'}
            elif mutation == 'name':
                next(x for x in receipt['known'] if x['kind'] == 'Secret')['name'] = 'foreign'
            elif mutation == 'keys':
                secret['keys'] = ['root-secret']
            else:
                receipt['profile_inputs']['scope'] = 'PRINCIPAL_ROLE:spark_'+'f'*32
            receipt['plan_sha256'] = C.plan_sha256(plan)
            with self.subTest(mutation=mutation), self.assertRaises((R.ExperimentError, C.ContractError)):
                R.validate_saved(plan, receipt)

    def test_legacy_native_receipt_without_profile_remains_recoverable(self):
        kube = FakeKube(); clock = Clock()
        exp = R.Experiment(TOKEN, 'success', self.output, kube, clock=clock.now, sleep=clock.sleep)
        exp.run()
        exp.receipt.pop('profile'); exp.receipt.pop('profile_inputs'); exp.persist()
        with patch.object(R, 'STATE', self.root):
            receipt = R.recover(TOKEN, kube)
        self.assertTrue(receipt['cleanup']['verified'])

    def test_fresh_cri_reference_and_build_identity_are_both_required(self):
        from tools.local_experiment.spark_iceberg import build as B
        inputs = remote_inputs(); digest = RC.IMAGE.split('@')[1]
        proof = {'image': RC.IMAGE, 'image_id': digest, 'build_token': inputs['build_token'],
                 'architecture': 'arm64', 'os': 'linux'}
        with patch.object(B, 'read_verified_image', return_value=proof) as verify, \
             patch.object(R.P, 'command', return_value=json.dumps({'status': {'id': digest, 'repoDigests': [RC.IMAGE]}})) as command:
            self.assertTrue(R.remote_image(inputs)['cri_exact_reference_verified'])
            self.assertEqual(verify.call_args.args, (inputs['build_token'],))
            self.assertIn(RC.IMAGE, command.call_args.args[0])
            command.return_value = json.dumps({'status': {'id': digest, 'repoDigests': ['other@'+digest]}})
            with self.assertRaisesRegex(R.ExperimentError, 'REMOTE_CRI_IMAGE_CHANGED'):
                R.remote_image(inputs)
            command.return_value = json.dumps({'status': {'id': R.P.DIGEST, 'repoDigests': [RC.IMAGE]}})
            with self.assertRaisesRegex(R.ExperimentError, 'REMOTE_CRI_IMAGE_CHANGED'):
                R.remote_image(inputs)


class BoundaryTests(unittest.TestCase):
    def test_unknown_secret_projection_drops_values_and_arbitrary_annotations(self):
        raw = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 'foreign',
            'namespace': C.namespace(TOKEN), 'annotations': {'copied': 'SENTINEL'}},
            'type': 'Opaque', 'immutable': True, 'data': {'secret': 'SENTINEL'},
            'stringData': {'other': 'SENTINEL'}}
        safe = R.safe_resource([raw])[0]
        self.assertEqual(safe['keys'], ['other', 'secret'])
        self.assertNotIn('SENTINEL', json.dumps(safe))
        self.assertNotIn('data', safe); self.assertNotIn('stringData', safe)

    def test_native_inputs_and_unknown_profiles_rejected(self):
        for profile, inputs in [('native', {}), ('extension', None)]:
            with self.subTest(profile=profile), self.assertRaises(R.ExperimentError):
                R.Kube(TOKEN, profile=profile, profile_inputs=inputs)

    def test_native_secret_create_is_forbidden_before_any_http(self):
        with patch.object(R.P, 'command', side_effect=AssertionError('NO_HTTP')):
            with self.assertRaisesRegex(R.ExperimentError, 'SECRET_PROVIDER_REQUIRED'):
                R.Kube(TOKEN).create({'kind': 'Secret'})

    def test_remote_immutable_secret_injected_only_into_memory_request(self):
        stub = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 'connection',
            'namespace': C.namespace(TOKEN), 'labels': C.labels(TOKEN),
            'annotations': {'shirokuma.io/owner': C.OWNER}}, 'type': 'Opaque',
            'immutable': True, 'keys': R.SECRET_KEYS}
        payload = {'aws-credentials': 'AWS_SENTINEL', 'polaris-token': 'JWT_SENTINEL'}
        response = actual(stub); response.pop('keys'); response['data'] = payload
        with patch.object(R, 'validate_profile', return_value={}), \
             patch.object(R.Kube, 'request', return_value=json.dumps(response)) as request:
            kube = R.Kube(TOKEN, profile='iceberg-write', profile_inputs={}, secret_provider=lambda: payload,
                          redactor=lambda value: value.replace('AWS_SENTINEL', '[redacted]').replace('JWT_SENTINEL', '[redacted]'))
            safe = kube.create(stub)
        body = request.call_args.args[1]
        self.assertNotIn('keys', body)
        self.assertEqual(body['stringData'], payload)
        self.assertEqual(stub['keys'], R.SECRET_KEYS)
        self.assertNotIn('data', safe); self.assertNotIn('stringData', safe)
        self.assertNotIn('SENTINEL', json.dumps(safe))
        R.validate_secret(safe, TOKEN)

    def test_remote_bad_secret_keys_stop_before_http(self):
        stub = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 'connection',
            'namespace': C.namespace(TOKEN)}, 'type': 'Opaque', 'immutable': True, 'keys': R.SECRET_KEYS}
        with patch.object(R, 'validate_profile', return_value={}), \
             patch.object(R.Kube, 'request', side_effect=AssertionError('NO_HTTP')):
            kube = R.Kube(TOKEN, profile='iceberg-write', profile_inputs={}, secret_provider=lambda: {'root-secret': 'x'})
            with self.assertRaisesRegex(R.ExperimentError, 'SECRET_PAYLOAD_INVALID'):
                kube.create(stub)

    def test_remote_logs_require_redactor_and_redact_before_return(self):
        with patch.object(R, 'validate_profile', return_value={}), \
             patch.object(R.Kube, 'request', return_value='raw JWT_SENTINEL') as request:
            kube = R.Kube(TOKEN, profile='iceberg-write', profile_inputs={})
            with self.assertRaisesRegex(R.ExperimentError, 'REDACTOR_REQUIRED'):
                kube.logs()
            request.assert_not_called()
            kube.redactor = lambda value: value.replace('JWT_SENTINEL', '[redacted]')
            self.assertEqual(kube.logs(), 'raw [redacted]')

    def test_live_identity_validation_does_not_run_literal_log_redactor(self):
        pod = actual(C.render(TOKEN, 'success', api_endpoints=ENDPOINTS)[-1])
        destructive_mask = lambda value: value.replace('shirokuma', '[REDACTED]')
        with patch.object(R, 'validate_profile', return_value={}), \
             patch.object(R.Kube, 'request', return_value=json.dumps(pod)) as request:
            kube = R.Kube(TOKEN, profile='iceberg-write', profile_inputs={}, redactor=destructive_mask)
            self.assertEqual(kube.get('Pod', 'driver'), pod)
            self.assertEqual(kube.create(pod), pod)
            request.return_value = json.dumps({'items': [pod]})
            self.assertEqual(kube.pods(), [pod])
            self.assertEqual(kube.inventory(), [pod])

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



class BenchmarkRunnerTests(unittest.TestCase):
    setUp = RunnerTests.setUp
    tearDown = RunnerTests.tearDown
    assert_no_secrets_saved = RemoteRunnerTests.assert_no_secrets_saved

    def fixture(self):
        from test_local_spark_benchmark_contract import inputs, output
        return inputs(), output()

    def experiment(self, kube=None):
        inputs, output = self.fixture(); clock = Clock()
        kube = kube or RemoteKube(profile='iceberg-benchmark', inputs=inputs, output=output)
        return R.Experiment(TOKEN, 'success', self.output, kube, profile='iceberg-benchmark',
                            profile_inputs=inputs, clock=clock.now, sleep=clock.sleep)

    def test_benchmark_lifecycle_accepts_measurements_without_write_output_fields(self):
        exp = self.experiment(); receipt = exp.run()
        self.assertEqual(receipt['result'], 'passed')
        self.assertEqual(receipt['profile'], 'iceberg-benchmark')
        self.assertEqual(len(receipt['output']['measurements']), 8)
        self.assertNotIn('row_count', receipt['output'])
        self.assertNotIn('table_identity', receipt['output'])
        self.assertEqual(receipt['output']['sealed_before'], receipt['output']['sealed_after'])
        self.assertEqual(len(exp.plan), 16)
        self.assertEqual(len(receipt['known']), 17)
        self.assertEqual(len(exp.kube.deleted), 17)
        self.assertFalse(exp.kube.objects)
        for name in ('spark_benchmark/contract.py', 'spark_benchmark/workload.py',
                     'spark_iceberg/remote_contract.py', 'spark_iceberg/build.py'):
            self.assertIn('tools/local_experiment/'+name, receipt['source_hashes'])
        R.validate_saved(exp.plan, receipt)
        self.assert_no_secrets_saved()

    def test_benchmark_mutated_seal_rejected_before_create(self):
        inputs, output = self.fixture()
        inputs['sealed']['identity']['snapshot_id'] += 1
        kube = RemoteKube(profile='iceberg-benchmark', inputs=inputs, output=output)
        with self.assertRaisesRegex(C.ContractError, 'BENCH_IDENTITY_INVALID'):
            R.Experiment(TOKEN, 'success', self.output, kube,
                profile='iceberg-benchmark', profile_inputs=inputs)
        self.assertFalse(kube.created)
        self.assertFalse(list(self.output.iterdir()))

    def test_benchmark_result_seal_drift_fails_and_owned_resources_are_cleaned(self):
        inputs, output = self.fixture()
        output['sealed_after']['identity']['snapshot_id'] += 1
        kube = RemoteKube(profile='iceberg-benchmark', inputs=inputs, output=output)
        exp = self.experiment(kube)
        with self.assertRaisesRegex(C.ContractError, 'BENCH_OUTPUT_INVALID'):
            exp.run()
        self.assertEqual(exp.receipt['result'], 'failed')
        self.assertEqual(exp.receipt['failure_code'], 'BENCH_OUTPUT_INVALID')
        self.assertNotIn('workload_verified', exp.receipt)
        self.assertTrue(exp.receipt['cleanup']['verified'])
        self.assertFalse(kube.objects)
        self.assert_no_secrets_saved()

    def test_benchmark_recovery_needs_no_secret_callback_or_current_render(self):
        exp = self.experiment(); exp.run()
        original = (self.output/'summary.json').read_bytes()
        exp.kube.redactor = None
        module = R.remote_contract('iceberg-benchmark')
        with patch.object(R, 'STATE', self.root), patch.object(module, 'render', side_effect=AssertionError('NO_RENDER')):
            recovered = R.recover(TOKEN, exp.kube)
        self.assertTrue(recovered['cleanup']['verified'])
        self.assertEqual((self.output/'summary.json').read_bytes(), original)
        self.assert_no_secrets_saved()

    def test_benchmark_receipt_cannot_change_profile_to_write(self):
        exp = self.experiment(); exp.run()
        receipt = deepcopy(exp.receipt); receipt['profile'] = 'iceberg-write'
        with self.assertRaises(C.ContractError):
            R.validate_saved(exp.plan, receipt)


if __name__ == '__main__':
    unittest.main()
