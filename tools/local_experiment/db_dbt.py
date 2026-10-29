"""Durable offline dbt SELECT plans over the existing typed WAP boundary.

No SQL or compile is launched by reads/reconciliation. Execution credentials and
leases live only in the accepting process; records contain no reusable secrets.
"""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading
import uuid

import control_store
import database_inventory as inventory
import data_management as registry
import dataops_service as ds
import manual_wap as wap
import query_service as queries
from query_context import QueryError, ResolvedQueryContext, revalidate_admission
import warehouse_admission as admission

_live = {}
_ACTIVE = {'QUEUED', 'BUILDING', 'TESTING', 'AUDITING', 'PUBLISHING', 'CANCELING', 'COMPILING'}
_REPO = Path(__file__).resolve().parents[2]


def _access(principal, context, *, admin=False):
    metadata = wap._access(principal, context, write=True, admin=admin)
    schema = metadata._schema(context.schema_id, write=True)
    if schema.sql_name != 'sandbox' or schema.purpose != 'sandbox':
        raise QueryError('SANDBOX_REQUIRED', 409)
    return metadata


def _projects():
    projects = [dict(id='lake_marts', name='Lake marts', path='tools/local_experiment/dataops/dbt_persistent')]
    config = os.environ.get('SHIROKUMA_DBT_PROJECTS_FILE')
    if config:
        try:
            extra = json.loads(Path(config).read_text())
            if not isinstance(extra, list):
                raise ValueError()
            projects.extend(extra)
        except (OSError, ValueError):
            raise QueryError('DBT_PROJECT_CONFIG_INVALID', 409) from None
    result = {}
    for project in projects:
        try:
            if set(project) != {'id', 'name', 'path'}:
                raise ValueError()
            ident, name = project['id'], project['name']
            if not isinstance(ident, str) or not re.fullmatch('[a-z][a-z0-9_-]{0,63}', ident):
                raise ValueError()
            if not isinstance(name, str) or not name.strip() or len(name) > 128 or ident in result:
                raise ValueError()
            path = (_REPO / project['path']).resolve()
            if not path.is_relative_to(_REPO) or not path.is_dir():
                raise ValueError()
            result[ident] = dict(id=ident, name=name, path=path)
        except (ValueError, TypeError, KeyError):
            raise QueryError('DBT_PROJECT_CONFIG_INVALID', 409) from None
    return result


def list_projects(principal, context):
    _access(principal, context)
    projects = [dict(id=p['id'], name=p['name'], materializations=['table', 'ephemeral', 'view'],
        sql_subset='Offline compiled SELECT through the WAP read parser; built-in generic tests',
        staging_materializations=[None, 'table', 'ephemeral']) for p in _projects().values()]
    with control_store.connect() as db:
        revalidate_admission(db, principal, context)
    return projects


def _row(db, ident):
    row = db.execute('SELECT * FROM dbt_runs WHERE id=?', (ident,)).fetchone()
    if row is None:
        raise QueryError('DBT_ARTIFACT_NOT_FOUND', 404)
    return row, json.loads(row['record'])


def _write(db, record):
    # Whole-record updates must never clear a cancellation accepted elsewhere.
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    old = db.execute('SELECT record FROM dbt_runs WHERE id=?', (record['id'],)).fetchone()
    if old and json.loads(old[0]).get('cancel_requested'):
        record['cancel_requested'] = True
        if record['state'] in _ACTIVE or record['state'] == 'READY':
            record['state'] = 'CANCELING'
    record['updated_at'] = admission._now()
    db.execute('UPDATE dbt_runs SET record=? WHERE id=?', (ds._json(record), record['id']))


def _authorized(db, principal, context, ident, kind=None):
    revalidate_admission(db, principal, context)
    row, record = _row(db, ident)
    if (row['database_id'] != context.database_id or kind and record['kind'] != kind or
            context.selected_role != 'admin' and (record['actor'] != principal.principal_id or
                record['context']['selected_role'] != context.selected_role)):
        raise QueryError('DBT_ARTIFACT_NOT_FOUND', 404)
    saved = record['context']
    if any(saved[k] != asdict(context)[k] for k in ('database_id', 'schema_id', 'warehouse_id')):
        raise QueryError('CONTEXT_MISMATCH', 409)
    return record


def _public(db, record):
    result = {k: v for k, v in record.items() if not k.startswith('_')}
    links = [dict(id=r['operation_id'], phase=r['phase'], node_id=r['node_id'], state=r['state'],
        committed=bool(r['committed'])) for r in db.execute('SELECT l.*,o.state,o.committed FROM dbt_run_operations l '
            'JOIN dataops_operations o ON o.id=l.operation_id WHERE l.run_id=? ORDER BY l.rowid', (record['id'],))]
    result['operations'] = links
    result['operation_ids'] = [r['id'] for r in links]
    def safe(value):
        if isinstance(value, dict):
            return {k: safe(v) for k, v in value.items() if not k.startswith('_')}
        if isinstance(value, list):
            return [safe(v) for v in value]
        return value
    return json.loads(ds._json(safe(result)))


def _cancel_owner(db, principal, context, ident):
    # Cleanup uses the accepting owner's exact saved context, never new authority.
    ds._cancel_account(principal)
    _, record = _row(db, ident)
    if record['kind'] != 'run' or record['actor'] != principal.principal_id or record['context'] != asdict(context):
        raise QueryError('CONTEXT_MISMATCH', 409)
    return record


def _cancel_receipt(db, principal, context, record):
    try:
        _access(principal, context)
        return _public(db, _authorized(db, principal, context, record['id'], 'run'))
    except (QueryError, PermissionError):
        # Keep the run receipt shape, but cleanup authority cannot read model SQL,
        # source bindings, candidate metadata, or child query results.
        result = _public(db, record)
        for field in ('models', 'tests', 'source_bindings', 'operations', 'operation_ids'):
            if field in result:
                result[field] = []
        return result


def _request(db, principal, context, route, key, payload, *, cleanup=False):
    if not isinstance(key, str) or not key.strip() or len(key) > 256:
        raise ValueError('idempotency key required')
    row = db.execute('SELECT * FROM dbt_requests WHERE actor=? AND route=? AND idempotency_key=?',
        (principal.principal_id, route, key)).fetchone()
    if row:
        if row['payload'] != payload:
            raise QueryError('IDEMPOTENCY_CONFLICT', 409)
        ident = json.loads(row['response'])['id']
        if cleanup:
            return _cancel_receipt(db, principal, context, _cancel_owner(db, principal, context, ident))
        return _public(db, _authorized(db, principal, context, ident))


def _receipt(db, principal, route, key, payload, record):
    db.execute('INSERT INTO dbt_requests VALUES (?,?,?,?,?)',
        (principal.principal_id, route, key, payload, ds._json(dict(id=record['id']))))


def _base(principal, context, kind, project):
    now = admission._now()
    return dict(id=str(uuid.uuid4()), kind=kind, actor=principal.principal_id, context=asdict(context),
        state='QUEUED', project_id=project['project_id'], project_name=project['project_name'],
        source_digest=project.get('source_digest'), created_at=now, updated_at=now,
        error_code=None, cancel_requested=False)


