"""Single TABLE SDK bridge for durable native runs and private operator proof.

Only ModelRunner.execute and the installed TABLE macro run. The normal durable
path isolates SDK globals in a fresh process; parent DataOps owns all backend
work. The standalone operator retains its serial, exclusive admission contract.
Neither path creates a WAP publication artifact or resumes saved receipts.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
import importlib.metadata
import json
import os
import threading
import subprocess
import selectors
import time
from pathlib import Path

import control_store
import database_dbt as compiler
import data_management as registry
import dataops_service as ds
import db_dbt
import manual_wap
from query_context import QueryError
import warehouse_admission as admission

_ROUTE = 'dbt-native-feasibility'
_PROVENANCE = dict(experimental=True, executor='dbt_native_model_runner',
                   materialization='materialization_table_trino')
_process = threading.Lock()
_SDK_TIMEOUT = 120
_SDK_MESSAGE_LIMIT = 1024 * 1024
_SDK_REQUEST_LIMIT = 32 * 1024 * 1024


def _reject(code='DBT_NATIVE_UNSUPPORTED'):
    raise QueryError(code, 409)


def _plan(principal, context, plan_id, node_id):
    metadata = db_dbt._access(principal, context)
    with control_store.connect() as db:
        plan = db_dbt._authorized(db, principal, context, plan_id, 'plan')
        if plan['actor'] != principal.principal_id or plan['context'] != asdict(context):
            _reject('CONTEXT_MISMATCH')
        if (plan['state'] != 'READY' or plan.get('compatibility') != compiler.compatibility()
                or db_dbt._hash_plan(plan) != plan.get('_plan_digest')):
            _reject('DBT_PLAN_CHANGED')
        db_dbt._source_identities(db, plan)
    models = {m['unique_id']: m for m in plan['models']}
    node = models.get(node_id)
    if node is None or node['materialized'] != 'table' or node.get('table_properties') != {'format': 'PARQUET'}:
        _reject()
    sources = {s['unique_id']: s for s in plan['source_bindings']}
    pending, seen = list(node['depends_on']), set()
    while pending:
        uid = pending.pop()
        if uid in seen:
            continue
        seen.add(uid)
        if uid in sources:
            continue
        dep = models.get(uid)
        if dep is None or dep['materialized'] != 'ephemeral':
            _reject('DBT_NATIVE_MATERIALIZED_DEPENDENCY')
        pending.extend(dep['depends_on'])
    used = {compiler._identity(t) for _, t in compiler._physical(compiler._sql_tree(node['compiled_sql']))}
    bindings = {tuple(s['relation']): registry.ResourceRef(**s['ref'])
                for uid, s in sources.items() if uid in seen and tuple(s['relation']) in used}
    for ref in bindings.values():
        schema = metadata._schema(ref.schema_id)
        if schema.sql_name != 'raw' or schema.purpose != 'raw':
            _reject('DBT_NATIVE_RAW_REQUIRED')
    normalized = compiler.normalize_select(node['compiled_sql'], bindings, database_id=context.database_id)
    return plan, node, bindings, normalized


def _native_select(sql, target, bindings, approved, database_id):
    """Accept one exact CTAS; return the approved canonical read model only."""
    import sqlglot
    from sqlglot import exp
    try:
        trees = sqlglot.parse(sql, read='trino', error_level=sqlglot.ErrorLevel.RAISE)
        if len(trees) != 1 or not isinstance(trees[0], exp.Create):
            _reject()
        tree = trees[0]
        if tree.kind != 'TABLE' or any(n.comments for n in tree.walk()):
            _reject()
        if any(v for k, v in tree.args.items() if k not in ('this', 'kind', 'expression', 'properties')):
            _reject()
        if not isinstance(tree.this, exp.Table) or compiler._identity(tree.this) != target:
            _reject()
        if any(v for k, v in tree.this.args.items() if k not in ('this', 'db', 'catalog')):
            _reject()
        props = tree.args.get('properties')
        # Compare typed ASTs: duplicates, other properties and expressions fail.
        expected = sqlglot.parse_one("CREATE TABLE c.s.t WITH (format='PARQUET') AS SELECT 1", read='trino').args['properties']
        if not isinstance(props, exp.Properties):
            _reject()
        props = props.copy()
        for prop in props.expressions:
            if isinstance(prop, exp.FileFormatProperty) and isinstance(prop.this, exp.Literal) and prop.this.is_string:
                prop.this.set('this', prop.this.this.upper())
        if props != expected:
            _reject()
        body = tree.expression
        if isinstance(body, exp.Subquery) and not any(v for k, v in body.args.items() if k != 'this'):
            body = body.this
        if not isinstance(body, (exp.Select, exp.SetOperation)):
            _reject()
        normalized = compiler.normalize_select(body.sql(dialect='trino'), bindings, database_id=database_id)
        if normalized != approved:
            _reject('DBT_NATIVE_BODY_CHANGED')
        return normalized
    except (sqlglot.errors.SqlglotError, ValueError, AttributeError):
        _reject()


def _check_inputs(execution, inputs, sources):
    for alias, ref in sources.items():
        current = execution.wap_measure(ref)
        if any(current[k] != inputs[alias][k] for k in ('ref', 'schema_id', 'schema_fingerprint')):
            _reject('SOURCE_SCHEMA_CHANGED')


class _Bridge:
    """One caller-thread capability, one native callback, one fixed write step."""
    def __init__(self, execution, locator, bindings, approved, receipt, *, wire=False):
        self.wire = wire
        self.execution, self.locator = execution, locator
        self.bindings, self.approved, self.receipt = bindings, approved, receipt
        self.thread = threading.get_ident()
        self.parent = admission.current()
        self.aborted, self.called, self.response = False, False, None
        self.candidate_ref = None
        context = execution._context()
        self.target = (context.sql_catalog, execution._schema(locator.schema_id, write=True).sql_name, locator.physical_name)
        self.inputs = (db_dbt._pinned(execution, approved['sources']) if ds._dbt.get() else
                       {alias: execution.wap_measure(ref) for alias, ref in approved['sources'].items()})
        self.receipt.update(candidate_locator=asdict(locator), pins=self.inputs)

    def _scope(self):
        if (self.aborted or threading.get_ident() != self.thread or self.parent is None
                or admission.current() is not self.parent):
            self.aborted = True
            _reject('DBT_NATIVE_CALLBACK_SCOPE')
        self.execution._active()

    def deny(self, *args, **kwargs):
        self.aborted = True
        _reject()

    def relation(self, database, schema, identifier):
        try:
            self._scope()
            if (database, schema) != self.target[:2] or identifier not in {
                    self.target[2], self.target[2] + '__dbt_tmp', self.target[2] + '__dbt_backup'}:
                _reject('DBT_NATIVE_RELATION_LOOKUP')
            self.execution._absent(registry.DestinationLocator(self.locator.database_id, self.locator.schema_id, identifier))
            return None
        except BaseException:
            self.aborted = True
            raise

    def execute(self, sql, auto_begin=False, fetch=False, limit=None):
        try:
            self._scope()
            # statement('main') requests auto_begin=True. No native connection
            # is opened: DS alone owns the actual engine request/transaction.
            if self.called or limit is not None or type(auto_begin) is not bool or type(fetch) is not bool:
                _reject('DBT_NATIVE_CALLBACK_REUSED')
            self.called = True
            context = self.execution._context()
            plan = _plan(self.execution.principal, context, self.receipt['plan_id'], self.receipt['node_id'])[0]
            if plan['_plan_digest'] != self.receipt['digest']:
                _reject('DBT_PLAN_CHANGED')
            _native_select(sql, self.target, self.bindings, self.approved, context.database_id)
            _check_inputs(self.execution, self.inputs, self.approved['sources'])
            generated = manual_wap._compile_model(**self.approved, execution=self.execution, measurements=self.inputs)
            self.execution._absent(self.locator)
            # Safe metadata survives ACK loss or owner death; no raw SQL or paths.
            with control_store.connect() as db:
                db.execute("UPDATE dataops_operations SET result=? WHERE id=? AND state='ACTIVE'",
                           (ds._json(self.receipt), self.execution.id))
            page = self.execution._execute('dbt_native_create', 'CREATE TABLE ' + self.execution._locator(self.locator)
                + " WITH (format='PARQUET') AS " + generated, 'native-create', commit_result=self.receipt)
            self.candidate_ref = self.execution._observe(self.locator)
            with control_store.connect() as db:
                db.execute('INSERT INTO dataops_owned_objects VALUES (?,?,?,?)',
                           (self.execution.id, self.candidate_ref.object_id, self.candidate_ref.incarnation, 'stage'))
            if self.wire:
                self.response = dict(_message='CREATE TABLE', code='CREATE TABLE', rows_affected=page.get('update_count'))
                return self.response
            from dbt.adapters.contracts.connection import AdapterResponse
            import agate
            self.response = AdapterResponse(_message='CREATE TABLE', code='CREATE TABLE',
                                            rows_affected=page.get('update_count'))
            return self.response, agate.Table(page.get('data', []),
                column_names=[c['name'] for c in page.get('columns', [])])
        except BaseException:
            self.aborted = True
            raise


@contextmanager
def _without_dotenv():
    """Suppress dbt.cli.main's import-time dotenv loader, changing one key only."""
    key = 'PYTHON_DOTENV_DISABLED'
    saved = os.environ.get(key)
    os.environ[key] = '1'
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = saved


