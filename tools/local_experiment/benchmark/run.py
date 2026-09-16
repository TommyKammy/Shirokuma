#!/usr/bin/env python3
"""Run the pinned Trino TPC-H SF10 functional suite over disposable Iceberg tables."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CONTEXT = "colima-mac-studio-solo"
NAMESPACE = "iceberg.local_experiment"
TABLES = ("region", "nation", "supplier", "customer", "part", "partsupp", "orders", "lineitem")
SF10_COUNTS = dict(zip(TABLES, (5, 25, 100_000, 1_500_000, 2_000_000, 8_000_000, 15_000_000, 59_986_052)))
FIXTURE_SQL = "SELECT id, name FROM iceberg.smoke.fixture_v1 ORDER BY id"
FIXTURE_ROWS = [[1, "shirokuma"], [2, "polaris"]]
SOURCE_COMMIT = "50b0b50b75abd47f830b7805ee1b51716eb4065e"
MIN_FREE_BYTES = 50 * 1024**3
Q15_ORIGINAL_REVENUE = "sum(l.extendedprice*(1-l.discount))"
Q15_EXACT_REVENUE = "sum(CAST(l.extendedprice AS DECIMAL(15,2)) * (DECIMAL '1.00' - CAST(l.discount AS DECIMAL(3,2))))"
Q15_ADAPTATION = {
    "query": "q15", "name": "exact monetary arithmetic before repeated CTE aggregation",
    "original": Q15_ORIGINAL_REVENUE, "replacement": Q15_EXACT_REVENUE,
    "reason": "Inlined DOUBLE SUM evaluations may disagree at exact-equality MAX filtering; keep price/discount in their original two-decimal domain.",
    "applied_to": ["generator reference", "Iceberg"],
}
SPEC = importlib.util.spec_from_file_location("benchmark_trino_base", HERE.parent / "local_trino_smoke.py")
TRINO = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TRINO)


class BenchmarkError(RuntimeError):
    pass


def validate_token(token: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise BenchmarkError("run token must be a UUID in hexadecimal form")
    return token


def table_names(token: str) -> dict[str, str]:
    validate_token(token)
    return {table: f"tpch_{token}_{table}" for table in TABLES}


def run_user(token: str) -> str:
    return "shirokuma-local-tpch-" + validate_token(token)


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    temporary.replace(path)


def verify_inputs(directory: Path = HERE) -> dict[str, Any]:
    manifest = json.loads((directory / "query_inputs.json").read_text())
    required = {f"queries/q{i:02}.sql" for i in range(1, 23)} | {"LICENSE.trino"}
    if manifest.get("commit") != SOURCE_COMMIT or {item["path"] for item in manifest["inputs"]} != required:
        raise BenchmarkError("benchmark query input identity differs")
    if len(manifest["inputs"]) != len(required):
        raise BenchmarkError("duplicate query input")
    for item in manifest["inputs"]:
        data = (directory / item["path"]).read_bytes()
        if len(data) != item["bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise BenchmarkError("benchmark query input checksum differs: " + item["path"])
    return manifest


def render_query(number: int, token: str | None = None, directory: Path = HERE) -> str:
    if number not in range(1, 23):
        raise BenchmarkError("query number must be between 1 and 22")
    sql = (directory / "queries" / f"q{number:02}.sql").read_text()
    if number == 15:
        if sql.count(Q15_ORIGINAL_REVENUE) != 1:
            raise BenchmarkError("Q15 monetary expression differs from the verified adaptation input")
        sql = sql.replace(Q15_ORIGINAL_REVENUE, Q15_EXACT_REVENUE)
    variables = {"database": "tpch", "schema": "sf10", "prefix": "", "scale": "10"}
    if token is not None:
        variables.update(database="iceberg", schema="local_experiment", prefix="tpch_" + validate_token(token) + "_")
    for key, value in variables.items():
        sql = sql.replace("${" + key + "}", value)
    if "${" in sql:
        raise BenchmarkError("unresolved benchmark query variable")
    return sql.rstrip().removesuffix(";").rstrip()


class BenchmarkClient(TRINO.TrinoClient):
    """Loopback-only client with run identity, server deadline and retained metrics."""

    def __init__(self, url: str, user: str, timeout: float = 900) -> None:
        super().__init__(url, timeout)
        if not re.fullmatch(r"shirokuma-local-tpch-(?:control-)?[0-9a-f]{32}", user):
            raise BenchmarkError("unexpected benchmark user identity")
        self.user = user

    def query(self, sql: str) -> dict[str, Any]:
        started = time.monotonic()
        deadline = started + self.timeout
        next_uri: str | None = self.url + "/v1/statement"
        first, query_id = True, ""
        rows, columns, column_types = [], [], []
        stats: dict[str, Any] = {}
        received = 0
        while next_uri:
            target = urllib.parse.urlsplit(next_uri)
            if ((target.scheme, target.netloc) != self.origin or target.username is not None
                    or target.password is not None or not target.path.startswith("/v1/statement")):
                raise BenchmarkError("polling URL escaped the loopback statement endpoint")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BenchmarkError(f"query timeout: {query_id}")
            headers = {"X-Trino-User": self.user, "X-Trino-Source": "shirokuma-local-tpch",
                       "X-Trino-Session": f"query_max_run_time={max(1, math.ceil(self.timeout))}s"}
            request = urllib.request.Request(next_uri, data=sql.encode() if first else None,
                                             headers=headers, method="POST" if first else "GET")
            try:
                with self.opener.open(request, timeout=min(remaining, 30)) as response:
                    raw = response.read(10 * 1024**2 + 1)
                received += len(raw)
                if len(raw) > 10 * 1024**2 or received > 64 * 1024**2:
                    raise BenchmarkError("query result exceeds the bounded response budget")
                page = json.loads(raw)
            except (OSError, ValueError) as error:
                raise BenchmarkError(f"local benchmark request failed for {query_id}: {error}") from error
            query_id = page.get("id", query_id)
            if "error" in page:
                error = page["error"]
                raise BenchmarkError(f"{query_id}: {error.get('errorName', 'QUERY_FAILED')}: {error.get('message', '')}")
            if "columns" in page:
                columns = [column["name"] for column in page["columns"]]
                column_types = [column["type"] for column in page["columns"]]
            rows.extend(page.get("data", []))
            if len(rows) > 500_000:
                raise BenchmarkError("query result exceeds the row budget")
            stats = page.get("stats", stats)
            next_uri, first = page.get("nextUri"), False
        return {"query_id": query_id, "columns": columns, "column_types": column_types,
                "rows": rows, "wall_seconds": time.monotonic() - started,
                "stats": {key: stats[key] for key in ("state", "elapsedTimeMillis", "cpuTimeMillis",
                    "processedRows", "processedBytes", "peakMemoryBytes", "spilledBytes") if key in stats}}


def timed_query(client: Any, sql: str, deadline: float | None = None) -> dict[str, Any]:
    previous = client.timeout
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BenchmarkError("query cleanup budget exhausted")
        client.timeout = min(previous, remaining)
    try:
        return client.query(sql)
    finally:
        client.timeout = previous


def drain_queries(control: Any, token: str, timeout: float = 90) -> dict[str, Any]:
    user = run_user(token)
    result: dict[str, Any] = {"verified": False, "user": user, "cancelled": [], "errors": []}
    deadline = time.monotonic() + timeout
    sql = ('SELECT query_id, state, "user" FROM system.runtime.queries '
           f'WHERE "user" = \'{user}\' AND state NOT IN (\'FINISHED\', \'FAILED\')')
    try:
        while True:
            active = timed_query(control, sql, min(deadline, time.monotonic() + 10))["rows"]
            result["active"] = active
            if not active:
                result["verified"] = True
                return result
            for row in active:
                if len(row) != 3 or row[2] != user or not isinstance(row[0], str) or not re.fullmatch(r"[A-Za-z0-9_]+", row[0]):
                    raise BenchmarkError("query drain returned an unexpected run identity")
            for query_id, _, _ in active:
                try:
                    timed_query(control, f"CALL system.runtime.kill_query(query_id => '{query_id}', "
                                "message => 'Shirokuma disposable TPC-H cleanup')", min(deadline, time.monotonic() + 10))
                    if query_id not in result["cancelled"]:
                        result["cancelled"].append(query_id)
                except Exception as error:
                    result.setdefault("cancellation_errors", []).append(str(error))
            if time.monotonic() >= deadline:
                raise BenchmarkError("server query drain timed out")
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))
    except Exception as error:
        result["errors"].append(str(error))
    return result


def existing_tables(client: Any, deadline: float | None = None) -> set[str]:
    return {row[0] for row in timed_query(client, f"SHOW TABLES FROM {NAMESPACE}", deadline)["rows"]}


def verify_marker(client: Any, name: str, token: str, deadline: float | None = None) -> None:
    if name not in table_names(token).values():
        raise BenchmarkError("cleanup target is outside the run")
    ddl = timed_query(client, f"SHOW CREATE TABLE {NAMESPACE}.{name}", deadline)["rows"]
    marker = f"COMMENT 'shirokuma-local-tpch:{token}'"
    if len(ddl) != 1 or marker.lower() not in ddl[0][0].lower():
        raise BenchmarkError(f"ownership marker missing; preserved {NAMESPACE}.{name}")


def cleanup_tables(control: Any, token: str, timeout: float = 180) -> dict[str, Any]:
    result: dict[str, Any] = {"removed": [], "errors": []}
    deadline = time.monotonic() + timeout
    names = list(table_names(token).values())
    try:
        present = existing_tables(control, deadline)
        for name in reversed(names):
            if name not in present:
                continue
            try:
                verify_marker(control, name, token, deadline)
                timed_query(control, f"DROP TABLE {NAMESPACE}.{name}", deadline)
                result["removed"].append(name)
            except Exception as error:
                result["errors"].append(str(error))
        result["remaining"] = sorted(set(names) & existing_tables(control, deadline))
        if result["remaining"]:
            result["errors"].append("benchmark tables remain; use the recorded run token for scoped recovery")
    except Exception as error:
        result["errors"].append(str(error))
    return result


def compare_results(reference: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
    """Compare bags, preserving duplicate rows and exact integral/text values."""
    if reference["columns"] != actual["columns"] or len(reference["rows"]) != len(actual["rows"]):
        raise BenchmarkError("query result shape differs from the generator reference")
    width = len(reference["columns"])
    for result in (reference, actual):
        kinds = result.get("column_types")
        if not isinstance(kinds, list) or len(kinds) != width or any(not isinstance(kind, str) or not kind for kind in kinds):
            raise BenchmarkError("query result column types are incomplete")
        if any(len(row) != width for row in result["rows"]):
            raise BenchmarkError("query result row width differs")
    type_mappings = []
    for i, (left, right) in enumerate(zip(reference["column_types"], actual["column_types"])):
        # Iceberg STRING has no length parameter; Trino's TPCH VARCHAR(n) loses
        # only this constraint on CTAS. Validate its original bound explicitly.
        varchar = re.fullmatch(r"varchar(?:\(([0-9]+)\))?", left)
        if left != right:
            if not varchar or varchar.group(1) is None or right != "varchar":
                raise BenchmarkError(f"query result column type differs at column {i}: {left} != {right}")
            type_mappings.append({"column": reference["columns"][i], "reference": left, "iceberg": right,
                                  "rule": "Iceberg STRING removes VARCHAR length; values remain within source bound"})
        for row in reference["rows"] + actual["rows"]:
            value = row[i]
            if value is None:
                continue
            if varchar:
                if not isinstance(value, str) or (varchar.group(1) and len(value) > int(varchar.group(1))):
                    raise BenchmarkError(f"query string value violates the reference type at column {i}")
            elif left in {"double", "real", "bigint", "integer", "smallint", "tinyint"}:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise BenchmarkError(f"query numeric value violates the result type at column {i}")
                if left not in {"double", "real"} and not isinstance(value, int):
                    raise BenchmarkError(f"query integral value violates the result type at column {i}")
    float_columns = {i for i, kind in enumerate(reference["column_types"]) if kind in {"double", "real"}}

    def key(row: list[Any]) -> tuple[Any, ...]:
        return tuple((i, value) for i, value in enumerate(row) if i not in float_columns)

    expected: dict[tuple[Any, ...], list[list[Any]]] = defaultdict(list)
    observed: dict[tuple[Any, ...], list[list[Any]]] = defaultdict(list)
    for row in reference["rows"]:
        expected[key(row)].append(row)
    for row in actual["rows"]:
        observed[key(row)].append(row)
    if expected.keys() != observed.keys():
        raise BenchmarkError("query result keys differ from the generator reference")
    for group, reference_rows in expected.items():
        actual_rows = observed[group]
        if len(reference_rows) != len(actual_rows):
            raise BenchmarkError("query result multiplicity differs")
        sort_key = lambda row: tuple((row[i] is not None, row[i] or 0) for i in sorted(float_columns))
        for wanted, got in zip(sorted(reference_rows, key=sort_key), sorted(actual_rows, key=sort_key)):
            if len(wanted) != len(got):
                raise BenchmarkError("query result row width differs")
            for i, (left, right) in enumerate(zip(wanted, got)):
                if i in float_columns and left is not None and right is not None:
                    if not math.isfinite(float(left)) or not math.isfinite(float(right)) or not math.isclose(float(left), float(right), rel_tol=1e-8, abs_tol=1e-6):
                        raise BenchmarkError(f"query floating result differs at column {i}")
                elif left != right:
                    raise BenchmarkError(f"query result differs at column {i}")
    return {"matched": True, "rows": len(reference["rows"]), "comparison": "unordered multiset",
            "type_mappings": type_mappings,
            "floating_relative_tolerance": 1e-8, "floating_absolute_tolerance": 1e-6}


def command_output(args: list[str]) -> str:
    result = subprocess.run(["rtk", "proxy", *args], cwd=ROOT, text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise BenchmarkError(f"environment command failed ({args[0]}, exit {result.returncode})")
    return result.stdout


def disk_free_bytes(text: str) -> int:
    lines = [line.split() for line in text.strip().splitlines()]
    if len(lines) != 2 or len(lines[1]) < 6:
        raise BenchmarkError("unexpected POSIX df output")
    return int(lines[1][3]) * 1024


def collect_environment() -> dict[str, Any]:
    profiles = [json.loads(line) for line in command_output(["colima", "list", "--json"]).splitlines() if line.strip()]
    matches = [profile for profile in profiles if profile.get("name") == "mac-studio-solo"]
    if len(matches) != 1 or matches[0].get("status") != "Running" or matches[0].get("arch") != "aarch64":
        raise BenchmarkError("the ARM64 mac-studio-solo Colima profile must be running")
    kubectl = ["kubectl", "--context", CONTEXT]
    server = command_output(kubectl + ["config", "view", "--minify", "-o", "jsonpath={.clusters[0].cluster.server}"]).strip()
    parsed = urllib.parse.urlsplit(server)
    if parsed.scheme != "https" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username:
        raise BenchmarkError("the fixed Kubernetes context must use loopback HTTPS")
    nodes = json.loads(command_output(kubectl + ["get", "nodes", "-o", "json"]))["items"]
    if len(nodes) != 1 or nodes[0]["status"]["nodeInfo"]["architecture"] != "arm64":
        raise BenchmarkError("benchmark requires the single ARM64 local node")
    deployment = json.loads(command_output(kubectl + ["-n", "shirokuma-dev", "get", "deployment", "trino-local", "-o", "json"]))
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    if not re.fullmatch(r"shirokuma\.local/trino@sha256:[0-9a-f]{64}", container["image"]):
        raise BenchmarkError("Trino runtime must use a recorded local image digest")
    if (deployment["status"].get("observedGeneration") != deployment["metadata"]["generation"]
            or deployment["status"].get("readyReplicas") != 1):
        raise BenchmarkError("Trino rollout is not ready")
    free = disk_free_bytes(command_output(["colima", "ssh", "--profile", "mac-studio-solo", "--", "df", "-Pk", "/var/lib/rancher/k3s/storage"]))
    host_free = shutil.disk_usage(ROOT).free
    if min(free, host_free) < MIN_FREE_BYTES:
        raise BenchmarkError("SF10 requires at least 50 GiB free on both host and VM storage")
    return {"colima": matches[0], "node": nodes[0]["status"]["nodeInfo"],
            "trino": {"image": container["image"], "resources": container.get("resources", {})},
            "host_free_bytes": host_free, "vm_storage_free_bytes": free,
            "minimum_free_bytes": MIN_FREE_BYTES,
            "co_located": True, "limitation": "Generator, client and measured system share one host; relative functional results only, not production capacity or official TPC-H performance"}


def progress(report: dict[str, Any], output: Path, message: str) -> None:
    report["phase"] = message
    write_json(output / "summary.json", report)
    print(message, file=sys.stderr, flush=True)


def run_benchmark(worker: Any, control: Any, output: Path, token: str,
                  environment: Callable[[], dict[str, Any]] = collect_environment) -> dict[str, Any]:
    names = table_names(token)
    report: dict[str, Any] = {"schema_version": 1, "result": "running", "scope": "local-experiment-only",
        "run_token": token, "scale_factor": 10, "nominal_source_gb": 10,
        "official_tpc_result": False, "queries_expected": 22, "tables": [], "queries": [], "errors": [],
        "runtime_query_adaptations": [Q15_ADAPTATION],
        "query_user": run_user(token), "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    mutation_started = False
    try:
        report["inputs"] = verify_inputs()
        report["environment"] = environment()
        report["version"] = worker.query("SELECT version() AS version")["rows"]
        if report["version"] != [["483"]]:
            raise BenchmarkError("benchmark inputs require Trino 483")
        report["fixture_before"] = control.query(FIXTURE_SQL)
        if report["fixture_before"]["rows"] != FIXTURE_ROWS:
            raise BenchmarkError("retained fixture differs before benchmark")
        if set(names.values()) & existing_tables(control):
            raise BenchmarkError("benchmark table name collision; refusing any mutation")
        progress(report, output, "preflight passed; loading SF10 Iceberg tables")
        for table, name in names.items():
            source = f"tpch.sf10.{table}"
            source_count = worker.query(f"SELECT count(*) AS rows FROM {source}")
            if source_count["rows"] != [[SF10_COUNTS[table]]]:
                raise BenchmarkError(f"SF10 source row count differs for {table}: {source_count['rows']}")
            mutation_started = True
            load = worker.query(f"CREATE TABLE {NAMESPACE}.{name} COMMENT 'shirokuma-local-tpch:{token}' "
                                f"WITH (format = 'PARQUET') AS SELECT * FROM {source}")
            verify_marker(control, name, token)
            count = worker.query(f"SELECT count(*) AS rows FROM {NAMESPACE}.{name}")
            if count["rows"] != source_count["rows"]:
                raise BenchmarkError(f"Iceberg row count differs for {table}")
            files = worker.query(f'SELECT count(*) AS files, coalesce(sum(file_size_in_bytes), 0) AS bytes, '
                                 f'coalesce(sum(record_count), 0) AS records FROM iceberg.local_experiment."{name}$files"')
            if len(files["rows"]) != 1 or files["rows"][0][2] != SF10_COUNTS[table]:
                raise BenchmarkError(f"Iceberg data-file record count differs for {table}")
            report["tables"].append({"table": table, "relation": f"{NAMESPACE}.{name}", "rows": SF10_COUNTS[table],
                "load": load, "source_count": source_count, "iceberg_count": count, "iceberg_files": files["rows"][0]})
            progress(report, output, f"loaded {table}: {SF10_COUNTS[table]} rows")
        for number in range(1, 23):
            label = f"q{number:02}"
            progress(report, output, f"running {label}: generator reference then Iceberg")
            reference_sql, actual_sql = render_query(number), render_query(number, token)
            (output / f"{label}-reference.sql").write_text(reference_sql + ";\n")
            (output / f"{label}-iceberg.sql").write_text(actual_sql + ";\n")
            reference = worker.query(reference_sql)
            write_json(output / f"{label}-reference.json", reference)
            actual = worker.query(actual_sql)
            write_json(output / f"{label}-iceberg.json", actual)
            comparison = compare_results(reference, actual)
            report["queries"].append({"query": label, "comparison": comparison,
                "reference_sql_sha256": hashlib.sha256(reference_sql.encode()).hexdigest(),
                "iceberg_sql_sha256": hashlib.sha256(actual_sql.encode()).hexdigest(),
                "reference_query_id": reference["query_id"], "iceberg_query_id": actual["query_id"],
                "reference_wall_seconds": reference["wall_seconds"], "iceberg_wall_seconds": actual["wall_seconds"],
                "reference_stats": reference.get("stats", {}), "iceberg_stats": actual.get("stats", {})})
            progress(report, output, f"{label} passed: {comparison['rows']} matching result rows")
    except KeyboardInterrupt:
        report["errors"].append("benchmark interrupted; server queries will be drained before cleanup")
    except Exception as error:
        report["errors"].append(str(error))
    finally:
        progress(report, output, "draining run queries before scoped cleanup")
        report["query_drain"] = drain_queries(control, token)
        if mutation_started and report["query_drain"]["verified"]:
            report["cleanup"] = cleanup_tables(control, token)
            report["errors"].extend(report["cleanup"]["errors"])
        elif mutation_started:
            report["cleanup"] = {"status": "deferred", "pending_relations": list(names.values()), "removed": []}
            report["errors"].append("server queries could not be proven terminal; cleanup deferred")
        if not report["query_drain"]["verified"]:
            report["errors"].extend(report["query_drain"]["errors"])
        try:
            report["fixture_after"] = timed_query(control, FIXTURE_SQL, time.monotonic() + 30)
            report["fixture_unchanged"] = report["fixture_after"]["rows"] == FIXTURE_ROWS
            if not report["fixture_unchanged"]:
                report["errors"].append("retained fixture differs after benchmark")
        except Exception as error:
            report["errors"].append("could not verify retained fixture after benchmark: " + str(error))
    report["result"] = ("passed" if not report["errors"] and len(report["queries"]) == 22
                        and len(report["tables"]) == 8 else "failed")
    report["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    progress(report, output, "completed: " + report["result"])
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:18080")
    parser.add_argument("--scale-factor", type=int, choices=[10], default=10)
    parser.add_argument("--query-timeout", type=float, default=900)
    parser.add_argument("--cleanup-run", help="recover only the exact retained run token after a failed run")
    args = parser.parse_args()
    if not math.isfinite(args.query_timeout) or not 10 <= args.query_timeout <= 3600:
        parser.error("query timeout must be between 10 and 3600 seconds")
    token = args.cleanup_run or uuid.uuid4().hex
    try:
        validate_token(token)
        output = ROOT / "tmp/local-experiment/benchmark" / token
        if args.cleanup_run:
            prior = json.loads((output / "summary.json").read_text())
            if prior.get("run_token") != token or prior.get("scope") != "local-experiment-only":
                raise BenchmarkError("recovery evidence does not identify this local benchmark run")
        else:
            output.mkdir(parents=True, exist_ok=False)
            output.chmod(0o700)
        with (output / "run.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            worker = BenchmarkClient(args.url, run_user(token), args.query_timeout)
            control = BenchmarkClient(args.url, "shirokuma-local-tpch-control-" + token, 30)
            if args.cleanup_run:
                drain = drain_queries(control, token)
                cleanup = cleanup_tables(control, token) if drain["verified"] else {"errors": ["query drain not verified; no DROP attempted"]}
                report = {"run_token": token, "query_drain": drain, "cleanup": cleanup,
                          "result": "passed" if drain["verified"] and not cleanup["errors"] else "failed"}
                write_json(output / "recovery.json", report)
            else:
                report = run_benchmark(worker, control, output, token)
        print(json.dumps({"result": report["result"], "run_token": token, "evidence": str(output),
                          "queries_passed": len(report.get("queries", [])), "errors": report.get("errors", [])}))
        return 0 if report["result"] == "passed" else 1
    except Exception as error:
        print(json.dumps({"result": "failed", "run_token": token, "error": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
