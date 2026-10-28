"""Bounded manual Stage batches and shared SQLite content reservations."""
from dataclasses import asdict
import json
import os
import secrets
import threading
import uuid

import control_store
import dataops_service as ds
import db_stages as stages
import manual_ingest
import query_service as queries
from query_context import QueryError, ResolvedQueryContext, revalidate_admission
import warehouse_admission as admission


_live = {}
_lock = threading.RLock()


class _Duplicate(Exception):
    def __init__(self, disposition):
        self.disposition = disposition


def configuration(context, definition, filename, raw):
    payload = manual_ingest._preview_request(context, **stages._options(definition, filename, raw))[5]
    config = {k: v for k, v in payload.items() if k not in ('input_sha256', 'input_length')}
    fingerprint = stages._hash(ds._json(config).encode())
    return fingerprint, payload


def content_key(context, stage_id, definition, filename, raw):
    fingerprint, payload = configuration(context, definition, filename, raw)
    return stages._hash(ds._json(dict(database_id=context.database_id, stage_id=stage_id,
        configuration_fingerprint=fingerprint, digest=payload['input_sha256'], length=payload['input_length'])).encode())


def _closed(db, operation_id):
    """Canonical query/admission closure, including missing and orphan links."""
    parent = db.execute("SELECT * FROM warehouse_admissions WHERE kind='dataops' AND subject_id=?", (operation_id,)).fetchone()
    if parent is None or parent['state'] not in admission.TERMINAL:
        return False
    rows = list(db.execute('''SELECT d.query_id,q.state,e.query_id AS execution_id,a.state AS admission_state
        FROM dataops_queries d LEFT JOIN query_records q ON q.id=d.query_id
        LEFT JOIN query_execution_contexts e ON e.query_id=d.query_id
        LEFT JOIN warehouse_admissions a ON a.kind='query' AND a.subject_id=d.query_id AND a.parent_id=?
        WHERE d.operation_id=?''', (parent['id'], operation_id)))
    if parent['state'] == 'FINISHED' and not rows:
        return False
    if any(r['state'] not in admission.TERMINAL or r['execution_id'] is None
           or r['admission_state'] not in admission.TERMINAL for r in rows):
        return False
    if db.execute('''SELECT 1 FROM warehouse_admissions a LEFT JOIN dataops_queries d
        ON d.query_id=a.subject_id AND d.operation_id=? WHERE a.parent_id=? AND d.query_id IS NULL''',
        (operation_id, parent['id'])).fetchone():
        return False
    return not db.execute('''SELECT 1 FROM query_execution_contexts e LEFT JOIN dataops_queries d
        ON d.query_id=e.query_id AND d.operation_id=? WHERE e.request_id=? AND d.query_id IS NULL''',
        (operation_id, operation_id)).fetchone()


def disposition(db, run_id):
    saved = db.execute('SELECT record,operation_id FROM db_stage_runs WHERE id=?', (run_id,)).fetchone()
    if saved is None:
        return dict(status='unknown', committed=False, retryable=False)
    record = json.loads(saved['record'])
    op = db.execute('SELECT state,committed,result FROM dataops_operations WHERE id=?', (saved['operation_id'],)).fetchone()
    committed = bool(record.get('committed') or (op and op['committed']))
    closed = op is not None and _closed(db, saved['operation_id'])
    result = json.loads(op['result'] or '{}') if op else {}
    partial = (result.get('mode') == 'append' and type(result.get('accepted_rows')) is int
        and type(result.get('committed_rows')) is int and result['committed_rows'] < result['accepted_rows'])
    if committed and partial:
        return dict(status='failed' if closed else 'unknown', committed=True, retryable=False,
                    error_code='STAGE_CONTENT_PARTIAL')
    if closed and op['state'] == 'FINISHED' and committed:
        return dict(status='skipped', committed=True, retryable=False)
    if closed and op['state'] in ('FAILED', 'CANCELED') and not committed:
        return dict(status='failed', committed=False, retryable=True, error_code='STAGE_CONTENT_FAILED')
    if closed and op['state'] in ('FAILED', 'CANCELED') and committed:
        return dict(status='failed', committed=True, retryable=False, error_code='STAGE_CONTENT_COMMITTED')
    return dict(status='unknown', committed=committed, retryable=False)


