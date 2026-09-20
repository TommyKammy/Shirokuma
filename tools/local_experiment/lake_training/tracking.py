#!/usr/bin/env python3
"""Track one sealed Iceberg training source without retrying uncertain writes."""
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

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.mlflow import train as B
from tools.local_experiment.mlflow import lake_training_workload as L
from tools.local_experiment.notebook import client as N

E, require = B.E, B.require
SOURCE_ROOT = ROOT / 'tmp/local-experiment/lake-training'
EXPERIMENT = 'shirokuma-local-iceberg-training-v1'
FILES = {'fixture/report.json', 'reader/reader-intent.json',
         'reader/reader-status.json', 'reader/reader.stdout.jsonl'}
READY_KEYS = {'schema_version', 'result', 'source_token', 'fixture_profile',
              'query_evidence_token', 'lake_input_sha256', 'files'}


def token(value):
    require(type(value) is str and re.fullmatch(r'[0-9a-f]{32}', value), 'LAKE_SOURCE_TOKEN_INVALID')
    return value


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def first(original, later):
    return later if original is None or isinstance(original, Exception) and not isinstance(later, Exception) else original


def source_hashes():
    result = {'mlflow/' + key: value for key, value in B.sources().items()}
    for name in ('lake_training/tracking.py', 'lake_training/run.py', 'lake_training/fixture.py',
                 'mlflow/lake_training_workload.py',
                 'query_read/contract.py', 'notebook/client.py', 'notebook/selection.py'):
        result[name] = sha((ROOT / 'tools/local_experiment' / name).read_bytes())
    return result


def private_parent(path):
    require(SOURCE_ROOT.is_absolute() and SOURCE_ROOT.resolve() == SOURCE_ROOT,
            'LAKE_SOURCE_PATH_INVALID')
    E.private_directory(SOURCE_ROOT)
    parent = SOURCE_ROOT
    for part in path.relative_to(SOURCE_ROOT).parts[:-1]:
        parent /= part
        E.private_directory(parent)


