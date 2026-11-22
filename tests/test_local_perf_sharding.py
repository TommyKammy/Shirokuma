"""File-list contracts plus offline native DuckDB sharding checks."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from benchmarks.local_perf.capacity import write_json
from benchmarks.local_perf.dataset_layout import table_files, read_sql, file_identity
from benchmarks.local_perf import sharding
from benchmarks.local_perf.dataset import generate

try:
    import duckdb
    NATIVE = duckdb.__version__ == "1.4.3"
except ImportError:
    NATIVE = False


class Campaign:
    def __init__(self, root):
        self.root = root
        self.state = dict(id="test", owned={})
    def refresh(self):
        pass
    def check(self, additional=0):
        pass
    def own(self, name, kind):
        path = self.root / name
        path.mkdir()
        self.state["owned"][name] = dict(kind=kind, status="INCOMPLETE")
        return path
    def mark(self, path, status):
        self.state["owned"][path.name]["status"] = status


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "a.parquet").write_bytes(b"parquet")
        self.legacy = dict(file="a.parquet", rows=0)
        self.file = dict(file="a.parquet", rows=0, file_bytes=7, physical_bytes=4096, sha256="a" * 64)
        self.table = dict(rows=0, file_bytes=7, physical_bytes=4096, files=[self.file])
    def test_legacy_no_hash_and_exact_sql(self):
        with patch("hashlib.sha256", side_effect=AssertionError("must not hash")):
            files = table_files(self.root, self.legacy)
            self.assertIsNone(files[0]["sha256"])
            self.assertEqual(files[0]["file_bytes"], 7)
            self.assertEqual(read_sql(self.root, self.legacy), "SELECT * FROM read_parquet('" + str(self.root / "a.parquet") + "')")
        relative = Path(os.path.relpath(self.root))
        self.assertEqual(read_sql(relative, self.legacy), "SELECT * FROM read_parquet('" + str(relative / "a.parquet") + "')")
    def test_legacy_sql_requires_no_filesystem_or_metadata_validation(self):
        with patch.object(Path, "resolve", side_effect=AssertionError("filesystem access")), \
                patch.object(Path, "is_file", side_effect=AssertionError("filesystem access")), \
                patch.object(Path, "stat", side_effect=AssertionError("filesystem access")), \
                patch("benchmarks.local_perf.dataset_layout.table_files", side_effect=AssertionError("file validation")):
            self.assertEqual(read_sql("literal-relative-root", {"file": "missing.parquet"}),
                             "SELECT * FROM read_parquet('literal-relative-root/missing.parquet')")

    def test_legacy_flat_filename_without_parquet_suffix(self):
        content = b"legacy source bytes"
        path = self.root / "events.bin"
        path.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        legacy = dict(file=path.name, rows=2, sha256=digest)
        files = table_files(self.root, legacy, 1)
        self.assertEqual(files[0]["path"], path)
        self.assertEqual(files[0]["file_bytes"], len(content))
        self.assertEqual(hashlib.sha256(files[0]["path"].read_bytes()).hexdigest(), digest)
        self.assertEqual(file_identity(files, 1), digest)
        table = dict(rows=2, file_bytes=files[0]["file_bytes"],
                     physical_bytes=files[0]["physical_bytes"], files=files)
        with self.assertRaises(ValueError):
            table_files(self.root, table, 2)
        with self.assertRaises(ValueError):
            file_identity(files, 2)

    def test_flat_filename_traversal_rejected_in_both_versions(self):
        for name in ("", ".", "..", "../events.bin", "sub/events.bin", "sub\\events.bin"):
            for version in (1, 2):
                entry = dict(self.file, file=name)
                table = entry if version == 1 else dict(self.table, files=[entry])
                with self.subTest(name=name, version=version):
                    with self.assertRaises(ValueError):
                        table_files(self.root, table, version)
                    with self.assertRaises(ValueError):
                        file_identity([entry], version)

    def test_missing_files_rejected_by_file_validation_and_v2_sql(self):
        legacy = dict(self.legacy, file="missing.parquet")
        table = dict(self.table, files=[dict(self.file, file="missing.parquet")])
        for version, metadata in ((1, legacy), (2, table)):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "missing"):
                table_files(self.root, metadata, version)
        with self.assertRaisesRegex(ValueError, "missing"):
            read_sql(self.root, table, 2)

    def test_glob_controls_and_ambiguous_legacy_rejected_before_filesystem(self):
        # A literal glob basename can exist beside another file DuckDB would match.
        (self.root / "part*.parquet").write_bytes(b"PAR1literalPAR1")
        (self.root / "part-neighbor.parquet").write_bytes(b"PAR1neighborPAR1")
        names = ["part*.parquet", "a?.parquet", "a[0].parquet", "a].parquet"]
        names += ["a" + chr(i) + ".parquet" for i in (*range(32), 127)]
        for name in names:
            for version, table in ((1, dict(file=name, rows=0)),
                                   (2, dict(self.table, files=[dict(self.file, file=name)]))):
                with self.subTest(name=name, version=version), patch.object(Path, "resolve", side_effect=AssertionError("filesystem access")):
                    with self.assertRaises(ValueError):
                        table_files(self.root, table, version)
                    with self.assertRaises(ValueError):
                        file_identity([dict(self.file, file=name)], version)
                    if version == 2:
                        with self.assertRaises(ValueError):
                            read_sql(self.root, table, version)
        with patch.object(Path, "resolve", side_effect=AssertionError("filesystem access")), self.assertRaises(ValueError):
            table_files(self.root, dict(self.legacy, files=[self.file]))

    def test_v2_sql_identity_order_and_no_paths(self):
        self.assertIn("union_by_name=false", read_sql(self.root, self.table, 2))
        identity_file = {k: self.file[k] for k in ("file", "rows", "file_bytes", "sha256")}
        expected = hashlib.sha256(json.dumps([identity_file], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(file_identity(table_files(self.root, self.table, 2), 2), expected)
        self.assertEqual(file_identity([self.file]), "a" * 64)
    def test_strict_formats_fields_and_aggregates(self):
        for version in (True, False, 0, 3, "2", None):
            with self.subTest(version=version), self.assertRaises(ValueError):
                table_files(self.root, self.table, version)
            with self.subTest(version=version), self.assertRaises(ValueError):
                read_sql(self.root, self.table, version)
        for key, value in (("rows", True), ("rows", -1), ("file_bytes", 0), ("physical_bytes", False), ("sha256", "A"*64), ("file", "../a.parquet")):
            table = copy.deepcopy(self.table)
            table["files"][0][key] = value
            with self.subTest(key=key), self.assertRaises((ValueError, KeyError)):
                table_files(self.root, table, 2)
            with self.subTest(key=key), self.assertRaises((ValueError, KeyError)):
                read_sql(self.root, table, 2)
        for mutate in (lambda t: t.update(file="a.parquet"), lambda t: t.update(rows=1),
                       lambda t: t.update(files=[]), lambda t: t["files"].append(dict(t["files"][0])),
                       lambda t: t["files"][0].pop("sha256"), lambda t: t.pop("file_bytes")):
            table = copy.deepcopy(self.table)
            mutate(table)
            with self.assertRaises((ValueError, KeyError)):
                table_files(self.root, table, 2)
            with self.assertRaises((ValueError, KeyError)):
                read_sql(self.root, table, 2)
    def test_symlink_escape(self):
        with tempfile.TemporaryDirectory() as other:
            external = Path(other) / "b.parquet"
            external.write_bytes(b"x")
            (self.root / "b.parquet").symlink_to(external)
            with self.assertRaises(ValueError):
                table_files(self.root, dict(file="b.parquet", rows=0))
    def test_identity_validation(self):
        for files in ([], [self.file, self.file], [dict(self.file, rows=True)], [dict(self.file, sha256=None)]):
            with self.assertRaises(ValueError):
                file_identity(files, 2)
    def test_controls_and_generate_options_before_ownership(self):
        campaign = Campaign(self.root)
        for options in (dict(target_file_bytes=True), dict(target_file_bytes=100, row_group_size=2047),
                        dict(target_file_bytes=100, max_file_bytes=99), dict(row_group_size=2048),
                        dict(max_file_bytes=1), dict(target_file_bytes=1, timeout=float("nan"))):
            with self.assertRaises(ValueError):
                generate(campaign, "unused", **options)
        self.assertFalse((self.root / "unused").exists())

    def bytefile_source(self, **changes):
        campaign = Campaign(self.root)
        source = campaign.own("source", "dataset")
        (source / "input.parquet").write_bytes(b"PAR1sourcePAR1")
        meta = dict(format=1, status="SUCCESS", dataset_id="custom/source", mode="qualification",
                    scale=.01, peak_bytes=256*1024**2,
                    tables=[dict(name="custom", file="input.parquet", rows=0, schema=[])])
        meta.update(changes)
        write_json(source / "dataset.json", meta)
        campaign.mark(source, "SUCCESS")
        return campaign, source, meta

    def test_low_sf1000_peak_requires_real_evidence_before_output(self):
        campaign, source, meta = self.bytefile_source(mode="performance", scale=1000,
                                                      peak_bytes=1024**3, repetitions=1, peak_evidence=None)
        before = {p.name: p.read_bytes() for p in source.iterdir()}
        with patch.object(campaign, "own", side_effect=AssertionError("ownership")), patch.object(sharding, "connect", side_effect=AssertionError("connect")), patch.object(sharding, "export_table", side_effect=AssertionError("COPY")):
            with self.assertRaisesRegex(ValueError, "pilot evidence"):
                sharding.shard_dataset(campaign, source, "unused")
        self.assertFalse((self.root / "unused").exists())
        self.assertEqual(before, {p.name: p.read_bytes() for p in source.iterdir()})

    def test_strict_measured_bytes_before_output(self):
        campaign, source, meta = self.bytefile_source()
        for value in (True, False, -1, .5, "0", None, 1_000_000_001):
            write_json(source / "dataset.json", dict(meta, measured_logical_bytes=value))
            with self.subTest(value=value), patch.object(campaign, "own", side_effect=AssertionError("ownership")), patch.object(sharding, "connect", side_effect=AssertionError("connect")):
                with self.assertRaises(ValueError):
                    sharding.shard_dataset(campaign, source, "unused")
        self.assertFalse((self.root / "unused").exists())

    def test_invalid_and_conflicting_queries_before_output(self):
        campaign, source, meta = self.bytefile_source()
        for value in ("nonempty", {"id": "q"}, 3, None, False):
            write_json(source / "dataset.json", dict(meta, queries=value))
            with self.subTest(value=value), patch.object(campaign, "own", side_effect=AssertionError("ownership")), self.assertRaisesRegex(ValueError, "queries must be a list"):
                sharding.shard_dataset(campaign, source, "unused")
        write_json(source / "dataset.json", dict(meta, queries=[dict(id="q", sql="SELECT 1")]))
        write_json(source / "queries.json", dict(format=1, dataset_id=meta["dataset_id"], queries=[dict(id="q", sql="SELECT 2")]))
        with patch.object(campaign, "own", side_effect=AssertionError("ownership")), patch.object(sharding, "connect", side_effect=AssertionError("connect")), self.assertRaisesRegex(ValueError, "manifests differ"):
            sharding.shard_dataset(campaign, source, "unused")
        write_json(source / "queries.json", None)
        with patch.object(campaign, "own", side_effect=AssertionError("ownership")), self.assertRaisesRegex(ValueError, "invalid source queries manifest"):
            sharding.shard_dataset(campaign, source, "unused")
        self.assertFalse((self.root / "unused").exists())

    def test_import_error_records_failure_and_cleans_only_owned_scratch(self):
        campaign, source, meta = self.bytefile_source(measured_logical_bytes=0)
        foreign = self.root / "foreign"
        foreign.mkdir()
        (foreign / "source.duckdb").write_bytes(b"foreign database")
        (source / "spill").mkdir()
        (source / "spill" / "preserve").write_bytes(b"foreign scratch")
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        def missing(path, spill, **kw):
            path.write_bytes(b"owned database")
            path.with_suffix(".duckdb.wal").write_bytes(b"owned wal")
            spill.mkdir()
            (spill / "scratch").write_bytes(b"owned scratch")
            raise ImportError("optional DuckDB unavailable")
        with patch.object(sharding, "connect", side_effect=missing), self.assertRaisesRegex(ImportError, "DuckDB unavailable"):
            sharding.shard_dataset(campaign, source, "failed")
        out = self.root / "failed"
        actual = json.loads((out / "dataset.json").read_text())
        self.assertEqual((actual["status"], actual["error_type"]), ("FAILED", "ImportError"))
        self.assertEqual(campaign.state["owned"]["failed"]["status"], "FAILED")
        self.assertEqual(actual["peak_evidence"], None)
        for name in ("source.duckdb", "source.duckdb.wal", "spill"):
            self.assertFalse((out / name).exists())
        for name, content in before.items():
            self.assertEqual((self.root / name).read_bytes(), content)

    def test_owned_failure_cleanup_without_optional_engine(self):
        campaign = Campaign(self.root)
        source = campaign.own("source", "dataset")
        (source / "input.parquet").write_bytes(b"source")
        meta = dict(format=1, status="SUCCESS", dataset_id="custom/source", mode="qualification",
                    scale=.01, peak_bytes=1024*1024, peak_evidence="small bytefile pilot", tables=[dict(name="custom", file="input.parquet", rows=0, schema=[])])
        write_json(source / "dataset.json", meta)
        campaign.mark(source, "SUCCESS")
        before = (source / "dataset.json").read_bytes()
        def fail(path, spill, **kw):
            path.write_bytes(b"owned scratch")
            spill.mkdir()
            (spill / "scratch").write_bytes(b"scratch")
            raise RuntimeError("original engine failure")
        with patch.object(sharding, "connect", side_effect=fail), self.assertRaisesRegex(RuntimeError, "original engine failure"):
            sharding.shard_dataset(campaign, source, "failed")
        out = self.root / "failed"
        self.assertEqual(json.loads((out / "dataset.json").read_text())["status"], "FAILED")
        self.assertFalse((out / "spill").exists())
        self.assertFalse((out / "source.duckdb").exists())
        self.assertEqual((source / "dataset.json").read_bytes(), before)
        with self.assertRaises(FileExistsError):
            sharding.shard_dataset(campaign, source, "failed")


@unittest.skipUnless(NATIVE, "optional DuckDB 1.4.3 required; no extensions used")
class NativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.c = Campaign(self.root)
        self.source = self.c.own("source", "dataset")
        con = duckdb.connect()
        self.addCleanup(con.close)
        self.con = con
        sql = "SELECT i%100 id, CASE WHEN i%7=0 THEN NULL ELSE repeat(md5(cast(i AS VARCHAR)), 5) END payload, cast(i%13 AS DECIMAL(9,2)) d, DATE '2020-01-01' dt FROM range(10000) t(i) UNION ALL SELECT 1, NULL, 1.00, DATE '2020-01-01' UNION ALL SELECT 1, NULL, 1.00, DATE '2020-01-01'"
        con.execute("CREATE TABLE data AS " + sql)
        con.execute("CREATE TABLE empty AS SELECT * FROM data WHERE false")
        tables = []
        for i, name in enumerate(("data", "empty")):
            path = self.source / (name + ".parquet")
            con.execute("COPY " + name + " TO '" + str(path) + "' (FORMAT PARQUET)")
            tables.append(dict(name="arbitrary / name" if i == 0 else name, file=path.name,
                               rows=con.execute("SELECT count(*) FROM " + name).fetchone()[0],
                               schema=sharding.schema_of(con, "SELECT * FROM " + name), file_bytes=path.stat().st_size))
        self.meta = dict(format=1, status="SUCCESS", dataset_id="test/source", mode="qualification", scale=.01,
                         peak_bytes=64*1024**2, peak_evidence="measured small fixture pilot",
                         seed=42, generator="custom SQL", timeout_s=10,
                         fixed_parameters={"custom": True, "codec": "uncompressed"}, tables=tables)
        write_json(self.source / "dataset.json", self.meta)
        self.c.mark(self.source, "SUCCESS")
        self.before = {p.name: p.read_bytes() for p in self.source.iterdir()}
    def run_shard(self, name="shards", **kw):
        return sharding.shard_dataset(self.c, self.source, name, target_file_bytes=10000, row_group_size=2048, **kw)
    def assert_clean(self, out):
        for name in ("source.duckdb", "source.duckdb.wal", "spill"):
            self.assertFalse((out / name).exists())
    def test_legacy_bin_parquet_sharding_preserves_schema_and_duplicates(self):
        (self.source / "data.parquet").rename(self.source / "events.bin")
        self.meta["tables"][0]["file"] = "events.bin"
        write_json(self.source / "dataset.json", self.meta)
        before = {p.name: p.read_bytes() for p in self.source.iterdir()}
        out = self.run_shard()
        meta = json.loads((out / "dataset.json").read_text())
        for table, original in zip(meta["tables"], ("data", "empty")):
            sql = read_sql(out, table, 2)
            self.assertEqual(sharding.schema_of(self.con, sql),
                             sharding.schema_of(self.con, "SELECT * FROM " + original))
            self.assertEqual(self.con.execute("SELECT count(*) FROM (" + sql + ")").fetchone()[0],
                             self.con.execute("SELECT count(*) FROM " + original).fetchone()[0])
            self.assertEqual(self.con.execute("SELECT count(*) FROM ((SELECT * FROM " + original + " EXCEPT ALL " + sql + ") UNION ALL (" + sql + " EXCEPT ALL SELECT * FROM " + original + "))").fetchone()[0], 0)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.source.iterdir()})
        self.assert_clean(out)
    def test_roundtrip_empty_queryless_and_reshard_ignores_neighbors(self):
        out = self.run_shard()
        meta = json.loads((out / "dataset.json").read_text())
        self.assertEqual(meta["format"], 2)
        self.assertGreater(len(meta["tables"][0]["files"]), 1)
        self.assertEqual(len(meta["tables"][1]["files"]), 1)
        self.assertEqual(meta["tables"][1]["files"][0]["rows"], 0)
        self.assertFalse((out / "queries.json").exists())
        self.assertEqual(meta["fixed_parameters"], self.meta["fixed_parameters"])
        for table, original in zip(meta["tables"], ("data", "empty")):
            sql = read_sql(out, table, 2)
            self.assertEqual(self.con.execute("SELECT count(*) FROM ((SELECT * FROM " + original + " EXCEPT ALL " + sql + ") UNION ALL (" + sql + " EXCEPT ALL SELECT * FROM " + original + "))").fetchone()[0], 0)
        (out / "nearby.parquet").write_bytes(b"ignored")
        again = sharding.shard_dataset(self.c, out, "again", target_file_bytes=10000, row_group_size=2048)
        self.assertEqual(json.loads((again / "dataset.json").read_text())["tables"][0]["rows"], 10002)
        self.assert_clean(out)
        self.assertEqual(self.before, {p.name: p.read_bytes() for p in self.source.iterdir()})
        with self.assertRaises(FileExistsError):
            self.run_shard()
    def test_output_budgets_codec_and_provenance(self):
        out = self.run_shard(timeout=90, peak_bytes=128*1024**2)
        actual = json.loads((out / "dataset.json").read_text())
        self.assertEqual(actual["timeout_s"], 90)
        self.assertEqual(actual["peak_bytes"], 128*1024**2)
        self.assertEqual(actual["peak_evidence"], self.meta["peak_evidence"])
        self.assertEqual(actual["sharding"], dict(target_file_bytes=10000, target_kind="soft",
                         row_group_size=2048, max_file_bytes=sharding.MAX_FILE_BYTES,
                         codec="zstd", timeout_s=90, peak_bytes=128*1024**2))
        for key in ("seed", "generator", "fixed_parameters"):
            self.assertEqual(actual[key], self.meta[key])
        path = out / actual["tables"][0]["files"][0]["file"]
        self.assertEqual(self.con.execute("SELECT DISTINCT compression FROM parquet_metadata(?)", [str(path)]).fetchall(), [("ZSTD",)])
        self.assertEqual(self.before, {p.name: p.read_bytes() for p in self.source.iterdir()})

    def test_query_manifest_preserved_and_rebased(self):
        query = dict(format=1, dataset_id="test/source", variants={"a": 1}, queries=[dict(id="custom", sql="SELECT 1", sql_sha256="unchanged",
                     trino_sql="SELECT 1 /* variant */", ordered=True, statement_count=1, extra={"custom": True})])
        self.meta["queries"] = copy.deepcopy(query["queries"])
        write_json(self.source / "dataset.json", self.meta)
        write_json(self.source / "queries.json", query)
        before = {p.name: p.read_bytes() for p in self.source.iterdir()}
        out = self.run_shard()
        actual = json.loads((out / "queries.json").read_text())
        self.assertEqual(actual, dict(query, dataset_id="test/shards"))
        self.assertEqual(json.loads((out / "dataset.json").read_text())["queries"], query["queries"])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.source.iterdir()})
    def test_hard_max_retains_failed_parquet(self):
        with self.assertRaisesRegex(ValueError, "hard maximum"):
            self.run_shard(max_file_bytes=10000)
        out = self.root / "shards"
        self.assertEqual(json.loads((out / "dataset.json").read_text())["status"], "FAILED")
        self.assertTrue(list(out.rglob("*.parquet")))
        self.assert_clean(out)
        self.assertEqual(self.before, {p.name: p.read_bytes() for p in self.source.iterdir()})
    def test_capacity_deadline_and_changed_source_cleanup(self):
        original = sharding.export_table
        def change(*args, **kw):
            result = original(*args, **kw)
            (self.source / "dataset.json").write_text("{}")
            return result
        with patch.object(sharding, "export_table", side_effect=change), self.assertRaisesRegex(ValueError, "changed"):
            self.run_shard("changed")
        self.assert_clean(self.root / "changed")
        write_json(self.source / "dataset.json", self.meta)
        with patch.object(sharding, "connect", side_effect=RuntimeError("capacity")), self.assertRaisesRegex(RuntimeError, "capacity"):
            self.run_shard("capacity")
        self.assert_clean(self.root / "capacity")
        with self.assertRaises(TimeoutError):
            self.run_shard("deadline", timeout=1e-12)
    def test_observed_capacity_growth_fails_and_retains_output(self):
        export = sharding.export_table
        def grow(*args, **kw):
            # A tiny declared budget is allowed with explicit evidence, but
            # actual allocation must fail independently of campaign capacity.
            (args[1] / "partial.parquet").write_bytes(b"x" * 8192)
            kw["check"]()
            return export(*args, **kw)
        with patch.object(sharding, "export_table", side_effect=grow), self.assertRaisesRegex(RuntimeError, "peak exceeded"):
            self.run_shard("growth", peak_bytes=4096)
        out = self.root / "growth"
        self.assertEqual(json.loads((out / "dataset.json").read_text())["status"], "FAILED")
        self.assert_clean(out)
    def test_source_file_mutation_detected_after_copy(self):
        export = sharding.export_table
        def mutate(*args, **kw):
            result = export(*args, **kw)
            if args[2] == 1:
                path = self.source / "data.parquet"
                with path.open("ab") as stream:
                    stream.write(b"changed")
            return result
        with patch.object(sharding, "export_table", side_effect=mutate), self.assertRaisesRegex(ValueError, "source file changed"):
            self.run_shard("mutated")
        self.assert_clean(self.root / "mutated")
    def test_budget_and_inflight_deadline_abort(self):
        with patch.object(self.c, "check", side_effect=RuntimeError("capacity reserve")), self.assertRaisesRegex(RuntimeError, "capacity reserve"):
            self.run_shard("preflight-capacity")
        self.assertFalse((self.root / "preflight-capacity").exists())
        def expired(*args, **kw):
            with patch.object(sharding.time, "monotonic", return_value=float("inf")):
                kw["check"]()
        with patch.object(sharding, "export_table", side_effect=expired), self.assertRaises(TimeoutError):
            self.run_shard("inflight")
        self.assertEqual(json.loads((self.root / "inflight/dataset.json").read_text())["status"], "FAILED")
        self.assert_clean(self.root / "inflight")

    def test_direct_generate_and_legacy_default_without_extensions(self):
        native_connect = sharding.connect
        copies = []
        class GeneratorConnection:
            def __init__(self, con):
                self.con = con
            def execute(self, sql, *args):
                if "duckdb_functions()" in sql:
                    cursor = MagicMock()
                    cursor.fetchall.return_value = []
                    return cursor
                if "tpch_queries()" in sql:
                    cursor = MagicMock()
                    cursor.fetchall.return_value = [(i, "SELECT 1") for i in range(1, 23)]
                    return cursor
                if sql.startswith("CALL dbgen"):
                    for i in range(8):
                        self.con.execute("CREATE TABLE t%d AS SELECT * FROM range(5000)" % i)
                    return self.con
                if sql.startswith("COPY"):
                    copies.append(sql)
                return self.con.execute(sql, *args)
            def __getattr__(self, key):
                return getattr(self.con, key)
        def connector(*args, **kw):
            return GeneratorConnection(native_connect(*args, **kw))
        with patch("benchmarks.local_perf.dataset.connect", side_effect=connector), patch("benchmarks.local_perf.dataset.load_extension", return_value={}):
            out = generate(self.c, "generated", target_file_bytes=10000, row_group_size=2048)
            meta = json.loads((out / "dataset.json").read_text())
            self.assertEqual(meta["format"], 2)
            self.assertEqual(len(meta["tables"]), 8)
            self.assertEqual(len(meta["queries"]), 22)
            self.assertTrue(all("FILE_SIZE_BYTES 10000" in sql for sql in copies))
            self.assertTrue(all("file" not in t for t in meta["tables"]))
            self.assert_clean(out)
            copies.clear()
            with patch.object(sharding, "file_hash", side_effect=AssertionError("legacy must not hash")):
                legacy = generate(self.c, "legacy")
            old = json.loads((legacy / "dataset.json").read_text())
            self.assertEqual(old["format"], 1)
            self.assertNotIn("sharding", old)
            self.assertTrue(all("FILE_SIZE_BYTES" not in sql for sql in copies))
            self.assertEqual(copies[0], "COPY \"t0\" TO '" + str(legacy / "t0.parquet") + "' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 122880)")
            self.assertTrue(all("files" not in t and "sha256" not in t for t in old["tables"]))

    def test_source_preflight_rejects_invalid_before_copy(self):
        for i, mutate in enumerate((lambda m: m.update(format=True), lambda m: m.update(format=99),
                                    lambda m: m["tables"].append(dict(m["tables"][0], name="another")),
                                    lambda m: m["tables"][0].update(sha256="a"*64))):
            meta = copy.deepcopy(self.meta)
            mutate(meta)
            write_json(self.source / "dataset.json", meta)
            with patch.object(sharding, "export_table", side_effect=AssertionError("COPY not allowed")), self.assertRaises((ValueError, KeyError)):
                self.run_shard("bad%d" % i)


if __name__ == "__main__":
    unittest.main()
