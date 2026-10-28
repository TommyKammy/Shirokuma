"""Dispatch and full-verify handoff tests without cluster or runtime effects."""
import copy
import json
import io
import re
import os
import stat
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
import uuid
from email.message import Message
from unittest.mock import Mock,patch

from tests.test_database_creations import CreationFixture
import control_store
import database_creation_auth as auth
import database_creation_bridge as bridge
import database_creation_worker as worker
import database_creations as creations
import warehouse_operations as core
from warehouse_operator import Operator,save_private


class WorkerTests(CreationFixture):
    def test_private_observe_does_not_rewrite_old_approval_and_public_completes(self):
        import database_creation_recovery as recovery
        ack = self.create()
        approval,public_plan,observations = self.approve(ack)
        auth.claim(self.config,self.executor,approval['request_id'],owner_id='owner')
        managed = self.managed()
        with control_store.connect() as db:
            db.execute("UPDATE db_creation_requests SET approval_state='REVOKED',stop_reason='OLD_STOP' WHERE id=?",(approval['request_id'],))
            old = dict(db.execute('SELECT * FROM db_creation_requests WHERE id=?',(approval['request_id'],)).fetchone())
            root = dict(db.execute('SELECT * FROM db_creations').fetchone())
            plan = dict(db.execute('SELECT * FROM db_creation_phase_plans').fetchone())
        operator = SimpleNamespace()
        def observation(*args):
            token = recovery._observation.set((managed,root['id'],plan['operation_id']))
            try:
                with auth.transaction() as db:
                    # Exercise the actual production auth completion hook used
                    # by canonical observer reconciliation.
                    auth.reconciled(db,plan['operation_id'])
                return dict(state='AWAITING_APPLY')
            finally:
                recovery._observation.reset(token)
        with patch.object(worker,'executor',return_value=self.executor),\
                patch.object(worker,'resolve',return_value=(old,root,plan,operator)),\
                patch.object(recovery,'observe',side_effect=observation):
            worker.dispatch(managed,approval['request_id'],observe=True)
        with control_store.connect() as db:
            self.assertEqual(dict(db.execute('SELECT * FROM db_creation_requests WHERE id=?',
                (approval['request_id'],)).fetchone()),old)
        current = creations.get(self.alice,'admin',ack['creation_id'])
        public = creations.mutate(self.alice,'admin',ack['creation_id'],'RECONCILE',
            dict(expected_revision=current['revision']),'public-observation')
        with control_store.connect() as db:
            request = dict(db.execute('SELECT * FROM db_creation_requests WHERE id=?',(public['request_id'],)).fetchone())
        with patch.object(worker,'executor',return_value=self.executor),\
                patch.object(worker,'resolve',return_value=(request,root,plan,operator)),\
                patch.object(recovery,'observe',side_effect=observation):
            worker.dispatch(managed,public['request_id'])
        with control_store.connect() as db:
            saved = db.execute('SELECT * FROM db_creation_requests WHERE id=?',(public['request_id'],)).fetchone()
            self.assertEqual((saved['dispatch_state'],saved['stop_reason']),('COMPLETE',None))
            self.assertEqual(db.execute('SELECT reservation_active FROM db_creations').fetchone()[0],1)

    def test_observer_return_without_durable_evidence_keeps_reconcile_blocked(self):
        import database_creation_recovery as recovery
        ack = self.create()
        approval,public_plan,observations = self.approve(ack)
        with control_store.connect() as db:
            db.execute("UPDATE operations SET state='UNKNOWN' WHERE id=?",(public_plan['operation_id'],))
            root = dict(db.execute('SELECT * FROM db_creations').fetchone())
            plan = dict(db.execute('SELECT * FROM db_creation_phase_plans').fetchone())
        current = creations.get(self.alice,'admin',ack['creation_id'])
        public = creations.mutate(self.alice,'admin',ack['creation_id'],'RECONCILE',
            dict(expected_revision=current['revision']),'no-proof')
        with control_store.connect() as db:
            request = dict(db.execute('SELECT * FROM db_creation_requests WHERE id=?',(public['request_id'],)).fetchone())
        with patch.object(worker,'executor',return_value=self.executor),\
                patch.object(worker,'resolve',return_value=(request,root,plan,SimpleNamespace())),\
                patch.object(recovery,'observe',return_value=dict(state='AWAITING_RECOVERY_APPROVAL',plan_id='absent')):
            worker.dispatch(self.managed(),public['request_id'])
        with control_store.connect() as db:
            self.assertEqual(dict(db.execute('SELECT * FROM db_creation_requests WHERE id=?',
                (public['request_id'],)).fetchone()),request)

    def test_exact_go_wire_fields_and_activation_only_journal_namespace(self):
        source = (Path(__file__).resolve().parents[1]/'internal/cli/managed_owner.go').read_text()
        def fields(name):
            body = re.search(r'type '+name+r' struct \{(.*?)\n\}',source,re.S).group(1)
            result = set(re.findall(r'json:"([^",]+)',body))
            for embedded in re.findall(r'^\s*(managed\w+)\s*$',body,re.M):
                result |= fields(embedded)
            return result
        self.assertEqual(fields('managedHello'),bridge.HELLO)
        self.assertEqual(fields('managedRecord'),bridge.RECORD)
        self.assertEqual(fields('managedAuthorization'),{'version','state','request_id','activation_id','owner_role',
            'owner_instance_id','leaf','intent_id','candidate_runtime_instance_id','profile_binding'})
        self.assertEqual(fields('managedDescriptor'),{'version','request_id','activation_id','owner_role','owner_instance_id',
            'old_runtime_instance_id','candidate_runtime_instance_id','profile_path','profile_binding','runtime_attestation',
            'original_operation_id','original_plan_id','candidate_id'})

    def test_authorization_rejection_has_nonzero_exit_and_empty_stdout(self):
        output,error = io.StringIO(),io.StringIO()
        with patch('sys.argv',['worker','authorize-leaf']),patch('sys.stdout',output),patch('sys.stderr',error),\
                patch.object(worker.runtime_profile,'configure_process'),patch.object(bridge,'configure',side_effect=ValueError('private detail')):
            self.assertEqual(worker.main(),1)
        self.assertEqual(output.getvalue(),'')
        self.assertEqual(error.getvalue(),'OPERATOR_UNAVAILABLE\n')

    def test_release_cli_is_separate_and_normal_worker_rejects_release_options(self):
        import database_creation_recovery as recovery
        rid = str(uuid.uuid4())
        for argv in (['worker','release-principal','--enrollment','/private/enrollment','--principal','alice','--key','explicit'],
                     ['worker','dispatch',rid,'--enrollment','/private/enrollment']):
            output = io.StringIO()
            with patch('sys.argv',argv),patch('sys.stdout',output),patch('sys.stderr',io.StringIO()),\
                    patch.object(recovery,'release_local',return_value=dict(request_id=rid)) as release,\
                    patch.object(worker.runtime_profile,'configure_process') as configure,patch.object(bridge,'configure') as managed:
                self.assertEqual(worker.main(),0 if argv[1]=='release-principal' else 1)
                configure.assert_not_called(); managed.assert_not_called()
                if argv[1]=='release-principal':
                    release.assert_called_once_with('/private/enrollment','alice','explicit')
                else:
                    release.assert_not_called()
    def test_post_dispatch_is_after_durable_commit_and_get_launches_zero(self):
        headers = Message()
        for key,value in self.headers().items():
            headers[key] = value
        headers['Idempotency-Key'] = 'same-public-key'
        handler = SimpleNamespace(path=creations.COLLECTION,headers=headers,
            database_body=lambda:dict(sql_catalog='research'),respond=Mock())
        captured = []
        def dispatch(rid,**kwargs):
            with control_store.connect() as db:
                row = db.execute('SELECT creation_id FROM db_creation_requests WHERE id=?',(rid,)).fetchone()
                self.assertIsNotNone(row)
                self.assertIsNotNone(db.execute('SELECT id FROM db_creations WHERE id=?',(row['creation_id'],)).fetchone())
            captured.append(rid)
            raise ConnectionError('private ACK lost')
        with patch.object(bridge,'dispatch',side_effect=dispatch) as launch:
            creations.dispatch(handler,'POST')
            self.assertEqual(handler.respond.call_args.args[1],201)
            original = handler.respond.call_args.args[0]
            creations.dispatch(handler,'POST')
            self.assertEqual(handler.respond.call_args.args[1],200)
            self.assertEqual(handler.respond.call_args.args[0],original)
            self.assertEqual(captured,[original['request_id']])
            launch.assert_called_once_with(original['request_id'],observe=False)
            launch.reset_mock()
            creations.dispatch(handler,'GET')
            self.assertEqual(handler.respond.call_args.args[1],200)
            launch.assert_not_called()

    def test_standalone_runtime_and_creation_are_503_before_registration(self):
        headers = Message()
        for key,value in self.headers().items():
            headers[key] = value
        headers['Idempotency-Key'] = 'standalone-key'
        handler = SimpleNamespace(path=creations.COLLECTION,headers=headers,
            database_body=lambda:dict(sql_catalog='research'),respond=Mock())
        with patch.object(creations,'_enrollment',None),patch.object(creations,'_foundation',None),patch.object(bridge,'_managed',None):
            creations.dispatch(handler,'POST')
            self.assertEqual(handler.respond.call_args.args[1],503)
            handler.path += '/runtime-instance'
            creations.dispatch(handler,'GET')
            self.assertEqual(handler.respond.call_args.args[1],503)
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM db_creations').fetchone()[0],0)

    def managed(self):
        return SimpleNamespace(require=Mock(),hello=dict(owner_role='workbench',owner_instance_id='owner'),
            config=SimpleNamespace(enrollment=self.config))

    def test_registered_create_resolves_durable_request_and_never_prepares(self):
        ack = self.create()
        managed = self.managed()
        with patch.object(worker,'executor',return_value=self.executor),patch.object(worker.preparation,'Preparation') as prepare:
            result = worker.dispatch(managed,ack['request_id'])
        self.assertEqual(result['state'],'REGISTERED')
        prepare.assert_not_called()
        with self.assertRaises(core.OperationError):
            worker.resolve(managed,str(uuid.uuid4()))

    def test_activation_identity_binds_original_plan_candidate_and_role(self):
        root,plan = dict(id='root'),dict(id='original')
        operator = SimpleNamespace(binding=dict(generation_id='candidate'))
        first = worker.activation_id(root,plan,operator,'workbench')
        self.assertRegex(first,'^[a-f0-9]{64}$')
        self.assertEqual(first,worker.activation_id(root,plan,operator,'workbench'))
        for other in ('native','another'):
            self.assertNotEqual(first,worker.activation_id(root,plan,operator,other))
        self.assertNotEqual(first,worker.activation_id(root,dict(id='recovery'),operator,'workbench'))

    def test_same_approval_claim_is_cas_and_other_owner_cannot_dispatch(self):
        ack = self.create()
        approval,plan,observations = self.approve(ack)
        first = auth.claim(self.config,self.executor,approval['request_id'],owner_id='first')
        self.assertEqual(first,auth.claim(self.config,self.executor,approval['request_id'],owner_id='first'))
        with self.assertRaisesRegex(core.OperationError,'CREATION_BUSY'):
            auth.claim(self.config,self.executor,approval['request_id'],owner_id='second')
        self.accounts['alice']['disabled'] = True
        self.save_accounts()
        with auth.delegated(first),self.assertRaises(auth.AuthorityLost):
            auth.assert_leaf(dict(id=plan['operation_id']))
        with control_store.connect() as db:
            self.assertEqual(db.execute("SELECT state FROM db_creation_principal_gates WHERE principal_id='alice'").fetchone()[0],'STOPPED')


