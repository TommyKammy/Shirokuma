"""A retained-receipt report must not improve results by hiding failed evidence."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("evaluation_report", ROOT / "tools/local_experiment/evaluation/report.py")
REPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORT)


def signal_triage(case):
    expected = case['expected']
    tools = expected['required_tools'] or (expected['allowed_tools'][:1] if case['evidence']['findings'] else [])
    return {'assessment': expected['assessment'], 'root_cause': expected['root_cause'],
            'checks': [{'tool': tool, 'finding_ids': [finding['id'] for finding in case['evidence']['findings']
                        if tool in finding['allowed_tools']]} for tool in tools]}


def receipt(suite="inventory", *, mock=False, old_sre=False, failed_index=None, truncate=None, unknown_tokens=False,
            data_variant=None, platform_sre=False, sre_signals=False):
    _, _, prefix, action, cases = REPORT.SUITES[suite]
    if old_sre:
        cases = cases[:6]
    elif platform_sre:
        cases = cases[:8]
    if suite == "data" and data_variant == "requests":
        cases = REPORT.DATA_REQUEST_CASES
    signal_definitions = None
    if suite == "sre" and sre_signals:
        cases = REPORT.SRE_SIGNAL_CASES
        signal_definitions, _ = REPORT.registered_sre_signals()
    provider, model = ("shirokuma-mock", "shirokuma-deterministic-v1") if mock else ("mtplx", "qwen3.8-flash-next")
    task, trace = prefix + uuid.uuid4().hex, uuid.uuid4().hex
    attempts = []
    for case in cases:
        for repeat in (1, 2):
            event_id = str(uuid.uuid4())
            passed = len(attempts) != failed_index
            metrics = {"prompt_tokens": None if unknown_tokens else 10,
                       "completion_tokens": None if unknown_tokens else 2,
                       "duration_ms": 100 + len(attempts), "inference_cost_estimate": None}
            attempts.append({"case_id": case, "repeat": repeat, "result": "passed" if passed else "failed",
                "audit": "verified", "event_id": event_id, "metrics": metrics,
                "audited_event": {"action_type": action, "event_id": event_id, "task_id": task,
                    "trace_id": trace, "model_provider": provider, "model_name": model,
                    "target_system": "llm-mock" if mock else "llm-local-compatible",
                    "status": "success" if passed else "failed", **metrics}})
            if suite == "data" and data_variant is not None:
                operation = REPORT.DATA_EXPECTED[case]
                attempts[-1].update(expected_operation=operation,
                    plan={"schema_version": 1, "source": "fixture_v1", "operation": operation})
            if signal_definitions is not None and passed:
                triage = signal_triage(signal_definitions[case])
                attempts[-1].update(triage=triage, selected_tools=[check['tool'] for check in triage['checks']])
    if truncate is not None:
        attempts = attempts[:truncate]
    usage = {key: sum(a["metrics"][key] for a in attempts) if key == "duration_ms" or (attempts and not unknown_tokens) else None
             for key in REPORT.METRICS}
    usage["inference_cost_estimate"] = None
    return {"result": "failed" if failed_index is not None or truncate is not None else "passed",
            "task_id": task, "trace_id": trace, "provider": provider, "model": model, "retries": 0,
            "requested_inferences": len(cases) * 2, "attempted_inferences": len(attempts),
            "passed_inferences": sum(a["result"] == "passed" for a in attempts),
            "audited_inferences": len(attempts), "elapsed_ms": 1000,
            "attempts": attempts, "usage": usage,
            **({"suite": data_variant, "dataset_sha256": REPORT.DATA_DATASET_HASHES[data_variant]}
               if suite == "data" and data_variant is not None else {}),
            **({"suite": REPORT.SRE_SIGNAL_SUITE, "dataset_sha256": REPORT.SRE_DATASET_HASHES[REPORT.SRE_SIGNAL_SUITE]}
               if suite == "sre" and sre_signals else {})}


class EvaluationReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        for directory, *_ in REPORT.SUITES.values():
            (self.base / directory).mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, suite, value, *, token=None, log=True):
        directory, filename, *_ = REPORT.SUITES[suite]
        path = self.base / directory / (token or uuid.uuid4().hex)
        path.mkdir()
        (path / filename).write_text(json.dumps(value))
        if log:
            (path / "attempts.jsonl").write_text("".join(json.dumps(a) + "\n" for a in value.get("attempts", [])))
        return path

    def complete_all(self):
        for suite in REPORT.SUITES:
            self.save(suite, receipt(suite))

    def test_every_failed_run_counts_even_when_a_newer_success_exists(self):
        self.complete_all()
        failed = self.save("inventory", receipt(failed_index=0))
        os.utime(failed / "summary.json", (1, 1))
        result = REPORT.collect(self.base)
        group = next(g for g in result["groups"] if g["suite"] == "inventory")
        self.assertEqual(result["result"], "complete")
        self.assertEqual(result["coverage"]["reported_failed_runs"], 1)
        self.assertEqual(group["functional_success_observed"], {"numerator": 19, "denominator": 20, "value": .95})
        self.assertEqual(group["first_repeat_success"]["numerator"], 9)
        self.assertEqual(group["first_repeat_success"]["denominator"], 10)
        self.assertTrue(all(r["started_at"] is None for r in result["runs"]))

    def test_mock_and_old_sre_case_signatures_stay_separate(self):
        self.complete_all()
        self.save("inventory", receipt(mock=True, unknown_tokens=True))
        self.save("sre", receipt("sre", old_sre=True))
        self.save("sre", receipt("sre", platform_sre=True))
        result = REPORT.collect(self.base)
        self.assertEqual(len(result["groups"]), len(REPORT.SUITES) + 3)
        sre = [g for g in result["groups"] if g["suite"] == "sre"]
        self.assertEqual({g["requested_inferences"] for g in sre}, {12, 16, 32})
        self.assertEqual(len({g["case_signature"] for g in sre}), 3)
        mock = next(g for g in result["groups"] if g["provider_kind"] == "mock")
        self.assertIsNone(mock["usage"]["prompt_tokens"]["total"])
        self.assertEqual(mock["usage"]["prompt_tokens"]["missing_calls"], 10)

    def test_container_case_cannot_be_substituted_into_historical_sre_receipt(self):
        value = receipt("sre", platform_sre=True)
        value['attempts'][0]['case_id'] = 'container_exit_nonzero'
        with self.assertRaises(REPORT.ReceiptError):
            REPORT.normalize(value, 'sre')

    def test_incomplete_container_evaluation_preserves_the_full_new_denominator(self):
        self.save('sre', receipt('sre', failed_index=16, truncate=17))
        result = REPORT.collect(self.base)
        group = result['groups'][0]
        self.assertEqual(group['requested_inferences'], 32)
        self.assertEqual(group['attempted_inferences'], 17)
        self.assertEqual(group['functional_success_observed']['numerator'], 16)
        self.assertEqual(group['functional_success_observed']['denominator'], 17)

    def test_partial_failed_evaluation_preserves_unattempted_denominator(self):
        self.save("data", receipt("data", failed_index=0, truncate=1))
        result = REPORT.collect(self.base)
        group = result["groups"][0]
        self.assertEqual(group["attempted_inferences"], 1)
        self.assertEqual(group["unattempted_inferences"], 11)
        self.assertEqual(group["functional_success_observed"]["denominator"], 1)
        self.assertEqual(group["functional_success_requested"]["denominator"], 12)
        self.assertEqual(group["audit_coverage"]["value"], 1)
        self.assertEqual(group["first_repeat_coverage"]["denominator"], 6)

    def test_unknown_token_is_not_zero_or_a_complete_total(self):
        value = receipt()
        value["attempts"][0]["metrics"]["completion_tokens"] = None
        value["attempts"][0]["audited_event"]["completion_tokens"] = None
        value["usage"]["completion_tokens"] = None
        self.save("inventory", value)
        metric = REPORT.collect(self.base)["groups"][0]["usage"]["completion_tokens"]
        self.assertIsNone(metric["total"])
        self.assertEqual((metric["known_subtotal"], metric["known_calls"], metric["missing_calls"], metric["attempted_calls"]),
                         (18, 9, 1, 10))

    def test_false_success_or_counts_and_usage_mismatch_are_invalid(self):
        for key, altered in (("passed_inferences", 999), ("attempted_inferences", True), ("usage", {})):
            with self.subTest(key=key):
                value = receipt()
                value[key] = altered
                path = self.save("inventory", value)
                run = REPORT.read_run(self.base, "inventory", path.name)
                self.assertEqual(run["integrity"], "invalid")
                self.assertEqual(run["_attempts"], [])
        value = receipt(failed_index=0)
        value["result"] = "passed"
        path = self.save("inventory", value)
        self.assertEqual(REPORT.read_run(self.base, "inventory", path.name)["issues"], ["false_passed_result"])

    def test_saved_audit_missing_wrong_trace_or_metric_is_not_verified(self):
        for key, altered in (("trace_id", "0" * 32), ("prompt_tokens", 999), ("status", "failed")):
            value = receipt()
            value["attempts"][0]["audited_event"][key] = altered
            path = self.save("inventory", value)
            self.assertEqual(REPORT.read_run(self.base, "inventory", path.name)["issues"], ["audit_correlation_mismatch"])

    def test_audit_failure_can_be_counted_without_hiding_functional_success(self):
        value = receipt(truncate=1)
        value["attempts"][0]["audit"] = "failed"
        value["attempts"][0].pop("audited_event")
        value["audited_inferences"] = 0
        self.save("inventory", value)
        group = REPORT.collect(self.base)["groups"][0]
        self.assertEqual(group["functional_success_observed"]["value"], 1)
        self.assertEqual(group["audit_coverage"]["value"], 0)
        self.assertEqual(group["failed_runs"], 1)

    def test_duplicate_executions_are_explicit_and_never_double_counted(self):
        value = receipt()
        self.save("inventory", value)
        self.save("inventory", value)
        result = REPORT.collect(self.base)
        self.assertEqual(result["coverage"]["duplicate_runs"], 2)
        self.assertEqual(result["groups"], [])
        self.assertTrue(all(r["issues"] == ["duplicate_execution_identity"] for r in result["runs"]))

    def test_attempt_log_mismatch_missing_receipt_and_setup_failure_remain_visible(self):
        first = self.save("inventory", receipt())
        (first / "attempts.jsonl").write_text("")
        self.save("data", {"result": "failed", "failure_code": "raw secret error"}, log=False)
        (self.base / "catalog-evaluation" / uuid.uuid4().hex).mkdir()
        result = REPORT.collect(self.base)
        self.assertEqual(result["coverage"]["discovered_runs"], 3)
        self.assertEqual(result["coverage"]["invalid_runs"], 1)
        self.assertEqual(result["coverage"]["incomplete_runs"], 2)
        self.assertEqual(result["coverage"]["reported_failed_runs"], 1)
        self.assertNotIn("raw secret", json.dumps(result))

    def test_unsafe_files_and_malformed_json_do_not_escape_fixed_roots(self):
        path = self.save("inventory", receipt())
        (path / "summary.json").unlink()
        outside = self.base / "outside.json"
        outside.write_text('{"secret":"DO_NOT_READ"}')
        (path / "summary.json").symlink_to(outside)
        result = REPORT.collect(self.base)
        self.assertEqual(result["runs"][0]["issues"], ["unsafe_or_oversized_file"])
        self.assertNotIn("DO_NOT_READ", json.dumps(result))
        (path / "summary.json").unlink()
        (path / "summary.json").write_text('{"result":"passed","result":"failed"}')
        self.assertEqual(REPORT.collect(self.base)["runs"][0]["issues"], ["duplicate_json_key"])

    def test_unobserved_suites_are_not_zero_percent_or_passed(self):
        result = REPORT.collect(self.base)
        self.assertEqual(result["result"], "incomplete")
        self.assertEqual(set(result["coverage"]["unobserved_suites"]), set(REPORT.SUITES))
        self.assertEqual(result["groups"], [])

    def test_new_query_suite_is_explicitly_unobserved_until_a_receipt_exists(self):
        for suite in REPORT.SUITES:
            if suite != "query-doctor":
                self.save(suite, receipt(suite))
        result = REPORT.collect(self.base)
        self.assertEqual(result["result"], "incomplete")
        self.assertEqual(result["coverage"]["unobserved_suites"], ["query-doctor"])
        self.save("query-doctor", receipt("query-doctor"))
        result = REPORT.collect(self.base)
        self.assertEqual(result["result"], "complete")
        query = next(group for group in result["groups"] if group["suite"] == "query-doctor")
        self.assertEqual(query["requested_inferences"], 16)

    def test_policy_suite_missing_then_failed_receipt_remains_in_denominators(self):
        for suite in REPORT.SUITES:
            if suite != "policy-gatekeeper":
                self.save(suite, receipt(suite))
        result = REPORT.collect(self.base)
        self.assertEqual(result["result"], "incomplete")
        self.assertEqual(result["coverage"]["unobserved_suites"], ["policy-gatekeeper"])
        self.save("policy-gatekeeper", receipt("policy-gatekeeper", failed_index=0))
        self.save("policy-gatekeeper", receipt("policy-gatekeeper"))
        result = REPORT.collect(self.base)
        self.assertEqual(result["result"], "complete")
        self.assertEqual(result["coverage"]["reported_failed_runs"], 1)
        group = next(g for g in result["groups"] if g["suite"] == "policy-gatekeeper")
        self.assertEqual(group["functional_success_requested"], {"numerator": 31, "denominator": 32, "value": 31 / 32})
        self.assertEqual(group["first_repeat_success"]["denominator"], 16)
        self.assertEqual(group["audit_coverage"]["denominator"], 32)

    def test_no_raw_extras_or_free_text_enter_json_or_markdown(self):
        value = receipt()
        value.update(prompt="AUTH_SECRET", sql="DROP SECRET_TABLE", error="RAW_ERROR")
        value["attempts"][0]["content"] = {"secret": "MODEL_SECRET"}
        self.save("inventory", value)
        result = REPORT.collect(self.base)
        rendered = json.dumps(result) + REPORT.markdown(result)
        for forbidden in ("AUTH_SECRET", "SECRET_TABLE", "RAW_ERROR", "MODEL_SECRET"):
            self.assertNotIn(forbidden, rendered)

    def test_private_output_and_honest_metric_limits(self):
        self.complete_all()
        result = REPORT.collect(self.base)
        output = REPORT.write_report(result, self.base)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((output / "report.json").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((output / "report.md").stat().st_mode), 0o600)
        self.assertEqual(json.loads((output / "report.json").read_text()), result)
        self.assertIsNone(result["unknown"]["chronological_first_attempt_success"])
        self.assertIn("runtime_success_rate", result["not_applicable"])
        self.assertTrue(all(g["fixture_content_comparability"] == "unknown" for g in result["groups"]))
        content = (output / "report.md").read_text()
        self.assertIn(result["groups"][0]["case_signature"][:12], content)
        self.assertIn("p50 / p95", content)
        self.assertIn("known subtotal (calls)", content)
        metric = result["groups"][0]["usage"]["duration_ms"]
        self.assertEqual(metric["p95"], 111)

    def test_data_legacy_baseline_and_requests_have_separate_case_signatures(self):
        self.complete_all()
        self.save("data", receipt("data", data_variant="baseline"))
        self.save("data", receipt("data", data_variant="requests"))
        with patch('urllib.request.urlopen', side_effect=AssertionError('no HTTP')), patch('socket.create_connection', side_effect=AssertionError('no HTTP')):
            report = REPORT.collect(self.base)
        groups = {g["evaluation_suite"]: g for g in report["groups"] if g["suite"] == "data"}
        self.assertEqual(set(groups), {"baseline", "requests"})
        self.assertEqual(groups["baseline"]["runs"], 2)
        self.assertEqual(groups["baseline"]["requested_inferences"], 24)
        self.assertEqual(groups["requests"]["requested_inferences"], 14)
        self.assertNotEqual(groups["baseline"]["case_signature"], groups["requests"]["case_signature"])
        data_runs = [r for r in report["runs"] if r["suite"] == "data"]
        legacy = next(r for r in data_runs if r["dataset_binding"] == "legacy-unrecorded")
        self.assertIsNone(legacy["dataset_sha256"])
        self.assertTrue(all(r["integrity"] == "valid" for r in data_runs))
        rendered = REPORT.markdown(report)
        self.assertIn('data/baseline (6;', rendered)
        self.assertIn('data/requests (7;', rendered)

    def test_data_unknown_suite_missing_hash_or_altered_registered_hash_rejected(self):
        for field, altered, code in [
            ('suite', 'other', 'unrecognized_data_suite'),
            ('suite', None, 'unrecognized_data_suite'),
            ('dataset_sha256', None, 'data_dataset_hash_mismatch'),
            ('dataset_sha256', 'f' * 64, 'data_dataset_hash_mismatch'),
            ('dataset_sha256', REPORT.DATA_DATASET_HASHES['baseline'], 'data_dataset_hash_mismatch'),
        ]:
            value = receipt('data', data_variant='requests')
            value[field] = altered
            with self.subTest(field=field, altered=altered), self.assertRaisesRegex(REPORT.ReceiptError, code):
                REPORT.normalize(value, 'data')
        for variant in ('baseline', 'requests'):
            value = receipt('data', data_variant=variant)
            del value['dataset_sha256']
            with self.subTest(missing=variant), self.assertRaisesRegex(REPORT.ReceiptError, 'data_dataset_hash_mismatch'):
                REPORT.normalize(value, 'data')
        value = receipt('data', data_variant='requests')
        del value['suite']; del value['dataset_sha256']
        with self.assertRaisesRegex(REPORT.ReceiptError, 'unrecognized_case_suite'):
            REPORT.normalize(value, 'data')

    def test_data_requests_requires_exact_seven_case_order_count_and_expected_operation(self):
        variants = []
        value = receipt('data', data_variant='requests'); value['requested_inferences'] = 12
        variants.append((value, 'unrecognized_case_suite'))
        value = receipt('data', data_variant='requests'); value['attempts'][0]['case_id'] = 'altered'
        variants.append((value, 'attempt_order_duplicate_or_missing'))
        value = receipt('data', data_variant='requests'); value['attempts'][0], value['attempts'][2] = value['attempts'][2], value['attempts'][0]
        variants.append((value, 'attempt_order_duplicate_or_missing'))
        value = receipt('data', data_variant='requests'); value['attempts'][6]['expected_operation'] = 'row_count'
        variants.append((value, 'data_expected_operation_mismatch'))
        value = receipt('data', data_variant='requests'); del value['attempts'][0]['expected_operation']
        variants.append((value, 'data_expected_operation_mismatch'))
        for value, code in variants:
            with self.subTest(code=code), self.assertRaisesRegex(REPORT.ReceiptError, code):
                REPORT.normalize(value, 'data')

    def test_data_false_semantic_pass_and_raw_sql_plan_are_rejected(self):
        value = receipt('data', data_variant='requests')
        value['attempts'][6]['plan']['operation'] = 'row_count'
        with self.assertRaisesRegex(REPORT.ReceiptError, 'data_false_semantic_pass'):
            REPORT.normalize(value, 'data')
        for change in ('sql', 'version', 'missing'):
            value = receipt('data', data_variant='requests')
            if change == 'sql':
                value['attempts'][0]['plan']['sql'] = 'DO NOT COPY MODEL SQL'
            elif change == 'version':
                value['attempts'][0]['plan']['schema_version'] = True
            else:
                del value['attempts'][0]['plan']
            with self.subTest(change=change), self.assertRaisesRegex(REPORT.ReceiptError, 'invalid_data_plan_projection'):
                REPORT.normalize(value, 'data')

    def test_data_failed_and_incomplete_request_runs_are_not_hidden(self):
        failed = receipt('data', data_variant='requests', failed_index=0)
        failed['attempts'][0]['plan']['operation'] = 'row_count'
        self.save('data', failed)
        partial = receipt('data', data_variant='requests', truncate=1)
        self.save('data', partial)
        self.save('data', receipt('data', data_variant='requests'))
        report = REPORT.collect(self.base)
        group = next(g for g in report['groups'] if g['suite'] == 'data')
        self.assertEqual(group['runs'], 3)
        self.assertEqual(group['failed_runs'], 2)
        self.assertEqual(group['requested_inferences'], 42)
        self.assertEqual(group['attempted_inferences'], 29)
        self.assertEqual(group['functional_success_observed']['numerator'], 28)

    def test_data_raw_requests_responses_and_free_text_are_not_projected(self):
        value = receipt('data', data_variant='requests')
        value['raw_request'] = 'PRIVATE REQUEST'
        value['attempts'][0]['raw_response'] = 'PRIVATE MODEL RESPONSE'
        self.save('data', value)
        report = REPORT.collect(self.base)
        rendered = json.dumps(report) + REPORT.markdown(report)
        self.assertNotIn('PRIVATE REQUEST', rendered)
        self.assertNotIn('PRIVATE MODEL RESPONSE', rendered)
        run = next(r for r in report['runs'] if r['suite'] == 'data')
        self.assertEqual(run['dataset_sha256'], REPORT.DATA_DATASET_HASHES['requests'])
        self.assertIsNone(run['fixture_content_sha256'])
        self.assertIsNone(run['evaluator_revision'])

    def test_registered_data_hashes_match_checked_in_canonical_golden_definitions(self):
        spec = importlib.util.spec_from_file_location('report_data_contract', ROOT / 'tools/local_experiment/bears/data_contract.py')
        contract = importlib.util.module_from_spec(spec); spec.loader.exec_module(contract)
        datasets = {'baseline': contract.model_cases(),
                    'requests': json.loads((ROOT / 'tools/local_experiment/bears/data_request_cases.json').read_text())['cases']}
        for label, cases in datasets.items():
            actual = hashlib.sha256(json.dumps(cases, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')).hexdigest()
            self.assertEqual(actual, REPORT.DATA_DATASET_HASHES[label])
            expected_ids = REPORT.DATA_REQUEST_CASES if label == 'requests' else REPORT.SUITES['data'][4]
            self.assertEqual(tuple(case['case_id'] for case in cases), expected_ids)
            self.assertEqual({case['case_id']: case['expected_operation'] for case in cases},
                             {case: REPORT.DATA_EXPECTED[case] for case in expected_ids})

    def test_sre_signal_identity_precedes_old_sixteen_requested_case_selection(self):
        self.complete_all()
        self.save('sre', receipt('sre', old_sre=True))
        self.save('sre', receipt('sre', platform_sre=True))
        self.save('sre', receipt('sre', sre_signals=True))
        with patch('urllib.request.urlopen', side_effect=AssertionError('no HTTP')), patch('socket.create_connection', side_effect=AssertionError('no HTTP')):
            result = REPORT.collect(self.base)
        groups = [g for g in result['groups'] if g['suite'] == 'sre']
        self.assertEqual(len(groups), 4)
        self.assertEqual(sorted(g['requested_inferences'] for g in groups), [12, 16, 16, 32])
        self.assertEqual(len({g['case_signature'] for g in groups}), 4)
        new = next(g for g in groups if 'evaluation_suite' in g)
        self.assertEqual(new['evaluation_suite'], 'observation-signals-v1')
        self.assertEqual(new['case_ids'], list(REPORT.SRE_SIGNAL_CASES))
        self.assertEqual(new['fixture_content_comparability'], 'unknown')
        run = next(r for r in result['runs'] if r.get('evaluation_suite') == REPORT.SRE_SIGNAL_SUITE)
        self.assertEqual(run['dataset_binding'], 'registered-golden-hash-matched')
        self.assertIsNone(run['fixture_content_sha256'])
        self.assertIsNone(run['evaluator_revision'])
        self.assertIn('sre/observation-signals-v1 (8;', REPORT.markdown(result))
        self.assertNotIn('triage', json.dumps(result))

    def test_sre_field_free_legacy_shape_and_case_signatures_are_unchanged(self):
        variants = [({'old_sre': True}, 'bef9b768328073392dae32d319304b066eda3ae8be0564d0abb3335317cd080b'),
                    ({'platform_sre': True}, '8a0a0ad37b19f9c9556c0e3a2286dfd647cd82626be53fecd8ec6a96f4548746'),
                    ({}, '8a98e35c3f8fc420758c7dda693574d6a746652b8bb3abcc39078816e340f23d')]
        expected_keys = {'_attempts', 'case_ids', 'case_signature', 'elapsed_ms', 'model',
            'model_identity_sha256', 'provider', 'provider_kind', 'reported_result', 'requested_inferences',
            'task_id', 'trace_id'}
        with patch.object(REPORT, 'registered_sre_signals', side_effect=AssertionError('legacy must not load new cases')):
            for variant, signature in variants:
                value = receipt('sre', **variant)
                with self.subTest(variant=variant):
                    result = REPORT.normalize(value, 'sre')
                    self.assertEqual(set(result), expected_keys)
                    self.assertEqual(result['case_signature'], signature)
                    self.assertTrue(all('triage' not in row for row in value['attempts']))

    def test_sre_unknown_suite_bad_hash_and_partial_identity_never_fall_back(self):
        for field, altered, code in [
            ('suite', 'baseline', 'unrecognized_sre_suite'),
            ('suite', 'requests', 'unrecognized_sre_suite'),
            ('suite', 'UNTRUSTED_LABEL', 'unrecognized_sre_suite'),
            ('suite', None, 'unrecognized_sre_suite'),
            ('suite', [], 'unrecognized_sre_suite'),
            ('dataset_sha256', None, 'sre_dataset_hash_mismatch'),
            ('dataset_sha256', False, 'sre_dataset_hash_mismatch'),
            ('dataset_sha256', 'f' * 64, 'sre_dataset_hash_mismatch'),
            ('dataset_sha256', REPORT.DATA_DATASET_HASHES['baseline'], 'sre_dataset_hash_mismatch'),
        ]:
            value = receipt('sre', sre_signals=True)
            value[field] = altered
            with self.subTest(field=field, altered=altered), self.assertRaisesRegex(REPORT.ReceiptError, code):
                REPORT.normalize(value, 'sre')
        for missing in ('suite', 'dataset_sha256'):
            value = receipt('sre', sre_signals=True)
            del value[missing]
            with self.subTest(missing=missing), self.assertRaisesRegex(REPORT.ReceiptError, 'incomplete_sre_suite_identity'):
                REPORT.normalize(value, 'sre')
        value = receipt('sre', sre_signals=True)
        del value['suite']; del value['dataset_sha256']
        with self.assertRaisesRegex(REPORT.ReceiptError, 'attempt_order_duplicate_or_missing'):
            REPORT.normalize(value, 'sre')

    def test_sre_new_suite_requires_exact_count_order_and_unique_attempts(self):
        edits = [
            (lambda x: x.update(requested_inferences=32), 'unrecognized_case_suite'),
            (lambda x: x['attempts'][0].update(case_id='intentional_flux_only'), 'attempt_order_duplicate_or_missing'),
            (lambda x: x['attempts'][1].update(repeat=1), 'attempt_order_duplicate_or_missing'),
            (lambda x: x['attempts'][1].update(repeat=True), 'attempt_order_duplicate_or_missing'),
            (lambda x: x['attempts'].__setitem__(slice(0, 2), list(reversed(x['attempts'][:2]))), 'attempt_order_duplicate_or_missing'),
            (lambda x: x['attempts'][1].update(event_id=x['attempts'][0]['event_id']), 'duplicate_event_identity'),
        ]
        for edit, code in edits:
            value = receipt('sre', sre_signals=True)
            edit(value)
            with self.subTest(code=code), self.assertRaisesRegex(REPORT.ReceiptError, code):
                REPORT.normalize(value, 'sre')
        legacy = receipt('sre', platform_sre=True)
        legacy.update(suite=REPORT.SRE_SIGNAL_SUITE, dataset_sha256=REPORT.SRE_DATASET_HASHES[REPORT.SRE_SIGNAL_SUITE])
        with self.assertRaisesRegex(REPORT.ReceiptError, 'attempt_order_duplicate_or_missing'):
            REPORT.normalize(legacy, 'sre')

    def test_sre_signal_duplicate_execution_is_visible_and_not_double_counted(self):
        value = receipt('sre', sre_signals=True)
        self.save('sre', value); self.save('sre', copy.deepcopy(value))
        result = REPORT.collect(self.base)
        self.assertEqual(result['coverage']['duplicate_runs'], 2)
        self.assertEqual(result['groups'], [])
        self.assertTrue(all(row['issues'] == ['duplicate_execution_identity'] for row in result['runs']))

    def test_sre_signal_pass_requires_exact_safe_triage_critical_coverage_and_selected_tools(self):
        edits = [lambda a: a.pop('triage'), lambda a: a.update(triage=None),
                 lambda a: a['triage'].update(raw_response='PRIVATE MODEL TEXT'),
                 lambda a: a['triage'].update(root_cause='OOM'),
                 lambda a: a['triage'].update(assessment='healthy-observed-scope'),
                 lambda a: a['triage']['checks'][0].update(tool='shell'),
                 lambda a: a['triage']['checks'][0].update(finding_ids=['fabricated']),
                 lambda a: a['triage']['checks'].pop()]
        for edit in edits:
            value = receipt('sre', sre_signals=True)
            attempt = next(a for a in value['attempts'] if a['case_id'] == 'signals_dual_critical')
            edit(attempt)
            with self.subTest(edit=edit), self.assertRaisesRegex(REPORT.ReceiptError, 'invalid_sre_signal_triage'):
                REPORT.normalize(value, 'sre')
        for altered in (None, [], ['shell']):
            value = receipt('sre', sre_signals=True)
            attempt = next(a for a in value['attempts'] if a['case_id'] == 'signals_dual_critical')
            attempt['selected_tools'] = altered
            with self.subTest(selected=altered), self.assertRaisesRegex(REPORT.ReceiptError, 'sre_selected_tools_mismatch'):
                REPORT.normalize(value, 'sre')

    def test_sre_failed_partial_signals_keep_denominator_without_fabricating_triage(self):
        partial = receipt('sre', sre_signals=True, failed_index=0, truncate=1)
        self.assertNotIn('triage', partial['attempts'][0])
        partial['attempts'][0]['raw_response'] = 'PRIVATE FAILED PROPOSAL'
        self.save('sre', partial)
        self.save('sre', receipt('sre', sre_signals=True))
        result = REPORT.collect(self.base)
        group = result['groups'][0]
        self.assertEqual((group['runs'], group['failed_runs']), (2, 1))
        self.assertEqual((group['requested_inferences'], group['attempted_inferences']), (32, 17))
        self.assertEqual(group['functional_success_observed'], {'numerator': 16, 'denominator': 17, 'value': 16 / 17})
        self.assertEqual(group['functional_success_requested']['denominator'], 32)
        self.assertNotIn('PRIVATE FAILED PROPOSAL', json.dumps(result))
        self.assertNotIn('triage', json.dumps(result))

    def test_sre_registered_golden_hash_and_ids_are_checked_before_rescoring(self):
        definitions, validate = REPORT.registered_sre_signals()
        self.assertEqual(tuple(definitions), REPORT.SRE_SIGNAL_CASES)
        self.assertFalse(set(definitions) & set(REPORT.SUITES['sre'][4]))
        self.assertEqual(len(definitions), 8)
        raw = json.dumps(list(definitions.values()), sort_keys=True, separators=(',', ':'),
                         ensure_ascii=False).encode('utf-8')
        self.assertEqual(hashlib.sha256(raw).hexdigest(), REPORT.SRE_DATASET_HASHES[REPORT.SRE_SIGNAL_SUITE])
        for case in definitions.values():
            triage = signal_triage(case)
            self.assertEqual(validate(case, triage), triage)
        with patch.dict(REPORT.SRE_DATASET_HASHES, {REPORT.SRE_SIGNAL_SUITE: 'f' * 64}):
            with self.assertRaisesRegex(REPORT.ReceiptError, 'sre_registered_dataset_mismatch'):
                REPORT.registered_sre_signals()


if __name__ == "__main__":
    unittest.main()
