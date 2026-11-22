"""Lifecycle coverage even when sandbox denies sockets; HTTP suite remains separate."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmarks.local_perf.capacity import Campaign
from benchmarks.local_perf.results import Writer
from benchmarks.local_perf.runner import TrinoAdapter, reconcile, run, sql_identity
from tests.local_perf_host import setUpModule, tearDownModule  # noqa: F401


class ScriptedTrino(TrinoAdapter):
    def __init__(self, case, before=lambda: None):
        super().__init__("http://127.0.0.1:1234", password="secret-never-record")
        self.case, self.before = case, before
        self.posts = self.deletes = 0
        self.pages = []

    def request(self, method, url, deadline, body=None):
        self.safe(url)
        if method == "DELETE":
            self.deletes += 1
            if self.case == "cancel-fails":
                raise OSError("cancel failed")
            return {}
        if "/v1/query/" in url:
            return {"state": "FAILED"}
        if method == "POST":
            self.posts += 1
            self.before()
            if self.case == "lost":
                raise OSError("POST accepted; response dropped")
            return {"id": "q1", "stats": {"state": "RUNNING"}, "nextUri": self.url + "/page/1"}
        self.pages.append(url)
        p = {"id": "q1", "columns": [{"name": "x", "type": "integer"}], "stats": {"state": "FINISHED"}, "data": [[1]]}
        if self.case == "duplicate":
            p.update(nextUri=self.url + "/page/1", stats={"state": "RUNNING"})
        elif self.case == "incomplete":
            p["stats"]["state"] = "RUNNING"
        elif self.case == "unknown":
            p["stats"]["state"] = "UNKNOWN"
        elif self.case == "canceled":
            p["stats"]["state"] = "CANCELED"
        elif self.case == "foreign":
            p["nextUri"] = "http://127.0.0.1:5678/page"
        elif self.case == "truncated":
            p["truncated"] = True
        elif self.case == "error":
            p["error"] = {"message": "secret-never-record"}
        elif self.case in ("deadline", "cancel-fails"):
            raise TimeoutError("fetch deadline")
        elif self.case == "schema":
            if url.endswith("/1"):
                p.update(data=[], nextUri=self.url + "/page/2", stats={"state": "RUNNING"})
            else:
                p["columns"][0]["type"] = "varchar"
        elif self.case == "ok" and url.endswith("/1"):
            p.update(data=[], nextUri=self.url + "/page/2", stats={"state": "RUNNING"})
        return p


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.campaign = Campaign.create(self.root / "campaign")
        self.ref = self.root / "reference"
        (self.ref / "1-0").mkdir(parents=True)
        w = Writer(self.ref / "1-0/result.jsonl", [{"name": "x", "type": "integer"}], {"dataset_id": "d", "query_id": "1", "sql_sha256": sql_identity("SELECT 1")})
        w.row([1])
        w.finish()
        self.manifest = {"format": 1, "dataset_id": "d", "queries": [{"id": "1", "sql": "SELECT 1"}]}

    def tearDown(self):
        self.tmp.cleanup()

    def attempt(self, name="run"):
        return json.loads((self.campaign.root / name / "1-0/attempt.json").read_text())

    def test_success_durable_intent_empty_page_and_no_overwrite(self):
        a = ScriptedTrino("ok", lambda: self.assertEqual(self.attempt()["status"], "INTENT"))
        run(self.campaign, "run", self.manifest, a, references=self.ref)
        self.assertEqual(a.posts, 1)
        self.assertEqual(len(a.pages), 2)
        self.assertEqual(self.attempt()["status"], "SUCCESS")
        with self.assertRaises(FileExistsError):
            run(self.campaign, "run", self.manifest, a, references=self.ref)
        with self.assertRaises(ValueError):
            self.campaign.cleanup("run")

    def test_lost_post_denominator_and_no_resubmit(self):
        a = ScriptedTrino("lost")
        with self.assertRaises(OSError):
            run(self.campaign, "run", self.manifest, a, references=self.ref)
        self.assertEqual(self.attempt()["status"], "UNKNOWN")
        self.assertEqual(json.loads((self.campaign.root / "run/run.json").read_text())["attempts"][0]["status"], "UNKNOWN")
        with self.assertRaises(RuntimeError):
            run(self.campaign, "new", self.manifest, a, references=self.ref)
        result = reconcile(self.campaign.root / "run/1-0/attempt.json", a)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(a.posts, 1)

    def test_failures_stop_and_cancel(self):
        self.manifest["queries"].append({"id": "2", "sql": "SELECT 1"})
        (self.ref / "2-0").mkdir()
        text = (self.ref / "1-0/result.jsonl").read_text().replace('"query_id":"1"', '"query_id":"2"')
        (self.ref / "2-0/result.jsonl").write_text(text)
        for case in ("duplicate", "incomplete", "unknown", "canceled", "foreign", "truncated", "error", "deadline", "schema", "cancel-fails"):
            with self.subTest(case=case):
                a = ScriptedTrino(case)
                with self.assertRaises(Exception):
                    run(self.campaign, case, self.manifest, a, references=self.ref)
                self.assertEqual(a.posts, 1)
                self.assertEqual(a.deletes, 1)
                self.assertEqual(self.attempt(case)["status"], "UNKNOWN" if case == "cancel-fails" else "FAILED")
                self.assertNotIn("secret-never-record", (self.campaign.root / case / "1-0/attempt.json").read_text())

    def test_validation_failure_cancels_and_mismatch_remains_failed(self):
        for error in (TimeoutError, ValueError, RuntimeError):
            with patch("benchmarks.local_perf.runner.compare", side_effect=error("validation/capacity failure")):
                a = ScriptedTrino("ok")
                with self.assertRaises(error):
                    run(self.campaign, error.__name__, self.manifest, a, references=self.ref)
                self.assertEqual(a.deletes, 1)
                self.assertEqual(self.attempt(error.__name__)["status"], "FAILED")

    def test_reconcile_checks_origin(self):
        a = ScriptedTrino("lost")
        with self.assertRaises(OSError):
            run(self.campaign, "run", self.manifest, a, references=self.ref)
        with self.assertRaises(ValueError):
            reconcile(self.campaign.root / "run/1-0/attempt.json", TrinoAdapter("http://127.0.0.1:5678"))

    def test_origin_guard(self):
        for url in ("https://example.com", "http://user:pass@127.0.0.1", "http://127.0.0.1.evil.test"):
            with self.assertRaises(ValueError):
                TrinoAdapter(url)


if __name__ == "__main__":
    unittest.main()
