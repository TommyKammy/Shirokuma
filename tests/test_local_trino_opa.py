import copy
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / 'tools/local_experiment/trino_opa'
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location('trino_opa_runner', HERE / 'run.py')
RUN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUN)
import common as COMMON


def build():
    return {'build_token': 'b' * 32, 'result': 'passed', 'inputs_sha256': RUN.digest(RUN.PINS),
            'policy_sha256': RUN.source_hashes(), 'images': {
                'trino': 'shirokuma.local/trino-opa@sha256:' + '1' * 64,
                'opa': 'shirokuma.local/opa@sha256:' + '2' * 64}}


class FakeTrino:
    cases = []
    failure = None
    generic_unavailable = False

    def __init__(self, url):
        self.url = url

    def ready(self):
        pass

    def query(self, name, user, sql, progress):
        query_id = f'20260917_050000_{len(self.cases):05}_abcde'
        expected = next((c[3] for c in RUN.CASES if c[0] == name), None)
        value = {'case': name, 'user': user, 'query_id': query_id, 'rows': expected or [],
                 'result': 'success' if expected is not None else 'denied', 'error_name': None,
                 'opa_transport_failure': False, 'response_complete': True, 'sql_sha256': RUN.digest(sql.encode())}
        if name == 'opa_unavailable':
            value.update(result='failed', opa_transport_failure=not self.generic_unavailable)
        progress(value)
        if self.failure == name:
            raise TimeoutError('private failure details')
        self.cases.append(copy.deepcopy(value))
        return value


def decisions(cases):
    result = []
    for case in cases:
        if case['case'] == 'opa_unavailable':
            continue
        def add(operation, answer, path='trino/allow'):
            result.append({'type': 'openpolicyagent.org/decision_logs', 'path': path,
                           'decision_id': str(uuid.uuid4()),
                           'input': {'context': {'queryId': case['query_id'], 'identity': {'user': case['user']}},
                                     'action': {'operation': operation}}, 'result': answer})
        add('ExecuteQuery' if case['case'] == 'denied_user' else 'SelectFromColumns', case['result'] == 'success')
        if case['case'] in {'row_filter', 'combined'}:
            add('GetRowFilters', [{'expression': 'nationkey = 0'}], 'trino/rowFilters')
        if case['case'] in {'column_mask', 'combined'}:
            add('GetColumnMask', {'expression': "'MASKED'"}, 'trino/columnMask')
    return result


class FakeKube:
    def __init__(self):
        self.objects, self.deleted, self.created = {}, [], []
        self.lose_create = None
        self.max_objects = 0

    def get(self, obj):
        return copy.deepcopy(self.objects.get(RUN.identity(obj)))

    def dry_run(self, obj):
        pass

    def create(self, obj):
        value = copy.deepcopy(obj)
        value['metadata'].update(uid=str(uuid.uuid4()), resourceVersion='1')
        if obj['kind'] == 'Pod':
            value['status'] = {'phase': 'Running', 'containerStatuses': [
                {'name': 'trino', 'ready': True, 'restartCount': 0}, {'name': 'opa', 'ready': True, 'restartCount': 0}]}
        self.objects[RUN.identity(obj)] = value
        self.created.append(RUN.identity(obj))
        self.max_objects = max(self.max_objects, len(self.objects))
        if self.lose_create == obj['kind']:
            self.lose_create = None
            raise TimeoutError('create response was lost')
        return copy.deepcopy(value)

    def delete(self, obj, current):
        actual = self.objects[RUN.identity(obj)]
        if actual['metadata']['uid'] != current['metadata']['uid']:
            raise RUN.ExperimentError('UID_PRECONDITION_FAILED')
        self.deleted.append((RUN.identity(obj), current['metadata']['uid']))
        del self.objects[RUN.identity(obj)]

    def logs(self, _):
        return '\n'.join(json.dumps(d) for d in decisions(FakeTrino.cases))

    def diagnostics(self, pod, container):
        return '{"level":"error","message":"synthetic private diagnostic"}\n'


