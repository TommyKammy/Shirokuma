"""Fake process/listener/HTTP ownership only: no server, SDK, port or model calls."""
from contextlib import ExitStack
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.mlflow import serving_runtime as R
from tests.test_mlflow_registry_contract import fixture


def binding(version='1'):
    return dict(schema_version=1, profile=R.S.PROFILE, model_name=R.K.MODEL_NAME,
                version=version, binding=fixture()[0])


class ServingRuntimeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve(); self.state = self.root/'state'
        self.python = self.root/'venv/bin/python'; self.python.parent.mkdir(parents=True); self.python.touch()
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        for name, value in [('STATE',self.state), ('PYTHON',self.python)]:
            self.stack.enter_context(patch.object(R, name, value))
        self.rows = {}; self.port = []; self.child = Mock(pid=9001, returncode=None); self.child.poll.return_value = None
        self.ps = self.stack.enter_context(patch.object(R.M, 'processes', side_effect=lambda:deepcopy(self.rows)))
        self.listeners = self.stack.enter_context(patch.object(R, 'listeners', side_effect=lambda:deepcopy(self.port)))
        self.stack.enter_context(patch.object(R.socket, 'socket'))
        self.http = self.stack.enter_context(patch.object(R.urllib.request, 'build_opener', side_effect=AssertionError('real HTTP forbidden')))
        self.real_healthy = R._healthy
        self.healthy = self.stack.enter_context(patch.object(R, '_healthy', return_value=True))
        self.load = self.stack.enter_context(patch.object(R.S, 'load_binding', side_effect=lambda version:binding(version)))
        self.health = self.stack.enter_context(patch.object(R.S, 'health', side_effect=R.S.expected_health))
        self.core = self.stack.enter_context(patch.object(R.S, 'serve', side_effect=AssertionError('real server forbidden')))
        self.spawn = self.stack.enter_context(patch.object(R.subprocess, 'Popen', side_effect=AssertionError('real process forbidden')))
        self.kill = self.stack.enter_context(patch.object(R.os, 'killpg', side_effect=self.gone))
        self.stack.enter_context(patch.object(R.time, 'sleep'))

    def gone(self, pgid, sig):
        self.assertEqual((pgid,sig), (9001,signal.SIGTERM)); self.rows = {}; self.port = []

    def launch(self, argv, **kwargs):
        self.assertEqual(argv[:4], [str(self.python),'-m','tools.local_experiment.mlflow.serving_runtime','serve'])
        self.assertTrue(kwargs['start_new_session']); self.assertEqual(kwargs['umask'],0o077)
        self.assertEqual(kwargs['cwd'],R.ROOT)
        self.assertEqual(kwargs['env'],R.environment())
        self.assertEqual(R._intent(R._owner())['state'],'pending')
        self.rows = {9001:dict(pid=9001,ppid=9000,pgid=9001,uid=os.getuid(),state='S',
            start_time='Mon Sep 21 00:00:00 2026',command=' '.join(argv))}
        self.port = [dict(pid=9001,address=f'{R.HOST}:{R.PORT}')]
        return self.child

    def start(self):
        self.spawn.side_effect = self.launch
        return R.start('1')

    def test_initial_status_is_read_only_and_foreign_listener_is_never_adopted(self):
        self.assertEqual(R.status()['state'],'uninitialized'); self.assertFalse(self.state.exists())
        self.port = [dict(pid=10,address='127.0.0.1:18570')]
        self.assertEqual(R.status()['state'],'unknown')
        for call in (lambda:R.start('1'),R.stop):
            with self.assertRaises(R.ServingRuntimeError): call()
        self.spawn.assert_not_called(); self.kill.assert_not_called(); self.load.assert_not_called()

    def test_start_pins_safe_binding_private_token_and_single_owned_argv(self):
        before = dict(os.environ); result = self.start()
        self.assertEqual(result['state'],'running'); self.assertTrue(result['healthy'])
        self.assertEqual(result['version'],'1'); self.assertEqual(dict(os.environ),before)
        owner = R._owner(); launch = R._intent(owner); record = R._record(owner,launch); config = R._config(owner,launch)
        self.assertEqual(launch['state'],'identified'); self.assertEqual(record['leader'],self.rows[9001])
        self.assertEqual(config['binding'],binding()); self.assertEqual(config['health'],R.S.expected_health(binding()))
        token = R._token(); self.assertEqual(len(token),64)
        self.assertNotIn(token,json.dumps(result)); self.assertNotIn(token,' '.join(record['argv']))
        for path in self.state.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777,0o700 if path.is_dir() else 0o600)
        self.assertEqual((self.state/'server.log').read_bytes(),b'')
        self.spawn.assert_called_once(); self.core.assert_not_called()

    def test_existing_version_is_noop_and_different_version_never_switches(self):
        first = self.start(); count = self.load.call_count
        self.assertEqual(R.start('1'),first); self.assertEqual(self.load.call_count,count)
        with self.assertRaisesRegex(R.ServingRuntimeError,'VERSION_ALREADY_RUNNING'): R.start('2')
        self.spawn.assert_called_once(); self.kill.assert_not_called()
        self.healthy.return_value = False
        with self.assertRaisesRegex(R.ServingRuntimeError,'UNHEALTHY'): R.start('1')

    def test_pending_start_preserves_unknown_and_does_not_retry(self):
        self.start(); launch = R._intent(R._owner()); launch['state']='pending'; R._save('start-intent.json',launch)
        self.rows = {}; self.port = []; before = (self.state/'start-intent.json').read_bytes()
        self.assertEqual(R.status()['state'],'unknown')
        with self.assertRaises(R.ServingRuntimeError): R.start('1')
        with self.assertRaises(R.ServingRuntimeError): R.stop()
        self.assertEqual((self.state/'start-intent.json').read_bytes(),before)
        self.spawn.assert_called_once(); self.kill.assert_not_called()

    def test_identity_reuse_foreign_group_and_wrong_listener_block_signal(self):
        self.start(); leader = deepcopy(self.rows[9001]); port = deepcopy(self.port)
        cases = [('start_time','later'),('command','/bin/sleep 100'),('pgid',1234),('uid',os.getuid()+1)]
        for key, value in cases:
            self.rows = {9001:dict(leader,**{key:value})}
            with self.subTest(key=key), self.assertRaises(R.ServingRuntimeError): R.stop()
            self.kill.assert_not_called()
        self.rows = {9001:leader,9002:dict(leader,pid=9002,ppid=9001)}
        with self.assertRaises(R.ServingRuntimeError): R.stop()
        self.rows = {9001:leader}
        for foreign in [dict(pid=9002,address='127.0.0.1:18570'),dict(pid=9001,address='*:18570')]:
            self.port = [foreign]
            with self.assertRaises(R.ServingRuntimeError): R.stop()
            self.kill.assert_not_called()
        self.port = port

    def test_health_response_is_rechecked_against_process_and_listener(self):
        self.start()
        with patch.object(R,'listeners',side_effect=[deepcopy(self.port),[]]):
            self.assertFalse(R.status()['healthy'])
        def changed(_):
            self.rows[9001]['start_time']='reused'; return True
        self.healthy.side_effect=changed
        self.assertEqual(R.status()['state'],'unknown'); self.kill.assert_not_called()

    def test_stop_sends_one_term_and_repeated_stop_preserves_receipts(self):
        self.start(); before = (self.state/'runtime.json').read_bytes()
        stopped = R.stop(); self.assertEqual(stopped['state'],'stopped'); self.assertTrue(stopped['cleanup_verified'])
        self.kill.assert_called_once_with(9001,signal.SIGTERM)
        self.assertEqual(R.stop(),stopped); self.assertEqual((self.state/'runtime.json').read_bytes(),before)
        self.kill.assert_called_once(); self.load.assert_called_once()

    def test_stop_accepts_known_zombie_without_signal_and_rejects_reused_zombie(self):
        self.start(); self.rows[9001].update(state='Z',command='(Python)'); self.port=[]
        self.assertEqual(R.stop()['state'],'stopped'); self.kill.assert_not_called()
        self.rows[9001]['start_time']='another process'
        with self.assertRaises(R.ServingRuntimeError): R.stop()
        self.kill.assert_not_called()

    def test_after_term_changed_identity_or_new_group_never_gets_more_signals(self):
        self.start(); leader = deepcopy(self.rows[9001]); record = R._record(R._owner(),R._intent(R._owner()))
        for after in ({9001:dict(leader,start_time='new')},{9002:dict(leader,pid=9002)}):
            self.kill.reset_mock(); self.kill.side_effect=None
            with patch.object(R.M,'processes',side_effect=[{9001:leader},after]), self.assertRaises(R.ServingRuntimeError):
                R._terminate(record)
            self.kill.assert_called_once_with(9001,signal.SIGTERM)

    def test_after_term_darwin_transient_command_is_only_observed(self):
        self.start(); record=R._record(R._owner(),R._intent(R._owner())); leader=deepcopy(self.rows[9001])
        self.kill.side_effect=None
        with patch.object(R.M,'processes',side_effect=[{9001:leader},{9001:dict(leader,command='(Python)')},{}]), \
                patch.object(R,'listeners',side_effect=[self.port,[]]):
            R._terminate(record)
        self.kill.assert_called_once_with(9001,signal.SIGTERM)

    def test_stop_deadline_does_not_escalate_or_declare_cleanup(self):
        self.start(); record=R._record(R._owner(),R._intent(R._owner())); self.kill.side_effect=None
        with patch.object(R.time,'monotonic',side_effect=[0,21]), self.assertRaisesRegex(R.ServingRuntimeError,'STOP_TIMEOUT'):
            R._terminate(record)
        self.kill.assert_called_once_with(9001,signal.SIGTERM); self.assertFalse(R.status()['cleanup_verified'])

    def test_late_foreign_listener_before_term_never_gets_a_signal(self):
        self.start(); record=R._record(R._owner(),R._intent(R._owner()))
        self.port=[dict(pid=100,address='127.0.0.1:18570')]
        with self.assertRaisesRegex(R.ServingRuntimeError,'PORT_CONFLICT'): R._terminate(record)
        self.kill.assert_not_called()

    def test_bind_probe_matches_only_fixed_loopback_and_drops_parent_secrets(self):
        R._port_free()
        sock=R.socket.socket.return_value.__enter__.return_value
        sock.bind.assert_called_once_with(('127.0.0.1',18570))
        with patch.dict(os.environ,{'HOME':'foreign','OPENAI_API_KEY':'secret','HTTP_PROXY':'foreign','PYTHONPATH':'foreign'}):
            env=R.environment()
        self.assertFalse({'OPENAI_API_KEY','HTTP_PROXY','PYTHONPATH'} & set(env))
        self.assertEqual(env['HOME'],str(self.state/'home'))
        self.assertEqual(env['MLFLOW_DISABLE_TELEMETRY'],'true')
        sock.bind.side_effect=OSError('collision')
        with self.assertRaisesRegex(R.ServingRuntimeError,'PORT_CONFLICT'): R._port_free()

    def test_private_unmarked_state_permissions_and_token_tampering_are_refused(self):
        self.state.mkdir(mode=0o700); (self.state/'foreign').touch(mode=0o600)
        with self.assertRaises(R.ServingRuntimeError): R.start('1')
        self.load.assert_not_called(); self.spawn.assert_not_called()
        (self.state/'foreign').unlink(); self.start()
        token=self.state/'token.json'; token.chmod(0o644)
        with self.assertRaises(R.M.RuntimeError): R._token()
        token.chmod(0o600); original=json.loads(token.read_bytes()); R._save('token.json',{'token':'short'})
        with self.assertRaises(R.ServingRuntimeError): R._token()
        R._save('token.json',original)

    def test_spawn_failure_is_not_started_and_pending_save_failure_never_spawns(self):
        self.spawn.side_effect=OSError('private details')
        with self.assertRaises(OSError): R.start('1')
        self.assertEqual(R._intent(R._owner())['state'],'not-started')
        self.assertEqual(R.status()['state'],'stopped'); self.kill.assert_not_called()
        self.spawn.reset_mock(); save=R._save
        def fail(name,value):
            if name=='start-intent.json': raise OSError('save failed')
            save(name,value)
        with patch.object(R,'_save',side_effect=fail), self.assertRaises(OSError): R.start('1')
        self.spawn.assert_not_called()

    def test_unidentified_child_is_never_signalled_and_blocks_retry(self):
        self.spawn.side_effect=None; self.spawn.return_value=self.child
        with patch.object(R.time,'monotonic',side_effect=[0,31]), self.assertRaisesRegex(R.ServingRuntimeError,'START_UNCONFIRMED'):
            R.start('1')
        self.kill.assert_not_called(); self.assertEqual(R.status()['state'],'unknown')

    def test_start_failure_after_identification_terminates_only_owned_child_and_reaps(self):
        self.spawn.side_effect=self.launch; self.healthy.return_value=False
        # One identification pass, then an expired health deadline.
        with patch.object(R.time,'monotonic',side_effect=[0,0,31,32,32]), self.assertRaisesRegex(R.ServingRuntimeError,'START_TIMEOUT'):
            R.start('1')
        self.kill.assert_called_once_with(9001,signal.SIGTERM); self.child.wait.assert_called_once_with(timeout=2)

    def test_first_start_cancellation_survives_secondary_cleanup_failure(self):
        self.spawn.side_effect=self.launch; first=KeyboardInterrupt('cancel start'); self.healthy.side_effect=first
        with patch.object(R,'_terminate',side_effect=OSError('cleanup failed')), self.assertRaises(KeyboardInterrupt) as caught:
            R.start('1')
        self.assertIs(caught.exception,first); self.kill.assert_not_called()
        self.assertEqual(R._intent(R._owner())['state'],'identified')

    def test_config_binding_health_and_launch_tampering_fail_before_internal_serve(self):
        self.start(); path=self.state/'config.json'; original=json.loads(path.read_bytes()); launch=R._intent(R._owner())
        for value in [dict(original,health={}),dict(original,launch_id=str(R.uuid.uuid4())),
                      dict(original,binding=binding('2'))]:
            R._save('config.json',value)
            with self.assertRaises(R.ServingRuntimeError): R.serve('1',launch['launch_id'])
        self.core.assert_not_called(); R._save('config.json',original)

    def test_internal_serve_requires_parent_identity_revalidates_binding_and_restores_signals(self):
        self.start(); launch=R._intent(R._owner()); old={signal.SIGTERM:Mock(),signal.SIGINT:Mock()}; handlers=dict(old)
        def serve(bound,token,**kwargs):
            self.assertEqual(bound,binding()); self.assertEqual(token,R._token())
            self.assertFalse(kwargs['should_stop']()); handlers[signal.SIGTERM](signal.SIGTERM,None)
            self.assertTrue(kwargs['should_stop']())
        with patch.object(R.os,'getpid',return_value=9001),patch.object(R.os,'getpgrp',return_value=9001), \
                patch.object(R.os,'getppid',return_value=9000), \
                patch.object(R.signal,'getsignal',side_effect=lambda s:handlers[s]), \
                patch.object(R.signal,'signal',side_effect=lambda s,h:handlers.__setitem__(s,h)), \
                patch.object(R.S,'serve',side_effect=serve) as core:
            self.assertEqual(R.serve('1',launch['launch_id']),0)
        core.assert_called_once(); self.assertEqual(handlers,old)
        self.load.return_value=binding('2'); self.load.side_effect=None
        with patch.object(R.os,'getpid',return_value=9001),patch.object(R.os,'getpgrp',return_value=9001), \
                patch.object(R.os,'getppid',return_value=9000), \
                self.assertRaisesRegex(R.ServingRuntimeError,'BINDING_CHANGED'):
            R.serve('1',launch['launch_id'])
        self.core.assert_not_called()

    def test_internal_handshake_creates_no_probe_child_and_external_group_checks_stay_strict(self):
        self.start(); launch=R._intent(R._owner()); leader=deepcopy(self.rows[9001])
        self.rows[9002]=dict(leader,pid=9002,ppid=9001,
                            command='/bin/ps -ax -o pid=,ppid=,pgid=,uid=,stat=,lstart=,command=')
        self.assertEqual(R.status()['state'],'unknown')
        with self.assertRaises(R.ServingRuntimeError): R.stop()
        self.kill.assert_not_called()
        with patch.object(R.os,'getpid',return_value=9001),patch.object(R.os,'getpgrp',return_value=9001), \
                patch.object(R.os,'getppid',return_value=9000), \
                patch.object(R.M,'processes',side_effect=AssertionError('self ps child forbidden')), \
                patch.object(R.signal,'getsignal',return_value=signal.SIG_DFL),patch.object(R.signal,'signal'), \
                patch.object(R.S,'serve',return_value=None) as serve:
            self.assertEqual(R.serve('1',launch['launch_id']),0)
        serve.assert_called_once()
        # The child must still belong to the parent that saved its launch record.
        with patch.object(R.os,'getpid',return_value=9001),patch.object(R.os,'getpgrp',return_value=9001), \
                patch.object(R.os,'getppid',return_value=9009), \
                self.assertRaisesRegex(R.ServingRuntimeError,'START_UNCONFIRMED'):
            R.serve('1',launch['launch_id'])
        self.core.assert_not_called()

    def test_auth_health_has_fixed_origin_no_proxy_redirect_or_persisted_body(self):
        self.start(); expected=R.S.expected_health(binding()); response=Mock(status=200)
        response.read.return_value=json.dumps(expected).encode()
        response.__enter__=Mock(return_value=response); response.__exit__=Mock(return_value=False)
        opener=Mock(); opener.open.return_value=response
        self.http.side_effect=None; self.http.return_value=opener
        self.assertTrue(self.real_healthy(expected))
        request=opener.open.call_args.args[0]
        self.assertEqual(request.full_url,R.URL+'/health'); self.assertEqual(request.get_header('Host'),'127.0.0.1:18570')
        self.assertEqual(request.get_header('Authorization'),'Bearer '+R._token())
        self.assertEqual(opener.open.call_args.kwargs,{'timeout':5})
        self.assertIsInstance(self.http.call_args.args[0],R.urllib.request.ProxyHandler)
        self.assertEqual(self.http.call_args.args[0].proxies,{})
        with self.assertRaises(R.ServingRuntimeError): R._NoRedirect().redirect_request(None,None,None,None,None,None)
        response.read.return_value=b'x'*65537; self.assertFalse(self.real_healthy(expected))
        response.read.return_value=json.dumps(dict(expected,version='2')).encode(); self.assertFalse(self.real_healthy(expected))

    def test_cli_rejects_missing_version_extra_args_and_hides_unexpected_details(self):
        for argv in [['start'],['status','--version','1'],['stop','--launch-id','secret']]:
            output=io.StringIO()
            with patch('sys.stdout',output): self.assertEqual(R.main(argv),1)
            self.assertNotIn('secret',output.getvalue())
        output=io.StringIO()
        with patch.object(R,'status',side_effect=OSError('secret environment')),patch('sys.stdout',output):
            self.assertEqual(R.main(['status']),1)
        self.assertEqual(json.loads(output.getvalue()),{'result':'failed','error_code':'SERVING_OPERATION_FAILED'})
        self.load.assert_not_called(); self.spawn.assert_not_called()

    def test_lock_and_invalid_version_prevent_parallel_or_accidental_start(self):
        for version in ('latest','01',True,0):
            with self.assertRaises(R.K.RegistryError): R.start(version)
        self.assertFalse(self.state.exists())
        with R._lock(),self.assertRaisesRegex(R.ServingRuntimeError,'BUSY'): R.start('1')
        self.spawn.assert_not_called()


if __name__=='__main__':
    unittest.main()
