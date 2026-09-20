"""Fixed training dispatch and owner ordering with fake kernels and pipeline only."""
import asyncio
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import signal
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tools.local_experiment.notebook import runner as R, train as TN, training as T
from tests.test_local_notebook_runner import FakeKernel, FakeNotebook

SOURCE, ATTEMPT, QUERY = 'a'*32, 'b'*32, 'c'*32
IDENTITY = dict(attempt_token=ATTEMPT,run_id='d'*32,experiment_id='4',
                workload_sha256='e'*64,store_id='fixed-store')
PUBLIC = dict(profile=T.LAKE_PROFILE,identity=IDENTITY,
              lake_source=dict(source_token=SOURCE,query_evidence_token=QUERY),model=dict(intercept=4.0))
SUMMARY = dict(query_evidence_token=QUERY,reader_cleanup_confirmed=True,store_id='fixed-store',
               tracking={k:v for k,v in IDENTITY.items() if k!='store_id'})


class TrainRunnerDispatchTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve();self.state=self.root/'runs'
        previous=os.umask(0o022);self.addCleanup(os.umask,previous)

    def main(self, *, change=None, error=None, closed=True, source_changed=False, save_failure=False):
        public=deepcopy(PUBLIC);summary=deepcopy(SUMMARY)
        if change=='model':public['model']['intercept']+=1
        elif change=='profile':public['profile']='training-review-v1'
        elif change=='source':public['lake_source']['source_token']='f'*32
        elif change=='query':public['lake_source']['query_evidence_token']='f'*32
        elif change=='run':public['identity']['run_id']='f'*32
        elif change=='store':public['identity']['store_id']='different-store'
        source={'runner':'1'*64};later={'runner':'2'*64} if source_changed else source
        captured=[]
        async def execute(directory,binary,notebook,**kwargs):
            self.assertIsNone(binary);self.assertEqual(kwargs,dict(train_lake=True))
            captured.append((directory,notebook))
            R.write_json(directory/'training-review.json',public)
            return dict(result='unknown' if not closed else 'interrupted' if error else 'passed',error=error,
                pipeline_cleanup_confirmed=closed,reader_cleanup_confirmed=closed,
                kernel_cleanup_confirmed=closed,kernel_pid=202)
        def verify(value,token,*,kind):
            self.assertEqual((token,kind),(ATTEMPT,'iceberg'))
            if value!=PUBLIC:raise ValueError('publication differs from private training')
        status=dict(state='passed' if error is None else 'interrupted',evidence_token=SOURCE,exit_code=0)
        if change=='missing':status=None
        elif change=='status-token':status['evidence_token']='f'*32
        original_write=R.write_json
        def write(path,value):
            if save_failure and path.name=='result.json':raise OSError('private failure')
            return original_write(path,value)
        output=io.StringIO()
        with patch.object(R,'ROOT',self.root),patch.object(R,'STATE',self.state), \
             patch.object(R,'execute',side_effect=execute),patch.object(R.subprocess,'run') as build, \
             patch.object(R,'reader_status',side_effect=AssertionError('outer Reader must not be inspected')), \
             patch.object(R,'train_sources',side_effect=[source,later]),patch.object(TN,'pipeline_status',return_value=status), \
             patch.object(TN,'pipeline_closed',return_value=closed), \
             patch.object(TN,'terminal_receipt',return_value=(SOURCE,summary,closed,'passed' if error is None else 'interrupted')), \
             patch.object(T,'validate_public'),patch.object(T,'verify_saved_review',side_effect=verify) as verify_mock, \
             patch.object(R,'write_json',side_effect=write),redirect_stdout(output):
            code=R.main(['--train-lake'])
        return code,json.loads(output.getvalue()),captured,build,verify_mock

    def test_fixed_dispatch_binds_pipeline_reader_and_tracked_model(self):
        code,result,captured,build,verify=self.main()
        self.assertEqual(code,0);self.assertEqual(result['result'],'passed')
        self.assertEqual(result['profile'],TN.PROFILE)
        self.assertEqual((result['source_token'],result['training_token'],result['reader_evidence_token']),
                         (SOURCE,ATTEMPT,QUERY))
        self.assertEqual(result['training_identity'],IDENTITY)
        self.assertTrue(all(result[k] for k in ('pipeline_cleanup_confirmed','reader_cleanup_confirmed','kernel_cleanup_confirmed')))
        self.assertEqual(result['code_cells'],3);build.assert_not_called();verify.assert_called_once()
        directory,notebook=captured[0]
        self.assertIn('training = train_lake()',notebook.cells[3].source)
        inputs=json.loads((directory/'inputs.json').read_text())
        self.assertEqual(inputs['profile'],TN.PROFILE);self.assertIsNone(inputs['binary_sha256'])
        self.assertEqual(inputs['template_sha256'],R.TRAIN_TEMPLATE_SHA256)
        self.assertFalse((directory/'shirokuma').exists());self.assertFalse((directory/'request.json').exists())

    def test_mixed_or_value_bearing_cli_never_allocates(self):
        for args in (['--train-lake','x'],['--train-lake','--request-stdin'],
                     ['--training-token',ATTEMPT,'--train-lake'],['--train-lake','--lake-training-token',ATTEMPT]):
            with self.subTest(args=args),patch.object(R,'STATE',self.state), \
                 patch.object(R,'execute') as execute,redirect_stdout(io.StringIO()) as output:
                self.assertEqual(R.main(args),1);self.assertFalse(self.state.exists())
                self.assertEqual(json.loads(output.getvalue())['error_code'],'ARGUMENTS_INVALID');execute.assert_not_called()

    def test_public_values_are_bound_to_private_model_and_pipeline(self):
        for field in ('model','profile','source','query','run','store','status-token','missing'):
            with self.subTest(field=field):
                code,result,_,build,_=self.main(change=field)
                self.assertEqual(code,1);self.assertNotEqual(result['result'],'passed');build.assert_not_called()

    def test_source_change_refuses_pass_after_closed_pipeline(self):
        code,result,*_=self.main(source_changed=True)
        self.assertEqual(code,1);self.assertEqual(result['result'],'failed')
        self.assertTrue(result['pipeline_cleanup_confirmed'])

    def test_cancel_and_unknown_cleanup_survive_later_source_failure(self):
        cancel=R.NotebookInterrupted(signal.SIGTERM)
        for closed in (True,False):
            with self.subTest(closed=closed):
                code,result,*_=self.main(error=cancel,closed=closed,source_changed=True)
                self.assertEqual(code,143);self.assertEqual(result['signal'],signal.SIGTERM)
                self.assertEqual(result['result'],'interrupted' if closed else 'unknown')
                self.assertEqual(result['error_code'],'INTERRUPTED')
                self.assertEqual(result['pipeline_cleanup_confirmed'],closed)

    def test_early_build_failure_closes_without_reader_or_training_identity(self):
        result=dict(result='failed',pipeline_cleanup_confirmed=False,reader_cleanup_confirmed=True,
                    source_token=None,reader_evidence_token=None,training_token=None,training_identity=None)
        status=dict(evidence_token=SOURCE,exit_code=1)
        with patch.object(TN,'pipeline_status',return_value=status),patch.object(TN,'pipeline_closed',return_value=True), \
             patch.object(TN,'terminal_receipt',return_value=(SOURCE,None,True,'failed')), \
             patch.object(T,'verify_saved_review') as verify:
            R.train_result(self.root,result)
        self.assertEqual(result['result'],'failed');self.assertTrue(result['pipeline_cleanup_confirmed'])
        self.assertTrue(result['reader_cleanup_confirmed']);self.assertEqual(result['source_token'],SOURCE)
        self.assertIsNone(result['reader_evidence_token']);self.assertIsNone(result['training_identity'])
        verify.assert_not_called()

    def test_final_save_failure_cannot_claim_pass(self):
        code,result,*_=self.main(save_failure=True)
        self.assertEqual(code,1);self.assertEqual(result['result'],'unknown')
        self.assertEqual(result['error_code'],'NOTEBOOK_EVIDENCE_SAVE_FAILED')

    def test_internal_mixed_modes_fail_before_kernel_or_directory_allocation(self):
        cases=[dict(train_lake=1),dict(train_lake=True,training_token=ATTEMPT),
               dict(train_lake=True,kind='iceberg'),dict(train_lake=True,binary=self.root/'binary'),
               dict(train_lake=True,request={'command':'select'})]
        for index,case in enumerate(cases):
            for entry in ('environment','execute'):
                directory=self.root/f'{index}-{entry}';directory.mkdir(mode=0o700)
                kwargs=dict(case);binary=kwargs.pop('binary',None)
                with self.subTest(case=case,entry=entry),patch.object(R,'AsyncKernelManager') as manager:
                    with self.assertRaises(ValueError):
                        if entry=='environment':R.kernel_environment(directory,binary,**kwargs)
                        else:asyncio.run(R.execute(directory,binary,object(),**kwargs))
                    manager.assert_not_called();self.assertEqual(list(directory.iterdir()),[])

    def test_trigger_environment_drops_inherited_reader_and_review_settings(self):
        directory=self.root/'env';directory.mkdir(mode=0o700)
        inherited={T.TOKEN_ENV:ATTEMPT,T.KIND_ENV:'iceberg','SHIROKUMA_NOTEBOOK_BINARY':'bad',
                   'SHIROKUMA_NOTEBOOK_REQUEST_PATH':'bad','SHIROKUMA_NOTEBOOK_REQUEST_SHA256':'f'*64,'PYTHONPATH':'bad'}
        with patch.dict(os.environ,inherited):
            value=R.kernel_environment(directory,None,train_lake=True)
            self.assertEqual({key:os.environ[key] for key in inherited},inherited)
        self.assertTrue(all(key not in value for key in inherited))
        self.assertEqual(value['SHIROKUMA_NOTEBOOK_RUN_DIR'],str(directory))

    def test_trigger_selects_pipeline_adapter_and_six_hundred_second_boundaries(self):
        directory=self.root/'execute';directory.mkdir(mode=0o700)
        channels=NS(start_channels=Mock(),wait_for_ready=AsyncMock())
        manager=NS(provisioner=NS(pid=202,pgid=202),start_kernel=AsyncMock(),client=Mock(return_value=channels))
        observed=dict(result='passed',error=None,pipeline_cleanup_confirmed=True,
                      reader_cleanup_confirmed=True,kernel_cleanup_confirmed=True)
        with patch.object(R,'AsyncKernelManager',return_value=manager), \
             patch.object(R,'NotebookClient',return_value=NS(kc=None)), \
             patch.object(R,'execute_owned',new=AsyncMock(return_value=observed)) as execute:
            result=asyncio.run(R.execute(directory,None,object(),train_lake=True))
        self.assertEqual(result['kernel_pid'],202)
        self.assertEqual({k:execute.call_args.kwargs[k] for k in ('pipeline','timeout','reader_grace')},
                         dict(pipeline=True,timeout=600,reader_grace=600))
        self.assertNotIn('SHIROKUMA_NOTEBOOK_BINARY',manager.start_kernel.call_args.kwargs['env'])

    def test_source_seal_includes_worker_pipeline_locks_and_registered_template(self):
        sources=R.train_sources()
        for name in ('notebook/train.py','notebook/runner.py','notebook/training.py','mlflow/uv.lock',
                     'mlflow/train.py','lake_training/run.py','lake_training/fixture.py','query_read/contract.py'):
            self.assertIn('tools/local_experiment/'+name,sources)
        self.assertIn('examples/local_lake_training.ipynb',sources)
        self.assertEqual(TN.arguments(self.root)[0],str(R.ROOT/'tools/local_experiment/mlflow/.venv/bin/python'))


class TrainRunnerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.directory=Path(temporary.name).resolve()
        self.kernel=FakeKernel();self.client=FakeNotebook(self.kernel)
        self.owner=dict(pid=303,ppid=202,pgid=303,process_start='Sun Sep 20 07:00:00 2026',
                        command=' '.join(TN.arguments(self.directory)))
        self.bind=self.enterContext(patch.object(R,'bind_pipeline',return_value=self.owner))
        self.allow=self.enterContext(patch.object(TN,'allow_launch'))
        self.closed=self.enterContext(patch.object(TN,'pipeline_closed',side_effect=lambda _:self.kernel.reader_cleaned))
        self.enterContext(patch.object(R,'reader_closed',side_effect=AssertionError('wrong Reader owner')))
        self.kill=self.enterContext(patch.object(R.os,'kill',side_effect=AssertionError('unexpected signal')))
        self.killpg=self.enterContext(patch.object(R.os,'killpg',side_effect=AssertionError('unexpected group signal')))

    async def execute(self,**kwargs):
        return await R.execute_owned(self.client,self.kernel,self.directory,pipeline=True,
                                     timeout=1,reader_grace=.2,kernel_grace=.1,**kwargs)

    async def test_pipeline_ack_once_then_cleanup_before_shutdown(self):
        self.client.complete=True
        result=await self.execute()
        self.assertEqual(result['result'],'passed');self.assertTrue(result['pipeline_cleanup_confirmed'])
        self.allow.assert_called_once_with(self.directory,self.owner)
        self.assertLess(self.kernel.calls.index('reader-cleaned'),self.kernel.calls.index(('shutdown',True)))
        self.kill.assert_not_called();self.killpg.assert_not_called()

    async def test_preexisting_stop_never_launches_pipeline_or_cell(self):
        self.kernel.reader_cleaned=True;cancel=R.NotebookInterrupted(signal.SIGTERM)
        result=await self.execute(stop=dict(reason=cancel))
        self.assertIs(result['error'],cancel);self.allow.assert_not_called();self.bind.assert_not_called()
        self.assertNotIn('cell',self.kernel.calls)

    async def test_expired_deadline_never_publishes_ack(self):
        result=await R.execute_owned(self.client,self.kernel,self.directory,pipeline=True,
                                     timeout=0,reader_grace=.2,kernel_grace=.1)
        self.assertIsInstance(result['error'],TimeoutError);self.allow.assert_not_called()

    async def test_signal_keeps_first_object_and_waits_for_pipeline_cleanup(self):
        cancel=R.NotebookInterrupted(signal.SIGTERM);stop=dict(reason=None)
        self.kernel.interrupt_error=OSError('secondary interruption')
        task=asyncio.create_task(self.execute(stop=stop));await self.kernel.started.wait()
        stop['reason']=cancel;result=await task
        self.assertIs(result['error'],cancel);self.assertEqual(result['result'],'interrupted')
        self.assertTrue(result['pipeline_cleanup_confirmed']);self.assertEqual(self.kernel.calls.count('interrupt'),1)
        self.assertLess(self.kernel.calls.index('reader-cleaned'),self.kernel.calls.index(('shutdown',True)))

    async def test_unknown_live_pipeline_prevents_kernel_shutdown(self):
        self.client.stalled=True
        result=await R.execute_owned(self.client,self.kernel,self.directory,pipeline=True,
                                     timeout=.01,reader_grace=.02,kernel_grace=.01)
        self.assertEqual(result['result'],'unknown');self.assertFalse(result['pipeline_cleanup_confirmed'])
        self.assertFalse(result['kernel_cleanup_confirmed'])
        self.assertFalse(any(isinstance(x,tuple) and x[0]=='shutdown' for x in self.kernel.calls))
        self.kill.assert_not_called();self.killpg.assert_not_called()

    async def test_ack_failure_retains_bound_owner_for_dead_kernel_orphan(self):
        self.kernel.alive=False
        cancel=R.NotebookInterrupted(signal.SIGINT)
        self.allow.side_effect=cancel
        with patch.object(R,'interrupt_orphan',new=AsyncMock(return_value=True)) as orphan:
            result=await R.execute_owned(self.client,self.kernel,self.directory,pipeline=True,
                                         timeout=1,reader_grace=.01,kernel_grace=.01)
        self.assertIs(result['error'],cancel);self.assertEqual(result['result'],'unknown')
        orphan.assert_awaited_once_with(self.owner,timeout=.01)
        self.assertFalse(result['pipeline_cleanup_confirmed'])

    def test_live_binding_requires_kernel_parent_group_start_and_fixed_argv(self):
        saved=dict(state='running',pid=303,pgid=303,process_start=self.owner['process_start'])
        # Use the original function while the lifecycle tests' binding mock is active.
        original=ORIGINAL_BIND
        with patch.object(TN,'pipeline_status',return_value=saved),patch.object(R,'process_identity',return_value=self.owner):
            self.assertEqual(original(self.directory,202),self.owner)
        for key,value in [('ppid',999),('pgid',999),('process_start','changed'),('command','other command')]:
            with self.subTest(key=key),patch.object(TN,'pipeline_status',return_value=saved), \
                 patch.object(R,'process_identity',return_value=dict(self.owner,**{key:value})):
                self.assertIsNone(original(self.directory,202))
        self.allow.assert_not_called()


ORIGINAL_BIND=R.bind_pipeline
if __name__=='__main__':unittest.main()
