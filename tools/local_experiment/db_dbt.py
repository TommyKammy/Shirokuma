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
_UNREVIEWED = object()
_ABSENT_NODE = object()


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
        if record['state'] in _ACTIVE or record['state'] in ('READY', 'NATIVE_VERIFIED'):
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
    if record.get('executor') == 'native_table' and record['kind'] == 'run':
        try:
            _native_publication_gate(db, record, record['selected_node_id'])
            result['publication_allowed'] = True
        except (QueryError, KeyError, TypeError, ValueError):
            result['publication_allowed'] = False
    if record.get('executor') == 'native_graph' and record['kind'] == 'run':
        result['publication_allowed'] = False
        for model in record['models']:
            try:
                _native_publication_gate(db, record, model['unique_id'])
                result['publication_allowed'] = True
                break
            except (QueryError, KeyError, TypeError, ValueError):
                pass
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
        for field in ('candidate', 'candidate_locator', 'pins', 'native_receipt'):
            result.pop(field, None)
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
    if '_native_tests' in plan:
        fields += ('_native_tests',)
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
    owners = summary.get('test_owners')
    if (isinstance(owners, dict) and set(owners) == {t['unique_id'] for t in summary['tests']}
            and all(type(uid) is str and (owner is None or type(owner) is str) for uid, owner in owners.items())):
        plan['_native_tests'] = dict(version=1, owners=owners)
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


def _run_arguments(executor, node_id):
    if type(executor) is not str or executor not in ('sql_wap', 'native_table', 'native_graph'):
        raise ValueError('invalid executor')
    if executor == 'native_table':
        if type(node_id) is not str or not re.fullmatch(r'[A-Za-z0-9_.-]{1,512}', node_id):
            raise ValueError('native node ID required')
    elif node_id is not None:
        raise ValueError('node ID requires native executor')


def submit_run(principal, context, *, key, plan_id, executor='sql_wap', node_id=_ABSENT_NODE, native_tests=False):
    if executor == 'native_graph' and node_id is not _ABSENT_NODE:
        raise ValueError('native_graph requires absent node ID')
    if node_id is _ABSENT_NODE:
        node_id = None
    _run_arguments(executor, node_id)
    if type(native_tests) is not bool or native_tests and executor not in ('native_table', 'native_graph'):
        raise ValueError('native_tests requires native executor and strict boolean')
    if type(plan_id) is not str or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', plan_id):
        raise ValueError('invalid plan ID')
    _access(principal, context)
    route = '/api/v1/dbt/runs'
    intent = dict(context=asdict(context), plan_id=plan_id)
    if native_tests or executor == 'native_graph':
        intent['native_tests'] = native_tests
    with control_store.connect() as db:
        if executor in ('native_table', 'native_graph'):
            old = db.execute('SELECT payload FROM dbt_requests WHERE actor=? AND route=? AND idempotency_key=?',
                (principal.principal_id, route, key)).fetchone()
            intent.update(executor=executor, plan_digest=json.loads(old['payload']).get('plan_digest') if old else None)
            if executor == 'native_table':
                intent['node_id'] = node_id
        payload = ds._json(intent)
        replay = _request(db, principal, context, route, key, payload)
        if replay:
            return replay
        plan = _authorized(db, principal, context, plan_id, 'plan')
    if executor == 'native_graph':
        import database_dbt_graph as graph
        import database_dbt_native as native
        metadata = _access(principal, context)
        with control_store.connect() as db:
            native._saved_plan(db, principal, context, plan)
        graph.validate(metadata, context, plan, tests=native_tests)
        intent['plan_digest'] = plan['_plan_digest']
        record = _base(principal, context, 'run', plan)
        record.update(plan_id=plan_id, _plan_digest=plan['_plan_digest'], _pins=None, _phase=None, _node_id=None,
            executor=executor, native_tests=native_tests, native_verified=False, publication_allowed=False,
            tests_status='QUEUED' if native_tests else 'NOT_RUN', task_receipts={},
            models=[dict(unique_id=m['unique_id'], name=m['name'], materialized=m['materialized'],
                table_properties=m['table_properties'], state='QUEUED', candidate=None, operation_ids=[])
                for m in plan['models']],
            tests=[dict(unique_id=t['unique_id'], name=t['name'], test_type=t['test_type'],
                state='QUEUED', failure_count=None, operation_id=None) for t in plan['tests']] if native_tests else [])
        return _accept(principal, context, route, key, ds._json(intent), record)
    if executor == 'native_table':
        import database_dbt_native as native
        plan, node, _, _ = native._plan(principal, context, plan_id, node_id)
        suite = native.suite(plan, node_id) if native_tests else []
        intent['plan_digest'] = plan['_plan_digest']
        payload = ds._json(intent)
        record = _base(principal, context, 'run', plan)
        record.update(plan_id=plan_id, _plan_digest=plan['_plan_digest'], _pins=None, _phase=None, _node_id=None,
            executor=executor, selected_node_id=node_id, tests_status='NOT_RUN', publication_allowed=False,
            native_verified=False, tests=[], models=[dict(unique_id=node_id, name=node['name'],
                materialized='table', table_properties=node['table_properties'], state='QUEUED',
                candidate=None, operation_ids=[])])
        if native_tests:
            record.update(native_tests=True, _native_test_suite=suite, tests_status='QUEUED',
                tests=[dict(unique_id=t['unique_id'], name=t['name'], test_type=t['test_type'],
                    state='QUEUED', failure_count=None, operation_id=None) for t in suite])
        return _accept(principal, context, route, key, payload, record)
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


def _native_node(db, run, phase, node_id):
    if run.get('executor') == 'native_graph':
        import database_dbt_graph as graph
        return graph.node_allowed(db, run, phase, node_id)
    if phase == 'build':
        return node_id == run['selected_node_id']
    if phase == 'test' and run.get('native_tests') is True:
        import database_dbt_native as native
        plan = _check_plan(db, run)
        suite = native.suite(plan, run['selected_node_id'])
        return suite == run.get('_native_test_suite') and node_id in {t['unique_id'] for t in suite}
    return False


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
    if run.get('executor') in ('native_table', 'native_graph') and not _native_node(db, run, phase, node_id):
        raise QueryError('DBT_NODE_NOT_FOUND', 409)
    if phase == 'test' and run['state'] != 'TESTING' or phase == 'build' and run['state'] != 'BUILDING':
        raise QueryError('DBT_RUN_STOPPED', 409)


