#!/usr/bin/env python3
"""One fixed sklearn autolog fit from a completed saved Iceberg training run.

The caller owns Tracking stop/export/restore. Completed repeats are cold reads.
No lifecycle manager, dataset reload, executable model artifact or Registry write.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from tools.local_experiment.mlflow import autolog_contract as C
from tools.local_experiment.mlflow import compare as M
from tools.local_experiment.mlflow import import_evaluations as E
from tools.local_experiment.mlflow import registry as G
from tools.local_experiment.mlflow import runtime as R
from tools.local_experiment.notebook import training as T

STATE = R.STATE
require = C.require


class Snapshot(G.Snapshot):
    """Reuse the Registry's cold, private completed-training source binding."""
    def load(self):
        bound = super().load()
        receipt = T.N._parse(self.saved[STATE/'lake-training'/self.token/'result.json'][0])
        self.workload = T.N._parse(self.saved[STATE/'lake-training'/self.token/'workload.json'][0])
        self.source = C.source(receipt, self.workload, store_id=bound['store_id'])
        return deepcopy(self.source)

    def raw(self, path, maximum=C.MAX_ARTIFACT_BYTES):
        raw, signature = T._read_file(path, maximum)
        self.saved[path] = (raw, signature, maximum)
        return raw


def _index(snapshot, store_id):
    path = STATE / C.INDEX_NAME
    value = snapshot.read(path) if path.exists() or path.is_symlink() else C.empty_index(store_id)
    return C.validate_index(value, store_id=store_id)


def _artifact_path(run_id):
    return STATE/'store/artifacts'/C.NAMESPACE/C.token(run_id)/'artifacts'/C.ARTIFACT


def recorded(training_token):
    """Original completed receipt only: no SDK, runtime status or backup read."""
    snapshot = Snapshot(C.token(training_token))
    first, result = None, None
    try:
        source = snapshot.load(); store_id = source['binding']['store_id']
        index = _index(snapshot, store_id); entry = index['runs'].get(training_token)
        if entry is not None:
            directory = STATE/C.FOLDER/entry['evidence_token']
            original = snapshot.read(directory/'source.json')
            require(original == source, 'AUTOLOG_SOURCE_CHANGED')
            failed = snapshot.read(directory/'result.json')
            recovery_path = directory/'recovery.json'
            recovery = snapshot.read(recovery_path) if recovery_path.exists() or recovery_path.is_symlink() else None
            if recovery is not None:
                require(snapshot.saved[directory/'result.json'][0] == C.canonical(failed), 'AUTOLOG_RECOVERY_INVALID')
                C.validate_recovery_entry(recovery, entry)
            result = C.validate_completed(index, original, C.completed_result(source, failed, recovery),
                snapshot.raw(_artifact_path(entry['run_id'])), store_id=store_id,
                backup=snapshot.read(directory/'backup.json'))
            require(result['run']['info']['artifact_uri'] == _artifact_path(entry['run_id']).parent.as_uri(),
                    'AUTOLOG_ARTIFACT_INVALID')
            require({p.name for p in _artifact_path(entry['run_id']).parent.iterdir()} == {C.ARTIFACT},
                    'AUTOLOG_ARTIFACT_INVALID')
            C.validate_calculation(result, snapshot.workload)
    except BaseException as error:
        first = error
    try: snapshot.check()
    except BaseException as error: first = T._keep(first, error)
    if first is not None: raise first
    return result


