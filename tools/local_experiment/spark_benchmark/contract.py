"""Fixed snapshot read benchmark on the existing native Spark/Iceberg image.

This contract accepts no SQL, source path, endpoint or workload from a caller.
Host preparation seals one disposable table. The Spark job only reads that
snapshot; timing is descriptive, not a cold-cache or performance guarantee.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from tools.local_experiment.spark import contract as N
from tools.local_experiment.spark_iceberg import remote_contract as R
from tools.local_experiment.spark_benchmark import workload as W

SCOPE = 'native-spark-fixed-snapshot-benchmark'
RESULT_PREFIX = 'SHIROKUMA_SPARK_BENCHMARK_RESULT='
CHECKS = ('classes_loaded', 'sealed_before', 'measurements', 'executor', 'sealed_after')
ERRORS = {'SPARK_START_FAILED', 'SEALED_IDENTITY_MISMATCH', 'MEASUREMENT_FAILED',
          'EXECUTOR_EVIDENCE_INVALID', 'SPARK_STOP_FAILED', 'INTERRUPTED', 'INTERNAL_ERROR'}
require = N.require


def marker(token):
    return 'shirokuma-local-spark-benchmark:' + N.validate_token(token)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def normalized(case_id, value):
    require(type(value) is dict and set(value) == {'schema', 'rows', 'row_count', 'sha256'}, 'BENCH_RESULT_INVALID')
    schema = value['schema']
    require(type(schema) is list and all(type(c) is dict and set(c) == {'name', 'type'} for c in schema),
            'BENCH_SCHEMA_INVALID')
    expected = W.normalize_result(case_id, 'spark', [c['name'] for c in schema],
                                  [c['type'] for c in schema], value['rows'])
    require(R._same(value, expected), 'BENCH_RESULT_INVALID')
    return deepcopy(value)


def validate_seal(value, token, base):
    require(type(value) is dict and set(value) == {'identity', 'data_files', 'dataset'}, 'BENCH_SEAL_INVALID')
    identity = value['identity']
    require(type(identity) is dict and set(identity) == {'table_uuid', 'table_location', 'marker', 'snapshot_id',
            'parent_snapshot_id', 'metadata_location', 'operation'}, 'BENCH_IDENTITY_INVALID')
    require(identity['table_uuid'] == base['table_uuid'] and identity['table_location'] == R.table_location(token)
            and identity['marker'] == marker(token) and identity['operation'] == 'append'
            and type(identity['snapshot_id']) is int and 0 < identity['snapshot_id'] < 2**63
            and identity['snapshot_id'] == base['initial_snapshot_id'], 'BENCH_IDENTITY_INVALID')
    parent = identity['parent_snapshot_id']
    require(parent is None or type(parent) is int and 0 < parent < 2**63 and parent != identity['snapshot_id'],
            'BENCH_PARENT_INVALID')
    location = identity['metadata_location']
    require(type(location) is str and location.startswith(base['table_location'] + '/metadata/')
            and len(location) <= 1024 and re.fullmatch(r'[A-Za-z0-9_./:\-]+', location)
            and '..' not in location and location.endswith('.metadata.json'), 'BENCH_METADATA_PATH_INVALID')
    dataset = value['dataset']
    require(type(dataset) is dict and set(dataset) == {'row_count', 'logical_sha256', 'expected'}
            and type(dataset['row_count']) is int and 1 <= dataset['row_count'] <= 1_000_000
            and type(dataset['logical_sha256']) is str and re.fullmatch('[0-9a-f]{64}', dataset['logical_sha256'])
            and type(dataset['expected']) is dict and set(dataset['expected']) == set(W.CASES), 'BENCH_DATASET_INVALID')
    for case_id in W.CASES:
        normalized(case_id, dataset['expected'][case_id])
    files = value['data_files']
    require(type(files) is list and 1 <= len(files) <= 32, 'BENCH_FILES_INVALID')
    seen, records, size = set(), 0, 0
    for file in files:
        require(type(file) is dict and set(file) == {'path', 'record_count', 'file_size_in_bytes'}, 'BENCH_FILE_INVALID')
        path = file['path']
        require(type(path) is str and len(path) <= 1024 and path.startswith(base['table_location'] + '/data/')
                and re.fullmatch(r'[A-Za-z0-9_./:\-=]+', path) and '..' not in path and path.endswith('.parquet')
                and path not in seen, 'BENCH_FILE_PATH_INVALID')
        seen.add(path)
        require(type(file['record_count']) is int and 0 < file['record_count'] <= 1_000_000
                and type(file['file_size_in_bytes']) is int and 0 < file['file_size_in_bytes'] <= 256*1024*1024,
                'BENCH_FILE_SIZE_INVALID')
        records += file['record_count']; size += file['file_size_in_bytes']
    require(files == sorted(files, key=lambda f: f['path']) and records == dataset['row_count']
            and size <= 256*1024*1024, 'BENCH_FILE_TOTAL_INVALID')
    return deepcopy(value)


def validate_inputs(value):
    require(type(value) is dict and set(value) == R.INPUT_KEYS | {'sealed'}, 'BENCH_INPUT_KEYS_INVALID')
    base = R.validate_inputs({k: value[k] for k in R.INPUT_KEYS})
    token = base['scope'].removeprefix('PRINCIPAL_ROLE:spark_')
    R._inputs(base, token)
    validate_seal(value['sealed'], token, base)
    return deepcopy(value)


def _inputs(value, token):
    value = validate_inputs(value)
    require(value['scope'] == 'PRINCIPAL_ROLE:spark_' + N.validate_token(token), 'BENCH_TOKEN_MISMATCH')
    return value


def base_inputs(inputs):
    return {k: inputs[k] for k in R.INPUT_KEYS}


def expected_pod_spec(token, role, inputs):
    return R.expected_pod_spec(token, role, base_inputs(_inputs(inputs, token)))


def render(token, mode='success', *, api_endpoints=None, inputs):
    inputs = _inputs(inputs, token)
    require(mode == 'success', 'BENCH_MODE_INVALID')
    plan = R.render(token, mode, api_endpoints=api_endpoints, inputs=base_inputs(inputs))
    for obj in plan:
        if obj['kind'] == 'ConfigMap':
            obj['data']['job.py'] = job_script(token, inputs)
    return plan


def validate_saved_plan(plan, token, inputs, api_endpoints=None):
    require(R._same(plan, render(token, inputs=inputs, api_endpoints=api_endpoints)), 'BENCH_PLAN_CHANGED')
    return deepcopy(plan)


def validate_measurements(value, engine, token, inputs, *, partial=False):
    require(engine in ('spark', 'trino') and type(value) is list
            and (0 <= len(value) <= 8 if partial else len(value) == 8), 'BENCH_SAMPLES_INVALID')
    for row, (case_id, sample_index) in zip(value, [(c, i) for c in W.CASES for i in W.SAMPLE_INDICES]):
        require(type(row) is dict and set(row) == {'engine', 'case_id', 'sample_index', 'sql_sha256',
                'elapsed_ns', 'result', 'query_id', 'stats'}, 'BENCH_SAMPLE_INVALID')
        sql = W.query_sql(engine, case_id, token, inputs['initial_snapshot_id'])
        require(row['engine'] == engine and row['case_id'] == case_id
                and type(row['sample_index']) is int and row['sample_index'] == sample_index
                and row['sql_sha256'] == hashlib.sha256(sql.encode()).hexdigest()
                and type(row['elapsed_ns']) is int and 0 < row['elapsed_ns'] <= 180*1_000_000_000,
                'BENCH_TIMING_INVALID')
        require(R._same(normalized(case_id, row['result']), inputs['sealed']['dataset']['expected'][case_id]),
                'BENCH_ORACLE_MISMATCH')
        if engine == 'spark':
            require(row['query_id'] is None and row['stats'] is None, 'BENCH_SPARK_STATS_INVALID')
        else:
            require(type(row['query_id']) is str and re.fullmatch(r'[0-9]{8}_[0-9]{6}_[0-9]{5}_[a-z0-9]+', row['query_id'])
                    and type(row['stats']) is dict, 'BENCH_TRINO_STATS_INVALID')
    return deepcopy(value)


def validate_output(value, token, inputs, *, executor_pod_name):
    inputs = _inputs(inputs, token)
    fixed = {'schema_version': 1, 'run_token': token, 'namespace': N.namespace(token), 'scope': SCOPE,
             'mode': 'success', 'configured_image': inputs['image'], 'workload_id': W.WORKLOAD_ID}
    fields = {'result', 'spark_version', 'iceberg_version', 'master', 'checks', 'measurements',
              'startup_ns', 'partitions', 'sealed_before', 'sealed_after', 'error_code', 'error_classes'}
    require(type(value) is dict and set(value) == set(fixed) | fields, 'BENCH_OUTPUT_FIELDS_INVALID')
    for key, expected in fixed.items():
        require(type(value[key]) is type(expected) and value[key] == expected, 'BENCH_OUTPUT_IDENTITY_INVALID')
    require(value['result'] in ('passed', 'failed'), 'BENCH_OUTPUT_RESULT_INVALID')
    require(type(value['checks']) is dict and set(value['checks']) == set(CHECKS)
            and all(type(v) is bool for v in value['checks'].values()), 'BENCH_CHECKS_INVALID')
    require(type(value['error_classes']) is list and len(value['error_classes']) <= 8
            and all(type(v) is str and len(v) <= 256 and (v in ('unknown', 'SafeFailure')
                or re.fullmatch(R.ERROR_CLASS, v)) for v in value['error_classes']), 'BENCH_ERROR_CLASSES_INVALID')
    for key, expected in [('spark_version', '4.1.3'), ('iceberg_version', '1.11.0'),
                          ('master', 'k8s://https://kubernetes.default.svc:443')]:
        require(value[key] is None or value[key] == expected, 'BENCH_VERSION_INVALID')
    require(value['startup_ns'] is None or type(value['startup_ns']) is int
            and 0 < value['startup_ns'] <= 180*1_000_000_000, 'BENCH_STARTUP_INVALID')
    for key in ('sealed_before', 'sealed_after'):
        require(value[key] is None or R._same(value[key], {k: inputs['sealed'][k] for k in ('identity', 'data_files')}),
                'BENCH_SEAL_CHANGED')
        require(not value['checks'][key] or value[key] is not None, 'BENCH_SEAL_MISSING')
    if value['measurements'] is not None:
        validate_measurements(value['measurements'], 'spark', token, inputs, partial=value['result'] == 'failed')
    require(not value['checks']['measurements'] or value['measurements'] is not None
            and len(value['measurements']) == 8, 'BENCH_MEASUREMENTS_MISSING')
    if value['partitions'] is not None:
        native = N.expected_output(token, 'success')
        native['partitions'] = value['partitions']
        N.validate_output(native, token, 'success', executor_pod_name=executor_pod_name)
    require(not value['checks']['executor'] or value['partitions'] is not None, 'BENCH_EXECUTOR_MISSING')
    if value['result'] == 'passed':
        require(all(value['checks'].values()) and value['error_code'] is None and not value['error_classes']
                and all(value[k] is not None for k in ('spark_version', 'iceberg_version', 'master', 'startup_ns')),
                'BENCH_PASSED_INCOMPLETE')
    else:
        require(value['error_code'] in ERRORS and value['error_classes'], 'BENCH_FAILURE_INVALID')
    return deepcopy(value)


def parse_output(logs, token, inputs, *, executor_pod_name):
    require(type(logs) is str and len(logs.encode()) <= N.MAX_LOG_BYTES, 'BENCH_LOG_BOUND')
    lines = [line[len(RESULT_PREFIX):] for line in logs.splitlines() if line.startswith(RESULT_PREFIX)]
    require(len(lines) == 1 and len(lines[0]) <= 64*1024, 'BENCH_RESULT_LINE_INVALID')
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'BENCH_DUPLICATE_RESULT_KEY'); result[key] = value
        return result
    try:
        value = json.loads(lines[0], object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        return validate_output(value, token, inputs, executor_pod_name=executor_pod_name)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise N.ContractError('BENCH_OUTPUT_INVALID') from None


def safe_summary(value, token, inputs, *, executor_pod_name):
    value = validate_output(value, token, inputs, executor_pod_name=executor_pod_name)
    return {k: deepcopy(value[k]) for k in ('schema_version', 'run_token', 'scope', 'result', 'workload_id',
            'spark_version', 'iceberg_version', 'checks', 'startup_ns', 'error_code', 'error_classes')}


def job_script(token, inputs):
    inputs = _inputs(inputs, token)
    config = {'token': token, 'namespace': N.namespace(token), 'image': inputs['image'], 'table': R.table_name(token),
              'sealed': inputs['sealed'], 'executor': N.executor_name(token), 'scope': SCOPE,
              'prefix': RESULT_PREFIX, 'checks': list(CHECKS), 'secret_dir': R.SECRET_DIR,
              'error_class_pattern': R.ERROR_CLASS}
    # Fixed repository source is part of the rendered plan hash, never caller code.
    workload = Path(W.__file__).read_text()
    helpers = R._JOB.split('def expected_rows():', 1)[0]
    return 'CONFIG = ' + repr(config) + '\nWORKLOAD_SOURCE = ' + repr(workload) + '\n' + helpers + _JOB


_JOB = r'''
import hashlib
import time
import types
W = types.ModuleType('shirokuma_benchmark_workload')
sys.modules[W.__name__] = W
exec(compile(WORKLOAD_SOURCE, '<fixed-benchmark-workload>', 'exec'), W.__dict__)

def observe_partition(partition_id, rows):
    from pyspark import TaskContext
    context = TaskContext.get()
    require(context is not None, 'EXECUTOR_EVIDENCE_INVALID')
    list(rows)
    yield {'partition_id': partition_id, 'stage_id': context.stageId(),
           'task_attempt_id': context.taskAttemptId(), 'attempt_number': context.attemptNumber(),
           'executor_id': os.environ.get('SPARK_EXECUTOR_ID'), 'hostname': socket.gethostname()}

def main():
    output = {'schema_version': 1, 'run_token': CONFIG['token'], 'namespace': CONFIG['namespace'],
        'scope': CONFIG['scope'], 'mode': 'success', 'configured_image': CONFIG['image'],
        'workload_id': W.WORKLOAD_ID, 'result': 'failed', 'spark_version': None, 'iceberg_version': None,
        'master': None, 'checks': {k:False for k in CONFIG['checks']}, 'measurements': None,
        'startup_ns': None, 'partitions': None, 'sealed_before': None, 'sealed_after': None,
        'error_code': None, 'error_classes': []}
    spark, stage = None, 'start'
    def load():
        sessions = spark._jvm.org.apache.spark.sql.SparkSession
        previous = sessions.getActiveSession()
        try:
            sessions.setActiveSession(spark._jsparkSession)
            return spark._jvm.org.apache.iceberg.spark.Spark3Util.loadIcebergTable(spark._jsparkSession, CONFIG['table'])
        finally:
            if previous.isDefined(): sessions.setActiveSession(previous.get())
            else: sessions.clearActiveSession()
    def observe():
        table = load(); table.refresh(); metadata = table.operations().current()
        snapshot = table.currentSnapshot()
        require(snapshot is not None, 'SEALED_IDENTITY_MISMATCH')
        identity = {'table_uuid': str(metadata.uuid()), 'table_location': str(table.location()),
            'marker': str(table.properties().get('comment')), 'snapshot_id': int(snapshot.snapshotId()),
            'parent_snapshot_id': None if snapshot.parentId() is None else int(snapshot.parentId()),
            'metadata_location': str(metadata.metadataFileLocation()), 'operation': str(snapshot.operation())}
        require(identity == CONFIG['sealed']['identity'], 'SEALED_IDENTITY_MISMATCH')
        fields = [(str(f.name()), str(f.type()).replace(' ', '')) for f in java_items(table.schema().columns(), 9, 'SEALED_IDENTITY_MISMATCH')]
        require(fields == [('orderkey','long'),('linenumber','int'),('returnflag','string'),('linestatus','string'),
            ('quantity','decimal(12,2)'),('extendedprice','decimal(15,2)'),('discount','decimal(3,2)'),
            ('tax','decimal(3,2)'),('shipdate','date')] and table.spec().isUnpartitioned()
            and int(metadata.formatVersion()) == 2, 'SEALED_IDENTITY_MISMATCH')
        files = []; tasks = table.newScan().useSnapshot(identity['snapshot_id']).planFiles()
        try:
            for task in java_items(tasks, 32, 'SEALED_IDENTITY_MISMATCH'):
                file = task.file()
                require(int(file.content().id()) == 0 and str(file.format()) == 'PARQUET'
                        and task.deletes().isEmpty(), 'SEALED_IDENTITY_MISMATCH')
                files.append({'path': str(file.location()), 'record_count': int(file.recordCount()),
                              'file_size_in_bytes': int(file.fileSizeInBytes())})
        finally: tasks.close()
        files.sort(key=lambda f:f['path'])
        require(files == CONFIG['sealed']['data_files'], 'SEALED_IDENTITY_MISMATCH')
        return {'identity': identity, 'data_files': files}
    try:
        from pyspark.sql import SparkSession
        started = time.perf_counter_ns()
        spark = SparkSession.builder.appName('shirokuma-spark-benchmark-'+CONFIG['token']).getOrCreate()
        output['startup_ns'] = time.perf_counter_ns() - started
        output['spark_version'], output['master'] = spark.version, spark.sparkContext.master
        output['iceberg_version'] = str(spark._jvm.org.apache.iceberg.IcebergBuild.version())
        require(output['spark_version'] == '4.1.3' and output['iceberg_version'] == '1.11.0'
                and output['master'] == 'k8s://https://kubernetes.default.svc:443'
                and spark.sparkContext.getConf().get('spark.kubernetes.container.image') == CONFIG['image'], 'SPARK_START_FAILED')
        loader = spark._jvm.java.lang.Thread.currentThread().getContextClassLoader()
        for name in ('org.apache.iceberg.spark.SparkCatalog','org.apache.iceberg.rest.RESTCatalog',
                     'org.apache.iceberg.aws.s3.S3FileIO','software.amazon.awssdk.services.s3.S3Client'):
            loader.loadClass(name)
        output['checks']['classes_loaded'] = True
        stage = 'identity'
        bearer = Path(CONFIG['secret_dir']+'/polaris-token').read_text()
        require(0 < len(bearer) <= 16384 and re.fullmatch(r'[A-Za-z0-9._~+/-]+=*',bearer), 'SEALED_IDENTITY_MISMATCH')
        key = 'spark.sql.catalog.iceberg.token'
        try:
            spark.conf.set(key,bearer); load()
        finally:
            spark.conf.unset(key); bearer = None
        output['sealed_before'] = observe(); output['checks']['sealed_before'] = True
        stage = 'measurements'
        samples = []; output['measurements'] = samples
        for case_id in W.CASES:
            for sample_index in W.SAMPLE_INDICES:
                sql = W.query_sql('spark',case_id,CONFIG['token'],CONFIG['sealed']['identity']['snapshot_id'])
                started = time.perf_counter_ns()
                frame = spark.sql(sql); rows = frame.collect()
                elapsed = time.perf_counter_ns() - started
                result = W.normalize_result(case_id,'spark',frame.columns,
                    [f.dataType.simpleString() for f in frame.schema.fields],[list(r) for r in rows])
                require(result == CONFIG['sealed']['dataset']['expected'][case_id], 'MEASUREMENT_FAILED')
                samples.append({'engine':'spark','case_id':case_id,'sample_index':sample_index,
                    'sql_sha256':hashlib.sha256(sql.encode()).hexdigest(),'elapsed_ns':elapsed,
                    'result':result,'query_id':None,'stats':None})
        output['measurements'] = samples; output['checks']['measurements'] = True
        stage = 'executor'
        # Separate bounded task probe: it is excluded from query timings.
        parts = spark.range(2,numPartitions=2).rdd.mapPartitionsWithIndex(observe_partition).collect()
        parts.sort(key=lambda p:p['partition_id'])
        require(len(parts)==2 and [p['partition_id'] for p in parts]==[0,1]
                and len({p['stage_id'] for p in parts})==1 and len({p['task_attempt_id'] for p in parts})==2
                and all(p['executor_id']=='1' and p['hostname']==CONFIG['executor']
                        and p['attempt_number']==0 for p in parts),'EXECUTOR_EVIDENCE_INVALID')
        output['partitions'] = parts; output['checks']['executor'] = True
        stage = 'identity'
        output['sealed_after'] = observe(); output['checks']['sealed_after'] = True
    except BaseException as error:
        output['error_classes'] = error_classes(error)
        output['error_code'] = str(error) if isinstance(error,SafeFailure) else ('INTERRUPTED'
            if not isinstance(error,Exception) else {'start':'SPARK_START_FAILED','identity':'SEALED_IDENTITY_MISMATCH',
            'measurements':'MEASUREMENT_FAILED','executor':'EXECUTOR_EVIDENCE_INVALID'}.get(stage,'INTERNAL_ERROR'))
    finally:
        if spark is not None:
            try: spark.stop()
            except BaseException as error:
                if output['error_code'] is None:
                    output['error_code'],output['error_classes'] = 'SPARK_STOP_FAILED',error_classes(error)
    if output['error_code'] is None and all(output['checks'].values()): output['result']='passed'
    print(CONFIG['prefix']+json.dumps(output,sort_keys=True,separators=(',',':'),allow_nan=False),flush=True)
    return 0 if output['result']=='passed' else 1

if __name__ == '__main__':
    sys.exit(main())
'''
