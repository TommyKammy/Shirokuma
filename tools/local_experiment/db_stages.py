"""Database owned manual Stages, sharing the P3 execution and admission ledger.

No scanner, scheduler, legacy Job scope, or credential refresh lives here.
"""
from dataclasses import asdict
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
import uuid

import control_store
import data_management as registry
import dataops_routes as common
import dataops_service as ds
import manual_export
import manual_ingest
import query_service as queries
from query_context import QueryError, ResolvedQueryContext, revalidate_admission
import warehouse_admission as admission


LIMITS = dict(file_bytes=25 * 1024 * 1024, entries=1000, hash_bytes=250 * 1024 * 1024)
_BINDING = ('database_id', 'schema_id', 'warehouse_id', 'selected_role',
            'sql_catalog', 'sql_schema', 'binding_revision')
_IDENTITY = _BINDING[:-1]
_SUFFIXES = {'.csv', '.tsv', '.txt', '.json', '.jsonl', '.ndjson'}
_threads = {}
_lock = threading.RLock()


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _access(principal, context):
    ds._validated_context(principal, context)
    if context.selected_role != 'admin':
        raise QueryError('SQL_PERMISSION_DENIED', 403)


def local_roots():
    raw = os.environ.get('SHIROKUMA_STAGE_ROOTS')
    values = json.loads(raw) if raw is not None else [str(control_store.root() / 'stages')]
    if not isinstance(values, list) or not values:
        raise ValueError('Stage roots must be a nonempty JSON array')
    roots = []
    for value in values:
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError('absolute Stage roots required')
        root = Path(value).resolve()
        # The default root is an advertised location, not an automatic mkdir.
        if raw is not None and not root.is_dir():
            raise QueryError('STAGE_ROOT_UNAVAILABLE', 409)
        roots.append(root)
    return roots


def _directory(value):
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError('absolute existing Stage directory required')
    path = Path(value).resolve()
    if not path.is_dir() or not any(root in path.parents for root in local_roots()):
        raise QueryError('STAGE_DIRECTORY_REJECTED', 409)
    return path


def _filename(value, *, input=False):
    if (not isinstance(value, str) or not value or value in ('.', '..', 'exports')
            or Path(value).name != value or '/' in value or '\\' in value
            or value.startswith(('.', '~')) or any(ord(c) < 32 for c in value)
            or len(value.encode('utf-8')) > 240
            or re.search(r'(?:\.(?:part|partial|tmp|temp|crdownload)(?:\.|$)|~$)', value, re.I)):
        raise ValueError('direct visible filename required')
    if input and Path(value).suffix.lower() not in _SUFFIXES:
        raise ValueError('unsupported Stage input suffix')
    return value


def _stage(db, principal, context, stage_id, revision=None, *, refresh_binding=False):
    row = db.execute('SELECT * FROM db_stages WHERE id=?', (stage_id,)).fetchone()
    if row is None or row['owner'] != principal.principal_id or row['database_id'] != context.database_id:
        raise QueryError('STAGE_NOT_FOUND', 404)
    saved = json.loads(row['context'])
    if any(saved[k] != asdict(context)[k] for k in (_IDENTITY if refresh_binding else _BINDING)):
        raise QueryError('CONTEXT_MISMATCH', 409)
    if revision is not None and row['revision'] != revision:
        raise QueryError('STAGE_REVISION_CONFLICT', 409)
    return row


def _public_stage(row):
    return {k: json.loads(row[k]) if k in ('context', 'definition') else row[k]
            for k in ('id', 'owner', 'selected_role', 'revision', 'context', 'definition', 'created_at', 'updated_at')}


def _payload(context, **values):
    return ds._json(dict(context=asdict(context), **values))


def _request(db, principal, route, key, payload):
    if not isinstance(key, str) or not key.strip() or len(key) > 256:
        raise ValueError('idempotency key required')
    row = db.execute('SELECT * FROM db_stage_requests WHERE actor=? AND route=? AND idempotency_key=?',
                     (principal.principal_id, route, key)).fetchone()
    if row is not None:
        if row['payload'] != payload:
            raise QueryError('IDEMPOTENCY_CONFLICT', 409)
        return json.loads(row['response'])


def _receipt(db, principal, route, key, payload, response):
    db.execute('INSERT INTO db_stage_requests VALUES (?,?,?,?,?)',
               (principal.principal_id, route, key, payload, ds._json(response)))


def _revision(value, *, create=False):
    if type(value) is not int or value < (0 if create else 1):
        raise ValueError('explicit Stage revision required')


@contextmanager
def _fresh_input():
    """Trusted, request-local rejection only after replay and before admission."""
    try:
        yield
    except QueryError as error:
        safe = {'STAGE_REVISION_CONFLICT', 'STAGE_INPUT_CHANGED', 'STAGE_INPUT_NOT_FOUND',
                'PREVIEW_CHANGED', 'STAGE_DIRECTORY_REJECTED', 'STAGE_ROOT_UNAVAILABLE',
                'STAGE_DIRECTORY_IMMUTABLE', 'STALE_RESOURCE'}
        if error.status in (400, 409) and error.code in safe:
            raise QueryError('STAGE_INPUT_REJECTED', error.status,
                details=dict(admission='NOT_ATTEMPTED', reason=error.code)) from None
        raise
    except (ValueError, TypeError, UnicodeError, LookupError):
        raise QueryError('STAGE_INPUT_REJECTED', 400,
            details=dict(admission='NOT_ATTEMPTED', reason='INVALID_INPUT')) from None


