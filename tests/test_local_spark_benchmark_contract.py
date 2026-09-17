from copy import deepcopy
import hashlib
import json
import unittest

from tools.local_experiment.spark import contract as N
from tools.local_experiment.spark_iceberg import remote_contract as R
from tools.local_experiment.spark_benchmark import contract as C, workload as W

TOKEN = 'a' * 32
SNAPSHOT = 1477241556327226373


def inputs():
    oracle = W.DatasetOracle()
    oracle.add([1, 1, 'N', 'O', '10.00', '100.00', '0.06', '0.02', '1994-06-01'])
    identity = {'table_uuid': '242d28cd-9200-484e-9569-bdfe437d571d', 'table_location': R.table_location(TOKEN),
                'marker': C.marker(TOKEN), 'snapshot_id': SNAPSHOT, 'parent_snapshot_id': None,
                'metadata_location': R.table_location(TOKEN) + '/metadata/00000-test.metadata.json', 'operation': 'append'}
    return {'build_token': 'b' * 32, 'image': R.IMAGE, 'table_uuid': identity['table_uuid'],
            'table_location': identity['table_location'], 'initial_snapshot_id': SNAPSHOT,
            'polaris_uri': R.POLARIS_URI, 'polaris_realm': 'POLARIS', 'warehouse': 'shirokuma_l1',
            'scope': 'PRINCIPAL_ROLE:spark_' + TOKEN, 's3_endpoint': R.S3_ENDPOINT, 's3_region': 'us-east-1',
            'data_egress': deepcopy(R.DATA_EGRESS), 'sealed': {'identity': identity,
                'data_files': [{'path': R.table_location(TOKEN) + '/data/one.parquet', 'record_count': 1, 'file_size_in_bytes': 1024}],
                'dataset': oracle.finish()}}


def samples(engine='spark'):
    value = inputs()
    return [{'engine': engine, 'case_id': case_id, 'sample_index': index,
             'sql_sha256': hashlib.sha256(W.query_sql(engine, case_id, TOKEN, SNAPSHOT).encode()).hexdigest(),
             'elapsed_ns': 1_000_000 + index, 'result': deepcopy(value['sealed']['dataset']['expected'][case_id]),
             'query_id': '20260917_130000_00001_abcd1' if engine == 'trino' else None,
             'stats': {} if engine == 'trino' else None}
            for case_id in W.CASES for index in W.SAMPLE_INDICES]


def output():
    value = inputs()
    seal = {k: value['sealed'][k] for k in ('identity', 'data_files')}
    return {'schema_version': 1, 'run_token': TOKEN, 'namespace': N.namespace(TOKEN), 'scope': C.SCOPE,
            'mode': 'success', 'configured_image': R.IMAGE, 'workload_id': W.WORKLOAD_ID, 'result': 'passed',
            'spark_version': '4.1.3', 'iceberg_version': '1.11.0', 'master': 'k8s://https://kubernetes.default.svc:443',
            'checks': {k: True for k in C.CHECKS}, 'measurements': samples(), 'startup_ns': 10_000_000,
            'sealed_before': deepcopy(seal), 'sealed_after': deepcopy(seal), 'error_code': None, 'error_classes': [],
            'partitions': [{'partition_id': i, 'stage_id': 20, 'task_attempt_id': 25+i, 'attempt_number': 0,
                            'executor_id': '1', 'hostname': N.executor_name(TOKEN)} for i in range(2)]}


