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
            responses = [{'rows': columns}, {'rows': [[3, 0, 2] if columns else [3]]}]
            responses.extend([{'rows': [[1, 'x']]}, {'rows': [[123]]}])
            with patch.object(b, 'sql', side_effect=responses) as execute:
                audit = b.audit_table('url', 'iceberg.sandbox.custom')
            self.assertEqual(audit['row_count'], 3)
            self.assertEqual(audit['column_count'], len(columns))
            self.assertEqual(audit['null_counts'], {'a': 0, 'dotted.col': 2} if columns else {})
            self.assertEqual(audit['snapshot_id'], 123)
            self.assertTrue(audit['quality_passed'])
            self.assertEqual(execute.call_count, 4)
            self.assertEqual(execute.call_args_list[0].args[1], 'SHOW COLUMNS FROM "iceberg"."sandbox"."custom"')
            self.assertEqual(execute.call_args_list[2].args[1], 'SELECT * FROM "iceberg"."sandbox"."custom" LIMIT 5')
            self.assertIn('"custom$snapshots"', execute.call_args.args[1])
            if columns:
                self.assertEqual(execute.call_args_list[1].args[1],
                                 'SELECT COUNT(*), COUNT(*) - COUNT("a"), COUNT(*) - COUNT("dotted.col") FROM "iceberg"."sandbox"."custom"')

    def test_audit_empty_table_still_samples_and_escapes_columns(self):
        with patch.object(b, 'sql', side_effect=[{'rows': [['a"b', 'bigint']]},
                 {'rows': [[0, 0]]}, {'rows': []}, {'rows': []}]) as execute:
            audit = b.audit_table('url', 'iceberg.sandbox.empty')
        self.assertEqual(execute.call_count, 4)
        self.assertIn('COUNT("a""b")', execute.call_args_list[1].args[1])
        self.assertEqual(audit['sample_rows'], [])
        self.assertFalse(audit['quality_passed'])

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
                with patch.object(b, 'audit_table', side_effect=[{'row_count': 3, 'null_counts': {}, 'columns': []}, {'snapshot_id': 456}]), \
                     patch.object(b, 'latest_snapshot', side_effect=RuntimeError('not found') if previous is None else None, return_value=previous), \
                     patch.object(b, 'sql', side_effect=[{'rows': [[3]]}, {'rows': []}, {'rows': []}]
                                  if previous is not None else [{'rows': []}]) as execute:
                    result = b.data(Args(publish=name))
                self.assertEqual(result['previous_snapshot_id'], previous)
                self.assertEqual(result['published_snapshot_id'], 456)
                self.assertEqual(result['wap_action'], 'publish')
                self.assertTrue(result['valid'])
                self.assertEqual(execute.call_count, 3 if previous is not None else 1)
                self.assertEqual(execute.call_args.args, ('http://trino', 'CREATE OR REPLACE TABLE "iceberg"."marts"."custom" AS SELECT * FROM "iceberg"."sandbox"."custom"'))
        with patch.object(b, 'audit_table', return_value={'row_count': 0, 'null_counts': {}}), patch.object(b, 'sql') as execute:
            result = b.data(Args(publish='custom'))
            self.assertFalse(result['valid'])
            self.assertIn('row_count must be > 0', result['error'])
            execute.assert_not_called()

    def test_publish_regression_guard(self):
        for count, target_cols, allow_shrink, valid, error in (
                (49, ['id'], False, False, '>50% shrink'),
                (49, ['id'], True, True, ''),
                (50, ['id'], False, True, ''),
                (100, ['id', 'region'], False, False, 'missing columns region'),
                (100, ['id', 'region'], True, True, '')):
            source = dict(row_count=count, columns=[{'name': 'id'}], null_counts={'id': 0})
            def execute(_url, statement):
                if statement.startswith('SELECT COUNT(*)'):
                    return {'rows': [[100]]}
                if statement.startswith('SHOW COLUMNS'):
                    return {'rows': [[c] for c in target_cols]}
                return {'rows': []}
            with self.subTest(count=count, target_cols=target_cols, allow_shrink=allow_shrink), \
                 patch.object(b, 'audit_table', side_effect=[source, {'snapshot_id': 456}]), \
                 patch.object(b, 'latest_snapshot', return_value=123), \
                 patch.object(b, 'sql', side_effect=execute) as sql:
                result = b.data(Args(publish='custom', allow_shrink=allow_shrink))
            self.assertEqual(result['valid'], valid)
            self.assertEqual(result['previous_snapshot_id'], 123)
            writes = [c for c in sql.call_args_list if c.args[1].startswith('CREATE')]
            self.assertEqual(len(writes), int(valid))
            if error:
                self.assertIn(error, result['error'])

    def test_publish_quality_assertions(self):
        for rows, minimum, not_null, nulls, valid, error in (
                (2, 3, '', {}, False, '>= 3'),
                (3, 3, ' region,segment ', {'region': 0, 'segment': 0}, True, ''),
                (3, 1, 'region', {'region': 1}, False, 'not-null assertion failed: region'),
                (3, 1, 'missing', {}, False, 'not-null column missing: missing'),
                (0, -1, '', {}, False, 'row_count must be > 0'),
                (1, 0, '', {}, True, '')):
            source = dict(row_count=rows, null_counts=nulls)
            with self.subTest(rows=rows, minimum=minimum, not_null=not_null), \
                 patch.object(b, 'audit_table', side_effect=[source, {'snapshot_id': 456}]), \
                 patch.object(b, 'latest_snapshot', return_value=None) as snapshot, \
                 patch.object(b, 'sql') as sql:
                result = b.data(Args(publish='t', min_rows=minimum, not_null=not_null, allow_shrink=True))
            self.assertEqual(result['valid'], valid)
            self.assertEqual(source['quality_passed'], valid)
            if error:
                self.assertIn(error, result['error'])
                sql.assert_not_called()
                snapshot.assert_not_called()

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
        with patch.object(sys, 'argv', ['bears', 'data', '--sql', 'SELECT 1', '--stage-table', 't', '--publish', 't', '--rollback', 't', '--snapshot-id', '12', '--allow-shrink', '--min-rows', '10', '--not-null', 'region,segment']), \
             patch.object(b, 'run', return_value={}) as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(b.main(), 0)
        args = run.call_args.args[0]
        self.assertEqual((args.request, args.sql, args.stage_table, args.publish, args.rollback, args.snapshot_id), ('', 'SELECT 1', 't', 't', 't', '12'))
        self.assertEqual((args.allow_shrink, args.min_rows, args.not_null), (True, 10, 'region,segment'))

    def test_generated_sql_self_correction(self):
        sources = [['marts', 'orders', 'amount', 'double']]
        for stage in (None, 'summary'):
            completions = [{'choices': [{'message': {'content': json.dumps({'generated_sql': statement})}}]}
                           for statement in ('SELECT missing FROM iceberg.marts.orders',
                                             'SELECT amount FROM iceberg.marts.orders')]
            responses = [{'rows': sources}, RuntimeError('Column missing cannot be resolved'), {'rows': []}]
            if stage:
                responses.extend([{'rows': []}, {'rows': []}])
            with patch.object(b.wr, 'http_json', side_effect=completions) as model, \
                 patch.object(b, 'sql', side_effect=responses) as execute, \
                 patch.object(b, 'audit_table', return_value={'quality_passed': True}):
                result = b.data(Args(request='show amounts', stage_table=stage))
            self.assertTrue(result['valid'])
            self.assertTrue(result['self_corrected'])
            self.assertEqual(result['generated_sql'], 'SELECT amount FROM iceberg.marts.orders')
            self.assertEqual(execute.call_args_list[2].args[1], 'EXPLAIN SELECT amount FROM iceberg.marts.orders')
            self.assertEqual(model.call_count, 2)
            retry = json.loads(model.call_args.args[1])
            context = json.loads(retry['messages'][1]['content'])
            self.assertEqual(context['sources'], sources)
            self.assertEqual(context['failed_sql'], 'SELECT missing FROM iceberg.marts.orders')
            self.assertIn('cannot be resolved', context['error_message'])
            self.assertIn('no QUALIFY', retry['messages'][0]['content'])

    def test_retry_is_bounded_and_does_not_retry_explicit_sql(self):
        for retry in ('SELECT missing', 'SELECT still_missing', RuntimeError('model unavailable'), '', 'DELETE FROM t'):
            with self.subTest(retry=retry), \
                 patch.object(b, 'generate_data_sql', return_value='SELECT missing'), \
                 patch.object(b, 'generate_data_sql_retry', side_effect=retry if isinstance(retry, Exception) else None,
                              return_value=retry) as model, \
                 patch.object(b, 'sql', side_effect=RuntimeError('invalid column')) as execute:
                result = b.data(Args(request='show amounts'))
            self.assertFalse(result['valid'])
            self.assertNotIn('self_corrected', result)
            model.assert_called_once()
            self.assertEqual(execute.call_count, 2 if retry == 'SELECT still_missing' else 1)
        with patch.object(b, 'generate_data_sql_retry') as model, \
             patch.object(b, 'sql', side_effect=RuntimeError('invalid column')):
            self.assertFalse(b.data(Args(sql='SELECT missing'))['valid'])
        model.assert_not_called()

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
