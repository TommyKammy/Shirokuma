#!/usr/bin/env python3
"""Operate the dedicated, loopback-only ClickHouse Pawprint experiment."""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import http.client
import math
import os
from pathlib import Path
import secrets
import stat
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
EVENT_FIELDS = frozenset({
    "event_id", "timestamp", "agent_id", "task_id", "repo", "issue", "pr",
    "action_type", "target_system", "risk_level", "approval_tier", "model_provider",
    "model_name", "prompt_tokens", "completion_tokens", "inference_cost_estimate",
    "cpu_seconds", "memory_mb_seconds", "status", "blocked_by_policy",
    "human_override", "trace_id", "duration_ms",
})


class AgentHouseError(RuntimeError):
    pass


def docker(*args: str, required: bool = True) -> str:
    result = subprocess.run(["docker", "--context", CONTEXT, *args], capture_output=True,
                            text=True, timeout=180)
    if required and result.returncode:
        raise AgentHouseError(f"Docker {args[0]} failed (exit {result.returncode}); inspect scoped runtime locally")
    return result.stdout if result.returncode == 0 else ""


def check_context() -> None:
    data = json.loads(docker("context", "inspect", CONTEXT))[0]
    endpoint = data["Endpoints"]["docker"]["Host"]
    if not endpoint.startswith("unix://") or not endpoint.endswith("/.colima/mac-studio-solo/docker.sock"):
        raise AgentHouseError("requires the dedicated Colima Docker socket")
    if docker("info", "--format", "{{.Architecture}}").strip() not in {"aarch64", "arm64"}:
        raise AgentHouseError("requires native ARM64 Docker runtime")


def inspect_owned(kind: str, name: str) -> dict | None:
    raw = docker(kind, "inspect", name, required=False)
    if not raw:
        return None
    data = json.loads(raw)[0]
    labels = data.get("Config", {}).get("Labels", {}) if kind == "container" else data.get("Labels", {})
    if labels.get(LABEL) != "agenthouse-local":
        raise AgentHouseError(f"refusing an unowned {kind} named {name}")
    return data


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AgentHouseError("local AgentHouse must not redirect requests")


@contextmanager
def private_state_fd():
    """Bind a local read/write to the private state directory, not a child pathname."""
    try:
        fd = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        identity = os.fstat(fd)
        if identity.st_uid != os.getuid() or stat.S_IMODE(identity.st_mode) != 0o700:
            raise AgentHouseError('local AgentHouse state directory is not private')
    except OSError:
        raise AgentHouseError('local AgentHouse state directory cannot be opened safely') from None
    except BaseException:
        os.close(fd)
        raise
    try:
        yield fd
    finally:
        os.close(fd)


class Client:
    def __init__(self, *, credential_bytes: bytes | None = None) -> None:
        if credential_bytes is None:
            try:
                with private_state_fd() as state:
                    fd = os.open('credential.json', os.O_RDONLY | os.O_NOFOLLOW,
                                 dir_fd=state)
                    with os.fdopen(fd, 'rb') as file:
                        identity = os.fstat(file.fileno())
                        if (not stat.S_ISREG(identity.st_mode) or identity.st_uid != os.getuid() or
                                stat.S_IMODE(identity.st_mode) != 0o600 or identity.st_nlink != 1 or
                                identity.st_size > 16384):
                            raise AgentHouseError('local credential is not a private regular file')
                        credential_bytes = file.read(16385)
                        if len(credential_bytes) > 16384:
                            raise AgentHouseError('local credential exceeds read limit')
            except OSError:
                raise AgentHouseError('local credential cannot be opened safely') from None
        try:
            credential = json.loads(credential_bytes)
            password = credential['password']
            if type(password) is not str or not password:
                raise ValueError('invalid credential')
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise AgentHouseError('local credential has invalid format') from None
        self.auth = base64.b64encode((USER + ":" + password).encode()).decode()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def query(self, sql: str) -> str:
        request = urllib.request.Request(URL + "/?wait_end_of_query=1", data=sql.encode(),
                                         headers={"Authorization": "Basic " + self.auth}, method="POST")
        try:
            with self.opener.open(request, timeout=30) as response:
                body = response.read(8 * 1024 * 1024 + 1)
                if len(body) > 8 * 1024 * 1024:
                    raise AgentHouseError("AgentHouse response exceeds the local export limit")
                try:
                    return body.decode('utf-8')
                except UnicodeError:
                    raise AgentHouseError('local AgentHouse response is not UTF-8') from None
        except (urllib.error.URLError, TimeoutError, http.client.HTTPException) as error:
            raise AgentHouseError("local AgentHouse request failed; verify its scoped runtime") from error


