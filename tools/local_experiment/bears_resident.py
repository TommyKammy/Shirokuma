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
AI_CONFIG_PATH = wap.state_path('ai_config.json')
_ai_config = {}


def load_ai_config():
    config = dict(provider='local-openai',
                  model=os.environ.get('BEAR_MODEL', 'qwen3.8-flash-next'),
                  api_url=os.environ.get('BEAR_MODEL_URL', 'http://127.0.0.1:8001/v1'),
                  api_key_env='BEAR_API_KEY' if os.environ.get('BEAR_API_KEY') else 'OPENAI_API_KEY',
                  api_key='', timeout=30, max_tokens=1024, temperature=0.0)
    config.update(_ai_config)
    try:
        saved = json.loads(AI_CONFIG_PATH.read_text())
        if isinstance(saved, dict):
            config.update(saved)
    except (OSError, ValueError):
        pass
    return config


def save_ai_config(config):
    """Activate settings only after they have been persisted successfully."""
    active = load_ai_config()
    config = dict(config)
    if config.get('api_key') == '********':
        del config['api_key']
    if any(key in config and config[key] != active.get(key) for key in ('api_url', 'provider')):
        if not config.get('api_key'):
            active['api_key'] = ''
    active.update(config)
    try:
        wap.persist_json(AI_CONFIG_PATH, active)
    except OSError as err:
        raise RuntimeError('failed to persist AI config: ' + str(err)) from err
    _ai_config.update(active)
    return active


def resolve_api_key(config):
    return config.get('api_key') or os.environ.get(config.get('api_key_env') or 'BEAR_API_KEY')


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


def sql(url, statement, max_rows=None):
    headers = {"X-Trino-User": "shirokuma-bears", "X-Trino-Source": "shirokuma-bears",
               "X-Trino-Catalog": "iceberg", "X-Trino-Schema": "marts"}
    if max_rows is not None:
        return wr.execute(url, statement, headers, max_rows=max_rows)
    # Honor explicit larger limits used by exports while keeping ordinary calls
    # and their default result cap unchanged.
    limit = re.search(r'\bLIMIT ([0-9]+)\s*$', statement)
    if limit and int(limit[1]) > 1000:
        return wr.execute(url, statement, headers, max_rows=int(limit[1]))
    return wr.execute(url, statement, headers)


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


def generate_data_sql(url, request, llm_usage=None, sources_rows=None, config=None):
    sources = sql(url, "SELECT table_schema, table_name, column_name, data_type FROM iceberg.information_schema.columns WHERE table_schema IN ('marts', 'tpch_tiny', 'raw', 'sandbox')", max_rows=10000)
    if sources_rows is not None:
        sources_rows.extend(sources['rows'])
    return _complete_data_sql(dict(request=request, sources=sources['rows']), llm_usage, config)


def generate_data_sql_retry(url, request, sources_rows, failed_sql, error_message, llm_usage=None, config=None):
    return _complete_data_sql(dict(request=request, sources=sources_rows,
                                  failed_sql=failed_sql, error_message=error_message), llm_usage, config)


def _extract_sql(content: str) -> str:
    content = content.strip()
    fence = re.search(r'```(?:sql|json)?\s*\n?(.*?)```', content, re.I | re.S)
    if fence:
        content = fence[1].strip()
    try:
        parsed = json.loads(content)
    except ValueError:
        statement = content
    else:
        if not isinstance(parsed, dict) or not isinstance(parsed.get('generated_sql'), str):
            raise ValueError('completion must contain generated_sql')
        statement = parsed['generated_sql']
    return statement.strip().removesuffix(';').rstrip()


def _ai_completion(config, body):
    headers = {'Content-Type': 'application/json'}
    key = resolve_api_key(config)
    if key:
        headers['Authorization'] = 'Bearer ' + key
    return wr.http_json(config['api_url'].rstrip('/') + '/chat/completions',
                        json.dumps(body).encode(), headers, timeout=config['timeout'])


def _complete_data_sql(context, llm_usage=None, config=None):
    active = load_ai_config()
    active.update(config or {})
    body = {"model": active['model'], "temperature": active['temperature'],
            "max_tokens": active['max_tokens'],
            "messages": [{"role": "system", "content": "Return JSON with generated_sql: one SELECT statement (WITH allowed). Use only supplied Iceberg sources. No DDL or DML. Use Trino ANSI SQL only (no QUALIFY; wrap window functions in a CTE and filter with WHERE). Correct failed_sql using error_message when supplied. Business semantic definitions (apply only to supplied columns): Completed sales require status = 'COMPLETED'; exclude 'CANCELLED' and 'REFUNDED'. Revenue uses total_amount at order grain, or quantity * extended_price * (1 - discount) at item grain. Time-grain aggregations use date_trunc('month', order_date), or the requested day/year grain. Grain-safe joins: avoid duplicate row multiplication when joining orders, order_items, or customers; aggregate items to order grain before summing order totals and use unique customer keys."},
                         {"role": "user", "content": json.dumps(context)}]}
    if active['provider'] == 'local-openai':
        body['enable_thinking'] = False
    completion = _ai_completion(active, body)
    if llm_usage is not None and (completion.get('model') or completion.get('usage')):
        usage = completion.get('usage') or {}
        llm_usage.update(model_provider=active['provider'], model_name=completion.get('model') or body['model'])
        for key in ('prompt_tokens', 'completion_tokens'):
            count = usage.get(key)
            if type(count) is int and count >= 0:
                llm_usage[key] = (llm_usage.get(key) or 0) + count
            else:
                llm_usage.setdefault(key, None)
    return _extract_sql(completion["choices"][0]["message"]["content"])


