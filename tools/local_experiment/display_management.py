"""Atomic display metadata edits; physical identities and schema purposes are immutable."""
import json
import unicodedata
import uuid
from contextlib import nullcontext

import control_store
import data_management as dm
from warehouse_operations import OperationError, _json, _text


def validate_payload(resource_type, payload):
    fields = {'display_name', 'expected_revision'}
    if resource_type == 'database':
        fields.add('description')
    if resource_type not in ('database', 'schema') or type(payload) is not dict or set(payload) != fields:
        raise OperationError('INVALID_DISPLAY_METADATA', 400)
    name = payload['display_name']
    revision = payload['expected_revision']
    if (not isinstance(name, str) or not 1 <= len(name) <= 128 or not name.strip()
            or any(unicodedata.category(c) == 'Cc' for c in name)
            or type(revision) is not int or revision < 1):
        raise OperationError('INVALID_DISPLAY_METADATA', 400)
    if resource_type == 'database':
        description = payload['description']
        if (not isinstance(description, str) or len(description) > 2048 or
                any(unicodedata.category(c) == 'Cc' and c not in '\r\n\t' for c in description)):
            raise OperationError('INVALID_DISPLAY_METADATA', 400)
    try:
        name.encode('utf-8')
        if resource_type == 'database':
            description.encode('utf-8')
    except UnicodeError:
        raise OperationError('INVALID_DISPLAY_METADATA', 400) from None


def list_databases(principal, role):
    from catalog_admin import require_admin
    require_admin(principal, role)
    with control_store.connect() as db:
        result = []
        for row in db.execute('SELECT id,sql_catalog,display_name,description,state,revision FROM databases ORDER BY sql_catalog,id'):
            item = dict(row)
            item['schemas'] = [dict(s) for s in db.execute('''SELECT id,sql_name,display_name,purpose,revision
                FROM schemas WHERE database_id=? ORDER BY sql_name,id''', (row['id'],))]
            binding = db.execute('''SELECT database_id,polaris_service_ref,polaris_realm,polaris_catalog,
                desired_revision,applied_revision,applied_generation_id,revision
                FROM catalog_bindings WHERE database_id=?''', (row['id'],)).fetchone()
            item['binding'] = dict(binding) if binding else None
            result.append(item)
        return {'databases': result}


def inspect(db, operation_id):
    row = db.execute('''SELECT o.id,o.actor,o.action,o.state,r.response,a.* FROM operations o
        JOIN metadata_requests r ON r.operation_id=o.id
        JOIN metadata_audit a ON a.operation_id=o.id WHERE o.id=?''', (operation_id,)).fetchone()
    if row is None:
        raise OperationError('OPERATION_NOT_FOUND', 404)
    return dict(id=row['id'], actor=row['actor'], action=row['action'], state=row['state'],
        result=json.loads(row['response']), audit=dict(resource_type=row['resource_type'],
        resource_id=row['resource_id'], before=json.loads(row['before_metadata']),
        after=json.loads(row['after_metadata'])))


def mutate(principal, role, resource_type, resource_id, payload, key):
    return _mutate(principal, role, resource_type, resource_id, payload, key)


def _mutate(principal, role, resource_type, resource_id, payload, key, *, connection=None):
    """Caller-supplied connections keep creation registration in one transaction."""
    from catalog_admin import require_admin
    from warehouse_admission import _now
    require_admin(principal, role)
    validate_payload(resource_type, payload)
    if not _text(resource_id) or not _text(key) or not key.strip():
        raise OperationError('INVALID_DISPLAY_METADATA', 400)
    route = '/api/v1/' + ('databases' if resource_type == 'database' else 'schemas') + '/' + resource_id + '/display'
    actor, normalized = principal.principal_id, _json(payload)
    with (nullcontext(connection) if connection is not None else control_store.connect()) as db:
        if connection is None:
            db.execute('BEGIN IMMEDIATE')
        saved = db.execute('SELECT * FROM metadata_requests WHERE actor=? AND route=? AND idempotency_key=?',
            (actor, route, key)).fetchone()
        if saved:
            if saved['payload'] != normalized:
                raise OperationError('IDEMPOTENCY_CONFLICT')
            return json.loads(saved['response'])
        before = (dm.get_database if resource_type == 'database' else dm.get_schema)(db, resource_id)
        if before is None:
            raise OperationError(resource_type.upper() + '_NOT_FOUND', 404)
        if before.revision != payload['expected_revision']:
            raise OperationError('REVISION_CONFLICT')
        if resource_type == 'database':
            after = dm.update_database_display(db, resource_id, before.revision, payload['display_name'], payload['description'])
        else:
            after = dm.update_schema_display(db, resource_id, before.revision, payload['display_name'])
        operation_id = str(uuid.uuid4())
        response = dict(operation_id=operation_id, state='APPLIED', resource_type=resource_type,
            resource_id=resource_id, revision=after.revision)
        dm.record_operation(db, dm.Operation(operation_id, actor, 'DISPLAY_METADATA_CHANGE', (), before.revision,
            'APPLIED', (), None, (), (('created_at', _now()),)))
        db.execute('INSERT INTO metadata_requests VALUES (?,?,?,?,?,?)',
            (operation_id, actor, route, key, normalized, _json(response)))
        fields = ('display_name', 'description', 'revision') if resource_type == 'database' else ('display_name', 'revision')
        db.execute('INSERT INTO metadata_audit VALUES (?,?,?,?,?)', (operation_id, resource_type, resource_id,
            _json({k: getattr(before, k) for k in fields}), _json({k: getattr(after, k) for k in fields})))
        return response