def _schema(metadata, value):
    if not isinstance(value, str) or not value:
        raise ValueError('registered schema required')
    context = metadata._context()
    with control_store.connect() as db:
        row = db.execute('SELECT id FROM schemas WHERE database_id=? AND (id=? OR sql_name=?)',
            (context.database_id, value, value)).fetchone()
    if row is None:
        raise QueryError('SCHEMA_NOT_FOUND', 404)
    return metadata._schema(row['id'])


def submit_plan(principal, context, *, key, project_id, source_schema, target_schema, staging_materialization):
    metadata = _access(principal, context)
    route = '/api/v1/dbt/plans'
    payload = ds._json(dict(context=asdict(context), project_id=project_id, source_schema=source_schema,
        target_schema=target_schema, staging_materialization=staging_materialization))
    with control_store.connect() as db:
        replay = _request(db, principal, context, route, key, payload)
        if replay:
            return replay
    project = _projects().get(project_id)
    if project is None:
        raise QueryError('DBT_PROJECT_NOT_FOUND', 404)
    if staging_materialization not in (None, 'table', 'ephemeral'):
        raise ValueError('staging_materialization must be null, table or ephemeral')
    source, target = _schema(metadata, source_schema), _schema(metadata, target_schema)
    record = _base(principal, context, 'plan', dict(project_id=project_id, project_name=project['name']))
    record.update(source_schema=source.id, target_schema=target.id, staging_materialization=staging_materialization,
        models=[], tests=[], source_bindings=[], _source_sql_schema=source.sql_name)
    owner = queries._acquire_query_owner('dbt-' + record['id'])
    try:
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            revalidate_admission(db, principal, context)
            replay = _request(db, principal, context, route, key, payload)
            if replay:
                owner.close()
                return replay
            db.execute('INSERT INTO dbt_runs VALUES (?,?,?,NULL)', (record['id'], context.database_id, ds._json(record)))
            _receipt(db, principal, route, key, payload, record)
            ack = _public(db, record)
        _live[record['id']] = (principal, context, None, None, None, owner)
        _start(record['id'])
        return ack
    except BaseException:
        _live.pop(record['id'], None)
        owner.close()
        raise


def _hash_plan(plan):
    fields = ('source_digest', 'source_schema', 'target_schema', 'staging_materialization',
              'models', 'tests', 'source_bindings', '_targets')
    if 'compatibility' in plan:
        fields += ('compatibility',)
    return hashlib.sha256(ds._json({k: plan[k] for k in fields}).encode()).hexdigest()


def _compile(ident, principal, context):
    import database_dbt as compiler
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        plan = _authorized(db, principal, context, ident, 'plan')
        plan['state'] = 'COMPILING'
        _write(db, plan)
    project = _projects().get(plan['project_id'])
    if project is None:
        raise QueryError('DBT_PROJECT_NOT_FOUND', 404)
    summary = compiler.compile_project(project['path'], catalog=context.sql_catalog,
        source_schema=plan['_source_sql_schema'], staging_materialization=plan['staging_materialization'])
    metadata = _access(principal, context)
    bindings, targets = [], {}
    for source in summary['sources']:
        relation = source['relation']
        if relation[:2] != [context.sql_catalog, plan['_source_sql_schema']]:
            raise QueryError('INVALID_BINDING', 409)
        locator = registry.DestinationLocator(context.database_id, plan['source_schema'], relation[2])
        # Observe identity using metadata only; retain the exact Ref, never a locator for sources.
        schema = metadata._schema(locator.schema_id)
        database, binding = metadata._catalog()
        with inventory.catalog_api(database, binding) as api:
            actual = inventory._metadata(api, schema, 'TABLE', locator.physical_name)
        identity = actual.get('table-uuid')
        try:
            uuid.UUID(identity)
        except (ValueError, TypeError, AttributeError):
            raise QueryError('STALE_RESOURCE', 409) from None
        with control_store.connect() as db:
            obj = registry.observe_object(db, schema.id, locator.physical_name, 'TABLE', identity, locator.physical_name)
        ref = registry.ResourceRef(context.database_id, schema.id, obj.id, obj.incarnation)
        metadata._ref(ref)
        bindings.append(dict(source, ref=asdict(ref)))
    for model in summary['models']:
        if model['materialized'] != 'table':
            continue
        name = model['relation'][2]
        registry.validate_sql_name(name)
        with control_store.connect() as db:
            rows = list(db.execute("SELECT id,incarnation FROM object_bindings WHERE schema_id=? AND physical_name=? AND kind='TABLE' AND current=1",
                (plan['target_schema'], name)))
        if len(rows) > 1:
            raise QueryError('TARGET_CHANGED', 409)
        target = registry.ResourceRef(context.database_id, plan['target_schema'], rows[0]['id'], rows[0]['incarnation']) if rows else registry.DestinationLocator(context.database_id, plan['target_schema'], name)
        if rows:
            metadata._ref(target)
        else:
            # A target need not have been opened in Catalog before planning.
            # Resolve it once here; execution never adopts a replacement UUID.
            schema = metadata._schema(target.schema_id)
            database, binding = metadata._catalog()
            actual = None
            with inventory.catalog_api(database, binding) as api:
                try:
                    actual = inventory._metadata(api, schema, 'TABLE', name)
                except inventory.InventoryError as error:
                    if error.status != 404:
                        raise
            if actual is None:
                metadata._absent_model(target)  # Includes the View absence guard.
            else:
                identity = actual.get('table-uuid')
                try:
                    uuid.UUID(identity)
                except (ValueError, TypeError, AttributeError):
                    raise QueryError('STALE_RESOURCE', 409) from None
                with control_store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    revalidate_admission(db, principal, context)
                    obj = registry.observe_object(db, schema.id, name, 'TABLE', identity, name)
                target = registry.ResourceRef(context.database_id, schema.id, obj.id, obj.incarnation)
                metadata._ref(target)
        targets[model['unique_id']] = asdict(target)
    plan.update(compatibility=summary['compatibility'], source_digest=summary['source_digest'], models=summary['models'], tests=summary['tests'],
        source_bindings=bindings, _targets=targets, compiler_versions=summary.get('versions'), state='READY')
    plan['_plan_digest'] = _hash_plan(plan)
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        revalidate_admission(db, principal, context)
        _write(db, plan)


def _check_plan(db, run):
    _, plan = _row(db, run['plan_id'])
    if (plan['kind'] != 'plan' or plan['state'] != 'READY' or _hash_plan(plan) != plan['_plan_digest']
            or run['_plan_digest'] != plan['_plan_digest'] or run['source_digest'] != plan['source_digest']):
        raise QueryError('DBT_PLAN_CHANGED', 409)
    return plan


def _supported_contract(plan):
    c = plan.get('compatibility')
    return any(c == dict(version=v, executor='sql_wap', status='supported', issues=[]) for v in (1, 2)) and (
        c['version'] == 2 or all(m['materialized'] in ('table', 'ephemeral') for m in plan['models'])) and all(
        m.get('view_security') == 'INVOKER' for m in plan['models'] if m['materialized'] == 'view')


