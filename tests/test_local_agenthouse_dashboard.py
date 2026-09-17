"""Dashboard SQL semantics without a Grafana/ClickHouse runtime dependency.

The small SQLite fixture executes the provisioned SELECTs after replacing only
Grafana time macros and ClickHouse FINAL, and adapting null-function names.
FINAL and the plugin format enum have separate contract checks; live
ClickHouse probes cover their engine behavior.
"""
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import unittest
from urllib.parse import quote, unquote


ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "tools/local_experiment/grafana"
SPEC = importlib.util.spec_from_file_location("agenthouse_dashboard", HERE / "dashboard.py")
DASHBOARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DASHBOARD)


class CountIf:
    def __init__(self):
        self.count = 0

    def step(self, condition):
        self.count += bool(condition)

    def finalize(self):
        return self.count


def interpolate(sql, task="", trace=""):
    # Grafana 13.2.2 uses scenes 8.13.5 encodeURIComponentStrict, RFC 3986:
    # https://github.com/grafana/scenes/blob/v8.13.5/packages/scenes/src/variables/interpolation/formatRegistry.ts
    for name, value in (("task", task), ("trace", trace)):
        sql = sql.replace("${" + name + ":percentencode}", quote(value, safe="-._~"))
    if "${" in sql:
        raise AssertionError("dashboard contains an unreviewed variable formatter")
    return sql.replace("$__timeFilter_ms(timestamp)",
        "timestamp >= '2026-09-17T00:00:00Z' AND timestamp < '2026-09-18T00:00:00Z'").replace(
        "$__timeInterval_ms(timestamp)", "substr(timestamp, 1, 13)").replace(
        "FROM agenthouse.pawprints FINAL", "FROM agenthouse.pawprints").replace(
        "isNull(", "is_null(").replace("isNotNull(", "is_not_null(")


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("ATTACH DATABASE ':memory:' AS agenthouse")
        self.db.execute("""CREATE TABLE agenthouse.pawprints (
            event_id TEXT PRIMARY KEY, timestamp TEXT, task_id TEXT, trace_id TEXT,
            action_type TEXT, target_system TEXT, status TEXT, blocked_by_policy INTEGER,
            model_provider TEXT, model_name TEXT, duration_ms INTEGER,
            prompt_tokens INTEGER, completion_tokens INTEGER, inference_cost_estimate REAL)""")
        self.db.create_function("decodeURLComponent", 1, unquote)
        self.db.create_function("if", 3, lambda condition, yes, no: yes if condition else no)
        self.db.create_function("is_null", 1, lambda value: value is None)
        self.db.create_function("is_not_null", 1, lambda value: value is not None)
        self.db.create_aggregate("countIf", 1, CountIf)
        records = [
            ("a", "task-a", "trace-a", "llm.plan", "success", "mtplx", "qwen", 100, 10, 3),
            ("b", "task-a", "trace-a", "llm.plan", "failed", "mtplx", "qwen", 200, None, None),
            ("c", "task-b", "trace-b", "llm.plan", "success", "mtplx", "qwen", 0, 0, 0),
            ("d", "task-mock", "trace-mock", "llm.plan", "success", "shirokuma-mock", "mock", 0, None, None),
            ("e", "task-a", "trace-a", "bear.assess", "success", "none", "", 700, None, None),
            ("f", "task-other", "trace-other", "llm.plan", "success", "mtplx", "other", 50, 7, 2),
            ("g", "task-a", "trace-a", "tool.fixed", "blocked", "none", "", 5, None, None),
        ]
        for eid, task, trace, action, status, provider, model, duration, prompt, completion in records:
            self.db.execute("INSERT INTO agenthouse.pawprints VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                eid, "2026-09-17T06:00:00Z", task, trace, action, "local", status,
                int(status == "blocked"), provider, model, duration, prompt, completion, None))
        self.db.execute("INSERT INTO agenthouse.pawprints VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "outside", "2026-09-16T06:00:00Z", "task-a", "trace-a", "llm.plan", "local",
            "success", 0, "mtplx", "qwen", 999, 999, 999, None))

    def tearDown(self):
        self.db.close()

    def query(self, name, task="", trace=""):
        return [dict(row) for row in self.db.execute(interpolate(DASHBOARD.QUERIES[name], task, trace))]

    def test_generated_artifact_matches_builder_and_uses_correct_plugin_format(self):
        value = DASHBOARD.dashboard()
        self.assertEqual(json.loads((HERE / "dashboards/agenthouse.json").read_text()), value)
        targets = [panel for panel in value["panels"] if "targets" in panel]
        self.assertEqual(len(targets), 5)
        for panel in targets:
            target = panel["targets"][0]
            # sqlds FormatOptionTimeSeries=0 and FormatOptionTable=1 (iota).
            self.assertEqual(target["format"], 0 if panel["type"] == "timeseries" else 1)
            self.assertEqual(target["queryType"], panel["type"])
            self.assertEqual(target["pluginVersion"], "4.21.3")
            self.assertEqual(target["datasource"]["uid"], "shirokuma-agenthouse-readonly")
        self.assertFalse(value["editable"])

    def test_all_queries_deduplicate_pawprints_and_remain_bounded_selects(self):
        for name, sql in DASHBOARD.QUERIES.items():
            with self.subTest(name=name):
                self.assertTrue(sql.startswith("SELECT "))
                self.assertEqual(re.findall(r"\bFROM\s+(\S+)\s+FINAL\b", sql), ["agenthouse.pawprints"])
                self.assertNotRegex(sql.upper(), r"\b(?:INSERT|DELETE|ALTER|DROP|CREATE|TRUNCATE|SETTINGS|UNION)\b|;")
                self.assertRegex(sql, r"LIMIT [1-9][0-9]{0,3}$")
                self.assertIn("$__timeFilter_ms(timestamp)", sql)

    def test_task_and_trace_are_exact_intersecting_filters(self):
        self.assertEqual(len(self.query("timeline")), 7)
        self.assertEqual(len(self.query("timeline", task="task-a")), 4)
        self.assertEqual(len(self.query("timeline", trace="trace-b")), 1)
        self.assertEqual(self.query("timeline", task="task-a", trace="trace-b"), [])
        self.assertEqual(self.query("timeline", task="task"), [])

    def test_encoded_variables_cannot_escape_sql_for_quotes_backslashes_or_macros(self):
        hostile = ["\\' IS NOT NULL) OR 1=1 -- ", "' OR 1=1 --", "$filter$", "${trace:raw}",
                   "O'Reilly", "日本語Task", "%27", "\\", "\n"]
        for value in hostile:
            with self.subTest(value=value):
                encoded = quote(value, safe="-._~")
                self.assertNotRegex(encoded, r"['\\$\n\x00]")
                self.assertEqual(unquote(encoded), value)
                self.assertEqual(self.query("timeline", task=value), [])
                self.assertEqual(self.query("timeline", trace=value), [])
        for sql in DASHBOARD.QUERIES.values():
            for variable in ("task", "trace"):
                self.assertIn("decodeURLComponent('${" + variable + ":percentencode}')", sql)
            self.assertNotIn(":singlequote}", sql)
            self.assertNotIn(":raw}", sql)

    def test_usage_separates_models_and_keeps_unknown_distinct_from_measured_zero(self):
        rows = {(row["model_provider"], row["model_name"]): row for row in self.query("model_usage")}
        self.assertEqual(set(rows), {("mtplx", "qwen"), ("mtplx", "other"), ("shirokuma-mock", "mock")})
        qwen = rows[("mtplx", "qwen")]
        self.assertEqual((qwen["inference_events"], qwen["prompt_known_calls"], qwen["prompt_unknown_calls"], qwen["known_prompt_tokens"]), (3, 2, 1, 10))
        self.assertEqual((qwen["completion_known_calls"], qwen["completion_unknown_calls"], qwen["known_completion_tokens"]), (2, 1, 3))
        self.assertEqual(qwen["cost_unknown_calls"], 3)
        self.assertIsNone(qwen["known_cost_estimate"])
        mock = rows[("shirokuma-mock", "mock")]
        self.assertEqual(mock["prompt_unknown_calls"], 1)
        self.assertIsNone(mock["known_prompt_tokens"])
        zero = self.query("model_usage", task="task-b")[0]
        self.assertEqual(zero["prompt_known_calls"], 1)
        self.assertEqual(zero["prompt_unknown_calls"], 0)
        self.assertEqual(zero["known_prompt_tokens"], 0)
        # ClickHouse sum(Nullable) is zero for all-NULL input; unlike SQLite,
        # the production engine needs this explicit coverage guard.
        for field in ("prompt_tokens", "completion_tokens", "inference_cost_estimate"):
            self.assertIn(f"if(countIf(isNotNull({field})) = 0, NULL, sum({field}))", DASHBOARD.QUERIES["model_usage"])

    def test_events_count_event_outcomes_without_claiming_workflow_completion(self):
        attention = self.query("attention")
        self.assertEqual({row["status"] for row in attention}, {"failed", "blocked"})
        trend = self.query("events_over_time")
        self.assertEqual([(row["success"], row["failed"], row["blocked"]) for row in trend], [(5, 1, 1)])
        self.assertEqual(len(self.query("timeline", task="task-a")), 4)
        text = json.dumps(DASHBOARD.dashboard(), ensure_ascii=False)
        self.assertIn("Task全体の完了判定やPR成功率ではありません", text)
        self.assertIn("未観測", text)

    def test_phase_latency_is_grouped_and_never_summed_across_parent_and_child(self):
        sql = DASHBOARD.QUERIES["phase_latency"]
        self.assertIn("GROUP BY action_type, model_provider, model_name", sql)
        self.assertIn("quantileExact(0.50)(duration_ms)", sql)
        self.assertIn("quantileExact(0.95)(duration_ms)", sql)
        self.assertNotRegex(sql, r"(?i)sum\s*\(\s*duration_ms")
        self.assertIn("親処理と子処理は重なる", json.dumps(DASHBOARD.dashboard(), ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