def _check_operation(db, operation_id):
    link = db.execute('SELECT * FROM dbt_run_operations WHERE operation_id=?', (operation_id,)).fetchone()
    if link:
        op = db.execute('SELECT * FROM dataops_operations WHERE id=?', (operation_id,)).fetchone()
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
        if run.get('executor') in ('native_table', 'native_graph') and not _native_node(db, run, link['phase'], link['node_id']):
            raise QueryError('DBT_RUN_STOPPED', 409)
        if run.get('executor') == 'native_table':
            import database_dbt_native as native
            links = list(db.execute('SELECT o.*,l.phase,l.node_id FROM dbt_run_operations l '
                'JOIN dataops_operations o ON o.id=l.operation_id WHERE l.run_id=?', (run['id'],)))
            if not _native_links(db, run, links):
                raise QueryError('DBT_NATIVE_CHILD_INVALID', 409)
            expected = dict(plan_id=run['plan_id'], node_id=link['node_id'], digest=run['_plan_digest'],
                **(native._TEST_PROVENANCE if link['phase'] == 'test' else native._PROVENANCE))
            if link['phase'] == 'test':
                expected['selected_node_id'] = run['selected_node_id']
            if op['payload'] != ds._json(dict(payload=expected, context=run['context'])):
                raise QueryError('DBT_NATIVE_CHILD_INVALID', 409)
        if run.get('executor') == 'native_graph':
            import database_dbt_graph as graph
            graph.check_operation(db, run, op, link)
        source = _row(db, run['parent_run_id'])[1] if run['kind'] == 'publication' else run
        _source_identities(db, _check_plan(db, source))
        if run['kind'] == 'publication':
            _gate(db, source, link['node_id'], publication=run['id'])
            if run.get('origin') in ('native_table', 'native_graph'):
                _native_publisher(db, principal=live[0], context=live[1], run=source, node_id=run['node_id'])
                if (link['phase'] not in ('audit', 'publish') or link['node_id'] != run['node_id']
                        or op['route'] != 'dbt-native-' + link['phase']
                        or op['payload'] != ds._json(dict(payload=_native_child_payload(run, link['phase']), context=run['context']))):
                    raise QueryError('DBT_NATIVE_CHILD_INVALID', 409)
                if link['phase'] == 'publish' and _native_audit_saved(db, run) is None:
                    raise QueryError('AUDIT_MISMATCH', 409)


def _before_query(operation_id, principal, context):
    with control_store.connect() as db:
        link = db.execute('SELECT run_id FROM dbt_run_operations WHERE operation_id=?', (operation_id,)).fetchone()
        if link is None:
            return
        _check_operation(db, operation_id)
        _, run = _row(db, link['run_id'])
        source = _row(db, run['parent_run_id'])[1] if run['kind'] == 'publication' else run
        plan = _check_plan(db, source)
        sent = db.execute('SELECT 1 FROM dataops_queries d JOIN query_execution_contexts e ON e.query_id=d.query_id '
            "WHERE d.operation_id=? AND e.backend_query_id IS NOT NULL AND e.backend_query_id<>''", (operation_id,)).fetchone()
    metadata = _access(principal, context)
    if run.get('origin') in ('native_table', 'native_graph') and not sent:
        # This is the final initial send (after wake). Once accepted by Trino,
        # polling must be able to observe our own write changing the target.
        _native_publication_live(metadata, run)
    if source.get('executor') == 'native_graph':
        import database_dbt_graph as graph
        with control_store.connect() as db:
            graph.check_verified(db, source)
        graph.check_candidates(metadata, source)
    if source.get('executor') not in ('native_table', 'native_graph'):
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
    if run.get('executor') in ('native_table', 'native_graph'):
        return _native_publication_gate(db, run, node_id, publication)
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
            managed.extend((run, m) for m in run['models'] if m.get('wap_model_id') == model_id)
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


def _native_publication_gate(db, run, node_id, publication=None):
    """Saved native evidence, never manual-WAP artifacts or live catalog calls."""
    if run.get('executor') == 'native_graph':
        import database_dbt_graph as graph
        model, proof = graph.publication_proof(db, run, node_id, publication)
        plan = _check_plan(db, run)
    else:
        if (run['kind'] != 'run' or run.get('executor') != 'native_table'
                or run['state'] != 'NATIVE_VERIFIED' or run.get('native_verified') is not True
                or run.get('native_tests') is not True or run.get('tests_status') != 'PASSED'
                or run['cancel_requested'] or not run['tests']
                or any(t['state'] != 'PASSED' or type(t['failure_count']) is not int
                    or t['failure_count'] != 0 for t in run['tests'])):
            raise QueryError('DBT_NATIVE_PUBLICATION_FORBIDDEN', 409)
        plan = _check_plan(db, run)
        if len(run['models']) != 1 or node_id != run['selected_node_id']:
            raise QueryError('DBT_NODE_NOT_FOUND', 404)
        model = run['models'][0]
        if (model['unique_id'] != node_id or model['materialized'] != 'table'
                or model['state'] != 'NATIVE_VERIFIED'):
            raise QueryError('DBT_NATIVE_PUBLICATION_FORBIDDEN', 409)
        ids = model.get('operation_ids', [])
        proof = _native_saved(db, run, ids[0]) if len(ids) == 1 else None
        if proof is None or not _native_quality_saved(db, run, proof):
            raise QueryError('DBT_NATIVE_PUBLICATION_FORBIDDEN', 409)
        if model.get('publication_id') and model['publication_id'] != publication:
            raise QueryError('DBT_PUBLICATION_EXISTS', 409)
    target = plan['_targets'][node_id]
    schema = registry.get_schema(db, target['schema_id'])
    sandbox = registry.get_schema(db, run['context']['schema_id'])
    if (schema is None or schema.database_id != run['context']['database_id']
            or target['database_id'] != schema.database_id or schema.purpose != 'marts'
            or sandbox is None or sandbox.purpose != 'sandbox'
            or sandbox.sql_name != 'sandbox'):
        raise QueryError('DBT_NATIVE_TARGET_FORBIDDEN', 409)
    return model, proof


def _native_publisher(db, principal, context, run, node_id):
    revalidate_admission(db, principal, context)
    if context.selected_role != 'admin':
        raise QueryError('SQL_PERMISSION_DENIED', 403)
    if any(v != asdict(context)[k] for k, v in run['context'].items()
            if k not in ('selected_role', 'grant_revision')):
        raise QueryError('CONTEXT_MISMATCH', 409)
    # Validate the source's current authority without capturing/reusing its credentials.
    account = ds.request_auth._principal_accounts().get(run['actor'])
    if account is None:
        raise QueryError('DB_ACCESS_REVOKED', 403)
    actor = ds.request_auth.AuthenticatedPrincipal(run['actor'], account['role'], account['platform_admin'])
    model = next(m for m in run['models'] if m['unique_id'] == node_id and m['materialized'] == 'table')
    ids = model['operation_ids']
    op = db.execute('SELECT backend_username FROM dataops_operations WHERE id=?', (ids[0],)).fetchone()
    revalidate_admission(db, actor, ResolvedQueryContext(**run['context']), backend_username=op['backend_username'])


def _native_target(destination):
    return (registry.ResourceRef(**destination) if 'object_id' in destination
        else registry.DestinationLocator(**destination))


