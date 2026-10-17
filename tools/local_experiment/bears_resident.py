"""Five resident lab Bears with explicit sandbox, publish, and rollback actions."""
import argparse
from functools import partial
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import quote

import warehouse_resident as wr
import wap_resident as wap

NAMES = dict(sre="sre", query="query_doctor", data="data_engineer",
             catalog="catalog_curator", policy="policy_gatekeeper")
WAP_REGISTRY = wap.WAP_REGISTRY
identifier = wr.identifier
literal = wr.literal
extract_source_tables = wap.extract_source_tables
check_quality_assertions = wap.check_quality_assertions
data_table = wap.data_table


class _BearsRuntime:
    """Resolve hooks live, including when loaded outside sys.modules."""

    def __getattr__(self, name):
        return globals()[name]


_runtime = _BearsRuntime()
load_wap_registry = partial(wap.load_wap_registry, _runtime)
save_wap_entry = partial(wap.save_wap_entry, _runtime)
latest_snapshot = partial(wap.latest_snapshot, _runtime)
audit_table = partial(wap.audit_table, _runtime)
data = partial(wap.data, _runtime)


def sql(url, statement):
    return wr.execute(url, statement, {"X-Trino-User": "shirokuma-bears", "X-Trino-Source": "shirokuma-bears",
                                        "X-Trino-Catalog": "iceberg", "X-Trino-Schema": "marts"})


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


def generate_data_sql(url, request, llm_usage=None, sources_rows=None):
    sources = sql(url, "SELECT table_schema, table_name, column_name, data_type FROM iceberg.information_schema.columns WHERE table_schema IN ('marts', 'tpch_tiny', 'raw', 'sandbox')")
    if sources_rows is not None:
        sources_rows.extend(sources['rows'])
    return _complete_data_sql(dict(request=request, sources=sources['rows']), llm_usage)


def generate_data_sql_retry(url, request, sources_rows, failed_sql, error_message):
    return _complete_data_sql(dict(request=request, sources=sources_rows,
                                  failed_sql=failed_sql, error_message=error_message))


def _complete_data_sql(context, llm_usage=None):
    body = {"model": os.environ.get("BEAR_MODEL", "qwen3.8-flash-next"), "temperature": 0,
            "max_tokens": 512, "enable_thinking": False,
            "messages": [{"role": "system", "content": "Return JSON with generated_sql: one SELECT statement (WITH allowed). Use only supplied Iceberg sources. No DDL or DML. Use Trino ANSI SQL only (no QUALIFY; wrap window functions in a CTE and filter with WHERE). Correct failed_sql using error_message when supplied."},
                         {"role": "user", "content": json.dumps(context)}]}
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


def models_inventory():
    models = Path(__file__).parent / 'dataops' / 'dbt_persistent' / 'models'
    dbt_models, wap_models = [], []
    for path in sorted(models.rglob('*.sql')):
        definition = path.read_text()
        layer = 'staging' if 'staging' in path.relative_to(models).parts else 'marts'
        refs = re.findall(r"ref\(\s*['\"]([\w]+)['\"]\s*\)", definition)
        sources = ['.'.join(parts) for parts in re.findall(
            r"source\(\s*['\"]([\w]+)['\"]\s*,\s*['\"]([\w]+)['\"]\s*\)", definition)]
        dbt_models.append(dict(name=path.stem, layer=layer,
                               table=f'iceberg.{layer}.{path.stem}', sql=definition,
                               sources=sorted(set(refs + sources))))
    for name, entry in sorted(load_wap_registry().items()):
        wap_models.append(dict(name=name, layer='wap',
                               table=entry.get('table') or entry.get('staged_table') or f'iceberg.sandbox.{name}',
                               sql=entry.get('sql', ''), sources=entry.get('sources', [])))
    all_models = dbt_models + wap_models
    def depends_on(source, model):
        return (source == model['name'] or
                source.removeprefix('iceberg.') == model['table'].removeprefix('iceberg.'))
    for model in all_models:
        model['downstream'] = sorted({other['table'] for other in all_models
                                      if any(depends_on(source, model) for source in other['sources'])})
    return dict(dbt_models=dbt_models, wap_models=wap_models,
                lineage={model['name']: model['sources'] for model in all_models})


def _table_lineage(table):
    inventory = models_inventory()
    models = inventory['dbt_models'] + inventory['wap_models']
    model = next((m for m in models if m['table'] == table), None)
    result = dict(upstream=model['sources'] if model else [],
                  downstream=sorted({m['table'] for m in models if any(
                      source.removeprefix('iceberg.') == table.removeprefix('iceberg.') or
                      source == table.split('.')[-1] for source in m['sources'])}))
    if model:
        result['layer'] = model['layer']
        if model['layer'] == 'wap':
            result['wap_sql'] = model['sql']
    return result


def drop_table(table):
    message = 'drop is only allowed in raw, sandbox, staging, or scratch schemas'
    if not isinstance(table, str) or not re.fullmatch(
            r'(?:iceberg\.)?(?:raw|sandbox|staging|scratch)\.[A-Za-z_][A-Za-z_0-9]*', table):
        raise ValueError(message)
    full_table = table if table.startswith('iceberg.') else 'iceberg.' + table
    started, status = time.monotonic(), 'success'
    try:
        with wr.trino() as url:
            sql(url, 'DROP TABLE IF EXISTS ' + identifier(full_table))
    except Exception:
        status = 'failed'
        raise
    finally:
        telemetry = wr.telemetry('catalog.drop', int((time.monotonic() - started) * 1000),
                                 status, prefix='', target=full_table)
    return dict(action='drop', table=full_table, dropped=True, telemetry=telemetry)


