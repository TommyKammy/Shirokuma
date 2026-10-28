"""Limited recovery uses real canonical approvals, claims, gates and reconcile."""
import copy
import base64
import json
import time
import io
from contextlib import redirect_stdout
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import Mock,patch

from tests.test_database_creations import CreationFixture
import control_store
import database_creation_auth as auth
import database_creation_bridge as bridge
import database_creation_recovery as recovery
import database_creations as creations
import runtime_profile
import request_auth
import warehouse_operations as core
from warehouse_operator import Operator,canonical,digest,save_private
from tests.test_database_creation_bridge import BaseParentFixture, BridgeFixture
from tests import test_database_creation_preparation as preparation_tests


# Test-owned native processes only; never invoke kubectl or an enrolled server.
_NATIVE_START = Operator.start_transports
_NATIVE_CLOSE = Operator.close_transports
_NATIVE_HTTP = Operator.http
import urllib.request as _urllib_request
_NATIVE_OPENER = _urllib_request.build_opener
import subprocess as _subprocess
_NATIVE_POPEN = _subprocess.Popen


def fixture_owned_transports(operator, operation):
    """Exercise production ownership/readiness using deterministic child handles."""
    import selectors
    canonical_kubectl = Operator.kubectl
    def service_get(instance,*args,**kwargs):
        if (not kwargs and len(args)==7 and args[:2]==('get','service')
                and args[2] in operator.config['managed_transport']['endpoints']
                and args[3:]==('-n','shirokuma-dev','-o','json')):
            return '{}'
        return canonical_kubectl(instance,*args,**kwargs)
    def spawn(argv, **kwargs):
        child = Mock()
        child.poll.return_value = None
        port = argv[argv.index('--address=127.0.0.1')-1].split(':')[0]
        child.stdout.readline.return_value = 'Forwarding from 127.0.0.1:'+port+' -> 8181\n'
        return child
    with patch.object(Operator,'kubectl',service_get),patch('socket.socket'), \
            patch('warehouse_operator.subprocess.Popen',side_effect=spawn), \
            patch('selectors.DefaultSelector') as selector,patch('threading.Thread'):
        selector.return_value.__enter__.return_value.select.return_value = [True]
        _NATIVE_START(operator,operation)


