"""Offline managed Mart HTTP contract tests."""
from contextlib import ExitStack
import copy
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
import marts_client as marts

C = dict(database_id='db', schema_id='sc', warehouse_id='LOCAL', sql_catalog='sales', sql_schema='marts',
         selected_role='admin', binding_revision=2, generation_id='gen', grant_revision=7)
REF = dict(database_id='db', schema_id='raw', object_id='input', incarnation=1)
TARGET = dict(database_id='db', schema_id='sc', physical_name='daily')


class MartsClient(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.context = self.root / 'context.json'
        self.context.write_text(json.dumps(C))
        self.definition = self.root / 'definition.json'
        self.d = dict(name='daily', model_id='explicit', version=3, source_refs=[REF], target=TARGET)
        self.definition.write_text(json.dumps(self.d))
        self.calls = []
        self.change = lambda reply: reply
        self.failure = False
        self.current_target = None
        self.model_target = None

    def call(self, action, *flags):
        test = self
        class Response(io.BytesIO):
            headers = {}
        class Opener:
            def open(self, req, **kwargs):
                test.calls.append(req)
                url = urlsplit(req.full_url)
                body = json.loads(req.data) if req.data else None
                if url.path == '/api/session':
                    reply = dict(username='owner', authenticated=True)
                elif url.path == '/api/v1/databases':
                    reply = dict(items=[dict(id='db', sql_catalog='sales', binding_revision=2, generation_id='gen', grant_revision=7)])
                elif url.path.endswith('/schemas'):
                    reply = dict(items=[dict(id='sc', sql_name='marts')])
                elif url.path == '/api/v1/wap/models':
                    reply = dict(context=C, model=dict(database_id='db', model_id='explicit', version=3, sources={'a': REF, 'b': REF}, target=TARGET, current_target_ref=test.model_target))
                else:
                    if test.failure:
                        raise HTTPError(req.full_url, 403, '', {}, io.BytesIO(b'{"secret":"withheld"}'))
                    mart = dict(id='m', database_id='db', owner='owner', context=C, revision=(body or {}).get('expected_revision', 0)+1,
                                definition=copy.deepcopy(test.d), current_target_ref=test.current_target)
                    run = dict(id='r', mart_id='m', mart_revision=(body or {}).get('expected_revision', 2), owner='owner',
                               context=C, kind='check' if url.path.endswith('/checks') else 'refresh', status='running', committed=False)
                    reply = test.change(dict(context=C, mart=mart, items=[run] if url.path.endswith('/runs') else [mart], run=run))
                return Response(json.dumps(reply).encode())
        with ExitStack() as stack:
            stack.enter_context(patch('dataops_client.runtime_profile.private_json', return_value=dict(username='owner', password='withheld', selected_role='admin')))
            stack.enter_context(patch('dataops_client.build_opener', return_value=Opener()))
            args = [action, '--context-file='+str(self.context), *flags]
            return marts.main(args, profile=SimpleNamespace(client_auth_file='unused'))

    def save(self, *flags):
        return self.call('save', '--definition='+str(self.definition), '--expected-revision=0', '--idempotency-key=k', *flags)

    def test_save_full9_and_header_model_version(self):
        self.save()
        req = self.calls[-1]
        self.assertEqual(urlsplit(req.full_url).path, '/api/v1/marts')
        self.assertEqual(json.loads(req.data), dict(context=C, expected_revision=0, definition=self.d))
        self.assertEqual(req.get_header('X-idempotency-key'), 'k')
        self.assertIsNone(req.get_header('Idempotency-key'))
        query = parse_qs(urlsplit(self.calls[-2].full_url).query)
        self.assertEqual(query['model_id'], ['explicit'])
        self.assertEqual(query['version'], ['3'])

    def test_all_read_and_execution_paths(self):
        for action, suffix, flags in [('list', '', []), ('get', '/m', ['--mart-id=m']), ('runs', '/m/runs', ['--mart-id=m']),
                                      ('inspect', '/m/runs/r', ['--mart-id=m', '--run-id=r'])]:
            self.call(action, *flags)
            req = self.calls[-1]
            self.assertEqual(urlsplit(req.full_url).path, '/api/v1/marts'+suffix)
            self.assertEqual(json.loads(parse_qs(urlsplit(req.full_url).query)['context'][0]), C)
        for action in ('check', 'refresh'):
            self.call(action, '--mart-id=m', '--expected-revision=2', '--idempotency-key=frozen')
            body = json.loads(self.calls[-1].data)
            self.assertEqual(body, dict(context=C, expected_revision=2, **({'check_changes': False} if action == 'refresh' else {})))
        self.call('refresh', '--mart-id=m', '--expected-revision=2', '--idempotency-key=frozen', '--check-changes')
        self.assertTrue(json.loads(self.calls[-1].data)['check_changes'])

    def test_update_requires_explicit_current_target(self):
        self.model_target = dict(database_id='db', schema_id='sc', object_id='model-current', incarnation=1)
        self.current_target = dict(database_id='db', schema_id='sc', object_id='published', incarnation=2)
        flags = ['--mart-id=m', '--definition='+str(self.definition), '--expected-revision=2', '--idempotency-key=k']
        with self.assertRaises(ValueError):
            self.call('save', *flags)
        self.d['target'] = self.current_target
        self.definition.write_text(json.dumps(self.d))
        self.call('save', *flags)
        self.assertEqual(urlsplit(self.calls[-1].full_url).path, '/api/v1/marts/m')
        self.assertEqual(json.loads(self.calls[-1].data)['definition']['target'], self.current_target)

    def test_new_mart_requires_published_model_ref_without_rewriting_definition(self):
        self.model_target = dict(database_id='db', schema_id='sc', object_id='published', incarnation=2)
        for target in (TARGET, {**self.model_target, 'object_id': 'foreign'},
                       {**self.model_target, 'database_id': 'other'}):
            with self.subTest(target=target):
                self.definition.write_text(json.dumps({**self.d, 'target': target}))
                self.calls.clear()
                with self.assertRaises(ValueError):
                    self.save()
                self.assertFalse(any(req.method == 'POST' for req in self.calls))
        self.d['target'] = self.model_target
        self.definition.write_text(json.dumps(self.d))
        self.save()
        self.assertEqual(json.loads(self.calls[-1].data)['definition'], self.d)
        query = parse_qs(urlsplit(self.calls[-2].full_url).query)
        self.assertEqual(query['model_id'], ['explicit'])
        self.assertEqual(query['version'], ['3'])
        self.assertEqual(json.loads(self.definition.read_text()), self.d)
        self.change = lambda reply: {**reply, 'mart': {**reply['mart'],
            'definition': {**reply['mart']['definition'], 'target': TARGET}}}
        self.call('save', '--mart-id=m', '--definition='+str(self.definition),
            '--expected-revision=2', '--idempotency-key=update-published')
        self.assertEqual(json.loads(self.calls[-1].data)['definition']['target'], self.model_target)

    def test_foreign_context_owner_id_run_rejected(self):
        for key, value in [('context', {**C, 'grant_revision': 99}), ('mart', {'id': 'foreign'}), ('mart', {'owner': 'platform-admin'}), ('mart', {'database_id': 'finance'})]:
            def change(reply, key=key, value=value):
                reply[key] = {**reply[key], **value}
                return reply
            self.change = change
            with self.assertRaises(ValueError):
                self.call('get', '--mart-id=m')
        for field, value in [('mart_id', 'foreign'), ('owner', 'foreign'), ('id', 'other'), ('context', {**C, 'schema_id': 'raw'})]:
            self.change = lambda reply, f=field, v=value: {**reply, 'run': {**reply['run'], f: v}}
            with self.assertRaises(ValueError):
                self.call('inspect', '--mart-id=m', '--run-id=r')

    def test_strict_definition_and_manual_only(self):
        for change in ({'sql': 'forbidden'}, {'source_refs': [REF, REF]}, {'version': True}, {'source_refs': [{**REF, 'database_id': 'other'}]}):
            self.definition.write_text(json.dumps({**self.d, **change}))
            with self.assertRaises(ValueError):
                self.save()
        self.d['source_refs'] = []
        self.definition.write_text(json.dumps(self.d))
        self.save()

    def test_http_failure_has_no_reply(self):
        self.failure = True
        with self.assertRaises(marts.ClientError) as error:
            self.call('refresh', '--mart-id=m', '--expected-revision=2', '--idempotency-key=k')
        self.assertIsNone(error.exception.reply)

    def test_strict_flags(self):
        for args in [('list', '--check-changes'), ('refresh', '--mart-id=m', '--expected-revision=0', '--idempotency-key=k'),
                     ('list', 'unexpected'), ('get', '--mart-id='), ('list', '--database=db')]:
            with self.subTest(args=args), self.assertRaises((ValueError, SystemExit)):
                self.call(*args)
