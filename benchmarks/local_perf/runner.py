"""Sequential durable attempts. A lost POST is never retried."""
import base64
import copy
import hashlib
import socket
import threading
import http.client
from decimal import Decimal
import ipaddress
import json
from pathlib import Path
import re
import shutil
import time
import urllib.parse
import uuid

from .capacity import Campaign, allocated, write_json
from .engine import bounded, connect, ident, quote
from .protocol import settings
from .results import Reader, Writer, column, compare


def sql_identity(sql):
    return hashlib.sha256(sql.encode("utf8")).hexdigest()


def adapter_identity(adapter):
    if isinstance(adapter, TrinoAdapter):
        return dict(kind=adapter.name, origin=list(adapter.origin),
                    user=adapter.headers["X-Trino-User"],
                    catalog=adapter.headers.get("X-Trino-Catalog"), schema=adapter.headers.get("X-Trino-Schema"))
    if isinstance(adapter, DuckDBAdapter):
        return dict(kind=adapter.name, dataset=str(adapter.dataset.resolve()))
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


class TrinoAdapter:
    name = "trino-loopback"

    def __init__(self, url, user="local-perf", password=None, catalog=None, schema=None):
        self.url = url.rstrip("/")
        self.origin = origin(self.url)
        if urllib.parse.urlsplit(self.url).path not in ("", "/") or urllib.parse.urlsplit(self.url).query:
            raise ValueError("Trino base must be origin only")
        self.headers = {"X-Trino-User": user, "Content-Type": "text/plain; charset=utf-8"}
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
        conn = cls(parsed.hostname, parsed.port, timeout=min(5, remaining))
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
            sock.settimeout(min(5, max(.001, deadline - time.monotonic())))
            target = parsed.path or "/"
            if parsed.query:
                target += "?" + parsed.query
            conn.request(method, target, body, self.headers)
            sock.settimeout(min(5, max(.001, deadline - time.monotonic())))
            response = conn.getresponse()
            if not 200 <= response.status < 300:
                raise RuntimeError("non-2xx backend")
            expected_length = response.length
            chunks, size = [], 0
            # read1 may close the response/socket as the final bytes arrive.
            while not response.isclosed() and response.length != 0:
                if time.monotonic() >= deadline:
                    raise TimeoutError("response deadline")
                sock.settimeout(min(5, max(.001, deadline - time.monotonic())))
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
                    incoming = [column(c["name"], c["type"]) for c in page["columns"]]
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

    def __init__(self, dataset):
        self.dataset = Path(dataset)
        self.con = None

    def execute(self, sql, out, metadata, deadline, save, check):
        meta = json.loads((self.dataset / "dataset.json").read_text())
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
        reference_label=None, peak_evidence=None):
    cfg = settings(mode, scale, peak, timeout, repetitions, peak_evidence)
    if (manifest.get("format") != 1 or not manifest.get("queries")
            or not isinstance(manifest.get("dataset_id"), str) or not manifest["dataset_id"]):
        raise ValueError("invalid query manifest")
    manifest = copy.deepcopy(manifest)
    queries = manifest["queries"]
    for q in queries:
        fingerprint = sql_identity(q["sql"])
        if q.get("sql_sha256", fingerprint) != fingerprint:
            raise ValueError("SQL fingerprint mismatch")
        q["sql_sha256"] = fingerprint
    ids = [q["id"] for q in queries]
    if len(set(ids)) != len(ids) or any(not re.fullmatch(r"[A-Za-z0-9_-]+", i) for i in ids):
        raise ValueError("unique safe query ids required")
    identity = adapter_identity(adapter)
    # Verify all reference identities before dispatch, including fail-fast tails.
    if references is not None:
        for q in queries:
            reader = Reader(Path(references) / (q["id"] + "-0") / "result.jsonl")
            try:
                if reader.header["metadata"] != result_metadata(manifest, q):
                    raise ValueError("reference SQL/dataset identity mismatch")
            finally:
                reader.f.close()
    if not allow_arbitrary:
        for q in queries:
            select_only(q["sql"])
    if references is None and reference_label is None:
        raise ValueError("reference directory required; use prepare-reference separately")
    campaign.refresh()
    for n, entry in campaign.state["owned"].items():
        if entry["kind"] == "run":
            if not (campaign.root / n / "run.json").exists():
                raise RuntimeError("incomplete prior run blocks dispatch; inspect campaign")
            for p in (campaign.root / n).glob("*/attempt.json"):
                audit = json.loads(p.read_text())
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
                  reference_label=reference_label, attempts=[],
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
                attempt = root / (q["id"] + "-" + str(repeat))
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
                intent = dict(status="INTENT", request_id=str(uuid.uuid4()), query_id=q["id"], sql=q["sql"],
                              adapter=adapter.name, adapter_identity=identity, sql_sha256=q["sql_sha256"], handle=None, elapsed_s=None, dataset_id=manifest.get("dataset_id"),
                              timeout_s=cfg["timeout_s"], wall_started=time.time(),
                              engine_origin=list(adapter.origin) if isinstance(adapter, TrinoAdapter) else None)
                path = attempt / "attempt.json"
                report["attempts"].append(dict(path=str(attempt.relative_to(root)), status="INTENT",
                                               request_id=intent["request_id"]))
                report["pending_attempts"] = report["planned_attempts"] - len(report["attempts"])
                # Register ownership before the first recoverable receipt can exist.
                write_json(root / "run.json", report)
                write_json(path, intent)  # fsync + atomic replace BEFORE POST/execute

                def save(handle):
                    intent.update(status="RUNNING", handle=handle)
                    write_json(path, intent)

                try:
                    metadata = result_metadata(manifest, q)
                    adapter.execute(q["sql"], attempt / "result.jsonl", metadata, deadline, save, check)
                    fetched = time.monotonic()
                    if references is not None:
                        reference = Path(references) / (q["id"] + "-0") / "result.jsonl"
                        compare(reference, attempt / "result.jsonl", ordered=q.get("ordered", False),
                                abs_tol=abs_tol, rel_tol=rel_tol, scratch=attempt, check=check)
                    check()
                    intent.update(status="SUCCESS", fetch_s=fetched - started,
                                  validation_s=time.monotonic() - fetched,
                                  validation="full comparison" if references is not None else "reference preparation only")
                except BaseException as exc:
                    terminal = (adapter.cancel_reconcile(intent["handle"])
                                if adapter_identity(adapter) == intent["adapter_identity"] else "UNKNOWN")
                    intent.update(status="UNKNOWN" if terminal == "UNKNOWN" else "FAILED",
                                  error_type=type(exc).__name__, reconciliation=terminal)
                    raise
                finally:
                    intent["elapsed_s"] = time.monotonic() - started
                    write_json(path, intent)
                    report["attempts"][-1]["status"] = intent["status"]
                    report["pending_attempts"] = report["planned_attempts"] - len(report["attempts"])
                    write_json(root / "run.json", report)
        report["status"] = "SUCCESS"
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


