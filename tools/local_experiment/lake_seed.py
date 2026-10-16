"""Seed and verify persistent TPC-H tables in the local Iceberg catalog."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

SPEC = importlib.util.spec_from_file_location(
    "warehouse_resident", Path(__file__).resolve().with_name("warehouse_resident.py"))
warehouse_resident = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(warehouse_resident)

TABLES = ("region", "nation", "part", "supplier", "partsupp", "customer", "orders", "lineitem")


def run(action, scale="tiny"):
    if action not in {"seed", "status"} or scale not in {"tiny", "sf1"}:
        raise ValueError("expected seed/status and scale tiny/sf1")
    started = time.monotonic()
    schema = f"iceberg.tpch_{scale}"
    report = dict(schema=schema, scale=scale, tables=[], result="incomplete")
    headers = {"X-Trino-User": "shirokuma-warehouse", "X-Trino-Source": f"lake.{action}"}
    try:
        if action == "seed" and warehouse_resident.status("LOCAL")["replicas"] == 0:
            warehouse_resident.transition("wake", "LOCAL")
        with warehouse_resident.trino() as url:
            def sql(statement):
                result = warehouse_resident.execute(url, statement, headers)
                if result["truncated"]:
                    raise RuntimeError("Trino result truncated: " + statement)
                return result["rows"]

            if action == "seed":
                sql(f"CREATE SCHEMA IF NOT EXISTS {schema}")
                exists = True
            else:
                exists = [f"tpch_{scale}"] in sql("SHOW SCHEMAS FROM iceberg")
            existing = {row[0] for row in sql(f"SHOW TABLES FROM {schema}")} if exists else set()
            created = set()
            if action == "seed":
                for table in TABLES:
                    if table not in existing:
                        sql(f"CREATE TABLE {schema}.{table} WITH (format = 'PARQUET') "
                            f"AS SELECT * FROM tpch.{scale}.{table}")
                        created.add(table)
            for table in TABLES:
                source_rows = sql(f"SELECT count(*) FROM tpch.{scale}.{table}")[0][0]
                rows, state = None, "missing"
                if table in existing or table in created:
                    rows = sql(f"SELECT count(*) FROM {schema}.{table}")[0][0]
                    state = "created" if table in created else "present"
                    if rows != source_rows:
                        state = "mismatch"
                report["tables"].append(dict(table=table, state=state, rows=rows, source_rows=source_rows))
            if all(table["state"] in {"created", "present"} for table in report["tables"]):
                report["result"] = "ready"
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
    finally:
        report["duration_ms"] = int((time.monotonic() - started) * 1000)
        warehouse_resident.telemetry(f"lake.{action}", report["duration_ms"],
                                     "success" if report["result"] == "ready" else "failed", prefix="")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("seed", "status"))
    parser.add_argument("--scale", choices=("tiny", "sf1"), default="tiny")
    args = parser.parse_args()
    report = run(args.action, args.scale)
    print(json.dumps(report))
    return 0 if report["result"] == "ready" else 1


if __name__ == "__main__":
    sys.exit(main())
