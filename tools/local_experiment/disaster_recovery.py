#!/usr/bin/env python3
"""Bundle warehouse definitions, logical data, dbt models and application state.

Restore validates the whole bundle before writing state/models. Opt-in warehouse
recovery also recreates catalog objects and restores logical table data.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import gzip
import io
import json
import os
import re
import sqlite3
from pathlib import Path, PurePosixPath
import sys
import subprocess
import tarfile
import tempfile
import zlib

import warehouse_resident as warehouse

ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = ROOT / 'tools/local_experiment/dataops/dbt_persistent/models'
STATE_FILES = ('wap_models.json', 'ai_config.json', 'jobs.sqlite3')
VERSION = 1


class BackupError(RuntimeError):
    """The backup could not be completed or validated."""


def state_dir():
    return Path(os.environ.get('SHIROKUMA_STATE_DIR', ROOT / 'var/shirokuma'))


def _json(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode('utf-8')


def _identifier(*parts):
    return '.'.join('"' + part.replace('"', '""') + '"' for part in parts)


def _catalog(include_tables):
    files, tables = {}, []
    # A single connection avoids opening a new kubectl tunnel per statement.
    with warehouse.trino() as url:
        def query(sql):
            result = warehouse.execute(url, sql, {'X-Trino-User': 'shirokuma-backup'},
                                       max_rows=sys.maxsize)
            if result.get('truncated'):
                raise BackupError('Refusing truncated backup: ' + sql)
            return result

        schemas = query('SHOW SCHEMAS FROM iceberg')['rows']
        for (schema,) in schemas:
            if schema not in warehouse.WRITABLE_SCHEMAS:
                continue
            qualified = _identifier('iceberg', schema)
            files[f'catalog/{schema}/schema.sql'] = (
                query('SHOW CREATE SCHEMA ' + qualified)['rows'][0][0] + ';\n').encode()
            relations = query(
                'SELECT table_name, table_type FROM iceberg.information_schema.tables '
                'WHERE table_schema = ' + warehouse.literal(schema))['rows']
            for index, (name, kind) in enumerate(relations):
                table = _identifier('iceberg', schema, name)
                prefix = f'catalog/{schema}/{index:06d}'
                ddl = prefix + '.sql'
                operation = 'SHOW CREATE VIEW ' if kind == 'VIEW' else 'SHOW CREATE TABLE '
                files[ddl] = (query(operation + table)['rows'][0][0] + ';\n').encode()
                entry = dict(catalog='iceberg', schema=schema, table=name, type=kind, ddl=ddl)
                if include_tables and kind != 'VIEW':
                    data = f'data/{schema}/{index:06d}.json'
                    result = query('SELECT * FROM ' + table)
                    files[data] = _json(dict(columns=result['columns'], rows=result['rows']))
                    entry['data'] = data
                tables.append(entry)
    return files, tables


def backup(output_path=None, include_tables=True):
    if warehouse._profile_active():
        raise ValueError('Legacy warehouse backup is unsupported with the database profile')
    include_ledger = (state_dir() / 'jobs.sqlite3').exists()
    with warehouse.warehouse_operation():
        return _backup(output_path, include_tables, include_ledger)


def _backup(output_path, include_tables, include_ledger):
    """Create a private tar.gz archive; query failures never publish a bundle."""
    files = {}
    for name in STATE_FILES:
        if name == 'jobs.sqlite3' and not include_ledger:
            continue
        source = state_dir() / name
        if source.exists():
            if name == 'jobs.sqlite3':
                # Capture committed transactions, including any live WAL,
                # without copying an inconsistent database/journal pair.
                with tempfile.TemporaryDirectory(prefix='shirokuma-jobs-backup-') as temporary:
                    snapshot = Path(temporary) / name
                    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as live:
                        with closing(sqlite3.connect(snapshot)) as saved:
                            live.backup(saved)
                    files['state/' + name] = snapshot.read_bytes()
            else:
                files['state/' + name] = source.read_bytes()
    for source in sorted(MODELS_DIR.rglob('*')):
        if not source.is_file() or source.suffix not in {'.sql', '.yml', '.yaml'}:
            continue
        files['models/' + source.relative_to(MODELS_DIR).as_posix()] = source.read_bytes()
    catalog, tables = _catalog(include_tables)
    files.update(catalog)
    now = datetime.now(timezone.utc)
    manifest = dict(version=VERSION, timestamp=now.isoformat(), tables=tables,
                    include_tables=include_tables,
                    checksums={name: hashlib.sha256(data).hexdigest()
                               for name, data in files.items()})
    # The manifest describes every payload file; it cannot checksum itself.
    files['manifest.json'] = _json(manifest)
    output = Path(output_path) if output_path is not None else (
        state_dir() / 'backups' / now.strftime('shirokuma-%Y%m%dT%H%M%S%fZ.tar.gz'))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        with tarfile.open(temporary, 'w:gz') as archive:
            for name, data in files.items():
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(data), 0o600
                archive.addfile(info, io.BytesIO(data))
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return dict(status='success', archive=str(output), manifest=manifest, files=list(files))


def _validated_files(archive_path):
    try:
        files = {}
        # Read through the gzip trailer as tar readers may stop at end-of-tar
        # before checking the compression CRC and length.
        with gzip.open(archive_path, 'rb') as stream:
            payload = stream.read()
        with tarfile.open(fileobj=io.BytesIO(payload), mode='r:') as archive:
            for member in archive:
                path = PurePosixPath(member.name)
                if (not member.isfile() or path.is_absolute() or '..' in path.parts
                        or path.as_posix() != member.name or member.name in files):
                    raise BackupError('Unsafe or duplicate archive member: ' + member.name)
                allowed = (member.name == 'manifest.json'
                           or member.name in {'state/' + name for name in STATE_FILES}
                           or (len(path.parts) > 1 and path.parts[0] in ('models', 'catalog', 'data')))
                if not allowed:
                    raise BackupError('Unexpected archive member: ' + member.name)
                files[member.name] = archive.extractfile(member).read()
        manifest = json.loads(files.pop('manifest.json'))
        if manifest['version'] != VERSION or not isinstance(manifest['tables'], list):
            raise BackupError('Unsupported backup manifest')
        checksums = manifest['checksums']
        if not isinstance(checksums, dict) or set(checksums) != set(files):
            raise BackupError('Manifest file inventory mismatch')
        for name, data in files.items():
            if hashlib.sha256(data).hexdigest() != checksums[name]:
                raise BackupError('Checksum mismatch: ' + name)
        return files, manifest
    except BackupError:
        raise
    except (OSError, EOFError, tarfile.TarError, zlib.error, ValueError, KeyError, TypeError) as exc:
        raise BackupError('Invalid backup archive or manifest') from exc


def _validate_restore_type(sql_type, table, column):
    """Reject temporal JSON casts before any recovery statements are executed."""
    # Quoted field names are identifiers, even when named "date" or "timestamp".
    types = re.sub(r'"(?:[^"]|"")*"', '_field', sql_type)
    if (re.search(r'\b(?:row|map)\s*\(', types, re.I) and
            re.search(r'\b(?:date|timestamp)\s*(?=[,)]|$|\(\s*\d|with\b|without\b)', types, re.I)):
        raise BackupError(
            f'Unsupported recovery type for {table}.{column}: {sql_type}. '
            'Trino 483 cannot cast JSON to ROW/MAP containing DATE or TIMESTAMP; '
            'restore was stopped before executing SQL.')


def _data_literal(value, sql_type):
    """Render Trino JSON result values with their original column type."""
    if value is None:
        return 'NULL'
    if isinstance(value, bool):
        return 'TRUE' if value else 'FALSE'
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return 'CAST(' + warehouse.literal(value) + ' AS ' + sql_type + ')'
    if isinstance(value, list) and sql_type.lower().startswith('array('):
        element_type = sql_type[6:-1]
        elements = ', '.join(_data_literal(item, element_type) for item in value)
        return 'CAST(ARRAY[' + elements + '] AS ' + sql_type + ')'
    if isinstance(value, (list, dict)):
        # Trino supports casting JSON arrays/objects to typed MAP and ROW values.
        return ('CAST(json_parse(' + warehouse.literal(json.dumps(value, ensure_ascii=False)) +
                ') AS ' + sql_type + ')')
    raise BackupError('Unsupported data value for type: ' + sql_type)


def _warehouse_plan(files, manifest):
    """Prepare SQL before any writes, using manifest names rather than filenames."""
    schemas, tables, data, statements = [], [], [], []
    for name in sorted(files):
        path = PurePosixPath(name)
        if len(path.parts) == 3 and path.parts[0] == 'catalog' and path.name == 'schema.sql':
            schemas.append(path.parts[1])
            ddl = files[name].decode().strip().rstrip(';')
            statements.append(re.sub(r'^CREATE\s+SCHEMA\s+(?:IF\s+NOT\s+EXISTS\s+)?',
                                     'CREATE SCHEMA IF NOT EXISTS ', ddl, count=1, flags=re.I))
    # Base tables must exist before views that reference them.
    for entry in sorted(manifest['tables'], key=lambda t: t['type'] == 'VIEW'):
        table = _identifier(entry['catalog'], entry['schema'], entry['table'])
        tables.append('.'.join((entry['catalog'], entry['schema'], entry['table'])))
        statements.append(files[entry['ddl']].decode().strip().rstrip(';'))
    for entry in manifest['tables']:
        if not entry.get('data'):
            continue
        payload = json.loads(files[entry['data']])
        columns, rows = payload['columns'], payload['rows']
        table = _identifier(entry['catalog'], entry['schema'], entry['table'])
        for column in columns:
            _validate_restore_type(column['type'], table, column['name'])
        data.append(dict(table='.'.join((entry['catalog'], entry['schema'], entry['table'])),
                         file=entry['data'], rows=len(rows)))
        column_sql = ', '.join(_identifier(c['name']) for c in columns)
        for offset in range(0, len(rows), 500):
            values = []
            for row in rows[offset:offset + 500]:
                if len(row) != len(columns):
                    raise BackupError('Data column count mismatch: ' + entry['data'])
                cells = []
                for value, column in zip(row, columns):
                    cells.append(_data_literal(value, column['type']))
                values.append('(' + ', '.join(cells) + ')')
            statements.append('INSERT INTO ' + table + ' (' + column_sql + ') VALUES ' + ', '.join(values))
    return schemas, tables, data, statements


def _pause_restored_schedules(data):
    """A recovered copy must not start recurring writes without re-enabling it."""
    with tempfile.TemporaryDirectory(prefix='shirokuma-jobs-restore-') as directory:
        snapshot = Path(directory) / 'jobs.sqlite3'
        snapshot.write_bytes(data)
        try:
            with closing(sqlite3.connect(snapshot)) as connection:
                if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schedules'").fetchone():
                    connection.execute('UPDATE schedules SET enabled=0,next_run_at=NULL')
                    connection.commit()
        except sqlite3.DatabaseError as exc:
            raise BackupError('Invalid job ledger in backup') from exc
        return snapshot.read_bytes()


def restore(archive_path, target_state_dir=None, dry_run=False, restore_warehouse=False):
    if restore_warehouse and not dry_run:
        with warehouse.warehouse_operation():
            return _restore(archive_path, target_state_dir, dry_run, restore_warehouse)
    return _restore(archive_path, target_state_dir, dry_run, restore_warehouse)


def _restore(archive_path, target_state_dir=None, dry_run=False, restore_warehouse=False):
    """Validate the bundle and optionally recover warehouse DDL and logical rows."""
    files, manifest = _validated_files(archive_path)
    schemas, tables, warehouse_data, statements = [], [], [], []
    if restore_warehouse:
        try:
            schemas, tables, warehouse_data, statements = _warehouse_plan(files, manifest)
        except (KeyError, TypeError, ValueError) as exc:
            raise BackupError('Invalid warehouse recovery payload') from exc
    target = Path(target_state_dir) if target_state_dir is not None else state_dir()
    if 'state/jobs.sqlite3' in files and (target / 'jobs.sqlite3').exists():
        raise BackupError('Restore jobs to an empty state directory; an existing job ledger must not be overwritten')
    if 'state/jobs.sqlite3' in files:
        files['state/jobs.sqlite3'] = _pause_restored_schedules(files['state/jobs.sqlite3'])
    restored = []
    for name, data in files.items():
        path = PurePosixPath(name)
        if path.parts[0] == 'state':
            destination = target / path.relative_to('state')
        elif path.parts[0] == 'models':
            destination = MODELS_DIR / path.relative_to('models')
        else:
            continue
        # Standard resolved-path containment also rejects existing escaping symlinks.
        root = target if path.parts[0] == 'state' else MODELS_DIR
        if not destination.resolve().is_relative_to(root.resolve()):
            raise BackupError('Restore destination escapes target: ' + name)
        restored.append((destination, data))
    if not dry_run:
        if restore_warehouse:
            with warehouse.trino() as url:
                views = []
                for statement in statements:
                    if re.match(r'^CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\b', statement, re.I):
                        views.append(statement)
                        continue
                    warehouse.execute(url, statement, {'X-Trino-User': 'shirokuma-restore'})
                while views:
                    pending, failure = [], None
                    for statement in views:
                        try:
                            warehouse.execute(url, statement, {'X-Trino-User': 'shirokuma-restore'})
                        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
                            pending.append(statement)
                            failure = exc
                    if len(pending) == len(views):
                        raise BackupError('Unable to restore dependent views: ' + str(failure)) from failure
                    views = pending
        for destination, data in restored:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
            try:
                if destination == target / 'jobs.sqlite3':
                    try:
                        os.link(temporary, destination)
                    except FileExistsError as exc:
                        raise BackupError('Job ledger appeared during restore; existing ledger was preserved') from exc
                else:
                    temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
    return dict(status='success', restored_files=[str(path) for path, _ in restored],
                manifest=manifest, dry_run=dry_run, restored_schemas=schemas,
                restored_tables=tables, rows_restored=sum(item['rows'] for item in warehouse_data),
                restored_data=warehouse_data)


def main(argv=None):
    import runtime_profile
    runtime_profile.configure_process()
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('backup')
    command.add_argument('--output')
    command.add_argument('--no-tables', action='store_true')
    command = commands.add_parser('restore')
    command.add_argument('--input', required=True)
    command.add_argument('--target-dir')
    command.add_argument('--dry-run', action='store_true')
    command.add_argument('--restore-warehouse', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'backup':
            result = backup(args.output, include_tables=not args.no_tables)
        else:
            result = restore(args.input, args.target_dir, args.dry_run, args.restore_warehouse)
        print(json.dumps(result))
        return 0
    except (RuntimeError, OSError, EOFError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
