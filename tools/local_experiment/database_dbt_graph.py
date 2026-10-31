"""Durable, sandbox-only dbt task execution. The SDK schedules; DataOps sends SQL.

Saved projections and settlement never start an SDK or contact the engine.
"""
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import uuid
import re
import os
from pathlib import Path
import secrets
import selectors
import subprocess
import threading
import time

import control_store
import database_dbt as compiler
import database_dbt_native as native
import data_management as registry
import dataops_service as ds
import db_dbt as dbt
import manual_wap
from query_context import QueryError, ResolvedQueryContext
import warehouse_admission as admission


reject = native._reject


def ancestors(plan, node):
    models = {m['unique_id']: m for m in plan['models']}
    sources = {s['unique_id'] for s in plan['source_bindings']}
    pending, seen = list(node['depends_on']), set()
    while pending:
        uid = pending.pop()
        if uid in seen:
            continue
        seen.add(uid)
        if uid not in sources:
            if uid not in models:
                reject('DBT_GRAPH_DEPENDENCY_INVALID')
            pending.extend(models[uid]['depends_on'])
    return seen


def validate(metadata, context, plan, *, tests=False):
    """Pure saved graph boundary, including all SQL and declared dependencies."""
    models = plan['models']
    ids = [m['unique_id'] for m in models]
    sources = plan['source_bindings']
    source_ids = [s['unique_id'] for s in sources]
    if len(set(ids + source_ids)) != len(ids + source_ids):
        reject('DBT_GRAPH_DEPENDENCY_INVALID')
    if not any(m['materialized'] in ('table', 'view') for m in models):
        reject('DBT_GRAPH_TABLE_REQUIRED')
    if any(m['materialized'] not in ('table', 'view', 'ephemeral') for m in models):
        reject('DBT_GRAPH_MATERIALIZATION_UNSUPPORTED')
    for model in models:
        if model['materialized'] == 'view':
            if model.get('view_security') != 'INVOKER' or model.get('table_properties') != {}:
                reject('DBT_GRAPH_MATERIALIZATION_UNSUPPORTED')
            deps = ancestors(plan, model)
            if any(m['unique_id'] in deps and m['materialized'] == 'view' for m in models):
                reject('UNSUPPORTED_VIEW_DEPENDENCY')
    if any(m['materialized'] == 'table' and m.get('table_properties') != {'format': 'PARQUET'} for m in models):
        reject('DBT_NATIVE_PARQUET_REQUIRED')
    remaining = {m['unique_id']: set(m['depends_on']) - set(source_ids) for m in models}
    while remaining:
        ready = {uid for uid, deps in remaining.items() if not deps}
        if not ready:
            reject('DBT_GRAPH_DEPENDENCY_INVALID')
        remaining = {uid: deps - ready for uid, deps in remaining.items() if uid not in ready}
    relations = {}
    refs = set()
    for source in sources:
        ref = registry.ResourceRef(**source['ref'])
        schema = metadata._schema(ref.schema_id)
        if (ref.database_id != context.database_id or schema.sql_name != 'raw' or schema.purpose != 'raw'
                or tuple(source['relation'])[:2] != (context.sql_catalog, 'raw')):
            reject('DBT_NATIVE_RAW_REQUIRED')
        if ref in refs:
            reject('DBT_GRAPH_ALIAS_COLLISION')
        refs.add(ref)
        relations[tuple(source['relation'])] = (source['unique_id'], ref)
    for model in models:
        if model['materialized'] in ('table', 'view'):
            relation = tuple(model['relation'])
            if relation[:2] != (context.sql_catalog, context.sql_schema) or relation in relations:
                reject('DBT_GRAPH_ALIAS_COLLISION')
            relations[relation] = (model['unique_id'], registry.ResourceRef(context.database_id,
                context.schema_id, model['unique_id'], 1))
    if len(relations) != len(sources) + sum(m['materialized'] in ('table', 'view') for m in models):
        reject('DBT_GRAPH_ALIAS_COLLISION')
    selected = plan['tests'] if tests else []
    if tests:
        contract = plan.get('_native_tests')
        owners = contract.get('owners') if isinstance(contract, dict) else None
        test_ids = [t['unique_id'] for t in selected]
        if not selected:
            reject('DBT_NATIVE_TEST_EMPTY_SUITE')
        if (not isinstance(contract, dict) or set(contract) != {'version', 'owners'}
                or type(contract['version']) is not int or contract['version'] != 1):
            reject('DBT_NATIVE_TEST_RECOMPILE_REQUIRED')
        if (not isinstance(owners, dict) or len(set(test_ids)) != len(test_ids)
                or set(owners) != set(test_ids)
                or any(o is not None and (type(o) is not str or o not in set(ids + source_ids)) for o in owners.values())
                or any(owners[t['unique_id']] is not None and owners[t['unique_id']] not in t['depends_on'] for t in selected)):
            reject('DBT_NATIVE_TEST_OWNERS_INVALID')
        if any(t.get('test_type') not in compiler._TESTS or t.get('relation') is not None for t in selected):
            reject('DBT_NATIVE_TEST_CONFIG_UNSUPPORTED')
    for node in models + selected:
        allowed = ancestors(plan, node)
        used = {compiler._identity(t) for _, t in compiler._physical(compiler._sql_tree(node['compiled_sql']))}
        if any(r not in relations or relations[r][0] not in allowed for r in used):
            reject('DBT_GRAPH_DEPENDENCY_INVALID')
        compiler.normalize_select(node['compiled_sql'], {r: relations[r][1] for r in used}, database_id=context.database_id)
    return deepcopy(selected)


def options(db, metadata, principal, context, plan):
    result = dict(version=1, executor='native_graph', plan_id=plan['id'], source_digest=plan.get('source_digest'),
        eligible=False, reason_code=None, models=[dict(node_id=m['unique_id'], materialized=m['materialized'],
            depends_on=m['depends_on']) for m in plan['models']], model_count=len(plan['models']),
        table_count=sum(m['materialized'] == 'table' for m in plan['models']),
        testability=dict(eligible=False, reason_code=None, count=len(plan['tests'])))
    try:
        native._saved_plan(db, principal, context, plan)
        validate(metadata, context, plan)
        result['eligible'] = True
    except (QueryError, compiler.DbtError) as error:
        result['reason_code'] = error.code
    try:
        native._saved_plan(db, principal, context, plan)
        validate(metadata, context, plan, tests=True)
        result['testability']['eligible'] = True
    except (QueryError, compiler.DbtError) as error:
        result['testability']['reason_code'] = error.code
    return result


def payload(run, phase, uid):
    return dict(executor='native_graph', plan_id=run['plan_id'], node_id=uid,
        digest=run['_plan_digest'], phase=phase)


def node_allowed(db, run, phase, uid):
    plan = dbt._check_plan(db, run)
    if phase == 'build':
        return any(m['unique_id'] == uid and m['materialized'] in ('table', 'view') for m in plan['models'])
    return phase == 'test' and run.get('native_tests') is True and any(t['unique_id'] == uid for t in plan['tests'])


