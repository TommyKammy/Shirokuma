"""Fake-only local Ray lifecycle and loopback/ownership boundaries."""
from copy import deepcopy
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.ray import run as R
from tests.test_local_ray_prediction import FakeRay, TOKEN
from tests.test_mlflow_predict import saved_review


def row(pid, ppid, pgid=None, command='fixed worker'):
    return dict(pid=pid,ppid=ppid,pgid=pid if pgid is None else pgid,uid=os.getuid(),effective_uid=os.getuid(),
                start_time='Sun Sep 20 12:00:00 2026',state='S',command=command)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve(); self.state=self.root/'runs';self.state.mkdir(mode=0o700)
        self.directory=self.state/('b'*32);self.directory.mkdir(mode=0o700)
        self.short=self.root/'short';self.short.mkdir(mode=0o700)
        for name,value in [('STATE',self.state),('_short',lambda directory:self.short)]:
            p=patch.object(R,name,value);p.start();self.addCleanup(p.stop)
        request=dict(schema_version=1,training_token=TOKEN)
        R.N._write(self.directory/'input.json',request)
        R.N._write(self.directory/'intent.json',dict(schema_version=1,profile=R.C.PROFILE,
            argv=R.arguments(self.directory),input_sha256=R.C.P.digest(request),short_temp=str(self.short)))
        self.leader=row(os.getpid(),os.getppid());self.rows={os.getpid():self.leader}
        R.N._write(self.directory/'launch.json',dict(R.identity(self.leader),ppid=os.getppid()))
        p=patch.object(R,'processes',side_effect=lambda:deepcopy(self.rows));p.start();self.addCleanup(p.stop)
        p=patch.object(R.C.T,'load_saved_review',return_value={});p.start();self.addCleanup(p.stop)
        self.ray=Mock(__version__=R.C.VERSION,__commit__=R.C.COMMIT)
        self.ray.is_initialized.return_value=False
        self.ray._common.network_utils.get_localhost_ip.return_value='127.0.0.1'
        self.ray.init.side_effect=self.started
        self.ray.shutdown.side_effect=self.stopped
        p=patch.object(R,'listeners',return_value=[{'pid':60001,'address':'127.0.0.1:12345'}]);p.start();self.addCleanup(p.stop)
        p=patch.object(R.C,'evaluate',side_effect=self.evaluated);p.start();self.addCleanup(p.stop)

    def started(self,**kwargs):
        (self.short/'r').mkdir(exist_ok=True)
        (self.short/'r').chmod(0o777)  # Pinned Ray shares its temp root.
        self.rows.update({60001:row(60001,os.getpid()),60002:row(60002,60001),60003:row(60003,60001)})
    def stopped(self,**kwargs): self.rows={os.getpid():self.leader}
    def evaluated(self,token,ray,inspect):
        inspect([60002,60003]);return {'training_token':token,'predictions':[0.0]*64}

    def test_explicit_local_resources_loopback_metrics_off_shutdown_wait_and_cross_group_cleanup(self):
        self.assertEqual(R.worker(self.directory,ray_module=self.ray),0)
        self.ray.init.assert_called_once_with(address='local',num_cpus=2,num_gpus=0,include_dashboard=False,
            dashboard_host='127.0.0.1',
            object_store_memory=128*1024**2,_node_ip_address='127.0.0.1',_temp_dir=str(self.short/'r'),
            log_to_driver=False,_system_config={'enable_metrics_collection':False})
        self.ray.shutdown.assert_called_once_with(wait_for_processes=True)
        result=R._json(self.directory/'worker-result.json')
        self.assertEqual(result['result'],'passed');self.assertTrue(result['cleanup_verified'])
        processes=R._json(self.directory/'processes.json')
        self.assertEqual({p['pid'] for p in processes},{60001,60002,60003})
        self.assertEqual({p['pgid'] for p in processes},{60001,60002,60003})
        self.assertEqual(self.short.stat().st_mode & 0o7777,0o700)
        self.assertEqual((self.short/'r').stat().st_mode & 0o7777,0o777)

    def test_partial_init_is_shutdown_and_residual_actor_prevents_success(self):
        def failed(**kwargs): self.started();raise ValueError('private initialization body')
        self.ray.init.side_effect=failed
        self.ray.shutdown.side_effect=lambda **kwargs:None
        self.assertEqual(R.worker(self.directory,ray_module=self.ray),1)
        value=R._json(self.directory/'worker-result.json')
        self.assertEqual(value['result'],'unknown');self.assertFalse(value['cleanup_verified'])
        self.assertIsNone(value['observation'])
        self.assertNotIn('private initialization',json.dumps(value))
        self.ray.shutdown.assert_called_once()

    def test_first_interrupt_survives_shutdown_failure_and_never_retries(self):
        first=KeyboardInterrupt('first')
        with patch.object(R.C,'evaluate',side_effect=first):
            self.ray.shutdown.side_effect=OSError('second')
            with self.assertRaises(KeyboardInterrupt) as caught:R.worker(self.directory,ray_module=self.ray)
        self.assertIs(caught.exception,first)
        self.assertIsNone(R._json(self.directory/'worker-result.json')['observation'])
        self.assertEqual(self.ray.init.call_count,1)

    def test_unexpected_version_or_nonloopback_resolution_never_initializes_ray(self):
        self.ray._common.network_utils.get_localhost_ip.return_value='192.0.2.1'
        self.assertEqual(R.worker(self.directory,ray_module=self.ray),1)
        self.ray.init.assert_not_called();self.ray.shutdown.assert_not_called()
        self.assertEqual(R._json(self.directory/'worker-result.json')['error_code'],'RAY_LISTENER_INVALID')

    def test_other_build_of_same_version_is_refused_before_ray_init(self):
        self.ray.__commit__='01b49e4fa77b30bc28c69ed702dfe523fd6421d8'
        self.assertEqual(R.worker(self.directory,ray_module=self.ray),1)
        self.ray.init.assert_not_called();self.ray.shutdown.assert_not_called()
        self.assertEqual(R._json(self.directory/'worker-result.json')['error_code'],'RAY_RUNTIME_VERSION')

    def test_environment_is_child_only_and_excludes_cluster_cloud_or_code_hooks(self):
        before=dict(os.environ)
        with patch.dict(os.environ,{'RAY_ADDRESS':'external:10001','RAY_RUNTIME_ENV_HOOK':'evil',
                                   'AWS_SECRET_ACCESS_KEY':'secret','PYTHONSTARTUP':'evil'}):
            current=dict(os.environ);env=R.environment(self.directory)
            self.assertEqual(os.environ,current)
        self.assertEqual(os.environ,before)
        for key in ('HOME','RAY_ADDRESS','RAY_RUNTIME_ENV_HOOK','AWS_SECRET_ACCESS_KEY','PYTHONSTARTUP'):
            self.assertNotIn(key,env)
        self.assertEqual(env['RAY_USAGE_STATS_ENABLED'],'0')
        self.assertEqual(env['PYTHONPATH'],str(R.ROOT))


