"""Database Jobs: frozen manual runs over the existing DataOps boundary."""
import base64
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import re
import secrets
import sqlite3
import threading
import uuid

import control_store
import data_management as registry
import dataops_service as ds
import dataops_routes as routes
import jobs_resident as legacy
import manual_export
import manual_ingest
import manual_wap
import query_service as queries
from query_context import QueryError, revalidate_admission
import warehouse_admission as admission
import warehouse_resident as warehouse

_live = {}
_TERMINAL = {'succeeded', 'failed', 'canceled'}


def _json(value):
    return ds._json(value)


def _access(principal, context):
    ds._validated_context(principal, context)
    if context.selected_role not in ('admin', 'data_engineer'):
        raise QueryError('SQL_PERMISSION_DENIED', 403)


def _key(key):
    if not isinstance(key, str) or not key.strip() or len(key) > 256:
        raise ValueError('idempotency key required')


def _saved(row, principal, context, *, run=False, refresh_binding=False):
    if row is None or row['owner'] != principal.principal_id or row['selected_role'] != context.selected_role:
        raise QueryError('JOB_NOT_FOUND', 404)
    saved, current = json.loads(row['context']), asdict(context)
    # Definitions bind resources, not a past authorization epoch.
    ignored = set() if run else {'grant_revision', 'generation_id'}
    if refresh_binding:
        ignored.add('binding_revision')
    if any(saved[k] != current[k] for k in current.keys() - ignored):
        raise QueryError('CONTEXT_MISMATCH', 409)


def _definition(row):
    return dict(id=row['id'], name=row['name'], owner=row['owner'], selected_role=row['selected_role'],
        revision=row['revision'], context=json.loads(row['context']),
        definition=json.loads(row['definition']), created_at=row['created_at'], updated_at=row['updated_at'])


def _job(db, job_id, principal, context, *, refresh_binding=False):
    if not isinstance(job_id, str) or not job_id.strip() or len(job_id) > 128:
        raise ValueError('job_id must be a nonempty string')
    row = db.execute('SELECT * FROM db_jobs WHERE id=?', (job_id,)).fetchone()
    _saved(row, principal, context, refresh_binding=refresh_binding)
    return row


def _parameters(definition, arguments):
    specs = definition.get('parameters', {})
    if not isinstance(specs, dict):
        raise ValueError('parameters must be an object')
    values = {}
    if arguments is not None and (not isinstance(arguments, dict) or arguments.keys() - specs.keys()):
        raise ValueError('unexpected arguments')
    for name, spec in specs.items():
        if (not isinstance(name, str) or not legacy.PARAMETER.fullmatch(name) or not isinstance(spec, dict)
                or set(spec) - {'type', 'required', 'default'} or
                spec.get('type') not in ('string', 'integer', 'boolean', 'date') or
                type(spec.get('required', False)) is not bool):
            raise ValueError('invalid parameter declaration')
        required = spec.get('required', False)
        if 'default' in spec:
            legacy._typed(spec['default'], spec['type'], not required)
        if arguments is None:
            value = spec.get('default', {'string': '', 'integer': 0, 'boolean': False, 'date': '2000-01-01'}[spec['type']])
        else:
            if required and name not in arguments and 'default' not in spec:
                raise ValueError('missing required parameter')
            value = arguments.get(name, spec.get('default'))
        values[name] = legacy._typed(value, spec['type'], not required)
    return values


def _target(principal, context, value, *, locator=False, write=False):
    target = routes.reference(value, locator=locator)
    metadata = manual_wap._Metadata(principal, context)
    if locator:
        metadata._locator(target, write=write)
    else:
        metadata._ref(target, write=write)
    return target


