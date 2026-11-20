"""Shared read-only inventory bootstrap for Workbench and native HTTP."""
import re

import database_inventory as inventory
import dataops_routes as common
import object_lineage
from query_context import QueryError


def dispatch(handler, method, path, params, principal, role):
    lineage = re.fullmatch(r'/api/v1/objects/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/lineage', path)
    search = path == '/api/v1/objects/search'
    if path.endswith('/lineage') and path.startswith('/api/v1/objects/'):
        if not lineage or method != 'GET':
            raise QueryError('NOT_FOUND', 404)
    if not lineage and (method != 'GET' or not (search or path in ('/api/v1/databases', '/api/v1/objects/resolve') or
            re.fullmatch(r'/api/v1/databases/[^/]+/schemas', path))):
        return False
    if ('#' in handler.path or handler.headers.get_all('Transfer-Encoding', []) or
            handler.headers.get_all('Content-Length', []) not in ([], ['0'])):
        raise ValueError('GET routes do not accept request bodies or fragments')
    if lineage:
        if set(params) != {'context'} or len(params['context']) != 1:
            raise ValueError('exact context required')
        context = common.context_value(common._json_context(params['context'][0]), role)
        result = object_lineage.get_lineage(principal, context, lineage[1])
    elif search:
        if method != 'GET' or set(params) - {'q', 'kind', 'limit'} or any(
                len(v) != 1 for v in params.values()) or 'q' not in params:
            raise ValueError('Search text and exact parameters are required')
        limit = params.get('limit', ['20'])[0]
        if not re.fullmatch(r'[0-9]+', limit):
            raise ValueError('limit must be an integer')
        result = inventory.search_objects(principal, role, q=params['q'][0],
            kind=params['kind'][0] if 'kind' in params else None, limit=int(limit))
    elif path == '/api/v1/objects/resolve':
        if set(params) != {'database_id', 'schema_id', 'name'} or any(
                len(v) != 1 or not v[0] for v in params.values()):
            raise ValueError('Object context is required')
        result = inventory.resolve_object(principal, role, **{k: v[0] for k, v in params.items()})
    else:
        if params:
            raise ValueError('Invalid route parameters')
        result = (inventory.list_databases(principal, role) if path == '/api/v1/databases' else
                  inventory.list_schemas(principal, role, path.split('/')[4]))
    handler.respond(result)
    return True