def ensure_runtime() -> Client:
    check_context()
    existing = inspect_owned("container", NAME)
    inspect_owned("volume", VOLUME)
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    credential_path = STATE / "credential.json"
    if not credential_path.exists():
        if existing:
            raise AgentHouseError("existing AgentHouse requires its original local credential; refusing rotation")
        credential_path.write_text(json.dumps({"password": secrets.token_urlsafe(36)}) + "\n")
        credential_path.chmod(0o600)
    password = json.loads(credential_path.read_text())["password"]
    environment = STATE / "container.env"
    environment.write_text(f"CLICKHOUSE_DB=agenthouse\nCLICKHOUSE_USER={USER}\nCLICKHOUSE_PASSWORD={password}\n")
    environment.chmod(0o600)
    if not existing:
        docker("image", "inspect", IMAGE)
        docker("volume", "create", "--label", LABEL + "=agenthouse-local", VOLUME)
        docker("create", "--name", NAME, "--label", LABEL + "=agenthouse-local",
               "--platform", "linux/arm64", "--cpus", "2", "--memory", "4g",
               "--pids-limit", "512", "--ulimit", "nofile=262144:262144",
               "--security-opt", "no-new-privileges:true",
               "--publish", "127.0.0.1:18123:8123", "--env-file", str(environment),
               "--mount", f"type=volume,src={VOLUME},dst=/var/lib/clickhouse", IMAGE)
    else:
        if existing["Config"]["Image"] != IMAGE:
            raise AgentHouseError("existing AgentHouse image differs; preserve it for explicit migration")
        bindings = existing["HostConfig"].get("PortBindings", {})
        if bindings != {"8123/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18123"}]}:
            raise AgentHouseError("existing AgentHouse port bindings differ from loopback scope")
        mounts = existing.get("Mounts", [])
        expected_mount = {"Type": "volume", "Name": VOLUME,
                          "Destination": "/var/lib/clickhouse", "RW": True}
        if (len(mounts) != 1 or
                {key: mounts[0].get(key) for key in expected_mount} != expected_mount):
            raise AgentHouseError("existing AgentHouse must mount only its owned data volume read-write")
        host_config = existing["HostConfig"]
        if host_config.get("NanoCpus") != 2_000_000_000 or host_config.get("Memory") != 4 * 1024**3:
            raise AgentHouseError("existing AgentHouse resource limits must remain 2 CPU and 4 GiB")
    docker("start", NAME)
    client = Client()
    deadline = time.monotonic() + 90
    while True:
        try:
            if client.query("SELECT 1").strip() == "1":
                break
            raise AgentHouseError("AgentHouse readiness response differs")
        except AgentHouseError:
            if time.monotonic() >= deadline:
                raise AgentHouseError("AgentHouse did not become ready within 90 seconds")
            time.sleep(1)
    client.query("CREATE DATABASE IF NOT EXISTS agenthouse")
    client.query((HERE / "schema.sql").read_text())
    return client


