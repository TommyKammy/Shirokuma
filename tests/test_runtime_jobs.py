"""Opt-in jobs acceptance against the dedicated resident TLS/Iceberg lab.

Set SHIROKUMA_RUNTIME_TESTS=1 and the existing backend credential/CA variables.
Job state and input files are temporary; only uniquely named synthetic tables
are written. No remote SQL Server, AI request, or external publication is used.
"""
import importlib
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import request_auth as auth
import trino_auth
import warehouse_resident as warehouse


@unittest.skipUnless(os.environ.get('SHIROKUMA_RUNTIME_TESTS') == '1',
                     'set SHIROKUMA_RUNTIME_TESTS=1 for dedicated jobs acceptance')
class RuntimeJobsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not trino_auth.password_mode():
            raise RuntimeError('Runtime jobs acceptance requires password-mode Trino')
        trino_auth.load_credentials()
        trino_auth.backend_ssl_context()
        cls.jobs = importlib.import_module('jobs_resident')
        cls.tunnel = warehouse.persistent_trino()
        cls.url = cls.tunnel.__enter__()
        cls.addClassCleanup(cls.tunnel.__exit__, None, None, None)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.directory = Path(temporary.name)
        self.addCleanup(temporary.cleanup)
        environment = patch.dict(os.environ, SHIROKUMA_STATE_DIR=str(self.directory),
                                 SHIROKUMA_AUTH_MODE='local', SHIROKUMA_AUTH_FILE='')
        environment.start()
        self.addCleanup(environment.stop)
        self.prefix = 'acceptance_jobs_' + uuid.uuid4().hex[:16]
        self.raw = 'iceberg.raw.' + self.prefix
        self.sandbox = 'iceberg.sandbox.' + self.prefix
        self.marts = 'iceberg.marts.' + self.prefix
        self.addCleanup(self.cleanup_tables)
        for schema in ('raw', 'sandbox', 'marts'):
            self.sql('CREATE SCHEMA IF NOT EXISTS iceberg.' + schema)

    def sql(self, statement):
        with auth.effective_role('admin'):
            return warehouse.execute(self.url, statement, {
                'X-Trino-User': 'shirokuma-runtime-jobs',
                'X-Trino-Catalog': 'iceberg', 'X-Trino-Schema': 'raw'})

    def cleanup_tables(self):
        for schema in ('raw', 'sandbox', 'marts'):
            rows = self.sql('SELECT table_name FROM iceberg.information_schema.tables '
                            'WHERE table_schema = ' + warehouse.literal(schema) +
                            ' AND table_name LIKE ' + warehouse.literal(self.prefix + '%'))['rows']
            for row in rows:
                if row[0].startswith(self.prefix):
                    self.sql('DROP TABLE IF EXISTS ' + warehouse.identifier('iceberg.' + schema + '.' + row[0]))

    def wait_terminal(self, run_id, timeout=960):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run = self.jobs.get_run(run_id, 'local', 'admin')
            if run['status'] not in {'queued', 'running'}:
                return run
            time.sleep(.1)
        self.fail('Synthetic jobs run did not reach a terminal state')

    def run_job(self, steps, arguments=None, parameters=None, role='admin'):
        name = 'job_' + uuid.uuid4().hex[:16]
        self.jobs.save_job(dict(name=name, parameters=parameters or {}, steps=steps), 'local', role)
        run = self.jobs.submit_run(name, arguments or {}, 'local', role)
        # Always wait before table/state cleanup, including assertion failures.
        self.addCleanup(self.wait_terminal, run['id'])
        return self.wait_terminal(run['id'])

    def stop_scheduler_and_wait(self):
        self.jobs.stop_scheduler()
        for run in self.jobs.list_runs('local', 'admin'):
            if run['status'] in {'queued', 'running'}:
                self.wait_terminal(run['id'])

    def test_ingest_parameterized_sql_preserves_quoted_value(self):
        source = self.directory / 'input.csv'
        source.write_text('id,label\n1,seed\n')
        value = "O'Brien'); INSERT INTO iceberg.marts.forbidden VALUES (7); --"
        run = self.run_job([
            dict(action='ingest', file=str(source), table=self.raw, mode='replace', format='csv'),
            dict(action='sql', sql='INSERT INTO ' + warehouse.identifier(self.raw) +
                 ' VALUES ({{id}}, {{label}})', mode='sandbox-write', schema='raw'),
            dict(action='sql', sql='SELECT id, label FROM ' + warehouse.identifier(self.raw), mode='read-only'),
        ], arguments={'id': 2, 'label': value}, parameters={
            'id': {'type': 'integer', 'required': True},
            'label': {'type': 'string', 'required': True}})
        self.assertEqual(run['status'], 'success')
        self.assertTrue(all(step['status'] == 'success' for step in run['steps']))
        self.assertEqual(run['steps'][-1]['summary']['row_count'], 2)
        self.assertEqual(self.sql('SELECT id, label FROM ' + warehouse.identifier(self.raw) +
                                 ' ORDER BY id')['rows'], [[1, 'seed'], [2, value]])
        self.assertTrue(all('rows' not in step.get('summary', {}) for step in run['steps']))

    def test_failed_sql_stops_later_insert(self):
        self.sql('CREATE TABLE ' + warehouse.identifier(self.raw) + ' (id BIGINT)')
        run = self.run_job([
            dict(action='sql', sql='SELECT missing_column FROM ' + warehouse.identifier(self.raw), mode='read-only'),
            dict(action='sql', sql='INSERT INTO ' + warehouse.identifier(self.raw) +
                 ' VALUES (99)', mode='sandbox-write', schema='raw'),
        ])
        self.assertEqual(run['status'], 'failed')
        self.assertEqual([step['status'] for step in run['steps']], ['failed', 'skipped'])
        self.assertEqual(self.sql('SELECT count(*) FROM ' + warehouse.identifier(self.raw))['rows'], [[0]])

    def test_engineer_background_role_keeps_raw_and_sandbox_scope(self):
        for table in (self.raw, self.sandbox, self.marts):
            self.sql('CREATE TABLE ' + warehouse.identifier(table) + ' (id BIGINT)')
        run = self.run_job([
            dict(action='sql', sql='INSERT INTO ' + warehouse.identifier(self.raw) +
                 ' VALUES (1)', mode='sandbox-write', schema='raw'),
            dict(action='sql', sql='INSERT INTO ' + warehouse.identifier(self.sandbox) +
                 ' VALUES (2)', mode='sandbox-write', schema='sandbox'),
            dict(action='sql', sql='SELECT query FROM system.runtime.queries', mode='read-only'),
            dict(action='sql', sql='INSERT INTO ' + warehouse.identifier(self.raw) +
                 ' VALUES (99)', mode='sandbox-write', schema='raw'),
        ], role='data_engineer')
        self.assertEqual(run['status'], 'failed')
        self.assertEqual([step['status'] for step in run['steps']],
                         ['success', 'success', 'failed', 'skipped'])
        self.assertEqual(self.sql('SELECT id FROM ' + warehouse.identifier(self.raw))['rows'], [[1]])
        self.assertEqual(self.sql('SELECT id FROM ' + warehouse.identifier(self.sandbox))['rows'], [[2]])
        self.assertEqual(self.sql('SELECT count(*) FROM ' + warehouse.identifier(self.marts))['rows'], [[0]])
        with self.assertRaises((PermissionError, ValueError)):
            self.jobs.save_job(dict(name='marts_denied', steps=[dict(action='sql',
                sql='INSERT INTO ' + warehouse.identifier(self.marts) + ' VALUES (99)',
                mode='sandbox-write')]), 'local', 'data_engineer')

    def test_existing_dbt_tests_run_as_background_job(self):
        run = self.run_job([dict(action='dbt_test')])
        self.assertEqual(run['status'], 'success')
        self.assertEqual(run['steps'][0]['status'], 'success')
        self.assertEqual(run['steps'][0]['summary']['model_count'], 7)

    def test_interval_scheduler_fires_once_and_retains_engineer_role(self):
        self.sql('CREATE TABLE ' + warehouse.identifier(self.raw) + ' (id BIGINT)')
        name = 'scheduled_' + uuid.uuid4().hex[:16]
        definition = dict(name=name, parameters={'id': {'type': 'integer', 'required': True}}, steps=[
            dict(action='sql', sql='INSERT INTO ' + warehouse.identifier(self.raw) +
                 ' VALUES ({{id}})', mode='sandbox-write', schema='raw'),
            # A default-admin worker would wrongly succeed here.
            dict(action='sql', sql='SELECT query FROM system.runtime.queries', mode='read-only'),
            dict(action='sql', sql='INSERT INTO ' + warehouse.identifier(self.raw) +
                 ' VALUES (99)', mode='sandbox-write', schema='raw'),
        ])
        self.jobs.save_job(definition, 'local', 'data_engineer')
        schedule = dict(name=name, kind='interval', every_minutes=1,
                        arguments={'id': 7}, enabled=True)
        self.jobs.save_schedule(schedule, 'local', 'data_engineer')
        self.jobs.start_scheduler(interval=.2)
        self.addCleanup(self.stop_scheduler_and_wait)
        # Real elapsed time exercises the daemon, not a direct injected tick.
        deadline = time.monotonic() + 100
        runs = []
        while time.monotonic() < deadline:
            runs = self.jobs.list_runs('local', 'admin')
            if runs:
                break
            time.sleep(.1)
        self.assertEqual(len(runs), 1, 'one due run must be claimed within the bounded wait')
        self.addCleanup(self.wait_terminal, runs[0]['id'])
        run = self.wait_terminal(runs[0]['id'])
        self.assertEqual(run['trigger'], 'schedule')
        self.assertTrue(run['scheduled_for'])
        self.assertEqual(run['role'], 'data_engineer')
        self.assertEqual(run['status'], 'failed')
        self.assertEqual([step['status'] for step in run['steps']], ['success', 'failed', 'skipped'])
        self.assertEqual(self.sql('SELECT id FROM ' + warehouse.identifier(self.raw))['rows'], [[7]])
        self.jobs.save_schedule({**schedule, 'enabled': False}, 'local', 'data_engineer')
        self.jobs.tick()
        self.jobs.tick()
        self.assertEqual(len(self.jobs.list_runs('local', 'admin')), 1)


if __name__ == '__main__':
    unittest.main()
