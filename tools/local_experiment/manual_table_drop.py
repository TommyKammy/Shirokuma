"""Explicit whole TABLE catalog unregister. No SQL, purge, retry or restore."""
from dataclasses import asdict, fields
import hashlib
import json
import re
import threading
import uuid

import control_store
import data_management as registry
import database_inventory as inventory
import dataops_service as ds
import warehouse_admission as admission
from query_context import QueryError, ResolvedQueryContext

ROUTE = 'manual-table-drop'
MODE = 'CATALOG_UNREGISTER_KEEP_DATA'
ACK_TYPE = 'ICEBERG_REST_DELETE_NONPURGING'


def _head(metadata):
    head = metadata.get('current-snapshot-id')
    if head is None or type(head) is int and head == -1:
        if metadata.get('snapshots') != []:
            raise QueryError('INVALID_TABLE_HEAD', 409)
        return None
    if type(head) not in (str, int) or not re.fullmatch('0|[1-9][0-9]*', str(head)):
        raise QueryError('INVALID_TABLE_HEAD', 409)
    return str(head)


def _old_retired(db, guard):
    source = guard['source']
    old = db.execute('SELECT * FROM object_bindings WHERE id=?', (source['object_id'],)).fetchone()
    return old is not None and all(old[k] == v for k, v in dict(
        schema_id=source['schema_id'], incarnation=source['incarnation'], backend_identity=guard['uuid'],
        physical_name=guard['name'], kind='TABLE', current=0).items())


def pure_closed_proof(db, operation_id):
    """Historical ledger proof only. Never authorize, observe, settle or write."""
    try:
        row = db.execute('SELECT * FROM dataops_operations WHERE id=?', (operation_id,)).fetchone()
        if row is None or row['route'] != ROUTE or row['state'] != 'FINISHED' or row['committed'] != 1:
            return False
        normalized, context, result = (json.loads(row[k]) for k in ('payload', 'context', 'result'))
        if (set(normalized) != {'context', 'payload'} or normalized['context'] != context
                or row['payload'] != ds._json(normalized) or row['context'] != ds._json(context)
                or row['result'] != ds._json(result) or not row['actor']
                or row['fingerprint'] != hashlib.sha256(row['payload'].encode()).hexdigest()):
            return False
        names = {f.name for f in fields(ResolvedQueryContext)}
        if (set(context) != names or context['selected_role'] != 'admin'
                or any(type(context[k]) is not str or not context[k].strip()
                    for k in names - {'binding_revision', 'grant_revision'})
                or type(context['binding_revision']) is not int or context['binding_revision'] < 1
                or context['grant_revision'] is not None and (
                    type(context['grant_revision']) is not int or context['grant_revision'] < 1)):
            return False
        payload = normalized['payload']
        if set(payload) != {'source', 'mode', 'confirm_name', 'expected_preview_fingerprint'}:
            return False
        source = asdict(registry.ResourceRef(**payload['source']))
        guard = result['guard']
        if (set(guard) != {'principal', 'context', 'source', 'name', 'uuid', 'head', 'schema', 'metadata', 'metadata_location', 'mode'}
                or payload['mode'] != MODE or guard['mode'] != MODE or result['mode'] != MODE
                or type(result['ack']) is not int or result['ack'] != 204 or result['ack_type'] != ACK_TYPE
                or result['phase'] != 'ACKNOWLEDGED' or result['retirement_closure'] != 'PROVEN'
                or guard['principal'] != row['actor'] or guard['context'] != context or guard['source'] != source
                or source['database_id'] != context['database_id'] or source['schema_id'] != context['schema_id']
                or type(guard['name']) is not str or not guard['name'] or '\x00' in guard['name']
                or payload['confirm_name'] != guard['name']
                or payload['expected_preview_fingerprint'] != hashlib.sha256(ds._json(guard).encode()).hexdigest()):
            return False
        uuid.UUID(guard['uuid'])
        schema = asdict(registry.Schema(**guard['schema']))
        if (schema['id'] != source['schema_id'] or schema['database_id'] != source['database_id']
                or schema['sql_name'] != context['sql_schema'] or schema['purpose'] != schema['sql_name']
                or schema['sql_name'] not in ('raw', 'sandbox', 'staging', 'marts')
                or guard['metadata']['table-uuid'] != guard['uuid'] or _head(guard['metadata']) != guard['head']
                or not _old_retired(db, guard)):
            return False
        parents = db.execute("SELECT * FROM warehouse_admissions WHERE kind='dataops' AND subject_id=?", (operation_id,)).fetchall()
        if len(parents) != 1:
            return False
        parent = parents[0]
        if (any(parent[k] != v for k, v in dict(actor=row['actor'], state='FINISHED',
                database_id=context['database_id'], warehouse_id=context['warehouse_id'], generation_id=context['generation_id']).items())
                or parent['execution_closure']
                or db.execute('SELECT 1 FROM dataops_queries WHERE operation_id=?', (operation_id,)).fetchone()
                or db.execute('SELECT 1 FROM query_execution_contexts WHERE request_id=?', (operation_id,)).fetchone()
                or db.execute('SELECT 1 FROM warehouse_admissions WHERE parent_id=?', (parent['id'],)).fetchone()):
            return False
        return True
    except Exception:
        return False


