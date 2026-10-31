"""Inference over a copied, closed plan; never executes or refreshes its source."""
from copy import deepcopy
from datetime import datetime
import json
import re
import threading
import uuid

import control_store
import db_ai as ai
import db_ai_plan as plan
from query_context import QueryError

KIND = 'saved_plan_explanation'
_TERMINAL = ('SUCCEEDED', 'FAILED', 'CANCELED', 'UNKNOWN')


def _invalid():
    ai._fail('AI_SAVED_PLAN_UNAVAILABLE', 409)


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 64:
        _invalid()
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _invalid()
    return parsed


def _projection(value):
    if (not isinstance(value, dict) or set(value) != {'projection_version', 'fragments'}
            or type(value['projection_version']) is not int or value['projection_version'] != 1
            or not isinstance(value['fragments'], list) or not 1 <= len(value['fragments']) <= 64
            or len(ai._json(value).encode()) > 65536):
        _invalid()
    ids = ['f' + str(i) for i in range(len(value['fragments']))]
    count = 0
    def node(v, depth):
        nonlocal count
        count += 1
        if (count > 512 or depth > 32 or not isinstance(v, dict)
                or set(v) != {'operator', 'children', 'remote_sources', 'attributes'}
                or v['operator'] not in plan.OPERATORS or not isinstance(v['children'], list)
                or not isinstance(v['remote_sources'], list) or len(v['remote_sources']) > 64
                or any(s not in ids for s in v['remote_sources'])
                or v['remote_sources'] and v['operator'] != 'RemoteSource'
                or not isinstance(v['attributes'], dict)):
            _invalid()
        allowed = set()
        name = v['operator']
        if name == 'Aggregate':
            allowed.add('phase')
        if name in ('LocalExchange', 'Exchange'):
            allowed.add('partitioning')
        if name.endswith('Join'):
            allowed.add('join_type')
        if name in ('LocalExchange', 'Exchange', 'Join', 'RemoteSource') or name.endswith('Join'):
            allowed.add('distribution')
        for k, item in v['attributes'].items():
            if k not in allowed or not isinstance(item, str) or item not in plan._ENUMS[k]:
                _invalid()
        for child in v['children']:
            node(child, depth + 1)
    for ident, fragment in zip(ids, value['fragments']):
        if not isinstance(fragment, dict) or set(fragment) != {'id', 'root'} or fragment['id'] != ident:
            _invalid()
        node(fragment['root'], 1)


