"""Actor-owned, metadata-only SQL drafts. Never submits SQL for execution."""
from copy import deepcopy
import json
import logging
import threading
import uuid

import control_store
import data_management as registry
import db_ai as ai
from query_context import QueryError

KIND = 'sql_proposal'
_REF_KEYS = {'database_id', 'schema_id', 'object_id', 'incarnation', 'binding_revision', 'generation_id'}


def _authority(principal, context, pins=()):
    ai._authority(principal, context, pins)
    with control_store.connect() as db:
        ai._transaction_authority(db, principal, context, pins)


def _refs(relations):
    if not isinstance(relations, list) or not 1 <= len(relations) <= 8:
        ai._fail('AI_INPUT_INVALID')
    seen = set()
    for ref in relations:
        if (not isinstance(ref, dict) or set(ref) != _REF_KEYS
                or any(not isinstance(ref[k], str) or not ref[k] for k in
                    ('database_id', 'schema_id', 'object_id', 'generation_id'))
                or any(type(ref[k]) is not int or ref[k] < 1 for k in ('incarnation', 'binding_revision'))
                or ref['object_id'] in seen):
            ai._fail('AI_INPUT_INVALID')
        seen.add(ref['object_id'])
        try:
            for field in ('database_id', 'schema_id', 'object_id', 'generation_id'):
                ref[field].encode('utf-8')
        except UnicodeEncodeError:
            ai._fail('AI_INPUT_INVALID')


def _local(db, principal, context, relations):
    # Validate the entire frontier before the first catalog REST request.
    ai._transaction_authority(db, principal, context, ())
    pins = []
    for ref in relations:
        if (ref['database_id'] != context.database_id
                or ref['binding_revision'] != context.binding_revision
                or ref['generation_id'] != context.generation_id):
            ai._fail('AI_METADATA_CHANGED', 409)
        schema = registry.get_schema(db, ref['schema_id'])
        obj = registry.get_object(db, ref['object_id'])
        if (schema is None or schema.database_id != context.database_id or obj is None
                or obj.schema_id != schema.id or obj.incarnation != ref['incarnation']
                or obj.kind not in ('TABLE', 'VIEW')):
            ai._fail('AI_METADATA_CHANGED', 409)
        pins.append(dict(schema_id=schema.id, schema=schema.sql_name, schema_revision=schema.revision,
            table=obj.physical_name, kind=obj.kind, ref=deepcopy(ref)))
    ai._schema_authority(db, context, pins)
    return pins


def _admission(db, principal, context, relations, pins):
    current = _local(db, principal, context, relations)
    if sorted(current, key=lambda p: p['ref']['object_id']) != sorted(
            [{k: p[k] for k in ('schema_id', 'schema', 'schema_revision', 'table', 'kind', 'ref')} for p in pins], key=lambda p: p['ref']['object_id']):
        ai._fail('AI_METADATA_CHANGED', 409)


def _capture(principal, context, relations):
    _authority(principal, context)
    with control_store.connect() as db:
        local = _local(db, principal, context, relations)
    # Names come exclusively from current owned registry objects, never user SQL.
    names = ['.'.join(registry.quote_identifier(n) for n in
        (context.sql_catalog, p['schema'], p['table'])) + ' AS p' + str(i) for i, p in enumerate(local)]
    synthetic = 'SELECT * FROM ' + ' CROSS JOIN '.join(names)
    pins, public, _ = ai._capture(principal, context, synthetic)
    if (len(pins) != len(relations) or sorted((ai._json(p['ref']) for p in pins)) !=
            sorted(ai._json(r) for r in relations)):
        ai._fail('AI_METADATA_CHANGED', 409)
    with control_store.connect() as db:
        _admission(db, principal, context, relations, pins)
    return pins, public


def _refresh(principal, context, relations, pins):
    fresh, _ = _capture(principal, context, relations)
    if fresh != pins:
        ai._fail('AI_METADATA_CHANGED', 409)


