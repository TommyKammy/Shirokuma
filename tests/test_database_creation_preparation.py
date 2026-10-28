"""Private preparation with real renderer/material/operator and mocked external I/O."""
import copy
from dataclasses import fields, replace
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.test_database_creations import CreationFixture
from tests.test_database_generation import spec, ROOT
from tests.test_database_material import private_input_from_bundle
import catalog_admin
import control_store
import data_management as dm
import database_creation_auth as auth
import database_creation_preparation as prep
import database_creations as creations
import database_generation as generation
import database_material as material
import grant_management
import warehouse_operations as core
import warehouse_operator as operator

REAL_RUN = prep._run
REAL_COMMAND = operator.Operator.command


class PreparationFixture(CreationFixture):
    def setUp(self):
        super().setUp()
        value = spec(3)
        value['version'] = 2
        value['profile']['template_fingerprint'] = generation.profile_fingerprint(version=2)
        self.base = generation.render(value)
        self.private = private_input_from_bundle(self.base)
        with control_store.connect() as db:
            for key,entry in value['databases'].items():
                db.execute('INSERT INTO databases VALUES (?,?,?,?,?,?,?)',
                    (key,entry['sql_catalog'],key,'','user','retired' if key=='opaque_0' else 'READY',1))
                dm.create_binding(db,key,**{k:v for k,v in entry['binding'].items() if k!='desired_revision'})
                db.execute('UPDATE catalog_bindings SET applied_revision=desired_revision WHERE database_id=?',(key,))
                for schema in entry['schemas']:
                    db.execute('INSERT INTO schemas VALUES (?,?,?,?,?,?,?)',(schema['id'],key,schema['sql_name'],
                        schema['sql_name'],schema['purpose'],'user',schema['revision']))
        self.enrolled = self.directory/'enrolled'
        self.enrolled.mkdir(mode=0o700)
        generation.write_bundle(self.base,self.enrolled/'base')
        self.write('private',self.private)
        self.write('state',dict(lineage='test-lineage',serial=3))
        self.write('kubeconfig',{'synthetic':'dedicated'})
        self.write('auth',self.headers('executor'))
        self.write('s3',dict(endpoint_url='http://127.0.0.1:8333',region='us-east-1',
            access_key_id='test-access-key',secret_access_key='test-secret'))
        self.write('ca',{'synthetic':'ca'})
        self.ack = self.create()
        self.root_id = self.ack['creation_id']
        with control_store.connect() as db:
            self.new_id = db.execute('SELECT database_id FROM db_creations').fetchone()[0]
            service = db.execute('SELECT polaris_service_ref FROM catalog_bindings WHERE database_id=?',(self.new_id,)).fetchone()[0]
        self.config = prep.PreparationConfig(self.config,self.enrolled/'base',self.enrolled/'private',
            ROOT/'opentofu/dev',self.enrolled/'state',self.enrolled/'kubeconfig',self.enrolled/'auth',
            self.enrolled/'s3',self.enrolled/'output',self.enrolled/'ca','https://127.0.0.1:8443',
            tuple((s,'http://127.0.0.1:8181') for s in ['polaris-0','polaris-1','polaris-2',service]))
        self.facade = prep.Preparation(self.config)
        self.request = self.request_phase('bootstrap')
        self.commands = []
        original = prep._run
        def run(argv,**kwargs):
            self.commands.append(argv)
            if argv[0] == 'tofu':
                if argv[1] == 'plan':
                    path = Path(next(x[5:] for x in argv if x.startswith('-out=')))
                    path.write_bytes(b'synthetic saved provider plan')
                    path.chmod(0o600)
                return ''
            return original(argv,**kwargs)
        def command(instance,argv,**kwargs):
            self.commands.append(argv)
            if 'show' in argv:
                return json.dumps(dict(configuration=dict(provider_config=dict(kubernetes=dict(expressions=dict(
                    config_context=dict(references=['var.kube_context']),config_path=dict(references=['var.kubeconfig_path']))))),
                    variables=dict(kube_context=dict(value=prep.local_gitops.CONTEXT),
                        kubeconfig_path=dict(value=str(self.config.kubeconfig))),resource_changes=[]))
            if 'kubectl' in argv:
                self.assertIn(str(self.config.kubeconfig),argv)
                return ''
            raise AssertionError('Unexpected external command')
        for context in (patch.object(prep,'_run',side_effect=run),patch.object(operator.Operator,'command',command)):
            context.start()
            self.addCleanup(context.stop)

    def write(self,name,value):
        operator.save_private(self.enrolled/name,value)

    def request_phase(self,phase):
        detail = creations.get(self.alice,'admin',self.root_id)
        return creations.mutate(self.alice,'admin',self.root_id,'PREPARE_PLAN',
            dict(expected_revision=detail['revision'],phase=phase),'prepare-'+phase)

    def prepare(self,request=None):
        return self.facade.prepare((request or self.request)['request_id'],self.executor,owner_id='canonical-owner')

    def directory_for(self,phase='bootstrap'):
        return self.config.output_root/self.root_id/phase/'1'


class RetiredBaselinePreparationTests(PreparationFixture):
    def retire_baseline(self, *, drain=False, before_revision=False):
        import database_retirement as retirement
        if before_revision:
            with control_store.connect() as db:
                dm.update_database_display(db,'opaque_1',1,'updated','metadata')
        if drain:
            with control_store.connect() as db:
                schema = db.execute("SELECT id FROM schemas WHERE database_id='opaque_1' LIMIT 1").fetchone()[0]
                db.execute('INSERT INTO query_records VALUES (?,?,?,?,?,?,?,?)',
                    ('baseline-drain','alice','opaque_1',schema,'warehouse',None,'RUNNING','now'))
        with control_store.connect() as db:
            revision = db.execute("SELECT revision FROM databases WHERE id='opaque_1'").fetchone()[0]
        started = retirement.request_retire(self.alice,'admin','opaque_1',
            dict(expected_revision=revision),'baseline-retire')
        self.assertEqual(started['state'],'RETIRING')
        if drain:
            pending = retirement.reconcile(self.alice,'admin',started['id'],
                dict(expected_revision=started['revision']),'baseline-drain')
            self.assertEqual(pending['state'],'RETIRING')
            self.assertTrue(pending['blockers'])
            with control_store.connect() as db:
                db.execute("UPDATE query_records SET state='FINISHED' WHERE id='baseline-drain'")
            started = pending
        terminal = retirement.reconcile(self.alice,'admin',started['id'],
            dict(expected_revision=started['revision']),'baseline-finalize')
        self.assertEqual(terminal['state'],'RETIRED')
        self.assertEqual(terminal['database']['revision'],revision+2)
        self.assertEqual(terminal['revision'],3 if drain else 2)
        return terminal

    def test_normal_nonpurge_retirement_prepares_with_legacy_material(self):
        terminal = self.retire_baseline(drain=True)
        public = catalog_admin.export_spec('retired-current',self.base['manifest']['spec'])
        previous = self.base['manifest']['spec']['databases']['opaque_1']
        self.assertEqual(public['databases']['opaque_1'],dict(previous,database_revision=3))
        with control_store.connect() as db:
            root = dict(db.execute('SELECT * FROM db_creations WHERE id=?',(self.root_id,)).fetchone())
            prep.target_only(db,self.base['manifest']['spec'],public,root,'bootstrap')
        linked = self.prepare()
        self.assertTrue(linked['plan_id'])
        bundle = generation.load_bundle(self.directory_for()/'bundle')
        self.assertEqual(bundle['manifest']['spec']['databases']['opaque_1'],public['databases']['opaque_1'])
        actual = json.loads(operator.private_file(self.directory_for()/'private-input.json'))
        for entry in self.base['tofu']['databases'].values():
            for role in material.DB_KEYS:
                ref = material.ref_identity(entry[role])
                self.assertEqual(actual['secrets'][ref],self.private['secrets'][ref])
        for user,password in self.private['passwords'].items():
            self.assertEqual(actual['passwords'][user],password)
        self.assertEqual(json.loads(operator.private_file(self.enrolled/'private')),self.private)
        self.assertFalse(any('apply' in argv for argv in self.commands))
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM database_retirements WHERE operation_id=?',
                (terminal['id'],)).fetchone()[0],'RETIRED')

    def test_normal_retirement_operation_revision_two_is_sufficient(self):
        self.retire_baseline()
        self.assertTrue(self.prepare()['plan_id'])
        self.assertFalse(any('apply' in argv for argv in self.commands))

    def test_retirement_revision_exception_rejects_incomplete_or_unrelated_evidence(self):
        terminal = self.retire_baseline()
        cases = [
            ("UPDATE databases SET state=? WHERE id='opaque_1'",(state,))
            for state in ('READY','RECONCILING','RETIRING')]
        cases += [
            ("UPDATE database_retirements SET database_id='opaque_2' WHERE database_id='opaque_1'",()),
            ("UPDATE database_retirements SET state='RETIRING' WHERE database_id='opaque_1'",()),
            ("UPDATE database_retirements SET revision=1 WHERE database_id='opaque_1'",()),
            ("UPDATE operations SET action='OTHER' WHERE id=?",(terminal['id'],)),
            ("UPDATE operations SET state='RETIRING' WHERE id=?",(terminal['id'],)),
            ("UPDATE operations SET expected_revision=2 WHERE id=?",(terminal['id'],)),
            ("UPDATE operations SET before_after=? WHERE id=?",(json.dumps([['database_id','opaque_2']]),terminal['id'])),
            ("UPDATE operations SET before_after='[]' WHERE id=?",(terminal['id'],)),
            ("UPDATE databases SET revision=revision+1 WHERE id='opaque_1'",()),
            ("UPDATE databases SET revision=revision-1 WHERE id='opaque_1'",()),
            ("UPDATE databases SET sql_catalog='changed' WHERE id='opaque_1'",()),
            ("UPDATE catalog_bindings SET polaris_realm='changed' WHERE database_id='opaque_1'",()),
            ("UPDATE catalog_bindings SET desired_revision=desired_revision+1 WHERE database_id='opaque_1'",()),
            ("UPDATE catalog_bindings SET applied_revision=0 WHERE database_id='opaque_1'",()),
            ("UPDATE schemas SET revision=revision+1 WHERE database_id='opaque_1'",()),
            ("INSERT INTO grants VALUES ('alice','opaque_1','admin',1)",()),
        ]
        for statement,params in cases:
            with self.subTest(statement=statement,params=params),control_store.connect() as db:
                db.execute('SAVEPOINT invalid_retirement')
                db.execute(statement,params)
                # Export on the same transaction to include each actual registry change.
                with patch.object(control_store,'connect') as connect:
                    from contextlib import nullcontext
                    connect.side_effect = lambda: nullcontext(db)
                    public = catalog_admin.export_spec('invalid-retired',self.base['manifest']['spec'])
                root = dict(db.execute('SELECT * FROM db_creations WHERE id=?',(self.root_id,)).fetchone())
                with patch.object(generation,'render') as render,self.assertRaisesRegex(core.OperationError,'UNAPPLIED_CHANGES'):
                    prep.target_only(db,self.base['manifest']['spec'],public,root,'bootstrap')
                render.assert_not_called()
                self.assertEqual(self.commands,[])
                self.assertFalse(self.config.output_root.exists())
                db.execute('ROLLBACK TO invalid_retirement')

    def test_metadata_revision_before_retirement_is_not_retirement_only(self):
        self.retire_baseline(before_revision=True)
        with self.assertRaisesRegex(core.OperationError,'UNAPPLIED_CHANGES'):
            self.prepare()
        self.assertEqual(self.commands,[])
        self.assertFalse(self.directory_for().exists())

    def test_metadata_revision_after_retirement_is_not_retirement_only(self):
        self.retire_baseline()
        with control_store.connect() as db:
            dm.update_database_display(db,'opaque_1',3,'updated','metadata')
        with self.assertRaisesRegex(core.OperationError,'UNAPPLIED_CHANGES'):
            self.prepare()
        self.assertEqual(self.commands,[])
        self.assertFalse(self.directory_for().exists())