def _native_review(metadata, proof, destination):
    if metadata.wap_measure(registry.ResourceRef(**proof['candidate']['ref'])) != proof['candidate']:
        raise QueryError('DBT_CANDIDATE_CHANGED', 409)
    target = _native_target(destination)
    if type(target) is registry.ResourceRef:
        with control_store.connect() as db:
            obj = registry.get_object(db, target.object_id)
        if obj is None or not obj.current or obj.incarnation != target.incarnation:
            raise QueryError('STALE_RESOURCE', 409)
        measured = metadata.wap_measure(target)
    else:
        metadata._absent(target)  # Both TABLE and VIEW; never adopt a new object.
        measured = None
    return dict(version=1, destination=destination, target=measured)


def _native_descriptor(expected, destination):
    """Strict JSON descriptor; measurements are compared whole, without coercion."""
    if (not isinstance(expected, dict) or set(expected) != {'version', 'destination', 'target'}
            or type(expected['version']) is not int or expected['version'] != 1
            or ds._json(expected['destination']) != ds._json(destination)):
        raise QueryError('DBT_PUBLICATION_REVIEW_REQUIRED', 400)
    target = expected['target']
    if 'object_id' in destination:
        if (not isinstance(target, dict) or set(target) != {'ref', 'snapshot', 'schema_id', 'columns', 'schema_fingerprint'}
                or ds._json(target['ref']) != ds._json(destination) or not isinstance(target['snapshot'], str)
                or not re.fullmatch('[0-9]+', target['snapshot']) or type(target['schema_id']) is not int
                or target['schema_id'] < 0 or not isinstance(target['columns'], list) or not target['columns']
                or not isinstance(target['schema_fingerprint'], str)
                or not re.fullmatch('[0-9a-f]{64}', target['schema_fingerprint'])
                or any(not isinstance(c, dict) or set(c) != {'name', 'type'}
                    or not isinstance(c['name'], str) or not c['name'] or not isinstance(c['type'], (str, dict))
                    for c in target['columns'])):
            raise QueryError('DBT_PUBLICATION_REVIEW_REQUIRED', 400)
    elif target is not None:
        raise QueryError('DBT_PUBLICATION_REVIEW_REQUIRED', 400)
    try:
        ds._json(expected)
    except (ValueError, TypeError):
        raise QueryError('DBT_PUBLICATION_REVIEW_REQUIRED', 400) from None


def get_publication_preview(principal, context, run_id, *, node_id):
    metadata = _access(principal, context, admin=True)
    with control_store.connect() as db:
        run = _authorized(db, principal, context, run_id, 'run')
        _, proof = _native_publication_gate(db, run, node_id)
        _native_publisher(db, principal, context, run, node_id)
        plan = _check_plan(db, run)
        destination = plan['_targets'][node_id]
        schema = registry.get_schema(db, destination['schema_id'])
        node = next(m for m in plan['models'] if m['unique_id'] == node_id)
        target = dict(catalog=context.sql_catalog, schema=schema.sql_name, name=node['relation'][2])
    if run['executor'] == 'native_graph':
        import database_dbt_graph as graph
        graph.check_candidates(metadata, run)
    expected = _native_review(metadata, proof, destination)
    return dict(version=1, origin=run['executor'], run_id=run_id, plan_id=run['plan_id'], node_id=node_id,
        source_digest=run['source_digest'], context=asdict(context), source_actor=run['actor'],
        publisher_actor=principal.principal_id, source_context=run['context'], candidate=proof['candidate'],
        quality=dict(status='PASSED', count=len(run['tests']), test_ids=[t['unique_id'] for t in run['tests']]),
        target=target, expected_target=expected)


def _native_intent(run, node_id, proof, expected, context, actor):
    model = next(m for m in run['models'] if m['unique_id'] == node_id)
    intent = dict(origin=run['executor'], run_id=run['id'], plan_id=run['plan_id'], node_id=node_id,
        source_digest=run['source_digest'], plan_digest=run['_plan_digest'], source_actor=run['actor'],
        source_context=run['context'], build_operation_id=model['operation_ids'][0],
        build=proof, candidate=proof['candidate'], quality=[t['native_receipt'] for t in run['tests']],
        expected_target=expected, audit_policy=dict(min_rows=1), context=asdict(context), publisher_actor=actor)
    if run['executor'] == 'native_graph':
        intent.update(task_receipts=run['task_receipts'], pins=run['_pins'],
            tables=[dict(node_id=m['unique_id'], receipt=m['native_receipt'],
                dependency_snapshots=m['dependency_snapshots']) for m in run['models'] if m['materialized'] == 'table'])
    return intent


def _native_publication_live(metadata, publication):
    with control_store.connect() as db:
        source = _row(db, publication['parent_run_id'])[1]
        _, proof = _native_publication_gate(db, source, publication['node_id'], publication['id'])
        _native_publisher(db, metadata.principal, metadata._context(), source, publication['node_id'])
        destination = _check_plan(db, source)['_targets'][publication['node_id']]
        if publication['_native_intent'] != _native_intent(source, publication['node_id'], proof,
                publication['expected_target'], metadata._context(), metadata.principal.principal_id):
            raise QueryError('DBT_NATIVE_PUBLICATION_INVALID', 409)
    if source['executor'] == 'native_graph':
        import database_dbt_graph as graph
        graph.check_candidates(metadata, source)
    actual = _native_review(metadata, proof, destination)
    if actual != publication['expected_target']:
        raise QueryError('TARGET_CHANGED', 409)


