"""Actor-owned metadata explanations and description drafts; never applies changes."""
from copy import deepcopy
import json
import threading
import uuid

import control_store
import data_management as registry
import database_inventory as inventory
import db_ai as ai
import db_ai_sql_proposal as b1
from query_context import QueryError

KIND = 'catalog_proposal'


def _string(value, limit=None):
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise ValueError()
    size = len(value.encode('utf-8'))
    if limit is not None and size > limit:
        raise ValueError()
    return value


def _authority(principal, context, pins=()):
    b1._authority(principal, context, pins)


def _local(db, principal, context, relation):
    if relation['schema_id'] != context.schema_id:
        ai._fail('AI_METADATA_CHANGED', 409)
    pins = b1._local(db, principal, context, [relation])
    obj = registry.get_object(db, relation['object_id'])
    try:
        identity = _string(obj.backend_identity)
    except (ValueError, UnicodeError):
        ai._fail('AI_METADATA_UNAVAILABLE', 502)
    pins[0]['backend_identity'] = identity
    return pins


def _admission(db, principal, context, relation, pins):
    current = _local(db, principal, context, relation)
    if current != [{k: p[k] for k in current[0]} for p in pins]:
        ai._fail('AI_METADATA_CHANGED', 409)


def _capture(principal, context, relation):
    _authority(principal, context)
    with control_store.connect() as db:
        pins = _local(db, principal, context, relation)
        schema = registry.get_schema(db, context.schema_id)
        database = registry.get_database(db, context.database_id)
        binding = registry.get_binding(db, context.database_id)
    pin = pins[0]
    try:
        with inventory.catalog_api(database, binding) as api:
            metadata = inventory._metadata(api, schema, pin['kind'], pin['table'])
    except Exception:
        ai._fail('AI_METADATA_UNAVAILABLE', 502)
    # Authority and local UUID must still match after catalog I/O.
    with control_store.connect() as db:
        _admission(db, principal, context, relation, pins)
    try:
        identity = _string(metadata.get('table-uuid' if pin['kind'] == 'TABLE' else 'view-uuid'))
        if identity != pin['backend_identity']:
            ai._fail('AI_METADATA_CHANGED', 409)
        schema_id = metadata.get('current-schema-id')
        if pin['kind'] == 'VIEW':
            version_id = metadata.get('current-version-id')
            if type(version_id) is not int or version_id < 1:
                raise ValueError()
            versions = metadata['versions']
            if not isinstance(versions, list):
                raise ValueError()
            selected = [v for v in versions if isinstance(v, dict) and type(v.get('version-id')) is int and v['version-id'] == version_id]
            if len(selected) != 1:
                raise ValueError()
            schema_id = selected[0].get('schema-id')
        if type(schema_id) is not int or schema_id < 0:
            raise ValueError()
        schemas = metadata['schemas']
        if not isinstance(schemas, list):
            raise ValueError()
        selected = [s for s in schemas if isinstance(s, dict) and type(s.get('schema-id')) is int and s['schema-id'] == schema_id]
        if len(selected) != 1 or not isinstance(selected[0].get('fields'), list):
            raise ValueError()
        columns, seen = [], set()
        for field in selected[0]['fields']:
            name = _string(field['name'])
            if name in seen:
                raise ValueError()
            seen.add(name)
            normalized = _string(ai._type(field['type']))
            def type_strings(item):
                if isinstance(item, str):
                    _string(item)
                elif isinstance(item, dict):
                    for value in item.values():
                        type_strings(value)
                elif isinstance(item, list):
                    for value in item:
                        type_strings(value)
            type_strings(json.loads(normalized) if isinstance(field['type'], dict) else normalized)
            columns.append(dict(name=name, type=normalized))
        public = dict(schema=_string(pin['schema']), table=_string(pin['table']), kind=pin['kind'], columns=columns)
        encoded = ai._json(public)
        # Also validate nested type names/strings after projection.
        if len(encoded.encode('utf-8')) > 49152:
            raise ValueError()
    except QueryError:
        raise
    except Exception:
        ai._fail('AI_METADATA_UNAVAILABLE', 502)
    pin['columns'] = columns
    return pins, public


