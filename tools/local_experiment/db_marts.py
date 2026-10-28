"""DB-owned manual Marts over the P3 WAP boundary. No scheduler or legacy state."""
from dataclasses import asdict
import json
import re
import secrets
import threading
import uuid

import control_store
import data_management as registry
import dataops_routes as common
import dataops_service as ds
import manual_wap as wap
import query_service as queries
from query_context import QueryError, ResolvedQueryContext, revalidate_admission
import warehouse_admission as admission


_live = {}
_lock = threading.RLock()


def _access(principal, context):
    metadata = wap._access(principal, context, write=True, admin=True)
    schema = metadata._schema(context.schema_id)
    if schema.sql_name != 'marts' or schema.purpose != 'marts':
        raise QueryError('MARTS_SCHEMA_REQUIRED', 409)
    return metadata


def _mart(db, principal, context, ident, revision=None):
    row = db.execute('SELECT * FROM db_marts WHERE id=?', (ident,)).fetchone()
    if row is None or row['owner'] != principal.principal_id or row['database_id'] != context.database_id:
        raise QueryError('MART_NOT_FOUND', 404)
    saved = json.loads(row['context'])
    # Authorization epoch and generation belong to each run, not a definition.
    for k in ('database_id', 'schema_id', 'warehouse_id', 'selected_role', 'sql_catalog', 'sql_schema'):
        if saved[k] != asdict(context)[k]:
            raise QueryError('CONTEXT_MISMATCH', 409)
    if revision is not None and row['revision'] != revision:
        raise QueryError('MART_REVISION_CONFLICT', 409)
    return row


def _public(row):
    return dict(json.loads(row['record']), id=row['id'], database_id=row['database_id'],
        owner=row['owner'], revision=row['revision'], context=json.loads(row['context']),
        definition=json.loads(row['definition']))


def _request(db, principal, route, key, payload):
    if not isinstance(key, str) or not key.strip() or len(key) > 256:
        raise ValueError('idempotency key required')
    row = db.execute('SELECT * FROM db_mart_requests WHERE actor=? AND route=? AND idempotency_key=?',
        (principal.principal_id, route, key)).fetchone()
    if row:
        if row['payload'] != payload:
            raise QueryError('IDEMPOTENCY_CONFLICT', 409)
        return json.loads(row['response'])


def _receipt(db, principal, route, key, payload, response):
    db.execute('INSERT INTO db_mart_requests VALUES (?,?,?,?,?)',
        (principal.principal_id, route, key, payload, ds._json(response)))


def _revision(value, create=False):
    if type(value) is not int or value < (0 if create else 1):
        raise ValueError('explicit Mart revision required')


def _definition(principal, context, definition, current=None):
    if not isinstance(definition, dict):
        raise ValueError('Mart definition required')
    common._keys(definition, {'name', 'model_id', 'version', 'source_refs', 'target'})
    definition = json.loads(ds._json(definition))
    if not isinstance(definition['name'], str) or not re.fullmatch('[a-z][a-z0-9_-]{0,47}', definition['name']):
        raise ValueError('invalid Mart name')
    if type(definition['version']) is not int or definition['version'] < 1:
        raise ValueError('explicit model version required')
    model = wap.get_model(principal, context, model_id=definition['model_id'], version=definition['version'])
    if wap._head(context, model['model_id'])['version'] != model['version']:
        raise QueryError('MODEL_VERSION_CHANGED', 409)
    metadata = _access(principal, context)
    sources = {ds._json(r): r for r in model['sources'].values()}
    if not isinstance(definition['source_refs'], list):
        raise ValueError('source_refs list required')
    watched = {ds._json(asdict(common.reference(r))): r for r in definition['source_refs']}
    if watched and set(watched) != set(sources):
        raise QueryError('WATCH_SOURCES_MISMATCH', 409)
    definition['source_refs'] = [sources[k] for k in sorted(watched)]
    for ref in sources.values():
        with control_store.connect() as db:
            obj = registry.get_object(db, ref['object_id'])
            if obj is None or not obj.current or obj.kind != 'TABLE':
                raise QueryError('STALE_RESOURCE', 409)
        metadata._ref(registry.ResourceRef(**ref))
    target = common.reference(definition['target'], locator='object_id' not in definition['target'])
    if target.schema_id != context.schema_id or target.database_id != context.database_id:
        raise QueryError('MARTS_SCHEMA_REQUIRED', 409)
    materialized = current and current.get('current_target_ref')
    if materialized and asdict(target) != materialized:
        raise QueryError('MATERIALIZED_TARGET_REQUIRED', 409)
    head = wap._head(context, model['model_id'])
    bound = wap._target(model, head)
    if asdict(bound) != asdict(target) or type(bound) is not type(target):
        raise QueryError('TARGET_CHANGED', 409)
    if type(target) is registry.ResourceRef:
        metadata._ref(target)
    else:
        metadata._absent_model(target)
    return definition, model


