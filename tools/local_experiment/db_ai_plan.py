"""Captured non-ANALYZE plans, with private acquisition and allowlisted topology."""
from copy import deepcopy
from dataclasses import dataclass, field
import json
import re
import threading
import time
import uuid

import control_store
import db_ai as ai
from query_context import QueryError

KIND = 'sql_plan_explanation'
OPERATORS = frozenset(('OtherOperator Output Project Filter TableScan ScanFilterProject Values Aggregate '
    'LocalExchange RemoteSource Join InnerJoin LeftJoin RightJoin FullJoin CrossJoin SemiJoin SpatialJoin '
    'IndexJoin Sort TopN TopNRanking Window RowNumber Limit DistinctLimit MarkDistinct Union Intersect '
    'Except EnforceSingleRow AssignUniqueId GroupId Offset Sample Exchange DynamicFilterSource '
    'PatternRecognition').split())
_ENUMS = dict(phase={'PARTIAL', 'INTERMEDIATE', 'FINAL', 'SINGLE'},
    partitioning={'HASH', 'SINGLE', 'ROUND_ROBIN', 'ARBITRARY', 'BROADCAST', 'REPLICATED'},
    join_type={'INNER', 'LEFT', 'RIGHT', 'FULL', 'CROSS', 'SEMI'},
    distribution={'PARTITIONED', 'REPLICATED', 'BROADCAST', 'GATHER', 'REPARTITION', 'REPLICATE'})
MAX_CELL = 256 * 1024
MAX_RESPONSE = 1024 * 1024
MAX_PAGES = 128
DEADLINE = 120


def project(raw):
    """Project topology only. No raw descriptor values survive this boundary."""
    if not isinstance(raw, str):
        ai._fail('AI_PLAN_INVALID', 502)
    try:
        size = len(raw.encode())
    except UnicodeEncodeError:
        ai._fail('AI_PLAN_INVALID', 502)
    if size > MAX_CELL:
        ai._fail('AI_PLAN_TOO_LARGE', 502)
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    try:
        fragments = json.loads(raw, object_pairs_hook=pairs)
        if not isinstance(fragments, dict) or not fragments:
            raise ValueError()
        if len(fragments) > 64:
            ai._fail('AI_PLAN_TOO_LARGE', 502)
        if any(not re.fullmatch(r'0|[1-9][0-9]{0,11}', key) for key in fragments):
            raise ValueError()
        keys = sorted(fragments, key=int)
        ids = {key: 'f' + str(i) for i, key in enumerate(keys)}
        count = 0
        def node(value, depth):
            nonlocal count
            count += 1
            if count > 512 or depth > 32:
                ai._fail('AI_PLAN_TOO_LARGE', 502)
            if not isinstance(value, dict) or not isinstance(value.get('name'), str):
                raise ValueError()
            name = value['name'] if value['name'] in OPERATORS else 'OtherOperator'
            children = value.get('children', [])
            if not isinstance(children, list):
                raise ValueError()
            descriptor = value.get('descriptor', {})
            if not isinstance(descriptor, dict):
                raise ValueError()
            attributes, sources = {}, []
            candidates = {}
            if name == 'Aggregate':
                candidates['phase'] = descriptor.get('type')
            if name in ('LocalExchange', 'Exchange'):
                candidates['partitioning'] = descriptor.get('partitioning')
            if name.endswith('Join'):
                candidates['join_type'] = descriptor.get('type')
            if name in ('LocalExchange', 'Exchange', 'Join', 'RemoteSource') or name.endswith('Join'):
                candidates['distribution'] = descriptor.get('distribution')
            for key, item in candidates.items():
                if isinstance(item, str) and item in _ENUMS[key]:
                    attributes[key] = item
            if name == 'RemoteSource':
                links = descriptor.get('sourceFragmentIds', [])
                if isinstance(links, str):
                    if len(links) > 1024:
                        raise ValueError()
                    links = json.loads(links)
                if not isinstance(links, list) or len(links) > 64:
                    raise ValueError()
                for link in links:
                    if type(link) is not int or str(link) not in ids:
                        raise ValueError()
                    sources.append(ids[str(link)])
            return dict(operator=name, children=[node(child, depth + 1) for child in children],
                remote_sources=sources, attributes=attributes)
        result = dict(projection_version=1, fragments=[dict(id=ids[key], root=node(fragments[key], 1))
            for key in keys])
        # The same bounded payload must fit the strict local inference transport.
        if len(ai._json(result).encode()) > 65536:
            ai._fail('AI_PLAN_TOO_LARGE', 502)
        return result
    except (ValueError, TypeError, KeyError):
        ai._fail('AI_PLAN_INVALID', 502)
    except RecursionError:
        ai._fail('AI_PLAN_TOO_LARGE', 502)