class BenchmarkContractTests(unittest.TestCase):
    def validate(self, value):
        return C.validate_output(value, TOKEN, inputs(), executor_pod_name=N.executor_name(TOKEN))

    def test_valid_exact_int64_snapshot_and_output(self):
        self.assertEqual(C.validate_inputs(inputs())['initial_snapshot_id'], SNAPSHOT)
        self.assertEqual(self.validate(output())['sealed_after']['identity']['snapshot_id'], SNAPSHOT)

    def test_rejects_changed_source_or_metadata_identity(self):
        for key, bad in [('snapshot_id', SNAPSHOT+1), ('table_uuid', '342d28cd-9200-484e-9569-bdfe437d571d'),
                         ('marker', 'other'), ('metadata_location', 's3://outside/metadata/x.metadata.json')]:
            value = inputs(); value['sealed']['identity'][key] = bad
            with self.subTest(key=key), self.assertRaises(ValueError): C.validate_inputs(value)

    def test_rejects_extra_input_and_model_sql(self):
        value = inputs(); value['sql'] = 'SELECT 1'
        with self.assertRaises(ValueError): C.validate_inputs(value)

    def test_rejects_numeric_boolean_and_float_snapshot(self):
        for bad in (True, float(SNAPSHOT)):
            value = inputs(); value['initial_snapshot_id'] = bad
            with self.assertRaises(ValueError): C.validate_inputs(value)

    def test_file_manifest_bound_and_duplicate(self):
        for mutation in ('duplicate', 'records', 'path', 'size'):
            value = inputs(); files = value['sealed']['data_files']
            if mutation == 'duplicate': files.append(deepcopy(files[0]))
            if mutation == 'records': files[0]['record_count'] = 2
            if mutation == 'path': files[0]['path'] = 's3://other/data/one.parquet'
            if mutation == 'size': files[0]['file_size_in_bytes'] = 256*1024*1024+1
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): C.validate_inputs(value)

    def test_oracle_hash_must_bind_rows(self):
        value = inputs(); value['sealed']['dataset']['expected']['q6']['rows'][0][0] = '9.000000'
        with self.assertRaises(ValueError): C.validate_inputs(value)

    def test_measurements_require_all_cases_samples_and_oracle(self):
        for mutation in ('missing', 'order', 'oracle', 'sql', 'zero', 'nan'):
            value = samples()
            if mutation == 'missing': value.pop()
            if mutation == 'order': value.reverse()
            if mutation == 'oracle': value[0]['result']['rows'][0][-1] = 2
            if mutation == 'sql': value[0]['sql_sha256'] = '0'*64
            if mutation == 'zero': value[0]['elapsed_ns'] = 0
            if mutation == 'nan': value[0]['elapsed_ns'] = float('nan')
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                C.validate_measurements(value, 'spark', TOKEN, inputs())

    def test_trino_and_spark_stats_stay_distinct(self):
        C.validate_measurements(samples('trino'), 'trino', TOKEN, inputs())
        value = samples(); value[0]['query_id'] = '20260917_130000_00001_abcd1'
        with self.assertRaises(ValueError): C.validate_measurements(value, 'spark', TOKEN, inputs())

    def test_failed_partial_samples_are_not_success(self):
        value = output(); value.update(result='failed', error_code='MEASUREMENT_FAILED', error_classes=['builtins.ValueError'])
        value['measurements'] = value['measurements'][:2]; value['checks']['measurements'] = False
        self.assertEqual(self.validate(value)['result'], 'failed')
        value['result'] = 'passed'
        with self.assertRaises(ValueError): self.validate(value)

    def test_changed_final_seal_is_rejected(self):
        value = output(); value['sealed_after']['identity']['snapshot_id'] += 1
        with self.assertRaises(ValueError): self.validate(value)

    def test_executor_task_proof_and_checks_are_required(self):
        for mutation in ('executor', 'startup', 'checks'):
            value = output()
            if mutation == 'executor': value['partitions'][0]['executor_id'] = 'driver'
            if mutation == 'startup': value['startup_ns'] = None
            if mutation == 'checks': value['checks']['sealed_after'] = False
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): self.validate(value)

    def test_safe_error_class_rejects_messages(self):
        value = output(); value.update(result='failed', error_code='MEASUREMENT_FAILED', error_classes=['secret endpoint failed'])
        with self.assertRaises(ValueError): self.validate(value)

    def test_output_parser_rejects_duplicate_fields_and_lines(self):
        raw = json.dumps(output())
        for logs in (C.RESULT_PREFIX+raw+'\n'+C.RESULT_PREFIX+raw, C.RESULT_PREFIX+raw.replace('{', '{"schema_version":1,', 1)):
            with self.assertRaises(ValueError): C.parse_output(logs,TOKEN,inputs(),executor_pod_name=N.executor_name(TOKEN))

    def test_render_is_fixed_read_workload_and_preserves_secret_stub(self):
        value = inputs(); plan = C.render(TOKEN, inputs=value)
        job = next(x['data']['job.py'] for x in plan if x['kind']=='ConfigMap')
        compile(job, '<benchmark-job>', 'exec')
        self.assertNotIn('INSERT INTO', job)
        self.assertNotIn('CREATE TABLE', job)
        self.assertNotIn('DROP TABLE', job)
        secret = next(x for x in plan if x['kind']=='Secret')
        self.assertEqual(secret['keys'], R.SECRET_KEYS)
        self.assertNotIn('data',secret); self.assertNotIn('stringData',secret)
        C.validate_saved_plan(plan,TOKEN,value)

    def test_job_helpers_use_java_iterator_and_restore_active_session(self):
        job = C.job_script(TOKEN, inputs())
        namespace = {'__name__': 'test_job'}; exec(compile(job,'<job>','exec'),namespace)
        class Items:
            def __iter__(self): raise AssertionError('Python iteration prohibited')
            def iterator(self): return self
            def hasNext(self): return not hasattr(self,'used')
            def next(self): self.used=True; return 7
        self.assertEqual(list(namespace['java_items'](Items(),1,'BAD')),[7])
        self.assertIn('sessions.setActiveSession(previous.get())',job)
        self.assertIn('spark.conf.unset(key)',job)


if __name__ == '__main__':
    unittest.main()