def save_mart(principal, context, *, key, expected_revision, definition, mart_id=None):
    _access(principal, context)
    _revision(expected_revision, create=True)
    if (mart_id is None) != (expected_revision == 0):
        raise ValueError('create requires revision zero; update requires Mart ID')
    route = '/api/v1/marts' + ('/' + mart_id if mart_id else '')
    payload = ds._json(dict(context=asdict(context), expected_revision=expected_revision, definition=definition))
    with _lock:
        with control_store.connect() as db:
            old = _public(_mart(db, principal, context, mart_id)) if mart_id else None
            replay = _request(db, principal, route, key, payload)
            if replay:
                return _public(_mart(db, principal, context, replay['mart_id']))
        normalized, model = _definition(principal, context, definition, old)
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            revalidate_admission(db, principal, context)
            replay = _request(db, principal, route, key, payload)
            if replay:
                return _public(_mart(db, principal, context, replay['mart_id']))
            if mart_id:
                row = _mart(db, principal, context, mart_id, expected_revision)
                old = _public(row)
                _idle(db, mart_id, old)
            else:
                mart_id = str(uuid.uuid4())
            head = db.execute('SELECT version FROM wap_models WHERE database_id=? AND model_id=?',
                (context.database_id, model['model_id'])).fetchone()
            if head is None or head['version'] != model['version']:
                raise QueryError('MODEL_VERSION_CHANGED', 409)
            record = dict(selected_role='admin', current_target_ref=None, last_success_at=None,
                last_success_baseline=None, reconciliation_required=False, blocked_run_id=None,
                created_at=admission._now()) if old is None else json.loads(row['record'])
            record.update(updated_at=admission._now(), quality=dict(min_rows=model['min_rows'], not_null=model['not_null']))
            if old is None:
                db.execute('INSERT INTO db_marts VALUES (?,?,?,?,?,?,?)', (mart_id, context.database_id,
                    principal.principal_id, 1, ds._json(asdict(context)), ds._json(normalized), ds._json(record)))
            else:
                db.execute('UPDATE db_marts SET revision=revision+1,context=?,definition=?,record=? WHERE id=?',
                    (ds._json(asdict(context)), ds._json(normalized), ds._json(record), mart_id))
            _receipt(db, principal, route, key, payload, dict(mart_id=mart_id))
            return _public(_mart(db, principal, context, mart_id))


def _idle(db, mart_id, mart):
    if mart['reconciliation_required']:
        raise QueryError('MART_RECONCILIATION_REQUIRED', 409)
    if db.execute("SELECT 1 FROM db_mart_runs WHERE mart_id=? AND json_extract(record,'$.status') IN ('queued','running','unknown')",
                  (mart_id,)).fetchone():
        raise QueryError('MART_BUSY', 409)


def get_mart(principal, context, mart_id):
    _access(principal, context)
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        revalidate_admission(db, principal, context)
        mart = _public(_mart(db, principal, context, mart_id))
    if mart['reconciliation_required'] and mart['blocked_run_id']:
        _repair_closed(principal, context, mart_id, mart['blocked_run_id'])
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        revalidate_admission(db, principal, context)
        return _public(_mart(db, principal, context, mart_id))