def _builtin_manifest(raw):
    # External packages are already excluded by compiler preflight. A root
    # project must not impersonate an installed execution namespace.
    namespaces = {'dbt', 'dbt_trino'}
    if (raw['metadata']['project_name'] in namespaces
            or any(n['package_name'] in namespaces for n in raw['nodes'].values())):
        _reject('DBT_NATIVE_MACRO_ORIGIN')
    manifest = deepcopy(raw)
    manifest['macros'] = {uid: macro for uid, macro in manifest['macros'].items()
                          if macro['package_name'] in namespaces
                          and uid == 'macro.' + macro['package_name'] + '.' + macro['name']}
    return manifest


@contextmanager
def _runtime(artifacts):
    """Load actual installed objects with exclusively dummy configuration.

    The operator process is serial. No adapter/global connection cache is shared
    with another invocation. The compile subprocess used the same private copy.
    """
    with _without_dotenv():
        from dbt.cli.main import cli
    from dbt.adapters.factory import FACTORY, register_adapter, get_adapter, reset_adapters
    from dbt.cli.flags import Flags
    from dbt.flags import set_flags, get_flags
    from dbt.config.runtime import RuntimeConfig
    from dbt.contracts.graph.manifest import Manifest
    from dbt.context.providers import generate_runtime_macro_context
    from dbt_common.context import reliably_get_invocation_var, InvocationContext
    from dbt_common.clients.jinja import template_cache
    from dbt.mp_context import get_mp_context
    import dbt.tracking
    _, raw, command, env = artifacts
    if FACTORY.adapters:
        _reject('DBT_NATIVE_PROCESS_NOT_ISOLATED')
    saved_flags = get_flags()
    invocation_var = reliably_get_invocation_var()
    token = invocation_var.set(InvocationContext(env))
    try:
        dbt.tracking.disable_tracking()
        with cli.make_context('dbt', command[1:]) as root:
            with cli.commands['compile'].make_context('compile', root.args, parent=root) as ctx:
                flags = Flags(ctx)
                set_flags(flags)
                config = RuntimeConfig.from_args(flags)
        template_cache.clear()
        register_adapter(config, get_mp_context())
        adapter = get_adapter(config)
        manifest = Manifest.from_dict(_builtin_manifest(raw))
        adapter.set_macro_resolver(manifest)
        adapter.set_macro_context_generator(generate_runtime_macro_context)
        # CLI paths/profile are explicit and Jinja env_var sees only env.
        # DS keeps the original host store/auth/HOME even during SDK setup.
        yield config, manifest, adapter
    finally:
        reset_adapters()
        template_cache.clear()
        set_flags(saved_flags)
        invocation_var.reset(token)


