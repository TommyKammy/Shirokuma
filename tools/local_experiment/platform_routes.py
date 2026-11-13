"""Platform HTTP route adapter shared by both Database surfaces."""
import json
import re

import db_platform_observations
import db_platform_resources
import db_platform_usage
import db_platform_capacity
import db_platform_lifecycle_history
import db_platform_workload_baseline
import db_platform_foundation_baseline
import db_platform_foundation_controllers
import db_platform_pod_baseline
import db_platform_manifest_advisory
import db_platform_controller_baseline
import db_platform_recent_failures
import db_platform_loki_errors
import db_platform_loki_foundation_errors
import db_platform_telemetry_logs
import db_platform_telemetry_readiness
from warehouse_operations import OperationError
from query_context import QueryError


MANIFEST_ADVISORY_PATH = '/api/v1/bears/platform-manifest-advisory'


def _invalid_basic_frame(handler, params):
    return ('?' in handler.path or '#' in handler.path or params
            or handler.headers.get_all('Transfer-Encoding', [])
            or handler.headers.get_all('Content-Length', []) not in ([], ['0']))


def _invalid_header_fields(headers):
    return (headers.defects
            or any(not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key)
                   or any(ord(c) < 32 and c != '\t' or ord(c) == 127 for c in value)
                   for key, value in headers.raw_items()))


def _read_advisory_manifest(handler, params):
    headers = handler.headers
    length = headers.get('Content-Length', '')
    if (handler.path != MANIFEST_ADVISORY_PATH or params or _invalid_header_fields(headers)
            or any(len(headers.get_all(key)) != 1 for key in headers.keys())
            or headers.get_all('Transfer-Encoding')
            or len(headers.get_all('Content-Type', [])) != 1
            or headers.get_content_type() != 'application/json'
            or not re.fullmatch(r'[1-9][0-9]{0,5}', length)
            or int(length) > 128 * 1024):
        raise ValueError()

    # Require a complete UTF-8 frame. The generic reader accepts short reads
    # and auto-detects UTF-16/32, neither of which is part of this endpoint.
    size = int(length)
    raw = handler.rfile.read(size)
    if len(raw) != size:
        raise ValueError()

    def fields(pairs):
        if len(pairs) != 1 or pairs[0][0] != 'manifest':
            raise ValueError()
        return dict(pairs)

    body = json.loads(raw.decode('utf-8'), object_pairs_hook=fields)
    if not isinstance(body, dict) or type(body['manifest']) is not str:
        raise ValueError()
    return body['manifest']