def _definition(context, definition):
    if not isinstance(definition, dict):
        raise ValueError('Stage definition required')
    common._keys(definition, {'name', 'directory', 'mode', 'format'},
        {'destination', 'target', 'encoding', 'delimiter', 'schema_hints', 'key_columns', 'batch_size', 'atomic_append'})
    definition = json.loads(ds._json(definition))
    if not isinstance(definition['name'], str) or not re.fullmatch('[a-z][a-z0-9_-]{0,47}', definition['name']):
        raise ValueError('invalid Stage name')
    definition['directory'] = str(_directory(definition['directory']))
    chosen = 'destination' if definition['mode'] == 'create' else 'target'
    other = 'target' if chosen == 'destination' else 'destination'
    if other in definition or chosen not in definition:
        raise ValueError('exact Stage target required')
    ref = common.reference(definition[chosen], locator=chosen == 'destination')
    if ref.database_id != context.database_id:
        raise QueryError('CROSS_DATABASE_TARGET', 409)
    with control_store.connect() as db:
        schema = registry.get_schema(db, ref.schema_id)
        if schema is None or schema.database_id != context.database_id:
            raise QueryError('CROSS_DATABASE_TARGET', 409)
        if chosen == 'target':
            obj = registry.get_object(db, ref.object_id)
            if obj is None or not obj.current or obj.schema_id != ref.schema_id or obj.incarnation != ref.incarnation:
                raise QueryError('STALE_RESOURCE', 409)
    options = {k: v for k, v in definition.items() if k not in ('name', 'directory')}
    options[chosen] = ref
    # Validate options without catalog access or SQL, using the P3 normalization.
    manual_ingest._preview_request(context, filename='check.csv', content=b'x\n1\n', **options)
    return definition


def _directory_overlap(candidate, claimed):
    if candidate == claimed or candidate in claimed.parents or claimed in candidate.parents:
        return True
    # Existing case aliases can resolve to different strings on APFS. Compare
    # each leaf only to the other path/ancestors, never shared ancestors alone.
    for leaf, other in ((candidate, claimed), (claimed, candidate)):
        for path in (other, *other.parents):
            try:
                if leaf.samefile(path):
                    return True
            except OSError:
                # Missing historical paths retain their literal ownership above.
                continue
    return False


def save_stage(principal, context, *, key, expected_revision, definition, stage_id=None):
    _access(principal, context)
    route = '/api/v1/stages'
    payload = _payload(context, stage_id=stage_id, expected_revision=expected_revision, definition=definition)
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        replay = _request(db, principal, route, key, payload)
        if replay is not None:
            return replay
        revalidate_admission(db, principal, context)
        with _fresh_input():
            _revision(expected_revision, create=True)
            if (expected_revision == 0) != (stage_id is None) or (stage_id is not None and not isinstance(stage_id, str)):
                raise ValueError('Stage ID and revision required for updates only')
            value = _definition(context, definition)
        now = admission._now()
        try:
            if stage_id is None:
                stage_id = str(uuid.uuid4())
                path = Path(value['directory'])
                for claim in db.execute('SELECT directory FROM db_stage_claims'):
                    old = Path(claim[0])
                    if _directory_overlap(path, old):
                        raise QueryError('STAGE_DIRECTORY_CLAIMED', 409)
                db.execute('INSERT INTO db_stages VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                    (stage_id, context.database_id, principal.principal_id, context.selected_role,
                     value['name'], value['directory'], 1, ds._json(asdict(context)), ds._json(value), now, now))
                db.execute('INSERT INTO db_stage_claims VALUES (?,?)', (value['directory'], stage_id))
            else:
                with _fresh_input():
                    old = _stage(db, principal, context, stage_id, expected_revision, refresh_binding=True)
                    if old['directory'] != value['directory']:
                        raise QueryError('STAGE_DIRECTORY_IMMUTABLE', 409)
                db.execute('UPDATE db_stages SET name=?,definition=?,context=?,revision=revision+1,updated_at=? WHERE id=?',
                           (value['name'], ds._json(value), ds._json(asdict(context)), now, stage_id))
        except sqlite3.IntegrityError:
            raise QueryError('STAGE_NAME_CONFLICT', 409) from None
        result = _public_stage(_stage(db, principal, context, stage_id))
        _receipt(db, principal, route, key, payload, result)
        return result


def list_stages(principal, context):
    _access(principal, context)
    with control_store.connect() as db:
        return [_public_stage(r) for r in db.execute('SELECT * FROM db_stages WHERE database_id=? AND owner=? ORDER BY name',
                                                     (context.database_id, principal.principal_id))]


def get_stage(principal, context, stage_id):
    _access(principal, context)
    with control_store.connect() as db:
        return _public_stage(_stage(db, principal, context, stage_id))


def _input(row, filename):
    _filename(filename, input=True)
    directory = _directory(row['directory'])
    path = (directory / filename).resolve()
    if path.parent != directory or not path.is_file():
        raise QueryError('STAGE_INPUT_NOT_FOUND', 409)
    try:
        with path.open('rb') as stream:
            raw = stream.read(LIMITS['file_bytes'] + 1)
            stat = os.fstat(stream.fileno())
    except OSError:
        raise QueryError('STAGE_INPUT_NOT_FOUND', 409) from None
    if len(raw) > LIMITS['file_bytes']:
        raise QueryError('STAGE_FILE_TOO_LARGE', 413)
    return raw, dict(name=filename, size=len(raw), modified_at=stat.st_mtime_ns, version=_hash(raw))


