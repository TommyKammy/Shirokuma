from __future__ import annotations

import importlib.util
import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location("local_agenthouse", Path(__file__).resolve().parents[1] / "tools/local_experiment/agenthouse/manage.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class AgentHouseTests(unittest.TestCase):
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
        config = [{"Endpoints": {"docker": {"Host": expected}}}]
        with patch.object(M, "docker", side_effect=[json.dumps(config), 'aarch64']) as docker:
            M.check_context()
            self.assertEqual(docker.call_count, 2)

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
            report = Path(directory) / 'report.jsonl'
            report.write_text('{"other":"value"}\n')
            client = Mock()
            with patch.object(M, 'event', return_value={'event_id': 'fixture'}), \
                    patch.object(M, 'insert') as inserted, patch.object(M, 'export', return_value=report):
                for reply in ('{}', '{"data":null}', '{"data":[{"n":1,"status":"success"}]}'):
                    client.query.return_value = reply
                    with self.subTest(reply=reply), self.assertRaises(M.AgentHouseError):
                        M.smoke(client)
                self.assertEqual(inserted.call_count, 6)  # two writes per attempt; no hidden retry

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
            "HostConfig": {"PortBindings": {"8123/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18123"}]},
                           "NanoCpus": 2_000_000_000, "Memory": 4 * 1024**3,
                           "NetworkMode": "default", "Privileged": False,
                           "PidMode": "", "PublishAllPorts": False,
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
