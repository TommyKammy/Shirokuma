import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace as Args
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools" / "local_experiment"
sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location("bears_resident", TOOLS / "bears_resident.py")
b = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(b)


class BearsResidentTests(unittest.TestCase):
    def setUp(self):
        self.connection = patch.object(b.wr, "trino", return_value=contextlib.nullcontext("http://trino"))
        self.connection.start()
        self.addCleanup(self.connection.stop)

    def test_general_sql_and_plan_analysis(self):
        plan = {"rows": [["Fragment 0 TableScan REPLICATED PARTITIONED"]], "query_id": "new"}
        with patch.object(b, "sql", return_value=plan) as execute:
            result = b.query(Args(target="explain", sql="SELECT x FROM iceberg.other.any_table"))
        self.assertEqual(execute.call_args.args[1], "EXPLAIN (TYPE DISTRIBUTED) SELECT x FROM iceberg.other.any_table")
        self.assertEqual(len(result["analysis"]["recommendations"]), 3)

    def test_query_id_lookup_escapes_input_and_retrieves_stats(self):
        stats = {"columns": [{"name": "query_id"}, {"name": "query"}], "rows": [["id", "SELECT 8"]]}
        with patch.object(b, "sql", side_effect=[stats, {"rows": [["Fragment 0"]]}]) as execute, patch.object(b.wr, "http_json", return_value={"queryStats": {}}):
            result = b.query(Args(target="id'other", sql=None))
        self.assertIn("'id''other'", execute.call_args_list[0].args[1])
        self.assertEqual(result["query_id"], "id")
        self.assertIn("execution", result["analysis"]["statistics"])

    def test_latest_missing_query(self):
        with patch.object(b, "sql", return_value={"rows": []}) as execute:
            with self.assertRaisesRegex(ValueError, "not found"):
                b.query(Args(target="latest", sql=None))
        self.assertIn("source <>", execute.call_args.args[1])

    def test_data_uses_live_sources_and_validates_generated_sql(self):
        for statement, valid in [("SELECT sum(amount) FROM iceberg.marts.custom", True), ("DELETE FROM x", False)]:
            completion = {"choices": [{"message": {"content": json.dumps({"generated_sql": statement})}}]}
            with patch.object(b.wr, "http_json", return_value=completion) as model, patch.object(b, "sql", return_value={"rows": [["marts", "custom", "amount", "double"]]}) as execute:
                result = b.data(Args(request="sum arbitrary amounts"))
            self.assertEqual(result["valid"], valid)
            self.assertIn("custom", model.call_args.args[1].decode())
            if valid:
                self.assertEqual(execute.call_args.args[1], "EXPLAIN " + statement)

    def test_invalid_sql_validation(self):
        completion = {"choices": [{"message": {"content": '{"generated_sql":"SELECT missing"}'}}]}
        with patch.object(b.wr, "http_json", return_value=completion), patch.object(b, "sql", side_effect=[{"rows": []}, RuntimeError("invalid column")]):
            self.assertFalse(b.data(Args(request="anything"))["valid"])

    def test_catalog_search_and_arbitrary_table(self):
        with patch.object(b, "sql", return_value={"rows": [["metadata"]]}) as execute, patch.object(b.wr, "kubectl", return_value="{}"):
            result = b.catalog(Args(action="search", table=None))
            self.assertIn("smoke", result["metadata"])
            result = b.catalog(Args(action="describe", table="custom.strange"))
        self.assertIn("snapshots", result["metadata"])
        self.assertIn('"iceberg"."custom"."strange$snapshots"', execute.call_args.args[1])

    def test_policy_manifest_and_baseline(self):
        container = {"name": "app", "securityContext": {"readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}}, "resources": {"limits": {"cpu": "1", "memory": "1Gi"}}}
        spec = {"securityContext": {"runAsNonRoot": True, "runAsUser": 1000}, "containers": [container]}
        workload = {"metadata": {"name": "custom"}, "spec": {"template": {"spec": spec}}}
        with patch.object(b.wr, "kubectl", return_value=json.dumps(workload)) as kubectl:
            self.assertEqual(b.policy(Args(action="check", manifest="arbitrary.yaml"))["advisory"], "allow")
            self.assertIn("--dry-run=client", kubectl.call_args.args)
        spec["initContainers"] = [{"name": "unsafe"}]
        with patch.object(b.wr, "kubectl", return_value=json.dumps({"items": [workload]})):
            result = b.policy(Args(action="baseline"))
        self.assertEqual(result["advisory"], "deny")
        self.assertEqual(len(result["violations"]), 3)

    def test_telemetry_on_every_bear_and_failure(self):
        for name, identity in b.NAMES.items():
            with patch.object(b, name, return_value={}), patch.object(b.wr, "telemetry", return_value="recorded") as telemetry:
                self.assertEqual(b.run(Args(bear=name))["bear"], identity)
                self.assertEqual(telemetry.call_args.args[0], "bear." + identity)
                self.assertEqual(telemetry.call_args.kwargs, {"prefix": ""})
        with patch.object(b, "sre", side_effect=RuntimeError("offline")), patch.object(b.wr, "telemetry", return_value="skipped") as telemetry:
            result = b.run(Args(bear="sre"))
            self.assertEqual(result["telemetry"], "skipped")
            self.assertEqual(telemetry.call_args.args[2], "failed")

    def test_main_clean_json(self):
        out = io.StringIO()
        with patch.object(sys, "argv", ["bears", "query", "--sql", "SELECT 42"]), patch.object(b, "query", return_value={}), patch.object(b.wr, "telemetry", return_value="recorded"), contextlib.redirect_stdout(out):
            self.assertEqual(b.main(), 0)
        self.assertEqual(json.loads(out.getvalue())["bear"], "query_doctor")

    def test_sre_readiness_and_observability_failure(self):
        obj = {"kind": "Deployment", "metadata": {"name": "agenthouse", "namespace": "telemetry"}, "spec": {"replicas": 1}, "status": {"readyReplicas": 1}}
        loki = dict(obj, metadata={"name": "loki", "namespace": "telemetry"})
        def kubectl(*args):
            if args[:2] == ("get", "deploy,sts"):
                return json.dumps({"items": [obj, loki]})
            if args[0] == "get" and args[1] != "--raw":
                return json.dumps(obj)
            if "--raw" in args:
                return "{}"
            return "metrics or logs"
        with patch.object(b.wr, "kubectl", side_effect=kubectl), patch("agenthouse.manage.Client") as client:
            client.return_value.query.return_value = "[]"
            self.assertEqual(b.sre(Args())["status"], "healthy")
            client.return_value.query.side_effect = RuntimeError("ClickHouse unavailable")
            self.assertEqual(b.sre(Args())["status"], "degraded")


if __name__ == "__main__":
    unittest.main()
