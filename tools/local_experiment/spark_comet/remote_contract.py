"""Two fixed Spark variants over one sealed Iceberg snapshot, without caller SQL."""
from __future__ import annotations
from copy import deepcopy
import json
import re

from tools.local_experiment.spark import contract as N
from tools.local_experiment.spark_benchmark import contract as B, workload as W
from tools.local_experiment.spark_iceberg import remote_contract as R
from tools.local_experiment.spark_comet import contract as G

PROFILE = 'iceberg-comet-benchmark'
SCOPE = 'native-spark-comet-fixed-snapshot-benchmark'
RESULT_PREFIX = 'SHIROKUMA_SPARK_COMET_BENCHMARK_RESULT='
IMAGES = {'stock': R.IMAGE,
    'comet': 'shirokuma.local/spark-comet-classpath@sha256:95b102c3be6e68b2d4ce507a7d8b5c1c41765f19d26d486d031d6c7cfa5f0da2'}
INPUT_KEYS = R.INPUT_KEYS | {'sealed', 'variant'}
CHECKS = (*B.CHECKS, 'variant_runtime', 'plan_observations')
EXTRA_KEYS = {'variant', 'comet_version', 'scala_version', 'java_version', 'os_arch',
              'plans', 'native_acceleration_verified'}
ERRORS = B.ERRORS | {'COMET_RUNTIME_MISMATCH', 'COMET_NATIVE_PLAN_MISSING', 'COMET_PLAN_INVALID',
                     'STOCK_COMET_PLAN_PRESENT'}
MAX_LOG_BYTES = 1024 * 1024
require, ContractError = N.require, N.ContractError


def legacy_inputs(value):
    """Only common fields use the old validator; the real image is bound below."""
    return {key: (R.IMAGE if key == 'image' else deepcopy(value[key]))
            for key in R.INPUT_KEYS | {'sealed'}}


def validate_inputs(value):
    require(type(value) is dict and set(value) == INPUT_KEYS, 'COMET_BENCH_INPUT_KEYS_INVALID')
    variant = value['variant']
    require(type(variant) is str and variant in IMAGES and value['image'] == IMAGES[variant],
            'COMET_BENCH_IMAGE_MISMATCH')
    B.validate_inputs(legacy_inputs(value))
    return deepcopy(value)


def _inputs(value, token):
    value = validate_inputs(value)
    B._inputs(legacy_inputs(value), token)
    return value


def validate_measurements(value, engine, token, inputs, *, partial=False):
    return B.validate_measurements(value, engine, token, legacy_inputs(_inputs(inputs, token)), partial=partial)


def expected_pod_spec(token, role, inputs):
    inputs = _inputs(inputs, token)
    spec = B.expected_pod_spec(token, role, legacy_inputs(inputs))
    spec['containers'][0]['image'] = inputs['image']
    return spec


def spark_properties(token, inputs):
    inputs = _inputs(inputs, token)
    props = R.spark_properties(token, B.base_inputs(legacy_inputs(inputs)))
    props.update({'spark.kubernetes.container.image': inputs['image'],
        'spark.executor.memoryOverhead': '768m', 'spark.driver.memoryOverhead': '768m',
        'spark.memory.offHeap.enabled': 'true',
        'spark.memory.offHeap.size': '256m'})
    if inputs['variant'] == 'comet':
        args = G.spark_arguments(token)
        for index, item in enumerate(args[:-1]):
            if item == '--conf':
                key, value = args[index + 1].split('=', 1); props[key] = value
    return props


def render(token, mode='success', *, api_endpoints=None, inputs):
    inputs = _inputs(inputs, token)
    plan = B.render(token, mode, api_endpoints=api_endpoints, inputs=legacy_inputs(inputs))
    for obj in plan:
        if obj['kind'] == 'ConfigMap':
            template = json.loads(obj['data']['executor-template.json'])
            template['spec'] = expected_pod_spec(token, 'executor', inputs)
            obj['data'].update({'job.py': job_script(token, inputs),
                'executor-template.json': json.dumps(template, sort_keys=True),
                'spark-defaults.conf': ''.join(k + ' ' + v + '\n' for k, v in sorted(spark_properties(token, inputs).items()))})
        elif obj['kind'] == 'Pod':
            obj['spec']['containers'][0]['image'] = inputs['image']
    return plan