def exclusive(path, value):
    """A durable claim survives failure; its presence never authorizes replay."""
    private_parent(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    stream, original = None, None
    try:
        stream = os.fdopen(descriptor, 'wb')
        stream.write(E.canonical(value))
        stream.flush()
        os.fsync(stream.fileno())
    except BaseException as error:
        original = error
    finally:
        try:
            stream.close() if stream is not None else os.close(descriptor)
        except BaseException as error:
            original = first(original, error)
    if original is not None:
        raise original
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    except BaseException as error:
        original = error
    finally:
        try:
            os.close(descriptor)
        except BaseException as error:
            original = first(original, error)
    if original is not None:
        raise original


class Source:
    """Fixed private source files and their signatures, sealed before any fit."""

    def __init__(self, source_token):
        self.token = token(source_token)
        self.directory = SOURCE_ROOT / self.token
        self.saved = {}
        self.code = source_hashes()
        ready_raw = self.read('source-ready.json')
        ready = N._parse(ready_raw)
        require(type(ready) is dict and set(ready) == READY_KEYS
                and type(ready['schema_version']) is int and ready['schema_version'] == 1
                and ready['result'] == 'passed' and ready['source_token'] == self.token
                and ready['fixture_profile'] == L.FIXTURE_PROFILE
                and type(ready['files']) is dict and set(ready['files']) == FILES,
                'LAKE_SOURCE_READY_INVALID')
        token(ready['query_evidence_token'])
        require(ready_raw == E.canonical(ready), 'LAKE_SOURCE_READY_INVALID')
        raw = self.read('lake-input.json')
        self.value = N._parse(raw)
        require(type(self.value) is dict and set(self.value) == L.INPUT_KEYS
                and raw == E.canonical(self.value) and sha(raw) == ready['lake_input_sha256'],
                'LAKE_SOURCE_INPUT_INVALID')
        normalized = L.normalize_input(self.value['observation'],
            evidence_token=ready['query_evidence_token'], fixture_profile=ready['fixture_profile'])
        require(self.value == normalized, 'LAKE_SOURCE_INPUT_INVALID')
        documents = {}
        for name in sorted(FILES):
            raw_file = self.read(name)
            require(sha(raw_file) == ready['files'][name], 'LAKE_SOURCE_FILE_CHANGED')
            documents[name] = raw_file if name.endswith('.jsonl') else N._parse(raw_file)
        intent = documents['reader/reader-intent.json']
        require(type(intent) is dict and set(intent) == {'schema_version', 'command', 'request'}
                and type(intent['schema_version']) is int and intent['schema_version'] == 1
                and intent['command'] == 'query select', 'LAKE_READER_INTENT_INVALID')
        status = documents['reader/reader-status.json']
        require(type(status) is dict and type(status.get('schema_version')) is int
                and status['schema_version'] == 1 and status.get('state') == 'passed'
                and status.get('child_exited') is True and status.get('cleanup_verified') is True
                and type(status.get('exit_code')) is int and status['exit_code'] == 0
                and status.get('error_code') is None
                and status.get('evidence_token') == ready['query_evidence_token'], 'LAKE_READER_STATUS_INVALID')
        evidence_token, observation, cleanup = N._reader_frames(
            documents['reader/reader.stdout.jsonl'], status['exit_code'], intent['request'])
        require(evidence_token == ready['query_evidence_token'] and cleanup is True
                and observation == self.value['observation'], 'LAKE_READER_SOURCE_MISMATCH')
        fixture = documents['fixture/report.json']
        require(type(fixture) is dict and type(fixture.get('schema_version')) is int
                and fixture['schema_version'] == 1 and fixture.get('result') == 'passed'
                and fixture.get('profile') == L.FIXTURE_PROFILE
                and fixture.get('fixture_token') == self.token
                and fixture.get('table') == observation['table'] == 'iceberg.local_experiment.training_' + self.token,
                'LAKE_FIXTURE_SOURCE_INVALID')
        runtime = fixture.get('runtime')
        require(type(runtime) is dict and set(runtime) == {'prepare', 'cleanup'}
                and all(type(value) is dict and value.get('state') == 'verified'
                        and value.get('cleanup_verified') is True for value in runtime.values()),
                'LAKE_FIXTURE_SOURCE_INVALID')
        identity = fixture.get('identity', {})
        require(identity.get('table_uuid') == observation['table_uuid']
                and identity.get('current_snapshot_id') == observation['snapshot_id']
                and identity.get('schema_sha256') == observation['schema_sha256']
                and fixture.get('reader_cleanup_verified') is True
                and fixture.get('cleanup', {}).get('verified') is True
                and fixture.get('query_drain', {}).get('verified') is True
                and fixture.get('fixture_unchanged') is True
                and fixture.get('table_inventory_unchanged') is True, 'LAKE_FIXTURE_SOURCE_INVALID')
        self.ready_sha256 = sha(ready_raw)
        self.input_sha256 = sha(raw)
        self.guard()

    @staticmethod
    def signature(path):
        info = path.lstat()
        return (info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_nlink,
                info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def read(self, name):
        path = self.directory / name
        private_parent(path)
        before = self.signature(path)
        raw = E.read_private(path)
        require(self.signature(path) == before, 'LAKE_SOURCE_FILE_CHANGED')
        self.saved[path] = (raw, before)
        return raw

    def guard(self):
        require(source_hashes() == self.code, 'LAKE_TRAINING_SOURCE_CHANGED')
        for path, (raw, signature) in self.saved.items():
            private_parent(path)
            require(self.signature(path) == signature and E.read_private(path) == raw
                    and self.signature(path) == signature, 'LAKE_SOURCE_FILE_CHANGED')

    def intent(self, store_id):
        return dict(schema_version=1, result='pending', source_token=self.token,
                    source_ready_sha256=self.ready_sha256, lake_input_sha256=self.input_sha256,
                    store_id=store_id, source_sha256=self.code)

    def claim(self, store_id):
        self.guard()
        exclusive(self.directory / 'tracking-intent.json', self.intent(store_id))
        self.verify_intent(store_id)

    def verify_intent(self, store_id):
        raw = self.read('tracking-intent.json')
        require(raw == E.canonical(self.intent(store_id)), 'LAKE_TRACKING_INTENT_INVALID')
        self.guard()


class LakeTrainer(B.Trainer):
    experiment_name = owner = EXPERIMENT
    artifact_namespace = 'iceberg-training'
    attempt_folder = 'lake-training'
    run_name_prefix = 'lake-ridge-'

    def __init__(self, client, *, source, source_token, state, artifacts, store_id,
                 guard=lambda: None, entities=None, train=None, validate=None):
        self.source_token = token(source_token)
        self.source = deepcopy(source)
        require(type(self.source) is dict and set(self.source) == L.INPUT_KEYS
                and self.source == L.normalize_input(self.source['observation'],
                    evidence_token=self.source['query_evidence_token'],
                    fixture_profile=self.source['fixture_profile'])
                and self.source['observation']['table'] == 'iceberg.local_experiment.training_' + self.source_token,
                'LAKE_SOURCE_INPUT_INVALID')
        supplied = train or (lambda: L.train(deepcopy(self.source)))
        validator = validate or L.validate_result
        def bound_validate(workload):
            validator(workload)
            require(workload.get('lake_input') == self.source, 'LAKE_TRAINING_INPUT_CHANGED')
        super().__init__(client, state=state, artifacts=artifacts, store_id=store_id, guard=guard,
                         entities=entities, train=supplied, validate=bound_validate)

    def source_hashes(self):
        return source_hashes()

    def expected(self, workload):
        params, tags, points = super().expected(workload)
        observation = self.source['observation']
        params.update(scope='fixed-iceberg-holdout; not general model quality',
            lake_table=observation['table'], lake_table_uuid=observation['table_uuid'],
            lake_snapshot_id=observation['snapshot_id'], lake_schema_sha256=observation['schema_sha256'],
            lake_rows_sha256=observation['rows_sha256'],
            query_evidence_token=self.source['query_evidence_token'], source_token=self.source_token,
            fixture_profile=self.source['fixture_profile'])
        return params, tags, points


def worker(source_token):
    from tools.local_experiment.mlflow import runtime
    require(dict(os.environ) == runtime._isolated_runtime(), 'WORKER_ENVIRONMENT_NOT_ISOLATED')
    os.umask(0o077)
    with runtime.store_lock():
        status = runtime.require_stopped()
        source = Source(source_token)
        source.verify_intent(status['store_id'])
        exclusive(source.directory / 'tracking-worker-intent.json', source.intent(status['store_id']))
        require(source.read('tracking-worker-intent.json') == E.canonical(source.intent(status['store_id'])),
                'LAKE_TRACKING_INTENT_INVALID')
        def guard():
            require(runtime.require_stopped()['store_id'] == status['store_id'], 'STORE_IDENTITY_CHANGED')
            source.guard()
        guard()
        os.chdir(runtime.STORE)
        from mlflow import MlflowClient, entities
        original = None
        try:
            result, output = LakeTrainer(MlflowClient(tracking_uri=runtime.BACKEND_URI, registry_uri=runtime.BACKEND_URI),
                source=source.value, source_token=source.token, state=runtime.STATE, artifacts=runtime.ARTIFACT_ROOT,
                store_id=status['store_id'], guard=guard, entities=entities).run()
        except BaseException as error:
            original = error
        finally:
            try:
                guard()
            except BaseException as error:
                original = first(original, error)
        if original is not None:
            raise original
        print(json.dumps({'result': result['result'], 'source_token': source.token, 'run_id': result['run_id'],
                          'evidence': str(output.relative_to(ROOT)), 'failure_code': result['failure_code']}))
        return 0 if result['result'] == 'verified' else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-token', required=True)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        source_token = token(args.source_token)
        if args.worker:
            return worker(source_token)
        from tools.local_experiment.mlflow import runtime
        status = runtime.require_stopped()
        source = Source(source_token)
        source.claim(status['store_id'])
        original = None
        try:
            process = subprocess.run([str(runtime.VENV_PYTHON), '-B', str(Path(__file__).resolve()),
                '--source-token', source_token, '--worker'], cwd=ROOT, env=runtime.environment(), timeout=180, check=False)
        except BaseException as error:
            original = error
        finally:
            try:
                source.guard()
            except BaseException as error:
                original = first(original, error)
        if original is not None:
            raise original
        return process.returncode
    except Exception:
        print(json.dumps({'result': 'failed', 'failure_code': 'LAKE_TRACKING_PREFLIGHT_OR_WORKER_FAILED'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