def submit_run(principal, context, *, key, plan_id):
    _access(principal, context)
    route = '/api/v1/dbt/runs'
    payload = ds._json(dict(context=asdict(context), plan_id=plan_id))
    with control_store.connect() as db:
        plan = _authorized(db, principal, context, plan_id, 'plan')
        replay = _request(db, principal, context, route, key, payload)
        if replay:
            return replay
    if plan['actor'] != principal.principal_id or plan['context'] != asdict(context):
        raise QueryError('CONTEXT_MISMATCH', 409)
    if plan['state'] != 'READY' or _hash_plan(plan) != plan['_plan_digest']:
        raise QueryError('DBT_PLAN_NOT_READY', 409)
    if not _supported_contract(plan):
        raise QueryError('DBT_PLAN_RECOMPILE_REQUIRED', 409)
    record = _base(principal, context, 'run', plan)
    record.update(plan_id=plan_id, _plan_digest=plan['_plan_digest'], _pins=None, _phase=None, _node_id=None,
        models=[dict(unique_id=m['unique_id'], name=m['name'], materialized=m['materialized'],
            table_properties=m['table_properties'],
            **({'view_security': m['view_security']} if m['materialized'] == 'view' else {}), state='QUEUED',
            wap_model_id='dbt_' + record['id'].replace('-', '') + '_' + str(i), wap_version=None,
            build_id=None, candidate=None, operation_ids=[]) for i, m in enumerate(plan['models'])],
        tests=[dict(unique_id=t['unique_id'], name=t['name'], test_type=t['test_type'], state='QUEUED',
            failure_count=None, operation_id=None) for t in plan['tests']])
    return _accept(principal, context, route, key, payload, record)


def _accept(principal, context, route, key, payload, record, check=None):
    frozen = queries._capture_dataops(principal, context)
    owner = queries._acquire_query_owner('dbt-' + record['id'])
    try:
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            revalidate_admission(db, principal, context)
            replay = _request(db, principal, context, route, key, payload)
            if replay:
                owner.close()
                return replay
            if check:
                check(db)
            elif record['kind'] == 'run':
                plan = _check_plan(db, record)
                if plan['actor'] != principal.principal_id or plan['context'] != asdict(context):
                    raise QueryError('CONTEXT_MISMATCH', 409)
            parent = admission.admit(db, warehouse_id=context.warehouse_id, actor=principal.principal_id,
                kind='dbt', subject_id=record['id'], database_id=context.database_id, generation_id=context.generation_id)
            db.execute('INSERT INTO dbt_runs VALUES (?,?,?,?)',
                (record['id'], context.database_id, ds._json(record), parent.id))
            _receipt(db, principal, route, key, payload, record)
            ack = _public(db, record)
        _live[record['id']] = (principal, context, parent, frozen, secrets.token_urlsafe(32), owner)
        _start(record['id'])
        return ack
    except BaseException:
        _live.pop(record['id'], None)
        owner.close()
        raise


def _source_identities(db, plan):
    for binding in plan['source_bindings']:
        ref = registry.ResourceRef(**binding['ref'])
        obj = registry.get_object(db, ref.object_id)
        if (obj is None or not obj.current or obj.kind != 'TABLE' or obj.schema_id != ref.schema_id
                or obj.incarnation != ref.incarnation or ref.database_id != plan['context']['database_id']):
            raise QueryError('STALE_RESOURCE', 409)


def _check_child(db, principal, context, run_id, phase, node_id):
    row, run = _row(db, run_id)
    if (run['actor'] != principal.principal_id or run['context'] != asdict(context)
            or run['cancel_requested'] or run.get('_phase') != phase or run.get('_node_id') != node_id
            or run['state'] not in ('BUILDING', 'TESTING', 'AUDITING', 'PUBLISHING')):
        raise QueryError('DBT_RUN_STOPPED', 409)
    revalidate_admission(db, principal, context)
    parent = admission.get_for_subject(db, kind='dbt', subject_id=run_id, warehouse_id=context.warehouse_id)
    if parent is None or parent.id != row['admission_id'] or admission.check_execution(db, parent)['state'] != 'ACTIVE':
        raise QueryError('DBT_RUN_STOPPED', 409)
    source_run = run
    if run['kind'] == 'publication':
        _, source_run = _row(db, run['parent_run_id'])
        _gate(db, source_run, node_id, publication=run_id)
    plan = _check_plan(db, source_run)
    _source_identities(db, plan)
    nodes = plan['tests'] if phase == 'test' else plan['models']
    if node_id not in {n['unique_id'] for n in nodes}:
        raise QueryError('DBT_NODE_NOT_FOUND', 409)
    if phase == 'test' and run['state'] != 'TESTING' or phase == 'build' and run['state'] != 'BUILDING':
        raise QueryError('DBT_RUN_STOPPED', 409)


def _check_operation(db, operation_id):
    link = db.execute('SELECT * FROM dbt_run_operations WHERE operation_id=?', (operation_id,)).fetchone()
    if link:
        op = db.execute('SELECT actor,context,backend_username,state FROM dataops_operations WHERE id=?', (operation_id,)).fetchone()
        _, run = _row(db, link['run_id'])
        live = _live.get(run['id'])
        if live is None or op['state'] != 'ACTIVE':
            raise QueryError('DBT_OWNER_LOST', 409)
        # Final-send authority uses only the accepting process's original actor.
        revalidate_admission(db, live[0], live[1], backend_username=op['backend_username'])
        if op['actor'] != run['actor'] or json.loads(op['context']) != run['context']:
            raise QueryError('CONTEXT_MISMATCH', 409)
        if (run['cancel_requested'] or run['state'] not in ('BUILDING', 'TESTING', 'AUDITING', 'PUBLISHING')
                or run.get('_phase') != link['phase'] or run.get('_node_id') != link['node_id']):
            raise QueryError('DBT_RUN_STOPPED', 409)
        source = _row(db, run['parent_run_id'])[1] if run['kind'] == 'publication' else run
        _source_identities(db, _check_plan(db, source))
        if run['kind'] == 'publication':
            _gate(db, source, link['node_id'], publication=run['id'])


def _before_query(operation_id, principal, context):
    with control_store.connect() as db:
        link = db.execute('SELECT run_id FROM dbt_run_operations WHERE operation_id=?', (operation_id,)).fetchone()
        if link is None:
            return
        _check_operation(db, operation_id)
        _, run = _row(db, link['run_id'])
        source = _row(db, run['parent_run_id'])[1] if run['kind'] == 'publication' else run
        plan = _check_plan(db, source)
    metadata = _access(principal, context)
    _verify_views(metadata, source, complete=run['kind'] == 'publication')
    for binding in plan['source_bindings']:
        ref = registry.ResourceRef(**binding['ref'])
        current = metadata.wap_measure(ref)
        saved = (source.get('_pins') or {}).get(ds._json(binding['ref']))
        if saved is None or any(current[k] != saved[k] for k in ('ref', 'schema_id', 'schema_fingerprint', 'columns')):
            raise QueryError('SOURCE_SCHEMA_CHANGED', 409)