@contextmanager
def token_forward_servers(case, endpoints, expiry, *, mode='valid'):
    """Only a fresh test HTTP server can occupy these fixture loopback ports."""
    import subprocess,sys
    from urllib.parse import urlsplit
    canonical_kubectl = Operator.kubectl
    def service_get(instance,*args,**kwargs):
        if (not kwargs and len(args)==7 and args[:2]==('get','service') and args[2] in endpoints
                and args[3:]==('-n','shirokuma-dev','-o','json')):
            return '{}'
        return canonical_kubectl(instance,*args,**kwargs)
    children,counts,borrowed_children = [],{},[]
    program = r'''import sys,json,base64
from pathlib import Path
from http.server import HTTPServer,BaseHTTPRequestHandler
port=int(sys.argv[1]); counter=Path(sys.argv[2]); expiry=float(sys.argv[3]); mode=sys.argv[4]
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def do_POST(self):
  self.rfile.read(int(self.headers.get('Content-Length','0')))
  counter.write_text(str((int(counter.read_text()) if counter.exists() else 0)+1))
  if mode=='lost': self.close_connection=True; return
  payload=base64.urlsafe_b64encode(json.dumps({'exp':expiry}).encode()).decode().rstrip('=')
  response={} if mode=='malformed' else {'access_token':'e30.'+payload+'.test'}
  body=json.dumps(response).encode(); self.send_response(200); self.send_header('Content-Length',str(len(body)))
  self.end_headers(); self.wfile.write(body)
server=HTTPServer(('127.0.0.1',port),Handler)
print('Forwarding from 127.0.0.1:'+str(port)+' -> 8181',flush=True)
server.serve_forever()
'''
    def start(port, **kwargs):
        count = case.directory/('token-count-'+str(port))
        counts[port] = count
        child = _NATIVE_POPEN([sys.executable,'-B','-u','-c',program,str(port),str(count),str(expiry),mode],**kwargs)
        children.append(child)
        return child
    def spawn(argv, **kwargs):
        case.assertEqual(argv[0],'kubectl')
        port = int(argv[argv.index('--address=127.0.0.1')-1].split(':')[0])
        service = argv[argv.index('port-forward')+1].removeprefix('service/')
        case.assertIn(service,endpoints)
        case.assertEqual(port,urlsplit(endpoints[service]).port)
        return start(port,**kwargs)
    def borrowed():
        for endpoint in endpoints.values():
            port = urlsplit(endpoint).port
            child = start(port,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            borrowed_children.append(child)
            line = child.stdout.readline().strip()
            if not line:
                case.fail('Test-owned token server failed: '+child.stderr.read())
            case.assertEqual(line,'Forwarding from 127.0.0.1:'+str(port)+' -> 8181')
    def stop():
        for child in children:
            if child.poll() is None: child.terminate(); child.wait(timeout=5)
            if child in borrowed_children and child.stdout is not None: child.stdout.close()
            if child.stderr is not None: child.stderr.close()
    try:
        with patch('warehouse_operator.subprocess.Popen',side_effect=spawn),patch.object(Operator,'kubectl',service_get), \
                patch.object(Operator,'start_transports',_NATIVE_START),patch.object(Operator,'close_transports',_NATIVE_CLOSE):
            yield SimpleNamespace(borrowed=borrowed,stop=stop,
                count=lambda:sum(int(p.read_text()) for p in counts.values() if p.exists()))
    finally:
        stop()


def native_token_http(operator, request, **kwargs):
    if (request.get_method() == 'POST' and operator.http_identity(request) in operator.read_auth_identities()
            and operator.http_identity(request) in operator.receipt.get('verify_http',{})):
        with patch.object(_urllib_request,'build_opener',_NATIVE_OPENER):
            return _NATIVE_HTTP(operator,request,**kwargs)
    # All non-token requests retain the canonical fixture's existing opener.
    return _NATIVE_HTTP(operator,request,**kwargs)


# Exact pre-refresh reader functions, deliberately independent of new classify.
OLD_READER_SOURCE = '''
def classify(operator):
    receipt = operator.receipt
    declaration = receipt.get('verify_declaration')
    if not declaration or declaration.get('state') != 'SEALED' or declaration['binding'] != operator.binding:
        raise core.OperationError('RECONCILIATION_REQUIRED')
    leaves = {}
    if set(receipt.get('runtime_activations',{})) != {'workbench','native'}:
        raise core.OperationError('RECONCILIATION_REQUIRED')
    for role,saved in receipt['runtime_activations'].items():
        leaves['activation/'+role] = 'COMPLETE' if saved['state']=='ACK' else 'UNSENT' if saved['state']=='UNSENT' else 'UNKNOWN'
        if saved['state'] == 'PARTIAL':
            leaves['activation/'+role] = 'PARTIAL'
        for name in ('stop','start','readiness'):
            state = saved.get('leaves',{}).get(name)
            leaves['activation/'+role+'/'+name] = 'COMPLETE' if saved['state']=='ACK' or state=='ACK' else 'UNSENT' if state=='UNSENT' else 'UNKNOWN'
    for key,state in declaration['sql'].items():
        saved = receipt['sql'].get(key)
        leaves['sql/'+key] = ('COMPLETE' if saved and saved.get('complete') and saved.get('backend_id')
            else 'GET_ONLY' if saved and saved.get('next_uri') else 'UNKNOWN' if saved
            else 'UNSENT' if state=='UNSENT' else 'UNKNOWN')
    for key,saved in receipt.get('verify_http',{}).items():
        leaves['http/'+key] = 'COMPLETE' if saved['state']=='ACK' else 'UNSENT' if saved['state']=='UNSENT' else 'UNKNOWN'
    return leaves

def projection_ready(db, root, plan, original):
    """Read-only local proof of a current, executable frozen recovery."""
    try:
        if (plan['phase'] != 'full' or original['phase'] != 'full'
                or plan['purpose'] != 'VERIFY_RECOVERY' or original['purpose'] != 'APPLY'
                or plan['operation_id'] != original['operation_id']):
            return False
        op = original_unknown(db, original['operation_id'])
        frozen = runtime_profile.private_json(plan['private_ref'])
        journal = control_store.root().resolve()/'warehouse-operator-receipts'/(original['operation_id']+'.json')
        receipt = runtime_profile.private_json(journal)
        declaration = receipt['verify_declaration']
        from types import SimpleNamespace
        leaves = classify(SimpleNamespace(receipt=receipt, binding=receipt['binding']))
        approval = db.execute('SELECT * FROM db_creation_requests WHERE id=?',
            (declaration['original_request_id'],)).fetchone()
        matching = (digest(canonical(frozen)) == plan['recovery_digest']
            and frozen['purpose'] == 'VERIFY_RECOVERY' and frozen['version'] == 1
            and frozen['creation_id'] == root['id'] and frozen['operation_id'] == original['operation_id']
            and frozen['original_plan_id'] == original['id'] and frozen['canonical_attempt'] == op['attempt_id']
            and frozen['candidate'] == receipt['binding']
            and frozen['candidate']['generation_id'] == original['generation_id']
            and frozen['candidate']['delivery_digest'] == original['delivery_digest']
            and plan['generation_id'] == original['generation_id'] and plan['delivery_digest'] == original['delivery_digest']
            and frozen['journal_path'] == str(journal) and frozen['journal_digest'] == digest(canonical(receipt))
            and declaration['state'] == 'SEALED' and declaration['original_plan_id'] == original['id']
            and declaration['original_claim_id'] == frozen['original_attempt']
            and approval is not None and approval['plan_id'] == original['id']
            and approval['claim_id'] == frozen['original_attempt']
            and frozen['executable'] is True and frozen['leaves'] == leaves and bool(leaves)
            and 'UNKNOWN' not in leaves.values())
        if not matching:
            return False
        # The parent receipt can lag an owner after a lost response. Reuse the
        # release verifier's local seal, descriptor and actual owner-journal
        # checks; no observer or health/SQL request is involved.
        config = getattr(creations._foundation, 'config', None)
        if config is None or config.enrollment != creations.enrollment():
            return False
        snapshot = release_snapshot(db,config,original['operation_id'])
        return (snapshot['recovery_plan']['id'] == plan['id']
            and snapshot['original_plan']['id'] == original['id']
            and frozen.get('owner_journals') == {role:owner['journal_digest'] for role,owner in snapshot['owners'].items()})
    except (OSError, ValueError, KeyError, TypeError, core.OperationError):
        return False
'''

def old_recovery_reader():
    from types import FunctionType
    namespace = dict(vars(recovery))
    exec(compile(OLD_READER_SOURCE,"<pre-refresh-reader>","exec"),namespace)
    # release_snapshot is unchanged; bind its classifier to the old namespace.
    namespace["release_snapshot"] = FunctionType(recovery.release_snapshot.__code__,namespace)
    return SimpleNamespace(classify=namespace["classify"],projection_ready=namespace["projection_ready"])


class BaseParentAttestationTests(BaseParentFixture):
    def test_base_parent_survives_exact_owner_child_switch(self):
        before = self.snapshot()
        for role in ('workbench','native'):
            self.assertEqual(recovery.previous_attestation(self.managed.config,self.root_row,self.plan,self.full,role),
                self.base_attestation)
        with control_store.connect() as db:
            approval = db.execute('SELECT * FROM db_creation_requests WHERE id=?',(self.approval['request_id'],)).fetchone()
            self.assertEqual((approval['approval_state'],approval['dispatch_state']),('VALID','CLAIMED'))
            self.assertTrue(approval['claim_id'])
            self.assertEqual(db.execute('SELECT state FROM operations WHERE id=?',(self.operation['id'],)).fetchone()[0],'UNKNOWN')
            states = dict(db.execute('SELECT step,state FROM operation_steps WHERE operation_id=?',(self.operation['id'],)))
            self.assertEqual(states.pop('verify'),'UNKNOWN')
            self.assertEqual(set(states.values()),{'SUCCEEDED'})
            self.assertEqual({r[0] for r in db.execute('SELECT state FROM db_creation_principal_gates')},{'ACTIVE'})
        self.assertEqual(self.snapshot(),before)

    def test_base_parent_still_rejects_owner_and_pinned_input_drift(self):
        before = self.snapshot()
        for role in ('workbench','native'):
            record = self.managed.config.owner(role)
            self.write_owner(role,owner_instance_id=str(uuid.uuid4()))
            with self.subTest(owner=role),self.assertRaises(core.OperationError):
                recovery.previous_attestation(self.managed.config,self.root_row,self.plan,self.full,role)
            self.write_owner(role,**record)
        paths = [Path(self.managed.config.value['base_runtime_profile']),self.config.base_private,
            next(p for p in self.config.base_bundle.rglob('*') if p.is_file())]
        for path in paths:
            original = path.read_bytes()
            try:
                path.write_bytes(original+b'\n')
                with self.subTest(pin=path.name),self.assertRaises((core.OperationError,ValueError,RuntimeError)):
                    recovery.previous_attestation(self.managed.config,self.root_row,self.plan,self.full,'workbench')
            finally:
                path.write_bytes(original)
        self.assertEqual(recovery.previous_attestation(self.managed.config,self.root_row,self.plan,self.full,'workbench'),
            self.base_attestation)
        self.assertEqual(self.snapshot(),before)


class PreverifyObservationTests(preparation_tests.PreparationFixture):
    complete_bootstrap = preparation_tests.PreparationTests.complete_bootstrap
    managed_candidate = preparation_tests.ManagedPreparationTests.managed_candidate

    def create(self, **kwargs):
        return super().create(initial_grants=[dict(principal_id='reader',maximum_role='bi_viewer')],**kwargs)

    def start_unknown(self, phase='bootstrap'):
        self.managed,bound,*ignored = self.managed_candidate(prepare=phase=='full')
        context = patch.object(bridge,'current',return_value=self.managed)
        context.start(); self.addCleanup(context.stop)
        if phase == 'bootstrap':
            self.prepare()
            bound = Operator(self.directory_for()/'operator.json')
        detail = creations.get(self.alice,'admin',self.root_id)
        public = next(p for p in detail['plans'] if p['phase']==phase)
        approved = creations.mutate(self.alice,'admin',self.root_id,'APPROVE',
            dict(expected_revision=detail['revision'],plan_id=public['plan_id'],operation_id=public['operation_id'],
                expected_operation_revision=public['operation_revision'],delivery_digest=public['delivery_digest']),
            'preverify-'+phase,phase=phase)
        permit = auth.claim(self.config.enrollment,self.executor,approved['request_id'],
            owner_id=self.managed.hello['owner_instance_id'])
        operation = core.inspect(public['operation_id'],actor='alice')
        bound.bind(operation); bound.check_receipt(operation)
        with control_store.connect() as db:
            self.plan = dict(db.execute('SELECT * FROM db_creation_phase_plans WHERE id=?',(public['plan_id'],)).fetchone())
            self.root = dict(db.execute('SELECT * FROM db_creations WHERE id=?',(self.root_id,)).fetchone())
        observations = runtime_profile.private_json(self.directory_for(phase)/'journal.json')['observations']
        def lost(step,current):
            bound.receipt['dispatches'].append(step)
            bound.save(); bound.assert_intent(current,step)
            if step == 'apply_secrets':
                bound.receipt['tofu_dispatched'] = True
                bound.receipt['children_before'] = {c['metadata']['name']:dict(uid='uid-'+c['metadata']['name'],spec=c['spec']) for c in bound.children}
                bound.save()
                raise ConnectionError('unit lost response')
            return dict(evidence_ref='unit:'+step)
        with auth.delegated(permit):
            self.operation = core.apply(public['operation_id'],actor='alice',expected_revision=public['operation_revision'],
                delivery_digest=public['delivery_digest'],authorize=lambda actor:True,
                observe_secrets=lambda p:copy.deepcopy(observations),execute_step=lost,
                verify=Mock(side_effect=AssertionError('verify must remain unissued')))
        self.assertEqual(self.operation['state'],'UNKNOWN')
        self.bound,self.approved = bound,approved
        self.receipt_bytes = bound.receipt_path.read_bytes()
        self.install_external_records()

    def install_external_records(self):
        import warehouse_operator as module
        refs = self.bound.bundle['tofu']['bootstrap_secret_refs']+self.bound.bundle['tofu']['deferred_secret_refs']
        self.objects,self.changes,self.resources = {},[],[]
        for ref in refs:
            meta = dict(name=ref['name'],namespace=ref['namespace'],annotations={module.VERSION_ANNOTATION:ref['version']})
            after = dict(metadata=[meta],immutable=True,data={key:'unit-value' for key in ref['keys']})
            uid = 'uid-'+ref['name']
            self.changes.append(dict(mode='managed',type='kubernetes_secret_v1',provider_name='registry.opentofu.org/hashicorp/kubernetes',
                change=dict(actions=['create'],after=after)))
            self.resources.append(dict(mode='managed',type='kubernetes_secret_v1',instances=[dict(attributes=dict(after,metadata=[dict(meta,uid=uid)]))]))
            self.objects[ref['name']] = dict(metadata=dict(meta,uid=uid,resourceVersion='1'),immutable=True,
                data={key:base64.b64encode(value.encode()).decode() for key,value in after['data'].items()})
        save_private(self.config.tofu_state,dict(lineage='test-lineage',serial=4,resources=self.resources))
        def command(instance,argv,**kw):
            # A second writer proves no external observation runs under SQLiteTx.
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE'); db.rollback()
            if 'show' in argv:
                return json.dumps(dict(configuration=dict(provider_config=dict(kubernetes=dict(expressions=dict(
                    config_context=dict(constant_value=module.local_gitops.CONTEXT),
                    config_path=dict(references=['var.kubeconfig_path']))))),
                    variables=dict(kubeconfig_path=dict(value=str(self.config.kubeconfig))),resource_changes=self.changes))
            if 'secret' in argv:
                return json.dumps(self.objects[argv[argv.index('secret')+1]])
            if 'kustomization.kustomize.toolkit.fluxcd.io' in argv:
                name = argv[argv.index('kustomization.kustomize.toolkit.fluxcd.io')+1]
                child = next(c for c in instance.children if c['metadata']['name']==name)
                return json.dumps(dict(child,metadata=dict(child['metadata'],uid='uid-'+name),spec=dict(child['spec'],suspend=True)))
            raise AssertionError('No mutation or other command permitted')
        context = patch.object(Operator,'command',command)
        context.start(); self.addCleanup(context.stop)

    def observe(self):
        # RECONCILE is public; original/failed private request IDs retain history.
        with control_store.connect() as db:
            root = dict(db.execute('SELECT * FROM db_creations WHERE id=?',(self.root_id,)).fetchone())
        return recovery.observe(self.managed,root,self.plan,self.bound)

    def assert_fenced(self):
        self.assertEqual(self.bound.receipt_path.read_bytes(),self.receipt_bytes)
        with control_store.connect() as db:
            self.assertNotEqual(db.execute('SELECT state FROM warehouse_controls').fetchone()[0],'OPEN')
            self.assertNotEqual(db.execute('SELECT state FROM databases WHERE id=?',(self.new_id,)).fetchone()[0],'READY')
            self.assertFalse(db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? AND step='verify' AND state='SUCCEEDED'",(self.operation['id'],)).fetchone())

    def test_bootstrap_and_full_real_secret_observer_require_fresh_approval(self):
        self.start_unknown()
        with patch.object(Operator,'execute_step',side_effect=AssertionError('no send')),\
                patch.object(Operator,'verify',side_effect=AssertionError('no verify')):
            result = self.observe()
        self.assertEqual(result['state'],'AWAITING_APPLY')
        self.assert_fenced()
        with control_store.connect() as db:
            old = db.execute('SELECT * FROM db_creation_requests WHERE id=?',(self.approved['request_id'],)).fetchone()
            self.assertEqual(old['approval_state'],'REVOKED')
            self.assertTrue(old['private_receipt_ref'].startswith('private-intent:'))
        with self.assertRaises(core.OperationError):
            auth.claim(self.config.enrollment,self.executor,self.approved['request_id'],owner_id=self.managed.hello['owner_instance_id'])
        detail = creations.get(self.alice,'admin',self.root_id)
        current = next(p for p in detail['plans'] if p['plan_id']==self.plan['id'])
        fresh = creations.mutate(self.alice,'admin',self.root_id,'APPROVE',
            dict(expected_revision=detail['revision'],plan_id=current['plan_id'],operation_id=current['operation_id'],
                expected_operation_revision=current['operation_revision'],delivery_digest=current['delivery_digest']),
            'fresh-positive-approval',phase='bootstrap')
        self.assertNotEqual(fresh['request_id'],self.approved['request_id'])

    def test_full_real_secret_observation(self):
        self.start_unknown('full')
        self.assertEqual(self.observe()['state'],'AWAITING_APPLY')
        self.assert_fenced()

    def stopped_proof(self, phase='bootstrap'):
        self.start_unknown(phase)
        self.accounts['alice']['disabled'] = True; self.save_accounts()
        with self.assertRaises(auth.AuthorityLost):
            self.observe()
        del self.accounts['alice']['disabled']; self.save_accounts()
        self.assertEqual(self.observe()['state'],'UNKNOWN')
        with control_store.connect() as db:
            snapshot = recovery.release_snapshot(db,self.managed.config,self.operation['id'])
            self.assertEqual(db.execute("SELECT state FROM db_creation_principal_gates WHERE principal_id='alice'").fetchone()[0],'STOPPED')
        return snapshot

    def test_stop_proof_append_only_release_then_fresh_reconcile(self):
        snapshot = self.stopped_proof()
        proof_dir = recovery.preverify_proof_path(self.managed.config,snapshot['preverify'])
        proof = next(proof_dir.glob('*.json')); original = proof.read_bytes()
        with recovery.observation_request(snapshot['preverify']['request']):
            self.assertEqual(self.observe()['state'],'UNKNOWN')
        self.assertEqual(list(proof_dir.glob('*.json')),[proof]); self.assertEqual(proof.read_bytes(),original)
        with control_store.connect() as db:
            self.assertEqual(dict(db.execute('SELECT * FROM db_creation_requests WHERE id=?',(self.approved['request_id'],)).fetchone()),snapshot['preverify']['request'])
        self.assertEqual(proof.stat().st_mode & 0o777,0o600)
        def http(instance,request,**kw):
            role = next(r for r in ('workbench','native') if request.full_url.startswith(self.managed.config.value['origins'][r]))
            owner = snapshot['owners'][role]
            if request.full_url.endswith('/runtime-instance'):
                return dict(version=1,environment_id=self.config.enrollment.environment_id,owner_role=role,
                    owner_instance_id=owner['owner_id'],runtime_instance_id=owner['runtime_id'],runtime_attestation=owner['attestation'])
            return owner['attestation']
        def get(config,role,*args):
            return dict(state='NOT_FOUND',runtime_instance_id=snapshot['owners'][role]['runtime_id'])
        with patch.object(Operator,'http',http),patch.object(bridge,'owner_get',side_effect=get):
            name = next(iter(self.objects))
            missing = self.objects.pop(name)
            try:
                reader = recovery.preverify_reader(self.bound,snapshot['preverify'])
                try:
                    evidence = reader.reconcile(snapshot['preverify']['operation'],snapshot['preverify']['step'])
                finally:
                    reader.close_transports()
                self.assertEqual(evidence.outcome,'UNKNOWN')
                with self.assertRaises(core.OperationError):
                    recovery.observe_release(self.managed.config,'alice')
                # Saved proof, original seal/receipt, gate epoch, UNKNOWN attempt
                # and old revoked approval remain identical after current loss.
                with control_store.connect() as db:
                    self.assertEqual(recovery.release_snapshot(db,self.managed.config,self.operation['id']),snapshot)
                    old = db.execute('SELECT approval_state FROM db_creation_requests WHERE id=?',(self.approved['request_id'],)).fetchone()
                    self.assertEqual(old['approval_state'],'REVOKED')
                self.assertEqual(proof.read_bytes(),original)
                self.assert_fenced()
            finally:
                self.objects[name] = missing
            with patch.object(Operator,'http',return_value={}):
                with self.assertRaises(core.OperationError): recovery.observe_release(self.managed.config,'alice')
            with patch.object(bridge,'owner_get',return_value=dict(state='UNKNOWN',runtime_instance_id='unowned')):
                with self.assertRaises(core.OperationError): recovery.observe_release(self.managed.config,'alice')
            observation = recovery.observe_release(self.managed.config,'alice')
            with control_store.connect() as db:
                self.assertTrue(recovery.release_safe(db,self.config.enrollment,self.operation['id'],observation=observation))
            auth.release_principal(self.config.enrollment,self.executor,'alice','explicit-release',observation=observation)
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM operations WHERE id=?',(self.operation['id'],)).fetchone()[0],'UNKNOWN')
        detail = creations.get(self.alice,'admin',self.root_id)
        payload = dict(expected_revision=detail['revision'])
        ack = creations.mutate(self.alice,'admin',self.root_id,'RECONCILE',payload,'fresh-after-release')
        with control_store.connect() as db:
            request = dict(db.execute('SELECT * FROM db_creation_requests WHERE id=?',(ack['request_id'],)).fetchone())
        with recovery.observation_request(request):
            self.assertEqual(self.observe()['state'],'AWAITING_APPLY')
        self.assertEqual(creations.mutate(self.alice,'admin',self.root_id,'RECONCILE',payload,'fresh-after-release'),ack)
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT dispatch_state FROM db_creation_requests WHERE id=?',(ack['request_id'],)).fetchone()[0],'COMPLETE')
            old = db.execute('SELECT * FROM db_creation_requests WHERE id=?',(self.approved['request_id'],)).fetchone()
            self.assertEqual(old['approval_state'],'REVOKED')
            self.assertEqual(old['private_receipt_ref'],snapshot['preverify']['request']['private_receipt_ref'])
        self.assert_fenced()

    def test_full_stop_proof_preserves_unknown_and_original_parent_owners(self):
        snapshot = self.stopped_proof('full')
        for role,owner in snapshot['owners'].items():
            pinned = self.bound.config['managed_handoff']['owners'][role]
            self.assertEqual(owner['owner_id'],pinned['owner_instance_id'])
            self.assertEqual(owner['runtime_id'],pinned['old_runtime_instance_id'])
            self.assertNotEqual(owner['runtime_id'],pinned['candidate_runtime_instance_id'])
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM operations WHERE id=?',(self.operation['id'],)).fetchone()[0],'UNKNOWN')
        self.assert_fenced()

    def test_missing_or_mismatched_secret_uid_never_proves_success(self):
        self.start_unknown()
        for invalid in (None,'different'):
            for obj in self.objects.values(): obj['metadata']['uid'] = invalid
            self.assertEqual(self.observe()['state'],'UNKNOWN')
        self.assert_fenced()

    def test_absence_timeout_exception_and_untyped_evidence_preserve_owned_unknown(self):
        self.start_unknown()
        with control_store.connect() as db:
            before = dict(db.execute('SELECT * FROM operation_requests WHERE operation_id=?',(self.operation['id'],)).fetchone())
        for failure in (FileNotFoundError('unit missing PID/file'),TimeoutError('unit timeout'),ConnectionError('unit 404')):
            with patch.object(Operator,'command',side_effect=failure):
                self.assertEqual(self.observe()['state'],'UNKNOWN')
        for invalid in (None,dict(step='apply_secrets',outcome='SUCCEEDED'),
                core.ReconciliationEvidence('apply_secrets','UNKNOWN','unit:unknown','observed_configuration')):
            with patch.object(Operator,'reconcile',return_value=invalid):
                self.assertEqual(self.observe()['state'],'UNKNOWN')
        with control_store.connect() as db:
            after = dict(db.execute('SELECT * FROM operation_requests WHERE operation_id=?',(self.operation['id'],)).fetchone())
            self.assertEqual(before,after)
            with self.assertRaises((core.OperationError,OSError)):
                recovery.release_snapshot(db,self.managed.config,self.operation['id'])
        self.assertEqual(self.observe()['state'],'AWAITING_APPLY')
        self.assert_fenced()

    def test_current_grant_drift_does_not_prevent_original_positive_classification(self):
        import grant_management
        # Intake remains pinned while desired access changes after partial apply.
        self.start_unknown()
        with control_store.connect() as db:
            row = db.execute('SELECT revision FROM grants WHERE database_id=? AND principal_id=?',(self.new_id,'reader')).fetchone()
        grant_management.mutate(self.alice,'admin',self.new_id,
            dict(principal_id='reader',maximum_role='revoked',expected_revision=row['revision']), 'revoke-original')
        self.assertEqual(self.observe()['state'],'AWAITING_APPLY')
        detail = creations.get(self.alice,'admin',self.root_id)
        self.assertNotIn('replan',detail['next_actions'])
        current = next(p for p in detail['plans'] if p['plan_id']==self.plan['id'])
        fresh = creations.mutate(self.alice,'admin',self.root_id,'APPROVE',
            dict(expected_revision=detail['revision'],plan_id=current['plan_id'],operation_id=current['operation_id'],
                expected_operation_revision=current['operation_revision'],delivery_digest=current['delivery_digest']),
            'fresh-stale-approval',phase='bootstrap')
        permit = auth.claim(self.config.enrollment,self.executor,fresh['request_id'],owner_id=self.managed.hello['owner_instance_id'])
        with auth.delegated(permit),patch.object(Operator,'execute_step') as send:
            with self.assertRaises(core.OperationError):
                core.apply(self.operation['id'],actor='alice',expected_revision=current['operation_revision'],
                    delivery_digest=current['delivery_digest'],authorize=lambda actor:True,
                    observe_secrets=self.bound.observe_secrets,execute_step=send,verify=send)
            send.assert_not_called()
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT maximum_role FROM grants WHERE database_id=? AND principal_id=?',(self.new_id,'reader')).fetchone()[0],'revoked')
            effective = db.execute('SELECT * FROM effective_grants WHERE database_id=? AND principal_id=?',(self.new_id,'reader')).fetchone()
            self.assertTrue(effective is None or effective['maximum_role']=='revoked')
        self.assert_fenced()

    def test_release_rejects_tampered_proof_receipt_candidate_attempt_cursor_and_extra_unknown(self):
        snapshot = self.stopped_proof()
        proof = next(recovery.preverify_proof_path(self.managed.config,snapshot['preverify']).glob('*.json'))
        original = proof.read_bytes()
        proof.write_bytes(original+b' ')
        # Whitespace alone has the same semantic digest; changed evidence does not.
        changed = runtime_profile.private_json(proof); changed['evidence']['evidence_ref']='forged'
        save_private(proof,changed)
        with control_store.connect() as db,self.assertRaises(core.OperationError):
            recovery.release_snapshot(db,self.managed.config,self.operation['id'])
        proof.write_bytes(original)
        for sql in ("UPDATE operation_requests SET attempt_id='wrong' WHERE operation_id=?",
                    "UPDATE operation_requests SET revision=revision+1 WHERE operation_id=?",
                    "UPDATE db_creation_requests SET revision_cursor=revision_cursor+1 WHERE plan_id IN (SELECT id FROM db_creation_phase_plans WHERE operation_id=?)",
                    "UPDATE db_creation_phase_plans SET delivery_digest='wrong' WHERE operation_id=?",
                    "UPDATE operation_steps SET state='UNKNOWN' WHERE step='verify' AND operation_id=?"):
            with control_store.connect() as db:
                db.execute('BEGIN'); db.execute(sql,(self.operation['id'],))
                with self.assertRaises(core.OperationError): recovery.release_snapshot(db,self.managed.config,self.operation['id'])
                db.rollback()
        for file in (self.directory_for()/'operator.json',self.directory_for()/'journal.json',
                Path(self.managed.config.value['owners']['native']['record_path'])):
            original = file.read_bytes(); changed = runtime_profile.private_json(file)
            if file.name == 'operator.json': changed['state_serial'] += 1
            elif file.name == 'journal.json': changed['binding']['generation_id']='wrong'
            else: changed['runtime_instance_id']='unowned-runtime'
            save_private(file,changed)
            with control_store.connect() as db,self.assertRaises((core.OperationError,OSError,ValueError)):
                recovery.release_snapshot(db,self.managed.config,self.operation['id'])
            file.write_bytes(original)
        with control_store.connect() as db:
            db.execute('BEGIN')
            db.execute("UPDATE db_creation_principal_gates SET revision=revision+1 WHERE principal_id='alice'")
            with self.assertRaises((core.OperationError,OSError)):
                recovery.release_snapshot(db,self.managed.config,self.operation['id'])
            db.rollback()
        receipt = runtime_profile.private_json(self.bound.receipt_path); receipt['dispatches'].append('verify')
        save_private(self.bound.receipt_path,receipt)
        with control_store.connect() as db,self.assertRaises((core.OperationError,OSError)):
            recovery.release_snapshot(db,self.managed.config,self.operation['id'])
        self.bound.receipt_path.write_bytes(self.receipt_bytes)


class FullSecretContinuityTests(preparation_tests.PreparationFixture):
    """Production Preparation/Secret/provisioning paths; only external clients are fake."""
    managed_candidate = preparation_tests.ManagedPreparationTests.managed_candidate

    def setUp(self):
        super().setUp()
        import database_material as material
        import database_provision
        import warehouse_operator as module
        from tests.test_database_provision import API
        self.module, self.material = module, material
        self.managed, *_ = self.managed_candidate(prepare=False)
        self.cluster, self.plans, self.apis, self.sends = {}, {}, {}, []
        self.loss = False
        self.external_reads = 0
        base = material.build_material(self.base, self.private)
        self.put_secrets(self.base, base['tofu_vars']['warehouse_generation_secrets'])
        for key, entry in self.base['tofu']['databases'].items():
            api = API()
            issued = database_provision.provision(api, key, entry,
                [s['sql_name'] for s in self.base['manifest']['spec']['databases'][key]['schemas']], {}, lambda state: None)
            for consumer, role in (('trino', 'trino_secret'), ('iceberg-bootstrap', 'bootstrap_secret')):
                principal = api.entities[database_provision.permissions.MANAGEMENT+'/principals/'+issued[consumer]['principal_name']]
                principal['clientId'] = self.private['secrets'][material.ref_identity(entry[role])]['data']['client_id']
            self.apis[entry['realm']] = api

        original_connect = control_store.connect
        native_connect = module.sqlite3.connect
        metadata_uri = (control_store.root()/'jobs.sqlite3').resolve().as_uri()+'?mode=ro'
        self.open_connections = 0
        self.metadata_connections = []
        case = self
        class MetadataConnection(module.sqlite3.Connection):
            closed = False
            def execute(self, sql, parameters=()):
                if sql.startswith('SELECT '):
                    case.assertEqual(super().execute('PRAGMA query_only').fetchone()[0],1)
                return super().execute(sql,parameters)
            def commit(self):
                raise AssertionError('read-only metadata must not commit')
            def close(self):
                self.closed = True
                super().close()
        def sqlite_connect(database, *args, **kwargs):
            if kwargs.get('uri'):
                self.assertEqual(database,metadata_uri)
                db = native_connect(database,*args,**kwargs,factory=MetadataConnection)
                self.metadata_connections.append(db)
                return db
            return native_connect(database,*args,**kwargs)
        @contextmanager
        def connected():
            with original_connect() as db:
                self.open_connections += 1
                try:
                    yield db
                finally:
                    self.open_connections -= 1

        def command(instance, argv, **kwargs):
            # A separate writer must finish at every external command boundary.
            self.assertEqual(self.open_connections,0)
            self.assertTrue(all(db.closed for db in self.metadata_connections))
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('UPDATE warehouse_controls SET revision=revision')
            self.external_reads += 1
            if 'show' in argv:
                if instance.phase not in self.plans:
                    values = runtime_profile.private_json(instance.path.parent/'material/terraform.tfvars.json')
                    changes = []
                    for identity, secret in values['warehouse_generation_secrets'].items():
                        ref = next(r for r in instance.bundle['tofu']['bootstrap_secret_refs']+
                            instance.bundle['tofu']['deferred_secret_refs'] if material.ref_identity(r)==identity)
                        meta = dict(namespace=ref['namespace'], name=ref['name'], annotations={
                            module.VERSION_ANNOTATION:ref['version'], module.database_generation.ANNOTATION:instance.binding['generation_id']})
                        after = dict(metadata=[meta], immutable=True, data=secret['data'])
                        old = self.cluster.get(('secret',ref['namespace'],ref['name']))
                        before = None if old is None else dict(after, metadata=[{k:old['metadata'][k]
                            for k in ('namespace','name','annotations')}],
                            data={k:base64.b64decode(v).decode() for k,v in old['data'].items()})
                        changes.append(dict(mode='managed', type='kubernetes_secret_v1',
                            provider_name='registry.opentofu.org/hashicorp/kubernetes', change=dict(
                                actions=['create'] if old is None else ['update'], before=before, after=after)))
                    self.plans[instance.phase] = dict(configuration=dict(provider_config=dict(kubernetes=dict(expressions=dict(
                        config_context=dict(references=['var.kube_context']),config_path=dict(references=['var.kubeconfig_path']))))),
                        variables=dict(kube_context=dict(value=module.local_gitops.CONTEXT),
                            kubeconfig_path=dict(value=str(self.config.kubeconfig))),resource_changes=changes)
                return json.dumps(self.plans[instance.phase])
            if 'get' in argv:
                index = argv.index('get')
                kind, name = argv[index+1:index+3]
                namespace = argv[argv.index('-n')+1]
                kind = kind.split('.')[0].lower()
                return json.dumps(self.cluster[(kind,namespace,name)]) if (kind,namespace,name) in self.cluster else ''
            if 'patch' in argv:
                name = argv[argv.index('patch')+1].split('/')[-1]
                obj = self.cluster[('kustomization','flux-system',name)]
                changes = json.loads(argv[argv.index('-p')+1])
                self.assertEqual(changes[0]['value'],obj['metadata']['uid'])
                self.assertEqual(changes[1]['value'],obj['metadata']['resourceVersion'])
                obj['spec']['suspend'] = changes[2]['value']
                return ''
            if 'apply' in argv:
                import yaml
                for obj in yaml.safe_load_all(kwargs['payload']):
                    self.put_object(obj)
                    if obj['kind']=='Kustomization':
                        self.health_objects(instance,obj)
                return ''
            if 'wait' in argv:
                return ''
            raise AssertionError('Unexpected external command')

        def apply(plan_path, state_path, workdir, **kwargs):
            self.assertEqual(self.open_connections,0)
            phase = Path(plan_path).parent.parent.name
            self.sends.append(phase)
            bundle = module.database_generation.load_bundle(Path(plan_path).parent/'bundle')
            values = runtime_profile.private_json(Path(plan_path).parent/'material/terraform.tfvars.json')
            self.put_secrets(bundle,values['warehouse_generation_secrets'])
            resources = []
            for resource in self.plans[phase]['resource_changes']:
                after = copy.deepcopy(resource['change']['after'])
                meta = after['metadata'][0]
                meta['uid'] = self.cluster[('secret',meta['namespace'],meta['name'])]['metadata']['uid']
                resources.append(dict(mode='managed',type='kubernetes_secret_v1',instances=[dict(attributes=after)]))
            old = runtime_profile.private_json(state_path)
            save_private(Path(state_path),dict(lineage=old['lineage'],serial=old['serial']+1,resources=resources))
            if self.loss:
                raise ConnectionError('unit completed apply reply loss')

        def open_api(request, **kwargs):
            import io
            from urllib.parse import urlsplit
            self.assertEqual(self.open_connections,0)
            realm = next(value for key,value in request.header_items() if key.lower()=='polaris-realm')
            api = self.apis.setdefault(realm, API())
            try:
                result = api(request.get_method(),urlsplit(request.full_url).path,
                    json.loads(request.data) if request.data else None)
            except database_provision.permissions.APIError as error:
                raise module.urllib.error.HTTPError(request.full_url,error.status,'unit response',None,None) from None
            if isinstance(result,dict) and 'credentials' in result:
                result['credentials']['clientSecret'] = 'unit-issued-'+result['principal']['name']
            return io.BytesIO(canonical(result))

        for context in (patch.object(bridge,'current',return_value=self.managed),
                patch.object(control_store,'connect',connected),
                patch.object(module.sqlite3,'connect',side_effect=sqlite_connect),
                patch.object(Operator,'command',command),patch.object(module,'_apply_saved_plan',side_effect=apply),
                patch.object(module.urllib.request,'build_opener',return_value=SimpleNamespace(open=open_api)),
                patch.object(Operator,'http',return_value=RecoveryTests.oauth_ack(time.time()+3600)),
                patch.object(Operator,'upload'),patch.object(Operator,'start_transports',fixture_owned_transports),
                patch.object(Operator,'close_transports',_NATIVE_CLOSE)):
            context.start(); self.addCleanup(context.stop)

    def put_object(self, obj):
        obj = copy.deepcopy(obj)
        meta = obj['metadata']
        meta.setdefault('namespace','shirokuma-dev')
        key = obj['kind'].lower(),meta['namespace'],meta['name']
        previous = self.cluster.get(key)
        meta.update(uid=previous['metadata']['uid'] if previous else 'uid-'+meta['name'],resourceVersion='17',generation=2)
        obj['status'] = dict(observedGeneration=2,readyReplicas=obj.get('spec',{}).get('replicas',1),
            updatedReplicas=obj.get('spec',{}).get('replicas',1),currentRevision='same',updateRevision='same',
            conditions=[dict(type='Ready',status='True',observedGeneration=2),dict(type='Complete',status='True')])
        self.cluster[key] = obj

    def put_secrets(self, bundle, values):
        for ref in bundle['tofu']['bootstrap_secret_refs']+bundle['tofu']['deferred_secret_refs']:
            value = values.get(self.material.ref_identity(ref))
            if value is not None:
                self.put_object(dict(kind='Secret',metadata=dict(namespace=ref['namespace'],name=ref['name'],annotations={
                    self.module.VERSION_ANNOTATION:ref['version'],self.module.database_generation.ANNOTATION:bundle['manifest']['generation_id']}),
                    immutable=True,data={k:base64.b64encode(v.encode()).decode() for k,v in value['data'].items()}))

    def health_objects(self, bound, child):
        import posixpath
        import yaml
        for health in child['spec'].get('healthChecks',[]):
            obj = bound.expected_resource(health)
            obj['metadata'].setdefault('namespace',health['namespace'])
            self.put_object(obj)
        for path, content in bound.files.items():
            if path.endswith(('.yaml','.yml')):
                for obj in yaml.safe_load_all(content):
                    if isinstance(obj,dict) and obj.get('kind')=='ConfigMap':
                        self.put_object(obj)
            if Path(path).name != 'kustomization.yaml':
                continue
            for config in yaml.safe_load(content).get('configMapGenerator',[]):
                data = {}
                for item in config.get('files',[]):
                    key, filename = item.split('=',1) if '=' in item else (Path(item).name,item)
                    data[key] = bound.files[posixpath.normpath(posixpath.join(posixpath.dirname(path),filename))].decode()
                self.put_object(dict(kind='ConfigMap',metadata=dict(name=config['name']),data=data))

    def approve_claim(self, phase, token):
        detail = creations.get(self.alice,'admin',self.root_id)
        public = next(p for p in detail['plans'] if p['phase']==phase)
        approved = creations.mutate(self.alice,'admin',self.root_id,'APPROVE',dict(expected_revision=detail['revision'],
            plan_id=public['plan_id'],operation_id=public['operation_id'],expected_operation_revision=public['operation_revision'],
            delivery_digest=public['delivery_digest']),token,phase=phase)
        permit = auth.claim(self.config.enrollment,self.executor,approved['request_id'],owner_id=self.managed.hello['owner_instance_id'])
        return approved,permit,core.inspect(public['operation_id'],actor='alice')

    def apply(self, bound, operation, permit, **kwargs):
        failures = []
        def checked(callback, *args):
            try:
                return callback(*args)
            except Exception as error:
                failures.append(error)
                raise
        with auth.delegated(permit):
            result = core.apply(operation['id'],actor='alice',expected_revision=operation['revision'],
                delivery_digest=operation['delivery_digest'],authorize=lambda actor:True,
                observe_secrets=bound.observe_secrets,execute_step=lambda *args:checked(bound.execute_step,*args),
                verify=lambda *args:checked(kwargs.get('verify',bound.verify),*args))
        if bound.phase=='bootstrap' and failures:
            raise failures[0]
        return result

    def lost_full(self):
        self.prepare()
        bootstrap = Operator(self.directory_for()/'operator.json')
        for child in bootstrap.children:
            self.put_object(child)
        self.put_object(dict(kind='Kustomization',metadata=dict(namespace='flux-system',name='flux-system'),spec=dict(suspend=True)))
        ref = bootstrap.bundle['manifest']['spec']['shared_secret_refs']['gitops_source']
        self.put_object(dict(apiVersion='source.toolkit.fluxcd.io/v1',kind='Bucket',metadata=dict(namespace='flux-system',
            name=self.module.local_gitops.SOURCE),spec=dict(prefix=bootstrap.prefix,secretRef={'name':ref['name']})))
        approved,permit,current = self.approve_claim('bootstrap','real-bootstrap')
        bootstrap.bind(current); bootstrap.check_receipt(current)
        prepared = self.apply(bootstrap,current,permit)
        self.assertEqual(prepared['state'],'PREPARED')
        issued = copy.deepcopy(bootstrap.receipt['provisioning'])
        self.module.validate_bootstrap_receipt(runtime_profile.private_json(bootstrap.receipt_path),prepared,require_seal=True)
        self.prepare(self.request_phase('full'))
        self.bound = Operator(self.directory_for('full')/'operator.json')
        approved,permit,current = self.approve_claim('full','lost-full')
        self.bound.bind(current); self.bound.check_receipt(current)
        entry = self.bound.bundle['tofu']['databases'][self.new_id]
        missing = {(entry[r]['namespace'],entry[r]['name']) for r in ('trino_secret','bootstrap_secret')}
        self.assertEqual({(r['namespace'],r['name']) for r in current['secret_observations'] if not r['exists']},missing)
        values = runtime_profile.private_json(self.directory_for('full')/'material/terraform.tfvars.json')['warehouse_generation_secrets']
        for consumer,role in (('trino','trino_secret'),('iceberg-bootstrap','bootstrap_secret')):
            self.assertEqual(values[self.material.ref_identity(entry[role])]['data'],
                {k:issued[self.new_id]['consumers'][consumer][k] for k in ('client_id','client_secret','realm')})
        self.loss = True
        unknown = self.apply(self.bound,current,permit)
        self.assertEqual(unknown['state'],'UNKNOWN')
        self.original_bytes = self.bound.receipt_path.read_bytes()
        self.assertNotIn('secret_after',runtime_profile.private_json(self.bound.receipt_path))
        self.assertNotIn('apply_secrets',runtime_profile.private_json(self.bound.receipt_path)['acknowledgements'])
        with control_store.connect() as db:
            self.plan = dict(db.execute('SELECT * FROM db_creation_phase_plans WHERE operation_id=?',(current['id'],)).fetchone())
            root = dict(db.execute('SELECT * FROM db_creations WHERE id=?',(self.root_id,)).fetchone())
            first = db.execute('SELECT private_receipt_ref FROM db_creation_requests WHERE id=?',(approved['request_id'],)).fetchone()[0]
        result = recovery.observe(self.managed,root,self.plan,self.bound)
        self.assertEqual(result['state'],'AWAITING_APPLY')
        self.assertEqual(self.bound.receipt_path.read_bytes(),self.original_bytes)
        with control_store.connect() as db:
            old = db.execute('SELECT * FROM db_creation_requests WHERE id=?',(approved['request_id'],)).fetchone()
            self.assertEqual(old['approval_state'],'REVOKED')
            self.assertEqual(old['private_receipt_ref'],first)
            self.assertIsNone(db.execute('SELECT secret_after FROM operation_requests WHERE operation_id=?',(current['id'],)).fetchone()[0])
        with self.assertRaises(core.OperationError):
            auth.claim(self.config.enrollment,self.executor,approved['request_id'],owner_id=self.managed.hello['owner_instance_id'])
        return issued

    def test_full_reply_loss_resumes_exact_proven_uids_with_fresh_public_claim(self):
        issued = self.lost_full()
        before = copy.deepcopy(self.cluster)
        fresh,permit,current = self.approve_claim('full','fresh-full')
        # Stop after real FULL configuration/Secret verification, before runtime
        # activation or SQL. Their independent protocol suites own those effects.
        with patch.object(self.bound,'declare_verify',side_effect=RuntimeError('unit stop before runtime')) as boundary,\
                patch.object(self.bound,'execute_step',wraps=self.bound.execute_step) as execute,\
                patch.object(self.bound,'verify_secret_identities',wraps=self.bound.verify_secret_identities) as identities:
            result = self.apply(self.bound,current,permit)
        self.assertEqual(result['state'],'UNKNOWN')
        boundary.assert_called_once(); identities.assert_called_once()
        self.assertNotIn('apply_secrets',[call.args[0] for call in execute.call_args_list])
        self.assertEqual(self.sends,['bootstrap','full'])
        self.assertEqual(self.bound.prepared_predecessor(current)['provisioning'],issued)
        for key, obj in before.items():
            if key[0]=='secret':
                self.assertEqual(self.cluster[key],obj)
        observed = core._observations(self.bound.bundle,self.bound.observe_secrets(current))
        with control_store.connect() as db:
            after = json.loads(db.execute('SELECT secret_after FROM operation_requests WHERE operation_id=?',(current['id'],)).fetchone()[0])
            self.assertEqual(observed,after)
            self.assertNotEqual(db.execute('SELECT state FROM warehouse_controls').fetchone()[0],'OPEN')
            self.assertNotEqual(db.execute('SELECT state FROM databases WHERE id=?',(self.new_id,)).fetchone()[0],'READY')
        self.assertNotIn('secret_after',runtime_profile.private_json(self.bound.receipt_path))
        self.assertGreater(self.external_reads,0)
        self.assertTrue(self.metadata_connections)

    def terminal_recovery(self, before_recovery=None, *, public_null=False):
        """Sealed real preparation with retained complete business/auth leaves."""
        self.lost_full()
        accepted,permit,current = self.approve_claim('full','terminal-retained-full')
        def retained(operation):
            bound = self.bound
            bound.check_receipt(operation)
            bound.receipt['dispatches'].append('verify')
            bound.assert_intent(operation,'verify')
            bound.verification_configuration(operation)
            bound.declare_verify(operation)
            # The original core verify owns its attempt here; a later approval
            # alone must never manufacture a business transport leaf intent.
            bound.start_transports(operation)
            for key,saved in bound.receipt['verify_http'].items():
                saved.update(state='ACK',response=RecoveryTests.oauth_ack(time.time()-60))
                bound.receipt.setdefault('read_auth',{})['retained/'+key] = dict(
                    state='ACK',context='retained',http_identity=key,response=RecoveryTests.oauth_ack(time.time()+3600))
            for saved in bound.receipt['runtime_activations'].values():
                saved.update(state='ACK',leaves={leaf:'ACK' for leaf in saved['leaves']})
            candidates = bound.candidate_credentials()
            roles = dict(data_engineer={key:value['trino'] for key,value in candidates['databases'].items()},
                bi_viewer=bound.viewer_credentials())
            if bound.bundle['manifest']['spec']['version'] == 2:
                roles['admin'] = bound.admin_credentials()
            for role,credentials in roles.items():
                for key,credential in credentials.items():
                    entry = bound.bundle['manifest']['spec']['databases'][key]
                    receipt_key = key if role == 'data_engineer' else key+'/'+role
                    bound.receipt['verify_declaration']['sql'][receipt_key] = 'INTENT'
                    bound.receipt['sql'][receipt_key] = dict(dispatched=True,complete=True,backend_id='retained-'+receipt_key,
                        rows=[[credential['username'],entry['sql_catalog'],schema['sql_name']] for schema in entry['schemas']
                              if role != 'bi_viewer' or schema['sql_name'] == 'marts'])
            bound.receipt.setdefault('provisioning',{})
            bound.save()
            bound.provision_databases(operation,verify_only=True)
            bound.save()
            if public_null:
                self.terminal_request = accepted['request_id']
                with self.terminal_backend():
                    bound.verify(operation)
            raise ConnectionError('retained completion before canonical acknowledgement')
        self.assertEqual(self.apply(self.bound,current,permit,verify=retained)['state'],'UNKNOWN')
        observed = before_recovery(current) if before_recovery else None
        with control_store.connect() as db:
            root = dict(db.execute('SELECT * FROM db_creations WHERE id=?',(self.root_id,)).fetchone())
            original = dict(db.execute("SELECT * FROM db_creation_phase_plans WHERE operation_id=? AND purpose='APPLY'",(current['id'],)).fetchone())
            op = recovery.original_unknown(db,current['id'])
        leaves = recovery.classify(self.bound)
        self.assertTrue(leaves and set(leaves.values()) == {'COMPLETE'})
        if observed is not None:
            with control_store.connect() as db:
                recovery_plan = dict(db.execute('SELECT * FROM db_creation_phase_plans WHERE id=?',(observed['plan_id'],)).fetchone())
            pid = recovery_plan['id']
            frozen = runtime_profile.private_json(recovery_plan['private_ref'])
            self.assertIn('canonical_attempt',frozen)
            self.assertIsNone(op['attempt_id']); self.assertIsNone(frozen['canonical_attempt'])
        else:
            frozen = dict(version=1,purpose='VERIFY_RECOVERY',creation_id=self.root_id,operation_id=current['id'],
                original_plan_id=original['id'],candidate=self.bound.binding,
                original_attempt=self.bound.receipt['verify_declaration']['original_claim_id'],canonical_attempt=op['attempt_id'],
                journal_path=str(self.bound.receipt_path),journal_digest=digest(canonical(self.bound.receipt)),
                leaves=leaves,transports=self.bound.config.get('managed_transport',{}).get('endpoints',{}),executable=True)
            path = self.directory/'terminal-recovery.json'
            save_private(path,frozen)
            with auth.transaction() as db:
                pid = creations._attach_plan(db,self.config.enrollment,self.root_id,current['id'],private_ref=str(path),
                    public_summary=dict(code='RECOVERY_READY'),purpose='VERIFY_RECOVERY',recovery_digest=digest(canonical(frozen)))
        detail = creations.get(self.alice,'admin',self.root_id)
        public = next(p for p in detail['plans'] if p['plan_id'] == pid)
        accepted = creations.mutate(self.alice,'admin',self.root_id,'RECOVER_VERIFY',dict(expected_revision=detail['revision'],
            plan_id=pid,operation_id=current['id'],expected_operation_revision=public['operation_revision'],
            delivery_digest=public['delivery_digest'],recovery_digest=public['recovery_digest']),'terminal-recovery',phase='full')
        self.terminal_operation,self.terminal_frozen,self.terminal_request = current,frozen,accepted['request_id']
        return accepted['request_id']

    @contextmanager
    def terminal_backend(self, after_verify=None, observer_error=False):
        """Isolate external readbacks, retaining real verify/auth/provision/reconcile."""
        from tests.test_database_provision import API
        native_api,native_verify,native_observe = API.__call__,Operator.verify,Operator.observe_completed_verify
        self.terminal_gets,self.terminal_contexts,self.terminal_rows = [],[],[]
        def api(instance,method,path,body=None):
            self.assertEqual(method,'GET')
            self.assertIsNone(body)
            self.terminal_gets.append(path)
            return native_api(instance,method,path,body)
        def catalog(instance,key,credential):
            entry = instance.bundle['manifest']['spec']['databases'][key]
            return dict(database_id=key,realm=entry['binding']['polaris_realm'],catalog=entry['binding']['polaris_catalog'],
                namespaces=sorted(s['sql_name'] for s in entry['schemas']))
        def verify(instance,operation):
            result = native_verify(instance,operation)
            auth.record_leaf_intent(operation,'private-intent:'+digest(canonical(instance.receipt)))
            with control_store.connect() as db:
                self.terminal_rows.append(dict(db.execute('SELECT * FROM db_creation_requests WHERE id=?',
                    (self.terminal_request,)).fetchone()))
            if after_verify: after_verify()
            return result
        def observe(instance,operation):
            self.assertFalse(recovery.active())
            self.terminal_contexts.append(copy.deepcopy(recovery._observation_request.get()))
            if observer_error: raise KeyboardInterrupt('terminal observer interruption')
            return native_observe(instance,operation)
        with patch.object(API,'__call__',api),patch.object(Operator,'verify_polaris',catalog),\
                patch.object(Operator,'verify_runtime_handoff',return_value=dict(retained=True)),\
                patch.object(self.module.local_gitops,'observe_public_generation',return_value=dict(evidence_ref='retained-snapshot')),\
                patch.object(Operator,'http',side_effect=AssertionError('No OAuth or SQL send')) as http,\
                patch.object(type(self.managed),'ipc',side_effect=AssertionError('No activation send')) as ipc,\
                patch.object(Operator,'verify',verify),patch.object(Operator,'observe_completed_verify',observe),\
                patch.object(Operator,'close_transports') as close:
            yield http,ipc,close

    def test_terminal_recovery_current_request_reuses_complete_effects_and_awaits_full_approval(self):
        rid = self.terminal_recovery()
        with control_store.connect() as db:
            stale = dict(db.execute('SELECT * FROM db_creation_requests WHERE id=?',(rid,)).fetchone())
        before = copy.deepcopy(self.bound.receipt)
        with self.terminal_backend() as (http,ipc,close):
            result = recovery.execute(self.managed,rid)
        self.assertEqual(result['state'],'AWAITING_APPLY')
        self.assertTrue(self.terminal_gets)
        self.assertTrue(self.terminal_contexts[0] == self.terminal_rows[0])
        self.assertIsNone(stale['claim_id'])
        self.assertNotEqual(stale['private_receipt_ref'],self.terminal_rows[0]['private_receipt_ref'])
        self.assertIsNotNone(self.terminal_rows[0]['claim_id'])
        http.assert_not_called(); ipc.assert_not_called(); close.assert_called_once()
        after = runtime_profile.private_json(self.bound.receipt_path)
        for key in ('verify_http','read_auth','sql','runtime_activations'):
            self.assertTrue(canonical(before[key]) == canonical(after[key]))
        self.assertEqual(recovery.classify(SimpleNamespace(receipt=after,binding=self.bound.binding)),self.terminal_frozen['leaves'])
        self.assertIsNone(recovery._observation_request.get()); self.assertFalse(recovery.observing()); self.assertFalse(recovery.active())
        with control_store.connect() as db:
            self.assertIsNone(db.execute('SELECT attempt_id FROM operation_requests WHERE operation_id=?',(result['id'],)).fetchone()[0])
            completed = dict(db.execute('SELECT * FROM db_creation_requests WHERE id=?',(rid,)).fetchone())
            self.assertEqual((completed['approval_state'],completed['dispatch_state'],completed['stop_reason']),
                ('REVOKED','COMPLETE','RECONCILED'))
            for key in set(completed)-{'approval_state','dispatch_state','stop_reason','updated_at'}:
                self.assertEqual(completed[key],self.terminal_rows[0][key])
            self.assertNotEqual(db.execute('SELECT state FROM databases WHERE id=?',(self.new_id,)).fetchone()[0].upper(),'READY')
        with self.assertRaises(core.OperationError):
            core.apply(result['id'],actor='alice',expected_revision=result['revision'],delivery_digest=result['delivery_digest'],
                authorize=lambda actor:True,observe_secrets=lambda p:[],execute_step=lambda *args:self.fail('fresh approval required'),
                verify=lambda p:self.fail('fresh approval required'))

    def test_terminal_recovery_observer_error_restores_request_and_execution_contexts(self):
        rid = self.terminal_recovery()
        enclosing = dict(id='enclosing-context')
        with recovery.observation_request(enclosing):
            with self.terminal_backend(observer_error=True) as (http,ipc,close):
                with self.assertRaises(KeyboardInterrupt): recovery.execute(self.managed,rid)
            self.assertTrue(recovery._observation_request.get() == enclosing)
        self.assertTrue(self.terminal_contexts[0] == self.terminal_rows[0])
        self.assertIsNone(recovery._observation_request.get()); self.assertFalse(recovery.observing()); self.assertFalse(recovery.active())
        http.assert_not_called(); ipc.assert_not_called(); close.assert_called_once()
        self.assertEqual(core.inspect(self.terminal_operation['id'],actor='alice')['state'],'UNKNOWN')

    def test_terminal_recovery_auth_loss_after_verify_latches_stop_before_observation(self):
        rid = self.terminal_recovery()
        def revoke():
            self.accounts['executor']['disabled'] = True
            self.save_accounts()
        with self.terminal_backend(after_verify=revoke) as (http,ipc,close):
            with self.assertRaises(auth.AuthorityLost): recovery.execute(self.managed,rid)
        self.assertEqual(self.terminal_contexts,[])
        http.assert_not_called(); ipc.assert_not_called(); close.assert_called_once()
        self.assertIsNone(recovery._observation_request.get()); self.assertFalse(recovery.observing()); self.assertFalse(recovery.active())
        with control_store.connect() as db:
            self.assertEqual(db.execute("SELECT state FROM db_creation_principal_gates WHERE principal_id='executor'").fetchone()[0],'STOPPED')
        del self.accounts['executor']['disabled']; self.save_accounts()
        with self.assertRaises(core.OperationError): recovery.claim(self.managed,rid,self.executor)

    def test_terminal_recovery_claim_drift_after_verify_is_rejected_before_observation(self):
        rid = self.terminal_recovery()
        with control_store.connect() as db:
            attempt = db.execute('SELECT attempt_id FROM operation_requests WHERE operation_id=?',
                (self.terminal_operation['id'],)).fetchone()[0]
        def change_claim():
            with control_store.connect() as db:
                db.execute('UPDATE db_creation_requests SET claim_id=? WHERE id=?',('unrelated-current-claim',rid))
        with self.terminal_backend(after_verify=change_claim) as (http,ipc,close):
            with self.assertRaisesRegex(core.OperationError,'STALE_PLAN'): recovery.execute(self.managed,rid)
        self.assertEqual(self.terminal_contexts,[])
        http.assert_not_called(); ipc.assert_not_called(); close.assert_called_once()
        self.assertIsNone(recovery._observation_request.get()); self.assertFalse(recovery.observing()); self.assertFalse(recovery.active())
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT attempt_id FROM operation_requests WHERE operation_id=?',
                (self.terminal_operation['id'],)).fetchone()[0],attempt)

    def test_public_observe_missing_owned_transport_creates_no_auth_intent(self):
        clock = time.time()+7200
        def observe(current):
            before = runtime_profile.private_json(self.bound.receipt_path)
            token_http = Mock(return_value=RecoveryTests.oauth_ack(clock+3600))
            with patch('warehouse_operator.time.time',return_value=clock), \
                    patch.object(Operator,'start_transports',side_effect=AssertionError('no full OBSERVE launch')):
                result = self.public_null_observation(current,token_http=token_http)
            token_http.assert_not_called()
            after = runtime_profile.private_json(self.bound.receipt_path)
            self.assertEqual(after['read_auth'],before['read_auth'])
            self.assertEqual(after['verify_http'],before['verify_http'])
            return result
        self.terminal_recovery(before_recovery=observe,public_null=True)
        self.assertEqual(set(self.terminal_frozen['leaves'].values()),{'COMPLETE'})
        self.assertIs(self.terminal_frozen['executable'],True)

    def test_public_observe_owned_token_barrier_real_http_and_recovery_chain(self):
        clock = time.time()+7200
        original = {}
        def observe(current):
            original['http'] = copy.deepcopy(self.bound.receipt['verify_http'])
            original['auth'] = copy.deepcopy(self.bound.receipt['read_auth'])
            endpoints = self.bound.config['managed_transport']['endpoints']
            with token_forward_servers(self,endpoints,clock+3600) as servers:
                servers.borrowed()
                with patch('warehouse_operator.time.time',return_value=clock), \
                        patch.object(Operator,'start_transports',side_effect=AssertionError('full OBSERVE never launches')):
                    result = self.public_null_observation(current,token_http=native_token_http,
                        after_dispatch=lambda:self.assertEqual(servers.count(),0,'Public OBSERVE made a token POST'))
                self.assertEqual(servers.count(),0)
                saved = runtime_profile.private_json(self.bound.receipt_path)
                self.assertTrue(saved['read_auth'] == original['auth'])
                self.assertTrue(saved['verify_http'] == original['http'])
            return result
        rid = self.terminal_recovery(before_recovery=observe,public_null=True)
        endpoints = self.bound.config['managed_transport']['endpoints']
        business = runtime_profile.private_json(self.bound.receipt_path)
        provider_sends = list(self.sends)
        with token_forward_servers(self,endpoints,clock+3600) as servers, \
                patch('warehouse_operator.time.time',return_value=clock):
            with self.terminal_backend() as (unused,ipc,close), \
                    patch.object(Operator,'start_transports',_NATIVE_START), \
                    patch.object(Operator,'close_transports',_NATIVE_CLOSE),patch.object(Operator,'http',native_token_http):
                result = recovery.execute(self.managed,rid)
            self.assertEqual(result['state'],'AWAITING_APPLY')
            ipc.assert_not_called()
            saved = runtime_profile.private_json(self.bound.receipt_path)
            attempts = {k:v for k,v in saved['read_auth'].items() if k not in original['auth']}
            self.assertTrue(attempts)
            self.assertEqual(servers.count(),len(attempts))
            self.assertTrue(all(v['state']=='ACK' and v['context']==rid for v in attempts.values()))
            self.assertTrue(saved['verify_http'] == original['http'])
            approved,permit,current = self.approve_current_creation('normal-after-owned-refresh')
            self.terminal_request = approved['request_id']; self.bound.check_receipt(current)
            count = servers.count()
            self.finish_current_creation(permit,current)
            self.assertEqual(servers.count(),count)
            final = runtime_profile.private_json(self.bound.receipt_path)
            for field in ('verify_http','sql','runtime_activations'):
                self.assertEqual(final[field],business[field])
            self.assertEqual(self.sends,provider_sends)

    def public_null_observation(self, current, *, token_http=None, after_dispatch=None):
        """A real PUBLIC RECONCILE keeps UNKNOWN and clears the canonical attempt."""
        import database_creation_worker as worker
        with control_store.connect() as db:
            root = dict(db.execute('SELECT * FROM db_creations WHERE id=?',(self.root_id,)).fetchone())
            plan = dict(db.execute("SELECT * FROM db_creation_phase_plans WHERE operation_id=? AND purpose='APPLY'",
                (current['id'],)).fetchone())
            self.assertIsNotNone(db.execute('SELECT attempt_id FROM operation_requests WHERE operation_id=?',
                (current['id'],)).fetchone()[0])
        self.assertIn('verification_complete',self.bound.receipt)
        request_id = self.bound.receipt['verify_declaration']['original_request_id']
        replies = {}
        attestation = self.bound.config['managed_handoff']['runtime_attestation']
        for role,saved in self.bound.receipt['runtime_activations'].items():
            owner = saved['owner']
            descriptor = worker.descriptor(dict(id=request_id),root,plan,self.bound,role)
            path = Path(self.managed.config.value['owners'][role]['directory'])/'activations'/(saved['activation_id']+'.json')
            path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            save_private(path,dict(descriptor=descriptor,state='ACK',**{
                leaf:dict(state='ACK',intent_id=str(uuid.uuid4())) for leaf in ('stop','start','readiness')}))
            record_path = Path(self.managed.config.value['owners'][role]['record_path'])
            record = runtime_profile.private_json(record_path)
            record['runtime_instance_id'] = owner['candidate_runtime_instance_id']
            save_private(record_path,record)
            replies[role] = dict(version=1,action='GET_ACTIVATION',request_id=request_id,state='ACK',owner_role=role,
                owner_instance_id=owner['owner_instance_id'],runtime_instance_id=owner['candidate_runtime_instance_id'],
                previous_runtime_instance_id=owner['old_runtime_instance_id'])
        def owner_read(instance,request,**kwargs):
            if request.get_method() == 'POST' and token_http is not None:
                return token_http(instance,request,**kwargs)
            self.assertEqual(request.get_method(),'GET')
            for role,saved in self.bound.receipt['runtime_activations'].items():
                origin = self.managed.config.value['origins'][role]
                if request.full_url == origin+creations.COLLECTION+'/runtime-instance':
                    return dict(version=1,environment_id=self.config.enrollment.environment_id,owner_role=role,
                        owner_instance_id=saved['owner']['owner_instance_id'],
                        runtime_instance_id=saved['owner']['candidate_runtime_instance_id'],runtime_attestation=attestation)
                if request.full_url == origin+'/api/v1/runtime': return attestation
            self.fail('undeclared owner read')
        def owner_ipc(instance,role,action,rid,activation):
            self.assertEqual(action,'GET_ACTIVATION'); self.assertEqual(rid,request_id)
            self.assertEqual(activation,self.bound.receipt['runtime_activations'][role]['activation_id'])
            return replies[role]
        detail = creations.get(self.alice,'admin',self.root_id)
        public = creations.mutate(self.alice,'admin',self.root_id,'RECONCILE',
            dict(expected_revision=detail['revision']),'public-null-attempt')
        # Fail only the actual catalog GET; real reconcile derives UNKNOWN from
        # the retained positive verification, then freezes the current NULL.
        from tests.test_database_provision import API
        failed_reads = []
        def unavailable(api,method,path,body=None):
            self.assertEqual(method,'GET')
            failed_reads.append(path)
            raise ConnectionError('catalog readback unavailable')
        with self.terminal_backend(),patch.object(Operator,'http',owner_read), \
                patch.object(type(self.managed),'ipc',owner_ipc),patch.object(API,'__call__',unavailable):
            try:
                result = worker.dispatch(self.managed,public['request_id'])
            finally:
                if after_dispatch is not None:
                    after_dispatch()
        if token_http is None:
            self.assertTrue(failed_reads)
        self.assertEqual(result['state'],'AWAITING_RECOVERY_APPROVAL')
        with control_store.connect() as db:
            self.assertIsNone(db.execute('SELECT attempt_id FROM operation_requests WHERE operation_id=?',
                (current['id'],)).fetchone()[0])
            old = db.execute('SELECT * FROM db_creation_requests WHERE id=?',(request_id,)).fetchone()
            self.assertEqual(old['approval_state'],'VALID')
            frozen_plan = db.execute('SELECT * FROM db_creation_phase_plans WHERE id=?',(result['plan_id'],)).fetchone()
        frozen = runtime_profile.private_json(frozen_plan['private_ref'])
        self.assertIn('canonical_attempt',frozen); self.assertIsNone(frozen['canonical_attempt'])
        self.assertEqual(set(frozen['leaves'].values()),{'COMPLETE'})
        return result

    def current_creation(self, *, public_null=False):
        """Real OBSERVE, fresh RECOVER and a distinct ordinary FULL approval."""
        import database_creation_worker as worker
        def observe(current):
            if public_null:
                return self.public_null_observation(current)
            with control_store.connect() as db:
                rid = db.execute("SELECT approval_request_id FROM db_creation_phase_plans WHERE operation_id=? AND purpose='APPLY'",
                    (current['id'],)).fetchone()[0]
            request,root,plan,operator = worker.resolve(self.managed,rid,observation=True)
            operator.check_receipt(current)
            operator.verify_secret_identities(current)
            token = recovery._observation.set((self.managed,root['id'],plan['operation_id']))
            try:
                with recovery.observation_request(request):
                    operator.provision_databases(current,verify_only=True)
            finally:
                recovery._observation.reset(token)
            with control_store.connect() as db:
                self.assertEqual(dict(db.execute('SELECT * FROM db_creation_requests WHERE id=?',(rid,)).fetchone()),request)
                self.assertEqual(request['approval_state'],'VALID')
        rid = self.terminal_recovery(before_recovery=observe,public_null=public_null)
        with control_store.connect() as db:
            historical = [dict(row) for row in db.execute("SELECT * FROM db_creation_requests WHERE approval_state='REVOKED'")]
        self.assertTrue(historical)
        try:
            with self.terminal_backend() as (http,ipc,close):
                result = recovery.execute(self.managed,rid)
        finally:
            self.assertIsNone(recovery._terminal_observation.get())
        self.assertEqual(result['state'],'AWAITING_APPLY')
        http.assert_not_called(); ipc.assert_not_called()
        with control_store.connect() as db:
            for old in historical:
                self.assertEqual(dict(db.execute('SELECT * FROM db_creation_requests WHERE id=?',(old['id'],)).fetchone()),old)
            rows = db.execute("SELECT approval_state,dispatch_state FROM db_creation_requests WHERE plan_id IN (SELECT id FROM db_creation_phase_plans WHERE operation_id=?) AND action IN ('APPROVE','RECOVER_VERIFY')",(result['id'],)).fetchall()
            self.assertTrue(rows and all(tuple(row) == ('REVOKED','COMPLETE') for row in rows))
        approved,permit,current = self.approve_current_creation('separate-normal-full')
        self.assertIs(type(permit),auth.CreationPermit)
        self.terminal_request = approved['request_id']
        self.bound.check_receipt(current)
        return permit,current

    def approve_current_creation(self, key):
        detail = creations.get(self.alice,'admin',self.root_id)
        original_id = self.bound.receipt['verify_declaration']['original_plan_id']
        public = next(p for p in detail['plans'] if p['plan_id'] == original_id and p['purpose'] == 'APPLY')
        approved = creations.mutate(self.alice,'admin',self.root_id,'APPROVE',dict(expected_revision=detail['revision'],
            plan_id=public['plan_id'],operation_id=public['operation_id'],expected_operation_revision=public['operation_revision'],
            delivery_digest=public['delivery_digest']),key,phase='full')
        permit = auth.claim(self.config.enrollment,self.executor,approved['request_id'],owner_id=self.managed.hello['owner_instance_id'])
        return approved,permit,core.inspect(public['operation_id'],actor='alice')

    def test_current_creation_reverify_reuses_recovery_aux_ack_and_becomes_ready(self):
        self.finish_current_creation(*self.current_creation())

    def test_public_reconcile_null_attempt_recovers_then_normal_creation_reverifies(self):
        self.finish_current_creation(*self.current_creation(public_null=True))

    def test_public_reconcile_null_attempt_rejects_a_different_canonical_attempt(self):
        rid = self.terminal_recovery(before_recovery=self.public_null_observation,public_null=True)
        def mismatch():
            # Negative fixture corruption only: the positive NULL is produced
            # by PUBLIC RECONCILE above, never by a metadata edit.
            with control_store.connect() as db:
                db.execute('UPDATE operation_requests SET attempt_id=? WHERE operation_id=?',
                    ('unrelated-attempt',self.terminal_operation['id']))
        with self.terminal_backend(after_verify=mismatch) as (http,ipc,close):
            with self.assertRaisesRegex(core.OperationError,'STALE_PLAN'):
                recovery.execute(self.managed,rid)
        self.assertEqual(self.terminal_contexts,[])
        self.assertIsNone(self.terminal_frozen['canonical_attempt'])
        self.assertIsNone(recovery._terminal_observation.get())
        self.assertIsNone(recovery._observation_request.get())
        self.assertFalse(recovery.active()); self.assertFalse(recovery.observing())
        http.assert_not_called(); ipc.assert_not_called(); close.assert_called_once()
        self.assertEqual(core.inspect(self.terminal_operation['id'],actor='alice')['state'],'UNKNOWN')

    def finish_current_creation(self, permit, current):
        before = copy.deepcopy(self.bound.receipt)
        native_post = Operator.post
        types,auth_reads = [],[]
        def post(instance,request,**kwargs):
            self.assertIs(type(auth._permit.get()),auth.CreationPermit)
            self.assertFalse(recovery.active()); self.assertFalse(recovery.observing())
            types.append(type(auth._permit.get()))
            result = native_post(instance,request,**kwargs)
            self.assertTrue(Operator.oauth_expiry(result)>time.time(),'ordinary FULL reused expired authentication')
            auth_reads.append(instance.http_identity(request))
            return result
        with self.terminal_backend() as (http,ipc,close),patch.object(Operator,'post',post):
            # Keep the real verify callback bound: the SUCCEEDED core step must
            # invoke it again under the new ordinary CreationPermit.
            real_verify = Operator.verify
            with patch.object(Operator,'verify',autospec=True,side_effect=real_verify) as mandatory:
                result = self.apply(self.bound,current,permit)
        self.assertEqual(result['state'],'APPLIED')
        mandatory.assert_called_once()
        self.assertTrue(types and auth_reads)
        http.assert_not_called(); ipc.assert_not_called()
        after = runtime_profile.private_json(self.bound.receipt_path)
        for key in ('verify_http','read_auth','sql','runtime_activations'):
            self.assertTrue(canonical(before[key]) == canonical(after[key]))
        self.assertEqual(set(recovery.classify(self.bound).values()),{'COMPLETE'})
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM databases WHERE id=?',(self.new_id,)).fetchone()[0].upper(),'READY')

    def test_terminal_read_auth_reexpiry_holds_same_recovery_scope_and_restores_context(self):
        rid = self.terminal_recovery()
        original = copy.deepcopy(self.bound.receipt['verify_http'])
        clock = [time.time()+7200]
        minted_expiry = clock[0]+3600
        def expire_after_verify():
            clock[0] += 7200
        with self.terminal_backend(after_verify=expire_after_verify) as (unused,ipc,close), \
                patch('warehouse_operator.time.time',side_effect=lambda:clock[0]), \
                patch.object(Operator,'http',return_value=RecoveryTests.oauth_ack(minted_expiry)) as mint:
            with self.assertRaises(core.OperationError):
                recovery.execute(self.managed,rid)
        after = runtime_profile.private_json(self.bound.receipt_path)
        new = {key:value for key,value in after['read_auth'].items() if key.startswith(rid+'/')}
        self.assertEqual(len(new),len(original))
        self.assertEqual(mint.call_count,len(original))
        self.assertTrue(all(saved['context'] == rid and saved['state'] == 'ACK' for saved in new.values()))
        self.assertFalse(any(key.startswith('observe:'+rid+'/') for key in after['read_auth']))
        self.assertTrue(canonical(original) == canonical(after['verify_http']))
        self.assertEqual(core.inspect(self.terminal_operation['id'],actor='alice')['state'],'UNKNOWN')
        self.assertIsNone(recovery._terminal_observation.get())
        self.assertIsNone(recovery._observation_request.get())
        self.assertFalse(recovery.active()); self.assertFalse(recovery.observing())
        ipc.assert_not_called(); close.assert_called_once()

    def current_creation_oauth(self):
        import urllib.request
        key,entry = next(iter(self.bound.bundle['tofu']['databases'].items()))
        ref = entry['root_secret']
        data = self.bound.secret_objects[ref['namespace'],ref['name']]['data']
        basic = base64.b64encode(base64.b64decode(data['client_id'])+b':'+base64.b64decode(data['client_secret'])).decode()
        origin = self.bound.candidate_credentials()['databases'][key]['polaris']['endpoint_url']
        return urllib.request.Request(origin+'/api/catalog/v1/oauth/tokens',
            data=b'grant_type=client_credentials&scope=PRINCIPAL_ROLE%3AALL',headers={
                'Authorization':'Basic '+basic,'Polaris-Realm':entry['realm'],
                'Content-Type':'application/x-www-form-urlencoded'})

    def test_current_creation_expired_ack_mints_once_and_same_approval_reexpiry_holds(self):
        permit,current = self.current_creation()
        request = self.current_creation_oauth()
        old = copy.deepcopy(self.bound.receipt)
        future = time.time()+7200
        with patch('warehouse_operator.time.time',return_value=future),auth.delegated(permit),\
                patch.object(Operator,'http',return_value=RecoveryTests.oauth_ack(future+3600)) as mint:
            response = self.bound.post(request)
            self.assertTrue(self.bound.post(request) == response)
            with patch('warehouse_operator.time.time',return_value=future+7200),self.assertRaisesRegex(RuntimeError,'exhausted'):
                self.bound.post(request)
            self.assertEqual(mint.call_count,1)
        after = runtime_profile.private_json(self.bound.receipt_path)
        self.assertTrue(canonical(old['verify_http']) == canonical(after['verify_http']))
        self.assertTrue(all(after['read_auth'][key] == value for key,value in old['read_auth'].items()))
        self.assertEqual(set(after['read_auth'])-set(old['read_auth']),{permit.approval_request_id+'/'+self.bound.http_identity(request)})
        self.assertEqual(recovery.classify(self.bound),self.terminal_frozen['leaves'])

    def test_current_creation_unresolved_cross_scope_and_legacy_identity_boundaries(self):
        import urllib.request
        permit,current = self.current_creation()
        request = self.current_creation_oauth()
        original = copy.deepcopy(self.bound.receipt['verify_http'])
        # No P5 permit retains the existing cached-response contract.
        with patch.object(Operator,'http') as mint:
            self.assertTrue(self.bound.post(request) == original[self.bound.http_identity(request)]['response'])
            wrong = urllib.request.Request(request.full_url+'/other',data=request.data,headers=dict(request.header_items()))
            with auth.delegated(permit),self.assertRaises(RuntimeError): self.bound.post(wrong)
            mint.assert_not_called()
        with patch('warehouse_operator.time.time',return_value=time.time()+7200),auth.delegated(permit),\
                patch.object(Operator,'http',side_effect=ConnectionError('private response lost')) as mint:
            with self.assertRaisesRegex(RuntimeError,'HOLD'): self.bound.post(request)
            self.assertEqual(mint.call_count,1)
        import database_creation_worker as worker
        selected,root,plan,reader = worker.resolve(self.managed,permit.approval_request_id,observation=True)
        pending = self.bound.receipt['read_auth'][permit.approval_request_id+'/'+self.bound.http_identity(request)]
        with patch.object(Operator,'http') as mint:
            for state in ('INTENT','UNKNOWN','ACK'):
                pending.update(state=state,response={})
                self.bound.save()
                token = recovery._observation.set((self.managed,root['id'],plan['operation_id']))
                try:
                    with self.subTest(state=state),recovery.observation_request(selected),self.assertRaisesRegex(RuntimeError,'HOLD'):
                        self.bound.post(request)
                finally:
                    recovery._observation.reset(token)
                with self.assertRaises(core.OperationError): recovery.classify(self.bound)
            mint.assert_not_called()
        self.assertTrue(canonical(original) == canonical(self.bound.receipt['verify_http']))

    def test_current_creation_account_loss_before_valid_reuse_latches_stop(self):
        permit,current = self.current_creation()
        request = self.current_creation_oauth()
        self.accounts['executor']['disabled'] = True
        self.save_accounts()
        with patch.object(Operator,'http') as mint:
            with auth.delegated(permit),self.assertRaises(auth.AuthorityLost): self.bound.post(request)
            del self.accounts['executor']['disabled']; self.save_accounts()
            with auth.delegated(permit),self.assertRaises(core.OperationError): self.bound.post(request)
            mint.assert_not_called()
        with control_store.connect() as db:
            self.assertEqual(db.execute("SELECT state FROM db_creation_principal_gates WHERE principal_id='executor'").fetchone()[0],'STOPPED')

    def test_current_creation_account_loss_at_second_send_fence_keeps_intent(self):
        permit,current = self.current_creation()
        request = self.current_creation_oauth()
        attempt_key = permit.approval_request_id+'/'+self.bound.http_identity(request)
        save = Operator.save
        def revoke(instance):
            save(instance)
            if instance.receipt.get('read_auth',{}).get(attempt_key,{}).get('state') == 'INTENT':
                self.accounts['executor']['disabled'] = True
                self.save_accounts()
        with patch('warehouse_operator.time.time',return_value=time.time()+7200),auth.delegated(permit),\
                patch.object(Operator,'save',revoke),patch.object(Operator,'http') as mint:
            with self.assertRaises(auth.AuthorityLost): self.bound.post(request)
            mint.assert_not_called()
        self.assertEqual(runtime_profile.private_json(self.bound.receipt_path)['read_auth'][attempt_key]['state'],'INTENT')
        with self.assertRaises(core.OperationError): recovery.classify(self.bound)
        with control_store.connect() as db:
            self.assertEqual(db.execute("SELECT state FROM db_creation_principal_gates WHERE principal_id='executor'").fetchone()[0],'STOPPED')

    def test_read_auth_full_observation_uses_canonical_request_and_sealed_preparation(self):
        import urllib.request
        self.lost_full()
        accepted,permit,current = self.approve_claim('full','oauth-full-observation')
        with patch.object(Operator,'http',return_value=RecoveryTests.oauth_ack(time.time()-60)),\
                patch.object(self.bound,'activate_runtimes',side_effect=ConnectionError('unit before activation')):
            result = self.apply(self.bound,current,permit)
        self.assertEqual(result['state'],'UNKNOWN')
        with control_store.connect() as db:
            request = dict(db.execute('SELECT * FROM db_creation_requests WHERE id=?',(accepted['request_id'],)).fetchone())
        entry = next(iter(self.bound.bundle['tofu']['databases'].values()))
        ref = entry['root_secret']
        credential = self.bound.secret_objects[ref['namespace'],ref['name']]['data']
        basic = base64.b64encode(base64.b64decode(credential['client_id'])+b':'+base64.b64decode(credential['client_secret'])).decode()
        endpoint = next(iter(self.bound.candidate_credentials()['databases'].values()))['polaris']['endpoint_url']
        oauth = urllib.request.Request(endpoint+'/api/catalog/v1/oauth/tokens',
            data=b'grant_type=client_credentials&scope=PRINCIPAL_ROLE%3AALL',headers={
                'Authorization':'Basic '+basic,'Polaris-Realm':entry['realm'],
                'Content-Type':'application/x-www-form-urlencoded'})
        old = canonical(self.bound.receipt['verify_http'])
        leaves = recovery.classify(self.bound)
        entities = {realm:copy.deepcopy(api.entities) for realm,api in self.apis.items()}
        token = recovery._observation.set((self.managed,self.root_id,current['id']))
        try:
            with patch.object(Operator,'http',return_value=RecoveryTests.oauth_ack(time.time()+3600)) as send:
                with self.assertRaisesRegex(core.OperationError,'OBSERVATION_IDENTITY_REQUIRED'):
                    self.bound.post(oauth)
                send.assert_not_called()
                wrong = dict(request,creation_id='unrelated')
                with recovery.observation_request(wrong),self.assertRaises(core.OperationError):
                    self.bound.post(oauth)
                send.assert_not_called()
                with recovery.observation_request(request):
                    response = self.bound.post(oauth)
                    self.assertEqual(self.bound.post(oauth),response)
                self.assertEqual(send.call_count,1)
                detail = creations.get(self.alice,'admin',self.root_id)
                accepted = creations.mutate(self.alice,'admin',self.root_id,'RECONCILE',
                    dict(expected_revision=detail['revision']),'canonical-second-observation')
                with control_store.connect() as db:
                    second = dict(db.execute('SELECT * FROM db_creation_requests WHERE id=?',(accepted['request_id'],)).fetchone())
                with recovery.observation_request(second):
                    self.assertTrue(self.bound.post(oauth) == response)
                    original_binding = self.bound.binding
                    try:
                        self.bound.binding = dict(original_binding,generation_id='unrelated')
                        with self.assertRaises((RuntimeError,core.OperationError)): self.bound.post(oauth)
                    finally:
                        self.bound.binding = original_binding
                    headers = Path(self.managed.config.value['executor_auth_file'])
                    original_headers = headers.read_bytes()
                    try:
                        save_private(headers,dict(Authorization='Basic aW52YWxpZDppbnZhbGlk'))
                        with self.assertRaises(request_auth.AuthenticationError): self.bound.post(oauth)
                    finally:
                        headers.write_bytes(original_headers)
                    self.accounts['executor']['disabled'] = True
                    self.save_accounts()
                    with self.assertRaises(auth.AuthorityLost): self.bound.post(oauth)
                    del self.accounts['executor']['disabled']; self.save_accounts()
                    with self.assertRaises(core.OperationError): self.bound.post(oauth)
                self.assertEqual(send.call_count,1)
        finally:
            recovery._observation.reset(token)
        self.assertEqual(canonical(self.bound.receipt['verify_http']),old)
        self.assertEqual(recovery.classify(self.bound),leaves)
        self.assertEqual({realm:api.entities for realm,api in self.apis.items()},entities)
        self.assertEqual(set(self.bound.receipt['read_auth']),{'observe:'+request['id']+'/'+self.bound.http_identity(oauth)})

    def test_reconciled_secret_baseline_rejects_invalid_or_foreign_proof(self):
        self.lost_full()
        fresh,permit,current = self.approve_claim('full','matrix-full')
        with patch.object(self.bound,'declare_verify',side_effect=RuntimeError('unit stop before runtime')) as boundary:
            self.apply(self.bound,current,permit)
        boundary.assert_called_once()
        receipt = self.bound.receipt_path.read_bytes()
        operation_id = current['id']
        generation_id = current['generation_id']
        with control_store.connect() as db:
            original_after = db.execute('SELECT secret_after FROM operation_requests WHERE operation_id=?',(operation_id,)).fetchone()[0]
            proof = db.execute("SELECT evidence_ref FROM operation_steps WHERE operation_id=? AND step='apply_secrets'",(operation_id,)).fetchone()[0]
        rows = json.loads(original_after)
        wrong_uid = copy.deepcopy(rows)
        wrong_uid[0]['uid'] = 'recreated-before-capture'
        wrong_ref = copy.deepcopy(rows)
        wrong_ref[0]['version'] = 'foreign-version'
        absent = copy.deepcopy(rows)
        absent[0].update(exists=False,uid=None,resource_version=None,immutable=None)
        prior_generation = self.bound.prepared_predecessor(current)['binding']['generation_id']
        cases = [
            ('missing','operation_requests','secret_after',None,original_after),
            ('json','operation_requests','secret_after','malformed',original_after),
            ('shape','operation_requests','secret_after','{}',original_after),
            ('incomplete','operation_requests','secret_after',json.dumps(rows[1:]),original_after),
            ('ref','operation_requests','secret_after',json.dumps(wrong_ref),original_after),
            ('absent','operation_requests','secret_after',json.dumps(absent),original_after),
            ('captured-recreated-uid','operation_requests','secret_after',json.dumps(wrong_uid),original_after),
            ('unknown','operation_steps','state','UNKNOWN','SUCCEEDED'),
            ('pending','operation_steps','state','PENDING','SUCCEEDED'),
            ('normal','operation_steps','evidence_ref','operator-ack:normal',proof),
            ('missing-proof','operation_steps','evidence_ref',None,proof),
            ('prefix','operation_steps','evidence_ref','observed:secrets',proof),
            ('digest','operation_steps','evidence_ref','secrets-observed:'+'0'*64,proof),
            ('operation-actor','operations','actor','reader','alice'),
            ('request-actor','operation_requests','actor','reader','alice'),
            ('generation','operation_requests','generation_id',prior_generation,generation_id),
            ('phase','operation_requests','phase','bootstrap','full'),
            ('predecessor','operation_requests','predecessor_id',None,current['predecessor_id']),
            ('delivery','generation_plans','delivery_digest','foreign',current['delivery_digest']),
            ('manifest','generation_plans','manifest','{}',json.dumps(self.bound.bundle['manifest'])),
        ]
        for name,table,column,value,original in cases:
            key,identity = ('generation_id',generation_id) if table=='generation_plans' else (
                ('id',operation_id) if table=='operations' else ('operation_id',operation_id))
            suffix = " AND step='apply_secrets'" if table=='operation_steps' else ''
            sql = f'UPDATE {table} SET {column}=? WHERE {key}=?'+suffix
            with self.subTest(name=name):
                with control_store.connect() as db:
                    db.execute(sql,(value,identity))
                try:
                    if name in ('normal','missing-proof','prefix'):
                        self.assertIsNone(self.bound._reconciled_secret_after(current))
                    else:
                        with self.assertRaises(RuntimeError):
                            self.bound._reconciled_secret_after(current)
                    with self.assertRaises(RuntimeError):
                        self.bound.verify_secret_identities(current)
                finally:
                    with control_store.connect() as db:
                        db.execute(sql,(original,identity))
        for field,value in (('id','foreign'),('actor','reader'),('generation_id','foreign'),
                ('delivery_digest','foreign'),('phase','bootstrap'),('predecessor_id',None),('manifest',{})):
            with self.subTest(plan_field=field),self.assertRaises(RuntimeError):
                self.bound.verify_secret_identities(dict(current,**{field:value}))
        ref = self.bound.bundle['tofu']['databases'][self.new_id]['trino_secret']
        obj = self.cluster[('secret',ref['namespace'],ref['name'])]
        uid = obj['metadata']['uid']
        obj['metadata']['uid'] = 'recreated-after-capture'
        with self.assertRaises(RuntimeError):
            self.bound.verify_secret_identities(current)
        obj['metadata']['uid'] = uid
        self.bound.verify_secret_identities(current)
        changes = self.plans['full']['resource_changes']
        try:
            # Match the observer's precise selection rules, including a no-op
            # Secret, while ignoring data/non-Secret/no-after entries.
            selected = copy.deepcopy(changes)
            selected[0]['change']['actions'] = ['no-op']
            selected[0]['change']['before'] = selected[0]['change']['after']
            self.plans['full']['resource_changes'] = selected+[
                dict(mode='data',type='kubernetes_secret_v1',change=dict(actions=['read'],after={'ignored':True})),
                dict(type='other',change=dict(actions=['no-op'],after={'ignored':True})),
                dict(type='kubernetes_secret_v1',change=dict(actions=['no-op'],after=None))]
            self.bound.verify_secret_identities(current)
            self.plans['full']['resource_changes'] = list(reversed(changes))
            with self.assertRaises(RuntimeError):
                self.bound.verify_secret_identities(current)
            self.plans['full']['resource_changes'] = []
            with self.assertRaises(RuntimeError):
                self.bound.verify_secret_identities(current)
        finally:
            self.plans['full']['resource_changes'] = changes
        self.assertEqual(self.bound.receipt_path.read_bytes(),receipt)
        self.assertEqual(self.sends,['bootstrap','full'])
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT secret_after FROM operation_requests WHERE operation_id=?',(operation_id,)).fetchone()[0],original_after)
            self.assertNotEqual(db.execute('SELECT state FROM warehouse_controls').fetchone()[0],'OPEN')
            self.assertNotEqual(db.execute('SELECT state FROM databases WHERE id=?',(self.new_id,)).fetchone()[0],'READY')

    def test_uid_recreation_before_fresh_capture_never_reaches_runtime(self):
        self.lost_full()
        ref = self.bound.bundle['tofu']['databases'][self.new_id]['trino_secret']
        self.cluster[('secret',ref['namespace'],ref['name'])]['metadata']['uid'] = 'recreated-before-capture'
        fresh,permit,current = self.approve_claim('full','recreated-full')
        with patch.object(self.bound,'declare_verify',side_effect=AssertionError('no runtime declaration')) as boundary,\
                patch.object(self.bound,'activate_runtimes',side_effect=AssertionError('no runtime activation')) as activate,\
                patch.object(self.bound,'verify_trino',side_effect=AssertionError('no SQL')) as sql:
            result = self.apply(self.bound,current,permit)
        self.assertEqual(result['state'],'UNKNOWN')
        boundary.assert_not_called(); activate.assert_not_called(); sql.assert_not_called()
        with control_store.connect() as db:
            captured = json.loads(db.execute('SELECT secret_after FROM operation_requests WHERE operation_id=?',(current['id'],)).fetchone()[0])
            self.assertEqual(next(r['uid'] for r in captured if r['name']==ref['name']),'recreated-before-capture')
            self.assertNotEqual(db.execute('SELECT state FROM warehouse_controls').fetchone()[0],'OPEN')
            self.assertNotEqual(db.execute('SELECT state FROM databases WHERE id=?',(self.new_id,)).fetchone()[0],'READY')
        self.assertEqual(self.sends,['bootstrap','full'])