def validate_saved_plan(plan, token, inputs, api_endpoints=None):
    require(R._same(plan, render(token, inputs=inputs, api_endpoints=api_endpoints)), 'COMET_BENCH_PLAN_CHANGED')
    return deepcopy(plan)


def validate_output(value, token, inputs, *, executor_pod_name):
    inputs = _inputs(inputs, token)
    require(type(value) is dict and EXTRA_KEYS <= set(value) and value.get('scope') == SCOPE
            and value['variant'] == inputs['variant'] and value.get('configured_image') == inputs['image'],
            'COMET_BENCH_OUTPUT_IDENTITY_INVALID')
    checks = value.get('checks')
    require(type(checks) is dict and set(checks) == set(CHECKS) and all(type(v) is bool for v in checks.values()),
            'COMET_BENCH_CHECKS_INVALID')
    old = {key: deepcopy(item) for key, item in value.items() if key not in EXTRA_KEYS}
    old.update(scope=B.SCOPE, configured_image=R.IMAGE, checks={key: checks[key] for key in B.CHECKS})
    if old.get('error_code') in ERRORS - B.ERRORS:
        old['error_code'] = 'INTERNAL_ERROR'
    B.validate_output(old, token, legacy_inputs(inputs), executor_pod_name=executor_pod_name)
    require(value['error_code'] is None or value['error_code'] in ERRORS, 'COMET_BENCH_ERROR_INVALID')
    require(type(value['native_acceleration_verified']) is bool, 'COMET_BENCH_NATIVE_FLAG_INVALID')
    for key in ('comet_version', 'scala_version', 'java_version', 'os_arch'):
        require(value[key] is None or type(value[key]) is str and 0 < len(value[key]) <= 64, 'COMET_RUNTIME_MISMATCH')
    if checks['variant_runtime'] or value['result'] == 'passed':
        require(value['java_version'] == '17' and value['os_arch'] == 'aarch64'
                and re.fullmatch(r'2\.13\.[0-9]+', value['scala_version'] or '')
                and value['comet_version'] == (G.VERSION if inputs['variant'] == 'comet' else None),
                'COMET_RUNTIME_MISMATCH')
    plans = value['plans']
    require(type(plans) is dict and set(plans) == set(W.CASES), 'COMET_PLAN_INVALID')
    for case_id in W.CASES:
        entries = plans[case_id]
        require(type(entries) is list and 0 <= len(entries) <= len(W.SAMPLE_INDICES), 'COMET_PLAN_INVALID')
        for item in entries:
            G.validate_plan(item)
            classes = {node['class'] for node in item['nodes']}
            code = 'STOCK_COMET_PLAN_PRESENT' if inputs['variant'] == 'stock' else 'COMET_NATIVE_PLAN_MISSING'
            matches = (not any(name.startswith('org.apache.spark.sql.comet.') for name in classes)
                       if inputs['variant'] == 'stock' else {G.NATIVE_SCAN, G.NATIVE_AGGREGATE} <= classes)
            recorded_failure = (value['result'] == 'failed' and value['error_code'] == code
                                and not checks['plan_observations'] and not value['native_acceleration_verified'])
            require(matches or recorded_failure, code)
    complete = all(len(plans[case_id]) == len(W.SAMPLE_INDICES) for case_id in W.CASES)
    require(not checks['plan_observations'] or complete, 'COMET_PLAN_INVALID')
    require(not value['native_acceleration_verified'] or complete and inputs['variant'] == 'comet',
            'COMET_NATIVE_PLAN_MISSING')
    if value['result'] == 'passed':
        require(all(checks.values()) and complete
                and value['native_acceleration_verified'] == (inputs['variant'] == 'comet'), 'COMET_PASSED_INCOMPLETE')
    return deepcopy(value)


def parse_output(logs, token, inputs, *, executor_pod_name):
    require(type(logs) is str and len(logs.encode()) <= MAX_LOG_BYTES, 'COMET_BENCH_LOG_BOUND')
    lines = [line[len(RESULT_PREFIX):] for line in logs.splitlines() if line.startswith(RESULT_PREFIX)]
    require(len(lines) == 1 and len(lines[0].encode()) <= MAX_LOG_BYTES - 16384, 'COMET_BENCH_RESULT_LINE_INVALID')
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, 'COMET_BENCH_DUPLICATE_KEY'); value[key] = item
        return value
    try:
        return validate_output(json.loads(lines[0], object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError())), token, inputs,
            executor_pod_name=executor_pod_name)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ContractError('COMET_BENCH_OUTPUT_INVALID') from None


