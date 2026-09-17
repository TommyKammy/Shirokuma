import copy
import base64
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import ssl
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / 'tools/local_experiment/trino_identity'
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location('identity_runner', HERE / 'run.py')
RUN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUN)
import client as CLIENT


def material(run_token, output):
    return {'run_token': run_token, 'issuer': RUN.ISSUER, 'audience': RUN.AUDIENCE, 'hashes': {'fixture': '1' * 64},
            'ca_path': output / 'ca.crt', 'public_key_path': output / 'jwt-public.pem',
            'public_data': {'ca.crt': 'synthetic public CA', 'jwt-public.pem': 'synthetic public key'},
            'secret_data': {'tls.crt': 'synthetic certificate', 'tls.key': 'PRIVATE TLS KEY TEST VALUE',
                            'realm.json': '{"synthetic":"PRIVATE REALM TEST VALUE"}', 'internal-secret': 'PRIVATE INTERNAL TEST VALUE'},
            'credentials': {'client_id': RUN.AUDIENCE, 'client_secret': 'PRIVATE CLIENT TEST VALUE',
                            'users': {user: 'PRIVATE USER PASSWORD ' + user for user in RUN.USERS}},
            'negative_tokens': {key: 'PRIVATE JWT ' + key for key in (*RUN.NEGATIVES, 'control')}}


def images():
    return {key: 'shirokuma.local/' + family + '@sha256:' + str(i) * 64
            for i, (key, family) in enumerate((('trino', 'trino-opa'), ('opa', 'opa'), ('keycloak', 'keycloak-identity')), 1)}


class FakeClient:
    decisions = []
    failure = None
    interrupt = False
    calls = []

    def __init__(self, ca):
        pass

    def ready(self, service, bearer=None):
        return {'service': service, 'tls_certificate_verified': True, 'observed_at': '2026-09-17T00:00:00+00:00'}

    def acquire(self, user, credentials, public_key_path):
        return 'PRIVATE LIVE TOKEN ' + user, {'user': user, 'signature_verified': True, 'source': 'keycloak-password-grant'}

    def query(self, case, bearer, progress):
        _, user, expected = next(c for c in RUN.CASES if c[0] == case)
        query_id = None if expected == 'unauthenticated' else f'20260917_060000_{len(self.calls):05}_abcde'
        user = 'lab_admin' if case == 'fixture_control' else user
        result = {'case': case, 'result': expected, 'query_id': query_id, 'http_status': 401 if expected == 'unauthenticated' else 200,
                  'response_complete': True, 'rows': RUN.expected_rows(user) if expected == 'success' else [],
                  'error_name': 'PERMISSION_DENIED' if expected == 'denied' else None,
                  'sql_sha256': RUN.digest(CLIENT.SELECT.encode())}
        progress(result)
        if self.failure == case:
            raise KeyboardInterrupt() if self.interrupt else TimeoutError('PRIVATE LIVE TOKEN lab_admin')
        self.calls.append(case)
        def add(operation, answer, path='trino/allow', target=None):
            action = {'operation': operation}
            if target:
                action['resource'] = {'user': {'user': target}}
            self.decisions.append({'type': 'openpolicyagent.org/decision_logs', 'decision_id': str(uuid.uuid4()),
                'path': path, 'input': {'context': {'identity': {'user': user, 'groups': []},
                    'queryId': None if operation in {'ExecuteQuery', 'ImpersonateUser'} else query_id}, 'action': action}, 'result': answer})
        if expected == 'success':
            add('ExecuteQuery', True)
            add('SelectFromColumns', True)
            if user in {'lab_row', 'lab_both'}:
                add('GetRowFilters', [{'expression': 'nationkey = 0'}], 'trino/rowFilters')
            if user in {'lab_mask', 'lab_both'}:
                add('GetColumnMask', {'expression': "'MASKED'"}, 'trino/columnMask')
        elif expected == 'denied':
            add('ImpersonateUser' if case.startswith('forge_') else 'ExecuteQuery', False,
                target='lab_admin' if case.startswith('forge_') else None)
        return result