def _access(principal, context, source=None):
    if context.selected_role != 'admin':
        raise QueryError('SQL_PERMISSION_DENIED', 403)
    ds._validated_context(principal, context)
    if source is not None and (source.database_id != context.database_id or source.schema_id != context.schema_id):
        raise QueryError('DROP_SOURCE_CONTEXT_REQUIRED', 409)
    with control_store.connect() as db:
        schema = registry.get_schema(db, context.schema_id)
        database = registry.get_database(db, context.database_id)
        binding = registry.get_binding(db, context.database_id)
    if (schema is None or schema.database_id != context.database_id
            or schema.sql_name not in ('raw', 'sandbox', 'staging', 'marts')
            or schema.purpose != schema.sql_name
            or not all(inventory._permissions('admin', schema).values())):
        raise QueryError('SQL_PERMISSION_DENIED', 403)
    if database.state != 'READY':
        raise QueryError('DB_ACCESS_REVOKED', 403)
    return schema, database, binding


def _registered(principal, context, source):
    schema, database, binding = _access(principal, context, source)
    with control_store.connect() as db:
        obj = registry.get_object(db, source.object_id)
        owned = db.execute('SELECT 1 FROM dataops_owned_objects WHERE object_id=? AND incarnation=?',
            (source.object_id, source.incarnation)).fetchone()
    if (obj is None or obj.schema_id != schema.id or obj.incarnation != source.incarnation
            or obj.kind != 'TABLE' or not obj.physical_name or '\x00' in obj.physical_name):
        raise QueryError('STALE_RESOURCE', 409)
    if owned:
        raise QueryError('OBJECT_OWNED_BY_DATAOPS', 409)
    try:
        uuid.UUID(obj.backend_identity)
    except (ValueError, TypeError, AttributeError):
        raise QueryError('STALE_RESOURCE', 409) from None
    return schema, database, binding, obj


def _measure(principal, context, source, api=None, expected=None):
    schema, database, binding, obj = _registered(principal, context, source)
    if expected is not None and (obj.physical_name != expected['name']
            or obj.backend_identity != expected['uuid'] or asdict(schema) != expected['schema']):
        raise QueryError('PREVIEW_CHANGED', 409)
    if api is None:
        with inventory.catalog_api(database, binding) as reader:
            raw = reader(inventory._path(schema, 'TABLE', obj.physical_name))
    else:
        raw = api(inventory._path(schema, 'TABLE', obj.physical_name))
    metadata = raw.get('metadata')
    if not isinstance(metadata, dict) or metadata.get('table-uuid') != obj.backend_identity:
        raise QueryError('STALE_RESOURCE', 409)
    head = _head(metadata)
    guard = dict(principal=principal.principal_id, context=asdict(context), source=asdict(source),
        name=obj.physical_name, uuid=obj.backend_identity, head=head,
        schema=asdict(schema), metadata=metadata, metadata_location=raw.get('metadata-location'), mode=MODE)
    fingerprint = hashlib.sha256(ds._json(guard).encode()).hexdigest()
    # Revalidate identity and authority after the external read. No observe writes.
    if _registered(principal, context, source) != (schema, database, binding, obj):
        raise QueryError('PREVIEW_CHANGED', 409)
    return guard, fingerprint