class PreparationTests(PreparationFixture):
    def test_executor_restore_and_release_cannot_revive_captured_claim_or_seal(self):
        with patch.object(creations,'_prepare_linked_plan',side_effect=ConnectionError):
            with self.assertRaises(ConnectionError):
                self.prepare()
        fence = auth.claim_preparation(self.config.enrollment,self.executor,self.request['request_id'],owner_id='canonical-owner')
        before = len(self.commands)
        self.accounts['executor']['disabled'] = True
        self.save_accounts()
        with self.assertRaisesRegex(core.OperationError,'ADMIN_REQUIRED'),auth.transaction() as db:
            db.execute("UPDATE databases SET state='READY' WHERE id=?",(self.new_id,))
            auth.preparation_guard(db,fence)
        self.accounts['executor']['disabled'] = False
        self.save_accounts()
        for action in (lambda:self.prepare(),
                lambda:auth.claim_preparation(self.config.enrollment,self.executor,self.request['request_id'],owner_id='canonical-owner')):
            with self.assertRaisesRegex(core.OperationError,'PRINCIPAL_STOPPED'):
                action()
        with self.assertRaisesRegex(core.OperationError,'PRINCIPAL_STOPPED'),auth.transaction() as db:
            auth.preparation_guard(db,fence)
        with control_store.connect() as db:
            self.assertEqual(db.execute("SELECT state FROM db_creation_principal_gates WHERE principal_id='executor'").fetchone()[0],'STOPPED')
            self.assertNotEqual(db.execute('SELECT state FROM databases WHERE id=?',(self.new_id,)).fetchone()[0],'READY')
            self.assertEqual(db.execute('SELECT count(*) FROM db_creation_phase_plans').fetchone()[0],0)
        auth.release_principal(self.config.enrollment,self.executor,'executor','release-executor')
        with self.assertRaisesRegex(core.OperationError,'STALE_PLAN'),auth.transaction() as db:
            auth.preparation_guard(db,fence)
        for action in (lambda:self.prepare(),
                lambda:auth.claim_preparation(self.config.enrollment,self.executor,self.request['request_id'],owner_id='canonical-owner')):
            with self.assertRaisesRegex(core.OperationError,'STALE_PLAN'):
                action()
        self.assertEqual(len(self.commands),before)
        detail = creations.get(self.alice,'admin',self.root_id)
        with control_store.connect() as db:
            old = dict(db.execute('SELECT * FROM db_creation_requests WHERE id=?',(self.request['request_id'],)).fetchone())
            root = dict(db.execute('SELECT * FROM db_creations WHERE id=?',(self.root_id,)).fetchone())
        self.assertTrue(root['reservation_active'])
        sealed = {str(p.relative_to(self.directory_for())):p.read_bytes()
            for p in self.directory_for().rglob('*') if p.is_file()}
        self.assertEqual(json.loads(sealed['journal.json'])['state'],'SEALED')
        with self.assertRaisesRegex(core.OperationError,'RECONCILIATION_REQUIRED'):
            creations.mutate(self.alice,'admin',self.root_id,'REPLAN',
                dict(expected_revision=detail['revision'],phase='bootstrap'),'fresh-after-release')
        with control_store.connect() as db:
            self.assertEqual(dict(db.execute('SELECT * FROM db_creation_requests WHERE id=?',
                (self.request['request_id'],)).fetchone()),old)
            self.assertEqual(dict(db.execute('SELECT * FROM db_creations WHERE id=?',(self.root_id,)).fetchone()),root)
            self.assertEqual(db.execute('SELECT count(*) FROM db_creation_phase_plans').fetchone()[0],0)
        self.assertEqual({str(p.relative_to(self.directory_for())):p.read_bytes()
            for p in self.directory_for().rglob('*') if p.is_file()},sealed)
        self.assertEqual(len(self.commands),before)

    def test_new_target_extra_grant_before_render_is_rejected(self):
        with control_store.connect() as db:
            dm.create_grant(db,'bob',self.new_id,'admin')
        with patch.object(generation,'load_bundle',return_value=self.base),patch.object(generation,'render') as render:
            with self.assertRaisesRegex(core.OperationError,'UNAPPLIED_CHANGES'):
                self.prepare()
            render.assert_not_called()
        self.assertFalse(self.commands)

    def test_real_tofu_subprocesses_share_phase_data_directory_and_preserve_environment(self):
        processes = []
        real_process = prep.subprocess.run
        def process(argv,**kwargs):
            if argv[:3] == ['rtk','proxy','tofu']:
                processes.append((argv[3],kwargs))
                if argv[3] == 'plan':
                    Path(next(x[5:] for x in argv if x.startswith('-out='))).write_bytes(b'saved plan')
                    provider = Path(kwargs['env']['TF_DATA_DIR'])/'providers/test-provider'
                    provider.parent.mkdir()
                    provider.write_bytes(b'executable cache excluded from seal')
                    provider.chmod(0o755)
                document = dict(configuration=dict(provider_config=dict(kubernetes=dict(expressions=dict(
                    config_context=dict(constant_value=prep.local_gitops.CONTEXT),
                    config_path=dict(references=['var.kubeconfig_path']))))),
                    variables=dict(kubeconfig_path=dict(value=str(self.config.kubeconfig))),resource_changes=[])
                return SimpleNamespace(returncode=0,stdout=json.dumps(document))
            if argv[:3] == ['rtk','proxy','kubectl']:
                return SimpleNamespace(returncode=0,stdout='')
            return real_process(argv,**kwargs)
        with patch.dict(os.environ,TF_DATA_DIR='/sentinel/shared',HOME='/preserved/home',
                home='/preserved/lowercase',CODEX_HOME='/preserved/codex'),\
                patch.object(prep,'_run',side_effect=REAL_RUN),\
                patch.object(operator.Operator,'command',REAL_COMMAND),\
                patch.object(prep.subprocess,'run',side_effect=process):
            parent = dict(os.environ)
            self.prepare()
            bound = operator.Operator(self.directory_for()/'operator.json')
            operator._apply_saved_plan(bound.config['tofu_plan'],bound.config['tofu_state'],bound.config['tofu_workdir'],
                tofu_data_dir=bound.config['tofu_data_dir'])
            expected = dict(parent,TF_DATA_DIR=bound.config['tofu_data_dir'])
            self.assertEqual([name for name,_ in processes],['init','plan','show','apply'])
            for _,kwargs in processes:
                self.assertEqual(kwargs['env'],expected)
            self.assertEqual(dict(os.environ),parent)
            data_dir = Path(bound.config['tofu_data_dir'])
            self.assertEqual(data_dir,Path(bound.config['tofu_workdir'])/'.terraform')
            provider = data_dir/'providers/test-provider'
            self.assertEqual(provider.stat().st_mode&0o777,0o755)
            # Executable cache changes do not alter the artifact seal.
            provider.write_bytes(b'cache update')
            self.prepare()

    def test_enrolled_external_inputs_fail_before_first_operator_bind(self):
        self.prepare()
        path = self.directory_for()/'operator.json'
        config = json.loads(operator.private_file(path))
        for enrolled in (self.config.auth_headers,self.config.s3_credentials,self.config.kubeconfig,self.config.trino_ca):
            with self.subTest(input=enrolled.name):
                original = enrolled.read_bytes()
                self.assertEqual(config['preparation_inputs'][str(enrolled)],operator.digest(original))
                enrolled.write_bytes(original+b' ')
                with patch.object(operator.Operator,'bind') as bind,\
                        patch.object(operator.subprocess,'run') as process:
                    with self.assertRaisesRegex(ValueError,'Sealed preparation inputs changed'):
                        operator.Operator(path).bind({})
                    bind.assert_not_called()
                    process.assert_not_called()
                enrolled.write_bytes(original)
        # State legitimately changes during apply; it is not an immutable input.
        self.assertNotIn(str(self.config.tofu_state),config['preparation_inputs'])
        self.write('state',dict(lineage='test-lineage',serial=4))
        operator.Operator(path)

    def test_full_reconciling_requires_exact_prepared_root_and_reservation(self):
        bootstrap,_ = self.complete_bootstrap()
        request = self.request_phase('full')
        with control_store.connect() as db:
            self.assertEqual(db.execute("SELECT state FROM databases WHERE id='opaque_1'").fetchone()[0],'reconciling')
            self.assertEqual(db.execute("SELECT state FROM databases WHERE id='opaque_0'").fetchone()[0],'retired')
            root = dict(db.execute('SELECT * FROM db_creations WHERE id=?',(self.root_id,)).fetchone())
            db.execute('''INSERT INTO operations SELECT 'other-owner',actor,action,targets,expected_revision,
                state,query_ids,apply_id,before_after,timestamps FROM operations WHERE id=?''',(bootstrap['id'],))
        public = catalog_admin.export_spec('full-snapshot',self.base['manifest']['spec'])
        for statement,params in (
                ("UPDATE warehouse_controls SET state='UNKNOWN'",()),
                ("UPDATE warehouse_controls SET operation_id='other-owner'",()),
                ("UPDATE operations SET state='UNKNOWN' WHERE id=?",(bootstrap['id'],)),
                ("UPDATE operations SET actor='bob' WHERE id=?",(bootstrap['id'],)),
                ("UPDATE db_creations SET reservation_active=0 WHERE id=?",(self.root_id,)),
                ("UPDATE databases SET state='reconciling' WHERE id='opaque_0'",())):
            with self.subTest(statement=statement),control_store.connect() as db:
                db.execute('SAVEPOINT invalid_predecessor')
                db.execute(statement,params)
                with self.assertRaisesRegex(core.OperationError,'UNAPPLIED_CHANGES'):
                    prep.target_only(db,self.base['manifest']['spec'],public,root,'full')
                db.execute('ROLLBACK TO invalid_predecessor')
        self.assertTrue(self.prepare(request)['plan_id'])

    def test_incremental_private_bytes_retired_and_shared_append_only(self):
        linked = self.prepare()
        directory = self.directory_for()
        actual = json.loads(operator.private_file(directory/'private-input.json'))
        bundle = generation.load_bundle(directory/'bundle')
        for key,entry in self.base['tofu']['databases'].items():
            for role in material.DB_KEYS:
                ref = material.ref_identity(entry[role])
                self.assertEqual(actual['secrets'][ref],self.private['secrets'][ref])
        for user,password in self.private['passwords'].items():
            self.assertEqual(actual['passwords'][user],password)
        before_auth = self.private['secrets'][material.ref_identity(self.base['tofu']['shared']['trino_auth_secret'])]['data']
        after_auth = actual['secrets'][material.ref_identity(bundle['tofu']['shared']['trino_auth_secret'])]['data']
        self.assertTrue(after_auth['password.db'].startswith(before_auth['password.db']))
        for key in ('tls.pem','shared-secret'):
            self.assertEqual(before_auth[key],after_auth[key])
        for role in ('postgres_secret','operator_secret','gitops_source_secret'):
            self.assertEqual(actual['secrets'][material.ref_identity(bundle['tofu']['shared'][role])],
                self.private['secrets'][material.ref_identity(self.base['tofu']['shared'][role])])
        self.assertEqual(json.loads(operator.private_file(self.enrolled/'private')),self.private)
        self.assertFalse(any('apply' in command for command in self.commands))
        self.assertTrue(any(command[:2]==['tofu','init'] and '-lockfile=readonly' in command for command in self.commands))
        self.assertTrue(any(command[:2]==['tofu','plan'] for command in self.commands))
        for path in directory.rglob('*'):
            self.assertEqual(path.stat().st_mode&0o777,0o700 if path.is_dir() else 0o600)
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM operations WHERE id=?',(linked['operation_id'],)).fetchone()[0],'AWAITING_APPLY')
            self.assertEqual(db.execute('SELECT state FROM databases WHERE id=?',(self.new_id,)).fetchone()[0].upper(),'REGISTERED')
            self.assertEqual(db.execute('SELECT state FROM databases WHERE id=?',('opaque_0',)).fetchone()[0],'retired')

    def test_same_request_concurrent_and_restart_rediscover_without_commands(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _:self.prepare(),range(2)))
        self.assertEqual(results[0],results[1])
        before = len(self.commands)
        self.facade = prep.Preparation(self.config)
        self.assertEqual(self.prepare(),results[0])
        self.assertEqual(len(self.commands),before)
        self.assertEqual(sum(command[:2]==['tofu','plan'] for command in self.commands),1)

    def test_response_loss_before_registration_rediscovered_from_seal(self):
        original = creations._prepare_linked_plan
        with patch.object(creations,'_prepare_linked_plan',side_effect=ConnectionError('response lost')):
            with self.assertRaises(ConnectionError):
                self.prepare()
        before = len(self.commands)
        linked = self.prepare()
        self.assertEqual(len(self.commands),before)
        self.assertTrue(linked['operation_id'])

    def test_artifact_and_journal_tamper_rejected(self):
        self.prepare()
        path = self.directory_for()/'saved.plan'
        path.write_bytes(b'changed')
        with self.assertRaisesRegex(core.OperationError,'RECONCILIATION_REQUIRED'):
            self.prepare()

    def test_unsealed_restart_requires_reconciliation_without_regeneration(self):
        original = prep._run
        def run(argv,**kwargs):
            if argv[:2]==['tofu','plan']:
                raise ConnectionError('planner response lost')
            return original(argv,**kwargs)
        with patch.object(prep,'_run',side_effect=run),self.assertRaises(ConnectionError):
            self.prepare()
        before = len(self.commands)
        self.facade = prep.Preparation(self.config)
        with self.assertRaisesRegex(core.OperationError,'RECONCILIATION_REQUIRED'):
            self.prepare()
        self.assertEqual(len(self.commands),before)

    def test_recomputed_artifact_seal_fails_against_canonical_private_reference(self):
        self.prepare()
        directory = self.directory_for()
        (directory/'saved.plan').write_bytes(b'forged')
        journal = json.loads(operator.private_file(directory/'journal.json'))
        journal['files']['saved.plan']=operator.digest(b'forged')
        operator.save_private(directory/'journal.json',journal)
        with self.assertRaisesRegex(core.OperationError,'RECONCILIATION_REQUIRED'):
            self.prepare()

    def test_unrelated_desired_change_before_render_rejected(self):
        with control_store.connect() as db:
            db.execute("UPDATE databases SET revision=revision+1 WHERE id='opaque_1'")
        with patch.object(generation,'render') as render,self.assertRaisesRegex(core.OperationError,'UNAPPLIED_CHANGES'):
            # load_bundle also renders to verify the frozen base, which is allowed.
            render.side_effect = RuntimeError('unused')
            with patch.object(generation,'load_bundle',return_value=self.base):
                self.prepare()
        self.assertFalse(self.commands)

    def test_pending_grant_and_extra_registered_database_rejected(self):
        with control_store.connect() as db:
            dm.create_grant(db,'reader','opaque_1','bi_viewer')
        with self.assertRaisesRegex(core.OperationError,'UNAPPLIED_CHANGES'):
            self.prepare()
        self.assertFalse(self.commands)

    def test_unrelated_registered_database_rejected_before_render(self):
        with control_store.connect() as db:
            catalog_admin._create_database(db,'bob','unrelated','unrelated',json.dumps(
                dict(sql_catalog='unrelated',profile_id='local-polaris-1.8.0'),sort_keys=True,separators=(',',':')))
        with self.assertRaisesRegex(core.OperationError,'UNAPPLIED_CHANGES'):
            self.prepare()
        self.assertFalse(self.commands)

    def test_final_registration_rechecks_registry_and_executor(self):
        original = creations._prepare_linked_plan
        def mutate(*args,**kwargs):
            with control_store.connect() as db:
                db.execute("UPDATE databases SET revision=revision+1 WHERE id='opaque_2'")
            return original(*args,**kwargs)
        with patch.object(creations,'_prepare_linked_plan',side_effect=mutate):
            with self.assertRaisesRegex(core.OperationError,'STALE_REGISTRY'):
                self.prepare()
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM db_creation_phase_plans').fetchone()[0],0)

    def test_executor_loss_before_io_is_durable_stop(self):
        self.accounts['executor']['disabled']=True
        self.save_accounts()
        with self.assertRaisesRegex(core.OperationError,'ADMIN_REQUIRED'):
            self.prepare()
        self.assertFalse(self.commands)
        with control_store.connect() as db:
            self.assertEqual(db.execute("SELECT state FROM db_creation_principal_gates WHERE principal_id='executor'").fetchone()[0],'STOPPED')

    def test_final_executor_loss_rolls_back_registration_and_durably_stops(self):
        original = creations._prepare_linked_plan
        def revoke(*args,**kwargs):
            self.accounts['executor']['disabled']=True
            self.save_accounts()
            return original(*args,**kwargs)
        with patch.object(creations,'_prepare_linked_plan',side_effect=revoke),self.assertRaisesRegex(core.OperationError,'ADMIN_REQUIRED'):
            self.prepare()
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM db_creation_phase_plans').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM generation_plans').fetchone()[0],0)
            self.assertEqual(db.execute("SELECT state FROM db_creation_principal_gates WHERE principal_id='executor'").fetchone()[0],'STOPPED')

    def test_final_requester_loss_rolls_back_registration_and_durably_stops(self):
        original = creations._prepare_linked_plan
        def revoke(*args,**kwargs):
            self.accounts['alice']['disabled']=True
            self.save_accounts()
            return original(*args,**kwargs)
        with patch.object(creations,'_prepare_linked_plan',side_effect=revoke),self.assertRaisesRegex(core.OperationError,'ADMIN_REQUIRED'):
            self.prepare()
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM db_creation_phase_plans').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM generation_plans').fetchone()[0],0)
            self.assertEqual(db.execute("SELECT state FROM db_creation_principal_gates WHERE principal_id='alice'").fetchone()[0],'STOPPED')

    def test_requester_stop_during_plan_prevents_show_and_registration(self):
        original = prep._run
        def run(argv,**kwargs):
            value = original(argv,**kwargs)
            if argv[:2]==['tofu','plan']:
                auth.stop_principal(self.config.enrollment,self.executor,'alice','during-plan')
            return value
        with patch.object(prep,'_run',side_effect=run):
            with self.assertRaisesRegex(core.OperationError,'PRINCIPAL_STOPPED'):
                self.prepare()
        self.assertFalse(any('show' in command for command in self.commands))
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM db_creation_phase_plans').fetchone()[0],0)

    def complete_bootstrap(self):
        linked = self.prepare()
        detail = creations.get(self.alice,'admin',self.root_id)
        plan = detail['plans'][0]
        request = creations.mutate(self.alice,'admin',self.root_id,'APPROVE',dict(expected_revision=detail['revision'],
            plan_id=plan['plan_id'],operation_id=plan['operation_id'],expected_operation_revision=plan['operation_revision'],
            delivery_digest=plan['delivery_digest']),'approve-bootstrap',phase='bootstrap')
        permit = auth.claim(self.config.enrollment,self.executor,request['request_id'],owner_id='canonical-owner')
        directory = self.directory_for()
        bundle = generation.load_bundle(directory/'bundle')
        private = json.loads(operator.private_file(directory/'private-input.json'))
        bound = operator.Operator(directory/'operator.json')
        observations = json.loads(operator.private_file(directory/'journal.json'))['observations']
        provisioning = {}
        for key,entry in bundle['tofu']['databases'].items():
            consumers = {}
            for consumer,role in (('trino','trino_secret'),('iceberg-bootstrap','bootstrap_secret')):
                ref = material.ref_identity(entry[role])
                data = private['secrets'].get(ref,dict(data=dict(client_id=key+consumer,
                    client_secret='issued-secret-'+consumer,realm=entry['realm'])))['data']
                consumers[consumer] = dict(data,principal_name=key+consumer)
            provisioning[key]=dict(consumers=consumers,leaves={c:dict(state='ACKNOWLEDGED') for c in consumers})
        self.issued = copy.deepcopy(provisioning)
        def verify(current):
            proof = dict(operation_id=current['id'],binding=bound.binding,
                configuration=dict(generation_id=current['generation_id']),
                provisioning_digest=operator.digest(operator.canonical(provisioning)))
            evidence = 'bootstrap-prepared:'+operator.digest(operator.canonical(proof))
            operator.save_private(control_store.root()/'warehouse-operator-receipts'/(current['id']+'.json'),
                dict(actor=current['actor'],binding=bound.binding,provisioning=provisioning,
                     bootstrap_complete=dict(proof=proof,evidence_ref=evidence)))
            return dict(verified=True,evidence_ref=evidence)
        with auth.delegated(permit):
            current = core.apply(plan['operation_id'],actor='alice',expected_revision=1,
                delivery_digest=plan['delivery_digest'],authorize=lambda actor:True,
                observe_secrets=lambda p:copy.deepcopy(observations),execute_step=lambda s,p:dict(evidence_ref='ack:'+s),verify=verify)
        self.assertEqual(current['state'],'PREPARED')
        return current,permit

    def test_full_reuses_issued_values_and_requires_separate_approval(self):
        bootstrap,old_permit = self.complete_bootstrap()
        request = self.request_phase('full')
        linked = self.prepare(request)
        directory = self.directory_for('full')
        private = json.loads(operator.private_file(directory/'private-input.json'))
        bundle = generation.load_bundle(directory/'bundle')
        bootstrap_input = json.loads(operator.private_file(self.directory_for()/'private-input.json'))
        self.assertEqual(private['passwords'],bootstrap_input['passwords'])
        for key,entry in bundle['tofu']['databases'].items():
            for consumer,role in (('trino','trino_secret'),('iceberg-bootstrap','bootstrap_secret')):
                self.assertEqual(private['secrets'][material.ref_identity(entry[role])]['data'],
                    {k:self.issued[key]['consumers'][consumer][k] for k in ('client_id','client_secret','realm')})
        current = core.inspect(linked['operation_id'],actor='alice')
        self.assertEqual((current['state'],current['predecessor_id']),('AWAITING_APPLY',bootstrap['id']))
        with auth.delegated(old_permit),auth.transaction() as db,self.assertRaises(core.OperationError):
            auth.guard(db,current['id'])
        detail = creations.get(self.alice,'admin',self.root_id)
        full_plan = next(p for p in detail['plans'] if p['phase']=='full')
        approval = creations.mutate(self.alice,'admin',self.root_id,'APPROVE',dict(expected_revision=detail['revision'],
            plan_id=full_plan['plan_id'],operation_id=current['id'],expected_operation_revision=current['revision'],
            delivery_digest=current['delivery_digest']),'approve-full',phase='full')
        fresh = auth.claim(self.config.enrollment,self.executor,approval['request_id'],owner_id='canonical-owner')
        self.assertNotEqual(fresh.approval_request_id,old_permit.approval_request_id)
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT reservation_active FROM db_creations').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT state FROM warehouse_controls').fetchone()[0],'APPLYING')
            self.assertNotEqual(db.execute('SELECT state FROM databases WHERE id=?',(self.new_id,)).fetchone()[0].upper(),'READY')

    def test_full_missing_receipt_and_forged_credential_rejected(self):
        bootstrap,_ = self.complete_bootstrap()
        request = self.request_phase('full')
        path = control_store.root()/'warehouse-operator-receipts'/(bootstrap['id']+'.json')
        value = json.loads(operator.private_file(path))
        value['provisioning'][self.new_id]['consumers']['trino']['client_secret']='forged'
        value['bootstrap_complete']['proof']['provisioning_digest']=operator.digest(operator.canonical(value['provisioning']))
        value['bootstrap_complete']['evidence_ref']='bootstrap-prepared:'+operator.digest(operator.canonical(value['bootstrap_complete']['proof']))
        operator.save_private(path,value)
        with self.assertRaisesRegex(RuntimeError,'original ledger'):
            self.prepare(request)
        with control_store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM db_creation_phase_plans WHERE phase='full'").fetchone()[0],0)

    def test_full_absent_receipt_is_not_replaced_with_supplied_material(self):
        bootstrap,_ = self.complete_bootstrap()
        request = self.request_phase('full')
        path = control_store.root()/'warehouse-operator-receipts'/(bootstrap['id']+'.json')
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.prepare(request)
        self.assertFalse((self.directory_for('full')/'saved.plan').exists())


