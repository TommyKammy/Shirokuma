"""Offline stateful catalog/Trino oracle; no runtime, HTTP, Spark or model calls."""
from copy import deepcopy
import json
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.spark_branch import table as T
from tools.local_experiment.spark_iceberg.remote_access import AccessError

TOKEN = 'a' * 32
UUID = '00000000-0000-4000-8000-000000000001'
E, A, B, C = (3831785292040503894, 8088807033386546123, 8990253737475478975, 480567632882700496)


class Catalog:
    def __init__(self, initial=True):
        self.run_token = TOKEN
        self.scope = T.names(TOKEN)
        self.initial, self.exists, self.revision = initial, False, 0
        self.posts, self.reads, self.fail_post, self.after_post = [], [], None, None
        self.value = {'metadata-location': self.scope['location'] + '/metadata/00000.metadata.json',
            'config': {'token': 'never-persist-this-secret'}, 'storage-credentials': ['never-persist-this-secret'],
            'metadata': {'table-uuid': UUID, 'format-version': 2, 'location': self.scope['location'],
                'properties': {'comment': self.scope['marker']}, 'current-schema-id': 0,
                'schemas': [{'type': 'struct', 'schema-id': 0, 'fields': [
                    {'id': i+1, 'name': name, 'type': kind, 'required': False}
                    for i, (name, kind) in enumerate(zip(T.C.COLUMNS, ('int', 'string', 'int', 'long')))]}],
                'partition-specs': [{'spec-id': 0, 'fields': []}], 'default-spec-id': 0,
                'snapshots': [], 'current-snapshot-id': -1, 'refs': {}}}
        self.rows, self.files = {}, {}

    def changed(self):
        self.revision += 1
        self.value['metadata-location'] = self.scope['location'] + f'/metadata/{self.revision:05d}.metadata.json'
        self.value['metadata']['last-updated-ms'] = 10000 + self.revision

    def file(self, name, count):
        return {'path': self.scope['location'] + '/data/' + name + '.parquet',
                'record_count': count, 'file_size_in_bytes': 100 + count}

    def snapshot(self, sid, parent, rows, files):
        sequence = len(self.value['metadata']['snapshots']) + 1
        self.value['metadata']['snapshots'].append({'snapshot-id': sid, 'parent-snapshot-id': parent,
            'sequence-number': sequence, 'timestamp-ms': 10000 + sequence,
            'manifest-list': self.scope['location'] + f'/metadata/snap-{sid}.avro',
            'summary': {'operation': 'append', 'total-records': str(len(rows)), 'total-data-files': str(len(files))}})
        self.rows[sid], self.files[sid] = deepcopy(rows), deepcopy(files)
        self.changed()

    def create(self):
        if self.initial:
            self.snapshot(E, None, [], [])
            self.value['metadata']['current-snapshot-id'] = E
            self.value['metadata']['refs'] = {'main': {'type': 'branch', 'snapshot-id': E}}
        self.exists = True

    def append(self):
        meta = self.value['metadata']
        before = meta['current-snapshot-id']
        if A not in self.rows:
            self.snapshot(A, None if before == -1 else before, T.C.ROWS_A, [self.file('a', 2)])
            after = A
        else:
            self.snapshot(B, A, T.C.ROWS_B, [self.file('a', 2), self.file('b', 1)])
            after = B
        meta['current-snapshot-id'] = after
        meta['refs']['main'] = {'type': 'branch', 'snapshot-id': after}

    def request(self, method, path, body=None):
        assert method == 'GET' and path == self.scope['path'] and body is None
        assert self.exists
        self.reads.append(path)
        return deepcopy(self.value)

    def commit_reference(self, body):
        T.validate_ref_body(body, TOKEN)
        self.posts.append(deepcopy(body))
        if self.fail_post:
            raise self.fail_post
        meta = self.value['metadata']
        assert body['requirements'][0]['uuid'] == meta['table-uuid']
        assert body['requirements'][1]['snapshot-id'] == meta['current-snapshot-id']
        item = body['updates'][0]
        assert item['ref-name'] not in meta['refs']
        meta['refs'][item['ref-name']] = {'type': item['type'], 'snapshot-id': item['snapshot-id']}
        self.changed()
        if self.after_post:
            self.after_post()
        return deepcopy(self.value)

    def spark(self, sealed):
        self.snapshot(C, A, T.C.ROWS_C, [self.file('a', 2), self.file('c', 1)])
        self.value['metadata']['refs']['audit_branch']['snapshot-id'] = C
        current = T.project(self.value, TOKEN)
        return {'result': 'passed', 'write_outcome': 'verified', 'write_attempts': 1,
            'before': {'refs': deepcopy(sealed['refs']), 'snapshot_summaries': T.C.safe_snapshots(sealed),
                       'snapshot_files': deepcopy(sealed['data_files'])},
            'table_identity': {'table_uuid': UUID, 'table_location': self.scope['location'], 'marker': self.scope['marker'],
                'metadata_location': current['metadata_location'], 'main_snapshot_id': B, 'tag_snapshot_id': A,
                'branch_snapshot_id': C, 'parent_snapshot_id': A},
            'snapshot_summaries': [{k:v for k,v in row.items() if k != 'sha256'} for row in current['snapshots']],
            'snapshot_files': {'a': deepcopy(self.files[A]), 'b': deepcopy(self.files[B]), 'c': deepcopy(self.files[C])},
            'dataframe_rows': {'main': deepcopy(T.C.ROWS_B), 'baseline_tag': deepcopy(T.C.ROWS_A), 'audit_branch': deepcopy(T.C.ROWS_C)},
            'sql_rows': {'main': deepcopy(T.C.ROWS_B), 'baseline_tag': deepcopy(T.C.ROWS_A), 'audit_branch': deepcopy(T.C.ROWS_C)}}