def _normalized(run, node):
    import database_dbt as compiler
    bindings = {tuple(s['relation']): registry.ResourceRef(**s['ref']) for s in run['_source_bindings']}
    for model in run['models']:
        if model['candidate']:
            relation = next(m['relation'] for m in run['_models'] if m['unique_id'] == model['unique_id'])
            bindings[tuple(relation)] = registry.ResourceRef(**model['candidate']['ref'])
    used = {compiler._identity(t) for _, t in compiler._physical(compiler._sql_tree(node['compiled_sql']))}
    if used - bindings.keys():
        raise QueryError('INVALID_BINDING', 409)
    return compiler.normalize_select(node['compiled_sql'], {r: bindings[r] for r in used}, database_id=run['context']['database_id'])


def _pinned(execution, sources):
    scope = ds._dbt.get()
    if scope is None:
        raise QueryError('DBT_CONTINUATION_REQUIRED', 409)
    with control_store.connect() as db:
        _check_child(db, execution.principal, execution._context(), *scope[5:])
        _, run = _row(db, scope[5])
        if run['kind'] != 'run' or scope[6] not in ('build', 'test') or run['_pins'] is None:
            raise QueryError('DBT_CONTINUATION_REQUIRED', 409)
    pins = dict(run['_pins'])
    for model in run['models']:
        if model['candidate']:
            pins[ds._json(model['candidate']['ref'])] = model.get('_view_measurement', model.get('_measurement'))
    inputs = {}
    for alias, ref in sources.items():
        saved = pins.get(ds._json(asdict(ref)))
        if saved is None:
            raise QueryError('SOURCE_CHANGED', 409)
        if saved.get('kind') == 'VIEW':
            _view_binding(execution, ref, expected=saved)
            inputs[alias] = saved
            continue
        current = execution.wap_measure(ref)
        if any(current[k] != saved[k] for k in ('ref', 'schema_id', 'schema_fingerprint', 'columns')):
            raise QueryError('SOURCE_SCHEMA_CHANGED', 409)
        if ref.schema_id == execution._context().schema_id and current != saved:
            raise QueryError('STAGE_CHANGED', 409)
        inputs[alias] = saved
    return inputs


def _row_run(ident):
    with control_store.connect() as db:
        return _row(db, ident)[1]


def _view_node(execution):
    """Resolve action exclusively from the admitted node and immutable plan."""
    scope = ds._dbt.get()
    if scope is None or scope[:2] != (execution.principal, execution._context()) or scope[6] != 'build':
        raise QueryError('DBT_CONTINUATION_REQUIRED', 409)
    if _live.get(scope[5], ())[:5] != scope[:5]:
        raise QueryError('DBT_OWNER_LOST', 409)
    with control_store.connect() as db:
        _check_child(db, execution.principal, execution._context(), *scope[5:])
        _check_operation(db, execution.id)
        link = db.execute('SELECT * FROM dbt_run_operations WHERE operation_id=?', (execution.id,)).fetchone()
        if link is None or tuple(link[k] for k in ('run_id', 'phase', 'node_id')) != scope[5:]:
            raise QueryError('DBT_CONTINUATION_REQUIRED', 409)
        _, run = _row(db, scope[5])
        plan = _check_plan(db, run)
        if run.get('_source_bindings') != plan['source_bindings'] or run.get('_models') != plan['models']:
            raise QueryError('DBT_PLAN_CHANGED', 409)
    operation = execution._row()
    expected = ds._json(dict(payload=dict(run_id=run['id'], node_id=scope[7]), context=run['context']))
    if (operation['route'] != 'dbt-view-create' or operation['payload'] != expected
            or operation['fingerprint'] != hashlib.sha256(expected.encode()).hexdigest()):
        raise QueryError('DBT_CONTINUATION_REQUIRED', 409)
    nodes = [m for m in plan['models'] if m['unique_id'] == scope[7] and m['materialized'] == 'view']
    selected = next(m for m in run['models'] if m['unique_id'] == scope[7])
    if (not _supported_contract(plan) or plan['compatibility']['version'] != 2 or len(nodes) != 1
            or selected['state'] != 'BUILDING' or selected['operation_ids'] != [execution.id]):
        raise QueryError('DBT_CONTINUATION_REQUIRED', 409)
    locator = registry.DestinationLocator(run['context']['database_id'],
        run['context']['schema_id'], 'dbtv_' + hashlib.sha256(execution.id.encode()).hexdigest()[:24])
    if selected['_view_locator'] != asdict(locator):
        raise QueryError('DBT_VIEW_CHANGED', 409)
    return run, nodes[0], locator


def _view_saved(db, run, model):
    """A run descriptor cannot substitute for its exact finished owned child."""
    saved = model.get('_view_measurement')
    ids = model.get('operation_ids', [])
    child = db.execute('SELECT o.*,l.phase,l.node_id FROM dataops_operations o JOIN dbt_run_operations l '
        'ON l.operation_id=o.id WHERE l.run_id=? AND o.id=?',
        (run['id'], ids[0] if len(ids) == 1 else '')).fetchone()
    expected = ds._json(dict(payload=dict(run_id=run['id'], node_id=model['unique_id']), context=run['context']))
    if (not saved or saved.get('kind') != 'VIEW' or model['state'] != 'BUILT' or child is None
            or child['phase'] != 'build' or child['node_id'] != model['unique_id']
            or child['route'] != 'dbt-view-create' or child['state'] != 'FINISHED' or not child['committed'] or child['cleanup_error']
            or child['actor'] != run['actor'] or json.loads(child['context']) != run['context']
            or child['payload'] != expected or child['fingerprint'] != hashlib.sha256(expected.encode()).hexdigest()
            or json.loads(child['result'] or '{}').get('view_digest') != hashlib.sha256(ds._json(saved).encode()).hexdigest()
            or json.loads(child['result'] or '{}').get('view') != _view_public(saved)
            or model['candidate'] != _view_public(saved)):
        raise QueryError('DBT_VIEW_CHANGED', 409)
    ref = registry.ResourceRef(**saved['ref'])
    obj = registry.get_object(db, ref.object_id)
    owned = db.execute('SELECT * FROM dataops_owned_objects WHERE operation_id=? AND object_id=?',
        (child['id'], ref.object_id)).fetchone()
    locator = model['_view_locator']
    if (obj is None or obj.kind != 'VIEW' or obj.backend_identity != saved['view_uuid']
            or obj.schema_id != ref.schema_id or obj.incarnation != ref.incarnation
            or owned is None or owned['incarnation'] != ref.incarnation
            or ref.database_id != run['context']['database_id'] or ref.schema_id != run['context']['schema_id']
            or locator != dict(database_id=ref.database_id, schema_id=ref.schema_id, physical_name=obj.physical_name)):
        raise QueryError('DBT_VIEW_CHANGED', 409)
    return saved


def _view_public(saved):
    return {k: saved[k] for k in ('kind', 'ref', 'view_uuid', 'version', 'security')}


def _view_receipts(db, run, plan, *, complete=False):
    planned = {m['unique_id'] for m in plan['models'] if m['materialized'] == 'view'}
    views = [m for m in run['models'] if m['materialized'] == 'view']
    if len(views) != len(planned) or {m['unique_id'] for m in views} != planned:
        raise QueryError('DBT_VIEW_CHANGED', 409)
    if any(m.get('view_security') != 'INVOKER' for m in views):
        raise QueryError('DBT_VIEW_CHANGED', 409)
    if complete:
        declared = {m['unique_id']: m['materialized'] for m in plan['models']}
        if (len(run['models']) != len(declared)
                or {m['unique_id']: m['materialized'] for m in run['models']} != declared
                or any(m['state'] != ('EPHEMERAL' if m['materialized'] == 'ephemeral' else 'BUILT')
                    for m in run['models'])):
            raise QueryError('DBT_TEST_GATE_FAILED', 409)
    return [_view_saved(db, run, m) for m in views
        if complete or m['state'] == 'BUILT' or m.get('_view_measurement')]


