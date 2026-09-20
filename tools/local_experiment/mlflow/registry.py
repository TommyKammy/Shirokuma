#!/usr/bin/env python3
"""Register an existing safe JSON Ridge artifact; resolve an explicit version.

First registration requires the stopped store and a matching cold export.
Completed repeats read saved receipts only. Resolve uses a SQLite read transaction,
never an SDK constructor, executable model loader, fit, or network request.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from tools.local_experiment.mlflow import registry_contract as C
from tools.local_experiment.mlflow import import_evaluations as E
from tools.local_experiment.mlflow import runtime as R
from tools.local_experiment.notebook import training as T

STATE = R.STATE
SCOPE = 'Local safe JSON registration; explicit version; no stage, alias, serving or quality promotion.'
require = C.require


def _read(path):
    """Existing no-follow private reader plus stable bytes/metadata recheck."""
    raw, signature = T._read_file(path)
    return T.N._parse(raw), (raw, signature, T.MAX_FILE)


class Snapshot:
    def __init__(self, token):
        self.token, self.saved = C.token(token), {}

    def read(self, path):
        value, saved = _read(path); self.saved[path] = saved
        return value

    def check(self):
        error = T._recheck(self.saved, None)
        if error is not None:
            if not isinstance(error, Exception): raise error
            raise C.RegistryError('REGISTRY_SOURCE_CHANGED') from None

    def load(self):
        first = None
        try:
            receipt = self.read(STATE / 'lake-training' / self.token / 'result.json')
            workload = self.read(STATE / 'lake-training' / self.token / 'workload.json')
            marker = self.read(STATE / 'store.json')
            bound = C.binding(receipt, workload, store_id=marker['store_id'])
            artifact = STATE / 'store/artifacts' / bound['artifact_relpath']
            self.read(artifact)
            public = T.load_saved_review(self.token, kind='iceberg')
            require(C.model_digest(public['model']) == bound['model_sha256']
                    and public['identity']['run_id'] == bound['run_id']
                    and public['identity']['workload_sha256'] == bound['workload_sha256']
                    and self.saved[artifact][0] == self.saved[STATE/'lake-training'/self.token/'workload.json'][0],
                    'REGISTRY_SOURCE_INVALID')
        except BaseException as error:
            first = error
        try: self.check()
        except BaseException as error: first = T._keep(first, error)
        if first is not None:
            if not isinstance(first, Exception): raise first
            raise C.RegistryError('REGISTRY_SOURCE_INVALID') from None
        self.binding, self.artifact = bound, artifact
        return bound


def _empty(store_id):
    return dict(schema_version=1, profile=C.PROFILE, store_id=store_id, model_name=C.MODEL_NAME,
                model_pending=None, model=None, entries={})


def _index(snapshot, store_id):
    path = STATE / C.INDEX_NAME
    value = snapshot.read(path) if path.exists() or path.is_symlink() else _empty(store_id)
    return C.validate_index(value, store_id=store_id)


def _public(result, *, observation='saved-registration'):
    return dict(schema_version=1, profile=C.PROFILE, result='verified', observation=observation,
        model_name=C.MODEL_NAME, version=result['model_version']['version'], binding=deepcopy(result['binding']),
        evidence=str((STATE/C.FOLDER/result['evidence_token']/'result.json').relative_to(ROOT)), scope=SCOPE)


def recorded(token):
    """Read the original completed registration, with no allocation or SDK."""
    snapshot = Snapshot(token)
    first, result = None, None
    try:
        bound = snapshot.load()
        index = _index(snapshot, bound['store_id'])
        entry = index['entries'].get(token)
        if entry is not None:
            directory = STATE/C.FOLDER/entry['evidence_token']
            result = C.validate_completed(index, entry['evidence_token'], snapshot.read(directory/'source.json'),
                                          snapshot.read(directory/'result.json'), bound)
            C.validate_version(result['model_version'], bound, source_uri=snapshot.artifact.as_uri())
    except BaseException as error: first = error
    try: snapshot.check()
    except BaseException as error: first = T._keep(first, error)
    if first is not None: raise first
    return result


def _model(entity, store_id):
    require(getattr(entity, 'workspace', 'default') == 'default'
            and getattr(entity, 'deployment_job_id', None) is None
            and getattr(entity, 'deployment_job_state', None) is None)
    return C.validate_model({key: getattr(entity, key) for key in C.MODEL_KEYS}, store_id=store_id)


def _version(entity, bound, uri):
    require(getattr(entity, 'workspace', 'default') == 'default')
    value = {key: getattr(entity, key) for key in C.VERSION_KEYS}
    value['version'] = C.version(value['version'])
    return C.validate_version(value, bound, source_uri=uri)


def registry_rows(database):
    """Read all Registry rows in one ordinary read-only SQLite transaction.