def list_marts(principal, context):
    _access(principal, context)
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        revalidate_admission(db, principal, context)
        return [_public(r) for r in db.execute('SELECT * FROM db_marts WHERE database_id=? AND owner=?',
            (context.database_id, principal.principal_id)) if json.loads(r['context'])['schema_id'] == context.schema_id
            and json.loads(r['context'])['warehouse_id'] == context.warehouse_id]


def _run_row(db, run_id):
    row = db.execute('SELECT * FROM db_mart_runs WHERE id=?', (run_id,)).fetchone()
    if row is None:
        raise QueryError('MART_RUN_NOT_FOUND', 404)
    return row, json.loads(row['record'])


def _write_run(db, run):
    db.execute('UPDATE db_mart_runs SET record=? WHERE id=?', (ds._json(run), run['id']))


def _public_run(run):
    return {k: v for k, v in run.items() if not k.startswith('_')}


def _check_child(db, principal, context, run_id):
    _, run = _run_row(db, run_id)
    row = _mart(db, principal, context, run['mart_id'], run['mart_revision'])
    if (run['owner'] != principal.principal_id or run['context'] != asdict(context)
            or run['status'] != 'running' or json.loads(row['definition']) != run['definition']):
        raise QueryError('MART_RUN_STOPPED', 409)
    head = db.execute('SELECT version FROM wap_models WHERE database_id=? AND model_id=?',
        (context.database_id, run['model']['model_id'])).fetchone()
    if head is None or head['version'] != run['model']['version']:
        raise QueryError('MODEL_VERSION_CHANGED', 409)


def _pinned(execution, sources, run_id):
    scope = ds._mart.get()
    context = execution._context()
    if scope is None or scope[5] != run_id or scope[6] != 'build':
        raise QueryError('MART_CONTINUATION_REQUIRED', 409)
    with control_store.connect() as db:
        _check_child(db, execution.principal, context, run_id)
        _, run = _run_row(db, run_id)
    inputs = run['start_measurements']
    if inputs is None or set(inputs) != set(sources):
        raise QueryError('SOURCE_CHANGED', 409)
    for alias, ref in sources.items():
        saved = inputs[alias]
        current = execution.wap_measure(ref)  # validates actual Ref/UUID and schema
        if (saved['ref'] != asdict(ref) or not re.fullmatch('[0-9]+', saved['snapshot'])
                or any(current[k] != saved[k] for k in ('ref', 'schema_id', 'schema_fingerprint', 'columns'))):
            raise QueryError('SOURCE_SCHEMA_CHANGED', 409)
    return inputs


def _comparison(model, measurements):
    refs = {ds._json(v['ref']): dict(ref=v['ref'], snapshot=v['snapshot']) for v in measurements.values()}
    return dict(model_id=model['model_id'], version=model['version'], sources=[refs[k] for k in sorted(refs)])