def _validate(principal, context, definition, arguments=None):
    if (not isinstance(definition, dict) or set(definition) - {'name', 'description', 'parameters', 'steps'}
            or not isinstance(definition.get('name'), str)
            or not re.fullmatch('[a-z][a-z0-9_-]{0,63}', definition['name'])
            or not isinstance(definition.get('description', ''), str)):
        raise ValueError('invalid job definition')
    values = _parameters(definition, arguments)
    steps = definition.get('steps')
    if not isinstance(steps, list) or not 1 <= len(steps) <= 50:
        raise ValueError('one to fifty steps required')
    names, prepared = set(), []
    for step in steps:
        if (not isinstance(step, dict) or not isinstance(step.get('name'), str)
                or not step['name'].strip() or len(step['name']) > 128 or step['name'] in names):
            raise ValueError('distinct step names required')
        names.add(step['name'])
        action = step.get('action')
        fields = {'sql': ({'sql', 'mode'}, set()), 'alert': ({'sql'}, {'mode'}),
            'ingest': ({'input', 'mode'}, {'target', 'destination', 'format', 'encoding', 'delimiter',
                'schema_hints', 'key_columns', 'batch_size', 'atomic_append'}),
            'wap_refresh': ({'model_id', 'version'}, set()),
            'table_export': ({'source', 'format'}, set())}
        if not isinstance(action, str) or action not in fields:
            raise ValueError('unsupported Job action')
        required, optional = fields[action]
        routes._keys(step, required | {'name', 'action'}, optional)
        item = dict(step)
        if action in ('sql', 'alert'):
            if not isinstance(step['sql'], str) or not step['sql'].strip():
                raise ValueError('SQL required')
            sql, _ = legacy._render(step['sql'], definition.get('parameters', {}), values)
            mode = step.get('mode', 'read-only')
            if action == 'alert' and mode != 'read-only':
                raise ValueError('alert must be read-only')
            warehouse.check_sql_policy(sql, mode, context.selected_role, context=context, database_only=True)
            item.update(sql=sql, mode=mode)
        else:
            if context.selected_role != 'admin':
                raise QueryError('SQL_PERMISSION_DENIED', 403)
            if action == 'ingest':
                if not isinstance(step['input'], str) or not legacy.PARAMETER.fullmatch(step['input']):
                    raise ValueError('named upload required')
                create = step['mode'] == 'create'
                if step['mode'] not in ('create', 'replace', 'append', 'upsert'):
                    raise ValueError('invalid ingest mode')
                field, other = ('destination', 'target') if create else ('target', 'destination')
                if field not in step or other in step:
                    raise ValueError('exact ingest target required')
                target = _target(principal, context, step[field], locator=create, write=True)
                if create:
                    manual_wap._Metadata(principal, context)._absent(target)
                if ('batch_size' in step and (type(step['batch_size']) is not int or step['batch_size'] < 1)
                        or 'atomic_append' in step and type(step['atomic_append']) is not bool
                        or 'schema_hints' in step and not isinstance(step['schema_hints'], dict)
                        or 'key_columns' in step and (not isinstance(step['key_columns'], list)
                            or any(not isinstance(k, str) for k in step['key_columns'])
                            or len(set(step['key_columns'])) != len(step['key_columns']))):
                    raise ValueError('invalid ingest options')
            elif action == 'table_export':
                if step['format'] not in ('csv', 'jsonl'):
                    raise ValueError('unsupported export format')
                _target(principal, context, step['source'])
            else:
                if not isinstance(step['model_id'], str) or type(step['version']) is not int or step['version'] < 1:
                    raise ValueError('exact WAP model version required')
                model = manual_wap.get_model(principal, context, model_id=step['model_id'], version=step['version'])
                head = manual_wap._head(context, step['model_id'])
                if head['version'] != step['version']:
                    raise QueryError('MODEL_VERSION_CHANGED', 409)
                for ref in model['sources'].values():
                    _target(principal, context, ref)
                target = manual_wap._target(model, head)
                metadata = manual_wap._Metadata(principal, context)
                if type(target) is registry.ResourceRef:
                    metadata._ref(target, write=True)
                else:
                    metadata._locator(target, write=True)
                    metadata._absent_model(target)
        prepared.append(item)
    return values, prepared


def _request(db, principal, route, key, payload):
    row = db.execute('SELECT payload,response FROM db_job_requests WHERE actor=? AND route=? AND idempotency_key=?',
        (principal.principal_id, route, key)).fetchone()
    if row and row['payload'] != payload:
        raise QueryError('IDEMPOTENCY_CONFLICT', 409)
    return json.loads(row['response']) if row else None


