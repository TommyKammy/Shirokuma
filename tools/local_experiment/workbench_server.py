#!/usr/bin/env python3
"""Serve the local Shirokuma Workbench with standard-library HTTP tooling."""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import bears_resident
import warehouse_resident
from agenthouse import manage

INDEX = Path(__file__).parent / "workbench" / "index.html"


def components():
    """One inventory request, without log collection or waking the warehouse."""
    items = json.loads(warehouse_resident.kubectl("get", "deploy,sts", "-o", "json"))["items"]
    return {item["metadata"]["name"]: {
        "replicas": item.get("spec", {}).get("replicas", 1),
        "ready_replicas": item.get("status", {}).get("readyReplicas", 0),
    } for item in items}


class WorkbenchHandler(BaseHTTPRequestHandler):
    def end_headers(self):
        # Only the Workbench's own origin may control this local lab.
        origin = self.headers.get("Origin")
        if origin == "http://" + self.headers.get("Host", ""):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def respond(self, value, status=200):
        data = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        try:
            url = urlsplit(self.path)
            if url.path in ("/", "/index.html"):
                data = INDEX.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if url.path == "/api/status":
                report = {"status": "healthy", "warehouse": None, "components": {}}
                try:
                    report["warehouse"] = warehouse_resident.status("LOCAL")
                except Exception as exc:
                    report.update(status="degraded", warehouse={"error": str(exc)})
                try:
                    report["components"] = components()
                    if any(v["ready_replicas"] < v["replicas"] for v in report["components"].values()):
                        report["status"] = "degraded"
                except Exception as exc:
                    report.update(status="degraded", components={"error": str(exc)})
                self.respond(report)
            elif url.path in ("/api/catalog", "/api/lineage"):
                table = parse_qs(url.query).get("table", [None])[0]
                action = "lineage" if url.path.endswith("lineage") else "describe" if table else "search"
                self.respond(bears_resident.catalog(SimpleNamespace(bear="catalog", action=action, table=table)))
            elif url.path == "/api/pawprints":
                result = manage.Client().query(
                    "SELECT * FROM agenthouse.pawprints ORDER BY timestamp DESC LIMIT 100 FORMAT JSON")
                self.respond(json.loads(result)["data"])
            else:
                self.respond({"error": "route not found"}, 404)
        except Exception as exc:
            self.respond({"error": str(exc)}, 502)

    def body(self):
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Content-Type must be application/json")
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 1024 * 1024:
            raise ValueError("JSON body must be between 1 byte and 1 MiB")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def do_POST(self):
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + self.headers.get("Host", ""):
            self.respond({"error": "cross-origin control requests are not allowed"}, 403)
            return
        path = urlsplit(self.path).path
        if path not in ("/api/warehouse/chill", "/api/warehouse/wake", "/api/warehouse/query", "/api/bears/run"):
            self.respond({"error": "route not found"}, 404)
            return
        try:
            if path.endswith(("/chill", "/wake")):
                result = warehouse_resident.transition(path.rsplit("/", 1)[1], "LOCAL")
            else:
                body = self.body()
                if path == "/api/warehouse/query":
                    statement, size, limit = body.get("sql"), body.get("warehouse", "LOCAL"), body.get("max_rows", 1000)
                    if not isinstance(statement, str) or not statement.strip():
                        raise ValueError("sql must be a non-empty string")
                    if not isinstance(size, str) or type(limit) is not int or limit < 1:
                        raise ValueError("warehouse must be a string and max_rows a positive integer")
                    size = warehouse_resident.warehouse(size)
                    result = warehouse_resident.query(size, statement, catalog="iceberg", schema="marts", max_rows=limit)
                else:
                    bear = body.get("bear")
                    actions = {"sre": ("inspect",), "query": ("explain",), "data": ("generate",),
                               "catalog": ("search", "describe", "lineage"), "policy": ("baseline", "check")}
                    if not isinstance(bear, str) or bear not in actions:
                        raise ValueError("unknown bear")
                    action = body.get("action", actions[bear][0])
                    if not isinstance(action, str) or action not in actions[bear]:
                        raise ValueError("invalid bear action")
                    fields = {key: body.get(key) for key in ("sql", "request", "manifest", "target", "table")}
                    if any(value is not None and not isinstance(value, str) for value in fields.values()):
                        raise ValueError("bear inputs must be strings")
                    if bear == "catalog":
                        fields["table"] = fields["table"] or fields["target"]
                    fields["target"] = fields["target"] or "latest"
                    if bear == "data" and not (fields["request"] or "").strip():
                        raise ValueError("data bear requires a request")
                    result = bears_resident.run(SimpleNamespace(bear=bear, action=action, **fields))
            self.respond(result)
        except (ValueError, UnicodeError) as exc:
            self.respond({"error": str(exc)}, 400)
        except Exception as exc:
            self.respond({"error": str(exc)}, 502)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8888)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)
    with HTTPServer((args.host, args.port), WorkbenchHandler) as server:
        print(f"Shirokuma Workbench: http://{args.host}:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
