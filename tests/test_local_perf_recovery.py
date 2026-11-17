"""Persisted crash cutpoints and reconciliation; stdlib, no sockets/extensions."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
from unittest import TestCase, main
from unittest.mock import patch

from benchmarks.local_perf import runner
from benchmarks.local_perf.capacity import Campaign
from benchmarks.local_perf.public import PublicAdapter
from tests.test_local_perf_runner import ScriptedTrino


PUBLIC_CONTEXT = dict(database_id="db", schema_id="schema", warehouse_id="LOCAL",
                      sql_catalog="catalog", sql_schema="sandbox",
                      selected_role="data_engineer", binding_revision=1,
                      generation_id="generation", grant_revision=None)
PUBLIC_MANIFEST = dict(format=1, dataset_id="portable-inline", queries=[
    dict(id="one", sql="SELECT 1 AS x", ordered=True)])


class RefusedHTTPConnection:
    """Production transport reaches conn.connect(); the kernel refuses."""
    def __init__(self, *args, **kwargs):
        pass

    def connect(self):
        raise ConnectionRefusedError(61, "Connection refused")

    def close(self):
        pass


class RecoveryTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.campaign = Campaign.create(self.root / "campaign")
        self.manifest = dict(format=1, dataset_id="d", queries=[
            dict(id="1", sql="SELECT 1"), dict(id="2", sql="SELECT 2")])

    def tearDown(self):
        self.tmp.cleanup()

    def crash_receipt(self, status, adapter=None):
        """Restore exact durable files observed before execute/finally unwinds."""
        adapter = adapter or ScriptedTrino("cancel-fails")
        run_root = self.campaign.root / "crashed"
        receipt = run_root / "1-0/attempt.json"
        captured = {}
        write = runner.write_json

        def capture(path, value):
            write(path, value)
            if path == receipt and value["status"] == status and not captured:
                for p in (run_root / "run.json", run_root / "queries.json", receipt):
                    captured[p] = p.read_bytes()

        with patch.object(runner, "write_json", side_effect=capture):
            with self.assertRaises(TimeoutError):
                runner.run(self.campaign, "crashed", self.manifest, adapter,
                           reference_label="test only", repetitions=2)
        self.assertEqual(len(captured), 3)
        for p, data in captured.items():
            p.write_bytes(data)
        report = json.loads((run_root / "run.json").read_text())
        intent = json.loads(receipt.read_text())
        self.assertEqual(report["status"], "INCOMPLETE")
        self.assertEqual(report["planned_attempts"], 4)
        self.assertEqual(report["pending_attempts"], 3)
        self.assertEqual(report["not_run_attempts"], 0)
        self.assertEqual(report["attempts"], [dict(path="1-0", status="INTENT", request_id=intent["request_id"])])
        self.assertEqual(intent["status"], status)
        self.assertIsNone(intent["elapsed_s"])
        return receipt

    def assert_blocked(self, adapter):
        with self.assertRaisesRegex(RuntimeError, "unreconciled"):
            runner.run(self.campaign, "blocked", self.manifest, adapter, reference_label="test only")

    def test_intent_cutpoint_has_owner_but_no_handle_and_never_retries(self):
        path = self.crash_receipt("INTENT")
        original = path.read_bytes()
        adapter = ScriptedTrino("ok")
        with patch.object(adapter, "request", side_effect=AssertionError("no HTTP without handle")):
            self.assertEqual(runner.reconcile(path, adapter)["status"], "UNKNOWN")
            self.assertEqual(runner.reconcile(path, adapter)["status"], "UNKNOWN")
            self.assert_blocked(adapter)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(adapter.posts, 0)

    def test_running_cutpoint_terminal_is_retained_without_http_and_allows_next_run(self):
        path = self.crash_receipt("RUNNING")
        original = path.read_bytes()
        adapter = ScriptedTrino("ok")
        with patch.object(adapter, "request", side_effect=[{}, {"state": "CANCELED"}]) as request:
            result = runner.reconcile(path, adapter)
            self.assertEqual([c.args[0] for c in request.call_args_list], ["DELETE", "GET"])
        self.assertEqual(result["reconciliation"], "CANCELED")
        evidence = path.parent / "reconciliation.json"
        retained = evidence.read_bytes()
        with patch.object(adapter, "request", side_effect=OSError("404: expired query")) as request, patch.object(
                runner, "write_json", side_effect=AssertionError("terminal evidence must not be rewritten")):
            self.assertEqual(runner.reconcile(path, adapter), result)
            request.assert_not_called()
        self.assertEqual(evidence.read_bytes(), retained)
        self.assertEqual(path.read_bytes(), original)
        runner.run(self.campaign, "next", self.manifest, adapter, reference_label="test only")
        self.assertEqual(adapter.posts, 2)

    def test_unknown_with_handle_can_be_observed_again_without_post(self):
        path = self.crash_receipt("RUNNING")
        adapter = ScriptedTrino("cancel-fails")
        self.assertEqual(runner.reconcile(path, adapter)["status"], "UNKNOWN")
        self.assert_blocked(adapter)
        with patch.object(adapter, "request", side_effect=[{}, {"state": "FINISHED"}]) as request:
            self.assertEqual(runner.reconcile(path, adapter)["reconciliation"], "FINISHED")
            self.assertEqual([c.args[0] for c in request.call_args_list], ["DELETE", "GET"])
        self.assertEqual(adapter.posts, 0)

    def test_mismatched_terminal_evidence_cannot_open_gate_or_be_reused(self):
        path = self.crash_receipt("RUNNING")
        intent = json.loads(path.read_text())
        adapter = ScriptedTrino("ok")
        evidence = path.parent / "reconciliation.json"
        for key, wrong in (("handle", {"id": "other"}), ("request_id", "other"),
                           ("adapter_identity", {"kind": "other"})):
            with self.subTest(key=key):
                record = dict(intent, status="FAILED", reconciliation="CANCELED")
                record[key] = wrong
                evidence.write_text(json.dumps(record))
                self.assert_blocked(adapter)
                with patch.object(adapter, "cancel_reconcile", return_value="UNKNOWN") as cancel:
                    self.assertEqual(runner.reconcile(path, adapter)["status"], "UNKNOWN")
                    cancel.assert_called_once_with(intent["handle"])

    def test_fresh_duckdb_cannot_resolve_running_or_unknown(self):
        adapter = runner.DuckDBAdapter(self.root / "dataset")

        def interrupted(sql, out, metadata, deadline, save, check):
            save({"id": "original-local-query"})
            raise TimeoutError("interrupted original process")

        with patch.object(adapter, "execute", side_effect=interrupted):
            path = self.crash_receipt("RUNNING", adapter)
        fresh = runner.DuckDBAdapter(adapter.dataset)
        for status in ("RUNNING", "UNKNOWN"):
            with self.subTest(status=status):
                intent = json.loads(path.read_text())
                intent["status"] = status
                path.write_text(json.dumps(intent))
                original = path.read_bytes()
                with patch.object(fresh, "cancel_reconcile") as cancel:
                    with self.assertRaisesRegex(ValueError, "external DuckDB"):
                        runner.reconcile(path, fresh)
                    cancel.assert_not_called()
                self.assertFalse((path.parent / "reconciliation.json").exists())
                self.assertEqual(path.read_bytes(), original)
                self.assert_blocked(fresh)


class NotSentTests(TestCase):
    """Offline, non-dispatching NOT_SENT proof and production gate reuse."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.sequence = 0

    def tearDown(self):
        self.tmp.cleanup()

    def adapter(self):
        return PublicAdapter("http://127.0.0.1:8888", "account", "never-record-password",
                             "data_engineer", PUBLIC_CONTEXT, max_rows=None)

    def fixture(self):
        self.sequence += 1
        campaign = Campaign.create(self.root / ("campaign-" + str(self.sequence)))
        adapter = self.adapter()
        with patch.object(runner.http.client, "HTTPConnection", RefusedHTTPConnection):
            with self.assertRaises(ConnectionRefusedError):
                runner.run(campaign, "failed", PUBLIC_MANIFEST, adapter, reference_label="test only")
        path = campaign.root / "failed" / "one-0" / "attempt.json"
        return campaign, path

    def reconcile_unknown(self, path):
        fresh = self.adapter()
        with patch.object(fresh, "request", return_value={"queries": []}):
            result = runner.reconcile(path, fresh)
        self.assertEqual((result["status"], result["reconciliation"]), ("UNKNOWN", "UNKNOWN"))
        return result

    def evidence(self, path, *, age=5.0, pid=4242, offset=0, state_rows=False,
                 server_after=False, pid_override=None):
        self.sequence += 1
        intent = json.loads(path.read_text())
        started = dt.datetime.fromtimestamp(intent["wall_started"], dt.timezone.utc)
        local = started + dt.timedelta(seconds=offset)
        terminal = local - dt.timedelta(seconds=age)
        process = (
            terminal.replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
            + ".203 Df runningboardd[1:1] [com.apple.runningboard:process] "
            + "[anon<python3.12>(501):" + str(pid) + "] termination reported by proc_exit\n")
        process += (
            (local - dt.timedelta(seconds=30)).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
            + ".100 Df mDNSResponder[1:1] [com.apple.mDNSResponder:Default] "
            + "START PID[" + str(pid) + "](python3.12)\n")
        process_log = self.root / ("process-" + str(self.sequence) + ".log")
        process_log.write_text(process)
        server_time = local + dt.timedelta(seconds=5) if server_after else local - dt.timedelta(seconds=5)
        server_log = self.root / ("server-" + str(self.sequence) + ".log")
        server_log.write_text(
            '127.0.0.1 - - [%s] "GET /api/status HTTP/1.1" 200 -\n'
            % server_time.strftime("%d/%b/%Y %H:%M:%S"))
        database = self.root / ("state-" + str(self.sequence) + ".sqlite3")
        connection = sqlite3.connect(database)
        try:
            connection.execute("CREATE TABLE query_records (id TEXT, created_at TEXT)")
            connection.execute("CREATE TABLE warehouse_admissions (id TEXT, created_at TEXT)")
            connection.execute("CREATE TABLE query_execution_contexts (request_id TEXT, worksheet_id TEXT)")
            connection.execute("INSERT INTO query_records VALUES (?, ?)",
                               ("other", "2020-01-01T00:00:00+00:00"))
            connection.execute("INSERT INTO query_execution_contexts VALUES (?, ?)", ("other", "other"))
            if state_rows:
                connection.execute("INSERT INTO query_execution_contexts VALUES (?, ?)",
                                   (intent["request_id"], intent["dispatch"]["worksheet_id"]))
                connection.execute("INSERT INTO query_records VALUES (?, ?)",
                                   ("matching", local.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")))
            connection.commit()
        finally:
            connection.close()
        prior = self.root / ("prior-" + str(self.sequence) + ".json")
        prior.write_text(json.dumps({"format": 1, "kind": "prior", "value": self.sequence}) + "\n")
        return dict(process_log=str(process_log), process_pid=pid if pid_override is None else pid_override,
                    utc_offset_seconds=offset, server_log=str(server_log),
                    state_db=str(database), historical_evidence=[str(prior)],
                    process_source_command="ps -p %d -o lstart=,command=" % pid)

    def mint(self, path, evidence_options=None, **overrides):
        options = self.evidence(path, **(evidence_options or {}))
        options.update(overrides)
        return runner.confirm_not_sent(path, self.adapter(), **options)

    def assert_gate_open(self, campaign):
        gate = runner.inspect_campaign(campaign)
        self.assertTrue(gate["ok"], gate)
        self.assertEqual(gate["blockers"], [])
        return gate

    def assert_gate_blocked(self, campaign):
        gate = runner.inspect_campaign(campaign)
        self.assertFalse(gate["ok"])
        self.assertTrue(gate["blockers"])
        return gate

    def test_verified_not_sent_preserves_originals_and_unlocks_same_gate(self):
        campaign, path = self.fixture()
        self.reconcile_unknown(path)
        original_attempt = path.read_bytes()
        original_reconciliation = (path.parent / "reconciliation.json").read_bytes()
        self.assert_gate_blocked(campaign)
        result = self.mint(path)
        self.assertEqual((result["status"], result["reconciliation"]), ("FAILED", "NOT_SENT"))
        self.assertEqual(path.read_bytes(), original_attempt)
        self.assertEqual((path.parent / "reconciliation.json").read_bytes(), original_reconciliation)
        receipt = path.parent / "not-sent.json"
        self.assertTrue(receipt.exists())
        original_receipt = receipt.read_bytes()
        self.assertEqual(json.loads(original_receipt)["reconciliation_state"], "unknown-sidecar")
        self.assert_gate_open(campaign)
        self.assertEqual(self.mint(path)["reconciliation"], "NOT_SENT")
        self.assertEqual(receipt.read_bytes(), original_receipt)
        fresh = self.adapter()
        with patch.object(fresh, "request", side_effect=AssertionError("no recovery HTTP")):
            self.assertEqual(runner.reconcile(path, fresh)["reconciliation"], "NOT_SENT")

    def test_unrelated_unknown_attempt_still_blocks_after_resolution(self):
        campaign, first = self.fixture()
        self.mint(first)
        adapter = self.adapter()
        with patch.object(runner.http.client, "HTTPConnection", RefusedHTTPConnection):
            with self.assertRaises(ConnectionRefusedError):
                runner.run(campaign, "second", PUBLIC_MANIFEST, adapter, reference_label="test only")
        gate = self.assert_gate_blocked(campaign)
        self.assertEqual([(b["run"], b["reason"]) for b in gate["blockers"]],
                         [("second", "unreconciled attempt blocks new dispatch")])
        self.assertEqual([a["reconciliation"] for a in gate["attempts"]], ["NOT_SENT", "UNKNOWN"])

    def test_transport_proof_rejects_sent_retry_response_and_ambiguous_shapes(self):
        _, path = self.fixture()
        valid = json.loads(path.read_text())
        self.assertEqual(runner.unsent.transport_proof(valid)["initial_calls"], 1)
        cases = {
            "sent": lambda value: value["timings"]["http"]["initial"].update(send=0.0001),
            "retry": lambda value: value["timings"]["http"]["initial"].update(calls=2),
            "follow-up": lambda value: value["timings"]["http"]["follow_up"].update(calls=1),
            "bool-initial": lambda value: value["timings"]["http"]["initial"].update(calls=True),
            "bool-follow-up": lambda value: value["timings"]["http"]["follow_up"].update(calls=False),
            "float-initial": lambda value: value["timings"]["http"]["initial"].update(calls=1.0),
            "float-follow-up": lambda value: value["timings"]["http"]["follow_up"].update(calls=0.0),
            "response": lambda value: value["timings"]["events"].update(initial_response_complete=0.1),
            "terminal": lambda value: value["timings"]["events"].update(terminal_observed=0.1),
            "timeout": lambda value: value.update(error_type="TimeoutError"),
            "reset": lambda value: value.update(error_type="ConnectionResetError"),
            "handle": lambda value: value.update(handle={"id": "query"}),
            "missing-handle": lambda value: value.pop("handle"),
            "rejection": lambda value: value.update(submission_rejection={"http_status": 403}),
            "running": lambda value: value.update(status="RUNNING"),
            "backend": lambda value: value["timings"]["backend_report"].update(client_observed=True),
            "missing-send": lambda value: value["timings"]["http"]["initial"].pop("send"),
            "missing-recovery": lambda value: value["timings"]["events"].update(recovery_started=None),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                changed = json.loads(json.dumps(valid))
                mutate(changed)
                with self.assertRaises(runner.unsent.UnsentError):
                    runner.unsent.transport_proof(changed)

    def test_contradictory_receipts_result_or_terminal_handle_block(self):
        # A valid receipt followed by a retained terminal handle is contradictory.
        campaign, path = self.fixture()
        self.mint(path)
        intent = json.loads(path.read_text())
        (path.parent / "reconciliation.json").write_text(json.dumps(
            dict(intent, status="FAILED", reconciliation="FINISHED",
                 recovered_handle={"id": "query"}), indent=2) + "\n")
        self.assert_gate_blocked(campaign)

        # A submission rejection is a complete response, not a connect refusal.
        campaign, path = self.fixture()
        self.mint(path)
        receipt = json.loads((path.parent / "not-sent.json").read_text())
        intent = json.loads(path.read_text())
        intent["submission_rejection"] = {"http_status": 403}
        path.write_text(json.dumps(intent, indent=2) + "\n")
        receipt["attempt_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        (path.parent / "not-sent.json").write_text(json.dumps(receipt, indent=2) + "\n")
        with self.assertRaises(ValueError):
            runner.inspect_campaign(campaign)

        # A result artifact contradicts an unsent request.
        campaign, path = self.fixture()
        self.mint(path)
        (path.parent / "result.jsonl").write_text("forbidden\n")
        self.assert_gate_blocked(campaign)

    def test_terminal_handle_would_block_mint_without_sidecar(self):
        campaign, path = self.fixture()
        intent = json.loads(path.read_text())
        (path.parent / "reconciliation.json").write_text(json.dumps(
            dict(intent, status="FAILED", reconciliation="FINISHED",
                 recovered_handle={"id": "query"})))
        with self.assertRaises(ValueError):
            self.mint(path)
        self.assertFalse((path.parent / "not-sent.json").exists())

    def test_result_file_or_insufficient_corroboration_blocks(self):
        campaign, path = self.fixture()
        (path.parent / "result.jsonl").write_text("forbidden\n")
        with self.assertRaises(ValueError):
            self.mint(path)
        (path.parent / "result.jsonl").unlink()
        with self.assertRaises(ValueError):
            runner.confirm_not_sent(path, self.adapter())

    def test_process_and_state_corroboration_must_match(self):
        cases = {
            "termination-after": dict(age=-5.0),
            "pid-mismatch": dict(pid_override=9999),
            "state-rows": dict(state_rows=True),
            "server-after": dict(server_after=True),
        }
        for name, options in cases.items():
            with self.subTest(name=name):
                _, path = self.fixture()
                with self.assertRaises(ValueError):
                    self.mint(path, evidence_options=options)
                self.assertFalse((path.parent / "not-sent.json").exists())

    def test_tampered_or_reused_sidecar_blocks(self):
        campaign, path = self.fixture()
        self.mint(path)
        receipt = path.parent / "not-sent.json"
        original = json.loads(receipt.read_text())
        mutations = {
            "request-id": lambda value: value.update(request_id="00000000-0000-4000-8000-000000000000"),
            "transport-send": lambda value: value["transport"].update(send=0.1),
            "attempt-hash": lambda value: value.update(attempt_sha256="0" * 64),
            "campaign-id": lambda value: value.update(campaign_id="00000000-0000-4000-8000-000000000000"),
            "state-matching": lambda value: value["corroboration"]["state"].update(matching_rows=[{"table": "x"}]),
            "reconciliation-hash": lambda value: value.update(reconciliation_sha256="0" * 64),
            "operator-bool": lambda value: value.update(allow=True),
            "self-hash": lambda value: value.update(receipt_sha256="0" * 64),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                value = json.loads(json.dumps(original))
                mutate(value)
                receipt.write_text(json.dumps(value, indent=2) + "\n")
                self.assert_gate_blocked(campaign)
        receipt.write_text(json.dumps(original, indent=2) + "\n")
        self.assert_gate_open(campaign)

        intent = json.loads(path.read_text())
        intent["timings"]["http"]["initial"]["send"] = 0.1
        path.write_text(json.dumps(intent, indent=2) + "\n")
        self.assert_gate_blocked(campaign)

    def test_state_absence_does_not_replace_transport_proof(self):
        _, path = self.fixture()
        intent = json.loads(path.read_text())
        intent["timings"]["http"]["initial"]["send"] = 0.1
        path.write_text(json.dumps(intent, indent=2) + "\n")
        options = self.evidence(path)
        options.pop("process_log")
        options.pop("process_pid")
        options.pop("server_log")
        with self.assertRaises(ValueError):
            runner.confirm_not_sent(path, self.adapter(), **options)
        self.assertFalse((path.parent / "not-sent.json").exists())

    def test_recorded_attempt_missing_file_or_directory_blocks(self):
        for missing in ("file", "directory"):
            with self.subTest(missing=missing):
                campaign, path = self.fixture()
                if missing == "file":
                    path.unlink()
                else:
                    shutil.rmtree(path.parent)
                gate = self.assert_gate_blocked(campaign)
                self.assertTrue(any("recorded attempt file is missing" in blocker["reason"]
                                    for blocker in gate["blockers"]), gate)
                adapter = ScriptedTrino("ok")
                with self.assertRaisesRegex(RuntimeError, "recorded attempt file is missing"):
                    runner.run(campaign, "blocked", PUBLIC_MANIFEST, adapter, reference_label="test only")
                self.assertEqual(adapter.posts, 0)

    def test_recorded_attempt_identity_and_path_must_match_discovery(self):
        cases = {
            "request-id": lambda report: report["attempts"][0].update(
                request_id="00000000-0000-4000-8000-000000000000"),
            "missing-request-id": lambda report: report["attempts"][0].pop("request_id"),
            "invalid-path": lambda report: report["attempts"][0].update(path="../escape"),
            "duplicate-path": lambda report: report["attempts"].append(dict(report["attempts"][0])),
            "missing-attempts": lambda report: report.pop("attempts"),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                campaign, path = self.fixture()
                report_path = path.parent.parent / "run.json"
                report = json.loads(report_path.read_text())
                mutate(report)
                report_path.write_text(json.dumps(report, indent=2) + "\n")
                self.assert_gate_blocked(campaign)
                self.assertFalse((path.parent / "not-sent.json").exists())

    def test_orphaned_unknown_attempt_is_still_validated(self):
        campaign, path = self.fixture()
        report_path = path.parent.parent / "run.json"
        report = json.loads(report_path.read_text())
        report["attempts"] = []
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        gate = self.assert_gate_blocked(campaign)
        self.assertTrue(any(blocker.get("attempt") == path.parent.name for blocker in gate["blockers"]), gate)

    def test_failed_first_capture_can_retry_without_deleting_evidence(self):
        campaign, path = self.fixture()
        with self.assertRaises(ValueError):
            self.mint(path, evidence_options=dict(state_rows=True))
        self.assertFalse((path.parent / "not-sent.json").exists())
        self.assertFalse((path.parent / "not-sent-evidence").exists())
        result = self.mint(path)
        self.assertEqual(result["reconciliation"], "NOT_SENT")
        receipt = json.loads((path.parent / "not-sent.json").read_text())
        self.assertTrue(receipt["corroboration"]["state"]["path"].startswith(
            "not-sent-evidence/capture-"))
        self.assert_gate_open(campaign)

        campaign, path = self.fixture()
        with self.assertRaises(ValueError):
            self.mint(path, evidence_options=dict(pid_override=9999))
        self.assertFalse((path.parent / "not-sent.json").exists())
        self.assertEqual(self.mint(path)["reconciliation"], "NOT_SENT")
        self.assert_gate_open(campaign)

    def test_missing_sidecar_reblocks_without_rewriting_original(self):
        campaign, path = self.fixture()
        original_attempt = path.read_bytes()
        self.mint(path)
        (path.parent / "not-sent.json").unlink()
        gate = self.assert_gate_blocked(campaign)
        self.assertTrue(any(blocker["effective_status"] == "UNKNOWN" for blocker in gate["blockers"]), gate)
        self.assertEqual(path.read_bytes(), original_attempt)

    def test_attempt_claiming_not_sent_without_sidecar_blocks(self):
        campaign, path = self.fixture()
        intent = json.loads(path.read_text())
        intent.update(status="FAILED", reconciliation="NOT_SENT")
        path.write_text(json.dumps(intent, indent=2) + "\n")
        gate = self.assert_gate_blocked(campaign)
        self.assertTrue(any("not-sent claim without sidecar" in blocker["reason"]
                            for blocker in gate["blockers"]), gate)
        self.assertFalse((path.parent / "not-sent.json").exists())

    def test_orphan_receipt_temp_does_not_block_retry(self):
        campaign, path = self.fixture()
        (path.parent / "not-sent.json.new-deadbeef").write_text("partial\n")
        self.assertEqual(self.mint(path)["reconciliation"], "NOT_SENT")
        self.assert_gate_open(campaign)

    def test_sidecar_cannot_be_reused_for_another_attempt(self):
        campaign, first = self.fixture()
        self.mint(first)
        adapter = self.adapter()
        with patch.object(runner.http.client, "HTTPConnection", RefusedHTTPConnection):
            with self.assertRaises(ConnectionRefusedError):
                runner.run(campaign, "other", PUBLIC_MANIFEST, adapter, reference_label="test only")
        other = campaign.root / "other" / "one-0"
        (other / "not-sent.json").write_bytes((first.parent / "not-sent.json").read_bytes())
        gate = self.assert_gate_blocked(campaign)
        self.assertTrue(any(blocker["run"] == "other" for blocker in gate["blockers"]))
        self.assertEqual(gate["attempts"][0]["reconciliation"], "NOT_SENT")


if __name__ == "__main__":
    main()
