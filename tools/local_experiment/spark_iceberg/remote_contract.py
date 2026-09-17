"""Pure, fixed native Spark -> precreated Iceberg table contract.

Inputs are host-verified identities, never secrets or model instructions. The
Secret in render() is a private-plan stub, not an API-ready Kubernetes object:
the owner must remove ``keys`` and supply exactly those two stringData entries.
The host owns CREATE/DROP, access grants, runtime attestation and Trino readback.
This module neither connects to services nor proves cleanup from job output.
Missing API endpoints is intentionally an API-denied, non-runnable plan.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re
import textwrap

from tools.local_experiment.spark import contract as N
from tools.local_experiment.spark_iceberg import contract as L

IMAGE = 'shirokuma.local/spark-iceberg-classpath@sha256:b7632ecd9a1b619a2836f6de23a27e0a7d13ade09d614fcfe1cc4e23401c8b43'
SCOPE = 'native-spark-precreated-rest-iceberg-table'
RESULT_PREFIX = 'SHIROKUMA_SPARK_REMOTE_ICEBERG_RESULT='
SECRET_NAME = 'connection'
SECRET_KEYS = ['aws-credentials', 'polaris-token']
SECRET_DIR = '/var/run/shirokuma-connection'
POLARIS_URI = 'http://polaris.shirokuma-dev.svc.cluster.local:8181/api/catalog'
S3_ENDPOINT = 'http://seaweedfs-s3.shirokuma-storage.svc.cluster.local:8333'
DATA_EGRESS = [
    {'namespace': 'shirokuma-dev', 'pod_selector': {'app.kubernetes.io/name': 'polaris',
      'app.kubernetes.io/component': 'catalog-server'}, 'port': 8181},
    {'namespace': 'shirokuma-storage', 'pod_selector': {'app.kubernetes.io/name': 'seaweedfs',
      'app.kubernetes.io/component': 'object-storage'}, 'port': 8333},
]
INPUT_KEYS = {'build_token', 'image', 'table_uuid', 'table_location', 'polaris_uri', 'polaris_realm',
              'warehouse', 'scope', 's3_endpoint', 's3_region', 'data_egress', 'initial_snapshot_id'}
CHECKS = ('classes_loaded', 'precreated_identity', 'empty_before', 'write', 'fresh_dataframe',
          'fresh_sql', 'group_oracle', 'executor', 'metadata')
ERRORS = {'INPUT_INVALID', 'SPARK_START_FAILED', 'VERSION_MISMATCH', 'CLASSPATH_MISSING',
          'PRECREATED_IDENTITY_MISMATCH', 'TABLE_NOT_EMPTY', 'WRITE_FAILED', 'READ_FAILED',
          'ORACLE_MISMATCH', 'EXECUTOR_EVIDENCE_INVALID', 'METADATA_INVALID',
          'SPARK_STOP_FAILED', 'INTERRUPTED', 'INTERNAL_ERROR'}
ERROR_CLASS = r'(?:builtins|py4j|pyspark|java|javax|org\.apache\.(?:iceberg|spark)|software\.amazon\.awssdk)(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+'
ContractError = N.ContractError
require = N.require
oracle_rows = L.oracle_rows
oracle_groups = L.oracle_groups


def table_location(token):
    return 's3://shirokuma-lakehouse/l1/local_experiment/spark_' + N.validate_token(token)


def table_name(token):
    return 'iceberg.local_experiment.spark_' + N.validate_token(token)


def marker(token):
    return 'shirokuma-local-spark-iceberg:' + N.validate_token(token)


def validate_inputs(value):
    require(type(value) is dict and set(value) == INPUT_KEYS, 'INVALID_REMOTE_INPUT_KEYS')
    N.validate_token(value['build_token'])
    require(value['image'] == IMAGE, 'REMOTE_IMAGE_MISMATCH')
    require(type(value['table_uuid']) is str and L.UUID.fullmatch(value['table_uuid']), 'INVALID_TABLE_UUID')
    require(value['initial_snapshot_id'] is None or type(value['initial_snapshot_id']) is int
            and 0 < value['initial_snapshot_id'] < 2**63, 'INVALID_INITIAL_SNAPSHOT')
    require(value['polaris_uri'] == POLARIS_URI and value['warehouse'] == 'shirokuma_l1'
            and value['s3_endpoint'] == S3_ENDPOINT and value['s3_region'] == 'us-east-1', 'REMOTE_ENDPOINT_MISMATCH')
    require(type(value['polaris_realm']) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value['polaris_realm']),
            'INVALID_POLARIS_REALM')
    require(type(value['scope']) is str and re.fullmatch(r'PRINCIPAL_ROLE:spark_[0-9a-f]{32}', value['scope']),
            'INVALID_PRINCIPAL_SCOPE')
    require(type(value['table_location']) is str and len(value['table_location']) <= 512, 'INVALID_TABLE_LOCATION')
    require(re.fullmatch(r's3://shirokuma-lakehouse/l1/local_experiment/spark_[0-9a-f]{32}',
                         value['table_location']), 'INVALID_TABLE_LOCATION')
    require(_same(value['data_egress'], DATA_EGRESS), 'INVALID_DATA_EGRESS')
    return deepcopy(value)


def _inputs(value, token):
    value = validate_inputs(value)
    N.validate_token(token)
    require(value['scope'] == 'PRINCIPAL_ROLE:spark_' + token, 'INPUT_TOKEN_MISMATCH')
    require(value['table_location'] == table_location(token), 'LOCATION_TOKEN_MISMATCH')
    return value


def _same(left, right):
    try:
        return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True, allow_nan=False)
    except (ValueError, TypeError):
        return False


def expected_pod_spec(token, role, inputs):
    inputs = _inputs(inputs, token)
    require(role in ('driver', 'executor'), 'INVALID_ROLE')
    spec = N.pod_spec(role)
    container = spec['containers'][0]
    container['image'] = inputs['image']
    keys = SECRET_KEYS if role == 'driver' else ['aws-credentials']
    spec['volumes'].append({'name': SECRET_NAME, 'secret': {'secretName': SECRET_NAME, 'defaultMode': 0o440,
                           'items': [{'key': key, 'path': key} for key in keys]}})
    container['volumeMounts'].append({'name': SECRET_NAME, 'mountPath': SECRET_DIR, 'readOnly': True})
    container['env'].extend([{'name': 'AWS_SHARED_CREDENTIALS_FILE', 'value': SECRET_DIR + '/aws-credentials'},
                             {'name': 'AWS_EC2_METADATA_DISABLED', 'value': 'true'},
                             {'name': 'AWS_REGION', 'value': inputs['s3_region']}])
    return spec


def spark_properties(token, inputs):
    inputs = _inputs(inputs, token)
    props = N.spark_properties(token)
    props.update({'spark.kubernetes.container.image': inputs['image'],
        'spark.redaction.regex': '(?i)secret|password|token|credential|access[.]?key|authorization',
        'spark.redaction.string.regex': '(?i)(bearer\\s+[^\\s]+|eyJ[A-Za-z0-9_-]+[.][A-Za-z0-9_-]+[.][A-Za-z0-9_-]+)',
        'spark.sql.redaction.options.regex': '(?i)url|token|credential|secret|password|access[.]?key',
        'spark.sql.extensions': 'org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions',
        'spark.sql.catalog.iceberg': 'org.apache.iceberg.spark.SparkCatalog'})
    for key, value in {'type': 'rest', 'uri': inputs['polaris_uri'], 'warehouse': inputs['warehouse'],
        'header.Polaris-Realm': inputs['polaris_realm'], 'scope': inputs['scope'],
        'rest.auth.type': 'oauth2', 'token-refresh-enabled': 'false', 'token-exchange-enabled': 'false',
        'oauth2-server-uri': inputs['polaris_uri'] + '/v1/oauth/tokens',
        'io-impl': 'org.apache.iceberg.aws.s3.S3FileIO', 's3.endpoint': inputs['s3_endpoint'],
        's3.path-style-access': 'true', 'client.region': inputs['s3_region'],
        'client.credentials-provider': 'software.amazon.awssdk.auth.credentials.ProfileCredentialsProvider',
        'client.refresh-credentials-enabled': 'false', 'cache-enabled': 'false',
        # Iceberg 1.11.0's strategy rejects zero; one is its minimum, not off.
        'rest-metrics-reporting-enabled': 'false', 'rest.client.max-retries': '1',
        'rest.client.connection-timeout-ms': '10000', 'rest.client.socket-timeout-ms': '10000'}.items():
        props['spark.sql.catalog.iceberg.' + key] = value
    return props


def render(token, mode='success', *, api_endpoints=None, inputs):
    """Return a fixed private plan (Secret contains key names, no secret values)."""
    inputs = _inputs(inputs, token)
    require(mode == 'success', 'REMOTE_MODE_UNSUPPORTED')
    plan = N.render(token, 'success', api_endpoints=api_endpoints)
    for obj in plan:
        if obj['kind'] == 'ResourceQuota':
            obj['spec']['hard']['secrets'] = '1'
        elif obj['kind'] == 'ConfigMap':
            executor = {'apiVersion': 'v1', 'kind': 'Pod',
                        'metadata': N.metadata(token, 'executor-template', role='executor'),
                        'spec': expected_pod_spec(token, 'executor', inputs)}
            obj['data'] = {'job.py': job_script(token, inputs),
                           'executor-template.json': json.dumps(executor, sort_keys=True),
                           'spark-defaults.conf': ''.join(key+' '+value+'\n' for key, value in sorted(spark_properties(token, inputs).items()))}
        elif obj['kind'] == 'Pod':
            old = obj['spec']['containers'][0]
            spec = expected_pod_spec(token, 'driver', inputs)
            spec['volumes'].append({'name': 'workload', 'configMap': {'name': 'workload', 'defaultMode': 0o444}})
            spec['containers'][0]['volumeMounts'].append({'name': 'workload', 'mountPath': '/opt/shirokuma', 'readOnly': True})
            spec['containers'][0].update({key: old[key] for key in ('command', 'args')})
            obj['spec'] = spec
    extra = [{'apiVersion': 'v1', 'kind': 'Secret', 'metadata': N.metadata(token, SECRET_NAME),
              'type': 'Opaque', 'immutable': True, 'keys': list(SECRET_KEYS)},
             {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy',
              'metadata': N.metadata(token, 'data-egress'),
              'spec': {'podSelector': {'matchLabels': N.labels(token)}, 'policyTypes': ['Egress'],
                       'egress': [{'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': e['namespace']}},
                                           'podSelector': {'matchLabels': e['pod_selector']}}],
                                  'ports': [{'protocol': 'TCP', 'port': e['port']}]} for e in inputs['data_egress']]}}]
    plan[-1:-1] = extra
    return plan


def validate_saved_plan(plan, token, inputs, api_endpoints=None):
    require(_same(plan, render(token, api_endpoints=api_endpoints, inputs=inputs)), 'REMOTE_MANIFEST_PLAN_CHANGED')
    return deepcopy(plan)


def job_script(token, inputs):
    inputs = _inputs(inputs, token)
    config = {'token': token, 'namespace': N.namespace(token), 'image': inputs['image'], 'table': table_name(token),
              'table_uuid': inputs['table_uuid'], 'table_location': inputs['table_location'], 'marker': marker(token),
              'initial_snapshot_id': inputs['initial_snapshot_id'],
              'error_class_pattern': ERROR_CLASS,
              'scope': SCOPE, 'checks': list(CHECKS), 'prefix': RESULT_PREFIX, 'secret_dir': SECRET_DIR,
              'executor': N.executor_name(token)}
    return 'CONFIG = ' + repr(config) + '\n' + textwrap.dedent(_JOB)


def _files(value, location):
    require(type(value) is list and 1 <= len(value) <= 32, 'INVALID_DATA_FILES')
    seen, records = set(), 0
    for item in value:
        require(type(item) is dict and set(item) == {'path', 'record_count', 'file_size_in_bytes'}, 'INVALID_DATA_FILE')
        require(type(item['path']) is str and item['path'].startswith(location + '/data/')
                and len(item['path']) <= 1024 and re.fullmatch(r'[A-Za-z0-9_./:\-=]+', item['path'])
                and '..' not in item['path'] and item['path'].endswith('.parquet'), 'INVALID_DATA_FILE_PATH')
        require(item['path'] not in seen, 'DUPLICATE_DATA_FILE')
        seen.add(item['path'])
        require(type(item['record_count']) is int and 0 < item['record_count'] <= 32
                and type(item['file_size_in_bytes']) is int and 0 < item['file_size_in_bytes'] <= 16*1024*1024,
                'INVALID_DATA_FILE_COUNT')
        records += item['record_count']
    require(records == 32 and value == sorted(value, key=lambda x: x['path']), 'DATA_FILE_ORACLE_MISMATCH')


def validate_output(value, token, inputs, *, executor_pod_name):
    inputs = _inputs(inputs, token)
    fixed = {'schema_version': 1, 'run_token': token, 'namespace': N.namespace(token), 'scope': SCOPE,
             'mode': 'success', 'configured_image': inputs['image']}
    fields = {'result', 'spark_version', 'iceberg_version', 'master', 'checks', 'row_count', 'dataframe_rows',
              'sql_rows', 'groups', 'partitions', 'shuffle_observed', 'table_identity', 'data_files', 'error_code', 'error_classes'}
    require(type(value) is dict and set(value) == set(fixed) | fields, 'INVALID_RESULT_FIELDS')
    for key, expected in fixed.items():
        require(type(value[key]) is type(expected) and value[key] == expected, 'RESULT_IDENTITY_MISMATCH')
    require(value['result'] in ('passed', 'failed'), 'INVALID_RESULT_STATUS')
    require(type(value['error_classes']) is list and len(value['error_classes']) <= 8
            and all(type(name) is str and len(name) <= 256 and
                    (name in ('unknown', 'SafeFailure') or re.fullmatch(ERROR_CLASS, name))
                    for name in value['error_classes']), 'INVALID_ERROR_CLASSES')
    require(type(value['checks']) is dict and set(value['checks']) == set(CHECKS)
            and all(type(x) is bool for x in value['checks'].values()), 'INVALID_RESULT_CHECKS')
    for key, expected in [('spark_version', N.VERSION), ('iceberg_version', L.ICEBERG_VERSION),
                          ('master', 'k8s://https://kubernetes.default.svc:443')]:
        require(value[key] is None or value[key] == expected, 'RESULT_VERSION_MISMATCH')
    for key, expected, check in [('dataframe_rows', L.oracle_rows(), 'fresh_dataframe'),
                                  ('sql_rows', L.oracle_rows(), 'fresh_sql'), ('groups', L.oracle_groups(), 'group_oracle')]:
        require(value[key] is None or _same(value[key], expected), 'RESULT_ORACLE_MISMATCH')
        require(not value['checks'][check] or value[key] is not None, 'MISSING_CHECK_EVIDENCE')
    require(value['row_count'] is None if value['dataframe_rows'] is None else
            type(value['row_count']) is int and value['row_count'] == 32, 'RESULT_COUNT_MISMATCH')
    require(type(value['shuffle_observed']) is bool, 'INVALID_SHUFFLE_EVIDENCE')
    if value['partitions'] is not None:
        native = N.expected_output(token, 'success')
        native['partitions'] = value['partitions']
        N.validate_output(native, token, 'success', executor_pod_name=executor_pod_name)
    require(not value['checks']['executor'] or (value['partitions'] is not None and value['shuffle_observed']),
            'MISSING_EXECUTOR_EVIDENCE')
    identity = value['table_identity']
    if identity is not None:
        require(type(identity) is dict and set(identity) == {'uuid', 'location', 'comment', 'snapshot_id',
                'parent_snapshot_id', 'metadata_location', 'operation'}, 'INVALID_METADATA_FIELDS')
        require(identity['uuid'] == inputs['table_uuid'] and identity['location'] == inputs['table_location']
                and identity['comment'] == marker(token) and identity['operation'] == 'append'
                and type(identity['snapshot_id']) is int and 0 < identity['snapshot_id'] < 2**63,
                'RESULT_TABLE_IDENTITY_MISMATCH')
        require(identity['snapshot_id'] != inputs['initial_snapshot_id']
                and type(identity['parent_snapshot_id']) is type(inputs['initial_snapshot_id'])
                and identity['parent_snapshot_id'] == inputs['initial_snapshot_id'], 'RESULT_SNAPSHOT_PARENT_MISMATCH')
        require(type(identity['metadata_location']) is str and identity['metadata_location'].startswith(inputs['table_location']+'/metadata/')
                and len(identity['metadata_location']) <= 1024 and re.fullmatch(r'[A-Za-z0-9_./:\-]+', identity['metadata_location'])
                and '..' not in identity['metadata_location'] and identity['metadata_location'].endswith('.metadata.json'),
                'INVALID_METADATA_LOCATION')
    if value['data_files'] is not None:
        _files(value['data_files'], inputs['table_location'])
    require(not value['checks']['metadata'] or (identity is not None and value['data_files'] is not None), 'MISSING_METADATA_EVIDENCE')
    if value['result'] == 'passed':
        require(all(value['checks'].values()) and value['error_code'] is None and value['error_classes'] == []
                and all(value[key] is not None for key in ('spark_version', 'iceberg_version', 'master')), 'INCOMPLETE_PASSED_RESULT')
    else:
        require(type(value['error_code']) is str and value['error_code'] in ERRORS
                and value['error_classes'], 'INVALID_SAFE_ERROR')
    return deepcopy(value)


def parse_output(logs, token, inputs, *, executor_pod_name):
    require(type(logs) is str, 'JOB_LOG_BOUND')
    try:
        require(len(logs.encode()) <= N.MAX_LOG_BYTES, 'JOB_LOG_BOUND')
        lines = [line[len(RESULT_PREFIX):] for line in logs.splitlines() if line.startswith(RESULT_PREFIX)]
        require(len(lines) == 1 and len(lines[0]) <= 32*1024, 'RESULT_LINE_COUNT')
        def pairs(items):
            value = {}
            for key, item in items:
                require(key not in value, 'DUPLICATE_RESULT_KEY')
                value[key] = item
            return value
        value = json.loads(lines[0], object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ContractError('NONFINITE_RESULT')))
        return validate_output(value, token, inputs, executor_pod_name=executor_pod_name)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ContractError('INVALID_REMOTE_RESULT') from None


def safe_summary(value, token, inputs, *, executor_pod_name):
    value = validate_output(value, token, inputs, executor_pod_name=executor_pod_name)
    return {key: deepcopy(value[key]) for key in ('schema_version', 'run_token', 'scope', 'result',
            'spark_version', 'iceberg_version', 'checks', 'row_count', 'shuffle_observed', 'error_code', 'error_classes')}


_JOB = r'''
import json
import os
from pathlib import Path
import re
import socket
import sys

class SafeFailure(Exception):
    pass

def require(value, code):
    if not value:
        raise SafeFailure(code)

def java_items(value, limit, code):
    # Iceberg Iterable/CloseableIterable is a Py4J JavaObject, not necessarily
    # a Python-iterable JavaList. Use the Java Iterator contract explicitly.
    iterator = value.iterator()
    count = 0
    while iterator.hasNext():
        require(count < limit, code)
        yield iterator.next()
        count += 1

def error_classes(error):
    # Class names only. Never call str(error), getMessage(), toString() or
    # stack-trace APIs on library exceptions. A dead Py4J gateway is unknown.
    names = []
    def add(name):
        name = name if type(name) is str and len(name) <= 256 and (
            name == 'SafeFailure' or re.fullmatch(CONFIG['error_class_pattern'], name)) else 'unknown'
        if len(names) < 8:
            names.append(name)
    try:
        current, seen = error, set()
        for _ in range(4):
            if current is None or id(current) in seen or len(names) >= 8:
                break
            seen.add(id(current))
            add('SafeFailure' if isinstance(current, SafeFailure) else
                type(current).__module__ + '.' + type(current).__qualname__)
            java = getattr(current, 'java_exception', None)
            if java is None:
                java = getattr(current, '_origin', None)
            for _ in range(7):
                if java is None or len(names) >= 8:
                    break
                add(java.getClass().getName())
                java = java.getCause()
            current = current.__cause__
    except BaseException:
        add('unknown')
    return names or ['unknown']

def expected_rows():
    return [[i, 'row-%03d' % i, i % 4, (i+1)*7] for i in range(32)]

def expected_groups():
    rows = [[b, 0, 0] for b in range(4)]
    for row in expected_rows():
        rows[row[2]][1] += 1
        rows[row[2]][2] += row[3]
    return rows

def observe_partition(partition_id, rows):
    from pyspark import TaskContext
    context = TaskContext.get()
    require(context is not None, 'EXECUTOR_EVIDENCE_INVALID')
    yield {'partition_id': partition_id, 'stage_id': context.stageId(),
           'task_attempt_id': context.taskAttemptId(), 'attempt_number': context.attemptNumber(),
           'executor_id': os.environ.get('SPARK_EXECUTOR_ID'), 'hostname': socket.gethostname(),
           'rows': [[int(r.bucket), int(r['count']), int(r.total)] for r in rows]}

def main():
    output = {'schema_version': 1, 'run_token': CONFIG['token'], 'namespace': CONFIG['namespace'],
        'scope': CONFIG['scope'], 'mode': 'success', 'configured_image': CONFIG['image'], 'result': 'failed',
        'spark_version': None, 'iceberg_version': None, 'master': None,
        'checks': {key: False for key in CONFIG['checks']}, 'row_count': None,
        'dataframe_rows': None, 'sql_rows': None, 'groups': None, 'partitions': None,
        'shuffle_observed': False, 'table_identity': None, 'data_files': None, 'error_code': None, 'error_classes': []}
    spark, stage = None, 'start'
    def load(session):
        # CatalogManager uses SQLConf.get, and SparkCatalog uses active(). A
        # direct JVM helper call does not provide Spark's usual withActive scope.
        sessions = session._jvm.org.apache.spark.sql.SparkSession
        previous = sessions.getActiveSession()
        try:
            sessions.setActiveSession(session._jsparkSession)
            return session._jvm.org.apache.iceberg.spark.Spark3Util.loadIcebergTable(session._jsparkSession, CONFIG['table'])
        finally:
            if previous.isDefined():
                sessions.setActiveSession(previous.get())
            else:
                sessions.clearActiveSession()
    def initialize(session):
        # Runtime SQLConf only: never submit/config-file/env token values. The
        # initialized driver catalog owns its auth session after SQLConf unset.
        bearer = Path(CONFIG['secret_dir']+'/polaris-token').read_text()
        require(0 < len(bearer) <= 16384 and re.fullmatch(r'[A-Za-z0-9._~+/-]+=*', bearer), 'INPUT_INVALID')
        key = 'spark.sql.catalog.iceberg.token'
        try:
            session.conf.set(key, bearer)
            return load(session)
        finally:
            session.conf.unset(key)
            bearer = None
    def identity(table):
        table.refresh()
        require(str(table.operations().current().uuid()) == CONFIG['table_uuid']
                and str(table.location()) == CONFIG['table_location']
                and str(table.properties().get('comment')) == CONFIG['marker'], 'PRECREATED_IDENTITY_MISMATCH')
        fields = list(java_items(table.schema().columns(), 4, 'PRECREATED_IDENTITY_MISMATCH'))
        require([(str(f.name()), str(f.type())) for f in fields] ==
                [('id','int'),('name','string'),('bucket','int'),('amount','long')]
                and table.spec().isUnpartitioned() and int(table.operations().current().formatVersion()) == 2,
                'PRECREATED_IDENTITY_MISMATCH')
    def empty(table):
        snapshot = table.currentSnapshot()
        expected = CONFIG['initial_snapshot_id']
        if expected is None:
            require(snapshot is None and not list(java_items(table.snapshots(), 1, 'TABLE_NOT_EMPTY')), 'TABLE_NOT_EMPTY')
        else:
            require(snapshot is not None and int(snapshot.snapshotId()) == expected
                    and snapshot.parentId() is None and str(snapshot.operation()) == 'append'
                    and snapshot.summary().get('total-records') == '0'
                    and snapshot.summary().get('total-data-files') == '0'
                    and [int(s.snapshotId()) for s in java_items(table.snapshots(), 1, 'TABLE_NOT_EMPTY')] == [expected],
                    'TABLE_NOT_EMPTY')
        tasks = table.newScan().planFiles()
        try:
            require(not tasks.iterator().hasNext(), 'TABLE_NOT_EMPTY')
        finally:
            tasks.close()
    try:
        from pyspark.sql import SparkSession, functions as F
        spark = SparkSession.builder.appName('shirokuma-spark-remote-'+CONFIG['token']).getOrCreate()
        output['spark_version'], output['master'] = spark.version, spark.sparkContext.master
        loader = spark._jvm.java.lang.Thread.currentThread().getContextClassLoader()
        for name in ('org.apache.iceberg.spark.SparkCatalog', 'org.apache.iceberg.rest.RESTCatalog',
                     'org.apache.iceberg.aws.s3.S3FileIO', 'software.amazon.awssdk.services.s3.S3Client'):
            loader.loadClass(name)
        output['iceberg_version'] = str(spark._jvm.org.apache.iceberg.IcebergBuild.version())
        require(output['spark_version'] == '4.1.3' and output['iceberg_version'] == '1.11.0'
                and output['master'] == 'k8s://https://kubernetes.default.svc:443'
                and spark.sparkContext.getConf().get('spark.kubernetes.container.image') == CONFIG['image'], 'VERSION_MISMATCH')
        output['checks']['classes_loaded'] = True
        stage = 'identity'
        before = initialize(spark)
        identity(before)
        output['checks']['precreated_identity'] = True
        empty(before)
        require(spark.table(CONFIG['table']).limit(1).count() == 0, 'TABLE_NOT_EMPTY')
        output['checks']['empty_before'] = True
        stage = 'write'
        before_write = load(spark)
        identity(before_write)
        empty(before_write)
        spark.sql('INSERT INTO '+CONFIG['table']+" SELECT CAST(id AS INT), concat('row-', lpad(CAST(id AS STRING), 3, '0')), "
                  'CAST(pmod(id, 4) AS INT), CAST((id + 1) * 7 AS BIGINT) FROM range(32)')
        output['checks']['write'] = True
        stage = 'read'
        fresh = spark.newSession()
        initialize(fresh)
        fresh.catalog.clearCache()
        frame = fresh.read.format('iceberg').load(CONFIG['table'])
        require(frame.schema.simpleString() == 'struct<id:int,name:string,bucket:int,amount:bigint>', 'ORACLE_MISMATCH')
        rows = [list(r) for r in frame.orderBy('id').limit(33).collect()]
        require(rows == expected_rows(), 'ORACLE_MISMATCH')
        output['dataframe_rows'], output['row_count'] = rows, len(rows)
        output['checks']['fresh_dataframe'] = True
        rows = [list(r) for r in fresh.sql('SELECT id,name,bucket,amount FROM '+CONFIG['table']+' ORDER BY id LIMIT 33').collect()]
        require(rows == expected_rows(), 'ORACLE_MISMATCH')
        output['sql_rows'], output['checks']['fresh_sql'] = rows, True
        grouped = frame.repartition(2, 'bucket').groupBy('bucket').agg(F.count('*').alias('count'), F.sum('amount').alias('total'))
        output['shuffle_observed'] = 'Exchange' in grouped._jdf.queryExecution().executedPlan().toString()
        packets = grouped.rdd.mapPartitionsWithIndex(observe_partition).collect()
        groups = sorted((r for p in packets for r in p['rows']), key=lambda r: r[0])
        require(groups == expected_groups(), 'ORACLE_MISMATCH')
        output['groups'], output['checks']['group_oracle'] = groups, True
        parts = sorted(({k:v for k,v in p.items() if k != 'rows'} for p in packets), key=lambda p:p['partition_id'])
        require(len(parts) == 2 and output['shuffle_observed']
                and [p['partition_id'] for p in parts] == [0,1]
                and len({p['stage_id'] for p in parts}) == 1 and len({p['task_attempt_id'] for p in parts}) == 2
                and all(p['executor_id'] == '1' and p['hostname'] == CONFIG['executor']
                        and p['attempt_number'] == 0 for p in parts), 'EXECUTOR_EVIDENCE_INVALID')
        output['partitions'], output['checks']['executor'] = parts, True
        stage = 'metadata'
        written = load(fresh)
        identity(written)
        snapshot = written.currentSnapshot()
        expected_parent = CONFIG['initial_snapshot_id']
        parent = None if snapshot is None or snapshot.parentId() is None else int(snapshot.parentId())
        snapshot_ids = [int(s.snapshotId()) for s in java_items(written.snapshots(), 2, 'METADATA_INVALID')]
        require(snapshot is not None and str(snapshot.operation()) == 'append'
                and parent == expected_parent and len(snapshot_ids) == (1 if expected_parent is None else 2),
                'METADATA_INVALID')
        snapshot_id = int(snapshot.snapshotId())
        require(0 < snapshot_id < 2**63 and snapshot_id != expected_parent and len(set(snapshot_ids)) == len(snapshot_ids)
                and set(snapshot_ids) == ({snapshot_id} if expected_parent is None else {expected_parent, snapshot_id}),
                'METADATA_INVALID')
        output['table_identity'] = {'uuid': str(written.operations().current().uuid()),
            'location': str(written.location()), 'comment': str(written.properties().get('comment')),
            'snapshot_id': snapshot_id, 'parent_snapshot_id': parent,
            'metadata_location': str(written.operations().current().metadataFileLocation()),
            'operation': str(snapshot.operation())}
        files = []
        tasks = written.newScan().useSnapshot(snapshot_id).planFiles()
        try:
            for task in java_items(tasks, 32, 'METADATA_INVALID'):
                file = task.file()
                require(len(files) < 32 and int(file.content().id()) == 0 and str(file.format()) == 'PARQUET', 'METADATA_INVALID')
                files.append({'path': str(file.location()), 'record_count': int(file.recordCount()),
                              'file_size_in_bytes': int(file.fileSizeInBytes())})
        finally:
            tasks.close()
        require(files and sum(f['record_count'] for f in files) == 32
                and all(f['path'].startswith(CONFIG['table_location']+'/data/') for f in files), 'METADATA_INVALID')
        output['data_files'] = sorted(files, key=lambda f:f['path'])
        output['checks']['metadata'] = True
    except BaseException as error:
        output['error_classes'] = error_classes(error)
        output['error_code'] = str(error) if isinstance(error, SafeFailure) else (
            'INTERRUPTED' if not isinstance(error, Exception) else {'start':'SPARK_START_FAILED',
            'identity':'PRECREATED_IDENTITY_MISMATCH','write':'WRITE_FAILED','read':'READ_FAILED',
            'metadata':'METADATA_INVALID'}.get(stage, 'INTERNAL_ERROR'))
    finally:
        if spark is not None:
            try:
                spark.stop()
            except BaseException as error:
                if output['error_code'] is None:
                    output['error_code'] = 'SPARK_STOP_FAILED'
                    output['error_classes'] = error_classes(error)
    if output['error_code'] is None and all(output['checks'].values()):
        output['result'] = 'passed'
    print(CONFIG['prefix']+json.dumps(output, sort_keys=True, separators=(',', ':'), allow_nan=False), flush=True)
    return 0 if output['result'] == 'passed' else 1

if __name__ == '__main__':
    sys.exit(main())
'''