class InitialGrantPreparationTests(PreparationFixture):
    complete_bootstrap = PreparationTests.complete_bootstrap

    def create(self, **kwargs):
        return super().create(initial_grants=[dict(principal_id='reader',maximum_role='bi_viewer')],**kwargs)

    def reject_before_render(self, mutation):
        with control_store.connect() as db:
            mutation(db)
        with patch.object(generation,'load_bundle',return_value=self.base),patch.object(generation,'render') as render:
            with self.assertRaisesRegex(core.OperationError,'UNAPPLIED_CHANGES'):
                self.prepare()
            render.assert_not_called()
        self.assertFalse(self.commands)
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM db_creation_phase_plans').fetchone()[0],0)

    def test_target_extra_principal_before_render(self):
        self.reject_before_render(lambda db:dm.create_grant(db,'bob',self.new_id,'admin'))

    def test_target_changed_role_before_render(self):
        self.reject_before_render(lambda db:dm.update_grant(db,'reader',self.new_id,1,'admin'))

    def test_target_revoked_initial_grant_before_render(self):
        self.reject_before_render(lambda db:dm.update_grant(db,'reader',self.new_id,1,'revoked'))

    def test_target_removed_initial_grant_before_render(self):
        self.reject_before_render(lambda db:db.execute('DELETE FROM grants WHERE database_id=?',(self.new_id,)))

    def test_target_grant_race_before_final_registration(self):
        original = creations._prepare_linked_plan
        def race(*args,**kwargs):
            with control_store.connect() as db:
                dm.update_grant(db,'reader',self.new_id,1,'admin')
            return original(*args,**kwargs)
        with patch.object(creations,'_prepare_linked_plan',side_effect=race):
            with self.assertRaisesRegex(core.OperationError,'UNAPPLIED_CHANGES'):
                self.prepare()
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM db_creation_phase_plans').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM warehouse_config_generations').fetchone()[0],0)

    def test_original_pending_initial_grant_survives_bootstrap_and_full_without_promotion(self):
        self.complete_bootstrap()
        self.assertTrue(self.prepare(self.request_phase('full'))['plan_id'])
        with control_store.connect() as db:
            self.assertEqual([tuple(r) for r in db.execute(
                'SELECT principal_id,maximum_role,revision FROM grants WHERE database_id=?',(self.new_id,))],
                [('reader','bi_viewer',1)])
            self.assertEqual([tuple(r) for r in db.execute(
                'SELECT principal_id,maximum_role FROM effective_grants WHERE database_id=?',(self.new_id,))],
                [('reader','revoked')])


class GrantReplanTests(PreparationFixture):
    complete_bootstrap = PreparationTests.complete_bootstrap

    def create(self, **kwargs):
        self.accounts['reader']['role'] = 'data_engineer'
        self.save_accounts()
        return super().create(initial_grants=[dict(principal_id='reader',maximum_role='data_engineer')],**kwargs)

    def change_grant(self, role, principal='reader', database=None):
        database = database or self.new_id
        with control_store.connect() as db:
            row = dm.get_grant(db,principal,database)
        revision = row.revision if row else 0
        return grant_management.mutate(self.alice,'admin',database,
            dict(principal_id=principal,maximum_role=role,expected_revision=revision),
            'grant-'+database+'-'+principal+'-'+str(revision))

    def replan(self, phase='full'):
        detail = creations.get(self.alice,'admin',self.root_id)
        return creations.mutate(self.alice,'admin',self.root_id,'REPLAN',
            dict(expected_revision=detail['revision'],phase=phase),'replan-'+str(detail['revision']))

    def grant_state(self):
        with control_store.connect() as db:
            return dict(intent=db.execute('SELECT intent_json FROM db_creations WHERE id=?',(self.root_id,)).fetchone()[0],
                desired=[dict(r) for r in db.execute('SELECT * FROM grants WHERE database_id=?',(self.new_id,))],
                effective=[dict(r) for r in db.execute('SELECT * FROM effective_grants WHERE database_id=?',(self.new_id,))])

    def assert_issued_values(self, request):
        with control_store.connect() as db:
            sequence = db.execute('SELECT requested_sequence FROM db_creation_requests WHERE id=?',
                (request['request_id'],)).fetchone()[0]
        directory = self.config.output_root/self.root_id/'full'/str(sequence)
        actual = json.loads(operator.private_file(directory/'private-input.json'))
        expected = json.loads(operator.private_file(self.directory_for()/'private-input.json'))
        bundle = generation.load_bundle(directory/'bundle')
        expected['generation_id'] = bundle['manifest']['generation_id']
        for key, entry in bundle['tofu']['databases'].items():
            for consumer, role in (('trino','trino_secret'),('iceberg-bootstrap','bootstrap_secret')):
                expected['secrets'][material.ref_identity(entry[role])] = dict(data={
                    k:self.issued[key]['consumers'][consumer][k] for k in ('client_id','client_secret','realm')})
        self.assertEqual(actual,expected)
        return bundle

    def full_approval(self, linked, key):
        detail = creations.get(self.alice,'admin',self.root_id)
        plan = next(p for p in detail['plans'] if p['plan_id'] == linked['plan_id'])
        payload = dict(expected_revision=detail['revision'],plan_id=plan['plan_id'],
            operation_id=plan['operation_id'],expected_operation_revision=plan['operation_revision'],
            delivery_digest=plan['delivery_digest'])
        ack = creations.mutate(self.alice,'admin',self.root_id,'APPROVE',payload,key,phase='full')
        return ack, payload

    def test_revoke_all_after_bootstrap_exposes_full_replan_and_preserves_issued_values(self):
        self.complete_bootstrap()
        self.assertEqual(creations.get(self.alice,'admin',self.root_id)['next_actions'],['withdraw','plans'])
        before_artifacts = prep._tree(self.directory_for())
        original_intent = self.grant_state()['intent']
        self.change_grant('revoked')
        self.accounts['reader']['disabled'] = True
        self.save_accounts()
        state = self.grant_state()
        detail = creations.get(self.alice,'admin',self.root_id)
        self.assertIn('replan',detail['next_actions'])
        self.assertEqual(detail['initial_grants'][0]['desired_role'],'revoked')
        # The ordinary initial policy remains exact, even for a full phase.
        public = catalog_admin.export_spec('initial-policy',self.base['manifest']['spec'])
        with control_store.connect() as db:
            root = dict(db.execute('SELECT * FROM db_creations WHERE id=?',(self.root_id,)).fetchone())
            with self.assertRaisesRegex(core.OperationError,'UNAPPLIED_CHANGES'):
                prep.target_only(db,self.base['manifest']['spec'],public,root,'full')
        request = self.replan()
        linked = self.prepare(request)
        bundle = self.assert_issued_values(request)
        self.assertEqual(bundle['manifest']['spec']['grant_revisions'],state['desired'])
        self.assertEqual(self.grant_state(),state)
        self.assertEqual(self.grant_state()['intent'],original_intent)
        self.assertEqual(state['effective'][0]['maximum_role'],'revoked')
        self.assertEqual(prep._tree(self.directory_for()),before_artifacts)
        with self.assertRaises(core.OperationError),auth.transaction() as db:
            auth.guard(db,linked['operation_id'])

    def test_old_approval_revoked_new_plan_needs_fresh_approval_and_ack_stays_retrievable(self):
        self.complete_bootstrap()
        first_request = self.request_phase('full')
        old = self.prepare(first_request)
        old_artifacts = prep._tree(self.directory_for('full'))
        approval, payload = self.full_approval(old,'old-full-approval')
        permit = auth.claim(self.config.enrollment,self.executor,approval['request_id'],owner_id='canonical-owner')
        self.change_grant('revoked')
        state = self.grant_state()
        request = self.replan()
        fresh = self.prepare(request)
        self.assertEqual(self.grant_state(),state)
        self.assertEqual(prep._tree(self.directory_for('full')),old_artifacts)
        self.assertEqual(creations.mutate(self.alice,'admin',self.root_id,'APPROVE',payload,
            'old-full-approval',phase='full'),approval)
        with self.assertRaises(core.OperationError):
            auth.claim(self.config.enrollment,self.executor,approval['request_id'],owner_id='canonical-owner')
        with auth.delegated(permit),auth.transaction() as db,self.assertRaises(core.OperationError):
            auth.guard(db,old['operation_id'])
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT approval_state FROM db_creation_requests WHERE id=?',
                (approval['request_id'],)).fetchone()[0],'REVOKED')
        detail = creations.get(self.alice,'admin',self.root_id)
        with self.assertRaisesRegex(core.OperationError,'STALE_PLAN'):
            creations.mutate(self.alice,'admin',self.root_id,'APPROVE',
                dict(payload,expected_revision=detail['revision']),'reuse-old-plan',phase='full')
        with auth.transaction() as db,self.assertRaises(core.OperationError):
            auth.guard(db,fresh['operation_id'])
        new_approval,_ = self.full_approval(fresh,'fresh-full-approval')
        new_permit = auth.claim(self.config.enrollment,self.executor,new_approval['request_id'],owner_id='canonical-owner')
        with auth.delegated(new_permit),auth.transaction() as db:
            auth.guard(db,fresh['operation_id'])
        self.assertNotEqual(new_approval['request_id'],approval['request_id'])

    def test_role_decrease_and_explicit_restore_within_intake_cap(self):
        self.complete_bootstrap()
        intent = self.grant_state()['intent']
        for role in ('bi_viewer','data_engineer'):
            with self.subTest(role=role):
                self.change_grant(role)
                state = self.grant_state()
                request = self.replan()
                self.prepare(request)
                self.assertEqual(self.assert_issued_values(request)['manifest']['spec']['grant_revisions'],state['desired'])
                self.assertEqual(self.grant_state(),state)
                self.assertEqual(state['effective'][0]['maximum_role'],'revoked')
                self.assertEqual(state['intent'],intent)

    def test_replan_rejects_added_principal_above_intake_and_live_account_cap(self):
        # Exercise public grant mutation, including changes that that API accepts.
        cases = ('added','above-intake','account-cap','disabled','missing')
        for case in cases:
            with self.subTest(case=case):
                with control_store.connect() as db:
                    db.execute('SAVEPOINT invalid_grants')
                    # Keep mutation and replan-policy reads in this disposable transaction.
                    principal = 'bob' if case == 'added' else 'reader'
                    role = 'admin' if case == 'above-intake' else 'data_engineer'
                    if case == 'above-intake':
                        self.accounts['reader']['role'] = 'admin'
                    elif case == 'account-cap':
                        self.accounts['reader']['role'] = 'bi_viewer'
                    elif case == 'disabled':
                        self.accounts['reader']['disabled'] = True
                    elif case == 'missing':
                        del self.accounts['reader']
                    self.save_accounts()
                    grant_management._mutate(self.alice,'admin',self.new_id,
                        dict(principal_id=principal,maximum_role=role,expected_revision=0 if case=='added' else 1),
                        'invalid-'+case,connection=db)
                    root = dict(db.execute('SELECT * FROM db_creations WHERE id=?',(self.root_id,)).fetchone())
                    with self.assertRaisesRegex(core.OperationError,'UNAPPLIED_CHANGES'):
                        creations._replan_target_grants(db,root)
                    db.execute('ROLLBACK TO invalid_grants')
                self.accounts['reader'] = dict(self.accounts.get('reader',self.accounts['bob']),role='data_engineer',disabled=False)
                self.save_accounts()

    def test_replan_rejects_unrelated_pending_changes(self):
        self.complete_bootstrap()
        self.change_grant('revoked')
        self.change_grant('bi_viewer',database='opaque_1')
        request = self.replan()
        with self.assertRaisesRegex(core.OperationError,'UNAPPLIED_CHANGES'):
            self.prepare(request)

    def test_replan_active_grant_uses_current_authentication_account_read(self):
        self.complete_bootstrap()
        self.accounts['reader']['role'] = 'bi_viewer'
        self.save_accounts()
        request = self.replan()
        with patch.object(generation,'load_bundle',return_value=self.base),\
                patch.object(generation,'render') as render,self.assertRaisesRegex(core.OperationError,'UNAPPLIED_CHANGES'):
            self.prepare(request)
        render.assert_not_called()

    def test_replan_omitted_principal_preserves_revocation_tombstone(self):
        self.complete_bootstrap()
        with control_store.connect() as db:
            db.execute('DELETE FROM grants WHERE database_id=?',(self.new_id,))
        state = self.grant_state()
        request = self.replan()
        self.prepare(request)
        self.assertEqual(self.assert_issued_values(request)['manifest']['spec']['grant_revisions'],[])
        self.assertEqual(self.grant_state(),state)
        self.assertEqual(state['effective'][0]['maximum_role'],'revoked')

    def test_replan_requires_bootstrap_receipt_without_base_credential_fallback(self):
        bootstrap,_ = self.complete_bootstrap()
        self.change_grant('revoked')
        request = self.replan()
        (control_store.root()/'warehouse-operator-receipts'/(bootstrap['id']+'.json')).unlink()
        with patch.object(prep,'incremental_input') as generate,self.assertRaises(FileNotFoundError):
            self.prepare(request)
        generate.assert_not_called()
        with control_store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM db_creation_phase_plans WHERE phase='full'").fetchone()[0],0)

    def test_replan_grant_races_during_generation_and_final_registration(self):
        for boundary in ('generation','registration'):
            with self.subTest(boundary=boundary):
                fixture = GrantReplanTests(self._testMethodName)
                try:
                    fixture.setUp()
                    fixture.complete_bootstrap()
                    fixture.change_grant('bi_viewer')
                    request = fixture.replan()
                    target, name = (generation,'render') if boundary=='generation' else (creations,'_prepare_linked_plan')
                    original = getattr(target,name)
                    def race(*args,**kwargs):
                        fixture.change_grant('revoked')
                        return original(*args,**kwargs)
                    with patch.object(target,name,side_effect=race),self.assertRaisesRegex(core.OperationError,'STALE_REGISTRY'):
                        fixture.prepare(request)
                    self.assertEqual(fixture.grant_state()['effective'][0]['maximum_role'],'revoked')
                    with control_store.connect() as db:
                        self.assertEqual(db.execute("SELECT count(*) FROM db_creation_phase_plans WHERE phase='full'").fetchone()[0],0)
                finally:
                    fixture.doCleanups()

    def test_initial_prepare_caller_mutation_cannot_enable_replan_policy(self):
        self.change_grant('revoked')
        caller = dict(self.request,action='REPLAN',phase='full')
        with self.assertRaisesRegex(core.OperationError,'UNAPPLIED_CHANGES'):
            self.prepare(caller)
        self.assertFalse(self.commands)

    def test_full_final_cas_drift_then_owned_verified_stale_replan(self):
        bootstrap,_ = self.complete_bootstrap()
        linked = self.prepare(self.request_phase('full'))
        approval,_ = self.full_approval(linked,'full-before-race')
        permit = auth.claim(self.config.enrollment,self.executor,approval['request_id'],owner_id='canonical-owner')
        def apply_full(linked, permit, directory, verify):
            journal = json.loads(operator.private_file(directory/'journal.json'))
            observations = copy.deepcopy(journal['observations'])
            def execute(step, current):
                if step == 'apply_secrets':
                    for row in observations:
                        row.update(exists=True,uid='test-'+row['name'],resource_version='1',immutable=True)
                return dict(evidence_ref='test:'+step)
            current = core.inspect(linked['operation_id'],actor='alice')
            with auth.delegated(permit):
                return core.apply(current['id'],actor='alice',expected_revision=current['revision'],
                    delivery_digest=current['delivery_digest'],authorize=lambda actor:True,
                    observe_secrets=lambda p:copy.deepcopy(observations),execute_step=execute,verify=verify)
        def verify(current):
            self.change_grant('revoked')
            return dict(verified=True,evidence_ref='test:verified-full')
        stale = apply_full(linked,permit,self.directory_for('full'),verify)
        self.assertEqual(stale['state'],'VERIFIED_STALE')
        state = self.grant_state()
        self.assertEqual(state['effective'][0]['maximum_role'],'revoked')
        self.assertTrue(all(s['state']=='SUCCEEDED' for s in stale['steps']))
        with control_store.connect() as db:
            self.assertEqual(tuple(db.execute('SELECT state,operation_id FROM warehouse_controls').fetchone()),
                ('APPLYING',linked['operation_id']))
            self.assertEqual(db.execute("SELECT state FROM databases WHERE id='opaque_1'").fetchone()[0],'reconciling')
        self.assertIn('replan',creations.get(self.alice,'admin',self.root_id)['next_actions'])
        public = catalog_admin.export_spec('owned-continuation',self.base['manifest']['spec'])
        request = self.replan()
        fence = auth.claim_preparation(self.config.enrollment,self.executor,request['request_id'],owner_id='canonical-owner')
        with control_store.connect() as db:
            root, canonical_request = auth.preparation_guard(db,fence)
            wrong_owner = db.execute("SELECT id FROM operations WHERE action='GRANT_CHANGE' LIMIT 1").fetchone()[0]
            for sql, params in (
                    ("UPDATE warehouse_controls SET operation_id=NULL",()),
                    ("UPDATE warehouse_controls SET operation_id=?",(wrong_owner,)),
                    ("UPDATE operations SET state='UNKNOWN' WHERE id=?",(linked['operation_id'],)),
                    ("UPDATE operation_requests SET predecessor_id=NULL WHERE operation_id=?",(linked['operation_id'],)),
                    ("UPDATE operation_requests SET attempt_id='active' WHERE operation_id=?",(linked['operation_id'],)),
                    ("UPDATE warehouse_controls SET state='UNKNOWN'",())):
                with self.subTest(sql=sql):
                    db.execute('SAVEPOINT invalid_owner')
                    db.execute(sql,params)
                    with self.assertRaisesRegex(core.OperationError,'UNAPPLIED_CHANGES'):
                        prep.target_only(db,self.base['manifest']['spec'],public,root,'full',request=canonical_request)
                    db.execute('ROLLBACK TO invalid_owner')
        fresh = self.prepare(request)
        self.assertEqual(core.inspect(fresh['operation_id'],actor='alice')['predecessor_id'],bootstrap['id'])
        self.assertEqual(self.grant_state(),state)
        self.assert_issued_values(request)
        new_approval,_ = self.full_approval(fresh,'full-after-replan')
        new_permit = auth.claim(self.config.enrollment,self.executor,new_approval['request_id'],owner_id='canonical-owner')
        applied = apply_full(fresh,new_permit,self.config.output_root/self.root_id/'full'/'2',
            lambda p:dict(verified=True,evidence_ref='test:fresh-verification'))
        self.assertEqual(applied['state'],'APPLIED')
        after = self.grant_state()
        self.assertEqual(after['desired'],state['desired'])
        self.assertEqual(after['intent'],state['intent'])
        self.assertEqual(after['effective'][0]['maximum_role'],'revoked')
        self.assertEqual(after['effective'][0]['epoch'],state['effective'][0]['epoch'])