No immutable=1: an active server's committed WAL must remain visible.
The writer path calls this only after require_stopped and cold export validation.
"""
    R.no_symlinks(database)
    connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=5)
    first, result = None, None
    try:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        result = {}
        for table in ('registered_models', 'registered_model_tags', 'model_versions',
                      'model_version_tags', 'registered_model_aliases'):
            rows = connection.execute('SELECT * FROM "' + table + '" LIMIT 1025').fetchall()
            require(len(rows) <= 1024, 'REGISTRY_READ_LIMIT')
            result[table] = [dict(row) for row in rows]
    except BaseException as error: first = error
    try: connection.close()
    except BaseException as error: first = T._keep(first, error)
    if first is not None: raise first
    return result


def _database_records(rows, index):
    """Require a one-to-one match to this profile; do not adopt foreign models."""
    require(not rows['registered_model_aliases'], 'REGISTRY_FOREIGN_CONTENT')
    for group in rows.values():
        require(all(row.get('workspace') == 'default' and row.get('name') == C.MODEL_NAME for row in group),
                'REGISTRY_FOREIGN_CONTENT')
    models = rows['registered_models']
    require(len(models) == int(index['model'] is not None), 'REGISTRY_FOREIGN_CONTENT')
    def tags(table, version=None):
        values = [row for row in rows[table] if version is None or C.version(row['version']) == version]
        require(len({row['key'] for row in values}) == len(values))
        return {row['key']: row['value'] for row in values}
    if models:
        row = models[0]
        require(row.get('deployment_job_id') is None and row.get('deployment_job_state') is None)
        model = {key: row[{'creation_timestamp': 'creation_time', 'last_updated_timestamp': 'last_updated_time'}.get(key, key)]
                 for key in C.MODEL_KEYS - {'tags', 'aliases'}}
        model.update(tags=tags('registered_model_tags'), aliases={})
        C.validate_model(model, store_id=index['store_id'])
        require(model == index['model'], 'REGISTRY_MODEL_CHANGED')
    else:
        require(not rows['registered_model_tags'])
    expected = {entry['version']['version']: entry for entry in index['entries'].values()}
    require(len(rows['model_versions']) == len(expected), 'REGISTRY_FOREIGN_CONTENT')
    actual = {}
    for row in rows['model_versions']:
        number = C.version(row['version']); entry = expected.get(number)
        require(entry is not None and number not in actual, 'REGISTRY_FOREIGN_CONTENT')
        value = {key: row[{'creation_timestamp': 'creation_time', 'last_updated_timestamp': 'last_updated_time'}.get(key, key)]
                 for key in C.VERSION_KEYS - {'tags', 'aliases'}}
        value.update(version=number, tags=tags('model_version_tags', number), aliases=[])
        C.validate_version(value, entry['binding'], source_uri=entry['version']['source'],
                           storage_location=row.get('storage_location'))
        require(value == entry['version'], 'REGISTRY_VERSION_CHANGED')
        actual[number] = value
    require(len(rows['model_version_tags']) == sum(len(v['tags']) for v in actual.values()), 'REGISTRY_FOREIGN_CONTENT')
    return actual


def resolve(name, version):
    require(name == C.MODEL_NAME, 'REGISTRY_NAME_INVALID')
    number = C.version(version)
    marker = R.require_store()
    probe = Snapshot('0'*32)
    first, result = None, None
    try:
        index = _index(probe, marker['store_id'])
        entries = [e for e in index['entries'].values() if e['version']['version'] == number]
        require(len(entries) == 1, 'REGISTRY_VERSION_NOT_FOUND')
        result = recorded(entries[0]['binding']['training_token'])
        require(result is not None and result['model_version']['version'] == number)
        _database_records(registry_rows(STATE/'store/tracking.sqlite'), index)
    except BaseException as error: first = error
    try: probe.check()
    except BaseException as error: first = T._keep(first, error)
    if first is not None: raise first
    return _public(result, observation='sqlite-read-only')


def validate_backup(path, store_id):
    # This module depends only on restore's filesystem export validator. Restore
    # depends on the pure registry_contract module, never this SDK entrypoint.
    from tools.local_experiment.mlflow import restore as B
    saved = B.validate_export(path)
    require(saved['manifest']['store_id'] == store_id, 'REGISTRY_BACKUP_INVALID')
    current, _ = B.inventory(STATE/'store')
    expected = {'.' if key == 'store' else key[len('store/'):]: value
                for key, value in saved['manifest']['inventory'].items()
                if key == 'store' or key.startswith('store/')}
    require(current == expected, 'REGISTRY_BACKUP_STALE')
    return dict(manifest=str(saved['path'].relative_to(ROOT)),
                sha256=hashlib.sha256(saved['raw']).hexdigest(), store_id=store_id)


class Registrar:
    """Caller owns the stopped store lock; injected client/guards are fake-tested."""
    def __init__(self, client, *, store_id, guard=lambda: None, backup=None):
        self.client, self.store_id, self.guard, self.backup = client, store_id, guard, backup

    def register(self, token):
        old = recorded(token)
        if old is not None: return _public(old)
        snapshot = Snapshot(token); bound = snapshot.load()
        require(bound['store_id'] == self.store_id, 'REGISTRY_STORE_CHANGED')
        index = _index(snapshot, self.store_id)
        old_model = deepcopy(index['model'])
        _database_records(registry_rows(STATE/'store/tracking.sqlite'), index)
        require(len(index['entries']) < C.MAX_VERSIONS, 'REGISTRY_VERSION_LIMIT')
        require(type(self.backup) is dict and self.backup.get('store_id') == self.store_id, 'REGISTRY_BACKUP_INVALID')
        # Index will change by our own writes; all input training files remain sealed.
        snapshot.saved.pop(STATE/C.INDEX_NAME, None)
        E.private_directory(STATE/C.FOLDER, create=True)
        output = STATE/C.FOLDER/uuid.uuid4().hex
        E.private_directory(output, create=True)
        source = dict(schema_version=1, profile=C.PROFILE, binding=bound)
        result = dict(schema_version=1, profile=C.PROFILE, evidence_token=output.name,
            result='failed', binding=bound, model=None, model_version=None, created_model=False,
            created_version=False, source_unchanged=False, error_code=None,
            started_ms=int(time.time()*1000), finished_ms=None)
        first, attempted = None, False
        def check():
            self.guard(); snapshot.check()
        def save(): E.persist(STATE/C.INDEX_NAME, index)
        try:
            E.persist(output/'source.json', source); E.persist(output/'backup.json', self.backup)
            if index['model'] is None:
                index['model_pending'] = output.name; save(); check(); attempted = True
                model = self.client.create_registered_model(C.MODEL_NAME, tags=C.model_tags(self.store_id))
                index['model'] = _model(model, self.store_id)
                # Keep the operation pending until version intent is durable too.
            else:
                require(_model(self.client.get_registered_model(C.MODEL_NAME), self.store_id) == index['model'])
            entry = dict(state='pending', binding=bound, evidence_token=output.name, version=None)
            index['entries'][token] = entry; index['model_pending'] = None
            save(); check(); attempted = True
            created = self.client.create_model_version(C.MODEL_NAME, source=snapshot.artifact.as_uri(),
                run_id=bound['run_id'], tags=C.version_tags(bound), await_creation_for=0)
            value = _version(created, bound, snapshot.artifact.as_uri())
            value2 = _version(self.client.get_model_version(C.MODEL_NAME, value['version']), bound, snapshot.artifact.as_uri())
            require(value == value2 and self.client.get_model_version_download_uri(C.MODEL_NAME, value['version'])
                    == snapshot.artifact.as_uri(), 'REGISTRY_VERSION_CHANGED')
            model = _model(self.client.get_registered_model(C.MODEL_NAME), self.store_id)
            check()
            result.update(result='verified', model=model, model_version=value,
                          created_version=True, source_unchanged=True, finished_ms=int(time.time()*1000))
            # Was this model created by this operation? The durable model intent
            # precedes its creation; an existing index model uses the other branch.
            result['created_model'] = old_model is None
            index['model'] = model
            entry.update(state='complete', version=value)
            C.validate_completed(index, output.name, source, result, bound)
            E.persist(output/'result.json', result)
            save()
        except BaseException as error:
            first = error
            result.update(result='interrupted' if not isinstance(error, Exception) else 'unknown' if attempted else 'failed',
                created_model=None if attempted else False, created_version=None if attempted else False,
                source_unchanged=False, error_code='REGISTRY_INTERRUPTED' if not isinstance(error, Exception)
                else 'REGISTRY_OUTCOME_UNKNOWN' if attempted else 'REGISTRY_FAILED', finished_ms=int(time.time()*1000))
            try: E.persist(output/'result.json', result)
            except BaseException as later: first = T._keep(first, later)
        if first is not None:
            if not isinstance(first, Exception): raise first
            raise C.RegistryError(result['error_code']) from None
        return _public(result)


def worker(token, backup_manifest):
    require(dict(os.environ) == R._isolated_runtime(), 'REGISTRY_ENVIRONMENT_INVALID')
    os.umask(0o077)
    with R.store_lock():
        old = recorded(token)
        if old is not None:
            print(C.canonical(_public(old)).decode(), end=''); return 0
        status = R.require_stopped()
        backup = validate_backup(backup_manifest, status['store_id'])
        def guard(): require(R.require_stopped()['store_id'] == status['store_id'], 'REGISTRY_STORE_CHANGED')
        from mlflow import MlflowClient
        client = MlflowClient(tracking_uri=R.BACKEND_URI, registry_uri=R.BACKEND_URI)
        output = Registrar(client, store_id=status['store_id'], guard=guard, backup=backup).register(token)
        print(C.canonical(output).decode(), end='')
        return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    registration = commands.add_parser('register')
    registration.add_argument('--training-token', required=True)
    registration.add_argument('--backup-manifest', required=True)
    registration.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    resolution = commands.add_parser('resolve')
    resolution.add_argument('--name', required=True)
    resolution.add_argument('--version', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'resolve':
            print(C.canonical(resolve(args.name, args.version)).decode(), end=''); return 0
        token = C.token(args.training_token)
        old = recorded(token)
        if old is not None:
            print(C.canonical(_public(old)).decode(), end=''); return 0
        if args.worker: return worker(token, args.backup_manifest)
        R.require_stopped()
        process = subprocess.run([str(R.VENV_PYTHON), '-I', '-B', str(Path(__file__).resolve()),
            'register', '--worker', '--training-token', token, '--backup-manifest', args.backup_manifest],
            cwd=ROOT, env=R.environment(), timeout=180, check=False)
        return process.returncode
    except Exception:
        print(C.canonical(dict(result='failed', error_code='REGISTRY_REQUEST_FAILED')).decode(), end='')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
