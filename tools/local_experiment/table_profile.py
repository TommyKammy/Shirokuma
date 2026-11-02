"""Explicit all-row, top-level NULL aggregation over one frozen TABLE version."""
from dataclasses import asdict
from decimal import Decimal, ROUND_HALF_UP, localcontext
import json
import re
import threading

import control_store
import data_management as registry
import database_inventory as inventory
import dataops_service as ds
import warehouse_admission as admission
from query_context import QueryError, ResolvedQueryContext


ROUTE = 'table-profile'
_slots = threading.BoundedSemaphore(4)


class ObservationExecution(ds.Execution):
    """Profile public reads validate access without touching child execution."""
    def _context(self):
        context = ResolvedQueryContext(**json.loads(self._row()['context']))
        ds._validated_context(self.principal, context)
        return context


def _authorize(execution, context, source):
    row = execution._row()
    if row['route'] != ROUTE or json.loads(row['context']) != asdict(context):
        raise QueryError('CONTEXT_MISMATCH', 409)
    ds._validated_context(execution.principal, context)
    if source.database_id != context.database_id:
        raise QueryError('CROSS_DATABASE_TARGET', 409)
    if source.schema_id != context.schema_id:
        raise QueryError('PROFILE_SOURCE_CONTEXT_REQUIRED', 409)
    execution._schema(source.schema_id)


def _guard(execution, context, source, frozen=None, snapshot_id=None):
    _authorize(execution, context, source)
    measured = execution.export_measure(source, frozen, snapshot_id=snapshot_id, allow_empty=True)
    _authorize(execution, context, source)
    return measured


def _source_status(execution, context, source):
    """Read canonical status only; ACTIVE does not establish engine liveness."""
    _authorize(execution, context, source)
    with control_store.connect() as db:
        row = db.execute('''SELECT o.id,o.state,EXISTS(
            SELECT 1 FROM dataops_queries d JOIN query_records q ON q.id=d.query_id
            WHERE d.operation_id=o.id AND q.state='UNKNOWN') AS child_unknown
            FROM dataops_operations o WHERE o.id=? AND o.actor=?''',
            (execution.id, execution.principal.principal_id)).fetchone()
    _authorize(execution, context, source)
    if row is None:
        raise QueryError('OPERATION_NOT_FOUND', 404)
    return dict(id=row['id'], state=row['state'], child_unknown=bool(row['child_unknown']))


def _observe_guard(execution, context, source, frozen, snapshot_id):
    try:
        _guard(execution, context, source, frozen, snapshot_id)
    except (QueryError, inventory.InventoryError) as error:
        # Only known source drift/expiry and normalized catalog failures qualify.
        # Context, account and permission failures never acquire a status DTO.
        allowed = (('STALE_RESOURCE', 'EXPORT_SOURCE_CHANGED', 'EXPORT_SNAPSHOT_UNAVAILABLE',
                    'SCHEMA_REQUIRED', 'CATALOG_UNAVAILABLE') if isinstance(error, QueryError)
                   else ('NOT_FOUND', 'CATALOG_UNAVAILABLE'))
        if error.status in (401, 403) or error.code not in allowed:
            raise
        minimal = _source_status(execution, context, source)
        raise QueryError(error.code, error.status,
            details=dict(context=context.public_metadata(), operation=minimal)) from None


def inspect(execution, context):
    """Owned typed observation, including generic GET and error projections."""
    execution = ObservationExecution(execution.principal, execution.id, execution.replayed)
    row = execution._row()
    if row['route'] != ROUTE or json.loads(row['context']) != asdict(context):
        raise QueryError('CONTEXT_MISMATCH', 409)
    payload = json.loads(row['payload'])['payload']
    source = registry.ResourceRef(**payload['source'])
    frozen = json.loads(row['result'])['source'] if row['result'] else None
    token = admission._current.set(None)
    try:
        _authorize(execution, context, source)
        return _inspect_source(execution, context, source, frozen, payload)
    finally:
        admission._current.reset(token)


def _inspect_source(execution, context, source, frozen, payload):
    _observe_guard(execution, context, source, frozen, payload['snapshot_id'])
    observation = execution._inspect()
    # Re-read result: a worker may have finished during the first check.
    result = observation.get('result')
    _observe_guard(execution, context, source, result['source'] if result else frozen, payload['snapshot_id'])
    return observation


