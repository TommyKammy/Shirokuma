"""Pure native-plan acceptance; no Spark/JVM/container invocation."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from tools.local_experiment.spark_comet import contract as C

TOKEN = '1' * 32


def plan(classes):
    nodes = [{'class': item, 'name': item.rsplit('.', 1)[-1], 'parent': None if i == 0 else i - 1}
             for i, item in enumerate(classes)]
    text = '\n'.join(row['name'] for row in nodes)
    return {'nodes': nodes, 'fingerprint_sha256': C.digest(nodes), 'text': text,
            'text_sha256': C.digest(text.encode()), 'explain': text + '\nNo fallback in this fixture.'}


def passed():
    return {'schema_version': 1, 'run_token': TOKEN, 'scope': C.SCOPE, 'result': 'passed',
        'spark_version': '4.1.3', 'iceberg_version': '1.11.0', 'comet_version': '1.0.0',
        'scala_version': '2.13.17', 'java_version': '17', 'os_arch': 'aarch64', 'master': 'local[1]',
        'checks': {key: True for key in C.CHECKS}, 'row_count': 32,
        'dataframe_rows': C.oracle_rows(), 'sql_rows': C.oracle_rows(), 'groups': C.oracle_groups(),
        'table_identity': {'uuid': '11111111-1111-1111-1111-111111111111', 'snapshot_id': 123,
            'metadata_location': C.I.WAREHOUSE + '/smoke/t_' + TOKEN + '/metadata/v2.metadata.json'},
        'cleanup': {'attempted': True, 'uuid_verified': True, 'table_absent': True}, 'error_code': None,
        'plans': {'rows': plan([C.NATIVE_SCAN]), 'groups': plan([C.NATIVE_AGGREGATE, C.NATIVE_SCAN])}}


class ContractTests(unittest.TestCase):
    def test_exact_32_rows_native_plan_and_summary(self):
        value = passed()
        self.assertEqual(C.parse_output('noise\n' + C.RESULT_PREFIX + json.dumps(value), TOKEN), value)
        summary = C.safe_summary(value)
        self.assertEqual(summary['row_count'], 32)
        self.assertFalse(set(summary) & {'dataframe_rows', 'table_identity', 'plans'})

    def test_jvm_comet_nodes_do_not_prove_native_execution(self):
        for field in ('rows', 'groups'):
            value = passed()
            value['plans'][field] = plan(['org.apache.spark.sql.comet.CometBatchScanExec',
                                         'org.apache.spark.sql.comet.CometColumnarToRowExec'])
            with self.assertRaisesRegex(C.ContractError, 'COMET_NATIVE_PLAN_MISSING'):
                C.validate_output(value, TOKEN)

    def test_plan_oracle_or_identity_mutation_is_rejected(self):
        mutations = [lambda v: v['plans']['rows'].update(text='another plan'),
            lambda v: v['plans']['groups']['nodes'][0].update(parent=0),
            lambda v: v['dataframe_rows'][0].__setitem__(3, 999),
            lambda v: v.update(run_token='2' * 32), lambda v: v.update(os_arch='amd64'),
            lambda v: v.update(java_version='21'), lambda v: v['checks'].update(native_plan=False)]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                value = passed(); mutation(value)
                with self.assertRaises(C.ContractError): C.validate_output(value, TOKEN)

    def test_duplicate_and_nonfinite_json_are_rejected(self):
        for raw in ('{"schema_version":1,"schema_version":1}', '{"value":NaN}'):
            with self.assertRaises(C.ContractError): C.parse_output(C.RESULT_PREFIX + raw, TOKEN)
        with self.assertRaises(C.ContractError):
            C.parse_output((C.RESULT_PREFIX + json.dumps(passed()) + '\n') * 2, TOKEN)

    def test_failed_native_receipt_stays_failed(self):
        value = passed(); value.update(result='failed', error_code='COMET_NATIVE_PLAN_MISSING', plans={})
        value['checks']['native_plan'] = False
        self.assertEqual(C.validate_output(value, TOKEN), value)

    def test_registered_job_compiles_and_fixed_arguments(self):
        script = C.job_script()
        compile(script, '<fixed-comet-job>', 'exec')
        namespace = {'__name__': 'pure_test'}
        exec(compile(script, '<fixed-comet-job>', 'exec'), namespace)
        self.assertEqual(namespace['expected_rows'](), C.oracle_rows())
        value = passed(); namespace['require_native'](value)
        self.assertTrue(value['checks']['native_plan'])
        args = C.spark_arguments(TOKEN)
        self.assertEqual(args[-2:], ['/opt/shirokuma/check.py', TOKEN])
        self.assertIn('spark.memory.offHeap.size=256m', args)
        self.assertIn('spark.comet.explain.fallback.enabled=true', args)
        self.assertNotIn('--packages', args)
        with self.assertRaises(C.ContractError): C.spark_arguments('../untrusted')

    def test_changed_base_job_cannot_silently_omit_observations(self):
        with patch.object(C.I, '_JOB', C.I._JOB.replace("        output['checks']['fresh_dataframe'] = True", '        pass')):
            with self.assertRaisesRegex(C.ContractError, 'COMET_BASE_JOB_CHANGED'): C.job_script()


if __name__ == '__main__':
    unittest.main()
