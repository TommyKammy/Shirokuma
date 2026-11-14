"""TPC-derived disk generation and streaming Parquet export; no in-memory dataset."""
import json
from pathlib import Path
import re
import shutil
import time

from .capacity import allocated, write_json
from .engine import bounded, connect, ident, load_extension, quote
from .protocol import LIMIT, settings


def generate(campaign, name, suite="tpch", **options):
    target_file_bytes = options.pop("target_file_bytes", None)
    row_group_size = options.pop("row_group_size", 122880)
    max_file_bytes = options.pop("max_file_bytes", 104857600000)
    from .sharding import controls, export_table
    if target_file_bytes is None:
        if type(row_group_size) is not int or row_group_size != 122880 or type(max_file_bytes) is not int or max_file_bytes != 104857600000:
            raise ValueError("sharding controls require target_file_bytes")
    else:
        controls(target_file_bytes, row_group_size, max_file_bytes, options.get("timeout") or 120)
    cfg = settings(**options)
    if suite not in ("tpch", "tpcds"):
        raise ValueError("unsupported suite")
    campaign.check(cfg["peak_bytes"])
    out = campaign.own(name, "dataset")
    meta = dict(format=1, status="INCOMPLETE", suite=suite, **cfg,
                generator="DuckDB core extension", generator_version="1.4.3",
                fixed_parameters={"sf": cfg["scale"], "query_bind_parameters": "extension fixed; no arbitrary bind seed API",
                                  "data_seed": "extension default; no caller seed supplied",
                                  "partition": "default single generation", "codec": "zstd", "row_group_size": 122880},
                generator_tool_version=("DuckDB bundled TPC-H" if suite == "tpch" else
                                        "older bundled TPC-DS; exact tool version not exposed; not version 4"),
                compliance=("TPC-derived; not audited TPC data or official scores" if suite == "tpch" else
                            "DuckDB 1.4.3 older TPC-DS tool; not TPC-DS 4 compliant or official scores"), tables=[], queries=[])
    meta["measured_logical_bytes"] = 0
    meta["logical_measurement"] = "UTF8 SQL text of each non-null value plus one delimiter per column and newline per row; distinct from nominal scale"
    meta["dataset_id"] = campaign.state["id"] + "/" + name
    if target_file_bytes is not None:
        meta["format"] = 2
        meta["fixed_parameters"]["row_group_size"] = row_group_size
        meta["sharding"] = dict(target_file_bytes=target_file_bytes, target_kind="soft",
                                row_group_size=row_group_size, max_file_bytes=max_file_bytes)
    manifest = out / "dataset.json"
    write_json(manifest, meta)
    con = None
    # Reserve the declared total peak before generation. During work, account
    # observed campaign growth plus the still-unallocated portion of this peak.
    start = allocated(out)

    def check():
        growth = max(0, allocated(out) - start)
        if growth > cfg["peak_bytes"]:
            raise RuntimeError("declared generation peak exceeded")
        campaign.check(max(0, cfg["peak_bytes"] - growth))

    try:
        con = connect(out / "source.duckdb", out / "spill", temp_bytes=cfg["peak_bytes"] // 3)
        with bounded(con, check, time.monotonic() + cfg["timeout_s"]):
            meta["extension"] = load_extension(con, suite, out / "extensions")
            meta["installed_api"] = [dict(name=r[0], parameters=r[1], parameter_types=[str(t) for t in r[2]])
                                     for r in con.execute("SELECT function_name, parameters, parameter_types FROM duckdb_functions() WHERE function_name IN (?, ?, ?)",
                                                          ["dbgen" if suite == "tpch" else "dsdgen", suite + "_queries", suite + "_answers"]).fetchall()]
            # Enumerate and preserve every SQL string, never silently drop variants.
            for nr, sql in con.execute("SELECT query_nr, query FROM " + suite + "_queries() ORDER BY query_nr").fetchall():
                statements = con.extract_statements(sql)
                from .runner import sql_identity
                meta["queries"].append({"id": str(nr), "sql": sql, "sql_sha256": sql_identity(sql), "ordered": False,
                                        "statement_count": len(statements)})
            expected = 22 if suite == "tpch" else 99
            if len(meta["queries"]) != expected or {q["id"] for q in meta["queries"]} != {str(i) for i in range(1,expected+1)}:
                raise RuntimeError("installed extension query enumeration differs")
            write_json(manifest, meta)
            generator = "dbgen" if suite == "tpch" else "dsdgen"
            con.execute(f"CALL {generator}(sf={cfg['scale']!r})")
            con.execute("CHECKPOINT")
            tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
            if len(tables) != (8 if suite == "tpch" else 24):
                raise ValueError("incomplete generated TPC table set")
            for table in tables:
                check()
                path = out / (table + ".parquet")
                schema = [{"name": r[0], "type": r[1]} for r in con.execute("DESCRIBE " + ident(table)).fetchall()]
                rows = con.execute("SELECT count(*) FROM " + ident(table)).fetchone()[0]
                fields = ["octet_length(encode(coalesce(cast(" + ident(c["name"]) + " AS VARCHAR),'')))" for c in schema]
                expr = " + ".join(fields + [str(len(schema) + 1)])
                logical = con.execute("SELECT coalesce(sum(" + expr + "),0) FROM " + ident(table)).fetchone()[0]
                meta["measured_logical_bytes"] += logical
                logical_limit = 1_000_000_000 if cfg["mode"] == "qualification" else LIMIT
                if meta["measured_logical_bytes"] > logical_limit:
                    raise RuntimeError("measured logical data exceeds mode ceiling")
                if target_file_bytes is not None:
                    exported = export_table(con, out, len(meta["tables"]), "SELECT * FROM " + ident(table),
                                            schema, rows, target_file_bytes=target_file_bytes,
                                            row_group_size=row_group_size, max_file_bytes=max_file_bytes, check=check)
                    exported.update(name=table, logical_bytes=logical)
                    meta["tables"].append(exported)
                else:
                    con.execute("COPY " + ident(table) + " TO " + quote(path) + " (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 122880)")
                    parquet = "SELECT * FROM read_parquet(" + quote(path) + ")"
                    exported_schema = [{"name": r[0], "type": r[1]} for r in con.execute("DESCRIBE " + parquet).fetchall()]
                    if exported_schema != schema:
                        raise ValueError("export schema mismatch")
                    source = "SELECT * FROM " + ident(table)
                    difference = "SELECT count(*) FROM ((" + source + " EXCEPT ALL " + parquet + ") UNION ALL (" + parquet + " EXCEPT ALL " + source + ")) differences"
                    if con.execute(difference).fetchone()[0] != 0:
                        raise ValueError("full Parquet multiset roundtrip mismatch")
                    meta["tables"].append(dict(name=table, rows=rows, schema=schema, logical_bytes=logical,
                                               file=path.name, physical_bytes=allocated(path), file_bytes=path.stat().st_size,
                                               roundtrip="full EXCEPT ALL both ways; same-engine, not independent"))
                write_json(manifest, meta)
                check()
            meta["parquet_physical_bytes"] = sum(t["physical_bytes"] for t in meta["tables"])
            write_json(out / "queries.json", {"format": 1, "dataset_id": meta["dataset_id"],
                                             "queries": meta["queries"]})
            check()
        con.close()
        con = None
        for path in (out / "source.duckdb", out / "source.duckdb.wal"):
            path.unlink(missing_ok=True)
        shutil.rmtree(out / "spill")
        meta["status"] = "SUCCESS"
        write_json(manifest, meta)
        campaign.mark(out, "SUCCESS")
        return out
    except BaseException as exc:
        meta.update(status="FAILED", error_type=type(exc).__name__)
        write_json(manifest, meta)
        campaign.mark(out, "FAILED")
        raise
    finally:
        # Best effort on failure, retaining the original exception and recording
        # cleanup trouble. These are exclusively this fresh output's temp paths.
        cleanup_errors = []
        if con is not None:
            try:
                con.close()
            except Exception as exc:
                cleanup_errors.append(type(exc).__name__)
        for path in (out / "source.duckdb", out / "source.duckdb.wal", out / "spill"):
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
            except Exception as exc:
                cleanup_errors.append(type(exc).__name__)
        if cleanup_errors:
            meta["cleanup_error_types"] = cleanup_errors
            write_json(manifest, meta)


def prepare_reference(campaign, dataset, name):
    """All rows of all queries against exported Parquet in an isolated engine.

    Same-engine roundtrip preparation, explicitly not an independent oracle.
    """
    dataset = Path(dataset)
    meta = json.loads((dataset / "dataset.json").read_text())
    if meta["status"] != "SUCCESS":
        raise ValueError("incomplete dataset")
    from .runner import DuckDBAdapter, run
    manifest = json.loads((dataset / "queries.json").read_text())
    adapter = DuckDBAdapter(dataset)
    return run(campaign, name, manifest, adapter, mode=meta["mode"], timeout=meta["timeout_s"],
               peak=meta["peak_bytes"], peak_evidence=meta.get("peak_evidence"), scale=meta["scale"], repetitions=1,
               reference_label="same-engine Parquet roundtrip; not independent")


def packaged_column_binding(labels, columns):
    """Bind canonical known-answer labels; never normalize ordinary result schemas.

    A bare aggregate label may name the same unaliased aggregate call at that
    position. All other labels and the number/order of columns remain exact.
    """
    if len(labels) != len(columns):
        raise ValueError("packaged answer column mismatch")
    binding = []
    for position, (label, col) in enumerate(zip(labels, columns)):
        name = col["name"]
        if label != name:
            aggregate = re.fullmatch(r"sum|avg|min|max|count", label, re.I)
            call = re.match(r"(sum|avg|min|max|count)\(", name, re.I)
            if not aggregate or not call or label.lower() != call[1].lower() or not name.endswith(")"):
                raise ValueError("packaged answer column mismatch")
            # Require one whole call, not e.g. sum(x) + max(y). Quoted
            # parentheses are data within an expression, not call boundaries.
            depth = 1
            for token in re.finditer(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|[()]", name[call.end():]):
                if token[0] in ("(", ")"):
                    depth += 1 if token[0] == "(" else -1
                    if depth == 0 and call.end() + token.end() != len(name):
                        raise ValueError("packaged answer column mismatch")
            if depth != 0 or not name[call.end():-1].strip():
                raise ValueError("packaged answer column mismatch")
        binding.append(dict(position=position, packaged=label, result=name))
    return binding


def qualify_packaged_tpch(campaign, dataset, reference, name, abs_tol=1e-8, rel_tol=1e-8):
    """Compare 22 results to packaged precomputed SF answers, not regenerated SQL.

    Uses result schemas only to parse the extension's pipe-delimited text values.
    Decimal values stay exact; floating values have explicit printed-answer tolerance.
    This is a known-answer check, not an independent cross-engine oracle.
    """
    from decimal import Decimal
    from .results import Reader, Writer, compare
    from .runner import result_metadata, sql_identity
    dataset, reference = Path(dataset), Path(reference)
    meta = json.loads((dataset / "dataset.json").read_text())
    if meta["status"] != "SUCCESS" or meta["suite"] != "tpch" or meta["scale"] not in (.01, .1, 1):
        raise ValueError("packaged TPC-H answers require successful SF0.01/0.1/1 dataset")
    manifest = json.loads((dataset / "queries.json").read_text())
    if len(manifest["queries"]) != 22:
        raise ValueError("all 22 TPC-H queries required")
    out = campaign.own(name, "known-answer")
    record = dict(status="INCOMPLETE", source="DuckDB 1.4.3 tpch_answers packaged precomputed answers",
                  independent_cross_engine=False, scale=meta["scale"], abs_tol=abs_tol, rel_tol=rel_tol, queries=[])
    con = None
    write_json(out / "qualification.json", record)
    deadline = time.monotonic() + meta["timeout_s"]
    def check():
        if time.monotonic() >= deadline:
            raise TimeoutError("known-answer validation deadline")
        campaign.check()
    try:
        con = connect(out / "answers.duckdb", out / "spill")
        with bounded(con, check, deadline):
            record["extension"] = load_extension(con, "tpch", out / "extensions")
            canonical_sql = {str(n): sql_identity(sql) for n, sql in con.execute(
                "SELECT query_nr, query FROM tpch_queries()").fetchall()}
            if ({q["id"]: sql_identity(q["sql"]) for q in manifest["queries"]} != canonical_sql
                    or len(canonical_sql) != 22):
                raise ValueError("packaged answers require canonical extension SQL for all 22 queries")
            answers = dict(con.execute("SELECT query_nr, answer FROM tpch_answers() WHERE scale_factor=?", [meta["scale"]]).fetchall())
            if set(answers) != set(range(1, 23)):
                raise ValueError("packaged answers incomplete")
            for q in manifest["queries"]:
                actual = reference / (q["id"] + "-0") / "result.jsonl"
                reader = Reader(actual)
                header = reader.header
                reader.f.close()
                if q.get("sql_sha256", sql_identity(q["sql"])) != sql_identity(q["sql"]):
                    raise ValueError("SQL fingerprint mismatch")
                if header["metadata"] != result_metadata(manifest, q):
                    raise ValueError("reference SQL/dataset identity mismatch")
                lines = answers[int(q["id"])].splitlines()
                binding = packaged_column_binding(lines[0].split("|"), header["columns"])
                target = out / (q["id"] + ".jsonl")
                writer = Writer(target, header["columns"], header["metadata"])
                try:
                    for line in lines[1:]:
                        values = line.split("|")
                        if len(values) != len(header["columns"]):
                            raise ValueError("unsupported packaged answer encoding")
                        row = []
                        for value, c in zip(values, header["columns"]):
                            numeric = ("int" in c["type"] or c["type"].startswith("decimal")
                                       or c["type"] in ("double", "real"))
                            # Packaged numeric NULL is an empty field, including
                            # a blank scalar row. Empty VARCHAR remains a value.
                            if value == "NULL" or (value == "" and numeric):
                                value = None
                            elif "int" in c["type"]:
                                number = Decimal(value)
                                if number != number.to_integral_value():
                                    raise ValueError("nonintegral packaged integer")
                                value = int(number)
                            elif c["type"].startswith("decimal"):
                                value = Decimal(value)
                            row.append(value)
                        writer.row(row)
                    writer.finish()
                finally:
                    writer.close()
                compare(target, actual, ordered=q.get("ordered", False), abs_tol=abs_tol,
                        rel_tol=rel_tol, scratch=out, check=check)
                record["queries"].append(dict(id=q["id"], status="SUCCESS", column_binding=binding))
                write_json(out / "qualification.json", record)
        record["status"] = "SUCCESS"
        campaign.mark(out, "SUCCESS")
        return out
    except BaseException as exc:
        record.update(status="FAILED", error_type=type(exc).__name__)
        campaign.mark(out, "FAILED")
        raise
    finally:
        if con is not None:
            con.close()
        for path in (out / "answers.duckdb", out / "answers.duckdb.wal", out / "spill"):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        write_json(out / "qualification.json", record)


def prepare_sql_reference(campaign, manifest, name, **options):
    """Freeze arbitrary portable SELECT SQL and full values without registered data."""
    from .runner import DuckDBAdapter, run
    return run(campaign, name, manifest, DuckDBAdapter(), repetitions=1,
               reference_label="SQL-only reference", **options)