class ManagedPreparationTests(PreparationFixture):
    complete_bootstrap = PreparationTests.complete_bootstrap

    def managed_candidate(self, existing=None, *, prepare=True):
        import database_creation_bridge as bridge
        import runtime_profile
        import uuid
        from types import SimpleNamespace
        if existing:
            managed,ignored,*rest = existing
            with patch.object(bridge,'current',return_value=managed):
                self.complete_bootstrap()
                linked = self.prepare(self.request_phase('full'))
                full = operator.Operator(self.directory_for('full')/'operator.json')
            return (managed,full,*rest)
        built = material.build_material(self.base,self.private,phase='full')
        material.write_material(built,self.enrolled/'base-material')
        control_store.root().chmod(0o700)
        self.account_file.chmod(0o600)
        client = self.enrolled/'client.json'
        operator.save_private(client,dict(username='alice',password='password',selected_role='admin'))
        base_profile = self.enrolled/'base-profile.json'
        operator.save_private(base_profile,dict(version=1,mode='database',state_dir=str(control_store.root().resolve()),
            auth_file=str(self.account_file),trino_auth_file=str(self.enrolled/'base-material/backend.json'),
            catalog_auth_file=str(self.enrolled/'base-material/catalog-auth.json'),trino_ca_file=str(self.config.trino_ca),
            client_auth_file=str(client),sql_endpoint_url='http://127.0.0.1:8080'))
        owners = {}
        for role,short in (('workbench','w'),('native','n')):
            directory = self.enrolled/short
            directory.mkdir(mode=0o700)
            owners[role] = dict(directory=str(directory),socket_path=str(directory/'ipc'),record_path=str(directory/'owner'))
            operator.save_private(directory/'owner',dict(version=1,environment_id=self.config.enrollment.environment_id,
                enrollment_id=self.config.enrollment.enrollment_id,enrollment_revision=1,owner_role=role,
                owner_instance_id=str(uuid.uuid4()),runtime_instance_id=str(uuid.uuid4()),
                socket_path=str(directory/'ipc'),capability='private-capability'))
        enrollment_path = self.enrolled/'managed.json'
        endpoints = tuple((service,'http://127.0.0.1:'+str(18181+i)) for i,(service,old) in enumerate(self.config.polaris_endpoints[:-1]))
        self.config = replace(self.config,managed_enrollment=str(enrollment_path),polaris_endpoints=endpoints,polaris_port_range=(18200,18220))
        nested = {f.name:str(getattr(self.config,f.name)) if f.type is Path else getattr(self.config,f.name)
                  for f in fields(self.config) if f.name not in ('enrollment','managed_enrollment')}
        value = dict(version=1,**self.config.enrollment.__dict__,state_dir=str(control_store.root().resolve()),
            base_runtime_profile=str(base_profile),executor_auth_file=str(self.config.auth_headers),owners=owners,
            origins=dict(workbench='http://127.0.0.1:8888',native='http://127.0.0.1:8080',backend=self.config.trino_endpoint),
            foundation=dict(kube_context=prep.local_gitops.CONTEXT,api_server='https://127.0.0.1:6443',
                ca_sha256='1'*64,node_uids=dict(node='uid'),tofu_lineage='test-lineage'),preparation=nested)
        operator.save_private(enrollment_path,value)
        # The explicit enrolled material represents an already APPLIED generation.
        # Later generations below are written only through the product core.
        with control_store.connect() as db:
            db.execute('INSERT INTO warehouse_config_generations VALUES (?,?,?,?,?,?,?)',
                (self.base['manifest']['generation_id'],'LOCAL','enrolled-base','[]','[]','{}','APPLIED'))
            db.execute('UPDATE warehouse_controls SET applied_generation_id=?',
                (self.base['manifest']['generation_id'],))
        self.facade = prep.Preparation(self.config)
        original = base_profile.read_bytes()
        import socket
        parent,child = socket.socketpair()
        self.addCleanup(parent.close); self.addCleanup(child.close)
        configuration = bridge.Configuration(enrollment_path)
        hello = dict(configuration.owner('workbench'),purpose='worker',
            private_enrollment_path=configuration.path,profile_path=str(base_profile))
        managed = bridge.Managed(child,hello,configuration)
        if not prepare:
            return managed,None,base_profile,client,owners,value,enrollment_path,original
        with patch.object(bridge,'current',return_value=managed):
            bootstrap,permit = self.complete_bootstrap()
            self.assertNotIn('managed_handoff',operator.Operator(self.directory_for()/'operator.json').config)
            request = self.request_phase('full')
            linked = self.prepare(request)
            full = operator.Operator(self.directory_for('full')/'operator.json')
            self.assertEqual(self.prepare(request),linked)
        return managed,full,base_profile,client,owners,value,enrollment_path,original

    def test_real_full_material_creates_new_sealed_profile_and_owner_handoff(self):
        self.complete_managed_full()

    def reenroll_applied(self, candidate):
        import database_creation_bridge as bridge
        import database_creation_worker as worker
        import runtime_profile
        import uuid
        managed,full,ignored,client,owners,value,path,original = candidate
        selected = worker.applied_material(managed.config)
        value = copy.deepcopy(value)
        value['revision'] += 1
        value['base_runtime_profile'] = selected.profile
        value['preparation'].update(base_bundle=str(selected.bundle),base_private=str(selected.private),
            polaris_endpoints=[list(pair) for pair in selected.endpoints.items()])
        for role in owners:
            directory = self.enrolled/(role[0]+str(value['revision']))
            directory.mkdir(mode=0o700)
            value['owners'][role] = dict(directory=str(directory),socket_path=str(directory/'ipc'),record_path=str(directory/'owner'))
            record = dict(managed.config.owner(role),enrollment_revision=value['revision'],
                owner_instance_id=str(uuid.uuid4()),runtime_instance_id=str(uuid.uuid4()),
                capability=str(uuid.uuid4()),socket_path=str(directory/'ipc'))
            operator.save_private(directory/'owner',record)
        path = self.enrolled/('managed-'+str(value['revision'])+'.json')
        operator.save_private(path,value)
        config = bridge.Configuration(path)
        self.config = config.preparation()
        self.facade = prep.Preparation(self.config)
        creations.install_foundation(config.enrollment,self.probe)
        hello = dict(config.owner('workbench'),purpose='worker',private_enrollment_path=config.path,
            profile_path=selected.profile)
        fresh = bridge.Managed(managed.channel,hello,config)
        return (fresh,None,Path(selected.profile),client,value['owners'],value,path,Path(selected.profile).read_bytes())

    def test_terminal_historical_base_two_reenrollments_prepare_with_fresh_owners(self):
        import database_creation_bridge as bridge
        import database_creation_worker as worker
        import runtime_profile
        candidate = self.complete_managed_full()
        retained = {}
        for revision in (2,3):
            old = candidate[0]
            selected = worker.applied_material(old.config)
            old_private = runtime_profile.private_json(selected.private)
            for path in (Path(old.config.path),selected.bundle.parent/'journal.json',
                         *[Path(o['record_path']) for o in old.config.value['owners'].values()]):
                retained[path] = path.read_bytes()
            candidate = self.reenroll_applied(candidate)
            managed = candidate[0]
            # Unrelated closed phase UNKNOWN evidence does not invalidate the
            # terminal head. Roll back before exercising ordinary recovery gates.
            with control_store.connect() as db:
                db.execute('SAVEPOINT unrelated_history')
                db.execute("""UPDATE operations SET state='UNKNOWN' WHERE id IN
                    (SELECT operation_id FROM db_creation_phase_plans WHERE phase='bootstrap')""")
                adopted = worker.applied_material(managed.config,db=db)
                db.execute('ROLLBACK TO unrelated_history')
            self.assertTrue(adopted.pin['base'])
            self.assertEqual((adopted.bundle,adopted.private,adopted.profile,adopted.endpoints),
                (selected.bundle,selected.private,selected.profile,selected.endpoints))
            for role in ('workbench','native'):
                self.assertNotEqual(old.config.owner(role)['owner_instance_id'],managed.config.owner(role)['owner_instance_id'])
                self.assertFalse((Path(candidate[4][role]['directory'])/'activations').exists())
            self.ack = creations.create(self.alice,'admin',dict(sql_catalog='revision'+str(revision)),
                'create-revision-'+str(revision))[0]
            self.root_id = self.ack['creation_id']
            with control_store.connect() as db:
                self.new_id = db.execute('SELECT database_id FROM db_creations WHERE id=?',(self.root_id,)).fetchone()[0]
                self.assertEqual(db.execute('SELECT reservation_active FROM db_creations WHERE id=?',(self.root_id,)).fetchone()[0],1)
            self.request = self.request_phase('bootstrap')
            with patch.object(bridge,'current',return_value=managed):
                self.assertTrue(self.prepare()['plan_id'])
            prepared = runtime_profile.private_json(self.directory_for()/'private-input.json')
            for user,password in old_private['passwords'].items():
                self.assertEqual(prepared['passwords'][user],password)
            for ref,secret in old_private['secrets'].items():
                if ref != material.ref_identity(generation.load_bundle(selected.bundle)['tofu']['shared']['trino_auth_secret']):
                    self.assertEqual(prepared['secrets'][ref],secret)
            if revision == 2:
                candidate = self.complete_managed_full(self.managed_candidate(existing=candidate))
            for path,content in retained.items():
                self.assertEqual(path.read_bytes(),content)
        with control_store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM db_creations WHERE terminal_outcome='APPLIED'").fetchone()[0],2)

    def test_terminal_historical_base_rejects_invalid_head_and_material(self):
        import database_creation_bridge as bridge
        import database_creation_worker as worker
        import runtime_profile
        first = self.complete_managed_full()
        old = first[0]
        selected = worker.applied_material(old.config)
        fresh = self.reenroll_applied(first)[0]
        # Start with a real successful adoption so negatives cannot pass merely
        # because historical generations are rejected unconditionally.
        self.assertTrue(worker.applied_material(fresh.config).pin['base'])
        failures = (
            ("UPDATE db_creation_phase_plans SET phase='bootstrap',sequence=2 WHERE id=?",(selected.pin['plan_id'],)),
            ("UPDATE db_creations SET terminal_outcome=NULL,reservation_active=1",()),
            ("UPDATE operations SET state='UNKNOWN' WHERE id=?",(selected.pin['operation_id'],)),
            ("UPDATE operations SET state='AWAITING_APPLY' WHERE id=?",(selected.pin['operation_id'],)),
            ("UPDATE db_creations SET environment_id='foreign'",()),
            ("UPDATE db_creations SET warehouse_id='foreign'",()),
            ("UPDATE db_creations SET enrollment_id='foreign'",()),
            ("UPDATE db_creations SET enrollment_revision=2",()),
            ("UPDATE db_creations SET enrollment_revision=3",()),
            ("UPDATE warehouse_config_generations SET state='PLANNED' WHERE id=?",(selected.pin['generation_id'],)),
            ("UPDATE warehouse_controls SET applied_generation_id=NULL",()),
            ("UPDATE db_creation_phase_plans SET phase='full',sequence=2,generation_id=? WHERE phase='bootstrap'",(selected.pin['generation_id'],)),
        )
        for sql,params in failures:
            with self.subTest(sql=sql),control_store.connect() as db:
                db.execute('SAVEPOINT bad_history')
                db.execute(sql,params)
                if sql.startswith("UPDATE db_creation_phase_plans SET phase='full'"):
                    db.execute("""UPDATE operations SET state='APPLIED' WHERE id IN
                        (SELECT operation_id FROM db_creation_phase_plans WHERE sequence=2)""")
                with self.assertRaises((core.OperationError,ValueError,FileNotFoundError)):
                    worker.applied_material(fresh.config,db=db)
                db.execute('ROLLBACK TO bad_history')
        value = fresh.config.value
        mutations = [('foundation',dict(value['foundation'],tofu_lineage='changed')),
            ('foundation',dict(value['foundation'],node_uids=dict(node='changed'))),
            ('state_dir',str(self.enrolled))]
        for key,replacement in mutations:
            changed = dict(value,**{key:replacement})
            with self.subTest(field=key,replacement=replacement):
                operator.save_private(Path(fresh.config.path),changed)
                with self.assertRaises((core.OperationError,ValueError)):
                    worker.applied_material(bridge.Configuration(fresh.config.path))
        operator.save_private(Path(fresh.config.path),value)
        for field,replacement in (
                ('base_bundle',str(self.enrolled/'base')),('base_private',str(self.enrolled/'private')),
                ('tofu_state',str(self.enrolled/'other-state')),
                ('polaris_endpoints',[[k,'http://127.0.0.1:19999'] for k in selected.endpoints])):
            changed = copy.deepcopy(value)
            changed['preparation'][field] = replacement
            with self.subTest(field=field):
                operator.save_private(Path(fresh.config.path),changed)
                with self.assertRaises((core.OperationError,ValueError,FileNotFoundError)):
                    worker.applied_material(bridge.Configuration(fresh.config.path))
        copied_profile = self.enrolled/'copied-profile.json'
        operator.save_private(copied_profile,runtime_profile.private_json(selected.profile))
        operator.save_private(Path(fresh.config.path),dict(value,base_runtime_profile=str(copied_profile)))
        with self.assertRaises((core.OperationError,ValueError)):
            worker.applied_material(bridge.Configuration(fresh.config.path))
        operator.save_private(Path(fresh.config.path),value)
        receipt = control_store.root()/'warehouse-operator-receipts'/(selected.pin['operation_id']+'.json')
        activation = next((Path(old.config.value['owners']['native']['directory'])/'activations').glob('*.json'))
        for path in (Path(old.config.path),selected.bundle.parent/'journal.json',selected.private,
                     Path(selected.profile),receipt,activation,self.config.tofu_state,
                     Path(runtime_profile.private_json(selected.profile)['trino_auth_file'])):
            original = path.read_bytes()
            for missing in (True,False):
                with self.subTest(path=str(path),missing=missing):
                    if missing:
                        path.unlink()
                    elif path == activation:
                        changed = json.loads(original)
                        changed['readiness']['state'] = 'INTENT'
                        operator.save_private(path,changed)
                    elif path == self.config.tofu_state:
                        operator.save_private(path,dict(json.loads(original),lineage='changed'))
                    elif path.name == 'journal.json':
                        operator.save_private(path,dict(json.loads(original),state='STARTED'))
                    elif path == receipt:
                        changed = json.loads(original)
                        changed['verification_complete']['proof']['operation_id'] = 'foreign'
                        operator.save_private(path,changed)
                    else:
                        path.write_bytes(original+b' ')
                    try:
                        with self.assertRaises((core.OperationError,ValueError,FileNotFoundError)):
                            worker.applied_material(fresh.config)
                    finally:
                        path.write_bytes(original); path.chmod(0o600)

    def test_initial_base_requires_exact_canonical_applied_generation(self):
        import database_creation_worker as worker
        managed,*rest = self.managed_candidate(prepare=False)
        for state in ('missing','different','not-applied'):
            with self.subTest(state=state),control_store.connect() as db:
                db.execute('SAVEPOINT initial_head')
                if state == 'missing':
                    db.execute('UPDATE warehouse_controls SET applied_generation_id=NULL')
                elif state == 'different':
                    db.execute('INSERT INTO warehouse_config_generations VALUES (?,?,?,?,?,?,?)',
                        ('different-base','LOCAL','different','[]','[]','{}','APPLIED'))
                    db.execute("UPDATE warehouse_controls SET applied_generation_id='different-base'")
                else:
                    db.execute("UPDATE warehouse_config_generations SET state='PLANNED'")
                with self.assertRaises(core.OperationError):
                    worker.applied_material(managed.config,db=db)
                db.execute('ROLLBACK TO initial_head')
        self.assertEqual(worker.applied_material(managed.config).bundle,self.config.base_bundle)

    def test_approved_or_unknown_runtime_candidate_is_not_applied_material(self):
        import database_creation_worker as worker
        managed,full,parent,approval,plan = self.approved_managed_candidate()
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM operations WHERE id=?',(plan['operation_id'],)).fetchone()[0],'AWAITING_APPLY')
        # Approval alone, with old runtimes, still selects the canonical base.
        self.assertEqual(worker.applied_material(managed.config).bundle,self.config.base_bundle)
        for role in ('workbench','native'):
            record = managed.config.owner(role)
            operator.save_private(Path(managed.config.value['owners'][role]['record_path']),dict(record,
                runtime_instance_id=full.config['managed_handoff']['owners'][role]['candidate_runtime_instance_id']))
        for state in ('AWAITING_APPLY','UNKNOWN'):
            with self.subTest(state=state),control_store.connect() as db:
                db.execute('UPDATE operations SET state=? WHERE id=?',(state,plan['operation_id']))
            with self.assertRaises((ValueError,FileNotFoundError,core.OperationError)):
                worker.applied_material(managed.config)

    def test_head_drift_during_planning_stops_before_next_io(self):
        import database_creation_bridge as bridge
        managed,*rest = self.managed_candidate(prepare=False)
        original = prep._run
        def run(argv,**kwargs):
            result = original(argv,**kwargs)
            if argv[:2] == ['tofu','init']:
                with control_store.connect() as db:
                    db.execute('UPDATE warehouse_controls SET applied_generation_id=NULL')
            return result
        with patch.object(bridge,'current',return_value=managed),patch.object(prep,'_run',side_effect=run):
            with self.assertRaises(core.OperationError):
                self.prepare()
        self.assertFalse(any(argv[:2] == ['tofu','plan'] for argv in self.commands))
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM db_creation_phase_plans').fetchone()[0],0)

    def test_head_drift_fences_sealed_rediscovery_final_cas_and_linked_retry(self):
        import database_creation_bridge as bridge
        managed,*rest = self.managed_candidate(prepare=False)
        original = creations._prepare_linked_plan
        def drift(*args,**kwargs):
            with control_store.connect() as db:
                db.execute('UPDATE warehouse_controls SET applied_generation_id=NULL')
            return original(*args,**kwargs)
        with patch.object(bridge,'current',return_value=managed),patch.object(creations,'_prepare_linked_plan',side_effect=drift):
            with self.assertRaises(core.OperationError):
                self.prepare()
        before = len(self.commands)
        sealed = (self.directory_for()/'private-input.json').read_bytes()
        with patch.object(bridge,'current',return_value=managed):
            with self.assertRaises(core.OperationError):
                self.prepare()
            with control_store.connect() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM db_creation_phase_plans').fetchone()[0],0)
                db.execute('UPDATE warehouse_controls SET applied_generation_id=?',(self.base['manifest']['generation_id'],))
            linked = self.prepare()
            with control_store.connect() as db:
                db.execute('UPDATE warehouse_controls SET applied_generation_id=NULL')
            with self.assertRaises(core.OperationError):
                self.prepare()
            with control_store.connect() as db:
                db.execute('UPDATE warehouse_controls SET applied_generation_id=?',(self.base['manifest']['generation_id'],))
            self.assertEqual(self.prepare(),linked)
        self.assertEqual(len(self.commands),before)
        self.assertEqual((self.directory_for()/'private-input.json').read_bytes(),sealed)

    def complete_managed_full(self, candidate=None, *, interrupt=True):
        import database_creation_bridge as bridge
        import runtime_profile
        import uuid
        from types import SimpleNamespace
        candidate = candidate or self.managed_candidate()
        managed,full,base_profile,client,owners,value,enrollment_path,original = candidate
        self.assertEqual(base_profile.read_bytes(),original)
        self.assertEqual(full.binding['runtime_profile'],runtime_profile.private_binding(full.config['runtime_profile']))
        profile = runtime_profile.load(full.config['runtime_profile'])
        self.assertEqual(profile.auth_file,str(self.account_file))
        self.assertEqual(profile.client_auth_file,str(client))
        self.assertEqual(profile.trino_auth_file,str(self.directory_for('full')/'material/backend.json'))
        for role,owner in full.config['managed_handoff']['owners'].items():
            self.assertEqual(owner['owner_instance_id'],managed.config.owner(role)['owner_instance_id'])
            self.assertNotEqual(owner['old_runtime_instance_id'],owner['candidate_runtime_instance_id'])
        # Exercise the real full verify, descriptor resolver, per-leaf authorizer
        # and SQL receipt paths. Only cluster/HTTP/owner process I/O is replaced.
        import base64
        import database_creation_worker as worker
        initial_attestation = worker.accepted_head(managed.config)[0]
        detail = creations.get(self.alice,'admin',self.root_id)
        public = next(p for p in detail['plans'] if p['phase']=='full')
        approval = creations.mutate(self.alice,'admin',self.root_id,'APPROVE',dict(expected_revision=detail['revision'],
            plan_id=public['plan_id'],operation_id=public['operation_id'],expected_operation_revision=public['operation_revision'],
            delivery_digest=public['delivery_digest']),'real-managed-full',phase='full')
        claimed = auth.claim(self.config.enrollment,self.executor,approval['request_id'],owner_id=managed.hello['owner_instance_id'])
        operation = core.inspect(public['operation_id'],actor='alice')
        full.bind(operation)
        full.check_receipt(operation)
        inputs = json.loads(operator.private_file(self.directory_for('full')/'material/terraform.tfvars.json'))
        full.secret_objects = {}
        for identity,secret in inputs['warehouse_generation_secrets'].items():
            namespace,name = identity.split('/',1)
            name = name.rsplit('@',1)[0]
            full.secret_objects[(namespace,name)] = dict(data={k:base64.b64encode(v.encode()).decode() for k,v in secret['data'].items()})
        attestation = full.config['managed_handoff']['runtime_attestation']
        calls = []
        sql_posts = []
        interrupted = []
        def ipc(role,action,rid,aid=None):
            record = managed.config.owner(role)
            if action == 'GET_REQUEST':
                return dict(state='NOT_FOUND',runtime_instance_id=record['runtime_instance_id'])
            if action == 'GET_ACTIVATION':
                path = Path(owners[role]['directory'])/'activations'/(aid+'.json')
                return dict(state=runtime_profile.private_json(path)['state'] if path.exists() else 'NOT_FOUND',runtime_instance_id=record['runtime_instance_id'],
                    previous_runtime_instance_id=full.config['managed_handoff']['owners'][role]['old_runtime_instance_id'])
            self.assertEqual(action,'ACTIVATE_CANDIDATE')
            import socket
            parent,channel = socket.socketpair()
            self.addCleanup(parent.close); self.addCleanup(channel.close)
            child = bridge.Managed(channel,dict(record,purpose='worker'),managed.config)
            message = dict(version=1,request_id=rid,activation_id=aid,owner_role=role,owner_instance_id=record['owner_instance_id'])
            resolved = worker.activation(child,message)
            path = Path(owners[role]['directory'])/'activations'/(aid+'.json')
            journal = runtime_profile.private_json(path) if path.exists() else dict(descriptor=resolved,state='UNSENT',
                **{leaf:dict(state='UNSENT') for leaf in ('stop','start','readiness')})
            self.assertEqual(journal['descriptor'],resolved)
            for leaf in ('stop','start','readiness'):
                intent = str(uuid.uuid4())
                journal[leaf] = dict(state='INTENT',intent_id=intent)
                operator.save_private(path,journal)
                authorized = worker.activation(child,dict(message,leaf=leaf,intent_id=intent),authorize=True)
                self.assertEqual(authorized['state'],'AUTHORIZED')
                self.assertEqual(set(authorized),{'version','state','request_id','activation_id','owner_role',
                    'owner_instance_id','leaf','intent_id','candidate_runtime_instance_id','profile_binding'})
                journal[leaf]['state'] = 'ACK'
                calls.append((role,leaf))
            journal['state'] = 'ACK'
            operator.save_private(path,journal)
            operator.save_private(Path(owners[role]['record_path']),dict(record,runtime_instance_id=resolved['candidate_runtime_instance_id']))
            if interrupt and role=='workbench' and not interrupted:
                interrupted.append(True)
                auth.stop_principal(self.config.enrollment,self.executor,'alice','stop-after-first-owner-'+self.root_id)
            return dict(state='ACK',runtime_instance_id=resolved['candidate_runtime_instance_id'],previous_runtime_instance_id=resolved['old_runtime_instance_id'])
        def http(request,**kwargs):
            role = 'workbench' if request.full_url.startswith(value['origins']['workbench']) else 'native'
            record = managed.config.owner(role)
            observed_attestation = attestation if record['runtime_instance_id']==full.config['managed_handoff']['owners'][role]['candidate_runtime_instance_id'] else initial_attestation
            if request.full_url.endswith('/api/v1/runtime'):
                return observed_attestation
            if request.full_url.endswith('/runtime-instance'):
                return dict(version=1,environment_id=self.config.enrollment.environment_id,owner_role=role,
                    owner_instance_id=record['owner_instance_id'],runtime_instance_id=record['runtime_instance_id'],runtime_attestation=observed_attestation)
            self.assertEqual(request.get_method(),'POST')
            self.assertTrue(request.full_url.endswith('/v1/statement'))
            sql_posts.append(request.full_url)
            username = request.get_header('X-trino-user')
            key = next(key for key,entry in full.bundle['manifest']['spec']['databases'].items()
                       if '"'+entry['sql_catalog']+'"' in request.data.decode())
            database = full.bundle['manifest']['spec']['databases'][key]
            return dict(id='sql-'+str(uuid.uuid4()),columns=[dict(name=n) for n in ('verified_user','catalog_name','schema_name')],
                data=[[username,database['sql_catalog'],s['sql_name']] for s in database['schemas']])
        managed.ipc = Mock(side_effect=ipc)
        configuration = dict(generation_id=operation['generation_id'])
        observations = json.loads(operator.private_file(self.directory_for('full')/'journal.json'))['observations']
        current_observations = copy.deepcopy(observations)
        def execute(step,current):
            if step=='apply_secrets':
                for row in current_observations:
                    row.update(exists=True,uid='uid-'+row['name'],resource_version='1',immutable=True)
            return dict(evidence_ref='ack:'+step)
        real_resolve = worker.resolve
        def resolve(m,rid):
            request,root,plan,ignored = real_resolve(m,rid)
            return request,root,plan,full
        def polaris(key,creds):
            entry = full.bundle['manifest']['spec']['databases'][key]
            return dict(database_id=key,realm=entry['binding']['polaris_realm'],catalog=entry['binding']['polaris_catalog'],
                        namespaces=sorted(s['sql_name'] for s in entry['schemas']))
        import database_creation_recovery as recovery
        errors = []
        def verify(current):
            try:
                return full.verify(current)
            except Exception as error:
                errors.append(error)
                raise
        with patch.object(bridge,'current',return_value=managed),patch.object(full,'verification_configuration',return_value=configuration),\
                patch.object(full,'start_transports'),patch.object(full,'provision_databases'),patch.object(full,'http',side_effect=http),\
                patch.object(full,'verify_polaris',side_effect=polaris),patch.object(worker,'resolve',side_effect=resolve),\
                patch.object(prep.local_gitops,'observe_public_generation',return_value=dict(evidence_ref='observed:public')):
            with auth.delegated(claimed):
                result = core.apply(operation['id'],actor='alice',expected_revision=operation['revision'],
                    delivery_digest=operation['delivery_digest'],authorize=full.authorize,observe_secrets=lambda p:copy.deepcopy(current_observations),
                    execute_step=execute,verify=verify)
            if not calls and errors:
                raise errors[0]
            if not interrupt:
                self.assertEqual(result['state'],'APPLIED')
                self.assertEqual(calls,[(role,leaf) for role in ('workbench','native') for leaf in ('stop','start','readiness')])
                return candidate
            self.assertEqual(result['state'],'UNKNOWN')
            self.assertEqual(calls,[('workbench',leaf) for leaf in ('stop','start','readiness')])
            self.assertEqual(full.receipt['runtime_activations']['native']['state'],'UNSENT')
            request,root,original,ignored = real_resolve(managed,approval['request_id'])
            native_descriptor = worker.descriptor(request,root,original,full,'native')
            native_path = Path(owners['native']['directory'])/'activations'/(native_descriptor['activation_id']+'.json')
            # Go retains the rejected authorization's intent UUID when it
            # positively journals UNSENT. That is classified, not UNKNOWN.
            operator.save_private(native_path,dict(descriptor=native_descriptor,state='REJECTED',
                stop=dict(state='UNSENT',intent_id=str(uuid.uuid4())),start=dict(state='UNSENT'),readiness=dict(state='UNSENT')))
            parent_pin = runtime_profile.private_json(self.directory_for('full')/'journal.json')['pins']['applied_material']
            if not parent_pin.get('base'):
                effects_before_observation = (list(calls),list(sql_posts))
                initial_attestation_profile = next(
                    path for path in full.config['preparation_inputs']
                    if path.endswith('/runtime-profile.json'))
                parent_receipt = control_store.root()/'warehouse-operator-receipts'/(parent_pin['operation_id']+'.json')
                # Original parent bytes are immutable even when HTTP is healthy.
                for path in (Path(initial_attestation_profile),parent_receipt):
                    original_bytes = path.read_bytes()
                    for changed in (None,original_bytes+b' '):
                        with self.subTest(parent=path.name,missing=changed is None):
                            if changed is None:
                                path.unlink()
                            else:
                                path.write_bytes(changed)
                            try:
                                with self.assertRaises((core.OperationError,ValueError,FileNotFoundError)):
                                    recovery.observe(managed,root,original,full)
                            finally:
                                path.write_bytes(original_bytes); path.chmod(0o600)
                # A wrong old attestation cannot be promoted from a self-report.
                correct_attestation = initial_attestation
                initial_attestation = runtime_profile._public_identity(managed.config.base)
                self.assertEqual(recovery.observe(managed,root,original,full)['state'],'UNKNOWN')
                initial_attestation = correct_attestation
                self.assertEqual((calls,sql_posts),effects_before_observation)
            observed = recovery.observe(managed,root,original,full)
            self.assertEqual(observed['state'],'AWAITING_RECOVERY_APPROVAL',recovery.classify(full))
            # RELEASE uses independent local GET proof, never a synthetic FD.
            def get(config,role,rid,aid=None):
                return ipc(role,'GET_ACTIVATION' if aid else 'GET_REQUEST',rid,aid)
            with patch.object(bridge,'owner_get',side_effect=get),patch.object(operator.Operator,'http',side_effect=lambda obj,request,**kw:http(request,**kw)):
                proof = recovery.observe_release(managed.config,'alice')
                if not parent_pin.get('base'):
                    saved_parent = parent_receipt.read_bytes()
                    parent_receipt.write_bytes(saved_parent+b' ')
                    with self.assertRaises((core.OperationError,ValueError)):
                        recovery.observe_release(managed.config,'alice')
                    with self.assertRaises(core.OperationError):
                        auth.release_principal(self.config.enrollment,self.executor,'alice','changed-parent',observation=proof)
                    parent_receipt.write_bytes(saved_parent)
                    with control_store.connect() as db:
                        self.assertEqual(db.execute("SELECT state FROM db_creation_principal_gates WHERE principal_id='alice'").fetchone()[0],'STOPPED')
                        self.assertNotEqual(db.execute('SELECT approval_state FROM db_creation_requests WHERE id=?',
                            (approval['request_id'],)).fetchone()[0],'VALID')
                with patch.object(bridge,'owner_get',return_value=dict(state='UNKNOWN',runtime_instance_id='unknown')):
                    with self.assertRaises(core.OperationError):
                        recovery.observe_release(managed.config,'alice')
                owner_path = Path(owners['workbench']['record_path'])
                owner_bytes = owner_path.read_bytes()
                operator.save_private(owner_path,dict(managed.config.owner('workbench'),owner_instance_id=str(uuid.uuid4())))
                with self.assertRaises(core.OperationError):
                    auth.release_principal(self.config.enrollment,self.executor,'alice','stale-owner',observation=proof)
                owner_path.write_bytes(owner_bytes)
                with control_store.connect() as db:
                    frozen_path = Path(db.execute('SELECT private_ref FROM db_creation_phase_plans WHERE id=?',(observed['plan_id'],)).fetchone()[0])
                frozen_bytes = frozen_path.read_bytes()
                frozen = json.loads(frozen_bytes)
                frozen['leaves']['activation/native/start'] = 'UNKNOWN'
                operator.save_private(frozen_path,frozen)
                with self.assertRaises(core.OperationError):
                    auth.release_principal(self.config.enrollment,self.executor,'alice','unclassified-proof',observation=proof)
                frozen_path.write_bytes(frozen_bytes)
                with self.assertRaises(core.OperationError):
                    auth.release_principal(self.config.enrollment,self.executor,'executor','wrong-target',observation=proof)
                with self.assertRaises(core.OperationError):
                    auth.release_principal(self.config.enrollment,self.executor,'alice','stale-proof',
                        observation=replace(proof,observed_at=proof.observed_at-31))
                with patch.object(bridge,'configure',side_effect=AssertionError('must not create Managed')),\
                        patch.object(creations,'install_foundation',side_effect=AssertionError('must not install Foundation')),\
                        patch.object(auth,'claim',side_effect=AssertionError('must not claim')):
                    recovery.release_local(str(enrollment_path),'alice','release-classified-stop-'+self.root_id)
            detail = creations.get(self.alice,'admin',self.root_id)
            recovery_plan = next(p for p in detail['plans'] if p['plan_id']==observed['plan_id'])
            request = creations.mutate(self.alice,'admin',self.root_id,'RECOVER_VERIFY',dict(expected_revision=detail['revision'],
                plan_id=recovery_plan['plan_id'],operation_id=recovery_plan['operation_id'],
                expected_operation_revision=recovery_plan['operation_revision'],delivery_digest=recovery_plan['delivery_digest'],
                recovery_digest=recovery_plan['recovery_digest']),'limited-recovery',phase='full')
            self.assertNotEqual(request['request_id'],approval['request_id'])
            original_journals = {p:p.read_bytes() for p in native_path.parent.glob('*.json') if p != native_path}
            result = recovery.execute(managed,request['request_id'])
            self.assertEqual(result['state'],'AWAITING_APPLY')
            self.assertEqual(runtime_profile.private_json(native_path)['descriptor'],native_descriptor)
            self.assertEqual(set(native_path.parent.glob('*.json')),{native_path} | set(original_journals))
            for path,saved_bytes in original_journals.items():
                self.assertEqual(path.read_bytes(),saved_bytes)
            detail = creations.get(self.alice,'admin',self.root_id)
            latest = next(p for p in detail['plans'] if p['purpose']=='APPLY' and p['phase']=='full')
            final = creations.mutate(self.alice,'admin',self.root_id,'APPROVE',dict(expected_revision=detail['revision'],
                plan_id=latest['plan_id'],operation_id=latest['operation_id'],expected_operation_revision=latest['operation_revision'],
                delivery_digest=latest['delivery_digest']),'fresh-final-full',phase='full')
            completed_posts = len(sql_posts)
            with patch.object(full,'observe_secrets',side_effect=lambda p:copy.deepcopy(current_observations)):
                result = worker.dispatch(managed,final['request_id'])
            self.assertEqual(len(sql_posts),completed_posts)
        self.assertEqual(result['state'],'APPLIED')
        self.assertEqual(calls,[(role,leaf) for role in ('workbench','native') for leaf in ('stop','start','readiness')])
        self.assertEqual(set(full.receipt['verification_complete']['proof']['runtime_readbacks']),{'workbench','native'})
        self.assertTrue(all(saved['complete'] for saved in full.receipt['sql'].values()))
        self.assertEqual(len(sql_posts),len(full.receipt['sql']))
        return candidate

    def test_two_applied_roots_use_canonical_material_without_enrollment_rewrite(self):
        import database_creation_bridge as bridge
        import database_creation_worker as worker
        import runtime_profile
        first = self.complete_managed_full()
        managed,full,*rest = first
        enrollment_bytes = Path(managed.config.path).read_bytes()
        enrolled_bundle = prep._tree(self.config.base_bundle)
        enrolled_private = self.config.base_private.read_bytes()
        selected = worker.applied_material(managed.config)
        first_private = runtime_profile.private_json(selected.private)
        first_bundle = generation.load_bundle(selected.bundle)
        first_root,first_target = self.root_id,self.new_id
        self.assertEqual(selected.bundle,Path(full.config['bundle']))
        # Historical proof is immutable; its parent is no longer the head.
        prep.Preparation._validate_seal(selected.bundle.parent,
            prep.Preparation._journal(selected.bundle.parent/'journal.json'),selected.pin['private_ref'])
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM databases WHERE id=?',(first_target,)).fetchone()[0],'READY')
        for sql,params in (
                ('UPDATE warehouse_controls SET applied_generation_id=NULL',()),
                ("UPDATE operations SET state='UNKNOWN' WHERE id=?",(selected.pin['operation_id'],)),
                ("UPDATE operations SET state='AWAITING_APPLY' WHERE id=?",(selected.pin['operation_id'],)),
                ("UPDATE warehouse_config_generations SET state='PLANNED' WHERE id=?",(selected.pin['generation_id'],)),
                ("UPDATE db_creations SET enrollment_id='foreign' WHERE id=?",(first_root,)),
                ("UPDATE db_creations SET environment_id='foreign' WHERE id=?",(first_root,)),
                ("UPDATE db_creations SET warehouse_id='foreign' WHERE id=?",(first_root,)),
                ("UPDATE db_creations SET enrollment_revision=2 WHERE id=?",(first_root,)),
                ("UPDATE operation_steps SET state='UNKNOWN' WHERE operation_id=? AND step='verify'",(selected.pin['operation_id'],)),
                ("UPDATE db_creations SET terminal_outcome=NULL,reservation_active=1 WHERE id=?",(first_root,))):
            with self.subTest(sql=sql),control_store.connect() as db:
                db.execute('SAVEPOINT invalid_head')
                db.execute(sql,params)
                with self.assertRaises(core.OperationError):
                    worker.applied_material(managed.config,db=db)
                db.execute('ROLLBACK TO invalid_head')
        for path in (selected.bundle.parent/'journal.json',selected.private):
            original_bytes = path.read_bytes()
            with self.subTest(path=path.name):
                path.unlink()
                with self.assertRaises((core.OperationError,FileNotFoundError)):
                    worker.applied_material(managed.config)
                path.write_bytes(original_bytes); path.chmod(0o600)
                if path.name == 'journal.json':
                    operator.save_private(path,dict(json.loads(original_bytes),state='STARTED'))
                else:
                    path.write_bytes(original_bytes+b' ')
                with self.assertRaises(core.OperationError):
                    worker.applied_material(managed.config)
                path.write_bytes(original_bytes)
        receipt_path = control_store.root()/'warehouse-operator-receipts'/(selected.pin['operation_id']+'.json')
        receipt_bytes = receipt_path.read_bytes()
        forged = json.loads(receipt_bytes)
        forged['verification_complete']['proof']['operation_id'] = 'foreign'
        forged['verification_complete']['evidence_ref'] = 'generation-verified:'+operator.digest(operator.canonical(forged['verification_complete']['proof']))
        operator.save_private(receipt_path,forged)
        with self.assertRaises(core.OperationError):
            worker.applied_material(managed.config)
        receipt_path.write_bytes(receipt_bytes)
        record_path = Path(managed.config.value['owners']['native']['record_path'])
        record = runtime_profile.private_json(record_path)
        operator.save_private(record_path,dict(record,runtime_instance_id=full.config['managed_handoff']['owners']['native']['old_runtime_instance_id']))
        with self.assertRaises(ValueError):
            worker.applied_material(managed.config)
        operator.save_private(record_path,record)
        self.ack = creations.create(self.alice,'admin',dict(sql_catalog='second'),'second-create')[0]
        self.root_id = self.ack['creation_id']
        with control_store.connect() as db:
            self.new_id = db.execute('SELECT database_id FROM db_creations WHERE id=?',(self.root_id,)).fetchone()[0]
        self.request = self.request_phase('bootstrap')
        # Loss immediately before registration reuses the original seal and keys.
        with patch.object(bridge,'current',return_value=managed),patch.object(creations,'_prepare_linked_plan',side_effect=ConnectionError):
            with self.assertRaises(ConnectionError):
                self.prepare()
        private_path = self.directory_for()/'private-input.json'
        prepared_bytes = private_path.read_bytes()
        before = len(self.commands)
        with patch.object(bridge,'current',return_value=managed):
            linked = self.prepare()
            self.assertEqual(self.prepare(),linked)
        self.assertEqual(len(self.commands),before)
        self.assertEqual(private_path.read_bytes(),prepared_bytes)
        second_candidate = self.managed_candidate(existing=first)
        second_full = second_candidate[1]
        previous_profile = runtime_profile.private_json(full.config['runtime_profile'])
        next_profile = runtime_profile.private_json(second_full.config['runtime_profile'])
        for key,value in previous_profile.items():
            if key not in ('trino_auth_file','catalog_auth_file'):
                self.assertEqual(next_profile[key],value)
        for role,owner in second_full.config['managed_handoff']['owners'].items():
            self.assertEqual(owner['old_runtime_instance_id'],full.config['managed_handoff']['owners'][role]['candidate_runtime_instance_id'])
        self.assertEqual(second_full.config['managed_transport']['endpoints'].items() & selected.endpoints.items(),selected.endpoints.items())
        second_private = runtime_profile.private_json(self.directory_for('full')/'private-input.json')
        second_bundle = second_full.bundle
        for ref,value in first_private['secrets'].items():
            old_auth = material.ref_identity(first_bundle['tofu']['shared']['trino_auth_secret'])
            if ref != old_auth:
                self.assertEqual(second_private['secrets'][ref],value)
        for user,password in first_private['passwords'].items():
            self.assertEqual(second_private['passwords'][user],password)
        old_auth = first_private['secrets'][old_auth]['data']
        new_auth = second_private['secrets'][material.ref_identity(second_bundle['tofu']['shared']['trino_auth_secret'])]['data']
        self.assertTrue(new_auth['password.db'].startswith(old_auth['password.db']))
        for key in ('tls.pem','shared-secret'):
            self.assertEqual(new_auth[key],old_auth[key])
        self.complete_managed_full(second_candidate)
        latest = worker.applied_material(managed.config)
        self.assertEqual(latest.bundle,Path(second_full.config['bundle']))
        with control_store.connect() as db:
            self.assertEqual([r[0] for r in db.execute('SELECT terminal_outcome FROM db_creations ORDER BY id')],['APPLIED','APPLIED'])
            for key in (first_target,self.new_id):
                self.assertEqual(db.execute('SELECT state FROM databases WHERE id=?',(key,)).fetchone()[0],'READY')
        self.assertEqual(Path(managed.config.path).read_bytes(),enrollment_bytes)
        self.assertEqual(prep._tree(self.config.base_bundle),enrolled_bundle)
        self.assertEqual(self.config.base_private.read_bytes(),enrolled_private)

    def approved_managed_candidate(self):
        import database_creation_bridge as bridge
        import socket
        managed,full,*rest = self.managed_candidate()
        parent,child = socket.socketpair()
        self.addCleanup(parent.close); self.addCleanup(child.close)
        hello = dict(managed.config.owner('workbench'),purpose='worker',
            private_enrollment_path=managed.config.path,profile_path=managed.config.value['base_runtime_profile'])
        managed = bridge.Managed(child,hello,managed.config)
        detail = creations.get(self.alice,'admin',self.root_id)
        plan = next(p for p in detail['plans'] if p['phase']=='full')
        approval = creations.mutate(self.alice,'admin',self.root_id,'APPROVE',dict(expected_revision=detail['revision'],
            plan_id=plan['plan_id'],operation_id=plan['operation_id'],expected_operation_revision=plan['operation_revision'],
            delivery_digest=plan['delivery_digest']),'managed-review-full',phase='full')
        return managed,full,parent,approval,plan

    def test_actual_executor_revocation_at_dispatch_precedes_profile_pin_validation(self):
        self.assert_startup_revocation('executor','dispatch')

    def test_old_managed_candidate_without_enrollment_byte_pin_requires_replan(self):
        managed,full,*rest = self.managed_candidate()
        value = json.loads(full.path.read_bytes())
        del value['preparation_inputs'][managed.config.path]
        operator.save_private(full.path,value)
        with self.assertRaisesRegex(ValueError,'Pinned enrollment bytes required'):
            operator.Operator(full.path)

    def test_actual_requester_revocation_at_resolver_precedes_profile_pin_validation(self):
        self.assert_startup_revocation('alice','resolve')

    def assert_startup_revocation(self,target,command):
        import database_creation_worker as worker
        managed,full,parent,approval,plan = self.approved_managed_candidate()
        original = self.account_file.read_bytes()
        self.accounts[target]['disabled'] = True
        self.save_accounts()
        with self.assertRaises(auth.AuthorityLost),patch.object(worker,'Operator',side_effect=AssertionError('auth must precede pin validation')):
            if command == 'dispatch':
                worker.dispatch(managed,approval['request_id'])
            else:
                worker.activation(managed,dict(version=1,request_id=approval['request_id'],
                    activation_id='0'*64,owner_role='workbench',owner_instance_id=managed.hello['owner_instance_id']))
        self.account_file.write_bytes(original)
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM db_creation_principal_gates WHERE principal_id=?',(target,)).fetchone()[0],'STOPPED')
            self.assertEqual(db.execute('SELECT approval_state FROM db_creation_requests WHERE id=?',(approval['request_id'],)).fetchone()[0],'REVOKED')
        with self.assertRaises(core.OperationError):
            auth.claim(self.config.enrollment,self.executor,approval['request_id'],owner_id=managed.hello['owner_instance_id'])
        self.assertEqual(operator.Operator(full.path).binding,full.binding)

    def test_managed_fd_closed_during_apply_blocks_next_effect_and_latches_stop(self):
        self.assert_managed_fence(close_fd=True)

    def test_nested_enrollment_same_revision_drift_blocks_next_effect_and_latches_stop(self):
        self.assert_managed_fence(close_fd=False)

    def test_startup_nested_enrollment_drift_stops_old_approval_even_after_restore(self):
        import database_creation_bridge as bridge
        import database_creation_worker as worker
        managed,full,parent,approval,plan = self.approved_managed_candidate()
        path = Path(managed.config.path)
        original = path.read_bytes()
        value = json.loads(original)
        value['foundation']['node_uids']['node'] = 'changed-at-same-revision'
        operator.save_private(path,value)
        managed.config = bridge.Configuration(path)
        with self.assertRaises(core.OperationError):
            worker.dispatch(managed,approval['request_id'])
        path.write_bytes(original)
        with control_store.connect() as db:
            self.assertEqual(db.execute("SELECT state FROM db_creation_principal_gates WHERE principal_id='executor'").fetchone()[0],'STOPPED')
            self.assertEqual(db.execute('SELECT approval_state FROM db_creation_requests WHERE id=?',(approval['request_id'],)).fetchone()[0],'REVOKED')

    def assert_managed_fence(self,*,close_fd):
        import database_creation_bridge as bridge
        managed,full,parent,approval,plan = self.approved_managed_candidate()
        permit = auth.claim(self.config.enrollment,self.executor,approval['request_id'],owner_id=managed.hello['owner_instance_id'])
        observations = json.loads(operator.private_file(self.directory_for('full')/'journal.json'))['observations']
        effects = []
        def execute(step,current):
            effects.append(step)
            if close_fd:
                parent.close()
            else:
                value = json.loads(Path(managed.config.path).read_bytes())
                value['foundation']['node_uids']['node'] = 'changed-same-revision'
                operator.save_private(Path(managed.config.path),value)
            return dict(evidence_ref='ack:'+step)
        with patch.object(creations,'_foundation',managed),patch.object(bridge,'_managed',managed),auth.delegated(permit):
            result = core.apply(plan['operation_id'],actor='alice',expected_revision=plan['operation_revision'],
                delivery_digest=plan['delivery_digest'],authorize=lambda a:True,
                observe_secrets=lambda p:copy.deepcopy(observations),execute_step=execute,verify=lambda p:self.fail('no verify after fence'))
        self.assertEqual(len(effects),1)
        self.assertEqual(result['state'],'UNKNOWN')
        with control_store.connect() as db:
            self.assertEqual(db.execute("SELECT state FROM db_creation_principal_gates WHERE principal_id='executor'").fetchone()[0],'STOPPED')

    def test_http_successor_startup_requires_exact_approved_owner_and_sealed_journal(self):
        import database_creation_bridge as bridge
        import database_creation_worker as worker
        import runtime_profile
        import socket
        import uuid
        managed,full,parent,approval,plan = self.approved_managed_candidate()
        claimed = auth.claim(self.config.enrollment,self.executor,approval['request_id'],owner_id=managed.hello['owner_instance_id'])
        request,root,original,ignored = worker.resolve(managed,approval['request_id'])
        worker.seal_activation_plans(managed,request,root,original,full)
        desc = worker.descriptor(request,root,original,full,'workbench')
        record = managed.config.owner('workbench')
        journal_path = Path(managed.config.value['owners']['workbench']['directory'])/'activations'/(desc['activation_id']+'.json')
        operator.save_private(journal_path,dict(descriptor=desc,state='UNKNOWN',stop=dict(state='ACK',intent_id=str(uuid.uuid4())),
            start=dict(state='INTENT',intent_id=str(uuid.uuid4())),readiness=dict(state='UNSENT')))
        record['runtime_instance_id'] = desc['candidate_runtime_instance_id']
        operator.save_private(Path(managed.config.value['owners']['workbench']['record_path']),record)
        hello = dict(record,purpose='http',private_enrollment_path=managed.config.path,profile_path=full.config['runtime_profile'])
        a,b = socket.socketpair()
        self.addCleanup(a.close); self.addCleanup(b.close)
        a.sendall(json.dumps(hello).encode()+b'\n')
        duplicate = os.dup
        with patch.dict(os.environ,SHIROKUMA_RUNTIME_PROFILE=full.config['runtime_profile'],SHIROKUMA_MANAGED_FD='3'),\
                patch.object(bridge.os,'dup',side_effect=lambda fd:duplicate(b.fileno())),\
                patch.object(bridge,'_managed',None),patch.object(creations,'install_foundation'):
            runtime_profile.configure_process()
            successor = bridge.configure()
            self.addCleanup(successor.channel.close)
            self.assertEqual(bridge.line(a)['runtime_attestation'],full.config['managed_handoff']['runtime_attestation'])
            successor.require()
            with self.assertRaises(ValueError):
                worker.validate_candidate_profile(managed.config,full.config['runtime_profile'],hello=dict(hello,runtime_instance_id=str(uuid.uuid4())))
            with self.assertRaises(ValueError):
                worker.validate_candidate_profile(managed.config,str(self.enrolled/'arbitrary-profile.json'),hello=hello)
        with self.assertRaises(ValueError):
            worker.accepted_head(managed.config)  # one new owner is not Foundation


