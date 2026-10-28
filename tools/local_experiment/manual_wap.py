"""Explicit backend-only manual WAP on the P3-1 Execution boundary.

Configure runtime_profile before calling these APIs. Imports create no paths,
read no legacy JSON, and do not import sqlglot. Save returns a model dictionary;
build/audit/publish/rollback return Execution.inspect() dictionaries with
id, state, committed, result, queries and cleanup_error. Artifact
IDs live in result.build_id/audit_id/publication_id. Replays are observation-only.
get_model/get_build/get_audit/get_publication expose authorized DB metadata,
never another actor's child-query results. No API accepts executable SQL except
save_model's constrained read model; all mutations are generated internally.
"""
from contextlib import contextmanager
from dataclasses import asdict
import fcntl
import hashlib
import json
import re
import uuid

import control_store
import data_management as registry
import dataops_service as ds
import warehouse_admission as admission
from query_context import QueryError, revalidate_admission


class _Metadata(ds.Execution):
    """Metadata authorization only: no admitted SQL execution handle."""
    def __init__(self, principal, context):
        self.principal, self.context = principal, context

    def _context(self):
        ds._validated_context(self.principal, self.context)
        return self.context

    def wap_measure(self, ref):
        # REST-only measurement has no SQL handle or success closure to fake.
        return ds.Execution.wap_measure.__wrapped__(self, ref)

    def _absent_model(self, target):
        # Catalog-only absence check permits an engineer to define MARTS output.
        class ReadMetadata(_Metadata):
            def _locator(self, locator, **kwargs):
                return super()._locator(locator, write=False)

            def _schema(self, schema_id, **kwargs):
                return super()._schema(schema_id)
        ReadMetadata(self.principal, self.context)._absent(target)


def _access(principal, context, *, write=False, admin=False):
    metadata = _Metadata(principal, context)
    metadata._context()
    if admin and context.selected_role != 'admin':
        raise QueryError('SQL_PERMISSION_DENIED', 403)
    if write and context.selected_role not in ('data_engineer', 'admin'):
        raise QueryError('SQL_PERMISSION_DENIED', 403)
    return metadata


@contextmanager
def _model_lock(database_id, model_id):
    # A scoped cross-process lock; no background worker or TTL-based takeover.
    key = hashlib.sha256(ds._json([database_id, model_id]).encode()).hexdigest()
    with (control_store.root() / ('wap-' + key + '.lock')).open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _head(context, model_id):
    with control_store.connect() as db:
        row = db.execute('SELECT * FROM wap_models WHERE database_id=? AND model_id=?',
                         (context.database_id, model_id)).fetchone()
    if row is None:
        raise QueryError('MODEL_NOT_FOUND', 404)
    return dict(row)


def _claim_model(context, model_id, operation_id=None):
    head = _head(context, model_id)
    with control_store.connect() as db:
        prior = db.execute('SELECT state,committed,route,result FROM dataops_operations WHERE id=?',
                           (head['operation_id'],)).fetchone()
        if prior and prior['committed'] and prior['route'] in ('manual-wap-publish', 'manual-wap-rollback'):
            receipt = json.loads(prior['result'] or '{}')
            artifact = db.execute('''SELECT id FROM wap_publications
                WHERE database_id=? AND model_id=? AND operation_id=? AND id=?''',
                (context.database_id, model_id, head['operation_id'], receipt.get('publication_id'))).fetchone()
            if artifact is None:
                raise QueryError('MODEL_RECONCILIATION_REQUIRED', 409)
        if prior and head['operation_id'] != operation_id and prior['state'] not in ('FINISHED', 'FAILED', 'CANCELED'):
            raise QueryError('MODEL_BUSY', 409)
        if operation_id:
            db.execute('UPDATE wap_models SET operation_id=? WHERE database_id=? AND model_id=?',
                       (operation_id, context.database_id, model_id))
    return head