class HandoffTests(unittest.TestCase):
    def test_private_bootstrap_observation_forward_has_no_mutation_intent(self):
        import database_creation_recovery as recovery
        import local_gitops
        operator = object.__new__(Operator)
        operator.phase = 'bootstrap'
        operator.config = dict(kubeconfig='/private/sealed-kubeconfig',managed_transport=dict(
            endpoints={'polaris-original':'http://127.0.0.1:18201'}))
        operator.kubectl = Mock()
        operator.leaf_intent = Mock(side_effect=AssertionError('no mutation intent'))
        child = Mock()
        child.poll.return_value = None
        child.stdout.readline.return_value = 'Forwarding from 127.0.0.1:18201 -> 8181\n'
        token = recovery._observation.set(('managed','root','original'))
        try:
            with patch.object(recovery,'guard_observation') as guard,\
                    patch('socket.socket'),patch('selectors.DefaultSelector') as selector,\
                    patch('threading.Thread'),patch('warehouse_operator.subprocess.Popen',return_value=child) as spawn:
                selector.return_value.__enter__.return_value.select.return_value = [True]
                operator.start_transports({'id':'original'})
                self.assertEqual(guard.call_count,2)
                argv = spawn.call_args.args[0]
                self.assertEqual(argv,['kubectl','--kubeconfig','/private/sealed-kubeconfig','--context',
                    local_gitops.CONTEXT,'-n','shirokuma-dev','port-forward','service/polaris-original',
                    '18201:8181','--address=127.0.0.1'])
                operator.close_transports()
                child.terminate.assert_called_once(); child.wait.assert_called_once()
                operator.leaf_intent.assert_not_called()
            with patch.object(recovery,'guard_observation',side_effect=RuntimeError('lost authority')),\
                    patch('warehouse_operator.subprocess.Popen') as spawn,self.assertRaises(RuntimeError):
                operator.start_transports({'id':'original'})
            spawn.assert_not_called()
        finally:
            recovery._observation.reset(token)

    def test_owned_forward_direct_handle_closes_listener_on_finally_and_owner_cancel(self):
        real_popen = subprocess.Popen
        for cancel in (False,True):
            with self.subTest(cancel=cancel):
                with socket.socket() as probe:
                    probe.bind(('127.0.0.1',0))
                    port = probe.getsockname()[1]
                operator = object.__new__(Operator)
                operator.config = dict(kubeconfig='/private/kubeconfig',managed_transport=dict(endpoints={
                    'polaris':'http://127.0.0.1:'+str(port)}))
                operator.kubectl = Mock(); operator.leaf_intent = Mock()
                children = []
                def spawn(argv,**kwargs):
                    self.assertEqual(argv[0],'kubectl')
                    self.assertIn(str(port)+':8181',argv)
                    self.assertNotIn('start_new_session',kwargs)
                    program = ('import socket,time; s=socket.socket(); s.bind(("127.0.0.1",'+str(port)+')); '
                        's.listen(); print("Forwarding from 127.0.0.1:'+str(port)+' -> 8181",flush=True); time.sleep(60)')
                    child = real_popen([sys.executable,'-u','-c',program],**kwargs)
                    children.append(child)
                    return child
                with patch('warehouse_operator.subprocess.Popen',side_effect=spawn):
                    try:
                        operator.start_transports(dict(id='op'))
                        child = children[0]
                        self.assertEqual(os.getpgid(child.pid),os.getpgrp())
                        with socket.create_connection(('127.0.0.1',port),timeout=1):
                            pass
                        if cancel:
                            child.terminate()  # the owner's known-child cancellation
                            child.wait(timeout=5)
                    finally:
                        operator.close_transports()
                self.assertIsNotNone(children[0].poll())
                with socket.socket() as probe:
                    probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
                    probe.bind(('127.0.0.1',port))

    def test_full_owned_readiness_is_complete_exact_instance_bound_and_cleared(self):
        for wrong in (False,True):
            operator = object.__new__(Operator)
            operator.phase = 'full'
            operator.config = dict(kubeconfig='/private/unit',managed_transport=dict(endpoints={
                'polaris-a':'http://127.0.0.1:18001','polaris-b':'http://127.0.0.1:18002'}))
            children = []
            def spawn(argv,**kwargs):
                child = Mock(); child.poll.return_value = None
                port = argv[-2].split(':')[0]
                child.stdout.readline.return_value = ('wrong' if wrong and children else
                    'Forwarding from 127.0.0.1:'+port+' -> 8181')
                children.append(child); return child
            with self.subTest(wrong=wrong),patch.object(Operator,'kubectl'),patch.object(Operator,'leaf_intent'), \
                    patch('socket.socket'),patch('threading.Thread'),patch('selectors.DefaultSelector') as selector, \
                    patch('warehouse_operator.subprocess.Popen',side_effect=spawn):
                selector.return_value.__enter__.return_value.select.return_value = [True]
                if wrong:
                    with self.assertRaises(RuntimeError): operator.start_transports(dict(id='unit'))
                    self.assertEqual(operator._ready_transports,{})
                    self.assertEqual(operator._transports,[])
                else:
                    operator.start_transports(dict(id='unit'))
                    operator.require_owned_transports()
                    self.assertEqual(set(operator._ready_transports),set(operator.config['managed_transport']['endpoints'].values()))
                    self.assertTrue(all(owner is operator and child in children for owner,child in operator._ready_transports.values()))
                    clone = copy.copy(operator)
                    with self.assertRaises(RuntimeError): clone.require_owned_transports()
                    operator.close_transports()
                    self.assertEqual(operator._ready_transports,{})
                    with self.assertRaises(RuntimeError): operator.require_owned_transports()
                for child in children: child.terminate.assert_called_once()

    def test_transport_conflict_or_stop_never_launches_or_adopts_a_listener(self):
        operator = object.__new__(Operator)
        operator.config = dict(kubeconfig='/private/kubeconfig',managed_transport=dict(endpoints={'polaris':'http://127.0.0.1:18181'}))
        operator.kubectl = Mock()
        operator.leaf_intent = Mock(side_effect=core.OperationError('PRINCIPAL_STOPPED',403))
        with patch('socket.socket') as sock,patch('warehouse_operator.subprocess.Popen') as launch:
            with self.assertRaisesRegex(RuntimeError,'no fallback'):
                operator.start_transports(dict(id='op'))
            launch.assert_not_called()
            sock.return_value.__enter__.return_value.bind.side_effect = OSError('already bound')
            with self.assertRaisesRegex(RuntimeError,'no fallback'):
                operator.start_transports(dict(id='op'))
            launch.assert_not_called()

    def test_bootstrap_verify_launches_zero_activations(self):
        operator = object.__new__(Operator)
        operator.phase = 'bootstrap'
        operator.receipt = dict(dispatches=[],provisioning={})
        operator.binding = {}
        for name in ('check_receipt','save','assert_intent','provision_databases','activate_runtimes','start_transports'):
            setattr(operator,name,Mock())
        operator.candidate_credentials = Mock(return_value={})
        operator.verification_configuration = Mock(return_value={})
        self.assertTrue(operator.verify(dict(id='bootstrap'))['verified'])
        operator.activate_runtimes.assert_not_called()

    def operator(self):
        operator = object.__new__(Operator)
        owner = lambda old,new:dict(owner_instance_id='owner',old_runtime_instance_id=old,candidate_runtime_instance_id=new)
        operator.config = dict(managed_handoff=dict(owners=dict(workbench=owner('old-w','new-w'),native=owner('old-n','new-n'))))
        operator.receipt = dict(runtime_activations={role:dict(state='UNSENT',owner=value,activation_id=role)
            for role,value in operator.config['managed_handoff']['owners'].items()})
        operator.save = Mock()
        operator.check_receipt = Mock()
        operator.leaf_intent = Mock()
        return operator

    def test_partial_activation_ack_loss_is_unknown_and_not_restarted(self):
        operator = self.operator()
        managed = SimpleNamespace(ipc=Mock(side_effect=[dict(state='ACK',runtime_instance_id='new-w',previous_runtime_instance_id='old-w'),ConnectionError()]))
        permit = SimpleNamespace(approval_request_id=str(uuid.uuid4()))
        with patch.object(bridge,'current',return_value=managed),patch.object(worker,'resolve',return_value=({}, {}, {}, None)),\
                patch.object(auth,'_permit',SimpleNamespace(get=lambda:permit)):
            with self.assertRaises(ConnectionError):
                operator.activate_runtimes(dict(id='operation'))
            self.assertEqual(operator.receipt['runtime_activations']['workbench']['state'],'ACK')
            self.assertEqual(operator.receipt['runtime_activations']['native']['state'],'INTENT')
            with self.assertRaisesRegex(RuntimeError,'UNKNOWN'):
                operator.activate_runtimes(dict(id='operation'))
        self.assertEqual(managed.ipc.call_count,2)


