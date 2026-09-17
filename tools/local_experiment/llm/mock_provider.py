"""Ephemeral loopback-only compatible endpoint; no model weights or inference."""
from __future__ import annotations

import contextlib
import http.server
import json
import threading

from providers import validate_evidence

MODEL = "shirokuma-deterministic-v1"


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Neither prompts nor requests enter application logs.

    def do_POST(self):
        self.connection.settimeout(5)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if self.path != "/v1/chat/completions" or not 0 < length <= 32 * 1024:
                raise ValueError("invalid request")
            data = json.loads(self.rfile.read(length))
            if data.get("model") != MODEL or data.get("stream") is not False:
                raise ValueError("unsupported mock request")
            phase = json.loads(data["messages"][-1]["content"])
            if phase["phase"] == "plan":
                content = {"tool": "dbt_list_models", "arguments": {}}
            elif phase["phase"] == "summarize":
                evidence = validate_evidence(phase.get("tool_evidence"))
                content = {"result": "complete", "model_count": len(evidence["models"]),
                           "models": evidence["models"],
                           "verified_run_available": evidence["verified_run_available"]}
            else:
                raise ValueError("unknown phase")
            result = {"id": "shirokuma-deterministic-response", "object": "chat.completion", "model": MODEL,
                      "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(content)},
                                   "finish_reason": "stop"}], "usage": None}
            raw = json.dumps(result).encode()
            self.send_response(200)
        except Exception:
            raw = b'{"error":"invalid bounded mock request"}'
            self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass


@contextlib.contextmanager
def serve_mock():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
