"""Strict authenticated database DataOps HTTP adapter, with no legacy dispatch."""
import base64
import json
import re
from dataclasses import fields

import control_store
import data_management as registry
import dataops_service as ds
import manual_export
import manual_ingest
import manual_wap
import warehouse_admission as admission
from query_context import QueryError, ResolvedQueryContext, resolve_context


def context_value(value, role):
    names = {f.name for f in fields(ResolvedQueryContext)}
    if (not isinstance(value, dict) or set(value) != names
            or any(not isinstance(value[k], str) or not value[k].strip() for k in names - {'binding_revision', 'grant_revision'})
            or type(value['binding_revision']) is not int or value['binding_revision'] < 1
            or (value['grant_revision'] is not None and (type(value['grant_revision']) is not int or value['grant_revision'] < 1))
            or value['selected_role'] != role):
        raise ValueError('exact resolved context required')
    return ResolvedQueryContext(**value)


def current_context(principal, context):
    current = resolve_context(principal, context.database_id, context.schema_id, context.selected_role,
        warehouse_id=context.warehouse_id, expected_binding_revision=context.binding_revision)
    if current != context:
        raise QueryError('CONTEXT_MISMATCH', 409)


def reference(value, *, locator=False):
    cls = registry.DestinationLocator if locator else registry.ResourceRef
    if (not isinstance(value, dict) or set(value) != {f.name for f in fields(cls)}
            or any(not isinstance(v, str) or not v.strip() for k, v in value.items() if k != 'incarnation')):
        raise ValueError('exact resource reference required')
    return cls(**value)


def _keys(body, required, optional=()):
    if not set(required) <= set(body) or set(body) - set(required) - set(optional):
        raise ValueError('invalid DataOps fields')


def _json_context(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('duplicate context field')
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs)


def _key(handler):
    keys = handler.headers.get_all('Idempotency-Key', [])
    if len(keys) != 1 or not keys[0].strip() or len(keys[0]) > 256:
        raise ValueError('one idempotency key required')
    return keys[0]


def _saved(execution, context):
    row = execution._row()  # Ownership is checked before context, including cancel.
    if json.loads(row['context']) != context.public_metadata():
        raise QueryError('CONTEXT_MISMATCH', 409)
    return row


def _cancel_context(principal, context, saved):
    """Exact persisted selection for cancel only; no READY/grant resolution."""
    ds._cancel_account(principal)
    if saved != context.public_metadata():
        raise QueryError('CONTEXT_MISMATCH', 409)
    return context


def _cancel_observation(execution):
    """Cancellation remains available after revocation; disclose no query results."""
    row = execution._row()
    return dict(id=execution.id, state=row['state'])


def _cancel_unknown(execution):
    row = execution._row()
    if row['state'] == 'UNKNOWN':
        return True
    if row['state'] in ('ACTIVE', 'CANCELING'):
        with control_store.connect() as db:
            return db.execute('''SELECT 1 FROM dataops_queries d JOIN query_records q
                ON q.id=d.query_id WHERE d.operation_id=? AND q.state='UNKNOWN' ''',
                (execution.id,)).fetchone() is not None
    return False