def submit(principal, context, mart_id, *, key, expected_revision, kind='refresh', check_changes=False):
    _access(principal, context)
    _revision(expected_revision)
    if kind not in ('check', 'refresh') or type(check_changes) is not bool:
        raise ValueError('invalid Mart action')
    route = '/api/v1/marts/' + mart_id + ('/checks' if kind == 'check' else '/refreshes')
    payload = ds._json(dict(context=asdict(context), expected_revision=expected_revision, kind=kind, check_changes=check_changes))
    with _lock:
        with control_store.connect() as db:
            _mart(db, principal, context, mart_id)
            replay = _request(db, principal, route, key, payload)
        if replay:
            return get_run(principal, context, mart_id, replay['run_id'])
        frozen = queries._capture_dataops(principal, context)
        ident = str(uuid.uuid4())
        credential = secrets.token_urlsafe(32)
        owner = queries._acquire_query_owner('mart-' + ident)
        try:
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                revalidate_admission(db, principal, context)
                replay = _request(db, principal, route, key, payload)
                if replay:
                    owner.close()
                    return _read_run(db, principal, context, mart_id, replay['run_id'])
                row = _mart(db, principal, context, mart_id, expected_revision)
                _idle(db, mart_id, _public(row))
                definition = json.loads(row['definition'])
                if (kind == 'check' or check_changes) and not definition['source_refs']:
                    raise QueryError('WATCH_REQUIRED', 409)
                model_row = db.execute('SELECT v.record FROM wap_model_versions v JOIN wap_models m USING(database_id,model_id) '
                    'WHERE v.database_id=? AND v.model_id=? AND v.version=? AND m.version=v.version',
                    (context.database_id, definition['model_id'], definition['version'])).fetchone()
                if model_row is None:
                    raise QueryError('MODEL_VERSION_CHANGED', 409)
                import db_schedules
                db_schedules._admit(db, 'mart', mart_id, expected_revision, ident, context)
                parent = admission.admit(db, warehouse_id=context.warehouse_id, actor=principal.principal_id,
                    kind='mart', subject_id=ident, generation_id=context.generation_id, database_id=context.database_id)
                run = dict(id=ident, mart_id=mart_id, mart_revision=expected_revision, kind=kind,
                    context=asdict(context), owner=principal.principal_id, definition=definition,
                    model=json.loads(model_row['record']), status='queued', committed=False, result=None,
                    error=None, start_measurements=None, comparison=None, check_changes=check_changes,
                    created_at=admission._now(), publication=None, baseline_saved=False,
                    _backend_username=frozen.headers['X-Trino-User'], _credential_snapshot_ref=credential)
                db.execute('INSERT INTO db_mart_runs VALUES (?,?,?,?)', (ident, mart_id, ds._json(run), parent.id))
                _receipt(db, principal, route, key, payload, dict(run_id=ident))
                # Freeze the authenticated ACK before a worker can write. The
                # admission transaction is this first return's authority boundary.
                accepted = _public_run(json.loads(ds._json(run)))
            _live[ident] = (principal, context, parent, frozen, credential, owner)
            _start(ident)
        except BaseException:
            _live.pop(ident, None)
            owner.close()
            raise
    return accepted


def _start(run_id):
    threading.Thread(target=_execute, args=(run_id,), daemon=True, name='mart-' + run_id).start()


def _validate(principal, context, run_id):
    _access(principal, context)
    with control_store.connect() as db:
        _check_child(db, principal, context, run_id)
        _, run = _run_row(db, run_id)
        mart = _public(_mart(db, principal, context, run['mart_id']))
    _definition(principal, context, run['definition'], mart)
    # Validate every model source before every child, allowing later appends.
    metadata = wap._Metadata(principal, context)
    for value in run['model']['sources'].values():
        metadata._ref(registry.ResourceRef(**value))
    return run


