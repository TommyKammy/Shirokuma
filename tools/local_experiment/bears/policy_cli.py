"""One bounded caller Pod check; status verifies saved evidence without API/model calls.

Raw input stays in memory. Stored hashes bind receipts but do not reconstruct
or independently attest the original manifest. Admission is advisory only.
"""
from __future__ import annotations
import argparse
import asyncio
import copy
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
LOCAL = HERE.parent
sys.path[:0] = [str(HERE), str(LOCAL)]
import policy_contract as C
from policy import candidate as P
from query_cli import canonical, same, loads, private_directory, read, sync, lock, Discard

STATE = ROOT / "tmp/local-experiment/policy-cli"
EVIDENCE = ROOT / "tmp/local-experiment/policy-candidate-bear"
CASE = "single-pod"
MAX_BYTES = 1024 * 1024
FILES = frozenset({"result.json", "spans.jsonl", "candidate.json", "observation.json",
    "classification.json", "verification-progress.json", "verification.json"})
ERROR_CODES = frozenset({"ARGUMENTS_INVALID", "EVIDENCE_TOKEN_INVALID", "ENVIRONMENT_NOT_READY",
    "POLICY_NOT_FOUND", "POLICY_BUSY", "POLICY_ALREADY_EXISTS", "POLICY_JOURNAL_INVALID",
    "POLICY_EVIDENCE_INVALID", "POLICY_CHECK_FAILED", "INPUT_INVALID", "INTERRUPTED"})
RESULTS = frozenset({"passed", "failed", "unknown", "not_started", "not_found", "busy"})
FINAL_KEYS = frozenset({"schema_version", "event", "command", "evidence_token", "workflow_token",
    "result", "execution", "verification", "admission", "expected_violation_ids", "reported_violation_ids",
    "response_content_check", "authority_granted", "persisted_workloads", "error_code", "evidence"})
ACTIONS = ("bear.policy_candidate.observe", "llm.policy_candidate.classify",
           "bear.policy_candidate.verify", "bear.policy_candidate.assess")



class CLIError(ValueError):
    def __init__(self, code):
        self.code = code if code in ERROR_CODES else "POLICY_EVIDENCE_INVALID"
        super().__init__(self.code)


def require(condition, code="POLICY_EVIDENCE_INVALID"):
    if not condition:
        raise CLIError(code)


def token(value):
    require(type(value) is str and re.fullmatch("[0-9a-f]{32}", value), "EVIDENCE_TOKEN_INVALID")
    return value


def snapshot(directory):
    private_directory(directory)
    entries = sorted(directory.iterdir())
    require(len(entries) <= len(FILES) and all(p.name in FILES for p in entries))
    raw = {p.name: read(p) for p in entries}
    require(sum(map(len, raw.values())) <= 6 * MAX_BYTES)
    return raw, {name: hashlib.sha256(value).hexdigest() for name, value in raw.items()}



def projection(command, identity, *, result="unknown", execution="not-started", verification="not-checked",
                workflow_token=None, admission="not-observed", expected_violation_ids=None,
                reported_violation_ids=None, response_content_check="unknown", error_code=None, has_evidence=False):
    return validate_projection(dict(schema_version=1, event="result", command=command, evidence_token=identity,
        workflow_token=workflow_token, result=result, execution=execution, verification=verification,
         admission=admission, expected_violation_ids=expected_violation_ids, reported_violation_ids=reported_violation_ids,
         response_content_check=response_content_check,
        authority_granted=False, persisted_workloads=0, error_code=error_code,
        evidence="tmp/local-experiment/policy-candidate-bear/" + identity if has_evidence else None))


def rules(value, *, nullable=False):
    require(nullable and value is None or type(value) is list and all(type(v) is str and
        v in {f"P{i:02}" for i in range(1, 7)} for v in value) and value == sorted(set(value)))
    return value


