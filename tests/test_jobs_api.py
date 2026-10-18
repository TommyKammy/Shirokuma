"""Job HTTP boundaries use the authenticated caller, including async submission."""
import base64
import csv
import http.client
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import request_auth as auth
import jobs_resident as jobs
import alerts_resident as alerts
import workbench_server as wb


class JobsAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.accounts = {}
        for user, role in [('alice', 'admin'), ('emily', 'data_engineer'),
                           ('oscar', 'data_engineer'), ('vera', 'bi_viewer')]:
            salt = os.urandom(16).hex()
            cls.accounts[user] = dict(role=role, salt=salt,
                                      password_hash=auth.password_hash('test-' + user, salt))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        accounts = Path(temporary.name) / 'accounts.json'
        accounts.write_text(json.dumps(self.accounts))
        env = patch.dict(os.environ, SHIROKUMA_AUTH_MODE='shared',
                         SHIROKUMA_AUTH_FILE=str(accounts),
                         SHIROKUMA_STATE_DIR=temporary.name,
                         SHIROKUMA_TRINO_AUTH_MODE='local')
        env.start()
        self.addCleanup(env.stop)
        guard = patch.object(auth, 'validate_shared_backend')
        guard.start()
        self.addCleanup(guard.stop)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), wb.WorkbenchHandler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, path, body=None, user='emily', headers=None):
        request_headers = {'Content-Type': 'application/json', **(headers or {})}
        if user:
            encoded = base64.b64encode((user + ':test-' + user).encode()).decode()
            request_headers['Authorization'] = 'Basic ' + encoded
        client = http.client.HTTPConnection(*self.server.server_address, timeout=10)
        try:
            client.request('GET' if body is None else 'POST', path,
                           None if body is None else json.dumps(body), request_headers)
            response = client.getresponse()
            return response.status, json.loads(response.read())
        finally:
            client.close()

    @staticmethod
    def definition(name='daily_check'):
        return dict(name=name, description='Check one parameter',
                    parameters={'value': {'type': 'integer'}},
                    steps=[dict(name='Check', action='sql', sql='SELECT {{value}} AS value',
                                mode='read-only', schema='marts')])

    def test_authentication_and_viewer_denied_on_every_route(self):
        routes = [('/api/jobs', None), ('/api/jobs/runs', None),
                  ('/api/alerts', None), ('/api/alerts/ack', {'alert_id': 'missing'}),
                  ('/api/jobs/schedules', None), ('/api/jobs/schedule', self.schedule()),
                  ('/api/jobs/save', self.definition()),
                  ('/api/jobs/run', {'name': 'daily_check'}),
                  ('/api/jobs/detail', {'run_id': 'missing'})]
        for path, body in routes:
            with self.subTest(path=path):
                self.assertEqual(self.request(path, body, user=None)[0], 401)
                self.assertEqual(self.request(path, body, user='vera')[0], 403)
                self.assertEqual(self.request(path, body, user='emily',
                    headers={'X-Shirokuma-Role': 'admin', 'X-Trino-User': 'admin'})[0], 403)

    def test_tls_proxy_origin_uses_same_host_and_shared_authentication_only(self):
        def request(origin, host, extra=None, name='origin_check'):
            headers = {'Host': host, 'Origin': origin, 'Content-Type': 'application/json', **(extra or {})}
            headers['Authorization'] = 'Basic ' + base64.b64encode(b'alice:test-alice').decode()
            client = http.client.HTTPConnection(*self.server.server_address, timeout=10)
            try:
                client.request('POST', '/api/jobs/save', json.dumps(self.definition(name)), headers)
                response = client.getresponse()
                return response.status, dict(response.getheaders()), json.loads(response.read())
            finally:
                client.close()
        for index, origin in enumerate(('https://workbench.example', 'http://workbench.example')):
            code, headers, body = request(origin, 'workbench.example', name='origin_shared_' + str(index))
            self.assertEqual(code, 200)
            self.assertEqual(body['job']['owner'], 'alice')
            self.assertEqual(headers.get('Access-Control-Allow-Origin'), origin)
        for origin, host, forwarded in (
                ('https://other.example', 'workbench.example', {}),
                ('https://workbench.example', '127.0.0.1',
                 {'X-Forwarded-Host': 'workbench.example', 'X-Forwarded-Proto': 'https'})):
            code, headers, _ = request(origin, host, forwarded)
            self.assertEqual(code, 403)
            self.assertNotIn('Access-Control-Allow-Origin', headers)
        with patch.dict(os.environ, SHIROKUMA_AUTH_MODE='local', SHIROKUMA_AUTH_FILE=''):
            code, headers, _ = request('https://workbench.example', 'workbench.example')
            self.assertEqual(code, 403)
            self.assertNotIn('Access-Control-Allow-Origin', headers)
            code, headers, body = request('http://workbench.example', 'workbench.example', name='origin_local')
            self.assertEqual(code, 200)
            self.assertEqual(body['job']['owner'], 'local')
            self.assertEqual(headers.get('Access-Control-Allow-Origin'), 'http://workbench.example')

    def test_oversized_csv_field_is_rejected_before_run_admission(self):
        path = Path(os.environ['SHIROKUMA_STATE_DIR']) / 'oversized.csv'
        marker = 'PRIVATE_CSV_CONTENT_'
        path.write_text('id\n' + marker + 'x' * (csv.field_size_limit() + 10) + '\n')
        definition = dict(name='csv_preflight', steps=[dict(action='ingest', file=str(path),
                          table='raw.csv_preflight', mode='append')])
        self.assertEqual(self.request('/api/jobs/save', definition, user='alice')[0], 200)
        with patch.object(jobs, '_start_worker') as worker:
            code, body = self.request('/api/jobs/run', {'name': 'csv_preflight'}, user='alice')
            self.assertEqual(code, 400)
            self.assertNotIn(marker, json.dumps(body))
            worker.assert_not_called()
        self.assertEqual(self.request('/api/jobs/runs', user='alice')[1], {'runs': []})

    def test_explicit_caller_identity_and_selected_role_reach_runner(self):
        with patch.object(jobs, 'submit_run', return_value={'id': 'accepted', 'status': 'running'}) as submit:
            code, body = self.request('/api/jobs/run', {'name': 'daily_check', 'arguments': {'value': 7}},
                                      user='alice', headers={'X-Shirokuma-Role': 'data_engineer'})
            self.assertEqual(code, 202)
            self.assertEqual(body['run']['id'], 'accepted')
            submit.assert_called_once_with('daily_check', {'value': 7}, 'alice', 'data_engineer')

    def open_alert(self):
        definition = dict(name='quality_check', steps=[dict(action='alert',
                          sql='SELECT TRUE', message='データを確認してください')])
        self.assertEqual(self.request('/api/jobs/save', definition)[0], 200)
        with patch.object(jobs.wr, 'query', return_value=dict(result='verified', rows=[[True]],
                columns=[dict(name='condition', type='boolean')], truncated=False)), \
             patch.object(jobs.wr, 'telemetry', return_value='skipped'):
            code, response = self.request('/api/jobs/run', {'name': 'quality_check'})
            self.assertEqual(code, 202)
            for _ in range(500):
                run = jobs.get_run(response['run']['id'], 'emily', 'data_engineer')
                if run['status'] not in ('queued', 'running'):
                    for thread in threading.enumerate():
                        if thread.name == 'job-' + run['id'][:8]:
                            thread.join(2)
                    break
                time.sleep(.01)
            self.assertEqual(run['status'], 'success', run)
        return self.request('/api/alerts')[1]['alerts'][0]

    def test_alert_visibility_and_owner_only_idempotent_acknowledgement(self):
        alert = self.open_alert()
        self.assertEqual(alert['owner'], 'emily')
        self.assertEqual(self.request('/api/alerts')[1]['unacknowledged_count'], 1)
        self.assertEqual(self.request('/api/alerts', user='oscar')[1],
                         {'alerts': [], 'unacknowledged_count': 0})
        admin_view = self.request('/api/alerts', user='alice')[1]
        self.assertEqual(admin_view['unacknowledged_count'], 1)
        self.assertFalse(admin_view['alerts'][0]['can_manage'])
        for user in ('oscar', 'alice'):
            self.assertIn(self.request('/api/alerts/ack', {'alert_id': alert['id']}, user=user)[0], (403, 404))
        code, response = self.request('/api/alerts/ack', {'alert_id': alert['id']})
        self.assertEqual(code, 200)
        self.assertEqual(response['alert']['acknowledged_by'], 'emily')
        self.assertIsNone(response['alert']['resolved_at'])
        self.assertEqual(self.request('/api/alerts')[1]['unacknowledged_count'], 0)
        self.assertEqual(self.request('/api/alerts/ack', {'alert_id': alert['id']})[1], response)
        self.assertEqual(self.request('/api/alerts/ack', {'alert_id': 'missing'})[0], 404)

    def test_alert_ack_uses_authenticated_identity_and_selected_role(self):
        with patch.object(alerts, 'acknowledge', return_value={'id': 'a'}) as acknowledge:
            code, body = self.request('/api/alerts/ack', {'alert_id': 'a'}, user='alice',
                                      headers={'X-Shirokuma-Role': 'data_engineer'})
        self.assertEqual((code, body), (200, {'alert': {'id': 'a'}}))
        acknowledge.assert_called_once_with('a', 'alice', 'data_engineer')

    def test_alert_ack_rejects_identity_fields_invalid_input_and_cross_origin(self):
        with patch.object(alerts, 'acknowledge') as acknowledge:
            for body in ({}, {'alert_id': 1}, {'alert_id': ''}, {'alert_id': 'a', 'owner': 'alice'},
                         {'alert_id': 'a', 'actor': 'alice'}, {'alert_id': 'a', 'role': 'admin'}):
                self.assertEqual(self.request('/api/alerts/ack', body)[0], 400)
            self.assertEqual(self.request('/api/alerts/ack', {'alert_id': 'a'},
                                         headers={'Origin': 'https://example.invalid'})[0], 403)
            acknowledge.assert_not_called()

    def test_job_definitions_are_isolated_by_owner(self):
        code, _ = self.request('/api/jobs/save', self.definition())
        self.assertEqual(code, 200)
        self.assertEqual(len(self.request('/api/jobs')[1]['jobs']), 1)
        self.assertEqual(self.request('/api/jobs', user='oscar')[1]['jobs'], [])
        self.assertEqual(len(self.request('/api/jobs', user='alice')[1]['jobs']), 1)
        self.assertIn(self.request('/api/jobs/save', self.definition(), user='oscar')[0], (403, 404))
        self.assertIn(self.request('/api/jobs/run', {'name': 'daily_check', 'arguments': {'value': 1}},
                                   user='oscar')[0], (403, 404))

    def test_privileged_operations_cannot_be_saved_by_engineer(self):
        for action in ('dbt_build', 'dbt_test', 'wap_refresh', 'wap_publish', 'ingest'):
            definition = self.definition()
            definition['parameters'] = {}
            step = {'action': action}
            if action.startswith('wap_'):
                step['table'] = 'daily_check'
            if action == 'ingest':
                step.update(file='/tmp/data.csv', table='raw.daily_check', mode='replace')
            definition['steps'] = [step]
            with self.subTest(action=action):
                self.assertEqual(self.request('/api/jobs/save', definition)[0], 403)

    def test_client_cannot_supply_an_execution_identity(self):
        for key in ('role', 'actor', 'username', 'run_as'):
            with patch.object(jobs, 'submit_run') as submit:
                code, _ = self.request('/api/jobs/run', {'name': 'daily_check', key: 'admin'})
                self.assertEqual(code, 400)
                submit.assert_not_called()

    def test_conflict_and_missing_run_have_distinct_http_statuses(self):
        with patch.object(jobs, 'submit_run', side_effect=jobs.JobConflict('A job is running')):
            self.assertEqual(self.request('/api/jobs/run', {'name': 'daily_check'})[0], 409)
        with patch.object(jobs, 'get_run', side_effect=jobs.JobNotFound('Run not found')):
            self.assertEqual(self.request('/api/jobs/detail', {'run_id': 'missing'})[0], 404)

    def test_cross_origin_and_invalid_request_do_not_submit(self):
        with patch.object(jobs, 'submit_run') as submit:
            self.assertEqual(self.request('/api/jobs/run', {'name': 'daily_check'},
                                         headers={'Origin': 'https://example.invalid'})[0], 403)
            self.assertEqual(self.request('/api/jobs/run', {'name': 42})[0], 400)
            self.assertEqual(self.request('/api/jobs/detail', {'run_id': 'x', 'actor': 'alice'})[0], 400)
            submit.assert_not_called()

    @staticmethod
    def schedule():
        return dict(name='daily_check', kind='interval', every_minutes=60,
                    arguments={'value': 7}, enabled=False)

    def test_schedule_uses_authenticated_identity_and_selected_role(self):
        payload = self.schedule()
        with patch.object(jobs, 'save_schedule', return_value=payload) as save:
            code, body = self.request('/api/jobs/schedule', payload, user='alice',
                                      headers={'X-Shirokuma-Role': 'data_engineer'})
        self.assertEqual(code, 200)
        self.assertEqual(body['schedule'], payload)
        save.assert_called_once_with(payload, 'alice', 'data_engineer')

    def test_schedule_owner_visibility_and_disabled_persistence(self):
        self.assertEqual(self.request('/api/jobs/save', self.definition())[0], 200)
        code, body = self.request('/api/jobs/schedule', self.schedule())
        self.assertEqual(code, 200)
        self.assertEqual(body['schedule']['role'], 'data_engineer')
        self.assertFalse(body['schedule']['enabled'])
        self.assertEqual(body['schedule']['auth_mode'], 'shared')
        self.assertEqual(self.request('/api/jobs/schedules')[1]['schedules'][0]['arguments'], {'value': 7})
        self.assertEqual(self.request('/api/jobs/schedules', user='oscar')[1]['schedules'], [])
        admin_view = self.request('/api/jobs/schedules', user='alice')[1]['schedules']
        self.assertEqual(len(admin_view), 1)
        self.assertFalse(admin_view[0]['can_manage'])
        for user in ('oscar', 'alice'):
            self.assertIn(self.request('/api/jobs/schedule', self.schedule(), user=user)[0], (403, 404))

    def test_schedule_rejects_identity_fields_and_cross_origin(self):
        self.request('/api/jobs/save', self.definition())
        for key, value in [('role', 'admin'), ('actor', 'alice'), ('owner', 'alice'),
                           ('auth_mode', 'local'), ('next_run_at', '2000-01-01T00:00:00Z')]:
            self.assertEqual(self.request('/api/jobs/schedule', {**self.schedule(), key: value})[0], 400)
        with patch.object(jobs, 'save_schedule') as save:
            self.assertEqual(self.request('/api/jobs/schedule', self.schedule(),
                                         headers={'Origin': 'https://example.invalid'})[0], 403)
            save.assert_not_called()
        self.assertEqual(self.request('/api/jobs/schedules')[1]['schedules'], [])

    def test_schedule_validates_arguments_and_cannot_expand_selected_role(self):
        self.request('/api/jobs/save', self.definition())
        self.assertEqual(self.request('/api/jobs/schedule', {**self.schedule(), 'arguments': {'value': '7'}})[0], 400)
        privileged = dict(name='admin_build', parameters={}, steps=[{'action': 'dbt_build'}])
        self.assertEqual(self.request('/api/jobs/save', privileged, user='alice')[0], 200)
        self.assertEqual(self.request('/api/jobs/schedule', {**self.schedule(), 'name': 'admin_build', 'arguments': {}},
                                      user='alice', headers={'X-Shirokuma-Role': 'data_engineer'})[0], 403)

    def test_owner_can_pause_without_execution_role_or_replacing_arguments(self):
        definition = dict(name='admin_build', steps=[{'action': 'dbt_build'}])
        self.assertEqual(self.request('/api/jobs/save', definition, user='alice')[0], 200)
        payload = {**self.schedule(), 'name': 'admin_build', 'arguments': {}, 'enabled': True}
        self.assertEqual(self.request('/api/jobs/schedule', payload, user='alice')[0], 200)
        pause = {'name': 'admin_build', 'enabled': False}
        self.assertEqual(self.request('/api/jobs/schedule', pause, user='emily')[0], 403)
        code, body = self.request('/api/jobs/schedule', pause, user='alice',
                                  headers={'X-Shirokuma-Role': 'data_engineer'})
        self.assertEqual(code, 200)
        self.assertFalse(body['schedule']['enabled'])
        self.assertIsNone(body['schedule']['next_run_at'])
        self.assertEqual(body['schedule']['role'], 'admin')
        self.assertEqual(body['schedule']['arguments'], {})
        self.assertEqual(self.request('/api/jobs/schedule', {'name': 'missing', 'enabled': False})[0], 404)


if __name__ == '__main__':
    unittest.main()
