"""Sequential durable attempts. A lost POST is never retried."""
import base64
import copy
import hashlib
import socket
import ssl
import threading
import http.client
from decimal import Decimal
import ipaddress
import json
import math
from pathlib import Path
import re
import shutil
import time
import urllib.parse
import uuid

from .capacity import Campaign, allocated, write_json
from .engine import bounded, connect, ident, quote
from .protocol import settings
from .results import Reader, Writer, column, compare, validate_order


def sql_identity(sql):
    return hashlib.sha256(sql.encode("utf8")).hexdigest()


def engine_family(identity):
    return {"duckdb-isolated": "duckdb", "trino-loopback": "trino", "shirokuma-public": "trino"}.get((identity or {}).get("kind"))


def query_path(query_id, repeat):
    encoded = urllib.parse.quote(query_id, safe="")
    if len(encoded) > 220:
        encoded = encoded[:180] + "-" + sql_identity(query_id)[:24]
    return encoded + "-" + str(repeat)


def adapter_identity(adapter):
    if adapter.name == "shirokuma-public":
        return adapter.identity()
    if isinstance(adapter, TrinoAdapter):
        return dict(tls_verified=adapter.origin[0] == "https", kind=adapter.name, origin=list(adapter.origin),
                    user=adapter.headers["X-Trino-User"],
                    client_capabilities=adapter.headers.get("X-Trino-Client-Capabilities"),
                    catalog=adapter.headers.get("X-Trino-Catalog"), schema=adapter.headers.get("X-Trino-Schema"))
    if isinstance(adapter, DuckDBAdapter):
        return dict(kind=adapter.name, dataset=str(adapter.dataset.resolve()) if adapter.dataset is not None else None)
    raise ValueError("unsupported adapter identity")


def origin(url):
    p = urllib.parse.urlsplit(url)
    if p.username or p.password or p.fragment or p.scheme not in ("http", "https"):
        raise ValueError("invalid loopback URL")
    if p.hostname != "localhost":
        try:
            if not ipaddress.ip_address(p.hostname).is_loopback:
                raise ValueError("loopback required")
        except (ValueError, TypeError):
            raise ValueError("literal loopback or localhost required") from None
    return p.scheme, p.hostname, p.port or (443 if p.scheme == "https" else 80)


SERVER_ERROR_ID = r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}"


class HTTPFailure(RuntimeError):
    """Complete bounded HTTP failure; only allowlisted API evidence is retained."""
    def __init__(self, status, method, url, api_code=None, server_request_id=None):
        super().__init__("HTTP request failed")
        self.http_status, self.method = status, method
        parsed = urllib.parse.urlsplit(url)
        self.path, self.origin = parsed.path if not parsed.query else None, origin(url)
        self.api_code = api_code if api_code == "SQL_PERMISSION_DENIED" else None
        self.server_request_id = (server_request_id if isinstance(server_request_id, str)
            and re.fullmatch(SERVER_ERROR_ID, server_request_id) else None)


