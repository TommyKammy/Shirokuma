"""Fixed catalog transport/owner with fake HTTP and Docker; no service calls."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

from tools.local_experiment.catalog_read import run as R
from test_catalog_read_contract import CANARY, documents, public, request

TOKEN = 'a' * 32


class FakeResponse:
    status = 200

    def __init__(self, value, *, fault=None, close_error=None, suppress=False):
        self.raw = io.BytesIO(json.dumps(value).encode())
        self.fault, self.close_error, self.suppress = fault, close_error, suppress
        self.closed = False

    def __enter__(self):
        return self

    def read(self, size):
        if self.fault is not None:
            raise self.fault
        return self.raw.read(size)

    def __exit__(self, *_):
        self.closed = True
        if self.close_error is not None:
            raise self.close_error
        return self.suppress


class FakeHTTP:
    def __init__(self, *, second=None, fail_at=None, fault=None, close_error=None, suppress=False):
        self.windows = [documents(), second or documents()]
        self.calls, self.responses, self.counts = [], [], {}
        self.fail_at, self.fault, self.close_error, self.suppress = fail_at, fault, close_error, suppress

    def open(self, req, timeout):
        self.calls.append((req.method, req.full_url, timeout))
        path = req.full_url[len(R.V.URL):]
        if req.method == 'POST':
            assert path == '/users/login'
            value = {'accessToken': 'SECRET_TOKEN_CANARY'}
        else:
            key = next((key for key, value in R.PATHS.items() if value == path), 'lineage')
            index = self.counts.get(key, 0)
            self.counts[key] = index + 1
            value = self.windows[index][key]
        active = len(self.calls) == self.fail_at
        response = FakeResponse(value, fault=self.fault if active else None,
                                close_error=self.close_error if active else None, suppress=self.suppress)
        self.responses.append(response)
        return response


class CatalogReadRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = self.root / 'metadata'
        self.env.mkdir(mode=0o700)
        values = {'OM_STATE': str(self.env), 'OM_DB_PASSWORD': 'db-secret', 'OM_POSTGRES_PASSWORD': 'pg-secret',
                  'OM_ADMIN_PASSWORD': 'a-private-test-password', 'OM_FERNET_KEY': 'test-fernet'}
        (self.env / '.env').write_text(''.join(key + '=' + value + '\n' for key, value in values.items()))
        (self.env / '.env').chmod(0o600)
        self.http, self.clients = FakeHTTP(), []
        self.runtime = mock.Mock(return_value={'container_id': '1' * 64})
        self.state = mock.Mock(return_value={'state_hash': '2' * 64})
        self.source = mock.Mock(return_value={'source.py': '3' * 64})

    def client(self, progress):
        value = R.Client(opener=self.http, state=self.env, progress=progress)
        self.clients.append(value)
        return value

    def invoke(self, **extra):
        events = []
        result = R.invoke(TOKEN, request(), events.append, state=self.root / 'runs',
                          client_factory=self.client, runtime_guard=self.runtime,
                          state_guard=self.state, source_reader=self.source, **extra)
        return result, events

    def summary(self):
        return json.loads((self.root / 'runs' / TOKEN / 'summary.json').read_bytes())

    def test_one_login_twelve_fixed_gets_and_safe_saved_projection(self):
        final, events = self.invoke()
        self.assertEqual([x['event'] for x in events], ['allocated', 'result'])
        self.assertEqual(final['observation'], public())
        self.assertEqual(final['result'], 'passed')
        self.assertTrue(final['cleanup_verified'])
        self.assertEqual([x[0] for x in self.http.calls], ['POST'] + ['GET'] * 12)
        self.assertEqual(set(self.http.counts.values()), {2})
        self.assertTrue(all(0 < x[2] <= 10 for x in self.http.calls))
        self.assertTrue(all(x.closed for x in self.http.responses))
        self.assertIsNone(self.clients[0].token)
        saved = self.summary()
        self.assertEqual(saved['case'], 'catalog-description-v1')
        self.assertEqual(saved['observation'], final['observation'])
        self.assertEqual(len(saved['snapshots']), 2)
        self.assertEqual(saved['snapshots'][0], saved['snapshots'][1])
        self.assertEqual(saved['reads'][0]['response_sha256'], None)
        for canary in (CANARY, 'SECRET_TOKEN_CANARY', 'a-private-test-password', 'sqlQuery', 'connection'):
            self.assertNotIn(canary, json.dumps(saved))
        self.assertEqual(self.runtime.call_count, 2)
        self.assertEqual((self.root / 'runs' / TOKEN / 'summary.json').stat().st_mode & 0o777, 0o600)

    def test_actual_client_refuses_unbound_paths_and_repeated_calls_before_open(self):
        client = self.client(lambda: None)
        for method, path in [('GET', '/tables'), ('POST', R.PATHS['table']),
                             ('GET', '/lineage/table/' + '1' * 36 + '?upstreamDepth=1&downstreamDepth=1')]:
            with self.subTest(path=path), self.assertRaises(R.ReadError):
                client.request(method, path)
        self.assertEqual(self.http.calls, [])
        self.invoke()
        count = len(self.http.calls)
        with self.assertRaises(R.ReadError):
            self.clients[-1]._request('GET', R.PATHS['table'])
        self.assertEqual(len(self.http.calls), count)

    def test_snapshot_drift_is_refused_even_when_only_unpublished_raw_field_changed(self):
        changed = documents(); changed['service']['href'] = 'changed-private-href'
        self.http = FakeHTTP(second=changed)
        final, _ = self.invoke()
        self.assertEqual(final['error_code'], 'CATALOG_CHANGED')
        self.assertIsNone(final['observation'])
        self.assertTrue(final['cleanup_verified'])

    def test_runtime_state_and_source_drift_discard_public_success(self):
        for name, expected in [('runtime', 'CATALOG_RUNTIME_CHANGED'), ('state', 'CATALOG_STATE_CHANGED'),
                               ('source', 'CATALOG_SOURCE_CHANGED')]:
            with self.subTest(name=name):
                folder = self.root / name
                dependency = mock.Mock(side_effect=[{'identity': 'old'}, {'identity': 'changed'}])
                kwargs = dict(runtime_guard=mock.Mock(return_value={}), state_guard=mock.Mock(return_value={}),
                              source_reader=mock.Mock(return_value={}))
                kwargs[{'runtime': 'runtime_guard', 'state': 'state_guard', 'source': 'source_reader'}[name]] = dependency
                self.http = FakeHTTP()
                final = R.invoke(TOKEN, request(), lambda _: None, state=folder, client_factory=self.client, **kwargs)
                self.assertEqual(final['error_code'], expected)
                self.assertFalse(final['cleanup_verified'])
                self.assertIsNone(final['observation'])

    def test_failed_http_stops_without_remaining_gets_but_confirms_closed_resources(self):
        self.http = FakeHTTP(fail_at=3, fault=OSError(CANARY))
        final, _ = self.invoke()
        self.assertEqual(final['result'], 'failed')
        self.assertIsNone(final['observation'])
        self.assertTrue(final['cleanup_verified'])
        self.assertEqual(len(self.http.calls), 3)
        self.assertEqual(self.runtime.call_count, 2)
        self.assertTrue(self.http.responses[-1].closed)
        self.assertNotIn(CANARY, json.dumps(self.summary()))

    def test_close_failure_never_claims_cleanup_even_after_runtime_guard(self):
        self.http = FakeHTTP(fail_at=3, fault=ValueError('bad response'), close_error=OSError('close'))
        final, _ = self.invoke()
        self.assertFalse(final['cleanup_verified'])
        self.assertIsNone(final['observation'])
        self.assertEqual(len(self.http.calls), 3)
        self.assertTrue(self.clients[0].opener.close_failed)

    def test_http_error_response_close_is_tracked_without_double_close(self):
        for broken in (False, True):
            with self.subTest(broken=broken):
                error = urllib.error.HTTPError(R.V.URL + '/users/login', 401, 'private', {}, None)
                error.close = mock.Mock(side_effect=OSError('close') if broken else None)
                opener = R.Opener(mock.Mock(open=mock.Mock(side_effect=error)))
                with self.assertRaises(R.ReadError):
                    opener.open(object(), timeout=1)
                self.assertEqual(error.close.call_count, 1)
                self.assertEqual(opener.close_failed, broken)
                self.assertEqual(opener.active, 1 if broken else 0)

    def test_login_deadline_survives_legacy_wrapper_and_starts_no_after_process(self):
        self.http = FakeHTTP(fail_at=1, fault=R.Deadline())
        final, _ = self.invoke()
        self.assertEqual(final['result'], 'failed')
        self.assertEqual(final['error_code'], 'CATALOG_DEADLINE')
        self.assertFalse(final['cleanup_verified'])
        self.assertEqual(len(self.http.calls), 1)
        self.assertEqual(self.runtime.call_count, 1)
        self.assertTrue(self.http.responses[0].closed)

    def test_first_cancel_survives_response_close_and_receipt_failure_no_after_gets(self):
        original = KeyboardInterrupt()
        self.http = FakeHTTP(fail_at=3, fault=original, close_error=OSError('close'))
        def writer(path, value):
            if value['result'] == 'interrupted':
                raise OSError('secondary persistence')
            R.save(path, value)
        with self.assertRaises(KeyboardInterrupt) as caught:
            self.invoke(writer=writer)
        self.assertIs(caught.exception, original)
        self.assertEqual(len(self.http.calls), 3)
        self.assertEqual(self.runtime.call_count, 1)

    def test_response_context_cannot_suppress_cancellation(self):
        original = KeyboardInterrupt()
        self.http = FakeHTTP(fail_at=1, fault=original, suppress=True)
        with self.assertRaises(KeyboardInterrupt) as caught:
            self.invoke()
        self.assertIs(caught.exception, original)
        self.assertEqual(self.runtime.call_count, 1)

    def test_intent_is_durable_before_http_and_final_save_failure_releases_no_public(self):
        def fail_initial(_path, _value):
            raise OSError('storage unavailable')
        final, _ = self.invoke(writer=fail_initial)
        self.assertEqual(final['result'], 'failed')
        self.assertEqual(self.http.calls, [])
        self.assertEqual(self.runtime.call_count, 0)

    def test_failed_login_or_get_is_not_retried_and_pending_intent_blocks_http(self):
        def writer(path, value):
            if len(value['reads']) == 2:
                raise OSError('GET intent storage unavailable')
            R.save(path, value)
        final, _ = self.invoke(writer=writer)
        self.assertEqual(final['result'], 'failed')
        self.assertEqual(len(self.http.calls), 1)
        self.assertIsNone(final['observation'])
        with self.assertRaises(R.ReadError):
            self.clients[-1]._request('GET', R.PATHS['team'])
        self.assertEqual(len(self.http.calls), 1)

    def test_failed_terminal_receipt_write_releases_no_public_success(self):
        def writer(path, value):
            if value['result'] == 'passed':
                raise OSError('final receipt unavailable')
            R.save(path, value)
        final, _ = self.invoke(writer=writer)
        self.assertEqual(final['result'], 'failed')
        self.assertIsNone(final['observation'])
        self.assertTrue(final['cleanup_verified'])
        self.assertEqual(len(self.http.calls), 13)

    def test_existing_token_is_never_reused(self):
        self.invoke()
        before = (self.root / 'runs' / TOKEN / 'summary.json').read_bytes()
        with self.assertRaisesRegex(R.ReadError, 'EVIDENCE_ALREADY_EXISTS'):
            self.invoke()
        self.assertEqual(len(self.http.calls), 13)
        self.assertEqual(before, (self.root / 'runs' / TOKEN / 'summary.json').read_bytes())

    def test_runtime_fixed_server_binding_and_wrong_port_refusal(self):
        image = json.loads(R.M.COMPOSE.read_bytes())['services']['server']['image']
        value = {'id': '1' * 64, 'image': image, 'image_id': 'sha256:' + '2' * 64,
                 'labels': {'com.docker.compose.project': R.M.PROJECT, 'com.docker.compose.service': 'server'},
                 'ports': {'8585/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '18585'}], '8586/tcp': None},
                 'state': 'running', 'running': True}
        for wrong in (False, True):
            with self.subTest(wrong=wrong):
                body = deepcopy(value)
                if wrong:
                    body['ports']['8585/tcp'][0]['HostIp'] = '0.0.0.0'
                docker = mock.Mock(side_effect=[b'1' * 64, json.dumps(body).encode()])
                check = mock.Mock()
                if wrong:
                    with self.assertRaises(R.ReadError):
                        R.inspect_runtime(check=check, docker=docker)
                else:
                    self.assertEqual(R.inspect_runtime(check=check, docker=docker)['container_id'], '1' * 64)
                self.assertEqual(check.call_count, 1)
                self.assertEqual([x.args[0] for x in docker.call_args_list], ['ps', 'inspect'])

    def test_state_signature_accepts_pinned_readonly_keys_and_rejects_symlinks(self):
        for name in ('admin-initialized', 'private_key.der', 'public_key.der', 'db-init.sql'):
            file = self.env / name
            file.write_bytes(b'' if name == 'admin-initialized' else b'private-fixture')
            file.chmod(0o600 if name == 'admin-initialized' else 0o444)
        before = R.state_identity(self.env)
        self.assertEqual(R.state_identity(self.env), before)
        self.assertEqual(before['files']['private_key.der']['mode'], 0o444)
        key = self.env / 'private_key.der'; key.unlink(); key.symlink_to(self.env / 'public_key.der')
        with self.assertRaises(R.ReadError):
            R.state_identity(self.env)

    def test_alarm_reaps_only_owned_inspection_subprocess(self):
        # A harmless Python sleeper substitutes for Docker; no services or network.
        children = []
        popen = subprocess.Popen
        def start(*args, **kwargs):
            child = popen(*args, **kwargs); children.append(child); return child
        previous = signal.getsignal(signal.SIGALRM)
        with mock.patch.object(R.M.subprocess, 'Popen', side_effect=start):
            with self.assertRaises(R.Deadline):
                with R.execution_deadline(0.05):
                    R.M.invoke([sys.executable, '-c', 'import time; time.sleep(10)'])
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())
        self.assertEqual(signal.getsignal(signal.SIGALRM), previous)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))

    def test_main_actual_owner_keeps_protocol_and_first_cancel_exit(self):
        def invoke(token, value, emit):
            return R.invoke(token, value, emit, state=self.root / 'main', client_factory=self.client,
                            runtime_guard=self.runtime, state_guard=self.state, source_reader=self.source)
        stream = io.StringIO()
        code = R.main(['describe', '--evidence-token', TOKEN, '--request-stdin'],
                      stdin=io.BytesIO(json.dumps(request()).encode()), stdout=stream, invoker=invoke)
        frames = [json.loads(x) for x in stream.getvalue().splitlines()]
        self.assertEqual(code, 0)
        self.assertEqual([x['event'] for x in frames], ['allocated', 'result'])
        self.assertEqual(frames[-1]['command'], 'catalog describe')
        self.assertEqual(frames[-1]['observation'], public())
        stream = io.StringIO()
        code = R.main(['describe', '--evidence-token', 'b' * 32, '--request-stdin'],
                      stdin=io.BytesIO(b'{"command":"describe","table":"other"}'), stdout=stream,
                      invoker=mock.Mock(side_effect=AssertionError('must not run')))
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stream.getvalue())['observation'], None)

    def test_sources_bind_transport_pure_contract_runtime_and_compose(self):
        paths = R.sources()
        for name in ('catalog_read/contract.py', 'catalog_read/run.py', 'metadata/coverage.py',
                     'metadata/runtime.py', 'metadata/client.py', 'metadata/curate.py'):
            self.assertIn('tools/local_experiment/' + name, paths)
        self.assertIn('deploy/local-experiment/metadata/compose.json', paths)
        self.assertEqual(len(paths), 9)


if __name__ == '__main__':
    unittest.main()
