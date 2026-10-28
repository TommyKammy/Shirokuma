"""P5 admission contracts on temporary ledgers and explicit named accounts."""
import base64
import copy
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools/local_experiment'))
import catalog_admin
import control_store
import database_creation_auth as delegation
import database_creations as creations
import database_generation
import data_management as dm
import grant_management
import request_auth
import warehouse_operations as operations
from tests.test_database_generation import spec


class Probe:
    def __init__(self):
        self.foundation = self.owner = True
        self.calls = 0

    def probe(self, enrollment):
        self.calls += 1
        return creations.FoundationReadiness(self.foundation,self.owner)


class CreationFixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir='/private/tmp')
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name).resolve()
        self.account_file = self.directory/'accounts.json'
        salt = '12'*16
        hashed = request_auth.password_hash('password',salt)
        self.accounts = {name:dict(role=role,platform_admin=role=='admin',salt=salt,password_hash=hashed)
            for name,role in [('alice','admin'),('bob','admin'),('executor','admin'),('reader','bi_viewer')]}
        self.save_accounts()
        for p in (patch.dict(os.environ,SHIROKUMA_STATE_DIR=str(self.directory/'state'),
                SHIROKUMA_AUTH_MODE='shared',SHIROKUMA_AUTH_FILE=str(self.account_file)),
                patch.object(creations,'_enrollment',None),patch.object(creations,'_foundation',None)):
            p.start()
            self.addCleanup(p.stop)
        self.config = creations.Enrollment('test-environment','LOCAL','test-enrollment',1,'executor')
        self.probe = Probe()
        creations.install_foundation(self.config,self.probe)
        self.alice, self.bob, self.executor = [request_auth.authenticate_principal(self.headers(n)) for n in ('alice','bob','executor')]

    def save_accounts(self):
        self.account_file.write_text(json.dumps(self.accounts))

    def headers(self, name='alice'):
        return {'Authorization':'Basic '+base64.b64encode((name+':password').encode()).decode(),
                'X-Shirokuma-Role':'admin'}

    def create(self, *, actor=None, key='create', **payload):
        return creations.create(actor or self.alice,'admin',dict(sql_catalog='research',**payload),key)[0]

    def snapshot(self):
        with control_store.connect() as db:
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {t:[tuple(r) for r in db.execute('SELECT * FROM '+t+' ORDER BY rowid')] for t in tables}

    def prepare(self, ack, phase='bootstrap', predecessor=None):
        current = creations.get(self.alice,'admin',ack['creation_id'])
        request = creations.mutate(self.alice,'admin',ack['creation_id'],'PREPARE_PLAN',
            dict(expected_revision=current['revision'],phase=phase),'prepare-'+phase)
        public = catalog_admin.export_spec('candidate-'+phase,spec(1))
        bundle = database_generation.render(public)
        refs = bundle['tofu']['bootstrap_secret_refs']+bundle['tofu']['deferred_secret_refs']
        observations = [dict(r,exists=True,uid='uid-'+r['name'],resource_version='1',immutable=True) for r in refs]
        linked = creations._prepare_linked_plan(self.config,ack['creation_id'],bundle,observations,
            private_ref='sealed:candidate-'+phase,public_summary={'code':'PLAN_READY'},phase=phase,predecessor_id=predecessor,
            preparation_request_id=request['request_id'])
        return creations.get(self.alice,'admin',ack['creation_id']),linked,observations

    def approve(self, ack):
        detail,linked,observations = self.prepare(ack)
        plan = detail['plans'][-1]
        payload = dict(expected_revision=detail['revision'],plan_id=plan['plan_id'],operation_id=plan['operation_id'],
            expected_operation_revision=plan['operation_revision'],delivery_digest=plan['delivery_digest'])
        approval = creations.mutate(self.alice,'admin',ack['creation_id'],'APPROVE',payload,'approval',phase='bootstrap')
        return approval,plan,observations


