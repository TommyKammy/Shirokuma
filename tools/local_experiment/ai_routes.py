"""Strict Database SQL explanation adapter for both HTTP surfaces."""
import re

import dataops_routes as common
import db_ai
from query_context import QueryError


def dispatch(handler, method, path, params, principal, role):
    if path != '/api/v1/bears' and not path.startswith('/api/v1/bears/'):
        return False
    match = re.fullmatch(r'/api/v1/bears/explanations(?:/([0-9a-f-]{36}))?', path)
    if not match:
        raise QueryError('NOT_FOUND', 404)
    if '#' in handler.path or handler.headers.get('Transfer-Encoding'):
        raise ValueError('invalid framing')
    ident = match[1]
    if method == 'GET':
        if (handler.headers.get_all('Content-Length', []) not in ([], ['0'])
                or set(params) != {'context'} or len(params['context']) != 1):
            raise ValueError('exact context required')
        context = common.context_value(common._json_context(params['context'][0]), role)
        result = {'explanation': db_ai.get(principal, context, ident)} if ident else {
            'explanations': db_ai.history(principal, context)}
        status = 200
    elif method == 'POST' and not ident:
        if params or len(handler.headers.get_all('Content-Type', [])) != 1:
            raise ValueError('invalid POST framing')
        key = common._key(handler)
        body = handler.database_body()
        common._keys(body, {'context', 'sql', 'language'})
        context = common.context_value(body['context'], role)
        result = {'explanation': db_ai.submit(principal, context, key=key,
            sql=body['sql'], language=body['language'])}
        status = 202
    else:
        raise QueryError('NOT_FOUND', 404)
    handler.respond(dict(context=context.public_metadata(), **result), status)
    return True
