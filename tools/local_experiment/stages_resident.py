"""Named local directories for explicit single-file imports through the jobs worker.

Versions are metadata checks, not content hashes or exactly-once guarantees.
The SQLite ledger and Iceberg commit are separate; unknown runs never replay.
"""
from contextlib import nullcontext
import csv
from datetime import datetime, timezone
from decimal import InvalidOperation
import hashlib
import json
import tempfile
from pathlib import Path
import re

import jobs_resident as jobs
import lake_ingest
import request_auth

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_DIRECTORY_ENTRIES = 1000
MAX_SCAN_BYTES = MAX_FILE_BYTES * 10


def _identity(actor, role):
    jobs._identity(actor, role)
    if role != 'admin':
        raise PermissionError('Stages require admin')
    jobs._check_account(actor, role)


def _validate(definition):
    if not isinstance(definition, dict) or definition.keys() - {'name', 'directory', 'table', 'mode', 'format', 'key'}:
        raise ValueError('invalid stage definition')
    name = definition.get('name')
    if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,47}', name):
        raise ValueError('stage name must be a lowercase slug of at most 48 characters')
    directory = definition.get('directory')
    if not isinstance(directory, str) or not Path(directory).is_absolute():
        raise ValueError('stage directory must be an absolute existing directory')
    directory = Path(directory).resolve()
    if not directory.is_dir():
        raise ValueError('stage directory must be an absolute existing directory')
    table, mode, format = definition.get('table'), definition.get('mode'), definition.get('format', 'auto')
    if not isinstance(table, str) or not table.strip():
        raise ValueError('stage requires an explicit target table')
    if not isinstance(mode, str) or mode not in {'append', 'replace', 'upsert'}:
        raise ValueError('stage requires append, replace, or upsert mode')
    if not isinstance(format, str) or format not in {'auto', 'csv', 'jsonl', 'parquet'}:
        raise ValueError('invalid stage format')
    key = definition.get('key')
    if key is not None and not isinstance(key, str):
        raise ValueError('stage key must be text')
    if mode == 'upsert' and (not key or not any(part.strip() for part in key.split(','))):
        raise ValueError('upsert requires key')
    result = dict(name=name, directory=str(directory), table=lake_ingest.target_table(table, directory),
                  mode=mode, format=format)
    if key is not None:
        result['key'] = key
    return result


def _entry(row, actor):
    return dict(name=row['name'], owner=row['owner'], definition=json.loads(row['definition']),
                created_at=row['created_at'], updated_at=row['updated_at'], can_manage=row['owner'] == actor,
                schedule=_get_schedule(row['name'], actor), legacy_history=_legacy_history(row['name']))


def save_stage(definition, actor, role):
    _identity(actor, role)
    definition = _validate(definition)
    name, now = definition['name'], jobs._now()
    with jobs._db() as connection:
        connection.execute('BEGIN IMMEDIATE')
        old = connection.execute('SELECT * FROM stages WHERE name=?', (name,)).fetchone()
        if old and old['owner'] != actor:
            raise PermissionError('Only the stage owner can update it')
        connection.execute('INSERT INTO stages VALUES (?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET '
                           'definition=excluded.definition,updated_at=excluded.updated_at',
                           (name, actor, json.dumps(definition), now, now))
        connection.execute('UPDATE schedules SET enabled=0,next_run_at=NULL WHERE name=?', ('stage:' + name,))
        row = connection.execute('SELECT * FROM stages WHERE name=?', (name,)).fetchone()
    return _entry(row, actor)


def list_stages(actor, role):
    _identity(actor, role)
    with jobs._db() as connection:
        rows = connection.execute('SELECT * FROM stages ORDER BY name').fetchall()
    return [_entry(row, actor) for row in rows]


def _owned(name, actor):
    if not isinstance(name, str):
        raise ValueError('stage name must be text')
    with jobs._db() as connection:
        row = connection.execute('SELECT * FROM stages WHERE name=? AND owner=?', (name, actor)).fetchone()
        if row is None:
            raise jobs.JobNotFound('Stage not found or not owned by this identity')
        definition = json.loads(row['definition'])
        definition['_revision'] = row['updated_at']
        return definition


def _filename(name):
    return (isinstance(name, str) and bool(name) and Path(name).name == name
            and '/' not in name and '\\' not in name and not name.startswith(('.', '~'))
            and not name.endswith('~') and not any(part in name.lower().split('.') for part in ('partial', 'part', 'tmp', 'crdownload'))
            and Path(name).suffix.lower() in lake_ingest.FORMATS)


