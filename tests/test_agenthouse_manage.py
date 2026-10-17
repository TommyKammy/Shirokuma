"""Compatibility checks for the resident AgentHouse client and retained state."""
import base64
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/local_experiment"))
from agenthouse import manage as m


@contextmanager
def clickhouse_reply(body, *, chunked=False, status=200, headers=None):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.server.sql = self.rfile.read(int(self.headers["Content-Length"])).decode()
            self.server.auth = self.headers["Authorization"]
            self.send_response(status)
            self.send_header("Content-Type", "application/x-ndjson; charset=UTF-8")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("Transfer-Encoding" if chunked else "Content-Length",
                             "chunked" if chunked else str(len(body)))
            self.end_headers()
            if chunked:
                for chunk in (body[:5], body[5:]):
                    if chunk:
                        self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                self.wfile.write(b"0\r\n\r\n")
            else:
                self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with patch.object(m, "URL", f"http://127.0.0.1:{server.server_port}"):
            yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class AgentHouseTests(unittest.TestCase):
    def client(self):
        return m.Client(credential_bytes=b'{"password":"saved-secret"}')

    def test_standard_http_json_responses(self):
        for chunked in (False, True):
            for body, expected in ((b'{"n":1}\n{"n":2}\n', [{"n": 1}, {"n": 2}]),
                                   (b'{"n":1}\n', [{"n": 1}]),
                                   (b'', [])):
                with self.subTest(chunked=chunked, body=body), clickhouse_reply(body, chunked=chunked) as server:
                    self.assertEqual(self.client().rows("SELECT number AS n FROM numbers(2);"), expected)
                    self.assertTrue(server.sql.endswith("FORMAT JSONEachRow"))
                    self.assertEqual(server.auth, "Basic " + base64.b64encode(
                        f"{m.USER}:saved-secret".encode()).decode())
        with clickhouse_reply(b'{"data":[{"n":3}]}') as server:
            self.assertEqual(self.client().rows("SELECT 3 AS n FORMAT JSON"), [{"n": 3}])
            self.assertEqual(server.sql, "SELECT 3 AS n FORMAT JSON")

    def test_query_errors_are_not_acknowledged(self):
        cases = [(500, {}, b'query failed'),
                 (200, {"X-ClickHouse-Exception-Code": "60"}, b''),
                 (200, {}, b'Code: 60. DB::Exception: unknown table\n'),
                 (302, {"Location": "http://127.0.0.1:1/"}, b'')]
        for status, headers, body in cases:
            with self.subTest(status=status, headers=headers), clickhouse_reply(body, status=status, headers=headers):
                with self.assertRaises(m.AgentHouseError):
                    self.client().query("SELECT 1")

    def test_saved_credentials_are_read_without_rewriting(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(m, "STATE", Path(directory)):
            state = Path(directory) / "state.json"
            saved = b'{"password":"original-password", "extra":"retained"}\n'
            state.write_bytes(saved)
            expected = m.Client(credential_bytes=saved).auth
            self.assertEqual(m.Client.from_state().auth, expected)
            self.assertEqual(m.Client.from_state(state).auth, expected)
            self.assertEqual(m.Client.from_state(directory).auth, expected)
            self.assertEqual(state.read_bytes(), saved)
            legacy = Path(directory) / "credential.json"
            legacy.write_bytes(b'{"password":"legacy-password"}')
            self.assertEqual(m.Client().auth, m.Client(credential_bytes=legacy.read_bytes()).auth)

    def test_existing_runtime_preserves_credentials_and_container(self):
        container = {"Config": {"Image": m.IMAGE}, "State": {"Running": True},
                     "HostConfig": {"PortBindings": {
                         "8123/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18123"}]}},
                     "Mounts": [{"Type": "volume", "Name": m.VOLUME,
                                 "Destination": "/var/lib/clickhouse", "RW": True}]}
        with tempfile.TemporaryDirectory() as directory, patch.object(m, "STATE", Path(directory)), \
                patch.object(m, "check_context"), patch.object(m, "inspect_owned", side_effect=[container, {"Name": m.VOLUME}]), \
                patch.object(m, "docker") as docker, patch.object(m.Client, "query", return_value="1\n"):
            state = Path(directory) / "state.json"
            saved = b'{"password":"original-password"}\n'
            state.write_bytes(saved)
            m.ensure_runtime()
            self.assertEqual(state.read_bytes(), saved)
            self.assertEqual(list(Path(directory).iterdir()), [state])
            docker.assert_not_called()

    def test_event_calling_conventions_and_insert(self):
        resident = m.event(action="warehouse.query", target="trino-warehouse", status="failed",
                           duration_ms=12, trace_id="trace", prompt_tokens=42)
        positional = m.event("warehouse.query", "failed", 12, session_id="trace")
        keyword = m.event("warehouse.query", status="failed", duration_ms=12, session_id="trace")
        for record in (resident, positional, keyword):
            self.assertEqual(record["status"], "failed")
            self.assertEqual(record["duration_ms"], 12)
            self.assertEqual(record["trace_id"], "trace")
        client = Mock()
        m.insert(client, resident)
        self.assertEqual(json.loads(client.query.call_args.args[0].split("\n", 1)[1]), resident)
        with self.assertRaises(m.AgentHouseError):
            m.event("query", raw_prompt="private")

    def test_smoke_checks_retry_and_exports_rows(self):
        client = Mock()
        client.rows.return_value = [{"n": "1", "status": "success"}]
        inserted = []

        def query(sql):
            if sql.startswith("INSERT"):
                inserted.append(json.loads(sql.split("\n", 1)[1]))
                return ""
            if "JSONEachRow" in sql:
                return json.dumps(inserted[0]) + "\n"
            return "26.8.5.13\n"

        client.query.side_effect = query
        with tempfile.TemporaryDirectory() as directory, patch.object(m, "STATE", Path(directory)):
            result = m.smoke(client)
            self.assertEqual(result["result"], "passed")
            self.assertEqual(inserted[0], inserted[1])
            path = Path(result["export"])
            self.assertEqual(json.loads(path.read_text()), inserted[0])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            client.rows.return_value = [{"n": "2", "status": "success"}]
            with self.assertRaises(m.AgentHouseError):
                m.smoke(client)


if __name__ == "__main__":
    unittest.main()
