"""Database Job contracts using real SQLite, flock and the P3 transport boundary."""
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch
import uuid

TOOLS = Path(__file__).resolve().parents[1] / 'tools/local_experiment'
sys.path.insert(0, str(TOOLS))
import control_store
import data_management as dm
import dataops_service as ds
import db_jobs as jobs
import jobs_resident as legacy
import manual_wap
import query_service as qs
from query_context import QueryError
import warehouse_admission as admission
import test_dataops_service as boundary


class DatabaseJobsTests(unittest.TestCase):

    def test_external_retiring_polls_do_not_cancel_active_job_child(self):
        run = self.submit()
        fetch = qs.fetch
        methods, observed = [], []
        def transport(url, method, pin, body=None):
            self.assertEqual(pin.headers['Authorization'], 'Basic original-secret')
            methods.append(method)
            if method == 'POST':
                return dict(id='job_poll', data=[[1]],
                    nextUri='http://127.0.0.1:1234/v1/statement/job_poll/1')
            return dict(id='job_poll', data=[[2]])
        def fetch_after_external_poll(principal, query_id, token, *, selected_role):
            self.f.retire()
            with control_store.connect() as db:
                child = db.execute('''SELECT q.state,a.state AS admission_state,p.kind,
                    p.parent_id,d.operation_id FROM query_records q
                    JOIN warehouse_admissions a ON a.subject_id=q.id AND a.kind='query'
                    JOIN warehouse_admissions p ON p.id=a.parent_id
                    JOIN dataops_queries d ON d.query_id=q.id WHERE q.id=?''', (query_id,)).fetchone()
                self.assertEqual((child['state'], child['admission_state'], child['kind']),
                                 ('RUNNING', 'ACTIVE', 'dataops'))
                self.assertEqual(db.execute('SELECT subject_id FROM warehouse_admissions WHERE id=?',
                                           (child['parent_id'],)).fetchone()[0], run['id'])
            execution = ds.Execution(principal, child['operation_id'])
            def external_poll():
                self.assertIsNone(admission._current.get())
                for poll in (lambda: fetch(principal, query_id, token, selected_role=selected_role),
                             lambda: qs.inspect_query(principal, query_id, selected_role=selected_role),
                             execution.inspect):
                    with self.assertRaises(QueryError) as denied:
                        poll()
                    self.assertEqual((denied.exception.status, denied.exception.details), (403, {}))
                observed.append(query_id)
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(external_poll).result(timeout=5)
            self.assertEqual(methods, ['POST'])
            self.assertEqual(qs._row(query_id)['state'], 'RUNNING')
            return fetch(principal, query_id, token, selected_role=selected_role)
        with patch.object(qs, '_transport', side_effect=transport), \
                patch.object(qs, 'fetch', side_effect=fetch_after_external_poll):
            jobs._worker(run['id'])
        self.assertEqual(len(observed), 1)
        self.assertEqual(methods, ['POST', 'GET'])
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM db_job_runs WHERE id=?',
                                       (run['id'],)).fetchone()[0], 'succeeded')
            self.assertEqual(db.execute("SELECT state FROM warehouse_admissions WHERE kind='query'").fetchone()[0], 'FINISHED')

    def test_retiring_accepted_multistep_job_continues_and_cancel_is_minimal(self):
        steps = [dict(name=n, action='sql', sql='SELECT 1', mode='read-only') for n in ('one', 'two')]
        run = self.submit(self.save(self.definition(steps=steps)))
        self.f.retire()
        with self.assertRaises((QueryError, PermissionError)):
            jobs.get_run(self.p, self.c, run['id'])
        jobs._worker(run['id'])
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM db_job_runs WHERE id=?',
                                        (run['id'],)).fetchone()[0], 'succeeded')
        self.assertEqual(len(self.f.calls), 2)
        self.f.assert_database_admissions()
        self.assertEqual(jobs.cancel_run(self.p, self.c, run['id']),
                         dict(id=run['id'], status='succeeded'))

    def setUp(self):
        self.f = boundary.DataOpsTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.addCleanup(self.clear_jobs)
        self.p, self.c = self.f.principal, self.f.context()
        self.start = patch.object(jobs, '_start')
        self.start.start()
        self.addCleanup(self.start.stop)

    def clear_jobs(self):
        for live in jobs._live.values():
            live[-1].close()
        jobs._live.clear()

    def definition(self, name='sample', sql='SELECT 1', steps=None):
        return dict(name=name, steps=steps if steps is not None else [dict(name='read', action='sql', sql=sql, mode='read-only')])

    def save(self, definition=None, key='save', **kw):
        return jobs.save_job(kw.pop('principal', self.p), kw.pop('context', self.c),
            expected_revision=kw.pop('expected_revision', 0), definition=definition or self.definition(), key=key, **kw)

    def submit(self, job=None, key='run', **kw):
        job = job or self.save()
        return jobs.submit_run(kw.pop('principal', self.p), kw.pop('context', self.c), job['id'],
            expected_revision=kw.pop('expected_revision', job['revision']), arguments=kw.pop('arguments', {}), key=key, **kw)

    def finish(self, run):
        jobs._worker(run['id'])
        return jobs.get_run(self.p, self.c, run['id'])

    def assert_code(self, code, call):
        with self.assertRaises(QueryError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)

    def assert_input_rejected(self, call):
        with self.assertRaises(QueryError) as caught:
            call()
        error = caught.exception
        self.assertEqual((error.code, error.status, error.details),
            ('JOB_INPUT_REJECTED', 400, {'admission': 'NOT_ATTEMPTED'}))

    def admin(self):
        p = self.f.fixture.principal('owner')
        return p, self.f.context(principal=p, role='admin')

    def test_metadata_cas_replay_and_db_local_names_no_sql(self):
        first = self.save()
        uuid.UUID(first['id'])
        self.assertEqual(first['revision'], 1)
        second = self.save(self.definition(sql='SELECT 2'), key='edit', job_id=first['id'], expected_revision=1)
        self.assertEqual(second['revision'], 2)
        self.assertEqual(self.save(), first, 'same-key receipt is immutable after editing')
        self.assert_code('REVISION_CONFLICT', lambda: self.save(key='stale', job_id=first['id'], expected_revision=1))
        self.assert_code('JOB_NAME_CONFLICT', lambda: self.save(key='duplicate'))
        other = self.save(key='finance', context=self.f.context(self.f.databases[1]))
        self.assertNotEqual(other['id'], first['id'])
        self.assertEqual(self.f.calls, [])
        self.assert_code('IDEMPOTENCY_CONFLICT', lambda: self.save(self.definition(sql='SELECT 3')))
        with self.assertRaises(ValueError):
            self.save(job_id=first['id'])

    def test_cas_and_key_races(self):
        def save(_):
            return self.save()['id']
        with ThreadPoolExecutor(4) as pool:
            ids = list(pool.map(save, range(4)))
        self.assertEqual(len(set(ids)), 1)
        def edit(i):
            try:
                return self.save(self.definition(sql='SELECT ' + str(i)), key='edit' + str(i),
                    expected_revision=1, job_id=ids[0])['revision']
            except QueryError as error:
                return error.code
        with ThreadPoolExecutor(4) as pool:
            results = list(pool.map(edit, range(4)))
        self.assertEqual(results.count(2), 1)
        self.assertEqual(results.count('REVISION_CONFLICT'), 3)

    def test_owner_role_database_and_binding_boundaries(self):
        saved = self.save()
        owner, admin = self.admin()
        self.assert_code('JOB_NOT_FOUND', lambda: jobs.get_job(owner, admin, saved['id']))
        low = self.f.context(role='bi_viewer', schema='marts')
        self.assert_code('SQL_PERMISSION_DENIED', lambda: jobs.list_jobs(self.p, low))
        self.assert_code('CONTEXT_MISMATCH', lambda: self.submit(saved, context=self.f.context(self.f.databases[1])))
        self.assert_code('CONTEXT_MISMATCH', lambda: jobs.get_job(self.p, replace(self.c, sql_schema='marts'), saved['id']))
        # A platform administrator's other selected role still cannot adopt a definition.
        admin_job = self.save(key='admin', definition=self.definition(name='admin'), principal=owner, context=admin)
        with control_store.connect() as db:
            dm.create_grant(db, 'owner', self.c.database_id, 'data_engineer')
            from grant_fixtures import verified_grants
            verified_grants(db)
        engineer = self.f.context(principal=owner, role='data_engineer')
        self.assert_code('JOB_NOT_FOUND', lambda: jobs.get_job(owner, engineer, admin_job['id']))
        with control_store.connect() as db:
            db.execute('UPDATE catalog_bindings SET desired_revision=2,revision=revision+1 WHERE database_id=?', (self.c.database_id,))
        self.assert_code('STALE_BINDING', lambda: self.submit(saved))

    def test_typed_literals_and_no_rows_in_history(self):
        definition = self.definition(sql='SELECT {{s}}, {{i}}, {{b}}, {{d}}')
        definition['parameters'] = {n: dict(type=t, required=True) for n, t in
            [('s', 'string'), ('i', 'integer'), ('b', 'boolean'), ('d', 'date')]}
        job = self.save(definition)
        args = dict(s="001'; SELECT 99 --", i=19, b=True, d='2026-10-21')
        run = self.submit(job, arguments=args)
        result = self.finish(run)
        self.assertEqual(result['status'], 'succeeded')
        sql = self.f.calls[0][1]
        self.assertIn("'001''; SELECT 99 --'", sql)
        self.assertIn("19, TRUE, DATE '2026-10-21'", sql)
        self.assertNotIn('"data":', json.dumps(result))
        self.assertEqual(result['steps'][0]['summary'], {'row_count': 3})
        self.assert_code('IDEMPOTENCY_CONFLICT', lambda: self.submit(job, arguments=dict(args, i=20)))
        self.assert_input_rejected(lambda: self.submit(job, key='bad', arguments=dict(args, i=True)))
        self.assert_input_rejected(lambda: self.submit(job, key='null', arguments=None))

    def test_explicit_cas_refreshes_binding_and_refs_without_changing_accepted_run(self):
        principal, context = self.admin()
        old_ref = self.f.existing('old_source')
        definition = self.definition(steps=[dict(name='export', action='table_export', source=asdict(old_ref), format='csv')])
        job = self.save(definition, principal=principal, context=context)
        run = self.submit(job, principal=principal, context=context)
        with control_store.connect() as db:
            accepted = dict(db.execute('SELECT * FROM db_job_runs WHERE id=?', (run['id'],)).fetchone())
            db.execute('UPDATE catalog_bindings SET desired_revision=2,applied_revision=2,revision=revision+1 WHERE database_id=?',
                (context.database_id,))
        current = replace(context, binding_revision=2)
        self.assert_code('CONTEXT_MISMATCH', lambda: self.submit(job, principal=principal, context=current, key='stale'))
        self.assert_code('CONTEXT_MISMATCH', lambda: jobs.get_job(principal, current, job['id']))
        new_ref = self.f.existing('new_source')
        refreshed_definition = self.definition(steps=[dict(name='export', action='table_export', source=asdict(new_ref), format='csv')])
        refreshed = self.save(refreshed_definition, principal=principal, context=current, key='refresh',
            job_id=job['id'], expected_revision=1)
        self.assertEqual(refreshed['revision'], 2)
        self.assertEqual(refreshed['context'], asdict(current))
        self.assertEqual(refreshed['definition'], refreshed_definition)
        self.assertEqual(jobs.get_job(principal, current, job['id']), refreshed)
        changed_schema = replace(current, schema_id=self.f.schemas[context.database_id]['sandbox'], sql_schema='sandbox')
        self.assert_code('CONTEXT_MISMATCH', lambda: self.save(refreshed_definition, principal=principal,
            context=changed_schema, key='move', job_id=job['id'], expected_revision=2))
        with control_store.connect() as db:
            self.assertEqual(dict(db.execute('SELECT * FROM db_job_runs WHERE id=?', (run['id'],)).fetchone()), accepted)
        self.assertEqual(self.f.calls, [])

    def test_later_occupied_ingest_destination_prevents_earlier_sql(self):
        principal, context = self.admin()
        steps = [dict(name='first', action='sql', sql='SELECT 1', mode='read-only'),
            dict(name='load', action='ingest', input='source', mode='create', destination=asdict(self.f.locator('occupied')))]
        job = self.save(self.definition(steps=steps), principal=principal, context=context)
        self.f.existing('occupied')
        inputs = dict(source=dict(filename='data.csv', content_base64=base64.b64encode(b'id\n1\n').decode()))
        self.assert_code('DESTINATION_EXISTS', lambda: self.submit(job, principal=principal, context=context, inputs=inputs))
        self.assertEqual(self.f.calls, [])
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM warehouse_admissions').fetchone()[0], 0)

    def test_malformed_input_names_and_job_id_are_rejected_before_admission(self):
        job = self.save()
        for inputs in ({1: dict(filename='data.csv', content_base64='')}, {'upload': None}):
            with self.subTest(inputs=inputs):
                self.assert_input_rejected(lambda: self.submit(job, inputs=inputs))
        for job_id in ([], {}, 7):
            with self.subTest(job_id=job_id):
                self.assert_input_rejected(lambda: jobs.submit_run(self.p, self.c, job_id,
                    expected_revision=1, arguments={}, key='bad'))
        self.assertEqual(self.f.calls, [])

    def test_input_rejection_marker_precedes_any_admission_or_capture(self):
        definition = self.definition(sql='SELECT {{count}}')
        definition['parameters'] = dict(count=dict(type='integer', required=True))
        job = self.save(definition)
        cases = [dict(arguments=None), dict(arguments={}), dict(arguments=dict(count=True)),
            dict(arguments=dict(count=1), inputs=[]),
            dict(arguments=dict(count=1), inputs={'upload': dict(filename='data.csv', content_base64='invalid!')}),
            dict(arguments=dict(count=1), inputs={'upload': dict(filename=[], content_base64='')})]
        with patch.object(admission, 'admit') as admit, patch.object(qs, '_capture_dataops') as capture:
            for index, options in enumerate(cases):
                with self.subTest(index=index):
                    self.assert_input_rejected(lambda: self.submit(job, key='bad-' + str(index), **options))
            admit.assert_not_called()
            capture.assert_not_called()
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM warehouse_admissions').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM db_job_runs').fetchone()[0], 0)

    def test_thread_start_failure_after_acceptance_has_no_input_rejection_marker(self):
        job = self.save()
        with patch.object(jobs, '_start', side_effect=ValueError('thread start failed')):
            with self.assertRaises(ValueError) as caught:
                self.submit(job)
        self.assertNotIsInstance(caught.exception, QueryError)
        self.assertFalse(hasattr(caught.exception, 'details'))
        self.assertNotEqual(getattr(caught.exception, 'code', None), 'JOB_INPUT_REJECTED')
        with control_store.connect() as db:
            row = db.execute('SELECT * FROM db_job_runs').fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row['status'], 'queued')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM warehouse_admissions').fetchone()[0], 1)
        # Replaying the accepted attempt returns its durable identity, not a
        # claim that admission was never attempted.
        self.assertEqual(self.submit(job)['id'], row['id'])

    def test_failure_summaries_allow_only_safe_query_codes_and_hide_driver_errors(self):
        job = self.save()
        driver = RuntimeError('secret credential and result rows')
        driver.code = 'DRIVER_SECRET'
        for index, (error, code) in enumerate([
                (QueryError('WAP_AUDIT_FAILED', 409, message='secret result rows'), 'WAP_AUDIT_FAILED'),
                (QueryError('secret=value\nrows', 502), 'JOB_STEP_FAILED'),
                (QueryError(['SECRET'], 502, message='secret'), 'JOB_STEP_FAILED'),
                (driver, 'JOB_STEP_FAILED')]):
            with self.subTest(index=index):
                run = self.submit(job, key='failure-' + str(index))
                with patch.object(jobs, '_execute', side_effect=error):
                    result = self.finish(run)
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['steps'][0]['summary'], dict(code=code))
                self.assertNotIn('secret', json.dumps(result).lower())
        original = jobs._execute
        def uncertain(*args):
            try:
                return original(*args)
            except QueryError:
                raise RuntimeError('secret backend failure rows') from None
        run = self.submit(job, key='uncertain')
        with patch.object(jobs, '_execute', side_effect=uncertain), patch.object(qs, '_transport', side_effect=OSError('secret')):
            result = self.finish(run)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['steps'][0]['summary'], dict(code='BACKEND_OUTCOME_UNKNOWN'))
        self.assertNotIn('secret', json.dumps(result).lower())

    def test_guard_and_unsupported_actions_fail_before_admission(self):
        for sql in ('SELECT 1; SELECT 2', 'SELECT * FROM finance.raw.t', 'CALL system.foo()', 'EXECUTE foo'):
            with self.assertRaises(ValueError):
                self.save(self.definition(sql=sql), key=sql)
        for action in ('dbt', 'command', 'stage_ingest'):
            with self.assertRaises(ValueError):
                self.save(self.definition(steps=[dict(name='escape', action=action)]), key=action)
        self.assertEqual(self.f.calls, [])
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM warehouse_admissions').fetchone()[0], 0)

    def foreign_sql(self):
        return [('SELECT * FROM finance.raw.p43_source', 'read-only'),
            ('SELECT * FROM "finance" . "raw" . "p43_source"', 'read-only'),
            ('INSERT INTO finance.raw.p43_target VALUES (1)', 'admin-write'),
            ('INSERT INTO finance.marts.p43_target SELECT * FROM sales.raw.p43_source', 'admin-write'),
            ('CREATE TABLE finance.sandbox.p43_target AS SELECT * FROM sales.raw.p43_source', 'admin-write'),
            ('CREATE TABLE sales.marts.p43_target AS SELECT * FROM finance.raw.p43_source', 'admin-write')]

    def test_admin_job_foreign_read_source_and_target_rejected_at_save_and_run(self):
        principal, context = self.admin()
        job = self.save(principal=principal, context=context)
        for index, (sql, mode) in enumerate(self.foreign_sql()):
            with self.subTest(sql=sql):
                definition = self.definition(steps=[dict(name='foreign', action='sql', sql=sql, mode=mode)])
                with self.assertRaises(ValueError):
                    self.save(definition, principal=principal, context=context, key='foreign-' + str(index))
                # Simulate a definition persisted before the Job-only guard.
                with control_store.connect() as db:
                    db.execute('UPDATE db_jobs SET definition=? WHERE id=?', (json.dumps(definition), job['id']))
                self.assert_input_rejected(lambda: self.submit(job, principal=principal, context=context,
                    key='persisted-' + str(index)))
        self.assertEqual(self.f.calls, [])
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM warehouse_admissions').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM db_job_runs').fetchone()[0], 0)

    def test_admin_job_same_database_names_and_writes_remain_accepted(self):
        principal, context = self.admin()
        cases = [('SELECT * FROM p43_source', 'read-only'),
            ('SELECT * FROM raw.p43_source', 'read-only'),
            ('SELECT * FROM "sales"."marts"."p43_source"', 'read-only')]
        for schema in ('raw', 'marts', 'sandbox'):
            cases.extend([('INSERT INTO sales.' + schema + '.p43_target VALUES (1)', 'admin-write'),
                ('INSERT INTO ' + schema + '.p43_target VALUES (1)', 'admin-write'),
                ('CREATE TABLE sales.' + schema + '.p43_target AS SELECT * FROM raw.p43_source', 'admin-write')])
        cases.append(('INSERT INTO p43_target VALUES (1)', 'admin-write'))
        for index, (sql, mode) in enumerate(cases):
            with self.subTest(sql=sql):
                definition = self.definition(name='same-' + str(index),
                    steps=[dict(name='same', action='sql', sql=sql, mode=mode)])
                job = self.save(definition, principal=principal, context=context, key='save-' + str(index))
                run = self.submit(job, principal=principal, context=context, key='run-' + str(index))
                jobs._worker(run['id'])
                self.assertEqual(jobs.get_run(principal, context, run['id'])['status'], 'succeeded')
                self.assertEqual(self.f.calls[-1][1], sql)
                self.assertEqual(self.f.calls[-1][2]['X-Trino-Catalog'], 'sales')

    def test_ordinary_admin_sql_cross_database_requires_explicit_platform_authority(self):
        principal, context = self.admin()
        self.assertTrue(principal.platform_admin)
        for sql, mode in self.foreign_sql():
            with self.subTest(sql=sql):
                self.assertEqual(jobs.warehouse.check_sql_policy(sql, mode, 'admin', context=context,
                    _platform_authorized=True), sql)
                self.assertEqual(jobs.warehouse.check_sql_policy(sql, mode, 'admin', context=context,
                    database_only=False, _platform_authorized=True), sql)
                with self.assertRaises(ValueError):
                    jobs.warehouse.check_sql_policy(sql, mode, 'admin', context=context)
                with self.assertRaises(ValueError):
                    jobs.warehouse.check_sql_policy(sql, mode, 'admin', context=context, _platform_authorized=False)
                with self.assertRaises(ValueError):
                    jobs.warehouse.check_sql_policy(sql, mode, 'admin', context=context,
                        database_only=True, _platform_authorized=True)
        sql = 'SELECT * FROM finance.raw.p43_source'
        jobs.warehouse._check_bi_schemas(sql, context=context, _platform_authorized=True)
        with self.assertRaises(ValueError):
            jobs.warehouse._check_bi_schemas(sql, context=context)
        with self.assertRaises(ValueError):
            jobs.warehouse._check_bi_schemas(sql, context=context, database_only=True, _platform_authorized=True)

    def test_private_job_sql_hook_rejects_foreign_sql_in_accepted_malicious_run(self):
        principal, context = self.admin()
        job = self.save(principal=principal, context=context)
        run = self.submit(job, principal=principal, context=context)
        _, _, parent, frozen, ref, _, _, _ = jobs._live[run['id']]
        with control_store.connect() as db:
            malicious = self.definition(sql='SELECT * FROM finance.raw.p43_source')
            db.execute('UPDATE db_job_runs SET definition=? WHERE id=?', (json.dumps(malicious), run['id']))
        with ds._job_scope(principal, context, parent, frozen, ref, run['id'], 1):
            execution = ds.begin(principal, context, route='job-sql', idempotency_key='direct', payload={})
        for sql, mode in self.foreign_sql():
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                execution._job_sql(sql, mode=mode)
        self.assertEqual(self.f.calls, [])
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM dataops_queries').fetchone()[0], 0)
        jobs.cancel_run(principal, context, run['id'])
        jobs._worker(run['id'])

    def test_frozen_definition_arguments_parent_drain_and_pin(self):
        steps = [dict(name=n, action='sql', sql='SELECT {{x}}', mode='read-only') for n in ('first', 'second')]
        definition = self.definition(steps=steps)
        definition['parameters'] = dict(x=dict(type='integer', required=True))
        job = self.save(definition)
        args = dict(x=31)
        run = self.submit(job, arguments=args)
        args['x'] = 99
        self.save(self.definition(sql='SELECT 777'), key='edit', expected_revision=1, job_id=job['id'])
        self.f.credential = 'Basic rotated-secret'
        # Drain after acceptance permits descendants but blocks fresh parents.
        with control_store.connect() as db:
            db.execute("UPDATE warehouse_controls SET state='DRAINING' WHERE warehouse_id=?", (self.c.warehouse_id,))
        def transport(url, method, pin, body=None):
            with control_store.connect() as db:
                parent = admission.get_for_subject(db, kind='job', subject_id=run['id'], warehouse_id=self.c.warehouse_id)
                self.assertEqual(db.execute('SELECT state FROM warehouse_admissions WHERE id=?', (parent.id,)).fetchone()[0], 'ACTIVE')
                link = db.execute('SELECT * FROM db_job_operations').fetchone()
                self.assertIsNotNone(link)
            return self.f.transport(url, method, pin, body)
        with patch.object(qs, '_transport', side_effect=transport):
            result = self.finish(run)
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual([c[1] for c in self.f.calls], ['SELECT 31', 'SELECT 31'])
        self.assertTrue(all(c[2]['Authorization'] == 'Basic original-secret' for c in self.f.calls))
        with control_store.connect() as db:
            refs = {r[0] for r in db.execute('SELECT credential_snapshot_ref FROM query_execution_contexts')}
            refs.update(r[0] for r in db.execute('SELECT credential_snapshot_ref FROM dataops_operations'))
            refs.update(r[0] for r in db.execute('SELECT credential_snapshot_ref FROM db_job_runs'))
            self.assertEqual(len(refs), 1)
            self.assertEqual(len(admission.blockers(db, self.c.warehouse_id)), 0)
        # Replay returns original run even after definition edit and while draining.
        replay = self.submit(job, arguments=dict(x=31))
        self.assertEqual(replay['id'], run['id'])
        self.assertEqual(len(self.f.calls), 2)

    def test_global_double_run_and_legacy_share_lock(self):
        job = self.save()
        run = self.submit(job)
        self.assertIsNone(legacy._lock())
        self.assert_code('JOB_BUSY', lambda: self.submit(job, key='other'))
        self.assertEqual(self.submit(job)['id'], run['id'])
        self.finish(run)

    def test_same_name_jobs_execute_in_three_arbitrary_database_contexts(self):
        for index, database in enumerate(self.f.databases):
            context = self.f.context(database)
            job = self.save(key='save-' + str(index), context=context)
            run = self.submit(job, key='run-' + str(index), context=context)
            jobs._worker(run['id'])
            result = jobs.get_run(self.p, context, run['id'])
            self.assertEqual(result['status'], 'succeeded')
            self.assertEqual(result['context']['database_id'], database)
            self.assertEqual(self.f.calls[-1][2]['X-Trino-Catalog'], context.sql_catalog)
            self.assertEqual(self.f.calls[-1][2]['X-Trino-User'], 'fixed_' + database)

    def test_cancel_flag_wins_before_child_transaction(self):
        run = self.submit()
        principal, context, parent, frozen, ref, _, _, _ = jobs._live[run['id']]
        jobs.cancel_run(principal, context, run['id'])
        with ds._job_scope(principal, context, parent, frozen, ref, run['id'], 1):
            self.assert_code('JOB_STOPPED', lambda: ds.begin(principal, context,
                route='job-sql', idempotency_key='late', payload={}))
        result = self.finish(run)
        self.assertEqual(result['status'], 'canceled')
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM dataops_operations').fetchone()[0], 0)

    def test_cancel_between_operation_and_query_blocks_query_intent(self):
        run = self.submit()
        principal, context, parent, frozen, ref, _, _, _ = jobs._live[run['id']]
        with ds._job_scope(principal, context, parent, frozen, ref, run['id'], 1):
            execution = ds.begin(principal, context, route='job-sql', idempotency_key='accepted', payload={})
        with control_store.connect() as db:
            db.execute('UPDATE db_job_runs SET cancel_requested=1 WHERE id=?', (run['id'],))
        self.assert_code('JOB_STOPPED', lambda: execution._execute('job_sql', 'SELECT 1', 'late'))
        self.assertEqual(self.f.calls, [])
        self.finish(run)

    def test_cancel_in_step_gap_and_transaction_winner(self):
        steps = [dict(name=n, action='sql', sql='SELECT 1', mode='read-only') for n in ('first', 'second')]
        run = self.submit(self.save(self.definition(steps=steps)))
        gap, resume = threading.Event(), threading.Event()
        original = jobs._execute
        def execute(*args):
            result = original(*args)
            gap.set()
            self.assertTrue(resume.wait(5))
            return result
        with patch.object(jobs, '_execute', side_effect=execute):
            worker = threading.Thread(target=jobs._worker, args=(run['id'],))
            worker.start()
            try:
                self.assertTrue(gap.wait(5))
                with control_store.connect() as db:
                    self.assertEqual(len(admission.blockers(db, self.c.warehouse_id)), 1)
                jobs.cancel_run(self.p, self.c, run['id'])
            finally:
                resume.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(self.f.calls), 1)
        self.assertEqual(jobs.get_run(self.p, self.c, run['id'])['status'], 'canceled')

    def test_revoke_after_first_step_stops_later_children(self):
        steps = [dict(name=n, action='sql', sql='SELECT 1', mode='read-only') for n in ('first', 'second')]
        run = self.submit(self.save(self.definition(steps=steps)))
        original = jobs._execute
        def execute(*args):
            result = original(*args)
            with control_store.connect() as db:
                db.execute('DELETE FROM grants WHERE principal_id=? AND database_id=?', ('alice', self.c.database_id))
            return result
        with patch.object(jobs, '_execute', side_effect=execute):
            jobs._worker(run['id'])
        self.assertEqual(len(self.f.calls), 1)
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM db_job_runs').fetchone()[0], 'failed')
            self.assertEqual(admission.blockers(db, self.c.warehouse_id), ())

    def test_grant_loss_read_and_replay_denied_cancel_uses_old_pin(self):
        run = self.submit()
        principal, context, parent, frozen, ref, _, _, _ = jobs._live[run['id']]
        with ds._job_scope(principal, context, parent, frozen, ref, run['id'], 1):
            execution = ds.begin(principal, context, route='job-sql', idempotency_key='waiting', payload={})
        with patch.object(qs, '_transport', return_value={'id': 'pending', 'nextUri': 'http://127.0.0.1:1234/v1/statement/pending/1'}):
            qs._submit_dataops(principal, ds._Statement('job_sql', 'SELECT 1'), frozen,
                ds._live[execution.id][1], execution.id, 'read')
        with control_store.connect() as db:
            db.execute('DELETE FROM grants WHERE principal_id=? AND database_id=?', (self.p.principal_id, self.c.database_id))
        self.assert_code('DB_ACCESS_REVOKED', lambda: jobs.get_run(self.p, self.c, run['id']))
        self.assert_code('DB_ACCESS_REVOKED', lambda: self.submit(key='run'))
        self.f.credential = 'Basic rotated'
        result = jobs.cancel_run(self.p, self.c, run['id'])
        self.assertEqual(set(result), {'id', 'status'})
        deletes = [c for c in self.f.calls if c[0] == 'DELETE']
        self.assertTrue(deletes)
        self.assertTrue(all(c[2]['Authorization'] == 'Basic original-secret' for c in deletes))
        jobs._worker(run['id'])
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM db_job_runs').fetchone()[0], 'canceled')

    def test_unknown_blocks_next_step_restart_and_evidence_only_reconcile(self):
        steps = [dict(name=n, action='sql', mode='read-only', sql='SELECT ' + str(i))
            for i, n in enumerate(('uncertain', 'never'), 1)]
        job = self.save(self.definition(steps=steps))
        run = self.submit(job)
        with patch.object(qs, '_transport', side_effect=OSError('lost response')) as transport:
            result = self.finish(run)
            self.assertEqual(transport.call_count, 1)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['steps'][1]['status'], 'skipped')
        self.assert_code('UNKNOWN_RECONCILIATION_REQUIRED', lambda: self.submit(job, key='retry'))
        with patch.object(qs, 'observe_query_backend', return_value=[]):
            self.assert_code('UNRESOLVED_CHILD_QUERIES', lambda: jobs.reconcile_run(self.p, self.c, run['id']))
        def observe(expected):
            return [dict(id='proof_1', source=expected['source'], user=expected['backend_username'],
                sql=expected['sql'], state='FINISHED')]
        with patch.object(qs, 'observe_query_backend', side_effect=observe), patch.object(qs, '_transport') as transport:
            result = jobs.reconcile_run(self.p, self.c, run['id'])
            transport.assert_not_called()
        self.assertEqual(result['status'], 'failed')
        with control_store.connect() as db:
            self.assertEqual(admission.blockers(db, self.c.warehouse_id), ())

    def test_lost_process_before_first_child_is_unknown_and_abandoned(self):
        run = self.submit()
        self.clear_jobs()  # Process lease lost; no SQL intent means only explicit abandon is safe.
        result = jobs.get_run(self.p, self.c, run['id'])
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(jobs.reconcile_run(self.p, self.c, run['id'])['status'], 'failed')
        self.assertEqual(self.f.calls, [])

    def test_alert_boolean_summary_and_stop_on_invalid_result(self):
        job = self.save(self.definition(steps=[dict(name='condition', action='alert', sql='SELECT TRUE')]))
        run = self.submit(job)
        with patch.object(qs, '_transport', return_value={'id': 'boolean', 'data': [[True]]}):
            result = self.finish(run)
        self.assertEqual(result['steps'][0]['summary'], {'triggered': True})
        run = self.submit(job, key='invalid')
        self.assertEqual(self.finish(run)['status'], 'failed')

    def test_admin_upload_capture_precision_and_private_history(self):
        p, c = self.admin()
        step = dict(name='load', action='ingest', input='source', mode='create',
            destination=asdict(self.f.locator('uploaded')), schema_hints={'code': 'VARCHAR', 'amount': 'DECIMAL(30,3)'})
        job = self.save(self.definition(steps=[step]), principal=p, context=c)
        upload = dict(source=dict(filename='orders.csv', content_base64=base64.b64encode(
            b'code,amount\n001,12345678901234567890.123\n').decode()))
        run = self.submit(job, principal=p, context=c, inputs=upload)
        upload['source']['content_base64'] = base64.b64encode(b'wrong').decode()
        def transport(url, method, pin, body=None):
            result = self.f.transport(url, method, pin, body)
            if body and body.decode().startswith('DESCRIBE'):
                result['data'] = [['code', 'varchar'], ['amount', 'decimal(30,3)']]
            return result
        with patch.object(qs, '_transport', side_effect=transport):
            jobs._worker(run['id'])
        result = jobs.get_run(p, c, run['id'])
        self.assertEqual(result['status'], 'succeeded')
        sql = '\n'.join(c[1] or '' for c in self.f.calls)
        self.assertIn("'001'", sql)
        self.assertIn('12345678901234567890.123', sql)
        for private in ('content_base64', 'filename', '12345678901234567890', 'orders.csv'):
            self.assertNotIn(private, json.dumps(result))
        self.assert_code('SQL_PERMISSION_DENIED', lambda: self.save(self.definition(steps=[step]), key='engineer'))
        self.assert_input_rejected(lambda: self.submit(job, principal=p, context=c, inputs={}, key='missing'))

    def test_ref_recreation_rejected_before_run_and_cross_db_locator(self):
        p, c = self.admin()
        ref = self.f.existing()
        job = self.save(self.definition(steps=[dict(name='export', action='table_export', source=asdict(ref), format='csv')]),
            principal=p, context=c)
        self.f.objects[('sales', 'raw', 'observed_data', 'tables')]['table-uuid'] = str(uuid.uuid4())
        self.assert_code('STALE_RESOURCE', lambda: self.submit(job, principal=p, context=c))
        step = dict(name='load', action='ingest', input='file', mode='create',
            destination=asdict(self.f.locator(database=self.f.databases[1])))
        self.assert_code('CROSS_DATABASE_TARGET', lambda: self.save(self.definition(steps=[step]), key='cross', principal=p, context=c))
        self.assertEqual(self.f.calls, [])

    def test_two_process_admission_and_process_loss(self):
        job = self.save()
        script = '''
import json,sys
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
import db_jobs as j, request_auth as a
from query_context import ResolvedQueryContext
p=a.authenticate_principal({'Authorization': sys.argv[2]})
c=ResolvedQueryContext(**json.loads(sys.argv[3]))
with patch.object(j.queries,'_capture_dataops',return_value=SimpleNamespace(headers={'X-Trino-User':'captured'})), patch.object(j,'_start'):
    try:
        r=j.submit_run(p,c,sys.argv[4],expected_revision=1,arguments={},key=sys.argv[5])
        print(r['id'],flush=True)
        if sys.argv[6]=='hold': sys.stdin.read()
    except Exception as e: print(getattr(e,'code',type(e).__name__),flush=True)
'''
        args = [sys.executable, '-c', script, str(TOOLS), self.f.fixture.headers()['Authorization'], json.dumps(asdict(self.c)), job['id']]
        first = subprocess.Popen(args + ['first', 'hold'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: first.poll() is None and first.kill())
        run_id = first.stdout.readline().strip()
        uuid.UUID(run_id)
        replay = subprocess.run(args + ['first', 'exit'], capture_output=True, text=True, timeout=20)
        self.assertEqual(replay.stdout.strip(), run_id, replay.stderr)
        second = subprocess.run(args + ['second', 'exit'], capture_output=True, text=True, timeout=20)
        self.assertEqual(second.stdout.strip(), 'JOB_BUSY', second.stderr)
        first.communicate('', timeout=20)
        self.assertEqual(jobs.get_run(self.p, self.c, run_id)['status'], 'unknown')
        self.assertEqual(jobs.reconcile_run(self.p, self.c, run_id)['status'], 'failed')


class JobMigrationTests(unittest.TestCase):
    def test_v11_migration_preserves_legacy_and_rolls_back_on_failure(self):
        import tempfile
        import os
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, SHIROKUMA_STATE_DIR=root):
            with control_store.connect() as db:
                db.execute("INSERT INTO jobs VALUES ('kept','owner','{}','now','now')")
                from test_control_store import drop_recovery_schema
                drop_recovery_schema(db)
                for table in ('db_job_requests', 'db_job_operations', 'db_job_steps', 'db_job_runs', 'db_jobs'):
                    db.execute('DROP TABLE ' + table)
                db.execute('PRAGMA user_version=11')
            original = sqlite3.connect
            def fail_after_schema(*args, **kwargs):
                db = original(*args, **kwargs)
                db.set_authorizer(lambda action, name, *rest: sqlite3.SQLITE_DENY
                    if action == sqlite3.SQLITE_CREATE_TABLE and name == 'db_job_runs' else sqlite3.SQLITE_OK)
                return db
            with patch.object(sqlite3, 'connect', side_effect=fail_after_schema):
                with self.assertRaises(sqlite3.DatabaseError):
                    with control_store.connect():
                        pass
            with sqlite3.connect(Path(root) / 'jobs.sqlite3') as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 11)
                self.assertEqual(db.execute("SELECT name FROM jobs").fetchone()[0], 'kept')
            with control_store.connect() as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], control_store.VERSION)
                self.assertEqual(db.execute("SELECT name FROM jobs").fetchone()[0], 'kept')
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])


