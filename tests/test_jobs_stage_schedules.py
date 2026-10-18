"""Stage auto-ingestion uses the existing jobs lock, schedule and content ledgers."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import importlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time
import unittest
from unittest.mock import patch

import test_jobs_stages as base
jobs, stages, lake_ingest = base.jobs, base.stages, base.lake_ingest
import schedule_resident as scheduler
import disaster_recovery as recovery


class StageSchedulesTests(unittest.TestCase):
    setUp = base.StagesTests.setUp
    definition = base.StagesTests.definition
    version = base.StagesTests.version
    done = base.StagesTests.done

    def stable(self, path=None):
        os.utime(path or self.file, (time.time() - 60,) * 2)

    def submit(self, retry=False):
        return stages.submit_import('incoming', 'data.csv', self.version(), 'alice', 'admin', retry=retry)

    def status(self, filename='data.csv'):
        return next(row for row in stages.list_files('incoming', 'alice', 'admin')['files'] if row['name'] == filename)

    def schedule(self, **extra):
        extra.setdefault('version', stages.list_stages('alice', 'admin')[0]['updated_at'])
        return stages.save_schedule(dict(name='incoming', every_minutes=1, **extra), 'alice', 'admin')

    def scan(self, version=None):
        return stages.submit_scan('incoming', 'alice', 'admin', version or stages.list_stages('alice', 'admin')[0]['updated_at'])

    def test_calendar_stage_kinds_preserve_revision_owner_and_modes(self):
        now = datetime(2026, 10, 18, tzinfo=timezone.utc)
        version = stages.list_stages('alice', 'admin')[0]['updated_at']
        cases = [
            dict(kind='once', date='2026-10-18', time='09:01'),
            dict(kind='daily', time='09:01'),
            dict(kind='weekly', weekdays=[7, 1], time='09:01'),
            dict(kind='monthly', day_of_month='last', time='09:01'),
        ]
        for options in cases:
            with self.subTest(options=options), patch.object(scheduler, '_utc', return_value=now):
                payload = dict(name='incoming', enabled=True, version=version, **options)
                saved = stages.save_schedule(payload, 'alice', 'admin')
                self.assertEqual(saved['kind'], options['kind'])
                self.assertEqual(saved['weekdays'], sorted(options.get('weekdays', [])))
                self.assertEqual(stages.list_stages('alice', 'admin')[0]['schedule']['next_run_at'], saved['next_run_at'])
                with self.assertRaises(ValueError):
                    stages.save_schedule({**payload, 'version': 'stale'}, 'alice', 'admin')
                with self.assertRaises(PermissionError):
                    stages.save_schedule(payload, 'alice', 'data_engineer')
                with self.assertRaises(jobs.JobNotFound):
                    stages.save_schedule(payload, 'bob', 'admin')
        stages.save_stage(self.definition(mode='replace'), 'alice', 'admin')
        version = stages.list_stages('alice', 'admin')[0]['updated_at']
        with self.assertRaises(ValueError):
            stages.save_schedule(dict(name='incoming', kind='weekly', weekdays=[1], time='09:00', enabled=True, version=version), 'alice', 'admin')

    def test_weekly_monthly_ticks_advance_and_preserve_ingestion_dedup(self):
        now = datetime(2026, 10, 18, tzinfo=timezone.utc)
        self.stable()
        version = stages.list_stages('alice', 'admin')[0]['updated_at']
        cases = [
            (dict(kind='weekly', weekdays=[7, 1], time='09:01'), '2026-10-19T00:01:00+00:00'),
            (dict(kind='monthly', day_of_month=31, time='09:01'), '2026-12-31T00:01:00+00:00'),
            (dict(kind='monthly', day_of_month='last', time='09:01'), '2026-11-30T00:01:00+00:00'),
        ]
        with patch.object(lake_ingest, 'ingest', return_value=dict(rows_ingested=2)) as ingest:
            for options, expected in cases:
                with self.subTest(options=options):
                    with patch.object(scheduler, '_utc', return_value=now):
                        saved = stages.save_schedule(dict(name='incoming', enabled=True, version=version, **options), 'alice', 'admin')
                    due = datetime.fromisoformat(saved['next_run_at'])
                    result = scheduler.tick(due)
                    self.assertEqual(self.done(result['started'][0])['status'], 'success')
                    self.assertEqual(scheduler.tick(due), dict(started=[], skipped=[], paused=[], unknown=[]))
                    schedule = stages.list_stages('alice', 'admin')[0]['schedule']
                    self.assertTrue(schedule['enabled'])
                    self.assertEqual(schedule['next_run_at'], expected)
                    self.assertGreater(datetime.fromisoformat(schedule['next_run_at']), due)
                    self.assertEqual(self.status()['ingestion_status'], 'success')
            self.assertEqual(ingest.call_count, 1)

    def test_once_stage_scan_consumes_slot_and_preserves_content_dedup(self):
        now = datetime(2026, 10, 18, tzinfo=timezone.utc)
        self.stable()
        payload = dict(name='incoming', kind='once', date='2026-10-18', time='09:01', enabled=True,
                       version=stages.list_stages('alice', 'admin')[0]['updated_at'])
        with patch.object(scheduler, '_utc', return_value=now):
            saved = stages.save_schedule(payload, 'alice', 'admin')
        due = datetime.fromisoformat(saved['next_run_at'])
        with patch.object(lake_ingest, 'ingest', return_value=dict(rows_ingested=2)) as ingest:
            result = scheduler.tick(due)
            self.assertEqual(self.done(result['started'][0])['status'], 'success')
            self.assertEqual(self.status()['ingestion_status'], 'success')
            self.assertEqual(scheduler.tick(due + timedelta(days=1))['started'], [])
            importlib.reload(stages)
            self.assertFalse(stages.list_stages('alice', 'admin')[0]['schedule']['enabled'])
            self.assertEqual(self.done(self.scan()['id'])['status'], 'success')
            self.assertEqual(ingest.call_count, 1)
        with patch.object(scheduler, '_utc', return_value=now):
            stages.save_schedule(payload, 'alice', 'admin')
        data = recovery._pause_restored_schedules((jobs._root() / 'jobs.sqlite3').read_bytes())
        restored = jobs._root() / 'restored-calendar.sqlite3'
        restored.write_bytes(data)
        with sqlite3.connect(restored) as connection:
            self.assertEqual(connection.execute('SELECT enabled,next_run_at,kind,date FROM schedules').fetchone(), (0, None, 'once', '2026-10-18'))

    def test_defaults_restart_pause_and_no_saved_job(self):
        self.assertIsNone(stages.list_stages('alice', 'admin')[0]['schedule'])
        saved = self.schedule()
        self.assertFalse(saved['enabled'])
        self.assertIsNone(saved['next_run_at'])
        self.assertEqual(saved['name'], 'incoming')
        saved = self.schedule(enabled=True)
        self.assertEqual(importlib.reload(stages).list_stages('alice', 'admin')[0]['schedule']['next_run_at'], saved['next_run_at'])
        self.assertEqual(jobs.list_jobs('alice', 'admin'), [])
        paused = stages.save_schedule(dict(name='incoming', enabled=False), 'alice', 'admin')
        self.assertFalse(paused['enabled'])
        self.assertEqual(paused['every_minutes'], 1)
        self.schedule(enabled=True)
        self.assertFalse(stages.save_stage(self.definition(), 'alice', 'admin')['schedule']['enabled'])
        for value in (0, 10081, True, 1.2):
            with self.subTest(value=value), self.assertRaises(ValueError):
                stages.save_schedule(dict(name='incoming', every_minutes=value), 'alice', 'admin')
        with self.assertRaises(jobs.JobNotFound):
            stages.save_schedule(dict(name='incoming', every_minutes=1), 'bob', 'admin')
        for function, args in ((stages.submit_scan, ('incoming',)), (stages.save_schedule, (dict(name='incoming', every_minutes=1),))):
            with self.assertRaises(PermissionError):
                if function is stages.submit_scan:
                    function(*args, actor='alice', role='data_engineer', version='stale')
                else:
                    function(*args, actor='alice', role='data_engineer')

    def test_content_dedup_survives_rename_touch_noop_and_restart(self):
        with patch.object(lake_ingest, 'ingest', return_value=dict(rows_ingested=2)) as ingest:
            self.assertEqual(self.done(self.submit()['id'])['status'], 'success')
            for retry in (False, True):
                with self.assertRaises(jobs.JobConflict):
                    self.submit(retry)
            stages.save_stage(self.definition(), 'alice', 'admin')
            self.file.touch()
            self.assertEqual(self.status()['ingestion_status'], 'success')
            renamed = self.file.with_name('renamed.csv')
            self.file.rename(renamed)
            self.assertEqual(self.status('renamed.csv')['ingestion_status'], 'success')
            self.stable(renamed)
            self.assertEqual(self.done(self.scan()['id'])['status'], 'success')
            self.assertEqual(ingest.call_count, 1)
            importlib.reload(stages)
            self.assertEqual(self.status('renamed.csv')['ingestion_status'], 'success')

    def test_modified_content_and_semantic_config_are_new(self):
        with patch.object(lake_ingest, 'ingest', return_value={}):
            self.done(self.submit()['id'])
            self.file.write_text('id,value\n3,new\n')
            self.assertEqual(self.status()['ingestion_status'], 'new')
            self.done(self.submit()['id'])
            for change in (dict(table='raw.other'), dict(mode='upsert', key='id'), dict(format='csv')):
                stages.save_stage(self.definition(**change), 'alice', 'admin')
                self.assertEqual(self.status()['ingestion_status'], 'new')
                self.assertEqual(self.done(self.submit()['id'])['status'], 'success')

    def test_effective_tsv_dialect_is_distinct(self):
        with patch.object(lake_ingest, 'ingest', return_value={}):
            self.done(self.submit()['id'])
        self.file.rename(self.file.with_suffix('.tsv'))
        self.assertEqual(self.status('data.tsv')['ingestion_status'], 'new')

    def test_alias_uses_resolved_dialect_for_manual_and_automatic_dedup(self):
        target = self.file.with_suffix('.tsv')
        self.file.rename(target)
        target.write_text('id\tvalue\n1\thello\n2\tworld\n')
        self.file.symlink_to(target)
        self.stable(target)
        saved = self.schedule(enabled=True)
        with patch.object(lake_ingest, 'ingest', return_value={}) as ingest:
            run = self.done(stages.submit_import('incoming', 'data.tsv', self.version('data.tsv'), 'alice', 'admin')['id'])
            alias = self.status()
            self.assertEqual(alias['ingestion_status'], 'success')
            self.assertEqual(alias['last_run_id'], run['id'])
            preview = stages.preview_file('incoming', 'data.csv', alias['version'], 'alice', 'admin')
            self.assertEqual(preview['file'], 'data.csv')
            self.assertEqual(preview['ingestion_status'], 'success')
            self.assertEqual(preview['sample_rows'], [['1', 'hello'], ['2', 'world']])
            with self.assertRaises(jobs.JobConflict):
                self.submit()
            result = jobs.tick(datetime.fromisoformat(saved['next_run_at']))
            self.assertEqual(self.done(result['started'][0])['steps'][0]['action'], 'stage_scan_empty')
            self.assertEqual(ingest.call_count, 1)

    def test_alias_cannot_expose_unsupported_or_unsettled_target(self):
        for filename in ('bad.txt', 'bad.partial.csv', '.hidden.csv', 'bad.tmp.csv'):
            target = self.input / filename
            target.write_text('id\n3\n')
            (self.input / (filename.replace('.', '_') + '.csv')).symlink_to(target)
            self.stable(target)
        files = stages.list_files('incoming', 'alice', 'admin')['files']
        self.assertEqual([item['name'] for item in files], ['data.csv'])
        for filename in ('bad.txt', 'bad.partial.csv', '.hidden.csv', 'bad.tmp.csv'):
            alias = filename.replace('.', '_') + '.csv'
            with self.subTest(alias=alias), self.assertRaises(ValueError):
                stages.preview_file('incoming', alias, files[0]['version'], 'alice', 'admin')
        self.stable()
        saved = self.schedule(enabled=True)
        self._healthy_due_schedule(saved['next_run_at'])
        with patch.object(lake_ingest, 'ingest', return_value={}), patch.object(jobs.wr, 'query', return_value={}), \
                patch.object(jobs, '_start_worker', side_effect=jobs._worker):
            result = jobs.tick(datetime.fromisoformat(saved['next_run_at']))
            self.assertEqual(len(result['started']), 2)
            self.assertTrue(all(self.done(run_id)['status'] == 'success' for run_id in result['started']))

    def _healthy_due_schedule(self, due):
        jobs.save_job(dict(name='zhealthy', steps=[dict(action='sql', sql='SELECT 1', mode='read-only')]), 'alice', 'admin')
        jobs.save_schedule(dict(name='zhealthy', kind='interval', every_minutes=1, enabled=True), 'alice', 'admin')
        with jobs._db() as connection:
            connection.execute('UPDATE schedules SET next_run_at=? WHERE name=?', (due, 'zhealthy'))

    def test_admission_conflict_rolls_back_partial_claims_and_continues_tick(self):
        with patch.object(lake_ingest, 'ingest', return_value={}):
            original = self.done(self.submit()['id'])
        fresh = self.input / 'fresh.csv'
        fresh.write_text('id\n3\n')
        definition = stages._owned('incoming', 'alice')
        steps = [stages._step(definition, 'fresh.csv', self.version('fresh.csv')),
                 stages._step(definition, 'data.csv', self.version())]
        saved = self.schedule(enabled=True)
        self._healthy_due_schedule(saved['next_run_at'])
        scheduled = stages._scheduled
        def conflict(name, actor, role, preflight=False, connection=None):
            if preflight:
                return dict(name='stage:incoming', stage=definition, steps=steps), steps
            return scheduled(name, actor, role, preflight=preflight, connection=connection)
        with patch.object(stages, '_scheduled', side_effect=conflict), patch.object(jobs.wr, 'query', return_value={}), \
                patch.object(jobs, '_start_worker', side_effect=jobs._worker):
            result = jobs.tick(datetime.fromisoformat(saved['next_run_at']))
            self.assertEqual(len(result['skipped']), 1)
            self.assertEqual(len(result['started']), 1)
            self.assertEqual(self.done(result['started'][0])['status'], 'success')
        skipped = jobs.get_run(result['skipped'][0], 'alice', 'admin')
        self.assertIn('admission conflict', skipped['error'])
        self.assertTrue(all(step['status'] == 'skipped' for step in skipped['steps']))
        self.assertEqual(self.status()['last_run_id'], original['id'])
        self.assertEqual(self.status()['ingestion_status'], 'success')
        self.assertEqual(self.status('fresh.csv')['ingestion_status'], 'new')
        schedule = stages.list_stages('alice', 'admin')[0]['schedule']
        self.assertTrue(schedule['enabled'])
        self.assertGreater(schedule['next_run_at'], saved['next_run_at'])
        with jobs._db() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM runs').fetchone()[0], 3)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM stage_files').fetchone()[0], 1)

    def test_scheduled_scan_dedups_consumes_once_and_preserves_cadence(self):
        self.stable()
        saved = self.schedule(enabled=True)
        due = datetime.fromisoformat(saved['next_run_at'])
        with patch.object(lake_ingest, 'ingest', return_value=dict(rows_ingested=2)) as ingest:
            result = jobs.tick(due)
            run = self.done(result['started'][0])
            self.assertEqual(run['status'], 'success')
            self.assertEqual(run['trigger'], 'schedule')
            self.assertEqual(jobs.tick(due)['started'], [])
            result = jobs.tick(due + timedelta(minutes=1))
            self.done(result['started'][0])
            self.assertEqual(ingest.call_count, 1)
        self.assertEqual(self.status()['last_run_id'], run['id'])
        self.assertEqual(stages.list_stages('alice', 'admin')[0]['schedule']['last_status'], 'success')

    def test_bulk_caps_sorted_stable_partial_and_source_unchanged(self):
        self.file.unlink()
        for index in range(13):
            path = self.input / ('f%02d.csv' % index)
            path.write_text('id\n%d\n' % index)
            self.stable(path)
        for name in ('new.csv', 'old.partial.csv', 'old.part.csv'):
            path = self.input / name
            path.write_text('id\n99\n')
            if name != 'new.csv':
                self.stable(path)
        before = {p.name: p.read_bytes() for p in self.input.iterdir()}
        with patch.object(lake_ingest, 'ingest', return_value={}) as ingest:
            first = self.done(self.scan()['id'])
            self.assertEqual(len(first['steps']), 10)
            self.assertEqual([step['name'] for step in first['steps']], ['Import f%02d.csv' % i for i in range(10)])
            second = self.done(self.scan()['id'])
            self.assertEqual(len(second['steps']), 3)
            self.assertEqual(ingest.call_count, 13)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.input.iterdir()})

    def test_unknown_after_publication_requires_informed_manual_retry(self):
        self.stable()
        with patch.object(lake_ingest, 'ingest', side_effect=ValueError('secret post-commit error')):
            run = self.done(self.submit()['id'])
        self.assertEqual(run['status'], 'unknown')
        self.assertNotIn('secret', run['error'])
        self.assertEqual(self.status()['ingestion_status'], 'unknown')
        with self.assertRaises(jobs.JobConflict):
            self.submit()
        with patch.object(lake_ingest, 'ingest', return_value={}) as ingest:
            self.done(self.scan()['id'])
            ingest.assert_not_called()
            self.assertEqual(self.done(self.submit(retry=True)['id'])['status'], 'success')
        with self.assertRaises(ValueError):
            self.submit(retry=1)

    def test_failed_parse_is_not_retried_by_scans(self):
        self.stable()
        with patch.object(lake_ingest, 'preview', side_effect=ValueError('bad file')):
            failed = self.done(self.scan()['id'])
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(self.status()['ingestion_status'], 'failed')
        with patch.object(lake_ingest, 'ingest', return_value={}) as ingest:
            self.done(self.scan()['id'])
            ingest.assert_not_called()
            self.assertEqual(self.done(self.submit(retry=True)['id'])['status'], 'success')

    def test_sqlite_record_failure_after_publication_is_unknown(self):
        original_db = jobs._db
        injected = []
        @contextmanager
        def database():
            with original_db() as connection:
                class Proxy:
                    def execute(self, sql, args=()):
                        if "UPDATE steps SET status='success'" in sql and not injected:
                            injected.append(True)
                            raise sqlite3.OperationalError('synthetic recording failure')
                        return connection.execute(sql, args)
                    def __getattr__(self, key):
                        return getattr(connection, key)
                yield Proxy()
        with patch.object(jobs, '_db', database), patch.object(lake_ingest, 'ingest', return_value={}) as ingest:
            run = self.done(self.submit()['id'])
        self.assertEqual(ingest.call_count, 1)
        self.assertEqual(run['status'], 'unknown')
        self.assertEqual(self.status()['ingestion_status'], 'unknown')
        with self.assertRaises(jobs.JobConflict):
            self.submit()

    def test_lost_claim_remains_unknown_and_no_automatic_replay(self):
        captured = []
        with patch.object(jobs, '_start_worker', side_effect=lambda *args: captured.append(args)):
            run = self.submit()
        captured[0][4].close()
        self.assertEqual(jobs.get_run(run['id'], 'alice', 'admin')['status'], 'unknown')
        self.assertEqual(self.status()['ingestion_status'], 'unknown')
        self.stable()
        with patch.object(lake_ingest, 'ingest') as ingest:
            self.done(self.scan()['id'])
        ingest.assert_not_called()

    def test_snapshot_is_exact_and_manual_auto_cannot_overlap(self):
        self.stable()
        saved = self.schedule(enabled=True)
        entered, release = threading.Event(), threading.Event()
        content = self.file.read_bytes()
        def ingest(path, **kwargs):
            self.assertNotEqual(Path(path), self.file)
            self.file.write_text('id\nmodified\n')
            self.assertEqual(Path(path).read_bytes(), content)
            entered.set()
            release.wait(3)
            return {}
        with patch.object(lake_ingest, 'ingest', side_effect=ingest):
            run = self.submit()
            try:
                self.assertTrue(entered.wait(2))
                with self.assertRaises(jobs.JobConflict):
                    self.scan()
                result = jobs.tick(datetime.fromisoformat(saved['next_run_at']))
                self.assertEqual(len(result['skipped']), 1)
                self.assertEqual(result['started'], [])
            finally:
                release.set()
                self.done(run['id'])
        self.assertEqual(self.status()['ingestion_status'], 'new')

    def test_role_and_auth_mode_changes_pause_automatic_schedule(self):
        saved = self.schedule(enabled=True)
        with patch.dict(os.environ, SHIROKUMA_DEFAULT_ROLE='bi_viewer'), patch.object(lake_ingest, 'ingest') as ingest:
            result = jobs.tick(datetime.fromisoformat(saved['next_run_at']))
        ingest.assert_not_called()
        self.assertEqual(result['paused'], ['stage:incoming'])
        saved = self.schedule(enabled=True)
        auth = self.root / 'accounts.json'
        auth.write_text(json.dumps({'alice': {'role': 'admin'}}))
        with patch.dict(os.environ, SHIROKUMA_AUTH_MODE='shared', SHIROKUMA_AUTH_FILE=str(auth)):
            self.assertEqual(jobs.tick(datetime.fromisoformat(saved['next_run_at']))['paused'], ['stage:incoming'])

    def test_role_rechecked_between_files(self):
        self.stable()
        other = self.input / 'other.csv'
        other.write_text('id\n3\n')
        self.stable(other)
        def ingest(*args, **kwargs):
            os.environ['SHIROKUMA_DEFAULT_ROLE'] = 'bi_viewer'
            return {}
        with patch.dict(os.environ, SHIROKUMA_DEFAULT_ROLE='admin'), patch.object(lake_ingest, 'ingest', side_effect=ingest) as called:
            result = self.done(self.scan()['id'])
        self.assertEqual(called.call_count, 1)
        self.assertEqual(result['status'], 'failed')

    def test_restore_pauses_namespaced_schedule_and_preserves_content_ledger(self):
        with patch.object(lake_ingest, 'ingest', return_value={}):
            self.done(self.submit()['id'])
        self.schedule(enabled=True)
        database = jobs._root() / 'jobs.sqlite3'
        data = recovery._pause_restored_schedules(database.read_bytes())
        restored = self.root / 'restored.sqlite'
        restored.write_bytes(data)
        with sqlite3.connect(restored) as db:
            self.assertEqual(db.execute('SELECT enabled,next_run_at FROM schedules').fetchone(), (0, None))
            self.assertEqual(db.execute('SELECT status FROM stage_files').fetchone()[0], 'success')

    def test_failed_first_file_releases_unstarted_claims(self):
        self.stable()
        other = self.input / 'z_other.csv'
        other.write_text('id\n3\n')
        self.stable(other)
        with patch.object(lake_ingest, 'preview', side_effect=ValueError('bad file')):
            self.assertEqual(self.done(self.scan()['id'])['status'], 'failed')
        self.assertEqual(self.status()['ingestion_status'], 'failed')
        self.assertEqual(self.status('z_other.csv')['ingestion_status'], 'new')
        with patch.object(lake_ingest, 'ingest', return_value={}) as ingest:
            self.assertEqual(self.done(self.scan()['id'])['status'], 'success')
            self.assertEqual(ingest.call_count, 1)

    def test_legacy_history_blocks_imports_and_enable_but_allows_inspection(self):
        with jobs._db() as db:
            snapshot = dict(name='stage:incoming', steps=[dict(action='stage_import')])
            jobs._insert_run(db, snapshot, {}, snapshot['steps'], 'alice', 'admin', status='success')
        self.assertTrue(stages.list_stages('alice', 'admin')[0]['legacy_history'])
        self.assertEqual(stages.preview_file('incoming', 'data.csv', self.version(), 'alice', 'admin')['row_count'], 2)
        for operation in (self.submit, lambda: self.scan(), lambda: self.schedule(enabled=True)):
            with self.assertRaisesRegex(ValueError, 'Legacy stage history'):
                operation()
        self.assertFalse(self.schedule()['enabled'])
        self.assertFalse(stages.save_schedule(dict(name='incoming', enabled=False), 'alice', 'admin')['enabled'])

    def test_stale_stage_revision_rejects_scan_and_auto_enable(self):
        old = stages.list_stages('alice', 'admin')[0]['updated_at']
        stages.save_stage(self.definition(table='raw.changed'), 'alice', 'admin')
        with self.assertRaisesRegex(ValueError, 'stage changed'):
            self.scan(old)
        with self.assertRaisesRegex(ValueError, 'stage changed'):
            self.schedule(enabled=True, version=old)
        with self.assertRaisesRegex(ValueError, 'stage changed'):
            stages.save_schedule(dict(name='incoming', every_minutes=1, enabled=True), 'alice', 'admin')
        self.assertFalse(self.schedule(enabled=False, version=old)['enabled'])

    def test_replace_bulk_and_auto_enable_rejected_manual_preserved(self):
        stages.save_stage(self.definition(mode='replace'), 'alice', 'admin')
        with self.assertRaisesRegex(ValueError, 'append or upsert'):
            self.scan()
        with self.assertRaisesRegex(ValueError, 'append or upsert'):
            self.schedule(enabled=True)
        self.assertFalse(self.schedule()['enabled'])
        with patch.object(lake_ingest, 'ingest', return_value={}):
            self.assertEqual(self.done(self.submit()['id'])['status'], 'success')


if __name__ == '__main__':
    unittest.main()