class LocalReleaseTests(BridgeFixture):
    def test_actual_current_auth_restore_alone_is_gated_and_release_confers_no_runtime(self):
        config = bridge.Configuration(self.path)
        principal = request_auth.authenticate_principal(runtime_profile.private_json(config.value['executor_auth_file']))
        auth.stop_principal(config.enrollment,principal,'alice','stop-local')
        account_path = Path(config.base.auth_file)
        original = account_path.read_bytes()
        accounts = runtime_profile.private_json(account_path)
        accounts['alice']['disabled'] = True
        save_private(account_path,accounts)
        with self.assertRaises(auth.AuthorityLost):
            recovery.release_local(str(self.path),'alice','disabled-release')
        account_path.write_bytes(original)
        with control_store.connect() as db:
            self.assertEqual(db.execute("SELECT state FROM db_creation_principal_gates WHERE principal_id='alice'").fetchone()[0],'STOPPED')
        with patch.object(bridge,'configure') as managed,patch.object(creations,'install_foundation') as foundation,\
                patch.object(auth,'claim') as claim,patch.object(Operator,'verify') as verify,\
                patch.object(bridge,'owner_get') as ipc:
            ack = recovery.release_local(str(self.path),'alice','explicit-local-release')
            self.assertEqual(recovery.release_local(str(self.path),'alice','explicit-local-release'),ack)
            for action in (managed,foundation,claim,verify,ipc):
                action.assert_not_called()
        with self.assertRaises(core.OperationError):
            bridge.runtime_instance()
        self.assertIsNone(creations._foundation)

    def test_wrong_credentials_do_not_fabricate_revocation_and_stale_gate_proof_is_rejected(self):
        config = bridge.Configuration(self.path)
        principal = request_auth.authenticate_principal(runtime_profile.private_json(config.value['executor_auth_file']))
        auth.stop_principal(config.enrollment,principal,'alice','stop-local')
        proof = recovery.observe_release(config,'alice')
        headers_path = Path(config.value['executor_auth_file'])
        original = headers_path.read_bytes()
        save_private(headers_path,dict(Authorization='Basic YWxpY2U6d3Jvbmc=',**{'X-Shirokuma-Role':'admin'}))
        with self.assertRaises(request_auth.AuthenticationError):
            recovery.release_local(str(self.path),'alice','invalid-password')
        headers_path.write_bytes(original)
        with control_store.connect() as db:
            self.assertEqual(db.execute("SELECT revision FROM db_creation_principal_gates WHERE principal_id='alice'").fetchone()[0],proof.gate_revision)
        with self.assertRaises(core.OperationError):
            auth.release_principal(config.enrollment,principal,'alice','wrong-revision',observation=replace(proof,gate_revision=proof.gate_revision+1))