def dispatch(handler, method, path, params, principal, role):
    """Return False only for routes outside this adapter; otherwise respond once."""
    if not any(path == '/api/v1/' + prefix or path.startswith('/api/v1/' + prefix + '/')
               for prefix in ('ingests', 'wap', 'table-exports', 'dataops')):
        return False
    if '#' in handler.path or handler.headers.get('Transfer-Encoding'):
        raise ValueError('invalid framing')
    if method == 'GET':
        if handler.headers.get_all('Content-Length', []) not in ([], ['0']):
            raise ValueError('GET body forbidden')
        allowed = {'context', 'model_id', 'version'} if path == '/api/v1/wap/models' else {'context'}
        if set(params) - allowed or 'context' not in params or any(len(v) != 1 for v in params.values()):
            raise ValueError('invalid context parameters')
        body = {}
        context = context_value(_json_context(params['context'][0]), role)
    elif method == 'POST':
        if params or len(handler.headers.get_all('Content-Type', [])) != 1:
            raise ValueError('POST query forbidden')
        body = handler.database_body()
        context = context_value(body.get('context'), role)
    else:
        raise QueryError('NOT_FOUND', 404)
    envelope = dict(context=context.public_metadata())
    match = re.fullmatch(r'/api/v1/dataops/([A-Za-z0-9_-]{1,128})(/cancel)?', path)
    if match:
        execution = ds.Execution(principal, match[1])
        saved = _saved(execution, context)
        if method == 'POST' and match[2]:
            _keys(body, {'context'})
            context = _cancel_context(principal, context, json.loads(saved['context']))
            if _cancel_unknown(execution):
                # Do not invoke inspect's authorization-loss cancellation path
                # on uncertain work. Owner/context already matched above.
                raise QueryError('UNKNOWN_RECONCILIATION_REQUIRED', 409,
                    details=dict(envelope, operation=_cancel_observation(execution)))
            try:
                execution.cancel()
            except Exception as error:
                details = dict(envelope, operation=_cancel_observation(execution))
                if _cancel_unknown(execution):
                    raise QueryError('UNKNOWN_RECONCILIATION_REQUIRED', 409, details=details) from None
                if isinstance(error, QueryError):
                    raise QueryError(error.code, error.status, details=details) from None
                raise QueryError('SERVICE_UNAVAILABLE', 502, details=details) from None
            if _cancel_unknown(execution):
                raise QueryError('UNKNOWN_RECONCILIATION_REQUIRED', 409,
                    details=dict(envelope, operation=_cancel_observation(execution)))
            envelope['operation'] = _cancel_observation(execution)
        elif method == 'GET' and not match[2]:
            current_context(principal, context)
            envelope['operation'] = execution.inspect()
        else:
            raise QueryError('NOT_FOUND', 404)
        handler.respond(envelope)
        return True
    current_context(principal, context)
    if method == 'POST' and path in ('/api/v1/ingests', '/api/v1/ingests/preview'):
        _keys(body, {'context', 'filename', 'content_base64', 'mode'},
            {'destination', 'target', 'format', 'encoding', 'delimiter', 'schema_hints', 'key_columns',
             'batch_size', 'atomic_append', 'expected_preview_fingerprint'})
        options = {k: v for k, v in body.items() if k not in ('context', 'content_base64', 'expected_preview_fingerprint')}
        options['content'] = base64.b64decode(body['content_base64'], validate=True)
        for name in ('destination', 'target'):
            if name in options:
                options[name] = reference(options[name], locator=name == 'destination')
        if 'key_columns' in options and not isinstance(options['key_columns'], list):
            raise ValueError('key_columns array required')
        if path.endswith('/preview'):
            envelope['preview'] = manual_ingest.input_preview(context, **options)
        else:
            key = _key(handler)
            if 'expected_preview_fingerprint' in body:
                expected = body['expected_preview_fingerprint']
                if not isinstance(expected, str) or not re.fullmatch('[a-f0-9]{64}', expected):
                    raise ValueError('preview fingerprint required')
                if manual_ingest.preview_fingerprint(context, **options) != expected:
                    raise QueryError('PREVIEW_CHANGED', 409)
            envelope['operation'] = _operation(principal, context, 'manual-ingest', key,
                lambda: manual_ingest.ingest(principal, context, idempotency_key=key, **options))
    elif path == '/api/v1/wap/models':
        if method == 'POST':
            _keys(body, {'context', 'model_id', 'sql', 'sources', 'target', 'expected_version'}, {'min_rows', 'not_null'})
            options = {k: v for k, v in body.items() if k != 'context'}
            if 'not_null' in options and not isinstance(options['not_null'], list):
                raise ValueError('not_null array required')
            if not isinstance(options['sources'], dict):
                raise ValueError('source bindings required')
            options['sources'] = {k: reference(v) for k, v in options['sources'].items()}
            options['target'] = reference(options['target'], locator=set(options['target']) == {f.name for f in fields(registry.DestinationLocator)})
            envelope['model'] = manual_wap.save_model(principal, context, **options)
        elif 'model_id' in params:
            version = params.get('version', [None])[0]
            if version is not None and (not re.fullmatch(r'[1-9][0-9]*', version)):
                raise ValueError('positive version required')
            envelope['model'] = manual_wap.get_model(principal, context, model_id=params['model_id'][0],
                version=int(version) if version is not None else None)
        else:
            if 'version' in params:
                raise ValueError('model_id required')
            manual_wap._access(principal, context, write=True)
            with control_store.connect() as db:
                ids = [r[0] for r in db.execute('SELECT model_id FROM wap_models WHERE database_id=? ORDER BY model_id', (context.database_id,))]
            envelope['items'] = [manual_wap.get_model(principal, context, model_id=m) for m in ids]
    elif method == 'POST' and path in ('/api/v1/wap/build', '/api/v1/wap/audit', '/api/v1/wap/publish', '/api/v1/wap/rollback'):
        action = path.rsplit('/', 1)[1]
        names = {'build': {'model_id', 'version'}, 'audit': {'build_id'},
            'publish': {'build_id', 'audit_id'}, 'rollback': {'publication_id'}}[action]
        _keys(body, names | {'context'})
        key = _key(handler)
        envelope['operation'] = _operation(principal, context, 'manual-wap-' + action, key,
            lambda: getattr(manual_wap, action)(principal, context, key=key, **{n: body[n] for n in names}))
    elif method == 'GET' and re.fullmatch(r'/api/v1/wap/(builds|audits|publications)/[A-Za-z0-9_-]{1,128}', path):
        kind, artifact_id = path.split('/')[-2:]
        name = {'builds': 'build', 'audits': 'audit', 'publications': 'publication'}[kind]
        envelope[name] = getattr(manual_wap, 'get_' + name)(principal, context, **{name + '_id': artifact_id})
    elif method == 'POST' and path == '/api/v1/table-exports':
        _keys(body, {'context', 'source', 'format'})
        key = _key(handler)
        envelope['operation'] = _operation(principal, context, 'manual-table-export', key,
            lambda: manual_export.export_table(principal, context, key=key, source=reference(body['source']), format=body['format']))
    elif method == 'GET' and re.fullmatch(r'/api/v1/table-exports/[A-Za-z0-9_-]{1,128}(/content)?', path):
        export_id = path.split('/')[4]
        if path.endswith('/content'):
            artifact = manual_export.read_export(principal, context, export_id=export_id)
            raw, receipt = artifact['content'], artifact['receipt']
            handler.send_response(200)
            handler.send_header('Content-Type', 'text/csv; charset=utf-8' if receipt['format'] == 'csv' else 'application/x-ndjson; charset=utf-8')
            handler.send_header('Content-Disposition', 'attachment; filename="table-export.' + receipt['format'] + '"')
            handler.send_header('Content-Length', str(len(raw)))
            handler.end_headers()
            handler.wfile.write(raw)
            return True
        envelope['receipt'] = manual_export.get_export(principal, context, export_id=export_id)
    else:
        raise QueryError('NOT_FOUND', 404)
    handler.respond(envelope)
    return True


def _operation(principal, context, route, key, run):
    try:
        return run()
    except Exception as error:
        if isinstance(error, (admission.AdmissionDenied, admission.AdmissionConflict, admission.GateTimeout)):
            error = QueryError(error.code, error.status)
        # Preserve status/code; expose only the actor's authorized durable record.
        try:
            with control_store.connect() as db:
                row = db.execute('SELECT id FROM dataops_operations WHERE actor=? AND route=? AND idempotency_key=?',
                    (principal.principal_id, route, key)).fetchone()
            if row:
                execution = ds.Execution(principal, row[0])
                _saved(execution, context)
                observation = execution.inspect()
                details = dict(context=context.public_metadata(), operation=observation)
                if isinstance(error, QueryError):
                    error.details = dict(error.details, context=context.public_metadata(), operation=observation)
                elif isinstance(error, ValueError):
                    raise QueryError('INVALID_INPUT', 400, details=details) from None
                else:
                    raise QueryError('SERVICE_UNAVAILABLE', 502, details=details) from None
        except QueryError as observed:
            if observed.code in ('INVALID_INPUT', 'SERVICE_UNAVAILABLE') and 'operation' in observed.details:
                raise
        raise error
