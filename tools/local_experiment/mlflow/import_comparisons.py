#!/usr/bin/env python3
"""Record one completed saved Ridge comparison in the stopped local store.

No fit, Reader, HTTP or executable model loading. MLflow timestamps describe
this import; the old comparison did not record start/end timestamps. An
unfinished durable intent is never replayed, including a lost SDK response.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.local_experiment.mlflow import import_evaluations as E
from tools.local_experiment.notebook_lab import comparison_saved as S

C, T, N = S.C, S.T, S.N
EXPERIMENT = OWNER = 'shirokuma-local-ridge-comparisons-v1'
PROFILE = 'saved-ridge-comparison-import-v1'
NAMESPACE = 'ridge-comparisons'
INDEX_NAME = 'comparison-import-index.json'
FOLDER = 'comparison-imports'
MAX_RUNS = 128
SCOPE = 'Saved exploratory comparison; reused holdout; import time is not fit time; no new fit or model promotion.'
canonical, digest, require, ImportError = E.canonical, E.digest, E.require, E.ImportError
SOURCE_KEYS = {'comparison/' + name for name in S.FILES} | {
    'training/result.json', 'training/workload.json', 'training/store.json', 'training/safe-projection.json'}
PROJECTION_KEYS = {'schema_version', 'profile', 'comparison_token', 'request', 'comparison',
                   'model_sha256', 'source_training', 'source_files_sha256',
                   'comparison_started_ms', 'comparison_finished_ms', 'scope'}
ENTRY_KEYS = {'source_key', 'projection_sha256', 'run_id', 'started_ms', 'finished_ms', 'phase', 'completed', 'evidence_token'}
SOURCE_NAMES = ('mlflow/import_comparisons.py', 'mlflow/import_evaluations.py', 'mlflow/runtime.py',
                'mlflow/compare.py', 'mlflow/pyproject.toml', 'mlflow/uv.lock',
                'notebook_lab/comparison_saved.py', 'notebook_lab/compare.py',
                'notebook/training.py', 'notebook/client.py', 'query_read/contract.py')
PHASES = {'create-run-pending', 'run-created', 'log-batch-pending', 'batch-verified',
          'artifact-pending', 'artifact-verified', 'finish-pending', 'verified'}


def normalize_token(token):
    require(type(token) is str and E.HEX.fullmatch(token), 'COMPARISON_IMPORT_TOKEN_INVALID')
    return token


def sources():
    base = ROOT / 'tools/local_experiment'
    return {name: hashlib.sha256((base / name).read_bytes()).hexdigest() for name in SOURCE_NAMES}


def model_sha256(comparison):
    candidate = comparison['candidate']
    return C.digest(dict(algorithm=candidate['algorithm'], alpha=candidate['alpha'],
                         solver=candidate['solver'], fit_intercept=candidate['fit_intercept'],
                         feature_names=['x1', 'x2', 'x3'], **candidate['model']))


def validate_projection(value):
    """Pure artifact validation, also usable in an inactive restored clone.

