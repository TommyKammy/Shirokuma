"""Public snapshot observations; original pins are immutable, checks are successors."""
import copy
import datetime as dt
import json
from pathlib import Path
import re
import time
import urllib.parse
import uuid

from .capacity import write_json
from .engine import ident
from .ingest import (budget, file_hash, fingerprint, get, operation, owned_record,
                     public_client, ref4, resolve, simple)
from .protocol import settings
from .public import PublicError, context
from .results import Reader, column, decoded
from .runner import run, sql_identity


def scalar_columns(columns):
    aliases = {"int": "integer", "long": "bigint", "string": "varchar", "float": "real",
               "timestamp": "timestamp(6)", "boolean": "boolean"}
    return [column(c["name"], aliases.get(c["type"], c["type"])) for c in columns]


def schema_details(adapter, physical, ref, deadline):
    if resolve(adapter, physical, deadline) != ref:
        raise PublicError("PUBLIC_RESOURCE")
    reply = get(adapter, "/api/v1/objects/" + urllib.parse.quote(ref["object_id"], safe=""), deadline, detail="columns")
    if (ref4(reply.get("ref"), adapter.context) != ref or reply.get("detail") != "columns"
            or reply.get("object", {}).get("physical_name") != physical):
        raise PublicError("PUBLIC_SCHEMA")
    return reply["columns"]


def observe(campaign, adapter, physical, prefix, deadline):
    """Every metadata SELECT uses the ordinary durable public runner."""
    ctx = adapter.context
    base = '.'.join(map(ident, (ctx["sql_catalog"], ctx["sql_schema"]))) + '.'
    queries = [dict(id="refs", sql="SELECT CAST(snapshot_id AS VARCHAR) AS snapshot_id FROM " + base
                    + ident(physical + "$refs") + " WHERE name='main'"),
               dict(id="log", sql="SELECT CAST(to_iso8601(timestamp) AS VARCHAR) AS committed_at, "
                    "CAST(file AS VARCHAR) AS metadata_file, CAST(latest_snapshot_id AS VARCHAR) AS snapshot_id, "
                    "CAST(latest_schema_id AS BIGINT) AS schema_id, CAST(latest_sequence_number AS BIGINT) AS sequence_number FROM "
                    + base + ident(physical + "$metadata_log_entries") + " ORDER BY timestamp DESC,file",
                    order_by=[])]
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("snapshot deadline")
    out = run(campaign, prefix + "-" + uuid.uuid4().hex, dict(format=1,
              dataset_id="snapshot-observation/" + fingerprint(ctx), queries=queries), adapter,
              timeout=remaining / 2, repetitions=1, reference_label="snapshot observation")
    results = {}
    for q in queries:
        reader = Reader(out / (q["id"] + "-0") / "result.jsonl")
        try:
            expected = ([column("snapshot_id", "varchar")] if q["id"] == "refs" else
                        [column(n, t) for n, t in (("committed_at", "varchar"), ("metadata_file", "varchar"),
                         ("snapshot_id", "varchar"), ("schema_id", "bigint"), ("sequence_number", "bigint"))])
            if reader.header["columns"] != expected:
                raise PublicError("PUBLIC_METADATA_SCHEMA")
            results[q["id"]] = [[decoded(v) for v in row] for row in reader]
        finally:
            reader.f.close()
    refs, rows = results["refs"], results["log"]
    if (len(refs) != 1 or not isinstance(refs[0][0], str) or not re.fullmatch(r"[0-9]+", refs[0][0])
            or not rows or len({tuple(r) for r in rows}) != len(rows)):
        raise PublicError("PUBLIC_SNAPSHOT_METADATA")
    entries = []
    for stamp, location, sid, schema, sequence in rows:
        if (not isinstance(stamp, str) or not isinstance(location, str) or not location
                or sid is not None and (not isinstance(sid, str) or not re.fullmatch(r"[0-9]+", sid))
                or schema is not None and (type(schema) is not int or schema < 0)
                or sequence is not None and (type(sequence) is not int or sequence < 0)):
            raise PublicError("PUBLIC_SNAPSHOT_METADATA")
        stamp = iso_time(stamp).isoformat(timespec="microseconds")
        entries.append(dict(committed_at=stamp, metadata_file=location, snapshot_id=sid, schema_id=schema,
                            sequence_number=sequence))
    expected = sorted(sorted(entries, key=lambda e: e["metadata_file"]), key=lambda e: iso_time(e["committed_at"]), reverse=True)
    if entries != expected:
        raise PublicError("PUBLIC_METADATA_ORDER")
    maximum = max(iso_time(e["committed_at"]) for e in entries)
    heads = [e for e in entries if iso_time(e["committed_at"]) == maximum]
    if (len(heads) != 1 or heads[0]["snapshot_id"] != refs[0][0]
            or any(heads[0][k] is None for k in ("snapshot_id", "schema_id", "sequence_number"))):
        raise PublicError("PUBLIC_METADATA_AMBIGUOUS")
    return dict(snapshot_id=refs[0][0], metadata_location=heads[0]["metadata_file"],
                metadata_log=entries, metadata_snapshot_schema_id=heads[0]["schema_id"], observation_run=out.name)