def _verify_views(metadata, run, *, complete=False):
    # During BUILDING only earlier views exist. READY/publication require all
    # declared models and owned VIEW receipts, including independent final views.
    with control_store.connect() as db:
        plan = _check_plan(db, run)
        saved = _view_receipts(db, run, plan, complete=complete)
    for expected in saved:
        actual = metadata._dbt_measure_view(registry.ResourceRef(**expected['ref']))
        if actual != expected:
            raise QueryError('DBT_VIEW_CHANGED', 409)


def _view_binding(execution, ref, *, expected=None):
    """Private scoped read: no descriptors or general WAP VIEW inputs accepted."""
    scope = ds._dbt.get()
    if scope is None or scope[:2] != (execution.principal, execution._context()) or scope[6] not in ('build', 'test'):
        raise QueryError('DBT_CONTINUATION_REQUIRED', 409)
    if _live.get(scope[5], ())[:5] != scope[:5]:
        raise QueryError('DBT_OWNER_LOST', 409)
    with control_store.connect() as db:
        _check_child(db, execution.principal, execution._context(), *scope[5:])
        if hasattr(execution, 'id'):
            _check_operation(db, execution.id)
            link = db.execute('SELECT * FROM dbt_run_operations WHERE operation_id=?', (execution.id,)).fetchone()
            if link is None or tuple(link[k] for k in ('run_id', 'phase', 'node_id')) != scope[5:]:
                raise QueryError('DBT_CONTINUATION_REQUIRED', 409)
        _, run = _row(db, scope[5])
        plan = _check_plan(db, run)
        node = next(n for n in plan['tests' if scope[6] == 'test' else 'models'] if n['unique_id'] == scope[7])
        if run.get('_source_bindings') != plan['source_bindings'] or run.get('_models') != plan['models']:
            raise QueryError('DBT_PLAN_CHANGED', 409)
        normalized = _normalized(run, node)
        if asdict(ref) not in [asdict(r) for r in normalized['sources'].values()]:
            raise QueryError('INVALID_BINDING', 409)
        matches = [m for m in run['models'] if (m.get('candidate') or {}).get('ref') == asdict(ref)]
        if len(matches) != 1:
            raise QueryError('DBT_VIEW_CHANGED', 409)
        saved = _view_saved(db, run, matches[0])
        if expected is not None and saved != expected:
            raise QueryError('DBT_VIEW_CHANGED', 409)
    if execution._dbt_measure_view(ref) != saved:
        raise QueryError('DBT_VIEW_CHANGED', 409)
    return saved


def _build_view(ident, principal, context, parent, frozen, credential, uid):
    with ds._dbt_scope(principal, context, parent, frozen, credential, ident, 'build', uid):
        execution = ds.begin(principal, context, route='dbt-view-create', idempotency_key=ident + ':' + uid,
            payload=dict(run_id=ident, node_id=uid))
        if execution.replayed:
            raise QueryError('OPERATION_OBSERVATION_ONLY', 409)
        with ds._accepted(execution):
            try:
                with control_store.connect() as db:
                    _, run = _row(db, ident)
                    model = next(m for m in run['models'] if m['unique_id'] == uid)
                    model.update(operation_ids=[execution.id], _view_locator=asdict(registry.DestinationLocator(
                        context.database_id, context.schema_id, 'dbtv_' + hashlib.sha256(execution.id.encode()).hexdigest()[:24])))
                    _write(db, run)
                ref = execution._dbt_create_view()
                # Preserve known identity even if the following REST measurement fails.
                with control_store.connect() as db:
                    _, run = _row(db, ident)
                    next(m for m in run['models'] if m['unique_id'] == uid)['_created_view_ref'] = asdict(ref)
                    _write(db, run)
                measured = execution._dbt_measure_view(ref)
                current = _row_run(ident)
                selected = next(m for m in current['models'] if m['unique_id'] == uid)
                if measured['_canonical_definition_digest'] != selected['_view_definition_digest']:
                    raise QueryError('DBT_VIEW_CHANGED', 409)
                execution.complete(dict(view=_view_public(measured),
                    view_digest=hashlib.sha256(ds._json(measured).encode()).hexdigest()))
                with control_store.connect() as db:
                    _, run = _row(db, ident)
                    model = next(m for m in run['models'] if m['unique_id'] == uid)
                    model.update(state='BUILT', candidate=_view_public(measured), _view_measurement=measured)
                    _write(db, run)
            except Exception as error:
                wap._settle_definite(execution, context, error)
                if execution._row()['committed']:
                    with control_store.connect() as db:
                        _, run = _row(db, ident)
                        next(m for m in run['models'] if m['unique_id'] == uid)['state'] = 'COMMITTED_RECORDING_FAILED'
                        _write(db, run)
                raise


def _table_properties(execution, *, build=None):
    """Only the exact admitted build/publish child may read saved typed properties."""
    scope = ds._dbt.get()
    if scope is None:
        return {}
    phase = 'build' if build is None else 'publish'
    if scope[:2] != (execution.principal, execution._context()) or scope[6] != phase:
        raise QueryError('DBT_CONTINUATION_REQUIRED', 409)
    with control_store.connect() as db:
        _check_child(db, execution.principal, execution._context(), *scope[5:])
        link = db.execute('SELECT * FROM dbt_run_operations WHERE operation_id=?', (execution.id,)).fetchone()
        if link is None or tuple(link[k] for k in ('run_id', 'phase', 'node_id')) != scope[5:]:
            raise QueryError('DBT_CONTINUATION_REQUIRED', 409)
        _, run = _row(db, scope[5])
        if phase == 'publish':
            if run['kind'] != 'publication' or run['build_id'] != build['id']:
                raise QueryError('DBT_CONTINUATION_REQUIRED', 409)
            _, source = _row(db, run['parent_run_id'])
            model, saved_build = _gate(db, source, scope[7], publication=run['id'])
            if saved_build != build:
                raise QueryError('DBT_CANDIDATE_CHANGED', 409)
        else:
            if run['kind'] != 'run':
                raise QueryError('DBT_CONTINUATION_REQUIRED', 409)
            source = run
        plan = _check_plan(db, source)
        if 'compatibility' not in plan:
            return {}  # Historical admitted runs retain their original SQL semantics.
        if not _supported_contract(plan):
            raise QueryError('DBT_PLAN_CHANGED', 409)
        matches = [m for m in plan['models'] if m['unique_id'] == scope[7] and m['materialized'] == 'table']
        if len(matches) != 1 or matches[0].get('table_properties') not in ({}, {'format': 'PARQUET'}):
            raise QueryError('DBT_PLAN_CHANGED', 409)
        return dict(matches[0]['table_properties'])


