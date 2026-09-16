from __future__ import annotations

import asyncio
import contextlib
import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, Mock, patch

HERE = Path(__file__).resolve().parents[1] / "tools/local_experiment/llm"
sys.path.insert(0, str(HERE))
import providers as P
import mock_provider as M
import workflow as W


def config():
    return json.loads((HERE / "providers.example.json").read_text())


class NoopTracer:
    def start_as_current_span(self, name):
        return contextlib.nullcontext()


class LocalLLMTests(unittest.TestCase):
    def test_default_mock_and_pending_cloud_are_valid(self):
        value = P.validate_config(config())
        self.assertEqual(value["active_provider"], "mock")
        self.assertFalse(value["providers"]["cloud"]["enabled"])
        self.assertIsNone(value["providers"]["cloud"]["provider"])
        self.assertIsNone(value["providers"]["cloud"]["budget_usd"])

    @unittest.skipUnless(importlib.util.find_spec("jsonschema"), "run with the pinned local LLM uv environment")
    def test_machine_readable_schema_matches_local_boundary(self):
        import jsonschema
        schema = json.loads((HERE / "config.schema.json").read_text())
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(config(), schema)
        for mutate in (lambda c: c["providers"]["cloud"].update(enabled=True),
                       lambda c: c["providers"]["mock"].update(api_key="secret"),
                       lambda c: c["providers"]["local"].update(endpoint="http://external.example/v1")):
            value = config()
            mutate(value)
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(value, schema)

    def test_cloud_activation_and_inline_secrets_are_rejected(self):
        for mutate in (
            lambda c: c.update(active_provider="cloud"),
            lambda c: c["providers"]["cloud"].update(enabled=True),
            lambda c: c["providers"]["cloud"].update(api_key="not-retained"),
            lambda c: c["providers"]["cloud"].update(api_key_env="secret-value"),
            lambda c: c["providers"]["local"].update(api_key_env="OPENAI_API_KEY"),
        ):
            value = config()
            mutate(value)
            with self.assertRaises(P.ProviderError):
                P.validate_config(value)
        with patch.object(P.urllib.request, "build_opener") as network:
            with self.assertRaises(P.ProviderError):
                P.LocalProvider({**config()["providers"]["cloud"], "enabled": True})
            network.assert_not_called()

    def test_local_profile_needs_explicit_model_and_safe_endpoint(self):
        value = config()
        value["active_provider"] = "local"
        value["providers"]["local"]["enabled"] = True
        with self.assertRaises(P.ProviderError):
            P.validate_config(value)
        value["providers"]["local"]["model"] = "owner-selected-local-model"
        self.assertEqual(P.validate_config(value)["active_provider"], "local")
        for endpoint in ("http://example.com/v1", "https://127.0.0.1/v1", "http://127.0.0.1/v1?key=secret",
                         "http://user:password@127.0.0.1/v1", "http://127.0.0.1.evil/v1", "http://127.0.0.1:99999/v1"):
            with self.subTest(endpoint=endpoint), self.assertRaises(P.ProviderError):
                P.validate_endpoint(endpoint)
        for endpoint in ("http://127.0.0.1:8080/v1", "http://[::1]:8080/v1", "http://host.lima.internal:8080/v1"):
            self.assertEqual(P.validate_endpoint(endpoint), endpoint)

    def test_config_request_limits_reject_invalid_values(self):
        for field, value in (("timeout_seconds", 0), ("timeout_seconds", 121), ("timeout_seconds", True),
                             ("max_output_tokens", 4097), ("budget_usd", float("nan"))):
            body = config()
            body["providers"]["cloud"][field] = value
            with self.assertRaises(P.ProviderError):
                P.validate_config(body)

    def test_environment_presence_reports_names_only(self):
        with patch.dict(P.os.environ, {"OPENAI_API_KEY": "must-not-appear"}, clear=True):
            presence = P.configuration_presence(config())
        self.assertTrue(presence["OPENAI_API_KEY"])
        self.assertNotIn("must-not-appear", json.dumps(presence))

    def test_gateway_environment_does_not_inherit_credentials_or_tracing(self):
        with patch.dict(W.os.environ, {"OPENAI_API_KEY": "not-forwarded", "LANGSMITH_API_KEY": "not-forwarded",
                                       "HTTPS_PROXY": "http://elsewhere", "LANGSMITH_TRACING": "true"}):
            env = W.child_environment("workflow-" + "a" * 32, "00-" + "b" * 32 + "-" + "c" * 16 + "-01")
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("LANGSMITH_API_KEY", env)
        self.assertNotIn("HTTPS_PROXY", env)
        self.assertEqual(env["LANGSMITH_TRACING"], "false")

    def test_real_mock_http_plan_and_summary_are_deterministic_without_fake_usage(self):
        with M.serve_mock() as endpoint:
            client = P.LocalProvider(config()["providers"]["mock"], endpoint)
            first = client.complete("plan")
            second = client.complete("plan")
            summary = client.complete("summarize", "success")
        self.assertEqual(first["content"], second["content"])
        self.assertEqual(first["content"], {"tool": "dbt_list_models", "arguments": {}})
        self.assertEqual(summary["content"]["result"], "complete")
        self.assertIsNone(first["prompt_tokens"])
        self.assertIsNone(first["completion_tokens"])
        self.assertIsNone(first["inference_cost_estimate"])
        self.assertGreaterEqual(first["duration_ms"], 0)

    def test_model_cannot_expand_gateway_allowlist(self):
        for plan in ({"tool": "github_issue_write", "arguments": {}},
                     {"tool": "dbt_list_models", "arguments": {"command": "rm"}},
                     {"tool": "dbt_list_models", "arguments": {}, "extra": "ignored?"}):
            with self.assertRaises(W.WorkflowError):
                W.validate_plan(plan)
        with self.assertRaises(W.WorkflowError):
            W.validate_summary({"result": "complete", "summary": "private\nmultiline"})

    def test_redirects_are_rejected(self):
        with self.assertRaises(P.ProviderError):
            P.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com")

    @unittest.skipUnless(importlib.util.find_spec("langgraph"), "run with the pinned local LLM uv environment")
    def test_actual_langgraph_checkpoint_and_gateway_correlation(self):
        task_id, trace_id = "workflow-" + "a" * 32, "b" * 32
        gateway = AsyncMock(return_value={"status": "success", "audit": "delivered", "task_id": task_id,
            "trace_id": trace_id, "event_id": "01234567-89ab-cdef-0123-456789abcdef", "data": {"models": []}})
        audit = Mock(side_effect=["plan-event", "summary-event"])
        with M.serve_mock() as endpoint:
            provider = P.LocalProvider(config()["providers"]["mock"], endpoint)
            graph = W.build_graph(provider, gateway, audit, NoopTracer())
            options = {"configurable": {"thread_id": task_id}, "recursion_limit": 8}
            state = asyncio.run(graph.ainvoke({"task_id": task_id, "trace_id": trace_id}, options))
            checkpoint = graph.get_state(options)
        self.assertEqual(state["summary"]["result"], "complete")
        self.assertFalse(checkpoint.next)
        self.assertEqual(checkpoint.values["summary"], state["summary"])
        gateway.assert_awaited_once_with("dbt_list_models", {}, task_id)
        self.assertEqual(audit.call_count, 2)

    @unittest.skipUnless(importlib.util.find_spec("langgraph"), "run with the pinned local LLM uv environment")
    def test_graph_stops_before_tool_on_invalid_model_plan(self):
        provider = Mock(profile=config()["providers"]["mock"])
        provider.complete.return_value = {"content": {"tool": "delete_data", "arguments": {}}, "duration_ms": 1}
        gateway, audit = AsyncMock(), Mock(return_value="blocked-event")
        graph = W.build_graph(provider, gateway, audit, NoopTracer())
        with self.assertRaises(W.WorkflowError):
            asyncio.run(graph.ainvoke({"task_id": "workflow-" + "a" * 32, "trace_id": "b" * 32},
                        {"configurable": {"thread_id": "blocked"}}))
        gateway.assert_not_awaited()
        self.assertEqual(audit.call_args.args[0]["status"], "blocked")

    @unittest.skipUnless(importlib.util.find_spec("langgraph"), "run with the pinned local LLM uv environment")
    def test_graph_does_not_summarize_failed_or_uncorrelated_gateway_result(self):
        for payload in ({"status": "failed", "audit": "delivered"},
                        {"status": "success", "audit": "failed"},
                        {"status": "success", "audit": "delivered", "task_id": "wrong", "trace_id": "wrong"}):
            with self.subTest(payload=payload):
                provider = Mock(profile=config()["providers"]["mock"])
                provider.complete.return_value = {"content": {"tool": "dbt_list_models", "arguments": {}}, "duration_ms": 1}
                graph = W.build_graph(provider, AsyncMock(return_value=payload), Mock(return_value="event"), NoopTracer())
                with self.assertRaises(W.WorkflowError):
                    asyncio.run(graph.ainvoke({"task_id": "workflow-" + "a" * 32, "trace_id": "b" * 32},
                                            {"configurable": {"thread_id": "failure"}}))
                provider.complete.assert_called_once_with("plan")


if __name__ == "__main__":
    unittest.main()