def database_run(run_id, *, store_id):
    """Targeted ordinary read-only transaction, after the existing stopped-store guard."""
    database = STATE/'store/tracking.sqlite'
    R.no_symlinks(database)
    sidecars = [database.with_name(database.name+suffix) for suffix in ('-wal', '-shm', '-journal')]
    require(not any(p.exists() or p.is_symlink() for p in sidecars), 'AUTOLOG_DATABASE_ACTIVE')
    before = database.stat()
    require(stat.S_ISREG(before.st_mode) and stat.S_IMODE(before.st_mode) == 0o600
            and before.st_uid == os.getuid() and before.st_nlink == 1, 'AUTOLOG_DATABASE_INVALID')
    connection = sqlite3.connect(database.as_uri()+'?mode=ro', uri=True, timeout=5)
    first, result = None, None
    try:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON'); connection.execute('BEGIN')
        rows = connection.execute('SELECT * FROM runs WHERE run_uuid=?', (run_id,)).fetchall()
        require(len(rows) == 1, 'AUTOLOG_RECOVERY_INVALID'); run = dict(rows[0])
        experiments = connection.execute('SELECT * FROM experiments WHERE experiment_id=?', (run['experiment_id'],)).fetchall()
        require(len(experiments) == 1, 'AUTOLOG_EXPERIMENT_CONFLICT')
        experiment = dict(experiments[0])
        tags = connection.execute('SELECT key,value FROM experiment_tags WHERE experiment_id=? LIMIT 4', (run['experiment_id'],)).fetchall()
        require(experiment['name'] == C.EXPERIMENT and experiment['lifecycle_stage'] == 'active'
                and experiment['artifact_location'] == (STATE/'store/artifacts'/C.NAMESPACE).as_uri()
                and len(tags) == 3 and dict(tags) == C.experiment_tags(store_id), 'AUTOLOG_EXPERIMENT_CONFLICT')
        info = {key:run[key] for key in ('status','start_time','end_time','lifecycle_stage','artifact_uri','user_id')}
        info.update(run_id=run_id, experiment_id=str(run['experiment_id']), run_name=run['name'])
        def selected(table):
            return [dict(row) for row in connection.execute('SELECT * FROM '+table+' WHERE run_uuid=? LIMIT 129', (run_id,)).fetchall()]
        params, tags = selected('params'), selected('tags')
        metrics, latest = selected('metrics'), selected('latest_metrics')
        require(len(params) <= 128 and len(tags) <= 128 and len(metrics) <= 128 and len(latest) <= 128
                and all(not row['is_nan'] for row in metrics+latest), 'AUTOLOG_RECOVERY_INVALID')
        histories = {}
        for row in metrics:
            histories.setdefault(row['key'], []).append([row['key'],row['timestamp'],row['step'],row['value']])
        require(all(len(rows)==len({r['key'] for r in rows}) for rows in (params,tags,latest)))
        require(all(histories.get(row['key']) == [[row['key'],row['timestamp'],row['step'],row['value']]]
                    for row in latest), 'AUTOLOG_RECOVERY_METRICS_CHANGED')
        result = dict(info=info, params={r['key']:r['value'] for r in params}, tags={r['key']:r['value'] for r in tags},
                      metrics={r['key']:r['value'] for r in latest}, histories={k:sorted(v) for k,v in histories.items()})
    except BaseException as error: first = error
    try: connection.close()
    except BaseException as error: first = T._keep(first, error)
    try:
        after = database.stat()
        fields = ('st_dev','st_ino','st_size','st_mtime_ns','st_mode','st_uid','st_nlink')
        require(all(getattr(before,k)==getattr(after,k) for k in fields)
                and not any(p.exists() or p.is_symlink() for p in sidecars), 'AUTOLOG_DATABASE_CHANGED')
    except BaseException as error: first = T._keep(first, error)
    if first is not None: raise first
    return result


