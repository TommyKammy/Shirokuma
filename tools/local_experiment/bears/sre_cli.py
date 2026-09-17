"""SRE JSONL bridge: fixed lab reads once, saved-evidence status only.

Old three-source receipts and pre-container four-source receipts are checked
against their recorded scope. Missing old snapshots remain unknown, not healthy.
MCP returned span IDs are correlation evidence; only local model/root spans are
saved spans. No status path imports a workflow runner or observes live services.
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
import sre_contract as C
from query_cli import canonical, same, loads, private_directory, read, sync, lock, Discard

STATE = ROOT / "tmp/local-experiment/sre-cli"
EVIDENCE = ROOT / "tmp/local-experiment/sre-llm"
CASE = "local-lab"
MAX_BYTES = 1024 * 1024
MAX_PUBLIC_BYTES = 4096
FILES = frozenset({"result.json", "spans.jsonl", "observations.json", "gateway-stderr.log",
                   "replay-verification.json", "interruption.json"})
ERROR_CODES = frozenset({"ARGUMENTS_INVALID", "EVIDENCE_TOKEN_INVALID", "ENVIRONMENT_NOT_READY",
    "SRE_NOT_FOUND", "SRE_BUSY", "SRE_ALREADY_EXISTS", "SRE_JOURNAL_INVALID",
    "SRE_EVIDENCE_INVALID", "SRE_FAILED", "INTERRUPTED"})
RESULTS = frozenset({"passed", "failed", "unknown", "not_started", "not_found", "busy"})
FINAL_KEYS = frozenset({"schema_version", "event", "command", "evidence_token", "workflow_token",
    "result", "execution", "verification", "assessment", "root_cause", "remediation_executed", "error_code", "evidence"})
USAGE_KEYS = frozenset({"source", "availability", "initial", "followup", "comparison"})


class CLIError(ValueError):
    def __init__(self, code):
        self.code = code if code in ERROR_CODES else "SRE_EVIDENCE_INVALID"
        super().__init__(self.code)


def require(condition, code="SRE_EVIDENCE_INVALID"):
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
               workflow_token=None, assessment=None, error_code=None, has_evidence=False,
               usage=False, resource_usage=None):
    require(type(usage) is bool and (not usage or command == "status"))
    value = dict(schema_version=2 if usage else 1, event="result", command=command, evidence_token=identity,
        workflow_token=workflow_token, result=result, execution=execution, verification=verification,
        assessment=assessment, root_cause="undetermined", remediation_executed=False, error_code=error_code,
        evidence="tmp/local-experiment/sre-llm/" + identity if has_evidence else None)
    if usage:
        value["resource_usage"] = resource_usage if resource_usage is not None else usage_projection()
    else:
        require(resource_usage is None)
    return validate_projection(value)


def usage_projection(initial=None, followup=None, comparison=None, *, verified=False):
    """Only values derived inside the verified saved snapshot reach schema2."""
    return dict(source="saved-evidence", availability=("available" if initial is not None or followup is not None
                else "legacy-absent") if verified else "unverified",
                initial=copy.deepcopy(initial) if verified else None,
                followup=copy.deepcopy(followup) if verified else None,
                comparison=copy.deepcopy(comparison) if verified and (initial is not None or followup is not None) else None)


def validate_public_usage(value, *, verified):
    require(type(value) is dict and set(value) == USAGE_KEYS and value["source"] == "saved-evidence" and
            value["availability"] in ("available", "legacy-absent", "unverified"))
    require((value["availability"] != "unverified") == verified)
    if value["availability"] != "available":
        require(all(value[key] is None for key in ("initial", "followup", "comparison")))
        return
    initial, followup, comparison = (value[key] for key in ("initial", "followup", "comparison"))
    require(initial is not None or followup is not None)
    from tools.local_experiment.gateway.pod_usage import validate as validate_usage
    try:
        for item in (initial, followup):
            if item is not None: validate_usage(item)
        require(type(comparison) is dict and set(comparison) == {"before_state", "after_state", "comparison", "limitations"})
        expected = C._compare_resource_usage(initial, followup,
                                             reobserved=comparison["after_state"] != "not-reobserved")
        require(same(comparison, expected))
    except (ValueError, TypeError, KeyError):
        raise CLIError("SRE_EVIDENCE_INVALID") from None


def validate_projection(value):
    require(type(value) is dict and type(value.get("schema_version")) is int and value["schema_version"] in (1, 2))
    usage = value["schema_version"] == 2
    require(set(value) == FINAL_KEYS | ({"resource_usage"} if usage else set()) and
        (not usage or value["command"] == "status") and value["event"] == "result" and value["command"] in ("run", "status")
        and value["result"] in RESULTS and value["execution"] in ("not-started", "invoked", "read-only")
        and value["verification"] in ("not-checked", "journal-only", "saved-evidence")
        and value["assessment"] in (None, "attention-required", "healthy-observed-scope", "unknown")
        and value["root_cause"] == "undetermined" and value["remediation_executed"] is False
        and (value["error_code"] is None or value["error_code"] in ERROR_CODES))
    for key in ("evidence_token", "workflow_token"):
        if value[key] is not None: token(value[key])
    require((value["command"] == "status") == (value["execution"] == "read-only"))
    require(value["evidence"] is None or value["evidence_token"] is not None and
            value["evidence"] == "tmp/local-experiment/sre-llm/" + value["evidence_token"])
    if value["result"] == "passed":
        require(value["workflow_token"] is not None and value["assessment"] is not None
            and value["verification"] == "saved-evidence" and value["evidence"] is not None and value["error_code"] is None)
    if value["result"] == "failed":
        require(value["workflow_token"] is not None and value["verification"] == "saved-evidence")
    if usage:
        validate_public_usage(value["resource_usage"], verified=value["result"] == "passed" and
                              value["verification"] == "saved-evidence")
    return value


def source_hash(value):
    # This is the existing workflow serialization, distinct from CLI file hashes.
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def compatible_evidence(initial, saved):
    rebuilt = C.build_evidence(*(initial[t] for t in C.TOOLS[:3]), initial.get(C.TOOLS[3]))
    old_three = set(initial) == set(C.TOOLS[:3])
    if old_three:
        require(set(saved.get("observations", {})) == {"doctor", "pods", "events"})
        rebuilt["observations"].pop("platform")
        rebuilt["findings"] = [f for f in rebuilt["findings"] if not (f["code"] == "OBSERVATION_UNKNOWN" and f["subject"] == "platform")]
        for i, finding in enumerate(rebuilt["findings"], 1): finding["id"] = f"f{i:02}"
        rebuilt["health"] = ("attention-required" if any(f["code"] not in C.UNKNOWN_FINDINGS for f in rebuilt["findings"])
            else "unknown" if rebuilt["findings"] or any(v["completeness"] != "complete" for v in rebuilt["observations"].values())
            else "healthy-observed-scope")
    legacy_details = "container_details" not in saved.get("observations", {}).get("pods", {})
    if legacy_details:
        require(all("container_observation" not in row for row in initial["k8s_read_pods"]["pods"]))
        rebuilt["observations"]["pods"].pop("container_details", None)
    require(same(rebuilt, saved))
    return old_three, legacy_details


def validate_triage(value, evidence, *, old_three=False):
    if not old_three:
        return C.validate_triage(value, evidence)
    # Historical three-source receipts predate platform observations. Validate
    # their exact original selection rules without inventing a fourth read.
    C._keys(value, ("assessment", "checks", "root_cause"))
    require(value["assessment"] == evidence["health"] and value["root_cause"] == "undetermined")
    checks, findings = value["checks"], {f["id"]: f for f in evidence["findings"]}
    require(type(checks) is list and len(checks) <= 2 and bool(checks) == bool(findings))
    seen, covered = set(), set()
    for check in checks:
        C._keys(check, ("tool", "finding_ids"))
        tool, ids = check["tool"], check["finding_ids"]
        require(type(tool) is str and tool in C.TOOLS[:3] and tool not in seen
            and type(ids) is list and ids and all(type(i) is str and i in findings for i in ids)
            and len(ids) == len(set(ids)) and all(tool in findings[i]["allowed_tools"] for i in ids))
        seen.add(tool)
        if tool == "k8s_read_pods": covered.update(ids)
    require({i for i,f in findings.items() if f["code"] in C.POD_CRITICAL} <= covered)
    return value


def legacy_comparison(before, after):
    """Replay the original three-source UID comparison; no fourth source exists."""
    sources = {}
    for tool in C.TOOLS[:3]:
        source = C.SOURCES[tool]
        prior = C._observation(source, before[tool])
        item = dict(status="not-reobserved", before_observed_at=prior["observed_at"],
                    after_observed_at=None, changes=[], limitations=["bounded-snapshots-only"])
        sources[tool] = item
        if tool not in after: continue
        current = C._observation(source, after[tool])
        item["after_observed_at"] = current["observed_at"]
        times = [C._timestamp(v["observed_at"]) for v in (prior,current)]
        if None in times or times[1] < times[0]:
            item["status"] = "incomparable"; item["limitations"].append("missing-or-reversed-observation-time"); continue
        left, right = C._rows(source,before[tool]), C._rows(source,after[tool])
        if left is None or right is None:
            item["status"] = "incomparable"; item["limitations"].append("missing-object-uid"); continue
        incomplete = any(v["completeness"] != "complete" for v in (prior,current))
        if incomplete: item["limitations"].append("incomplete-observation-lists")
        changes, replaced = item["changes"], set()
        if source == "pods":
            old_names = {row["subject"]: uid for uid,row in left.items()}
            for uid,row in right.items():
                old_uid = old_names.get(row["subject"])
                if old_uid is not None and old_uid != uid:
                    changes.append(dict(kind="pod-replaced",subject=row["subject"],before_uid=old_uid,after_uid=uid))
                    replaced.update((uid,old_uid))
        for uid in sorted(set(left)|set(right)):
            if uid in replaced: continue
            old,new = left.get(uid),right.get(uid)
            if source == "pods":
                for row in (old,new):
                    if row is not None: require("container_observation" not in row)
            if old is None or new is None:
                row = new if old is None else old
                changes.append(dict(kind=source+("-first-observed" if old is None else "-no-longer-observed"),subject=row["subject"],identity=uid))
            elif old != new:
                change = dict(kind=source+"-changed",subject=new["subject"],identity=uid,before=old,after=new)
                if source == "events":
                    if new["count"] < old["count"]: item["limitations"].append("event-count-decreased")
                    else: change["count_delta"] = new["count"]-old["count"]
                if source == "pods" and new["restarts"] < old["restarts"]: item["limitations"].append("pod-restart-count-decreased")
                changes.append(change)
        item["status"] = "changed" if changes else "incomparable" if incomplete else "no-change-observed"
        item["limitations"] = list(dict.fromkeys(item["limitations"]))
    return dict(scope="selected-bounded-snapshots",root_cause="undetermined",sources=sources)


def compatible_comparison(initial, followup, *, old_three, legacy_details):
    if old_three:
        return legacy_comparison(initial, followup)
    value = C.compare_observations(initial, followup)
    if legacy_details:
        require(all("container_observation" not in row for snap in [initial.get("k8s_read_pods"), followup.get("k8s_read_pods")]
                    if snap for row in snap["pods"]))
        row = value["sources"]["k8s_read_pods"]
        row["limitations"] = [x for x in row["limitations"] if x not in
            ("container-process-identity-not-observed", "no-recovery-claim", "container-details-not-observed")]
    return value


def audits_and_spans(docs, result):
    calls, records = result.get("tool_calls"), result.get("audit_records")
    require(type(calls) is list and len(calls) <= 6 and type(records) is list and len(records) <= 2)
    events, span_ids, phases = set(), set(), set()
    for call in calls:
        C._keys(call, ("tool", "phase", "event_id", "span_id", "action_type"))
        require(call["tool"] in C.TOOLS and call["phase"] in ("initial", "followup")
                and call["action_type"] == "mcp." + call["tool"] and (call["tool"],call["phase"]) not in phases)
        phases.add((call["tool"],call["phase"]))
    for row in calls + records:
        require(type(row) is dict and type(row.get("event_id")) is str and str(uuid.UUID(row["event_id"])) == row["event_id"]
            and row["event_id"] not in events and type(row.get("span_id")) is str
            and re.fullmatch("[0-9a-f]{16}",row["span_id"]) and row["span_id"] not in span_ids)
        events.add(row["event_id"]); span_ids.add(row["span_id"])
    require(len({r.get("action_type") for r in records}) == len(records))
    for row in records:
        require(row.get("action_type") in ("llm.sre_triage", "bear.sre.assess")
            and row.get("task_id") == result["task_id"] and row.get("trace_id") == result["trace_id"]
            and row.get("status") in ("success", "blocked", "failed") and row.get("audit_delivery") in ("pending", "delivered", "failed"))
        for key in ("prompt_tokens", "completion_tokens", "duration_ms", "inference_cost_estimate"):
            v = row.get(key)
            require(v is None or type(v) in (int,float) and math.isfinite(v) and 0 <= v < 2**63)
    expected = {r["span_id"]:r for r in records}
    spans, seen, root = docs.get("spans.jsonl", []), set(), None
    require(type(spans) is list and len(spans) <= 2)
    for span in spans:
        require(type(span) is dict and span.get("span_id") in expected and span["span_id"] not in seen)
        row = expected[span["span_id"]]
        status = "OK" if row["status"] == "success" else "ERROR"
        require(span.get("name") == row["action_type"] and span.get("trace_id") == result["trace_id"]
            and (span.get("status") == status or span.get("name") == "bear.sre.assess" and
                 span.get("status") == "ERROR" and result["result"] != "passed")
            and type(span.get("start_time_unix_nano")) is int and type(span.get("end_time_unix_nano")) is int
            and 0 < span["start_time_unix_nano"] <= span["end_time_unix_nano"])
        seen.add(span["span_id"])
        if span["name"] == "bear.sre.assess":
            require(span.get("attributes", {}).get("task_id") == result["task_id"])
            root = span
    if root is not None:
        require(seen == set(expected) and all(root["start_time_unix_nano"] <= s["start_time_unix_nano"]
            <= s["end_time_unix_nano"] <= root["end_time_unix_nano"] for s in spans))
    return root


def verify_documents(raw, *, usage=False):
    # stderr remains a hashed, private opaque file. It never enters a parser,
    # safe projection, diagnosis, assessment or model-facing evidence.
    docs = {name: ([loads(line) for line in value.splitlines() if line] if name == "spans.jsonl" else loads(value))
            for name,value in raw.items() if name != "gateway-stderr.log"}
    empty = dict(result="unknown", verification="journal-only", workflow_token=None, assessment=None)
    if "result.json" not in docs: return empty
    result = docs["result.json"]
    require(type(result) is dict and type(result.get("task_id")) is str and re.fullmatch("workflow-[0-9a-f]{32}",result["task_id"]))
    inner = result["task_id"][9:]
    require(result.get("result") in ("passed", "failed", "interrupted") and result.get("scope") == "local-read-only-sre-followup"
        and result.get("remediation_executed") is False and type(result.get("trace_id")) is str
        and re.fullmatch("[0-9a-f]{32}",result["trace_id"]))
    root = audits_and_spans(docs,result)
    receipts = docs.get("observations.json")
    initial, followup = {}, {}
    if receipts is not None:
        require(type(receipts) is list and len(receipts) <= 6 and len(receipts) <= len(result["tool_calls"]))
        for receipt, call in zip(receipts,result["tool_calls"]):
            C._keys(receipt,("tool","phase","snapshot"))
            require(receipt["tool"] == call["tool"] and receipt["phase"] == call["phase"])
            tool, phase, snap = receipt["tool"],receipt["phase"],receipt["snapshot"]
            require(same(C.project_observations({tool:snap})[tool],snap))
            (initial if phase == "initial" else followup)[tool] = snap
        require(list(initial) == list(C.TOOLS[:len(initial)]))
    if result["result"] == "passed" and receipts is None:
        return dict(empty,workflow_token=inner)
    if result["result"] == "passed":
        require(root is not None and root["status"] == "OK" and result.get("checkpoint") == "completed-in-memory"
            and set(initial) in (set(C.TOOLS[:3]),set(C.TOOLS)) and len(receipts) == len(result["tool_calls"]))
        old_three, legacy_details = compatible_evidence(initial,result.get("evidence",{}))
        triage = validate_triage(result.get("triage"),result["evidence"],old_three=old_three)
        require(list(followup) == [c["tool"] for c in triage["checks"]]
            and [(c["phase"],c["tool"]) for c in result["tool_calls"]] == [("initial",t) for t in initial] + [("followup",t) for t in followup])
        require(same(compatible_comparison(initial,followup,old_three=old_three,legacy_details=legacy_details),result.get("comparisons"))
            and result.get("initial_sha256") == source_hash(initial)
            and result.get("followup_sha256") == {t:source_hash(s) for t,s in followup.items()})
        records = result["audit_records"]
        require([r["action_type"] for r in records] == ["llm.sre_triage","bear.sre.assess"]
            and all(r["status"] == "success" and r["audit_delivery"] == "delivered" for r in records)
            and type(result.get("correlated_events")) is int and result["correlated_events"] == len(records)+len(receipts))
        require(type(result.get("inference")) is dict and all(same(result["inference"].get(k),records[0].get(k))
            for k in ("prompt_tokens","completion_tokens","duration_ms","inference_cost_estimate")))
        view = dict(result="passed",verification="saved-evidence",workflow_token=inner,assessment=triage["assessment"])
        if usage:
            view["resource_usage"] = usage_projection(initial["k8s_read_pods"].get("resource_usage"),
                followup.get("k8s_read_pods", {}).get("resource_usage"),
                result["comparisons"]["sources"]["k8s_read_pods"].get("resource_usage"), verified=True)
        return view
    if root is not None:
        require("checkpoint" not in result and result.get("error_code") in
                ("SRE_OBSERVATION_INFERENCE_OR_AUDIT_FAILED","SRE_SAVED_TRACE_VERIFICATION_FAILED"))
        return dict(result="failed",verification="saved-evidence",workflow_token=inner,assessment=None)
    return dict(empty,workflow_token=inner)


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
    C._keys(value, ("schema_version", "evidence_token", "case", "invocations", "state", "files"))
    require(type(value["schema_version"]) is int and value["schema_version"] == 1
        and value["evidence_token"] == identity and value["case"] == CASE
        and type(value["invocations"]) is int and value["invocations"] == 1
        and value["state"] in ("invoked", "returned", "interrupted", "unknown"), "SRE_JOURNAL_INVALID")
    files = value["files"]
    require(files is None or type(files) is dict and set(files) <= FILES and all(
        type(v) is str and re.fullmatch("[0-9a-f]{64}", v) for v in files.values()), "SRE_JOURNAL_INVALID")
    require(value["state"] != "returned" or files is not None, "SRE_JOURNAL_INVALID")
    return value


def inspect_saved(identity, *, command="status", journal=None, usage=False):
    require(type(usage) is bool and (not usage or command == "status"))
    output = EVIDENCE / identity
    if EVIDENCE.exists() or EVIDENCE.is_symlink():
        private_directory(EVIDENCE, legacy_parent=True)
    if not output.exists() and not output.is_symlink():
        return projection(command, identity, execution="read-only" if command == "status" else "invoked",
            result="unknown" if journal else "not_found", verification="journal-only" if journal else "not-checked",
            error_code=None if journal else "SRE_NOT_FOUND", usage=usage), 0 if journal else 1
    private_directory(EVIDENCE, legacy_parent=True)
    raw, hashes = snapshot(output)
    if journal is not None and journal["files"] is not None:
        require(journal["files"] == hashes)
    view = verify_documents(raw, usage=usage)
    require(snapshot(output) == (raw, hashes))
    return projection(command, identity, execution="read-only" if command == "status" else "invoked",
        has_evidence=True, error_code="SRE_FAILED" if view["result"] == "failed" else None, usage=usage, **view), 0


def inspect_sre(identity, *, usage=False):
    require(type(usage) is bool)
    token(identity)
    try:
        directory = STATE / identity
        if STATE.exists() or STATE.is_symlink():
            private_directory(STATE)
        if directory.exists() or directory.is_symlink():
            private_directory(directory)
            require({p.name for p in directory.iterdir()} == {"intent.json", "run.lock"}, "SRE_JOURNAL_INVALID")
            with lock(directory / "run.lock") as busy:
                original = read(directory / "intent.json")
                journal = validate_journal(loads(original), identity)
                if busy:
                    return projection("status", identity, result="busy", execution="read-only",
                        verification="journal-only", error_code="SRE_BUSY", usage=usage), 0
                view = inspect_saved(identity, journal=journal, usage=usage)
                require(read(directory / "intent.json") == original, "SRE_JOURNAL_INVALID")
                return view
        return inspect_saved(identity, usage=usage)
    except Exception as error:
        code = error.code if isinstance(error, CLIError) else "SRE_EVIDENCE_INVALID"
        return projection("status", identity, execution="read-only", error_code=code, usage=usage), 1


def runner():
    import run_sre_llm
    return run_sre_llm


def configured_runner():
    R = runner()
    from providers import validate_config
    config = R.load_config(LOCAL / "llm/providers.qwen-local.json")
    validate_config(config)
    require(config["active_provider"] == "local", "ENVIRONMENT_NOT_READY")
    return R, config


def invoke(identity, lifecycle):
    require(STATE.parent.resolve() == STATE.parent and STATE.parent.is_dir(), "ENVIRONMENT_NOT_READY")
    for base in (STATE, EVIDENCE):
        if not base.exists() and not base.is_symlink():
            base.mkdir(mode=0o700); sync(base.parent)
        private_directory(base, legacy_parent=base == EVIDENCE)
    directory, output = STATE / identity, EVIDENCE / identity
    require(not directory.exists() and not directory.is_symlink() and not output.exists() and not output.is_symlink(), "SRE_ALREADY_EXISTS")
    try:
        R, config = configured_runner()
    except Exception:
        raise CLIError("ENVIRONMENT_NOT_READY") from None
    from local_session import interruption_handlers
    directory.mkdir(mode=0o700); sync(STATE)
    with lock(directory / "run.lock", create=True) as busy:
        require(not busy, "SRE_BUSY")
        journal = dict(schema_version=1, evidence_token=identity, case=CASE, invocations=1, state="invoked", files=None)
        persist(directory, journal)  # Durable uncertainty precedes the sole invocation.
        try:
            with interruption_handlers():
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


def arguments(argv):
    parser = Parser(description=__doc__, allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
    for action in ("run", "status"):
        command = sub.add_parser(action, allow_abbrev=False)
        command.add_argument("--evidence-token", required=True)
        if action == "status":
            command.add_argument("--usage", action="store_true")
    return parser.parse_args(argv)


def emit(stream, value):
    encoded = canonical(value) + "\n"
    require(len(encoded.encode("utf-8")) <= MAX_PUBLIC_BYTES)
    stream.write(encoded); stream.flush()


def main(argv=None, *, stdout=None):
    stream = stdout or sys.stdout
    action, identity = "run", None
    usage = False
    lifecycle = {"invoked": False}
    try:
        args = arguments(argv)
        # Once the opt-in is parsed, even a rejected token uses its schema2
        # status failure frame; no allocation or saved-file read has occurred.
        if args.command == "status" and args.usage:
            action, usage = "status", True
        action, identity = args.command, token(args.evidence_token)
        usage = action == "status" and args.usage
        emit(stream, dict(schema_version=1, event="allocated", command=action, evidence_token=identity))
        with redirect_stdout(Discard()), redirect_stderr(Discard()):
            if action == "status":
                report, code = inspect_sre(identity, usage=usage)
            else:
                report, code = invoke(identity, lifecycle)
        emit(stream, report)
        return code
    except BaseException as error:
        if isinstance(error, SystemExit):
            raise
        code = error.code if isinstance(error, CLIError) else "INTERRUPTED" if not isinstance(error, Exception) else "SRE_FAILED"
        # An allocated identity alone cannot prove that run() was invoked.
        intent = lifecycle["invoked"]
        report = projection(action, identity, result="unknown" if intent or action == "status" else "not_started",
            execution="read-only" if action == "status" else "invoked" if intent else "not-started", error_code=code, usage=usage)
        try:
            emit(stream, report)
        except BaseException:
            if not isinstance(error, Exception):
                raise error
            raise
        return 128 + getattr(error, "signum", 2) if not isinstance(error, Exception) else 1


if __name__ == "__main__":
    raise SystemExit(main())
