"""Small server-side DataOps boundary; deliberately has no HTTP routes.

Call begin once, execute explicit generated steps, then complete. A repeated
request returns an observation-only handle, even in the original process.
Credentials are private process memory; a lost process can only reconcile.
"""
from dataclasses import asdict, dataclass
from contextlib import nullcontext, contextmanager
from contextvars import ContextVar
from datetime import date, datetime
from decimal import Decimal
from functools import wraps
import hashlib
import json
import math
import re
import secrets
import threading
from typing import Optional
import uuid

import control_store
import data_management as registry
import database_inventory as inventory
import query_service as queries
from query_context import QueryError, ResolvedQueryContext, resolve_context
import request_auth
import warehouse_admission as admission


@dataclass(frozen=True)
class _Statement:
    action: str
    sql: str
    commit_result: Optional[str] = None


_live = {}
_lock = threading.RLock()
_job = ContextVar('dataops_job', default=None)
_stage_scan = ContextVar('dataops_stage_scan', default=None)
_mart = ContextVar('dataops_mart', default=None)


@contextmanager
def _mart_scope(principal, context, parent, frozen, credential_ref, run_id, phase):
    """Private continuation from an admitted Mart, never request supplied."""
    token = _mart.set((principal, context, parent, frozen, credential_ref, run_id, phase))
    try:
        yield
    finally:
        _mart.reset(token)


@contextmanager
def _stage_scan_scope(principal, context, parent, frozen, credential_ref, scan_id, item_id, attempt):
    token = _stage_scan.set((principal, context, parent, frozen, credential_ref, scan_id, item_id, attempt))
    try:
        yield
    finally:
        _stage_scan.reset(token)


def _check_stage_scan(db, scope):
    row = db.execute('SELECT record,cancel_requested FROM db_stage_scans WHERE id=?', (scope[5],)).fetchone()
    if row is None or row['cancel_requested'] or json.loads(row['record'])['status'] not in ('queued', 'running'):
        raise QueryError('STAGE_SCAN_STOPPED', 409)


@contextmanager
def _job_scope(principal, context, parent, frozen, credential_ref, run_id, position):
    """Private server continuation; never populated from request fields."""
    token = _job.set((principal, context, parent, frozen, credential_ref, run_id, position))
    try:
        yield
    finally:
        _job.reset(token)


def _check_job(db, run_id):
    row = db.execute('SELECT status,cancel_requested FROM db_job_runs WHERE id=?', (run_id,)).fetchone()
    if row is None or row['status'] not in ('queued', 'running') or row['cancel_requested']:
        raise QueryError('JOB_STOPPED', 409)


@contextmanager
def _accepted(execution):
    """Private worker lifetime; never entered by context or observation reads."""
    row = execution._row()  # Check the exact actor before using any private live pin.
    live = _live.get(execution.id)
    if execution.replayed or live is None:
        raise QueryError('OPERATION_OBSERVATION_ONLY', 409)
    if row['state'] != 'ACTIVE':
        raise QueryError('ADMISSION_CONFLICT', 409)
    with live[3], admission.accepted(live[1]):
        execution._active()
        yield


def _cancel_account(principal):
    if (type(principal) is not request_auth.AuthenticatedPrincipal or
            principal.principal_id not in request_auth._principal_accounts()):
        raise QueryError('DB_ACCESS_REVOKED', 403)


def _serialized(function):
    @wraps(function)
    def execute(self, *args, **kwargs):
        with _accepted(self):
            return function(self, *args, **kwargs)
    return execute


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False)


def _identifier(value):
    return registry.quote_identifier(registry.validate_sql_name(value))


def _column(value):
    if not isinstance(value, str) or not value or '\x00' in value:
        raise ValueError('invalid column name')
    return '"' + value.replace('"', '""') + '"'


def _type(value):
    if not isinstance(value, str):
        raise ValueError('column type is required')
    value = re.sub(r'\s+', '', value.upper())
    if value in ('VARCHAR', 'BOOLEAN', 'BIGINT', 'INTEGER', 'SMALLINT', 'TINYINT', 'DOUBLE', 'REAL', 'DATE'):
        return value
    match = re.fullmatch(r'DECIMAL\(([1-9][0-9]?),([0-9]+)\)', value)
    if match and 0 <= int(match[2]) <= int(match[1]) <= 38:
        return value
    if re.fullmatch(r'TIMESTAMP\([0-6]\)', value):
        return value
    raise ValueError('unsupported DataOps type')


def _literal(value):
    if value is None:
        return 'NULL'
    if type(value) is bool:
        return 'TRUE' if value else 'FALSE'
    if type(value) is int:
        return str(value)
    if isinstance(value, Decimal):
        if not value.is_finite() or len(value.as_tuple().digits) > 38:
            raise ValueError('invalid decimal literal')
        return "DECIMAL '" + format(value, 'f') + "'"
    if type(value) is float and math.isfinite(value):
        return repr(value)
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            raise ValueError('offset timestamps are unsupported')
        return "TIMESTAMP '" + value.isoformat(sep=' ', timespec='microseconds') + "'"
    if isinstance(value, date):
        return "DATE '" + value.isoformat() + "'"
    if type(value) is str and '\x00' not in value:
        return "'" + value.replace("'", "''") + "'"
    raise ValueError('unsupported literal')


def _validated_context(principal, context):
    if type(principal) is not request_auth.AuthenticatedPrincipal or type(context) is not ResolvedQueryContext:
        raise QueryError('UNKNOWN_CONTEXT', 400)
    current = resolve_context(principal, context.database_id, context.schema_id, context.selected_role,
        warehouse_id=context.warehouse_id, expected_binding_revision=context.binding_revision)
    if current != context:
        raise QueryError('CONTEXT_MISMATCH', 409)


