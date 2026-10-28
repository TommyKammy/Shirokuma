"""Shared non-purge retirement HTTP boundary, independent of generation handoffs."""
import json
import re
from urllib.parse import parse_qs, urlsplit

import catalog_admin
import request_auth


def is_retirement_operation(operation_id):
    import control_store
    with control_store.connect() as db:
        row = db.execute('SELECT action FROM operations WHERE id=?', (operation_id,)).fetchone()
    return row is not None and row['action'] == 'DATABASE_RETIRE'


def body(handler, length):
    def pairs(items):
        value = {}
        for name, item in items:
            if name in value:
                raise ValueError('duplicate JSON field')
            value[name] = item
        return value
    raw = handler.rfile.read(length)
    if len(raw) != length:
        raise ValueError('incomplete JSON body')
    value = json.loads(raw, object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('invalid JSON number')))
    if not isinstance(value, dict):
        raise ValueError('JSON object required')
    return value


def dispatch(handler, method):
    url = urlsplit(handler.path)
    retirement = re.fullmatch(r'/api/v1/admin/databases/([A-Za-z0-9_-]{1,128})/retire', url.path)
    operation = re.fullmatch(r'/api/v1/operations/([A-Za-z0-9_-]{1,128})(/reconcile)?', url.path)
    if not retirement and not operation:
        return False
    backend = None
    try:
        # Generic URLs retain their original authentication, framing and body
        # handling unless the canonical registry identifies a retirement.
        if operation and not is_retirement_operation(operation[1]):
            return False
        principal = request_auth.authenticate_principal(handler.headers)
        roles = handler.headers.get_all('X-Shirokuma-Role', [])
        if len(roles) != 1 or roles[0] not in request_auth.ROLES:
            raise ValueError('one explicit role required')
        role = roles[0]
        catalog_admin.require_admin(principal, role)
        try:
            import database_retirement as backend
        except ImportError:
            handler.respond(dict(code='SERVICE_UNAVAILABLE', message='Retirement service unavailable',
                                 details={}, retryable=True), 503)
            return True
        if operation:
            result = backend.inspect(principal, role, operation[1])
            if not isinstance(result, dict) or result.get('action') != 'DATABASE_RETIRE':
                raise RuntimeError('Retirement inspection type mismatch')
        headers = handler.headers
        if ('?' in handler.path or '#' in handler.path or parse_qs(url.query, keep_blank_values=True) or
                headers.get_all('Transfer-Encoding', [])):
            raise ValueError('invalid route framing')
        origins = headers.get_all('Origin', [])
        if origins:
            from workbench_server import request_origin_allowed
            if len(origins) != 1 or not origins[0] or not request_origin_allowed(headers):
                raise PermissionError('invalid origin')
        if method == 'GET' and (retirement or not operation[2]):
            if headers.get_all('Content-Length', []) not in ([], ['0']):
                raise ValueError('GET body forbidden')
            if retirement:
                result = backend.preview(principal, role, retirement[1])
            handler.respond(result)
        elif method == 'POST' and (retirement or operation[2]):
            keys = headers.get_all('Idempotency-Key', [])
            lengths = headers.get_all('Content-Length', [])
            if (len(keys) != 1 or not keys[0].strip() or len(keys[0]) > 256 or
                    any(ord(c) < 32 or ord(c) == 127 for c in keys[0]) or
                    headers.get_all('X-Idempotency-Key', []) or
                    len(headers.get_all('Content-Type', [])) != 1 or
                    headers.get_content_type() != 'application/json' or
                    len(lengths) != 1 or not re.fullmatch(r'[0-9]+', lengths[0]) or
                    not 0 < int(lengths[0]) <= 10 * 1024 * 1024):
                raise ValueError('invalid mutation framing')
            payload = body(handler, int(lengths[0]))
            if (set(payload) != {'expected_revision'} or type(payload['expected_revision']) is not int or
                    payload['expected_revision'] < 1):
                raise ValueError('exact positive revision required')
            result = (backend.request_retire(principal, role, retirement[1], payload, keys[0]) if retirement else
                      backend.reconcile(principal, role, operation[1], payload, keys[0]))
            handler.respond(result, 202)
        else:
            handler.respond(dict(code='NOT_FOUND', message='Route not found', details={}, retryable=False), 404)
    except Exception as error:
        if isinstance(error, request_auth.AuthenticationError):
            code, message, status = 'AUTHENTICATION_REQUIRED', 'Authentication required', 401
        elif isinstance(error, PermissionError):
            code, message, status = 'FORBIDDEN', 'Current platform administrator with selected admin required', 403
        elif backend is not None and isinstance(error, backend.RetirementError):
            code, message, status = error.code, str(error), error.status
        elif isinstance(error, (ValueError, TypeError, UnicodeError)):
            code, message, status = 'INVALID_INPUT', 'Invalid retirement request', 400
        else:
            code, message, status = 'SERVICE_UNAVAILABLE', 'Retirement service unavailable', 503
        handler.respond(dict(code=code, message=message, details={}, retryable=status >= 500), status)
    return True