def _metadata(context, metadata, pins, refs):
    if (not isinstance(metadata, dict) or set(metadata) not in (
            {'captured_at', 'relations'}, {'captured_at', 'relations', 'view_dependencies'})
            or not isinstance(pins, list) or len(pins) > 64):
        _invalid()
    _timestamp(metadata['captured_at'])
    known = {}
    if any(not isinstance(p, dict) for p in pins):
        _invalid()
    has_views = any(p.get('kind') == 'VIEW' for p in pins)
    envelope = ({k: metadata[k] for k in ('relations', 'view_dependencies') if k in metadata}
        if has_views else metadata['relations'])
    if len(ai._json(envelope).encode()) > 65536:
        _invalid()
    direct, deps = [], []
    for p in pins:
        key = (p['schema'], p['table'])
        if key in known or not all(isinstance(s, str) and s for s in key):
            _invalid()
        known[key] = p
        if p['kind'] not in ('TABLE', 'VIEW') or not isinstance(p['schema_id'], str):
            _invalid()
        ref = p['ref']
        if (not isinstance(ref, dict) or set(ref) != {'database_id', 'schema_id', 'object_id',
                'incarnation', 'binding_revision', 'generation_id'}
                or ref['database_id'] != context.database_id or ref['schema_id'] != p['schema_id']
                or ref['binding_revision'] != context.binding_revision or ref['generation_id'] != context.generation_id
                or not isinstance(ref['object_id'], str) or type(ref['incarnation']) is not int
                or ref['incarnation'] < 1 or type(p['schema_revision']) is not int):
            _invalid()
        allowed = {'schema_id', 'schema', 'schema_revision', 'table', 'ref', 'columns', 'kind'}
        if has_views:
            allowed.add('direct')
        if p['kind'] == 'VIEW':
            allowed.update(('view_uuid', 'view_version', 'view_schema_id', 'security', 'defaults_digest',
                'definition_digest', 'canonical_definition_digest', 'head_digest', 'dependencies'))
        if p['kind'] == 'TABLE':
            allowed.add('snapshots')
        if set(p) - allowed:
            _invalid()
        columns = p['columns']
        if not isinstance(columns, list):
            _invalid()
        for c in columns:
            if (not isinstance(c, dict) or set(c) != {'name', 'type'}
                    or not isinstance(c['name'], str) or not c['name'] or not isinstance(c['type'], str)):
                _invalid()
            typ = c['type']
            if typ.startswith('{'):
                if ai._type(json.loads(typ)) != typ:
                    _invalid()
        public = dict(schema=p['schema'], table=p['table'], kind=p['kind'], columns=columns)
        if has_views:
            if type(p.get('direct')) is not bool:
                _invalid()
            (direct if p['direct'] else deps).append(public)
        else:
            direct.append(public)
        if 'snapshots' in p:
            if p['kind'] != 'TABLE' or not isinstance(p['snapshots'], list) or not p['snapshots']:
                _invalid()
            seen = set()
            for snap in p['snapshots']:
                if (set(snap) != {'snapshot_id', 'schema_fingerprint'} or type(snap['snapshot_id']) is not int
                        or not 1 <= snap['snapshot_id'] <= 2**63-1 or snap['snapshot_id'] in seen
                        or not re.fullmatch('[0-9a-f]{64}', snap['schema_fingerprint'])):
                    _invalid()
                seen.add(snap['snapshot_id'])
    if (metadata['relations'] != direct or (has_views and metadata.get('view_dependencies') != deps)
            or (not has_views and 'view_dependencies' in metadata)
            or {(r['schema'], r['table']) for r in direct} != set(refs)):
        _invalid()
    visited, visiting, heights = set(), set(), {}
    def visit(key, depth):
        if depth > 17 or key in visiting or key not in known:
            _invalid()
        if key in visited:
            if depth + heights[key] - 1 > 16:
                _invalid()
            return heights[key]
        p = known[key]
        visiting.add(key)
        height = 0
        if p['kind'] == 'VIEW':
            if depth > 16 or p.get('security') != 'INVOKER':
                _invalid()
            uuid.UUID(p['view_uuid'])
            if type(p['view_version']) is not int or p['view_version'] < 1 or type(p['view_schema_id']) is not int:
                _invalid()
            for k in ('defaults_digest', 'definition_digest', 'canonical_definition_digest', 'head_digest'):
                if not re.fullmatch('[0-9a-f]{64}', p[k]):
                    _invalid()
            if not isinstance(p['dependencies'], list) or len(p['dependencies']) > 64:
                _invalid()
            height = 1
            for child in p['dependencies']:
                if not isinstance(child, list) or len(child) != 2:
                    _invalid()
                height = max(height, 1 + visit(tuple(child), depth + 1))
        visiting.remove(key)
        heights[key] = height
        visited.add(key)
        return height
    for key in refs:
        visit(key, 1)
    if visited != set(known):
        _invalid()


def _source(db, principal, context, ident):
    _, source, private = plan._owned(principal, context, ident, db)
    if source['state'] not in _TERMINAL or source['plan_status'] != 'COLLECTED':
        _invalid()
    try:
        if source['context'] != context.public_metadata() or source['actor'] != principal.principal_id:
            ai._fail('AI_EXPLANATION_NOT_FOUND', 404)
        saved = source['plan']
        if saved['query_state'] != 'FINISHED' or saved['format'] != 'TRINO_DISTRIBUTED_JSON':
            _invalid()
        sql = source['input']['sql']
        if not isinstance(sql, str) or not sql.strip() or len(sql.encode()) > 32768:
            _invalid()
        refs, prompt = ai._parse(sql, context)
        pins = private['relations']
        _projection(saved['operators'])
        _metadata(context, source['metadata'], pins, refs)
        captured = _timestamp(saved['captured_at'])
        if not (_timestamp(source['created_at']) <= _timestamp(source['metadata']['captured_at'])
                <= captured <= _timestamp(source['updated_at'])):
            _invalid()
        child = plan._linked_query(principal, context, source, db)
        if child is None or child['state'] != 'FINISHED':
            _invalid()
        ai._transaction_authority(db, principal, context, pins)
        snapshot = dict(explanation_id=ident, query_id=saved['query_id'], state=source['state'],
            query_state='FINISHED', format=saved['format'], captured_at=saved['captured_at'],
            sql=sql, operators=saved['operators'])
        return deepcopy(pins), deepcopy(source['metadata']), deepcopy(snapshot), prompt
    except QueryError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, UnicodeError):
        _invalid()


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


