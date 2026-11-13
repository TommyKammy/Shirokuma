from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from benchmarks.local_perf.capacity import Campaign
from benchmarks.local_perf.results import Writer
from benchmarks.local_perf.runner import TrinoAdapter, reconcile, run, sql_identity


class Server:
    def __init__(self, case):
        self.case, self.posts, self.gets, self.cancels = case, 0, [], 0
        self.before_post = lambda: None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send(self, value, status=200):
                data = json.dumps(value).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def page(self, state="FINISHED", data=None, next_=None, columns=True):
                p = {"id": "q1", "stats": {"state": state}}
                if columns:
                    p["columns"] = [{"name": "x", "type": "integer"}]
                if data is not None:
                    p["data"] = data
                if next_:
                    p["nextUri"] = outer.url + next_ if next_.startswith("/") else next_
                return p

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                outer.posts += 1
                outer.before_post()
                if outer.case == "lost":
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                if outer.case == "non2xx":
                    self.send({"error": "secret"}, 503)
                    return
                if outer.case == "foreign":
                    self.send(self.page("RUNNING", next_="http://192.0.2.1:1234/steal"))
                    return
                self.send(self.page("RUNNING", next_="/page/1", columns=False))

            def do_GET(self):
                outer.gets.append(self.path)
                if self.path == "/v1/query/q1":
                    self.send({"state": "RUNNING" if outer.case == "cancel-fails" else "FAILED"})
                    return
                if outer.case == "slow-header":
                    try:
                        for byte in b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}":
                            self.connection.sendall(bytes([byte]))
                            time.sleep(.015)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                elif outer.case in ("deadline", "cancel-fails"):
                    time.sleep(.12)
                    self.send(self.page("RUNNING", next_="/page/2"))
                elif outer.case == "duplicate":
                    self.send(self.page("RUNNING", [[1]], next_="/page/1"))
                elif outer.case == "incomplete":
                    self.send(self.page("RUNNING", [[1]]))
                elif outer.case == "truncated":
                    self.send(dict(self.page(data=[[1]]), truncated=True))
                elif outer.case == "error":
                    self.send(dict(self.page(data=[[1]]), error={"message": "secret"}))
                elif outer.case == "schema":
                    if self.path == "/page/1":
                        self.send(self.page("RUNNING", [], next_="/page/2"))
                    else:
                        p = self.page(data=[[1]])
                        p["columns"][0]["type"] = "varchar"
                        self.send(p)
                elif outer.case == "http-truncated":
                    self.send_response(200)
                    self.send_header("Content-Length", "999")
                    self.end_headers()
                    self.wfile.write(b'{"id":"q1"}')
                    self.close_connection = True
                elif self.path == "/page/1":
                    self.send(self.page("RUNNING", [], next_="/page/2"))
                else:
                    self.send(self.page(data=[[2 if outer.case == "mismatch" else 1]]))

            def do_DELETE(self):
                outer.cancels += 1
                self.send({}, 503 if outer.case == "cancel-fails" else 200)

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:" + str(self.http.server_port)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()