def submit_publication(principal, context, run_id, *, key, node_id, expected_target=_UNREVIEWED):
    metadata = _access(principal, context, admin=True)
    route = '/api/v1/dbt/runs/' + run_id + '/publish'
    body = dict(context=asdict(context), node_id=node_id)
    if expected_target is not _UNREVIEWED:
        body['expected_target'] = expected_target
    payload = ds._json(body)
    with control_store.connect() as db:
        run = _authorized(db, principal, context, run_id, 'run')
        replay = _request(db, principal, context, route, key, payload)
        if replay:
            return replay
        model, proof = _gate(db, run, node_id)
        native = run.get('executor') in ('native_table', 'native_graph')
        if native:
            _native_publisher(db, principal, context, run, node_id)
            destination = _check_plan(db, run)['_targets'][node_id]
            _native_descriptor(expected_target, destination)
        elif expected_target is not _UNREVIEWED:
            raise QueryError('DBT_PUBLICATION_REVIEW_UNEXPECTED', 400)
    if native:
        try:
            if run['executor'] == 'native_graph':
                import database_dbt_graph as graph
                graph.check_candidates(metadata, run)
            if _native_review(metadata, proof, destination) != expected_target:
                raise QueryError('TARGET_CHANGED', 409)
        except QueryError as error:
            if error.code not in ('DBT_CANDIDATE_CHANGED', 'STALE_RESOURCE', 'DESTINATION_EXISTS',
                    'TARGET_CHANGED', 'SNAPSHOT_REQUIRED', 'SCHEMA_REQUIRED'):
                raise
            raise QueryError('DBT_PUBLICATION_REVIEW_CHANGED', 409) from None
        except inventory.InventoryError as error:
            if error.status != 404:
                raise
            raise QueryError('DBT_PUBLICATION_REVIEW_CHANGED', 409) from None
    record = _base(principal, context, 'publication', run)
    record.update(run_id=run_id, parent_run_id=run_id, plan_id=run['plan_id'], _plan_digest=run['_plan_digest'],
        node_id=node_id, build_id=model.get('build_id'), audit_id=None, publication_id=None,
        _phase=None, _node_id=node_id, committed=False)
    if native:
        record.update(origin=run['executor'], expected_target=expected_target,
            _native_intent=_native_intent(run, node_id, proof, expected_target, context, principal.principal_id))
    def check(db):
        current = _authorized(db, principal, context, run_id, 'run')
        selected, current_proof = _gate(db, current, node_id)
        if native:
            if current['executor'] == 'native_graph':
                import database_dbt_graph as graph
                graph.check_candidates(metadata, current)
                if _native_review(metadata, current_proof, destination) != expected_target:
                    raise QueryError('DBT_PUBLICATION_REVIEW_CHANGED', 409)
            _native_publisher(db, principal, context, current, node_id)
            if record['_native_intent'] != _native_intent(current, node_id, current_proof,
                    expected_target, context, principal.principal_id):
                raise QueryError('DBT_NATIVE_PUBLICATION_INVALID', 409)
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


def _native_links(db, run, links):
    """Exact bounded child set; unfinished expected tests do not invalidate build proof."""
    allowed = {('build', run['selected_node_id']): 'dbt-native-table'}
    if run.get('native_tests') is True:
        import database_dbt_native as native
        suite = native.suite(_check_plan(db, run), run['selected_node_id'])
        if suite != run.get('_native_test_suite') or [t['unique_id'] for t in suite] != [t['unique_id'] for t in run['tests']]:
            return False
        allowed.update({('test', t['unique_id']): 'dbt-native-test' for t in suite})
    seen = set()
    for link in links:
        key = (link['phase'], link['node_id'])
        if key in seen or link['route'] != allowed.get(key):
            return False
        seen.add(key)
    return ('build', run['selected_node_id']) in seen


def _native_saved(db, run, operation_id):
    """Validate saved native proof only; settlement never measures or runs SQL."""
    import database_dbt_native as native
    try:
        row, _ = _row(db, run['id'])
        links = list(db.execute('SELECT o.*,l.phase,l.node_id FROM dbt_run_operations l '
            'JOIN dataops_operations o ON o.id=l.operation_id WHERE l.run_id=?', (run['id'],)))
        if (run.get('executor') != 'native_table' or run['kind'] != 'run' or not _native_links(db, run, links)
                or len(run['models']) != 1 or run['models'][0]['unique_id'] != run['selected_node_id']):
            return None
        builds = [op for op in links if op['phase'] == 'build']
        if len(builds) != 1:
            return None
        op = builds[0]
        receipt = json.loads(op['result'] or '{}')
        if not isinstance(receipt, dict):
            return None
        payload = dict(plan_id=run['plan_id'], node_id=run['selected_node_id'],
                       digest=run['_plan_digest'], **native._PROVENANCE)
        expected = ds._json(dict(payload=payload, context=run['context']))
        child = db.execute("SELECT * FROM warehouse_admissions WHERE kind='dataops' AND subject_id=? AND warehouse_id=?",
            (op['id'], run['context']['warehouse_id'])).fetchone()
        if (op['id'] != operation_id or op['phase'] != 'build' or op['node_id'] != run['selected_node_id']
                or op['route'] != 'dbt-native-table' or op['state'] != 'FINISHED'
                or not op['committed'] or op['cleanup_error'] or op['actor'] != run['actor']
                or json.loads(op['context']) != run['context'] or op['payload'] != expected
                or op['fingerprint'] != hashlib.sha256(expected.encode()).hexdigest()
                or child is None or child['parent_id'] != row['admission_id']
                or child['actor'] != run['actor'] or child['state'] != 'FINISHED'
                or receipt.get('native_verified') is not True
                or any(receipt.get(k) != v for k, v in payload.items())):
            return None
        if (run['state'] == 'NATIVE_VERIFIED' or run.get('native_verified') is True) and (run.get('native_receipt') != receipt
                or run['models'][0].get('candidate') != receipt.get('candidate')
                or run['models'][0].get('operation_ids') != [op['id']]):
            return None
        queries = list(db.execute('SELECT d.step,d.action,q.*,e.* FROM dataops_queries d '
            'LEFT JOIN query_records q ON q.id=d.query_id '
            'LEFT JOIN query_execution_contexts e ON e.query_id=d.query_id '
            'WHERE d.operation_id=?', (op['id'],)))
        if len(queries) != 1:
            return None
        query = queries[0]
        if (query['step'] != 'native-create' or query['action'] != 'dbt_native_create'
                or query['state'] != 'FINISHED' or not query['backend_query_id']
                or not query['backend_query_id'].strip() or query['actor'] != op['actor']
                or any(query[k] != run['context'][k] for k in (
                    'database_id', 'schema_id', 'warehouse_id', 'generation_id', 'selected_role',
                    'sql_catalog', 'sql_schema', 'binding_revision', 'grant_revision'))
                or query['mode'] != 'dataops' or query['request_id'] != op['id']
                or query['worksheet_id'] != 'native-create'
                or query['backend_username'] != op['backend_username']
                or query['credential_snapshot_ref'] != op['credential_snapshot_ref']):
            return None
        candidate = receipt['candidate']
        ref = registry.ResourceRef(**candidate['ref'])
        obj = registry.get_object(db, ref.object_id)
        schema = registry.get_schema(db, ref.schema_id)
        owned = db.execute('SELECT * FROM dataops_owned_objects WHERE operation_id=? AND object_id=?',
            (op['id'], ref.object_id)).fetchone()
        locator = receipt['candidate_locator']
        if (ref.database_id != run['context']['database_id'] or ref.schema_id != run['context']['schema_id']
                or schema is None or schema.database_id != ref.database_id or schema.purpose != 'sandbox'
                or obj is None or not obj.current or obj.kind != 'TABLE' or obj.schema_id != ref.schema_id
                or obj.incarnation != ref.incarnation or not obj.backend_identity or not candidate.get('snapshot')
                or owned is None or owned['incarnation'] != ref.incarnation or owned['purpose'] != 'stage'
                or obj.physical_name != 'n_' + op['id'].replace('-', '')[:17]
                or locator != dict(database_id=ref.database_id, schema_id=ref.schema_id, physical_name=obj.physical_name)):
            return None
        return receipt
    except (KeyError, TypeError, ValueError, AttributeError, IndexError, QueryError):
        return None