def recover(training_token):
    """Adopt only a complete fit rejected by the original HTML wrapper check.

    The original result/source/backup and database stay byte-for-byte unchanged.
    An interruption after recovery persistence may finish only the index update.
    """
    training_token = C.token(training_token)
    with R.store_lock():
        status = R.require_stopped(); snapshot = Snapshot(training_token)
        first, candidate = None, None
        try:
            source = snapshot.load(); store_id=source['binding']['store_id']
            require(status['store_id'] == store_id, 'AUTOLOG_STORE_CHANGED')
            index_path = STATE/C.INDEX_NAME
            index = C.validate_index(snapshot.read(index_path), store_id=store_id, completed=False)
            require(not index['experiment_pending'] and training_token in index['runs']
                    and all(e['completed'] for t,e in index['runs'].items() if t != training_token),
                    'AUTOLOG_PRIOR_OUTCOME_UNKNOWN')
            entry = index['runs'][training_token]
            directory = STATE/C.FOLDER/entry['evidence_token']
            original_source = snapshot.read(directory/'source.json')
            original = snapshot.read(directory/'result.json'); backup = snapshot.read(directory/'backup.json')
            require(original_source == source and snapshot.saved[directory/'result.json'][0] == C.canonical(original),
                    'AUTOLOG_RECOVERY_INVALID')
            recovery_path = directory/'recovery.json'
            if recovery_path.exists() or recovery_path.is_symlink():
                recovery = snapshot.read(recovery_path)
            else:
                require(entry['phase'] == 'fit-started' and entry['completed'] is False
                        and entry['run_id'] is not None and entry['finished_ms'] is None, 'AUTOLOG_RECOVERY_INVALID')
                recovered = deepcopy(original); recovered.update(result='verified', failure_code=None)
                recovery = dict(schema_version=1, profile=C.PROFILE, evidence_token=entry['evidence_token'],
                    source_sha256=C.digest(source), original_result_sha256=C.digest(original), original_entry=deepcopy(entry),
                    original_entry_sha256=C.digest(entry), recovered_at_ms=time.time_ns()//1_000_000,
                    observation='stopped-sqlite-read-only', result=recovered)
            candidate = C.completed_result(source, original, recovery)
            expected_entry = dict(entry, completed=True, phase='verified', finished_ms=candidate['finished_ms'])
            C.validate_recovery_entry(recovery, expected_entry)
            require(entry in (recovery['original_entry'], expected_entry), 'AUTOLOG_RECOVERY_INVALID')
            index['runs'][training_token] = expected_entry
            path = _artifact_path(entry['run_id']); raw = snapshot.raw(path)
            require({p.name for p in path.parent.iterdir()} == {C.ARTIFACT}
                    and candidate['run']['info']['artifact_uri'] == path.parent.as_uri(), 'AUTOLOG_ARTIFACT_INVALID')
            C.validate_completed(index,source,candidate,raw,store_id=store_id,backup=backup)
            C.validate_calculation(candidate,snapshot.workload)
            require(database_run(entry['run_id'],store_id=store_id) == candidate['run'], 'AUTOLOG_RECOVERY_RUN_CHANGED')
            require(R.require_stopped()['store_id'] == store_id, 'AUTOLOG_STORE_CHANGED'); snapshot.check()
            if recovery_path not in snapshot.saved:
                E.persist(recovery_path,recovery)
                require(snapshot.read(recovery_path) == recovery, 'AUTOLOG_RECOVERY_INVALID')
            # Recheck immutable inputs after the durable recovery record and before
            # touching just the pending index. Never rerun the estimator or SDK.
            snapshot.check()
            snapshot.saved.pop(index_path, None)
            E.persist(index_path,index)
        except BaseException as error: first=error
        try:snapshot.check()
        except BaseException as error:first=T._keep(first,error)
        if first is not None:raise first
        return candidate


def public(result):
    directory = STATE/C.FOLDER/result['evidence_token']
    recovery = directory/'recovery.json'
    recovered = recovery.exists() or recovery.is_symlink()
    if recovered:
        value = T.N._parse(T._read_file(recovery)[0])
        require(type(value) is dict and value.get('result') == result, 'AUTOLOG_RECOVERY_INVALID')
    return dict(schema_version=1, profile=C.PROFILE, result='verified',
        observation='saved-autolog-recovery' if recovered else 'saved-autolog',
        training_token=result['training_token'], experiment_id=result['experiment_id'], run_id=result['run_id'],
        params=deepcopy(result['run']['params']), metrics=deepcopy(result['run']['metrics']),
        artifact=deepcopy(result['artifact']),
        evidence=str((recovery if recovered else directory/'result.json').relative_to(ROOT)), scope=C.SCOPE)


def run_view(client, run_id):
    run = client.get_run(run_id)
    info = {key: getattr(run.info, key) for key in ('run_id', 'experiment_id', 'status', 'start_time',
            'end_time', 'lifecycle_stage', 'artifact_uri', 'run_name', 'user_id')}
    histories = {}
    for key in sorted(C.METRICS):
        histories[key] = [[m.key, m.timestamp, m.step, m.value] for m in client.get_metric_history(run_id, key)]
    return dict(info=info, params=dict(run.data.params), tags=dict(run.data.tags),
                metrics=dict(run.data.metrics), histories=histories)


def fit_once(client, run_id, train):
    """SDK imports and the only fit occur in the isolated stopped-store worker."""
    import mlflow
    import mlflow.sklearn
    require(mlflow.__version__ == '3.16.1', 'AUTOLOG_VERSION_INVALID')
    mlflow.set_tracking_uri(R.BACKEND_URI)
    mlflow.set_registry_uri(R.BACKEND_URI)
    mlflow.sklearn.autolog(**C.AUTOLOG_OPTIONS)
    first, result = None, None
    try:
        # A preallocated owned run prevents autolog from silently selecting a new run.
        with mlflow.start_run(run_id=run_id):
            try:
                model, versions = M._fit(train, 1.0)
                result = model, dict(versions, mlflow=mlflow.__version__)
            except BaseException as error:
                first = error
                raise
    except BaseException as error:
        first = T._keep(first, error)
    try: mlflow.sklearn.autolog(disable=True)
    except BaseException as error: first = T._keep(first, error)
    if first is not None: raise first
    return result


