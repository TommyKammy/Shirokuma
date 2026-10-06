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

    def test_context_must_be_dedicated_socket(self):
        config = [{"Endpoints": {"docker": {"Host": "unix:///var/run/docker.sock"}}}]
        with patch.object(M, "docker", return_value=json.dumps(config)) as docker:
            with self.assertRaisesRegex(M.AgentHouseError, "dedicated"):
                M.check_context()
            self.assertEqual(docker.call_count, 1)

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
                           "NanoCpus": 2_000_000_000, "Memory": 4 * 1024**3},
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
                    raise
                docker.assert_called_once_with("start", M.NAME)
                self.assertIs(result, client.return_value)

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


if __name__ == "__main__":
    unittest.main()