def check_operation(db, run, op, link):
    expected = ds._json(dict(payload=payload(run, link['phase'], link['node_id']), context=run['context']))
    links = list(db.execute('SELECT phase,node_id FROM dbt_run_operations WHERE run_id=?', (run['id'],)))
    if (not node_allowed(db, run, link['phase'], link['node_id'])
            or len({(l['phase'], l['node_id']) for l in links}) != len(links)
            or any(not node_allowed(db, run, l['phase'], l['node_id']) for l in links)
            or op['route'] != 'dbt-graph-' + link['phase'] or op['payload'] != expected):
        reject('DBT_NATIVE_CHILD_INVALID')


def check_candidates(metadata, run):
    for model in run['models']:
        if model.get('native_receipt', {}).get('native_verified') is True:
            saved = model['_measurement']
            measure = metadata._dbt_measure_view if saved.get('kind') == 'VIEW' else metadata.wap_measure
            if measure(registry.ResourceRef(**saved['ref'])) != saved:
                reject('DBT_VIEW_CHANGED' if saved.get('kind') == 'VIEW' else 'STAGE_CHANGED')
    # Historical RAW snapshots remain pinned across appends. Their binding and
    # schema must still match; same-run candidates above must match full heads.
    for saved in (run.get('_pins') or {}).values():
        current = metadata.wap_measure(registry.ResourceRef(**saved['ref']))
        if any(current[k] != saved[k] for k in ('ref', 'schema_id', 'schema_fingerprint', 'columns')):
            reject('SOURCE_SCHEMA_CHANGED')


def logical_sql(sql, remapping):
    """Undo candidate tuples before normalization assigns sorted source aliases.

    Rename scope-bound column qualifiers together with relation identifiers.
    No string replacement is used, including for fully qualified columns.
    """
    import sqlglot
    from sqlglot import exp
    trees = sqlglot.parse(sql, read='trino', error_level=sqlglot.ErrorLevel.RAISE)
    if len(trees) != 1:
        reject('DBT_NATIVE_BODY_CHANGED')
    tree = trees[0]
    for scope, table in compiler._physical(tree):
        key = compiler._identity(table)
        logical = remapping.get(key)
        if logical is None:
            continue
        for column in scope.columns:
            if not column.table:
                continue
            parts = tuple(column.args.get(k) for k in ('catalog', 'db', 'table') if column.args.get(k) is not None)
            names = tuple(p.name if p.args.get('quoted') else p.name.lower() for p in parts)
            if (column.catalog or column.db) and names == key[-len(names):]:
                for k, value in zip(('catalog', 'db', 'table')[-len(parts):], logical[-len(parts):]):
                    column.set(k, exp.to_identifier(value, quoted=True))
            elif not table.args.get('alias') and names == (key[2],):
                column.set('table', exp.to_identifier(logical[2], quoted=True))
        for k, value in zip(('catalog', 'db', 'this'), logical):
            table.set(k, exp.to_identifier(value, quoted=True))
    return tree.sql(dialect='trino')


class Bridge(native._Bridge):
    def __init__(self, owner, execution, node, phase):
        self.owner, self.node, self.phase = owner, node, phase
        run = dbt._row_run(owner.ident)
        bindings, approved = bindings_for(owner.plan, run, node)
        locator = registry.DestinationLocator(**owner.locators.get(node['unique_id'], next(iter(owner.locators.values()))))
        super().__init__(execution, locator, bindings, approved, payload(run, phase, node['unique_id']), wire=True)
        if phase == 'test':
            self.receipt.pop('candidate_locator', None)
        self.receipt['native_verified' if phase == 'build' else 'test_verified'] = False
        self.failures = None

    def execute(self, sql, auto_begin=False, fetch=False, limit=None):
        try:
            self._scope()
            if self.called or type(auto_begin) is not bool or type(fetch) is not bool or limit is not None:
                reject('DBT_NATIVE_CALLBACK_REUSED')
            self.called = True
            self.owner.check()
            context = self.execution._context()
            sql = logical_sql(sql, self.owner.remapping)
            if self.phase == 'build':
                validator = native._native_view_select if self.node['materialized'] == 'view' else native._native_select
                validator(sql, self.target, self.bindings, self.approved, context.database_id)
            else:
                if auto_begin is not True or fetch is not True:
                    reject('DBT_NATIVE_TEST_CALLBACK_REUSED')
                native._native_test_select(sql, self.bindings, self.approved, context.database_id)
            native._check_inputs(self.execution, self.inputs, self.approved['sources'])
            generated = manual_wap._compile_model(**self.approved, execution=self.execution, measurements=self.inputs)
            with control_store.connect() as db:
                db.execute("UPDATE dataops_operations SET result=? WHERE id=? AND state='ACTIVE'",
                    (ds._json(self.receipt), self.execution.id))
            if self.phase == 'build' and self.node['materialized'] == 'view':
                self.candidate_ref = self.execution._dbt_create_view()
                self.receipt['candidate'] = self.execution._dbt_measure_view(self.candidate_ref)
                selected = next(m for m in dbt._row_run(self.owner.ident)['models'] if m['unique_id'] == self.node['unique_id'])
                if self.receipt['candidate']['_canonical_definition_digest'] != selected['_view_definition_digest']:
                    reject('DBT_VIEW_CHANGED')
                with control_store.connect() as db:
                    db.execute("UPDATE dataops_operations SET result=? WHERE id=? AND state='ACTIVE'",
                        (ds._json(self.receipt), self.execution.id))
                self.response = dict(_message='CREATE VIEW', code='CREATE VIEW')
                return self.response
            if self.phase == 'build':
                self.execution._absent(self.locator)
                page = self.execution._execute('dbt_native_create', 'CREATE TABLE ' + self.execution._locator(self.locator)
                    + " WITH (format='PARQUET') AS " + generated, 'native-create', commit_result=self.receipt)
                self.candidate_ref = self.execution._observe(self.locator)
                with control_store.connect() as db:
                    db.execute('INSERT INTO dataops_owned_objects VALUES (?,?,?,?)',
                        (self.execution.id, self.candidate_ref.object_id, self.candidate_ref.incarnation, 'stage'))
                self.receipt['candidate'] = self.execution.wap_measure(self.candidate_ref)
                with control_store.connect() as db:
                    db.execute("UPDATE dataops_operations SET result=? WHERE id=? AND state='ACTIVE'",
                        (ds._json(self.receipt), self.execution.id))
                self.response = dict(_message='CREATE TABLE', code='CREATE TABLE', rows_affected=page.get('update_count'))
                self.response = {k: v for k, v in self.response.items() if v is not None}
                return self.response
            page = self.execution._execute('dbt_test', native._test_wrapper(generated), 'native-test')
            self.owner.check()
            native._check_inputs(self.execution, self.inputs, self.approved['sources'])
            data, columns = page.get('data'), page.get('columns')
            if (not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], list) or len(data[0]) != 3
                    or not isinstance(columns, list) or any(not isinstance(c, dict) for c in columns)
                    or [c.get('name') for c in columns] != ['failures', 'should_warn', 'should_error']):
                reject('DBT_NATIVE_TEST_RESULT_INVALID')
            count, warn, error = data[0]
            if type(count) is not int or count < 0 or type(warn) is not bool or type(error) is not bool or warn != (count != 0) or error != (count != 0):
                reject('DBT_NATIVE_TEST_RESULT_INVALID')
            self.failures = count
            self.receipt.update(query_id=page['id'], failures=count)
            with control_store.connect() as db:
                _, run = dbt._row(db, self.owner.ident)
                run.setdefault('_graph_test_results', {})[self.node['unique_id']] = dict(
                    operation_id=self.execution.id, query_id=page['id'], failures=count,
                    should_warn=warn, should_error=error, pins=self.inputs)
                dbt._write(db, run)
            self.response = dict(_message='SELECT', code='SELECT', rows_affected=1)
            return dict(adapter_response=self.response, data=data)
        except BaseException:
            self.aborted = True
            raise


