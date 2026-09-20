"""One gated training child with fake processes; no Reader/fit/SDK/services."""
from copy import deepcopy
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.notebook import train as C
from test_mlflow_predict import saved_review

PID, PPID, START = 43210, 43200, 'Sun Sep 20 10:00:00 2026'


class Cancel(BaseException):
    pass


class Child:
    pid = PID
    def __init__(self, owner, stdout):
        self.owner, self.stdout, self.returncode = owner, stdout, None
    def wait(self, timeout):
        self.owner.waits.append(timeout)
        if self.owner.wait_error is not None:
            error, self.owner.wait_error = self.owner.wait_error, None
            raise error
        self.owner.finish(self)
        return self.returncode
    def poll(self): return self.returncode


class TrainClientTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.state = self.base/'notebooks'; self.state.mkdir(mode=0o700)
        self.lake = self.base/'lake'; self.lake.mkdir(mode=0o700)
        self.directory = self.state/('a'*32); self.directory.mkdir(mode=0o700)
        self.review, _ = saved_review()
        self.waits, self.calls, self.signals = [], [], []
        self.wait_error = None
        self.terminal_result, self.terminal_exit, self.closed, self.authorize = 'passed', 0, True, True
        self.extra_frame = False
        for target, value in [('NOTEBOOK_STATE',self.state),('LAKE_STATE',self.lake)]:
            item=patch.object(C,target,value); item.start(); self.addCleanup(item.stop)
        env=patch.dict(os.environ,{'SHIROKUMA_NOTEBOOK_RUN_DIR':str(self.directory),
            'SHIROKUMA_NOTEBOOK_BINARY':'UNTRUSTED','SHIROKUMA_NOTEBOOK_REQUEST_PATH':'UNTRUSTED'},clear=False)
        env.start(); self.addCleanup(env.stop)
        item=patch.object(C.N,'_process_start',return_value=START); item.start(); self.addCleanup(item.stop)
        item=patch.object(C.subprocess,'Popen',side_effect=self.spawn); item.start(); self.addCleanup(item.stop)
        item=patch.object(C.os,'kill',side_effect=self.kill); item.start(); self.addCleanup(item.stop)
        item=patch.object(C,'group_empty',return_value=True)
        self.empty=item.start(); self.addCleanup(item.stop)
        item=patch.object(C.T,'load_saved_review',return_value=self.review)
        self.loader=item.start(); self.addCleanup(item.stop)
        item=patch.object(C.T,'_get',side_effect=AssertionError('HTTP prohibited'))
        item.start(); self.addCleanup(item.stop)

    def owner(self):
        return dict(pid=PID,ppid=PPID,pgid=PID,process_start=START,
                    command=' '.join(C.arguments(self.directory)))

    def spawn(self, argv, **kwargs):
        self.calls.append((argv,kwargs))
        self.child=Child(self,kwargs['stdout'])
        return self.child

    def terminal(self, child):
        token=self.review['lake_source']['source_token']
        output=self.lake/token; output.mkdir(mode=0o700,exist_ok=True)
        summary=dict(schema_version=1,profile='fixed-iceberg-training-pipeline-v1',source_token=token,
            result=self.terminal_result,phase='complete',store_id=self.review['identity']['store_id'],
            query_evidence_token=self.review['lake_source']['query_evidence_token'],
            tracking={key:self.review['identity'][key] for key in ('attempt_token','run_id','experiment_id','workload_sha256')},
            reader_cleanup_confirmed=self.closed,fixture_cleanup_confirmed=True,
            tracker_cleanup_confirmed=True,runtime_restored=True)
        owner=dict(schema_version=1,profile=summary['profile'],result=self.terminal_result,
                   summary_result=summary['result'],cleanup_confirmed=self.closed,
                   error_code=None if self.terminal_result=='passed' else 'LAKE_PIPELINE_FAILED')
        C.N._write(output/'summary.json',summary)
        C.N._write(output/'owner-result.json',owner)
        frame=dict(result=self.terminal_result,cleanup_confirmed=self.closed,
                   evidence='tmp/local-experiment/lake-training/'+token+'/owner-result.json')
        child.stdout.write(C.N._canonical(frame)+(b'{}\n' if self.extra_frame else b''))
        child.returncode=self.terminal_exit

    def finish(self, child):
        if self.authorize:
            if not (self.directory/'pipeline-launch.json').exists(): C.allow_launch(self.directory,self.owner())
            self.terminal(child)
        else: child.returncode=1

    def kill(self,pid,sig):
        self.signals.append((pid,sig))
        self.terminal_result,self.terminal_exit='interrupted',130
        self.finish(self.child)

    def initial(self):
        C.N._write(self.directory/'pipeline-intent.json',dict(schema_version=1,profile=C.PROFILE,
                                                             argv=C.arguments(self.directory)))
        status=dict(schema_version=1,state='running',pid=PID,pgid=PID,process_start=START,evidence_token=None,
                    exit_code=None,child_exited=False,cleanup_verified=False,error_code=None)
        C.N._write(self.directory/'pipeline-status.json',status)

    def test_passed_child_bound_to_saved_review_and_private_result(self):
        public=C.train_lake()
        self.assertEqual(public,self.review)
        self.assertEqual(C._json(self.directory/'training-review.json'),self.review)
        self.assertTrue(C.pipeline_closed(self.directory))
        self.assertEqual(C.pipeline_status(self.directory)['evidence_token'],self.review['lake_source']['source_token'])
        self.loader.assert_called_once_with(self.review['identity']['attempt_token'],kind='iceberg')
        self.assertEqual(self.waits,[600]); self.assertEqual(len(self.calls),1)
        argv,kw=self.calls[0]
        self.assertEqual(argv,C.arguments(self.directory)); self.assertIn('/mlflow/.venv/bin/python',argv[0])
        self.assertTrue(kw['start_new_session']); self.assertEqual(kw['stdin'],subprocess.DEVNULL)
        self.assertFalse(any(key.startswith('SHIROKUMA_') for key in kw['env']))
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in self.directory.iterdir()))
        with self.assertRaises(FileExistsError): C.train_lake()
        self.assertEqual(len(self.calls),1)

    def test_no_launch_permission_is_closed_without_training(self):
        self.authorize=False
        with self.assertRaisesRegex(C.N.NotebookError,'NOTEBOOK_TRAIN_FAILED'): C.train_lake()
        status=C.pipeline_status(self.directory)
        self.assertEqual(status['state'],'failed'); self.assertIsNone(status['evidence_token'])
        self.assertTrue(C.pipeline_closed(self.directory)); self.loader.assert_not_called()
        self.assertEqual(list(self.lake.iterdir()),[])

    def test_terminal_failure_preserves_cleanup_and_releases_no_review(self):
        self.terminal_result,self.terminal_exit='failed',1
        with self.assertRaises(C.N.NotebookError): C.train_lake()
        self.assertTrue(C.pipeline_closed(self.directory)); self.loader.assert_not_called()
        self.assertFalse((self.directory/'training-review.json').exists())

    def test_cleanup_unknown_is_not_passed_or_retried(self):
        self.terminal_result,self.terminal_exit,self.closed='unknown',1,False
        with self.assertRaisesRegex(C.N.NotebookError,'CLEANUP_UNKNOWN'): C.train_lake()
        self.assertFalse(C.pipeline_closed(self.directory)); self.assertEqual(len(self.calls),1)
        self.assertEqual(C.pipeline_status(self.directory)['state'],'unknown')
        self.loader.assert_not_called()

    def test_reaped_worker_with_remaining_group_is_unknown(self):
        self.empty.return_value=False
        with self.assertRaisesRegex(C.N.NotebookError,'CLEANUP_UNKNOWN'): C.train_lake()
        self.assertEqual(C.pipeline_status(self.directory)['state'],'unknown')
        self.assertFalse(C.pipeline_closed(self.directory)); self.loader.assert_not_called()
        self.assertFalse((self.directory/'training-review.json').exists())

    def test_early_pipeline_build_failure_without_effects_is_closed(self):
        def before_pipeline(child):
            C.allow_launch(self.directory,self.owner())
            token=self.review['lake_source']['source_token']
            output=self.lake/token; output.mkdir(mode=0o700)
            C.N._write(output/'owner-result.json',dict(schema_version=1,
                profile='fixed-iceberg-training-pipeline-v1',result='failed',summary_result=None,
                cleanup_confirmed=True,error_code='LAKE_PIPELINE_FAILED'))
            child.stdout.write(C.N._canonical(dict(result='failed',cleanup_confirmed=True,
                evidence='tmp/local-experiment/lake-training/'+token+'/owner-result.json')))
            child.returncode=1
        self.finish=before_pipeline
        with self.assertRaises(C.N.NotebookError): C.train_lake()
        self.assertTrue(C.pipeline_closed(self.directory)); self.loader.assert_not_called()
        token=C.pipeline_status(self.directory)['evidence_token']
        C.N._write(self.lake/token/'pipeline-intent.json',{})
        with self.assertRaises(C.N.NotebookError): C.pipeline_closed(self.directory)

    def test_pre_wait_failure_reaps_no_ack_child_without_false_unknown(self):
        self.authorize=False
        with patch.object(C.N,'_process_start',side_effect=OSError('before wait')):
            with self.assertRaises(C.N.NotebookError): C.train_lake()
        self.assertTrue(C.pipeline_closed(self.directory))
        self.assertEqual(self.signals,[(PID,signal.SIGINT)]); self.loader.assert_not_called()

    def test_extra_frame_or_nonzero_pass_is_rejected(self):
        self.extra_frame=True
        with self.assertRaises(C.N.NotebookError): C.train_lake()
        self.assertFalse(C.pipeline_closed(self.directory)); self.loader.assert_not_called()

    def test_saved_review_source_mismatch_refuses_output_but_keeps_known_cleanup(self):
        altered=deepcopy(self.review); altered['lake_source']['query_evidence_token']='2'*32
        self.loader.return_value=altered
        with self.assertRaises(C.N.NotebookError): C.train_lake()
        self.assertTrue(C.pipeline_closed(self.directory))
        self.assertFalse((self.directory/'training-review.json').exists())

    def test_first_cancel_survives_closed_pipeline_and_secondary_status_failure(self):
        original=Cancel('first'); self.wait_error=original
        write=C.N._write
        def fail_final(path,value,**kwargs):
            if path.name=='pipeline-status.json' and value['state']=='interrupted': raise OSError('secondary')
            return write(path,value,**kwargs)
        with patch.object(C.N,'_write',side_effect=fail_final),self.assertRaises(Cancel) as caught:
            C.train_lake()
        self.assertIs(caught.exception,original)
        self.assertEqual(self.signals,[(PID,signal.SIGINT)])
        self.loader.assert_not_called(); self.assertFalse((self.directory/'training-review.json').exists())

    def test_timeout_signals_once_and_keeps_closed_failure(self):
        self.wait_error=subprocess.TimeoutExpired('fixed',600)
        with self.assertRaises(C.N.NotebookError): C.train_lake()
        self.assertEqual(self.signals,[(PID,signal.SIGINT)])
        self.assertTrue(C.pipeline_closed(self.directory)); self.assertEqual(len(self.calls),1)

    def test_worker_requires_parent_ack_before_any_pipeline_call(self):
        self.initial(); pipeline=Mock(return_value=0)
        with patch.object(C.time,'monotonic',side_effect=[0,31]):
            with self.assertRaisesRegex(C.N.NotebookError,'NOT_AUTHORIZED'):
                C.worker(self.directory,pipeline=pipeline)
        pipeline.assert_not_called(); self.assertEqual(list(self.lake.iterdir()),[])

    def test_worker_accepts_exact_live_identity_then_invokes_unchanged_main(self):
        self.initial(); C.allow_launch(self.directory,self.owner()); pipeline=Mock(return_value=0)
        with patch.object(C.os,'getpid',return_value=PID),patch.object(C.os,'getppid',return_value=PPID),\
             patch.object(C.os,'getpgrp',return_value=PID):
            self.assertEqual(C.worker(self.directory,pipeline=pipeline),0)
        pipeline.assert_called_once_with([])

    def test_changed_ack_or_parent_is_refused_before_pipeline(self):
        self.initial(); C.allow_launch(self.directory,self.owner()); pipeline=Mock()
        with patch.object(C.os,'getpid',return_value=PID),patch.object(C.os,'getppid',return_value=PPID+1),\
             patch.object(C.os,'getpgrp',return_value=PID),self.assertRaises(C.N.NotebookError):
            C.worker(self.directory,pipeline=pipeline)
        pipeline.assert_not_called()

    def test_symlink_directory_and_unknown_worker_arguments_refused(self):
        alias=self.state/('b'*32); alias.symlink_to(self.directory,target_is_directory=True)
        with self.assertRaises(C.N.NotebookError): C.pipeline_status(alias)
        with patch.object(C,'worker') as worker:
            self.assertEqual(C.main(['--worker',str(self.directory),'--retry']),1)
            worker.assert_not_called()


if __name__=='__main__': unittest.main()