class BoundaryTests(unittest.TestCase):
    def test_real_uid_parser_and_cross_observer_setuid_ps_tracking(self):
        uid=os.getuid()
        raw=(f'60001 1 60001 {uid} {uid} S Sun Sep 20 12:00:00 2026 fixed worker\n'
             f'60002 60001 60001 {uid} 0 R Sun Sep 20 12:00:00 2026 '
             + ' '.join(R.PS_ARGV) + '\n').encode()
        with patch.object(R.M,'_command',return_value=SimpleNamespace(returncode=0,stdout=raw)) as command:
            rows=R.processes()
        command.assert_called_once_with(R.PS_ARGV)
        self.assertEqual(rows[60002]['uid'],uid)
        self.assertEqual(rows[60002]['effective_uid'],0)
        known={};leader=R.identity(rows[60001])
        # The parent sees the worker's simultaneous inspection; the same rule
        # also handles an inspector observed by its direct worker parent.
        R.capture(leader,known,rows)
        self.assertEqual(known[60002]['uid'],uid)
        self.assertFalse(R.gone(known,rows))
        self.assertTrue(R.gone(known,{60001:rows[60001]}))
        R.capture(leader,known,rows)  # A known inspector remains narrowly valid.

    def test_privileged_noninspector_foreign_real_uid_and_inspector_leader_are_rejected(self):
        parent=row(60001,1)
        ps=dict(row(60002,60001),effective_uid=0,command=' '.join(R.PS_ARGV))
        for child in (dict(ps,uid=os.getuid()+1),dict(ps,command='/bin/ps -ax'),
                      dict(ps,command='/usr/sbin/lsof -nP'),dict(ps,effective_uid=os.getuid()+1)):
            with self.subTest(child=child):
                with self.assertRaisesRegex(R.C.RayPredictionError,'OWNERSHIP'):
                    R.capture(R.identity(parent),{},{60001:parent,60002:child})
        with self.assertRaisesRegex(R.C.RayPredictionError,'OWNERSHIP'):R.identity(ps)
        known={}
        R.capture(R.identity(parent),known,{60001:parent,60002:dict(ps,ppid=12345)})
        self.assertEqual(known,{})  # A matching unrelated ps is not adopted.
        for bad in (dict(R.identity(parent),uid=True),dict(R.identity(parent),effective_uid=0)):
            with self.assertRaisesRegex(R.C.RayPredictionError,'OWNERSHIP'):R.saved_identity(bad)

    def test_known_setuid_inspector_zombie_is_dead_but_unknown_or_reused_identity_is_refused(self):
        parent=row(60001,1);leader=R.identity(parent)
        ps=dict(row(60002,60001),effective_uid=0,command=' '.join(R.PS_ARGV))
        known={};R.capture(leader,known,{60001:parent,60002:ps})
        zombie=dict(ps,state='Z',command='<defunct>')
        rows={60001:parent,60002:zombie}
        R.capture(leader,known,rows)
        self.assertTrue(R.gone(known,rows))
        with self.assertRaisesRegex(R.C.RayPredictionError,'OWNERSHIP'):
            R.capture(leader,{},rows)
        for key,value in [('start_time','later'),('pgid',60003),('uid',os.getuid()+1)]:
            changed={60001:parent,60002:dict(zombie,**{key:value})}
            with self.subTest(key=key):
                with self.assertRaisesRegex(R.C.RayPredictionError,'OWNERSHIP'):R.capture(leader,known,changed)
                with self.assertRaisesRegex(R.C.RayPredictionError,'OWNERSHIP'):R.gone(known,changed)

    def test_listener_wildcard_ipv6_wildcard_foreign_pid_and_valid_loopbacks(self):
        for raw,valid in [(b'p60001\nn127.0.0.1:12345\n',True),(b'p60001\nn[::1]:12345\n',True),
                          (b'p60001\nn*:12345\n',False),(b'p60001\nn0.0.0.0:12345\n',False),
                          (b'p60001\nn[::]:12345\n',False),(b'p60002\nn127.0.0.1:12345\n',False)]:
            with self.subTest(raw=raw),patch.object(R.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=raw)):
                if valid:self.assertEqual(len(R.listeners({60001})),1)
                else:
                    with self.assertRaisesRegex(R.C.RayPredictionError,'LISTENER'):R.listeners({60001})

    def test_descendant_new_session_is_owned_but_reused_pid_never_signalled(self):
        leader=R.identity(row(60001,1));known={}
        rows={60001:row(60001,1),60002:row(60002,60001),60003:row(60003,60002)}
        R.capture(leader,known,rows)
        self.assertEqual(set(known),{60002,60003})
        self.assertFalse(R.gone(known,{60003:rows[60003]}))
        self.assertTrue(R.gone(known,{}))
        reused=dict(rows[60001],start_time='later');child=Mock(pid=60001);child.poll.return_value=None
        first=KeyboardInterrupt('original')
        with patch.object(R,'processes',return_value={60001:reused}),patch.object(R.os,'kill') as kill, \
             patch.object(R.time,'monotonic',side_effect=[0,100]):
            self.assertIs(R._interrupt(child,leader,known,first),first)
        kill.assert_not_called()

    def test_bad_cli_never_allocates_or_loads_sources_and_never_prints_raw_errors(self):
        for argv in ([],['--address','external'],['--training-token',TOKEN,'extra']):
            output=io.StringIO()
            with patch.object(R,'run') as run:
                self.assertEqual(R.main(argv,stdout=output),1)
            run.assert_not_called();self.assertEqual(len(output.getvalue().splitlines()),1)
            self.assertEqual(json.loads(output.getvalue())['error_code'],'RAY_ARGUMENTS_INVALID')
        output=io.StringIO()
        with patch.object(R,'run',side_effect=ValueError('secret raw body')):
            self.assertEqual(R.main(['--training-token',TOKEN],stdout=output),1)
        self.assertNotIn('secret',output.getvalue())


class ParentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='srt-',dir='/private/tmp' if Path('/private/tmp').exists() else '/tmp')
        self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()
        self.state=self.root/'runs';self.state.mkdir(mode=0o700)
        self.directory=self.state/('b'*32);self.short=self.root/'s'
        for name,value in [('ROOT',self.root),('STATE',self.state),('SHORT_ROOT',self.root),
                           ('_short',lambda directory:self.short)]:
            p=patch.object(R,name,value);p.start();self.addCleanup(p.stop)
        p=patch.object(R.uuid,'uuid4',return_value=SimpleNamespace(hex='b'*32));p.start();self.addCleanup(p.stop)
        review,features=saved_review()
        p=patch.object(R.C.T,'load_saved_review',return_value=review);p.start();self.addCleanup(p.stop)
        request={'schema_version':1,'rows':[dict(zip(R.C.P.FEATURES,values)) for values in features]*4}
        baseline=R.C.P.predict(TOKEN,request)
        parts=[]
        for index in range(2):
            batch={'schema_version':1,'rows':request['rows'][index*32:(index+1)*32]}
            value=R.C.P.predict(TOKEN,batch)
            parts.append(dict(partition=index,pid=50001+index,**{key:value[key] for key in
                ('request_sha256','identity','lake_source','model_sha256','predictions')}))
        self.public=R.C.validate_partitions(parts,request,baseline)
        @contextmanager
        def saved(token):yield deepcopy(request),deepcopy(baseline)
        p=patch.object(R.C,'saved_batch',side_effect=saved);p.start();self.addCleanup(p.stop)
        self.child=Mock(pid=60000);self.child.returncode=None
        self.child.poll.side_effect=lambda:self.child.returncode
        self.first=None;self.waited=0;self.residual=False
        def wait(timeout):
            self.waited+=1
            if self.first is not None and self.waited==1:raise self.first
            self.child.returncode=130 if self.first is not None else 0
            R.N._write(self.directory/'processes.json',[R.identity(row(pid,60000)) for pid in (50001,50002)])
            R.N._write(self.directory/'worker-result.json',dict(schema_version=1,profile=R.C.PROFILE,
                result='interrupted' if self.first is not None else 'passed',cleanup_verified=True,
                error_code='RAY_INTERRUPTED' if self.first is not None else None,
                observation=None if self.first is not None else self.public,
                listeners=[{'pid':50001,'address':'127.0.0.1:12345'}]))
        self.child.wait.side_effect=wait
        def processes():
            if self.child.returncode is None:
                return {60000:row(60000,os.getpid(),command=' '.join(R.arguments(self.directory)))}
            return {50001:row(50001,1)} if self.residual else {}
        p=patch.object(R,'processes',side_effect=processes);p.start();self.addCleanup(p.stop)
        p=patch.object(R.subprocess,'Popen',return_value=self.child);self.spawn=p.start();self.addCleanup(p.stop)
        p=patch.object(R.os,'kill');self.kill=p.start();self.addCleanup(p.stop)

    def test_parent_rechecks_source_and_exited_actor_pids_before_returning_predictions(self):
        public=R.run(TOKEN)
        self.assertEqual(public['predictions'],self.public['predictions'])
        self.assertTrue(public['cleanup_verified']);self.assertEqual(public['result'],'passed')
        self.spawn.assert_called_once();self.kill.assert_not_called()
        self.assertEqual(self.spawn.call_args.kwargs['env'],R.environment(self.directory))
        self.assertTrue(self.spawn.call_args.kwargs['start_new_session'])
        status=R._json(self.directory/'status.json')
        self.assertTrue(status['child_exited']);self.assertTrue(status['cleanup_verified'])

    def test_parent_first_cancel_sends_only_worker_interrupt_and_preserves_first(self):
        self.first=KeyboardInterrupt('first')
        with self.assertRaises(KeyboardInterrupt) as caught:R.run(TOKEN)
        self.assertIs(caught.exception,self.first)
        self.kill.assert_called_once_with(60000,signal.SIGINT)
        status=R._json(self.directory/'status.json')
        self.assertEqual(status['result'],'interrupted');self.assertTrue(status['cleanup_verified'])

    def test_orphan_actor_is_unknown_and_prevents_a_new_attempt(self):
        self.residual=True
        with self.assertRaisesRegex(R.C.RayPredictionError,'RAY_CLEANUP_UNKNOWN'):R.run(TOKEN)
        status=R._json(self.directory/'status.json')
        self.assertEqual(status['result'],'unknown');self.assertFalse(status['cleanup_verified'])
        with self.assertRaisesRegex(R.C.RayPredictionError,'RAY_CLEANUP_UNKNOWN'):R.run(TOKEN)
        self.spawn.assert_called_once();self.kill.assert_not_called()


if __name__=='__main__':unittest.main()
