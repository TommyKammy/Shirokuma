"""Fake-only lake training orchestration: no API, Reader, SDK, fit or kernel."""
from copy import deepcopy
import io
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.local_experiment.lake_training import run as P
from tests.test_lake_training_fixture import Harness, TOKEN
from tests.test_lake_training_workload import observation

N, F, T = P.N, P.F, P.T
QUERY = 'd'*32
STORE = '00000000-0000-4000-8000-000000000001'


class Runtime:
    def __init__(self, events, state='running'):
        self.events, self.state, self.store_id = events, state, STORE
        self.stop_error = self.start_error = None

    def status(self):
        return dict(schema_version=1, state=self.state, store_id=self.store_id,
                    healthy=self.state == 'running', identity={'pid':123} if self.state == 'running' else None)

    def require_stopped(self):
        if self.state != 'stopped': raise ValueError('not stopped')
        return self.status()

    def stop(self):
        self.events.append('stop'); self.state = 'stopped'
        if self.stop_error: raise self.stop_error
        return self.status()

    def start(self):
        self.events.append('start')
        if self.start_error: raise self.start_error
        self.state = 'running'
        return self.status()


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.state = self.root/'attempts'; self.state.mkdir(mode=0o700)
        self.output = self.state/TOKEN; self.output.mkdir(mode=0o700)
        self.binary = self.output/'shirokuma'; self.binary.write_bytes(b'fake binary, never executed')
        self.binary.chmod(0o700)
        self.harness = Harness(self.output/'fixture')
        request = dict(command='select', table='local_experiment.training_'+TOKEN,
                       columns=['row_id','x1','x2','x3','target'], limit=100)
        self.identity = N.C.project_identity(self.harness.metadata(), request, F.COLUMNS)
        self.harness.loads = 0
        self.events = []; self.runtime = Runtime(self.events)
        self.code = {'test source':'f'*64}
        self.public = observation()
        self.public.update(table_uuid=self.identity['table_uuid'], snapshot_id=self.identity['current_snapshot_id'],
                           schema_sha256=self.identity['schema_sha256'])
        self.reader_error = self.backup_error = self.tracker_error = None
        self.reader_unknown = self.tracker_unknown = False
        for target, name, value in ((T,'SOURCE_ROOT',self.state), (P,'STATE',self.state)):
            item = patch.object(target,name,value); item.start(); self.addCleanup(item.stop)
        item = patch.object(F.SB,'bounded_query',side_effect=lambda client, sql, deadline:client.query(sql))
        item.start(); self.addCleanup(item.stop)
        self.original_env = {key:'untrusted-'+str(i) for i,key in enumerate(P.ENV_KEYS)}
        item = patch.dict(os.environ,self.original_env); item.start(); self.addCleanup(item.stop)

    def fixture(self, output, token, callback, *, reader_closed):
        self.events.append('fixture')
        result = F.run(output,token,callback,reader_closed=reader_closed,
                       _runtime=self.harness.runtime,_drain=self.harness.drain)
        self.events.append('fixture-finished')
        return result

    def read(self, request):
        self.events.append('reader')
        self.assertEqual(os.environ['SHIROKUMA_NOTEBOOK_RUN_DIR'],str(self.output/'reader'))
        self.assertEqual(os.environ['SHIROKUMA_NOTEBOOK_BINARY'],str(self.binary))
        self.assertNotIn(P.S.REQUEST_PATH_ENV,os.environ); self.assertNotIn(P.S.REQUEST_SHA_ENV,os.environ)
        self.assertFalse(self.harness.active)
        folder = self.output/'reader'
        N._write(folder/'reader-intent.json',dict(schema_version=1,command='query select',request=request))
        cancelled = self.reader_error is not None
        frames = [dict(schema_version=1,event='allocated',command='query select',evidence_token=QUERY),
            dict(schema_version=1,event='result',command='query select',evidence_token=QUERY,
                 result='interrupted' if cancelled else 'passed', observation=None if cancelled else self.public,
                 error_code='INTERRUPTED' if cancelled else None,cleanup_verified=not self.reader_unknown,
                 evidence=f'tmp/local-experiment/query-read/{QUERY}/summary.json')]
        path = folder/'reader.stdout.jsonl'
        path.write_bytes(b''.join(N._canonical(frame) for frame in frames)); path.chmod(0o600)
        N._write(folder/'reader-status.json',dict(schema_version=1,state='interrupted' if cancelled else 'passed',
            pid=12345,pgid=12345,process_start='Sun Sep 20 12:00:00 2026',evidence_token=QUERY,
            exit_code=130 if cancelled else 0,child_exited=True,cleanup_verified=not self.reader_unknown,
            error_code='NOTEBOOK_INTERRUPTED' if cancelled else None))
        if self.reader_error: raise self.reader_error
        return deepcopy(self.public)

    def backup(self,destination):
        self.events.append('backup'); self.runtime.require_stopped()
        self.assertEqual(destination,self.output/'cold-backup')
        self.assertIn('fixture-finished',self.events)
        self.assertTrue((self.output/'source-ready.json').exists())
        if self.backup_error: raise self.backup_error
        return dict(result='verified',store_id=STORE,source='fixed/store',backup='fixed/cold-backup',inventory={})

    def tracker(self,output,token):
        self.events.append('tracking'); self.runtime.require_stopped()
        self.assertEqual(token,TOKEN)
        T.Source(token).guard()
        N._write(output/'tracker-owner-intent.json',dict(schema_version=1,source_token=token))
        N._write(output/'tracker-status.json',dict(schema_version=1,pid=23456,child_exited=not self.tracker_unknown,
            group_empty=not self.tracker_unknown,exit_code=None if self.tracker_unknown else 0))
        if self.tracker_error: raise self.tracker_error
        return dict(result='verified',source_token=token,run_id='e'*32,
                    evidence='tmp/local-experiment/mlflow-tracking/lake-training/'+'f'*32,failure_code=None)

    def verify_tracking(self,value,token,store_id,output):
        self.events.append('verify')
        self.assertEqual((value['source_token'],token,store_id,output),(TOKEN,TOKEN,STORE,self.output))
        return dict(attempt_token='f'*32,run_id='e'*32,experiment_id='4',workload_sha256='9'*64)

    def execute(self,**overrides):
        arguments = dict(fixture_runner=self.fixture,read_select=self.read,runtime=self.runtime,
                         backup_export=self.backup,tracker=self.tracker,tracking_validator=self.verify_tracking,
                         source_factory=lambda:deepcopy(self.code))
        arguments.update(overrides)
        return P.execute(self.output,self.binary,TOKEN,**arguments)

    def test_accepted_reader_source_is_sealed_after_fixture_cleanup_and_runtime_restored(self):
        result = self.execute()
        self.assertEqual(result['result'],'passed')
        self.assertIsNone(result['failure_phase'])
        self.assertEqual(self.events,['fixture','reader','fixture-finished','stop','backup','tracking','verify','start'])
        self.assertFalse(self.harness.exists)
        self.assertEqual(self.runtime.state,'running')
        self.assertEqual({key:os.environ.get(key) for key in P.ENV_KEYS},self.original_env)
        source = T.Source(TOKEN)
        self.assertEqual(source.value['observation'],self.public)
        self.assertEqual(source.value['query_evidence_token'],QUERY)
        self.assertEqual(len(source.value['split_row_ids']['train']),48)
        ready = N._parse((self.output/'source-ready.json').read_bytes())
        self.assertEqual(ready['lake_input_sha256'],P.digest((self.output/'lake-input.json').read_bytes()))
        self.assertTrue(all((self.output/name).stat().st_mode & 0o777 == 0o600 for name in
            ('pipeline-intent.json','summary.json','lake-input.json','source-ready.json')))

    def test_original_stopped_runtime_remains_stopped(self):
        self.runtime.state='stopped'
        self.assertEqual(self.execute()['result'],'passed')
        self.assertNotIn('stop',self.events); self.assertNotIn('start',self.events)
        self.assertEqual(self.runtime.state,'stopped')

    def test_uuid_drift_refuses_source_and_all_tracking_actions(self):
        self.public['table_uuid']='11111111-1111-4111-8111-111111111112'
        report = self.execute()
        self.assertEqual(report['result'],'failed')
        self.assertFalse((self.output/'source-ready.json').exists())
        self.assertNotIn('stop',self.events); self.assertNotIn('tracking',self.events)
        self.assertFalse(self.harness.exists)

    def test_reader_return_must_equal_saved_terminal_observation(self):
        def different(request):
            value=self.read(request); value['rows'][0][1]['value']='0.0'
            value['rows_sha256']=N.C.digest(value['rows']);return value
        self.assertEqual(self.execute(read_select=different)['result'],'failed')
        self.assertNotIn('stop',self.events)

    def test_unknown_reader_cleanup_defers_drop_and_never_creates_source_ready(self):
        self.reader_unknown=True
        report=self.execute()
        self.assertEqual(report['result'],'unknown')
        self.assertTrue(self.harness.exists)
        self.assertFalse(report['reader_cleanup_confirmed'])
        self.assertNotIn('stop',self.events)
        self.assertFalse((self.output/'source-ready.json').exists())

    def test_unknown_fixture_drop_prevents_source_and_mlflow_changes(self):
        self.harness.failure='drop'
        report=self.execute()
        self.assertEqual(report['result'],'unknown')
        self.assertNotIn('backup',self.events)
        self.assertFalse((self.output/'source-ready.json').exists())

    def test_reader_first_cancellation_survives_fixture_cleanup_failure(self):
        first=KeyboardInterrupt('first'); self.reader_error=first
        self.harness.drop_error=RuntimeError('secondary cleanup')
        with self.assertRaises(KeyboardInterrupt) as caught:self.execute()
        self.assertIs(caught.exception,first)
        self.assertNotIn('stop',self.events)
        self.assertEqual({key:os.environ.get(key) for key in P.ENV_KEYS},self.original_env)

    def test_source_change_before_reader_prevents_reader_and_tracking(self):
        def changed_fixture(output,token,callback,**kwargs):
            self.code['test source']='0'*64
            return self.fixture(output,token,callback,**kwargs)
        report=self.execute(fixture_runner=changed_fixture)
        self.assertEqual(report['result'],'failed')
        self.assertNotIn('reader',self.events); self.assertNotIn('stop',self.events)

    def test_source_change_after_backup_prevents_tracker_and_restores_runtime(self):
        def changed_backup(destination):
            value=self.backup(destination); self.code['test source']='0'*64;return value
        report=self.execute(backup_export=changed_backup)
        self.assertEqual(report['result'],'failed')
        self.assertNotIn('tracking',self.events); self.assertEqual(self.runtime.state,'running')

    def test_binary_change_after_read_is_rejected_before_source_ready(self):
        def changed(request):
            value=self.read(request); self.binary.write_bytes(b'changed');return value
        report=self.execute(read_select=changed)
        self.assertEqual(report['result'],'failed')
        self.assertFalse((self.output/'source-ready.json').exists())
        self.assertNotIn('stop',self.events)

    def test_unknown_initial_runtime_is_never_stopped_or_tracked(self):
        self.runtime.state='unknown'
        report=self.execute()
        self.assertEqual(report['result'],'failed')
        self.assertNotIn('stop',self.events);self.assertNotIn('backup',self.events)

    def test_backup_failure_restores_original_running_state_without_tracking(self):
        self.backup_error=ValueError('backup failed')
        report=self.execute()
        self.assertEqual(report['result'],'failed')
        self.assertEqual(self.runtime.state,'running');self.assertNotIn('tracking',self.events)
        self.assertEqual(report['failure_phase'],'backup')

    def test_stop_cancellation_restores_owned_stopped_runtime_and_preserves_first(self):
        first=KeyboardInterrupt('first');self.runtime.stop_error=first
        with self.assertRaises(KeyboardInterrupt) as caught:self.execute()
        self.assertIs(caught.exception,first)
        self.assertEqual(self.runtime.state,'running');self.assertNotIn('backup',self.events)

    def test_tracker_cancellation_waited_closed_then_runtime_restored(self):
        first=KeyboardInterrupt('first');self.tracker_error=first
        with self.assertRaises(KeyboardInterrupt) as caught:self.execute()
        self.assertIs(caught.exception,first)
        self.assertEqual(self.runtime.state,'running')
        self.assertNotIn('verify',self.events)

    def test_unknown_tracker_group_prevents_runtime_restart(self):
        self.tracker_unknown=True
        report=self.execute()
        self.assertEqual(report['result'],'unknown')
        self.assertFalse(report['tracker_cleanup_confirmed'])
        self.assertFalse(report['runtime_restored'])
        self.assertEqual(self.runtime.state,'stopped');self.assertNotIn('start',self.events)

    def test_first_cancel_survives_restart_failure(self):
        first=KeyboardInterrupt('first');self.tracker_error=first
        self.runtime.start_error=ValueError('restart failed')
        with self.assertRaises(KeyboardInterrupt) as caught:self.execute()
        self.assertIs(caught.exception,first)
        self.assertEqual(N._parse((self.output/'summary.json').read_bytes())['result'],'unknown')

    def test_store_identity_change_prevents_restart(self):
        def changed(output,token):
            value=self.tracker(output,token);self.runtime.store_id='00000000-0000-4000-8000-000000000002';return value
        report=self.execute(tracker=changed)
        self.assertEqual(report['result'],'unknown')
        self.assertNotIn('start',self.events)

    def test_repeat_attempt_is_refused_before_fixture(self):
        self.execute(); count=len(self.events)
        with self.assertRaises(FileExistsError):self.execute()
        self.assertEqual(len(self.events),count)

    def test_final_evidence_failure_never_returns_passed(self):
        write=N._write
        def fail(path,value,**kwargs):
            if path.name=='summary.json' and value.get('phase')=='complete':raise OSError('disk write failed')
            return write(path,value,**kwargs)
        with patch.object(N,'_write',side_effect=fail):report=self.execute()
        self.assertEqual(report['result'],'failed')
        self.assertTrue(report['runtime_restored'])

    def test_environment_setup_failure_restores_all_four_parent_values(self):
        with patch.object(os.environ,'update',side_effect=OSError('setup failed')):
            with self.assertRaises(OSError):
                with P.reader_environment(self.output,self.binary):self.fail('body must not run')
        self.assertEqual({key:os.environ.get(key) for key in P.ENV_KEYS},self.original_env)

    def test_invalid_cli_arguments_allocate_nothing(self):
        with patch.object(P,'sources',side_effect=AssertionError('must not read source')),patch('sys.stdout',new=io.StringIO()):
            self.assertEqual(P.main(['--table','arbitrary']),1)

    def test_owner_late_cancellation_keeps_passed_business_result_separate(self):
        report=dict(result='passed',phase='complete',reader_cleanup_confirmed=True,fixture_cleanup_confirmed=True,
                    tracker_cleanup_confirmed=True,runtime_restored=True)
        terminal=[];write=N._write
        def interrupted(path,value,**kwargs):
            if path.name=='owner-result.json':
                terminal.append(deepcopy(value));raise P.A.AssetsInterrupted(15)
            return write(path,value,**kwargs)
        stream=io.StringIO()
        with patch.object(P,'sources',return_value=self.code),patch.object(P.uuid,'uuid4',return_value=SimpleNamespace(hex='c'*32)), \
             patch.object(P.subprocess,'run',return_value=SimpleNamespace(returncode=0)), \
             patch.object(P,'execute',return_value=report),patch.object(N,'_write',side_effect=interrupted), \
             patch('sys.stdout',new=stream):
            self.assertEqual(P.main([]),143)
        self.assertEqual(terminal[0]['summary_result'],'passed')
        self.assertEqual(N._parse(stream.getvalue())['result'],'interrupted')

    def test_cancel_with_failed_final_summary_does_not_trust_stale_cleanup_flags(self):
        first=P.A.AssetsInterrupted(15);write=N._write;execute=P.execute;final_saves=[];observed=[]
        def fixture(output,token,callback,*,reader_closed):
            output.mkdir(mode=0o700)
            N._write(output/'report.json',dict(result='unknown',reader_cleanup_verified=False,
                cleanup={'verified':False},query_drain={'verified':False},runtime={}))
            raise first
        def fail_final(path,value,**kwargs):
            if path.name=='summary.json' and value.get('phase')=='complete':
                final_saves.append(deepcopy(value));raise OSError('final summary failed')
            return write(path,value,**kwargs)
        def build(argv,**kwargs):
            Path(argv[3]).write_bytes(b'fake binary');return SimpleNamespace(returncode=0)
        def run(output,binary,token,**kwargs):
            try:return execute(output,binary,token,fixture_runner=fixture,source_factory=lambda:self.code,**kwargs)
            except BaseException as error:
                observed.append(error);raise
        stream=io.StringIO();token='c'*32
        with patch.object(P,'sources',return_value=self.code),patch.object(P.uuid,'uuid4',return_value=SimpleNamespace(hex=token)), \
             patch.object(P.subprocess,'run',side_effect=build),patch.object(P,'execute',side_effect=run), \
             patch.object(N,'_write',side_effect=fail_final),patch('sys.stdout',new=stream):
            self.assertEqual(P.main([]),143)
        self.assertEqual(observed,[first]);self.assertEqual(len(final_saves),1)
        stale=N._parse((self.state/token/'summary.json').read_bytes())
        self.assertEqual(stale['result'],'running');self.assertNotEqual(stale['phase'],'complete')
        self.assertTrue(stale['fixture_cleanup_confirmed'])
        owner=N._parse((self.state/token/'owner-result.json').read_bytes())
        self.assertEqual(owner['result'],'unknown');self.assertFalse(owner['cleanup_confirmed'])
        self.assertEqual(owner['summary_result'],'running')
        self.assertEqual(N._parse(stream.getvalue())['result'],'unknown')

    def test_complete_phase_with_nonterminal_result_is_not_cleanup_proof(self):
        report=dict(result='running',phase='complete',reader_cleanup_confirmed=True,fixture_cleanup_confirmed=True,
                    tracker_cleanup_confirmed=True,runtime_restored=True)
        stream=io.StringIO()
        with patch.object(P,'sources',return_value=self.code),patch.object(P.uuid,'uuid4',return_value=SimpleNamespace(hex='c'*32)), \
             patch.object(P.subprocess,'run',return_value=SimpleNamespace(returncode=0)), \
             patch.object(P,'execute',return_value=report),patch('sys.stdout',new=stream):
            self.assertEqual(P.main([]),1)
        self.assertEqual(N._parse(stream.getvalue())['result'],'unknown')
        self.assertFalse(N._parse(stream.getvalue())['cleanup_confirmed'])


