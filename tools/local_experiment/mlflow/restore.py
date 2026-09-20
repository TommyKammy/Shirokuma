"""Restore a verified local Tracking export into a fresh, inactive private clone.

``restore(manifest_path)`` copies the complete export as inert provenance, then
copies its store into a separate clone and changes only two artifact URI columns.
It neither imports MLflow nor opens the live store. No restart, adoption, retry,
source deletion, migration, or replacement of an existing clone is performed.
"""
import argparse
import base64
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import sys
from urllib.parse import unquote, urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[3]
SOURCE = 'tmp/local-experiment/mlflow-tracking'
DESTINATION = 'tmp/local-experiment/mlflow-restores'
VERSION = '3.16.1'
REVISION = 'b7e2c1a4d9f3'
# Exact sqlite_schema text from pinned 3.16.1 migration
# 1b5f0d9ad7c1_add_workspace_columns_and_catalog.py. The secrets table must be empty.
SECRETS_TRIGGER_SQL = """CREATE TRIGGER prevent_secrets_aad_mutation
BEFORE UPDATE ON secrets
FOR EACH ROW
WHEN OLD.secret_id != NEW.secret_id OR OLD.secret_name != NEW.secret_name
BEGIN
    SELECT RAISE(ABORT, 'secret_id and secret_name are immutable (used as AAD in encryption)');
END"""
MAX_ENTRIES = 4096
MAX_BYTES = 64 * 1024 * 1024
MAX_MANIFEST = 2 * 1024 * 1024
HEX = re.compile(r'[0-9a-f]{64}\Z')
TOKEN = re.compile(r'[0-9a-f]{32}\Z')
TABLE = re.compile(r'[A-Za-z_][A-Za-z_0-9]*\Z')
EXPERIMENTS = {'0': ('Default', '0'),
               '1': ('shirokuma-local-evaluation-receipts-v1', 'evaluations'),
               '2': ('shirokuma-local-spark-benchmark-v1', 'spark-benchmarks')}
TRAINING_EXPERIMENTS = {'3': ('shirokuma-local-supervised-training-v1', 'supervised-training'),
                        '4': ('shirokuma-local-iceberg-training-v1', 'iceberg-training')}
COMPARISON_EXPERIMENTS = {'5': ('shirokuma-local-ridge-comparisons-v1', 'ridge-comparisons')}
TRAINING_PROFILES = {'3': ('training', 'fixed-supervised-regression-v1', 'fixed-ridge-'),
                     '4': ('lake-training', 'fixed-iceberg-regression-v1', 'lake-ridge-')}
TRAINING_RESULT_KEYS = {'schema_version', 'result', 'phase', 'token', 'store_id', 'source_sha256',
                        'experiment_id', 'run_id', 'started_ms', 'training_finished_ms', 'finished_ms',
                        'workload_sha256', 'failure_code'}
TRACKING_TABLES = {'alembic_version', 'experiments', 'experiment_tags', 'runs',
                   'metrics', 'latest_metrics', 'params', 'tags'}
DEFAULT_WORKSPACE = {'name': 'default', 'description': 'Default workspace for legacy resources',
                     'default_artifact_root': None, 'trace_archival_location': None,
                     'trace_archival_retention': None}
RESULT_KEYS = {'schema_version', 'profile', 'token', 'result', 'source_manifest',
               'source_manifest_sha256', 'store_id', 'export', 'clone', 'database',
               'artifacts', 'source_unchanged', 'activation_performed', 'error_code'}


class RestoreError(ValueError):
    """Fixed public error code; raw paths, SQLite errors and data stay private."""


def require(condition, code):
    if not condition:
        raise RestoreError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def close_preserving(close):
    original = sys.exc_info()[1]
    try:
        close()
    except BaseException:
        if original is not None and not isinstance(original, Exception):
            raise original
        raise


def parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'RESTORE_JSON_INVALID')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: require(False, 'RESTORE_JSON_INVALID'))
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, RestoreError):
            raise
        raise RestoreError('RESTORE_JSON_INVALID') from None


def relative(value, *, dot=False):
    require(type(value) is str and len(value) <= 4096 and '\\' not in value
            and '\x00' not in value, 'RESTORE_PATH_INVALID')
    if dot and value == '.':
        return value
    p = PurePosixPath(value)
    require(not p.is_absolute() and str(p) == value and value not in ('', '.')
            and all(x not in ('', '.', '..') for x in value.split('/')), 'RESTORE_PATH_INVALID')
    return value


def fingerprint(s):
    return (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_nlink,
            s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def private(path, directory=False):
    s = path.lstat()
    require((stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode))
            and s.st_uid == os.getuid()
            and stat.S_IMODE(s.st_mode) == (0o700 if directory else 0o600)
            and (directory or s.st_nlink == 1), 'RESTORE_PRIVATE_PATH_INVALID')
    return s