def _refresh(principal, context, relation, pins):
    fresh, _ = _capture(principal, context, relation)
    if fresh != pins:
        ai._fail('AI_METADATA_CHANGED', 409)


def _output(output, relation):
    try:
        _string(output, 16384)
        def pairs(items):
            value = {}
            for key, item in items:
                if key in value:
                    raise ValueError()
                value[key] = item
            return value
        value = json.loads(output, object_pairs_hook=pairs)
        if not isinstance(value, dict) or set(value) != {'summary', 'description', 'columns'}:
            raise ValueError()
        _string(value['summary'], 4096)
        _string(value['description'], 4096)
        columns = value['columns']
        if not isinstance(columns, list) or len(columns) > 8:
            raise ValueError()
        names = [c['name'] for c in relation['columns']]
        seen = set()
        for column in columns:
            if not isinstance(column, dict) or set(column) != {'name', 'description'}:
                raise ValueError()
            name = _string(column['name'])
            if name not in names or name in seen:
                raise ValueError()
            seen.add(name)
            _string(column['description'], 1024)
        columns.sort(key=lambda c: names.index(c['name']))
        if len(ai._json(value).encode('utf-8')) > 16384:
            raise ValueError()
        return value
    except Exception:
        ai._fail('AI_OUTPUT_INVALID', 502)


def _owned(db, principal, context, ident):
    row = db.execute('SELECT * FROM ai_explanations WHERE id=?', (ident,)).fetchone()
    if (row is None or row['actor'] != principal.principal_id
            or json.loads(row['context']) != context.public_metadata()
            or json.loads(row['record']).get('kind') != KIND):
        ai._fail('AI_EXPLANATION_NOT_FOUND', 404)
    return json.loads(row['record']), json.loads(row['pins'])


def _save(db, record):
    record['updated_at'] = ai._now()
    db.execute('UPDATE ai_explanations SET record=? WHERE id=?', (ai._json(record), record['id']))


def _settle(principal, context, ident, state, error=None, proposal=None):
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        record, pins = _owned(db, principal, context, ident)
        if record['state'] not in ai._ACTIVE or record['cancel_requested']:
            return
        if state == 'SUCCEEDED':
            _admission(db, principal, context, record['input']['relation'], pins)
        record.update(state=state, inference_state=state, proposal=proposal, error_code=error)
        _save(db, record)


def _run(principal, context, ident, config, owner):
    try:
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            record, pins = _owned(db, principal, context, ident)
            if record['state'] not in ai._ACTIVE or record['cancel_requested']:
                return
            _admission(db, principal, context, record['input']['relation'], pins)
            record.update(state='RUNNING', inference_state='RUNNING')
            _save(db, record)
        _refresh(principal, context, record['input']['relation'], pins)
        # Check cancellation again after catalog I/O, before the single inference.
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            record, pins = _owned(db, principal, context, ident)
            if record['state'] not in ai._ACTIVE or record['cancel_requested']:
                return
            _admission(db, principal, context, record['input']['relation'], pins)
        output = ai._complete(config, None, [record['metadata']['relation']], record['input']['language'],
            catalog_proposal=True)
        result = _output(output, record['metadata']['relation'])
        _refresh(principal, context, record['input']['relation'], pins)
        _settle(principal, context, ident, 'SUCCEEDED', proposal=result)
    except QueryError as error:
        unknown = error.code in ('AI_PROVIDER_TIMEOUT', 'AI_PROVIDER_OUTCOME_UNKNOWN')
        _settle(principal, context, ident, 'UNKNOWN' if unknown else 'FAILED', error.code)
    except Exception:
        _settle(principal, context, ident, 'UNKNOWN', 'AI_PROVIDER_OUTCOME_UNKNOWN')
    finally:
        owner.close()


