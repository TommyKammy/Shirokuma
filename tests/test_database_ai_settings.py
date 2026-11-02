"""Shared Database-mode settings: no remote requests or Database grants."""
from dataclasses import replace
from http.client import HTTPResponse
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'tools/local_experiment'))
import db_ai_settings as settings
import bears_resident as bears
import request_auth as auth
import runtime_profile
import wap_resident as wap
import workbench_server as wb
from warehouse_operations import OperationError
import test_database_platform_resources as resources_tests

CONFIG = dict(provider='local-openai', model='test-model', api_url='http://127.0.0.1:8001/v1', timeout=30)


class AISettingsTests(unittest.TestCase):
    handlers = resources_tests.ResourcesTests.handlers

    def patch(self, item):
        result = item.start()
        self.addCleanup(item.stop)
        return result

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.patch(patch.dict(os.environ, {'SHIROKUMA_STATE_DIR': str(self.root)}, clear=True))
        self.users = {'p': dict(role='admin', platform_admin=True, enabled=True),
                      'dba': dict(role='admin', platform_admin=False, enabled=True)}
        self.patch(patch.object(auth, '_principal_accounts', side_effect=lambda: self.users))
        self.principal = auth.AuthenticatedPrincipal('p', 'admin', True)
        self.complete = self.patch(patch.object(bears, '_ai_completion', return_value={
            'choices': [{'message': {'content': 'pong secret'}}], 'usage': {'secret': 1}}))
        for target in ('request_auth.authorize_database', 'query_context.resolve_context',
                       'control_store.connect', 'urllib.request.urlopen', 'socket.create_connection'):
            self.patch(patch(target, side_effect=AssertionError(target)))

    def perform(self, action='get', changes=None, principal=None, role='admin'):
        return settings.perform(principal or self.principal, role, action, changes)

    def stored(self, **extra):
        wap.persist_json(wap.state_path('ai_config.json'), dict(CONFIG, **extra))

    def test_active_root_known_dto_and_no_grants_or_implicit_test(self):
        self.stored(api_key='secret', arbitrary='secret', api_key_env='SECRET')
        self.patch(patch.object(bears, 'AI_CONFIG_PATH', self.root / 'stale.json'))
        self.patch(patch.object(bears, '_ai_config', dict(CONFIG, model='stale')))
        got = self.perform()
        self.assertEqual(got, dict(CONFIG, api_key='********'))
        self.assertNotIn('secret', json.dumps(got))
        self.perform('save', dict(CONFIG, model='changed'))
        saved = json.loads(wap.state_path('ai_config.json').read_text())
        self.assertEqual(saved['model'], 'changed')
        self.assertNotIn('arbitrary', saved)
        self.assertFalse((self.root / 'stale.json').exists())
        self.complete.assert_not_called()

    def test_all_authority_requirements_before_read_and_current_account_loss(self):
        for principal, role in [(replace(self.principal, platform_admin=False), 'admin'),
                (replace(self.principal, platform_admin=1), 'admin'),
                (replace(self.principal, maximum_role='data_engineer'), 'admin'),
                (self.principal, 'data_engineer'), (self.principal, 'bi_viewer'),
                (auth.AuthenticatedPrincipal('dba', 'admin', False), 'admin')]:
            for action in ('get', 'save', 'test'):
                with patch.object(bears, 'load_ai_config') as load:
                    with self.assertRaises(OperationError) as error:
                        self.perform(action, CONFIG, principal, role)
                    self.assertEqual(error.exception.status, 403)
                    load.assert_not_called()
        for change in ({'platform_admin': False}, {'platform_admin': 1}, {'enabled': False},
                       {'disabled': True}, {'role': 'data_engineer'}):
            self.users['p'] = dict(role='admin', platform_admin=True, enabled=True) | change
            with self.assertRaises(OperationError): self.perform()
        self.users.clear()
        with self.assertRaises(OperationError): self.perform()
        self.complete.assert_not_called()

    def test_save_preserves_backend_inference_settings_without_exposing_them(self):
        for tokens, temperature in ((73, 0.65), (2048, 0), (4096, None)):
            with self.subTest(tokens=tokens, temperature=temperature):
                self.stored(max_tokens=tokens, temperature=temperature, arbitrary='discard')
                for change in ({'model': 'changed'}, {'provider': 'custom'},
                               {'api_url': 'https://example.invalid/v1'}):
                    result = self.perform('save', CONFIG | change)
                    saved = json.loads(wap.state_path('ai_config.json').read_text())
                    self.assertEqual(saved['max_tokens'], tokens)
                    self.assertEqual(saved['temperature'], temperature)
                    self.assertNotIn('arbitrary', saved)
                    self.assertEqual(set(result), settings.FIELDS)
                    self.assertEqual(set(self.perform()), settings.FIELDS)
                for key in ('max_tokens', 'temperature'):
                    with self.assertRaises(OperationError):
                        self.perform('save', CONFIG | {key: 1})
        self.complete.assert_not_called()

    def test_preserve_masked_omitted_direct_env_and_reset_endpoint_provider(self):
        self.patch(patch.dict(os.environ, {'TEST_KEY': 'env-secret'}))
        for key in ('direct-secret', ''):
            for supplied in ({}, {'api_key': '********'}):
                self.stored(api_key=key, api_key_env='TEST_KEY')
                self.perform('save', CONFIG | supplied)
                active = bears.load_ai_config(path=wap.state_path('ai_config.json'))
                self.assertEqual(bears.resolve_api_key(active), key or 'env-secret')
                for changed in ({'provider': 'custom'}, {'api_url': 'https://example.invalid/v1'}):
                    self.stored(api_key=key, api_key_env='TEST_KEY')
                    self.perform('save', CONFIG | changed | supplied)
                    active = bears.load_ai_config(path=wap.state_path('ai_config.json'))
                    self.assertIsNone(bears.resolve_api_key(active))
                    self.assertEqual(active['api_key_env'], '')
                    self.perform('save', CONFIG | changed | {'api_key': 'new-secret'})
                    self.assertEqual(bears.load_ai_config(path=wap.state_path('ai_config.json'))['api_key'], 'new-secret')

    def test_strict_fields_scalar_types_and_url(self):
        bad = [CONFIG | {k: v} for k, values in {
            'provider': [None, True, [], {}, 'other'], 'model': [None, 1, [], '', 'x\n', 'x' * 513],
            'api_key': [None, True, 4, {}], 'timeout': [True, 0, 601, 1.5, '30'],
            'api_url': ['', None, 'ftp://host/x', 'http://u:p@host', 'http://host?',
                        'http://host#', 'http://host:bad', 'http://host:', 'http://host/a b',
                        'http://host:0/v1', 'http://[::1]:0/v1', 'http://[::1]:/v1',
                        'http://host:65536/v1', 'http://host:1.5/v1', 'http://host\\evil/v1',
                        'http://host/v1\\x', 'http://host/v1\x7f', 'http://host/v1\x85',
                        'http://host/v1\t', 'http://host/v1\n', 'http://host/v1\u2003',
                        'http://host/' + 'x' * 4096]
        }.items() for v in values]
        bad += [CONFIG | {'context': {}}, CONFIG | {'api_key_env': 'SECRET'}, {},
                {k: v for k, v in CONFIG.items() if k != 'model'}]
        for body in bad:
            for action in ('save', 'test'):
                with self.subTest(body=body, action=action):
                    with self.assertRaises(OperationError) as error: self.perform(action, body)
                    self.assertEqual(error.exception.status, 400)
        self.complete.assert_not_called()
        self.assertFalse(wap.state_path('ai_config.json').exists())
        for provider in settings.PROVIDERS:
            self.perform('save', CONFIG | {'provider': provider, 'timeout': 600})
        self.perform('save', CONFIG | {'model': 'x' * 512})

    def test_test_synthetic_no_save_no_leak_and_sanitized_failure(self):
        self.stored(api_key='secret')
        before = wap.state_path('ai_config.json').read_bytes()
        result = self.perform('test', CONFIG)
        self.assertEqual(set(result), {'status', 'latency_ms'})
        self.assertEqual(result['status'], 'connected')
        config, body = self.complete.call_args.args
        self.assertEqual(body['messages'], [dict(role='user', content='Reply with pong.')])
        self.assertIs(body['enable_thinking'], False)
        self.assertEqual(body['max_tokens'], 16)
        self.assertEqual(config['api_key'], 'secret')
        self.assertEqual(before, wap.state_path('ai_config.json').read_bytes())
        for provider in settings.PROVIDERS - {'local-openai'}:
            self.perform('test', CONFIG | {'provider': provider})
            body = self.complete.call_args.args[1]
            self.assertNotIn('enable_thinking', body)
            self.assertEqual(body['max_tokens'], 16)
        for response in ({}, {'choices': []}, {'choices': [{'message': {'content': ''}}]}):
            self.complete.return_value = response
            self.assertEqual(self.perform('test', CONFIG)['error'], 'AI_TEST_FAILED')
        self.complete.side_effect = RuntimeError('secret provider response')
        self.assertNotIn('secret', json.dumps(self.perform('test', CONFIG)))
        with patch.object(wap, 'persist_json', side_effect=OSError('secret path')):
            with self.assertRaises(OperationError) as error: self.perform('save', CONFIG)
            self.assertEqual(error.exception.code, 'AI_CONFIG_UNAVAILABLE')

    def test_loss_before_mutation_dispatch_and_after_results(self):
        original = settings._authority
        for action in ('get', 'save', 'test'):
            for lose_on in range(2, 3 if action == 'get' else 4):
                calls = 0
                def authority(*args):
                    nonlocal calls
                    calls += 1
                    if calls == lose_on: self.users['p']['platform_admin'] = False
                    return original(*args)
                self.users['p']['platform_admin'] = True
                self.complete.reset_mock()
                with patch.object(settings, '_authority', authority), patch.object(wap, 'persist_json') as save:
                    with self.assertRaises(OperationError) as error: self.perform(action, CONFIG)
                    self.assertEqual(error.exception.status, 403)
                    if lose_on == 2:
                        save.assert_not_called(); self.complete.assert_not_called()

    def request(self, handler, *, method='GET', endpoint='ai-config', suffix='', headers='', body='', role='admin', principal=None):
        class Connection:
            def __init__(self, raw): self.incoming, self.outgoing = io.BytesIO(raw), io.BytesIO()
            def makefile(self, *args): return self.incoming
            def sendall(self, raw): self.outgoing.write(raw)
        raw = (method + ' /api/v1/bears/' + endpoint + suffix + ' HTTP/1.1\r\n'
               'Host: 127.0.0.1\r\nConnection: close\r\nX-Shirokuma-Role: ' + role + '\r\n'
               + headers + '\r\n' + body).encode()
        conn = Connection(raw)
        with patch.object(runtime_profile, 'active', return_value=True), \
                patch.object(auth, 'authenticate_principal', return_value=principal or self.principal), \
                patch.object(handler, 'log_message'):
            handler(conn, ('127.0.0.1', 1), SimpleNamespace())
        response = HTTPResponse(Connection(conn.outgoing.getvalue()))
        response.begin()
        return response.status, json.loads(response.read())

    def test_both_http_surfaces_get_save_explicit_test_and_strict_framing(self):
        body = json.dumps(CONFIG)
        post = dict(method='POST', headers=f'Content-Type: application/json\r\nContent-Length: {len(body)}\r\n', body=body)
        for handler in self.handlers():
            self.complete.reset_mock()
            self.assertEqual(self.request(handler)[0], 200)
            self.assertEqual(self.request(handler, **post)[0], 200)
            self.complete.assert_not_called()
            self.assertEqual(self.request(handler, endpoint='ai-test', **post)[0], 200)
            self.complete.assert_called_once()
            cases = [({'suffix': '?'}, 400), ({'suffix': '#x'}, 400),
                     ({'suffix': '?x=1&x=2'}, 400), ({'headers': 'Transfer-Encoding: \r\n'}, 400),
                     ({'headers': 'Content-Length: 1\r\n', 'body': 'x'}, 400),
                     ({'headers': 'Content-Length: 0\r\nContent-Length: 0\r\n'}, 400),
                     ({'headers': 'Host: duplicate\r\n'}, 400),
                     ({'role': 'data_engineer'}, 403),
                     ({'principal': auth.AuthenticatedPrincipal('dba', 'admin', False)}, 403),
                     ({'endpoint': 'ai-test'}, 404),
                     (post | {'headers': post['headers'] + 'Content-Type: application/json\r\n'}, 400)]
            duplicate = '{"model":"a","model":"b"}'
            cases.append((dict(method='POST', body=duplicate, headers=f'Content-Type: application/json\r\nContent-Length: {len(duplicate)}\r\n'), 400))
            for options, expected in cases:
                with self.subTest(handler=handler.__name__, options=options):
                    status, result = self.request(handler, **options)
                    self.assertEqual(status, expected, result)
                    self.assertNotIn('secret', json.dumps(result))


if __name__ == '__main__': unittest.main()