def iso_time(stamp):
    parsed = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PublicError("PUBLIC_METADATA_TIMESTAMP")
    return parsed.astimezone(dt.timezone.utc)


def reselect(adapter, expected):
    adapter.select(expected["database_id"], expected["schema_id"])
    if adapter.context != expected:
        raise PublicError("PUBLIC_CONTEXT")


def freeze_snapshots(campaign, load_journal, name, adapter, timeout=600):
    identity, ctx = public_client(adapter)
    load_path = Path(load_journal).resolve()
    load_root = load_path if load_path.is_dir() else load_path.parent
    campaign.refresh()
    kind = campaign.state["owned"].get(load_root.name, {}).get("kind")
    if kind not in ("public-load", "bulk-load"):
        raise ValueError("successful owned load required")
    _, load = owned_record(campaign, load_journal, "load.json", kind)
    if (type(load.get("format")) is not int or load["format"] != 1
            or load.get("status") != "SUCCESS"
            or (load.get("kind") != kind if kind == "bulk-load" else load.get("kind", kind) != kind)):
        raise ValueError("successful matching load format required")
    if load["context"] != ctx or load["adapter_identity"] != identity:
        raise PublicError("PUBLIC_CONTEXT")
    cfg = settings(timeout=timeout)
    if timeout > 600:
        raise ValueError("small qualification deadline required")
    campaign.check(cfg["peak_bytes"])
    root = campaign.own(name, "snapshot-pins")
    root.chmod(0o700)
    path = root / "snapshots.json"
    record = dict(format=1, status="INCOMPLETE", dataset_id=load["source_dataset_id"],
                  source_dataset_id=load["source_dataset_id"], context=ctx, tables=[], operations=[],
                  load_journal_sha256=file_hash(Path(load_journal) / "load.json" if Path(load_journal).is_dir() else load_journal))
    def save():
        write_json(path, record)
        path.chmod(0o600)
    save()
    deadline = time.monotonic() + timeout
    check = budget(campaign, root, cfg, deadline)
    try:
        reselect(adapter, ctx)
        for table in load["tables"]:
            check()
            if kind == "bulk-load":
                uid, sid = table.get("uuid"), table.get("snapshot")
                try:
                    parsed_uuid = uuid.UUID(uid)
                except (ValueError, TypeError, AttributeError):
                    raise PublicError("PUBLIC_LOAD_UNMET") from None
                if (not isinstance(uid, str) or str(parsed_uuid) != uid or parsed_uuid.int == 0
                        or not (type(sid) is int and sid > 0
                                or isinstance(sid, str) and re.fullmatch(r"[1-9][0-9]*", sid))):
                    raise PublicError("PUBLIC_LOAD_UNMET")
            physical, ref = simple(table["physical_name"]), ref4(table["ref"], ctx)
            if (table["committed_rows"] != table["rows"] or not table["cleanup_clean"]
                    or resolve(adapter, physical, deadline) != ref):
                raise PublicError("PUBLIC_LOAD_UNMET")
            plan = dict(table=table["name"])
            record["operations"].append(plan)
            op = operation(adapter, "table-profile", "/api/v1/table-profiles",
                           dict(context=ctx, source=ref, snapshot_id=None), plan, save, deadline, check)
            result = op.get("result") or {}
            source = result.get("source", {})
            if (op["state"] != "FINISHED" or source.get("ref") != ref
                    or result.get("row_count") != str(table["rows"])
                    or scalar_columns(source.get("columns", [])) != table["schema"]
                    or not isinstance(source.get("table_uuid"), str) or not source["table_uuid"]
                    or not re.fullmatch(r"[0-9]+", source.get("snapshot", ""))
                    or kind == "bulk-load" and (source.get("table_uuid") != uid or source.get("snapshot") != str(sid))
                    or any(type(source.get(k)) is not int or source[k] < 0 for k in ("schema_id", "current_schema_id"))
                    or any(not re.fullmatch(r"[a-f0-9]{64}", source.get(k, ""))
                           for k in ("schema_fingerprint", "current_schema_fingerprint"))):
                raise PublicError("PUBLIC_PROFILE_UNMET")
            current_columns = schema_details(adapter, physical, ref, deadline)
            if scalar_columns(current_columns) != table["schema"]:
                raise PublicError("PUBLIC_SCHEMA")
            measured = observe(campaign, adapter, physical, name, deadline)
            if measured["snapshot_id"] != source["snapshot"] or measured["metadata_snapshot_schema_id"] != source["schema_id"]:
                raise PublicError("PUBLIC_SNAPSHOT_CHANGED")
            record["tables"].append(dict(name=table["name"], physical_name=physical, ref=ref,
                table_uuid=source["table_uuid"], source_schema_id=source["schema_id"], snapshot_schema_id=source["schema_id"], schema_id=source["schema_id"],
                schema_fingerprint=source["schema_fingerprint"], columns=source["columns"],
                source_schema_fingerprint=source["schema_fingerprint"], source_columns=source["columns"],
                current_schema_id=source["current_schema_id"], current_schema_fingerprint=source["current_schema_fingerprint"],
                current_columns=current_columns, rows=table["rows"], source_sha256=table.get("source_sha256"), **measured))
            save()
        record["status"] = "SUCCESS"
        return path
    except BaseException as exc:
        record.update(status="UNKNOWN" if any(p.get("status") == "UNKNOWN" for p in record["operations"]) else "FAILED",
                      error_type=type(exc).__name__)
        raise
    finally:
        save()
        campaign.mark(root, record["status"])