class ReceiptSealTests(CreationFixture):
    def receipt(self):
        value = spec(2)
        manifest = generation.render(value)['manifest']
        prior = dict(id='canonical-bootstrap',actor='alice',phase='bootstrap',state='PREPARED',
            generation_id=manifest['generation_id'],delivery_digest=manifest['delivery_digest'],manifest=manifest)
        binding = dict(phase='bootstrap',predecessor_id=None,generation_id=prior['generation_id'],delivery_digest=prior['delivery_digest'])
        provisioning = {key:dict(leaves={role:dict(state='ACKNOWLEDGED') for role in ('trino','iceberg-bootstrap')},
            consumers={role:dict(client_id=key+role,client_secret='secret-'+key+role,principal_name=key+role,
                realm=entry['binding']['polaris_realm']) for role in ('trino','iceberg-bootstrap')})
            for key,entry in value['databases'].items()}
        proof = dict(operation_id=prior['id'],binding=binding,configuration=dict(generation_id=prior['generation_id']),
            provisioning_digest=operator.digest(operator.canonical(provisioning)))
        evidence = 'bootstrap-prepared:'+operator.digest(operator.canonical(proof))
        prior['steps'] = [dict(step='verify',state='SUCCEEDED',evidence_ref=evidence)]
        return dict(actor='alice',binding=binding,provisioning=provisioning,
            bootstrap_complete=dict(proof=proof,evidence_ref=evidence)),prior

    def test_sealed_receipt_and_legacy_exact_shape(self):
        receipt,prior = self.receipt()
        self.assertEqual(operator.validate_bootstrap_receipt(receipt,prior,require_seal=True),receipt)
        del receipt['bootstrap_complete']['proof']['provisioning_digest']
        evidence = 'bootstrap-prepared:'+operator.digest(operator.canonical(receipt['bootstrap_complete']['proof']))
        receipt['bootstrap_complete']['evidence_ref']=evidence
        prior['steps'][0]['evidence_ref']=evidence
        operator.validate_bootstrap_receipt(receipt,prior)
        with self.assertRaises(RuntimeError):
            operator.validate_bootstrap_receipt(receipt,prior,require_seal=True)

    def test_tamper_recomputed_digest_still_fails_canonical_ledger(self):
        for field in ('consumer','actor','operation','generation','delivery','unknown-leaf','extra-proof'):
            receipt,prior = self.receipt()
            proof = receipt['bootstrap_complete']['proof']
            if field=='consumer':
                receipt['provisioning']['opaque_0']['consumers']['trino']['client_secret']='forged'
                proof['provisioning_digest']=operator.digest(operator.canonical(receipt['provisioning']))
                receipt['bootstrap_complete']['evidence_ref']='bootstrap-prepared:'+operator.digest(operator.canonical(proof))
            elif field=='actor': receipt['actor']='bob'
            elif field=='operation': proof['operation_id']='wrong'
            elif field=='generation': receipt['binding']['generation_id']='wrong'
            elif field=='delivery': receipt['binding']['delivery_digest']='wrong'
            elif field=='unknown-leaf': receipt['provisioning']['opaque_0']['leaves']['trino']['state']='DISPATCHED'
            else: proof['extra']=True
            with self.subTest(field=field),self.assertRaises(RuntimeError):
                operator.validate_bootstrap_receipt(receipt,prior,require_seal=True)


