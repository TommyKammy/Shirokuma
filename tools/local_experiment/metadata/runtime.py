#!/usr/bin/env python3
"""Manage only the isolated Shirokuma local OpenMetadata Compose project."""
from __future__ import annotations
import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[3]
STATE = ROOT / "tmp/local-experiment/metadata"
COMPOSE = ROOT / "deploy/local-experiment/metadata/compose.json"
CONTEXT = "colima-mac-studio-solo"
PROJECT = "shirokuma-local-metadata"


def invoke(args, *, timeout=600, output=None):
    result = subprocess.run(args, stdout=output or subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"metadata command failed (exit {result.returncode}); inspect scoped Docker logs locally")
    return result.stdout


def docker(*args, **kwargs):
    return invoke(["docker", "--context", CONTEXT, *args], **kwargs)


def check_runtime():
    context = json.loads(docker("context", "inspect", CONTEXT))[0]
    endpoint = context["Endpoints"]["docker"]["Host"]
    if not endpoint.startswith("unix://") or not endpoint.endswith("/.colima/mac-studio-solo/docker.sock"):
        raise RuntimeError("metadata requires the dedicated local Colima Docker socket")
    info = json.loads(docker("info", "--format", "{{json .}}"))
    if info["Architecture"] not in {"aarch64", "arm64"} or info["OSType"] != "linux":
        raise RuntimeError("metadata requires Linux/arm64 Docker")


def initialize():
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    STATE.chmod(0o700)
    if (STATE / ".env").exists():
        return
    selector = "label=com.docker.compose.project=" + PROJECT
    containers = docker("ps", "--all", "--quiet", "--filter", selector).strip()
    volumes = docker("volume", "ls", "--quiet", "--filter", selector).strip()
    if containers or volumes:
        raise RuntimeError("retained metadata resources require their original credentials; refusing automatic secret rotation")
    if any(STATE.glob("*.der")):
        raise RuntimeError("partial metadata credential setup; inspect state before retry")
    values = {"OM_STATE": str(STATE), "OM_DB_PASSWORD": secrets.token_urlsafe(36),
              "OM_POSTGRES_PASSWORD": secrets.token_urlsafe(36),
              "OM_ADMIN_PASSWORD": "Aa1!" + secrets.token_urlsafe(32),
              "OM_FERNET_KEY": secrets.token_urlsafe(32) + "="}
    # These bind-mounted files are readable by their non-root container users;
    # their enclosing host directory is private and never checked into Git.
    (STATE / "db-init.sql").write_text(
        "CREATE USER openmetadata_user WITH PASSWORD '" + values["OM_DB_PASSWORD"] + "';\n"
        "CREATE DATABASE openmetadata_db OWNER openmetadata_user;\n")
    invoke(["openssl", "genrsa", "-out", str(STATE / "key.pem"), "2048"])
    invoke(["openssl", "pkcs8", "-topk8", "-inform", "PEM", "-outform", "DER", "-nocrypt",
            "-in", str(STATE / "key.pem"), "-out", str(STATE / "private_key.der")])
    invoke(["openssl", "rsa", "-in", str(STATE / "key.pem"), "-pubout", "-outform", "DER",
            "-out", str(STATE / "public_key.der")])
    (STATE / "key.pem").unlink()
    for name in ("db-init.sql", "private_key.der", "public_key.der"):
        (STATE / name).chmod(0o444)
    env = STATE / ".env"
    env.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    env.chmod(0o600)


def compose(*args, **kwargs):
    return docker("compose", "--project-name", PROJECT, "--env-file", str(STATE / ".env"),
                  "--file", str(COMPOSE), *args, **kwargs)


def status():
    """Return only the three persistent services, never raw Compose labels/paths."""
    expected = ("server", "postgresql", "search")
    missing = [{"name": name, "state": "missing", "health": "unknown", "image": None}
               for name in expected]
    if not (STATE / ".env").is_file():
        return {"result": "unavailable", "ready": False, "services": missing}
    try:
        raw = compose("ps", "--all", "--format", "json").decode().strip()
        if not raw:
            entries = []
        else:
            try:
                decoded = json.loads(raw)
                entries = decoded if isinstance(decoded, list) else [decoded]
            except json.JSONDecodeError:
                entries = [json.loads(line) for line in raw.splitlines() if line.strip()]
        images = {name: value["image"] for name, value in json.loads(COMPOSE.read_text())["services"].items()}
        services = []
        ready = True
        for name, absent in zip(expected, missing):
            matches = [row for row in entries if row.get("Service") == name]
            if len(matches) != 1:
                services.append({**absent, "state": "missing" if not matches else "ambiguous"})
                ready = False
                continue
            row = matches[0]
            item = {"name": name, "state": row.get("State", "unknown"),
                    "health": row.get("Health") or "unknown", "image": row.get("Image")}
            services.append(item)
            ready = ready and item["state"] == "running" and item["health"] == "healthy" and item["image"] == images[name]
        return {"result": "ready" if ready else "degraded", "ready": ready, "services": services}
    except (RuntimeError, OSError, ValueError, KeyError, TypeError):
        return {"result": "unavailable", "ready": False, "services": missing}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("up", "status", "stop", "backup"))
    args = parser.parse_args()
    os.umask(0o077)
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    STATE.chmod(0o700)
    with (STATE / "runtime.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        check_runtime()
        if args.command == "up":
            initialize()
        if args.command == "up":
            compose("up", "-d", "--wait", "--wait-timeout", "480", "server", timeout=600)
            from metadata.client import Client
            client = Client()
            client.authenticate(STATE)
            version = client.request("GET", "/system/version")
            print(json.dumps({"result": "ready", "url": "http://127.0.0.1:18585", "scope": PROJECT, "version": version["version"]}))
        elif args.command == "status":
            result = status()
            print(json.dumps(result))
            return 0 if result["ready"] else 1
        elif args.command == "stop":
            compose("stop", timeout=180)
            print("metadata stopped; generated database/search volumes and credentials retained")
        else:
            name = "backup-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".dump"
            target = STATE / name
            with target.open("xb") as output:
                compose("exec", "-T", "postgresql", "pg_dump", "-U", "postgres", "-Fc", "openmetadata_db", output=output)
            print(target.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"metadata: {error}", file=sys.stderr)
        raise SystemExit(1)
