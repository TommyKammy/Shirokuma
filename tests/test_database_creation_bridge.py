"""Managed identity tests use real inherited socketpairs and private files."""
import copy
import base64
from dataclasses import fields
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

from tests.test_runtime_profile import synthetic_profile
from tests import test_database_creation_preparation as preparation_tests
import database_creation_bridge as bridge
import database_creation_preparation as preparation
import database_creations as creations
import local_gitops
import runtime_profile
import request_auth
from warehouse_operations import OperationError
from warehouse_operator import Operator, save_private


class BaseParentFixture(preparation_tests.PreparationFixture):
    """Real sealed first FULL, with a lost Workbench ACT response."""
    complete_bootstrap = preparation_tests.PreparationTests.complete_bootstrap
    managed_candidate = preparation_tests.ManagedPreparationTests.managed_candidate

    def setUp(self):
        super().setUp()
        import hashlib
        import control_store
        import database_creation_auth as auth
        import database_creation_worker as worker
        import warehouse_operations as core
        candidate = self.managed_candidate(prepare=False)
        self.managed = candidate[0]
        self.ca = b'unit foundation CA'
        value = self.managed.config.value
        value['foundation']['ca_sha256'] = hashlib.sha256(self.ca).hexdigest()
        save_private(Path(self.managed.config.path),value)
        self.managed.config = bridge.Configuration(self.managed.config.path)
        self.managed,self.full,*_ = self.managed_candidate(existing=candidate)
        detail = creations.get(self.alice,'admin',self.root_id)
        public = next(p for p in detail['plans'] if p['phase']=='full')
        self.approval = creations.mutate(self.alice,'admin',self.root_id,'APPROVE',
            dict(expected_revision=detail['revision'],plan_id=public['plan_id'],operation_id=public['operation_id'],
                expected_operation_revision=public['operation_revision'],delivery_digest=public['delivery_digest']),
            'base-parent-full',phase='full')
        permit = auth.claim(self.config.enrollment,self.executor,self.approval['request_id'],
            owner_id=self.managed.hello['owner_instance_id'])
        with control_store.connect() as db:
            self.plan = dict(db.execute('SELECT * FROM db_creation_phase_plans WHERE id=?',(public['plan_id'],)).fetchone())
            self.root_row = dict(db.execute('SELECT * FROM db_creations WHERE id=?',(self.root_id,)).fetchone())
        self.handoff = self.full.config['managed_handoff']
        self.base_attestation = runtime_profile._public_identity(self.managed.config.base)
        operation = core.inspect(public['operation_id'],actor='alice')
        self.full.bind(operation); self.full.check_receipt(operation)
        observations = runtime_profile.private_json(self.directory_for('full')/'journal.json')['observations']
        self.assertTrue(runtime_profile.private_json(self.directory_for('full')/'journal.json')['pins']['applied_material']['base'])
        def lost(current):
            worker.seal_activation_plans(self.managed,dict(id=self.approval['request_id']),self.root_row,self.plan,self.full)
            sealed = worker.descriptor(dict(id=self.approval['request_id']),self.root_row,self.plan,self.full,'workbench')
            self.activation_path = Path(value['owners']['workbench']['directory'])/'activations'/(sealed['activation_id']+'.json')
            save_private(self.activation_path,dict(descriptor=sealed,state='ACK',
                **{leaf:dict(state='ACK',intent_id=str(uuid.uuid4())) for leaf in ('stop','start','readiness')}))
            self.write_owner('workbench',runtime_instance_id=sealed['candidate_runtime_instance_id'])
            raise ConnectionError('lost Workbench ACT response')
        with auth.delegated(permit):
            self.operation = core.apply(operation['id'],actor='alice',expected_revision=operation['revision'],
                delivery_digest=operation['delivery_digest'],authorize=lambda actor:True,
                observe_secrets=lambda p:copy.deepcopy(observations),
                execute_step=lambda s,p:dict(evidence_ref='unit:'+s),verify=lost)
        self.assertEqual(self.operation['state'],'UNKNOWN')

    def write_owner(self, role, **changes):
        path = Path(self.managed.config.value['owners'][role]['record_path'])
        save_private(path,dict(self.managed.config.owner(role),**changes))

    def mock_projection_reads(self):
        from types import SimpleNamespace
        config = self.managed.config
        foundation = config.value['foundation']
        def command(argv, **kwargs):
            self.assertIn('kubectl',argv)
            if 'view' in argv:
                result = {'current-context':foundation['kube_context'],'clusters':[{'cluster':dict(
                    server=foundation['api_server'],**{'certificate-authority-data':base64.b64encode(self.ca).decode()})}]}
            else:
                self.assertIn('nodes',argv); self.assertIn('get',argv)
                result = dict(items=[dict(metadata=dict(name='node',uid='uid'))])
            return SimpleNamespace(returncode=0,stdout=json.dumps(result))
        def ipc(role, action, rid, aid=None):
            self.assertEqual(action,'GET_REQUEST')
            return dict(state='NOT_FOUND',runtime_instance_id=config.owner(role)['runtime_instance_id'])
        def http(ignored, request):
            self.assertEqual(request.get_method(),'GET')
            role = 'workbench' if request.full_url.startswith(config.value['origins']['workbench']) else 'native'
            record = config.owner(role)
            attestation = self.handoff['runtime_attestation'] if role=='workbench' else self.base_attestation
            if request.full_url.endswith('/api/v1/runtime'):
                return attestation
            self.assertTrue(request.full_url.endswith('/runtime-instance'))
            return dict(version=1,environment_id=config.enrollment.environment_id,owner_role=role,
                owner_instance_id=record['owner_instance_id'],runtime_instance_id=record['runtime_instance_id'],runtime_attestation=attestation)
        for context in (patch.object(bridge.subprocess,'run',side_effect=command),
                patch.object(self.managed,'ipc',side_effect=ipc),patch.object(Operator,'http',http)):
            context.start(); self.addCleanup(context.stop)
        creations.install_foundation(config.enrollment,self.managed)