def _compile_model(sql, sources, *, execution=None, measurements=None):
    """Validate Trino AST and scope-resolve physical bindings; optionally pin them.

    Unknown scalar functions, comments, recursive CTEs and table modifiers are
    deliberately unsupported. sqlglot is loaded only in the explicit runtime.
    """
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.scope import traverse_scope
    if sqlglot.__version__ != '28.0.0':
        raise RuntimeError('manual WAP requires sqlglot==28.0.0')
    if not isinstance(sql, str) or not sql.strip() or not isinstance(sources, dict):
        raise ValueError('read SQL and explicit source bindings required')
    if any(not isinstance(n, str) or not re.fullmatch('[a-z_][a-z0-9_]*', n) for n in sources):
        raise ValueError('source aliases must be lowercase SQL identifiers')
    try:
        trees = sqlglot.parse(sql, read='trino', error_level=sqlglot.ErrorLevel.RAISE)
        if len(trees) != 1 or not isinstance(trees[0], (exp.Select, exp.SetOperation)):
            raise ValueError('one read Query required')
        tree = trees[0]
        forbidden = (exp.DDL, exp.DML, exp.Command, exp.Into, exp.Lock, exp.Anonymous,
                     exp.Dot, exp.Unnest, exp.Lateral, exp.Values)
        if any(isinstance(n, forbidden) or n.comments for n in tree.walk()):
            raise ValueError('unsupported model SQL')
        if any(w.args.get('recursive') for w in tree.find_all(exp.With)):
            raise ValueError('recursive CTE unsupported')
        for table in tree.find_all(exp.Table):
            if not isinstance(table.this, exp.Identifier) or table.db or table.catalog:
                raise ValueError('physical sources must be unqualified aliases')
            if any(v for k, v in table.args.items() if k not in ('this', 'alias')):
                raise ValueError('table modifiers unsupported')
        physical = []
        for scope in traverse_scope(tree):
            if not isinstance(scope.expression, (exp.Select, exp.SetOperation)):
                raise ValueError('unsupported source scope')
            for _, (node, source) in scope.selected_sources.items():
                if isinstance(source, exp.Table):
                    physical.append(source)
                elif not isinstance(source.expression, (exp.Select, exp.SetOperation)):
                    raise ValueError('unsupported source scope')
        if {t.name for t in physical} != set(sources):
            raise ValueError('extra or missing source bindings')
        if execution is not None:
            for table in physical:
                alias = table.alias or table.name
                name = execution._ref(sources[table.name])
                snapshot = measurements[table.name]['snapshot']
                if not isinstance(snapshot, str) or not re.fullmatch('[0-9]+', snapshot):
                    raise ValueError('decimal snapshot required')
                replacement = sqlglot.parse_one('SELECT * FROM ' + name +
                    ' FOR VERSION AS OF ' + snapshot, read='trino').args['from_'].this
                replacement.set('alias', table.args.get('alias') or
                    exp.TableAlias(this=exp.Identifier(this=alias, quoted=True)))
                table.replace(replacement)
        return tree.sql(dialect='trino', unsupported_level=sqlglot.ErrorLevel.RAISE)
    except (sqlglot.errors.SqlglotError, AttributeError, KeyError) as error:
        raise ValueError('unsupported model SQL: ' + str(error)) from error


def save_model(principal, context, *, model_id, sql, sources, target,
               expected_version, min_rows=1, not_null=()):
    """Authorize DB metadata and CAS an immutable model version; issue no SQL.

    expected_version=None creates only. Updates require the exact current int.
    sources maps SQL aliases to same-DB ResourceRefs; target is ResourceRef for
    existing tables or DestinationLocator for an absent first publication.
    """
    metadata = _access(principal, context, write=True)
    if not isinstance(sources, dict):
        raise ValueError('explicit source bindings required')
    sources = dict(sources)
    if not isinstance(model_id, str) or not model_id or len(model_id) > 256:
        raise ValueError('model_id required')
    if expected_version is not None and (type(expected_version) is not int or expected_version < 1):
        raise ValueError('invalid expected_version')
    not_null = tuple(not_null)
    if type(min_rows) is not int or min_rows < 1 or len(set(not_null)) != len(not_null):
        raise ValueError('positive min_rows and distinct not_null columns required')
    for column in not_null:
        ds._column(column)
    _compile_model(sql, sources)
    for ref in sources.values():
        metadata._ref(ref)
    if type(target) is registry.ResourceRef:
        metadata._ref(target)
    elif type(target) is registry.DestinationLocator:
        # An engineer may define an admin-published MARTS model, without writing.
        metadata._locator(target, write=False)
        metadata._absent_model(target)
    else:
        raise ValueError('explicit ResourceRef or DestinationLocator target required')
    with _model_lock(context.database_id, model_id):
        try:
            head = _claim_model(context, model_id)
        except QueryError as error:
            if error.code != 'MODEL_NOT_FOUND':
                raise
            head = None
        if (head['version'] if head else None) != expected_version:
            raise QueryError('MODEL_VERSION_CONFLICT', 409)
        # Once materialized, a locator cannot adopt an externally recreated table.
        materialized = json.loads(head['materialized_ref']) if head and head['materialized_ref'] else None
        if materialized is not None and asdict(target) != materialized:
            raise QueryError('MATERIALIZED_TARGET_REQUIRED', 409)
        version = 1 if head is None else head['version'] + 1
        record = dict(database_id=context.database_id, model_id=model_id, version=version,
            sql=sql, sources={n: asdict(r) for n, r in sources.items()}, target=asdict(target),
            target_kind='ref' if type(target) is registry.ResourceRef else 'locator',
            min_rows=min_rows, not_null=list(not_null), actor=principal.principal_id,
            context=asdict(context))
        metadata._context()
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            revalidate_admission(db, principal, context)
            if head is None:
                db.execute('INSERT INTO wap_models(database_id,model_id,version) VALUES (?,?,?)',
                           (context.database_id, model_id, version))
            else:
                cursor = db.execute('UPDATE wap_models SET version=? WHERE database_id=? AND model_id=? AND version=?',
                    (version, context.database_id, model_id, expected_version))
                if cursor.rowcount != 1:
                    raise QueryError('MODEL_VERSION_CONFLICT', 409)
            db.execute('INSERT INTO wap_model_versions VALUES (?,?,?,?,?)',
                (context.database_id, model_id, version, principal.principal_id, ds._json(record)))
        return record


