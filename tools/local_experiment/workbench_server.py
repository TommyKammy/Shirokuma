#!/usr/bin/env python3
"""Serve the local Shirokuma Workbench with standard-library HTTP tooling."""
import argparse
import base64
import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer as HTTPServer
import json
from pathlib import Path
import re
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import bears_resident
import lake_ingest
import jobs_resident
import alerts_resident
import stages_resident
import marts_resident
import schedule_resident
import stage_exports
import warehouse_resident
import request_auth
from agenthouse import manage

INDEX = Path(__file__).parent / "workbench" / "index.html"
ALL_ROLES = {'admin', 'data_engineer', 'bi_viewer'}
EDIT_ROLES = {'admin', 'data_engineer'}
GET_ROUTES = {path: ALL_ROLES for path in (
    '/', '/index.html', '/api/session', '/api/status', '/api/catalog', '/api/lineage',
    '/api/history', '/api/models', '/api/pawprints')}
GET_ROUTES['/api/ai/config'] = {'admin'}
GET_ROUTES['/api/stages'] = {'admin'}
GET_ROUTES['/api/marts'] = {'admin'}
GET_ROUTES.update({path: EDIT_ROLES for path in ('/api/jobs', '/api/jobs/runs', '/api/jobs/schedules', '/api/alerts')})
POST_ROUTES = {path: EDIT_ROLES for path in (
    '/api/warehouse/chill', '/api/warehouse/autochill', '/api/lake/ingest',
    '/api/lake/preview', '/api/lake/export', '/api/catalog/drop',
    '/api/catalog/clone', '/api/models/build')}
POST_ROUTES.update({path: ALL_ROLES for path in (
    '/api/warehouse/wake', '/api/warehouse/query', '/api/bears/run')})
POST_ROUTES.update({path: {'admin'} for path in ('/api/ai/config', '/api/ai/test')})
POST_ROUTES.update({path: {'admin'} for path in ('/api/stages/export', '/api/stages/exports', '/api/stages/save', '/api/stages/files', '/api/stages/preview', '/api/stages/import', '/api/stages/schedule', '/api/stages/scan')})
POST_ROUTES.update({path: {'admin'} for path in ('/api/marts/save', '/api/marts/run', '/api/marts/schedule')})
POST_ROUTES.update({path: EDIT_ROLES for path in ('/api/jobs/save', '/api/jobs/run', '/api/jobs/detail', '/api/jobs/schedule', '/api/alerts/ack')})


def masked_ai_config(config):
    return {**config, 'api_key': '********' if config.get('api_key') else ''}


def ai_config_input(body):
    config = {key: value for key, value in body.items()
              if key in {'provider', 'model', 'api_url', 'api_key', 'timeout'}}
    if 'provider' in config and config['provider'] not in ('local-openai', 'openai', 'bedrock', 'custom'):
        raise ValueError('invalid AI provider')
    for key in ('model', 'api_url', 'api_key'):
        if key in config and not isinstance(config[key], str):
            raise ValueError(key + ' must be a string')
    for key in ('model', 'api_url'):
        if key in config and not config[key].strip():
            raise ValueError(key + ' must not be empty')
    if 'api_url' in config:
        url = urlsplit(config['api_url'])
        if url.scheme not in ('http', 'https') or not url.netloc:
            raise ValueError('api_url must be an HTTP(S) URL')
    if 'timeout' in config and (type(config['timeout']) is not int or not 1 <= config['timeout'] <= 600):
        raise ValueError('timeout must be an integer between 1 and 600 seconds')
    # A masked value must never replace the stored credential.
    if config.get('api_key') == '********':
        del config['api_key']
    return config


def components():
    """One inventory request, without log collection or waking the warehouse."""
    items = json.loads(warehouse_resident.kubectl("get", "deploy,sts", "-o", "json"))["items"]
    return {item["metadata"]["name"]: {
        "replicas": item.get("spec", {}).get("replicas", 1),
        "ready_replicas": item.get("status", {}).get("readyReplicas", 0),
    } for item in items}


