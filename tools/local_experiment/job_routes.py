"""Strict normal authenticated HTTP adapter for database Jobs."""
import re
import json

import control_store
import db_jobs as jobs
import dataops_routes as common
from query_context import QueryError
import warehouse_admission as admission


def dispatch(handler, method, path, params, principal, role):
    if path != '/api/v1/jobs' and not path.startswith('/api/v1/jobs/'):
        return False
    if '#' in handler.path or handler.headers.get('Transfer-Encoding'):
        raise ValueError('invalid framing')
    if method == 'GET':
        if handler.headers.get_all('Content-Length', []) not in ([], ['0']):
            raise ValueError('GET body forbidden')
        allowed = {'context', 'job_id'} if path == '/api/v1/jobs/runs' else {'context'}
        if set(params) - allowed or 'context' not in params or any(len(v) != 1 for v in params.values()):
            raise ValueError('exact context required')
        context = common.context_value(common._json_context(params['context'][0]), role)
        body = {}
    elif method == 'POST':
        if params or len(handler.headers.get_all('Content-Type', [])) != 1:
            raise ValueError('POST query forbidden')
        body = handler.database_body()
        context = common.context_value(body.get('context'), role)
    else:
        raise QueryError('NOT_FOUND', 404)
    envelope = dict(context=context.public_metadata())
    match = re.fullmatch(r'/api/v1/jobs/runs/([A-Za-z0-9_-]{1,128})(?:/(cancel|reconcile))?', path)
    job = re.fullmatch(r'/api/v1/jobs/([A-Za-z0-9_-]{1,128})(/runs)?', path)
    try:
        if match:
            run_id, action = match.groups()
            if method == 'GET' and action is None:
                envelope['run'] = jobs.get_run(principal, context, run_id)
            elif method == 'POST' and action in ('cancel', 'reconcile'):
                common._keys(body, {'context'})
                if action == 'cancel':
                    with control_store.connect() as db:
                        saved = jobs._run(db, run_id, principal, context)
                    context = common._cancel_context(principal, context, json.loads(saved['context']))
                envelope['run'] = getattr(jobs, action + '_run')(principal, context, run_id)
            else:
                raise QueryError('NOT_FOUND', 404)
        elif path == '/api/v1/jobs/runs' and method == 'GET':
            envelope['runs'] = jobs.list_runs(principal, context, params.get('job_id', [None])[0])
        elif path == '/api/v1/jobs' and method == 'GET':
            envelope['jobs'] = jobs.list_jobs(principal, context)
        elif path == '/api/v1/jobs' and method == 'POST':
            common._keys(body, {'context', 'expected_revision', 'definition'}, {'job_id'})
            envelope['job'] = jobs.save_job(principal, context, key=common._key(handler),
                **{k: v for k, v in body.items() if k != 'context'})
        elif job and job[1] != 'runs':
            if method == 'GET' and not job[2]:
                envelope['job'] = jobs.get_job(principal, context, job[1])
            elif method == 'POST' and job[2]:
                common._keys(body, {'context', 'expected_revision', 'arguments'}, {'inputs'})
                envelope['run'] = jobs.submit_run(principal, context, job[1], key=common._key(handler),
                    **{k: v for k, v in body.items() if k != 'context'})
            else:
                raise QueryError('NOT_FOUND', 404)
        else:
            raise QueryError('NOT_FOUND', 404)
    except (admission.AdmissionDenied, admission.AdmissionConflict, admission.GateTimeout) as error:
        raise QueryError(error.code, error.status) from None
    handler.respond(envelope)
    return True
