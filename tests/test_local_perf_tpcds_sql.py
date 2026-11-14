"""Offline contracts plus opt-in full 99-query source/cast/portable parity."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from benchmarks.local_perf import tpcds_sql as ds
from benchmarks.local_perf.capacity import Campaign
from benchmarks.local_perf.results import Reader, Writer, compare


def source_manifest():
    return dict(format=1, dataset_id="source/test", queries=[
        dict(id=q["id"], sql=q["portable_body"], sql_sha256=ds.digest(q["portable_body"]))
        for q in ds.contracts().values()])


def pins_for(manifest):
    ctx = dict(database_id="db", schema_id="schema", warehouse_id="LOCAL", sql_catalog="iceberg",
               sql_schema="bench", selected_role="admin", binding_revision=1, generation_id="g", grant_revision=1)
    return dict(format=1, status="SUCCESS", dataset_id=manifest["source_dataset_id"], context=ctx, tables=[
        dict(name=t, physical_name="run_" + t, snapshot_id="123", ref=dict(database_id="db", schema_id="schema"),
             table_uuid="uuid-" + t, columns=manifest["table_schemas"][t]) for t in ds.TABLES])


class SyntheticSource(unittest.TestCase):
    def setUp(self):
        # Offline sources reuse the sole stored body. Only canonical source pins
        # are patched; all manifest/hash/schema/snapshot validation still runs.
        original = ds.portable
        def synthetic_portable(sql, rule):
            return original(sql, dict(rule, source_sql_sha256=ds.digest(rule["portable_body"])))
        self.patcher = patch.object(ds, "portable", side_effect=synthetic_portable)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)


class Contracts(SyntheticSource):
    def test_complete_static_contracts_no_optional_imports(self):
        rules = ds.contracts()
        self.assertEqual(set(rules), {str(i) for i in range(1, 100)})
        self.assertEqual(len(ds.TABLES), 24)
        self.assertEqual({q["id"] for q in rules.values() if any("expression" in k for k in q["order_by"])}, {"36", "47", "57", "70", "86", "89"})
        self.assertEqual({q["id"] for q in rules.values() if q["limit"] and not q["order_by"]}, {"28", "32", "38", "97"})
        self.assertNotIn("sqlglot", sys.modules)
        text = Path(ds.__file__).with_name("tpcds_contracts.json").read_text()
        for forbidden in ("/tmp/", "/Users/", "source_result_rows", "dataset_id"):
            self.assertNotIn(forbidden, text)
        for q in rules.values():
            self.assertNotIn("hugeint", [c["type"] for c in q["columns"]])
            self.assertEqual(ds.digest(ds.projection(q["portable_body"], q)), q["portable_sql_sha256"])
            self.assertFalse({"source_sql", "portable_sql", "sort_keys"} & set(q))

    def test_contract_tampering(self):
        original = json.loads(Path(ds.__file__).with_name("tpcds_contracts.json").read_text())
        changes = [lambda d: d["queries"].pop(),
                   lambda d: d["queries"][0].update(source_sql_sha256="bad"),
                   lambda d: d["queries"][0].update(portable_sql_sha256="bad"),
                   lambda d: d["queries"][0].update(portable_body="SELECT 1"),
                   lambda d: d["queries"][0]["order_by"][0].update(index=-1),
                   lambda d: d["queries"][0]["order_by"][0].update(index=True),
                   lambda d: d["queries"][0]["order_by"][0].update(descending=1),
                   lambda d: d["queries"][0]["order_by"][0].update(nulls="auto"),
                   lambda d: d["table_schemas"].pop("store")]
        for change in changes:
            bad = copy.deepcopy(original)
            change(bad)
            with self.subTest(change=change), patch.object(Path, "read_text", return_value=json.dumps(bad)):
                with self.assertRaises(ValueError):
                    ds.contracts()

    def test_q72_ansi_day_arithmetic_and_canonical_pin(self):
        rule = ds.contracts()["72"]
        self.assertEqual(rule["source_sql_sha256"],
                         "8646866ad7d1ba9e888b70c7f5ccf6480f8085475381b8ca747c85c0c409a744")
        self.assertRegex(rule["portable_body"],
                         r"(?s)d3\.d_date > CAST\(d1\.d_date \+ \(\s*5\s*(?:/\*.*?\*/\s*)?\) \* INTERVAL '1' DAY AS DATE\)")
        marker = "DATE +/- integer days via CAST(DATE + signed integer * INTERVAL '1' DAY AS DATE)"
        self.assertEqual([q["id"] for q in ds.contracts().values()
                          if marker in q["rewrite_provenance"]["transformations"]], ["72"])

    def test_manifest_rejects_mismatched_sql_hash_count(self):
        for change in (lambda s: s["queries"].pop(),
                       lambda s: s["queries"][0].update(sql_sha256="bad"),
                       lambda s: s["queries"][0].update(sql="SELECT 1", sql_sha256=ds.digest("SELECT 1")),
                       lambda s: s.update(dataset_id="")):
            source = source_manifest()
            change(source)
            with self.assertRaises(ValueError):
                ds.logical_manifest(source)

    def test_projection_numeric_and_explicit_null_order(self):
        rules = ds.contracts()
        self.assertIn("NULLS FIRST", ds.projection(rules["2"]["portable_body"], rules["2"]))
        self.assertIn("AVG(CAST(", ds.projection(rules["1"]["portable_body"], rules["1"]))
        self.assertIn(" AS DOUBLE)", ds.projection(rules["2"]["portable_body"], rules["2"]))
        self.assertIn("AS BIGINT)", ds.projection(rules["97"]["portable_body"], rules["97"]))
        self.assertIn("AS DATE)", ds.projection(rules["32"]["portable_body"], rules["32"]))
        self.assertIn("AS result(c0", ds.projection(rules["2"]["portable_body"], rules["2"]))
        self.assertIn("CASE WHEN", ds.projection("SELECT 1", rules["36"]))
        for qid in ("47", "57", "89"):
            sql = ds.projection(rules[qid]["portable_body"], rules[qid])
            self.assertIn('(CAST("sum_sales" AS DOUBLE) - CAST("avg_monthly_sales" AS DOUBLE))', sql)

    def test_limit_full_peer_scan_identical_and_late_conflicts(self):
        import ast
        rule = dict(limit=2, columns=[dict(name="k", type="integer"), dict(name="v", type="varchar")],
                    order_by=[dict(index=0, descending=False, nulls="last")])
        con = Mock()
        def feed(data):
            def cursor(rows):
                pending = list(rows)
                def fetch(n):
                    batch = pending[:n]
                    del pending[:n]
                    return batch
                result = Mock()
                result.fetchmany.side_effect = fetch
                return result
            con.execute.side_effect = [cursor(data), cursor(data)]
        for rows, good in (("(1,'a'),(2,'b'),(2,'b'),(2,'c')", False),
                           ("(1,'a'),(2,'b'),(2,'b'),(2,'b'),(2,'b'),(2,'c')", False),
                           ("(1,'a'),(2,'b'),(2,'b'),(2,'b'),(3,'z')", True)):
            sql = "SELECT * FROM (VALUES " + rows + ") t(k,v) ORDER BY k,v LIMIT 2;"
            feed(ast.literal_eval("[" + rows + "]"))
            with self.subTest(rows=rows):
                if good:
                    audit = ds.audit_limit(con, sql, rule)
                    self.assertTrue(audit["interchangeable"])
                else:
                    with self.assertRaises(ds.UnsupportedBoundaryTie) as raised:
                        ds.audit_limit(con, sql, rule)
                    audit = raised.exception.audit
                    self.assertFalse(audit["interchangeable"])
                self.assertTrue(audit["full_scan"])
                self.assertTrue(audit["boundary_tie"])
                self.assertEqual(audit["total_rows"], rows.count("("))
                self.assertEqual(audit["boundary_multiplicity"], rows.count("(2,"))
                self.assertEqual(audit["input_sql_sha256"], ds.digest(sql))
                self.assertEqual(audit["unlimited_sql_sha256"], ds.digest(sql[:-8]))
        # N+1 must match every ORDER key; preserve nested LIMITs verbatim.
        rule["order_by"].append(dict(index=1, descending=False, nulls="last"))
        feed([(1, "a"), (2, "b"), (2, "c")])
        audit = ds.audit_limit(con, "SELECT * FROM (SELECT 1 LIMIT 1) t LIMIT 2;", rule)
        self.assertFalse(audit["boundary_tie"])
        self.assertEqual(con.execute.call_args.args[0], "SELECT * FROM (SELECT 1 LIMIT 1) t LIMIT 3")
        # No ORDER: every full row must be identical, including later rows.
        rule["order_by"] = []
        for rows, good in (("(2,'b'),(2,'b'),(2,'b'),(2,'c')", False),
                           ("(2,'b'),(2,'b'),(2,'b'),(2,'b')", True)):
            sql = "SELECT * FROM (VALUES " + rows + ") t(k,v) LIMIT 2"
            feed(ast.literal_eval("[" + rows + "]"))
            if good:
                audit = ds.audit_limit(con, sql, rule)
                self.assertEqual(audit["boundary_multiplicity"], 4)
            else:
                with self.assertRaises(ds.UnsupportedBoundaryTie):
                    ds.audit_limit(con, sql, rule)
        feed([(1, "a")])
        self.assertFalse(ds.audit_limit(con, "SELECT 1, 'a' LIMIT 2", rule)["boundary_tie"])
        with self.assertRaisesRegex(ValueError, "rewrite mismatch"):
            ds.audit_limit(con, "SELECT 1 LIMIT 3", rule)

    def test_limit_exact_float_rows_and_budget_propagation(self):
        from benchmarks.local_perf.results import cell, order_values
        columns = [dict(name="k", type="integer"), dict(name="v", type="double")]
        rule = dict(limit=2, columns=columns, order_by=[dict(index=0, descending=False, nulls="first")])
        con = Mock()
        # The conflicting row occurs after a complete 256-row scan batch.
        con.execute.return_value.fetchmany.side_effect = [
            [(1, 0.), (2, 1.), (2, 1.)],
            [(2, 1.)] * 256, [(2, 1.000000000001)], []]
        with self.assertRaises(ds.UnsupportedBoundaryTie) as raised:
            ds.audit_limit(con, "SELECT k,v FROM t LIMIT 2", rule)
        self.assertEqual(raised.exception.audit["boundary_multiplicity"], 257)
        for error in (TimeoutError("deadline"), RuntimeError("capacity")):
            con.reset_mock()
            con.execute.return_value.fetchmany.side_effect = [
                [(1, 0.), (2, 1.), (2, 1.)], [(2, 1.)] * 256]
            check = Mock(side_effect=[None, None, None, None, error])
            with self.assertRaisesRegex(type(error), str(error)):
                ds.audit_limit(con, "SELECT k,v FROM t LIMIT 2", rule, check)
            self.assertEqual(con.execute.call_count, 2)
        # ORDER equality of signed zeros does not imply identical cell encoding.
        con.execute.return_value.fetchmany.side_effect = [
            [(1, 0.), (2, 0.), (2, 0.)], [(2, 0.), (2, -0.)], []]
        with self.assertRaises(ds.UnsupportedBoundaryTie):
            ds.audit_limit(con, "SELECT k,v FROM t LIMIT 2", rule)
        # Shared CASE evaluator consumes canonical projected cells.
        keys = [dict(expression=dict(op="case_equal", condition=0, value=0, then=1), descending=False, nulls="first")]
        ds.validate_order_contract(columns, keys)
        self.assertEqual(order_values([cell(0, "integer"), cell(2., "double")], columns, keys), [2.])
        self.assertEqual(order_values([cell(1, "integer"), cell(2., "double")], columns, keys), [None])

    def test_computed_sort_validation_without_transient_results(self):
        from decimal import Decimal
        rule = dict(columns=[dict(name="a", type="decimal(10,2)"), dict(name="b", type="double")],
                    order_by=[dict(expression=dict(op="subtract", left=0, right=1), descending=False, nulls="last")])
        with tempfile.TemporaryDirectory() as root:
            for rows, good in (([(Decimal('9'), 8.), (Decimal('2'), 0.)], True),
                               ([(Decimal('2'), 0.), (Decimal('9'), 8.)], False)):
                path = Path(root) / str(good)
                w = Writer(path, rule["columns"], {})
                for row in rows:
                    w.row(row)
                w.finish()
                before = set(Path(root).iterdir())
                if good:
                    ds.validate_query_order(path, rule)
                else:
                    with self.assertRaisesRegex(ValueError, "ORDER BY"):
                        ds.validate_query_order(path, rule)
                self.assertEqual(before, set(Path(root).iterdir()))

    def test_bigint_range_and_decimal_remain_exact(self):
        from decimal import Decimal
        con = Mock()
        con.execute.return_value.description = [("total", "HUGEINT")]
        con.execute.return_value.fetchmany.side_effect = [[(2 ** 63,)], []]
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, "range"):
                ds._stream(con, "SELECT sum(x)", Path(root) / "overflow", [dict(name="total", type="bigint")], {}, lambda: None)
            for label, value in (("a", "1.01"), ("b", "1.02")):
                w = Writer(Path(root) / label, [dict(name="d", type="decimal(5,2)")], {})
                w.row([Decimal(value)])
                w.finish()
            with self.assertRaises(ValueError):
                compare(Path(root) / "a", Path(root) / "b", abs_tol=1, rel_tol=1)

    def test_failed_prepare_records_owned_status_and_retains_error(self):
        source = source_manifest()
        meta = dict(status="SUCCESS", suite="tpcds", generator_version="1.4.3", dataset_id=source["dataset_id"],
                    tables=[dict(name=t, schema=cols) for t, cols in ds.table_schemas().items()])
        with tempfile.TemporaryDirectory() as root:
            dataset = Path(root) / "source"
            dataset.mkdir()
            (dataset / "dataset.json").write_text(json.dumps(meta))
            (dataset / "queries.json").write_text(json.dumps(source))
            campaign = Campaign.create(Path(root) / "campaign")
            with patch.object(ds, "connect", side_effect=RuntimeError("engine failed")):
                with self.assertRaises(RuntimeError):
                    ds.prepare_tpcds(campaign, dataset, "failed")
            record = json.loads((campaign.root / "failed" / "plan.json").read_text())
            self.assertEqual(record["status"], "FAILED")
            self.assertEqual(record["error"], "engine failed")
            self.assertFalse((campaign.root / "failed" / "parity.duckdb").exists())
            campaign.refresh()
            self.assertEqual(campaign.state["owned"]["failed"]["status"], "FAILED")


class Binding(SyntheticSource):
    def setUp(self):
        super().setUp()
        self.manifest = ds.logical_manifest(source_manifest())
        self.pins = pins_for(self.manifest)

    def test_all_ctes_outer_scope_hashes_and_immutability(self):
        original = copy.deepcopy(self.manifest)
        bound = ds.bind_snapshots(self.manifest, self.pins)
        self.assertEqual(self.manifest, original)
        for q in bound["queries"]:
            self.assertEqual(q["sql"].count("FOR VERSION AS OF 123"), 24)
            self.assertIn("SELECT * FROM (SELECT", q["sql"])
            self.assertEqual(q["sql_sha256"], ds.digest(q["sql"]))
            self.assertNotEqual(q["reference_sha256"], q["target_sha256"])
            self.assertEqual(q["reference_sql"], q["logical_sql"])
        pins = copy.deepcopy(self.pins)
        pins["tables"].reverse()
        self.assertEqual(ds.bind_snapshots(self.manifest, pins)["dataset_id"], bound["dataset_id"])
        pins["tables"][0]["snapshot_id"] = "124"
        self.assertNotEqual(ds.bind_snapshots(self.manifest, pins)["dataset_id"], bound["dataset_id"])
        # scalar c_customer_id/aliases are preserved inside the wrapped query.
        self.assertIn("ctr1.ctr_customer_sk", bound["queries"][0]["sql"])

    def test_reject_unsuccessful_snapshot_freeze(self):
        for status in ("FAILED", "INCOMPLETE", None):
            pins = copy.deepcopy(self.pins)
            if status is None:
                pins.pop("status")
            else:
                pins["status"] = status
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, "successful snapshot freeze required"):
                ds.bind_snapshots(self.manifest, pins)

    def test_reject_pin_identity_context_schema_tampering(self):
        cases = [lambda p: p["tables"].pop(), lambda p: p.update(dataset_id="other"),
                 lambda p: p["context"].pop("warehouse_id"),
                 lambda p: p["context"].update(sql_schema="x;sql"),
                 lambda p: p["tables"][0].update(snapshot_id="１２３"),
                 lambda p: p["tables"][0].update(snapshot_id=123),
                 lambda p: p["tables"][0].update(physical_name="x.y"),
                 lambda p: p["tables"][0].update(physical_name="run_customer"),
                 lambda p: p["tables"][0]["ref"].update(database_id="other"),
                 lambda p: p["tables"][0]["ref"].update(sql_catalog="other"),
                 lambda p: p["tables"][0]["columns"][0].update(type="varchar")]
        for change in cases:
            pins = copy.deepcopy(self.pins)
            change(pins)
            with self.subTest(change=change), self.assertRaises((ValueError, KeyError)):
                ds.bind_snapshots(self.manifest, pins)

    def test_reject_manifest_contract_source_hash_and_schema_tampering(self):
        cases = [lambda m: m.update(source_hashes={}),
                 lambda m: m["table_schemas"].pop("customer"),
                 lambda m: m.update(context=dict(self.pins["context"], generation_id="other"))]
        for field in ("logical_sql", "logical_sha256", "source_sha256", "reference_sql", "sql_sha256", "columns", "order_by"):
            cases.append(lambda m, f=field: m["queries"][0].update({f: "changed"}))
        for change in cases:
            manifest = copy.deepcopy(self.manifest)
            change(manifest)
            with self.subTest(change=change), self.assertRaises((ValueError, KeyError)):
                ds.bind_snapshots(manifest, self.pins)


class DateArithmeticGeneration(unittest.TestCase):
    def setUp(self):
        try:
            import sqlglot
        except ImportError:
            self.skipTest("optional generator requires SQLGlot 30.31.0")
        if sqlglot.__version__ != "30.31.0":
            self.skipTest("optional generator requires SQLGlot 30.31.0")
        self.sqlglot = sqlglot

    def normalize(self, sql):
        from sqlglot.optimizer.annotate_types import annotate_types
        tree = self.sqlglot.parse_one(sql, read="duckdb")
        annotate_types(tree, dialect="duckdb")
        return ds.normalize_date_integer_arithmetic(tree)

    def test_typed_scope_and_nested_arithmetic(self):
        for expression in ("DATE '2024-02-29' - DATE '2024-02-28'", "1 + 2",
                           "DATE '2024-02-29' + 1.5", "DATE '2024-02-29' + 'x'",
                           "TIMESTAMP '2024-02-29 00:00:00' + 1"):
            with self.subTest(expression=expression):
                original = self.sqlglot.parse_one("SELECT " + expression, read="duckdb")
                tree, count = self.normalize("SELECT " + expression)
                self.assertEqual(count, 0)
                self.assertEqual(tree.sql(dialect="trino"), original.sql(dialect="trino"))
        tree, count = self.normalize("SELECT (DATE '2024-02-28' + 1) - 2")
        self.assertEqual(count, 2)
        self.assertEqual(len(list(tree.find_all(self.sqlglot.exp.Interval))), 2)
        again, count = ds.normalize_date_integer_arithmetic(tree)
        self.assertEqual(count, 0)
        self.assertEqual(again, tree)

    def test_native_date_values_and_filter_semantics(self):
        try:
            import duckdb
        except ImportError:
            self.skipTest("optional execution requires DuckDB")
        con = duckdb.connect()
        self.addCleanup(con.close)
        data = """(VALUES (DATE '2023-12-31', 1), (DATE '2024-01-31', 1),
                  (DATE '2024-02-28', 1), (DATE '2024-02-29', 1),
                  (DATE '2024-03-01', -1), (DATE '2024-02-29', 0),
                  (NULL::DATE, 1), (DATE '2024-02-29', NULL::INTEGER)) AS t(d, n)"""
        # Explicit casts give the isolated generator the same types as qualified inventory.
        for expression in ("CAST(d AS DATE) + CAST(n AS INTEGER)",
                           "CAST(d AS DATE) - CAST(n AS INTEGER)",
                           "CAST(n AS INTEGER) + CAST(d AS DATE)",
                           "CAST(d AS DATE) - -CAST(n AS INTEGER)"):
            with self.subTest(expression=expression):
                source = "SELECT " + expression + " AS shifted FROM " + data
                tree, count = self.normalize(source)
                self.assertEqual(count, 1)
                portable = tree.sql(dialect="trino")
                self.assertIn("INTERVAL '1' DAY", portable)
                self.assertNotIn("DATE_ADD", portable)
                expected = con.execute(source).fetchall()
                result = con.execute(portable)
                self.assertEqual(str(result.description[0][1]), "DATE")
                self.assertEqual(result.fetchall(), expected)
                predicate = " WHERE shifted > DATE '2024-02-28'"
                self.assertEqual(con.execute("SELECT * FROM (" + source + ")" + predicate).fetchall(),
                                 con.execute("SELECT * FROM (" + portable + ")" + predicate).fetchall())
        tree, _ = self.normalize("SELECT CAST(d AS DATE) + CAST(n AS INTEGER) AS shifted FROM " + data)
        import datetime
        self.assertEqual(con.execute(tree.sql(dialect="trino")).fetchall(), [
            (datetime.date(2024, 1, 1),), (datetime.date(2024, 2, 1),),
            (datetime.date(2024, 2, 29),), (datetime.date(2024, 3, 1),),
            (datetime.date(2024, 2, 29),), (datetime.date(2024, 2, 29),), (None,), (None,)])


@unittest.skipUnless(os.environ.get("TPC_DS_SOURCE"), "set TPC_DS_SOURCE to read-only 24-Parquet dataset")
class RealParity(unittest.TestCase):
    def test_all_99_full_source_cast_portable_qualification(self):
        source = Path(os.environ["TPC_DS_SOURCE"])
        def fingerprints():
            hashes = {}
            for path in source.iterdir():
                if path.is_file():
                    h = hashlib.sha256()
                    with path.open("rb") as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            h.update(chunk)
                    hashes[path.name] = h.hexdigest()
            return hashes
        before = fingerprints()
        # Own a fresh campaign; never modify the root's campaign or old receipts.
        root = Path(tempfile.mkdtemp(prefix="tpcds-all99-qualified-"))
        campaign = Campaign.create(root / "campaign")
        out = ds.prepare_tpcds(campaign, source, "proof", timeout=1200)
        record = json.loads((out / "plan.json").read_text())
        self.assertEqual(before, fingerprints())
        self.assertEqual(record["status"], "SUCCESS")
        self.assertEqual((record["query_count"], record["nonempty_queries"], record["result_rows"]), (99, 72, 3928))
        q59 = next(q for q in record["queries"] if q["id"] == "59")
        for label in ("limit_audit", "portable_limit_audit"):
            audit = q59[label]
            self.assertTrue(audit["boundary_tie"])
            self.assertTrue(audit["interchangeable"])
            self.assertTrue(audit["full_scan"])
            self.assertGreater(audit["total_rows"], 101)
            self.assertGreater(audit["boundary_multiplicity"], 1)
        print("TPCDS all99 qualification:", json.dumps(dict(path=str(out), queries=record["query_count"],
              nonempty=record["nonempty_queries"], result_rows=record["result_rows"],
              q59_source=q59["limit_audit"], q59_portable=q59["portable_limit_audit"])), flush=True)




class MultifilePreparation(unittest.TestCase):
    def test_v1_direct_child_guard_before_engine_sql(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            dataset = root / "source"
            dataset.mkdir()
            outside = root / "outside.parquet"
            outside.write_bytes(b"outside")
            (dataset / "linked.parquet").symlink_to(outside)
            campaign = Campaign.create(root / "campaign")
            columns = [dict(name="id", type="bigint")]
            tables = [dict(name=name, file=name + ".parquet", schema=columns) for name in ds.TABLES]
            meta = dict(format=1, status="SUCCESS", suite="tpcds", generator_version="1.4.3",
                        dataset_id="source", tables=tables)
            source_text = json.dumps(dict(format=1, dataset_id="source", queries=[]))
            (dataset / "queries.json").write_text(source_text)
            manifest = dict(format=1, dataset_id="source", source_dataset_id="source", queries=[],
                            table_schemas={t["name"]: columns for t in tables})
            con = Mock()
            con.execute.return_value.description = [("id", "BIGINT")]
            with patch.object(ds, "logical_manifest", return_value=manifest), patch.object(ds, "connect", return_value=con):
                for case, filename in (("traversal", "../outside.parquet"), ("symlink", "linked.parquet")):
                    with self.subTest(case=case):
                        tables[0]["file"] = filename
                        (dataset / "dataset.json").write_text(json.dumps(meta))
                        con.reset_mock()
                        with self.assertRaisesRegex(ValueError, "^dataset table must be a direct child$"):
                            ds.prepare_tpcds(campaign, dataset, case)
                        con.execute.assert_not_called()
                # Missing direct children are left for the engine to open.
                tables[0]["file"] = tables[0]["name"] + ".parquet"
                (dataset / "dataset.json").write_text(json.dumps(meta))
                con.reset_mock()
                out = ds.prepare_tpcds(campaign, dataset, "missing_direct_children")
                views = [c.args[0] for c in con.execute.call_args_list if c.args[0].startswith("CREATE VIEW")]
                self.assertEqual(views, [
                    "CREATE VIEW " + ds.ident(t["name"]) + " AS SELECT * FROM read_parquet("
                    + ds.quote((dataset / t["file"]).resolve()) + ")" for t in tables])
                self.assertEqual(json.loads((out / "queries.json").read_text()), manifest)
                record = json.loads((out / "plan.json").read_text())
                self.assertEqual(record["source_manifest_sha256"], ds.digest(source_text))
                self.assertEqual(record["status"], "SUCCESS")

    def test_v2_views_check_later_schema_and_keep_query_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            dataset = root / "source"
            dataset.mkdir()
            campaign = Campaign.create(root / "campaign")
            columns = [dict(name="id", type="bigint")]
            tables = []
            for name in ds.TABLES:
                files = []
                for i in range(2):
                    path = dataset / (name + "-" + str(i) + ".parquet")
                    path.write_bytes(b"parquet")
                    files.append(dict(file=path.name, rows=1, file_bytes=7, physical_bytes=4096, sha256="a" * 64))
                tables.append(dict(name=name, schema=columns, files=files, rows=2, file_bytes=14, physical_bytes=8192))
            meta = dict(format=2, status="SUCCESS", suite="tpcds", generator_version="1.4.3", dataset_id="source", tables=tables)
            source_text = json.dumps(dict(format=1, dataset_id="source", queries=[]))
            (dataset / "dataset.json").write_text(json.dumps(meta))
            (dataset / "queries.json").write_text(source_text)
            manifest = dict(format=1, dataset_id="source", source_dataset_id="source", queries=[],
                            table_schemas={t["name"]: columns for t in tables})
            con = Mock()
            con.execute.return_value.fetchall.return_value = [("id", "BIGINT")]
            con.execute.return_value.description = [("id", "BIGINT")]
            with patch.object(ds, "logical_manifest", return_value=manifest), patch.object(ds, "connect", return_value=con):
                out = ds.prepare_tpcds(campaign, dataset, "good")
                record = json.loads((out / "plan.json").read_text())
                self.assertEqual(record["source_manifest_sha256"], ds.digest(source_text))
                views = [c.args[0] for c in con.execute.call_args_list if c.args[0].startswith("CREATE VIEW")]
                self.assertEqual(len(views), len(tables))
                self.assertTrue(all("union_by_name=false" in sql and "-0.parquet" in sql and "-1.parquet" in sql for sql in views))
                con.reset_mock()
                con.execute.return_value.fetchall.side_effect = [[("id", "BIGINT")], [("id", "DOUBLE")]]
                with self.assertRaisesRegex(ValueError, "schema"):
                    ds.prepare_tpcds(campaign, dataset, "bad_schema")
                self.assertFalse(any(c.args[0].startswith("CREATE VIEW") for c in con.execute.call_args_list))
            for invalid in (True, 3, "2"):
                meta["format"] = invalid
                (dataset / "dataset.json").write_text(json.dumps(meta))
                with self.assertRaisesRegex(ValueError, "format"):
                    ds.prepare_tpcds(campaign, dataset, "invalid")


if __name__ == "__main__":
    unittest.main()
