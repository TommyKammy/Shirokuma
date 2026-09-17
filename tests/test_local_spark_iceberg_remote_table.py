import copy
import unittest
from unittest.mock import Mock

from tools.local_experiment.spark_iceberg import remote_table as T
from tools.local_experiment.trino_opa.common import ExperimentError

TOKEN = 'a' * 32
UUID = '00000000-0000-4000-8000-000000000001'
SNAPSHOT = 8088807033386546123
INITIAL = 3831785292040503894


def metadata(written=False):
    scope = T.names(TOKEN)
    return {'metadata-location': scope['location'] + '/metadata/00000-test.metadata.json',
            'config': {'credential': 'sentinel-credential-never-project'},
            'metadata': {'table-uuid': UUID, 'format-version': 2, 'location': scope['location'],
                         'properties': {'comment': scope['marker']}, 'current-schema-id': 0,
                         'schemas': [{'type': 'struct', 'schema-id': 0, 'fields': [
                             {'id': i+1, 'name': name, 'type': kind, 'required': False}
                             for i, (name, kind) in enumerate(zip(['id', 'name', 'bucket', 'amount'],
                                                                 ['int', 'string', 'int', 'long']))]}],
                         'partition-specs': [{'spec-id': 0, 'fields': []}], 'default-spec-id': 0,
                         'snapshots': ([{'snapshot-id': SNAPSHOT, 'parent-snapshot-id': None,
                                        'summary': {'operation': 'append', 'total-records': '32',
                                                    'total-data-files': '1'}}] if written else []),
                         'current-snapshot-id': SNAPSHOT if written else -1,
                         'refs': {'main': {'type': 'branch', 'snapshot-id': SNAPSHOT}} if written else {}}}


def initial_metadata(written=False):
    value = metadata(written)
    initial = {'snapshot-id': INITIAL, 'parent-snapshot-id': None,
               'summary': {'operation': 'append', 'total-records': '0', 'total-data-files': '0'}}
    if written:
        value['metadata']['snapshots'][0]['parent-snapshot-id'] = INITIAL
    else:
        value['metadata']['current-snapshot-id'] = INITIAL
        value['metadata']['refs'] = {'main': {'type': 'branch', 'snapshot-id': INITIAL}}
    value['metadata']['snapshots'].insert(0, initial)
    return value


class Client:
    def __init__(self, control=False):
        self.user = T.SB.run_user(TOKEN, control)
        self.records = []
        self.timeout = 10
        self.queries = []
        self.responses = []

    def query(self, sql):
        self.queries.append(sql)
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return {'query_id': 'query_1', 'column_types': [], **value}


