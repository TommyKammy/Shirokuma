"""DB scheduler admission and notification contracts over real temporary ledgers."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import importlib
import json
import threading
import unittest
from unittest.mock import patch

import test_db_jobs as job_fixture
import control_store
import data_management as dm
import db_jobs as jobs
import db_schedules as schedules
import query_service as queries
import request_auth
from query_context import QueryError
import schedule_resident as calendar
import warehouse_admission as admission


class ScheduleFixture(unittest.TestCase):
    setUp = job_fixture.DatabaseJobsTests.setUp
    clear_jobs = job_fixture.DatabaseJobsTests.clear_jobs
    definition = job_fixture.DatabaseJobsTests.definition
    save = job_fixture.DatabaseJobsTests.save
    submit = job_fixture.DatabaseJobsTests.submit
    finish = job_fixture.DatabaseJobsTests.finish
    admin = job_fixture.DatabaseJobsTests.admin
    assert_code = job_fixture.DatabaseJobsTests.assert_code

    now = datetime(2026, 10, 23, 0, 0, tzinfo=timezone.utc)

    def schedule(self, job=None, **kw):
        job = job or self.save()
        with patch.object(calendar, '_utc', return_value=self.now):
            return schedules.save_schedule(kw.pop('principal', self.p), kw.pop('context', self.c),
                key=kw.pop('key', 'schedule'), target_kind=kw.pop('target_kind', 'job'),
                target_id=job['id'], target_revision=kw.pop('target_revision', job['revision']),
                expected_revision=kw.pop('expected_revision', 0),
                spec=kw.pop('spec', dict(kind='interval', every_minutes=1)),
                intent=kw.pop('intent', dict(arguments={})), **kw)

    def history(self, schedule):
        return schedules.list_ticks(self.p, self.c, schedule['id'])

    def current(self, schedule):
        return schedules.get_schedule(self.p, self.c, schedule['id'])

class ScheduleTests(ScheduleFixture):
    def test_new_firing_freezes_current_generation_without_rewriting_definitions(self):
        job = self.save()
        schedule = self.schedule(job, enabled=True)
        generation = 'next-' + self.c.generation_id
        with control_store.connect() as db:
            dm.record_generation(db, dm.WarehouseConfigGeneration(generation, 'w', 'apply-next', (), (), (), 'READY'))
            db.execute('UPDATE catalog_bindings SET applied_generation_id=? WHERE database_id=?',
                       (generation, self.c.database_id))
        fresh = self.f.context()
        self.assertEqual(asdict(fresh), dict(asdict(self.c), generation_id=generation))
        def prepare(*, principal, database_id, selected_role):
            decision = request_auth.authorize_database(principal, database_id, selected_role,
                expected_generation_id=generation)
            return decision, {'X-Trino-User': 'fixed_' + database_id, 'Authorization': self.f.credential}
        due = self.now + timedelta(minutes=1)
        with patch('trino_auth.prepare_backend_v2', side_effect=prepare):
            schedules.tick(due)
            with control_store.connect() as db:
                firing = dict(db.execute('SELECT * FROM db_schedule_ticks WHERE schedule_id=?', (schedule['id'],)).fetchone())
                run = dict(db.execute('SELECT * FROM db_job_runs WHERE id=?', (firing['run_id'],)).fetchone())
                self.assertEqual(firing['status'], 'accepted')
                self.assertEqual(json.loads(firing['context']), asdict(fresh))
                self.assertEqual(json.loads(run['context']), asdict(fresh))
                self.assertEqual(json.loads(db.execute('SELECT context FROM db_schedules WHERE id=?', (schedule['id'],)).fetchone()[0]), asdict(self.c))
                self.assertEqual(json.loads(db.execute('SELECT context FROM db_jobs WHERE id=?', (job['id'],)).fetchone()[0]), asdict(self.c))
            jobs._worker(run['id'])
            schedules.tick(due)
        self.assertEqual(schedules.list_ticks(self.p, fresh, schedule['id'])[0]['status'], 'succeeded')
        self.assertTrue(schedules.get_schedule(self.p, fresh, schedule['id'])['enabled'])
        with control_store.connect() as db:
            self.assertEqual(json.loads(db.execute('SELECT context FROM db_job_runs WHERE id=?', (run['id'],)).fetchone()[0]), asdict(fresh))

    def test_old_known_success_after_generation_apply_keeps_schedule_enabled_and_context_frozen(self):
        schedule = self.schedule(enabled=True)
        due = self.now + timedelta(minutes=1)
        schedules.tick(due)
        firing = self.history(schedule)[0]
        jobs._worker(firing['run_id'])
        generation = 'next-' + self.c.generation_id
        with control_store.connect() as db:
            dm.record_generation(db, dm.WarehouseConfigGeneration(generation, 'w', 'apply-next', (), (), (), 'READY'))
            db.execute('UPDATE catalog_bindings SET applied_generation_id=? WHERE database_id=?',
                       (generation, self.c.database_id))
        fresh = self.f.context()
        with patch.object(jobs, 'get_run', side_effect=AssertionError('no rebinding old results')), \
                patch.object(schedules, '_submit', side_effect=AssertionError('no replay')):
            schedules.tick(due)
        self.assertEqual(schedules.list_ticks(self.p, fresh, schedule['id'])[0]['status'], 'succeeded')
        current = schedules.get_schedule(self.p, fresh, schedule['id'])
        self.assertTrue(current['enabled'])
        self.assertIsNone(current['paused_reason'])
        with control_store.connect() as db:
            self.assertEqual(json.loads(db.execute('SELECT context FROM db_schedule_ticks WHERE id=?', (firing['id'],)).fetchone()[0]), asdict(self.c))
            self.assertEqual(json.loads(db.execute('SELECT context FROM db_job_runs WHERE id=?', (firing['run_id'],)).fetchone()[0]), asdict(self.c))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM db_job_runs').fetchone()[0], 1)
        self.assertEqual(len(self.f.calls), 1)

    def test_active_observation_after_generation_apply_passes_original_context(self):
        schedule = self.schedule(enabled=True)
        due = self.now + timedelta(minutes=1)
        schedules.tick(due)
        firing = self.history(schedule)[0]
        generation = 'next-' + self.c.generation_id
        with control_store.connect() as db:
            dm.record_generation(db, dm.WarehouseConfigGeneration(generation, 'w', 'apply-next', (), (), (), 'READY'))
            db.execute('UPDATE catalog_bindings SET applied_generation_id=? WHERE database_id=?',
                       (generation, self.c.database_id))
        fresh = self.f.context()
        get_run = jobs.get_run
        with patch.object(jobs, 'get_run', wraps=get_run) as observe:
            schedules.tick(due)
        self.assertEqual(observe.call_args.args[1], self.c)
        self.assertNotEqual(observe.call_args.args[1], fresh)
        self.assertEqual(schedules.list_ticks(self.p, fresh, schedule['id'])[0]['status'], 'unknown')
        with control_store.connect() as db:
            self.assertEqual(json.loads(db.execute('SELECT context FROM db_job_runs WHERE id=?', (firing['run_id'],)).fetchone()[0]), asdict(self.c))
            self.assertEqual(json.loads(db.execute('SELECT context FROM db_schedule_ticks WHERE id=?', (firing['id'],)).fetchone()[0]), asdict(self.c))
        self.assertEqual(self.f.calls, [])

    def test_new_fire_still_rejects_grant_epoch_and_binding_drift(self):
        for change in ('epoch', 'binding'):
            with self.subTest(change=change):
                job = self.save(self.definition(name=change), key=change)
                schedule = self.schedule(job, key=change, enabled=True)
                with control_store.connect() as db:
                    if change == 'epoch':
                        db.execute('UPDATE effective_grants SET epoch=epoch+1 WHERE principal_id=? AND database_id=?',
                                   (self.p.principal_id, self.c.database_id))
                    else:
                        db.execute('UPDATE catalog_bindings SET desired_revision=2,applied_revision=2,revision=revision+1 WHERE database_id=?',
                                   (self.c.database_id,))
                schedules.tick(self.now + timedelta(minutes=1))
                with control_store.connect() as db:
                    row = db.execute('SELECT * FROM db_schedules WHERE id=?', (schedule['id'],)).fetchone()
                    self.assertFalse(row['enabled'])
                    self.assertEqual(row['paused_reason'], 'DB_ACCESS_REVOKED' if change == 'epoch' else 'STALE_BINDING')
                    self.assertIsNone(db.execute('SELECT run_id FROM db_schedule_ticks WHERE schedule_id=?', (schedule['id'],)).fetchone()[0])
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM db_job_runs').fetchone()[0], 0)
                    if change == 'epoch':
                        db.execute('UPDATE effective_grants SET epoch=? WHERE principal_id=? AND database_id=?',
                                   (self.c.grant_revision, self.p.principal_id, self.c.database_id))
        self.assertEqual(self.f.calls, [])

    def test_malformed_schedule_identifiers_and_target_kinds_do_not_write(self):
        job = self.save()
        with control_store.connect() as db:
            before = tuple(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                           for table in ('db_schedules', 'db_schedule_requests'))
        cases = [dict(schedule_id=value, expected_revision=revision)
                 for value in ('', '   ', False, 0, [], {}, ['id'], {'id': 'value'}, 'x' * 129)
                 for revision in (0, 1)]
        cases += [dict(target_kind=value) for value in ([], {})]
        for fields in cases:
            with self.subTest(fields=fields):
                with self.assertRaises(ValueError):
                    self.schedule(job, **fields)
                with control_store.connect() as db:
                    after = tuple(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                                  for table in ('db_schedules', 'db_schedule_requests'))
                self.assertEqual(after, before)

    def test_backward_clock_during_capture_consumes_slot_without_early_admission(self):
        schedule = self.schedule(enabled=True)
        due = self.now + timedelta(minutes=1)
        clock = [due]
        capture = queries._capture_dataops
        def backward(*args, **kw):
            frozen = capture(*args, **kw)
            clock[0] = due - timedelta(seconds=1)
            return frozen
        with patch.object(calendar, '_utc', side_effect=lambda value=None: value if value is not None else clock[0]), \
                patch.object(queries, '_capture_dataops', side_effect=backward):
            schedules.tick()
        firing = self.history(schedule)[0]
        self.assertEqual((firing['status'], firing['code'], firing['run_id']), ('skipped', 'CLOCK_MOVED', None))
        schedules.tick(due)
        self.assertEqual(len(self.history(schedule)), 1)
        self.assertTrue(self.current(schedule)['enabled'])
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM db_job_runs').fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM warehouse_admissions WHERE kind='job'").fetchone()[0], 0)
        self.assertEqual(self.f.calls, [])

    def test_revoked_accepted_native_failure_settles_unknown_across_regrant_epoch(self):
        schedule = self.schedule(enabled=True)
        due = self.now + timedelta(minutes=1)
        schedules.tick(due)
        firing = self.history(schedule)[0]
        with control_store.connect() as db:
            db.execute("UPDATE effective_grants SET maximum_role='revoked',epoch=epoch+1 WHERE principal_id=? AND database_id=?",
                       (self.p.principal_id, self.c.database_id))
        with self.assertRaises(QueryError):
            schedules.list_ticks(self.p, self.c, schedule['id'])
        schedules.tick(due)
        self.assertEqual(self.history_unchecked(schedule)[0]['status'], 'unknown')
        # The accepted worker closes its canonical run under the revoked grant.
        # Scheduler observation must not require a now-denied public run read.
        jobs._worker(firing['run_id'])
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM db_job_runs WHERE id=?', (firing['run_id'],)).fetchone()[0], 'failed')
        with patch.object(jobs, 'get_run', side_effect=AssertionError('no denied results')), \
                patch.object(schedules, '_submit', side_effect=AssertionError('no replay')):
            schedules.tick(due)
        settled = self.history_unchecked(schedule)[0]
        self.assertEqual((settled['status'], settled['code'], settled['run_id']), ('failed', 'RUN_FAILED', firing['run_id']))
        old_context = self.c
        with control_store.connect() as db:
            db.execute("UPDATE effective_grants SET maximum_role='data_engineer',epoch=epoch+1 WHERE principal_id=? AND database_id=?",
                       (self.p.principal_id, self.c.database_id))
        self.c = self.f.context()
        self.assertNotEqual(self.c.grant_revision, old_context.grant_revision)
        with self.assertRaises(QueryError):
            jobs.get_run(self.p, self.c, firing['run_id'])
        with self.assertRaises(QueryError):
            schedules.get_schedule(self.p, old_context, schedule['id'])
        with patch.object(jobs, 'get_run', side_effect=AssertionError('no denied results')), \
                patch.object(schedules, '_submit', side_effect=AssertionError('no replay')):
            schedules.tick(due + timedelta(minutes=5))
        self.assertEqual(self.history(schedule)[0]['status'], 'failed')
        self.assertFalse(self.current(schedule)['enabled'])
        self.assertEqual(set(self.history(schedule)[0]), {'id', 'schedule_id', 'schedule_revision',
            'target_revision', 'scheduled_for', 'status', 'code', 'run_id'})
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM db_job_runs').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM query_records').fetchone()[0], 0)
        self.assertEqual(self.f.calls, [])

    def test_internal_terminal_lookup_rejects_different_target_or_original_context(self):
        schedule = self.schedule(enabled=True)
        due = self.now + timedelta(minutes=1)
        schedules.tick(due)
        firing = self.history(schedule)[0]
        jobs._worker(firing['run_id'])
        other_job = self.save(self.definition(name='other'), key='other')
        other_run = self.submit(other_job, key='other-run')
        jobs._worker(other_run['id'])
        with control_store.connect() as db:
            tick = dict(db.execute('SELECT * FROM db_schedule_ticks WHERE id=?', (firing['id'],)).fetchone())
            stored = dict(db.execute('SELECT * FROM db_schedules WHERE id=?', (schedule['id'],)).fetchone())
        for invalid in (dict(tick, run_id=other_run['id']),
                        dict(tick, context=json.dumps(dict(asdict(self.c), grant_revision=999)))):
            with patch.object(jobs, 'get_run', side_effect=AssertionError('no mismatched results')):
                self.assert_code('SCHEDULE_RUN_MISMATCH', lambda: schedules._observe(invalid, stored))

    def test_final_admission_deadline_after_slow_capture_and_frozen_injected_time(self):
        schedule = self.schedule(enabled=True)
        due = self.now + timedelta(minutes=1)
        clock = [due]
        capture = queries._capture_dataops
        def slow(*args, **kw):
            frozen = capture(*args, **kw)
            clock[0] = due + timedelta(seconds=60)
            return frozen
        def utc(value=None):
            return value if value is not None else clock[0]
        with patch.object(calendar, '_utc', side_effect=utc), patch.object(queries, '_capture_dataops', side_effect=slow):
            schedules.tick()
        firing = self.history(schedule)[0]
        self.assertEqual((firing['status'], firing['code'], firing['run_id']), ('skipped', 'MISSED', None))
        self.assertTrue(self.current(schedule)['enabled'])
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM db_job_runs').fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM warehouse_admissions WHERE kind='job'").fetchone()[0], 0)
        # An injected time is intentionally frozen despite preflight clock drift.
        clock[0] = due
        with patch.object(calendar, '_utc', side_effect=utc), patch.object(queries, '_capture_dataops', side_effect=slow):
            schedules.tick(due + timedelta(minutes=1))
        self.assertIsNotNone(self.history(schedule)[1]['run_id'])
        self.assertEqual(self.f.calls, [])

    def test_production_clock_refreshes_between_due_schedules(self):
        first = self.schedule(enabled=True)
        second = self.schedule(self.save(self.definition(name='second'), key='second'), enabled=True, key='second-schedule')
        due = self.now + timedelta(minutes=1)
        clock = [due]
        def finish_then_delay(run_id):
            jobs._worker(run_id)
            clock[0] = due + timedelta(seconds=60)
        with patch.object(calendar, '_utc', side_effect=lambda value=None: value if value is not None else clock[0]), \
                patch.object(jobs, '_start', side_effect=finish_then_delay):
            calendar._tick(due, refresh_clock=True)
        ticks = self.history(first) + self.history(second)
        self.assertEqual(sorted(t['status'] for t in ticks), ['skipped', 'succeeded'])
        self.assertEqual([t['code'] for t in ticks if t['status'] == 'skipped'], ['MISSED'])
        self.assertEqual(len(self.f.calls), 1)

    def test_stop_during_capture_rejects_reservation_and_leaves_later_due(self):
        first = self.schedule(enabled=True)
        second = self.schedule(self.save(self.definition(name='second'), key='second'), enabled=True, key='second-schedule')
        stop = threading.Event()
        capture = queries._capture_dataops
        def stopping(*args, **kw):
            frozen = capture(*args, **kw)
            stop.set()
            return frozen
        with patch.object(queries, '_capture_dataops', side_effect=stopping):
            calendar._tick(self.now + timedelta(minutes=1), stop=stop)
        ticks = self.history(first) + self.history(second)
        self.assertEqual(len(ticks), 1)
        self.assertEqual((ticks[0]['status'], ticks[0]['code'], ticks[0]['run_id']), ('skipped', 'STOPPED', None))
        self.assertEqual(self.f.calls, [])

    def test_stop_after_acceptance_preserves_completion_and_later_due(self):
        first = self.schedule(enabled=True)
        second = self.schedule(self.save(self.definition(name='second'), key='second'), enabled=True, key='second-schedule')
        stop = threading.Event()
        def accepted(run_id):
            stop.set()
            jobs._worker(run_id)
        with patch.object(jobs, '_start', side_effect=accepted):
            calendar._tick(self.now + timedelta(minutes=1), stop=stop)
        ticks = self.history(first) + self.history(second)
        self.assertEqual(len(ticks), 1)
        self.assertEqual(ticks[0]['status'], 'succeeded')
        self.assertEqual(len(self.f.calls), 1)

    def test_target_edit_completion_preserves_pause_and_save_receipt_is_observation(self):
        job = self.save()
        saved = self.schedule(job, enabled=True)
        due = self.now + timedelta(minutes=1)
        schedules.tick(due)
        firing = self.history(saved)[0]
        self.save(self.definition(sql='SELECT 2'), key='edit', job_id=job['id'], expected_revision=1)
        jobs._worker(firing['run_id'])
        schedules.tick(due)
        current = self.current(saved)
        self.assertFalse(current['enabled'])
        self.assertEqual(current['paused_reason'], 'TARGET_REVISION_CHANGED')
        self.assertEqual(self.history(saved)[0]['status'], 'succeeded')
        self.assertEqual(self.schedule(job, enabled=True), saved)
        self.assertFalse(self.current(saved)['enabled'])
        self.assert_code('IDEMPOTENCY_CONFLICT', lambda: self.schedule(job, enabled=False))
        self.assertEqual(set(self.history(saved)[0]), {'id', 'schedule_id', 'schedule_revision',
            'target_revision', 'scheduled_for', 'status', 'code', 'run_id'})

    def test_reservation_transaction_rollback_is_isolated(self):
        broken = self.schedule(enabled=True)
        valid = self.schedule(self.save(self.definition(name='valid'), key='valid'), enabled=True, key='valid-schedule')
        following = calendar._next
        # An unexpected preflight failure rolls back its inserted reservation.
        def fail(spec, now, previous=None):
            if spec.get('every_minutes') == 2:
                raise OSError('calendar read failed')
            return following(spec, now, previous)
        with control_store.connect() as db:
            db.execute('UPDATE db_schedules SET spec=? WHERE id=?',
                (json.dumps(dict(kind='interval', every_minutes=2)), broken['id']))
        with patch.object(calendar, '_next', side_effect=fail), self.assertLogs(schedules.__name__, level='ERROR'):
            schedules.tick(self.now + timedelta(minutes=1))
        self.assertFalse(self.current(broken)['enabled'])
        self.assertEqual(self.history(broken), [])
        self.assertIsNotNone(self.history(valid)[0]['run_id'])

    def test_default_offline_cas_receipts_and_current_context_inspection(self):
        job = self.save()
        first = self.schedule(job)
        self.assertFalse(first['enabled'])
        self.assertEqual(first['context'], asdict(self.c))
        self.assertEqual(self.f.calls, [])
        second = self.schedule(job, key='enable', expected_revision=1, schedule_id=first['id'], enabled=True)
        self.assertEqual(second['revision'], 2)
        self.assertEqual(self.schedule(job), first)
        self.assert_code('REVISION_CONFLICT', lambda: self.schedule(job, key='stale', expected_revision=1, schedule_id=first['id']))
        self.assert_code('IDEMPOTENCY_CONFLICT', lambda: self.schedule(job, enabled=True))
        # Inspection ignores saved epoch, but still validates the current request.
        with control_store.connect() as db:
            db.execute('UPDATE db_schedules SET context=? WHERE id=?',
                (json.dumps(dict(asdict(self.c), grant_revision=999, generation_id='old')), first['id']))
        self.assertEqual(self.current(first)['context']['generation_id'], 'old')
        refreshed = self.schedule(job, key='refresh', expected_revision=2, schedule_id=first['id'], enabled=True)
        self.assertEqual(refreshed['context'], asdict(self.c))
        other, admin = self.admin()
        self.assert_code('SCHEDULE_NOT_FOUND', lambda: schedules.get_schedule(other, admin, first['id']))
        self.assertEqual(schedules.list_schedules(self.p, self.f.context(self.f.databases[1])), [])
        self.assert_code('SCHEDULE_NOT_FOUND', lambda: schedules.get_schedule(self.p, self.f.context(self.f.databases[1]), first['id']))

    def test_once_calendar_and_missed_consumption(self):
        schedule = self.schedule(enabled=True, spec=dict(kind='once', date='2026-10-23', time='00:01', timezone='UTC'))
        due = self.now + timedelta(minutes=1)
        schedules.tick(due + timedelta(seconds=60))
        tick = self.history(schedule)[0]
        self.assertEqual((tick['status'], tick['code'], tick['run_id']), ('skipped', 'MISSED', None))
        self.assertFalse(self.current(schedule)['enabled'])
        schedules.tick(due + timedelta(days=1))
        self.assertEqual(len(self.history(schedule)), 1)
        self.assertEqual(self.f.calls, [])
        with patch.object(calendar, '_utc', return_value=due + timedelta(days=1)):
            replay = schedules.save_schedule(self.p, self.c, key='schedule', target_kind='job',
                target_id=schedule['target_id'], target_revision=1, expected_revision=0,
                spec=dict(kind='once', date='2026-10-23', time='00:01', timezone='UTC'),
                intent=dict(arguments={}), enabled=True)
        self.assertEqual(replay, schedule)
        self.assertFalse(self.current(schedule)['enabled'])
        for spec, start, expected in [
            (dict(kind='monthly', time='12:00', timezone='UTC', day_of_month='last'),
             datetime(2026, 2, 1, tzinfo=timezone.utc), datetime(2026, 2, 28, 12, tzinfo=timezone.utc)),
            (dict(kind='daily', time='02:30', timezone='America/New_York'),
             datetime(2026, 3, 8, 0, tzinfo=timezone.utc), datetime(2026, 3, 9, 6, 30, tzinfo=timezone.utc)),
            (dict(kind='weekly', time='09:00', timezone='Asia/Tokyo', weekdays=[1]),
             self.now, datetime(2026, 10, 26, tzinfo=timezone.utc)),
        ]:
            self.assertEqual(calendar._next(spec, start), expected)

    def test_duplicate_ticks_and_restart_never_resubmit(self):
        schedule = self.schedule(enabled=True)
        due = self.now + timedelta(minutes=1)
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(schedules.tick, [due] * 4))
        ticks = self.history(schedule)
        self.assertEqual(len(ticks), 1)
        self.assertIsNotNone(ticks[0]['run_id'])
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM db_job_runs').fetchone()[0], 1)
        jobs._worker(ticks[0]['run_id'])
        importlib.reload(schedules)
        schedules.tick(due)
        self.assertEqual(self.history(schedule)[0]['status'], 'succeeded')
        self.assertEqual(len(self.f.calls), 1)

    def test_reserved_crash_is_unknown_and_manual_enable_required(self):
        schedule = self.schedule(enabled=True)
        class Crash(BaseException):
            pass
        with patch.object(schedules, '_submit', side_effect=Crash):
            with self.assertRaises(Crash):
                schedules.tick(self.now + timedelta(minutes=1))
        self.assertEqual(self.history(schedule)[0]['status'], 'reserved')
        importlib.reload(schedules)
        schedules.tick(self.now + timedelta(minutes=2))
        self.assertEqual(self.history(schedule)[0]['status'], 'unknown')
        self.assertFalse(self.current(schedule)['enabled'])
        self.assertEqual(self.f.calls, [])
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM db_job_runs').fetchone()[0], 0)

    def test_lost_ack_preserves_link_and_observes_without_replay(self):
        schedule = self.schedule(enabled=True)
        submit = jobs.submit_run
        def lose(*args, **kw):
            submit(*args, **kw)
            raise OSError('lost ACK')
        with patch.object(jobs, 'submit_run', side_effect=lose):
            schedules.tick(self.now + timedelta(minutes=1))
        tick = self.history(schedule)[0]
        self.assertEqual(tick['status'], 'accepted')
        jobs._worker(tick['run_id'])
        with patch.object(jobs, 'submit_run', side_effect=AssertionError('replay')):
            schedules.tick(self.now + timedelta(minutes=1))
        self.assertEqual(self.history(schedule)[0]['status'], 'succeeded')

    def test_target_and_schedule_edit_after_preflight_reject_admission(self):
        for edit_target in (True, False):
            job = self.save(self.definition(name='race' + str(int(edit_target))), key='job' + str(edit_target))
            schedule = self.schedule(job, enabled=True, key='s' + str(edit_target))
            capture = queries._capture_dataops
            def race(*args, **kw):
                result = capture(*args, **kw)
                if edit_target:
                    self.save(self.definition(name='race1', sql='SELECT 2'), key='edit-target', job_id=job['id'], expected_revision=1)
                else:
                    self.schedule(job, key='disable', expected_revision=1, schedule_id=schedule['id'], enabled=False)
                return result
            with patch.object(queries, '_capture_dataops', side_effect=race):
                schedules.tick(self.now + timedelta(minutes=1))
            self.assertIsNone(self.history(schedule)[0]['run_id'])
            self.assertFalse(self.current(schedule)['enabled'])
        self.assertEqual(self.f.calls, [])
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM db_job_runs').fetchone()[0], 0)

    def test_current_grant_role_and_auth_mode_revoke_pause(self):
        for change in ('grant', 'role', 'mode'):
            with self.subTest(change=change):
                job = self.save(self.definition(name=change), key='job' + change)
                schedule = self.schedule(job, enabled=True, key=change)
                if change == 'grant':
                    with control_store.connect() as db:
                        grants = [dict(r) for r in db.execute('SELECT * FROM effective_grants WHERE principal_id=?', (self.p.principal_id,))]
                        db.execute('DELETE FROM effective_grants WHERE principal_id=?', (self.p.principal_id,))
                elif change == 'role':
                    self.f.fixture.users['alice']['role'] = 'bi_viewer'
                    self.f.fixture.save()
                else:
                    with control_store.connect() as db:
                        db.execute("UPDATE db_schedules SET auth_mode='local' WHERE id=?", (schedule['id'],))
                schedules.tick(self.now + timedelta(minutes=1))
                with control_store.connect() as db:
                    row = db.execute('SELECT * FROM db_schedules WHERE id=?', (schedule['id'],)).fetchone()
                    self.assertEqual(row['enabled'], 0)
                self.assertIsNone(self.history_unchecked(schedule)[0]['run_id'])
                self.f.fixture.users['alice']['role'] = 'data_engineer'
                self.f.fixture.save()
                if change == 'grant':
                    with control_store.connect() as db:
                        for grant in grants:
                            columns = ','.join(grant)
                            placeholders = ','.join('?' for _ in grant)
                            db.execute('INSERT INTO effective_grants (' + columns + ') VALUES (' + placeholders + ')', tuple(grant.values()))
        self.assertEqual(self.f.calls, [])

    def history_unchecked(self, schedule):
        with control_store.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM db_schedule_ticks WHERE schedule_id=?', (schedule['id'],))]

    def test_drain_consumes_due_and_accepted_work_completes(self):
        first = self.schedule(enabled=True)
        due = self.now + timedelta(minutes=1)
        schedules.tick(due)
        accepted = self.history(first)[0]
        other = self.save(self.definition(name='other'), key='other')
        second = self.schedule(other, enabled=True, key='second')
        with control_store.connect() as db:
            dm.record_operation(db, dm.Operation('drain', 'alice', 'test', (), None, 'RUNNING', (), None, (), ()))
            admission.begin_drain(db, warehouse_id='w', operation_id='drain')
        schedules.tick(due)
        self.assertEqual(self.history(second)[0]['code'], 'DRAIN')
        jobs._worker(accepted['run_id'])
        schedules.tick(due)
        self.assertEqual(self.history(first)[0]['status'], 'succeeded')

    def test_fault_isolation_and_busy_consumes_occurrence(self):
        broken = self.schedule(enabled=True)
        other = self.save(self.definition(name='other'), key='other')
        valid = self.schedule(other, key='valid', enabled=True)
        with control_store.connect() as db:
            db.execute("UPDATE db_schedules SET spec='{}' WHERE id=?", (broken['id'],))
        due = self.now + timedelta(minutes=1)
        schedules.tick(due)
        self.assertFalse(self.current(broken)['enabled'])
        self.assertIsNotNone(self.history(valid)[0]['run_id'])
        schedules.tick(due + timedelta(minutes=1))
        self.assertEqual(self.history(valid)[1]['code'], 'JOB_BUSY')
        self.assertTrue(self.current(valid)['enabled'])

    def test_ingest_rejected_and_failed_unknown_runs_pause(self):
        p, c = self.admin()
        job = self.save(self.definition(name='upload', steps=[dict(name='upload', action='ingest', input='file',
            mode='create', destination=asdict(self.f.locator('upload')))]), principal=p, context=c)
        with self.assertRaisesRegex(ValueError, 'runtime uploads'):
            self.schedule(job, principal=p, context=c)
        job = self.save(self.definition(name='failure'), key='failure')
        schedule = self.schedule(job, enabled=True)
        schedules.tick(self.now + timedelta(minutes=1))
        run_id = self.history(schedule)[0]['run_id']
        with patch.object(queries, '_transport', return_value={'id': 'fail', 'error': {'message': 'failed'}}):
            jobs._worker(run_id)
        schedules.tick(self.now + timedelta(minutes=1))
        self.assertFalse(self.current(schedule)['enabled'])
        self.assertIn(self.history(schedule)[0]['status'], ('failed', 'unknown'))


class AlertTests(ScheduleFixture):
    # Only notification cases use this fixture; scheduler cases remain above.
    def alert_job(self, name='alert'):
        return self.save(self.definition(name=name, steps=[dict(name='condition', action='alert', sql='SELECT TRUE')]))

    def evaluate(self, job, data, key):
        run = self.submit(job, key=key)
        with patch.object(queries, '_transport', return_value={'id': key, 'data': data}):
            result = self.finish(run)
        return run, result

    def test_strict_boolean_dedup_failure_retention_and_false(self):
        job = self.alert_job()
        _, result = self.evaluate(job, [[True]], 'true')
        self.assertEqual(result['status'], 'succeeded')
        alerts = schedules.list_alerts(self.p, self.c)
        self.assertEqual(alerts['unacknowledged_count'], 1)
        alert = alerts['alerts'][0]
        self.assertEqual(alert['context'], asdict(self.c))
        for i, data in enumerate(([[None]], [[True], [False]], [[1]], [[True, False]], [])):
            _, result = self.evaluate(job, data, 'invalid' + str(i))
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(schedules.list_alerts(self.p, self.c), alerts)
        self.evaluate(job, [[True]], 'true-again')
        self.assertEqual(schedules.list_alerts(self.p, self.c)['alerts'][0]['occurrences'], 2)
        acknowledged = schedules.acknowledge_alert(self.p, self.c, alert['id'], key='ack')
        self.assertEqual(schedules.acknowledge_alert(self.p, self.c, alert['id'], key='ack'), acknowledged)
        self.assertEqual(schedules.list_alerts(self.p, self.c)['unacknowledged_count'], 0)
        self.evaluate(job, [[False]], 'false')
        self.assertIsNotNone(schedules.list_alerts(self.p, self.c)['alerts'][0]['resolved_at'])
        self.evaluate(job, [[True]], 'reopen')
        self.assertEqual(len(schedules.list_alerts(self.p, self.c)['alerts']), 2)

    def test_notification_owner_database_role_and_ack_grants(self):
        job = self.alert_job()
        self.evaluate(job, [[True]], 'true')
        alert = schedules.list_alerts(self.p, self.c)['alerts'][0]
        p, c = self.admin()
        self.assertEqual(schedules.list_alerts(p, c)['alerts'], [])
        self.assert_code('SCHEDULE_NOT_FOUND', lambda: schedules.acknowledge_alert(p, c, alert['id'], key='other'))
        self.assertEqual(schedules.list_alerts(self.p, self.f.context(self.f.databases[1]))['alerts'], [])
        with control_store.connect() as db:
            db.execute('DELETE FROM effective_grants WHERE principal_id=?', (self.p.principal_id,))
        with self.assertRaises(QueryError):
            schedules.acknowledge_alert(self.p, self.c, alert['id'], key='revoked')
        with control_store.connect() as db:
            self.assertIsNone(db.execute('SELECT acknowledged_at FROM db_alerts').fetchone()[0])

    def test_delayed_old_completion_and_definition_retirement(self):
        job = self.alert_job()
        older, _ = self.evaluate(job, [[True]], 'old')
        newer, _ = self.evaluate(job, [[False]], 'new')
        step = job['definition']['steps'][0]
        schedules.record_alert(self.p, self.c, older['id'], 1, step, True)
        self.assertEqual(schedules.list_alerts(self.p, self.c)['unacknowledged_count'], 0)
        self.evaluate(job, [[True]], 'latest')
        self.save(self.definition(name='alert', steps=[dict(step, sql='SELECT FALSE')]), key='edit', job_id=job['id'], expected_revision=1)
        schedules.record_alert(self.p, self.c, newer['id'], 1, step, True)
        self.assertEqual(schedules.list_alerts(self.p, self.c)['unacknowledged_count'], 0)
        with control_store.connect() as db:
            records = [dict(r) for r in db.execute('SELECT * FROM db_alerts')]
        self.assertNotIn('"data"', json.dumps(records))
        self.assertEqual(records[-1]['resolution'], 'definition_changed')

    def test_initial_false_cursor_blocks_delayed_true_without_prior_notification(self):
        job = self.alert_job()
        old, _ = self.evaluate(job, [[False]], 'old-false')
        new, _ = self.evaluate(job, [[False]], 'new-false')
        self.assertEqual(schedules.list_alerts(self.p, self.c), dict(alerts=[], unacknowledged_count=0))
        schedules.record_alert(self.p, self.c, old['id'], 1, job['definition']['steps'][0], True)
        self.assertEqual(schedules.list_alerts(self.p, self.c), dict(alerts=[], unacknowledged_count=0))
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT acceptance_order FROM db_alert_subjects').fetchone()[0],
                db.execute('SELECT rowid FROM db_job_runs WHERE id=?', (new['id'],)).fetchone()[0])

    def test_sql_and_authorization_failure_retain_old_notification(self):
        job = self.alert_job()
        self.evaluate(job, [[True]], 'true')
        original = schedules.list_alerts(self.p, self.c)
        run = self.submit(job, key='sql-failure')
        with patch.object(queries, '_transport', return_value={'id': 'failed', 'error': {'message': 'SQL failed'}}):
            self.assertEqual(self.finish(run)['status'], 'failed')
        self.assertEqual(schedules.list_alerts(self.p, self.c), original)
        run = self.submit(job, key='revocation')
        self.f.fixture.users['alice']['role'] = 'bi_viewer'
        self.f.fixture.save()
        jobs._worker(run['id'])
        self.f.fixture.users['alice']['role'] = 'data_engineer'
        self.f.fixture.save()
        self.assertEqual(schedules.list_alerts(self.p, self.c), original)


class ScheduledScanTests(unittest.TestCase):
    now = ScheduleFixture.now

    def setUp(self):
        import test_db_stage_scans
        self.f = test_db_stage_scans.ScanTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def save_schedule(self, **kw):
        with patch.object(calendar, '_utc', return_value=self.now):
            return schedules.save_schedule(self.f.p, self.f.c, key=kw.pop('key', 'schedule'),
                target_kind='stage', target_id=self.f.stage['id'], target_revision=self.f.stage['revision'],
                expected_revision=kw.pop('expected_revision', 0), spec=dict(kind='interval', every_minutes=1),
                intent=dict(action='scan'), enabled=True, **kw)

    def test_scan_content_dedup_is_inherited(self):
        import db_stage_scans as scans
        schedule = self.save_schedule()
        for minute in (1, 2):
            schedules.tick(self.now + timedelta(minutes=minute))
            ticks = schedules.list_ticks(self.f.p, self.f.c, schedule['id'])
            scans._execute(ticks[-1]['run_id'])
            schedules.tick(self.now + timedelta(minutes=minute))
        ticks = schedules.list_ticks(self.f.p, self.f.c, schedule['id'])
        self.assertEqual([t['status'] for t in ticks], ['succeeded', 'succeeded'])
        second = scans.get(self.f.p, self.f.c, self.f.stage['id'], ticks[-1]['run_id'])
        self.assertEqual(second['items'][0]['status'], 'skipped')
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM db_stage_runs').fetchone()[0], 1)

    def test_slow_manifest_deadline_rejected_inside_native_transaction(self):
        import db_stage_scans as scans
        schedule = self.save_schedule()
        due = self.now + timedelta(minutes=1)
        clock = [due]
        manifest = scans._manifest
        def slow(row):
            files = manifest(row)
            clock[0] = due + timedelta(seconds=60)
            return files
        with patch.object(calendar, '_utc', side_effect=lambda value=None: value if value is not None else clock[0]), \
                patch.object(scans, '_manifest', side_effect=slow):
            schedules.tick()
        tick = schedules.list_ticks(self.f.p, self.f.c, schedule['id'])[0]
        self.assertEqual((tick['status'], tick['code'], tick['run_id']), ('skipped', 'MISSED', None))
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM db_stage_scans').fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM warehouse_admissions WHERE kind='stage_scan'").fetchone()[0], 0)
        self.assertEqual(self.f.b.calls, [])

    def test_scan_target_edit_during_preflight_rejects_without_parent(self):
        import db_stages as stages
        schedule = self.save_schedule()
        capture = queries._capture_dataops
        def race(*args, **kw):
            frozen = capture(*args, **kw)
            self.f.stage = stages.save_stage(self.f.p, self.f.c, key='edit', expected_revision=1,
                stage_id=self.f.stage['id'], definition=self.f.definition_value)
            return frozen
        with patch.object(queries, '_capture_dataops', side_effect=race):
            schedules.tick(self.now + timedelta(minutes=1))
        tick = schedules.list_ticks(self.f.p, self.f.c, schedule['id'])[0]
        self.assertIsNone(tick['run_id'])
        self.assertFalse(schedules.get_schedule(self.f.p, self.f.c, schedule['id'])['enabled'])
        self.assertEqual(self.f.b.calls, [])
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM db_stage_scans').fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM warehouse_admissions WHERE kind='stage_scan'").fetchone()[0], 0)

    def test_scan_failed_content_pauses_and_restart_has_no_resend(self):
        import db_stage_scans as scans
        self.f.path.write_bytes(b'id,label\n1,too,many\n')
        schedule = self.save_schedule()
        due = self.now + timedelta(minutes=1)
        schedules.tick(due)
        tick = schedules.list_ticks(self.f.p, self.f.c, schedule['id'])[0]
        scans._execute(tick['run_id'])
        schedules.tick(due)
        self.assertFalse(schedules.get_schedule(self.f.p, self.f.c, schedule['id'])['enabled'])
        self.assertEqual(schedules.list_ticks(self.f.p, self.f.c, schedule['id'])[0]['status'], 'failed')
        importlib.reload(schedules)
        schedules.tick(due + timedelta(minutes=1))
        self.assertEqual(len(schedules.list_ticks(self.f.p, self.f.c, schedule['id'])), 1)

    def test_scan_unknown_child_pauses_and_keeps_link(self):
        import db_stage_scans as scans
        schedule = self.save_schedule()
        due = self.now + timedelta(minutes=1)
        schedules.tick(due)
        tick = schedules.list_ticks(self.f.p, self.f.c, schedule['id'])[0]
        with patch.object(queries, '_transport', side_effect=OSError('lost POST')):
            scans._execute(tick['run_id'])
        schedules.tick(due)
        observed = schedules.list_ticks(self.f.p, self.f.c, schedule['id'])[0]
        self.assertEqual(observed['run_id'], tick['run_id'])
        self.assertEqual(observed['status'], 'unknown')
        self.assertFalse(schedules.get_schedule(self.f.p, self.f.c, schedule['id'])['enabled'])


class ScheduledMartTests(unittest.TestCase):
    now = ScheduleFixture.now

    def setUp(self):
        import test_db_marts
        self.f = test_db_marts.MartTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.mart, self.source = self.f.create()

    def save_schedule(self, **kw):
        with patch.object(calendar, '_utc', return_value=kw.pop('now', self.now)):
            return schedules.save_schedule(self.f.owner, self.f.context, key=kw.pop('key', 'schedule'),
                target_kind='mart', target_id=self.mart['id'], target_revision=self.mart['revision'],
                expected_revision=kw.pop('expected_revision', 0), spec=dict(kind='interval', every_minutes=1),
                intent=dict(action='refresh', check_changes=True), enabled=True, **kw)

    def test_refresh_and_unchanged_keep_baseline_and_schedule_enabled(self):
        import db_marts as marts
        schedule = self.save_schedule()
        first = self.now + timedelta(minutes=1)
        schedules.tick(first)
        initial = marts.get_mart(self.f.owner, self.f.context, self.mart['id'])
        self.assertIsNotNone(initial['last_success_baseline'])
        paused = schedules.get_schedule(self.f.owner, self.f.context, schedule['id'])
        # First publication replaces a locator with the actual materialized Ref
        # and increments the target definition revision. Rebinding is explicit.
        self.assertFalse(paused['enabled'])
        self.assertEqual(paused['paused_reason'], 'TARGET_REVISION_CHANGED')
        self.mart = initial
        self.save_schedule(key='rebind', expected_revision=1, schedule_id=schedule['id'], now=first)
        schedules.tick(first + timedelta(minutes=1))
        ticks = schedules.list_ticks(self.f.owner, self.f.context, schedule['id'])
        self.assertEqual([t['status'] for t in ticks], ['succeeded', 'skipped'])
        self.assertEqual(ticks[1]['code'], 'NO_CHANGES')
        current = marts.get_mart(self.f.owner, self.f.context, self.mart['id'])
        self.assertEqual(current['last_success_at'], initial['last_success_at'])
        self.assertTrue(schedules.get_schedule(self.f.owner, self.f.context, schedule['id'])['enabled'])
        with control_store.connect() as db:
            run = json.loads(db.execute('SELECT record FROM db_mart_runs WHERE id=?', (ticks[1]['run_id'],)).fetchone()[0])
        self.assertTrue(run['check_changes'])

    def test_mart_definition_edit_between_capture_and_admit_has_no_run(self):
        import db_marts as marts
        schedule = self.save_schedule()
        capture = queries._capture_dataops
        def race(*args, **kw):
            frozen = capture(*args, **kw)
            marts.save_mart(self.f.owner, self.f.context, key='edit', mart_id=self.mart['id'],
                expected_revision=1, definition=self.mart['definition'])
            return frozen
        with patch.object(queries, '_capture_dataops', side_effect=race):
            schedules.tick(self.now + timedelta(minutes=1))
        tick = schedules.list_ticks(self.f.owner, self.f.context, schedule['id'])[0]
        self.assertIsNone(tick['run_id'])
        self.assertFalse(schedules.get_schedule(self.f.owner, self.f.context, schedule['id'])['enabled'])
        self.assertEqual(self.f.b.calls, [])
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM db_mart_runs').fetchone()[0], 0)

    def test_mart_network_unknown_pauses_and_no_republish(self):
        schedule = self.save_schedule()
        due = self.now + timedelta(minutes=1)
        with patch.object(queries, '_transport', side_effect=OSError('lost POST')):
            schedules.tick(due)
        tick = schedules.list_ticks(self.f.owner, self.f.context, schedule['id'])[0]
        self.assertIsNotNone(tick['run_id'])
        self.assertIn(tick['status'], ('unknown', 'reconciliation_required'))
        self.assertFalse(schedules.get_schedule(self.f.owner, self.f.context, schedule['id'])['enabled'])
        with patch.object(queries, '_transport', side_effect=AssertionError('no replay')):
            schedules.tick(due + timedelta(minutes=1))
        self.assertEqual(len(schedules.list_ticks(self.f.owner, self.f.context, schedule['id'])), 1)


if __name__ == '__main__':
    unittest.main()
