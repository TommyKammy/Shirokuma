"""Bounded DuckDB 1.4.3 TPC-DS 99-string qualification (not official 103/4)."""
import copy
import json
import math
from pathlib import Path
import re
import shutil
import time

from .capacity import write_json
from .engine import bounded, connect, ident, quote
from .results import cell, column, compare, order_values, validate_order, validate_order_contract
from .tpch_sql import digest, _stream, _safe

TABLES = ("call_center", "catalog_page", "catalog_returns", "catalog_sales", "customer",
          "customer_address", "customer_demographics", "date_dim", "household_demographics",
          "income_band", "inventory", "item", "promotion", "reason", "ship_mode", "store",
          "store_returns", "store_sales", "time_dim", "warehouse", "web_page", "web_returns",
          "web_sales", "web_site")
SUITE = "DuckDB 1.4.3 tpcds_queries: 99 single statements; not official 103 or TPC-DS 4"


def contracts():
    document = json.loads(Path(__file__).with_name("tpcds_contracts.json").read_text())
    entries = document["queries"]
    if (type(document.get("format")) is not int or document.get("format") != 1 or document.get("suite") != SUITE
            or len(entries) != 99 or {q["id"] for q in entries} != {str(i) for i in range(1, 100)}):
        raise ValueError("all 99 bounded contracts required")
    for q in entries:
        if (not q["columns"] or any(column(c["name"], c["type"]) != c or c["type"] == "hugeint"
                                   for c in q["columns"])
                or len({c["name"] for c in q["columns"]}) != len(q["columns"])):
            raise ValueError("invalid output contract")
        validate_order_contract(q["columns"], q["order_by"])
        if (not re.fullmatch(r"[0-9a-f]{64}", q["source_sql_sha256"])
                or digest(projection(q["portable_body"], q)) != q["portable_sql_sha256"]):
            raise ValueError("contract SQL fingerprint mismatch")
        if q["limit"] is not None and (type(q["limit"]) is not int or q["limit"] <= 0):
            raise ValueError("invalid limit contract")
    schemas = document.get("table_schemas")
    if (not isinstance(schemas, dict) or set(schemas) != set(TABLES)
            or any(not cols or len({c["name"] for c in cols}) != len(cols)
                   or any(column(c["name"], c["type"]) != c for c in cols) for cols in schemas.values())):
        raise ValueError("invalid input schema contract")
    return {q["id"]: q for q in entries}


def table_schemas():
    contracts()
    return json.loads(Path(__file__).with_name("tpcds_contracts.json").read_text())["table_schemas"]


def projection(sql, rule):
    aliases = ", ".join("c" + str(i) for i in range(len(rule["columns"])))
    fields = ", ".join(f"CAST(c{i} AS {c['type']}) AS {ident(c['name'])}"
                       for i, c in enumerate(rule["columns"]))
    result = f"SELECT {fields} FROM ({sql.strip().rstrip(';')}) AS result({aliases})"
    if rule["order_by"]:
        result += " ORDER BY " + ", ".join(order_expression(rule, o) + (" DESC" if o["descending"] else " ASC")
            + " NULLS " + o["nulls"].upper() for o in rule["order_by"])
    return result


def order_expression(rule, key):
    if "index" in key:
        return str(key["index"] + 1)
    expr = key["expression"]

    def operand(key):
        return ident(rule["columns"][expr[key]]["name"])

    if expr["op"] == "subtract":
        return "(CAST(" + operand("left") + " AS DOUBLE) - CAST(" + operand("right") + " AS DOUBLE))"
    return "CASE WHEN " + operand("condition") + " = " + str(expr["value"]) + " THEN " + operand("then") + " END"


def validate_query_order(path, rule, check=lambda: None):
    """Compatibility wrapper around the shared projected-column validator."""
    return validate_order(path, rule["order_by"], check)


def portable(sql, rule):
    if digest(sql) != rule["source_sql_sha256"]:
        raise ValueError("unexpected canonical source SQL")
    rendered = projection(rule["portable_body"], rule)
    if digest(rendered) != rule["portable_sql_sha256"]:
        raise ValueError("portable SQL fingerprint mismatch")
    return rendered