def lake_input(body):
    """Validate the two supported input forms without changing local-file calls."""
    if not any(key in body for key in ('filename', 'content', 'content_base64')):
        file = body.get('file')
        if not isinstance(file, str) or not file.strip():
            raise ValueError('file must be a non-empty string')
        return file, {}
    if 'file' in body:
        raise ValueError('file and uploaded content are mutually exclusive')
    filename = body.get('filename')
    if not isinstance(filename, str) or not filename.strip():
        raise ValueError('filename must be a non-empty string')
    if ('content' in body) == ('content_base64' in body):
        raise ValueError('provide exactly one of content or content_base64')
    content = body.get('content_base64') if 'content_base64' in body else body.get('content')
    if not isinstance(content, str):
        raise ValueError('content and content_base64 must be strings')
    if 'content_base64' in body:
        content = base64.b64decode(content, validate=True)
    return None, dict(filename=filename, content=content)


def request_origin_allowed(headers):
    """Match the browser authority; TLS proxies must preserve Host."""
    origin, host = headers.get('Origin'), headers.get('Host')
    if not origin or not host:
        return False
    if origin == 'http://' + host:
        return True
    if origin == 'https://' + host:
        try:
            return request_auth.shared_mode()
        except request_auth.AuthenticationError:
            return False
    return False