def _execute(run_id):
    principal, context, parent, frozen, credential, owner = _live[run_id]
    try:
        with control_store.connect() as db:
            _, run = _run_row(db, run_id)
            run.update(status='running', started_at=admission._now())
            _write_run(db, run)
        with admission.accepted(parent):
            run = _validate(principal, context, run_id)
            metadata = wap._Metadata(principal, context)
            measurements, observed = {}, {}
            for alias, value in run['model']['sources'].items():
                identity = ds._json(value)
                if identity not in observed:
                    observed[identity] = metadata.wap_measure(registry.ResourceRef(**value))
                measurements[alias] = observed[identity]
            comparison = _comparison(run['model'], measurements)
            with control_store.connect() as db:
                _, run = _run_row(db, run_id)
                run.update(start_measurements=measurements, comparison=comparison)
                _write_run(db, run)
                baseline = _public(_mart(db, principal, context, run['mart_id']))['last_success_baseline']
            unchanged = baseline is not None and baseline['comparison'] == comparison
            if run['kind'] == 'check' or (run['check_changes'] and unchanged):
                with control_store.connect() as db:
                    _, run = _run_row(db, run_id)
                    run.update(status='skipped' if run['kind'] == 'refresh' else 'succeeded',
                        result=dict(changed=not unchanged, unchanged=unchanged))
                    _write_run(db, run)
            else:
                result = {}
                for phase in ('build', 'audit', 'publish'):
                    _validate(principal, context, run_id)
                    with ds._mart_scope(principal, context, parent, frozen, credential, run_id, phase):
                        if phase == 'build':
                            child = wap.build(principal, context, key=run_id + ':build',
                                model_id=run['model']['model_id'], version=run['model']['version'], _start=run_id)
                        elif phase == 'audit':
                            child = wap.audit(principal, context, key=run_id + ':audit', build_id=result['build_id'])
                        else:
                            child = wap.publish(principal, context, key=run_id + ':publish',
                                build_id=result['build_id'], audit_id=result['audit_id'])
                    result.update(child.get('result') or {})
                    with control_store.connect() as db:
                        _, current = _run_row(db, run_id)
                        current['result'] = result
                        _write_run(db, current)
                    if child['state'] != 'FINISHED':
                        raise QueryError('MART_CHILD_UNRESOLVED', 409)
                    if phase == 'audit' and not result['audit']['passed']:
                        raise QueryError('QUALITY_FAILED', 409)
    except Exception as error:
        with control_store.connect() as db:
            _, run = _run_row(db, run_id)
            run['error'] = getattr(error, 'code', type(error).__name__)
            _write_run(db, run)
    finally:
        try:
            _settle(principal, context, run_id)
        except Exception:
            # A persisted physical root remains a fail-closed observation point.
            pass
        _live.pop(run_id, None)
        owner.close()


def _closed_child(db, operation_id):
    parent = admission.get_for_subject(db, kind='dataops', subject_id=operation_id,
        warehouse_id=json.loads(db.execute('SELECT context FROM dataops_operations WHERE id=?', (operation_id,)).fetchone()[0])['warehouse_id'])
    if parent is None:
        return False
    rows = list(db.execute('''SELECT d.query_id,q.state,e.query_id AS execution_id,a.state AS admission_state
        FROM dataops_queries d LEFT JOIN query_records q ON q.id=d.query_id
        LEFT JOIN query_execution_contexts e ON e.query_id=d.query_id
        LEFT JOIN warehouse_admissions a ON a.kind='query' AND a.subject_id=d.query_id AND a.parent_id=?
        WHERE d.operation_id=?''', (parent.id, operation_id)))
    if any(r['state'] not in admission.TERMINAL or r['execution_id'] is None
           or r['admission_state'] not in admission.TERMINAL for r in rows):
        return False
    if db.execute('''SELECT 1 FROM warehouse_admissions a LEFT JOIN dataops_queries d
        ON d.query_id=a.subject_id AND d.operation_id=? WHERE a.parent_id=? AND d.query_id IS NULL''',
        (operation_id, parent.id)).fetchone():
        return False
    if db.execute('''SELECT 1 FROM query_execution_contexts e LEFT JOIN dataops_queries d
        ON d.query_id=e.query_id AND d.operation_id=? WHERE e.request_id=? AND d.query_id IS NULL''',
        (operation_id, operation_id)).fetchone():
        return False
    op = db.execute('SELECT * FROM dataops_operations WHERE id=?', (operation_id,)).fetchone()
    if op['state'] == 'FINISHED' and not rows:
        return False
    if op['committed'] and not any(r['state'] == 'FINISHED' for r in rows):
        return False
    state = 'FINISHED' if op['committed'] else 'FAILED'
    saved = db.execute('SELECT state FROM warehouse_admissions WHERE id=?', (parent.id,)).fetchone()[0]
    if saved not in admission.TERMINAL:
        admission.finish(db, parent, state=state, evidence_ref='mart-child-closure:' + operation_id)
    if op['state'] not in admission.TERMINAL:
        db.execute('UPDATE dataops_operations SET state=? WHERE id=?', (state, operation_id))
    return True