def get(principal, context, ident):
    with control_store.connect() as db:
        record, pins = _owned(db, principal, context, ident)
    _authority(principal, context, pins)
    if record['state'] in ai._ACTIVE:
        try:
            owner = ai._owner(ident)
        except BlockingIOError:
            pass
        else:
            try:
                _settle(principal, context, ident, 'UNKNOWN', 'AI_OWNER_LOST')
            finally:
                owner.close()
        with control_store.connect() as db:
            record, pins = _owned(db, principal, context, ident)
    _authority(principal, context, pins)
    return deepcopy(record)


def history(principal, context):
    _authority(principal, context)
    with control_store.connect() as db:
        ids = [r[0] for r in db.execute("""SELECT id FROM ai_explanations WHERE actor=? AND context=?
            AND json_extract(record,'$.kind')=? ORDER BY json_extract(record,'$.created_at') DESC,id DESC""",
            (principal.principal_id, ai._json(context.public_metadata()), KIND))]
    records = []
    for ident in ids:
        try:
            records.append(get(principal, context, ident))
        except QueryError:
            continue
        if len(records) == 50:
            break
    for record in records:
        with control_store.connect() as db:
            _, pins = _owned(db, principal, context, record['id'])
        _authority(principal, context, pins)
    _authority(principal, context)
    return records


def submit(principal, context, *, key, relation, language):
    if (language not in ('ja', 'en') or not isinstance(key, str) or not key.strip() or len(key) > 256):
        ai._fail('AI_INPUT_INVALID')
    b1._refs([relation])
    try:
        _string(key)
        for value in relation.values():
            if isinstance(value, str):
                _string(value)
    except (ValueError, UnicodeError):
        ai._fail('AI_INPUT_INVALID')
    relation = deepcopy(relation)
    payload = ai._json(dict(context=context.public_metadata(), relation=relation, language=language))
    def replay(row):
        if json.loads(row['record']).get('kind') != KIND or row['payload'] != payload:
            ai._fail('IDEMPOTENCY_CONFLICT', 409)
        return get(principal, context, row['id'])
    with control_store.connect() as db:
        row = db.execute('SELECT * FROM ai_explanations WHERE actor=? AND idempotency_key=?',
            (principal.principal_id, key)).fetchone()
    if row:
        return replay(row)
    pins, public = _capture(principal, context, relation)
    config = ai._config()
    ident, now = str(uuid.uuid4()), ai._now()
    owner = ai._owner(ident)
    try:
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM ai_explanations WHERE actor=? AND idempotency_key=?',
                (principal.principal_id, key)).fetchone()
            if not row:
                _admission(db, principal, context, relation, pins)
                record = dict(id=ident, kind=KIND, state='QUEUED', actor=principal.principal_id,
                    context=context.public_metadata(), input=dict(relation=relation, language=language),
                    model=dict(provider='local-openai', name=config['model']),
                    metadata=dict(relation=public, captured_at=now), proposal=None,
                    error_code=None, created_at=now, updated_at=now,
                    cancel_requested=False, inference_state='NOT_STARTED')
                db.execute('INSERT INTO ai_explanations VALUES (?,?,?,?,?,?,?)', (ident, principal.principal_id,
                    key, payload, ai._json(context.public_metadata()), ai._json(pins), ai._json(record)))
        if row:
            owner.close()
            return replay(row)
        threading.Thread(target=_run, args=(principal, context, ident, config, owner),
            daemon=True, name='ai-catalog-proposal-' + ident).start()
    except BaseException:
        owner.close()
        raise
    return get(principal, context, ident)


def cancel(principal, context, ident):
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        record, pins = _owned(db, principal, context, ident)
        ai._transaction_authority(db, principal, context, pins)
        if record['state'] in ai._ACTIVE:
            record['cancel_requested'] = True
            record.update(state='CANCELED', inference_state='CANCELED', proposal=None)
            _save(db, record)
    return get(principal, context, ident)