def _fail_definite(execution, context):
    """Settle owned accepted work without requiring a now-revoked grant."""
    execution._row()
    live = ds._live.get(execution.id)
    if live is None or execution.replayed:
        return
    with live[3], control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT state FROM dataops_operations WHERE id=?', (execution.id,)).fetchone()
        states = [r[0] for r in db.execute('''SELECT q.state FROM query_records q JOIN dataops_queries d
            ON q.id=d.query_id WHERE d.operation_id=?''', (execution.id,))]
        if row['state'] != 'ACTIVE' or any(s not in ('FINISHED', 'FAILED', 'CANCELED') for s in states):
            return
        admission.finish(db, live[1], state='FAILED')
        db.execute("UPDATE dataops_operations SET state='FAILED' WHERE id=?", (execution.id,))
    live[2].close()


def _count(value):
    if (type(value) is not str or not re.fullmatch(r'0|[1-9][0-9]{0,18}', value)
            or int(value) > 2**63 - 1):
        raise QueryError('INVALID_PROFILE_RESULT', 502)
    return int(value)


def _metrics(frozen, rows):
    columns = frozen['columns']
    if (type(rows) is not list or len(rows) != 1 or not isinstance(rows[0], (list, tuple))
            or len(rows[0]) != len(columns) + 1):
        raise QueryError('INVALID_PROFILE_RESULT', 502)
    total, *nulls = [_count(value) for value in rows[0]]
    if any(value > total for value in nulls) or frozen['snapshot'] is None and total != 0:
        raise QueryError('INVALID_PROFILE_RESULT', 502)
    result = []
    with localcontext() as decimal_context:
        decimal_context.prec = 50
        for column, value in zip(columns, nulls):
            percent = (format((Decimal(value) * 100 / Decimal(total)).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP), '.2f') if total else None)
            result.append(dict(name=column['name'], type=column['type'],
                null_count=str(value), null_percent=percent))
    return dict(source=frozen, row_count=str(total), column_count=str(len(columns)), columns=result)


def _work(execution, context, source, frozen):
    try:
        with ds._accepted(execution):
            _guard(execution, context, source, frozen)
            schema = execution._schema(source.schema_id)
            with control_store.connect() as db:
                obj = registry.get_object(db, source.object_id)
            if (obj is None or not obj.current or obj.kind != 'TABLE'
                    or obj.schema_id != schema.id or obj.incarnation != source.incarnation
                    or obj.backend_identity != frozen['table_uuid']):
                raise QueryError('STALE_RESOURCE', 409)
            # Read the guarded registry identity; write-name grammar excludes
            # otherwise readable catalog TABLE names.
            name = '.'.join(registry.quote_identifier(part) for part in
                (context.sql_catalog, schema.sql_name, obj.physical_name))
            expressions = ['CAST(COUNT(*) AS VARCHAR) AS "_p0"'] + [
                'CAST(COUNT(*)-COUNT(' + ds._column(column['name']) + ') AS VARCHAR) AS "_p' + str(i) + '"'
                for i, column in enumerate(frozen['columns'], 1)]
            sql = 'SELECT ' + ', '.join(expressions) + ' FROM ' + name
            if frozen['snapshot'] is not None:
                sql += ' FOR VERSION AS OF ' + frozen['snapshot']
            page = execution._execute('table_profile', sql, 'profile',
                projection=[('_p' + str(i), 'varchar') for i in range(len(expressions))])
            result = _metrics(frozen, page['data'])
            _guard(execution, context, source, frozen)
            execution.complete(result)  # Read-only, committed remains false.
    except Exception:
        # UNKNOWN (parent or child) stays unresolved. Never cancel or resend.
        try:
            _fail_definite(execution, context)
        except Exception:
            pass
    finally:
        _slots.release()


def profile(principal, context, *, key, source, snapshot_id):
    if type(source) is not registry.ResourceRef:
        raise ValueError('explicit source required')
    if snapshot_id is not None and (type(snapshot_id) is not str or not re.fullmatch('[0-9]+', snapshot_id)):
        raise ValueError('ASCII decimal snapshot required')
    ds._validated_context(principal, context)
    execution = ds.begin(principal, context, route=ROUTE, idempotency_key=key,
        payload=dict(source=asdict(source), snapshot_id=snapshot_id), _inspect_replay=False)
    if execution.replayed:
        return inspect(execution, context)
    try:
        frozen = _guard(ObservationExecution(principal, execution.id), context, source, snapshot_id=snapshot_id)
        with control_store.connect() as db:
            db.execute('UPDATE dataops_operations SET result=? WHERE id=?',
                (ds._json(dict(source=frozen)), execution.id))
        if not _slots.acquire(blocking=False):
            raise QueryError('PROFILE_WORKER_BUSY', 409)
        try:
            threading.Thread(target=_work, args=(execution, context, source, frozen), daemon=True).start()
        except Exception:
            _slots.release()
            raise
    except Exception:
        _fail_definite(execution, context)
        raise
    return inspect(execution, context)
