"""HTTP boundary tests without a server, control store, or backend execution."""
import base64
from contextlib import ExitStack
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import jobs_client as jobs


C = dict(database_id='db', schema_id='sc', warehouse_id='LOCAL', sql_catalog='sales',
         sql_schema='raw', selected_role='data_engineer', binding_revision=2,
         generation_id='gen', grant_revision=7)


class JobsClient(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.context = self.root / 'context.json'
        self.context.write_text(json.dumps({'context': C}))
        self.definition = self.root / 'definition.json'
        self.definition.write_text(json.dumps(dict(name='daily', parameters={}, steps=[dict(name='q', action='sql', mode='read-only', sql='SELECT 1')])))
        self.calls = []

    def call(self, args, *, fail=False):
        test = self
        class Response(io.BytesIO):
            headers = {}
        class Opener:
            def open(self, req, **kwargs):
                test.calls.append(req)
                if fail:
                    raise HTTPError(req.full_url, 500, 'private response', {}, io.BytesIO(b'private-password'))
                path = urlsplit(req.full_url).path
                if path == '/api/v1/databases':
                    data = {'items': [dict(id='db', sql_catalog='sales', binding_revision=2, generation_id='gen', grant_revision=7)]}
                elif path.endswith('/schemas'):
                    data = {'items': [dict(id='sc', sql_name='raw')]}
                else:
                    data = {'context': C, 'job': {'id': 'j'}, 'run': {'id': 'r'}}
                return Response(json.dumps(data).encode())
        with ExitStack() as stack:
            stack.enter_context(patch('dataops_client.runtime_profile.private_json', return_value=dict(username='owner', password='private-password', selected_role='admin')))
            stack.enter_context(patch('dataops_client.build_opener', return_value=Opener()))
            return jobs.main(args, profile=SimpleNamespace(client_auth_file='auth'))

    def saved(self, action, *args):
        return [action, '--context-file', str(self.context), *args]

    def test_save_create_and_update_cas_and_role(self):
        for revision, extra in [(0, []), (3, ['--job-id', 'j/one'])]:
            self.call(self.saved('save', '--definition', str(self.definition), '--expected-revision', str(revision), '--idempotency-key', 'fixed', *extra))
            req = self.calls[-1]
            body = json.loads(req.data)
            self.assertEqual(body['context'], C)
            self.assertEqual(body['expected_revision'], revision)
            self.assertEqual(body['definition']['steps'][0]['sql'], 'SELECT 1')
            self.assertEqual(body.get('job_id'), 'j/one' if extra else None)
            self.assertEqual(req.get_header('X-shirokuma-role'), 'data_engineer')
            self.assertEqual(req.get_header('Idempotency-key'), 'fixed')
            self.assertTrue(req.get_header('Authorization').startswith('Basic '))

    def test_run_typed_args_and_all_input_bytes_before_post(self):
        upload = self.root / 'input.csv'
        upload.write_bytes(b'v\n\x00\xff')
        arguments = self.root / 'args.json'
        values = dict(number=7, enabled=True, date='2026-10-21', word='hello')
        arguments.write_text(json.dumps(values))
        args = self.saved('run', '--job-id', 'j/one ?', '--expected-revision', '2', '--idempotency-key', 'run-key', '--arguments', '@'+str(arguments), '--input', 'source='+str(upload))
        self.call(args)
        req = self.calls[-1]
        self.assertEqual(urlsplit(req.full_url).path, '/api/v1/jobs/j%2Fone%20%3F/runs')
        body = json.loads(req.data)
        self.assertEqual(body['arguments'], values)
        self.assertEqual(body['inputs'], {'source': dict(filename='input.csv', content_base64=base64.b64encode(upload.read_bytes()).decode())})
        self.assertNotIn(str(self.root), req.data.decode())
        self.calls.clear()
        with self.assertRaises(ValueError):
            self.call(args+['--input', 'missing='+str(self.root/'missing')])
        self.assertEqual(self.calls, [])

    def test_get_and_followups_preserve_epoch_and_encode(self):
        for action, options, suffix in [('list', [], '/api/v1/jobs'), ('show', ['--job-id', 'j/x'], '/api/v1/jobs/j%2Fx'), ('runs', ['--job-id', 'j&x'], '/api/v1/jobs/runs'), ('inspect', ['--run-id', 'r/x'], '/api/v1/jobs/runs/r%2Fx'), ('cancel', ['--run-id', 'r/x'], '/api/v1/jobs/runs/r%2Fx/cancel'), ('reconcile', ['--run-id', 'r/x'], '/api/v1/jobs/runs/r%2Fx/reconcile')]:
            self.call(self.saved(action, *options))
            req = self.calls[-1]
            self.assertEqual(urlsplit(req.full_url).path, suffix)
            if req.data:
                self.assertEqual(json.loads(req.data), {'context': C})
            else:
                query = parse_qs(urlsplit(req.full_url).query)
                self.assertEqual(json.loads(query['context'][0]), C)
                if action == 'runs':
                    self.assertEqual(query['job_id'], ['j&x'])
            self.assertEqual(req.get_header('X-shirokuma-role'), C['selected_role'])

    def test_convenience_selection_and_invalid_inputs_are_local(self):
        self.call(['list', '--database', 'sales', '--schema', 'raw'])
        self.assertEqual(len(self.calls), 3)
        cases = [self.saved('list', '--database', 'wrong'), ['list'],
                 self.saved('save', '--definition', str(self.definition), '--expected-revision', '1', '--idempotency-key', 'k'),
                 self.saved('save', '--definition', str(self.definition), '--job-id', 'j', '--expected-revision', '0', '--idempotency-key', 'k'),
                 self.saved('run', '--job-id', 'j', '--expected-revision', '0', '--arguments', '{}', '--idempotency-key', 'k'),
                 self.saved('run', '--job-id', 'j', '--expected-revision', '1', '--arguments', '[]', '--idempotency-key', 'k')]
        for args in cases:
            self.calls.clear()
            with self.subTest(action=args[0]), self.assertRaises(ValueError):
                self.call(args)
            self.assertEqual(self.calls, [])

    def test_missing_or_unrelated_flags_and_safe_http_error(self):
        for args in [self.saved('show'), self.saved('cancel'), self.saved('list', '--input', 'n=p'), self.saved('run', '--job-id', 'j')]:
            with self.assertRaises(SystemExit), patch('sys.stderr', new=io.StringIO()):
                self.call(args)
        with self.assertRaises(jobs.ClientError) as error:
            self.call(self.saved('list'), fail=True)
        self.assertEqual(str(error.exception), 'HTTP_REQUEST_FAILED')

    def test_redirect_refused_by_shared_client(self):
        from dataops_client import NoRedirect
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'http://example.com'))

    def test_equals_flags_preserve_hyphen_key_and_identifier(self):
        self.call(['run', '--context-file='+str(self.context), '--job-id=-job',
                   '--expected-revision=1', '--idempotency-key=-foo', '--arguments={}'])
        req = self.calls[-1]
        self.assertEqual(urlsplit(req.full_url).path, '/api/v1/jobs/-job/runs')
        self.assertEqual(req.get_header('Idempotency-key'), '-foo')
        self.assertEqual(json.loads(req.data)['context'], C)


if __name__ == '__main__':
    unittest.main()
