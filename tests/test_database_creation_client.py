"""Public creation CLI contract tests; all data and transports are synthetic."""
import base64
import builtins
from concurrent.futures import ThreadPoolExecutor
import copy
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import database_creation_client as cli


class Response(io.BytesIO):
    def __init__(self, value, *, raw=False):
        data = value if raw else json.dumps(value).encode()
        super().__init__(data)
        self.headers = {'Content-Length': str(len(data))}


def plan(phase='bootstrap', recovery=False, sequence=1):
    return dict(plan_id=phase + ('-recovery' if recovery else '-plan') + str(sequence),
        phase=phase, purpose='VERIFY_RECOVERY' if recovery else 'APPLY', sequence=sequence,
        operation_id=phase + '-operation', operation_revision=2,
        delivery_digest=phase + '-delivery', recovery_digest='verify-digest' if recovery else None,
        state='UNKNOWN' if recovery else 'AWAITING_APPLY', steps=[],
        public_summary=dict(code='RECOVERY_READY' if recovery else 'PLAN_READY',
            impact='VERIFY_RECOVERY_LIMITED' if recovery else 'BOOTSTRAP_MAINTENANCE' if phase == 'bootstrap' else 'FULL_RUNTIME_HANDOFF',
            fixed_steps=['publish_snapshot']))


class Server:
    def __init__(self):
        self.detail = dict(creation_id='creation-1', database_id='database-1', sql_catalog='sales',
            revision=1, lifecycle='REGISTERED', progress_code='PREPARATION_PENDING',
            initial_grants=[dict(principal_id='bob', desired_role='bi_viewer', effective_role='bi_viewer')],
            plans=[], next_actions=['plans', 'withdraw', 'reconcile'])
        self.items = None
        self.calls = []
        self.pending = False
        self.lost = False
        self.capability = dict(environment_id='lab', available=True)

    def open(self, request, timeout):
        path = request.full_url.split('localhost:8888')[-1].split('127.0.0.1:8888')[-1]
        body = json.loads(request.data) if request.data else None
        self.calls.append(dict(method=request.method, path=path, body=body,
                               headers=dict(request.header_items()), timeout=timeout))
        tail = path[len(cli.COLLECTION):].split('?')[0]
        if request.method == 'GET':
            if tail == '/capabilities':
                return Response(self.capability)
            if tail == '/principals':
                return Response(dict(principals=[dict(principal_id='alice', maximum_role='admin'),
                                                 dict(principal_id='bob', maximum_role='bi_viewer')]))
            if not tail:
                return Response(dict(creations=self.items if self.items is not None else [self.detail], cursor=None))
            return Response(self.detail)
        if self.lost:
            raise URLError('private credentials /private/backend')
        if tail.endswith('/plans') or tail.endswith('/replan'):
            if not self.pending:
                self.add_plan(body['phase'])
        elif tail.endswith('/approve') or tail.endswith('/recover-verify'):
            self.detail.update(revision=self.detail['revision'] + 1,
                progress_code='READY' if '/full/' in tail else 'FULL_REVIEW_REQUIRED',
                next_actions=['plans', 'withdraw', 'reconcile'])
            self.detail['plans'][-1]['state'] = 'PREPARED' if '/bootstrap/' in tail else 'VERIFIED'
        return Response(dict(request_id='request-1', creation_id='creation-1',
                             accepted_revision=self.detail['revision']))

    def add_plan(self, phase='bootstrap', recovery=False, sequence=1):
        self.detail['plans'].append(plan(phase, recovery, sequence))
        self.detail.update(revision=self.detail['revision'] + 1,
            progress_code='PLAN_REVIEW_REQUIRED', next_actions=['approve', 'replan', 'withdraw', 'reconcile'] + (['recover-verify'] if recovery else []))

    def posts(self):
        return [c for c in self.calls if c['method'] == 'POST']


class CreationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.auth = self.root / 'client.json'
        self.auth.write_text(json.dumps(dict(username='alice', password='test-secret', selected_role='admin')))
        self.auth.chmod(0o600)
        self.profile = SimpleNamespace(client_auth_file=str(self.auth), sql_endpoint_url='http://localhost:8888')
        self.server = Server()
        self.opener = patch.object(cli, 'CreationClient', wraps=cli.CreationClient)
        # Keep the real Client and Request building, replacing only transport.
        self.transport = patch('dataops_client.build_opener', return_value=self.server)
        self.transport.start()
        self.addCleanup(self.transport.stop)
        for name in ('load', 'configure_process', 'private_binding'):
            p = patch.object(cli.runtime_profile, name, side_effect=AssertionError('backend loader touched'))
            p.start(); self.addCleanup(p.stop)
        original_import = builtins.__import__
        def client_import(name, *args, **kw):
            if name in {'control_store', 'database_creations', 'database_creation_bridge', 'warehouse_operations'}:
                raise AssertionError('private backend import touched')
            return original_import(name, *args, **kw)
        imports = patch('builtins.__import__', side_effect=client_import)
        imports.start(); self.addCleanup(imports.stop)

    def run_cli(self, *args, **kw):
        return cli.main(list(args), profile=kw.get('profile', self.profile), state_dir=self.root / 'receipts')

    def ok(self, *args):
        value = self.run_cli(*args)
        self.assertTrue(value['ok'], value)
        return value

    def error(self, code, *args):
        value = self.run_cli(*args)
        self.assertEqual(value, dict(ok=False, error=code))

    def review(self, phase='bootstrap', recovery=False):
        return self.ok('plan', '--database', 'sales', '--phase', phase, *(['--recovery'] if recovery else []))

    def test_two_phase_name_only_flow_and_exact_http(self):
        self.ok('principals')
        self.ok('create', '--sql-catalog', 'sales', '--grant', 'bob:bi_viewer')
        first = self.server.posts()[0]
        self.assertEqual(first['path'], cli.COLLECTION)
        self.assertEqual(first['body'], dict(sql_catalog='sales', display_name='sales', description='',
                                           initial_grants=[dict(principal_id='bob', maximum_role='bi_viewer')]))
        for phase in ('bootstrap', 'full'):
            review = self.review(phase)
            self.assertIn('impact', review['data']['public_summary'])
            self.ok('approve', '--database', 'sales', '--phase', phase)
            approval = self.server.posts()[-1]
            self.assertEqual(approval['path'], cli.COLLECTION + '/creation-1/phases/' + phase + '/approve')
            self.assertEqual(set(approval['body']), {'plan_id', 'operation_id', 'expected_revision',
                'expected_operation_revision', 'delivery_digest'})
        self.assertEqual(self.ok('show', '--database', 'sales')['data']['progress_code'], 'READY')
        for call in self.server.calls:
            self.assertEqual(call['headers']['Authorization'], 'Basic ' + base64.b64encode(b'alice:test-secret').decode())
            self.assertEqual(call['headers']['X-shirokuma-role'], 'admin')
            self.assertEqual(call['timeout'], 15)
            if call['method'] == 'POST':
                self.assertEqual(call['headers']['X-shirokuma-environment'], 'lab')
            else:
                self.assertNotIn('X-shirokuma-environment', call['headers'])
        keys = [c['headers']['Idempotency-key'] for c in self.server.posts()]
        self.assertEqual(len(set(keys)), len(keys))

    def test_profile_reads_only_references_and_client(self):
        profile = self.root / 'runtime.json'
        profile.write_text(json.dumps(dict(mode='database', client_auth_file=str(self.auth),
            sql_endpoint_url='http://localhost:8888', auth_file='/never/backend', trino_ca_file='/never/ca')))
        profile.chmod(0o600)
        real = cli.runtime_profile.private_json
        reads = []
        def read(path):
            reads.append(str(path))
            return real(path)
        with patch.dict(os.environ, SHIROKUMA_RUNTIME_PROFILE=str(profile)), patch.object(cli.runtime_profile, 'private_json', side_effect=read):
            self.assertTrue(cli.main(['principals'], state_dir=self.root / 'receipts')['ok'])
        self.assertEqual(reads, [str(profile), str(self.auth)])

    def test_help_and_invalid_flags_never_load_profile_or_http(self):
        with patch.object(cli, 'profile_references', side_effect=AssertionError('profile read')):
            for argv in (['plan', '--help'], ['create'], ['show', '--database', 'sales', '--release']):
                with self.assertRaises(SystemExit), patch('sys.stdout', new=io.StringIO()), patch('sys.stderr', new=io.StringIO()):
                    cli.main(argv)
            self.error('INVALID_NAME', 'show', '--database', '../bad')
            self.error('INVALID_NAME', 'create', '--sql-catalog', '')
            self.error('INVALID_MANAGEMENT_ORIGIN', 'principals', '--base-url', 'http://example.com')
            self.error('INVALID_INITIAL_GRANTS', 'create', '--sql-catalog', 'sales', '--grant', 'bob:root')
        self.assertEqual(self.server.calls, [])

    def test_no_default_grants_and_no_role_escalation(self):
        self.error('INVALID_INITIAL_GRANTS', 'create', '--sql-catalog', 'sales', '--grant', 'bob:admin')
        self.error('INVALID_INITIAL_GRANTS', 'create', '--sql-catalog', 'sales', '--grant', 'missing:bi_viewer')
        self.assertEqual(self.server.posts(), [])
        self.ok('create', '--sql-catalog', 'sales')
        self.assertEqual(self.server.posts()[0]['body']['initial_grants'], [])

    def test_ambiguous_exact_names(self):
        self.server.items = [self.server.detail, dict(self.server.detail, creation_id='other')]
        self.error('DATABASE_NAME_AMBIGUOUS', 'show', '--database', 'sales')
        self.server.items = [dict(self.server.detail, sql_catalog='sales_extra')]
        self.error('DATABASE_NOT_FOUND', 'show', '--database', 'sales')
        self.assertEqual(self.server.posts(), [])

    def test_progress_is_get_only_and_filters_private_fields(self):
        self.server.detail['private_ref'] = '/private/backend/password'
        self.server.detail['plans'] = [dict(plan(), private_capability='secret')]
        for action in ('list', 'show', 'progress'):
            args = [action] + ([] if action == 'list' else ['--database', 'sales'])
            result = self.ok(*args)
            self.assertNotIn('private', json.dumps(result))
            self.assertNotIn('secret', json.dumps(result))
        self.assertEqual(self.server.posts(), [])

    def test_executor_blocked_reason_and_access_survive_public_show_and_list(self):
        self.server.detail.update(revision=99,progress_code='BLOCKED',blocker='PRINCIPAL_STOPPED',
            access_source='platform_admin',next_actions=['withdraw'],private_ref='/private/backend/password')
        for action in ('show','list','progress'):
            with self.subTest(action=action):
                result=self.ok(action,*([] if action=='list' else ['--database','sales']))
                detail=result['data']['creations'][0] if action=='list' else result['data']
                self.assertEqual(detail['progress_code'],'BLOCKED')
                self.assertEqual(detail['blocker'],'PRINCIPAL_STOPPED')
                self.assertEqual(detail['access_source'],'platform_admin')
                self.assertEqual(detail['next_actions'],['withdraw'])
                self.assertEqual(detail['revision'],99)
                self.assertNotIn('/private',json.dumps(result))
                self.assertIn('PRINCIPAL_STOPPED',result['message'])
        self.assertEqual(self.server.posts(),[])

    def test_optional_blocker_and_fixed_access_source_projection(self):
        absent=self.ok('show','--database','sales')['data']
        self.assertNotIn('blocker',absent); self.assertNotIn('access_source',absent)
        for blocker,access in [(None,'effective_grant'),('PRINCIPAL_STOPPED','platform_admin'),
                ('OPERATOR_UNAVAILABLE','none'),('FOUNDATION_NOT_READY','platform_admin')]:
            with self.subTest(blocker=blocker,access=access):
                self.server.detail.update(blocker=blocker,access_source=access)
                result=self.ok('show','--database','sales')['data']
                self.assertEqual(result['blocker'],blocker)
                self.assertEqual(result['access_source'],access)
        self.assertEqual(self.server.posts(),[])

    def test_invalid_blocker_or_access_source_never_leaks(self):
        for field,invalid in [('blocker','/private/password'),('blocker',{'password':'secret'}),
                ('blocker',False),('blocker',42),('access_source','secret-token'),
                ('access_source',{'password':'secret'}),('access_source',None),('access_source',True)]:
            with self.subTest(field=field,invalid=invalid):
                self.server.detail.pop('blocker',None); self.server.detail.pop('access_source',None)
                self.server.detail[field]=invalid
                self.error('PUBLIC_PROJECTION_REQUIRED','show','--database','sales')
        self.assertEqual(self.server.posts(),[])

    def test_prior_review_required(self):
        self.server.add_plan()
        self.error('PRIOR_REVIEW_REQUIRED', 'approve', '--database', 'sales')

    def test_entire_review_tuple_must_match(self):
        mutations = [lambda d: d.update(revision=99),
            lambda d: d['plans'][0].update(operation_revision=99),
            lambda d: d['plans'][0].update(operation_id='other'),
            lambda d: d['plans'][0].update(delivery_digest='changed'),
            lambda d: d['plans'][0].update(plan_id='other'),
            lambda d: d['plans'][0].update(sequence=2),
            lambda d: d['plans'][0]['public_summary'].update(fixed_steps=['reconcile_trino']),
            lambda d: d.update(initial_grants=[])]
        self.server.add_plan()
        original = copy.deepcopy(self.server.detail)
        for mutation in mutations:
            self.server.detail = copy.deepcopy(original)
            self.review()
            mutation(self.server.detail)
            self.error('STALE_REVIEW', 'approve', '--database', 'sales')
        self.assertEqual(self.server.posts(), [])

    def test_origin_actor_role_environment_cannot_reuse_review(self):
        self.server.add_plan(); self.review()
        self.error('PRIOR_REVIEW_REQUIRED', 'approve', '--database', 'sales', '--base-url', 'http://127.0.0.1:8888')
        for field, value in [('username', 'other'), ('selected_role', 'bi_viewer')]:
            auth = dict(username='alice', password='test-secret', selected_role='admin'); auth[field] = value
            self.auth.write_text(json.dumps(auth))
            self.error('PRIOR_REVIEW_REQUIRED', 'approve', '--database', 'sales')
        self.auth.write_text(json.dumps(dict(username='alice', password='test-secret', selected_role='admin')))
        self.server.capability['environment_id'] = 'other-lab'
        self.error('PRIOR_REVIEW_REQUIRED', 'approve', '--database', 'sales')
        self.assertEqual(self.server.posts(), [])

    def test_phase_purpose_and_sequence_separation(self):
        self.server.add_plan('bootstrap', sequence=999)
        self.server.add_plan('full', sequence=1)
        self.review('full')
        self.error('PRIOR_REVIEW_REQUIRED', 'approve', '--database', 'sales')
        self.server.add_plan('full', recovery=True, sequence=1)
        self.review('full', recovery=True)
        self.ok('approve', '--database', 'sales', '--phase', 'full', '--recovery')
        last = self.server.posts()[-1]
        self.assertTrue(last['path'].endswith('/full/recover-verify'))
        self.assertEqual(last['body']['recovery_digest'], 'verify-digest')
        self.assertEqual(last['body']['plan_id'], 'full-recovery1')

    def test_recovery_digest_change_and_unknown_never_generic_retry(self):
        self.server.add_plan('full', recovery=True)
        self.review('full', recovery=True)
        self.server.detail['plans'][0]['recovery_digest'] = 'new'
        self.error('STALE_REVIEW', 'approve', '--database', 'sales', '--phase', 'full', '--recovery')
        self.server.detail['plans'] = [plan('full')]
        self.server.detail['plans'][0]['state'] = 'UNKNOWN'
        self.error('PLAN_NOT_REVIEWABLE', 'plan', '--database', 'sales', '--phase', 'full')
        self.assertEqual(self.server.posts(), [])

    def test_next_actions_stop_and_missing_projection(self):
        self.server.add_plan(); self.review()
        for actions, progress, expected in [(['retry', 'release', 'resume'], 'PLAN_REVIEW_REQUIRED', 'ACTION_NOT_AVAILABLE'),
                (['approve'], 'STOPPED', 'ACTION_NOT_AVAILABLE'), (['approve'], None, 'PUBLIC_PROJECTION_REQUIRED')]:
            self.server.detail.update(next_actions=actions, progress_code=progress)
            self.error(expected, 'approve', '--database', 'sales')
        self.assertEqual(self.server.posts(), [])

    def test_pending_plan_repeat_get_only_then_pin_review(self):
        self.server.pending = True
        result = self.review()
        self.assertIn('pending', result['message'])
        self.review()
        self.assertEqual(len(self.server.posts()), 1)
        self.server.add_plan()
        self.review()
        self.assertEqual(len(self.server.posts()), 1)
        self.ok('approve', '--database', 'sales')

    def test_replan_waits_for_new_same_phase_sequence(self):
        self.server.add_plan(); self.server.pending = True
        self.server.detail['progress_code'] = 'REPLAN_REQUIRED'
        result = self.ok('replan', '--database', 'sales')
        self.assertEqual(json.loads(result['message']), result['data'])
        self.assertEqual(result['data']['progress_code'], 'REPLAN_REQUIRED')
        self.assertEqual(result['data']['plans'][0]['sequence'], 1)
        self.ok('replan', '--database', 'sales')
        self.assertEqual(len(self.server.posts()), 1)
        self.server.add_plan(sequence=2)
        self.ok('replan', '--database', 'sales')
        self.ok('approve', '--database', 'sales')
        self.assertEqual(self.server.posts()[-1]['body']['plan_id'], 'bootstrap-plan2')

    def test_saved_pending_plan_observes_current_nonpending_detail_without_post(self):
        observations = [(progress, blocker)
            for progress in sorted(cli.PROGRESS - {'PREPARATION_PENDING'})
            for blocker in (None, 'PRINCIPAL_STOPPED')]
        observations += [('PREPARATION_PENDING', blocker)
            for blocker in ('PRINCIPAL_STOPPED', 'OPERATOR_UNAVAILABLE', 'FOUNDATION_NOT_READY')]
        for action, uncertain in [('plan', False), ('plan', True), ('replan', False), ('replan', True)]:
            for progress, blocker in observations:
                with self.subTest(action=action, uncertain=uncertain, progress=progress, blocker=blocker), tempfile.TemporaryDirectory() as temp:
                    state_dir = Path(temp).resolve()
                    self.server = Server()
                    if action == 'replan':
                        self.server.add_plan()
                        self.server.detail['progress_code'] = 'REPLAN_REQUIRED'
                    # Retain real request/auth construction; replace transport only.
                    with patch('dataops_client.build_opener', return_value=self.server):
                        args = [action, '--database', 'sales', '--phase', 'bootstrap']
                        self.server.pending = True
                        self.server.lost = uncertain
                        first = cli.main(args, profile=self.profile, state_dir=state_dir)
                        if uncertain:
                            self.assertEqual(first, dict(ok=False, error='RESPONSE_UNCERTAIN'))
                        else:
                            self.assertTrue(first['ok'], first)
                            if action == 'plan':
                                self.assertIn('pending', first['message'])
                            else:
                                self.assertEqual(json.loads(first['message']), first['data'])
                        receipts = {p: p.read_bytes() for p in state_dir.rglob('*.json')}
                        self.assertEqual(len(receipts), 1)
                        self.server.lost = False
                        self.server.detail.update(revision=99, progress_code=progress,
                            blocker=blocker,
                            access_source='platform_admin', next_actions=['withdraw', 'reconcile'],
                            private_ref='/private/backend/password')
                        before = len(self.server.calls)
                        result = cli.main(args, profile=self.profile, state_dir=state_dir)
                        self.assertTrue(result['ok'], result)
                        self.assertEqual(result['data'], cli.public(self.server.detail))
                        self.assertEqual(json.loads(result['message']), result['data'])
                        self.assertNotIn('Preparation pending', result['message'])
                        self.assertNotIn('repeat plan', result['message'])
                        self.assertNotIn('/private', json.dumps(result))
                        self.assertEqual({c['method'] for c in self.server.calls[before:]}, {'GET'})
                        self.assertEqual(len(self.server.posts()), 1)
                        self.assertEqual({p: p.read_bytes() for p in state_dir.rglob('*.json')}, receipts)

    def test_saved_plan_and_replan_keep_genuine_pending_observation_clear(self):
        for action, uncertain in [('plan', False), ('plan', True), ('replan', False), ('replan', True)]:
            with self.subTest(action=action, uncertain=uncertain), tempfile.TemporaryDirectory() as temp:
                state_dir = Path(temp).resolve()
                self.server = Server()
                if action == 'replan':
                    self.server.add_plan()
                self.server.detail.update(progress_code='PREPARATION_PENDING', blocker=None)
                self.server.pending = True
                self.server.lost = uncertain
                args = [action, '--database', 'sales', '--phase', 'bootstrap']
                with patch('dataops_client.build_opener', return_value=self.server):
                    first = cli.main(args, profile=self.profile, state_dir=state_dir)
                    if uncertain:
                        self.assertEqual(first, dict(ok=False, error='RESPONSE_UNCERTAIN'))
                    else:
                        self.assertTrue(first['ok'], first)
                    receipts = {p: p.read_bytes() for p in state_dir.rglob('*.json')}
                    self.assertEqual(len(receipts), 1)
                    self.server.lost = False
                    before = len(self.server.calls)
                    result = cli.main(args, profile=self.profile, state_dir=state_dir)
                    self.assertTrue(result['ok'], result)
                    self.assertEqual(result['message'], 'Preparation pending; repeat plan to inspect with GET only.')
                    self.assertEqual(result['data'], cli.public(self.server.detail))
                    self.assertEqual({c['method'] for c in self.server.calls[before:]}, {'GET'})
                    self.assertEqual(len(self.server.posts()), 1)
                    self.assertEqual({p: p.read_bytes() for p in state_dir.rglob('*.json')}, receipts)

    def test_uncertain_create_persists_key_body_and_no_redispatch(self):
        self.server.lost = True
        self.error('RESPONSE_UNCERTAIN', 'create', '--sql-catalog', 'sales')
        files = list((self.root / 'receipts').glob('*/create.json'))
        self.assertEqual(len(files), 1)
        frozen = files[0].read_bytes()
        self.assertNotIn(b'test-secret', frozen)
        self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
        self.error('RESPONSE_UNCERTAIN', 'create', '--sql-catalog', 'sales')
        self.error('FROZEN_INTENT_CONFLICT', 'create', '--sql-catalog', 'sales', '--description', 'changed')
        self.assertEqual(files[0].read_bytes(), frozen)
        receipt = json.loads(frozen)
        self.assertEqual(receipt['key'], self.server.posts()[0]['headers']['Idempotency-key'])
        self.assertEqual(receipt['body'], self.server.posts()[0]['body'])
        self.assertEqual(len(self.server.posts()), 1)

    def test_uncertain_approval_cannot_issue_new_key(self):
        self.server.add_plan(); self.review(); self.server.lost = True
        self.error('RESPONSE_UNCERTAIN', 'approve', '--database', 'sales')
        self.error('RESPONSE_UNCERTAIN', 'approve', '--database', 'sales')
        self.assertEqual(len(self.server.posts()), 1)
        self.server.detail['revision'] += 1
        self.error('STALE_REVIEW', 'approve', '--database', 'sales')
        self.assertEqual(len(self.server.posts()), 1)

    def test_accepted_create_repeat_reuses_receipt_without_dispatch(self):
        first = self.ok('create', '--sql-catalog', 'sales')
        second = self.ok('create', '--sql-catalog', 'sales')
        self.assertEqual(first, second)
        self.assertEqual(len(self.server.posts()), 1)

    def test_malformed_ack_is_persistent_uncertainty(self):
        original = self.server.open
        def malformed(request, timeout):
            if request.method == 'POST':
                self.server.calls.append(dict(method='POST'))
                return Response(dict(raw_secret='/private/password'))
            return original(request, timeout)
        with patch.object(self.server, 'open', side_effect=malformed):
            self.error('RESPONSE_UNCERTAIN', 'create', '--sql-catalog', 'sales')
        self.error('RESPONSE_UNCERTAIN', 'create', '--sql-catalog', 'sales')
        self.assertEqual(len(self.server.posts()), 1)

    def test_recovery_requires_matching_purpose_and_public_proof_gate(self):
        self.server.add_plan('full')
        self.error('ACTION_NOT_AVAILABLE', 'plan', '--database', 'sales', '--phase', 'full', '--recovery')
        self.server.add_plan('full', recovery=True)
        self.review('full', recovery=True)
        self.server.detail['next_actions'] = ['approve']
        self.error('ACTION_NOT_AVAILABLE', 'approve', '--database', 'sales', '--phase', 'full', '--recovery')
        self.assertEqual(self.server.posts(), [])

    def test_unknown_steps_never_enable_ordinary_approval(self):
        self.server.add_plan()
        self.server.detail['plans'][0]['steps'] = [dict(step='verify', state='UNKNOWN')]
        self.error('PLAN_NOT_REVIEWABLE', 'plan', '--database', 'sales')
        self.assertEqual(self.server.posts(), [])

    def test_uncertain_plan_observation_does_not_redispatch(self):
        self.server.lost = True
        self.error('RESPONSE_UNCERTAIN', 'plan', '--database', 'sales')
        self.review()
        self.assertEqual(len(self.server.posts()), 1)

    def test_explicit_reconcile_and_withdraw_only(self):
        self.ok('reconcile', '--database', 'sales')
        self.ok('withdraw', '--database', 'sales')
        self.assertEqual([p['path'].split('/')[-1] for p in self.server.posts()], ['reconcile', 'withdraw'])
        for post in self.server.posts():
            self.assertEqual(post['body'], dict(expected_revision=1))

    def test_concurrency_one_admission_one_key(self):
        entered, release = threading.Event(), threading.Event()
        receipts = cli.Receipts(self.root / 'race', dict(target='sales', environment='lab'))
        class Slow:
            calls = []
            def request(self, method, path, **kw):
                self.calls.append(kw); entered.set(); release.wait(5)
                return dict(request_id='one', creation_id='one', accepted_revision=1)
        client = Slow()
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(receipts.mutate, client, 'create', cli.COLLECTION, dict(sql_catalog='sales'))
            self.assertTrue(entered.wait(5))
            try:
                with self.assertRaises(cli.Failure) as caught:
                    receipts.mutate(client, 'create', cli.COLLECTION, dict(sql_catalog='sales'))
                self.assertEqual(caught.exception.code, 'CLIENT_ACTION_BUSY')
            finally:
                release.set()
            first.result()
        self.assertEqual(len(client.calls), 1)

    def test_http_errors_and_malformed_response_are_sanitized(self):
        for status in (401, 403, 409, 503):
            for field in ('error', 'code'):
                error = HTTPError('http://localhost/private', status, 'secret', {},
                    io.BytesIO(json.dumps({field: 'ADMIN_REQUIRED', 'details': 'test-secret /private'}).encode()))
                with patch.object(self.server, 'open', side_effect=error):
                    self.error('ADMIN_REQUIRED', 'principals')
        for raw in (b'private password', b'[]', b'{"error":"/private/password"}'):
            error = HTTPError('http://localhost', 503, 'private', {}, io.BytesIO(raw))
            with patch.object(self.server, 'open', side_effect=error):
                self.error('HTTP_503', 'principals')
        with patch.object(self.server, 'open', return_value=Response(b'raw /private test-secret', raw=True)):
            self.error('HTTP_RESPONSE_FAILED', 'principals')

    def test_loopback_only_and_client_selected_role(self):
        self.assertFalse(self.run_cli('principals', '--base-url', 'http://example.com')['ok'])
        self.assertEqual(self.server.calls, [])
        self.auth.write_text(json.dumps(dict(username='alice', password='test-secret', selected_role='bi_viewer')))
        self.ok('principals')
        self.assertEqual(self.server.calls[-1]['headers']['X-shirokuma-role'], 'bi_viewer')

    def test_transport_no_proxy_no_redirect(self):
        with patch('dataops_client.build_opener', wraps=__import__('urllib.request', fromlist=['build_opener']).build_opener):
            client = cli.CreationClient(self.profile)
        handlers = client.opener.handlers
        self.assertTrue(any(isinstance(h, cli.__dict__['Client'].__init__.__globals__['NoRedirect']) for h in handlers))
        # urllib omits ProxyHandler entirely when its proxy mapping is empty.
        self.assertFalse(any(hasattr(h, 'proxies') and h.proxies for h in handlers))

    def receipt_file(self, label):
        paths = list((self.root / 'receipts').glob('*/' + label + '.json'))
        self.assertEqual(len(paths), 1)
        return paths[0]

    def lost_after_admission(self):
        original = self.server.open
        saved, deliveries, dispatches = {}, [], []
        def transport(request, timeout):
            if request.method != 'POST':
                return original(request, timeout)
            key = request.get_header('Idempotency-key')
            deliveries.append((request.full_url, request.data, key, request.get_header('X-shirokuma-environment')))
            if key in saved:
                return Response(saved[key], raw=True)
            dispatches.append(key)
            with original(request, timeout) as response:
                saved[key] = response.read()
            raise URLError('lost /private/password ACK')
        return patch.object(self.server, 'open', side_effect=transport), deliveries, dispatches

    def test_retry_lost_approval_ack_after_executor_stop_exact_tuple(self):
        self.server.add_plan(); self.review()
        transport, deliveries, dispatches = self.lost_after_admission()
        with transport:
            self.error('RESPONSE_UNCERTAIN', 'approve', '--database', 'sales')
            frozen = json.loads(self.receipt_file('approve-bootstrap-apply').read_text())
            self.server.detail.update(revision=99, progress_code='STOPPED', next_actions=[])
            self.server.capability.update(available=False, blocker='PRINCIPAL_STOPPED')
            result = self.ok('retry', '--database', 'sales', '--action', 'approve', '--phase', 'bootstrap')
        self.assertEqual(deliveries[0], deliveries[1])
        self.assertEqual(deliveries[1][-1], 'lab')
        self.assertEqual(len(dispatches), 1)
        self.assertEqual(result['data']['acknowledgment']['accepted_revision'], 3)
        self.assertEqual(result['data']['current']['revision'], 99)
        self.assertEqual(result['data']['current']['progress_code'], 'STOPPED')
        self.assertIn('Historical acknowledgment', result['message'])
        self.assertIn('STOPPED', result['message'])
        recovered = json.loads(self.receipt_file('approve-bootstrap-apply').read_text())
        self.assertEqual(recovered['body'], frozen['body'])
        self.assertEqual(recovered['key'], frozen['key'])
        self.assertEqual(recovered['status'], 'complete')
        self.assertTrue(self.server.calls[-1]['method'] == 'GET')

    def test_retry_historical_ack_keeps_current_executor_blocked_reason_separate(self):
        self.server.add_plan(); self.review()
        transport,deliveries,dispatches=self.lost_after_admission()
        with transport:
            self.error('RESPONSE_UNCERTAIN','approve','--database','sales')
            frozen=json.loads(self.receipt_file('approve-bootstrap-apply').read_text())
            self.server.capability.update(available=False,blocker='PRINCIPAL_STOPPED')
            first_ack=None
            for revision,actions in [(99,['withdraw']),(100,['withdraw','reconcile'])]:
                self.server.detail.update(revision=revision,progress_code='BLOCKED',blocker='PRINCIPAL_STOPPED',
                    access_source='platform_admin',next_actions=actions)
                result=self.ok('retry','--database','sales','--action','approve','--phase','bootstrap')
                ack,current=result['data']['acknowledgment'],result['data']['current']
                if first_ack is None:
                    first_ack=ack
                self.assertEqual(ack,first_ack)
                self.assertEqual(ack['accepted_revision'],3)
                self.assertEqual(result['data']['current_status'],'AVAILABLE')
                self.assertEqual((current['revision'],current['progress_code'],current['blocker']),
                    (revision,'BLOCKED','PRINCIPAL_STOPPED'))
                self.assertEqual(current['next_actions'],actions)
                self.assertEqual(current['access_source'],'platform_admin')
                self.assertIn('BLOCKED',result['message']); self.assertIn('PRINCIPAL_STOPPED',result['message'])
                recovered=json.loads(self.receipt_file('approve-bootstrap-apply').read_text())
                self.assertEqual((recovered['key'],recovered['body'],recovered['status']),
                    (frozen['key'],frozen['body'],'complete'))
                self.assertEqual(recovered['ack'],first_ack)
                self.assertEqual(self.server.calls[-1]['method'],'GET')
        self.assertEqual(len(deliveries),3)
        self.assertTrue(all(delivery==deliveries[0] for delivery in deliveries))
        self.assertEqual(deliveries[0][-1],'lab')
        self.assertEqual(len(dispatches),1)

    def test_retry_requester_denial_prevents_replay(self):
        self.server.lost = True
        self.error('RESPONSE_UNCERTAIN', 'create', '--sql-catalog', 'sales')
        frozen = self.receipt_file('create').read_bytes()
        for status in (401, 403):
            with patch.object(self.server, 'open', side_effect=HTTPError('http://localhost', status, 'private', {},
                    io.BytesIO(b'{"error":"ADMIN_REQUIRED"}'))):
                self.error('ADMIN_REQUIRED', 'retry', '--database', 'sales', '--action', 'create')
        self.assertEqual(len(self.server.posts()), 1)
        self.assertEqual(self.receipt_file('create').read_bytes(), frozen)

    def test_retry_scope_changes_have_no_delivery(self):
        self.server.lost = True
        self.error('RESPONSE_UNCERTAIN', 'create', '--sql-catalog', 'sales')
        for extra in (['--base-url', 'http://127.0.0.1:8888'], ['--database', 'other']):
            self.error('NO_FROZEN_INTENT', 'retry', '--database', 'sales', '--action', 'create', *extra)
        self.server.capability['environment_id'] = 'different'
        self.error('NO_FROZEN_INTENT', 'retry', '--database', 'sales', '--action', 'create')
        self.server.capability['environment_id'] = 'lab'
        for field, value in (('username', 'bob'), ('selected_role', 'bi_viewer')):
            auth = dict(username='alice', password='test-secret', selected_role='admin'); auth[field] = value
            self.auth.write_text(json.dumps(auth))
            self.error('NO_FROZEN_INTENT', 'retry', '--database', 'sales', '--action', 'create')
        self.assertEqual(len(self.server.posts()), 1)

    def test_retry_rejects_absent_corrupt_and_wrong_intent(self):
        self.error('NO_FROZEN_INTENT', 'retry', '--database', 'sales', '--action', 'create')
        self.server.lost = True
        self.error('RESPONSE_UNCERTAIN', 'create', '--sql-catalog', 'sales')
        path = self.receipt_file('create')
        original = json.loads(path.read_text())
        corruptions = [lambda r: r['scope'].update(target='other'), lambda r: r.update(key='not-uuid'),
            lambda r: r.update(path=cli.COLLECTION + '/other/withdraw'),
            lambda r: r['body'].update(sql_catalog='other'), lambda r: r['body'].update(private_capability='bad'),
            lambda r: r.update(status='complete'), lambda r: r.update(status='unknown')]
        for mutate in corruptions:
            receipt = copy.deepcopy(original); mutate(receipt); path.write_text(json.dumps(receipt))
            self.error('INVALID_CLIENT_RECEIPT', 'retry', '--database', 'sales', '--action', 'create')
        for raw in ('{bad /private/password', 'null', '{"status":"uncertain","status":"complete"}'):
            path.write_text(raw)
            self.error('INVALID_CLIENT_RECEIPT', 'retry', '--database', 'sales', '--action', 'create')
        path.write_text(json.dumps(original))
        self.error('NO_FROZEN_INTENT', 'retry', '--database', 'sales', '--action', 'approve', '--phase', 'bootstrap')
        self.assertEqual(len(self.server.posts()), 1)

    def test_retry_binds_create_ack_to_exact_current_name_root(self):
        transport, deliveries, dispatches = self.lost_after_admission()
        with transport:
            self.error('RESPONSE_UNCERTAIN', 'create', '--sql-catalog', 'sales')
            self.server.detail.update(progress_code='READY', next_actions=[])
            result = self.ok('retry', '--database', 'sales', '--action', 'create')
            self.assertEqual(result['data']['current']['progress_code'], 'READY')
            self.server.items = [dict(self.server.detail, creation_id='different')]
            self.server.detail['creation_id'] = 'different'
            result = self.ok('retry', '--database', 'sales', '--action', 'create')
            self.assertEqual(result['data']['current_status'], 'UNAVAILABLE')
            self.assertIsNone(result['data']['current'])
            self.assertEqual(result['data']['acknowledgment']['creation_id'], 'creation-1')
        self.assertEqual(len(dispatches), 1)
        self.assertEqual(deliveries, [deliveries[0]] * 3)

    def test_retry_ack_recovered_when_current_get_fails(self):
        transport, deliveries, _ = self.lost_after_admission()
        with transport:
            self.error('RESPONSE_UNCERTAIN', 'create', '--sql-catalog', 'sales')
            original = self.server.open
            def unavailable(request, timeout):
                if request.method == 'GET' and not request.full_url.endswith('/capabilities'):
                    raise URLError('/private/password')
                return original(request, timeout)
            with patch.object(self.server, 'open', side_effect=unavailable):
                result = self.ok('retry', '--database', 'sales', '--action', 'create')
        self.assertEqual(result['data']['current_status'], 'UNAVAILABLE')
        self.assertIn('current status unavailable', result['message'])
        self.assertNotIn('/private', json.dumps(result))
        self.assertEqual(len(deliveries), 2)

    def test_invalid_ack_bindings_stay_uncertain(self):
        self.server.add_plan(); self.review()
        valid = dict(request_id='ack', creation_id='creation-1', accepted_revision=3, phase='bootstrap', action='APPROVE')
        for change in (dict(creation_id='other'), dict(request_id=''), dict(creation_id=''),
                       dict(accepted_revision=True), dict(accepted_revision=0), dict(accepted_revision=None),
                       dict(phase='full'), dict(action='WITHDRAW')):
            ack = dict(valid, **change)
            original = self.server.open
            def invalid(request, timeout):
                return Response(ack) if request.method == 'POST' else original(request, timeout)
            with patch.object(self.server, 'open', side_effect=invalid):
                command = ['approve', '--database', 'sales'] if not list((self.root / 'receipts').glob('*/approve-bootstrap-apply.json')) else ['retry', '--database', 'sales', '--action', 'approve', '--phase', 'bootstrap']
                self.error('RESPONSE_UNCERTAIN', *command)
            receipt = json.loads(self.receipt_file('approve-bootstrap-apply').read_text())
            self.assertEqual(receipt['status'], 'uncertain')
            self.assertNotIn('ack', receipt)

    def test_known_approval_rejection_requires_explicit_new_review(self):
        self.server.add_plan(); self.review()
        original = self.server.open
        def rejected(request, timeout):
            if request.method == 'POST':
                self.server.calls.append(dict(method='POST', body=json.loads(request.data),
                    headers=dict(request.header_items())))
                raise HTTPError(request.full_url, 409, 'private', {}, io.BytesIO(b'{"error":"STALE_PLAN"}'))
            return original(request, timeout)
        with patch.object(self.server, 'open', side_effect=rejected):
            self.error('STALE_PLAN', 'approve', '--database', 'sales')
        path = self.receipt_file('approve-bootstrap-apply')
        rejected_receipt = json.loads(path.read_text())
        self.assertEqual(rejected_receipt['status'], 'rejected')
        self.error('INTENT_REJECTED', 'retry', '--database', 'sales', '--action', 'approve', '--phase', 'bootstrap')
        self.error('PRIOR_REVIEW_REQUIRED', 'approve', '--database', 'sales')
        self.assertEqual(len(self.server.posts()), 1)
        self.server.detail['revision'] += 1
        self.review()
        self.assertEqual(len(self.server.posts()), 1)
        self.ok('approve', '--database', 'sales')
        fresh = json.loads(path.read_text())
        self.assertNotEqual(fresh['key'], rejected_receipt['key'])
        self.assertEqual(fresh['body']['expected_revision'], 3)
        self.assertEqual(len(list(path.parent.glob('approve-bootstrap-apply-rejected-*.json'))), 1)

    def test_canonical_create_rejection_allows_only_manual_correction(self):
        original = self.server.open
        def rejected(request, timeout):
            if request.method == 'POST':
                self.server.calls.append(dict(method='POST', body=json.loads(request.data)))
                raise HTTPError(request.full_url, 400, 'private', {}, io.BytesIO(b'{"code":"INVALID_INITIAL_GRANTS"}'))
            return original(request, timeout)
        with patch.object(self.server, 'open', side_effect=rejected):
            self.error('INVALID_INITIAL_GRANTS', 'create', '--sql-catalog', 'sales', '--grant', 'bob:bi_viewer')
        rejected_receipt = json.loads(self.receipt_file('create').read_text())
        self.assertEqual(rejected_receipt['status'], 'rejected')
        self.assertEqual(len(self.server.posts()), 1)
        self.ok('create', '--sql-catalog', 'sales')
        self.assertEqual(len(self.server.posts()), 2)
        self.assertEqual(self.server.posts()[-1]['body']['initial_grants'], [])
        self.assertNotEqual(json.loads(self.receipt_file('create').read_text())['key'], rejected_receipt['key'])

    def test_generic_errors_keep_uncertainty_even_when_get_absent(self):
        original = self.server.open
        cases = [(400, b'{"error":"SOME_CODE"}'), (409, b'not-json'), (409, b'{"error":"CONFLICT"}'),
                 (409, b'{"error":"INVALID_REQUEST"}'), (400, b'{"error":"STALE_PLAN"}'),
                 (500, b'{"error":"STALE_PLAN"}'), (503, b'{"error":"INVALID_REQUEST"}'),
                 (401, b'{"error":"ADMIN_REQUIRED"}'), (403, b'{"error":"STALE_PLAN"}')]
        frozen_key = None
        deliveries = []
        for status, raw in cases:
            def rejected(request, timeout):
                if request.method == 'POST':
                    deliveries.append((request.full_url, request.data, request.get_header('Idempotency-key')))
                    raise HTTPError(request.full_url, status, 'private', {}, io.BytesIO(raw))
                return original(request, timeout)
            command = ['create', '--sql-catalog', 'sales'] if frozen_key is None else ['retry', '--database', 'sales', '--action', 'create']
            with patch.object(self.server, 'open', side_effect=rejected):
                result = self.run_cli(*command)
            self.assertFalse(result['ok'])
            receipt = json.loads(self.receipt_file('create').read_text())
            self.assertEqual(receipt['status'], 'uncertain')
            frozen_key = frozen_key or receipt['key']
            self.assertEqual(receipt['key'], frozen_key)
        self.assertEqual(len(deliveries), len(cases))
        self.assertEqual(deliveries, [deliveries[0]] * len(cases))
        self.server.items = []
        self.error('DATABASE_NOT_FOUND', 'show', '--database', 'sales')
        self.error('RESPONSE_UNCERTAIN', 'create', '--sql-catalog', 'sales')
        self.assertEqual(json.loads(self.receipt_file('create').read_text())['key'], frozen_key)

    def test_retry_malformed_ack_retains_exact_receipt(self):
        self.server.lost = True
        self.error('RESPONSE_UNCERTAIN', 'create', '--sql-catalog', 'sales')
        path = self.receipt_file('create')
        before = json.loads(path.read_text())
        original = self.server.open
        def malformed(request, timeout):
            if request.method == 'POST':
                return Response(b'raw /private/password', raw=True)
            return original(request, timeout)
        with patch.object(self.server, 'open', side_effect=malformed):
            self.error('RESPONSE_UNCERTAIN', 'retry', '--database', 'sales', '--action', 'create')
        after = json.loads(path.read_text())
        self.assertEqual(before, after)

    def test_retry_current_invalid_steps_does_not_echo_injected_string(self):
        transport, _, _ = self.lost_after_admission()
        with transport:
            self.error('RESPONSE_UNCERTAIN', 'create', '--sql-catalog', 'sales')
            self.server.add_plan()
            self.server.detail['plans'][0]['public_summary']['fixed_steps'] = ['/private/password']
            result = self.ok('retry', '--database', 'sales', '--action', 'create')
        self.assertEqual(result['data']['current_status'], 'UNAVAILABLE')
        self.assertNotIn('/private', json.dumps(result))

    def test_retry_selection_and_phase_recovery_are_explicit(self):
        for argv in (['--action', 'approve'], ['--action', 'create', '--phase', 'full'],
                     ['--action', 'plan', '--phase', 'full', '--recovery']):
            self.error('INVALID_RETRY_SELECTION', 'retry', '--database', 'sales', *argv)
        self.server.add_plan('bootstrap'); self.server.add_plan('full'); self.server.add_plan('full', recovery=True)
        for phase, recovery in (('bootstrap', False), ('full', False), ('full', True)):
            self.review(phase, recovery)
            extra = ['--phase', phase] + (['--recovery'] if recovery else [])
            self.server.lost = True
            self.error('RESPONSE_UNCERTAIN', 'approve', '--database', 'sales', *extra)
        paths = list((self.root / 'receipts').glob('*/approve-*.json'))
        self.assertEqual(len(paths), 3)
        before = {p.name: json.loads(p.read_text()) for p in paths}
        self.error('RESPONSE_UNCERTAIN', 'retry', '--database', 'sales', '--action', 'approve', '--phase', 'full', '--recovery')
        last = self.server.posts()[-1]
        frozen = before['approve-full-recovery.json']
        self.assertEqual(last['body'], frozen['body'])
        self.assertEqual(last['headers']['Idempotency-key'], frozen['key'])

    def test_injected_review_steps_are_rejected_without_echo(self):
        self.server.add_plan()
        self.server.detail['plans'][0]['public_summary']['fixed_steps'] = ['/private/password']
        result = self.run_cli('plan', '--database', 'sales')
        self.assertEqual(result, dict(ok=False, error='PUBLIC_PROJECTION_REQUIRED'))
        self.assertNotIn('/private', json.dumps(result))
        self.assertEqual(self.server.posts(), [])

    def test_environment_precondition_race_preserves_frozen_uncertainty(self):
        original = self.server.open
        before = copy.deepcopy(self.server.detail)
        deliveries = []
        def reenrolled(request, timeout):
            if request.method == 'POST':
                deliveries.append((request.full_url, request.data, request.get_header('Idempotency-key'),
                                   request.get_header('X-shirokuma-environment')))
                # GET observed lab, but the authoritative POST server is now a
                # different enrollment. It rejects before ACK lookup/admission.
                self.assertEqual(deliveries[-1][-1], 'lab')
                raise HTTPError(request.full_url, 409, 'private', {},
                                io.BytesIO(b'{"error":"ENVIRONMENT_MISMATCH"}'))
            return original(request, timeout)
        with patch.object(self.server, 'open', side_effect=reenrolled):
            self.error('ENVIRONMENT_MISMATCH', 'create', '--sql-catalog', 'sales')
            self.assertEqual(len(deliveries), 1)
            path = self.receipt_file('create')
            frozen_bytes = path.read_bytes()
            frozen = json.loads(frozen_bytes)
            self.assertEqual(frozen['status'], 'uncertain')
            self.assertEqual(frozen['scope']['environment'], 'lab')
            self.assertEqual(frozen['key'], deliveries[0][2])
            self.assertEqual(frozen['body'], json.loads(deliveries[0][1]))
            self.error('RESPONSE_UNCERTAIN', 'create', '--sql-catalog', 'sales')
            self.assertEqual(len(deliveries), 1)
            self.error('ENVIRONMENT_MISMATCH', 'retry', '--database', 'sales', '--action', 'create')
            self.assertEqual(deliveries, [deliveries[0]] * 2)
            self.assertEqual(path.read_bytes(), frozen_bytes)
            self.server.capability['environment_id'] = 'new-enrollment'
            self.error('NO_FROZEN_INTENT', 'retry', '--database', 'sales', '--action', 'create')
            self.assertEqual(len(deliveries), 2)
            self.assertEqual(path.read_bytes(), frozen_bytes)
        self.assertEqual(self.server.detail, before)
        self.assertEqual(self.server.posts(), [])