def _test_select(execution):
    scope = ds._dbt.get()
    if scope is None or scope[6] != 'test':
        raise QueryError('DBT_CONTINUATION_REQUIRED', 409)
    with control_store.connect() as db:
        _check_child(db, execution.principal, execution._context(), *scope[5:])
        link = db.execute('SELECT * FROM dbt_run_operations WHERE operation_id=?', (execution.id,)).fetchone()
        if link is None or tuple(link[k] for k in ('run_id', 'phase', 'node_id')) != scope[5:]:
            raise QueryError('DBT_CONTINUATION_REQUIRED', 409)
        _, run = _row(db, scope[5])
        plan = _check_plan(db, run)
    test = next(t for t in plan['tests'] if t['unique_id'] == scope[7])
    return _normalized(run, test)


def _gate(db, run, node_id, publication=None):
    plan = _check_plan(db, run)
    expected = {t['unique_id'] for t in plan['tests']}
    actual = [t['unique_id'] for t in run['tests']]
    if (run['kind'] != 'run' or run['state'] != 'READY' or run['cancel_requested']
            or len(actual) != len(expected) or set(actual) != expected
            or any(t['state'] != 'PASSED' or t['failure_count'] != 0 for t in run['tests'])):
        raise QueryError('DBT_TEST_GATE_FAILED', 409)
    for test in run['tests']:
        child = db.execute('SELECT o.state,o.result,l.node_id,l.phase FROM dataops_operations o '
            'JOIN dbt_run_operations l ON l.operation_id=o.id WHERE o.id=? AND l.run_id=?',
            (test['operation_id'], run['id'])).fetchone()
        if (child is None or child['state'] != 'FINISHED' or child['phase'] != 'test'
                or child['node_id'] != test['unique_id'] or json.loads(child['result'] or '{}').get('failure_count') != 0):
            raise QueryError('DBT_TEST_GATE_FAILED', 409)
    matches = [m for m in run['models'] if m['unique_id'] == node_id and m['materialized'] == 'table']
    if len(matches) != 1:
        raise QueryError('DBT_NODE_NOT_FOUND', 404)
    model = matches[0]
    if model['state'] != 'BUILT' or not model['build_id'] or not model['candidate']:
        raise QueryError('DBT_TEST_GATE_FAILED', 409)
    if model.get('publication_id') and model['publication_id'] != publication:
        raise QueryError('DBT_PUBLICATION_EXISTS', 409)
    built_row = db.execute('SELECT record FROM wap_builds WHERE id=?', (model['build_id'],)).fetchone()
    if built_row is None:
        raise QueryError('DBT_CANDIDATE_CHANGED', 409)
    built = json.loads(built_row[0])
    head = db.execute('SELECT version FROM wap_models WHERE database_id=? AND model_id=?',
        (run['context']['database_id'], model['wap_model_id'])).fetchone()
    if (built['model_id'] != model['wap_model_id'] or built['version'] != model['wap_version']
            or head is None or head['version'] != model['wap_version']
            or built['stage'] != model['_measurement']
            or dict(ref=built['stage']['ref'], snapshot=built['stage']['snapshot']) != model['candidate']
            or built['destination'] != plan['_targets'][node_id]
            or built['actor'] != run['actor'] or built['context'] != run['context']):
        raise QueryError('DBT_CANDIDATE_CHANGED', 409)
    return model, built


def _guard_id(principal, context, action, build_id):
    with control_store.connect() as db:
        row = db.execute('SELECT record FROM wap_builds WHERE id=? AND database_id=?',
            (build_id, context.database_id)).fetchone()
    if row:
        _guard(principal, context, action, build=json.loads(row[0]))


def _guard(principal, context, action, *, model_id=None, build=None):
    """All existing WAP mutations fail closed for a durable dbt-managed model."""
    model_id = model_id if model_id is not None else build['model_id']
    with control_store.connect() as db:
        managed = []
        for row in db.execute("SELECT record FROM dbt_runs WHERE database_id=? AND json_extract(record,'$.kind')='run'",
                (context.database_id,)):
            run = json.loads(row[0])
            managed.extend((run, m) for m in run['models'] if m['wap_model_id'] == model_id)
        if not managed:
            return
        if len(managed) != 1:
            raise QueryError('DBT_CONTINUATION_REQUIRED', 409)
        run, model = managed[0]
        scope = ds._dbt.get()
        if scope is None or scope[:2] != (principal, context) or scope[7] != model['unique_id']:
            raise QueryError('DBT_CONTINUATION_REQUIRED', 409)
        _check_child(db, principal, context, *scope[5:])
        if action in ('save', 'build'):
            if scope[5] != run['id'] or scope[6] != 'build' or model['state'] != 'BUILDING':
                raise QueryError('DBT_CONTINUATION_REQUIRED', 409)
        else:
            _, publication = _row(db, scope[5])
            if (publication['kind'] != 'publication' or publication['parent_run_id'] != run['id']
                    or scope[6] != action or publication['build_id'] != model['build_id']):
                raise QueryError('DBT_CONTINUATION_REQUIRED', 409)
            _gate(db, run, model['unique_id'], publication=publication['id'])
            if build is None or build['id'] != model['build_id']:
                raise QueryError('DBT_CANDIDATE_CHANGED', 409)


def submit_publication(principal, context, run_id, *, key, node_id):
    _access(principal, context, admin=True)
    route = '/api/v1/dbt/runs/' + run_id + '/publish'
    payload = ds._json(dict(context=asdict(context), node_id=node_id))
    with control_store.connect() as db:
        run = _authorized(db, principal, context, run_id, 'run')
        replay = _request(db, principal, context, route, key, payload)
        if replay:
            return replay
        model, _ = _gate(db, run, node_id)
    record = _base(principal, context, 'publication', run)
    record.update(run_id=run_id, parent_run_id=run_id, plan_id=run['plan_id'], _plan_digest=run['_plan_digest'],
        node_id=node_id, build_id=model['build_id'], audit_id=None, publication_id=None,
        _phase=None, _node_id=node_id, committed=False)
    def check(db):
        current = _authorized(db, principal, context, run_id, 'run')
        selected, _ = _gate(db, current, node_id)
        selected['publication_id'] = record['id']
        _write(db, current)
    return _accept(principal, context, route, key, payload, record, check)


def _phase(ident, state, phase, node):
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        _, run = _row(db, ident)
        if run['cancel_requested']:
            raise QueryError('DBT_RUN_STOPPED', 409)
        run.update(state=state, _phase=phase, _node_id=node)
        if run['kind'] == 'run':
            item = next(n for n in run['tests' if phase == 'test' else 'models'] if n['unique_id'] == node)
            item['state'] = 'TESTING' if phase == 'test' else 'BUILDING'
        _write(db, run)
        return run


