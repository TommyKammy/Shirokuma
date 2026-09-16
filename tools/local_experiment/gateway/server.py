#!/usr/bin/env python3
"""Official MCP stdio adapter for Shirokuma's typed local read allowlist."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from core import Backends, Dispatcher, TOOLS
from telemetry import Telemetry


def trace_configuration(environment):
    task_id = environment.get('SHIROKUMA_GATEWAY_TASK_ID')
    parent = environment.get('SHIROKUMA_GATEWAY_PARENT_TRACEPARENT')
    if task_id is not None and not re.fullmatch(r'(gateway|workflow)-[0-9a-f]{32}', task_id):
        raise ValueError('invalid bounded gateway task identity')
    if parent is not None:
        if not re.fullmatch(r'00-[0-9a-f]{32}-[0-9a-f]{16}-01', parent):
            raise ValueError('invalid W3C gateway parent trace context')
        from opentelemetry.propagate import extract
        from opentelemetry.trace import get_current_span
        context = extract({'traceparent': parent})
        if not get_current_span(context).get_span_context().is_valid:
            raise ValueError('invalid zero-valued gateway trace context')
    else:
        context = None
    return task_id, context


async def serve() -> None:
    telemetry = Telemetry()
    task_id, parent_context = trace_configuration(os.environ)
    dispatcher = Dispatcher(Backends(), telemetry.audit, telemetry.tracer, task_id, parent_context)

    async def list_tools(_context, _params):
        return types.ListToolsResult(tools=[types.Tool(
            name=name, description=description, inputSchema=schema,
            annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                               idempotentHint=True, openWorldHint=False))
            for name, (description, _target, schema) in TOOLS.items()])

    async def call_tool(_context, params):
        result = await anyio.to_thread.run_sync(dispatcher.call, params.name, params.arguments or {})
        return types.CallToolResult(content=[types.TextContent(type='text', text=json.dumps(result))],
                                    structuredContent=result, isError=result['status'] != 'success')

    server = Server('shirokuma-local-gateway', version='0.1.0',
                    instructions='Local read-only allowlist; external GitHub tools are inactive.',
                    on_list_tools=list_tools, on_call_tool=call_tool)
    try:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    finally:
        telemetry.close()


if __name__ == '__main__':
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    asyncio.run(serve())