def begin(principal, context, *, route, idempotency_key, payload, _admission_hook=None):
    """Persist normalized request and admission before creating any SQL intent."""
    if any(not isinstance(v, str) or not v or len(v) > 256 for v in (route, idempotency_key)):
        raise ValueError('route and idempotency key are required')
    if type(context) is not ResolvedQueryContext or type(principal) is not request_auth.AuthenticatedPrincipal:
        raise QueryError('UNKNOWN_CONTEXT', 400)
    normalized = _json({'payload': payload, 'context': asdict(context)})
    fingerprint = hashlib.sha256(normalized.encode()).hexdigest()
    with _lock:
        # Replay lookup deliberately precedes new-operation admission/capture.
        with control_store.connect() as db:
            row = db.execute('SELECT * FROM dataops_operations WHERE actor=? AND route=? AND idempotency_key=?',
                (principal.principal_id, route, idempotency_key)).fetchone()
        if row is not None:
            if row['payload'] != normalized:
                raise QueryError('IDEMPOTENCY_CONFLICT', 409)
            execution = Execution(principal, row['id'], replayed=True)
            execution.inspect()
            return execution
        _validated_context(principal, context)
        scan_scope = _stage_scan.get()
        mart_scope = _mart.get()
        inherited = mart_scope or scan_scope or _job.get()
        if inherited is not None and (inherited[0] != principal or inherited[1] != context):
            raise QueryError('CONTEXT_MISMATCH', 409)
        frozen = inherited[3] if inherited else queries._capture_dataops(principal, context)
        operation_id = str(uuid.uuid4())
        owner = queries._acquire_query_owner('dataops-' + operation_id)
        try:
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                queries.revalidate_admission(db, principal, context)
                if inherited:
                    if mart_scope:
                        import db_marts
                        db_marts._check_child(db, principal, context, mart_scope[5])
                    elif scan_scope:
                        _check_stage_scan(db, scan_scope)
                    else:
                        _check_job(db, inherited[5])
                # Another process may have accepted this key during capture.
                row = db.execute('SELECT * FROM dataops_operations WHERE actor=? AND route=? AND idempotency_key=?',
                    (principal.principal_id, route, idempotency_key)).fetchone()
                if row is not None:
                    owner.close()
                    if row['payload'] != normalized:
                        raise QueryError('IDEMPOTENCY_CONFLICT', 409)
                    return Execution(principal, row['id'], replayed=True)
                handle = admission.admit(db, warehouse_id=context.warehouse_id, actor=principal.principal_id,
                    kind='dataops', subject_id=operation_id, generation_id=context.generation_id, database_id=context.database_id,
                    parent=inherited[2] if inherited else None)
                db.execute('''INSERT INTO dataops_operations
                    (id,actor,route,idempotency_key,payload,fingerprint,context,backend_username,
                     credential_snapshot_ref,state,created_at) VALUES (?,?,?,?,?,?,?,?,?,'ACTIVE',?)''',
                    (operation_id, principal.principal_id, route, idempotency_key, normalized, fingerprint,
                     _json(asdict(context)), frozen.headers['X-Trino-User'],
                     inherited[4] if inherited else secrets.token_urlsafe(32), admission._now()))
                if mart_scope:
                    db.execute('INSERT INTO db_mart_operations VALUES (?,?,?)',
                               (inherited[5], inherited[6], operation_id))
                elif scan_scope:
                    db.execute('INSERT INTO db_stage_scan_operations VALUES (?,?,?,?)',
                               (inherited[5], inherited[6], inherited[7], operation_id))
                elif inherited:
                    db.execute('INSERT INTO db_job_operations VALUES (?,?,?)',
                               (inherited[5], inherited[6], operation_id))
                if _admission_hook is not None:
                    _admission_hook(db, operation_id)
            _live[operation_id] = (frozen, handle, owner, threading.RLock())
        except BaseException:
            owner.close()
            raise
    return Execution(principal, operation_id)


