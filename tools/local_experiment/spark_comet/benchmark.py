#!/usr/bin/env python3
"""Two sequential Spark applications over one sealed local Iceberg snapshot."""
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
from tools.local_experiment.spark_benchmark import run as B, workload as W
from tools.local_experiment.spark_comet import build as BUILD, remote_contract as C
from tools.local_experiment.spark_iceberg import remote_run as SHARED
from tools.local_experiment.trino_opa.common import directory, require, token as validate_token, run_lock

STATE = ROOT / 'tmp/local-experiment/spark-comet-benchmark'
PROFILE = 'iceberg-comet-benchmark'
VARIANTS = ('stock', 'comet')
LIMITS = [value for value in B.LIMITS if value != 'TRINO_FIRST_SPARK_SECOND_NO_RANDOMIZED_ORDER'] + [
    'STOCK_FIRST_COMET_SECOND_NO_RANDOMIZED_ORDER', 'NO_TRINO_TIMING_PASS']


def inputs_for(token, build, sealed, realm, variant):
    require(variant in VARIANTS, 'COMET_VARIANT_INVALID')
    # The stock contract pins its own image; construct the common fields with
    # that image before the new contract binds the selected variant image.
    base = B.inputs_for(token, dict(build, image=B.REMOTE.IMAGE), sealed, realm)
    return C.validate_inputs(dict(base, image=build['image'], variant=variant))


def comparison(outputs, token, inputs):
    require(set(outputs) == set(inputs) == set(VARIANTS), 'COMET_VARIANTS_INCOMPLETE')
    baseline = inputs['stock']
    require({k: v for k, v in inputs['comet'].items() if k not in ('variant', 'build_token', 'image')} ==
            {k: v for k, v in baseline.items() if k not in ('variant', 'build_token', 'image')},
            'COMET_COMPARISON_SOURCE_CHANGED')
    for variant in VARIANTS:
        value = C.validate_output(outputs[variant], token, inputs[variant],
                                  executor_pod_name=NATIVE.C.executor_name(token))
        require(value['result'] == 'passed', 'COMET_OUTPUT_NOT_PASSED')
        C.validate_measurements(value['measurements'], 'spark', token, inputs[variant])
    rows = []
    for case_id in W.CASES:
        samples = {variant: [v['elapsed_ns'] for v in outputs[variant]['measurements']
                             if v['case_id'] == case_id] for variant in VARIANTS}
        rows.append({'case_id': case_id,
                     'expected_sha256': baseline['sealed']['dataset']['expected'][case_id]['sha256'],
                     **{variant + '_elapsed_ns': samples[variant] for variant in VARIANTS},
                     **{variant + '_repeat_median_ns': sorted(samples[variant][1:])[1] for variant in VARIANTS}})
    return {'verified': True, 'measurements': 16, 'execution_order': list(VARIANTS),
            'snapshot_id': baseline['initial_snapshot_id'], 'table_uuid': baseline['table_uuid'],
            'row_count': baseline['sealed']['dataset']['row_count'],
            'logical_sha256': baseline['sealed']['dataset']['logical_sha256'], 'queries': rows}


