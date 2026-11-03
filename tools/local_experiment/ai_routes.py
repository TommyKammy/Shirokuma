"""Strict Database SQL explanation adapter for both HTTP surfaces."""
import re
from urllib.parse import unquote_to_bytes

import dataops_routes as common
import db_ai
import db_ai_plan
import db_ai_sources
import db_ai_reexplain
import db_ai_sql_proposal
import db_ai_catalog
import db_ai_wap
import db_bear_observations
import db_platform_observations
import db_platform_resources
import db_platform_usage
import db_platform_capacity
import db_platform_lifecycle_history
import db_platform_workload_baseline
import db_ai_settings
from warehouse_operations import OperationError
from query_context import QueryError


def dispatch(handler, method, path, params, principal, role):
    if path != '/api/v1/bears' and not path.startswith('/api/v1/bears/'):
        return False
    if path == '/api/v1/bears/platform-runtime-history':
        if method != 'GET':
            raise QueryError('NOT_FOUND', 404)
        raw = handler.path.partition('?')[2]
        if ('#' in handler.path or handler.headers.defects
                or any(not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", k)
                       or any(ord(c) < 32 and c != '\t' or ord(c) == 127 for c in v)
                       for k, v in handler.headers.raw_items())
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
    if path in ('/api/v1/bears/ai-config', '/api/v1/bears/ai-test'):
        if method not in (('GET', 'POST') if path.endswith('ai-config') else ('POST',)):
            raise QueryError('NOT_FOUND', 404)
        if ('?' in handler.path or '#' in handler.path or params
                or handler.headers.get_all('Transfer-Encoding', [])
                or any(len(handler.headers.get_all(k, [])) != 1 for k in handler.headers.keys())):
            raise QueryError('INVALID_INPUT', 400)
        if method == 'GET':
            if handler.headers.get_all('Content-Length', []) not in ([], ['0']):
                raise QueryError('INVALID_INPUT', 400)
            body, action = None, 'get'
        else:
            if len(handler.headers.get_all('Content-Type', [])) != 1:
                raise QueryError('INVALID_INPUT', 400)
            body = handler.database_body()
            action = 'save' if path.endswith('ai-config') else 'test'
        try:
            result = db_ai_settings.perform(principal, role, action, body)
        except OperationError as error:
            raise QueryError(error.code, error.status) from None
        handler.respond(result, 200)
        return True
    if path == '/api/v1/bears/platform-workload-baseline':
        if method != 'GET':
            raise QueryError('NOT_FOUND', 404)
        if ('?' in handler.path or '#' in handler.path or params
                or handler.headers.get_all('Transfer-Encoding', [])
                or handler.headers.get_all('Content-Length', []) not in ([], ['0'])):
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
    if path == '/api/v1/bears/platform-capacity':
        if method != 'GET':
            raise QueryError('NOT_FOUND', 404)
        if ('?' in handler.path or '#' in handler.path or params
                or handler.headers.defects
                or any(not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", k)
                       or any(ord(c) < 32 and c != '\t' or ord(c) == 127 for c in v)
                       for k, v in handler.headers.raw_items())
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
        if ('?' in handler.path or '#' in handler.path or params
                or handler.headers.get_all('Transfer-Encoding', [])
                or handler.headers.get_all('Content-Length', []) not in ([], ['0'])):
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
        if ('?' in handler.path or '#' in handler.path or params
                or handler.headers.get_all('Transfer-Encoding', [])
                or handler.headers.get_all('Content-Length', []) not in ([], ['0'])):
            raise QueryError('INVALID_INPUT', 400)
        try:
            result = db_platform_observations.observe(principal, role)
        except OperationError as error:
            raise QueryError(error.code, error.status) from None
        handler.respond(result, 200)
        return True
    if path == '/api/v1/bears/context-observations':
        if method != 'GET':
            raise QueryError('NOT_FOUND', 404)
        if ('#' in handler.path or handler.headers.get_all('Transfer-Encoding', [])
                or handler.headers.get_all('Content-Length', []) not in ([], ['0'])
                or set(params) != {'context'} or len(params['context']) != 1):
            raise QueryError('INVALID_INPUT', 400)
        try:
            unquote_to_bytes(handler.path).decode('utf-8')
        except UnicodeError:
            raise QueryError('INVALID_INPUT', 400) from None
        context = db_bear_observations.parse_context(params['context'][0], role)
        handler.respond(db_bear_observations.observe(principal, context), 200)
        return True
    if path == '/api/v1/bears/query-sources/latest':
        if method != 'GET':
            raise QueryError('NOT_FOUND', 404)
        if ('#' in handler.path or handler.headers.get_all('Transfer-Encoding', [])
                or handler.headers.get_all('Content-Length', []) not in ([], ['0'])
                or set(params) != {'context'} or len(params['context']) != 1):
            raise ValueError('exact context required')
        context = common.context_value(common._json_context(params['context'][0]), role)
        source = db_ai_sources.latest(principal, context)
        handler.respond(dict(context=context.public_metadata(), source=source), 200)
        return True
    draft = re.fullmatch(r'/api/v1/bears/sql-proposals/([0-9a-f-]{36})/wap-draft', path)
    if draft:
        if method != 'GET':
            raise QueryError('NOT_FOUND', 404)
        if ('#' in handler.path or handler.headers.get_all('Transfer-Encoding', [])
                or handler.headers.get_all('Content-Length', []) not in ([], ['0'])
                or handler.headers.get_all('Idempotency-Key', [])
                or set(params) != {'context', 'destination_context'}
                or any(len(params[k]) != 1 for k in params)):
            raise ValueError('exact source and destination contexts required')
        context = common.context_value(common._json_context(params['context'][0]), role)
        destination = common.context_value(common._json_context(params['destination_context'][0]), role)
        result = db_ai_wap.prepare(principal, context, destination, draft[1])
        handler.respond(dict(context=context.public_metadata(), draft=result), 200)
        return True
    match = re.fullmatch(r'/api/v1/bears/(explanations|plan-explanations|saved-plan-explanations|sql-proposals|catalog-proposals)(?:/([0-9a-f-]{36})(/cancel)?)?', path)
    if not match:
        raise QueryError('NOT_FOUND', 404)
    if '#' in handler.path or handler.headers.get('Transfer-Encoding'):
        raise ValueError('invalid framing')
    family, ident, cancel = match.groups()
    if family == 'catalog-proposals' and handler.headers.get_all('Transfer-Encoding', []):
        raise ValueError('invalid framing')
    service = (db_ai_catalog if family == 'catalog-proposals' else db_ai_sql_proposal if family == 'sql-proposals' else db_ai_reexplain if family == 'saved-plan-explanations' else
        db_ai_plan if family == 'plan-explanations' else db_ai)
    if cancel and (service is db_ai or method != 'POST'):
        raise QueryError('NOT_FOUND', 404)
    if method == 'GET':
        if (handler.headers.get_all('Content-Length', []) not in ([], ['0'])
                or set(params) != {'context'} or len(params['context']) != 1):
            raise ValueError('exact context required')
        context = common.context_value(common._json_context(params['context'][0]), role)
        result = {'explanation': service.get(principal, context, ident)} if ident else {
            'explanations': service.history(principal, context)}
        status = 200
    elif method == 'POST' and cancel:
        if params or len(handler.headers.get_all('Content-Type', [])) != 1:
            raise ValueError('invalid POST framing')
        body = handler.database_body()
        common._keys(body, {'context'})
        context = common.context_value(body['context'], role)
        result = {'explanation': service.cancel(principal, context, ident)}
        status = 200
    elif method == 'POST' and not ident:
        if params or len(handler.headers.get_all('Content-Type', [])) != 1:
            raise ValueError('invalid POST framing')
        key = common._key(handler)
        body = handler.database_body()
        fields = ({'relation'} if service is db_ai_catalog else {'request', 'relations'} if service is db_ai_sql_proposal else
            {'source_explanation_id'} if service is db_ai_reexplain else {'sql'})
        common._keys(body, {'context', 'language'} | fields)
        context = common.context_value(body['context'], role)
        result = {'explanation': service.submit(principal, context, key=key,
            language=body['language'], **{field: body[field] for field in fields})}
        status = 202
    else:
        raise QueryError('NOT_FOUND', 404)
    handler.respond(dict(context=context.public_metadata(), **result), status)
    return True
