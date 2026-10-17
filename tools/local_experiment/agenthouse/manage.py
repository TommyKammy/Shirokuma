#!/usr/bin/env python3
"""Operate the dedicated, loopback-only ClickHouse Pawprint experiment."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import http.client
import json
from pathlib import Path
import secrets
import subprocess
import time
import urllib.error
import urllib.request
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
STATE = ROOT / "tmp/local-experiment/agenthouse"
CONTEXT = "colima-mac-studio-solo"
NAME = "shirokuma-local-agenthouse"
VOLUME = NAME + "-data"
IMAGE = "clickhouse/clickhouse-server@sha256:79f936494ccd00791676e0352d5988f30a215286592c6da83966c4c49fabd2c1"
URL = "http://127.0.0.1:18123"
USER = "shirokuma_agenthouse"
LABEL = "shirokuma.dev.component"


class AgentHouseError(RuntimeError):
    pass


def docker(*args: str, required: bool = True) -> str:
    try:
        result = subprocess.run(["docker", "--context", CONTEXT, *args],
                                capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as error:
        raise AgentHouseError("Docker command could not complete") from error
    if result.returncode:
        missing = (not required and len(args) == 3 and args[1] == "inspect" and
                   result.stderr.strip() in {f"Error: No such {args[0]}: {args[2]}",
                                             f"Error: No such object: {args[2]}"})
        if missing:
            return ""
        raise AgentHouseError(f"Docker {args[0]} failed (exit {result.returncode})")
    return result.stdout


def check_context() -> None:
    context = json.loads(docker("context", "inspect", CONTEXT))[0]
    expected = "unix://" + str(Path.home() / ".colima/mac-studio-solo/docker.sock")
    if context["Name"] != CONTEXT or context["Endpoints"]["docker"]["Host"] != expected:
        raise AgentHouseError("requires the dedicated Colima Docker socket")
    if docker("info", "--format", "{{.Architecture}}").strip() not in {"aarch64", "arm64"}:
        raise AgentHouseError("requires native ARM64 Docker runtime")


def inspect_owned(kind: str, name: str) -> dict | None:
    raw = docker(kind, "inspect", name, required=False)
    if not raw:
        return None
    data = json.loads(raw)[0]
    labels = data["Config"].get("Labels", {}) if kind == "container" else data.get("Labels", {})
    if (labels or {}).get(LABEL) != "agenthouse-local":
        raise AgentHouseError(f"refusing an unowned {kind} named {name}")
    return data


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AgentHouseError("local AgentHouse must not redirect requests")


def credential_path() -> Path:
    # Keep the original credential authoritative when both files exist.
    legacy = STATE / "credential.json"
    return legacy if legacy.exists() else STATE / "state.json"


class Client:
    def __init__(self, *, credential_bytes: bytes | None = None) -> None:
        try:
            credential = json.loads(credential_bytes if credential_bytes is not None
                                    else credential_path().read_bytes())
            password = credential["password"]
            if not isinstance(password, str) or not password:
                raise ValueError("missing password")
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise AgentHouseError("local credential is missing or invalid") from error
        self.auth = base64.b64encode(f"{USER}:{password}".encode()).decode("ascii")
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    @classmethod
    def from_state(cls, path: Path | str | None = None) -> Client:
        if path is None:
            return cls()
        path = Path(path)
        if path.is_dir():
            path = path / ("credential.json" if (path / "credential.json").exists() else "state.json")
        return cls(credential_bytes=path.read_bytes())

    def query(self, sql: str) -> str:
        request = urllib.request.Request(URL + "/?wait_end_of_query=1", data=sql.encode("utf-8"),
                                         headers={"Authorization": "Basic " + self.auth}, method="POST")
        try:
            with self.opener.open(request, timeout=30) as response:
                if response.status != 200 or response.headers.get("X-ClickHouse-Exception-Code"):
                    raise AgentHouseError("local AgentHouse query failed")
                # urllib handles Content-Length and chunked transfer framing.
                text = response.read().decode("utf-8")
                if any(line.startswith("Code:") and "DB::Exception:" in line for line in text.splitlines()):
                    raise AgentHouseError("local AgentHouse query returned an exception")
                return text
        except (urllib.error.URLError, OSError, http.client.HTTPException, UnicodeError) as error:
            raise AgentHouseError("local AgentHouse request failed; verify its runtime") from error

    def rows(self, sql: str) -> list[dict]:
        """Read a JSONEachRow query, or an explicitly requested JSON response."""
        if "FORMAT" not in sql.upper().split():
            sql = sql.rstrip().rstrip(";") + " FORMAT JSONEachRow"
        text = self.query(sql)
        if not text.strip():
            return []
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            return [json.loads(line) for line in text.splitlines() if line.strip()]
        return result["data"] if isinstance(result, dict) and "data" in result else [result]


def write_private(path: Path, text: str) -> None:
    with path.open("x", encoding="utf-8") as file:
        path.chmod(0o600)
        file.write(text)


def ensure_runtime() -> Client:
    check_context()
    existing = inspect_owned("container", NAME)
    volume = inspect_owned("volume", VOLUME)
    if bool(existing) != bool(volume):
        raise AgentHouseError("container and retained data volume must exist together; inspect local state")
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    if existing:
        client = Client.from_state()  # Read the saved secret before starting; never rotate it.
        if existing["Config"]["Image"] != IMAGE:
            raise AgentHouseError("existing AgentHouse image differs; requires explicit migration")
        if existing["HostConfig"].get("PortBindings") != {
                "8123/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18123"}]}:
            raise AgentHouseError("existing AgentHouse port bindings differ from loopback scope")
        mounts = existing.get("Mounts", [])
        if len(mounts) != 1 or any(mounts[0].get(key) != value for key, value in {
                "Type": "volume", "Name": VOLUME, "Destination": "/var/lib/clickhouse", "RW": True}.items()):
            raise AgentHouseError("existing AgentHouse requires its original data volume")
    else:
        if credential_path().exists() or (STATE / "container.env").exists():
            raise AgentHouseError("saved AgentHouse state exists; inspect it before creating a runtime")
        docker("image", "inspect", IMAGE)
        password = secrets.token_urlsafe(36)
        write_private(STATE / "credential.json", json.dumps({"password": password}) + "\n")
        environment = STATE / "container.env"
        write_private(environment, f"CLICKHOUSE_DB=agenthouse\nCLICKHOUSE_USER={USER}\nCLICKHOUSE_PASSWORD={password}\n")
        docker("volume", "create", "--label", LABEL + "=agenthouse-local", VOLUME)
        docker("create", "--name", NAME, "--label", LABEL + "=agenthouse-local",
               "--platform", "linux/arm64", "--cpus", "2", "--memory", "4g",
               "--pids-limit", "512", "--ulimit", "nofile=262144:262144",
               "--security-opt", "no-new-privileges:true",
               "--publish", "127.0.0.1:18123:8123", "--env-file", str(environment),
               "--mount", f"type=volume,src={VOLUME},dst=/var/lib/clickhouse", IMAGE)
        client = Client.from_state()
    if not existing or not existing["State"].get("Running"):
        docker("start", NAME)
    deadline = time.monotonic() + 90
    while True:
        try:
            if client.query("SELECT 1").strip() != "1":
                raise AgentHouseError("unexpected readiness response")
            break
        except AgentHouseError:
            if time.monotonic() >= deadline:
                raise AgentHouseError("AgentHouse did not become ready within 90 seconds")
            time.sleep(1)
    client.query("CREATE DATABASE IF NOT EXISTS agenthouse")
    client.query((HERE / "schema.sql").read_text())
    return client


def event(action: str, target: str = "local-experiment", status: str = "success",
          duration_ms: int = 0, session_id: str | None = None, **fields) -> dict:
    """Build a Pawprint; also accept event(action, status, duration_ms)."""
    if isinstance(status, (int, float)):
        target, status, duration_ms = "local-experiment", target, status
    value = {
        "event_id": str(uuid.uuid4()), "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        "agent_id": "local-experiment", "task_id": "local-development", "repo": "Shirokuma",
        "issue": None, "pr": None, "action_type": action, "target_system": target,
        "risk_level": "low", "approval_tier": "local-standing", "model_provider": "none",
        "model_name": "", "prompt_tokens": None, "completion_tokens": None,
        "inference_cost_estimate": None, "cpu_seconds": None, "memory_mb_seconds": None,
        "status": status, "blocked_by_policy": False, "human_override": False,
        "trace_id": session_id or "", "duration_ms": duration_ms,
    }
    if fields.keys() - value.keys():
        raise AgentHouseError("unknown Pawprint field")
    value.update(fields)
    if value["status"] not in {"success", "failed", "blocked"}:
        raise AgentHouseError("invalid Pawprint status")
    return value


EVENT_FIELDS = frozenset(event("", ""))


def insert(client: Client, record: dict) -> None:
    if record.keys() != EVENT_FIELDS:
        raise AgentHouseError("insert requires a complete Pawprint; construct it with event()")
    client.query("INSERT INTO agenthouse.pawprints FORMAT JSONEachRow\n" + json.dumps(record, allow_nan=False))


def export(client: Client) -> Path:
    rows = client.query("SELECT * FROM agenthouse.pawprints FINAL ORDER BY timestamp, event_id FORMAT JSONEachRow")
    for line in rows.splitlines():
        json.loads(line)
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = STATE / ("pawprints-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".jsonl")
    write_private(path, rows)
    return path


def checked_version(client: Client) -> str:
    return client.query("SELECT version()").strip()


def smoke(client: Client) -> dict:
    value = event("runtime.smoke", "clickhouse")
    insert(client, value)
    insert(client, value)
    rows = client.rows("SELECT count() AS n, any(status) AS status FROM agenthouse.pawprints FINAL "
                       f"WHERE event_id = '{value['event_id']}' FORMAT JSON")
    if len(rows) != 1 or int(rows[0]["n"]) != 1 or rows[0]["status"] != "success":
        raise AgentHouseError("Pawprint insertion or retry readback differs")
    path = export(client)
    exported = [json.loads(line) for line in path.read_text().splitlines()]
    matching = [row for row in exported if row.get("event_id") == value["event_id"]]
    if len(matching) != 1 or matching[0]["status"] != "success":
        raise AgentHouseError("export does not contain the successful smoke event")
    return {"result": "passed", "version": checked_version(client), "event_id": value["event_id"],
            "logical_rows": 1, "export": str(path), "endpoint": URL}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["up", "status", "smoke", "export", "stop"])
    args = parser.parse_args()
    try:
        if args.command in {"up", "smoke"}:
            client = ensure_runtime()
            result = smoke(client) if args.command == "smoke" else {"result": "ready", "endpoint": URL}
        elif args.command == "stop":
            check_context()
            if inspect_owned("container", NAME):
                docker("stop", NAME)
            result = {"result": "stopped", "data_volume_retained": True}
        elif args.command == "export":
            result = {"export": str(export(Client.from_state()))}
        else:
            result = {"result": "ready", "version": checked_version(Client.from_state()), "endpoint": URL}
        print(json.dumps(result))
        return 0
    except (AgentHouseError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(json.dumps({"result": "failed", "error": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
