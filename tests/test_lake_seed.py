import importlib.util
import io
import json
from contextlib import ExitStack, nullcontext
from pathlib import Path
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "lake_seed", Path(__file__).resolve().parents[1] / "tools/local_experiment/lake_seed.py")
lake = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lake)
w = lake.warehouse_resident


class LakeSeedTests(unittest.TestCase):
    def setup_lab(self, existing=None, replicas=1, schema=True):
        self.existing = dict(existing or {})
        self.statements = []

        def execute(url, sql, headers):
            self.assertEqual(url, "http://trino")
            self.statements.append(sql)
            rows = []
            if sql.startswith("SHOW SCHEMAS"):
                rows = [["tpch_tiny"], ["tpch_sf1"]] if schema else []
            elif sql.startswith("SHOW TABLES"):
                rows = [[table] for table in self.existing]
            elif sql.startswith("CREATE TABLE"):
                table = sql.split()[2].split(".")[-1]
                self.assertNotIn(table, self.existing)
                self.existing[table] = 5
            elif sql.startswith("SELECT count(*)"):
                table = sql.split()[-1].split(".")[-1]
                rows = [[5 if "FROM tpch." in sql else self.existing[table]]]
            return dict(rows=rows, truncated=False, columns=[], query_id="q")

        stack = ExitStack()
        self.addCleanup(stack.close)
        self.execute = stack.enter_context(patch.object(w, "execute", side_effect=execute))
        self.trino = stack.enter_context(patch.object(w, "trino", return_value=nullcontext("http://trino")))
        stack.enter_context(patch.object(w, "status", return_value={"replicas": replicas}))
        self.wake = stack.enter_context(patch.object(w, "transition"))
        self.telemetry = stack.enter_context(patch.object(w, "telemetry"))

    def command(self, action, scale="tiny"):
        with patch.object(lake.sys, "argv", ["lake", action, "--scale", scale]), \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            code = lake.main()
        report = json.loads(output.getvalue())
        self.assertEqual(report["schema"], f"iceberg.tpch_{scale}")
        self.assertEqual([t["table"] for t in report["tables"]], list(lake.TABLES))
        self.assertEqual(report["scale"], scale)
        self.assertIsInstance(report["duration_ms"], int)
        self.assertFalse(any(word in sql.upper() for sql in self.statements
                             for word in ("DROP", "DELETE", "OVERWRITE")))
        return code, report

    def test_seed_created_then_present_in_one_session_per_command(self):
        self.setup_lab(replicas=0)
        code, report = self.command("seed")
        self.assertEqual(code, 0)
        self.assertEqual(report["result"], "ready")
        self.assertEqual({t["state"] for t in report["tables"]}, {"created"})
        self.assertTrue(all(t["rows"] == t["source_rows"] == 5 for t in report["tables"]))
        self.assertEqual([sql.split()[2].split(".")[-1] for sql in self.statements
                          if sql.startswith("CREATE TABLE")], list(lake.TABLES))
        self.trino.assert_called_once()
        self.wake.assert_called_once_with("wake", "LOCAL")
        self.telemetry.assert_called_once_with("lake.seed", report["duration_ms"], "success", prefix="")
        self.statements.clear()
        code, report = self.command("seed")
        self.assertEqual(code, 0)
        self.assertEqual({t["state"] for t in report["tables"]}, {"present"})
        self.assertFalse(any(sql.startswith("CREATE TABLE") for sql in self.statements))
        self.assertEqual(self.trino.call_count, 2)

    def test_seed_mixed_existing_and_missing_with_mismatch(self):
        self.setup_lab({"region": 2, "nation": 5})
        code, report = self.command("seed", "sf1")
        self.assertEqual(code, 1)
        self.assertEqual(report["result"], "incomplete")
        self.assertEqual([t["state"] for t in report["tables"]], ["mismatch", "present"] + ["created"] * 6)
        self.assertEqual(report["tables"][0]["rows"], 2)
        self.wake.assert_not_called()
        self.telemetry.assert_called_once_with("lake.seed", report["duration_ms"], "failed", prefix="")

    def test_status_present_mismatch_missing_and_absent_schema(self):
        for existing, schema in [({t: 5 for t in lake.TABLES}, True),
                                 ({"region": 2, "nation": 5}, True), ({}, False)]:
            with self.subTest(existing=existing, schema=schema):
                self.setup_lab(existing, schema=schema)
                code, report = self.command("status")
                self.assertEqual(code, int(len(existing) != 8))
                self.assertFalse(any(sql.startswith("CREATE") for sql in self.statements))
                for table in report["tables"]:
                    count = existing.get(table["table"])
                    self.assertEqual(table["state"], "missing" if count is None else
                                     "present" if count == 5 else "mismatch")
                    self.assertEqual(table["rows"], count)
                self.wake.assert_not_called()
                self.telemetry.assert_called_once()

    def test_sql_failure_reports_incomplete_and_one_failed_pawprint(self):
        self.setup_lab()
        self.execute.side_effect = RuntimeError("SQL failed")
        with patch("sys.stderr", new_callable=io.StringIO):
            report = lake.run("seed")
        self.assertEqual(report["result"], "incomplete")
        self.telemetry.assert_called_once_with("lake.seed", report["duration_ms"], "failed", prefix="")