def lookup(db, context, stage_id, definition, filename, raw, *, exclude_run_id=None):
    key = content_key(context, stage_id, definition, filename, raw)
    row = db.execute('SELECT run_id FROM db_stage_content WHERE content_key=?', (key,)).fetchone()
    if row is not None:
        return key, row['run_id'], disposition(db, row['run_id'])
    # Schema14 evidence remains immutable, including its original full context.
    fingerprint, _ = configuration(context, definition, filename, raw)
    matches = []
    for old in db.execute('SELECT id,record,input FROM db_stage_runs WHERE stage_id=?', (stage_id,)):
        if old['id'] == exclude_run_id:
            continue
        record = json.loads(old['record'])
        if (record.get('kind') == 'ingest' and record.get('configuration_fingerprint') == fingerprint
                and record.get('input_fingerprint') == stages._hash(raw) and old['input'] is not None
                and len(old['input']) == len(raw)):
            matches.append((old['id'], disposition(db, old['id'])))
    if not matches:
        return key, None, None
    # A committed or unresolved historical attempt dominates known failure.
    matches.sort(key=lambda v: (bool(v[1]['committed']), not v[1]['retryable']), reverse=True)
    run_id, result = matches[0]
    db.execute('INSERT INTO db_stage_content VALUES (?,?)', (key, run_id))
    return key, run_id, result


def reserve(db, context, stage_id, definition, filename, raw, run_id, *, expected_run_id=None):
    key, previous, result = lookup(db, context, stage_id, definition, filename, raw, exclude_run_id=run_id)
    if expected_run_id is not None and previous != expected_run_id:
        raise QueryError('STAGE_SCAN_ATTEMPT_CONFLICT', 409)
    if result is not None and not (expected_run_id is not None and result['retryable']):
        raise _Duplicate(result)
    if previous is None:
        db.execute('INSERT INTO db_stage_content VALUES (?,?)', (key, run_id))
    else:
        db.execute('UPDATE db_stage_content SET run_id=? WHERE content_key=?', (run_id, key))


def _failed_link(db, run_id):
    row = db.execute('SELECT operation_id FROM db_stage_runs WHERE id=?', (run_id,)).fetchone()
    attempt = db.execute('SELECT attempt FROM db_stage_scan_operations WHERE operation_id=?', (row['operation_id'],)).fetchone()
    return dict(run_id=run_id, operation_id=row['operation_id'], attempt=attempt[0] if attempt else 1)


def _scan(db, principal, context, stage_id, scan_id):
    stages._stage(db, principal, context, stage_id)
    row = db.execute('SELECT * FROM db_stage_scans WHERE id=? AND stage_id=?', (scan_id, stage_id)).fetchone()
    if row is None:
        raise QueryError('STAGE_SCAN_NOT_FOUND', 404)
    record = json.loads(row['record'])
    if record['owner'] != principal.principal_id:
        raise QueryError('STAGE_SCAN_NOT_FOUND', 404)
    if record['context'] != asdict(context):
        raise QueryError('CONTEXT_MISMATCH', 409)
    return row


def _update(db, table, ident, **changes):
    record = json.loads(db.execute('SELECT record FROM ' + table + ' WHERE id=?', (ident,)).fetchone()[0])
    record.update(changes)
    db.execute('UPDATE ' + table + ' SET record=? WHERE id=?', (ds._json(record), ident))
    return record


def _items(db, scan_id):
    return list(db.execute('SELECT * FROM db_stage_scan_items WHERE scan_id=? ORDER BY position', (scan_id,)))


def _public(db, scan_id):
    record = json.loads(db.execute('SELECT record FROM db_stage_scans WHERE id=?', (scan_id,)).fetchone()[0])
    current = db.execute('SELECT definition FROM db_stages WHERE id=?', (record['stage_id'],)).fetchone()
    definition = json.loads(current['definition'])
    context = ResolvedQueryContext(**record['context'])
    items = []
    for row in _items(db, scan_id):
        item = {k: v for k, v in json.loads(row['record']).items() if not k.startswith('_')}
        if item['retryable']:
            prior = db.execute('SELECT run_id FROM db_stage_content WHERE content_key=?', (row['content_key'],)).fetchone()
            item['retryable'] = bool(record['status'] not in ('queued', 'running', 'unknown') and prior
                and prior['run_id'] == item.get('run_id') and disposition(db, prior['run_id'])['retryable']
                and content_key(context, record['stage_id'], definition, item['file'], bytes(row['input'])) == row['content_key'])
        items.append(item)
    record['items'] = items
    record['committed'] = any(item['committed'] for item in record['items'])
    return record