def verify_snapshots(campaign, pins, adapter, timeout=120):
    _, record = owned_record(campaign, pins, "snapshots.json", "snapshot-pins")
    _, ctx = public_client(adapter)
    if ctx != record["context"]:
        raise PublicError("PUBLIC_CONTEXT")
    cfg = settings(timeout=timeout)
    if timeout > 600:
        raise ValueError("small qualification deadline required")
    root = campaign.own("snapshot-check-" + uuid.uuid4().hex, "snapshot-check")
    evidence = dict(format=1, status="INCOMPLETE", pins_sha256=fingerprint(record), context=ctx, tables=[])
    path = root / "check.json"
    write_json(path, evidence)
    deadline = time.monotonic() + timeout
    check = budget(campaign, root, cfg, deadline)
    try:
        reselect(adapter, ctx)
        for table in record["tables"]:
            check()
            ref = ref4(table["ref"], ctx)
            cols = schema_details(adapter, table["physical_name"], ref, deadline)
            measured = observe(campaign, adapter, table["physical_name"], root.name, deadline)
            evidence["tables"].append(dict(name=table["name"], ref=ref, current_columns=cols, **measured))
            if (cols != table["current_columns"] or any(measured[k] != table[k] for k in
                    ("snapshot_id", "metadata_location", "metadata_log", "metadata_snapshot_schema_id"))):
                raise PublicError("PUBLIC_SNAPSHOT_DRIFT")
            # Stable public ref is a registry surrogate bound to the Iceberg UUID.
            # With identical complete metadata, the frozen profile UUID/schema remain valid.
            write_json(path, evidence)
        evidence["status"] = "SUCCESS"
        return path
    except BaseException as exc:
        evidence.update(status="FAILED", error_type=type(exc).__name__)
        raise
    finally:
        write_json(path, evidence)
        campaign.mark(root, evidence["status"])