def _native_test_saved(db, run, uid, build):
    """Verify one SDK test's saved parent, child and query evidence without execution."""
    import database_dbt_native as native
    try:
        row, _ = _row(db, run['id'])
        plan = _check_plan(db, run)
        suite = native.suite(plan, run['selected_node_id'])
        if suite != run.get('_native_test_suite') or run.get('native_tests') is not True:
            return None
        test = next(t for t in suite if t['unique_id'] == uid)
        links = list(db.execute('SELECT o.*,l.phase,l.node_id FROM dbt_run_operations l '
            'JOIN dataops_operations o ON o.id=l.operation_id WHERE l.run_id=?', (run['id'],)))
        if not _native_links(db, run, links):
            return None
        op = next(o for o in links if o['phase'] == 'test' and o['node_id'] == uid)
        receipt = json.loads(op['result'] or '{}')
        payload = dict(plan_id=run['plan_id'], node_id=uid, selected_node_id=run['selected_node_id'],
            digest=run['_plan_digest'], **native._TEST_PROVENANCE)
        expected = ds._json(dict(payload=payload, context=run['context']))
        child = db.execute("SELECT * FROM warehouse_admissions WHERE kind='dataops' AND subject_id=? AND warehouse_id=?",
            (op['id'], run['context']['warehouse_id'])).fetchone()
        build_op = next(o for o in links if o['phase'] == 'build')
        if (op['route'] != 'dbt-native-test' or op['state'] != 'FINISHED' or op['committed'] or op['cleanup_error']
                or op['actor'] != run['actor'] or json.loads(op['context']) != run['context']
                or op['payload'] != expected or op['fingerprint'] != hashlib.sha256(expected.encode()).hexdigest()
                or child is None or child['parent_id'] != row['admission_id']
                or child['actor'] != run['actor'] or child['state'] != 'FINISHED'
                or any(op[k] != build_op[k] for k in ('backend_username', 'credential_snapshot_ref'))
                or not isinstance(receipt, dict) or receipt.get('test_verified') is not True
                or any(receipt.get(k) != v for k, v in payload.items())
                or type(receipt.get('failures')) is not int or receipt['failures'] < 0
                or receipt.get('status') != ('PASSED' if receipt['failures'] == 0 else 'FAILED')
                or receipt.get('candidate') != build['candidate']):
            return None
        # Match every input to original frozen measurements, including exact candidate head.
        pins = receipt.get('pins')
        if not isinstance(pins, dict):
            return None
        bindings, approved = native._test_plan(plan, test, build, ResolvedQueryContext(**run['context']))
        if set(pins) != set(approved['sources']):
            return None
        if any(pins[alias].get('ref') != asdict(ref) for alias, ref in approved['sources'].items()):
            return None
        originals = dict(run['_pins'])
        originals[ds._json(build['candidate']['ref'])] = build['candidate']
        for pin in pins.values():
            if not isinstance(pin, dict) or originals.get(ds._json(pin.get('ref'))) != pin:
                return None
        queries = list(db.execute('SELECT d.step,d.action,q.*,e.* FROM dataops_queries d '
            'LEFT JOIN query_records q ON q.id=d.query_id '
            'LEFT JOIN query_execution_contexts e ON e.query_id=d.query_id WHERE d.operation_id=?', (op['id'],)))
        if len(queries) != 1:
            return None
        query = queries[0]
        if (query['step'] != 'native-test' or query['action'] != 'dbt_test'
                or query['state'] != 'FINISHED' or not query['backend_query_id'] or not query['backend_query_id'].strip()
                or query['actor'] != run['actor'] or any(query[k] != run['context'][k] for k in (
                    'database_id', 'schema_id', 'warehouse_id', 'generation_id', 'selected_role',
                    'sql_catalog', 'sql_schema', 'binding_revision', 'grant_revision'))
                or query['mode'] != 'dataops' or query['request_id'] != op['id'] or query['worksheet_id'] != 'native-test'
                or query['backend_username'] != op['backend_username']
                or query['credential_snapshot_ref'] != op['credential_snapshot_ref']):
            return None
        result = run.get('_native_test_results', {}).get(uid)
        if (not isinstance(result, dict) or type(result.get('failures')) is not int
                or type(result.get('should_warn')) is not bool or type(result.get('should_error')) is not bool
                or result != dict(operation_id=op['id'], query_id=query['id'],
                failures=receipt['failures'], should_warn=receipt['failures'] != 0,
                should_error=receipt['failures'] != 0, pins=pins, candidate=build['candidate'])
                or receipt.get('query_id') != query['id']):
            return None
        return receipt
    except (KeyError, TypeError, ValueError, AttributeError, IndexError, StopIteration, QueryError):
        return None


def _native_quality_saved(db, run, build):
    """Closure can retain exact quality proof; it can never create success."""
    if not run.get('native_tests'):
        return run.get('tests_status', 'NOT_RUN') == 'NOT_RUN' and not run['tests']
    suite = run.get('_native_test_suite')
    if not suite or [t['unique_id'] for t in suite] != [t['unique_id'] for t in run['tests']]:
        return False
    for test in run['tests']:
        proof = _native_test_saved(db, run, test['unique_id'], build)
        if (proof is None or test.get('native_receipt') != proof or test['state'] != proof['status']
                or type(test.get('failure_count')) is not int or test['failure_count'] != proof['failures']):
            return False
        op = db.execute('SELECT node_id,phase FROM dbt_run_operations WHERE operation_id=? AND run_id=?',
            (test.get('operation_id'), run['id'])).fetchone()
        if op is None or op['phase'] != 'test' or op['node_id'] != test['unique_id']:
            return False
    return True