def _run_native(artifacts, node_id, bridge):
    with _runtime(artifacts) as (config, manifest, adapter):
        from dbt.task.run import ModelRunner
        from dbt.artifacts.schemas.results import RunStatus
        node = manifest.nodes[node_id]
        if compiler.normalize_select(node.compiled_code, bridge.bindings,
                database_id=bridge.locator.database_id) != bridge.approved:
            _reject('DBT_NATIVE_BODY_CHANGED')
        node.database, node.schema, node.alias = bridge.target
        node.relation_name = str(adapter.Relation.create(database=node.database, schema=node.schema, identifier=node.alias))
        runner = ModelRunner(config, adapter, node, 1, 1)
        macro = runner._get_materialization_macro(node, manifest)
        if macro.unique_id != 'macro.dbt_trino.materialization_table_trino':
            _reject()
        adapter.execute = bridge.execute
        adapter.get_relation = bridge.relation
        adapter.connections.open = bridge.deny
        # These methods must never fall through to a DBAPI or metadata path.
        for name in ('add_query', 'execute'):
            setattr(adapter.connections, name, bridge.deny)
        for name in ('create_schema', 'drop_schema', 'drop_relation', 'rename_relation',
                     'get_columns_in_relation', 'list_relations_without_caching'):
            setattr(adapter, name, bridge.deny)
        try:
            result = runner.execute(node, manifest)
            if (result.status != RunStatus.Success or bridge.aborted or not bridge.called
                    or bridge.response is None or result.adapter_response != bridge.response.to_dict(omit_none=True)):
                _reject('DBT_NATIVE_RUNNER_FAILED')
            if compiler.normalize_select(node.compiled_code, bridge.bindings,
                    database_id=bridge.locator.database_id) != bridge.approved:
                _reject('DBT_NATIVE_BODY_CHANGED')
            return result
        except BaseException:
            bridge.aborted = True
            raise


