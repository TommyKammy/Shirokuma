#!/usr/bin/env python3
"""Bounded same-snapshot Spark/Trino measurements in the local lab."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.spark import run as NATIVE
from tools.local_experiment.spark_iceberg import build as BUILD, remote_contract as REMOTE
from tools.local_experiment.spark_iceberg import remote_run as SHARED, remote_table as ORIGINAL_TABLE
from tools.local_experiment.spark_benchmark import contract as C, workload as W, table as T, client as CLIENT
from tools.local_experiment.iceberg_refs import run as REFS
from tools.local_experiment.iceberg_sandbox import run as SB
from tools.local_experiment.trino_opa.common import directory, require, token as validate_token, run_lock

STATE = ROOT / 'tmp/local-experiment/spark-benchmark'
LIMITS = ['LOCAL_EXPERIMENT_ONLY', 'FIXED_TPCH_DERIVED_WORKLOAD_NOT_STANDARD_TPCH',
          'ONE_PRECREATED_OWNED_TABLE', 'SHARED_HOST_AND_CACHE_NOT_COLD',
          'TRINO_FIRST_SPARK_SECOND_NO_RANDOMIZED_ORDER', 'NO_SPARK_CACHE_OR_PERSIST',
          'POLARIS_READ_ONLY_GRANTS_NOT_S3_READ_ONLY_ENFORCEMENT',
          'STATIC_BUCKET_SCOPED_S3_CREDENTIALS', 'NO_GENERAL_PERFORMANCE_OR_PRODUCTION_CLAIM']


def drain_queries(control, worker, token):
    # A first POST with no returned query ID can still run up to the server's
    # 120-second bound. Retain the normal owned-user polling beyond that horizon.
    return SB.drain_queries(control, worker, token, timeout=150)


def inputs_for(token, build, sealed, realm):
    identity = sealed['identity']
    return C.validate_inputs({'build_token': build['build_token'], 'image': build['image'],
        'table_uuid': identity['table_uuid'], 'table_location': identity['table_location'],
        'initial_snapshot_id': identity['snapshot_id'], 'polaris_uri': REMOTE.POLARIS_URI,
        'polaris_realm': realm, 'warehouse': 'shirokuma_l1', 'scope': 'PRINCIPAL_ROLE:spark_' + token,
        's3_endpoint': REMOTE.S3_ENDPOINT, 's3_region': 'us-east-1', 'data_egress': deepcopy(REMOTE.DATA_EGRESS),
        'sealed': sealed})


def comparison(trino, spark, token, inputs):
    C.validate_measurements(trino, 'trino', token, inputs)
    C.validate_measurements(spark, 'spark', token, inputs)
    rows = []
    for case_id in W.CASES:
        entries = {engine: [v['elapsed_ns'] for v in values if v['case_id'] == case_id]
                   for engine, values in [('trino', trino), ('spark', spark)]}
        rows.append({'case_id': case_id, 'expected_sha256': inputs['sealed']['dataset']['expected'][case_id]['sha256'],
                     'trino_elapsed_ns': entries['trino'], 'spark_elapsed_ns': entries['spark'],
                     'trino_repeat_median_ns': sorted(entries['trino'][1:])[1],
                     'spark_repeat_median_ns': sorted(entries['spark'][1:])[1]})
    return {'verified': True, 'measurements': 16, 'snapshot_id': inputs['initial_snapshot_id'],
            'table_uuid': inputs['table_uuid'], 'row_count': inputs['sealed']['dataset']['row_count'],
            'logical_sha256': inputs['sealed']['dataset']['logical_sha256'], 'queries': rows}


class Workflow:
    def __init__(self, token, output, build, worker, control, admin, access_factory, services,
                 *, table_factory=T.HostTable, native_factory=None, drain=drain_queries,
                 fixture=ORIGINAL_TABLE.fixture_snapshot):
        validate_token(token)
        self.token, self.output, self.build = token, output, build
        self.worker, self.control, self.admin, self.services = worker, control, admin, services
        self.native_factory, self.drain, self.fixture = native_factory or self.native, drain, fixture
        self.report = {'schema_version': 1, 'run_token': token, 'result': 'running', 'started_at': NATIVE.now(),
            'scope': C.SCOPE, 'workload_id': W.WORKLOAD_ID, 'limits': LIMITS,
            'build': {k: build[k] for k in ('build_token', 'image', 'image_id')}, 'services': services,
            'errors': [], 'query_drain': {'verified': False}, 'cleanup': {'verified': False},
            'timing_scope': {'trino': 'host request start through complete result receipt; engine stats separate',
                             'spark': 'spark.sql plus collect; normalization and oracle excluded',
                             'spark_startup': 'SparkSession getOrCreate only; first query may include executor readiness',
                             'native_total': 'native preflight through verified resource cleanup',
                             'source_preparation': 'excluded; streams and file checks may warm shared caches'},
            'source_hashes': self.source_hashes()}
        self.table = table_factory(token, worker, control, admin, progress=self.persist)
        self.access = access_factory(self.table.guard)
        self.spark = None
        worker.progress = control.progress = self.persist

    @staticmethod
    def source_hashes():
        from tools.local_experiment.spark_iceberg import remote_access as A
        paths = [Path(module.__file__) for module in (C, W, T, CLIENT, NATIVE, REMOTE, A, BUILD)] + [Path(__file__)]
        return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}

    def persist(self):
        if hasattr(self, 'table'):
            self.report['table'] = self.table.receipt()
        if hasattr(self, 'access'):
            self.report['access'] = self.access.receipt()
        self.report['queries'] = {key: [{k: deepcopy(v) for k, v in row.items() if not k.startswith('_')}
                                       for row in client.records]
                                  for key, client in [('worker', self.worker), ('control', self.control)]}
        NATIVE.save(self.output / 'summary.json', self.report)

    def native(self, inputs):
        output = NATIVE.output_directory(self.token)
        kube = NATIVE.Kube(self.token, profile='iceberg-benchmark', profile_inputs=inputs,
                          secret_provider=self.access.secret_provider, redactor=self.access.redact)
        return NATIVE.Experiment(self.token, 'success', output, kube=kube,
                                 profile='iceberg-benchmark', profile_inputs=inputs)

    def run(self):
        original, success = None, False
        try:
            self.persist()
            self.report['fixture_before'] = self.fixture(self.control); self.persist()
            sealed = self.table.prepare()
            inputs = inputs_for(self.token, self.build, sealed, self.admin.realm)
            self.report['profile_inputs'] = inputs; self.persist()
            self.report['trino_measurements'] = self.table.measure()
            C.validate_measurements(self.report['trino_measurements'], 'trino', self.token, inputs)
            require(self.table.guard_seal() == sealed, 'BENCH_PRE_SPARK_SEAL_CHANGED')
            identity = sealed['identity']
            scope = self.access.prepare({k: identity[k] for k in ('table_uuid', 'table_location')}, self.services)
            require(scope == {k: v for k, v in inputs.items() if k not in
                              ('build_token', 'image', 'initial_snapshot_id', 'sealed')}, 'BENCH_ACCESS_SCOPE_DIFFER')
            self.spark = self.native_factory(inputs)
            self.report['spark_evidence'] = str(self.spark.output.relative_to(ROOT)); self.persist()
            started = time.perf_counter_ns()
            runtime = self.spark.run()
            self.report['native_total_ns'] = time.perf_counter_ns() - started
            require(runtime['result'] == 'passed' and runtime['cleanup']['verified'], 'BENCH_NATIVE_NOT_VERIFIED')
            self.report['spark_output'] = runtime['output']
            require(self.table.guard_seal() == sealed, 'BENCH_POST_SPARK_SEAL_CHANGED')
            self.report['comparison'] = comparison(self.report['trino_measurements'], runtime['output']['measurements'], self.token, inputs)
            self.persist(); success = True
        except BaseException as error:
            original = error; self.report['failure_code'] = SHARED.failure(error)
        finally:
            stopped = self.spark is None or self.spark.receipt.get('cleanup', {}).get('verified') is True
            self.report['spark_stopped'] = stopped
            try:
                self.report['query_drain'] = self.drain(self.control, self.worker, self.token)
            except BaseException as error:
                self.report['errors'].append('BENCH_QUERY_DRAIN_UNVERIFIED'); original = SHARED.retain_error(original, error)
            drained = self.report['query_drain'].get('verified') is True
            try:
                require(stopped and drained, 'BENCH_READERS_OR_QUERIES_NOT_DRAINED')
                cleanup = self.access.cleanup()
                require(cleanup.get('verified', cleanup.get('cleanup', {}).get('verified')) is True, 'BENCH_ACCESS_CLEANUP_UNVERIFIED')
                self.report['access_cleanup'] = deepcopy(cleanup)
                self.report['cleanup'] = self.table.cleanup(writers_stopped=True, queries_drained=True)
            except BaseException as error:
                self.report['errors'].append(SHARED.failure(error)); original = SHARED.retain_error(original, error)
            try:
                if 'fixture_before' in self.report:
                    self.report['fixture_after'] = self.fixture(self.control)
                    self.report['fixture_unchanged'] = self.report['fixture_before'] == self.report['fixture_after']
                    require(self.report['fixture_unchanged'], 'BENCH_FIXTURE_OR_TABLE_INVENTORY_CHANGED')
            except BaseException as error:
                self.report['errors'].append('BENCH_FIXTURE_READBACK_UNVERIFIED'); original = SHARED.retain_error(original, error)
            passed = success and original is None and self.report['cleanup'].get('verified') is True
            self.report.update(result='passed' if passed else 'interrupted' if original and not isinstance(original, Exception)
                               else 'failed', finished_at=NATIVE.now())
            try:
                self.persist()
            except BaseException as error:
                original = SHARED.retain_error(original, error)
                if isinstance(original, Exception): raise original
        if original is not None and not isinstance(original, Exception): raise original
        return deepcopy(self.report)


def run(build_token):
    from tools.local_experiment.spark_iceberg.remote_access import AdminClient, AccessKube, RemoteAccess, discover_services
    validate_token(build_token)
    token = uuid.uuid4().hex
    directory(STATE, create=True)
    output = STATE / token; output.mkdir(mode=0o700)
    tunnel = {}
    def progress(value):
        tunnel.update(value); NATIVE.save(output / 'tunnel.json', value)
    report = {'run_token': token, 'result': 'failed'}
    try:
        with run_lock(output), SB.SESSION.interruption_handlers():
            build = BUILD.read_verified_image(build_token)
            with REFS.polaris_tunnel(progress) as guard, SB.SESSION.trino_tunnel() as url:
                admin = AdminClient(token, guard); admin.authenticate()
                kube = AccessKube(token); services = discover_services(kube)
                worker, control = CLIENT.Client(url, token), CLIENT.Client(url, token, control=True)
                def access_factory(table_guard):
                    return RemoteAccess(token, output, admin=admin, kube=kube, table_guard=table_guard, read_only=True)
                report = Workflow(token, output, build, worker, control, admin, access_factory, services).run()
            require(tunnel.get('state') == 'verified', 'BENCH_POLARIS_TUNNEL_GUARD_FAILED')
    except BaseException as error:
        path = output / 'summary.json'
        if path.is_file() and not path.is_symlink(): report = json.loads(path.read_bytes())
        report.update(result='failed' if isinstance(error, Exception) else 'interrupted', exit_error=SHARED.failure(error))
        try: NATIVE.save(path, report)
        except BaseException as saving_error:
            if isinstance(error, Exception): raise SHARED.retain_error(error, saving_error)
        if not isinstance(error, Exception): raise
    return report, output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-token', required=True)
    args = parser.parse_args(argv)
    report, output = run(args.build_token)
    print(json.dumps({'result': report['result'], 'run_token': report['run_token'], 'evidence': str(output.relative_to(ROOT))}))
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