class CallbackDiagnosticsTests(unittest.TestCase):
    """Offline boundary injections only; no managed identity or durable ledgers."""

    def setUp(self):
        import tempfile
        from contextlib import ExitStack
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir='/private/tmp'))).resolve()
        self.root.chmod(0o700)
        self.log = self.root/'callback-failures.jsonl'
        self.profile = SimpleNamespace(state_dir=str(self.root))
        self.stack.enter_context(patch.dict(os.environ,{},clear=True))
        self.stack.enter_context(patch.object(worker.runtime_profile,'current',return_value=self.profile))
        # Any accidental SQL access is a test failure, never an offline DB write.
        self.stack.enter_context(patch.object(control_store,'connect',side_effect=AssertionError('SQL forbidden')))

    def records(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def invoke(self, command='resolve-activation', **patches):
        from contextlib import ExitStack
        out,err = io.StringIO(),io.StringIO()
        rid = patches.pop('request_id',None)
        with ExitStack() as stack:
            stack.enter_context(patch('sys.argv',['worker',command]+([rid] if rid else [])))
            stack.enter_context(patch('sys.stdin',SimpleNamespace(buffer=io.BytesIO(b'{}\n'))))
            stack.enter_context(patch('sys.stdout',out))
            stack.enter_context(patch('sys.stderr',err))
            stack.enter_context(patch.object(worker.runtime_profile,'configure_process',**patches.pop('runtime',{})))
            stack.enter_context(patch.object(bridge,'configure',**patches.pop('configure',dict(return_value=getattr(self,'boundary',object())))))
            for name,options in patches.items():
                target,attribute = (bridge,'decode') if name=='decode' else (worker,name)
                stack.enter_context(patch.object(target,attribute,**options))
            code = worker.main()
        return code,out.getvalue(),err.getvalue()

    def test_append_private_mode_preserves_prior_lines_single_write(self):
        prior = b'{"prior":true}\n'
        self.log.write_bytes(prior)
        self.log.chmod(0o600)
        with patch.object(worker.os,'write',wraps=os.write) as write:
            worker._log_callback_failure('resolve',ValueError('SECRET'))
        self.assertEqual(write.call_count,1)
        self.assertTrue(self.log.read_bytes().startswith(prior))
        self.assertEqual(self.log.stat().st_mode & 0o777,0o600)
        self.assertEqual(self.records()[1],dict(stage='resolve',exception='ValueError',code=None))

    def test_new_file_private_and_multiple_failures_append(self):
        worker._log_callback_failure('resolve',core.OperationError('STALE_PLAN'))
        worker._log_callback_failure('receipt',KeyError('SECRET'))
        self.assertEqual(self.log.stat().st_mode & 0o777,0o600)
        self.assertEqual([r['stage'] for r in self.records()],['resolve','receipt'])
        self.assertEqual(self.records()[0]['code'],'STALE_PLAN')

    def test_unknown_names_codes_and_input_secrets_never_logged(self):
        secret = 'SECRET_PATH_UUID_CREDENTIAL_BODY'
        arbitrary = type(secret,(Exception,),{})
        spoof = type('ValueError',(Exception,),{})
        subclass = type(secret,(core.OperationError,),{})
        for error in (arbitrary(secret,b'secret'),spoof(secret),core.OperationError(secret),ValueError(secret),subclass('STALE_PLAN')):
            worker._log_callback_failure(secret,error)
        raw = self.log.read_text()
        self.assertNotIn(secret,raw)
        self.assertEqual([r['exception'] for r in self.records()],['OTHER','OTHER','OperationError','ValueError','OTHER'])
        self.assertTrue(all(r['stage']=='OTHER' and r['code'] is None for r in self.records()))

    def test_only_real_operation_error_can_supply_allowlisted_code(self):
        error = ValueError('SECRET')
        error.code = 'STALE_PLAN'
        worker._log_callback_failure('resolve',error)
        worker._log_callback_failure('authority',core.OperationError('AUTHORIZATION_UNAVAILABLE'))
        self.assertEqual([r['code'] for r in self.records()],[None,'AUTHORIZATION_UNAVAILABLE'])
        worker._log_callback_failure('authority',auth.AuthorityLost(None,'SECRET'))
        self.assertEqual(self.records()[-1],dict(stage='authority',exception='AuthorityLost',code='ADMIN_REQUIRED'))

    def test_untrusted_or_missing_root_and_nonprivate_file_are_untouched(self):
        self.log.write_bytes(b'prior\n'); self.log.chmod(0o644)
        worker._log_callback_failure('resolve',ValueError())
        self.assertEqual(self.log.read_bytes(),b'prior\n')
        self.log.unlink()
        for root in (self.root/'absent',Path('relative')):
            with patch.object(worker.runtime_profile,'current',return_value=SimpleNamespace(state_dir=str(root))):
                worker._log_callback_failure('resolve',ValueError())
        self.root.chmod(0o755)
        worker._log_callback_failure('resolve',ValueError())
        self.assertEqual(list(self.root.iterdir()),[])

    def test_no_default_or_state_environment_fallback(self):
        with patch.object(worker.runtime_profile,'current',return_value=None),patch.dict(os.environ,SHIROKUMA_STATE_DIR=str(self.root)):
            worker._log_callback_failure('runtime-profile',ValueError())
        self.assertFalse(self.log.exists())

    def test_symlink_sink_does_not_modify_target(self):
        target = self.root/'sentinel'
        target.write_bytes(b'unchanged'); target.chmod(0o600)
        self.log.symlink_to(target)
        worker._log_callback_failure('resolve',ValueError('SECRET'))
        self.assertEqual(target.read_bytes(),b'unchanged')

    def test_configure_failure_uses_only_valid_explicit_profile(self):
        with patch.object(worker.runtime_profile,'current',return_value=None),\
                patch.dict(os.environ,SHIROKUMA_RUNTIME_PROFILE=str(self.root/'explicit.json')),\
                patch.object(worker.runtime_profile,'load',return_value=self.profile) as load:
            self.assertEqual(self.invoke(runtime=dict(side_effect=ValueError('SECRET'))),(1,'','OPERATOR_UNAVAILABLE\n'))
            load.assert_called_once_with(str(self.root/'explicit.json'))
        self.assertEqual(self.records(),[dict(stage='runtime-profile',exception='ValueError',code=None)])

    def test_invalid_explicit_profile_skips_diagnostics(self):
        with patch.object(worker.runtime_profile,'current',return_value=None),\
                patch.dict(os.environ,SHIROKUMA_RUNTIME_PROFILE=str(self.root/'SECRET')),\
                patch.object(worker.runtime_profile,'load',side_effect=ValueError('SECRET')):
            self.assertEqual(self.invoke(runtime=dict(side_effect=ValueError('SECRET'))),(1,'','OPERATOR_UNAVAILABLE\n'))
        self.assertFalse(self.log.exists())

    def test_main_configure_and_decode_stage_markers(self):
        for stage,options in [('configure',dict(configure=dict(side_effect=ValueError('SECRET')))),
                              ('decode',dict(decode=dict(side_effect=ValueError('SECRET'))))]:
            with self.subTest(stage=stage):
                self.assertEqual(self.invoke(**options),(1,'','OPERATOR_UNAVAILABLE\n'))
                self.assertEqual(self.records()[-1]['stage'],stage)

    def test_all_diagnostic_failures_preserve_public_failure(self):
        close = os.close
        def close_then_fail(fd):
            close(fd)
            raise RuntimeError('SECRET')
        for target,name in ((worker.runtime_profile,'current'),(worker.os,'open'),
                            (bridge,'directory'),(worker.json,'dumps'),
                            (worker.os,'fstat'),(worker.os,'write'),(worker.os,'close')):
            effect = close_then_fail if name=='close' else RuntimeError('SECRET')
            with self.subTest(operation=name),patch.object(target,name,side_effect=effect):
                self.assertEqual(self.invoke(configure=dict(side_effect=ValueError('SECRET'))),
                                 (1,'','OPERATOR_UNAVAILABLE\n'))

    def test_observe_private_failure_diagnostic_and_public_redaction(self):
        secret = 'private-observe-sentinel'
        for options,stage in [(dict(configure=dict(side_effect=ValueError(secret))),'configure'),
                (dict(dispatch=dict(side_effect=core.OperationError('STALE_PLAN')),request_id=str(uuid.uuid4())),'observe')]:
            with self.subTest(stage=stage):
                self.assertEqual(self.invoke('observe',**options),(1,'','OPERATOR_UNAVAILABLE\n'))
                record = self.records()[-1]
                self.assertEqual(record['stage'],stage)
                self.assertNotIn(secret,self.log.read_text())
                self.assertEqual(set(record),{'stage','exception','code'})
        self.assertEqual(stat.S_IMODE(self.log.stat().st_mode),0o600)
        # Main's final diagnostic cannot establish swallowed inner HTTP arrival;
        # only the separate auxiliary INTENT diagnostic marks that boundary.
        with patch.object(worker.os,'write',side_effect=OSError(secret)):
            self.assertEqual(self.invoke('observe',configure=dict(side_effect=ValueError(secret))),
                (1,'','OPERATOR_UNAVAILABLE\n'))

    def test_unapplied_changes_diagnostic_keeps_public_error_generic(self):
        self.assertEqual(self.invoke('observe',request_id=str(uuid.uuid4()),
            dispatch=dict(side_effect=core.OperationError('UNAPPLIED_CHANGES'))),
            (1,'','OPERATOR_UNAVAILABLE\n'))
        self.assertEqual(self.records(),[dict(stage='observe',exception='OperationError',code='UNAPPLIED_CHANGES')])
        self.assertEqual(stat.S_IMODE(self.log.stat().st_mode),0o600)

    def test_observe_success_retains_exact_stdout_and_no_diagnostic(self):
        rid = str(uuid.uuid4())
        result = dict(state='AWAITING_RECOVERY_APPROVAL',private='private-observe-sentinel')
        self.assertEqual(self.invoke('observe',request_id=rid,dispatch=dict(return_value=result)),
            (0,json.dumps(dict(version=1,state=result['state'],request_id=rid),separators=(',',':'))+'\n',''))
        self.assertFalse(self.log.exists())

    def test_success_and_other_commands_never_log(self):
        result = dict(version=1,state='AUTHORIZED')
        for command in ('resolve-activation','authorize-leaf'):
            self.assertEqual(self.invoke(command,activation=dict(return_value=result)),
                             (0,'{"version":1,"state":"AUTHORIZED"}\n',''))
        for command in ('dispatch','release-principal'):
            self.assertEqual(self.invoke(command,configure=dict(side_effect=ValueError('SECRET'))),
                             (1,'','OPERATOR_UNAVAILABLE\n'))
        with patch('sys.argv',['worker','--help']),patch('sys.stdout',io.StringIO()),self.assertRaises(SystemExit) as raised:
            worker.main()
        self.assertEqual(raised.exception.code,0)
        self.assertFalse(self.log.exists())

    def activation_boundaries(self):
        """Pure boundary doubles: these never construct bridge.Managed or an FD."""
        from contextlib import nullcontext
        owner = dict(owner_instance_id='owner',candidate_runtime_instance_id='candidate')
        value = dict(version=1,request_id='request',activation_id='activation',owner_role='workbench',owner_instance_id='owner')
        request = dict(id='request',action='APPROVE')
        operation = dict(id='operation',state='APPLIED')
        operator = SimpleNamespace(phase='full',config=dict(managed_handoff=dict(owners=dict(workbench=owner)),runtime_profile='SECRET'),
            check_receipt=Mock(),assert_intent=Mock(),leaf_intent=Mock(),
            receipt=dict(runtime_activations=dict(workbench=dict(activation_id='activation',owner=owner))))
        boundary = SimpleNamespace(hello=dict(owner_role='workbench',owner_instance_id='owner'),
            config=SimpleNamespace(value=dict(state_dir=str(self.root))))
        self.boundary = boundary
        for target,name,options in (
                (bridge,'configure',dict(return_value=boundary)),(bridge,'decode',dict(return_value=value)),
                (worker,'resolve',dict(return_value=(request,dict(actor='actor'),dict(operation_id='operation'),operator))),
                (worker,'activation_id',dict(return_value='activation')),(core,'inspect',dict(return_value=operation)),
                (worker,'authority',dict(side_effect=lambda *a,**k:nullcontext())),
                (worker,'descriptor',dict(return_value=dict(version=1))),
                (worker.runtime_profile,'private_json',dict(return_value=dict(version=1))),
                (worker.runtime_profile,'private_binding',dict(return_value={})),
                (bridge,'request_id',dict(side_effect=lambda v:v)),
                (worker,'activation_journal',dict(return_value=dict(stop=dict(state='INTENT',intent_id='intent'))))):
            self.stack.enter_context(patch.object(target,name,**options))
        return value,operator,operation

    def test_activation_checkpoints_and_original_exception_propagation(self):
        value,operator,operation = self.activation_boundaries()
        for target,name,stage in ((worker,'resolve','resolve'),(core,'inspect','receipt'),
                (operator,'check_receipt','receipt'),(worker,'authority','authority'),
                (operator,'assert_intent','assert-original'),(worker,'descriptor','descriptor'),
                (worker.runtime_profile,'private_json','sealed-descriptor')):
            error = ValueError('SECRET')
            with self.subTest(stage=stage),patch.object(target,name,side_effect=error):
                self.assertEqual(self.invoke(),(1,'','OPERATOR_UNAVAILABLE\n'))
                self.assertEqual(self.records()[-1]['stage'],stage)
                with self.assertRaises(ValueError) as raised:
                    worker.activation(self.boundary,value)
                self.assertIs(raised.exception,error)
        import database_creation_recovery as recovery
        worker.resolve.return_value[0]['action'] = 'RECOVER_VERIFY'
        with patch.object(recovery,'assert_original',side_effect=ValueError('SECRET')):
            self.assertEqual(self.invoke(),(1,'','OPERATOR_UNAVAILABLE\n'))
            self.assertEqual(self.records()[-1]['stage'],'assert-original')

    def test_authority_selection_and_leaf_checkpoints(self):
        value,operator,operation = self.activation_boundaries()
        operation['state'] = 'UNKNOWN'
        self.assertEqual(self.invoke(),(1,'','OPERATOR_UNAVAILABLE\n'))
        self.assertEqual(self.records()[-1]['stage'],'authority-select')
        operation['state'] = 'APPLIED'
        value.update(leaf='stop',intent_id='intent')
        for target,name,stage in ((worker,'activation_journal','activation-journal'),
                                  (operator,'leaf_intent','leaf-intent'),
                                  (worker.runtime_profile,'private_binding','descriptor')):
            with self.subTest(stage=stage),patch.object(target,name,side_effect=core.OperationError('STALE_PLAN')):
                self.assertEqual(self.invoke('authorize-leaf'),(1,'','OPERATOR_UNAVAILABLE\n'))
                self.assertEqual(self.records()[-1],dict(stage=stage,exception='OperationError',code='STALE_PLAN'))

    def test_activation_success_exact_stdout_and_no_diagnostic_writes(self):
        value,operator,operation = self.activation_boundaries()
        self.assertEqual(self.invoke(),(0,'{"version":1}\n',''))
        value.update(leaf='stop',intent_id='intent')
        expected = dict(version=1,state='AUTHORIZED',request_id='request',activation_id='activation',
            owner_role='workbench',owner_instance_id='owner',leaf='stop',intent_id='intent',
            candidate_runtime_instance_id='candidate',profile_binding={})
        self.assertEqual(self.invoke('authorize-leaf'),(0,json.dumps(expected,separators=(',',':'))+'\n',''))
        operator.leaf_intent.assert_called_once()
        self.assertEqual(list(self.root.iterdir()),[])


if __name__ == '__main__':
    unittest.main()