class ProjectionTests(CreationFixture):
    def detail(self, ack, actor=None):
        return creations.get(actor or self.alice,'admin',ack['creation_id'])

    def test_prepared_bootstrap_bounded_grant_drift_exposes_only_safe_full_replan(self):
        ack = self.create(initial_grants=[dict(principal_id='reader',maximum_role='bi_viewer')])
        detail, linked, _ = self.prepare(ack)
        operation = linked['operation_id']
        with control_store.connect() as db:
            db.execute("UPDATE operations SET state='PREPARED' WHERE id=?",(operation,))
            db.execute("UPDATE operation_steps SET state='SUCCEEDED',evidence_ref='test:prepared' WHERE operation_id=?",(operation,))
            db.execute("UPDATE warehouse_controls SET state='APPLYING',operation_id=?",(operation,))
        self.assertEqual(self.detail(ack)['next_actions'],['withdraw','plans'])
        grant_management.mutate(self.alice,'admin',detail['database_id'],
            dict(principal_id='reader',maximum_role='revoked',expected_revision=1),'revoke-target')
        self.assertEqual(self.detail(ack)['next_actions'],['withdraw','replan'])
        self.assertEqual(self.detail(ack,self.bob)['next_actions'],[])
        for sql, value, restore in (
                ('UPDATE warehouse_controls SET operation_id=?',None,operation),
                ('UPDATE warehouse_controls SET state=?','UNKNOWN','APPLYING'),
                ('UPDATE operations SET state=? WHERE id=\''+operation+'\'','UNKNOWN','PREPARED'),
                ('UPDATE operation_steps SET state=? WHERE operation_id=\''+operation+'\' AND step=\'verify\'','RUNNING','SUCCEEDED')):
            with self.subTest(sql=sql):
                with control_store.connect() as db:
                    db.execute(sql,(value,))
                self.assertNotIn('replan',self.detail(ack)['next_actions'])
                with control_store.connect() as db:
                    db.execute(sql,(restore,))
        self.probe.foundation = False
        self.assertNotIn('replan',self.detail(ack)['next_actions'])
        self.probe.foundation = True
        # Current account authority loss persists STOP and must hide the route.
        self.accounts['executor']['disabled'] = True
        self.save_accounts()
        self.assertNotIn('replan',self.detail(ack)['next_actions'])

    def test_registered_preparing_review_and_safe_summary(self):
        ack = self.create()
        self.assertEqual(self.detail(ack)['next_actions'],['withdraw','plans'])
        detail, linked, observations = self.prepare(ack)
        self.assertEqual(detail['progress_code'],'PLAN_REVIEW_REQUIRED')
        self.assertEqual(detail['next_actions'],['withdraw','approve','replan'])
        with control_store.connect() as db:
            db.execute('UPDATE db_creation_phase_plans SET public_summary=?',
                (json.dumps(dict(code='RECOVERY_READY',password='secret',SQL='SELECT private',
                    manifest='/private/path',nextUri='https://private')),))
            db.execute("UPDATE db_creation_requests SET stop_reason='HISTORICAL_FAILURE' WHERE action='CREATE'")
        detail = self.detail(ack)
        self.assertIsNone(detail['blocker'])
        self.assertEqual(detail['plans'][-1]['public_summary'],dict(code='PLAN_READY',
            impact='BOOTSTRAP_MAINTENANCE',fixed_steps=list(operations._ALL_STEPS)))
        for secret in ('password','SELECT private','/private/path','nextUri','HISTORICAL_FAILURE'):
            self.assertNotIn(secret,json.dumps(detail))
        self.assertEqual(self.detail(ack,self.bob)['next_actions'],[])
        self.assertEqual(detail['access_source'],'platform_admin')
        self.assertEqual(detail['initial_grants'],[])

    def test_phase_progress_truth_matrix_and_no_get_success_writes(self):
        ack = self.create()
        detail, linked, observations = self.prepare(ack)
        op = detail['plans'][-1]['operation_id']
        cases = [('AWAITING_APPLY',None,'PLAN_REVIEW_REQUIRED','approve'),
            ('PREPARED',None,'FULL_REVIEW_REQUIRED','plans'),
            ('DRAINING','apply_secrets','APPLYING',None),
            ('APPLYING','verify','VERIFYING',None),
            ('UNKNOWN','verify','RECONCILE_REQUIRED','reconcile'),
            ('VERIFIED_STALE',None,'REPLAN_REQUIRED','replan')]
        for state,step,progress,action in cases:
            with self.subTest(state=state):
                with control_store.connect() as db:
                    db.execute('UPDATE operations SET state=? WHERE id=?',(state,op))
                    db.execute("UPDATE operation_steps SET state='PENDING' WHERE operation_id=?",(op,))
                    if step:
                        db.execute('UPDATE operation_steps SET state=? WHERE operation_id=? AND step=?',
                            ('UNKNOWN' if state == 'UNKNOWN' else 'RUNNING',op,step))
                before = self.snapshot()
                current = self.detail(ack)
                self.assertEqual(current['progress_code'],progress)
                if action:
                    self.assertIn(action,current['next_actions'])
                else:
                    self.assertEqual(current['next_actions'],['withdraw'])
                self.assertNotIn('recover-verify',current['next_actions'])
                self.assertEqual(before,self.snapshot())
        with control_store.connect() as db:
            db.execute("UPDATE db_creations SET reservation_active=0,terminal_outcome='APPLIED'")
            db.execute("UPDATE db_creation_requests SET stop_reason='OLD_FAILURE'")
        current = self.detail(ack)
        self.assertEqual((current['progress_code'],current['next_actions'],current['blocker']),('READY',[],None))

    def test_get_list_sample_once_outside_connections_and_never_dispatch(self):
        ack = self.create()
        active = []
        original = control_store.connect
        from contextlib import contextmanager
        @contextmanager
        def connection(*args,**kwargs):
            with original(*args,**kwargs) as db:
                active.append(db)
                try:
                    yield db
                finally:
                    active.pop()
        def sample(config):
            self.assertEqual(active,[])
            return creations.FoundationReadiness(True,True)
        import database_creation_bridge as bridge
        before = self.snapshot()
        with patch.object(control_store,'connect',side_effect=connection),\
                patch.object(self.probe,'probe',side_effect=sample) as probe,\
                patch.object(bridge,'dispatch') as dispatch,patch.object(operations,'apply') as apply:
            self.detail(ack)
            self.assertEqual(probe.call_count,1)
            probe.reset_mock()
            self.assertEqual(len(creations.listing(self.alice,'admin')['creations']),1)
            self.assertEqual(probe.call_count,1)
            dispatch.assert_not_called(); apply.assert_not_called()
        self.assertEqual(before,self.snapshot())

    def test_current_executor_loss_latches_stop_restore_and_release(self):
        ack = self.create()
        approval,plan,observations = self.approve(ack)
        self.accounts['executor']['enabled'] = False
        self.save_accounts()
        detail = self.detail(ack)
        self.assertEqual(detail['next_actions'],['withdraw'])
        self.assertEqual(detail['blocker'],'PRINCIPAL_STOPPED')
        self.accounts['executor']['enabled'] = True
        self.save_accounts()
        self.assertEqual(self.detail(ack)['next_actions'],['withdraw','reconcile'])
        delegation.release_principal(self.config,self.executor,'executor','release-projection')
        self.assertIn('approve',self.detail(ack)['next_actions'])
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT approval_state FROM db_creation_requests WHERE id=?',
                (approval['request_id'],)).fetchone()[0],'REVOKED')
        self.accounts['alice']['platform_admin'] = False
        self.save_accounts()
        self.assertEqual(self.detail(ack,self.bob)['progress_code'],'STOPPED')
        self.accounts['alice']['platform_admin'] = True
        self.save_accounts()
        self.assertEqual(self.detail(ack)['next_actions'],[])

    def test_capabilities_actual_authority_and_relevant_durable_stops(self):
        ack = self.create()
        approval,plan,observations = self.approve(ack)
        self.assertTrue(creations.capabilities(self.alice,'admin')['available'])
        self.accounts['executor']['enabled'] = False
        self.save_accounts()
        unavailable = creations.capabilities(self.alice,'admin')
        self.assertEqual((unavailable['available'],unavailable['blocker']),(False,'PRINCIPAL_STOPPED'))
        self.accounts['executor']['enabled'] = True
        self.save_accounts()
        self.assertFalse(creations.capabilities(self.alice,'admin')['available'])
        with control_store.connect() as db:
            payload = json.loads(db.execute('SELECT payload FROM db_creation_requests WHERE id=?',
                (approval['request_id'],)).fetchone()[0])
        before = self.snapshot()
        self.assertEqual(creations.mutate(self.alice,'admin',ack['creation_id'],'APPROVE',payload,'approval',phase='bootstrap'),approval)
        self.assertEqual(before,self.snapshot())
        fresh_payload = dict(payload,expected_revision=self.detail(ack)['revision'])
        with self.assertRaisesRegex(operations.OperationError,'PRINCIPAL_STOPPED'):
            creations.mutate(self.alice,'admin',ack['creation_id'],'APPROVE',fresh_payload,'fresh-denied',phase='bootstrap')
        delegation.release_principal(self.config,self.executor,'executor','capabilities-release')
        self.assertTrue(creations.capabilities(self.alice,'admin')['available'])
        delegation.stop_principal(self.config,self.executor,'alice','requester-stop')
        self.assertFalse(creations.capabilities(self.alice,'admin')['available'])
        self.assertEqual(self.detail(ack)['next_actions'],[])

    def test_executor_stop_allows_only_known_observation_until_release(self):
        ack = self.create()
        self.approve(ack)
        delegation.stop_principal(self.config,self.executor,'executor','stop-for-observation')
        for enabled,foundation,owner,expected in [(True,True,True,True),(False,True,True,False),
                (True,False,True,False),(True,True,False,False),(True,True,True,True)]:
            with self.subTest(enabled=enabled,foundation=foundation,owner=owner):
                self.accounts['executor']['enabled'] = enabled
                self.save_accounts()
                self.probe.foundation,self.probe.owner = foundation,owner
                current = self.detail(ack)
                self.assertEqual('reconcile' in current['next_actions'],expected)
                self.assertFalse(set(current['next_actions']) & {'plans','replan','approve','recover-verify'})
        observed = creations.mutate(self.alice,'admin',ack['creation_id'],'RECONCILE',
            dict(expected_revision=current['revision']),'explicit-stopped-observation')
        self.assertEqual(observed['action'],'RECONCILE')
        with control_store.connect() as db:
            self.assertEqual(db.execute("SELECT state FROM db_creation_principal_gates WHERE principal_id='executor'").fetchone()[0],'STOPPED')
        delegation.stop_principal(self.config,self.executor,'alice','stop-requester-observation')
        self.assertEqual(self.detail(ack)['next_actions'],[])

    def test_canonical_failed_claimed_approval_never_projects_running(self):
        ack = self.create()
        approval,plan,observations = self.approve(ack)
        permit = delegation.claim(self.config,self.executor,approval['request_id'],owner_id='owner')
        # Model the canonical failed boundary on a real linked/claimed Operation;
        # this is not a failed delivery or a synthetic successful effect.
        with delegation.delegated(permit),delegation.transaction() as db:
            operations._state(db,plan['operation_id'],'FAILED')
        current = self.detail(ack)
        self.assertEqual(current['progress_code'],'REPLAN_REQUIRED')
        self.assertIn('replan',current['next_actions'])
        with control_store.connect() as db:
            row = db.execute('SELECT * FROM db_creation_requests WHERE id=?',(approval['request_id'],)).fetchone()
            self.assertEqual((row['approval_state'],row['dispatch_state']),('VALID','CLAIMED'))
        # A succeeded non-publish effect still denies POST replan; projection
        # must not promise that action even though the parent is FAILED.
        with control_store.connect() as db:
            db.execute("UPDATE operation_steps SET state='SUCCEEDED',evidence_ref='fixture:confirmed-effect' WHERE operation_id=? AND step='apply_secrets'",(plan['operation_id'],))
        current = self.detail(ack)
        self.assertEqual(current['progress_code'],'REPLAN_REQUIRED')
        self.assertNotIn('replan',current['next_actions'])
        with self.assertRaisesRegex(operations.OperationError,'RECONCILIATION_REQUIRED'):
            creations.mutate(self.alice,'admin',ack['creation_id'],'REPLAN',
                dict(expected_revision=current['revision'],phase='bootstrap'),'denied-failed-replan')
        with control_store.connect() as db:
            db.execute("UPDATE operation_steps SET state='PENDING',evidence_ref=NULL WHERE operation_id=? AND step='apply_secrets'",(plan['operation_id'],))
        current = self.detail(ack)
        result = creations.mutate(self.alice,'admin',ack['creation_id'],'REPLAN',
            dict(expected_revision=current['revision'],phase='bootstrap'),'eligible-failed-replan')
        self.assertEqual(result['action'],'REPLAN')


