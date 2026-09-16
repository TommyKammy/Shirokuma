#!/usr/bin/env python3
"""Prove a bounded LangGraph -> mock/local provider -> MCP -> Pawprint workflow."""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.parse
import uuid
from typing import Any, TypedDict

from providers import LocalProvider, ProviderError, configuration_presence, load_config
from mock_provider import serve_mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
LOCAL = HERE.parent


class WorkflowError(RuntimeError):
    pass


class State(TypedDict, total=False):
    task_id: str
    trace_id: str
    plan: dict
    tool_result: dict
    summary: dict
    model_calls: list[dict]
    event_ids: list[str]


def disable_external_tracing() -> None:
    # Local runtime overrides only; no account/global configuration is changed.
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    os.environ["LANGCHAIN_TRACING"] = "false"


def validate_plan(value: dict) -> dict:
    if (not isinstance(value, dict) or set(value) != {"tool", "arguments"}
            or value["tool"] != "dbt_list_models" or value["arguments"] != {}):
        raise WorkflowError("model plan exceeded the fixed read-only workflow allowlist")
    return value


def validate_summary(value: dict) -> dict:
    if (not isinstance(value, dict) or set(value) != {"result", "summary"}
            or value["result"] != "complete" or not isinstance(value["summary"], str)
            or not 1 <= len(value["summary"]) <= 256 or "\n" in value["summary"] or "\r" in value["summary"]):
        raise WorkflowError("model summary is not a bounded completed result")
    return value


def build_graph(provider: Any, gateway_call: Any, audit: Any, tracer: Any):
    disable_external_tracing()
    from langgraph.graph import StateGraph, START, END
    from langgraph.checkpoint.memory import InMemorySaver

    def model_event(state, phase, response=None, status="success"):
        response = response or {}
        return audit({"action_type": "llm." + phase, "target_system": "llm-" + provider.profile["kind"],
            "task_id": state["task_id"], "trace_id": state["trace_id"], "status": status,
            "blocked_by_policy": status == "blocked", "model_provider": provider.profile["provider"],
            "model_name": provider.profile["model"], "prompt_tokens": response.get("prompt_tokens"),
            "completion_tokens": response.get("completion_tokens"), "inference_cost_estimate": None,
            "duration_ms": response.get("duration_ms", 0)})

    async def plan(state):
        with tracer.start_as_current_span("llm.plan"):
            started = time.monotonic()
            try:
                response = await asyncio.to_thread(provider.complete, "plan")
            except Exception:
                model_event(state, "plan", {"duration_ms": max(0, int((time.monotonic() - started) * 1000))}, status="failed")
                raise
            try:
                proposed = validate_plan(response["content"])
            except WorkflowError:
                model_event(state, "plan", response, status="blocked")
                raise
            event_id = model_event(state, "plan", response)
            return {"plan": proposed, "model_calls": [{k: v for k, v in response.items() if k != "content"}],
                    "event_ids": [event_id]}

    async def call_gateway(state):
        with tracer.start_as_current_span("gateway.read"):
            payload = await gateway_call(state["plan"]["tool"], state["plan"]["arguments"], state["task_id"])
            if (payload.get("status") != "success" or payload.get("audit") != "delivered"
                    or payload.get("task_id") != state["task_id"] or payload.get("trace_id") != state["trace_id"]):
                raise WorkflowError("Gateway did not return successful, delivered, correlated tool evidence")
            event_id = str(uuid.UUID(payload["event_id"]))
            return {"tool_result": payload, "event_ids": state["event_ids"] + [event_id]}

    async def summarize(state):
        with tracer.start_as_current_span("llm.summarize"):
            started = time.monotonic()
            try:
                response = await asyncio.to_thread(provider.complete, "summarize", state["tool_result"]["status"])
                summary = validate_summary(response["content"])
            except Exception:
                model_event(state, "summarize", {"duration_ms": max(0, int((time.monotonic() - started) * 1000))}, status="failed")
                raise
            event_id = model_event(state, "summarize", response)
            return {"summary": summary,
                    "model_calls": state["model_calls"] + [{k: v for k, v in response.items() if k != "content"}],
                    "event_ids": state["event_ids"] + [event_id]}

    graph = StateGraph(State)
    graph.add_node("plan", plan)
    graph.add_node("gateway", call_gateway)
    graph.add_node("summarize", summarize)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "gateway")
    graph.add_edge("gateway", "summarize")
    graph.add_edge("summarize", END)
    return graph.compile(checkpointer=InMemorySaver())