def _preview(guard, fingerprint):
    return dict(label=guard['name'], source=guard['source'], mode=MODE,
        head=guard['head'], scope='ALL_SNAPSHOTS', preview_fingerprint=fingerprint,
        confirmation_name=guard['name'])


def preview(principal, context, source):
    if type(source) is not registry.ResourceRef:
        raise ValueError('ResourceRef required')
    guard, fingerprint = _measure(principal, context, source)
    return _preview(guard, fingerprint)


class DropExecution(ds.Execution):
    def inspect(self):
        return inspect(self, self._context())

    def _context(self):
        row = self._row()
        if row['route'] != ROUTE:
            raise QueryError('OPERATION_NOT_FOUND', 404)
        context = ResolvedQueryContext(**json.loads(row['context']))
        source = registry.ResourceRef(**json.loads(row['payload'])['payload']['source'])
        _access(self.principal, context, source)
        return context

    def _ref(self, ref, *, write=False, admin=False):
        context = self._context()
        schema, _, _, obj = _registered(self.principal, context, ref)
        return '.'.join(registry.quote_identifier(s) for s in
            (context.sql_catalog, schema.sql_name, obj.physical_name))

    def _successful_work(self):
        self._row()
        with control_store.connect() as db:
            if not pure_closed_proof(db, self.id):
                raise QueryError('OPERATION_NOT_SUCCESSFUL', 409)

    def reconcile_failure(self):
        self._row()
        raise QueryError('OPERATION_OBSERVATION_ONLY', 409)

    def reconcile_queries(self):
        self._row()
        raise QueryError('OPERATION_OBSERVATION_ONLY', 409)

    def _inspect(self, *, _recover=True):
        # Base recovery only settles a lost lease as UNKNOWN. Its raw DTO is
        # deliberately discarded; private guards never leave this module.
        if _recover:
            super()._inspect()
        row = self._row()
        context = self._context()
        payload = json.loads(row['payload'])['payload']
        result = json.loads(row['result'] or '{}')
        return dict(id=self.id, state=row['state'], committed=bool(row['committed']),
            context=asdict(context), source=payload['source'], mode=MODE,
            label=result.get('guard', {}).get('name'), head=result.get('guard', {}).get('head'),
            phase=result.get('phase', 'PREPARING'), acknowledged=result.get('ack') == 204,
            retirement_closure=result.get('retirement_closure'),
            warning=result.get('warning'), queries=[], replayed=self.replayed)

    def cancel(self):
        self._context()
        # Do not wait on the worker lock: SENT may be blocked in transport.
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM dataops_operations WHERE id=?', (self.id,)).fetchone()
            result = json.loads(row['result'] or '{}')
            if row['state'] == 'ACTIVE' and result.get('phase') != 'SENT' and not row['committed']:
                parent = admission.get_for_subject(db, kind='dataops', subject_id=self.id,
                    warehouse_id=json.loads(row['context'])['warehouse_id'])
                admission.finish(db, parent, state='CANCELED')
                db.execute("UPDATE dataops_operations SET state='CANCELED' WHERE id=?", (self.id,))
                live = ds._live.get(self.id)
                if live:
                    live[2].close()
        return self.inspect()


def inspect(execution, context, *, _recover=True):
    typed = DropExecution(execution.principal, execution.id, execution.replayed)
    if json.loads(typed._row()['context']) != asdict(context):
        raise QueryError('CONTEXT_MISMATCH', 409)
    token = admission._current.set(None)
    try:
        typed._context()
        result = typed._inspect(_recover=_recover)
        typed._context()
        return result
    finally:
        admission._current.reset(token)