class StaleUnstartedRegistryTests(CreationFixture):
    def full_plan(self):
        ack = self.create(initial_grants=[dict(principal_id='reader',maximum_role='bi_viewer')])
        _, bootstrap, _ = self.prepare(ack)
        operation = bootstrap['operation_id']
        with control_store.connect() as db:
            db.execute("UPDATE operations SET state='PREPARED' WHERE id=?",(operation,))
            db.execute("UPDATE operation_steps SET state='SUCCEEDED',evidence_ref='test:prepared' WHERE operation_id=?",(operation,))
            db.execute("UPDATE warehouse_controls SET state='APPLYING',operation_id=?",(operation,))
        detail, _, _ = self.prepare(ack,'full',operation)
        plan = detail['plans'][-1]
        payload = dict(expected_revision=detail['revision'],plan_id=plan['plan_id'],
            operation_id=plan['operation_id'],expected_operation_revision=plan['operation_revision'],
            delivery_digest=plan['delivery_digest'])
        return ack, plan, payload

    def drift(self, ack, kind):
        with control_store.connect() as db:
            database_id = db.execute('SELECT database_id FROM db_creations WHERE id=?',
                (ack['creation_id'],)).fetchone()[0]
        if kind == 'metadata':
            with control_store.connect() as db:
                database = dm.get_database(db,database_id)
                dm.update_database_display(db,database.id,database.revision,'Updated display','Updated description')
        else:
            grant_management.mutate(self.alice,'admin',database_id,
                dict(principal_id='reader',maximum_role='revoked',expected_revision=1),'narrow-target')

    def intake(self, ack, payload, key):
        return creations._mutate(self.alice,'admin',ack['creation_id'],'APPROVE',payload,key,phase='full')

    def test_real_drift_before_and_after_valid_or_claimed_approval(self):
        for kind in ('metadata','grants'):
            for approval_state in (None,'VALID','CLAIMED'):
                with self.subTest(kind=kind,approval_state=approval_state):
                    # Each case has independent canonical generations and account-backed authority.
                    with StaleUnstartedRegistryTests() as fixture:
                        fixture.check_drift_approval(kind,approval_state)

    def __enter__(self):
        self.setUp()
        return self

    def __exit__(self, *args):
        self.doCleanups()

    def check_drift_approval(self, kind, approval_state):
        ack, plan, payload = self.full_plan()
        before = self.snapshot()
        normal = creations.get(self.alice,'admin',ack['creation_id'])
        self.assertEqual(normal['progress_code'],'FULL_REVIEW_REQUIRED')
        self.assertIn('approve',normal['next_actions'])
        self.assertEqual(before,self.snapshot())
        original = None
        if approval_state:
            original, fresh = self.intake(ack,payload,'original')
            self.assertTrue(fresh)
            if approval_state == 'CLAIMED':
                delegation.claim(self.config,self.executor,original['request_id'],owner_id='owner')
            before = self.snapshot()
            self.assertEqual(creations.get(self.alice,'admin',ack['creation_id'])['progress_code'],'APPLYING')
            self.assertEqual(before,self.snapshot())
        self.drift(ack,kind)
        before = self.snapshot()
        detail = creations.get(self.alice,'admin',ack['creation_id'])
        self.assertEqual((detail['progress_code'],detail['blocker']),('REPLAN_REQUIRED','STALE_PLAN'))
        self.assertEqual(detail['next_actions'],['withdraw','replan'])
        self.assertEqual(creations.get(self.bob,'admin',ack['creation_id'])['next_actions'],[])
        self.assertEqual(before,self.snapshot())
        fresh_payload = dict(payload,expected_revision=detail['revision'])
        with self.assertRaises(operations.OperationError) as caught:
            self.intake(ack,fresh_payload,'fresh-stale')
        self.assertEqual((caught.exception.code,caught.exception.status),('STALE_PLAN',409))
        self.assertEqual(before,self.snapshot())
        if original:
            replay, fresh = self.intake(ack,payload,'original')
            self.assertEqual(replay,original)
            self.assertFalse(fresh)
            self.assertEqual(before,self.snapshot())
            with control_store.connect() as db:
                row = db.execute('SELECT * FROM db_creation_requests WHERE id=?',(original['request_id'],)).fetchone()
                self.assertEqual(row['approval_state'],'VALID')
                self.assertEqual(row['dispatch_state'],'CLAIMED' if approval_state == 'CLAIMED' else 'BLOCKED')
        # Existing full replan guards still require the exact prepared predecessor.
        with control_store.connect() as db:
            db.execute("UPDATE warehouse_controls SET state='UNKNOWN'")
        self.assertNotIn('replan',creations.get(self.alice,'admin',ack['creation_id'])['next_actions'])

    def test_stale_approve_rejects_before_writes_independent_of_projection(self):
        for kind in ('metadata','grants'):
            with self.subTest(kind=kind), StaleUnstartedRegistryTests() as fixture:
                ack, _, payload = fixture.full_plan()
                fixture.drift(ack,kind)
                before = fixture.snapshot()
                with self.assertRaises(operations.OperationError) as caught:
                    fixture.intake(ack,payload,'already-stale')
                self.assertEqual((caught.exception.code,caught.exception.status),('STALE_PLAN',409))
                self.assertEqual(before,fixture.snapshot())

    def test_stale_does_not_override_active_effect_or_terminal_views(self):
        ack, plan, payload = self.full_plan()
        self.intake(ack,payload,'original')
        self.drift(ack,'metadata')
        op = plan['operation_id']
        for state,step_state,progress in (
                ('UNKNOWN','PENDING','RECONCILE_REQUIRED'),
                ('RUNNING','PENDING','APPLYING'),
                ('AWAITING_APPLY','RUNNING','VERIFYING'),
                ('AWAITING_APPLY','UNKNOWN','RECONCILE_REQUIRED'),
                ('AWAITING_APPLY','SUCCEEDED','APPLYING'),
                ('FAILED','PENDING','REPLAN_REQUIRED'),
                ('APPLIED','SUCCEEDED','REPLAN_REQUIRED')):
            with self.subTest(state=state,step_state=step_state):
                with control_store.connect() as db:
                    db.execute('UPDATE operations SET state=? WHERE id=?',(state,op))
                    db.execute("UPDATE operation_steps SET state=? WHERE operation_id=? AND step='verify'",(step_state,op))
                before = self.snapshot()
                detail = creations.get(self.alice,'admin',ack['creation_id'])
                self.assertEqual(detail['progress_code'],progress)
                self.assertIsNone(detail['blocker'])
                self.assertEqual(before,self.snapshot())
        with control_store.connect() as db:
            db.execute("UPDATE operations SET state='AWAITING_APPLY' WHERE id=?",(op,))
            db.execute("UPDATE operation_steps SET state='PENDING' WHERE operation_id=?",(op,))
        current = creations.get(self.alice,'admin',ack['creation_id'])
        creations.mutate(self.alice,'admin',ack['creation_id'],'REPLAN',
            dict(expected_revision=current['revision'],phase='full'),'newer-preparation')
        self.assertEqual(creations.get(self.alice,'admin',ack['creation_id'])['progress_code'],'PREPARATION_PENDING')
        with control_store.connect() as db:
            db.execute("UPDATE db_creations SET reservation_active=0,terminal_outcome='APPLIED'")
        detail = creations.get(self.alice,'admin',ack['creation_id'])
        self.assertEqual((detail['progress_code'],detail['blocker'],detail['next_actions']),('READY',None,[]))

    def test_exact_pending_steps_and_specific_registry_error_only(self):
        ack, plan, payload = self.full_plan()
        self.drift(ack,'metadata')
        with control_store.connect() as db:
            row = tuple(db.execute("SELECT * FROM operation_steps WHERE operation_id=? AND step='verify'",
                (plan['operation_id'],)).fetchone())
            db.execute("DELETE FROM operation_steps WHERE operation_id=? AND step='verify'",(plan['operation_id'],))
        before = self.snapshot()
        detail = creations.get(self.alice,'admin',ack['creation_id'])
        self.assertEqual(detail['progress_code'],'FULL_REVIEW_REQUIRED')
        self.assertIsNone(detail['blocker'])
        self.assertEqual(before,self.snapshot())
        with control_store.connect() as db:
            db.execute('INSERT INTO operation_steps VALUES (?,?,?,?,?)',row)
        # Fault injection supplements real drift tests: unrelated errors are never stale proof.
        for error in (operations.OperationError('OPERATION_NOT_FOUND',404),RuntimeError('registry failure')):
            with self.subTest(error=error), patch.object(operations,'_check_registry',side_effect=error):
                before = self.snapshot()
                with self.assertRaises(type(error)) as caught:
                    creations.get(self.alice,'admin',ack['creation_id'])
                self.assertIs(caught.exception,error)
                with self.assertRaises(type(error)) as caught:
                    self.intake(ack,payload,'unrelated-error')
                self.assertIs(caught.exception,error)
                self.assertEqual(before,self.snapshot())

    def test_bootstrap_stale_and_recovery_intake_remain_separate(self):
        ack = self.create()
        approval, plan, _ = self.approve(ack)
        delegation.claim(self.config,self.executor,approval['request_id'],owner_id='owner')
        self.drift(ack,'metadata')
        detail = creations.get(self.alice,'admin',ack['creation_id'])
        self.assertEqual((detail['progress_code'],detail['blocker'],detail['next_actions']),
            ('REPLAN_REQUIRED','STALE_PLAN',['withdraw','replan']))
        # Recovery retains its existing UNKNOWN-state admission, despite registry drift.
        with delegation.transaction() as db:
            recovery_id = creations._attach_plan(db,self.config,ack['creation_id'],plan['operation_id'],
                private_ref='sealed:recovery',public_summary={'code':'PLAN_READY'},
                purpose='VERIFY_RECOVERY',recovery_digest='recovery-digest')
            db.execute("UPDATE operations SET state='UNKNOWN' WHERE id=?",(plan['operation_id'],))
        detail = creations.get(self.alice,'admin',ack['creation_id'])
        self.assertEqual(detail['progress_code'],'RECONCILE_REQUIRED')
        self.assertIsNone(detail['blocker'])
        recovery = next(p for p in detail['plans'] if p['plan_id'] == recovery_id)
        payload = dict(expected_revision=detail['revision'],plan_id=recovery_id,operation_id=plan['operation_id'],
            expected_operation_revision=recovery['operation_revision'],delivery_digest=recovery['delivery_digest'],
            recovery_digest=recovery['recovery_digest'])
        result = creations.mutate(self.alice,'admin',ack['creation_id'],'RECOVER_VERIFY',payload,
            'recovery',phase='bootstrap')
        self.assertEqual(result['action'],'RECOVER_VERIFY')

    def test_real_account_auth_and_current_loss_precede_stale_evidence(self):
        ack, _, payload = self.full_plan()
        self.drift(ack,'metadata')
        headers = Message()
        for name,value in self.headers().items():
            headers[name] = value
        handler = SimpleNamespace(path=creations.COLLECTION+'/'+ack['creation_id'],headers=headers,respond=Mock())
        before = self.snapshot()
        headers.replace_header('Authorization','Basic '+base64.b64encode(b'alice:wrong').decode())
        with patch.object(operations,'_check_registry',wraps=operations._check_registry) as check:
            self.assertTrue(creations.dispatch(handler,'GET'))
            self.assertEqual(handler.respond.call_args.args[1],401)
            check.assert_not_called()
        headers.replace_header('Authorization',self.headers()['Authorization'])
        headers.replace_header('X-Shirokuma-Role','bi_viewer')
        with patch.object(operations,'_check_registry',wraps=operations._check_registry) as check:
            creations.dispatch(handler,'GET')
            self.assertEqual(handler.respond.call_args.args[1],403)
            check.assert_not_called()
        self.assertEqual(before,self.snapshot())
        headers.replace_header('X-Shirokuma-Role','admin')
        creations.dispatch(handler,'GET')
        self.assertEqual(handler.respond.call_args.args[1],200)
        self.accounts['alice']['disabled'] = True
        self.save_accounts()
        with patch.object(operations,'_check_registry',wraps=operations._check_registry) as check:
            creations.dispatch(handler,'GET')
            self.assertEqual(handler.respond.call_args.args[1],403)
            # A previously authenticated request also re-reads the authoritative file.
            with self.assertRaisesRegex(operations.OperationError,'ADMIN_REQUIRED'):
                creations.get(self.alice,'admin',ack['creation_id'])
            with self.assertRaisesRegex(operations.OperationError,'ADMIN_REQUIRED'):
                self.intake(ack,payload,'denied')
            check.assert_not_called()
        with control_store.connect() as db:
            gate = db.execute("SELECT state FROM db_creation_principal_gates WHERE principal_id='alice'").fetchone()
            self.assertEqual(gate[0],'STOPPED')


