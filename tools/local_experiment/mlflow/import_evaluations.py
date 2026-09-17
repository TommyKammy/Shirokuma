#!/usr/bin/env python3
"""Import fixed, validated evaluation receipts into the stopped private MLflow store.

The parent never imports MLflow. The SDK worker uses a new environment and the
runtime's exclusive lock. Standard run times describe this import, not old model
execution. A lost create_run reply stays unknown and is never silently retried.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.evaluation import report as REPORT

STATE = ROOT / 'tmp/local-experiment/mlflow-tracking'
EXPERIMENT = 'shirokuma-local-evaluation-receipts-v1'
OWNER = 'shirokuma-local-evaluation-import-v1'
MAX_RUNS = 128
MAX_FILE = 4 * 1024 * 1024
HEX = re.compile(r'[0-9a-f]{32}')
SHA = re.compile(r'[0-9a-f]{64}')


class ImportError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise ImportError(code)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False) + '\n').encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def private_directory(path, *, create=False):
    if create and not path.exists():
        path.mkdir(mode=0o700)
        sync_directory(path.parent)
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and not path.is_symlink() and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o700, 'UNSAFE_PRIVATE_DIRECTORY')


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read_private(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
            and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= MAX_FILE, 'UNSAFE_PRIVATE_FILE')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as stream:
        opened = os.fstat(stream.fileno())
        require((opened.st_ino, opened.st_dev) == (info.st_ino, info.st_dev), 'PRIVATE_FILE_CHANGED')
        raw = stream.read(MAX_FILE + 1)
    require(len(raw) <= MAX_FILE, 'PRIVATE_FILE_TOO_LARGE')
    return raw


def persist(path, value):
    private_directory(path.parent)
    if path.exists() or path.is_symlink():
        read_private(path)
    temporary = path.parent / ('.pending-' + uuid.uuid4().hex)
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb') as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def projection(run):
    aggregate = REPORT.aggregate([run])
    return {'schema_version': 1, 'source_id': run['suite'] + '/' + run['run_token'],
            'source_key': digest({'suite': run['suite'], 'run_token': run['run_token'],
                                  'artifact_sha256': run['artifact_sha256']}),
            'suite': run['suite'], 'run_token': run['run_token'], 'artifact_sha256': run['artifact_sha256'],
            'task_id': run['task_id'], 'trace_id': run['trace_id'],
            'provider_kind': run['provider_kind'], 'provider': run['provider'], 'model': run['model'],
            'case_signature': run['case_signature'], 'case_ids': run['case_ids'],
            'source_result': run['reported_result'], 'source_complete': len(run['_attempts']) == run['requested_inferences'],
            'source_started_at': run['started_at'], 'fixture_content_sha256': run['fixture_content_sha256'],
            'evaluator_revision': run['evaluator_revision'],
            'dataset_sha256': run.get('dataset_sha256'), 'evaluation_suite': run.get('evaluation_suite'),
            'aggregate': aggregate, 'inference_cost_estimate': None,
            'attempts': [{key: value for key, value in a.items() if key != 'event_id'} for a in run['_attempts']],
            'scope': 'saved-evaluation-evidence-only; no fresh inference or audit verification'}


def collect_sources(base=REPORT.BASE):
    """collect supplies global duplicate/discovery checks; read_run revalidates both files."""
    require(not Path(base).is_symlink() and Path(base).is_dir(), 'UNSAFE_SOURCE_ROOT')
    report = REPORT.collect(base)
    safe = {key: report[key] for key in ('schema_version', 'coverage', 'discovery_issues', 'result')}
    safe['rejected'] = [{key: run[key] for key in ('suite', 'run_token', 'integrity', 'issues')}
                        for run in report['runs'] if run['integrity'] != 'valid']
    sources = []
    require(len(report['runs']) <= MAX_RUNS, 'SOURCE_RUN_LIMIT')
    for saved in report['runs']:
        if saved['integrity'] != 'valid':
            continue
        checked = REPORT.read_run(base, saved['suite'], saved['run_token'])
        require(checked['integrity'] == 'valid' and {k: v for k, v in checked.items() if not k.startswith('_')} == saved,
                'SOURCE_CHANGED_DURING_PLAN')
        sources.append(projection(checked))
    return {'inventory': safe, 'sources': sources, 'source_sha256': digest(sources)}


def metric_values(source, started_ms):
    """NULL is omitted, not zero; each point has a stable import time and step."""
    result = []
    def add(key, value, step=0):
        if value is not None:
            require(type(value) in (int, float) and math.isfinite(value) and abs(value) <= 2 ** 53,
                    'UNREPRESENTABLE_METRIC')
            result.append(dict(key=key, value=float(value), timestamp=started_ms, step=step))
    aggregate = source['aggregate']
    for key in ('requested_inferences', 'attempted_inferences', 'unattempted_inferences'):
        add(key, aggregate[key])
    for key in ('functional_success_observed', 'functional_success_requested', 'audit_coverage', 'attempt_coverage'):
        for part in ('numerator', 'denominator', 'value'):
            add(key + '.' + part, aggregate[key][part])
    add('evaluation_elapsed_ms', aggregate['elapsed_ms']['sum'])
    for key, row in aggregate['usage'].items():
        for part in ('total', 'known_subtotal', 'known_calls', 'missing_calls', 'p50', 'p95'):
            if part in row:
                add(key + '.' + part, row[part])
    for step, attempt in enumerate(source['attempts'], 1):
        add('attempt.success', int(attempt['result'] == 'passed'), step)
        add('attempt.audit_verified', int(attempt['audited']), step)
        for key, value in attempt['metrics'].items():
            add('attempt.' + key, value, step)
    return result


class Importer:
    """Called under the runtime store lock. Fake SDK injection performs offline tests."""
    def __init__(self, client, *, state, artifacts, store_id, base=REPORT.BASE, guard=lambda: None, entities=None, client_factory=None):
        self.client, self.state, self.artifacts = client, Path(state), Path(artifacts)
        self.store_id, self.base, self.guard, self.entities = store_id, base, guard, entities
        self.client_factory = client_factory
        self.index_path = self.state / 'import-index.json'
        self.index = {'schema_version': 1, 'store_id': store_id, 'experiment_id': None,
                      'experiment_pending': False, 'runs': {}}
        if self.index_path.exists() or self.index_path.is_symlink():
            self.index = REPORT.parse(read_private(self.index_path))
            require(type(self.index) is dict and set(self.index) == {'schema_version', 'store_id', 'experiment_id', 'experiment_pending', 'runs'}
                    and self.index['schema_version'] == 1 and self.index['store_id'] == store_id
                    and type(self.index['runs']) is dict and len(self.index['runs']) <= MAX_RUNS
                    and type(self.index['experiment_pending']) is bool, 'IMPORT_INDEX_INVALID')

    def save(self):
        persist(self.index_path, self.index)

    def experiment(self):
        tags = {'shirokuma.owner': OWNER, 'shirokuma.store_id': self.store_id}
        root = self.artifacts / 'evaluations'
        expected = root.as_uri()
        current = self.client.get_experiment_by_name(EXPERIMENT)
        if self.index['experiment_id'] is None:
            require(not self.index['experiment_pending'] and current is None, 'EXPERIMENT_CREATE_UNKNOWN_OR_COLLISION')
            self.index['experiment_pending'] = True
            self.save()
            self.guard()
            identity = self.client.create_experiment(EXPERIMENT, artifact_location=expected, tags=tags)
            require(type(identity) is str and re.fullmatch(r'[0-9]{1,20}', identity), 'EXPERIMENT_ID_INVALID')
            self.index.update(experiment_id=identity, experiment_pending=False)
            self.save()
            current = self.client.get_experiment(identity)
        require(current is not None and current.experiment_id == self.index['experiment_id']
                and current.name == EXPERIMENT and current.lifecycle_stage == 'active'
                and current.artifact_location == expected and current.tags == tags, 'EXPERIMENT_CONFLICT')
        return current.experiment_id

    def expected(self, source, entry):
        params = {key: str(source[key]) for key in ('suite', 'provider_kind', 'provider', 'model', 'case_signature', 'source_result')}
        params.update(case_count=str(len(source['case_ids'])), source_complete=str(source['source_complete']).lower(),
                      evaluation_suite=source['evaluation_suite'] or 'not-recorded')
        tags = {'shirokuma.owner': OWNER, 'shirokuma.store_id': self.store_id,
                'shirokuma.source_key': source['source_key'], 'shirokuma.source_id': source['source_id'],
                'shirokuma.projection_sha256': digest(source), 'shirokuma.time_semantics': 'import-time-not-source-execution',
                'shirokuma.source_started_at': 'unknown', 'shirokuma.source_result': source['source_result'],
                'shirokuma.source_complete': str(source['source_complete']).lower(),
                'mlflow.runName': source['suite'] + '-' + source['run_token'][:8]}
        return params, tags, metric_values(source, entry['started_ms'])

    def inspect(self, source, entry, experiment_id):
        params, tags, points = self.expected(source, entry)
        run = self.client.get_run(entry['run_id'])
        artifact = self.artifacts / 'evaluations' / entry['run_id'] / 'artifacts'
        require(run.info.run_id == entry['run_id'] and run.info.experiment_id == experiment_id
                and run.info.lifecycle_stage == 'active' and run.info.artifact_uri == artifact.as_uri()
                and run.info.start_time == entry['started_ms'] and run.info.status in ('RUNNING', 'FINISHED', 'FAILED'), 'RUN_IDENTITY_CONFLICT')
        require(run.data.tags == tags and set(run.data.params) <= set(params)
                and all(params[k] == v for k, v in run.data.params.items()), 'RUN_CONTENT_CONFLICT')
        expected_keys = {p['key'] for p in points}
        require(set(run.data.metrics) <= expected_keys, 'UNEXPECTED_METRIC')
        missing = []
        for key in sorted(expected_keys):
            expected = [p for p in points if p['key'] == key]
            found = self.client.get_metric_history(entry['run_id'], key)
            require(len(found) <= len(expected), 'DUPLICATE_METRIC_HISTORY')
            actual = [dict(key=x.key, value=x.value, timestamp=x.timestamp, step=x.step) for x in found]
            require(len({canonical(p) for p in actual}) == len(actual) and all(p in expected for p in actual), 'METRIC_HISTORY_CONFLICT')
            missing.extend(p for p in expected if p not in actual)
        return run, artifact, {k: v for k, v in params.items() if k not in run.data.params}, missing

    def artifact(self, path, source, *, completed):
        target = path / 'safe-projection.json'
        require(not completed or target.is_file(), 'COMPLETED_ARTIFACT_MISSING')
        private_directory(self.artifacts)
        parent = self.artifacts
        for part in path.relative_to(self.artifacts).parts:
            parent = parent / part
            private_directory(parent, create=True)
        data = canonical(source)
        if target.exists() or target.is_symlink():
            require(read_private(target) == data, 'ARTIFACT_CONFLICT')
            return False
        self.guard()
        temporary = path / ('.pending-' + uuid.uuid4().hex)
        try:
            with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            # The common store lock excludes every supported writer. Check once
            # more before the atomic rename; never replace a retained artifact.
            require(not target.exists() and not target.is_symlink(), 'ARTIFACT_APPEARED')
            os.rename(temporary, target)
            sync_directory(path)
        finally:
            temporary.unlink(missing_ok=True)
        require(read_private(target) == data, 'ARTIFACT_READBACK_FAILED')
        return True

    def import_one(self, source, experiment_id):
        self.recheck_one(source)
        key = source['source_id']
        entry = self.index['runs'].get(key)
        created = False
        if entry is None:
            entry = {'source_key': source['source_key'], 'projection_sha256': digest(source), 'run_id': None,
                     'started_ms': int(time.time() * 1000), 'completed': False}
            self.index['runs'][key] = entry
            self.save()  # durable intent before the single allowed create call
            self.guard()
            _, tags, _ = self.expected(source, entry)
            run = self.client.create_run(experiment_id, start_time=entry['started_ms'], tags=tags)
            require(type(run.info.run_id) is str and HEX.fullmatch(run.info.run_id), 'CREATED_RUN_ID_INVALID')
            entry['run_id'] = run.info.run_id
            self.save()  # retain returned identity before any further validation
            created = True
        require(type(entry) is dict and set(entry) == {'source_key', 'projection_sha256', 'run_id', 'started_ms', 'completed'}
                and entry['source_key'] == source['source_key'] and entry['projection_sha256'] == digest(source)
                and type(entry['started_ms']) is int and entry['started_ms'] > 0 and type(entry['completed']) is bool,
                'SOURCE_IDENTITY_CONFLICT')
        require(type(entry['run_id']) is str and HEX.fullmatch(entry['run_id']), 'RUN_CREATE_OUTCOME_UNKNOWN')
        run, artifact, params, missing = self.inspect(source, entry, experiment_id)
        terminal = 'FINISHED' if source['source_result'] == 'passed' else 'FAILED'
        if run.info.status != 'RUNNING':
            require(run.info.status == terminal and not params and not missing, 'TERMINAL_RUN_CONFLICT')
        require(not entry['completed'] or (run.info.status == terminal and not params and not missing), 'COMPLETED_RUN_CHANGED')
        if params or missing:
            self.guard()
            payload = dict(metrics=missing, params=[dict(key=k, value=v) for k, v in params.items()])
            if self.entities is not None:
                payload = dict(metrics=[self.entities.Metric(**p) for p in missing],
                               params=[self.entities.Param(k, v) for k, v in params.items()])
            self.client.log_batch(entry['run_id'], **payload, synchronous=True)
            _, _, pending_params, pending_metrics = self.inspect(source, entry, experiment_id)
            require(not pending_params and not pending_metrics, 'BATCH_READBACK_FAILED')
        artifact_added = self.artifact(artifact, source, completed=entry['completed'])
        if run.info.status == 'RUNNING':
            self.guard()
            self.client.set_terminated(entry['run_id'], status=terminal)
        final, _, remaining_params, remaining_metrics = self.inspect(source, entry, experiment_id)
        require(final.info.status == terminal and not remaining_params and not remaining_metrics, 'RUN_READBACK_FAILED')
        self.recheck_one(source)
        if not entry['completed']:
            entry['completed'] = True
            self.save()
        return dict(source_id=key, source_key=source['source_key'], run_id=entry['run_id'], created=created,
                    added_metrics=len(missing), added_params=len(params), artifact_added=artifact_added,
                    source_result=source['source_result'], source_complete=source['source_complete'], verified=True)

    def recheck_one(self, source):
        run = REPORT.read_run(self.base, source['suite'], source['run_token'])
        require(run['integrity'] == 'valid' and projection(run) == source, 'SOURCE_CHANGED')

    def run(self):
        private_directory(self.state)
        imports = self.state / 'imports'
        private_directory(imports, create=True)
        output = imports / uuid.uuid4().hex
        private_directory(output, create=True)
        result = dict(schema_version=1, result='failed', store_id=self.store_id,
                      import_started_at=dt.datetime.now(dt.timezone.utc).isoformat(), runs=[], source_unchanged=False)
        try:
            plan = collect_sources(self.base)
            persist(output / 'sources.json', plan)
            result['inventory'] = plan['inventory']
            require(plan['inventory']['result'] == 'complete', 'SOURCE_INVENTORY_INCOMPLETE')
            for source in plan['sources']:
                known = self.index['runs'].get(source['source_id'])
                require(known is None or (type(known) is dict and known.get('source_key') == source['source_key']
                        and known.get('projection_sha256') == digest(source)), 'SOURCE_IDENTITY_CONFLICT')
            self.guard()
            if self.client is None:
                self.client = self.client_factory()
            experiment_id = self.experiment()
            all_runs = self.client.search_runs([experiment_id], run_view_type=3, max_results=MAX_RUNS + 1)
            require(len(all_runs) <= MAX_RUNS and not getattr(all_runs, 'token', None), 'RUN_INVENTORY_LIMIT')
            known_ids = {r.get('run_id') for r in self.index['runs'].values() if isinstance(r, dict)} - {None}
            require({r.info.run_id for r in all_runs} == known_ids, 'UNINDEXED_OR_MISSING_RUN')
            for source in plan['sources']:
                result['runs'].append(self.import_one(source, experiment_id))
                persist(output / 'result.json', result)
            after = collect_sources(self.base)
            result['source_unchanged'] = (after['inventory']['result'] == 'complete'
                                          and after['source_sha256'] == plan['source_sha256'])
            require(result['source_unchanged'], 'SOURCE_INVENTORY_CHANGED')
            result.update(result='verified', experiment_id=experiment_id,
                          created_runs=sum(r['created'] for r in result['runs']),
                          added_metrics=sum(r['added_metrics'] for r in result['runs']))
        except BaseException as error:
            result['failure_code'] = str(error) if isinstance(error, ImportError) else 'IMPORT_INTERRUPTED' if not isinstance(error, Exception) else 'IMPORT_FAILED'
            if not isinstance(error, Exception):
                try:
                    persist(output / 'result.json', result)
                finally:
                    # A secondary disk error must not turn cancellation into an
                    # ordinary failure or invite an automatic retry.
                    raise error
            persist(output / 'result.json', result)
        persist(output / 'result.json', result)
        return result, output


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
        result, output = Importer(None, state=runtime.STATE, artifacts=runtime.ARTIFACT_ROOT,
                                  store_id=status['store_id'], guard=guard, entities=entities,
                                  client_factory=lambda: MlflowClient(tracking_uri=runtime.BACKEND_URI,
                                                                     registry_uri=runtime.BACKEND_URI)).run()
        print(json.dumps({'result': result['result'], 'created_runs': result.get('created_runs'),
                          'added_metrics': result.get('added_metrics'), 'evidence': str(output.relative_to(ROOT)),
                          'failure_code': result.get('failure_code')}))
        return 0 if result['result'] == 'verified' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--plan', action='store_true', help='Validate all fixed source receipts without SDK/runtime writes.')
    args = parser.parse_args()
    try:
        if args.plan:
            value = collect_sources()
            print(json.dumps({'result': value['inventory']['result'], **value['inventory']['coverage'],
                              'source_sha256': value['source_sha256']}))
            return 0 if value['inventory']['result'] == 'complete' else 1
        if args.worker:
            return worker()
        from tools.local_experiment.mlflow import runtime
        runtime.require_stopped()
        process = subprocess.run([str(runtime.VENV_PYTHON), str(Path(__file__).resolve()), '--worker'],
                                 env=runtime.environment(), cwd=ROOT, timeout=180, check=False)
        return process.returncode
    except Exception:
        print(json.dumps({'result': 'failed', 'failure_code': 'IMPORT_PREFLIGHT_FAILED'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