def _native_run(ident, principal, context, parent, frozen, credential):
    import database_dbt_native as native
    run = _row_run(ident)
    uid = run['selected_node_id']
    plan, node, bindings, approved = native._plan(principal, context, run['plan_id'], uid)
    metadata = _access(principal, context)
    pins = {ds._json(s['ref']): metadata.wap_measure(registry.ResourceRef(**s['ref']))
            for s in plan['source_bindings']}
    with control_store.connect() as db:
        _, run = _row(db, ident)
        _check_plan(db, run)
        run['_pins'] = pins
        _write(db, run)
    _phase(ident, 'BUILDING', 'build', uid)
    with ds._dbt_scope(principal, context, parent, frozen, credential, ident, 'build', uid):
        payload = dict(plan_id=plan['id'], node_id=uid, digest=plan['_plan_digest'], **native._PROVENANCE)
        execution = ds.begin(principal, context, route='dbt-native-table', idempotency_key=ident + ':' + uid, payload=payload)
        if execution.replayed:
            raise QueryError('OPERATION_OBSERVATION_ONLY', 409)
        native.materialize_accepted(execution, principal, context, plan, node, bindings, approved, payload, isolated=True)
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        _, run = _row(db, ident)
        revalidate_admission(db, principal, context)
        _check_plan(db, run)
        if run['cancel_requested']:
            raise QueryError('DBT_RUN_STOPPED', 409)
        receipt = _native_saved(db, run, execution.id)
        if receipt is None:
            raise QueryError('DBT_CHILD_UNRESOLVED', 409)
        run['models'][0].update(state='NATIVE_VERIFIED', candidate=receipt['candidate'],
            _measurement=receipt['candidate'], operation_ids=[execution.id])
        run.update(state='TESTING' if run.get('native_tests') else 'NATIVE_VERIFIED',
            native_verified=True, native_receipt=receipt, _phase=None, _node_id=None)
        _write(db, run)
    if not run.get('native_tests'):
        return
    for test in run['_native_test_suite']:
        uid = test['unique_id']
        _phase(ident, 'TESTING', 'test', uid)
        with control_store.connect() as db:
            _, current = _row(db, ident)
            current['tests_status'] = 'RUNNING'
            _write(db, current)
        with ds._dbt_scope(principal, context, parent, frozen, credential, ident, 'test', uid):
            payload = dict(plan_id=plan['id'], node_id=uid, selected_node_id=run['selected_node_id'],
                digest=plan['_plan_digest'], **native._TEST_PROVENANCE)
            execution = ds.begin(principal, context, route='dbt-native-test',
                idempotency_key=ident + ':' + uid, payload=payload)
            if execution.replayed:
                raise QueryError('OPERATION_OBSERVATION_ONLY', 409)
            native.test_accepted(execution, principal, context, plan, test, receipt, payload, isolated=True)
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            _, current = _row(db, ident)
            proof = _native_test_saved(db, current, uid, receipt)
            if proof is None:
                raise QueryError('DBT_NATIVE_TEST_RECEIPT_INVALID', 409)
            selected = next(t for t in current['tests'] if t['unique_id'] == uid)
            selected.update(state=proof['status'], failure_count=proof['failures'],
                operation_id=execution.id, native_receipt=proof)
            _write(db, current)
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        _, run = _row(db, ident)
        revalidate_admission(db, principal, context)
        _check_plan(db, run)
        if run['cancel_requested']:
            raise QueryError('DBT_RUN_STOPPED', 409)
        if not _native_quality_saved(db, run, receipt):
            raise QueryError('DBT_NATIVE_TEST_RECEIPT_INVALID', 409)
        run['tests_status'] = 'FAILED' if any(t['state'] == 'FAILED' for t in run['tests']) else 'PASSED'
        run.update(state='FAILED' if run['tests_status'] == 'FAILED' else 'NATIVE_VERIFIED',
            error_code='DBT_TEST_FAILED' if run['tests_status'] == 'FAILED' else None, _phase=None, _node_id=None)
        _write(db, run)


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


def _native_child_payload(publication, phase):
    return dict(publication_id=publication['id'], phase=phase, intent=publication['_native_intent'])


def _native_execution_intent(execution, phase):
    """No caller data: resolve only an admitted, owned, exact phase continuation."""
    scope = ds._dbt.get()
    if scope is None or scope[6] != phase:
        raise QueryError('DBT_CONTINUATION_REQUIRED', 409)
    with control_store.connect() as db:
        _check_child(db, execution.principal, execution._context(), *scope[5:])
        _check_operation(db, execution.id)
        link = db.execute('SELECT * FROM dbt_run_operations WHERE operation_id=?', (execution.id,)).fetchone()
        publication = _row(db, scope[5])[1]
        if (publication.get('origin') not in ('native_table', 'native_graph') or publication['kind'] != 'publication'
                or link is None or tuple(link[k] for k in ('run_id', 'phase', 'node_id')) != scope[5:]):
            raise QueryError('DBT_CONTINUATION_REQUIRED', 409)
        if phase == 'publish' and _native_audit_saved(db, publication) is None:
            raise QueryError('AUDIT_MISMATCH', 409)
    _native_publication_live(execution, publication)
    return publication


def _native_child_saved(db, publication, phase):
    """Exact closed child/query lineage, independent of subsequent catalog heads."""
    from db_marts import _closed_child
    if (publication.get('origin') not in ('native_table', 'native_graph')
            or publication['_native_intent'].get('origin') != publication['origin']):
        return None
    ops = list(db.execute('SELECT o.*,l.node_id FROM dataops_operations o JOIN dbt_run_operations l '
        'ON l.operation_id=o.id WHERE l.run_id=? AND l.phase=?', (publication['id'], phase)))
    if len(ops) != 1:
        return None
    op = ops[0]
    payload = ds._json(dict(payload=_native_child_payload(publication, phase), context=publication['context']))
    parent = _row(db, publication['id'])[0]
    child = db.execute("SELECT * FROM warehouse_admissions WHERE kind='dataops' AND subject_id=?", (op['id'],)).fetchone()
    if (op['route'] != 'dbt-native-' + phase or op['node_id'] != publication['node_id']
            or op['state'] != 'FINISHED' or phase == 'audit' and op['cleanup_error']
            or bool(op['committed']) != (phase == 'publish')
            or op['actor'] != publication['actor'] or json.loads(op['context']) != publication['context']
            or op['payload'] != payload or op['fingerprint'] != hashlib.sha256(payload.encode()).hexdigest()
            or child is None or child['parent_id'] != parent['admission_id'] or child['actor'] != publication['actor']
            or child['state'] != 'FINISHED' or not _closed_child(db, op['id'])):
        return None
    rows = list(db.execute('SELECT d.step,d.action,d.commit_result,q.*,e.* FROM dataops_queries d '
        'JOIN query_records q ON q.id=d.query_id JOIN query_execution_contexts e ON e.query_id=q.id '
        'WHERE d.operation_id=?', (op['id'],)))
    if len(rows) != 1:
        return None
    query = rows[0]
    if (query['step'] != phase or query['action'] != 'wap_' + phase or query['state'] != 'FINISHED'
            or not query['backend_query_id'] or not query['backend_query_id'].strip()
            or query['actor'] != publication['actor'] or query['mode'] != 'dataops'
            or query['request_id'] != op['id'] or query['worksheet_id'] != phase
            or any(query[k] != v for k, v in publication['context'].items())
            or query['backend_username'] != op['backend_username']
            or query['credential_snapshot_ref'] != op['credential_snapshot_ref']):
        return None
    intent = publication['_native_intent']
    candidate = intent['candidate']
    locator = intent['build']['candidate_locator']
    source = '.'.join(ds._identifier(n) for n in (publication['context']['sql_catalog'],
        publication['context']['sql_schema'], locator['physical_name']))
    source += ' FOR VERSION AS OF ' + candidate['snapshot']
    if phase == 'audit':
        sql = 'SELECT COUNT(*) FROM ' + source
    else:
        destination = publication['expected_target']['destination']
        schema = registry.get_schema(db, destination['schema_id'])
        if 'object_id' in destination:
            original = db.execute('SELECT * FROM object_bindings WHERE id=?', (destination['object_id'],)).fetchone()
            if original is None or original['incarnation'] != destination['incarnation']:
                return None
            name, prefix = original['physical_name'], 'CREATE OR REPLACE TABLE '
        else:
            name, prefix = destination['physical_name'], 'CREATE TABLE '
        target = '.'.join(ds._identifier(n) for n in (publication['context']['sql_catalog'], schema.sql_name, name))
        sql = prefix + target + " WITH (format='PARQUET') AS SELECT " + ', '.join(
            ds._column(c['name']) for c in candidate['columns']) + ' FROM ' + source
    if query['sql'] != sql:
        return None
    return op, query, json.loads(op['result'] or '{}')


