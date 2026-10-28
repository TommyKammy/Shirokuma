"""Manual batch behavior on isolated SQLite state and the real P3 boundary."""
from dataclasses import asdict
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid

import test_db_stages as fixtures
import test_manual_ingest as ingest_fixture
import control_store
import data_management as dm
import dataops_service as ds
import db_stage_scans as scans
import db_stages as stages
from query_context import QueryError
import warehouse_admission as admission


class ScanTests(fixtures.StageFixture, unittest.TestCase):

    def test_retiring_scan_drains_all_files_and_public_scan_is_denied(self):
        (self.directory / 'second.csv').write_bytes(b'id,label\n8,second\n')
        scan = self.scan()
        self.b.retire()
        with self.assertRaises((QueryError, PermissionError)):
            self.observe(scan)
        scans._execute(scan['id'])
        with control_store.connect() as db:
            row = db.execute('SELECT record FROM db_stage_scans WHERE id=?', (scan['id'],)).fetchone()
            self.assertEqual(json.loads(row[0])['status'], 'succeeded')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM db_stage_scan_operations').fetchone()[0], 2)
        self.b.assert_database_admissions()

    def test_retiring_scan_cancel_preserves_exact_context_without_results(self):
        scan = self.scan()
        self.b.retire()
        result = scans.cancel(self.p, self.c, self.stage['id'], scan['id'],
                              key='retire-cancel', expected_revision=1)
        self.assertEqual(set(result), {'id', 'status'})
        with self.assertRaises(QueryError):
            scans.cancel(self.p, fixtures.replace(self.c, database_id=self.b.databases[1]),
                         self.stage['id'], scan['id'], key='wrong-db-cancel', expected_revision=1)
        scans._execute(scan['id'])
        self.assertFalse(self.b.calls)

    def setUp(self):
        self.stage_setup(ingest_fixture.ManualIngestTests())
        self.target = self.b.existing('scan_target')
        self.definition_value = self.definition(mode='append', atomic_append=True, target=asdict(self.target))
        self.definition_value.pop('destination')
        self.stage = self.save(definition=self.definition_value)
        self.start_scan = patch.object(scans, '_start')
        self.start_scan.start()
        self.addCleanup(self.start_scan.stop)
        self.start_retry = patch.object(scans, '_start_retry')
        self.start_retry.start()
        self.addCleanup(self.start_retry.stop)
        self.addCleanup(self.close_scans)

    def close_scans(self):
        for ident, live in list(scans._live.items()):
            live[-1].close()
            scans._live.pop(ident, None)

    def scan(self, key='scan'):
        return scans.submit(self.p, self.c, self.stage['id'], key=key, expected_revision=self.stage['revision'])

    def observe(self, scan):
        return scans.get(self.p, self.c, self.stage['id'], scan['id'])

    def execute_scan(self, scan, retry_item=None):
        scans._execute(scan['id'], retry_item)
        return self.observe(scan)

    def history(self, scan):
        with control_store.connect() as db:
            row = db.execute('SELECT * FROM db_stage_scans WHERE id=?', (scan['id'],)).fetchone()
            return (tuple(row), [tuple(r) for r in scans._items(db, scan['id'])],
                    tuple(db.execute('SELECT * FROM warehouse_admissions WHERE id=?', (row['admission_id'],)).fetchone()))

    def test_sorted_frozen_manifest_and_sequential_success(self):
        (self.directory / 'z.csv').write_bytes(b'id,label\n7,last\n')
        scan = self.scan()
        self.assertEqual([i['file'] for i in scan['items']], ['data.csv', 'z.csv'])
        self.path.write_bytes(b'id,label\n100,changed\n')
        (self.directory / 'z.csv').unlink()
        result = self.execute_scan(scan)
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual([i['status'] for i in result['items']], ['succeeded', 'succeeded'])
        self.assertTrue(result['committed'])
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM db_stage_scan_operations').fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM warehouse_admissions WHERE kind='stage_scan' AND state='FINISHED'").fetchone()[0], 1)
            summaries = [json.loads(r[0])['summary']['committed_rows'] for r in db.execute('SELECT record FROM db_stage_runs ORDER BY rowid')]
        self.assertEqual(summaries, [3, 1])

    def test_failure_keeps_commit_stops_later_and_requires_explicit_retry(self):
        (self.directory / 'm.csv').write_bytes(b'id,label\n1,too,many\n')
        (self.directory / 'z.csv').write_bytes(b'id,label\n7,last\n')
        result = self.execute_scan(self.scan())
        self.assertEqual(result['status'], 'partial', result)
        self.assertEqual([i['status'] for i in result['items']], ['succeeded', 'failed', 'not_started'])
        item = result['items'][1]
        self.assertTrue(item['retryable'])
        again = self.execute_scan(self.scan('new-scan'))
        self.assertEqual([i['status'] for i in again['items']], ['skipped', 'failed', 'not_started'])
        with self.assertRaises(QueryError) as caught:
            scans.retry(self.p, self.c, self.stage['id'], result['id'], key='bad-cas', expected_revision=1,
                        item_id=item['id'], expected_attempt=0)
        self.assertEqual(caught.exception.code, 'STAGE_SCAN_ATTEMPT_CONFLICT')
        history = self.history(result)
        accepted = scans.retry(self.p, self.c, self.stage['id'], result['id'], key='retry', expected_revision=1,
                               item_id=item['id'], expected_attempt=1)
        self.assertNotEqual(accepted['id'], result['id'])
        self.assertEqual((accepted['source_scan_id'], accepted['source_item_id']), (result['id'], item['id']))
        self.assertEqual(len(accepted['items']), 1)
        self.assertEqual(accepted['items'][0]['attempt'], 2)
        retried = self.execute_scan(accepted)
        self.assertEqual(retried['items'][0]['attempt'], 2)
        self.assertNotEqual(item['run_id'], retried['items'][0]['run_id'])
        self.assertEqual(self.history(result), history)
        replay = scans.retry(self.p, self.c, self.stage['id'], result['id'], key='retry', expected_revision=1,
                             item_id=item['id'], expected_attempt=1)
        self.assertEqual(replay['id'], retried['id'])
        self.assertEqual(replay['items'][0]['run_id'], retried['items'][0]['run_id'])

    def test_failed_backend_retry_can_succeed_and_changed_bytes_are_new(self):
        def fail(run_id, execution):
            execution.fail()
            with control_store.connect() as db:
                stages._write_run(db, run_id, status='failed')
        with patch.object(stages, '_execute_run', side_effect=fail):
            result = self.execute_scan(self.scan())
        item = result['items'][0]
        self.assertTrue(item['retryable'])
        accepted = scans.retry(self.p, self.c, self.stage['id'], result['id'], key='retry', expected_revision=1,
                               item_id=item['id'], expected_attempt=1)
        result = self.execute_scan(accepted)
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(result['items'][0]['attempt'], 2)
        self.path.write_bytes(b'id,label\n9,new\n')
        self.assertEqual(self.execute_scan(self.scan('new'))['items'][0]['status'], 'succeeded')

    def test_schema14_manual_evidence_rename_mtime_generation_refresh_and_safe_duplicate(self):
        manual = self.execute(self.submit(self.stage))
        self.assertTrue(manual['committed'], manual)
        with control_store.connect() as db:
            original = db.execute('SELECT record FROM db_stage_runs WHERE id=?', (manual['id'],)).fetchone()[0]
            from test_control_store import drop_creation_schema
            drop_creation_schema(db)
            for table in ('db_stage_scan_operations', 'db_stage_scan_items', 'db_stage_scans', 'db_stage_content'):
                db.execute('DROP TABLE ' + table)
            db.execute('PRAGMA user_version=14')
        with control_store.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], control_store.VERSION)
            self.assertEqual(db.execute('SELECT record FROM db_stage_runs WHERE id=?', (manual['id'],)).fetchone()[0], original)
        self.path.rename(self.directory / 'renamed.csv')
        os.utime(self.directory / 'renamed.csv', (10, 10))
        self.revoke()
        self.regrant(new_generation=True)
        self.stage = self.save(key='refresh', stage_id=self.stage['id'], expected_revision=1, definition=self.definition_value)
        result = self.execute_scan(self.scan())
        self.assertEqual(result['items'][0]['status'], 'skipped', result)
        self.assertNotIn('run_id', result['items'][0])
        duplicate = self.submit(self.stage, key='duplicate')
        self.assertEqual(duplicate, dict(id=None, status='skipped', committed=True, deduplicated=True, context=asdict(self.c)))
        self.assertEqual(self.submit(self.stage, key='duplicate'), duplicate)
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT record FROM db_stage_runs WHERE id=?', (manual['id'],)).fetchone()[0], original)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM dataops_operations').fetchone()[0], 1)

    def test_modes_rejected_before_dispatch_and_single_file_create_still_works(self):
        for index, changes in enumerate((dict(mode='create'), dict(mode='replace'), dict(atomic_append=False))):
            definition = dict(self.definition_value, **changes)
            if definition['mode'] == 'create':
                definition.pop('target')
                definition['destination'] = asdict(self.b.locator('new_target'))
            self.stage = self.save(key='edit' + str(index), stage_id=self.stage['id'],
                                   expected_revision=self.stage['revision'], definition=definition)
            self.code('STAGE_SCAN_MODE_REJECTED', lambda: self.scan(str(index)))
        self.assertEqual(self.b.calls, [])

    def test_config_target_keys_and_stage_identity_distinguish_content(self):
        raw = self.path.read_bytes()
        key = scans.content_key(self.c, self.stage['id'], self.definition_value, 'data.csv', raw)
        self.assertEqual(key, scans.content_key(self.c, self.stage['id'], dict(self.definition_value, name='renamed'), 'other.csv', raw))
        for definition in (dict(self.definition_value, batch_size=1), dict(self.definition_value, encoding='cp932'),
                           dict(self.definition_value, schema_hints={'id': 'INTEGER'}),
                           dict(self.definition_value, mode='upsert', key_columns=['id']),
                           dict(self.definition_value, target=asdict(self.b.existing('other_target')))):
            self.assertNotEqual(key, scans.content_key(self.c, self.stage['id'], definition, 'data.csv', raw))
        self.assertNotEqual(key, scans.content_key(self.c, str(uuid.uuid4()), self.definition_value, 'data.csv', raw))

    def test_parent_held_between_files_drain_and_global_job_cap(self):
        (self.directory / 'z.csv').write_bytes(b'id,label\n7,last\n')
        scan = self.scan()
        original = scans._admit_file
        seen = []
        def check(*args, **kwargs):
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                with self.assertRaises(admission.AdmissionConflict):
                    admission.admit(db, warehouse_id='other', actor='other', kind='job', subject_id=str(uuid.uuid4()))
                parent = scans._live[scan['id']][2]
                self.assertEqual(db.execute('SELECT state FROM warehouse_admissions WHERE id=?', (parent.id,)).fetchone()[0], 'ACTIVE')
                if seen:
                    dm.record_operation(db, dm.Operation('test-drain', 'admin', 'apply', (), None, 'AWAITING_APPLY', (), None, (), ()))
                    admission.begin_drain(db, warehouse_id=self.c.warehouse_id, operation_id='test-drain')
            seen.append(args[1])
            return original(*args, **kwargs)
        with patch.object(scans, '_admit_file', side_effect=check):
            result = self.execute_scan(scan)
        self.assertEqual(len(seen), 2, result)
        self.assertEqual(result['status'], 'succeeded', result)

    def test_manual_scan_atomic_reservation_and_unknown_never_retried(self):
        manual = self.submit(self.stage)
        result = self.execute_scan(self.scan())
        self.assertEqual(result['status'], 'unknown')
        self.assertFalse(result['items'][0]['retryable'])
        with self.assertRaises(QueryError):
            scans.retry(self.p, self.c, self.stage['id'], result['id'], key='unknown-retry', expected_revision=1,
                        item_id=result['items'][0]['id'], expected_attempt=0)
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM db_stage_runs').fetchone()[0], 1)
        self.execute(manual)
        calls = len(self.b.calls)
        observed = self.observe(result)
        self.assertEqual(observed['items'][0]['status'], 'skipped')
        self.assertTrue(observed['items'][0]['committed'])
        self.assertNotIn('run_id', observed['items'][0])
        self.assertEqual(len(self.b.calls), calls)

    def test_external_manual_failure_observe_links_positive_attempt_for_retry(self):
        manual = self.submit(self.stage)
        source = self.execute_scan(self.scan())
        self.assertEqual(source['items'][0]['status'], 'unknown')
        self.assertNotIn('run_id', source['items'][0])
        self.assertFalse(source['items'][0]['retryable'])
        calls = len(self.b.calls)
        # The independently owned import later settles positively uncommitted.
        ds.Execution(self.p, manual['operation_id']).fail()
        observed = self.observe(source)
        item = observed['items'][0]
        self.assertEqual(item['status'], 'failed')
        self.assertEqual(item['attempt'], 1)
        self.assertEqual(item['run_id'], manual['id'])
        self.assertEqual(item['operation_id'], manual['operation_id'])
        self.assertTrue(item['retryable'])
        self.assertNotIn('summary', item)
        self.assertNotIn('context', item)
        self.assertEqual(len(self.b.calls), calls)
        retry = scans.retry(self.p, self.c, self.stage['id'], observed['id'], key='retry-observed',
            expected_revision=1, item_id=item['id'], expected_attempt=1)
        result = self.execute_scan(retry)
        self.assertNotEqual(result['id'], source['id'])
        self.assertEqual(result['items'][0]['attempt'], 2)
        self.assertEqual(result['status'], 'succeeded')

    def test_lost_owner_and_true_unknown_child_hold_parent(self):
        scan = self.scan()
        def lose(run_id, execution):
            # Admission exists, but SQL ownership/outcome cannot be proved.
            stages._unknown(run_id, execution, OSError('lost owner'))
        with patch.object(stages, '_execute_run', side_effect=lose):
            result = self.execute_scan(scan)
        self.assertEqual(result['status'], 'unknown', result)
        self.assertFalse(result['items'][0]['retryable'])
        with control_store.connect() as db:
            self.assertTrue(admission.blockers(db, self.c.warehouse_id))
        self.code('STAGE_SCAN_RETRY_REJECTED', lambda: scans.retry(self.p, self.c, self.stage['id'], scan['id'],
            key='no-retry', expected_revision=1, item_id=result['items'][0]['id'], expected_attempt=1))
        self.assertEqual(self.b.calls, [])

    def test_unknown_second_file_retains_first_commit_and_blocks_parent(self):
        (self.directory / 'm.csv').write_bytes(b'id,label\n7,second\n')
        (self.directory / 'z.csv').write_bytes(b'id,label\n8,last\n')
        commits = []
        def fault(sql):
            if sql.startswith('INSERT INTO') and '"scan_target"' in sql:
                commits.append(sql)
                if len(commits) == 2:
                    raise TimeoutError('private transport details')
        self.f.fault = fault
        result = self.execute_scan(self.scan())
        self.assertEqual(result['status'], 'unknown', result)
        self.assertEqual([i['status'] for i in result['items']], ['succeeded', 'unknown', 'not_started'])
        self.assertTrue(result['committed'])
        self.assertFalse(result['items'][1]['retryable'])
        self.assertNotIn('private transport details', json.dumps(result))
        with control_store.connect() as db:
            self.assertTrue(db.execute("SELECT 1 FROM query_records WHERE state='UNKNOWN'").fetchone())
            self.assertEqual(db.execute("SELECT state FROM warehouse_admissions WHERE kind='stage_scan'").fetchone()[0], 'UNKNOWN')

    def test_changed_effective_config_removes_retry_capability(self):
        self.path.write_bytes(b'id,label\n1,too,many\n')
        result = self.execute_scan(self.scan())
        item = result['items'][0]
        self.assertTrue(item['retryable'])
        self.stage = self.save(key='edit', stage_id=self.stage['id'], expected_revision=1,
                               definition=dict(self.definition_value, batch_size=1))
        self.assertFalse(self.observe(result)['items'][0]['retryable'])
        self.code('STAGE_SCAN_RETRY_REJECTED', lambda: scans.retry(self.p, self.c, self.stage['id'], result['id'],
            key='changed-retry', expected_revision=2, item_id=item['id'], expected_attempt=1))

    def test_upsert_scan_success(self):
        self.stage = self.save(key='upsert', stage_id=self.stage['id'], expected_revision=1,
            definition=dict(self.definition_value, mode='upsert', key_columns=['id']))
        result = self.execute_scan(self.scan())
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertTrue(result['items'][0]['committed'])

    def test_empty_manifest_is_success_without_child_ids(self):
        self.path.unlink()
        result = self.execute_scan(self.scan())
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['items'], [])
        self.assertEqual(self.b.calls, [])

    def test_committed_canonical_state_repairs_lost_item_without_sql(self):
        scan = self.scan()
        scans._admit_file(scan['id'], scan['items'][0]['id'])
        with control_store.connect() as db:
            scans._update(db, 'db_stage_scan_items', scan['items'][0]['id'], status='running', committed=False)
        live = scans._live.pop(scan['id'])
        live[-1].close()
        calls = len(self.b.calls)
        result = self.observe(scan)
        self.assertEqual(result['items'][0]['status'], 'succeeded', result)
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertTrue(result['committed'])
        self.assertEqual(len(self.b.calls), calls)

    def test_committed_after_run_ledger_failure_never_reingested(self):
        scan = self.scan()
        original = stages._write_run
        def broken(db, run_id, **changes):
            if 'finished_at' in changes:
                raise OSError('private ledger fault')
            return original(db, run_id, **changes)
        with patch.object(stages, '_write_run', side_effect=broken):
            scans._execute(scan['id'])
        result = self.observe(scan)
        self.assertTrue(result['committed'], result)
        calls = len(self.b.calls)
        duplicate = self.execute_scan(self.scan('duplicate'))
        self.assertEqual(duplicate['items'][0]['status'], 'skipped', duplicate)
        self.assertEqual(len(self.b.calls), calls)

    def test_two_process_content_reservation_has_one_winner(self):
        operations = [ds.begin(self.p, self.c, route='/reservation-test', idempotency_key=str(i), payload={}) for i in range(2)]
        template = dict(stage_id=self.stage['id'], kind='ingest', owner=self.p.principal_id,
                        context=asdict(self.c), committed=False, status='queued', file='data.csv',
                        input_fingerprint=stages._hash(self.path.read_bytes()),
                        configuration_fingerprint=scans.configuration(self.c, self.definition_value, 'data.csv', self.path.read_bytes())[0])
        program = '''
import json, sys, uuid
sys.path.insert(0, 'tools/local_experiment')
import control_store, db_stage_scans as scans, dataops_service as ds
from query_context import ResolvedQueryContext
p = json.loads(sys.stdin.read())
raw = bytes.fromhex(p['raw'])
run_id = str(uuid.uuid4())
record = dict(p['record'], id=run_id, operation_id=p['operation'])
try:
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT INTO db_stage_runs VALUES (?,?,?,?,?,?,?)',
            (run_id, p['stage'], p['operation'], ds._json(record), ds._json(p['definition']), 'test', raw))
        scans.reserve(db, ResolvedQueryContext(**p['context']), p['stage'], p['definition'], 'data.csv', raw, run_id)
    print('reserved')
except scans._Duplicate:
    print('duplicate')
'''
        processes = [subprocess.Popen([sys.executable, '-c', program], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True) for _ in operations]
        for process, operation in zip(processes, operations):
            process.stdin.write(json.dumps(dict(raw=self.path.read_bytes().hex(), record=template, operation=operation.id,
                stage=self.stage['id'], definition=self.definition_value, context=asdict(self.c))))
            process.stdin.close()
            process.stdin = None
        results = [process.communicate(timeout=20) for process in processes]
        self.assertEqual([process.returncode for process in processes], [0, 0], results)
        self.assertEqual(sorted(out.strip() for out, _ in results), ['duplicate', 'reserved'])
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM db_stage_content').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM db_stage_runs').fetchone()[0], 1)

    def test_cancel_and_response_loss_replay_never_recaptures_bytes(self):
        scan = self.scan()
        self.path.unlink()
        self.assertEqual(self.scan()['id'], scan['id'])
        canceled = scans.cancel(self.p, self.c, self.stage['id'], scan['id'], key='cancel', expected_revision=1)
        result = self.execute_scan(canceled)
        self.assertEqual(result['status'], 'canceled')
        self.assertEqual(result['items'][0]['status'], 'not_started')
        self.assertEqual(self.b.calls, [])

    def test_owner_database_grant_and_stale_context_denial(self):
        scan = self.scan()
        other = self.b.fixture.principal('alice')
        with self.assertRaises((QueryError, PermissionError)):
            scans.get(other, self.c, self.stage['id'], scan['id'])
        context = self.b.context(database=self.b.databases[1], principal=self.p, role='admin')
        with self.assertRaises(QueryError):
            scans.submit(self.p, context, self.stage['id'], key='other-db', expected_revision=1)
        self.revoke()
        for operation in (lambda: self.observe(scan), lambda: scans.list_scans(self.p, self.c, self.stage['id']), lambda: self.scan('denied')):
            with self.assertRaises((QueryError, PermissionError)):
                operation()
        self.execute_scan_denied(scan)

    def test_manual_failure_prior_content_can_retry_and_stale_source_cannot(self):
        self.stage = self.save(key='upsert', stage_id=self.stage['id'], expected_revision=1,
            definition=dict(self.definition_value, mode='upsert', key_columns=['id']))
        self.f.bad_keys = True
        manual = self.execute(self.submit(self.stage))
        self.assertEqual(manual['status'], 'failed', manual)
        calls = len(self.b.calls)
        source = self.execute_scan(self.scan())
        item = source['items'][0]
        self.assertEqual(item['attempt'], 1)
        self.assertEqual(item['run_id'], manual['id'])
        self.assertEqual(len(self.b.calls), calls)
        retry = scans.retry(self.p, self.c, self.stage['id'], source['id'], key='retry', expected_revision=2,
                            item_id=item['id'], expected_attempt=1)
        failed = self.execute_scan(retry)
        self.assertEqual(failed['items'][0]['attempt'], 2)
        self.assertTrue(failed['items'][0]['retryable'])
        self.assertFalse(self.observe(source)['items'][0]['retryable'])
        self.code('STAGE_SCAN_RETRY_REJECTED', lambda: scans.retry(self.p, self.c, self.stage['id'], source['id'],
            key='stale-source', expected_revision=2, item_id=item['id'], expected_attempt=1))
        self.f.bad_keys = False
        next_retry = scans.retry(self.p, self.c, self.stage['id'], failed['id'], key='retry-new-source', expected_revision=2,
                                 item_id=failed['items'][0]['id'], expected_attempt=2)
        self.assertEqual(self.execute_scan(next_retry)['status'], 'succeeded')

    def test_retry_frozen_single_item_then_fresh_scan_processes_not_started(self):
        second = self.directory / 'm.csv'
        original_bytes = b'id,label\n7,second\n'
        second.write_bytes(original_bytes)
        (self.directory / 'z.csv').write_bytes(b'id,label\n8,last\n')
        execute = stages._execute_run
        def fail_second(run_id, execution):
            with control_store.connect() as db:
                run = json.loads(db.execute('SELECT record FROM db_stage_runs WHERE id=?', (run_id,)).fetchone()[0])
            if run['file'] == 'm.csv':
                execution.fail()
                with control_store.connect() as db:
                    stages._write_run(db, run_id, status='failed')
            else:
                execute(run_id, execution)
        with patch.object(stages, '_execute_run', side_effect=fail_second):
            source = self.execute_scan(self.scan())
        history = self.history(source)
        item = source['items'][1]
        second.write_bytes(b'id,label\n999,changed\n')
        accepted = scans.retry(self.p, self.c, self.stage['id'], source['id'], key='retry', expected_revision=1,
                               item_id=item['id'], expected_attempt=1)
        retried = self.execute_scan(accepted)
        self.assertEqual(len(retried['items']), 1)
        self.assertEqual(retried['status'], 'succeeded')
        run = retried['items'][0]
        self.assertEqual(stages._captured_input(run['run_id'], run['operation_id']), original_bytes)
        self.assertEqual(self.history(source), history)
        second.write_bytes(original_bytes)
        fresh = self.execute_scan(self.scan('fresh'))
        self.assertEqual([i['status'] for i in fresh['items']], ['skipped', 'skipped', 'succeeded'])

    def test_partial_and_unknown_committed_content_never_skip_or_retry(self):
        run = self.execute(self.submit(self.stage))
        with control_store.connect() as db:
            complete = db.execute('SELECT result FROM dataops_operations WHERE id=?', (run['operation_id'],)).fetchone()[0]
        for index, (state, result) in enumerate((('FINISHED', dict(mode='append', atomic=False, accepted_rows=1001, committed_rows=500)),
                                               ('UNKNOWN', json.loads(complete)))):
            with control_store.connect() as db:
                db.execute('UPDATE dataops_operations SET state=?,result=? WHERE id=?', (state, ds._json(result), run['operation_id']))
            for _ in range(2):
                self.code('STAGE_CONTENT_HELD', lambda: self.submit(self.stage, key='held-' + str(index)))
            with control_store.connect() as db:
                disposition = scans.disposition(db, run['id'])
                self.assertTrue(disposition['committed'])
                self.assertFalse(disposition['retryable'])
                self.assertNotEqual(disposition['status'], 'skipped')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM dataops_operations').fetchone()[0], 1)
            observed = self.execute_scan(self.scan('held-scan-' + str(index)))
            self.assertNotEqual(observed['items'][0]['status'], 'skipped')
            self.assertTrue(observed['committed'])
            self.assertFalse(observed['items'][0]['retryable'])

    def test_upsert_coalesced_keys_are_full_success_and_duplicate_replay_is_stable(self):
        self.path.write_bytes(b'id,label\n1,a\n1,b\n2,c\n')
        self.stage = self.save(key='upsert', stage_id=self.stage['id'], expected_revision=1,
            definition=dict(self.definition_value, mode='upsert', key_columns=['id']))
        run = self.execute(self.submit(self.stage))
        self.assertEqual(run['summary']['accepted_rows'], 3)
        self.assertEqual(run['summary']['committed_rows'], 2)
        duplicate = self.submit(self.stage, key='duplicate')
        self.path.write_bytes(b'id,label\n3,changed\n')
        # Replay uses the original request, without recapturing the current file.
        with control_store.connect() as db:
            receipt = db.execute("SELECT payload FROM db_stage_requests WHERE idempotency_key='duplicate' AND route LIKE '%/imports'").fetchone()[0]
        payload = json.loads(receipt)
        result = stages.import_file(self.p, self.c, self.stage['id'], key='duplicate',
            **{k: v for k, v in payload.items() if k != 'context'})
        self.assertEqual(result, duplicate)
        self.assertEqual(result['status'], 'skipped')

    def test_revoke_during_observe_denies_get_and_final_list_read(self):
        source = self.execute_scan(self.scan())
        with patch.object(scans, '_observe', side_effect=lambda ident: self.revoke()):
            self.code('DB_ACCESS_REVOKED', lambda: self.observe(source))
        self.regrant()
        # Refresh the immutable-context source after grant epoch changes.
        source = self.execute_scan(self.scan('current-context'))
        original_get = scans.get
        def revoke_after_get(*args):
            result = original_get(*args)
            self.revoke()
            return result
        with patch.object(scans, 'get', side_effect=revoke_after_get):
            self.code('DB_ACCESS_REVOKED', lambda: scans.list_scans(self.p, self.c, self.stage['id']))

    def test_inner_submit_replay_precedes_advanced_stage_cas(self):
        capture = scans.queries._capture_dataops
        accepted = []
        def compete(*args):
            with patch.object(scans.queries, '_capture_dataops', capture):
                accepted.append(self.scan())
            self.stage = self.save(key='advance', stage_id=self.stage['id'], expected_revision=1,
                                   definition=dict(self.definition_value, name='advanced'))
            return capture(*args)
        with patch.object(scans.queries, '_capture_dataops', side_effect=compete):
            result = self.scan()
        self.assertEqual(result['id'], accepted[0]['id'])
        self.assertEqual(self.b.calls, [])

    def test_inner_retry_replay_precedes_advanced_stage_cas(self):
        self.path.write_bytes(b'id,label\n1,too,many\n')
        source = self.execute_scan(self.scan())
        item = source['items'][0]
        options = dict(key='retry', expected_revision=1, item_id=item['id'], expected_attempt=1)
        capture = scans.queries._capture_dataops
        accepted = []
        def compete(*args):
            with patch.object(scans.queries, '_capture_dataops', capture):
                accepted.append(scans.retry(self.p, self.c, self.stage['id'], source['id'], **options))
            self.stage = self.save(key='advance', stage_id=self.stage['id'], expected_revision=1,
                                   definition=dict(self.definition_value, name='advanced'))
            return capture(*args)
        with patch.object(scans.queries, '_capture_dataops', side_effect=compete):
            result = scans.retry(self.p, self.c, self.stage['id'], source['id'], **options)
        self.assertEqual(result['id'], accepted[0]['id'])
        self.assertNotEqual(result['id'], source['id'])

    def test_missing_child_evidence_holds_content_and_parent(self):
        manual = self.execute(self.submit(self.stage))
        with control_store.connect() as db:
            query = db.execute('SELECT query_id FROM dataops_queries WHERE operation_id=? LIMIT 1', (manual['operation_id'],)).fetchone()[0]
            db.execute('DELETE FROM dataops_queries WHERE query_id=?', (query,))
        result = self.execute_scan(self.scan())
        self.assertEqual(result['status'], 'unknown')
        self.assertFalse(result['items'][0]['retryable'])
        with control_store.connect() as db:
            self.assertEqual(db.execute("SELECT state FROM warehouse_admissions WHERE kind='stage_scan'").fetchone()[0], 'UNKNOWN')

    def test_historical_fenced_query_does_not_block_root_scan(self):
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            query = admission.admit(db, warehouse_id=self.c.warehouse_id, actor=self.p.principal_id,
                kind='query', subject_id=str(uuid.uuid4()), generation_id=self.c.generation_id)
            admission.finish(db, query, state='UNKNOWN')
            db.execute("UPDATE warehouse_admissions SET execution_closure='FENCED_READ_ONLY' WHERE id=?", (query.id,))
        self.assertEqual(self.execute_scan(self.scan())['status'], 'succeeded')

    def execute_scan_denied(self, scan):
        scans._execute(scan['id'])
        self.assertEqual(self.b.calls, [])


if __name__ == '__main__':
    unittest.main()