class Workflow(B.Workflow):
    """Reuse the established table/access journals, changing only execution order."""
    def __init__(self, token, output, builds, worker, control, admin, access_factory, services,
                 *, table_factory=B.T.HostTable, native_factory=None, drain=B.drain_queries,
                 fixture=B.ORIGINAL_TABLE.fixture_snapshot):
        require(type(builds) is dict and set(builds) == set(VARIANTS), 'COMET_BUILDS_INVALID')
        super().__init__(token, output, builds['stock'], worker, control, admin, access_factory, services,
                         table_factory=table_factory, native_factory=native_factory,
                         drain=drain, fixture=fixture)
        self.builds, self.applications = deepcopy(builds), {}
        del self.report['build']
        self.report.update(profile=PROFILE, scope=C.SCOPE, limits=LIMITS,
            builds={variant: {k: builds[variant][k] for k in ('build_token', 'image', 'image_id')}
                    for variant in VARIANTS}, profile_inputs={}, applications={},
            timing_scope={'query': 'spark.sql plus collect; normalization and oracle excluded',
                          'startup': 'SparkSession getOrCreate; executor readiness may affect first query',
                          'application_total': 'preflight through verified namespace cleanup',
                          'source_preparation': 'excluded; preparation and stock queries may warm shared caches'},
            execution_order=list(VARIANTS))

    @staticmethod
    def source_hashes():
        from tools.local_experiment.spark_iceberg import remote_access as A
        modules = (B, B.C, W, B.T, B.CLIENT, NATIVE, B.REMOTE, A, B.BUILD, BUILD, C, SHARED)
        paths = [Path(module.__file__) for module in modules] + [Path(__file__)]
        return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}

    def native(self, inputs):
        output = self.output / (inputs['variant'] + '-native')
        require(not output.exists() and not output.is_symlink(), 'COMET_APPLICATION_ALREADY_EXISTS')
        output.mkdir(mode=0o700)
        kube = NATIVE.Kube(self.token, profile=PROFILE, profile_inputs=inputs,
                          secret_provider=self.access.secret_provider, redactor=self.access.redact)
        return NATIVE.Experiment(self.token, 'success', output, kube=kube,
                                 profile=PROFILE, profile_inputs=inputs)

    def closed(self, variant):
        instance = self.applications[variant]
        require(instance.receipt.get('cleanup', {}).get('verified') is True, 'COMET_NAMESPACE_CLEANUP_UNVERIFIED')
        namespaces = [item for item in instance.receipt.get('known', []) if item['kind'] == 'Namespace']
        require(len(namespaces) == 1 and namespaces[0]['name'] == NATIVE.C.namespace(self.token),
                'COMET_NAMESPACE_UID_MISSING')
        require(NATIVE.UUID.fullmatch(namespaces[0]['uid']) is not None, 'COMET_NAMESPACE_UID_INVALID')
        require(instance.kube.get('Namespace', namespaces[0]['name']) is None, 'COMET_NAMESPACE_STILL_PRESENT')
        return {'verified': True, 'namespace_uid': namespaces[0]['uid'], 'namespace_absent': True}

    def run(self):
        original, success = None, False
        try:
            self.persist()
            self.report['fixture_before'] = self.fixture(self.control); self.persist()
            sealed = self.table.prepare()
            all_inputs = {variant: inputs_for(self.token, self.builds[variant], sealed, self.admin.realm, variant)
                          for variant in VARIANTS}
            self.report['profile_inputs'] = all_inputs; self.persist()
            require(self.table.guard_seal() == sealed, 'COMET_PRE_APPLICATION_SEAL_CHANGED')
            identity = sealed['identity']
            scope = self.access.prepare({k: identity[k] for k in ('table_uuid', 'table_location')}, self.services)
            require(scope == {k: v for k, v in all_inputs['stock'].items() if k not in
                              ('build_token', 'image', 'initial_snapshot_id', 'sealed', 'variant')},
                    'COMET_ACCESS_SCOPE_DIFFER')
            outputs = {}
            for variant in VARIANTS:
                if variant == 'comet':
                    self.report['applications']['stock']['closure'] = self.closed('stock')
                    require(self.table.guard_seal() == sealed, 'COMET_BETWEEN_APPLICATIONS_SEAL_CHANGED')
                instance = self.native_factory(all_inputs[variant])
                self.spark = instance; self.applications[variant] = instance
                self.report['applications'][variant] = {'evidence': variant + '-native', 'cleanup_verified': False}
                self.persist()
                started = time.perf_counter_ns()
                runtime = instance.run()
                entry = self.report['applications'][variant]
                entry['total_ns'] = time.perf_counter_ns() - started
                require(runtime.get('result') == 'passed' and runtime.get('cleanup', {}).get('verified') is True,
                        'COMET_APPLICATION_NOT_VERIFIED')
                entry['closure'] = self.closed(variant)
                if variant == 'comet':
                    require(entry['closure']['namespace_uid'] !=
                            self.report['applications']['stock']['closure']['namespace_uid'],
                            'COMET_NAMESPACE_UID_REUSED')
                entry['cleanup_verified'] = True
                outputs[variant] = C.validate_output(runtime['output'], self.token, all_inputs[variant],
                    executor_pod_name=NATIVE.C.executor_name(self.token))
                entry['output'] = outputs[variant]
                require(self.table.guard_seal() == sealed, 'COMET_POST_APPLICATION_SEAL_CHANGED')
                self.persist()
            self.report['comparison'] = comparison(outputs, self.token, all_inputs)
            self.persist(); success = True
        except BaseException as error:
            original = error; self.report['failure_code'] = SHARED.failure(error)
        finally:
            stopped = False
            try:
                stopped = all(item.receipt.get('cleanup', {}).get('verified') is True and
                              item.kube.get('Namespace', NATIVE.C.namespace(self.token)) is None
                              for item in self.applications.values())
            except BaseException as error:
                original = SHARED.retain_error(original, error)
            self.report['spark_stopped'] = stopped
            try:
                self.report['query_drain'] = self.drain(self.control, self.worker, self.token)
            except BaseException as error:
                self.report['errors'].append('COMET_QUERY_DRAIN_UNVERIFIED')
                original = SHARED.retain_error(original, error)
            try:
                require(stopped and self.report['query_drain'].get('verified') is True,
                        'COMET_READERS_OR_QUERIES_NOT_DRAINED')
                cleanup = self.access.cleanup()
                require(cleanup.get('verified', cleanup.get('cleanup', {}).get('verified')) is True,
                        'COMET_ACCESS_CLEANUP_UNVERIFIED')
                self.report['access_cleanup'] = deepcopy(cleanup)
                self.report['cleanup'] = self.table.cleanup(writers_stopped=True, queries_drained=True)
            except BaseException as error:
                self.report['errors'].append(SHARED.failure(error)); original = SHARED.retain_error(original, error)
            try:
                if 'fixture_before' in self.report:
                    self.report['fixture_after'] = self.fixture(self.control)
                    self.report['fixture_unchanged'] = self.report['fixture_before'] == self.report['fixture_after']
                    require(self.report['fixture_unchanged'], 'COMET_FIXTURE_OR_TABLE_INVENTORY_CHANGED')
            except BaseException as error:
                self.report['errors'].append('COMET_FIXTURE_READBACK_UNVERIFIED')
                original = SHARED.retain_error(original, error)
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