class BootstrapObservationTests(CreationFixture):
    def test_completed_bootstrap_observation_reconciles_and_requires_fresh_approval(self):
        import database_creation_worker as worker
        ack = self.create()
        approval,plan,observations = self.approve(ack)
        permit = auth.claim(self.config,self.executor,approval['request_id'],owner_id='owner')
        operator = Operator.__new__(Operator)
        operator.phase = 'bootstrap'
        operator.config = {}
        operator.binding = dict(generation_id=plan['generation_id'],delivery_digest=plan['delivery_digest'])
        operator.receipt = dict(dispatches=['verify'],provisioning={'db':{'consumers':{'trino':{'client_secret':'test'}}}})
        operator.receipt_path = self.directory/'completion.json'
        configuration = dict(candidate=operator.binding)
        def completed_then_lost(operation):
            operator.verify(operation)
            raise ConnectionError('lost canonical verify acknowledgement')
        with patch.object(operator,'check_receipt'),patch.object(operator,'assert_intent'),\
                patch.object(operator,'candidate_credentials'),\
                patch.object(operator,'verification_configuration',return_value=configuration),\
                patch.object(operator,'provision_databases') as provision:
            with auth.delegated(permit):
                operation = core.apply(plan['operation_id'],actor='alice',expected_revision=plan['operation_revision'],
                    delivery_digest=plan['delivery_digest'],authorize=lambda actor:True,
                    observe_secrets=lambda p:copy.deepcopy(observations),execute_step=lambda s,p:dict(evidence_ref='ack:'+s),
                    verify=completed_then_lost)
            self.assertEqual(operation['state'],'UNKNOWN')
            saved = runtime_profile.private_json(operator.receipt_path)
            self.assertEqual(saved['bootstrap_complete']['proof']['operation_id'],operation['id'])
            headers = self.directory/'executor.json'
            save_private(headers,self.headers('executor'))
            managed = SimpleNamespace(require=Mock(),config=SimpleNamespace(enrollment=self.config,
                value={'executor_auth_file':str(headers)}))
            root = dict(id=ack['creation_id'],actor='alice')
            internal_plan = dict(id=plan['plan_id'],operation_id=operation['id'],phase='bootstrap')
            # Sealed-candidate loading is isolated here; real account authentication,
            # completion verification and canonical reconciliation remain active.
            def read_receipt(ignored):
                operator.receipt = runtime_profile.private_json(operator.receipt_path)
            with patch.object(worker,'resolve',return_value=({},root,internal_plan,operator)),\
                    patch.object(operator,'check_receipt',side_effect=read_receipt),\
                    patch.object(operator,'group',return_value=[]),\
                    patch.object(operator,'start_transports') as start,patch.object(operator,'close_transports') as close,\
                    patch.object(recovery,'original_unknown',side_effect=AssertionError('full-only recovery forbidden')),\
                    patch.object(operator,'activate_runtimes') as activation,patch.object(auth,'claim') as claim:
                # Neither absent completion nor failed current credential proof
                # can turn UNKNOWN into a strict full candidate.
                incomplete = copy.deepcopy(saved)
                del incomplete['bootstrap_complete']
                save_private(operator.receipt_path,incomplete)
                self.assertEqual(recovery.observe(managed,root,internal_plan,operator)['state'],'UNKNOWN')
                save_private(operator.receipt_path,saved)
                provision.side_effect = RuntimeError('Missing consumer credential or Secret')
                self.assertEqual(recovery.observe(managed,root,internal_plan,operator)['state'],'UNKNOWN')
                provision.side_effect = None
                result = recovery.observe(managed,root,internal_plan,operator)
                self.assertEqual(result['state'],'AWAITING_APPLY')
                self.assertEqual(start.call_count,3); self.assertEqual(close.call_count,3)
                activation.assert_not_called(); claim.assert_not_called()
                with control_store.connect() as db:
                    self.assertEqual(db.execute('SELECT approval_state FROM db_creation_requests WHERE id=?',
                        (approval['request_id'],)).fetchone()[0],'REVOKED')
                detail = creations.get(self.alice,'admin',ack['creation_id'])
                current = next(p for p in detail['plans'] if p['plan_id']==plan['plan_id'])
                fresh = creations.mutate(self.alice,'admin',ack['creation_id'],'APPROVE',
                    dict(expected_revision=detail['revision'],plan_id=current['plan_id'],operation_id=current['operation_id'],
                        expected_operation_revision=current['operation_revision'],delivery_digest=current['delivery_digest']),
                    'fresh-bootstrap-approval',phase='bootstrap')
                self.assertNotEqual(fresh['request_id'],approval['request_id'])
                self.accounts['executor']['disabled'] = True
                self.save_accounts()
                with self.assertRaises(auth.AuthorityLost):
                    recovery.observe(managed,root,internal_plan,operator)
            self.assertTrue(all(call.kwargs == {'verify_only':True} for call in provision.call_args_list))
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM operations WHERE id=?',(operation['id'],)).fetchone()[0],'AWAITING_APPLY')
            self.assertIsNone(db.execute("SELECT 1 FROM db_creation_phase_plans WHERE purpose='VERIFY_RECOVERY'").fetchone())
        detail = creations.get(self.alice,'admin',ack['creation_id'])
        self.assertNotEqual(detail['lifecycle'],'READY')
        with self.assertRaises(core.OperationError):
            auth.claim(self.config,self.executor,approval['request_id'],owner_id='owner')


