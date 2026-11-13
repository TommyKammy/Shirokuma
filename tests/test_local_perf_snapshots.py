"""Public profiles plus ordinary durable SQL metadata observations."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import urllib.parse

from benchmarks.local_perf.capacity import Campaign, write_json
from benchmarks.local_perf.results import Writer
from benchmarks.local_perf.snapshots import freeze_snapshots, verify_snapshots, table_manifest
from test_local_perf_ingest import CTX, REF, FakePublic


class SnapshotPublic(FakePublic):
    def __init__(self, case='ok'):
        super().__init__(case)
        self.profile_posts = self.sql_posts = 0
        self.source = dict(ref=REF, table_uuid='b69a1029-a887-4675-8159-8bc142a7ef21', snapshot='123',
                           schema_id=1, current_schema_id=2, schema_fingerprint='a' * 64,
                           current_schema_fingerprint='b' * 64, columns=[dict(id=1, name='id', type='long')])

    def request(self, method, url, deadline, body=None):
        p = urllib.parse.urlsplit(url)
        if p.path.endswith('/table-profiles'):
            self.profile_posts += 1
            self.expected_save()
            self.assert_body = json.loads(body)
            if self.case == 'lost-profile':
                raise OSError('response lost')
            source = copy.deepcopy(self.source)
            if self.case == 'profile-ref':
                source['ref'] = dict(REF, incarnation=2)
            if self.case == 'profile-schema':
                source['columns'][0]['type'] = 'string'
            if self.case == 'profile-count':
                count = '3'
            else:
                count = '2'
            self.profile_op = dict(id='profile1', state='FINISHED', committed=False,
                                   result=dict(source=source, row_count=count), cleanup_error=None)
            return dict(context=CTX, operation=self.profile_op)
        if '/dataops/profile1' in p.path:
            return dict(context=CTX, operation=self.profile_op)
        if p.path.endswith('/by-key'):
            raise ValueError('table-profile route currently unavailable')
        if p.path.endswith('/objects/opaque-registry-id'):
            return dict(ref=REF, detail='columns', object=dict(physical_name='p_events'),
                        columns=[dict(name='id', type='long', nullable=True)])
        if p.path.endswith('/resolve') and self.case == 'ref-drift':
            return dict(ref=dict(REF, incarnation=2), object=dict(physical_name='p_events', kind='TABLE'))
        return super().request(method, url, deadline, body)

    def execute(self, sql, out, metadata, deadline, save, check):
        self.sql_posts += 1
        # The normal runner must freeze intent and SQL before metadata is sent.
        intent = json.loads((out.parent / 'attempt.json').read_text())
        assert intent['sql'] == sql and intent['status'] == 'INTENT'
        save(dict(id='metadata' + str(self.sql_posts), backend_query_id='trino' + str(self.sql_posts)))
        if '$refs' in sql:
            columns = [dict(name='snapshot_id', type='varchar')]
            rows = [['124' if self.case == 'snapshot-drift' else '123']]
            if self.case == 'multiple-refs':
                rows *= 2
        else:
            columns = [dict(name=n, type=t) for n, t in [('committed_at', 'varchar'), ('metadata_file', 'varchar'),
                       ('snapshot_id', 'varchar'), ('schema_id', 'bigint'), ('sequence_number', 'bigint')]]
            rows = [['2026-11-14T12:00:00.000Z', 's3://warehouse/v2.json', '123', 1, 2],
                    ['2026-11-14T11:00:00.000+00:00', 's3://warehouse/v1.json', '100', 1, 1]]
            assert 'CAST(to_iso8601(timestamp) AS VARCHAR) AS committed_at' in sql
            if self.case == 'historical-empty':
                rows.append(['2026-11-14T10:00:00Z', 's3://warehouse/v0.json', None, None, None])
            elif self.case.startswith('offsets'):
                rows[0][0] = '2026-11-14T12:00:00+00:00'
                rows[1][0] = '2026-11-14T13:00:00+03:00'  # Earlier instant despite larger wall clock.
                if self.case == 'offsets-reversed':
                    rows.reverse()
            elif self.case == 'head-null':
                rows[0][3] = None
            if self.case == 'ambiguous':
                rows[1][0] = rows[0][0]
                rows.reverse()  # ORDER BY timestamp DESC, file
            elif self.case == 'log-drift':
                rows[1][1] = 's3://warehouse/other.json'
            elif self.case == 'current-schema':
                rows[0][3] = 99
        w = Writer(out, columns, metadata)
        for row in rows:
            check()
            w.row(row)
        w.finish()


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.c = Campaign.create(Path(self.tmp.name).resolve() / 'campaign')
        root = self.c.own('loaded', 'public-load')
        self.a = SnapshotPublic()
        write_json(root / 'load.json', dict(format=1, status='SUCCESS', source_dataset_id='source', context=CTX,
            adapter_identity=self.a.identity(), tables=[dict(name='events', physical_name='p_events', ref=REF,
                rows=2, committed_rows=2, cleanup_clean=True, schema=[dict(name='id', type='bigint')])]))
        self.c.mark(root, 'SUCCESS')
        self.load = root / 'load.json'

    def tearDown(self):
        self.tmp.cleanup()

    def test_freeze_real_uuid_distinct_schema_and_verify_preserves_original(self):
        def saved():
            plan = json.loads((self.c.root / 'pins/snapshots.json').read_text())['operations'][-1]
            self.assertEqual(plan['status'], 'INTENT')
            self.assertIn('body_sha256', plan)
            self.assertIn('key', plan)
        self.a.expected_save = saved
        path = freeze_snapshots(self.c, self.load, 'pins', self.a)
        original = path.read_bytes()
        pins = json.loads(original)
        table = pins['tables'][0]
        self.assertEqual(table['table_uuid'], self.a.source['table_uuid'])
        self.assertNotEqual(table['table_uuid'], table['ref']['object_id'])
        self.assertEqual(table['source_schema_id'], 1)
        self.assertEqual(table['current_schema_id'], 2)
        self.assertEqual(len(table['metadata_log']), 2)
        self.assertEqual(table['metadata_location'], 's3://warehouse/v2.json')
        evidence = verify_snapshots(self.c, path, self.a)
        self.assertEqual(json.loads(evidence.read_text())['status'], 'SUCCESS')
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.a.profile_posts, 1)
        self.assertEqual(self.a.sql_posts, 4)
        self.assertEqual(self.a.assert_body, dict(context=CTX, source=REF, snapshot_id=None))
        self.assertNotIn('never-log-password', original.decode())

    def test_profile_metadata_and_context_mismatch(self):
        for case in ('profile-ref', 'profile-schema', 'profile-count', 'snapshot-drift', 'multiple-refs',
                     'ambiguous', 'offsets-reversed', 'head-null', 'current-schema', 'context', 'ref-drift', 'lost-profile'):
            with self.subTest(case=case):
                a = SnapshotPublic(case)
                with self.assertRaises(Exception):
                    freeze_snapshots(self.c, self.load, 'pins-' + case, a)
                record = json.loads((self.c.root / ('pins-' + case) / 'snapshots.json').read_text())
                self.assertNotEqual(record['status'], 'SUCCESS')
                if case == 'lost-profile':
                    self.assertEqual(record['status'], 'UNKNOWN')
                    self.assertEqual(a.profile_posts, 1)

    def test_drift_checks_complete_log_ref_and_head_without_reprofiling(self):
        pins = freeze_snapshots(self.c, self.load, 'pins', self.a)
        original = pins.read_bytes()
        for case in ('log-drift', 'ref-drift', 'snapshot-drift', 'context'):
            a = SnapshotPublic(case)
            with self.subTest(case=case), self.assertRaises(ValueError):
                verify_snapshots(self.c, pins, a)
            self.assertEqual(a.profile_posts, 0)
            self.assertEqual(pins.read_bytes(), original)


    def test_iso_z_offsets_and_nullable_initial_metadata(self):
        for case in ('offsets', 'historical-empty'):
            pins = freeze_snapshots(self.c, self.load, 'pins-' + case, SnapshotPublic(case))
            table = json.loads(pins.read_text())['tables'][0]
            self.assertEqual(table['snapshot_schema_id'], 1)
            self.assertEqual(table['current_schema_id'], 2)
            self.assertEqual(table['metadata_snapshot_schema_id'], 1)
            self.assertEqual(table['metadata_log'][0]['committed_at'], '2026-11-14T12:00:00.000000+00:00')
            if case == 'historical-empty':
                self.assertIsNone(table['metadata_log'][-1]['schema_id'])
                self.assertIsNone(table['metadata_log'][-1]['sequence_number'])
                self.assertIsNone(table['metadata_log'][-1]['snapshot_id'])
            else:
                self.assertEqual(table['metadata_log'][1]['committed_at'], '2026-11-14T10:00:00.000000+00:00')

    def test_generic_full_table_manifest_quoted_names_and_pair_identity(self):
        from benchmarks.local_perf.runner import result_metadata, validate_sql_contract
        from benchmarks.local_perf.public import context
        pins_path = freeze_snapshots(self.c, self.load, 'pins', self.a)
        pins = json.loads(pins_path.read_text())
        t = pins['tables'][0]
        t['name'] = 'Sales / 日本語'
        t['physical_name'] = 'pilot_sales'
        t['columns'] = [dict(id=1, name='quote"col', type='long'), dict(id=2, name='balance', type='decimal(18,2)')]
        m = table_manifest(pins)
        q = m['queries'][0]
        self.assertEqual(q['id'], 'table_Sales / 日本語')
        self.assertIn('CAST("quote""col" AS BIGINT) AS "quote""col"', q['sql'])
        self.assertIn('FROM "iceberg"."raw"."pilot_sales" FOR VERSION AS OF 123', q['sql'])
        self.assertIn('FROM "Sales / 日本語"', q['reference_sql'])
        self.assertEqual(q['logical_sql'], q['reference_sql'])
        self.assertFalse(q['ordered'])
        validate_sql_contract(m, q)
        metadata = result_metadata(m, q)
        self.assertNotIn('sql_sha256', metadata)
        self.assertEqual(metadata['reference_sql_sha256'], q['reference_sha256'])
        self.assertEqual(metadata['target_sql_sha256'], q['sql_sha256'])
        self.assertEqual(metadata['logical_sql_sha256'], q['logical_sha256'])
        self.assertEqual(table_manifest(pins), m)
        # Same stable pins hash contract as the SQL binding, with canonical table order.
        import hashlib
        expected = hashlib.sha256(json.dumps(m['snapshot_pins'], sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(m['snapshot_set_sha256'], expected)
        from benchmarks.local_perf.__main__ import main
        from unittest.mock import patch
        from contextlib import redirect_stdout, redirect_stderr
        import io
        input_path = self.c.root.parent / 'input-pins.json'
        input_path.write_text(json.dumps(pins))
        output = self.c.root.parent / 'table-manifest.json'
        argv = ['local_perf', 'table-manifest', str(input_path), str(output)]
        with patch('sys.argv', argv), redirect_stdout(io.StringIO()):
            main()
        self.assertEqual(json.loads(output.read_text()), m)
        with patch('sys.argv', argv), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main()

    def test_full_table_manifest_rejects_invalid_pins_and_duplicate_identities(self):
        pins = json.loads(freeze_snapshots(self.c, self.load, 'pins', self.a).read_text())
        for field, value in [('snapshot_id', '123; DROP TABLE x'), ('snapshot_id', -1),
                             ('table_uuid', 'opaque-surrogate'), ('table_uuid', '00000000-0000-0000-0000-000000000000'),
                             ('ref', dict(REF, schema_id='wrong')), ('columns', [dict(name='x', type='map')])]:
            bad = copy.deepcopy(pins)
            bad['tables'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                table_manifest(bad)
        for field in ('name', 'physical_name', 'ref', 'table_uuid'):
            bad = copy.deepcopy(pins)
            second = copy.deepcopy(bad['tables'][0])
            second.update(name='other', physical_name='pilot_other', ref=dict(REF, object_id='different'),
                          table_uuid='c69a1029-a887-4675-8159-8bc142a7ef21')
            second[field] = bad['tables'][0][field]
            bad['tables'].append(second)
            with self.subTest(duplicate=field), self.assertRaises(ValueError):
                table_manifest(bad)
        for field, value in [('status', 'FAILED'), ('format', 2), ('tables', []), ('source_dataset_id', 'other')]:
            bad = copy.deepcopy(pins)
            bad[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                table_manifest(bad)