def lookup_by_key(principal, context, key):
    """Read an owned original intent only. Absence never proves non-dispatch."""
    if (type(key) is not str or not key.strip() or len(key) > 256
            or any(ord(c) < 32 or ord(c) == 127 for c in key)):
        raise ValueError('one valid original idempotency key required')
    _access(principal, context)
    with control_store.connect() as db:
        row = db.execute('''SELECT id,context FROM dataops_operations
            WHERE actor=? AND route=? AND idempotency_key=?''',
            (principal.principal_id, ROUTE, key)).fetchone()
    _access(principal, context)
    if row is None:
        raise QueryError('OPERATION_NOT_FOUND', 404)
    if json.loads(row['context']) != asdict(context):
        raise QueryError('CONTEXT_MISMATCH', 409)
    # Key recovery does not acquire a lease or reconcile a lost worker. Return
    # the same closed DTO without invoking base inspect's settlement path.
    return inspect(DropExecution(principal, row['id'], replayed=True), context, _recover=False)


def _settle(execution, state, result, *, ack=False):
    # Effect receipt and parent settlement must survive final authorization loss.
    row = execution._row()
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        current = db.execute('SELECT state FROM dataops_operations WHERE id=?', (execution.id,)).fetchone()
        if current['state'] == 'CANCELED' and not ack:
            live = ds._live.get(execution.id)
            if live:
                live[2].close()
            return
        parent = admission.get_for_subject(db, kind='dataops', subject_id=execution.id,
            warehouse_id=json.loads(row['context'])['warehouse_id'])
        if ack:
            guard = result['guard']
            source = guard['source']
            db.execute('''UPDATE object_bindings SET current=0 WHERE id=? AND schema_id=?
                AND incarnation=? AND backend_identity=? AND current=1''',
                (source['object_id'], source['schema_id'], source['incarnation'], guard['uuid']))
            result.update(ack=204, ack_type=ACK_TYPE, mode=MODE,
                retirement_closure='PROVEN' if _old_retired(db, guard) else 'UNPROVEN')
            if result['retirement_closure'] != 'PROVEN':
                result['warning'] = 'CATALOG_REMOVED_RETIREMENT_UNPROVEN'
        try:
            admission.finish(db, parent, state=state)
        except admission.AdmissionConflict:
            if not ack:
                raise
            result.update(retirement_closure='UNPROVEN', warning='CATALOG_REMOVED_RETIREMENT_UNPROVEN')
        db.execute('UPDATE dataops_operations SET state=?,committed=MAX(committed,?),result=? WHERE id=?',
            (state, int(ack), ds._json(result), execution.id))
    live = ds._live.get(execution.id)
    if live:
        live[2].close()