def bindings_for(plan, run, node):
    relations = {tuple(s['relation']): registry.ResourceRef(**s['ref']) for s in plan['source_bindings']}
    for model in run['models']:
        if model.get('native_receipt', {}).get('native_verified') is True:
            logical = next(m['relation'] for m in plan['models'] if m['unique_id'] == model['unique_id'])
            relations[tuple(logical)] = registry.ResourceRef(**model['candidate']['ref'])
    used = {compiler._identity(t) for _, t in compiler._physical(compiler._sql_tree(node['compiled_sql']))}
    if used - relations.keys():
        reject('DBT_GRAPH_DEPENDENCY_UNVERIFIED')
    bindings = {r: relations[r] for r in used}
    return bindings, compiler.normalize_select(node['compiled_sql'], bindings, database_id=run['context']['database_id'])


class Graph:
    def __init__(self, ident, principal, context, parent, frozen, credential, plan, locators):
        self.ident, self.principal, self.context, self.parent = ident, principal, context, parent
        self.frozen, self.credential, self.plan, self.locators = frozen, credential, plan, locators
        self.thread = threading.get_ident()
        self.phase, self.active, self.stack, self.bridge = 'build', None, None, None
        self.execution = None
        self.events = {'build': {}, 'test': {}}
        self.completed = set()
        self.remapping = {(context.sql_catalog, context.sql_schema, locators[m['unique_id']]['physical_name']): tuple(m['relation'])
            for m in plan['models'] if m['materialized'] in ('table', 'view')}

    def check(self, *, candidates=True):
        if self.thread != threading.get_ident() or admission.current() is not (self.bridge.parent if self.bridge else self.parent) or self.ident not in dbt._live:
            reject('DBT_OWNER_LOST')
        metadata = dbt._access(self.principal, self.context)
        with control_store.connect() as db:
            _, run = dbt._row(db, self.ident)
            if run['cancel_requested'] or run['state'] not in ('BUILDING', 'TESTING'):
                reject('DBT_RUN_STOPPED')
            if dbt._check_plan(db, run) != self.plan:
                reject('DBT_PLAN_CHANGED')
            native._saved_plan(db, self.principal, self.context, self.plan)
        with control_store.connect() as db:
            check_verified(db, run)
        if candidates:
            check_candidates(metadata, run)

    def inventory(self, message):
        self.check()
        if message['op'] == 'list_schemas' and set(message) == {'op', 'database'}:
            if message['database'] not in (self.context.sql_catalog, '"' + self.context.sql_catalog + '"'):
                reject('DBT_NATIVE_RELATION_LOOKUP')
            return [self.context.sql_schema]
        if message['op'] == 'list_relations' and set(message) == {'op', 'database', 'schema'}:
            if (message['database'], message['schema']) != (self.context.sql_catalog, self.context.sql_schema):
                reject('DBT_NATIVE_RELATION_LOOKUP')
            run = dbt._row_run(self.ident)
            return [dict(database=self.context.sql_catalog, schema=self.context.sql_schema,
                identifier=self.locators[m['unique_id']]['physical_name'], type=m['materialized'])
                for m in run['models'] if m.get('native_receipt', {}).get('native_verified') is True]
        if message['op'] != 'get_relation' or set(message) != {'op', 'database', 'schema', 'identifier'}:
            reject('DBT_NATIVE_PROTOCOL_FAILED')
        if (message['database'], message['schema']) != (self.context.sql_catalog, self.context.sql_schema):
            reject('DBT_NATIVE_RELATION_LOOKUP')
        for uid, locator in self.locators.items():
            name = locator['physical_name']
            if message['identifier'] in (name, name + '__dbt_tmp', name + '__dbt_backup'):
                model = next(m for m in dbt._row_run(self.ident)['models'] if m['unique_id'] == uid)
                if message['identifier'] == name and (model.get('native_receipt', {}).get('native_verified') is True
                        or self.bridge and self.bridge.node['unique_id'] == uid and self.bridge.candidate_ref is not None):
                    return dict(database=self.context.sql_catalog, schema=self.context.sql_schema, identifier=name, type=model['materialized'])
                dbt._access(self.principal, self.context)._absent(registry.DestinationLocator(**dict(locator, physical_name=message['identifier'])))
                return None
        reject('DBT_NATIVE_RELATION_LOOKUP')

    def enter(self, uid, skipped):
        self.check()
        if self.active is not None or type(uid) is not str or type(skipped) is not bool or uid in self.events[self.phase]:
            reject('DBT_NATIVE_CALLBACK_REUSED')
        nodes = self.plan['models'] if self.phase == 'build' else self.plan['tests']
        node = next((n for n in nodes if n['unique_id'] == uid), None)
        if node is None:
            reject('DBT_NODE_NOT_FOUND')
        deps = ancestors(self.plan, node) & {m['unique_id'] for m in self.plan['models']}
        run = dbt._row_run(self.ident)
        states = {m['unique_id']: m['state'] for m in run['models']}
        failed = any(states[d] in ('FAILED', 'SKIPPED') for d in deps)
        if skipped != failed or not skipped and any(states[d] not in ('EXPANDED', 'NATIVE_VERIFIED') for d in deps):
            reject('DBT_GRAPH_DEPENDENCY_UNVERIFIED')
        self.active = (node, skipped)
        if skipped or node.get('materialized') == 'ephemeral':
            return None
        dbt._phase(self.ident, 'BUILDING' if self.phase == 'build' else 'TESTING', self.phase, uid)
        self.stack = ExitStack()
        try:
            self.stack.enter_context(ds._dbt_scope(self.principal, self.context, self.parent,
                self.frozen, self.credential, self.ident, self.phase, uid))
            execution = ds.begin(self.principal, self.context, route='dbt-graph-' + self.phase,
                idempotency_key=self.ident + ':' + uid, payload=payload(run, self.phase, uid))
            if execution.replayed:
                reject('OPERATION_OBSERVATION_ONLY')
            self.execution = execution
            self.stack.enter_context(ds._accepted(execution))
            self.bridge = Bridge(self, execution, node, self.phase)
        except BaseException:
            self.close()
            raise
        return None

    def end(self, result):
        self.check()
        if self.active is None or not valid_result(result) or result['node_id'] != self.active[0]['unique_id']:
            reject('DBT_NATIVE_PROTOCOL_FAILED')
        node, skipped = self.active
        uid = node['unique_id']
        status = result['status']
        if self.phase == 'build' and node['materialized'] == 'view' and (
                result.get('model_type') != 'view' or result.get('view_security') != 'invoker'):
            reject('DBT_NATIVE_RUNNER_FAILED')
        if skipped and status != 'skipped' or not skipped and status == 'skipped':
            reject('DBT_NATIVE_RUNNER_FAILED')
        proof = None
        if self.bridge:
            b = self.bridge
            positive = status == ('success' if self.phase == 'build' else 'pass' if b.failures == 0 else 'fail')
            if positive:
                if b.aborted or not b.called or b.response != result['adapter_response'] or self.phase == 'test' and (type(result['failures']) is not int or result['failures'] != b.failures):
                    reject('DBT_NATIVE_RUNNER_FAILED')
                native._check_inputs(b.execution, b.inputs, b.approved['sources'])
                proof = dict(b.receipt, sdk_result=result)
                if self.phase == 'build':
                    if b.candidate_ref is None:
                        reject('TABLE_OBSERVATION_REQUIRED')
                    measure = b.execution._dbt_measure_view if node['materialized'] == 'view' else b.execution.wap_measure
                    proof.update(native_verified=True, candidate=measure(b.candidate_ref))
                    if node['materialized'] == 'view' and proof['candidate'] != b.receipt['candidate']:
                        reject('DBT_VIEW_CHANGED')
                else:
                    proof.update(test_verified=True, status='PASSED' if b.failures == 0 else 'FAILED')
                b.execution.complete(proof)
            else:
                b.execution.fail()
            self.close()
        elif not skipped and (node.get('materialized') != 'ephemeral' or status not in ('success', 'error')
                or result['adapter_response'] != {} or result['failures'] is not None):
            reject('DBT_NATIVE_RUNNER_FAILED')
        with control_store.connect() as db:
            _, run = dbt._row(db, self.ident)
            item = next(n for n in run['models' if self.phase == 'build' else 'tests'] if n['unique_id'] == uid)
            item['_sdk_result'] = result
            item['state'] = ('SKIPPED' if skipped else 'EXPANDED' if node.get('materialized') == 'ephemeral' and status == 'success'
                else 'NATIVE_VERIFIED' if proof and self.phase == 'build' else proof['status'] if proof else 'FAILED')
            if self.bridge:
                b = self.bridge
                if self.phase == 'build':
                    item['operation_ids'] = [b.execution.id]
                    if proof:
                        item.update(candidate=dbt._view_public(proof['candidate']) if node['materialized'] == 'view' else proof['candidate'],
                            _measurement=proof['candidate'], native_receipt=proof,
                            dependency_snapshots=proof['pins'])
                else:
                    item['operation_id'] = b.execution.id
                    if proof:
                        item.update(failure_count=b.failures, native_receipt=proof)
            dbt._write(db, run)
        self.events[self.phase][uid] = result
        self.active, self.bridge = None, None
        self.execution = None
        return None

    def task_done(self, message):
        self.check()
        expected = {m['unique_id'] for m in (self.plan['models'] if self.phase == 'build' else self.plan['tests'])}
        result_ids = {m['unique_id'] for m in self.plan['models'] if m['materialized'] in ('table', 'view')} if self.phase == 'build' else expected
        if (self.phase in self.completed or self.active is not None or set(message) != {'op', 'phase', 'selected', 'results', 'success'}
                or message['phase'] != self.phase or type(message['success']) is not bool
                or not isinstance(message['selected'], list) or len(message['selected']) != len(expected)
                or set(message['selected']) != expected or set(self.events[self.phase]) != expected
                or not isinstance(message['results'], list) or any(not valid_result(r) for r in message['results'])
                or len(message['results']) != len(result_ids)
                or {r['node_id'] for r in message['results']} != result_ids
                or any(r != self.events[self.phase][r['node_id']] for r in message['results'])):
            reject('DBT_GRAPH_TASK_RECEIPT_INVALID')
        success = all(r['status'] == ('success' if self.phase == 'build' else 'pass') for r in self.events[self.phase].values())
        if message['success'] != success:
            reject('DBT_GRAPH_TASK_RECEIPT_INVALID')
        self.completed.add(self.phase)
        receipt = dict(task='RunTask' if self.phase == 'build' else 'TestTask', completed=True,
            selected=sorted(expected), results=message['results'], nodes=list(self.events[self.phase].values()), success=success)
        with control_store.connect() as db:
            _, run = dbt._row(db, self.ident)
            run.setdefault('task_receipts', {})[self.phase] = receipt
            if self.phase == 'build':
                run['native_verified'] = success
                run['state'] = 'TESTING' if success and run['native_tests'] else 'BUILDING'
            dbt._write(db, run)
        if self.phase == 'build' and success and run['native_tests']:
            self.phase = 'test'
            with control_store.connect() as db:
                _, run = dbt._row(db, self.ident)
                run.update(tests_status='RUNNING', _phase=None, _node_id=None)
                dbt._write(db, run)
        return None

    def handle(self, message):
        if not isinstance(message, dict):
            reject('DBT_NATIVE_PROTOCOL_FAILED')
        op = message.get('op')
        if op in ('list_schemas', 'list_relations', 'get_relation'):
            return self.inventory(message)
        if op == 'enter_node' and set(message) == {'op', 'phase', 'node_id', 'skipped'} and message['phase'] == self.phase:
            return self.enter(message['node_id'], message['skipped'])
        if op == 'end_node' and set(message) == {'op', 'phase', 'result'} and message['phase'] == self.phase:
            return self.end(message['result'])
        if op == 'task_done':
            return self.task_done(message)
        if op == 'execute' and set(message) == {'op', 'sql', 'auto_begin', 'fetch', 'limit'} and self.bridge and type(message['sql']) is str:
            try:
                return self.bridge.execute(message['sql'], message['auto_begin'], message['fetch'], message['limit'])
            except QueryError as error:
                # A definite backend failure is returned to the installed runner
                # so the real scheduler can mark downstream nodes skipped.
                if error.code != 'DATAOPS_STEP_FAILED':
                    raise
                with control_store.connect() as db:
                    queries = list(db.execute('SELECT q.state FROM dataops_queries d JOIN query_records q ON q.id=d.query_id '
                        'WHERE d.operation_id=?', (self.bridge.execution.id,)))
                self.check()
                if len(queries) != 1 or queries[0]['state'] != 'FAILED':
                    raise
                return {'_graph_error': 'DATAOPS_STEP_FAILED'}
        reject('DBT_NATIVE_PROTOCOL_FAILED')

    def close(self):
        try:
            if self.execution:
                execution = self.execution
                try:
                    if execution._row()['state'] == 'ACTIVE':
                        execution.fail()
                except (QueryError, admission.AdmissionConflict):
                    pass
                finally:
                    if self.bridge:
                        self.bridge.aborted = True
                    live = ds._live.pop(execution.id, None)
                    if live is not None:
                        live[2].close()
                    execution._inspect()
                    self.execution = None
        finally:
            if self.stack:
                self.stack.close()
                self.stack = None