def list_files(principal, context, stage_id):
    _access(principal, context)
    with control_store.connect() as db:
        row = _stage(db, principal, context, stage_id)
    directory = _directory(row['directory'])
    files, total = [], 0
    # Count every entry, even ignored entries, and never allocate an unbounded list.
    with os.scandir(directory) as entries:
        for number, entry in enumerate(entries, 1):
            if number > LIMITS['entries']:
                raise QueryError('STAGE_ENTRY_LIMIT', 413)
            try:
                _filename(entry.name, input=True)
            except ValueError:
                continue
            if not entry.is_file():
                continue
            size = entry.stat().st_size
            if size > LIMITS['file_bytes']:
                raise QueryError('STAGE_FILE_TOO_LARGE', 413)
            if total + size > LIMITS['hash_bytes']:
                raise QueryError('STAGE_HASH_LIMIT', 413)
            raw, record = _input(row, entry.name)
            total += len(raw)
            if total > LIMITS['hash_bytes']:
                raise QueryError('STAGE_HASH_LIMIT', 413)
            files.append(record)
    return dict(stage_id=stage_id, revision=row['revision'], files=sorted(files, key=lambda f: f['name']), limits=LIMITS.copy())


def _options(definition, filename, raw):
    options = {k: v for k, v in definition.items() if k not in ('name', 'directory')}
    for name in ('destination', 'target'):
        if name in options:
            options[name] = common.reference(options[name], locator=name == 'destination')
    return dict(options, filename=filename, content=raw)


def _confirmation(context, row, filename, raw):
    typed = manual_ingest.preview_fingerprint(context, **_options(json.loads(row['definition']), filename, raw))
    return _hash(ds._json(dict(stage_id=row['id'], revision=row['revision'], typed=typed,
                              file=filename, version=_hash(raw), context=asdict(context))).encode())


def preview(principal, context, stage_id, *, key, expected_revision, file, version):
    _access(principal, context)
    route = '/api/v1/stages/' + stage_id + '/preview'
    payload = _payload(context, expected_revision=expected_revision, file=file, version=version)
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        replay = _request(db, principal, route, key, payload)
        if replay is not None:
            return replay
        revalidate_admission(db, principal, context)
        with _fresh_input():
            _revision(expected_revision)
            row = _stage(db, principal, context, stage_id, expected_revision)
            raw, info = _input(row, file)
            if version != info['version']:
                raise QueryError('STAGE_INPUT_CHANGED', 409)
            result = dict(stage_id=stage_id, revision=row['revision'],
                preview=manual_ingest.input_preview(context, **_options(json.loads(row['definition']), file, raw)),
                confirmation_fingerprint=_confirmation(context, row, file, raw))
        _receipt(db, principal, route, key, payload, result)
        return result


def _run_row(db, principal, context, stage_id, run_id):
    row = db.execute('SELECT * FROM db_stage_runs WHERE id=? AND stage_id=?', (run_id, stage_id)).fetchone()
    if row is None:
        raise QueryError('STAGE_RUN_NOT_FOUND', 404)
    record = json.loads(row['record'])
    if record['owner'] != principal.principal_id:
        raise QueryError('STAGE_RUN_NOT_FOUND', 404)
    if record['context'] != asdict(context):
        raise QueryError('CONTEXT_MISMATCH', 409)
    return row, record


def _write_run(db, run_id, **changes):
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    record = json.loads(db.execute('SELECT record FROM db_stage_runs WHERE id=?', (run_id,)).fetchone()[0])
    record.update(changes)
    db.execute('UPDATE db_stage_runs SET record=? WHERE id=?', (ds._json(record), run_id))
    return record


