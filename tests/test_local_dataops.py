from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location(
    "local_dataops", Path(__file__).resolve().parents[1] / "tools/local_experiment/dataops/run.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
TOKEN = "a" * 32


class FakeTrino:
    url = "http://127.0.0.1:18080"
    timeout = 120

    def __init__(self):
        self.tables = {"retained_other": "not-owned"}
        self.queries = []
        self.drop_error = None
        self.fixture_reads = 0
        self.changed_fixture = False
        self.active_queries = []
        self.drain_error = None

    def query(self, sql):
        self.queries.append(sql)
        if 'FROM system.runtime.queries' in sql:
            if self.drain_error:
                raise MODULE.DataOpsError(self.drain_error)
            return {"rows": self.active_queries.copy()}
        if sql.startswith("CALL system.runtime.kill_query"):
            query_id = sql.split("query_id => '", 1)[1].split("'", 1)[0]
            self.active_queries = [row for row in self.active_queries if row[0] != query_id]
            return {"rows": []}
        if sql == MODULE.FIXTURE_SQL:
            self.fixture_reads += 1
            rows = MODULE.FIXTURE_ROWS
            if self.changed_fixture and self.fixture_reads > 1:
                rows = [[1, "changed"]]
            return {"rows": rows}
        if sql.startswith("SHOW TABLES"):
            return {"rows": [[name] for name in self.tables]}
        if sql.startswith("SHOW CREATE TABLE"):
            name = sql.rsplit(".", 1)[1]
            marker = self.tables[name]
            return {"rows": [[f"CREATE TABLE {name} COMMENT '{marker}'"]]}
        if sql.startswith("DROP TABLE"):
            if self.drop_error:
                raise MODULE.DataOpsError(self.drop_error)
            del self.tables[sql.rsplit(".", 1)[1]]
            return {"rows": []}
        if sql.startswith("SELECT id, name_upper"):
            return {"rows": MODULE.MART_ROWS}
        raise AssertionError(sql)


class LocalDataOpsTests(unittest.TestCase):
    def output(self, directory):
        return Path(directory) / "new-run"

    def materializer(self, client):
        def run(attempt, profiles, token, fail, timeout, env):
            for name in MODULE.names_for(token):
                client.tables[name] = f"shirokuma-local-dataops:{token}"
            return {"success": not fail, "materializations": 0 if fail else 1,
                    "commands": [{"command": "build", "exit_code": 1 if fail else 0, "nodes": [{
                        "unique_id": "test.shirokuma_local_dataops.mart_expected_rows",
                        "status": "fail" if fail else "pass", "failures": 1 if fail else 0,
                    }]}]}
        return run

    def test_repeat_failure_retry_and_owned_cleanup(self):
        client = FakeTrino()
        with tempfile.TemporaryDirectory() as directory:
            output = self.output(directory)
            result = MODULE.run_experiment(client, output, True, token=TOKEN,
                                           materializer=self.materializer(client))
            self.assertEqual(result["result"], "passed", result)
            self.assertEqual([x["label"] for x in result["attempts"]],
                             ["first", "repeat", "controlled-failure", "retry"])
            self.assertTrue(result["deterministic"])
            self.assertTrue(result["fixture_unchanged"])
            self.assertTrue(result["controlled_failure_observed"])
            self.assertEqual(len(result["cleanup"]["removed"]), 2)
            self.assertEqual(client.tables, {"retained_other": "not-owned"})
            self.assertEqual(json.loads((output / "summary.json").read_text()), result)
            self.assertEqual(output.stat().st_mode & 0o777, 0o700)

    def test_collision_never_materializes_or_cleans_existing_data(self):
        client = FakeTrino()
        name = MODULE.names_for(TOKEN)[0]
        client.tables[name] = "retained-data"
        materializer = Mock()
        with tempfile.TemporaryDirectory() as directory:
            result = MODULE.run_experiment(client, self.output(directory), token=TOKEN,
                                           materializer=materializer)
        self.assertEqual(result["result"], "failed")
        self.assertIn("collision", result["errors"][0])
        materializer.assert_not_called()
        self.assertFalse(any(sql.startswith("DROP") for sql in client.queries))
        self.assertIn(name, client.tables)

    def test_cleanup_refuses_unmarked_replacement(self):
        client = FakeTrino()
        def replace(*args):
            client.tables[MODULE.names_for(TOKEN)[0]] = "another-writer"
            raise MODULE.DataOpsError("injected original failure")
        with tempfile.TemporaryDirectory() as directory:
            result = MODULE.run_experiment(client, self.output(directory), token=TOKEN,
                                           materializer=replace)
        self.assertEqual(result["result"], "failed")
        self.assertIn("injected original failure", result["errors"])
        self.assertTrue(any("ownership marker missing" in error for error in result["errors"]))
        self.assertFalse(any(sql.startswith("DROP") for sql in client.queries))

    def test_partial_creation_cleans_marked_relation_and_preserves_failure(self):
        client = FakeTrino()
        def fail(*args):
            client.tables[MODULE.names_for(TOKEN)[0]] = f"shirokuma-local-dataops:{TOKEN}"
            raise MODULE.DataOpsError("dbt stopped after CTAS")
        with tempfile.TemporaryDirectory() as directory:
            result = MODULE.run_experiment(client, self.output(directory), token=TOKEN,
                                           materializer=fail)
        self.assertIn("dbt stopped after CTAS", result["errors"])
        self.assertEqual(len(result["cleanup"]["removed"]), 1)
        self.assertEqual(client.tables, {"retained_other": "not-owned"})

    def test_interruption_cleans_marked_relation_and_records_failure(self):
        client = FakeTrino()
        def interrupted(*args):
            client.tables[MODULE.names_for(TOKEN)[0]] = f"shirokuma-local-dataops:{TOKEN}"
            raise KeyboardInterrupt()
        with tempfile.TemporaryDirectory() as directory:
            output = self.output(directory)
            result = MODULE.run_experiment(client, output, token=TOKEN, materializer=interrupted)
            self.assertEqual(result["result"], "failed")
            self.assertIn("interrupted", result["errors"][0])
            self.assertTrue((output / "summary.json").exists())
        self.assertEqual(client.tables, {"retained_other": "not-owned"})

    def test_cleanup_failure_does_not_hide_materialization_failure(self):
        client = FakeTrino()
        def fail(*args):
            client.tables[MODULE.names_for(TOKEN)[0]] = f"shirokuma-local-dataops:{TOKEN}"
            client.drop_error = "purge denied"
            raise MODULE.DataOpsError("build failed")
        with tempfile.TemporaryDirectory() as directory:
            result = MODULE.run_experiment(client, self.output(directory), token=TOKEN,
                                           materializer=fail)
        self.assertIn("build failed", result["errors"])
        self.assertIn("purge denied", result["errors"])
        self.assertEqual(result["cleanup"]["remaining"], [MODULE.names_for(TOKEN)[0]])

    def test_unrelated_failure_is_not_accepted_as_injected_test_failure(self):
        failed = {"success": False, "commands": [{"command": "build", "exit_code": 1, "nodes": [{
            "unique_id": "model.shirokuma_local_dataops.stg_fixture", "status": "error",
            "failures": 1}]}]}
        self.assertFalse(MODULE.expected_test_failure(failed))
        failed["commands"][0]["nodes"][0].update({
            "unique_id": "test.shirokuma_local_dataops.mart_expected_rows", "status": "error"})
        self.assertFalse(MODULE.expected_test_failure(failed))

    def test_controlled_failure_requires_normal_dbt_build_exit_one(self):
        failed = {"success": False, "commands": [{"command": "build", "nodes": [{
            "unique_id": "test.shirokuma_local_dataops.mart_expected_rows",
            "status": "fail", "failures": 1}]}]}
        for exit_code in (None, 0, 2, 137, -15):
            failed["commands"][0]["exit_code"] = exit_code
            self.assertFalse(MODULE.expected_test_failure(failed), exit_code)
        failed["commands"][0]["exit_code"] = 1
        self.assertTrue(MODULE.expected_test_failure(failed))
        failed["commands"][0]["process_error"] = "timed out after artifact was written"
        self.assertFalse(MODULE.expected_test_failure(failed))

    def test_server_queries_are_cancelled_and_drained_before_table_cleanup(self):
        client = FakeTrino()
        original_query = client.query
        def query(sql):
            if sql.startswith("CALL system.runtime.kill_query"):
                # A CTAS can commit after the local process exits but before its
                # server query terminates. Cleanup must see this late table.
                client.tables[MODULE.names_for(TOKEN)[0]] = f"shirokuma-local-dataops:{TOKEN}"
            return original_query(sql)
        client.query = query
        def interrupted(*args):
            client.active_queries = [["20260916_00001_abc", "RUNNING", MODULE.run_user(TOKEN)]]
            raise MODULE.DataOpsError("dbt process timeout")
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(MODULE.time, "sleep"):
                result = MODULE.run_experiment(client, self.output(directory), token=TOKEN,
                                               materializer=interrupted)
        self.assertEqual(result["result"], "failed")
        self.assertTrue(result["query_drain"]["verified"])
        self.assertEqual(result["query_drain"]["cancelled"], ["20260916_00001_abc"])
        self.assertEqual(len(result["cleanup"]["removed"]), 1)
        self.assertEqual(client.tables, {"retained_other": "not-owned"})
        kill_index = next(i for i, sql in enumerate(client.queries) if sql.startswith("CALL"))
        drop_index = next(i for i, sql in enumerate(client.queries) if sql.startswith("DROP"))
        self.assertLess(kill_index, drop_index)

    def test_failed_drain_defers_cleanup_and_preserves_tables(self):
        client = FakeTrino()
        def failed(*args):
            client.tables[MODULE.names_for(TOKEN)[0]] = f"shirokuma-local-dataops:{TOKEN}"
            client.drain_error = "query monitoring unavailable"
            raise MODULE.DataOpsError("original timeout")
        with tempfile.TemporaryDirectory() as directory:
            result = MODULE.run_experiment(client, self.output(directory), token=TOKEN,
                                           materializer=failed)
        self.assertEqual(result["cleanup"]["status"], "deferred")
        self.assertFalse(result["query_drain"]["verified"])
        self.assertIn("original timeout", result["errors"])
        self.assertIn(MODULE.names_for(TOKEN)[0], client.tables)
        self.assertFalse(any(sql.startswith("DROP") for sql in client.queries))

    def test_drain_refuses_to_cancel_another_runs_query(self):
        client = FakeTrino()
        client.active_queries = [["20260916_00001_abc", "RUNNING", "another-user"]]
        result = MODULE.drain_queries(client, TOKEN)
        self.assertFalse(result["verified"])
        self.assertIn("unexpected identity", result["errors"][0])
        self.assertFalse(any(sql.startswith("CALL") for sql in client.queries))

    def test_exhausted_drain_budget_never_claims_verified(self):
        client = FakeTrino()
        result = MODULE.drain_queries(client, TOKEN, timeout=0)
        self.assertFalse(result["verified"])
        self.assertIn("time budget exhausted", result["errors"][0])
        self.assertEqual(client.queries, [])

    def test_retained_fixture_change_is_failure(self):
        client = FakeTrino()
        client.changed_fixture = True
        with tempfile.TemporaryDirectory() as directory:
            result = MODULE.run_experiment(client, self.output(directory), token=TOKEN,
                                           materializer=self.materializer(client))
        self.assertEqual(result["result"], "failed")
        self.assertFalse(result["fixture_unchanged"])

    def test_profile_rejects_remote_url_and_fixed_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(MODULE.TRINO.SmokeError):
                MODULE.prepare_profile(Path(directory), "http://example.com:8080", TOKEN)
            profiles = MODULE.prepare_profile(Path(directory), "http://127.0.0.1:18080", TOKEN)
            parsed = json.loads((profiles / "profiles.yml").read_text())
            config = parsed["shirokuma_local_dataops"]["outputs"]["local"]
            self.assertEqual(config["database"], "iceberg")
            self.assertEqual(config["schema"], "local_experiment")
            self.assertEqual(config["host"], "127.0.0.1")
            self.assertEqual(config["port"], 18080)
            self.assertEqual(config["user"], f"shirokuma-local-dataops-{TOKEN}")
            self.assertFalse(parsed["config"]["send_anonymous_usage_stats"])

    def test_environment_does_not_inherit_proxy_or_dbt_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(MODULE.os.environ, {"HTTPS_PROXY": "http://remote", "DBT_USER": "other",
                                               "HOME": "/original-home"}):
                env = MODULE.isolated_environment(Path(directory))
        self.assertNotIn("HTTPS_PROXY", env)
        self.assertNotIn("DBT_USER", env)
        self.assertEqual(env["HOME"], "/original-home")
        self.assertEqual(env["DBT_SEND_ANONYMOUS_USAGE_STATS"], "false")
        self.assertEqual(env["NETRC"], MODULE.os.devnull)

    def test_process_timeout_terminates_and_reaps_before_reporting(self):
        process = Mock(pid=4321)
        process.wait.side_effect = [MODULE.subprocess.TimeoutExpired(["dbt"], 1), 0]
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(MODULE.subprocess, "Popen", return_value=process):
                with patch.object(MODULE.os, "killpg") as kill:
                    with self.assertRaisesRegex(MODULE.DataOpsError, "exceeded"):
                        MODULE.run_command(["dbt"], Path(directory) / "run.log", {}, 1)
        kill.assert_called_once_with(4321, MODULE.signal.SIGTERM)
        self.assertEqual(process.wait.call_count, 2)

    def test_process_interruption_reaps_before_propagating(self):
        process = Mock(pid=4321)
        process.wait.side_effect = [KeyboardInterrupt(), MODULE.subprocess.TimeoutExpired(["dbt"], 5), 0]
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(MODULE.subprocess, "Popen", return_value=process):
                with patch.object(MODULE.os, "killpg") as kill:
                    with self.assertRaises(KeyboardInterrupt):
                        MODULE.run_command(["dbt"], Path(directory) / "run.log", {}, 1)
        self.assertEqual([call.args for call in kill.call_args_list],
                         [(4321, MODULE.signal.SIGTERM), (4321, MODULE.signal.SIGKILL)])
        self.assertEqual(process.wait.call_count, 3)

    def test_command_pins_profile_and_artifacts_and_disables_telemetry(self):
        args = MODULE.dbt_command("build", Path("/attempt"), Path("/profiles"), TOKEN, True)
        self.assertEqual(args[:2], ["rtk", "proxy"])
        self.assertIn("--no-send-anonymous-usage-stats", args)
        self.assertEqual(args[args.index("--profiles-dir") + 1], "/profiles")
        self.assertEqual(args[args.index("--target-path") + 1], "/attempt/build-target")
        self.assertTrue(json.loads(args[-1])["exercise_failure"])
        with self.assertRaises(MODULE.DataOpsError):
            MODULE.names_for("arbitrary_identifier; DROP TABLE retained")


if __name__ == "__main__":
    unittest.main()