def child_environment(task_id: str, traceparent: str) -> dict[str, str]:
    if not re.fullmatch(r"workflow-[0-9a-f]{32}", task_id) or not re.fullmatch(r"00-[0-9a-f]{32}-[0-9a-f]{16}-01", traceparent):
        raise WorkflowError("invalid local workflow correlation identity")
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "USER", "TMPDIR", "LANG", "LC_ALL"}}
    env.update({"SHIROKUMA_GATEWAY_TASK_ID": task_id,
                "SHIROKUMA_GATEWAY_PARENT_TRACEPARENT": traceparent,
                "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"})
    return env


async def gateway_call(tool: str, arguments: dict, task_id: str, output: Path) -> dict:
    from mcp import Client, StdioServerParameters, stdio_client
    from opentelemetry import trace
    validate_plan({"tool": tool, "arguments": arguments})
    span = trace.get_current_span().get_span_context()
    traceparent = f"00-{span.trace_id:032x}-{span.span_id:016x}-01"
    python = LOCAL / "gateway/.venv/bin/python"
    if not python.exists():
        raise WorkflowError("initialize the pinned local Gateway environment before the LLM workflow")
    params = StdioServerParameters(command="rtk", args=["proxy", str(python), str(LOCAL / "gateway/server.py")],
        env=child_environment(task_id, traceparent), cwd=ROOT)
    with (output / "gateway-stderr.log").open("a") as log:
        async with Client(stdio_client(params, errlog=log)) as client:
            tools = await client.list_tools()
            if tool not in {item.name for item in tools.tools}:
                raise WorkflowError("Gateway does not expose the required read-only tool")
            result = await client.call_tool(tool, arguments=arguments, read_timeout_seconds=30)
            payload = result.structured_content
            if payload is None:
                payload = json.loads(result.content[0].text)
            return payload


def probe_vm_to_host(endpoint: str) -> dict:
    port = urllib.parse.urlsplit(endpoint).port
    if port is None:
        raise WorkflowError("mock endpoint must bind an explicit ephemeral port")
    payload = {"model": "shirokuma-deterministic-v1", "stream": False,
               "messages": [{"role": "user", "content": '{"phase":"plan"}'}]}
    url = f"http://host.lima.internal:{port}/v1/chat/completions"
    process = subprocess.run(["rtk", "proxy", "colima", "ssh", "--profile", "mac-studio-solo", "--",
        "curl", "--noproxy", "host.lima.internal", "--silent", "--show-error", "--fail", "--max-time", "10",
        "--request", "POST", "--header", "Content-Type:application/json", "--data-binary", "@-", url],
        input=json.dumps(payload), text=True, capture_output=True, timeout=20)
    if process.returncode:
        raise WorkflowError("Colima-to-host loopback mock probe failed")
    result = json.loads(process.stdout)
    if result.get("model") != "shirokuma-deterministic-v1":
        raise WorkflowError("Colima mock probe returned the wrong model identity")
    validate_plan(json.loads(result["choices"][0]["message"]["content"]))
    return {"result": "passed", "source": "mac-studio-solo", "destination": "host.lima.internal",
            "host_listener": "127.0.0.1", "model": result["model"], "public_listener": False}


