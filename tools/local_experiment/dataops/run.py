#!/usr/bin/env python3
"""Materialize and verify disposable dbt tables through a local Dagster asset."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SPEC = importlib.util.spec_from_file_location("local_trino_smoke", HERE.parent / "local_trino_smoke.py")
TRINO = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TRINO)
FIXTURE_SQL = "SELECT id, name FROM iceberg.smoke.fixture_v1 ORDER BY id"
FIXTURE_ROWS = [[1, "shirokuma"], [2, "polaris"]]
MART_ROWS = [[1, "SHIROKUMA"], [2, "POLARIS"]]
NAMESPACE = "iceberg.local_experiment"


class DataOpsError(RuntimeError):
    pass


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def names_for(token: str) -> tuple[str, str]:
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise DataOpsError("run token must be a fresh UUID in hexadecimal form")
    return f"dataops_{token}_stg", f"dataops_{token}_mart"


def bounded_query(client: Any, sql: str, deadline: float | None = None) -> dict[str, Any]:
    if deadline is None:
        return client.query(sql)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DataOpsError("local cleanup SQL time budget exhausted")
    previous_timeout = client.timeout
    try:
        client.timeout = min(previous_timeout, remaining)
        return client.query(sql)
    finally:
        client.timeout = previous_timeout


def existing_tables(client: Any, deadline: float | None = None) -> set[str]:
    return {row[0] for row in bounded_query(client, f"SHOW TABLES FROM {NAMESPACE}", deadline)["rows"]}


def verify_marker(client: Any, name: str, token: str, deadline: float | None = None) -> None:
    # The adapter puts this exact comment in CREATE TABLE, before CTAS commits.
    # A replacement or partially observed unrelated table must never be dropped.
    ddl = bounded_query(client, f"SHOW CREATE TABLE {NAMESPACE}.{name}", deadline)["rows"]
    marker = f"COMMENT 'shirokuma-local-dataops:{token}'"
    if len(ddl) != 1 or marker.lower() not in ddl[0][0].lower():
        raise DataOpsError(f"ownership marker missing; preserved {NAMESPACE}.{name}")


def cleanup_tables(client: Any, names: tuple[str, str], token: str,
                   deadline: float | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"removed": [], "errors": []}
    try:
        present = existing_tables(client, deadline)
    except Exception as error:
        result["errors"].append(f"could not inspect cleanup candidates: {error}")
        return result
    for name in reversed(names):
        if name not in present:
            continue
        try:
            verify_marker(client, name, token, deadline)
            bounded_query(client, f"DROP TABLE {NAMESPACE}.{name}", deadline)
            result["removed"].append(f"{NAMESPACE}.{name}")
        except Exception as error:
            result["errors"].append(str(error))
    try:
        remaining = set(names) & existing_tables(client, deadline)
        result["remaining"] = sorted(remaining)
        if remaining:
            result["errors"].append("experiment relations remain; inspect evidence before cleanup")
    except Exception as error:
        result["errors"].append(f"could not verify cleanup: {error}")
    return result


def run_user(token: str) -> str:
    names_for(token)
    return f"shirokuma-local-dataops-{token}"


def drain_queries(client: Any, token: str, timeout: float = 90) -> dict[str, Any]:
    """Stop only this invocation's queries and confirm all are terminal before DROP."""
    deadline = time.monotonic() + timeout
    user = run_user(token)
    result: dict[str, Any] = {"verified": False, "user": user, "cancelled": [],
                              "cancel_errors": [], "errors": []}
    inspect_sql = (
        'SELECT query_id, state, "user" FROM system.runtime.queries '
        f'WHERE "user" = \'{user}\' AND state NOT IN (\'FINISHED\', \'FAILED\')'
    )
    try:
        while True:
            # Short individual requests keep the overall drain bounded on failures.
            request_deadline = min(deadline, time.monotonic() + 10)
            active = bounded_query(client, inspect_sql, request_deadline)["rows"]
            result["active"] = active
            if not active:
                result["verified"] = True
                return result
            for row in active:
                if (len(row) != 3 or row[2] != user
                        or not isinstance(row[0], str)
                        or not re.fullmatch(r"[A-Za-z0-9_]+", row[0])):
                    raise DataOpsError("query drain returned an unexpected identity; no broad cancellation")
            for query_id, _state, _user in active:
                if query_id in result["cancelled"]:
                    continue
                try:
                    bounded_query(client,
                        f"CALL system.runtime.kill_query(query_id => '{query_id}', "
                        "message => 'Shirokuma disposable DataOps run cleanup')",
                        min(deadline, time.monotonic() + 10))
                    result["cancelled"].append(query_id)
                except Exception as error:
                    # A query may finish between inspection and cancellation; a fresh
                    # inspection must still prove termination before cleanup proceeds.
                    result["cancel_errors"].append({"query_id": query_id, "error": str(error)})
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DataOpsError("server query drain timed out")
            time.sleep(min(0.25, remaining))
    except Exception as error:
        result["errors"].append(str(error))
    return result


