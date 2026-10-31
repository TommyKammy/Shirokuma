"""Strict Database SQL explanation adapter for both HTTP surfaces."""
import re

import dataops_routes as common
import db_ai
import db_ai_plan
import db_ai_sources
from query_context import QueryError


def dispatch(handler, method, path, params, principal, role):
    if path != '/api/v1/bears' and not path.startswith('/api/v1/bears/'):
        return False
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
    match = re.fullmatch(r'/api/v1/bears/(explanations|plan-explanations)(?:/([0-9a-f-]{36})(/cancel)?)?', path)
    if not match:
        raise QueryError('NOT_FOUND', 404)
    if '#' in handler.path or handler.headers.get('Transfer-Encoding'):
        raise ValueError('invalid framing')
    family, ident, cancel = match.groups()
    service = db_ai_plan if family == 'plan-explanations' else db_ai
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
        common._keys(body, {'context', 'sql', 'language'})
        context = common.context_value(body['context'], role)
        result = {'explanation': service.submit(principal, context, key=key,
            sql=body['sql'], language=body['language'])}
        status = 202
    else:
        raise QueryError('NOT_FOUND', 404)
    handler.respond(dict(context=context.public_metadata(), **result), status)
    return True
