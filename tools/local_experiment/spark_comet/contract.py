"""Fixed local[1] Comet gate on 32 disposable Iceberg rows; no remote catalog.

The existing Iceberg job owns its temporary table and UUID-bound cleanup. This
profile adds a fixed native plugin and observations of executed plans. It does
not establish Kubernetes/S3 compatibility or a performance improvement.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

from tools.local_experiment.spark_iceberg import contract as I

VERSION = '1.0.0'
SPARK_VERSION = I.SPARK_VERSION
ICEBERG_VERSION = I.ICEBERG_VERSION
SCOPE = 'local-single-process-comet-iceberg-classpath'
RESULT_PREFIX = 'SHIROKUMA_SPARK_COMET_RESULT='
MAX_LOG_BYTES = 512 * 1024
MAX_PLAN_BYTES = 32 * 1024
CHECKS = (*I.CHECKS, 'comet_runtime', 'native_plan')
ERRORS = I.ERRORS | {'COMET_RUNTIME_MISMATCH', 'COMET_NATIVE_PLAN_MISSING', 'COMET_PLAN_INVALID'}
ContractError = I.ContractError
require = I.require
validate_token = I.validate_token
oracle_rows = I.oracle_rows
oracle_groups = I.oracle_groups
PLAN_KEYS = {'nodes', 'fingerprint_sha256', 'text', 'text_sha256', 'explain'}
EXTRA_KEYS = {'comet_version', 'scala_version', 'java_version', 'os_arch', 'plans'}
NATIVE_SCAN = 'org.apache.spark.sql.comet.CometIcebergNativeScanExec'
NATIVE_AGGREGATE = 'org.apache.spark.sql.comet.CometHashAggregateExec'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value)).hexdigest()


def spark_arguments(token):
    props = {
        'spark.plugins': 'org.apache.spark.CometPlugin',
        'spark.shuffle.manager': 'org.apache.spark.sql.comet.execution.shuffle.CometShuffleManager',
        'spark.comet.enabled': 'true',
        'spark.comet.exec.enabled': 'true',
        'spark.comet.scan.icebergNative.enabled': 'true',
        'spark.comet.explain.fallback.enabled': 'true',
        'spark.comet.explain.fallback.log.enabled': 'true',
        'spark.memory.offHeap.enabled': 'true',
        'spark.memory.offHeap.size': '256m',
        'spark.comet.maxTempDirectorySize': '128m',
        'spark.comet.parquet.write.enabled': 'false',
    }
    args = ['--master', 'local[1]', '--driver-memory', '512m']
    for key, value in sorted(props.items()):
        args += ['--conf', key + '=' + value]
    return args + ['/opt/shirokuma/check.py', validate_token(token)]


def validate_plan(value):
    require(type(value) is dict and set(value) == PLAN_KEYS, 'COMET_PLAN_INVALID')
    nodes = value['nodes']
    require(type(nodes) is list and 1 <= len(nodes) <= 128, 'COMET_PLAN_INVALID')
    for index, node in enumerate(nodes):
        require(type(node) is dict and set(node) == {'class', 'name', 'parent'}, 'COMET_PLAN_INVALID')
        require(type(node['class']) is str and re.fullmatch(r'org\.apache\.spark\.sql\.[A-Za-z0-9_.$]+', node['class'])
                and type(node['name']) is str and 0 < len(node['name']) <= 256
                and (node['parent'] is None if index == 0 else
                     type(node['parent']) is int and 0 <= node['parent'] < index), 'COMET_PLAN_INVALID')
    for key in ('text', 'explain'):
        require(type(value[key]) is str and 0 < len(value[key].encode()) <= MAX_PLAN_BYTES, 'COMET_PLAN_INVALID')
    require(value['fingerprint_sha256'] == digest(nodes)
            and value['text_sha256'] == digest(value['text'].encode()), 'COMET_PLAN_INVALID')
    return deepcopy(value)


def validate_output(value, token):
    require(type(value) is dict and EXTRA_KEYS <= set(value), 'COMET_OUTPUT_FIELDS_INVALID')
    require(value.get('scope') == SCOPE and type(value.get('checks')) is dict
            and set(value['checks']) == set(CHECKS)
            and all(type(v) is bool for v in value['checks'].values()), 'COMET_OUTPUT_IDENTITY_INVALID')
    base = {k: deepcopy(v) for k, v in value.items() if k not in EXTRA_KEYS}
    base['scope'] = I.SCOPE
    base['checks'] = {key: value['checks'][key] for key in I.CHECKS}
    if base.get('error_code') in ERRORS - I.ERRORS:
        base['error_code'] = 'INTERNAL_ERROR'
    I.validate_output(base, token)
    require(value['error_code'] is None or value['error_code'] in ERRORS, 'COMET_ERROR_INVALID')
    for key in EXTRA_KEYS - {'plans'}:
        require(value[key] is None or type(value[key]) is str and 0 < len(value[key]) <= 64,
                'COMET_RUNTIME_MISMATCH')
    if value['checks']['comet_runtime'] or value['result'] == 'passed':
        require(value['comet_version'] == VERSION and value['java_version'] == '17'
                and value['os_arch'] == 'aarch64' and re.fullmatch(r'2\.13\.[0-9]+', value['scala_version'] or ''),
                'COMET_RUNTIME_MISMATCH')
    require(type(value['plans']) is dict and set(value['plans']) <= {'rows', 'groups'}, 'COMET_PLAN_INVALID')
    for plan in value['plans'].values():
        validate_plan(plan)
    if value['checks']['native_plan'] or value['result'] == 'passed':
        require(set(value['plans']) == {'rows', 'groups'}, 'COMET_NATIVE_PLAN_MISSING')
        for key, required in [('rows', {NATIVE_SCAN}), ('groups', {NATIVE_SCAN, NATIVE_AGGREGATE})]:
            require(required <= {node['class'] for node in value['plans'][key]['nodes']}, 'COMET_NATIVE_PLAN_MISSING')
    if value['result'] == 'passed':
        require(all(value['checks'].values()), 'COMET_PASSED_INCOMPLETE')
    return deepcopy(value)


def parse_output(logs, token):
    require(type(logs) is str and len(logs.encode()) <= MAX_LOG_BYTES, 'COMET_LOG_BOUND')
    lines = [line[len(RESULT_PREFIX):] for line in logs.splitlines() if line.startswith(RESULT_PREFIX)]
    require(len(lines) == 1 and len(lines[0].encode()) <= 192 * 1024, 'COMET_RESULT_LINE_INVALID')
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, 'COMET_DUPLICATE_KEY'); value[key] = item
        return value
    try:
        return validate_output(json.loads(lines[0], object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError())), token)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ContractError('COMET_RESULT_INVALID') from None


def safe_summary(value):
    value = validate_output(value, value.get('run_token') if type(value) is dict else None)
    return {key: deepcopy(value[key]) for key in ('schema_version', 'run_token', 'scope', 'result',
        'spark_version', 'iceberg_version', 'comet_version', 'scala_version', 'java_version',
        'os_arch', 'master', 'checks', 'row_count', 'cleanup', 'error_code')}


def job_script():
    # Reuse the registered owner/UUID cleanup job, with exact source anchors.
    # An upstream edit fails generation rather than silently skipping a gate.
    job = I._JOB
    replacements = [
        ("'spark_version': None, 'iceberg_version': None, 'master': None,",
         "'spark_version': None, 'iceberg_version': None, 'master': None,\n              'comet_version': None, 'scala_version': None, 'java_version': None, 'os_arch': None, 'plans': {},"),
        ("        output['checks']['classes_loaded'] = True", "        output['checks']['classes_loaded'] = True\n        comet_runtime(spark, output)"),
        ("        output['checks']['fresh_dataframe'] = True", "        output['checks']['fresh_dataframe'] = True\n        output['plans']['rows'] = observe_plan(dataframe, spark)"),
        ("        groups = [list(row) for row in fresh.sql('SELECT bucket, count(*), sum(amount) FROM ' + table +\n                                                ' GROUP BY bucket ORDER BY bucket').collect()]",
         "        grouped = fresh.sql('SELECT bucket, count(*), sum(amount) FROM ' + table + ' GROUP BY bucket ORDER BY bucket')\n        groups = [list(row) for row in grouped.collect()]"),
        ("        output['checks']['group_oracle'] = True",
         "        output['checks']['group_oracle'] = True\n        output['plans']['groups'] = observe_plan(grouped, spark)\n        require_native(output)"),
    ]
    for before, after in replacements:
        require(job.count(before) == 1, 'COMET_BASE_JOB_CHANGED')
        job = job.replace(before, after)
    config = {'spark_version': SPARK_VERSION, 'iceberg_version': ICEBERG_VERSION, 'scope': SCOPE,
        'warehouse': I.WAREHOUSE, 'rows': I.ROWS, 'checks': list(CHECKS), 'prefix': RESULT_PREFIX,
        'comet_version': VERSION, 'native_scan': NATIVE_SCAN, 'native_aggregate': NATIVE_AGGREGATE}
    return 'CONFIG = ' + repr(config) + '\n' + _HELPERS + job


_HELPERS = r'''
import hashlib

def comet_runtime(spark, output):
    loader = spark._jvm.java.lang.Thread.currentThread().getContextClassLoader()
    for name in ('org.apache.spark.CometPlugin', 'org.apache.comet.NativeBase'):
        loader.loadClass(name)
    output['comet_version'] = spark.sparkContext.getConf().get('spark.comet.version', '')
    output['scala_version'] = str(spark._jvm.scala.util.Properties.versionNumberString())
    output['java_version'] = str(spark._jvm.java.lang.System.getProperty('java.specification.version'))
    output['os_arch'] = str(spark._jvm.java.lang.System.getProperty('os.arch'))
    require(output['comet_version'] == CONFIG['comet_version'] and output['java_version'] == '17'
            and output['os_arch'] == 'aarch64' and re.fullmatch(r'2\.13\.[0-9]+', output['scala_version']),
            'COMET_RUNTIME_MISMATCH')
    output['checks']['comet_runtime'] = True

def observe_plan(frame, spark):
    plan = frame._jdf.queryExecution().executedPlan()
    nodes = []
    def visit(node, parent):
        require(len(nodes) < 128, 'COMET_PLAN_INVALID')
        index = len(nodes)
        nodes.append({'class': str(node.getClass().getName()), 'name': str(node.nodeName()), 'parent': parent})
        children = node.children()
        require(0 <= children.size() <= 32, 'COMET_PLAN_INVALID')
        for i in range(children.size()): visit(children.apply(i), index)
    visit(plan, None)
    text = str(plan.treeString())
    explain = str(spark._jvm.PythonSQLUtils.explainString(frame._jdf.queryExecution(), 'extended'))
    require(0 < len(text.encode()) <= 32768 and 0 < len(explain.encode()) <= 32768, 'COMET_PLAN_INVALID')
    raw = json.dumps(nodes, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return {'nodes': nodes, 'fingerprint_sha256': hashlib.sha256(raw).hexdigest(),
            'text': text, 'text_sha256': hashlib.sha256(text.encode()).hexdigest(), 'explain': explain}

def require_native(output):
    for key, required in [('rows', {CONFIG['native_scan']}),
                          ('groups', {CONFIG['native_scan'], CONFIG['native_aggregate']})]:
        require(required <= {node['class'] for node in output['plans'][key]['nodes']}, 'COMET_NATIVE_PLAN_MISSING')
    output['checks']['native_plan'] = True
'''