The embedded baseline workload supplies the fixed 64 rows and split. Both
Ridge models and all metrics are replayed with arithmetic, never fitted.
This validates saved data, not current live store ownership.
"""
    try:
        require(type(value) is dict and set(value) == PROJECTION_KEYS
                and type(value['schema_version']) is int and value['schema_version'] == 1
                and value['profile'] == PROFILE and value['scope'] == SCOPE
                and value['comparison_started_ms'] is None and value['comparison_finished_ms'] is None,
                'COMPARISON_IMPORT_PROJECTION_INVALID')
        normalize_token(value['comparison_token'])
        request = C.normalize_request(value['request'])
        require(canonical(request) == canonical(value['request']), 'COMPARISON_IMPORT_PROJECTION_INVALID')
        source = value['source_training']
        require(type(source) is dict and set(source) == {'receipt', 'workload'}, 'COMPARISON_IMPORT_PROJECTION_INVALID')
        receipt, workload = source['receipt'], source['workload']
        # _receipt's marker argument is a structural check, not an IO operation.
        # Construct its fixed layout here so clone validation needs no live path.
        marker = dict(schema_version=1, owner='shirokuma-local-mlflow', mlflow_version='3.16.1',
                      store_id=receipt['store_id'], backend_uri='sqlite:///' + str(T.STATE / 'store/tracking.sqlite'),
                      artifact_root=str(T.STATE / 'store/artifacts'), url=N.URL)
        T._receipt(receipt, request['training_token'], marker, kind='iceberg')
        T._workload(workload, kind='iceberg')
        require(digest(workload) == receipt['workload_sha256'], 'COMPARISON_IMPORT_PROJECTION_INVALID')
        reference = T._project(receipt, workload, kind='iceberg')
        comparison = value['comparison']
        expected = C._project(request, workload, reference, comparison['candidate']['model'], comparison['versions'])
        require(canonical(comparison) == canonical(expected)
                and value['model_sha256'] == model_sha256(expected), 'COMPARISON_IMPORT_PROJECTION_INVALID')
        hashes = value['source_files_sha256']
        require(type(hashes) is dict and set(hashes) == SOURCE_KEYS
                and all(type(sha) is str and E.SHA.fullmatch(sha) for sha in hashes.values())
                and hashes['comparison/input.json'] == digest(request)
                and hashes['comparison/worker.stdout.json'] == digest(comparison)
                and hashes['training/result.json'] == digest(receipt)
                and hashes['training/workload.json'] == hashes['training/safe-projection.json'] == digest(workload),
                'COMPARISON_IMPORT_PROJECTION_INVALID')
        require(len(canonical(value)) <= 256 * 1024, 'COMPARISON_IMPORT_PROJECTION_INVALID')
        return deepcopy(value)
    except Exception as error:
        if isinstance(error, ImportError):
            raise
        raise ImportError('COMPARISON_IMPORT_PROJECTION_INVALID') from None


class Snapshot:
    """Keep the exact private comparison and baseline bytes through the write."""
    def __init__(self, token):
        self.token = normalize_token(token)
        self.comparison_files, self.training_files = {}, {}
        self.projection = None

    def check(self):
        first = T._recheck(self.training_files, None)
        for path, (raw, signature, maximum) in self.comparison_files.items():
            try:
                current, current_signature = S._read(path, maximum)
                require((current, current_signature) == (raw, signature), 'COMPARISON_IMPORT_SOURCE_CHANGED')
            except BaseException as error:
                first = T._keep(first, error)
        if first is not None:
            if not isinstance(first, Exception):
                raise first
            raise ImportError('COMPARISON_IMPORT_SOURCE_CHANGED') from None

    def load(self):
        first = None
        try:
            directory = S.STATE / self.token
            for name, maximum in S.FILES.items():
                path = directory / name
                raw, signature = S._read(path, maximum)
                self.comparison_files[path] = raw, signature, maximum
            raw = {path.name: item[0] for path, item in self.comparison_files.items()}
            saved = S._validate(raw, self.token, directory)
            comparison = saved['comparison']
            request = C.parse_request(raw['input.json'])
            receipt, workload, artifact, self.training_files = T._sources(request['training_token'], kind='iceberg')
            require(T._project(receipt, workload, kind='iceberg')['identity'] == comparison['identity'],
                    'COMPARISON_IMPORT_SOURCE_CHANGED')
            names = {T.STATE / 'lake-training' / request['training_token'] / 'result.json': 'result.json',
                     T.STATE / 'lake-training' / request['training_token'] / 'workload.json': 'workload.json',
                     T.STATE / 'store.json': 'store.json', artifact: 'safe-projection.json'}
            hashes = {'comparison/' + name: hashlib.sha256(data).hexdigest() for name, data in raw.items()}
            hashes.update({'training/' + names[path]: hashlib.sha256(data).hexdigest()
                           for path, (data, _, _) in self.training_files.items()})
            self.projection = validate_projection(dict(schema_version=1, profile=PROFILE,
                comparison_token=self.token, request=request, comparison=comparison,
                model_sha256=model_sha256(comparison), source_training=dict(receipt=receipt, workload=workload),
                source_files_sha256=hashes, comparison_started_ms=None, comparison_finished_ms=None, scope=SCOPE))
        except BaseException as error:
            first = error
        finally:
            try:
                self.check()
            except BaseException as error:
                first = T._keep(first, error)
        if first is not None:
            if not isinstance(first, Exception) or isinstance(first, ImportError):
                raise first
            raise ImportError('COMPARISON_IMPORT_SOURCE_INVALID') from None
        return deepcopy(self.projection)


def expected(source, store_id, started_ms):
    """Stable Tracking projection; all 18 points use the import timestamp."""
    require(type(started_ms) is int and 0 < started_ms <= 2**53, 'COMPARISON_IMPORT_TIME_INVALID')
    comparison, token = source['comparison'], source['comparison_token']
    identity = comparison['identity']
    require(identity['store_id'] == store_id, 'COMPARISON_IMPORT_STORE_MISMATCH')
    params = dict(alpha=str(comparison['candidate']['alpha']), algorithm='Ridge', solver='svd',
                  fit_intercept='true', feature_names='x1,x2,x3', train_rows='48', test_rows='16',
                  reference_alpha='1.0', comparison_token=token, training_token=identity['attempt_token'],
                  source_run_id=identity['run_id'], source_experiment_id=identity['experiment_id'],
                  source_workload_sha256=identity['workload_sha256'], request_sha256=comparison['request_sha256'],
                  model_sha256=source['model_sha256'], source_model_sha256=comparison['source_model_sha256'],
                  dataset_sha256=comparison['dataset_sha256'], split_sha256=comparison['split_sha256'],
                  comparison_started_ms='not-recorded', comparison_finished_ms='not-recorded',
                  model_artifact='safe-projection.json', scope='exploratory-reused-holdout')
    params.update({'version_' + key: value for key, value in comparison['versions'].items()})
    params.update({'lake_' + key: value for key, value in comparison['lake_source'].items()})
    tags = {'shirokuma.owner': OWNER, 'shirokuma.store_id': store_id,
            'shirokuma.comparison_token': token, 'shirokuma.projection_sha256': digest(source),
            'shirokuma.source_run_id': identity['run_id'],
            'shirokuma.time_semantics': 'import-time-not-comparison-execution',
            'mlflow.runName': 'saved-comparison-' + token[:8]}
    groups = {'candidate': comparison['candidate']['metrics'], 'reference': comparison['reference']['metrics'],
              'delta': comparison['metric_deltas']}
    points = [dict(key=label + '_' + group + '_' + key, value=float(value), timestamp=started_ms, step=0)
              for label, metrics in groups.items() for group, values in metrics.items() for key, value in values.items()]
    return params, tags, points


class Importer:
    """Called under the stopped store lock; fake clients are used by tests."""
    artifact = E.Importer.artifact

    def __init__(self, client, *, state, artifacts, store_id, comparison_token,
                 guard=lambda: None, entities=None, client_factory=None):
        self.client, self.state, self.artifacts = client, Path(state), Path(artifacts)
        self.store_id, self.token = store_id, normalize_token(comparison_token)
        self.runtime_guard, self.entities, self.client_factory = guard, entities, client_factory
        self.index_path = self.state / INDEX_NAME
        self.index = dict(schema_version=1, profile=PROFILE, store_id=store_id,
                          experiment_id=None, experiment_pending=False, runs={})
        self.snapshot, self.code = None, None

    def save(self):
        E.persist(self.index_path, self.index)

    def guard(self):
        self.runtime_guard()
        require(sources() == self.code, 'COMPARISON_IMPORT_CODE_CHANGED')
        self.snapshot.check()

    def read_index(self):
        if self.index_path.exists() or self.index_path.is_symlink():
            self.index = N._parse(E.read_private(self.index_path))
        value = self.index
        require(type(value) is dict and set(value) == {'schema_version', 'profile', 'store_id',
                'experiment_id', 'experiment_pending', 'runs'} and type(value['schema_version']) is int
                and value['schema_version'] == 1 and value['profile'] == PROFILE and value['store_id'] == self.store_id
                and (value['experiment_id'] is None or type(value['experiment_id']) is str
                     and re.fullmatch(r'[0-9]{1,20}', value['experiment_id']))
                and type(value['experiment_pending']) is bool and type(value['runs']) is dict
                and len(value['runs']) <= MAX_RUNS, 'COMPARISON_IMPORT_INDEX_INVALID')
        for token, entry in value['runs'].items():
            normalize_token(token)
            require(type(entry) is dict and set(entry) == ENTRY_KEYS
                    and all(type(entry[k]) is str and E.SHA.fullmatch(entry[k]) for k in ('source_key', 'projection_sha256'))
                    and type(entry['evidence_token']) is str and E.HEX.fullmatch(entry['evidence_token'])
                    and (entry['run_id'] is None or type(entry['run_id']) is str and E.HEX.fullmatch(entry['run_id']))
                    and type(entry['started_ms']) is int and 0 < entry['started_ms'] <= 2**53
                    and (entry['finished_ms'] is None or type(entry['finished_ms']) is int
                         and entry['started_ms'] <= entry['finished_ms'] <= 2**53)
                    and type(entry['completed']) is bool and entry['phase'] in PHASES
                    and (not entry['completed'] or entry['phase'] == 'verified'
                         and entry['run_id'] is not None and entry['finished_ms'] is not None),
                    'COMPARISON_IMPORT_INDEX_INVALID')
        entries = list(value['runs'].values())
        require((not entries or value['experiment_id'] is not None)
                and len({entry['evidence_token'] for entry in entries}) == len(entries)
                and len({entry['run_id'] for entry in entries if entry['run_id'] is not None})
                    == sum(entry['run_id'] is not None for entry in entries), 'COMPARISON_IMPORT_INDEX_INVALID')
        # No inferred recovery or resend, even if a lost reply later becomes visible.
        require(not value['experiment_pending'] and all(e['completed'] for e in value['runs'].values()),
                'COMPARISON_IMPORT_PRIOR_OUTCOME_UNKNOWN')

    def experiment(self):
        tags = {'shirokuma.owner': OWNER, 'shirokuma.store_id': self.store_id}
        location = (self.artifacts / NAMESPACE).as_uri()
        current = self.client.get_experiment_by_name(EXPERIMENT)
        if self.index['experiment_id'] is None:
            require(current is None and not self.index['runs'], 'COMPARISON_IMPORT_EXPERIMENT_COLLISION')
            self.index['experiment_pending'] = True
            self.save()
            self.guard()
            identity = self.client.create_experiment(EXPERIMENT, artifact_location=location, tags=tags)
            require(type(identity) is str and re.fullmatch(r'[0-9]{1,20}', identity), 'COMPARISON_IMPORT_EXPERIMENT_INVALID')
            self.index.update(experiment_id=identity, experiment_pending=False)
            self.save()
            current = self.client.get_experiment(identity)
        require(current is not None and current.experiment_id == self.index['experiment_id']
                and current.name == EXPERIMENT and current.lifecycle_stage == 'active'
                and current.artifact_location == location and current.tags == tags,
                'COMPARISON_IMPORT_EXPERIMENT_CONFLICT')
        found = self.client.search_runs([current.experiment_id], run_view_type=3, max_results=MAX_RUNS + 1)
        require(len(found) <= MAX_RUNS and not getattr(found, 'token', None)
                and len(found) == len(self.index['runs'])
                and {r.info.run_id for r in found} == {e['run_id'] for e in self.index['runs'].values()},
                'COMPARISON_IMPORT_RUN_INVENTORY_CHANGED')
        return current.experiment_id

    def inspect(self, source, entry, *, finished):
        params, tags, points = expected(source, self.store_id, entry['started_ms'])
        run = self.client.get_run(entry['run_id'])
        artifact = self.artifacts / NAMESPACE / entry['run_id'] / 'artifacts'
        require(run.info.run_id == entry['run_id'] and run.info.experiment_id == self.index['experiment_id']
                and run.info.lifecycle_stage == 'active' and run.info.artifact_uri == artifact.as_uri()
                and run.info.start_time == entry['started_ms']
                and run.info.end_time == (entry['finished_ms'] if finished else None)
                and run.info.status == ('FINISHED' if finished else 'RUNNING'), 'COMPARISON_IMPORT_RUN_CONFLICT')
        require(run.data.params == params and run.data.tags == tags
                and run.data.metrics == {p['key']: p['value'] for p in points}, 'COMPARISON_IMPORT_CONTENT_CONFLICT')
        for point in points:
            found = self.client.get_metric_history(entry['run_id'], point['key'])
            require(len(found) == 1 and dict(key=found[0].key, value=found[0].value,
                    timestamp=found[0].timestamp, step=found[0].step) == point,
                    'COMPARISON_IMPORT_HISTORY_CONFLICT')
        return artifact

    def import_one(self, source):
        key = digest(dict(comparison_token=self.token, request_sha256=source['comparison']['request_sha256'],
                          model_sha256=source['model_sha256']))
        entry = self.index['runs'].get(self.token)
        if entry is not None:
            require(entry['source_key'] == key and entry['projection_sha256'] == digest(source),
                    'COMPARISON_IMPORT_SOURCE_CONFLICT')
            artifact = self.inspect(source, entry, finished=True)
            require(E.read_private(artifact / 'safe-projection.json') == canonical(source),
                    'COMPARISON_IMPORT_ARTIFACT_CONFLICT')
            self.guard()
            return entry, False, 0, 0, False
        require(len(self.index['runs']) < MAX_RUNS, 'COMPARISON_IMPORT_RUN_LIMIT')
        entry = dict(source_key=key, projection_sha256=digest(source), run_id=None,
                     started_ms=int(time.time() * 1000), finished_ms=None, phase='create-run-pending',
                     completed=False, evidence_token=self.output.name)
        self.index['runs'][self.token] = entry
        self.save()
        self.guard()
        params, tags, points = expected(source, self.store_id, entry['started_ms'])
        created = self.client.create_run(self.index['experiment_id'], start_time=entry['started_ms'], tags=tags)
        require(type(created.info.run_id) is str and E.HEX.fullmatch(created.info.run_id), 'COMPARISON_IMPORT_RUN_INVALID')
        entry.update(run_id=created.info.run_id, phase='run-created')
        self.save()
        artifact = self.artifacts / NAMESPACE / entry['run_id'] / 'artifacts'
        require(created.info.experiment_id == self.index['experiment_id'] and created.info.status == 'RUNNING'
                and created.info.lifecycle_stage == 'active' and created.info.start_time == entry['started_ms']
                and created.info.end_time is None and created.info.artifact_uri == artifact.as_uri()
                and created.data.params == {} and created.data.metrics == {} and created.data.tags == tags,
                'COMPARISON_IMPORT_CREATED_RUN_CONFLICT')
        entry['phase'] = 'log-batch-pending'
        self.save()
        self.guard()
        metrics = [self.entities.Metric(**p) for p in points] if self.entities else points
        parameters = [dict(key=k, value=v) for k, v in params.items()]
        if self.entities:
            parameters = [self.entities.Param(**p) for p in parameters]
        self.client.log_batch(entry['run_id'], metrics=metrics, params=parameters, synchronous=True)
        self.inspect(source, entry, finished=False)
        entry['phase'] = 'artifact-pending'
        self.save()
        self.guard()
        added = self.artifact(artifact, source, completed=False)
        entry.update(phase='finish-pending', finished_ms=max(entry['started_ms'], int(time.time() * 1000)))
        self.save()
        self.guard()
        self.client.set_terminated(entry['run_id'], status='FINISHED', end_time=entry['finished_ms'])
        self.inspect(source, entry, finished=True)
        require(E.read_private(artifact / 'safe-projection.json') == canonical(source), 'COMPARISON_IMPORT_ARTIFACT_CONFLICT')
        self.guard()
        entry.update(completed=True, phase='verified')
        self.save()
        return entry, True, len(points), len(params), added

    def run(self):
        E.private_directory(self.state)
        E.private_directory(self.artifacts)
        folder = self.state / FOLDER
        E.private_directory(folder, create=True)
        output = folder / uuid.uuid4().hex
        E.private_directory(output, create=True)
        self.output = output
        result = dict(schema_version=1, profile=PROFILE, token=output.name, comparison_token=self.token,
                      result='failed', phase='allocated', store_id=self.store_id, experiment_id=None, run_id=None,
                      import_started_ms=int(time.time() * 1000), import_finished_ms=None,
                      created_runs=0, added_metrics=0, added_params=0, artifact_added=False,
                      source_unchanged=False, failure_code=None)
        first = None
        try:
            self.code = sources()
            self.snapshot = Snapshot(self.token)
            source = self.snapshot.load()
            require(source['comparison']['identity']['store_id'] == self.store_id, 'COMPARISON_IMPORT_STORE_MISMATCH')
            self.read_index()
            result.update(source_sha256=self.code, projection_sha256=digest(source))
            E.persist(output / 'source.json', source)
            self.guard()
            if self.client is None:
                self.client = self.client_factory()
            result['experiment_id'] = self.experiment()
            entry, created, metrics, params, added = self.import_one(source)
            result.update(run_id=entry['run_id'], run_started_ms=entry['started_ms'], run_finished_ms=entry['finished_ms'],
                          created_runs=int(created), added_metrics=metrics, added_params=params, artifact_added=added)
            self.guard()
            result.update(result='verified', phase='verified', source_unchanged=True)
        except BaseException as error:
            first = error
        finally:
            if self.snapshot is not None:
                try:
                    self.guard()
                except BaseException as error:
                    first = T._keep(first, error)
            result['import_finished_ms'] = int(time.time() * 1000)
            if first is not None:
                index = self.index if type(self.index) is dict else {}
                entries = index.get('runs') if type(index.get('runs')) is dict else {}
                pending = index.get('experiment_pending') or any(not e.get('completed', False)
                          for e in entries.values() if type(e) is dict)
                entry = entries.get(self.token)
                if type(entry) is dict:
                    result.update(run_id=entry.get('run_id'), phase=entry.get('phase'))
                elif index.get('experiment_pending'):
                    result['phase'] = 'create-experiment-pending'
                result['experiment_id'] = index.get('experiment_id')
                result.update(result='interrupted' if not isinstance(first, Exception) else 'unknown' if pending else 'failed',
                              source_unchanged=False, failure_code=str(first) if isinstance(first, ImportError)
                              else 'COMPARISON_IMPORT_INTERRUPTED' if not isinstance(first, Exception) else 'COMPARISON_IMPORT_FAILED')
                if pending:
                    # Unknown SDK outcomes are not a claim of zero logical writes.
                    result.update(created_runs=None, added_metrics=None, added_params=None, artifact_added=None)
            try:
                E.persist(output / 'result.json', result)
            except BaseException as error:
                first = T._keep(first, error)
        if first is not None and not isinstance(first, Exception):
            raise first
        # A receipt write failure must not publish an earlier success.
        if first is not None and result['result'] == 'verified':
            raise first
        return result, output


def recorded(comparison_token):
    """Read the original verified import receipt, or None if never imported.

