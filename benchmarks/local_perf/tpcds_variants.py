"""Opt-in private-source supplemental iterations; no SQL or downloader bundled.

source_dir must contain exactly query14b.sql, query23a.sql, query24b.sql,
query39b.sql as direct children (no other SQL files, including subdirectories).
Only preparation imports the dedicated optional SDKs. Binding uses static pins.
"""
import copy
import hashlib
import json
import math
from pathlib import Path
import time

from . import tpcds_sql as ds
from .results import column, validate_order_contract

SUITE = "TPC-derived supplemental iterations: Flink 2.1.0 pinned 4; selected mixed corpus, not official 103"
IDS = ("14b", "23a", "24b", "39b")
ALTERNATIVES = (5, 10, 14, 18, 22, 27, 35, 36, 51, 67, 70, 77, 80, 86)


def metadata():
    doc = json.loads(Path(__file__).with_name("tpcds_variants_metadata.json").read_text())
    if doc["suite"] != SUITE or doc["format"] != 1 or len(doc["queries"]) != 4 or {q["id"] for q in doc["queries"]} != set(IDS):
        raise ValueError("exact supplemental contracts required")
    if doc["table_schemas"] != ds.table_schemas():
        raise ValueError("supplemental input schemas mismatch")
    for q in doc["queries"]:
        validate_order_contract(q["columns"], q["order_by"])
        if len({c["name"] for c in q["columns"]}) != len(q["columns"]):
            raise ValueError("unique supplemental outputs required")
    return doc


def coverage():
    """Inventory denominators, never an official score or all-variant assertion."""
    return dict(selected_execution_cases=103, official_tpc_score=False, all_variants_complete=False,
        baseline=dict(count=99, suite=ds.SUITE, status="fixed contracts", generator_version="1.4.3",
                      iterations={"14": 1, "23": 2, "24": 1, "39": 1}),
        supplement=dict(count=4, ids=list(IDS), suite=SUITE, status="opt-in source/dataset qualification required",
                        provenance=metadata()["provenance"]),
        approved_alternative_templates=dict(count=14, status="pending separate source/dialect/equivalence acceptance",
            specification="https://www.tpc.org/TPC_Documents_Current_Versions/pdf/TPC-DS_v4.0.0.pdf",
            distinction="Appendix C a.tpl alternatives are distinct from multi-iteration a/b statements",
            candidates=[dict(id=str(n)+"a", url="https://github.com/apache/spark/blob/v4.0.1/sql/core/src/test/resources/tpcds-v2.7.0/q"+str(n)+"a.sql",
                status="pending", reason="Spark parser exception; dialect adaptation unaccepted" if n == 77 else
                "DuckDB parse observed; source provenance and equivalence unaccepted") for n in ALTERNATIVES]))


def read_sources(source_dir, check=lambda: None):
    check()
    root = Path(source_dir).resolve()
    expected = {"query" + i + ".sql" for i in IDS}
    found = list(root.rglob("*")) if root.is_dir() else []
    sql = [p for p in found if p.is_file() and p.suffix.lower() == ".sql"]
    if len(sql) != 4 or {p.name for p in sql} != expected or any(p.parent != root for p in sql):
        raise ValueError("source-dir requires exactly four direct query{14b,23a,24b,39b}.sql files")
    rules = {q["id"]: q for q in metadata()["queries"]}
    result = {}
    for i in IDS:
        check()
        raw = (root / ("query" + i + ".sql")).read_bytes()
        check()
        text = raw.decode("utf8")
        # digest(text) hashes UTF-8; read_bytes preserves line endings.
        if ds.digest(text) != rules[i]["source_sql_sha256"]:
            raise ValueError("pinned source bytes mismatch: " + i)
        result[i] = text
    return result


