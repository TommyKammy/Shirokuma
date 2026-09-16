#!/usr/bin/env python3
"""Scoped local Loki lifecycle and bounded, normalized Pawprint logs."""
from __future__ import annotations

import argparse
import fcntl
import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
from local_experiment import check_context as check_kubernetes_context

IMAGE = "grafana/loki@sha256:a7280d594e8d49e9d123a06a4baeea95f3a7d1d4e8a7ff134b4f3d2c8d9a8e7d"
NAME = "shirokuma-local-loki"
VOLUME = NAME + "-data"
LABEL = "shirokuma.dev.component"
URL = "http://127.0.0.1:13100"
FIELDS = {"task_id", "trace_id", "span_id", "action", "status"}


class LokiError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise LokiError("Loki redirect refused")


def docker(*args: str) -> str:
    result = subprocess.run(["rtk", "proxy", "docker", "--context", "colima-mac-studio-solo", *args],
                            cwd=ROOT, text=True, capture_output=True, timeout=180)
    if result.returncode:
        raise LokiError(f"scoped Docker operation failed: {args[0]}")
    return result.stdout


def check_context() -> None:
    check_kubernetes_context()
    context = json.loads(docker("context", "inspect", "colima-mac-studio-solo"))[0]
    endpoint = context["Endpoints"]["docker"]["Host"]
    if not endpoint.startswith("unix://") or not endpoint.endswith("/.colima/mac-studio-solo/docker.sock"):
        raise LokiError("requires the dedicated Colima Docker socket")
    if docker("info", "--format", "{{.Architecture}}").strip() not in {"arm64", "aarch64"}:
        raise LokiError("requires native ARM64 Docker runtime")


def request(path: str, payload=None, *, timeout=10):
    opener = urllib.request.build_opener(NoRedirect, urllib.request.ProxyHandler({}))
    req = urllib.request.Request(URL + path, data=None if payload is None else json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with opener.open(req, timeout=timeout) as response:
        raw = response.read(2 * 1024**2 + 1)
    if len(raw) > 2 * 1024**2:
        raise LokiError("Loki response exceeded budget")
    return json.loads(raw) if raw and raw[:1] in (b"{", b"[") else raw.decode()


def wait_ready(timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LokiError("Loki readiness timed out")
        try:
            if request("/ready", timeout=min(10, remaining)).strip() == "ready":
                return
        except (OSError, ValueError):
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LokiError("Loki readiness timed out")
        time.sleep(min(1, remaining))


def validate_record(record: dict) -> dict:
    if set(record) != FIELDS:
        raise LokiError("log requires the exact normalized field set")
    for name in ("task_id", "action"):
        if not isinstance(record[name], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", record[name]):
            raise LokiError("invalid bounded log identity")
    for name, width in (("trace_id", 32), ("span_id", 16)):
        if not isinstance(record[name], str) or not re.fullmatch(r"[0-9a-f]{%d}" % width, record[name]):
            raise LokiError("invalid trace identity")
    if record["status"] not in {"success", "failed", "blocked"}:
        raise LokiError("invalid log status")
    return record


def push_record(record: dict) -> None:
    record = validate_record(record)
    request("/loki/api/v1/push", {"streams": [{"stream": {"service_name": "shirokuma-local", "component": "pawprint"},
        "values": [[str(time.time_ns()), json.dumps(record, separators=(",", ":"))]]}]})


def query_task(task_id: str) -> list:
    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", task_id):
        raise LokiError("invalid bounded task identity")
    query = '{service_name="shirokuma-local",component="pawprint"} | json | task_id="' + task_id + '"'
    params = urllib.parse.urlencode({"query": query, "start": time.time_ns() - 3600 * 10**9,
                                    "end": time.time_ns(), "limit": 1000, "direction": "forward"})
    result = request("/loki/api/v1/query_range?" + params)
    if result.get("status") != "success":
        raise LokiError("Loki query failed")
    return [validate_record(json.loads(value[1])) for stream in result["data"]["result"] for value in stream["values"]]


def container():
    ids = docker("ps", "-aq", "--filter", "name=^/" + NAME + "$").split()
    if not ids:
        return None
    if len(ids) != 1:
        raise LokiError("ambiguous local Loki container")
    value = json.loads(docker("inspect", ids[0]))[0]
    config, host = value["Config"], value["HostConfig"]
    mounts = {(m["Type"], m.get("Name", m.get("Source")), m["Destination"], m["RW"]) for m in value["Mounts"]}
    expected = {("volume", VOLUME, "/loki", True), ("bind", str(HERE / "config.yaml"), "/etc/loki/local-config.yaml", False)}
    if (config["Image"] != IMAGE or config.get("Labels", {}).get(LABEL) != "loki-local"
            or host.get("PortBindings") != {"3100/tcp": [{"HostIp": "127.0.0.1", "HostPort": "13100"}]}
            or mounts != expected or host.get("NanoCpus") != 10**9 or host.get("Memory") != 1024**3):
        raise LokiError("existing local Loki scope differs; refusing mutation")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["up", "status", "stop"])
    args = parser.parse_args()
    try:
        check_context()
        state = ROOT / "tmp/local-experiment/loki"
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (state / "runtime.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            current = container()
            if args.command == "up":
                if current is None:
                    docker("pull", "--platform", "linux/arm64", IMAGE)
                    identity = json.loads(docker("image", "inspect", IMAGE))[0]
                    if identity["Architecture"] != "arm64" or identity["Os"] != "linux":
                        raise LokiError("Loki requires native ARM64")
                    existing = docker("volume", "ls", "--format", "{{.Name}}", "--filter", "name=^" + VOLUME + "$").split()
                    if existing:
                        volume = json.loads(docker("volume", "inspect", VOLUME))[0]
                        if volume.get("Labels", {}).get(LABEL) != "loki-local":
                            raise LokiError("unowned Loki volume")
                    else:
                        docker("volume", "create", "--label", LABEL + "=loki-local", VOLUME)
                    docker("run", "-d", "--name", NAME, "--label", LABEL + "=loki-local", "--cpus", "1",
                           "--memory", "1g", "--pids-limit", "256", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                           "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "-p", "127.0.0.1:13100:3100",
                           "--mount", "type=volume,source=" + VOLUME + ",target=/loki",
                           "--mount", "type=bind,source=" + str(HERE / "config.yaml") + ",target=/etc/loki/local-config.yaml,readonly",
                           IMAGE, "-config.file=/etc/loki/local-config.yaml")
                elif not current["State"]["Running"]:
                    docker("start", NAME)
                wait_ready()
            elif args.command == "stop" and current is not None:
                docker("stop", "--time", "30", NAME)
            current = container()
            running = bool(current and current["State"]["Running"])
            ready = running and request("/ready").strip() == "ready"
            result = {"running": running, "ready": ready, "url": URL, "image": IMAGE, "volume_retained": True}
            (state / "runtime.json").write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result))
            return 0 if ready or args.command == "stop" else 1
    except Exception as error:
        print(json.dumps({"result": "failed", "error": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
