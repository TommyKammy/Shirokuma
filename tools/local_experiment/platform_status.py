#!/usr/bin/env python3
"""Read readiness across the dedicated local Kubernetes and Docker experiments."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
SUPERSET_SPEC = importlib.util.spec_from_file_location(
    "platform_superset", ROOT / "tools/local_experiment/superset/local_superset.py")
SUPERSET = importlib.util.module_from_spec(SUPERSET_SPEC)
SUPERSET_SPEC.loader.exec_module(SUPERSET)
COMMANDS = {
    "lakehouse": ["go", "run", "./cmd/shirokuma", "doctor", "--profile", "local-experiment", "--output", "json"],
    "superset": [PYTHON, "tools/local_experiment/superset/local_superset.py", "status"],
    "metadata": [PYTHON, "tools/local_experiment/metadata/runtime.py", "status"],
    "agenthouse": [PYTHON, "tools/local_experiment/agenthouse/manage.py", "status"],
    "loki": [PYTHON, "tools/local_experiment/loki/manage.py", "status"],
}


def observation(status, reason=None):
    result = {"ready": status == "ready", "status": status}
    if reason:
        result["reason"] = reason
    return result


def superset_health():
    """Probe the existing fixed loopback health URL without auth or redirects."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), SUPERSET.NoRedirect())
    try:
        with opener.open(SUPERSET.URL + "/health", timeout=3) as response:
            if response.status != 200:
                return observation("not-ready", "health endpoint returned an unexpected status")
            body = response.read(17)
            if len(body) > 16 or body.strip() != b"OK":
                return observation("not-ready", "health endpoint returned an unexpected body")
            return observation("ready")
    except urllib.error.HTTPError:
        return observation("not-ready", "health endpoint returned an HTTP error")
    except SUPERSET.LocalError:
        return observation("not-ready", "health endpoint redirect rejected")
    except (OSError, urllib.error.URLError):
        return observation("unknown", "health endpoint unavailable")


def check(item):
    name, command = item
    try:
        result = subprocess.run(["rtk", "proxy", *command], cwd=ROOT, capture_output=True,
                                text=True, timeout=60)
        if result.returncode not in (0, 1) or len(result.stdout) > 1024**2:
            return name, observation("unknown", "component status failed")
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise ValueError("invalid component status")
        # Each component's own status validates its fixed scope and live state.
        # Do not propagate raw Docker labels, credentials or backend stderr.
        if name == "lakehouse":
            if not isinstance(value.get("status"), str) or value["status"] not in {"healthy", "degraded"}:
                raise ValueError("invalid lakehouse status")
            ready = value.get("status") == "healthy"
        elif name == "agenthouse":
            if value.get("result") != "ready":
                return name, observation("unknown", "authenticated query status unavailable")
            ready = value.get("result") == "ready"
        elif name == "superset":
            if type(value.get("running")) is not bool:
                raise ValueError("invalid Superset running status")
            ready = value.get("running") is True
        else:
            if type(value.get("ready")) is not bool:
                raise ValueError("invalid component readiness")
            ready = value.get("ready") is True
        if ready and result.returncode:
            return name, observation("unknown", "component status exit code disagrees")
        if name == "superset" and ready:
            return name, superset_health()
        return name, observation("ready" if ready else "not-ready")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return name, observation("unknown", "status unavailable or invalid")


def main():
    with ThreadPoolExecutor(max_workers=len(COMMANDS)) as pool:
        components = dict(pool.map(check, COMMANDS.items()))
    ready = all(value["ready"] for value in components.values())
    print(json.dumps({"observed_at": datetime.now(timezone.utc).isoformat(),
        "profile": "local-experiment", "ready": ready, "components": components,
        "scope": "runtime readiness; SQL, saved dashboards, lineage and agent behavior require their smoke checks"}, indent=2))
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