def get_model(principal, context, *, model_id, version=None):
    """Read an immutable version with the model identity's current binding."""
    metadata = _access(principal, context, write=True)
    with control_store.connect() as db:
        db.execute('BEGIN')
        head = db.execute('SELECT * FROM wap_models WHERE database_id=? AND model_id=?',
                          (context.database_id, model_id)).fetchone()
        if head is None:
            raise QueryError('MODEL_NOT_FOUND', 404)
        row = db.execute('SELECT record FROM wap_model_versions WHERE database_id=? AND model_id=? AND version=?',
            (context.database_id, model_id, head['version'] if version is None else version)).fetchone()
        if row is None:
            raise QueryError('MODEL_NOT_FOUND', 404)
        record = json.loads(row['record'])
        record['current_target_ref'] = json.loads(head['materialized_ref']) if head['materialized_ref'] else None
        revalidate_admission(db, principal, context)
        return record


def _get(principal, context, table, artifact_id):
    _access(principal, context, write=True)
    with control_store.connect() as db:
        row = db.execute('SELECT record FROM ' + table + ' WHERE id=? AND database_id=?',
                         (artifact_id, context.database_id)).fetchone()
    if row is None:
        raise QueryError('WAP_ARTIFACT_NOT_FOUND', 404)
    return json.loads(row['record'])


def get_build(principal, context, *, build_id):
    """Get DB-authorized build metadata without builder query results."""
    return _get(principal, context, 'wap_builds', build_id)


def get_audit(principal, context, *, audit_id):
    """Get DB-authorized immutable audit measurements."""
    return _get(principal, context, 'wap_audits', audit_id)


def get_publication(principal, context, *, publication_id):
    """Get DB-authorized publication/rollback correspondence."""
    return _get(principal, context, 'wap_publications', publication_id)


def _current(execution, model):
    execution._context()
    if _head(execution._context(), model['model_id'])['version'] != model['version']:
        raise QueryError('MODEL_VERSION_CHANGED', 409)


def _same_measurement(actual, expected):
    if actual != expected:
        raise QueryError('TARGET_CHANGED', 409)


def _store_artifact(table, record):
    # Six columns; keep one transaction for the artifact and head correspondence.
    with control_store.connect() as db:
        db.execute('INSERT INTO ' + table + ' VALUES (?,?,?,?,?,?)',
            (record['id'], record['database_id'], record['model_id'], record['version'],
             record['operation_id'], ds._json(record)))
        if table == 'wap_publications':
            db.execute('UPDATE wap_models SET materialized_ref=?,current_publication_id=? WHERE database_id=? AND model_id=?',
                (ds._json(record['target']['ref']), record['id'], record['database_id'], record['model_id']))


def _receipt_intent(execution, result):
    # Proposed artifact IDs are useful on UNKNOWN too. committed remains false
    # until the child's existing atomic commit_result marker proves the write.
    with control_store.connect() as db:
        db.execute('UPDATE dataops_operations SET result=? WHERE id=?', (ds._json(result), execution.id))