class BaseParentProjectionTests(BaseParentFixture):
    def test_unknown_mixed_base_children_offer_only_advisory_reconciliation(self):
        import database_creation_auth as auth
        self.mock_projection_reads()
        before = self.snapshot()
        receipt = self.full.receipt_path.read_bytes()
        activation = self.activation_path.read_bytes()
        with patch.object(auth,'claim') as claim,patch.object(bridge,'dispatch') as dispatch,\
                patch.object(Operator,'execute_step') as execute,patch.object(Operator,'verify') as verify:
            detail = creations.get(self.alice,'admin',self.root_id)
            self.assertEqual(detail['progress_code'],'RECONCILE_REQUIRED')
            self.assertEqual(detail['next_actions'],['reconcile'])
            self.assertIsNone(detail['blocker'])
            for action in (claim,dispatch,execute,verify):
                action.assert_not_called()
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.full.receipt_path.read_bytes(),receipt)
        self.assertEqual(self.activation_path.read_bytes(),activation)
        for role in ('workbench','native'):
            record = self.managed.config.owner(role)
            with self.subTest(role=role):
                self.write_owner(role,runtime_instance_id=str(uuid.uuid4()))
                with self.assertRaisesRegex(ValueError,'sealed handoff'):
                    bridge._projection_identities(self.managed.config)
                blocked = creations.get(self.alice,'admin',self.root_id)
                self.assertEqual(blocked['blocker'],'OPERATOR_UNAVAILABLE')
                self.assertEqual(blocked['next_actions'],['withdraw'])
                self.write_owner(role,**record)
        self.assertEqual(self.snapshot(),before)


class BridgeFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir='/private/tmp')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.root.chmod(0o700)
        self.profile,self.profile_value = synthetic_profile(self.root)
        accounts = runtime_profile.private_json(self.profile_value['auth_file'])
        accounts['alice']['password_hash'] = request_auth.password_hash('password',accounts['alice']['salt'])
        save_private(Path(self.profile_value['auth_file']),accounts)
        self.path = self.root/'enrollment.json'
        self.owners = {}
        for role in ('workbench','native'):
            directory = self.root/role
            directory.mkdir(mode=0o700)
            self.owners[role] = dict(directory=str(directory),socket_path=str(directory/'ipc'),record_path=str(directory/'owner.json'))
        p = {f.name:str(self.root/f.name) for f in fields(preparation.PreparationConfig)
             if f.type is Path}
        p.update(trino_endpoint='https://127.0.0.1:18443',polaris_endpoints=[['polaris','http://127.0.0.1:18181']],
                 polaris_port_range=[18200,18220])
        self.value = dict(version=1,environment_id='env',warehouse_id='LOCAL',enrollment_id='enrollment',revision=1,
            executor_id='alice',state_dir=str(self.root),base_runtime_profile=str(self.profile),
            executor_auth_file=p['auth_headers'],owners=self.owners,
            origins=dict(workbench='http://127.0.0.1:18888',native=self.profile_value['sql_endpoint_url'],backend=p['trino_endpoint']),
            foundation=dict(kube_context=local_gitops.CONTEXT,api_server='https://127.0.0.1:16443',
                ca_sha256='1'*64,node_uids={'node':'uid'},tofu_lineage='lineage'),preparation=p)
        save_private(Path(p['auth_headers']),{'Authorization':'Basic '+base64.b64encode(b'alice:password').decode(),'X-Shirokuma-Role':'admin'})
        save_private(Path(p['tofu_state']),dict(lineage='lineage',serial=1))
        self.save()
        self.record = dict(version=1,environment_id='env',enrollment_id='enrollment',enrollment_revision=1,
            owner_role='workbench',owner_instance_id=str(uuid.uuid4()),runtime_instance_id=str(uuid.uuid4()),
            socket_path=self.owners['workbench']['socket_path'],capability='private-startup-capability')
        save_private(Path(self.owners['workbench']['record_path']),self.record)
        native = dict(self.record,owner_role='native',socket_path=self.owners['native']['socket_path'],
                      owner_instance_id=str(uuid.uuid4()),runtime_instance_id=str(uuid.uuid4()))
        save_private(Path(self.owners['native']['record_path']),native)
        self.hello = dict(self.record,purpose='http',private_enrollment_path=str(self.path),profile_path=str(self.profile))
        for context in (patch.dict(os.environ,SHIROKUMA_RUNTIME_PROFILE=str(self.profile)),
                patch.object(bridge,'_managed',None),patch.object(creations,'_enrollment',None),patch.object(creations,'_foundation',None)):
            context.start(); self.addCleanup(context.stop)
        runtime_profile.configure_process()

    def save(self):
        save_private(self.path,self.value)

    def connect(self,hello=None):
        parent,child = socket.socketpair()
        self.addCleanup(parent.close); self.addCleanup(child.close)
        parent.sendall(json.dumps(hello or self.hello).encode()+b'\n')
        duplicate = os.dup
        with patch.dict(os.environ,SHIROKUMA_MANAGED_FD='3'),patch.object(bridge.os,'dup',side_effect=lambda ignored:duplicate(child.fileno())):
            managed = bridge.configure()
        self.addCleanup(managed.channel.close)
        ready = bridge.line(parent)
        return managed,parent,ready