def test_ai_connection(config=None) -> dict:
    started = time.monotonic()
    try:
        active = load_ai_config()
        active.update(config or {})
        completion = _ai_completion(active, dict(model=active['model'],
            temperature=active['temperature'], max_tokens=active['max_tokens'],
            messages=[dict(role='user', content='What is 1+1?')]))
        if not completion['choices'][0]['message']['content']:
            raise ValueError('empty completion')
        return dict(status='connected', provider=active['provider'], model=active['model'],
                    latency_ms=int((time.monotonic() - started) * 1000), usage=completion.get('usage') or {})
    except Exception as exc:
        return dict(status='failed', error=str(exc), latency_ms=int((time.monotonic() - started) * 1000))


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


def qualified_table(table):
    if not isinstance(table, str) or not re.fullmatch(
            r'(?:iceberg\.)?[A-Za-z_][A-Za-z_0-9]*\.[A-Za-z_][A-Za-z_0-9]*', table):
        raise ValueError('table must be [iceberg.]<schema>.<identifier>')
    return table if table.startswith('iceberg.') else 'iceberg.' + table


def clone_table(source_table, target_table=None, snapshot_id=None):
    full_source = qualified_table(source_table)
    if target_table is None or target_table == '':
        target_table = full_source.split('.')[-1] + '_clone'
    if isinstance(target_table, str) and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', target_table):
        target_table = 'sandbox.' + target_table
    if not isinstance(target_table, str) or not re.fullmatch(
            r'(?:iceberg\.)?(?:sandbox|raw)\.[A-Za-z_][A-Za-z_0-9]*', target_table):
        raise ValueError('clone target must be in sandbox or raw schema')
    full_target = qualified_table(target_table)
    select_sql = 'SELECT * FROM ' + identifier(full_source)
    if snapshot_id not in (None, ''):
        if not re.fullmatch(r'[0-9]+', str(snapshot_id)):
            raise ValueError('snapshot_id must be digits-only')
        select_sql += f' FOR VERSION AS OF {int(snapshot_id)}'
    started, status = time.monotonic(), 'success'
    try:
        with wr.trino() as url:
            for statement in (
                    'CREATE SCHEMA IF NOT EXISTS iceberg.' + full_target.split('.')[1],
                    'CREATE OR REPLACE TABLE ' + identifier(full_target) + ' AS ' + select_sql):
                sql(url, wr.check_sql_policy(statement, mode='sandbox-write'))
            audit = audit_table(url, full_target)
    except Exception:
        status = 'failed'
        raise
    finally:
        telemetry = wr.telemetry('catalog.clone', int((time.monotonic() - started) * 1000),
                                 status, prefix='', target=full_target)
    return dict(action='clone', source_table=full_source, table=full_target,
                snapshot_id=str(snapshot_id) if snapshot_id not in (None, '') else None,
                audit=audit, telemetry=telemetry)


def activity_history(limit=50):
    if type(limit) is not int or limit < 1:
        raise ValueError('limit must be a positive integer')

    def runtime():
        with wr.trino() as url:
            return sql(url, 'SELECT query_id, state, "user", source, query FROM system.runtime.queries '
                       "WHERE source <> 'shirokuma-bears' ORDER BY created DESC LIMIT " + str(limit))

    def pawprints():
        from agenthouse.manage import Client
        return json.loads(Client().query(
            'SELECT * FROM agenthouse.pawprints ORDER BY timestamp DESC LIMIT 100 FORMAT JSON'))['data']

    runtime_queries = attempt(runtime)
    events = attempt(pawprints)
    if isinstance(events, dict):
        return dict(runtime_queries=runtime_queries, query_history=events, load_history=events)
    loads = {'lake.ingest', 'lake.export', 'catalog.clone', 'catalog.drop', 'bear.data_engineer'}
    return dict(runtime_queries=runtime_queries,
                query_history=[e for e in events if e.get('action_type') == 'warehouse.query'],
                load_history=[e for e in events if e.get('action_type') in loads])


def drop_table(table):
    message = 'drop is only allowed in raw, sandbox, staging, marts, or scratch schemas'
    if not isinstance(table, str) or not re.fullmatch(
            r'(?:iceberg\.)?(?:raw|sandbox|staging|marts|scratch)\.[A-Za-z_][A-Za-z_0-9]*', table):
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
    command = sub.add_parser('ai')
    actions = command.add_subparsers(dest='action', required=True)
    actions.add_parser('test')
    config_command = actions.add_parser('config')
    config_command.add_argument('--provider', choices=['local-openai', 'openai', 'bedrock', 'custom'])
    config_command.add_argument('--model')
    config_command.add_argument('--url', dest='api_url')
    config_command.add_argument('--key-env', dest='api_key_env')
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
    args = parser.parse_args()
    if args.bear == 'ai':
        if args.action == 'test':
            result = test_ai_connection()
        else:
            changes = {key: value for key, value in vars(args).items()
                       if key not in ('bear', 'action') and value is not None}
            result = (save_ai_config(changes) if changes else load_ai_config()).copy()
            if result.get('api_key'):
                result['api_key'] = '***'
    else:
        result = run(args)
    print(json.dumps(result))
    return int("error" in result)


if __name__ == "__main__":
    sys.exit(main())
