import importlib.util
import io
import json
import copy
import http.client
import http.server
import threading
import subprocess
import sys
import tempfile
from contextlib import nullcontext
from pathlib import Path
import unittest
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))

SPEC = importlib.util.spec_from_file_location(
    "warehouse_resident", Path(__file__).resolve().parents[1] / "tools/local_experiment/warehouse_resident.py")
w = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(w)


class LogicalSharedWarehouseTests(unittest.TestCase):
    def setUp(self):
        import control_store
        import logical_foundation
        from test_logical_foundation import settings
        from types import SimpleNamespace
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg = dict(environment_id='lab', warehouse_id='LOCAL', foundation_generation_id='wcg-owned',
                        foundation_revision=3, provider_config_file='/tmp/synthetic-foundation.json')
        self.foundation = logical_foundation.validate(settings(self.temp.name))
        self.foundation['warehouse_id'] = 'LOCAL'
        self.foundation['trino']['workers'] = 1
        self.proof_path = str(Path(self.temp.name)/'verification.json')
        self.expected = dict(ready=True, foundation_id='wcg-owned', revision=3,
            incarnation=dict(namespace='namespace-uid', node=dict(self.foundation['kube']['node_uids'])),
            spi_sha256='a'*64, readiness={key:True for key in
                ('opa','storage','polaris','trino','runtime_files','spi')},
            hashes={'target.'+name:'b'*64 for name in
                ('credentials.properties','password.db','rules.json','guard.rego')})
        for kind,names in [('namespace',('safe-logical',)), ('configmap',('owned-secrets-code',)),
                ('service',('owned-seaweed','owned-polaris','trino-local')),
                ('deployment',('trino-local','trino-local-worker','trino-opa','owned-postgres','owned-seaweed','owned-polaris')),
                ('pvc',('trino-runtime','trino-catalogs','owned-seaweed-data','owned-postgres-data'))]:
            for name in names:
                self.expected['incarnation'][kind+'/'+name] = kind+'-'+name+'-uid'
                if kind == 'pvc':
                    self.expected['incarnation'][name] = kind+'-'+name+'-uid'
                    self.expected['incarnation']['pv/'+name+'-pv'] = 'pv-'+name+'-uid'
        self.saved = dict(kind='foundation_probe',generation_id='wcg-owned',revision=3,result=self.expected)
        logical_foundation.save_private(self.proof_path,json.dumps(self.saved))
        from logical_provider import Provider as ActualProvider
        self.actual_provider_type = ActualProvider
        self.bound_provider = Mock()
        self.provider_context = MagicMock()
        self.provider_context.__enter__.return_value = self.bound_provider
        provider_patch = patch('logical_provider.Provider', return_value=self.provider_context)
        self.provider_factory = provider_patch.start()
        self.addCleanup(provider_patch.stop)
        for mock in (patch.dict(w.os.environ, SHIROKUMA_STATE_DIR=self.temp.name),
                     patch('runtime_profile.current', return_value=SimpleNamespace(mode='logical_v1')),
                     patch('runtime_profile.logical_config', side_effect=lambda: dict(self.cfg)),
                     patch('logical_foundation.load', side_effect=lambda _: self.foundation),
                     patch.object(w, 'telemetry', return_value='skipped'),
                     patch.object(w, 'touch_activity')):
            mock.start()
            self.addCleanup(mock.stop)
        with control_store.connect() as db:
            db.execute("INSERT INTO warehouse_config_generations VALUES (?,?,?,'[]','[]',?,'APPLIED')",
                ('wcg-owned','LOCAL','foundation:wcg-owned',json.dumps([self.cfg['provider_config_file'],self.proof_path])))
            db.execute("INSERT INTO logical_foundations VALUES ('lab','LOCAL','wcg-owned',3,'READY',?,?)",
                (self.cfg['provider_config_file'],self.proof_path))
            db.execute("INSERT INTO warehouse_controls VALUES ('LOCAL','OPEN',NULL,1,'wcg-owned')")

    def objects(self, coordinator=1, workers=1):
        import logical_foundation
        return dict(items=[dict(kind='Deployment', metadata=dict(name=name, namespace='safe-logical',
            uid=name+'-uid', generation=7, labels={logical_foundation.LABEL:'wcg-owned',logical_foundation.REVISION:'3'}),
            spec=dict(replicas=count), status=dict(replicas=count,readyReplicas=count,updatedReplicas=count,
                availableReplicas=count,observedGeneration=7))
            for name,count in [('trino-local',coordinator),('trino-local-worker',workers)]])

    def test_single_batch_sealed_target_and_public_states(self):
        import logical_foundation
        for counts, expected in [((1,1),'active'),((0,0),'chilled'),((1,0),'starting'),((0,1),'starting')]:
            with self.subTest(counts=counts), patch.object(w.subprocess,'run',return_value=Mock(stdout=json.dumps(self.objects(*counts)))) as run:
                result = w.status('LOCAL')
                self.assertEqual(result['state'],expected)
                self.assertEqual(run.call_args.args[0],logical_foundation.kube_argv(self.foundation)+
                    ['get','deployment/trino-local','deployment/trino-local-worker','-o','json'])
                run.assert_called_once()
                self.assertNotIn(self.temp.name,json.dumps(result))
                self.assertEqual(set(result),{'warehouse','backend','deployment','namespace','state','replicas','ready_replicas'})

    def test_partial_rollout_stale_generation_and_stopping_workers(self):
        for field in ('replicas','readyReplicas','updatedReplicas','availableReplicas','observedGeneration'):
            for index in (0,1):
                objects = self.objects()
                objects['items'][index]['status'][field] = 0
                with self.subTest(field=field,index=index), patch.object(w.subprocess,'run',return_value=Mock(stdout=json.dumps(objects))):
                    self.assertEqual(w.status('LOCAL')['state'],'starting')
        for field in ('replicas','readyReplicas','updatedReplicas','availableReplicas'):
            objects = self.objects(0,0)
            objects['items'][1]['status'][field] = 1
            with self.subTest(stopping=field), patch.object(w.subprocess,'run',return_value=Mock(stdout=json.dumps(objects))):
                self.assertEqual(w.status('LOCAL')['state'],'starting')
        objects = self.objects(0,0)
        objects['items'][1]['status']['observedGeneration'] = 6
        with patch.object(w.subprocess,'run',return_value=Mock(stdout=json.dumps(objects))):
            self.assertEqual(w.status('LOCAL')['state'],'starting')

    def test_zero_worker_active_requires_fully_stopped_worker(self):
        self.foundation['trino']['workers'] = 0
        for worker,expected in ((0,'active'),(1,'starting')):
            with patch.object(w.subprocess,'run',return_value=Mock(stdout=json.dumps(self.objects(1,worker)))):
                self.assertEqual(w.status('LOCAL')['state'],expected)

    def test_foreign_missing_and_malformed_metadata_fail_safely(self):
        from warehouse_operations import OperationError
        for key,value in (('namespace','foreign'),('uid',''),('generation',True),('deletionTimestamp','now')):
            objects = self.objects();objects['items'][1]['metadata'][key] = value
            with self.subTest(key=key), patch.object(w.subprocess,'run',return_value=Mock(stdout=json.dumps(objects))):
                with self.assertRaises(OperationError) as error:
                    w.status('LOCAL')
                self.assertEqual(error.exception.code,'WAREHOUSE_UNAVAILABLE')
        import logical_foundation
        for objects in (dict(items=self.objects()['items'][:1]),dict(items=self.objects()['items'][:1]*2)):
            with patch.object(w.subprocess,'run',return_value=Mock(stdout=json.dumps(objects))), self.assertRaises(OperationError):
                w.status('LOCAL')
        for key,value in ((logical_foundation.LABEL,'foreign'),(logical_foundation.REVISION,'2')):
            objects = self.objects();objects['items'][0]['metadata']['labels'][key] = value
            with patch.object(w.subprocess,'run',return_value=Mock(stdout=json.dumps(objects))), self.assertRaises(OperationError):
                w.transition('chill','LOCAL')

    def test_config_and_committed_provenance_mismatches_issue_no_commands(self):
        import control_store
        from warehouse_operations import OperationError
        for key,value in (('environment_id','other'),('warehouse_id','other'),('foundation_generation_id','other'),
                          ('foundation_revision',4),('provider_config_file','/tmp/other')):
            previous = self.cfg[key];self.cfg[key] = value
            with self.subTest(config=key), patch.object(w.subprocess,'run') as run:
                for action in ('chill','wake'):
                    with self.assertRaises(OperationError):
                        w.transition(action,'LOCAL')
                run.assert_not_called()
            self.cfg[key] = previous
        mutations = [
            ("UPDATE warehouse_config_generations SET state='PLANNED'", "UPDATE warehouse_config_generations SET state='APPLIED'"),
            ("UPDATE warehouse_config_generations SET warehouse_id='other'", "UPDATE warehouse_config_generations SET warehouse_id='LOCAL'"),
            ("UPDATE warehouse_config_generations SET apply_id='foreign'", "UPDATE warehouse_config_generations SET apply_id='foundation:wcg-owned'"),
            ("UPDATE warehouse_config_generations SET configuration_refs='[]'", None),
            ("UPDATE logical_foundations SET revision=4", "UPDATE logical_foundations SET revision=3"),
            ("UPDATE logical_foundations SET status='MAINTENANCE'", "UPDATE logical_foundations SET status='READY'"),
            ("UPDATE logical_foundations SET config_ref='/tmp/other'", None),
            ("UPDATE warehouse_controls SET applied_generation_id=NULL", "UPDATE warehouse_controls SET applied_generation_id='wcg-owned'")]
        for sql, restore in mutations:
            with control_store.connect() as db:
                db.execute(sql)
            with self.subTest(sql=sql), patch.object(w.subprocess,'run') as run:
                for action in ('chill','wake'):
                    with self.assertRaises(OperationError):
                        w.transition(action,'LOCAL')
                with self.assertRaises(OperationError):
                    w.status('LOCAL')
                run.assert_not_called()
            with control_store.connect() as db:
                if restore:
                    db.execute(restore)
                elif 'configuration_refs' in sql:
                    db.execute('UPDATE warehouse_config_generations SET configuration_refs=?',
                        (json.dumps([self.cfg['provider_config_file'],self.proof_path]),))
                else:
                    db.execute('UPDATE logical_foundations SET config_ref=?',(self.cfg['provider_config_file'],))

    def test_transitions_restore_exact_replicas_observe_both_and_preserve_registry(self):
        import control_store
        for workers in (0,1):
            self.foundation['trino']['workers'] = workers
            for action,initial,target in [('chill',(1,workers),(0,0)),('wake',(0,0),(1,workers))]:
                observations = [self.objects(*initial),self.objects(0,1),self.objects(*target)]
                def command(argv,**kwargs):
                    import sqlite3
                    self.assertEqual(w._WAREHOUSE_GATE.mode,'exclusive' if action=='chill' else 'shared')
                    # Even a second writer must remain blocked throughout scale and observation.
                    with control_store.connect() as other:
                        other.execute('PRAGMA busy_timeout=0')
                        with self.assertRaises(sqlite3.OperationalError):
                            other.execute('BEGIN IMMEDIATE')
                    if 'get' in argv:
                        return Mock(stdout=json.dumps(observations.pop(0)))
                    self.assertIn('scale',argv)
                    return Mock(stdout='')
                with control_store.connect() as db:
                    before = list(db.iterdump())
                with self.subTest(workers=workers,action=action), patch.object(w.subprocess,'run',side_effect=command) as run, patch.object(w.time,'sleep'):
                    report = w.transition(action,'LOCAL')
                    self.assertEqual(report['state'],'chilled' if action=='chill' else 'active')
                    self.assertEqual(report['result'],'verified')
                    commands = [call.args[0] for call in run.call_args_list if 'scale' in call.args[0]]
                    self.assertEqual([cmd[-2:] for cmd in commands],[[f'deployment/{name}',f'--replicas={count}']
                        for name,count in zip(('trino-local','trino-local-worker'),target)])
                    self.assertEqual(observations,[])
                with control_store.connect() as db:
                    self.assertEqual(list(db.iterdump()),before)

    def test_noop_requires_both_deployments_at_the_observed_target(self):
        for workers in (0,1):
            self.foundation['trino']['workers'] = workers
            for action,counts in [('wake',(1,workers)),('chill',(0,0))]:
                with self.subTest(workers=workers,action=action), patch.object(w.subprocess,'run',return_value=Mock(stdout=json.dumps(self.objects(*counts)))) as run:
                    self.assertEqual(w.transition(action,'LOCAL')['result'],'noop')
                    run.assert_called_once()
                    self.assertIn('get',run.call_args.args[0])

    def test_configured_warehouse_admission_guard_uses_its_own_control(self):
        import control_store
        import warehouse_admission as admission
        self.cfg['warehouse_id'] = 'shared-compute'
        self.foundation['warehouse_id'] = 'shared-compute'
        with control_store.connect() as db:
            db.execute("UPDATE warehouse_config_generations SET warehouse_id='shared-compute'")
            db.execute("UPDATE logical_foundations SET warehouse_id='shared-compute'")
            db.execute("UPDATE warehouse_controls SET warehouse_id='shared-compute'")
            db.execute("INSERT INTO warehouse_controls VALUES ('LOCAL','OPEN',NULL,1,NULL)")
            handle = admission.admit(db,warehouse_id='shared-compute',actor='test',kind='operation',subject_id='accepted')
        for state in ('ACTIVE','UNKNOWN'):
            if state == 'UNKNOWN':
                with control_store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    admission.finish(db,handle,state=state)
            with self.subTest(state=state), patch.object(w.subprocess,'run') as run:
                for action in ('chill','wake'):
                    with self.subTest(action=action), self.assertRaises(admission.AdmissionConflict):
                        w.transition(action,'LOCAL')
                run.assert_not_called()

    def test_accepted_work_and_stop_guards_precede_commands(self):
        import control_store
        import warehouse_admission as admission
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            handle = admission.admit(db,actor='test',kind='operation',subject_id='accepted')
        with patch.object(w.subprocess,'run') as run:
            for action in ('chill','wake'):
                with self.assertRaises(admission.AdmissionConflict):
                    w.transition(action,'LOCAL')
            run.assert_not_called()
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE');admission.finish(db,handle,state='FINISHED')
            db.execute("UPDATE warehouse_controls SET state='DRAINING'")
        with patch.object(w.subprocess,'run') as run, self.assertRaises(admission.AdmissionDenied):
            w.transition('chill','LOCAL')
        run.assert_not_called()


    def fake_readiness_clock(self, timeout=1):
        self.elapsed = 0.0
        def sleep(seconds):
            self.elapsed += seconds
        for patcher in (patch.object(w, 'TIMEOUT', timeout),
                patch.object(w.time, 'monotonic', side_effect=lambda: self.elapsed),
                patch.object(w.time, 'sleep', side_effect=sleep)):
            mocked = patcher.start()
            self.addCleanup(patcher.stop)
        return mocked

    def test_wake_waits_for_binding_after_deployment_ready_without_repeating_scales(self):
        from logical_provider import Rejected
        self.fake_readiness_clock()
        events = []
        foundation_guard = w._logical_foundation
        def guard(db=None):
            events.append('guard')
            return foundation_guard(db)
        attempts = 0
        def bind(expected):
            nonlocal attempts
            self.assertEqual(expected, self.expected)
            self.assertEqual(events[-1], 'guard')
            self.assertEqual(w._WAREHOUSE_GATE.mode, 'shared')
            attempts += 1
            events.append('unready' if attempts == 1 else 'bound')
            if attempts == 1:
                raise Rejected('FOUNDATION_BINDING_INVALID')
        self.bound_provider.bind_registered.side_effect = bind
        self.provider_context.__exit__.side_effect = lambda *args: events.append('closed')
        observations = iter((self.objects(0,0), self.objects()))
        def command(argv, **kwargs):
            self.assertIn('receipt',events)
            return Mock(stdout=json.dumps(next(observations)) if 'get' in argv else '')
        import runtime_profile
        private_json = runtime_profile.private_json
        def receipt(path):
            events.append('receipt')
            return private_json(path)
        with patch.object(w.subprocess,'run',side_effect=command) as run, \
                patch.object(w,'_logical_foundation',side_effect=guard), \
                patch.object(runtime_profile,'private_json',side_effect=receipt) as saved, \
                patch.object(w,'touch_activity',side_effect=lambda: events.append('activity')):
            result = w.transition('wake','LOCAL')
            events.append('returned')
        self.assertEqual(result['result'],'verified')
        self.assertEqual(attempts,2)
        self.assertEqual(sum('scale' in call.args[0] for call in run.call_args_list),2)
        self.assertEqual(saved.call_count,1)
        self.assertEqual(events[-4:],['bound','closed','activity','returned'])
        self.provider_factory.assert_called_once_with(self.foundation)
        self.provider_context.__enter__.assert_called_once_with()
        self.provider_context.__exit__.assert_called_once()

    def test_noop_wake_also_waits_for_engine_without_scaling(self):
        from logical_provider import Rejected
        self.fake_readiness_clock()
        self.bound_provider.bind_registered.side_effect = [Rejected('FOUNDATION_BINDING_INVALID'),None]
        with patch.object(w.subprocess,'run',return_value=Mock(stdout=json.dumps(self.objects()))) as run:
            report = w.transition('wake','LOCAL')
        self.assertEqual(report['result'],'noop')
        run.assert_called_once()
        self.assertEqual(self.bound_provider.bind_registered.call_count,2)
        self.provider_context.__exit__.assert_called_once()
        w.touch_activity.assert_called_once_with()

    def test_permanent_binding_trust_and_uid_failures_propagate_without_wait_retry(self):
        from logical_provider import Rejected
        sleep = self.fake_readiness_clock()
        for code in ('FOUNDATION_BINDING_MISMATCH','FOUNDATION_TRUST_MISMATCH',
                'FOUNDATION_INCARNATION_CHANGED','FOUNDATION_SERVICE_TARGET_MISMATCH'):
            self.bound_provider.bind_registered.reset_mock()
            self.provider_context.__exit__.reset_mock()
            self.bound_provider.bind_registered.side_effect = Rejected(code)
            with self.subTest(code=code), \
                    patch.object(w.subprocess,'run',return_value=Mock(stdout=json.dumps(self.objects()))):
                with self.assertRaises(Rejected) as caught:
                    w.transition('wake','LOCAL')
                self.assertEqual(caught.exception.code,code)
                self.bound_provider.bind_registered.assert_called_once_with(self.expected)
                self.provider_context.__exit__.assert_called_once()
        sleep.assert_not_called()
        w.touch_activity.assert_not_called()

    def test_invalid_saved_proof_rejected_once_before_provider_work(self):
        from warehouse_operations import OperationError
        cases = [None, [], {}, {**self.saved,'kind':'other'}, {**self.saved,'generation_id':'foreign'},
            {**self.saved,'revision':True}, {**self.saved,'revision':4}]
        for field, values in {'ready':[False,1], 'foundation_id':['foreign'], 'revision':[True,4],
                'spi_sha256':[None,'A'*64,'a'*63], 'incarnation':[None,{}, {'node':{'lab-node':'foreign'}}],
                'readiness':[{},None], 'hashes':[{},None]}.items():
            for value in values:
                expected = {**self.expected,field:value}
                cases.append({**self.saved,'result':expected})
        for missing in ('ready','incarnation','spi_sha256'):
            expected = dict(self.expected)
            del expected[missing]
            cases.append({**self.saved,'result':expected})
        for missing in self.expected['incarnation']:
            expected = copy.deepcopy(self.expected)
            del expected['incarnation'][missing]
            cases.append({**self.saved,'result':expected})
        for saved in cases:
            Path(self.proof_path).write_text(json.dumps(saved))
            for counts in ((0,0),(1,1)):
                with self.subTest(saved=saved,counts=counts), \
                        patch.object(w.subprocess,'run',return_value=Mock(stdout=json.dumps(self.objects(*counts)))) as run:
                    with self.assertRaises(OperationError) as caught:
                        w.transition('wake','LOCAL')
                    self.assertEqual(caught.exception.code,'FOUNDATION_NOT_READY')
                    run.assert_not_called()  # No status, scale or PF work, including from chilled.
        for content in ('not json',None):
            if content is None:
                Path(self.proof_path).unlink()
            else:
                Path(self.proof_path).write_text(content)
            for counts in ((0,0),(1,1)):
                with patch.object(w.subprocess,'run',return_value=Mock(stdout=json.dumps(self.objects(*counts)))) as run:
                    with self.assertRaises(OperationError):
                        w.transition('wake','LOCAL')
                    run.assert_not_called()
        self.provider_factory.assert_not_called()
        w.touch_activity.assert_not_called()

    def test_actual_unready_times_out_closes_lifetime_without_activity(self):
        from logical_provider import Rejected
        self.fake_readiness_clock(timeout=0.5)
        self.bound_provider.bind_registered.side_effect = Rejected('FOUNDATION_BINDING_INVALID')
        with patch.object(w.subprocess,'run',return_value=Mock(stdout=json.dumps(self.objects()))), \
                self.assertRaises(TimeoutError):
            w.transition('wake','LOCAL')
        self.assertEqual(self.bound_provider.bind_registered.call_count,3)
        self.provider_context.__exit__.assert_called_once()
        w.touch_activity.assert_not_called()

    def test_foundation_is_rechecked_before_each_actual_readiness_attempt(self):
        from logical_provider import Rejected
        from warehouse_operations import OperationError
        self.fake_readiness_clock()
        self.bound_provider.bind_registered.side_effect = Rejected('FOUNDATION_BINDING_INVALID')
        original = w._logical_foundation
        def changed(db=None):
            current = original(db)
            return dict(current,prefix='different-foundation') if self.bound_provider.bind_registered.called else current
        with patch.object(w,'_logical_foundation',side_effect=changed), \
                patch.object(w.subprocess,'run',return_value=Mock(stdout=json.dumps(self.objects()))):
            with self.assertRaises(OperationError) as caught:
                w.transition('wake','LOCAL')
        self.assertEqual(caught.exception.code,'FOUNDATION_NOT_READY')
        self.bound_provider.bind_registered.assert_called_once()
        self.provider_context.__exit__.assert_called_once()
        w.touch_activity.assert_not_called()

    def test_chill_never_constructs_provider_or_reads_engine_receipt(self):
        import runtime_profile
        with patch.object(w.subprocess,'run',return_value=Mock(stdout=json.dumps(self.objects(0,0)))), \
                patch.object(runtime_profile,'private_json',side_effect=AssertionError('engine receipt')):
            self.assertEqual(w.transition('chill','LOCAL')['result'],'noop')
        self.provider_factory.assert_not_called()
        w.touch_activity.assert_not_called()

    def test_cold_wake_uses_real_full_bind_and_waits_for_two_registered_trino_nodes(self):
        import logical_foundation
        from test_logical_provider import Harness, PROBE_SQL
        for section,key,value in [('storage','admin_credentials_file',dict(access_key='admin',secret_key='admin-secret')),
                ('storage','issuer_credentials_file',dict(access_key='issuer',secret_key='issuer-secret')),
                ('polaris','root_credentials_file',dict(client_id='root',client_secret='root-secret')),
                ('trino','provisioner_credentials_file',dict(version=1,username='logical_provisioner',password='provisioner-secret'))]:
            logical_foundation.save_private(self.foundation[section][key],json.dumps(value))
        logical_foundation.save_private(self.foundation['artifacts']['jar'],b'code')
        harness = Harness(self.foundation)
        actual = self.actual_provider_type(self.foundation,command=harness.command,http=harness.http,
            sdk=harness.sdk,popen=harness.popen)
        self.addCleanup(actual.close)
        with actual:
            full_saved = actual.probe()
        self.assertTrue(full_saved['ready'])
        Path(self.proof_path).write_text(json.dumps(dict(self.saved,result=full_saved)))
        self.provider_factory.return_value = actual
        statements, readiness = [], []
        def http(method,url,**kwargs):
            if url.endswith('/v1/statement'):
                statement = kwargs['data'].decode()
                statements.append(statement)
                if statement == PROBE_SQL and statements.count(PROBE_SQL) == 1:
                    return 200,b'{"data":[["logical_provisioner","483",true,1]]}'  # Deployments Ready, worker not yet registered.
            return harness.http(method,url,**kwargs)
        actual._http_transport = http
        full_probe = actual.probe
        def observe_probe():
            proof = full_probe()
            readiness.append(proof['ready'])
            return proof
        self.fake_readiness_clock()
        def activity():
            self.assertEqual(readiness,[False,True])
            self.assertTrue(all(child.terminated for child in harness.children))
        with patch.object(actual,'probe',side_effect=observe_probe), \
                patch.object(actual,'_effect',side_effect=AssertionError('business mutation')), \
                patch.object(w.subprocess,'run',return_value=Mock(stdout=json.dumps(self.objects()))), \
                patch.object(w,'touch_activity',side_effect=activity):
            report = w.transition('wake','LOCAL')
        self.assertEqual(report['result'],'noop')
        self.assertEqual(statements,[PROBE_SQL]*2)
        self.assertEqual(readiness,[False,True])
        self.assertEqual(len(harness.children),6)  # Registration fixture + one wake lifetime, never reopened on retry.
        self.assertTrue(all(child.terminated for child in harness.children))
        self.assertTrue(all(argv[-1] == 'read' for argv,_ in harness.commands if 'exec' in argv))
        def still_unready(method,url,**kwargs):
            if url.endswith('/v1/statement') and kwargs['data'].decode() == PROBE_SQL:
                return 200,b'{"data":[["logical_provisioner","483",true,1]]}'
            return harness.http(method,url,**kwargs)
        actual._http_transport = still_unready
        self.elapsed = 0.0
        readiness.clear()
        with patch.object(actual,'probe',side_effect=observe_probe), \
                patch.object(actual,'_effect',side_effect=AssertionError('business mutation')), \
                patch.object(w,'TIMEOUT',0.5), \
                patch.object(w.subprocess,'run',return_value=Mock(stdout=json.dumps(self.objects()))), \
                self.assertRaises(TimeoutError):
            w.transition('wake','LOCAL')
        self.assertEqual(readiness,[False,False,False])
        self.assertEqual(len(harness.children),9)
        self.assertTrue(all(child.terminated for child in harness.children))
        w.touch_activity.assert_not_called()