def result_metadata(manifest, q):
    return dict(dataset_id=manifest["dataset_id"], query_id=q["id"],
                sql_sha256=sql_identity(q["sql"]))


def terminal_reconciliation(path, intent):
    if not path.exists() or intent.get("adapter_identity", {}).get("kind") != TrinoAdapter.name:
        return None
    result = json.loads(path.read_text())
    if (intent.get("handle") and result.get("status") == "FAILED"
            and result.get("reconciliation") in ("FINISHED", "FAILED", "CANCELED", "CANCELLED")
            and all(result.get(key) == intent.get(key) for key in ("request_id", "handle", "adapter_identity"))):
        return result
    return None


def reconcile(path, adapter):
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
    if (intent.get("dataset_id") != snapshot["dataset_id"] or not any(
            q["id"] == intent.get("query_id") and sql_identity(q["sql"]) == intent.get("sql_sha256")
            and q["sql"] == intent.get("sql") for q in snapshot["queries"])):
        raise ValueError("attempt does not match frozen query snapshot")
    identity = adapter_identity(adapter)
    if intent.get("adapter_identity") != identity or report.get("adapter_identity") != identity:
        raise ValueError("reconcile must use original adapter/user/catalog/schema/origin")
    if intent["status"] not in ("UNKNOWN", "RUNNING", "INTENT"):
        return intent
    if isinstance(adapter, DuckDBAdapter):
        raise ValueError("external DuckDB reconciliation cannot prove original connection closed")
    retained = terminal_reconciliation(path.parent / "reconciliation.json", intent)
    if retained is not None:
        return retained
    terminal = adapter.cancel_reconcile(intent.get("handle")) if intent.get("handle") else "UNKNOWN"
    result = dict(intent, status="UNKNOWN" if terminal == "UNKNOWN" else "FAILED", reconciliation=terminal)
    write_json(path.parent / "reconciliation.json", result)
    return result