def run(stock_build_token, comet_build_token):
    from tools.local_experiment.spark_iceberg.remote_access import AdminClient, AccessKube, RemoteAccess, discover_services
    validate_token(stock_build_token); validate_token(comet_build_token)
    token = uuid.uuid4().hex
    directory(STATE, create=True)
    output = STATE / token; output.mkdir(mode=0o700)
    tunnel = {}
    def progress(value):
        tunnel.update(value); NATIVE.save(output / 'tunnel.json', value)
    report = {'run_token': token, 'result': 'failed', 'profile': PROFILE}
    try:
        with run_lock(output), B.SB.SESSION.interruption_handlers():
            builds = {'stock': B.BUILD.read_verified_image(stock_build_token),
                      'comet': BUILD.read_verified_image(comet_build_token)}
            require(builds['comet']['base_build_token'] == stock_build_token, 'COMET_STOCK_BASE_DIFFER')
            with B.REFS.polaris_tunnel(progress) as guard, B.SB.SESSION.trino_tunnel() as url:
                admin = AdminClient(token, guard); admin.authenticate()
                kube = AccessKube(token); services = discover_services(kube)
                worker, control = B.CLIENT.Client(url, token), B.CLIENT.Client(url, token, control=True)
                def access_factory(table_guard):
                    return RemoteAccess(token, output, admin=admin, kube=kube, table_guard=table_guard, read_only=True)
                report = Workflow(token, output, builds, worker, control, admin, access_factory, services).run()
            require(tunnel.get('state') == 'verified', 'COMET_POLARIS_TUNNEL_GUARD_FAILED')
    except BaseException as error:
        path = output / 'summary.json'
        try:
            if path.is_file() and not path.is_symlink(): report = json.loads(B.BUILD.file_bytes(path, limit=4*1024**2))
        except BaseException as reading_error:
            error = SHARED.retain_error(error, reading_error)
        report.update(result='failed' if isinstance(error, Exception) else 'interrupted', exit_error=SHARED.failure(error))
        try: NATIVE.save(path, report)
        except BaseException as saving_error:
            if isinstance(error, Exception): raise SHARED.retain_error(error, saving_error)
        if not isinstance(error, Exception): raise error
    return report, output