def agenthouse_module():
    spec = importlib.util.spec_from_file_location("llm_agenthouse", LOCAL / "agenthouse/manage.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tracing(output: Path):
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

    class LocalExporter(SpanExporter):
        def export(self, spans):
            with (output / "spans.jsonl").open("a") as file:
                for span in spans:
                    context = span.get_span_context()
                    file.write(json.dumps({"name": span.name, "trace_id": f"{context.trace_id:032x}",
                        "span_id": f"{context.span_id:016x}",
                        "parent_span_id": f"{span.parent.span_id:016x}" if span.parent else None,
                        "start_time_unix_nano": span.start_time, "end_time_unix_nano": span.end_time,
                        "status": span.status.status_code.name}) + "\n")
            return SpanExportResult.SUCCESS

    provider = TracerProvider(resource=Resource.create({"service.name": "shirokuma-local-llm"}))
    provider.add_span_processor(SimpleSpanProcessor(LocalExporter()))
    return provider


async def run_workflow(config: dict, endpoint: str, output: Path) -> dict:
    profile = config["providers"][config["active_provider"]]
    model = LocalProvider(profile, endpoint)
    agenthouse = agenthouse_module()
    client = agenthouse.Client()
    trace_provider = tracing(output)
    tracer = trace_provider.get_tracer("shirokuma.local.llm", "0.1.0")
    records = []

    def audit(fields):
        value = agenthouse.event(fields["action_type"], fields["target_system"],
            **{key: item for key, item in fields.items() if key not in {"action_type", "target_system"}},
            agent_id="local-langgraph")
        agenthouse.insert(client, value)
        records.append({key: value[key] for key in ("event_id", "task_id", "trace_id", "action_type", "status",
            "model_provider", "model_name", "prompt_tokens", "completion_tokens", "inference_cost_estimate", "duration_ms")})
        return value["event_id"]

    runs = []
    try:
        for _ in range(2):
            task_id = "workflow-" + uuid.uuid4().hex
            with tracer.start_as_current_span("langgraph.workflow") as span:
                trace_id = f"{span.get_span_context().trace_id:032x}"
                graph = build_graph(model, lambda tool, args, task: gateway_call(tool, args, task, output), audit, tracer)
                graph_config = {"configurable": {"thread_id": task_id}, "recursion_limit": 8, "callbacks": []}
                started = time.monotonic()
                try:
                    state = await asyncio.wait_for(graph.ainvoke({"task_id": task_id, "trace_id": trace_id}, graph_config), 90)
                except Exception:
                    audit({"action_type": "workflow.complete", "target_system": "langgraph", "task_id": task_id,
                        "trace_id": trace_id, "status": "failed", "model_provider": profile["provider"],
                        "model_name": profile["model"], "duration_ms": max(0, int((time.monotonic() - started) * 1000))})
                    raise
                checkpoint = graph.get_state(graph_config)
                if checkpoint.next or checkpoint.values.get("summary") != state["summary"]:
                    raise WorkflowError("LangGraph completed checkpoint did not retain the result")
                completed = audit({"action_type": "workflow.complete", "target_system": "langgraph", "task_id": task_id,
                    "trace_id": trace_id, "status": "success", "model_provider": profile["provider"],
                    "model_name": profile["model"], "duration_ms": max(0, int((time.monotonic() - started) * 1000))})
                runs.append({"task_id": task_id, "trace_id": trace_id, "plan": state["plan"], "summary": state["summary"],
                    "model_calls": state["model_calls"], "gateway": state["tool_result"],
                    "checkpoint": "completed-in-memory", "event_ids": state["event_ids"] + [completed]})
        ids = [str(uuid.UUID(value)) for run in runs for value in run["event_ids"]]
        quoted = ",".join("'" + value + "'" for value in ids)
        rows = json.loads(client.query("SELECT event_id, task_id, trace_id, action_type, model_provider, model_name, "
            "prompt_tokens, completion_tokens, inference_cost_estimate, duration_ms, status "
            f"FROM agenthouse.pawprints FINAL WHERE event_id IN ({quoted}) FORMAT JSON"))["data"]
        indexed = {row["event_id"]: row for row in rows}
        if len(indexed) != len(ids):
            raise WorkflowError("AgentHouse is missing workflow, inference, or Gateway events")
        for run in runs:
            for event_id in run["event_ids"]:
                row = indexed[event_id]
                if row["task_id"] != run["task_id"] or row["trace_id"] != run["trace_id"] or row["status"] != "success":
                    raise WorkflowError("Pawprint trace/task correlation differs")
                if profile["kind"] == "mock" and any(row[key] is not None for key in ("prompt_tokens", "completion_tokens", "inference_cost_estimate")):
                    raise WorkflowError("deterministic mock must not invent measured inference usage")
        return {"result": "passed", "scope": "local-experiment-only", "provider_kind": profile["kind"],
            "provider": profile["provider"], "model": profile["model"], "runs": runs, "audited_events": rows,
            "deterministic_mock": profile["kind"] == "mock" and runs[0]["summary"] == runs[1]["summary"],
            "cloud_acceptance": "pending provider/model/spending authorization; no cloud call attempted",
            "model_quality_evaluated": False}
    finally:
        trace_provider.shutdown()
        (output / "model-events.json").write_text(json.dumps(records, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "providers.example.json")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    output = ROOT / "tmp/local-experiment/llm" / uuid.uuid4().hex
    try:
        config = load_config(args.config)
        presence = configuration_presence(config)
        if args.validate_only:
            print(json.dumps({"result": "valid", "active_provider": config["active_provider"],
                              "environment_names_present": presence, "cloud_execution": "disabled"}))
            return 0
        disable_external_tracing()
        output.mkdir(parents=True, mode=0o700)
        if config["active_provider"] == "mock":
            with serve_mock() as endpoint:
                vm_probe = probe_vm_to_host(endpoint)
                report = asyncio.run(run_workflow(config, endpoint, output))
                report["vm_to_host_mock"] = vm_probe
        else:
            report = asyncio.run(run_workflow(config, config["providers"]["local"]["endpoint"], output))
        report["environment_names_present"] = presence
    except Exception as error:
        report = {"result": "failed", "scope": "local-experiment-only", "error": type(error).__name__ + ": " + str(error)}
    if output.exists():
        (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({**report, "evidence": str(output) if output.exists() else None}))
    return 0 if report["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