def dispatch(handler, method, path, params, principal, role):
    if path == MANIFEST_ADVISORY_PATH:
        handler.close_connection = True
        if method != 'POST':
            raise QueryError('NOT_FOUND', 404)
        try:
            result = db_platform_manifest_advisory.advise(
                principal, role, lambda: _read_advisory_manifest(handler, params))
        except OperationError as error:
            raise QueryError(error.code, error.status) from None
        except (ValueError, RecursionError):
            raise QueryError('INVALID_INPUT', 400) from None
        handler.respond(result, 200)
        return True
    if path == '/api/v1/bears/platform-runtime-history':
        if method != 'GET':
            raise QueryError('NOT_FOUND', 404)
        raw = handler.path.partition('?')[2]
        if ('#' in handler.path or _invalid_header_fields(handler.headers)
                or any(len(handler.headers.get_all(k, [])) != 1 for k in handler.headers.keys())
                or handler.headers.get_all('Transfer-Encoding', [])
                or handler.headers.get_all('Content-Length', []) not in ([], ['0'])
                or '?' in handler.path and not raw
                or raw and not re.fullmatch(r'(?:limit=[1-9][0-9]?|cursor=[A-Za-z0-9_.-]{1,2048})(?:&(?:limit=[1-9][0-9]?|cursor=[A-Za-z0-9_.-]{1,2048}))*', raw)):
            raise QueryError('INVALID_INPUT', 400)
        parts = [p.split('=', 1) for p in raw.split('&')] if raw else []
        if len({p[0] for p in parts}) != len(parts):
            raise QueryError('INVALID_INPUT', 400)
        values = dict(parts)
        limit = int(values.get('limit', '20'))
        if not 1 <= limit <= 50:
            raise QueryError('INVALID_INPUT', 400)
        try:
            result = db_platform_lifecycle_history.observe(principal, role, limit=limit, cursor=values.get('cursor'))
        except OperationError as error:
            raise QueryError(error.code, error.status) from None
        handler.respond(result, 200)
        return True
    if path == '/api/v1/bears/platform-workload-baseline':
        if method != 'GET':
            raise QueryError('NOT_FOUND', 404)
        if _invalid_basic_frame(handler, params):
            raise QueryError('INVALID_INPUT', 400)
        try:
            result = db_platform_workload_baseline.observe(principal, role)
        except OperationError as error:
            raise QueryError(error.code, error.status) from None
        handler.respond(result, 200)
        return True
    if path == '/api/v1/bears/platform-usage':
        if method != 'GET':
            raise QueryError('NOT_FOUND', 404)
        if ('?' in handler.path or '#' in handler.path or params
                or any(len(handler.headers.get_all(k, [])) != 1 for k in handler.headers.keys())
                or handler.headers.get_all('Transfer-Encoding', [])
                or handler.headers.get_all('Content-Length', []) not in ([], ['0'])):
            raise QueryError('INVALID_INPUT', 400)
        try:
            result = db_platform_usage.observe(principal, role)
        except OperationError as error:
            raise QueryError(error.code, error.status) from None
        handler.respond(result, 200)
        return True
    if path == '/api/v1/bears/platform-foundation-baseline':
        if method != 'GET':
            raise QueryError('NOT_FOUND', 404)
        if ('?' in handler.path or '#' in handler.path or params
                or _invalid_header_fields(handler.headers)
                or any(len(handler.headers.get_all(k, [])) != 1 for k in handler.headers.keys())
                or handler.headers.get_all('Transfer-Encoding', [])
                or handler.headers.get_all('Content-Length', []) not in ([], ['0'])):
            raise QueryError('INVALID_INPUT', 400)
        try:
            result = db_platform_foundation_baseline.observe(principal, role)
        except OperationError as error:
            raise QueryError(error.code, error.status) from None
        handler.respond(result, 200)
        return True
    if path == '/api/v1/bears/platform-foundation-controllers':
        if method != 'GET':
            raise QueryError('NOT_FOUND', 404)
        if ('?' in handler.path or '#' in handler.path or params
                or _invalid_header_fields(handler.headers)
                or any(len(handler.headers.get_all(k, [])) != 1 for k in handler.headers.keys())
                or handler.headers.get_all('Transfer-Encoding', [])
                or handler.headers.get_all('Content-Length', []) not in ([], ['0'])):
            raise QueryError('INVALID_INPUT', 400)
        try:
            result = db_platform_foundation_controllers.observe(principal, role)
        except OperationError as error:
            raise QueryError(error.code, error.status) from None
        handler.respond(result, 200)
        return True
    if path in ('/api/v1/bears/platform-recent-failures', '/api/v1/bears/platform-telemetry-readiness',
                '/api/v1/bears/platform-telemetry-logs', db_platform_telemetry_logs.RAW_LOGS_PATH,
                db_platform_loki_errors.PATH, db_platform_loki_foundation_errors.PATH):
        severity_route = path in (db_platform_loki_errors.PATH, db_platform_loki_foundation_errors.PATH)
        if severity_route:
            handler.close_connection = True
        if method != 'GET' and not (severity_route and method == 'POST'):
            raise QueryError('NOT_FOUND', 404)
        if severity_route or path == db_platform_telemetry_logs.RAW_LOGS_PATH:
            origins = handler.headers.get_all('Origin', [])
            require_origin = severity_route and method == 'POST' and not getattr(handler, '_native_bears', False)
            if origins or require_origin:
                from http_origin import request_origin_allowed
                if len(origins) != 1 or not request_origin_allowed(handler.headers):
                    raise QueryError('INVALID_ORIGIN', 403)
        if (severity_route and handler.path != path
                or '?' in handler.path or '#' in handler.path or params
                or _invalid_header_fields(handler.headers)
                or any(len(handler.headers.get_all(k, [])) != 1 for k in handler.headers.keys())
                or handler.headers.get_all('Transfer-Encoding', [])
                or handler.headers.get_all('Content-Length', []) not in ([], ['0'])):
            raise QueryError('INVALID_INPUT', 400)
        try:
            source = {
                'platform-loki-errors': (db_platform_loki_errors.capture if method == 'POST'
                                         else db_platform_loki_errors.search),
                'platform-loki-foundation-errors': (db_platform_loki_foundation_errors.capture if method == 'POST'
                                                    else db_platform_loki_foundation_errors.search),
                'platform-telemetry-raw-logs': db_platform_telemetry_logs.observe_raw,
                'platform-telemetry-logs': db_platform_telemetry_logs.observe,
                'platform-telemetry-readiness': db_platform_telemetry_readiness.observe,
                'platform-recent-failures': db_platform_recent_failures.observe,
            }[path.rsplit('/', 1)[-1]]
            result = source(principal, role)
        except OperationError as error:
            raise QueryError(error.code, error.status) from None
        handler.respond(result, 200)
        return True
    baseline_source = {
        '/api/v1/bears/platform-pod-baseline': db_platform_pod_baseline,
        '/api/v1/bears/platform-controller-baseline': db_platform_controller_baseline,
    }.get(path)
    if baseline_source is not None:
        if method != 'GET':
            raise QueryError('NOT_FOUND', 404)
        if ('?' in handler.path or '#' in handler.path or params
                or _invalid_header_fields(handler.headers)
                or any(len(handler.headers.get_all(k, [])) != 1 for k in handler.headers.keys())
                or handler.headers.get_all('Transfer-Encoding', [])
                or handler.headers.get_all('Content-Length', []) not in ([], ['0'])):
            raise QueryError('INVALID_INPUT', 400)
        try:
            result = baseline_source.observe(principal, role)
        except OperationError as error:
            raise QueryError(error.code, error.status) from None
        handler.respond(result, 200)
        return True
    if path == '/api/v1/bears/platform-capacity':
        if method != 'GET':
            raise QueryError('NOT_FOUND', 404)
        if ('?' in handler.path or '#' in handler.path or params
                or _invalid_header_fields(handler.headers)
                or any(len(handler.headers.get_all(k, [])) != 1 for k in handler.headers.keys())
                or handler.headers.get_all('Transfer-Encoding', [])
                or handler.headers.get_all('Content-Length', []) not in ([], ['0'])):
            raise QueryError('INVALID_INPUT', 400)
        try:
            result = db_platform_capacity.observe(principal, role)
        except OperationError as error:
            raise QueryError(error.code, error.status) from None
        handler.respond(result, 200)
        return True
    if path == '/api/v1/bears/platform-resources':
        if method != 'GET':
            raise QueryError('NOT_FOUND', 404)
        if _invalid_basic_frame(handler, params):
            raise QueryError('INVALID_INPUT', 400)
        try:
            result = db_platform_resources.observe(principal, role)
        except OperationError as error:
            raise QueryError(error.code, error.status) from None
        handler.respond(result, 200)
        return True
    if path == '/api/v1/bears/platform-observations':
        if method != 'GET':
            raise QueryError('NOT_FOUND', 404)
        if _invalid_basic_frame(handler, params):
            raise QueryError('INVALID_INPUT', 400)
        try:
            result = db_platform_observations.observe(principal, role)
        except OperationError as error:
            raise QueryError(error.code, error.status) from None
        handler.respond(result, 200)
        return True
    return False