def _receipt(db, principal, route, key, payload, response):
    db.execute('INSERT INTO db_job_requests VALUES (?,?,?,?,?)',
        (principal.principal_id, route, key, payload, _json(response)))


def save_job(principal, context, *, expected_revision, definition, key, job_id=None):
    _access(principal, context)
    _key(key)
    if (type(expected_revision) is not int or expected_revision < 0 or bool(job_id) != (expected_revision > 0)
            or job_id is not None and (not isinstance(job_id, str) or not job_id.strip() or len(job_id) > 128)):
        raise ValueError('creation revision zero; updates require outer job_id and positive revision')
    definition = json.loads(_json(definition))
    payload = _json(dict(context=asdict(context), expected_revision=expected_revision, definition=definition, job_id=job_id))
    with control_store.connect() as db:
        replay = _request(db, principal, 'save', key, payload)
        if replay:
            _job(db, replay['id'], principal, context)
            return replay
    _validate(principal, context, definition)
    try:
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            revalidate_admission(db, principal, context)
            replay = _request(db, principal, 'save', key, payload)
            if replay:
                _job(db, replay['id'], principal, context)
                return replay
            now = admission._now()
            if job_id is not None:
                row = _job(db, job_id, principal, context, refresh_binding=True)
                if row['revision'] != expected_revision:
                    raise QueryError('REVISION_CONFLICT', 409)
                db.execute('UPDATE db_jobs SET name=?,revision=revision+1,context=?,definition=?,updated_at=? WHERE id=?',
                    (definition['name'], _json(asdict(context)), _json(definition), now, job_id))
            else:
                job_id = str(uuid.uuid4())
                db.execute('INSERT INTO db_jobs VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (job_id, context.database_id, definition['name'], principal.principal_id, context.selected_role,
                     1, _json(asdict(context)), _json(definition), now, now))
            result = _definition(_job(db, job_id, principal, context))
            _receipt(db, principal, 'save', key, payload, result)
            return result
    except sqlite3.IntegrityError:
        raise QueryError('JOB_NAME_CONFLICT', 409) from None


def get_job(principal, context, job_id):
    _access(principal, context)
    with control_store.connect() as db:
        return _definition(_job(db, job_id, principal, context))


def list_jobs(principal, context):
    _access(principal, context)
    with control_store.connect() as db:
        return [_definition(r) for r in db.execute('SELECT * FROM db_jobs WHERE database_id=? AND owner=? AND selected_role=? ORDER BY name',
            (context.database_id, principal.principal_id, context.selected_role))]


def _decode_upload(name, value):
    if (not isinstance(name, str) or not legacy.PARAMETER.fullmatch(name)
            or not isinstance(value, dict)):
        raise ValueError('named upload object required')
    routes._keys(value, {'filename', 'content_base64'})
    if (not isinstance(value['filename'], str) or not value['filename'].strip()
            or not isinstance(value['content_base64'], str)):
        raise ValueError('upload filename and base64 must be strings')
    return base64.b64decode(value['content_base64'], validate=True)


def _uploads(context, steps, inputs):
    required = {s['input'] for s in steps if s['action'] == 'ingest'}
    if not isinstance(inputs, dict) or set(inputs) != required:
        raise ValueError('exact named uploads required')
    captured, fingerprints = {}, {}
    for name, value in inputs.items():
        raw = _decode_upload(name, value)
        for step in steps:
            if step['action'] == 'ingest' and step['input'] == name:
                options = {k: v for k, v in step.items() if k not in ('name', 'action', 'input')}
                for field in ('target', 'destination'):
                    if field in options:
                        options[field] = routes.reference(options[field], locator=field == 'destination')
                # Input-only validation and typed parsing before acceptance.
                manual_ingest.input_preview(context, filename=value['filename'], content=raw, **options)
        captured[name] = dict(filename=value['filename'], content_base64=base64.b64encode(raw).decode('ascii'))
        fingerprints[name] = dict(filename=value['filename'], sha256=hashlib.sha256(raw).hexdigest(), length=len(raw))
    return captured, fingerprints


def _run(db, run_id, principal, context):
    row = db.execute('SELECT * FROM db_job_runs WHERE id=?', (run_id,)).fetchone()
    _saved(row, principal, context, run=True)
    return row


