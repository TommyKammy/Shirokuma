#!/usr/bin/env python3
"""Bundle warehouse definitions, logical data, dbt models and application state.

Restore validates the whole bundle before writing state/models. Catalog SQL and
data are retained in the archive for operator-controlled warehouse recovery.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import gzip
import io
import json
import os
from pathlib import Path, PurePosixPath
import sys
import subprocess
import tarfile
import tempfile
import zlib

import warehouse_resident as warehouse

ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = ROOT / 'tools/local_experiment/dataops/dbt_persistent/models'
STATE_FILES = ('wap_models.json', 'ai_config.json')
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
    """Create a private tar.gz archive; query failures never publish a bundle."""
    files = {}
    for name in STATE_FILES:
        source = state_dir() / name
        if source.exists():
            files['state/' + name] = source.read_bytes()
    for source in sorted(MODELS_DIR.rglob('*.sql')):
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


def restore(archive_path, target_state_dir=None, dry_run=False):
    """Validate all payloads before restoring state and models (never execute SQL)."""
    files, manifest = _validated_files(archive_path)
    target = Path(target_state_dir) if target_state_dir is not None else state_dir()
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
        for destination, data in restored:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
            try:
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
    return dict(status='success', restored_files=[str(path) for path, _ in restored],
                manifest=manifest, dry_run=dry_run)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('backup')
    command.add_argument('--output')
    command.add_argument('--no-tables', action='store_true')
    command = commands.add_parser('restore')
    command.add_argument('--input', required=True)
    command.add_argument('--target-dir')
    command.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'backup':
            result = backup(args.output, include_tables=not args.no_tables)
        else:
            result = restore(args.input, args.target_dir, args.dry_run)
        print(json.dumps(result))
        return 0
    except (RuntimeError, OSError, EOFError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