class FakeKube:
    def __init__(self):
        self.objects, self.deleted, self.created = {}, [], []
        self.unknown = False

    def get(self, obj):
        return copy.deepcopy(self.objects.get(RUN.identity(obj)))

    def dry_run(self, obj):
        pass

    def create(self, obj):
        current = copy.deepcopy(obj)
        current['metadata'].update(uid=str(uuid.uuid4()), resourceVersion='1')
        if obj['kind'] == 'Pod':
            current['status'] = {'phase': 'Running', 'containerStatuses': [
                {'name': name, 'ready': True, 'restartCount': 0} for name in ('trino', 'opa', 'keycloak')]}
        self.objects[RUN.identity(obj)] = current
        self.created.append(RUN.identity(obj))
        if self.unknown and obj['kind'] == 'Pod':
            raise TimeoutError('create response lost')
        return copy.deepcopy(current)

    def delete(self, obj, current):
        if self.objects[RUN.identity(obj)]['metadata']['uid'] != current['metadata']['uid']:
            raise RUN.ExperimentError('UID_CHANGED')
        self.deleted.append(RUN.identity(obj))
        del self.objects[RUN.identity(obj)]

    def logs(self, pod, component='opa'):
        if component == 'opa':
            return '\n'.join(json.dumps(value) for value in FakeClient.decisions)
        return 'PRIVATE LIVE TOKEN lab_admin\nPRIVATE USER PASSWORD lab_admin\nPRIVATE TLS KEY TEST VALUE'


@contextmanager
def tunnel(_):
    yield


class FakeTelemetry:
    def __init__(self, output, kube, fail_at=None):
        self.output, self.kube, self.fail_at = output, kube, fail_at
        self.active_case, self.events, self.cleanup = None, [], None

    def fail(self, stage):
        if self.fail_at == stage:
            raise RuntimeError('PRIVATE TELEMETRY ERROR ' + stage)

    def __enter__(self):
        assert self.kube.created == []
        self.events.append('enter')
        self.fail('enter')
        return self

    def __exit__(self, kind, error, traceback):
        assert not self.kube.objects
        self.events.append(('exit', kind))
        self.fail('exit')
        return False

    @contextmanager
    def case(self, name):
        owner = self
        self.active_case = name
        self.events.append(('case', name))
        class Observation:
            def progress(self, result):
                owner.events.append(('progress', result['case'], result['query_id']))
                owner.fail('progress')

            def finish(self, result, decisions):
                assert owner.active_case == result['case']
                assert all(value['case'] == result['case'] for value in decisions)
                owner.events.append(('observed', result['case'], result['result']))
                owner.fail('observation')
        try:
            yield Observation()
            self.fail('delivery')
        finally:
            self.active_case = None

    def record_cleanup(self, report):
        assert not self.kube.objects
        self.cleanup = copy.deepcopy(report)
        self.events.append('cleanup')
        self.fail('cleanup')

    def finish(self, receipt):
        assert not self.kube.objects and self.cleanup['verified']
        assert receipt['result'] == 'verified' and receipt['telemetry']['result'] == 'running'
        assert json.loads((self.output / 'receipt.json').read_text())['result'] == 'verified'
        self.events.append('finish')
        self.fail('finish')


class IdentityRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.output = Path(self.tmp.name).resolve()
        self.output.chmod(0o700)
        self.token = 'a' * 32
        self.material = material(self.token, self.output)
        self.plan = {'schema_version': 1, 'run_token': self.token, 'build_token': 'b' * 32, 'images': images(),
                     'objects': RUN.render(images(), self.token, self.material)}
        FakeClient.calls, FakeClient.decisions, FakeClient.failure, FakeClient.interrupt = [], [], None, False

    def tearDown(self):
        self.tmp.cleanup()

    def execute(self, kube, **kwargs):
        with patch.object(RUN, 'token_projection', return_value={'source': 'host-signed-validation-control'}):
            return RUN.execute(self.plan, self.material, self.output, kube=kube, tunnel_factory=tunnel, client_factory=FakeClient, **kwargs)

    def test_standard_run_never_initializes_optional_telemetry(self):
        with patch.object(RUN, 'open_telemetry', side_effect=AssertionError('optional import')):
            receipt = self.execute(FakeKube())
        self.assertEqual(receipt['result'], 'passed')
        self.assertNotIn('telemetry', receipt)

    def test_opt_in_observes_query_and_audit_before_cleanup_and_final_success(self):
        kube = FakeKube()
        audit = FakeTelemetry(self.output, kube)
        original = RUN.collect_audit
        def collect(*args):
            self.assertEqual(audit.active_case, args[2])
            audit.events.append(('audit', args[2]))
            return original(*args)
        with patch.object(RUN, 'collect_audit', side_effect=collect):
            receipt = self.execute(kube, telemetry_factory=lambda output, token: audit)
        self.assertEqual(receipt['result'], 'passed')
        self.assertEqual(receipt['telemetry']['result'], 'passed')
        self.assertEqual([v[1] for v in audit.events if isinstance(v, tuple) and v[0] == 'case'], [v[0] for v in RUN.CASES])
        for name, _, expected in RUN.CASES:
            self.assertLess(audit.events.index(('audit', name)), audit.events.index(('observed', name, expected)))
        self.assertEqual(audit.events[-3:], ['cleanup', 'finish', ('exit', None)])
        self.assertEqual(len(kube.deleted), 4)

    def test_telemetry_startup_failure_prevents_runtime_creation(self):
        for constructor in (False, True):
            with self.subTest(constructor=constructor):
                kube = FakeKube()
                audit = FakeTelemetry(self.output, kube, 'enter')
                def factory(output, token):
                    if constructor:
                        raise RuntimeError('PRIVATE CONSTRUCTOR ERROR')
                    return audit
                with self.assertRaises(RuntimeError):
                    self.execute(kube, telemetry_factory=factory)
                receipt = json.loads((self.output / 'receipt.json').read_text())
                self.assertEqual(kube.created, [])
                self.assertEqual(receipt['result'], 'failed')
                self.assertEqual(receipt['failure_code'], 'TELEMETRY_UNVERIFIED')
                self.assertIsNone(receipt['cleanup'])
                self.assertNotIn('PRIVATE', RUN.canonical(receipt))

    def test_each_telemetry_failure_keeps_cleanup_and_never_passes(self):
        for stage in ('progress', 'observation', 'delivery', 'cleanup', 'finish', 'exit'):
            with self.subTest(stage=stage):
                FakeClient.calls, FakeClient.decisions = [], []
                kube = FakeKube()
                audit = FakeTelemetry(self.output, kube, stage)
                with self.assertRaises(RuntimeError):
                    self.execute(kube, telemetry_factory=lambda output, token: audit)
                receipt = json.loads((self.output / 'receipt.json').read_text())
                self.assertEqual(receipt['result'], 'failed')
                self.assertEqual(receipt['telemetry']['result'], 'failed')
                self.assertTrue(receipt['cleanup']['verified'])
                self.assertEqual(len(kube.deleted), 4)
                self.assertEqual(kube.objects, {})
                self.assertEqual(audit.events.count('cleanup'), 1)
                self.assertNotIn('PRIVATE TELEMETRY ERROR', RUN.canonical(receipt))
                if stage in ('progress', 'observation', 'delivery'):
                    self.assertLessEqual(len(FakeClient.calls), 1)

    def test_cancellation_preserved_when_cleanup_telemetry_also_fails(self):
        kube = FakeKube()
        audit = FakeTelemetry(self.output, kube, 'cleanup')
        FakeClient.failure, FakeClient.interrupt = 'row', True
        with self.assertRaises(KeyboardInterrupt):
            self.execute(kube, telemetry_factory=lambda output, token: audit)
        receipt = json.loads((self.output / 'receipt.json').read_text())
        self.assertEqual(receipt['result'], 'interrupted')
        self.assertEqual(receipt['telemetry']['result'], 'failed')
        self.assertTrue(audit.cleanup['verified'])
        self.assertEqual(audit.events[-1], ('exit', KeyboardInterrupt))
        self.assertEqual(len(kube.deleted), 4)
        self.assertTrue(any(v[:2] == ('progress', 'row') for v in audit.events if isinstance(v, tuple)))

    def test_telemetry_flag_cannot_relabel_plan_or_recovery(self):
        for command in ('plan', 'recover'):
            with self.subTest(command=command), patch.object(sys, 'argv', ['run.py', command, '--telemetry']), \
                    patch.object(RUN, 'check_context') as context, patch('builtins.print') as printed:
                self.assertEqual(RUN.main(), 1)
                self.assertEqual(json.loads(printed.call_args.args[0])['failure_code'], 'TELEMETRY_RUN_ONLY')
                context.assert_not_called()

    def test_connected_run_exports_real_local_spans_with_fake_services(self):
        try:
            import opentelemetry.sdk.trace  # Optional only in the pinned telemetry environment.
        except ImportError:
            self.skipTest('OpenTelemetry is optional for the standard-library runner')
        from tools.local_experiment.trino_identity import telemetry as telemetry
        from tools.local_experiment.agenthouse import manage as agenthouse
        events, logs = [], []
        class Backend:
            def event(self, **fields):
                return agenthouse.event(**fields)

            def insert(self, event):
                events.append(copy.deepcopy(event))

            def verify(self, event):
                assert events[-1] == event

            def push_log(self, record):
                logs.append(copy.deepcopy(record))

            def verify_log(self, record):
                assert logs[-1] == record
        self.output = self.output / self.token
        self.output.mkdir(mode=0o700)
        kube = FakeKube()
        receipt = self.execute(kube, telemetry_factory=lambda output, token: telemetry.identity_telemetry(output, token, backend=Backend()))
        journal = json.loads((self.output / 'telemetry.json').read_text())
        spans = [json.loads(line) for line in (self.output / 'identity-spans.jsonl').read_text().splitlines()]
        self.assertEqual(receipt['result'], 'passed')
        self.assertEqual(journal['result'], 'passed')
        self.assertTrue(journal['cleanup_verified'])
        self.assertTrue(journal['saved_spans_verified'])
        self.assertEqual((len(events), len(logs), len(spans)), (16, 16, 16))
        self.assertEqual({event['agent_id'] for event in events}, {'local-trino-identity'})
        self.assertEqual(sum(event['status'] == 'blocked' for event in events), 9)
        self.assertEqual(sum(event['blocked_by_policy'] for event in events), 3)
        self.assertEqual({event['trace_id'] for event in events}, {journal['trace_id']})
        self.assertTrue(all(event['prompt_tokens'] is None and event['completion_tokens'] is None
                            and event['inference_cost_estimate'] is None for event in events))
        encoded = RUN.canonical(journal) + RUN.canonical(spans) + RUN.canonical(events) + RUN.canonical(logs)
        for secret in ('PRIVATE', 'ALGERIA', CLIENT.SELECT):
            self.assertNotIn(secret, encoded)
        self.assertEqual(len(kube.deleted), 4)

    def test_secret_projection_is_scoped_and_no_credentials_in_configmap(self):
        RUN.validate_plan(self.plan)
        objects = self.plan['objects']
        self.assertEqual([obj['kind'] for obj in objects], ['NetworkPolicy', 'ConfigMap', 'Secret', 'Pod'])
        self.assertNotIn('PRIVATE', RUN.canonical(objects[1]))
        pod = objects[-1]
        self.assertEqual(len(pod['spec']['containers']), 3)
        self.assertEqual(pod['spec']['containers'][1].get('volumeMounts', []), [])
        self.assertEqual(pod['spec']['containers'][2]['resources']['limits'], {'cpu': '2', 'memory': '2Gi'})
        self.assertFalse(pod['spec']['automountServiceAccountToken'])
        self.assertFalse(pod['spec']['enableServiceLinks'])
        for field in ('persistentVolumeClaim', 'hostPath', 'NodePort', 'AWS_', 'iceberg.properties'):
            self.assertNotIn(field, RUN.canonical(objects))
        config = RUN.configs('fixed-test-secret')
        for value in ('allow-insecure-over-http=false', 'process-forwarded=false', 'required-issuer=' + RUN.ISSUER,
                      'required-audience=' + RUN.AUDIENCE, 'principal-field=preferred_username'):
            self.assertIn(value, config)

    def test_full_fifteen_cases_record_readiness_and_cleanup_without_raw_secrets(self):
        kube = FakeKube()
        receipt = self.execute(kube)
        self.assertEqual(receipt['result'], 'passed')
        self.assertEqual(len(receipt['cases']), 15)
        self.assertEqual(len(receipt['readiness']), 4)
        self.assertEqual(len(receipt['tokens']), 6)
        self.assertTrue(receipt['cleanup']['verified'])
        self.assertEqual(len(kube.deleted), 4)
        self.assertTrue(kube.deleted[0].startswith('Pod/'))
        self.assertFalse(kube.objects)
        self.assertNotIn('PRIVATE', RUN.canonical(receipt))
        self.assertEqual((self.output / 'receipt.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual([value['result'] for value in receipt['cases'] if value['case'] in {'forge_user', 'forge_original'}], ['denied', 'denied'])

    def test_collision_preflight_performs_no_create_or_delete(self):
        kube = FakeKube()
        kube.objects[RUN.identity(self.plan['objects'][2])] = {'retained': True}
        with self.assertRaisesRegex(RUN.ExperimentError, 'OBJECT_NAME_COLLISION'):
            self.execute(kube)
        self.assertFalse(kube.created)
        self.assertFalse(kube.deleted)

    def test_unknown_uid_defers_pod_and_preserves_secret_config_network(self):
        kube = FakeKube()
        kube.unknown = True
        with self.assertRaises(TimeoutError):
            self.execute(kube)
        receipt = json.loads((self.output / 'receipt.json').read_text())
        self.assertFalse(receipt['cleanup']['verified'])
        self.assertEqual(len(receipt['cleanup']['deferred']), 1)
        self.assertEqual(len(kube.objects), 4)
        self.assertFalse(kube.deleted)

    def test_create_response_uid_is_saved_before_content_validation_fails(self):
        kube = FakeKube()
        original = kube.create
        def altered_create(obj):
            result = original(obj)
            if obj['kind'] == 'Pod':
                result['spec']['containers'][0]['resources']['limits']['memory'] = '5Gi'
                kube.objects[RUN.identity(obj)] = copy.deepcopy(result)
            return result
        with patch.object(kube, 'create', side_effect=altered_create), self.assertRaisesRegex(RUN.ExperimentError, 'POD_SCOPE_CONFLICT'):
            self.execute(kube)
        receipt = json.loads((self.output / 'receipt.json').read_text())
        self.assertEqual(receipt['objects'][-1]['uid'], kube.objects[RUN.identity(self.plan['objects'][-1])]['metadata']['uid'])
        self.assertFalse(receipt['cleanup']['deferred'])
        self.assertTrue(receipt['cleanup']['conflicts'])
        self.assertFalse(kube.deleted)

    def test_timeout_and_interrupt_keep_pending_and_cleanup_secrets(self):
        for interruption in (False, True):
            FakeClient.calls, FakeClient.decisions = [], []
            FakeClient.failure, FakeClient.interrupt = 'row', interruption
            kube = FakeKube()
            with self.assertRaises(KeyboardInterrupt if interruption else TimeoutError):
                self.execute(kube)
            receipt = json.loads((self.output / 'receipt.json').read_text())
            self.assertEqual(receipt['result'], 'interrupted' if interruption else 'failed')
            self.assertEqual(receipt['pending']['case'], 'row')
            self.assertTrue(receipt['cleanup']['verified'])
            self.assertFalse(kube.objects)
            for path in self.output.glob('diagnostic-*.json'):
                self.assertNotIn('PRIVATE LIVE TOKEN', path.read_text())
                self.assertNotIn('PRIVATE USER PASSWORD', path.read_text())

    def test_resealed_hostport_or_exec_probe_is_outside_saved_plan_scope(self):
        for mutate in (lambda pod: pod['spec']['containers'][0]['ports'][0].update(hostPort=443),
                       lambda pod: pod['spec']['containers'][0].update(readinessProbe={'exec': {'command': ['sh']}}),
                       lambda pod: pod['spec'].update(hostNetwork=True)):
            altered = copy.deepcopy(self.plan)
            pod = altered['objects'][-1]
            mutate(pod)
            pod['metadata'].pop('annotations')
            pod['metadata']['annotations'] = {RUN.HASH: RUN.digest(pod)}
            with self.assertRaisesRegex(RUN.ExperimentError, 'PLAN_SCOPE_INVALID'):
                RUN.validate_plan(altered)

    def test_replaced_pod_uid_keeps_all_dependents(self):
        kube = FakeKube()
        records = []
        for obj in self.plan['objects']:
            value = kube.create(obj)
            records.append({'key': RUN.identity(obj), 'uid': value['metadata']['uid']})
        kube.objects[RUN.identity(self.plan['objects'][-1])]['metadata']['uid'] = str(uuid.uuid4())
        receipt = {'objects': records}
        RUN.cleanup(kube, receipt, self.plan['objects'], lambda: None)
        self.assertFalse(receipt['cleanup']['verified'])
        self.assertFalse(kube.deleted)
        self.assertEqual(len(kube.objects), 4)

    def test_recovery_does_not_depend_on_current_build_config_or_renderer(self):
        state = self.output / 'state'
        target = state / 'runs' / self.token
        target.mkdir(parents=True, mode=0o700)
        kube, records = FakeKube(), []
        for obj in self.plan['objects']:
            value = kube.create(obj)
            records.append({'key': RUN.identity(obj), 'uid': value['metadata']['uid'], 'content_sha256': obj['metadata']['annotations'][RUN.HASH]})
        receipt = {'run_token': self.token, 'build_token': self.plan['build_token'], 'images': self.plan['images'],
                   'plan_sha256': RUN.digest(self.plan), 'result': 'interrupted', 'objects': records}
        RUN.write(target / 'plan.json', self.plan)
        RUN.write(target / 'receipt.json', receipt)
        before = (target / 'receipt.json').read_bytes()
        with patch.object(RUN, 'STATE', state), patch.object(RUN, 'render', side_effect=AssertionError()), \
                patch.object(RUN, 'configs', side_effect=AssertionError()), patch.object(RUN.image_build, 'load_build', side_effect=AssertionError()):
            recovered = RUN.recover(self.token, kube)
        self.assertTrue(recovered['cleanup']['verified'])
        self.assertEqual(len(kube.deleted), 4)
        self.assertEqual((target / 'receipt.json').read_bytes(), before)

    def test_same_no_query_id_decision_cannot_cover_two_forgery_cases(self):
        client = FakeClient(None)
        result = client.query('forge_user', 'masked', lambda _: None)
        raw = '\n'.join(json.dumps(v) for v in FakeClient.decisions)
        values, seen = RUN.audit_case(raw, 'forge_user', result, {})
        self.assertEqual(values[0]['target_user'], 'lab_admin')
        with self.assertRaisesRegex(RUN.ExperimentError, 'OPA_AUDIT_MISSING'):
            RUN.audit_case(raw, 'forge_original', result, seen)
        FakeClient.decisions[0]['input']['context']['identity']['user'] = 'lab_admin'
        with self.assertRaisesRegex(RUN.ExperimentError, 'OPA_IDENTITY_MISMATCH'):
            RUN.audit_case(json.dumps(FakeClient.decisions[0]), 'forge_user', result, {})

    def test_authentication_rejection_must_not_reach_opa(self):
        client = FakeClient(None)
        client.query('admin_before', 'token', lambda _: None)
        rejection = {'result': 'unauthenticated', 'query_id': None}
        with self.assertRaisesRegex(RUN.ExperimentError, 'AUTH_REJECTION_REACHED_OPA'):
            RUN.audit_case(json.dumps(FakeClient.decisions[0]), 'wrong_issuer', rejection, {})

    def test_conditional_delete_includes_uid_version_and_orphan_propagation(self):
        expected = self.plan['objects'][2]
        current, kube = FakeKube().create(expected), RUN.Kubernetes()
        with patch.object(kube, 'request') as request:
            kube.delete(expected, current)
        args, payload = request.call_args.args
        self.assertIn('/secrets/identity-', args[1])
        self.assertEqual(payload['preconditions'], {'uid': current['metadata']['uid'], 'resourceVersion': '1'})
        self.assertEqual(payload['propagationPolicy'], 'Orphan')


class IdentityClientTests(unittest.TestCase):
    def client(self):
        value = CLIENT.Client.__new__(CLIENT.Client)
        return value

    def test_401_is_authentication_failure_and_forgery_uses_permission_result(self):
        client = self.client()
        response = {'status': 401, 'authenticate': 'Bearer realm="Trino", token_type="JWT"', 'body': b'no raw retention'}
        with patch.object(client, 'request', return_value=response) as request:
            value = client.query('no_token', None, lambda _: None)
        self.assertEqual(value['result'], 'unauthenticated')
        self.assertNotIn('Authorization', request.call_args.kwargs['headers'])
        response = {'status': 200, 'body': json.dumps({'id': '20260917_060000_00000_abcde',
                    'error': {'errorName': 'PERMISSION_DENIED'}}).encode()}
        with patch.object(client, 'request', return_value=response) as request:
            value = client.query('forge_original', 'token', lambda _: None)
        self.assertEqual(value['result'], 'denied')
        self.assertEqual(request.call_args.kwargs['headers']['X-Trino-User'], 'lab_mask')
        self.assertEqual(request.call_args.kwargs['headers']['X-Trino-Original-User'], 'lab_admin')
        with patch.object(client, 'request', return_value={'status': 403, 'body': b'forbidden'}), \
                self.assertRaisesRegex(RUN.ExperimentError, 'TRINO_HTTP_INVALID'):
            client.query('forge_user', 'token', lambda _: None)

    def test_next_uri_cannot_leak_bearer_to_other_origin(self):
        client = self.client()
        response = {'status': 200, 'body': json.dumps({'id': '20260917_060000_00000_abcde',
                    'nextUri': 'https://outside.invalid/v1/statement/queued/abc/slug/0'}).encode()}
        with patch.object(client, 'request', return_value=response) as request, \
                self.assertRaisesRegex(RUN.ExperimentError, 'UNSAFE_NEXT_URI'):
            client.query('admin_before', 'secret', lambda _: None)
        self.assertEqual(request.call_count, 1)

    def test_private_ca_and_hostname_verification_are_required(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        with patch.object(CLIENT.ssl, 'create_default_context', return_value=context) as create:
            client = CLIENT.Client(Path('/private/ca.crt'))
        create.assert_called_once_with(cafile='/private/ca.crt')
        self.assertTrue(client.context.check_hostname)
        self.assertEqual(client.context.verify_mode, ssl.CERT_REQUIRED)
        with self.assertRaisesRegex(RUN.ExperimentError, 'TLS_REDIRECT_REFUSED'):
            CLIENT.NoRedirect().redirect_request()

    def test_ready_keeps_starting_check_and_returns_safe_observation(self):
        client = self.client()
        with patch.object(client, 'request', side_effect=[
            {'status': 200, 'body': b'{"nodeVersion":{"version":"483"},"starting":true}'},
            {'status': 200, 'body': b'{"nodeVersion":{"version":"483"},"starting":false}'}]), patch.object(CLIENT.time, 'sleep'):
            value = client.ready('trino', 'secret')
        self.assertEqual(value['version'], '483')
        self.assertFalse(value['starting'])
        self.assertTrue(value['tls_certificate_verified'])
        self.assertIn('observed_at', value)
        self.assertNotIn('secret', RUN.canonical(value))

    def test_claim_failure_codes_are_specific_without_disclosing_claim_values(self):
        baseline = {'iss': RUN.ISSUER, 'aud': RUN.AUDIENCE, 'preferred_username': 'lab_admin',
                    'sub': 'synthetic-distinct-subject', 'iat': 100, 'exp': 400}
        encode = lambda value: base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
        for changed, code in (
                ({'iss': 'PRIVATE WRONG ISSUER'}, 'TOKEN_ISSUER_MISMATCH'),
                ({'aud': 'PRIVATE WRONG AUDIENCE'}, 'TOKEN_AUDIENCE_MISMATCH'),
                ({'preferred_username': 'PRIVATE WRONG USER'}, 'TOKEN_PRINCIPAL_MISMATCH'),
                ({'sub': None}, 'TOKEN_SUBJECT_MISSING'),
                ({'sub': 'lab_admin'}, 'TOKEN_SUBJECT_NOT_DISTINCT'),
                ({'exp': True}, 'TOKEN_TIMES_INVALID'),
                ({'iat': 120}, 'TOKEN_ISSUED_IN_FUTURE'),
                ({'exp': 101}, 'TOKEN_EXPIRY_TOO_CLOSE'),
                ({'exp': 1000}, 'TOKEN_LIFETIME_INVALID')):
            bearer = encode({'alg': 'RS256'}) + '.' + encode(dict(baseline, **changed)) + '.synthetic'
            with patch.object(CLIENT, 'verify_signature'), self.assertRaisesRegex(RUN.ExperimentError, '^' + code + '$'):
                CLIENT.token_projection(bearer, 'lab_admin', Path('/private/public.pem'), now=100)
        bearer = encode({'alg': 'RS256'}) + '.' + encode(baseline) + '.synthetic'
        with patch.object(CLIENT, 'verify_signature'):
            projected = CLIENT.token_projection(bearer, 'lab_admin', Path('/private/public.pem'), now=100)
        self.assertNotIn('synthetic-distinct-subject', RUN.canonical(projected))


if __name__ == '__main__':
    unittest.main()
