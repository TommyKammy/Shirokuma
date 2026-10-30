"""Strict DBT HTTP adapter shared by Workbench and native HTTP."""
import re

import dataops_routes as common
import db_dbt as dbt
from query_context import QueryError
import warehouse_admission as admission


_ID = r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})'


def dispatch(handler, method, path, params, principal, role):
    if path != '/api/v1/dbt' and not path.startswith('/api/v1/dbt/'):
        return False
    if '#' in handler.path or handler.headers.get('Transfer-Encoding'):
        raise ValueError('invalid framing')
    match = re.fullmatch(r'/api/v1/dbt/(projects|plans|runs|publications)(?:/' + _ID + r'(?:/(cancel|reconcile|publish|publication-preview))?)?', path)
    if not match:
        raise QueryError('NOT_FOUND', 404)
    family, ident, action = match.groups()
    if method == 'GET':
        if handler.headers.get_all('Content-Length', []) not in ([], ['0']):
            raise ValueError('GET body forbidden')
        preview = family == 'runs' and ident and action == 'publication-preview'
        allowed = {'context', 'node_id'} if preview else {'context', 'plan_id'} if family == 'runs' and not ident else {'context'}
        if set(params) - allowed or 'context' not in params or any(len(v) != 1 for v in params.values()):
            raise ValueError('exact parameters required')
        if action and not preview or family == 'projects' and ident or family == 'publications' and not ident:
            raise QueryError('NOT_FOUND', 404)
        if preview and ('node_id' not in params or not re.fullmatch(r'[A-Za-z0-9_.-]{1,512}', params['node_id'][0])):
            raise ValueError('node ID required')
        plan_id = params.get('plan_id', [None])[0]
        if plan_id is not None and not re.fullmatch(_ID, plan_id):
            raise ValueError('invalid plan ID')
        context = common.context_value(common._json_context(params['context'][0]), role)
        body, key = {}, None
    elif method == 'POST':
        if params or len(handler.headers.get_all('Content-Type', [])) != 1:
            raise ValueError('POST query forbidden')
        key = common._key(handler)
        body = handler.database_body()
        required = {'context'}
        if family == 'plans' and not ident:
            required |= {'project_id', 'source_schema', 'target_schema', 'staging_materialization'}
        elif family == 'runs' and not ident:
            required.add('plan_id')
        elif family == 'runs' and ident and action in ('cancel', 'reconcile', 'publish'):
            if action == 'publish':
                required.add('node_id')
        else:
            raise QueryError('NOT_FOUND', 404)
        optional = {'executor', 'node_id', 'native_tests'} if family == 'runs' and not ident else {'expected_target'} if action == 'publish' else set()
        common._keys(body, required, optional)
        if family == 'runs' and not ident:
            dbt._run_arguments(body.get('executor', 'sql_wap'), body.get('node_id'))
            if type(body.get('native_tests', False)) is not bool or body.get('native_tests', False) and body.get('executor') != 'native_table':
                raise ValueError('native tests require boolean and native executor')
            if 'node_id' in body and body['node_id'] is None:
                raise ValueError('node ID must be a string')
        context = common.context_value(body['context'], role)
        for name in required - {'context', 'staging_materialization'}:
            if not isinstance(body[name], str) or not body[name].strip():
                raise ValueError('nonempty identifier required')
        if 'project_id' in body and not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', body['project_id']):
            raise ValueError('invalid project ID')
        for name in ('source_schema', 'target_schema'):
            if name in body and not (re.fullmatch(r'[a-z][a-z0-9_]{0,30}', body[name]) or re.fullmatch(_ID, body[name])):
                raise ValueError('registered schema identifier required')
        if 'node_id' in body and not re.fullmatch(r'[A-Za-z0-9_.-]{1,512}', body['node_id']):
            raise ValueError('invalid node ID')
        if 'plan_id' in body and not re.fullmatch(_ID, body['plan_id']):
            raise ValueError('invalid plan ID')
        if 'staging_materialization' in body and body['staging_materialization'] not in (None, 'table', 'ephemeral'):
            raise ValueError('invalid staging materialization')
    else:
        raise QueryError('NOT_FOUND', 404)
    envelope, status = dict(context=context.public_metadata()), 200
    try:
        if method == 'GET':
            if family == 'projects':
                envelope['projects'] = dbt.list_projects(principal, context)
            elif family == 'plans':
                if ident:
                    envelope['plan'] = dbt.get_plan(principal, context, ident)
                    envelope['run_options'] = dbt.get_run_options(principal, context, ident)
                else:
                    envelope['items'] = dbt.list_plans(principal, context)
            elif family == 'runs':
                if preview:
                    envelope['preview'] = dbt.get_publication_preview(principal, context, ident, node_id=params['node_id'][0])
                elif ident:
                    envelope['run'] = dbt.get_run(principal, context, ident)
                else:
                    envelope['items'] = dbt.list_runs(principal, context, plan_id=plan_id)
            else:
                envelope['publication'] = dbt.get_publication(principal, context, ident)
        elif family == 'plans':
            envelope['plan'] = dbt.submit_plan(principal, context, key=key,
                **{k: body[k] for k in ('project_id', 'source_schema', 'target_schema', 'staging_materialization')})
            status = 202
        elif not ident:
            envelope['run'] = dbt.submit_run(principal, context, key=key, plan_id=body['plan_id'],
                **{k: body[k] for k in ('executor', 'node_id', 'native_tests') if k in body})
            status = 202
        elif action == 'publish':
            envelope['publication'] = dbt.submit_publication(principal, context, ident, key=key, node_id=body['node_id'],
                **{k: body[k] for k in ('expected_target',) if k in body})
            status = 202
        else:
            function = dbt.cancel_run if action == 'cancel' else dbt.reconcile_run
            envelope['run'] = function(principal, context, ident, key=key)
    except (admission.AdmissionDenied, admission.AdmissionConflict, admission.GateTimeout) as error:
        raise QueryError(error.code, error.status) from None
    handler.respond(envelope, status)
    return True
