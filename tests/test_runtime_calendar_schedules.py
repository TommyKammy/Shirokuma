"""Opt-in calendar acceptance through disposable shared HTTP/TLS state.

Real upcoming wall-clock slots exercise the resident scheduler after restart.
Only temporary state, files, and uniquely named Iceberg tables are written.
"""
import calendar
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo


_spec = importlib.util.spec_from_file_location(
    'calendar_runtime_harness', Path(__file__).with_name('test_runtime_stages.py'))
_harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_harness)
_warehouse = _harness.warehouse


@unittest.skipUnless(os.environ.get('SHIROKUMA_RUNTIME_TESTS') == '1',
                     'set SHIROKUMA_RUNTIME_TESTS=1 for calendar acceptance')
class RuntimeCalendarSchedulesTests(unittest.TestCase):
    # Keep the harness class on its module: a TestCase alias at module scope
    # would cause unittest discovery to execute the imported suite as well.
    setUpClass = classmethod(_harness.RuntimeStagesTests.setUpClass.__func__)
    setUp = _harness.RuntimeStagesTests.setUp
    sql = _harness.RuntimeStagesTests.sql
    cleanup_tables = _harness.RuntimeStagesTests.cleanup_tables
    start_workbench = _harness.RuntimeStagesTests.start_workbench
    stop_workbench = staticmethod(_harness.RuntimeStagesTests.stop_workbench)
    http = _harness.RuntimeStagesTests.http
    request = _harness.RuntimeStagesTests.request
    wait_run = _harness.RuntimeStagesTests.wait_run
    stable_file = _harness.RuntimeStagesTests.stable_file
    stage_scan = _harness.RuntimeStagesTests.stage_scan

    def upcoming_once(self):
        # Leave at least 35 seconds for API saves and a genuine process restart.
        due = (datetime.now(ZoneInfo('Asia/Tokyo')) + timedelta(seconds=95)).replace(
            second=0, microsecond=0)
        return dict(kind='once', date=due.date().isoformat(), time=due.strftime('%H:%M'),
                    timezone='Asia/Tokyo', enabled=True), due.astimezone(timezone.utc)

    def wait_scheduled(self, name, due):
        deadline = time.monotonic() + max(0, (due - datetime.now(timezone.utc)).total_seconds()) + 50
        while time.monotonic() < deadline:
            runs = [run for run in self.request('/api/jobs/runs')['runs']
                    if run['job_name'] == name and run['trigger'] == 'schedule']
            if runs:
                self.assertEqual(len(runs), 1, runs)
                return self.wait_run(runs[0]['id'])
            time.sleep(.25)
        self.fail('Calendar slot did not produce history within its wall-clock window')

    def jobs_schedules(self):
        return {item['name']: item for item in self.request('/api/jobs/schedules')['schedules']}

    def stage(self, name):
        return next(item for item in self.request('/api/stages')['stages'] if item['name'] == name)

    def assert_consumed(self, schedule, run):
        self.assertFalse(schedule['enabled'], schedule)
        self.assertIsNone(schedule['next_run_at'])
        self.assertEqual(schedule['last_run_id'], run['id'])

    def assert_no_replay(self, history):
        # Cross the normal ten-second poll as well as the startup tick.
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            self.assertEqual(self.request('/api/jobs/runs')['runs'], history)
            time.sleep(.5)

    def test_job_once_restart_revocation_recurring_readback_and_restore(self):
        self.sql('CREATE TABLE ' + _warehouse.identifier(self.table) + ' (id BIGINT)')
        child = self.start_workbench()
        for name, user in [('once_insert', 'emily'), ('revoked_calendar', 'bob'),
                           ('weekly_calendar', 'alice'), ('monthly_calendar', 'alice')]:
            self.request('/api/jobs/save', dict(name=name, steps=[dict(action='sql',
                sql='INSERT INTO ' + _warehouse.identifier(self.table) + ' VALUES (7)',
                mode='sandbox-write', schema='raw')]), user=user)
        tomorrow = datetime.now(ZoneInfo('Asia/Tokyo')).date() + timedelta(days=1)
        weekly = self.request('/api/jobs/schedule', dict(name='weekly_calendar', kind='weekly',
            weekdays=[tomorrow.isoweekday()], time='12:34', enabled=True))['schedule']
        monthly = self.request('/api/jobs/schedule', dict(name='monthly_calendar', kind='monthly',
            day_of_month='last', time='23:59', timezone='America/New_York', enabled=True))['schedule']
        self.assertEqual(weekly['timezone'], 'Asia/Tokyo')
        weekly_due = datetime.fromisoformat(weekly['next_run_at']).astimezone(ZoneInfo('Asia/Tokyo'))
        self.assertEqual((weekly_due.date(), weekly_due.strftime('%H:%M')), (tomorrow, '12:34'))
        monthly_due = datetime.fromisoformat(monthly['next_run_at']).astimezone(ZoneInfo('America/New_York'))
        self.assertEqual(monthly_due.day, calendar.monthrange(monthly_due.year, monthly_due.month)[1])
        self.assertEqual(monthly_due.strftime('%H:%M'), '23:59')
        payload, due = self.upcoming_once()
        for user, status in [(None, 401), ('vera', 403), ('bob', 403)]:
            self.request('/api/jobs/schedule', dict(payload, name='once_insert'), user=user, expected=status)
        once = self.request('/api/jobs/schedule', dict(payload, name='once_insert'), user='emily')['schedule']
        revoked = self.request('/api/jobs/schedule', dict(payload, name='revoked_calendar'), user='bob')['schedule']
        self.assertEqual(datetime.fromisoformat(once['next_run_at']), due)
        accounts = json.loads(self.accounts.read_text())
        accounts['bob']['role'] = 'bi_viewer'
        self.accounts.write_text(json.dumps(accounts))
        self.stop_workbench(child)
        child = self.start_workbench()
        persisted = self.jobs_schedules()
        for saved in (weekly, monthly, once, revoked):
            self.assertEqual(persisted[saved['name']],
                             dict(saved, can_manage=saved['owner'] == 'alice'))
        successful = self.wait_scheduled('once_insert', due)
        denied = self.wait_scheduled('revoked_calendar', due)
        self.assertEqual((successful['status'], successful['actor'], successful['role']),
                         ('success', 'emily', 'data_engineer'), successful)
        self.assertEqual((denied['status'], denied['actor']), ('skipped', 'bob'), denied)
        self.assertEqual(successful['scheduled_for'], once['next_run_at'])
        self.assertEqual(self.sql('SELECT id FROM ' + _warehouse.identifier(self.table))['rows'], [[7]])
        schedules = self.jobs_schedules()
        self.assert_consumed(schedules['once_insert'], successful)
        self.assert_consumed(schedules['revoked_calendar'], denied)
        self.request('/api/jobs/schedule', dict(payload, name='once_insert'), user='emily', expected=400)
        history = self.request('/api/jobs/runs')['runs']
        self.stop_workbench(child)
        child = self.start_workbench()
        self.assert_no_replay(history)
        self.assertEqual(self.jobs_schedules(), schedules)
        import disaster_recovery as dr
        archive = self.directory / 'calendar-backup.tar.gz'
        with patch.object(dr, '_catalog', return_value=({}, [])), \
                patch.object(dr, 'MODELS_DIR', self.directory / 'no-models'):
            dr.backup(archive, include_tables=False)
        self.stop_workbench(child)
        restored = self.directory / 'recovered'
        with patch.object(dr, 'MODELS_DIR', restored / 'models'):
            dr.restore(archive, target_state_dir=restored)
        child = self.start_workbench(restored)
        self.assert_no_replay(history)
        recovered = self.jobs_schedules()
        for name, previous in schedules.items():
            self.assertFalse(recovered[name]['enabled'])
            self.assertIsNone(recovered[name]['next_run_at'])
            for field in ('kind', 'time', 'timezone', 'date', 'weekdays', 'day_of_month'):
                self.assertEqual(recovered[name][field], previous[field])
        self.stop_workbench(child)

    def test_stage_once_restart_manual_dedup_calendar_schemas_and_permissions(self):
        self.sql('CREATE TABLE ' + _warehouse.identifier(self.table) + ' (id BIGINT, code VARCHAR)')
        source = self.directory / 'incoming'
        source.mkdir()
        first = self.stable_file(source, 'first.csv', 'id,code\n1,00123\n')
        child = self.start_workbench()
        name = 'once_stage'
        definition = dict(name=name, directory=str(source), table=self.table, mode='append', format='auto')
        saved = self.request('/api/stages/save', definition)['stage']
        file = self.request('/api/stages/files', {'name': name})['files'][0]
        manual = self.wait_run(self.request('/api/stages/import', dict(name=name,
            file=file['name'], version=file['version']), expected=202)['run']['id'])
        self.assertEqual(manual['status'], 'success', manual)
        first.rename(source / 'renamed.csv')
        self.stable_file(source, 'copy.csv', 'id,code\n1,00123\n')
        self.stable_file(source, 'new.csv', 'id,code\n2,00007\n')
        version = saved['updated_at']
        # Each kind round-trips through the stage's own API and SQLite state.
        for spec in [dict(kind='weekly', weekdays=[1, 5], time='12:34'),
                     dict(kind='monthly', day_of_month=31, time='23:59')]:
            recurring = self.request('/api/stages/schedule', dict(spec, name=name,
                version=version, enabled=True))['schedule']
            self.assertEqual(recurring['kind'], spec['kind'])
            self.assertTrue(recurring['next_run_at'])
            self.stop_workbench(child)
            child = self.start_workbench()
            self.assertEqual(self.stage(name)['schedule'], recurring)
        payload, due = self.upcoming_once()
        selection = dict(payload, name=name, version=version)
        for user, status in [('bob', 404), ('emily', 403), ('vera', 403), (None, 401)]:
            self.request('/api/stages/schedule', selection, user=user, expected=status)
        self.request('/api/stages/schedule', dict(selection, version='stale'), expected=400)
        self.request('/api/stages/schedule', dict(selection, every_minutes=1), expected=400)
        scheduled = self.request('/api/stages/schedule', selection)['schedule']
        self.stop_workbench(child)
        child = self.start_workbench()
        self.assertEqual(self.stage(name)['schedule'], scheduled)
        run = self.wait_scheduled('stage:' + name, due)
        self.assertEqual((run['status'], run['actor'], run['role']), ('success', 'alice', 'admin'), run)
        self.assertEqual(run['scheduled_for'], scheduled['next_run_at'])
        self.assertEqual(len(run['definition']['steps']), 1)
        self.assertEqual(self.sql('SELECT id, code FROM ' + _warehouse.identifier(self.table) +
                                 ' ORDER BY id')['rows'], [[1, '00123'], [2, '00007']])
        self.assert_consumed(self.stage(name)['schedule'], run)
        files = self.request('/api/stages/files', {'name': name})['files']
        self.assertEqual({item['ingestion_status'] for item in files}, {'success'})
        self.assertEqual({item['last_run_id'] for item in files if item['name'] != 'new.csv'}, {manual['id']})
        history = self.request('/api/jobs/runs')['runs']
        self.stop_workbench(child)
        child = self.start_workbench()
        self.assert_no_replay(history)
        self.assert_consumed(self.stage(name)['schedule'], run)
        empty = self.stage_scan(name)
        self.assertEqual([step['action'] for step in empty['definition']['steps']], ['stage_scan_empty'])
        self.assertEqual(self.sql('SELECT count(*) FROM ' + _warehouse.identifier(self.table))['rows'], [[2]])
        self.request('/api/stages/schedule', selection, expected=400)
        self.stop_workbench(child)


if __name__ == '__main__':
    unittest.main()