@contextmanager
def fake_tunnel(pod):
    yield 'http://127.0.0.1:12345'


class TrinoOpaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.output = Path(self.temporary.name).resolve()
        self.output.chmod(0o700)
        self.token = 'a' * 32
        FakeTrino.cases, FakeTrino.failure, FakeTrino.generic_unavailable = [], None, False

    def tearDown(self):
        self.temporary.cleanup()

    def execute(self, kube):
        return RUN.execute(build(), self.token, self.output, kube, fake_tunnel, FakeTrino)

    def test_render_has_no_retained_credentials_storage_service_or_network_egress(self):
        objects = RUN.render(build(), self.token, 'normal')
        self.assertEqual([o['kind'] for o in objects], ['NetworkPolicy', 'ConfigMap', 'Pod'])
        pod = objects[-1]
        self.assertFalse(pod['spec']['automountServiceAccountToken'])
        self.assertEqual(pod['spec']['containers'][0]['resources']['limits'], {'cpu': '2', 'memory': '4Gi'})
        self.assertEqual(pod['spec']['containers'][1]['resources']['limits'], {'cpu': '500m', 'memory': '256Mi'})
        self.assertEqual(objects[0]['spec']['ingress'], [])
        self.assertEqual(objects[0]['spec']['egress'], [])
        raw = RUN.canonical(objects)
        for field in ('secretKeyRef', 'persistentVolumeClaim', 'hostPath', 'iceberg.properties', 'POLARIS', 'AWS_', 'NodePort'):
            self.assertNotIn(field, raw)
        self.assertEqual([v['emptyDir']['sizeLimit'] for v in pod['spec']['volumes'] if 'emptyDir' in v], ['256Mi', '512Mi'])
        self.assertIn('access-control.name=read-only', raw)

    def test_unavailable_config_changes_only_fixed_opa_port(self):
        good, bad = RUN.configs('normal'), RUN.configs('unavailable')
        self.assertEqual({k: v for k, v in good.items() if k != 'opa.properties'},
                         {k: v for k, v in bad.items() if k != 'opa.properties'})
        self.assertEqual(good['opa.properties'].replace(':8181/', ':8182/'), bad['opa.properties'])
        with self.assertRaises(RUN.ExperimentError):
            RUN.configs('untrusted')

    def test_full_workflow_is_sequential_and_cleans_exact_created_uids(self):
        kube = FakeKube()
        result = self.execute(kube)
        self.assertEqual(result['result'], 'passed')
        self.assertFalse(result['authentication_verified'])
        self.assertEqual(len(result['cases']), 8)
        self.assertTrue(result['cleanup']['verified'])
        self.assertEqual(len(result['phase_cleanup'][0]['removed']), 3)
        self.assertEqual(len(kube.deleted), 6)
        self.assertEqual(kube.max_objects, 3)
        self.assertFalse(kube.objects)
        self.assertGreater(len(result['decisions']), 7)
        self.assertEqual((self.output / 'receipt.json').stat().st_mode & 0o777, 0o600)

    def test_collision_is_checked_before_all_mutations(self):
        kube = FakeKube()
        pod = RUN.render(build(), self.token, 'unavailable')[-1]
        kube.objects[RUN.identity(pod)] = {'retained': True}
        with self.assertRaisesRegex(RUN.ExperimentError, 'OBJECT_NAME_COLLISION'):
            self.execute(kube)
        self.assertFalse(kube.created)
        self.assertFalse(kube.deleted)
        self.assertEqual(kube.objects[RUN.identity(pod)], {'retained': True})

    def test_unknown_create_uid_is_deferred_and_keeps_pod_isolation(self):
        kube = FakeKube()
        kube.lose_create = 'Pod'
        with self.assertRaises(TimeoutError):
            self.execute(kube)
        result = json.loads((self.output / 'receipt.json').read_text())
        self.assertEqual(result['result'], 'failed')
        self.assertFalse(result['cleanup']['verified'])
        self.assertFalse(kube.deleted)
        self.assertEqual(len(kube.objects), 3)
        self.assertIsNone(result['objects'][-1]['uid'])
        self.assertEqual(result['cleanup']['deferred'], [result['objects'][-1]['key']])

    def test_create_collision_after_preflight_never_adopts_other_uid(self):
        kube = FakeKube()
        create = kube.create
        def collision(obj):
            value = create(obj)
            if obj['kind'] == 'Pod':
                raise RUN.ExperimentError('CREATE_CONFLICT_409')
            return value
        with patch.object(kube, 'create', side_effect=collision), self.assertRaisesRegex(RUN.ExperimentError, 'CREATE_CONFLICT_409'):
            self.execute(kube)
        self.assertFalse(kube.deleted)
        self.assertEqual(len(kube.objects), 3)

    def test_replaced_uid_is_preserved_with_its_isolation_and_config(self):
        kube = FakeKube()
        objects = RUN.render(build(), self.token, 'normal')
        records = []
        for obj in objects:
            value = kube.create(obj)
            records.append({'key': '/'.join(RUN.identity(obj)), 'uid': value['metadata']['uid']})
        kube.objects[RUN.identity(objects[-1])]['metadata']['uid'] = str(uuid.uuid4())
        receipt = {'objects': records}
        RUN.cleanup(kube, receipt, objects, lambda: None)
        self.assertFalse(receipt['cleanup']['verified'])
        self.assertEqual(len(kube.objects), 3)
        self.assertFalse(kube.deleted)

    def test_content_drift_and_retained_volume_injection_are_rejected(self):
        expected = RUN.render(build(), self.token, 'normal')[-1]
        current = FakeKube().create(expected)
        current['spec']['containers'][0]['envFrom'] = [{'secretRef': {'name': 'retained-secret'}}]
        with self.assertRaisesRegex(RUN.ExperimentError, 'POD_SCOPE_CONFLICT'):
            RUN.assert_owned(current, expected)
        current = FakeKube().create(expected)
        current['spec']['volumes'][1] = {'name': 'tmp', 'hostPath': {'path': '/'}}
        with self.assertRaisesRegex(RUN.ExperimentError, 'POD_SCOPE_CONFLICT'):
            RUN.assert_owned(current, expected)

    def test_generic_query_failure_cannot_count_as_opa_unavailability(self):
        FakeTrino.generic_unavailable = True
        kube = FakeKube()
        with self.assertRaisesRegex(RUN.ExperimentError, 'OPA_UNAVAILABLE_NOT_PROVEN'):
            self.execute(kube)
        value = json.loads((self.output / 'receipt.json').read_text())
        self.assertEqual(value['result'], 'failed')
        self.assertTrue(value['cleanup']['verified'])
        self.assertEqual(value['pending']['case'], 'opa_unavailable')
        self.assertTrue(value['diagnostics'])

    def test_timeout_keeps_query_pending_and_still_cleans_owned_pod(self):
        FakeTrino.failure = 'row_filter'
        kube = FakeKube()
        with self.assertRaises(TimeoutError):
            self.execute(kube)
        receipt = json.loads((self.output / 'receipt.json').read_text())
        self.assertEqual(receipt['pending']['case'], 'row_filter')
        self.assertTrue(receipt['cleanup']['verified'])
        self.assertNotIn('private failure details', RUN.canonical(receipt))
        self.assertFalse(kube.objects)

    def test_opa_transport_error_needs_specific_backend_and_connect_classes(self):
        error = {'failureInfo': {'type': 'io.trino.plugin.opa.OpaQueryException$QueryFailed',
                                'cause': {'type': 'java.net.ConnectException', 'message': 'private'}}}
        self.assertTrue(RUN.opa_transport_failure(error))
        self.assertFalse(RUN.opa_transport_failure({'failureInfo': {'type': 'java.net.ConnectException'}}))
        self.assertFalse(RUN.opa_transport_failure({'failureInfo': {'type': 'io.trino.sql.parser.ParsingException'}}))
        self.assertFalse(RUN.opa_transport_failure({'failureInfo': {'type': 'io.trino.plugin.opa.OpaQueryException$DeserializeFailed',
                                                                 'cause': {'type': 'java.net.ConnectException'}}}))
        self.assertNotIn('private', RUN.canonical(RUN.error_classes(error)))

    def test_actual_trino_483_canonical_nested_class_transport_failure(self):
        # Safe projection of the retained real failure; no message or stack data.
        classes = ['io.trino.plugin.opa.OpaQueryException.QueryFailed', 'java.util.concurrent.ExecutionException',
                   'java.io.UncheckedIOException', 'java.net.ConnectException']
        info = None
        for kind in reversed(classes):
            info = {'type': kind, 'cause': info}
        error = {'failureInfo': info}
        self.assertEqual(RUN.error_classes(error), classes)
        self.assertTrue(RUN.opa_transport_failure(error))
        info['type'] = 'io.trino.plugin.opa.OpaQueryException.DeserializeFailed'
        self.assertFalse(RUN.opa_transport_failure(error))
        info['type'] = 'io.trino.plugin.opa.OpaQueryException.QueryFailed'
        info['cause']['cause']['cause']['type'] = 'java.net.SocketTimeoutException'
        self.assertFalse(RUN.opa_transport_failure(error))

    def test_query_rejects_other_origin_changed_id_and_non_integral_results(self):
        client = RUN.Trino('http://127.0.0.1:12345')
        qid = '20260917_050000_00001_abcde'
        page = {'id': qid, 'nextUri': 'http://foreign/v1/statement/queued/' + qid + '/slug/1'}
        with patch.object(client, 'request', return_value=page), self.assertRaisesRegex(RUN.ExperimentError, 'UNSAFE_NEXT_URI'):
            client.query('admin_full', 'lab_admin', RUN.SELECT, lambda _: None)
        page = {'id': qid, 'columns': [{'name': 'nationkey', 'type': 'bigint'}, {'name': 'name', 'type': 'varchar(25)'}],
                'data': [[False, 'ALGERIA']]}
        with patch.object(client, 'request', return_value=page), self.assertRaisesRegex(RUN.ExperimentError, 'QUERY_RESULT_SCHEMA_INVALID'):
            client.query('admin_full', 'lab_admin', RUN.SELECT, lambda _: None)
        with self.assertRaisesRegex(RUN.ExperimentError, 'QUERY_NOT_ALLOWED'):
            client.query('admin_full', 'lab_admin', 'SELECT secret FROM iceberg.smoke.fixture_v1', lambda _: None)

    def test_http200_starting_state_is_polled_until_exact_version_is_ready(self):
        client = RUN.Trino('http://127.0.0.1:12345')
        with patch.object(client, 'request', side_effect=[
                {'nodeVersion': {'version': '483'}, 'starting': True},
                {'nodeVersion': {'version': '483'}, 'starting': False}]) as request, \
                patch.object(RUN.time, 'sleep'):
            client.ready()
        self.assertEqual(request.call_count, 2)
        with patch.object(client, 'request', return_value={'nodeVersion': {'version': '482'}, 'starting': False}), \
                self.assertRaisesRegex(RUN.ExperimentError, 'TRINO_VERSION_MISMATCH'):
            client.ready()

    def test_starting_never_false_remains_bounded_failure(self):
        client = RUN.Trino('http://127.0.0.1:12345')
        with patch.object(client, 'request', return_value={'nodeVersion': {'version': '483'}, 'starting': True}), \
                patch.object(RUN.time, 'monotonic', side_effect=[0, 0, 1, 2]), patch.object(RUN.time, 'sleep'), \
                self.assertRaisesRegex(RUN.ExperimentError, 'TRINO_NOT_READY'):
            client.ready(timeout=1)

    def test_audit_missing_or_foreign_decision_prevents_completion(self):
        kube = FakeKube()
        self.execute(kube)
        cases = [c for c in FakeTrino.cases if c['case'] != 'opa_unavailable']
        values = decisions(cases)
        safe = RUN.audit_decisions('\n'.join(json.dumps(x) for x in values), cases)
        self.assertEqual(len(safe), len(values))
        values[0]['input']['context']['queryId'] = 'foreign'
        with self.assertRaisesRegex(RUN.ExperimentError, 'OPA_FOREIGN_DECISION'):
            RUN.audit_decisions('\n'.join(json.dumps(x) for x in values), cases)
        with self.assertRaisesRegex(RUN.ExperimentError, 'OPA_ALLOW_AUDIT_MISSING'):
            RUN.audit_decisions('', cases)

    def test_policy_source_changes_refuse_saved_build(self):
        value = build()
        value['policy_sha256']['trino.rego'] = '0' * 64
        with self.assertRaisesRegex(RUN.ExperimentError, 'BUILD_POLICY_MISMATCH'):
            RUN.render(value, self.token, 'normal')

    def test_audit_requires_exact_query_user_and_path_operation(self):
        self.execute(FakeKube())
        cases = [c for c in FakeTrino.cases if c['case'] != 'opa_unavailable']
        values = decisions(cases)
        row = next(v for v in values if v['path'] == 'trino/rowFilters')
        row['input']['context']['identity']['user'] = 'lab_admin'
        with self.assertRaisesRegex(RUN.ExperimentError, 'OPA_FOREIGN_DECISION'):
            RUN.audit_decisions('\n'.join(json.dumps(v) for v in values), cases)
        row['input']['context']['identity']['user'] = 'lab_row'
        row['input']['action']['operation'] = 'AccessCatalog'
        with self.assertRaisesRegex(RUN.ExperimentError, 'OPA_PATH_OPERATION_MISMATCH'):
            RUN.audit_decisions('\n'.join(json.dumps(v) for v in values), cases)
        row['input']['action']['operation'] = 'GetRowFilters'
        row['input']['context']['queryId'] = None
        with self.assertRaisesRegex(RUN.ExperimentError, 'OPA_FOREIGN_DECISION'):
            RUN.audit_decisions('\n'.join(json.dumps(v) for v in values), cases)
        values = decisions(cases)
        denied = next(v for v in values if v['input']['context']['identity']['user'] == 'lab_denied')
        denied['input']['context']['queryId'] = None
        result = RUN.audit_decisions('\n'.join(json.dumps(v) for v in values), cases)
        self.assertEqual(next(v for v in result if v['user'] == 'lab_denied')['correlation'], 'user-and-execute-query-only')

    def test_interruption_keeps_pending_receipt_and_cleans_confirmed_objects(self):
        kube = FakeKube()
        original = FakeTrino.query
        def interrupted(client, name, user, sql, progress):
            value = original(client, name, user, sql, progress)
            if name == 'row_filter':
                raise KeyboardInterrupt()
            return value
        with patch.object(FakeTrino, 'query', new=interrupted), self.assertRaises(KeyboardInterrupt):
            self.execute(kube)
        receipt = json.loads((self.output / 'receipt.json').read_text())
        self.assertEqual(receipt['result'], 'interrupted')
        self.assertEqual(receipt['pending']['case'], 'row_filter')
        self.assertTrue(receipt['cleanup']['verified'])
        self.assertFalse(kube.objects)

    def test_recovery_uses_saved_plan_after_renderer_rego_and_pin_changes(self):
        state = self.output / 'state'
        output = state / 'runs' / self.token
        output.mkdir(parents=True, mode=0o700)
        value, kube = build(), FakeKube()
        builddir = state / 'builds' / value['build_token']
        builddir.mkdir(parents=True, mode=0o700)
        COMMON.write(builddir / 'build.json', value)
        objects = [o for mode in ('normal', 'unavailable') for o in RUN.render(value, self.token, mode)]
        records = []
        for obj in objects[:3]:
            current = kube.create(obj)
            records.append({'key': '/'.join(RUN.identity(obj)), 'uid': current['metadata']['uid'],
                            'content_sha256': obj['metadata']['annotations'][RUN.HASH]})
        original = {'run_token': self.token, 'build_token': value['build_token'], 'result': 'failed',
                    'images': value['images'], 'policy_sha256': value['policy_sha256'], 'objects': records}
        COMMON.write(output / 'receipt.json', original)
        COMMON.write(output / 'plan.json', {'run_token': self.token, 'build_token': value['build_token'], 'objects': objects})
        before = (output / 'receipt.json').read_bytes()
        with patch.object(RUN, 'STATE', state), patch.object(COMMON, 'STATE', state), \
                patch.object(COMMON, 'PINS', {'new': 'pins'}), \
                patch.object(RUN, 'render', side_effect=AssertionError('must not render current manifests')), \
                patch.object(RUN, 'configs', side_effect=AssertionError('must not read current configs')), \
                patch.object(RUN, 'source_hashes', side_effect=AssertionError('must not read current Rego')):
            result = RUN.recover(self.token, kube)
        self.assertTrue(result['cleanup']['verified'])
        self.assertEqual(len(kube.deleted), 3)
        self.assertEqual((output / 'receipt.json').read_bytes(), before)
        self.assertEqual((output / 'recovery.json').stat().st_mode & 0o777, 0o600)

    def test_saved_recovery_plan_cannot_expand_scope_even_with_resealed_hash(self):
        value = build()
        objects = [o for mode in ('normal', 'unavailable') for o in RUN.render(value, self.token, mode)]
        original = {'objects': objects}
        self.assertEqual(RUN.recovery_objects(original, value, self.token), objects)
        altered = copy.deepcopy(original)
        altered['objects'][2]['spec']['volumes'][1] = {'name': 'tmp', 'hostPath': {'path': '/'}}
        pod = altered['objects'][2]
        pod['metadata'].pop('annotations')
        pod['metadata']['annotations'] = {RUN.HASH: RUN.digest(pod)}
        with self.assertRaisesRegex(RUN.ExperimentError, 'RECOVERY_SCOPE_INVALID'):
            RUN.recovery_objects(altered, value, self.token)
        altered = copy.deepcopy(original)
        altered['objects'][2]['metadata']['namespace'] = 'default'
        with self.assertRaisesRegex(RUN.ExperimentError, 'RECOVERY_PLAN_INVALID'):
            RUN.recovery_objects(altered, value, self.token)

    def test_cleanup_uses_api_uid_and_resource_version_preconditions(self):
        expected = RUN.render(build(), self.token, 'normal')[-1]
        current = FakeKube().create(expected)
        kube = RUN.Kubernetes()
        with patch.object(kube, 'request') as request:
            kube.delete(expected, current)
        args, payload = request.call_args.args
        self.assertEqual(payload['preconditions'], {'uid': current['metadata']['uid'], 'resourceVersion': '1'})
        self.assertTrue(args[1].startswith('--raw=/api/v1/namespaces/shirokuma-dev/pods/opa-'))
        self.assertNotIn('--all', args)

    def test_lock_prevents_concurrent_run_recovery(self):
        with COMMON.run_lock(self.output):
            with self.assertRaisesRegex(RUN.ExperimentError, 'RUN_ACTIVE'):
                with COMMON.run_lock(self.output):
                    pass


if __name__ == '__main__':
    unittest.main()
