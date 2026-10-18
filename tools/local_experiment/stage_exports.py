"""Bounded, manually submitted exports in a registered stage's exports directory."""
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import unicodedata

import bears_resident as bears
import jobs_resident as jobs
import lake_ingest
import request_auth
import stages_resident as stages

MAX_ROWS = 100000
MAX_FILE_BYTES = 25 * 1024 * 1024
FORMATS = {'csv', 'jsonl', 'parquet'}


def _filename(file, format):
    if (not isinstance(format, str) or format not in FORMATS or
            not isinstance(file, str) or not stages._filename(file) or
            any(unicodedata.category(char).startswith('C') for char in file) or
            Path(file).suffix.lower() != '.' + format):
        raise ValueError('select a direct CSV, JSONL, or Parquet filename matching the format')
    return file


def _directory(definition, create=False):
    root = Path(definition['directory']).resolve()
    directory = (root / 'exports').resolve()
    if directory.parent != root or not root.is_dir():
        raise ValueError('exports directory is unavailable or outside the stage directory')
    if create:
        directory.mkdir(exist_ok=True)
    if directory.exists() and not directory.is_dir():
        raise ValueError('exports directory is unavailable')
    return directory


def list_exports(name, actor, role):
    stages._identity(actor, role)
    definition = stages._owned(name, actor)
    directory = _directory(definition)
    files = []
    if directory.exists():
        for count, path in enumerate(directory.iterdir(), 1):
            if count > stages.MAX_DIRECTORY_ENTRIES:
                raise ValueError('exports directory exceeds 1000 entries; use a smaller directory')
            try:
                _filename(path.name, path.suffix.lower().lstrip('.'))
                if path.resolve().parent != directory or not path.is_file():
                    continue
                stat = path.stat()
            except (OSError, ValueError):
                continue
            files.append(dict(name=path.name, size=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()))
    return dict(directory=str(directory), files=sorted(files, key=lambda item: item['name']),
                limits=dict(max_rows=MAX_ROWS, max_file_bytes=MAX_FILE_BYTES,
                            max_directory_entries=stages.MAX_DIRECTORY_ENTRIES))


def _current(connection, definition, actor):
    row = connection.execute('SELECT updated_at FROM stages WHERE name=? AND owner=?',
                             (definition['name'], actor)).fetchone()
    if row is None or row['updated_at'] != definition['_revision']:
        raise ValueError('stage changed; refresh stages before exporting')


def _authorize(step):
    actor, role = step['actor'], step['role']
    if step['auth_mode'] != ('shared' if request_auth.shared_mode() else 'local'):
        raise PermissionError('Authentication mode changed while the export was active')
    stages._identity(actor, role)
    if step['auth_mode'] == 'local':
        jobs.wr.resolve_role(actor, role)


def submit_export(name, table, format, file, version, actor, role):
    stages._identity(actor, role)
    definition = stages._owned(name, actor)
    if not isinstance(version, str) or version != definition['_revision']:
        raise ValueError('stage changed; refresh stages before exporting')
    table = bears.qualified_table(table)
    _filename(file, format)
    directory = _directory(definition)
    if (directory / file).exists():
        raise ValueError('export filename already exists; choose a new filename')
    lock = jobs._lock()
    if lock is None:
        raise jobs.JobConflict('Another job is active; no waiting queue')
    try:
        mode = 'shared' if request_auth.shared_mode() else 'local'
        step = dict(name='Export ' + file, action='stage_export', stage=definition,
                    table=table, format=format, file=file, actor=actor, role=role, auth_mode=mode)
        snapshot = dict(name='stage:' + name, stage=definition, steps=[step])
        with jobs._db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            _current(connection, definition, actor)
            _authorize(step)
            jobs._lost(connection)
            run_id = jobs._insert_run(connection, snapshot, {}, [step], actor, role, trigger='stage')
        result = jobs.get_run(run_id, actor, role)
        jobs._start_worker(run_id, [step], actor, role, lock, mode == 'shared', snapshot['name'], mode)
        return result
    except Exception:
        lock.close()
        raise


def _execute_export(step):
    definition = step['stage']
    _authorize(step)
    with jobs._db() as connection:
        _current(connection, definition, step['actor'])
    _filename(step['file'], step['format'])
    directory = _directory(definition, create=True)
    destination = directory / step['file']
    if destination.exists():
        raise ValueError('export filename already exists; choose a new filename')
    temporary = None
    published = False
    try:
        with tempfile.NamedTemporaryFile(prefix='.stage-export-', suffix='.tmp', dir=directory, delete=False) as stream:
            temporary = Path(stream.name)
        report = lake_ingest.export_table(step['table'], file=str(temporary), format=step['format'],
                                          max_rows=MAX_ROWS + 1, full=False)
        if (type(report.get('row_count')) is not int or report['row_count'] > MAX_ROWS or
                report['row_count'] < 0 or report['row_count'] != report.get('total_rows') or
                report.get('truncated') is not False):
            raise ValueError('export must contain the complete table within the 100000 row limit')
        size = temporary.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ValueError('export exceeds the 25 MiB limit')
        with jobs._db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            _current(connection, definition, step['actor'])
            _authorize(step)
            if _directory(definition) != directory:
                raise ValueError('exports directory changed before publication')
            try:
                os.link(temporary, destination)
            except FileExistsError:
                raise ValueError('export filename already exists; choose a new filename') from None
            published = True
        return dict(action='export', result='success', table=step['table'], file=step['file'],
                    format=step['format'], size=size, row_count=report['row_count'])
    except Exception as exc:
        if published:
            unknown = jobs.StepUnknown('Export published but bookkeeping failed; outcome unknown; do not replay')
            unknown.summary = dict(action='export', table=step['table'], file=step['file'],
                                   format=step['format'], size=size, row_count=report['row_count'])
            raise unknown from exc
        raise
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
