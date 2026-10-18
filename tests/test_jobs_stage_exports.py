"""Manual stage exports publish complete files through the shared jobs worker."""
from contextlib import nullcontext, contextmanager
import csv
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import jobs_resident as jobs
import lake_ingest
import request_auth
import stage_exports as exports
import stages_resident as stages


class StageExportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.input = self.root / 'input'
        self.input.mkdir()
        for mock in (patch.dict(os.environ, SHIROKUMA_STATE_DIR=str(self.root / 'state'),
                                SHIROKUMA_AUTH_MODE='local', SHIROKUMA_AUTH_FILE=''),
                     patch.object(jobs.wr, 'telemetry', return_value='skipped')):
            mock.start()
            self.addCleanup(mock.stop)
        self.stage = stages.save_stage(self.definition(), 'alice', 'admin')

    def definition(self):
        return dict(name='outgoing', directory=str(self.input), table='raw.input', mode='append')

    def submit(self, file='output.csv', format='csv', version=None):
        return exports.submit_export('outgoing', 'marts.result', format, file,
            self.stage['updated_at'] if version is None else version, 'alice', 'admin')

    def done(self, run):
        for _ in range(300):
            result = jobs.get_run(run['id'], 'alice', 'admin')
            if result['status'] not in jobs.ACTIVE:
                for thread in threading.enumerate():
                    if thread.name == 'job-' + run['id'][:8]:
                        thread.join(2)
                return result
            time.sleep(.01)
        self.fail('worker did not finish')

    def fake_export(self, table, *, file, **kwargs):
        Path(file).write_text('id,value\n1,hello\n')
        return dict(row_count=1, total_rows=1, truncated=False)

    def test_real_csv_and_jsonl_publication_and_summary(self):
        rows = [[1, None], [2, 'hello']]
        with patch.object(lake_ingest.wr, 'trino', return_value=nullcontext('local')), \
             patch.object(lake_ingest.bears, 'sql', return_value={'rows': [[2]]}), \
             patch.object(lake_ingest.wr, 'execute', return_value=dict(
                 columns=[{'name': 'id'}, {'name': 'value'}], rows=rows)):
            for format in ('csv', 'jsonl'):
                result = self.done(self.submit(file='output.' + format, format=format))
                self.assertEqual(result['status'], 'success')
                self.assertEqual(result['trigger'], 'stage')
                self.assertEqual(result['job_name'], 'stage:outgoing')
                summary = result['steps'][0]['summary']
                path = self.input / 'exports' / summary['file']
                self.assertEqual(summary['row_count'], 2)
                self.assertEqual(summary['size'], path.stat().st_size)
                self.assertEqual(summary['format'], format)
                if format == 'csv':
                    self.assertEqual(list(csv.reader(path.read_text().splitlines())),
                                     [['id', 'value'], ['1', ''], ['2', 'hello']])
                else:
                    self.assertEqual([json.loads(line) for line in path.read_text().splitlines()],
                                     [{'id': 1, 'value': None}, {'id': 2, 'value': 'hello'}])
        self.assertEqual(jobs.list_jobs('alice', 'admin'), [])
        listing = exports.list_exports('outgoing', 'alice', 'admin')
        self.assertEqual(listing['limits'], dict(max_rows=100000, max_file_bytes=26214400, max_directory_entries=1000))
        self.assertEqual([file['name'] for file in listing['files']], ['output.csv', 'output.jsonl'])

    def test_empty_table_publishes_csv_header_and_listing_bound(self):
        with patch.object(lake_ingest.wr, 'trino', return_value=nullcontext('local')), \
             patch.object(lake_ingest.bears, 'sql', return_value={'rows': [[0]]}), \
             patch.object(lake_ingest.wr, 'execute', return_value=dict(columns=[{'name': 'id'}], rows=[])):
            self.assertEqual(self.done(self.submit())['status'], 'success')
        self.assertEqual((self.input / 'exports/output.csv').read_text(), 'id\n')
        (self.input / 'exports/.temp').touch()
        with patch.object(stages, 'MAX_DIRECTORY_ENTRIES', 1), self.assertRaisesRegex(ValueError, '1000 entries'):
            exports.list_exports('outgoing', 'alice', 'admin')

    def test_parquet_uses_existing_serializer(self):
        try:
            import pyarrow.parquet as parquet
        except ImportError:
            self.skipTest('pyarrow is optional')
        with patch.object(lake_ingest.wr, 'trino', return_value=nullcontext('local')), \
             patch.object(lake_ingest.bears, 'sql', return_value={'rows': [[1]]}), \
             patch.object(lake_ingest.wr, 'execute', return_value=dict(columns=[{'name': 'id'}], rows=[[7]])):
            self.assertEqual(self.done(self.submit('output.parquet', 'parquet'))['status'], 'success')
        self.assertEqual(parquet.read_table(self.input / 'exports/output.parquet').to_pydict(), {'id': [7]})

    def test_invalid_filenames_formats_tables_and_revision(self):
        cases = [('../x.csv', 'csv'), ('/x.csv', 'csv'), ('x\\x.csv', 'csv'),
                 ('.hidden.csv', 'csv'), ('~x.csv', 'csv'), ('x.tmp.csv', 'csv'),
                 ('x.part.csv', 'csv'), ('x.csv~', 'csv'), ('x\n.csv', 'csv'),
                 ('x\x00.csv', 'csv'), ('x.tsv', 'csv'), ('x.csv', 'jsonl'), ('x.xml', 'xml')]
        for file, format in cases:
            with self.subTest(file=file), self.assertRaises(ValueError):
                self.submit(file, format)
        for table in ('result', 'other.marts.result', 'marts.result; DROP TABLE x'):
            with self.assertRaises(ValueError):
                exports.submit_export('outgoing', table, 'csv', 'x.csv', self.stage['updated_at'], 'alice', 'admin')
        for version in ('stale', '', 123):
            with self.assertRaises(ValueError):
                self.submit(version=version)
        with self.assertRaises(ValueError):
            jobs.save_job(dict(name='hidden', steps=[dict(action='stage_export')]), 'alice', 'admin')

    def test_only_admin_owner_and_exports_child(self):
        for actor, role, error in [('alice', 'data_engineer', PermissionError), ('bob', 'admin', jobs.JobNotFound)]:
            with self.assertRaises(error):
                exports.submit_export('outgoing', 'marts.result', 'csv', 'x.csv', self.stage['updated_at'], actor, role)
            with self.assertRaises(error):
                exports.list_exports('outgoing', actor, role)
        outside = self.root / 'outside'
        outside.mkdir()
        (self.input / 'exports').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.submit()

    def test_existing_file_and_atomic_collision_keep_original(self):
        with patch.object(lake_ingest, 'export_table', side_effect=self.fake_export):
            self.assertEqual(self.done(self.submit())['status'], 'success')
        path = self.input / 'exports/output.csv'
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.submit()
        path.unlink()
        original = os.link
        def race(source, target):
            Path(target).write_text('winner')
            return original(source, target)
        with patch.object(lake_ingest, 'export_table', side_effect=self.fake_export), patch.object(exports.os, 'link', side_effect=race):
            self.assertEqual(self.done(self.submit())['status'], 'failed')
        self.assertEqual(path.read_text(), 'winner')
        self.assertTrue(before)
        self.assertEqual([p.name for p in path.parent.iterdir()], ['output.csv'])

    def test_row_and_size_limits_cleanup_without_partial_publication(self):
        for row_count, total_rows, truncated, size in [(100001, 100001, False, 1),
                (1, 2, False, 1), (2, 1, False, 1), (1, 1, True, 1), (1, 1, False, exports.MAX_FILE_BYTES + 1)]:
            def bounded(table, *, file, **kwargs):
                self.assertEqual(kwargs, dict(format='csv', max_rows=100001, full=False))
                with Path(file).open('wb') as stream:
                    stream.truncate(size)
                return dict(row_count=row_count, total_rows=total_rows, truncated=truncated)
            with self.subTest(row_count=row_count, size=size), patch.object(lake_ingest, 'export_table', side_effect=bounded):
                self.assertEqual(self.done(self.submit())['status'], 'failed')
                self.assertEqual(list((self.input / 'exports').iterdir()), [])

    def test_export_error_cleanup(self):
        def failure(table, *, file, **kwargs):
            Path(file).write_text('partial')
            raise ValueError('provider failed')
        with patch.object(lake_ingest, 'export_table', side_effect=failure):
            self.assertEqual(self.done(self.submit())['status'], 'failed')
        self.assertEqual(list((self.input / 'exports').iterdir()), [])

    def test_stage_changes_during_export_block_publication(self):
        def changed(*args, **kwargs):
            report = self.fake_export(*args, **kwargs)
            stages.save_stage(self.definition(), 'alice', 'admin')
            return report
        with patch.object(lake_ingest, 'export_table', side_effect=changed):
            result = self.done(self.submit())
        self.assertEqual(result['status'], 'failed')
        self.assertIn('stage changed', result['error'])
        self.assertEqual(list((self.input / 'exports').iterdir()), [])

    def test_exact_row_and_byte_limits_publish(self):
        def bounded(table, *, file, **kwargs):
            with Path(file).open('wb') as stream:
                stream.truncate(exports.MAX_FILE_BYTES)
            return dict(row_count=exports.MAX_ROWS, total_rows=exports.MAX_ROWS, truncated=False)
        with patch.object(lake_ingest, 'export_table', side_effect=bounded):
            self.assertEqual(self.done(self.submit())['status'], 'success')
        self.assertEqual((self.input / 'exports/output.csv').stat().st_size, exports.MAX_FILE_BYTES)

    def test_owner_and_local_role_revocation_before_publication(self):
        for change in ('owner', 'role'):
            def revoke(*args, **kwargs):
                report = self.fake_export(*args, **kwargs)
                if change == 'owner':
                    with jobs._db() as connection:
                        connection.execute("UPDATE stages SET owner='bob' WHERE name='outgoing'")
                else:
                    os.environ['SHIROKUMA_DEFAULT_ROLE'] = 'data_engineer'
                return report
            with patch.dict(os.environ, SHIROKUMA_DEFAULT_ROLE='admin'), patch.object(lake_ingest, 'export_table', side_effect=revoke):
                self.assertEqual(self.done(self.submit())['status'], 'failed')
            self.assertEqual(list((self.input / 'exports').iterdir()), [])
            with jobs._db() as connection:
                connection.execute("UPDATE stages SET owner='alice' WHERE name='outgoing'")

    def test_worker_rechecks_revision_before_query_and_global_lock(self):
        captured = []
        with patch.object(jobs, '_start_worker', side_effect=lambda *args: captured.append(args)):
            run = self.submit()
        with self.assertRaises(jobs.JobConflict):
            self.submit('other.csv')
        stages.save_stage(self.definition(), 'alice', 'admin')
        with patch.object(lake_ingest, 'export_table') as query:
            jobs._worker(*captured.pop())
        query.assert_not_called()
        self.assertEqual(self.done(run)['status'], 'failed')

    def test_authorization_revoked_during_export(self):
        auth = self.root / 'auth.json'
        auth.write_text(json.dumps({'alice': {'role': 'admin'}}))
        def revoked(*args, **kwargs):
            report = self.fake_export(*args, **kwargs)
            auth.write_text(json.dumps({'alice': {'role': 'data_engineer'}}))
            return report
        with patch.dict(os.environ, SHIROKUMA_AUTH_MODE='shared', SHIROKUMA_AUTH_FILE=str(auth)), \
             patch.object(lake_ingest, 'export_table', side_effect=revoked):
            result = self.done(self.submit())
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(list((self.input / 'exports').iterdir()), [])

    def test_auth_mode_and_local_role_rechecked_and_effective_role(self):
        for shared in (False, True):
            auth = self.root / 'auth.json'
            auth.write_text(json.dumps({'alice': {'role': 'admin'}}))
            def query(*args, **kwargs):
                self.assertEqual(request_auth.current_role(), 'admin')
                return self.fake_export(*args, **kwargs)
            with patch.dict(os.environ, SHIROKUMA_AUTH_MODE='shared' if shared else 'local', SHIROKUMA_AUTH_FILE=str(auth) if shared else ''), \
                 patch.object(jobs.wr, 'resolve_role', return_value='admin') as resolve, \
                 patch.object(lake_ingest, 'export_table', side_effect=query):
                result = self.done(self.submit('shared.csv' if shared else 'local.csv'))
            self.assertEqual(result['status'], 'success')
            self.assertEqual(resolve.called, not shared)
        def changed(*args, **kwargs):
            report = self.fake_export(*args, **kwargs)
            os.environ['SHIROKUMA_AUTH_MODE'] = 'shared'
            return report
        with patch.dict(os.environ, SHIROKUMA_AUTH_MODE='local'), patch.object(lake_ingest, 'export_table', side_effect=changed):
            self.assertEqual(self.done(self.submit())['status'], 'failed')
        with patch.object(jobs.wr, 'resolve_role', side_effect=PermissionError('revoked')):
            with self.assertRaises(PermissionError):
                self.submit()

    def test_restart_unknown_no_replay_and_listing_filters_temporary(self):
        captured = []
        with patch.object(jobs, '_start_worker', side_effect=lambda *args: captured.append(args)):
            run = self.submit()
        captured[0][4].close()
        with patch.object(lake_ingest, 'export_table') as query:
            self.assertEqual(self.done(run)['status'], 'unknown')
        query.assert_not_called()
        directory = self.input / 'exports'
        directory.mkdir()
        for name in ('.stage-export-crash.tmp', '.hidden.csv', 'unfinished.part.csv', 'normal.csv'):
            (directory / name).write_text('test')
        self.assertEqual([item['name'] for item in exports.list_exports('outgoing', 'alice', 'admin')['files']], ['normal.csv'])

    def test_publication_transaction_failure_retains_unknown_file_summary(self):
        original_db = jobs._db
        failed = []
        @contextmanager
        def database():
            with original_db() as connection:
                yield connection
                if (self.input / 'exports/output.csv').exists() and not failed:
                    failed.append(True)
                    raise ValueError('commit failed after publication')
        with patch.object(jobs, '_db', side_effect=database), patch.object(lake_ingest, 'export_table', side_effect=self.fake_export):
            result = self.done(self.submit())
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['steps'][0]['summary']['file'], 'output.csv')
        self.assertEqual([path.name for path in (self.input / 'exports').iterdir()], ['output.csv'])

    def test_published_file_bookkeeping_failure_is_unknown_with_summary(self):
        original_db = jobs._db
        failed = []
        class Connection:
            def __init__(self, connection):
                self.connection = connection
            def execute(self, sql, parameters=()):
                if "UPDATE steps SET status='success'" in sql and not failed:
                    failed.append(True)
                    raise ValueError('bookkeeping failed')
                return self.connection.execute(sql, parameters)
            def __getattr__(self, name):
                return getattr(self.connection, name)
        @contextmanager
        def database():
            with original_db() as connection:
                yield Connection(connection)
        with patch.object(jobs, '_db', side_effect=database), patch.object(lake_ingest, 'export_table', side_effect=self.fake_export):
            result = self.done(self.submit())
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['steps'][0]['summary']['file'], 'output.csv')
        self.assertTrue((self.input / 'exports/output.csv').exists())
        with self.assertRaises(ValueError):
            self.submit()


if __name__ == '__main__':
    unittest.main()