def _file(definition, name, version=None):
    if not _filename(name):
        raise ValueError('select a direct, supported stage filename')
    directory = Path(definition['directory']).resolve()
    path = (directory / name).resolve()
    if path.parent != directory or not path.is_file():
        raise ValueError('stage file is unavailable or outside its directory')
    if not _filename(path.name):
        raise ValueError('select a direct, supported stage filename')
    stat = path.stat()
    metadata = dict(name=name, size=stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                    version=f"{definition['_revision']}:{stat.st_size}:{stat.st_mtime_ns}")
    if version is not None and (not isinstance(version, str) or version != metadata['version']):
        raise ValueError('stage or file changed; refresh files and preview again')
    return path, metadata


def _selected(definition, name, version):
    if not isinstance(version, str) or not version:
        raise ValueError('selected file version is required')
    path, metadata = _file(definition, name, version)
    if metadata['size'] > MAX_FILE_BYTES:
        raise ValueError('stage file exceeds the 25 MiB limit')
    return path, metadata


def list_files(name, actor, role, *, _definition=None, _connection=None):
    _identity(actor, role)
    if _connection is None:
        jobs._recover()
    definition = _definition or _owned(name, actor)
    files, hashed_bytes = [], 0
    try:
        for count, path in enumerate(Path(definition['directory']).iterdir(), 1):
            if count > MAX_DIRECTORY_ENTRIES:
                raise ValueError('stage directory exceeds 1000 entries; use a smaller directory')
            if not _filename(path.name):
                continue
            try:
                resolved, metadata = _file(definition, path.name)
            except (OSError, ValueError):
                continue
            metadata['available'] = metadata['size'] <= MAX_FILE_BYTES
            if not metadata['available']:
                metadata['error'] = 'File exceeds the 25 MiB limit'
            if metadata['available']:
                hashed_bytes += metadata['size']
                if hashed_bytes > MAX_SCAN_BYTES:
                    raise ValueError('stage directory exceeds the 250 MiB content inspection budget; use a smaller directory')
                metadata.update(_status(definition, resolved, _connection))
            else:
                metadata.update(ingestion_status='new', last_run_id=None)
            files.append(metadata)
    except OSError:
        raise ValueError('stage directory is unavailable') from None
    return dict(files=sorted(files, key=lambda item: item['name']), max_file_bytes=MAX_FILE_BYTES,
                max_directory_entries=MAX_DIRECTORY_ENTRIES, max_scan_bytes=MAX_SCAN_BYTES)


def _preview(definition, name, version):
    try:
        path, metadata = _selected(definition, name, version)
    except OSError:
        raise ValueError('stage file is unavailable') from None
    try:
        result = lake_ingest.preview(str(path), format=definition['format'], limit=10)
    except (csv.Error, InvalidOperation, UnicodeError, ValueError):
        # Parser exceptions can include source rows or local paths.
        raise ValueError('stage file could not be parsed; check its format') from None
    except OSError:
        raise ValueError('stage file is unavailable') from None
    _selected(definition, name, version)
    result.update(_status(definition, path))
    result.update(file=name, version=metadata['version'],
                  stage={key: value for key, value in definition.items() if key != '_revision'})
    if definition['mode'] == 'upsert':
        keys = [lake_ingest.sanitize(part) for part in definition['key'].split(',') if part.strip()]
        if any(key not in {column['name'] for column in result['columns']} for key in keys):
            raise ValueError('upsert key must match input columns')
    return result


def preview_file(name, file, version, actor, role):
    _identity(actor, role)
    return _preview(_owned(name, actor), file, version)


def _legacy_history(name, connection=None):
    with (nullcontext(connection) if connection is not None else jobs._db()) as connection:
        for row in connection.execute('SELECT definition FROM runs WHERE job_name=?', ('stage:' + name,)):
            if any(step.get('action') == 'stage_import' and 'content_key' not in step
                   for step in json.loads(row['definition']).get('steps', [])):
                return True
    return False


def _require_fingerprints(name, connection=None):
    if _legacy_history(name, connection):
        raise ValueError('Legacy stage history has no content fingerprint; review prior imports and register a new stage before importing')