def isolated_environment(output: Path) -> dict[str, str]:
    # Do not read user dbt profiles, proxy settings, or inherit dbt target overrides.
    env = {
        key: value for key, value in os.environ.items()
        if not key.upper().endswith("_PROXY")
        and not key.startswith(("DBT_", "DAGSTER_", "TRINO_"))
    }
    env.update({
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
        "DBT_USE_COLORS": "false",
        "DAGSTER_TELEMETRY_ENABLED": "false",
        "NO_PROXY": "127.0.0.1,localhost,::1",
        "NETRC": os.devnull,
    })
    return env


def prepare_profile(output: Path, url: str, token: str) -> Path:
    TRINO.TrinoClient(url)  # Enforce the same loopback-only contract as SQL smoke.
    parsed = urllib.parse.urlsplit(url)
    profiles = output / "profiles"
    profiles.mkdir()
    # JSON is valid YAML; serializing protects the generated profile from injection.
    profile = {
        "config": {"send_anonymous_usage_stats": False},
        "shirokuma_local_dataops": {
            "target": "local",
            "outputs": {"local": {
                "type": "trino", "method": "none", "host": parsed.hostname,
                "port": parsed.port or 80, "user": run_user(token),
                "database": "iceberg", "schema": "local_experiment",
                "threads": 1, "http_scheme": "http", "retries": 1,
                "session_properties": {"query_max_run_time": "120s"},
            }},
        },
    }
    write_json(profiles / "profiles.yml", profile)
    return profiles


def dbt_command(command: str, attempt: Path, profiles: Path, token: str, fail: bool) -> list[str]:
    names_for(token)
    return [
        "rtk", "proxy", str(Path(sys.executable).parent / "dbt"),
        "--no-send-anonymous-usage-stats", "--no-use-colors", "--no-partial-parse",
        command, "--project-dir", str(HERE / "dbt"), "--profiles-dir", str(profiles),
        "--target-path", str(attempt / f"{command}-target"),
        "--log-path", str(attempt / f"{command}-logs"),
        "--vars", json.dumps({"relation_prefix": f"dataops_{token}", "run_token": token,
                               "exercise_failure": fail}),
    ]


def run_command(args: list[str], logfile: Path, env: dict[str, str], timeout: float) -> int:
    with logfile.open("w") as log:
        process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, env=env,
                                   cwd=HERE, start_new_session=True)
        try:
            return process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            if isinstance(error, KeyboardInterrupt):
                raise
            raise DataOpsError(f"dbt command exceeded {timeout:g}s; see {logfile}")


