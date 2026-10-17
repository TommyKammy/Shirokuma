"""Five resident lab Bears with explicit sandbox, publish, and rollback actions."""
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


def latest_snapshot(url, table):
    rows = sql(url, 'SELECT snapshot_id FROM ' + identifier(table + '$snapshots') +
               ' ORDER BY committed_at DESC LIMIT 1')['rows']
    return rows[0][0] if rows else None


def audit_table(url, full_table_name):
    table = identifier(full_table_name)
    row_count = sql(url, 'SELECT COUNT(*) FROM ' + table)['rows'][0][0]
    columns = [dict(name=row[0], type=row[1]) for row in
               sql(url, 'SHOW COLUMNS FROM ' + table)['rows']]
    null_counts = {}
    if columns:
        counts = sql(url, 'SELECT ' + ', '.join(
            'COUNT(*) - COUNT("' + c['name'].replace('"', '""') + '")' for c in columns) +
            ' FROM ' + table)['rows'][0]
        null_counts = dict(zip([c['name'] for c in columns], counts))
    sample_rows = sql(url, 'SELECT * FROM ' + table + ' LIMIT 5')['rows']
    return dict(table=full_table_name, row_count=row_count, column_count=len(columns),
                columns=columns, null_counts=null_counts, sample_rows=sample_rows,
                snapshot_id=latest_snapshot(url, full_table_name), quality_passed=row_count > 0)