def validate_projection(value):
    require(type(value) is dict and set(value) == FINAL_KEYS and type(value["schema_version"]) is int
        and value["schema_version"] == 1 and value["event"] == "result" and value["command"] in ("check", "status")
        and value["result"] in RESULTS and value["execution"] in ("not-started", "invoked", "read-only")
        and value["verification"] in ("not-checked", "journal-only", "saved-evidence")
        and value["admission"] in ("not-observed", "unknown", "allowed", "denied")
        and value["response_content_check"] in ("unknown", "legacy-unrecorded", "producer-checked", "not-applicable")
        and value["authority_granted"] is False and type(value["persisted_workloads"]) is int
        and value["persisted_workloads"] == 0 and (value["error_code"] is None or value["error_code"] in ERROR_CODES))
    for key in ("evidence_token", "workflow_token"):
        if value[key] is not None: token(value[key])
    for key in ("expected_violation_ids", "reported_violation_ids"): rules(value[key], nullable=True)
    require((value["command"] == "status") == (value["execution"] == "read-only"))
    require(value["evidence"] is None or value["evidence_token"] is not None and
            value["evidence"] == "tmp/local-experiment/policy-candidate-bear/" + value["evidence_token"])
    if value["result"] == "passed":
        require(value["workflow_token"] is not None and value["verification"] == "saved-evidence"
            and value["evidence"] is not None and value["error_code"] is None)
        expected, reported = value["expected_violation_ids"], value["reported_violation_ids"]
        require(value["admission"] == "allowed" and expected == [] and reported == [] or
            value["admission"] == "denied" and bool(expected) and bool(reported) and set(reported) <= set(expected))
        require(value["response_content_check"] in ("legacy-unrecorded", "producer-checked")
            if value["admission"] == "allowed" else value["response_content_check"] == "not-applicable")
    if value["result"] == "failed":
        require(value["workflow_token"] is not None and value["verification"] == "saved-evidence")
    return value


def sha(value):
    require(type(value) is str and re.fullmatch("[0-9a-f]{64}", value))


def uuid_string(value):
    require(type(value) is str and str(uuid.UUID(value)) == value)


