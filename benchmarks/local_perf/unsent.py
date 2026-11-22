"""Offline proof for a confirmed pre-send TCP connect refusal.

This module never opens a network connection.  It only reads the immutable
attempt/run/manifest files and explicitly supplied historical evidence, and
derives whether the public initial POST failed *before* ``conn.request``.

Transport basis (``benchmarks/local_perf/runner.py``):
``TrinoAdapter.request`` measures ``connect`` around ``conn.connect()`` and
``send`` around ``conn.request(...)``.  ``Timings.measure`` writes the phase in
a ``finally`` block, so a phase is non-null even when the wrapped call raises.
A connect-only ``ConnectionRefusedError`` therefore leaves ``connect`` set,
``send``/``header_wait``/``body_read``/``decode`` null, one initial call and no
follow-up call.  If ``conn.request`` had been entered, ``send`` would be set.
No HTTP request bytes can have reached the server's TCP stack.
"""
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time
import urllib.parse
import uuid


NOT_SENT_NAME = "not-sent.json"
EVIDENCE_DIR = "not-sent-evidence"
KIND = "verified-pre-send-connect-refusal"
PROCEDURE = "offline-transport-proof-v1"
PUBLIC = "shirokuma-public"
HTTP_PHASES = ("connect", "send", "header_wait", "body_read", "decode", "total")
EVENTS = ("dispatch_started", "initial_response_complete", "first_row", "last_row",
          "terminal_observed", "result_complete", "validation_started",
          "validation_complete", "recovery_started", "recovery_complete",
          "attempt_complete")
DURATIONS = ("artifact_processing_s", "query_execute_s", "local_fetch_s", "client_poll_wait_s")
TERMINAL_EVENTS = ("initial_response_complete", "first_row", "last_row", "terminal_observed",
                   "result_complete", "validation_started", "validation_complete")
UUID = re.compile(r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$")


class UnsentError(ValueError):
    pass


def _require(value, message):
    if not value:
        raise UnsentError(message)


def _finite(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value))


def _positive(value):
    return _finite(value) and value > 0


def _nonnegative(value):
    return _finite(value) and value >= 0


def _text(value):
    return isinstance(value, str) and bool(value) and "\x00" not in value


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 ** 2), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value):
    return sha256_bytes(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=False, allow_nan=False).encode("utf8"))


def _loopback(origin):
    if (not isinstance(origin, list) or len(origin) != 3
            or origin[0] not in ("http", "https") or not isinstance(origin[1], str)
            or not isinstance(origin[2], int) or isinstance(origin[2], bool)
            or not 1 <= origin[2] <= 65535):
        return False
    host = origin[1]
    return host in ("localhost", "::1") or bool(re.fullmatch(r"127(?:\.[0-9]{1,3}){3}", host))


def valid_context(value):
    keys = {"database_id", "schema_id", "warehouse_id", "sql_catalog", "sql_schema",
            "selected_role", "binding_revision", "generation_id", "grant_revision"}
    if not isinstance(value, dict) or set(value) != keys:
        return False
    if (any(not _text(value[k]) for k in keys if k not in ("binding_revision", "grant_revision"))
            or value["warehouse_id"] != "LOCAL"
            or not isinstance(value["binding_revision"], int) or isinstance(value["binding_revision"], bool)
            or value["binding_revision"] < 1
            or (value["grant_revision"] is not None
                and (not isinstance(value["grant_revision"], int)
                     or isinstance(value["grant_revision"], bool) or value["grant_revision"] < 1))):
        return False
    return True


