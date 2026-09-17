#!/usr/bin/env python3
"""Import saved Spark/Trino benchmark evidence into the stopped local MLflow store.

Four engine/case runs retain distinct observation boundaries. MLflow timestamps
describe import time; source times, unknowns, and exact integers stay in the fixed
safe artifact. This entrypoint does not run a query, model, or benchmark.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.mlflow import import_evaluations as E
from tools.local_experiment.spark_benchmark import report as REPORT

STATE = E.STATE
BASE = ROOT / 'tmp/local-experiment/spark-benchmark'
NATIVE_BASE = ROOT / 'tmp/local-experiment/spark-runs'
EXPERIMENT = 'shirokuma-local-spark-benchmark-v1'
OWNER = 'shirokuma-local-spark-benchmark-import-v1'
MAX_SOURCES = 128
MAX_RUNS = MAX_SOURCES * 4
HEX, SHA = E.HEX, E.SHA
ImportError = E.ImportError
require, canonical, digest = E.require, E.canonical, E.digest
private_directory, read_private, persist = E.private_directory, E.read_private, E.persist
SERVER_METRICS = {
    'elapsedTimeMillis': 'trino_server_elapsed_ms',
    'queuedTimeMillis': 'trino_server_queued_ms',
    'cpuTimeMillis': 'trino_server_cpu_ms',
    'wallTimeMillis': 'trino_server_wall_ms',
    'processedRows': 'trino_server_processed_rows',
    'processedBytes': 'trino_server_processed_bytes',
    'physicalInputBytes': 'trino_server_physical_input_bytes',
}


def collect_sources(base=BASE, native_base=NATIVE_BASE):
    """Use the report's full receipt/native/source verification, never normalize alone."""
    value = REPORT.collect(base=base, native_base=native_base)
    require(type(value) is dict and len(value['runs']) <= MAX_SOURCES, 'SOURCE_RUN_LIMIT')
    sources = []
    for saved in value['runs']:
        if not saved['eligible']:
            continue
        checked = REPORT.read_run(saved['run_token'], base=base, native_base=native_base)
        require(checked['eligible'] and checked['integrity'] == 'valid', 'SOURCE_CHANGED_DURING_PLAN')
        groups = REPORT.projection(checked)
        require(len(groups) == 4, 'SOURCE_GROUPS_INCOMPLETE')
        sources.extend(groups)
    require(sources == value['sources'], 'SOURCE_CHANGED_DURING_PLAN')
    require(len(sources) <= MAX_RUNS
            and len({s['source_id'] for s in sources}) == len(sources)
            and len({s['source_key'] for s in sources}) == len(sources), 'DUPLICATE_SOURCE')
    for source in sources:
        require(source['source_id'] == 'spark-benchmark/' + source['run_token'] + '/' + source['engine'] + '/' + source['case_id']
                and HEX.fullmatch(source['run_token']) and SHA.fullmatch(source['source_key']), 'SOURCE_IDENTITY_INVALID')
    inventory = {key: value[key] for key in ('schema_version', 'scope', 'inventory', 'runs', 'result')}
    inventory['issues'] = value.get('issues', [])
    # The full inventory participates: disappearance or a changed rejected source
    # cannot be hidden by hashing only the four successful groups.
    return {'inventory': inventory, 'sources': sources,
            'source_sha256': digest({'inventory': inventory, 'sources': sources})}


def metric_values(source, started_ms):
    """NULL and integer counters beyond exact IEEE754 range are not zero-filled."""
    points = []
    def add(key, value, step=0):
        if value is None:
            return
        require(type(value) in (int, float) and math.isfinite(value) and value >= 0, 'METRIC_VALUE_INVALID')
        if value > 2**53:
            return  # the safe artifact retains the exact integer
        points.append(dict(key=key, value=float(value), timestamp=started_ms, step=step))
    def milliseconds(value):
        require(value is None or type(value) is int and value >= 0, 'DURATION_INVALID')
        return None if value is None else value / 1_000_000
    add('expected_samples', source['expected_samples'])
    add('observed_samples', source['observed_samples'])
    add('unobserved_samples', source['unobserved_samples'])
    add('first_client_observed_ms', milliseconds(source['first_ns']))
    add('repeat_median_client_observed_ms', milliseconds(source['repeat_median_ns']))
    for sample in source['samples']:
        step = sample['sample_index']
        add('client_observed_ms', milliseconds(sample['elapsed_ns']), step)
        if source['engine'] == 'trino' and sample['stats'] is not None:
            for source_key, metric in SERVER_METRICS.items():
                add(metric, sample['stats'].get(source_key), step)
    return points


