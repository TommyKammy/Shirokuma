"""Strict GET-only Database audit HTTP boundary."""
import json
import re

import audit_service as audit


def dispatch(handler, method, path, params, principal, role):
    if path != '/api/v1/audit' and not path.startswith('/api/v1/audit/'):
        return False
    if method != 'GET':
        audit.fail('METHOD_NOT_ALLOWED', 405)
    headers = handler.headers
    roles = headers.get_all('X-Shirokuma-Role', [])
    if len(roles) != 1 or roles[0] != role or role not in audit.request_auth.ROLES:
        audit.fail('INVALID_INPUT', 400)
    if ('#' in handler.path or headers.get_all('Transfer-Encoding', []) or
            headers.get_all('Content-Length', []) not in ([], ['0'])):
        audit.fail('INVALID_INPUT', 400)
    detail = re.fullmatch(r'/api/v1/audit/events/([A-Za-z0-9_-]{1,2048})(/download)?', path)
    if path == '/api/v1/audit/events':
        allowed = {'scope', 'database_id', 'source', 'state', 'search', 'from', 'to', 'limit', 'cursor'}
    elif detail:
        allowed = {'scope', 'database_id'}
    elif path == '/api/v1/audit/telemetry':
        allowed = {'limit'}
    elif path == '/api/v1/audit/capabilities':
        allowed = set()
    else:
        audit.fail()
    if set(params) - allowed or any(len(v) != 1 for v in params.values()):
        audit.fail('INVALID_INPUT', 400)
    values = {k: v[0] for k, v in params.items()}
    if 'limit' in values:
        if not re.fullmatch(r'[0-9]{1,3}', values['limit']):
            audit.fail('INVALID_INPUT', 400)
        values['limit'] = int(values['limit'])
    if path.endswith('/capabilities'):
        result = audit.capabilities(principal, role)
    elif path.endswith('/telemetry'):
        result = audit.telemetry(principal, role, **values)
    else:
        result = audit.events(principal, role, event_id=detail[1] if detail else None, **values)
    if detail and detail[2]:
        # Exactly the same redacted event as detail, with identical authorization.
        raw = json.dumps(result, ensure_ascii=False).encode()
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/json; charset=utf-8')
        handler.send_header('Content-Disposition', 'attachment; filename="audit-metadata.json"')
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('Content-Length', str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)
    else:
        handler.respond(result)
    return True
