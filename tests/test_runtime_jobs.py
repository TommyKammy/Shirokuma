"""Opt-in jobs acceptance against the dedicated resident TLS/Iceberg lab.

Set SHIROKUMA_RUNTIME_TESTS=1 and the existing backend credential/CA variables.
Job state and input files are temporary; only uniquely named synthetic tables
are written. No remote SQL Server, AI request, or external publication is used.
"""
import base64
import importlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
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
        return self.run_saved_job(name, arguments, role)

    def run_saved_job(self, name, arguments=None, role='admin'):
        run = self.jobs.submit_run(name, arguments or {}, 'local', role)
        # Always wait before table/state cleanup, including assertion failures.
        self.addCleanup(self.wait_terminal, run['id'])
        return self.wait_terminal(run['id'])

    def stop_scheduler_and_wait(self):
        self.jobs.stop_scheduler()
        for run in self.jobs.list_runs('local', 'admin'):
            if run['status'] in {'queued', 'running'}:
                self.wait_terminal(run['id'])

    def start_http_workbench(self, accounts, state=None):
        """Use the normal entrypoint and isolated state, including its scheduler."""
        log_path = self.directory / ('http-' + uuid.uuid4().hex + '.log')
        env = {**os.environ, 'SHIROKUMA_AUTH_MODE': 'shared', 'SHIROKUMA_AUTH_FILE': str(accounts),
               'SHIROKUMA_STATE_DIR': str(state or self.directory)}
        with log_path.open('w') as log:
            child = subprocess.Popen([sys.executable, str(Path(__file__).resolve().parents[1] /
                'tools/local_experiment/workbench_server.py'), '--port', '0'], env=env,
                stdout=log, stderr=log, start_new_session=True)
        self.addCleanup(self.stop_http_workbench, child)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if child.poll() is not None:
                self.fail('Isolated Workbench exited before accepting requests')
            match = re.search(r'Shirokuma Workbench: (http://127\.0\.0\.1:\d+)', log_path.read_text())
            if match:
                url = match.group(1)
                try:
                    if self.http_request(url, '/api/session', user='alice')[0] == 200:
                        return child, url
                except (OSError, TimeoutError):
                    pass
            time.sleep(.1)
        self.fail('Isolated Workbench did not start within 30 seconds')

    @staticmethod
    def stop_http_workbench(child):
        if child.poll() is None:
            child.send_signal(signal.SIGINT)
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                # This group belongs only to this isolated test server and its tunnel.
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)

    @staticmethod
    def http_request(url, path, body=None, user='emily', role=None):
        headers = {'Content-Type': 'application/json', 'Origin': url}
        if user:
            token = base64.b64encode((user + ':test-' + user).encode()).decode()
            headers['Authorization'] = 'Basic ' + token
        if role:
            headers['X-Shirokuma-Role'] = role
        request = urllib.request.Request(url + path, headers=headers,
            data=None if body is None else json.dumps(body).encode())
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def test_http_restart_scheduled_to_manual_alert_lifecycle_and_revoked_owner(self):
        self.sql('CREATE TABLE ' + warehouse.identifier(self.raw) + ' (id BIGINT)')
        accounts = {}
        for user, role in [('alice', 'admin'), ('emily', 'data_engineer'),
                           ('oscar', 'data_engineer'), ('vera', 'bi_viewer')]:
            salt = os.urandom(16).hex()
            accounts[user] = dict(role=role, salt=salt,
                                  password_hash=auth.password_hash('test-' + user, salt))
        account_file = self.directory / 'accounts.json'
        account_file.write_text(json.dumps(accounts))
        child, url = self.start_http_workbench(account_file)

        def request(path, body=None, user='emily', expected=200, role=None):
            code, result = self.http_request(url, path, body, user, role)
            self.assertEqual(code, expected, result)
            return result

        def manual():
            run_id = request('/api/jobs/run', {'name': name, 'arguments': {'id': 7}}, expected=202)['run']['id']
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                run = request('/api/jobs/detail', {'run_id': run_id})['run']
                if run['status'] not in {'queued', 'running'}:
                    self.assertEqual(run['status'], 'success', run)
                    self.assertEqual(run['trigger'], 'manual')
                    self.assertEqual(run['role'], 'data_engineer')
                    return run
                time.sleep(.1)
            self.fail('HTTP manual run did not finish')

        name = 'restart_quality'
        definition = dict(name=name, parameters={'id': {'type': 'integer', 'required': True}}, steps=[
            dict(action='sql', sql='INSERT INTO ' + warehouse.identifier(self.raw) + ' VALUES ({{id}})',
                 mode='sandbox-write', schema='raw'),
            dict(action='alert', sql='SELECT count(*) > 0 AND min(id) >= 0 FROM ' + warehouse.identifier(self.raw),
                 message='Synthetic scheduled and manual condition'),
        ])
        request('/api/jobs/save', definition)
        request('/api/jobs/save', dict(name='revoked_owner', steps=[dict(action='alert',
            sql='SELECT TRUE', message='Must never run after revocation')]), user='oscar')
        for path in ('/api/jobs', '/api/jobs/runs', '/api/jobs/schedules', '/api/alerts'):
            request(path, user=None, expected=401)
            request(path, user='vera', expected=403)
        request('/api/jobs/run', {'name': name, 'arguments': {'id': 7}}, user='oscar', expected=404)
        request('/api/jobs/run', {'name': name, 'arguments': {'id': 7}}, role='admin', expected=403)
        saved = request('/api/jobs/schedule', dict(name=name, kind='interval', every_minutes=1,
            enabled=True, arguments={'id': 7}))['schedule']
        request('/api/jobs/schedule', dict(name='revoked_owner', kind='interval', every_minutes=1,
            enabled=True, arguments={}), user='oscar')
        accounts['oscar']['role'] = 'bi_viewer'
        account_file.write_text(json.dumps(accounts))
        self.stop_http_workbench(child)
        child, url = self.start_http_workbench(account_file)
        restored = request('/api/jobs/schedules')['schedules'][0]
        self.assertEqual(restored['next_run_at'], saved['next_run_at'])
        self.assertEqual((restored['owner'], restored['role'], restored['auth_mode']),
                         ('emily', 'data_engineer', 'shared'))

        # Wait for actual wall time through the normal server's ten-second poll.
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            runs = request('/api/jobs/runs', user='alice')['runs']
            if len(runs) == 2 and all(run['status'] not in {'queued', 'running'} for run in runs):
                break
            time.sleep(.5)
        self.assertEqual(len(runs), 2, runs)
        scheduled = next(run for run in runs if run['job_name'] == name)
        self.assertEqual((scheduled['status'], scheduled['actor'], scheduled['role'], scheduled['trigger']),
                         ('success', 'emily', 'data_engineer', 'schedule'))
        self.assertEqual(scheduled['scheduled_for'], saved['next_run_at'])
        revoked = next(run for run in runs if run['job_name'] == 'revoked_owner')
        self.assertEqual(revoked['status'], 'skipped')
        schedules = request('/api/jobs/schedules', user='alice')['schedules']
        self.assertFalse(next(item for item in schedules if item['name'] == 'revoked_owner')['enabled'])
        request('/api/jobs/schedule', {'name': name, 'enabled': False})
        self.assertEqual(self.sql('SELECT id FROM ' + warehouse.identifier(self.raw))['rows'], [[7]])
        first = request('/api/alerts')['alerts'][0]
        self.assertEqual(first['first_run_id'], scheduled['id'])
        self.assertEqual(first['owner'], 'emily')
        request('/api/alerts/ack', {'alert_id': first['id']}, user='alice', expected=403)
        request('/api/alerts/ack', {'alert_id': first['id']}, user='vera', expected=403)
        confirmed = request('/api/alerts/ack', {'alert_id': first['id']})['alert']
        self.stop_http_workbench(child)
        child, url = self.start_http_workbench(account_file)
        self.assertEqual(request('/api/alerts')['alerts'][0], confirmed)
        self.assertEqual(len(request('/api/jobs/runs', user='alice')['runs']), 2)

        manual()
        ongoing = request('/api/alerts')
        self.assertEqual(len(ongoing['alerts']), 1)
        self.assertEqual(ongoing['alerts'][0]['id'], first['id'])
        self.assertEqual(ongoing['alerts'][0]['occurrences'], 2)
        self.assertEqual(ongoing['unacknowledged_count'], 0)
        self.sql('UPDATE ' + warehouse.identifier(self.raw) + ' SET id = -1')
        manual()
        resolved = request('/api/alerts')['alerts'][0]
        self.assertEqual(resolved['resolution'], 'condition_cleared')
        self.assertEqual(resolved['last_seen_at'], ongoing['alerts'][0]['last_seen_at'])
        self.sql('DELETE FROM ' + warehouse.identifier(self.raw) + ' WHERE id < 0')
        manual()
        notices = request('/api/alerts')
        self.assertEqual(len(notices['alerts']), 2)
        active = next(item for item in notices['alerts'] if item['resolved_at'] is None)
        self.assertNotEqual(active['id'], first['id'])
        self.assertEqual(notices['unacknowledged_count'], 1)
        # Restore an independently usable ledger: history survives, schedules pause.
        import disaster_recovery as dr
        request('/api/jobs/schedule', dict(name=name, kind='interval', every_minutes=60,
            enabled=True, arguments={'id': 7}))
        archive = self.directory / 'backup.tar.gz'
        dr.backup(archive, include_tables=False)
        self.stop_http_workbench(child)
        restored_state = self.directory / 'restored'
        with patch.object(dr, 'MODELS_DIR', restored_state / 'models'):
            dr.restore(archive, target_state_dir=restored_state)
        child, url = self.start_http_workbench(account_file, restored_state)
        self.assertEqual(request('/api/alerts'), notices)
        self.assertEqual(len(request('/api/jobs/runs', user='alice')['runs']), 5)
        restored_schedules = request('/api/jobs/schedules', user='alice')['schedules']
        self.assertTrue(all(not item['enabled'] and item['next_run_at'] is None for item in restored_schedules))
        self.stop_http_workbench(child)

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

    def test_alert_true_acknowledge_resolve_and_retrigger(self):
        self.sql('CREATE TABLE ' + warehouse.identifier(self.raw) + ' AS SELECT TRUE AS triggered')
        name = 'alert_' + uuid.uuid4().hex[:16]
        self.jobs.save_job(dict(name=name, steps=[dict(action='alert',
            sql='SELECT triggered FROM ' + warehouse.identifier(self.raw),
            message='Synthetic condition requires attention', severity='warning')]), 'local', 'data_engineer')
        for _ in range(2):
            self.assertEqual(self.run_saved_job(name, role='data_engineer')['status'], 'success')
        notifications = self.jobs.list_alerts('local', 'data_engineer')
        self.assertEqual(len(notifications['alerts']), 1)
        first = notifications['alerts'][0]
        self.assertEqual(notifications['unacknowledged_count'], 1)
        self.jobs.acknowledge(first['id'], 'local', 'data_engineer')
        self.assertEqual(self.run_saved_job(name, role='data_engineer')['status'], 'success')
        notifications = self.jobs.list_alerts('local', 'data_engineer')
        self.assertEqual(len(notifications['alerts']), 1)
        self.assertTrue(notifications['alerts'][0]['acknowledged_at'])
        self.assertIsNone(notifications['alerts'][0]['resolved_at'])
        self.assertEqual(notifications['unacknowledged_count'], 0)
        self.sql('UPDATE ' + warehouse.identifier(self.raw) + ' SET triggered = FALSE')
        self.assertEqual(self.run_saved_job(name, role='data_engineer')['status'], 'success')
        self.assertTrue(self.jobs.list_alerts('local', 'data_engineer')['alerts'][0]['resolved_at'])
        self.sql('UPDATE ' + warehouse.identifier(self.raw) + ' SET triggered = TRUE')
        self.assertEqual(self.run_saved_job(name, role='data_engineer')['status'], 'success')
        notifications = self.jobs.list_alerts('local', 'data_engineer')
        self.assertEqual(len(notifications['alerts']), 2)
        active = next(alert for alert in notifications['alerts'] if alert['resolved_at'] is None)
        self.assertNotEqual(active['id'], first['id'])
        self.assertIsNone(active['acknowledged_at'])
        self.assertEqual(notifications['unacknowledged_count'], 1)
        # Resolution does not acknowledge an unread notification.
        self.sql('UPDATE ' + warehouse.identifier(self.raw) + ' SET triggered = FALSE')
        self.assertEqual(self.run_saved_job(name, role='data_engineer')['status'], 'success')
        notifications = self.jobs.list_alerts('local', 'data_engineer')
        self.assertEqual(notifications['unacknowledged_count'], 1)
        self.assertTrue(all(alert['resolved_at'] for alert in notifications['alerts']))
        self.jobs.acknowledge(active['id'], 'local', 'data_engineer')
        self.assertEqual(self.jobs.list_alerts('local', 'data_engineer')['unacknowledged_count'], 0)

    def test_invalid_alert_results_preserve_notification_and_engineer_boundary(self):
        self.sql('CREATE TABLE ' + warehouse.identifier(self.raw) + ' AS SELECT TRUE AS triggered')
        self.sql('CREATE TABLE ' + warehouse.identifier(self.sandbox) + ' (id BIGINT)')
        name = 'alert_invalid_' + uuid.uuid4().hex[:16]
        self.jobs.save_job(dict(name=name, steps=[
            dict(action='alert', sql='SELECT triggered FROM ' + warehouse.identifier(self.raw),
                 message='Synthetic condition'),
            dict(action='sql', sql='INSERT INTO ' + warehouse.identifier(self.sandbox) +
                 ' VALUES (99)', mode='sandbox-write'),
        ]), 'local', 'data_engineer')
        first_run = self.run_saved_job(name, role='data_engineer')
        self.assertEqual(first_run['status'], 'success')
        self.sql('DELETE FROM ' + warehouse.identifier(self.sandbox))
        first = self.jobs.list_alerts('local', 'data_engineer')['alerts'][0]
        for values in ('SELECT 1 AS triggered',
                       'SELECT triggered FROM (VALUES TRUE, FALSE) AS t(triggered)'):
            self.sql('CREATE OR REPLACE TABLE ' + warehouse.identifier(self.raw) + ' AS ' + values)
            run = self.run_saved_job(name, role='data_engineer')
            self.assertEqual(run['status'], 'failed')
            self.assertEqual([step['status'] for step in run['steps']], ['failed', 'skipped'])
            notifications = self.jobs.list_alerts('local', 'data_engineer')['alerts']
            self.assertEqual(len(notifications), 1)
            self.assertEqual(notifications[0]['id'], first['id'])
            self.assertEqual(notifications[0]['last_run_id'], first_run['id'])
            self.assertIsNone(notifications[0]['resolved_at'])
            self.assertEqual(self.sql('SELECT count(*) FROM ' + warehouse.identifier(self.sandbox))['rows'], [[0]])
        denied = self.run_job([dict(action='alert',
            sql='SELECT count(*) > 0 FROM system.runtime.queries',
            message='Must remain inaccessible')], role='data_engineer')
        self.assertEqual(denied['status'], 'failed')
        self.assertEqual(len(self.jobs.list_alerts('local', 'data_engineer')['alerts']), 1)

    def test_interval_scheduler_fires_once_and_retains_engineer_role(self):
        self.sql('CREATE TABLE ' + warehouse.identifier(self.raw) + ' (id BIGINT)')
        name = 'scheduled_' + uuid.uuid4().hex[:16]
        definition = dict(name=name, parameters={'id': {'type': 'integer', 'required': True}}, steps=[
            dict(action='alert', sql='SELECT TRUE', message='Synthetic scheduled condition'),
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
        self.assertEqual([step['status'] for step in run['steps']], ['success', 'success', 'failed', 'skipped'])
        self.assertEqual(self.sql('SELECT id FROM ' + warehouse.identifier(self.raw))['rows'], [[7]])
        notifications = self.jobs.list_alerts('local', 'data_engineer')
        self.assertEqual(len(notifications['alerts']), 1)
        self.assertEqual(notifications['alerts'][0]['first_run_id'], run['id'])
        self.assertEqual(notifications['unacknowledged_count'], 1)
        self.jobs.save_schedule({**schedule, 'enabled': False}, 'local', 'data_engineer')
        self.jobs.tick()
        self.jobs.tick()
        self.assertEqual(len(self.jobs.list_runs('local', 'admin')), 1)


if __name__ == '__main__':
    unittest.main()