class CreationTests(CreationFixture):
    def approval_intake(self, action):
        ack = self.create()
        detail, linked, observations = self.prepare(ack)
        plan = detail['plans'][-1]
        if action == 'RECOVER_VERIFY':
            # Canonical association in a disposable ledger; no runtime effect or
            # fabricated successful completion is needed to exercise intake.
            with delegation.transaction() as db:
                plan_id = creations._attach_plan(db,self.config,ack['creation_id'],plan['operation_id'],
                    private_ref='sealed:recovery',public_summary={'code':'PLAN_READY'},
                    purpose='VERIFY_RECOVERY',recovery_digest='temporary-recovery-digest')
                db.execute("UPDATE operations SET state='UNKNOWN' WHERE id=?",(plan['operation_id'],))
            detail = creations.get(self.alice,'admin',ack['creation_id'])
            plan = next(p for p in detail['plans'] if p['plan_id'] == plan_id)
        payload = dict(expected_revision=detail['revision'],plan_id=plan['plan_id'],
            operation_id=plan['operation_id'],expected_operation_revision=plan['operation_revision'],
            delivery_digest=plan['delivery_digest'])
        if action == 'RECOVER_VERIFY':
            payload['recovery_digest'] = plan['recovery_digest']
        return ack, payload

    def intake(self, ack, action, payload, key):
        return creations.mutate(self.alice,'admin',ack['creation_id'],action,payload,key,phase='bootstrap')

    def assert_intake_rejected(self, ack, action, payload, key, code):
        before = self.snapshot()
        with self.assertRaisesRegex(operations.OperationError,code):
            self.intake(ack,action,payload,key)
        self.assertEqual(self.snapshot(),before)

    def release_for_intake(self, ack, action, payload):
        if action == 'RECOVER_VERIFY':
            with self.assertRaisesRegex(operations.OperationError,'RECONCILIATION_REQUIRED'):
                delegation.release_principal(self.config,self.executor,'executor','release')
            # Model classified uncertainty only in the temporary ledger so the
            # real RELEASE guard can run. Reintroduce UNKNOWN afterward to test
            # a fresh recovery approval; this does not claim recovery completed.
            with control_store.connect() as db:
                db.execute("UPDATE operations SET state='AWAITING_APPLY' WHERE id=?",(payload['operation_id'],))
        delegation.release_principal(self.config,self.executor,'executor','release')
        if action == 'RECOVER_VERIFY':
            with control_store.connect() as db:
                db.execute("UPDATE operations SET state='UNKNOWN' WHERE id=?",(payload['operation_id'],))
        fresh_payload = dict(payload,expected_revision=creations.get(self.alice,'admin',ack['creation_id'])['revision'])
        fresh = self.intake(ack,action,fresh_payload,'fresh-approval')
        with control_store.connect() as db:
            row = db.execute('SELECT * FROM db_creation_requests WHERE id=?',(fresh['request_id'],)).fetchone()
            self.assertEqual(row['approval_state'],'VALID')
            self.assertIsNone(row['claim_id'])
        return fresh

    def stopped_executor_intake(self, action):
        ack, payload = self.approval_intake(action)
        old = self.intake(ack,action,payload,'old-approval')
        delegation.stop_principal(self.config,self.executor,'executor','stop-executor')
        current_payload = dict(payload,expected_revision=creations.get(self.alice,'admin',ack['creation_id'])['revision'])
        self.assert_intake_rejected(ack,action,current_payload,'during-stop','PRINCIPAL_STOPPED')
        before = self.snapshot()
        self.accounts['executor']['disabled'] = True
        self.save_accounts()
        self.assertEqual(self.intake(ack,action,payload,'old-approval'),old)
        self.assertEqual(self.snapshot(),before)
        self.accounts['executor']['disabled'] = False
        self.save_accounts()
        fresh = self.release_for_intake(ack,action,current_payload)
        self.assertNotEqual(fresh['request_id'],old['request_id'])
        before = self.snapshot()
        self.assertEqual(self.intake(ack,action,payload,'old-approval'),old)
        self.assertEqual(self.snapshot(),before)
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT approval_state FROM db_creation_requests WHERE id=?',
                (old['request_id'],)).fetchone()[0],'REVOKED')

    def lost_executor_intake(self, action, changes):
        ack, payload = self.approval_intake(action)
        before = self.snapshot()
        original = copy.deepcopy(self.accounts['executor'])
        self.accounts['executor'].update(changes)
        self.save_accounts()
        with self.assertRaises(delegation.AuthorityLost) as error:
            self.intake(ack,action,payload,'authority-lost')
        self.assertEqual(error.exception.target,'executor')
        after = self.snapshot()
        # Only the durable STOP request and gate may survive this failed intake.
        for table in before.keys() - {'db_creation_requests','db_creation_principal_gates'}:
            self.assertEqual(after[table],before[table],table)
        with control_store.connect() as db:
            added = [dict(r) for r in db.execute('SELECT * FROM db_creation_requests')
                if r['id'] not in {r[0] for r in before['db_creation_requests']}]
            self.assertEqual([r['action'] for r in added],['STOP_PRINCIPAL'])
            self.assertEqual(db.execute("SELECT state,reason_code FROM db_creation_principal_gates WHERE principal_id='executor'").fetchone()[:],
                ('STOPPED','AUTHORITY_LOST'))
        self.accounts['executor'] = original
        self.save_accounts()
        self.assert_intake_rejected(ack,action,payload,'restored-account','PRINCIPAL_STOPPED')
        self.release_for_intake(ack,action,payload)

    def test_approve_executor_stop_replay_and_explicit_release(self):
        self.stopped_executor_intake('APPROVE')

    def test_recover_verify_executor_stop_replay_and_explicit_release(self):
        self.stopped_executor_intake('RECOVER_VERIFY')

    def test_approve_executor_disabled_durably_stops_intake(self):
        self.lost_executor_intake('APPROVE',dict(disabled=True))

    def test_recover_verify_executor_disabled_durably_stops_intake(self):
        self.lost_executor_intake('RECOVER_VERIFY',dict(disabled=True))

    def test_approve_executor_global_role_loss_durably_stops_intake(self):
        # The account schema requires platform_admin=False for a non-admin role.
        self.lost_executor_intake('APPROVE',dict(role='bi_viewer',platform_admin=False))

    def test_recover_verify_executor_global_role_loss_durably_stops_intake(self):
        self.lost_executor_intake('RECOVER_VERIFY',dict(role='bi_viewer',platform_admin=False))

    def test_approve_executor_platform_flag_loss_durably_stops_intake(self):
        self.lost_executor_intake('APPROVE',dict(platform_admin=False))

    def test_recover_verify_executor_platform_flag_loss_durably_stops_intake(self):
        self.lost_executor_intake('RECOVER_VERIFY',dict(platform_admin=False))

    def test_atomic_registration_metadata_desired_grants_and_replay(self):
        payload = dict(display_name='研究',description='description\nline',initial_grants=[dict(principal_id='reader',maximum_role='bi_viewer')])
        ack = self.create(**payload)
        with control_store.connect() as db:
            root = db.execute('SELECT * FROM db_creations').fetchone()
            self.assertEqual((root['revision'],root['reservation_active']),(1,1))
            self.assertEqual(db.execute('SELECT count(*) FROM schemas').fetchone()[0],4)
            self.assertEqual(db.execute('SELECT display_name FROM databases').fetchone()[0],'研究')
            self.assertEqual(db.execute('SELECT maximum_role FROM grants').fetchone()[0],'bi_viewer')
            self.assertEqual(db.execute('SELECT maximum_role FROM effective_grants').fetchone()[0],'revoked')
            self.assertEqual(db.execute('SELECT state FROM warehouse_controls').fetchone()[0],'OPEN')
        before = self.snapshot()
        self.accounts['reader']['disabled'] = True
        self.save_accounts()
        self.probe.owner = False
        self.assertEqual(self.create(**payload),ack)
        self.assertEqual(self.snapshot(),before)
        with self.assertRaisesRegex(operations.OperationError,'IDEMPOTENCY_CONFLICT'):
            self.create(description='different')

    def test_registration_failure_rolls_back_every_child_and_ack(self):
        before = self.snapshot()
        with patch.object(grant_management,'_mutate',side_effect=RuntimeError('injected')):
            with self.assertRaises(RuntimeError):
                self.create(initial_grants=[dict(principal_id='reader',maximum_role='bi_viewer')])
        self.assertEqual(self.snapshot(),before)

    def test_parallel_distinct_keys_have_one_winner_without_loser_children(self):
        def run(index):
            try:
                return creations.create(self.alice,'admin',dict(sql_catalog='database_'+str(index)),str(index))[0]
            except operations.OperationError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run,range(2)))
        self.assertEqual(sum(isinstance(r,dict) for r in results),1)
        self.assertIn('CREATION_BUSY',results)
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM databases').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM schemas').fetchone()[0],4)

    def test_missing_foundation_owner_and_role_ceiling_leave_registry_empty(self):
        for foundation,owner,code in [(False,True,'FOUNDATION_NOT_READY'),(True,False,'OPERATOR_UNAVAILABLE')]:
            self.probe.foundation,self.probe.owner=foundation,owner
            before=self.snapshot()
            with self.assertRaisesRegex(operations.OperationError,code):
                self.create()
            self.assertEqual(self.snapshot(),before)
        self.probe.foundation=self.probe.owner=True
        with self.assertRaisesRegex(operations.OperationError,'INVALID_INITIAL_GRANTS'):
            self.create(initial_grants=[dict(principal_id='reader',maximum_role='admin')])
        self.assertEqual(self.snapshot()['databases'],[])

    def test_legacy_reservation_guard_and_existing_create_compatibility(self):
        ack=self.create()
        before=self.snapshot()
        with self.assertRaisesRegex(operations.OperationError,'CREATION_BUSY'):
            catalog_admin.create_database(self.alice,'admin',dict(sql_catalog='legacy',profile_id='local-polaris-1.8.0'),'legacy')
        self.assertEqual(self.snapshot(),before)
        detail,linked,observations=self.prepare(ack)
        bundle=database_generation.render(catalog_admin.export_spec('legacy-plan',spec(1)))
        with self.assertRaisesRegex(operations.OperationError,'CREATION_BUSY'):
            operations.create_plan('alice','legacy','legacy',bundle,observations,phase='bootstrap')

    def test_owner_only_mutations_and_missing_workers_are_persistently_blocked(self):
        ack=self.create()
        with self.assertRaisesRegex(operations.OperationError,'CREATION_OWNER_REQUIRED'):
            creations.mutate(self.bob,'admin',ack['creation_id'],'PREPARE_PLAN',dict(expected_revision=1,phase='bootstrap'),'bob')
        self.assertEqual(creations.get(self.bob,'admin',ack['creation_id'])['requester_id'],'alice')
        accepted=creations.mutate(self.alice,'admin',ack['creation_id'],'PREPARE_PLAN',dict(expected_revision=1,phase='bootstrap'),'prepare')
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT dispatch_state FROM db_creation_requests WHERE id=?',(accepted['request_id'],)).fetchone()[0],'BLOCKED')
        detail = creations.get(self.alice,'admin',ack['creation_id'])
        # BLOCKED is the saved delivery placeholder, not current availability.
        self.assertIsNone(detail['blocker'])
        self.assertEqual(detail['progress_code'],'PREPARATION_PENDING')
        self.assertEqual(detail['next_actions'],['withdraw'])
        with self.assertRaisesRegex(operations.OperationError,'STALE_PLAN'):
            creations.mutate(self.alice,'admin',ack['creation_id'],'PREPARE_PLAN',dict(expected_revision=2,phase='full'),'full')

    def test_empty_grants_no_implicit_self_and_principal_allowlist(self):
        self.accounts['reader'].update(display_name='Reader',disabled=True)
        self.accounts['bob']['enabled'] = False
        self.save_accounts()
        listing=creations.principals(self.alice,'admin')['principals']
        self.assertNotIn('reader',[r['principal_id'] for r in listing])
        self.assertNotIn('bob',[r['principal_id'] for r in listing])
        self.assertTrue(all(set(r)=={'principal_id','display_name','maximum_role'} for r in listing))
        ack=self.create()
        self.assertEqual(creations.get(self.alice,'admin',ack['creation_id'])['initial_grants'],[])

    def test_pending_grants_on_ready_database_block_new_admission(self):
        legacy=catalog_admin.create_database(self.alice,'admin',dict(sql_catalog='existing',profile_id='local-polaris-1.8.0'),'legacy')
        with control_store.connect() as db:
            db.execute("UPDATE databases SET state='READY'")
            db.execute('UPDATE catalog_bindings SET applied_revision=desired_revision')
        grant_management.mutate(self.alice,'admin',legacy['database_id'],
            dict(principal_id='reader',maximum_role='bi_viewer',expected_revision=0),'pending')
        before=self.snapshot()
        with self.assertRaisesRegex(operations.OperationError,'UNAPPLIED_CHANGES'):
            self.create()
        self.assertEqual(self.snapshot(),before)

    def test_active_reservation_does_not_block_grant_revocation(self):
        ack=self.create(initial_grants=[dict(principal_id='reader',maximum_role='bi_viewer')])
        database_id=creations.get(self.alice,'admin',ack['creation_id'])['database_id']
        revoked=grant_management.mutate(self.alice,'admin',database_id,
            dict(principal_id='reader',maximum_role='revoked',expected_revision=1),'revoke')
        self.assertEqual(revoked['state'],'AWAITING_APPLY')
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT maximum_role FROM grants').fetchone()[0],'revoked')

    def test_prepare_sequences_reserved_once_and_private_worker_binds_acceptance(self):
        ack=self.create()
        first=creations.mutate(self.alice,'admin',ack['creation_id'],'PREPARE_PLAN',dict(expected_revision=1,phase='bootstrap'),'first')
        self.assertEqual(creations.mutate(self.alice,'admin',ack['creation_id'],'PREPARE_PLAN',dict(expected_revision=1,phase='bootstrap'),'first'),first)
        second=creations.mutate(self.alice,'admin',ack['creation_id'],'REPLAN',dict(expected_revision=2,phase='bootstrap'),'second')
        with control_store.connect() as db:
            self.assertEqual([r[0] for r in db.execute('SELECT requested_sequence FROM db_creation_requests WHERE requested_sequence IS NOT NULL ORDER BY rowid')],[1,2])
        bundle=database_generation.render(catalog_admin.export_spec('reserved',spec(1)))
        refs=bundle['tofu']['bootstrap_secret_refs']+bundle['tofu']['deferred_secret_refs']
        observations=[dict(r,exists=False,uid=None,resource_version=None,immutable=None) for r in refs]
        def prepare(request):
            return creations._prepare_linked_plan(self.config,ack['creation_id'],bundle,observations,
                private_ref='sealed:reserved',public_summary={'code':'PLAN_READY'},
                preparation_request_id=request['request_id'])
        with self.assertRaisesRegex(operations.OperationError,'STALE_PLAN'):
            prepare(first)
        linked=prepare(second)
        self.assertEqual(prepare(second),linked)
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT sequence FROM db_creation_phase_plans').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT count(*) FROM operation_requests').fetchone()[0],1)


