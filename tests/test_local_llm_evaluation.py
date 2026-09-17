from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path
import re
import stat
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parents[1] / "tools/local_experiment/llm"
sys.path.insert(0, str(HERE))
import evaluation as E
import mock_provider as M
import providers as P


def config():
    return json.loads((HERE / "providers.example.json").read_text())


def summary(evidence):
    return {"result": "complete", "models": list(reversed(evidence["models"])),
            "model_count": len(evidence["models"]),
            "verified_run_available": evidence["verified_run_available"]}


class FakeProvider:
    def __init__(self, failures=None):
        self.calls = []
        self.failures = failures or {}

    def complete(self, phase, evidence):
        self.calls.append((phase, copy.deepcopy(evidence)))
        failure = self.failures.get(len(self.calls))
        if isinstance(failure, Exception):
            raise failure
        content = summary(evidence) if failure is None else failure
        return {"content": content, "prompt_tokens": 17, "completion_tokens": 9,
                "duration_ms": 5, "inference_cost_estimate": None}


class FakeClient:
    def __init__(self, transform=None, fail_insert=False, fail_preflight=False):
        self.events = []
        self.transform = transform
        self.fail_insert = fail_insert
        self.fail_preflight = fail_preflight

    def query(self, sql):
        if sql == "SELECT 1":
            if self.fail_preflight:
                raise RuntimeError("private-connection-error")
            return "1\n"
        if sql.startswith("INSERT INTO agenthouse.pawprints FORMAT JSONEachRow\n"):
            if self.fail_insert:
                raise RuntimeError("private-insert-error")
            self.events.append(json.loads(sql.split("\n", 1)[1]))
            return ""
        matched = re.search(r"WHERE event_id = '([a-f0-9-]{36})' FORMAT JSON$", sql)
        if not matched:
            raise AssertionError("unexpected test query")
        rows = [{key: item[key] for key in E.AUDIT_FIELDS} for item in self.events
                if item["event_id"] == matched.group(1)]
        for row in rows:
            for key in ("prompt_tokens", "completion_tokens", "duration_ms"):
                if row[key] is not None:
                    row[key] = str(row[key])
        if self.transform is not None:
            rows = self.transform(rows)
        return json.dumps({"data": rows})


class LLMEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audit_module = E.agenthouse_module()

    def evaluate(self, directory, provider=None, client=None):
        return E.run_evaluation(config(), Path(directory) / "evidence", provider=provider or FakeProvider(),
                                client=client or FakeClient(), audit_module=self.audit_module)

    def test_fixed_five_cases_cover_empty_renaming_record_status_and_boundary(self):
        cases = E.fixtures()
        self.assertEqual(len(cases), 5)
        self.assertEqual([len(case["evidence"]["models"]) for case in cases], [0, 1, 3, 2, 16])
        self.assertEqual([case["evidence"]["verified_run_available"] for case in cases],
                         [False, False, True, False, True])
        self.assertEqual(cases[2]["evidence"]["models"], ["renamed_alpha", "renamed_beta", "renamed_gamma"])
        for case in cases:
            self.assertEqual(P.validate_evidence(case["evidence"]), case["evidence"])

    def test_exact_truth_comparison_rejects_self_consistent_wrong_model_output(self):
        evidence = E.fixtures()[2]["evidence"]
        good = summary(evidence)
        self.assertTrue(E.correctness(good, evidence)[1])
        invalid = [{**good, "model_count": 1, "models": ["invented_model"]},
                   {**good, "model_count": True}, {**good, "verified_run_available": 1},
                   {**good, "verified_run_available": False}, {**good, "result": "failed"},
                   {**good, "extra": "private-output"}, {**good, "models": ["renamed_alpha"] * 3},
                   {**good, "models": "renamed_alpha"}, None, [], "private-output"]
        for value in invalid:
            with self.subTest(value=value):
                checks, passed = E.correctness(value, evidence)
                self.assertFalse(passed)
                self.assertFalse(all(checks.values()))

    def test_ten_summaries_are_audited_and_aggregated_with_private_evidence(self):
        provider, client = FakeProvider(), FakeClient()
        with tempfile.TemporaryDirectory() as directory:
            report = self.evaluate(directory, provider, client)
            output = Path(directory) / "evidence"
            retained = json.loads((output / "summary.json").read_text())
            attempts = [json.loads(line) for line in (output / "attempts.jsonl").read_text().splitlines()]
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
            for path in (output / "summary.json", output / "attempts.jsonl"):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(report, retained)
        self.assertEqual(report["result"], "passed")
        self.assertEqual(report["requested_inferences"], 10)
        self.assertEqual(report["attempted_inferences"], 10)
        self.assertEqual(report["passed_inferences"], 10)
        self.assertEqual(report["audited_inferences"], 10)
        self.assertEqual(report["retries"], 0)
        self.assertEqual(report["cloud_calls"], 0)
        self.assertEqual(report["check_successes"], dict.fromkeys(E.CHECKS, 10))
        self.assertEqual(report["usage"]["prompt_tokens"], 170)
        self.assertEqual(report["usage"]["completion_tokens"], 90)
        self.assertEqual(report["usage"]["duration_ms"], 50)
        self.assertIsNone(report["usage"]["inference_cost_estimate"])
        self.assertEqual(len(attempts), 10)
        self.assertEqual(provider.calls, [("summarize", case["evidence"]) for case in E.fixtures() for _ in range(2)])
        self.assertEqual(len({event["event_id"] for event in client.events}), 10)
        for event in client.events:
            self.assertEqual(event["task_id"], report["task_id"])
            self.assertEqual(event["trace_id"], report["trace_id"])
            self.assertEqual(event["action_type"], "llm.eval_summary")
            self.assertEqual(event["status"], "success")
            self.assertIsNone(event["inference_cost_estimate"])

    def test_inference_failures_are_retained_and_do_not_retry_or_hide_usage(self):
        failure = P.ProviderError("private-provider-error", {"prompt_tokens": 17, "completion_tokens": 9,
                                                             "duration_ms": 5, "content": "private-content"})
        provider = FakeProvider({2: failure, 6: {"result": "complete", "model_count": 1,
                    "models": ["private-invented-model"], "verified_run_available": True}})
        client = FakeClient()
        with tempfile.TemporaryDirectory() as directory:
            report = self.evaluate(directory, provider, client)
            retained = "\n".join(path.read_text() for path in (Path(directory) / "evidence").iterdir())
        self.assertEqual(report["result"], "failed")
        self.assertEqual(report["attempted_inferences"], 10)
        self.assertEqual(report["passed_inferences"], 8)
        self.assertEqual(report["audited_inferences"], 10)
        self.assertEqual(report["usage"]["completion_tokens"], 90)
        self.assertEqual(report["attempts"][1]["failure_code"], "provider_error")
        self.assertEqual(report["attempts"][5]["failure_code"], "summary_mismatch")
        self.assertEqual([event["status"] for event in client.events].count("failed"), 2)
        self.assertEqual(len(provider.calls), 10)
        for private_value in ("private-provider-error", "private-content", "private-invented-model"):
            self.assertNotIn(private_value, retained)

    def test_readback_requires_exact_correlation_identity_status_metrics_and_cost(self):
        for field, value in (("task_id", "different-task"), ("trace_id", "different-trace"),
                             ("model_name", "different-model"), ("status", "failed"),
                             ("prompt_tokens", "18"), ("completion_tokens", True),
                             ("duration_ms", "6"), ("inference_cost_estimate", 0)):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                provider = FakeProvider()
                client = FakeClient(transform=lambda rows: [{**rows[0], field: value}])
                report = self.evaluate(directory, provider, client)
                self.assertEqual(report["result"], "failed")
                self.assertEqual(report["attempted_inferences"], 1)
                self.assertEqual(report["audited_inferences"], 0)
                self.assertEqual(report["attempts"][0]["audit"], "failed")
                self.assertEqual(len(provider.calls), 1)

    def test_missing_or_duplicate_audit_rows_cannot_pass(self):
        for transform in (lambda rows: [], lambda rows: rows + rows):
            with tempfile.TemporaryDirectory() as directory:
                report = self.evaluate(directory, client=FakeClient(transform=transform))
            self.assertEqual(report["result"], "failed")
            self.assertEqual(report["attempted_inferences"], 1)
            self.assertEqual(report["audited_inferences"], 0)

    def test_audit_outage_stops_without_retries_and_sanitizes_errors(self):
        for preflight in (True, False):
            provider = FakeProvider()
            with tempfile.TemporaryDirectory() as directory:
                report = self.evaluate(directory, provider, FakeClient(fail_preflight=preflight, fail_insert=True))
                saved = (Path(directory) / "evidence/summary.json").read_text()
            self.assertEqual(report["result"], "failed")
            self.assertEqual(len(provider.calls), 0 if preflight else 1)
            self.assertEqual(report["audited_inferences"], 0)
            self.assertNotIn("private-", saved)

    def test_mock_only_http_evaluation_keeps_unknown_usage_null(self):
        with tempfile.TemporaryDirectory() as directory, M.serve_mock() as endpoint:
            report = E.run_evaluation(config(), Path(directory) / "evidence", endpoint=endpoint,
                                      client=FakeClient(), audit_module=self.audit_module)
        self.assertEqual(report["result"], "passed")
        self.assertEqual(report["passed_inferences"], 10)
        self.assertIsNone(report["usage"]["prompt_tokens"])
        self.assertIsNone(report["usage"]["completion_tokens"])
        self.assertEqual(report["usage"]["prompt_tokens_known_calls"], 0)
        self.assertIsNone(report["usage"]["inference_cost_estimate"])

    def test_cloud_config_rejected_before_provider_or_audit_creation(self):
        value = config()
        value["active_provider"] = "cloud"
        value["providers"]["cloud"]["enabled"] = True
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(E, "LocalProvider") as provider, patch.object(E, "agenthouse_module") as audit:
                with self.assertRaises(P.ProviderError):
                    E.run_evaluation(value, Path(directory) / "evidence")
                provider.assert_not_called()
                audit.assert_not_called()

    def test_cli_has_failure_exit_and_private_evidence_without_raw_setup_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(E, "ROOT", Path(directory)), patch.object(E, "load_config",
                    side_effect=RuntimeError("private-setup-detail")), contextlib.redirect_stdout(io.StringIO()) as output:
                code = E.main([])
            result = json.loads(output.getvalue())
            saved = json.loads((Path(result["evidence"]) / "summary.json").read_text())
        self.assertEqual(code, 1)
        self.assertEqual(saved["result"], "failed")
        self.assertNotIn("private-setup-detail", output.getvalue())
        self.assertNotIn("private-setup-detail", json.dumps(saved))


if __name__ == "__main__":
    unittest.main()