def _manifest(row):
    directory = stages._directory(row['directory'])
    files, total = [], 0
    with os.scandir(directory) as entries:
        for count, entry in enumerate(entries, 1):
            if count > stages.LIMITS['entries']:
                raise QueryError('STAGE_ENTRY_LIMIT', 413)
            try:
                stages._filename(entry.name, input=True)
            except ValueError:
                continue
            if not entry.is_file():
                continue
            raw, info = stages._input(row, entry.name)
            total += len(raw)
            if total > stages.LIMITS['hash_bytes']:
                raise QueryError('STAGE_HASH_LIMIT', 413)
            files.append((entry.name, raw))
    return sorted(files)


def _mode(definition):
    if definition['mode'] not in ('append', 'upsert') or (
            definition['mode'] == 'append' and definition.get('atomic_append') is not True):
        raise QueryError('STAGE_SCAN_MODE_REJECTED', 409)
    if definition['mode'] == 'upsert' and not definition.get('key_columns'):
        raise QueryError('STAGE_SCAN_MODE_REJECTED', 409)


def _parent(db, principal, context, subject):
    return admission.admit(db, warehouse_id=context.warehouse_id, actor=principal.principal_id,
        kind='stage_scan', subject_id=subject, generation_id=context.generation_id, database_id=context.database_id)


def _launch(scan_id, principal, context, parent, frozen, owner):
    _live[scan_id] = (principal, context, parent, frozen, secrets.token_urlsafe(32), owner)
    try:
        _start(scan_id)
    except Exception:
        _live.pop(scan_id, None)
        owner.close()
        _observe(scan_id)


def submit(principal, context, stage_id, *, key, expected_revision):
    stages._access(principal, context)
    stages._revision(expected_revision)
    route = '/api/v1/stages/' + stage_id + '/scans'
    payload = stages._payload(context, expected_revision=expected_revision)
    with _lock:
        with control_store.connect() as db:
            stages._stage(db, principal, context, stage_id)
            replay = stages._request(db, principal, route, key, payload)
        if replay is not None:
            return get(principal, context, stage_id, replay['scan_id'])
        frozen = queries._capture_dataops(principal, context)
        scan_id = str(uuid.uuid4())
        owner = queries._acquire_query_owner('stage-scan-' + scan_id)
        try:
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                revalidate_admission(db, principal, context)
                replay = stages._request(db, principal, route, key, payload)
                if replay is not None:
                    owner.close()
                    return _public(db, replay['scan_id'])
                row = stages._stage(db, principal, context, stage_id, expected_revision)
                definition = json.loads(row['definition'])
                _mode(definition)
                files = _manifest(row)
                import db_schedules
                db_schedules._admit(db, 'stage', stage_id, expected_revision, scan_id, context)
                parent = _parent(db, principal, context, scan_id)
                record = dict(id=scan_id, stage_id=stage_id, stage_revision=expected_revision,
                    context=asdict(context), owner=principal.principal_id, status='queued', committed=False)
                db.execute('INSERT INTO db_stage_scans VALUES (?,?,?,?,?,0)',
                    (scan_id, stage_id, ds._json(record), row['definition'], parent.id))
                for position, (filename, raw) in enumerate(files):
                    ident = str(uuid.uuid4())
                    content, prior_run, prior = lookup(db, context, stage_id, definition, filename, raw)
                    item = dict(id=ident, file=filename, input_fingerprint=stages._hash(raw), status='pending',
                                attempt=0, committed=False, retryable=False)
                    if prior:
                        item.update(prior)
                        if prior['retryable']:
                            item.update(_failed_link(db, prior_run))
                    db.execute('INSERT INTO db_stage_scan_items VALUES (?,?,?,?,?,?)',
                        (ident, scan_id, position, ds._json(item), raw, content))
                stages._receipt(db, principal, route, key, payload, dict(scan_id=scan_id))
            _launch(scan_id, principal, context, parent, frozen, owner)
        except BaseException:
            owner.close()
            raise
        return get(principal, context, stage_id, scan_id)


def _start(scan_id):
    threading.Thread(target=_execute, args=(scan_id,), daemon=True).start()