class LoopbackTransportTests(unittest.TestCase):
    def test_real_http_wire(self):
        received = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append((self.path, dict(self.headers), json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                body = b'{"request_id":"one","creation_id":"one"}'
                self.send_response(201); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
            def log_message(self, *args):
                pass
        try:
            server = HTTPServer(('127.0.0.1', 0), Handler)
        except PermissionError:
            self.skipTest('Sandbox forbids a loopback listener; mocked Request tests still run')
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory).resolve() / 'auth.json'
                path.write_text(json.dumps(dict(username='alice', password='synthetic', selected_role='admin'))); path.chmod(0o600)
                client = cli.CreationClient(SimpleNamespace(client_auth_file=str(path)), 'http://127.0.0.1:' + str(server.server_port))
                client.request('POST', cli.COLLECTION, body=dict(sql_catalog='sales'), key='frozen')
                self.assertEqual(received[0][0], cli.COLLECTION)
                self.assertEqual(received[0][2], dict(sql_catalog='sales'))
                self.assertEqual(received[0][1]['Idempotency-Key'], 'frozen')
                self.assertEqual(received[0][1]['X-Shirokuma-Role'], 'admin')
        finally:
            server.shutdown(); server.server_close(); thread.join(5)


if __name__ == '__main__':
    unittest.main()


class PlanlessReplanClientTests(unittest.TestCase):
    setUp = CreationTests.setUp
    run_cli = CreationTests.run_cli
    ok = CreationTests.ok
    error = CreationTests.error

    def test_pending_is_get_only_then_explicit_planless_replan_posts_once(self):
        self.server.detail.update(next_actions=['withdraw'],progress_code='PREPARATION_PENDING')
        self.ok('progress','--database','sales')
        self.error('ACTION_NOT_AVAILABLE','replan','--database','sales')
        self.assertEqual(self.server.posts(),[])
        self.server.detail.update(revision=3,progress_code='REPLAN_REQUIRED',
            blocker=None,next_actions=['withdraw','replan'])
        self.ok('progress','--database','sales')
        self.assertEqual(self.server.posts(),[])
        self.server.pending = True
        self.ok('replan','--database','sales')
        self.ok('replan','--database','sales')
        self.assertEqual(len(self.server.posts()),1)
        post = self.server.posts()[0]
        self.assertEqual(post['path'],cli.COLLECTION+'/creation-1/replan')
        self.assertEqual(post['body'],dict(expected_revision=3,phase='bootstrap'))
        self.server.add_plan(sequence=2)
        self.ok('replan','--database','sales')
        self.assertEqual(len(self.server.posts()),1)


class LogicalCreationClientTests(unittest.TestCase):
    def test_warehouse_state_public_projection_only_accepts_three_strings(self):
        for state in ('ACTIVE','STOPPED','TRANSITIONING'):
            self.assertEqual(cli.public(dict(warehouse_state=state,private_config='/private/secret')),
                             dict(warehouse_state=state))
            self.assertEqual(cli.public(dict(current=dict(warehouse_state=state))),dict(current=dict(warehouse_state=state)))
        for invalid in (None,True,0,1,[],{},['ACTIVE'],{'state':'ACTIVE'},'active','UNAVAILABLE','/private/secret','secret diagnostic'):
            with self.subTest(invalid=invalid), self.assertRaises(cli.Failure) as error:
                cli.public(dict(warehouse_state=invalid))
            self.assertEqual(error.exception.code,'PUBLIC_PROJECTION_REQUIRED')


    setUp = CreationTests.setUp
    run_cli = CreationTests.run_cli
    ok = CreationTests.ok
    error = CreationTests.error
    receipt_file = CreationTests.receipt_file

    def logical(self):
        self.server.capability.update(creation_mode='logical_v1', foundation_id='foundation', foundation_revision=2)
        self.server.detail.update(creation_mode='logical_v1', foundation_id='foundation', foundation_revision=2,
            binding_revision=3, policy_revision=4, plans=[], progress_code='BLOCKED', next_actions=['resume'])

    def test_logical_profile_loads_only_same_public_references(self):
        path = self.root / 'logical-profile.json'
        path.write_text(json.dumps(dict(mode='logical_v1', client_auth_file=str(self.auth),
            sql_endpoint_url='http://localhost:8888', auth_file='/never/backend', private_binding='secret')))
        path.chmod(0o600)
        real = cli.runtime_profile.private_json
        reads = []
        def read(filename):
            reads.append(str(filename))
            return real(filename)
        with patch.dict(os.environ, SHIROKUMA_RUNTIME_PROFILE=str(path)), patch.object(cli.runtime_profile, 'private_json', side_effect=read):
            self.assertTrue(cli.main(['principals'], state_dir=self.root / 'receipts')['ok'])
        self.assertEqual(reads, [str(path), str(self.auth)])

    def test_logical_public_root_fields_states_and_blocker_no_arbitrary_fields(self):
        self.logical()
        self.server.detail.update(private_effect={'password': 'secret'}, catalog_sql='private',
                                  blocker='WAREHOUSE_START_REQUIRED', access_source='effective_grant')
        for progress in ('CREATING', 'VERIFYING', 'UNKNOWN', 'READY', 'BLOCKED', 'STOPPED'):
            self.server.detail['progress_code'] = progress
            for action in ('show', 'list', 'progress'):
                with self.subTest(progress=progress, action=action):
                    result = self.ok(action, *([] if action == 'list' else ['--database', 'sales']))
                    detail = result['data']['creations'][0] if action == 'list' else result['data']
                    for key in ('creation_mode', 'foundation_id', 'foundation_revision', 'binding_revision', 'policy_revision',
                                'creation_id', 'database_id', 'sql_catalog', 'initial_grants', 'plans', 'blocker', 'access_source'):
                        self.assertEqual(detail[key], self.server.detail[key])
                    self.assertEqual(detail['progress_code'], progress)
                    self.assertNotIn('private', json.dumps(result))
                    self.assertNotIn('secret', json.dumps(result))
        self.assertEqual(self.server.posts(), [])
        with self.assertRaises(cli.Failure):
            cli.public(dict(blocker='NEW_UNREVIEWED_BLOCKER'))

    def test_all_six_reviewed_blockers_project_and_private_codes_are_rejected(self):
        self.logical()
        for blocker in ('PRINCIPAL_STOPPED', 'OPERATOR_UNAVAILABLE', 'FOUNDATION_NOT_READY',
                        'WAREHOUSE_START_REQUIRED', 'RECONCILIATION_REQUIRED', 'CREATION_FAILED'):
            self.server.detail['blocker'] = blocker
            for action in ('show', 'list', 'progress'):
                with self.subTest(blocker=blocker, action=action):
                    result = self.ok(action, *([] if action == 'list' else ['--database', 'sales']))
                    detail = result['data']['creations'][0] if action == 'list' else result['data']
                    self.assertEqual(detail['blocker'], blocker)
        for blocker in ('NEW_UNREVIEWED_BLOCKER', 'private: password=secret', {'private': 'secret'}):
            with self.subTest(blocker=blocker), self.assertRaises(cli.Failure) as caught:
                cli.public({'creations': [{'blocker': blocker}]})
            self.assertEqual(str(caught.exception), 'PUBLIC_PROJECTION_REQUIRED')
        self.assertEqual(self.server.posts(), [])

    def test_logical_create_one_post_identical_input_and_warehouse_precondition(self):
        self.logical()
        self.ok('create', '--sql-catalog', 'sales')
        self.assertEqual(len(self.server.posts()), 1)
        self.assertEqual(self.server.posts()[0]['body'], dict(sql_catalog='sales', display_name='sales', description='', initial_grants=[]))
        self.ok('create', '--sql-catalog', 'sales')
        self.assertEqual(len(self.server.posts()), 1)
        self.ok('show', '--database', 'sales')
        self.assertEqual(len(self.server.posts()), 1)
        self.server.capability.update(available=False, blocker='WAREHOUSE_START_REQUIRED')
        self.error('WAREHOUSE_START_REQUIRED', 'create', '--sql-catalog', 'another')
        self.assertEqual(len(self.server.posts()), 1)

    def test_resume_receipt_ack_mapping_and_accepted_exact_retry_after_stopped(self):
        self.logical()
        original = self.server.open
        def opened(request, timeout):
            response = original(request, timeout)
            if request.method == 'POST':
                ack = json.loads(response.read())
                return Response(dict(ack, accepted_revision=1, action='RESUME', phase=None))
            return response
        self.server.open = opened
        ack = self.ok('resume', '--database', 'sales')['data']
        self.assertEqual(ack['action'], 'RESUME')
        self.assertIsNone(ack['phase'])
        post = self.server.posts()[0]
        self.assertEqual(post['path'], cli.COLLECTION + '/creation-1/resume')
        self.assertEqual(post['body'], dict(expected_revision=1))
        self.assertEqual(post['headers']['X-shirokuma-environment'], 'lab')
        self.ok('resume', '--database', 'sales')
        self.assertEqual(len(self.server.posts()), 1)
        self.server.detail.update(revision=9, progress_code='STOPPED', next_actions=[])
        result = self.ok('retry', '--database', 'sales', '--action', 'resume')['data']
        self.assertEqual(result['acknowledgment'], ack)
        self.assertEqual(result['current']['progress_code'], 'STOPPED')
        self.assertEqual(self.server.posts()[1], post)
        receipt = json.loads(self.receipt_file('resume').read_text())
        self.assertEqual(receipt['status'], 'complete')
        self.assertEqual(receipt['body'], post['body'])

    def test_uncertain_resume_get_only_then_explicit_exact_retry_no_new_revision(self):
        self.logical()
        self.server.lost = True
        self.error('RESPONSE_UNCERTAIN', 'resume', '--database', 'sales')
        frozen = json.loads(self.receipt_file('resume').read_text())
        first = self.server.posts()[0]
        self.ok('progress', '--database', 'sales')
        self.error('RESPONSE_UNCERTAIN', 'resume', '--database', 'sales')
        self.assertEqual(len(self.server.posts()), 1)
        self.server.lost = False
        self.server.detail.update(revision=7, progress_code='UNKNOWN', next_actions=['reconcile'])
        self.ok('retry', '--database', 'sales', '--action', 'resume')
        self.assertEqual(self.server.posts()[1], first)
        self.assertEqual(json.loads(self.receipt_file('resume').read_text())['key'], frozen['key'])

    def test_resume_requires_logical_blocked_and_server_offer_legacy_still_reviewed(self):
        for mode, progress, actions, blocker in [
            (None, 'BLOCKED', ['resume'], None),
            ('logical_v1', 'UNKNOWN', ['resume'], None),
            ('logical_v1', 'STOPPED', ['resume'], None),
            ('logical_v1', 'CREATING', ['resume'], None),
            ('logical_v1', 'BLOCKED', [], None),
            ('logical_v1', 'BLOCKED', ['resume'], 'PRINCIPAL_STOPPED'),
        ]:
            with self.subTest(mode=mode, progress=progress):
                self.server.detail.update(creation_mode=mode, progress_code=progress, next_actions=actions, blocker=blocker)
                self.error('ACTION_NOT_AVAILABLE', 'resume', '--database', 'sales')
        self.assertEqual(self.server.posts(), [])
        self.logical()
        for action in ('plan', 'approve', 'replan'):
            self.server.detail['next_actions'] = ['plans', 'approve', 'replan']
            # approve requires its prior reviewed plan even before eligibility.
            self.error('PRIOR_REVIEW_REQUIRED' if action == 'approve' else 'ACTION_NOT_AVAILABLE', action, '--database', 'sales')
        self.assertEqual(self.server.posts(), [])

    def test_resume_invalid_flags_and_receipt_tampering_never_send(self):
        with patch.object(cli, 'profile_references', side_effect=AssertionError('profile read')):
            for extra in ([], ['--database', 'sales', '--phase', 'full'], ['--database', 'sales', '--recovery']):
                with self.assertRaises(SystemExit), patch('sys.stderr', new=io.StringIO()):
                    cli.main(['resume', *extra])
            for extra in (['--phase', 'full'], ['--recovery']):
                self.assertFalse(cli.main(['retry', '--database', 'sales', '--action', 'resume', *extra])['ok'])
        self.logical()
        self.server.lost = True
        self.error('RESPONSE_UNCERTAIN', 'resume', '--database', 'sales')
        file = self.receipt_file('resume')
        original = json.loads(file.read_text())
        for change in (dict(path=cli.COLLECTION + '/creation-1/approve'), dict(body=dict(expected_revision=0)), dict(body=dict(expected_revision=1, phase='full'))):
            file.write_text(json.dumps(dict(original, **change)))
            self.error('INVALID_CLIENT_RECEIPT', 'retry', '--database', 'sales', '--action', 'resume')
        self.assertEqual(len(self.server.posts()), 1)

    def test_resume_malformed_ack_and_arbitrary_503_remain_uncertain(self):
        self.logical()
        original = self.server.open
        def opened(request, timeout):
            response = original(request, timeout)
            if request.method == 'POST':
                ack = json.loads(response.read())
                return Response(dict(ack, action='CREATE', phase=None))
            return response
        self.server.open = opened
        self.error('RESPONSE_UNCERTAIN', 'resume', '--database', 'sales')
        first = self.server.posts()[0]
        def unavailable(request, timeout):
            if request.method == 'POST':
                original(request, timeout)
                raise HTTPError(request.full_url, 503, '', {}, io.BytesIO(b'{"error":"OPERATOR_UNAVAILABLE","message":"not accepted"}'))
            return original(request, timeout)
        self.server.open = unavailable
        self.error('OPERATOR_UNAVAILABLE', 'retry', '--database', 'sales', '--action', 'resume')
        receipt = json.loads(self.receipt_file('resume').read_text())
        self.assertEqual(receipt['status'], 'uncertain')
        self.assertNotIn('ack', receipt)
        self.assertEqual(self.server.posts()[1], first)
