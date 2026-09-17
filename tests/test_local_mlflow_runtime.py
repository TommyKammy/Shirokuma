"""No MLflow imports, server launches, or retained store writes in these tests."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('local_mlflow_runtime', ROOT/'tools/local_experiment/mlflow/runtime.py')
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        state = self.root/'state'
        venv = self.root/'venv'
        (venv/'bin').mkdir(parents=True)
        (venv/'bin/python').touch()
        package = venv/'lib/python3.12/site-packages'
        (package/'mlflow-3.16.1.dist-info').mkdir(parents=True)
        (package/'mlflow/server').mkdir(parents=True)
        (package/'mlflow/server/uvicorn_log_config.yaml').touch()
        self.patches = [patch.object(R,k,v) for k,v in {
            'STATE':state,'STORE':state/'store','ARTIFACT_ROOT':state/'store/artifacts',
            'VENV':venv,'VENV_PYTHON':venv/'bin/python',
            'BACKEND_URI':'sqlite:///'+str(state/'store/tracking.sqlite')}.items()]
        for p in self.patches:p.start()
        self.addCleanup(self.temp.cleanup)
        for p in self.patches:self.addCleanup(p.stop)
        self.listener = patch.object(R,'listeners',return_value=[]).start()
        # Offline tests must not bind the real port when the owned server runs.
        self.socket = patch.object(R.socket,'socket').start()
        self.addCleanup(patch.stopall)

    def prepare(self):
        return R.prepare()

    def leader(self):
        return {'pid':9001,'ppid':10,'pgid':9001,'uid':os.getuid(),
                'start_time':'Thu Sep 17 01:00:00 2026','command':' '.join(R.server_argv())}

    def worker(self):
        return {'pid':9002,'ppid':9001,'pgid':9001,'uid':os.getuid(),
                'start_time':'Thu Sep 17 01:00:01 2026','command':' '.join(R.worker_argv())}

    def record(self):
        value={'schema_version':1,'store_id':R.require_store()['store_id'],
               'argv':R.server_argv(),'leader':self.leader(),'members':[self.leader(),self.worker()]}
        R.write_private(R.STATE/'runtime.json',value)
        return value

    def test_status_uninitialized_does_not_create_store(self):
        self.assertEqual(R.status()['state'],'uninitialized')
        self.assertFalse(R.STATE.exists())

    def test_dangling_state_symlink_is_not_uninitialized(self):
        R.STATE.symlink_to(self.root/'missing')
        with self.assertRaisesRegex(R.RuntimeError,'SYMLINK'):R.status()

    def test_pending_start_blocks_import_even_without_listener(self):
        self.prepare()
        R.write_private(R.STATE/'start-intent.json',{'schema_version':1,
            'store_id':R.require_store()['store_id'],'launch_id':'12345678-1234-1234-1234-123456789abc',
            'state':'pending'})
        self.assertEqual(R.status()['state'],'unknown')
        with self.assertRaisesRegex(R.RuntimeError,'SERVER_NOT_STOPPED'):R.require_stopped()

    def test_prepare_is_idempotent_and_preserves_database(self):
        first=self.prepare()
        database=R.STORE/'tracking.sqlite'
        database.write_bytes(b'retained sqlite bytes')
        database.chmod(0o600)
        before=database.stat()
        self.assertEqual(R.prepare(),first)
        self.assertEqual(database.read_bytes(),b'retained sqlite bytes')
        self.assertEqual(database.stat().st_mtime_ns,before.st_mtime_ns)
        self.assertEqual((R.STATE/'store.json').stat().st_mode&0o777,0o600)
        self.assertEqual(R.ARTIFACT_ROOT.stat().st_mode&0o777,0o700)

    def test_unmarked_existing_store_is_not_adopted(self):
        R.STATE.mkdir(mode=0o700)
        (R.STATE/'tracking.sqlite').write_bytes(b'foreign')
        with self.assertRaisesRegex(R.RuntimeError,'UNOWNED_STORE_CONTENT'):R.prepare()

    def test_marker_owner_conflict(self):
        self.prepare()
        value=R.require_store();value['owner']='another-project'
        R.write_private(R.STATE/'store.json',value)
        with self.assertRaisesRegex(R.RuntimeError,'STORE_MARKER_CONFLICT'):R.require_store()

    def test_symlink_and_hardlink_refused(self):
        self.prepare()
        target=self.root/'target';target.write_bytes(b'x');target.chmod(0o600)
        database=R.STORE/'tracking.sqlite';database.symlink_to(target)
        with self.assertRaisesRegex(R.RuntimeError,'SYMLINK'):R.require_store()
        database.unlink();os.link(target,database)
        with self.assertRaisesRegex(R.RuntimeError,'UNSAFE_DATABASE'):R.require_store()

    def test_public_database_permissions_refused(self):
        self.prepare();database=R.STORE/'tracking.sqlite';database.touch(mode=0o644)
        with self.assertRaisesRegex(R.RuntimeError,'UNSAFE_DATABASE'):R.require_store()

    def test_common_lock_rejects_parallel_import_or_start(self):
        self.prepare()
        with R.store_lock():
            with self.assertRaisesRegex(R.RuntimeError,'STORE_BUSY'):
                with R.store_lock():pass

    def test_environment_drops_parent_credentials_and_proxy(self):
        with patch.dict(os.environ,{'AWS_SECRET_ACCESS_KEY':'do-not-copy','OPENAI_API_KEY':'do-not-copy',
                                    'HTTP_PROXY':'bad','DOCKER_HOST':'bad','PYTHONPATH':'bad','HOME':'bad'}):
            env=R.environment()
        self.assertFalse({'AWS_SECRET_ACCESS_KEY','OPENAI_API_KEY','HTTP_PROXY','DOCKER_HOST','PYTHONPATH'}&set(env))
        self.assertEqual(env['HOME'],str(R.STATE/'home'))
        self.assertEqual({env[k] for k in ('TMPDIR','TMP','TEMP')},{str(R.STATE/'tmp')})
        self.assertEqual(env['PATH'],str(R.VENV/'bin')+':/usr/bin:/bin')
        self.assertEqual(env['MLFLOW_SERVER_ENABLE_JOB_EXECUTION'],'false')
        self.assertEqual(env['MLFLOW_DISABLE_TELEMETRY'],'true')
        self.assertEqual(env['_MLFLOW_SERVER_ARTIFACT_ROOT'],R.ARTIFACT_ROOT.as_uri())
        self.assertEqual(env['__CF_USER_TEXT_ENCODING'],f'0x{os.getuid():X}:0x0:0x0')

    def test_docker_or_assistant_visible_in_isolated_path_refused(self):
        with patch.object(R.shutil,'which',side_effect=lambda name,**kw:'/bad/docker' if name=='docker' else None):
            with self.assertRaisesRegex(R.RuntimeError,'UNSAFE_EXECUTABLE_VISIBLE'):R._isolated_runtime()

    def test_server_fixed_origins_roots_and_single_worker(self):
        argv=R.server_argv()
        for flag,value in [('--host','127.0.0.1'),('--port','18550'),('--workers','1'),
                           ('--backend-store-uri',R.BACKEND_URI),('--registry-store-uri',R.BACKEND_URI),
                           ('--default-artifact-root',R.ARTIFACT_ROOT.as_uri()),
                           ('--allowed-hosts','127.0.0.1:18550'),('--cors-allowed-origins',R.URL)]:
            self.assertEqual(argv[argv.index(flag)+1],value)
        self.assertIn('--no-serve-artifacts',argv)
        self.assertNotIn('--disable-security-middleware',argv)

    def test_unknown_listener_is_not_stopped(self):
        self.prepare();self.listener.return_value=[{'pid':5,'address':'127.0.0.1:18550'}]
        self.assertEqual(R.status()['state'],'unknown')
        with patch.object(R.os,'killpg') as kill:
            with self.assertRaisesRegex(R.RuntimeError,'STOP_IDENTITY_UNCONFIRMED'):R.stop()
            kill.assert_not_called()

    def test_pid_reuse_or_start_time_change_refuses_stop(self):
        self.prepare();self.record();leader=self.leader();leader['start_time']='later'
        with patch.object(R,'processes',return_value={9001:leader}),patch.object(R.os,'killpg') as kill:
            self.assertEqual(R.status()['state'],'unknown')
            with self.assertRaises(R.RuntimeError):R.stop()
            kill.assert_not_called()

    def test_same_group_unexpected_command_refuses_stop(self):
        self.prepare();self.record();worker=self.worker();worker['command']='/bin/sleep 100'
        with patch.object(R,'processes',return_value={9001:self.leader(),9002:worker}),patch.object(R.os,'killpg') as kill:
            self.assertEqual(R.status()['state'],'unknown')
            with self.assertRaises(R.RuntimeError):R.stop()
            kill.assert_not_called()

    def test_nonloopback_listener_fails_even_owned_pid(self):
        self.prepare();self.record();self.listener.return_value=[{'pid':9002,'address':'*:18550'}]
        with patch.object(R,'processes',return_value={9001:self.leader(),9002:self.worker()}):
            self.assertEqual(R.status()['state'],'unknown')

    def test_owned_worker_health_and_stopped_import_guard(self):
        self.prepare();self.record();self.listener.return_value=[{'pid':9002,'address':'127.0.0.1:18550'}]
        with patch.object(R,'processes',return_value={9001:self.leader(),9002:self.worker()}),patch.object(R,'_healthy',return_value=True):
            self.assertTrue(R.status()['healthy'])
            with self.assertRaisesRegex(R.RuntimeError,'SERVER_NOT_STOPPED'):R.require_stopped()

    def test_orphan_child_requires_saved_identity(self):
        self.prepare();record=self.record();worker=self.worker();worker['ppid']=1
        self.assertEqual(R._owned_group(record,{9002:worker}),[worker])
        worker['start_time']='changed'
        with self.assertRaisesRegex(R.RuntimeError,'PROCESS_IDENTITY_CONFLICT'):R._owned_group(record,{9002:worker})

    def test_stop_sends_only_term_to_exact_owned_group(self):
        self.prepare();record=self.record()
        with patch.object(R,'processes',side_effect=[{9001:self.leader(),9002:self.worker()},{}]),patch.object(R.os,'killpg') as kill:
            R._terminate(record)
            kill.assert_called_once_with(9001,signal.SIGTERM)

    def test_known_zombie_after_term_is_exited_not_foreign(self):
        self.prepare();record=self.record()
        dead=self.leader();dead.update(state='Z',command='(Python)')
        with patch.object(R,'processes',side_effect=[{9001:self.leader()}, {9001:dead}]),patch.object(R.os,'killpg') as kill:
            R._terminate(record)
            kill.assert_called_once_with(9001,signal.SIGTERM)
        with patch.object(R,'processes',return_value={9001:dead}),patch.object(R.os,'killpg') as kill:
            self.assertEqual(R.stop()['state'],'stopped')
            kill.assert_not_called()

    def test_unknown_or_reused_zombie_is_not_adopted(self):
        self.prepare();self.record()
        dead=self.leader();dead.update(state='Z',command='(Python)',start_time='different')
        with patch.object(R,'processes',return_value={9001:dead}),patch.object(R.os,'killpg') as kill:
            with self.assertRaisesRegex(R.RuntimeError,'STOP_IDENTITY_UNCONFIRMED'):R.stop()
            kill.assert_not_called()

    def test_darwin_exiting_command_changes_before_zombie_state(self):
        self.prepare();record=self.record()
        exiting=self.worker();exiting.update(state='S',command='(python3.12)')
        with patch.object(R,'processes',side_effect=[{9001:self.leader(),9002:self.worker()},
                                                    {9001:self.leader(),9002:exiting},{}]),patch.object(R.os,'killpg') as kill,patch.object(R.time,'sleep'):
            R._terminate(record)
            kill.assert_called_once_with(9001,signal.SIGTERM)

    def test_after_term_reused_pid_or_foreign_group_never_gets_more_signals(self):
        self.prepare();record=self.record()
        reused=self.worker();reused.update(start_time='new process',command='(python3.12)',state='S')
        foreign=self.worker();foreign['pid']=9003
        for after in ({9002:reused},{9003:foreign}):
            with self.subTest(after=after),patch.object(R,'processes',side_effect=[{9001:self.leader(),9002:self.worker()},after]),patch.object(R.os,'killpg') as kill:
                with self.assertRaisesRegex(R.RuntimeError,'STOP_PROCESS_'):R._terminate(record)
                kill.assert_called_once_with(9001,signal.SIGTERM)

    def test_bind_probe_reuses_time_wait_but_checks_listeners_first(self):
        self.prepare();fake=Mock();fake.__enter__=Mock(return_value=fake);fake.__exit__=Mock()
        with patch.object(R.socket,'socket',return_value=fake):
            R.require_stopped()
        self.assertEqual(fake.method_calls[0],unittest.mock.call.setsockopt(R.socket.SOL_SOCKET,R.socket.SO_REUSEADDR,1))
        self.assertEqual(fake.method_calls[1],unittest.mock.call.bind(('127.0.0.1',18550)))
        self.listener.return_value=[{'pid':100,'address':'127.0.0.1:18550'}]
        with patch.object(R.socket,'socket') as socket:
            with self.assertRaisesRegex(R.RuntimeError,'SERVER_NOT_STOPPED'):R.require_stopped()
            socket.assert_not_called()

    def test_bind_collision_is_reported_without_a_real_host_socket(self):
        self.prepare()
        self.socket.return_value.__enter__.return_value.bind.side_effect=OSError('occupied')
        with self.assertRaisesRegex(R.RuntimeError,'PORT_UNAVAILABLE'):
            R.require_stopped()

    def test_ps_stat_parser_preserves_start_identity(self):
        line=b' 9001 10 9001 501 Z Thu Sep 17 01:00:00 2026 (Python)\n'
        with patch.object(R,'_command',return_value=Mock(returncode=0,stdout=line)):
            parsed=R.processes()[9001]
        self.assertEqual(parsed['state'],'Z')
        self.assertEqual(parsed['start_time'],self.leader()['start_time'])

    def test_stopped_stop_is_noop_preserves_identity_receipt(self):
        self.prepare();self.record();before=(R.STATE/'runtime.json').read_bytes()
        with patch.object(R,'processes',return_value={}),patch.object(R.os,'killpg') as kill:
            self.assertEqual(R.stop()['state'],'stopped');self.assertEqual(R.stop()['state'],'stopped')
            kill.assert_not_called()
        self.assertEqual((R.STATE/'runtime.json').read_bytes(),before)

    def test_start_isolated_child_and_durable_identity(self):
        self.prepare();child=Mock(pid=9001);child.poll.return_value=None
        rows={9001:self.leader(),9002:self.worker()}
        # No subprocess executes: ownership, listener, and health are fake observations.
        with patch.object(R,'processes',return_value=rows),patch.object(R,'_healthy',return_value=True),patch.object(R,'require_stopped'),patch.object(R,'_isolated_runtime',return_value=R.environment()),patch.object(R.subprocess,'Popen',return_value=child) as spawn:
            self.listener.side_effect=[[],[{'pid':9002,'address':'127.0.0.1:18550'}]]
            self.assertTrue(R.start()['healthy'])
            options=spawn.call_args.kwargs
            self.assertEqual(options['env'],R.environment());self.assertEqual(options['umask'],0o077)
            self.assertTrue(options['start_new_session']);self.assertEqual(options['cwd'],R.STORE)
        self.assertEqual(R._record()['leader'],self.leader())
        self.assertEqual(R._record()['members'],[self.leader(),self.worker()])
        self.assertEqual(json.loads(R.read_private(R.STATE/'start-intent.json'))['state'],'identified')

    def test_start_intent_write_failure_prevents_spawn(self):
        self.prepare()
        with patch.object(R,'require_stopped'),patch.object(R,'_isolated_runtime',return_value=R.environment()),patch.object(R,'write_private',side_effect=OSError('private details')),patch.object(R.subprocess,'Popen') as spawn:
            with self.assertRaises(OSError):R.start()
            spawn.assert_not_called()

    def test_cli_help_imports_no_sdk_or_initializes_store(self):
        result=subprocess.run([sys.executable,str(ROOT/'tools/local_experiment/mlflow/runtime.py'),'--help'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0)
        self.assertIn('prepare,start,status,stop',result.stdout)
        self.assertFalse(R.STATE.exists())


if __name__=='__main__':unittest.main()