@dataclass(repr=False)
class Capture:
    principal: object
    explanation_id: str
    pins: list
    deadline: float = field(default_factory=lambda: time.monotonic() + DEADLINE)
    pages: int = 0
    cells: int = 0
    raw: object = None
    error: object = None

    def consume(self, data):
        self.pages += 1
        if self.pages > MAX_PAGES or time.monotonic() >= self.deadline:
            ai._fail('AI_PLAN_TOO_LARGE' if self.pages > MAX_PAGES else 'AI_PLAN_TIMEOUT', 502)
        if not isinstance(data, list):
            self.error = 'AI_PLAN_INVALID'
            return
        for row in data:
            self.cells += 1
            if (self.cells != 1 or not isinstance(row, list) or len(row) != 1
                    or not isinstance(row[0], str)):
                self.error = 'AI_PLAN_INVALID'
            elif len(row[0].encode()) > MAX_CELL:
                self.error = 'AI_PLAN_TOO_LARGE'
            elif self.error is None:
                self.raw = row[0]
        if self.error:
            self.raw = None

    def terminal(self, context):
        raw, self.raw = self.raw, None
        try:
            if self.error:
                ai._fail(self.error, 502)
            if self.cells != 1:
                ai._fail('AI_PLAN_INVALID', 502)
            operators = project(raw)
            check_metadata(self.principal, context, self.explanation_id, self.pins)
            return dict(operators=operators, error=None)
        except QueryError as error:
            return dict(operators=None, error=error.code)
        except Exception:
            return dict(operators=None, error='AI_PLAN_INVALID')


def _owned(principal, context, ident, db=None):
    row = db.execute('SELECT * FROM ai_explanations WHERE id=?', (ident,)).fetchone() if db else ai._row(ident)
    if (row is None or row['actor'] != principal.principal_id
            or json.loads(row['context']) != context.public_metadata()
            or json.loads(row['record']).get('kind') != KIND):
        ai._fail('AI_EXPLANATION_NOT_FOUND', 404)
    return row, json.loads(row['record']), json.loads(row['pins'])


def _save(db, ident, record, private):
    record['updated_at'] = ai._now()
    db.execute('UPDATE ai_explanations SET record=?,pins=? WHERE id=?',
        (ai._json(record), ai._json(private), ident))


def check_metadata(principal, context, ident, pins):
    row, record, _ = _owned(principal, context, ident)
    current, _, _ = ai._capture(principal, context, record['input']['sql'], tables_only=True)
    if current != pins:
        ai._fail('AI_METADATA_CHANGED', 409)


def dispatch_claim(db, capture, context, *, claim):
    row, record, private = _owned(capture.principal, context, capture.explanation_id, db)
    ai._transaction_authority(db, capture.principal, context, capture.pins)
    if private['relations'] != capture.pins:
        ai._fail('AI_METADATA_CHANGED', 409)
    if record['cancel_requested']:
        ai._fail('AI_PLAN_CANCELED', 409)
    if record['state'] not in ai._ACTIVE:
        ai._fail('AI_PLAN_NOT_ACTIVE', 409)
    if time.monotonic() >= capture.deadline:
        ai._fail('AI_PLAN_TIMEOUT', 504)
    if claim:
        if private['control'].get('dispatch_claimed'):
            ai._fail('AI_PLAN_ALREADY_SUBMITTED', 409)
        private['control']['dispatch_claimed'] = True
        _save(db, capture.explanation_id, record, private)


def cancel_claim(db, capture, context):
    row, record, private = _owned(capture.principal, context, capture.explanation_id, db)
    record['cancel_requested'] = True
    claimed = private['control'].get('cancel_claimed', False)
    private['control']['cancel_claimed'] = True
    _save(db, capture.explanation_id, record, private)
    return not claimed