This cold snapshot check does not inspect current DB contents, query historical
PIDs, stop the server or initialize the SDK. Unfinished intents still block it.
"""
    from tools.local_experiment.mlflow import runtime as R
    token = normalize_token(comparison_token)
    marker = R.require_store()
    return _recorded(token, state=R.STATE, artifacts=R.ARTIFACT_ROOT, store_id=marker['store_id'])


def validate_record(value, source, entry, *, experiment_id, store_id):
    """Validate the original import receipt using inert index/artifact data."""
    require(type(value) is dict, 'COMPARISON_IMPORT_RECEIPT_INVALID')
    params, _, _ = expected(source, store_id, entry['started_ms'])
    expected_result = dict(schema_version=1, profile=PROFILE, token=entry['evidence_token'],
        comparison_token=source['comparison_token'], result='verified', phase='verified', store_id=store_id,
        experiment_id=experiment_id, run_id=entry['run_id'], import_started_ms=value.get('import_started_ms'),
        import_finished_ms=value.get('import_finished_ms'), created_runs=1, added_metrics=18,
        added_params=len(params), artifact_added=True, source_unchanged=True, failure_code=None,
        source_sha256=value.get('source_sha256'), projection_sha256=digest(source),
        run_started_ms=entry['started_ms'], run_finished_ms=entry['finished_ms'])
    code = value.get('source_sha256')
    require(type(code) is dict and set(code) == set(SOURCE_NAMES)
            and all(type(sha) is str and E.SHA.fullmatch(sha) for sha in code.values())
            and type(value.get('import_started_ms')) is int and type(value.get('import_finished_ms')) is int
            and 0 < value['import_started_ms'] <= entry['started_ms'] <= entry['finished_ms']
            <= value['import_finished_ms'] <= 2**53 and canonical(value) == canonical(expected_result),
            'COMPARISON_IMPORT_RECEIPT_INVALID')
    return deepcopy(expected_result)


def _recorded(token, *, state, artifacts, store_id):
    snapshot, saved, first, result = Snapshot(token), {}, None, None
    def read(path, maximum=256 * 1024):
        raw, signature = T._read_file(path, maximum)
        saved[path] = raw, signature, maximum
        return raw
    try:
        source = snapshot.load()
        require(source['comparison']['identity']['store_id'] == store_id, 'COMPARISON_IMPORT_STORE_MISMATCH')
        importer = Importer(None, state=state, artifacts=artifacts, store_id=store_id, comparison_token=token)
        if importer.index_path.exists() or importer.index_path.is_symlink():
            read(importer.index_path, E.MAX_FILE)
        importer.read_index()
        entry = importer.index['runs'].get(token)
        if entry is not None:
            require(entry['projection_sha256'] == digest(source)
                    and entry['source_key'] == digest(dict(comparison_token=token,
                        request_sha256=source['comparison']['request_sha256'], model_sha256=source['model_sha256'])),
                    'COMPARISON_IMPORT_SOURCE_CONFLICT')
            output = Path(state) / FOLDER / entry['evidence_token']
            require(read(output / 'source.json') == canonical(source)
                    and read(Path(artifacts) / NAMESPACE / entry['run_id'] / 'artifacts/safe-projection.json') == canonical(source),
                    'COMPARISON_IMPORT_ARTIFACT_CONFLICT')
            raw = read(output / 'result.json')
            value = N._parse(raw)
            result = validate_record(value, source, entry, experiment_id=importer.index['experiment_id'], store_id=store_id)
            require(raw == canonical(result), 'COMPARISON_IMPORT_RECEIPT_INVALID')
    except BaseException as error:
        first = error
    finally:
        first = T._recheck(saved, first)
        try:
            snapshot.check()
        except BaseException as error:
            first = T._keep(first, error)
    if first is not None:
        if isinstance(first, ImportError) or not isinstance(first, Exception):
            raise first
        raise ImportError('COMPARISON_IMPORT_RECEIPT_INVALID') from None
    return deepcopy(result)


def worker(token):
    from tools.local_experiment.mlflow import runtime as R
    require(dict(os.environ) == R._isolated_runtime(), 'WORKER_ENVIRONMENT_NOT_ISOLATED')
    os.umask(0o077)
    with R.store_lock():
        status = R.require_stopped()
        os.chdir(R.STORE)
        def guard():
            require(R.require_stopped()['store_id'] == status['store_id'], 'STORE_IDENTITY_CHANGED')
        from mlflow import MlflowClient, entities
        result, output = Importer(None, state=R.STATE, artifacts=R.ARTIFACT_ROOT,
            store_id=status['store_id'], comparison_token=token, guard=guard, entities=entities,
            client_factory=lambda: MlflowClient(tracking_uri=R.BACKEND_URI, registry_uri=R.BACKEND_URI)).run()
        print(json.dumps({key: result.get(key) for key in ('result', 'comparison_token', 'experiment_id',
              'run_id', 'created_runs', 'added_metrics', 'failure_code')} | {'evidence': str(output.relative_to(ROOT))}))
        return 0 if result['result'] == 'verified' else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--comparison-token', required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    modes.add_argument('--plan', action='store_true', help='Validate this saved candidate without SDK or writes.')
    args = parser.parse_args(argv)
    try:
        token = normalize_token(args.comparison_token)
        if args.plan:
            source = Snapshot(token).load()
            print(json.dumps(dict(result='verified', comparison_token=token, projection_sha256=digest(source),
                                  model_sha256=source['model_sha256'])))
            return 0
        if args.worker:
            return worker(token)
        from tools.local_experiment.mlflow import runtime as R
        R.require_stopped()
        process = subprocess.run([str(R.VENV_PYTHON), '-I', '-B', str(Path(__file__).resolve()),
                                  '--worker', '--comparison-token', token], cwd=ROOT,
                                 env=R.environment(), timeout=180, check=False)
        return process.returncode
    except Exception:
        print(json.dumps(dict(result='failed', failure_code='COMPARISON_IMPORT_PREFLIGHT_OR_WORKER_FAILED')))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