def data_table(value, rollback=False):
    """Normalize explicit lab table names without accepting SQL fragments."""
    parts = value.split('.')
    if parts[0] == 'iceberg':
        parts = parts[1:]
    if not 1 <= len(parts) <= 2 or not all(re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', p) for p in parts):
        raise ValueError('invalid data table name')
    if rollback:
        return 'iceberg.' + ('.'.join(parts) if len(parts) == 2 else 'marts.' + parts[0])
    if len(parts) == 2 and parts[0] not in {'raw', 'sandbox', 'staging', 'marts'}:
        raise ValueError('stage/publish table must use raw, sandbox, staging, or marts')
    return parts[-1]


def data(args):
    request = getattr(args, 'request', '')
    rollback = getattr(args, 'rollback', None)
    publish = getattr(args, 'publish', None)
    if rollback:
        snapshot = str(getattr(args, 'snapshot_id', ''))
        if not re.fullmatch(r'[0-9]+', snapshot):
            raise ValueError('rollback requires a digits-only snapshot-id')
        target = data_table(rollback, rollback=True)
        statement = wr.check_sql_policy('ALTER TABLE ' + identifier(target) +
            f' EXECUTE rollback_to_snapshot({int(snapshot)})', mode='admin-write')
        with wr.trino() as url:
            sql(url, statement)
            audit = audit_table(url, target)
        return dict(wap_action='rollback', table=target, restored_snapshot_id=int(snapshot), audit=audit, valid=True)
    if publish:
        name = data_table(publish)
        source, target = 'iceberg.sandbox.' + name, 'iceberg.marts.' + name
        with wr.trino() as url:
            source_audit = audit_table(url, source)
            if not source_audit['quality_passed']:
                return dict(wap_action='publish', table=target, source_table=source, valid=False,
                            audit=source_audit, error='sandbox table failed quality audit (row_count must be > 0)')
            previous = attempt(lambda: dict(snapshot_id=latest_snapshot(url, target))).get('snapshot_id')
            statement = wr.check_sql_policy('CREATE OR REPLACE TABLE ' + identifier(target) +
                ' AS SELECT * FROM ' + identifier(source), mode='admin-write')
            sql(url, statement)
            audit = audit_table(url, target)
        return dict(wap_action='publish', source_table=source, table=target, previous_snapshot_id=previous,
                    published_snapshot_id=audit['snapshot_id'], audit=audit, valid=True)
    explicit_sql = getattr(args, 'sql', None)
    if not explicit_sql and not request:
        raise ValueError('data bear requires request, --sql, --publish, or --rollback')
    stage = getattr(args, 'stage_table', None)
    staged_table = 'iceberg.sandbox.' + data_table(stage) if stage else None
    llm_usage = {}
    with wr.trino() as url:
        statement = explicit_sql.strip() if explicit_sql else generate_data_sql(url, request, llm_usage)
        def validate():
            if not re.match(r'(?i)^(SELECT|WITH)\b', statement):
                raise ValueError('SELECT model required')
            return sql(url, 'EXPLAIN ' + wr.check_sql_policy(statement, mode='read-only'))
        validation = attempt(validate)
        if staged_table and 'error' not in validation:
            sql(url, wr.check_sql_policy('CREATE SCHEMA IF NOT EXISTS iceberg.sandbox', mode='sandbox-write'))
            sql(url, wr.check_sql_policy('CREATE OR REPLACE TABLE ' + identifier(staged_table) +
                ' AS ' + wr.check_sql_policy(statement), mode='sandbox-write'))
            audit = audit_table(url, staged_table)
            return dict(wap_action='stage', intent=request, generated_sql=statement,
                        valid=audit['quality_passed'], validation=validation, staged_table=staged_table, audit=audit,
                        **({'llm_usage': llm_usage} if llm_usage else {}))
    return dict(intent=request, generated_sql=statement, valid='error' not in validation, validation=validation,
                **({'llm_usage': llm_usage} if llm_usage else {}))


def generate_data_sql(url, request, llm_usage=None):
    sources = sql(url, "SELECT table_schema, table_name, column_name, data_type FROM iceberg.information_schema.columns WHERE table_schema IN ('marts', 'tpch_tiny', 'raw', 'sandbox')")
    body = {"model": os.environ.get("BEAR_MODEL", "qwen3.8-flash-next"), "temperature": 0,
            "max_tokens": 512, "enable_thinking": False,
            "messages": [{"role": "system", "content": "Return JSON with generated_sql: one SELECT statement (WITH allowed). Use only supplied Iceberg sources. No DDL or DML."},
                         {"role": "user", "content": json.dumps({"request": request, "sources": sources["rows"]})}]}
    completion = wr.http_json(os.environ.get("BEAR_MODEL_URL", "http://127.0.0.1:8001/v1") +
                              "/chat/completions", json.dumps(body).encode(), {"Content-Type": "application/json"}, timeout=30)
    if llm_usage is not None and (completion.get('model') or completion.get('usage')):
        usage = completion.get('usage') or {}
        llm_usage.update(model_provider='local-openai', model_name=completion.get('model') or body['model'],
                         prompt_tokens=usage.get('prompt_tokens'), completion_tokens=usage.get('completion_tokens'))
    content = completion["choices"][0]["message"]["content"].strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.MULTILINE).strip()
    return json.loads(content)["generated_sql"].strip()


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
            for schema in ("tpch_tiny", "raw", "sandbox", "staging", "marts", "smoke"):
                result = attempt(lambda: sql(url, "SHOW TABLES FROM iceberg." + identifier(schema)))
                metadata[schema] = result.get("rows", [])
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
    extra = {}
    target = result.get('table') or result.get('staged_table')
    if target:
        target = str(target)
        audit = result.get('audit')
        snapshot = result.get('published_snapshot_id')
        if snapshot is None:
            snapshot = result.get('restored_snapshot_id')
        if snapshot is None and isinstance(audit, dict):
            snapshot = audit.get('snapshot_id')
        if snapshot is not None:
            target += f'@{snapshot}'
        extra['target'] = target[:256]
    if result.get('wap_action') in ('publish', 'rollback'):
        extra['risk_level'] = 'normal'
    usage = result.get('llm_usage')
    if isinstance(usage, dict):
        for key in ('model_provider', 'model_name'):
            if isinstance(usage.get(key), str) and usage[key]:
                extra[key] = usage[key]
        for key in ('prompt_tokens', 'completion_tokens'):
            if type(usage.get(key)) is int and usage[key] >= 0:
                extra[key] = usage[key]
    result["telemetry"] = wr.telemetry("bear." + NAMES[args.bear], int((time.monotonic() - started) * 1000), status, prefix="", **extra)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="bear", required=True)
    sub.add_parser("sre").add_argument("action", nargs="?", choices=["inspect"], default="inspect")
    command = sub.add_parser("query")
    command.add_argument("target", nargs="?", default="latest")
    command.add_argument("--sql")
    command = sub.add_parser("data")
    command.add_argument("request", nargs="?", default="")
    for flag in ('sql', 'stage-table', 'publish', 'rollback', 'snapshot-id'):
        command.add_argument('--' + flag)
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