def _admit_file(scan_id, item_id, *, retry=False):
    principal, context, parent, frozen, ref, _ = _live[scan_id]
    with control_store.connect() as db:
        scan = db.execute('SELECT * FROM db_stage_scans WHERE id=?', (scan_id,)).fetchone()
        item_row = db.execute('SELECT * FROM db_stage_scan_items WHERE id=?', (item_id,)).fetchone()
    item = json.loads(item_row['record'])
    definition, raw = json.loads(scan['definition']), bytes(item_row['input'])
    run_id = str(uuid.uuid4())
    attempt = item['attempt'] if item.get('_retry_run_id') else item['attempt'] + 1

    def accept(db, operation_id):
        ds._check_stage_scan(db, (None, None, None, None, None, scan_id))
        current = stages._stage(db, principal, context, json.loads(scan['record'])['stage_id'])
        fingerprint, _ = configuration(context, definition, item['file'], raw)
        record = dict(id=run_id, stage_id=current['id'], stage_revision=json.loads(scan['record'])['stage_revision'],
            kind='ingest', owner=principal.principal_id, context=asdict(context), status='queued',
            operation_id=operation_id, committed=False, file=item['file'], created_at=admission._now(),
            input_fingerprint=stages._hash(raw), configuration_fingerprint=fingerprint, target_ref=definition['target'])
        db.execute('INSERT INTO db_stage_runs VALUES (?,?,?,?,?,?,?)',
            (run_id, current['id'], operation_id, ds._json(record), scan['definition'], stages._hash(scan['definition'].encode()), raw))
        reserve(db, context, current['id'], definition, item['file'], raw, run_id,
                expected_run_id=item.get('_retry_run_id'))
        _update(db, 'db_stage_scan_items', item_id, status='running', attempt=attempt,
                run_id=run_id, operation_id=operation_id, retryable=False, error_code=None)

    try:
        with ds._stage_scan_scope(principal, context, parent, frozen, ref, scan_id, item_id, attempt):
            execution = ds.begin(principal, context, route='/stage-scan/' + scan_id,
                idempotency_key=item_id + ':' + str(attempt), payload=dict(item_id=item_id, attempt=attempt),
                _admission_hook=accept)
        stages._execute_run(run_id, execution)
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            result = disposition(db, run_id)
            if result['status'] == 'skipped':
                result['status'] = 'succeeded'
            _update(db, 'db_stage_scan_items', item_id, **result)
    except _Duplicate as duplicate:
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            _, prior_run, result = lookup(db, context, json.loads(scan['record'])['stage_id'], definition, item['file'], raw)
            changes = dict(result)
            if result['retryable']:
                changes.update(_failed_link(db, prior_run))
            _update(db, 'db_stage_scan_items', item_id, **changes)


def _settle(db, scan_id, *, stopped=False):
    row = db.execute('SELECT * FROM db_stage_scans WHERE id=?', (scan_id,)).fetchone()
    records = [json.loads(r['record']) for r in _items(db, scan_id)]
    for item in records:
        if item['status'] == 'pending':
            _update(db, 'db_stage_scan_items', item['id'], status='not_started')
    records = [json.loads(r['record']) for r in _items(db, scan_id)]
    unresolved = any(not _closed(db, row[0]) for row in db.execute(
        'SELECT operation_id FROM db_stage_scan_operations WHERE scan_id=?', (scan_id,)))
    unknown = unresolved or any(i['status'] in ('running', 'unknown') for i in records)
    committed = any(i['committed'] for i in records)
    if unknown:
        status, state = 'unknown', 'UNKNOWN'
    elif stopped or row['cancel_requested']:
        status, state = 'canceled', 'CANCELED'
    elif any(i['status'] in ('failed', 'not_started') for i in records):
        status, state = ('partial' if committed else 'failed'), 'FAILED'
    else:
        status, state = 'succeeded', 'FINISHED'
    parent = admission._handle(db.execute('SELECT * FROM warehouse_admissions WHERE id=?', (row['admission_id'],)).fetchone())
    admission.finish(db, parent, state=state, evidence_ref='stage-scan:canonical-children')
    _update(db, 'db_stage_scans', scan_id, status=status, committed=committed)


def _execute(scan_id, retry_item=None):
    with admission.accepted(_live[scan_id][2]):
        _execute_accepted(scan_id, retry_item)


