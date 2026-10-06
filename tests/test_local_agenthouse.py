from __future__ import annotations

import importlib.util
import base64
import copy
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location("local_agenthouse", Path(__file__).resolve().parents[1] / "tools/local_experiment/agenthouse/manage.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class AgentHouseTests(unittest.TestCase):
    def test_status_version_reply_is_bounded_before_public_result(self):
        for version in ('25.8.4.1\x1b[0m', '25.8.4.1\nother', 'x' * 200, None):
            with self.subTest(version=version), patch('sys.argv', ['manage.py', 'status']), \
                    patch.object(M, 'Client') as client_class, contextlib.redirect_stdout(io.StringIO()) as output:
                client_class.return_value.query.return_value = version
                self.assertEqual(M.main(), 1)
                self.assertEqual(json.loads(output.getvalue())['result'], 'failed')
                client_class.return_value.query.assert_called_once_with('SELECT version()')
        with patch('sys.argv', ['manage.py', 'status']), patch.object(M, 'Client') as client_class, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            client_class.return_value.query.return_value = '25.8.4.1\n'
            self.assertEqual(M.main(), 0)
            self.assertEqual(json.loads(output.getvalue())['version'], '25.8.4.1')

    def test_unknown_usage_remains_null_and_retry_preserves_identity(self):
        value = M.event("test", "clickhouse")
        self.assertIsNone(value["inference_cost_estimate"])
        self.assertIsNone(value["prompt_tokens"])
        client = Mock()
        M.insert(client, value)
        M.insert(client, value)
        self.assertEqual(client.query.call_args_list[0], client.query.call_args_list[1])

    def test_free_form_payloads_and_invalid_metrics_are_rejected(self):
        for fields in ({"prompt": "private"}, {"duration_ms": -1}, {"prompt_tokens": True},
                        {"inference_cost_estimate": float("nan")}, {"human_override": "yes"},
                       {"status": "maybe"}, {"agent_id": "x\nsecret"}, {"task_id": "x" * 257},
                       {"duration_ms": None}):
            with self.subTest(fields=fields), self.assertRaises(M.AgentHouseError):
                M.event("test", "clickhouse", **fields)

    def test_control_bearing_pawprint_text_stops_before_http(self):
        for text in ('tab\tvalue', 'nul\x00value', 'delete\x7fvalue', 'c1\x85value'):
            with self.subTest(text=repr(text)):
                with self.assertRaises(M.AgentHouseError):
                    M.event(text, 'clickhouse')
                value = M.event('test', 'clickhouse')
                value['action_type'] = text
                client = Mock()
                with self.assertRaises(M.AgentHouseError):
                    M.insert(client, value)
                client.query.assert_not_called()
        self.assertEqual(M.event('test', 'clickhouse', model_name='safe-name')['model_name'], 'safe-name')

    def test_float_metrics_refuse_unrepresentable_integer_before_insert(self):
        for field in ('inference_cost_estimate', 'cpu_seconds', 'memory_mb_seconds'):
            for number in (2**53 + 1, 10**400):
                with self.subTest(field=field, number=str(number)[:20]):
                    with self.assertRaises(M.AgentHouseError):
                        M.event('test', 'clickhouse', **{field: number})
                    value = M.event('test', 'clickhouse')
                    value[field] = number
                    client = Mock()
                    with self.assertRaises(M.AgentHouseError):
                        M.insert(client, value)
                    client.query.assert_not_called()
            self.assertEqual(M.event('test', 'clickhouse', **{field: 2**53})[field], 2**53)

    def test_invalid_event_identity_and_timestamp_fail_before_insert(self):
        for fields in ({'event_id': 'not-a-uuid'}, {'timestamp': 'yesterday'},
                       {'event_id': '00000000000000000000000000000001'},
                       {'timestamp': '2026-1-2 03:04:05.123'},
                       {'timestamp': '2026-01-02 03:04:05.123000'}):
            with self.subTest(fields=fields), self.assertRaises(M.AgentHouseError):
                M.event('test', 'clickhouse', **fields)
            value = M.event('test', 'clickhouse')
            value.update(fields)
            client = Mock()
            with self.assertRaises(M.AgentHouseError):
                M.insert(client, value)
            client.query.assert_not_called()

    def test_unencodable_event_fields_fail_before_insert(self):
        for field in ('action_type', 'target_system', 'agent_id', 'model_name'):
            action = 'bad\ud800' if field == 'action_type' else 'test'
            target = 'bad\ud800' if field == 'target_system' else 'clickhouse'
            fields = {field: 'bad\ud800'} if field not in {'action_type', 'target_system'} else {}
            with self.subTest(field=field), self.assertRaises(M.AgentHouseError):
                M.event(action, target, **fields)
            value = M.event('test', 'clickhouse')
            value[field] = 'bad\ud800'
            client = Mock()
            with self.assertRaises(M.AgentHouseError):
                M.insert(client, value)
            client.query.assert_not_called()

    def test_insert_uses_json_data_not_sql_interpolation(self):
        value = M.event("x'); DROP TABLE unrelated; --", "clickhouse")
        client = Mock()
        M.insert(client, value)
        statement = client.query.call_args.args[0]
        prefix, data = statement.split("\n", 1)
        self.assertEqual(prefix, "INSERT INTO agenthouse.pawprints FORMAT JSONEachRow")
        self.assertEqual(json.loads(data)["action_type"], value["action_type"])

    def test_unowned_named_runtime_is_preserved(self):
        with patch.object(M, "docker", return_value=json.dumps([{"Config": {"Labels": {}}}])):
            with self.assertRaisesRegex(M.AgentHouseError, "unowned"):
                M.inspect_owned("container", M.NAME)

    def test_owned_inspection_must_report_requested_resource_name(self):
        for kind, name, labels in (
            ('container', M.NAME, {'Config': {'Labels': {M.LABEL: 'agenthouse-local'}}}),
            ('volume', M.VOLUME, {'Labels': {M.LABEL: 'agenthouse-local'}}),
        ):
            for reported in (None, '/other' if kind == 'container' else 'other',
                             name if kind == 'container' else '/' + name):
                reply = dict(labels)
                if reported is not None:
                    reply['Name'] = reported
                with self.subTest(kind=kind, reported=reported), \
                        patch.object(M, 'docker', return_value=json.dumps([reply])):
                    with self.assertRaisesRegex(M.AgentHouseError, 'invalid .* inspection'):
                        M.inspect_owned(kind, name)
            reply = dict(labels, Name='/' + name if kind == 'container' else name)
            with self.subTest(kind=kind, reported='correct'), \
                    patch.object(M, 'docker', return_value=json.dumps([reply])):
                self.assertEqual(M.inspect_owned(kind, name), reply)

    def test_malformed_owned_inspection_is_not_absence(self):
        for kind, name in (('container', M.NAME), ('volume', M.VOLUME)):
            for reply in ('[]', '{}', 'not json', '[null]', '[{}, {}]',
                          '[{"Config":null}]' if kind == 'container' else '[{"Labels":[]}]'):
                with self.subTest(kind=kind, reply=reply), \
                        patch.object(M, 'docker', return_value=reply):
                    with self.assertRaisesRegex(M.AgentHouseError, 'invalid .* inspection'):
                        M.inspect_owned(kind, name)

    def test_ambiguous_owned_inspection_does_not_authorize_runtime(self):
        for kind, name, reply in (
            ('container', M.NAME, '[{"Config":{"Labels":{"shirokuma.dev.component":"foreign","shirokuma.dev.component":"agenthouse-local"}}}]'),
            ('volume', M.VOLUME, '[{"Labels":{"shirokuma.dev.component":"foreign","shirokuma.dev.component":"agenthouse-local"}}]'),
        ):
            with self.subTest(kind=kind), patch.object(M, 'docker', return_value=reply):
                with self.assertRaisesRegex(M.AgentHouseError, 'invalid .* inspection'):
                    M.inspect_owned(kind, name)

    def test_ambiguous_context_endpoint_stops_before_docker_info(self):
        expected = 'unix://' + str(Path.home() / '.colima/mac-studio-solo/docker.sock')
        reply = '[{"Endpoints":{"docker":{"Host":"unix:///foreign","Host":' + json.dumps(expected) + '}}}]'
        with patch.object(M, 'docker', return_value=reply) as docker:
            with self.assertRaisesRegex(M.AgentHouseError, 'context metadata'):
                M.check_context()
            docker.assert_called_once_with('context', 'inspect', M.CONTEXT)

    def test_nonstandard_docker_inspection_numbers_are_not_valid_metadata(self):
        expected = 'unix://' + str(Path.home() / '.colima/mac-studio-solo/docker.sock')
        for token in ('NaN', 'Infinity', '-Infinity'):
            reply = '[{"Endpoints":{"docker":{"Host":' + json.dumps(expected) + '},"unknown":' + token + '}}]'
            with self.subTest(token=token), patch.object(M, 'docker', return_value=reply) as docker:
                with self.assertRaisesRegex(M.AgentHouseError, 'context metadata'):
                    M.check_context()
                docker.assert_called_once_with('context', 'inspect', M.CONTEXT)
            owned = '[{"Labels":{"shirokuma.dev.component":"agenthouse-local"},"unknown":' + token + '}]'
            with patch.object(M, 'docker', return_value=owned):
                with self.assertRaisesRegex(M.AgentHouseError, 'invalid volume inspection'):
                    M.inspect_owned('volume', M.VOLUME)

    def test_unrepresentable_docker_inspection_numbers_fail_before_runtime(self):
        expected = 'unix://' + str(Path.home() / '.colima/mac-studio-solo/docker.sock')
        for token in ('1e9999', '1e-9999', '9' * 129, '1.' + '0' * 129):
            with self.subTest(token=token[:24]):
                reply = '[{"Endpoints":{"docker":{"Host":' + json.dumps(expected) + '}},"unknown":' + token + '}]'
                with patch.object(M, 'docker', return_value=reply) as docker:
                    with self.assertRaisesRegex(M.AgentHouseError, 'context metadata'):
                        M.check_context()
                    docker.assert_called_once_with('context', 'inspect', M.CONTEXT)
                owned = '[{"Labels":{"shirokuma.dev.component":"agenthouse-local"},"unknown":' + token + '}]'
                with patch.object(M, 'docker', return_value=owned):
                    with self.assertRaisesRegex(M.AgentHouseError, 'invalid volume inspection'):
                        M.inspect_owned('volume', M.VOLUME)
        self.assertEqual(M.decode_docker_json('{"n":1e-2,"i":123}'), {'n': 0.01, 'i': 123})

    def test_docker_inspection_refuses_deep_or_unencodable_metadata(self):
        endpoint = 'unix://' + str(Path.home() / '.colima/mac-studio-solo/docker.sock')
        for extra in ('"\\ud800"', '[' * 65 + '0' + ']' * 65):
            reply = '[{"Endpoints":{"docker":{"Host":' + json.dumps(endpoint) + '}},"extra":' + extra + '}]'
            with self.subTest(extra=extra[:30]), patch.object(M, 'docker', return_value=reply) as docker:
                with self.assertRaisesRegex(M.AgentHouseError, 'context metadata'):
                    M.check_context()
                docker.assert_called_once_with('context', 'inspect', M.CONTEXT)
            owned = '[{"Labels":{"shirokuma.dev.component":"agenthouse-local"},"extra":' + extra + '}]'
            with patch.object(M, 'docker', return_value=owned):
                with self.assertRaisesRegex(M.AgentHouseError, 'invalid volume inspection'):
                    M.inspect_owned('volume', M.VOLUME)
        self.assertEqual(M.decode_docker_json('{"nested":["ordinary"]}'), {'nested': ['ordinary']})

    def test_docker_inspection_reply_is_bounded_before_json_parse(self):
        endpoint = 'unix://' + str(Path.home() / '.colima/mac-studio-solo/docker.sock')
        oversized = '[{"Name":' + json.dumps(M.CONTEXT) + ',"Endpoints":{"docker":{"Host":' + json.dumps(endpoint) + '}},"unused":"' + 'a' * (2 * 1024 * 1024) + '"}]'
        with patch.object(M, 'docker', return_value=oversized) as docker:
            with self.assertRaisesRegex(M.AgentHouseError, 'context metadata'):
                M.check_context()
            docker.assert_called_once_with('context', 'inspect', M.CONTEXT)
        with self.assertRaises(ValueError):
            M.decode_docker_json('"\ud800"')

    def test_context_must_be_dedicated_socket(self):
        expected = 'unix://' + str(Path.home() / '.colima/mac-studio-solo/docker.sock')
        for endpoint in ('unix:///var/run/docker.sock',
                         'unix:///tmp/.colima/mac-studio-solo/docker.sock',
                         expected + '/other'):
            config = [{"Endpoints": {"docker": {"Host": endpoint}}}]
            with self.subTest(endpoint=endpoint), patch.object(M, "docker", return_value=json.dumps(config)) as docker:
                with self.assertRaisesRegex(M.AgentHouseError, "dedicated"):
                    M.check_context()
                self.assertEqual(docker.call_count, 1)
        config = [{"Name": M.CONTEXT, "Endpoints": {"docker": {"Host": expected}}}]
        with patch.object(M, "docker", side_effect=[json.dumps(config), 'aarch64']) as docker:
            M.check_context()
            self.assertEqual(docker.call_count, 2)

    def test_context_reply_must_identify_requested_context(self):
        endpoint = 'unix://' + str(Path.home() / '.colima/mac-studio-solo/docker.sock')
        for name in (None, 'default', M.CONTEXT + '-other', ''):
            context = {'Endpoints': {'docker': {'Host': endpoint}}}
            if name is not None:
                context['Name'] = name
            with self.subTest(name=name), patch.object(M, 'docker', return_value=json.dumps([context])) as docker:
                with self.assertRaisesRegex(M.AgentHouseError, 'context metadata'):
                    M.check_context()
                docker.assert_called_once_with('context', 'inspect', M.CONTEXT)
        with patch.object(M, 'docker', side_effect=[json.dumps([{
                'Name': M.CONTEXT, 'Endpoints': {'docker': {'Host': endpoint}}}]), 'arm64\n']):
            M.check_context()

    def test_architecture_reply_requires_one_exact_line(self):
        endpoint = 'unix://' + str(Path.home() / '.colima/mac-studio-solo/docker.sock')
        config = json.dumps([{'Name': M.CONTEXT, 'Endpoints': {'docker': {'Host': endpoint}}}])
        for architecture in ('aarch64\n', 'arm64', 'arm64\n'):
            with self.subTest(architecture=architecture), patch.object(M, 'docker', side_effect=[config, architecture]):
                M.check_context()
        for architecture in (' arm64\n', 'arm64\n\n', 'arm64\r\n', 'arm64\t', 'arm64\x00'):
            with self.subTest(architecture=architecture), patch.object(M, 'docker', side_effect=[config, architecture]):
                with self.assertRaisesRegex(M.AgentHouseError, 'ARM64'):
                    M.check_context()

    def test_malformed_context_stops_before_docker_info(self):
        for reply in ('not json', '{}', '[]', '[{}, {}]', '[null]',
                      '[{"Endpoints":null}]', '[{"Endpoints":{"docker":{}}}]'):
            with self.subTest(reply=reply), patch.object(M, 'docker', return_value=reply) as docker:
                with self.assertRaisesRegex(M.AgentHouseError, 'context metadata'):
                    M.check_context()
                docker.assert_called_once_with('context', 'inspect', M.CONTEXT)

    def test_redirect_never_receives_authorization(self):
        with self.assertRaisesRegex(M.AgentHouseError, "redirect"):
            M.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com")

    def test_direct_client_uses_only_bounded_private_regular_credential(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            credential = state / 'credential.json'
            credential.write_text('{"password":"fixture-only"}')
            credential.chmod(0o600)
            with patch.object(M, 'STATE', state):
                self.assertEqual(base64.b64decode(M.Client().auth).decode(),
                                 M.USER + ':fixture-only')
                credential.chmod(0o644)
                with self.assertRaisesRegex(M.AgentHouseError, 'private regular'):
                    M.Client()
                credential.chmod(0o600)
                credential.write_bytes(b'x' * 16385)
                with self.assertRaisesRegex(M.AgentHouseError, 'private regular'):
                    M.Client()
                credential.unlink()
                other = state / 'other'
                other.write_text('{"password":"foreign"}')
                other.chmod(0o600)
                credential.symlink_to(other)
                with self.assertRaisesRegex(M.AgentHouseError, 'opened safely'):
                    M.Client()
                credential.unlink()
                self.assertEqual(base64.b64decode(M.Client(
                    credential_bytes=b'{"password":"pinned"}').auth).decode(),
                                   M.USER + ':pinned')

    def test_credential_shape_and_env_token_stop_before_http(self):
        for raw in (b'{"password":"ok","unused":1}', b'["ok"]',
                    b'{"password":"bad:colon"}', b'{"password":"bad space"}',
                    b'{"password":"bad\\u2028value"}', b'{"password":""}'):
            with self.subTest(raw=raw), self.assertRaisesRegex(M.AgentHouseError, 'invalid format'):
                M.Client(credential_bytes=raw)
        self.assertEqual(base64.b64decode(M.Client(credential_bytes=b'{"password":"safe_123-"}').auth).decode(),
                         M.USER + ':safe_123-')

    def test_ambiguous_credential_json_is_refused_before_http(self):
        for raw in (b'{"password":"first","password":"last"}',
                    b'{"password":"valid","nested":{"x":1,"x":2}}',
                    b'{"password":"valid","unused":NaN}'):
            with self.subTest(raw=raw), self.assertRaisesRegex(M.AgentHouseError, 'invalid format'):
                M.Client(credential_bytes=raw)
        self.assertIn('valid', base64.b64decode(M.Client(
            credential_bytes=b'{"password":"valid"}').auth).decode())

    def test_credential_refuses_non_utf8_or_bom_encoding(self):
        value = '{"password":"valid"}'
        for raw in (value.encode('utf-16'), value.encode('utf-32'),
                    b'\xef\xbb\xbf' + value.encode('utf-8')):
            with self.subTest(raw=raw[:8]), self.assertRaisesRegex(M.AgentHouseError, 'invalid format'):
                M.Client(credential_bytes=raw)

    def test_direct_client_refuses_redirected_or_public_state_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / 'state'
            state.mkdir(mode=0o700)
            credential = state / 'credential.json'
            credential.write_text('{"password":"fixture-only"}')
            credential.chmod(0o600)
            with patch.object(M, 'STATE', state):
                self.assertIn('fixture-only', base64.b64decode(M.Client().auth).decode())
                state.chmod(0o755)
                with self.assertRaises(M.AgentHouseError):
                    M.Client()
                state.chmod(0o700)
                state.rename(root / 'original')
                state.symlink_to(root / 'original', target_is_directory=True)
                with self.assertRaises(M.AgentHouseError):
                    M.Client()

    def test_export_refuses_existing_report_and_symlink_without_modification(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            client = Mock()
            client.query.return_value = '{"event_id":"fixture"}\n'
            with patch.object(M, 'STATE', state), patch.object(M, 'datetime') as clock:
                clock.now.return_value.strftime.return_value = 'fixed'
                existing = state / 'pawprints-fixed.jsonl'
                existing.write_text('retained')
                with self.assertRaises(M.AgentHouseError):
                    M.export(client)
                self.assertEqual(existing.read_text(), 'retained')
                existing.unlink()
                target = state / 'other'
                target.write_text('retained')
                existing.symlink_to(target)
                with self.assertRaises(M.AgentHouseError):
                    M.export(client)
                self.assertEqual(target.read_text(), 'retained')
                existing.unlink()
                self.assertEqual(M.export(client), existing)
                self.assertEqual(existing.read_text(), client.query.return_value)
                self.assertEqual(existing.stat().st_mode & 0o777, 0o600)
                self.assertEqual(list(state.glob('*.pending')), [])

    def test_smoke_invalid_readback_is_scoped_without_replaying_insert(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'pawprints-20261006T010203000000Z.jsonl'
            report.write_text('{"other":"value"}\n')
            report.chmod(0o600)
            client = Mock()
            with patch.object(M, 'STATE', Path(directory)), patch.object(M, 'event', return_value={'event_id': 'fixture'}), \
                    patch.object(M, 'insert') as inserted, patch.object(M, 'export', return_value=report):
                for reply in ('{}', '{"data":null}', '{"data":[{"n":1,"status":"success"}]}'):
                    client.query.return_value = reply
                    with self.subTest(reply=reply), self.assertRaises(M.AgentHouseError):
                        M.smoke(client)
                self.assertEqual(inserted.call_count, 6)  # two writes per attempt; no hidden retry

    def test_smoke_refuses_ambiguous_count_and_export_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'pawprints-20261006T010203000000Z.jsonl'
            value = M.event('runtime.smoke', 'clickhouse')
            good = json.dumps({key: value[key] for key in
                               ('event_id', 'timestamp', 'agent_id', 'task_id',
                                'action_type', 'target_system', 'status')}) + '\n'
            report.write_text(good)
            report.chmod(0o600)
            client = Mock()
            with patch.object(M, 'STATE', Path(directory)), patch.object(M, 'event', return_value=value), \
                    patch.object(M, 'insert') as inserted, patch.object(M, 'export', return_value=report):
                for reply in ('{"data":[{"n":true,"status":"success"}]}',
                              '{"data":[{"n":1,"n":1,"status":"success"}]}',
                              '{"data":[{"n":1,"status":"success"}],"data":[]}',
                              '{"data":[{"n":1,"status":"success"}],"unused":' + '9' * 129 + '}'):
                    with self.subTest(reply=reply[:60]):
                        client.query.return_value = reply
                        with self.assertRaises(M.AgentHouseError):
                            M.smoke(client)
                client.query.return_value = '{"data":[{"n":1,"status":"success"}]}'
                for text in (good.replace('"success"', '"failed"'), good * 2):
                    with self.subTest(report=text):
                        report.write_text(text)
                        with self.assertRaises(M.AgentHouseError):
                            M.smoke(client)
                report.write_text(good)
                client.query.side_effect = lambda statement: '25.8.4.1\n' if statement == 'SELECT version()' else '{"data":[{"n":1,"status":"success"}]}'
                self.assertEqual(M.smoke(client)['result'], 'passed')
                self.assertEqual(inserted.call_count, 14)

    def test_smoke_count_refuses_unexamined_nested_values(self):
        client = Mock()
        with patch.object(M, 'event', return_value={'event_id': 'fixture'}), \
                patch.object(M, 'insert') as inserted, patch.object(M, 'export') as exported:
            for extra in ('{"bad":"\\ud800"}', '[' * 65 + '0' + ']' * 65,
                          '1e-999', '-1e-999'):
                client.query.return_value = ('{"data":[{"n":1,"status":"success"}],'
                                             '"unused":' + extra + '}')
                with self.subTest(extra=extra[:30]), self.assertRaises(M.AgentHouseError):
                    M.smoke(client)
            self.assertEqual(inserted.call_count, 8)
            exported.assert_not_called()

    def test_smoke_version_reply_is_bounded_before_public_result(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'pawprints-20261006T010203000000Z.jsonl'
            value = M.event('runtime.smoke', 'clickhouse')
            report.write_text(json.dumps({key: value[key] for key in
                                          ('event_id', 'timestamp', 'agent_id', 'task_id',
                                           'action_type', 'target_system', 'status')}) + '\n')
            report.chmod(0o600)
            client = Mock()
            with patch.object(M, 'STATE', Path(directory)), patch.object(M, 'event', return_value=value), \
                    patch.object(M, 'insert') as inserted, patch.object(M, 'export', return_value=report):
                for version in ('25.8.4.1\x1b[0m', '25.8.4.1\nother', 'x' * 200, None):
                    client.query.side_effect = lambda statement: version if statement == 'SELECT version()' else '{"data":[{"n":1,"status":"success"}]}'
                    with self.subTest(version=version), self.assertRaisesRegex(M.AgentHouseError, 'version reply'):
                        M.smoke(client)
                client.query.side_effect = lambda statement: '25.8.4.1\n' if statement == 'SELECT version()' else '{"data":[{"n":1,"status":"success"}]}'
                self.assertEqual(M.smoke(client)['version'], '25.8.4.1')
                self.assertEqual(inserted.call_count, 10)

    def test_smoke_refuses_same_id_with_different_exported_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'pawprints-20261006T010203000000Z.jsonl'
            value = M.event('runtime.smoke', 'clickhouse')
            fields = ('event_id', 'timestamp', 'agent_id', 'task_id',
                      'action_type', 'target_system', 'status')
            client = Mock()
            client.query.return_value = '{"data":[{"n":1,"status":"success"}]}'
            with patch.object(M, 'STATE', Path(directory)), patch.object(M, 'event', return_value=value), \
                    patch.object(M, 'insert') as inserted, patch.object(M, 'export', return_value=report):
                for field in fields[1:-1]:
                    row = {key: value[key] for key in fields}
                    row[field] = 'other'
                    report.write_text(json.dumps(row) + '\n')
                    report.chmod(0o600)
                    with self.subTest(field=field), self.assertRaisesRegex(M.AgentHouseError, 'identity differs'):
                        M.smoke(client)
                self.assertEqual(inserted.call_count, 10)

    def test_smoke_export_readback_rejects_redirected_and_oversized_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state'
            state.mkdir(mode=0o700)
            report = state / 'pawprints-20261006T010203000000Z.jsonl'
            foreign = Path(directory) / 'foreign'
            foreign.write_text('{"event_id":"fixture","status":"success"}\n')
            report.symlink_to(foreign)
            with patch.object(M, 'STATE', state):
                with self.assertRaises(M.AgentHouseError):
                    M.read_smoke_export(report)
                report.unlink()
                report.write_text(foreign.read_text())
                report.chmod(0o644)
                with self.assertRaises(M.AgentHouseError):
                    M.read_smoke_export(report)
                report.chmod(0o600)
                self.assertEqual(M.read_smoke_export(report)[0]['event_id'], 'fixture')
                with report.open('ab') as file:
                    file.truncate(8 * 1024 * 1024 + 1)
                with self.assertRaises(M.AgentHouseError):
                    M.read_smoke_export(report)
                with self.assertRaises(M.AgentHouseError):
                    M.read_smoke_export(foreign)

    def test_smoke_export_readback_refuses_corrupt_other_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            report = state / 'pawprints-20261006T010203000000Z.jsonl'
            good = '{"event_id":"fixture","status":"success"}\n'
            with patch.object(M, 'STATE', state):
                for suffix in ('{"other":1,"other":2}\n', 'not-json\n',
                               'null\n', '{"other":NaN}\n', '{"other":1}',
                               '{"other":1e999}\n', '{"other":1e-999}\n',
                               '{"other":2}\r\n', '\n'):
                    report.write_text(good + suffix)
                    report.chmod(0o600)
                    with self.subTest(suffix=suffix), self.assertRaises(M.AgentHouseError):
                        M.read_smoke_export(report)

    def test_smoke_export_readback_refuses_invalid_nested_values(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'pawprints-20261006T010203000000Z.jsonl'
            good = '{"event_id":"fixture","status":"success"}\n'
            with patch.object(M, 'STATE', Path(directory)):
                for suffix in ('{"other":"\\ud800"}\n',
                               '{"\\ud800":1}\n',
                               '{"other":' + '[' * 65 + '0' + ']' * 65 + '}\n'):
                    report.write_text(good + suffix)
                    report.chmod(0o600)
                    with self.subTest(suffix=suffix[:30]), self.assertRaises(M.AgentHouseError):
                        M.read_smoke_export(report)

    def test_export_write_failure_never_publishes_partial_report(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            client = Mock()
            client.query.return_value = '{"event_id":"fixture"}\n'
            with patch.object(M, 'STATE', state), patch.object(M, 'datetime') as clock:
                clock.now.return_value.strftime.return_value = 'fixed'
                with patch.object(M.os, 'fsync', side_effect=OSError('fixture disk failure')):
                    with self.assertRaisesRegex(M.AgentHouseError, 'pending'):
                        M.export(client)
                self.assertFalse((state / 'pawprints-fixed.jsonl').exists())
                pending = list(state.glob('*.pending'))
                self.assertEqual(len(pending), 1)
                self.assertEqual(pending[0].stat().st_mode & 0o777, 0o600)
                # A failed attempt is retained; a later export must not erase it.
                self.assertEqual(M.export(client).read_text(), client.query.return_value)
                self.assertTrue(pending[0].exists())

    def test_export_existing_name_is_not_replaced_by_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            report = state / 'pawprints-fixed.jsonl'
            report.write_text('retained')
            client = Mock()
            client.query.return_value = '{}\n'
            with patch.object(M, 'STATE', state), patch.object(M, 'datetime') as clock:
                clock.now.return_value.strftime.return_value = 'fixed'
                with self.assertRaises(M.AgentHouseError):
                    M.export(client)
                self.assertEqual(report.read_text(), 'retained')
                self.assertEqual(list(state.glob('*.pending')), [])

    def test_unencodable_export_reply_does_not_create_report(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            client = Mock()
            client.query.return_value = 'bad\ud800'
            with patch.object(M, 'STATE', state), patch.object(M, 'datetime') as clock:
                clock.now.return_value.strftime.return_value = 'fixed'
                with self.assertRaises(M.AgentHouseError):
                    M.export(client)
                self.assertEqual(list(state.iterdir()), [])

    def test_malformed_export_reply_does_not_create_report(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            client = Mock()
            with patch.object(M, 'STATE', state), patch.object(M, 'datetime') as clock:
                clock.now.return_value.strftime.return_value = 'fixed'
                for reply in ('not json\n', '{"event_id":1}', '[]\n', '{}\n\n',
                               '{"event_id":"fixture"}\r\n',
                               ' {"event_id":"fixture"}\n',
                               '{"event_id":"fixture"} \n',
                               '{"event_id":"fixture"}\t\n',
                              '{"a":1}\u2028{"b":2}\n', '{"a":1}\x85{"b":2}\n',
                              '{"event_id":"a","event_id":"b"}\n',
                              '{"value":NaN}\n', '{"value":Infinity}\n',
                               '{"nested":{"value":-Infinity}}\n',
                               '{"value":"\\ud800"}\n', '{"\\ud800":1}\n',
                               '{"value":1e9999}\n', '{"value":1e-9999}\n',
                               '{"value":' + '9' * 129 + '}\n',
                               '{"value":1.' + '0' * 129 + '}\n',
                               '{"value":1e' + '0' * 129 + '}\n',
                               '{"nested":' + '[' * 65 + '0' + ']' * 65 + '}\n',
                               '{"nested":' + '[' * 1200 + '0' + ']' * 1200 + '}\n',
                               ' ' * (8 * 1024 * 1024 + 1)):
                    with self.subTest(reply=reply[:32]):
                        client.query.return_value = reply
                        with self.assertRaises(M.AgentHouseError):
                            M.export(client)
                        self.assertEqual(list(state.iterdir()), [])

                client.query.return_value = '{"nested":' + '[' * 63 + '0' + ']' * 63 + '}\n'
                self.assertEqual(M.export(client).read_text(), client.query.return_value)

    def test_query_rejects_invalid_utf8_response_with_scoped_error(self):
        client = M.Client(credential_bytes=b'{"password":"fixture-only"}')
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'\xff'
        client.opener.open = Mock(return_value=response)
        with self.assertRaises(M.AgentHouseError):
            client.query('SELECT 1')

    def test_unencodable_credential_and_query_fail_before_http(self):
        with self.assertRaisesRegex(M.AgentHouseError, 'invalid format'):
            M.Client(credential_bytes=b'{"password":"\\ud800"}')
        client = M.Client(credential_bytes=b'{"password":"fixture-only"}')
        with patch.object(client.opener, 'open') as open_request:
            with self.assertRaisesRegex(M.AgentHouseError, 'invalid SQL'):
                client.query('SELECT \ud800')
            open_request.assert_not_called()

    def test_non_string_query_fails_before_http(self):
        client = M.Client(credential_bytes=b'{"password":"fixture-only"}')
        with patch.object(client.opener, 'open') as open_request:
            for sql in (None, b'SELECT 1', 12):
                with self.subTest(sql=sql), self.assertRaises(M.AgentHouseError):
                    client.query(sql)
            open_request.assert_not_called()

    def test_insert_rejects_missing_fields_without_inventing_retry_identity(self):
        complete = M.event("test", "clickhouse")
        self.assertEqual(set(complete), M.EVENT_FIELDS)
        for field in complete:
            with self.subTest(field=field):
                incomplete = dict(complete)
                del incomplete[field]
                client = Mock()
                with self.assertRaisesRegex(M.AgentHouseError, "complete Pawprint"):
                    M.insert(client, incomplete)
                client.query.assert_not_called()
        unknown = dict(complete, extra="not accepted")
        with self.assertRaisesRegex(M.AgentHouseError, "complete Pawprint"):
            M.insert(Mock(), unknown)

    @staticmethod
    def existing_runtime():
        return {
            "Config": {"Image": M.IMAGE},
            "State": {"Status": "exited", "Running": False, "Paused": False,
                      "Restarting": False, "Dead": False},
            "HostConfig": {"PortBindings": {"8123/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18123"}]},
                           "NanoCpus": 2_000_000_000, "Memory": 4 * 1024**3,
                           "NetworkMode": "default", "Privileged": False,
                            "PidMode": "", "IpcMode": "private", "UTSMode": "",
                            "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
                            "PublishAllPorts": False,
                           "SecurityOpt": ["no-new-privileges:true"],
                           "CapAdd": None, "Devices": [], "Binds": None},
            "Mounts": [{"Type": "volume", "Name": M.VOLUME,
                        "Destination": "/var/lib/clickhouse", "RW": True}],
        }

    def ensure_existing(self, existing):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "credential.json").write_text(json.dumps({"password": "test-only"}))
            with patch.object(M, "STATE", state), patch.object(M, "check_context"), \
                    patch.object(M, "inspect_owned", side_effect=[existing, {"Labels": {M.LABEL: "agenthouse-local"}}]), \
                    patch.object(M, "docker") as docker, patch.object(M, "Client") as client:
                client.return_value.query.return_value = "1"
                try:
                    result = M.ensure_runtime()
                except M.AgentHouseError:
                    docker.assert_not_called()
                    client.assert_not_called()
                    self.assertFalse((state / 'container.env').exists())
                    raise
                docker.assert_called_once_with("start", M.NAME)
                self.assertIs(result, client.return_value)
                self.assertFalse((state / 'container.env').exists())

    def test_existing_runtime_requires_exact_owned_persistent_mount(self):
        valid = self.existing_runtime()
        self.ensure_existing(valid)
        variants = [[], valid["Mounts"] * 2]
        for change in ({"Type": "bind"}, {"Name": "retained-unrelated"},
                       {"Destination": "/other"}, {"RW": False}):
            variants.append([dict(valid["Mounts"][0], **change)])
        for mounts in variants:
            with self.subTest(mounts=mounts):
                existing = copy.deepcopy(valid)
                existing["Mounts"] = mounts
                with self.assertRaisesRegex(M.AgentHouseError, "owned data volume"):
                    self.ensure_existing(existing)

    def test_existing_runtime_requires_unambiguously_stopped_state(self):
        self.ensure_existing(self.existing_runtime())
        created = self.existing_runtime()
        created['State']['Status'] = 'created'
        self.ensure_existing(created)
        for field, value in (('Status', 'running'), ('Status', 'paused'),
                             ('Status', 'restarting'), ('Status', 'dead'),
                             ('Status', 'unknown'), ('Running', True),
                             ('Paused', True), ('Restarting', True), ('Dead', True)):
            existing = self.existing_runtime()
            existing['State'][field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(
                    M.AgentHouseError, 'stopped'):
                self.ensure_existing(existing)
        for field in ('Status', 'Running', 'Paused', 'Restarting', 'Dead'):
            existing = self.existing_runtime()
            del existing['State'][field]
            with self.subTest(missing=field), self.assertRaisesRegex(
                    M.AgentHouseError, 'stopped'):
                self.ensure_existing(existing)
        existing = self.existing_runtime()
        existing['State'] = None
        with self.assertRaisesRegex(M.AgentHouseError, 'stopped'):
            self.ensure_existing(existing)

    def test_readiness_requires_exact_select_one_reply(self):
        # The ClickHouse plain-text response has one LF; arbitrary surrounding
        # whitespace must not turn a different response into readiness.
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            credential = state / 'credential.json'
            credential.write_text(json.dumps({'password': 'fixture'}))
            credential.chmod(0o600)
            with patch.object(M, 'STATE', state), patch.object(M, 'check_context'), \
                    patch.object(M, 'inspect_owned', side_effect=[self.existing_runtime(), {}]), \
                    patch.object(M, 'docker'), patch.object(M, 'Client') as client:
                client.return_value.query.return_value = '1\n'
                self.assertIs(M.ensure_runtime(), client.return_value)
        for reply in (' 1\n', '1\n\n', '1\r\n', '1\t', '1\x00'):
            with self.subTest(reply=reply), tempfile.TemporaryDirectory() as directory:
                state = Path(directory)
                credential = state / 'credential.json'
                credential.write_text(json.dumps({'password': 'fixture'}))
                credential.chmod(0o600)
                with patch.object(M, 'STATE', state), patch.object(M, 'check_context'), \
                        patch.object(M, 'inspect_owned', side_effect=[self.existing_runtime(), {}]), \
                        patch.object(M, 'docker') as docker, patch.object(M, 'Client') as client, \
                        patch.object(M.time, 'monotonic', side_effect=[0, 90]), \
                        patch.object(M.time, 'sleep') as sleep:
                    client.return_value.query.return_value = reply
                    with self.assertRaisesRegex(M.AgentHouseError, 'did not become ready'):
                        M.ensure_runtime()
                    docker.assert_called_once_with('start', M.NAME)
                    client.return_value.query.assert_called_once_with('SELECT 1')
                    sleep.assert_not_called()

    def test_existing_runtime_refuses_unbounded_or_changed_resources(self):
        for key, value in (("NanoCpus", 0), ("NanoCpus", 4_000_000_000),
                           ("Memory", 0), ("Memory", 8 * 1024**3)):
            with self.subTest(key=key, value=value):
                existing = self.existing_runtime()
                existing["HostConfig"][key] = value
                with self.assertRaisesRegex(M.AgentHouseError, "resource limits"):
                    self.ensure_existing(existing)

    def test_existing_runtime_refuses_unsafe_or_missing_isolation(self):
        for key, values in (("NetworkMode", ("host", "container:foreign", None)),
                            ("Privileged", (True, None, 0)),
                            ("PidMode", ("host", None)),
                            ("PublishAllPorts", (True, None, 0))):
            for value in values:
                with self.subTest(key=key, value=value):
                    existing = self.existing_runtime()
                    existing['HostConfig'][key] = value
                    with self.assertRaisesRegex(M.AgentHouseError, 'isolation'):
                        self.ensure_existing(existing)
            existing = self.existing_runtime()
            del existing['HostConfig'][key]
            with self.subTest(key=key, value='missing'), self.assertRaisesRegex(M.AgentHouseError, 'isolation'):
                self.ensure_existing(existing)

    def test_existing_runtime_refuses_shared_ipc_or_uts_namespace(self):
        for key, values in (("IpcMode", ("host", "container:foreign", None)),
                            ("UTSMode", ("host", "container:foreign", None))):
            for value in values:
                with self.subTest(key=key, value=value):
                    existing = self.existing_runtime()
                    existing['HostConfig'][key] = value
                    with self.assertRaisesRegex(M.AgentHouseError, 'isolation'):
                        self.ensure_existing(existing)
            existing = self.existing_runtime()
            del existing['HostConfig'][key]
            with self.subTest(key=key, value='missing'), self.assertRaisesRegex(M.AgentHouseError, 'isolation'):
                self.ensure_existing(existing)
        private = self.existing_runtime()
        private['HostConfig']['IpcMode'] = ''  # Docker's other default spelling.
        self.ensure_existing(private)

    def test_existing_runtime_refuses_unattended_restart_policy(self):
        for policy in (None, {}, {'Name': 'always', 'MaximumRetryCount': 0},
                       {'Name': 'unless-stopped', 'MaximumRetryCount': 0},
                       {'Name': 'on-failure', 'MaximumRetryCount': 1},
                       {'Name': 'no', 'MaximumRetryCount': 1},
                       {'Name': 'no', 'MaximumRetryCount': False}):
            existing = self.existing_runtime()
            existing['HostConfig']['RestartPolicy'] = policy
            with self.subTest(policy=policy), self.assertRaisesRegex(M.AgentHouseError, 'restart policy'):
                self.ensure_existing(existing)
        existing = self.existing_runtime()
        del existing['HostConfig']['RestartPolicy']
        with self.assertRaisesRegex(M.AgentHouseError, 'restart policy'):
            self.ensure_existing(existing)

    def test_existing_runtime_refuses_lost_security_options_or_host_access(self):
        for key, values in (("SecurityOpt", ([], ["no-new-privileges:false"],
                                               ["no-new-privileges:true", "seccomp=unconfined"])),
                            ("CapAdd", (["SYS_ADMIN"],)),
                            ("Devices", ([{"PathOnHost": "/dev/disk0"}],)),
                            ("Binds", (["/host:/host"],))):
            for value in values:
                with self.subTest(key=key, value=value):
                    existing = self.existing_runtime()
                    existing['HostConfig'][key] = value
                    with self.assertRaisesRegex(M.AgentHouseError, 'isolation'):
                        self.ensure_existing(existing)
            existing = self.existing_runtime()
            del existing['HostConfig'][key]
            with self.subTest(key=key, value='missing'), self.assertRaisesRegex(M.AgentHouseError, 'isolation'):
                self.ensure_existing(existing)

    def test_existing_runtime_refuses_malformed_inspection_before_start(self):
        for field, malformed in (("Config", None), ("Config", {}),
                                 ("HostConfig", None), ("HostConfig", []),
                                 ("Mounts", None), ("Mounts", [None])):
            with self.subTest(field=field, malformed=malformed):
                existing = self.existing_runtime()
                existing[field] = malformed
                with self.assertRaises(M.AgentHouseError):
                    self.ensure_existing(existing)

    def test_new_runtime_refuses_existing_env_without_modifying_it(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state'
            state.mkdir(mode=0o700)
            credential = state / 'credential.json'
            credential.write_text('{"password":"fixture-only"}')
            credential.chmod(0o600)
            target = state / 'retained'
            target.write_text('keep')
            (state / 'container.env').symlink_to(target)
            with patch.object(M, 'STATE', state), patch.object(M, 'check_context'), \
                    patch.object(M, 'inspect_owned', side_effect=[None, None]), \
                    patch.object(M, 'docker') as docker:
                with self.assertRaises(M.AgentHouseError):
                    M.ensure_runtime()
                self.assertEqual(target.read_text(), 'keep')
                docker.assert_not_called()

    def test_new_runtime_refuses_dangling_credential_link(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state'
            state.mkdir(mode=0o700)
            target = state / 'target'
            (state / 'credential.json').symlink_to(target)
            with patch.object(M, 'STATE', state), patch.object(M, 'check_context'), \
                    patch.object(M, 'inspect_owned', side_effect=[None, None]), \
                    patch.object(M, 'docker') as docker:
                with self.assertRaises(M.AgentHouseError):
                    M.ensure_runtime()
                self.assertFalse(target.exists())
                docker.assert_not_called()

    def test_new_runtime_rejects_environment_line_in_credential(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state'
            state.mkdir(mode=0o700)
            credential = state / 'credential.json'
            credential.write_text(json.dumps({'password': 'fixture\nCLICKHOUSE_DB=other'}))
            credential.chmod(0o600)
            with patch.object(M, 'STATE', state), patch.object(M, 'check_context'), \
                    patch.object(M, 'inspect_owned', side_effect=[None, None]), \
                    patch.object(M, 'docker') as docker:
                with self.assertRaisesRegex(M.AgentHouseError, 'invalid format'):
                    M.ensure_runtime()
                self.assertFalse((state / 'container.env').exists())
                docker.assert_not_called()

    def test_existing_runtime_missing_credential_does_not_start(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state'
            state.mkdir(mode=0o700)
            with patch.object(M, 'STATE', state), patch.object(M, 'check_context'), \
                    patch.object(M, 'inspect_owned', side_effect=[self.existing_runtime(), {}]), \
                    patch.object(M, 'docker') as docker:
                with self.assertRaisesRegex(M.AgentHouseError, 'original local credential'):
                    M.ensure_runtime()
                docker.assert_not_called()

    def test_existing_runtime_missing_owned_volume_does_not_start_or_create_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state'
            with patch.object(M, 'STATE', state), patch.object(M, 'check_context'), \
                    patch.object(M, 'inspect_owned', side_effect=[self.existing_runtime(), None]), \
                    patch.object(M, 'docker') as docker:
                with self.assertRaisesRegex(M.AgentHouseError, 'original owned data volume'):
                    M.ensure_runtime()
                docker.assert_not_called()
                self.assertFalse(state.exists())

    def test_existing_runtime_unsafe_credential_does_not_start(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state'
            state.mkdir(mode=0o700)
            (state / 'credential.json').write_text('{"password":"fixture"}')
            with patch.object(M, 'STATE', state), patch.object(M, 'check_context'), \
                    patch.object(M, 'inspect_owned', side_effect=[self.existing_runtime(), {}]), \
                    patch.object(M, 'docker') as docker:
                with self.assertRaisesRegex(M.AgentHouseError, 'private regular'):
                    M.ensure_runtime()
                docker.assert_not_called()

    def test_new_runtime_creates_private_files_before_docker(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state'
            state.mkdir(mode=0o700)
            with patch.object(M, 'STATE', state), patch.object(M, 'check_context'), \
                    patch.object(M, 'inspect_owned', side_effect=[None, None]), \
                    patch.object(M, 'docker') as docker, patch.object(M, 'Client') as client:
                client.return_value.query.return_value = '1'
                M.ensure_runtime()
                self.assertEqual((state / 'credential.json').stat().st_mode & 0o777, 0o600)
                self.assertEqual((state / 'container.env').stat().st_mode & 0o777, 0o600)
                self.assertIn('CLICKHOUSE_PASSWORD=', (state / 'container.env').read_text())
                self.assertIn(('start', M.NAME), [call.args for call in docker.call_args_list])


if __name__ == "__main__":
    unittest.main()
