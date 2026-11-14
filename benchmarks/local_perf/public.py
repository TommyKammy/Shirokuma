"""Public Workbench SQL protocol only; credentials are explicit client material."""
import base64
import copy
import json
from pathlib import Path
import re
import stat
import time
import urllib.parse
import uuid

from .runner import HTTPFailure, SERVER_ERROR_ID, TrinoAdapter, adapter_timings, sql_identity
from .results import Writer, column

CONTEXT = ("database_id", "schema_id", "warehouse_id", "sql_catalog", "sql_schema",
           "selected_role", "binding_revision", "generation_id", "grant_revision")
WIRE_CONTEXT = ("database_id", "schema_id", "warehouse_id", "binding_revision", "generation_id")
TOKEN = r"[A-Za-z0-9_-]{1,128}"


class PublicError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)  # Never include backend messages, bodies, or credentials.


def private_auth(path, public=False):
    path = Path(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("explicit auth file must be mode 0600")
    value = json.loads(path.read_text())
    keys = {"username", "password", "selected_role"} if public else {"username", "password"}
    if (set(value) != keys or any(not isinstance(value[k], str) or not value[k]
                                 or "\r" in value[k] or "\n" in value[k] for k in keys)):
        raise ValueError("invalid explicit client credentials")
    return value


def client_auth_path(auth_file=None, runtime_profile=None):
    if bool(auth_file) == bool(runtime_profile):
        raise ValueError("choose explicit auth file or runtime profile reference")
    if auth_file:
        return str(Path(auth_file).resolve())
    # Read references as plain JSON. Never import runtime_profile or read backend authority.
    profile = Path(runtime_profile).resolve()
    reference = json.loads(profile.read_text()).get("client_auth_file")
    if not isinstance(reference, str) or not Path(reference).is_absolute():
        raise ValueError("absolute client auth reference required")
    return reference


def context(value):
    if not isinstance(value, dict) or set(value) != set(CONTEXT):
        raise PublicError("PUBLIC_CONTEXT")
    if (any(not isinstance(value[k], str) or not value[k] for k in CONTEXT
            if k not in ("binding_revision", "grant_revision"))
            or value["warehouse_id"] != "LOCAL"
            or type(value["binding_revision"]) is not int or value["binding_revision"] < 1
            or (value["grant_revision"] is not None and
                (type(value["grant_revision"]) is not int or value["grant_revision"] < 1))):
        raise PublicError("PUBLIC_CONTEXT")
    return copy.deepcopy(value)


def valid_handle(handle):
    return (isinstance(handle, dict) and isinstance(handle.get("id"), str)
            and re.fullmatch(TOKEN, handle["id"]) is not None
            and (handle.get("backend_query_id") is None or
                 isinstance(handle["backend_query_id"], str) and bool(handle["backend_query_id"])))


def rejection_scope(value, intent):
    try:
        b, identity = intent["dispatch"], intent["adapter_identity"]
        return (isinstance(value, dict) and intent.get("handle") is None and identity["kind"] == "shirokuma-public"
                and context(b["context"]) == identity["context"] and b["context"]["selected_role"] == identity["selected_role"]
                and b["request_id"] == intent["request_id"] and isinstance(b["worksheet_id"], str) and bool(b["worksheet_id"])
                and intent.get("mode") == "read-only" and sql_identity(intent["sql"]) == intent["sql_sha256"]
                and all(value.get(k) == intent[k] for k in ("request_id", "dispatch", "adapter_identity", "sql_sha256")))
    except (KeyError, ValueError, TypeError):
        return False


def valid_submission_rejection(value, intent):
    return (rejection_scope(value, intent)
            and set(value) == {"format", "kind", "http_status", "captured_api_code", "completed_response",
                               "server_request_id", "method", "path", "origin", "request_id", "dispatch", "adapter_identity", "sql_sha256"}
            and type(value.get("format")) is int and value["format"] == 1 and value.get("kind") == "http-submission-rejection"
            and type(value.get("http_status")) is int and value["http_status"] == 403 and value.get("captured_api_code") == "SQL_PERMISSION_DENIED"
            and value.get("completed_response") is True and value.get("method") == "POST"
            and value.get("path") == "/api/v1/queries" and value.get("origin") == intent["adapter_identity"]["origin"]
            and (value.get("server_request_id") is None or isinstance(value["server_request_id"], str)
                 and re.fullmatch(SERVER_ERROR_ID, value["server_request_id"]) is not None))


class PublicAdapter(TrinoAdapter):
    name = "shirokuma-public"

    def __init__(self, url, username, password, selected_role, captured=None, *, max_rows=10000):
        super().__init__(url)
        if type(max_rows) is not int or not 1 <= max_rows <= 10000:
            raise ValueError("max_rows must be 1..10000")
        self.account, self.role, self.max_rows = username, selected_role, max_rows
        self.headers = {"Authorization": "Basic " + base64.b64encode((username + ":" + password).encode()).decode(),
                        "X-Shirokuma-Role": selected_role, "Content-Type": "application/json"}
        self.context = context(captured) if captured is not None else None
        if self.context and self.context["selected_role"] != self.role:
            raise PublicError("PUBLIC_CONTEXT")
        self.binding = None

    def identity(self):
        if self.context is None:
            raise PublicError("PUBLIC_CONTEXT")
        return dict(kind=self.name, origin=list(self.origin), account=self.account,
                    selected_role=self.role, context=context(self.context), max_rows=self.max_rows)

    def select(self, database, schema, timeout=5):
        deadline = time.monotonic() + timeout
        items = self.request("GET", self.url + "/api/v1/databases", deadline)["items"]
        matches = [d for d in items if database in (d["id"], d["sql_catalog"])]
        if len(matches) != 1:
            raise PublicError("PUBLIC_DATABASE_SELECTION")
        d = matches[0]
        if d.get("state") != "READY" or not d.get("capabilities", {}).get("query") or self.role not in d.get("allowed_roles", []):
            raise PublicError("PUBLIC_AUTH")
        items = self.request("GET", self.url + "/api/v1/databases/" + urllib.parse.quote(d["id"], safe="") + "/schemas", deadline)["items"]
        matches = [s for s in items if schema in (s["id"], s["sql_name"])]
        if len(matches) != 1 or "read-only" not in matches[0].get("allowed_modes", []):
            raise PublicError("PUBLIC_SCHEMA_SELECTION")
        s = matches[0]
        self.context = context(dict(database_id=d["id"], schema_id=s["id"], warehouse_id="LOCAL",
            sql_catalog=d["sql_catalog"], sql_schema=s["sql_name"], selected_role=self.role,
            binding_revision=d["binding_revision"], generation_id=d["generation_id"], grant_revision=d.get("grant_revision")))
        return self.identity()

    def prepare_attempt(self, intent):
        intent["mode"] = "read-only"
        intent["dispatch"] = dict(request_id=intent["request_id"], worksheet_id=str(uuid.uuid4()),
                                  context=context(self.context))
        self.bind(intent)

    def bind(self, intent):
        b = intent.get("dispatch", {})
        if (intent.get("adapter_identity") != self.identity() or b.get("request_id") != intent.get("request_id")
                or not isinstance(b.get("worksheet_id"), str) or not b["worksheet_id"]
                or b.get("context") != self.context or not isinstance(intent.get("sql"), str)
                or intent.get("mode") != "read-only"):
            raise PublicError("PUBLIC_CONTEXT")
        self.binding = copy.deepcopy(b)
        self.sql, self.mode = intent["sql"], intent["mode"]

    def submission_rejection(self, error, intent):
        if (not isinstance(error, HTTPFailure) or error.http_status != 403 or error.api_code != "SQL_PERMISSION_DENIED"
                or error.method != "POST" or error.path != "/api/v1/queries" or list(error.origin) != self.identity()["origin"]
                or intent.get("handle") is not None or self.binding != intent.get("dispatch")
                or self.sql != intent.get("sql") or self.identity() != intent.get("adapter_identity")):
            return None
        value = dict(format=1, kind="http-submission-rejection", http_status=403, captured_api_code=error.api_code,
                     completed_response=True, server_request_id=error.server_request_id, method=error.method,
                     path=error.path, origin=list(error.origin),
                     **{k: copy.deepcopy(intent[k]) for k in ("request_id", "dispatch", "adapter_identity", "sql_sha256")})
        return value if valid_submission_rejection(value, intent) else None

    def validate(self, page, handle=None):
        b = self.binding
        if (not isinstance(page, dict) or "backend_query_id" not in page or not valid_handle(page) or b is None
                or any(page.get(k) != b[k] for k in ("request_id", "worksheet_id", "context"))
                or handle and (page["id"] != handle["id"] or handle.get("backend_query_id") is not None
                               and page.get("backend_query_id") != handle["backend_query_id"])):
            raise PublicError("PUBLIC_CONTEXT")
        context(page.get("context"))
        return {"id": page["id"], "backend_query_id": page.get("backend_query_id")}

    def page_url(self, uri, qid):
        if not isinstance(uri, str) or "#" in uri or "?" in uri:
            raise PublicError("PUBLIC_PAGE_URI")
        url = urllib.parse.urljoin(self.url + "/", uri)
        self.safe(url)
        p = urllib.parse.urlsplit(url)
        if p.query or p.fragment or not re.fullmatch("/api/v1/queries/" + re.escape(qid) + "/pages/" + TOKEN, p.path):
            raise PublicError("PUBLIC_PAGE_URI")
        return url

    def execute(self, sql, out, metadata, deadline, save, check):
        timing = adapter_timings(self)
        timing.durations["client_poll_wait_s"] = 0
        writer, columns, native, handle = None, None, None, None
        seen, receipts = set(), set()
        try:
            b = self.binding
            if sql != self.sql:
                raise PublicError("PUBLIC_CONTEXT")
            body = dict(sql=sql, request_id=b["request_id"], worksheet_id=b["worksheet_id"],
                        context={k: b["context"][k] for k in WIRE_CONTEXT}, mode="read-only", max_rows=self.max_rows)
            self.request_kind = "initial"
            timing.mark("dispatch_started")
            page = self.request("POST", self.url + "/api/v1/queries", deadline, json.dumps(body).encode())
            timing.mark("initial_response_complete")
            self.request_kind = "follow_up"
            while True:
                handle = self.validate(page, handle)
                save(handle)
                # query_service._page maps backend states, then overwrites stats.state
                # with the exposed state, including cancellation after truncation.
                state, stats = page.get("state"), page.get("stats")
                if (not isinstance(state, str) or state not in ("QUEUED", "RUNNING", "FINISHED", "FAILED", "CANCELED", "UNKNOWN")
                        or not isinstance(stats, dict) or stats.get("state") != state
                        or state == "FINISHED" and page.get("next_uri") is not None):
                    raise PublicError("PUBLIC_STATE")
                if state in ("FAILED", "CANCELED"):
                    timing.page({"stats": stats}, state)
                if page.get("truncated"):
                    raise PublicError("PUBLIC_TRUNCATED")
                if page.get("error") or state not in ("QUEUED", "RUNNING", "FINISHED"):
                    raise PublicError("PUBLIC_QUERY_UNMET")
                with timing.measure(timing.durations, "artifact_processing_s"):
                    data, incoming = page.get("data"), page.get("columns")
                    if not isinstance(data, list) or not isinstance(incoming, list):
                        raise PublicError("PUBLIC_SCHEMA")
                    pending = (handle["backend_query_id"] is None and incoming == [] and data == []
                               and state == "QUEUED" and "result_page_id" not in page and native is None
                               and bool(page.get("next_uri")))
                    if not pending:
                        pid = page.get("result_page_id")
                        if not (isinstance(pid, str) and re.fullmatch(TOKEN, pid) or type(pid) is int and pid > 0) or pid in receipts:
                            raise PublicError("PUBLIC_DUPLICATE_PAGE")
                        receipts.add(pid)
                        if (not handle["backend_query_id"] or not incoming and
                                (native is not None or data or state not in ("QUEUED", "RUNNING") or not page.get("next_uri"))):
                            raise PublicError("PUBLIC_SCHEMA")
                        if incoming:
                            raw = [{"name": c["name"], "type": c["type"]} for c in incoming]
                            if native is not None and raw != native:
                                raise PublicError("PUBLIC_SCHEMA")
                            native = raw
                            columns = [column(c["name"], c["type"]) for c in incoming]
                    timing.page(page, state)
                    if incoming and writer is None:
                        # Native type contracts remain in attempt evidence, outside equality metadata.
                        save(dict(handle, native_columns=native))
                        writer = Writer(out, columns, metadata)
                    check()
                    if incoming:
                        for n, row in enumerate(data):
                            if n % 256 == 0:
                                check()
                            writer.row(row)
                uri = page.get("next_uri")
                if uri is None:
                    if page["state"] != "FINISHED" or writer is None:
                        raise PublicError("PUBLIC_INCOMPLETE")
                    with timing.measure(timing.durations, "artifact_processing_s"):
                        writer.finish()
                    timing.mark("result_complete")
                    return
                url = self.page_url(uri, handle["id"])
                if url in seen:
                    raise PublicError("PUBLIC_DUPLICATE_PAGE")
                seen.add(url)
                check()
                page = self.request("GET", url, deadline)
        finally:
            self.request_kind = None
            if writer is not None and not writer.f.closed:
                writer.close()

    def cancel_reconcile(self, handle, timeout=5):
        if self.binding is None:
            return "UNKNOWN"
        intent = dict(request_id=self.binding["request_id"], dispatch=self.binding,
                      adapter_identity=self.identity(), handle=handle, sql=self.sql, mode=self.mode)
        return self.recover(intent, lambda h: None, timeout)

    def validate_inspect(self, page, handle):
        captured = self.validate(page, handle)
        if (page.get("sql") != self.sql or page.get("mode") != self.mode
                or page.get("state") not in ("QUEUED", "RUNNING", "FINISHED", "FAILED", "CANCELED", "UNKNOWN")):
            raise PublicError("PUBLIC_CONTEXT")
        return captured

    def recover(self, intent, save, timeout=5):
        self.request_kind = None  # history/inspect/cancel are recovery, not delivery
        self.bind(intent)
        deadline = time.monotonic() + timeout
        handle = intent.get("handle")
        try:
            if handle is None:
                query = urllib.parse.urlencode({"database_id": self.context["database_id"]})
                entries = self.request("GET", self.url + "/api/v1/queries?" + query, deadline)["queries"]
                matches = [p for p in entries if all(p.get(k) == self.binding[k]
                           for k in ("request_id", "worksheet_id", "context"))
                           and p.get("sql") == intent["sql"] and p.get("mode") == "read-only"]
                if len(matches) != 1:
                    return "UNKNOWN"
                handle = self.validate(matches[0])
                save(handle)
            if not valid_handle(handle):
                return "UNKNOWN"
            url = self.url + "/api/v1/queries/" + handle["id"]
            # Verify before cancellation too: a supplied/retained handle cannot cancel another query.
            page = self.request("GET", url, deadline)
            handle = self.validate_inspect(page, handle)
            save(handle)
            if page["state"] in ("FINISHED", "FAILED", "CANCELED"):
                return page["state"]  # A terminal observation needs no live backend pin/cancel.
            self.request("POST", url + "/cancel", deadline, b"{}")
            while time.monotonic() < deadline:
                page = self.request("GET", url, deadline)
                handle = self.validate_inspect(page, handle)
                save(handle)
                if page.get("state") in ("FINISHED", "FAILED", "CANCELED"):
                    return page["state"]
                time.sleep(min(.05, max(0, deadline - time.monotonic())))
        except Exception:
            pass
        return "UNKNOWN"