class CreationDispatchTests(CreationFixture):
    def test_frozen_environment_header_denies_replay_after_server_switch_before_saved(self):
        import database_creation_bridge as bridge
        payload = dict(sql_catalog='research',initial_grants=[dict(principal_id='reader',maximum_role='bi_viewer')])
        first = self.handler(creations.COLLECTION,payload,'frozen-environment')
        first.headers['X-Shirokuma-Environment'] = self.config.environment_id
        with patch.object(bridge,'dispatch',side_effect=self.committed_dispatch) as dispatch:
            ack,status = self.send(first)
            self.assertEqual(status,201)
            dispatch.assert_called_once()
            before = self.snapshot()
            creations.install_foundation(replace(self.config,environment_id='different-environment'),self.probe)
            replay = self.handler(creations.COLLECTION,payload,'frozen-environment')
            replay.headers['X-Shirokuma-Environment'] = self.config.environment_id
            dispatch.reset_mock()
            with patch.object(creations,'_saved',wraps=creations._saved) as saved,\
                    patch.object(catalog_admin,'_create_database') as register,\
                    patch.object(grant_management,'_mutate') as grants:
                error,status = self.send(replay)
                self.assertEqual((status,error['error']),(409,'ENVIRONMENT_MISMATCH'))
                saved.assert_not_called(); register.assert_not_called(); grants.assert_not_called()
                dispatch.assert_not_called()
            self.assertEqual(before,self.snapshot())
            self.assertNotIn(self.config.environment_id,json.dumps(error))
            self.assertNotIn('different-environment',json.dumps(error))
            creations.install_foundation(self.config,self.probe)
            self.assertEqual(self.send(replay),(ack,200))
            dispatch.assert_not_called()

    def test_matching_environment_create_cross_origin_and_stopped_executor_saved_approval(self):
        import database_creation_bridge as bridge
        def handler(path,payload,key,origin='workbench'):
            value = self.handler(path,payload,key,origin)
            value.headers['X-Shirokuma-Environment'] = self.config.environment_id
            return value
        payload = dict(sql_catalog='research')
        with patch.object(bridge,'dispatch',side_effect=self.committed_dispatch) as dispatch:
            ack,status = self.send(handler(creations.COLLECTION,payload,'matching'))
            self.assertEqual(status,201)
            for origin in ('native','workbench'):
                before = self.snapshot()
                self.assertEqual(self.send(handler(creations.COLLECTION,payload,'matching',origin)),(ack,200))
                self.assertEqual(before,self.snapshot())
            dispatch.assert_called_once_with(ack['request_id'],observe=False)
            detail,linked,observations = self.prepare(ack)
            plan = detail['plans'][-1]
            path = creations.COLLECTION+'/'+ack['creation_id']+'/phases/bootstrap/approve'
            payload = dict(expected_revision=detail['revision'],plan_id=plan['plan_id'],operation_id=plan['operation_id'],
                expected_operation_revision=plan['operation_revision'],delivery_digest=plan['delivery_digest'])
            dispatch.reset_mock()
            approved,status = self.send(handler(path,payload,'matching-approval'))
            self.assertEqual(status,202)
            dispatch.assert_called_once_with(approved['request_id'],observe=False)
            delegation.stop_principal(self.config,self.executor,'executor','header-executor-stop')
            before = self.snapshot()
            dispatch.reset_mock()
            self.assertEqual(self.send(handler(path,payload,'matching-approval','native')),(approved,202))
            self.assertEqual(before,self.snapshot())
            dispatch.assert_not_called()

    def test_environment_precondition_is_uniform_for_linked_mutation(self):
        import database_creation_bridge as bridge
        path,payload = self.route('PREPARE_PLAN')
        handler = self.handler(path,payload,'wrong-linked-environment')
        handler.headers['X-Shirokuma-Environment'] = 'other_environment-1'
        before = self.snapshot()
        with patch.object(creations,'_mutate') as mutate,patch.object(creations,'_saved') as saved,\
                patch.object(bridge,'dispatch') as dispatch:
            error,status = self.send(handler)
            self.assertEqual((status,error['error']),(409,'ENVIRONMENT_MISMATCH'))
            mutate.assert_not_called(); saved.assert_not_called(); dispatch.assert_not_called()
        self.assertEqual(before,self.snapshot())

    def test_invalid_environment_headers_deny_before_admission_and_body(self):
        import database_creation_bridge as bridge
        values = [('',),('one','one'),('one','two'),('one,two',),('one, two',),(' one',),('one ',),
            ('one\ttwo',),('one\ntwo',),('one\rtwo',),('one\x00two',),('日本語',),('one/two',),('a'*129,)]
        for headers in values:
            with self.subTest(headers=repr(headers)):
                handler = self.handler(creations.COLLECTION,dict(sql_catalog='research'),'invalid-header')
                for value in headers:
                    handler.headers['X-Shirokuma-Environment'] = value
                handler.database_body = Mock(side_effect=AssertionError('body must not be read'))
                before = self.snapshot()
                with patch.object(creations,'_saved') as saved,patch.object(creations,'create') as create,\
                        patch.object(bridge,'dispatch') as dispatch:
                    error,status = self.send(handler)
                    self.assertEqual((status,error['error']),(400,'INVALID_REQUEST'))
                    saved.assert_not_called(); create.assert_not_called(); dispatch.assert_not_called()
                    handler.database_body.assert_not_called()
                self.assertEqual(before,self.snapshot())

    def test_auth_role_and_origin_precede_environment_inspection(self):
        import database_creation_bridge as bridge
        cases = [('password',401,'UNAUTHENTICATED'),('disabled',403,'ADMIN_REQUIRED'),
            ('role',403,'ADMIN_REQUIRED'),('authority',403,'ADMIN_REQUIRED'),('origin',403,'INVALID_ORIGIN')]
        for case,status,code in cases:
            with self.subTest(case=case):
                handler = self.handler(creations.COLLECTION,dict(sql_catalog='research'),'auth-before-environment')
                handler.headers['X-Shirokuma-Environment'] = 'wrong-environment'
                if case == 'password':
                    handler.headers.replace_header('Authorization','Basic '+base64.b64encode(b'alice:wrong').decode())
                elif case == 'disabled':
                    self.accounts['alice']['disabled'] = True
                elif case == 'role':
                    handler.headers.replace_header('X-Shirokuma-Role','bi_viewer')
                elif case == 'authority':
                    self.accounts['alice']['platform_admin'] = False
                else:
                    handler.headers.replace_header('Origin','http://untrusted.example')
                self.save_accounts()
                try:
                    with patch.object(creations,'_check_environment',wraps=creations._check_environment) as check,\
                            patch.object(creations,'_saved',wraps=creations._saved) as saved,patch.object(bridge,'dispatch') as dispatch:
                        error,actual = self.send(handler)
                        self.assertEqual((actual,error['error']),(status,code))
                        check.assert_not_called(); dispatch.assert_not_called()
                        # Auth-loss STOP remains legitimate; only public saved
                        # ACK/business lookup must be absent at this boundary.
                        self.assertTrue(all(call.args[3].startswith('private/STOP_PRINCIPAL/') for call in saved.call_args_list))
                finally:
                    self.accounts['alice'].update(disabled=False,platform_admin=True)
                    self.save_accounts()
        with control_store.connect() as db:
            self.assertEqual(db.execute("SELECT state FROM db_creation_principal_gates WHERE principal_id='alice'").fetchone()[0],'STOPPED')
            self.assertEqual(db.execute('SELECT count(*) FROM db_creations').fetchone()[0],0)

    def test_environment_header_missing_enrollment_and_get_compatibility(self):
        handler = self.handler(creations.COLLECTION,dict(sql_catalog='research'),'not-configured')
        handler.headers['X-Shirokuma-Environment'] = self.config.environment_id
        with patch.object(creations,'_enrollment',None),patch.object(creations,'_saved') as saved:
            error,status = self.send(handler)
            self.assertEqual((status,error['error']),(503,'OPERATOR_UNAVAILABLE'))
            saved.assert_not_called()
        handler.headers.replace_header('X-Shirokuma-Environment','invalid, ignored for GET')
        self.assertEqual(self.send(handler,'GET')[1],200)

    def handler(self, path, payload, key, origin='workbench'):
        headers = Message()
        for name, value in self.headers().items():
            headers[name] = value
        host = '127.0.0.1:18888' if origin == 'workbench' else '127.0.0.1:18080'
        headers['Host'], headers['Origin'] = host, 'http://'+host
        headers['Idempotency-Key'] = key
        return SimpleNamespace(path=path, headers=headers, database_body=lambda:copy.deepcopy(payload),
                               respond=Mock())

    def send(self, handler, method='POST'):
        self.assertTrue(creations.dispatch(handler, method))
        return handler.respond.call_args.args

    def route(self, action):
        if action == 'CREATE':
            return creations.COLLECTION, dict(sql_catalog='research')
        if action == 'APPROVE':
            ack, payload = CreationTests.approval_intake(self, action)
            suffix = 'phases/bootstrap/approve'
        elif action == 'RECOVER_VERIFY':
            from tests.test_database_creation_auth import CreationAuthTests
            ack = self.create()
            approval, bootstrap, observations = self.approve(ack)
            permit = delegation.claim(self.config, self.executor, approval['request_id'], owner_id='owner')
            CreationAuthTests.execute(self, bootstrap, observations, permit)
            detail, _, _ = self.prepare(ack, 'full', bootstrap['operation_id'])
            full = next(p for p in detail['plans'] if p['phase'] == 'full')
            # Only model the uncertain state in the disposable ledger; recovery
            # intake and replay still use the real transaction and auth paths.
            with delegation.transaction() as db:
                plan_id = creations._attach_plan(db, self.config, ack['creation_id'], full['operation_id'],
                    private_ref='sealed:recovery', public_summary={'code':'PLAN_READY'},
                    purpose='VERIFY_RECOVERY', recovery_digest='temporary-recovery-digest')
                db.execute("UPDATE operations SET state='UNKNOWN' WHERE id=?", (full['operation_id'],))
            detail = creations.get(self.alice, 'admin', ack['creation_id'])
            plan = next(p for p in detail['plans'] if p['plan_id'] == plan_id)
            payload = dict(expected_revision=detail['revision'], plan_id=plan_id, operation_id=plan['operation_id'],
                expected_operation_revision=plan['operation_revision'], delivery_digest=plan['delivery_digest'],
                recovery_digest=plan['recovery_digest'])
            suffix = 'phases/full/recover-verify'
        else:
            ack = self.create()
            payload = dict(expected_revision=1)
            suffix = {'PREPARE_PLAN':'plans', 'REPLAN':'replan', 'RECONCILE':'reconcile',
                      'WITHDRAW':'withdraw'}[action]
            if action in ('PREPARE_PLAN', 'REPLAN'):
                payload['phase'] = 'bootstrap'
        return creations.COLLECTION+'/'+ack['creation_id']+'/'+suffix, payload

    def committed_dispatch(self, rid, **options):
        # A separate connection must observe the durable request before sending.
        with control_store.connect() as db:
            row = db.execute('SELECT ack FROM db_creation_requests WHERE id=?', (rid,)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(json.loads(row['ack'])['request_id'], rid)

    def assert_dispatch_replay(self, action, *, failed=False):
        import database_creation_bridge as bridge
        path, payload = self.route(action)
        def launch(rid, **options):
            self.committed_dispatch(rid, **options)
            if failed:
                raise ConnectionError('first dispatch acknowledgement lost')
        with patch.object(bridge, 'dispatch', side_effect=launch) as dispatch:
            first, status = self.send(self.handler(path, payload, 'same-key'))
            self.assertEqual(status, 201 if action == 'CREATE' else 202)
            self.assertEqual(set(first), {'request_id','creation_id','action','phase','accepted_revision'})
            dispatch.assert_called_once_with(first['request_id'], observe=action == 'RECONCILE')
            before = self.snapshot()
            dispatch.reset_mock()
            for origin in ('workbench', 'native', 'workbench'):
                replay, status = self.send(self.handler(path, payload, 'same-key', origin))
                self.assertEqual((replay, status), (first, 200 if action == 'CREATE' else 202))
                self.assertEqual(self.snapshot(), before)
                dispatch.assert_not_called()
            for get_path in (creations.COLLECTION, creations.COLLECTION+'/'+first['creation_id']):
                self.assertEqual(self.send(self.handler(get_path, {}, 'unused', 'native'), 'GET')[1], 200)
                dispatch.assert_not_called()

    def test_create_duplicate_dispatches_once_across_origins(self):
        self.assert_dispatch_replay('CREATE')

    def test_prepare_duplicate_dispatches_once_across_origins(self):
        self.assert_dispatch_replay('PREPARE_PLAN')

    def test_replan_duplicate_dispatches_once_across_origins(self):
        self.assert_dispatch_replay('REPLAN')

    def test_approve_duplicate_dispatches_once_across_origins(self):
        self.assert_dispatch_replay('APPROVE')

    def test_reconcile_duplicate_dispatches_once_across_origins(self):
        self.assert_dispatch_replay('RECONCILE')

    def test_recovery_duplicate_dispatches_once_across_origins(self):
        self.assert_dispatch_replay('RECOVER_VERIFY')

    def test_failed_create_dispatch_is_not_resent_by_duplicate(self):
        self.assert_dispatch_replay('CREATE', failed=True)

    def test_failed_mutation_dispatch_is_not_resent_by_duplicate(self):
        self.assert_dispatch_replay('PREPARE_PLAN', failed=True)

    def test_withdraw_and_get_never_dispatch(self):
        import database_creation_bridge as bridge
        path, payload = self.route('WITHDRAW')
        with patch.object(bridge, 'dispatch') as dispatch:
            first, status = self.send(self.handler(path, payload, 'withdraw'))
            self.assertEqual(status, 202)
            before = self.snapshot()
            for origin in ('workbench', 'native'):
                self.assertEqual(self.send(self.handler(path, payload, 'withdraw', origin)), (first, 202))
                self.assertEqual(self.snapshot(), before)
            self.assertEqual(self.send(self.handler(creations.COLLECTION, {}, 'unused'), 'GET')[1], 200)
            dispatch.assert_not_called()

    def assert_concurrent_dispatch(self, action):
        import database_creation_bridge as bridge
        path, payload = self.route(action)
        with control_store.connect() as db:
            count = db.execute('SELECT count(*) FROM db_creation_requests').fetchone()[0]
        barrier = threading.Barrier(2)
        def send(origin):
            handler = self.handler(path, payload, 'concurrent-key', origin)
            barrier.wait(timeout=10)
            return self.send(handler)
        with patch.object(bridge, 'dispatch', side_effect=self.committed_dispatch) as dispatch, \
                ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(send, ('workbench', 'native')))
        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual(sorted(status for _, status in results), [200,201] if action == 'CREATE' else [202,202])
        dispatch.assert_called_once_with(results[0][0]['request_id'], observe=False)
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM db_creation_requests').fetchone()[0], count+1)

    def test_concurrent_create_dispatches_once(self):
        self.assert_concurrent_dispatch('CREATE')

    def test_concurrent_mutation_dispatches_once(self):
        self.assert_concurrent_dispatch('PREPARE_PLAN')

    def assert_preparation_executor_loss(self, action):
        import database_creation_bridge as bridge
        path, payload = self.route(action)
        original_accounts = self.account_file.read_bytes()
        with patch.object(bridge, 'dispatch', side_effect=self.committed_dispatch) as dispatch:
            first, status = self.send(self.handler(path, payload, 'accepted'))
            self.assertEqual(status, 202)
            dispatch.assert_called_once_with(first['request_id'], observe=False)
            dispatch.reset_mock()
            before_loss = self.snapshot()
            del self.accounts['executor']
            self.save_accounts()
            fresh_payload = dict(payload, expected_revision=first['accepted_revision'])
            denied, status = self.send(self.handler(path, fresh_payload, 'executor-absent'))
            self.assertEqual((status, denied['error']), (403, 'ADMIN_REQUIRED'))
            dispatch.assert_not_called()
            stopped = self.snapshot()
            for table in before_loss.keys() - {'db_creation_requests','db_creation_principal_gates'}:
                self.assertEqual(stopped[table], before_loss[table], table)
            with control_store.connect() as db:
                added = [dict(r) for r in db.execute('SELECT * FROM db_creation_requests')
                    if r['id'] not in {r[0] for r in before_loss['db_creation_requests']}]
                self.assertEqual([r['action'] for r in added], ['STOP_PRINCIPAL'])
                self.assertEqual(tuple(db.execute("SELECT state,reason_code FROM db_creation_principal_gates WHERE principal_id='executor'").fetchone()),
                                 ('STOPPED', 'AUTHORITY_LOST'))
            # Saved ACK lookup precedes executor checks, including actual absence.
            self.assertEqual(self.send(self.handler(path, payload, 'accepted')), (first, 202))
            self.assertEqual(self.snapshot(), stopped)
            self.account_file.write_bytes(original_accounts)
            for fresh_action, suffix in [('PREPARE_PLAN','plans'), ('REPLAN','replan')]:
                fresh_path = creations.COLLECTION+'/'+first['creation_id']+'/'+suffix
                denied, status = self.send(self.handler(fresh_path, fresh_payload, 'restored-'+fresh_action))
                self.assertEqual((status, denied['error']), (403, 'PRINCIPAL_STOPPED'))
                self.assertEqual(self.snapshot(), stopped)
                dispatch.assert_not_called()
            self.assertEqual(self.send(self.handler(path, payload, 'accepted', 'native')), (first, 202))
            for read_path in (creations.COLLECTION, creations.COLLECTION+'/'+first['creation_id']):
                self.assertEqual(self.send(self.handler(read_path, {}, 'read'), 'GET')[1], 200)
            self.assertEqual(self.snapshot(), stopped)
            dispatch.assert_not_called()
            root_path = creations.COLLECTION+'/'+first['creation_id']
            observed, status = self.send(self.handler(root_path+'/reconcile',
                dict(expected_revision=first['accepted_revision']), 'observe-stopped'))
            self.assertEqual((status, observed['action']), (202, 'RECONCILE'))
            dispatch.assert_called_once_with(observed['request_id'], observe=True)
            dispatch.reset_mock()
            withdrawn, status = self.send(self.handler(root_path+'/withdraw',
                dict(expected_revision=observed['accepted_revision']), 'withdraw-stopped'))
            self.assertEqual((status, withdrawn['action']), (202, 'WITHDRAW'))
            dispatch.assert_not_called()
            with control_store.connect() as db:
                self.assertEqual(db.execute("SELECT state FROM db_creation_principal_gates WHERE principal_id='executor'").fetchone()[0], 'STOPPED')
                historical = tuple(db.execute('SELECT * FROM db_creation_requests WHERE id=?', (first['request_id'],)).fetchone())
                self.assertEqual(historical, next(r for r in before_loss['db_creation_requests'] if r[0] == first['request_id']))

    def test_prepare_executor_loss_restore_denies_fresh_admission(self):
        self.assert_preparation_executor_loss('PREPARE_PLAN')

    def test_replan_executor_loss_restore_denies_fresh_admission(self):
        self.assert_preparation_executor_loss('REPLAN')

    def test_http_auth_loss_replay_still_stops_without_dispatch(self):
        import database_creation_bridge as bridge
        path, payload = self.route('PREPARE_PLAN')
        with patch.object(bridge, 'dispatch', side_effect=self.committed_dispatch) as dispatch:
            first, status = self.send(self.handler(path, payload, 'revoke'))
            self.assertEqual(status, 202)
            with control_store.connect() as db:
                original = dict(db.execute('SELECT * FROM db_creation_requests WHERE id=?', (first['request_id'],)).fetchone())
            dispatch.reset_mock()
            self.accounts['alice']['platform_admin'] = False
            self.save_accounts()
            denied, status = self.send(self.handler(path, payload, 'revoke', 'native'))
            self.assertEqual((status, denied['error']), (403, 'ADMIN_REQUIRED'))
            dispatch.assert_not_called()
            self.accounts['alice']['platform_admin'] = True
            self.save_accounts()
            self.assertEqual(self.send(self.handler(path, payload, 'revoke', 'native')), (first, 202))
            dispatch.assert_not_called()
        with control_store.connect() as db:
            self.assertEqual(tuple(db.execute("SELECT state,reason_code FROM db_creation_principal_gates WHERE principal_id='alice'").fetchone()),
                             ('STOPPED', 'AUTHORITY_LOST'))
            row = db.execute('SELECT * FROM db_creation_requests WHERE id=?', (first['request_id'],)).fetchone()
            self.assertEqual(dict(row), original)