def _build_run(ident, principal, context, parent, frozen, credential):
    metadata = _access(principal, context)
    with control_store.connect() as db:
        _, run = _row(db, ident)
        plan = _check_plan(db, run)
    pins = {}
    for source in plan['source_bindings']:
        ref = registry.ResourceRef(**source['ref'])
        identity = ds._json(source['ref'])
        if identity not in pins:
            pins[identity] = metadata.wap_measure(ref)
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        _, run = _row(db, ident)
        if run['cancel_requested']:
            raise QueryError('DBT_RUN_STOPPED', 409)
        run.update(_pins=pins, _source_bindings=plan['source_bindings'], _models=plan['models'])
        _write(db, run)
    for model in plan['models']:
        uid = model['unique_id']
        if model['materialized'] == 'ephemeral':
            with control_store.connect() as db:
                _, run = _row(db, ident)
                next(m for m in run['models'] if m['unique_id'] == uid)['state'] = 'EPHEMERAL'
                _write(db, run)
            continue
        run = _phase(ident, 'BUILDING', 'build', uid)
        selected = next(m for m in run['models'] if m['unique_id'] == uid)
        normalized = _normalized(run, model)
        if model['materialized'] == 'view':
            _build_view(ident, principal, context, parent, frozen, credential, uid)
            continue
        target = plan['_targets'][uid]
        target = registry.ResourceRef(**target) if 'object_id' in target else registry.DestinationLocator(**target)
        with ds._dbt_scope(principal, context, parent, frozen, credential, ident, 'build', uid):
            saved = wap.save_model(principal, context, model_id=selected['wap_model_id'],
                sql=normalized['sql'], sources=normalized['sources'], target=target, expected_version=None)
            with control_store.connect() as db:
                _, run = _row(db, ident)
                next(m for m in run['models'] if m['unique_id'] == uid)['wap_version'] = saved['version']
                _write(db, run)
            child = wap.build(principal, context, key=ident + ':' + uid,
                model_id=saved['model_id'], version=saved['version'])
        _verify_views(metadata, run)
        built = (child.get('result') or {}).get('build')
        if child['state'] != 'FINISHED' or built is None:
            raise QueryError('DBT_CHILD_UNRESOLVED', 409)
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            _, run = _row(db, ident)
            selected = next(m for m in run['models'] if m['unique_id'] == uid)
            selected.update(state='BUILT', build_id=built['id'], _measurement=built['stage'],
                candidate=dict(ref=built['stage']['ref'], snapshot=built['stage']['snapshot']), operation_ids=[child['id']])
            _write(db, run)
    for test in plan['tests']:
        uid = test['unique_id']
        _phase(ident, 'TESTING', 'test', uid)
        with ds._dbt_scope(principal, context, parent, frozen, credential, ident, 'test', uid):
            execution = ds.begin(principal, context, route='dbt-test', idempotency_key=ident + ':' + uid,
                payload=dict(run_id=ident, node_id=uid))
            if execution.replayed:
                raise QueryError('OPERATION_OBSERVATION_ONLY', 409)
            try:
                count = execution._dbt_test()
                _verify_views(metadata, _row_run(ident))
                child = execution.complete(dict(failure_count=count))
            except Exception as error:
                with control_store.connect() as db:
                    canceled = _row(db, ident)[1]['cancel_requested']
                if canceled:
                    try:
                        execution.cancel()
                    except QueryError:
                        pass  # An uncertain backend cancellation stays unresolved.
                wap._settle_definite(execution, context, error)
                raise
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            _, run = _row(db, ident)
            selected = next(t for t in run['tests'] if t['unique_id'] == uid)
            selected.update(state='PASSED' if count == 0 else 'FAILED', failure_count=count, operation_id=child['id'])
            _write(db, run)
    _verify_views(metadata, _row_run(ident), complete=True)
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        _, run = _row(db, ident)
        if run['cancel_requested']:
            raise QueryError('DBT_RUN_STOPPED', 409)
        _view_receipts(db, run, _check_plan(db, run), complete=True)
        if any(t['state'] != 'PASSED' for t in run['tests']):
            raise QueryError('DBT_TEST_FAILED', 409)
        run.update(state='READY', _phase=None, _node_id=None)
        _write(db, run)


def _publish_run(ident, principal, context, parent, frozen, credential):
    with control_store.connect() as db:
        _, publication = _row(db, ident)
    _verify_views(_access(principal, context), _row_run(publication['parent_run_id']), complete=True)
    uid = publication['node_id']
    _phase(ident, 'AUDITING', 'audit', uid)
    with ds._dbt_scope(principal, context, parent, frozen, credential, ident, 'audit', uid):
        child = wap.audit(principal, context, key=ident + ':audit', build_id=publication['build_id'])
    result = child.get('result') or {}
    if child['state'] != 'FINISHED' or not result.get('audit', {}).get('passed'):
        raise QueryError('DBT_AUDIT_FAILED', 409)
    with control_store.connect() as db:
        _, publication = _row(db, ident)
        publication['audit_id'] = result['audit_id']
        _write(db, publication)
    _phase(ident, 'PUBLISHING', 'publish', uid)
    with ds._dbt_scope(principal, context, parent, frozen, credential, ident, 'publish', uid):
        child = wap.publish(principal, context, key=ident + ':publish', build_id=publication['build_id'], audit_id=result['audit_id'])
    with control_store.connect() as db:
        _, publication = _row(db, ident)
        result = child.get('result') or {}
        publication.update(publication_id=result.get('publication_id'), committed=child['committed'])
        if child['committed']:
            publication['state'] = 'PUBLISHED' if result.get('publication') and not child.get('cleanup_error') else 'COMMITTED_RECORDING_FAILED'
        else:
            publication['state'] = 'UNKNOWN' if child['state'] == 'UNKNOWN' else 'FAILED'
        _write(db, publication)


def _start(ident):
    threading.Thread(target=_execute, args=(ident,), daemon=True, name='dbt-' + ident).start()


def _execute(ident):
    principal, context, parent, frozen, credential, owner = _live[ident]
    try:
        with control_store.connect() as db:
            _, run = _row(db, ident)
        if run['kind'] == 'plan':
            _compile(ident, principal, context)
        else:
            with admission.accepted(parent):
                if run['kind'] == 'run':
                    _build_run(ident, principal, context, parent, frozen, credential)
                else:
                    _publish_run(ident, principal, context, parent, frozen, credential)
    except Exception as error:
        with control_store.connect() as db:
            _, run = _row(db, ident)
            run['error_code'] = getattr(error, 'code', 'DBT_EXECUTION_FAILED')
            if run['kind'] == 'plan':
                run['state'] = 'FAILED'
                import database_dbt as compiler
                run['compatibility'] = getattr(error, 'compatibility', compiler.compatibility(supported=False))
            _write(db, run)
    finally:
        try:
            if parent:
                _settle(ident)
        finally:
            _live.pop(ident, None)
            owner.close()


