"""Stage persistence, local file checks, and shared jobs-worker contracts."""
import csv
import importlib
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
import stages_resident as stages


class StagesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.input = self.root / 'input'
        self.input.mkdir()
        self.file = self.input / 'data.csv'
        self.file.write_text('id,value\n1,hello\n2,world\n')
        env = patch.dict(os.environ, {'SHIROKUMA_STATE_DIR': str(self.root / 'state'),
            'SHIROKUMA_AUTH_MODE': 'local', 'SHIROKUMA_AUTH_FILE': ''})
        env.start()
        self.addCleanup(env.stop)
        telemetry = patch.object(jobs.wr, 'telemetry', return_value='skipped')
        telemetry.start()
        self.addCleanup(telemetry.stop)
        stages.save_stage(self.definition(), 'alice', 'admin')

    def definition(self, **overrides):
        return dict(dict(name='incoming', directory=str(self.input), table='raw.incoming', mode='append'), **overrides)

    def version(self, filename='data.csv'):
        return next(item['version'] for item in stages.list_files('incoming', 'alice', 'admin')['files']
                    if item['name'] == filename)

    def done(self, run_id):
        for _ in range(300):
            result = jobs.get_run(run_id, 'alice', 'admin')
            if result['status'] not in jobs.ACTIVE:
                for thread in threading.enumerate():
                    if thread.name == 'job-' + run_id[:8]:
                        thread.join(2)
                return result
            time.sleep(.01)
        self.fail('worker did not finish')

    def test_permissions_owner_and_persistent_normalization(self):
        entry = stages.list_stages('bob', 'admin')[0]
        self.assertFalse(entry['can_manage'])
        self.assertEqual(entry['definition']['table'], 'iceberg.raw.incoming')
        self.assertEqual(entry['definition']['format'], 'auto')
        self.assertEqual(importlib.reload(stages).list_stages('alice', 'admin')[0]['owner'], 'alice')
        for role in ('data_engineer', 'bi_viewer'):
            for function, args in [(stages.list_stages, ()), (stages.save_stage, (self.definition(),)),
                                   (stages.list_files, ('incoming',)),
                                   (stages.preview_file, ('incoming', 'data.csv', 'v')),
                                   (stages.submit_import, ('incoming', 'data.csv', 'v'))]:
                with self.subTest(role=role, function=function), self.assertRaises(PermissionError):
                    function(*args, actor='alice', role=role)
        with self.assertRaises(PermissionError):
            stages.save_stage(self.definition(), 'bob', 'admin')
        for function, args in [(stages.list_files, ('incoming',)),
                               (stages.preview_file, ('incoming', 'data.csv', 'v')),
                               (stages.submit_import, ('incoming', 'data.csv', 'v'))]:
            with self.assertRaises(jobs.JobNotFound):
                function(*args, actor='bob', role='admin')

    def test_invalid_definitions(self):
        cases = [dict(name='Upper'), dict(name='a' * 49), dict(directory='relative'),
                 dict(directory=str(self.file)), dict(directory=str(self.root / 'missing')),
                 dict(table=''), dict(table='other.raw.t'), dict(mode='automatic'),
                 dict(mode='upsert'), dict(mode='upsert', key=' , '), dict(format='xml'),
                 dict(key=['id']), dict(path='/override')]
        for overrides in cases:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                stages.save_stage(self.definition(**overrides), 'alice', 'admin')
        for missing in ('table', 'mode'):
            definition = self.definition()
            del definition[missing]
            with self.assertRaises(ValueError):
                stages.save_stage(definition, 'alice', 'admin')

    def test_direct_supported_files_only_and_local_preview(self):
        for filename in ('.hidden.csv', '~temporary.csv', 'data.csv~', 'data.txt', 'data.csv.tmp'):
            (self.input / filename).write_text('id\n1\n')
        (self.input / 'directory.csv').mkdir()
        (self.input / 'escape.csv').symlink_to(self.root / 'outside.csv')
        (self.root / 'outside.csv').write_text('id\n1\n')
        files = stages.list_files('incoming', 'alice', 'admin')['files']
        self.assertEqual([item['name'] for item in files], ['data.csv'])
        with patch.object(lake_ingest.wr, 'trino', side_effect=AssertionError('warehouse called')):
            preview = stages.preview_file('incoming', 'data.csv', self.version(), 'alice', 'admin')
        self.assertEqual(preview['file'], 'data.csv')
        self.assertEqual(preview['stage']['table'], 'iceberg.raw.incoming')
        self.assertEqual(preview['stage']['mode'], 'append')
        self.assertNotIn('_revision', preview['stage'])
        self.assertEqual(preview['row_count'], 2)
        self.assertEqual(preview['sample_rows'], [['1', 'hello'], ['2', 'world']])
        for name in ('../outside.csv', str(self.file), '.hidden.csv', 'x/data.csv', 'x\\data.csv', 'escape.csv'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                stages.preview_file('incoming', name, self.version(), 'alice', 'admin')

    def test_format_sample_limit_and_safe_parser_errors(self):
        self.file.write_text('id\n' + '\n'.join(str(i) for i in range(20)))
        self.assertEqual(len(stages.preview_file('incoming', 'data.csv', self.version(), 'alice', 'admin')['sample_rows']), 10)
        for error in (csv.Error('secret source row'), ValueError('secret source row'), UnicodeError('secret source row')):
            with patch.object(lake_ingest, 'preview', side_effect=error), self.assertRaisesRegex(ValueError, 'could not be parsed') as caught:
                stages.preview_file('incoming', 'data.csv', self.version(), 'alice', 'admin')
            self.assertNotIn('secret', str(caught.exception))
        (self.input / 'records.jsonl').write_text('{"id": 1}\n')
        self.assertEqual(stages.preview_file('incoming', 'records.jsonl', self.version('records.jsonl'), 'alice', 'admin')['format'], 'jsonl')
        stages.save_stage(self.definition(mode='upsert', key='missing'), 'alice', 'admin')
        with self.assertRaisesRegex(ValueError, 'key must match'):
            stages.submit_import('incoming', 'data.csv', self.version(), 'alice', 'admin')

    def test_changed_file_limits_and_scan_are_explicit(self):
        version = self.version()
        self.file.write_text('id\n123456\n')
        with self.assertRaisesRegex(ValueError, 'changed'):
            stages.submit_import('incoming', 'data.csv', version, 'alice', 'admin')
        with self.file.open('wb') as stream:
            stream.truncate(stages.MAX_FILE_BYTES + 1)
        self.assertFalse(stages.list_files('incoming', 'alice', 'admin')['files'][0]['available'])
        with self.assertRaisesRegex(ValueError, '25 MiB'):
            stages.preview_file('incoming', 'data.csv', self.version(), 'alice', 'admin')
        with patch.object(stages, 'MAX_DIRECTORY_ENTRIES', 1):
            (self.input / 'unrelated.txt').touch()
            with self.assertRaisesRegex(ValueError, '1000 entries'):
                stages.list_files('incoming', 'alice', 'admin')
        self.file.unlink()
        (self.input / 'unrelated.txt').unlink()
        self.input.rmdir()
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            stages.list_files('incoming', 'alice', 'admin')

    def test_stage_edit_invalidates_file_selection_and_preview(self):
        version = self.version()
        stages.preview_file('incoming', 'data.csv', version, 'alice', 'admin')
        stages.save_stage(self.definition(table='marts.new_target', mode='replace'), 'alice', 'admin')
        for function in (stages.preview_file, stages.submit_import):
            with self.assertRaisesRegex(ValueError, 'stage or file changed'):
                function('incoming', 'data.csv', version, 'alice', 'admin')
        preview = stages.preview_file('incoming', 'data.csv', self.version(), 'alice', 'admin')
        self.assertEqual(preview['stage']['table'], 'iceberg.marts.new_target')
        self.assertEqual(preview['stage']['mode'], 'replace')
        self.assertNotIn('_revision', preview['stage'])
        with patch.object(lake_ingest, 'ingest', return_value=dict(rows_ingested=2)) as ingest:
            run = stages.submit_import('incoming', 'data.csv', self.version(), 'alice', 'admin')
            self.assertEqual(self.done(run['id'])['status'], 'success')
        self.assertEqual(ingest.call_args.kwargs['table'], 'iceberg.marts.new_target')

    def test_worker_snapshot_no_saved_jobs_and_semantic_change_allows_new_import(self):
        entered, release = threading.Event(), threading.Event()
        def ingest(*args, **kwargs):
            entered.set()
            release.wait(3)
            return dict(action='ingest', rows_ingested=2, table=kwargs['table'])
        with patch.object(lake_ingest, 'ingest', side_effect=ingest) as called:
            run = stages.submit_import('incoming', 'data.csv', self.version(), 'alice', 'admin')
            try:
                self.assertTrue(entered.wait(2))
                with self.assertRaises(jobs.JobConflict):
                    stages.submit_import('incoming', 'data.csv', self.version(), 'alice', 'admin')
                jobs.save_job(dict(name='sql', steps=[dict(action='sql', sql='SELECT 1', mode='read-only')]), 'alice', 'admin')
                with self.assertRaises(jobs.JobConflict):
                    jobs.submit_run('sql', {}, 'alice', 'admin')
                stages.save_stage(self.definition(table='marts.other', mode='replace'), 'alice', 'admin')
            finally:
                release.set()
                result = self.done(run['id'])
            self.assertEqual(result['status'], 'success')
            self.assertEqual(result['trigger'], 'stage')
            self.assertEqual(result['definition']['stage']['table'], 'iceberg.raw.incoming')
            self.assertEqual(result['definition']['source']['name'], 'data.csv')
            self.assertEqual(called.call_args.kwargs['mode'], 'append')
            self.assertTrue(called.call_args.kwargs['atomic_append'])
            self.assertEqual([job['name'] for job in jobs.list_jobs('alice', 'admin')], ['sql'])
            repeat = stages.submit_import('incoming', 'data.csv', self.version(), 'alice', 'admin')
            self.assertEqual(self.done(repeat['id'])['status'], 'success')
            self.assertEqual(called.call_count, 2)

    def test_worker_rechecks_file_version_path_and_role(self):
        captured = []
        with patch.object(jobs, '_start_worker', side_effect=lambda *args: captured.append(args)):
            run = stages.submit_import('incoming', 'data.csv', self.version(), 'alice', 'admin')
        args = captured.pop()
        self.file.write_text('id\nchanged\n')
        with patch.object(lake_ingest, 'ingest') as ingest:
            jobs._worker(*args)
        ingest.assert_not_called()
        self.assertEqual(self.done(run['id'])['status'], 'failed')
        with patch.object(jobs, '_start_worker', side_effect=lambda *args: captured.append(args)):
            run = stages.submit_import('incoming', 'data.csv', self.version(), 'alice', 'admin')
        with patch.object(jobs, '_check_account', side_effect=PermissionError('downgraded')), patch.object(lake_ingest, 'ingest') as ingest:
            jobs._worker(*captured.pop())
        ingest.assert_not_called()
        self.assertEqual(self.done(run['id'])['status'], 'failed')

    def test_worker_rechecks_resolved_source_identity(self):
        other = self.input / 'other.csv'
        other.write_bytes(self.file.read_bytes())
        original = self.file.stat()
        os.utime(other, ns=(original.st_atime_ns, original.st_mtime_ns))
        selected = self.input / 'selected.csv'
        selected.symlink_to(self.file)
        captured = []
        with patch.object(jobs, '_start_worker', side_effect=lambda *args: captured.append(args)):
            run = stages.submit_import('incoming', 'selected.csv', self.version('selected.csv'), 'alice', 'admin')
        selected.unlink()
        selected.symlink_to(other)
        with patch.object(lake_ingest, 'ingest') as ingest:
            jobs._worker(*captured.pop())
        ingest.assert_not_called()
        self.assertEqual(self.done(run['id'])['status'], 'failed')
        self.assertIn('path changed', self.done(run['id'])['error'])

    def test_lost_worker_history_recovers_unknown_without_replay(self):
        captured = []
        with patch.object(jobs, '_start_worker', side_effect=lambda *args: captured.append(args)):
            run = stages.submit_import('incoming', 'data.csv', self.version(), 'alice', 'admin')
        captured[0][4].close()
        with patch.object(lake_ingest, 'ingest') as ingest:
            result = jobs.get_run(run['id'], 'alice', 'admin')
        ingest.assert_not_called()
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['steps'][0]['status'], 'skipped')
        self.assertEqual(stages.list_stages('alice', 'admin')[0]['name'], 'incoming')
        self.assertEqual(jobs.list_jobs('alice', 'admin'), [])


if __name__ == '__main__':
    unittest.main()
