"""Synthetic scheduler checks: cadence, authorization, DST and process claims."""
from datetime import datetime, timedelta, timezone
from contextlib import ExitStack
import importlib
import csv
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / 'tools/local_experiment'
sys.path.insert(0, str(TOOLS))
import jobs_resident as jobs
import schedule_resident as scheduler
import request_auth

BASE = datetime(2026, 10, 18, tzinfo=timezone.utc)


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        jobs.stop_scheduler()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        environment = patch.dict(os.environ, SHIROKUMA_STATE_DIR=temporary.name,
            SHIROKUMA_AUTH_MODE='local', SHIROKUMA_AUTH_FILE='', SHIROKUMA_DEFAULT_ROLE='admin')
        environment.start()
        self.addCleanup(environment.stop)
        self.addCleanup(jobs.stop_scheduler)
        telemetry = patch.object(jobs.wr, 'telemetry', return_value='skipped')
        telemetry.start()
        self.addCleanup(telemetry.stop)

    def job(self, name='scheduled', actor='local', role='admin', steps=None, parameters=None):
        return jobs.save_job(dict(name=name, parameters=parameters or {},
            steps=steps or [dict(action='sql', sql='SELECT 1', mode='read-only')]), actor, role)

    def schedule(self, name='scheduled', actor='local', role='admin', now=BASE, **options):
        payload = dict(name=name, kind='interval', every_minutes=1, enabled=True, arguments={})
        payload.update(options)
        if payload['kind'] != 'interval':
            payload.pop('every_minutes', None)
        with patch.object(scheduler, '_utc', return_value=now):
            return jobs.save_schedule(payload, actor, role)

    def terminal(self, run_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = jobs.get_run(run_id, 'local', 'admin')
            if result['status'] not in jobs.ACTIVE:
                for thread in threading.enumerate():
                    if thread.name == 'job-' + run_id[:8]:
                        thread.join(2)
                return result
            time.sleep(.01)
        self.fail('scheduled worker did not finish')

    def test_calendar_normalization_roundtrip_and_kind_changes(self):
        self.job()
        cases = [
            dict(kind='once', date='2026-10-19', time='09:00'),
            dict(kind='weekly', weekdays=[7, 1, 3], time='09:00'),
            dict(kind='monthly', day_of_month=31, time='09:00'),
            dict(kind='monthly', day_of_month='last', time='09:00'),
        ]
        for options in cases:
            with self.subTest(options=options):
                saved = self.schedule(**options)
                self.assertEqual(saved['timezone'], 'Asia/Tokyo')
                self.assertEqual(saved['date'], options.get('date'))
                self.assertEqual(saved['weekdays'], sorted(options.get('weekdays', [])))
                self.assertEqual(saved['day_of_month'], options.get('day_of_month'))
                self.assertEqual(jobs.list_schedules('local', 'admin')[0]['next_run_at'], saved['next_run_at'])
        cleared = self.schedule()
        self.assertEqual((cleared['date'], cleared['weekdays'], cleared['day_of_month']), (None, [], None))

    def test_recurring_calendar_tick_decodes_fields_and_advances_without_duplicate(self):
        self.job()
        for options, expected in [
                (dict(kind='weekly', weekdays=[1, 7], time='09:01'), '2026-10-19T00:01:00+00:00'),
                (dict(kind='monthly', day_of_month=31, time='09:01'), '2026-12-31T00:01:00+00:00'),
                (dict(kind='monthly', day_of_month='last', time='09:01'), '2026-11-30T00:01:00+00:00')]:
            with self.subTest(options=options):
                saved = self.schedule(**options)
                due = datetime.fromisoformat(saved['next_run_at'])
                with patch.object(jobs, '_execute_step', return_value={}) as execute:
                    result = jobs.tick(due)
                    self.assertEqual(self.terminal(result['started'][0])['status'], 'success')
                    self.assertEqual(jobs.tick(due), dict(started=[], skipped=[], paused=[], unknown=[]))
                    self.assertEqual(execute.call_count, 1)
                persisted = jobs.list_schedules('local', 'admin')[0]
                self.assertTrue(persisted['enabled'])
                self.assertEqual(persisted['next_run_at'], expected)
                self.assertGreater(datetime.fromisoformat(persisted['next_run_at']), due)

    def test_calendar_invalid_fields_and_once_draft_future_rules(self):
        self.job()
        cases = [
            dict(kind='weekly', weekdays=[]), dict(kind='weekly', weekdays=[1, 1]),
            dict(kind='weekly', weekdays=[True]), dict(kind='weekly', weekdays=[0]),
            dict(kind='weekly', weekdays='1'), dict(kind='weekly', weekdays=[1.0]),
            dict(kind='monthly', day_of_month=True), dict(kind='monthly', day_of_month=0),
            dict(kind='monthly', day_of_month=32), dict(kind='monthly', day_of_month='31'),
            dict(kind='once', date='2026-02-29'),
            dict(kind='once', date='0001-01-01'),
            dict(kind='once', date='9999-12-31', time='23:30', timezone='America/New_York'), dict(kind='once', date='2026-1-01'),
            dict(kind='once', date='2026-03-08', time='02:30', timezone='America/New_York'),
            dict(kind='daily', weekdays=[1]), dict(kind='interval', date=None),
            dict(kind='weekly', weekdays=[1], day_of_month=None),
        ]
        for case in cases:
            payload = dict(name='scheduled', time='09:00', **{key: value for key, value in case.items() if key != 'time'})
            payload['time'] = case.get('time', '09:00')
            with self.subTest(case=case), self.assertRaises(ValueError):
                jobs.save_schedule(payload, 'local', 'admin')
        draft = self.schedule(kind='once', date='2026-10-17', time='09:00', enabled=False)
        self.assertFalse(draft['enabled'])
        with self.assertRaises(ValueError):
            self.schedule(kind='once', date='2026-10-18', time='09:00')

    def test_calendar_week_month_leap_and_dst_boundaries(self):
        cases = [
            (dict(kind='weekly', weekdays=[1], time='09:00'), '2026-10-18T00:00:00+00:00', '2026-10-19T00:00:00+00:00'),
            (dict(kind='weekly', weekdays=[1], time='09:00'), '2026-10-19T00:00:00+00:00', '2026-10-26T00:00:00+00:00'),
            (dict(kind='monthly', day_of_month=31, time='09:00'), '2026-04-01T00:00:00+00:00', '2026-05-31T00:00:00+00:00'),
            (dict(kind='monthly', day_of_month='last', time='09:00'), '2028-02-01T00:00:00+00:00', '2028-02-29T00:00:00+00:00'),
            (dict(kind='monthly', day_of_month='last', time='09:00'), '2026-02-01T00:00:00+00:00', '2026-02-28T00:00:00+00:00'),
            (dict(kind='monthly', day_of_month=29, time='09:00'), '2026-02-01T00:00:00+00:00', '2026-03-29T00:00:00+00:00'),
            (dict(kind='weekly', weekdays=[7], time='02:30', timezone='America/New_York'), '2026-03-07T12:00:00+00:00', '2026-03-15T06:30:00+00:00'),
            (dict(kind='monthly', day_of_month=8, time='02:30', timezone='America/New_York'), '2026-03-07T12:00:00+00:00', '2026-04-08T06:30:00+00:00'),
            (dict(kind='weekly', weekdays=[7], time='01:30', timezone='America/New_York'), '2026-11-01T05:45:00+00:00', '2026-11-08T06:30:00+00:00'),
            (dict(kind='monthly', day_of_month=1, time='01:30', timezone='America/New_York'), '2026-10-31T12:00:00+00:00', '2026-11-01T05:30:00+00:00'),
        ]
        for payload, start, expected in cases:
            with self.subTest(payload=payload, start=start):
                now = datetime.fromisoformat(start)
                normalized = scheduler._validate_schedule(payload, now)
                self.assertEqual(scheduler._next(normalized, now).isoformat(), expected)
        once = scheduler._validate_schedule(dict(kind='once', date='2026-11-01', time='01:30', timezone='America/New_York'), BASE)
        self.assertEqual(scheduler._next(once, BASE).isoformat(), '2026-11-01T05:30:00+00:00')
        self.assertIsNone(scheduler._next(once, datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)))

    def test_once_consumed_on_success_skip_start_failure_and_auth_pause(self):
        self.job()
        for outcome in ('success', 'missed', 'start_failure', 'paused'):
            with self.subTest(outcome=outcome):
                saved = self.schedule(kind='once', date='2026-10-18', time='09:01')
                due = datetime.fromisoformat(saved['next_run_at'])
                importlib.reload(scheduler)
                self.assertEqual(jobs.list_schedules('local', 'admin')[0]['next_run_at'], saved['next_run_at'])
                with ExitStack() as patches:
                    patches.enter_context(patch.object(jobs, '_execute_step', return_value={}))
                    if outcome == 'start_failure':
                        patches.enter_context(patch.object(jobs, '_start_worker', side_effect=RuntimeError('unavailable')))
                    elif outcome == 'paused':
                        patches.enter_context(patch.object(scheduler, '_authorize', side_effect=PermissionError('revoked')))
                    result = jobs.tick(due + timedelta(seconds=60 if outcome == 'missed' else 0))
                    if outcome == 'success':
                        self.assertEqual(self.terminal(result['started'][0])['status'], 'success')
                    else:
                        self.assertEqual(len(result['unknown'] if outcome == 'start_failure' else result['skipped']), 1)
                saved = jobs.list_schedules('local', 'admin')[0]
                self.assertFalse(saved['enabled'])
                self.assertIsNone(saved['next_run_at'])
                self.assertEqual(jobs.tick(due + timedelta(days=1)), dict(started=[], skipped=[], paused=[], unknown=[]))

    def test_once_concurrent_ticks_claim_only_one_run(self):
        self.job()
        saved = self.schedule(kind='once', date='2026-10-18', time='09:01')
        due = datetime.fromisoformat(saved['next_run_at'])
        ready = threading.Barrier(2)
        results = []
        def tick():
            ready.wait()
            results.append(jobs.tick(due))
        with patch.object(jobs, '_execute_step', return_value={}):
            threads = [threading.Thread(target=tick) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(3)
                self.assertFalse(thread.is_alive())
            started = [run for result in results for run in result['started']]
            self.assertEqual(len(started), 1)
            self.terminal(started[0])
        self.assertFalse(jobs.list_schedules('local', 'admin')[0]['enabled'])
        self.assertEqual(len(jobs.list_runs('local', 'admin')), 1)

    def test_invalid_calendar_does_not_starve_independent_due_work(self):
        self.job(name='a_bad')
        bad = self.schedule(name='a_bad', kind='weekly', weekdays=[1], time='09:00')
        self.job(name='z_good')
        self.schedule(name='z_good')
        due = BASE + timedelta(minutes=1)
        with jobs._db() as connection:
            connection.execute("UPDATE schedules SET next_run_at=?,timezone='Bad/Zone' WHERE name='a_bad'", (due.isoformat(),))
        with patch.object(jobs, '_execute_step', return_value={}):
            result = jobs.tick(due)
            self.assertEqual(self.terminal(result['started'][0])['status'], 'success')
        self.assertEqual(result['paused'], ['a_bad'])
        self.assertEqual(len(result['skipped']), 1)

    def test_legacy_schedule_migration_preserves_rows_and_is_idempotent(self):
        database = self.directory / 'jobs.sqlite3'
        with sqlite3.connect(database) as connection:
            connection.execute('CREATE TABLE schedules(name TEXT PRIMARY KEY, owner TEXT, kind TEXT, every_minutes INTEGER, time TEXT, timezone TEXT, arguments TEXT, enabled INTEGER, role TEXT, auth_mode TEXT,next_run_at TEXT,last_status TEXT,last_run_id TEXT,last_scheduled_for TEXT)')
            connection.execute('INSERT INTO schedules VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)', ('old', 'local', 'interval', 5, None, None, '{}', 1, 'admin', 'local', (BASE + timedelta(minutes=5)).isoformat(), 'success', 'previous-run', BASE.isoformat()))
        for _ in range(2):
            saved = jobs.list_schedules('local', 'admin')[0]
            self.assertEqual(saved['every_minutes'], 5)
            self.assertEqual((saved['last_status'], saved['last_run_id'], saved['last_scheduled_for']), ('success', 'previous-run', BASE.isoformat()))
            self.assertTrue(saved['enabled'])
            self.assertEqual(saved['next_run_at'], (BASE + timedelta(minutes=5)).isoformat())
            self.assertEqual((saved['date'], saved['weekdays'], saved['day_of_month']), (None, [], None))

    def test_schedule_saves_future_utc_and_defaults_disabled(self):
        self.job()
        with patch.object(scheduler, '_utc', return_value=BASE):
            disabled = jobs.save_schedule(dict(name='scheduled', kind='interval', every_minutes=3), 'local', 'admin')
        self.assertFalse(disabled['enabled'])
        self.assertIsNone(disabled['next_run_at'])
        enabled = self.schedule(every_minutes=3)
        self.assertEqual(enabled['next_run_at'], (BASE + timedelta(minutes=3)).isoformat())
        self.assertEqual((enabled['owner'], enabled['role'], enabled['auth_mode']), ('local', 'admin', 'local'))
        self.assertTrue(enabled['can_manage'])
        daily = self.schedule(kind='daily', time='09:01')
        self.assertEqual(daily['timezone'], 'Asia/Tokyo')
        self.assertEqual(daily['next_run_at'], (BASE + timedelta(minutes=1)).isoformat())

    def test_invalid_input_rejected_and_one_schedule_per_job(self):
        self.job()
        payload = dict(name='scheduled', kind='interval', every_minutes=1, enabled=False, arguments={})
        for changes in ({'every_minutes': True}, {'every_minutes': 0}, {'every_minutes': 10081},
                        {'enabled': 1}, {'arguments': []}, {'role': 'admin'}, {'owner': 'other'},
                        {'kind': 'cron'}, {'kind': 'daily', 'every_minutes': None, 'time': '09:00'},
                        {'timezone': 'Asia/Tokyo'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                jobs.save_schedule({**payload, **changes}, 'local', 'admin')
        for at, zone in [('24:00', 'UTC'), ('9:00', 'UTC'), ('12:60', 'UTC'), ('09:00', 'Bad/Zone')]:
            with self.assertRaises(ValueError):
                jobs.save_schedule(dict(name='scheduled', kind='daily', time=at, timezone=zone), 'local', 'admin')
        self.schedule(every_minutes=3)
        self.schedule(every_minutes=7)
        entries = jobs.list_schedules('local', 'admin')
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['every_minutes'], 7)
        with self.assertRaises(ValueError):
            jobs.tick(datetime(2026, 10, 18))

    def test_ownership_viewer_and_selected_role_not_body_are_authoritative(self):
        self.job(actor='alice', role='data_engineer')
        saved = self.schedule(actor='alice', role='data_engineer')
        self.assertEqual(saved['role'], 'data_engineer')
        self.assertEqual(jobs.list_schedules('bob', 'data_engineer'), [])
        self.assertFalse(jobs.list_schedules('root', 'admin')[0]['can_manage'])
        with self.assertRaises(PermissionError):
            self.schedule(actor='root')
        with self.assertRaises(PermissionError):
            self.schedule(actor='viewer', role='bi_viewer')
        with self.assertRaises(PermissionError):
            jobs.list_schedules('viewer', 'bi_viewer')
        self.job(name='private', steps=[dict(action='dbt_test')])
        with self.assertRaises(jobs.JobNotFound):
            self.schedule(name='missing')

    def test_interval_fires_once_and_preserves_previous_due_cadence(self):
        self.job()
        saved = self.schedule()
        due = datetime.fromisoformat(saved['next_run_at'])
        with patch.object(jobs, '_execute_step', return_value={'result': 'verified'}):
            accepted = jobs.tick(due + timedelta(seconds=15))
            run = self.terminal(accepted['started'][0])
            self.assertEqual(jobs.tick(due + timedelta(seconds=16))['started'], [])
        self.assertEqual(run['trigger'], 'schedule')
        self.assertEqual(run['scheduled_for'], due.isoformat())
        self.assertEqual(run['status'], 'success')
        entry = jobs.list_schedules('local', 'admin')[0]
        self.assertEqual(entry['next_run_at'], (due + timedelta(minutes=1)).isoformat())
        self.assertEqual(entry['last_status'], 'success')
        self.assertEqual(len(jobs.list_runs('local', 'admin')), 1)

    def test_sleep_missed_slots_record_one_skip_without_catchup_or_file_read(self):
        missing = str(self.directory / 'not-arrived.csv')
        self.job(steps=[dict(action='ingest', file=missing, table='raw.input', mode='replace')])
        saved = self.schedule(every_minutes=3)
        due = datetime.fromisoformat(saved['next_run_at'])
        now = due + timedelta(hours=5, seconds=60)
        import lake_ingest
        with patch.object(lake_ingest, 'read_file') as read, patch.object(jobs, '_execute_step') as execute:
            result = jobs.tick(now)
        self.assertEqual(result['started'], [])
        self.assertEqual(len(result['skipped']), 1)
        read.assert_not_called()
        execute.assert_not_called()
        run = jobs.get_run(result['skipped'][0], 'local', 'admin')
        self.assertEqual(run['status'], 'skipped')
        self.assertIn('no catch-up', run['error'])
        self.assertEqual(run['scheduled_for'], due.isoformat())
        next_run = datetime.fromisoformat(jobs.list_schedules('local', 'admin')[0]['next_run_at'])
        self.assertGreater(next_run, now)
        self.assertEqual((next_run - due).total_seconds() % 180, 0)
        self.assertEqual(jobs.tick(now)['skipped'], [])

    def test_exactly_60_seconds_late_is_skipped(self):
        self.job()
        due = datetime.fromisoformat(self.schedule()['next_run_at'])
        with patch.object(jobs, '_execute_step') as execute:
            result = jobs.tick(due + timedelta(seconds=60))
        execute.assert_not_called()
        self.assertEqual(len(result['skipped']), 1)

    def test_bad_csv_and_json_inputs_pause_without_starving_healthy_due_job(self):
        for name, filename, content in (
                ('a_bad_csv', 'input.csv', 'payload\n' + 'x' * (csv.field_size_limit() + 1) + '\n'),
                ('b_bad_json', 'input.jsonl', '{"value":1e9999999999999999999}')):
            path = self.directory / filename
            path.write_text(content)
            self.job(name=name, steps=[dict(action='ingest', file=str(path), table='raw.input', mode='replace')])
            self.schedule(name=name)
        self.job(name='z_healthy')
        due = datetime.fromisoformat(self.schedule(name='z_healthy')['next_run_at'])
        with patch.object(jobs, '_execute_step', return_value={}) as execute:
            result = jobs.tick(due)
            self.assertEqual(self.terminal(result['started'][0])['status'], 'success')
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(result['paused'], ['a_bad_csv', 'b_bad_json'])
        self.assertEqual(len(result['skipped']), 2)
        self.assertEqual(len(result['started']), 1)
        schedules = {row['name']: row for row in jobs.list_schedules('local', 'admin')}
        for name in ('a_bad_csv', 'b_bad_json'):
            self.assertFalse(schedules[name]['enabled'])
            self.assertIsNone(schedules[name]['next_run_at'])
        self.assertTrue(schedules['z_healthy']['enabled'])
        self.assertEqual(len(jobs.list_runs('local', 'admin')), 3)
        self.assertEqual(jobs.tick(due)['started'], [])

    def test_busy_run_skips_due_slot_without_queue_or_preflight_file_read(self):
        self.job(name='manual')
        missing = str(self.directory / 'missing.csv')
        self.job(steps=[dict(action='ingest', file=missing, table='raw.t', mode='replace')])
        due = datetime.fromisoformat(self.schedule()['next_run_at'])
        entered, release = threading.Event(), threading.Event()
        def execute(step):
            entered.set()
            release.wait(3)
            return {}
        import lake_ingest
        with patch.object(jobs, '_execute_step', side_effect=execute):
            run = jobs.submit_run('manual', {}, 'local', 'admin')
            try:
                self.assertTrue(entered.wait(2))
                with patch.object(lake_ingest, 'read_file') as read:
                    result = jobs.tick(due)
                self.assertEqual(len(result['skipped']), 1)
                read.assert_not_called()
                self.assertIn('active', jobs.get_run(result['skipped'][0], 'local', 'admin')['error'])
            finally:
                release.set()
                self.terminal(run['id'])
        self.assertEqual(jobs.tick(due)['started'], [])

    def test_ui_recovery_lock_contention_defers_without_consuming_schedule(self):
        self.job()
        saved = self.schedule()
        due = datetime.fromisoformat(saved['next_run_at'])
        lock = jobs._lock()
        try:
            result = jobs.tick(due)
            self.assertEqual(result['started'] + result['skipped'], [])
            with jobs._db() as db:
                self.assertEqual(db.execute('SELECT next_run_at FROM schedules').fetchone()[0], saved['next_run_at'])
        finally:
            lock.close()
        with patch.object(jobs, '_execute_step', return_value={}):
            accepted = jobs.tick(due + timedelta(seconds=1))
            self.assertEqual(self.terminal(accepted['started'][0])['status'], 'success')

    def test_auth_mode_changes_pause_in_both_directions(self):
        auth = self.directory / 'accounts.json'
        auth.write_text(json.dumps({'local': {'role': 'admin'}}))
        for initial, changed in [('local', 'shared'), ('shared', 'local')]:
            with self.subTest(initial=initial), patch.dict(os.environ, SHIROKUMA_AUTH_MODE=initial,
                    SHIROKUMA_AUTH_FILE=str(auth) if initial == 'shared' else ''):
                self.job()
                saved = self.schedule()
                os.environ['SHIROKUMA_AUTH_MODE'] = changed
                os.environ['SHIROKUMA_AUTH_FILE'] = str(auth) if changed == 'shared' else ''
                with patch.object(jobs, '_execute_step') as execute:
                    result = jobs.tick(datetime.fromisoformat(saved['next_run_at']))
                execute.assert_not_called()
                self.assertEqual(result['paused'], ['scheduled'])
                entry = jobs.list_schedules('local', 'admin')[0]
                self.assertFalse(entry['enabled'])
                self.assertIsNone(entry['next_run_at'])

    def test_account_downgrade_deletion_and_local_ceiling_changes_pause(self):
        auth = self.directory / 'accounts.json'
        for account in ({'alice': {'role': 'bi_viewer'}}, {}):
            auth.write_text(json.dumps({'alice': {'role': 'admin'}}))
            with patch.dict(os.environ, SHIROKUMA_AUTH_MODE='shared', SHIROKUMA_AUTH_FILE=str(auth)):
                self.job(actor='alice')
                saved = self.schedule(actor='alice')
                auth.write_text(json.dumps(account))
                self.assertEqual(jobs.tick(datetime.fromisoformat(saved['next_run_at']))['paused'], ['scheduled'])
        self.job(name='local_job')
        saved = self.schedule(name='local_job')
        with patch.dict(os.environ, SHIROKUMA_DEFAULT_ROLE='bi_viewer'):
            self.assertEqual(jobs.tick(datetime.fromisoformat(saved['next_run_at']))['paused'], ['local_job'])

    def test_latest_definition_is_checked_under_saved_role_and_args(self):
        self.job(actor='alice', role='data_engineer')
        saved = self.schedule(actor='alice', role='data_engineer')
        self.job(actor='alice', role='admin', steps=[dict(action='dbt_test')])
        with patch.object(jobs, '_execute_step') as execute:
            result = jobs.tick(datetime.fromisoformat(saved['next_run_at']))
        execute.assert_not_called()
        self.assertEqual(result['paused'], ['scheduled'])
        self.assertEqual(jobs.get_run(result['skipped'][0], 'alice', 'data_engineer')['role'], 'data_engineer')

    def test_minimal_owner_pause_preserves_admin_schedule_under_lower_selected_role(self):
        self.job(steps=[dict(action='dbt_test')])
        original = self.schedule()
        with self.assertRaises(PermissionError):
            self.schedule(role='data_engineer', enabled=False)
        self.assertTrue(jobs.list_schedules('local', 'admin')[0]['enabled'])
        paused = jobs.save_schedule({'name': 'scheduled', 'enabled': False}, 'local', 'data_engineer')
        self.assertFalse(paused['enabled'])
        self.assertIsNone(paused['next_run_at'])
        for field in ('owner', 'kind', 'every_minutes', 'arguments', 'role', 'auth_mode'):
            self.assertEqual(paused[field], original[field])
        with self.assertRaises(PermissionError):
            jobs.save_schedule({'name': 'scheduled', 'enabled': False}, 'other', 'admin')
        with self.assertRaises(jobs.JobNotFound):
            jobs.save_schedule({'name': 'missing', 'enabled': False}, 'local', 'admin')
        with self.assertRaises(PermissionError):
            jobs.save_schedule({'name': 'scheduled', 'enabled': False}, 'local', 'bi_viewer')

    def test_actual_clock_refresh_rejects_preflight_overdue_and_later_slots(self):
        self.job(name='first')
        self.job(name='second')
        due = datetime.fromisoformat(self.schedule(name='first')['next_run_at'])
        self.schedule(name='second')
        clock = {'now': due}
        original = jobs._validate
        preflights = []
        def validate(*args, **kwargs):
            result = original(*args, **kwargs)
            if kwargs.get('preflight'):
                preflights.append(1)
                clock['now'] += timedelta(seconds=61)
            return result
        with patch.object(scheduler, '_utc', side_effect=lambda now=None: now or clock['now']), \
             patch.object(jobs, '_validate', side_effect=validate), \
             patch.object(jobs, '_execute_step') as execute:
            result = jobs.tick()
        execute.assert_not_called()
        self.assertEqual(len(preflights), 1)
        self.assertEqual(len(result['skipped']), 2)
        self.assertEqual(result['started'], [])
        for entry in jobs.list_schedules('local', 'admin'):
            self.assertGreater(datetime.fromisoformat(entry['next_run_at']), clock['now'])

    def test_explicit_tick_time_stays_fixed_during_preflight(self):
        self.job()
        due = datetime.fromisoformat(self.schedule()['next_run_at'])
        original = jobs._validate
        clock = {'now': due}
        def validate(*args, **kwargs):
            result = original(*args, **kwargs)
            if kwargs.get('preflight'):
                clock['now'] += timedelta(seconds=61)
            return result
        with patch.object(scheduler, '_utc', side_effect=lambda now=None: now or clock['now']), \
             patch.object(jobs, '_validate', side_effect=validate), \
             patch.object(jobs, '_execute_step', return_value={}):
            result = jobs.tick(due)
            self.assertEqual(self.terminal(result['started'][0])['status'], 'success')

    def test_scheduled_worker_auth_mode_is_rechecked_between_steps_both_directions(self):
        auth = self.directory / 'accounts.json'
        auth.write_text(json.dumps({'local': {'role': 'admin'}}))
        for initial, changed in [('local', 'shared'), ('shared', 'local')]:
            with self.subTest(initial=initial), patch.dict(os.environ, SHIROKUMA_AUTH_MODE=initial,
                    SHIROKUMA_AUTH_FILE=str(auth) if initial == 'shared' else ''):
                self.job(steps=[dict(action='sql', sql='SELECT 1', mode='read-only')] * 2)
                due = datetime.fromisoformat(self.schedule()['next_run_at'])
                def operation(step):
                    os.environ['SHIROKUMA_AUTH_MODE'] = changed
                    os.environ['SHIROKUMA_AUTH_FILE'] = str(auth) if changed == 'shared' else ''
                    return {}
                with patch.object(jobs, '_execute_step', side_effect=operation) as execute:
                    result = jobs.tick(due)
                    run = self.terminal(result['started'][0])
                self.assertEqual(execute.call_count, 1)
                self.assertEqual(run['status'], 'failed')
                self.assertEqual([step['status'] for step in run['steps']], ['success', 'failed'])

    def test_scheduled_worker_uses_saved_engineer_role_and_typed_arguments(self):
        self.job(actor='alice', role='data_engineer', parameters={'id': {'type': 'integer', 'required': True}},
                 steps=[dict(action='sql', sql='SELECT {{id}}', mode='read-only')])
        saved = self.schedule(actor='alice', role='data_engineer', arguments={'id': 7})
        observed = []
        def query(*args, **kwargs):
            observed.append((request_auth.current_role(), args[1], kwargs['max_rows']))
            return dict(result='verified', row_count=1)
        with patch.object(jobs.wr, 'query', side_effect=query):
            result = jobs.tick(datetime.fromisoformat(saved['next_run_at']))
            self.assertEqual(self.terminal(result['started'][0])['status'], 'success')
        self.assertEqual(observed, [('data_engineer', 'SELECT 7', None)])

    def test_daily_dst_gap_skips_date_and_fold_runs_only_first_occurrence(self):
        self.job()
        spring = self.schedule(kind='daily', time='02:30', timezone='America/New_York',
            now=datetime(2026, 3, 7, 12, tzinfo=timezone.utc))
        self.assertEqual(spring['next_run_at'], datetime(2026, 3, 9, 6, 30, tzinfo=timezone.utc).isoformat())
        fall = self.schedule(kind='daily', time='01:30', timezone='America/New_York',
            now=datetime(2026, 10, 31, 12, tzinfo=timezone.utc))
        first = datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)
        self.assertEqual(fall['next_run_at'], first.isoformat())
        with patch.object(jobs, '_execute_step', return_value={}):
            result = jobs.tick(first)
            self.terminal(result['started'][0])
            self.assertEqual(jobs.tick(first + timedelta(hours=1))['started'], [])
        next_run = jobs.list_schedules('local', 'admin')[0]['next_run_at']
        self.assertEqual(next_run, datetime(2026, 11, 2, 6, 30, tzinfo=timezone.utc).isoformat())
        after_first = self.schedule(kind='daily', time='01:30', timezone='America/New_York',
                                   now=first + timedelta(minutes=15))
        self.assertEqual(after_first['next_run_at'], next_run)

    def test_start_failure_consumes_slot_as_unknown_and_never_replays(self):
        self.job()
        due = datetime.fromisoformat(self.schedule()['next_run_at'])
        with patch.object(jobs, '_start_worker', side_effect=RuntimeError('thread unavailable')):
            result = jobs.tick(due)
        self.assertEqual(len(result['unknown']), 1)
        run = jobs.get_run(result['unknown'][0], 'local', 'admin')
        self.assertEqual(run['status'], 'unknown')
        self.assertEqual(run['trigger'], 'schedule')
        self.assertEqual(jobs.tick(due)['started'], [])

    def test_legacy_database_migrates_manual_run_without_losing_history(self):
        database = self.directory / 'jobs.sqlite3'
        with sqlite3.connect(database) as db:
            db.execute('CREATE TABLE runs(id TEXT PRIMARY KEY,job_name TEXT,actor TEXT,role TEXT,definition TEXT,arguments TEXT,status TEXT,created_at TEXT,started_at TEXT,finished_at TEXT,error TEXT)')
            db.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                ('old', 'legacy', 'local', 'admin', '{"name":"legacy","steps":[]}', '{}', 'success', BASE.isoformat(), None, None, None))
        migrated = jobs.get_run('old', 'local', 'admin')
        self.assertEqual(migrated['trigger'], 'manual')
        self.assertIsNone(migrated['scheduled_for'])
        self.assertEqual(migrated['status'], 'success')
        self.job()
        self.schedule()
        self.assertEqual(len(jobs.list_schedules('local', 'admin')), 1)

    def test_concurrent_process_ticks_claim_due_slot_only_once(self):
        self.job()
        due = self.schedule()['next_run_at']
        code = f'''
import sys,json,threading
from datetime import datetime
sys.path.insert(0,{str(TOOLS)!r})
import jobs_resident as jobs
jobs._execute_step=lambda step:{{}}
jobs.wr.telemetry=lambda *a,**k:'skipped'
sys.stdin.readline()
result=jobs.tick(datetime.fromisoformat({due!r}))
for thread in threading.enumerate():
    if thread.name.startswith('job-'): thread.join(3)
print(json.dumps(result),flush=True)
'''
        children = [subprocess.Popen([sys.executable, '-c', code], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        try:
            for child in children:
                child.stdin.write('go\n')
                child.stdin.flush()
            results = []
            for child in children:
                out, err = child.communicate(timeout=8)
                self.assertEqual(child.returncode, 0, err)
                results.append(json.loads(out))
            self.assertEqual(sum(len(result['started']) for result in results), 1)
            runs = jobs.list_runs('local', 'admin')
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]['status'], 'success')
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.communicate(timeout=5)

    def test_daemon_is_process_singleton_survives_errors_and_stops(self):
        retried = threading.Event()
        calls = []
        def tick(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError('synthetic tick failure')
            retried.set()
        with patch.object(scheduler, '_tick', side_effect=tick), self.assertLogs(scheduler.__name__, level='ERROR'):
            self.assertTrue(jobs.start_scheduler(interval=.01))
            self.assertFalse(jobs.start_scheduler(interval=.01))
            self.assertTrue(retried.wait(2))
            jobs.stop_scheduler()
        self.assertFalse(scheduler._THREAD.is_alive())
        self.assertGreaterEqual(len(calls), 2)


if __name__ == '__main__':
    unittest.main()