def _output(output, context, pins):
    # No candidate, provider content or parser excerpt escapes on rejection.
    thread_id = threading.get_ident()
    class QuietDiagnostics(logging.Filter):
        def filter(self, record):
            return record.thread != thread_id
    logger, quiet = logging.getLogger('sqlglot'), QuietDiagnostics()
    logger.addFilter(quiet)
    try:
        def pairs(items):
            value = {}
            for k, v in items:
                if k in value:
                    raise ValueError()
                value[k] = v
            return value
        value = json.loads(output, object_pairs_hook=pairs)
        if not isinstance(value, dict) or set(value) != {'sql', 'explanation'}:
            raise ValueError()
        for key, limit in (('sql', 16384), ('explanation', 4096)):
            if not isinstance(value[key], str) or not value[key].strip() or len(value[key].encode()) > limit:
                raise ValueError()
        import sqlglot
        from sqlglot import exp
        from sqlglot.dialects.trino import Trino
        class QuietParser(Trino.Parser):
            def _warn_unsupported(self):
                raise ValueError()
        sql = value['sql']
        trees = QuietParser(error_level=sqlglot.ErrorLevel.RAISE, error_message_context=0).parse(
            Trino().tokenize(sql), sql)
        if (len(trees) != 1 or not isinstance(trees[0], (exp.Select, exp.SetOperation))
                or any(isinstance(n, exp.Command) for n in trees[0].walk())):
            raise ValueError()
        refs, _ = ai._parse(sql, context)
        if not set(refs).issubset({(p['schema'], p['table']) for p in pins}):
            raise ValueError()
        import warehouse_resident
        warehouse_resident.check_sql_policy(sql, 'read-only', context.selected_role,
            context=context, database_only=True)
        return sql, value['explanation']
    except Exception:
        ai._fail('AI_OUTPUT_INVALID', 502)
    finally:
        logger.removeFilter(quiet)


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


def _settle(principal, context, ident, state, error=None, text=None, sql=None):
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        record, pins = _owned(db, principal, context, ident)
        if record['state'] not in ai._ACTIVE or record['cancel_requested']:
            return
        if state == 'SUCCEEDED':
            _admission(db, principal, context, record['input']['relations'], pins)
        record.update(state=state, inference_state=state, text=text, sql=sql, error_code=error)
        _save(db, record)


def _run(principal, context, ident, config, owner):
    try:
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            record, pins = _owned(db, principal, context, ident)
            if record['state'] not in ai._ACTIVE or record['cancel_requested']:
                return
            _admission(db, principal, context, record['input']['relations'], pins)
            record.update(state='RUNNING', inference_state='RUNNING')
            _save(db, record)
        _refresh(principal, context, record['input']['relations'], pins)
        # Check cancellation again after catalog I/O, before the single inference.
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            record, pins = _owned(db, principal, context, ident)
            if record['state'] not in ai._ACTIVE or record['cancel_requested']:
                return
            _admission(db, principal, context, record['input']['relations'], pins)
        output = ai._complete(config, None, record['metadata']['relations'], record['input']['language'],
            proposal_request=record['input']['request'])
        sql, text = _output(output, context, pins)
        _refresh(principal, context, record['input']['relations'], pins)
        _settle(principal, context, ident, 'SUCCEEDED', text=text, sql=sql)
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


def submit(principal, context, *, key, request, relations, language):
    if (not isinstance(request, str) or not request.strip()
            or language not in ('ja', 'en') or not isinstance(key, str) or not key.strip() or len(key) > 256):
        ai._fail('AI_INPUT_INVALID')
    try:
        if len(request.encode('utf-8')) > 4096:
            ai._fail('AI_INPUT_INVALID')
    except UnicodeEncodeError:
        ai._fail('AI_INPUT_INVALID')
    _refs(relations)
    relations = deepcopy(relations)
    payload = ai._json(dict(context=context.public_metadata(), request=request, relations=relations, language=language))
    def replay(row):
        if json.loads(row['record']).get('kind') != KIND or row['payload'] != payload:
            ai._fail('IDEMPOTENCY_CONFLICT', 409)
        return get(principal, context, row['id'])
    with control_store.connect() as db:
        row = db.execute('SELECT * FROM ai_explanations WHERE actor=? AND idempotency_key=?',
            (principal.principal_id, key)).fetchone()
    if row:
        return replay(row)
    pins, public = _capture(principal, context, relations)
    config = ai._config()
    ident, now = str(uuid.uuid4()), ai._now()
    owner = ai._owner(ident)
    try:
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM ai_explanations WHERE actor=? AND idempotency_key=?',
                (principal.principal_id, key)).fetchone()
            if not row:
                _admission(db, principal, context, relations, pins)
                record = dict(id=ident, kind=KIND, state='QUEUED', actor=principal.principal_id,
                    context=context.public_metadata(), input=dict(request=request, relations=relations, language=language),
                    model=dict(provider='local-openai', name=config['model']),
                    metadata=dict(relations=public, captured_at=now), sql=None, text=None,
                    error_code=None, created_at=now, updated_at=now, sql_status='NOT_EXECUTED',
                    cancel_requested=False, inference_state='NOT_STARTED')
                db.execute('INSERT INTO ai_explanations VALUES (?,?,?,?,?,?,?)', (ident, principal.principal_id,
                    key, payload, ai._json(context.public_metadata()), ai._json(pins), ai._json(record)))
        if row:
            owner.close()
            return replay(row)
        threading.Thread(target=_run, args=(principal, context, ident, config, owner),
            daemon=True, name='ai-sql-proposal-' + ident).start()
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
            record.update(state='CANCELED', inference_state='CANCELED', text=None, sql=None)
        _save(db, record)
    return get(principal, context, ident)
