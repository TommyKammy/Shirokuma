"""Pure contract for an isolated local[1] Spark/Iceberg classpath smoke.

The generated job accepts only one run token. Its complete result belongs in a
private log: table UUID, snapshot ID and metadata path are deliberately omitted
by safe_summary(). HadoopFileIO success does not establish S3, REST catalog,
Kubernetes executor or retained-catalog compatibility. Valid failed receipts are
returned by parse_output; callers must require result=passed AND process exit=0.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re
import textwrap
from urllib.parse import urlsplit

SPARK_VERSION = '4.1.3'
ICEBERG_VERSION = '1.11.0'
JARS = ('iceberg-spark-runtime-4.1_2.13-1.11.0.jar', 'iceberg-aws-bundle-1.11.0.jar')
RESULT_PREFIX = 'SHIROKUMA_SPARK_ICEBERG_RESULT='
SCOPE = 'local-single-process-hadoop-catalog-classpath'
WAREHOUSE = '/tmp/iceberg-warehouse'
MAX_LOG_BYTES = 256 * 1024
ROWS = 32
CHECKS = ('classes_loaded', 'write', 'fresh_dataframe', 'fresh_sql', 'group_oracle', 'metadata', 'drop')
ERRORS = {'INPUT_INVALID', 'WAREHOUSE_NOT_EMPTY', 'CLASSPATH_MISSING', 'ABI_INCOMPATIBLE',
          'VERSION_MISMATCH', 'SPARK_START_FAILED', 'WRITE_FAILED', 'READ_FAILED',
          'ORACLE_MISMATCH', 'METADATA_INVALID', 'CLEANUP_FAILED', 'SPARK_STOP_FAILED',
          'INTERRUPTED', 'INTERNAL_ERROR'}
TOKEN = re.compile(r'[0-9a-f]{32}')
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')


class ContractError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise ContractError(code)


def validate_token(token):
    require(type(token) is str and TOKEN.fullmatch(token), 'INVALID_RUN_TOKEN')
    return token


def table_name(token):
    return 'local.smoke.t_' + validate_token(token)


def spark_arguments(token):
    return ['--master', 'local[1]', '--driver-memory', '512m', '/opt/shirokuma/check.py', validate_token(token)]


def oracle_rows():
    return [[identity, 'row-'+str(identity).zfill(3), identity % 4, (identity+1)*7]
            for identity in range(ROWS)]


def oracle_groups():
    values = []
    for bucket in range(4):
        members = [row for row in oracle_rows() if row[2] == bucket]
        values.append([bucket, len(members), sum(row[3] for row in members)])
    return values


def _same_json(value, expected):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) == json.dumps(
        expected, sort_keys=True, separators=(',', ':'), allow_nan=False)


def validate_output(value, token):
    validate_token(token)
    keys = {'schema_version', 'run_token', 'scope', 'result', 'spark_version', 'iceberg_version', 'master',
            'checks', 'row_count', 'dataframe_rows', 'sql_rows', 'groups', 'table_identity', 'cleanup', 'error_code'}
    require(type(value) is dict and set(value) == keys, 'RESULT_FIELDS_INVALID')
    require(type(value['schema_version']) is int and value['schema_version'] == 1 and
            value['run_token'] == token and value['scope'] == SCOPE and
            value['result'] in ('passed', 'failed'), 'RESULT_IDENTITY_INVALID')
    require(type(value['checks']) is dict and set(value['checks']) == set(CHECKS) and
            all(type(x) is bool for x in value['checks'].values()), 'RESULT_CHECKS_INVALID')
    for key, expected in [('spark_version', SPARK_VERSION), ('iceberg_version', ICEBERG_VERSION), ('master', 'local[1]')]:
        require(value[key] is None or (type(value[key]) is str and len(value[key]) <= 64), 'RESULT_RUNTIME_INVALID')
        if value['result'] == 'passed':
            require(value[key] == expected, 'RESULT_RUNTIME_INVALID')
    for key, expected in [('dataframe_rows', oracle_rows()), ('sql_rows', oracle_rows()), ('groups', oracle_groups())]:
        require(value[key] is None or _same_json(value[key], expected), 'RESULT_ORACLE_MISMATCH')
    require(value['row_count'] is None if value['dataframe_rows'] is None else
            type(value['row_count']) is int and value['row_count'] == ROWS, 'RESULT_COUNT_INVALID')
    for check, field in [('fresh_dataframe', 'dataframe_rows'), ('fresh_sql', 'sql_rows'), ('group_oracle', 'groups')]:
        require(not value['checks'][check] or value[field] is not None, 'RESULT_CHECK_EVIDENCE_MISSING')
    identity = value['table_identity']
    if identity is not None:
        require(type(identity) is dict and set(identity) == {'uuid', 'snapshot_id', 'metadata_location'}, 'RESULT_METADATA_INVALID')
        require(type(identity['uuid']) is str and UUID.fullmatch(identity['uuid']) and
                type(identity['snapshot_id']) is int and 0 < identity['snapshot_id'] < 2**63 and
                type(identity['metadata_location']) is str and len(identity['metadata_location']) <= 256,
                'RESULT_METADATA_INVALID')
        location = urlsplit(identity['metadata_location'])
        require(location.scheme in ('', 'file') and not location.netloc and not location.query and not location.fragment and
                re.fullmatch(re.escape(WAREHOUSE+'/smoke/t_'+token+'/metadata/') + r'v[1-9][0-9]{0,5}\.metadata\.json', location.path),
                'RESULT_METADATA_SCOPE_INVALID')
    require(not value['checks']['metadata'] or identity is not None, 'RESULT_CHECK_EVIDENCE_MISSING')
    cleanup = value['cleanup']
    require(type(cleanup) is dict and set(cleanup) == {'attempted', 'uuid_verified', 'table_absent'} and
            type(cleanup['attempted']) is bool and type(cleanup['uuid_verified']) is bool and
            (cleanup['table_absent'] is None or type(cleanup['table_absent']) is bool), 'RESULT_CLEANUP_INVALID')
    require(not value['checks']['drop'] or cleanup == {'attempted': True, 'uuid_verified': True, 'table_absent': True},
            'RESULT_CLEANUP_INVALID')
    if value['result'] == 'passed':
        require(value['error_code'] is None and all(value['checks'].values()) and value['row_count'] == ROWS,
                'RESULT_SUCCESS_INCOMPLETE')
    else:
        require(type(value['error_code']) is str and value['error_code'] in ERRORS, 'RESULT_FAILURE_CODE_INVALID')
    return deepcopy(value)


def parse_output(logs, token):
    require(type(logs) is str, 'JOB_LOG_BOUND')
    try:
        require(len(logs.encode('utf-8')) <= MAX_LOG_BYTES, 'JOB_LOG_BOUND')
    except UnicodeError:
        raise ContractError('JOB_LOG_ENCODING') from None
    lines = [line[len(RESULT_PREFIX):] for line in logs.splitlines() if line.startswith(RESULT_PREFIX)]
    require(len(lines) == 1 and len(lines[0]) <= 16*1024, 'RESULT_LINE_COUNT')
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'DUPLICATE_RESULT_KEY')
            result[key] = value
        return result
    try:
        value = json.loads(lines[0], object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ContractError('NONFINITE_RESULT')))
        return validate_output(value, token)
    except (ValueError, TypeError, RecursionError):
        raise ContractError('INVALID_RESULT_JSON_OR_CONTRACT') from None


def safe_summary(value):
    """No raw rows, local filesystem paths, table UUIDs or snapshot IDs."""
    value = validate_output(value, value.get('run_token') if type(value) is dict else None)
    return {key: deepcopy(value[key]) for key in ('schema_version', 'run_token', 'scope', 'result',
            'spark_version', 'iceberg_version', 'master', 'checks', 'row_count', 'cleanup', 'error_code')}


def job_script():
    """Generate a reusable image asset. argv[1] is the only input, a strict UUID token."""
    config = {'spark_version': SPARK_VERSION, 'iceberg_version': ICEBERG_VERSION, 'scope': SCOPE,
              'warehouse': WAREHOUSE, 'rows': ROWS, 'checks': list(CHECKS), 'prefix': RESULT_PREFIX}
    return 'CONFIG = ' + repr(config) + '\n' + textwrap.dedent(_JOB)


_JOB = r'''
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

class SafeFailure(Exception):
    pass

def require(condition, code):
    if not condition:
        raise SafeFailure(code)

def expected_rows():
    return [[i, 'row-%03d' % i, i % 4, (i + 1) * 7] for i in range(32)]

def expected_groups():
    buckets = [[i, 0, 0] for i in range(4)]
    for row in expected_rows():
        buckets[row[2]][1] += 1
        buckets[row[2]][2] += row[3]
    return buckets

def classify(error, stage):
    if isinstance(error, SafeFailure):
        return str(error)
    if not isinstance(error, Exception):
        return 'INTERRUPTED'
    text = str(error)
    if isinstance(error, ImportError) or any(name in text for name in ('ClassNotFoundException', 'NoClassDefFoundError')):
        return 'CLASSPATH_MISSING'
    if any(name in text for name in ('NoSuchMethodError', 'AbstractMethodError', 'IncompatibleClassChangeError')):
        return 'ABI_INCOMPATIBLE'
    return {'start': 'SPARK_START_FAILED', 'write': 'WRITE_FAILED', 'read': 'READ_FAILED',
            'metadata': 'METADATA_INVALID'}.get(stage, 'INTERNAL_ERROR')

def local_path(value):
    parsed = urlsplit(str(value))
    require(parsed.scheme in ('', 'file') and not parsed.netloc and not parsed.query and not parsed.fragment
            and '%' not in parsed.path, 'METADATA_INVALID')
    return Path(parsed.path)

def main():
    token = sys.argv[1] if len(sys.argv) == 2 else None
    require(type(token) is str and re.fullmatch(r'[0-9a-f]{32}', token), 'INPUT_INVALID')
    table = 'local.smoke.t_' + token
    table_path = Path(CONFIG['warehouse']) / 'smoke' / ('t_' + token)
    output = {'schema_version': 1, 'run_token': token, 'scope': CONFIG['scope'], 'result': 'failed',
              'spark_version': None, 'iceberg_version': None, 'master': None,
              'checks': {key: False for key in CONFIG['checks']}, 'row_count': None,
              'dataframe_rows': None, 'sql_rows': None, 'groups': None, 'table_identity': None,
              'cleanup': {'attempted': False, 'uuid_verified': False, 'table_absent': None}, 'error_code': None}
    spark, table_uuid, create_attempted, stage = None, None, False, 'start'
    def load_table():
        return spark._jvm.org.apache.iceberg.spark.Spark3Util.loadIcebergTable(spark._jsparkSession, table)
    try:
        warehouse = Path(CONFIG['warehouse'])
        require(not warehouse.exists() and not warehouse.is_symlink(), 'WAREHOUSE_NOT_EMPTY')
        warehouse.mkdir(mode=0o700)
        from pyspark.sql import SparkSession
        spark = (SparkSession.builder.master('local[1]').appName('shirokuma-iceberg-' + token)
            .config('spark.ui.enabled', 'false').config('spark.eventLog.enabled', 'false')
            .config('spark.driver.bindAddress', '127.0.0.1').config('spark.driver.host', '127.0.0.1')
            .config('spark.local.dir', '/tmp/spark-local').config('spark.sql.warehouse.dir', '/tmp/spark-warehouse')
            .config('spark.default.parallelism', '1').config('spark.sql.shuffle.partitions', '1')
            .config('spark.sql.adaptive.enabled', 'false')
            .config('spark.hadoop.fs.defaultFS', 'file:///')
            .config('spark.sql.extensions', 'org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions')
            .config('spark.sql.catalog.local', 'org.apache.iceberg.spark.SparkCatalog')
            .config('spark.sql.catalog.local.type', 'hadoop')
            .config('spark.sql.catalog.local.warehouse', CONFIG['warehouse'])
            .config('spark.sql.catalog.local.io-impl', 'org.apache.iceberg.hadoop.HadoopFileIO')
            .getOrCreate())
        output['spark_version'] = spark.version
        output['master'] = spark.sparkContext.master
        loader = spark._jvm.java.lang.Thread.currentThread().getContextClassLoader()
        for name in ('org.apache.iceberg.spark.SparkCatalog', 'org.apache.iceberg.hadoop.HadoopCatalog',
                     'org.apache.iceberg.aws.s3.S3FileIO', 'software.amazon.awssdk.services.s3.S3Client'):
            loader.loadClass(name)
        output['iceberg_version'] = str(spark._jvm.org.apache.iceberg.IcebergBuild.version())
        require(output['spark_version'] == CONFIG['spark_version'] and output['iceberg_version'] == CONFIG['iceberg_version']
                and output['master'] == 'local[1]', 'VERSION_MISMATCH')
        output['checks']['classes_loaded'] = True
        stage = 'write'
        spark.sql('CREATE NAMESPACE local.smoke')
        create_attempted = True
        spark.sql('CREATE TABLE ' + table + ' (id INT, name STRING, bucket INT, amount BIGINT) USING iceberg '
                  "TBLPROPERTIES ('format-version'='2')")
        created = load_table()
        require(local_path(created.location()) == table_path, 'METADATA_INVALID')
        table_uuid = str(created.operations().current().uuid())
        require(re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', table_uuid), 'METADATA_INVALID')
        spark.sql('INSERT INTO ' + table + " SELECT CAST(id AS INT), concat('row-', lpad(CAST(id AS STRING), 3, '0')), "
                  'CAST(pmod(id, 4) AS INT), CAST((id + 1) * 7 AS BIGINT) FROM range(32)')
        output['checks']['write'] = True
        stage = 'read'
        fresh = spark.newSession()
        fresh.catalog.clearCache()
        dataframe = fresh.read.format('iceberg').load(table).orderBy('id')
        require(dataframe.schema.simpleString() == 'struct<id:int,name:string,bucket:int,amount:bigint>', 'ORACLE_MISMATCH')
        rows = [list(row) for row in dataframe.collect()]
        require(rows == expected_rows(), 'ORACLE_MISMATCH')
        output['dataframe_rows'], output['row_count'] = rows, len(rows)
        output['checks']['fresh_dataframe'] = True
        sql_rows = [list(row) for row in fresh.sql('SELECT id, name, bucket, amount FROM ' + table + ' ORDER BY id').collect()]
        require(sql_rows == expected_rows(), 'ORACLE_MISMATCH')
        output['sql_rows'] = sql_rows
        output['checks']['fresh_sql'] = True
        groups = [list(row) for row in fresh.sql('SELECT bucket, count(*), sum(amount) FROM ' + table +
                                                ' GROUP BY bucket ORDER BY bucket').collect()]
        require(groups == expected_groups(), 'ORACLE_MISMATCH')
        output['groups'] = groups
        output['checks']['group_oracle'] = True
        stage = 'metadata'
        written = load_table()
        written.refresh()
        current = written.operations().current()
        snapshot = written.currentSnapshot()
        require(snapshot is not None and str(current.uuid()) == table_uuid, 'METADATA_INVALID')
        snapshot_id = int(snapshot.snapshotId())
        metadata_location = str(current.metadataFileLocation())
        metadata = local_path(metadata_location)
        require(metadata.parent == table_path / 'metadata' and
                re.fullmatch(r'v[1-9][0-9]{0,5}\.metadata\.json', metadata.name) and
                metadata.is_file() and not metadata.is_symlink() and metadata.stat().st_size <= 1024*1024, 'METADATA_INVALID')
        stored = json.loads(metadata.read_text())
        require(stored.get('table-uuid') == table_uuid and stored.get('current-snapshot-id') == snapshot_id and
                stored.get('format-version') == 2 and local_path(stored.get('location')) == table_path and
                0 < snapshot_id < 2**63, 'METADATA_INVALID')
        output['table_identity'] = {'uuid': table_uuid, 'snapshot_id': snapshot_id, 'metadata_location': metadata_location}
        output['checks']['metadata'] = True
    except BaseException as error:
        output['error_code'] = classify(error, stage)
    finally:
        if spark is not None and create_attempted:
            output['cleanup']['attempted'] = True
            try:
                if spark.catalog.tableExists(table):
                    current = load_table()
                    require(table_uuid is not None and str(current.operations().current().uuid()) == table_uuid and
                            local_path(current.location()) == table_path, 'CLEANUP_FAILED')
                    output['cleanup']['uuid_verified'] = True
                    spark.sql('DROP TABLE ' + table + ' PURGE')
                output['cleanup']['table_absent'] = not spark.catalog.tableExists(table)
                require(output['cleanup']['table_absent'], 'CLEANUP_FAILED')
                output['checks']['drop'] = output['cleanup']['uuid_verified']
            except BaseException:
                if output['error_code'] is None:
                    output['error_code'] = 'CLEANUP_FAILED'
        if spark is not None:
            try:
                spark.stop()
            except BaseException:
                if output['error_code'] is None:
                    output['error_code'] = 'SPARK_STOP_FAILED'
    if output['error_code'] is None and all(output['checks'].values()):
        output['result'] = 'passed'
    if output['result'] != 'passed' and output['error_code'] is None:
        output['error_code'] = 'INTERNAL_ERROR'
    print(CONFIG['prefix'] + json.dumps(output, sort_keys=True, separators=(',', ':'), allow_nan=False), flush=True)
    return 0 if output['result'] == 'passed' else 1

if __name__ == '__main__':
    try:
        code = main()
    except SafeFailure:
        code = 2
    sys.exit(code)
'''
