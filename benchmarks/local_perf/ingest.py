"""Bounded Parquet export and explicit public ingestion, with durable one-shot keys."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import urllib.parse
import uuid

from .capacity import allocated, write_json
from .engine import bounded, connect
from .protocol import settings
from .public import PublicAdapter, PublicError, context
from .results import cell, column, decoded, dumps
from .dataset_layout import table_files, file_identity
from .runner import dataset_version, dataset_read

REF = ("database_id", "schema_id", "object_id", "incarnation")


def digest(value):
    return hashlib.sha256(value).hexdigest()


def fingerprint(value):
    return digest(dumps(value).encode("utf8"))


def file_hash(path, check=lambda: None):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        while True:
            block = f.read(1024 * 1024)
            check()
            if not block:
                return h.hexdigest()
            h.update(block)


def owned_record(campaign, value, filename, kind):
    path = Path(value).resolve()
    root = path if path.is_dir() else path.parent
    campaign.refresh()
    entry = campaign.state["owned"].get(root.name, {})
    if root.parent != campaign.root or entry.get("kind") != kind or entry.get("status") != "SUCCESS":
        raise ValueError("successful owned input required")
    record = json.loads((root / filename).read_text())
    if record.get("status") != "SUCCESS":
        raise ValueError("incomplete input")
    return root, record


def child(root, name):
    path = (root / name).resolve()
    if path.parent != root or not path.is_file():
        raise ValueError("owned input file required")
    return path


def budget(campaign, root, cfg, deadline):
    def check():
        if time.monotonic() >= deadline:
            raise TimeoutError("qualification deadline")
        growth = allocated(root)
        if growth > cfg["peak_bytes"]:
            raise ValueError("qualification peak exceeded")
        campaign.check(max(0, cfg["peak_bytes"] - growth))
    return check


def simple(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z_][a-z0-9_]{0,126}", value):
        raise ValueError("simple lowercase SQL identifier required")
    return value


def physical_identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,30}", value):
        raise ValueError("physical table name must be lowercase, letter-leading, and at most 31 characters")
    return value


def target_name(prefix, canonical):
    physical_identifier(prefix)
    if not isinstance(canonical, str) or not canonical or "\x00" in canonical:
        raise ValueError("valid source table name required")
    stem = re.sub(r"[^a-z0-9_]", "_", canonical.lower()).strip("_") or "table"
    suffix = "_" + digest(canonical.encode("utf8"))[:12]
    candidate = prefix + "_" + stem
    if canonical == stem and len(candidate) <= 31:
        return physical_identifier(candidate)
    available = 31 - len(prefix) - 1 - len(suffix)
    if available < 1:
        raise ValueError("prefix leaves no room for target identity")
    return physical_identifier(prefix + "_" + stem[:available] + suffix)


def hints_for(schema):
    columns = [column(c["name"], c["type"]) for c in schema]
    if (not columns or any(not isinstance(c["name"], str) or not c["name"] or "\x00" in c["name"] for c in columns)
            or len({c["name"] for c in columns}) != len(columns)):
        raise ValueError("unique nonempty scalar schema required")
    hints = {}
    for c in columns:
        kind = c["type"]
        if kind in ("hugeint", "ubigint") or "with time zone" in kind:
            raise ValueError("scalar type unsupported by manual ingest")
        hints[c["name"]] = kind.upper()
    return columns, hints


def prepare_ingest(campaign, dataset, name, prefix, chunk_bytes=4 * 1024 ** 2):
    """Export every source row, bounded by actual UTF8 bytes, without source writes."""
    simple(prefix)
    if type(chunk_bytes) is not int or not 1 <= chunk_bytes <= 4 * 1024 ** 2:
        raise ValueError("chunk_bytes must be 1..4MiB")
    source, meta = owned_record(campaign, dataset, "dataset.json", "dataset")
    version = dataset_version(meta)
    cfg = settings(scale=meta.get("scale", .01), timeout=min(meta.get("timeout_s", 120), 600))
    if meta.get("mode", "qualification") != "qualification" or meta.get("measured_logical_bytes", 0) > 1_000_000_000:
        raise ValueError("small qualification dataset required")
    campaign.check(cfg["peak_bytes"])
    root = campaign.own(name, "ingest-preparation")
    record = dict(format=version, status="INCOMPLETE", source_dataset_id=meta["dataset_id"],
                  source_manifest_sha256=file_hash(source / "dataset.json"), prefix=prefix,
                  chunk_bytes=chunk_bytes, tables=[], settings=cfg)
    if version == 2:
        record["source_manifest_file"] = str(source / "dataset.json")
    path = root / "input.json"
    write_json(path, record)
    con = None
    check = budget(campaign, root, cfg, time.monotonic() + cfg["timeout_s"])
    try:
        con = connect(root / "export.duckdb", root / "spill")
        names = set()
        with bounded(con, check, time.monotonic() + cfg["timeout_s"]):
            for table in meta["tables"]:
                physical = target_name(prefix, table["name"])
                if physical in names:
                    raise ValueError("duplicate source table")
                names.add(physical)
                files = table_files(source, table, version, check)
                for f in files:
                    sha = file_hash(f["path"], check)
                    if f["path"].stat().st_size != f["file_bytes"] or f.get("sha256") not in (None, sha):
                        raise ValueError("source file changed")
                    f["sha256"] = sha
                columns, hints = hints_for(table["schema"])
                sql = dataset_read(con, source, table, version, check)
                actual = [column(r[0], r[1]) for r in con.execute("DESCRIBE " + sql).fetchall()]
                if actual != columns:
                    raise ValueError("source schema changed")
                if version == 1:
                    item = dict(name=table["name"], physical_name=physical, schema=columns, schema_hints=hints,
                                source_file=str(files[0]["path"]), source_sha256=files[0]["sha256"],
                                source_bytes=files[0]["file_bytes"], rows=0, parts=[])
                else:
                    item = dict(name=table["name"], physical_name=physical, schema=columns, schema_hints=hints,
                                source_sha256=file_identity(files, version), source_bytes=sum(f["file_bytes"] for f in files),
                                rows=0, parts=[])
                    item["source_files"] = [dict(f, path=str(f["path"])) for f in files]
                    if con.execute("SELECT count(*) FROM (" + sql + ")").fetchone()[0] != table["rows"]:
                        raise ValueError("source row count changed")
                cursor = con.execute(sql)
                record["tables"].append(item)
                buf, rows = bytearray(), 0
                def flush():
                    nonlocal buf, rows
                    part = root / (physical + "-" + str(len(item["parts"])) + ".jsonl")
                    with part.open("xb") as f:
                        f.write(buf)
                        f.flush()
                        os.fsync(f.fileno())
                    item["parts"].append(dict(path=part.name, sha256=digest(buf), bytes=len(buf), rows=rows))
                    buf, rows = bytearray(), 0
                    check()
                while True:
                    batch = cursor.fetchmany(256)
                    check()
                    if not batch:
                        break
                    for row in batch:
                        values = {}
                        for value, col in zip(row, columns):
                            typed = cell(value, col["type"])
                            if typed is not None and typed[0] == "ts":
                                # cell() has checked declared precision and normalized to UTC.
                                # Manual ingest's timezone-free SQL type requires a naive ISO value.
                                typed = ["ts", decoded(typed).replace(tzinfo=None).isoformat(timespec="microseconds")]
                            # Decimal/date/timestamp stay exact strings on the public wire.
                            values[col["name"]] = (None if typed is None else typed[1]
                                if typed[0] in ("d", "date", "ts") else decoded(typed))
                        line = (dumps(values) + "\n").encode("utf8")
                        if len(line) > chunk_bytes:
                            raise ValueError("row exceeds chunk byte ceiling")
                        if buf and len(buf) + len(line) > chunk_bytes:
                            flush()
                        buf.extend(line)
                        rows += 1
                        item["rows"] += 1
                if buf or not item["parts"]:
                    flush()
                if version == 2 and con.execute("SELECT count(*) FROM (" + sql + ")").fetchone()[0] != table["rows"]:
                    raise ValueError("source row count changed")
                if (item["rows"] != table["rows"] or any(
                        f["path"].stat().st_size != f["file_bytes"] or file_hash(f["path"], check) != f["sha256"]
                        for f in files)):
                    raise ValueError("source row count/digest changed")
                write_json(path, record)
        if version == 2:
            validate_prepared_sources(campaign, record, check)
        if file_hash(source / "dataset.json", check) != record["source_manifest_sha256"]:
            raise ValueError("source manifest changed")
        record["status"] = "SUCCESS"
        return root
    except BaseException as exc:
        record.update(status="FAILED", error_type=type(exc).__name__)
        raise
    finally:
        cleanup_errors = []
        for action in ([con.close] if con is not None else []) + [
                lambda p=p: shutil.rmtree(p) if p.is_dir() else p.unlink(missing_ok=True)
                for p in (root / "export.duckdb", root / "export.duckdb.wal", root / "spill")]:
            try:
                action()
            except Exception as exc:
                cleanup_errors.append(type(exc).__name__)
        succeeded = record["status"] == "SUCCESS"
        if cleanup_errors:
            record.update(status="FAILED", cleanup_error_types=cleanup_errors)
        write_json(path, record)
        campaign.mark(root, record["status"])
        if cleanup_errors and succeeded:
            raise RuntimeError("owned export cleanup failed")


def public_client(adapter):
    if not isinstance(adapter, PublicAdapter):
        raise ValueError("explicit PublicAdapter required")
    return adapter.identity(), context(adapter.context)


def get(adapter, endpoint, deadline, **params):
    return adapter.request("GET", adapter.url + endpoint + "?" + urllib.parse.urlencode(params), deadline)


def ref4(value, ctx):
    if (not isinstance(value, dict) or not set(REF) <= set(value)
            or any(not isinstance(value[k], str) or not value[k] for k in REF if k != "incarnation")
            or type(value.get("incarnation")) is not int or value["incarnation"] < 1
            or any(value[k] != ctx[k] for k in ("database_id", "schema_id"))):
        raise PublicError("PUBLIC_RESOURCE")
    return {k: value[k] for k in REF}


def resolve(adapter, physical, deadline):
    ctx = adapter.context
    reply = get(adapter, "/api/v1/objects/resolve", deadline, database_id=ctx["database_id"],
                schema_id=ctx["schema_id"], name=physical)
    obj = reply["object"]
    if obj.get("physical_name") != physical or obj.get("kind") != "TABLE":
        raise PublicError("PUBLIC_RESOURCE")
    return ref4(reply["ref"], ctx)


def absent(adapter, names, deadline):
    """Use the actual paginated public inventory; errors never mean absence."""
    cursor, seen = None, set()
    while True:
        params = dict(limit=100)
        if cursor:
            params["cursor"] = cursor
        page = get(adapter, "/api/v1/schemas/" + urllib.parse.quote(adapter.context["schema_id"], safe="") + "/objects", deadline, **params)
        if not isinstance(page.get("items"), list):
            raise PublicError("PUBLIC_INVENTORY")
        if any(i.get("physical_name") in names for i in page["items"]):
            raise PublicError("PUBLIC_TARGET_EXISTS")
        cursor = page.get("next_cursor")
        if cursor is None:
            return
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise PublicError("PUBLIC_INVENTORY")
        seen.add(cursor)


def receipt(operation):
    """Retain public operation evidence, excluding free-form backend error text."""
    result = operation.get("result")
    if isinstance(result, dict):
        result = {k: v for k, v in result.items() if k in {
            "source", "row_count", "column_count", "operation_id", "input_fingerprint", "request_fingerprint",
            "input_length", "format", "encoding", "delimiter", "mode", "atomic", "target", "columns",
            "accepted_rows", "committed_rows", "committed_batches", "loaded_batches", "cleanup",
            "schema_hints", "observed_columns"}}
    return dict(id=operation.get("id"), state=operation.get("state"), committed=operation.get("committed"),
                cleanup_error=bool(operation.get("cleanup_error")), result=result)


def cancel_owned(adapter, plan, identity, ctx, save):
    """One bounded cancellation of an owned active operation, retaining commit evidence."""
    known = plan.get("receipt", {})
    if (not plan.get("operation_id") or known.get("state") not in ("ACTIVE", "CANCELING")
            or adapter.identity() != identity or adapter.context != ctx):
        return None
    evidence = dict(status="INTENT", operation_id=plan["operation_id"], context=ctx, observations=[])
    plan["cancellation"] = evidence
    save()
    deadline = time.monotonic() + 5
    terminal = None
    endpoint = "/api/v1/dataops/" + plan["operation_id"]
    try:
        reply = adapter.request("POST", adapter.url + endpoint + "/cancel", deadline, dumps(dict(context=ctx)).encode())
        op = reply.get("operation", {})
        if reply.get("context") != ctx or op.get("id") != plan["operation_id"]:
            raise PublicError("PUBLIC_OPERATION")
        evidence["response"] = dict(id=op["id"], state=op.get("state"))
        if op.get("state") in ("FINISHED", "FAILED", "CANCELED"):
            terminal = op["state"]
        save()  # Cancellation responses omit result/committed; never replace the receipt.
        while time.monotonic() < deadline:
            if adapter.identity() != identity or adapter.context != ctx:
                raise PublicError("PUBLIC_CONTEXT")
            reply = get(adapter, endpoint, deadline, context=dumps(ctx))
            op = reply.get("operation", {})
            if reply.get("context") != ctx or op.get("id") != plan["operation_id"]:
                raise PublicError("PUBLIC_OPERATION")
            observed = receipt(op)
            evidence["observations"].append(observed)
            if known.get("committed") is True and op.get("committed", True) is not True:
                raise PublicError("PUBLIC_OPERATION_CHANGED")
            if op.get("state") in ("FINISHED", "FAILED", "CANCELED"):
                terminal = op["state"]
                # Full inspection can enrich a receipt, but an omitted result cannot erase it.
                if observed["result"] is None:
                    observed["result"] = known.get("result")
                if observed["committed"] is None:
                    observed["committed"] = known.get("committed")
                if "cleanup_error" not in op:
                    observed["cleanup_error"] = known.get("cleanup_error", False)
                plan["receipt"] = observed
                plan.setdefault("receipts", []).append(observed)
                break
            if op.get("state") == "UNKNOWN":
                break
            save()
            time.sleep(min(.05, max(0, deadline - time.monotonic())))
    except Exception as exc:
        evidence["error_type"] = type(exc).__name__
    evidence["status"] = terminal or "UNKNOWN"
    save()
    return terminal


def operation(adapter, route, endpoint, body, plan, save, deadline, check):
    """One POST only. Reconcile its private key; never infer acceptance from data."""
    identity, ctx = public_client(adapter)
    if body["context"] != ctx:
        raise PublicError("PUBLIC_CONTEXT")
    plan.update(context=ctx, adapter_identity=identity, route=route, key=uuid.uuid4().hex,
                body_sha256=fingerprint(body), status="INTENT", operation_id=None)
    save()  # body, preview and key frozen before the mutation
    previous = adapter.headers.get("Idempotency-Key")
    accepted = None
    try:
        adapter.headers["Idempotency-Key"] = plan["key"]
        try:
            reply = adapter.request("POST", adapter.url + endpoint, deadline, dumps(body).encode("utf8"))
            if reply.get("context") != ctx:
                raise PublicError("PUBLIC_CONTEXT")
            accepted = reply["operation"]
            if not isinstance(accepted.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", accepted["id"]):
                raise PublicError("PUBLIC_OPERATION")
            plan["operation_id"] = accepted["id"]
            plan["receipt"] = receipt(accepted)
            plan["receipts"] = [plan["receipt"]]
            save()
        except Exception as exc:
            plan["post_error_type"] = type(exc).__name__
            save()
        # Ingest lookup checks server-side authenticated owner + route + key + exact context.
        # table-profile does not currently expose by-key recovery; known IDs still inspect.
        if route == "manual-ingest" or accepted is None:
            reply = get(adapter, "/api/v1/dataops/by-key", deadline, context=dumps(ctx), route=route, key=plan["key"])
            found = reply.get("recovery", {})
            if (reply.get("context") != ctx or found.get("route") != route
                    or not isinstance(found.get("operation_id"), str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", found["operation_id"])
                    or plan["operation_id"] is not None and found["operation_id"] != plan["operation_id"]):
                raise PublicError("PUBLIC_OPERATION")
            plan["operation_id"] = found["operation_id"]
            save()
        while True:
            check()
            if adapter.identity() != identity:
                raise PublicError("PUBLIC_CONTEXT")
            reply = get(adapter, "/api/v1/dataops/" + plan["operation_id"], deadline, context=dumps(ctx))
            op = reply.get("operation", {})
            if reply.get("context") != ctx or op.get("id") != plan["operation_id"]:
                raise PublicError("PUBLIC_OPERATION")
            state = op.get("state")
            if state not in ("ACTIVE", "CANCELING", "UNKNOWN", "FINISHED", "FAILED", "CANCELED"):
                raise PublicError("PUBLIC_OPERATION")
            observed, previous_receipt = receipt(op), plan.get("receipt")
            if previous_receipt and (previous_receipt["state"] in ("FINISHED", "FAILED", "CANCELED")
                    and previous_receipt["state"] != state or previous_receipt["committed"] is True and op.get("committed") is not True):
                raise PublicError("PUBLIC_OPERATION_CHANGED")
            if observed != previous_receipt:
                plan.setdefault("receipts", []).append(observed)
            plan.update(receipt=observed, status=state)
            save()
            if state in ("FINISHED", "FAILED", "CANCELED"):
                return plan["receipt"]
            if state == "UNKNOWN":
                raise PublicError("PUBLIC_OPERATION_UNKNOWN")
            time.sleep(min(.05, max(0, deadline - time.monotonic())))
    except BaseException as exc:
        canceled = cancel_owned(adapter, plan, identity, ctx, save) if isinstance(exc, TimeoutError) or time.monotonic() >= deadline else None
        known = canceled or plan.get("receipt", {}).get("state")
        plan.update(status=known if known in ("FINISHED", "FAILED", "CANCELED") else "UNKNOWN",
                    error_type=type(exc).__name__)
        save()
        raise
    finally:
        if previous is None:
            adapter.headers.pop("Idempotency-Key", None)
        else:
            adapter.headers["Idempotency-Key"] = previous


def validate_prepared_sources(campaign, meta, check):
    """Bind prepared shards to the successful owned producer before dispatch."""
    source, original = owned_record(campaign, meta["source_manifest_file"], "dataset.json", "dataset")
    if (Path(meta["source_manifest_file"]).resolve() != source / "dataset.json"
            or dataset_version(original) != 2 or original["dataset_id"] != meta["source_dataset_id"]
            or file_hash(source / "dataset.json", check) != meta["source_manifest_sha256"]):
        raise ValueError("source manifest changed")
    tables = original["tables"]
    if len(tables) != len(meta["tables"]) or len({t["name"] for t in tables}) != len(tables):
        raise ValueError("source table provenance changed")
    by_name = {t["name"]: t for t in tables}
    for t in meta["tables"]:
        declared = by_name.pop(t["name"])
        files = table_files(source, declared, 2, check)
        expected = [dict(f, path=str(f["path"])) for f in files]
        if (t["source_files"] != expected or t["source_sha256"] != file_identity(files, 2)
                or t["source_bytes"] != declared["file_bytes"] or t["rows"] != declared["rows"]
                or t["schema"] != [column(c["name"], c["type"]) for c in declared["schema"]]):
            raise ValueError("source provenance changed")
        for f in files:
            check()
            if f["path"].stat().st_size != f["file_bytes"] or file_hash(f["path"], check) != f["sha256"]:
                raise ValueError("source file changed")


def load_public(campaign, prepared, name, adapter, timeout=600, batch_size=2000):
    identity, ctx = public_client(adapter)
    cfg = settings(timeout=timeout)
    if timeout > 600 or type(batch_size) is not int or not 1 <= batch_size <= 10000:
        raise ValueError("bounded qualification timeout/batch size required")
    source, meta = owned_record(campaign, prepared, "input.json", "ingest-preparation")
    version = dataset_version(meta)
    campaign.refresh()
    for prior, entry in campaign.state["owned"].items():
        if entry["kind"] == "public-load" and entry["status"] in ("INCOMPLETE", "UNKNOWN"):
            raise PublicError("PUBLIC_PRIOR_LOAD_UNKNOWN")
    campaign.check(cfg["peak_bytes"])
    root = campaign.own(name, "public-load")
    root.chmod(0o700)  # Private idempotency keys, no credentials.
    path = root / "load.json"
    journal = dict(format=1, status="INCOMPLETE", source_dataset_id=meta["source_dataset_id"],
                   prepared_manifest_sha256=file_hash(source / "input.json"), context=ctx,
                   adapter_identity=identity, tables=[], operations=[], settings=cfg)
    def save():
        write_json(path, journal)
        path.chmod(0o600)
    save()
    deadline = time.monotonic() + timeout
    check = budget(campaign, root, cfg, deadline)
    previous_timeout = adapter.request_timeout
    adapter.request_timeout = 30
    try:
        if version == 2:
            validate_prepared_sources(campaign, meta, check)
        # ALL input bytes/rows/schema/source hashes and worst-case encoded bodies before mutation.
        names = set()
        for t in meta["tables"]:
            physical = physical_identifier(t["physical_name"])
            if physical != target_name(meta["prefix"], t["name"]) or physical in names:
                raise ValueError("invalid fresh target")
            names.add(physical)
            cols, hints = hints_for(t["schema"])
            if (hints != t["schema_hints"] or version == 1
                    and file_hash(t["source_file"], check) != t["source_sha256"]):
                raise ValueError("source/schema changed")
            count = 0
            for part in t["parts"]:
                part_path = child(source, part["path"])
                if not 0 <= part_path.stat().st_size <= min(meta.get("chunk_bytes", 4 * 1024 ** 2), 4 * 1024 ** 2):
                    raise ValueError("chunk exceeds bounded input size")
                raw = part_path.read_bytes()
                if digest(raw) != part["sha256"] or len(raw) != part["bytes"]:
                    raise ValueError("chunk changed")
                rows = raw.decode("utf8").splitlines()
                if len(rows) != part["rows"]:
                    raise ValueError("chunk row count changed")
                for line in rows:
                    row = json.loads(line)
                    if list(row) != [c["name"] for c in cols]:
                        raise ValueError("chunk fields changed")
                    for c in cols:
                        cell(row[c["name"]], c["type"])
                count += len(rows)
                # Conservative maximum ref/key overhead; exact body checked again below.
                if len(dumps(dict(content_base64=base64.b64encode(raw).decode(), context=ctx,
                                  schema_hints=hints)).encode()) + 8192 >= 10 * 1024 ** 2:
                    raise ValueError("encoded body exceeds API ceiling")
                check()
            if count != t["rows"] or not t["parts"] or count == 0:
                raise ValueError("incomplete table chunks")
        if file_hash(source / "input.json", check) != journal["prepared_manifest_sha256"]:
            raise ValueError("prepared manifest changed")
        absent(adapter, names, deadline)
        for t in meta["tables"]:
            item = dict(name=t["name"], physical_name=t["physical_name"], schema=t["schema"],
                        schema_hints=t["schema_hints"], source_sha256=t["source_sha256"],
                        rows=t["rows"], committed_rows=0, ref=None, cleanup_clean=None)
            journal["tables"].append(item)
            for part in t["parts"]:
                check()
                raw = child(source, part["path"]).read_bytes()
                if digest(raw) != part["sha256"]:
                    raise ValueError("chunk changed")
                body = dict(context=ctx, filename=part["path"], content_base64=base64.b64encode(raw).decode(),
                            format="jsonl", schema_hints=t["schema_hints"], batch_size=batch_size,
                            key_columns=[], atomic_append=True, mode="append" if item["ref"] else "create")
                if item["ref"]:
                    body["target"] = item["ref"]
                else:
                    body["destination"] = dict(database_id=ctx["database_id"], schema_id=ctx["schema_id"], physical_name=t["physical_name"])
                plan = dict(table=t["name"], part=part["path"], source_sha256=part["sha256"], context=ctx,
                            preview_body_sha256=fingerprint(body), status="PREVIEW_INTENT")
                journal["operations"].append(plan)
                save()
                preview = adapter.request("POST", adapter.url + "/api/v1/ingests/preview", deadline, dumps(body).encode())
                p = preview.get("preview", {})
                if (preview.get("context") != ctx or p.get("input_fingerprint") != part["sha256"]
                        or p.get("total_rows") != part["rows"]
                        or p.get("columns") != [dict(name=c["name"], type=t["schema_hints"][c["name"]]) for c in t["schema"]]
                        or not re.fullmatch(r"[a-f0-9]{64}", p.get("request_fingerprint", ""))):
                    raise PublicError("PUBLIC_PREVIEW")
                plan["preview"] = {k: p[k] for k in ("input_fingerprint", "request_fingerprint", "total_rows", "columns")}
                body["expected_preview_fingerprint"] = p["request_fingerprint"]
                if len(dumps(body).encode()) >= 10 * 1024 ** 2:
                    raise ValueError("encoded body exceeds API ceiling")
                op = operation(adapter, "manual-ingest", "/api/v1/ingests", body, plan, save, deadline, check)
                result = op.get("result") or {}
                item["cleanup_clean"] = item["cleanup_clean"] is not False and result.get("cleanup") in ("finished", "not-needed") and not op["cleanup_error"]
                if (op["committed"] is True and result.get("committed_rows") == part["rows"]
                        and result.get("input_fingerprint") == part["sha256"]):
                    committed_target = ref4(result.get("target"), ctx)
                    if item["ref"] and item["ref"] != committed_target:
                        raise PublicError("PUBLIC_RESOURCE")
                    item.update(ref=committed_target, committed_rows=item["committed_rows"] + part["rows"])
                    save()  # Preserve committed counts even when later observation/cleanup fails.
                if (op["state"] != "FINISHED" or op["committed"] is not True
                        or result.get("committed_rows") != part["rows"] or result.get("accepted_rows") != part["rows"]
                        or result.get("operation_id") != op["id"] or result.get("mode") != body["mode"]
                        or result.get("input_length") != len(raw)
                        or result.get("input_fingerprint") != part["sha256"] or result.get("atomic") is not True
                        or result.get("columns") != p["columns"] or result.get("observed_columns") != p["columns"]
                        or result.get("schema_hints") != t["schema_hints"]):
                    raise PublicError("PUBLIC_COMMIT_UNMET")
                target = ref4(result.get("target"), ctx)
                if item["ref"] and item["ref"] != target or resolve(adapter, t["physical_name"], deadline) != target:
                    raise PublicError("PUBLIC_RESOURCE")
                save()
                if not item["cleanup_clean"]:
                    raise PublicError("PUBLIC_CLEANUP_UNMET")
        if version == 2:
            validate_prepared_sources(campaign, meta, check)
        if file_hash(source / "input.json", check) != journal["prepared_manifest_sha256"]:
            raise ValueError("prepared manifest changed")
        journal["status"] = "SUCCESS"
        return path
    except BaseException as exc:
        unknown = any(p.get("status") in ("INTENT", "ACTIVE", "CANCELING", "UNKNOWN") for p in journal["operations"])
        journal.update(status="UNKNOWN" if unknown else "FAILED", error_type=type(exc).__name__)
        raise
    finally:
        # Terminal POST receipts remain useful evidence even if lookup/inspection fails.
        for table in journal["tables"]:
            plans = [p for p in journal["operations"] if p["table"] == table["name"]]
            commits = [p["receipt"]["result"] for p in plans
                       if p.get("receipt", {}).get("committed") is True
                       and isinstance(p["receipt"].get("result"), dict)
                       and p["receipt"]["result"].get("input_fingerprint") == p["source_sha256"]
                       and type(p["receipt"]["result"].get("committed_rows")) is int]
            table["committed_rows"] = sum(r["committed_rows"] for r in commits)
            table["cleanup_clean"] = bool(plans) and all(
                p.get("receipt", {}).get("state") in ("FINISHED", "FAILED", "CANCELED")
                and not p["receipt"]["cleanup_error"]
                and (p["receipt"].get("result") or {}).get("cleanup") in ("finished", "not-needed") for p in plans)
            if commits and table["ref"] is None:
                try:
                    refs = [ref4(r.get("target"), ctx) for r in commits]
                    if all(ref == refs[0] for ref in refs):
                        table["ref"] = refs[0]
                except ValueError:
                    pass  # Invalid refs remain unmet; original receipt and failure are retained.
        adapter.request_timeout = previous_timeout
        save()
        campaign.mark(root, journal["status"])
