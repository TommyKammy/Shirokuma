"""Native/Spark contrast, fixed images and unchanged credential/pod boundaries."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from tools.local_experiment.spark_comet import remote_contract as C, build as CB
from tools.local_experiment.spark_iceberg import build as IB
from tools.local_experiment.spark import run as NATIVE
import test_local_spark_benchmark_contract as BASE
from test_spark_comet_contract import plan as native_plan

TOKEN = BASE.TOKEN


def inputs(variant='stock'):
    value = BASE.inputs(); value.update(variant=variant, image=C.IMAGES[variant])
    if variant == 'comet': value['build_token'] = 'c' * 32
    return value


def output(variant='stock'):
    value = BASE.output()
    classes = [C.G.NATIVE_AGGREGATE, C.G.NATIVE_SCAN] if variant == 'comet' else [
        'org.apache.spark.sql.execution.aggregate.HashAggregateExec',
        'org.apache.spark.sql.execution.datasources.v2.BatchScanExec']
    value.update(scope=C.SCOPE, variant=variant, configured_image=C.IMAGES[variant],
        comet_version='1.0.0' if variant == 'comet' else None, scala_version='2.13.17',
        java_version='17', os_arch='aarch64', native_acceleration_verified=variant == 'comet',
        plans={case: [native_plan(classes) for _ in C.W.SAMPLE_INDICES] for case in C.W.CASES},
        checks={key: True for key in C.CHECKS})
    return value


class RemoteContractTests(unittest.TestCase):
    def validate(self, value, variant='stock'):
        return C.validate_output(value, TOKEN, inputs(variant), executor_pod_name=C.N.executor_name(TOKEN))

    def test_both_variants_bind_identical_oracle_and_distinct_native_claim(self):
        for variant in C.IMAGES:
            value = output(variant)
            self.assertEqual(self.validate(value, variant), value)
            self.assertEqual(len(value['measurements']), 8)
            self.assertEqual(value['native_acceleration_verified'], variant == 'comet')
            summary = C.safe_summary(value, TOKEN, inputs(variant), executor_pod_name=C.N.executor_name(TOKEN))
            self.assertFalse(set(summary) & {'plans', 'sealed_before', 'measurements'})

    def test_unknown_variant_cross_image_token_or_sql_is_refused(self):
        mutations = [lambda v: v.update(variant='other'), lambda v: v.update(variant=True),
            lambda v: v.update(image=C.IMAGES['comet']), lambda v: v.update(sql='SELECT 1'),
            lambda v: v.update(scope='PRINCIPAL_ROLE:spark_' + 'd' * 32),
            lambda v: v.update(initial_snapshot_id=True)]
        for change in mutations:
            value = inputs(); change(value)
            with self.assertRaises(ValueError): C._inputs(value, TOKEN)

    def test_plan_identity_or_oracle_mismatch_cannot_pass(self):
        changes = [lambda v: v.update(variant='stock'), lambda v: v.update(configured_image=C.IMAGES['stock']),
            lambda v: v['plans']['q6'].pop(), lambda v: v.update(native_acceleration_verified=False),
            lambda v: v['plans']['q1_sums'][0].update(text='changed'),
            lambda v: v['measurements'][0].update(sql_sha256='0' * 64),
            lambda v: v['sealed_after']['identity'].update(snapshot_id=3)]
        for change in changes:
            value = output('comet'); change(value)
            with self.assertRaises(ValueError): self.validate(value, 'comet')

    def test_jvm_comet_name_is_not_native_scan_and_aggregate(self):
        value = output('comet')
        value['plans']['q6'][0] = native_plan(['org.apache.spark.sql.comet.CometBatchScanExec'])
        with self.assertRaisesRegex(ValueError, 'COMET_NATIVE_PLAN_MISSING'): self.validate(value, 'comet')
        value = output(); value['plans']['q6'][0] = native_plan([C.G.NATIVE_SCAN])
        with self.assertRaisesRegex(ValueError, 'STOCK_COMET_PLAN_PRESENT'): self.validate(value)

    def test_failed_fallback_retains_observed_plan_without_native_claim(self):
        value = output('comet')
        value.update(result='failed', error_code='COMET_NATIVE_PLAN_MISSING', error_classes=['SafeFailure'],
                     native_acceleration_verified=False, measurements=[])
        value['checks'].update(plan_observations=False, measurements=False)
        value['plans'] = {'q1_sums': [native_plan(['org.apache.spark.sql.comet.CometBatchScanExec'])], 'q6': []}
        self.assertEqual(self.validate(value, 'comet'), value)
        incorrect = deepcopy(value); incorrect['error_code'] = 'INTERNAL_ERROR'
        with self.assertRaises(ValueError): self.validate(incorrect, 'comet')
        value['result'] = 'passed'
        with self.assertRaises(ValueError): self.validate(value, 'comet')

    def test_render_has_two_gib_pods_file_only_credentials_and_fixed_jobs(self):
        for variant in C.IMAGES:
            value = inputs(variant)
            plan = C.render(TOKEN, inputs=value)
            config = next(row for row in plan if row['kind'] == 'ConfigMap')['data']
            compile(config['job.py'], '<remote-comet>', 'exec')
            spec = json.loads(config['executor-template.json'])['spec']
            self.assertEqual(spec, C.expected_pod_spec(TOKEN, 'executor', value))
            self.assertEqual(spec['containers'][0]['resources']['limits']['memory'], '2Gi')
            props = dict(line.split(' ', 1) for line in config['spark-defaults.conf'].splitlines())
            self.assertEqual(props['spark.executor.memory'], '1g')
            self.assertEqual(props['spark.executor.memoryOverhead'], '768m')
            self.assertEqual(props['spark.driver.memoryOverhead'], '768m')
            self.assertEqual(props['spark.memory.offHeap.size'], '256m')
            self.assertEqual('spark.plugins' in props, variant == 'comet')
            self.assertFalse(any('access-key-id' in key or 'secret-access-key' in key for key in props))
            self.assertEqual([row['name'] for row in spec['containers'][0]['env'] if row['name'].startswith('AWS_')],
                             ['AWS_SHARED_CREDENTIALS_FILE', 'AWS_EC2_METADATA_DISABLED', 'AWS_REGION'])
            secret = next(row for row in plan if row['kind'] == 'Secret')
            self.assertEqual(secret['keys'], C.R.SECRET_KEYS); self.assertNotIn('data', secret)
            for verb in ('INSERT INTO', 'DROP TABLE', 'CREATE TABLE'): self.assertNotIn(verb, config['job.py'])
            C.validate_saved_plan(plan, TOKEN, value)
            config['spark-defaults.conf'] += 'spark.plugins other\n'
            with self.assertRaises(ValueError): C.validate_saved_plan(plan, TOKEN, value)

    def test_fixed_driver_observer_enforces_native_and_preserves_timing_scope(self):
        for variant in C.IMAGES:
            script = C.job_script(TOKEN, inputs(variant))
            namespace = {'__name__': 'pure_test'}; exec(compile(script, '<remote-comet>', 'exec'), namespace)
            namespace['check_native'](output(variant)['plans']['q6'][0])
            wrong = output('stock' if variant == 'comet' else 'comet')['plans']['q6'][0]
            with self.assertRaises(namespace['SafeFailure']): namespace['check_native'](wrong)
            self.assertLess(script.index('elapsed = time.perf_counter_ns() - started'), script.index('observation = observe_plan(frame, spark)'))

    def test_saved_json_roundtrip_regenerates_identical_plan_and_job_bytes(self):
        for variant in C.IMAGES:
            value = inputs(variant)
            plan = C.render(TOKEN, inputs=value)
            reread_inputs = json.loads(json.dumps(value, sort_keys=True))
            reread_plan = json.loads(json.dumps(plan, sort_keys=True))
            self.assertEqual(C.job_script(TOKEN, value), C.job_script(TOKEN, reread_inputs))
            self.assertEqual(C.validate_saved_plan(reread_plan, TOKEN, reread_inputs), reread_plan)

    def test_duplicate_json_and_crossvariant_parser(self):
        raw = json.dumps(output('comet'))
        self.assertEqual(C.parse_output(C.RESULT_PREFIX + raw, TOKEN, inputs('comet'),
            executor_pod_name=C.N.executor_name(TOKEN))['variant'], 'comet')
        for text in (raw.replace('{', '{"schema_version":1,', 1), raw + '\n' + C.RESULT_PREFIX + raw):
            with self.assertRaises(ValueError): C.parse_output(C.RESULT_PREFIX + text, TOKEN, inputs('comet'), executor_pod_name=C.N.executor_name(TOKEN))

    def test_fixed_host_dispatch_verifies_correct_builder_and_cri(self):
        for variant in C.IMAGES:
            value = inputs(variant)
            proof = {'build_token': value['build_token'], 'image': value['image'],
                'image_id': value['image'].split('@')[1], 'architecture': 'arm64', 'os': 'linux'}
            with patch.object(CB, 'read_verified_image', return_value=proof) as comet, \
                 patch.object(IB, 'read_verified_image', return_value=proof) as stock, \
                 patch.object(NATIVE.P, 'command', return_value=json.dumps({'status': {'id': proof['image_id'], 'repoDigests': [proof['image']]}})):
                result = NATIVE.remote_image(value, C.PROFILE)
                self.assertTrue(result['cri_exact_reference_verified'])
                self.assertEqual(comet.call_count, variant == 'comet'); self.assertEqual(stock.call_count, variant == 'stock')
            bad = dict(proof, image=C.IMAGES['stock' if variant == 'comet' else 'comet'])
            with patch.object(CB if variant == 'comet' else IB, 'read_verified_image', return_value=bad), \
                 patch.object(NATIVE.P, 'command') as command:
                with self.assertRaisesRegex(Exception, 'REMOTE_IMAGE_CHANGED'): NATIVE.remote_image(value, C.PROFILE)
                command.assert_not_called()

    def test_new_profile_log_bound_and_sources_do_not_change_old_contract(self):
        for profile, value, limit in [(C.PROFILE, inputs('comet'), C.MAX_LOG_BYTES),
                                     ('iceberg-benchmark', BASE.inputs(), C.N.MAX_LOG_BYTES)]:
            kube = NATIVE.Kube(TOKEN, profile=profile, profile_inputs=value, redactor=lambda text: text)
            with patch.object(kube, 'request', return_value='private log') as request:
                self.assertEqual(kube.logs(), 'private log')
                self.assertIn('--limit-bytes=' + str(limit), request.call_args.args[0])
        sources = NATIVE.source_hashes(C.PROFILE)
        self.assertIn('tools/local_experiment/spark_comet/remote_contract.py', sources)
        self.assertIn('tools/local_experiment/spark_benchmark/workload.py', sources)
        with self.assertRaises(ValueError): C.B.validate_inputs(inputs('comet'))


if __name__ == '__main__': unittest.main()