class Client:
    def __init__(self, catalog, control=False):
        self.catalog = catalog
        self.user, self.timeout, self.records, self.queries = T.SB.run_user(TOKEN, control), 10, [], []
        self.fail = None
        self.alter_result = None

    def query(self, sql):
        self.queries.append(sql)
        if self.fail and self.fail(sql):
            raise TimeoutError('raw private error must never enter receipt')
        catalog, meta = self.catalog, self.catalog.value['metadata']
        columns, types, rows = [], [], []
        if sql == 'SHOW TABLES FROM iceberg.local_experiment':
            columns, types = ['Table'], ['varchar']
            rows = [[catalog.scope['name']]] if catalog.exists else []
        elif sql.startswith('CREATE TABLE '):
            catalog.create()
        elif sql.startswith('INSERT INTO '):
            catalog.append()
        elif sql == 'DROP TABLE ' + catalog.scope['table']:
            catalog.exists = False
        elif '$files"' in sql:
            columns, types = ['file_path', 'record_count', 'file_size_in_bytes'], ['varchar', 'bigint', 'bigint']
            rows = [[r['path'], r['record_count'], r['file_size_in_bytes']] for r in catalog.files.get(meta['current-snapshot-id'], [])]
        elif '$refs"' in sql:
            columns, types = ['name', 'type', 'snapshot_id'], ['varchar', 'varchar', 'bigint']
            rows = [[name, value['type'].upper(), value['snapshot-id']] for name, value in sorted(meta['refs'].items())]
        elif '$snapshots"' in sql:
            columns, types = ['snapshot_id', 'parent_id', 'operation'], ['bigint', 'bigint', 'varchar']
            rows = [[r['snapshot-id'], r['parent-snapshot-id'], r['summary']['operation']]
                    for r in sorted(meta['snapshots'], key=lambda row: row['snapshot-id'])]
        elif sql.startswith('SELECT id, name, bucket, amount '):
            columns, types = T.C.COLUMNS, T.C.COLUMN_TYPES
            if 'FOR VERSION AS OF' in sql:
                ref = sql.split('FOR VERSION AS OF ')[1].split(' ORDER BY')[0]
                snapshot = meta['refs'][ref.strip("'")]['snapshot-id'] if ref.startswith("'") else int(ref)
            else:
                snapshot = meta['current-snapshot-id']
            rows = catalog.rows.get(snapshot, [])
        else:
            raise AssertionError('unexpected fixed SQL: ' + sql)
        result = {'query_id': f'20260917_010000_{len(self.queries):05d}_abcde',
                  'columns': deepcopy(columns), 'column_types': deepcopy(types), 'rows': deepcopy(rows)}
        if self.alter_result:
            self.alter_result(sql, result)
        return result


