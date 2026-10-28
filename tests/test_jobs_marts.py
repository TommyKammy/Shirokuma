"""Registered mart admission, WAP publication and shared job ledger boundaries."""
import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import bears_resident as b
import jobs_resident as jobs
import marts_resident as marts
import schedule_resident as scheduler
import wap_resident as wap
from datetime import datetime, timedelta


class MartsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        for manager in (patch.dict(os.environ, SHIROKUMA_STATE_DIR=str(self.root),
                SHIROKUMA_AUTH_MODE='local', SHIROKUMA_AUTH_FILE=''),
                patch.object(b, 'WAP_REGISTRY', self.root / 'wap_models.json'),
                patch.object(jobs.wr, 'telemetry', return_value='skipped'),
                patch.object(jobs.wr, 'status', return_value={'replicas': 1}),
                patch.object(jobs.wr, 'warehouse_activity', side_effect=contextlib.nullcontext)):
            manager.start()
            self.addCleanup(manager.stop)

    def save(self, **changes):
        return marts.save_target(dict(name='orders', sql='SELECT 1 AS id', **changes), 'alice', 'admin')

    def done(self, run_id):
        until = time.monotonic() + 5
        while time.monotonic() < until:
            run = jobs.get_run(run_id, 'alice', 'admin')
            if run['status'] not in jobs.ACTIVE:
                for thread in threading.enumerate():
                    if thread.name == 'job-' + run_id[:8]:
                        thread.join(2)
                return run
            time.sleep(.01)
        self.fail('worker did not finish')

    def test_save_is_offline_and_validates_sql_quality_and_revision(self):
        with patch.object(b.wr, 'trino') as trino:
            target = self.save(min_rows=1, not_null=['id'])
            self.assertEqual(target['version'], 1)
            self.assertEqual(target['table'], 'iceberg.marts.orders')
            self.assertIsNone(target['last_run'])
            self.assertIsNone(target['last_success_at'])
            trino.assert_not_called()
        for changes in ({'sql': 'DELETE FROM iceberg.marts.t'}, {'sql': 'SELECT 1; SELECT 2'},
                        {'min_rows': -1}, {'min_rows': 0}, {'min_rows': True}, {'not_null': ['COUNT(*)']},
                        {'not_null': 'id'}, {'name': 'marts.orders'}):
            payload = dict(name='other', sql='SELECT 1', **{})
            payload.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                marts.save_target(payload, 'alice', 'admin')
        for version in (None, True, 0, 2):
            with self.subTest(version=version), self.assertRaises(jobs.JobConflict):
                self.save(version=version)
        self.assertEqual(self.save(version=1)['version'], 2)
        self.assertEqual(json.loads(b.WAP_REGISTRY.read_text())['orders']['owner'], 'alice')

    def test_admin_owner_and_legacy_boundaries(self):
        self.save()
        for method, args in ((marts.list_targets, ()), (marts.save_target, ({'name': 'x', 'sql': 'SELECT 1'},)),
                             (marts.submit_refresh, ('orders', 1))):
            with self.assertRaises(PermissionError):
                method(*args, 'alice', 'data_engineer')
        self.assertFalse(marts.list_targets('bob', 'admin')[0]['can_manage'])
        with self.assertRaises(PermissionError):
            marts.save_target(dict(name='orders', sql='SELECT 2', version=1), 'bob', 'admin')
        with self.assertRaises(PermissionError):
            marts.submit_refresh('orders', 1, 'bob', 'admin')
        b.save_wap_entry('legacy', sql='SELECT 3')
        self.assertEqual(len(marts.list_targets('alice', 'admin')), 1)
        with self.assertRaises(jobs.JobConflict):
            marts.save_target(dict(name='LEGACY', sql='SELECT 4'), 'alice', 'admin')
        with self.assertRaises(PermissionError):
            b.save_wap_entry('orders', sql='SELECT 99')
        with self.assertRaises(PermissionError):
            b.save_wap_entry('ORDERS', sql='SELECT 99')
        for args in (SimpleNamespace(refresh='orders'), SimpleNamespace(publish='ORDERS'),
                     SimpleNamespace(stage_table='orders', sql='SELECT 99')):
            with patch.object(b.wr, 'trino') as trino, self.assertRaises(PermissionError):
                b.data(args)
            trino.assert_not_called()
        self.assertEqual(b.load_wap_registry()['orders']['sql'], 'SELECT 1 AS id')

    def test_admission_captures_snapshot_and_excludes_jobs_and_edits(self):
        self.save(min_rows=5, not_null=['id'])
        started, release = threading.Event(), threading.Event()
        captured = []
        def execute(step):
            captured.append(step)
            started.set()
            if not release.wait(5):
                raise ValueError('test timeout')
            return dict(valid=True, table='iceberg.marts.orders')
        with patch.object(jobs, '_execute_step', side_effect=execute), patch.object(jobs.wr, 'resolve_role'):
            run = marts.submit_refresh('orders', 1, 'alice', 'admin')
            try:
                self.assertTrue(started.wait(5))
                with self.assertRaises(jobs.JobConflict):
                    self.save(version=1)
                with self.assertRaises(jobs.JobConflict):
                    marts.submit_refresh('orders', 1, 'alice', 'admin')
                with self.assertRaises(jobs.JobConflict):
                    jobs.submit_run('any', {}, 'alice', 'admin')
            finally:
                release.set()
            complete = self.done(run['id'])
        self.assertEqual(complete['status'], 'success')
        self.assertEqual(complete['definition']['steps'][0]['min_rows'], 5)
        self.assertEqual(captured[0]['not_null'], ['id'])
        self.save(version=1)
        self.assertEqual(jobs.get_run(run['id'], 'alice', 'admin')['definition']['steps'][0]['version'], 1)
        listing = marts.list_targets('alice', 'admin')[0]
        self.assertEqual(listing['last_run']['id'], run['id'])
        self.assertEqual(listing['last_success_at'], complete['finished_at'])
        with self.assertRaises(jobs.JobConflict):
            marts.submit_refresh('orders', 1, 'alice', 'admin')

    def test_restart_recovers_admitted_run_as_unknown_without_replay(self):
        self.save()
        def stopped(run_id, steps, actor, role, lock, *args):
            lock.close()
        with patch.object(jobs, '_start_worker', side_effect=stopped), patch.object(b, 'data') as execute:
            run = marts.submit_refresh('orders', 1, 'alice', 'admin')
            recovered = marts.list_targets('alice', 'admin')[0]['last_run']
            self.assertEqual(recovered['id'], run['id'])
            self.assertEqual(recovered['status'], 'unknown')
            self.assertIn('no automatic retry', recovered['error'])
            execute.assert_not_called()

    def test_private_action_cannot_be_registered_as_public_job(self):
        with self.assertRaises(ValueError):
            jobs.save_job(dict(name='bypass', steps=[dict(action='mart_refresh', model='orders')]),
                          'alice', 'admin')

    def test_successful_publication_with_failed_ledger_write_is_unknown(self):
        self.save()
        original = jobs.json.dumps
        failed = []
        def serialize(value, *args, **kwargs):
            if isinstance(value, dict) and value.get('valid') is True and not failed:
                failed.append(True)
                raise OSError('ledger write failed')
            return original(value, *args, **kwargs)
        with patch.object(jobs.wr, 'resolve_role'), \
             patch.object(jobs, '_execute_step', return_value=dict(valid=True, table='iceberg.marts.orders')), \
             patch.object(jobs.json, 'dumps', side_effect=serialize):
            run = marts.submit_refresh('orders', 1, 'alice', 'admin')
            complete = self.done(run['id'])
        self.assertEqual(complete['status'], 'unknown')
        self.assertEqual(complete['steps'][0]['status'], 'unknown')

    def test_quality_parameters_flow_to_wap_and_failed_quality_keeps_target(self):
        self.save(min_rows=3, not_null=['id'])
        statements = []
        def sql(url, statement):
            statements.append(statement)
            return {'rows': []}
        with patch.object(b.wr, 'trino', return_value=contextlib.nullcontext('url')), \
             patch.object(jobs.wr, 'resolve_role'), patch.object(b, 'sql', side_effect=sql), \
             patch.object(b, 'latest_snapshot', return_value=7), \
             patch.object(b, 'audit_table', return_value=dict(snapshot_id=7, row_count=2, null_counts={'id': 0})):
            run = marts.submit_refresh('orders', 1, 'alice', 'admin')
            complete = self.done(run['id'])
        self.assertEqual(complete['status'], 'failed')
        self.assertIn('>= 3', complete['error'])
        self.assertTrue(any('CREATE OR REPLACE TABLE "iceberg"."sandbox"."orders"' in s for s in statements))
        self.assertFalse(any('CREATE OR REPLACE TABLE "iceberg"."marts"' in s for s in statements))
        self.assertEqual(b.load_wap_registry()['orders']['version'], 1)

    def test_success_preserves_registered_definition_and_late_ledger_failure_is_unknown(self):
        self.save(min_rows=1)
        statements = []
        def sql(url, statement):
            statements.append(statement)
            return {'rows': []}
        for fail_metadata in (False, True):
            with self.subTest(fail_metadata=fail_metadata), \
                 patch.object(b.wr, 'trino', return_value=contextlib.nullcontext('url')), \
                 patch.object(jobs.wr, 'resolve_role'), patch.object(b, 'sql', side_effect=sql), \
                 patch.object(b, 'latest_snapshot', return_value=7), \
                 patch.object(b, 'audit_table', side_effect=[dict(snapshot_id=7,row_count=1,null_counts={}),
                                                           dict(snapshot_id=8)]), \
                 patch.object(b.wap, 'persist_json', side_effect=OSError('disk full') if fail_metadata else None) as persist:
                if not fail_metadata:
                    persist.side_effect = lambda path, value: path.write_text(json.dumps(value))
                run = marts.submit_refresh('orders', 1, 'alice', 'admin')
                complete = self.done(run['id'])
            self.assertEqual(complete['status'], 'unknown' if fail_metadata else 'success')
            entry = b.load_wap_registry()['orders']
            self.assertEqual(entry['owner'], 'alice')
            self.assertEqual(entry['version'], 1)
            self.assertEqual(entry['min_rows'], 1)
            self.assertEqual(entry['sql'], 'SELECT 1 AS id')
        self.assertTrue(any('FOR VERSION AS OF 7' in s for s in statements))

    def test_shared_identity_revocation_before_work(self):
        self.save()
        account = self.root / 'accounts.json'
        account.write_text(json.dumps({'alice': {'role': 'admin'}}))
        def delayed(run_id, steps, actor, role, lock, shared, name):
            account.write_text('{}')
            jobs._worker(run_id, steps, actor, role, lock, shared, name)
        with patch.dict(os.environ, SHIROKUMA_AUTH_MODE='shared', SHIROKUMA_AUTH_FILE=str(account)), \
             patch.object(jobs, '_start_worker', side_effect=delayed), patch.object(b, 'data') as execute:
            run = marts.submit_refresh('orders', 1, 'alice', 'admin')
            self.assertEqual(jobs.get_run(run['id'], 'alice', 'admin')['status'], 'failed')
            execute.assert_not_called()


