"""Normal HTTP Job routing with real Basic identity and durable backend service."""
from dataclasses import asdict
from http.client import HTTPResponse
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import db_jobs as jobs
import runtime_profile
import workbench_server as wb
import test_db_jobs as fixtures


class JobHTTPTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.DatabaseJobsTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.c = asdict(self.f.c)
        for p in (patch.object(runtime_profile, 'active', return_value=True),
                  patch.object(wb.WorkbenchHandler, 'log_message')):
            p.start()
            self.addCleanup(p.stop)

    def request(self, path='/api/v1/jobs', body=None, key=None, context=True, headers=None):
        if body is None and context:
            path += '?' + urlencode({'context': json.dumps(self.c)})
        values = dict(self.f.f.fixture.headers())
        values['X-Shirokuma-Role'] = self.c.get('selected_role', 'data_engineer')
        if body is not None:
            values['Content-Type'] = 'application/json'
        if key is not None:
            values['Idempotency-Key'] = key
        values.update(headers or {})
        raw = b'' if body is None else json.dumps(body).encode()
        values.update({'Host': '127.0.0.1', 'Connection': 'close', 'Content-Length': str(len(raw))})
        request = (('GET' if body is None else 'POST') + ' ' + path + ' HTTP/1.1\r\n' +
            ''.join(k + ': ' + v + '\r\n' for k, v in values.items()) + '\r\n').encode() + raw
        # Exercise the real HTTP parser, authentication and handler without a
        # listening socket, which is unavailable in the worker sandbox.
        class Connection:
            def __init__(self, incoming):
                self.incoming, self.outgoing = io.BytesIO(incoming), io.BytesIO()
            def makefile(self, *args):
                return self.incoming
            def sendall(self, data):
                self.outgoing.write(data)
        connection = Connection(request)
        wb.WorkbenchHandler(connection, ('127.0.0.1', 1), SimpleNamespace())
        response = HTTPResponse(Connection(connection.outgoing.getvalue()))
        response.begin()
        return response.status, json.loads(response.read())

    def save(self, **extra):
        return self.request(body=dict(context=self.c, expected_revision=0, definition=self.f.definition(), **extra), key='save')

    def test_retiring_cancel_exact_tuple_owner_account_and_no_results(self):
        job = self.save()[1]['job']
        submit = '/api/v1/jobs/' + job['id'] + '/runs'
        body = dict(context=self.c, expected_revision=1, arguments={})
        run = self.request(submit, body, key='run')[1]['run']
        show = '/api/v1/jobs/runs/' + run['id']
        self.f.f.retire()
        for name, value in self.c.items():
            changed = dict(self.c, **{name: value + 1 if type(value) is int else str(value) + '-wrong'})
            self.assertGreaterEqual(self.request(show + '/cancel', dict(context=changed))[0], 400, name)
        self.assertEqual(self.request(show + '/cancel', dict(context=self.c),
            headers=self.f.f.fixture.headers('owner'))[0], 404)
        for path in (show, '/api/v1/jobs/runs', '/api/v1/jobs'):
            self.assertGreaterEqual(self.request(path)[0], 400)
        self.assertGreaterEqual(self.request(submit, body, key='denied')[0], 400)
        self.assertGreaterEqual(self.request(show + '/reconcile', dict(context=self.c))[0], 400)
        status, result = self.request(show + '/cancel', dict(context=self.c))
        self.assertEqual(status, 200, result)
        self.assertEqual(set(result['run']), {'id', 'status'})
        del self.f.f.fixture.users['alice']
        self.f.f.fixture.save()
        self.assertIn(self.request(show + '/cancel', dict(context=self.c))[0], (401, 403))
        self.assertFalse(self.f.f.calls)

    def test_save_outer_job_id_cas_and_show_list_envelopes(self):
        status, result = self.save()
        self.assertEqual(status, 200, result)
        job = result['job']
        self.assertEqual(result['context'], self.c)
        self.assertEqual(self.request('/api/v1/jobs/' + job['id'])[1]['job'], job)
        self.assertEqual(self.request()[1]['jobs'], [job])
        body = dict(context=self.c, expected_revision=1, job_id=job['id'], definition=self.f.definition(sql='SELECT 2'))
        self.assertEqual(self.request(body=body, key='edit')[1]['job']['revision'], 2)
        self.assertEqual(self.request(body=body, key='stale')[0], 409)
        body['definition']['job_id'] = body.pop('job_id')
        self.assertEqual(self.request(body=body, key='nested')[0], 400)
        self.assertEqual(self.f.f.calls, [])

    def test_run_observe_history_cancel_and_reconcile_routes(self):
        job = self.save()[1]['job']
        path = '/api/v1/jobs/' + job['id'] + '/runs'
        body = dict(context=self.c, expected_revision=1, arguments={})
        status, result = self.request(path, body, key='run')
        self.assertEqual(status, 200, result)
        run = result['run']
        show = '/api/v1/jobs/runs/' + run['id']
        self.assertEqual(self.request(show)[1]['run']['id'], run['id'])
        self.assertEqual(self.request('/api/v1/jobs/runs')[1]['runs'][0]['id'], run['id'])
        self.assertEqual(self.request(path, body, key='run')[1]['run']['id'], run['id'])
        self.assertEqual(self.request(show + '/cancel', dict(context=self.c))[0], 200)
        jobs._worker(run['id'])
        self.assertEqual(self.request(show)[1]['run']['status'], 'canceled')
        run = self.request(path, body, key='lost')[1]['run']
        self.f.clear_jobs()
        show = '/api/v1/jobs/runs/' + run['id']
        self.assertEqual(self.request(show)[1]['run']['status'], 'unknown')
        self.assertEqual(self.request(show + '/reconcile', dict(context=self.c))[1]['run']['status'], 'failed')
        self.assertEqual(self.f.f.calls, [])

    def test_auth_roles_full_context_key_and_framing(self):
        self.assertEqual(self.request(headers={'Authorization': 'Basic invalid'})[0], 401)
        self.assertEqual(self.request(context=False)[0], 400)
        for name in self.c:
            original = self.c
            self.c = {k: v for k, v in original.items() if k != name}
            self.assertEqual(self.request()[0], 400)
            self.c = original
        self.assertEqual(self.request(headers={'X-Shirokuma-Role': 'admin'})[0], 400)
        body = dict(context=self.c, expected_revision=0, definition=self.f.definition())
        self.assertEqual(self.request(body=body)[0], 400)
        self.assertEqual(self.save(admission={})[0], 400)
        self.assertEqual(self.save(backend_pin={})[0], 400)
        self.assertEqual(self.request(body=body, key='save', headers={'Origin': 'https://untrusted.invalid'})[0], 403)
        self.assertEqual(self.request('/api/v1/jobs?context={}&context={}', context=False)[0], 400)

    def test_grant_revoke_hides_outward_reads_but_owner_can_cancel(self):
        job = self.save()[1]['job']
        run = self.request('/api/v1/jobs/' + job['id'] + '/runs',
            dict(context=self.c, expected_revision=1, arguments={}), key='run')[1]['run']
        import control_store
        with control_store.connect() as db:
            db.execute('DELETE FROM grants WHERE principal_id=? AND database_id=?', ('alice', self.c['database_id']))
        self.assertEqual(self.request()[0], 403)
        show = '/api/v1/jobs/runs/' + run['id']
        self.assertEqual(self.request(show)[0], 403)
        self.assertEqual(self.request(show + '/cancel', dict(context=self.c))[0], 200)
        body = dict(context=self.c, expected_revision=0, definition=self.f.definition())
        self.assertEqual(self.request(body=body, key='save')[0], 403)

    def test_legacy_job_routes_remain_gated_in_database_mode(self):
        self.assertEqual(self.request('/api/jobs', context=False)[0], 404)
        self.assertEqual(self.request('/api/jobs/run', dict(name='old'))[0], 404)

    def test_malformed_actions_job_ids_and_upload_fields_are_400_before_admission(self):
        import control_store
        for index, action in enumerate(([], {})):
            definition = self.f.definition(steps=[dict(name='bad', action=action)])
            status, result = self.request(body=dict(context=self.c, expected_revision=0, definition=definition), key='action-' + str(index))
            self.assertEqual((status, result['code']), (400, 'INVALID_INPUT'))
        for job_id in ([], {}, 17):
            status, result = self.request(body=dict(context=self.c, expected_revision=1,
                job_id=job_id, definition=self.f.definition()), key='job-id')
            self.assertEqual((status, result['code']), (400, 'INVALID_INPUT'))
        job = self.save()[1]['job']
        path = '/api/v1/jobs/' + job['id'] + '/runs'
        for index, upload in enumerate((
                dict(filename=17, content_base64=''), dict(filename=[], content_base64=''),
                dict(filename='data.csv', content_base64=17), dict(filename='data.csv', content_base64={}),
                dict(filename='data.csv', content_base64='not valid base64!'),
                dict(filename='data.csv', content_base64='非ASCII'), None)):
            status, result = self.request(path, dict(context=self.c, expected_revision=1, arguments={},
                inputs={'upload': upload}), key='input-' + str(index))
            self.assertEqual((status, result['code']), (400, 'JOB_INPUT_REJECTED'))
            self.assertEqual(result['details'], {'admission': 'NOT_ATTEMPTED'})
        self.assertEqual(self.f.f.calls, [])
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM warehouse_admissions').fetchone()[0], 0)