def _submit(principal, context, stage_id, *, key, expected_revision, kind, file,
            version=None, expected_preview_fingerprint=None, source=None, format=None):
    _access(principal, context)
    route = '/api/v1/stages/' + stage_id + ('/imports' if kind == 'ingest' else '/exports')
    values = dict(expected_revision=expected_revision, file=file)
    if kind == 'ingest':
        values.update(version=version, expected_preview_fingerprint=expected_preview_fingerprint)
    else:
        values.update(source=asdict(source) if type(source) is registry.ResourceRef else source, format=format)
    payload = _payload(context, **values)
    with _lock:
        with control_store.connect() as db:
            db.execute('BEGIN')
            revalidate_admission(db, principal, context)
            replay = _request(db, principal, route, key, payload)
        if replay is not None:
            if 'duplicate' in replay:
                return replay['duplicate']
            return get_run(principal, context, stage_id, replay['run_id'])
        with _fresh_input():
            _revision(expected_revision)
            with control_store.connect() as db:
                row = _stage(db, principal, context, stage_id, expected_revision)
            _directory(row['directory'])
            raw = None
            if kind == 'ingest':
                raw, info = _input(row, file)
                if info['version'] != version:
                    raise QueryError('STAGE_INPUT_CHANGED', 409)
                if _confirmation(context, row, file, raw) != expected_preview_fingerprint:
                    raise QueryError('PREVIEW_CHANGED', 409)
                manual_ingest.input_preview(context, **_options(json.loads(row['definition']), file, raw))
            else:
                _filename(file)
                if type(source) is not registry.ResourceRef:
                    source = common.reference(source)
                if format not in ('csv', 'jsonl'):
                    raise ValueError('csv/jsonl required')
                if source.database_id != context.database_id:
                    raise QueryError('CROSS_DATABASE_TARGET', 409)
        run_id = str(uuid.uuid4())

        def accept(db, operation_id):
            current = _stage(db, principal, context, stage_id, expected_revision)
            _directory(current['directory'])
            if kind == 'ingest':
                captured, info = _input(current, file)
                if captured != raw or info['version'] != version or _confirmation(context, current, file, captured) != expected_preview_fingerprint:
                    raise QueryError('STAGE_INPUT_CHANGED', 409)
            record = dict(id=run_id, stage_id=stage_id, stage_revision=expected_revision,
                kind=kind, owner=principal.principal_id, context=asdict(context), status='queued',
                operation_id=operation_id, committed=False, file=file, created_at=admission._now(),
                request_fingerprint=_hash(payload.encode()))
            if kind == 'ingest':
                record['input_fingerprint'] = _hash(raw)
                definition = json.loads(current['definition'])
                normalized = manual_ingest._preview_request(context, **_options(definition, file, raw))[5]
                configuration = {k: v for k, v in normalized.items() if k not in ('input_sha256', 'input_length')}
                record['configuration_fingerprint'] = _hash(ds._json(configuration).encode())
                if definition['mode'] == 'create':
                    record['destination_intent'] = definition['destination']
                else:
                    record['target_ref'] = definition['target']
            else:
                record.update(target_ref=asdict(source), publication_state='reserved')
            db.execute('INSERT INTO db_stage_runs VALUES (?,?,?,?,?,?,?)',
                (run_id, stage_id, operation_id, ds._json(record), current['definition'],
                 _hash(current['definition'].encode()), raw))
            if kind == 'ingest' and definition['mode'] in ('append', 'upsert'):
                import db_stage_scans as scans
                scans.reserve(db, context, stage_id, definition, file, raw, run_id)
            if kind == 'export':
                try:
                    db.execute('INSERT INTO db_stage_publications VALUES (?,?,?,?,NULL)',
                               (stage_id, file, run_id, 'reserved'))
                except sqlite3.IntegrityError:
                    raise QueryError('STAGE_EXPORT_RESERVED', 409) from None
                # Format is immutable invocation intent, independent of Stage format.
                _write_run(db, run_id, summary=dict(format=format))
            _receipt(db, principal, route, key, payload, dict(run_id=run_id))

        import db_stage_scans as scans
        try:
            execution = ds.begin(principal, context, route=route, idempotency_key=key,
                                 payload=json.loads(payload), _admission_hook=accept)
        except scans._Duplicate:
            # The hook rolled back the tentative DataOps/admission/run together.
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                revalidate_admission(db, principal, context)
                current = _stage(db, principal, context, stage_id, expected_revision)
                replay = _request(db, principal, route, key, payload)
                if replay is not None:
                    return replay['duplicate']
                _, _, prior = scans.lookup(db, context, stage_id, json.loads(current['definition']), file, raw)
                if prior is None or prior['status'] != 'skipped':
                    raise QueryError('STAGE_CONTENT_HELD', 409)
                result = dict(id=None, status='skipped', committed=True, deduplicated=True, context=asdict(context))
                _receipt(db, principal, route, key, payload, dict(duplicate=result))
                return result
        if execution.replayed:
            with control_store.connect() as db:
                found = db.execute('SELECT id FROM db_stage_runs WHERE operation_id=?', (execution.id,)).fetchone()
            return get_run(principal, context, stage_id, found[0])
        try:
            _start_run(run_id, execution)
        except Exception as error:
            _unknown(run_id, execution, error)
        return get_run(principal, context, stage_id, run_id)


def import_file(principal, context, stage_id, *, key, expected_revision, file, version, expected_preview_fingerprint):
    return _submit(principal, context, stage_id, key=key, expected_revision=expected_revision, kind='ingest',
                   file=file, version=version, expected_preview_fingerprint=expected_preview_fingerprint)


def export_table(principal, context, stage_id, *, key, expected_revision, source, format, file):
    return _submit(principal, context, stage_id, key=key, expected_revision=expected_revision, kind='export',
                   file=file, source=source, format=format)


def _start_run(run_id, execution):
    thread = threading.Thread(target=_execute_run, args=(run_id, execution), daemon=True)
    _threads[run_id] = thread
    thread.start()


def _captured_input(run_id, operation_id):
    with control_store.connect() as db:
        row = db.execute('SELECT input FROM db_stage_runs WHERE id=? AND operation_id=?', (run_id, operation_id)).fetchone()
    if row is None or row['input'] is None:
        raise QueryError('STAGE_CAPTURE_UNAVAILABLE', 409)
    return bytes(row['input'])


def _unknown(run_id, execution, error):
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM dataops_operations WHERE id=?', (execution.id,)).fetchone()
        if row is None or row['actor'] != execution.principal.principal_id:
            raise QueryError('OPERATION_NOT_FOUND', 404)
        context = ResolvedQueryContext(**json.loads(row['context']))
        linked = db.execute('SELECT stage_id FROM db_stage_runs WHERE id=? AND operation_id=?', (run_id, execution.id)).fetchone()
        if linked is None:
            raise QueryError('STAGE_RUN_NOT_FOUND', 404)
        _, run = _run_row(db, execution.principal, context, linked['stage_id'], run_id)
        parent = admission.get_for_subject(db, kind='dataops', subject_id=execution.id, warehouse_id=context.warehouse_id)
        parent_state = db.execute('SELECT state FROM warehouse_admissions WHERE id=?', (parent.id,)).fetchone()[0]
        claim = db.execute('SELECT state FROM db_stage_publications WHERE run_id=?', (run_id,)).fetchone()
        changes = dict(committed=bool(row['committed'] or run['committed']))
        if row['result'] is not None:
            changes['summary'] = json.loads(row['result'])
        if row['state'] == 'FINISHED':
            changes['status'] = 'succeeded'
        elif row['state'] in ('FAILED', 'CANCELED'):
            if run['status'] not in ('succeeded', 'failed', 'canceled'):
                changes['status'] = _status(row['state'])
        elif (run['status'] not in ('succeeded', 'failed', 'canceled')
              and not (claim and claim['state'] == 'published')
              and parent_state not in admission.TERMINAL):
            if row['state'] in ('ACTIVE', 'CANCELING'):
                admission.finish(db, parent, state='UNKNOWN')
                db.execute("UPDATE dataops_operations SET state='UNKNOWN' WHERE id=?", (execution.id,))
            changes.update(status='unknown', finished_at=admission._now())
        _write_run(db, run_id, **changes)
    if execution.id in ds._live:
        ds._live[execution.id][2].close()


