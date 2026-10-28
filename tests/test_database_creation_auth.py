"""Typed delegation, current authority, immutable approval and core bypass tests."""
import copy
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from tests.test_database_creations import CreationFixture
import control_store
import database_creation_auth as delegation
import database_creations as creations
import warehouse_operations as operations


class CreationAuthTests(CreationFixture):
    def test_preparation_claim_latches_executor_stop_and_preserves_immutable_request(self):
        ack = self.create()
        request = creations.mutate(self.alice,'admin',ack['creation_id'],'PREPARE_PLAN',
            dict(expected_revision=ack['accepted_revision'],phase='bootstrap'),'prepare')
        def claim():
            return delegation.claim_preparation(self.config,self.executor,request['request_id'],owner_id='owner')
        fence = claim()
        with control_store.connect() as db:
            before = dict(db.execute('SELECT * FROM db_creation_requests WHERE id=?',(request['request_id'],)).fetchone())
        self.accounts['executor']['disabled'] = True
        self.save_accounts()
        with self.assertRaisesRegex(operations.OperationError,'ADMIN_REQUIRED'):
            claim()
        self.accounts['executor']['disabled'] = False
        self.save_accounts()
        with self.assertRaisesRegex(operations.OperationError,'PRINCIPAL_STOPPED'):
            claim()
        delegation.release_principal(self.config,self.executor,'executor','release')
        with self.assertRaisesRegex(operations.OperationError,'STALE_PLAN'):
            claim()
        with self.assertRaisesRegex(operations.OperationError,'STALE_PLAN'),delegation.transaction() as db:
            delegation.preparation_guard(db,fence)
        # An in-memory fence cannot rebind the persisted claim to a released epoch.
        with self.assertRaisesRegex(operations.OperationError,'STALE_PLAN'),delegation.transaction() as db:
            delegation.preparation_guard(db,replace(fence,executor_gate_revision=fence.executor_gate_revision+2))
        with control_store.connect() as db:
            after = dict(db.execute('SELECT * FROM db_creation_requests WHERE id=?',(request['request_id'],)).fetchone())
            for key in ('gate_revision','actor','payload','ack','requested_sequence','claim_id'):
                self.assertEqual(after[key],before[key])
            self.assertEqual(db.execute('SELECT count(*) FROM db_creation_phase_plans').fetchone()[0],0)

    def execute(self, plan, observations, permit):
        with delegation.delegated(permit):
            return operations.apply(plan['operation_id'],actor=permit.requester_id,
                expected_revision=plan['operation_revision'],delivery_digest=plan['delivery_digest'],
                authorize=lambda actor:True,observe_secrets=lambda p:copy.deepcopy(observations),
                execute_step=lambda name,p:dict(evidence_ref='ack:'+name),
                verify=lambda p:dict(verified=True,evidence_ref='ack:verify'))

    def full_approval(self, ack, bootstrap):
        detail,linked,observations=self.prepare(ack,'full',bootstrap['operation_id'])
        plan=next(p for p in detail['plans'] if p['phase']=='full')
        accepted=creations.mutate(self.alice,'admin',ack['creation_id'],'APPROVE',dict(expected_revision=detail['revision'],
            plan_id=plan['plan_id'],operation_id=plan['operation_id'],expected_operation_revision=plan['operation_revision'],
            delivery_digest=plan['delivery_digest']),'approve-full',phase='full')
        permit=delegation.claim(self.config,self.executor,accepted['request_id'],owner_id='canonical-owner')
        return plan,observations,permit

    def claimed(self):
        ack=self.create()
        approval,plan,observations=self.approve(ack)
        permit=delegation.claim(self.config,self.executor,approval['request_id'],owner_id='canonical-owner')
        return ack,approval,plan,observations,permit

    def test_executor_stop_release_requires_fresh_approval_and_durable_epoch(self):
        ack,approval,plan,observations,permit=self.claimed()
        with control_store.connect() as db:
            before=dict(db.execute('SELECT * FROM db_creation_requests WHERE id=?',(approval['request_id'],)).fetchone())
        stopped=delegation.stop_principal(self.config,self.executor,'executor','executor-stop')
        self.assertEqual(delegation.stop_principal(self.config,self.executor,'executor','executor-stop'),stopped)
        self.assertTrue(creations._admin_account(self.accounts['executor']))
        with delegation.delegated(permit):
            with self.assertRaises(operations.OperationError):
                delegation.assert_leaf(dict(id=plan['operation_id']))
            with self.assertRaises(operations.OperationError),delegation.transaction() as db:
                delegation.complete(db,plan['operation_id'],'APPLIED')
        with self.assertRaisesRegex(operations.OperationError,'PRINCIPAL_STOPPED'):
            delegation.claim(self.config,self.executor,approval['request_id'],owner_id='canonical-owner')
        delegation.release_principal(self.config,self.executor,'executor','executor-release')
        with self.assertRaisesRegex(operations.OperationError,'STALE_PLAN'):
            delegation.claim(self.config,self.executor,approval['request_id'],owner_id='canonical-owner')
        with delegation.delegated(permit),self.assertRaises(operations.OperationError):
            delegation.assert_leaf(dict(id=plan['operation_id']))
        # Changing only the in-memory epoch cannot rebind the durable claim.
        with delegation.delegated(replace(permit,executor_gate_revision=permit.executor_gate_revision+2)):
            with self.assertRaises(operations.OperationError),delegation.transaction() as db:
                delegation.guard(db,plan['operation_id'])
        with control_store.connect() as db:
            after=dict(db.execute('SELECT * FROM db_creation_requests WHERE id=?',(approval['request_id'],)).fetchone())
            self.assertEqual(after['approval_state'],'REVOKED')
            for key in ('gate_revision','actor','payload','ack','requested_sequence','claim_id','revision_cursor'):
                self.assertEqual(after[key],before[key])
            self.assertEqual(db.execute("SELECT state FROM db_creation_principal_gates WHERE principal_id='alice'").fetchone()[0],'ACTIVE')
        detail=creations.get(self.alice,'admin',ack['creation_id'])
        fresh=creations.mutate(self.alice,'admin',ack['creation_id'],'APPROVE',dict(expected_revision=detail['revision'],
            plan_id=plan['plan_id'],operation_id=plan['operation_id'],expected_operation_revision=plan['operation_revision'],
            delivery_digest=plan['delivery_digest']),'fresh-executor',phase='bootstrap')
        renewed=delegation.claim(self.config,self.executor,fresh['request_id'],owner_id='canonical-owner')
        self.assertGreater(renewed.executor_gate_revision,permit.executor_gate_revision)
        self.assertTrue(renewed.claim_id.startswith('apply:'+str(renewed.executor_gate_revision)+':'))
        self.assertEqual(self.execute(plan,observations,renewed)['state'],'PREPARED')

    def test_legacy_or_stale_epoch_claim_cannot_be_rediscovered_or_rebound(self):
        ack,approval,plan,observations,permit=self.claimed()
        # Emulate persisted claims from before epoch binding and from an older
        # executor epoch, keeping the canonical request, approval and ownership.
        for historical in ('legacy-claim','apply:0:historical'):
            with self.subTest(claim=historical):
                with control_store.connect() as db:
                    db.execute('UPDATE db_creation_requests SET claim_id=? WHERE id=?',(historical,approval['request_id']))
                before=CreationFixture.snapshot(self)
                with self.assertRaisesRegex(operations.OperationError,'STALE_PLAN'):
                    delegation.claim(self.config,self.executor,approval['request_id'],owner_id='canonical-owner')
                with delegation.delegated(replace(permit,claim_id=historical)):
                    with self.assertRaisesRegex(operations.OperationError,'STALE_PLAN'),delegation.transaction() as db:
                        delegation.guard(db,plan['operation_id'])
                self.assertEqual(CreationFixture.snapshot(self),before)

    def test_executor_stop_revokes_not_yet_claimed_approval(self):
        ack=self.create()
        approval,plan,observations=self.approve(ack)
        delegation.stop_principal(self.config,self.executor,'executor','pending-stop')
        with control_store.connect() as db:
            row=db.execute('SELECT * FROM db_creation_requests WHERE id=?',(approval['request_id'],)).fetchone()
            self.assertEqual(row['approval_state'],'REVOKED')
            self.assertIsNone(row['claim_id'])
            self.assertGreater(db.execute('SELECT revision FROM db_creations WHERE id=?',(ack['creation_id'],)).fetchone()[0],approval['accepted_revision'])
        with self.assertRaisesRegex(operations.OperationError,'PRINCIPAL_STOPPED'):
            delegation.claim(self.config,self.executor,approval['request_id'],owner_id='owner')

    def test_executor_account_loss_in_claim_stops_and_restoration_does_not_unblock(self):
        ack=self.create()
        approval,plan,observations=self.approve(ack)
        self.accounts['executor']['disabled']=True
        self.save_accounts()
        with self.assertRaisesRegex(delegation.AuthorityLost,'ADMIN_REQUIRED'):
            delegation.claim(self.config,self.executor,approval['request_id'],owner_id='owner')
        self.accounts['executor']['disabled']=False
        self.save_accounts()
        with self.assertRaisesRegex(operations.OperationError,'PRINCIPAL_STOPPED'):
            delegation.claim(self.config,self.executor,approval['request_id'],owner_id='owner')
        with control_store.connect() as db:
            self.assertEqual(db.execute("SELECT state,reason_code FROM db_creation_principal_gates WHERE principal_id='executor'").fetchone()[:],('STOPPED','AUTHORITY_LOST'))
            self.assertEqual(db.execute('SELECT approval_state FROM db_creation_requests WHERE id=?',(approval['request_id'],)).fetchone()[0],'REVOKED')

    def test_executor_account_loss_in_guard_rolls_back_and_durably_stops(self):
        ack,approval,plan,observations,permit=self.claimed()
        self.accounts['executor']['platform_admin']=False
        self.save_accounts()
        with delegation.delegated(permit),self.assertRaises(delegation.AuthorityLost):
            with delegation.transaction() as db:
                db.execute("UPDATE databases SET state='READY'")
                delegation.guard(db,plan['operation_id'])
        with control_store.connect() as db:
            self.assertNotEqual(db.execute('SELECT state FROM databases').fetchone()[0],'READY')
            self.assertEqual(db.execute("SELECT state FROM db_creation_principal_gates WHERE principal_id='executor'").fetchone()[0],'STOPPED')
            self.assertEqual(db.execute('SELECT approval_state FROM db_creation_requests WHERE id=?',(approval['request_id'],)).fetchone()[0],'REVOKED')
        self.accounts['executor']['platform_admin']=True
        self.save_accounts()
        with delegation.delegated(permit),self.assertRaises(operations.OperationError):
            delegation.assert_leaf(dict(id=plan['operation_id']))

    def test_executor_authority_loss_at_final_cas_rolls_back_ready_and_grants(self):
        ack=self.create(initial_grants=[dict(principal_id='reader',maximum_role='bi_viewer')])
        approval,bootstrap,observations=self.approve(ack)
        permit=delegation.claim(self.config,self.executor,approval['request_id'],owner_id='canonical-owner')
        self.assertEqual(self.execute(bootstrap,observations,permit)['state'],'PREPARED')
        full,observations,permit=self.full_approval(ack,bootstrap)
        original=operations._check_registry
        calls=[]
        def current_registry(db,spec):
            calls.append(1)
            if len(calls)==4:
                self.accounts['executor']['disabled']=True
                self.save_accounts()
            return original(db,spec)
        with patch.object(operations,'_check_registry',side_effect=current_registry):
            result=self.execute(full,observations,permit)
        self.assertEqual(result['state'],'UNKNOWN')
        with control_store.connect() as db:
            self.assertNotEqual(db.execute('SELECT state FROM databases').fetchone()[0],'READY')
            self.assertEqual(db.execute('SELECT applied_revision FROM catalog_bindings').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT maximum_role FROM effective_grants').fetchone()[0],'revoked')
            self.assertEqual(db.execute('SELECT reservation_active FROM db_creations').fetchone()[0],1)
            self.assertEqual(db.execute("SELECT state FROM db_creation_principal_gates WHERE principal_id='executor'").fetchone()[0],'STOPPED')
        self.accounts['executor']['disabled']=False
        self.save_accounts()
        with self.assertRaisesRegex(operations.OperationError,'RECONCILIATION_REQUIRED'):
            delegation.release_principal(self.config,self.executor,'executor','final-unknown-release')

    def test_invalid_executor_credentials_do_not_stop_other_work(self):
        ack,approval,plan,observations,permit=self.claimed()
        self.accounts['executor']['disabled']=True
        self.save_accounts()
        before=CreationFixture.snapshot(self)
        for untrusted in (self.bob,replace(self.executor,platform_admin=False),object()):
            with self.assertRaisesRegex(operations.OperationError,'ADMIN_REQUIRED'):
                delegation.claim(self.config,untrusted,approval['request_id'],owner_id='owner')
            with delegation.delegated(permit),self.assertRaisesRegex(operations.OperationError,'ADMIN_REQUIRED'):
                delegation.authorize_actor(untrusted,'alice')
        self.assertEqual(CreationFixture.snapshot(self),before)

    def test_executor_release_rejects_other_requester_running_and_unknown_effect(self):
        ack,approval,plan,observations,permit=self.claimed()
        calls=[]
        def step(name,current):
            calls.append(name)
            delegation.stop_principal(self.config,self.executor,'executor','effect-stop')
            with self.assertRaisesRegex(operations.OperationError,'RECONCILIATION_REQUIRED'):
                delegation.release_principal(self.config,self.executor,'executor','running-release')
            return dict(evidence_ref='ack:effect')
        with delegation.delegated(permit):
            result=operations.apply(plan['operation_id'],actor='alice',expected_revision=1,delivery_digest=plan['delivery_digest'],
                authorize=lambda actor:True,observe_secrets=lambda p:copy.deepcopy(observations),execute_step=step,
                verify=lambda p:self.fail('verification after executor stop'))
        self.assertEqual(calls,['publish_snapshot'])
        self.assertEqual(result['state'],'UNKNOWN')
        before=CreationFixture.snapshot(self)
        with self.assertRaisesRegex(operations.OperationError,'RECONCILIATION_REQUIRED'):
            delegation.release_principal(self.config,self.executor,'executor','unknown-release')
        self.assertEqual(CreationFixture.snapshot(self),before)

    def test_unrelated_principal_stop_leaves_executor_claim_usable(self):
        ack,approval,plan,observations,permit=self.claimed()
        delegation.stop_principal(self.config,self.executor,'bob','unrelated-stop')
        delegation.release_principal(self.config,self.executor,'bob','unrelated-release')
        self.assertEqual(delegation.claim(self.config,self.executor,approval['request_id'],owner_id='canonical-owner'),permit)
        self.assertEqual(self.execute(plan,observations,permit)['state'],'PREPARED')
        with control_store.connect() as db:
            completed=tuple(db.execute('SELECT * FROM db_creation_requests WHERE id=?',(approval['request_id'],)).fetchone())
        delegation.stop_principal(self.config,self.executor,'executor','completed-stop')
        with control_store.connect() as db:
            self.assertEqual(tuple(db.execute('SELECT * FROM db_creation_requests WHERE id=?',(approval['request_id'],)).fetchone()),completed)

    def test_independent_executor_claim_does_not_consume_and_same_claim_rediscovery(self):
        ack,approval,plan,observations,permit=self.claimed()
        self.assertEqual((permit.requester_id,permit.executor_id),('alice','executor'))
        with control_store.connect() as db:
            row=db.execute('SELECT * FROM db_creation_requests WHERE id=?',(approval['request_id'],)).fetchone()
            self.assertEqual((row['approval_state'],row['dispatch_state']),('VALID','CLAIMED'))
        self.assertEqual(delegation.claim(self.config,self.executor,approval['request_id'],owner_id='canonical-owner'),permit)
        with self.assertRaisesRegex(operations.OperationError,'ADMIN_REQUIRED'):
            delegation.claim(self.config,self.bob,approval['request_id'],owner_id='canonical-owner')
        with delegation.delegated(permit), delegation.transaction() as db:
            operations._state(db,plan['operation_id'],'PREPARING')
        self.assertEqual(delegation.claim(self.config,self.executor,approval['request_id'],owner_id='canonical-owner'),permit)

    def test_legacy_same_actor_and_missing_callback_cannot_execute_or_observe_p5(self):
        ack,approval,plan,observations,permit=self.claimed()
        calls=[]
        for authorize in (None,lambda actor:True):
            with self.assertRaisesRegex(operations.OperationError,'P5_PERMIT_REQUIRED'):
                operations.apply(plan['operation_id'],actor='alice',expected_revision=plan['operation_revision'],
                    delivery_digest=plan['delivery_digest'],authorize=authorize,observe_secrets=lambda p:calls.append('observe'),
                    execute_step=lambda s,p:calls.append(s),verify=lambda p:calls.append('verify'))
        with self.assertRaisesRegex(operations.OperationError,'P5_PERMIT_REQUIRED'):
            operations.reconcile_from_observer(plan['operation_id'],actor='alice',observer=lambda p:calls.append('observer'))
        with self.assertRaisesRegex(operations.OperationError,'P5_PERMIT_REQUIRED'):
            operations.request_action(plan['operation_id'],actor='alice',route='/api/v1/operations/'+plan['operation_id']+'/apply',
                idempotency_key='legacy',payload=dict(expected_revision=1,delivery_digest=plan['delivery_digest']))
        self.assertEqual(calls,[])

    def test_stop_revoke_release_requires_new_approval_and_old_claim_stays_invalid(self):
        ack,approval,plan,observations,permit=self.claimed()
        stopped=delegation.stop_principal(self.config,self.executor,'alice','stop')
        self.assertEqual(delegation.stop_principal(self.config,self.executor,'alice','stop'),stopped)
        with control_store.connect() as db:
            row=db.execute('SELECT approval_state FROM db_creation_requests WHERE id=?',(approval['request_id'],)).fetchone()
            self.assertEqual(row[0],'REVOKED')
        delegation.release_principal(self.config,self.executor,'alice','release')
        with delegation.delegated(permit),self.assertRaises(operations.OperationError),delegation.transaction() as db:
            delegation.guard(db,plan['operation_id'])
        detail=creations.get(self.alice,'admin',ack['creation_id'])
        new=creations.mutate(self.alice,'admin',ack['creation_id'],'APPROVE',dict(expected_revision=detail['revision'],
            plan_id=plan['plan_id'],operation_id=plan['operation_id'],expected_operation_revision=1,
            delivery_digest=plan['delivery_digest']),'fresh',phase='bootstrap')
        fresh=delegation.claim(self.config,self.executor,new['request_id'],owner_id='canonical-owner')
        self.assertGreater(fresh.principal_gate_revision,permit.principal_gate_revision)

    def test_role_switch_does_not_stop_but_current_authority_loss_durably_stops(self):
        ack,approval,plan,observations,permit=self.claimed()
        with self.assertRaises(PermissionError):
            creations.get(self.alice,'bi_viewer',ack['creation_id'])
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM db_creation_principal_gates').fetchone()[0],'ACTIVE')
        self.accounts['alice']['disabled']=True
        self.save_accounts()
        with delegation.delegated(permit),self.assertRaisesRegex(operations.OperationError,'ADMIN_REQUIRED'):
            with delegation.transaction() as db:
                # Regression: any tentative caller write must roll back before
                # the authority-loss STOP is committed in a separate transaction.
                db.execute("UPDATE databases SET state='READY'")
                delegation.guard(db,plan['operation_id'])
        with control_store.connect() as db:
            self.assertNotEqual(db.execute('SELECT state FROM databases').fetchone()[0],'READY')
            self.assertEqual(db.execute('SELECT state FROM db_creation_principal_gates').fetchone()[0],'STOPPED')
            self.assertEqual(db.execute('SELECT approval_state FROM db_creation_requests WHERE id=?',(approval['request_id'],)).fetchone()[0],'REVOKED')

    def test_replan_revokes_claim_and_immutable_ack_retains_accepted_revision(self):
        ack,approval,plan,observations,permit=self.claimed()
        accepted=creations.mutate(self.alice,'admin',ack['creation_id'],'REPLAN',dict(expected_revision=approval['accepted_revision'],phase='bootstrap'),'replan')
        with delegation.delegated(permit),self.assertRaises(operations.OperationError),delegation.transaction() as db:
            delegation.guard(db,plan['operation_id'])
        with control_store.connect() as db:
            self.assertEqual(json.loads(db.execute('SELECT ack FROM db_creation_requests WHERE id=?',(approval['request_id'],)).fetchone()[0]),approval)
        self.assertGreater(accepted['accepted_revision'],approval['accepted_revision'])

    def test_cursor_owned_progress_and_bootstrap_completion_only_consumes_at_prepared(self):
        ack,approval,plan,observations,permit=self.claimed()
        calls=[]
        def step(name,current):
            calls.append(name)
            with control_store.connect() as db:
                self.assertEqual(db.execute('SELECT approval_state FROM db_creation_requests WHERE id=?',(approval['request_id'],)).fetchone()[0],'VALID')
            return dict(evidence_ref='ack:'+name)
        with delegation.delegated(permit):
            result=operations.apply(plan['operation_id'],actor='alice',expected_revision=1,delivery_digest=plan['delivery_digest'],
                authorize=lambda actor:True,observe_secrets=lambda p:copy.deepcopy(observations),execute_step=step,
                verify=lambda p:dict(verified=True,evidence_ref='ack:verify'))
        self.assertEqual(result['state'],'PREPARED')
        self.assertNotIn('reconcile_trino',calls)
        with control_store.connect() as db:
            request=db.execute('SELECT * FROM db_creation_requests WHERE id=?',(approval['request_id'],)).fetchone()
            self.assertEqual(request['approval_state'],'CONSUMED')
            self.assertEqual(request['revision_cursor'],result['revision'])
            self.assertEqual(db.execute('SELECT reservation_active FROM db_creations').fetchone()[0],1)

    def failed_claim(self, step='publish_snapshot'):
        ack,approval,plan,observations,permit=self.claimed()
        def execute(name,current):
            if name == step:
                raise RuntimeError('effect acknowledgement unavailable')
            return dict(evidence_ref='ack:'+name)
        with delegation.delegated(permit):
            result=operations.apply(plan['operation_id'],actor='alice',expected_revision=plan['operation_revision'],
                delivery_digest=plan['delivery_digest'],authorize=lambda actor:True,
                observe_secrets=lambda p:copy.deepcopy(observations),execute_step=execute,
                verify=lambda p:self.fail('verify after failed step'))
        self.assertEqual(result['state'],'UNKNOWN')
        return ack,approval,plan,observations,permit

    def positive_reconcile(self, step, outcome):
        ack,approval,plan,observations,permit=self.failed_claim(step)
        evidence=operations.ReconciliationEvidence(step,outcome,'observe:canonical',
            'confirmed_not_dispatched' if outcome == 'NOT_EXECUTED' else 'backend_ack')
        with delegation.delegated(permit):
            resumed=operations.reconcile_from_observer(plan['operation_id'],actor='alice',observer=lambda p:evidence)
        self.assertEqual(resumed['state'],'AWAITING_APPLY')
        before=CreationFixture.snapshot(self)
        old=next(r for r in before['db_creation_requests'] if r[0] == approval['request_id'])
        with control_store.connect() as db:
            row=db.execute('SELECT * FROM db_creation_requests WHERE id=?',(approval['request_id'],)).fetchone()
            self.assertEqual((row['approval_state'],row['dispatch_state'],row['stop_reason']),
                ('REVOKED','COMPLETE','RECONCILED'))
            self.assertEqual(json.loads(row['ack']),approval)
        with self.assertRaisesRegex(operations.OperationError,'STALE_PLAN'):
            delegation.claim(self.config,self.executor,approval['request_id'],owner_id='canonical-owner')
        with delegation.delegated(permit),self.assertRaisesRegex(operations.OperationError,'STALE_PLAN'):
            operations.apply(plan['operation_id'],actor='alice',expected_revision=resumed['revision'],
                delivery_digest=plan['delivery_digest'],authorize=lambda actor:True,
                observe_secrets=lambda p:self.fail('old permit observation'),
                execute_step=lambda s,p:self.fail('old permit effect'),verify=lambda p:self.fail('old permit verify'))
        self.assertEqual(CreationFixture.snapshot(self),before)
        detail=creations.get(self.alice,'admin',ack['creation_id'])
        self.assertGreater(detail['revision'],approval['accepted_revision'])
        fresh=creations.mutate(self.alice,'admin',ack['creation_id'],'APPROVE',
            dict(expected_revision=detail['revision'],plan_id=plan['plan_id'],operation_id=plan['operation_id'],
                expected_operation_revision=resumed['revision'],delivery_digest=plan['delivery_digest']),
            'fresh-reconciled',phase='bootstrap')
        renewed=delegation.claim(self.config,self.executor,fresh['request_id'],owner_id='canonical-owner')
        self.assertNotEqual(renewed.claim_id,permit.claim_id)
        calls=[]
        with delegation.delegated(renewed):
            result=operations.apply(plan['operation_id'],actor='alice',expected_revision=resumed['revision'],
                delivery_digest=plan['delivery_digest'],authorize=lambda actor:True,
                observe_secrets=lambda p:copy.deepcopy(observations),
                execute_step=lambda s,p:(calls.append(s) or dict(evidence_ref='ack:'+s)),
                verify=lambda p:dict(verified=True,evidence_ref='ack:verify'))
        self.assertEqual((result['id'],result['state']),(plan['operation_id'],'PREPARED'))
        succeeded={s['step']:s['evidence_ref'] for s in resumed['steps'] if s['state'] == 'SUCCEEDED'}
        self.assertTrue(set(calls).isdisjoint(succeeded))
        for saved in result['steps']:
            if saved['step'] in succeeded:
                self.assertEqual(saved['evidence_ref'],succeeded[saved['step']])
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM operation_requests').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM db_creation_phase_plans').fetchone()[0],1)
            self.assertEqual(tuple(db.execute('SELECT * FROM db_creation_requests WHERE id=?',
                (approval['request_id'],)).fetchone()),old)

    def test_not_dispatched_reconcile_requires_fresh_approval(self):
        self.positive_reconcile('publish_snapshot','NOT_EXECUTED')

    def test_successful_reconcile_does_not_resend_confirmed_effect(self):
        self.positive_reconcile('publish_snapshot','SUCCEEDED')

    def test_reconcile_preserves_prior_successful_steps(self):
        self.positive_reconcile('reconcile_storage','NOT_EXECUTED')

    def test_reconcile_revocation_rolls_back_with_core_transition(self):
        ack,approval,plan,observations,permit=self.failed_claim()
        before=CreationFixture.snapshot(self)
        original=delegation.reconciled
        def fail_after_revocation(db, operation_id):
            original(db,operation_id)
            raise RuntimeError('rollback after revocation')
        evidence=operations.ReconciliationEvidence('publish_snapshot','NOT_EXECUTED',
            'observe:not-dispatched','confirmed_not_dispatched')
        with delegation.delegated(permit),patch.object(delegation,'reconciled',side_effect=fail_after_revocation):
            with self.assertRaisesRegex(RuntimeError,'rollback after revocation'):
                operations.reconcile_from_observer(plan['operation_id'],actor='alice',observer=lambda p:evidence)
        self.assertEqual(CreationFixture.snapshot(self),before)
        self.assertEqual(delegation.claim(self.config,self.executor,approval['request_id'],owner_id='canonical-owner'),permit)

    def test_unknown_observation_keeps_existing_approval_and_claim(self):
        ack,approval,plan,observations,permit=self.failed_claim()
        evidence=operations.ReconciliationEvidence('publish_snapshot','UNKNOWN','observe:uncertain','backend_ack')
        with delegation.delegated(permit):
            result=operations.reconcile_from_observer(plan['operation_id'],actor='alice',observer=lambda p:evidence)
        self.assertEqual(result['state'],'UNKNOWN')
        self.assertEqual(delegation.claim(self.config,self.executor,approval['request_id'],owner_id='canonical-owner'),permit)
        with control_store.connect() as db:
            row=db.execute('SELECT * FROM db_creation_requests WHERE id=?',(approval['request_id'],)).fetchone()
            self.assertEqual((row['approval_state'],row['dispatch_state']),('VALID','CLAIMED'))

    def test_stop_during_effect_keeps_receipt_unknown_and_prevents_next_leaf(self):
        ack,approval,plan,observations,permit=self.claimed()
        calls=[]
        def step(name,current):
            calls.append(name)
            delegation.stop_principal(self.config,self.executor,'alice','during-effect')
            return dict(evidence_ref='ack:known-effect')
        with delegation.delegated(permit):
            result=operations.apply(plan['operation_id'],actor='alice',expected_revision=1,delivery_digest=plan['delivery_digest'],
                authorize=lambda actor:True,observe_secrets=lambda p:copy.deepcopy(observations),execute_step=step,
                verify=lambda p:self.fail('verify after STOP'))
        self.assertEqual(calls,['publish_snapshot'])
        self.assertEqual(result['state'],'UNKNOWN')
        self.assertEqual((result['steps'][0]['state'],result['steps'][0]['evidence_ref']),('UNKNOWN','ack:known-effect'))
        with self.assertRaisesRegex(operations.OperationError,'RECONCILIATION_REQUIRED'):
            delegation.release_principal(self.config,self.executor,'alice','unsafe-release')

    def test_missing_reservation_and_forged_candidate_permit_are_rejected(self):
        ack,approval,plan,observations,permit=self.claimed()
        for forged in (replace(permit,candidate_binding='different'),replace(permit,executor_id='bob')):
            with delegation.delegated(forged),self.assertRaises(operations.OperationError),delegation.transaction() as db:
                delegation.guard(db,plan['operation_id'])
        with control_store.connect() as db:
            db.execute('UPDATE db_creations SET reservation_active=0')
        with delegation.delegated(permit),self.assertRaises(operations.OperationError),delegation.transaction() as db:
            delegation.guard(db,plan['operation_id'])

    def test_prepare_cannot_hide_unknown_in_a_new_operation(self):
        ack,approval,plan,observations,permit=self.claimed()
        with control_store.connect() as db:
            db.execute("UPDATE operations SET state='UNKNOWN' WHERE id=?", (plan['operation_id'],))
            db.execute("UPDATE operation_steps SET state='UNKNOWN' WHERE operation_id=? AND step='publish_snapshot'", (plan['operation_id'],))
        detail=creations.get(self.alice,'admin',ack['creation_id'])
        for action in ('PREPARE_PLAN','REPLAN'):
            with self.assertRaisesRegex(operations.OperationError,'RECONCILIATION_REQUIRED'):
                creations.mutate(self.alice,'admin',ack['creation_id'],action,
                    dict(expected_revision=detail['revision'],phase='bootstrap'),action)
        with self.assertRaisesRegex(operations.OperationError,'RECONCILIATION_REQUIRED'):
            creations.mutate(self.alice,'admin',ack['creation_id'],'APPROVE',dict(expected_revision=detail['revision'],
                plan_id=plan['plan_id'],operation_id=plan['operation_id'],expected_operation_revision=1,
                delivery_digest=plan['delivery_digest']),'approve-unknown',phase='bootstrap')
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM operation_requests').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM db_creation_phase_plans').fetchone()[0],1)

    def test_full_verified_cas_releases_reservation_promotes_grant_and_second_requester(self):
        ack=self.create(initial_grants=[dict(principal_id='reader',maximum_role='bi_viewer')])
        approval,bootstrap,observations=self.approve(ack)
        permit=delegation.claim(self.config,self.executor,approval['request_id'],owner_id='canonical-owner')
        self.assertEqual(self.execute(bootstrap,observations,permit)['state'],'PREPARED')
        full,observations,permit=self.full_approval(ack,bootstrap)
        self.assertEqual(self.execute(full,observations,permit)['state'],'APPLIED')
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM databases').fetchone()[0],'READY')
            self.assertEqual(db.execute('SELECT maximum_role FROM effective_grants').fetchone()[0],'bi_viewer')
            self.assertEqual(db.execute('SELECT reservation_active FROM db_creations').fetchone()[0],0)
        completed=creations.get(self.alice,'admin',ack['creation_id'])
        self.assertEqual((completed['blocker'],completed['next_actions']),(None,[]))
        with self.assertRaisesRegex(operations.OperationError,'REVISION_CONFLICT'):
            creations.mutate(self.alice,'admin',ack['creation_id'],'PREPARE_PLAN',dict(expected_revision=completed['revision'],phase='bootstrap'),'late')
        second=creations.create(self.bob,'admin',dict(sql_catalog='bob_database'),'bob-create')[0]
        self.assertEqual(creations.get(self.bob,'admin',second['creation_id'])['requester_id'],'bob')

    def test_authority_loss_at_final_cas_rolls_back_ready_grant_and_binding(self):
        ack=self.create(initial_grants=[dict(principal_id='reader',maximum_role='bi_viewer')])
        approval,bootstrap,observations=self.approve(ack)
        permit=delegation.claim(self.config,self.executor,approval['request_id'],owner_id='canonical-owner')
        self.execute(bootstrap,observations,permit)
        full,observations,permit=self.full_approval(ack,bootstrap)
        original=operations._check_registry
        calls=[]
        def current_registry(db, spec):
            calls.append(1)
            if len(calls)==4:
                self.accounts['alice']['disabled']=True
                self.save_accounts()
            return original(db,spec)
        with patch.object(operations,'_check_registry',side_effect=current_registry):
            result=self.execute(full,observations,permit)
        self.assertEqual(result['state'],'UNKNOWN')
        with control_store.connect() as db:
            self.assertNotEqual(db.execute('SELECT state FROM databases').fetchone()[0],'READY')
            self.assertEqual(db.execute('SELECT applied_revision FROM catalog_bindings').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT maximum_role FROM effective_grants').fetchone()[0],'revoked')
            self.assertEqual(db.execute('SELECT reservation_active FROM db_creations').fetchone()[0],1)
            self.assertEqual(db.execute("SELECT state FROM db_creation_principal_gates WHERE principal_id='alice'").fetchone()[0],'STOPPED')

    def snapshot(self):
        with control_store.connect() as db:
            return {'db_creation_requests':[tuple(r) for r in db.execute('SELECT * FROM db_creation_requests ORDER BY rowid')]}


class PreflightTerminalAuthorityTests(CreationFixture):
    def test_claimed_preparation_cannot_be_superseded_by_direct_mutation(self):
        ack = self.create()
        request = creations.mutate(self.alice,'admin',ack['creation_id'],'PREPARE_PLAN',
            dict(expected_revision=1,phase='bootstrap'),'initial')
        fence = delegation.claim_preparation(self.config,self.executor,request['request_id'],owner_id='owner')
        before = self.snapshot()
        for action in ('PREPARE_PLAN','REPLAN'):
            with self.subTest(action=action),self.assertRaisesRegex(operations.OperationError,'RECONCILIATION_REQUIRED'):
                creations.mutate(self.alice,'admin',ack['creation_id'],action,
                    dict(expected_revision=2,phase='bootstrap'),'bypass-'+action)
        self.assertEqual(self.snapshot(),before)
        with delegation.transaction() as db:
            delegation.preparation_guard(db,fence)

    def test_executor_auth_file_revocation_blocks_terminal_and_persists_stop(self):
        ack = self.create()
        request = creations.mutate(self.alice,'admin',ack['creation_id'],'PREPARE_PLAN',
            dict(expected_revision=1,phase='bootstrap'),'initial')
        fence = delegation.claim_preparation(self.config,self.executor,request['request_id'],owner_id='owner')
        self.accounts['executor']['disabled'] = True
        self.save_accounts()
        with self.assertRaises(delegation.AuthorityLost):
            delegation.complete_preflight_failure(fence,operations.OperationError('UNAPPLIED_CHANGES'),self.directory/'absent')
        with control_store.connect() as db:
            self.assertEqual(db.execute("SELECT state FROM db_creation_principal_gates WHERE principal_id='executor'").fetchone()[0],'STOPPED')
            self.assertEqual(db.execute('SELECT dispatch_state FROM db_creation_requests WHERE id=?',(request['request_id'],)).fetchone()[0],'CLAIMED')
        self.assertFalse(delegation._preflight_receipt_path(request['request_id']).exists())