class KnownPreflightFailureTests(PreparationFixture):
    def create(self, **kwargs):
        kwargs.setdefault('initial_grants',[dict(principal_id='bob',maximum_role='admin')])
        return super().create(**kwargs)

    def narrow_target(self):
        self.accounts['bob'].update(role='bi_viewer',platform_admin=False)
        self.save_accounts()
        grant_management.mutate(self.alice,'admin',self.new_id,
            dict(principal_id='bob',maximum_role='bi_viewer',expected_revision=1),'narrow-target')

    def claimed_request(self):
        with control_store.connect() as db:
            return dict(db.execute('SELECT * FROM db_creation_requests WHERE id=?',
                (self.request['request_id'],)).fetchone())

    def fail_preflight(self):
        self.narrow_target()
        with patch.object(generation,'render',wraps=generation.render) as render,\
                patch.object(prep,'incremental_input',wraps=prep.incremental_input) as generate:
            with self.assertRaisesRegex(core.OperationError,'UNAPPLIED_CHANGES'):
                self.prepare()
            render.assert_called_once_with(self.base['manifest']['spec'],ROOT)
            generate.assert_not_called()
        self.assertEqual(self.commands,[])
        self.assertFalse(self.directory_for().exists())
        request = self.claimed_request()
        self.assertEqual(request['dispatch_state'],'COMPLETE')
        self.assertEqual(request['stop_reason'],'UNAPPLIED_CHANGES')
        return request

    def replan(self, key='explicit-replan'):
        detail = creations.get(self.alice,'admin',self.root_id)
        return creations.mutate(self.alice,'admin',self.root_id,'REPLAN',
            dict(expected_revision=detail['revision'],phase='bootstrap'),key)

    def test_real_claim_failure_receipt_same_root_explicit_sequence_two(self):
        before = self.claimed_request()
        self.assertNotIn('replan',creations.get(self.alice,'admin',self.root_id)['next_actions'])
        request = self.fail_preflight()
        receipt_path = auth._preflight_receipt_path(request['id'])
        receipt = operator.private_file(receipt_path)
        detail = creations.get(self.alice,'admin',self.root_id)
        self.assertEqual(detail['progress_code'],'REPLAN_REQUIRED')
        self.assertEqual(detail['next_actions'],['withdraw','replan'])
        self.assertEqual(detail['plans'],[])
        fence = auth.PreparationFence(self.config.enrollment,self.executor,request['id'],
            request['owner_id'],request['claim_id'],request['gate_revision'],1,initial_claim=True)
        snapshot = self.snapshot()
        auth.complete_preflight_failure(fence,core.OperationError('UNAPPLIED_CHANGES'),self.directory_for())
        self.assertEqual(snapshot,self.snapshot())
        self.assertEqual(operator.private_file(receipt_path),receipt)
        self.assertEqual(creations.mutate(self.alice,'admin',self.root_id,'PREPARE_PLAN',
            json.loads(before['payload']),before['idempotency_key']),self.request)
        payload = dict(expected_revision=detail['revision'],phase='bootstrap')
        next_request = self.replan()
        self.assertEqual(creations.mutate(self.alice,'admin',self.root_id,'REPLAN',payload,'explicit-replan'),next_request)
        self.assertEqual(next_request['creation_id'],self.root_id)
        linked = self.prepare(next_request)
        with control_store.connect() as db:
            new = db.execute('SELECT * FROM db_creation_requests WHERE id=?',(next_request['request_id'],)).fetchone()
            plan = db.execute('SELECT * FROM db_creation_phase_plans WHERE id=?',(linked['plan_id'],)).fetchone()
            root = db.execute('SELECT * FROM db_creations WHERE id=?',(self.root_id,)).fetchone()
            self.assertEqual(new['requested_sequence'],2)
            self.assertEqual(plan['sequence'],2)
            self.assertIsNone(plan['approval_request_id'])
            self.assertIsNone(new['approval_state'])
            self.assertTrue(root['reservation_active'])
            self.assertIsNone(root['terminal_outcome'])
        old = self.claimed_request()
        for field in ('ack','idempotency_key','claim_id','owner_id','private_receipt_ref'):
            self.assertEqual(old[field],request[field])
        self.assertEqual(operator.private_file(receipt_path),receipt)
        with auth.transaction() as db,self.assertRaises(core.OperationError):
            auth.preparation_guard(db,fence)
        self.assertFalse(any('apply' in argv for argv in self.commands))

    def test_missing_tampered_or_mismatched_receipt_blocks_dto_and_mutation(self):
        request = self.fail_preflight()
        path = auth._preflight_receipt_path(request['id'])
        data = operator.private_file(path)
        values = [None,b'{}',data.replace(b'UNAPPLIED_CHANGES',b'STALE_PLAN')]
        for field in ('request_id','creation_id','claim_id','phase','sequence','accepted_revision','owner_id','executor_gate_revision'):
            value = json.loads(data)
            value[field] = 'mismatched'
            values.append(operator.canonical(value))
        for value in values:
            with self.subTest(value=value):
                path.unlink()
                if value is not None:
                    path.write_bytes(value);path.chmod(0o600)
                self.assertNotIn('replan',creations.get(self.alice,'admin',self.root_id)['next_actions'])
                with self.assertRaisesRegex(core.OperationError,'RECONCILIATION_REQUIRED'):
                    self.replan()
                if path.exists():path.unlink()
                path.write_bytes(data);path.chmod(0o600)

    def test_post_started_material_or_effect_refuses_terminal_classification(self):
        self.narrow_target()
        fence = auth.claim_preparation(self.config.enrollment,self.executor,self.request['request_id'],owner_id='canonical-owner')
        directory = self.directory_for()
        directory.parent.mkdir(parents=True,mode=0o700)
        for name in ('journal.json','private-input.json','material'):
            directory.mkdir(mode=0o700)
            (directory/name).write_text('preserved evidence')
            with self.assertRaisesRegex(core.OperationError,'RECONCILIATION_REQUIRED'):
                auth.complete_preflight_failure(fence,core.OperationError('UNAPPLIED_CHANGES'),directory)
            self.assertEqual((directory/name).read_text(),'preserved evidence')
            (directory/name).unlink();directory.rmdir()
        with control_store.connect() as db:
            db.execute("UPDATE db_creation_requests SET private_receipt_ref='preparation-start:existing' WHERE id=?",(fence.request_id,))
        with self.assertRaisesRegex(core.OperationError,'RECONCILIATION_REQUIRED'):
            auth.complete_preflight_failure(fence,core.OperationError('UNAPPLIED_CHANGES'),directory)
        self.assertEqual(self.claimed_request()['dispatch_state'],'CLAIMED')
        self.assertFalse(auth._preflight_receipt_path(fence.request_id).exists())
        for error in (core.OperationError('STALE_PLAN'),ValueError('UNAPPLIED_CHANGES')):
            with self.assertRaises(core.OperationError):auth.complete_preflight_failure(fence,error,directory)

    def test_stale_owner_claim_and_root_cannot_close_and_late_worker_is_fenced(self):
        self.narrow_target()
        fence = auth.claim_preparation(self.config.enrollment,self.executor,self.request['request_id'],owner_id='canonical-owner')
        for stale in (replace(fence,owner_id='other'),replace(fence,claim_id='other'),
                replace(fence,gate_revision=2),replace(fence,executor_gate_revision=2)):
            with self.assertRaises(core.OperationError):
                auth.complete_preflight_failure(stale,core.OperationError('UNAPPLIED_CHANGES'),self.directory_for())
        with control_store.connect() as db:
            db.execute('UPDATE db_creations SET revision=revision+1 WHERE id=?',(self.root_id,))
        with self.assertRaises(core.OperationError):
            auth.complete_preflight_failure(fence,core.OperationError('UNAPPLIED_CHANGES'),self.directory_for())
        self.assertFalse(auth._preflight_receipt_path(fence.request_id).exists())
        self.assertEqual(self.claimed_request()['dispatch_state'],'CLAIMED')
        with self.assertRaisesRegex(core.OperationError,'RECONCILIATION_REQUIRED'):self.replan()

    def test_linked_plan_and_changed_control_cannot_be_closed_as_preflight(self):
        linked = self.prepare()
        request = self.claimed_request()
        fence = auth.PreparationFence(self.config.enrollment,self.executor,request['id'],request['owner_id'],
            request['claim_id'],request['gate_revision'],1)
        with self.assertRaises(core.OperationError):
            auth.complete_preflight_failure(fence,core.OperationError('UNAPPLIED_CHANGES'),self.directory_for())
        self.assertFalse(auth._preflight_receipt_path(request['id']).exists())
        self.assertTrue(linked['plan_id'])

    def test_actual_account_file_revocation_stops_before_terminal_receipt(self):
        self.narrow_target()
        fence = auth.claim_preparation(self.config.enrollment,self.executor,self.request['request_id'],owner_id='canonical-owner')
        self.accounts['alice']['disabled'] = True
        self.save_accounts()
        with self.assertRaises(auth.AuthorityLost):
            auth.complete_preflight_failure(fence,core.OperationError('UNAPPLIED_CHANGES'),self.directory_for())
        with control_store.connect() as db:
            gate = db.execute("SELECT * FROM db_creation_principal_gates WHERE principal_id='alice'").fetchone()
            self.assertEqual(gate['state'],'STOPPED')
        self.assertFalse(auth._preflight_receipt_path(fence.request_id).exists())
        self.assertEqual(self.claimed_request()['dispatch_state'],'CLAIMED')

    def test_receipt_first_lost_db_commit_reuses_identical_evidence_only(self):
        from contextlib import contextmanager
        self.narrow_target()
        fence = auth.claim_preparation(self.config.enrollment,self.executor,self.request['request_id'],owner_id='canonical-owner')
        original = auth.transaction
        @contextmanager
        def rollback():
            with original() as db:
                yield db
                raise RuntimeError('simulated commit loss')
        with patch.object(auth,'transaction',rollback),self.assertRaisesRegex(RuntimeError,'commit loss'):
            auth.complete_preflight_failure(fence,core.OperationError('UNAPPLIED_CHANGES'),self.directory_for())
        path = auth._preflight_receipt_path(fence.request_id)
        data = operator.private_file(path)
        self.assertEqual(self.claimed_request()['dispatch_state'],'CLAIMED')
        with self.assertRaisesRegex(core.OperationError,'RECONCILIATION_REQUIRED'):self.replan()
        auth.complete_preflight_failure(fence,core.OperationError('UNAPPLIED_CHANGES'),self.directory_for())
        self.assertEqual(operator.private_file(path),data)
        self.assertEqual(self.claimed_request()['dispatch_state'],'COMPLETE')


    def test_historical_claim_reentry_cannot_create_new_terminal_attestation(self):
        self.narrow_target()
        first = auth.claim_preparation(self.config.enrollment,self.executor,self.request['request_id'],owner_id='canonical-owner')
        self.assertTrue(first.initial_claim)
        with self.assertRaisesRegex(core.OperationError,'UNAPPLIED_CHANGES'):
            self.prepare()
        self.assertEqual(self.claimed_request()['dispatch_state'],'CLAIMED')
        self.assertIsNone(self.claimed_request()['private_receipt_ref'])
        self.assertFalse(auth._preflight_receipt_path(first.request_id).exists())

    def test_terminal_proof_cannot_override_later_artifacts_or_effect_state(self):
        self.fail_preflight()
        directory = self.directory_for()
        directory.mkdir(parents=True,mode=0o700)
        (directory/'journal.json').write_text('STARTED')
        self.assertNotIn('replan',creations.get(self.alice,'admin',self.root_id)['next_actions'])
        with self.assertRaisesRegex(core.OperationError,'RECONCILIATION_REQUIRED'):self.replan()
        (directory/'journal.json').unlink();directory.rmdir()
        for sql,params,restore,restore_params in (
                ("UPDATE warehouse_controls SET state='UNKNOWN'",(),"UPDATE warehouse_controls SET state='OPEN'",()),
                ("UPDATE databases SET state='PREPARING' WHERE id=?",(self.new_id,),
                    "UPDATE databases SET state='REGISTERED' WHERE id=?",(self.new_id,)),
                ('UPDATE catalog_bindings SET applied_revision=1 WHERE database_id=?',(self.new_id,),
                    'UPDATE catalog_bindings SET applied_revision=0 WHERE database_id=?',(self.new_id,))):
            with self.subTest(sql=sql):
                with control_store.connect() as db:db.execute(sql,params)
                self.assertNotIn('replan',creations.get(self.alice,'admin',self.root_id)['next_actions'])
                with self.assertRaisesRegex(core.OperationError,'RECONCILIATION_REQUIRED'):self.replan()
                with control_store.connect() as db:db.execute(restore,restore_params)

    def test_mismatched_or_partial_preexisting_receipt_is_never_overwritten(self):
        self.narrow_target()
        fence = auth.claim_preparation(self.config.enrollment,self.executor,self.request['request_id'],owner_id='canonical-owner')
        path = auth._preflight_receipt_path(fence.request_id)
        operator.save_private(path,dict(partial='preserve'))
        before = operator.private_file(path)
        with self.assertRaisesRegex(core.OperationError,'RECONCILIATION_REQUIRED'):
            auth.complete_preflight_failure(fence,core.OperationError('UNAPPLIED_CHANGES'),self.directory_for())
        self.assertEqual(operator.private_file(path),before)
        self.assertEqual(self.claimed_request()['dispatch_state'],'CLAIMED')