def _content_key(definition, path, data=None):
    if data is None:
        with path.open('rb') as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError('stage file exceeds the 25 MiB limit')
    effective = lake_ingest.FORMATS[path.suffix.lower()] if definition['format'] == 'auto' else definition['format']
    # TSV parsing uses the suffix, even when an explicit csv format is supplied.
    dialect = 'tsv' if effective == 'csv' and path.suffix.lower() == '.tsv' else effective
    semantic = [definition['name'], definition['table'], definition['mode'], definition['format'],
                [lake_ingest.sanitize(k) for k in definition.get('key', '').split(',') if k.strip()], dialect]
    return hashlib.sha256(json.dumps(semantic, separators=(',', ':')).encode() + b'\0' + data).hexdigest()


def _status(definition, path, connection=None):
    key = _content_key(definition, path)
    with (nullcontext(connection) if connection is not None else jobs._db()) as connection:
        row = connection.execute('SELECT * FROM stage_files WHERE stage_name=? AND content_key=?',
                                 (definition['name'], key)).fetchone()
    return dict(ingestion_status=row['status'] if row else 'new', last_run_id=row['last_run_id'] if row else None)


def _step(definition, file, version, retry=False):
    path, source = _selected(definition, file, version)
    source['path'] = str(path)
    return dict(name='Import ' + file, action='stage_import', stage=definition, source=source,
                content_key=_content_key(definition, path), retry=retry)


def _claim(connection, run_id, steps):
    for position, step in enumerate(steps, 1):
        if step['action'] != 'stage_import':
            continue
        name, key = step['stage']['name'], step['content_key']
        old = connection.execute('SELECT status FROM stage_files WHERE stage_name=? AND content_key=?', (name, key)).fetchone()
        if old and (old['status'] not in {'failed', 'unknown'} or not step.get('retry', False)):
            raise jobs.JobConflict('Content already imported or attempted; failed/unknown content requires explicit retry')
        connection.execute("INSERT INTO stage_files VALUES (?,?,?,?,?) ON CONFLICT(stage_name,content_key) DO UPDATE SET status=excluded.status,last_run_id=excluded.last_run_id,position=excluded.position",
                           (name, key, 'queued', run_id, position))


def _scan_steps(definition, actor, role, connection=None):
    _require_fingerprints(definition['name'], connection)
    if definition['mode'] == 'replace':
        raise ValueError('Bulk stage scans require append or upsert mode')
    files = list_files(definition['name'], actor, role, _definition=definition, _connection=connection)['files']
    steps, seen = [], set()
    for source in files:
        if (not source['available'] or source['ingestion_status'] != 'new' or
                (datetime.now(timezone.utc) - datetime.fromisoformat(source['modified_at'])).total_seconds() < 30):
            continue
        step = _step(definition, source['name'], source['version'])
        if step['content_key'] in seen:
            continue
        seen.add(step['content_key'])
        steps.append(step)
        if len(steps) == 10:
            break
    return steps or [dict(name='No new stable files', action='stage_scan_empty')]


def _scheduled(name, actor, role, preflight=False, connection=None):
    _identity(actor, role)
    row = connection.execute('SELECT * FROM stages WHERE name=? AND owner=?', (name, actor)).fetchone()
    if row is None:
        raise PermissionError('Stage ownership unavailable; schedule paused')
    definition = json.loads(row['definition'])
    definition['_revision'] = row['updated_at']
    if definition['mode'] == 'replace':
        raise ValueError('Automatic stage scans require append or upsert mode')
    steps = _scan_steps(definition, actor, role, connection) if preflight else []
    return dict(name='stage:' + name, stage=definition, steps=steps), steps


def _submit(name, actor, role, file=None, version=None, retry=False):
    _identity(actor, role)
    if type(retry) is not bool:
        raise ValueError('retry must be boolean')
    definition = _owned(name, actor)
    _require_fingerprints(name)
    if file is None:
        _revision(definition, version)
    lock = jobs._lock()
    if lock is None:
        raise jobs.JobConflict('Another job is active; no waiting queue')
    try:
        if file is None:
            steps = _scan_steps(definition, actor, role)
        else:
            _preview(definition, file, version)
            steps = [_step(definition, file, version, retry)]
        snapshot = dict(name='stage:' + name, stage=definition, steps=steps)
        if file is not None:
            snapshot['source'] = steps[0]['source']
        with jobs._db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            current = connection.execute('SELECT updated_at FROM stages WHERE name=? AND owner=?', (name, actor)).fetchone()
            if current is None or current['updated_at'] != definition['_revision']:
                raise ValueError('stage changed; refresh files and preview again')
            jobs._lost(connection)
            run_id = jobs._insert_run(connection, snapshot, {}, steps, actor, role, trigger='stage')
        result = jobs.get_run(run_id, actor, role)
        jobs._start_worker(run_id, steps, actor, role, lock, request_auth.shared_mode(), snapshot['name'],
                           'shared' if request_auth.shared_mode() else 'local')
        return result
    except Exception:
        lock.close()
        raise


