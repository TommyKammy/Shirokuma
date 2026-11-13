"""Public protocol, crash recovery, explicit authority and verified transport tests."""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import ssl
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from benchmarks.local_perf import runner
from benchmarks.local_perf.capacity import Campaign
from benchmarks.local_perf.public import PublicAdapter, PublicError, client_auth_path, private_auth
from benchmarks.local_perf.results import Reader, Writer


CONTEXT = dict(database_id="db", schema_id="schema", warehouse_id="LOCAL", sql_catalog="catalog",
               sql_schema="sandbox", selected_role="data_engineer", binding_revision=1,
               generation_id="generation", grant_revision=None)
MANIFEST = dict(format=1, dataset_id="portable-inline", queries=[dict(id="one", sql="SELECT 1 AS x", ordered=True)])


class ScriptedPublic(PublicAdapter):
    def __init__(self, case="ok", **kwargs):
        super().__init__("http://127.0.0.1:8123", "account", "never-record-password", "data_engineer", CONTEXT, **kwargs)
        self.case, self.calls, self.posts, self.cancelled = case, [], 0, False
        self.before = lambda: None

    def page(self, state="RUNNING", number=1):
        return dict(id="query", backend_query_id="backend", **copy.deepcopy(self.binding),
                    columns=[dict(name="x", type="integer")], data=[], state=state, stats={"state": state},
                    result_page_id="receipt" + str(number), next_uri="/api/v1/queries/query/pages/token" + str(number))

    def inspect(self):
        p = self.page("CANCELED" if self.cancelled else "RUNNING")
        for key in ("columns", "data", "result_page_id", "next_uri", "stats"):
            p.pop(key)
        p.update(sql=self.sql, mode=self.mode)
        if self.case == "lost-sql":
            p["sql"] = "SELECT 999 AS x"
        elif self.case == "lost-mode":
            p["mode"] = "sandbox-write"
        return p

    def request(self, method, url, deadline, body=None):
        self.calls.append((method, url))
        if method == "POST" and url.endswith("/cancel"):
            if self.case == "cancel-fails":
                raise OSError("cancel unavailable")
            self.cancelled = True
            return {}
        if method == "POST":
            self.posts += 1
            self.before()
            value = json.loads(body)
            assert set(value) == {"sql", "request_id", "worksheet_id", "context", "mode", "max_rows"}
            assert set(value["context"]) == {"database_id", "schema_id", "warehouse_id", "binding_revision", "generation_id"}
            assert value["mode"] == "read-only" and value["max_rows"] == self.max_rows
            assert value["request_id"] == self.binding["request_id"]
            if self.case.startswith("lost"):
                raise OSError("lost submit")
            p = self.page()
            if self.case == "cold":
                p.update(backend_query_id=None, columns=[], data=[], state="QUEUED", stats={"state": "QUEUED"})
                p.pop("result_page_id")
            return p
        if "?database_id=" in url:
            count = 0 if self.case == "lost0" else 2 if self.case == "lost2" else 1
            return {"queries": [self.inspect() for _ in range(count)]}
        if url.endswith("/query"):
            return self.inspect()
        p = self.page("FINISHED", 2)
        p.update(data=[[1]], next_uri=None)
        if self.case in ("truncated", "error"):
            p[self.case] = True
        elif self.case in ("UNKNOWN", "FAILED", "CANCELED"):
            p["state"] = self.case
        elif self.case in ("request_id", "worksheet_id", "id", "backend_query_id"):
            p[self.case] = "changed"
        elif self.case == "context":
            p["context"]["grant_revision"] = 2
        elif self.case == "schema":
            p["columns"][0]["name"] = "other"
        elif self.case == "duplicate":
            p["result_page_id"] = "receipt1"
        elif self.case in ("foreign", "crossquery", "fragment", "repeat"):
            p["state"] = "RUNNING"
            p["next_uri"] = {"foreign": "http://127.0.0.1:9999/api/v1/queries/query/pages/token",
                             "crossquery": "/api/v1/queries/other/pages/token",
                             "fragment": "/api/v1/queries/query/pages/token#fragment",
                             "repeat": "/api/v1/queries/query/pages/token1"}[self.case]
        elif self.case == "cancel-fails":
            p["state"] = "UNKNOWN"
        p["stats"] = {"state": p["state"]}
        return p


class PublicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.c = Campaign.create(self.root / "campaign")
        self.ref = self.root / "ref"
        (self.ref / "one-0").mkdir(parents=True)
        w = Writer(self.ref / "one-0/result.jsonl", [dict(name="x", type="integer")],
                   runner.result_metadata(MANIFEST, MANIFEST["queries"][0]))
        w.row([1])
        w.finish()

    def tearDown(self):
        self.tmp.cleanup()

    def run_adapter(self, a, name="run"):
        return runner.run(self.c, name, MANIFEST, a, references=self.ref)

    def receipt(self, name="run"):
        return self.c.root / name / "one-0/attempt.json"

    def test_normal_empty_and_cold_pending_durable_before_dispatch(self):
        for case in ("ok", "cold"):
            a = ScriptedPublic(case)
            def before():
                intent = json.loads(self.receipt(case).read_text())
                self.assertEqual(intent["status"], "INTENT")
                self.assertEqual(intent["dispatch"], a.binding)
                self.assertEqual(intent["sql"], MANIFEST["queries"][0]["sql"])
                self.assertEqual(intent["mode"], "read-only")
                self.assertIsNone(intent["handle"])
                self.assertTrue((self.c.root / case / "queries.json").exists())
            a.before = before
            self.run_adapter(a, case)
            intent = json.loads(self.receipt(case).read_text())
            self.assertEqual(intent["status"], "SUCCESS")
            self.assertEqual(intent["native_columns"], [dict(name="x", type="integer")])
            report = json.loads((self.receipt(case).parent.parent / "run.json").read_text())
            self.assertTrue(report["public_api_qualified"])
            self.assertEqual(report["planned_attempts"], 1)
            self.assertEqual(a.posts, 1)
            for p in (self.c.root / case).rglob("*"):
                if p.is_file():
                    self.assertNotIn("never-record-password", p.read_text())
                    self.assertNotIn("Authorization", p.read_text())
            reader = Reader(self.receipt(case).parent / "result.jsonl")
            self.assertEqual(reader.header["metadata"], runner.result_metadata(MANIFEST, MANIFEST["queries"][0]))
            self.assertEqual(len(list(reader)), 1)

    def test_page_contract_errors_cancel_and_never_qualify(self):
        for case in ("truncated", "error", "UNKNOWN", "FAILED", "CANCELED", "context", "request_id", "worksheet_id",
                     "id", "backend_query_id", "schema", "duplicate", "foreign", "crossquery", "fragment", "repeat"):
            with self.subTest(case=case):
                a = ScriptedPublic(case)
                with self.assertRaises(ValueError):
                    self.run_adapter(a, case)
                intent = json.loads(self.receipt(case).read_text())
                self.assertEqual(intent["status"], "FAILED")
                self.assertTrue(a.cancelled)
                if case == "truncated":
                    self.assertEqual(intent["error_code"], "PUBLIC_TRUNCATED")
                self.assertFalse(json.loads((self.c.root / case / "run.json").read_text())["public_api_qualified"])

    def test_backend_receipted_pages_can_wait_for_schema(self):
        a = ScriptedPublic()
        a.prepare_attempt(dict(request_id="request", adapter_identity=a.identity(), sql="SELECT 1"))
        first, second, final = a.page("QUEUED", 1), a.page("RUNNING", 2), a.page("FINISHED", 3)
        for p in (first, second):
            p["columns"] = []
        final.update(data=[[1]], next_uri=None)
        out = self.root / "waiting.jsonl"
        pages = iter((first, second, final))
        def request(*args):
            self.assertFalse(out.exists())  # No writer for either pre-schema page.
            return next(pages)
        with patch.object(a, "request", side_effect=request):
            a.execute("SELECT 1", out, {}, time.monotonic()+1, lambda h: None, lambda: None)
        self.assertEqual(list(Reader(out)), [[["i", "1"]]])

    def test_pre_schema_receipts_data_and_schema_disappearance_rejected(self):
        for case in ("duplicate", "missing_receipt", "no_next", "data", "terminal", "disappeared"):
            with self.subTest(case=case):
                a = ScriptedPublic()
                a.prepare_attempt(dict(request_id="request", adapter_identity=a.identity(), sql="SELECT 1"))
                first, final = a.page("RUNNING", 1), a.page("FINISHED", 2)
                first["columns"] = []
                final.update(data=[[1]], next_uri=None)
                if case == "duplicate":
                    final["result_page_id"] = first["result_page_id"]
                elif case == "missing_receipt":
                    first.pop("result_page_id")
                elif case == "no_next":
                    first["next_uri"] = None
                elif case == "data":
                    first["data"] = [[1]]
                elif case == "terminal":
                    final.update(columns=[], data=[])
                else:
                    first["columns"] = final["columns"]
                    final.update(columns=[], data=[])
                with patch.object(a, "request", side_effect=[first, final]), self.assertRaises(PublicError):
                    a.execute("SELECT 1", self.root / (case+".jsonl"), {}, time.monotonic()+1, lambda h: None, lambda: None)

    def test_public_stats_must_match_mapped_state_even_when_truncated(self):
        for state, stats in (("RUNNING", None), ("RUNNING", []), ("RUNNING", {}),
                             ("RUNNING", {"state": "PLANNING"}), ("PLANNING", {"state": "PLANNING"}),
                             ("FINISHED", {"state": "RUNNING"}), (1, {"state": 1}),
                             ("CANCELED", {"state": "RUNNING"})):
            with self.subTest(state=state, stats=stats):
                a = ScriptedPublic()
                a.prepare_attempt(dict(request_id="request", adapter_identity=a.identity(), sql="SELECT 1"))
                p = a.page()
                p.update(state=state, stats=stats, truncated=state == "CANCELED")
                with patch.object(a, "request", return_value=p), self.assertRaisesRegex(PublicError, "PUBLIC_STATE"):
                    a.execute("SELECT 1", self.root / "invalid.jsonl", {}, time.monotonic()+1, lambda h: None, lambda: None)
        for state in ("CANCELED", "UNKNOWN", "FINISHED"):
            a = ScriptedPublic()
            a.prepare_attempt(dict(request_id="request", adapter_identity=a.identity(), sql="SELECT 1"))
            p = a.page(state)
            p.update(truncated=True, next_uri=None)
            with patch.object(a, "request", return_value=p), self.assertRaisesRegex(PublicError, "PUBLIC_TRUNCATED"):
                a.execute("SELECT 1", self.root / "invalid.jsonl", {}, time.monotonic()+1, lambda h: None, lambda: None)

    def test_example_manifests_are_portable_select_only_inputs(self):
        examples = Path(__file__).resolve().parents[1] / "benchmarks/local_perf/examples"
        for filename in ("connection-qualification.json", "overflow-qualification.json"):
            manifest = json.loads((examples / filename).read_text())
            self.assertEqual(set(manifest), {"format", "dataset_id", "queries"})
            self.assertEqual(manifest["format"], 1)
            self.assertTrue(manifest["queries"])
            for q in manifest["queries"]:
                self.assertEqual(set(q), {"id", "sql", "ordered"})
                runner.select_only(q["sql"])  # Static guard only; no engine or extension loading.
        timestamp_sql = [q["sql"] for q in json.loads((examples / "connection-qualification.json").read_text())["queries"]
                         if q["id"] == "timestamp"][0]
        self.assertIn("12:34:56.123456", timestamp_sql)
        self.assertIn("TIMESTAMP(6)", timestamp_sql)

    def test_direct_datetime_capability_persisted_and_reconcile_cannot_change(self):
        a = runner.TrinoAdapter("http://127.0.0.1:8123")
        self.assertEqual(a.headers["X-Trino-Client-Capabilities"], "PARAMETRIC_DATETIME")
        with patch.object(a, "execute", side_effect=OSError()), patch.object(a, "cancel_reconcile", return_value="UNKNOWN"):
            with self.assertRaises(OSError):
                self.run_adapter(a)
        intent = json.loads(self.receipt().read_text())
        self.assertEqual(intent["adapter_identity"]["client_capabilities"], "PARAMETRIC_DATETIME")
        for capability in (None, "OTHER"):
            a.headers["X-Trino-Client-Capabilities"] = capability
            with patch.object(a, "request") as request, self.assertRaises(ValueError):
                runner.reconcile(self.receipt(), a)
            request.assert_not_called()

    def test_lost_submit_unique_history_and_no_resubmit(self):
        a = ScriptedPublic("lost1")
        with self.assertRaises(OSError):
            self.run_adapter(a)
        intent = json.loads(self.receipt().read_text())
        self.assertEqual(intent["handle"]["id"], "query")
        self.assertEqual(intent["status"], "FAILED")
        self.assertTrue(a.cancelled)
        self.assertEqual(a.posts, 1)
        self.assertFalse((self.receipt().parent / "result.jsonl").exists())

    def test_lost_submit_zero_multiple_and_cancel_failure_block(self):
        for case in ("lost0", "lost2", "lost-sql", "lost-mode", "cancel-fails"):
            with self.subTest(case=case):
                a = ScriptedPublic(case)
                with self.assertRaises(Exception):
                    self.run_adapter(a)
                self.assertEqual(json.loads(self.receipt().read_text())["status"], "UNKNOWN")
                with self.assertRaisesRegex(RuntimeError, "unreconciled"):
                    self.run_adapter(a, "next")
                runner.reconcile(self.receipt(), a)
                self.assertEqual(a.posts, 1)
            self.c = Campaign.create(self.root / case)

    def crash(self, state):
        a = ScriptedPublic("lost0" if state == "INTENT" else "cancel-fails")
        snapshots = {}
        write = runner.write_json
        def capture(path, value):
            write(path, value)
            if path == self.receipt() and value["status"] == state and not snapshots:
                for p in (path, path.parent.parent / "run.json", path.parent.parent / "queries.json"):
                    snapshots[p] = p.read_bytes()
        with patch.object(runner, "write_json", side_effect=capture):
            with self.assertRaises(Exception):
                self.run_adapter(a)
        self.assertEqual(len(snapshots), 3)
        for p, value in snapshots.items():
            p.write_bytes(value)
        return a

    def test_crash_intent_history_recovery_and_terminal_retention(self):
        for state in ("INTENT", "RUNNING"):
            with self.subTest(state=state):
                self.crash(state)
                original = self.receipt().read_bytes()
                a = ScriptedPublic("lost1")
                result = runner.reconcile(self.receipt(), a)
                self.assertEqual(result["status"], "FAILED")
                self.assertEqual((result.get("recovered_handle") or result["handle"])["id"], "query")
                self.assertEqual(a.posts, 0)
                evidence = self.receipt().parent / "reconciliation.json"
                retained = evidence.read_bytes()
                with patch.object(a, "request", side_effect=AssertionError("no repeated HTTP")):
                    self.assertEqual(runner.reconcile(self.receipt(), a), result)
                self.assertEqual(evidence.read_bytes(), retained)
                self.assertEqual(self.receipt().read_bytes(), original)
                self.run_adapter(ScriptedPublic(), "next")
            self.c = Campaign.create(self.root / state)

    def test_matching_terminal_inspect_needs_no_cancel_or_live_pin(self):
        for status in ("INTENT", "RUNNING"):
            for terminal in ("FINISHED", "FAILED", "CANCELED"):
                with self.subTest(status=status, terminal=terminal):
                    self.crash(status)
                    original = self.receipt().read_bytes()
                    a = ScriptedPublic("cancel-fails")
                    inspect = a.inspect
                    def terminal_inspect():
                        return dict(inspect(), state=terminal)
                    with patch.object(a, "inspect", side_effect=terminal_inspect):
                        result = runner.reconcile(self.receipt(), a)
                    self.assertEqual(result["status"], "FAILED")  # Never fetched full result values.
                    self.assertEqual(result["reconciliation"], terminal)
                    self.assertFalse(any(method == "POST" for method, url in a.calls))
                    self.assertEqual(self.receipt().read_bytes(), original)
                    retained = (self.receipt().parent / "reconciliation.json").read_bytes()
                    with patch.object(a, "request", side_effect=AssertionError("retained proof needs no HTTP")):
                        self.assertEqual(runner.reconcile(self.receipt(), a), result)
                    self.assertEqual((self.receipt().parent / "reconciliation.json").read_bytes(), retained)
                self.c = Campaign.create(self.root / (status + "-" + terminal))

    def test_inspect_sql_mode_mismatch_cannot_cancel_or_resolve_known_handle(self):
        self.crash("RUNNING")
        for key, wrong in (("sql", "SELECT 999 AS x"), ("mode", "sandbox-write"),
                           ("sql", None), ("mode", None)):
            for state in ("RUNNING", "FINISHED"):
                with self.subTest(key=key, wrong=wrong, state=state):
                    a = ScriptedPublic()
                    inspect = a.inspect
                    def changed_inspect():
                        page = dict(inspect(), state=state)
                        page[key] = wrong
                        return page
                    with patch.object(a, "inspect", side_effect=changed_inspect):
                        result = runner.reconcile(self.receipt(), a)
                    self.assertEqual(result["status"], "UNKNOWN")
                    self.assertFalse(any(method == "POST" for method, url in a.calls))

    def test_history_match_cannot_hide_subsequent_inspect_sql_mode_change(self):
        self.crash("INTENT")
        for key, wrong in (("sql", "SELECT 999 AS x"), ("mode", "sandbox-write")):
            a = ScriptedPublic()
            request = a.request
            def changed_inspect(method, url, *args):
                page = request(method, url, *args)
                if url.endswith("/query"):
                    page[key] = wrong
                return page
            with patch.object(a, "request", side_effect=changed_inspect):
                self.assertEqual(runner.reconcile(self.receipt(), a)["status"], "UNKNOWN")
            self.assertFalse(any(method == "POST" for method, url in a.calls))
            # Start again from the original no-handle intent, exercising history each time.
            (self.receipt().parent / "reconciliation.json").unlink()

    def test_every_post_cancel_inspect_must_preserve_sql_and_mode(self):
        self.crash("RUNNING")
        for key, wrong in (("sql", "SELECT 999 AS x"), ("mode", "sandbox-write")):
            a = ScriptedPublic()
            inspect = a.inspect
            def changed_after_cancel():
                page = inspect()
                if a.cancelled:
                    page[key] = wrong
                return page
            with patch.object(a, "inspect", side_effect=changed_after_cancel):
                result = runner.reconcile(self.receipt(), a)
            self.assertTrue(a.cancelled)
            self.assertEqual(result["status"], "UNKNOWN")

    def test_recovered_handle_durable_before_cancel_and_retry(self):
        self.crash("INTENT")
        a = ScriptedPublic("lost1")
        request = a.request
        def crash_cancel(method, url, *args):
            if url.endswith("/cancel"):
                raise KeyboardInterrupt()
            return request(method, url, *args)
        with patch.object(a, "request", side_effect=crash_cancel), self.assertRaises(KeyboardInterrupt):
            runner.reconcile(self.receipt(), a)
        saved = json.loads((self.receipt().parent / "reconciliation.json").read_text())
        self.assertEqual(saved["recovered_handle"]["id"], "query")
        a.case = "lost0"
        self.assertEqual(runner.reconcile(self.receipt(), a)["status"], "FAILED")
        self.assertEqual(a.posts, 0)

    def test_reconcile_rejects_account_role_context_origin_and_unowned_slot(self):
        self.crash("INTENT")
        for field, value in (("account", "other"), ("role", "admin"), ("origin", ("http", "127.0.0.1", 9999)),
                             ("context", dict(CONTEXT, generation_id="other"))):
            a = ScriptedPublic()
            setattr(a, field, value)
            with patch.object(a, "request") as request, self.assertRaises(ValueError):
                runner.reconcile(self.receipt(), a)
            request.assert_not_called()
        wrong = self.receipt().parent.parent / "other"
        wrong.mkdir()
        (wrong / "attempt.json").write_bytes(self.receipt().read_bytes())
        with self.assertRaisesRegex(ValueError, "unowned"):
            runner.reconcile(wrong / "attempt.json", ScriptedPublic())

    def test_public_cancel_interface_never_uses_direct_trino_route(self):
        a = ScriptedPublic()
        self.assertEqual(a.cancel_reconcile({"id": "query"}), "UNKNOWN")
        a.prepare_attempt(dict(request_id="request", adapter_identity=a.identity(), sql="SELECT 1"))
        self.assertEqual(a.cancel_reconcile({"id": "query", "backend_query_id": "backend"}), "CANCELED")
        self.assertTrue(all("/v1/query/" not in url for method, url in a.calls))
        self.assertIn(("POST", a.url + "/api/v1/queries/query/cancel"), a.calls)

    def test_mismatched_public_evidence_cannot_clear_unknown(self):
        self.crash("INTENT")
        intent = json.loads(self.receipt().read_text())
        evidence = self.receipt().parent / "reconciliation.json"
        for key, value in (("dispatch", dict(intent["dispatch"], worksheet_id="other")),
                           ("adapter_identity", dict(intent["adapter_identity"], selected_role="admin")),
                           ("request_id", "other")):
            result = dict(intent, status="FAILED", reconciliation="FINISHED",
                          recovered_handle={"id": "query", "backend_query_id": "backend"})
            result[key] = value
            evidence.write_text(json.dumps(result))
            with self.assertRaisesRegex(RuntimeError, "unreconciled"):
                self.run_adapter(ScriptedPublic(), "blocked")
            a = ScriptedPublic("lost0")
            self.assertEqual(runner.reconcile(self.receipt(), a)["status"], "UNKNOWN")
            self.assertEqual(a.posts, 0)

    def test_unknown_stays_in_planned_denominator_with_undispatched_tail(self):
        manifest = copy.deepcopy(MANIFEST)
        manifest["queries"].append(dict(id="two", sql="SELECT 2 AS x"))
        a = ScriptedPublic("lost0")
        with self.assertRaises(OSError):
            runner.run(self.c, "run", manifest, a, reference_label="test only", repetitions=2)
        report = json.loads((self.c.root / "run/run.json").read_text())
        self.assertEqual(report["planned_attempts"], 4)
        self.assertEqual(report["not_run_attempts"], 3)
        self.assertEqual(report["attempts"][0]["status"], "UNKNOWN")
        self.assertFalse(report["public_api_qualified"])
        self.assertEqual(a.posts, 1)

    def test_pending_does_not_create_writer_and_context_types_are_exact(self):
        a = ScriptedPublic("cold")
        a.prepare_attempt(dict(request_id="request", adapter_identity=a.identity(), sql="SELECT 1"))
        request = a.request
        def fail_get(method, *args):
            if method == "GET":
                self.assertFalse((self.root / "pending.jsonl").exists())
                raise TimeoutError()
            return request(method, *args)
        with patch.object(a, "request", side_effect=fail_get), self.assertRaises(TimeoutError):
            a.execute("SELECT 1", self.root / "pending.jsonl", {}, time.monotonic()+1, lambda h: None, lambda: None)
        p = a.page()
        p["context"]["binding_revision"] = True
        with self.assertRaises(PublicError):
            a.validate(p)

    def test_native_bounded_varchar_preserved_without_contract_coercion(self):
        a = ScriptedPublic()
        a.prepare_attempt(dict(request_id="request", adapter_identity=a.identity(), sql="SELECT 'abc' AS label"))
        p = a.page("FINISHED")
        p.update(columns=[dict(name="label", type="varchar(3)")], data=[["abc"]], next_uri=None)
        handles = []
        with patch.object(a, "request", return_value=p):
            a.execute("SELECT 'abc' AS label", self.root / "text.jsonl", {}, time.monotonic()+1,
                      handles.append, lambda: None)
        self.assertEqual(handles[-1]["native_columns"][0]["type"], "varchar(3)")
        reader = Reader(self.root / "text.jsonl")
        self.assertEqual(reader.header["columns"], [dict(name="label", type="varchar")])
        self.assertEqual(list(reader), [[["s", "abc"]]])

    def test_cli_credentials_and_saved_connection_roundtrip(self):
        from benchmarks.local_perf.__main__ import main
        from contextlib import redirect_stdout
        import io
        auth = self.root / "auth.json"
        auth.write_text(json.dumps(dict(username="account", password="never-record-password", selected_role="data_engineer")))
        auth.chmod(0o600)
        captured = self.root / "context.json"
        captured.write_text(json.dumps(CONTEXT))
        saved = self.root / "connection.json"
        args = ["local-perf", "freeze-public-connection", str(saved), "--adapter", "public", "--auth-file", str(auth),
                "--context-file", str(captured), "--url", "http://127.0.0.1:8123", "--max-rows", "17"]
        out = io.StringIO()
        with patch("sys.argv", args), redirect_stdout(out):
            main()
        self.assertNotIn("never-record-password", saved.read_text()+out.getvalue())
        with patch("sys.argv", ["local-perf", "reconcile", "unused", "--adapter", "public", "--connection-file", str(saved)]), patch(
                "benchmarks.local_perf.__main__.reconcile", return_value={}) as reconcile, redirect_stdout(out):
            main()
        self.assertEqual(reconcile.call_args.args[1].max_rows, 17)
        direct = self.root / "direct.json"
        direct.write_text(json.dumps(dict(username="direct", password="secret")))
        direct.chmod(0o600)
        with patch("sys.argv", ["local-perf", "reconcile", "unused", "--adapter", "trino", "--auth-file", str(direct), "--user", "other"]), self.assertRaises(SystemExit), patch("sys.stderr", io.StringIO()):
            main()

    def test_context_selection_and_explicit_auth_references(self):
        a = PublicAdapter("http://127.0.0.1:8123", "account", "password", "data_engineer")
        d = dict(id="db", sql_catalog="catalog", state="READY", capabilities={"query": True},
                 allowed_roles=["data_engineer"], binding_revision=1, generation_id="generation", grant_revision=None)
        s = dict(id="schema", sql_name="sandbox", allowed_modes=["read-only"])
        with patch.object(a, "request", side_effect=[{"items": [d]}, {"items": [s]}]):
            self.assertEqual(a.select("catalog", "sandbox")["context"], CONTEXT)
        auth = self.root / "auth.json"
        auth.write_text(json.dumps(dict(username="account", password="secret", selected_role="data_engineer")))
        auth.chmod(0o600)
        profile = self.root / "profile.json"
        profile.write_text(json.dumps(dict(client_auth_file=str(auth), auth_file="DO NOT OPEN", sql_endpoint_url="DO NOT USE")))
        self.assertEqual(client_auth_path(runtime_profile=profile), str(auth))
        self.assertEqual(private_auth(auth, public=True)["username"], "account")
        auth.chmod(0o644)
        with self.assertRaises(ValueError):
            private_auth(auth, public=True)
        with self.assertRaises(ValueError):
            client_auth_path(auth, profile)

    def test_tls_verified_ca_and_unsafe_context_refused(self):
        secure = ssl.create_default_context()
        conn = MagicMock()
        response = conn.getresponse.return_value
        response.status, response.length = 200, 0
        with patch.object(runner.ssl, "create_default_context", return_value=secure) as create, patch.object(
                runner.http.client, "HTTPSConnection", return_value=conn) as connection:
            a = runner.TrinoAdapter("https://localhost:8123", ca_file="explicit-ca.pem")
            a.request("GET", a.url, time.monotonic()+1)
            create.assert_called_once_with(cafile="explicit-ca.pem")
            self.assertIs(connection.call_args.kwargs["context"], secure)
            self.assertEqual(conn.request.call_args.args[3]["X-Trino-Client-Capabilities"], "PARAMETRIC_DATETIME")
            self.assertTrue(secure.check_hostname)
            self.assertEqual(secure.verify_mode, ssl.CERT_REQUIRED)
        unsafe = ssl._create_unverified_context()
        with self.assertRaises(ValueError):
            runner.TrinoAdapter("https://localhost", ssl_context=unsafe)

    # Only this test needs sockets; all other coverage stays active in sandbox.
    def test_real_public_loopback_headers_and_cold_pages(self):
        outer = self
        observed = []
        a = PublicAdapter("http://127.0.0.1", "account", "never-record-password", "data_engineer", CONTEXT)
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def send(self, page):
                data = json.dumps(page).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def page(self, pending=False):
                page = dict(id="query", backend_query_id=None if pending else "backend", **copy.deepcopy(a.binding),
                            columns=[] if pending else [dict(name="x", type="integer")], data=[] if pending else [[1]],
                            state="QUEUED" if pending else "FINISHED", stats={"state": "QUEUED" if pending else "FINISHED"}, next_uri="/api/v1/queries/query/pages/token" if pending else None)
                if not pending:
                    page["result_page_id"] = "receipt"
                return page
            def do_POST(self):
                observed.append(dict(self.headers))
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.assertEqual(body["request_id"], json.loads(outer.receipt().read_text())["request_id"])
                self.send(self.page(True))
            def do_GET(self):
                observed.append(dict(self.headers))
                page = self.page()
                if self.path.endswith("/token"):
                    page.update(data=[], state="RUNNING", stats={"state": "RUNNING"}, next_uri="/api/v1/queries/query/pages/token2", result_page_id="empty")
                self.send(page)
        try:
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        except PermissionError:
            self.skipTest("sandbox denies loopback bind; Commander must run real HTTP tests")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            a.url = "http://127.0.0.1:" + str(server.server_port)
            a.origin = runner.origin(a.url)
            self.run_adapter(a)
            self.assertEqual(len(observed), 3)
            for headers in observed:
                self.assertEqual(headers["X-Shirokuma-Role"], "data_engineer")
                self.assertEqual(headers["Content-Type"], "application/json")
                self.assertEqual(headers["Authorization"], a.headers["Authorization"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


    def test_definite_submission_rejection_and_nondefinite_errors(self):
        from test_local_perf_http import http_failure
        for name, options, definite in [('captured', {}, True), ('other-code', dict(code='OTHER'), False),
                ('server-error', dict(status=503), False), ('get', dict(method='GET'), False),
                ('other-route', dict(path='/api/v1/ingests'), False), ('partial', dict(framing='partial'), False),
                ('oversize', dict(framing='oversize'), False), ('deadline', dict(framing='deadline'), False)]:
            error = http_failure(**options)
            class Rejected(ScriptedPublic):
                def request(self, method, url, deadline, body=None):
                    self.calls.append((method, url))
                    if method == 'POST':
                        self.posts += 1
                        raise error
                    return dict(queries=[])
            adapter = Rejected()
            with self.assertRaises(Exception):
                self.run_adapter(adapter, name)
            intent = json.loads(self.receipt(name).read_text())
            self.assertEqual(intent['status'], 'FAILED' if definite else 'UNKNOWN')
            self.assertEqual(adapter.posts, 1)
            self.assertNotIn('never-record-password', self.receipt(name).read_text())
            if definite:
                self.assertEqual(intent['reconciliation'], 'REJECTED')
                self.assertEqual(intent['error_code'], 'SQL_PERMISSION_DENIED')
                self.assertEqual(intent['submission_rejection']['captured_api_code'], 'SQL_PERMISSION_DENIED')
                self.assertEqual(len(adapter.calls), 1)
                before = list(adapter.calls)
                self.assertEqual(runner.reconcile(self.receipt(name), adapter)['status'], 'FAILED')
                self.assertEqual(adapter.calls, before)
            else:
                self.assertNotIn('submission_rejection', intent)
                self.assertEqual(len(adapter.calls), 2)
            # Isolate intentional UNKNOWN holds for the next independent case.
            if not definite:
                self.tmp.cleanup()
                self.setUp()

    def legacy_audit(self):
        import hashlib
        adapter = ScriptedPublic('lost0')
        with self.assertRaises(OSError):
            self.run_adapter(adapter)
        path = self.receipt()
        intent = json.loads(path.read_text())
        manifest = path.parent.parent / 'queries.json'
        report = json.loads((manifest.parent / 'run.json').read_text())
        linked = self.root / 'readonly-audit.json'
        linked.write_text(json.dumps(dict(ledger_rows=0, completed_http_events=1)))
        evidence = dict(format=1, kind='local-operator-audit', reviewer='Codex local operator',
            observed_http_status=403, captured_api_code=None, derived_code='SQL_PERMISSION_DENIED',
            completed_response=True, unique_http_event=True, readonly_ledger_rows=0, pre_dispatch_guard_rejected=True,
            backend_nonterminal=0, original_attempt_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(), query_manifest_sha256=report['query_manifest_sha256'],
            context=copy.deepcopy(intent['dispatch']['context']), policy_source_commit='a'*40, policy_source_sha256='b'*64,
            evidence_files=[dict(path=str(linked), sha256=hashlib.sha256(linked.read_bytes()).hexdigest())],
            **{k: copy.deepcopy(intent[k]) for k in ('request_id', 'dispatch', 'adapter_identity', 'sql_sha256')})
        audit = self.root / 'operator-rejection.json'
        audit.write_text(json.dumps(evidence))
        return adapter, path, audit, evidence

    def test_legacy_operator_audit_sidecar_preserves_original_bytes_and_reuses_without_http(self):
        adapter, path, audit, evidence = self.legacy_audit()
        files = [path, path.parent.parent / 'run.json', path.parent.parent / 'queries.json']
        originals = {p: p.read_bytes() for p in files}
        with patch.object(adapter, 'request', side_effect=AssertionError('operator path must not use HTTP')):
            result = runner.reconcile(path, adapter, rejection_evidence=audit)
            self.assertEqual(result['status'], 'FAILED')
            self.assertEqual(result['reconciliation'], 'REJECTED')
            self.assertIsNone(result['handle'])
            self.assertIsNone(result['no_dispatch_evidence']['captured_api_code'])
            self.assertEqual(result['no_dispatch_evidence']['derived_code'], 'SQL_PERMISSION_DENIED')
            self.assertEqual(json.loads(path.read_text())['status'], 'UNKNOWN')
            self.assertEqual(runner.reconcile(path, adapter), result)
        self.assertEqual({p: p.read_bytes() for p in files}, originals)
        # A fresh bounded run can pass the old hold only through the validated sidecar.
        self.run_adapter(ScriptedPublic(), 'fresh')
        evidence['observed_http_status'] = 500
        sidecar = path.parent / 'reconciliation.json'
        bad = json.loads(sidecar.read_text())
        bad['no_dispatch_evidence'] = evidence
        sidecar.write_text(json.dumps(bad))
        self.assertIsNone(runner.terminal_reconciliation(sidecar, json.loads(path.read_text())))

    def test_legacy_rejection_audit_rejects_tampered_scopes_facts_and_linked_bytes_without_http(self):
        adapter, path, audit, evidence = self.legacy_audit()
        changes = [('request_id', 'different'), ('sql_sha256', '0'*64), ('original_attempt_sha256', '0'*64),
                   ('manifest_sha256', '0'*64), ('query_manifest_sha256', '0'*64), ('observed_http_status', 500),
                   ('captured_api_code', 'SQL_PERMISSION_DENIED'), ('derived_code', 'OTHER'), ('unique_http_event', False),
                   ('readonly_ledger_rows', 1), ('backend_nonterminal', 1), ('completed_response', False),
                   ('pre_dispatch_guard_rejected', False), ('evidence_files', []), ('policy_source_commit', ''),
                   ('context', dict(evidence['context'], generation_id='wrong')),
                   ('dispatch', dict(evidence['dispatch'], worksheet_id='wrong')),
                   ('adapter_identity', dict(evidence['adapter_identity'], account='wrong'))]
        with patch.object(adapter, 'request', side_effect=AssertionError('no HTTP on rejected audit')):
            for key, value in changes:
                bad = copy.deepcopy(evidence)
                bad[key] = value
                audit.write_text(json.dumps(bad))
                with self.subTest(key=key), self.assertRaises(ValueError):
                    runner.reconcile(path, adapter, rejection_evidence=audit)
            audit.write_text(json.dumps(evidence))
            original = path.read_bytes()
            for key, value in [('handle', dict(id='known')), ('sql', 'SELECT 999'), ('status', 'RUNNING')]:
                intent = json.loads(original)
                intent[key] = value
                path.write_text(json.dumps(intent))
                with self.subTest(intent=key), self.assertRaises(ValueError):
                    runner.reconcile(path, adapter, rejection_evidence=audit)
                path.write_bytes(original)
            manifest = path.parent.parent / 'queries.json'
            raw = manifest.read_bytes()
            manifest.write_bytes(raw + b' ')
            with self.assertRaises(ValueError):
                runner.reconcile(path, adapter, rejection_evidence=audit)
            manifest.write_bytes(raw)
            linked = Path(evidence['evidence_files'][0]['path'])
            linked.write_text('{}')
            with self.assertRaises(ValueError):
                runner.reconcile(path, adapter, rejection_evidence=audit)
        self.assertFalse((path.parent / 'reconciliation.json').exists())


    def test_captured_rejection_scope_and_forged_legacy_captured_receipt_are_refused(self):
        from test_local_perf_http import http_failure
        from benchmarks.local_perf.public import valid_submission_rejection
        adapter = ScriptedPublic()
        with patch.object(adapter, 'request', side_effect=http_failure()):
            with self.assertRaises(runner.HTTPFailure):
                self.run_adapter(adapter)
        intent = json.loads(self.receipt().read_text())
        for field, value in [('http_status', 500), ('request_id', 'wrong'), ('sql_sha256', 'wrong'),
                             ('path', '/api/v1/other'), ('captured_api_code', 'OTHER'),
                             ('dispatch', dict(intent['dispatch'], context=dict(CONTEXT, generation_id='wrong')))]:
            rejection = copy.deepcopy(intent['submission_rejection'])
            rejection[field] = value
            with self.subTest(field=field):
                self.assertFalse(valid_submission_rejection(rejection, intent))
        # A new run can reuse the genuinely captured terminal attempt without HTTP to it.
        self.run_adapter(ScriptedPublic(), 'after-captured')
        self.tmp.cleanup()
        self.setUp()
        adapter, path, audit, evidence = self.legacy_audit()
        original = json.loads(path.read_text())
        adapter.bind(original)
        forged = adapter.submission_rejection(http_failure(), original)
        sidecar = path.parent / 'reconciliation.json'
        sidecar.write_text(json.dumps(dict(original, status='FAILED', reconciliation='REJECTED', submission_rejection=forged)))
        self.assertIsNone(runner.terminal_reconciliation(sidecar, original))
        sidecar.unlink()
        resolved = runner.reconcile(path, adapter, rejection_evidence=audit)
        resolved['error_code'] = 'SQL_PERMISSION_DENIED'  # Cannot relabel an old lost code as captured.
        sidecar.write_text(json.dumps(resolved))
        self.assertIsNone(runner.terminal_reconciliation(sidecar, original))


    def test_operator_reviewer_is_explicit_bounded_and_not_agent_specific(self):
        adapter, path, audit, evidence = self.legacy_audit()
        original = path.read_bytes()
        with patch.object(adapter, 'request', side_effect=AssertionError('no HTTP for operator audit')):
            for reviewer in ('Codex local operator', 'Aiko Tanaka', 'Local operator 7'):
                evidence['reviewer'] = reviewer
                audit.write_text(json.dumps(evidence))
                result = runner.reconcile(path, adapter, rejection_evidence=audit)
                self.assertEqual(result['no_dispatch_evidence']['reviewer'], reviewer)
                self.assertEqual(path.read_bytes(), original)
            for reviewer in ('', '   ', 'x' * 129, 'operator\nforged', 'operator\rforged', 'operator\x00forged', None):
                evidence['reviewer'] = reviewer
                audit.write_text(json.dumps(evidence))
                with self.subTest(reviewer=repr(reviewer)), self.assertRaises(ValueError):
                    runner.reconcile(path, adapter, rejection_evidence=audit)

    def test_submission_rejection_drops_reflected_non_uuid_server_id(self):
        from test_local_perf_http import http_failure
        from benchmarks.local_perf.public import valid_submission_rejection
        adapter = ScriptedPublic()
        with patch.object(adapter, 'request', side_effect=http_failure(request_id='never-record-password')):
            with self.assertRaises(runner.HTTPFailure):
                self.run_adapter(adapter)
        intent = json.loads(self.receipt().read_text())
        receipt = intent['submission_rejection']
        self.assertIsNone(receipt['server_request_id'])
        self.assertNotIn('never-record-password', self.receipt().read_text())
        self.assertTrue(valid_submission_rejection(receipt, intent))
        receipt['server_request_id'] = 'never-record-password'
        self.assertFalse(valid_submission_rejection(receipt, intent))
        receipt['server_request_id'] = 'a9ee0597-9d68-49fb-bb2a-1408a6d774b3'
        self.assertTrue(valid_submission_rejection(receipt, intent))


    def test_original_intent_without_reconciliation_reuses_rejected_sidecar_without_http(self):
        import hashlib
        adapter, path, audit, evidence = self.legacy_audit()
        intent = json.loads(path.read_text())
        intent['status'] = 'INTENT'
        intent.pop('reconciliation', None)
        path.write_text(json.dumps(intent))
        original = path.read_bytes()
        manifest = path.parent.parent / 'queries.json'
        manifest_bytes = manifest.read_bytes()
        evidence['original_attempt_sha256'] = hashlib.sha256(original).hexdigest()
        evidence['manifest_sha256'] = hashlib.sha256(manifest_bytes).hexdigest()
        audit.write_text(json.dumps(evidence))
        with patch.object(adapter, 'request', side_effect=AssertionError('INTENT rejection must not dispatch or use HTTP')):
            result = runner.reconcile(path, adapter, rejection_evidence=audit)
            self.assertEqual(result['status'], 'FAILED')
            self.assertEqual(result['reconciliation'], 'REJECTED')
            sidecar = path.parent / 'reconciliation.json'
            self.assertEqual(runner.terminal_reconciliation(sidecar, intent), result)
            self.assertEqual(runner.reconcile(path, adapter), result)
            for key, value in [('status', 'UNKNOWN'), ('request_id', 'wrong'), ('handle', dict(id='known'))]:
                bad = copy.deepcopy(result)
                bad[key] = value
                sidecar.write_text(json.dumps(bad))
                with self.subTest(key=key):
                    self.assertIsNone(runner.terminal_reconciliation(sidecar, intent))
            bad = copy.deepcopy(result)
            bad['no_dispatch_evidence']['manifest_sha256'] = '0' * 64
            sidecar.write_text(json.dumps(bad))
            self.assertIsNone(runner.terminal_reconciliation(sidecar, intent))
            sidecar.write_text(json.dumps(result))
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(manifest.read_bytes(), manifest_bytes)


if __name__ == "__main__":
    unittest.main()
