"""Pure host contract and generated-job tests; Spark/filesystem are strict fakes."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import PurePosixPath
import sys
from types import SimpleNamespace as NS, ModuleType
import unittest
from unittest.mock import patch

from tools.local_experiment.spark_iceberg import contract as C

TOKEN = 'a'*32
TABLE_UUID = '12345678-1234-4321-8123-123456789abc'
METADATA = C.WAREHOUSE+'/smoke/t_'+TOKEN+'/metadata/v2.metadata.json'


def passed():
    return {'schema_version': 1, 'run_token': TOKEN, 'scope': C.SCOPE, 'result': 'passed',
        'spark_version': C.SPARK_VERSION, 'iceberg_version': C.ICEBERG_VERSION, 'master': 'local[1]',
        'checks': {key: True for key in C.CHECKS}, 'row_count': 32,
        'dataframe_rows': C.oracle_rows(), 'sql_rows': C.oracle_rows(), 'groups': C.oracle_groups(),
        'table_identity': {'uuid': TABLE_UUID, 'snapshot_id': 123, 'metadata_location': METADATA},
        'cleanup': {'attempted': True, 'uuid_verified': True, 'table_absent': True}, 'error_code': None}


class HostContractTests(unittest.TestCase):
    def test_independent_oracle_has_exact_32_rows_and_four_groups(self):
        rows = C.oracle_rows()
        self.assertEqual(len(rows), 32)
        self.assertEqual(rows[0], [0, 'row-000', 0, 7])
        self.assertEqual(rows[31], [31, 'row-031', 3, 224])
        self.assertEqual(C.oracle_groups(), [[0, 8, 840], [1, 8, 896], [2, 8, 952], [3, 8, 1008]])
        self.assertEqual(sum(row[2] for row in C.oracle_groups()), sum(range(1, 33))*7)

    def test_reusable_script_and_fixed_only_runtime_arguments(self):
        script = C.job_script()
        compile(script, '<generated>', 'exec')
        self.assertNotIn(TOKEN, script)
        self.assertEqual(C.spark_arguments(TOKEN), ['--master', 'local[1]', '--driver-memory', '512m',
                                                   '/opt/shirokuma/check.py', TOKEN])
        self.assertEqual(C.table_name(TOKEN), 'local.smoke.t_'+TOKEN)
        for token in ('A'*32, 'a'*31, '../table', TOKEN+';DROP', 1, True, None):
            with self.subTest(token=token), self.assertRaises(C.ContractError):
                C.spark_arguments(token)

    def test_full_success_is_private_and_safe_summary_removes_data_and_metadata(self):
        value = C.parse_output('log\n'+C.RESULT_PREFIX+json.dumps(passed()), TOKEN)
        safe = C.safe_summary(value)
        self.assertEqual(safe['result'], 'passed')
        self.assertEqual(safe['row_count'], 32)
        for key in ('table_identity', 'dataframe_rows', 'sql_rows', 'groups'):
            self.assertNotIn(key, safe)
        text = json.dumps(safe)
        self.assertNotIn(TABLE_UUID, text)
        self.assertNotIn('/tmp/', text)
        self.assertNotIn('snapshot', text)

    def test_failed_receipt_preserves_unobserved_null_instead_of_zero(self):
        value = passed()
        value.update(result='failed', error_code='CLASSPATH_MISSING', spark_version=None, iceberg_version=None,
                     master=None, row_count=None, dataframe_rows=None, sql_rows=None, groups=None, table_identity=None,
                     checks={key: False for key in C.CHECKS},
                     cleanup={'attempted': False, 'uuid_verified': False, 'table_absent': None})
        self.assertEqual(C.parse_output(C.RESULT_PREFIX+json.dumps(value), TOKEN), value)
        self.assertIsNone(C.safe_summary(value)['row_count'])
        value['row_count'] = 0
        with self.assertRaises(C.ContractError):
            C.validate_output(value, TOKEN)

    def test_success_cannot_omit_read_or_cleanup(self):
        for key in C.CHECKS:
            value = passed(); value['checks'][key] = False
            with self.subTest(key=key), self.assertRaises(C.ContractError):
                C.validate_output(value, TOKEN)
        value = passed(); value['cleanup']['uuid_verified'] = False
        with self.assertRaises(C.ContractError):
            C.validate_output(value, TOKEN)

    def test_wrong_or_reordered_values_and_bool_number_substitutions_rejected(self):
        for field in ('dataframe_rows', 'sql_rows', 'groups'):
            for replacement in (True, 0.0, -1):
                value = passed(); value[field][0][0] = replacement
                with self.subTest(field=field, replacement=replacement), self.assertRaises(C.ContractError):
                    C.validate_output(value, TOKEN)
        value = passed(); value['sql_rows'].reverse()
        with self.assertRaises(C.ContractError):
            C.validate_output(value, TOKEN)

    def test_metadata_uuid_snapshot_and_exact_owned_path_are_required(self):
        for key, bad in [('uuid', TOKEN), ('snapshot_id', True), ('snapshot_id', 0), ('snapshot_id', 2**63),
                         ('metadata_location', 'https://example.invalid/metadata/v2.metadata.json'),
                         ('metadata_location', C.WAREHOUSE+'/smoke/t_'+'b'*32+'/metadata/v2.metadata.json'),
                         ('metadata_location', METADATA+'/../foreign'),
                         ('metadata_location', 'file://host'+METADATA),
                         ('metadata_location', METADATA+'?token=secret')]:
            value = passed(); value['table_identity'][key] = bad
            with self.subTest(key=key, bad=bad), self.assertRaises(C.ContractError):
                C.validate_output(value, TOKEN)
        value = passed(); value['table_identity']['metadata_location'] = 'file:'+METADATA
        self.assertEqual(C.validate_output(value, TOKEN), value)

    def test_version_scope_unknownfields_and_failure_text_cannot_be_forged(self):
        for key, value in [('spark_version', '4.0.0'), ('iceberg_version', '1.10.2'), ('master', 'k8s://server'),
                           ('scope', 'production'), ('run_token', 'b'*32), ('schema_version', True), ('raw_sql', 'secret')]:
            altered = passed(); altered[key] = value
            with self.subTest(key=key), self.assertRaises(C.ContractError):
                C.validate_output(altered, TOKEN)
        altered = passed(); altered.update(result='failed', error_code='raw exception text with credentials')
        with self.assertRaises(C.ContractError):
            C.validate_output(altered, TOKEN)

    def test_bounded_strict_json_rejects_duplicate_nonfinite_and_multiple_result_lines(self):
        good = C.RESULT_PREFIX+json.dumps(passed())
        bad = ['x'*(C.MAX_LOG_BYTES+1), good+'\n'+good, C.RESULT_PREFIX+'{"run_token":"a","run_token":"b"}',
               C.RESULT_PREFIX+'{"n":NaN}', C.RESULT_PREFIX+'[]', C.RESULT_PREFIX+'{}{}', '\ud800']
        for log in bad:
            with self.subTest(size=len(log)), self.assertRaises(C.ContractError):
                C.parse_output(log, TOKEN)


class FakePath:
    made = []
    metadata = {}
    existing = False

    def __init__(self, path):
        self.path = PurePosixPath(path)

    def __truediv__(self, value):
        return FakePath(self.path / value)

    def __eq__(self, other):
        return isinstance(other, FakePath) and self.path == other.path

    def __str__(self):
        return str(self.path)

    @property
    def parent(self):
        return FakePath(self.path.parent)

    @property
    def name(self):
        return self.path.name

    def exists(self):
        return self.existing

    def is_symlink(self):
        return False

    def mkdir(self, **kwargs):
        self.made.append((str(self.path), kwargs))

    def is_file(self):
        return str(self.path) == METADATA

    def stat(self):
        return NS(st_size=500)

    def read_text(self):
        assert str(self.path) == METADATA
        return json.dumps(self.metadata)


class Frame:
    schema = NS(simpleString=lambda: 'struct<id:int,name:string,bucket:int,amount:bigint>')

    def __init__(self, rows, failure=None):
        self.rows, self.failure = deepcopy(rows), failure

    def orderBy(self, name):
        assert name == 'id'
        return self

    def collect(self):
        if self.failure:
            raise self.failure
        return self.rows


class FakeSpark:
    def __init__(self):
        self.version = C.SPARK_VERSION
        self.sparkContext = NS(master='local[1]')
        self.exists, self.stopped, self.cache_cleared, self.fresh_sessions = False, False, False, 0
        self.statements, self.loaded, self.settings = [], [], {}
        self.read_failure, self.drop_failure, self.stop_failure = None, None, None
        metadata = NS(uuid=lambda: TABLE_UUID, metadataFileLocation=lambda: METADATA)
        table = NS(location=lambda: C.WAREHOUSE+'/smoke/t_'+TOKEN,
            operations=lambda: NS(current=lambda: metadata), refresh=lambda: None,
            currentSnapshot=lambda: NS(snapshotId=lambda: 123))
        self._jvm = NS(java=NS(lang=NS(Thread=NS(currentThread=lambda: NS(
            getContextClassLoader=lambda: NS(loadClass=lambda name: self.loaded.append(name)))))),
            org=NS(apache=NS(iceberg=NS(IcebergBuild=NS(version=lambda: C.ICEBERG_VERSION),
                spark=NS(Spark3Util=NS(loadIcebergTable=lambda _, name: table))))))
        self._jsparkSession = object()
        self.catalog = NS(tableExists=lambda _: self.exists, clearCache=self.clear_cache)
        self.read = NS(format=lambda name: NS(load=lambda _: Frame(C.oracle_rows(), self.read_failure)))

    def master(self, value):
        self.settings['master'] = value
        return self

    def appName(self, value):
        self.settings['appName'] = value
        return self

    def config(self, name, value):
        self.settings[name] = value
        return self

    def getOrCreate(self):
        return self

    def sql(self, statement):
        self.statements.append(statement)
        if statement.startswith('CREATE TABLE'):
            self.exists = True
        if statement.startswith('DROP TABLE'):
            if self.drop_failure:
                raise self.drop_failure
            self.exists = False
        return Frame(C.oracle_groups() if statement.startswith('SELECT bucket') else C.oracle_rows())

    def newSession(self):
        self.fresh_sessions += 1
        return self

    def clear_cache(self):
        self.cache_cleared = True

    def stop(self):
        self.stopped = True
        if self.stop_failure:
            raise self.stop_failure


class GeneratedJobTests(unittest.TestCase):
    def setUp(self):
        FakePath.made, FakePath.existing = [], False
        FakePath.metadata = {'table-uuid': TABLE_UUID, 'current-snapshot-id': 123, 'format-version': 2,
                             'location': C.WAREHOUSE+'/smoke/t_'+TOKEN}

    def execute(self, spark):
        namespace = {'__name__': 'test_job'}
        exec(compile(C.job_script(), '<generated-job>', 'exec'), namespace)
        namespace['Path'] = FakePath
        sql_module = ModuleType('pyspark.sql'); sql_module.SparkSession = NS(builder=spark)
        output = io.StringIO()
        with patch.dict(sys.modules, {'pyspark': ModuleType('pyspark'), 'pyspark.sql': sql_module}), \
                patch.object(sys, 'argv', ['check.py', TOKEN]), redirect_stdout(output):
            code = namespace['main']()
        return code, C.parse_output(output.getvalue(), TOKEN), output.getvalue()

    def test_full_job_reads_new_session_and_uuid_guarded_drop_before_pass(self):
        spark = FakeSpark()
        code, output, _ = self.execute(spark)
        self.assertEqual(code, 0)
        self.assertEqual(output, passed())
        self.assertTrue(spark.stopped and spark.cache_cleared)
        self.assertEqual(spark.fresh_sessions, 1)
        self.assertEqual(spark.settings['master'], 'local[1]')
        self.assertEqual(spark.settings['spark.sql.catalog.local.type'], 'hadoop')
        self.assertEqual(spark.settings['spark.sql.catalog.local.io-impl'], 'org.apache.iceberg.hadoop.HadoopFileIO')
        self.assertEqual(spark.settings['spark.driver.host'], '127.0.0.1')
        self.assertEqual(spark.statements[-1], 'DROP TABLE '+C.table_name(TOKEN)+' PURGE')
        self.assertEqual(FakePath.made, [(C.WAREHOUSE, {'mode': 0o700})])
        self.assertIn('software.amazon.awssdk.services.s3.S3Client', spark.loaded)

    def test_reported_flink_classpath_read_failure_is_failure_after_write_and_cleanup(self):
        spark = FakeSpark()
        spark.read_failure = RuntimeError('NoClassDefFoundError: org/apache/flink/table/data/RowData secret-text')
        code, output, log = self.execute(spark)
        self.assertEqual(code, 1)
        self.assertEqual(output['result'], 'failed')
        self.assertEqual(output['error_code'], 'CLASSPATH_MISSING')
        self.assertTrue(output['checks']['write'] and output['checks']['drop'])
        self.assertFalse(output['checks']['fresh_dataframe'])
        self.assertIsNone(output['row_count'])
        self.assertNotIn('secret-text', log)
        self.assertTrue(spark.stopped)

    def test_abi_error_is_distinct_and_does_not_pass_read(self):
        spark = FakeSpark(); spark.read_failure = RuntimeError('NoSuchMethodError: secret-detail')
        code, output, log = self.execute(spark)
        self.assertEqual((code, output['error_code']), (1, 'ABI_INCOMPATIBLE'))
        self.assertNotIn('secret-detail', log)

    def test_drop_failure_is_not_a_successful_roundtrip(self):
        spark = FakeSpark(); spark.drop_failure = RuntimeError('private failure')
        code, output, log = self.execute(spark)
        self.assertEqual((code, output['result'], output['error_code']), (1, 'failed', 'CLEANUP_FAILED'))
        self.assertFalse(output['checks']['drop'])
        self.assertIsNone(output['cleanup']['table_absent'])
        self.assertNotIn('private failure', log)
        self.assertTrue(spark.stopped)

    def test_stop_failure_keeps_failed_result_after_valid_drop(self):
        spark = FakeSpark(); spark.stop_failure = RuntimeError('private failure')
        code, output, _ = self.execute(spark)
        self.assertEqual((code, output['result'], output['error_code']), (1, 'failed', 'SPARK_STOP_FAILED'))

    def test_existing_warehouse_is_not_touched(self):
        FakePath.existing = True
        spark = FakeSpark()
        code, output, _ = self.execute(spark)
        self.assertEqual((code, output['error_code']), (1, 'WAREHOUSE_NOT_EMPTY'))
        self.assertFalse(FakePath.made or spark.statements or spark.stopped)

    def test_disk_metadata_change_refuses_success(self):
        FakePath.metadata['current-snapshot-id'] = 999
        spark = FakeSpark()
        code, output, _ = self.execute(spark)
        self.assertEqual((code, output['error_code']), (1, 'METADATA_INVALID'))
        self.assertTrue(output['checks']['drop'])
        self.assertFalse(output['checks']['metadata'])


if __name__ == '__main__':
    unittest.main()