def _settle_definite(execution, context, error):
    """Settle our own definite children even when grant revocation denies reads.

    This never uses replacement credentials or declares an unresolved POST a
    failure. Parent admission checks child admissions again in its transaction.
    """
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT state,committed FROM dataops_operations WHERE id=?', (execution.id,)).fetchone()
        if row['state'] != 'ACTIVE':
            return
        children = [r[0] for r in db.execute('''SELECT q.state FROM dataops_queries d
            JOIN query_records q ON q.id=d.query_id WHERE d.operation_id=?''', (execution.id,))]
        if any(state not in ('FINISHED', 'FAILED', 'CANCELED') for state in children):
            return
        state = 'FINISHED' if row['committed'] else 'FAILED'
        parent = admission.get_for_subject(db, kind='dataops', subject_id=execution.id,
                                           warehouse_id=context.warehouse_id)
        admission.finish(db, parent, state=state)
        db.execute('UPDATE dataops_operations SET state=?,cleanup_error=? WHERE id=?',
            (state, getattr(error, 'code', type(error).__name__), execution.id))
    if execution.id in ds._live:
        ds._live[execution.id][2].close()


def _run(principal, context, key, action, payload, work):
    observation = _run_accepted(principal, context, key, action, payload, work)
    ds._validated_context(principal, context)
    return observation


def _run_accepted(principal, context, key, action, payload, work):
    execution = ds.begin(principal, context, route='manual-wap-' + action,
                         idempotency_key=key, payload=payload)
    if execution.replayed:
        return execution.inspect()
    with ds._accepted(execution):
        try:
            _access(principal, context, write=True, admin=action in ('publish', 'rollback'))
            return work(execution)
        except Exception as error:
            # Child commit markers are already durable. Do not retry any mutation.
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
                    _settle_definite(execution, context, error)
            if row['committed'] or row['state'] == 'UNKNOWN':
                return execution._inspect()
            raise


def build(principal, context, *, key, model_id, version, _start=None):
    """Build one exact current model version into a retained, owned SANDBOX CTAS."""
    def work(execution):
        if type(version) is not int or version < 1:
            raise ValueError('positive integer version required')
        with _model_lock(context.database_id, model_id):
            head = _claim_model(context, model_id, execution.id)
            model = get_model(principal, context, model_id=model_id, version=version)
            _current(execution, model)
            sources = {n: registry.ResourceRef(**r) for n, r in model['sources'].items()}
            target = _target(model, head)
            if type(target) is registry.ResourceRef:
                expected = execution.wap_measure(target)
            else:
                execution._locator(target, write=False)
                _Metadata(principal, context)._absent_model(target)
                expected = None
            with control_store.connect() as db:
                schema = db.execute("SELECT id FROM schemas WHERE database_id=? AND sql_name='sandbox'",
                                    (context.database_id,)).fetchone()
            if schema is None:
                raise ValueError('registered SANDBOX required')
            build_id = str(uuid.uuid4())
            result = dict(build_id=build_id, model_id=model_id, version=version)
            _receipt_intent(execution, result)
            _current(execution, model)
            stage, inputs, generated = execution.wap_build(schema['id'], model['sql'], sources,
                                                          step='build', result=result, _start=_start)
            record = dict(id=build_id, database_id=context.database_id, model_id=model_id, version=version,
                operation_id=execution.id, actor=principal.principal_id, context=asdict(context),
                stage=stage, input_refs=inputs, generated_sql=generated, target=expected,
                destination=asdict(target), target_kind='ref' if expected else 'locator')
            _store_artifact('wap_builds', record)
            return execution.complete(dict(result, build=record))
    return _run(principal, context, key, 'build', dict(model_id=model_id, version=version), work)


def _target(model, head):
    if head['materialized_ref']:
        return registry.ResourceRef(**json.loads(head['materialized_ref']))
    constructor = registry.ResourceRef if model['target_kind'] == 'ref' else registry.DestinationLocator
    return constructor(**model['target'])


def audit(principal, context, *, key, build_id):
    """Measure exact immutable build stage; failed quality is a durable failed audit."""
    def work(execution):
        built = get_build(principal, context, build_id=build_id)
        with _model_lock(context.database_id, built['model_id']):
            _claim_model(context, built['model_id'], execution.id)
            model = get_model(principal, context, model_id=built['model_id'], version=built['version'])
            _current(execution, model)
            audit_id = str(uuid.uuid4())
            _receipt_intent(execution, dict(audit_id=audit_id, build_id=build_id,
                model_id=built['model_id'], version=built['version'], stage=built['stage']))
            count, nulls = execution.wap_audit(built, model['not_null'], step='audit')
            execution._wap_owned(built)
            _current(execution, model)
            record = dict(id=audit_id, database_id=context.database_id,
                model_id=built['model_id'], version=built['version'], operation_id=execution.id,
                actor=principal.principal_id, context=asdict(context), build_id=build_id,
                stage=built['stage'], count=count, null_counts=nulls,
                passed=count >= model['min_rows'] and not any(nulls.values()))
            _store_artifact('wap_audits', record)
            return execution.complete(dict(audit_id=record['id'], audit=record))
    return _run(principal, context, key, 'audit', dict(build_id=build_id), work)