def _publication(principal, context, run, operations):
    """Only exact P3 artifact or positively measured canonical receipt is proof."""
    op = operations.get('publish')
    if op is None or not op['committed']:
        return None
    result = json.loads(op['result'] or '{}')
    with control_store.connect() as db:
        row = db.execute('SELECT record FROM wap_publications WHERE id=? AND operation_id=?',
            (result.get('publication_id'), op['id'])).fetchone()
        if row:
            publication = json.loads(row[0])
        else:
            publication = result.get('publication')
        if not publication or not all(p in operations for p in ('build', 'audit')):
            return None
        for phase, child in operations.items():
            if (child['actor'] != run['owner'] or json.loads(child['context']) != run['context']
                    or child['route'] != 'manual-wap-' + phase
                    or db.execute("SELECT 1 FROM dataops_queries d JOIN query_records q ON q.id=d.query_id "
                        "WHERE d.operation_id=? AND q.state!='FINISHED'", (child['id'],)).fetchone()):
                return None
        built_row = db.execute('SELECT record FROM wap_builds WHERE id=? AND operation_id=?',
            (publication.get('build_id'), operations['build']['id'])).fetchone()
        audited_row = db.execute('SELECT record FROM wap_audits WHERE id=? AND operation_id=?',
            (publication.get('audit_id'), operations['audit']['id'])).fetchone()
        receipt = db.execute("SELECT d.commit_result FROM dataops_queries d JOIN query_records q ON q.id=d.query_id "
            "WHERE d.operation_id=? AND d.action='wap_publish' AND q.state='FINISHED'", (op['id'],)).fetchall()
    if not built_row or not audited_row or len(receipt) != 1 or not receipt[0][0]:
        return None
    built, audited, intent = json.loads(built_row[0]), json.loads(audited_row[0]), json.loads(receipt[0][0])
    with control_store.connect() as db:
        owned = db.execute('SELECT incarnation FROM dataops_owned_objects WHERE operation_id=? AND object_id=?',
            (built['operation_id'], built['stage']['ref']['object_id'])).fetchone()
    if owned is None or owned['incarnation'] != built['stage']['ref']['incarnation']:
        return None
    if (any(publication.get(k) != v for k, v in intent.items())
            or publication['operation_id'] != op['id'] or publication['context'] != run['context']
            or publication['actor'] != run['owner'] or publication['database_id'] != context.database_id
            or publication['model_id'] != run['model']['model_id'] or publication['version'] != run['model']['version']
            or built['input_refs'] != run['start_measurements']
            or built['model_id'] != run['model']['model_id'] or built['version'] != run['model']['version']
            or built['context'] != run['context'] or built['actor'] != run['owner']
            or audited['model_id'] != built['model_id'] or audited['version'] != built['version']
            or audited['context'] != run['context'] or audited['actor'] != run['owner']
            or not audited['passed'] or audited['build_id'] != built['id']
            or audited['stage'] != built['stage'] or publication['stage'] != audited['stage']):
        return None
    if row is None:
        # No locator lookup/adoption: the receipt must already contain an actual Ref.
        metadata = wap._Metadata(principal, context)
        target = registry.ResourceRef(**publication['target']['ref'])
        measured = metadata.wap_measure(target)
        if measured != publication['target']:
            return None
        with control_store.connect() as db:
            obj = registry.get_object(db, target.object_id)
            if (target.schema_id != context.schema_id or obj is None or not obj.current
                    or not db.execute('SELECT 1 FROM dataops_target_claims WHERE catalog=? AND schema_name=? '
                        'AND table_name=? AND operation_id=?',
                        (context.sql_catalog, context.sql_schema, obj.physical_name, op['id'])).fetchone()):
                return None
        wap._store_artifact('wap_publications', publication)
    return publication