def _run_subprocess(artifacts, node_id, bridge):
    """SDK only in a fresh process; typed callbacks stay on this owning thread."""
    _, raw, command, env = artifacts
    worker = Path(__file__).with_name('database_dbt_native_worker.py')
    request = dict(manifest=_builtin_manifest(raw), command=command, node_id=node_id,
        target=bridge.target, database_id=bridge.locator.database_id,
        bindings=[dict(relation=k, ref=asdict(v)) for k, v in bridge.bindings.items()],
        approved=dict(bridge.approved, sources={k: asdict(v) for k, v in bridge.approved['sources'].items()}))
    child = None
    deadline = time.monotonic() + _SDK_TIMEOUT
    buffered = bytearray()
    eof = object()

    def check():
        bridge._scope()
        if time.monotonic() >= deadline:
            _reject('DBT_NATIVE_SDK_TIMEOUT')

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
        if len(data) > _SDK_REQUEST_LIMIT:
            _reject('DBT_NATIVE_PROTOCOL_FAILED')
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
                    if len(line) > _SDK_MESSAGE_LIMIT:
                        _reject('DBT_NATIVE_PROTOCOL_FAILED')
                    return json.loads(line)
                if len(buffered) > _SDK_MESSAGE_LIMIT:
                    _reject('DBT_NATIVE_PROTOCOL_FAILED')
                if ready.select(.2):
                    try:
                        chunk = os.read(child.stdout.fileno(), 65536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        if buffered:
                            _reject('DBT_NATIVE_PROTOCOL_FAILED')
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
                _reject('DBT_NATIVE_PROTOCOL_FAILED')
            if message == {'op': 'done', 'status': 'success'}:
                child.stdin.close()
                if receive() is not eof:
                    _reject('DBT_NATIVE_PROTOCOL_FAILED')
                while child.poll() is None:
                    check()
                    try:
                        child.wait(timeout=.2)
                    except subprocess.TimeoutExpired:
                        pass
                if child.returncode != 0 or bridge.aborted or not bridge.called or bridge.response is None:
                    _reject('DBT_NATIVE_RUNNER_FAILED')
                return
            if set(message) == {'op', 'database', 'schema', 'identifier'} and message['op'] == 'get_relation':
                if any(type(message[k]) is not str for k in ('database', 'schema', 'identifier')):
                    _reject('DBT_NATIVE_PROTOCOL_FAILED')
                callback(bridge.relation, message['database'], message['schema'], message['identifier'])
                response = dict(result=None)
            elif set(message) == {'op', 'sql', 'auto_begin', 'fetch', 'limit'} and message['op'] == 'execute':
                if (type(message['sql']) is not str or type(message['auto_begin']) is not bool
                        or type(message['fetch']) is not bool or message['limit'] is not None):
                    _reject('DBT_NATIVE_PROTOCOL_FAILED')
                response = dict(result=callback(bridge.execute, message['sql'], message['auto_begin'], message['fetch'], message['limit']))
            else:
                _reject('DBT_NATIVE_PROTOCOL_FAILED')
            send(response)
    except BaseException as error:
        bridge.aborted = True
        if isinstance(error, (QueryError, KeyboardInterrupt, SystemExit)):
            raise
        _reject('DBT_NATIVE_PROTOCOL_FAILED')
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


def _idle():
    import warehouse_resident
    warehouse_resident.check_lifecycle()
    with control_store.connect() as db:
        # LOCAL/XS/S share physical capacity, so check every logical alias.
        if any(admission.execution_unresolved(r) for r in db.execute('SELECT * FROM warehouse_admissions')):
            _reject('DBT_NATIVE_WAREHOUSE_BUSY')


def _replay(principal, context, plan_id, node_id, key):
    with control_store.connect() as db:
        old = db.execute('SELECT id,payload FROM dataops_operations WHERE actor=? AND route=? AND idempotency_key=?',
                         (principal.principal_id, _ROUTE, key)).fetchone()
    if old is None:
        return None
    saved = json.loads(old['payload'])
    payload = dict(plan_id=plan_id, node_id=node_id, digest=saved['payload']['digest'], **_PROVENANCE)
    if old['payload'] != ds._json(dict(payload=payload, context=asdict(context))):
        _reject('IDEMPOTENCY_CONFLICT')
    return ds.Execution(principal, old['id'], replayed=True).inspect()


def materialize_accepted(execution, principal, context, plan, node, bindings, approved, payload, *, isolated=False):
    """Execute one accepted child; caller owns admission, authority and lifetime."""
    plan_id, node_id = plan['id'], node['unique_id']
    runner = _run_subprocess if isolated else _run_native
    bridge = None
    try:
        with ds._accepted(execution):
            fresh = _plan(principal, context, plan_id, node_id)
            if fresh != (plan, node, bindings, approved):
                _reject('DBT_PLAN_CHANGED')
            project = db_dbt._projects().get(plan['project_id'])
            if project is None:
                _reject('DBT_PROJECT_NOT_FOUND')
            with compiler._compiled_project(project['path'], catalog=context.sql_catalog,
                    source_schema=plan['_source_sql_schema'],
                    staging_materialization=plan['staging_materialization']) as artifacts:
                summary = artifacts[0]
                if (summary['source_digest'] != plan['source_digest'] or summary['models'] != plan['models']
                        or summary['tests'] != plan['tests'] or summary['compatibility'] != plan['compatibility']
                        or summary['sources'] != [{k: v for k, v in s.items() if k != 'ref'}
                                                 for s in plan['source_bindings']]):
                    _reject('DBT_PLAN_CHANGED')
                locator = registry.DestinationLocator(context.database_id, context.schema_id,
                                                     'n_' + execution.id.replace('-', '')[:17])
                receipt = dict(payload, native_verified=False)
                bridge = _Bridge(execution, locator, bindings, approved, receipt, wire=isolated)
                runner(artifacts, node_id, bridge)
                # The callback registered the exact created UUID. A
                # positive backend response alone never verifies it.
                if bridge.candidate_ref is None:
                    _reject('TABLE_OBSERVATION_REQUIRED')
                candidate = execution.wap_measure(bridge.candidate_ref)
                _check_inputs(execution, bridge.inputs, approved['sources'])
                _plan(principal, context, plan_id, node_id)
                return execution.complete(dict(receipt, native_verified=True, candidate=candidate))
    except BaseException:
        if bridge is not None:
            bridge.aborted = True
        # Generic reconciliation preserves UNKNOWN/cancel/owner loss.
        # Never clean up, retry, resume or claim native verification.
        try:
            if execution._row()['state'] == 'ACTIVE':
                execution.fail()
        except (QueryError, admission.AdmissionConflict):
            pass
        finally:
            live = ds._live.pop(execution.id, None)
            if live is not None:
                live[2].close()
            execution._inspect()
        raise


def materialize(principal, context, *, plan_id, node_id, key):
    """On the operator main thread, produce a private TABLE candidate/DS receipt.

    A same-key replay is observation only, including UNKNOWN. No recompile,
    runner, credentials capture, idle requirement or SQL follows that lookup.
    """
    observed = _replay(principal, context, plan_id, node_id, key)
    if observed is not None:
        return observed
    if threading.current_thread() is not threading.main_thread():
        _reject('DBT_NATIVE_PROCESS_NOT_ISOLATED')
    plan, node, bindings, approved = _plan(principal, context, plan_id, node_id)
    payload = dict(plan_id=plan_id, node_id=node_id, digest=plan['_plan_digest'], **_PROVENANCE)
    if not _process.acquire(blocking=False):
        _reject('DBT_NATIVE_PROCESS_NOT_ISOLATED')
    try:
        if admission.current() is not None:
            _reject('DBT_NATIVE_PROCESS_NOT_ISOLATED')
        for package, version in (('dbt-core', '1.12.5'), ('dbt-trino', '1.10.3'), ('sqlglot', '28.0.0')):
            if importlib.metadata.version(package) != version:
                _reject('DBT_NATIVE_VERSION')
        with admission.lifecycle_gate(exclusive=True):
            observed = _replay(principal, context, plan_id, node_id, key)
            if observed is not None:
                return observed
            _idle()
            execution = ds.begin(principal, context, route=_ROUTE, idempotency_key=key, payload=payload)
            if execution.replayed:
                return execution.inspect()
            return materialize_accepted(execution, principal, context, plan, node, bindings, approved, payload)
    finally:
        _process.release()