def _execute_run(run_id, execution):
    with ds._accepted(execution):
        _execute_run_accepted(run_id, execution)


def _execute_run_accepted(run_id, execution):
    try:
        context = execution._context()
        # Share P3's operation lock with cancellation through file publication.
        with execution._active()[3]:
            with control_store.connect() as db:
                run = _write_run(db, run_id, status='running')
                saved = db.execute('SELECT definition FROM db_stage_runs WHERE id=?', (run_id,)).fetchone()[0]
                stage = _stage(db, execution.principal, context, run['stage_id'],
                               run['stage_revision'] if run['kind'] == 'export' else None)
            _directory(stage['directory'])
            if run['kind'] == 'ingest':
                raw = _captured_input(run_id, execution.id)
                if _hash(raw) != run['input_fingerprint']:
                    raise QueryError('STAGE_CAPTURE_UNAVAILABLE', 409)
                result = manual_ingest.ingest(execution.principal, context, idempotency_key=run_id,
                    _execution=execution, _defer_finalization=True,
                    **_options(json.loads(saved), run['file'], raw))
                if result['state'] == 'ACTIVE':
                    execution.complete(result['result'])
            else:
                result = manual_export.export_table(execution.principal, context, key=run_id,
                    source=registry.ResourceRef(**run['target_ref']), format=run['summary']['format'],
                    _execution=execution, _defer_finalization=True)
                _publish(run_id, execution, result['result']['export_id'])
            observed = execution._inspect()
            with control_store.connect() as db:
                target = (observed['result'] or {}).get('target')
                if run['kind'] == 'ingest' and target is not None:
                    _write_run(db, run_id, target_ref=target)
                _write_run(db, run_id, status=_status(observed['state']), committed=observed['committed'],
                    summary=observed['result'], finished_at=admission._now())
    except Exception as error:
        # Publication has an irreversible filesystem boundary. Never erase the
        # claim or repeat SQL when a final ledger write/response is uncertain.
        with control_store.connect() as db:
            intent = db.execute('SELECT state FROM db_stage_publications WHERE run_id=?', (run_id,)).fetchone()
        if intent is not None and intent['state'] in ('intent', 'published') and not isinstance(error, _NoLink):
            _unknown(run_id, execution, error)
        else:
            _settle_failed(run_id, execution, error.error if isinstance(error, _NoLink) else error)
            row = execution._row()
            with control_store.connect() as db:
                _write_run(db, run_id, status=_status(row['state']), committed=bool(row['committed']),
                    summary=json.loads(row['result']) if row['result'] else dict(error=getattr(error, 'code', type(error).__name__)),
                    finished_at=admission._now())
    finally:
        _threads.pop(run_id, None)


def _status(state):
    return {'ACTIVE': 'running', 'CANCELING': 'running', 'FINISHED': 'succeeded',
            'FAILED': 'failed', 'CANCELED': 'canceled', 'UNKNOWN': 'unknown'}[state]


def _publish_link(temporary, destination):
    os.link(temporary, destination)


class _NoLink(RuntimeError):
    """A known rejection before the irreversible publication boundary."""
    def __init__(self, error):
        self.error = error


def _settle_failed(run_id, execution, error):
    context = ResolvedQueryContext(**json.loads(execution._row()['context']))
    with control_store.connect() as db:
        parent = admission.get_for_subject(db, kind='dataops', subject_id=execution.id, warehouse_id=context.warehouse_id)
        children = list(db.execute('''SELECT d.query_id,q.state,e.query_id AS execution_id,a.state AS admission_state
            FROM dataops_queries d LEFT JOIN query_records q ON q.id=d.query_id
            LEFT JOIN query_execution_contexts e ON e.query_id=d.query_id
            LEFT JOIN warehouse_admissions a ON a.kind='query' AND a.subject_id=d.query_id
                AND a.parent_id=? WHERE d.operation_id=?''', (parent.id, execution.id)))
        orphan = db.execute('''SELECT 1 FROM warehouse_admissions a LEFT JOIN dataops_queries d
            ON d.query_id=a.subject_id AND d.operation_id=? WHERE a.parent_id=? AND d.query_id IS NULL''',
            (execution.id, parent.id)).fetchone()
        uncertain = orphan or any(c['state'] not in ('FINISHED', 'FAILED', 'CANCELED')
            or c['execution_id'] is None or c['admission_state'] not in ('FINISHED', 'FAILED', 'CANCELED') for c in children)
        if not uncertain:
            db.execute("UPDATE db_stage_publications SET state='failed' WHERE run_id=? AND state!='published'", (run_id,))
            if db.execute('SELECT 1 FROM db_stage_publications WHERE run_id=?', (run_id,)).fetchone():
                _write_run(db, run_id, publication_state='failed')
    if uncertain:
        _unknown(run_id, execution, error)
    else:
        manual_export._settle(execution, context, error)


def _fsync_directory(directory):
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publication_ledger(db, run_id, receipt):
    db.execute("UPDATE db_stage_publications SET state='published',receipt=? WHERE run_id=? AND state='intent'",
               (ds._json(receipt), run_id))
    operation = db.execute('''SELECT o.state,o.result FROM dataops_operations o
        JOIN db_stage_runs r ON r.operation_id=o.id WHERE r.id=?''', (run_id,)).fetchone()
    terminal = dict(status='succeeded', summary=json.loads(operation['result']), finished_at=admission._now())
    _write_run(db, run_id, publication_state='published', export_id=receipt['export_id'], **terminal)