def _settle(principal, context, ident, state, error=None, text=None):
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        record, pins = _owned(db, principal, context, ident)
        if record['state'] not in ai._ACTIVE or record['cancel_requested']:
            return
        if state == 'SUCCEEDED':
            ai._transaction_authority(db, principal, context, pins)
        record.update(state=state, inference_state=state, text=text, error_code=error)
        _save(db, record)


def _run(principal, context, ident, config, prompt, owner):
    try:
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            record, pins = _owned(db, principal, context, ident)
            if record['state'] not in ai._ACTIVE or record['cancel_requested']:
                return
            ai._transaction_authority(db, principal, context, pins)
            record.update(state='RUNNING', inference_state='RUNNING')
            _save(db, record)
        ai._authority(principal, context, pins)
        metadata = record['metadata']
        extra = ({'view_dependencies': metadata['view_dependencies']} if 'view_dependencies' in metadata else {})
        text = ai._complete(config, prompt, metadata['relations'], record['input']['language'],
            plan=record['source_snapshot']['operators'], **extra)
        ai._authority(principal, context, pins)
        _settle(principal, context, ident, 'SUCCEEDED', text=text)
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
    ai._authority(principal, context, pins)
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
    ai._authority(principal, context, pins)
    return deepcopy(record)


def history(principal, context):
    ai._authority(principal, context)
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
        ai._authority(principal, context, pins)
    ai._authority(principal, context)
    return records


def submit(principal, context, *, key, source_explanation_id, language):
    try:
        if not isinstance(source_explanation_id, str) or str(uuid.UUID(source_explanation_id)) != source_explanation_id:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        ai._fail('AI_INPUT_INVALID')
    if language not in ('ja', 'en') or not isinstance(key, str) or not key.strip() or len(key) > 256:
        ai._fail('AI_INPUT_INVALID')
    payload = ai._json(dict(context=context.public_metadata(), source_explanation_id=source_explanation_id, language=language))
    def replay(row):
        if json.loads(row['record']).get('kind') != KIND or row['payload'] != payload:
            ai._fail('IDEMPOTENCY_CONFLICT', 409)
        return get(principal, context, row['id'])
    with control_store.connect() as db:
        row = db.execute('SELECT * FROM ai_explanations WHERE actor=? AND idempotency_key=?',
            (principal.principal_id, key)).fetchone()
    if row:
        return replay(row)
    ident, now = str(uuid.uuid4()), ai._now()
    owner = ai._owner(ident)
    try:
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM ai_explanations WHERE actor=? AND idempotency_key=?',
                (principal.principal_id, key)).fetchone()
            if not row:
                pins, metadata, snapshot, prompt = _source(db, principal, context, source_explanation_id)
                config = ai._config()
                record = dict(id=ident, kind=KIND, state='QUEUED', actor=principal.principal_id,
                    context=context.public_metadata(), input=dict(source_explanation_id=source_explanation_id, language=language),
                    model=dict(provider='local-openai', name=config['model']), metadata=metadata,
                    source_snapshot=snapshot, text=None, error_code=None, created_at=now, updated_at=now,
                    sql_status='NOT_EXECUTED', cancel_requested=False, inference_state='NOT_STARTED')
                db.execute('INSERT INTO ai_explanations VALUES (?,?,?,?,?,?,?)', (ident, principal.principal_id,
                    key, payload, ai._json(context.public_metadata()), ai._json(pins), ai._json(record)))
        if row:
            owner.close()
            return replay(row)
        threading.Thread(target=_run, args=(principal, context, ident, config, prompt, owner),
            daemon=True, name='ai-saved-plan-' + ident).start()
    except BaseException:
        owner.close()
        raise
    return get(principal, context, ident)


def cancel(principal, context, ident):
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        record, pins = _owned(db, principal, context, ident)
        ai._transaction_authority(db, principal, context, pins)
        record['cancel_requested'] = True
        if record['state'] in ai._ACTIVE:
            record.update(state='CANCELED', inference_state='CANCELED', text=None)
        _save(db, record)
    return get(principal, context, ident)