class BridgeTests(BridgeFixture):
    def test_projection_handoff_uses_only_sealed_old_or_candidate_per_owner(self):
        from types import SimpleNamespace
        import control_store
        import database_creation_worker as worker
        import database_creation_recovery as recovery
        root = dict(id='root')
        plan = dict(id='plan',creation_id='root',sequence=1)
        owners = {role:dict(owner_instance_id=role,old_runtime_instance_id='old-'+role,
            candidate_runtime_instance_id='new-'+role) for role in ('workbench','native')}
        records = {role:dict(owner_instance_id=role,runtime_instance_id=('old-' if role == 'workbench' else 'new-')+role)
            for role in owners}
        operator = SimpleNamespace(config=dict(managed_handoff=dict(owners=owners,runtime_attestation={'generation':'candidate'})))
        config = SimpleNamespace(enrollment=creations.Enrollment('env','LOCAL','enrollment',1,'alice'),
            preparation=lambda:SimpleNamespace(output_root=self.root),owner=lambda role:records[role])
        with patch.object(control_store,'connect') as connect,patch.object(creations,'_root',return_value=root),\
                patch('warehouse_operator.Operator',return_value=operator),\
                patch.object(recovery,'previous_attestation',return_value={'generation':'parent'}) as previous,\
                patch.object(worker,'validate_candidate_profile') as validate:
            connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = plan
            self.assertEqual(bridge._projection_identities(config),dict(
                workbench=[('old-workbench',{'generation':'parent'})],native=[('new-native',{'generation':'candidate'})]))
            self.assertEqual(previous.call_count,2)
            self.assertEqual(validate.call_count,1)
            self.assertEqual(validate.call_args.kwargs['hello']['owner_role'],'native')
            records['native']['runtime_instance_id'] = 'unrelated'
            with self.assertRaisesRegex(ValueError,'sealed handoff'):
                bridge._projection_identities(config)
            records['native']['runtime_instance_id'] = 'new-native'
            previous.side_effect = ValueError('seal changed')
            with self.assertRaisesRegex(ValueError,'seal changed'):
                bridge._projection_identities(config)

    def test_both_http_endpoints_ack_only_after_real_bind(self):
        import http.server
        import workbench_server
        import warehouse_resident
        from contextlib import ExitStack
        for role in ('workbench','native'):
            with self.subTest(role=role), ExitStack() as stack:
                record = runtime_profile.private_json(self.owners[role]['record_path'])
                hello = dict(record,purpose='http',private_enrollment_path=str(self.path),profile_path=str(self.profile))
                parent,child = socket.socketpair()
                stack.callback(parent.close); stack.callback(child.close)
                parent.sendall(json.dumps(hello).encode()+b'\n')
                duplicate = os.dup
                stack.enter_context(patch.dict(os.environ,SHIROKUMA_MANAGED_FD='3'))
                stack.enter_context(patch.object(bridge.os,'dup',side_effect=lambda ignored:duplicate(child.fileno())))
                stack.enter_context(patch.object(workbench_server.schedule_resident,'start_scheduler'))
                stack.enter_context(patch.object(workbench_server.schedule_resident,'stop_scheduler'))
                stack.enter_context(patch('trino_auth.backend_ssl_context'))
                def serving(server):
                    stack.callback(bridge._managed.channel.close)
                    # A real TCP connection proves listening portably, including on macOS.
                    with socket.create_connection(server.server_address,timeout=1):
                        pass
                    ready = bridge.line(parent)
                    self.assertEqual(ready,dict(version=1,state='READY',owner_role=role,
                        owner_instance_id=record['owner_instance_id'],runtime_instance_id=record['runtime_instance_id'],
                        runtime_attestation=runtime_profile.generation_attestation()))
                stack.enter_context(patch.object(http.server.HTTPServer,'serve_forever',serving))
                if role == 'workbench':
                    workbench_server.main(['--port','0'])
                else:
                    warehouse_resident.serve_endpoint(port=0)

    def test_both_http_bind_failures_send_no_ready(self):
        import workbench_server
        import warehouse_resident
        for role in ('workbench','native'):
            with self.subTest(role=role), socket.socket() as occupied:
                occupied.bind(('127.0.0.1',0)); occupied.listen()
                port = occupied.getsockname()[1]
                parent,child = socket.socketpair()
                try:
                    record = runtime_profile.private_json(self.owners[role]['record_path'])
                    parent.sendall(json.dumps(dict(record,purpose='http',private_enrollment_path=str(self.path),
                        profile_path=str(self.profile))).encode()+b'\n')
                    duplicate = os.dup
                    with patch.dict(os.environ,SHIROKUMA_MANAGED_FD='3'),\
                            patch.object(bridge.os,'dup',side_effect=lambda ignored:duplicate(child.fileno())),\
                            patch('trino_auth.backend_ssl_context'),\
                            patch.object(bridge,'configure',wraps=bridge.configure) as configure, self.assertRaises(OSError):
                        if role == 'workbench':
                            workbench_server.main(['--port',str(port)])
                        else:
                            warehouse_resident.serve_endpoint(port=port)
                    configure.assert_not_called()
                    parent.settimeout(.05)
                    with self.assertRaises(socket.timeout):
                        parent.recv(1)
                finally:
                    parent.close(); child.close()

    def test_standalone_env_paths_do_not_confer_managed_identity(self):
        with patch.dict(os.environ,{},clear=True):
            self.assertIsNone(bridge.configure())
        with self.assertRaisesRegex(OperationError,'OPERATOR_UNAVAILABLE'):
            bridge.runtime_instance()
        with patch.dict(os.environ,SHIROKUMA_MANAGED_FD='4'),self.assertRaises(OperationError):
            bridge.configure()

    def test_real_fd_handshake_exact_instance_and_eof_fence(self):
        managed,parent,ready = self.connect()
        self.assertEqual(set(ready),{'version','state','owner_role','owner_instance_id','runtime_instance_id','runtime_attestation'})
        self.assertEqual(ready['runtime_attestation'],runtime_profile.generation_attestation())
        instance = managed.instance()
        self.assertEqual(set(instance),bridge.INSTANCE)
        self.assertNotIn('capability',json.dumps(instance))
        parent.close()
        self.assertTrue(managed.invalidated.wait(2))
        with self.assertRaisesRegex(OperationError,'OPERATOR_UNAVAILABLE'):
            managed.require()

    def test_hello_extra_identity_profile_or_capability_mismatch_is_rejected(self):
        for change in ({'extra':'field'},{'owner_instance_id':str(uuid.uuid4())},{'capability':'other'},
                       {'enrollment_revision':True},{'profile_path':str(self.root/'other')},{'purpose':'worker'}):
            parent,child = socket.socketpair()
            duplicate = os.dup
            parent.sendall(json.dumps(dict(self.hello,**change)).encode()+b'\n')
            try:
                with patch.dict(os.environ,SHIROKUMA_MANAGED_FD='3'),patch.object(bridge.os,'dup',side_effect=lambda ignored:duplicate(child.fileno())),self.assertRaises(OperationError):
                    bridge.configure()
            finally:
                parent.close(); child.close()

    def test_strict_nested_config_and_private_owner_permissions(self):
        for change in (dict(extra=True),dict(version=True),dict(foundation=dict(self.value['foundation'],ready=True)),
                       dict(preparation=dict(self.value['preparation'],command=['anything'])),
                       dict(origins=dict(self.value['origins'],native='http://example.org'))):
            save_private(self.path,dict(self.value,**change))
            with self.assertRaises(ValueError):
                bridge.Configuration(self.path)
        self.save()
        config = bridge.Configuration(self.path)
        Path(self.owners['workbench']['record_path']).chmod(0o644)
        with self.assertRaises(ValueError):
            config.owner('workbench')

    def test_duplicate_ipc_keys_and_oversize_are_rejected(self):
        for raw in (b'{"version":1,"version":1}\n',b'x'*65536):
            a,b = socket.socketpair()
            try:
                sender = threading.Thread(target=a.sendall,args=(raw,))
                sender.start()
                with self.assertRaises(ValueError):
                    bridge.line(b)
                sender.join(timeout=2)
            finally:
                a.close(); b.close()

    def test_owner_replacement_fences_existing_fd(self):
        managed,parent,ready = self.connect()
        save_private(Path(self.owners['workbench']['record_path']),dict(self.record,owner_instance_id=str(uuid.uuid4())))
        with self.assertRaises(OperationError):
            managed.require()

    def test_worker_survives_http_generation_change_but_old_http_is_fenced(self):
        managed,parent,ready = self.connect()
        worker = bridge.Managed(managed.channel,dict(managed.hello,purpose='worker'),managed.config)
        save_private(Path(self.owners['workbench']['record_path']),dict(self.record,runtime_instance_id=str(uuid.uuid4())))
        worker.require()
        with self.assertRaises(OperationError):
            managed.require()

    def test_go_activation_not_found_empty_previous_is_readable_but_not_unsent_proof(self):
        managed,parent,ready = self.connect()
        record = managed.config.owner('workbench')
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as server:
            server.bind(record['socket_path'])
            Path(record['socket_path']).chmod(0o600)
            server.listen(1)
            captured = []
            def serve():
                peer,_ = server.accept()
                with peer:
                    request = bridge.line(peer)
                    captured.append(request)
                    response = {k:v for k,v in request.items() if k!='capability'}
                    response.update(state='NOT_FOUND',runtime_instance_id=record['runtime_instance_id'],previous_runtime_instance_id='')
                    peer.sendall(json.dumps(response).encode()+b'\n')
            thread = threading.Thread(target=serve)
            thread.start()
            result = managed.ipc('workbench','GET_ACTIVATION',str(uuid.uuid4()),'a'*64)
            thread.join(timeout=2)
            self.assertEqual(result['state'],'NOT_FOUND')
            self.assertEqual(result['previous_runtime_instance_id'],'')
            self.assertEqual(captured[0]['action'],'GET_ACTIVATION')

    def test_eof_is_synchronous_without_monitor_thread(self):
        parent,child = socket.socketpair()
        self.addCleanup(child.close)
        managed = bridge.Managed(child,self.hello,bridge.Configuration(self.path))
        parent.close()
        self.assertTrue(managed.alive)
        with self.assertRaises(OperationError):
            managed.require()
        self.assertTrue(managed.invalidated.is_set())
        with patch.object(bridge.subprocess,'run') as command,patch.object(managed,'ipc') as ipc:
            with self.assertRaises(OperationError):
                managed.projection_probe(managed.config.enrollment)
            command.assert_not_called(); ipc.assert_not_called()

    def test_fixed_ipc_request_and_strict_response_without_launch(self):
        managed,parent,ready = self.connect()
        record = managed.config.owner('workbench')
        Path(record['socket_path']).touch(mode=0o600)
        for extra in ({},{'arbitrary_path':'/not-accepted'}):
            client,peer = socket.socketpair()
            captured = []
            def serve():
                request = bridge.line(peer)
                captured.append(request)
                response = dict(version=1,action=request['action'],request_id=request['request_id'],state='NOT_FOUND',
                    owner_role='workbench',owner_instance_id=record['owner_instance_id'],runtime_instance_id=record['runtime_instance_id'],**extra)
                peer.sendall(json.dumps(response).encode()+b'\n')
            thread = threading.Thread(target=serve)
            thread.start()
            class Connection:
                def __enter__(self): return self
                def __exit__(self,*args): client.close()
                def settimeout(self,value): client.settimeout(value)
                def connect(self,path): self.path = path
                def sendall(self,value): client.sendall(value)
                def recv(self,size): return client.recv(size)
            try:
                with patch.object(bridge.socket,'socket',return_value=Connection()),patch.object(bridge.stat,'S_ISSOCK',return_value=True),\
                        patch.object(bridge.subprocess,'run') as launch:
                    if extra:
                        with self.assertRaises(OperationError):
                            managed.ipc('workbench','GET_REQUEST',str(uuid.UUID(int=0)))
                    else:
                        self.assertEqual(managed.ipc('workbench','GET_REQUEST',str(uuid.UUID(int=0)))['state'],'NOT_FOUND')
                    launch.assert_not_called()
            finally:
                thread.join(timeout=2)
                client.close(); peer.close()
            self.assertEqual(set(captured[0]),{'version','action','request_id','owner_role','owner_instance_id','capability'})

    def test_foundation_probes_current_cluster_runtime_and_actual_auth_source(self):
        from types import SimpleNamespace
        from warehouse_operator import Operator
        import hashlib
        ca = b'private dedicated CA'
        self.value['foundation']['ca_sha256'] = hashlib.sha256(ca).hexdigest()
        self.save()
        managed,parent,ready = self.connect()
        view = dict(**{'current-context':local_gitops.CONTEXT},clusters=[dict(cluster={
            'server':self.value['foundation']['api_server'],'certificate-authority-data':base64.b64encode(ca).decode()})])
        nodes = dict(items=[dict(metadata=dict(name='node',uid='uid'))])
        attestation = runtime_profile.generation_attestation()
        mismatch = []
        def command(argv,**kwargs):
            self.assertIn(str(self.root/'kubeconfig'),argv)
            return SimpleNamespace(returncode=0,stdout=json.dumps(view if 'config' in argv else nodes))
        def ipc(role,*args):
            return dict(state='NOT_FOUND',runtime_instance_id=managed.config.owner(role)['runtime_instance_id'])
        def http(*args,**kwargs):
            request = args[-1]
            role = 'workbench' if request.full_url.startswith(self.value['origins']['workbench']) else 'native'
            actual = dict(attestation,generation_id='different') if mismatch and (role=='native' or mismatch[0]=='both') else attestation
            if request.full_url.endswith('/runtime-instance'):
                record = managed.config.owner(role)
                return dict(version=1,environment_id='env',owner_role=role,owner_instance_id=record['owner_instance_id'],
                            runtime_instance_id=record['runtime_instance_id'],runtime_attestation=actual)
            return actual
        with patch.object(bridge.subprocess,'run',side_effect=command),patch.object(managed,'ipc',side_effect=ipc),patch.object(Operator,'http',side_effect=http):
            self.assertEqual(managed.probe(managed.config.enrollment),creations.FoundationReadiness(True,True))
            mismatch.append(True)
            with self.assertRaisesRegex(ValueError,'generations differ'):
                managed.probe(managed.config.enrollment)
            mismatch.clear()
            mismatch.append('both')
            with self.assertRaisesRegex(ValueError,'accepted head'):
                managed.probe(managed.config.enrollment)
            mismatch.clear()
            mismatch.append(True)
            allowed = {role:[(managed.config.owner(role)['runtime_instance_id'],
                dict(attestation,generation_id='different') if role == 'native' else attestation)]
                for role in ('workbench','native')}
            with patch.object(bridge,'_projection_identities',return_value=allowed):
                self.assertEqual(managed.projection_probe(managed.config.enrollment),creations.FoundationReadiness(True,True))
                # Projection can accept only the positively sealed pair; an
                # unrelated attestation still fails even during a handoff.
                allowed['native'][0] = (allowed['native'][0][0],dict(attestation,generation_id='unknown'))
                with self.assertRaisesRegex(ValueError,'sealed handoff'):
                    managed.projection_probe(managed.config.enrollment)
            mismatch.clear()
            nodes['items'][0]['metadata']['uid'] = 'different'
            with self.assertRaises(ValueError):
                managed.probe(managed.config.enrollment)
            with self.assertRaises(ValueError):
                managed.projection_probe(managed.config.enrollment)
            nodes['items'][0]['metadata']['uid'] = 'uid'
            view['clusters'][0]['cluster']['server'] = 'https://127.0.0.1:9999'
            with self.assertRaises(ValueError):
                managed.probe(managed.config.enrollment)
            view['clusters'][0]['cluster']['server'] = self.value['foundation']['api_server']
            accounts = runtime_profile.private_json(self.profile_value['auth_file'])
            accounts['alice']['disabled'] = True
            save_private(Path(self.profile_value['auth_file']),accounts)
            with self.assertRaises(ValueError):
                managed.probe(managed.config.enrollment)


if __name__ == '__main__':
    unittest.main()