def recover(token, variant, *, kube=None):
    """Close only the recorded namespace; never rerun queries or adopt host cleanup."""
    validate_token(token); require(variant in VARIANTS, 'COMET_VARIANT_INVALID')
    root = STATE / token; B.BUILD.private(STATE); B.BUILD.private(root)
    with run_lock(root), B.SB.SESSION.interruption_handlers():
        output = root / (variant + '-native'); B.BUILD.private(output)
        raw = B.BUILD.file_bytes(output / 'summary.json', limit=4*1024**2)
        receipt = json.loads(raw)
        plan = json.loads(B.BUILD.file_bytes(output / 'plan.json', limit=4*1024**2))
        outer = json.loads(B.BUILD.file_bytes(root / 'summary.json', limit=4*1024**2))
        require(outer.get('run_token') == receipt.get('run_token') == token and
                outer.get('profile') == receipt.get('profile') == PROFILE and receipt.get('mode') == 'success',
                'COMET_RECOVERY_SCOPE_INVALID')
        inputs = C.validate_inputs(receipt['profile_inputs'])
        require(inputs['variant'] == variant and outer['profile_inputs'][variant] == inputs and
                outer['applications'][variant]['evidence'] == variant + '-native', 'COMET_RECOVERY_INPUTS_CHANGED')
        require(all(inputs[k] == outer['builds'][variant][k] for k in ('build_token', 'image')),
                'COMET_RECOVERY_BUILD_CHANGED')
        NATIVE.validate_saved(plan, receipt)
        C.validate_saved_plan(plan, token, inputs, api_endpoints=receipt['preflight']['api_endpoints'])
        selected = kube or NATIVE.Kube(token, profile=PROFILE, profile_inputs=inputs)
        selected.recovery_guard(receipt['preflight'])
        recovery = output / ('recovery-' + uuid.uuid4().hex); recovery.mkdir(mode=0o700)
        instance = NATIVE.Experiment(token, 'success', recovery, kube=selected,
                                     profile=PROFILE, profile_inputs=inputs)
        instance.receipt = deepcopy(receipt)
        instance.receipt.update(result='recovery', recovered_from_sha256=hashlib.sha256(raw).hexdigest(),
                                recovery_scope='recorded-spark-namespace-only')
        instance.persist()
        instance.cleanup(); instance.persist()
        require(instance.receipt['cleanup']['verified'], 'COMET_RECOVERY_UNVERIFIED')
        require(selected.get('Namespace', receipt['namespace']) is None, 'COMET_NAMESPACE_STILL_PRESENT')
        return deepcopy(instance.receipt)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    start = commands.add_parser('run')
    start.add_argument('--stock-build-token', required=True); start.add_argument('--comet-build-token', required=True)
    recovery = commands.add_parser('recover')
    recovery.add_argument('--run-token', required=True); recovery.add_argument('--variant', choices=VARIANTS, required=True)
    args = parser.parse_args(argv)
    if args.action == 'recover':
        value = recover(args.run_token, args.variant)
        print(json.dumps({'result': 'recovered', 'run_token': args.run_token, 'variant': args.variant,
                          'cleanup_verified': value['cleanup']['verified'],
                          'scope': 'recorded-spark-namespace-only'}))
        return 0
    report, output = run(args.stock_build_token, args.comet_build_token)
    print(json.dumps({'result': report['result'], 'run_token': report['run_token'],
                      'evidence': str(output.relative_to(ROOT))}))
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