def _publish(run_id, execution, export_id):
    principal, context = execution.principal, execution._context()
    artifact = manual_export._read_complete(principal, context, export_id=export_id, _execution=execution)
    with control_store.connect() as db:
        row = db.execute('SELECT * FROM db_stage_runs WHERE id=?', (run_id,)).fetchone()
        run = json.loads(row['record'])
        stage = _stage(db, principal, context, run['stage_id'], run['stage_revision'])
    directory = _directory(stage['directory'])
    output = directory / 'exports'
    output.mkdir(mode=0o700, exist_ok=True)
    if output.resolve().parent != directory or output.resolve() != output:
        raise QueryError('STAGE_DIRECTORY_REJECTED', 409)
    destination = output / run['file']
    receipt = dict(artifact['receipt'], stage_id=run['stage_id'], stage_revision=run['stage_revision'],
                   run_id=run_id, file=run['file'], publication_state='published')
    descriptor, name = tempfile.mkstemp(prefix='.stage-', suffix='.tmp', dir=output)
    temporary = Path(name)
    link_possible = False
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(artifact['content'])
            stream.flush()
            os.fsync(stream.fileno())
        # Durable intent precedes the nontransactional filesystem boundary.
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE db_stage_publications SET state='intent',receipt=? WHERE run_id=? AND state='reserved'",
                       (ds._json(receipt), run_id))
            _write_run(db, run_id, publication_state='intent', export_id=export_id)
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            revalidate_admission(db, principal, context)
            current = _stage(db, principal, context, run['stage_id'], run['stage_revision'])
            if _directory(current['directory']) != directory or output.resolve() != output:
                raise QueryError('STAGE_DIRECTORY_REJECTED', 409)
            operation = db.execute('SELECT state FROM dataops_operations WHERE id=?', (execution.id,)).fetchone()
            if operation['state'] != 'ACTIVE':
                raise QueryError('ADMISSION_CONFLICT', 409)
            execution.export_measure(registry.ResourceRef(**artifact['receipt']['source']['ref']), artifact['receipt']['source'])
            try:
                link_possible = True
                _publish_link(temporary, destination)
            except FileExistsError:
                link_possible = False
                raise QueryError('STAGE_EXPORT_EXISTS', 409) from None
            _fsync_directory(output)
            _publication_ledger(db, run_id, receipt)
            parent = admission.get_for_subject(db, kind='dataops', subject_id=execution.id, warehouse_id=context.warehouse_id)
            admission.finish(db, parent, state='FINISHED')
            db.execute("UPDATE dataops_operations SET state='FINISHED' WHERE id=? AND state='ACTIVE'", (execution.id,))
        ds._live[execution.id][2].close()
    except Exception as error:
        if not link_possible:
            raise _NoLink(error) from error
        raise
    finally:
        temporary.unlink(missing_ok=True)