class Importer:
    """The runtime owns the lock and stopped-store checks; tests inject a fake SDK."""
    # Reuse only the fixed safe-projection writer, whose path is supplied by our
    # separately validated experiment/run identity. Evaluation logic is untouched.
    artifact = E.Importer.artifact

    def __init__(self, client, *, state, artifacts, store_id, base=BASE, native_base=NATIVE_BASE,
                 guard=lambda: None, entities=None, client_factory=None):
        self.client, self.state, self.artifacts = client, Path(state), Path(artifacts)
        self.store_id, self.base, self.native_base = store_id, base, native_base
        self.guard, self.entities, self.client_factory = guard, entities, client_factory
        self.index_path = self.state / 'benchmark-import-index.json'
        self.index = {'schema_version': 1, 'store_id': store_id, 'experiment_id': None,
                      'experiment_pending': False, 'runs': {}}
        if self.index_path.exists() or self.index_path.is_symlink():
            self.index = E.REPORT.parse(read_private(self.index_path))
        require(type(self.index) is dict and set(self.index) == {
            'schema_version', 'store_id', 'experiment_id', 'experiment_pending', 'runs'}
            and type(self.index['schema_version']) is int and self.index['schema_version'] == 1
            and self.index['store_id'] == store_id and type(self.index['runs']) is dict
            and len(self.index['runs']) <= MAX_RUNS and type(self.index['experiment_pending']) is bool
            and (self.index['experiment_id'] is None or type(self.index['experiment_id']) is str
                 and re.fullmatch(r'[0-9]{1,20}', self.index['experiment_id'])), 'IMPORT_INDEX_INVALID')
        for key, entry in self.index['runs'].items():
            require(type(key) is str and re.fullmatch(r'spark-benchmark/[0-9a-f]{32}/(trino|spark)/(q1_sums|q6)', key)
                    and type(entry) is dict and set(entry) == {
                        'source_key', 'projection_sha256', 'run_id', 'started_ms', 'completed'}
                    and type(entry['source_key']) is str and SHA.fullmatch(entry['source_key'])
                    and type(entry['projection_sha256']) is str and SHA.fullmatch(entry['projection_sha256'])
                    and (entry['run_id'] is None or type(entry['run_id']) is str and HEX.fullmatch(entry['run_id']))
                    and type(entry['started_ms']) is int and entry['started_ms'] > 0
                    and type(entry['completed']) is bool, 'IMPORT_INDEX_INVALID')
        known_ids = [r['run_id'] for r in self.index['runs'].values() if r['run_id'] is not None]
        require(len(set(known_ids)) == len(known_ids), 'IMPORT_INDEX_DUPLICATE_RUN')

    def save(self):
        persist(self.index_path, self.index)

    def experiment(self):
        tags = {'shirokuma.owner': OWNER, 'shirokuma.store_id': self.store_id}
        location = (self.artifacts / 'spark-benchmarks').as_uri()
        current = self.client.get_experiment_by_name(EXPERIMENT)
        if self.index['experiment_id'] is None:
            require(not self.index['experiment_pending'] and current is None, 'EXPERIMENT_CREATE_UNKNOWN_OR_COLLISION')
            self.index['experiment_pending'] = True
            self.save()
            self.guard()
            identity = self.client.create_experiment(EXPERIMENT, artifact_location=location, tags=tags)
            require(type(identity) is str and re.fullmatch(r'[0-9]{1,20}', identity), 'EXPERIMENT_ID_INVALID')
            self.index.update(experiment_id=identity, experiment_pending=False)
            self.save()
            current = self.client.get_experiment(identity)
        require(current is not None and current.experiment_id == self.index['experiment_id']
                and current.name == EXPERIMENT and current.lifecycle_stage == 'active'
                and current.artifact_location == location and current.tags == tags, 'EXPERIMENT_CONFLICT')
        return current.experiment_id

    def expected(self, source, entry):
        def value(item):
            return 'unknown' if item is None else str(item).lower() if type(item) is bool else str(item)
        params = {key: value(source[key]) for key in (
            'engine', 'case_id', 'source_result', 'measurement_complete', 'comparison_eligible',
            'expected_samples', 'observed_samples', 'unobserved_samples', 'workload_id', 'scale_factor',
            'observed_version', 'expected_version', 'iceberg_observed_version', 'snapshot_id', 'table_uuid',
            'row_count', 'logical_sha256', 'expected_result_sha256', 'cleanup_verified', 'query_drain_verified')}
        params.update({key: value(item) for key, item in source['timings'].items()})
        params.update({key: value(source['conditions'][key]) for key in (
            'measurement_scope', 'cache', 'engine_order', 'source_preparation', 'first_sample')})
        params.update(time_semantics='MLflow time is import time; original times remain in artifact',
                      duration_semantics='client observation; boundaries differ by engine; not engine-only comparison',
                      metric_policy='unknown and counters above 2^53 omitted; exact values in safe artifact')
        tags = {'shirokuma.owner': OWNER, 'shirokuma.store_id': self.store_id,
                'shirokuma.source_key': source['source_key'], 'shirokuma.source_id': source['source_id'],
                'shirokuma.projection_sha256': digest(source),
                'shirokuma.source_started_at': source['source_started_at'] or 'unknown',
                'shirokuma.source_finished_at': source['source_finished_at'] or 'unknown',
                'shirokuma.time_semantics': 'import-time-not-source-execution',
                'shirokuma.source_result': source['source_result'],
                'shirokuma.comparison_eligible': str(source['comparison_eligible']).lower(),
                'mlflow.runName': source['engine'] + '-' + source['case_id'] + '-' + source['run_token'][:8]}
        return params, tags, metric_values(source, entry['started_ms'])

    def inspect(self, source, entry, experiment_id):
        params, tags, points = self.expected(source, entry)
        run = self.client.get_run(entry['run_id'])
        artifact = self.artifacts / 'spark-benchmarks' / entry['run_id'] / 'artifacts'
        require(run.info.run_id == entry['run_id'] and run.info.experiment_id == experiment_id
                and run.info.lifecycle_stage == 'active' and run.info.artifact_uri == artifact.as_uri()
                and run.info.start_time == entry['started_ms'] and run.info.status in ('RUNNING', 'FINISHED', 'FAILED'),
                'RUN_IDENTITY_CONFLICT')
        require(run.data.tags == tags and set(run.data.params) <= set(params)
                and all(params[k] == v for k, v in run.data.params.items()), 'RUN_CONTENT_CONFLICT')
        keys = {p['key'] for p in points}
        require(set(run.data.metrics) <= keys, 'UNEXPECTED_METRIC')
        missing = []
        for key in sorted(keys):
            expected = [p for p in points if p['key'] == key]
            found = self.client.get_metric_history(entry['run_id'], key)
            require(len(found) <= len(expected), 'DUPLICATE_METRIC_HISTORY')
            actual = [dict(key=x.key, value=x.value, timestamp=x.timestamp, step=x.step) for x in found]
            require(len({canonical(p) for p in actual}) == len(actual)
                    and all(p in expected for p in actual), 'METRIC_HISTORY_CONFLICT')
            # MLflow's comparison UI reads the latest-value map, not history.
            # All points use one import timestamp; highest step is latest here.
            require((key in run.data.metrics) == bool(actual)
                    and (not actual or run.data.metrics[key] == max(actual, key=lambda p: p['step'])['value']),
                    'METRIC_LATEST_CONFLICT')
            missing.extend(p for p in expected if p not in actual)
        return run, artifact, {k: v for k, v in params.items() if k not in run.data.params}, missing

    def recheck_one(self, source):
        run = REPORT.read_run(source['run_token'], base=self.base, native_base=self.native_base)
        require(run['integrity'] == 'valid' and run['eligible']
                and source in REPORT.projection(run), 'SOURCE_CHANGED')

    def import_one(self, source, experiment_id):
        self.recheck_one(source)
        key = source['source_id']
        entry, created = self.index['runs'].get(key), False
        if entry is None:
            entry = {'source_key': source['source_key'], 'projection_sha256': digest(source), 'run_id': None,
                     'started_ms': int(time.time() * 1000), 'completed': False}
            self.index['runs'][key] = entry
            self.save()  # A lost create reply must never lead to a second create.
            self.guard()
            _, tags, _ = self.expected(source, entry)
            run = self.client.create_run(experiment_id, start_time=entry['started_ms'], tags=tags)
            require(type(run.info.run_id) is str and HEX.fullmatch(run.info.run_id), 'CREATED_RUN_ID_INVALID')
            entry['run_id'] = run.info.run_id
            self.save()
            created = True
        require(entry['source_key'] == source['source_key'] and entry['projection_sha256'] == digest(source),
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
        final, _, pending_params, pending_metrics = self.inspect(source, entry, experiment_id)
        require(final.info.status == terminal and not pending_params and not pending_metrics, 'RUN_READBACK_FAILED')
        self.recheck_one(source)
        if not entry['completed']:
            entry['completed'] = True
            self.save()
        return dict(source_id=key, source_key=source['source_key'], run_id=entry['run_id'], created=created,
                    added_metrics=len(missing), added_params=len(params), artifact_added=artifact_added,
                    source_result=source['source_result'], comparison_eligible=source['comparison_eligible'], verified=True)

    def run(self):
        private_directory(self.state)
        imports = self.state / 'benchmark-imports'
        private_directory(imports, create=True)
        output = imports / uuid.uuid4().hex
        private_directory(output, create=True)
        result = dict(schema_version=1, result='failed', store_id=self.store_id,
                      import_started_at=dt.datetime.now(dt.timezone.utc).isoformat(), runs=[], source_unchanged=False)
        try:
            plan = collect_sources(self.base, self.native_base)
            persist(output / 'sources.json', plan)
            result['inventory'] = plan['inventory']
            require(plan['inventory']['result'] == 'complete', 'SOURCE_INVENTORY_INCOMPLETE')
            require(set(self.index['runs']) <= {s['source_id'] for s in plan['sources']}, 'IMPORTED_SOURCE_MISSING')
            for source in plan['sources']:
                known = self.index['runs'].get(source['source_id'])
                require(known is None or (known['source_key'] == source['source_key']
                        and known['projection_sha256'] == digest(source)), 'SOURCE_IDENTITY_CONFLICT')
                require(known is None or known['run_id'] is not None, 'RUN_CREATE_OUTCOME_UNKNOWN')
            self.guard()
            if self.client is None:
                self.client = self.client_factory()
            experiment_id = self.experiment()
            all_runs = self.client.search_runs([experiment_id], run_view_type=3, max_results=MAX_RUNS + 1)
            require(len(all_runs) <= MAX_RUNS and not getattr(all_runs, 'token', None), 'RUN_INVENTORY_LIMIT')
            known_ids = {r['run_id'] for r in self.index['runs'].values()} - {None}
            require({r.info.run_id for r in all_runs} == known_ids, 'UNINDEXED_OR_MISSING_RUN')
            for source in plan['sources']:
                result['runs'].append(self.import_one(source, experiment_id))
                persist(output / 'result.json', result)
            after = collect_sources(self.base, self.native_base)
            result['source_unchanged'] = (after['inventory']['result'] == 'complete'
                                          and after['source_sha256'] == plan['source_sha256'])
            require(result['source_unchanged'], 'SOURCE_INVENTORY_CHANGED')
            result.update(result='verified', experiment_id=experiment_id,
                          created_runs=sum(r['created'] for r in result['runs']),
                          added_metrics=sum(r['added_metrics'] for r in result['runs']))
        except BaseException as error:
            result['failure_code'] = str(error) if isinstance(error, ImportError) else (
                'IMPORT_INTERRUPTED' if not isinstance(error, Exception) else 'IMPORT_FAILED')
            if not isinstance(error, Exception):
                try:
                    persist(output / 'result.json', result)
                finally:
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
    parser.add_argument('--plan', action='store_true', help='Verify fixed saved evidence without SDK or runtime writes.')
    args = parser.parse_args()
    try:
        if args.plan:
            value = collect_sources()
            print(json.dumps({'result': value['inventory']['result'], **value['inventory']['inventory'],
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