class Execution:
    def __init__(self, principal, operation_id, replayed=False):
        if type(principal) is not request_auth.AuthenticatedPrincipal:
            raise QueryError('OPERATION_NOT_FOUND', 404)
        self.principal, self.id, self.replayed = principal, operation_id, replayed

    def _row(self):
        with control_store.connect() as db:
            row = db.execute('SELECT * FROM dataops_operations WHERE id=?', (self.id,)).fetchone()
        if row is None or row['actor'] != self.principal.principal_id:
            raise QueryError('OPERATION_NOT_FOUND', 404)
        return dict(row)

    def _context(self):
        context = ResolvedQueryContext(**json.loads(self._row()['context']))
        try:
            _validated_context(self.principal, context)
        except (QueryError, PermissionError):
            if queries._retirement_only_denial(self.principal, context):
                raise QueryError('DB_ACCESS_REVOKED', 403) from None
            # Cancellation uses existing query pins, never replacement credentials.
            with control_store.connect() as db:
                ids = [r[0] for r in db.execute('SELECT query_id FROM dataops_queries WHERE operation_id=?', (self.id,))]
            for query_id in ids:
                queries._best_cancel(query_id)
            raise QueryError('DB_ACCESS_REVOKED', 403) from None
        return context

    def _active(self):
        self._context()
        if self.replayed or self.id not in _live:
            raise QueryError('OPERATION_OBSERVATION_ONLY', 409)
        if self._row()['state'] != 'ACTIVE':
            raise QueryError('ADMISSION_CONFLICT', 409)
        return _live[self.id]

    def _schema(self, schema_id, *, write=False, admin=False):
        context = self._context()
        with control_store.connect() as db:
            schema = registry.get_schema(db, schema_id)
        if schema is None or schema.database_id != context.database_id:
            raise QueryError('CROSS_DATABASE_TARGET', 409)
        if admin and context.selected_role != 'admin':
            raise QueryError('SQL_PERMISSION_DENIED', 403)
        permissions = inventory._permissions(context.selected_role, schema)
        if not permissions['write' if write else 'query']:
            raise QueryError('SQL_PERMISSION_DENIED', 403)
        return schema

    def _locator(self, locator, *, write=True, admin=False):
        if type(locator) is not registry.DestinationLocator:
            raise ValueError('DestinationLocator required')
        context = self._context()
        if locator.database_id != context.database_id:
            raise QueryError('CROSS_DATABASE_TARGET', 409)
        schema = self._schema(locator.schema_id, write=write, admin=admin)
        return '.'.join((_identifier(context.sql_catalog), _identifier(schema.sql_name),
                         _identifier(locator.physical_name)))

    def _catalog(self):
        context = self._context()
        with control_store.connect() as db:
            database = registry.get_database(db, context.database_id)
            binding = registry.get_binding(db, context.database_id)
        return database, binding

    def _absent(self, locator):
        self._locator(locator)
        schema = self._schema(locator.schema_id, write=True)
        database, binding = self._catalog()
        exists = False
        with inventory.catalog_api(database, binding) as api:
            # HEAD-like metadata requests detect Views even without view UUIDs.
            for kind in ('TABLE', 'VIEW'):
                try:
                    api(inventory._path(schema, kind, locator.physical_name))
                except inventory.InventoryError as error:
                    if error.status == 404:
                        continue
                    raise
                exists = True
                break
        # catalog_api normalizes errors raised by its body as catalog failures.
        if exists:
            raise QueryError('DESTINATION_EXISTS', 409)

    def _ref(self, ref, *, write=False, admin=False):
        if type(ref) is not registry.ResourceRef:
            raise ValueError('ResourceRef required')
        context = self._context()
        if ref.database_id != context.database_id:
            raise QueryError('CROSS_DATABASE_TARGET', 409)
        schema = self._schema(ref.schema_id, write=write, admin=admin)
        with control_store.connect() as db:
            obj = registry.get_object(db, ref.object_id)
        if obj is None or obj.schema_id != schema.id or obj.incarnation != ref.incarnation or obj.kind != 'TABLE':
            raise QueryError('STALE_RESOURCE', 409)
        database, binding = self._catalog()
        with inventory.catalog_api(database, binding) as api:
            metadata = inventory._metadata(api, schema, 'TABLE', obj.physical_name)
        if metadata.get('table-uuid') != obj.backend_identity:
            raise QueryError('STALE_RESOURCE', 409)
        try:
            uuid.UUID(obj.backend_identity)
        except (ValueError, TypeError):
            raise QueryError('STALE_RESOURCE', 409) from None
        return self._locator(registry.DestinationLocator(ref.database_id, ref.schema_id, obj.physical_name),
                             write=write, admin=admin)

    def _execute(self, action, sql, step, *, commit_result=None, projection=None):
        if not isinstance(step, str) or not step or len(step) > 128:
            raise ValueError('unique step name is required')
        frozen, parent, _, lock = self._active()
        with lock, admission.accepted(parent):
            page = queries._submit_dataops(self.principal, _Statement(action, sql,
                _json(commit_result) if commit_result is not None else None), frozen, parent, self.id, step)
            seen_projection = False
            def validate(page):
                nonlocal seen_projection
                if projection is None:
                    return
                columns = page.get('columns', [])
                seen_projection = seen_projection or bool(columns)
                if columns and [(c.get('name'), c.get('type')) for c in columns] != projection:
                    raise QueryError('EXPORT_PAGE_SCHEMA_MISMATCH', 502)
                if (page['data'] and not columns or
                        any(not isinstance(r, (list, tuple)) or len(r) != len(projection) for r in page['data'])):
                    raise QueryError('EXPORT_ROW_SHAPE_MISMATCH', 502)
            validate(page)
            rows = list(page['data'])
            while page.get('next_uri'):
                self._context()
                page = queries.fetch(self.principal, page['id'], page['next_uri'].rsplit('/', 1)[1],
                                     selected_role=frozen.context.selected_role)
                validate(page)
                rows.extend(page['data'])
            page['data'] = rows
            self._context()
            if page['state'] != 'FINISHED':
                raise QueryError('DATAOPS_STEP_FAILED', 502, {'query_id': page['id'], 'state': page['state']})
            if projection is not None and not seen_projection:
                raise QueryError('EXPORT_PAGE_SCHEMA_MISMATCH', 502)
            return page

    @_serialized
    def _job_sql(self, sql, *, mode, step='sql', alert=False):
        """Guarded SQL only for a durably linked database Job operation."""
        import warehouse_resident as wr
        with control_store.connect() as db:
            link = db.execute('SELECT run_id FROM db_job_operations WHERE operation_id=?', (self.id,)).fetchone()
            if link is None:
                raise QueryError('SQL_PERMISSION_DENIED', 403)
            _check_job(db, link['run_id'])
        context = self._context()
        if context.selected_role not in ('admin', 'data_engineer') or alert and mode != 'read-only':
            raise QueryError('SQL_PERMISSION_DENIED', 403)
        wr.check_sql_policy(sql, mode, context.selected_role, context=context, database_only=True)
        page = self._execute('job_alert' if alert else 'job_sql', sql, step)
        if alert:
            if (len(page['data']) != 1 or len(page['data'][0]) != 1 or
                    type(page['data'][0][0]) is not bool):
                raise QueryError('INVALID_ALERT_RESULT', 502)
            return dict(triggered=page['data'][0][0])
        return dict(row_count=len(page['data']))

    def export_measure(self, ref, captured=None):
        """One REST measurement of UUID, schema identity and available snapshots.

        Project the captured snapshot's schema, as Trino time travel does.
        Separately guard the current schema against in-flight DDL. Current
        head may advance, but both schema guards and the snapshot must remain.
        """
        if type(ref) is not registry.ResourceRef:
            raise ValueError('ResourceRef required')
        context = self._context()
        if ref.database_id != context.database_id:
            raise QueryError('CROSS_DATABASE_TARGET', 409)
        schema = self._schema(ref.schema_id)
        with control_store.connect() as db:
            obj = registry.get_object(db, ref.object_id)
        if obj is not None and obj.kind == 'VIEW':
            raise QueryError('VIEW_EXPORT_NOT_SUPPORTED', 400)
        if (obj is None or obj.kind != 'TABLE' or not obj.current or
                obj.schema_id != schema.id or obj.incarnation != ref.incarnation):
            raise QueryError('STALE_RESOURCE', 409)
        database, binding = self._catalog()
        with inventory.catalog_api(database, binding) as api:
            metadata = inventory._metadata(api, schema, 'TABLE', obj.physical_name)
        identity = metadata.get('table-uuid')
        try:
            uuid.UUID(identity)
        except (ValueError, TypeError, AttributeError):
            raise QueryError('STALE_RESOURCE', 409) from None
        if identity != obj.backend_identity:
            raise QueryError('STALE_RESOURCE', 409)
        if captured is None and type(metadata.get('current-snapshot-id')) is not int:
            raise QueryError('EXPORT_SNAPSHOT_UNAVAILABLE', 409)
        snapshot = captured['snapshot'] if captured else str(metadata.get('current-snapshot-id'))
        snapshots = [s for s in metadata.get('snapshots', [])
                     if type(s.get('snapshot-id')) is int and s['snapshot-id'] >= 0
                     and str(s['snapshot-id']) == snapshot]
        if not re.fullmatch('[0-9]+', snapshot) or len(snapshots) != 1:
            raise QueryError('EXPORT_SNAPSHOT_UNAVAILABLE', 409)
        sid = snapshots[0].get('schema-id')
        schemas = [s for s in metadata.get('schemas', []) if s.get('schema-id') == sid]
        current_sid = metadata.get('current-schema-id')
        current_schemas = [s for s in metadata.get('schemas', []) if s.get('schema-id') == current_sid]
        if (type(sid) is not int or sid < 0 or len(schemas) != 1 or not schemas[0].get('fields') or
                type(current_sid) is not int or current_sid < 0 or len(current_schemas) != 1 or
                not current_schemas[0].get('fields')):
            raise QueryError('SCHEMA_REQUIRED', 409)
        columns = [dict(id=f.get('id'), name=f.get('name'), type=f.get('type')) for f in schemas[0]['fields']]
        for c in columns:
            _column(c['name'])
        if (len({c['name'] for c in columns}) != len(columns) or
                any(type(c['id']) is not int for c in columns) or
                len({c['id'] for c in columns}) != len(columns)):
            raise QueryError('SCHEMA_REQUIRED', 409)
        measured = dict(ref=asdict(ref), table_uuid=identity, snapshot=snapshot,
            schema_id=sid, columns=columns,
            schema_fingerprint=hashlib.sha256(_json(schemas[0]).encode()).hexdigest(),
            current_schema_id=current_sid,
            current_schema_fingerprint=hashlib.sha256(_json(current_schemas[0]).encode()).hexdigest())
        if captured is not None:
            # Earlier completed artifacts used one schema for both guards.
            expected = dict(captured)
            expected.setdefault('current_schema_id', captured['schema_id'])
            expected.setdefault('current_schema_fingerprint', captured['schema_fingerprint'])
            if measured != expected:
                raise QueryError('EXPORT_SOURCE_CHANGED', 409)
        return measured

    @_serialized
    def export_query(self, ref, measurement, *, count=False):
        self.export_measure(ref, measurement)
        name = self._ref(ref)
        columns = measurement['columns']
        projection = [('count', 'bigint')] if count else [(c['name'], 'varchar') for c in columns]
        expression = 'COUNT(*) AS "count"' if count else ', '.join(
            'CAST(' + _column(c['name']) + ' AS VARCHAR) AS ' + _column(c['name']) for c in columns)
        return self._execute('export_count' if count else 'export_select',
            'SELECT ' + expression + ' FROM ' + name + ' FOR VERSION AS OF ' + measurement['snapshot'],
            'count' if count else 'select', projection=projection)

    @_serialized
    def create_table(self, locator, columns, *, step):
        self._active()
        name = self._locator(locator)
        columns = tuple(columns)
        if not columns or len({c[0] for c in columns}) != len(columns):
            raise ValueError('distinct columns required')
        definitions = ', '.join(_column(n) + ' ' + _type(t) for n, t in columns)
        self._absent(locator)  # The final pre-CREATE external observation.
        self._execute('create', 'CREATE TABLE ' + name + ' (' + definitions + ')', step)
        return self._observe(locator)

    def _observe(self, locator):
        schema = self._schema(locator.schema_id, write=True)
        database, binding = self._catalog()
        with inventory.catalog_api(database, binding) as api:
            metadata = inventory._metadata(api, schema, 'TABLE', locator.physical_name)
        identity = metadata.get('table-uuid')
        try:
            uuid.UUID(identity)
        except (ValueError, TypeError, AttributeError):
            raise QueryError('TABLE_OBSERVATION_REQUIRED', 409) from None
        with control_store.connect() as db:
            obj = registry.observe_object(db, schema.id, locator.physical_name, 'TABLE', identity, locator.physical_name)
        return registry.ResourceRef(locator.database_id, locator.schema_id, obj.id, obj.incarnation)

    def _ingest_schema(self, schema_id):
        schema = self._schema(schema_id, write=True)
        if schema.sql_name not in ('raw', 'sandbox'):
            raise QueryError('SQL_PERMISSION_DENIED', 403)
        return schema

    @_serialized
    def ingest_claim(self, target):
        """Serialize adapter writers by physical destination, including recreations.

        Claims survive process loss. Only a terminal owner's claim is reclaimable.
        """
        self._ingest_schema(target.schema_id)
        return self._target_claim(target)

    def _target_claim(self, target):
        schema = self._schema(target.schema_id, write=True)
        context = self._context()
        if type(target) is registry.ResourceRef:
            self._ref(target, write=True)
            with control_store.connect() as db:
                name = registry.get_object(db, target.object_id).physical_name
        elif type(target) is registry.DestinationLocator:
            self._locator(target)
            name = target.physical_name
        else:
            raise ValueError('explicit target required')
        key = (context.sql_catalog, schema.sql_name, name)
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('''SELECT o.id,o.state FROM dataops_target_claims c
                JOIN dataops_operations o ON o.id=c.operation_id
                WHERE c.catalog=? AND c.schema_name=? AND c.table_name=?''', key).fetchone()
            if prior and prior['id'] != self.id and prior['state'] not in ('FINISHED', 'FAILED', 'CANCELED'):
                raise QueryError('TARGET_BUSY', 409)
            db.execute('INSERT OR REPLACE INTO dataops_target_claims VALUES (?,?,?,?)', (*key, self.id))
        return registry.DestinationLocator(context.database_id, schema.id, name)

    @_serialized
    def wap_claim(self, target):
        """Claim a WAP publication destination with explicit admin authority."""
        self._schema(target.schema_id, write=True, admin=True)
        return self._target_claim(target)

    @_serialized
    def wap_measure(self, ref):
        """Observe actual Iceberg main head and schema, never max(history)."""
        self._ref(ref)
        schema = self._schema(ref.schema_id)
        with control_store.connect() as db:
            obj = registry.get_object(db, ref.object_id)
        database, binding = self._catalog()
        with inventory.catalog_api(database, binding) as api:
            metadata = inventory._metadata(api, schema, 'TABLE', obj.physical_name)
        if metadata.get('table-uuid') != obj.backend_identity:
            raise QueryError('STALE_RESOURCE', 409)
        snapshot = metadata.get('current-snapshot-id')
        if type(snapshot) is not int or snapshot < 0:
            raise QueryError('SNAPSHOT_REQUIRED', 409)
        schema_id = metadata.get('current-schema-id')
        if type(schema_id) is not int or schema_id < 0:
            raise QueryError('SCHEMA_REQUIRED', 409)
        schemas = [s for s in metadata.get('schemas', []) if s.get('schema-id') == schema_id]
        if len(schemas) != 1 or not schemas[0].get('fields'):
            raise QueryError('SCHEMA_REQUIRED', 409)
        columns = [{'name': f['name'], 'type': f['type']} for f in schemas[0]['fields']]
        if len({c['name'] for c in columns}) != len(columns):
            raise QueryError('SCHEMA_REQUIRED', 409)
        return dict(ref=asdict(ref), snapshot=str(snapshot), schema_id=schema_id,
            columns=columns, schema_fingerprint=hashlib.sha256(_json(schemas[0]).encode()).hexdigest())

    def _wap_owned(self, build):
        """Resolve an immutable, DB-local build; ownership stays with its builder."""
        context = self._context()
        with control_store.connect() as db:
            model = db.execute('SELECT version FROM wap_models WHERE database_id=? AND model_id=?',
                               (context.database_id, build['model_id'])).fetchone()
            row = db.execute('SELECT * FROM wap_builds WHERE id=? AND database_id=?',
                             (build['id'], context.database_id)).fetchone()
            owned = db.execute('SELECT * FROM dataops_owned_objects WHERE operation_id=? AND object_id=?',
                (build['operation_id'], build['stage']['ref']['object_id'])).fetchone()
        if (row is None or json.loads(row['record']) != build or owned is None or
                owned['incarnation'] != build['stage']['ref']['incarnation']):
            raise QueryError('OBJECT_NOT_OWNED', 403)
        if model is None or model['version'] != build['version']:
            raise QueryError('MODEL_VERSION_CHANGED', 409)
        ref = registry.ResourceRef(**build['stage']['ref'])
        if self._schema(ref.schema_id).sql_name != 'sandbox':
            raise QueryError('OBJECT_NOT_OWNED', 403)
        if self.wap_measure(ref) != build['stage']:
            raise QueryError('STAGE_CHANGED', 409)
        return ref

    @_serialized
    def wap_build(self, schema_id, sql, sources, *, step, result, _start=None):
        """Validate/rewrite a read model internally; never execute caller SQL verbatim."""
        from manual_wap import _compile_model
        schema = self._schema(schema_id, write=True)
        if schema.sql_name != 'sandbox':
            raise QueryError('SQL_PERMISSION_DENIED', 403)
        if _start is None:
            inputs = {alias: self.wap_measure(ref) for alias, ref in sources.items()}
        else:
            import db_marts
            inputs = db_marts._pinned(self, sources, _start)
        generated = _compile_model(sql, sources, execution=self, measurements=inputs)
        # Head append is harmless because SQL pins the captured snapshot; a
        # schema change can affect column binding even with the same snapshot.
        for alias, ref in sources.items():
            current = self.wap_measure(ref)
            if any(current[k] != inputs[alias][k] for k in ('ref', 'schema_id', 'schema_fingerprint')):
                raise QueryError('SOURCE_SCHEMA_CHANGED', 409)
        locator = registry.DestinationLocator(self._context().database_id, schema_id,
                                              'wap_' + secrets.token_hex(12))
        name = self._locator(locator)
        self._absent(locator)
        self._execute('wap_build', 'CREATE TABLE ' + name + ' AS ' + generated, step,
                      commit_result=dict(result, stage_locator=asdict(locator), input_refs=inputs,
                                         generated_sql=generated))
        ref = self._observe(locator)
        with control_store.connect() as db:
            db.execute('INSERT INTO dataops_owned_objects VALUES (?,?,?,?)',
                       (self.id, ref.object_id, ref.incarnation, 'stage'))
        return self.wap_measure(ref), inputs, generated

    @_serialized
    def wap_audit(self, build, not_null, *, step):
        """Count rows/nulls using the measured build's exact snapshot."""
        ref = self._wap_owned(build)
        names = {c['name'] for c in build['stage']['columns']}
        if set(not_null) - names:
            raise ValueError('not_null column absent from stage')
        expressions = ['COUNT(*)'] + ['COUNT(*) - COUNT(' + _column(n) + ')' for n in not_null]
        name = self._ref(ref) + ' FOR VERSION AS OF ' + build['stage']['snapshot']
        rows = self._execute('wap_audit', 'SELECT ' + ', '.join(expressions) + ' FROM ' + name, step)['data']
        if len(rows) != 1 or len(rows[0]) != len(expressions) or any(type(v) is not int or v < 0 for v in rows[0]):
            raise QueryError('INVALID_AUDIT_RESULT', 502)
        return rows[0][0], dict(zip(not_null, rows[0][1:]))

    @_serialized
    def wap_publish(self, target, build, audit, *, step, result):
        """Admin CTAS/CRTA of explicit columns at a passed immutable audit."""
        from manual_wap import _same_measurement
        self._schema(target.schema_id, write=True, admin=True)
        if asdict(target) != build['destination'] or (
                type(target) is registry.ResourceRef) != (build['target_kind'] == 'ref'):
            raise QueryError('TARGET_CHANGED', 409)
        self.wap_claim(target)
        ref = self._wap_owned(build)
        with control_store.connect() as db:
            saved = db.execute('SELECT record FROM wap_audits WHERE id=? AND database_id=?',
                               (audit['id'], self._context().database_id)).fetchone()
        if (saved is None or json.loads(saved['record']) != audit or not audit['passed'] or
                audit['build_id'] != build['id'] or audit['stage'] != build['stage'] or
                audit['version'] != build['version'] or audit['model_id'] != build['model_id']):
            raise QueryError('AUDIT_MISMATCH', 409)
        if type(target) is registry.ResourceRef:
            _same_measurement(self.wap_measure(target), build['target'])
            name, prefix = self._ref(target, write=True, admin=True), 'CREATE OR REPLACE TABLE '
        else:
            if build['target'] is not None:
                raise QueryError('TARGET_CHANGED', 409)
            name, prefix = self._locator(target, admin=True), 'CREATE TABLE '
            self._absent(target)
        columns = ', '.join(_column(c['name']) for c in audit['stage']['columns'])
        source = self._ref(ref) + ' FOR VERSION AS OF ' + audit['stage']['snapshot']
        return self._execute('wap_publish', prefix + name + ' AS SELECT ' + columns + ' FROM ' + source,
                             step, commit_result=result)

    @_serialized
    def wap_rollback(self, publication, *, step, result):
        """Admin rollback only to a DB-local publication's recorded predecessor."""
        from manual_wap import _same_measurement
        with control_store.connect() as db:
            row = db.execute('SELECT record FROM wap_publications WHERE id=? AND database_id=?',
                (publication['id'], self._context().database_id)).fetchone()
            model = db.execute('SELECT version,current_publication_id FROM wap_models WHERE database_id=? AND model_id=?',
                (self._context().database_id, publication['model_id'])).fetchone()
        if row is None or json.loads(row['record']) != publication or not publication.get('previous'):
            raise QueryError('NO_PREVIOUS_PUBLICATION', 409)
        if model is None or model['version'] != publication['version']:
            raise QueryError('MODEL_VERSION_CHANGED', 409)
        if model['current_publication_id'] != publication['id']:
            raise QueryError('PUBLICATION_CHANGED', 409)
        ref = registry.ResourceRef(**publication['target']['ref'])
        self.wap_claim(ref)
        name = self._ref(ref, write=True, admin=True)
        _same_measurement(self.wap_measure(ref), publication['target'])
        previous = publication['previous']
        if previous['ref'] != publication['target']['ref']:
            raise QueryError('TARGET_CHANGED', 409)
        # Iceberg rollback changes the main snapshot without restoring the
        # current schema. Do not attribute old rows to a different field contract.
        if (previous['schema_fingerprint'] != publication['target']['schema_fingerprint'] or
                previous['columns'] != publication['target']['columns']):
            raise QueryError('ROLLBACK_SCHEMA_INCOMPATIBLE', 409)
        schema = self._schema(ref.schema_id, write=True, admin=True)
        database, binding = self._catalog()
        with control_store.connect() as db:
            obj = registry.get_object(db, ref.object_id)
        with inventory.catalog_api(database, binding) as api:
            metadata = inventory._metadata(api, schema, 'TABLE', obj.physical_name)
        snapshot = previous['snapshot']
        if not isinstance(snapshot, str) or not re.fullmatch('[0-9]+', snapshot):
            raise ValueError('decimal snapshot required')
        if snapshot not in {str(s['snapshot-id']) for s in metadata.get('snapshots', [])}:
            raise QueryError('PREVIOUS_SNAPSHOT_EXPIRED', 409)
        return self._execute('wap_rollback', 'ALTER TABLE ' + name +
            ' EXECUTE rollback_to_snapshot(' + snapshot + ')', step, commit_result=result)

    @_serialized
    def ingest_columns(self, target, *, step):
        self._ingest_schema(target.schema_id)
        page = self._execute('describe', 'DESCRIBE ' + self._ref(target, write=True), step)
        columns = tuple((row[0], _type(row[1].upper())) for row in page['data'])
        if not columns or len({n for n, _ in columns}) != len(columns):
            raise ValueError('invalid target columns')
        return columns

    @_serialized
    def ingest_validate_keys(self, target, keys, *, step):
        self._ingest_schema(target.schema_id)
        names = tuple(_column(k) for k in keys)
        if not names:
            raise ValueError('upsert keys required')
        sql = 'SELECT ' + ', '.join(names) + ' FROM ' + self._ref(target, write=True)
        sql += ' GROUP BY ' + ', '.join(names) + ' HAVING COUNT(*) > 1 OR '
        sql += ' OR '.join(n + ' IS NULL' for n in names) + ' LIMIT 1'
        if self._execute('validate_keys', sql, step)['data']:
            raise ValueError('upsert target keys must be unique and non-null')

    @_serialized
    def ingest_rows(self, target, columns, rows, *, step, commit_result=None):
        """Typed VALUES only; callers cannot supply SQL expressions."""
        self._ingest_schema(target.schema_id)
        # Share the strict conversion with the adapter, including direct callers.
        from manual_ingest import typed_value
        columns = tuple((n, _type(t)) for n, t in columns)
        rows = tuple(tuple(row) for row in rows)
        if (not columns or len({n for n, _ in columns}) != len(columns) or not rows
                or any(len(row) != len(columns) for row in rows)):
            raise ValueError('distinct columns and matching rows required')
        values = ', '.join('(' + ', '.join('CAST(' + _literal(typed_value(v, t)) + ' AS ' + t + ')'
            for v, (_, t) in zip(row, columns)) + ')' for row in rows)
        return self._execute('ingest_rows', 'INSERT INTO ' + self._ref(target, write=True) +
            ' (' + ', '.join(_column(n) for n, _ in columns) + ') VALUES ' + values,
            step, commit_result=commit_result)

    def _owned_stage(self, source):
        with control_store.connect() as db:
            owned = db.execute('SELECT incarnation FROM dataops_owned_objects WHERE operation_id=? AND object_id=?',
                               (self.id, source.object_id)).fetchone()
        if owned is None or owned['incarnation'] != source.incarnation:
            raise QueryError('OBJECT_NOT_OWNED', 403)
        schema = self._ingest_schema(source.schema_id)
        if schema.sql_name != 'sandbox':
            raise QueryError('SQL_PERMISSION_DENIED', 403)
        return self._ref(source)

    @_serialized
    def ingest_publish(self, target, stage, columns, *, mode, expected_columns, keys=(), step, result):
        """Generated CTAS / INSERT SELECT / CRTA / MERGE, bounded to Ingest."""
        self._ingest_schema(target.schema_id)
        source = self._owned_stage(stage)
        columns = tuple((n, _type(t)) for n, t in columns)
        names = ', '.join(_column(n) for n, _ in columns)
        if mode == 'create':
            if type(target) is not registry.DestinationLocator:
                raise ValueError('create requires DestinationLocator')
            name = self._locator(target)
            self._absent(target)
            sql = 'CREATE TABLE ' + name + ' AS SELECT ' + names + ' FROM ' + source
        else:
            if mode not in ('replace', 'append', 'upsert'):
                raise ValueError('invalid ingest mode')
            current = self.ingest_columns(target, step=step + '-schema')
            if current != tuple(expected_columns):
                raise QueryError('TARGET_SCHEMA_CHANGED', 409)
            name = self._ref(target, write=True)  # final UUID observation
            if mode == 'append':
                sql = 'INSERT INTO ' + name + ' (' + names + ') SELECT ' + names + ' FROM ' + source
            elif mode == 'replace':
                sql = 'CREATE OR REPLACE TABLE ' + name + ' AS SELECT ' + names + ' FROM ' + source
            else:
                incoming = {n for n, _ in columns}
                if not keys or any(k not in incoming for k in keys):
                    raise ValueError('upsert keys required in input')
                self.ingest_validate_keys(target, keys, step=step + '-keys')
                if incoming - {n for n, _ in current}:
                    raise ValueError('upsert does not add target columns')
                match = ' AND '.join('s.' + _column(k) + ' = t.' + _column(k) for k in keys)
                updates = [n for n, _ in columns if n not in keys]
                sql = 'MERGE INTO ' + name + ' t USING ' + source + ' s ON ' + match
                if updates:
                    sql += ' WHEN MATCHED THEN UPDATE SET ' + ', '.join(
                        _column(n) + ' = s.' + _column(n) for n in updates)
                sql += ' WHEN NOT MATCHED THEN INSERT (' + names + ') VALUES ('
                sql += ', '.join('s.' + _column(n) for n, _ in columns) + ')'
            self._ref(target, write=True)
        return self._execute('ingest_commit', sql, step, commit_result=result)

    @_serialized
    def create_stage(self, schema_id, columns, *, step, purpose='stage'):
        if purpose not in ('stage', 'backup'):
            raise ValueError('invalid owned object purpose')
        context = self._context()
        locator = registry.DestinationLocator(context.database_id, schema_id,
            'dop_' + secrets.token_hex(12))
        ref = self.create_table(locator, columns, step=step)
        with control_store.connect() as db:
            db.execute('INSERT INTO dataops_owned_objects VALUES (?,?,?,?)',
                       (self.id, ref.object_id, ref.incarnation, purpose))
        return ref

    @_serialized
    def insert_rows(self, target, columns, rows, *, step):
        name = self._ref(target, write=True)
        columns, rows = tuple(columns), tuple(tuple(row) for row in rows)
        if not columns or len(set(columns)) != len(columns) or not rows or any(len(row) != len(columns) for row in rows):
            raise ValueError('distinct columns and matching nonempty rows required')
        names = ', '.join(_identifier(c) for c in columns)
        values = ', '.join('(' + ', '.join(_literal(v) for v in row) + ')' for row in rows)
        return self._execute('insert', 'INSERT INTO ' + name + ' (' + names + ') VALUES ' + values, step)

    @_serialized
    def select(self, source, columns, *, step, snapshot_id=None):
        name = self._ref(source)
        columns = tuple(columns)
        if not columns:
            raise ValueError('explicit columns required')
        if snapshot_id is not None:
            if not isinstance(snapshot_id, str) or not re.fullmatch('[0-9]+', snapshot_id):
                raise ValueError('snapshot id must be a decimal string')
            name += ' FOR VERSION AS OF ' + snapshot_id
        return self._execute('select', 'SELECT ' + ', '.join(_identifier(c) for c in columns) + ' FROM ' + name, step)

    @_serialized
    def insert_select(self, target, source, columns, *, step):
        target_name, source_name = self._ref(target, write=True), self._ref(source)
        columns = tuple(columns)
        if not columns:
            raise ValueError('explicit columns required')
        names = ', '.join(_identifier(c) for c in columns)
        return self._execute('insert_select', 'INSERT INTO ' + target_name + ' (' + names + ') SELECT ' + names + ' FROM ' + source_name, step)

    @_serialized
    def rename(self, source, destination, *, step):
        source_name = self._ref(source, write=True, admin=True)
        destination_name = self._locator(destination, admin=True)
        self._absent(destination)
        self._execute('rename', 'ALTER TABLE ' + source_name + ' RENAME TO ' + destination_name, step)
        observed = self._observe(destination)
        with control_store.connect() as db:
            db.execute('UPDATE object_bindings SET current=0 WHERE id=?', (source.object_id,))
            # Promotion makes a final destination; it is no longer temporary.
            db.execute('DELETE FROM dataops_owned_objects WHERE operation_id=? AND object_id=?',
                       (self.id, source.object_id))
        return observed

    @_serialized
    def cleanup(self, ref, *, step):
        try:
            with control_store.connect() as db:
                owned = db.execute('SELECT incarnation FROM dataops_owned_objects WHERE operation_id=? AND object_id=?',
                                   (self.id, ref.object_id)).fetchone()
            if owned is None or owned['incarnation'] != ref.incarnation:
                raise QueryError('OBJECT_NOT_OWNED', 403)
            name = self._ref(ref, write=True)
            result = self._execute('cleanup', 'DROP TABLE ' + name, step)
            with control_store.connect() as db:
                db.execute('UPDATE object_bindings SET current=0 WHERE id=?', (ref.object_id,))
            return result
        except Exception as error:
            with control_store.connect() as db:
                db.execute('UPDATE dataops_operations SET cleanup_error=? WHERE id=?',
                           (getattr(error, 'code', 'CLEANUP_FAILED'), self.id))
            raise

    def _successful_work(self):
        with control_store.connect() as db:
            states = [r[0] for r in db.execute('''SELECT q.state FROM dataops_queries d
                JOIN query_records q ON q.id=d.query_id WHERE d.operation_id=? AND d.action!='cleanup' ''', (self.id,))]
        if not states or any(state != 'FINISHED' for state in states):
            raise QueryError('OPERATION_NOT_SUCCESSFUL', 409)

    @_serialized
    def record_commit(self, result):
        """Record a verified commit before attempting optional cleanup."""
        self._active()
        self._successful_work()
        with control_store.connect() as db:
            db.execute('UPDATE dataops_operations SET committed=1,result=? WHERE id=?', (_json(result), self.id))

    @_serialized
    def complete(self, result=None, *, committed=False):
        frozen, parent, owner, _ = self._active()
        self._successful_work()
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            admission.finish(db, parent, state='FINISHED')
            db.execute('''UPDATE dataops_operations SET state='FINISHED',
                committed=MAX(committed,?),result=COALESCE(?,result) WHERE id=?''',
                (int(committed), _json(result) if result is not None else None, self.id))
        owner.close()
        return self._inspect()

    @_serialized
    def fail(self):
        """Settle a definite failure after all child queries have settled."""
        _, parent, owner, _ = self._active()
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            admission.finish(db, parent, state='FAILED')
            db.execute("UPDATE dataops_operations SET state='FAILED' WHERE id=?", (self.id,))
        owner.close()
        return self._inspect()

    def inspect(self):
        token = admission._current.set(None)
        try:
            self._context()
            return self._inspect()
        finally:
            admission._current.reset(token)

    def _inspect(self):
        """Private canonical observation for a worker after parent settlement."""
        row = self._row()
        if row['state'] in ('ACTIVE', 'CANCELING') and self.id not in _live:
            # An unlocked process lease proves only loss of the owner, never
            # whether SQL ran. Preserve all unresolved intents as UNKNOWN.
            try:
                owner = queries._acquire_query_owner('dataops-' + self.id)
            except QueryError:
                owner = None
            if owner is not None:
                try:
                    context = ResolvedQueryContext(**json.loads(row['context']))
                    with control_store.connect() as db:
                        db.execute('BEGIN IMMEDIATE')
                        current = db.execute('SELECT state FROM dataops_operations WHERE id=?', (self.id,)).fetchone()
                        if current['state'] not in ('ACTIVE', 'CANCELING'):
                            return self._inspect()
                        parent = admission.get_for_subject(db, kind='dataops', subject_id=self.id,
                                                           warehouse_id=context.warehouse_id)
                        for child in db.execute('''SELECT q.id FROM query_records q JOIN dataops_queries d
                            ON d.query_id=q.id WHERE d.operation_id=? AND q.state IN ('QUEUED','RUNNING')''', (self.id,)):
                            registry.set_query_state(db, child['id'], 'UNKNOWN')
                            handle = admission.get_for_subject(db, kind='query', subject_id=child['id'],
                                                               warehouse_id=context.warehouse_id)
                            admission.finish(db, handle, state='UNKNOWN')
                        admission.finish(db, parent, state='UNKNOWN')
                        db.execute("UPDATE dataops_operations SET state='UNKNOWN' WHERE id=?", (self.id,))
                finally:
                    owner.close()
                row = self._row()
        with control_store.connect() as db:
            children = [dict(r) for r in db.execute('''SELECT d.step,d.action,q.id,q.state,e.backend_query_id,
                COALESCE((SELECT execution_closure FROM warehouse_admissions a
                    WHERE a.kind='query' AND a.subject_id=q.id),'') AS execution_closure
                FROM dataops_queries d JOIN query_records q ON q.id=d.query_id
                JOIN query_execution_contexts e ON e.query_id=q.id WHERE d.operation_id=? ORDER BY q.created_at''', (self.id,))]
        return dict(id=self.id, state=row['state'], committed=bool(row['committed']),
                    result=json.loads(row['result']) if row['result'] else None,
                    cleanup_error=row['cleanup_error'], queries=children, replayed=self.replayed)

    def reconcile_queries(self):
        """Use the existing evidence-based query reconciler; never resend SQL.

        Settled children do not imply that a multi-step operation committed.
        Its UNKNOWN parent remains an operator reconciliation boundary.
        """
        context = self._context()
        snapshot = self.inspect()
        for child in snapshot['queries']:
            if child['state'] == 'UNKNOWN':
                queries.reconcile_query_from_backend(self.principal, child['id'],
                    selected_role=context.selected_role)
        return self.inspect()

    def reconcile_failure(self):
        """Explicitly abandon a reconciled operation, retaining known commits.

        Every child must have a definite terminal outcome. An empty child
        ledger can be abandoned under the owner lease because every POST must
        first persist a child intent. This never declares successful execution.
        """
        context = self._context()
        lock = _live[self.id][3] if self.id in _live else _lock
        with lock:
            owner = None
            if self.id not in _live:
                owner = queries._acquire_query_owner('dataops-' + self.id)
            try:
                with control_store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    row = db.execute('SELECT state,context,fingerprint FROM dataops_operations WHERE id=?', (self.id,)).fetchone()
                    if row['state'] != 'UNKNOWN':
                        raise QueryError('ADMISSION_CONFLICT', 409)
                    children = [dict(r) for r in db.execute('''SELECT q.id,q.state,e.backend_query_id,
                        a.state AS admission_state,a.evidence_ref FROM dataops_queries d
                        JOIN query_records q ON q.id=d.query_id
                        JOIN query_execution_contexts e ON e.query_id=q.id
                        JOIN warehouse_admissions a ON a.kind='query' AND a.subject_id=q.id
                            AND a.warehouse_id=q.warehouse_id WHERE d.operation_id=?''', (self.id,))]
                    if any(c['state'] not in queries._TERMINAL or
                            c['admission_state'] not in admission.TERMINAL for c in children):
                        raise QueryError('UNRESOLVED_CHILD_QUERIES', 409)
                    evidence = hashlib.sha256(_json(dict(operation_id=self.id,
                        context=json.loads(row['context']), fingerprint=row['fingerprint'],
                        children=children, outcome='FAILED')).encode()).hexdigest()
                    parent = admission.get_for_subject(db, kind='dataops', subject_id=self.id,
                                                       warehouse_id=context.warehouse_id)
                    admission.finish(db, parent, state='FAILED', evidence_ref=evidence)
                    db.execute("UPDATE dataops_operations SET state='FAILED' WHERE id=?", (self.id,))
                if self.id in _live:
                    _live[self.id][2].close()
            finally:
                if owner is not None:
                    owner.close()
        return self.inspect()

    def cancel(self):
        _cancel_account(self.principal)
        row = self._row()
        context = ResolvedQueryContext(**json.loads(row['context']))
        # Stop new steps durably before cancellation. Do not wait for the
        # operation lock: an ongoing page loop holds it until the query settles.
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE dataops_operations SET state='CANCELING' WHERE id=? AND state='ACTIVE'", (self.id,))
            children = [r[0] for r in db.execute('SELECT query_id FROM dataops_queries WHERE operation_id=?', (self.id,))]
        results = []
        for query_id in children:
            pin = queries._pins.get(query_id)
            # Page delivery and cancellation share this lock. An UNKNOWN result
            # arriving after the route preflight must remain observation-only.
            with pin.lock if pin is not None else nullcontext():
                with control_store.connect() as db:
                    parent_state = db.execute('SELECT state FROM dataops_operations WHERE id=?',
                                              (self.id,)).fetchone()['state']
                    child_state = db.execute('SELECT state FROM query_records WHERE id=?',
                                             (query_id,)).fetchone()['state']
                if parent_state == 'UNKNOWN' or child_state == 'UNKNOWN' or child_state in queries._TERMINAL:
                    results.append(dict(id=query_id, state=child_state))
                else:
                    results.append(queries.cancel(self.principal, query_id,
                                                  selected_role=context.selected_role))
        # Never hold a child pin while waiting for the step's operation lock.
        lock = _live[self.id][3] if self.id in _live else _lock
        with lock:
            row = self._row()
            if any(r['state'] == 'UNKNOWN' for r in results) and row['state'] in ('ACTIVE', 'CANCELING'):
                with control_store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    parent = admission.get_for_subject(db, kind='dataops', subject_id=self.id,
                                                       warehouse_id=context.warehouse_id)
                    admission.finish(db, parent, state='UNKNOWN')
                    db.execute("UPDATE dataops_operations SET state='UNKNOWN' WHERE id=?", (self.id,))
                row = self._row()
            if row['state'] in ('ACTIVE', 'CANCELING') and all(r['state'] in queries._TERMINAL for r in results):
                with control_store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    parent = admission.get_for_subject(db, kind='dataops', subject_id=self.id,
                                                       warehouse_id=context.warehouse_id)
                    admission.finish(db, parent, state='CANCELED')
                    db.execute("UPDATE dataops_operations SET state='CANCELED' WHERE id=?", (self.id,))
                if self.id in _live:
                    _live[self.id][2].close()
            return results