def _public(db, row, *, details=True):
    definition = json.loads(row['definition'])
    steps = []
    for step in db.execute('SELECT * FROM db_job_steps WHERE run_id=? ORDER BY position', (row['id'],)):
        ops = [r[0] for r in db.execute('SELECT operation_id FROM db_job_operations WHERE run_id=? AND position=? ORDER BY rowid',
            (row['id'], step['position']))] if details else []
        children = []
        for operation_id in ops:
            children.extend(dict(r) for r in db.execute('''SELECT q.id,q.state,
                COALESCE((SELECT execution_closure FROM warehouse_admissions a
                    WHERE a.kind='query' AND a.subject_id=q.id),'') AS execution_closure FROM dataops_queries d
                JOIN query_records q ON q.id=d.query_id WHERE d.operation_id=? ORDER BY q.created_at,q.id''', (operation_id,)))
        steps.append(dict(name=step['name'], action=step['action'], status=step['status'], operation_ids=ops,
            summary=json.loads(step['summary']) if details and step['summary'] else None,
            queries=[dict(c, context=json.loads(row['context'])) for c in children]))
    return dict(id=row['id'], job_id=row['job_id'], job_name=definition['name'], owner=row['owner'],
        selected_role=row['selected_role'], revision=row['revision'], status=row['status'],
        context=json.loads(row['context']), steps=steps, created_at=row['created_at'],
        started_at=row['started_at'], finished_at=row['finished_at'])


@contextmanager
def _input_validation():
    try:
        yield
    except ValueError as error:
        if isinstance(error, ds.inventory.InventoryError):
            raise
        raise QueryError('JOB_INPUT_REJECTED', 400, details={'admission': 'NOT_ATTEMPTED'}) from None


def submit_run(principal, context, job_id, *, expected_revision, arguments, key, inputs=None):
    _access(principal, context)
    _key(key)
    with _input_validation():
        if type(expected_revision) is not int or expected_revision < 1 or not isinstance(arguments, dict):
            raise ValueError('positive expected revision and arguments object required')
        if not isinstance(job_id, str) or not job_id.strip() or len(job_id) > 128:
            raise ValueError('job_id must be a nonempty string')
    with control_store.connect() as db:
        job = _job(db, job_id, principal, context)
        definition = json.loads(job['definition'])
    # Replay fingerprints do not depend on a subsequently edited definition.
    with _input_validation():
        raw_inputs = inputs if inputs is not None else {}
        if not isinstance(raw_inputs, dict):
            raise ValueError('named uploads required')
        fingerprints, validated_inputs = {}, {}
        for name, value in raw_inputs.items():
            raw = _decode_upload(name, value)
            validated_inputs[name] = dict(value)
            fingerprints[name] = dict(filename=value['filename'], sha256=hashlib.sha256(raw).hexdigest(), length=len(raw))
        raw_inputs = validated_inputs
        arguments = json.loads(_json(arguments))
        payload = _json(dict(context=asdict(context), expected_revision=expected_revision, arguments=arguments, inputs=fingerprints))
    route = 'run/' + job_id
    with control_store.connect() as db:
        replay = _request(db, principal, route, key, payload)
        if replay:
            return _public(db, _run(db, replay, principal, context))
    if job['revision'] != expected_revision:
        raise QueryError('REVISION_CONFLICT', 409)
    with _input_validation():
        captured, _ = _uploads(context, definition['steps'], raw_inputs)
        values, steps = _validate(principal, context, definition, arguments)
    frozen = queries._capture_dataops(principal, context)
    lock = legacy._lock()
    if lock is None:
        # The same key may have won in a different process during capture.
        with control_store.connect() as db:
            replay = _request(db, principal, route, key, payload)
            if replay:
                return _public(db, _run(db, replay, principal, context))
        raise QueryError('JOB_BUSY', 409)
    try:
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            revalidate_admission(db, principal, context)
            legacy._lost(db)
            replay = _request(db, principal, route, key, payload)
            if replay:
                lock.close()
                return _public(db, _run(db, replay, principal, context))
            job = _job(db, job_id, principal, context)
            if job['revision'] != expected_revision:
                raise QueryError('REVISION_CONFLICT', 409)
            if (db.execute("SELECT 1 FROM db_job_runs WHERE status IN ('queued','running','unknown')").fetchone()
                    or db.execute("SELECT 1 FROM runs WHERE status IN ('queued','running','unknown')").fetchone()):
                raise QueryError('UNKNOWN_RECONCILIATION_REQUIRED', 409)
            run_id, ref, now = str(uuid.uuid4()), secrets.token_urlsafe(32), admission._now()
            import db_schedules
            db_schedules._admit(db, 'job', job_id, expected_revision, run_id, context)
            parent = admission.admit(db, warehouse_id=context.warehouse_id, actor=principal.principal_id,
                kind='job', subject_id=run_id, generation_id=context.generation_id, database_id=context.database_id)
            db.execute('''INSERT INTO db_job_runs
                (id,job_id,owner,selected_role,revision,context,definition,arguments,inputs,backend_username,
                 credential_snapshot_ref,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,'queued',?)''',
                (run_id, job_id, principal.principal_id, context.selected_role, expected_revision,
                 _json(asdict(context)), _json(definition), _json(values), _json(captured),
                 frozen.headers['X-Trino-User'], ref, now))
            db.executemany('INSERT INTO db_job_steps VALUES (?,?,?,?,?,NULL)',
                [(run_id, i, s['name'], s['action'], 'pending') for i, s in enumerate(steps, 1)])
            _receipt(db, principal, route, key, payload, run_id)
            result = _public(db, _run(db, run_id, principal, context))
        _live[run_id] = (principal, context, parent, frozen, ref, steps, captured, lock)
        try:
            _start(run_id)
        except BaseException:
            _live.pop(run_id, None)
            raise
        return result
    except BaseException:
        lock.close()
        raise


