"""Bounded direct DuckDB Parquet export and owned dataset sharding."""
import hashlib
import json
import math
from pathlib import Path
import shutil
import time

from .capacity import allocated, write_json
from .dataset_layout import table_files, read_sql
from .engine import bounded, connect, quote
from .protocol import LIMIT, settings

MAX_FILE_BYTES = 104857600000
DEFAULT_ROW_GROUP_SIZE = 122880


def controls(target_file_bytes, row_group_size, max_file_bytes, timeout):
    for value in (target_file_bytes, row_group_size, max_file_bytes):
        if type(value) is not int or value <= 0:
            raise ValueError("positive integer sharding controls required")
    if not target_file_bytes <= max_file_bytes <= MAX_FILE_BYTES or row_group_size < 2048:
        raise ValueError("invalid sharding bounds")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("positive finite timeout required")


def file_hash(path, check=lambda: None):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while True:
            check()
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def schema_of(con, sql):
    return [{"name": r[0], "type": r[1]} for r in con.execute("DESCRIBE " + sql).fetchall()]


def export_table(con, out, table_index, source_sql, schema, rows, *,
                 target_file_bytes=256 * 1024 ** 2, row_group_size=DEFAULT_ROW_GROUP_SIZE,
                 max_file_bytes=MAX_FILE_BYTES, check=lambda: None):
    """Scan once into a fresh directory; flatten and validate every emitted file.

    The byte target is soft at row-group boundaries. The post-stat maximum is hard.
    Caller holds engine.bounded throughout export and multiset validation.
    """
    controls(target_file_bytes, row_group_size, max_file_bytes, 1)
    out = Path(out)
    directory = out / ("parts-%06d" % table_index)
    if directory.exists():
        raise FileExistsError("export directory already used")
    check()
    con.execute("COPY (" + source_sql + ") TO " + quote(directory) +
                f" (FORMAT PARQUET, FILE_SIZE_BYTES {target_file_bytes}, ROW_GROUP_SIZE {row_group_size}, PER_THREAD_OUTPUT false, COMPRESSION ZSTD)")
    paths = sorted(directory.glob("*.parquet"))
    if not paths:
        raise ValueError("export produced no Parquet files")
    files = []
    for index, path in enumerate(paths):
        target = out / ("table-%06d-part-%06d.parquet" % (table_index, index))
        if target.exists():
            raise FileExistsError("export file already used")
        path.rename(target)
        check()
        size = target.stat().st_size
        if size > max_file_bytes:
            raise ValueError("Parquet file exceeds hard maximum")
        sql = "SELECT * FROM read_parquet(" + quote(target) + ")"
        if schema_of(con, sql) != schema:
            raise ValueError("export schema mismatch")
        count = con.execute("SELECT count(*) FROM (" + sql + ")").fetchone()[0]
        files.append(dict(file=target.name, rows=count, file_bytes=size,
                          physical_bytes=allocated(target), sha256=file_hash(target, check)))
    directory.rmdir()
    table = dict(rows=rows, schema=schema, files=files,
                 file_bytes=sum(f["file_bytes"] for f in files),
                 physical_bytes=sum(f["physical_bytes"] for f in files))
    if sum(f["rows"] for f in files) != rows:
        raise ValueError("export row count mismatch")
    parquet = read_sql(out, table, 2)
    difference = "SELECT count(*) FROM ((" + source_sql + " EXCEPT ALL " + parquet + ") UNION ALL (" + parquet + " EXCEPT ALL " + source_sql + ")) differences"
    if con.execute(difference).fetchone()[0] != 0:
        raise ValueError("full Parquet multiset roundtrip mismatch")
    table["roundtrip"] = "full EXCEPT ALL both ways; same-engine, not independent"
    check()
    return table