def logical_manifest(source):
    rules = contracts()
    queries = source["queries"]
    if (type(source.get("format")) is not int or source.get("format") != 1 or not isinstance(source.get("dataset_id"), str)
            or not source["dataset_id"] or len(queries) != 99 or {q["id"] for q in queries} != set(rules)):
        raise ValueError("complete 99-query source manifest required")
    result = dict(format=1, suite=SUITE, dataset_id=source["dataset_id"],
                  source_dataset_id=source["dataset_id"], table_schemas=table_schemas(), queries=[])
    for q in queries:
        rule = rules[q["id"]]
        if q.get("sql_sha256") != digest(q["sql"]):
            raise ValueError("source SQL fingerprint mismatch")
        sql = portable(q["sql"], rule)
        result["queries"].append(dict(id=q["id"], source_sql=q["sql"], source_sha256=digest(q["sql"]),
            logical_sql=sql, logical_sha256=digest(sql), reference_sql=sql, reference_sha256=digest(sql),
            sql=sql, sql_sha256=digest(sql), ordered=False, columns=copy.deepcopy(rule["columns"]),
            order_by=copy.deepcopy(rule["order_by"]), limit=rule["limit"],
            rewrite_provenance=copy.deepcopy(rule["rewrite_provenance"])))
    result["source_hashes"] = {q["id"]: q["source_sha256"] for q in result["queries"]}
    return result


class UnsupportedBoundaryTie(ValueError):
    """Qualification cannot choose an arbitrary subset of boundary peers."""
    def __init__(self, audit):
        super().__init__("unsupported LIMIT boundary tie: dataset requires explicit requalification")
        self.audit = audit


def audit_limit(con, sql, rule, check=lambda: None):
    """Prove a candidate boundary group is interchangeable by scanning all peers.

    The N+1 probe only detects a candidate. No ORDER means every row is a peer.
    Full projected typed rows must match exactly, including floating encodings.
    """
    n = rule["limit"]
    if n is None:
        return None
    columns, keys = rule["columns"], rule["order_by"]
    validate_order_contract(columns, keys)
    # LIMIT is top-level in all pinned strings. Never rewrite a nested/scalar LIMIT.
    pattern = r"(?i)\bLIMIT\s+" + str(n) + r"\s*;?\s*$"
    expanded, count = re.subn(pattern, "LIMIT " + str(n + 1), sql)
    if count != 1:
        raise ValueError("top-level LIMIT audit rewrite mismatch")

    def typed(row):
        if len(row) != len(columns):
            raise ValueError("LIMIT audit projection width mismatch")
        return [cell(v, c["type"]) for v, c in zip(row, columns)]

    check()
    cursor = con.execute(expanded)
    rows = []
    while len(rows) < n + 1:
        batch = cursor.fetchmany(min(256, n + 1 - len(rows)))
        if not batch:
            break
        rows.extend(typed(row) for row in batch)
        check()
    boundary = rows[n - 1] if len(rows) >= n else None
    boundary_keys = order_values(boundary, columns, keys) if boundary is not None else None
    tie = len(rows) > n and boundary_keys == order_values(rows[n], columns, keys)
    audit = dict(limit=n, observed_rows=len(rows), boundary_tie=tie, interchangeable=False,
                 input_sql_sha256=digest(sql), executed_sql_sha256=digest(expanded),
                 output_sql_sha256=digest(expanded), full_scan=False)
    if tie:
        unlimited = re.sub(pattern, "", sql)
        audit.update(full_scan=True, unlimited_sql_sha256=digest(unlimited),
                     output_sql_sha256=digest(unlimited), total_rows=0, boundary_multiplicity=0)
        check()
        cursor = con.execute(unlimited)
        conflicting = False
        while True:
            check()
            batch = cursor.fetchmany(256)
            if not batch:
                break
            for raw in batch:
                row = typed(raw)
                audit["total_rows"] += 1
                if order_values(row, columns, keys) == boundary_keys:
                    audit["boundary_multiplicity"] += 1
                    if row != boundary:
                        conflicting = True
            check()
        if conflicting:
            raise UnsupportedBoundaryTie(audit)
        audit["interchangeable"] = True
    check()
    return audit