def transport_proof(intent):
    """Derive a compact proof from one immutable attempt.  No file or network I/O."""
    _require(isinstance(intent, dict), "attempt object required")
    identity, dispatch = intent.get("adapter_identity"), intent.get("dispatch")
    _require(isinstance(identity, dict) and identity.get("kind") == PUBLIC,
             "public adapter identity required")
    _require(_loopback(identity.get("origin")), "public loopback origin required")
    _require(_text(identity.get("account")) and len(identity["account"]) <= 128,
             "public account required")
    _require(_text(identity.get("selected_role")) and len(identity["selected_role"]) <= 128,
             "public selected role required")
    context = identity.get("context")
    _require(valid_context(context), "public context required")
    _require(context["selected_role"] == identity["selected_role"], "role/context mismatch")
    if identity.get("max_rows") is None:
        _require(identity.get("result_mode") == "all", "all-results identity required")
    else:
        _require(isinstance(identity["max_rows"], int) and not isinstance(identity["max_rows"], bool)
                 and 1 <= identity["max_rows"] <= 10000 and "result_mode" not in identity,
                 "invalid preview identity")

    _require(intent.get("status") == "UNKNOWN", "attempt must remain UNKNOWN")
    _require("handle" in intent and intent.get("handle") is None,
             "attempt handle field must be present and null")
    _require("submission_rejection" not in intent or intent.get("submission_rejection") is None,
             "submission rejection contradicts pre-send refusal")
    _require("recovered_handle" not in intent or intent.get("recovered_handle") is None,
             "recovered handle contradicts pre-send refusal")
    _require(intent.get("error_type") == "ConnectionRefusedError", "connect refusal required")
    _require(intent.get("error_code") == "PUBLIC_QUERY_UNMET", "public failure code required")
    _require(intent.get("reconciliation") == "UNKNOWN", "supported reconcile must remain UNKNOWN")
    _require(intent.get("mode") == "read-only", "read-only mode required")
    _require(_text(intent.get("sql")) and _positive(intent.get("wall_started")), "attempt SQL/start required")
    _require(intent.get("sql_sha256") == sha256_bytes(intent["sql"].encode("utf8")),
             "attempt SQL fingerprint mismatch")
    _require(_positive(intent.get("timeout_s")), "positive timeout required")
    _require(_finite(intent.get("elapsed_s")) and intent["elapsed_s"] >= 0, "elapsed required")
    request_id, worksheet_id = intent.get("request_id"), (dispatch or {}).get("worksheet_id")
    _require(isinstance(request_id, str) and UUID.fullmatch(request_id), "canonical request id required")
    _require(isinstance(worksheet_id, str) and UUID.fullmatch(worksheet_id), "canonical worksheet id required")
    _require(isinstance(dispatch, dict), "dispatch binding required")
    _require(dispatch.get("request_id") == request_id, "dispatch request id mismatch")
    _require(dispatch.get("context") == context, "dispatch context mismatch")

    timings = intent.get("timings")
    _require(isinstance(timings, dict) and timings.get("version") == 1, "timings version 1 required")
    _require(set(timings) == {"version", "clock", "event_units", "row_observation", "events",
                              "durations", "http", "backend_report"}, "timings shape mismatch")
    _require(timings.get("clock") == "monotonic", "monotonic timings required")
    _require(timings.get("event_units") == "seconds_from_attempt_start", "timing event units mismatch")
    _require(timings.get("row_observation") == "decoded_page_or_batch", "row observation mismatch")
    http = timings.get("http")
    _require(isinstance(http, dict) and set(http) == {"units", "initial", "follow_up"},
             "HTTP timing shape mismatch")
    _require(http.get("units") == "seconds", "HTTP timing units mismatch")
    initial, follow = http.get("initial"), http.get("follow_up")
    _require(isinstance(initial, dict) and isinstance(follow, dict), "HTTP phase groups required")
    _require(set(initial) == {"calls", *HTTP_PHASES}, "initial phase fields missing")
    _require(set(follow) == {"calls", *HTTP_PHASES}, "follow-up phase fields missing")
    _require(type(initial.get("calls")) is int and initial.get("calls") == 1,
             "exactly one integer initial call required")
    connect, total = initial.get("connect"), initial.get("total")
    _require(_positive(connect), "initial connect phase required")
    _require(_positive(total) and total + 1e-9 >= connect, "initial total must include connect")
    for phase in ("send", "header_wait", "body_read", "decode"):
        _require(initial.get(phase) is None, "initial request/response phase contradicts NOT_SENT")
    _require(type(follow.get("calls")) is int and follow.get("calls") == 0,
             "zero integer follow-up calls required")
    for phase in HTTP_PHASES:
        _require(follow.get(phase) is None, "follow-up phase contradicts NOT_SENT")

    events = timings.get("events")
    _require(isinstance(events, dict) and set(events) == set(EVENTS), "timing event fields missing")
    for event in ("dispatch_started", "recovery_started", "recovery_complete", "attempt_complete"):
        _require(_positive(events.get(event)), "recovery-complete transport sequence required")
    for event in TERMINAL_EVENTS:
        _require(events.get(event) is None, "response/terminal event contradicts NOT_SENT")
    _require(events["recovery_started"] >= events["dispatch_started"], "recovery order mismatch")
    _require(events["recovery_complete"] >= events["recovery_started"], "recovery order mismatch")
    _require(events["attempt_complete"] >= events["recovery_complete"], "attempt order mismatch")

    durations = timings.get("durations")
    _require(isinstance(durations, dict) and set(durations) == set(DURATIONS),
             "duration timing fields missing")
    _require(durations.get("query_execute_s") is None, "engine execution contradicts NOT_SENT")
    _require(durations.get("local_fetch_s") is None, "row fetch contradicts NOT_SENT")
    _require(durations.get("client_poll_wait_s") == 0, "client poll contradicts NOT_SENT")
    _require(_nonnegative(durations.get("artifact_processing_s")), "artifact duration required")

    backend = timings.get("backend_report")
    _require(isinstance(backend, dict) and set(backend) == {
        "source", "units", "client_observed", "page_state", "values"},
        "backend report shape mismatch")
    _require(backend.get("source") == "response.stats" and backend.get("units") == "milliseconds",
             "backend report units mismatch")
    _require(backend.get("client_observed") is False, "backend observation contradicts NOT_SENT")
    _require("page_state" in backend and backend.get("page_state") is None,
             "backend page state contradicts NOT_SENT")
    values = backend.get("values")
    _require(isinstance(values, dict) and set(values) == {
        "queuedTimeMillis", "cpuTimeMillis", "wallTimeMillis", "elapsedTimeMillis"},
        "backend report shape mismatch")
    _require(all(value is None for value in values.values()),
             "backend stats contradict NOT_SENT")

    return {
        "error_type": intent["error_type"],
        "error_code": intent["error_code"],
        "initial_calls": 1,
        "connect_s": connect,
        "send": None,
        "header_wait": None,
        "body_read": None,
        "decode": None,
        "total_s": total,
        "follow_up_calls": 0,
        "dispatch_started_s": events["dispatch_started"],
        "recovery_started_s": events["recovery_started"],
        "recovery_complete_s": events["recovery_complete"],
        "attempt_complete_s": events["attempt_complete"],
    }