def shard_dataset(campaign, dataset, name, *, target_file_bytes=256 * 1024 ** 2,
                  row_group_size=DEFAULT_ROW_GROUP_SIZE, max_file_bytes=MAX_FILE_BYTES,
                  timeout=600, peak_bytes=None):
    controls(target_file_bytes, row_group_size, max_file_bytes, timeout)
    source = Path(dataset).resolve()
    # Require a successful dataset owned by this campaign, without writing it.
    campaign.refresh()
    owned = campaign.state.get("owned", {}).get(source.name, {})
    if source.parent != Path(campaign.root).resolve() or owned.get("kind") != "dataset" or owned.get("status") != "SUCCESS":
        raise ValueError("successful owned dataset required")
    deadline = time.monotonic() + timeout
    def precheck():
        if time.monotonic() >= deadline:
            raise TimeoutError("sharding deadline")
        campaign.check(peak)
    manifest = source / "dataset.json"
    frozen = manifest.read_bytes()
    original = json.loads(frozen)
    if original.get("status") != "SUCCESS" or not isinstance(original.get("dataset_id"), str) or not original["dataset_id"].strip():
        raise ValueError("successful source identity required")
    peak = original.get("peak_bytes") if peak_bytes is None else peak_bytes
    # Reuse protocol mode/scale constraints; supplied peak is an explicit budget,
    # not a fresh six-times generation estimate.
    cfg = settings(mode=original.get("mode", "qualification"), scale=original.get("scale", .01),
                   peak=peak, timeout=timeout, repetitions=original.get("repetitions", 1),
                   peak_evidence=original.get("peak_evidence"))
    if peak is None:
        raise ValueError("source or override peak_bytes required")
    peak = cfg["peak_bytes"]
    measured = original.get("measured_logical_bytes", 0)
    if type(measured) is not int or measured < 0:
        raise ValueError("nonnegative integer measured_logical_bytes required")
    if measured > (1_000_000_000 if cfg["mode"] == "qualification" else LIMIT):
        raise ValueError("source logical data exceeds mode ceiling")
    precheck()
    version = original.get("format")
    tables = original.get("tables")
    if not isinstance(tables, list) or not tables:
        raise ValueError("source tables required")
    files, names, filenames = [], set(), set()
    for table in tables:
        if not isinstance(table.get("name"), str) or not table["name"] or table["name"] in names:
            raise ValueError("unique table names required")
        names.add(table["name"])
        items = table_files(source, table, version, precheck)
        for item in items:
            if item["file"] in filenames:
                raise ValueError("duplicate dataset file")
            filenames.add(item["file"])
            digest = file_hash(item["path"], precheck)
            if item["path"].stat().st_size != item["file_bytes"] or item["sha256"] not in (None, digest):
                raise ValueError("source file bytes/digest mismatch")
            item["sha256"] = digest
        files.extend(items)
    original_queries = original.get("queries", [])
    if not isinstance(original_queries, list):
        raise ValueError("source queries must be a list")
    query_path = source / "queries.json"
    query_bytes = query_path.read_bytes() if query_path.exists() else None
    queries = json.loads(query_bytes) if query_bytes is not None else None
    if query_bytes is not None:
        if not isinstance(queries, dict) or type(queries.get("format")) is not int or queries["format"] != 1 or queries.get("dataset_id") != original["dataset_id"] or not isinstance(queries.get("queries"), list):
            raise ValueError("invalid source queries manifest")
        if original_queries and queries["queries"] != original_queries:
            raise ValueError("source queries manifests differ")
    elif original_queries:
        queries = dict(format=1, queries=original_queries)

    def unchanged():
        if manifest.read_bytes() != frozen or (query_path.read_bytes() if query_path.exists() else None) != query_bytes:
            raise ValueError("source manifest changed")
        for item in files:
            if item["path"].stat().st_size != item["file_bytes"] or file_hash(item["path"], check) != item["sha256"]:
                raise ValueError("source file changed")

    out = campaign.own(name, "dataset")
    meta = dict(original)
    meta.update(**cfg)
    meta.update(format=2, status="INCOMPLETE", dataset_id=campaign.state["id"] + "/" + name,
                source_dataset_id=original["dataset_id"], source_manifest_sha256=hashlib.sha256(frozen).hexdigest(),
                peak_bytes=peak, tables=[], sharding=dict(target_file_bytes=target_file_bytes, target_kind="soft",
                row_group_size=row_group_size, max_file_bytes=max_file_bytes,
                codec="zstd", timeout_s=cfg["timeout_s"], peak_bytes=cfg["peak_bytes"]))
    destination = out / "dataset.json"
    write_json(destination, meta)
    start = allocated(out)
    con = None
    def check():
        if time.monotonic() >= deadline:
            raise TimeoutError("sharding deadline")
        growth = max(0, allocated(out) - start)
        if growth > peak:
            raise RuntimeError("declared sharding peak exceeded")
        campaign.check(max(0, peak - growth))
    try:
        con = connect(out / "source.duckdb", out / "spill", temp_bytes=peak // 3)
        with bounded(con, check, deadline):
            # Validate every source before the first COPY.
            sources = []
            for table in tables:
                sql = read_sql(source, table, version)
                schema = schema_of(con, sql)
                rows = con.execute("SELECT count(*) FROM (" + sql + ")").fetchone()[0]
                if table.get("schema") != schema or rows != table["rows"]:
                    raise ValueError("source schema/row count mismatch")
                for item in table_files(source, table, version, check):
                    one = "SELECT * FROM read_parquet(" + quote(item["path"]) + ")"
                    if schema_of(con, one) != schema or con.execute("SELECT count(*) FROM (" + one + ")").fetchone()[0] != item["rows"]:
                        raise ValueError("source file schema/row count mismatch")
                sources.append((sql, schema, rows))
            unchanged()
            for index, (table, (sql, schema, rows)) in enumerate(zip(tables, sources)):
                exported = export_table(con, out, index, sql, schema, rows,
                                        target_file_bytes=target_file_bytes, row_group_size=row_group_size,
                                        max_file_bytes=max_file_bytes, check=check)
                exported["name"] = table["name"]
                if "logical_bytes" in table:
                    exported["logical_bytes"] = table["logical_bytes"]
                meta["tables"].append(exported)
                write_json(destination, meta)
            unchanged()
            check()
        con.close()
        con = None
        for p in (out / "source.duckdb", out / "source.duckdb.wal", out / "spill"):
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink(missing_ok=True)
        if queries is not None:
            write_json(out / "queries.json", dict(queries, dataset_id=meta["dataset_id"]))
        unchanged()
        check()
        meta["parquet_physical_bytes"] = sum(t["physical_bytes"] for t in meta["tables"])
        meta["status"] = "SUCCESS"
        write_json(destination, meta)
        campaign.mark(out, "SUCCESS")
        return out
    except BaseException as exc:
        meta.update(status="FAILED", error_type=type(exc).__name__)
        write_json(destination, meta)
        campaign.mark(out, "FAILED")
        raise
    finally:
        errors = []
        if con is not None:
            try:
                con.close()
            except Exception as exc:
                errors.append(type(exc).__name__)
        for p in (out / "source.duckdb", out / "source.duckdb.wal", out / "spill"):
            try:
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink(missing_ok=True)
            except Exception as exc:
                errors.append(type(exc).__name__)
        if errors:
            meta["cleanup_error_types"] = errors
            write_json(destination, meta)