def catalog(args):
    metadata = {}
    if getattr(args, 'snapshot_id', None) is not None:
        if not getattr(args, 'table', None):
            raise ValueError('table is required for snapshot_id')
        if not re.fullmatch(r'[0-9]+', str(args.snapshot_id)):
            raise ValueError('snapshot_id must be digits-only')
        table = args.table
        if not table.startswith('iceberg.'):
            table = 'iceberg.' + table if '.' in table else 'iceberg.marts.' + table
        quoted = identifier(table)
        with wr.trino() as url:
            preview = sql(url, f'SELECT * FROM {quoted} FOR VERSION AS OF {int(args.snapshot_id)} LIMIT 50')
            count = sql(url, f'SELECT COUNT(*) FROM {quoted} FOR VERSION AS OF {int(args.snapshot_id)}')['rows'][0][0]
            current_count = attempt(lambda: sql(url, f'SELECT COUNT(*) FROM {quoted}')['rows'][0][0])
        return dict(action='snapshot_preview', catalog='iceberg', table=table,
                    snapshot_id=str(args.snapshot_id), row_count=count,
                    current_row_count=None if isinstance(current_count, dict) else current_count,
                    preview=preview)
    if args.action == "lineage" and not args.table:
        models = Path(__file__).parent / "dataops" / "dbt_persistent" / "models"
        lineage = {p.stem: sorted(set(re.findall(r"ref\(\s*['\"]([\w]+)['\"]\s*\)", p.read_text())))
                   for p in sorted(models.rglob("*.sql"))}
        for name, entry in load_wap_registry().items():
            if entry.get('sources'):
                lineage.setdefault(name, entry['sources'])
        return dict(action="lineage", catalog="iceberg", metadata={"lineage": lineage,
                    "lineage_note": "Dependencies from local dbt models and recorded WAP models"})
    include_config = getattr(args, "include_config", True)
    with wr.trino() as url:
        if args.action == "search":
            schemas = ("tpch_tiny", *wr.WRITABLE_SCHEMAS, "smoke")
            if not include_config:
                for schema in schemas:
                    metadata[schema] = []
                schema_list = ", ".join(wr.literal(s) for s in schemas)
                result = attempt(lambda: sql(
                    url,
                    f"SELECT table_schema, table_name FROM iceberg.information_schema.tables "
                    f"WHERE table_schema IN ({schema_list}) ORDER BY table_schema, table_name",
                ))
                rows = result.get("rows", [])
                if rows and all(isinstance(r, list) and len(r) >= 2 for r in rows):
                    for row in rows:
                        if row[0] in metadata:
                            metadata[row[0]].append([row[1]])
                elif rows:
                    for schema in schemas:
                        metadata[schema] = rows
            else:
                for schema in schemas:
                    result = attempt(lambda: sql(url, "SHOW TABLES FROM iceberg." + identifier(schema)))
                    metadata[schema] = result.get("rows", [])
        else:
            table = args.table
            if not table:
                raise ValueError("table name required")
            if not table.startswith("iceberg."):
                table = "iceberg." + table if "." in table else "iceberg.marts." + table
            metadata["columns"] = sql(url, "SHOW COLUMNS FROM " + identifier(table))["rows"]
            if include_config and getattr(args, 'detail', None) != 'full':
                metadata["definition"] = sql(url, "SHOW CREATE TABLE " + identifier(table))["rows"]
            metadata["snapshots"] = attempt(lambda: sql(url, "SELECT snapshot_id, parent_id, committed_at FROM " + identifier(table + "$snapshots") + " ORDER BY committed_at DESC"))
            if getattr(args, 'detail', None) == 'full':
                metadata['preview'] = attempt(lambda: sql(url, 'SELECT * FROM ' + identifier(table) + ' LIMIT 50'))
                metadata['profile'] = attempt(lambda: audit_table(url, table))
                metadata['definition'] = attempt(lambda: sql(url, 'SHOW CREATE TABLE ' + identifier(table))['rows'])
                metadata['lineage_detail'] = _table_lineage(table)
            if args.action == "lineage":
                models = Path(__file__).parent / "dataops" / "dbt_persistent" / "models"
                definitions = [p.read_text() for p in models.rglob("*.sql") if p.stem == table.split(".")[-1]]
                metadata["lineage"] = sorted(set(ref for definition in definitions for ref in re.findall(r"(?:ref|source)\([^)]*?['\"]([\w]+)['\"]\s*\)", definition)))
                metadata["lineage_note"] = "Dependencies from local dbt model definitions; empty means no recorded model"
                if not metadata['lineage']:
                    entry = load_wap_registry().get(table.split('.')[-1]) or {}
                    metadata['lineage'] = entry.get('sources', [])
                    if 'sql' in entry:
                        metadata['wap_sql'] = entry['sql']
        if include_config:
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
    for flag in ('sql', 'stage-table', 'publish', 'rollback', 'snapshot-id', 'refresh'):
        command.add_argument('--' + flag)
    command.add_argument('--allow-shrink', action='store_true')
    command.add_argument('--min-rows', type=int, default=1)
    command.add_argument('--not-null', default='')
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