def valid_result(result):
    return (isinstance(result, dict) and set(result) in (
            {'node_id', 'status', 'failures', 'adapter_response'},
            {'node_id', 'status', 'failures', 'adapter_response', 'model_type', 'view_security'})
        and type(result['node_id']) is str and result['status'] in ('success', 'error', 'skipped', 'pass', 'fail')
        and (result['failures'] is None or type(result['failures']) is int and result['failures'] >= 0)
        and isinstance(result['adapter_response'], dict)
        and set(result['adapter_response']) <= {'_message', 'code', 'rows_affected'}
        and (result['status'] not in ('error', 'skipped') or result['adapter_response'] == {}))


def _run_subprocess(artifacts, owner, tests):
    """SDK only in a fresh process; typed callbacks stay on this owning thread."""
    _, raw, command, env = artifacts
    worker = Path(__file__).with_name('database_dbt_graph_worker.py')
    request = dict(manifest=raw, command=command, native_tests=tests,
        targets={uid: [owner.context.sql_catalog, owner.context.sql_schema, loc['physical_name']]
                 for uid, loc in owner.locators.items()})
    child = None
    deadline = time.monotonic() + native._SDK_TIMEOUT
    buffered = bytearray()
    eof = object()

    def check():
        nonlocal deadline
        started = time.monotonic()
        try:
            # Protocol polling keeps scope/liveness checks without catalog scans.
            # All real callbacks and final SQL sends retain candidate validation.
            owner.check(candidates=False)
        finally:
            deadline += time.monotonic() - started
        if time.monotonic() >= deadline:
            reject('DBT_NATIVE_SDK_TIMEOUT')

    def callback(function, *args):
        nonlocal deadline
        started = time.monotonic()
        try:
            return function(*args)
        finally:
            # Parent SQL/metadata work has its own DS query/cancel checks.
            # Only SDK communication and child waiting consume this budget.
            deadline += time.monotonic() - started

    def send(value):
        data = memoryview((json.dumps(value, allow_nan=False) + '\n').encode())
        if len(data) > native._SDK_REQUEST_LIMIT:
            reject('DBT_NATIVE_PROTOCOL_FAILED')
        with selectors.DefaultSelector() as ready:
            ready.register(child.stdin, selectors.EVENT_WRITE)
            while data:
                check()
                if ready.select(.2):
                    try:
                        data = data[os.write(child.stdin.fileno(), data[:65536]):]
                    except BlockingIOError:
                        pass

    def receive():
        with selectors.DefaultSelector() as ready:
            ready.register(child.stdout, selectors.EVENT_READ)
            while True:
                check()
                if b'\n' in buffered:
                    line, _, rest = buffered.partition(b'\n')
                    buffered[:] = rest
                    if len(line) > native._SDK_MESSAGE_LIMIT:
                        reject('DBT_NATIVE_PROTOCOL_FAILED')
                    return json.loads(line)
                if len(buffered) > native._SDK_MESSAGE_LIMIT:
                    reject('DBT_NATIVE_PROTOCOL_FAILED')
                if ready.select(.2):
                    try:
                        chunk = os.read(child.stdout.fileno(), 65536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        if buffered:
                            reject('DBT_NATIVE_PROTOCOL_FAILED')
                        return eof
                    buffered.extend(chunk)

    try:
        check()
        child = subprocess.Popen([str(Path(command[0]).with_name('python3')), str(worker)],
            cwd=env['HOME'], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, bufsize=0)
        os.set_blocking(child.stdin.fileno(), False)
        os.set_blocking(child.stdout.fileno(), False)
        send(request)
        while True:
            message = receive()
            if not isinstance(message, dict):
                reject('DBT_NATIVE_PROTOCOL_FAILED')
            if message == {'op': 'done'}:
                child.stdin.close()
                if receive() is not eof:
                    reject('DBT_NATIVE_PROTOCOL_FAILED')
                while child.poll() is None:
                    check()
                    try:
                        child.wait(timeout=.2)
                    except subprocess.TimeoutExpired:
                        pass
                if child.returncode != 0:
                    reject('DBT_NATIVE_RUNNER_FAILED')
                return
            response = dict(result=callback(owner.handle, message))
            send(response)
    except BaseException as error:
        if isinstance(error, (QueryError, KeyboardInterrupt, SystemExit)):
            raise
        reject('DBT_NATIVE_PROTOCOL_FAILED')
    finally:
        if child is not None:
            for pipe in (child.stdin, child.stdout):
                if pipe is not None and not pipe.closed:
                    pipe.close()
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
            else:
                child.wait(timeout=5)



def child_proof(db, run, uid, phase, *, historical=False):
    """Exact persisted parent/child/query/identity and input proof, no live RPC."""
    try:
        from db_marts import _closed_child
        row, _ = dbt._row(db, run['id'])
        plan = dbt._check_plan(db, run)
        nodes = plan['models'] if phase == 'build' else plan['tests']
        node = next(n for n in nodes if n['unique_id'] == uid)
        links = list(db.execute('SELECT o.*,l.phase,l.node_id FROM dbt_run_operations l '
            'JOIN dataops_operations o ON o.id=l.operation_id WHERE l.run_id=? AND l.phase=? AND l.node_id=?',
            (run['id'], phase, uid)))
        if len(links) != 1:
            return None
        op = links[0]
        check_operation(db, run, op, op)
        expected = ds._json(dict(payload=payload(run, phase, uid), context=run['context']))
        receipt = json.loads(op['result'] or '{}')
        child = db.execute("SELECT * FROM warehouse_admissions WHERE kind='dataops' AND subject_id=? AND warehouse_id=?",
            (op['id'], run['context']['warehouse_id'])).fetchone()
        if (op['state'] != 'FINISHED' or bool(op['committed']) != (phase == 'build') or op['cleanup_error']
                or op['actor'] != run['actor'] or json.loads(op['context']) != run['context']
                or op['fingerprint'] != hashlib.sha256(expected.encode()).hexdigest()
                or child is None or child['parent_id'] != row['admission_id'] or child['actor'] != run['actor']
                or child['state'] != 'FINISHED' or not _closed_child(db, op['id'])
                or not isinstance(receipt, dict) or any(receipt.get(k) != v for k, v in payload(run, phase, uid).items())
                or receipt.get('native_verified' if phase == 'build' else 'test_verified') is not True):
            return None
        query_rows = list(db.execute('SELECT d.step,d.action,q.*,e.* FROM dataops_queries d '
            'LEFT JOIN query_records q ON q.id=d.query_id LEFT JOIN query_execution_contexts e ON e.query_id=d.query_id '
            'WHERE d.operation_id=?', (op['id'],)))
        if len(query_rows) != 1:
            return None
        query = query_rows[0]
        view = phase == 'build' and node['materialized'] == 'view'
        step = 'view' if view else 'native-create' if phase == 'build' else 'native-test'
        action = 'dbt_view_create' if view else 'dbt_native_create' if phase == 'build' else 'dbt_test'
        if (query['step'] != step or query['action'] != action
                or query['state'] != 'FINISHED' or not query['backend_query_id'] or not query['backend_query_id'].strip()
                or query['actor'] != run['actor'] or query['mode'] != 'dataops' or query['request_id'] != op['id']
                or query['worksheet_id'] != step or any(query[k] != run['context'][k] for k in run['context'])
                or any(query[k] != op[k] for k in ('backend_username', 'credential_snapshot_ref'))):
            return None
        all_ops = list(db.execute('SELECT o.* FROM dbt_run_operations l JOIN dataops_operations o ON o.id=l.operation_id '
            'WHERE l.run_id=?', (run['id'],)))
        if any(any(o[k] != op[k] for k in ('backend_username', 'credential_snapshot_ref')) for o in all_ops):
            return None
        pins = receipt.get('pins')
        bindings, approved = bindings_for(plan, run, node)
        originals = dict(run['_pins'])
        originals.update({ds._json(m['candidate']['ref']): m['_measurement'] for m in run['models']
            if m.get('native_receipt', {}).get('native_verified') is True})
        if not isinstance(pins, dict) or set(pins) != set(approved['sources']):
            return None
        if any(not isinstance(pins[a], dict) or pins[a].get('ref') != asdict(ref)
                or pins[a].get('kind') == 'VIEW' and not valid_view_measurement(pins[a])
                or ds._json(originals.get(ds._json(asdict(ref)))) != ds._json(pins[a]) for a, ref in approved['sources'].items()):
            return None
        item = next(m for m in run['models' if phase == 'build' else 'tests'] if m['unique_id'] == uid)
        if ds._json(item.get('native_receipt')) != ds._json(receipt):
            return None
        sdk = receipt.get('sdk_result')
        if not valid_result(sdk) or sdk['node_id'] != uid or sdk != item.get('_sdk_result'):
            return None
        if phase == 'build':
            candidate = receipt['candidate']
            public = dbt._view_public(candidate) if view else candidate
            ref = registry.ResourceRef(**candidate['ref'])
            if historical:
                saved_object = db.execute('SELECT * FROM object_bindings WHERE id=?', (ref.object_id,)).fetchone()
                obj = registry.ObjectBinding(**dict(saved_object)) if saved_object is not None else None
            else:
                obj = registry.get_object(db, ref.object_id)
            schema = registry.get_schema(db, ref.schema_id)
            owned = db.execute('SELECT * FROM dataops_owned_objects WHERE operation_id=? AND object_id=?',
                (op['id'], ref.object_id)).fetchone()
            locator = run['_graph_locators'][uid]
            if (item['state'] != 'NATIVE_VERIFIED' or ds._json(item.get('candidate')) != ds._json(public)
                    or item.get('materialized') != node['materialized']
                    or ds._json(item.get('_measurement')) != ds._json(candidate) or item.get('operation_ids') != [op['id']]
                    or ds._json(item.get('dependency_snapshots')) != ds._json(pins) or sdk['status'] != 'success'
                    or ref.database_id != run['context']['database_id'] or ref.schema_id != run['context']['schema_id']
                    or schema is None or schema.purpose != 'sandbox' or schema.database_id != ref.database_id
                    or obj is None or not historical and not obj.current or obj.kind != ('VIEW' if view else 'TABLE') or obj.schema_id != ref.schema_id
                    or obj.incarnation != ref.incarnation or not obj.backend_identity
                    or not view and not candidate.get('snapshot')
                    or owned is None or owned['incarnation'] != ref.incarnation or owned['purpose'] != 'stage'
                    or receipt.get('candidate_locator') != locator
                    or locator != dict(database_id=ref.database_id, schema_id=ref.schema_id, physical_name=obj.physical_name)):
                return None
            if view and not valid_view_measurement(candidate):
                return None
            if view and (candidate.get('kind') != 'VIEW' or 'snapshot' in candidate
                    or candidate.get('view_uuid') != obj.backend_identity or candidate.get('security') != 'INVOKER'
                    or candidate.get('_canonical_definition_digest') != item.get('_view_definition_digest')
                    or node.get('view_security') != 'INVOKER' or item.get('view_security') != 'INVOKER'
                    or sdk.get('model_type') != 'view' or sdk.get('view_security') != 'invoker'
                    or sdk['failures'] is not None or sdk['adapter_response'] != dict(_message='CREATE VIEW', code='CREATE VIEW')):
                return None
            if view:
                # The ledger must contain the lowered absent-only statement,
                # not the SDK's CREATE OR REPLACE callback or another target.
                prefix = 'CREATE VIEW ' + '.'.join(ds._identifier(p) for p in (
                    run['context']['sql_catalog'], run['context']['sql_schema'], locator['physical_name'])) + ' SECURITY INVOKER AS '
                if not query['sql'].startswith(prefix):
                    return None
                canonical = compiler._sql_tree(query['sql'][len(prefix):]).sql(dialect='trino')
                if hashlib.sha256(canonical.encode()).hexdigest() != item['_view_definition_digest']:
                    return None
        else:
            failures = receipt.get('failures')
            result = run.get('_graph_test_results', {}).get(uid)
            if (type(failures) is not int or failures < 0 or receipt.get('status') != ('PASSED' if failures == 0 else 'FAILED')
                    or not isinstance(result, dict) or type(result.get('failures')) is not int
                    or type(result.get('should_warn')) is not bool or type(result.get('should_error')) is not bool
                    or receipt.get('query_id') != query['id'] or item.get('operation_id') != op['id']
                    or type(item.get('failure_count')) is not int or item['failure_count'] != failures
                    or item['state'] != receipt['status'] or sdk['failures'] != failures
                    or sdk['status'] != ('pass' if failures == 0 else 'fail')
                    or result != dict(operation_id=op['id'], query_id=query['id'], failures=failures,
                        should_warn=failures != 0, should_error=failures != 0, pins=pins)):
                return None
        return receipt
    except (KeyError, TypeError, ValueError, AttributeError, IndexError, StopIteration, QueryError, compiler.DbtError):
        return None


def valid_view_measurement(candidate):
    """Strict saved VIEW identity and digests; a VIEW never carries a snapshot."""
    try:
        if (set(candidate) != {'kind', 'ref', 'view_uuid', 'version', 'security', 'schema_id',
                'columns', 'schema_fingerprint', '_definition_digest', '_canonical_definition_digest', '_head_digest'}
                or candidate['kind'] != 'VIEW' or candidate['security'] != 'INVOKER'
                or type(candidate['version']) is not int or candidate['version'] < 1
                or type(candidate['schema_id']) is not int or candidate['schema_id'] < 0
                or not isinstance(candidate['columns'], list) or not candidate['columns']
                or any(not isinstance(c, dict) or set(c) != {'name', 'type'}
                    or not isinstance(c['name'], str) or not c['name'] or not isinstance(c['type'], (str, dict))
                    for c in candidate['columns'])
                or len({c['name'] for c in candidate['columns']}) != len(candidate['columns'])
                or any(not isinstance(candidate[k], str) or not re.fullmatch('[0-9a-f]{64}', candidate[k])
                    for k in ('schema_fingerprint', '_definition_digest', '_canonical_definition_digest', '_head_digest'))):
            return False
        registry.ResourceRef(**candidate['ref'])
        uuid.UUID(candidate['view_uuid'])
        return True
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def check_verified(db, run):
    for item in run['models']:
        if item.get('native_receipt', {}).get('native_verified') is True:
            if child_proof(db, run, item['unique_id'], 'build') is None:
                reject('DBT_GRAPH_DEPENDENCY_UNVERIFIED')


def _failed_build_child(db, run, op):
    """A failed SDK node proves SQL failure only with a closed failed CREATE."""
    from db_marts import _closed_child
    check_operation(db, run, op, op)
    row, _ = dbt._row(db, run['id'])
    expected = ds._json(dict(payload=payload(run, 'build', op['node_id']), context=run['context']))
    child = db.execute("SELECT * FROM warehouse_admissions WHERE kind='dataops' AND subject_id=? AND warehouse_id=?",
        (op['id'], run['context']['warehouse_id'])).fetchone()
    parent = db.execute('SELECT * FROM warehouse_admissions WHERE id=?', (row['admission_id'],)).fetchone()
    if (op['state'] != 'FAILED' or op['committed'] != 0 or op['cleanup_error']
            or op['actor'] != run['actor'] or json.loads(op['context']) != run['context']
            or op['fingerprint'] != hashlib.sha256(expected.encode()).hexdigest()
            or child is None or parent is None or child['parent_id'] != parent['id']
            or child['state'] != 'FAILED' or not child['finished_at'] or child['execution_closure']
            or any(child[k] != parent[k] for k in ('actor', 'warehouse_id', 'database_id', 'generation_id'))
            or child['actor'] != run['actor'] or child['database_id'] != run['context']['database_id']):
        return False
    rows = list(db.execute('SELECT d.step,d.action,q.*,e.* FROM dataops_queries d '
        'LEFT JOIN query_records q ON q.id=d.query_id LEFT JOIN query_execution_contexts e ON e.query_id=d.query_id '
        'WHERE d.operation_id=?', (op['id'],)))
    if len(rows) != 1:
        return False
    query = rows[0]
    node = next(m for m in dbt._check_plan(db, run)['models'] if m['unique_id'] == op['node_id'])
    step, action = ('view', 'dbt_view_create') if node['materialized'] == 'view' else ('native-create', 'dbt_native_create')
    if (query['step'] != step or query['action'] != action
            or query['state'] != 'FAILED' or not query['backend_query_id'] or not query['backend_query_id'].strip()
            or query['actor'] != run['actor'] or query['mode'] != 'dataops' or query['request_id'] != op['id']
            or query['worksheet_id'] != step or any(query[k] != run['context'][k] for k in run['context'])
            or any(query[k] != op[k] for k in ('backend_username', 'credential_snapshot_ref'))):
        return False
    admissions = list(db.execute('SELECT * FROM warehouse_admissions WHERE parent_id=?', (child['id'],)))
    if len(admissions) != 1:
        return False
    admitted = admissions[0]
    if (admitted['kind'] != 'query' or admitted['subject_id'] != query['id'] or admitted['state'] != 'FAILED'
            or not admitted['finished_at'] or admitted['execution_closure']
            or any(admitted[k] != child[k] for k in ('actor', 'warehouse_id', 'database_id', 'generation_id'))):
        return False
    all_ops = db.execute('SELECT o.* FROM dbt_run_operations l JOIN dataops_operations o ON o.id=l.operation_id '
        'WHERE l.run_id=?', (run['id'],))
    return (not any(any(o[k] != op[k] for k in ('backend_username', 'credential_snapshot_ref')) for o in all_ops)
        and _closed_child(db, op['id']))


def _task_completion_proof(db, run, phase, *, historical=False):
    """Validate completion, including SDK error/skipped nodes, without claiming success."""
    try:
        plan = dbt._check_plan(db, run)
        nodes = plan['models'] if phase == 'build' else plan['tests']
        items = run['models'] if phase == 'build' else run['tests']
        expected = {n['unique_id'] for n in nodes}
        receipt = run.get('task_receipts', {}).get(phase)
        if (not isinstance(receipt, dict) or set(receipt) != {'task', 'completed', 'selected', 'results', 'nodes', 'success'}
                or receipt['task'] != ('RunTask' if phase == 'build' else 'TestTask')
                or receipt['completed'] is not True or type(receipt['success']) is not bool
                or receipt['selected'] != sorted(expected) or len(items) != len(expected)
                or {m['unique_id'] for m in items} != expected
                or len(receipt['nodes']) != len(expected) or any(not valid_result(r) for r in receipt['nodes'])
                or {r['node_id'] for r in receipt['nodes']} != expected):
            return False
        results = {r['node_id']: r for r in receipt['nodes']}
        actual = [n for n in nodes if n.get('materialized') != 'ephemeral']
        if (len(receipt['results']) != len(actual) or any(not valid_result(r) for r in receipt['results'])
                or {r['node_id'] for r in receipt['results']} != {n['unique_id'] for n in actual}
                or any(r != results[r['node_id']] for r in receipt['results'])
                or receipt['success'] != all(r['status'] == ('success' if phase == 'build' else 'pass') for r in results.values())):
            return False
        for item in items:
            uid = item['unique_id']
            if phase == 'build' and item.get('materialized') != next(n['materialized'] for n in nodes if n['unique_id'] == uid):
                return False
            if phase == 'build' and item['materialized'] == 'view' and (
                    results[uid].get('model_type') != 'view' or results[uid].get('view_security') != 'invoker'):
                return False
            if item.get('_sdk_result') != results[uid]:
                return False
            if phase == 'build' and results[uid]['status'] in ('error', 'skipped'):
                skipped = results[uid]['status'] == 'skipped'
                node = next(n for n in nodes if n['unique_id'] == uid)
                deps = ancestors(plan, node) & expected
                failed = any(results[d]['status'] in ('error', 'skipped') for d in deps)
                links = list(db.execute('SELECT o.*,l.phase,l.node_id FROM dbt_run_operations l '
                    'JOIN dataops_operations o ON o.id=l.operation_id '
                    'WHERE l.run_id=? AND l.phase=? AND l.node_id=?', (run['id'], phase, uid)))
                if (item['state'] != ('SKIPPED' if skipped else 'FAILED') or skipped != failed
                        or item.get('native_receipt', {}).get('native_verified') is True):
                    return False
                if skipped or item.get('materialized') == 'ephemeral':
                    if links or item['operation_ids']:
                        return False
                else:
                    if len(links) != 1 or item['operation_ids'] != [links[0]['id']] or links[0]['state'] != 'FAILED':
                        return False
                    if not _failed_build_child(db, run, links[0]):
                        return False
                continue
            if item.get('materialized') == 'ephemeral':
                if (item['state'] != 'EXPANDED' or results[uid]['status'] != 'success' or item['operation_ids']
                        or results[uid]['adapter_response'] != {} or results[uid]['failures'] is not None):
                    return False
            elif child_proof(db, run, uid, phase, historical=historical) is None:
                return False
        return True
    except (KeyError, TypeError, ValueError, QueryError, compiler.DbtError):
        return False


def task_proof(db, run, phase, *, historical=False):
    return (_task_completion_proof(db, run, phase, historical=historical)
        and (phase != 'build' or run['task_receipts'][phase]['success']))



def publication_proof(db, run, uid, publication=None, *, historical=False):
    """Whole saved task and quality closure for one explicit TABLE publication."""
    forbidden = 'DBT_GRAPH_PUBLICATION_FORBIDDEN'
    try:
        plan = dbt._check_plan(db, run)
        if (run['kind'] != 'run' or run.get('executor') != 'native_graph'
                or not historical and (run['state'] != 'NATIVE_VERIFIED' or run.get('native_verified') is not True
                    or run.get('cancel_requested') is not False or run.get('tests_status') != 'PASSED')
                or run.get('_graph_sdk_closed') is not True or run.get('native_tests') is not True
                or not plan['tests']
                or [t['unique_id'] for t in run['tests']] != [t['unique_id'] for t in plan['tests']]
                or any(t['state'] != 'PASSED' or type(t['failure_count']) is not int
                    or t['failure_count'] != 0 for t in run['tests'])
                or any(not task_proof(db, run, phase, historical=historical)
                    or run['task_receipts'][phase]['success'] is not True for phase in ('build', 'test'))):
            reject(forbidden)
        model = next(m for m in run['models'] if m['unique_id'] == uid)
        if model['materialized'] != 'table':
            reject(forbidden)
        proofs = {m['unique_id']: child_proof(db, run, m['unique_id'], 'build', historical=historical)
            for m in run['models'] if m['materialized'] in ('table', 'view')}
        if any(p is None for p in proofs.values()) or uid not in proofs:
            reject(forbidden)
        if not historical and model.get('publication_id') and model['publication_id'] != publication:
            reject('DBT_PUBLICATION_EXISTS')
        return model, proofs[uid]
    except (KeyError, TypeError, ValueError, StopIteration, compiler.DbtError):
        reject(forbidden)


def settle(db, run, operations, *, closed, owner_lost):
    """Observation only: retain proven builds and all unverified committed outputs."""
    build = task_proof(db, run, 'build')
    quality = run.get('native_tests') and task_proof(db, run, 'test')
    for op in operations:
        try:
            evidence = json.loads(op['result'] or '{}')
        except (ValueError, TypeError):
            evidence = {}
        if op['phase'] == 'build':
            model = next((m for m in run['models'] if m['unique_id'] == op['node_id']), None)
            if model:
                model['operation_ids'] = [op['id']]
                proof = child_proof(db, run, op['node_id'], 'build')
                if proof is None:
                    model['native_receipt'] = dict(evidence, native_verified=False) if isinstance(evidence, dict) else dict(native_verified=False)
                    for key in ('candidate', 'candidate_locator'):
                        if isinstance(evidence, dict) and key in evidence:
                            model[key] = evidence[key]
                    if model['state'] == 'NATIVE_VERIFIED':
                        model['state'] = 'FAILED' if closed else 'UNKNOWN'
    run['native_verified'] = build
    run['publication_allowed'] = False
    if not closed:
        run['state'] = 'UNKNOWN'
    elif run['cancel_requested']:
        run['state'] = 'CANCELED'
    elif (run['state'] == 'NATIVE_VERIFIED' and build and run.get('_graph_sdk_closed') is True
            and (not run['native_tests'] or quality and run['tests_status'] == 'PASSED')):
        pass
    else:
        run['state'] = 'FAILED'
    for item in run['models']:
        if item['state'] in ('QUEUED', 'BUILDING', 'TESTING'):
            item['state'] = 'UNKNOWN' if not closed or owner_lost else 'CANCELED' if run['cancel_requested'] else 'FAILED'
    if run['native_tests']:
        pending = 'UNKNOWN' if not closed or owner_lost else 'CANCELED' if run['cancel_requested'] else 'ERROR'
        for test in run['tests']:
            if test['state'] in ('QUEUED', 'TESTING', 'RUNNING') or test['state'] == 'FAILED' and test.get('failure_count') is None:
                test['state'] = pending
        if not closed:
            run['tests_status'] = 'UNKNOWN'
        elif run['cancel_requested']:
            run['tests_status'] = 'CANCELED'
        elif quality:
            run['tests_status'] = 'FAILED' if any(t['state'] == 'FAILED' for t in run['tests']) else 'PASSED'
        else:
            run['tests_status'] = 'UNKNOWN' if owner_lost else 'ERROR'
    if run['state'] == 'FAILED' and not run['error_code']:
        run['error_code'] = 'DBT_OWNER_LOST' if owner_lost else 'DBT_GRAPH_TASK_INCOMPLETE'
    run.update(_phase=None, _node_id=None)


def execute(ident, principal, context, parent, frozen, credential):
    run = dbt._row_run(ident)
    metadata = dbt._access(principal, context)
    with control_store.connect() as db:
        plan = dbt._check_plan(db, run)
        native._saved_plan(db, principal, context, plan)
    validate(metadata, context, plan, tests=run['native_tests'])
    pins = {ds._json(s['ref']): metadata.wap_measure(registry.ResourceRef(**s['ref'])) for s in plan['source_bindings']}
    locators = {m['unique_id']: dict(database_id=context.database_id, schema_id=context.schema_id,
        physical_name='g_' + secrets.token_hex(8)) for m in plan['models'] if m['materialized'] in ('table', 'view')}
    with control_store.connect() as db:
        _, run = dbt._row(db, ident)
        run.update(_pins=pins, _graph_locators=locators, state='BUILDING')
        dbt._write(db, run)
    owner = Graph(ident, principal, context, parent, frozen, credential, plan, locators)
    try:
        project = dbt._projects().get(plan['project_id'])
        if project is None:
            reject('DBT_PROJECT_NOT_FOUND')
        with compiler._compiled_project(project['path'], catalog=context.sql_catalog,
                source_schema=plan['_source_sql_schema'], staging_materialization=plan['staging_materialization']) as artifacts:
            native._fresh_summary(plan, artifacts)
            _run_subprocess(artifacts, owner, run['native_tests'])
        owner.check()
        with control_store.connect() as db:
            _, run = dbt._row(db, ident)
            if not _task_completion_proof(db, run, 'build'):
                reject('DBT_GRAPH_TASK_RECEIPT_INVALID')
            if run['task_receipts']['build']['success'] is False:
                if 'test' in run.get('task_receipts', {}):
                    reject('DBT_GRAPH_TASK_RECEIPT_INVALID')
                run.update(state='FAILED', native_verified=False, publication_allowed=False,
                    tests_status='ERROR' if run['native_tests'] else 'NOT_RUN',
                    error_code='DBT_GRAPH_BUILD_FAILED', _graph_sdk_closed=True, _phase=None, _node_id=None)
                if run['native_tests']:
                    for test in run['tests']:
                        test['state'] = 'ERROR'
                dbt._write(db, run)
                return
            build = task_proof(db, run, 'build')
            quality = not run['native_tests'] or task_proof(db, run, 'test')
            if not build or not quality:
                reject('DBT_GRAPH_TASK_RECEIPT_INVALID')
            passed = not run['native_tests'] or all(t['state'] == 'PASSED' for t in run['tests'])
            run.update(state='NATIVE_VERIFIED' if passed else 'FAILED', native_verified=True,
                tests_status=('PASSED' if passed else 'FAILED') if run['native_tests'] else 'NOT_RUN',
                error_code=None if passed else 'DBT_TEST_FAILED', _graph_sdk_closed=True, _phase=None, _node_id=None)
            dbt._write(db, run)
    finally:
        owner.close()