def _execute_accepted(scan_id, retry_item=None):
    principal, context, _, _, _, owner = _live[scan_id]
    try:
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            _update(db, 'db_stage_scans', scan_id, status='running')
            items = _items(db, scan_id)
        for row in items:
            with control_store.connect() as db:
                scan = db.execute('SELECT * FROM db_stage_scans WHERE id=?', (scan_id,)).fetchone()
                item = json.loads(db.execute('SELECT record FROM db_stage_scan_items WHERE id=?', (row['id'],)).fetchone()[0])
            if scan['cancel_requested']:
                break
            if retry_item is not None and row['id'] != retry_item:
                continue
            if item['status'] in ('failed', 'unknown'):
                break
            if item['status'] != 'pending':
                continue
            stages._access(principal, context)
            _admit_file(scan_id, row['id'], retry=row['id'] == retry_item)
            with control_store.connect() as db:
                result = json.loads(db.execute('SELECT record FROM db_stage_scan_items WHERE id=?', (row['id'],)).fetchone()[0])
            if result['status'] not in ('succeeded', 'skipped'):
                break
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            _settle(db, scan_id)
    except Exception:
        # No SQL replay on errors, including lost bookkeeping after admission.
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in _items(db, scan_id):
                item = json.loads(row['record'])
                if item['status'] == 'running':
                    _update(db, 'db_stage_scan_items', row['id'], status='unknown', retryable=False)
            _settle(db, scan_id, stopped=True)
    finally:
        _live.pop(scan_id, None)
        owner.close()


def _observe(scan_id):
    if scan_id in _live:
        return
    try:
        owner = queries._acquire_query_owner('stage-scan-' + scan_id)
    except QueryError:
        return
    try:
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            scan = db.execute('SELECT record FROM db_stage_scans WHERE id=?', (scan_id,)).fetchone()
            if json.loads(scan['record'])['status'] not in ('queued', 'running', 'unknown'):
                return
            items = _items(db, scan_id)
            pending = any(json.loads(row['record'])['status'] == 'pending' for row in items)
            for row in items:
                item = json.loads(row['record'])
                if item.get('run_id'):
                    result = disposition(db, item['run_id'])
                    if result['status'] == 'skipped':
                        result['status'] = 'succeeded'
                    _update(db, 'db_stage_scan_items', row['id'], **result)
                elif item['status'] == 'unknown':
                    prior = db.execute('SELECT run_id FROM db_stage_content WHERE content_key=?', (row['content_key'],)).fetchone()
                    if prior:
                        result = disposition(db, prior['run_id'])
                        if result['retryable']:
                            result.update(_failed_link(db, prior['run_id']))
                        _update(db, 'db_stage_scan_items', row['id'], **result)
            # Owner loss is not evidence that pending input executed. It is
            # safe to close only positively closed admitted children.
            _settle(db, scan_id, stopped=pending)
    finally:
        owner.close()


def get(principal, context, stage_id, scan_id):
    stages._access(principal, context)
    with control_store.connect() as db:
        _scan(db, principal, context, stage_id, scan_id)
    _observe(scan_id)
    with control_store.connect() as db:
        db.execute('BEGIN')
        revalidate_admission(db, principal, context)
        _scan(db, principal, context, stage_id, scan_id)
        return _public(db, scan_id)


def list_scans(principal, context, stage_id):
    stages._access(principal, context)
    with control_store.connect() as db:
        stages._stage(db, principal, context, stage_id)
        ids = [r[0] for r in db.execute('SELECT id FROM db_stage_scans WHERE stage_id=? ORDER BY rowid DESC', (stage_id,))
               if json.loads(db.execute('SELECT record FROM db_stage_scans WHERE id=?', (r[0],)).fetchone()[0])['context'] == asdict(context)]
    for ident in ids:
        get(principal, context, stage_id, ident)
    with control_store.connect() as db:
        db.execute('BEGIN')
        revalidate_admission(db, principal, context)
        stages._stage(db, principal, context, stage_id)
        return [_public(db, ident) for ident in ids]