def source_normalization(source, body, version):
    """Hash-only lineage for wrapper/audit input; raw source executes separately."""
    return dict(raw_source_sha256=ds.digest(source), normalized_source_body_sha256=ds.digest(body),
                parser="SQLGlot", version=version, read_dialect="duckdb", write_dialect="duckdb",
                comments=False, transformation="parse_one(raw source, read=duckdb).sql(dialect=duckdb, comments=False)",
                uses=["source_cast ordinal wrapper", "source LIMIT audit"], raw_source_execution="exact pinned bytes")


def _entry(i, source, portable, rule):
    return dict(id=i, source_sql=source, source_sha256=ds.digest(source),
        logical_sql=portable, logical_sha256=ds.digest(portable), reference_sql=portable,
        reference_sha256=ds.digest(portable), sql=portable, sql_sha256=ds.digest(portable),
        ordered=False, **{k: copy.deepcopy(rule[k]) for k in ("columns", "order_by", "limit", "rewrite_provenance", "source_normalization")})


def _dataset_identity(dataset, meta, version, check):
    from .dataset_layout import table_files
    result = {}
    for table in meta["tables"]:
        for f in table_files(dataset, table, version, check):
            check()
            h = hashlib.sha256()
            with f["path"].open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(chunk)
                    check()
            check()
            if f["path"].stat().st_size != f["file_bytes"] or (f["sha256"] and f["sha256"] != h.hexdigest()):
                raise ValueError("dataset file fingerprint mismatch")
            if f["file"] in result:
                raise ValueError("duplicate dataset file")
            result[f["file"]] = h.hexdigest()
    return result


def validate_manifest(manifest):
    """Reconstruct expected entries against repository pins, without SQLGlot."""
    doc = metadata()
    queries = manifest.get("queries", [])
    if (manifest.get("suite") != SUITE or type(manifest.get("format")) is not int or manifest["format"] != 1
            or not isinstance(manifest.get("source_dataset_id"), str) or not manifest["source_dataset_id"]
            or manifest.get("dataset_id") != manifest["source_dataset_id"]
            or len(queries) != 4 or {q["id"] for q in queries} != set(IDS)
            or manifest.get("table_schemas") != doc["table_schemas"]
            or manifest.get("provenance") != doc["provenance"]):
        raise ValueError("exact separate supplemental suite required")
    rules = {q["id"]: q for q in doc["queries"]}
    expected = copy.deepcopy(manifest)
    expected["queries"] = []
    for q in queries:
        rule = rules[q["id"]]
        if ds.digest(q["source_sql"]) != rule["source_sql_sha256"] or ds.digest(q["logical_sql"]) != rule["portable_sql_sha256"]:
            raise ValueError("static supplemental SQL pin mismatch")
        e = _entry(q["id"], q["source_sql"], q["logical_sql"], rule)
        if any(q.get(k) != v for k, v in e.items()):
            raise ValueError("supplemental entry contract mismatch")
        expected["queries"].append(e)
    hashes = {q["id"]: q["source_sha256"] for q in expected["queries"]}
    if manifest.get("source_hashes") != hashes:
        raise ValueError("supplemental source hash set mismatch")
    return expected