def query_state(db, capture, context, query_id, state, backend_id, result):
    """Called inside the query/admission terminal transaction; no metadata I/O."""
    _, record, private = _owned(capture.principal, context, capture.explanation_id, db)
    plan = record['plan']
    plan.update(query_id=query_id, query_state=state)
    if backend_id:
        plan['backend_query_id'] = backend_id
    if state == 'FINISHED':
        # Never let projection/permission rejection roll back known closure.
        error = result['error'] if result else 'AI_PLAN_UNAVAILABLE'
        if error is None:
            try:
                ai._transaction_authority(db, capture.principal, context, capture.pins)
            except QueryError as denied:
                error = denied.code
            except Exception:
                error = 'AI_PLAN_INVALID'
        if error:
            record.update(state='FAILED', plan_status='FAILED', error_code=error)
            plan.update(operators=None, error_code=error)
        else:
            plan.update(operators=result['operators'], captured_at=ai._now(), error_code=None)
            record['plan_status'] = 'COLLECTED'
        if record['cancel_requested']:
            record.update(state='CANCELED', inference_state='CANCELED')
    elif state in ('FAILED', 'CANCELED', 'UNKNOWN'):
        status = state if state in ('CANCELED', 'UNKNOWN') else 'FAILED'
        error = result.get('error') if result else (
            plan.get('error_code') if status == 'UNKNOWN' and plan.get('error_code') else 'AI_PLAN_' + status)
        record.update(state=status, plan_status=status, inference_state='CANCELED' if
            record['cancel_requested'] else 'NOT_STARTED', error_code=error)
        plan['error_code'] = error
    _save(db, capture.explanation_id, record, private)


def _linked_query(principal, context, record, db=None):
    """Read only the original typed child, after checking its immutable link."""
    import query_service as qs
    query_id = record['plan']['query_id']
    if query_id is None:
        return None
    if db is None:
        with control_store.connect() as db:
            return _linked_query(principal, context, record, db)
    row = db.execute('''SELECT q.*, e.*,
        COALESCE((SELECT execution_closure FROM warehouse_admissions a
            WHERE a.kind='query' AND a.subject_id=q.id),'') AS execution_closure
        FROM query_records q JOIN query_execution_contexts e ON q.id=e.query_id
        WHERE q.id=?''', (query_id,)).fetchone()
    expected_sql = 'EXPLAIN (TYPE DISTRIBUTED, FORMAT JSON) ' + qs._backend_statement(record['input']['sql'])
    if (row is None or row['actor'] != principal.principal_id
            or qs._metadata(row) != context.public_metadata()
            or row['request_id'] != record['id']
            or row['worksheet_id'] != 'ai-plan-' + record['id']
            or row['mode'] != 'read-only' or row['sql'] != expected_sql):
        ai._fail('AI_EXPLANATION_NOT_FOUND', 404)
    return row


def _public(record, query=None):
    # Explicit wire projection: private pins/control are never serialized here.
    keys = ('id', 'kind', 'state', 'actor', 'context', 'input', 'model', 'metadata', 'text',
        'error_code', 'created_at', 'updated_at', 'sql_status', 'cancel_requested',
        'inference_state', 'plan_status')
    result = {key: deepcopy(record[key]) for key in keys}
    result['plan'] = {key: deepcopy(record['plan'][key]) for key in (
        'format', 'query_id', 'backend_query_id', 'query_state', 'execution_closure',
        'captured_at', 'operators', 'error_code')}
    if query is not None:
        result['plan'].update(query_state=query['state'], backend_query_id=query['backend_query_id'],
            execution_closure=query['execution_closure'] or None)
    result['plan']['execution_closure'] = result['plan']['execution_closure'] or None
    return result