def event(action: str, target: str, status: str = "success", **fields) -> dict:
    value = {
        "event_id": str(uuid.uuid4()), "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        "agent_id": "local-experiment", "task_id": "local-development", "repo": "Shirokuma",
        "issue": None, "pr": None, "action_type": action, "target_system": target,
        "risk_level": "low", "approval_tier": "local-standing", "model_provider": "none",
        "model_name": "", "prompt_tokens": None, "completion_tokens": None,
        "inference_cost_estimate": None, "cpu_seconds": None, "memory_mb_seconds": None,
        "status": status, "blocked_by_policy": False, "human_override": False,
        "trace_id": "", "duration_ms": 0,
    }
    if set(fields) - set(value):
        raise AgentHouseError("unknown Pawprint field; raw prompts, tool inputs and credentials are not accepted")
    value.update(fields)
    uuid.UUID(value["event_id"])
    for key in ("issue", "pr", "prompt_tokens", "completion_tokens", "duration_ms"):
        number = value[key]
        if key == "duration_ms" and number is None:
            raise AgentHouseError("duration_ms must be a nonnegative integer, not null")
        if number is not None and (type(number) is not int or not 0 <= number <= 2**64 - 1):
            raise AgentHouseError(f"invalid nonnegative integer: {key}")
    for key in ("inference_cost_estimate", "cpu_seconds", "memory_mb_seconds"):
        number = value[key]
        if number is not None and (type(number) not in {int, float} or not math.isfinite(number) or number < 0):
            raise AgentHouseError(f"invalid measured value: {key}")
    for key, item in value.items():
        if isinstance(item, str) and (len(item) > 256 or "\n" in item or "\r" in item):
            raise AgentHouseError(f"unbounded or multiline field: {key}")
    if value["status"] not in {"success", "failed", "blocked"}:
        raise AgentHouseError("invalid Pawprint status")
    if value["risk_level"] not in {"low", "normal", "high"}:
        raise AgentHouseError("invalid Pawprint risk level")
    for key in ("blocked_by_policy", "human_override"):
        if type(value[key]) is not bool:
            raise AgentHouseError(f"invalid boolean: {key}")
    for key in ("event_id", "timestamp", "agent_id", "task_id", "repo", "action_type", "target_system",
                "risk_level", "approval_tier", "model_provider", "model_name", "status", "trace_id"):
        if not isinstance(value[key], str):
            raise AgentHouseError(f"invalid string: {key}")
    datetime.strptime(value["timestamp"], "%Y-%m-%d %H:%M:%S.%f")
    return value


def insert(client: Client, value: dict) -> None:
    # Revalidate the complete input, then serialize as data, never SQL literals.
    # Defaults belong to event(); inventing an ID while inserting would turn a
    # retry of an incomplete payload into a second logical audit event.
    if not isinstance(value, dict) or set(value) != EVENT_FIELDS:
        raise AgentHouseError("insert requires the complete Pawprint field set; construct new events with event()")
    validated = event(value["action_type"], value["target_system"], **{
        key: item for key, item in value.items() if key not in {"action_type", "target_system"}})
    client.query("INSERT INTO agenthouse.pawprints FORMAT JSONEachRow\n" + json.dumps(validated))


def export(client: Client) -> Path:
    rows = client.query("SELECT * FROM agenthouse.pawprints FINAL ORDER BY timestamp, event_id FORMAT JSONEachRow")
    name = "pawprints-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".jsonl"
    path = STATE / name
    try:
        with private_state_fd() as state:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=state)
            with os.fdopen(fd, 'w', encoding='utf-8') as file:
                os.fchmod(file.fileno(), 0o600)
                file.write(rows)
    except OSError:
        raise AgentHouseError('local AgentHouse export cannot be created safely') from None
    return path


def smoke(client: Client) -> dict:
    value = event("runtime.smoke", "clickhouse")
    insert(client, value)
    insert(client, value)  # Retry must produce one logical event through FINAL.
    rows = json.loads(client.query("SELECT count() AS n, any(status) AS status FROM agenthouse.pawprints FINAL "
                                  f"WHERE event_id = '{value['event_id']}' FORMAT JSON"))["data"]
    if rows != [{"n": "1", "status": "success"}] and rows != [{"n": 1, "status": "success"}]:
        raise AgentHouseError("Pawprint insertion or retry readback differs")
    path = export(client)
    exported = [json.loads(line) for line in path.read_text().splitlines()]
    if not any(row["event_id"] == value["event_id"] for row in exported):
        raise AgentHouseError("export omitted the inserted event")
    return {"result": "passed", "version": client.query("SELECT version()").strip(),
            "event_id": value["event_id"], "logical_rows": 1, "export": str(path), "endpoint": URL}


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
            result = {"export": str(export(Client()))}
        else:
            result = {"result": "ready", "version": Client().query("SELECT version()").strip(), "endpoint": URL}
        print(json.dumps(result))
        return 0
    except (AgentHouseError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"result": "failed", "error": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