class Runner:
    """One durable attempt under the existing store lock; injectable fake SDK."""
    def __init__(self, client, *, store_id, backup, guard=lambda: None, fit=fit_once, clock=None):
        self.client, self.store_id = client, store_id
        self.backup = C.validate_backup(backup, store_id=store_id)
        self.guard_runtime, self.fit = guard, fit
        self.clock = clock or (lambda: time.time_ns() // 1_000_000)

    def guard(self):
        self.guard_runtime(); self.snapshot.check()

    def save(self):
        E.persist(STATE/C.INDEX_NAME, self.index)

    def experiment(self):
        location = (STATE/'store/artifacts'/C.NAMESPACE).as_uri()
        if self.index['experiment_id'] is None:
            require(self.client.get_experiment_by_name(C.EXPERIMENT) is None, 'AUTOLOG_EXPERIMENT_CONFLICT')
            self.index['experiment_pending'] = True; self.save(); self.guard()
            identity = self.client.create_experiment(C.EXPERIMENT, artifact_location=location,
                                                    tags=C.experiment_tags(self.store_id))
            C.numeric_id(identity)
            self.index.update(experiment_id=identity, experiment_pending=False); self.save()
        experiment = self.client.get_experiment(self.index['experiment_id'])
        require(experiment.experiment_id == self.index['experiment_id'] and experiment.name == C.EXPERIMENT
                and experiment.artifact_location == location and experiment.lifecycle_stage == 'active'
                and dict(experiment.tags) == C.experiment_tags(self.store_id), 'AUTOLOG_EXPERIMENT_CONFLICT')
        runs = self.client.search_runs([self.index['experiment_id']], max_results=C.MAX_RUNS+1)
        require(len(runs) <= C.MAX_RUNS and {r.info.run_id for r in runs}
                == {e['run_id'] for e in self.index['runs'].values()}, 'AUTOLOG_EXPERIMENT_CONFLICT')

    def run(self, training_token):
        training_token = C.token(training_token)
        self.snapshot = Snapshot(training_token); source = self.snapshot.load()
        require(source['binding']['store_id'] == self.store_id, 'AUTOLOG_STORE_CHANGED')
        self.index = _index(self.snapshot, self.store_id)
        if training_token in self.index['runs']: return recorded(training_token)
        # This locked owner will update its index; only the immutable training
        # inputs belong in the source seal during writes.
        self.snapshot.saved.pop(STATE/C.INDEX_NAME, None)
        require(len(self.index['runs']) < C.MAX_RUNS, 'AUTOLOG_RUN_LIMIT')
        E.private_directory(STATE/C.FOLDER, create=True)
        evidence = uuid.uuid4().hex; directory = STATE/C.FOLDER/evidence
        E.private_directory(directory, create=True)
        E.persist(directory/'source.json', source); E.persist(directory/'backup.json', self.backup)
        started = self.clock()
        result = dict(schema_version=1, profile=C.PROFILE, evidence_token=evidence, training_token=training_token,
            store_id=self.store_id, experiment_id=None, run_id=None, result='running', phase='source',
            started_ms=started, finished_ms=None, source_sha256=C.digest(source), source_unchanged=False,
            failure_code=None, model=None, versions=None, run=None, artifact=None, backup_sha256=C.digest(self.backup))
        first = None
        try:
            self.guard(); result['phase'] = 'experiment'; self.experiment()
            result['experiment_id'] = self.index['experiment_id']
            entry = dict(evidence_token=evidence, run_id=None, source_sha256=C.digest(source),
                         started_ms=started, finished_ms=None, completed=False, phase='pending')
            self.index['runs'][training_token] = entry; self.save(); self.guard()
            result['phase'] = 'run-create'
            run = self.client.create_run(self.index['experiment_id'], start_time=started,
                tags=C.run_tags(source), run_name='autolog-' + training_token)
            run_id = C.token(run.info.run_id)
            require(run.info.experiment_id == self.index['experiment_id'], 'AUTOLOG_RUN_INVALID')
            entry.update(run_id=run_id, phase='run-created'); result['run_id'] = run_id; self.save()
            self.guard(); entry['phase'] = 'fit-started'; self.save(); self.guard(); result['phase'] = 'fit'
            rows = self.snapshot.workload['dataset']['rows']
            train = [deepcopy(rows[i]) for i in self.snapshot.workload['split']['train_indices']]
            result['model'], result['versions'] = self.fit(self.client, run_id, train)
            self.guard(); result['phase'] = 'verify'; result['run'] = run_view(self.client, run_id)
            expected_path = _artifact_path(run_id)
            require(result['run']['info']['artifact_uri'] == expected_path.parent.as_uri(), 'AUTOLOG_ARTIFACT_INVALID')
            files = self.client.list_artifacts(run_id)
            require(len(files) == 1 and files[0].path == C.ARTIFACT and files[0].is_dir is False,
                    'AUTOLOG_ARTIFACT_INVALID')
            E.private_directory(expected_path.parent)
            require({p.name for p in expected_path.parent.iterdir()} == {C.ARTIFACT}, 'AUTOLOG_ARTIFACT_INVALID')
            raw, signature = T._read_file(expected_path, C.MAX_ARTIFACT_BYTES)
            require(type(files[0].file_size) is int and files[0].file_size == len(raw), 'AUTOLOG_ARTIFACT_INVALID')
            result['artifact'] = dict(name=C.ARTIFACT, size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            self.guard()
            require(T._read_file(expected_path, C.MAX_ARTIFACT_BYTES) == (raw, signature), 'AUTOLOG_ARTIFACT_CHANGED')
            result.update(result='verified', phase='verified', finished_ms=self.clock(), source_unchanged=True)
            entry.update(completed=True, phase='verified', finished_ms=result['finished_ms'])
            C.validate_completed(self.index, source, result, raw, store_id=self.store_id, backup=self.backup)
            C.validate_calculation(result, self.snapshot.workload)
            # Receipt first: any failure before the final index write remains non-repeatable.
            E.persist(directory/'result.json', result); self.save()
        except BaseException as error:
            first = error
            # Do not rewrite the durable pending index into a retryable state.
            result.update(result='interrupted' if not isinstance(error, Exception) else 'failed',
                          finished_ms=self.clock(), failure_code='AUTOLOG_INTERRUPTED' if not isinstance(error, Exception)
                          else str(error) if isinstance(error, C.AutologError) else 'AUTOLOG_FAILED')
            try: E.persist(directory/'result.json', result)
            except BaseException as later: first = T._keep(first, later)
        try: self.snapshot.check()
        except BaseException as error: first = T._keep(first, error)
        if first is not None: raise first
        return result


def worker(training_token, backup_manifest):
    require(dict(os.environ) == R._isolated_runtime(), 'AUTOLOG_ENVIRONMENT_INVALID')
    os.umask(0o077)
    with R.store_lock():
        old = recorded(training_token)
        if old is not None:
            print(C.canonical(public(old)).decode(), end=''); return 0
        status = R.require_stopped()
        backup = G.validate_backup(backup_manifest, status['store_id'])
        def guard(): require(R.require_stopped()['store_id'] == status['store_id'], 'AUTOLOG_STORE_CHANGED')
        from mlflow import MlflowClient
        result = Runner(MlflowClient(tracking_uri=R.BACKEND_URI, registry_uri=R.BACKEND_URI),
                        store_id=status['store_id'], backup=backup, guard=guard).run(training_token)
        print(C.canonical(public(result)).decode(), end=''); return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--training-token', required=True)
    parser.add_argument('--backup-manifest')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--recover', action='store_true', help='Verify a finished fit rejected only by the original HTML wrapper check')
    args = parser.parse_args(argv)
    try:
        token = C.token(args.training_token)
        if args.recover:
            require(not args.worker and args.backup_manifest is None, 'AUTOLOG_REQUEST_INVALID')
            print(C.canonical(public(recover(token))).decode(),end='');return 0
        old = recorded(token)
        if old is not None:
            print(C.canonical(public(old)).decode(), end=''); return 0
        require(type(args.backup_manifest) is str and bool(args.backup_manifest), 'AUTOLOG_BACKUP_REQUIRED')
        if args.worker: return worker(token, args.backup_manifest)
        R.require_stopped()
        process = subprocess.run([str(R.VENV_PYTHON), '-I', '-B', str(Path(__file__).resolve()),
            '--worker', '--training-token', token, '--backup-manifest', args.backup_manifest],
            cwd=ROOT, env=R.environment(), timeout=180, check=False)
        return process.returncode
    except Exception as error:
        code = str(error) if isinstance(error, C.AutologError) else 'AUTOLOG_FAILED'
        print(C.canonical(dict(result='failed', error_code=code)).decode(), end=''); return 1


if __name__ == '__main__':
    raise SystemExit(main())
