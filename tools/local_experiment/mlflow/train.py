#!/usr/bin/env python3
"""Train one fixed regression workload and verify its local Tracking record.

The SDK worker shares the stopped store's exclusive lock. Each invocation is a
new experiment attempt; uncertain writes remain recorded and are never retried.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.mlflow import import_evaluations as E

EXPERIMENT = OWNER = 'shirokuma-local-supervised-training-v1'
require = E.require


def sources():
    names = ('train.py', 'training_workload.py', 'runtime.py', 'import_evaluations.py',
             'pyproject.toml', 'uv.lock')
    return {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest() for name in names}


class Trainer:
    """One owned run, explicit metric logging and a non-executable JSON model."""
    artifact = E.Importer.artifact
    experiment_name = EXPERIMENT
    owner = OWNER
    artifact_namespace = 'supervised-training'
    attempt_folder = 'training'
    run_name_prefix = 'fixed-ridge-'

    def __init__(self, client, *, state, artifacts, store_id, guard=lambda: None,
                 entities=None, train=None, validate=None):
        self.client, self.state, self.artifacts = client, Path(state), Path(artifacts)
        self.store_id, self.guard, self.entities = store_id, guard, entities
        if train is None or validate is None:
            from tools.local_experiment.mlflow import training_workload as W
            train, validate = train or W.train, validate or W.validate_result
        self.train, self.validate = train, validate

    def source_hashes(self):
        return sources()

    def save(self, phase=None):
        if phase is not None:
            self.result['phase'] = phase
        E.persist(self.output / 'result.json', self.result)

    def checked(self):
        self.guard()
        require(self.source_hashes() == self.result['source_sha256'], 'TRAINING_SOURCE_CHANGED')

    def experiment(self):
        location = (self.artifacts / self.artifact_namespace).as_uri()
        tags = {'shirokuma.owner': self.owner, 'shirokuma.store_id': self.store_id}
        current = self.client.get_experiment_by_name(self.experiment_name)
        if current is None:
            self.save('create-experiment-pending')
            self.checked()
            identity = self.client.create_experiment(self.experiment_name, artifact_location=location, tags=tags)
            require(type(identity) is str and identity.isascii() and identity.isdecimal()
                    and len(identity) <= 20, 'TRAINING_EXPERIMENT_ID_INVALID')
            self.result['experiment_id'] = identity
            self.save('experiment-created')
            current = self.client.get_experiment(identity)
        require(current is not None and type(current.experiment_id) is str
                and current.experiment_id.isascii() and current.experiment_id.isdecimal()
                and len(current.experiment_id) <= 20 and current.name == self.experiment_name
                and self.result['experiment_id'] in (None, current.experiment_id)
                and current.lifecycle_stage == 'active' and current.artifact_location == location
                and current.tags == tags, 'TRAINING_EXPERIMENT_CONFLICT')
        self.result['experiment_id'] = current.experiment_id
        return current.experiment_id

    def expected(self, workload):
        params = {key: str(value).lower() if type(value) is bool else str(value)
                  for key, value in workload['params'].items()}
        params.update(dataset_sha256=workload['hashes']['dataset_sha256'],
                      split_sha256=workload['hashes']['split_sha256'],
                      train_rows=str(len(workload['split']['train_indices'])),
                      test_rows=str(len(workload['split']['test_indices'])),
                      feature_names=','.join(workload['dataset']['feature_names']),
                      split_seed=str(workload['split']['seed']),
                      workload=workload['profile'],
                      model_artifact='safe-projection.json',
                      scope='fixed-synthetic-holdout; not general model quality')
        params.update({'version_' + key: value for key, value in workload['versions'].items()})
        tags = {'shirokuma.owner': self.owner, 'shirokuma.store_id': self.store_id,
                'shirokuma.attempt': self.result['token'],
                'shirokuma.workload_sha256': self.result['workload_sha256'],
                'shirokuma.time_semantics': 'actual-local-training-and-tracking',
                'mlflow.runName': self.run_name_prefix + self.result['token'][:8]}
        points = [dict(key=group + '_' + key, value=value, step=0,
                       timestamp=self.result['training_finished_ms'])
                  for group, metrics in workload['metrics'].items() for key, value in metrics.items()]
        return params, tags, points

    def inspect(self, workload, params, tags, points, *, finished):
        identity = self.result['run_id']
        run = self.client.get_run(identity)
        artifact = self.artifacts / self.artifact_namespace / identity / 'artifacts'
        require(run.info.run_id == identity and run.info.experiment_id == self.result['experiment_id']
                and run.info.lifecycle_stage == 'active' and run.info.artifact_uri == artifact.as_uri()
                and run.info.start_time == self.result['started_ms']
                and run.info.status == ('FINISHED' if finished else 'RUNNING')
                and run.info.end_time == (self.result['finished_ms'] if finished else None),
                'TRAINING_RUN_IDENTITY_CONFLICT')
        require(run.data.params == params and run.data.tags == tags
                and all(type(value) in (int, float) for value in run.data.metrics.values())
                and run.data.metrics == {p['key']: p['value'] for p in points}, 'TRAINING_RUN_CONTENT_CONFLICT')
        for point in points:
            history = self.client.get_metric_history(identity, point['key'])
            require(len(history) == 1
                    and type(history[0].key) is str
                    and type(history[0].value) in (int, float)
                    and type(history[0].timestamp) is int and type(history[0].step) is int
                    and {key: getattr(history[0], key) for key in point} == point,
                    'TRAINING_METRIC_HISTORY_CONFLICT')
        raw = E.read_private(artifact / 'safe-projection.json')
        require(raw == E.canonical(workload), 'TRAINING_ARTIFACT_CONFLICT')
        self.validate(json.loads(raw))  # safe coefficient replay, no pickle or fit
        require(E.read_private(self.output / 'workload.json') == raw, 'TRAINING_WORKLOAD_CHANGED')

    def run(self):
        E.private_directory(self.state)
        directory = self.state / self.attempt_folder
        E.private_directory(directory, create=True)
        token = uuid.uuid4().hex
        self.output = directory / token
        self.output.mkdir(mode=0o700)
        self.result = dict(schema_version=1, result='running', phase='allocated', token=token,
                           store_id=self.store_id, source_sha256=self.source_hashes(), experiment_id=None,
                           run_id=None, started_ms=int(time.time() * 1000), training_finished_ms=None,
                           finished_ms=None, workload_sha256=None, failure_code=None)
        try:
            self.save('training')
            self.checked()
            workload = self.train()
            self.result['training_finished_ms'] = int(time.time() * 1000)
            self.validate(workload)
            E.persist(self.output / 'workload.json', workload)
            self.result['workload_sha256'] = E.digest(workload)
            self.save('trained')
            self.checked()
            experiment = self.experiment()
            params, tags, points = self.expected(workload)
            self.save('create-run-pending')
            self.checked()
            created = self.client.create_run(experiment, start_time=self.result['started_ms'], tags=tags)
            identity = created.info.run_id
            require(type(identity) is str and E.HEX.fullmatch(identity), 'TRAINING_RUN_ID_INVALID')
            self.result['run_id'] = identity
            self.save('run-created')
            # Validate the returned identity before any content writes.
            require(created.info.experiment_id == experiment and created.info.status == 'RUNNING'
                    and created.info.lifecycle_stage == 'active'
                    and created.info.start_time == self.result['started_ms']
                    and created.info.end_time is None
                    and created.info.artifact_uri == (self.artifacts / self.artifact_namespace / identity / 'artifacts').as_uri()
                    and created.data.params == {} and created.data.metrics == {} and created.data.tags == tags,
                    'TRAINING_CREATED_RUN_CONFLICT')
            self.save('log-batch-pending')
            self.checked()
            metrics = [self.entities.Metric(**p) for p in points] if self.entities else points
            parameters = [dict(key=k, value=v) for k, v in params.items()]
            parameters = [self.entities.Param(**p) for p in parameters] if self.entities else parameters
            self.client.log_batch(identity, metrics=metrics, params=parameters, synchronous=True)
            self.save('artifact-pending')
            self.checked()
            artifact = self.artifacts / self.artifact_namespace / identity / 'artifacts'
            self.artifact(artifact, workload, completed=False)
            self.inspect(workload, params, tags, points, finished=False)
            self.checked()
            self.result['finished_ms'] = int(time.time() * 1000)
            self.save('finish-pending')
            self.checked()
            self.client.set_terminated(identity, status='FINISHED', end_time=self.result['finished_ms'])
            self.inspect(workload, params, tags, points, finished=True)
            self.checked()
            self.result.update(result='verified', phase='verified')
            self.save()
        except BaseException as error:
            # Unknown SDK write outcomes and partial records remain untouched.
            # The pending phase and token permit inspection, never blind replay.
            self.result['result'] = 'interrupted' if not isinstance(error, Exception) else 'failed'
            self.result['failure_code'] = (str(error) if isinstance(error, E.ImportError)
                                           else 'TRAINING_INTERRUPTED' if not isinstance(error, Exception)
                                           else 'TRAINING_FAILED')
            try:
                self.save()
            except BaseException:
                if isinstance(error, Exception):
                    raise
            if not isinstance(error, Exception):
                raise error
        return self.result, self.output


def worker():
    from tools.local_experiment.mlflow import runtime
    require(dict(os.environ) == runtime._isolated_runtime(), 'WORKER_ENVIRONMENT_NOT_ISOLATED')
    os.umask(0o077)
    with runtime.store_lock():
        status = runtime.require_stopped()
        os.chdir(runtime.STORE)
        from mlflow import MlflowClient, entities
        def guard():
            require(runtime.require_stopped()['store_id'] == status['store_id'], 'STORE_IDENTITY_CHANGED')
        result, output = Trainer(MlflowClient(tracking_uri=runtime.BACKEND_URI, registry_uri=runtime.BACKEND_URI),
                                  state=runtime.STATE, artifacts=runtime.ARTIFACT_ROOT,
                                  store_id=status['store_id'], guard=guard, entities=entities).run()
        print(json.dumps({'result': result['result'], 'run_id': result['run_id'],
                          'evidence': str(output.relative_to(ROOT)), 'failure_code': result['failure_code']}))
        return 0 if result['result'] == 'verified' else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.worker:
            return worker()
        from tools.local_experiment.mlflow import runtime
        runtime.require_stopped()
        process = subprocess.run([str(runtime.VENV_PYTHON), '-B', str(Path(__file__).resolve()), '--worker'],
                                 cwd=ROOT, env=runtime.environment(), timeout=180, check=False)
        return process.returncode
    except Exception:
        print(json.dumps({'result': 'failed', 'failure_code': 'TRAINING_PREFLIGHT_OR_WORKER_FAILED'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