class HostTableTests(unittest.TestCase):
    def setUp(self):
        self.catalog = Catalog()
        self.worker, self.control = Client(self.catalog), Client(self.catalog, True)
        self.table = T.HostTable(TOKEN, self.worker, self.control, self.catalog)

    def prepared(self):
        return self.table.prepare()

    def written(self):
        seal = self.prepared()
        self.table.mark_spark_pending()
        output = self.catalog.spark(seal)
        return seal, output

    def test_full_branch_siblings_and_all_named_numeric_rows(self):
        seal, output = self.written()
        self.assertEqual((seal['initial_snapshot_id'], seal['snapshot_a'], seal['snapshot_b']), (E, A, B))
        result = self.table.verify_written(output)
        self.assertTrue(result['verified'])
        self.assertTrue(result['main_files_unchanged'])
        self.assertTrue(result['sibling_parents_verified'])
        self.assertEqual(result['rows']['main'], T.C.ROWS_B)
        self.assertEqual(result['rows']['audit_branch'], T.C.ROWS_C)
        self.assertNotEqual(result['rows']['main'], result['rows']['audit_branch'])
        self.assertEqual(len(self.catalog.posts), 2)
        for name in ('baseline_tag', 'audit_branch'):
            self.assertTrue(any("FOR VERSION AS OF '" + name + "'" in sql for sql in self.worker.queries))
        for snapshot in (A, B, C):
            self.assertTrue(any('FOR VERSION AS OF ' + str(snapshot) in sql for sql in self.worker.queries))
        cleanup = self.table.cleanup(writers_stopped=True, queries_drained=True)
        self.assertTrue(cleanup['verified'])
        self.assertEqual(self.control.queries[0], 'DROP TABLE ' + self.catalog.scope['table'])
        self.assertFalse(self.catalog.exists)
        self.assertNotIn('never-persist-this-secret', json.dumps(self.table.receipt()))

    def test_no_initial_snapshot_is_supported(self):
        self.catalog.initial = False
        seal, output = self.written()
        self.assertIsNone(seal['initial_snapshot_id'])
        self.assertTrue(self.table.verify_written(output)['verified'])

    def test_admin_from_another_run_is_rejected_before_query(self):
        self.catalog.run_token = 'b' * 32
        with self.assertRaisesRegex(T.TableError, 'ADMIN_IDENTITY_OR_COMMIT_SCOPE_DIFFER'):
            T.HostTable(TOKEN, self.worker, self.control, self.catalog)
        self.assertEqual(self.worker.queries, [])

    def test_existing_table_is_not_adopted_or_dropped(self):
        self.catalog.create()
        with self.assertRaisesRegex(T.TableError, 'TABLE_ALREADY_EXISTS'):
            self.prepared()
        self.assertTrue(self.table.cleanup(writers_stopped=True, queries_drained=True)['verified'])
        self.assertFalse(any(sql.startswith(('CREATE', 'DROP')) for sql in self.worker.queries + self.control.queries))
        self.assertTrue(self.catalog.exists)

    def test_unknown_create_defers_cleanup_and_cannot_retry(self):
        self.worker.fail = lambda sql: sql.startswith('CREATE TABLE')
        with self.assertRaises(TimeoutError):
            self.prepared()
        self.assertFalse(self.table.cleanup(writers_stopped=True, queries_drained=True)['verified'])
        with self.assertRaisesRegex(T.TableError, 'PREPARE_RETRY_FORBIDDEN'):
            self.prepared()
        self.assertEqual(len([sql for sql in self.worker.queries if sql.startswith('CREATE')]), 1)
        self.assertEqual(self.control.queries, [])

    def test_create_success_then_persist_failure_keeps_unknown_ownership(self):
        def progress():
            if any(s['label'] == 'create' and s['outcome'] == 'verified' for s in self.table.journal['steps']):
                raise OSError('failed durable result')
        self.table.progress = progress
        with self.assertRaises(OSError):
            self.prepared()
        self.table.progress = lambda: None
        self.assertTrue(self.catalog.exists)
        self.assertFalse(self.table.cleanup(writers_stopped=True, queries_drained=True)['verified'])
        self.assertEqual(self.control.queries, [])

    def test_lost_ref_reply_stays_unknown_even_when_get_shows_commit(self):
        self.catalog.after_post = lambda: (_ for _ in ()).throw(TimeoutError('response lost'))
        with self.assertRaisesRegex(T.TableError, 'REF_COMMIT_UNKNOWN'):
            self.prepared()
        self.assertIn('baseline_tag', self.table.guard()['refs'])
        self.assertEqual(self.table.receipt()['ref_commits'][0]['outcome'], 'unknown')
        self.assertFalse(self.table.cleanup(writers_stopped=True, queries_drained=True)['verified'])
        self.assertEqual(len(self.catalog.posts), 1)
        self.assertEqual(self.control.queries, [])

    def test_typed_ref_conflict_can_cleanup_known_owned_table_without_retry(self):
        self.catalog.fail_post = AccessError('POLARIS_HTTP_FAILED', outcome='rejected', http_status=409)
        with self.assertRaisesRegex(T.TableError, 'REF_COMMIT_REJECTED'):
            self.prepared()
        self.assertEqual(self.table.receipt()['ref_commits'][0]['outcome'], 'rejected')
        self.assertTrue(self.table.cleanup(writers_stopped=True, queries_drained=True)['verified'])
        self.assertEqual(len(self.catalog.posts), 1)

    def test_ref_intent_persistence_failure_sends_no_post(self):
        def progress():
            if self.table.journal['ref_commits'] and self.table.journal['ref_commits'][0]['outcome'] == 'pending':
                raise OSError('intent failure')
        self.table.progress = progress
        with self.assertRaises(OSError):
            self.prepared()
        self.assertEqual(self.catalog.posts, [])
        self.assertFalse(self.table.journal['uncertain'])

    def test_mutating_ref_response_cannot_be_reported_as_metadata_only(self):
        def alter():
            self.catalog.value['metadata']['snapshots'][0]['summary']['total-records'] = '2'
            self.catalog.value['metadata']['snapshots'][0]['summary']['total-data-files'] = '1'
        self.catalog.after_post = alter
        with self.assertRaisesRegex(T.TableError, 'REF_COMMIT_UNKNOWN'):
            self.prepared()
        self.assertTrue(self.table.journal['uncertain'])

    def test_original_ref_cancellation_survives_secondary_save_failure(self):
        cancellation = KeyboardInterrupt()
        self.catalog.after_post = lambda: (_ for _ in ()).throw(cancellation)
        def progress():
            if self.table.journal['ref_commits'] and self.table.journal['ref_commits'][0]['outcome'] == 'unknown':
                raise OSError('secondary failure')
        self.table.progress = progress
        with self.assertRaises(KeyboardInterrupt) as caught:
            self.prepared()
        self.assertIs(caught.exception, cancellation)
        self.assertTrue(self.table.journal['uncertain'])

    def test_spark_pending_survives_unknown_or_not_attempted_output(self):
        self.prepared()
        self.table.mark_spark_pending()
        for outcome in ('unknown', 'returned', 'not-attempted'):
            with self.assertRaisesRegex(T.TableError, 'SPARK_WRITE_UNCONFIRMED'):
                self.table.verify_written({'result': 'failed', 'write_outcome': outcome, 'write_attempts': 0})
        self.assertFalse(self.table.cleanup(writers_stopped=True, queries_drained=True)['verified'])
        self.assertEqual(self.control.queries, [])

    def test_pending_intent_failure_does_not_authorize_native_launch(self):
        self.prepared()
        def progress():
            if self.table.journal['spark_outcome'] == 'unknown':
                raise OSError('intent failure')
        self.table.progress = progress
        with self.assertRaises(OSError):
            self.table.mark_spark_pending()
        self.assertEqual(self.table.journal['spark_outcome'], 'not-started')
        self.assertFalse(self.table.journal['uncertain'])

    def test_wrong_snapshot_parent_is_not_independent_branch(self):
        _, output = self.written()
        self.catalog.value['metadata']['snapshots'][-1]['parent-snapshot-id'] = B
        with self.assertRaisesRegex(T.TableError, 'BRANCH_SIBLING_LINEAGE_INVALID'):
            self.table.verify_written(output)
        self.assertTrue(self.table.journal['uncertain'])

    def test_main_or_tag_movement_is_rejected(self):
        for target in ('main', 'baseline_tag'):
            with self.subTest(target=target):
                self.setUp()
                _, output = self.written()
                self.catalog.value['metadata']['refs'][target]['snapshot-id'] = C
                if target == 'main':
                    self.catalog.value['metadata']['current-snapshot-id'] = C
                with self.assertRaisesRegex(T.TableError, 'MAIN_OR_TAG_CHANGED'):
                    self.table.verify_written(output)

    def test_trino_branch_rows_are_independent_oracle_not_only_spark_json(self):
        _, output = self.written()
        self.catalog.rows[C] = deepcopy(T.C.ROWS_B)
        with self.assertRaisesRegex(T.TableError, 'TRINO_ORACLE_MISMATCH'):
            self.table.verify_written(output)

    def test_spark_before_observation_cannot_be_replaced_with_postwrite_refs(self):
        _, output = self.written()
        output['before']['refs']['audit_branch']['snapshot_id'] = C
        with self.assertRaisesRegex(Exception, 'PREWRITE_MISMATCH'):
            self.table.verify_written(output)

    def test_spark_output_files_must_match_host_original_snapshot_files(self):
        _, output = self.written()
        output['snapshot_files']['a'][0]['file_size_in_bytes'] += 1
        with self.assertRaisesRegex(T.TableError, 'SPARK_FILES_DIFFER'):
            self.table.verify_written(output)

    def test_main_current_files_unchanged_after_branch_append(self):
        _, output = self.written()
        self.catalog.files[B][1]['file_size_in_bytes'] += 1
        with self.assertRaisesRegex(T.TableError, 'MAIN_FILES_CHANGED'):
            self.table.verify_written(output)

    def test_original_snapshot_metadata_hash_must_not_change(self):
        _, output = self.written()
        self.catalog.value['metadata']['snapshots'][0]['timestamp-ms'] += 1
        with self.assertRaisesRegex(T.TableError, 'BRANCH_SIBLING_LINEAGE_INVALID'):
            self.table.verify_written(output)

    def test_uuid_marker_location_or_schema_drift_blocks_drop(self):
        cases = [('table-uuid', '00000000-0000-4000-8000-000000000002'),
                 ('location', 's3://retained/prefix'), ('properties', {'comment': 'wrong'}), ('schemas', [])]
        for key, value in cases:
            with self.subTest(key=key):
                self.setUp()
                self.prepared()
                self.catalog.value['metadata'][key] = value
                with self.assertRaises(T.TableError):
                    self.table.cleanup(writers_stopped=True, queries_drained=True)
                self.assertEqual(self.control.queries, [])

    def test_cleanup_requires_both_writers_and_queries_to_finish(self):
        self.prepared()
        for writers, queries in ((False, True), (True, False), (False, False)):
            with self.assertRaisesRegex(T.TableError, 'NOT_DRAINED'):
                self.table.cleanup(writers_stopped=writers, queries_drained=queries)
        self.assertEqual(self.control.queries, [])

    def test_unknown_drop_is_not_retried(self):
        self.prepared()
        self.control.fail = lambda sql: sql.startswith('DROP TABLE')
        with self.assertRaises(TimeoutError):
            self.table.cleanup(writers_stopped=True, queries_drained=True)
        self.assertFalse(self.table.cleanup(writers_stopped=True, queries_drained=True)['verified'])
        self.assertEqual(len(self.control.queries), 1)

    def test_boolean_cells_and_wrong_sql_types_are_not_numeric_oracle_matches(self):
        self.worker.alter_result = lambda sql, result: result['rows'][0].__setitem__(0, True) if 'ORDER BY id' in sql and result['rows'] else None
        with self.assertRaisesRegex(T.TableError, 'TRINO_ORACLE_MISMATCH'):
            self.prepared()