def _recover(principal, context, ident):
    import query_service as qs
    try:
        owner = ai._owner(ident)
    except BlockingIOError:
        return
    try:
        _, record, private = _owned(principal, context, ident)
        if record['state'] not in ai._ACTIVE:
            return
        query_id = record['plan']['query_id']
        query_owner = None
        try:
            if query_id:
                row = _linked_query(principal, context, record)
                if row['state'] not in qs._TERMINAL and not row['execution_closure']:
                    try:
                        query_owner = qs._acquire_query_owner(query_id)
                    except QueryError:
                        return  # A cold acquisition still has a live query owner.
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                _, record, private = _owned(principal, context, ident, db)
                if record['state'] not in ai._ACTIVE:
                    return
                if query_id:
                    row = _linked_query(principal, context, record, db)
                    record['plan'].update(query_state=row['state'], backend_query_id=row['backend_query_id'])
                    handle = qs.admission.get_for_subject(db, kind='query', subject_id=query_id,
                        warehouse_id=context.warehouse_id)
                    closure = qs.admission.query_closure(db, query_id)
                    record['plan']['execution_closure'] = closure or None
                    if row['state'] not in qs._TERMINAL and not closure:
                        qs.registry.set_query_state(db, query_id, 'UNKNOWN')
                        qs.admission.finish(db, handle, state='UNKNOWN')
                        record['plan']['query_state'] = 'UNKNOWN'
                        record['plan_status'] = 'UNKNOWN'
                    elif row['state'] == 'FINISHED' and record['plan_status'] != 'COLLECTED':
                        record['plan_status'] = 'FAILED'
                        record['plan']['error_code'] = 'AI_PLAN_UNAVAILABLE'
                record.update(state='UNKNOWN', error_code='AI_OWNER_LOST')
                if record['inference_state'] == 'RUNNING':
                    record['inference_state'] = 'UNKNOWN'
                _save(db, ident, record, private)
        finally:
            if query_owner:
                query_owner.close()
    finally:
        owner.close()


def get(principal, context, ident):
    _, record, private = _owned(principal, context, ident)
    ai._authority(principal, context, private['relations'])
    if record['state'] in ai._ACTIVE:
        _recover(principal, context, ident)
        _, record, private = _owned(principal, context, ident)
    ai._authority(principal, context, private['relations'])
    return _public(record, _linked_query(principal, context, record))


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
        _, _, private = _owned(principal, context, record['id'])
        ai._authority(principal, context, private['relations'])
    return records


def _settle_error(principal, context, ident, error):
    import query_service as qs
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        _, record, private = _owned(principal, context, ident, db)
        if record['state'] not in ai._ACTIVE:
            return
        model = record['inference_state'] == 'RUNNING'
        unknown = model and error in ('AI_PROVIDER_TIMEOUT', 'AI_PROVIDER_OUTCOME_UNKNOWN', 'AI_PLAN_OUTCOME_UNKNOWN')
        query_id = record['plan']['query_id']
        if query_id:
            row = _linked_query(principal, context, record, db)
            record['plan'].update(query_state=row['state'], backend_query_id=row['backend_query_id'],
                execution_closure=row['execution_closure'] or None)
            unknown = unknown or row['state'] not in qs._TERMINAL and not row['execution_closure']
        record.update(state='UNKNOWN' if unknown else 'FAILED', error_code=error)
        if model:
            record['inference_state'] = 'UNKNOWN' if unknown else 'FAILED'
        elif record['plan_status'] != 'COLLECTED':
            record['plan_status'] = 'UNKNOWN' if unknown else 'FAILED'
            record['plan']['error_code'] = error
        _save(db, ident, record, private)


def _run(principal, context, ident, pins, config, prompt_sql, relations, language, owner):
    import query_service as qs
    try:
        check_metadata(principal, context, ident, pins)
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            _, record, private = _owned(principal, context, ident, db)
            if record['cancel_requested'] or record['state'] not in ai._ACTIVE:
                return
            ai._transaction_authority(db, principal, context, pins)
            record['state'] = 'RUNNING'
            _save(db, ident, record, private)
        page = qs.submit_explain(principal, context, explanation_id=ident)
        while page.get('next_uri'):
            time.sleep(.02)
            query_id = page['id']
            pin = qs._pins[query_id]
            if time.monotonic() >= pin.plan.deadline:
                qs._best_cancel(query_id)
                ai._fail('AI_PLAN_TIMEOUT', 504)
            page = qs.fetch(principal, query_id, page['next_uri'].rsplit('/', 1)[1],
                selected_role=context.selected_role)
        _, record, _ = _owned(principal, context, ident)
        if record['state'] not in ai._ACTIVE or record['plan_status'] != 'COLLECTED':
            return
        check_metadata(principal, context, ident, pins)
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            _, record, private = _owned(principal, context, ident, db)
            if record['cancel_requested'] or record['state'] not in ai._ACTIVE:
                return
            ai._transaction_authority(db, principal, context, pins)
            record['inference_state'] = 'RUNNING'
            _save(db, ident, record, private)
        text = ai._complete(config, prompt_sql, relations, language, plan=record['plan']['operators'])
        check_metadata(principal, context, ident, pins)
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            _, record, private = _owned(principal, context, ident, db)
            if record['cancel_requested'] or record['state'] not in ai._ACTIVE:
                return
            ai._transaction_authority(db, principal, context, pins)
            record.update(state='SUCCEEDED', inference_state='SUCCEEDED', text=text)
            _save(db, ident, record, private)
    except QueryError as error:
        _, record, _ = _owned(principal, context, ident)
        if record['plan']['query_id'] and record['plan']['query_state'] not in qs._TERMINAL:
            qs._best_cancel(record['plan']['query_id'])
        _settle_error(principal, context, ident, error.code)
    except Exception:
        _settle_error(principal, context, ident, 'AI_PLAN_OUTCOME_UNKNOWN')
    finally:
        owner.close()