def _save_baseline(run_id, publication, *, authority=None):
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        _, run = _run_row(db, run_id)
        if authority is not None:
            principal, context = authority
            revalidate_admission(db, principal, context)
            _mart(db, principal, context, run['mart_id'])
        row = db.execute('SELECT * FROM db_marts WHERE id=?', (run['mart_id'],)).fetchone()
        record = json.loads(row['record'])
        if not run['baseline_saved']:
            times = list(db.execute("SELECT a.finished_at FROM db_mart_operations m JOIN dataops_queries d "
                "ON d.operation_id=m.operation_id JOIN warehouse_admissions a ON a.kind='query' AND a.subject_id=d.query_id "
                "WHERE m.run_id=? AND m.phase='publish' AND d.action='wap_publish'", (run_id,)))
            if len(times) != 1 or not times[0]['finished_at']:
                raise QueryError('PUBLICATION_TIME_REQUIRED', 409)
            record.update(current_target_ref=publication['target']['ref'], last_success_at=times[0]['finished_at'],
                last_success_baseline=dict(comparison=run['comparison'], publication_id=publication['id'],
                    target=publication['target'], run_id=run_id))
            run.update(baseline_saved=True, committed=True, publication=publication)
            _write_run(db, run)
            definition = json.loads(row['definition'])
            transition = definition['target'] != publication['target']['ref']
            definition['target'] = publication['target']['ref']
            cursor = db.execute('UPDATE db_marts SET record=?,definition=?,revision=revision+? WHERE id=? AND revision=?',
                (ds._json(record), ds._json(definition), int(transition), run['mart_id'], run['mart_revision']))
            if cursor.rowcount != 1:
                raise QueryError('MART_REVISION_CONFLICT', 409)


def _finalize(run_id, publication, *, authority=None):
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        _, run = _run_row(db, run_id)
        if authority is not None:
            principal, context = authority
            revalidate_admission(db, principal, context)
            _mart(db, principal, context, run['mart_id'])
        if publication:
            run.update(status='succeeded', committed=True,
                result=dict(run.get('result') or {}, publication=publication), error=None)
        elif run['status'] not in ('skipped', 'succeeded'):
            run['status'] = 'failed'
        run['finished_at'] = admission._now()
        _write_run(db, run)
        row = db.execute('SELECT record FROM db_marts WHERE id=?', (run['mart_id'],)).fetchone()
        record = json.loads(row[0])
        if record.get('blocked_run_id') == run_id:
            record.update(reconciliation_required=False, blocked_run_id=None)
            db.execute('UPDATE db_marts SET record=? WHERE id=?', (ds._json(record), run['mart_id']))


def _block(run_id, *, committed, closed):
    with control_store.connect() as db:
        _, run = _run_row(db, run_id)
        run.update(status='reconciliation_required' if closed else 'unknown', committed=committed)
        _write_run(db, run)
        record = json.loads(db.execute('SELECT record FROM db_marts WHERE id=?', (run['mart_id'],)).fetchone()[0])
        record.update(reconciliation_required=True, blocked_run_id=run_id)
        db.execute('UPDATE db_marts SET record=? WHERE id=?', (ds._json(record), run['mart_id']))


def _settle(principal, context, run_id):
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row, run = _run_row(db, run_id)
        operations = {r['phase']: dict(r) for r in db.execute('SELECT o.*,m.phase FROM db_mart_operations m '
            'JOIN dataops_operations o ON o.id=m.operation_id WHERE m.run_id=?', (run_id,))}
        closed = all([_closed_child(db, op['id']) for op in operations.values()])
        parent = admission.get_for_subject(db, kind='mart', subject_id=run_id, warehouse_id=context.warehouse_id)
        if closed:
            admission.finish(db, parent, state='FINISHED', evidence_ref='mart-physical-closure:' + run_id)
        else:
            admission.finish(db, parent, state='UNKNOWN')
    committed = bool(operations.get('publish', {}).get('committed'))
    if not closed:
        _block(run_id, committed=committed, closed=False)
        return
    # Close physical capacity before separate baseline/history writes. A known
    # bookkeeping wait holds only this Mart; true SQL UNKNOWN holds the root.
    try:
        publication = _publication(principal, context, run, operations) if committed else None
        if committed and publication is None:
            _block(run_id, committed=True, closed=True)
            return
        if publication:
            _save_baseline(run_id, publication)
        _finalize(run_id, publication)
    except Exception:
        _block(run_id, committed=committed, closed=True)