def parents(root, path):
    """Never follow a symlink anywhere from the checkout to an input/output."""
    require(root.is_absolute() and root.resolve() == root, 'RESTORE_PATH_INVALID')
    rel = path.relative_to(root)
    current = root
    for part in rel.parts:
        current /= part
        s = current.lstat()
        require(not stat.S_ISLNK(s.st_mode) and s.st_uid == os.getuid(), 'RESTORE_PRIVATE_PATH_INVALID')
        if current != path:
            require(stat.S_ISDIR(s.st_mode) and not stat.S_IMODE(s.st_mode) & 0o022,
                    'RESTORE_PRIVATE_PATH_INVALID')


def read(path, limit=MAX_BYTES):
    before = private(path)
    require(before.st_size <= limit, 'RESTORE_READ_LIMIT')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        require(fingerprint(os.fstat(fd)) == fingerprint(before), 'RESTORE_SOURCE_CHANGED')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(limit + 1)
        require(len(raw) <= limit and fingerprint(os.fstat(fd)) == fingerprint(before)
                and fingerprint(path.lstat()) == fingerprint(before), 'RESTORE_SOURCE_CHANGED')
        return raw
    finally:
        close_preserving(lambda: os.close(fd))


def inventory(base):
    """Return manifest-compatible inventory plus a stronger within-run seal."""
    rows, seal, total = {}, {}, 0
    def visit(path):
        nonlocal total
        s = path.lstat()
        is_dir = stat.S_ISDIR(s.st_mode)
        private(path, is_dir)
        key = str(path.relative_to(base))
        require(len(rows) < MAX_ENTRIES, 'RESTORE_READ_LIMIT')
        row = {'kind': 'directory' if is_dir else 'file', 'mode': stat.S_IMODE(s.st_mode),
               'mtime_ns': s.st_mtime_ns}
        if not is_dir:
            total += s.st_size
            require(total <= MAX_BYTES, 'RESTORE_READ_LIMIT')
            row.update(size=s.st_size, sha256=digest(read(path)))
        rows[key], seal[key] = row, fingerprint(s)
        if is_dir:
            for child in sorted(path.iterdir()):
                visit(child)
            require(fingerprint(path.lstat()) == fingerprint(s), 'RESTORE_SOURCE_CHANGED')
    visit(base)
    return rows, seal


def validate_export(manifest_path, *, root=ROOT):
    """Validate a stopped-state manifest and its immutable-by-agreement export."""
    root = Path(root)
    path = Path(manifest_path)
    if not path.is_absolute():
        path = root / relative(str(path))
    rel = relative(str(path.relative_to(root)))
    require(rel.startswith('tmp/local-experiment/') and path.name == 'backup-manifest.json',
            'RESTORE_PATH_INVALID')
    parents(root, path)
    manifest_seal = fingerprint(private(path))
    raw = read(path, MAX_MANIFEST)
    manifest = parse(raw)
    require(type(manifest) is dict and set(manifest) == {
        'result', 'observed_at', 'store_id', 'source', 'backup', 'inventory'}
        and manifest['result'] == 'verified' and manifest['source'] == SOURCE,
        'RESTORE_MANIFEST_INVALID')
    try:
        when = datetime.fromisoformat(manifest['observed_at'])
        require(when.tzinfo is not None and str(uuid.UUID(manifest['store_id'])) == manifest['store_id'],
                'RESTORE_MANIFEST_INVALID')
    except (ValueError, TypeError, AttributeError):
        raise RestoreError('RESTORE_MANIFEST_INVALID') from None
    backup_rel = relative(manifest['backup'])
    require(backup_rel.startswith('tmp/local-experiment/')
            and not backup_rel.startswith(SOURCE + '/') and backup_rel != SOURCE
            and not backup_rel.startswith(DESTINATION + '/'), 'RESTORE_PATH_INVALID')
    backup = root / backup_rel
    parents(root, backup)
    require(path.parent == backup.parent, 'RESTORE_PATH_INVALID')
    expected = manifest['inventory']
    require(type(expected) is dict and 0 < len(expected) <= MAX_ENTRIES
            and '.' in expected, 'RESTORE_MANIFEST_INVALID')
    for key, value in expected.items():
        relative(key, dot=True)
        require(type(value) is dict and value.get('kind') in ('file', 'directory'),
                'RESTORE_MANIFEST_INVALID')
        is_file = value['kind'] == 'file'
        require(set(value) == ({'kind', 'mode', 'mtime_ns', 'size', 'sha256'} if is_file else
                              {'kind', 'mode', 'mtime_ns'})
                and type(value['mode']) is int and value['mode'] == (0o600 if is_file else 0o700)
                and type(value['mtime_ns']) is int and value['mtime_ns'] >= 0,
                'RESTORE_MANIFEST_INVALID')
        if is_file:
            require(type(value['size']) is int and 0 <= value['size'] <= MAX_BYTES
                    and type(value['sha256']) is str and HEX.fullmatch(value['sha256']),
                    'RESTORE_MANIFEST_INVALID')
    actual, seal = inventory(backup)
    require(actual == expected, 'RESTORE_EXPORT_MISMATCH')
    old_store = root / SOURCE / 'store'
    owner = parse(read(backup / 'store.json', MAX_MANIFEST))
    require(owner == {'schema_version': 1, 'owner': 'shirokuma-local-mlflow',
                     'store_id': manifest['store_id'], 'mlflow_version': VERSION,
                     'backend_uri': 'sqlite:///' + str(old_store / 'tracking.sqlite'),
                     'artifact_root': str(old_store / 'artifacts'), 'url': 'http://127.0.0.1:18550'}
            and type(owner['schema_version']) is int, 'RESTORE_OWNER_INVALID')
    require(set(p.name for p in (backup / 'store').iterdir()) == {'tracking.sqlite', 'artifacts'},
            'RESTORE_DATABASE_SIDECAR')
    require(fingerprint(private(path)) == manifest_seal and read(path, MAX_MANIFEST) == raw,
            'RESTORE_SOURCE_CHANGED')
    return {'path': path, 'raw': raw, 'manifest': manifest, 'backup': backup,
            'seal': seal, 'manifest_seal': manifest_seal}