def _exclusive_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        if path.read_bytes() != data:
            raise UnsentError("append-only evidence already exists with different bytes") from None
        return path
    with os.fdopen(descriptor, "wb") as target:
        target.write(data)
        target.flush()
        os.fsync(target.fileno())
    return path


def _exclusive_json(path, value):
    data = (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf8")
    _exclusive_bytes(path, data)


def write_json_new(path, value):
    """Atomically publish an append-only JSON receipt.

    The final name is created with a hard link only after the full body has been
    written and fsynced.  A killed writer leaves either no final name or a
    complete one, plus at most an unreferenced temporary file.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf8")
    temporary = path.with_name(path.name + ".new-" + uuid.uuid4().hex)
    descriptor = os.open(str(temporary), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise UnsentError("append-only receipt already exists with different bytes") from None
    finally:
        temporary.unlink(missing_ok=True)


def publish_evidence(staging, final_dir):
    """Atomically move a validated staging directory to its capture name."""
    staging, final_dir = Path(staging), Path(final_dir)
    _require(staging.is_dir(), "staged evidence missing")
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    _require(not final_dir.exists(), "capture directory collision")
    try:
        os.replace(staging, final_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return final_dir


def _copy_evidence(source, target):
    source = Path(source)
    _require(source.is_file(), "evidence file missing")
    data = source.read_bytes()
    _exclusive_bytes(target, data)
    return sha256_bytes(data)


def _parse_log_time(value, offset):
    text = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed.replace(tzinfo=dt.timezone(dt.timedelta(seconds=offset))).astimezone(dt.timezone.utc)
    raise UnsentError("unrecognized unified-log timestamp")


def _pid_from_termination(line):
    for pattern in (r"\([0-9]+\):([0-9]+)\]", r"PID\[([0-9]+)\]", r"\bpid[= ]([0-9]+)\b"):
        match = re.search(pattern, line)
        if match:
            return int(match.group(1))
    return None


def parse_process_log(text, pid, offset):
    """Return the unique proc_exit time for ``pid``; reject ambiguity/restart."""
    _require(isinstance(pid, int) and not isinstance(pid, bool) and pid > 0, "positive process id required")
    terminations, after_terminal = [], []
    terminal_time = None
    for line in text.splitlines():
        if "termination reported by proc_exit" in line:
            match = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)\s", line)
            _require(match is not None, "termination line without timestamp")
            found = _pid_from_termination(line)
            _require(found is not None, "termination line without pid")
            terminations.append((found, _parse_log_time(match.group(1), offset)))
        if "START PID[" in line or "Now tracking process" in line or "running-NotVisible" in line:
            match = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)\s", line)
            if match:
                found = _pid_from_termination(line)
                if found == pid:
                    after_terminal.append(_parse_log_time(match.group(1), offset))
    _require(len(terminations) == 1, "exactly one process termination required")
    found, terminal_time = terminations[0]
    _require(found == pid, "termination pid mismatch")
    _require(not after_terminal or max(after_terminal) <= terminal_time,
             "process lifecycle continues after termination")
    return terminal_time


def parse_server_log(text, wall_started, offset):
    """Return bounded access-log facts; any entry at/after the attempt is ambiguous."""
    pattern = re.compile(r'^(\S+) - - \[([^\]]+)\] "([A-Z]+) ([^"]*)" ([0-9]{3})')
    entries = []
    for line in text.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        local = dt.datetime.strptime(match.group(2), "%d/%b/%Y %H:%M:%S")
        when = local.replace(tzinfo=dt.timezone(dt.timedelta(seconds=offset))).astimezone(dt.timezone.utc)
        entries.append((when, match.group(3), match.group(4)))
    _require(entries, "server access log has no timestamped entries")
    attempt = dt.datetime.fromtimestamp(wall_started, dt.timezone.utc)
    after = [entry for entry in entries if entry[0] >= attempt]
    posts = [entry for entry in after if entry[1] == "POST" and entry[2] == "/api/v1/queries"]
    last = max(entry[0] for entry in entries)
    return {
        "entries": len(entries),
        "last_entry_utc": last.isoformat(timespec="microseconds"),
        "entries_after_wall_started": len(after),
        "post_requests_at_or_after_wall_started": len(posts),
    }


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def scan_state(db_path, request_id, worksheet_id, wall_started, window_s=60):
    """Read-only, transaction-consistent scan of the authoritative Workbench DB.

    Only counts are retained.  The database is opened with ``mode=ro`` and
    ``query_only``; no write, checkpoint or service action is performed.
    """
    db_path = Path(db_path).resolve()
    _require(db_path.is_file(), "state database missing")
    before = db_path.stat()
    digest = file_sha256(db_path)
    uri = "file:" + urllib.parse.quote(str(db_path), safe="/") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=10)
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        journal = connection.execute("PRAGMA journal_mode").fetchone()[0]
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        _require(tables, "state database has no tables")
        matches = []
        for table in tables:
            columns = [row[1] for row in connection.execute("PRAGMA table_info(" + _quote(table) + ")")]
            conditions, params = [], []
            for column in columns:
                fragment = "instr(CAST(" + _quote(column) + " AS TEXT), ?) > 0"
                conditions.extend((fragment, fragment))
                params.extend((request_id, worksheet_id))
            if not conditions:
                continue
            count = connection.execute(
                "SELECT COUNT(*) FROM " + _quote(table) + " WHERE " + " OR ".join(conditions),
                params).fetchone()[0]
            if count:
                matches.append({"table": table, "rows": count})
        attempt = dt.datetime.fromtimestamp(wall_started, dt.timezone.utc)
        start = (attempt - dt.timedelta(seconds=window_s)).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
        end = (attempt + dt.timedelta(seconds=window_s)).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")

        def window_count(table):
            names = {row[1] for row in connection.execute("PRAGMA table_info(" + _quote(table) + ")")}
            if "created_at" not in names:
                return None
            return connection.execute(
                "SELECT COUNT(*) FROM " + _quote(table)
                + " WHERE julianday(created_at) BETWEEN julianday(?) AND julianday(?)",
                (start, end)).fetchone()[0]

        execution_contexts = None
        names = {row[1] for row in connection.execute("PRAGMA table_info(\"query_execution_contexts\")")}
        if {"request_id", "worksheet_id"} <= names:
            execution_contexts = connection.execute(
                'SELECT COUNT(*) FROM "query_execution_contexts" WHERE request_id=? OR worksheet_id=?',
                (request_id, worksheet_id)).fetchone()[0]
        query_records_window = window_count("query_records")
        admissions_window = window_count("warehouse_admissions")
        connection.execute("ROLLBACK")
    finally:
        connection.close()
    after = db_path.stat()
    return {
        "format": 1,
        "kind": "readonly-workbench-state-scan",
        "db_path": str(db_path),
        "db_sha256": digest,
        "db_size": before.st_size,
        "db_mtime_ns": before.st_mtime_ns,
        "db_size_after": after.st_size,
        "db_mtime_ns_after": after.st_mtime_ns,
        "stable_during_scan": before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns,
        "sqlite_version": sqlite3.sqlite_version,
        "journal_mode": journal,
        "transaction": "mode=ro; query_only=ON; BEGIN/ROLLBACK; counts only",
        "window_start_utc": start,
        "window_end_utc": end,
        "window_seconds": window_s,
        "needles": [request_id, worksheet_id],
        "tables": tables,
        "tables_scanned": len(tables),
        "matching_rows": matches,
        "query_records_window": query_records_window,
        "admissions_window": admissions_window,
        "execution_contexts": execution_contexts,
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds"),
    }


def build_corroboration(attempt_dir, wall_started, request_id, worksheet_id, *,
                        process_log=None, process_pid=None, utc_offset_seconds=None,
                        server_log=None, state_db=None, state_window_s=60,
                        historical_evidence=(), process_source_command=None):
    """Stage corroborating evidence; publication happens only after validation.

    Files are written under a hidden staging directory first.  The caller
    atomically publishes the whole directory under a fresh
    ``not-sent-evidence/capture-<id>/`` name and only then writes the receipt.
    A failed or interrupted first confirmation therefore leaves no fixed-name
    evidence and cannot strand a later valid confirmation.
    """
    attempt_dir = Path(attempt_dir).resolve()
    offset = time.localtime().tm_gmtoff if utc_offset_seconds is None else utc_offset_seconds
    _require(isinstance(offset, int) and -86400 <= offset <= 86400, "UTC offset out of range")
    staging = Path(tempfile.mkdtemp(prefix=".not-sent-staging-", dir=attempt_dir))
    final_rel = Path(EVIDENCE_DIR) / ("capture-" + uuid.uuid4().hex)
    final_dir = attempt_dir / final_rel
    try:
        _require(not final_dir.exists(), "capture directory collision")
        process = None
        if process_log is not None:
            _require(process_pid is not None, "process pid required with process log")
            source = Path(process_log).resolve()
            target = staging / "process.log"
            source_sha = file_sha256(source)
            copy_sha = _copy_evidence(source, target)
            _require(source_sha == copy_sha, "process evidence copy changed")
            terminal = parse_process_log(target.read_text(encoding="utf8", errors="replace"),
                                         process_pid, offset)
            started = dt.datetime.fromtimestamp(wall_started, dt.timezone.utc)
            age = (started - terminal).total_seconds()
            _require(0 < age <= 3600, "termination must immediately precede the attempt")
            process = {
                "kind": "macos-unified-log-process-exit",
                "path": str(final_rel / "process.log"),
                "sha256": copy_sha,
                "source_path": str(source),
                "source_sha256": source_sha,
                "source_command": process_source_command,
                "pid": process_pid,
                "utc_offset_seconds": offset,
                "termination_utc": terminal.isoformat(timespec="microseconds"),
                "age_before_wall_started_s": age,
                "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds"),
                "limits": "the unified log identifies a process exit; pid-to-Workbench command linkage must come from contemporaneous ps evidence",
            }

        server = None
        if server_log is not None:
            source = Path(server_log).resolve()
            target = staging / "server.log"
            source_sha = file_sha256(source)
            copy_sha = _copy_evidence(source, target)
            _require(source_sha == copy_sha, "server evidence copy changed")
            facts = parse_server_log(target.read_text(encoding="utf8", errors="replace"),
                                     wall_started, offset)
            _require(facts["post_requests_at_or_after_wall_started"] == 0,
                     "server access log contains a POST at/after the attempt")
            _require(facts["entries_after_wall_started"] == 0,
                     "server access log continues after the attempt")
            server = {
                "kind": "workbench-access-log",
                "path": str(final_rel / "server.log"),
                "sha256": copy_sha,
                "source_path": str(source),
                "source_sha256": source_sha,
                "utc_offset_seconds": offset,
                "entries": facts["entries"],
                "last_entry_utc": facts["last_entry_utc"],
                "entries_after_wall_started": facts["entries_after_wall_started"],
                "post_requests_at_or_after_wall_started": facts["post_requests_at_or_after_wall_started"],
                "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds"),
                "limits": "scoped to the captured server process access log; not a cluster-wide request log",
            }

        state = None
        if state_db is not None:
            target = staging / "state-scan.json"
            scan = scan_state(state_db, request_id, worksheet_id, wall_started, window_s=state_window_s)
            _exclusive_json(target, scan)
            raw = json.loads(target.read_text())
            _require(raw == scan, "state scan copy mismatch")
            _require(not raw["matching_rows"], "state database contains matching request ids")
            _require(raw["query_records_window"] == 0, "query record exists in failure window")
            _require(raw["admissions_window"] == 0, "warehouse admission exists in failure window")
            _require(raw["execution_contexts"] == 0, "execution context exists for request ids")
            state = {
                "kind": "readonly-workbench-state-scan",
                "path": str(final_rel / "state-scan.json"),
                "sha256": file_sha256(target),
                "source_path": raw["db_path"],
                "source_sha256": raw["db_sha256"],
                "db_path": raw["db_path"],
                "db_sha256": raw["db_sha256"],
                "db_size": raw["db_size"],
                "db_mtime_ns": raw["db_mtime_ns"],
                "window_seconds": raw["window_seconds"],
                "tables_scanned": raw["tables_scanned"],
                "matching_rows": raw["matching_rows"],
                "query_records_window": raw["query_records_window"],
                "admissions_window": raw["admissions_window"],
                "execution_contexts": raw["execution_contexts"],
                "captured_at": raw["captured_at"],
                "limits": "absence of rows is corroboration only; the transport phase proof is the primary basis",
            }

        _require(process is not None or state is not None,
                 "at least one independent corroboration is required")

        prior = []
        seen = set()
        for item in historical_evidence:
            source = Path(item).resolve()
            source_sha = file_sha256(source)
            name = "prior-" + source_sha[:12] + "-" + re.sub(r"[^A-Za-z0-9._-]", "_", source.name)
            target = staging / name
            copy_sha = _copy_evidence(source, target)
            _require(copy_sha == source_sha, "historical evidence copy changed")
            key = str(target.resolve())
            _require(key not in seen, "duplicate historical evidence")
            seen.add(key)
            prior.append({
                "path": str(final_rel / name),
                "sha256": copy_sha,
                "source_path": str(source),
                "source_sha256": source_sha,
                "role": "historical-corroboration",
            })
        prior.sort(key=lambda item: item["source_path"])

        return {"process": process, "server": server, "state": state, "prior": prior}, staging, final_dir
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _validate_linked(attempt_dir, item, kind, exact_keys):
    _require(isinstance(item, dict) and set(item) == exact_keys, "evidence shape mismatch")
    path = Path(item["path"])
    _require(not path.is_absolute() and ".." not in path.parts, "relative evidence path required")
    target = (attempt_dir / path).resolve()
    _require(target.is_relative_to(attempt_dir.resolve()), "evidence path escapes attempt")
    _require(target.is_file(), "linked evidence missing")
    _require(file_sha256(target) == item["sha256"], "linked evidence hash mismatch")
    _require(item["kind"] == kind, "linked evidence kind mismatch")
    return target


def validate_corroboration(attempt_dir, corroboration, wall_started, request_id, worksheet_id):
    attempt_dir = Path(attempt_dir)
    _require(isinstance(corroboration, dict)
             and set(corroboration) == {"process", "server", "state", "prior"},
             "corroboration shape mismatch")
    process, server, state, prior = (corroboration[key] for key in ("process", "server", "state", "prior"))
    attempt = dt.datetime.fromtimestamp(wall_started, dt.timezone.utc)

    if process is not None:
        keys = {"kind", "path", "sha256", "source_path", "source_sha256", "source_command",
                "pid", "utc_offset_seconds", "termination_utc", "age_before_wall_started_s",
                "captured_at", "limits"}
        target = _validate_linked(attempt_dir, process, "macos-unified-log-process-exit", keys)
        terminal = parse_process_log(target.read_text(encoding="utf8", errors="replace"),
                                     process["pid"], process["utc_offset_seconds"])
        parsed = dt.datetime.fromisoformat(process["termination_utc"].replace("Z", "+00:00"))
        _require(abs((parsed - terminal).total_seconds()) < 1e-6, "termination timestamp mismatch")
        age = (attempt - terminal).total_seconds()
        _require(0 < age <= 3600, "termination does not immediately precede attempt")
        _require(abs(age - process["age_before_wall_started_s"]) < 1e-6, "termination age mismatch")
        _require(_text(process["source_path"]) and _text(process["source_sha256"]), "process provenance missing")

    if server is not None:
        keys = {"kind", "path", "sha256", "source_path", "source_sha256", "utc_offset_seconds",
                "entries", "last_entry_utc", "entries_after_wall_started",
                "post_requests_at_or_after_wall_started", "captured_at", "limits"}
        target = _validate_linked(attempt_dir, server, "workbench-access-log", keys)
        facts = parse_server_log(target.read_text(encoding="utf8", errors="replace"),
                                 wall_started, server["utc_offset_seconds"])
        _require(facts["entries"] == server["entries"], "server entry count mismatch")
        _require(facts["last_entry_utc"] == server["last_entry_utc"], "server last entry mismatch")
        _require(server["entries_after_wall_started"] == 0 and facts["entries_after_wall_started"] == 0,
                 "server log continues after attempt")
        _require(server["post_requests_at_or_after_wall_started"] == 0
                 and facts["post_requests_at_or_after_wall_started"] == 0,
                 "server log contains a POST after attempt")

    if state is not None:
        keys = {"kind", "path", "sha256", "source_path", "source_sha256", "db_path", "db_sha256",
                "db_size", "db_mtime_ns", "window_seconds", "tables_scanned", "matching_rows",
                "query_records_window", "admissions_window", "execution_contexts",
                "captured_at", "limits"}
        target = _validate_linked(attempt_dir, state, "readonly-workbench-state-scan", keys)
        raw = json.loads(target.read_text())
        _require(raw.get("format") == 1 and raw.get("kind") == "readonly-workbench-state-scan",
                 "state scan kind mismatch")
        _require(raw.get("needles") == [request_id, worksheet_id], "state scan needles mismatch")
        _require(raw.get("matching_rows") == [], "state scan matched request ids")
        _require(raw.get("query_records_window") == 0, "state scan found a query record")
        _require(raw.get("admissions_window") == 0, "state scan found an admission")
        _require(raw.get("execution_contexts") == 0, "state scan found an execution context")
        _require(raw.get("tables_scanned") == len(raw.get("tables") or []), "state table count mismatch")
        for key in ("db_path", "db_sha256", "db_size", "db_mtime_ns", "window_seconds",
                    "tables_scanned", "matching_rows", "query_records_window",
                    "admissions_window", "execution_contexts", "captured_at"):
            _require(raw.get(key) == state[key], "state scan summary mismatch")
        _require(state["source_path"] == raw.get("db_path"), "state source path mismatch")
        _require(state["source_sha256"] == raw.get("db_sha256"), "state source hash mismatch")

    _require(isinstance(prior, list), "prior evidence list required")
    for item in prior:
        keys = {"path", "sha256", "source_path", "source_sha256", "role"}
        _require(isinstance(item, dict) and set(item) == keys, "prior evidence shape mismatch")
        _require(item["role"] == "historical-corroboration", "prior evidence role mismatch")
        _validate_linked(attempt_dir, dict(item, kind="historical-corroboration"),
                         "historical-corroboration", keys | {"kind"})
        _require(_text(item["source_path"]) and item["source_sha256"] == item["sha256"],
                 "prior evidence provenance mismatch")

    _require(process is not None or state is not None,
             "at least one independent corroboration is required")
    return True
