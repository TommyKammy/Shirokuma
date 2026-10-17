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
    def test_audit_table(self):
        for columns in ([['a', 'bigint'], ['dotted.col', 'varchar']], []):
            responses = [{'rows': [[3]]}, {'rows': columns}]
            if columns:
                responses.append({'rows': [[0, 2]]})
            responses.extend([{'rows': [[1, 'x']]}, {'rows': [[123]]}])
            with patch.object(b, 'sql', side_effect=responses) as execute:
                audit = b.audit_table('url', 'iceberg.sandbox.custom')
            self.assertEqual(audit['row_count'], 3)
            self.assertEqual(audit['column_count'], len(columns))
            self.assertEqual(audit['null_counts'], {'a': 0, 'dotted.col': 2} if columns else {})
            self.assertEqual(audit['snapshot_id'], 123)
            self.assertTrue(audit['quality_passed'])
            self.assertIn('"custom$snapshots"', execute.call_args.args[1])
            if columns:
                self.assertIn('COUNT("dotted.col")', execute.call_args_list[2].args[1])

    def test_explicit_sql_stage_and_dry_run(self):
        for stage in (None, 'custom', 'iceberg.sandbox.custom', 'sandbox.custom'):
            with patch.object(b, 'sql', return_value={'rows': []}) as execute, \
                 patch.object(b, 'audit_table', return_value={'quality_passed': True}) as audit, \
                 patch.object(b.wr, 'http_json') as model:
                result = b.data(Args(request='', sql='SELECT 42; ', stage_table=stage))
            self.assertTrue(result['valid'])
            model.assert_not_called()
            if stage:
                self.assertEqual(result['staged_table'], 'iceberg.sandbox.custom')
                self.assertEqual(result['wap_action'], 'stage')
                self.assertEqual(execute.call_args_list[1].args[1], 'CREATE SCHEMA IF NOT EXISTS iceberg.sandbox')
                self.assertEqual(execute.call_args.args[1], 'CREATE OR REPLACE TABLE "iceberg"."sandbox"."custom" AS SELECT 42')
                audit.assert_called_once()
            else:
                self.assertNotIn('wap_action', result)
                execute.assert_called_once_with('http://trino', 'EXPLAIN SELECT 42')
                audit.assert_not_called()

    def test_stage_validation_and_quality_failures(self):
        for statement in ('SELECT 1; DELETE FROM x', 'WITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x', 'DELETE FROM x'):
            with patch.object(b, 'sql') as execute:
                self.assertFalse(b.data(Args(request='', sql=statement, stage_table='t'))['valid'])
                execute.assert_not_called()
        with patch.object(b, 'sql', side_effect=RuntimeError('bad syntax')) as execute:
            self.assertFalse(b.data(Args(sql='SELECT bad', stage_table='t'))['valid'])
            self.assertEqual(execute.call_count, 1)
        with patch.object(b, 'sql', return_value={}), patch.object(b, 'audit_table', return_value={'quality_passed': False}):
            self.assertFalse(b.data(Args(sql='SELECT 1', stage_table='t'))['valid'])

    def test_publish_and_empty_source(self):
        for name in ('custom', 'sandbox.custom', 'marts.custom', 'iceberg.sandbox.custom', 'iceberg.marts.custom'):
            for previous in (123, None):
                with patch.object(b, 'audit_table', side_effect=[{'quality_passed': True}, {'snapshot_id': 456}]), \
                     patch.object(b, 'latest_snapshot', side_effect=RuntimeError('not found') if previous is None else None, return_value=previous), \
                     patch.object(b, 'sql', return_value={}) as execute:
                    result = b.data(Args(publish=name))
                self.assertEqual(result['previous_snapshot_id'], previous)
                self.assertEqual(result['published_snapshot_id'], 456)
                self.assertEqual(result['wap_action'], 'publish')
                self.assertTrue(result['valid'])
                execute.assert_called_once_with('http://trino', 'CREATE OR REPLACE TABLE "iceberg"."marts"."custom" AS SELECT * FROM "iceberg"."sandbox"."custom"')
        with patch.object(b, 'audit_table', return_value={'quality_passed': False}), patch.object(b, 'sql') as execute:
            result = b.data(Args(publish='custom'))
            self.assertFalse(result['valid'])
            self.assertIn('row_count must be > 0', result['error'])
            execute.assert_not_called()

    def test_rollback_and_invalid_arguments(self):
        with patch.object(b, 'sql') as execute, patch.object(b, 'audit_table', return_value={}):
            self.assertEqual(b.data(Args(rollback='t', snapshot_id=0))['restored_snapshot_id'], 0)
        self.assertIn('rollback_to_snapshot(0)', execute.call_args.args[1])
        for name in ('custom', 'marts.custom', 'iceberg.marts.custom', 'other.custom'):
            with patch.object(b, 'sql', return_value={}) as execute, patch.object(b, 'audit_table', return_value={'snapshot_id': 12}):
                result = b.data(Args(rollback=name, snapshot_id='0012'))
            self.assertEqual(result['restored_snapshot_id'], 12)
            self.assertEqual(result['wap_action'], 'rollback')
            self.assertTrue(result['valid'])
            self.assertIn('EXECUTE rollback_to_snapshot(12)', execute.call_args.args[1])
        for args in (Args(), Args(rollback='t'), Args(rollback='t', snapshot_id='-1'),
                     Args(rollback='t', snapshot_id='1); DROP TABLE t'),
                     Args(sql='SELECT 1', stage_table='other.t'), Args(publish='t; DELETE'),
                     Args(rollback='other.catalog.schema.t', snapshot_id=12)):
            with self.subTest(args=args), patch.object(b, 'sql') as execute, self.assertRaises(ValueError):
                b.data(args)
            execute.assert_not_called()

    def test_data_parser_optional_request_and_wap_flags(self):
        with patch.object(sys, 'argv', ['bears', 'data', '--sql', 'SELECT 1', '--stage-table', 't', '--publish', 't', '--rollback', 't', '--snapshot-id', '12']), \
             patch.object(b, 'run', return_value={}) as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(b.main(), 0)
        args = run.call_args.args[0]
        self.assertEqual((args.request, args.sql, args.stage_table, args.publish, args.rollback, args.snapshot_id), ('', 'SELECT 1', 't', 't', 't', '12'))

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

    def test_llm_usage_flows_into_generated_and_staged_results(self):
        for stage in (None, 'orders_summary'):
            for metadata in ({}, {'model': 'served-model'}, {'usage': {'prompt_tokens': 0, 'completion_tokens': 12}},
                             {'model': 'served-model', 'usage': {'prompt_tokens': 25, 'completion_tokens': 8}}):
                with self.subTest(stage=stage, metadata=metadata):
                    completion = dict(metadata, choices=[{'message': {'content': '{"generated_sql":"SELECT 42"}'}}])
                    with patch.object(b.wr, 'http_json', return_value=completion), \
                         patch.object(b, 'sql', return_value={'rows': []}), \
                         patch.object(b, 'audit_table', return_value={'quality_passed': True, 'snapshot_id': 9}):
                        result = b.data(Args(request='count orders', stage_table=stage))
                    if metadata:
                        usage = result['llm_usage']
                        self.assertEqual(usage['model_provider'], 'local-openai')
                        if metadata.get('model'):
                            self.assertEqual(usage['model_name'], metadata['model'])
                        else:
                            self.assertTrue(usage['model_name'])
                        self.assertEqual(usage['prompt_tokens'], metadata.get('usage', {}).get('prompt_tokens'))
                        self.assertEqual(usage['completion_tokens'], metadata.get('usage', {}).get('completion_tokens'))
                    else:
                        self.assertNotIn('llm_usage', result)

    def test_rich_wap_telemetry(self):
        cases = [({'staged_table': 'iceberg.sandbox.t', 'audit': {'snapshot_id': 0}, 'wap_action': 'stage'},
                  {'target': 'iceberg.sandbox.t@0'}),
                 ({'table': 'iceberg.marts.t', 'published_snapshot_id': 42, 'audit': {'snapshot_id': 1}, 'wap_action': 'publish'},
                  {'target': 'iceberg.marts.t@42', 'risk_level': 'normal'}),
                 ({'table': 'iceberg.marts.t', 'restored_snapshot_id': 7, 'wap_action': 'rollback'},
                  {'target': 'iceberg.marts.t@7', 'risk_level': 'normal'}),
                 ({'table': 'x' * 300, 'audit': None}, {'target': 'x' * 256}),
                 ({'llm_usage': {'model_provider': 'local-openai', 'model_name': 'test', 'prompt_tokens': 0, 'completion_tokens': 8}},
                  {'model_provider': 'local-openai', 'model_name': 'test', 'prompt_tokens': 0, 'completion_tokens': 8}),
                 ({'llm_usage': {'model_name': '', 'prompt_tokens': True, 'completion_tokens': -1}}, {}),
                 ({'llm_usage': {'prompt_tokens': 1.5, 'completion_tokens': None}}, {})]
        for report, expected in cases:
            with self.subTest(report=report), patch.object(b, 'data', return_value=dict(report)), \
                 patch.object(b.wr, 'telemetry', return_value='recorded') as telemetry:
                b.run(Args(bear='data'))
                self.assertEqual(telemetry.call_args.kwargs, dict(prefix='', **expected))

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