def result_nodes(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [{key: entry.get(key) for key in ("unique_id", "status", "failures", "message")}
            for entry in json.loads(path.read_text()).get("results", [])]


def materialize_dbt(attempt: Path, profiles: Path, token: str, fail: bool,
                    timeout: float, env: dict[str, str]) -> dict[str, Any]:
    from dagster import DagsterInstance, asset, materialize

    attempt.mkdir()
    invocations: list[dict[str, Any]] = []

    @asset(name="local_lakehouse_dbt")
    def local_lakehouse_dbt(context):
        for command in ("build", "test"):
            logfile = attempt / f"{command}.log"
            entry: dict[str, Any] = {"command": command, "log": str(logfile)}
            invocations.append(entry)
            try:
                entry["exit_code"] = run_command(
                    dbt_command(command, attempt, profiles, token, fail), logfile, env, timeout)
            except BaseException as error:
                entry["process_error"] = f"{type(error).__name__}: {error}"
                raise
            finally:
                entry["nodes"] = result_nodes(attempt / f"{command}-target" / "run_results.json")
            if entry["exit_code"] != 0:
                raise DataOpsError(f"dbt {command} failed; see {logfile}")
        context.add_output_metadata({"relations": list(names_for(token)), "run_token": token})

    (attempt / "dagster").mkdir()
    with DagsterInstance.local_temp(str(attempt / "dagster"),
                                   overrides={"telemetry": {"enabled": False}}) as instance:
        result = materialize([local_lakehouse_dbt], instance=instance, raise_on_error=False)
        summary = {"success": result.success, "run_id": result.run_id,
                   "materializations": len(result.get_asset_materialization_events()),
                   "commands": invocations}
    write_json(attempt / "result.json", summary)
    return summary


def expected_test_failure(attempt: dict[str, Any]) -> bool:
    commands = attempt.get("commands", [])
    if (len(commands) != 1 or commands[0].get("command") != "build"
            or commands[0].get("exit_code") != 1 or commands[0].get("process_error")):
        return False
    nodes = commands[0].get("nodes", [])
    failed = [node for node in nodes if node["status"] not in {"success", "pass", "skipped"}]
    return (not attempt["success"] and len(failed) == 1
            and failed[0]["unique_id"] == "test.shirokuma_local_dataops.mart_expected_rows"
            and failed[0]["status"] == "fail" and failed[0]["failures"] == 1)


def run_experiment(client: Any, output: Path, exercise_failure: bool = False,
                   timeout: float = 300, token: str | None = None,
                   materializer: Callable[..., dict[str, Any]] = materialize_dbt) -> dict[str, Any]:
    token = token or uuid.uuid4().hex
    names = names_for(token)
    output.mkdir(parents=True, exist_ok=False)
    os.chmod(output, 0o700)
    report: dict[str, Any] = {"result": "failed", "scope": "local-experiment-only",
                              "run_token": token, "evidence": str(output),
                              "relations": [f"{NAMESPACE}.{name}" for name in names],
                              "attempts": [], "errors": []}
    mutation_started = False
    try:
        before = client.query(FIXTURE_SQL)
        report["fixture_before"] = before
        if before["rows"] != FIXTURE_ROWS:
            raise DataOpsError("retained fixture differs before materialization")
        collision = set(names) & existing_tables(client)
        if collision:
            raise DataOpsError(f"relation collision; refusing to modify {sorted(collision)}")
        profiles = prepare_profile(output, client.url, token)
        env = isolated_environment(output)
        sequence = [("first", False), ("repeat", False)]
        if exercise_failure:
            sequence += [("controlled-failure", True), ("retry", False)]
        readbacks = []
        for label, fail in sequence:
            if mutation_started:
                for name in names:
                    verify_marker(client, name, token)
            mutation_started = True
            result = materializer(output / label, profiles, token, fail, timeout, env)
            result["label"] = label
            report["attempts"].append(result)
            if fail:
                if not expected_test_failure(result):
                    raise DataOpsError("controlled failure did not surface the expected dbt row-count test")
                report["controlled_failure_observed"] = True
                continue
            if not result["success"] or result.get("materializations", 0) != 1:
                raise DataOpsError(f"Dagster asset materialization failed during {label}")
            for name in names:
                verify_marker(client, name, token)
            readback = client.query(f"SELECT id, name_upper FROM {NAMESPACE}.{names[1]} ORDER BY id")
            result["readback"] = readback
            if readback["rows"] != MART_ROWS:
                raise DataOpsError(f"mart returned unexpected rows during {label}")
            readbacks.append(readback["rows"])
        report["deterministic"] = len(readbacks) >= 2 and all(rows == readbacks[0] for rows in readbacks)
    except KeyboardInterrupt:
        report["errors"].append("interrupted; dbt subprocess stopped before cleanup")
    except Exception as error:
        report["errors"].append(str(error))
    finally:
        if mutation_started:
            report["query_drain"] = drain_queries(client, token)
            cleanup_deadline = time.monotonic() + 60
            if report["query_drain"]["verified"]:
                report["cleanup"] = cleanup_tables(client, names, token, cleanup_deadline)
            else:
                report["cleanup"] = {
                    "status": "deferred", "removed": [], "pending_relations": list(names),
                    "errors": ["server queries could not be proven terminal; cleanup deferred"],
                }
            report["errors"].extend(report["cleanup"]["errors"])
        else:
            cleanup_deadline = time.monotonic() + 60
        try:
            report["fixture_after"] = bounded_query(client, FIXTURE_SQL, cleanup_deadline)
            report["fixture_unchanged"] = report["fixture_after"]["rows"] == FIXTURE_ROWS
            if not report["fixture_unchanged"]:
                report["errors"].append("retained fixture differs after experiment")
        except Exception as error:
            report["errors"].append(f"retained fixture could not be verified after experiment: {error}")
    if not report["errors"] and report.get("deterministic"):
        report["result"] = "passed"
    write_json(output / "summary.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:18080")
    parser.add_argument("--timeout", type=float, default=300, help="timeout per dbt command in seconds")
    parser.add_argument("--exercise-failure", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.timeout <= 900:
        parser.error("timeout must be between 1 and 900 seconds")
    token = uuid.uuid4().hex
    try:
        report = run_experiment(TRINO.TrinoClient(args.url),
                                ROOT / "tmp/local-experiment/dataops" / token,
                                args.exercise_failure, args.timeout, token)
    except Exception as error:
        report = {"result": "failed", "error": str(error)}
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