class PlanlessPreparationMutationTests(CreationFixture):
    def test_historical_claim_without_trusted_receipt_remains_hold(self):
        ack = self.create()
        request = creations.mutate(self.alice,'admin',ack['creation_id'],'PREPARE_PLAN',
            dict(expected_revision=1,phase='bootstrap'),'initial')
        delegation.claim_preparation(self.config,self.executor,request['request_id'],owner_id='historical-owner')
        before = self.snapshot()
        for _ in range(2):
            detail = creations.get(self.alice,'admin',ack['creation_id'])
            self.assertEqual(detail['progress_code'],'PREPARATION_PENDING')
            self.assertNotIn('replan',detail['next_actions'])
        with self.assertRaisesRegex(operations.OperationError,'RECONCILIATION_REQUIRED'):
            creations.mutate(self.alice,'admin',ack['creation_id'],'REPLAN',
                dict(expected_revision=2,phase='bootstrap'),'no-proof')
        self.assertEqual(before,self.snapshot())

    def test_unclaimed_superseding_still_reserves_next_sequence(self):
        ack = self.create()
        old = creations.mutate(self.alice,'admin',ack['creation_id'],'PREPARE_PLAN',
            dict(expected_revision=1,phase='bootstrap'),'initial')
        new = creations.mutate(self.alice,'admin',ack['creation_id'],'REPLAN',
            dict(expected_revision=2,phase='bootstrap'),'explicit')
        with control_store.connect() as db:
            rows = [dict(db.execute('SELECT * FROM db_creation_requests WHERE id=?',(r['request_id'],)).fetchone()) for r in (old,new)]
        self.assertEqual([r['requested_sequence'] for r in rows],[1,2])
        self.assertIsNone(rows[0]['claim_id'])
        self.assertEqual(json.loads(rows[0]['ack']),old)