class RecoveryTests(CreationFixture):
    def setUp(self):
        super().setUp()
        self.ack = self.create()
        approval,plan,observations = self.approve(self.ack)
        permit = auth.claim(self.config,self.executor,approval['request_id'],owner_id='owner')
        self.apply(plan,observations,permit,lambda p:dict(verified=True,evidence_ref='ack:bootstrap'))
        detail,linked,self.observations = self.prepare(self.ack,'full',plan['operation_id'])
        self.full = next(p for p in detail['plans'] if p['phase']=='full')
        approval = self.approve_full(detail)
        permit = auth.claim(self.config,self.executor,approval['request_id'],owner_id='owner')
        def interrupted(p):
            raise ConnectionError('lost activation acknowledgement')
        self.operation = self.apply(self.full,self.observations,permit,interrupted)
        self.assertEqual(self.operation['state'],'UNKNOWN')
        self.managed = SimpleNamespace(require=Mock(),hello=dict(owner_instance_id='owner'),
            config=SimpleNamespace(enrollment=self.config,owner=lambda role:dict(owner_instance_id='owner')))
        context = patch.object(bridge,'current',return_value=self.managed)
        context.start(); self.addCleanup(context.stop)
        binding = dict(generation_id=self.operation['generation_id'],delivery_digest=self.operation['delivery_digest'])
        self.receipt = dict(binding=binding,sql=dict(done=dict(dispatched=True,complete=True,backend_id='query',rows=[])),
            verify_declaration=dict(state='SEALED',binding=binding,original_claim_id=permit.claim_id,original_plan_id=self.full['plan_id'],
                original_request_id=approval['request_id'],sql=dict(done='INTENT',remaining='UNSENT')),
            verify_http=dict(token=dict(state='UNSENT')),
            runtime_activations=dict(workbench=dict(state='ACK',activation_id='workbench',owner=dict(owner_instance_id='owner'),leaves={}),
                native=dict(state='UNSENT',activation_id='native',owner=dict(owner_instance_id='owner'),leaves={k:'UNSENT' for k in ('stop','start','readiness')})))
        self.operator = SimpleNamespace(binding=self.receipt['binding'],receipt=self.receipt)

    def apply(self,plan,observations,permit,verify):
        with auth.delegated(permit):
            return core.apply(plan['operation_id'],actor='alice',expected_revision=plan['operation_revision'],
                delivery_digest=plan['delivery_digest'],authorize=lambda actor:True,
                observe_secrets=lambda p:copy.deepcopy(observations),execute_step=lambda s,p:dict(evidence_ref='ack:'+s),verify=verify)

    def approve_full(self,detail,key='full'):
        return creations.mutate(self.alice,'admin',self.ack['creation_id'],'APPROVE',
            dict(expected_revision=detail['revision'],plan_id=self.full['plan_id'],operation_id=self.full['operation_id'],
                expected_operation_revision=self.full['operation_revision'],delivery_digest=self.full['delivery_digest']),key,phase='full')

    def recovery_plan(self, key='recover'):
        self.receipt_path = control_store.root().resolve()/'warehouse-operator-receipts'/(self.operation['id']+'.json')
        save_private(self.receipt_path,self.receipt)
        self.path = self.directory/'recovery.json'
        with control_store.connect() as db:
            op = recovery.original_unknown(db,self.operation['id'])
        frozen = dict(version=1,purpose='VERIFY_RECOVERY',creation_id=self.ack['creation_id'],operation_id=self.operation['id'],
            original_plan_id=self.full['plan_id'],candidate=self.operator.binding,original_attempt=op['attempt_id'],
            canonical_attempt=op['attempt_id'],
            journal_path=str(self.receipt_path),journal_digest=digest(canonical(self.receipt)),
            leaves=recovery.classify(self.operator),
            transports=getattr(self.operator,'config',{}).get('managed_transport',{}).get('endpoints',{}),executable=True)
        if hasattr(self.probe,'config'):
            with control_store.connect() as db:
                root = dict(db.execute('SELECT * FROM db_creations').fetchone())
                original = dict(db.execute("SELECT * FROM db_creation_phase_plans WHERE phase='full' AND purpose='APPLY'").fetchone())
            frozen['owner_journals'] = recovery.owner_journal_digests(self.managed.config,root,original,self.operator)
        save_private(self.path,frozen)
        with auth.transaction() as db:
            pid = creations._attach_plan(db,self.config,self.ack['creation_id'],self.operation['id'],
                private_ref=str(self.path),public_summary=dict(code='RECOVERY_READY'),purpose='VERIFY_RECOVERY',
                recovery_digest=digest(canonical(frozen)))
        detail = creations.get(self.alice,'admin',self.ack['creation_id'])
        plan = next(p for p in detail['plans'] if p['plan_id']==pid)
        accepted = creations.mutate(self.alice,'admin',self.ack['creation_id'],'RECOVER_VERIFY',
            dict(expected_revision=detail['revision'],plan_id=pid,operation_id=plan['operation_id'],
                expected_operation_revision=plan['operation_revision'],delivery_digest=plan['delivery_digest'],
                recovery_digest=plan['recovery_digest']),key,phase='full')
        return accepted

    @staticmethod
    def oauth_ack(expiry):
        payload = base64.urlsafe_b64encode(json.dumps(dict(exp=expiry)).encode()).decode().rstrip('=')
        return dict(access_token='e30.'+payload+'.secret-token-sentinel')

    def oauth_operator(self, response=None, *, transport_count=1):
        """Real rendered bundle, private candidate map and canonical recovery claim."""
        import database_generation
        import catalog_admin
        import urllib.request
        from tests.test_database_generation import spec
        operator = Operator.__new__(Operator)
        operator.phase = 'full'
        operator.binding = self.operator.binding
        operator.bundle = database_generation.render(catalog_admin.export_spec('candidate-full',spec(1)))
        operator.secret_objects = {}
        identities = json.loads(operator.bundle['files']['identity-public.json'])
        databases = {}
        for key,entry in operator.bundle['tofu']['databases'].items():
            credential = dict(client_id='root',client_secret='secret-credential-sentinel',realm=entry['realm'])
            for name in ('root_secret','trino_secret','bootstrap_secret'):
                ref = entry[name]
                operator.secret_objects[ref['namespace'],ref['name']] = dict(data={
                    k:base64.b64encode(v.encode()).decode() for k,v in credential.items()})
            databases[key] = dict(polaris=dict(endpoint_url='http://127.0.0.1:18181',
                client_id=credential['client_id'],client_secret=credential['client_secret']),
                trino=dict(username=identities['databases'][key]['roles']['data_engineer'],password='secret-password-sentinel'))
        candidate = self.directory/'oauth-candidate.json'
        save_private(candidate,dict(version=1,generation_id=operator.binding['generation_id'],databases=databases,
            trino=dict(endpoint_url='https://127.0.0.1:18443',ca_file=str(self.directory/'ca'))))
        operator.config = dict(candidate_credentials=str(candidate),kubeconfig='/private/unit-kubeconfig',
            managed_transport=dict(endpoints={'polaris-unit-'+str(i):'http://127.0.0.1:'+str(18181+i)
                for i in range(transport_count)}))
        realm = next(iter(operator.bundle['tofu']['databases'].values()))['realm']
        request = urllib.request.Request('http://127.0.0.1:18181/api/catalog/v1/oauth/tokens',
            data=b'grant_type=client_credentials&scope=PRINCIPAL_ROLE%3AALL',headers={
                'Authorization':'Basic '+base64.b64encode(b'root:secret-credential-sentinel').decode(),
                'Polaris-Realm':realm,'Content-Type':'application/x-www-form-urlencoded'})
        self.receipt['verify_http'] = {operator.http_identity(request):dict(state='ACK',
            response=response if response is not None else self.oauth_ack(time.time()-60))}
        operator.receipt = self.receipt
        self.operator = operator
        accepted = self.recovery_plan()
        operator.receipt_path = self.receipt_path
        operator.http = Mock(return_value=self.oauth_ack(time.time()+3600))
        permit = self.running_oauth_permit(accepted)
        with recovery.delegated(permit):
            fixture_owned_transports(operator,self.operation)
        return operator,request,permit

    def running_oauth_permit(self, accepted):
        permit = recovery.claim(self.managed,accepted['request_id'],self.executor)
        save_private(recovery.intent_path(permit),dict(version=1,request_id=permit.approval_request_id,
            claim_id=permit.claim_id,original_operation_id=permit.operation_id,recovery_digest=permit.recovery_digest,state='RUNNING'))
        return permit

    def test_read_auth_valid_cached_responses_need_no_owned_transports(self):
        operator,request,permit = self.oauth_operator(self.oauth_ack(time.time()+3600))
        _NATIVE_CLOSE(operator)
        before = operator.receipt_path.read_bytes()
        with recovery.delegated(permit): operator.post(request)
        operator.http.assert_not_called(); self.assertEqual(operator.receipt_path.read_bytes(),before)
        key = operator.http_identity(request)
        self.receipt['verify_http'][key]['response'] = self.oauth_ack(time.time()-60)
        self.receipt['read_auth'] = {'prior/'+key:dict(context='prior',http_identity=key,state='ACK',
            response=self.oauth_ack(time.time()+3600))}
        operator.save()
        before = operator.receipt_path.read_bytes()
        with recovery.delegated(permit): operator.post(request)
        operator.http.assert_not_called(); self.assertEqual(operator.receipt_path.read_bytes(),before)

    def test_token_fixture_routes_only_declared_posts_and_keeps_canonical_reads(self):
        import sys
        operator,request,permit = self.oauth_operator()
        canonical = Mock(return_value=SimpleNamespace(open=lambda *a,**k:io.BytesIO(b'{"canonical":true}')))
        wire = Mock(return_value=SimpleNamespace(open=lambda *a,**k:io.BytesIO(b'{"wire":true}')))
        with patch.object(_urllib_request,'build_opener',canonical), \
                patch.object(sys.modules[__name__],'_NATIVE_OPENER',wire):
            get = _urllib_request.Request(request.full_url+'/readback')
            self.assertEqual(native_token_http(operator,get),dict(canonical=True))
            wire.assert_not_called(); canonical.assert_called_once()
            self.assertEqual(native_token_http(operator,request),dict(wire=True))
            wire.assert_called_once(); self.assertEqual(canonical.call_count,1)
        with patch.object(Operator,'command',return_value='{"canonical":true}') as command, \
                token_forward_servers(self,operator.config['managed_transport']['endpoints'],time.time()+3600):
            self.assertEqual(operator.kubectl('get','service','polaris-unit-0','-n','shirokuma-dev','-o','json'),'{}')
            command.assert_not_called()
            self.assertEqual(operator.kubectl('-n','flux-system','get','Bucket','source','-o','json'),'{"canonical":true}')
            command.assert_called_once()
            operator.kubectl('get','service','polaris-unit-0','-n','shirokuma-dev','-o','json',payload='{}')
            self.assertEqual(command.call_args.kwargs,dict(payload='{}'))

    def test_read_auth_owned_barrier_rejects_duplicate_ready_child_before_intent(self):
        operator,request,permit = self.oauth_operator(transport_count=2)
        children = operator._transports
        self.assertEqual(len({id(child) for child in children}),2)
        endpoints = list(operator._ready_transports)
        operator._ready_transports[endpoints[1]] = (operator,children[0])
        original = copy.deepcopy(self.receipt['verify_http'])
        with recovery.delegated(permit),self.assertRaisesRegex(RuntimeError,'Owned sealed'):
            operator.post(request)
        operator.http.assert_not_called()
        self.assertNotIn('read_auth',self.receipt)
        self.assertEqual(self.receipt['verify_http'],original)

    def test_read_auth_owned_barrier_rejects_missing_partial_dead_wrong_and_copied_handles(self):
        operator,request,permit = self.oauth_operator()
        original = copy.deepcopy(self.receipt['verify_http'])
        ready,children = operator._ready_transports,operator._transports
        endpoint = next(iter(ready))
        alternate = Operator.__new__(Operator)
        variants = [{}, {endpoint:(alternate,children[0])},
            {'http://127.0.0.1:19999':(operator,children[0])}]
        with recovery.delegated(permit):
            for proof in variants:
                operator._ready_transports = proof
                with self.subTest(proof_count=len(proof)),self.assertRaisesRegex(RuntimeError,'Owned sealed'):
                    operator.post(request)
                self.assertNotIn('read_auth',self.receipt)
            operator._ready_transports = ready
            children[0].poll.return_value = 1
            with self.assertRaisesRegex(RuntimeError,'Owned sealed'): operator.post(request)
            children[0].poll.return_value = None
            operator._transports = []
            with self.assertRaisesRegex(RuntimeError,'Owned sealed'): operator.post(request)
            operator._transports = children
            clone = copy.copy(operator)
            with self.assertRaisesRegex(RuntimeError,'Owned sealed'): clone.post(request)
        operator.http.assert_not_called()
        self.assertEqual(self.receipt['verify_http'],original)
        self.assertNotIn('read_auth',self.receipt)
        _NATIVE_CLOSE(operator)
        self.assertEqual(operator._ready_transports,{})

    def assert_safe_auth_intent(self, operator, stage, exception):
        saved = next(iter(runtime_profile.private_json(operator.receipt_path)['read_auth'].values()))
        self.assertEqual(saved['state'],'INTENT')
        self.assertNotIn('response',saved)
        self.assertEqual(saved['diagnostic'],dict(stage=stage,exception=exception,code=None))
        self.assertNotIn('sentinel',json.dumps(saved['diagnostic']))
        with self.assertRaises(core.OperationError): recovery.classify(operator)

    def test_read_auth_http_failure_diagnostic_is_durable_and_sanitized(self):
        operator,request,permit = self.oauth_operator()
        original = copy.deepcopy(self.receipt['verify_http'])
        operator.http.side_effect = ConnectionError('secret-http-sentinel')
        output = io.StringIO()
        with redirect_stdout(output),recovery.delegated(permit),self.assertRaises(RuntimeError) as error:
            operator.post(request)
        self.assertNotIn('sentinel',str(error.exception)+output.getvalue())
        self.assert_safe_auth_intent(operator,'http','ConnectionError')
        with recovery.delegated(permit),self.assertRaises(RuntimeError): operator.post(request)
        self.assertEqual(operator.http.call_count,1)
        self.assertEqual(self.receipt['verify_http'],original)

    def test_read_auth_http_failure_diagnostic_save_cannot_mask_redaction(self):
        operator,request,permit = self.oauth_operator()
        operator.http.side_effect = RuntimeError('secret-http-sentinel')
        native_save = Operator.save
        def fail_diagnostic(instance):
            if any('diagnostic' in a for a in instance.receipt.get('read_auth',{}).values()):
                raise OSError('secret-save-sentinel')
            native_save(instance)
        with recovery.delegated(permit),patch.object(Operator,'save',fail_diagnostic),self.assertRaises(RuntimeError) as error:
            operator.post(request)
        self.assertEqual(str(error.exception),'Read authentication outcome unavailable; HOLD')
        saved = next(iter(runtime_profile.private_json(operator.receipt_path)['read_auth'].values()))
        self.assertEqual(saved['state'],'INTENT'); self.assertNotIn('response',saved)
        diagnostic = next(iter(operator.receipt['read_auth'].values()))['diagnostic']
        self.assertEqual(diagnostic,dict(stage='http',exception='RuntimeError',code=None))
        with recovery.delegated(permit),self.assertRaises(RuntimeError): operator.post(request)
        self.assertEqual(operator.http.call_count,1)

    def test_read_auth_malformed_wire_response_retains_intent_and_safe_diagnostic(self):
        operator,request,permit = self.oauth_operator()
        operator.http.return_value = dict(access_token='secret-wire-sentinel')
        old = copy.deepcopy(self.receipt['verify_http'])
        with recovery.delegated(permit),self.assertRaisesRegex(RuntimeError,'HOLD') as error: operator.post(request)
        self.assertNotIn('sentinel',str(error.exception))
        self.assert_safe_auth_intent(operator,'response-validity','RuntimeError')
        with recovery.delegated(permit),self.assertRaises(RuntimeError): operator.post(request)
        self.assertEqual(operator.http.call_count,1); self.assertEqual(self.receipt['verify_http'],old)

    def test_read_auth_expired_wire_response_retains_intent_and_safe_diagnostic(self):
        operator,request,permit = self.oauth_operator()
        operator.http.return_value = self.oauth_ack(time.time()-1)
        with recovery.delegated(permit),self.assertRaisesRegex(RuntimeError,'HOLD'): operator.post(request)
        self.assert_safe_auth_intent(operator,'response-validity','RuntimeError')
        with recovery.delegated(permit),self.assertRaises(RuntimeError): operator.post(request)
        self.assertEqual(operator.http.call_count,1)

    def test_read_auth_final_guard_diagnostic_uses_actual_revocation_and_latches_stop(self):
        operator,request,permit = self.oauth_operator()
        native_save = Operator.save
        def revoke(instance):
            native_save(instance)
            if instance.receipt.get('read_auth'):
                self.accounts['executor']['disabled'] = True; self.save_accounts()
        with recovery.delegated(permit),patch.object(Operator,'save',revoke),self.assertRaises(auth.AuthorityLost):
            operator.post(request)
        saved = next(iter(runtime_profile.private_json(operator.receipt_path)['read_auth'].values()))
        self.assertEqual(saved['state'],'INTENT')
        self.assertEqual(saved['diagnostic'],dict(stage='final-guard',exception='AuthorityLost',code='ADMIN_REQUIRED'))
        operator.http.assert_not_called()
        del self.accounts['executor']['disabled']; self.save_accounts()
        with recovery.delegated(permit),self.assertRaises(core.OperationError): recovery.assert_original(self.operation)
        with control_store.connect() as db:
            self.assertEqual(db.execute("SELECT state FROM db_creation_principal_gates WHERE principal_id='executor'").fetchone()[0],'STOPPED')

    def test_read_auth_valid_ack_reused_without_send(self):
        operator,request,permit = self.oauth_operator(self.oauth_ack(time.time()+3600))
        before = operator.receipt_path.read_bytes()
        with recovery.delegated(permit):
            self.assertEqual(operator.post(request),next(iter(self.receipt['verify_http'].values()))['response'])
        operator.http.assert_not_called()
        self.assertEqual(operator.receipt_path.read_bytes(),before)

    def test_read_auth_expired_ack_one_send_preserves_original_and_domain(self):
        operator,request,permit = self.oauth_operator()
        old = canonical(self.receipt['verify_http'])
        original_receipt = canonical(self.receipt)
        leaves = recovery.classify(operator)
        def send(sent, **kwargs):
            current = runtime_profile.private_json(operator.receipt_path)
            self.assertEqual(next(iter(current['read_auth'].values()))['state'],'INTENT')
            self.assertEqual(operator.http_identity(sent),operator.http_identity(request))
            return self.oauth_ack(time.time()+3600)
        operator.http.side_effect = send
        with recovery.delegated(permit):
            result = operator.post(request)
            self.assertEqual(operator.post(request),result)
            with self.assertRaises(core.OperationError):
                recovery.authorize_leaf(self.operation,dict(kind='verify-http',key=operator.http_identity(request)),self.receipt)
        self.assertEqual(operator.http.call_count,1)
        self.assertEqual(canonical(self.receipt['verify_http']),old)
        self.assertEqual(canonical({k:v for k,v in self.receipt.items() if k != 'read_auth'}),original_receipt)
        self.assertEqual(recovery.classify(operator),leaves)
        self.assertEqual(set(self.receipt['read_auth']),{permit.approval_request_id+'/'+operator.http_identity(request)})

    def test_read_auth_same_approval_expired_refresh_stops_new_approval_may_send(self):
        operator,request,permit = self.oauth_operator()
        with recovery.delegated(permit):
            operator.post(request)
            with patch('warehouse_operator.time.time',return_value=time.time()+7200):
                with self.assertRaises(RuntimeError): operator.post(request)
        self.assertEqual(operator.http.call_count,1)
        fresh = self.running_oauth_permit(self.recovery_plan('explicit-second-recovery'))
        future = time.time()+7200
        operator.http.return_value = self.oauth_ack(future+3600)
        with patch('warehouse_operator.time.time',return_value=future),recovery.delegated(fresh): operator.post(request)
        self.assertEqual(operator.http.call_count,2)
        self.assertEqual(len(self.receipt['read_auth']),2)

    def test_read_auth_cross_scope_valid_aux_ack_reused_after_current_guard(self):
        operator,request,permit = self.oauth_operator()
        with recovery.delegated(permit): response = operator.post(request)
        original = canonical(self.receipt)
        fresh = self.running_oauth_permit(self.recovery_plan('explicit-reuse'))
        operator.http.reset_mock()
        with recovery.delegated(fresh): self.assertEqual(operator.post(request),response)
        operator.http.assert_not_called()
        self.assertEqual(canonical(self.receipt),original)
        self.accounts['executor']['disabled'] = True
        self.save_accounts()
        with recovery.delegated(fresh),self.assertRaises(auth.AuthorityLost): operator.post(request)
        del self.accounts['executor']['disabled']; self.save_accounts()
        with recovery.delegated(fresh),self.assertRaises(core.OperationError): operator.post(request)
        operator.http.assert_not_called()
        with control_store.connect() as db:
            self.assertEqual(db.execute("SELECT state FROM db_creation_principal_gates WHERE principal_id='executor'").fetchone()[0],'STOPPED')

    def test_read_auth_valid_aux_does_not_bypass_other_unresolved_attempt(self):
        operator,request,permit = self.oauth_operator()
        with recovery.delegated(permit): operator.post(request)
        fresh = self.running_oauth_permit(self.recovery_plan('explicit-resolved-before-loss'))
        operator.http.reset_mock()
        key = operator.http_identity(request)
        for state in ('INTENT','UNKNOWN','ACK'):
            self.receipt['read_auth']['unresolved/'+key] = dict(state=state,context='unresolved',
                http_identity=key,response={})
            operator.save()
            with recovery.delegated(fresh),self.assertRaises(RuntimeError): operator.post(request)
            with self.assertRaises(core.OperationError): recovery.classify(operator)
        operator.http.assert_not_called()

    def test_read_auth_resolved_freeze_remains_compatible_with_pre_refresh_reader(self):
        operator,request,permit = self.oauth_operator()
        old = old_recovery_reader()
        leaves = old.classify(operator)
        original_http = canonical(self.receipt['verify_http'])
        with recovery.delegated(permit): operator.post(request)
        self.assertEqual(old.classify(operator),leaves)
        self.assertTrue(canonical(self.receipt['verify_http']) == original_http)
        self.projection_material()
        self.recovery_plan('old-reader-final-freeze')
        frozen = runtime_profile.private_json(self.path)
        self.assertEqual(old.classify(operator),frozen['leaves'])
        self.assertEqual(recovery.classify(operator),frozen['leaves'])
        self.assertTrue(frozen['journal_digest'] == digest(canonical(self.receipt)))
        self.assertTrue(frozen['journal_digest'] != digest(canonical({k:v for k,v in self.receipt.items() if k != 'read_auth'})))
        with control_store.connect() as db:
            root = dict(db.execute('SELECT * FROM db_creations').fetchone())
            plan = dict(db.execute("SELECT * FROM db_creation_phase_plans WHERE purpose='VERIFY_RECOVERY' ORDER BY sequence DESC LIMIT 1").fetchone())
            original = dict(db.execute("SELECT * FROM db_creation_phase_plans WHERE phase='full' AND purpose='APPLY'").fetchone())
            self.assertTrue(old.projection_ready(db,root,plan,original))
            self.assertTrue(recovery.projection_ready(db,root,plan,original))
            self.receipt = operator.receipt
            attempt = next(iter(self.receipt['read_auth'].values()))
            for state in ('INTENT','UNKNOWN'):
                attempt['state'] = state
                operator.save()
                self.assertFalse(old.projection_ready(db,root,plan,original))
                self.assertFalse(recovery.projection_ready(db,root,plan,original))
                with self.assertRaises(core.OperationError): recovery.classify(operator)
        with self.assertRaises(core.OperationError): self.recovery_plan('unresolved-not-executable')

    def test_read_auth_lost_response_holds_across_fresh_approval(self):
        operator,request,permit = self.oauth_operator()
        operator.http.side_effect = ConnectionError('secret-credential-sentinel secret-token-sentinel')
        output = io.StringIO()
        with redirect_stdout(output),recovery.delegated(permit):
            with self.assertRaises(RuntimeError) as error: operator.post(request)
            with self.assertRaises(RuntimeError): operator.post(request)
            with self.assertRaises(core.OperationError):
                recovery.authorize_leaf(self.operation,dict(kind='sql-post',receipt_key='remaining'),self.receipt)
        self.assertNotIn('sentinel',str(error.exception)+output.getvalue())
        with self.assertRaises(core.OperationError): recovery.classify(operator)
        with self.assertRaises(core.OperationError): self.recovery_plan('explicit-but-held')
        # Even an explicitly supplied different current approval cannot bypass
        # the unresolved auxiliary send; canonical planning also refuses it.
        with recovery.delegated(replace(permit,approval_request_id='fresh-explicit')):
            with self.assertRaises(RuntimeError): operator.post(request)
        self.assertEqual(operator.http.call_count,1)

    def test_read_auth_original_unknown_and_malformed_expiry_never_send(self):
        operator,request,permit = self.oauth_operator()
        saved = next(iter(self.receipt['verify_http'].values()))
        for state in ('INTENT','UNKNOWN','ACK'):
            saved['state'] = state
            saved['response'] = dict(access_token='secret-token-sentinel')
            with self.subTest(state=state),recovery.delegated(permit),self.assertRaises(RuntimeError):
                operator.post(request)
        operator.http.assert_not_called()
        saved['state'] = 'ACK'
        fresh = self.running_oauth_permit(self.recovery_plan('fresh-malformed'))
        with recovery.delegated(fresh),self.assertRaises(RuntimeError): operator.post(request)
        operator.http.assert_not_called()

    def test_read_auth_expiry_must_be_positive_unambiguous_number(self):
        operator,request,permit = self.oauth_operator()
        saved = next(iter(self.receipt['verify_http'].values()))
        malformed = [None,{},dict(access_token=None),dict(access_token='malformed.sentinel.token'),
            *[self.oauth_ack(value) for value in (None,True,'0',0,-1,float('nan'),float('inf'))]]
        for response in malformed:
            saved['response'] = response
            output = io.StringIO()
            with self.subTest(response_type=type(response).__name__),redirect_stdout(output),recovery.delegated(permit):
                with self.assertRaises(RuntimeError) as error: operator.post(request)
            self.assertNotIn('sentinel',str(error.exception)+output.getvalue())
        operator.http.assert_not_called()

    def test_read_auth_changed_request_denied_no_issuance(self):
        import urllib.request
        operator,request,permit = self.oauth_operator()
        changes = [dict(url=request.full_url+'/other'),dict(data=b'grant_type=password'),
            dict(headers={'Authorization':'Basic changed'}),dict(method='PUT')]
        for change in changes:
            values = dict(url=request.full_url,data=request.data,headers=dict(request.header_items()))
            values.update(change)
            changed = urllib.request.Request(**values)
            # A forged journal entry alone is insufficient: sealed material
            # and the fixed method/body/credential still have to match.
            self.receipt['verify_http'][operator.http_identity(changed)] = dict(state='ACK',response=self.oauth_ack(time.time()-60))
            with self.subTest(change=tuple(change)),recovery.delegated(permit),self.assertRaises(RuntimeError):
                operator.post(changed)
        operator.http.assert_not_called()
        self.assertNotIn('read_auth',self.receipt)

    def test_read_auth_current_account_loss_before_send_latches_stop(self):
        operator,request,permit = self.oauth_operator()
        self.accounts['executor']['disabled'] = True
        self.save_accounts()
        with recovery.delegated(permit),self.assertRaises(auth.AuthorityLost): operator.post(request)
        operator.http.assert_not_called()
        del self.accounts['executor']['disabled']; self.save_accounts()
        with recovery.delegated(permit),self.assertRaises(core.OperationError): operator.post(request)
        with control_store.connect() as db:
            self.assertEqual(db.execute("SELECT state FROM db_creation_principal_gates WHERE principal_id='executor'").fetchone()[0],'STOPPED')
        operator.http.assert_not_called()

    def test_read_auth_authority_loss_after_intent_before_send_holds(self):
        operator,request,permit = self.oauth_operator()
        native_save = Operator.save
        def revoke(instance):
            native_save(instance)
            if instance.receipt.get('read_auth'):
                self.accounts['executor']['disabled'] = True
                self.save_accounts()
        with patch.object(Operator,'save',revoke),recovery.delegated(permit),self.assertRaises(auth.AuthorityLost):
            operator.post(request)
        operator.http.assert_not_called()
        self.assertEqual(next(iter(runtime_profile.private_json(operator.receipt_path)['read_auth'].values()))['state'],'INTENT')
        with self.assertRaises(core.OperationError): recovery.classify(operator)
        del self.accounts['executor']['disabled']; self.save_accounts()
        with recovery.delegated(permit),self.assertRaises(RuntimeError): operator.post(request)
        with control_store.connect() as db:
            self.assertEqual(db.execute("SELECT state FROM db_creation_principal_gates WHERE principal_id='executor'").fetchone()[0],'STOPPED')
        operator.http.assert_not_called()

    def projection_material(self):
        """Keep local owner journals real; isolate immutable candidate loading."""
        import database_creation_worker as worker
        import database_creation_preparation as preparation
        with control_store.connect() as db:
            root = dict(db.execute('SELECT * FROM db_creations').fetchone())
            original = dict(db.execute("SELECT * FROM db_creation_phase_plans WHERE phase='full' AND purpose='APPLY'").fetchone())
        config = self.managed.config
        output = self.directory/'prepared'
        directory = output/root['id']/'full'/str(original['sequence'])
        directory.mkdir(mode=0o700,parents=True)
        save_private(directory/'journal.json',dict(binding=self.operator.binding))
        config.preparation = lambda:SimpleNamespace(output_root=output)
        config.path,config.binding = str(self.directory/'enrollment.json'),'enrollment-binding'
        config.value = dict(state_dir=str(control_store.root().resolve()),owners={role:dict(directory=str(self.directory/role))
            for role in ('workbench','native')})
        owners = {role:dict(owner_instance_id='owner-'+role,old_runtime_instance_id='old-'+role,
            candidate_runtime_instance_id='candidate-'+role) for role in ('workbench','native')}
        records = {role:dict(owner_instance_id=owners[role]['owner_instance_id'],
            runtime_instance_id=owners[role]['candidate_runtime_instance_id' if role == 'workbench' else 'old_runtime_instance_id']) for role in owners}
        config.owner = lambda role:records[role]
        self.probe.config = config
        self.operator.config = dict(preparation_inputs={config.path:config.binding},runtime_profile=str(directory/'runtime-profile.json'),
            managed_handoff=dict(owners=owners,candidate_id='candidate',runtime_attestation={'generation':'candidate'}))
        for context in (patch('warehouse_operator.Operator',return_value=self.operator),
                patch.object(preparation.Preparation,'_validate_seal'),
                patch.object(runtime_profile,'private_binding',return_value='sealed-profile')):
            context.start(); self.addCleanup(context.stop)
        self.owner_journals = {}
        request_id = self.receipt['verify_declaration']['original_request_id']
        for role,saved in self.receipt['runtime_activations'].items():
            descriptor = worker.descriptor(dict(id=request_id),root,original,self.operator,role)
            saved.update(owner=owners[role],activation_id=descriptor['activation_id'])
            sealed = Path(config.value['state_dir'])/'activation-plans'/request_id/(descriptor['activation_id']+'.json')
            sealed.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            save_private(sealed,descriptor)
            path = self.directory/role/'activations'/(descriptor['activation_id']+'.json')
            path.parent.mkdir(mode=0o700,parents=True)
            leaves = {leaf:dict(state='ACK',intent_id=str(uuid.uuid4())) if role == 'workbench' else dict(state='UNSENT')
                for leaf in ('stop','start','readiness')}
            save_private(path,dict(descriptor=descriptor,state=saved['state'],**leaves))
            self.owner_journals[role] = path

    def test_public_recovery_requires_current_frozen_receipt_attempt_and_latest_plan(self):
        self.projection_material()
        accepted = self.recovery_plan()
        with control_store.connect() as db:
            db.execute("UPDATE db_creation_requests SET approval_state='REVOKED' WHERE id=?",(accepted['request_id'],))
        def detail():
            return creations.get(self.alice,'admin',self.ack['creation_id'])
        current = detail()
        self.assertEqual(current['progress_code'],'RECONCILE_REQUIRED')
        self.assertIn('recover-verify',current['next_actions'])
        self.assertEqual(next(p for p in current['plans'] if p['purpose']=='VERIFY_RECOVERY')['public_summary']['code'],'RECOVERY_READY')
        changed = copy.deepcopy(self.receipt)
        changed['verify_http']['token']['state'] = 'INTENT'
        save_private(self.receipt_path,changed)
        self.assertNotIn('recover-verify',detail()['next_actions'])
        save_private(self.receipt_path,self.receipt)
        with control_store.connect() as db:
            db.execute("UPDATE operation_requests SET attempt_id='changed' WHERE operation_id=?",(self.operation['id'],))
        self.assertNotIn('recover-verify',detail()['next_actions'])
        with control_store.connect() as db:
            db.execute('UPDATE operation_requests SET attempt_id=? WHERE operation_id=?',
                (self.receipt['verify_declaration']['original_claim_id'],self.operation['id']))
            db.execute("UPDATE db_creation_phase_plans SET recovery_digest='changed' WHERE purpose='VERIFY_RECOVERY'")
        self.assertNotIn('recover-verify',detail()['next_actions'])
        # Read-only projection does not alter canonical UNKNOWN or issue work.
        self.assertEqual(detail()['plans'][1]['state'],'UNKNOWN')

    def test_latest_full_review_running_and_stale_replan_ignore_older_review(self):
        with control_store.connect() as db:
            db.execute("UPDATE db_creation_requests SET approval_state='REVOKED' WHERE approval_state='VALID'")
            db.execute("UPDATE operations SET state='AWAITING_APPLY' WHERE id IN (SELECT operation_id FROM db_creation_phase_plans WHERE phase='bootstrap')")
        for state,step,progress,action in [('AWAITING_APPLY',None,'FULL_REVIEW_REQUIRED','approve'),
                ('APPLYING','verify','VERIFYING',None),('DRAINING','apply_secrets','APPLYING',None),
                ('VERIFIED_STALE',None,'REPLAN_REQUIRED','replan')]:
            with self.subTest(state=state),control_store.connect() as db:
                if state == 'VERIFIED_STALE':
                    db.execute("UPDATE operations SET state='PREPARED' WHERE id IN (SELECT operation_id FROM db_creation_phase_plans WHERE phase='bootstrap')")
                    # A genuine verified-stale full handoff retains APPLYING
                    # ownership; UNKNOWN control is an unresolved boundary.
                    db.execute("UPDATE warehouse_controls SET state='APPLYING' WHERE operation_id=?",(self.operation['id'],))
                    db.execute('UPDATE operation_requests SET attempt_id=NULL WHERE operation_id=?',(self.operation['id'],))
                db.execute('UPDATE operations SET state=? WHERE id=?',(state,self.operation['id']))
                db.execute("UPDATE operation_steps SET state='SUCCEEDED' WHERE operation_id=?",(self.operation['id'],))
                if step:
                    db.execute("UPDATE operation_steps SET state='RUNNING' WHERE operation_id=? AND step=?",(self.operation['id'],step))
            current = creations.get(self.alice,'admin',self.ack['creation_id'])
            self.assertEqual(current['progress_code'],progress)
            if action:
                self.assertIn(action,current['next_actions'])
            else:
                self.assertEqual(current['next_actions'],['withdraw'])
            self.assertEqual(current['plans'][1]['public_summary']['impact'],'FULL_RUNTIME_HANDOFF')

    def test_public_reconcile_completes_from_durable_frozen_without_root_success(self):
        self.projection_material()
        accepted = self.recovery_plan()
        current = creations.get(self.alice,'admin',self.ack['creation_id'])
        ack = creations.mutate(self.alice,'admin',self.ack['creation_id'],'RECONCILE',
            dict(expected_revision=current['revision']),'observe-current')
        with control_store.connect() as db:
            request = dict(db.execute('SELECT * FROM db_creation_requests WHERE id=?',(ack['request_id'],)).fetchone())
            root = dict(db.execute('SELECT * FROM db_creations WHERE id=?',(self.ack['creation_id'],)).fetchone())
            original = dict(db.execute("SELECT * FROM db_creation_phase_plans WHERE phase='full' AND purpose='APPLY'").fetchone())
            frozen = dict(db.execute("SELECT * FROM db_creation_phase_plans WHERE purpose='VERIFY_RECOVERY'").fetchone())
        with recovery.observation_request(request), auth.transaction() as db:
            recovery.complete_reconcile(db,root,original,dict(plan_id=frozen['id'],state='AWAITING_RECOVERY_APPROVAL'))
        with control_store.connect() as db:
            saved = db.execute('SELECT * FROM db_creation_requests WHERE id=?',(ack['request_id'],)).fetchone()
            self.assertEqual((saved['dispatch_state'],saved['stop_reason']),('COMPLETE',None))
            self.assertEqual(saved['ack'],request['ack'])
            self.assertEqual(db.execute('SELECT state FROM operations WHERE id=?',(self.operation['id'],)).fetchone()[0],'UNKNOWN')
            self.assertEqual(db.execute('SELECT reservation_active FROM db_creations').fetchone()[0],1)
        # Lost worker stdout can be retried for its saved completion without
        # resolving an operator or observing again.
        import database_creation_worker as worker
        with patch.object(worker,'resolve') as resolve,patch.object(recovery,'observe') as observe:
            self.assertEqual(worker.dispatch(self.managed,ack['request_id'])['state'],'COMPLETE')
            resolve.assert_not_called(); observe.assert_not_called()

    def test_owner_journal_only_drift_hides_recovery_without_observing(self):
        self.projection_material()
        accepted = self.recovery_plan()
        with control_store.connect() as db:
            db.execute("UPDATE db_creation_requests SET approval_state='REVOKED' WHERE id=?",(accepted['request_id'],))
        def detail():
            return creations.get(self.alice,'admin',self.ack['creation_id'])
        self.assertIn('recover-verify',detail()['next_actions'])
        parent,frozen = self.receipt_path.read_bytes(),self.path.read_bytes()
        path = self.owner_journals['native']
        initial = runtime_profile.private_json(path)
        for change in ('intent','descriptor','readiness','ack-intent-id'):
            path = self.owner_journals['native']
            changed = copy.deepcopy(initial)
            if change == 'intent':
                changed['stop'] = dict(state='INTENT',intent_id=str(uuid.uuid4()))
            elif change == 'descriptor':
                changed['descriptor']['original_plan_id'] = 'different'
            elif change == 'readiness':
                changed['readiness'] = dict(state='ACK',intent_id=str(uuid.uuid4()))
            else:
                # Unchanged classifications cannot hide a later ACK attempt.
                path = self.owner_journals['workbench']
                changed = runtime_profile.private_json(path)
                changed['start']['intent_id'] = str(uuid.uuid4())
            original = runtime_profile.private_json(path)
            save_private(path,changed)
            before = self.snapshot()
            with patch.object(recovery,'observe') as observe,patch.object(bridge,'dispatch') as dispatch:
                current = detail()
                self.assertNotIn('recover-verify',current['next_actions'])
                self.assertIn('reconcile',current['next_actions'])
                observe.assert_not_called(); dispatch.assert_not_called()
            self.assertEqual(before,self.snapshot())
            self.assertEqual(self.receipt_path.read_bytes(),parent)
            self.assertEqual(self.path.read_bytes(),frozen)
            save_private(path,original)
        self.assertIn('recover-verify',detail()['next_actions'])

    def test_recovered_live_candidate_awaiting_apply_still_offers_fresh_approval(self):
        import database_creation_worker as worker
        self.projection_material()
        native = runtime_profile.private_json(self.owner_journals['native'])
        native['state'] = 'ACK'
        for leaf in ('stop','start','readiness'):
            native[leaf] = dict(state='ACK',intent_id=str(uuid.uuid4()))
        save_private(self.owner_journals['native'],native)
        self.receipt['runtime_activations']['native']['state'] = 'ACK'
        self.managed.config.owner('native')['runtime_instance_id'] = 'candidate-native'
        accepted = self.recovery_plan()
        recovery.claim(self.managed,accepted['request_id'],self.executor)
        token = recovery._observation.set((self.managed,self.ack['creation_id'],self.operation['id']))
        try:
            observed = core.reconcile_from_observer(self.operation['id'],actor='alice',observer=lambda p:
                core.ReconciliationEvidence('verify','SUCCEEDED','observer:recovered-candidate','observed_configuration'))
        finally:
            recovery._observation.reset(token)
        self.assertEqual(observed['state'],'AWAITING_APPLY')
        with control_store.connect() as db:
            root = dict(db.execute('SELECT * FROM db_creations').fetchone())
            original = dict(db.execute("SELECT * FROM db_creation_phase_plans WHERE phase='full' AND purpose='APPLY'").fetchone())
        request = dict(id=self.receipt['verify_declaration']['original_request_id'])
        def validate(config,path,*,hello):
            journal = worker.activation_journal(self.managed,request,root,original,self.operator,hello['owner_role'])
            if journal['stop']['state'] != 'ACK' or journal['start']['state'] != 'ACK':
                raise ValueError('Unproven candidate')
            return self.operator
        def sample(config):
            identities = bridge._projection_identities(self.managed.config)
            self.assertEqual({values[0][0] for values in identities.values()},{'candidate-workbench','candidate-native'})
            return creations.FoundationReadiness(True,True)
        self.probe.projection_probe = sample
        with patch.object(recovery,'previous_attestation',return_value={'generation':'parent'}),\
                patch.object(worker,'validate_candidate_profile',side_effect=validate),\
                patch.object(worker,'accepted_head',side_effect=AssertionError('prior APPLIED fallback forbidden')):
            detail = creations.get(self.alice,'admin',self.ack['creation_id'])
            self.assertEqual(detail['progress_code'],'FULL_REVIEW_REQUIRED')
            self.assertIn('approve',detail['next_actions'])
            wrong = copy.deepcopy(native)
            wrong['descriptor']['original_plan_id'] = 'stale'
            save_private(self.owner_journals['native'],wrong)
            self.assertNotIn('approve',creations.get(self.alice,'admin',self.ack['creation_id'])['next_actions'])
            save_private(self.owner_journals['native'],native)
            record = self.managed.config.owner('native')
            record['runtime_instance_id'] = 'unrelated'
            self.assertNotIn('approve',creations.get(self.alice,'admin',self.ack['creation_id'])['next_actions'])
            record['runtime_instance_id'] = 'candidate-native'
            detail = creations.get(self.alice,'admin',self.ack['creation_id'])
            self.full = next(p for p in detail['plans'] if p['purpose']=='APPLY' and p['phase']=='full')
            self.assertEqual(self.approve_full(detail,key='fresh-after-recovery')['action'],'APPROVE')

    def test_private_observe_preserves_revoked_approval_history(self):
        accepted = self.recovery_plan()
        auth.stop_principal(self.config,self.executor,'alice','stop-history')
        with control_store.connect() as db:
            request = dict(db.execute('SELECT * FROM db_creation_requests WHERE id=?',(accepted['request_id'],)).fetchone())
            root = dict(db.execute('SELECT * FROM db_creations').fetchone())
            original = dict(db.execute("SELECT * FROM db_creation_phase_plans WHERE phase='full' AND purpose='APPLY'").fetchone())
            frozen = db.execute("SELECT id FROM db_creation_phase_plans WHERE purpose='VERIFY_RECOVERY'").fetchone()[0]
        with recovery.observation_request(request), auth.transaction() as db:
            recovery.complete_reconcile(db,root,original,dict(plan_id=frozen))
        with control_store.connect() as db:
            self.assertEqual(dict(db.execute('SELECT * FROM db_creation_requests WHERE id=?',
                (accepted['request_id'],)).fetchone()),request)

    def test_classifies_every_leaf_absence_is_never_unsent_after_intent(self):
        self.receipt['sql']['remaining'] = dict(dispatched=True,complete=False)
        self.receipt['runtime_activations']['native']['state'] = 'INTENT'
        self.receipt['runtime_activations']['native']['leaves']['stop'] = 'UNKNOWN'
        states = recovery.classify(self.operator)
        self.assertEqual(states['sql/remaining'],'UNKNOWN')
        self.assertEqual(states['sql/done'],'COMPLETE')
        self.assertEqual(states['activation/native/stop'],'UNKNOWN')
        self.receipt['sql']['remaining']['next_uri'] = 'https://127.0.0.1:8443/v1/statement/query/1'
        self.assertEqual(recovery.classify(self.operator)['sql/remaining'],'GET_ONLY')

    def test_claim_preserves_original_unknown_and_attempt_and_has_narrow_scope(self):
        accepted = self.recovery_plan()
        permit = recovery.claim(self.managed,accepted['request_id'],self.executor)
        self.assertEqual(permit,recovery.claim(self.managed,accepted['request_id'],self.executor))
        save_private(recovery.intent_path(permit),dict(version=1,request_id=permit.approval_request_id,
            claim_id=permit.claim_id,original_operation_id=permit.operation_id,recovery_digest=permit.recovery_digest,state='RUNNING'))
        with recovery.delegated(permit):
            for identity in (dict(kind='activation',role='native'),dict(kind='sql-post',receipt_key='remaining'),dict(kind='verify-http',key='token')):
                recovery.authorize_leaf(self.operation,identity,self.receipt)
            for leaf in ('stop','start','readiness'):
                recovery.authorize_leaf(self.operation,dict(kind='managed-activation',activation_id='native',leaf=leaf),self.receipt)
            for identity in (dict(kind='activation',role='workbench'),dict(kind='sql-post',receipt_key='done'),dict(kind='arbitrary-shell')):
                with self.assertRaises(core.OperationError):
                    recovery.authorize_leaf(self.operation,identity,self.receipt)
            with auth.transaction() as db:
                original = recovery.original_unknown(db,self.operation['id'])
                self.assertNotEqual(original['attempt_id'],permit.claim_id)
                self.assertEqual(original['verify_state'],'UNKNOWN')

    def test_claim_is_not_a_running_recovery_intent_and_other_owner_cannot_steal_it(self):
        accepted = self.recovery_plan()
        permit = recovery.claim(self.managed,accepted['request_id'],self.executor)
        with recovery.delegated(permit),self.assertRaises((OSError,ValueError,core.OperationError)):
            recovery.authorize_leaf(self.operation,dict(kind='sql-post',receipt_key='remaining'),self.receipt)
        other = SimpleNamespace(require=Mock(),hello=dict(owner_instance_id='different'),config=self.managed.config)
        with self.assertRaisesRegex(core.OperationError,'CREATION_BUSY'):
            recovery.claim(other,accepted['request_id'],self.executor)

    def test_stop_fences_each_remaining_leaf_and_existing_approval_cannot_revive(self):
        accepted = self.recovery_plan()
        permit = recovery.claim(self.managed,accepted['request_id'],self.executor)
        auth.stop_principal(self.config,self.executor,'alice','stop-during-recovery')
        with recovery.delegated(permit):
            for identity in (dict(kind='activation',role='native'),dict(kind='sql-post',receipt_key='remaining'),dict(kind='verify-http',key='token')):
                with self.assertRaises(core.OperationError):
                    recovery.authorize_leaf(self.operation,identity,self.receipt)
            for leaf in ('stop','start','readiness'):
                with self.assertRaises(core.OperationError):
                    recovery.authorize_leaf(self.operation,dict(kind='managed-activation',activation_id='native',leaf=leaf),self.receipt)
        # A stub receipt/Managed value cannot authorize the local management
        # release; the real sealed-material integration covers positive release.
        with self.assertRaisesRegex(core.OperationError,'RECONCILIATION_REQUIRED'):
            auth.release_principal(self.config,self.executor,'alice','explicit-release-after-classification')
        with self.assertRaises(core.OperationError):
            recovery.claim(self.managed,accepted['request_id'],self.executor)

    def test_current_authentication_file_revocation_latches_stop(self):
        accepted = self.recovery_plan()
        permit = recovery.claim(self.managed,accepted['request_id'],self.executor)
        self.accounts['executor']['disabled'] = True
        self.save_accounts()
        with recovery.delegated(permit),self.assertRaises(auth.AuthorityLost):
            recovery.assert_original(self.operation)
        with control_store.connect() as db:
            self.assertEqual(db.execute("SELECT state FROM db_creation_principal_gates WHERE principal_id='executor'").fetchone()[0],'STOPPED')

    def test_recovery_seal_drift_is_not_executable(self):
        accepted = self.recovery_plan()
        permit = recovery.claim(self.managed,accepted['request_id'],self.executor)
        value = runtime_profile.private_json(self.path)
        value['leaves']['sql/done'] = 'UNSENT'
        save_private(self.path,value)
        with recovery.delegated(permit),self.assertRaises(core.OperationError):
            recovery.assert_original(self.operation)

    def test_completed_observation_never_launches_transport_and_requires_explicit_recovery(self):
        operator = SimpleNamespace(start_transports=Mock(),reconcile=Mock())
        root = dict(id=self.ack['creation_id'],actor='alice')
        plan = dict(operation_id=self.operation['id'])
        expected = dict(state='AWAITING_RECOVERY_APPROVAL')
        with patch.object(recovery,'_observe',side_effect=[None,expected]) as classify,\
                patch.object(core,'reconcile_from_observer',return_value=dict(state='UNKNOWN')):
            self.assertEqual(recovery.observe(self.managed,root,plan,operator),expected)
        self.assertTrue(classify.call_args.kwargs['plan_completed'])
        operator.start_transports.assert_not_called()

    def test_reconcile_requires_fresh_ordinary_full_approval_before_ready(self):
        accepted = self.recovery_plan()
        recovery.claim(self.managed,accepted['request_id'],self.executor)
        token = recovery._observation.set((self.managed,self.ack['creation_id'],self.operation['id']))
        try:
            observed = core.reconcile_from_observer(self.operation['id'],actor='alice',observer=lambda p:
                core.ReconciliationEvidence('verify','SUCCEEDED','real-observer:fixture','observed_configuration'))
        finally:
            recovery._observation.reset(token)
        self.assertEqual(observed['state'],'AWAITING_APPLY')
        with control_store.connect() as db:
            self.assertNotEqual(db.execute('SELECT state FROM databases WHERE id=(SELECT database_id FROM db_creations)').fetchone()[0].upper(),'READY')
        detail = creations.get(self.alice,'admin',self.ack['creation_id'])
        self.full = next(p for p in detail['plans'] if p['purpose']=='APPLY' and p['phase']=='full')
        approval = self.approve_full(detail,key='fresh-final')
        fresh = auth.claim(self.config,self.executor,approval['request_id'],owner_id='owner')
        verify = Mock(return_value=dict(verified=True,evidence_ref='real-observer:fixture'))
        result = self.apply(self.full,self.observations,fresh,verify)
        self.assertEqual(result['state'],'APPLIED')
        # Core deliberately reruns the verify callback on the final approval;
        # Operator.verify_trino must reuse its completed SQL receipts (below).
        verify.assert_called_once()