def _start(run_id):
    threading.Thread(target=_worker, args=(run_id,), name='db-job-' + run_id[:8], daemon=True).start()


def _operation_result(result):
    if result['state'] != 'FINISHED':
        raise QueryError('JOB_STEP_UNRESOLVED' if result['state'] == 'UNKNOWN' else 'JOB_STEP_FAILED', 409)
    return result.get('result') or {}


def _execute(principal, context, run_id, position, step, inputs):
    action, key = step['action'], run_id + ':' + str(position)
    if action in ('sql', 'alert'):
        execution = ds.begin(principal, context, route='job-sql', idempotency_key=key,
            payload=dict(sql=step['sql'], mode=step['mode'], action=action))
        if execution.replayed:
            raise QueryError('OPERATION_OBSERVATION_ONLY', 409)
        summary = execution._job_sql(step['sql'], mode=step['mode'], alert=action == 'alert')
        execution.complete(summary)
        if action == 'alert':
            import db_schedules
            db_schedules.record_alert(principal, context, run_id, position, step, summary['triggered'])
        return summary
    if action == 'ingest':
        options = {k: v for k, v in step.items() if k not in ('name', 'action', 'input')}
        for field in ('destination', 'target'):
            if field in options:
                options[field] = routes.reference(options[field], locator=field == 'destination')
        upload = inputs[step['input']]
        result = manual_ingest.ingest(principal, context, idempotency_key=key,
            filename=upload['filename'], content=base64.b64decode(upload['content_base64']), **options)
        receipt = _operation_result(result)
        return {k: receipt[k] for k in ('committed_rows', 'partial', 'target') if k in receipt}
    if action == 'table_export':
        result = manual_export.export_table(principal, context, key=key,
            source=routes.reference(step['source']), format=step['format'])
        receipt = _operation_result(result)
        return {k: receipt[k] for k in ('export_id', 'row_count', 'format') if k in receipt}
    built = _operation_result(manual_wap.build(principal, context, key=key + ':build',
        model_id=step['model_id'], version=step['version']))
    audited = _operation_result(manual_wap.audit(principal, context, key=key + ':audit', build_id=built['build_id']))
    if not audited['audit']['passed']:
        raise QueryError('WAP_AUDIT_FAILED', 409)
    published = _operation_result(manual_wap.publish(principal, context, key=key + ':publish',
        build_id=built['build_id'], audit_id=audited['audit_id']))
    return dict(publication_id=published['publication_id'])


