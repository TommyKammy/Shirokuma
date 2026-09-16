from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location(
    "local_tpch", Path(__file__).resolve().parents[1] / "tools/local_experiment/benchmark/run.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
TOKEN = "a" * 32


def result(rows, columns=None, types=None):
    return {"rows": rows, "columns": columns or ["value"], "column_types": types or ["bigint"],
            "query_id": "20260916_000000_00000_local", "wall_seconds": 0.1, "stats": {}}


class FakeClient:
    timeout = 30

    def __init__(self):
        self.tables = {"retained_other": "not-owned"}
        self.calls = []
        self.source_count_wrong = False
        self.fail_ctas = False
        self.failed_drain = False
        self.replace_marker = False
        self.fixture_changed = False
        self.fixture_reads = 0

    def query(self, sql):
        self.calls.append(sql)
        if sql.startswith("SELECT version()"):
            return result([["483"]])
        if sql == MODULE.FIXTURE_SQL:
            self.fixture_reads += 1
            return result([[1, "changed"]] if self.fixture_changed and self.fixture_reads > 1 else MODULE.FIXTURE_ROWS)
        if "FROM system.runtime.queries" in sql:
            if self.failed_drain:
                raise MODULE.BenchmarkError("injected control failure")
            return result([])
        if sql.startswith("SHOW TABLES"):
            return result([[name] for name in self.tables])
        if sql.startswith("SHOW CREATE TABLE"):
            name = sql.rsplit(".", 1)[1]
            return result([[f"CREATE TABLE {name} COMMENT '{self.tables[name]}'"]])
        if sql.startswith("CREATE TABLE"):
            name = sql.split()[2].rsplit(".", 1)[1]
            self.tables[name] = "not-owned" if self.replace_marker else f"shirokuma-local-tpch:{TOKEN}"
            if self.fail_ctas:
                raise MODULE.BenchmarkError("injected CTAS timeout after creation")
            return result([])
        if sql.startswith("DROP TABLE"):
            del self.tables[sql.rsplit(".", 1)[1]]
            return result([])
        if sql.startswith("SELECT count(*) AS rows FROM"):
            table = sql.rsplit(".", 1)[1]
            if table.startswith("tpch_"):
                table = table.rsplit("_", 1)[1]
            count = MODULE.SF10_COUNTS[table]
            if self.source_count_wrong:
                count += 1
            return result([[count]])
        if "$files" in sql:
            table = sql.rsplit("_", 1)[1].split("$")[0]
            return result([[1, 1024, MODULE.SF10_COUNTS[table]]])
        # Corpus correctness is checked separately against pinned source identities.
        return result([[42]])


class LocalTPCHTests(unittest.TestCase):
    def run_fake(self, client):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stderr(io.StringIO()):
            output = Path(directory)
            report = MODULE.run_benchmark(client, client, output, TOKEN,
                                          environment=lambda: {"co_located": True})
            self.assertEqual(json.loads((output / "summary.json").read_text()), report)
            return report

    def test_all_sf10_tables_queries_and_cleanup(self):
        client = FakeClient()
        report = self.run_fake(client)
        self.assertEqual(report["result"], "passed", report)
        self.assertEqual(len(report["tables"]), 8)
        self.assertEqual(len(report["queries"]), 22)
        self.assertEqual(report["tables"][-1]["rows"], 59_986_052)
        self.assertTrue(report["fixture_unchanged"])
        self.assertFalse(report["official_tpc_result"])
        self.assertEqual(len(report["cleanup"]["removed"]), 8)
        self.assertEqual(client.tables, {"retained_other": "not-owned"})
        creates = [sql for sql in client.calls if sql.startswith("CREATE TABLE")]
        self.assertEqual(len(creates), 8)
        self.assertTrue(all("tpch.sf10." in sql for sql in creates))
        self.assertFalse(any("DROP TABLE iceberg.smoke" in sql for sql in client.calls))

    def test_collision_does_not_mutate_or_clean_existing_data(self):
        client = FakeClient()
        name = MODULE.table_names(TOKEN)["region"]
        client.tables[name] = "retained"
        report = self.run_fake(client)
        self.assertEqual(report["result"], "failed")
        self.assertIn("collision", report["errors"][0])
        self.assertFalse(any(sql.startswith(("CREATE", "DROP")) for sql in client.calls))
        self.assertIn(name, client.tables)

    def test_failed_comparison_retains_both_results_before_cleanup(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stderr(io.StringIO()):
            output = Path(directory)
            with patch.object(MODULE, "compare_results", side_effect=MODULE.BenchmarkError("shape differs")):
                report = MODULE.run_benchmark(client, client, output, TOKEN, environment=lambda: {})
            self.assertEqual(report["result"], "failed")
            for name in ("q01-reference.json", "q01-iceberg.json"):
                self.assertEqual(json.loads((output / name).read_text())["rows"], [[42]])
            self.assertEqual(len(report["cleanup"]["removed"]), 8)

    def test_source_count_mismatch_prevents_creation(self):
        client = FakeClient()
        client.source_count_wrong = True
        report = self.run_fake(client)
        self.assertEqual(report["result"], "failed")
        self.assertFalse(any(sql.startswith("CREATE") for sql in client.calls))

    def test_ctas_timeout_drains_then_cleans_marked_partial_relation(self):
        client = FakeClient()
        client.fail_ctas = True
        report = self.run_fake(client)
        self.assertEqual(report["result"], "failed")
        self.assertIn("injected CTAS timeout after creation", report["errors"])
        self.assertEqual(len(report["cleanup"]["removed"]), 1)
        inspect_index = next(i for i, sql in enumerate(client.calls) if "system.runtime.queries" in sql)
        drop_index = next(i for i, sql in enumerate(client.calls) if sql.startswith("DROP"))
        self.assertLess(inspect_index, drop_index)
        self.assertEqual(client.tables, {"retained_other": "not-owned"})

    def test_failed_drain_defers_all_drops(self):
        client = FakeClient()
        client.fail_ctas = True
        client.failed_drain = True
        report = self.run_fake(client)
        self.assertEqual(report["cleanup"]["status"], "deferred")
        self.assertFalse(any(sql.startswith("DROP") for sql in client.calls))
        self.assertEqual(len(client.tables), 2)

    def test_cleanup_preserves_unmarked_replacement(self):
        client = FakeClient()
        client.replace_marker = True
        report = self.run_fake(client)
        self.assertEqual(report["result"], "failed")
        self.assertTrue(any("ownership marker" in error for error in report["errors"]))
        self.assertFalse(any(sql.startswith("DROP") for sql in client.calls))

    def test_retained_fixture_change_fails_report(self):
        client = FakeClient()
        client.fixture_changed = True
        report = self.run_fake(client)
        self.assertEqual(report["result"], "failed")
        self.assertFalse(report["fixture_unchanged"])

    def test_input_hashes_and_every_runtime_substitution(self):
        manifest = MODULE.verify_inputs()
        self.assertEqual(len(manifest["inputs"]), 23)
        for number in range(1, 23):
            reference = MODULE.render_query(number)
            iceberg = MODULE.render_query(number, TOKEN)
            self.assertNotIn("${", reference + iceberg)
            self.assertNotIn('"tpch"."sf10"', iceberg)
            self.assertIn('"iceberg"."local_experiment"."tpch_' + TOKEN, iceberg)
        self.assertIn("0.0001000000 / 10", MODULE.render_query(11))
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory)
            for item in manifest["inputs"]:
                target = copied / item["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((MODULE.HERE / item["path"]).read_bytes())
            (copied / "query_inputs.json").write_text(json.dumps(manifest))
            (copied / "queries/q01.sql").write_text("SELECT 'drift'")
            with self.assertRaisesRegex(MODULE.BenchmarkError, "checksum"):
                MODULE.verify_inputs(copied)

    def test_token_validation_prevents_relation_injection(self):
        for token in ("abc", "A" * 32, "x; DROP TABLE other", "../" + TOKEN):
            with self.assertRaises(MODULE.BenchmarkError):
                MODULE.table_names(token)
            with self.assertRaises(MODULE.BenchmarkError):
                MODULE.render_query(1, token)

    def test_q15_exact_arithmetic_keeps_upstream_input_and_exact_max_predicate(self):
        upstream = (MODULE.HERE / "queries/q15.sql").read_text()
        self.assertIn(MODULE.Q15_ORIGINAL_REVENUE, upstream)
        for token in (None, TOKEN):
            sql = MODULE.render_query(15, token)
            self.assertIn(MODULE.Q15_EXACT_REVENUE, sql)
            self.assertNotIn(MODULE.Q15_ORIGINAL_REVENUE, sql)
            self.assertIn("total_revenue = (SELECT max(total_revenue) FROM revenue0)", sql)
        self.assertTrue(MODULE.verify_inputs())

    def test_result_comparison_respects_float_tolerance_and_duplicate_rows(self):
        expected = result([[1, 123.5], [1, 123.5], [2, 800.1]], ["id", "value"], ["bigint", "double"])
        actual = result([[2, 800.10000001], [1, 123.50000001], [1, 123.5]], ["id", "value"], ["bigint", "double"])
        self.assertTrue(MODULE.compare_results(expected, actual)["matched"])
        actual["rows"][0][1] = 810
        with self.assertRaises(MODULE.BenchmarkError):
            MODULE.compare_results(expected, actual)
        actual["rows"] = [[1, 123.5], [2, 800.1], [2, 800.1]]
        with self.assertRaisesRegex(MODULE.BenchmarkError, "multiplicity"):
            MODULE.compare_results(expected, actual)

    def test_result_comparison_keeps_integral_values_exact(self):
        with self.assertRaises(MODULE.BenchmarkError):
            MODULE.compare_results(result([[10**12]]), result([[10**12 + 1]]))
        with self.assertRaises(MODULE.BenchmarkError):
            MODULE.compare_results(result([[float("nan")]], types=["double"]), result([[float("nan")]], types=["double"]))
        with self.assertRaises(MODULE.BenchmarkError):
            MODULE.compare_results(result([[1]]), result([[1]], columns=["different"]))

    def test_result_comparison_rejects_cross_types_and_missing_types(self):
        for reference, actual in [
            (result([[1.0]], types=["double"]), result([["1.0"]], types=["varchar"])),
            (result([[1]], types=["bigint"]), result([[True]], types=["boolean"])),
            (result([[1]], types=["bigint"]), result([[True]], types=["bigint"])),
        ]:
            with self.assertRaisesRegex(MODULE.BenchmarkError, "type"):
                MODULE.compare_results(reference, actual)
        for key in ("reference", "actual"):
            values = {"reference": result([[1]]), "actual": result([[1]])}
            values[key].pop("column_types")
            with self.assertRaisesRegex(MODULE.BenchmarkError, "incomplete"):
                MODULE.compare_results(**values)
        with self.assertRaisesRegex(MODULE.BenchmarkError, "incomplete"):
            MODULE.compare_results(result([[1]], types=["bigint", "bigint"]), result([[1]]))

    def test_result_comparison_checks_explicit_iceberg_varchar_mapping(self):
        reference = result([["abc"], [None]], types=["varchar(3)"])
        actual = result([[None], ["abc"]], types=["varchar"])
        compared = MODULE.compare_results(reference, actual)
        self.assertTrue(compared["matched"])
        self.assertEqual(compared["type_mappings"][0]["reference"], "varchar(3)")
        actual["rows"][1] = ["abcd"]
        with self.assertRaisesRegex(MODULE.BenchmarkError, "string value"):
            MODULE.compare_results(reference, actual)
        with self.assertRaisesRegex(MODULE.BenchmarkError, "column type"):
            MODULE.compare_results(reference, result([["abc"], [None]], types=["varchar(4)"]))

    def test_drain_only_cancels_this_run_and_verifies_terminal(self):
        client = Mock(timeout=30)
        client.query.side_effect = [result([["query_123", "RUNNING", MODULE.run_user(TOKEN)]]), result([]), result([])]
        with patch.object(MODULE.time, "sleep"):
            drained = MODULE.drain_queries(client, TOKEN)
        self.assertTrue(drained["verified"])
        self.assertEqual(drained["cancelled"], ["query_123"])
        self.assertIn("WHERE \"user\" = '" + MODULE.run_user(TOKEN), client.query.call_args_list[0].args[0])
        self.assertIn("query_id => 'query_123'", client.query.call_args_list[1].args[0])

    def test_drain_rejects_foreign_user_before_cancellation(self):
        client = Mock(timeout=30)
        client.query.return_value = result([["query_123", "RUNNING", "retained-other-run"]])
        drained = MODULE.drain_queries(client, TOKEN)
        self.assertFalse(drained["verified"])
        self.assertEqual(client.query.call_count, 1)

    def test_timed_query_preserves_timeout_on_failure(self):
        client = Mock(timeout=30)
        client.query.side_effect = RuntimeError("injected")
        with self.assertRaises(RuntimeError):
            MODULE.timed_query(client, "SELECT 1", MODULE.time.monotonic() + 1)
        self.assertEqual(client.timeout, 30)

    def test_profile_disk_budget_parser(self):
        text = "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/vdb1 400000000 1000000 350000000 1% /data\n"
        self.assertEqual(MODULE.disk_free_bytes(text), 350000000 * 1024)
        with self.assertRaises(MODULE.BenchmarkError):
            MODULE.disk_free_bytes("unexpected")

    def test_http_client_loopback_and_run_identity(self):
        with self.assertRaises(MODULE.TRINO.SmokeError):
            MODULE.BenchmarkClient("http://remote.example", MODULE.run_user(TOKEN))
        with self.assertRaises(MODULE.BenchmarkError):
            MODULE.BenchmarkClient("http://127.0.0.1:18080", "other-user")
        client = MODULE.BenchmarkClient("http://127.0.0.1:18080", MODULE.run_user(TOKEN), 120)
        response = Mock()
        response.read.return_value = json.dumps({"id": "query_1", "columns": [{"name": "value", "type": "bigint"}],
                                               "data": [[1]], "stats": {"state": "FINISHED", "cpuTimeMillis": 2}}).encode()
        context = Mock()
        context.__enter__ = Mock(return_value=response)
        context.__exit__ = Mock(return_value=False)
        client.opener = Mock()
        client.opener.open.return_value = context
        query = client.query("SELECT 1")
        self.assertEqual(query["rows"], [[1]])
        self.assertEqual(query["stats"]["cpuTimeMillis"], 2)
        request = client.opener.open.call_args.args[0]
        self.assertEqual(request.get_header("X-trino-user"), MODULE.run_user(TOKEN))
        self.assertEqual(request.get_header("X-trino-session"), "query_max_run_time=120s")


if __name__ == "__main__":
    unittest.main()