class PureBoundaryTests(unittest.TestCase):
    def test_branch_admin_only_fixed_reference_commit(self):
        client = object.__new__(T.BranchAdmin)
        client.run_token, client.p, client._exchange = TOKEN, {'table': T.names(TOKEN)['path']}, Mock(return_value={})
        body = {'requirements': [{'type': 'assert-table-uuid', 'uuid': UUID},
                {'type': 'assert-ref-snapshot-id', 'ref': 'main', 'snapshot-id': A},
                {'type': 'assert-ref-snapshot-id', 'ref': 'audit_branch', 'snapshot-id': None}],
                'updates': [{'action': 'set-snapshot-ref', 'ref-name': 'audit_branch', 'type': 'branch', 'snapshot-id': A}]}
        client.commit_reference(body)
        client._exchange.assert_called_once_with('POST', T.names(TOKEN)['path'], body, statuses=(200,))
        for mutation in (lambda b: b['updates'][0].update({'ref-name': 'main'}),
                         lambda b: b['requirements'][2].update({'snapshot-id': B}),
                         lambda b: b['updates'][0].update({'snapshot-id': True})):
            changed = deepcopy(body)
            mutation(changed)
            with self.assertRaises(T.TableError):
                client.commit_reference(changed)
        self.assertEqual(client._exchange.call_count, 1)

    def test_projection_rejects_extra_refs_retention_cycle_and_path_escape(self):
        catalog = Catalog()
        catalog.create()
        mutations = [lambda v: v['metadata']['refs'].update(other={'type': 'branch', 'snapshot-id': E}),
                     lambda v: v['metadata']['refs']['main'].update({'min-snapshots-to-keep': 1}),
                     lambda v: v['metadata']['snapshots'][0].update({'parent-snapshot-id': E}),
                     lambda v: v.update({'metadata-location': catalog.scope['location'] + '/metadata/../else.metadata.json'})]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                value = deepcopy(catalog.value)
                mutation(value)
                with self.assertRaises(T.TableError):
                    T.project(value, TOKEN)

    def test_files_are_bounded_sorted_owned_and_complete(self):
        catalog = Catalog()
        valid = [catalog.file('a', 2), catalog.file('b', 1)]
        self.assertEqual(T.validate_files(valid, catalog.scope['location'], 3), valid)
        for files in (list(reversed(valid)), [valid[0], valid[0]], [],
                      [{**valid[0], 'path': 's3://retained/data/x.parquet'}],
                      [{**valid[0], 'file_size_in_bytes': T.MAX_FILE_BYTES + 1}]):
            with self.subTest(files=files):
                with self.assertRaises(T.TableError):
                    T.validate_files(files, catalog.scope['location'], 3)


if __name__ == '__main__':
    unittest.main()
