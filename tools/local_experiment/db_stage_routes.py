"""One strict normal Stage HTTP dispatcher for Workbench and native endpoints."""
import re
import json

import control_store
import db_stages as stages
import db_stage_scans as scans
import dataops_routes as common
from query_context import QueryError
import warehouse_admission as admission


def dispatch(handler, method, path, params, principal, role):
    if path != '/api/v1/stages' and not path.startswith('/api/v1/stages/'):
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
        key = common._key(handler)
        body = handler.database_body()
        context = common.context_value(body.get('context'), role)
    else:
        raise QueryError('NOT_FOUND', 404)
    envelope, status = dict(context=context.public_metadata()), 200
    ident = r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})'
    match = re.fullmatch(r'/api/v1/stages/' + ident + r'(?:/(files|preview|imports|exports|runs|scans)(?:/' + ident + r'(?:/(cancel|content|retry))?)?)?', path)
    try:
        if path == '/api/v1/stages':
            if method == 'GET':
                envelope.update(stages=stages.list_stages(principal, context),
                                local_roots=[str(p) for p in stages.local_roots()])
            else:
                common._keys(body, {'context', 'expected_revision', 'definition'}, {'stage_id'})
                envelope['stage'] = stages.save_stage(principal, context, key=key,
                    **{k: v for k, v in body.items() if k != 'context'})
        elif match:
            stage_id, action, child_id, suffix = match.groups()
            envelope['stage_id'] = stage_id
            if action is None and method == 'GET':
                envelope.pop('stage_id')
                envelope['stage'] = stages.get_stage(principal, context, stage_id)
            elif action == 'files' and child_id is None and method == 'GET':
                envelope.update(stages.list_files(principal, context, stage_id))
            elif action == 'preview' and child_id is None and method == 'POST':
                common._keys(body, {'context', 'expected_revision', 'file', 'version'})
                envelope.update(stages.preview(principal, context, stage_id, key=key,
                    **{k: v for k, v in body.items() if k != 'context'}))
            elif action in ('imports', 'exports') and child_id is None and method == 'POST':
                required = {'expected_revision', 'file', 'version', 'expected_preview_fingerprint'} if action == 'imports' else {'expected_revision', 'source', 'format', 'file'}
                common._keys(body, required | {'context'})
                options = {k: v for k, v in body.items() if k != 'context'}
                envelope['run'] = (stages.import_file if action == 'imports' else stages.export_table)(
                    principal, context, stage_id, key=key, **options)
                status = 202
            elif action == 'scans':
                if child_id is None and method == 'GET':
                    envelope['scans'] = scans.list_scans(principal, context, stage_id)
                elif child_id is None and method == 'POST':
                    common._keys(body, {'context', 'expected_revision'})
                    envelope['scan'] = scans.submit(principal, context, stage_id, key=key,
                                                   expected_revision=body['expected_revision'])
                    status = 202
                elif child_id and suffix is None and method == 'GET':
                    envelope['scan'] = scans.get(principal, context, stage_id, child_id)
                elif child_id and suffix in ('retry', 'cancel') and method == 'POST':
                    required = {'context', 'expected_revision'}
                    if suffix == 'retry':
                        required |= {'item_id', 'expected_attempt'}
                    common._keys(body, required)
                    if suffix == 'cancel':
                        with control_store.connect() as db:
                            saved = scans._scan(db, principal, context, stage_id, child_id)
                        context = common._cancel_context(principal, context, json.loads(saved['record'])['context'])
                    envelope['scan'] = (scans.retry if suffix == 'retry' else scans.cancel)(
                        principal, context, stage_id, child_id, key=key,
                        **{k: v for k, v in body.items() if k != 'context'})
                    if suffix == 'retry':
                        status = 202
                else:
                    raise QueryError('NOT_FOUND', 404)
            elif action == 'runs':
                if child_id is None and method == 'GET':
                    envelope['runs'] = stages.list_runs(principal, context, stage_id)
                elif child_id and suffix is None and method == 'GET':
                    envelope['run'] = stages.get_run(principal, context, stage_id, child_id)
                elif child_id and suffix == 'cancel' and method == 'POST':
                    common._keys(body, {'context'})
                    with control_store.connect() as db:
                        _, saved = stages._run_row(db, principal, context, stage_id, child_id)
                    context = common._cancel_context(principal, context, saved['context'])
                    canceled = stages.cancel_run(principal, context, stage_id, child_id, key=key)
                    envelope['run'] = {name: canceled[name] for name in ('id', 'status')}
                else:
                    raise QueryError('NOT_FOUND', 404)
            elif action == 'exports' and method == 'GET':
                if child_id is None:
                    envelope['receipts'] = stages.list_exports(principal, context, stage_id)
                elif suffix is None:
                    envelope['receipt'] = stages.get_export(principal, context, stage_id, child_id)
                elif suffix == 'content':
                    artifact = stages.read_export(principal, context, stage_id, child_id)
                    raw, receipt = artifact['content'], artifact['receipt']
                    handler.send_response(200)
                    handler.send_header('Content-Type', 'text/csv; charset=utf-8' if receipt['format'] == 'csv' else 'application/x-ndjson; charset=utf-8')
                    handler.send_header('Content-Length', str(len(raw)))
                    handler.send_header('Cache-Control', 'no-store')
                    handler.end_headers()
                    handler.wfile.write(raw)
                    return True
                else:
                    raise QueryError('NOT_FOUND', 404)
            else:
                raise QueryError('NOT_FOUND', 404)
        else:
            raise QueryError('NOT_FOUND', 404)
    except (admission.AdmissionDenied, admission.AdmissionConflict, admission.GateTimeout) as error:
        raise QueryError(error.code, error.status, details=envelope) from None
    handler.respond(envelope, status)
    return True
