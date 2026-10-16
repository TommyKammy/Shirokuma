"""Build, test, and inspect persistent Iceberg analytics models."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

SPEC = importlib.util.spec_from_file_location(
    "warehouse_resident", Path(__file__).resolve().with_name("warehouse_resident.py"))
warehouse_resident = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(warehouse_resident)
DATAOPS = Path(__file__).resolve().parent / "dataops"
PROJECT = DATAOPS / "dbt_persistent"
DBT = DATAOPS / ".venv/bin/dbt"
MARTS = ("fct_orders", "dim_customers")


def run_dbt(action, url):
    endpoint = urlsplit(url)
    with tempfile.TemporaryDirectory(prefix="shirokuma-lake-") as directory:
        output = Path(directory)
        profile = {
            "config": {"send_anonymous_usage_stats": False},
            "shirokuma_lake_marts": {
                "target": "local", "outputs": {"local": {
                    "type": "trino", "method": "none", "host": endpoint.hostname,
                    "port": endpoint.port or 80, "http_scheme": "http",
                    "user": "shirokuma-lake", "database": "iceberg",
                    "schema": "marts", "threads": 1, "retries": 1,
                }},
            },
        }
        (output / "profiles.yml").write_text(json.dumps(profile))
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith("DBT_") and not key.upper().endswith("_PROXY")}
        completed = subprocess.run(
            [str(DBT), action, "--project-dir", str(PROJECT),
             "--profiles-dir", directory, "--target-path", str(output / "target"),
             "--log-path", str(output / "logs")],
            env=env, capture_output=True, text=True, timeout=900)
        artifact = output / "target/run_results.json"
        results = json.loads(artifact.read_text())["results"] if artifact.exists() else []
        models = [{"name": item["unique_id"], "status": item["status"]} for item in results]
        success = completed.returncode == 0 and bool(results) and all(
            item["status"] in ("success", "pass", "warn") for item in results)
        return models, success


def run(action):
    started = time.monotonic()
    report = dict(action=action, result="failed", models=[], telemetry="skipped")
    try:
        if warehouse_resident.status("LOCAL")["replicas"] < 1:
            warehouse_resident.kubectl("scale", "deployment/trino-local", "--replicas=1")
            warehouse_resident.kubectl("rollout", "status", "deployment/trino-local",
                                       f"--timeout={warehouse_resident.TIMEOUT}s")
        with warehouse_resident.trino() as url:
            headers = {"X-Trino-User": "shirokuma-lake"}
            if action == "build":
                for schema in ("staging", "marts"):
                    warehouse_resident.execute(
                        url, f"CREATE SCHEMA IF NOT EXISTS iceberg.{schema}", headers)
            if action == "status":
                for model in MARTS:
                    count = warehouse_resident.execute(
                        url, f"SELECT count(*) FROM iceberg.marts.{model}", headers)["rows"][0][0]
                    report["models"].append(dict(name=model, status="success" if count > 0
                                                 else "failed", row_count=count))
                success = all(model["status"] == "success" for model in report["models"])
            else:
                report["models"], success = run_dbt(action, url)
            report["result"] = "success" if success else "failed"
    except Exception as exc:
        report["error"] = str(exc)
    report["duration_ms"] = int((time.monotonic() - started) * 1000)
    if action in ("build", "test"):
        report["telemetry"] = warehouse_resident.telemetry(
            action, report["duration_ms"], report["result"], prefix="lake.pipeline.")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "test", "status"))
    report = run(parser.parse_args().action)
    print(json.dumps(report))
    return 0 if report["result"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
