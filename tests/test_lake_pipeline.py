import importlib.util
import io
import json
from contextlib import ExitStack, nullcontext
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "lake_pipeline", Path(__file__).resolve().parents[1] / "tools/local_experiment/lake_pipeline.py")
lake = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lake)
w = lake.warehouse_resident


class LakePipelineTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.state = stack.enter_context(patch.object(w, "status", return_value={"replicas": 1}))
        self.kubectl = stack.enter_context(patch.object(w, "kubectl"))
        self.tunnel = stack.enter_context(patch.object(w, "trino", return_value=nullcontext(
            "http://127.0.0.1:18080")))
        self.sql = stack.enter_context(patch.object(w, "execute", return_value={"rows": [[42]]}))
        self.telemetry = stack.enter_context(patch.object(w, "telemetry", return_value="recorded"))
        self.process = stack.enter_context(patch.object(lake.subprocess, "run", side_effect=self.dbt))

    def dbt(self, command, **kwargs):
        self.assertEqual(command[0], str(lake.DBT))
        self.assertEqual(command[command.index("--project-dir") + 1], str(lake.PROJECT))
        self.assertEqual(kwargs["timeout"], 900)
        self.assertFalse(any(k.upper().startswith("DBT_") for k in kwargs["env"]))
        self.profile_dir = Path(command[command.index("--profiles-dir") + 1])
        profile = json.loads((self.profile_dir / "profiles.yml").read_text())
        target = profile["shirokuma_lake_marts"]["outputs"]["local"]
        self.assertEqual((target["host"], target["port"]), ("127.0.0.1", 18080))
        self.assertEqual((target["database"], target["schema"]), ("iceberg", "marts"))
        output = Path(command[command.index("--target-path") + 1])
        output.mkdir()
        (output / "run_results.json").write_text(json.dumps({"results": [
            {"unique_id": "model.shirokuma_lake_marts.fct_orders", "status": "success"}]}))
        return subprocess.CompletedProcess(command, 0, "dbt log", "")

    def test_build_wakes_creates_schemas_and_records_once(self):
        self.state.return_value = {"replicas": 0}
        report = lake.run("build")
        self.assertEqual(report["result"], "success")
        self.assertEqual(report["telemetry"], "recorded")
        self.assertIsInstance(report["duration_ms"], int)
        self.assertEqual(report["models"][0]["status"], "success")
        self.kubectl.assert_any_call("scale", "deployment/trino-local", "--replicas=1")
        self.assertEqual([c.args[1] for c in self.sql.call_args_list], [
            "CREATE SCHEMA IF NOT EXISTS iceberg.staging",
            "CREATE SCHEMA IF NOT EXISTS iceberg.marts"])
        self.assertEqual(self.process.call_args.args[0][1], "build")
        self.telemetry.assert_called_once_with("build", report["duration_ms"], "success",
                                               prefix="lake.pipeline.")
        self.assertFalse(self.profile_dir.exists())

    def test_test_uses_dbt_test_without_schema_changes(self):
        report = lake.run("test")
        self.assertEqual(report["result"], "success")
        self.assertEqual(self.process.call_args.args[0][1], "test")
        self.sql.assert_not_called()
        self.kubectl.assert_not_called()
        self.telemetry.assert_called_once_with("test", report["duration_ms"], "success",
                                               prefix="lake.pipeline.")

    def test_status_requires_both_marts_nonempty(self):
        self.assertEqual(lake.run("status")["result"], "success")
        self.assertEqual([c.args[1] for c in self.sql.call_args_list], [
            f"SELECT count(*) FROM iceberg.marts.{m}" for m in lake.MARTS])
        self.sql.side_effect = [{"rows": [[42]]}, {"rows": [[0]]}]
        self.assertEqual(lake.run("status")["result"], "failed")
        self.telemetry.assert_not_called()
        self.process.assert_not_called()

    def test_missing_dbt_timeout_and_sql_errors_are_structured(self):
        for error in (FileNotFoundError("dbt missing"), subprocess.TimeoutExpired("dbt", 900)):
            with self.subTest(error=error):
                self.process.side_effect = error
                self.assertEqual(lake.run("test")["result"], "failed")
        self.sql.side_effect = RuntimeError("Trino unavailable")
        report = lake.run("build")
        self.assertEqual(report["error"], "Trino unavailable")
        self.assertEqual(self.telemetry.call_args.args[2], "failed")

    def test_failed_or_missing_dbt_results_fail(self):
        for status, code in (("fail", 0), ("success", 1), (None, 0)):
            def finish(command, **kwargs):
                result = self.dbt(command, **kwargs)
                artifact = self.profile_dir / "target/run_results.json"
                artifact.write_text(json.dumps({"results": [] if status is None else [
                    {"unique_id": "test.customer_key", "status": status}]}))
                result.returncode = code
                return result
            self.process.side_effect = finish
            self.assertEqual(lake.run("test")["result"], "failed")

    def test_cli_emits_one_json_and_skips_unavailable_telemetry(self):
        self.telemetry.return_value = "skipped"
        with patch.object(lake, "__name__", "lake_pipeline"), \
             patch("sys.argv", ["lake_pipeline", "build"]), \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(lake.main(), 0)
        self.assertEqual(json.loads(output.getvalue())["telemetry"], "skipped")
        self.assertLess(len(Path(lake.__file__).read_text().splitlines()), 200)


if __name__ == "__main__":
    unittest.main()
