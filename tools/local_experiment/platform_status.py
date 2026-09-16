#!/usr/bin/env python3
"""Read readiness across the dedicated local Kubernetes and Docker experiments."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
COMMANDS = {
    "lakehouse": ["go", "run", "./cmd/shirokuma", "doctor", "--profile", "local-experiment", "--output", "json"],
    "superset": [PYTHON, "tools/local_experiment/superset/local_superset.py", "status"],
    "metadata": [PYTHON, "tools/local_experiment/metadata/runtime.py", "status"],
    "agenthouse": [PYTHON, "tools/local_experiment/agenthouse/manage.py", "status"],
    "loki": [PYTHON, "tools/local_experiment/loki/manage.py", "status"],
}


def check(item):
    name, command = item
    try:
        result = subprocess.run(["rtk", "proxy", *command], cwd=ROOT, capture_output=True,
                                text=True, timeout=60)
        if result.returncode or len(result.stdout) > 1024**2:
            return name, {"ready": False, "reason": "component status failed"}
        value = json.loads(result.stdout)
        # Each component's own status validates its fixed scope and live state.
        # Do not propagate raw Docker labels, credentials or backend stderr.
        if name == "lakehouse":
            ready = value.get("status") == "healthy"
        elif name == "agenthouse":
            ready = value.get("result") == "ready"
        elif name == "superset":
            ready = value.get("running") is True
        else:
            ready = value.get("ready") is True
        return name, {"ready": ready}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return name, {"ready": False, "reason": "status unavailable or invalid"}


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