def submit(principal, context, *, key, sql, language):
    if (not isinstance(sql, str) or not sql.strip() or len(sql.encode()) > 32768
            or language not in ('ja', 'en') or not isinstance(key, str) or not key.strip() or len(key) > 256):
        ai._fail('AI_INPUT_INVALID')
    payload = ai._json(dict(context=context.public_metadata(), sql=sql, language=language))
    def replay(row):
        if json.loads(row['record'])['kind'] != KIND or row['payload'] != payload:
            ai._fail('IDEMPOTENCY_CONFLICT', 409)
        return get(principal, context, row['id'])
    with control_store.connect() as db:
        row = db.execute('SELECT * FROM ai_explanations WHERE actor=? AND idempotency_key=?',
            (principal.principal_id, key)).fetchone()
    if row:
        return replay(row)
    pins, relations, prompt_sql = ai._capture(principal, context, sql, tables_only=True)
    config = ai._config()
    ident, now = str(uuid.uuid4()), ai._now()
    record = dict(id=ident, kind=KIND, state='QUEUED', actor=principal.principal_id,
        context=context.public_metadata(), input=dict(sql=sql, language=language),
        model=dict(provider='local-openai', name=config['model']),
        metadata=dict(captured_at=now, relations=relations), text=None, error_code=None,
        created_at=now, updated_at=now, sql_status='NOT_EXECUTED', cancel_requested=False,
        inference_state='NOT_STARTED', plan_status='NOT_COLLECTED',
        plan=dict(format='TRINO_DISTRIBUTED_JSON', query_id=None, backend_query_id=None,
            query_state='NOT_SUBMITTED', execution_closure=None, captured_at=None, operators=None, error_code=None))
    private = dict(relations=pins, control={})
    owner = ai._owner(ident)
    try:
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM ai_explanations WHERE actor=? AND idempotency_key=?',
                (principal.principal_id, key)).fetchone()
            if not row:
                ai._transaction_authority(db, principal, context, pins)
                db.execute('INSERT INTO ai_explanations VALUES (?,?,?,?,?,?,?)', (ident,
                    principal.principal_id, key, payload, ai._json(context.public_metadata()),
                    ai._json(private), ai._json(record)))
        if row:
            owner.close()
            return replay(row)
        threading.Thread(target=_run, args=(principal, context, ident, pins, config, prompt_sql,
            relations, language, owner), daemon=True, name='ai-plan-' + ident).start()
    except BaseException:
        owner.close()
        raise
    return get(principal, context, ident)


def cancel(principal, context, ident):
    import query_service as qs
    _, _, private = _owned(principal, context, ident)
    ai._authority(principal, context, private['relations'])
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        _, record, private = _owned(principal, context, ident, db)
        ai._transaction_authority(db, principal, context, private['relations'])
        query = _linked_query(principal, context, record, db)
        record['cancel_requested'] = True
        if record['state'] in ai._ACTIVE:
            if query is None or query['state'] in qs._TERMINAL:
                record.update(state='CANCELED', inference_state='CANCELED', text=None)
                if record['plan_status'] == 'NOT_COLLECTED':
                    record['plan_status'] = 'CANCELED'
        query_id = record['plan']['query_id']
        _save(db, ident, record, private)
    if query_id and query['state'] not in qs._TERMINAL and not query['execution_closure']:
        try:
            qs.cancel(principal, query_id, selected_role=context.selected_role)
        except QueryError:
            _recover(principal, context, ident)
    return get(principal, context, ident)
