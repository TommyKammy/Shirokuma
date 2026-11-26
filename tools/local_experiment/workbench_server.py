#!/usr/bin/env python3
"""Serve the local Shirokuma Workbench with standard-library HTTP tooling."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer as HTTPServer
import database_http_json
from pathlib import Path
import re
import signal
import sys
from urllib.parse import parse_qs, urlsplit

import db_schedules
import warehouse_resident
import request_auth
from http_origin import request_origin_allowed

INDEX = Path(__file__).parent / "workbench" / "index.html"
RESULT_CHART = INDEX.with_name("result_chart.js")
STATIC_ASSETS = {
    '/assets/result_chart.js': (RESULT_CHART, 'application/javascript; charset=utf-8'),
    '/assets/base_i18n.js': (INDEX.with_name('base_i18n.js'), 'application/javascript; charset=utf-8'),
    '/assets/saved_lineage.js': (INDEX.with_name('saved_lineage.js'), 'application/javascript; charset=utf-8'),
    '/assets/result_export.js': (INDEX.with_name('result_export.js'), 'application/javascript; charset=utf-8'),
    '/assets/db_admin.js': (INDEX.with_name('db_admin.js'), 'application/javascript; charset=utf-8'),
    '/assets/db_dataops.js': (INDEX.with_name('db_dataops.js'), 'application/javascript; charset=utf-8'),
    '/assets/db_explorer.js': (INDEX.with_name('db_explorer.js'), 'application/javascript; charset=utf-8'),
    '/assets/db_jobs.js': (INDEX.with_name('db_jobs.js'), 'application/javascript; charset=utf-8'),
    '/assets/db_stages.js': (INDEX.with_name('db_stages.js'), 'application/javascript; charset=utf-8'),
    '/assets/db_marts.js': (INDEX.with_name('db_marts.js'), 'application/javascript; charset=utf-8'),
    '/assets/db_dbt.js': (INDEX.with_name('db_dbt.js'), 'application/javascript; charset=utf-8'),
    '/assets/db_creation.js': (INDEX.with_name('db_creation.js'), 'application/javascript; charset=utf-8'),
    '/assets/db_audit.js': (INDEX.with_name('db_audit.js'), 'application/javascript; charset=utf-8'),
    '/assets/db_runtime.js': (INDEX.with_name('db_runtime.js'), 'application/javascript; charset=utf-8'),
    '/assets/workbench.css': (INDEX.with_name('workbench.css'), 'text/css; charset=utf-8'),
}


class WorkbenchHandler(BaseHTTPRequestHandler):
    def database_request(self, method):
        """Database mode has its own strict routing and identity boundary."""
        import re
        import uuid
        import database_inventory as inventory
        import query_context
        import query_service
        error_id = str(uuid.uuid4())
        try:
            principal = request_auth.authenticate_principal(self.headers)
            if method == 'POST' and self.headers.get('Origin') and not request_origin_allowed(self.headers):
                raise inventory.InventoryError('INVALID_ORIGIN', 403, 'Cross-origin control requests are not allowed')
            if method == 'GET' and (self.headers.get('Transfer-Encoding') or self.headers.get('Content-Length', '0') != '0'):
                raise ValueError('GET routes do not accept request bodies')
            url = urlsplit(self.path)
            params = parse_qs(url.query, keep_blank_values=True)
            def parameters(allowed, required=()):
                if set(params) - set(allowed) or any(len(v) != 1 for v in params.values()) or not set(required) <= set(params):
                    raise ValueError('Invalid route parameters')
                return {k: v[0] for k, v in params.items()}
            headers = self.headers.get_all('X-Shirokuma-Role', [])
            if url.path == '/api/session' and method == 'GET':
                parameters(())
                if headers and (len(headers) != 1 or headers[0] not in request_auth.ROLES):
                    raise ValueError('One valid explicit role is required')
                role = headers[0] if headers else principal.maximum_role
                self.respond(dict(username=principal.principal_id, role=role,
                    maximum_role=principal.maximum_role, authenticated=True,
                    data_mode='database', platform_admin=principal.platform_admin))
                return
            if method == 'GET' and url.path in ('/', '/index.html', *STATIC_ASSETS):
                parameters(())
                asset, content_type = STATIC_ASSETS.get(url.path, (INDEX, 'text/html; charset=utf-8'))
                data = asset.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if method == 'GET' and url.path == '/api/status':
                parameters(())
                import runtime_profile
                profile = runtime_profile.current()
                if profile is not None and profile.mode == 'logical_v1':
                    report = dict(status='healthy', data_mode='database', creation_mode='logical_v1')
                    try:
                        report['warehouse'] = warehouse_resident.status('LOCAL')
                    except Exception:
                        report.update(status='degraded', warehouse=dict(state='unavailable'))
                    self.respond(report)
                    return
                self.respond(dict(status='healthy', data_mode='database'))
                return
            if not url.path.startswith('/api/v1/'):
                raise inventory.InventoryError('FEATURE_UNAVAILABLE', 404, 'Feature unavailable in database mode')
            if len(headers) != 1 or headers[0] not in request_auth.ROLES:
                raise ValueError('One valid explicit role is required')
            role = headers[0]
            path = url.path
            import query_result_exports
            if query_result_exports.dispatch(self, method, url, principal, role):
                return
            import ai_routes
            if ai_routes.dispatch(self, method, path, params, principal, role):
                return
            import audit_routes
            if audit_routes.dispatch(self, method, path, params, principal, role):
                return
            import db_schedule_routes
            if db_schedule_routes.dispatch(self, method, path, params, principal, role):
                return
            import job_routes
            if job_routes.dispatch(self, method, path, params, principal, role):
                return
            import dataops_routes
            if dataops_routes.dispatch(self, method, path, params, principal, role):
                return
            import db_stage_routes
            if db_stage_routes.dispatch(self, method, path, params, principal, role):
                return
            import dbt_routes
            if dbt_routes.dispatch(self, method, path, params, principal, role):
                return
            import db_mart_routes
            if db_mart_routes.dispatch(self, method, path, params, principal, role):
                return
            import db_inventory_routes
            if db_inventory_routes.dispatch(self, method, path, params, principal, role):
                return
            result = None
            if method == 'GET' and re.fullmatch(r'/api/v1/schemas/[^/]+/objects', path):
                p = parameters(('kind', 'q', 'limit', 'cursor'))
                if 'limit' in p:
                    if not re.fullmatch(r'[0-9]+', p['limit']):
                        raise ValueError('limit must be an integer')
                    p['limit'] = int(p['limit'])
                result = inventory.list_objects(principal, role, path.split('/')[4], **p)
            elif method == 'GET' and re.fullmatch(r'/api/v1/objects/[^/]+', path):
                p = parameters(('detail', 'snapshot_id'), ('detail',))
                result = inventory.object_detail(principal, role, path.split('/')[4], **p)
            elif path == '/api/v1/queries' and method == 'GET':
                p = parameters(('database_id',), ('database_id',))
                if not p['database_id']:
                    raise ValueError('database_id is required')
                result = query_service.history(principal, database_id=p['database_id'], selected_role=role)
            elif path == '/api/v1/queries' and method == 'POST':
                parameters(())
                body = self.database_body()
                fields = {'sql', 'request_id', 'worksheet_id', 'context', 'mode', 'max_rows'}
                full_results = set(body) == fields | {'result_mode'} and body['result_mode'] == 'all'
                if set(body) != fields and not full_results:
                    raise ValueError('Invalid query fields')
                for name in ('sql', 'request_id', 'worksheet_id'):
                    if not isinstance(body[name], str) or not body[name].strip():
                        raise ValueError('Query text and identifiers are required')
                if ((full_results and (body['max_rows'] is not None or body['mode'] != 'read-only')) or
                        (not full_results and (body['mode'] not in ('read-only', 'sandbox-write', 'admin-write') or
                         type(body['max_rows']) is not int or not 1 <= body['max_rows'] <= 10000))):
                    raise ValueError('Invalid query mode or row limit')
                c = body['context']
                if not isinstance(c, dict) or set(c) != {'database_id', 'schema_id', 'warehouse_id', 'binding_revision', 'generation_id'}:
                    raise ValueError('Invalid query context fields')
                if (any(not isinstance(c[k], str) or not c[k].strip() for k in ('database_id', 'schema_id', 'generation_id')) or
                        c['warehouse_id'] != 'LOCAL' or type(c['binding_revision']) is not int or c['binding_revision'] < 1):
                    raise ValueError('Invalid query context values')
                context = query_context.resolve_context(principal, c['database_id'], c['schema_id'], role,
                    warehouse_id=c['warehouse_id'], expected_binding_revision=c['binding_revision'])
                if context.generation_id != c['generation_id']:
                    raise inventory.InventoryError('STALE_CONTEXT', 409, 'Database generation changed')
                result = query_service.submit(principal, context, body['sql'], mode=body['mode'],
                    max_rows=body['max_rows'], request_id=body['request_id'], worksheet_id=body['worksheet_id'],
                    **({'result_mode': 'all'} if full_results else {}))
            elif method == 'GET' and re.fullmatch(r'/api/v1/queries/[^/]+', path):
                parameters(())
                result = query_service.inspect_query(principal, path.split('/')[4], selected_role=role)
            elif method == 'GET' and re.fullmatch(r'/api/v1/queries/[^/]+/pages/[^/]+', path):
                parameters(())
                result = query_service.fetch(principal, path.split('/')[4], path.split('/')[6], selected_role=role)
            elif method == 'POST' and re.fullmatch(r'/api/v1/queries/[^/]+/cancel', path):
                parameters(())
                if self.database_body() != {}:
                    raise ValueError('Cancel body must be empty')
                # Core owns cancellation authorization, including revoked/downgraded owners.
                result = query_service.cancel(principal, path.split('/')[4], selected_role=role)
            else:
                raise inventory.InventoryError('NOT_FOUND', 404, 'Route not found')
            self.respond(result)
        except Exception as exc:
            if isinstance(exc, request_auth.AuthenticationError):
                status, code, message, details = 401, 'AUTHENTICATION_REQUIRED', 'Valid Basic credentials are required', {}
            elif isinstance(exc, (inventory.InventoryError, query_service.QueryError)):
                status, code, message, details = exc.status, exc.code, str(exc), exc.details
            elif isinstance(exc, PermissionError):
                status, code, message, details = 404, 'NOT_FOUND', 'Resource not found', {}
            elif isinstance(exc, (ValueError, TypeError, UnicodeError)):
                status, code, message, details = 400, 'INVALID_INPUT', 'Invalid request parameters or body', {}
            else:
                status, code, message, details = 502, 'SERVICE_UNAVAILABLE', 'Database service unavailable', {}
            self.respond(dict(code=code, message=message, request_id=error_id, details=details, retryable=status >= 500), status)

    def management_request(self, method):
        """Public control routes only hand operations to the explicit private operator."""
        path = urlsplit(self.path).path
        if path == '/api/v1/audit' or path.startswith('/api/v1/audit/'):
            self.database_request(method)
            return True
        import warehouse_runtime_routes
        if warehouse_runtime_routes.dispatch(self, method):
            return True
        import database_creations
        if database_creations.dispatch(self, method):
            return True
        import database_retirement_routes
        if database_retirement_routes.dispatch(self, method):
            return True
        import re
        import uuid
        path = urlsplit(self.path).path
        if not (path.startswith('/api/v1/warehouses') or path.startswith('/api/v1/operations')
                or path == '/api/v1/admin/databases'
                or re.fullmatch(r'/api/v1/(databases|schemas)/[^/]+/display', path)
                or re.fullmatch(r'/api/v1/databases/[^/]+/grants', path)
                or path == '/api/v1/runtime' or method == 'POST' and path == '/api/v1/databases'):
            return False
        import catalog_admin
        from warehouse_operations import OperationError
        status, result = 200, None
        try:
            principal = request_auth.authenticate_principal(self.headers)
            if '?' in self.path or '#' in self.path or self.headers.get('Transfer-Encoding'):
                raise ValueError('Invalid management route')
            if method in ('POST', 'PATCH') and self.headers.get('Origin') and not request_origin_allowed(self.headers):
                raise PermissionError('Invalid origin')
            roles = self.headers.get_all('X-Shirokuma-Role', [])
            if (len(roles) != 1 or roles[0] not in request_auth.ROLES or
                    request_auth.ROLES[roles[0]] > request_auth.ROLES[principal.maximum_role]):
                raise ValueError('Explicit authorized role required')
            role = roles[0]
            match = re.fullmatch(r'/api/v1/operations/([A-Za-z0-9_-]{1,128})(?:/(apply|reconcile))?', path)
            grant_match = re.fullmatch(r'/api/v1/databases/([A-Za-z0-9_-]{1,128})/grants', path)
            display_match = re.fullmatch(r'/api/v1/(databases|schemas)/([A-Za-z0-9_-]{1,128})/display', path)
            if method == 'GET' and path == '/api/v1/admin/databases':
                import display_management
                catalog_admin.require_admin(principal, role)
                if self.headers.get_all('Content-Length', []) not in ([], ['0']):
                    raise ValueError('GET does not accept a body')
                result = display_management.list_databases(principal, role)
            elif method == 'PATCH' and display_match:
                import display_management
                catalog_admin.require_admin(principal, role)
                keys = self.headers.get_all('Idempotency-Key', [])
                if len(keys) != 1:
                    raise ValueError('One idempotency key required')
                result = display_management.mutate(principal, role,
                    'database' if display_match[1] == 'databases' else 'schema',
                    display_match[2], self.database_body(), keys[0])
            elif grant_match and method in ('GET', 'POST'):
                import grant_management
                catalog_admin.require_admin(principal, role)
                if method == 'GET':
                    if self.headers.get_all('Content-Length', []) not in ([], ['0']):
                        raise ValueError('GET does not accept a body')
                    result = grant_management.list_grants(principal, role, grant_match[1])
                else:
                    keys = self.headers.get_all('Idempotency-Key', [])
                    if len(keys) != 1:
                        raise ValueError('One idempotency key required')
                    result = grant_management.mutate(principal, role, grant_match[1], self.database_body(), keys[0])
                    status = 202
            elif method == 'GET' and path == '/api/v1/runtime':
                import runtime_profile
                catalog_admin.require_admin(principal, role)
                if self.headers.get_all('Content-Length', []) not in ([], ['0']):
                    raise ValueError('GET does not accept a body')
                result = runtime_profile.generation_attestation()
            elif method == 'POST' and path == catalog_admin.CREATE_ROUTE:
                keys = self.headers.get_all('Idempotency-Key', [])
                if len(keys) != 1:
                    raise ValueError('One idempotency key required')
                result = catalog_admin.create_database(principal, role, self.database_body(), keys[0])
                status = 202
            elif method == 'POST' and path == catalog_admin.PLAN_ROUTE:
                catalog_admin.require_admin(principal, role)
                keys = self.headers.get_all('Idempotency-Key', [])
                if len(keys) != 1:
                    raise ValueError('One idempotency key required')
                body = self.database_body()
                if set(body) not in ({'generation_id'}, {'generation_id', 'phase', 'predecessor_id'}):
                    raise ValueError('Invalid public plan fields')
                result = catalog_admin.public_plan(principal, role, body['generation_id'], keys[0],
                    phase=body.get('phase', 'full'), predecessor_id=body.get('predecessor_id'))
                status = 201
            elif method == 'GET' and match and not match[2]:
                lengths = self.headers.get_all('Content-Length', [])
                if lengths not in ([], ['0']):
                    raise ValueError('GET does not accept a body')
                result = catalog_admin.inspect_operation(principal, role, match[1])
            elif method == 'POST' and match and match[2]:
                catalog_admin.require_admin(principal, role)
                keys = self.headers.get_all('Idempotency-Key', [])
                if len(keys) != 1 or not keys[0].strip():
                    raise ValueError('One idempotency key required')
                body = self.database_body()
                fields = {'expected_revision', 'delivery_digest'} if match[2] == 'apply' else {'expected_revision'}
                if (set(body) != fields or type(body.get('expected_revision')) is not int or
                        body['expected_revision'] < 1 or 'delivery_digest' in fields and
                        (not isinstance(body['delivery_digest'], str) or not body['delivery_digest'].strip())):
                    raise ValueError('Invalid operator handoff payload')
                result = catalog_admin.request_action(principal, role, match[1], path, keys[0], body)
                status = 202
            else:
                status = 404
                result = dict(code='NOT_FOUND', message='Route not found', retryable=False)
        except Exception as error:
            if isinstance(error, request_auth.AuthenticationError):
                status, code = 401, 'AUTHENTICATION_REQUIRED'
            elif isinstance(error, OperationError):
                status, code = error.status, error.code
            elif isinstance(error, PermissionError):
                status, code = 403, 'FORBIDDEN'
            elif isinstance(error, (ValueError, TypeError, UnicodeError)):
                status, code = 400, 'INVALID_INPUT'
            else:
                status, code = 409, 'OPERATION_UNAVAILABLE'
            result = dict(code=code, message='Management request rejected', retryable=False,
                          request_id=str(uuid.uuid4()), details={})
        self.respond(result, status)
        return True

    def database_body(self):
        return database_http_json.database_body(self)

    def end_headers(self):
        # Only the Workbench's own origin may control this local lab.
        origin = self.headers.get("Origin")
        if request_origin_allowed(self.headers):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Shirokuma-Role, Idempotency-Key")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def respond(self, value, status=200):
        return database_http_json.respond(self, value, status)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    @request_auth.role_scope
    def do_GET(self):
        if self.management_request('GET'):
            return
        import runtime_profile
        if runtime_profile.active():
            return self.database_request('GET')
        self.respond(dict(code='RUNTIME_PROFILE_REQUIRED',
                          message='Database runtime profile required', retryable=False), 503)

    @request_auth.role_scope
    def do_PATCH(self):
        if not self.management_request('PATCH'):
            self.respond(dict(code='NOT_FOUND', message='Route not found', retryable=False), 404)

    @request_auth.role_scope
    def do_POST(self):
        if self.management_request('POST'):
            return
        import runtime_profile
        if runtime_profile.active():
            return self.database_request('POST')
        self.respond(dict(code='RUNTIME_PROFILE_REQUIRED',
                          message='Database runtime profile required', retryable=False), 503)


def main(argv=None):
    import runtime_profile
    runtime_profile.configure_process()
    import database_creation_bridge
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8888)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)
    if not runtime_profile.active():
        print('Shirokuma Workbench requires a database runtime profile '
              '(SHIROKUMA_RUNTIME_PROFILE or var/shirokuma/runtime-profile.json)', file=sys.stderr)
        raise SystemExit(2)
    request_auth.validate_bind(args.host)
    # SIGTERM unwinds like Ctrl-C so owned port-forwards and atexit cleanup run.
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    with HTTPServer((args.host, args.port), WorkbenchHandler) as server:
        import logical_creations
        if logical_creations.enabled():
            with logical_creations.lifetime() as provider:
                import warehouse_lifecycle
                warehouse_lifecycle.observe_startup(provider)
                warehouse_resident.initialize_auto_chill()
                print(f'Shirokuma Workbench: http://{args.host}:{server.server_port}', flush=True)
                try:
                    db_schedules.start_scheduler()
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
                finally:
                    warehouse_resident.stop_auto_chill_daemon()
                    db_schedules.stop_scheduler()
            return
        database_creation_bridge.configure()
        if runtime_profile.active():
            print(f'Shirokuma Workbench: http://{args.host}:{server.server_port}', flush=True)
            try:
                db_schedules.start_scheduler()
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                db_schedules.stop_scheduler()
            return


if __name__ == "__main__":
    main()