class JobAdapterTests(unittest.TestCase):
    def start(self, fixture):
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        patcher = patch.object(jobs, '_start')
        patcher.start()
        self.addCleanup(patcher.stop)
        return fixture

    def run_job(self, principal, context, steps):
        job = jobs.save_job(principal, context, expected_revision=0, key='save',
            definition=dict(name='adapter', steps=steps))
        run = jobs.submit_run(principal, context, job['id'], expected_revision=1, arguments={}, key='run')
        jobs._worker(run['id'])
        return jobs.get_run(principal, context, run['id'])

    def test_wap_build_audit_publish_share_job_pin_and_parent(self):
        import test_manual_wap as fixtures
        fixture = self.start(fixtures.ManualWapTests())
        model = fixture.save()
        step = dict(name='refresh', action='wap_refresh', model_id=model['model_id'], version=model['version'])
        result = self.run_job(fixture.owner, fixture.admin, [step])
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(len(result['steps'][0]['operation_ids']), 3)
        self.assertIn('publication_id', result['steps'][0]['summary'])
        with control_store.connect() as db:
            operations = list(db.execute('SELECT * FROM dataops_operations'))
            run = db.execute('SELECT * FROM db_job_runs').fetchone()
            self.assertTrue(all(r['credential_snapshot_ref'] == run['credential_snapshot_ref'] for r in operations))
            self.assertTrue(all(r['context'] == run['context'] for r in operations))
            parent = admission.get_for_subject(db, kind='job', subject_id=run['id'], warehouse_id='w')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM warehouse_admissions WHERE parent_id=?', (parent.id,)).fetchone()[0], 3)
            self.assertEqual(admission.blockers(db, 'w'), ())

    def test_wap_failed_audit_never_publishes_or_runs_later_step(self):
        import test_manual_wap as fixtures
        fixture = self.start(fixtures.ManualWapTests())
        model = fixture.save(min_rows=100)
        result = self.run_job(fixture.owner, fixture.admin,
            [dict(name='refresh', action='wap_refresh', model_id=model['model_id'], version=model['version']),
             dict(name='never', action='sql', sql='SELECT 999', mode='read-only')])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['steps'][1]['status'], 'skipped')
        self.assertEqual(len(result['steps'][0]['operation_ids']), 2)
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM wap_publications').fetchone()[0], 0)

    def test_later_stale_materialized_wap_target_prevents_earlier_sql(self):
        import test_manual_wap as fixtures
        fixture = self.start(fixtures.ManualWapTests())
        model = fixture.save()
        step = dict(name='refresh', action='wap_refresh', model_id=model['model_id'], version=model['version'])
        self.assertEqual(self.run_job(fixture.owner, fixture.admin, [step])['status'], 'succeeded')
        # The old model locator is now occupied by the accepted publication.
        # Preflight must validate the head's effective ResourceRef instead.
        definition = dict(name='later', steps=[dict(name='first', action='sql', sql='SELECT 1', mode='read-only'), step])
        job = jobs.save_job(fixture.owner, fixture.admin, expected_revision=0, key='save-later', definition=definition)
        fixture.b.objects[('sales', 'marts', 'published_data', 'tables')]['table-uuid'] = str(uuid.uuid4())
        fixture.posts.clear()
        with self.assertRaises(QueryError) as caught:
            jobs.submit_run(fixture.owner, fixture.admin, job['id'], expected_revision=1, arguments={}, key='stale')
        self.assertEqual(caught.exception.code, 'STALE_RESOURCE')
        self.assertEqual(fixture.posts, [])
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM db_job_runs WHERE job_id=?', (job['id'],)).fetchone()[0], 0)

    def test_unmaterialized_wap_destination_requires_absence(self):
        import test_manual_wap as fixtures
        fixture = self.start(fixtures.ManualWapTests())
        model = fixture.save()
        fixture.existing('published_data', schema='marts')
        with self.assertRaises(QueryError) as caught:
            jobs.save_job(fixture.owner, fixture.admin, expected_revision=0, key='occupied', definition=dict(name='occupied',
                steps=[dict(name='refresh', action='wap_refresh', model_id=model['model_id'], version=model['version'])]))
        self.assertEqual(caught.exception.code, 'DESTINATION_EXISTS')
        self.assertEqual(fixture.posts, [])

    def test_snapshot_export_adapter_keeps_rows_private(self):
        import test_manual_export as fixtures
        fixture = self.start(fixtures.ManualExportTests())
        owner = fixture.b.fixture.principal('owner')
        fixture.context = fixture.b.context(principal=owner, role='admin')
        result = self.run_job(owner, fixture.context,
            [dict(name='export', action='table_export', source=asdict(fixture.source), format='csv')])
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertIn('export_id', result['steps'][0]['summary'])
        self.assertNotIn('9007199254740993', json.dumps(result))
        self.assertEqual(len(fixture.posts), 2)
        self.assertTrue(all('FOR VERSION AS OF 101' in sql for sql in fixture.posts))