def _publication_proof(db, principal, context, run):
    """Return exact durable evidence, or None. Missing LEFT JOIN rows fail closed."""
    saved, current_run = _run_row(db, principal, context, run['stage_id'], run['id'])
    if current_run != run or run['kind'] != 'export':
        return
    stage = _stage(db, principal, context, run['stage_id'], run['stage_revision'])
    if (saved['definition'] != stage['definition'] or
            saved['config_fingerprint'] != _hash(saved['definition'].encode())):
        return
    claim = db.execute('SELECT * FROM db_stage_publications WHERE run_id=?', (run['id'],)).fetchone()
    operation = db.execute('SELECT * FROM dataops_operations WHERE id=?', (run['operation_id'],)).fetchone()
    artifact = db.execute('SELECT * FROM export_artifacts WHERE operation_id=?', (run['operation_id'],)).fetchone()
    if (claim is None or claim['state'] not in ('intent', 'published') or not claim['receipt']
            or claim['stage_id'] != run['stage_id'] or claim['filename'] != run['file']
            or operation is None or operation['state'] not in ('UNKNOWN', 'FINISHED')
            or operation['actor'] != principal.principal_id or operation['context'] != ds._json(asdict(context))
            or artifact is None or artifact['state'] != 'COMPLETE'
            or artifact['owner'] != principal.principal_id or artifact['context'] != operation['context']
            or artifact['profile_ref'] != manual_export._profile_ref()):
        return
    receipt, original = json.loads(claim['receipt']), json.loads(artifact['record'])
    if (original.get('export_id') != artifact['export_id'] or original.get('operation_id') != operation['id']
            or original.get('owner') != principal.principal_id or original.get('context') != asdict(context)
            or original.get('format') not in ('csv', 'jsonl')
            or artifact['relative_name'] != str(uuid.UUID(artifact['export_id'])) + '.' + original['format']
            or original.get('source', {}).get('ref') != run['target_ref']
            or type(original.get('byte_count')) is not int or original['byte_count'] < 0
            or type(original.get('expected_count')) is not int or original['expected_count'] < 0
            or original['expected_count'] != original.get('written_count')
            or not isinstance(original.get('sha256'), str) or not re.fullmatch('[a-f0-9]{64}', original['sha256'])
            or json.loads(operation['result'] or 'null') != dict(export_id=artifact['export_id'], receipt=original)
            or receipt != dict(original, stage_id=run['stage_id'], stage_revision=run['stage_revision'],
                               run_id=run['id'], file=run['file'], publication_state='published')):
        return
    parent = admission.get_for_subject(db, kind='dataops', subject_id=operation['id'], warehouse_id=context.warehouse_id)
    parent_row = db.execute('SELECT * FROM warehouse_admissions WHERE id=?', (parent.id,)).fetchone() if parent else None
    if (parent_row is None or parent_row['actor'] != principal.principal_id
            or parent_row['generation_id'] != context.generation_id
            or parent_row['state'] not in ('UNKNOWN', 'FINISHED') or parent_row['execution_closure']):
        return
    children = list(db.execute('''SELECT d.step,d.action,d.query_id,q.id AS recorded_id,
        q.actor,q.database_id,q.schema_id,q.warehouse_id,q.generation_id,q.state,
        e.query_id AS execution_id,e.selected_role,e.mode,e.sql_catalog,e.sql_schema,
        e.binding_revision,e.grant_revision,e.backend_username,e.credential_snapshot_ref,
        e.backend_query_id,e.request_id,e.worksheet_id,a.id AS admission_id,
        a.actor AS admission_actor,a.generation_id AS admission_generation,a.state AS admission_state,
        a.execution_closure FROM dataops_queries d
        LEFT JOIN query_records q ON q.id=d.query_id
        LEFT JOIN query_execution_contexts e ON e.query_id=d.query_id
        LEFT JOIN warehouse_admissions a ON a.kind='query' AND a.subject_id=d.query_id
            AND a.warehouse_id=? AND a.parent_id=? WHERE d.operation_id=? ORDER BY d.step''',
        (context.warehouse_id, parent.id, operation['id'])))
    if len(children) != 2 or {(c['step'], c['action']) for c in children} != {('count', 'export_count'), ('select', 'export_select')}:
        return
    for child in children:
        if (not child['recorded_id'] or not child['execution_id'] or not child['admission_id']
                or child['state'] != 'FINISHED' or child['admission_state'] != 'FINISHED'
                or child['execution_closure'] or not child['backend_query_id'] or child['mode'] != 'dataops'
                or child['actor'] != principal.principal_id or child['admission_actor'] != principal.principal_id
                or child['admission_generation'] != context.generation_id
                or child['backend_username'] != operation['backend_username']
                or child['credential_snapshot_ref'] != operation['credential_snapshot_ref']
                or child['request_id'] != operation['id'] or child['worksheet_id'] != child['step']
                or any(child[k] != v for k, v in asdict(context).items())):
            return
    descendants = list(db.execute('''SELECT a.id,d.query_id FROM warehouse_admissions a
        LEFT JOIN dataops_queries d ON d.query_id=a.subject_id AND d.operation_id=? WHERE a.parent_id=?''',
        (operation['id'], parent.id)))
    if len(descendants) != 2 or {r['id'] for r in descendants} != {c['admission_id'] for c in children}:
        return
    if db.execute('''SELECT 1 FROM query_execution_contexts e LEFT JOIN dataops_queries d
        ON d.query_id=e.query_id AND d.operation_id=? WHERE e.request_id=? AND d.query_id IS NULL''',
        (operation['id'], operation['id'])).fetchone():
        return
    evidence = dict(run=run, receipt=receipt, operation=operation['id'],
                    children=[dict(c) for c in children], config_fingerprint=saved['config_fingerprint'])
    return receipt, original, stage['directory'], parent, _hash(ds._json(evidence).encode())


def _proof_bytes(path, receipt):
    with path.open('rb') as stream:
        content = stream.read(receipt['byte_count'] + 1)
    return len(content) == receipt['byte_count'] and _hash(content) == receipt['sha256']


def _observe_publication(principal, context, run):
    """Recover a proved publication and settle its parent; never submit SQL."""
    live = ds._live.get(run['operation_id'])
    lock = live[3] if live else ds._lock
    with lock:
        lease = None
        try:
            # An existing open owner plus its operation lock fences this process.
            # A closed/missing owner must reacquire the same process lease.
            if live is None or live[2].closed:
                lease = queries._acquire_query_owner('dataops-' + run['operation_id'])
            _access(principal, context)
            with control_store.connect() as db:
                proof = _publication_proof(db, principal, context, run)
            if proof is None:
                return
            receipt, original, saved_directory, _, evidence = proof
            directory = _directory(saved_directory)
            output = directory / 'exports'
            _filename(run['file'])
            if output.resolve() != output or (output / run['file']).resolve() != output / run['file'] or not _proof_bytes(output / run['file'], receipt):
                return
            # Both the original COMPLETE P3 file and the actual destination prove
            # the exact admitted bytes. A ledger or absent path alone proves nothing.
            p3_path = manual_export._directory() / (receipt['export_id'] + '.' + receipt['format'])
            if not _proof_bytes(p3_path, original):
                return
            execution = ds.Execution(principal, run['operation_id'])
            execution.export_measure(registry.ResourceRef(**original['source']['ref']), original['source'])
            _fsync_directory(output)
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                revalidate_admission(db, principal, context)
                _, latest = _run_row(db, principal, context, run['stage_id'], run['id'])
                latest_operation = db.execute('SELECT state FROM dataops_operations WHERE id=?', (latest['operation_id'],)).fetchone()
                latest_claim = db.execute('SELECT state FROM db_stage_publications WHERE run_id=?', (run['id'],)).fetchone()
                if latest_operation['state'] == 'FINISHED' and latest_claim and latest_claim['state'] == 'published':
                    return
                current = _publication_proof(db, principal, context, run)
                if current is None or current[4] != evidence or _directory(current[2]) != directory:
                    return
                if output.resolve() != output or (output / run['file']).resolve() != output / run['file'] or not _proof_bytes(output / run['file'], receipt):
                    return
                _publication_ledger(db, run['id'], receipt)
                admission.finish(db, current[3], state='FINISHED', evidence_ref=evidence)
                db.execute("UPDATE dataops_operations SET state='FINISHED' WHERE id=? AND state IN ('UNKNOWN','FINISHED')", (run['operation_id'],))
            if live is not None:
                live[2].close()
        except (QueryError, OSError, ValueError, TypeError, KeyError):
            # Failed proof is observation-only; never turn absence into a retry.
            return
        finally:
            if lease is not None:
                lease.close()