class TrackerProcessTests(unittest.TestCase):
    def test_one_owned_tracker_process_has_private_output_and_no_parent_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary).resolve()
            result=dict(result='verified',source_token=TOKEN,run_id='e'*32,
                        evidence='tmp/local-experiment/mlflow-tracking/lake-training/'+'f'*32,failure_code=None)
            class Child:
                pid=12345
                def wait(self,timeout):return 0
                def poll(self):return 0
            def spawn(argv,**kwargs):
                self.assertTrue(kwargs['start_new_session'])
                self.assertEqual(argv[-2:],['--source-token',TOKEN])
                self.assertNotIn('AWS_SECRET_ACCESS_KEY',kwargs['env'])
                os.write(kwargs['stdout'],N._canonical(result));return Child()
            with patch.dict(os.environ,{'AWS_SECRET_ACCESS_KEY':'never forwarded'}), \
                 patch.object(P.subprocess,'Popen',side_effect=spawn) as called,patch.object(P.R,'processes',return_value={}):
                self.assertEqual(P.run_tracker(output,TOKEN),result)
            self.assertEqual(called.call_count,1)
            self.assertTrue(P.tracker_closed(output))
            self.assertEqual((output/'tracker.stdout.json').stat().st_mode & 0o777,0o600)

    def test_first_cancel_survives_status_failure_and_group_is_not_killed(self):
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary).resolve();first=KeyboardInterrupt('first')
            class Child:
                pid=12345
                returncode=None
                calls=0
                def wait(self,timeout):
                    self.calls+=1
                    if self.calls==1:raise first
                    self.returncode=130;return 130
                def poll(self):return self.returncode
            child=Child();write=N._write
            def status_fail(path,value,**kwargs):
                if path.name=='tracker-status.json' and value['child_exited']:raise OSError('final status failed')
                return write(path,value,**kwargs)
            with patch.object(P.subprocess,'Popen',return_value=child),patch.object(P.os,'killpg') as signal_group, \
                 patch.object(P.R,'processes',return_value={}),patch.object(N,'_write',side_effect=status_fail):
                with self.assertRaises(KeyboardInterrupt) as caught:P.run_tracker(output,TOKEN)
            self.assertIs(caught.exception,first)
            signal_group.assert_called_once_with(child.pid,P.signal.SIGINT)
            self.assertFalse(P.tracker_closed(output))

    def test_reaped_parent_with_live_worker_remains_unknown(self):
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary).resolve()
            class Child:
                pid=12345
                def wait(self,timeout):return 0
                def poll(self):return 0
            with patch.object(P.subprocess,'Popen',return_value=Child()), \
                 patch.object(P.R,'processes',return_value={12346:{'pgid':12345}}):
                with self.assertRaises(P.PipelineError):P.run_tracker(output,TOKEN)
            self.assertFalse(P.tracker_closed(output))


if __name__ == '__main__':
    unittest.main()