class DesiredGrantCancellationTests(PreparationFixture):
    complete_bootstrap = PreparationTests.complete_bootstrap
    full_approval = GrantReplanTests.full_approval

    def create(self, **kwargs):
        # Establish the enrollment baseline through the real full core before intake.
        bundle = self.base
        refs = bundle['tofu']['bootstrap_secret_refs'] + bundle['tofu']['deferred_secret_refs']
        observed = [dict(r,exists=True,uid='base-'+r['name'],resource_version='1',immutable=True) for r in refs]
        plan = core.create_plan('alice','/warehouse/configuration','verified-baseline',bundle,observed)
        applied = core.apply(plan['id'],actor='alice',expected_revision=plan['revision'],delivery_digest=plan['delivery_digest'],
            authorize=lambda actor:True,observe_secrets=lambda p:copy.deepcopy(observed),
            execute_step=lambda s,p:dict(evidence_ref='baseline:'+s),
            verify=lambda p:dict(verified=True,evidence_ref='baseline:verified'))
        self.assertEqual(applied['state'],'APPLIED')
        return super().create(**kwargs)

    def directory_for(self, phase='bootstrap'):
        sequence = '1'
        if phase == 'bootstrap':
            with control_store.connect() as db:
                sequence = str(db.execute('SELECT requested_sequence FROM db_creation_requests WHERE id=?',
                    (self.request['request_id'],)).fetchone()[0])
        return self.config.output_root/self.root_id/phase/sequence

    def change_unrelated(self, role, revision, key):
        return grant_management.mutate(self.alice,'admin','opaque_1',
            dict(principal_id='bob',maximum_role=role,expected_revision=revision),key)

    def test_failed_same_root_cancel_replan_full_then_next_create(self):
        pending = self.change_unrelated('bi_viewer',0,'unrelated-new')
        before = dict(self.request)
        with self.assertRaisesRegex(core.OperationError,'UNAPPLIED_CHANGES'):
            self.prepare()
        self.assertEqual(self.commands,[])
        with control_store.connect() as db:
            failed = dict(db.execute('SELECT * FROM db_creation_requests WHERE id=?',(before['request_id'],)).fetchone())
        self.assertEqual(failed['dispatch_state'],'COMPLETE')
        receipt_path = auth._preflight_receipt_path(failed['id'])
        receipt = operator.private_file(receipt_path)
        canceled = self.change_unrelated('revoked',1,'unrelated-cancel')
        self.assertEqual(canceled['state'],'CANCELED')
        self.assertEqual(self.change_unrelated('revoked',1,'unrelated-cancel'),canceled)
        public = catalog_admin.export_spec('candidate',self.base['manifest']['spec'])
        self.assertEqual([g for g in public['grant_revisions'] if g['database_id'] != self.new_id],
            self.base['manifest']['spec']['grant_revisions'])
        with control_store.connect() as db:
            held = grant_management.effective(db,'bob','opaque_1')
            audit = [tuple(r) for r in db.execute("SELECT * FROM grant_audit WHERE principal_id='bob' AND database_id='opaque_1'")]
            self.assertEqual(grant_management.inspect(db,pending['id'],'alice')['state'],'SUPERSEDED')
            root = dict(db.execute('SELECT * FROM db_creations WHERE id=?',(self.root_id,)).fetchone())
            prep.target_only(db,self.base['manifest']['spec'],public,root,'bootstrap')
        detail = creations.get(self.alice,'admin',self.root_id)
        self.request = creations.mutate(self.alice,'admin',self.root_id,'REPLAN',
            dict(expected_revision=detail['revision'],phase='bootstrap'),'explicit-cancel-replan')
        bootstrap,_ = self.complete_bootstrap()
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT sequence FROM db_creation_phase_plans WHERE operation_id=?',(bootstrap['id'],)).fetchone()[0],2)
            self.assertEqual(dict(db.execute('SELECT * FROM db_creation_requests WHERE id=?',(failed['id'],)).fetchone()),failed)
        self.assertEqual(operator.private_file(receipt_path),receipt)
        full_request = self.request_phase('full')
        linked = self.prepare(full_request)
        approval,_ = self.full_approval(linked,'approve-after-cancel')
        permit = auth.claim(self.config.enrollment,self.executor,approval['request_id'],owner_id='canonical-owner')
        observed = json.loads(operator.private_file(self.directory_for('full')/'journal.json'))['observations']
        def execute(step,current):
            if step == 'apply_secrets':
                for row in observed:
                    row.update(exists=True,uid='full-'+row['name'],resource_version='1',immutable=True)
            return dict(evidence_ref='full:'+step)
        plan = core.inspect(linked['operation_id'],actor='alice')
        with auth.delegated(permit):
            applied = core.apply(plan['id'],actor='alice',expected_revision=plan['revision'],delivery_digest=plan['delivery_digest'],
                authorize=lambda actor:True,observe_secrets=lambda p:copy.deepcopy(observed),execute_step=execute,
                verify=lambda p:dict(verified=True,evidence_ref='full:verified'))
        self.assertEqual(applied['state'],'APPLIED')
        public = catalog_admin.export_spec('after-target-full',applied['manifest']['spec'])
        self.assertFalse(any(g['principal_id']=='bob' and g['database_id']=='opaque_1' for g in public['grant_revisions']))
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM databases WHERE id=?',(self.new_id,)).fetchone()[0],'READY')
            self.assertEqual(grant_management.effective(db,'bob','opaque_1'),held)
            self.assertEqual([tuple(r) for r in db.execute("SELECT * FROM grant_audit WHERE principal_id='bob' AND database_id='opaque_1'")],audit)
            self.assertEqual(dict(db.execute('SELECT * FROM db_creation_requests WHERE id=?',(failed['id'],)).fetchone()),failed)
            db.execute("UPDATE operation_steps SET evidence_ref='' WHERE operation_id=? AND step='verify'",(bootstrap['id'],))
        # A historical bootstrap is unrelated to full verified authority.
        self.change_unrelated('bi_viewer',2,'extra-active')
        with self.assertRaisesRegex(core.OperationError,'UNAPPLIED_CHANGES'):
            creations.create(self.alice,'admin',dict(sql_catalog='nextdatabase'),'next-create')
        self.assertEqual(self.change_unrelated('revoked',3,'cancel-extra')['state'],'CANCELED')
        with control_store.connect() as db:
            db.execute("UPDATE operation_steps SET evidence_ref='' WHERE operation_id=? AND step='verify'",(applied['id'],))
        with self.assertRaisesRegex(core.OperationError,'UNAPPLIED_CHANGES'):
            creations.create(self.alice,'admin',dict(sql_catalog='nextdatabase'),'next-create')
        with control_store.connect() as db:
            db.execute("UPDATE operation_steps SET evidence_ref='full:verified' WHERE operation_id=? AND step='verify'",(applied['id'],))
            second_held = grant_management.effective(db,'bob','opaque_1')
        ack, created = creations.create(self.alice,'admin',dict(sql_catalog='nextdatabase'),'next-create')
        self.assertTrue(created)
        self.assertNotEqual(ack['creation_id'],self.root_id)
        with control_store.connect() as db:
            self.assertEqual(grant_management.effective(db,'bob','opaque_1'),second_held)
        self.assertEqual(operator.private_file(receipt_path),receipt)

    def test_cancel_then_regrant_during_final_preparation_cas_is_stale(self):
        self.change_unrelated('bi_viewer',0,'pending-cas')
        self.assertEqual(self.change_unrelated('revoked',1,'cancel-cas')['state'],'CANCELED')
        original = creations._prepare_linked_plan
        def regrant(*args,**kwargs):
            self.change_unrelated('bi_viewer',2,'regrant-at-final-cas')
            return original(*args,**kwargs)
        with patch.object(creations,'_prepare_linked_plan',side_effect=regrant):
            with self.assertRaisesRegex(core.OperationError,'STALE_REGISTRY'):
                self.prepare()
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM db_creation_phase_plans').fetchone()[0],0)
            self.assertEqual(grant_management.effective(db,'bob','opaque_1')['maximum_role'],'revoked')
            self.assertTrue(any(g['principal_id']=='bob' for g in grant_management.desired_projection(db)))