def write(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def sync(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        close_preserving(lambda: os.close(fd))


def copy_tree(source, destination, expected):
    destination.mkdir(mode=0o700)
    for key, row in sorted(expected.items(), key=lambda item: (item[0].count('/'), item[0])):
        if key == '.':
            continue
        path = destination / key
        if row['kind'] == 'directory':
            path.mkdir(mode=0o700)
        else:
            raw = read(source / key)
            require(len(raw) == row['size'] and digest(raw) == row['sha256'], 'RESTORE_SOURCE_CHANGED')
            write(path, raw)
            os.utime(path, ns=(row['mtime_ns'], row['mtime_ns']))
    for key, row in sorted(expected.items(), key=lambda item: item[0].count('/'), reverse=True):
        if row['kind'] == 'directory':
            path = destination / key
            os.utime(path, ns=(row['mtime_ns'], row['mtime_ns']))
            sync(path)
    require(inventory(destination)[0] == expected, 'RESTORE_COPY_MISMATCH')


def sql_atom(value):
    if value is None:
        return ['null']
    if type(value) is int:
        return ['integer', str(value)]
    if type(value) is float:
        return ['real', value.hex()]
    if type(value) is str:
        return ['text', value]
    require(type(value) is bytes, 'RESTORE_DATABASE_INVALID')
    return ['blob', base64.b64encode(value).decode('ascii')]


def row_hash(rows):
    # Sorted row encodings retain duplicate multiplicity and SQLite scalar types.
    return digest(canonical(sorted(canonical([sql_atom(x) for x in row]).decode('ascii') for row in rows)))


def database_snapshot(connection):
    require(connection.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
            and not connection.execute('PRAGMA foreign_key_check').fetchall(), 'RESTORE_DATABASE_INVALID')
    schema = connection.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name').fetchall()
    require(all((row[0] in ('table', 'index') and TABLE.fullmatch(row[1])
                 and (row[3] is None or 'CREATE VIRTUAL TABLE' not in row[3].upper()))
                or row == ('trigger', 'prevent_secrets_aad_mutation', 'secrets', SECRETS_TRIGGER_SQL)
                for row in schema),
            'RESTORE_SCHEMA_UNSUPPORTED')
    tables = {}
    for kind, name, _, _ in schema:
        if kind != 'table':
            continue
        cursor = connection.execute('SELECT * FROM "' + name + '"')
        rows = cursor.fetchmany(100001)
        require(len(rows) <= 100000, 'RESTORE_READ_LIMIT')
        tables[name] = {'columns': [x[0] for x in cursor.description], 'rows': rows}
    require(TRACKING_TABLES | {'workspaces'} <= tables.keys(), 'RESTORE_SCHEMA_UNSUPPORTED')
    require(tables['alembic_version']['columns'] == ['version_num']
            and tables['alembic_version']['rows'] == [(REVISION,)], 'RESTORE_SCHEMA_UNSUPPORTED')
    require([dict(zip(tables['workspaces']['columns'], row)) for row in tables['workspaces']['rows']]
            == [DEFAULT_WORKSPACE], 'RESTORE_PROFILE_UNSUPPORTED')
    require(all(not table['rows'] for name, table in tables.items()
                if name not in TRACKING_TABLES | {'workspaces'}), 'RESTORE_PROFILE_UNSUPPORTED')
    require(not any(row[0] == 'trigger' for row in schema)
            or ('secrets' in tables and not tables['secrets']['rows']), 'RESTORE_PROFILE_UNSUPPORTED')
    return schema, tables


def artifact_suffix(uri, old_root):
    require(type(uri) is str, 'RESTORE_ARTIFACT_URI_INVALID')
    parsed = urlsplit(uri)
    require(parsed.scheme == 'file' and not parsed.netloc and not parsed.query and not parsed.fragment,
            'RESTORE_ARTIFACT_URI_INVALID')
    decoded = unquote(parsed.path, errors='strict')
    path = Path(decoded)
    require(path.is_absolute() and path.as_uri() == uri and '\x00' not in decoded
            and '\\' not in decoded and all(p not in ('.', '..') for p in decoded.split('/')),
            'RESTORE_ARTIFACT_URI_INVALID')
    try:
        suffix = str(path.relative_to(old_root))
    except ValueError:
        raise RestoreError('RESTORE_ARTIFACT_URI_INVALID') from None
    return relative(suffix)


def training_receipt(run, key, tags, export, artifact, store_id):
    """Bind a finished run to its inert, exported training evidence only."""
    code = 'RESTORE_TRAINING_INVALID'
    require(len(tags) == 6 and len({item['key'] for item in tags}) == 6, code)
    values = {item['key']: item['value'] for item in tags}
    attempt = values.get('shirokuma.attempt')
    require(type(attempt) is str and TOKEN.fullmatch(attempt), code)
    folder, profile, prefix = TRAINING_PROFILES[key]
    directory = export / folder / attempt
    require(directory.is_dir(), code)
    private(directory, True)
    require({path.name for path in directory.iterdir()} == {'result.json', 'workload.json'}, code)
    receipt = parse(read(directory / 'result.json', MAX_MANIFEST))
    require(type(receipt) is dict and set(receipt) == TRAINING_RESULT_KEYS
            and type(receipt['schema_version']) is int and receipt['schema_version'] == 1
            and receipt['result'] == receipt['phase'] == 'verified' and receipt['failure_code'] is None
            and receipt['token'] == attempt and receipt['run_id'] == run['run_uuid']
            and receipt['experiment_id'] == key and receipt['store_id'] == store_id, code)
    require(all(type(receipt[name]) is int and 0 < receipt[name] <= 2**53
                for name in ('started_ms', 'training_finished_ms', 'finished_ms'))
            and receipt['started_ms'] <= receipt['training_finished_ms'] <= receipt['finished_ms']
            and type(run['start_time']) is int and run['start_time'] == receipt['started_ms']
            and type(run.get('end_time')) is int and run['end_time'] == receipt['finished_ms'], code)
    hashes = receipt['source_sha256']
    require(type(hashes) is dict and 0 < len(hashes) <= 64
            and all(type(name) is str and re.fullmatch(r'[A-Za-z0-9_./-]{1,128}', name)
                    and type(value) is str and HEX.fullmatch(value) for name, value in hashes.items()), code)
    expected = {'shirokuma.owner': TRAINING_EXPERIMENTS[key][0], 'shirokuma.store_id': store_id,
                'shirokuma.attempt': attempt, 'shirokuma.workload_sha256': receipt['workload_sha256'],
                'shirokuma.time_semantics': 'actual-local-training-and-tracking',
                'mlflow.runName': prefix + attempt[:8]}
    require(values == expected and run.get('name') == expected['mlflow.runName'], code)
    raw = read(directory / 'workload.json', 128 * 1024)
    workload = parse(raw)
    keys = {'schema_version', 'profile', 'dataset', 'split', 'params', 'model', 'baseline',
            'metrics', 'test_predictions', 'hashes', 'versions'} | ({'lake_input'} if key == '4' else set())
    require(type(workload) is dict and set(workload) == keys
            and type(workload['schema_version']) is int and workload['schema_version'] == 1
            and workload['profile'] == profile, code)
    try:
        require(raw == canonical(workload) + b'\n', code)
    except (ValueError, OverflowError):
        raise RestoreError(code) from None
    require(type(receipt['workload_sha256']) is str and HEX.fullmatch(receipt['workload_sha256'])
            and digest(raw) == receipt['workload_sha256'] and read(artifact, 128 * 1024) == raw, code)
    return attempt


def comparison_plan(records, export, clone_artifacts, store_id):
    """Verify imported comparisons from the inert export, without SDK or fit."""
    code = 'RESTORE_COMPARISON_INVALID'
    try:
        code_root = str(Path(__file__).resolve().parents[3])
        if code_root not in sys.path:
            sys.path.insert(0, code_root)
        from tools.local_experiment.mlflow import import_comparisons as I
        importer = I.Importer(None, state=export, artifacts=clone_artifacts,
                              store_id=store_id, comparison_token='0' * 32)
        require(importer.index_path.is_file(), code)
        importer.read_index()  # Strict completed-only index; performs no write.
        index = importer.index
        require(index['experiment_id'] == '5', code)
        runs = {r['run_uuid']: r for r in records('runs') if str(r['experiment_id']) == '5'}
        require(len(index['runs']) == len(runs)
                and {entry['run_id'] for entry in index['runs'].values()} == set(runs), code)
        for token, entry in index['runs'].items():
            run_id = entry['run_id']
            run = runs[run_id]
            raw = read(clone_artifacts / I.NAMESPACE / run_id / 'artifacts/safe-projection.json', 256 * 1024)
            source = I.validate_projection(parse(raw))
            require(source['comparison_token'] == token and raw == I.canonical(source)
                    and digest(raw) == entry['projection_sha256']
                    and entry['source_key'] == I.digest(dict(comparison_token=token,
                        request_sha256=source['comparison']['request_sha256'], model_sha256=source['model_sha256'])), code)
            directory = export / I.FOLDER / entry['evidence_token']
            private(directory, True)
            require({p.name for p in directory.iterdir()} == {'source.json', 'result.json'}
                    and read(directory / 'source.json', 256 * 1024) == raw, code)
            result_raw = read(directory / 'result.json', MAX_MANIFEST)
            result = I.validate_record(parse(result_raw), source, entry, experiment_id='5', store_id=store_id)
            require(result_raw == I.canonical(result), code)
            # The reference remains an actual retained training run, not a
            # disconnected copy of an otherwise arithmetically valid model.
            baseline = source['source_training']
            reference = baseline['receipt']
            require(any(r['run_uuid'] == reference['run_id'] and str(r['experiment_id']) == '4'
                        for r in records('runs')), code)
            original = export / 'lake-training' / reference['token']
            require(read(original / 'result.json') == I.canonical(reference)
                    and read(original / 'workload.json') == I.canonical(baseline['workload']), code)
            params, tags, points = I.expected(source, store_id, entry['started_ms'])
            require(type(run['start_time']) is int and run['start_time'] == entry['started_ms']
                    and type(run.get('end_time')) is int and run['end_time'] == entry['finished_ms']
                    and run.get('name') == tags['mlflow.runName'], code)
            for table, expected in (('params', params), ('tags', tags)):
                selected = [r for r in records(table) if r['run_uuid'] == run_id]
                require(len(selected) == len(expected) and {r['key']: r['value'] for r in selected} == expected, code)
            expected_points = sorted((p['key'], p['value'], p['timestamp'], p['step']) for p in points)
            for table in ('metrics', 'latest_metrics'):
                selected = [r for r in records(table) if r['run_uuid'] == run_id]
                require(len(selected) == len(points) and all(type(r['timestamp']) is int and type(r['step']) is int
                        and r['is_nan'] == 0 for r in selected)
                        and sorted((r['key'], r['value'], r['timestamp'], r['step']) for r in selected) == expected_points, code)
    except Exception as error:
        if isinstance(error, RestoreError):
            raise
        raise RestoreError(code) from None


def profile_plan(before, export, clone_artifacts, *, root):
    """Validate saved profile/index/artifacts and plan URI changes without SQL writes."""
    records = lambda table: [dict(zip(before[table]['columns'], row)) for row in before[table]['rows']]
    experiments, runs = records('experiments'), records('runs')
    known = {**EXPERIMENTS, **TRAINING_EXPERIMENTS, **COMPARISON_EXPERIMENTS}
    ids = {str(x['experiment_id']) for x in experiments}
    require(len(experiments) == len(ids) and set(EXPERIMENTS) <= ids <= set(known),
            'RESTORE_PROFILE_UNSUPPORTED')
    store_id = parse(read(export / 'store.json'))['store_id']
    old_artifacts = root / SOURCE / 'store/artifacts'
    mutations = {'experiments': [], 'runs': []}
    for item in experiments:
        key = str(item['experiment_id'])
        name, suffix = known[key]
        require(item['name'] == name and item['lifecycle_stage'] == 'active'
                and artifact_suffix(item['artifact_location'], old_artifacts) == suffix,
                'RESTORE_ARTIFACT_URI_INVALID')
        mutations['experiments'].append((item['experiment_id'], (clone_artifacts / suffix).as_uri()))
        if key in TRAINING_EXPERIMENTS or key in COMPARISON_EXPERIMENTS:
            tags = [x for x in records('experiment_tags') if str(x['experiment_id']) == key]
            require(len(tags) == 2 and {x['key']: x['value'] for x in tags} == {
                    'shirokuma.owner': name, 'shirokuma.store_id': store_id},
                    'RESTORE_COMPARISON_INVALID' if key in COMPARISON_EXPERIMENTS else 'RESTORE_TRAINING_INVALID')
    require(0 < len(runs) <= 1024 and len({x['run_uuid'] for x in runs}) == len(runs),
            'RESTORE_PROFILE_UNSUPPORTED')
    for item in runs:
        key, run_id = str(item['experiment_id']), item['run_uuid']
        require(key in ids - {'0'} and type(run_id) is str and TOKEN.fullmatch(run_id)
                and item['status'] == 'FINISHED' and item['lifecycle_stage'] == 'active',
                'RESTORE_PROFILE_UNSUPPORTED')
        suffix = known[key][1] + '/' + run_id + '/artifacts'
        require(artifact_suffix(item['artifact_uri'], old_artifacts) == suffix,
                'RESTORE_ARTIFACT_URI_INVALID')
        target = clone_artifacts / suffix
        require(target.exists(), 'RESTORE_ARTIFACT_MISSING')
        private(target, True)
        require({p.name for p in target.iterdir()} == {'safe-projection.json'}, 'RESTORE_ARTIFACT_MISSING')
        if key in TRAINING_EXPERIMENTS:
            tags = [x for x in records('tags') if x['run_uuid'] == run_id]
            training_receipt(item, key, tags, export, target / 'safe-projection.json', store_id)
        mutations['runs'].append((run_id, target.as_uri()))
    for key, index_name in [('1', 'import-index.json'), ('2', 'benchmark-import-index.json')]:
        index = parse(read(export / index_name, MAX_MANIFEST))
        require(type(index) is dict and set(index) == {'schema_version', 'store_id', 'experiment_id',
                                                      'experiment_pending', 'runs'}
                and type(index['schema_version']) is int and index['schema_version'] == 1
                and index['store_id'] == parse(read(export / 'store.json'))['store_id']
                and index['experiment_id'] == key and index['experiment_pending'] is False
                and type(index['runs']) is dict, 'RESTORE_INDEX_INVALID')
        indexed = []
        for source_id, entry in index['runs'].items():
            require(type(entry) is dict and set(entry) == {'completed', 'projection_sha256', 'run_id',
                                                          'source_key', 'started_ms'}
                    and entry['completed'] is True and type(source_id) is str and 0 < len(source_id) <= 256
                    and type(entry['source_key']) is str and HEX.fullmatch(entry['source_key'])
                    and type(entry['run_id']) is str and TOKEN.fullmatch(entry['run_id'])
                    and type(entry['projection_sha256']) is str and HEX.fullmatch(entry['projection_sha256'])
                    and type(entry['started_ms']) is int and entry['started_ms'] >= 0,
                    'RESTORE_INDEX_INVALID')
            require(entry['run_id'] in {x['run_uuid'] for x in runs if str(x['experiment_id']) == key},
                    'RESTORE_INDEX_INVALID')
            run = next(x for x in runs if x['run_uuid'] == entry['run_id'])
            require(type(run['start_time']) is int and run['start_time'] == entry['started_ms'],
                    'RESTORE_INDEX_INVALID')
            indexed.append(entry['run_id'])
            artifact = clone_artifacts / EXPERIMENTS[key][1] / entry['run_id'] / 'artifacts/safe-projection.json'
            artifact_raw = read(artifact)
            projection = parse(artifact_raw)
            require(digest(artifact_raw) == entry['projection_sha256'] and type(projection) is dict
                    and projection.get('source_id') == source_id
                    and projection.get('source_key') == entry['source_key'], 'RESTORE_INDEX_INVALID')
            for tag, value in [('shirokuma.source_id', source_id),
                               ('shirokuma.source_key', entry['source_key']),
                               ('shirokuma.projection_sha256', entry['projection_sha256'])]:
                require([x['value'] for x in records('tags')
                         if x['run_uuid'] == entry['run_id'] and x['key'] == tag] == [value],
                        'RESTORE_INDEX_INVALID')
        require(len(set(indexed)) == len(indexed)
                and set(indexed) == {x['run_uuid'] for x in runs if str(x['experiment_id']) == key},
                'RESTORE_INDEX_INVALID')
    if '5' in ids:
        require('4' in ids, 'RESTORE_COMPARISON_INVALID')
        comparison_plan(records, export, clone_artifacts, store_id)
    else:
        require(not (export / 'comparison-import-index.json').exists(), 'RESTORE_COMPARISON_INVALID')
    return mutations


def rewrite_database(path, export, clone_artifacts, *, root):
    """Rewrite clone URI columns; compare every other row, schema and artifact."""
    connection = sqlite3.connect(path)
    try:
        connection.execute('PRAGMA trusted_schema=OFF')
        connection.execute('PRAGMA foreign_keys=ON')
        schema, before = database_snapshot(connection)
        mutations = profile_plan(before, export, clone_artifacts, root=root)
        expected = {name: {'columns': table['columns'], 'rows': list(table['rows'])} for name, table in before.items()}
        for table, identifier, column in [('experiments', 'experiment_id', 'artifact_location'),
                                           ('runs', 'run_uuid', 'artifact_uri')]:
            id_at, uri_at = before[table]['columns'].index(identifier), before[table]['columns'].index(column)
            changes = dict(mutations[table])
            expected[table]['rows'] = [tuple(changes[row[id_at]] if i == uri_at else value
                                                for i, value in enumerate(row)) for row in before[table]['rows']]
            connection.executemany('UPDATE "' + table + '" SET "' + column + '"=? WHERE "' + identifier + '"=?',
                                   [(uri, key) for key, uri in mutations[table]])
        connection.commit()
        after_schema, after = database_snapshot(connection)
        require(schema == after_schema and before.keys() == after.keys(), 'RESTORE_DATABASE_CHANGED')
        proof = {}
        for name, table in after.items():
            require(table['columns'] == expected[name]['columns']
                    and row_hash(table['rows']) == row_hash(expected[name]['rows']), 'RESTORE_DATABASE_CHANGED')
            proof[name] = {'rows': len(table['rows']), 'before_sha256': row_hash(before[name]['rows']),
                           'after_sha256': row_hash(table['rows'])}
        result = {'schema_sha256': row_hash(schema), 'tables': proof,
                  'artifact_uri_updates': {key: len(value) for key, value in mutations.items()}}
    finally:
        close_preserving(connection.close)
    require(not any(Path(str(path) + suffix).exists() or Path(str(path) + suffix).is_symlink()
                    for suffix in ('-wal', '-shm', '-journal')), 'RESTORE_DATABASE_SIDECAR')
    return result


def logical_snapshot(path):
    """Read-only clone/export schema and typed row snapshot, without MLflow SDK."""
    path = Path(path)
    before = fingerprint(private(path))
    def closed_state():
        require(fingerprint(private(path)) == before, 'RESTORE_SOURCE_CHANGED')
        require(not any(Path(str(path) + suffix).exists() or Path(str(path) + suffix).is_symlink()
                        for suffix in ('-wal', '-shm', '-journal')), 'RESTORE_DATABASE_SIDECAR')
    closed_state()
    connection = sqlite3.connect(Path(path).as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA trusted_schema=OFF')
        result = database_snapshot(connection)
    finally:
        close_preserving(connection.close)
    closed_state()
    return result


def inspect_completed(token, *, root=ROOT):
    """Validate a completed clone for a separate read-only SDK worker.

    The returned SQLite rows are internal expectations, never public output.
    A caller may create a fresh ``readback/<uuid>`` below this owned result.
    """
    require(type(token) is str and TOKEN.fullmatch(token), 'RESTORE_TOKEN_INVALID')
    root = Path(root)
    output = root / DESTINATION / token
    parents(root, output)
    private(output, True)
    receipt = parse(read(output / 'result.json', MAX_MANIFEST))
    require(type(receipt) is dict and set(receipt) == RESULT_KEYS and receipt.get('schema_version') == 1
            and type(receipt['schema_version']) is int and receipt.get('result') == 'verified'
            and receipt.get('profile') == 'local-tracking-clone-v1' and receipt.get('token') == token
            and receipt.get('source_unchanged') is True and receipt.get('activation_performed') is False
            and receipt.get('error_code') is None, 'RESTORE_RESULT_INVALID')
    checked = validate_export(receipt['source_manifest'], root=root)
    manifest = checked['manifest']
    require(receipt['store_id'] == manifest['store_id']
            and receipt['source_manifest_sha256'] == digest(checked['raw'])
            and receipt['export'] == str((output / 'export').relative_to(root))
            and receipt['clone'] == str((output / 'clone').relative_to(root))
            and inventory(output / 'export')[0] == manifest['inventory'], 'RESTORE_RESULT_INVALID')
    clone = output / 'clone'
    parents(root, clone / 'store/tracking.sqlite')
    private(clone, True)
    require({p.name for p in clone.iterdir()} == {'store'}
            and {p.name for p in (clone / 'store').iterdir()} == {'artifacts', 'tracking.sqlite'},
            'RESTORE_RESULT_INVALID')
    marker = parse(read(output / 'clone.json', MAX_MANIFEST))
    require(marker == {'schema_version': 1, 'owner': 'shirokuma-local-mlflow-restore',
            'token': token, 'source_store_id': manifest['store_id'], 'activation_supported': False,
            'mlflow_version': VERSION, 'backend_uri': 'sqlite:///' + str(clone / 'store/tracking.sqlite'),
            'artifact_root': str(clone / 'store/artifacts')}
            and type(marker['schema_version']) is int, 'RESTORE_RESULT_INVALID')
    artifacts = inventory(clone / 'store/artifacts')[0]
    expected_artifacts = inventory(output / 'export/store/artifacts')[0]
    require(artifacts == expected_artifacts and receipt['artifacts'] == {
            'files': sum(x['kind'] == 'file' for x in artifacts.values()),
            'bytes': sum(x.get('size', 0) for x in artifacts.values()),
            'inventory_sha256': digest(canonical(artifacts))}, 'RESTORE_ARTIFACT_CHANGED')
    database = clone / 'store/tracking.sqlite'
    require(type(receipt['database']) is dict and set(receipt['database']) == {
        'schema_sha256', 'tables', 'artifact_uri_updates', 'source_sha256', 'clone_sha256'},
        'RESTORE_RESULT_INVALID')
    require(digest(read(database)) == receipt['database']['clone_sha256']
            and receipt['database']['source_sha256'] == manifest['inventory']['store/tracking.sqlite']['sha256'],
            'RESTORE_DATABASE_CHANGED')
    snapshot = logical_snapshot(database)
    schema, tables = snapshot
    require(row_hash(schema) == receipt['database']['schema_sha256']
            and set(tables) == set(receipt['database']['tables']), 'RESTORE_DATABASE_CHANGED')
    for name, table in tables.items():
        saved = receipt['database']['tables'][name]
        require(type(saved) is dict and set(saved) == {'rows', 'before_sha256', 'after_sha256'}
                and type(saved['rows']) is int and saved['rows'] == len(table['rows'])
                and saved['after_sha256'] == row_hash(table['rows']),
                'RESTORE_DATABASE_CHANGED')
    # Recompute the exact two-column delta from the untouched provenance DB.
    old_schema, old_tables = logical_snapshot(output / 'export/store/tracking.sqlite')
    require(schema == old_schema, 'RESTORE_DATABASE_CHANGED')
    for name, table in old_tables.items():
        expected_rows = table['rows']
        if name in ('experiments', 'runs'):
            column = 'artifact_location' if name == 'experiments' else 'artifact_uri'
            position = table['columns'].index(column)
            expected_rows = [tuple((clone / 'store/artifacts' / artifact_suffix(value, root / SOURCE / 'store/artifacts')).as_uri()
                                   if i == position else value for i, value in enumerate(row)) for row in expected_rows]
        require(table['columns'] == tables[name]['columns']
                and row_hash(expected_rows) == row_hash(tables[name]['rows'])
                and receipt['database']['tables'][name]['before_sha256'] == row_hash(table['rows']),
                'RESTORE_DATABASE_CHANGED')
    require(receipt['database']['artifact_uri_updates'] == {
        'experiments': len(tables['experiments']['rows']), 'runs': len(tables['runs']['rows'])}
        and all(type(n) is int for n in receipt['database']['artifact_uri_updates'].values()),
        'RESTORE_RESULT_INVALID')
    return {'root': output, 'clone_db': database, 'clone_artifacts': clone / 'store/artifacts',
            'source_manifest': checked['path'], 'source_backup': checked['backup'],
            'expected_snapshot': snapshot, 'receipt': receipt}


def restore(manifest_path, *, root=ROOT):
    """Return a verified receipt; failures retain the fresh partial clone, never retry."""
    root = Path(root)
    checked = validate_export(manifest_path, root=root)
    manifest, backup = checked['manifest'], checked['backup']
    parent = root / DESTINATION
    if not parent.exists():
        parent.mkdir(mode=0o700)
    parents(root, parent)
    private(parent, True)
    token = uuid.uuid4().hex
    output = parent / token
    output.mkdir(mode=0o700)
    receipt = {'schema_version': 1, 'profile': 'local-tracking-clone-v1', 'token': token,
               'result': 'pending', 'source_manifest': str(checked['path'].relative_to(root)),
               'source_manifest_sha256': digest(checked['raw']), 'store_id': manifest['store_id'],
               'export': str((output / 'export').relative_to(root)),
               'clone': str((output / 'clone').relative_to(root)), 'database': None,
               'artifacts': None, 'source_unchanged': False, 'activation_performed': False,
               'error_code': None}
    try:
        write(output / 'intent.json', canonical(receipt))
        sync(output)
        copy_tree(backup, output / 'export', manifest['inventory'])
        clone = output / 'clone'
        clone.mkdir(mode=0o700)
        store_inventory = inventory(output / 'export/store')[0]
        copy_tree(output / 'export/store', clone / 'store', store_inventory)
        artifacts_before = inventory(clone / 'store/artifacts')[0]
        proof = rewrite_database(clone / 'store/tracking.sqlite', output / 'export', clone / 'store/artifacts', root=root)
        require(inventory(clone / 'store/artifacts')[0] == artifacts_before, 'RESTORE_ARTIFACT_CHANGED')
        require(inventory(output / 'export')[0] == manifest['inventory'], 'RESTORE_EXPORT_MISMATCH')
        source_after, seal_after = inventory(backup)
        require(source_after == manifest['inventory'] and seal_after == checked['seal']
                and read(checked['path'], MAX_MANIFEST) == checked['raw']
                and fingerprint(private(checked['path'])) == checked['manifest_seal'], 'RESTORE_SOURCE_CHANGED')
        proof.update(source_sha256=manifest['inventory']['store/tracking.sqlite']['sha256'],
                     clone_sha256=digest(read(clone / 'store/tracking.sqlite')))
        receipt.update(result='verified', database=proof, source_unchanged=True,
                       artifacts={'files': sum(x['kind'] == 'file' for x in artifacts_before.values()),
                                  'bytes': sum(x.get('size', 0) for x in artifacts_before.values()),
                                  'inventory_sha256': digest(canonical(artifacts_before))})
        write(output / 'clone.json', canonical({'schema_version': 1, 'owner': 'shirokuma-local-mlflow-restore',
              'token': token, 'source_store_id': manifest['store_id'], 'activation_supported': False,
              'mlflow_version': VERSION, 'backend_uri': 'sqlite:///' + str(clone / 'store/tracking.sqlite'),
              'artifact_root': str(clone / 'store/artifacts')}))
        sync(output)
        sync(parent)
        write(output / 'result.json', canonical(receipt))
        return receipt
    except BaseException as exc:
        receipt.update(result='failed' if isinstance(exc, Exception) else 'interrupted',
                       error_code=str(exc) if isinstance(exc, RestoreError) else
                       ('RESTORE_FAILED' if isinstance(exc, Exception) else 'INTERRUPTED'))
        try:
            if not (output / 'result.json').exists():
                write(output / 'result.json', canonical(receipt))
                sync(output)
        except BaseException as secondary:
            if isinstance(exc, Exception) and not isinstance(secondary, Exception):
                raise secondary
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    args = parser.parse_args(argv)
    try:
        result = restore(args.manifest)
        print(json.dumps({'result': result['result'], 'token': result['token'],
                          'evidence': DESTINATION + '/' + result['token'] + '/result.json'}))
        return 0
    except Exception as exc:
        print(json.dumps({'result': 'failed', 'error_code': str(exc) if isinstance(exc, RestoreError) else 'RESTORE_FAILED'}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