class ResidentWarehouseTests(unittest.TestCase):
    def test_scoped_admin_sql_context_keeps_same_db_and_denies_system_shortcuts(self):
        from test_request_auth import PrincipalTests
        import control_store
        import data_management as dm
        import query_context
        from grant_fixtures import verified_grants
        fixture = PrincipalTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.users['alice']['role'] = 'admin'
        fixture.save()
        with control_store.connect() as db:
            for key in (fixture.sales, fixture.finance):
                for name in ('raw', 'sandbox', 'staging', 'marts'):
                    schema = dm.create_schema(db, key, name, name, name, 'test')
                    if key == fixture.sales and name == 'raw':
                        schema_id = schema.id
            dm.update_grant(db, 'alice', fixture.sales, 1, 'admin')
            # Seed only the SQL boundary; no verification receipt is invented.
            db.execute("UPDATE effective_grants SET maximum_role='admin',epoch=epoch+1 WHERE principal_id='alice'")
        def context(who):
            return query_context.resolve_context(who, fixture.sales, schema_id, 'admin',
                warehouse_id='w', expected_binding_revision=1)
        scoped = context(fixture.principal())
        platform = context(fixture.principal('owner'))
        self.assertIsNotNone(scoped.grant_revision)
        for sql in ('CREATE TABLE sales.marts.t (id integer)',
                    'INSERT INTO sales.staging.t VALUES (1)',
                    'INSERT INTO marts.t SELECT * FROM sales.raw.t',
                    'ALTER TABLE sales.raw.t EXECUTE rollback_to_snapshot(1)',
                    'SELECT * FROM sales.sandbox.t'):
            self.assertEqual(w.check_sql_policy(sql, 'admin-write', 'admin', context=scoped), sql)
        for sql in ('SELECT * FROM finance.raw.t', 'CREATE TABLE finance.raw.t (id integer)',
                    'INSERT INTO sales.raw.t SELECT * FROM finance.raw.t',
                    'SELECT * FROM system.runtime.queries', 'SELECT * FROM sales.information_schema.tables',
                    'SHOW SESSION', 'SHOW CATALOGS', 'SHOW ROLES', 'SHOW FUNCTIONS',
                    'SET SESSION query_max_run_time = \'1h\'', 'RESET SESSION query_max_run_time',
                    'USE finance.raw', 'SHOW CREATE SCHEMA finance.raw',
                    'EXPLAIN SHOW SESSION'):
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                w.check_sql_policy(sql, 'admin-write', 'admin', context=scoped)
        # The authenticated platform context retains registered cross-DB access.
        sql = 'CREATE TABLE finance.raw.t (id integer)'
        self.assertIsNone(platform.grant_revision)
        with self.assertRaises(ValueError):
            w.check_sql_policy(sql, 'admin-write', 'admin', context=platform)
        import request_auth
        decision = request_auth.authorize_database(fixture.principal('owner'), fixture.sales, 'admin',
            expected_generation_id=platform.generation_id)
        self.assertEqual(w.check_sql_policy(sql, 'admin-write', 'admin', context=platform,
            _platform_authorized=decision.platform_admin), sql)
        with self.assertRaises(ValueError):
            w.check_sql_policy(sql, 'admin-write', 'admin', context=platform,
                database_only=True, _platform_authorized=decision.platform_admin)

    def test_active_profile_client_query_reports_normal_verified_envelope_only_for_finished(self):
        import runtime_profile
        with tempfile.TemporaryDirectory() as directory:
            auth = Path(directory) / 'client.json'
            auth.write_text(json.dumps(dict(username='alice', password='secret', selected_role='data_engineer')))
            auth.chmod(0o600)
            profile = Mock(client_auth_file=str(auth), sql_endpoint_url='http://127.0.0.1:8080')
            for state in ('FINISHED', 'UNKNOWN', 'CANCELED'):
                with self.subTest(state=state):
                    page = dict(id='query-1', columns=[dict(name='_col0', type='integer')],
                        data=[[1]], stats=dict(state=state), context=dict(database_id='sales'))
                    opener = Mock()
                    opener.open.return_value = io.BytesIO(json.dumps(page).encode())
                    with patch.object(runtime_profile, 'current', return_value=profile), \
                            patch.object(w.urllib.request, 'build_opener', return_value=opener):
                        result = w._profile_client_query('SELECT 1', 'sales', 'raw', 10)
                    self.assertEqual(result['warehouse'], 'LOCAL')
                    self.assertEqual(result['action'], 'query')
                    self.assertEqual(result['result'], 'verified' if state == 'FINISHED' else 'unverified')
                    self.assertEqual(result['rows'], [[1]])
                    self.assertEqual(result['query_id'], 'query-1')
                    self.assertEqual(result['state'], state)
                    self.assertEqual(result['context'], page['context'])
                    self.assertFalse(result['truncated'])

    def test_active_profile_query_main_preserves_warehouse_alias_and_uncertainty_exit(self):
        for state, expected in (('FINISHED', 0), ('UNKNOWN', 1)):
            with self.subTest(state=state), patch.object(w, '_profile_active', return_value=True), \
                    patch.object(w, '_profile_client_query', return_value=dict(warehouse='LOCAL', action='query',
                        result='verified' if state == 'FINISHED' else 'unverified', state=state, rows=[[1]], truncated=False)), \
                    patch.object(sys, 'argv', ['warehouse_resident.py', 'query', '--warehouse', 'XS',
                        '--sql', 'SELECT 1', '--catalog', 'sales', '--schema', 'raw']), \
                    patch.object(sys, 'stdout', new_callable=io.StringIO) as output:
                self.assertEqual(w.main(), expected)
                record = json.loads(output.getvalue())
                self.assertEqual(record['warehouse'], 'XS')
                self.assertEqual(record['state'], state)
                self.assertEqual(record['rows'], [[1]])

    def test_native_runtime_proof_requires_platform_and_selected_admin_without_admission(self):
        import request_auth
        import runtime_profile
        import query_service
        proof = dict(version=1, generation_id='fixed-generation', databases={}, profile_ref='fixed-profile')
        admin = request_auth.AuthenticatedPrincipal('admin', 'admin', True)
        with patch.object(request_auth, 'authenticate_principal', return_value=admin), \
                patch.object(runtime_profile, 'generation_attestation', return_value=proof), \
                patch('warehouse_admission.admit', side_effect=AssertionError('proof must work while CLOSED')):
            self.assertEqual(w._native_request({'X-Shirokuma-Role': 'admin'}, '/api/v1/runtime',
                'GET', b'', 'http://127.0.0.1:8080'), proof)
            for selected in ('bi_viewer', 'data_engineer'):
                with self.assertRaises(query_service.QueryError) as error:
                    w._native_request({'X-Shirokuma-Role': selected}, '/api/v1/runtime', 'GET', b'', 'http://127.0.0.1:8080')
                self.assertEqual(error.exception.status, 403)
            for path, method in (('/api/v1/runtime?path=secret', 'GET'), ('/api/v1/runtime', 'POST')):
                with self.assertRaises(query_service.QueryError):
                    w._native_request({}, path, method, b'', 'http://127.0.0.1:8080')
        with patch.object(request_auth, 'authenticate_principal', return_value=
                request_auth.AuthenticatedPrincipal('admin', 'admin', False)), self.assertRaises(query_service.QueryError):
            w._native_request({'X-Shirokuma-Role': 'admin'}, '/api/v1/runtime', 'GET', b'', 'http://127.0.0.1:8080')
        with patch.object(request_auth, 'authenticate_principal', side_effect=request_auth.AuthenticationError('required')), \
                self.assertRaises(request_auth.AuthenticationError):
            w._native_request({}, '/api/v1/runtime', 'GET', b'', 'http://127.0.0.1:8080')
        with patch.object(request_auth, 'authenticate_principal', return_value=admin), \
                patch.object(runtime_profile, 'generation_attestation', side_effect=ValueError('restart required')), \
                self.assertRaises(ValueError):
            w._native_request({'X-Shirokuma-Role': 'admin'}, '/api/v1/runtime', 'GET', b'', 'http://127.0.0.1:8080')

    def setUp(self):
        import runtime_profile
        self.configure_process = runtime_profile.configure_process
        profile_configuration = patch('runtime_profile.configure_process', return_value=False)
        profile_configuration.start()
        self.addCleanup(profile_configuration.stop)
        profile_state = patch('runtime_profile._current', None)
        profile_state.start()
        self.addCleanup(profile_state.stop)
        state = tempfile.TemporaryDirectory()
        self.addCleanup(state.cleanup)
        environment = patch.dict(w.os.environ, {'SHIROKUMA_STATE_DIR': state.name,
            'SHIROKUMA_AUTH_MODE': 'local', 'SHIROKUMA_AUTH_FILE': '',
            'SHIROKUMA_TRINO_AUTH_MODE': 'local', 'SHIROKUMA_TRINO_AUTH_FILE': '',
            'SHIROKUMA_TRINO_CA_FILE': ''})
        environment.start()
        self.addCleanup(environment.stop)
        advertised = patch.dict(w.os.environ)
        advertised.start()
        w.os.environ.pop('TRINO_ADVERTISED_URL', None)
        w.os.environ.pop('TRINO_TRUST_FORWARDED_HEADERS', None)
        self.addCleanup(advertised.stop)

    def test_three_tier_role_resolution(self):
        for user in ('bi_reader', 'powerbi', 'bi_team', 'POWERBI'):
            for role in (None, ''):
                self.assertEqual(w.resolve_role(user, role), 'bi_viewer')
            for role in ('admin', 'editor', 'data_engineer'):
                with self.assertRaises(ValueError):
                    w.resolve_role(user, role)
        for role in ('bi_viewer', 'viewer', 'bi_reader'):
            self.assertEqual(w.resolve_role('person', role), 'bi_viewer')
        for role in ('data_engineer', 'editor'):
            self.assertEqual(w.resolve_role('person', role), 'data_engineer')
        for role in (None, 'admin'):
            self.assertEqual(w.resolve_role('person', role), 'admin')
        for role in ('unknown', 'admn', ' ', 'administrator'):
            with self.assertRaisesRegex(ValueError, 'Invalid role:'):
                w.resolve_role('admin', role)
        with patch.dict(w.os.environ, {'SHIROKUMA_DEFAULT_ROLE': 'viewer'}):
            for user in ('', 'person'):
                self.assertEqual(w.resolve_role(user), 'bi_viewer')
            for user in ('admin', 'root', 'ordinary_person', 'bi_reader', 'unknown'):
                self.assertEqual(w.resolve_role(user), 'bi_viewer')
                for role in ('admin', 'data_engineer', 'editor'):
                    with self.assertRaises(ValueError):
                        w.resolve_role(user, role)
        with patch.dict(w.os.environ, {'SHIROKUMA_DEFAULT_ROLE': 'data_engineer'}):
            self.assertEqual(w.resolve_role('person'), 'data_engineer')
            with self.assertRaises(ValueError):
                w.resolve_role('person', 'admin')
        with patch.dict(w.os.environ, {'SHIROKUMA_DEFAULT_ROLE': 'admn'}):
            with self.assertRaisesRegex(ValueError, 'Invalid role:'):
                w.resolve_role('person')

    def test_quoted_keywords_and_dynamic_sql_boundaries(self):
        for keyword in ('where', 'group', 'order', 'having', 'limit', 'union', 'except', 'intersect'):
            for alias in (keyword, '"' + keyword + '"'):
                with self.subTest(alias=alias), self.assertRaises(ValueError):
                    w.check_bi_policy(f'SELECT * FROM iceberg.marts.t AS {alias}, iceberg.raw.secret', 'bi_reader')
        sql = 'SELECT "from", a.b FROM iceberg.marts.t a'
        self.assertEqual(w.check_bi_policy(sql, 'bi_reader'), sql)
        for sql in ('EXPLAIN EXECUTE p', 'EXPLAIN ANALYZE EXECUTE p', 'EXECUTE p',
                    'EXPLAIN (TYPE LOGICAL) EXECUTE p',
                    'SELECT 1; EXECUTE p', 'EXECUTE IMMEDIATE \'SELECT 1\''):
            for mode in ('read-only', 'sandbox-write', 'admin-write'):
                with self.subTest(sql=sql, mode=mode), self.assertRaises(ValueError):
                    w.check_sql_policy(sql, mode)
        self.assertEqual(w.check_sql_policy('SELECT "execute" FROM iceberg.marts.t'),
                         'SELECT "execute" FROM iceberg.marts.t')
        for sql in ('SELECT execute FROM iceberg.marts.t',
                    'SELECT t.execute FROM iceberg.marts.t t',
                    "SELECT 'text ; EXECUTE p' FROM iceberg.marts.t"):
            self.assertEqual(w.check_sql_policy(sql), sql)
            self.assertEqual(w.check_sql_policy(sql, role='bi_viewer'), sql)
        rollback = 'ALTER TABLE iceberg.marts.t EXECUTE rollback_to_snapshot(123)'
        self.assertEqual(w.check_sql_policy(rollback, 'admin-write'), rollback)

    def test_prepared_headers_are_checked_before_backend_access(self):
        for user, definition in (('bi_reader', 'SELECT 1'), ('powerbi', 'SELECT 1'),
                                 ('admin', 'DROP TABLE iceberg.raw.orders'),
                                 ('admin', 'EXPLAIN EXECUTE hidden'), ('admin', 'PREPARE hidden FROM SELECT 1')):
            headers = {'X-Trino-User': user,
                       'X-Trino-Prepared-Statement': 'p=' + w.urllib.parse.quote(definition, safe='')}
            for method, path in (('POST', '/v1/statement'), ('GET', '/v1/statement/q/1')):
                with self.subTest(user=user, definition=definition, method=method):
                    code, _, _, backend, wake, status, trino, _ = self.endpoint_request(
                        method, path, 'SELECT 1' if method == 'POST' else None, headers)
                    self.assertEqual(code, 403)
                    for mock in (backend.request, wake, status, trino):
                        mock.assert_not_called()
        self.assertEqual(self.endpoint_request('POST', '/v1/statement', 'SELECT 1', {
            'X-Trino-User': 'admin', 'X-Trino-Prepared-Statement': 'p=SELECT%201'})[0], 200)

    def test_activity_io_failure_preserves_successful_query(self):
        for operation in ('mkdir', 'write_text', 'replace', 'unlink'):
            original = getattr(Path, operation)
            activity = w.activity_path()
            def fail_activity(path, *args, **kwargs):
                if path == activity.parent or path.parent == activity.parent:
                    raise PermissionError('read-only activity')
                return original(path, *args, **kwargs)
            with self.subTest(operation=operation), \
                 patch.object(Path, operation, fail_activity), \
                 self.assertLogs(w.__name__, level='WARNING'), \
                 patch.object(w, 'status', return_value={'replicas': 1}), \
                 patch.object(w, 'trino', return_value=nullcontext('url')), \
                 patch.object(w, 'execute', return_value={'rows': [[1]]}), \
                 patch.object(w, 'telemetry'):
                self.assertEqual(w.query('LOCAL', 'SELECT 1')['rows'], [[1]])

    def test_role_sql_permissions(self):
        self.assertEqual(w.check_sql_policy('SELECT * FROM marts.t', role='bi_viewer'), 'SELECT * FROM marts.t')
        for mode in ('sandbox-write', 'admin-write'):
            with self.assertRaises(ValueError):
                w.check_sql_policy('SELECT 1', mode=mode, role='bi_viewer')
        for sql in ('SELECT * FROM iceberg.raw.t', 'INSERT INTO iceberg.sandbox.t VALUES (1)'):
            with self.assertRaises(ValueError):
                w.check_sql_policy(sql, role='bi_viewer')
        for schema in ('raw', 'sandbox'):
            sql = f'CREATE TABLE iceberg.{schema}.t AS SELECT 1'
            self.assertEqual(w.check_sql_policy(sql, mode='sandbox-write', role='data_engineer'), sql)
        for mode, sql in [('admin-write', 'SELECT 1'),
                          ('sandbox-write', 'CREATE TABLE iceberg.marts.t AS SELECT 1'),
                          ('sandbox-write', 'CREATE TABLE iceberg.staging.t AS SELECT 1')]:
            with self.assertRaises(ValueError):
                w.check_sql_policy(sql, mode=mode, role='data_engineer')

    def test_bi_policy(self):
        allowed = ['SELECT 1', 'SELECT count(*) FROM iceberg.marts.revenue',
                   'SELECT * FROM information_schema.tables', 'SELECT * FROM sys.tables',
                   'SELECT * FROM system.runtime.nodes', 'SHOW CATALOGS',
                   'SHOW TABLES FROM iceberg.marts', 'DESCRIBE iceberg.marts.revenue',
                   "SELECT 'iceberg.raw.t' /* iceberg.sandbox.t */ -- iceberg.staging.t\n",
                   'WITH x AS (SELECT * FROM iceberg.marts.t) SELECT * FROM x']
        for user in ('bi_reader', 'POWERBI', 'Bi_analyst'):
            for sql in allowed:
                with self.subTest(user=user, sql=sql):
                    self.assertEqual(w.check_bi_policy(sql, user), w.check_sql_policy(sql))
            for schema in ('raw', 'sandbox', 'staging', 'tpch', 'tpch_tiny', 'other'):
                for target in (f'iceberg.{schema}.t', f'"ICEBERG" . "{schema.upper()}" . "t"',
                               f'iceberg /* ignored */ . {schema}.t'):
                    for sql in (f'SELECT * FROM {target}',
                                f'SELECT * FROM iceberg.marts.t JOIN {target} ON true',
                                f'WITH x AS (SELECT * FROM {target}) SELECT * FROM x',
                                f'DESCRIBE {target}'):
                        with self.subTest(user=user, sql=sql), self.assertRaisesRegex(
                                ValueError, 'query restricted to iceberg.marts and metadata'):
                            w.check_bi_policy(sql, user)
            for sql in ('INSERT INTO iceberg.marts.t VALUES (1)', 'DROP TABLE iceberg.marts.t',
                        'SELECT 1; SELECT 2'):
                with self.assertRaises(ValueError):
                    w.check_bi_policy(sql, user)
        sql = 'INSERT INTO iceberg.raw.t VALUES (1)'
        self.assertEqual(w.check_bi_policy(sql, 'engineer'), sql)
        self.assertEqual(w.check_bi_policy('SELECT 1;  ', 'powerbi'), 'SELECT 1')

    def test_dynamic_and_prepared_sql_rejected_in_every_mode(self):
        statements = ("EXECUTE IMMEDIATE 'SELECT 1'", 'PREPARE p FROM SELECT 1',
                      'DEALLOCATE PREPARE p', 'EXECUTE p', 'SELECT 1; EXECUTE p',
                      "/* prefix */ EXECUTE /* comment */ IMMEDIATE 'DELETE FROM marts.t'")
        for sql in statements:
            for mode in ('read-only', 'sandbox-write', 'admin-write'):
                with self.subTest(sql=sql, mode=mode), self.assertRaises(ValueError):
                    w.check_sql_policy(sql, mode=mode)
            with self.assertRaises(ValueError):
                w.check_bi_policy(sql, 'bi_reader')
            with self.assertRaises(ValueError):
                w.check_bi_policy(sql, 'engineer')
        sql = 'ALTER TABLE iceberg.marts.t EXECUTE rollback_to_snapshot(123)'
        self.assertEqual(w.check_sql_policy(sql, mode='admin-write'), sql)
        self.assertEqual(w.check_sql_policy("SELECT 'EXECUTE IMMEDIATE' AS value"),
                         "SELECT 'EXECUTE IMMEDIATE' AS value")

    def test_bi_statement_and_schema_whitelists(self):
        blocked = ('USE marts', 'SET SESSION x = true', 'RESET SESSION x', 'START TRANSACTION',
                   'COMMIT', 'ROLLBACK', 'ANALYZE iceberg.marts.t', 'VALUES (1)',
                   'SELECT * FROM tpch_tiny.orders',
                   'SELECT * FROM marts.t, raw.secret',
                   'SELECT * FROM "other-schema".secret',
                   'SELECT * FROM (raw.secret)',
                   'SELECT * FROM (SELECT * FROM staging.t) x',
                   'SHOW TABLES FROM raw', 'SHOW COLUMNS FROM iceberg.sandbox.t',
                   'SHOW TABLES IN raw', 'SHOW STATS FOR tpch.t',
                   "SELECT * FROM TABLE(tpch.system.query(query => 'SELECT 1'))",
                   'EXPLAIN SELECT * FROM other.marts.t',
                   'DESCRIBE "iceberg" /* comment */ . "tpch" . "t"')
        for sql in blocked:
            for check in (lambda s: w.check_sql_policy(s, role='bi_viewer'),
                          lambda s: w.check_bi_policy(s, 'bi_reader')):
                with self.subTest(sql=sql), self.assertRaises(ValueError):
                    check(sql)
        for sql in ('/* prefix */ SELECT 1', 'EXPLAIN SELECT * FROM iceberg.marts.t',
                    'SELECT t.id FROM marts.t t JOIN iceberg.marts.u u ON t.id = u.id',
                    'SHOW TABLES FROM iceberg.marts', 'DESCRIBE marts.t',
                    'SELECT * FROM iceberg.information_schema.tables'):
            self.assertEqual(w.check_sql_policy(sql, role='bi_viewer'), sql)

    def endpoint_request(self, method, path, sql=None, headers=None, replicas=1,
                         backend_status=200, payload=None, failure=None, location=None,
                         omit_host=False):
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), http.server.BaseHTTPRequestHandler)
        client = http.client.HTTPConnection(*server.server_address, timeout=5)
        backend = Mock()
        response = backend.getresponse.return_value
        response.status = backend_status
        response.read.return_value = json.dumps(payload if payload is not None else
            {'id': 'q', 'stats': {'state': 'FINISHED'}, 'data': [[42]]}).encode()
        response.getheaders.return_value = [('Content-Type', 'application/json'),
                                            ('X-Trino-Set-Catalog', 'iceberg')]
        if location:
            response.getheaders.return_value.append(('Location', location))
        response.getheader.return_value = ''
        backend.request.side_effect = failure
        # Keep a real listening server, while allowing serve_endpoint to install
        # its handler and exercise one complete HTTP exchange synchronously.
        def factory(address, handler):
            server.RequestHandlerClass = handler
            return server
        with patch.object(w.http.server, 'ThreadingHTTPServer', side_effect=factory) as factory_mock, \
             patch.object(w.http.client, 'HTTPConnection', return_value=backend), \
             patch.object(w, 'trino', return_value=nullcontext('http://127.0.0.1:1234')) as trino, \
             patch.object(w, 'status', return_value={'replicas': replicas}) as status, \
             patch.object(w, 'transition') as wake, patch.object(w, '_LAST_ACTIVITY', 0):
            thread = threading.Thread(target=w.serve_endpoint, kwargs={'port': server.server_port, 'once': True})
            thread.start()
            try:
                if omit_host:
                    client.putrequest(method, path, skip_host=True)
                    for name, value in (headers or {}).items():
                        client.putheader(name, value)
                    body = sql.encode() if sql is not None else None
                    if body is not None:
                        client.putheader('Content-Length', str(len(body)))
                    client.endheaders(body)
                else:
                    client.request(method, path, body=sql, headers=headers or {})
                result = client.getresponse()
                code, result_headers, data = result.status, dict(result.getheaders()), result.read()
            finally:
                client.close()
                thread.join(timeout=5)
            self.assertFalse(thread.is_alive(), 'once=True did not stop')
            factory_mock.assert_called_once()
            return code, result_headers, json.loads(data), backend, wake, status, trino, w._LAST_ACTIVITY

    def test_endpoint_host_and_advertised_url_rewriting(self):
        uri = 'http://backend:8080/v1/statement/q/1?token=abc#fragment'
        payload = dict.fromkeys(('nextUri', 'partialCancelUri', 'infoUri'), uri)
        payload.update(id='q', stats={'state': 'RUNNING'})
        cases = [({'Host': 'localhost:18080'}, '', '', 'http://localhost:18080'),
                 ({'Host': 'internal:8080', 'X-Forwarded-Host': 'proxy.example:443',
                   'X-Forwarded-Proto': 'https'}, '', '', 'http://internal:8080'),
                 ({'Host': 'internal:8080', 'X-Forwarded-Host': 'proxy.example:443',
                   'X-Forwarded-Proto': 'https'}, '', 'true', 'https://proxy.example:443'),
                 ({'Host': '', 'X-Forwarded-Host': 'proxy.example:443',
                   'X-Forwarded-Proto': 'https'}, '', 'true', 'https://proxy.example:443'),
                 ({'Host': 'localhost:18080', 'X-Forwarded-Host': 'proxy.example'},
                  'https://trino.example/trino/', 'true', 'https://trino.example/trino')]
        for headers, advertised, trust, base in cases:
            with self.subTest(base=base), patch.dict(w.os.environ, {
                    'TRINO_ADVERTISED_URL': advertised, 'TRINO_TRUST_FORWARDED_HEADERS': trust}):
                code, response_headers, page, *_ = self.endpoint_request(
                    'POST', '/v1/statement', 'SELECT 1', headers, payload=payload, location=uri)
                self.assertEqual(code, 200)
                expected = base + '/v1/statement/q/1?token=abc#fragment'
                self.assertEqual(page, dict(payload, **dict.fromkeys(('nextUri', 'partialCancelUri', 'infoUri'), expected)))
                self.assertEqual(response_headers['Location'], expected)

    def test_endpoint_missing_host_does_not_imply_forwarded_trust(self):
        uri = 'http://backend:8080/v1/statement/q/1?token=abc'
        payload = dict.fromkeys(('nextUri', 'partialCancelUri', 'infoUri'), uri)
        payload.update(id='q', stats={'state': 'RUNNING'})
        for host in (None, ''):
            for trust in ('', 'false', '0', 'true'):
                with self.subTest(host=host, trust=trust), patch.dict(w.os.environ, {
                        'TRINO_TRUST_FORWARDED_HEADERS': trust}):
                    headers = {'X-Forwarded-Host': 'untrusted.example:443',
                               'X-Forwarded-Proto': 'https'}
                    if host is not None:
                        headers['Host'] = host
                    code, response_headers, page, *_ = self.endpoint_request(
                        'POST', '/v1/statement', 'SELECT 1', headers, payload=payload,
                        location=uri, omit_host=True)
                    self.assertEqual(code, 200)
                    parsed = w.urllib.parse.urlsplit(page['nextUri'])
                    self.assertEqual(parsed.scheme, 'https' if trust == 'true' else 'http')
                    self.assertEqual(parsed.hostname,
                                     'untrusted.example' if trust == 'true' else '127.0.0.1')
                    self.assertGreater(parsed.port, 0)
                    self.assertEqual(parsed.path, '/v1/statement/q/1')
                    self.assertEqual(parsed.query, 'token=abc')
                    self.assertEqual(page, dict(payload, **dict.fromkeys(('nextUri', 'partialCancelUri', 'infoUri'), page['nextUri'])))
                    self.assertEqual(response_headers['Location'], page['nextUri'])

    def test_endpoint_uses_threading_http_server(self):
        with patch.object(w.http.server, 'ThreadingHTTPServer') as server:
            w.serve_endpoint(port=18080, once=True)
        self.assertEqual(server.call_args.args[0], ('127.0.0.1', 18080))
        server.return_value.__enter__.return_value.handle_request.assert_called_once_with()

    def test_endpoint_polls_and_cancels_while_statement_is_blocked(self):
        real_server = http.server.ThreadingHTTPServer
        real_client = http.client.HTTPConnection
        ready, running, release = threading.Event(), threading.Event(), threading.Event()
        servers, outcomes = [], []

        def factory(address, handler):
            server = real_server(address, handler)
            servers.append(server)
            ready.set()
            return server

        def connection(*args, **kwargs):
            backend = Mock()
            def request(method, *args, **kwargs):
                if method == 'POST':
                    running.set()
                    if not release.wait(5):
                        raise TimeoutError('test statement was not released')
            backend.request.side_effect = request
            backend.getresponse.return_value.status = 200
            backend.getresponse.return_value.read.return_value = b'{"id":"q","stats":{"state":"FINISHED"}}'
            backend.getresponse.return_value.getheaders.return_value = []
            backend.getresponse.return_value.getheader.return_value = ''
            return backend

        def send(method, path):
            client = real_client(*servers[0].server_address, timeout=2)
            try:
                client.request(method, path, body='SELECT 1' if method == 'POST' else None)
                response = client.getresponse()
                response.read()
                outcomes.append((method, response.status))
            finally:
                client.close()

        with patch.object(w.http.server, 'ThreadingHTTPServer', side_effect=factory), \
             patch.object(w.http.client, 'HTTPConnection', side_effect=connection), \
             patch.object(w, 'trino', return_value=nullcontext('http://localhost:1234')), \
             patch.object(w, 'status', return_value={'replicas': 1}), \
             patch.object(w, 'initialize_auto_chill'), \
             patch.object(w, '_AUTO_CHILL_MINUTES', 1), patch.object(w, '_LAST_ACTIVITY', 0), \
             patch.object(w.time, 'time', return_value=100), \
             patch.object(w, '_execute', return_value={'rows': [[0]]}), \
             patch.object(w, 'transition') as transition:
            endpoint = threading.Thread(target=w.serve_endpoint, kwargs={'port': 0})
            endpoint.start()
            statement = None
            try:
                self.assertTrue(ready.wait(2))
                statement = threading.Thread(target=send, args=('POST', '/v1/statement'))
                statement.start()
                self.assertTrue(running.wait(2))
                self.assertEqual(w.read_last_activity(), 100)
                self.assertFalse(w.check_auto_chill()['chilled'])
                transition.assert_not_called()
                send('GET', '/v1/statement/q/1')
                send('DELETE', '/v1/statement/q/1')
                # Until the POST response provides a continuation, guessing a
                # backend path cannot borrow its still-active admission.
                self.assertEqual(outcomes, [('GET', 409), ('DELETE', 409)])
                self.assertTrue(statement.is_alive())
            finally:
                release.set()
                if statement:
                    statement.join(5)
                if servers:
                    servers[0].shutdown()
                endpoint.join(5)
            self.assertFalse(endpoint.is_alive())
            self.assertIn(('POST', 200), outcomes)

    def test_endpoint_statement_wake_headers_and_continuation_urls(self):
        code, headers, page, backend, wake, _, _, activity = self.endpoint_request(
            'POST', '/v1/statement', 'SELECT 42; ',
            {'X-Trino-User': 'powerbi', 'X-Trino-Source': 'Power BI'}, replicas=0,
            payload={'id': 'q', 'stats': {'state': 'RUNNING'}, 'nextUri': 'http://backend:8080/v1/statement/queued/q/1?x=2',
                     'partialCancelUri': 'http://backend:8080/v1/statement/queued/q/1', 'data': [[42]]})
        self.assertEqual(code, 200)
        wake.assert_called_once_with('wake', 'LOCAL')
        self.assertGreater(activity, 0)
        self.assertEqual(headers['X-Trino-Set-Catalog'], 'iceberg')
        self.assertEqual(page['data'], [[42]])
        self.assertTrue(page['nextUri'].startswith('http://127.0.0.1:'))
        self.assertTrue(page['nextUri'].endswith('/v1/statement/queued/q/1?x=2'))
        self.assertNotIn('backend', page['partialCancelUri'])
        call = backend.request.call_args
        self.assertEqual(call.args, ('POST', '/v1/statement'))
        self.assertEqual(call.kwargs['body'], b'SELECT 42')
        self.assertEqual(call.kwargs['headers']['X-Trino-Source'], 'Power BI')
        self.assertEqual(call.kwargs['headers']['X-Trino-User'], 'powerbi')
        backend.close.assert_called_once()

    def test_powerbi_desktop_import_simulation(self):
        headers = {
            'User-Agent': 'Microsoft.Data.Mashup (https://go.microsoft.com/fwlink/?LinkID=304249)',
            'X-Trino-User': 'bi_reader',
            'X-Trino-Catalog': 'iceberg',
            'X-Trino-Schema': 'marts',
        }
        cases = (
            ('SHOW SCHEMAS FROM iceberg',
             [{'name': 'Schema', 'type': 'varchar'}], [['marts']]),
            ('SHOW TABLES FROM iceberg.marts',
             [{'name': 'Table', 'type': 'varchar'}], [['fct_orders']]),
            ("SELECT table_name FROM information_schema.tables WHERE table_schema = 'marts'",
             [{'name': 'table_name', 'type': 'varchar'}], [['fct_orders']]),
            ('SELECT order_id, customer_code, total_amount, order_date FROM iceberg.marts.fct_orders',
             [{'name': 'order_id', 'type': 'bigint'},
              {'name': 'customer_code', 'type': 'varchar'},
              {'name': 'total_amount', 'type': 'decimal(18,2)'},
              {'name': 'order_date', 'type': 'date'}],
             [[1, '000123', '1250.50', '2026-10-17'],
              [2, '000007', '0.01', '2026-10-16']]),
        )
        for sql, columns, rows in cases:
            with self.subTest(sql=sql):
                payload = {'id': 'powerbi-import', 'columns': columns, 'data': rows,
                           'stats': {'state': 'FINISHED'}}
                code, _, page, backend, _, _, _, activity = self.endpoint_request(
                    'POST', '/v1/statement', sql, headers, payload=payload)
                self.assertEqual(code, 200)
                self.assertEqual(page, payload)
                backend.request.assert_called_once()
                call = backend.request.call_args
                self.assertEqual(call.args, ('POST', '/v1/statement'))
                self.assertEqual(call.kwargs['body'], sql.encode())
                for name, value in headers.items():
                    self.assertEqual(call.kwargs['headers'][name], value)
                self.assertGreater(activity, 0)
                backend.close.assert_called_once()

        for sql in ('SELECT * FROM iceberg.raw.orders', 'SELECT * FROM raw.orders'):
            with self.subTest(sql=sql):
                code, _, page, backend, wake, status, trino, activity = self.endpoint_request(
                    'POST', '/v1/statement', sql, headers, replicas=0)
                self.assertEqual(code, 403)
                self.assertIn('SQL policy violation', page['error']['message'])
                self.assertIn('restricted to iceberg.marts and metadata', page['error']['message'])
                for mock in (backend.request, wake, status, trino):
                    mock.assert_not_called()
                self.assertEqual(activity, 0)

    def test_endpoint_rejection_precedes_access(self):
        for sql, headers in (
                ('DELETE FROM iceberg.marts.t', {'X-Trino-User': 'bi_reader'}),
                ('SELECT * FROM "iceberg"."raw".t', {'X-Trino-User': 'BI_tool'}),
                ('SELECT * FROM staging.t', {'X-Trino-User': 'powerbi', 'X-Trino-Catalog': 'iceberg'}),
                ('SELECT * FROM t', {'X-Trino-User': 'bi_reader', 'X-Trino-Catalog': 'iceberg',
                                     'X-Trino-Schema': 'sandbox'}),
                ('SELECT * FROM t', {'X-Trino-User': 'bi_reader', 'X-Trino-Catalog': 'tpch',
                                     'X-Trino-Schema': 'tiny'}),
                ('SELECT * FROM t', {'X-Trino-User': 'bi_reader', 'X-Trino-Schema': 'raw'})):
            with self.subTest(sql=sql):
                code, _, page, backend, wake, status, trino, activity = self.endpoint_request(
                    'POST', '/v1/statement', sql, headers, replicas=0)
                self.assertEqual(code, 403)
                self.assertIn('SQL policy violation', page['error']['message'])
                for mock in (backend.request, wake, status, trino):
                    mock.assert_not_called()
                self.assertEqual(activity, 0)

    def test_endpoint_continuations_info_errors_and_non_bi(self):
        for method, path in (('GET', '/v1/statement/queued/q/1?token=abc'),
                             ('DELETE', '/v1/statement/executing/q/1'),
                             ('POST', '/v1/statement/executing/q/1'), ('GET', '/v1/info')):
            code, _, _, backend, wake, status, _, _ = self.endpoint_request(method, path)
            self.assertEqual(code, 200 if path == '/v1/info' else 409)
            if path == '/v1/info':
                self.assertEqual(backend.request.call_args.args, (method, path))
            else:
                backend.request.assert_not_called()
            wake.assert_not_called()
            status.assert_not_called()
        code, _, page, _, _, _, _, activity = self.endpoint_request(
            'GET', '/v1/info', backend_status=503, payload={'error': {'message': 'unavailable'}})
        self.assertEqual(code, 503)
        self.assertEqual(page['error']['message'], 'unavailable')
        self.assertEqual(activity, 0)
        self.assertEqual(self.endpoint_request('GET', '/v1/info', failure=OSError('offline'))[0], 502)
        self.assertEqual(self.endpoint_request('GET', '/unrelated')[0], 404)
        code, _, _, _, wake, _, _, _ = self.endpoint_request(
            'POST', '/v1/statement', 'INSERT INTO iceberg.raw.t VALUES (1)', {'X-Trino-User': 'engineer'})
        self.assertEqual(code, 200)
        wake.assert_not_called()

    def test_endpoint_parser(self):
        for options, expected in (([], (8080, '127.0.0.1', 'LOCAL', False)),
                (['--port', '8090', '--bind', 'localhost', '--warehouse', 's', '--once'],
                 (8090, 'localhost', 'S', True))):
            with patch.object(w.sys, 'argv', ['warehouse', 'endpoint'] + options), \
                 patch.object(w, 'serve_endpoint', return_value={}) as serve, \
                 patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(w.main(), 0)
                serve.assert_called_once_with(*expected)

    def test_auto_chill_configuration_and_threshold(self):
        with patch.object(w, '_AUTO_CHILL_MINUTES', 0), patch.object(w, '_LAST_ACTIVITY', 0), \
             patch.object(w.time, 'time', return_value=100) as clock, \
             patch.object(w, 'status', return_value={'replicas': 1}) as status, \
             patch.object(w, 'start_auto_chill_daemon') as start, \
             patch.object(w, 'stop_auto_chill_daemon') as stop, \
             patch.object(w, 'trino', return_value=nullcontext('url')), \
             patch.object(w, '_execute', return_value={'rows': [[0]]}), \
             patch.object(w, 'transition') as transition:
            self.assertFalse(w.check_auto_chill()['chilled'])
            status.assert_not_called()
            for minutes in (-1, 1441, True, 1.0, '1', None):
                with self.subTest(minutes=minutes), self.assertRaises(ValueError):
                    w.configure_auto_chill(minutes)
            self.assertEqual(w.configure_auto_chill(1), {'auto_chill_minutes': 1, 'idle_seconds': 0})
            clock.return_value = 159
            self.assertFalse(w.check_auto_chill()['chilled'])
            clock.return_value = 160
            self.assertEqual(w.check_auto_chill(), dict(chilled=True, auto_chill_minutes=1, idle_seconds=60))
            transition.assert_called_once_with('chill', 'LOCAL')
            status.return_value = {'replicas': 0}
            self.assertFalse(w.check_auto_chill()['chilled'])
            transition.assert_called_once()
            self.assertEqual(w.configure_auto_chill(1440)['auto_chill_minutes'], 1440)
            self.assertEqual(w.configure_auto_chill(0)['auto_chill_minutes'], 0)
            self.assertEqual(start.call_count, 2)
            stop.assert_called_once_with()
            clock.return_value = 0
            self.assertEqual(w.auto_chill_status()['idle_seconds'], 0)

    def test_auto_chill_active_queries_postpone_chill(self):
        for count in (1, 7):
            w.activity_path().unlink(missing_ok=True)
            with self.subTest(count=count), patch.object(w, '_AUTO_CHILL_MINUTES', 1), \
                 patch.object(w, '_LAST_ACTIVITY', 0), patch.object(w.time, 'time', return_value=100), \
                 patch.object(w, 'status', return_value={'replicas': 1}), \
                 patch.object(w, 'trino', return_value=nullcontext('url')), \
                 patch.object(w, '_execute', return_value={'rows': [[count]]}) as execute, \
                 patch.object(w, 'transition') as transition:
                self.assertEqual(w.check_auto_chill(), dict(chilled=False, active_queries=count,
                    auto_chill_minutes=1, idle_seconds=0))
                self.assertEqual(w._LAST_ACTIVITY, 100)
                transition.assert_not_called()
                self.assertEqual(execute.call_args.args[1],
                    "SELECT COUNT(*) FROM system.runtime.queries WHERE state IN ('QUEUED', 'WAITING_FOR_RESOURCES', 'DISPATCHING', 'PLANNING', 'STARTING', 'RUNNING', 'FINISHING') "
                    "AND query NOT LIKE '%system.runtime.queries%'")

    def test_auto_chill_protects_all_active_states(self):
        for state in ('QUEUED', 'WAITING_FOR_RESOURCES', 'DISPATCHING', 'PLANNING', 'STARTING', 'RUNNING', 'FINISHING'):
            w.activity_path().unlink(missing_ok=True)
            def active_count(url, sql, headers):
                # Emulate a runtime table containing one query in this state.
                states = sql.split('state IN (', 1)[1].split(')', 1)[0]
                return {'rows': [[int(repr(state) in states)]]}
            with self.subTest(state=state), patch.object(w, '_AUTO_CHILL_MINUTES', 1), \
                 patch.object(w, '_LAST_ACTIVITY', 0), patch.object(w.time, 'time', return_value=100), \
                 patch.object(w, 'status', return_value={'replicas': 1}), \
                 patch.object(w, 'trino', return_value=nullcontext('url')), \
                 patch.object(w, '_execute', side_effect=active_count), \
                 patch.object(w, 'transition') as transition:
                self.assertEqual(w.check_auto_chill()['active_queries'], 1)
                transition.assert_not_called()

    def test_auto_chill_activity_during_poll_aborts_chill(self):
        def poll(*args):
            # A CLI or endpoint request completes while Trino is being polled.
            w.touch_activity()
            return {'rows': [[0]]}
        with patch.object(w, '_AUTO_CHILL_MINUTES', 1), patch.object(w, '_LAST_ACTIVITY', 0), \
             patch.object(w.time, 'time', return_value=100), \
             patch.object(w, 'status', return_value={'replicas': 1}), \
             patch.object(w, 'trino', return_value=nullcontext('url')), \
             patch.object(w, '_execute', side_effect=poll), \
             patch.object(w, 'transition') as transition:
            self.assertFalse(w.check_auto_chill()['chilled'])
            transition.assert_not_called()

    def test_auto_chill_settings_survive_initialization(self):
        with patch.object(w, '_AUTO_CHILL_MINUTES', 0), \
             patch.object(w, 'start_auto_chill_daemon') as start, \
             patch.object(w, 'stop_auto_chill_daemon') as stop:
            for minutes in (17, 0):
                w.configure_auto_chill(minutes)
                w._AUTO_CHILL_MINUTES = 999
                start.reset_mock()
                stop.reset_mock()
                w.initialize_auto_chill()
                self.assertEqual(w.auto_chill_status()['auto_chill_minutes'], minutes)
                (start if minutes else stop).assert_called_once_with()
            path = w.activity_path().with_name('auto_chill.json')
            for invalid in ('invalid json', '{"auto_chill_minutes": true}', '{"auto_chill_minutes": -1}'):
                path.write_text(invalid)
                w.initialize_auto_chill()
                self.assertEqual(w._AUTO_CHILL_MINUTES, 0)

    def test_auto_chill_monitor_reloads_external_settings(self):
        path = w.activity_path().with_name('auto_chill.json')
        path.parent.mkdir(parents=True, exist_ok=True)

        def update(minutes):
            subprocess.run([sys.executable, '-c',
                            'import json, pathlib, sys; '
                            'pathlib.Path(sys.argv[1]).write_text(json.dumps('
                            '{"auto_chill_minutes": int(sys.argv[2])}))',
                            str(path), str(minutes)], check=True, timeout=10)

        def tick(interval):
            # Each interval sees a write from a separate process, without restart.
            minutes = next(settings)
            if minutes is None:
                return True
            update(minutes)
            return False

        settings = iter((5, 1, 0, None))
        with patch.object(w, '_AUTO_CHILL_THREAD', None), \
             patch.object(w, '_AUTO_CHILL_STOP') as stop, \
             patch.object(w.threading, 'Thread') as thread, \
             patch.object(w, '_AUTO_CHILL_MINUTES', 1), patch.object(w, '_LAST_ACTIVITY', 0), \
             patch.object(w.time, 'time', return_value=100), \
             patch.object(w, 'status', return_value={'replicas': 1}) as status, \
             patch.object(w, 'trino', return_value=nullcontext('url')), \
             patch.object(w, '_execute', return_value={'rows': [[0]]}), \
             patch.object(w, 'transition') as transition:
            stop.wait.side_effect = tick
            w.start_auto_chill_daemon(interval=3)
            thread.call_args.kwargs['target']()
            self.assertEqual(w._AUTO_CHILL_MINUTES, 0)
            status.assert_called_once_with('LOCAL')
            transition.assert_called_once_with('chill', 'LOCAL')
            w.stop_auto_chill_daemon()

    def test_auto_chill_reload_off_during_poll_aborts_chill(self):
        path = w.activity_path().with_name('auto_chill.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"auto_chill_minutes": 1}')

        def poll(*args):
            path.write_text('{"auto_chill_minutes": 0}')
            return {'rows': [[0]]}

        with patch.object(w, '_AUTO_CHILL_MINUTES', 1), patch.object(w, '_LAST_ACTIVITY', 0), \
             patch.object(w.time, 'time', return_value=100), \
             patch.object(w, 'status', return_value={'replicas': 1}), \
             patch.object(w, 'trino', return_value=nullcontext('url')), \
             patch.object(w, '_execute', side_effect=poll), \
             patch.object(w, 'transition') as transition:
            self.assertEqual(w.check_auto_chill(),
                             dict(chilled=False, auto_chill_minutes=0, idle_seconds=100))
            transition.assert_not_called()

    def test_query_records_activity_before_initial_execution(self):
        def execute(*args):
            self.assertEqual(w.read_last_activity(), 100)
            self.assertFalse(w.check_auto_chill()['chilled'])
            return {'rows': []}

        with patch.object(w, '_AUTO_CHILL_MINUTES', 1), patch.object(w, '_LAST_ACTIVITY', 0), \
             patch.object(w.time, 'time', return_value=100), \
             patch.object(w, 'status', return_value={'replicas': 1}), \
             patch.object(w, 'trino', return_value=nullcontext('url')), \
             patch.object(w, 'execute', side_effect=execute), \
             patch.object(w, 'transition') as transition, patch.object(w, 'telemetry'):
            w.query('LOCAL', 'SELECT 1')
            transition.assert_not_called()

    def test_cross_process_activity_and_idle_seconds(self):
        with patch.object(w, '_LAST_ACTIVITY', 0), patch.object(w.time, 'time', return_value=100):
            w.touch_activity()
            self.assertEqual(json.loads(w.activity_path().read_text()), {'last_activity': 100})
        script = (
            "import importlib.util, sys; "
            f"sys.path.insert(0, {str(Path(w.__file__).resolve().parent)!r}); "
            f"spec = importlib.util.spec_from_file_location('resident', {str(Path(w.__file__).resolve())!r}); "
            "module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
            "module.time.time = lambda: 190; module.touch_activity()"
        )
        subprocess.run([sys.executable, '-c', script], check=True, timeout=10)
        with patch.object(w, '_LAST_ACTIVITY', 100), patch.object(w.time, 'time', return_value=200), \
             patch.object(w, '_AUTO_CHILL_MINUTES', 1), patch.object(w, 'status') as status:
            self.assertEqual(w.read_last_activity(), 190)
            self.assertEqual(w.auto_chill_status()['idle_seconds'], 10)
            self.assertFalse(w.check_auto_chill()['chilled'])
            status.assert_not_called()
            w._LAST_ACTIVITY = 195
            self.assertEqual(w.auto_chill_status()['idle_seconds'], 5)
            w.activity_path().write_text('incomplete JSON')
            self.assertEqual(w.read_last_activity(), 195)
            w.activity_path().unlink()
            self.assertEqual(w.read_last_activity(), 195)

    def test_query_and_endpoint_update_shared_activity(self):
        with patch.object(w, 'status', return_value={'replicas': 1}), \
             patch.object(w, 'trino', return_value=nullcontext('url')), \
             patch.object(w, 'execute', return_value={'rows': []}), \
             patch.object(w, 'telemetry'), patch.object(w.time, 'time', return_value=100):
            w.query('LOCAL', 'SELECT 1')
        self.assertEqual(json.loads(w.activity_path().read_text())['last_activity'], 100)
        with patch.object(w.time, 'time', return_value=200):
            self.endpoint_request('POST', '/v1/statement', 'SELECT 1')
        self.assertEqual(json.loads(w.activity_path().read_text())['last_activity'], 200)

    def test_auto_chill_query_failure_does_not_chill(self):
        with patch.object(w, '_AUTO_CHILL_MINUTES', 1), patch.object(w, '_LAST_ACTIVITY', 0), \
             patch.object(w.time, 'time', return_value=100), \
             patch.object(w, 'status', return_value={'replicas': 1}), \
             patch.object(w, 'trino', return_value=nullcontext('url')), \
             patch.object(w, '_execute', side_effect=RuntimeError('Trino unavailable')), \
             patch.object(w, 'transition') as transition:
            with self.assertRaisesRegex(RuntimeError, 'Trino unavailable'):
                w.check_auto_chill()
            transition.assert_not_called()

    def test_auto_chill_daemon_start_stop_and_restart(self):
        self.addCleanup(w.stop_auto_chill_daemon)
        with patch.object(w, '_AUTO_CHILL_MINUTES', 0), patch.object(w, '_LAST_ACTIVITY', 0):
            w.configure_auto_chill(1)
            first = w._AUTO_CHILL_THREAD
            self.assertTrue(first.daemon)
            self.assertTrue(first.is_alive())
            w.configure_auto_chill(2)
            self.assertIs(w._AUTO_CHILL_THREAD, first)
            w.configure_auto_chill(0)
            self.assertFalse(first.is_alive())
            self.assertIsNone(w._AUTO_CHILL_THREAD)
            w.stop_auto_chill_daemon()
            w.configure_auto_chill(1)
            self.assertIsNot(w._AUTO_CHILL_THREAD, first)
            w.configure_auto_chill(0)

    def test_auto_chill_daemon_checks_periodically_and_recovers(self):
        with patch.object(w, '_AUTO_CHILL_THREAD', None), \
             patch.object(w, '_AUTO_CHILL_STOP') as stop, \
             patch.object(w.threading, 'Thread') as thread, \
             patch.object(w, 'check_auto_chill', side_effect=[RuntimeError('temporary'), {}]) as check, \
             patch.object(w.logging, 'getLogger') as logger:
            stop.wait.side_effect = [False, False, True]
            w.start_auto_chill_daemon(interval=3)
            thread.return_value.start.assert_called_once_with()
            thread.call_args.kwargs['target']()
            self.assertEqual(check.call_count, 2)
            self.assertEqual(stop.wait.call_count, 3)
            stop.wait.assert_called_with(3)
            logger.return_value.exception.assert_called_once()
            w.stop_auto_chill_daemon()
            stop.set.assert_called_once_with()
            thread.return_value.join.assert_called_once_with()

    def test_activity_updated_for_accepted_query_or_successful_wake(self):
        with patch.object(w, '_LAST_ACTIVITY', 0), patch.object(w.time, 'time', return_value=50), \
             patch.object(w, 'status', return_value={'replicas': 1, 'state': 'active'}), \
             patch.object(w, 'trino', return_value=nullcontext('url')), \
             patch.object(w, 'execute', return_value={'rows': []}) as execute, \
             patch.object(w, 'telemetry'):
            w.query('LOCAL', 'SELECT 1')
            self.assertEqual(w._LAST_ACTIVITY, 50)
            w.transition('wake', 'LOCAL')
            self.assertEqual(w._LAST_ACTIVITY, 50)
            w._LAST_ACTIVITY = 0
            with patch.object(w, 'status', return_value={'replicas': 0, 'state': 'chilled'}):
                w.transition('chill', 'LOCAL')
            self.assertEqual(w._LAST_ACTIVITY, 0)
            with patch.object(w, 'status', side_effect=RuntimeError('failed')):
                with self.assertRaises(RuntimeError):
                    w.transition('wake', 'LOCAL')
            self.assertEqual(w._LAST_ACTIVITY, 0)
            execute.side_effect = RuntimeError('failed')
            with self.assertRaises(RuntimeError):
                w.query('LOCAL', 'SELECT 1')
            self.assertEqual(w._LAST_ACTIVITY, 50)
            with self.assertRaises(w.admission.AdmissionConflict):
                w.transition('wake', 'LOCAL')
            self.assertEqual(w._LAST_ACTIVITY, 50)

    def test_qualify_rewrite(self):
        body = 'SELECT id, row_number() OVER (PARTITION BY id ORDER BY ts DESC) AS rn FROM events'
        for suffix in ('', ' ORDER BY id', ' LIMIT 5', ' ORDER BY id LIMIT 5'):
            with self.subTest(suffix=suffix):
                statement = body + ' QUALIFY rn = 1' + suffix
                expected = 'SELECT * FROM (' + body + ') AS _qualify_sub WHERE rn = 1' + suffix
                self.assertEqual(w.normalize_trino_sql(statement), expected)
                self.assertEqual(w.check_sql_policy(statement + '; '), expected)
        body = 'WITH ranked AS (' + body + ') SELECT * FROM ranked'
        self.assertEqual(w.normalize_trino_sql(body + ' qualify rn = 1'),
                         'SELECT * FROM (' + body + ') AS _qualify_sub WHERE rn = 1')

    def test_qualify_ignores_quotes_comments_and_nested_clauses(self):
        for statement in ("SELECT 'QUALIFY it''s ORDER BY LIMIT' AS s", 'SELECT "QUALIFY" FROM t',
                          'SELECT * FROM (SELECT * FROM t QUALIFY rn = 1)',
                          'WITH t AS (SELECT * FROM x QUALIFY rn = 1) SELECT * FROM t',
                          'SELECT 1 -- QUALIFY rn = 1\n', 'SELECT 1 /* QUALIFY rn = 1 */',
                          'CREATE TABLE t AS SELECT * FROM x QUALIFY rn = 1'):
            self.assertEqual(w.normalize_trino_sql(statement), statement)
        statement = "SELECT rn FROM t QUALIFY rn IN (SELECT rn FROM x ORDER BY rn LIMIT 1) AND 'LIMIT' <> '' ORDER BY rn LIMIT 3"
        self.assertEqual(w.normalize_trino_sql(statement),
                         "SELECT * FROM (SELECT rn FROM t) AS _qualify_sub WHERE rn IN (SELECT rn FROM x ORDER BY rn LIMIT 1) AND 'LIMIT' <> '' ORDER BY rn LIMIT 3")
        self.assertEqual(w.normalize_trino_sql('SELECT rn FROM t -- comment\nQUALIFY rn = 1'),
                         'SELECT * FROM (SELECT rn FROM t -- comment\n) AS _qualify_sub WHERE rn = 1')
        with self.assertRaisesRegex(ValueError, 'multiple statements'):
            w.check_sql_policy('SELECT rn FROM t QUALIFY rn = 1; SELECT 2')

    def test_sql_policy_read_only_and_quoting(self):
        for statement in ["SELECT 1", "WITH x AS (SELECT 1) SELECT * FROM x", "invalid SQL",
                          "SELECT 'DROP; it''s fine', \"DELETE;\"", "-- INSERT ignored\nSELECT 1"]:
            self.assertEqual(w.check_sql_policy(statement + ';  '), statement)
        for mode in ('read-only', 'sandbox-write', 'admin-write'):
            for statement in ["SELECT 1; SELECT 2", "SELECT 'x'; INSERT INTO iceberg.sandbox.t VALUES (1)"]:
                with self.subTest(mode=mode, statement=statement), self.assertRaisesRegex(ValueError, 'SQL policy violation'):
                    w.check_sql_policy(statement, mode)
            for verb in ('DROP TABLE', 'TRUNCATE TABLE', 'DELETE FROM', 'GRANT', 'REVOKE'):
                for prefix in ('', 'WITH x AS (SELECT 1) ', '/* comment */ '):
                    with self.subTest(mode=mode, verb=verb, prefix=prefix), self.assertRaises(ValueError):
                        w.check_sql_policy(prefix + verb.lower() + ' iceberg.sandbox.t', mode)
        with self.assertRaises(ValueError):
            w.check_sql_policy('SELECT 1', 'unknown')
        for verb in ('INSERT INTO', 'UPDATE', 'MERGE INTO', 'CREATE TABLE', 'ALTER TABLE', 'CALL'):
            with self.assertRaisesRegex(ValueError, 'read-only'):
                w.check_sql_policy(verb + ' iceberg.sandbox.t')

    def test_sql_policy_write_targets(self):
        sandbox = ['CREATE SCHEMA IF NOT EXISTS iceberg.sandbox',
                   'CREATE TABLE iceberg.sandbox.any_table (id BIGINT)',
                   'CREATE OR REPLACE TABLE "iceberg"."sandbox"."other" AS SELECT 2',
                   'INSERT INTO iceberg.sandbox.any_table VALUES (1)',
                   'ALTER TABLE iceberg.sandbox.any_table EXECUTE rollback_to_snapshot(123)']
        admin = ['CREATE SCHEMA IF NOT EXISTS iceberg.other',
                 'CREATE OR REPLACE TABLE iceberg.marts.any_table AS SELECT 1',
                 'INSERT INTO iceberg.marts.any_table VALUES (1)',
                 'ALTER TABLE iceberg.other.any_table EXECUTE rollback_to_snapshot(123)']
        for statement in sandbox:
            for mode in ('sandbox-write', 'admin-write'):
                with self.subTest(statement=statement, mode=mode):
                    self.assertEqual(w.check_sql_policy(statement, mode), statement)
        for statement in admin:
            self.assertEqual(w.check_sql_policy(statement, 'admin-write'), statement)
            with self.assertRaises(ValueError):
                w.check_sql_policy(statement, 'sandbox-write')
        for mode in ('sandbox-write', 'admin-write'):
            for statement in ['CREATE TABLE marts.t AS SELECT 1', 'INSERT INTO iceberg.other.t VALUES (1)',
                              'CREATE TABLE iceberg.sandbox.t.extra AS SELECT 1',
                              'ALTER TABLE iceberg.sandbox.t RENAME TO iceberg.marts.t',
                              'ALTER TABLE iceberg.sandbox.t EXECUTE optimize()',
                              'UPDATE iceberg.sandbox.t SET x=1', 'CALL arbitrary()']:
                with self.subTest(mode=mode, statement=statement), self.assertRaises(ValueError):
                    w.check_sql_policy(statement, mode)

    def test_policy_rejection_precedes_warehouse_access(self):
        with patch.object(w, 'status') as status, patch.object(w, 'trino') as trino:
            with self.assertRaises(ValueError):
                w.query('LOCAL', 'INSERT INTO iceberg.marts.t VALUES (1)')
            status.assert_not_called()
            trino.assert_not_called()
        with patch.object(w, 'status', return_value={'replicas': 1}), \
             patch.object(w, 'trino', return_value=nullcontext('url')), \
             patch.object(w, 'execute', return_value={'rows': []}) as execute, patch.object(w, 'telemetry'):
            report = w.query('LOCAL', 'INSERT INTO iceberg.sandbox.t VALUES (1); ', mode='sandbox-write')
            self.assertEqual(report['mode'], 'sandbox-write')
            self.assertEqual(execute.call_args.args[1], 'INSERT INTO iceberg.sandbox.t VALUES (1)')

    def test_sizes_and_states(self):
        for size in ("xs", "s", "local"):
            self.assertEqual(w.warehouse(size), size.upper())
        with self.assertRaises(ValueError):
            w.warehouse("XL")
        for replicas, ready, state in [(0, 0, "chilled"), (1, 0, "starting"), (2, 1, "active")]:
            with patch.object(w, "deployment", return_value={
                "spec": {"replicas": replicas}, "status": {"readyReplicas": ready}
            }):
                self.assertEqual(w.status("xs"), dict(warehouse="XS", backend="trino-local", deployment="trino-local",
                    namespace="shirokuma-dev", state=state, replicas=replicas, ready_replicas=ready))

    @patch.object(w, "telemetry", return_value="skipped")
    def test_transitions_and_noops(self, telemetry):
        for action, replicas, state in [("chill", 1, "active"), ("wake", 0, "chilled")]:
            with patch.object(w, "status", return_value={"replicas": replicas, "state": state}), \
                 patch.object(w, "kubectl") as kube, \
                 patch.object(w, "deployment", side_effect=[
                     {"spec": {"replicas": 0}, "status": {"replicas": 1}},
                     {"spec": {"replicas": 0}, "status": {}}]), \
                 patch.object(w.time, "sleep"), patch.object(w, "trino", return_value=nullcontext()):
                report = w.transition(action, "XS")
                self.assertEqual(report["result"], "verified")
                self.assertEqual(report["backend"], "trino-local")
                kube.assert_any_call("scale", "deployment/trino-local", f"--replicas={1-replicas}")
                self.assertEqual(report["after_replicas"], 1-replicas)
        for action, replicas, state in [("chill", 0, "chilled"), ("wake", 1, "active")]:
            with patch.object(w, "status", return_value={"replicas": replicas, "state": state}), \
                 patch.object(w, "kubectl") as kube:
                self.assertEqual(w.transition(action, "S")["result"], "noop")
                kube.assert_not_called()

    @patch.object(w, "telemetry", return_value="recorded")
    def test_arbitrary_sql_pagination_and_auto_wake(self, telemetry):
        sql = "SELECT name FROM arbitrary_table WHERE amount > 17"
        with patch.object(w, "status", return_value={"replicas": 0}), \
             patch.object(w, "transition") as wake, \
             patch.object(w, "trino", return_value=nullcontext("http://localhost:1234")), \
             patch.object(w, "http_json", side_effect=[
                 {"id": "query1", "stats": {"state": "RUNNING"}, "nextUri": "http://localhost:1234/next"},
                 {"id": "query1", "stats": {"state": "FINISHED"}, "columns": [{"name": "name"}], "data": [["a"], ["b"]]}]) as http:
            report = w.query("local", sql, "iceberg", "public")
            wake.assert_called_once_with("wake", "LOCAL")
            self.assertEqual(report["rows"], [["a"], ["b"]])
            self.assertEqual(report["row_count"], 2)
            self.assertEqual(report["backend"], "trino-local")
            self.assertFalse(report["truncated"])
            self.assertTrue(report["auto_woken"])
            self.assertEqual(report["query_id"], "query1")
            self.assertEqual(http.call_args_list[0].args[1], sql.encode())
            headers = http.call_args_list[0].args[2]
            self.assertEqual(headers["X-Trino-Catalog"], "iceberg")
            self.assertEqual(headers["X-Trino-Schema"], "public")
            self.assertEqual(headers["X-Trino-Source"], "shirokuma-warehouse-LOCAL")

    @patch.object(w, "telemetry", return_value="recorded")
    def test_job_query_drains_pages_without_retaining_rows(self, telemetry):
        with patch.object(w, "status", return_value={"replicas": 1}), \
             patch.object(w, "trino", return_value=nullcontext("http://localhost:1234")), \
             patch.object(w, "http_json", side_effect=[
                 {"id": "job1", "stats": {"state": "RUNNING"}, "data": [[1], [2]], "nextUri": "http://localhost:1234/next"},
                 {"id": "job1", "stats": {"state": "RUNNING"}, "data": [[3]], "nextUri": "http://localhost:1234/done"},
                 {"id": "job1", "stats": {"state": "FINISHED"}}]) as http, patch.object(w.urllib.request, "urlopen") as cancel:
            report = w.query("LOCAL", "SELECT 1", max_rows=None)
        self.assertEqual(http.call_count, 3)
        cancel.assert_not_called()
        self.assertEqual(report['rows'], [])
        self.assertEqual(report['row_count'], 3)
        self.assertFalse(report['truncated'])
        self.assertEqual(report['result'], 'verified')

    @patch.object(w, "telemetry", return_value="recorded")
    def test_job_query_reports_error_after_result_page(self, telemetry):
        with patch.object(w, "status", return_value={"replicas": 1}), \
             patch.object(w, "trino", return_value=nullcontext("http://localhost:1234")), \
             patch.object(w, "http_json", side_effect=[
                 {"id": "job1", "stats": {"state": "RUNNING"}, "data": [[1]], "nextUri": "http://localhost:1234/next"},
                 {"id": "job1", "stats": {"state": "FAILED"}, "error": {"message": "private value", "errorName": "DIVISION_BY_ZERO"}}]), \
             patch.object(w.urllib.request, "urlopen") as cancel:
            with self.assertRaises(w.TrinoQueryError) as failure:
                w.query("LOCAL", "SELECT 1", max_rows=None)
        cancel.assert_not_called()
        self.assertEqual(failure.exception.error_name, 'DIVISION_BY_ZERO')
        self.assertEqual(w.TrinoQueryError('private', 'unsafe value').error_name, 'UNKNOWN')
        self.assertEqual(telemetry.call_args.kwargs, {'status': 'failed'})

    def test_query_rejections_and_trino_error(self):
        with self.assertRaises(ValueError):
            w.query("XS", "  ")
        with self.assertRaises(ValueError):
            w.query("LOCAL", "SELECT 1", max_rows=0)
        with patch.object(w, "status", return_value={"replicas": 0}), \
             patch.object(w, "transition") as wake:
            with self.assertRaises(ValueError):
                w.query("XS", "SELECT 1", no_auto_wake=True)
            wake.assert_not_called()
        for failure in ({"error": {"message": "bad SQL"}}, OSError("HTTP failed"), TimeoutError()):
            with patch.object(w, "status", return_value={"replicas": 1}), \
                 patch.object(w, "trino", return_value=nullcontext("http://localhost:1234")), \
                 patch.object(w, "http_json", side_effect=[failure]), patch.object(w, "telemetry") as telemetry:
                with self.assertRaises((RuntimeError, OSError)):
                    w.query("S", "invalid SQL")
                self.assertEqual(telemetry.call_args.args[0], "query")
                self.assertIsInstance(telemetry.call_args.args[1], int)
                self.assertEqual(telemetry.call_args.kwargs, {"status": "failed"})

    def test_default_local(self):
        for action in ("status", "chill", "wake", "query"):
            argv = ["warehouse", action] + (["--sql", "SELECT 1"] if action == "query" else [])
            with patch.object(w.sys, "argv", argv), patch.object(w, "status", return_value={}) as status, \
                 patch.object(w, "transition", return_value={}) as transition, \
                 patch.object(w, "query", return_value={}) as query, patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(w.main(), 0)
                call = {"status": status, "query": query}.get(action, transition).call_args
                self.assertEqual(call.args[1 if action in ("chill", "wake") else 0], "LOCAL")
                if action == "query":
                    self.assertEqual(call.args[-1], 1000)

    def test_query_mode_parser(self):
        with patch.object(w.sys, 'argv', ['warehouse', 'query', '--sql', 'SELECT 1', '--mode', 'admin-write']), \
             patch.object(w, 'query', return_value={}) as query, patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(w.main(), 0)
        self.assertEqual(query.call_args.kwargs, {'mode': 'admin-write'})
        self.assertEqual(query.call_args.args[-1], 1000)

    @patch.object(w, "telemetry", return_value="skipped")
    def test_bounded_rows_and_best_effort_cancel(self, telemetry):
        for count, next_uri, cancel_error in [(3, "http://localhost/next", False),
                                             (2, "http://localhost/next", True), (3, None, False), (2, None, False)]:
            with patch.object(w, "status", return_value={"replicas": 1}), \
                 patch.object(w, "trino", return_value=nullcontext("http://localhost")), \
                 patch.object(w, "http_json", return_value={"id": "bounded", "stats": {"state": "RUNNING" if next_uri else "FINISHED"}, "data": [[i] for i in range(count)],
                              "nextUri": next_uri}) as http, \
                 patch.object(w.urllib.request, "urlopen", side_effect=OSError() if cancel_error else None) as cancel:
                report = w.query("LOCAL", "SELECT arbitrary", max_rows=2)
                self.assertEqual(report["rows"], [[0], [1]])
                self.assertEqual(report["truncated"], count > 2 or bool(next_uri))
                http.assert_called_once()
                self.assertEqual(cancel.call_count, int(bool(next_uri)))
                if next_uri:
                    self.assertEqual(cancel.call_args.args[0].get_method(), "DELETE")
                    self.assertEqual(cancel.call_args.args[0].full_url, next_uri)

    def test_port_forward_cleanup_on_failure_and_success(self):
        for failure in (False, True):
            process = Mock()
            process.poll.return_value = None
            with patch.object(w.subprocess, "Popen", return_value=process) as popen, \
                 patch.object(w.socket, "socket") as socket, \
                 patch.object(w, "http_json", return_value={"starting": False}):
                socket.return_value.__enter__.return_value.getsockname.return_value = ("127.0.0.1", 1234)
                try:
                    with w.trino() as url:
                        self.assertTrue(url.startswith("http://127.0.0.1:"))
                        if failure:
                            raise RuntimeError("query failed")
                except RuntimeError:
                    pass
                process.terminate.assert_called_once()
                process.wait.assert_called_once()
                self.assertIn("svc/trino-local", popen.call_args.args[0])

    def test_managed_sql_uses_enrolled_foundation_for_both_roles(self):
        import database_creation_bridge as bridge
        import database_creation_worker as worker
        import runtime_profile
        from tests.test_database_creation_bridge import BridgeFixture
        for role in ('native', 'workbench'):
            with self.subTest(role=role):
                fixture = BridgeFixture()
                with patch('runtime_profile.configure_process', self.configure_process):
                    fixture.setUp()
                previous = sys.getprofile()
                try:
                    view, nodes = self.sql_foundation(fixture)
                    record = runtime_profile.private_json(fixture.owners[role]['record_path'])
                    hello = dict(record, purpose='http', private_enrollment_path=str(fixture.path),
                                 profile_path=str(fixture.profile))
                    managed, _, _ = fixture.connect(hello)
                    kubeconfig = fixture.value['preparation']['kubeconfig']
                    calls = []
                    def profile(frame, event, arg):
                        if event == 'call' and frame.f_code is worker.validate_candidate_profile.__code__:
                            calls.append(frame.f_code.co_name)
                    sys.setprofile(profile)
                    with patch.dict('os.environ', KUBECONFIG='/wrong/config', KUBE_CONTEXT='wrong', KUBE_NAMESPACE='wrong'), \
                         patch.object(w.subprocess, 'run', side_effect=self.sql_responses(view, nodes)) as run, \
                         patch.object(w, 'KUBECTL', ['kubectl', '--context', 'wrong', '-n', 'wrong']), \
                         patch.object(w.subprocess, 'Popen') as popen, \
                         patch.object(w.socket, 'socket') as socket_factory, \
                         patch.object(w, 'http_json', return_value={'starting': False}) as http, \
                         patch('trino_auth.backend_ssl_context', return_value=None):
                        socket_factory.return_value.__enter__.return_value.getsockname.return_value = ('127.0.0.1', 1234)
                        popen.return_value.poll.return_value = None
                        with w.trino() as url:
                            self.assertTrue(url.startswith('https://127.0.0.1:'))
                        argv = popen.call_args.args[0]
                        self.assertEqual(argv[:7], ['kubectl', '--kubeconfig', kubeconfig,
                            '--context', fixture.value['foundation']['kube_context'], '-n', 'shirokuma-dev'])
                        self.assertEqual(argv[7:-1], ['port-forward', '--address', '127.0.0.1', 'svc/trino-local'])
                        self.assertRegex(argv[-1], r'^\d+:8080$')
                        self.assertEqual(calls, [])
                        self.assert_sql_reads(run, managed.config)
                        self.assertEqual(http.call_args.args, (url + '/v1/info',))
                        popen.return_value.terminate.assert_called_once()
                        popen.return_value.wait.assert_called_once()
                finally:
                    sys.setprofile(previous)
                    fixture.doCleanups()

    def sql_foundation(self, fixture):
        import base64
        import hashlib
        fixture.value['foundation']['ca_sha256'] = hashlib.sha256(b'enrolled-ca').hexdigest()
        fixture.save()
        return (dict(**{'current-context': fixture.value['foundation']['kube_context']},
                     clusters=[dict(cluster={'server': fixture.value['foundation']['api_server'],
                         'certificate-authority-data': base64.b64encode(b'enrolled-ca').decode()})]),
                dict(items=[dict(metadata=dict(name='node', uid='uid'))]))

    def sql_responses(self, view, nodes):
        return [subprocess.CompletedProcess([], 0, json.dumps(value), '') for value in (view, nodes)]

    def assert_sql_reads(self, run, config):
        prefix = ['rtk', 'proxy', 'kubectl', '--kubeconfig', str(config.preparation().kubeconfig),
                  '--context', config.value['foundation']['kube_context'], '--request-timeout=10s']
        self.assertEqual([call.args[0] for call in run.call_args_list],
                         [prefix + ['config', 'view', '--minify', '--raw', '-o', 'json'],
                          prefix + ['get', 'nodes', '-o', 'json']])
        for call in run.call_args_list:
            self.assertEqual(call.kwargs, dict(capture_output=True, text=True, timeout=15))

    def test_managed_sql_real_sealed_full_candidate(self):
        import base64
        import hashlib
        import os
        import socket
        import uuid
        import database_creation_auth as auth
        import database_creation_bridge as bridge
        import database_creation_worker as worker
        import database_creations as creations
        import runtime_profile
        from tests.test_database_creation_preparation import ManagedPreparationTests
        from warehouse_operator import save_private
        from warehouse_operations import OperationError
        fixture = ManagedPreparationTests()
        fixture.setUp()
        try:
            candidate = fixture.managed_candidate(prepare=False)
            managed, _, _, _, _, value, path, _ = candidate
            value['foundation']['ca_sha256'] = hashlib.sha256(b'enrolled-ca').hexdigest()
            save_private(path, value)
            managed.config = bridge.Configuration(path)
            managed, full, *_ = fixture.managed_candidate(candidate)
            detail = creations.get(fixture.alice, 'admin', fixture.root_id)
            plan = next(p for p in detail['plans'] if p['phase'] == 'full')
            approval = creations.mutate(fixture.alice, 'admin', fixture.root_id, 'APPROVE',
                dict(expected_revision=detail['revision'], plan_id=plan['plan_id'],
                     operation_id=plan['operation_id'], expected_operation_revision=plan['operation_revision'],
                     delivery_digest=plan['delivery_digest']), 'resident-full-review', phase='full')
            auth.claim(fixture.config.enrollment, fixture.executor, approval['request_id'],
                       owner_id=managed.hello['owner_instance_id'])
            request, root, original, _ = worker.resolve(managed, approval['request_id'])
            worker.seal_activation_plans(managed, request, root, original, full)
            for role in ('native', 'workbench'):
                desc = worker.descriptor(request, root, original, full, role)
                record = managed.config.owner(role)
                journal = Path(value['owners'][role]['directory'])/'activations'/(desc['activation_id']+'.json')
                save_private(journal, dict(descriptor=desc, state='UNKNOWN',
                    stop=dict(state='ACK', intent_id=str(uuid.uuid4())),
                    start=dict(state='INTENT', intent_id=str(uuid.uuid4())), readiness=dict(state='UNSENT')))
                record['runtime_instance_id'] = desc['candidate_runtime_instance_id']
                save_private(Path(value['owners'][role]['record_path']), record)
                hello = dict(record, purpose='http', private_enrollment_path=str(path),
                             profile_path=full.config['runtime_profile'])
                with self.subTest(role=role):
                    parent, child = socket.socketpair()
                    fixture.addCleanup(parent.close); fixture.addCleanup(child.close)
                    parent.sendall(json.dumps(hello).encode()+b'\n')
                    duplicate = os.dup
                    with patch.dict(os.environ, SHIROKUMA_RUNTIME_PROFILE=full.config['runtime_profile'],
                                    SHIROKUMA_MANAGED_FD='3', KUBECONFIG='/wrong/config',
                                    KUBE_CONTEXT='wrong', KUBE_NAMESPACE='wrong'), \
                         patch.object(bridge.os, 'dup', side_effect=lambda fd: duplicate(child.fileno())), \
                         patch.object(bridge, '_managed', None):
                        self.configure_process()
                        successor = bridge.configure()
                        fixture.addCleanup(successor.channel.close)
                        self.assertEqual(bridge.line(parent)['runtime_attestation'],
                                         full.config['managed_handoff']['runtime_attestation'])
                        view = {'current-context': value['foundation']['kube_context'],
                            'clusters': [dict(cluster={'server': value['foundation']['api_server'],
                                'certificate-authority-data': base64.b64encode(b'enrolled-ca').decode()})]}
                        nodes = dict(items=[dict(metadata=dict(name='node', uid='uid'))])
                        with patch.object(w.subprocess, 'run', side_effect=self.sql_responses(view, nodes)) as run, \
                             patch.object(w.subprocess, 'Popen') as popen, \
                             patch.object(w.socket, 'socket') as network, \
                             patch.object(w, 'http_json', return_value={'starting': False}), \
                             patch('trino_auth.backend_ssl_context', return_value=None):
                            network.return_value.__enter__.return_value.getsockname.return_value = ('127.0.0.1', 1234)
                            popen.return_value.poll.return_value = None
                            with w.trino():
                                pass
                            self.assertEqual(popen.call_args.args[0], ['kubectl', '--kubeconfig',
                                str(managed.config.preparation().kubeconfig), '--context', value['foundation']['kube_context'],
                                '-n', 'shirokuma-dev', 'port-forward', '--address', '127.0.0.1', 'svc/trino-local', '1234:8080'])
                            self.assert_sql_reads(run, successor.config)
                        # The real validator must reject byte drift in the sealed kubeconfig.
                        kubeconfig = managed.config.preparation().kubeconfig
                        before = kubeconfig.read_bytes()
                        try:
                            kubeconfig.write_bytes(before+b'\n')
                            with patch.object(w.subprocess, 'Popen') as popen, \
                                 patch.object(w.subprocess, 'run') as run, patch.object(w, 'http_json') as http:
                                with self.assertRaises(OperationError):
                                    with w.trino():
                                        self.fail('sealed drift must fail')
                                popen.assert_not_called(); run.assert_not_called(); http.assert_not_called()
                        finally:
                            kubeconfig.write_bytes(before)
        finally:
            fixture.doCleanups()

    def test_managed_sql_rejects_lost_authority_before_transport(self):
        import runtime_profile
        from tests.test_database_creation_bridge import BridgeFixture
        from warehouse_operations import OperationError
        for loss in ('eof', 'closed_fd', 'owner', 'runtime', 'profile', 'enrollment', 'api', 'ca', 'nodes',
                     'lineage', 'context', 'insecure', 'kubectl', 'late_eof', 'invalid_candidate'):
            with self.subTest(loss=loss):
                fixture = BridgeFixture()
                with patch('runtime_profile.configure_process', self.configure_process):
                    fixture.setUp()
                try:
                    view, nodes = self.sql_foundation(fixture)
                    managed, parent, _ = fixture.connect()
                    if loss == 'eof':
                        parent.close()
                    elif loss == 'closed_fd':
                        managed.channel.close()
                    elif loss == 'owner':
                        Path(fixture.owners['workbench']['record_path']).unlink()
                    elif loss == 'runtime':
                        Path(fixture.profile_value['trino_auth_file']).unlink()
                    elif loss == 'profile':
                        fixture.profile.write_text('{}')
                    elif loss == 'enrollment':
                        fixture.path.write_text('{}')
                    elif loss == 'api':
                        view['clusters'][0]['cluster']['server'] = 'https://wrong:6443'
                    elif loss == 'ca':
                        view['clusters'][0]['cluster']['certificate-authority-data'] = 'd3Jvbmc='
                    elif loss == 'nodes':
                        nodes['items'][0]['metadata']['uid'] = 'wrong'
                    elif loss == 'lineage':
                        Path(fixture.value['preparation']['tofu_state']).write_text('{"lineage":"wrong"}')
                    elif loss == 'context':
                        view['current-context'] = 'wrong'
                    elif loss == 'insecure':
                        view['clusters'][0]['cluster']['insecure-skip-tls-verify'] = True
                    elif loss == 'invalid_candidate':
                        from warehouse_operator import save_private
                        path = fixture.root/'unsealed-profile.json'
                        save_private(path, fixture.profile_value)
                        w.os.environ['SHIROKUMA_RUNTIME_PROFILE'] = str(path)
                        managed.hello['profile_path'] = str(path)
                        self.configure_process()
                    responses = iter(self.sql_responses(view, nodes))
                    def read(*args, **kwargs):
                        if loss == 'kubectl':
                            raise subprocess.TimeoutExpired(args[0], 15)
                        result = next(responses)
                        if loss == 'late_eof' and 'get' in args[0]:
                            parent.close()
                        return result
                    with patch.object(w.subprocess, 'run', side_effect=read), \
                         patch.object(w.subprocess, 'Popen') as popen, \
                         patch.object(w, 'http_json') as http:
                        process = Mock()
                        process.poll.return_value = None
                        for persistent in (None, dict(process=process, url='https://127.0.0.1:1234')):
                            responses = iter(self.sql_responses(view, nodes))
                            with patch.object(w, '_PERSISTENT_TRINO', persistent), self.assertRaises(OperationError):
                                with w.trino():
                                    self.fail('lost managed authority must not yield')
                        popen.assert_not_called()
                        http.assert_not_called()
                        process.terminate.assert_not_called()
                finally:
                    fixture.doCleanups()

    def test_unmanaged_sql_argv_remains_legacy(self):
        import database_creation_bridge as bridge
        with patch.object(bridge, '_managed', None), patch.dict('os.environ', {}, clear=True), \
             patch.object(w, 'KUBECTL', ['kubectl', '--context', 'legacy', '-n', 'legacy']):
            self.assertEqual(w._sql_kubectl(), w.KUBECTL)
        with patch.object(bridge, '_managed', None), patch.dict('os.environ', SHIROKUMA_MANAGED_FD='3'):
            from warehouse_operations import OperationError
            with self.assertRaises(OperationError):
                w._sql_kubectl()

    def test_uninitialized_telemetry_is_best_effort(self):
        with patch.dict("sys.modules", {"agenthouse.manage": None}):
            self.assertEqual(w.telemetry("query", 12), "skipped")

    def test_persistent_trino_reuses_and_owns_tunnel(self):
        for failure in (False, True):
            with self.subTest(failure=failure), patch.object(w.subprocess, 'Popen') as popen, \
                 patch.object(w, 'http_json', return_value={'starting': False}):
                process = popen.return_value
                process.poll.return_value = None
                with self.assertRaisesRegex(RuntimeError, 'caller failed') if failure else nullcontext():
                    with w.persistent_trino() as persistent_url:
                        for _ in range(2):
                            with w.trino() as url:
                                self.assertEqual(url, persistent_url)
                        with w.persistent_trino() as nested_url:
                            self.assertEqual(nested_url, persistent_url)
                        process.terminate.assert_not_called()
                        popen.assert_called_once()
                        if failure:
                            raise RuntimeError('caller failed')
                self.assertIsNone(w._PERSISTENT_TRINO)
                process.terminate.assert_called_once()
                process.wait.assert_called_once()

    def test_dead_persistent_tunnel_falls_back_and_failed_start_cleans_up(self):
        dead = Mock()
        dead.poll.return_value = 1
        with patch.object(w, '_PERSISTENT_TRINO', {'process': dead, 'url': 'dead'}), \
             patch.object(w.subprocess, 'Popen') as popen, \
             patch.object(w, 'http_json', return_value={'starting': False}):
            popen.return_value.poll.return_value = None
            with w.trino() as url:
                self.assertNotEqual(url, 'dead')
            popen.return_value.terminate.assert_called_once()
            dead.terminate.assert_not_called()
        with patch.object(w.subprocess, 'Popen') as popen:
            popen.return_value.poll.return_value = 1
            with self.assertRaisesRegex(RuntimeError, 'port-forward exited'):
                with w.persistent_trino():
                    self.fail('failed startup must not yield')
            self.assertIsNone(w._PERSISTENT_TRINO)
            popen.return_value.terminate.assert_called_once()

    def test_rich_telemetry_omits_none_and_passes_event_fields(self):
        manage = Mock()
        with patch.dict('sys.modules', {'agenthouse.manage': manage}):
            self.assertEqual(w.telemetry('bear.data_engineer', 7, prefix='', target='iceberg.marts.t@42',
                risk_level='normal', model_name='local-model', prompt_tokens=0,
                completion_tokens=None, blocked_by_policy=False), 'recorded')
        fields = manage.event.call_args.kwargs
        self.assertEqual(fields['target'], 'iceberg.marts.t@42')
        self.assertEqual(fields['action'], 'bear.data_engineer')
        self.assertEqual(fields['risk_level'], 'normal')
        self.assertEqual(fields['prompt_tokens'], 0)
        self.assertFalse(fields['blocked_by_policy'])
        self.assertNotIn('completion_tokens', fields)

    def test_failed_pawprint_has_only_action_and_timing(self):
        manage = Mock()
        with patch.dict("sys.modules", {"agenthouse.manage": manage}):
            self.assertEqual(w.telemetry("query", 12, status="failed"), "recorded")
        fields = manage.event.call_args.kwargs
        self.assertEqual(fields["action"], "warehouse.query")
        self.assertEqual(fields["status"], "failed")
        self.assertEqual(set(fields), {"action", "target", "status", "agent_id", "task_id", "trace_id", "duration_ms"})


if __name__ == "__main__":
    unittest.main()