def table_manifest(pins):
    """Reproducible full-table projections over the source and immutable target snapshots."""
    ctx = context(pins.get("context"))
    source_id = pins.get("source_dataset_id")
    tables = pins.get("tables")
    if (pins.get("format") != 1 or pins.get("status") != "SUCCESS"
            or not isinstance(source_id, str) or not source_id
            or pins.get("dataset_id") != source_id or not isinstance(tables, list) or not tables):
        raise ValueError("successful source snapshot pins required")
    stable = copy.deepcopy(pins)
    names, physical, refs, uuids = set(), set(), set(), set()
    queries = []
    for table in tables:
        name, target = table.get("name"), table.get("physical_name")
        if any(not isinstance(n, str) or not n or "\x00" in n for n in (name, target)):
            raise ValueError("valid SQL table names required")
        ref = ref4(table.get("ref"), ctx)
        if set(table["ref"]) != set(ref):
            raise ValueError("exact public resource reference required")
        uid = table.get("table_uuid")
        try:
            parsed_uuid = uuid.UUID(uid)
        except (ValueError, TypeError, AttributeError):
            raise ValueError("real Iceberg table UUID required") from None
        sid = table.get("snapshot_id")
        if (parsed_uuid.int == 0 or not isinstance(sid, str) or not re.fullmatch(r"[0-9]+", sid)
                or int(sid) > 2**63 - 1):
            raise ValueError("valid snapshot identity required")
        if name in names or target in physical or fingerprint(ref) in refs or parsed_uuid in uuids:
            raise ValueError("unique canonical names, physical names, refs and UUIDs required")
        names.add(name)
        physical.add(target)
        refs.add(fingerprint(ref))
        uuids.add(parsed_uuid)
        cols = scalar_columns(table.get("columns", []))
        if (not cols or any(not isinstance(c["name"], str) or not c["name"] or "\x00" in c["name"] for c in cols)
                or len({c["name"] for c in cols}) != len(cols)):
            raise ValueError("unique scalar column schema required")
        projection = ", ".join("CAST(" + ident(c["name"]) + " AS " + c["type"].upper() + ") AS " + ident(c["name"]) for c in cols)
        logical = "SELECT " + projection + " FROM " + ident(name)
        sql = "SELECT " + projection + " FROM " + '.'.join(map(ident, (ctx["sql_catalog"], ctx["sql_schema"], target))) + " FOR VERSION AS OF " + sid
        queries.append(dict(id="table_" + name, sql=sql, sql_sha256=sql_identity(sql), target_sha256=sql_identity(sql),
                            logical_sql=logical, logical_sha256=sql_identity(logical), reference_sql=logical,
                            reference_sha256=sql_identity(logical), columns=cols, ordered=False))
    stable["tables"] = sorted(stable["tables"], key=lambda t: t["name"])
    snapshot_hash = sql_identity(json.dumps(stable, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False))
    return dict(format=1, source_dataset_id=source_id, dataset_id=source_id + "/snapshot/" + snapshot_hash,
                context=ctx, snapshot_pins=stable, snapshot_set_sha256=snapshot_hash,
                queries=sorted(queries, key=lambda q: q["id"]))