class TableTests(unittest.TestCase):
    def setUp(self):
        self.worker, self.control = Client(), Client(True)
        self.admin = Mock()
        self.admin.request.return_value = metadata()
        self.table = T.HostTable(TOKEN, self.worker, self.control, self.admin)

    def create(self):
        self.worker.responses = [{'rows': []}, {'rows': []}, {'columns': ['id', 'name', 'bucket', 'amount'], 'rows': []},
                                 {'columns': ['file_path'], 'rows': []}]
        return self.table.create()

    def test_create_captures_identity_before_allowing_work(self):
        identity = self.create()
        self.assertEqual(identity['table_uuid'], UUID)
        self.assertTrue(self.table.journal['created'])
        self.assertIn("location = 's3://shirokuma-lakehouse/l1/local_experiment/spark_", self.worker.queries[1])
        self.assertNotIn('sentinel', repr(self.table.receipt()))

    def test_unknown_create_is_not_adopted_or_dropped(self):
        self.worker.responses = [{'rows': []}, RuntimeError('response lost')]
        with self.assertRaises(RuntimeError):
            self.table.create()
        with self.assertRaisesRegex(ExperimentError, 'TABLE_CREATE_UNCONFIRMED'):
            self.table.cleanup(writers_stopped=True, queries_drained=True)
        self.assertEqual(self.control.queries, [])
        self.assertFalse(self.table.journal['created'])

    def test_existing_table_does_not_create(self):
        self.worker.responses = [{'rows': [[T.names(TOKEN)['name']]]}]
        with self.assertRaisesRegex(ExperimentError, 'TABLE_ALREADY_EXISTS'):
            self.table.create()
        self.assertFalse(self.table.journal['create_attempted'])

    def test_create_is_never_retried(self):
        self.create()
        with self.assertRaisesRegex(ExperimentError, 'TABLE_CREATE_RETRY_FORBIDDEN'):
            self.table.create()

    def test_changed_uuid_blocks_drop(self):
        self.create()
        changed = metadata()
        changed['metadata']['table-uuid'] = '00000000-0000-4000-8000-000000000002'
        self.admin.request.return_value = changed
        with self.assertRaisesRegex(ExperimentError, 'TABLE_UUID_CHANGED'):
            self.table.cleanup(writers_stopped=True, queries_drained=True)
        self.assertEqual(self.control.queries, [])

    def test_cleanup_requires_both_drains(self):
        self.create()
        for writers, queries in [(False, True), (True, False), (False, False)]:
            with self.assertRaisesRegex(ExperimentError, 'NOT_DRAINED'):
                self.table.cleanup(writers_stopped=writers, queries_drained=queries)
        self.assertEqual(self.control.queries, [])

    def test_successful_cleanup_drops_only_owned_table(self):
        self.create()
        self.control.responses = [{'rows': []}, {'rows': []}]
        result = self.table.cleanup(writers_stopped=True, queries_drained=True)
        self.assertTrue(result['verified'])
        self.assertEqual(self.control.queries[0], 'DROP TABLE ' + T.names(TOKEN)['table'])

    def test_unknown_drop_is_never_retried(self):
        self.create()
        self.control.responses = [RuntimeError('lost')]
        with self.assertRaises(RuntimeError):
            self.table.cleanup(writers_stopped=True, queries_drained=True)
        with self.assertRaisesRegex(ExperimentError, 'TABLE_DROP_RETRY_FORBIDDEN'):
            self.table.cleanup(writers_stopped=True, queries_drained=True)
        self.assertEqual(len(self.control.queries), 1)

    def test_no_create_cleanup_is_safe(self):
        self.assertTrue(self.table.cleanup(writers_stopped=True, queries_drained=True)['verified'])
        self.admin.request.assert_not_called()

    def test_full_trino_oracle_snapshot_and_files(self):
        self.create()
        self.admin.request.return_value = metadata(True)
        self.worker.responses = [
            {'columns': ['id', 'name', 'bucket', 'amount'], 'rows': T.LOCAL.oracle_rows()},
            {'columns': ['bucket', 'n', 'total'], 'rows': T.LOCAL.oracle_groups()},
            {'columns': ['snapshot_id', 'parent_id', 'operation'], 'rows': [[SNAPSHOT, None, 'append']]},
            {'columns': ['file_path', 'record_count', 'file_size_in_bytes'],
             'rows': [[T.names(TOKEN)['location'] + '/data/part-1.parquet', 32, 2048]]}]
        result = self.table.verify_written()
        self.assertEqual(result['identity']['snapshot_id'], SNAPSHOT)
        self.assertEqual(len(result['rows']), 32)

    def test_fresh_table_is_required_before_execution(self):
        self.admin.request.return_value = metadata(True)
        with self.assertRaisesRegex(ExperimentError, 'TABLE_NOT_EMPTY'):
            self.create()
        # CREATE was acknowledged and UUID captured; a later oracle failure
        # must not lose ownership of the disposable table.
        self.assertTrue(self.table.journal['created'])
        self.assertEqual(self.table.journal['identity']['table_uuid'], UUID)

    def test_trino_empty_initial_snapshot_can_be_appended(self):
        self.admin.request.return_value = initial_metadata()
        identity = self.create()
        self.assertEqual(identity['snapshot_id'], INITIAL)
        self.admin.request.return_value = initial_metadata(True)
        self.worker.responses = [
            {'columns': ['id', 'name', 'bucket', 'amount'], 'rows': T.LOCAL.oracle_rows()},
            {'columns': ['bucket', 'n', 'total'], 'rows': T.LOCAL.oracle_groups()},
            {'columns': ['snapshot_id', 'parent_id', 'operation'],
             'rows': [[INITIAL, None, 'append'], [SNAPSHOT, INITIAL, 'append']]},
            {'columns': ['file_path', 'record_count', 'file_size_in_bytes'],
             'rows': [[T.names(TOKEN)['location'] + '/data/part-1.parquet', 32, 2048]]}]
        result = self.table.verify_written()
        self.assertEqual(result['identity']['parent_snapshot_id'], INITIAL)
        self.assertEqual(result['identity']['snapshot_count'], 2)

    def test_empty_rows_with_existing_file_are_rejected(self):
        self.worker.responses = [{'rows': []}, {'rows': []}, {'columns': ['id', 'name', 'bucket', 'amount'], 'rows': []},
                                 {'columns': ['file_path'], 'rows': [['unexpected']]}]
        with self.assertRaisesRegex(ExperimentError, 'ORACLE_MISMATCH'):
            self.table.create()