class SqlRecoveryTests(unittest.TestCase):
    def test_stopped_positive_unsent_sql_never_posts(self):
        operator = self.operator(None)
        operator.leaf_intent.side_effect = core.OperationError('PRINCIPAL_STOPPED',403)
        with self.assertRaises(core.OperationError):
            operator.verify_trino('db',dict(username='user',password='private'),dict(endpoint_url='https://127.0.0.1:8443',ca_file='/private/ca'),dict(id='op',actor='alice'))
        operator.http.assert_not_called()

    def test_completed_or_unknown_verify_http_is_never_posted_again(self):
        import urllib.request
        request = urllib.request.Request('http://127.0.0.1:18181/api/catalog/v1/oauth/tokens',data=b'fixed')
        for saved in (dict(state='ACK',response=dict(access_token='private')),dict(state='INTENT')):
            operator = object.__new__(Operator)
            operator.receipt = dict(verify_http={operator.http_identity(request):saved})
            operator.http = Mock()
            operator.leaf_intent = Mock()
            if saved['state']=='ACK':
                self.assertEqual(operator.post(request),saved['response'])
            else:
                with self.assertRaisesRegex(RuntimeError,'UNKNOWN'):
                    operator.post(request)
            operator.http.assert_not_called()
            operator.leaf_intent.assert_not_called()

    def operator(self,saved):
        operator = object.__new__(Operator)
        operator.bundle = dict(manifest=dict(spec=dict(databases={'db':dict(sql_catalog='catalog',schemas=[dict(sql_name='marts')])})))
        operator.receipt = dict(sql={} if saved is None else dict(db=saved),
            verify_declaration=dict(sql={'db':'UNSENT'}))
        operator.assert_intent = Mock(); operator.leaf_intent = Mock(); operator.save = Mock(); operator.http = Mock()
        return operator

    def test_complete_sql_and_unknown_sql_never_post(self):
        complete = dict(dispatched=True,complete=True,backend_id='q',rows=[['user','catalog','marts']])
        for saved in (complete,dict(dispatched=True,complete=False)):
            operator = self.operator(saved)
            if saved.get('complete'):
                operator.verify_trino('db',dict(username='user',password='private'),dict(endpoint_url='https://127.0.0.1:8443',ca_file='/private/ca'),dict(id='op',actor='alice'))
            else:
                with self.assertRaisesRegex(RuntimeError,'UNKNOWN'):
                    operator.verify_trino('db',dict(username='user',password='private'),dict(endpoint_url='https://127.0.0.1:8443',ca_file='/private/ca'),dict(id='op',actor='alice'))
            operator.http.assert_not_called()

    def test_saved_next_uri_is_get_only(self):
        uri = 'https://127.0.0.1:8443/v1/statement/q/1'
        operator = self.operator(dict(dispatched=True,complete=False,backend_id='q',rows=[],next_uri=uri))
        operator.http.return_value = dict(id='q',columns=[dict(name=n) for n in ('verified_user','catalog_name','schema_name')],data=[['user','catalog','marts']])
        operator.verify_trino('db',dict(username='user',password='private'),dict(endpoint_url='https://127.0.0.1:8443',ca_file='/private/ca'),dict(id='op',actor='alice'))
        self.assertEqual(operator.http.call_args.args[0].get_method(),'GET')
        operator.leaf_intent.assert_not_called()


if __name__ == '__main__':
    unittest.main()