def publish(principal, context, *, key, build_id, audit_id):
    """Explicit admin publication of a passed audit; never re-run the model."""
    def work(execution):
        built = get_build(principal, context, build_id=build_id)
        audited = get_audit(principal, context, audit_id=audit_id)
        with _model_lock(context.database_id, built['model_id']):
            head = _claim_model(context, built['model_id'], execution.id)
            model = get_model(principal, context, model_id=built['model_id'], version=built['version'])
            _current(execution, model)
            target = registry.ResourceRef(**built['destination']) if built['target_kind'] == 'ref' else registry.DestinationLocator(**built['destination'])
            locator = execution.wap_claim(target)
            previous_publication_id = head['current_publication_id']
            if previous_publication_id:
                previous_publication = get_publication(principal, context,
                    publication_id=previous_publication_id)
                if previous_publication['target'] != built['target']:
                    # External writes can produce a measured predecessor with
                    # no WAP audit provenance. Preserve its snapshot honestly.
                    previous_publication_id = None
            result = dict(publication_id=str(uuid.uuid4()), build_id=build_id, audit_id=audit_id,
                          model_id=built['model_id'], version=built['version'], stage=audited['stage'],
                          destination=built['destination'], previous=built['target'],
                          previous_publication_id=previous_publication_id)
            _receipt_intent(execution, result)
            _current(execution, model)
            execution.wap_publish(target, built, audited, step='publish', result=result)
            ref = execution._observe(locator)
            measured = execution.wap_measure(ref)
            record = dict(result, id=result['publication_id'], database_id=context.database_id,
                model_id=built['model_id'], version=built['version'], operation_id=execution.id,
                actor=principal.principal_id, context=asdict(context), kind='publish',
                target=measured, stage=audited['stage'])
            # Positive, measured receipt survives a later artifact write failure.
            # Only the private Mart observer may reconcile it, with child closure.
            _receipt_intent(execution, dict(result, publication=record))
            _store_artifact('wap_publications', record)
            return execution.complete(dict(result, publication=record))
    return _run(principal, context, key, 'publish', dict(build_id=build_id, audit_id=audit_id), work)


def rollback(principal, context, *, key, publication_id):
    """Admin rollback only to this publication's recorded prior target snapshot."""
    def work(execution):
        publication = get_publication(principal, context, publication_id=publication_id)
        with _model_lock(context.database_id, publication['model_id']):
            head = _claim_model(context, publication['model_id'], execution.id)
            model = get_model(principal, context, model_id=publication['model_id'], version=publication['version'])
            _current(execution, model)
            if head['current_publication_id'] != publication_id:
                raise QueryError('PUBLICATION_CHANGED', 409)
            target = registry.ResourceRef(**publication['target']['ref'])
            execution.wap_claim(target)
            result = dict(publication_id=str(uuid.uuid4()), rolled_back_publication_id=publication_id,
                          restored_publication_id=publication['previous_publication_id'],
                          target_ref=publication['target']['ref'], expected_head=publication['target'],
                          restored=publication['previous'])
            _receipt_intent(execution, result)
            _current(execution, model)
            execution.wap_rollback(publication, step='rollback', result=result)
            measured = execution.wap_measure(target)
            if measured['snapshot'] != publication['previous']['snapshot']:
                raise QueryError('RESTORED_SNAPSHOT_MISMATCH', 409)
            restored = (get_publication(principal, context, publication_id=publication['previous_publication_id'])
                        if publication['previous_publication_id'] else None)
            record = dict(result, id=result['publication_id'], database_id=context.database_id,
                model_id=publication['model_id'], version=publication['version'], operation_id=execution.id,
                actor=principal.principal_id, context=asdict(context), kind='rollback', target=measured,
                previous=publication['target'], previous_publication_id=publication_id,
                audit_id=restored.get('audit_id') if restored else None,
                stage=restored.get('stage') if restored else None)
            _store_artifact('wap_publications', record)
            return execution.complete(dict(result, publication=record))
    return _run(principal, context, key, 'rollback', dict(publication_id=publication_id), work)