class HTTPTests(unittest.TestCase):
    def setUp(self):
        probe = socket.socket()
        try:
            probe.bind(("127.0.0.1", 0))
        except PermissionError:
            self.skipTest("sandbox denies loopback bind; run fake HTTP server suite outside restricted sandbox")
        finally:
            probe.close()
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.campaign = Campaign.create(self.base / "campaign")
        self.ref = self.base / "ref"
        (self.ref / "1-0").mkdir(parents=True)
        w = Writer(self.ref / "1-0/result.jsonl", [{"name": "x", "type": "integer"}],
                   {"dataset_id": "test", "query_id": "1", "sql_sha256": sql_identity("SELECT 1")})
        w.row([1])
        w.finish()
        self.manifest = {"format": 1, "dataset_id": "test", "queries": [{"id": "1", "sql": "SELECT 1"}]}

    def tearDown(self):
        self.tmp.cleanup()

    def run_case(self, server, name="run", **kwargs):
        return run(self.campaign, name, self.manifest, TrinoAdapter(server.url, password="do-not-record"),
                   references=self.ref, **kwargs)

    def attempt(self):
        return json.loads((self.campaign.root / "run/1-0/attempt.json").read_text())

    def test_pagination_empty_page_and_durable_intent(self):
        with Server("ok") as server:
            def before_post():
                self.assertEqual(self.attempt()["status"], "INTENT")
            server.before_post = before_post
            self.run_case(server)
            self.assertEqual(server.posts, 1)
            self.assertEqual(server.gets, ["/page/1", "/page/2"])
            self.assertEqual(self.attempt()["status"], "SUCCESS")
            self.assertEqual(self.attempt()["handle"], {"id": "q1"})
            with self.assertRaises(FileExistsError):
                self.run_case(server)
            with self.assertRaises(ValueError):
                self.campaign.cleanup("run")
        for p in (self.campaign.root / "run").rglob("*.json"):
            self.assertNotIn("do-not-record", p.read_text())

    def test_lost_post_never_retried_even_new_run(self):
        with Server("lost") as server:
            with self.assertRaises(Exception):
                self.run_case(server)
            self.assertEqual(server.posts, 1)
            self.assertEqual(self.attempt()["status"], "UNKNOWN")
            with self.assertRaises(RuntimeError):
                self.run_case(server, "new-run")
            state = reconcile(self.campaign.root / "run/1-0/attempt.json", TrinoAdapter(server.url))
            self.assertEqual(state["status"], "UNKNOWN")
            self.assertEqual(server.posts, 1)

    def test_backend_errors_truncation_duplicate_and_terminal(self):
        for case in ("non2xx", "foreign", "duplicate", "incomplete", "truncated", "error", "schema", "http-truncated", "mismatch"):
            with self.subTest(case=case), Server(case) as server:
                name = "run-" + case
                with self.assertRaises(Exception):
                    self.run_case(server, name)
                a = json.loads((self.campaign.root / name / "1-0/attempt.json").read_text())
                self.assertNotEqual(a["status"], "SUCCESS")
                if case != "non2xx":
                    self.assertGreater(server.cancels, 0)
            # A non-2xx POST has uncertain acceptance and blocks this campaign.
            self.campaign = Campaign.create(self.base / ("c-" + case))

    def test_deadline_cancels_and_failed_cancel_stops(self):
        for case in ("deadline", "cancel-fails"):
            with self.subTest(case=case), Server(case) as server:
                with self.assertRaises(Exception):
                    self.run_case(server, timeout=.05)
                a = self.attempt()
                self.assertEqual(a["status"], "UNKNOWN" if case == "cancel-fails" else "FAILED")
                self.assertGreater(server.cancels, 0)
                self.assertLess(a["elapsed_s"], 2)
            self.campaign = Campaign.create(self.base / ("c-" + case))

    def test_validation_deadline_cancellation(self):
        def fail(*args, **kwargs):
            raise TimeoutError("validation deadline")
        with Server("ok") as server, patch("benchmarks.local_perf.runner.compare", side_effect=fail):
            with self.assertRaises(TimeoutError):
                self.run_case(server)
            self.assertEqual(self.attempt()["status"], "FAILED")
            self.assertEqual(server.cancels, 1)

    def test_absolute_slow_header_deadline(self):
        with Server("slow-header") as server:
            adapter = TrinoAdapter(server.url)
            start = time.monotonic()
            with self.assertRaises(Exception):
                adapter.request("GET", server.url + "/page/1", start + .1)
            self.assertLess(time.monotonic() - start, .4)

    def test_incomplete_content_length(self):
        with Server("http-truncated") as server:
            with self.assertRaisesRegex(ValueError, "truncated HTTP body"):
                TrinoAdapter(server.url).request("GET", server.url + "/page/1", time.monotonic() + 1)

    def test_origin_credentials_guard(self):
        for url in ("https://example.com", "http://127.0.0.1.evil.test", "http://user:pass@127.0.0.1"):
            with self.assertRaises(ValueError):
                TrinoAdapter(url)
        a = TrinoAdapter("http://127.0.0.1:1234")
        with self.assertRaises(ValueError):
            a.safe("http://127.0.0.1:4321/page")


if __name__ == "__main__":
    unittest.main()
