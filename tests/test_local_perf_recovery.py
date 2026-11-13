"""Persisted crash cutpoints and reconciliation; stdlib, no sockets/extensions."""
import json
from pathlib import Path
import tempfile
from unittest import TestCase, main
from unittest.mock import patch

from benchmarks.local_perf import runner
from benchmarks.local_perf.capacity import Campaign
from tests.test_local_perf_runner import ScriptedTrino


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


if __name__ == "__main__":
    main()
