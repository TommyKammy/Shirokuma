"""Five resident lab Bears; observations and advice never mutate workloads."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import quote

import warehouse_resident as wr

NAMES = dict(sre="sre", query="query_doctor", data="data_engineer",
             catalog="catalog_curator", policy="policy_gatekeeper")


def sql(url, statement):
    return wr.execute(url, statement, {"X-Trino-User": "shirokuma-bears", "X-Trino-Source": "shirokuma-bears",
                                        "X-Trino-Catalog": "iceberg", "X-Trino-Schema": "marts"})


def literal(value):
    return "'" + value.replace("'", "''") + "'"


def identifier(value):
    return ".".join('"' + part.replace('"', '""') + '"' for part in value.split("."))


def attempt(function):
    try:
        return function()
    except Exception as exc:
        return {"error": str(exc)}


def sre(_args):
    components = {}
    for name in ("deploy/trino-local", "deploy/polaris", "sts/polaris-postgresql", "sts/seaweedfs"):
        obj = attempt(lambda: json.loads(wr.kubectl("get", name, "-o", "json")))
        components[name] = {"healthy": "error" not in obj and
                            obj.get("status", {}).get("readyReplicas", 0) >=
                            max(1, obj.get("spec", {}).get("replicas", 1)), "observation": obj}
    workloads = json.loads(wr.kubectl("get", "deploy,sts", "-A", "-o", "json"))["items"]
    for service in ("agenthouse", "loki"):
        matches = [w for w in workloads if service in w["metadata"]["name"]]
        components[service] = {"healthy": bool(matches) and all(
            w.get("status", {}).get("readyReplicas", 0) >= max(1, w["spec"].get("replicas", 1)) for w in matches)}
        components[service]["recent_errors"] = [attempt(lambda w=w: wr.kubectl(
            "logs", "-n", w["metadata"]["namespace"], w["kind"].lower() + "/" + w["metadata"]["name"],
            "--since=10m", "--tail=100")) for w in matches]
        if service == "loki":
            components[service]["log_entries"] = [attempt(lambda w=w: json.loads(wr.kubectl(
                "get", "--raw", "/api/v1/namespaces/" + w["metadata"]["namespace"] +
                "/services/http:loki:3100/proxy/loki/api/v1/query_range?query=" +
                quote('{namespace="shirokuma-dev"} |~ "(?i)error|fatal"', safe="") + "&limit=20"))) for w in matches]
        if any(isinstance(entry, dict) and "error" in entry for entry in
               components[service].get("log_entries", []) + components[service]["recent_errors"]):
            components[service]["healthy"] = False
    metrics = attempt(lambda: wr.kubectl("top", "pods", "--containers"))
    from agenthouse.manage import Client
    events = attempt(lambda: Client().query("SELECT * FROM agenthouse.pawprints WHERE status = 'failed' ORDER BY timestamp DESC LIMIT 20 FORMAT JSON"))
    healthy = all(c["healthy"] for c in components.values()) and not any(
        isinstance(v, dict) and "error" in v for v in (metrics, events))
    return dict(status="healthy" if healthy else "degraded", components=components,
                metrics=metrics, recent_errors=events, summary="Resident workload readiness and recent telemetry collected")


def query(args):
    with wr.trino() as url:
        query_id, stats = args.target, {}
        statement = args.sql
        if not statement:
            if query_id == "explain":
                raise ValueError("explain requires --sql")
            condition = "WHERE source <> 'shirokuma-bears'" if query_id == "latest" else "WHERE query_id = " + literal(query_id)
            stats = sql(url, "SELECT * FROM system.runtime.queries " + condition +
                        " ORDER BY created DESC LIMIT 1")
            if not stats["rows"]:
                raise ValueError("query not found in resident runtime history")
            row = dict(zip([c["name"] for c in stats["columns"]], stats["rows"][0]))
            query_id, statement = row["query_id"], row["query"]
            stats["execution"] = attempt(lambda: wr.http_json(url + "/v1/query/" + quote(query_id, safe="")))
        plan = sql(url, "EXPLAIN (TYPE DISTRIBUTED) " + statement)
    text = "\n".join(str(row[0]) for row in plan["rows"])
    recommendations = []
    for pattern, advice in [("TableScan", "Check scan predicates and partition pruning"),
                            ("REPLICATED|BROADCAST", "Check broadcast build-side size"),
                            ("PARTITIONED", "Check partition count and data skew")]:
        if re.search(pattern, text, re.I):
            recommendations.append(advice)
    return dict(query_id=query_id or plan["query_id"], analysis={"cost": text,
                "stages": len(re.findall(r"Fragment \d+", text)), "statistics": stats,
                "recommendations": recommendations})


def data(args):
    with wr.trino() as url:
        sources = sql(url, "SELECT table_schema, table_name, column_name, data_type FROM iceberg.information_schema.columns WHERE table_schema IN ('marts', 'tpch_tiny')")
        body = {"model": os.environ.get("BEAR_MODEL", "qwen3.8-flash-next"), "temperature": 0,
                "max_tokens": 512, "enable_thinking": False,
                "messages": [{"role": "system", "content": "Return JSON with generated_sql: one SELECT statement (WITH allowed). Use only supplied Iceberg sources. No DDL or DML."},
                             {"role": "user", "content": json.dumps({"request": args.request, "sources": sources["rows"]})}]}
        completion = wr.http_json(os.environ.get("BEAR_MODEL_URL", "http://127.0.0.1:8001/v1") +
                                  "/chat/completions", json.dumps(body).encode(), {"Content-Type": "application/json"}, timeout=30)
        content = completion["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.MULTILINE).strip()
        statement = json.loads(content)["generated_sql"].strip()
        validation = attempt(lambda: sql(url, "EXPLAIN " + statement)) if re.match(r"(?i)^(SELECT|WITH)\b", statement) else {"error": "SELECT model required"}
    return dict(intent=args.request, generated_sql=statement, valid="error" not in validation, validation=validation)


def catalog(args):
    metadata = {}
    if args.action == "lineage" and not args.table:
        models = Path(__file__).parent / "dataops" / "dbt_persistent" / "models"
        lineage = {p.stem: sorted(set(re.findall(r"ref\(\s*['\"]([\w]+)['\"]\s*\)", p.read_text())))
                   for p in sorted(models.rglob("*.sql"))}
        return dict(action="lineage", catalog="iceberg", metadata={"lineage": lineage,
                    "lineage_note": "Dependencies from local dbt model definitions"})
    with wr.trino() as url:
        if args.action == "search":
            for schema in ("tpch_tiny", "staging", "marts", "smoke"):
                metadata[schema] = sql(url, "SHOW TABLES FROM iceberg." + identifier(schema))["rows"]
        else:
            table = args.table
            if not table:
                raise ValueError("table name required")
            if not table.startswith("iceberg."):
                table = "iceberg." + table if "." in table else "iceberg.marts." + table
            metadata["columns"] = sql(url, "SHOW COLUMNS FROM " + identifier(table))["rows"]
            metadata["definition"] = sql(url, "SHOW CREATE TABLE " + identifier(table))["rows"]
            metadata["snapshots"] = attempt(lambda: sql(url, "SELECT snapshot_id, parent_id, committed_at FROM " + identifier(table + "$snapshots") + " ORDER BY committed_at DESC"))
            if args.action == "lineage":
                models = Path(__file__).parent / "dataops" / "dbt_persistent" / "models"
                definitions = [p.read_text() for p in models.rglob("*.sql") if p.stem == table.split(".")[-1]]
                metadata["lineage"] = sorted(set(ref for definition in definitions for ref in re.findall(r"(?:ref|source)\([^)]*?['\"]([\w]+)['\"]\s*\)", definition)))
                metadata["lineage_note"] = "Dependencies from local dbt model definitions; empty means no recorded model"
        metadata["catalog_config"] = attempt(lambda: wr.kubectl("get", "configmap", "-l", "app.kubernetes.io/name=trino-local", "-o", "json"))
    return dict(action=args.action, catalog="iceberg", metadata=metadata)


def policy(args):
    if args.action == "check":
        if not args.manifest:
            raise ValueError("manifest path required")
        obj = json.loads(wr.kubectl("create", "--dry-run=client", "--validate=false", "-f", args.manifest, "-o", "json"))
    else:
        obj = json.loads(wr.kubectl("get", "deploy,sts,ds,job,cronjob,pods", "-o", "json"))
    violations = []
    for item in obj.get("items", [obj]):
        spec = item.get("spec", {})
        if "jobTemplate" in spec:
            spec = spec["jobTemplate"]["spec"]
        spec = spec.get("template", {}).get("spec", spec)
        pod_security = spec.get("securityContext", {})
        for container in spec.get("containers", []) + spec.get("initContainers", []) + spec.get("ephemeralContainers", []):
            security = container.get("securityContext", {})
            checks = {"readOnlyRootFilesystem": security.get("readOnlyRootFilesystem") is True,
                      "runAsNonRoot": security.get("runAsNonRoot", pod_security.get("runAsNonRoot")) is True and security.get("runAsUser", pod_security.get("runAsUser")) != 0,
                      "drop ALL capabilities": "ALL" in security.get("capabilities", {}).get("drop", []),
                      "cpu/memory limits": all(container.get("resources", {}).get("limits", {}).get(k) not in (None, "0", 0) for k in ("cpu", "memory"))}
            violations.extend(f"{item['metadata']['name']}/{container['name']}: {rule}" for rule, ok in checks.items() if not ok)
        if "containers" not in spec:
            violations.append(item.get("kind", "unknown") + ": no workload pod specification")
    return dict(advisory="deny" if violations else "allow", violations=violations)


def run(args):
    started, status = time.monotonic(), "success"
    try:
        result = globals()[args.bear](args)
        if result.get("status") == "degraded" or result.get("valid") is False or result.get("advisory") == "deny":
            status = "failed"
    except Exception as exc:
        status, result = "failed", {"error": str(exc)}
    result["bear"] = NAMES[args.bear]
    result["telemetry"] = wr.telemetry("bear." + NAMES[args.bear], int((time.monotonic() - started) * 1000), status, prefix="")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="bear", required=True)
    sub.add_parser("sre").add_argument("action", nargs="?", choices=["inspect"], default="inspect")
    command = sub.add_parser("query")
    command.add_argument("target", nargs="?", default="latest")
    command.add_argument("--sql")
    sub.add_parser("data").add_argument("request")
    command = sub.add_parser("catalog")
    command.add_argument("action", nargs="?", choices=["search", "lineage", "describe"], default="search")
    command.add_argument("table", nargs="?")
    command = sub.add_parser("policy")
    command.add_argument("action", nargs="?", choices=["check", "baseline"], default="baseline")
    command.add_argument("manifest", nargs="?")
    result = run(parser.parse_args())
    print(json.dumps(result))
    return int("error" in result)


if __name__ == "__main__":
    sys.exit(main())