class TrinoAdapter:
    name = "trino-loopback"

    def __init__(self, url, user="local-perf", password=None, catalog=None, schema=None, *, ca_file=None, ssl_context=None, request_timeout=5):
        if (isinstance(request_timeout, bool) or not isinstance(request_timeout, (int, float))
                or not math.isfinite(request_timeout) or request_timeout <= 0):
            raise ValueError("positive finite request timeout required")
        self.request_timeout = request_timeout
        self.url = url.rstrip("/")
        self.origin = origin(self.url)
        if ca_file is not None and ssl_context is not None:
            raise ValueError("choose CA file or verified SSL context")
        self.ssl_context = ssl_context or ssl.create_default_context(cafile=ca_file)
        if not self.ssl_context.check_hostname or self.ssl_context.verify_mode != ssl.CERT_REQUIRED:
            raise ValueError("verified TLS required")
        if urllib.parse.urlsplit(self.url).path not in ("", "/") or urllib.parse.urlsplit(self.url).query:
            raise ValueError("Trino base must be origin only")
        self.headers = {"X-Trino-User": user, "Content-Type": "text/plain; charset=utf-8",
                        "X-Trino-Client-Capabilities": "PARAMETRIC_DATETIME"}
        if password is not None:
            token = base64.b64encode((user + ":" + password).encode()).decode()
            self.headers["Authorization"] = "Basic " + token
        for k, v in (("X-Trino-Catalog", catalog), ("X-Trino-Schema", schema)):
            if v is not None:
                self.headers[k] = v

    def safe(self, url):
        if origin(url) != self.origin:
            raise ValueError("foreign nextURI refused")
        return url

    def request(self, method, url, deadline, body=None):
        self.safe(url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("query deadline")
        parsed = urllib.parse.urlsplit(url)
        cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        kwargs = {"context": self.ssl_context} if parsed.scheme == "https" else {}
        conn = cls(parsed.hostname, parsed.port, timeout=min(self.request_timeout, remaining), **kwargs)
        timer, expired = None, threading.Event()
        try:
            conn.connect()
            sock = conn.sock
            def interrupt():
                expired.set()
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            timer = threading.Timer(max(0, deadline - time.monotonic()), interrupt)
            timer.daemon = True
            timer.start()
            sock.settimeout(min(self.request_timeout, max(.001, deadline - time.monotonic())))
            target = parsed.path or "/"
            if parsed.query:
                target += "?" + parsed.query
            conn.request(method, target, body, self.headers)
            sock.settimeout(min(self.request_timeout, max(.001, deadline - time.monotonic())))
            response = conn.getresponse()
            expected_length = response.length
            if expected_length is not None and expected_length > 16 * 1024 ** 2:
                raise ValueError("page exceeds bounded transport size")
            chunks, size = [], 0
            # read1 may close the response/socket as the final bytes arrive.
            while not response.isclosed() and response.length != 0:
                if time.monotonic() >= deadline:
                    raise TimeoutError("response deadline")
                sock.settimeout(min(self.request_timeout, max(.001, deadline - time.monotonic())))
                chunk = response.read1(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > 16 * 1024 ** 2:
                    raise ValueError("page exceeds bounded transport size")
                chunks.append(chunk)
            if expired.is_set() or time.monotonic() >= deadline:
                raise TimeoutError("response deadline")
            raw = b"".join(chunks)
            if (response.length not in (0, None)
                    or (expected_length is not None and size != expected_length)):
                raise ValueError("truncated HTTP body")
            if not 200 <= response.status < 300:
                try:
                    def unique_fields(items):
                        if len(items) != len(dict(items)):
                            raise ValueError("ambiguous HTTP error JSON")
                        return dict(items)
                    error = json.loads(raw, object_pairs_hook=unique_fields) if raw else {}
                except (ValueError, UnicodeError):
                    error = {}
                error = error if isinstance(error, dict) else {}
                raise HTTPFailure(response.status, method, url, error.get("code"), error.get("request_id"))
            return json.loads(raw, parse_float=Decimal) if raw else {}
        except Exception as exc:
            if expired.is_set():
                raise TimeoutError("response deadline") from exc
            raise
        finally:
            if timer is not None:
                timer.cancel()
                timer.join()
            conn.close()

    def cancel_reconcile(self, handle, timeout=5):
        if not handle or not re.fullmatch(r"[A-Za-z0-9_-]+", handle.get("id", "")):
            return "UNKNOWN"
        deadline = time.monotonic() + timeout
        url = self.url + "/v1/query/" + handle["id"]
        try:
            self.request("DELETE", url, deadline)
            while time.monotonic() < deadline:
                state = self.request("GET", url, deadline).get("state")
                if state in ("FINISHED", "FAILED", "CANCELED", "CANCELLED"):
                    return state
                time.sleep(min(.05, max(0, deadline - time.monotonic())))
        except Exception:
            pass
        return "UNKNOWN"

    def execute(self, sql, out, metadata, deadline, save, check):
        page = self.request("POST", self.url + "/v1/statement", deadline, sql.encode())
        writer, columns, seen, page_ids, handle = None, None, set(), set(), None
        native = None
        try:
            while True:
                qid = page.get("id")
                if not isinstance(qid, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", qid):
                    raise ValueError("missing query handle")
                if handle is not None and qid != handle["id"]:
                    raise ValueError("query identity changed")
                handle = {"id": qid}
                save(handle)  # durable immediately, before any data/schema handling
                check()
                if page.get("result_page_id") is not None:
                    pid = page["result_page_id"]
                    if pid in page_ids:
                        raise ValueError("duplicate page identity")
                    page_ids.add(pid)
                state = page.get("stats", {}).get("state")
                if (page.get("error") or page.get("truncated")
                        or state not in ("QUEUED", "WAITING_FOR_RESOURCES", "DISPATCHING", "PLANNING", "STARTING", "RUNNING", "FINISHING", "FINISHED")):
                    raise ValueError("backend error/truncation/invalid state")
                if "columns" in page:
                    raw = [{"name": c["name"], "type": c["type"]} for c in page["columns"]]
                    if native is not None and native != raw:
                        raise ValueError("native page schema changed")
                    native = raw
                    save(dict(handle, native_columns=native))
                    incoming = [column(c["name"], c["type"]) for c in raw]
                    if columns is not None and incoming != columns:
                        raise ValueError("page schema changed")
                    columns = incoming
                    if writer is None:
                        writer = Writer(out, columns, metadata)
                data = page.get("data", [])
                if not isinstance(data, list) or (data and writer is None):
                    raise ValueError("data before schema")
                for n, row in enumerate(data):
                    if n % 256 == 0:
                        check()
                    writer.row(row)
                next_uri = page.get("nextUri")
                if next_uri is None:
                    if page.get("stats", {}).get("state") != "FINISHED" or writer is None:
                        raise ValueError("incomplete terminal response")
                    writer.finish()
                    return
                self.safe(next_uri)
                if next_uri in seen:
                    raise ValueError("duplicate result page")
                seen.add(next_uri)
                check()
                page = self.request("GET", next_uri, deadline)
        finally:
            if writer is not None and not writer.f.closed:
                writer.close()


class DuckDBAdapter:
    name = "duckdb-isolated"

    def __init__(self, dataset=None):
        self.dataset = Path(dataset) if dataset is not None else None
        self.con = None

    def execute(self, sql, out, metadata, deadline, save, check):
        meta = (json.loads((self.dataset / "dataset.json").read_text()) if self.dataset is not None
                else dict(status="SUCCESS", peak_bytes=256 * 1024 ** 2, tables=[]))
        if meta["status"] != "SUCCESS":
            raise ValueError("incomplete dataset")
        work = out.parent / "duckdb-work"
        work.mkdir()
        writer = None
        try:
            self.con = connect(work / "query.duckdb", work / "spill", temp_bytes=meta["peak_bytes"] // 3)
            save({"id": "local-" + uuid.uuid4().hex})
            with bounded(self.con, check, deadline):
                for t in meta["tables"]:
                    self.con.execute("CREATE VIEW " + ident(t["name"]) + " AS SELECT * FROM read_parquet(" + quote(self.dataset / t["file"]) + ")")
                cursor = self.con.execute(sql)
                columns = [column(d[0], str(d[1])) for d in cursor.description]
                writer = Writer(out, columns, metadata)
                while True:
                    rows = cursor.fetchmany(256)
                    check()
                    if not rows:
                        break
                    for row in rows:
                        writer.row(row)
                writer.finish()
        finally:
            if writer is not None and not writer.f.closed:
                writer.close()
            if self.con is not None:
                self.con.close()
                self.con = None
            shutil.rmtree(work)

    def cancel_reconcile(self, handle, timeout=5):
        # Synchronous execute unwinds and closes the local connection before here.
        return "CANCELED" if self.con is None else "UNKNOWN"


def select_only(sql):
    # Conservative lexical qualification filter, not an authorization boundary.
    text = re.sub(r"--[^\n]*|/\*.*?\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"", " ", sql, flags=re.S)
    text = text.strip().rstrip(";").strip()
    if not re.match(r"(?i)^(select|with)\b", text) or ";" in text or re.search(
            r"(?i)\b(insert|update|delete|drop|create|alter|copy|call|install|load|attach|detach|pragma|export|import|merge|set)\b", text):
        raise ValueError("qualification defaults to SELECT; explicitly select arbitrary SQL mode")


def run(campaign, name, manifest, adapter, *, references=None, mode="qualification", scale=None,
        peak=None, timeout=None, repetitions=None, allow_arbitrary=False, abs_tol=0, rel_tol=0,
        reference_label=None, peak_evidence=None, sql_variant="sql"):
    cfg = settings(mode, scale, peak, timeout, repetitions, peak_evidence)
    if (manifest.get("format") != 1 or not manifest.get("queries")
            or not isinstance(manifest.get("dataset_id"), str) or not manifest["dataset_id"]):
        raise ValueError("invalid query manifest")
    manifest = copy.deepcopy(manifest)
    queries = manifest["queries"]
    if sql_variant not in ("sql", "reference_sql") or (sql_variant != "sql" and not isinstance(adapter, DuckDBAdapter)):
        raise ValueError("reference_sql is DuckDB only")
    for q in queries:
        validate_sql_contract(manifest, q)
        if sql_variant != "sql" and not bound_manifest(manifest):
            raise ValueError("reference SQL requires snapshot-bound manifest")
    ids = [q["id"] for q in queries]
    if len(set(ids)) != len(ids) or any(not isinstance(i, str) or not i or "\x00" in i or (not bound_manifest(manifest) and not re.fullmatch(r"[A-Za-z0-9_-]+", i)) for i in ids):
        raise ValueError("unique safe query ids required")
    identity = adapter_identity(adapter)
    if bound_manifest(manifest) and isinstance(adapter, DuckDBAdapter):
        if adapter.dataset is None:
            raise ValueError("bound reference needs the source Parquet dataset")
        source = json.loads((adapter.dataset / "dataset.json").read_text())
        if source.get("status") != "SUCCESS" or source.get("dataset_id") != manifest["source_dataset_id"]:
            raise ValueError("bound reference source dataset mismatch")
    reference_evidence = None
    if references is not None and (Path(references) / "run.json").is_file():
        raw = (Path(references) / "run.json").read_bytes()
        ref_identity = json.loads(raw).get("adapter_identity")
        reference_evidence = dict(run_sha256=hashlib.sha256(raw).hexdigest(), adapter_identity=ref_identity,
                                  engine_family=engine_family(ref_identity))
    current_engine = engine_family(identity)
    reference_engine = reference_evidence["engine_family"] if reference_evidence else None
    # Verify all reference identities before dispatch, including fail-fast tails.
    if references is not None:
        for q in queries:
            reader = Reader(Path(references) / query_path(q["id"], 0) / "result.jsonl")
            try:
                if reader.header["metadata"] != result_metadata(manifest, q):
                    raise ValueError("reference SQL/dataset identity mismatch")
            finally:
                reader.f.close()
    if not allow_arbitrary:
        for q in queries:
            select_only(q["sql"])
    if bound_manifest(manifest):
        for q in queries:
            select_only(q["sql"])
            select_only(q[sql_variant])
    if references is None and reference_label is None:
        raise ValueError("reference directory required; use prepare-reference separately")
    campaign.refresh()
    for n, entry in campaign.state["owned"].items():
        if entry["kind"] == "run":
            if not (campaign.root / n / "run.json").exists():
                raise RuntimeError("incomplete prior run blocks dispatch; inspect campaign")
            for p in (campaign.root / n).glob("*/attempt.json"):
                audit = json.loads(p.read_text())
                validate_frozen(json.loads((campaign.root / n / "queries.json").read_text()),
                                json.loads((campaign.root / n / "run.json").read_text()), audit)
                if "submission_rejection" in audit:
                    from .public import valid_submission_rejection
                    if not valid_submission_rejection(audit["submission_rejection"], audit):
                        raise ValueError("invalid submission rejection")
                reconciliation = p.parent / "reconciliation.json"
                if reconciliation.exists():
                    resolved = terminal_reconciliation(reconciliation, audit)
                    if resolved is not None:
                        audit = resolved
                if audit["status"] in ("INTENT", "RUNNING", "UNKNOWN"):
                    raise RuntimeError("unreconciled attempt blocks new dispatch")
    campaign.check(cfg["peak_bytes"])
    root = campaign.own(name, "run")
    report = dict(format=1, status="INCOMPLETE", adapter=adapter.name, adapter_identity=identity, dataset_id=manifest["dataset_id"], settings=cfg,
                  sql_variant=sql_variant, independent_cross_engine=bool(current_engine and reference_engine and current_engine != reference_engine),
                  engine_family=current_engine, reference_evidence=reference_evidence,
                  reference_label=reference_label, provenance=reference_label, attempts=[],
                  query_manifest_sha256=hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(), public_api_qualified=False,
                  planned_attempts=len(queries) * cfg["repetitions"],
                  pending_attempts=len(queries) * cfg["repetitions"], not_run_attempts=0,
                  max_wall_s=len(queries) * cfg["repetitions"] * (cfg["timeout_s"] + 5),
                  concurrency=1, cache="data cache unknown", floating_tolerance={"abs": abs_tol, "rel": rel_tol})
    write_json(root / "queries.json", manifest)  # frozen before any dispatch
    write_json(root / "run.json", report)
    initial = allocated(root)
    try:
        for repeat in range(cfg["repetitions"]):
            for q in queries:
                attempt = root / query_path(q["id"], repeat)
                attempt.mkdir()
                started = time.monotonic()
                deadline = started + cfg["timeout_s"]

                def check():
                    if time.monotonic() >= deadline:
                        raise TimeoutError("fetch/validation deadline")
                    growth = max(0, allocated(root) - initial)
                    if growth > cfg["peak_bytes"]:
                        raise RuntimeError("declared run peak exceeded")
                    campaign.check(max(0, cfg["peak_bytes"] - growth))

                check()
                intent = dict(status="INTENT", request_id=str(uuid.uuid4()), query_id=q["id"], sql=q[sql_variant],
                              adapter=adapter.name, adapter_identity=identity, sql_sha256=sql_identity(q[sql_variant]), handle=None, elapsed_s=None, dataset_id=manifest.get("dataset_id"),
                              timeout_s=cfg["timeout_s"], wall_started=time.time(),
                              engine_origin=list(adapter.origin) if isinstance(adapter, TrinoAdapter) else None)
                intent["sql_variant"] = sql_variant
                if bound_manifest(manifest):
                    intent.update(target_sql=q["sql"], target_sql_sha256=sql_identity(q["sql"]),
                                  logical_sql_sha256=sql_identity(q["logical_sql"]),
                                  reference_sql_sha256=sql_identity(q["reference_sql"]))
                if adapter.name == "shirokuma-public":
                    adapter.prepare_attempt(intent)
                path = attempt / "attempt.json"
                report["attempts"].append(dict(path=str(attempt.relative_to(root)), status="INTENT",
                                               request_id=intent["request_id"]))
                report["pending_attempts"] = report["planned_attempts"] - len(report["attempts"])
                # Register ownership before the first recoverable receipt can exist.
                write_json(root / "run.json", report)
                write_json(path, intent)  # fsync + atomic replace BEFORE POST/execute

                def save(handle):
                    handle = dict(handle)
                    if "native_columns" in handle:
                        intent["native_columns"] = handle.pop("native_columns")
                    intent.update(status="RUNNING", handle=handle)
                    write_json(path, intent)

                try:
                    metadata = result_metadata(manifest, q)
                    adapter.execute(q[sql_variant], attempt / "result.jsonl", metadata, deadline, save, check)
                    if "columns" in q:
                        reader = Reader(attempt / "result.jsonl")
                        try:
                            if reader.header["columns"] != [column(c["name"], c["type"]) for c in q["columns"]]:
                                raise ValueError("manifest result schema mismatch")
                        finally:
                            reader.f.close()
                    validate_order(attempt / "result.jsonl", q.get("order_by", []), check)
                    fetched = time.monotonic()
                    if references is not None:
                        reference = Path(references) / query_path(q["id"], 0) / "result.jsonl"
                        compare(reference, attempt / "result.jsonl", ordered=q.get("ordered", False),
                                abs_tol=abs_tol, rel_tol=rel_tol, scratch=attempt, check=check)
                    check()
                    intent.update(status="SUCCESS", fetch_s=fetched - started,
                                  validation_s=time.monotonic() - fetched,
                                  validation="full comparison" if references is not None else "reference preparation only")
                except BaseException as exc:
                    rejection = adapter.submission_rejection(exc, intent) if adapter.name == "shirokuma-public" else None
                    if rejection is not None:
                        intent["submission_rejection"] = rejection
                    terminal = ("REJECTED" if rejection is not None else adapter.recover(intent, save) if adapter.name == "shirokuma-public"
                                and adapter_identity(adapter) == intent["adapter_identity"] else
                                adapter.cancel_reconcile(intent["handle"])
                                if adapter_identity(adapter) == intent["adapter_identity"] else "UNKNOWN")
                    intent.update(status="UNKNOWN" if terminal == "UNKNOWN" else "FAILED",
                                  error_type=type(exc).__name__, reconciliation=terminal)
                    if adapter.name == "shirokuma-public":
                        intent["error_code"] = "SQL_PERMISSION_DENIED" if rejection is not None else getattr(exc, "code", "PUBLIC_QUERY_UNMET")
                    raise
                finally:
                    intent["elapsed_s"] = time.monotonic() - started
                    write_json(path, intent)
                    report["attempts"][-1]["status"] = intent["status"]
                    report["pending_attempts"] = report["planned_attempts"] - len(report["attempts"])
                    write_json(root / "run.json", report)
        report["status"] = "SUCCESS"
        report["public_api_qualified"] = adapter.name == "shirokuma-public" and references is not None
        campaign.mark(root, "SUCCESS")
        return root
    except BaseException:
        report["status"] = "FAILED"
        campaign.mark(root, "FAILED")
        raise
    finally:
        report["not_run_attempts"] = report["pending_attempts"]
        report["pending_attempts"] = 0
        write_json(root / "run.json", report)


def bound_manifest(manifest):
    return any(k in manifest for k in ("snapshot_set_sha256", "snapshot_pins"))


def validate_sql_contract(manifest, q):
    fields = (("sql", "sql_sha256"),)
    if bound_manifest(manifest):
        if not manifest.get("source_dataset_id") or not manifest.get("snapshot_set_sha256") or not manifest.get("snapshot_pins"):
            raise ValueError("incomplete snapshot binding")
        pins = copy.deepcopy(manifest["snapshot_pins"])
        pins["tables"] = sorted(pins["tables"], key=lambda t: t["name"])
        digest = sql_identity(json.dumps(pins, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False))
        if digest != manifest["snapshot_set_sha256"]:
            raise ValueError("snapshot fingerprint mismatch")
        fields += (("logical_sql", "logical_sha256"), ("reference_sql", "reference_sha256"))
    elif any(k in q for k in ("logical_sql", "reference_sql")):
        raise ValueError("SQL variants require snapshot binding")
    for field, hash_field in fields:
        text = q.get(field)
        if not isinstance(text, str) or not text.strip():
            raise ValueError("SQL required")
        digest = sql_identity(text)
        if (bound_manifest(manifest) and hash_field not in q) or q.get(hash_field, digest) != digest:
            raise ValueError("SQL fingerprint mismatch")
        for alias in ({"sql": ("target_sha256", "target_sql_sha256"),
                       "logical_sql": ("logical_sql_sha256",), "reference_sql": ("reference_sql_sha256",)}[field]):
            if alias in q and q[alias] != digest:
                raise ValueError("SQL fingerprint mismatch")
        q[hash_field] = digest
    if "source_sql" in q and q.get("source_sha256") != sql_identity(q["source_sql"]):
        raise ValueError("source SQL fingerprint mismatch")


def result_metadata(manifest, q):
    if bound_manifest(manifest):
        return dict(dataset_id=manifest["dataset_id"], source_dataset_id=manifest["source_dataset_id"],
                    snapshot_set_sha256=manifest["snapshot_set_sha256"], query_id=q["id"],
                    logical_sql_sha256=sql_identity(q["logical_sql"]),
                    target_sql_sha256=sql_identity(q["sql"]), reference_sql_sha256=sql_identity(q["reference_sql"]),
                    ordered=q.get("ordered", False), order_by=q.get("order_by", []), pair_contract=q.get("pair_contract"))
    return dict(dataset_id=manifest["dataset_id"], query_id=q["id"], sql_sha256=sql_identity(q["sql"]))


def validate_frozen(snapshot, report, intent):
    if sql_identity(json.dumps(snapshot, sort_keys=True)) != report.get("query_manifest_sha256"):
        raise ValueError("frozen query manifest fingerprint mismatch")
    variant = intent.get("sql_variant", "sql")
    if variant != report.get("sql_variant", "sql") or variant not in ("sql", "reference_sql"):
        raise ValueError("SQL variant mismatch")
    for q in snapshot["queries"]:
        validate_sql_contract(snapshot, q)
    matches = [q for q in snapshot["queries"] if q["id"] == intent.get("query_id")]
    if len(matches) != 1 or intent.get("dataset_id") != snapshot["dataset_id"]:
        raise ValueError("attempt snapshot mismatch")
    q = matches[0]
    if q.get(variant) != intent.get("sql") or sql_identity(q[variant]) != intent.get("sql_sha256"):
        raise ValueError("attempt SQL mismatch")
    if bound_manifest(snapshot) and any(intent.get(k) != v for k, v in dict(
            target_sql=q["sql"], target_sql_sha256=sql_identity(q["sql"]),
            logical_sql_sha256=sql_identity(q["logical_sql"]), reference_sql_sha256=sql_identity(q["reference_sql"])).items()):
        raise ValueError("attempt variant identity mismatch")


def terminal_reconciliation(path, intent):
    if not path.exists() or intent.get("adapter_identity", {}).get("kind") not in (TrinoAdapter.name, "shirokuma-public"):
        return None
    result = json.loads(path.read_text())
    keys = ("request_id", "handle", "adapter_identity", "dispatch", "query_id", "sql_sha256", "dataset_id", "sql", "mode", "sql_variant", "target_sql", "target_sql_sha256", "logical_sql_sha256", "reference_sql_sha256")
    handle = result.get("recovered_handle") or intent.get("handle")
    if (not handle and result.get("status") == "FAILED" and result.get("reconciliation") == "REJECTED"
            and all(result.get(key) == intent.get(key) for key in keys)):
        from .public import valid_submission_rejection
        if (result.get("submission_rejection") == intent.get("submission_rejection")
                and valid_submission_rejection(result.get("submission_rejection"), intent)):
            return result
        try:
            if (set(result) != set(intent) | {"reconciliation", "no_dispatch_evidence"}
                    or any(result.get(k) != v for k, v in intent.items() if k not in ("status", "reconciliation"))):
                return None
            validate_rejection_audit(path.parent / "attempt.json", intent, result["no_dispatch_evidence"])
            return result
        except (ValueError, KeyError, TypeError, OSError):
            return None
    if (handle and result.get("status") == "FAILED"
            and result.get("reconciliation") in ("FINISHED", "FAILED", "CANCELED", "CANCELLED")
            and all(result.get(key) == intent.get(key) for key in keys)):
        if intent["adapter_identity"]["kind"] == "shirokuma-public":
            from .public import context, valid_handle
            identity, binding = intent["adapter_identity"], intent.get("dispatch", {})
            try:
                if (not valid_handle(handle) or context(binding.get("context")) != identity.get("context")
                        or binding.get("request_id") != intent.get("request_id")
                        or not isinstance(binding.get("worksheet_id"), str) or not binding["worksheet_id"]
                        or binding["context"]["selected_role"] != identity.get("selected_role")
                        or not isinstance(intent.get("sql"), str) or intent.get("mode") != "read-only"
                        or intent.get("handle") and any(intent["handle"].get(k) is not None
                            and intent["handle"][k] != handle.get(k) for k in ("id", "backend_query_id"))):
                    return None
            except (ValueError, KeyError, TypeError):
                return None
        return result
    return None


def validate_rejection_audit(path, intent, evidence):
    from .public import rejection_scope
    report_path, manifest_path = path.parent.parent / "run.json", path.parent.parent / "queries.json"
    report = json.loads(report_path.read_text())
    validate_frozen(json.loads(manifest_path.read_text()), report, intent)
    fixed = dict(format=1, kind="local-operator-audit", observed_http_status=403,
                 captured_api_code=None, derived_code="SQL_PERMISSION_DENIED", completed_response=True,
                 unique_http_event=True, readonly_ledger_rows=0, pre_dispatch_guard_rejected=True, backend_nonterminal=0)
    required = set(fixed) | {"reviewer", "request_id", "dispatch", "context", "adapter_identity", "sql_sha256",
                             "original_attempt_sha256", "manifest_sha256", "query_manifest_sha256",
                             "policy_source_commit", "policy_source_sha256", "evidence_files"}
    retained = path.parent / "reconciliation.json"
    if retained.exists() and json.loads(retained.read_text()).get("recovered_handle") is not None:
        raise ValueError("recovered query cannot be rejected as unadmitted")
    if (set(evidence) != required or not isinstance(evidence.get("reviewer"), str)
            or not evidence["reviewer"].strip() or len(evidence["reviewer"]) > 128
            or any(c in evidence["reviewer"] for c in ("\r", "\n", "\x00")) or intent.get("status") not in ("UNKNOWN", "INTENT") or intent.get("handle") is not None
            or not rejection_scope(evidence, intent) or any(type(evidence.get(k)) is not type(v) or evidence[k] != v for k, v in fixed.items())
            or evidence.get("context") != intent["dispatch"]["context"]
            or evidence.get("original_attempt_sha256") != hashlib.sha256(path.read_bytes()).hexdigest()
            or evidence.get("manifest_sha256") != hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            or evidence.get("query_manifest_sha256") != report.get("query_manifest_sha256")
            or not isinstance(evidence.get("policy_source_commit"), str) or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", evidence["policy_source_commit"])
            or not isinstance(evidence.get("policy_source_sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", evidence["policy_source_sha256"])
            or not isinstance(evidence.get("evidence_files"), list) or not evidence["evidence_files"]):
        raise ValueError("invalid scoped operator rejection audit")
    seen = set()
    for item in evidence["evidence_files"]:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"} or not isinstance(item["path"], str):
            raise ValueError("invalid linked audit file")
        file = Path(item["path"])
        if (set(item) != {"path", "sha256"} or not file.is_absolute() or str(file.resolve()) in seen
                or hashlib.sha256(file.read_bytes()).hexdigest() != item["sha256"]):
            raise ValueError("invalid linked audit file")
        json.loads(file.read_bytes())  # Actual audit JSON, explicitly linked by the operator.
        seen.add(str(file.resolve()))
    return evidence


def reconcile(path, adapter, *, rejection_evidence=None):
    """Owned uncertain attempt only; append reconciliation without rewriting audit."""
    path = Path(path).resolve()
    if path.name != "attempt.json":
        raise ValueError("owned attempt path required")
    campaign = Campaign(path.parent.parent.parent)
    run_root = path.parent.parent
    owned = campaign.state["owned"].get(run_root.name, {})
    if owned.get("kind") != "run" or run_root.parent != campaign.root:
        raise ValueError("unowned attempt")
    report = json.loads((run_root / "run.json").read_text())
    intent = json.loads(path.read_text())
    if not any(a["path"] == path.parent.name and a.get("request_id") == intent.get("request_id")
               for a in report["attempts"]):
        raise ValueError("unowned attempt")
    snapshot = json.loads((run_root / "queries.json").read_text())
    validate_frozen(snapshot, report, intent)
    identity = adapter_identity(adapter)
    if intent.get("adapter_identity") != identity or report.get("adapter_identity") != identity:
        raise ValueError("reconcile must use original adapter/user/catalog/schema/origin")
    if rejection_evidence is not None:
        from .public import PublicAdapter
        if not isinstance(adapter, PublicAdapter):
            raise ValueError("public operator rejection audit only")
        adapter.bind(intent)
        audit = validate_rejection_audit(path, intent, json.loads(Path(rejection_evidence).read_bytes()))
        sidecar = dict(intent, status="FAILED", reconciliation="REJECTED", no_dispatch_evidence=audit)
        write_json(path.parent / "reconciliation.json", sidecar)
        return sidecar
    if intent["status"] not in ("UNKNOWN", "RUNNING", "INTENT"):
        if "submission_rejection" in intent:
            from .public import valid_submission_rejection
            if not valid_submission_rejection(intent["submission_rejection"], intent):
                raise ValueError("invalid submission rejection")
        return intent
    if isinstance(adapter, DuckDBAdapter):
        raise ValueError("external DuckDB reconciliation cannot prove original connection closed")
    if adapter.name == "shirokuma-public":
        adapter.bind(intent)
    retained = terminal_reconciliation(path.parent / "reconciliation.json", intent)
    if retained is not None:
        return retained
    result = dict(intent, status="UNKNOWN", reconciliation="UNKNOWN")
    evidence = path.parent / "reconciliation.json"
    if adapter.name == "shirokuma-public":
        if evidence.exists():
            previous = json.loads(evidence.read_text())
            if all(previous.get(k) == intent.get(k) for k in ("request_id", "handle", "dispatch", "adapter_identity", "sql", "mode")):
                result["recovered_handle"] = previous.get("recovered_handle")
        def save_recovered(handle):
            result["recovered_handle"] = handle
            write_json(evidence, result)  # recoverable before cancel, without changing original intent
        terminal = adapter.recover(dict(intent, handle=result.get("recovered_handle") or intent.get("handle")), save_recovered)
    else:
        terminal = adapter.cancel_reconcile(intent.get("handle")) if intent.get("handle") else "UNKNOWN"
    result.update(status="UNKNOWN" if terminal == "UNKNOWN" else "FAILED", reconciliation=terminal)
    write_json(evidence, result)
    return result
