import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import request_auth
import warehouse_runtime_routes as runtime


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        env = patch.dict(os.environ, SHIROKUMA_STATE_DIR=temporary.name)
        env.start(); self.addCleanup(env.stop)
        self.principal = request_auth.AuthenticatedPrincipal('owner','admin',True)
        for p in (patch('warehouse_lifecycle.applicable', return_value=True),
                  patch('request_auth._principal_accounts', return_value={'owner':dict(role='admin',platform_admin=True)})):
            p.start(); self.addCleanup(p.stop)

    def test_policy_cas_idempotency_conflicts_and_disable(self):
        body = dict(minutes=5, expected_revision=0)
        ack = runtime.accept(self.principal,'admin','auto-chill',body,'first')
        self.assertEqual(runtime.accept(self.principal,'admin','auto-chill',body,'first'),ack)
        self.assertEqual(runtime.policy(),dict(minutes=5,revision=1))
        with self.assertRaisesRegex(Exception,'IDEMPOTENCY_CONFLICT'):
            runtime.accept(self.principal,'admin','auto-chill',dict(minutes=6,expected_revision=1),'first')
        with self.assertRaisesRegex(Exception,'REVISION_CONFLICT'):
            runtime.accept(self.principal,'admin','auto-chill',body,'stale')
        runtime.accept(self.principal,'admin','auto-chill',dict(minutes=0,expected_revision=1),'disable')
        self.assertEqual(runtime.policy(),dict(minutes=0,revision=2))
        self.assertEqual(runtime.operation(ack['operation_id'])['phase'],'SUCCEEDED')

    def test_current_real_account_revocation(self):
        with patch('request_auth._principal_accounts', return_value={'owner':dict(role='admin',platform_admin=False)}):
            with self.assertRaises(PermissionError):
                runtime.accept(self.principal,'admin','wake',{},'key')

    def test_manual_action_rejects_nonempty_body_and_nonplatform_selected_role(self):
        with self.assertRaises(ValueError): runtime.accept(self.principal,'admin','wake',{'sql':'SELECT 1'},'key')
        with self.assertRaises(PermissionError): runtime.accept(self.principal,'bi_viewer','wake',{},'key')


    def test_policy_disable_does_not_wait_for_physical_wake_lock(self):
        import threading
        completed = threading.Event()
        errors = []
        def disable():
            try:
                runtime.accept(self.principal,'admin','auto-chill',dict(minutes=0,expected_revision=0),'off')
            except BaseException as error:
                errors.append(error)
            finally:
                completed.set()
        with runtime.lifecycle._lock():
            thread = threading.Thread(target=disable)
            thread.start()
            self.assertTrue(completed.wait(1))
        thread.join()
        self.assertEqual(errors,[])

    def test_http_routes_strict_framing_roles_and_no_get_effects(self):
        import http.client
        import http.server
        import threading
        from workbench_server import WorkbenchHandler
        with patch('request_auth.authenticate_principal', return_value=self.principal), patch.object(runtime,'status',return_value={'warehouse':'LOCAL'}) as status, patch.object(runtime,'accept',return_value={'operation_id':'op','phase':'ACCEPTED'}) as accept:
            server = http.server.ThreadingHTTPServer(('127.0.0.1',0),WorkbenchHandler)
            thread = threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                def request(method,path,roles=('admin',),body=None,extra=()):
                    connection = http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2)
                    connection.putrequest(method,path)
                    for role in roles: connection.putheader('X-Shirokuma-Role',role)
                    for key,value in extra: connection.putheader(key,value)
                    if body is not None:
                        connection.putheader('Content-Type','application/json')
                        connection.putheader('Content-Length',str(len(body)))
                    connection.endheaders(body)
                    response = connection.getresponse()
                    response.read()
                    code = response.status
                    connection.close()
                    return code
                self.assertEqual(request('GET',runtime.BASE),200)
                accept.assert_not_called()
                self.assertEqual(request('GET',runtime.BASE,roles=('admin','admin')),400)
                self.assertEqual(request('GET',runtime.BASE+'?x=1'),400)
                self.assertEqual(request('GET',runtime.BASE,extra=(('Content-Length','1'),)),400)
                self.assertEqual(request('POST',runtime.BASE+'/wake',body=b'{}'),400)
                accept.assert_not_called()
                self.assertEqual(request('POST',runtime.BASE+'/wake',body=b'{}',extra=(('Idempotency-Key','key'),)),202)
                accept.assert_called_once()
            finally:
                server.shutdown(); server.server_close(); thread.join()


    def test_other_live_owner_phase_and_orphan_phase(self):
        import json
        with patch.object(runtime.os,'kill') as alive:
            metadata = dict(process='other',pid=os.getpid()+10)
            row = dict(id='op',actor='owner',action='WAREHOUSE_WAKE',state='RUNNING',before_after=json.dumps(metadata),timestamps='{}')
            self.assertEqual(runtime._operation(row)['phase'],'RUNNING')
            alive.side_effect=ProcessLookupError()
            self.assertEqual(runtime._operation(row)['phase'],'UNKNOWN')

    def test_invalid_action_cannot_be_accepted(self):
        with self.assertRaises(ValueError): runtime.accept(self.principal,'admin','arbitrary',{},'key')

    def test_failed_policy_write_is_unknown_without_blocking_compute_or_replaying(self):
        import control_store
        with patch.object(runtime, '_set_policy', side_effect=OSError('private path')) as write:
            with self.assertRaisesRegex(Exception, 'POLICY_UNAVAILABLE'):
                runtime.accept(self.principal, 'admin', 'auto-chill', dict(minutes=0, expected_revision=0), 'failed-policy')
            ack = runtime.accept(self.principal, 'admin', 'auto-chill', dict(minutes=0, expected_revision=0), 'failed-policy')
            write.assert_called_once()
        self.assertEqual(runtime.operation(ack['operation_id'])['phase'], 'UNKNOWN')
        with control_store.connect() as db:
            self.assertEqual(runtime.lifecycle.runtime_blockers(db), ())

    def test_status_identifies_physical_operation_and_invalidates_asleep_proof(self):
        import json
        import control_store
        with control_store.connect() as db:
            for identifier, action, policy in (('physical-op', 'WAREHOUSE_WAKE', False), ('setting-op', 'WAREHOUSE_AUTO_CHILL', True)):
                db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (identifier, 'owner', action, '["LOCAL"]', None, 'RUNNING', '[]', None,
                     json.dumps(dict(process=runtime._PROCESS, pid=os.getpid(), policy_change=policy)), '{}'))
        proof = runtime.lifecycle._proof_path()
        config = dict(foundation_generation_id='registered-generation', foundation_revision=3)
        with patch('runtime_profile.logical_config', return_value=config), \
             patch.object(runtime.lifecycle, '_LOCAL_PROOF_ID', None), \
             patch.object(runtime.lifecycle, 'blockers', return_value={}), \
             patch.object(runtime.wr, '_logical_foundation', return_value={}), \
             patch.object(runtime.wr, '_logical_status', return_value=dict(state='chilled', replicas=0, ready_replicas=0)):
            with runtime.lifecycle._lock():
                runtime.lifecycle._save_proof()
            result = runtime.status(self.principal, 'bi_viewer')
            self.assertIsNone(runtime.lifecycle._LOCAL_PROOF_ID)
        self.assertEqual((result['operation_id'], result['operation_action'], result['operation_phase']),
                         ('physical-op', 'WAREHOUSE_WAKE', 'RUNNING'))
        self.assertFalse(result['engine_verified'])
        self.assertFalse(proof.exists())
        self.assertNotIn('process', result)
        self.assertNotIn('actor', result)

    def test_paused_nonactive_status_preserves_new_wake_epoch_and_local_cache(self):
        import threading
        from contextlib import ExitStack
        lifecycle = runtime.lifecycle
        config = dict(foundation_generation_id='registered-generation', foundation_revision=3)
        # Both a stale epoch and an absent capture must preserve a later Wake.
        for previous in (True, False):
            with self.subTest(previous=previous), ExitStack() as guards:
                guards.enter_context(patch('runtime_profile.logical_config', return_value=config))
                guards.enter_context(patch.object(lifecycle, '_LOCAL_PROOF_ID', None))
                guards.enter_context(patch.object(lifecycle, 'blockers', return_value={}))
                guards.enter_context(patch.object(runtime.wr, '_logical_foundation', return_value=config))
                lifecycle._proof_path().unlink(missing_ok=True)
                if previous:
                    with lifecycle._lock():
                        lifecycle._save_proof()
                observed, resume, completed = threading.Event(), threading.Event(), threading.Event()
                results, errors = [], []
                def physical(*args):
                    observed.set()
                    if not resume.wait(3):
                        raise AssertionError('paused status was not released')
                    return dict(state='chilled', replicas=0, ready_replicas=0)
                physical_get = guards.enter_context(patch.object(runtime.wr, '_logical_status', side_effect=physical))
                def status():
                    try:
                        results.append(runtime.status(self.principal, 'bi_viewer'))
                    except BaseException as error:
                        errors.append(error)
                    finally:
                        completed.set()
                thread = threading.Thread(target=status)
                thread.start()
                try:
                    self.assertTrue(observed.wait(1))
                    with lifecycle._lock():
                        lifecycle._save_proof()
                    fresh, local = lifecycle._proof_path().read_bytes(), lifecycle._LOCAL_PROOF_ID
                    resume.set()
                    self.assertTrue(completed.wait(1))
                finally:
                    resume.set()
                    thread.join(timeout=4)
                self.assertEqual(errors, [])
                self.assertFalse(results[0]['engine_verified'])
                self.assertEqual(lifecycle._proof_path().read_bytes(), fresh)
                self.assertEqual(lifecycle._LOCAL_PROOF_ID, local)
                physical_get.assert_called_once()

    def test_nonactive_status_returns_immediately_when_physical_lock_busy(self):
        import threading
        lifecycle = runtime.lifecycle
        config = dict(foundation_generation_id='registered-generation', foundation_revision=3)
        with patch('runtime_profile.logical_config', return_value=config), \
             patch.object(lifecycle, '_LOCAL_PROOF_ID', None), \
             patch.object(lifecycle, 'blockers', return_value={}), \
             patch.object(runtime.wr, '_logical_foundation', return_value=config), \
             patch.object(runtime.wr, '_logical_status', return_value=dict(state='chilled', replicas=0, ready_replicas=0)) as physical, \
             patch.object(runtime.admission, 'lifecycle_gate', side_effect=AssertionError('status waited on SH gate')):
            completed, errors = threading.Event(), []
            def status():
                try:
                    self.assertFalse(runtime.status(self.principal, 'bi_viewer')['engine_verified'])
                except BaseException as error:
                    errors.append(error)
                finally:
                    completed.set()
            with lifecycle._lock():
                lifecycle._save_proof()
                before, local = lifecycle._proof_path().read_bytes(), lifecycle._LOCAL_PROOF_ID
                thread = threading.Thread(target=status)
                thread.start()
                self.assertTrue(completed.wait(1), 'status waited for physical lock')
                self.assertEqual(lifecycle._proof_path().read_bytes(), before)
                self.assertEqual(lifecycle._LOCAL_PROOF_ID, local)
            thread.join(timeout=3)
            self.assertEqual(errors, [])
            physical.assert_called_once()

    def test_monitor_disabled_recently_active_blocked_and_asleep_ticks_create_no_operations(self):
        import control_store
        with patch.object(runtime.wr, '_load_auto_chill_minutes', return_value=0), \
             patch.object(runtime.wr, '_logical_status') as physical:
            for _ in range(3):
                runtime.monitor_chill()
            physical.assert_not_called()
        with patch.object(runtime.wr, '_load_auto_chill_minutes', return_value=1), \
             patch.object(runtime.wr, 'read_last_activity', return_value=0), \
             patch.object(runtime.wr.time, 'time', return_value=1000), \
             patch.object(runtime.wr, 'check_lifecycle'), \
             patch.object(runtime.wr, '_logical_foundation', return_value={}), \
             patch.object(runtime.wr, '_logical_status', return_value=dict(state='chilled')):
            for _ in range(3):
                result = runtime.monitor_chill()
                self.assertEqual(result['state'], 'unchanged')
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM operations').fetchone()[0], 0)

    def test_paused_monitor_preserves_new_wake_epoch_without_journal_churn(self):
        import control_store
        import threading
        lifecycle = runtime.lifecycle
        config = dict(foundation_generation_id='registered-generation', foundation_revision=3)
        observed, resume, completed = threading.Event(), threading.Event(), threading.Event()
        results, errors = [], []
        def physical(*args):
            observed.set()
            if not resume.wait(3):
                raise AssertionError('paused monitor was not released')
            return dict(state='chilled')
        def monitor():
            try:
                results.append(runtime.monitor_chill())
            except BaseException as error:
                errors.append(error)
            finally:
                completed.set()
        with patch('runtime_profile.logical_config', return_value=config), \
             patch.object(lifecycle, '_LOCAL_PROOF_ID', None), \
             patch.object(runtime.wr, '_load_auto_chill_minutes', return_value=1), \
             patch.object(runtime.wr, 'read_last_activity', return_value=0), \
             patch.object(runtime.wr.time, 'time', return_value=1000), \
             patch.object(runtime.wr, 'check_lifecycle'), \
             patch.object(runtime.wr, '_logical_foundation', return_value=config), \
             patch.object(runtime.wr, '_logical_status', side_effect=physical) as physical_get, \
             patch.object(lifecycle, 'chill', side_effect=AssertionError('noop dispatched Chill')):
            with lifecycle._lock():
                lifecycle._save_proof()
            previous = lifecycle.shared_engine_proof()
            thread = threading.Thread(target=monitor)
            thread.start()
            try:
                self.assertTrue(observed.wait(1))
                # Wake completes while the monitor retains its stale observation.
                with lifecycle._lock():
                    lifecycle._save_proof()
                fresh, local = lifecycle._proof_path().read_bytes(), lifecycle._LOCAL_PROOF_ID
                self.assertNotEqual(lifecycle.shared_engine_proof()['epoch'], previous['epoch'])
                resume.set()
                self.assertTrue(completed.wait(1))
            finally:
                resume.set()
                thread.join(timeout=4)
            self.assertEqual(errors, [])
            self.assertEqual(results, [dict(state='unchanged', result='already-stopped')])
            self.assertEqual(lifecycle._proof_path().read_bytes(), fresh)
            self.assertEqual(lifecycle._LOCAL_PROOF_ID, local)
            physical_get.assert_called_once()
            with control_store.connect() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM operations').fetchone()[0], 0)

    def test_monitor_cleans_unchanged_stale_proof_without_journal_churn(self):
        import control_store
        lifecycle = runtime.lifecycle
        config = dict(foundation_generation_id='registered-generation', foundation_revision=3)
        with patch('runtime_profile.logical_config', return_value=config), \
             patch.object(lifecycle, '_LOCAL_PROOF_ID', None), \
             patch.object(runtime.wr, '_load_auto_chill_minutes', return_value=1), \
             patch.object(runtime.wr, 'read_last_activity', return_value=0), \
             patch.object(runtime.wr.time, 'time', return_value=1000), \
             patch.object(runtime.wr, 'check_lifecycle'), \
             patch.object(runtime.wr, '_logical_foundation', return_value=config), \
             patch.object(runtime.wr, '_logical_status', return_value=dict(state='chilled')) as physical_get, \
             patch.object(lifecycle, 'chill', side_effect=AssertionError('noop dispatched Chill')):
            with lifecycle._lock():
                lifecycle._save_proof()
            for _ in range(3):
                self.assertEqual(runtime.monitor_chill(), dict(state='unchanged', result='already-stopped'))
                self.assertFalse(lifecycle._proof_path().exists())
                self.assertIsNone(lifecycle._LOCAL_PROOF_ID)
            self.assertEqual(physical_get.call_count, 3)
            with control_store.connect() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM operations').fetchone()[0], 0)

    def test_status_reads_other_process_shared_proof_without_adopting_local_readiness(self):
        import json
        import subprocess
        from contextlib import ExitStack
        config=dict(foundation_generation_id='registered-generation', foundation_revision=3)
        script="""
import json,sys
sys.path.insert(0,sys.argv[1])
import runtime_profile
import warehouse_lifecycle as lifecycle
runtime_profile.logical_config=lambda:json.loads(sys.argv[2])
lifecycle._save_proof()
print(json.dumps(lifecycle.shared_engine_proof()))
"""
        child=subprocess.run([sys.executable,'-c',script,str(Path(runtime.lifecycle.__file__).parent),json.dumps(config)],
            capture_output=True,text=True,check=True)
        shared=json.loads(child.stdout)
        with ExitStack() as guards:
            guards.enter_context(patch('runtime_profile.logical_config',return_value=config))
            guards.enter_context(patch.object(runtime.lifecycle,'_LOCAL_PROOF_ID',None))
            guards.enter_context(patch.object(runtime.lifecycle,'blockers',return_value={}))
            guards.enter_context(patch.object(runtime.wr,'_logical_foundation',return_value=config))
            guards.enter_context(patch.object(runtime.wr,'_logical_status',return_value=dict(state='active',replicas=2,ready_replicas=2)))
            for owner,name in ((runtime.lifecycle,'_adopt_proof'),(runtime.lifecycle,'_lock'),
                    (runtime.wr,'_logical_transition')):
                guards.enter_context(patch.object(owner,name,side_effect=AssertionError('status performed readiness work')))
            guards.enter_context(patch('logical_creations.ensure_active_provider',side_effect=AssertionError('status repaired Provider')))
            guards.enter_context(patch('logical_provider.Provider._sql',side_effect=AssertionError('status submitted SQL')))
            self.assertTrue(runtime.lifecycle.requires_wake())
            result=runtime.status(self.principal,'bi_viewer')
            self.assertEqual((result['engine_verified'],result['engine_verified_at']),(True,shared['verified_at']))
            self.assertEqual(result['available_actions'],[])
            import http.client
            import http.server
            import threading
            from workbench_server import WorkbenchHandler
            with patch('request_auth.authenticate_principal',return_value=self.principal), \
                 http.server.ThreadingHTTPServer(('127.0.0.1',0),WorkbenchHandler) as server:
                thread=threading.Thread(target=server.serve_forever,daemon=True)
                thread.start()
                connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2)
                try:
                    connection.request('GET',runtime.BASE,headers={'X-Shirokuma-Role':'bi_viewer'})
                    response=connection.getresponse()
                    payload=json.loads(response.read())
                    self.assertEqual(response.status,200)
                    self.assertEqual((payload['engine_verified'],payload['engine_verified_at']),
                                     (True,shared['verified_at']))
                    self.assertEqual(payload['available_actions'],[])
                finally:
                    connection.close()
                    server.shutdown()
                    thread.join(timeout=5)
            self.assertIsNone(runtime.lifecycle._LOCAL_PROOF_ID)
            self.assertTrue(runtime.lifecycle.requires_wake())
            with self.assertRaises(PermissionError):
                runtime.authorize(self.principal,'bi_viewer',platform=True)
            self.assertNotIn('epoch',result)
            self.assertNotIn('generation_id',result)

    def test_status_requires_valid_shared_proof_and_active_physical_observation(self):
        import json
        config=dict(foundation_generation_id='registered-generation',foundation_revision=3)
        valid=dict(generation_id='registered-generation',revision=3,epoch='remote-epoch',
                   verified_at='2026-10-29T00:00:00+00:00')
        malformed=[None,'{','[]',json.dumps({}),json.dumps(dict(valid,generation_id='other')),
                   json.dumps(dict(valid,revision=4)),json.dumps(dict(valid,revision=True)),
                   json.dumps({k:v for k,v in valid.items() if k!='verified_at'}),
                   json.dumps(dict(valid,verified_at=None)),json.dumps(dict(valid,verified_at='not-a-time')),
                   json.dumps(dict(valid,verified_at='2026-10-29T00:00:00')),json.dumps(dict(valid,epoch=''))]
        path=runtime.lifecycle._proof_path()
        with patch('runtime_profile.logical_config',return_value=config), \
             patch.object(runtime.lifecycle,'blockers',return_value={}), \
             patch.object(runtime.wr,'_logical_foundation',return_value=config), \
             patch.object(runtime.wr,'_logical_status',return_value=dict(state='active',replicas=2,ready_replicas=2)):
            for body in malformed:
                if body is None:path.unlink(missing_ok=True)
                else:path.write_text(body)
                result=runtime.status(self.principal,'admin')
                self.assertFalse(result['engine_verified'])
                self.assertIsNone(result['engine_verified_at'])
                self.assertEqual(result['available_actions'],['wake','chill','auto-chill','reconcile'])
            path.write_text(json.dumps(valid))
            with patch.object(runtime.wr,'_logical_status',return_value=dict(state='chilled',replicas=0,ready_replicas=0)):
                result=runtime.status(self.principal,'admin')
            self.assertFalse(result['engine_verified'])
            self.assertIsNone(result['engine_verified_at'])

    def test_manual_run_classifies_known_rejection_and_actual_dispatch_uncertainty(self):
        import json
        import control_store
        from warehouse_operations import OperationError
        for identifier, code, dispatched in (('busy', 'WAREHOUSE_BUSY', False),
                ('proof', 'FOUNDATION_NOT_READY', False), ('uncertain', 'WAREHOUSE_STOP_UNKNOWN', True)):
            with control_store.connect() as db:
                db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (identifier, 'owner', 'WAREHOUSE_CHILL', '["LOCAL"]', None, 'ACCEPTED', '[]', None,
                     json.dumps(dict(process=runtime._PROCESS, pid=os.getpid(), stopping_barrier=True)), '{}'))
            def stop(**kwargs):
                if dispatched:
                    runtime.lifecycle.mark_physical_started()
                raise OperationError(code, 409)
            with patch.object(runtime.lifecycle, 'chill', side_effect=stop):
                runtime._run(identifier, 'chill', self.principal, 'admin')
            self.assertEqual(runtime.operation(identifier)['phase'], 'UNKNOWN' if dispatched else 'FAILED')
            with control_store.connect() as db:
                metadata = json.loads(db.execute('SELECT before_after FROM operations WHERE id=?', (identifier,)).fetchone()[0])
            self.assertIs(metadata['stopping_barrier'], dispatched)


if __name__ == '__main__': unittest.main()