def get_run(principal, context, stage_id, run_id):
    _access(principal, context)
    with control_store.connect() as db:
        _stage(db, principal, context, stage_id)
        row, run = _run_row(db, principal, context, stage_id, run_id)
    operation = ds.Execution(principal, row['operation_id']).inspect()

    def current_observation(observed):
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            _, current = _run_row(db, principal, context, stage_id, run_id)
            saved = db.execute('SELECT * FROM dataops_operations WHERE id=?', (current['operation_id'],)).fetchone()
            committed = bool(current['committed'] or saved['committed'])
            result = json.loads(saved['result']) if saved['result'] is not None else None
            changes = dict(committed=committed)
            if saved['state'] == 'FINISHED':
                changes.update(status='succeeded', summary=result)
            elif current['status'] in ('queued', 'running') and saved['state'] != 'ACTIVE':
                changes.update(status=_status(saved['state']), summary=result)
            elif committed and result is not None:
                changes['summary'] = result
            if current['kind'] == 'ingest' and committed and result and result.get('target'):
                changes['target_ref'] = result['target']
            if any(current.get(k) != v for k, v in changes.items()):
                current = _write_run(db, run_id, **changes)
            return current, dict(observed, state=saved['state'], committed=committed, result=result)

    run, operation = current_observation(operation)
    if run['status'] == 'unknown':
        _observe_publication(principal, context, run)
        operation = ds.Execution(principal, row['operation_id']).inspect()
        run, operation = current_observation(operation)
    return dict(run, operation=operation)


def list_runs(principal, context, stage_id):
    _access(principal, context)
    with control_store.connect() as db:
        _stage(db, principal, context, stage_id)
        ids = [r['id'] for r in db.execute('SELECT id,record FROM db_stage_runs WHERE stage_id=? ORDER BY rowid DESC', (stage_id,))
               if json.loads(r['record'])['context'] == asdict(context)]
    return [get_run(principal, context, stage_id, run_id) for run_id in ids]


def cancel_run(principal, context, stage_id, run_id, *, key):
    ds._cancel_account(principal)
    route = '/api/v1/stages/' + stage_id + '/runs/' + run_id + '/cancel'
    payload = _payload(context)
    with control_store.connect() as db:
        row, run = _run_row(db, principal, context, stage_id, run_id)
        _request(db, principal, route, key, payload)
    execution = ds.Execution(principal, row['operation_id'])
    if not common._cancel_unknown(execution):
        execution.cancel()
    # Revoked callers get no definitions, input names, summaries or result fields.
    try:
        _access(principal, context)
        result = get_run(principal, context, stage_id, run_id)
    except QueryError:
        result = dict(id=run_id, stage_id=stage_id, owner=principal.principal_id,
                      status=_status(execution._row()['state']), operation_id=execution.id)
    with control_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if _request(db, principal, route, key, payload) is None:
            _receipt(db, principal, route, key, payload, dict(run_id=run_id))
    return result


def read_export(principal, context, stage_id, export_id):
    _access(principal, context)
    with control_store.connect() as db:
        stage = _stage(db, principal, context, stage_id)
        claims = list(db.execute("SELECT * FROM db_stage_publications WHERE stage_id=? AND state='published'", (stage_id,)))
    matches = [json.loads(c['receipt']) for c in claims if json.loads(c['receipt'])['export_id'] == export_id]
    if len(matches) != 1:
        raise QueryError('STAGE_EXPORT_NOT_FOUND', 404)
    receipt = matches[0]
    with control_store.connect() as db:
        _, run = _run_row(db, principal, context, stage_id, receipt['run_id'])
    # Preserve P3's source measurement, owner, epoch, complete receipt and hash checks.
    artifact = manual_export.read_export(principal, context, export_id=export_id)
    if receipt != dict(artifact['receipt'], stage_id=stage_id, stage_revision=run['stage_revision'],
                        run_id=run['id'], file=run['file'], publication_state='published'):
        raise QueryError('EXPORT_INTEGRITY_FAILED', 409)
    directory = _directory(stage['directory'])
    path = directory / 'exports' / receipt['file']
    if path.resolve() != path:
        raise QueryError('EXPORT_INTEGRITY_FAILED', 409)
    try:
        with path.open('rb') as stream:
            content = stream.read(receipt['byte_count'] + 1)
    except OSError:
        raise QueryError('EXPORT_INTEGRITY_FAILED', 409) from None
    if content != artifact['content']:
        raise QueryError('EXPORT_INTEGRITY_FAILED', 409)
    _access(principal, context)
    return dict(receipt=receipt, content=content)


def get_export(principal, context, stage_id, export_id):
    return read_export(principal, context, stage_id, export_id)['receipt']


def list_exports(principal, context, stage_id):
    _access(principal, context)
    with control_store.connect() as db:
        _stage(db, principal, context, stage_id)
        receipts = [value for r in db.execute(
            "SELECT receipt FROM db_stage_publications WHERE stage_id=? AND state='published' ORDER BY rowid DESC", (stage_id,))
                    if (value := json.loads(r[0]))['context'] == asdict(context)]
    exports = []
    for receipt in receipts:
        try:
            exports.append(get_export(principal, context, stage_id, receipt['export_id']))
        except QueryError as error:
            if error.status not in (403, 404, 409):
                raise
            continue
    _access(principal, context)
    return exports