def _descendants(db, run_id):
    return list(db.execute('''WITH RECURSIVE tree AS (
        SELECT * FROM warehouse_admissions WHERE kind='job' AND subject_id=?
        UNION ALL SELECT a.* FROM warehouse_admissions a JOIN tree t ON a.parent_id=t.id)
        SELECT * FROM tree''', (run_id,)))


def _settle(db, run_id, desired, *, failure_code=None):
    row = db.execute('SELECT * FROM db_job_runs WHERE id=?', (run_id,)).fetchone()
    if row['status'] in _TERMINAL:
        return
    descendants = _descendants(db, run_id)
    unresolved = any(admission.execution_unresolved(r) for r in descendants[1:])
    status = 'unknown' if unresolved else ('canceled' if row['cancel_requested'] else desired)
    if desired == 'failed':
        code = failure_code or ('BACKEND_OUTCOME_UNKNOWN' if unresolved else 'JOB_STEP_FAILED')
        db.execute("UPDATE db_job_steps SET summary=? WHERE run_id=? AND status='running'",
            (_json(dict(code=code)), run_id))
    parent = admission.get_for_subject(db, kind='job', subject_id=run_id,
        warehouse_id=json.loads(row['context'])['warehouse_id'])
    admission.finish(db, parent, state={'succeeded': 'FINISHED', 'failed': 'FAILED',
        'canceled': 'CANCELED', 'unknown': 'UNKNOWN'}[status])
    db.execute('UPDATE db_job_runs SET status=?,finished_at=? WHERE id=?', (status, admission._now(), run_id))
    db.execute("UPDATE db_job_steps SET status=? WHERE run_id=? AND status='running'",
        ('unknown' if status == 'unknown' else status, run_id))
    db.execute("UPDATE db_job_steps SET status='skipped' WHERE run_id=? AND status='pending'", (run_id,))


def _worker(run_id):
    with admission.accepted(_live[run_id][2]):
        _worker_accepted(run_id)


