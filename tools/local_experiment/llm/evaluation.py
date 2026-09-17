#!/usr/bin/env python3
"""Evaluate bounded inventory fact following over five synthetic local fixtures."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
import uuid
from typing import Any

from mock_provider import serve_mock
from providers import LocalProvider, ProviderError, load_config, validate_config
from workflow import agenthouse_module, disable_external_tracing, inventory_evidence, validate_summary

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
REPEATS = 2
CHECKS = ("required_keys", "model_names", "model_count", "verified_run_available")
SUMMARY_KEYS = {"result", "model_count", "models", "verified_run_available"}
AUDIT_FIELDS = ("event_id", "task_id", "trace_id", "action_type", "target_system", "status",
                "model_provider", "model_name", "prompt_tokens", "completion_tokens",
                "inference_cost_estimate", "duration_ms")
SCOPE = ("Local synthetic inventory fact-following evaluation only. Five fixed fixtures, two independent "
         "summaries each; no retries. This does not evaluate SRE diagnosis, tool selection, general model "
         "performance, or production readiness. A verified-run flag describes a record, not live tables.")


class EvaluationError(RuntimeError):
    pass


def fixtures() -> list[dict]:
    inputs = (
        ("empty_no_run", {"models": [], "latest_verified_run": None}),
        ("single_no_run", {"models": ["order_stage"], "latest_verified_run": None}),
        ("renamed_three_verified", {"models": ["renamed_gamma", "renamed_alpha", "renamed_beta"],
                                    "latest_verified_run": {"verified": True}}),
        ("unverified_record", {"models": ["customer_snapshot", "daily_metrics"],
                               "latest_verified_run": {"verified": False}}),
        ("sixteen_names_boundary", {"models": [f"boundary_model_{index:02d}" for index in range(16)],
                                    "latest_verified_run": {"verified": True}}),
    )
    return [{"case_id": name, "evidence": inventory_evidence(data)} for name, data in inputs]


def correctness(content: Any, evidence: dict) -> tuple[dict[str, bool], bool]:
    """Compare with fixture truth, never infer expected values from model output."""
    value = content if isinstance(content, dict) else {}
    names = value.get("models")
    checks = {
        "required_keys": set(value) == SUMMARY_KEYS and value.get("result") == "complete",
        "model_names": isinstance(names, list) and all(isinstance(name, str) for name in names)
                       and sorted(names) == evidence["models"],
        "model_count": type(value.get("model_count")) is int
                       and value["model_count"] == len(evidence["models"]),
        "verified_run_available": type(value.get("verified_run_available")) is bool
                                  and value["verified_run_available"] == evidence["verified_run_available"],
    }
    try:
        validate_summary(content, evidence)
    except (ProviderError, RuntimeError, TypeError, ValueError, KeyError):
        return checks, False
    return checks, all(checks.values())


def safe_metrics(value: Any, elapsed_ms: int) -> dict:
    value = value if isinstance(value, dict) else {}
    result = {"inference_cost_estimate": None}
    for key in ("prompt_tokens", "completion_tokens", "duration_ms"):
        item = value.get(key)
        result[key] = item if type(item) is int and 0 <= item <= 2**64 - 1 else None
    if result["duration_ms"] is None:
        result["duration_ms"] = elapsed_ms
    return result


def private_write(path: Path, value: Any, *, append: bool = False) -> None:
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else os.O_TRUNC)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags, 0o600), "w") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(json.dumps(value, sort_keys=True) + "\n")


def verify_audit(client: Any, event: dict) -> dict:
    event_id = str(uuid.UUID(event["event_id"]))
    rows = json.loads(client.query("SELECT " + ", ".join(AUDIT_FIELDS)
        + " FROM agenthouse.pawprints FINAL WHERE event_id = '" + event_id + "' FORMAT JSON"))["data"]
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise EvaluationError("expected exactly one evaluation Pawprint")
    row = rows[0]
    normalized = {}
    for key in AUDIT_FIELDS:
        observed = row[key]
        if key in {"prompt_tokens", "completion_tokens", "duration_ms"} and observed is not None:
            if isinstance(observed, str) and observed.isascii() and observed.isdigit():
                observed = int(observed)  # ClickHouse UInt64 can be encoded as a JSON string.
            if type(observed) is not int or not 0 <= observed <= 2**64 - 1:
                raise EvaluationError("evaluation Pawprint metrics have invalid types")
        if observed != event[key]:
            raise EvaluationError("evaluation Pawprint correlation or metrics differ")
        normalized[key] = observed
    return normalized


def aggregate(attempts: list[dict], cases: list[dict]) -> dict:
    successes = {key: sum(attempt["checks"][key] for attempt in attempts) for key in CHECKS}
    per_case = []
    for case in cases:
        selected = [attempt for attempt in attempts if attempt["case_id"] == case["case_id"]]
        per_case.append({"case_id": case["case_id"], "expected": case["evidence"], "requested": REPEATS,
                         "attempted": len(selected), "passed": sum(item["result"] == "passed" for item in selected),
                         "check_successes": {key: sum(item["checks"][key] for item in selected) for key in CHECKS}})
    usage = {"inference_cost_estimate": None,
             "duration_ms": sum(attempt["metrics"]["duration_ms"] for attempt in attempts)}
    for key in ("prompt_tokens", "completion_tokens"):
        values = [attempt["metrics"][key] for attempt in attempts]
        known = [value for value in values if value is not None]
        usage[key] = sum(known) if values and len(known) == len(values) else None
        usage[key + "_known_calls"] = len(known)
    return {"cases": per_case, "check_successes": successes, "usage": usage}


def run_evaluation(config: dict, output: Path, *, endpoint: str | None = None,
                   provider: Any = None, client: Any = None, audit_module: Any = None) -> dict:
    """Dependency injection is for tests; executable mode always uses the local provider boundary."""
    config = validate_config(config)
    profile = config["providers"][config["active_provider"]]
    disable_external_tracing()
    output.mkdir(parents=True, mode=0o700, exist_ok=True)
    if output.is_symlink():
        raise EvaluationError("evaluation evidence directory must not be a symlink")
    output.chmod(0o700)
    provider = provider if provider is not None else LocalProvider(profile, endpoint)
    audit_module = audit_module if audit_module is not None else agenthouse_module()
    client = client if client is not None else audit_module.Client()
    cases = fixtures()
    task_id = "llm-eval-" + uuid.uuid4().hex
    trace_id = uuid.uuid4().hex
    report = {"result": "failed", "scope": SCOPE, "provider_kind": profile["kind"],
              "provider": profile["provider"], "model": profile["model"], "task_id": task_id,
              "trace_id": trace_id, "trace_kind": "evaluation correlation identifier",
              "requested_inferences": len(cases) * REPEATS, "retries": 0,
              "cloud_calls": 0, "model_server_configuration_changed": False, "attempts": []}
    attempts = report["attempts"]
    started = time.monotonic()
    try:
        if client.query("SELECT 1").strip() != "1":
            raise EvaluationError("AgentHouse evaluation preflight failed")
        for case in cases:
            for repeat in range(1, REPEATS + 1):
                call_started = time.monotonic()
                attempt = {"case_id": case["case_id"], "repeat": repeat, "result": "failed",
                           "checks": dict.fromkeys(CHECKS, False), "audit": "pending"}
                try:
                    response = provider.complete("summarize", case["evidence"])
                    attempt["metrics"] = safe_metrics(response, int((time.monotonic() - call_started) * 1000))
                    checks, passed = correctness(response["content"], case["evidence"])
                    attempt.update(checks=checks, result="passed" if passed else "failed")
                    if not passed:
                        attempt["failure_code"] = "summary_mismatch"
                except Exception as error:
                    metrics = error.metrics if isinstance(error, ProviderError) else {}
                    attempt["metrics"] = safe_metrics(metrics, int((time.monotonic() - call_started) * 1000))
                    attempt["failure_code"] = "provider_error" if isinstance(error, ProviderError) else "inference_error"
                attempts.append(attempt)
                event = audit_module.event("llm.eval_summary", "llm-" + profile["kind"],
                    status="success" if attempt["result"] == "passed" else "failed", agent_id="local-llm-evaluation",
                    task_id=task_id, trace_id=trace_id, model_provider=profile["provider"],
                    model_name=profile["model"], **attempt["metrics"])
                attempt["event_id"] = event["event_id"]
                private_write(output / "summary.json", {**report, **aggregate(attempts, cases)})
                try:
                    audit_module.insert(client, event)
                    attempt["audited_event"] = verify_audit(client, event)
                    attempt["audit"] = "verified"
                except Exception:
                    attempt["audit"] = "failed"
                    attempt["audit_failure_code"] = "audit_delivery_or_readback_failed"
                    raise EvaluationError("evaluation audit failed") from None
                finally:
                    private_write(output / "attempts.jsonl", attempt, append=True)
        if all(attempt["result"] == "passed" for attempt in attempts):
            report["result"] = "passed"
    except Exception:
        report["failure_code"] = "audit_unavailable_or_failed"
    report.update(aggregate(attempts, cases))
    report.update(attempted_inferences=len(attempts),
                  passed_inferences=sum(attempt["result"] == "passed" for attempt in attempts),
                  audited_inferences=sum(attempt["audit"] == "verified" for attempt in attempts),
                  elapsed_ms=max(0, int((time.monotonic() - started) * 1000)))
    private_write(output / "summary.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "providers.example.json")
    args = parser.parse_args(argv)
    output = ROOT / "tmp/local-experiment/llm-evaluation" / uuid.uuid4().hex
    try:
        config = load_config(args.config)
        if config["active_provider"] == "mock":
            with serve_mock() as endpoint:
                report = run_evaluation(config, output, endpoint=endpoint)
        else:
            report = run_evaluation(config, output)
    except Exception:
        report = {"result": "failed", "scope": SCOPE, "failure_code": "configuration_or_setup_failed"}
        output.mkdir(parents=True, mode=0o700, exist_ok=True)
        private_write(output / "summary.json", report)
    print(json.dumps({key: report[key] for key in ("result", "scope", "model", "requested_inferences",
        "attempted_inferences", "passed_inferences", "audited_inferences", "check_successes", "usage", "failure_code")
        if key in report} | {"evidence": str(output)}))
    return 0 if report["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
