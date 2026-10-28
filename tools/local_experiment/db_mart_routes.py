"""Strict normal DB Mart dispatcher shared by Workbench and native HTTP."""
import re

import dataops_routes as common
import db_marts as marts
from query_context import QueryError
import warehouse_admission as admission


def dispatch(handler, method, path, params, principal, role):
    if path != '/api/v1/marts' and not path.startswith('/api/v1/marts/'):
        return False
    if '#' in handler.path or handler.headers.get('Transfer-Encoding'):
        raise ValueError('invalid framing')
    if method == 'GET':
        if handler.headers.get_all('Content-Length', []) not in ([], ['0']):
            raise ValueError('GET body forbidden')
        if set(params) != {'context'} or len(params['context']) != 1:
            raise ValueError('exact context required')
        context = common.context_value(common._json_context(params['context'][0]), role)
        body, key = {}, None
    elif method == 'POST':
        if params or len(handler.headers.get_all('Content-Type', [])) != 1:
            raise ValueError('POST query forbidden')
        keys = handler.headers.get_all('X-Idempotency-Key', [])
        if len(keys) != 1 or not keys[0].strip() or len(keys[0]) > 256:
            raise ValueError('one X-Idempotency-Key required')
        key = keys[0]
        body = handler.database_body()
        context = common.context_value(body.get('context'), role)
    else:
        raise QueryError('NOT_FOUND', 404)
    ident = r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})'
    match = re.fullmatch('/api/v1/marts/' + ident + '(?:/(checks|refreshes|runs)(?:/' + ident + ')?)?', path)
    envelope, status = dict(context=context.public_metadata()), 200
    try:
        if path == '/api/v1/marts':
            if method == 'GET':
                envelope['items'] = marts.list_marts(principal, context)
            else:
                common._keys(body, {'context', 'expected_revision', 'definition'})
                envelope['mart'] = marts.save_mart(principal, context, key=key,
                    expected_revision=body['expected_revision'], definition=body['definition'])
        elif match:
            mart_id, action, run_id = match.groups()
            if action is None and method == 'GET':
                envelope['mart'] = marts.get_mart(principal, context, mart_id)
            elif action is None and method == 'POST':
                common._keys(body, {'context', 'expected_revision', 'definition'})
                envelope['mart'] = marts.save_mart(principal, context, key=key, mart_id=mart_id,
                    expected_revision=body['expected_revision'], definition=body['definition'])
            elif action in ('checks', 'refreshes') and run_id is None and method == 'POST':
                common._keys(body, {'context', 'expected_revision'}, {'check_changes'} if action == 'refreshes' else set())
                envelope['run'] = marts.submit(principal, context, mart_id, key=key,
                    expected_revision=body['expected_revision'], kind='check' if action == 'checks' else 'refresh',
                    check_changes=body.get('check_changes', False))
                status = 202
            elif action == 'runs' and method == 'GET':
                if run_id:
                    envelope['run'] = marts.get_run(principal, context, mart_id, run_id)
                else:
                    envelope['items'] = marts.list_runs(principal, context, mart_id)
            else:
                raise QueryError('NOT_FOUND', 404)
        else:
            raise QueryError('NOT_FOUND', 404)
    except (admission.AdmissionDenied, admission.AdmissionConflict, admission.GateTimeout) as error:
        raise QueryError(error.code, error.status) from None
    handler.respond(envelope, status)
    return True
