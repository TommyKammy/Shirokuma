import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import control_store as store
import warehouse_admission as admission
import warehouse_lifecycle as lifecycle
import logical_creations as lifecycle_creations
ACTUAL_ENSURE_ACTIVE_PROVIDER = lifecycle_creations.ensure_active_provider


def metrics(all_count,expired):
    return ("# HELP unrelated Gauge\n# TYPE unrelated gauge\nunrelated{node=\"local\"} 0.0\n"
        + lifecycle._METRIC_ALL + ' ' + str(all_count) + '\n'
        + lifecycle._METRIC_EXPIRED + ' ' + str(expired) + '\n# EOF\n').encode()


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        env = patch.dict(os.environ, SHIROKUMA_STATE_DIR=temporary.name)
        env.start(); self.addCleanup(env.stop)
        patches = [patch.object(lifecycle, 'applicable', return_value=True),
                   patch('warehouse_resident._logical_foundation', return_value={'proof':'saved'}),
                   patch('warehouse_resident._logical_wake_expected', return_value={'saved':True}),
                   patch('warehouse_resident._logical_status', return_value={'state':'chilled'}),
                   patch('warehouse_resident._logical_stopped_ready'),
                   patch('logical_creations.ensure_active_provider'),
                   patch('logical_creations.compute_blockers',return_value={'requests':0,'effects':0,'roots':0}),
                   patch.object(lifecycle, '_save_proof')]
        for p in patches:
            p.start(); self.addCleanup(p.stop)

    def test_revoked_authority_has_no_physical_effect(self):
        def reject(): raise PermissionError()
        with patch('warehouse_resident._logical_transition') as physical:
            with self.assertRaises(PermissionError):
                lifecycle.ensure_awake(actor='owner', authorize=reject)
            physical.assert_not_called()
        with store.connect() as db:
            self.assertEqual(admission.blockers(db), ())

    def test_wait_has_no_database_write_transaction_and_independent_admission(self):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            parent = admission.admit(db, actor='owner', kind='query', subject_id='query')
        def physical(action, size, foundation, db, **kwargs):
            self.assertFalse(db.in_transaction)
            with store.connect() as writer:
                writer.execute('BEGIN IMMEDIATE')
                rows = admission.blockers(writer)
                wake = next(row for row in rows if row['kind'] == 'wake')
                self.assertIsNone(wake['parent_id'])
            return {'state':'active'}
        with admission.accepted(parent), patch('warehouse_resident._logical_transition', side_effect=physical):
            self.assertEqual(lifecycle.ensure_awake(actor='owner', authorize=lambda: None)['state'], 'active')

    def test_uncertain_wake_is_not_retried(self):
        def fail(*args, **kwargs):
            kwargs['mutation_started']()
            raise RuntimeError()
        with patch('warehouse_resident._logical_transition', side_effect=fail) as physical:
            with self.assertRaises(RuntimeError): lifecycle.ensure_awake(actor='owner', authorize=lambda: None)
            with self.assertRaisesRegex(Exception, 'RECOVERY_REQUIRED'):
                lifecycle.ensure_awake(actor='owner', authorize=lambda: None)
            self.assertEqual(physical.call_count, 1)

    def test_cancelled_waiter_never_claims(self):
        with patch('warehouse_resident._logical_transition') as physical:
            with self.assertRaisesRegex(Exception, 'CANCELED'):
                lifecycle.ensure_awake(actor='owner', authorize=lambda: None, cancelled=lambda: True)
            physical.assert_not_called()

    def test_cancel_after_admission_before_physical_withdraws_waiter(self):
        checks = []
        def authorize():
            checks.append(1)
        with patch('warehouse_resident._logical_transition') as physical:
            with self.assertRaisesRegex(Exception, 'CANCELED'):
                lifecycle.ensure_awake(actor='owner', authorize=authorize,
                    cancelled=lambda: len(checks) >= 4)
            physical.assert_not_called()
        with store.connect() as db:
            self.assertEqual(admission.blockers(db), ())
            self.assertEqual(db.execute("SELECT state FROM warehouse_admissions WHERE kind='wake'").fetchone()[0], 'CANCELED')

    def test_reconcile_rejects_unknown_query_before_any_observation_or_effect(self):
        import json
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            query = admission.admit(db, actor='owner', kind='query', subject_id='unresolved-query')
            wake = admission.admit(db, actor='owner', kind='wake', subject_id='lost-wake')
            admission.finish(db, query, state='UNKNOWN')
            admission.finish(db, wake, state='UNKNOWN')
        with patch('warehouse_resident._logical_foundation',side_effect=AssertionError('foundation observation')),patch('warehouse_resident.invalidate_persistent_trino',side_effect=AssertionError('SQL invalidation')),patch('logical_creations.ensure_active_provider',side_effect=AssertionError('Provider repair')):
            with self.assertRaisesRegex(Exception,'^WAREHOUSE_BUSY$'):
                lifecycle.reconcile(authorize=lambda:None)
        with store.connect() as db:
            self.assertEqual({r['id'] for r in admission.blockers(db)},{query.id,wake.id})

    def test_reconcile_wake_only_observes_orphan_runtime_without_sql_replay(self):
        import json
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            wake=admission.admit(db,actor='owner',kind='wake',subject_id='lost-wake')
            admission.finish(db,wake,state='UNKNOWN')
            db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?,?,?,?)',
                ('lost-chill','owner','WAREHOUSE_CHILL','["LOCAL"]',None,'RUNNING','[]',None,json.dumps({'process':'lost'}),'{}'))
        def metadata(db):
            self.assertTrue(db.in_transaction)
            self.assertEqual(admission._held.mode,'exclusive')
            return {'requests':0,'effects':0,'roots':0}
        def physical(foundation,db,expected):
            self.assertFalse(db.in_transaction)
            with store.connect() as writer:writer.execute('BEGIN IMMEDIATE')
        with patch('logical_creations.compute_blockers',side_effect=metadata),patch('warehouse_resident._logical_stopped_ready',side_effect=physical) as verify,patch('warehouse_resident._logical_transition') as mutation:
            result=lifecycle.reconcile(authorize=lambda:None)
            verify.assert_called_once();mutation.assert_not_called()
        self.assertEqual(result,dict(state='chilled',engine_verified=False))
        with store.connect() as db:
            self.assertEqual(admission.blockers(db),())
            self.assertEqual(db.execute("SELECT state FROM operations WHERE id='lost-chill'").fetchone()[0],'RECONCILED')

    def test_creation_blocks_stop(self):
        with patch('logical_creations.enabled', return_value=True), patch('runtime_profile.logical_config', return_value={'warehouse_id':'LOCAL'}), patch('logical_creations.compute_blockers', return_value={'requests':1,'effects':0,'roots':0}):
            with self.assertRaises(admission.AdmissionConflict):
                import warehouse_resident
                warehouse_resident.check_lifecycle()


    def test_cross_process_cold_wake_scales_once(self):
        import subprocess
        script = """
import os,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import warehouse_lifecycle as lifecycle
import warehouse_resident as wr
import logical_creations
marker = Path(os.environ['SHIROKUMA_STATE_DIR'])/'ready'
count = marker.with_name('effects')
lifecycle.applicable=lambda: True
lifecycle.requires_wake=lambda: not marker.exists()
lifecycle._save_proof=lambda: marker.write_text('ready')
lifecycle._shared_proof=lambda: {'epoch':'shared'} if marker.exists() else None
wr._logical_foundation=lambda db: {'proof':'saved'}
wr._logical_wake_expected=lambda f,db: {'saved':True}
wr._logical_status=lambda s,f: {'state':'active' if marker.exists() else 'chilled'}
def ensure(*,force_rebind=False):
    assert force_rebind is True
    return object()
logical_creations.ensure_active_provider=ensure
def transition(*args,**kwargs):
    with count.open('a') as stream: stream.write('scale\\n')
    time.sleep(.15)
    return {'state':'active'}
wr._logical_transition=transition
lifecycle.ensure_awake(actor='owner',authorize=lambda: None)
"""
        processes = [subprocess.Popen([sys.executable,'-c',script,str(Path(lifecycle.__file__).parent)],stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(2)]
        for process in processes:
            output,error = process.communicate(timeout=10)
            self.assertEqual(process.returncode,0,error.decode())
        self.assertEqual((store.root()/'effects').read_text().count('scale'),1)

    def test_orphan_runtime_operation_blocks_automatic_wake(self):
        import json
        with store.connect() as db:
            db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?,?,?,?)',('orphan','owner','WAREHOUSE_CHILL','["LOCAL"]',None,'RUNNING','[]',None,json.dumps({'process':'lost'}),'{}'))
        with patch('warehouse_resident._logical_transition') as physical:
            with self.assertRaisesRegex(Exception,'RECOVERY_REQUIRED'):
                lifecycle.ensure_awake(actor='owner',authorize=lambda: None)
            physical.assert_not_called()


    def test_revoked_after_claim_before_physical_has_no_unknown(self):
        calls = []
        def authorize():
            calls.append(1)
            if len(calls) == 4: raise PermissionError()
        with patch('warehouse_resident._logical_transition') as physical:
            with self.assertRaises(PermissionError):
                lifecycle.ensure_awake(actor='owner',authorize=authorize)
            physical.assert_not_called()
        with store.connect() as db:
            self.assertEqual(admission.blockers(db),())
            self.assertEqual(db.execute("SELECT state FROM warehouse_admissions WHERE kind='wake'").fetchone()[0],'FAILED')

    def test_active_engine_queries_and_failed_observation_never_stop(self):
        from unittest.mock import Mock
        for result in (metrics(4,2),RuntimeError('observation unavailable')):
            provider = Mock()
            provider._provisioner.return_value={'username':'provisioner','password':'private'}
            if isinstance(result,Exception): provider._request.side_effect=result
            else: provider._request.return_value=result
            with patch('warehouse_resident.check_lifecycle'), patch('warehouse_resident._logical_status',return_value={'state':'active'}), patch('logical_creations.ensure_active_provider',return_value=provider), patch('warehouse_resident._logical_transition') as physical:
                with self.assertRaises(Exception): lifecycle.chill(authorize=lambda: None)
                physical.assert_not_called()
                self.assertEqual(provider._request.call_args.args,('trino-local','GET','/metrics'))
                provider._sql.assert_not_called()


    def test_live_accepted_manual_wake_does_not_block_query_wake(self):
        import json
        import warehouse_runtime_routes as runtime
        with store.connect() as db:
            db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?,?,?,?)',('manual','owner','WAREHOUSE_WAKE','["LOCAL"]',None,'ACCEPTED','[]',None,json.dumps(dict(process=runtime._PROCESS,pid=os.getpid())),'{}'))
        with patch('warehouse_resident._logical_transition',return_value={'state':'active'}) as physical:
            self.assertEqual(lifecycle.ensure_awake(actor='owner',authorize=lambda: None)['state'],'active')
            physical.assert_called_once()

    def test_direct_transition_takes_physical_lock_before_short_write_transaction(self):
        from contextlib import contextmanager
        observed=[]
        @contextmanager
        def lock(*args):
            with store.connect() as writer:
                writer.execute('BEGIN IMMEDIATE')
                observed.append('lock-before-write')
            yield
        def physical(action,size,foundation,db):
            self.assertFalse(db.in_transaction)
            return {'state':'active'}
        with patch('logical_creations.enabled',return_value=True),patch.object(lifecycle,'_lock',side_effect=lock),patch('warehouse_resident.check_lifecycle'),patch('warehouse_resident._logical_transition',side_effect=physical):
            import warehouse_resident
            warehouse_resident.transition('wake','LOCAL')
        self.assertEqual(observed,['lock-before-write'])


    def test_active_and_unknown_parent_admissions_prevent_chill(self):
        for state in ('ACTIVE','UNKNOWN'):
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                handle=admission.admit(db,actor='owner',kind='job',subject_id=state)
                if state=='UNKNOWN': admission.finish(db,handle,state='UNKNOWN')
            with patch('logical_creations.enabled',return_value=True),patch('runtime_profile.logical_config',return_value={'warehouse_id':'LOCAL'}),patch('warehouse_resident._logical_transition') as physical:
                with self.assertRaises(admission.AdmissionConflict): lifecycle.chill(authorize=lambda:None)
                physical.assert_not_called()
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                admission.finish(db,handle,state='FINISHED',evidence_ref='test:observed')

    def test_cancel_after_physical_start_does_not_cancel_shared_wake(self):
        started=threading.Event()
        release=threading.Event()
        canceled=threading.Event()
        ready=threading.Event()
        results=[]
        errors=[]
        def physical(*args,**kwargs):
            started.set()
            if not release.wait(2): raise RuntimeError('test release missing')
            ready.set()
            return {'state':'active'}
        def wait(cancelled=None):
            try: results.append(lifecycle.ensure_awake(actor='owner',authorize=lambda:None,cancelled=cancelled))
            except BaseException as error: errors.append(error)
        with patch.object(lifecycle,'_shared_proof',side_effect=lambda:{'epoch':'shared'} if ready.is_set() else None),patch('warehouse_resident._logical_status',side_effect=lambda *args:{'state':'active' if ready.is_set() else 'chilled'}),patch.object(lifecycle,'requires_wake',side_effect=lambda:not ready.is_set()),patch('warehouse_resident._logical_transition',side_effect=physical) as transition:
            first=threading.Thread(target=wait,args=(canceled.is_set,))
            second=threading.Thread(target=wait)
            first.start()
            self.assertTrue(started.wait(1))
            second.start()
            canceled.set()
            release.set()
            first.join(3);second.join(3)
            self.assertFalse(first.is_alive() or second.is_alive())
            self.assertEqual(errors,[])
            self.assertEqual(len(results),2)
            transition.assert_called_once()


    def test_stop_barrier_blocks_root_admission_between_final_check_and_scale(self):
        from unittest.mock import Mock
        import warehouse_resident as wr
        provider=Mock()
        provider._provisioner.return_value={'username':'provisioner','password':'private'}
        provider._request.return_value=metrics(7,7)
        seen=[]
        def physical(*args,**kwargs):
            with store.connect() as db:
                self.assertFalse(db.in_transaction)
                self.assertEqual(len(admission.stopping_barriers(db)),1)
            outcomes=[]
            def late_admission():
                try:
                    with store.connect() as writer:
                        writer.execute('BEGIN IMMEDIATE')
                        admission.admit(writer,actor='late',kind='query',subject_id='late-'+str(len(seen)))
                    outcomes.append('unexpected admission')
                except BaseException as error:
                    outcomes.append(error)
            late=threading.Thread(target=late_admission)
            late.start();late.join(2)
            self.assertFalse(late.is_alive(), 'SQLite writer held across physical dispatch')
            self.assertEqual(len(outcomes),1)
            self.assertIsInstance(outcomes[0],admission.AdmissionDenied)
            self.assertFalse(lifecycle.physical_started())
            kwargs['mutation_started']()
            self.assertTrue(lifecycle.physical_started())
            seen.append(True)
            return {'state':'chilled'}
        with patch('warehouse_resident._logical_status',return_value={'state':'active'}),patch('logical_creations.ensure_active_provider',return_value=provider),patch('warehouse_resident._logical_transition',side_effect=physical),patch('logical_creations.enabled',return_value=True),patch('logical_creations.compute_blockers',return_value={'requests':0,'effects':0,'roots':0}),patch('runtime_profile.logical_config',return_value={'warehouse_id':'LOCAL'}),patch('warehouse_resident._load_auto_chill_minutes',return_value=1),patch('warehouse_resident.read_last_activity',return_value=0):
            lifecycle.chill(authorize=lambda:None,automatic=True)
            wr.transition('chill','LOCAL')
        self.assertEqual(len(seen),2)
        with store.connect() as db:
            self.assertEqual(admission.stopping_barriers(db),())

    def test_stop_preflight_failure_failed_and_dispatch_failure_retains_barrier(self):
        from unittest.mock import Mock
        provider=Mock(); provider._provisioner.return_value={'username':'provisioner','password':'private'}; provider._request.return_value=metrics(7,7)
        def failure(*args,**kwargs):
            if dispatch: kwargs['mutation_started']()
            raise RuntimeError('failure')
        for dispatch in (False,True):
            with patch('warehouse_resident.check_lifecycle'),patch('warehouse_resident._logical_status',return_value={'state':'active'}),patch('logical_creations.ensure_active_provider',return_value=provider),patch('warehouse_resident._logical_transition',side_effect=failure):
                with self.assertRaises(Exception): lifecycle.chill(authorize=lambda:None)
            with store.connect() as db:
                row=db.execute('SELECT state,before_after FROM operations ORDER BY rowid DESC LIMIT 1').fetchone()
                self.assertEqual(row['state'],'UNKNOWN' if dispatch else 'FAILED')
                self.assertEqual(bool(admission.stopping_barriers(db)),dispatch)

    def test_warm_path_has_no_physical_lock_probe_or_provider_and_keeps_parent_drain(self):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            handle=admission.admit(db,actor='owner',kind='job',subject_id='parent')
            db.execute("UPDATE warehouse_controls SET state='DRAINING' WHERE warehouse_id='LOCAL'")
        with admission.accepted(handle),patch.object(lifecycle,'requires_wake',return_value=False),patch.object(lifecycle,'_lock',side_effect=AssertionError('physical lock')),patch('warehouse_resident._logical_status',side_effect=AssertionError('kube')),patch('logical_creations.ensure_active_provider',side_effect=AssertionError('provider')):
            self.assertEqual(lifecycle.ensure_awake(actor='owner',authorize=lambda:None)['state'],'active')

    def test_missing_owned_provider_does_not_create_ready_cache(self):
        with patch('warehouse_resident._logical_transition', return_value={'state':'active'}), \
             patch('logical_creations.ensure_active_provider', return_value=None):
            with self.assertRaisesRegex(Exception,'FOUNDATION_NOT_READY'):
                lifecycle.ensure_awake(actor='owner',authorize=lambda:None)
        self.assertFalse(lifecycle._proof_path().exists())
        with store.connect() as db:
            self.assertEqual(admission.blockers(db),())

    def test_stopped_verifier_waits_for_terminating_compute_and_rejects_bad_spec(self):
        import warehouse_resident as wr
        from logical_provider import Rejected
        from unittest.mock import Mock
        provider=Mock()
        provider.bind_stopped_registered.side_effect=[Rejected('FOUNDATION_COMPUTE_NOT_STOPPED'),None]
        # Use the real unopened-Provider verifier, bypassing setUp's physical boundary patch.
        import importlib.util
        spec=importlib.util.spec_from_file_location('stopped_resident',wr.__file__)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with patch('logical_provider.Provider',return_value=provider),patch.object(module,'_logical_foundation',return_value={'proof':'saved'}),patch.object(module.time,'sleep'):
            module._logical_stopped_ready({'proof':'saved'},None,{'saved':True})
            self.assertEqual(provider.bind_stopped_registered.call_count,2)
            provider.close.assert_called_once()
        provider.reset_mock();provider.bind_stopped_registered.side_effect=Rejected('FOUNDATION_SPEC_MISMATCH')
        with patch('logical_provider.Provider',return_value=provider),patch.object(module,'_logical_foundation',return_value={'proof':'saved'}):
            with self.assertRaises(Rejected): module._logical_stopped_ready({'proof':'saved'},None,{'saved':True})
            provider.bind_stopped_registered.assert_called_once()


    def test_real_stopped_binding_rejects_terminating_pod_until_it_disappears(self):
        from tests.test_logical_compute_lifecycle import StoppedProofTests
        from unittest.mock import Mock
        import logical_provider
        import importlib.util
        import warehouse_resident as wr
        fixture=StoppedProofTests('test_exact_stop_binds_metadata_without_tunnels_runtime_readiness_or_mutations')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.h.ghost=True
        def terminating(kind,name,obj):
            if kind=='pod':
                obj['metadata']['deletionTimestamp']='2026-10-29T00:00:00Z'
                obj['status']['phase']='Running'
        fixture.h.change=terminating
        provider=fixture.make_provider()
        actual_type=logical_provider.Provider
        factory=Mock(return_value=provider)
        factory._matches_spec=actual_type._matches_spec
        spec=importlib.util.spec_from_file_location('real_stopped_resident',wr.__file__)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        observed=[]
        def wait(predicate):
            observed.append(predicate())
            self.assertFalse(observed[-1])
            fixture.h.ghost=False
            observed.append(predicate())
            self.assertTrue(observed[-1])
        with patch('logical_provider.Provider',factory),patch.object(module,'_logical_foundation',return_value=fixture.c),patch.object(module,'wait_for',side_effect=wait):
            module._logical_stopped_ready(fixture.c,None,fixture.saved)
        self.assertEqual(observed,[False,True])
        self.assertFalse(provider._opened)
        self.assertEqual(fixture.h.http_calls,[])


    def test_real_transition_marks_dispatch_only_immediately_before_first_scale(self):
        import importlib.util
        import warehouse_resident as wr
        spec=importlib.util.spec_from_file_location('dispatch_resident',wr.__file__)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        events=[]
        foundation={'trino':{'workers':1}}
        def command(*args):
            self.assertEqual(events[-1],'dispatch')
            events.append('scale')
            raise RuntimeError('RPC failed')
        with patch.object(module,'_logical_wake_expected',return_value={'saved':True}),patch.object(module,'_logical_status',return_value={'state':'active','replicas':2}),patch.object(module,'_logical_foundation',return_value=foundation),patch.object(module,'_logical_kubectl',side_effect=command):
            with self.assertRaises(RuntimeError):
                module._logical_transition('chill','LOCAL',foundation,None,
                    authorize=lambda:events.append('authorize'),mutation_started=lambda:events.append('dispatch'))
        self.assertEqual(events,['authorize','dispatch','scale'])
        events.clear()
        with patch.object(module,'_logical_wake_expected',side_effect=ValueError('saved proof rejected')),patch.object(module,'_logical_kubectl') as command:
            with self.assertRaises(ValueError):
                module._logical_transition('chill','LOCAL',foundation,None,mutation_started=lambda:events.append('dispatch'))
            command.assert_not_called()
        self.assertEqual(events,[])


    def test_process_repairs_shared_epoch_once_then_uses_own_warm_cache(self):
        import subprocess
        script="""
import os,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
import warehouse_lifecycle as lifecycle
import warehouse_resident as wr
import logical_creations
import runtime_profile
lifecycle.applicable=lambda:True
runtime_profile.logical_config=lambda:dict(foundation_generation_id='generation',foundation_revision=1)
marker=lifecycle._proof_path()
count=marker.with_name('scale-effects')
repair=marker.with_name('repair-'+sys.argv[2])
wr._logical_foundation=lambda db:{'saved':True}
wr._logical_wake_expected=lambda f,db:{'saved':True}
wr._logical_status=lambda *args:{'state':'active' if marker.exists() else 'chilled'}
def transition(*args,**kwargs):
    with count.open('a') as out:out.write('scale\\n')
    return {'state':'active'}
wr._logical_transition=transition
def ensure(*,force_rebind=False):
    assert force_rebind is True
    with repair.open('a') as out:out.write('repair\\n')
    return object()
logical_creations.ensure_active_provider=ensure
assert lifecycle.requires_wake()
lifecycle.ensure_awake(actor='owner',authorize=lambda:None)
assert not lifecycle.requires_wake()
with patch.object(lifecycle,'_lock',side_effect=AssertionError('warm lock')),patch.object(wr,'_logical_status',side_effect=AssertionError('warm observation')),patch.object(logical_creations,'ensure_active_provider',side_effect=AssertionError('warm repair')):
    lifecycle.ensure_awake(actor='owner',authorize=lambda:None)
"""
        for process in ('first','second'):
            result=subprocess.run([sys.executable,'-c',script,str(Path(lifecycle.__file__).parent),process],capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr.decode())
        self.assertEqual((store.root()/'scale-effects').read_text().count('scale'),1)
        for process in ('first','second'):
            self.assertEqual((store.root()/('repair-'+process)).read_text().count('repair'),1)

    def test_cold_wake_repairs_own_provider_before_publishing_engine_proof(self):
        events = []
        def repair(*, force_rebind=False):
            self.assertIs(force_rebind, True)
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
            events.append('own-full-binding')
            return object()
        with patch('warehouse_resident._logical_transition', side_effect=lambda *args, **kwargs:
                    events.append('physical-wake') or {'state': 'active'}), \
                patch('warehouse_resident.invalidate_persistent_trino', side_effect=lambda:
                    events.append('invalidate-sql')), \
                patch('logical_creations.ensure_active_provider', side_effect=repair), \
                patch.object(lifecycle, '_save_proof', side_effect=lambda: events.append('publish-proof')):
            lifecycle.ensure_awake(actor='owner', authorize=lambda: None)
        self.assertEqual(events, ['physical-wake', 'invalidate-sql', 'own-full-binding', 'publish-proof'])

    def test_adopt_changed_shared_epoch_forces_own_binding_before_adoption(self):
        shared = {'epoch': 'other-process-wake'}
        events = []
        def repair(*, force_rebind=False):
            self.assertIs(force_rebind, True)
            events.append('own-full-binding')
            return object()
        with patch.object(lifecycle, 'requires_wake', return_value=True), \
                patch('warehouse_resident.invalidate_persistent_trino', side_effect=lambda:
                    events.append('invalidate-sql')), \
                patch.object(lifecycle, '_shared_proof', return_value=shared), \
                patch('warehouse_resident._logical_status', return_value={'state': 'active'}), \
                patch('logical_creations.ensure_active_provider', side_effect=repair), \
                patch.object(lifecycle, '_adopt_proof', side_effect=lambda proof:
                    events.append(('adopt', proof))), \
                patch('warehouse_resident._logical_transition') as physical:
            lifecycle.ensure_awake(actor='owner', authorize=lambda: None)
        self.assertEqual(events, ['invalidate-sql', 'own-full-binding', ('adopt', shared)])
        physical.assert_not_called()

    def test_reconcile_invalidates_sql_before_provider_and_new_proof(self):
        events = []
        with patch('warehouse_resident._logical_status', return_value={'state': 'active'}), \
                patch('warehouse_resident._logical_wake_ready'), \
                patch('warehouse_resident.invalidate_persistent_trino', side_effect=lambda:
                    events.append('invalidate-sql')), \
                patch('logical_creations.ensure_active_provider', side_effect=lambda **kwargs:
                    events.append('provider') or object()), \
                patch.object(lifecycle, '_save_proof', side_effect=lambda: events.append('publish-proof')):
            lifecycle.reconcile(authorize=lambda: None)
        self.assertEqual(events, ['invalidate-sql', 'provider', 'publish-proof'])

    def test_warm_ensure_does_not_invalidate_owned_sql(self):
        with patch.object(lifecycle, '_warm_ready', return_value=True), \
                patch('warehouse_resident.invalidate_persistent_trino') as invalidate, \
                patch('logical_creations.ensure_active_provider') as repair:
            lifecycle.ensure_awake(actor='owner', authorize=lambda: None)
        invalidate.assert_not_called()
        repair.assert_not_called()

    def test_provider_repair_failure_never_caches_finished_wake(self):
        for dispatched in (False,True):
            def physical(*args,**kwargs):
                if dispatched:kwargs['mutation_started']()
                return {'state':'active'}
            with patch('warehouse_resident._logical_transition',side_effect=physical),patch('logical_creations.ensure_active_provider',side_effect=RuntimeError('repair failed')):
                with self.assertRaises(RuntimeError): lifecycle.ensure_awake(actor='owner',authorize=lambda:None)
            self.assertFalse(lifecycle._proof_path().exists())
            self.assertIsNone(lifecycle._LOCAL_PROOF_ID)
            with store.connect() as db:
                row=db.execute("SELECT id,state FROM warehouse_admissions WHERE kind='wake' ORDER BY rowid DESC LIMIT 1").fetchone()
                self.assertEqual(row['state'],'UNKNOWN' if dispatched else 'FAILED')
                if dispatched:
                    db.execute("UPDATE warehouse_admissions SET state='FAILED' WHERE id=?",(row['id'],))
                    db.execute("UPDATE operations SET state='FAILED' WHERE state='UNKNOWN'")

    def test_warm_cache_refuses_unknown_runtime_operation(self):
        import json
        with store.connect() as db:
            db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?,?,?,?)',('unknown','owner','WAREHOUSE_WAKE','["LOCAL"]',None,'UNKNOWN','[]',None,'{}','{}'))
        with patch.object(lifecycle,'requires_wake',return_value=False),patch.object(lifecycle,'_lock',side_effect=AssertionError('must reject before lock')):
            with self.assertRaisesRegex(Exception,'RECOVERY_REQUIRED'):
                lifecycle.ensure_awake(actor='owner',authorize=lambda:None)


    def test_metric_aggregate_uses_real_provider_request_and_fresh_private_identity(self):
        import base64
        from unittest.mock import Mock
        from logical_provider import Provider
        provider=object.__new__(Provider)
        provider._gate=Mock()
        provider._owned=Mock()
        provider._tunnels={'trino-local':'https://127.0.0.1:12345'}
        provider._config={'trino':{'ca_file':'synthetic-ca'}}
        provider._provisioner=Mock(side_effect=[{'username':'private-provisioner','password':'first-secret'},
                                             {'username':'private-provisioner','password':'second-secret'}])
        provider._http_transport=Mock(return_value=(200,metrics('5.0','3e0')))
        self.assertEqual(lifecycle._active_engine_queries(provider),2)
        self.assertEqual(lifecycle._active_engine_queries(provider),2)
        self.assertEqual(provider._provisioner.call_count,2)
        for call,password in zip(provider._http_transport.call_args_list,('first-secret','second-secret')):
            self.assertEqual(call.args,('GET','https://127.0.0.1:12345/metrics'))
            self.assertIsNone(call.kwargs['data'])
            self.assertEqual(call.kwargs['ca_file'],'synthetic-ca')
            self.assertEqual(call.kwargs['headers'],{'Authorization':'Basic '+base64.b64encode(('private-provisioner:'+password).encode()).decode()})

    def test_metric_aggregate_covers_nonrunning_registered_work_and_large_realistic_body(self):
        from unittest.mock import Mock
        provider=Mock()
        provider._provisioner.return_value={'username':'private','password':'secret'}
        body=(b'# TYPE trino_execution_name_QueryManager_RunningQueries gauge\n'
              b'trino_execution_name_QueryManager_RunningQueries 0.0\n'
              b'trino_execution_name_QueryManager_QueuedQueries 0.0\n'
              b'trino_execution_name_QueryManager_FinishingQueries 0.0\n'
              b'trino_execution_name_QueryManager_WaitingForPrerequisitesQueries 0.0\n'
              b'unrelated_nonfinite{node="local"} NaN\n'
              b'unrelated_positive_infinity +Inf\n'
              b'unrelated_negative_infinity -Inf\n')
        body+=b''.join(('unrelated_metric_'+str(n)+'{node="local"} 0.0\n').encode() for n in range(30000))
        provider._request.return_value=body+metrics('9007199254740993.0','9007199254740992.0')
        self.assertGreater(len(provider._request.return_value),922000)
        self.assertEqual(lifecycle._active_engine_queries(provider),1)
        provider._sql.assert_not_called()
        provider._request.assert_called_once()

    def test_metric_malformed_unavailable_and_impossible_observations_are_safe(self):
        from unittest.mock import Mock
        malformed=[b'',b'private secret raw\xff',metrics(0,0).decode(),
            metrics(-1,0),metrics(1,-1),metrics(1,2),metrics('NaN',0),metrics('Inf',0),
            metrics('-Inf',0),metrics('1.5',0),metrics('1e30',0),metrics('1e-1',0),
            (lifecycle._METRIC_ALL+' 1\n').encode(),metrics(1,0)+(lifecycle._METRIC_ALL+' 1\n').encode(),
            metrics(1,0)+b'private secret malformed sample\n',
            metrics(1,0)+b'other{label="private\x00value"} 0\n',
            metrics(1,0)+b'other{label="private\x7fvalue"} 0\n',
            metrics(1,0).replace((lifecycle._METRIC_ALL+' 1').encode(),(lifecycle._METRIC_ALL+'{node="local"} 1').encode()),
            metrics(1,0).replace((lifecycle._METRIC_ALL+' 1').encode(),(lifecycle._METRIC_ALL+' 1 123456789').encode()),
            metrics(1,0).replace((lifecycle._METRIC_ALL+' 1').encode(),(lifecycle._METRIC_ALL+' 1 # {trace_id="private"} 1').encode()),
            metrics(1,0)+b'#'+b'x'*16384+b'\n', b'x'*(4*1024*1024),
            metrics(1,0)+(lifecycle._METRIC_EXPIRED+'{node="other"} 0\n').encode()]
        for raw in malformed:
            provider=Mock()
            provider._provisioner.return_value={'username':'private-user','password':'private-password'}
            provider._request.return_value=raw
            with self.subTest(kind=type(raw).__name__,size=len(raw)):
                with self.assertRaises(Exception) as caught:lifecycle._active_engine_queries(provider)
                self.assertEqual(str(caught.exception),'WAREHOUSE_OBSERVATION_FAILED')
                self.assertNotIn('private',str(caught.exception))
                with patch('warehouse_resident.check_lifecycle'), \
                     patch('warehouse_resident._logical_status',return_value={'state':'active'}), \
                     patch('logical_creations.ensure_active_provider',return_value=provider), \
                     patch('warehouse_resident._logical_transition') as physical:
                    with self.assertRaisesRegex(Exception,'^WAREHOUSE_OBSERVATION_FAILED$'):
                        lifecycle.chill(authorize=lambda:None)
                    physical.assert_not_called()
                with store.connect() as db:
                    row=db.execute('SELECT state,before_after FROM operations ORDER BY rowid DESC LIMIT 1').fetchone()
                    self.assertEqual(row['state'],'FAILED')
                    self.assertNotIn('private',row['before_after'])
                    self.assertEqual(admission.stopping_barriers(db),())
        provider._request.side_effect=RuntimeError('private raw response password')
        with self.assertRaisesRegex(Exception,'^WAREHOUSE_OBSERVATION_FAILED$'):
            lifecycle._active_engine_queries(provider)

    def test_positive_metric_count_is_known_busy_without_barrier_or_physical_effect(self):
        from unittest.mock import Mock
        provider=Mock()
        provider._provisioner.return_value={'username':'provisioner','password':'private'}
        provider._request.return_value=metrics(6,5)
        with patch('warehouse_resident.check_lifecycle'),patch('warehouse_resident._logical_status',return_value={'state':'active'}),patch('logical_creations.ensure_active_provider',return_value=provider),patch('warehouse_resident._logical_transition') as physical:
            with self.assertRaisesRegex(Exception,'^WAREHOUSE_BUSY$'):
                lifecycle.chill(authorize=lambda:None)
            physical.assert_not_called()
        with store.connect() as db:
            row=db.execute('SELECT state,before_after FROM operations ORDER BY rowid DESC LIMIT 1').fetchone()
            self.assertEqual(row['state'],'FAILED')
            self.assertEqual(admission.stopping_barriers(db),())
            import json
            self.assertIsNot(json.loads(row['before_after']).get('physical_started'),True)

    def test_zero_metric_difference_reaches_real_scale_with_atomic_stop_barrier(self):
        from unittest.mock import Mock
        import warehouse_resident as wr
        provider=Mock()
        provider._provisioner.return_value={'username':'provisioner','password':'private'}
        provider._request.return_value=metrics(12,12)
        def scale(foundation,*args):
            self.assertEqual(args[0],'scale')
            with store.connect() as db:
                self.assertEqual(len(admission.stopping_barriers(db)),1)
                self.assertTrue(lifecycle.physical_started())
                db.execute('BEGIN IMMEDIATE')
        with patch.object(wr,'check_lifecycle'), \
             patch.object(wr,'_logical_status',side_effect=[{'state':'active','replicas':2},
                 {'state':'active','replicas':2},{'state':'chilled','replicas':0}]), \
             patch('logical_creations.ensure_active_provider',return_value=provider), \
             patch.object(wr,'_logical_kubectl',side_effect=scale) as command, \
             patch.object(wr,'telemetry',return_value='skipped'):
            self.assertEqual(lifecycle.chill(authorize=lambda:None)['state'],'chilled')
        self.assertEqual([call.args[1:] for call in command.call_args_list],
            [('scale','deployment/trino-local','--replicas=0'),
             ('scale','deployment/trino-local-worker','--replicas=0')])
        provider._sql.assert_not_called()
        with store.connect() as db:
            self.assertEqual(admission.stopping_barriers(db),())
            self.assertEqual(db.execute('SELECT state FROM operations ORDER BY rowid DESC LIMIT 1').fetchone()[0],'SUCCEEDED')


    def test_shared_engine_proof_is_read_only_and_keeps_unadopted_local_epoch(self):
        import json
        proof=dict(generation_id='generation',revision=1,epoch='other-process-epoch',
                   verified_at='2026-10-29T12:34:56.123456+09:00')
        lifecycle._proof_path().write_text(json.dumps(dict(proof,private_extra='must-not-return')))
        with patch('runtime_profile.logical_config',return_value=dict(foundation_generation_id='generation',foundation_revision=1)),patch.object(lifecycle,'_LOCAL_PROOF_ID',None),patch.object(lifecycle,'_adopt_proof',side_effect=AssertionError('adopted')),patch.object(lifecycle,'_lock',side_effect=AssertionError('locked')),patch('logical_creations.ensure_active_provider',side_effect=AssertionError('provider')):
            self.assertEqual(lifecycle.shared_engine_proof(),proof)
            self.assertTrue(lifecycle.requires_wake())
            self.assertIsNone(lifecycle._LOCAL_PROOF_ID)
        self.assertEqual(json.loads(lifecycle._proof_path().read_text())['private_extra'],'must-not-return')

    def test_shared_engine_proof_rejects_partial_malformed_and_mismatched_evidence(self):
        import json
        proof=dict(generation_id='generation',revision=1,epoch='epoch',verified_at='2026-10-29T12:34:56+00:00')
        invalid=[None,[],{},True,'invalid-proof']
        for field in proof:
            invalid.append({key:value for key,value in proof.items() if key!=field})
        for field,values in {
            'generation_id':[None,False,1,[],{},'', 'other'],
            'revision':[None,True,False,1.0,'1',[],{},0,-1,2],
            'epoch':[None,True,1,[],{},''],
            'verified_at':[None,True,1,[],{},'', 'not-a-time','2026-10-29',
                '2026-10-29T12:34:56','2026-02-30T12:34:56+00:00','2026-10-29T12:34:56+25:00']}.items():
            invalid.extend(dict(proof,**{field:value}) for value in values)
        with patch('runtime_profile.logical_config',return_value=dict(foundation_generation_id='generation',foundation_revision=1)):
            self.assertIsNone(lifecycle.shared_engine_proof())
            for value in invalid:
                with self.subTest(value=value):
                    lifecycle._proof_path().write_text(json.dumps(value))
                    self.assertIsNone(lifecycle.shared_engine_proof())
            lifecycle._proof_path().write_text('{malformed json')
            self.assertIsNone(lifecycle.shared_engine_proof())
            lifecycle._proof_path().write_text(json.dumps(proof))
            self.assertEqual(lifecycle.shared_engine_proof(),proof)


    def test_reconcile_runtime_worker_fails_busy_for_ordinary_admissions_without_effects(self):
        import json
        from types import SimpleNamespace
        import warehouse_runtime_routes as runtime
        for kind,state in (('query','ACTIVE'),('dataops','ACTIVE'),('job','ACTIVE'),('dataops','UNKNOWN'),('job','UNKNOWN')):
            identifier='reconcile-'+kind+'-'+state
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                handle=admission.admit(db,actor='owner',kind=kind,subject_id=identifier)
                if state=='UNKNOWN':admission.finish(db,handle,state='UNKNOWN')
                db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (identifier,'owner','WAREHOUSE_RECONCILE','["LOCAL"]',None,'ACCEPTED','[]',None,
                     json.dumps(dict(process=runtime._PROCESS,pid=os.getpid())),'{}'))
            lifecycle._proof_path().write_bytes(b'existing shared epoch must survive')
            local=lifecycle._LOCAL_PROOF_ID
            with self.subTest(kind=kind,state=state),patch.object(runtime,'authorize'),patch('warehouse_resident._logical_foundation',side_effect=AssertionError('foundation probe')),patch('warehouse_resident._logical_wake_ready',side_effect=AssertionError('Provider probe')),patch('warehouse_resident.invalidate_persistent_trino',side_effect=AssertionError('SQL invalidation')),patch('logical_creations.ensure_active_provider',side_effect=AssertionError('Provider repair')):
                runtime._run(identifier,'reconcile',SimpleNamespace(principal_id='owner'),'admin')
            with store.connect() as db:
                row=db.execute('SELECT state,before_after FROM operations WHERE id=?',(identifier,)).fetchone()
                self.assertEqual(row['state'],'FAILED')
                self.assertEqual(json.loads(row['before_after'])['error_code'],'WAREHOUSE_BUSY')
                db.execute('BEGIN IMMEDIATE')
                admission.finish(db,handle,state='FINISHED',evidence_ref='test:observed')
            self.assertEqual(lifecycle._proof_path().read_bytes(),b'existing shared epoch must survive')
            self.assertEqual(lifecycle._LOCAL_PROOF_ID,local)

    def test_reconcile_creation_counts_reject_before_provider_or_proof_observation(self):
        for field in ('requests','effects','roots'):
            counts=dict(requests=0,effects=0,roots=0);counts[field]=1
            lifecycle._proof_path().write_bytes(b'existing epoch')
            local=lifecycle._LOCAL_PROOF_ID
            with self.subTest(field=field),patch('logical_creations.compute_blockers',return_value=counts),patch('warehouse_resident._logical_foundation',side_effect=AssertionError('foundation probe')),patch('warehouse_resident._logical_wake_ready',side_effect=AssertionError('Provider probe')),patch('warehouse_resident.invalidate_persistent_trino',side_effect=AssertionError('SQL invalidation')),patch('logical_creations.ensure_active_provider',side_effect=AssertionError('Provider repair')):
                with self.assertRaisesRegex(Exception,'^WAREHOUSE_BUSY$'):
                    lifecycle.reconcile(authorize=lambda:None)
            self.assertEqual(lifecycle._proof_path().read_bytes(),b'existing epoch')
            self.assertEqual(lifecycle._LOCAL_PROOF_ID,local)


class StartupProofTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        for change in (patch.dict(os.environ,SHIROKUMA_STATE_DIR=temporary.name),
                       patch('runtime_profile.logical_config',return_value=dict(foundation_generation_id='generation',foundation_revision=1)),
                       patch.object(lifecycle,'applicable',return_value=True),
                       patch.object(lifecycle,'_LOCAL_PROOF_ID',None),
                       patch('logical_creations.ensure_active_provider',side_effect=lambda **kwargs:__import__('logical_creations')._provider)):
            change.start();self.addCleanup(change.stop)

    def observe(self,provider):
        with patch('logical_creations._provider',provider):
            lifecycle.observe_startup(provider)

    def test_verified_startup_reuses_epoch_and_preserves_peer_on_local_failure(self):
        from unittest.mock import Mock
        provider=Mock()
        provider.compute_state.return_value='ACTIVE'
        provider.bound_alive.return_value=True
        self.observe(provider)
        initial=lifecycle.shared_engine_proof()
        self.assertFalse(lifecycle.requires_wake())
        self.observe(provider)
        self.assertEqual(lifecycle.shared_engine_proof(),initial)
        self.assertFalse(lifecycle.requires_wake())
        provider.compute_state.side_effect=RuntimeError('local observation failed')
        with self.assertRaises(RuntimeError):self.observe(provider)
        self.assertEqual(lifecycle.shared_engine_proof(),initial)
        self.assertTrue(lifecycle.requires_wake())
        provider.compute_state.side_effect=None
        provider.bound_alive.return_value=False
        self.observe(provider)
        self.assertEqual(lifecycle.shared_engine_proof(),initial)
        self.assertTrue(lifecycle.requires_wake())
        provider.bound_alive.side_effect=RuntimeError('local binding failed')
        with self.assertRaises(RuntimeError):self.observe(provider)
        self.assertEqual(lifecycle.shared_engine_proof(),initial)
        self.assertTrue(lifecycle.requires_wake())
        for state in ('STOPPED','TRANSITIONING'):
            lifecycle._save_proof()
            provider.compute_state.return_value=state
            self.observe(provider)
            self.assertIsNone(lifecycle.shared_engine_proof())
            self.assertIsNone(lifecycle._LOCAL_PROOF_ID)

    def test_startup_uses_shared_gate_then_physical_lock_before_compute(self):
        from contextlib import contextmanager
        from unittest.mock import Mock
        import fcntl
        events=[]
        actual_gate=admission.lifecycle_gate
        actual_lock=lifecycle._lock
        @contextmanager
        def gate(*args,**kwargs):
            with actual_gate(*args,**kwargs):
                events.append('shared-gate')
                self.assertFalse(kwargs.get('exclusive',False))
                yield
        @contextmanager
        def lock(*args,**kwargs):
            self.assertEqual(events,['shared-gate'])
            with actual_lock(*args,**kwargs):
                events.append('physical-lock')
                yield
        provider=Mock()
        def compute():
            self.assertEqual(events,['shared-gate','physical-lock'])
            self.assertEqual(admission._held.mode,'shared')
            with (store.root()/'local-lifecycle.lock').open('a') as contender:
                with self.assertRaises(BlockingIOError):fcntl.flock(contender,fcntl.LOCK_EX|fcntl.LOCK_NB)
            events.append('compute')
            return 'ACTIVE'
        provider.compute_state.side_effect=compute
        provider.bound_alive.return_value=True
        with patch.object(admission,'lifecycle_gate',side_effect=gate),patch.object(lifecycle,'_lock',side_effect=lock):
            self.observe(provider)
        self.assertEqual(events,['shared-gate','physical-lock','compute'])

    def test_concurrent_absent_proof_startups_seed_once(self):
        import subprocess
        script="""
import sys,time
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,sys.argv[1])
import warehouse_lifecycle as lifecycle
import runtime_profile
runtime_profile.logical_config=lambda:dict(foundation_generation_id='generation',foundation_revision=1)
save=lifecycle._save_proof
def record():
    with (lifecycle._proof_path().with_name('seeds')).open('a') as out:out.write('seed\\n')
    time.sleep(.05)
    save()
lifecycle._save_proof=record
provider=SimpleNamespace(compute_state=lambda:'ACTIVE',bound_alive=lambda:True)
import logical_creations
logical_creations._provider=provider
logical_creations.ensure_active_provider=lambda **kwargs:provider
lifecycle.observe_startup(provider)
(lifecycle._proof_path().with_name(sys.argv[2])).write_text(lifecycle._shared_proof()['epoch'])
"""
        processes=[subprocess.Popen([sys.executable,'-c',script,str(Path(lifecycle.__file__).parent),name],stdout=subprocess.PIPE,stderr=subprocess.PIPE) for name in ('a','b')]
        for process in processes:
            _,error=process.communicate(timeout=10)
            self.assertEqual(process.returncode,0,error.decode())
        self.assertEqual((store.root()/'seeds').read_text().count('seed'),1)
        self.assertEqual((store.root()/'a').read_text(),(store.root()/'b').read_text())

    def test_managed_chill_excludes_startup_observation_and_stopped_never_seeds(self):
        from unittest.mock import Mock
        stop_started=threading.Event();release=threading.Event();observed=threading.Event()
        errors=[];state={'compute':'ACTIVE'}
        lifecycle._save_proof()
        diagnostic=Mock()
        diagnostic._provisioner.return_value={'username':'private','password':'private'}
        diagnostic._request.return_value=metrics(3,3)
        startup=Mock()
        startup.compute_state.side_effect=lambda:(observed.set(),state['compute'])[1]
        startup.bound_alive.return_value=True
        def stop(*args,**kwargs):
            kwargs['mutation_started']()
            stop_started.set()
            if not release.wait(2):raise RuntimeError('release missing')
            state['compute']='STOPPED'
            return {'state':'chilled'}
        def chill():
            try:lifecycle.chill(authorize=lambda:None)
            except BaseException as error:errors.append(error)
        def observe():
            try:self.observe(startup)
            except BaseException as error:errors.append(error)
        with patch('warehouse_resident.check_lifecycle'),patch('warehouse_resident._logical_foundation',return_value={'saved':True}),patch('warehouse_resident._logical_status',return_value={'state':'active'}),patch('logical_creations.ensure_active_provider',return_value=diagnostic),patch('warehouse_resident._logical_transition',side_effect=stop):
            stopping=threading.Thread(target=chill);stopping.start()
            self.assertTrue(stop_started.wait(1))
            starting=threading.Thread(target=observe);starting.start()
            self.assertFalse(observed.wait(.05))
            release.set()
            stopping.join(3);starting.join(3)
            self.assertFalse(stopping.is_alive() or starting.is_alive())
        self.assertEqual(errors,[])
        self.assertTrue(observed.is_set())
        self.assertIsNone(lifecycle.shared_engine_proof())
        self.assertIsNone(lifecycle._LOCAL_PROOF_ID)


    def test_startup_rebinds_old_live_children_under_all_locks_before_new_epoch_adoption(self):
        import fcntl
        from unittest.mock import Mock
        events=[]
        lifecycle._save_proof()
        old=lifecycle._LOCAL_PROOF_ID
        # The server paused after its initial full binding; a peer completed Chill/Wake.
        lifecycle._save_proof()
        shared=lifecycle.shared_engine_proof()
        lifecycle._LOCAL_PROOF_ID=old
        class BoundProvider:
            children=True
            epoch='before-chill'
            def compute_state(inner):
                self.assertEqual(admission._held.mode,'shared')
                with (store.root()/'local-lifecycle.lock').open('a') as contender:
                    with self.assertRaises(BlockingIOError):fcntl.flock(contender,fcntl.LOCK_EX|fcntl.LOCK_NB)
                events.append('compute')
                return 'ACTIVE'
            def bound_alive(inner):return inner.children
            def close(inner):
                self.assertTrue(lifecycle_creations._provider_lock._is_owned())
                events.append('close-old');inner.children=False
            def open(inner):
                self.assertTrue(lifecycle_creations._provider_lock._is_owned())
                events.append('open-own');inner.children=True
            def bind_registered(inner,expected):
                self.assertTrue(lifecycle_creations._provider_lock._is_owned())
                self.assertIsNone(lifecycle._LOCAL_PROOF_ID)
                self.assertEqual(expected,{'saved':'registered-proof'})
                self.assertEqual(lifecycle.shared_engine_proof(),shared)
                events.append('full-bind');inner.epoch='after-wake'
            def probe(inner):raise AssertionError('extra Provider probe')
        provider=BoundProvider()
        with patch('logical_creations._provider',provider),patch('logical_creations.ensure_active_provider',side_effect=ACTUAL_ENSURE_ACTIVE_PROVIDER) as repair,patch('logical_creations._bound_profile'),patch('logical_creations._foundation',return_value={'verification_ref':'saved'}),patch('logical_creations._registered_probe',return_value={'saved':'registered-proof'}),patch('warehouse_resident.invalidate_persistent_trino',side_effect=AssertionError('startup invalidated SQL')):
            lifecycle.observe_startup(provider)
            repair.assert_called_once_with(force_rebind=True)
        self.assertEqual(events,['compute','compute','close-old','open-own','full-bind'])
        self.assertEqual(provider.epoch,'after-wake')
        self.assertEqual(lifecycle.shared_engine_proof(),shared)
        self.assertFalse(lifecycle.requires_wake())

    def test_failed_or_wrong_startup_binding_keeps_peer_epoch_and_local_unready(self):
        from unittest.mock import Mock
        lifecycle._save_proof()
        peer_bytes=lifecycle._proof_path().read_bytes()
        provider=Mock()
        provider.compute_state.return_value='ACTIVE'
        provider.bound_alive.return_value=True
        with patch('logical_creations._provider',object()),patch('logical_creations.ensure_active_provider') as repair:
            with self.assertRaisesRegex(Exception,'WAREHOUSE_UNAVAILABLE'):lifecycle.observe_startup(provider)
            repair.assert_not_called()
        self.assertIsNone(lifecycle._LOCAL_PROOF_ID)
        self.assertEqual(lifecycle._proof_path().read_bytes(),peer_bytes)
        for effect in ('bind-failure','wrong-return','lost-binding'):
            with self.subTest(effect=effect),patch('logical_creations._provider',provider),patch('logical_creations.ensure_active_provider') as repair,patch('warehouse_resident.invalidate_persistent_trino',side_effect=AssertionError('SQL invalidation')):
                provider.bound_alive.side_effect=None
                if effect=='bind-failure':repair.side_effect=RuntimeError('full binding failed')
                elif effect=='wrong-return':repair.return_value=object()
                else:
                    repair.return_value=provider
                    provider.bound_alive.side_effect=[True,False]
                with self.assertRaises(Exception):lifecycle.observe_startup(provider)
                repair.assert_called_once_with(force_rebind=True)
                self.assertIsNone(lifecycle._LOCAL_PROOF_ID)
                self.assertEqual(lifecycle._proof_path().read_bytes(),peer_bytes)