def timestamp(value):
    from datetime import datetime
    require(type(value) is str and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z", value))
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def candidate_receipt(value):
    C.keys(value, ("schema_version", "input_sha256", "canonical_input_sha256", "body_sha256",
                   "transform_ids", "candidate", "host_truth"))
    require(type(value["schema_version"]) is int and value["schema_version"] == 1)
    for key in ("input_sha256", "canonical_input_sha256", "body_sha256"): sha(value[key])
    transforms = value["transform_ids"]
    require(type(transforms) is list and all(type(v) is str and v in P.TRANSFORM_IDS for v in transforms)
        and transforms == [v for v in P.TRANSFORM_IDS if v in transforms])
    candidate = C.native_candidate(value["candidate"])
    require(candidate["id"] == "c01" and candidate["kind"] == "Pod" and candidate["namespace_scope"] == "protected")
    truth = P._truth(candidate)
    require(same(truth, value["host_truth"]))
    return C.build_evidence([candidate], "native-baseline"), truth


def sources(value):
    C.keys(value, C.SOURCE_FIELDS)
    for item in value.values(): sha(item)


def baseline(value):
    C.keys(value, ("namespace_uid", "namespace_labels_sha256", "namespace_selector", "limit_ranges", "resources"))
    uuid_string(value["namespace_uid"]); sha(value["namespace_labels_sha256"])
    require(value["namespace_selector"] == {"kubernetes.io/metadata.name":"shirokuma-dev"} and value["limit_ranges"] == [])
    rows = value["resources"]
    require(type(rows) is list and len(rows) == 16)
    expected = {(kind, name) for kind in (C.CORE.MANAGE.POLICY, C.CORE.MANAGE.BINDING) for name in C.CORE.MANAGE.NAMES}
    seen, uids = set(), set()
    for row in rows:
        C.keys(row, ("kind", "name", "uid", "generation", "spec_sha256", "owner_sha256"))
        pair = (row["kind"], row["name"])
        require(pair in expected and pair not in seen and row["uid"] not in uids
            and type(row["generation"]) is int and row["generation"] > 0)
        uuid_string(row["uid"]); sha(row["spec_sha256"]); sha(row["owner_sha256"])
        seen.add(pair); uids.add(row["uid"])
    require(seen == expected)


def verification(value, receipt):
    require(type(value) is dict and type(value.get("schema_version")) is int and value["schema_version"] in (1, 2))
    keys = ("schema_version", "result", "server_dry_run", "authority_granted", "persisted_workloads",
        "request_count", "pending", "admission", "expected_violation_ids", "reported_violation_ids",
        "matched_host_expectation", "absence_before", "absence_after", "baseline_stable", "error_code",
        "body_sha256", "baseline_before", "baseline_after", "policy_sources")
    C.keys(value, keys + (("response_content_check",) if value["schema_version"] == 2 else ()))
    require(value["result"] in ("not-started", "ready", "running", "passed", "failed", "interrupted")
        and value["server_dry_run"] is True and value["authority_granted"] is False
        and type(value["persisted_workloads"]) is int and value["persisted_workloads"] == 0
        and type(value["request_count"]) is int and value["request_count"] in (0,1)
        and value["admission"] in ("not-observed", "unknown", "allowed", "denied")
        and type(value["matched_host_expectation"]) is bool and value["body_sha256"] == receipt["body_sha256"])
    for key in ("absence_before", "absence_after", "baseline_stable"):
        require(value[key] is None or type(value[key]) is bool)
    rules(value["expected_violation_ids"]); rules(value["reported_violation_ids"])
    require(same(value["expected_violation_ids"], receipt["host_truth"]["candidates"][0]["violation_ids"]))
    if value["pending"] is not None:
        require(same(value["pending"], dict(body_sha256=receipt["body_sha256"],outcome="unknown")))
    for key in ("baseline_before", "baseline_after"):
        if value[key] is not None: baseline(value[key])
    if value["policy_sources"] is not None: sources(value["policy_sources"])
    if value["schema_version"] == 2:
        require(value["response_content_check"] in ("not-checked", "matched", "not-applicable"))
        if value["result"] != "passed": require(value["response_content_check"] == "not-checked")
    require(value["error_code"] is None or type(value["error_code"]) is str and
        re.fullmatch("[A-Z_]{1,80}", value["error_code"]))
    if value["result"] == "passed":
        require(value["request_count"] == 1 and value["pending"] is None and value["error_code"] is None
            and all(value[k] is True for k in ("matched_host_expectation", "absence_before", "absence_after", "baseline_stable"))
            and value["baseline_before"] is not None and value["policy_sources"] is not None
            and same(value["baseline_before"], value["baseline_after"]))
        expected, reported = value["expected_violation_ids"], value["reported_violation_ids"]
        require(value["admission"] == "allowed" and expected == [] and reported == [] or
            value["admission"] == "denied" and bool(expected) and bool(reported) and set(reported) <= set(expected))
        if value["schema_version"] == 2:
            require(value["response_content_check"] == ("matched" if value["admission"] == "allowed" else "not-applicable"))


def metrics(value):
    C.keys(value, ("prompt_tokens", "completion_tokens", "duration_ms", "inference_cost_estimate"))
    for key, number in value.items():
        require(number is None or type(number) in ((int,float) if key == "inference_cost_estimate" else (int,))
            and math.isfinite(number) and 0 <= number < 2**63)


def audits_and_spans(docs, result):
    records, spans = result["audit_records"], docs.get("spans.jsonl", [])
    require(type(records) is list and len(records) <= 4 and type(spans) is list and len(spans) <= 4)
    events, ids, actions = set(), set(), []
    for row in records:
        require(type(row) is dict and row.get("action_type") in ACTIONS
            and row.get("task_id") == result["task_id"] and row.get("trace_id") == result["trace_id"]
            and row.get("status") in ("success", "blocked", "failed") and row.get("audit_delivery") in ("pending", "delivered", "failed"))
        uuid_string(row.get("event_id"))
        require(row["event_id"] not in events and type(row.get("span_id")) is str
            and re.fullmatch("[0-9a-f]{16}", row["span_id"]) and row["span_id"] not in ids
            and row["action_type"] not in actions)
        metrics({k:row.get(k) for k in ("prompt_tokens","completion_tokens","duration_ms","inference_cost_estimate")})
        events.add(row["event_id"]); ids.add(row["span_id"]); actions.append(row["action_type"])
    require(actions == [a for a in ACTIONS if a in actions])
    rows = {row["span_id"]:row for row in records}
    seen, root = set(), None
    for span in spans:
        require(type(span) is dict and span.get("span_id") in rows and span["span_id"] not in seen)
        row = rows[span["span_id"]]
        require(span.get("name") == row["action_type"] and span.get("trace_id") == result["trace_id"]
            and (span.get("status") == ("OK" if row["status"] == "success" else "ERROR") or
                 span.get("name") == ACTIONS[-1] and span.get("status") == "ERROR" and result["result"] != "passed")
            and type(span.get("start_time_unix_nano")) is int and type(span.get("end_time_unix_nano")) is int
            and 0 < span["start_time_unix_nano"] <= span["end_time_unix_nano"])
        seen.add(span["span_id"])
        if span["name"] == ACTIONS[-1]:
            require(span.get("attributes", {}).get("task_id") == result["task_id"]); root = span
    if root is not None:
        require(seen == set(rows) and all(root["start_time_unix_nano"] <= s["start_time_unix_nano"]
            <= s["end_time_unix_nano"] <= root["end_time_unix_nano"] for s in spans))
    return root


def verify_documents(raw):
    docs = {name: ([loads(line) for line in data.splitlines() if line] if name == "spans.jsonl" else loads(data))
            for name,data in raw.items()}
    empty = dict(result="unknown", verification="journal-only", workflow_token=None, admission="not-observed",
                  expected_violation_ids=None, reported_violation_ids=None, response_content_check="unknown")
    receipt = docs.get("candidate.json")
    if receipt is None:
        require(not docs); return empty
    evidence, truth = candidate_receipt(receipt)
    result = docs.get("result.json")
    if result is None: return empty
    require(type(result) is dict and type(result.get("task_id")) is str and re.fullmatch("workflow-[0-9a-f]{32}", result["task_id"]))
    inner = result["task_id"][9:]
    token(result.get("run_token")); token(result.get("trace_id"))
    require(type(result.get("schema_version")) is int and result["schema_version"] == 1
        and result.get("scope") == "advisory-single-pod-server-dry-run" and result.get("result") in
            ("running", "verified", "passed", "failed", "interrupted")
        and result.get("authority_granted") is False and type(result.get("persisted_workloads")) is int
        and result["persisted_workloads"] == 0 and type(result.get("retries")) is int and result["retries"] == 0
        and type(result.get("cloud_calls")) is int and result["cloud_calls"] == 0
        and type(result.get("model_attempts")) is int and result["model_attempts"] in (0,1)
        and type(result.get("model_matched")) is bool and type(result.get("workflow_complete")) is bool
        and same(result.get("candidate"),receipt))
    start = timestamp(result.get("started_at"))
    if result.get("finished_at") is not None: require(timestamp(result["finished_at"]) >= start)
    if result.get("model_metrics") is not None: metrics(result["model_metrics"])
    v = result.get("verification"); verification(v,receipt)
    for name in ("verification-progress.json", "verification.json"):
        if name in docs:
            verification(docs[name],receipt)
            if name == "verification.json": require(docs[name]["result"] == "passed")
    observed = docs.get("observation.json")
    if observed is not None:
        C.keys(observed, ("schema_version", "authority", "candidate", "baseline", "policy_sources", "body_sha256"))
        require(type(observed["schema_version"]) is int and observed["schema_version"] == 1
            and observed["authority"] == "advisory_only" and same(observed["candidate"],receipt["candidate"])
            and observed["body_sha256"] == receipt["body_sha256"])
        baseline(observed["baseline"]); sources(observed["policy_sources"])
        require(same(observed["baseline"], v["baseline_before"]) and same(observed["policy_sources"],v["policy_sources"]))
    classification = docs.get("classification.json")
    if classification is not None:
        C.keys(classification,("plan","metrics")); metrics(classification["metrics"])
        C.validate_plan(classification["plan"], evidence, truth)
        require(result["model_attempts"] == 1 and result["model_matched"] is True
            and same(classification["plan"],result.get("plan")) and same(classification["metrics"],result["model_metrics"]))
    root = audits_and_spans(docs,result)
    if result["result"] == "passed":
        require(set(docs) == FILES and root is not None and root["status"] == "OK" and result["workflow_complete"] is True
            and result.get("finished_at") is not None and result.get("error_code") is None and v["result"] == "passed"
            and observed is not None and classification is not None and same(v,docs["verification.json"])
            and same(v,docs["verification-progress.json"]))
        rows = result["audit_records"]
        require([r["action_type"] for r in rows] == list(ACTIONS)
            and all(r["status"] == "success" and r["audit_delivery"] == "delivered" for r in rows)
            and type(result.get("correlated_events")) is int and result["correlated_events"] == 4
            and type(result.get("saved_otel_spans_verified")) is int and result["saved_otel_spans_verified"] == 4
            and all(same(result["model_metrics"][k],rows[1].get(k)) for k in result["model_metrics"]))
        return dict(result="passed",verification="saved-evidence",workflow_token=inner,admission=v["admission"],
            expected_violation_ids=v["expected_violation_ids"],reported_violation_ids=v["reported_violation_ids"],
            response_content_check=("not-applicable" if v["admission"] == "denied" else
                "producer-checked" if v["schema_version"] == 2 else "legacy-unrecorded"))
    require(result["workflow_complete"] is False)
    if result["result"] == "failed" and root is not None and result.get("finished_at") is not None:
        require(result.get("error_code") in ("POLICY_CANDIDATE_OBSERVATION_MODEL_DRYRUN_OR_AUDIT_FAILED",
                                            "POLICY_CANDIDATE_SAVED_TRACE_VERIFICATION_FAILED"))
        return dict(result="failed",verification="saved-evidence",workflow_token=inner,admission=v["admission"],
            expected_violation_ids=v["expected_violation_ids"],reported_violation_ids=v["reported_violation_ids"],
            response_content_check="unknown")
    return dict(empty,workflow_token=inner,admission="unknown" if v["pending"] is not None else v["admission"])

def persist(directory, journal):
    private_directory(directory)
    temporary = directory / (".intent-" + uuid.uuid4().hex)
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(canonical(journal) + "\n"); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, directory / "intent.json")
        sync(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def validate_journal(value, identity):
    C.keys(value, ("schema_version", "evidence_token", "case", "invocations", "state", "files"))
    require(type(value["schema_version"]) is int and value["schema_version"] == 1
        and value["evidence_token"] == identity and value["case"] == CASE
        and type(value["invocations"]) is int and value["invocations"] == 1
        and value["state"] in ("invoked", "returned", "interrupted", "unknown"), "POLICY_JOURNAL_INVALID")
    files = value["files"]
    require(files is None or type(files) is dict and set(files) <= FILES and all(
        type(v) is str and re.fullmatch("[0-9a-f]{64}", v) for v in files.values()), "POLICY_JOURNAL_INVALID")
    require(value["state"] != "returned" or files is not None, "POLICY_JOURNAL_INVALID")
    return value


def inspect_saved(identity, *, command="status", journal=None):
    output = EVIDENCE / identity
    if EVIDENCE.exists() or EVIDENCE.is_symlink():
        private_directory(EVIDENCE, legacy_parent=True)
    if not output.exists() and not output.is_symlink():
        return projection(command, identity, execution="read-only" if command == "status" else "invoked",
            result="unknown" if journal else "not_found", verification="journal-only" if journal else "not-checked",
            error_code=None if journal else "POLICY_NOT_FOUND"), 0 if journal else 1
    private_directory(EVIDENCE, legacy_parent=True)
    raw, hashes = snapshot(output)
    if "result.json" in raw:
        require(loads(raw["result.json"]).get("run_token") == identity)
    if journal is not None and journal["files"] is not None:
        require(journal["files"] == hashes)
    view = verify_documents(raw)
    require(snapshot(output) == (raw, hashes))
    return projection(command, identity, execution="read-only" if command == "status" else "invoked",
        has_evidence=True, error_code="POLICY_CHECK_FAILED" if view["result"] == "failed" else None, **view), 0


def inspect_policy(identity):
    token(identity)
    try:
        directory = STATE / identity
        if STATE.exists() or STATE.is_symlink():
            private_directory(STATE)
        if directory.exists() or directory.is_symlink():
            private_directory(directory)
            require({p.name for p in directory.iterdir()} == {"intent.json", "run.lock"}, "POLICY_JOURNAL_INVALID")
            with lock(directory / "run.lock") as busy:
                original = read(directory / "intent.json")
                journal = validate_journal(loads(original), identity)
                if busy:
                    return projection("status", identity, result="busy", execution="read-only",
                        verification="journal-only", error_code="POLICY_BUSY"), 0
                view = inspect_saved(identity, journal=journal)
                require(read(directory / "intent.json") == original, "POLICY_JOURNAL_INVALID")
                return view
        return inspect_saved(identity)
    except Exception as error:
        code = error.code if isinstance(error, CLIError) else "POLICY_EVIDENCE_INVALID"
        return projection("status", identity, execution="read-only", error_code=code), 1


def runner():
    import run_policy_candidate
    return run_policy_candidate


def configured_runner():
    R = runner()
    from providers import validate_config
    config = R.load_config(LOCAL / "llm/providers.qwen-local.json")
    validate_config(config)
    require(config["active_provider"] == "local", "ENVIRONMENT_NOT_READY")
    return R, config


def invoke(identity, lifecycle, raw):
    try:
        P.prepare(raw, identity)
    except Exception:
        raise CLIError("INPUT_INVALID") from None
    require(STATE.parent.resolve() == STATE.parent and STATE.parent.is_dir(), "ENVIRONMENT_NOT_READY")
    for base in (STATE, EVIDENCE):
        if not base.exists() and not base.is_symlink():
            base.mkdir(mode=0o700); sync(base.parent)
        private_directory(base, legacy_parent=base == EVIDENCE)
    directory, output = STATE / identity, EVIDENCE / identity
    require(not directory.exists() and not directory.is_symlink() and not output.exists() and not output.is_symlink(), "POLICY_ALREADY_EXISTS")
    try:
        R, config = configured_runner()
    except Exception:
        raise CLIError("ENVIRONMENT_NOT_READY") from None
    from local_session import interruption_handlers
    directory.mkdir(mode=0o700); sync(STATE)
    with lock(directory / "run.lock", create=True) as busy:
        require(not busy, "POLICY_BUSY")
        journal = dict(schema_version=1, evidence_token=identity, case=CASE, invocations=1, state="invoked", files=None)
        persist(directory, journal)  # Durable uncertainty precedes the sole invocation.
        try:
            with interruption_handlers():
                lifecycle["invoked"] = True
                returned = asyncio.run(R.run(config, output, raw))
            saved, hashes = snapshot(output)
            require(same(loads(saved.get("result.json", b"null")), returned))
            report, _ = inspect_saved(identity, command="check", journal=journal)
            journal.update(state="returned", files=hashes)
            persist(directory, journal)
            return report, (2 if report["admission"] == "denied" else 0) if report["result"] == "passed" else 1
        except BaseException as error:
            journal["state"] = "unknown" if isinstance(error, Exception) else "interrupted"
            try:
                if output.exists():
                    journal["files"] = snapshot(output)[1]
                persist(directory, journal)
            finally:
                raise error


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CLIError("ARGUMENTS_INVALID")


def arguments(argv):
    parser = Parser(description=__doc__, allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
    for action in ("check", "status"):
        command = sub.add_parser(action, allow_abbrev=False)
        command.add_argument("--evidence-token", required=True)
    return parser.parse_args(argv)


def emit(stream, value):
    stream.write(canonical(value) + "\n"); stream.flush()


def main(argv=None, *, stdout=None, stdin=None):
    stream = stdout or sys.stdout
    action, identity = "check", None
    lifecycle = {"invoked": False}
    try:
        args = arguments(argv)
        action, identity = args.command, token(args.evidence_token)
        emit(stream, dict(schema_version=1, event="allocated", command=action, evidence_token=identity))
        with redirect_stdout(Discard()), redirect_stderr(Discard()):
            if action == "status":
                report, code = inspect_policy(identity)
            else:
                raw = (stdin or sys.stdin.buffer).read(P.MAX_BYTES + 1)
                report, code = invoke(identity, lifecycle, raw)
        emit(stream, report)
        return code
    except BaseException as error:
        if isinstance(error, SystemExit):
            raise
        code = error.code if isinstance(error, CLIError) else "INTERRUPTED" if not isinstance(error, Exception) else "POLICY_CHECK_FAILED"
        # An allocated identity alone cannot prove that run() was invoked.
        intent = lifecycle["invoked"]
        report = projection(action, identity, result="unknown" if intent or action == "status" else "not_started",
            execution="read-only" if action == "status" else "invoked" if intent else "not-started", error_code=code)
        emit(stream, report)
        return 128 + getattr(error, "signum", 2) if not isinstance(error, Exception) else 1


if __name__ == "__main__":
    raise SystemExit(main())