def _native_audit_saved(db, publication):
    try:
        saved = _native_child_saved(db, publication, 'audit')
        if saved is None:
            return None
        op, query, result = saved
        receipt = result['audit']
        expected = dict(id=publication['audit_id'], origin=publication['origin'], publication_id=publication['id'],
            intent=publication['_native_intent'], operation_id=op['id'], query_id=query['id'],
            count=receipt['count'], passed=True)
        if (type(receipt['count']) is not int or receipt['count'] < 1 or receipt.get('passed') is not True or receipt != expected
                or result != dict(audit_id=receipt['id'], audit=receipt)
                or publication.get('native_audit') != receipt or query['commit_result'] is not None):
            return None
        return receipt
    except (KeyError, TypeError, ValueError, QueryError):
        return None


def _native_publication_saved(db, publication):
    try:
        if publication.get('origin') == 'native_graph':
            import database_dbt_graph as graph
            source = _row(db, publication['parent_run_id'])[1]
            # Source observation may retire current build eligibility. Closed
            # publication evidence instead uses its frozen child receipts.
            for table in publication['_native_intent']['tables']:
                model = next((m for m in source['models'] if m['unique_id'] == table['node_id']), None)
                if model is None:
                    return None
                model.update(state='NATIVE_VERIFIED', native_receipt=table['receipt'])
            _, proof = graph.publication_proof(db, source, publication['node_id'], historical=True)
            if publication['_native_intent'] != _native_intent(source, publication['node_id'], proof,
                    publication['expected_target'], ResolvedQueryContext(**publication['context']), publication['actor']):
                return None
        links = list(db.execute('SELECT phase,node_id FROM dbt_run_operations WHERE run_id=?', (publication['id'],)))
        if sorted((r['phase'], r['node_id']) for r in links) != [('audit', publication['node_id']), ('publish', publication['node_id'])]:
            return None
        saved = _native_child_saved(db, publication, 'publish')
        audit = _native_audit_saved(db, publication)
        if saved is None or audit is None:
            return None
        op, query, result = saved
        audit_op = db.execute('SELECT o.* FROM dataops_operations o JOIN dbt_run_operations l '
            "ON l.operation_id=o.id WHERE l.run_id=? AND l.phase='audit'", (publication['id'],)).fetchone()
        if any(op[k] != audit_op[k] for k in ('backend_username', 'credential_snapshot_ref')):
            return None
        intent = _native_publish_intent(publication, op['id'])
        receipt = result['publication']
        target = receipt['target']
        if (result != dict(intent, publication=receipt) or query['commit_result'] != ds._json(intent)
                or receipt != dict(intent, id=publication['id'], actor=publication['actor'],
                    context=publication['context'], query_id=query['id'], target=target)
                or target['ref']['database_id'] != publication['context']['database_id']
                or target['ref']['schema_id'] != publication['expected_target']['destination']['schema_id']
                or not target['snapshot'] or type(target['schema_id']) is not int
                or target['columns'] != publication['_native_intent']['candidate']['columns']
                or set(target) != {'ref', 'snapshot', 'schema_id', 'columns', 'schema_fingerprint'}):
            return None
        registry.ResourceRef(**target['ref'])
        _native_descriptor(dict(version=1, destination=target['ref'], target=target), target['ref'])
        # Retired object rows remain valid historical identity evidence. Neither
        # a later snapshot nor replacement of this destination invalidates it.
        obj = db.execute('SELECT * FROM object_bindings WHERE id=?', (target['ref']['object_id'],)).fetchone()
        destination = publication['expected_target']['destination']
        if 'object_id' in destination:
            original = db.execute('SELECT physical_name FROM object_bindings WHERE id=?', (destination['object_id'],)).fetchone()
            name = original['physical_name'] if original else None
        else:
            name = destination['physical_name']
        if (obj is None or obj['kind'] != 'TABLE' or obj['schema_id'] != target['ref']['schema_id']
                or obj['incarnation'] != target['ref']['incarnation'] or obj['physical_name'] != name):
            return None
        # Once saved, the parent receipt anchors historical proof, including
        # JSON value types, even if this FQN is later recreated.
        if ('native_publication' in publication
                and ds._json(publication['native_publication']) != ds._json(receipt)):
            return None
        return receipt
    except (KeyError, TypeError, ValueError, AttributeError, StopIteration, QueryError):
        return None


def _native_publish_intent(publication, operation_id):
    if publication.get('origin') not in ('native_table', 'native_graph'):
        raise QueryError('DBT_NATIVE_PUBLICATION_INVALID', 409)
    return dict(origin=publication['origin'], publication_id=publication['id'], audit_id=publication['audit_id'],
        operation_id=operation_id, intent=publication['_native_intent'])


def _native_publication_child(ident, principal, context, phase):
    publication = _row_run(ident)
    execution = ds.begin(principal, context, route='dbt-native-' + phase,
        idempotency_key=ident + ':' + phase, payload=_native_child_payload(publication, phase))
    if execution.replayed:
        return execution.inspect()
    with ds._accepted(execution):
        try:
            if phase == 'audit':
                result = execution._dbt_native_audit()
            else:
                result = execution._dbt_native_publish()
            return execution.complete(result)
        except Exception as error:
            # Same durable query/commit behavior as manual WAP; never retry a send.
            row = execution._row()
            if row['state'] == 'ACTIVE':
                try:
                    if row['committed']:
                        with control_store.connect() as db:
                            db.execute('UPDATE dataops_operations SET cleanup_error=? WHERE id=?',
                                (getattr(error, 'code', type(error).__name__), execution.id))
                        return execution.complete()
                    execution.fail()
                except Exception:
                    wap._settle_definite(execution, context, error)
            if row['committed'] or row['state'] == 'UNKNOWN':
                return execution._inspect()
            raise