class ProjectionTests(unittest.TestCase):
    def test_int64_is_exact(self):
        result = T.project(metadata(True), TOKEN)
        self.assertEqual(result['snapshot_id'], SNAPSHOT)
        self.assertNotIn('sentinel', repr(result))

    def test_identity_and_schema_tampering(self):
        for key, new, error in [('location', 's3://retained/table', 'LOCATION'),
                                ('properties', {'comment': 'other-owner'}, 'OWNER'),
                                ('schemas', [], 'SCHEMA'),
                                ('partition-specs', [], 'PARTITION')]:
            with self.subTest(key=key):
                value = metadata()
                value['metadata'][key] = new
                with self.assertRaisesRegex(ExperimentError, error):
                    T.project(value, TOKEN)

    def test_metadata_path_escape_rejected(self):
        value = metadata()
        value['metadata-location'] = T.names(TOKEN)['location'] + '/metadata/../retained.metadata.json'
        with self.assertRaisesRegex(ExperimentError, 'METADATA_LOCATION'):
            T.project(value, TOKEN)

    def test_unexpected_second_snapshot_rejected(self):
        value = metadata(True)
        value['metadata']['snapshots'] *= 3
        with self.assertRaisesRegex(ExperimentError, 'UNEXPECTED_SNAPSHOT_COUNT'):
            T.project(value, TOKEN)

    def test_wrong_token_and_boolean_oracle_rejected(self):
        with self.assertRaises(ExperimentError):
            T.names('../retained')
        with self.assertRaisesRegex(ExperimentError, 'TYPE_MISMATCH'):
            T.rows({'columns': ['n'], 'rows': [[True]]}, ['n'], [[1]])

    def test_initial_snapshot_chain_is_fixed(self):
        value = initial_metadata(True)
        value['metadata']['snapshots'][1]['parent-snapshot-id'] = INITIAL + 1
        with self.assertRaisesRegex(ExperimentError, 'CHAIN_INVALID'):
            T.project(value, TOKEN)

    def test_initial_snapshot_counts_must_be_zero(self):
        value = initial_metadata()
        value['metadata']['snapshots'][0]['summary']['total-data-files'] = '1'
        with self.assertRaisesRegex(ExperimentError, 'COUNTS_INVALID'):
            T.project(value, TOKEN)

    def test_boolean_snapshot_id_is_not_an_integer_identity(self):
        value = metadata(True)
        value['metadata']['current-snapshot-id'] = True
        value['metadata']['snapshots'][0]['snapshot-id'] = 1
        value['metadata']['refs']['main']['snapshot-id'] = 1
        with self.assertRaisesRegex(ExperimentError, 'SNAPSHOT_INVALID'):
            T.project(value, TOKEN)


if __name__ == '__main__':
    unittest.main()