class WorkbenchHandler(BaseHTTPRequestHandler):
    def end_headers(self):
        # Only the Workbench's own origin may control this local lab.
        origin = self.headers.get("Origin")
        if request_origin_allowed(self.headers):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Shirokuma-Role")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def respond(self, value, status=200):
        data = json.dumps(value, default=str).encode("utf-8")
        self.send_response(status)
        if status == 401:
            self.send_header('WWW-Authenticate', 'Basic realm="Shirokuma", charset="UTF-8"')
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def request_role(self):
        params = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        if params.keys() - {'role', 'table', 'detail', 'snapshot_id'}:
            raise PermissionError('Unknown query parameter')
        roles = self.headers.get_all('X-Shirokuma-Role', []) + params.get('role', [])
        principal = request_auth.authenticate(self.headers)
        self.authenticated_user = principal[0] if principal else None
        self.maximum_role = principal[1] if principal else warehouse_resident.resolve_role(self.headers.get('X-Trino-User', ''))
        if principal:
            self.authenticated_user = principal[0]
            return request_auth.set_role(request_auth.select_role(principal[1], roles))
        try:
            for role in roles:
                if not role.strip():
                    raise ValueError('Invalid role: ' + role)
                warehouse_resident.resolve_role(self.headers.get('X-Trino-User', ''), role)
            return request_auth.set_role(warehouse_resident.resolve_role(
                self.headers.get('X-Trino-User', ''), roles[0] if roles else None))
        except ValueError as exc:
            raise PermissionError(str(exc)) from exc

    def authorize(self, routes, path):
        if path not in routes:
            self.respond({'error': 'route not found'}, 404)
            return None
        role = self.request_role()
        if role not in routes[path]:
            raise PermissionError('Forbidden for role: ' + role)
        return role

    @request_auth.role_scope
    def do_GET(self):
        try:
            url = urlsplit(self.path)
            role = self.authorize(GET_ROUTES, url.path)
            if role is None:
                return
            if url.path == '/api/session':
                self.respond({'username': self.authenticated_user, 'role': role,
                              'maximum_role': self.maximum_role,
                              'authenticated': self.authenticated_user is not None})
                return
            elif url.path in ("/", "/index.html"):
                data = INDEX.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if url.path == "/api/status":
                report = {"status": "healthy", "warehouse": None, "components": {}}
                report['auto_chill'] = warehouse_resident.auto_chill_status()
                try:
                    report["warehouse"] = warehouse_resident.status("LOCAL")
                except Exception as exc:
                    report.update(status="degraded", warehouse={"error": str(exc)})
                try:
                    report["components"] = components()
                    if any(v["ready_replicas"] < v["replicas"] for v in report["components"].values()):
                        report["status"] = "degraded"
                except Exception as exc:
                    report.update(status="degraded", components={"error": str(exc)})
                self.respond(report)
            elif url.path in ("/api/catalog", "/api/lineage"):
                params = parse_qs(url.query, keep_blank_values=True)
                table = params.get('table', [None])[0]
                bears_resident.check_catalog_access(role, table)
                options = {key: params[key][0] for key in ('detail', 'snapshot_id') if key in params}
                if 'snapshot_id' in options and not table:
                    raise ValueError('table is required for snapshot_id')
                action = "lineage" if url.path.endswith("lineage") else "describe" if table else "search"
                result = bears_resident.catalog(SimpleNamespace(
                    bear="catalog", action=action, table=table, role=role, include_config=False, **options))
                # Iceberg IDs exceed JavaScript's safe integer range. Preserve
                # their exact digits for snapshot display and rollback requests.
                snapshots = result.get('metadata', {}).get('snapshots')
                if isinstance(snapshots, dict) and snapshots.get('rows'):
                    snapshots['rows'] = [
                        [str(value) if i < 2 and value is not None else value for i, value in enumerate(row)]
                        for row in snapshots['rows']]
                profile = result.get('metadata', {}).get('profile')
                if isinstance(profile, dict) and type(profile.get('snapshot_id')) is int:
                    profile['snapshot_id'] = str(profile['snapshot_id'])
                self.respond(result)
            elif url.path == '/api/ai/config':
                self.respond(masked_ai_config(bears_resident.load_ai_config()))
            elif url.path == '/api/history':
                self.respond(bears_resident.activity_history())
            elif url.path == '/api/models':
                self.respond(bears_resident.models_inventory())
            elif url.path == '/api/jobs':
                self.respond({'jobs': jobs_resident.list_jobs(self.authenticated_user or 'local', role)})
            elif url.path == '/api/marts':
                self.respond({'marts': marts_resident.list_targets(self.authenticated_user or 'local', role)})
            elif url.path == '/api/stages':
                self.respond({'stages': stages_resident.list_stages(self.authenticated_user or 'local', role)})
            elif url.path == '/api/jobs/runs':
                self.respond({'runs': jobs_resident.list_runs(self.authenticated_user or 'local', role)})
            elif url.path == '/api/jobs/schedules':
                self.respond({'schedules': jobs_resident.list_schedules(self.authenticated_user or 'local', role)})
            elif url.path == '/api/alerts':
                self.respond(alerts_resident.list_alerts(self.authenticated_user or 'local', role))
            elif url.path == "/api/pawprints":
                result = manage.Client().query(
                    "SELECT * FROM agenthouse.pawprints ORDER BY timestamp DESC LIMIT 100 FORMAT JSON")
                self.respond(json.loads(result)["data"])
            else:
                self.respond({"error": "route not found"}, 404)
        except request_auth.AuthenticationError as exc:
            self.respond({'error': str(exc)}, 401)
        except PermissionError as exc:
            self.respond({'error': str(exc)}, 403)
        except jobs_resident.JobNotFound as exc:
            self.respond({'error': str(exc)}, 404)
        except jobs_resident.JobConflict as exc:
            self.respond({'error': str(exc)}, 409)
        except (ValueError, UnicodeError) as exc:
            self.respond({'error': str(exc)}, 400)
        except Exception as exc:
            self.respond({"error": str(exc)}, 502)

    def body(self):
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Content-Type must be application/json")
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 10 * 1024 * 1024:
            raise ValueError("JSON body must be between 1 byte and 10 MiB")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    @request_auth.role_scope
    def do_POST(self):
        origin = self.headers.get("Origin")
        if origin and not request_origin_allowed(self.headers):
            self.respond({"error": "cross-origin control requests are not allowed"}, 403)
            return
        path = urlsplit(self.path).path
        try:
            role = self.authorize(POST_ROUTES, path)
            if role is None:
                return
            if path.endswith(("/chill", "/wake")):
                result = warehouse_resident.transition(path.rsplit("/", 1)[1], "LOCAL")
            else:
                body = self.body()
                if path == '/api/marts/save':
                    fields = {'name', 'sql'}
                    if not fields <= set(body) or set(body) - fields - {'min_rows', 'not_null', 'version', 'watch_tables'}:
                        raise ValueError('invalid mart definition fields')
                    result = {'mart': marts_resident.save_target(body, self.authenticated_user or 'local', role)}
                elif path == '/api/marts/schedule':
                    fields = {'name', 'enabled'}
                    if (not fields <= set(body) or set(body) - fields - schedule_resident.SCHEDULE_FIELDS - {'version'}
                            or not isinstance(body.get('name'), str) or not body['name']
                            or type(body.get('enabled')) is not bool
                            or (body['enabled'] and (type(body.get('version')) is not int or body['version'] < 1))
                            or ('version' in body and (type(body['version']) is not int or body['version'] < 1))):
                        raise ValueError('invalid mart schedule fields')
                    if set(body) != fields or body['enabled']:
                        schedule_resident._validate_schedule({'kind': 'interval', **body}, schedule_resident._utc())
                    result = {'schedule': marts_resident.save_schedule(body, self.authenticated_user or 'local', role)}
                elif path == '/api/marts/run':
                    if (set(body) != {'name', 'version'} or not isinstance(body['name'], str) or not body['name']
                            or type(body['version']) is not int or body['version'] < 1):
                        raise ValueError('invalid mart refresh fields')
                    self.respond({'run': marts_resident.submit_refresh(body['name'], body['version'], self.authenticated_user or 'local', role)}, 202)
                    return
                elif path == '/api/stages/save':
                    if not {'name', 'directory', 'table', 'mode', 'format'} <= set(body) or set(body) - {'name', 'directory', 'table', 'mode', 'format', 'key'}:
                        raise ValueError('invalid stage definition fields')
                    result = {'stage': stages_resident.save_stage(body, self.authenticated_user or 'local', role)}
                elif path == '/api/stages/schedule':
                    fields = {'name', 'enabled'}
                    if (not fields <= set(body) or set(body) - fields - schedule_resident.SCHEDULE_FIELDS - {'version'}
                            or not isinstance(body.get('name'), str) or not body['name']
                            or type(body.get('enabled')) is not bool
                            or (body['enabled'] and (not isinstance(body.get('version'), str) or not body['version']))
                            or ('version' in body and (not isinstance(body['version'], str) or not body['version']))):
                        raise ValueError('invalid stage schedule fields')
                    if set(body) != fields or body['enabled']:
                        schedule_resident._validate_schedule({'kind': 'interval', **body}, schedule_resident._utc())
                    result = {'schedule': stages_resident.save_schedule(body, self.authenticated_user or 'local', role)}
                elif path == '/api/stages/scan':
                    if set(body) != {'name', 'version'} or any(not isinstance(body[key], str) or not body[key] for key in ('name', 'version')):
                        raise ValueError('invalid stage scan fields')
                    self.respond({'run': stages_resident.submit_scan(body['name'], self.authenticated_user or 'local', role, body['version'])}, 202)
                    return
                elif path in {'/api/stages/export', '/api/stages/exports'}:
                    fields = {'name'} if path.endswith('/exports') else {'name', 'table', 'format', 'file', 'version'}
                    if set(body) != fields or any(not isinstance(body[key], str) or not body[key].strip() for key in fields):
                        raise ValueError('invalid stage export fields')
                    actor = self.authenticated_user or 'local'
                    if path.endswith('/exports'):
                        result = stage_exports.list_exports(body['name'], actor, role)
                    else:
                        run = stage_exports.submit_export(body['name'], body['table'], body['format'], body['file'], body['version'], actor, role)
                        self.respond({'run': run}, 202)
                        return
                elif path in {'/api/stages/files', '/api/stages/preview', '/api/stages/import'}:
                    fields = {'name'} if path.endswith('/files') else {'name', 'file', 'version'}
                    optional = {'retry'} if path.endswith('/import') else set()
                    if (not fields <= set(body) or set(body) - fields - optional
                            or any(not isinstance(body[key], str) or not body[key] for key in fields)
                            or ('retry' in body and type(body['retry']) is not bool)):
                        raise ValueError('invalid stage request fields')
                    actor = self.authenticated_user or 'local'
                    if path.endswith('/files'):
                        result = stages_resident.list_files(body['name'], actor, role)
                    elif path.endswith('/preview'):
                        result = stages_resident.preview_file(body['name'], body['file'], body['version'], actor, role)
                    else:
                        retry = {'retry': body['retry']} if 'retry' in body else {}
                        result = {'run': stages_resident.submit_import(body['name'], body['file'], body['version'], actor, role, **retry)}
                        self.respond(result, 202)
                        return
                elif path == '/api/jobs/save':
                    result = {'job': jobs_resident.save_job(body, self.authenticated_user or 'local', role)}
                elif path == '/api/jobs/schedule':
                    result = {'schedule': jobs_resident.save_schedule(body, self.authenticated_user or 'local', role)}
                elif path == '/api/jobs/run':
                    if set(body) - {'name', 'arguments'} or not isinstance(body.get('name'), str):
                        raise ValueError('name and arguments are required job inputs')
                    result = {'run': jobs_resident.submit_run(body['name'], body.get('arguments', {}),
                                                            self.authenticated_user or 'local', role)}
                    self.respond(result, 202)
                    return
                elif path == '/api/jobs/detail':
                    if set(body) != {'run_id'} or not isinstance(body['run_id'], str):
                        raise ValueError('run_id must be a string')
                    result = {'run': jobs_resident.get_run(body['run_id'], self.authenticated_user or 'local', role)}
                elif path == '/api/alerts/ack':
                    if set(body) != {'alert_id'} or not isinstance(body['alert_id'], str) or not body['alert_id']:
                        raise ValueError('alert_id must be a non-empty string')
                    result = {'alert': alerts_resident.acknowledge(body['alert_id'], self.authenticated_user or 'local', role)}
                elif path == '/api/ai/config':
                    result = masked_ai_config(bears_resident.save_ai_config(ai_config_input(body)))
                elif path == '/api/ai/test':
                    result = bears_resident.test_ai_connection(ai_config_input(body))
                elif path == "/api/warehouse/query":
                    statement, size, limit = body.get("sql"), body.get("warehouse", "LOCAL"), body.get("max_rows", 1000)
                    if not isinstance(statement, str) or not statement.strip():
                        raise ValueError("sql must be a non-empty string")
                    if not isinstance(size, str) or type(limit) is not int or limit < 1:
                        raise ValueError("warehouse must be a string and max_rows a positive integer")
                    size = warehouse_resident.warehouse(size)
                    schema = body.get('schema', 'marts')
                    if not isinstance(schema, str) or schema not in {'marts', 'raw', 'sandbox', 'staging', 'tpch_tiny', 'smoke'}:
                        raise ValueError('invalid query schema')
                    options = {}
                    if 'mode' in body:
                        mode = body['mode']
                        if not isinstance(mode, str) or mode not in {'read-only', 'sandbox-write', 'admin-write'}:
                            raise ValueError('invalid query mode')
                        options['mode'] = mode
                    if role == 'bi_viewer':
                        options['mode'] = 'read-only'
                        if schema != 'marts':
                            raise PermissionError('BI Viewer queries require marts schema')
                    if role != 'admin':
                        try:
                            statement = warehouse_resident.check_sql_policy(
                                statement, mode=options.get('mode', 'read-only'), role=role)
                        except ValueError as exc:
                            raise PermissionError(str(exc)) from exc
                    result = warehouse_resident.query(size, statement, catalog="iceberg", schema=schema, max_rows=limit, **options)
                elif path == '/api/warehouse/autochill':
                    if 'minutes' in body and role != 'admin':
                        raise PermissionError('Only admin can configure auto-chill')
                    if 'minutes' not in body and 'check' not in body:
                        raise ValueError('minutes or check is required')
                    if 'minutes' in body and (type(body['minutes']) is not int or not 0 <= body['minutes'] <= 1440):
                        raise ValueError('minutes must be an integer between 0 and 1440')
                    if 'check' in body and type(body['check']) is not bool:
                        raise ValueError('check must be a boolean')
                    if 'minutes' in body:
                        result = warehouse_resident.configure_auto_chill(body['minutes'])
                    else:
                        result = warehouse_resident.auto_chill_status()
                    if body.get('check') is True:
                        result = warehouse_resident.check_auto_chill('LOCAL')
                elif path == '/api/catalog/clone':
                    source = body.get('source_table')
                    if not isinstance(source, str) or not source.strip():
                        raise ValueError('source_table must be a non-empty string')
                    for key in ('target_table', 'snapshot_id'):
                        if key in body and body[key] is not None and not isinstance(body[key], str):
                            raise ValueError(key + ' must be a string')
                    result = bears_resident.clone_table(source, target_table=body.get('target_table'),
                                                        snapshot_id=body.get('snapshot_id'))
                    audit = result.get('audit')
                    if isinstance(audit, dict) and type(audit.get('snapshot_id')) is int:
                        audit['snapshot_id'] = str(audit['snapshot_id'])
                elif path == '/api/lake/export':
                    table, fmt = body.get('table'), body.get('format', 'auto')
                    file, limit = body.get('file'), body.get('max_rows')
                    if not isinstance(table, str) or not table.strip():
                        raise ValueError('table must be a non-empty string')
                    if not isinstance(fmt, str) or fmt not in {'auto', 'csv', 'jsonl', 'parquet'}:
                        raise ValueError('invalid export format')
                    if file is not None and (not isinstance(file, str) or not file.strip()):
                        raise ValueError('file must be a non-empty string')
                    if limit is not None and (type(limit) is not int or limit < 1):
                        raise ValueError('max_rows must be a positive integer')
                    result = lake_ingest.export_table(table, file=file, format=fmt, max_rows=limit)
                elif path == '/api/catalog/drop':
                    table = body.get('table')
                    if not isinstance(table, str) or not table.strip():
                        raise ValueError('table must be a non-empty string')
                    if role == 'data_engineer':
                        if not re.fullmatch(r'(?:iceberg\.)?(?:sandbox|staging|raw)\.[A-Za-z_][A-Za-z_0-9]*', table):
                            raise PermissionError('Data Engineer can drop only sandbox, staging, or raw tables')
                    result = bears_resident.drop_table(table)
                elif path == '/api/models/build':
                    action = body.get('action', 'build')
                    if not isinstance(action, str) or action not in {'build', 'test', 'status'}:
                        raise ValueError('invalid model action')
                    import lake_pipeline
                    result = lake_pipeline.run(action)
                elif path in ('/api/lake/ingest', '/api/lake/preview'):
                    file, options = lake_input(body)
                    table, mode, fmt = body.get('table'), body.get('mode', 'replace'), body.get('format', 'auto')
                    if not isinstance(fmt, str) or fmt not in {'auto', 'csv', 'jsonl', 'parquet'}:
                        raise ValueError('invalid file format')
                    if path == '/api/lake/preview':
                        limit = body.get('limit', 10)
                        if type(limit) is not int or limit < 1:
                            raise ValueError('limit must be a positive integer')
                        self.respond(lake_ingest.preview(file, format=fmt, limit=limit, **options))
                        return
                    batch_size = body.get('batch_size', 500)
                    if ('table' in body and not isinstance(table, str)) or not all(isinstance(v, str) for v in (mode, fmt)):
                        raise ValueError('table, mode, and format must be strings')
                    if type(batch_size) is not int or batch_size < 1:
                        raise ValueError('batch_size must be a positive integer')
                    if 'key' in body:
                        if not isinstance(body['key'], str):
                            raise ValueError('key must be a string')
                        options['key'] = body['key']
                    result = lake_ingest.ingest(file, table=table, mode=mode, format=fmt, batch_size=batch_size, **options)
                else:
                    bear = body.get("bear")
                    actions = {"sre": ("inspect",), "query": ("explain",), "data": ("generate", "stage", "publish", "rollback", "refresh"),
                               "catalog": ("search", "describe", "lineage"), "policy": ("baseline", "check")}
                    if not isinstance(bear, str) or bear not in actions:
                        raise ValueError("unknown bear")
                    action = body.get("action", actions[bear][0])
                    if not isinstance(action, str) or action not in actions[bear]:
                        raise ValueError("invalid bear action")
                    if bear == 'data' and ((role == 'bi_viewer' and action != 'generate') or
                            (role == 'data_engineer' and action in {'publish', 'refresh', 'rollback'})):
                        raise PermissionError('Forbidden Bear action for role: ' + role)
                    if bear == 'data' and role != 'admin' and any(body.get(key) for key in ('publish', 'refresh', 'rollback')):
                        raise PermissionError('Forbidden Bear write for role: ' + role)
                    if bear == 'data' and role == 'bi_viewer' and body.get('stage_table'):
                        raise PermissionError('BI Viewer cannot stage data')
                    fields = {key: body.get(key) for key in ("sql", "request", "manifest", "target", "table")}
                    if any(value is not None and not isinstance(value, str) for value in fields.values()):
                        raise ValueError("bear inputs must be strings")
                    for key in ('stage_table', 'publish', 'rollback', 'snapshot_id', 'refresh'):
                        if key in body:
                            if not isinstance(body[key], str):
                                raise ValueError('bear inputs must be strings')
                            fields[key] = body[key]
                    if bear == "catalog":
                        fields["table"] = fields["table"] or fields["target"]
                        bears_resident.check_catalog_access(role, fields['table'])
                        fields['role'] = role
                        fields['include_config'] = False
                    if bear == 'data':
                        if action == 'stage':
                            fields['stage_table'] = fields.get('stage_table') or fields['table']
                        elif action == 'publish':
                            fields['publish'] = fields.get('publish') or fields['table'] or fields.get('stage_table')
                        elif action == 'rollback':
                            fields['rollback'] = fields.get('rollback') or fields['table'] or fields['target']
                        elif action == 'refresh':
                            fields['refresh'] = fields.get('refresh') or fields['table'] or fields.get('stage_table')
                        if not any((fields.get(key) or '').strip() for key in ('request', 'sql', 'publish', 'rollback', 'refresh')):
                            raise ValueError('data bear requires a request, sql, publish, rollback, or refresh')
                    fields["target"] = fields["target"] or "latest"
                    result = bears_resident.run(SimpleNamespace(bear=bear, action=action, **fields))
            self.respond(result)
        except request_auth.AuthenticationError as exc:
            self.respond({'error': str(exc)}, 401)
        except PermissionError as exc:
            self.respond({'error': str(exc)}, 403)
        except jobs_resident.JobNotFound as exc:
            self.respond({'error': str(exc)}, 404)
        except jobs_resident.JobConflict as exc:
            self.respond({'error': str(exc)}, 409)
        except (ValueError, UnicodeError) as exc:
            self.respond({"error": str(exc)}, 400)
        except Exception as exc:
            self.respond({"error": str(exc)}, 502)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8888)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)
    request_auth.validate_bind(args.host)
    with HTTPServer((args.host, args.port), WorkbenchHandler) as server:
        warehouse_resident.initialize_auto_chill()
        print(f"Shirokuma Workbench: http://{args.host}:{server.server_port}", flush=True)
        try:
            with contextlib.ExitStack() as stack:
                try:
                    stack.enter_context(warehouse_resident.persistent_trino())
                except (OSError, RuntimeError) as exc:
                    print(f'Trino tunnel unavailable; queries will reconnect: {exc}', flush=True)
                stack.callback(jobs_resident.stop_scheduler)
                jobs_resident.start_scheduler()
                server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            warehouse_resident.stop_auto_chill_daemon()


if __name__ == "__main__":
    main()