class CompletedQueryIntegrationTests(unittest.TestCase):
    def test_real_completed_submit_uses_warm_lifecycle_without_physical_probe(self):
        from contextlib import ExitStack, nullcontext
        import data_management
        import query_context
        import query_service
        import request_auth
        import warehouse_resident
        from test_request_auth import PrincipalTests
        fixture = PrincipalTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with store.connect() as db:
            db.execute("UPDATE warehouse_config_generations SET warehouse_id='LOCAL'")
            schema = data_management.create_schema(db, fixture.sales, 'marts', 'Marts', 'marts', 'test')
        principal = fixture.principal()
        context = query_context.resolve_context(principal, fixture.sales, schema.id, 'bi_viewer',
            warehouse_id='LOCAL', expected_binding_revision=1)
        decision = request_auth.authorize_database(principal, fixture.sales, 'bi_viewer',
            expected_generation_id=context.generation_id)
        config = dict(warehouse_id='LOCAL', foundation_generation_id=context.generation_id, foundation_revision=1)
        pins = {}
        def cleanup():
            for pin in pins.values():
                query_service._close_query_owner(pin)
        self.addCleanup(cleanup)
        with ExitStack() as owner, patch.object(lifecycle, 'applicable', return_value=True), \
             patch('runtime_profile.logical_config', return_value=config), \
             patch.object(query_service, '_pins', pins), patch.object(query_service, '_origin', None), \
             patch.object(query_service, '_lifetime', owner), \
             patch('trino_auth.prepare_backend_v2', return_value=(decision, {'X-Trino-User': 'scoped'})), \
             patch('trino_auth.backend_ssl_context', return_value=None), patch('trino_auth.validate_backend_url'), \
             patch.object(warehouse_resident, 'persistent_trino', return_value=nullcontext('https://trusted:8443')), \
             patch.object(query_service, '_transport', return_value=dict(id='backend', data=[[1]])) as transport, \
             patch.object(lifecycle, '_lock', side_effect=AssertionError('warm query took physical lock')), \
             patch.object(warehouse_resident, '_logical_status', side_effect=AssertionError('warm query probed kube')), \
             patch('logical_provider.Provider.compute_state', side_effect=AssertionError('warm query probed Provider')):
            lifecycle._save_proof()
            page = query_service.submit(principal, context, 'SELECT 1', mode='read-only',
                max_rows=10, request_id='warm-integration', worksheet_id=None)
            self.assertEqual((page['state'], page['data']), ('FINISHED', [[1]]))
            self.assertEqual(query_service.inspect_query(principal, page['id'], selected_role='bi_viewer')['state'], 'FINISHED')
            self.assertEqual(transport.call_count, 1)
            samples = []
            for _ in range(30):
                start = time.perf_counter()
                lifecycle.ensure_awake(actor=principal.principal_id,
                    authorize=lambda: request_auth.authorize_database(principal, fixture.sales, 'bi_viewer',
                        expected_generation_id=context.generation_id))
                samples.append((time.perf_counter() - start) * 1000)
            print('fixture warm lifecycle p95_ms=' + str(round(sorted(samples)[28], 3)))


    def test_peer_startup_preserves_existing_paged_query_continuation(self):
        from contextlib import ExitStack,nullcontext
        import subprocess
        import data_management
        import query_context
        import query_service
        import request_auth
        import warehouse_resident
        from test_request_auth import PrincipalTests
        fixture=PrincipalTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        with store.connect() as db:
            db.execute("UPDATE warehouse_config_generations SET warehouse_id='LOCAL'")
            schema=data_management.create_schema(db,fixture.sales,'marts','Marts','marts','test')
        principal=fixture.principal()
        context=query_context.resolve_context(principal,fixture.sales,schema.id,'bi_viewer',warehouse_id='LOCAL',expected_binding_revision=1)
        decision=request_auth.authorize_database(principal,fixture.sales,'bi_viewer',expected_generation_id=context.generation_id)
        config=dict(warehouse_id='LOCAL',foundation_generation_id=context.generation_id,foundation_revision=1)
        pins={}
        def cleanup():
            for pin in pins.values():query_service._close_query_owner(pin)
        self.addCleanup(cleanup)
        pages=[dict(id='backend',data=[[1]],nextUri='https://trusted:8443/v1/statement/backend/1'),
               dict(id='backend',data=[[2]])]
        script="""
import sys
from types import SimpleNamespace
sys.path.insert(0,sys.argv[1])
import warehouse_lifecycle as lifecycle
import runtime_profile
runtime_profile.logical_config=lambda:dict(foundation_generation_id=sys.argv[2],foundation_revision=1)
provider=SimpleNamespace(compute_state=lambda:'ACTIVE',bound_alive=lambda:True)
import logical_creations
logical_creations._provider=provider
logical_creations.ensure_active_provider=lambda **kwargs:provider
lifecycle.observe_startup(provider)
assert lifecycle._shared_proof()['epoch']==sys.argv[3]
"""
        with ExitStack() as owner,patch.object(lifecycle,'applicable',return_value=True),patch('runtime_profile.logical_config',return_value=config),patch.object(query_service,'_pins',pins),patch.object(query_service,'_origin',None),patch.object(query_service,'_lifetime',owner),patch('trino_auth.prepare_backend_v2',return_value=(decision,{'X-Trino-User':'scoped'})),patch('trino_auth.backend_ssl_context',return_value=None),patch('trino_auth.validate_backend_url'),patch.object(warehouse_resident,'persistent_trino',return_value=nullcontext('https://trusted:8443')),patch.object(query_service,'_transport',side_effect=pages) as transport,patch.object(warehouse_resident,'invalidate_persistent_trino',side_effect=AssertionError('peer invalidated SQL tunnel')) as invalidate,patch('logical_creations.ensure_active_provider',side_effect=AssertionError('peer forced Provider rebind')) as repair:
            lifecycle._save_proof()
            initial=lifecycle.shared_engine_proof()
            first=query_service.submit(principal,context,'SELECT 1',mode='read-only',max_rows=10,request_id='paged-startup',worksheet_id=None)
            self.assertEqual(first['state'],'RUNNING')
            with patch.object(warehouse_resident,'_logical_foundation',side_effect=AssertionError('reconcile probed while page active')):
                with self.assertRaisesRegex(Exception,'^WAREHOUSE_BUSY$'):
                    lifecycle.reconcile(authorize=lambda:None)
            self.assertEqual(lifecycle.shared_engine_proof(),initial)
            self.assertFalse(lifecycle.requires_wake())
            pin=pins[first['id']]
            result=subprocess.run([sys.executable,'-c',script,str(Path(lifecycle.__file__).parent),context.generation_id,initial['epoch']],capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr.decode())
            self.assertEqual(lifecycle.shared_engine_proof(),initial)
            self.assertFalse(lifecycle.requires_wake())
            lifecycle.ensure_awake(actor=principal.principal_id,authorize=lambda:None)
            final=query_service.fetch(principal,first['id'],first['next_uri'].rsplit('/',1)[1],selected_role='bi_viewer')
            self.assertEqual((final['state'],final['data']),('FINISHED',[[2]]))
            self.assertIs(pins[first['id']],pin)
            self.assertEqual(transport.call_count,2)
            invalidate.assert_not_called();repair.assert_not_called()


if __name__ == '__main__': unittest.main()
