"""Fixed QueryDoctor JSONL bridge; status only verifies saved local evidence.

The CLI identity names an evidence directory, not the inner query/user identity.
Allocation is not invocation. An immutable invocation intent prevents run retries.
Legacy receipts have no CLI lock/hash seal; their complete saved evidence is
validated and read twice, without claiming a fresh API or audit observation.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
LOCAL = HERE.parent
sys.path.insert(0, str(HERE))
import query_contract as C  # Pure stdlib contract; status never imports a runner.

STATE = ROOT / "tmp/local-experiment/query-cli"
EVIDENCE = ROOT / "tmp/local-experiment/query-bear"
CASE = "retained-fixture"
MAX_BYTES = 1024 * 1024
FILES = frozenset({"result.json", "spans.jsonl", "observer-receipt.json", "observation.json",
    "model-evidence.json", "plan.json", "fixture_stats.json", "own_query_history.json",
    "interruption.json", "independent-verification.json"})
ERROR_CODES = frozenset({"ARGUMENTS_INVALID", "EVIDENCE_TOKEN_INVALID", "ENVIRONMENT_NOT_READY",
    "QUERY_NOT_FOUND", "QUERY_BUSY", "QUERY_ALREADY_EXISTS", "QUERY_JOURNAL_INVALID",
    "QUERY_EVIDENCE_INVALID", "QUERY_FAILED", "INTERRUPTED"})
RESULTS = frozenset({"passed", "failed", "unknown", "not_started", "not_found", "busy"})
FINAL_KEYS = frozenset({"schema_version", "event", "command", "evidence_token", "query_run_token",
    "result", "execution", "verification", "cleanup_verified", "root_cause", "performance_gain",
    "error_code", "evidence"})
ACTIONS = ("bear.query.observe", "llm.query_plan", "bear.query.fixture_stats",
           "bear.query.own_query_history", "bear.query.assess")


class CLIError(ValueError):
    def __init__(self, code):
        self.code = code if code in ERROR_CODES else "QUERY_EVIDENCE_INVALID"
        super().__init__(self.code)


def require(condition, code="QUERY_EVIDENCE_INVALID"):
    if not condition:
        raise CLIError(code)


def token(value):
    require(type(value) is str and re.fullmatch("[0-9a-f]{32}", value), "EVIDENCE_TOKEN_INVALID")
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def same(a, b):
    return canonical(a) == canonical(b)


def loads(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value)
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(CLIError("QUERY_EVIDENCE_INVALID")))


def private_directory(path, *, legacy_parent=False):
    info = path.lstat()
    require(path.resolve() == path and stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) in ((0o700, 0o755) if legacy_parent else (0o700,)), "QUERY_JOURNAL_INVALID")


def read(path):
    before = path.lstat()
    require(path.resolve() == path and stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
            and stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1 and before.st_size <= MAX_BYTES)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        require((before.st_dev, before.st_ino) == (opened.st_dev, opened.st_ino))
        raw = stream.read(MAX_BYTES + 1)
        after = os.fstat(stream.fileno())
    require(len(raw) <= MAX_BYTES and fingerprint(before) == fingerprint(after))
    return raw


def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def snapshot(directory):
    private_directory(directory)
    entries = sorted(directory.iterdir())
    require(len(entries) <= len(FILES) and all(p.name in FILES for p in entries))
    raw = {p.name: read(p) for p in entries}
    require(sum(map(len, raw.values())) <= 8 * MAX_BYTES)
    hashes = {name: hashlib.sha256(value).hexdigest() for name, value in raw.items()}
    return raw, hashes


def projection(command, identity, *, result="unknown", execution="not-started",
               verification="not-checked", query_run_token=None, cleanup_verified=None,
               error_code=None, has_evidence=False):
    value = dict(schema_version=1, event="result", command=command, evidence_token=identity,
        query_run_token=query_run_token, result=result, execution=execution, verification=verification,
        cleanup_verified=cleanup_verified, root_cause="undetermined", performance_gain="unmeasured",
        error_code=error_code, evidence="tmp/local-experiment/query-bear/" + identity if has_evidence else None)
    return validate_projection(value)


def validate_projection(value):
    require(type(value) is dict and set(value) == FINAL_KEYS and type(value["schema_version"]) is int
            and value["schema_version"] == 1 and value["event"] == "result"
            and value["command"] in ("run", "status") and value["result"] in RESULTS
            and value["execution"] in ("not-started", "invoked", "read-only")
            and value["verification"] in ("not-checked", "journal-only", "saved-evidence")
            and (value["cleanup_verified"] is None or type(value["cleanup_verified"]) is bool)
            and value["root_cause"] == "undetermined" and value["performance_gain"] == "unmeasured"
            and (value["error_code"] is None or value["error_code"] in ERROR_CODES))
    for key in ("evidence_token", "query_run_token"):
        if value[key] is not None:
            token(value[key])
    require((value["command"] == "status") == (value["execution"] == "read-only"))
    require(value["evidence"] is None or value["evidence_token"] is not None and
            value["evidence"] == "tmp/local-experiment/query-bear/" + value["evidence_token"])
    if value["result"] == "passed":
        require(value["query_run_token"] is not None and value["cleanup_verified"] is True
                and value["verification"] == "saved-evidence" and value["evidence"] is not None
                and value["error_code"] is None)
    if value["result"] == "failed":
        require(value["query_run_token"] is not None and value["verification"] == "saved-evidence")
    return value


def observer_receipt(value, identity):
    C.keys(value, ("schema_version", "run_token", "outcome", "server_max_run_seconds", "cleanup_verified",
                   "target_history_verified", "requests", "uncaptured_requests"))
    require(type(value["schema_version"]) is int and value["schema_version"] == 1
        and value["run_token"] == identity and type(value["server_max_run_seconds"]) is int
        and value["server_max_run_seconds"] == 30 and value["outcome"] in ("completed", "incomplete", "failed", "interrupted")
        and type(value["cleanup_verified"]) is bool and type(value["target_history_verified"]) is bool)
    requests = value["requests"]
    require(type(requests) is list and len(requests) <= 7)
    order = [(case, kind) for case in C.CASES for kind in ("select", "explain")]
    order.append((None, "own_query_history"))
    seen = set()
    for i, row in enumerate(requests):
        C.keys(row, ("case_id", "kind", "query_id", "state", "response_complete"))
        require((row["case_id"], row["kind"]) == order[i] if i < 5 else
                row["case_id"] is None and row["kind"] in C.TOOLS and
                row["kind"] not in [r["kind"] for r in requests[5:i]])
        require(type(row["response_complete"]) is bool and (row["state"] is None or row["state"] in
            ("FINISHED", "FAILED", "QUEUED", "WAITING_FOR_RESOURCES", "DISPATCHING", "PLANNING", "STARTING", "RUNNING", "FINISHING")))
        if row["query_id"] is not None:
            C.query_id(row["query_id"])
            require(row["query_id"] not in seen)
            seen.add(row["query_id"])
        require(not row["response_complete"] or row["query_id"] is not None)
    require(type(value["uncaptured_requests"]) is int and value["uncaptured_requests"] == sum(r["query_id"] is None for r in requests))
    complete = (value["target_history_verified"] and bool(requests) and value["outcome"] not in ("failed", "interrupted")
                and all(r["response_complete"] and r["state"] in ("FINISHED", "FAILED") for r in requests))
    require(value["cleanup_verified"] == complete and (value["outcome"] == "completed") == complete)
    return requests


def span_evidence(documents, result):
    """Closed root span is terminal evidence; the initial failed value is not."""
    records = result.get("audit_records")
    require(type(records) is list and len(records) <= 5)
    spans = documents.get("spans.jsonl", [])
    require(type(spans) is list and len(spans) <= 5)
    record_ids, events, actions = set(), set(), set()
    for row in records:
        require(type(row) is dict and row.get("action_type") in ACTIONS and row["action_type"] not in actions
                and row.get("status") in ("success", "failed", "blocked")
                and row.get("audit_delivery") in ("pending", "delivered", "failed")
                and row.get("task_id") == result["task_id"] and row.get("trace_id") == result["trace_id"]
                and type(row.get("span_id")) is str and re.fullmatch("[0-9a-f]{16}", row["span_id"]))
        event = row.get("event_id")
        require(type(event) is str and str(uuid.UUID(event)) == event and event not in events and row["span_id"] not in record_ids)
        for key in ("prompt_tokens", "completion_tokens", "duration_ms", "inference_cost_estimate"):
            val = row.get(key)
            require(val is None or type(val) in (int, float) and math.isfinite(val) and 0 <= val < 2**63)
        events.add(event); record_ids.add(row["span_id"]); actions.add(row["action_type"])
    by_id = {row["span_id"]: row for row in records}
    seen = set()
    root = None
    for span in spans:
        require(type(span) is dict and span.get("span_id") in by_id and span["span_id"] not in seen)
        row = by_id[span["span_id"]]
        require(span.get("trace_id") == result["trace_id"] and span.get("name") == row["action_type"]
                and span.get("status") == ("OK" if row["status"] == "success" else "ERROR")
                and type(span.get("start_time_unix_nano")) is int and type(span.get("end_time_unix_nano")) is int
                and 0 < span["start_time_unix_nano"] <= span["end_time_unix_nano"])
        seen.add(span["span_id"])
        if span["name"] == "bear.query.assess":
            require(span.get("attributes", {}).get("task_id") == result["task_id"])
            root = span
    if root is not None:
        require(seen == record_ids and all(root["start_time_unix_nano"] <= s["start_time_unix_nano"]
                <= s["end_time_unix_nano"] <= root["end_time_unix_nano"] for s in spans))
    return root


def verify_documents(raw):
    docs = {name: ([loads(line) for line in value.splitlines() if line] if name == "spans.jsonl" else loads(value))
            for name, value in raw.items()}
    if "result.json" not in docs:
        return {"result": "unknown", "verification": "journal-only", "query_run_token": None, "cleanup_verified": None}
    result = docs["result.json"]
    require(type(result) is dict)
    inner = token(result.get("query_run_token"))
    require(result.get("result") in ("passed", "failed", "interrupted")
        and result.get("scope") == "fixed-two-row-fixture-only" and result.get("task_id") == "workflow-" + inner
        and type(result.get("trace_id")) is str and re.fullmatch("[0-9a-f]{32}", result["trace_id"])
        and result.get("root_cause") == "undetermined" and result.get("performance_gain") == "unmeasured"
        and all(type(result.get(k)) is int and result[k] == 0 for k in ("retries", "cloud_calls", "sql_rewrites"))
        and type(result.get("cleanup_verified")) is bool)
    receipt = docs.get("observer-receipt.json")
    requests = observer_receipt(receipt, inner) if receipt is not None else None
    require(receipt is not None or result["cleanup_verified"] is False)
    if receipt is not None:
        require(result["cleanup_verified"] == receipt["cleanup_verified"])
    observation = docs.get("observation.json")
    if observation is not None:
        require(observation.get("run_token") == inner and requests is not None)
        evidence = C.project_observation(observation)
        require(same(evidence, docs.get("model-evidence.json")))
        expected_ids = [(case["id"], kind, case[kind]["query_id"]) for case in observation["cases"] for kind in ("select", "explain")]
        require(len(requests) >= 5 and [(r["case_id"], r["kind"], r["query_id"]) for r in requests[:4]] == expected_ids
                and requests[4]["query_id"] == observation["history"]["query_id"])
        plan = docs.get("plan.json")
        followups = {tool: docs[tool + ".json"] for tool in C.TOOLS if tool + ".json" in docs}
        for tool, value in followups.items():
            C.followup_summary(observation, tool, value)
        history = followups.get("own_query_history", observation["history"])
        history_terminal = (history["completeness"] == "complete" and
                            all(row["state"] in ("FINISHED", "FAILED") for row in history["entries"]))
        require(receipt["target_history_verified"] == history_terminal)
        states = {row["query_id"]: row["state"] for row in requests[:4]}
        require(all(states.get(row["query_id"]) == row["state"] for row in history["entries"]))
        require([(r["kind"], r["query_id"]) for r in requests[5:] if r["response_complete"]]
                == [(tool, followups[tool]["query_id"]) for tool in C.TOOLS if tool in followups])
        if plan is not None:
            require(same(C.validate_plan(plan, evidence), result.get("plan")))
            require(same(C.assess_followups(observation, plan, followups), result.get("assessment")))
        if result["result"] == "passed":
            require(plan is not None and len(requests) == 5 + len(plan["checks"]))
    else:
        require(not any(name in docs for name in ("model-evidence.json", "plan.json", "fixture_stats.json", "own_query_history.json")))
    root = span_evidence(docs, result)
    final = "unknown"
    if result["result"] == "passed":
        records = result["audit_records"]
        require(root is not None and root["status"] == "OK" and observation is not None
            and result["cleanup_verified"] is True and receipt["outcome"] == "completed"
            and result.get("checkpoint") == "completed-in-memory"
            and type(result.get("saved_otel_spans_verified")) is int and result["saved_otel_spans_verified"] == len(records)
            and type(result.get("correlated_events")) is int and result["correlated_events"] == len(records)
            and [r["action_type"] for r in records] == ["bear.query.observe", "llm.query_plan"] +
                ["bear.query." + c["tool"] for c in result["plan"]["checks"]] + ["bear.query.assess"]
            and all(r["status"] == "success" and r["audit_delivery"] == "delivered" for r in records))
        inference = next(r for r in records if r["action_type"] == "llm.query_plan")
        require(type(result.get("inference")) is dict and all(same(result["inference"].get(k), inference.get(k))
                for k in ("prompt_tokens", "completion_tokens", "duration_ms", "inference_cost_estimate")))
        final = "passed"
    elif root is not None:
        require("checkpoint" not in result and result.get("error_code") in
            ("QUERY_OBSERVATION_PLAN_FOLLOWUP_OR_AUDIT_FAILED", "QUERY_SAVED_TRACE_VERIFICATION_FAILED"))
        final = "failed"
    return {"result": final, "verification": "saved-evidence" if final != "unknown" else "journal-only",
            "query_run_token": inner, "cleanup_verified": result["cleanup_verified"]}


def sync(directory):
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


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
        and value["state"] in ("invoked", "returned", "interrupted", "unknown"), "QUERY_JOURNAL_INVALID")
    files = value["files"]
    require(files is None or type(files) is dict and set(files) <= FILES and all(
        type(v) is str and re.fullmatch("[0-9a-f]{64}", v) for v in files.values()), "QUERY_JOURNAL_INVALID")
    require(value["state"] != "returned" or files is not None, "QUERY_JOURNAL_INVALID")
    return value


@contextmanager
def lock(path, *, create=False):
    fd = os.open(path, (os.O_RDWR | os.O_CREAT | os.O_EXCL if create else os.O_RDONLY) | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode) == 0o600, "QUERY_JOURNAL_INVALID")
        busy = False
        try:
            fcntl.flock(fd, (fcntl.LOCK_EX if create else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            busy = True
        yield busy
    finally:
        os.close(fd)


def inspect_saved(identity, *, command="status", journal=None):
    output = EVIDENCE / identity
    if EVIDENCE.exists() or EVIDENCE.is_symlink():
        private_directory(EVIDENCE, legacy_parent=True)
    if not output.exists() and not output.is_symlink():
        return projection(command, identity, execution="read-only" if command == "status" else "invoked",
            result="unknown" if journal else "not_found", verification="journal-only" if journal else "not-checked",
            error_code=None if journal else "QUERY_NOT_FOUND"), 0 if journal else 1
    private_directory(EVIDENCE, legacy_parent=True)
    raw, hashes = snapshot(output)
    if journal is not None and journal["files"] is not None:
        require(journal["files"] == hashes)
    view = verify_documents(raw)
    require(snapshot(output) == (raw, hashes))
    return projection(command, identity, execution="read-only" if command == "status" else "invoked",
        has_evidence=True, error_code="QUERY_FAILED" if view["result"] == "failed" else None, **view), 0


def inspect_query(identity):
    token(identity)
    try:
        directory = STATE / identity
        if STATE.exists() or STATE.is_symlink():
            private_directory(STATE)
        if directory.exists() or directory.is_symlink():
            private_directory(directory)
            require({p.name for p in directory.iterdir()} == {"intent.json", "run.lock"}, "QUERY_JOURNAL_INVALID")
            with lock(directory / "run.lock") as busy:
                original = read(directory / "intent.json")
                journal = validate_journal(loads(original), identity)
                if busy:
                    return projection("status", identity, result="busy", execution="read-only",
                        verification="journal-only", error_code="QUERY_BUSY"), 0
                view = inspect_saved(identity, journal=journal)
                require(read(directory / "intent.json") == original, "QUERY_JOURNAL_INVALID")
                return view
        return inspect_saved(identity)
    except Exception as error:
        code = error.code if isinstance(error, CLIError) else "QUERY_EVIDENCE_INVALID"
        return projection("status", identity, execution="read-only", error_code=code), 1


def runner():
    import run_query
    return run_query


def invoke(identity, lifecycle):
    require(STATE.parent.resolve() == STATE.parent and STATE.parent.is_dir(), "ENVIRONMENT_NOT_READY")
    for base in (STATE, EVIDENCE):
        if not base.exists() and not base.is_symlink():
            base.mkdir(mode=0o700); sync(base.parent)
        private_directory(base, legacy_parent=base == EVIDENCE)
    directory, output = STATE / identity, EVIDENCE / identity
    require(not directory.exists() and not directory.is_symlink() and not output.exists() and not output.is_symlink(), "QUERY_ALREADY_EXISTS")
    try:
        R = runner()
        config = R.load_config(LOCAL / "llm/providers.qwen-local.json")
        R.validate_config(config)
        require(config["active_provider"] == "local", "ENVIRONMENT_NOT_READY")
    except Exception:
        raise CLIError("ENVIRONMENT_NOT_READY") from None
    directory.mkdir(mode=0o700); sync(STATE)
    with lock(directory / "run.lock", create=True) as busy:
        require(not busy, "QUERY_BUSY")
        journal = dict(schema_version=1, evidence_token=identity, case=CASE, invocations=1, state="invoked", files=None)
        persist(directory, journal)  # Durable uncertainty precedes the sole invocation.
        try:
            with R.SESSION.interruption_handlers():
                lifecycle["invoked"] = True
                returned = asyncio.run(R.run(config, output))
            raw, hashes = snapshot(output)
            require(same(loads(raw.get("result.json", b"null")), returned))
            report, _ = inspect_saved(identity, command="run", journal=journal)
            journal.update(state="returned", files=hashes)
            persist(directory, journal)
            return report, 0 if report["result"] == "passed" else 1
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


class Discard(io.TextIOBase):
    def write(self, value):
        return len(value)

    def flush(self):
        pass


def arguments(argv):
    parser = Parser(description=__doc__, allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
    for action in ("run", "status"):
        command = sub.add_parser(action, allow_abbrev=False)
        command.add_argument("--evidence-token", required=True)
        if action == "run":
            command.add_argument("--case", choices=(CASE,), required=True)
    return parser.parse_args(argv)


def emit(stream, value):
    stream.write(canonical(value) + "\n"); stream.flush()


def main(argv=None, *, stdout=None):
    stream = stdout or sys.stdout
    action, identity = "run", None
    lifecycle = {"invoked": False}
    try:
        args = arguments(argv)
        action, identity = args.command, token(args.evidence_token)
        emit(stream, dict(schema_version=1, event="allocated", command=action, evidence_token=identity))
        with redirect_stdout(Discard()), redirect_stderr(Discard()):
            if action == "status":
                report, code = inspect_query(identity)
            else:
                report, code = invoke(identity, lifecycle)
        emit(stream, report)
        return code
    except BaseException as error:
        if isinstance(error, SystemExit):
            raise
        code = error.code if isinstance(error, CLIError) else "INTERRUPTED" if not isinstance(error, Exception) else "QUERY_FAILED"
        # An allocated identity alone cannot prove that run() was invoked.
        intent = lifecycle["invoked"]
        report = projection(action, identity, result="unknown" if intent or action == "status" else "not_started",
            execution="read-only" if action == "status" else "invoked" if intent else "not-started", error_code=code)
        emit(stream, report)
        return 128 + getattr(error, "signum", 2) if not isinstance(error, Exception) else 1


if __name__ == "__main__":
    raise SystemExit(main())