def _worker_accepted(run_id):
    principal, context, parent, frozen, ref, steps, inputs, lock = _live[run_id]
    outcome = 'failed'
    failure_code = None
    try:
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            ds._check_job(db, run_id)
            db.execute("UPDATE db_job_runs SET status='running',started_at=? WHERE id=?", (admission._now(), run_id))
        for position, step in enumerate(steps, 1):
            _access(principal, context)
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                ds._check_job(db, run_id)
                db.execute("UPDATE db_job_steps SET status='running' WHERE run_id=? AND position=?", (run_id, position))
            with ds._job_scope(principal, context, parent, frozen, ref, run_id, position):
                summary = _execute(principal, context, run_id, position, step, inputs)
            with control_store.connect() as db:
                db.execute("UPDATE db_job_steps SET status='succeeded',summary=? WHERE run_id=? AND position=?",
                    (_json(summary), run_id, position))
        outcome = 'succeeded'
    except Exception as error:
        # Settle accepted operations, including after grant loss, without retry.
        if (isinstance(error, QueryError) and isinstance(error.code, str)
                and re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', error.code)):
            failure_code = error.code
        with control_store.connect() as db:
            ids = [r[0] for r in db.execute('SELECT operation_id FROM db_job_operations WHERE run_id=?', (run_id,))]
        for operation_id in ids:
            execution = ds.Execution(principal, operation_id)
            if execution._row()['state'] in ('ACTIVE', 'CANCELING'):
                try:
                    execution.cancel()
                except Exception:
                    pass
    finally:
        try:
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                _settle(db, run_id, outcome, failure_code=failure_code)
        finally:
            _live.pop(run_id, None)
            lock.close()


def _lost(db):
    """Called only while holding the same global Jobs flock as the legacy runner."""
    for row in db.execute("SELECT id FROM db_job_runs WHERE status IN ('queued','running')").fetchall():
        run_id = row['id']
        for child in reversed(_descendants(db, run_id)):
            if child['state'] == 'ACTIVE':
                admission.finish(db, admission._handle(child), state='UNKNOWN')
                if child['kind'] == 'query':
                    registry.set_query_state(db, child['subject_id'], 'UNKNOWN')
                elif child['kind'] == 'dataops':
                    db.execute("UPDATE dataops_operations SET state='UNKNOWN' WHERE id=? AND state IN ('ACTIVE','CANCELING')",
                        (child['subject_id'],))
        db.execute("UPDATE db_job_runs SET status='unknown',finished_at=? WHERE id=?", (admission._now(), run_id))
        db.execute("UPDATE db_job_steps SET status='unknown' WHERE run_id=? AND status='running'", (run_id,))
        db.execute("UPDATE db_job_steps SET status='skipped' WHERE run_id=? AND status='pending'", (run_id,))


def _recover():
    lock = legacy._lock()
    if lock is not None:
        try:
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                _lost(db)
        finally:
            lock.close()


def get_run(principal, context, run_id):
    _access(principal, context)
    _recover()
    with control_store.connect() as db:
        return _public(db, _run(db, run_id, principal, context))


def list_runs(principal, context, job_id=None):
    _access(principal, context)
    _recover()
    with control_store.connect() as db:
        if job_id is not None:
            _job(db, job_id, principal, context)
        return [_public(db, row) for row in db.execute('''SELECT * FROM db_job_runs
            WHERE owner=? AND selected_role=? AND context=? AND (? IS NULL OR job_id=?) ORDER BY created_at DESC,id''',
            (principal.principal_id, context.selected_role, _json(asdict(context)), job_id, job_id))]


def cancel_run(principal, context, run_id):
    # Original exact context and owner suffice for cleanup with the old pin.
    ds._cancel_account(principal)
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = _run(db, run_id, principal, context)
        if row['status'] not in _TERMINAL:
            db.execute('UPDATE db_job_runs SET cancel_requested=1 WHERE id=?', (run_id,))
        ids = [r[0] for r in db.execute('SELECT operation_id FROM db_job_operations WHERE run_id=?', (run_id,))]
    for operation_id in ids:
        execution = ds.Execution(principal, operation_id)
        if execution._row()['state'] in ('ACTIVE', 'CANCELING'):
            try:
                execution.cancel()
            except Exception:
                pass
    _recover()
    with control_store.connect() as db:
        # A worker owns parent settlement; cancellation cannot declare a lost
        # worker successful or release an uncertain parent.
        row = _run(db, run_id, principal, context)
        return dict(id=run_id, status=row['status'])


def reconcile_run(principal, context, run_id):
    _access(principal, context)
    with control_store.connect() as db:
        row = _run(db, run_id, principal, context)
    lock = legacy._lock()
    if lock is None:
        raise QueryError('JOB_BUSY', 409)
    try:
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            _lost(db)
            row = _run(db, run_id, principal, context)
            if row['status'] != 'unknown':
                raise QueryError('UNKNOWN_RECONCILIATION_REQUIRED', 409)
            ids = [r[0] for r in db.execute('SELECT operation_id FROM db_job_operations WHERE run_id=?', (run_id,))]
        for operation_id in ids:
            execution = ds.Execution(principal, operation_id)
            snapshot = execution.reconcile_queries()
            if snapshot['state'] == 'UNKNOWN':
                execution.reconcile_failure()
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            revalidate_admission(db, principal, context)
            descendants = _descendants(db, run_id)
            if any(admission.execution_unresolved(r) for r in descendants[1:]):
                raise QueryError('UNRESOLVED_CHILD_QUERIES', 409)
            evidence = hashlib.sha256(_json([dict(r) for r in descendants]).encode()).hexdigest()
            parent = admission.get_for_subject(db, kind='job', subject_id=run_id, warehouse_id=context.warehouse_id)
            admission.finish(db, parent, state='FAILED', evidence_ref=evidence)
            db.execute("UPDATE db_job_runs SET status='failed',finished_at=? WHERE id=?", (admission._now(), run_id))
            db.execute("UPDATE db_job_steps SET status='failed' WHERE run_id=? AND status='unknown'", (run_id,))
            return _public(db, _run(db, run_id, principal, context))
    finally:
        lock.close()