def safe_summary(value, token, inputs, *, executor_pod_name):
    value = validate_output(value, token, inputs, executor_pod_name=executor_pod_name)
    return {key: deepcopy(value[key]) for key in ('schema_version', 'run_token', 'scope', 'result', 'variant',
        'workload_id', 'spark_version', 'iceberg_version', 'comet_version', 'scala_version', 'java_version',
        'os_arch', 'checks', 'native_acceleration_verified', 'startup_ns', 'error_code', 'error_classes')}


def job_script(token, inputs):
    # CONFIG uses repr for its fixed Python literal. Canonicalize nested maps so
    # a saved JSON receipt regenerates exactly the same registered source bytes.
    inputs = json.loads(json.dumps(_inputs(inputs, token), sort_keys=True, allow_nan=False))
    script = B.job_script(token, legacy_inputs(inputs)).split('\n', 1)[1]
    config = {'token': token, 'namespace': N.namespace(token), 'image': inputs['image'], 'table': R.table_name(token),
        'sealed': inputs['sealed'], 'executor': N.executor_name(token), 'scope': SCOPE, 'variant': inputs['variant'],
        'prefix': RESULT_PREFIX, 'checks': list(CHECKS), 'secret_dir': R.SECRET_DIR, 'error_class_pattern': R.ERROR_CLASS,
        'comet_version': G.VERSION, 'native_scan': G.NATIVE_SCAN, 'native_aggregate': G.NATIVE_AGGREGATE}
    replacements = [
        ("'master': None, 'checks':", "'variant': CONFIG['variant'], 'comet_version': None, 'scala_version': None, 'java_version': None, 'os_arch': None,\n        'plans': {c: [] for c in W.CASES}, 'native_acceleration_verified': False, 'master': None, 'checks':"),
        ("        output['checks']['classes_loaded'] = True", "        output['checks']['classes_loaded'] = True\n        variant_runtime(spark, output)"),
        ("                elapsed = time.perf_counter_ns() - started", "                elapsed = time.perf_counter_ns() - started\n                observation = observe_plan(frame, spark)\n                output['plans'][case_id].append(observation)\n                check_native(observation)"),
        ("        output['measurements'] = samples; output['checks']['measurements'] = True", "        output['measurements'] = samples; output['checks']['measurements'] = True\n        output['checks']['plan_observations'] = True\n        output['native_acceleration_verified'] = CONFIG['variant'] == 'comet'"),
    ]
    for before, after in replacements:
        require(script.count(before) == 1, 'COMET_BENCH_BASE_JOB_CHANGED')
        script = script.replace(before, after)
    observer = 'def observe_plan(' + G._HELPERS.split('def observe_plan(', 1)[1].split('def require_native(', 1)[0]
    return 'CONFIG = ' + repr(config) + '\n' + observer + _HELPERS + script


_HELPERS = r'''
def variant_runtime(spark, output):
    conf = spark.sparkContext.getConf()
    version = conf.get('spark.comet.version', '')
    output['comet_version'] = version or None
    output['scala_version'] = str(spark._jvm.scala.util.Properties.versionNumberString())
    output['java_version'] = str(spark._jvm.java.lang.System.getProperty('java.specification.version'))
    output['os_arch'] = str(spark._jvm.java.lang.System.getProperty('os.arch'))
    require(output['java_version'] == '17' and output['os_arch'] == 'aarch64'
            and re.fullmatch(r'2\.13\.[0-9]+', output['scala_version']), 'COMET_RUNTIME_MISMATCH')
    if CONFIG['variant'] == 'comet':
        require(version == CONFIG['comet_version'] and conf.get('spark.plugins', '') == 'org.apache.spark.CometPlugin',
                'COMET_RUNTIME_MISMATCH')
    else:
        require(version == '' and conf.get('spark.plugins', '') == '', 'COMET_RUNTIME_MISMATCH')
    output['checks']['variant_runtime'] = True

def check_native(plan):
    classes = {node['class'] for node in plan['nodes']}
    if CONFIG['variant'] == 'comet':
        require({CONFIG['native_scan'], CONFIG['native_aggregate']} <= classes, 'COMET_NATIVE_PLAN_MISSING')
    else:
        require(not any(name.startswith('org.apache.spark.sql.comet.') for name in classes), 'STOCK_COMET_PLAN_PRESENT')
'''
