"""Named local directories for explicit single-file imports through the jobs worker.

Versions are metadata checks, not content hashes or exactly-once guarantees.
The SQLite ledger and Iceberg commit are separate; unknown runs never replay.
"""
import csv
from datetime import datetime, timezone
from decimal import InvalidOperation
import json
from pathlib import Path
import re

import jobs_resident as jobs
import lake_ingest
import request_auth

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_DIRECTORY_ENTRIES = 1000


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
                created_at=row['created_at'], updated_at=row['updated_at'], can_manage=row['owner'] == actor)


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
        return _entry(connection.execute('SELECT * FROM stages WHERE name=?', (name,)).fetchone(), actor)


def list_stages(actor, role):
    _identity(actor, role)
    with jobs._db() as connection:
        return [_entry(row, actor) for row in connection.execute('SELECT * FROM stages ORDER BY name')]


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
            and not name.endswith('~') and Path(name).suffix.lower() in lake_ingest.FORMATS)


def _file(definition, name, version=None):
    if not _filename(name):
        raise ValueError('select a direct, supported stage filename')
    directory = Path(definition['directory']).resolve()
    path = (directory / name).resolve()
    if path.parent != directory or not path.is_file():
        raise ValueError('stage file is unavailable or outside its directory')
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


def list_files(name, actor, role):
    _identity(actor, role)
    definition = _owned(name, actor)
    files = []
    try:
        for count, path in enumerate(Path(definition['directory']).iterdir(), 1):
            if count > MAX_DIRECTORY_ENTRIES:
                raise ValueError('stage directory exceeds 1000 entries; use a smaller directory')
            if not _filename(path.name):
                continue
            try:
                _, metadata = _file(definition, path.name)
            except (OSError, ValueError):
                continue
            metadata['available'] = metadata['size'] <= MAX_FILE_BYTES
            if not metadata['available']:
                metadata['error'] = 'File exceeds the 25 MiB limit'
            files.append(metadata)
    except OSError:
        raise ValueError('stage directory is unavailable') from None
    return dict(files=sorted(files, key=lambda item: item['name']), max_file_bytes=MAX_FILE_BYTES,
                max_directory_entries=MAX_DIRECTORY_ENTRIES)


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


def submit_import(name, file, version, actor, role):
    _identity(actor, role)
    definition = _owned(name, actor)
    lock = jobs._lock()
    if lock is None:
        raise jobs.JobConflict('Another job is active; no waiting queue')
    try:
        _preview(definition, file, version)
        path, source = _selected(definition, file, version)
        source['path'] = str(path)
        step = dict(name='Import ' + file, action='stage_import', stage=definition, source=source)
        snapshot = dict(name='stage:' + name, stage=definition, source=source, steps=[step])
        with jobs._db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            current = connection.execute('SELECT updated_at FROM stages WHERE name=? AND owner=?',
                                         (name, actor)).fetchone()
            if current is None or current['updated_at'] != definition['_revision']:
                raise ValueError('stage changed; refresh files and preview again')
            jobs._lost(connection)
            run_id = jobs._insert_run(connection, snapshot, {}, [step], actor, role, trigger='stage')
        result = jobs.get_run(run_id, actor, role)
        jobs._start_worker(run_id, [step], actor, role, lock, request_auth.shared_mode(), snapshot['name'])
        return result
    except Exception:
        lock.close()
        raise


def _execute_import(step):
    definition, source = step['stage'], step['source']
    try:
        path, _ = _selected(definition, source['name'], source['version'])
    except OSError:
        raise ValueError('stage file is unavailable') from None
    if str(path) != source['path']:
        raise ValueError('stage file path changed; refresh files and preview again')
    return lake_ingest.ingest(str(path), table=definition['table'], mode=definition['mode'],
                              format=definition['format'], key=definition.get('key'), atomic_append=True)