class MartScheduleTests(unittest.TestCase):
    setUp = MartsTests.setUp
    save = MartsTests.save
    done = MartsTests.done
    def enable(self, **changes):
        payload = dict(name='orders', version=1, enabled=True, kind='interval', every_minutes=1)
        payload.update(changes)
        with patch.object(jobs.wr, 'resolve_role'):
            return marts.save_schedule(payload, 'alice', 'admin')

    def tick_done(self):
        with jobs._db() as connection:
            row = connection.execute("SELECT next_run_at FROM schedules WHERE name='mart:orders'").fetchone()
        result = scheduler.tick(datetime.fromisoformat(row[0]))
        self.assertEqual(len(result['started']), 1, result)
        return self.done(result['started'][0])

    def watched(self):
        return self.save(watch_tables=['ICEBERG.RAW.Input', 'iceberg.staging.other'])

    def test_watch_validation_is_explicit_and_offline(self):
        with patch.object(b.wr, 'trino') as trino:
            target = self.watched()
            self.assertEqual(target['watch_tables'], ['iceberg.raw.input', 'iceberg.staging.other'])
            self.enable()
            trino.assert_not_called()
        for value in ('iceberg.raw.t', ['raw.t'], ['iceberg.marts.t'], ['iceberg.sandbox.orders'],
                      ['iceberg.raw.t$refs'], ['iceberg.information_schema.tables'], [None]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                marts.save_target(dict(name='bad', sql='SELECT 1', watch_tables=value), 'alice', 'admin')
        with self.assertRaises(PermissionError):
            marts.save_schedule(dict(name='orders', enabled=False), 'bob', 'admin')
        with self.assertRaises(PermissionError):
            marts.save_schedule(dict(name='orders', enabled=False), 'alice', 'analyst')
        with self.assertRaises(jobs.JobConflict):
            self.enable(version=2)
        self.assertTrue(marts.list_targets('alice', 'admin')[0]['schedule']['enabled'])
        paused = marts.save_schedule(dict(name='orders', enabled=False), 'alice', 'admin')
        self.assertFalse(paused['enabled'])

    def test_initial_refresh_unchanged_multiple_sources_rollback_and_manual_force(self):
        self.watched()
        snapshots = {'iceberg.raw.input': 7, 'iceberg.staging.other': 11}
        with patch.object(marts, '_source_snapshots', side_effect=lambda watched: dict(snapshots)), \
             patch.object(marts, '_publish_refresh', return_value=dict(valid=True, table='iceberg.marts.orders')) as publish, \
             patch.object(jobs.wr, 'resolve_role'):
            self.enable()
            first = self.tick_done()
            self.assertEqual(first['steps'][0]['summary']['result'], 'refreshed')
            self.assertEqual(b.load_wap_registry()['orders']['watch_baseline']['snapshots'], snapshots)
            first_success = marts.list_targets('alice', 'admin')[0]['last_success_at']
            unchanged = self.tick_done()
            self.assertEqual(unchanged['steps'][0]['summary']['result'], 'unchanged')
            self.assertFalse(unchanged['steps'][0]['summary']['changed'])
            self.assertEqual(publish.call_count, 1)
            self.assertEqual(marts.list_targets('alice', 'admin')[0]['last_success_at'], first_success)
            snapshots['iceberg.staging.other'] = 10  # rollback, not max(snapshot_id)
            self.assertEqual(self.tick_done()['steps'][0]['summary']['result'], 'refreshed')
            self.assertEqual(publish.call_count, 2)
            self.done(marts.submit_refresh('orders', 1, 'alice', 'admin')['id'])
            self.assertEqual(publish.call_count, 3)
            self.assertEqual(self.tick_done()['steps'][0]['summary']['result'], 'unchanged')

    def test_pre_refresh_capture_detects_concurrent_update_next_time(self):
        self.watched()
        snapshots = {'iceberg.raw.input': 7, 'iceberg.staging.other': 11}
        def publish(step):
            snapshots['iceberg.raw.input'] += 1
            return dict(valid=True, table='iceberg.marts.orders')
        with patch.object(marts, '_source_snapshots', side_effect=lambda watched: dict(snapshots)), \
             patch.object(marts, '_publish_refresh', side_effect=publish), patch.object(jobs.wr, 'resolve_role'):
            self.enable()
            self.tick_done()
            self.assertEqual(b.load_wap_registry()['orders']['watch_baseline']['snapshots']['iceberg.raw.input'], 7)
            self.assertEqual(self.tick_done()['steps'][0]['summary']['result'], 'refreshed')

    def test_edit_pauses_invalidates_and_reenable_requires_new_revision(self):
        self.watched()
        with patch.object(marts, '_source_snapshots', return_value={'iceberg.raw.input': 7}), \
             patch.object(marts, '_publish_refresh', return_value=dict(valid=True)), patch.object(jobs.wr, 'resolve_role'):
            self.enable()
            self.tick_done()
        target = self.save(version=1, watch_tables=['iceberg.raw.input'])
        self.assertFalse(target['schedule']['enabled'])
        self.assertNotIn('watch_baseline', b.load_wap_registry()['orders'])
        with self.assertRaises(jobs.JobConflict):
            self.enable()
        self.enable(version=2)
        with self.assertRaises(PermissionError):
            b.save_wap_entry('orders', watch_baseline={})

    def test_failure_preserves_baseline_pauses_and_unknown_requires_manual(self):
        self.watched()
        snapshots = {'iceberg.raw.input': 7}
        with patch.object(marts, '_source_snapshots', side_effect=lambda watched: dict(snapshots)), \
             patch.object(marts, '_publish_refresh', return_value=dict(valid=True)), patch.object(jobs.wr, 'resolve_role'):
            self.enable()
            self.tick_done()
            baseline = b.load_wap_registry()['orders']['watch_baseline']
            snapshots['iceberg.raw.input'] = 8
            with patch.object(marts, '_publish_refresh', side_effect=ValueError('quality failed')):
                self.assertEqual(self.tick_done()['status'], 'failed')
            self.assertEqual(b.load_wap_registry()['orders']['watch_baseline'], baseline)
            self.assertFalse(marts.list_targets('alice', 'admin')[0]['schedule']['enabled'])
            self.enable()
            with patch.object(marts, '_publish_refresh', side_effect=jobs.StepUnknown('publication unknown')):
                self.assertEqual(self.tick_done()['status'], 'unknown')
            with self.assertRaises(ValueError):
                self.enable()
            with patch.object(marts, '_publish_refresh', side_effect=ValueError('still invalid')):
                self.assertEqual(self.done(marts.submit_refresh('orders', 1, 'alice', 'admin')['id'])['status'], 'failed')
            with self.assertRaises(ValueError):
                self.enable()
            # Routine checks, failed manual attempts and newer skipped rows do not
            # hide unknown or expand the rows inspected by the recovery gate.
            with jobs._db() as connection:
                step = dict(name='Check orders', action='mart_refresh')
                definition = dict(name='mart:orders', steps=[step])
                for _ in range(50):
                    check_id = jobs._insert_run(connection, definition, {}, [step], 'alice', 'admin',
                                                status='success', trigger='schedule')
                    connection.execute("UPDATE steps SET status='success',summary=? WHERE run_id=?",
                                       (json.dumps(dict(result='unchanged')), check_id))
                jobs._insert_run(connection, dict(name='mart:orders', steps=[]), {}, [], 'alice', 'admin', status='skipped')
                statements = []
                connection.set_trace_callback(statements.append)
                self.assertTrue(marts._requires_manual('orders', connection))
                connection.set_trace_callback(None)
                query = next(sql for sql in statements if 'FROM runs WHERE' in sql)
                candidates = connection.execute(query).fetchall()
                self.assertEqual(len(candidates), 1)
                self.assertEqual(candidates[0]['status'], 'unknown')
            self.assertTrue(marts.list_targets('alice', 'admin')[0]['requires_manual_refresh'])
            self.done(marts.submit_refresh('orders', 1, 'alice', 'admin')['id'])
            self.assertFalse(marts.list_targets('alice', 'admin')[0]['requires_manual_refresh'])
            self.enable()

    def test_restart_unknown_pauses_no_replay_and_empty_watches_manual(self):
        self.watched()
        self.enable()
        def stopped(run_id, steps, actor, role, lock, *args, **kwargs):
            lock.close()
        with patch.object(jobs, '_start_worker', side_effect=stopped), patch.object(marts, '_publish_refresh') as publish:
            with jobs._db() as connection:
                due = connection.execute("SELECT next_run_at FROM schedules WHERE name='mart:orders'").fetchone()[0]
            result = scheduler.tick(datetime.fromisoformat(due))
            target = marts.list_targets('alice', 'admin')[0]
            self.assertEqual(target['last_run']['status'], 'unknown')
            self.assertFalse(target['schedule']['enabled'])
            self.assertTrue(target['requires_manual_refresh'])
            self.assertEqual(scheduler.tick(datetime.fromisoformat(due) + timedelta(minutes=1))['started'], [])
            publish.assert_not_called()
        self.save(version=1)
        with self.assertRaises(ValueError):
            self.enable(version=2)

    def test_metadata_failure_is_not_unchanged_and_pauses(self):
        self.watched()
        with patch.object(b.wr, 'trino', return_value=contextlib.nullcontext('url')), \
             patch.object(b, 'sql', return_value={'rows': []}), patch.object(marts, '_publish_refresh') as publish, \
             patch.object(jobs.wr, 'resolve_role'):
            self.enable()
            run = self.tick_done()
            self.assertEqual(run['status'], 'failed')
            self.assertFalse(marts.list_targets('alice', 'admin')[0]['schedule']['enabled'])
            self.assertFalse(marts.list_targets('alice', 'admin')[0]['requires_manual_refresh'])
            publish.assert_not_called()

    def test_current_snapshot_prefers_refs_and_fallback_uses_history(self):
        with patch.object(b, 'sql', return_value={'rows': [[5]]}) as sql:
            self.assertEqual(wap.current_snapshot(b, 'url', 'iceberg.raw.t'), 5)
            self.assertIn('$refs', sql.call_args.args[1])
            sql.assert_called_once()
        with patch.object(b, 'sql', side_effect=[ValueError('refs unsupported'), {'rows': [[3]]}]) as sql:
            self.assertEqual(wap.current_snapshot(b, 'url', 'iceberg.raw.t'), 3)
            self.assertIn('$history', sql.call_args.args[1])
        with patch.object(b, 'sql', return_value={'rows': []}), self.assertRaises(RuntimeError):
            wap.current_snapshot(b, 'url', 'iceberg.raw.t')


    def test_late_busy_stale_version_and_removed_mart_do_not_block_other_schedules(self):
        self.watched()
        with patch.object(jobs.wr, 'resolve_role'), patch.object(marts, '_source_snapshots', return_value={'iceberg.raw.input': 7}), \
             patch.object(marts, '_publish_refresh', return_value=dict(valid=True)):
            self.enable()
            with jobs._db() as connection:
                due = datetime.fromisoformat(connection.execute("SELECT next_run_at FROM schedules WHERE name='mart:orders'").fetchone()[0])
            late = scheduler.tick(due + timedelta(seconds=60))
            self.assertEqual(len(late['skipped']), 1)
            self.assertEqual(late['started'], [])
            with jobs._db() as connection:
                connection.execute('BEGIN IMMEDIATE')
                run_id = jobs._insert_run(connection, dict(name='busy', steps=[]), {}, [], 'alice', 'admin')
            held = jobs._lock()
            try:
                with jobs._db() as connection:
                    due = datetime.fromisoformat(connection.execute("SELECT next_run_at FROM schedules WHERE name='mart:orders'").fetchone()[0])
                busy = scheduler.tick(due)
                self.assertEqual(len(busy['skipped']), 1)
                self.assertEqual(busy['started'], [])
            finally:
                with jobs._db() as connection:
                    connection.execute("UPDATE runs SET status='failed' WHERE id=?", (run_id,))
                held.close()
            jobs.save_job(dict(name='zhealthy', steps=[dict(action='sql', sql='SELECT 1', mode='read-only')]), 'alice', 'admin')
            jobs.save_schedule(dict(name='zhealthy', enabled=True, kind='interval', every_minutes=1), 'alice', 'admin')
            with jobs._db() as connection:
                connection.execute("UPDATE schedules SET arguments=? WHERE name='mart:orders'", (json.dumps({'version': 999}),))
                connection.execute('UPDATE schedules SET next_run_at=?', (due.isoformat(),))
            with patch.object(jobs, '_execute_step', return_value=dict(result='success')):
                result = scheduler.tick(due)
                self.assertEqual(result['paused'], ['mart:orders'])
                self.assertEqual(len(result['started']), 1)
                self.done(result['started'][0])
            # A removed registry model is isolated by the same admission path.
            self.enable()
            b.WAP_REGISTRY.write_text('{}')
            with jobs._db() as connection:
                due = datetime.fromisoformat(connection.execute("SELECT next_run_at FROM schedules WHERE name='mart:orders'").fetchone()[0])
            result = scheduler.tick(due)
            self.assertEqual(result['paused'], ['mart:orders'])


    def test_chilled_check_wakes_inside_activity_but_save_stays_offline(self):
        with patch.object(jobs.wr, 'status', return_value={'replicas': 0}) as status, \
             patch.object(jobs.wr, 'transition') as wake:
            self.watched()
            self.enable()
            status.assert_not_called()
            wake.assert_not_called()
            with patch.object(marts, '_source_snapshots', return_value={'iceberg.raw.input': 7}), \
                 patch.object(marts, '_publish_refresh', return_value=dict(valid=True)), patch.object(jobs.wr, 'resolve_role'):
                self.tick_done()
                self.assertEqual(self.tick_done()['steps'][0]['summary']['result'], 'unchanged')
            self.assertEqual(wake.call_count, 2)
            wake.assert_called_with('wake', 'LOCAL')
            self.assertEqual(jobs.wr.warehouse_activity.call_count, 2)


    def test_calendar_kinds_share_validation_and_scheduled_auth_blocks_before_wake(self):
        self.watched()
        choices = [dict(kind='daily', time='09:00', timezone='Asia/Tokyo'),
                   dict(kind='weekly', time='10:00', timezone='UTC', weekdays=[1, 5]),
                   dict(kind='monthly', time='11:00', timezone='UTC', day_of_month='last'),
                   dict(kind='once', time='12:00', timezone='UTC', date='2099-01-01')]
        with patch.object(jobs.wr, 'resolve_role'), patch.object(jobs.wr, 'transition') as wake:
            for choice in choices:
                schedule = marts.save_schedule(dict(name='orders', version=1, enabled=True, **choice), 'alice', 'admin')
                self.assertEqual(schedule['kind'], choice['kind'])
            with patch.dict(os.environ, SHIROKUMA_AUTH_MODE='shared'):
                with jobs._db() as connection:
                    due = datetime.fromisoformat(connection.execute("SELECT next_run_at FROM schedules WHERE name='mart:orders'").fetchone()[0])
                result = scheduler.tick(due)
                self.assertEqual(result['paused'], ['mart:orders'])
                self.assertEqual(result['started'], [])
            wake.assert_not_called()