def _work(execution, context, source, expected, confirm_name):
    sent = False
    result = {}
    try:
        with ds._accepted(execution):
            if _registered(execution.principal, context, source)[3].physical_name != confirm_name:
                raise QueryError('CONFIRMATION_MISMATCH', 409)
            # Inherited physical destination claim includes unresolved UNKNOWN owners.
            execution._target_claim(source)
            guard, fingerprint = _measure(execution.principal, context, source)
            if fingerprint != expected or confirm_name != guard['name']:
                raise QueryError('PREVIEW_CHANGED', 409)
            result = dict(guard=guard, phase='PREPARED')
            with control_store.connect() as db:
                db.execute('UPDATE dataops_operations SET result=? WHERE id=? AND state=\'ACTIVE\'',
                    (ds._json(result), execution.id))
            schema, database, binding, obj = _registered(execution.principal, context, source)
            if (obj.physical_name != confirm_name or obj.backend_identity != guard['uuid']
                    or asdict(schema) != guard['schema']):
                raise QueryError('PREVIEW_CHANGED', 409)
            with inventory.table_drop_api(database, binding) as (reader, unregister):
                current, digest = _measure(execution.principal, context, source, reader, expected=guard)
                if current != guard or digest != expected or confirm_name != obj.physical_name:
                    raise QueryError('PREVIEW_CHANGED', 409)
                execution._active()
                _registered(execution.principal, context, source)
                result['phase'] = 'SENT'
                with control_store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    changed = db.execute('UPDATE dataops_operations SET result=? WHERE id=? AND state=\'ACTIVE\'',
                        (ds._json(result), execution.id)).rowcount
                    if changed != 1:
                        raise QueryError('OPERATION_OBSERVATION_ONLY', 409)
                sent = True
                ack = unregister(schema, obj.physical_name)
                if type(ack) is not int or ack != 204:
                    raise inventory.TableDropTransportError(True)
                result.update(phase='ACKNOWLEDGED', ack=204)
                _settle(execution, 'FINISHED', result, ack=True)
            # An ACK is proof of effect; subsequent absence is only observation.
            try:
                _access(execution.principal, context, source)
                with inventory.catalog_api(database, binding) as reader:
                    try:
                        reader(inventory._path(schema, 'TABLE', obj.physical_name))
                    except inventory.InventoryError as error:
                        if error.status != 404:
                            raise
                    else:
                        raise ValueError('registration still present or replaced')
                _access(execution.principal, context, source)
            except Exception:
                result.setdefault('warning', 'POST_ACK_OBSERVATION_UNAVAILABLE')
                with control_store.connect() as db:
                    db.execute('UPDATE dataops_operations SET result=? WHERE id=? AND committed=1',
                        (ds._json(result), execution.id))
    except Exception as error:
        if execution._row()['committed']:
            result.setdefault('warning', 'POST_ACK_OBSERVATION_UNAVAILABLE')
            with control_store.connect() as db:
                db.execute('UPDATE dataops_operations SET result=? WHERE id=? AND committed=1',
                    (ds._json(result), execution.id))
            return
        uncertain = sent and not (isinstance(error, inventory.TableDropTransportError) and not error.possible_send)
        result.pop('ack', None)
        result.update(phase='SENT' if uncertain else 'REJECTED', warning='OUTCOME_UNKNOWN' if uncertain else 'REMOVAL_NOT_SENT_OR_REJECTED')
        _settle(execution, 'UNKNOWN' if uncertain else 'FAILED', result)


def submit(principal, context, *, key, source, mode, expected_preview_fingerprint, confirm_name):
    if (type(source) is not registry.ResourceRef or mode != MODE or type(confirm_name) is not str
            or type(expected_preview_fingerprint) is not str
            or not re.fullmatch('[a-f0-9]{64}', expected_preview_fingerprint)):
        raise ValueError('exact nonpurging mode, preview digest and confirmation required')
    _access(principal, context, source)
    base = ds.begin(principal, context, route=ROUTE, idempotency_key=key, _inspect_replay=False,
        payload=dict(source=asdict(source), mode=mode, expected_preview_fingerprint=expected_preview_fingerprint,
            confirm_name=confirm_name))
    execution = DropExecution(principal, base.id, base.replayed)
    if not execution.replayed:
        try:
            _, _, _, obj = _registered(principal, context, source)
            if confirm_name != obj.physical_name:
                raise QueryError('CONFIRMATION_MISMATCH', 409)
            guard, digest = _measure(principal, context, source)
            if digest != expected_preview_fingerprint:
                raise QueryError('PREVIEW_CHANGED', 409)
            with control_store.connect() as db:
                db.execute('UPDATE dataops_operations SET result=? WHERE id=?',
                    (ds._json(dict(guard=guard, phase='PREPARED')), execution.id))
            threading.Thread(target=_work, args=(execution, context, source,
                expected_preview_fingerprint, confirm_name), daemon=True).start()
        except Exception as error:
            _settle(execution, 'FAILED', dict(phase='REJECTED'))
            if isinstance(error, (QueryError, inventory.InventoryError, PermissionError)):
                raise
            raise QueryError('SERVICE_UNAVAILABLE', 502) from None
    return inspect(execution, context)