def get_run(principal, context, mart_id, run_id):
    _access(principal, context)
    with control_store.connect() as db:
        _mart(db, principal, context, mart_id)
        _, run = _run_row(db, run_id)
        if run['mart_id'] != mart_id or run['owner'] != principal.principal_id:
            raise QueryError('MART_RUN_NOT_FOUND', 404)
        if run['context'] != asdict(context):
            raise QueryError('CONTEXT_MISMATCH', 409)
    if run_id not in _live and run['status'] in ('queued', 'running', 'unknown', 'reconciliation_required'):
        try:
            owner = queries._acquire_query_owner('mart-' + run_id)
        except QueryError:
            owner = None  # Another process owns execution; Observe never steals it.
        if owner is not None:
            try:
                _settle(principal, context, run_id)
            finally:
                owner.close()
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        return _read_run(db, principal, context, mart_id, run_id)


def _read_run(db, principal, context, mart_id, run_id):
    revalidate_admission(db, principal, context)
    _mart(db, principal, context, mart_id)
    _, run = _run_row(db, run_id)
    if run['mart_id'] != mart_id or run['owner'] != principal.principal_id:
        raise QueryError('MART_RUN_NOT_FOUND', 404)
    if run['context'] != asdict(context):
        raise QueryError('CONTEXT_MISMATCH', 409)
    return _public_run(run)


def list_runs(principal, context, mart_id):
    _access(principal, context)
    with control_store.connect() as db:
        _mart(db, principal, context, mart_id)
        ids = [r[0] for r in db.execute("SELECT id FROM db_mart_runs WHERE mart_id=? "
            "AND json_extract(record,'$.context')=? ORDER BY rowid DESC", (mart_id, ds._json(asdict(context))))]
    for ident in ids:
        get_run(principal, context, mart_id, ident)
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        revalidate_admission(db, principal, context)
        _mart(db, principal, context, mart_id)
        return [_read_run(db, principal, context, mart_id, ident) for ident in ids]


def _repair_closed(principal, context, mart_id, run_id):
    """Observe positive physical closure using current authority, never run pins."""
    if run_id in _live:
        return
    try:
        owner = queries._acquire_query_owner('mart-' + run_id)
    except QueryError:
        return
    try:
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            revalidate_admission(db, principal, context)
            mart = _public(_mart(db, principal, context, mart_id))
            row, run = _run_row(db, run_id)
            if (mart['blocked_run_id'] != run_id or not mart['reconciliation_required']
                    or run['mart_id'] != mart_id or run['owner'] != principal.principal_id
                    or run['status'] != 'reconciliation_required'):
                return
            parent = db.execute("SELECT * FROM warehouse_admissions WHERE id=? AND kind='mart' AND subject_id=?",
                (row['admission_id'], run_id)).fetchone()
            if parent is None or parent['state'] != 'FINISHED':
                return
            operations = {r['phase']: dict(r) for r in db.execute('SELECT o.*,m.phase FROM db_mart_operations m '
                'JOIN dataops_operations o ON o.id=m.operation_id WHERE m.run_id=?', (run_id,))}
            children = list(db.execute('SELECT * FROM warehouse_admissions WHERE parent_id=?', (parent['id'],)))
            if (set(operations) != {'build', 'audit', 'publish'}
                    or {r['subject_id'] for r in children} != {op['id'] for op in operations.values()}
                    or any(r['kind'] != 'dataops' or r['state'] not in admission.TERMINAL for r in children)
                    or any(op['state'] not in admission.TERMINAL for op in operations.values())
                    or not all(_closed_child(db, op['id']) for op in operations.values())):
                return
        _access(principal, context)
        publication = _publication(principal, context, run, operations)
        if publication is None:
            return
        if wap._Metadata(principal, context).wap_measure(registry.ResourceRef(**publication['target']['ref'])) != publication['target']:
            return
        _save_baseline(run_id, publication, authority=(principal, context))
        _finalize(run_id, publication, authority=(principal, context))
    except Exception:
        # A failed proof or bookkeeping write keeps this Mart blocked. The
        # caller still performs its final current-authority read transaction.
        pass
    finally:
        owner.close()