def submit_import(name, file, version, actor, role, retry=False):
    return _submit(name, actor, role, file, version, retry)


def _revision(definition, version):
    if not isinstance(version, str) or version != definition['_revision']:
        raise ValueError('stage changed; refresh stages before enabling or scanning')


def submit_scan(name, actor, role, version):
    return _submit(name, actor, role, version=version)


def _get_schedule(name, actor):
    import schedule_resident as scheduler
    with jobs._db() as connection:
        row = connection.execute('SELECT * FROM schedules WHERE name=?', ('stage:' + name,)).fetchone()
        if row is None:
            return None
        result = scheduler._schedule(row, actor, connection)
    result['name'] = name
    return result


def save_schedule(payload, actor, role):
    import schedule_resident as scheduler
    _identity(actor, role)
    if not isinstance(payload, dict) or payload.keys() - (scheduler.SCHEDULE_FIELDS | {'name', 'enabled', 'version'}):
        raise ValueError('invalid stage schedule fields')
    name = payload.get('name')
    definition = _owned(name, actor)
    pause = payload.keys() == {'name', 'enabled'} and payload['enabled'] is False
    if not pause:
        now = scheduler._utc()
        schedule = scheduler._validate_schedule({'kind': 'interval', **payload}, now)
        enabled = payload.get('enabled', False)
        if enabled:
            _revision(definition, payload.get('version'))
            _require_fingerprints(name)
        if enabled and definition['mode'] == 'replace':
            raise ValueError('Automatic stage scans require append or upsert mode')
        scheduler._authorize(dict(owner=actor, role=role, auth_mode=scheduler._mode()))
    with jobs._db() as connection:
        connection.execute('BEGIN IMMEDIATE')
        current = connection.execute('SELECT updated_at FROM stages WHERE name=? AND owner=?', (name, actor)).fetchone()
        if current is None or (not pause and enabled and current['updated_at'] != definition['_revision']):
            raise ValueError('stage changed; refresh stages before enabling or scanning')
        if pause:
            old = connection.execute('SELECT owner FROM schedules WHERE name=?', ('stage:' + name,)).fetchone()
            if old is None:
                raise jobs.JobNotFound('Schedule not found')
            if old['owner'] != actor:
                raise PermissionError('Only the schedule owner can pause it')
            connection.execute('UPDATE schedules SET enabled=0,next_run_at=NULL WHERE name=?', ('stage:' + name,))
        else:
            scheduler._persist(connection, 'stage:' + name, actor, role, schedule, {}, enabled, now)
    return _get_schedule(name, actor)


def _execute_import(step):
    definition, source = step['stage'], step['source']
    try:
        path, _ = _selected(definition, source['name'], source['version'])
    except OSError:
        raise ValueError('stage file is unavailable') from None
    if str(path) != source['path']:
        raise ValueError('stage file path changed; refresh files and preview again')
    with path.open('rb') as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if _content_key(definition, path, data) != step['content_key']:
        raise ValueError('stage file content changed; refresh files and preview again')
    with tempfile.TemporaryDirectory(prefix='shirokuma-stage-') as directory:
        snapshot = Path(directory) / ('input' + path.suffix)
        snapshot.write_bytes(data)
        # Parse and ingest exactly the bytes claimed in the content ledger.
        _preview(dict(definition, directory=directory), snapshot.name,
                 _file(dict(definition, directory=directory), snapshot.name)[1]['version'])
        with jobs._db() as connection:
            connection.execute("UPDATE stage_files SET status='running' WHERE stage_name=? AND content_key=?",
                               (definition['name'], step['content_key']))
        try:
            return lake_ingest.ingest(str(snapshot), table=definition['table'], mode=definition['mode'],
                                      format=definition['format'], key=definition.get('key'), atomic_append=True)
        except Exception:
            raise jobs.StepUnknown('Stage ingestion outcome unknown; inspect target before explicit retry') from None