def prepare_tpcds(campaign, dataset, name, timeout=120, abs_tol=1e-8, rel_tol=1e-8):
    """Full source/cast/portable parity; same engine, not an independent oracle."""
    if any(not math.isfinite(v) or v < 0 for v in (abs_tol, rel_tol)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("invalid deadline/tolerance")
    dataset = Path(dataset)
    meta = json.loads((dataset / "dataset.json").read_text())
    source_text = (dataset / "queries.json").read_text()
    source = json.loads(source_text)
    if (meta["status"] != "SUCCESS" or meta["suite"] != "tpcds" or meta["generator_version"] != "1.4.3"
            or meta["dataset_id"] != source["dataset_id"] or len(meta["tables"]) != 24
            or {t["name"] for t in meta["tables"]} != set(TABLES)):
        raise ValueError("successful DuckDB 1.4.3 24-table TPC-DS dataset required")
    manifest = logical_manifest(source)
    manifest["source_manifest_sha256"] = digest(source_text)
    if manifest["table_schemas"] != {t["name"]: [column(c["name"], c["type"]) for c in t["schema"]]
                                      for t in meta["tables"]}:
        raise ValueError("dataset input schema contract mismatch")
    rules = contracts()
    campaign.check()
    out = campaign.own(name, "tpcds-plan")
    record = dict(format=1, suite=SUITE, status="INCOMPLETE", source_dataset_id=source["dataset_id"],
                  source_manifest_sha256=digest(source_text), abs_tol=abs_tol, rel_tol=rel_tol,
                  equivalence="same-engine full multiset source/cast/portable; independent engine required", queries=[])
    write_json(out / "plan.json", record)
    write_json(out / "queries.json", manifest)
    deadline = time.monotonic() + timeout
    con = None

    def check():
        if time.monotonic() >= deadline:
            raise TimeoutError("TPC-DS plan deadline")
        campaign.check()

    try:
        con = connect(out / "parity.duckdb", out / "spill")
        with bounded(con, check, deadline):
            for t in meta["tables"]:
                file = (dataset / t["file"]).resolve()
                if file.parent != dataset.resolve():
                    raise ValueError("dataset table must be a direct child")
                con.execute("CREATE VIEW " + ident(t["name"]) + " AS SELECT * FROM read_parquet(" + quote(file) + ")")
                actual = [column(d[0], str(d[1])) for d in con.execute("SELECT * FROM " + ident(t["name"]) + " LIMIT 0").description]
                if actual != manifest["table_schemas"][t["name"]]:
                    raise ValueError("dataset table schema mismatch")
            for q in manifest["queries"]:
                check()
                rule = rules[q["id"]]
                pair = dict(source_hash=q["source_sha256"], portable_hash=q["logical_sha256"],
                            source_dataset_id=source["dataset_id"])
                audit = dict(id=q["id"], status="INCOMPLETE", equivalence_contract=pair)
                record["queries"].append(audit)
                variants = {"source": q["source_sql"], "source_cast": projection(q["source_sql"], rule), "portable": q["sql"]}
                for label, sql in variants.items():
                    path = out / (q["id"] + "-" + label + ".jsonl")
                    audit[label] = _stream(con, sql, path, q["columns"], pair, check)
                    validate_query_order(path, rule, check)
                for label in ("source_cast", "portable"):
                    compare(out / audit["source"]["file"], out / audit[label]["file"], ordered=False,
                            abs_tol=abs_tol, rel_tol=rel_tol, scratch=out, check=check)
                audit.update(rows=audit["source"]["rows"], nonempty=audit["source"]["rows"] > 0)
                # Audit both bodies, including when source already reports a tie.
                ties = []
                for label, sql in (("limit_audit", q["source_sql"]), ("portable_limit_audit", rule["portable_body"])):
                    try:
                        audit[label] = audit_limit(con, sql, rule, check)
                    except UnsupportedBoundaryTie as exc:
                        audit[label] = exc.audit
                        ties.append(exc)
                if ties:
                    raise ties[0]
                audit["status"] = "SUCCESS"
                write_json(out / "plan.json", record)
        record.update(status="SUCCESS", query_count=len(record["queries"]),
                      nonempty_queries=sum(q["nonempty"] for q in record["queries"]),
                      result_rows=sum(q["rows"] for q in record["queries"]))
        campaign.mark(out, "SUCCESS")
        return out
    except BaseException as exc:
        record.update(status="FAILED", error_type=type(exc).__name__, error=str(exc))
        if record["queries"]:
            record["queries"][-1].update(status="FAILED", error_type=type(exc).__name__, error=str(exc))
            record["failed_query_id"] = record["queries"][-1]["id"]
        campaign.mark(out, "FAILED")
        raise
    finally:
        if con is not None:
            con.close()
        for path in (out / "parity.duckdb", out / "parity.duckdb.wal", out / "spill"):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        record.update(query_count=len(record["queries"]),
                      nonempty_queries=sum(q.get("nonempty", False) for q in record["queries"]),
                      result_rows=sum(q.get("rows", 0) for q in record["queries"]))
        write_json(out / "plan.json", record)


def bind_snapshots(manifest, pins):
    """Bind all 24 relations through an outer CTE scope; preserve scalar names."""
    from .public import context
    if pins.get("status") != "SUCCESS":
        raise ValueError("successful snapshot freeze required")
    ctx = context(pins["context"])
    catalog, schema = _safe(ctx["sql_catalog"]), _safe(ctx["sql_schema"])
    tables = pins["tables"]
    if (type(pins.get("format")) is not int or pins.get("format") != 1 or pins.get("dataset_id") != manifest.get("source_dataset_id")
            or len(tables) != 24 or {t["name"] for t in tables} != set(TABLES)):
        raise ValueError("exact source dataset and 24 snapshot tables required")
    if manifest.get("context") is not None and manifest["context"] != ctx:
        raise ValueError("snapshot context mismatch")
    expected = logical_manifest(dict(format=1, dataset_id=manifest["source_dataset_id"], queries=[
        dict(id=q["id"], sql=q["source_sql"], sql_sha256=q["source_sha256"]) for q in manifest["queries"]]))
    if (type(manifest.get("format")) is not int or manifest.get("format") != 1
            or manifest.get("dataset_id") != manifest["source_dataset_id"]
            or manifest.get("source_hashes") != expected["source_hashes"] or manifest.get("suite") != SUITE):
        raise ValueError("source hash set/suite mismatch")
    for q, e in zip(manifest["queries"], expected["queries"]):
        for key in ("logical_sql", "logical_sha256", "columns", "order_by", "limit", "ordered", "rewrite_provenance"):
            if q[key] != e[key]:
                raise ValueError("logical/source SQL contract mismatch")
        if q["reference_sql"] != e["logical_sql"] or q["reference_sha256"] != e["logical_sha256"]:
            raise ValueError("reference SQL fingerprint mismatch")
        if q["sql_sha256"] != digest(q["sql"]) or q["sql"] != e["logical_sql"]:
            raise ValueError("target SQL fingerprint mismatch")
    schemas = manifest.get("table_schemas")
    if schemas != expected["table_schemas"]:
        raise ValueError("exact 24 table schemas required")
    ctes, physical = [], set()
    for t in sorted(tables, key=lambda t: t["name"]):
        name = _safe(t["physical_name"])
        if name in physical or name in TABLES:
            raise ValueError("unique physical prefix table names required")
        physical.add(name)
        sid = t["snapshot_id"]
        if not isinstance(sid, str) or not re.fullmatch(r"[0-9]+", sid):
            raise ValueError("ASCII decimal snapshot ID required")
        ref = t["ref"]
        if (ref.get("database_id") != ctx["database_id"] or ref.get("schema_id") != ctx["schema_id"]
                or not isinstance(t["table_uuid"], str) or not t["table_uuid"]):
            raise ValueError("snapshot database/schema identity mismatch")
        for key, value in (("sql_catalog", catalog), ("sql_schema", schema)):
            if key in ref and ref[key] != value:
                raise ValueError("snapshot SQL context mismatch")
        aliases = {"long": "bigint", "string": "varchar"}
        for key in ("columns", "source_columns", "current_columns"):
            if key in t:
                actual = [column(c["name"], aliases.get(c["type"], c["type"])) for c in t[key]]
                if actual != schemas[t["name"]]:
                    raise ValueError("snapshot schema mismatch")
        if "columns" not in t:
            raise ValueError("snapshot schema required")
        ctes.append(ident(t["name"]) + " AS (SELECT * FROM " + ".".join(map(ident, (catalog, schema, name)))
                    + " FOR VERSION AS OF " + sid + ")")
    stable = copy.deepcopy(pins)
    stable["tables"] = sorted(stable["tables"], key=lambda t: t["name"])
    snapshot_hash = digest(json.dumps(stable, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False))
    result = copy.deepcopy(manifest)
    result.update(context=ctx, snapshot_set_sha256=snapshot_hash, snapshot_pins=stable,
                  dataset_id=manifest["source_dataset_id"] + "/snapshot/" + snapshot_hash)
    for q in result["queries"]:
        q["sql"] = "WITH " + ",\n".join(ctes) + "\nSELECT * FROM (" + q["logical_sql"] + ") AS snapshot_result"
        # Ordering must be on the outermost SELECT as well.
        if q["order_by"]:
            q["sql"] += " ORDER BY " + ", ".join(order_expression(q, o) + (" DESC" if o["descending"] else " ASC")
                + " NULLS " + o["nulls"].upper() for o in q["order_by"])
        q["sql_sha256"] = q["target_sha256"] = digest(q["sql"])
    return result


def normalize_date_integer_arithmetic(tree):
    """Normalize annotated DATE day arithmetic in the offline generator only."""
    from sqlglot import exp

    integer_types = {exp.DType.TINYINT, exp.DType.SMALLINT, exp.DType.INT, exp.DType.BIGINT,
                     exp.DType.UTINYINT, exp.DType.USMALLINT, exp.DType.UINT, exp.DType.UBIGINT}
    changed = 0
    # Children first so nested day arithmetic retains its annotated DATE type.
    for node in reversed(list(tree.walk())):
        if not isinstance(node, (exp.Add, exp.Sub)):
            continue
        date, days = node.left, node.right
        if isinstance(node, exp.Add) and days.type and days.type.this == exp.DType.DATE:
            date, days = days, date
        if not (date.type and date.type.this == exp.DType.DATE
                and days.type and days.type.this in integer_types):
            continue
        signed = days.copy()
        if isinstance(node, exp.Sub):
            signed = exp.Neg(this=exp.Paren(this=signed))
        interval = exp.Mul(this=exp.Paren(this=signed), expression=exp.Interval(
            this=exp.Literal.string("1"), unit=exp.Var(this="DAY")))
        replacement = exp.Cast(this=exp.Add(this=date.copy(), expression=interval),
                               to=exp.DataType.build("DATE"))
        replacement.type = exp.DataType.build("DATE")
        if node is tree:
            tree = replacement
        else:
            node.replace(replacement)
        changed += 1
    return tree, changed


def generate_contracts(inventory, table_schemas):
    """Offline regeneration only: SQLGlot 30.31.0, DuckDB 1.4.3 descriptions.

    inventory is tpcds_queries SQL plus native DESCRIBE rows; table_schemas is
    {table: {column: SQL type}}. No datasets or results are embedded in contracts.
    Unmapped ORDER expressions fail with the query ID for explicit review.
    """
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.qualify import qualify
    from sqlglot.optimizer.annotate_types import annotate_types
    if sqlglot.__version__ != "30.31.0":
        raise RuntimeError("regeneration requires isolated SQLGlot 30.31.0")
    entries = []
    for q in inventory:
        trees = sqlglot.parse(q["sql"], read="duckdb")
        if len(trees) != 1 or not isinstance(trees[0], exp.Query):
            raise ValueError("single query required: " + q["id"])
        original = trees[0]
        tree = qualify(original.copy(), dialect="duckdb", schema=table_schemas,
                       quote_identifiers=False, identify=False)
        annotate_types(tree, schema=table_schemas, dialect="duckdb")
        tree, date_arithmetic_count = normalize_date_integer_arithmetic(tree)
        selected = original
        while isinstance(selected, exp.SetOperation):
            selected = selected.this
        # Qualified projection expands stars, but native DuckDB names remain canonical.
        fields = selected.expressions
        columns = [column("c" + str(i) if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", d[0]) else d[0],
                          "bigint" if d[1].lower() == "hugeint" else d[1])
                   for i, d in enumerate(q["description"])]
        # Stable unique names even when the source emits duplicate labels.
        used = set()
        for i, c in enumerate(columns):
            if c["name"] in used:
                c["name"] = "column_" + str(i)
            used.add(c["name"])
        order = []
        manual = []
        # Reviewed overrides: preserve these computed source ORDER keys exactly.
        overrides = {
            "36": dict(op="case_equal", condition=3, value=0, then=1),
            "70": dict(op="case_equal", condition=3, value=0, then=1),
            "86": dict(op="case_equal", condition=3, value=0, then=1),
            "47": dict(op="subtract", left=7, right=6),
            "57": dict(op="subtract", left=6, right=5),
            "89": dict(op="subtract", left=6, right=7),
        }
        for o in original.args.get("order", exp.Order()).expressions:
            e = o.this
            while isinstance(e, exp.Paren):
                e = e.this
            matches = [i for i, v in enumerate(fields) if e == v or e == v.unalias()
                       or (isinstance(e, exp.Column) and e.name.lower() == v.alias_or_name.lower())]
            if isinstance(e, exp.Literal) and e.is_int:
                matches = [int(e.this) - 1]
            if not matches and isinstance(e, exp.Column):
                matches = [i for i, d in enumerate(q["description"]) if d[0].lower() == e.name.lower()]
            if not matches:
                spec = overrides.get(q["id"])
                if spec is None or manual:
                    raise ValueError("manual ORDER override required: " + q["id"] + " " + e.sql())
                # Guard each reviewed expression against silently changed source SQL.
                if spec["op"] == "subtract":
                    if not isinstance(e, exp.Sub) or e.left.name != "sum_sales" or e.right.name != "avg_monthly_sales":
                        raise ValueError("unexpected subtraction override")
                elif not isinstance(e, exp.Case) or "= 0" not in e.sql():
                    raise ValueError("unexpected CASE override")
                manual.append(dict(source_expression=e.sql(dialect="duckdb"), expression=spec))
                key = dict(expression=spec)
            else:
                key = dict(index=matches[0])
            key.update(descending=bool(o.args.get("desc")), nulls="first" if o.args.get("nulls_first") else "last")
            if key not in order:
                order.append(key)
        # Trino does not implicitly compare DATE columns with VARCHAR literals.
        for literal in list(tree.find_all(exp.Literal)):
            if literal.is_string and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", literal.this):
                parent = literal.parent
                peers = list(parent.iter_expressions()) if parent else []
                if any(v is not literal and v.type and v.type.this == exp.DType.DATE for v in peers):
                    literal.replace(exp.Cast(this=literal.copy(), to=exp.DataType.build("DATE")))
        # Normalize before rendering: DuckDB AVG and / are DOUBLE even on exact inputs.
        for a in list(tree.find_all(exp.Avg)):
            a.set("this", exp.Cast(this=a.this.copy(), to=exp.DataType.build("DOUBLE")))
        for d in list(tree.find_all(exp.Div)):
            d.set("this", exp.Cast(this=d.this.copy(), to=exp.DataType.build("DOUBLE")))
            d.set("expression", exp.Cast(this=d.expression.copy(), to=exp.DataType.build("DOUBLE")))
        integer_types = {exp.DType.TINYINT, exp.DType.SMALLINT, exp.DType.INT, exp.DType.BIGINT,
                         exp.DType.UTINYINT, exp.DType.USMALLINT, exp.DType.UINT, exp.DType.UBIGINT}
        for s in list(tree.find_all(exp.Sum)):
            if s.this.type and s.this.type.this in integer_types:
                s.replace(exp.Cast(this=s.copy(), to=exp.DataType.build("BIGINT")))
        body = tree.sql(dialect="trino", pretty=True)
        rule = dict(id=q["id"], source_sql_sha256=digest(q["sql"]), columns=columns,
                    order_by=order, limit=int(original.args["limit"].expression.this) if original.args.get("limit") else None,
                    portable_body=body, rewrite_provenance=dict(
                        source="DuckDB 1.4.3 tpcds_queries", generator="SQLGlot 30.31.0",
                        method="generate_contracts(inventory, table_schemas)",
                        ordering="shared projected-column indices and expressions; validate_order",
                        transformations=["DuckDB parse; qualify and expand aliases/stars with input schemas",
                            "AVG inputs and both division operands explicitly DOUBLE",
                            "integer SUM explicitly BIGINT; signed64 validated on raw source results",
                            "DATE literals explicitly typed where compared to DATE expressions",
                            "Trino render; exact output ordinal casts; explicit outer ORDER/nulls"],
                        manual_order_overrides=manual))
        if date_arithmetic_count:
            rule["rewrite_provenance"]["transformations"].append(
                "DATE +/- integer days via CAST(DATE + signed integer * INTERVAL '1' DAY AS DATE)")
        rule["portable_sql_sha256"] = digest(projection(body, rule))
        entries.append(rule)
    return dict(format=1, suite=SUITE,
                table_schemas={t: [column(n, typ) for n, typ in cols.items()] for t, cols in table_schemas.items()},
                queries=entries)
