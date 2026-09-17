#!/usr/bin/env python3
"""Publish one verified descendant branch to main on a fresh owned table."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.spark import run as NATIVE
from tools.local_experiment.spark_iceberg import build as BUILD, remote_contract as RC
from tools.local_experiment.spark_publish import contract as C, table as T
from tools.local_experiment.iceberg_refs import run as REFS
from tools.local_experiment.iceberg_sandbox import run as SB
from tools.local_experiment.trino_opa.common import directory, require, token as validate_token, run_lock

STATE = ROOT / 'tmp/local-experiment/spark-publish'


def failure(error):
    text = str(error)
    return text if re.fullmatch('[A-Z][A-Z0-9_]{0,99}', text) else 'PUBLISH_EXPERIMENT_FAILED'


def retain_error(original, error):
    # The first cancellation must survive later cleanup and evidence failures.
    if original is None or (isinstance(original, Exception) and not isinstance(error, Exception)):
        return error
    return original


def inputs_for(token, build, sealed, realm):
    value = {'build_token': build['build_token'], 'image': build['image'],
        'table_uuid': sealed['identity']['table_uuid'], 'table_location': sealed['identity']['table_location'],
        'initial_snapshot_id': sealed['snapshot_b'],
        'polaris_uri': RC.POLARIS_URI, 'polaris_realm': realm, 'warehouse': 'shirokuma_l1',
        'scope': 'PRINCIPAL_ROLE:spark_' + token, 's3_endpoint': RC.S3_ENDPOINT,
        's3_region': 'us-east-1', 'data_egress': deepcopy(RC.DATA_EGRESS), 'sealed': deepcopy(sealed)}
    C._inputs(value, token)
    return value


class Workflow:
    def __init__(self, token, output, build, worker, control, admin, access_factory, services,
                 *, mode='positive', native_factory=None, drain=SB.drain_queries, fixture=T.fixture_snapshot):
        validate_token(token)
        require(mode in ('positive', 'negative'), 'PUBLISH_MODE_INVALID')
        self.mode = mode
        self.access_cleanup_attempted = False
        self.token, self.output, self.build = token, output, build
        self.worker, self.control, self.admin, self.services = worker, control, admin, services
        self.native_factory = native_factory or self.native
        self.drain, self.fixture = drain, fixture
        self.report = {'schema_version': 1, 'run_token': token, 'result': 'running', 'started_at': NATIVE.now(),
                       'scope': C.SCOPE, 'mode': mode, 'limits': ['LOCAL_EXPERIMENT_ONLY', 'ONE_PRECREATED_OWNED_TABLE',
                       'NAMED_REFS_ON_ONE_TABLE_NOT_ZERO_COPY_CLONE', 'ONE_BRANCH_INSERT_NO_BLIND_RETRY', 'ONE_MAIN_CAS_NO_BLIND_RETRY',
                       'HOST_ANCESTRY_GUARD_NOT_GENERIC_MERGE',
                       'NO_CONCURRENT_WRITER_OR_BRANCH_AUTHORIZATION_CLAIM',
                       'STATIC_BUCKET_SCOPED_S3_CREDENTIALS', 'POLARIS_TABLE_SCOPE_IS_NOT_S3_PREFIX_ENFORCEMENT',
                       'TOKEN_TTL_IS_SERVER_REPORTED_NOT_IMMEDIATE_REVOCATION', 'NO_PRODUCTION_ACCEPTANCE'],
                       'build': {k: build[k] for k in ('build_token', 'image', 'image_id')},
                       'services': services, 'errors': [], 'query_drain': {'verified': False},
                       'cleanup': {'verified': False}, 'source_hashes': self.source_hashes()}
        self.table = T.HostTable(token, worker, control, admin, progress=self.persist, mode=mode)
        self.access = access_factory(self.table.guard)
        self.spark = None
        worker.progress = control.progress = self.persist

    @staticmethod
    def source_hashes():
        paths = [Path(__file__), Path(T.__file__), Path(C.__file__), Path(NATIVE.__file__),
                 ROOT / 'tools/local_experiment/spark_iceberg/remote_access.py', Path(RC.__file__), Path(BUILD.__file__)]
        # Seal inherited table/transport/cleanup implementations as well as the
        # new publisher: their code is part of the mutation boundary.
        paths += [ROOT / 'tools/local_experiment' / relative for relative in (
            'spark_branch/table.py', 'spark_branch/contract.py', 'spark_iceberg/remote_table.py',
            'spark/contract.py', 'spark/prepare.py', 'iceberg_refs/run.py', 'iceberg_refs/client.py',
            'iceberg_refs/contract.py', 'iceberg_sandbox/run.py',
            'trino_opa/common.py', 'local_session.py', 'local_trino_smoke.py')]
        paths += [p for p in Path(__file__).parent.glob('*.py') if p not in paths]
        return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()}

    def persist(self):
        self.report['table'] = self.table.receipt()
        if hasattr(self, 'access'):
            self.report['access'] = self.access.receipt()
        self.report['queries'] = {key: [{k: deepcopy(v) for k, v in row.items() if not k.startswith('_')}
                                        for row in client.records]
                                   for key, client in [('worker', self.worker), ('control', self.control)]}
        NATIVE.save(self.output / 'summary.json', self.report)

    def native(self, inputs):
        output = NATIVE.output_directory(self.token)
        kube = NATIVE.Kube(self.token, profile='iceberg-publish', profile_inputs=inputs,
                          secret_provider=self.access.secret_provider, redactor=self.access.redact)
        return NATIVE.Experiment(self.token, 'success', output, kube=kube,
                                 profile='iceberg-publish', profile_inputs=inputs)

    def stop_access(self):
        require(not self.access_cleanup_attempted, 'ACCESS_CLEANUP_ALREADY_ATTEMPTED')
        self.access_cleanup_attempted = True
        value = self.access.cleanup()
        verified = value.get('verified', value.get('cleanup', {}).get('verified'))
        require(verified is True, 'ACCESS_CLEANUP_UNVERIFIED')
        self.report['access_cleanup'] = deepcopy(value)
        self.persist()

    def publish(self, sealed):
        require(self.report.get('query_drain', {}).get('verified') is True
                and 'access_cleanup' in self.report and self.spark.receipt['cleanup']['verified'] is True,
                'PUBLISH_WRITER_CLEANUP_UNVERIFIED')
        require(self.report['source_hashes'] == self.source_hashes(), 'PUBLISH_SOURCE_CHANGED')
        if self.mode == 'positive':
            plan = self.table.plan_publish(sealed['snapshot_b'])
            require(plan['outcome'] == 'ready', 'FAST_FORWARD_PLAN_REJECTED')
            self.report['publish_plan'] = deepcopy(plan)
            self.persist()
            result = self.table.publish(plan)
            self.report['publish_result'] = deepcopy(result)
            self.persist()
            require(result['outcome'] == 'verified' and result['metadata_only'] is True,
                    'MAIN_PUBLISH_UNVERIFIED')
        else:
            self.report['negative_main'] = self.table.negative_main_append()
            self.persist()
            rejections = []
            for expected,reason in [(sealed['snapshot_b'], 'STALE_MAIN'),
                                    (self.report['negative_main']['snapshot_d'], 'MAIN_NOT_ANCESTOR')]:
                plan = self.table.plan_publish(expected)
                rejections.append(deepcopy(plan))
                self.report['publish_rejections'] = rejections
                self.persist()
                require(plan['outcome'] == 'rejected' and plan['reason'] == reason
                        and plan['body_sha256'] is None, 'DIVERGED_MAIN_NOT_REJECTED')
            self.report['negative_verification'] = self.table.verify_negative()
            require(self.report['negative_verification']['verified'] is True, 'NEGATIVE_READBACK_UNVERIFIED')
            self.persist()

    def run(self):
        original = None
        success = False
        try:
            self.persist()
            self.report['fixture_before'] = self.fixture(self.control)
            self.persist()
            sealed = self.table.prepare()
            access_scope = self.access.prepare({k: sealed['identity'][k] for k in ('table_uuid', 'table_location')}, self.services)
            inputs = inputs_for(self.token, self.build, sealed, self.admin.realm)
            require(access_scope == {k: v for k, v in inputs.items() if k not in ('build_token', 'image', 'initial_snapshot_id', 'sealed')},
                    'ACCESS_AND_WORKLOAD_SCOPE_DIFFER')
            self.report['profile_inputs'] = inputs
            self.persist()
            self.spark = self.native_factory(inputs)
            self.report['spark_evidence'] = str(self.spark.output.relative_to(ROOT))
            self.persist()
            self.table.mark_spark_pending()
            runtime = self.spark.run()
            require(runtime['result'] == 'passed' and runtime['cleanup']['verified'], 'SPARK_RUNTIME_NOT_VERIFIED')
            self.report['spark_output'] = runtime['output']
            self.report['cross_engine'] = self.table.verify_written(runtime['output'])
            self.persist()
            # Native resources must be gone and the temporary writer access
            # revoked before the host alone evaluates or sends a main commit.
            self.report['query_drain'] = self.drain(self.control, self.worker, self.token)
            require(self.report['query_drain'].get('verified') is True, 'PUBLISH_QUERIES_NOT_DRAINED')
            self.stop_access()
            self.publish(sealed)
            success = True
        except BaseException as error:
            original = error
            self.report['failure_code'] = failure(error)
        finally:
            writers_stopped = self.spark is None or self.spark.receipt.get('cleanup', {}).get('verified') is True
            self.report['writers_stopped'] = writers_stopped
            try:
                # A prior drain is not evidence for this cleanup boundary.
                self.report['query_drain'] = {'verified': False}
                self.report['query_drain'] = self.drain(self.control, self.worker, self.token)
            except BaseException as error:
                self.report['errors'].append('QUERY_DRAIN_UNVERIFIED')
                original = retain_error(original, error)
            drained = self.report['query_drain'].get('verified') is True
            try:
                require(writers_stopped and drained, 'WRITERS_OR_QUERIES_NOT_DRAINED')
                if not self.access_cleanup_attempted:
                    self.stop_access()
                require('access_cleanup' in self.report, 'ACCESS_CLEANUP_UNVERIFIED')
                self.report['cleanup'] = self.table.cleanup(writers_stopped=True, queries_drained=True)
            except BaseException as error:
                self.report['errors'].append(failure(error))
                original = retain_error(original, error)
            try:
                if 'fixture_before' in self.report:
                    self.report['fixture_after'] = self.fixture(self.control)
                    self.report['fixture_unchanged'] = self.report['fixture_before'] == self.report['fixture_after']
                    require(self.report['fixture_unchanged'], 'FIXTURE_OR_TABLE_INVENTORY_CHANGED')
            except BaseException as error:
                self.report['errors'].append('FIXTURE_READBACK_UNVERIFIED')
                original = retain_error(original, error)
            passed = success and original is None and self.report['cleanup'].get('verified') is True
            self.report.update(result='passed' if passed else 'interrupted' if original and not isinstance(original, Exception)
                               else 'failed', finished_at=NATIVE.now())
            try:
                self.persist()
            except BaseException as error:
                original = retain_error(original, error)
                if isinstance(original, Exception):
                    raise original
        if original is not None and not isinstance(original, Exception):
            raise original
        return deepcopy(self.report)


def run(build_token, mode='positive'):
    from tools.local_experiment.spark_iceberg.remote_access import AccessKube, RemoteAccess, discover_services
    validate_token(build_token)
    require(mode in ('positive', 'negative'), 'PUBLISH_MODE_INVALID')
    token = uuid.uuid4().hex
    directory(STATE, create=True)
    output = STATE / token
    output.mkdir(mode=0o700)
    tunnel = {}
    def progress(value):
        tunnel.update(value)
        NATIVE.save(output / 'tunnel.json', value)
    report = {'run_token': token, 'result': 'failed'}
    try:
        with run_lock(output), SB.SESSION.interruption_handlers():
            build = BUILD.read_verified_image(build_token)
            with REFS.polaris_tunnel(progress) as guard, SB.SESSION.trino_tunnel() as url:
                admin = T.PublishAdmin(token, guard)
                admin.authenticate()
                kube = AccessKube(token)
                services = discover_services(kube)
                worker, control = SB.SandboxClient(url, token), SB.SandboxClient(url, token, control=True)
                def access_factory(table_guard):
                    return RemoteAccess(token, output, admin=admin, kube=kube, table_guard=table_guard)
                workflow = Workflow(token, output, build, worker, control, admin, access_factory, services, mode=mode)
                report = workflow.run()
            require(tunnel.get('state') == 'verified', 'POLARIS_TUNNEL_FINAL_GUARD_FAILED')
    except BaseException as error:
        path = output / 'summary.json'
        if path.is_file() and not path.is_symlink():
            report = json.loads(path.read_bytes())
        report.update(result='failed' if isinstance(error, Exception) else 'interrupted', exit_error=failure(error))
        try:
            NATIVE.save(path, report)
        except BaseException as saving_error:
            if isinstance(error, Exception):
                raise retain_error(error, saving_error)
        if not isinstance(error, Exception):
            raise
    return report, output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-token', required=True)
    parser.add_argument('--mode', choices=('positive', 'negative'), required=True)
    args = parser.parse_args(argv)
    report, output = run(args.build_token, args.mode)
    print(json.dumps({'result': report['result'], 'run_token': report['run_token'],
                      'evidence': str(output.relative_to(ROOT))}))
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
