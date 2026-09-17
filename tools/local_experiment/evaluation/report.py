#!/usr/bin/env python3
"""Report every retained local evaluation receipt without running a model or service."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import uuid

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "tmp/local-experiment"
MAX_BYTES = 16 * 1024 * 1024
MAX_RUNS = 1000
SUITES = {
    "inventory": ("llm-evaluation", "summary.json", "llm-eval-", "llm.eval_summary", (
        "empty_no_run", "single_no_run", "renamed_three_verified", "unverified_record", "sixteen_names_boundary")),
    "sre": ("sre-evaluation", "result.json", "sre-eval-", "llm.sre_eval", (
        "intentional_flux_only", "historical_restart", "critical_pod", "warning_event", "doctor_degraded",
        "event_list_truncated", "runtime_degraded", "runtime_unknown")),
    "data": ("data-evaluation", "result.json", "data-eval-", "llm.data_eval", (
        "uppercase_names_ja", "name_lengths_ja", "row_count_ja", "delete_rows_unsupported_ja",
        "unknown_column_unsupported_ja", "name_lengths_en")),
    "catalog": ("catalog-evaluation", "result.json", "catalog-eval-", "llm.catalog_eval", (
        "all_fields_missing", "descriptions_only", "owner_and_lineage", "already_curated",
        "unverified_evidence", "unrelated_scope")),
    "query-doctor": ("query-evaluation", "result.json", "query-eval-", "llm.query_eval", (
        "complete_observations", "projection_estimates_unknown", "both_estimates_unknown", "history_missing",
        "estimates_and_history_unknown", "zero_is_known", "failed_captured_query", "history_timing_unknown")),
}
TOKENS = ("prompt_tokens", "completion_tokens")
METRICS = (*TOKENS, "duration_ms")


class ReceiptError(ValueError):
    """Only the fixed error code is exposed in reports."""


def require(condition, code):
    if not condition:
        raise ReceiptError(code)


def integer(value):
    return type(value) is int and 0 <= value <= 2**64 - 1


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result

    def invalid_constant(_):
        raise ReceiptError("non_finite_json")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except ReceiptError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise ReceiptError("invalid_json") from None


def read_file(path):
    try:
        before = path.lstat()
        require(stat.S_ISREG(before.st_mode) and before.st_size <= MAX_BYTES, "unsafe_or_oversized_file")
        with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb") as stream:
            opened = os.fstat(stream.fileno())
            raw = stream.read(MAX_BYTES + 1)
            after = os.fstat(stream.fileno())
        require(len(raw) <= MAX_BYTES and (before.st_dev, before.st_ino) == (opened.st_dev, opened.st_ino),
                "unsafe_or_oversized_file")
        require((opened.st_size, opened.st_mtime_ns, opened.st_ino) ==
                (after.st_size, after.st_mtime_ns, after.st_ino), "file_changed_during_read")
        return raw, hashlib.sha256(raw).hexdigest()
    except ReceiptError:
        raise
    except OSError:
        raise ReceiptError("file_unavailable") from None


def _uuid(value):
    try:
        require(type(value) is str and str(uuid.UUID(value)) == value, "invalid_event_identity")
    except (ValueError, AttributeError):
        raise ReceiptError("invalid_event_identity") from None


def normalize(value, suite):
    """Recompute counts/usage and verify saved audit correlation, not live audit availability."""
    require(type(value) is dict and value.get("result") in {"passed", "failed"}, "invalid_result")
    directory, filename, task_prefix, action, current_cases = SUITES[suite]
    requested = value.get("requested_inferences")
    require(integer(requested), "missing_or_invalid_requested_count")
    cases = current_cases[:6] if suite == "sre" and requested == 12 else current_cases
    require(requested == len(cases) * 2, "unrecognized_case_suite")
    require(type(value.get("retries")) is int and value["retries"] == 0, "unsupported_retry_semantics")
    task, trace = value.get("task_id"), value.get("trace_id")
    require(type(task) is str and re.fullmatch(re.escape(task_prefix) + "[0-9a-f]{32}", task)
            and type(trace) is str and re.fullmatch("[0-9a-f]{32}", trace), "invalid_run_identity")
    provider, model = value.get("provider"), value.get("model")
    require(type(provider) is str and type(model) is str and len(provider) <= 256 and len(model) <= 256,
            "invalid_model_identity")
    kind = ("mock" if (provider, model) == ("shirokuma-mock", "shirokuma-deterministic-v1") else
            "local-compatible" if (provider, model) == ("mtplx", "qwen3.8-flash-next") else "unrecognized")
    require(suite == "inventory" or kind != "mock", "unsupported_mock_suite")
    if "provider_kind" in value:
        require(value["provider_kind"] == kind, "provider_kind_mismatch")
    expected_pairs = [(case, repeat) for case in cases for repeat in (1, 2)]
    attempts = value.get("attempts")
    require(type(attempts) is list and len(attempts) <= requested, "invalid_attempt_list")
    seen, normalized = set(), []
    for index, attempt in enumerate(attempts):
        require(type(attempt) is dict, "invalid_attempt")
        require((attempt.get("case_id"), attempt.get("repeat")) == expected_pairs[index]
                and type(attempt.get("repeat")) is int, "attempt_order_duplicate_or_missing")
        require(attempt.get("result") in {"passed", "failed"}
                and attempt.get("audit") in {"verified", "failed", "pending"}, "invalid_attempt_status")
        event_id = attempt.get("event_id")
        _uuid(event_id)
        require(event_id not in seen, "duplicate_event_identity")
        seen.add(event_id)
        metrics = attempt.get("metrics")
        require(type(metrics) is dict and all(key in metrics and (metrics[key] is None or integer(metrics[key]))
                                            for key in METRICS), "invalid_attempt_metrics")
        require(integer(metrics["duration_ms"]) and metrics.get("inference_cost_estimate") is None,
                "invalid_attempt_metrics")
        if attempt["audit"] == "verified":
            audit = attempt.get("audited_event")
            expected = {"event_id": event_id, "task_id": task, "trace_id": trace,
                        "action_type": action, "target_system": "llm-mock" if kind == "mock" else "llm-local-compatible",
                        "model_provider": provider, "model_name": model,
                        "status": "success" if attempt["result"] == "passed" else "failed",
                        "inference_cost_estimate": None, **{key: metrics[key] for key in METRICS}}
            require(type(audit) is dict and all(type(audit.get(key)) is type(wanted) and audit.get(key) == wanted
                                               for key, wanted in expected.items()), "audit_correlation_mismatch")
        normalized.append({"case_id": attempt["case_id"], "repeat": attempt["repeat"],
                           "result": attempt["result"], "audited": attempt["audit"] == "verified",
                           "event_id": event_id, "metrics": {key: metrics[key] for key in METRICS}})
    counts = {"attempted_inferences": len(attempts),
              "passed_inferences": sum(x["result"] == "passed" for x in attempts),
              "audited_inferences": sum(x["audit"] == "verified" for x in attempts)}
    require(all(type(value.get(key)) is int and value[key] == number for key, number in counts.items()),
            "declared_count_mismatch")
    require(integer(value.get("elapsed_ms")), "invalid_elapsed_time")
    usage = value.get("usage")
    require(type(usage) is dict and usage.get("inference_cost_estimate") is None, "invalid_usage")
    for key in METRICS:
        known = [x["metrics"][key] for x in normalized if x["metrics"][key] is not None]
        total = sum(known) if key == "duration_ms" or (normalized and len(known) == len(normalized)) else None
        require(type(usage.get(key)) is type(total) and usage.get(key) == total, "declared_usage_mismatch")
        if key + "_known_calls" in usage:
            require(type(usage[key + "_known_calls"]) is int and usage[key + "_known_calls"] == len(known),
                    "declared_usage_mismatch")
    if value["result"] == "passed":
        require(len(attempts) == requested and counts["passed_inferences"] == requested
                and counts["audited_inferences"] == requested, "false_passed_result")
    return {"provider_kind": kind, "provider": provider if kind != "unrecognized" else "unrecognized",
            "model": model if kind != "unrecognized" else "unrecognized",
            "model_identity_sha256": digest([provider, model]), "case_ids": list(cases),
            "case_signature": digest({"suite": suite, "cases": cases, "independent_repeats": 2}),
            "requested_inferences": requested, "elapsed_ms": value["elapsed_ms"],
            "reported_result": value["result"], "task_id": task, "trace_id": trace, "_attempts": normalized}


def read_run(base, suite, token):
    directory, filename, *_ = SUITES[suite]
    path = base / directory / token
    result = {"suite": suite, "run_token": token, "integrity": "invalid", "issues": [],
              "reported_result": "unknown", "artifact_sha256": {},
              "fixture_content_sha256": None, "evaluator_revision": None,
              "started_at": None, "_attempts": []}
    try:
        require(not path.is_symlink() and path.is_dir(), "unsafe_run_directory")
        if not (path / filename).exists():
            result.update(integrity="incomplete", issues=["missing_result_receipt"])
            return result
        raw, sha = read_file(path / filename)
        result["artifact_sha256"][filename] = sha
        value = parse(raw)
        if type(value) is dict and value.get("result") in {"passed", "failed"}:
            result["reported_result"] = value["result"]
        if type(value) is dict and value.get("result") == "failed" and "attempts" not in value:
            result.update(integrity="incomplete", issues=["setup_failure_without_attempt_receipt"])
            return result
        normalized = normalize(value, suite)
        if not (path / "attempts.jsonl").exists():
            result.update(integrity="incomplete", issues=["missing_attempt_log"])
            return result
        raw_log, log_sha = read_file(path / "attempts.jsonl")
        result["artifact_sha256"]["attempts.jsonl"] = log_sha
        logs = [parse(line) for line in raw_log.splitlines() if line.strip()]
        require(digest(logs) == digest(value["attempts"]), "attempt_log_mismatch")
        require(read_file(path / filename)[1] == sha and read_file(path / "attempts.jsonl")[1] == log_sha,
                "receipt_changed_during_collection")
        result.update(normalized, integrity="valid")
    except ReceiptError as error:
        result["issues"] = [str(error)]
    except (TypeError, KeyError, ValueError, OSError):
        result["issues"] = ["malformed_receipt"]
    return result


def ratio(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "value": numerator / denominator if denominator else None}


def aggregate(runs):
    attempts = [attempt for run in runs for attempt in run["_attempts"]]
    requested = sum(run["requested_inferences"] for run in runs)
    first = [attempt for attempt in attempts if attempt["repeat"] == 1]
    result = {"runs": len(runs), "failed_runs": sum(run["reported_result"] == "failed" for run in runs),
              "requested_inferences": requested, "attempted_inferences": len(attempts),
              "unattempted_inferences": requested - len(attempts),
              "functional_success_observed": ratio(sum(a["result"] == "passed" for a in attempts), len(attempts)),
              "functional_success_requested": ratio(sum(a["result"] == "passed" for a in attempts), requested),
              "audit_coverage": ratio(sum(a["audited"] for a in attempts), len(attempts)),
              "attempt_coverage": ratio(len(attempts), requested),
              "first_repeat_success": ratio(sum(a["result"] == "passed" for a in first), len(first)),
              "first_repeat_coverage": ratio(len(first), requested // 2),
              "elapsed_ms": {"sum": sum(run["elapsed_ms"] for run in runs), "known_runs": len(runs)},
              "usage": {}}
    for key in METRICS:
        known = [a["metrics"][key] for a in attempts if a["metrics"][key] is not None]
        metric = {"total": sum(known) if attempts and len(known) == len(attempts) else None,
                  "known_subtotal": sum(known), "known_calls": len(known),
                  "missing_calls": len(attempts) - len(known), "attempted_calls": len(attempts)}
        if key == "duration_ms":
            values = sorted(known)
            metric.update(p50=values[math.ceil(len(values) * .50) - 1] if values else None,
                          p95=values[math.ceil(len(values) * .95) - 1] if values else None,
                          quantile_method="nearest-rank")
        result["usage"][key] = metric
    return result


def collect(base=BASE):
    """Inspect all fixed roots. Invalid/incomplete/duplicate runs stay visible."""
    base = Path(base)
    runs, discovery_issues, unobserved = [], [], []
    for suite, (directory, *_) in SUITES.items():
        path = base / directory
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            discovery_issues.append({"suite": suite, "code": "unsafe_suite_directory", "count": 1})
            unobserved.append(suite)
            continue
        if not path.exists():
            unobserved.append(suite)
            continue
        entries = sorted(path.iterdir(), key=lambda entry: entry.name)
        tokens = [p.name for p in entries if re.fullmatch("[0-9a-f]{32}", p.name)]
        unexpected = len(entries) - len(tokens)
        if unexpected:
            discovery_issues.append({"suite": suite, "code": "unrecognized_directory_entry", "count": unexpected})
        if len(tokens) > MAX_RUNS:
            discovery_issues.append({"suite": suite, "code": "scan_limit_exceeded", "count": len(tokens) - MAX_RUNS})
        if not tokens:
            unobserved.append(suite)
        runs.extend(read_run(base, suite, token) for token in tokens[:MAX_RUNS])
    identities = {}
    for index, run in enumerate(runs):
        if run["integrity"] == "valid":
            for kind, value in [("task", run["task_id"]), ("trace", run["trace_id"]),
                                *[("event", a["event_id"]) for a in run["_attempts"]]]:
                identities.setdefault((kind, value), set()).add(index)
    duplicates = {index for entries in identities.values() if len(entries) > 1 for index in entries}
    for index in duplicates:
        runs[index].update(integrity="duplicate", issues=["duplicate_execution_identity"])
    buckets = {}
    for run in runs:
        if run["integrity"] == "valid":
            key = (run["suite"], run["model_identity_sha256"], run["case_signature"])
            buckets.setdefault(key, []).append(run)
    groups = []
    for key, members in sorted(buckets.items()):
        first = members[0]
        groups.append({"suite": key[0], "provider_kind": first["provider_kind"],
                       "provider": first["provider"], "model": first["model"],
                       "model_identity_sha256": key[1], "case_signature": key[2],
                       "case_ids": first["case_ids"], "fixture_content_comparability": "unknown",
                       "run_tokens": [r["run_token"] for r in members], **aggregate(members)})
    invalid = sum(run["integrity"] != "valid" for run in runs)
    failures = sum(run["reported_result"] == "failed" for run in runs)
    return {"schema_version": 1, "observed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "mode": "read-only-retained-receipt-report", "result": "complete" if not (invalid or discovery_issues or unobserved) else "incomplete",
            "selection": "all discovered run directories; no latest or success-only filtering",
            "scope": "saved synthetic evaluation outcomes; no live runtime or fresh audit verification",
            "coverage": {"discovered_runs": len(runs), "valid_runs": len(runs) - invalid,
                         "invalid_runs": sum(r["integrity"] == "invalid" for r in runs),
                         "incomplete_runs": sum(r["integrity"] == "incomplete" for r in runs),
                         "duplicate_runs": len(duplicates), "reported_failed_runs": failures,
                         "unobserved_suites": unobserved},
            "discovery_issues": discovery_issues, "groups": groups,
            "runs": [{key: value for key, value in run.items() if not key.startswith("_")} for run in runs],
            "not_applicable": {key: "not measured by retained synthetic receipts" for key in
                               ("pr_success_rate", "pr_revision_rate", "mttp", "runtime_success_rate", "production_readiness")},
            "unknown": {"inference_cost": None, "fixture_content_revision": None,
                        "evaluator_code_revision": None, "chronological_first_attempt_success": None,
                        "correction_rate": None},
            "limits": ["First repeat is an independent sample, not a chronological retry or correction.",
                       "Case signatures identify case IDs and repeat count, not historical fixture content or evaluator code.",
                       "Groups contain only internally consistent receipts; excluded runs and unattempted calls remain explicit.",
                       "Saved audit correlation is historical evidence, not current AgentHouse availability.",
                       "Local artifact hashes detect changes; they are not independently signed provenance."]}


def markdown(report):
    lines = ["# Local evaluation receipt report", "", "Observed: " + report["observed_at"], "",
             "All retained UUID runs are inspected, including failed and incomplete runs. No models or services were called.", "",
             "| Suite (cases; signature) | Provider / model | Runs | Functional pass / attempted | Attempted / requested | Audit verified / attempted |",
             "| --- | --- | ---: | ---: | ---: | ---: |"]
    for group in report["groups"]:
        functional, audit = group["functional_success_observed"], group["audit_coverage"]
        lines.append(f"| {group['suite']} ({len(group['case_ids'])}; {group['case_signature'][:12]}) | {group['provider']} / {group['model']} | {group['runs']} | "
                     f"{functional['numerator']} / {functional['denominator']} | {group['attempted_inferences']} / "
                     f"{group['requested_inferences']} | {audit['numerator']} / {audit['denominator']} |")
    lines += ["", "| Suite (cases; signature) / provider | First repeat passed / observed | Inference p50 / p95 ms | Prompt tokens: total; known subtotal (calls) | Completion tokens: total; known subtotal (calls) |",
              "| --- | ---: | ---: | ---: | ---: |"]
    for group in report["groups"]:
        first, duration = group["first_repeat_success"], group["usage"]["duration_ms"]
        tokens = []
        for key in TOKENS:
            metric = group["usage"][key]
            total = str(metric["total"]) if metric["total"] is not None else "unknown"
            tokens.append(f"{total}; {metric['known_subtotal']} ({metric['known_calls']} / {metric['attempted_calls']})")
        lines.append(f"| {group['suite']} ({len(group['case_ids'])}; {group['case_signature'][:12]}) / {group['provider']} | "
                     f"{first['numerator']} / {first['denominator']} | {duration['p50']} / {duration['p95']} | "
                     f"{tokens[0]} | {tokens[1]} |")
    lines += ["", "Coverage: " + json.dumps(report["coverage"], sort_keys=True), "",
              "PR metrics, MTTP, runtime success and production readiness: N/A. Fixture/code revision, chronological first success, correction rate and inference cost: unknown.", "",
              "First-repeat metrics, latency, token totals and missing-value denominators are in report.json. Different case signatures and mock/local providers are separate groups."]
    if report["discovery_issues"] or any(run["issues"] for run in report["runs"]):
        lines += ["", "Receipt issues:"]
        lines += [f"- {run['suite']} / {run['run_token']}: {', '.join(run['issues'])}"
                  for run in report["runs"] if run["issues"]]
        lines += [f"- {issue['suite']}: {issue['code']} ({issue['count']})" for issue in report["discovery_issues"]]
    return "\n".join(lines) + "\n"


def write_report(report, base=BASE):
    parent = Path(base) / "evaluation-reports"
    require(not parent.is_symlink(), "unsafe_output_directory")
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    output = parent / uuid.uuid4().hex
    output.mkdir(mode=0o700)
    for name, content in (("report.json", json.dumps(report, indent=2, sort_keys=True) + "\n"),
                          ("report.md", markdown(report))):
        with os.fdopen(os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600), "w") as stream:
            stream.write(content)
    return output


def main():
    """Exit 1 for incomplete/invalid receipts or a recorded evaluation failure."""
    try:
        report = collect()
        output = write_report(report)
        failed = report["result"] != "complete" or report["coverage"]["reported_failed_runs"] > 0
        print(json.dumps({"result": report["result"], "evaluation_failures": report["coverage"]["reported_failed_runs"],
                          "coverage": report["coverage"], "evidence": str(output.relative_to(ROOT))}, sort_keys=True))
        return 1 if failed else 0
    except Exception:
        print(json.dumps({"result": "failed", "failure_code": "report_collection_or_storage_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
