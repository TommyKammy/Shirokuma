"""Resident Trino warehouse control for the dedicated local lab."""
import argparse
from contextlib import contextmanager
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

KUBECTL = ["kubectl", "--context", "colima-mac-studio-solo", "-n", "shirokuma-dev"]
TIMEOUT = 180


def warehouse(value):
    size = value.upper()
    if size not in {"XS", "S", "LOCAL"}:
        raise ValueError("warehouse must be XS, S, or LOCAL")
    return size


def kubectl(*args):
    return subprocess.run(KUBECTL + list(args), check=True, capture_output=True,
                          text=True, timeout=TIMEOUT).stdout


def deployment():
    return json.loads(kubectl("get", "deployment/trino-local", "-o", "json"))


def status(size):
    size = warehouse(size)
    obj = deployment()
    replicas = obj.get("spec", {}).get("replicas", 1)
    ready = obj.get("status", {}).get("readyReplicas", 0)
    state = "chilled" if replicas == 0 else "active" if ready >= 1 else "starting"
    return dict(warehouse=warehouse(size), deployment="trino-local", namespace="shirokuma-dev",
                state=state, replicas=replicas, ready_replicas=ready)


def wait_for(check):
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.2)
    raise TimeoutError("resident warehouse readiness timed out")


def http_json(url, data=None, headers=None):
    request = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


@contextmanager
def trino():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(KUBECTL + ["port-forward", "--address", "127.0.0.1",
                                        "svc/trino-local", f"{port}:8080"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    try:
        def ready():
            if process.poll() is not None:
                raise RuntimeError("Trino port-forward exited")
            try:
                return http_json(url + "/v1/info").get("starting") is False
            except (OSError, urllib.error.URLError):
                return False
        wait_for(ready)
        yield url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def telemetry(action, duration):
    try:
        from agenthouse.manage import Client, event, insert
        insert(Client(), event(action="warehouse." + action, target="trino-warehouse",
                               status="success", agent_id="shirokuma-warehouse",
                               task_id=str(uuid.uuid4()), trace_id=uuid.uuid4().hex,
                               duration_ms=duration))
        return "recorded"
    except Exception:
        return "skipped"


def finish(report, started):
    report["duration_ms"] = int((time.monotonic() - started) * 1000)
    report["telemetry"] = telemetry(report["action"], report["duration_ms"])
    return report


def transition(action, size):
    started = time.monotonic()
    before = status(size)
    chilled = action == "chill"
    noop = before["replicas"] == 0 if chilled else before["state"] == "active"
    if not noop:
        kubectl("scale", "deployment/trino-local", f"--replicas={0 if chilled else 1}")
        if chilled:
            def stopped():
                obj = deployment()
                observed = obj.get("status", {})
                return (obj["spec"]["replicas"] == 0 and
                        observed.get("replicas", 0) == 0 and
                        observed.get("readyReplicas", 0) == 0)
            wait_for(stopped)
        else:
            kubectl("rollout", "status", "deployment/trino-local", f"--timeout={TIMEOUT}s")
            with trino():
                pass
    return finish(dict(warehouse=warehouse(size), action=action,
                       result="noop" if noop else "verified", before_replicas=before["replicas"],
                       after_replicas=0 if chilled else 1,
                       state="chilled" if chilled else "active"), started)


def query(size, sql, catalog=None, schema=None, no_auto_wake=False):
    size = warehouse(size)
    if not sql or not sql.strip():
        raise ValueError("query requires non-empty SQL")
    started = time.monotonic()
    auto_woken = status(size)["replicas"] == 0
    if auto_woken:
        if no_auto_wake:
            raise ValueError("warehouse is chilled; auto-wake is disabled")
        transition("wake", size)
    headers = {"X-Trino-User": "shirokuma-warehouse", "X-Trino-Source": "shirokuma-warehouse-" + size}
    if catalog:
        headers["X-Trino-Catalog"] = catalog
    if schema:
        headers["X-Trino-Schema"] = schema
    rows, columns, query_id = [], [], None
    with trino() as url:
        page = http_json(url + "/v1/statement", sql.encode("utf-8"), headers)
        while True:
            if page.get("error"):
                raise RuntimeError("Trino query failed: " + page["error"].get("message", "unknown error"))
            query_id = page.get("id", query_id)
            columns = page.get("columns", columns)
            rows.extend(page.get("data", []))
            if not page.get("nextUri"):
                break
            page = http_json(page["nextUri"], headers=headers)
    return finish(dict(warehouse=size, action="query", result="verified", auto_woken=auto_woken,
                       query_id=query_id, columns=columns, rows=rows, row_count=len(rows)), started)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("status", "chill", "wake", "query"):
        command = sub.add_parser(action)
        command.add_argument("--warehouse", default="XS", type=warehouse)
        if action == "query":
            command.add_argument("--sql", required=True)
            command.add_argument("--catalog")
            command.add_argument("--schema")
            command.add_argument("--no-auto-wake", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "status":
            result = status(args.warehouse)
        elif args.action == "query":
            result = query(args.warehouse, args.sql, args.catalog, args.schema, args.no_auto_wake)
        else:
            result = transition(args.action, args.warehouse)
        print(json.dumps(result))
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