def _settle(ident, *, owner_lost=False):
    # Reuse the existing positive child/query closure proof. It never issues SQL.
    from db_marts import _closed_child
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row, run = _row(db, ident)
        operations = [dict(r) for r in db.execute('SELECT o.*,l.phase,l.node_id FROM dbt_run_operations l '
            'JOIN dataops_operations o ON o.id=l.operation_id WHERE l.run_id=?', (ident,))]
        closed = all([_closed_child(db, op['id']) for op in operations])
        parent = admission.get_for_subject(db, kind='dbt', subject_id=ident, warehouse_id=run['context']['warehouse_id'])
        # Include orphan admissions, never infer closure from a partial link set.
        children = list(db.execute('SELECT subject_id,kind,state FROM warehouse_admissions WHERE parent_id=?', (parent.id,)))
        closed = closed and {c['subject_id'] for c in children} == {o['id'] for o in operations} and all(c['kind'] == 'dataops' and c['state'] in admission.TERMINAL for c in children)
        committed = next((op for op in operations if op['phase'] == 'publish' and op['committed']), None)
        if run['kind'] == 'publication' and committed:
            receipt = json.loads(committed['result'] or '{}')
            run.update(committed=True, publication_id=receipt.get('publication_id'))
            artifact = db.execute('SELECT id FROM wap_publications WHERE id=? AND operation_id=?',
                (receipt.get('publication_id'), committed['id'])).fetchone()
            run['state'] = 'PUBLISHED' if artifact and closed and not committed['cleanup_error'] else 'COMMITTED_RECORDING_FAILED'
        elif not closed:
            run['state'] = 'UNKNOWN'
        elif run['cancel_requested']:
            run['state'] = 'CANCELED'
        elif run['state'] not in ('READY', 'PUBLISHED', 'FAILED', 'CANCELED', 'COMMITTED_RECORDING_FAILED'):
            run['state'] = 'FAILED'
        if owner_lost and not run['error_code'] and run['state'] == 'FAILED':
            run['error_code'] = 'DBT_OWNER_LOST'
        for item in run.get('models', []) + run.get('tests', []):
            if item['state'] in ('QUEUED', 'BUILDING', 'TESTING'):
                item['state'] = 'CANCELED' if run['cancel_requested'] and closed else 'UNKNOWN' if not closed else 'FAILED'
        run.update(_phase=None, _node_id=None)
        _write(db, run)
        admission.finish(db, parent, state='FINISHED' if closed else 'UNKNOWN',
            evidence_ref='dbt-child-closure:' + ident if closed else None)


def _observe(principal, context, ident, kind):
    _access(principal, context)
    with control_store.connect() as db:
        record = _authorized(db, principal, context, ident, kind)
    if ident not in _live and record['state'] in _ACTIVE | {'UNKNOWN', 'COMMITTED_RECORDING_FAILED'}:
        try:
            owner = queries._acquire_query_owner('dbt-' + ident)
        except QueryError:
            owner = None
        if owner:
            try:
                if kind == 'plan':
                    with control_store.connect() as db:
                        _, current = _row(db, ident)
                        current.update(state='FAILED', error_code='DBT_OWNER_LOST')
                        _write(db, current)
                else:
                    _settle(ident, owner_lost=True)
            finally:
                owner.close()
    with control_store.connect() as db:
        return _public(db, _authorized(db, principal, context, ident, kind))


def get_plan(principal, context, plan_id):
    return _observe(principal, context, plan_id, 'plan')


def get_run(principal, context, run_id):
    return _observe(principal, context, run_id, 'run')


def get_publication(principal, context, publication_id):
    return _observe(principal, context, publication_id, 'publication')


def _list(principal, context, kind, plan_id=None):
    _access(principal, context)
    with control_store.connect() as db:
        revalidate_admission(db, principal, context)
        ids = [r[0] for r in db.execute("SELECT id FROM dbt_runs WHERE database_id=? AND json_extract(record,'$.kind')=? "
            "AND json_extract(record,'$.context.schema_id')=? AND json_extract(record,'$.context.warehouse_id')=? "
            "AND (json_extract(record,'$.actor')=? OR ?='admin') "
            "AND (json_extract(record,'$.context.selected_role')=? OR ?='admin') "
            "AND (? IS NULL OR json_extract(record,'$.plan_id')=?) ORDER BY rowid DESC LIMIT 50",
            (context.database_id, kind, context.schema_id, context.warehouse_id, principal.principal_id,
             context.selected_role, context.selected_role, context.selected_role, plan_id, plan_id))]
    return [_observe(principal, context, ident, kind) for ident in ids]


def list_plans(principal, context):
    return _list(principal, context, 'plan')


def list_runs(principal, context, plan_id=None):
    return _list(principal, context, 'run', plan_id)


def cancel_run(principal, context, run_id, *, key):
    ds._cancel_account(principal)
    route = '/api/v1/dbt/runs/' + run_id + '/cancel'
    payload = ds._json(dict(context=asdict(context)))
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        run = _cancel_owner(db, principal, context, run_id)
        replay = _request(db, principal, context, route, key, payload, cleanup=True)
        if replay:
            return replay
        run.update(cancel_requested=True, state='CANCELING')
        _write(db, run)
        # Publications already accepted for this run must latch as well.
        for row in db.execute("SELECT record FROM dbt_runs WHERE json_extract(record,'$.parent_run_id')=?", (run_id,)).fetchall():
            publication = json.loads(row[0])
            if publication['state'] in _ACTIVE:
                publication.update(cancel_requested=True, state='CANCELING')
                _write(db, publication)
        _receipt(db, principal, route, key, payload, run)
    with control_store.connect() as db:
        operations = [r[0] for r in db.execute('SELECT operation_id FROM dbt_run_operations WHERE run_id=?', (run_id,))]
    for operation in operations:
        try:
            ds.Execution(principal, operation).cancel()
        except QueryError:
            pass  # Other-process owner observes the persisted latch before its next send/fetch.
    if run_id not in _live:
        with control_store.connect() as db:
            parent = db.execute('SELECT admission_id FROM dbt_runs WHERE id=?', (run_id,)).fetchone()[0]
        if parent:
            try:
                owner = queries._acquire_query_owner('dbt-' + run_id)
            except QueryError:
                owner = None
            if owner:
                try:
                    _settle(run_id, owner_lost=True)
                finally:
                    owner.close()
    with control_store.connect() as db:
        return _cancel_receipt(db, principal, context, _cancel_owner(db, principal, context, run_id))


def reconcile_run(principal, context, run_id, *, key):
    _access(principal, context)
    route = '/api/v1/dbt/runs/' + run_id + '/reconcile'
    payload = ds._json(dict(context=asdict(context)))
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        run = _authorized(db, principal, context, run_id, 'run')
        replay = _request(db, principal, context, route, key, payload)
        if replay:
            return replay
        # An administrator may inspect E metadata and reconcile only A's own
        # publication children, never gain access to E's raw query results.
        candidates = [run] + [json.loads(r[0]) for r in db.execute(
            "SELECT record FROM dbt_runs WHERE json_extract(record,'$.parent_run_id')=?", (run_id,))]
        owned = [r for r in candidates if r['actor'] == principal.principal_id and r['context'] == asdict(context)]
        if not owned:
            raise QueryError('CONTEXT_MISMATCH', 409)
        _receipt(db, principal, route, key, payload, run)
        ids = [(r['id'], [q[0] for q in db.execute('SELECT d.query_id FROM dbt_run_operations l JOIN dataops_queries d '
            'ON d.operation_id=l.operation_id JOIN query_records q ON q.id=d.query_id '
            "WHERE l.run_id=? AND q.state IN ('QUEUED','RUNNING','UNKNOWN')", (r['id'],))]) for r in owned]
    for ident, children in ids:
        for query_id in children:
            # Existing reconciler observes Trino metadata with GET only.
            queries.reconcile_query_from_backend(principal, query_id, selected_role=context.selected_role)
        _observe(principal, context, ident, 'run' if ident == run_id else 'publication')
    return get_run(principal, context, run_id)