def _native_publish_run(ident, principal, context, parent, frozen, credential):
    publication = _row_run(ident)
    uid = publication['node_id']
    _phase(ident, 'AUDITING', 'audit', uid)
    with ds._dbt_scope(principal, context, parent, frozen, credential, ident, 'audit', uid):
        child = _native_publication_child(ident, principal, context, 'audit')
    audited = (child.get('result') or {}).get('audit')
    with control_store.connect() as db:
        _, publication = _row(db, ident)
        if audited:
            publication.update(audit_id=child['result']['audit_id'], native_audit=audited)
            _write(db, publication)
    # Preserve the observed audit even when it prevents publication.
    if child['state'] != 'FINISHED' or not audited or not audited['passed']:
        raise QueryError('DBT_AUDIT_FAILED', 409)
    with control_store.connect() as db:
        if _native_audit_saved(db, publication) is None:
            raise QueryError('AUDIT_MISMATCH', 409)
    _phase(ident, 'PUBLISHING', 'publish', uid)
    with ds._dbt_scope(principal, context, parent, frozen, credential, ident, 'publish', uid):
        _native_publication_child(ident, principal, context, 'publish')
    # _settle consumes the closed canonical receipt, even after a parent write failure.


def _publish_run(ident, principal, context, parent, frozen, credential):
    with control_store.connect() as db:
        _, publication = _row(db, ident)
    if publication.get('origin') in ('native_table', 'native_graph'):
        return _native_publish_run(ident, principal, context, parent, frozen, credential)
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
                    if run.get('executor') == 'native_graph':
                        import database_dbt_graph as graph
                        function = graph.execute
                    else:
                        function = _native_run if run.get('executor') == 'native_table' else _build_run
                    function(ident, principal, context, parent, frozen, credential)
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
        if run.get('executor') == 'native_graph':
            import database_dbt_graph as graph
            graph.settle(db, run, operations, closed=closed, owner_lost=owner_lost)
            _write(db, run)
            admission.finish(db, parent, state='FINISHED' if closed else 'UNKNOWN',
                evidence_ref='dbt-child-closure:' + ident if closed else None)
            return
        # Owner-loss observation can retain existing proof, never promote a run.
        native_proof = None
        if run.get('executor') == 'native_table':
            ids = run['models'][0].get('operation_ids', [])
            native_proof = _native_saved(db, run, ids[0]) if len(ids) == 1 else None
        committed = next((op for op in operations if op['phase'] == 'publish' and op['committed']), None)
        if run['kind'] == 'publication' and committed:
            receipt = json.loads(committed['result'] or '{}')
            run.update(committed=True, publication_id=receipt.get('publication_id'))
            if run.get('origin') in ('native_table', 'native_graph'):
                artifact = _native_publication_saved(db, run)
                if artifact:
                    run['native_publication'] = artifact
            else:
                artifact = db.execute('SELECT id FROM wap_publications WHERE id=? AND operation_id=?',
                    (receipt.get('publication_id'), committed['id'])).fetchone()
            recorded = artifact and closed and (run.get('origin') in ('native_table', 'native_graph') or not committed['cleanup_error'])
            run['state'] = 'PUBLISHED' if recorded else 'COMMITTED_RECORDING_FAILED'
        elif not closed:
            run['state'] = 'UNKNOWN'
        elif run['cancel_requested']:
            run['state'] = 'CANCELED'
        elif run['state'] == 'NATIVE_VERIFIED' and run.get('executor') == 'native_table' and run.get('native_verified') is True and native_proof is not None and _native_quality_saved(db, run, native_proof) and (
                not run.get('native_tests') or run.get('tests_status') == 'PASSED'
                and all(t['state'] == 'PASSED' for t in run['tests'])):
            pass
        elif run['state'] not in ('READY', 'PUBLISHED', 'FAILED', 'CANCELED', 'COMMITTED_RECORDING_FAILED'):
            run['state'] = 'FAILED'
        if owner_lost and not run['error_code'] and run['state'] == 'FAILED':
            run['error_code'] = 'DBT_OWNER_LOST'
        if run.get('executor') == 'native_table':
            for op in operations:
                try:
                    receipt = json.loads(op['result'] or '{}')
                except (TypeError, ValueError):
                    receipt = {}
                if not isinstance(receipt, dict):
                    receipt = {}
                if op['phase'] == 'build' and op['node_id'] == run['selected_node_id']:
                    # Preserve committed evidence on failure without treating it as proof.
                    run['native_receipt'] = native_proof if native_proof is not None else dict(run.get('native_receipt') or receipt, native_verified=False)
                    model = run['models'][0]
                    model['operation_ids'] = [op['id']]
                    evidence = run['native_receipt']
                    if evidence.get('candidate_locator'):
                        model['candidate_locator'] = evidence['candidate_locator']
                    if evidence.get('candidate'):
                        model['candidate'] = evidence['candidate']
            run['native_verified'] = native_proof is not None
            if native_proof is None:
                run['native_verified'] = False
                if run.get('native_receipt'):
                    run['native_receipt']['native_verified'] = False
                if run['models'][0]['state'] == 'NATIVE_VERIFIED':
                    run['models'][0]['state'] = run['state']
        for item in run.get('models', []) + run.get('tests', []):
            if item['state'] in ('QUEUED', 'BUILDING', 'TESTING'):
                item['state'] = 'CANCELED' if run['cancel_requested'] and closed else 'UNKNOWN' if not closed else 'FAILED'
        if run.get('native_tests') is True:
            pending_state = ('UNKNOWN' if not closed else 'CANCELED' if run['cancel_requested']
                else 'UNKNOWN' if owner_lost else 'ERROR')
            for test in run['tests']:
                if test['state'] not in ('PASSED', 'FAILED', 'ERROR', 'CANCELED', 'UNKNOWN'):
                    test['state'] = pending_state
                elif test['state'] == 'FAILED' and test.get('failure_count') is None:
                    test['state'] = pending_state
            if not closed or run['cancel_requested']:
                run['tests_status'] = pending_state
            elif run['state'] != 'NATIVE_VERIFIED' and owner_lost:
                run['tests_status'] = 'UNKNOWN'
            elif run['state'] != 'NATIVE_VERIFIED' and not (run.get('tests_status') == 'PASSED'
                    and native_proof is not None and _native_quality_saved(db, run, native_proof)
                    and all(t['state'] == 'PASSED' for t in run['tests'])):
                run['tests_status'] = (pending_state if any(t['state'] in ('ERROR', 'UNKNOWN', 'CANCELED') for t in run['tests'])
                    else 'FAILED' if any(t['state'] == 'FAILED' for t in run['tests']) else 'ERROR')
            if run['state'] == 'FAILED' and not run['error_code']:
                run['error_code'] = 'DBT_NATIVE_TEST_INCOMPLETE'
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


def get_run_options(principal, context, plan_id):
    import database_dbt_native as native
    metadata = _access(principal, context)
    with control_store.connect() as db:
        plan = _authorized(db, principal, context, plan_id, 'plan')
        return native.run_options(db, metadata, principal, context, plan)


def get_graph_options(principal, context, plan_id):
    import database_dbt_graph as graph
    metadata = _access(principal, context)
    with control_store.connect() as db:
        plan = _authorized(db, principal, context, plan_id, 'plan')
        return graph.options(db, metadata, principal, context, plan)


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