def prepare_supplement(campaign, dataset, name, source_dir, timeout=300, abs_tol=1e-8, rel_tol=1e-8):
    """Regenerate static contracts then run shared full source/cast/portable parity."""
    if any(not math.isfinite(v) or v < 0 for v in (abs_tol, rel_tol)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("invalid deadline/tolerance")
    deadline = time.monotonic() + timeout

    def check_input():
        if time.monotonic() >= deadline:
            raise TimeoutError("TPC-DS supplement deadline")
        campaign.check()
        if time.monotonic() >= deadline:
            raise TimeoutError("TPC-DS supplement deadline")

    sources = read_sources(source_dir, check_input)  # Reject inputs before optional imports/output.
    from .runner import dataset_version
    dataset = Path(dataset).resolve()
    check_input()
    dataset_text = (dataset / "dataset.json").read_text()
    check_input()
    source_text = (dataset / "queries.json").read_text()
    check_input()
    meta = json.loads(dataset_text)
    version = dataset_version(meta)
    original = ds.logical_manifest(json.loads(source_text))
    doc = metadata()
    if (meta["status"] != "SUCCESS" or meta["suite"] != "tpcds" or meta["generator_version"] != "1.4.3"
            or meta["dataset_id"] != original["source_dataset_id"] or len(meta["tables"]) != 24
            or {t["name"] for t in meta["tables"]} != set(ds.TABLES)
            or {t["name"]: [column(c["name"], c["type"]) for c in t["schema"]] for t in meta["tables"]} != doc["table_schemas"]):
        raise ValueError("successful pinned 24-table DuckDB 1.4.3 dataset required")
    campaign.check(additional=2 * 1024 ** 3)
    check_input()
    identities = _dataset_identity(dataset, meta, version, check_input)
    check_input()
    import duckdb
    if duckdb.__version__ != "1.4.3":
        raise RuntimeError("supplement requires isolated DuckDB 1.4.3")
    # Empty typed schemas suffice for DESCRIBE; no dataset writes or downloads.
    con = duckdb.connect(":memory:")
    try:
        for t, cols in doc["table_schemas"].items():
            check_input()
            con.execute("CREATE TABLE " + ds.ident(t) + " (" + ", ".join(ds.ident(c["name"])+" "+c["type"] for c in cols) + ")")
        inventory = []
        with ds.bounded(con, check_input, deadline):
            for i in IDS:
                check_input()
                inventory.append(dict(id=i, sql=sources[i], description=[(d[0], str(d[1])) for d in con.execute("DESCRIBE " + sources[i]).fetchall()]))
    finally:
        con.close()
    import sqlglot
    bodies = {}
    for i in IDS:
        check_input()
        bodies[i] = sqlglot.parse_one(sources[i], read="duckdb").sql(dialect="duckdb", comments=False)
        check_input()
    for q in inventory:
        q["sql"] = bodies[q["id"]]
    generated = ds.generate_contracts(inventory, {t: {c["name"]: c["type"] for c in cols} for t, cols in doc["table_schemas"].items()})
    check_input()
    rules, entries = {}, []
    for rule, static in zip(generated["queries"], doc["queries"]):
        check_input()
        rule["source_sql_sha256"] = ds.digest(sources[rule["id"]])
        rule["source_normalization"] = source_normalization(sources[rule["id"]], bodies[rule["id"]], sqlglot.__version__)
        rule["rewrite_provenance"]["source"] = static["rewrite_provenance"]["source"]
        body = rule["portable_body"]
        structural = {k: v for k, v in rule.items() if k != "portable_body"}
        structural["portable_body_sha256"] = ds.digest(body)
        if structural != static:
            raise ValueError("regenerated supplemental contract differs from static acceptance")
        rules[rule["id"]] = rule
        entries.append(_entry(rule["id"], sources[rule["id"]], ds.projection(body, rule), rule))
    manifest = dict(format=1, suite=SUITE, dataset_id=meta["dataset_id"], source_dataset_id=meta["dataset_id"],
        table_schemas=doc["table_schemas"], provenance=doc["provenance"], queries=entries,
        source_manifest_sha256=ds.digest(source_text), source_hashes={q["id"]: q["source_sha256"] for q in entries})
    validate_manifest(manifest)

    def recheck():
        check_input()
        if read_sources(source_dir, check_input) != sources:
            raise ValueError("source changed during qualification")
        if _dataset_identity(dataset, meta, version, check_input) != identities:
            raise ValueError("dataset content changed during qualification")
        check_input()
    check_input()
    return ds._prepare_parity(campaign, dataset, name, meta, version, dataset_text, source_text,
                              manifest, rules, max(0, deadline - time.monotonic()), abs_tol, rel_tol, recheck, bodies, absolute_deadline=deadline)