def retry(principal, context, stage_id, scan_id, *, key, expected_revision, item_id, expected_attempt):
    stages._access(principal, context)
    stages._revision(expected_revision)
    if type(expected_attempt) is not int or expected_attempt < 0:
        raise ValueError('explicit attempt required')
    if not isinstance(item_id, str) or str(uuid.UUID(item_id)) != item_id:
        raise ValueError('canonical item UUID required')
    route = '/api/v1/stages/' + stage_id + '/scans/' + scan_id + '/retry'
    payload = stages._payload(context, expected_revision=expected_revision, item_id=item_id, expected_attempt=expected_attempt)
    with _lock:
        with control_store.connect() as db:
            _scan(db, principal, context, stage_id, scan_id)
            replay = stages._request(db, principal, route, key, payload)
        if replay is not None:
            return get(principal, context, stage_id, replay['scan_id'])
        get(principal, context, stage_id, scan_id)
        frozen = queries._capture_dataops(principal, context)
        new_id, new_item_id = str(uuid.uuid4()), str(uuid.uuid4())
        owner = queries._acquire_query_owner('stage-scan-' + new_id)
        try:
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                revalidate_admission(db, principal, context)
                replay = stages._request(db, principal, route, key, payload)
                if replay is not None:
                    owner.close()
                    _scan(db, principal, context, stage_id, replay['scan_id'])
                    return _public(db, replay['scan_id'])
                scan = _scan(db, principal, context, stage_id, scan_id)
                current = stages._stage(db, principal, context, stage_id, expected_revision)
                if scan_id in _live or json.loads(scan['record'])['status'] in ('queued', 'running', 'unknown'):
                    raise QueryError('STAGE_SCAN_RETRY_REJECTED', 409)
                row = db.execute('SELECT * FROM db_stage_scan_items WHERE id=? AND scan_id=?', (item_id, scan_id)).fetchone()
                if row is None:
                    raise QueryError('STAGE_SCAN_ITEM_NOT_FOUND', 404)
                item = json.loads(row['record'])
                if item['attempt'] != expected_attempt:
                    raise QueryError('STAGE_SCAN_ATTEMPT_CONFLICT', 409)
                definition, raw = json.loads(current['definition']), bytes(row['input'])
                _mode(definition)
                if content_key(context, stage_id, definition, item['file'], raw) != row['content_key']:
                    raise QueryError('STAGE_SCAN_RETRY_REJECTED', 409)
                _, prior_run, prior = lookup(db, context, stage_id, definition, item['file'], raw)
                if (prior is None or not prior['retryable'] or item['status'] != 'failed'
                        or prior_run != item.get('run_id')
                        or _failed_link(db, prior_run)['attempt'] != expected_attempt):
                    raise QueryError('STAGE_SCAN_RETRY_REJECTED', 409)
                parent = _parent(db, principal, context, new_id)
                record = dict(id=new_id, stage_id=stage_id, stage_revision=expected_revision,
                    context=asdict(context), owner=principal.principal_id, status='queued', committed=False,
                    source_scan_id=scan_id, source_item_id=item_id)
                db.execute('INSERT INTO db_stage_scans VALUES (?,?,?,?,?,0)',
                    (new_id, stage_id, ds._json(record), current['definition'], parent.id))
                retry_item = dict(id=new_item_id, file=item['file'], input_fingerprint=item['input_fingerprint'],
                    status='pending', attempt=expected_attempt + 1, committed=False, retryable=False,
                    _retry_run_id=prior_run)
                db.execute('INSERT INTO db_stage_scan_items VALUES (?,?,?,?,?,?)',
                    (new_item_id, new_id, 0, ds._json(retry_item), raw, row['content_key']))
                stages._receipt(db, principal, route, key, payload, dict(scan_id=new_id))
            _live[new_id] = (principal, context, parent, frozen, secrets.token_urlsafe(32), owner)
            try:
                _start_retry(new_id, new_item_id)
            except Exception:
                _live.pop(new_id, None)
                owner.close()
                _observe(new_id)
        except BaseException:
            owner.close()
            raise
        return get(principal, context, stage_id, new_id)


def _start_retry(scan_id, item_id):
    threading.Thread(target=_execute, args=(scan_id, item_id), daemon=True).start()


def cancel(principal, context, stage_id, scan_id, *, key, expected_revision):
    ds._cancel_account(principal)
    stages._revision(expected_revision)
    route = '/api/v1/stages/' + stage_id + '/scans/' + scan_id + '/cancel'
    payload = stages._payload(context, expected_revision=expected_revision)
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        _scan(db, principal, context, stage_id, scan_id)
        replay = stages._request(db, principal, route, key, payload)
        if replay is None:
            stages._stage(db, principal, context, stage_id, expected_revision)
            db.execute('UPDATE db_stage_scans SET cancel_requested=1 WHERE id=?', (scan_id,))
            stages._receipt(db, principal, route, key, payload, dict(scan_id=scan_id))
    # Cancellation bounds the batch at its current file boundary. The active
    # file retains its ordinary P3 settlement and any verified commit.
    with control_store.connect() as db:
        row = _scan(db, principal, context, stage_id, scan_id)
        return dict(id=scan_id, status=json.loads(row['record'])['status'])
