"""Private fake lifecycle only: no listener, HTTP, kernel, or server is started."""
from contextlib import ExitStack
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('lab_runtime_test', ROOT / 'tools/local_experiment/notebook_lab/runtime.py')
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)
KID = '12345678-1234-4234-8234-123456789012'


class LabRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.state = self.directory / 'lab'
        self.template = self.directory / 'template.ipynb'
        self.template.write_bytes(b'{"cells":[]}')
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(R, 'STATE', self.state))
        self.stack.enter_context(patch.object(R, 'TEMPLATE', self.template))
        # Every test refuses accidental external operations by default.
        self.stack.enter_context(patch.object(R.M, 'processes', side_effect=AssertionError('unexpected ps')))
        self.stack.enter_context(patch.object(R, 'listeners', side_effect=AssertionError('unexpected listener')))
        self.stack.enter_context(patch.object(R, '_api', side_effect=AssertionError('unexpected HTTP')))
        self.stack.enter_context(patch.object(R.subprocess, 'Popen', side_effect=AssertionError('unexpected server')))

    def prepared(self):
        self.state.mkdir(mode=0o700)
        return R._prepare()

    def process(self, pid=420, kernel=False):
        return {'pid': pid, 'ppid': 420 if kernel else 1, 'pgid': pid,
                'uid': os.getuid(), 'state': 'S', 'start_time': 'Sun Sep 20 10:00:00 2026',
                'command': ' '.join(R.kernel_argv(KID) if kernel else R.server_argv())}

    def running(self, kernel=True):
        owner = self.prepared()
        leader = self.process()
        record = {'schema_version': 1, 'instance_id': owner['instance_id'],
                  'argv': R.server_argv(), 'leader': leader}
        R._save('runtime.json', record)
        rows = {420: leader}
        if kernel:
            rows[421] = self.process(421, kernel=True)
        return record, rows

    def test_prepare_preserves_user_workspace_and_private_auth(self):
        first = self.prepared()
        token = R._token()
        p = self.state / 'workspace/training_workbench.ipynb'
        p.write_bytes(b'user edited output')
        before = p.stat()
        self.assertEqual(R._prepare(), first)
        self.assertEqual(p.read_bytes(), b'user edited output')
        self.assertEqual(p.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(R._token(), token)
        self.assertNotIn(token, ' '.join(R.server_argv()))
        self.assertNotIn(token, json.dumps(R._public('stopped')))
        for path in self.state.rglob('*'):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)

    def test_lake_training_template_seed_preserves_all_edited_notebooks(self):
        self.prepared()
        workspace = self.state / 'workspace'
        destination = workspace / 'lake_training_workbench.ipynb'
        self.assertEqual(destination.read_bytes(), R.LAKE_TRAINING_TEMPLATE.read_bytes())
        before = {}
        for path in workspace.iterdir():
            path.write_bytes(b'edited cells and outputs: ' + path.name.encode())
            before[path.name] = (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode)
        R._prepare()
        self.assertEqual({path.name: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode)
                          for path in workspace.iterdir()}, before)

    def test_unknown_training_blocks_start_and_stop_before_service_or_seed(self):
        from tools.local_experiment.notebook_lab import train
        self.state.mkdir(mode=0o700)
        trainings = self.state / 'trainings'; trainings.mkdir(mode=0o700)
        with patch.object(train, 'LAB_STATE', self.state), patch.object(train, 'check_idle', return_value=False) as check:
            for action in (R.start, R.stop):
                with self.subTest(action=action.__name__), self.assertRaisesRegex(R.LabError, 'LAB_TRAINING_ACTIVE_OR_UNKNOWN'):
                    action()
        self.assertEqual(check.call_count, 2)
        self.assertFalse((self.state / 'owner.json').exists())
        self.assertFalse((self.state / 'workspace').exists())

    def test_training_guard_rejects_state_mismatch_and_dangling_symlink(self):
        from tools.local_experiment.notebook_lab import train
        self.state.mkdir(mode=0o700)
        trainings = self.state / 'trainings'; trainings.mkdir(mode=0o700)
        with patch.object(train, 'LAB_STATE', self.directory / 'different'), patch.object(train, 'check_idle') as check:
            with self.assertRaisesRegex(R.LabError, 'LAB_TRAINING_STATE_CHANGED'):
                R._trainings_idle()
            check.assert_not_called()
        trainings.rmdir(); trainings.symlink_to(self.directory / 'missing')
        with patch.object(train, 'check_idle') as check:
            with self.assertRaises(Exception):
                R._trainings_idle()
            check.assert_not_called()

    def test_recording_guard_blocks_lifecycle_and_other_workflows(self):
        from tools.local_experiment.notebook_lab import record_comparison, query, compare, train, runtime
        self.state.mkdir(mode=0o700)
        (self.state / 'comparison-recordings').mkdir(mode=0o700)
        with patch.object(record_comparison, 'LAB_STATE', self.state), \
                patch.object(record_comparison, 'check_idle', return_value=False):
            for action in (R.start, R.stop):
                with self.subTest(action=action.__name__), self.assertRaisesRegex(R.LabError, 'LAB_RECORD_ACTIVE_OR_UNKNOWN'):
                    action()
        self.assertFalse((self.state / 'workspace').exists())
        with patch.object(runtime, 'STATE', self.state), patch.object(runtime, '_recordings_idle', return_value=False):
            for module in (query, compare, train):
                with self.subTest(module=module.__name__), self.assertRaisesRegex(module.N.NotebookError, 'CLEANUP_UNKNOWN'):
                    with module._lock():
                        self.fail('blocked operation entered')

    def test_recording_template_preserves_edits_and_clears_stale_result(self):
        self.prepared()
        target = self.state / 'workspace/comparison_recording.ipynb'
        self.assertEqual(target.read_bytes(), R.COMPARISON_RECORDING_TEMPLATE.read_bytes())
        target.write_bytes(b'edited recording and saved receipt')
        before = (target.read_bytes(), target.stat().st_mtime_ns, target.stat().st_mode)
        R._prepare()
        self.assertEqual((target.read_bytes(), target.stat().st_mtime_ns, target.stat().st_mode), before)
        cells = [''.join(c['source']) for c in json.loads(R.COMPARISON_RECORDING_TEMPLATE.read_bytes())['cells']
                 if c['cell_type'] == 'code']
        token = 'b' * 32
        value = dict(reused=True, comparison={'candidate': {'alpha': 10}}, import_receipt={'run_id': 'c' * 32})
        scope = dict(inventory={'result': 'complete', 'runs': [{'comparison_token': token}]},
                     comparison_token=token, record_comparison=Mock(return_value=value), display=Mock(), JSON=lambda x: x)
        with patch('sys.stdout', new_callable=io.StringIO):
            exec(cells[-1], scope)
        scope['record_comparison'].assert_called_once_with(token)
        self.assertEqual(scope['recording'], value)
        scope['record_comparison'].side_effect = ValueError('private-body')
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            exec(cells[-1], scope)
        self.assertIsNone(scope['recording'])
        self.assertNotIn('private-body', output.getvalue())
        scope['record_comparison'].reset_mock(); scope['comparison_token'] = 'unknown'
        with patch('sys.stdout', new_callable=io.StringIO):
            exec(cells[-1], scope)
        scope['record_comparison'].assert_not_called()

    def test_training_template_uses_new_token_and_clears_failed_results(self):
        cells = [''.join(c['source']) for c in json.loads(R.LAKE_TRAINING_TEMPLATE.read_bytes())['cells']
                 if c['cell_type'] == 'code']
        class FixedError(Exception):
            pass
        current = {'identity': {'attempt_token': 'b' * 32}}
        scope = dict(NotebookError=FixedError, PredictionError=FixedError, TrainingRunsError=FixedError,
                     HTML=lambda value: value, SVG=lambda value: value, JSON=lambda value: value,
                     display=Mock(), train_lake=Mock(return_value=current), render_review=Mock(return_value='HTML'),
                     residual_svg=Mock(return_value='SVG'), predict=Mock(return_value={'predictions': [1, 2]}),
                     collect=Mock(return_value={'runs': [{'training_token': 'b' * 32}]}))
        exec(cells[1], scope)
        scope['train_lake'].assert_called_once_with()
        exec(cells[2], scope); exec(cells[3], scope)
        self.assertEqual(scope['saved_runs']['runs'][0]['training_token'], 'b' * 32)
        self.assertEqual(scope['predict'].call_args.args[0], 'b' * 32)
        self.assertEqual(len(scope['predict'].call_args.args[1]['rows']), 2)
        scope['predict'].reset_mock()
        scope['train_lake'].side_effect = FixedError('raw-sensitive-body')
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            exec(cells[1], scope); exec(cells[3], scope)
        self.assertIsNone(scope['review']); self.assertIsNone(scope['prediction']); self.assertIsNone(scope['saved_runs'])
        scope['predict'].assert_not_called()
        self.assertNotIn('raw-sensitive-body', output.getvalue())
        scope['review'] = current; scope['prediction'] = {'stale': True}
        scope['predict'].side_effect = FixedError('raw-prediction-body')
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            exec(cells[3], scope)
        self.assertIsNone(scope['prediction'])
        self.assertNotIn('raw-prediction-body', output.getvalue())

    def test_training_template_recovery_never_starts_new_fit(self):
        cells = [''.join(c['source']) for c in json.loads(R.LAKE_TRAINING_TEMPLATE.read_bytes())['cells']
                 if c['cell_type'] == 'code']
        class FixedError(Exception):
            pass
        current = {'identity': {'attempt_token': 'b' * 32}}
        scope = dict(NotebookError=FixedError, HTML=lambda x: x, SVG=lambda x: x,
                     display=Mock(), train_lake=Mock(), recover_training=Mock(return_value=current),
                     render_review=Mock(return_value='HTML'), residual_svg=Mock(return_value='SVG'))
        recovery_cell = cells[1].replace('recovery_attempt = ""', 'recovery_attempt = "' + 'a' * 32 + '"')
        exec(recovery_cell, scope)
        scope['recover_training'].assert_called_once_with('a' * 32)
        scope['train_lake'].assert_not_called()
        self.assertEqual(scope['review'], current)
        scope['recover_training'].side_effect = FixedError('private-details')
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            exec(recovery_cell, scope)
        self.assertIsNone(scope['review'])
        self.assertIsNone(scope['prediction'])
        scope['train_lake'].assert_not_called()
        self.assertNotIn('private-details', output.getvalue())

    def test_new_query_example_is_seeded_once_and_preserves_user_edit(self):
        self.prepared()
        destination = self.state / 'workspace/query_workbench.ipynb'
        self.assertEqual(destination.read_bytes(), R.QUERY_TEMPLATE.read_bytes())
        destination.write_bytes(b'user query and saved results')
        before = destination.stat().st_mtime_ns
        R._prepare()
        self.assertEqual(destination.read_bytes(), b'user query and saved results')
        self.assertEqual(destination.stat().st_mtime_ns, before)

    def test_pending_query_prevents_stop_before_any_service_operation(self):
        self.prepared()
        with patch.object(R, '_queries_idle', return_value=False):
            with self.assertRaisesRegex(R.LabError, 'LAB_QUERY_ACTIVE_OR_UNKNOWN'):
                R.stop()

    def test_comparison_template_retains_edited_notebook(self):
        self.prepared()
        destination = self.state / 'workspace/ridge_comparison.ipynb'
        self.assertEqual(destination.read_bytes(), R.COMPARISON_TEMPLATE.read_bytes())
        destination.write_bytes(b'edited alpha and saved comparison')
        before = destination.stat().st_mtime_ns
        R._prepare()
        self.assertEqual(destination.read_bytes(), b'edited alpha and saved comparison')
        self.assertEqual(destination.stat().st_mtime_ns, before)

    def test_pending_comparison_prevents_stop_without_query_history(self):
        self.prepared()
        self.assertFalse((self.state / 'queries').exists())
        with patch.object(R, '_comparisons_idle', return_value=False):
            with self.assertRaisesRegex(R.LabError, 'LAB_COMPARISON_ACTIVE_OR_UNKNOWN'):
                R.stop()

    def test_pending_comparison_prevents_new_server_without_query_history(self):
        self.prepared()
        self.assertFalse((self.state / 'queries').exists())
        with patch.object(R, 'status', return_value=R._public('stopped', kernels=0)), \
                patch.object(R, '_comparisons_idle', return_value=False):
            with self.assertRaisesRegex(R.LabError, 'LAB_COMPARISON_ACTIVE_OR_UNKNOWN'):
                R.start()

    def test_pending_query_prevents_starting_a_new_server(self):
        self.prepared()
        with patch.object(R, 'status', return_value=R._public('stopped', kernels=0)), \
                patch.object(R, '_queries_idle', return_value=False):
            with self.assertRaisesRegex(R.LabError, 'LAB_QUERY_ACTIVE_OR_UNKNOWN'):
                R.start()

    def test_config_blocks_global_inputs_and_optional_network_features(self):
        self.prepared()
        with patch.dict(os.environ, {'HTTP_PROXY': 'secret', 'JUPYTER_TOKEN': 'secret',
                                      'SHIROKUMA_NOTEBOOK_REQUEST_PATH': '/secret'}):
            env = R.environment()
        self.assertNotIn('HOME', env)
        self.assertNotIn('HTTP_PROXY', env)
        self.assertNotIn('JUPYTER_TOKEN', env)
        self.assertNotIn('SHIROKUMA_NOTEBOOK_REQUEST_PATH', env)
        self.assertEqual(env['JUPYTER_NO_CONFIG'], '1')
        config = R._config(R._token())
        self.assertEqual(config['KernelSpecManager']['allowed_kernelspecs'], [R.KERNEL])
        self.assertFalse(config['KernelSpecManager']['ensure_native_kernel'])
        for name in ['allow_remote_access', 'allow_unauthenticated_access',
                     'allow_external_kernels', 'terminals_enabled', 'open_browser']:
            self.assertIs(config['ServerApp'][name], False)
        self.assertIsNone(config['LabApp']['news_url'])
        self.assertEqual(config['LabApp']['extension_manager'], 'readonly')
        self.assertTrue(config['LabApp']['check_for_updates_class'].endswith('.NeverCheckForUpdate'))
        self.assertFalse(config['KernelManager']['autorestart'])

    def test_all_config_values_match_pinned_class_traits_without_app_initialization(self):
        from jupyter_server.serverapp import ServerApp
        from jupyter_server.auth.identity import IdentityProvider
        from jupyter_server.services.kernels.kernelmanager import MappingKernelManager
        from jupyter_client.kernelspec import KernelSpecManager
        from jupyter_client.manager import KernelManager
        from jupyterlab.labapp import LabApp
        from jupyterlab_server.app import LabServerApp
        from traitlets import TraitError
        from types import SimpleNamespace

        classes = {c.__name__: c for c in [ServerApp, IdentityProvider, KernelSpecManager,
                   MappingKernelManager, KernelManager, LabApp, LabServerApp]}
        config = R._config('a' * 64)
        owner = SimpleNamespace(_cross_validation_lock=True)
        self.assertEqual(set(config), set(classes))
        for section, values in config.items():
            traits = classes[section].class_traits(config=True)
            for key, value in values.items():
                with self.subTest(section=section, key=key):
                    self.assertIn(key, traits)
                    traits[key]._validate(owner, value)
        # This exact invalid spelling previously prevented the server starting.
        with self.assertRaises(TraitError):
            ServerApp.class_traits(config=True)['log_level']._validate(owner, 'WARNING')

    def test_pinned_kernelspec_manager_never_searches_global_specs(self):
        self.prepared()
        with patch('jupyter_client.kernelspec.jupyter_path', side_effect=AssertionError('global search')):
            manager = R.PrivateKernelSpecManager(ensure_native_kernel=False,
                                                allowed_kernelspecs={R.KERNEL})
            self.assertEqual(manager.kernel_dirs, [str(self.state / 'data/kernels')])
            self.assertEqual(manager.find_kernel_specs(), {
                R.KERNEL: str(self.state / 'data/kernels' / R.KERNEL)})
            self.assertEqual(manager.get_kernel_spec(R.KERNEL).argv, R._kernelspec()['argv'])
        self.assertNotIn('kernel_dirs', R._config('a' * 64)['KernelSpecManager'])

    def test_only_runtime_accepts_jupyter_connection_sticky_bit(self):
        owner = self.prepared()
        runtime = self.state / 'runtime'
        runtime.chmod(0o1700)
        self.assertEqual(R._owner(), owner)
        self.assertEqual(R._prepare(), owner)
        self.assertEqual(runtime.stat().st_mode & 0o7777, 0o1700)
        R._exclusive(runtime / 'fake-connection', b'private')
        with patch.object(R, 'listeners', return_value=[]), patch.object(R.M, 'processes', return_value={}):
            self.assertEqual(R.status()['state'], 'stopped')
            self.assertTrue(R.stop()['cleanup_verified'])
        for mode in (0o1777, 0o1770, 0o2700):
            runtime.chmod(mode)
            with self.assertRaisesRegex(R.LabError, 'LAB_RUNTIME_DIRECTORY_INVALID'):
                R._owner()
        runtime.chmod(0o1700)
        workspace = self.state / 'workspace'
        workspace.chmod(0o1700)
        with self.assertRaises(R.M.RuntimeError):
            R._owner()
        workspace.chmod(0o700)

    def test_missing_token_changed_config_and_symlinks_fail_closed(self):
        self.prepared()
        token = self.state / 'token'
        old = token.read_bytes()
        for raw in [b'', b'not-a-token', old + b'\n']:
            token.write_bytes(raw)
            with self.assertRaisesRegex(R.LabError, 'LAB_TOKEN_INVALID'):
                R._owner()
        token.write_bytes(old)
        config = R._config(R._token())
        config['ServerApp']['allow_origin'] = '*'
        R._save('config/jupyter_server_config.json', config)
        with self.assertRaisesRegex(R.LabError, 'LAB_CONFIG_CHANGED'):
            R._owner()
        token.unlink()
        token.symlink_to(self.template)
        with self.assertRaises(R.M.RuntimeError):
            R._token()

    def test_status_absent_conflict_and_read_only(self):
        with patch.object(R, 'listeners', return_value=[]):
            result = R.status()
        self.assertEqual(result['state'], 'uninitialized')
        self.assertFalse(self.state.exists())
        with patch.object(R, 'listeners', return_value=[{'pid': 999, 'address': '127.0.0.1:18560'}]):
            result = R.status()
        self.assertEqual(result['state'], 'unknown')
        self.assertFalse(result['cleanup_verified'])

    def test_running_status_binds_server_and_only_registered_kernel(self):
        _, rows = self.running()
        before = {p: p.read_bytes() for p in self.state.rglob('*') if p.is_file()}
        with patch.object(R, 'listeners', return_value=[{'pid': 420, 'address': '127.0.0.1:18560'}]), \
             patch.object(R.M, 'processes', return_value=rows), \
             patch.object(R, '_api', return_value=[{'id': KID, 'name': R.KERNEL}]):
            result = R.status()
        self.assertTrue(result['healthy'])
        self.assertEqual(result['kernel_count'], 1)
        self.assertFalse(result['cleanup_verified'])
        self.assertEqual(before, {p: p.read_bytes() for p in self.state.rglob('*') if p.is_file()})

    def test_reused_pid_foreign_listener_and_unmatched_kernel_refused(self):
        record, rows = self.running()
        altered = dict(rows[420], start_time='another process')
        with self.assertRaisesRegex(R.LabError, 'LAB_PROCESS_IDENTITY_CHANGED'):
            R._leader(record, {420: altered})
        with patch.object(R, 'listeners', return_value=[{'pid': 999, 'address': '*:18560'}]), \
             patch.object(R.M, 'processes', return_value=rows):
            with self.assertRaisesRegex(R.LabError, 'LAB_PORT_CONFLICT'):
                R.status()
        for changed in [dict(rows[421], ppid=999), dict(rows[421], pgid=420),
                        dict(rows[421], command='unrelated')]:
            with self.assertRaises(R.LabError):
                R._kernel_processes(record, [KID], {420: rows[420], 421: changed})
        with self.assertRaisesRegex(R.LabError, 'LAB_KERNEL_CHILDREN_UNKNOWN'):
            R._kernel_processes(record, [KID], dict(rows, **{}) | {
                422: dict(rows[421], pid=422, pgid=421, command='user subprocess')})

    def test_pending_launch_and_orphan_never_become_stopped(self):
        owner = self.prepared()
        R._save('start-intent.json', {'schema_version': 1, 'instance_id': owner['instance_id'], 'state': 'pending'})
        with patch.object(R, 'listeners', return_value=[]):
            self.assertEqual(R.status()['state'], 'unknown')
        R._save('start-intent.json', {'schema_version': 1, 'instance_id': owner['instance_id'], 'state': 'not-started'})
        with patch.object(R, 'listeners', return_value=[]), \
             patch.object(R.M, 'processes', return_value={421: self.process(421, kernel=True)}):
            self.assertEqual(R.status()['state'], 'unknown')

    def test_start_fixed_args_and_health_before_success(self):
        self.prepared()
        child = Mock(pid=420)
        child.poll.return_value = None
        calls = [R._public('stopped', kernels=0), R._public('running', healthy=True, pid=420, kernels=0)]
        with patch.object(R, 'status', side_effect=calls), patch.object(R, '_port_free'), \
             patch.object(R.M, 'processes', return_value={420: self.process()}), \
             patch.object(R.subprocess, 'Popen', return_value=child) as popen:
            result = R.start()
        self.assertTrue(result['healthy'])
        self.assertEqual(popen.call_args.args[0], R.server_argv())
        self.assertTrue(popen.call_args.kwargs['start_new_session'])
        self.assertEqual(popen.call_args.kwargs['umask'], 0o077)
        self.assertEqual(json.loads((self.state / 'start-intent.json').read_bytes())['state'], 'identified')

    def test_start_port_conflict_launches_nothing(self):
        self.prepared()
        with patch.object(R, 'status', return_value=R._public('stopped', kernels=0)), \
             patch.object(R, '_port_free', side_effect=R.LabError('LAB_PORT_CONFLICT')):
            with self.assertRaisesRegex(R.LabError, 'LAB_PORT_CONFLICT'):
                R.start()

    def test_stop_protocol_then_pid_gone_then_delete_then_server(self):
        _, rows = self.running()
        order = []
        status = [R._public('running', healthy=True, pid=420, kernels=1), R._public('stopped', kernels=0)]
        def gone(saved):
            order.append('gone-' + str(saved['pid']))
            rows.pop(saved['pid'])
        def api(method, path):
            order.append(method + ' ' + path)
        with patch.object(R, 'status', side_effect=status), patch.object(R.M, 'processes', side_effect=lambda: dict(rows)), \
             patch.object(R, '_kernels', side_effect=[[KID], []]), \
             patch.object(R, '_request_shutdown', side_effect=lambda kid: order.append('control')), \
             patch.object(R, '_wait_gone', side_effect=gone), patch.object(R, '_api', side_effect=api), \
             patch.object(R, 'listeners', return_value=[]):
            result = R.stop()
        self.assertTrue(result['cleanup_verified'])
        self.assertEqual(order, ['control', 'gone-421', 'DELETE /api/kernels/' + KID,
                                  'POST /api/shutdown', 'gone-420'])
        self.assertEqual(json.loads((self.state / 'stop-result.json').read_bytes()), result)

    def test_stop_kernel_timeout_never_deletes_or_shuts_server(self):
        _, rows = self.running()
        with patch.object(R, 'status', return_value=R._public('running', healthy=True, pid=420, kernels=1)), \
             patch.object(R.M, 'processes', return_value=rows), patch.object(R, '_kernels', return_value=[KID]), \
             patch.object(R, '_request_shutdown') as shutdown, \
             patch.object(R, '_wait_gone', side_effect=R.LabError('LAB_STOP_TIMEOUT')):
            with self.assertRaisesRegex(R.LabError, 'LAB_STOP_TIMEOUT'):
                R.stop()
        shutdown.assert_called_once_with(KID)
        self.assertTrue((self.state / 'stop-intent.json').exists())
        self.assertFalse((self.state / 'stop-result.json').exists())

    def test_stop_new_kernel_race_blocks_server_shutdown(self):
        _, rows = self.running(kernel=False)
        with patch.object(R, 'status', return_value=R._public('running', healthy=True, pid=420, kernels=0)), \
             patch.object(R.M, 'processes', return_value=rows), patch.object(R, '_kernels', side_effect=[[], [KID]]):
            with self.assertRaisesRegex(R.LabError, 'LAB_KERNEL_INVENTORY_CHANGED'):
                R.stop()

    def test_control_shutdown_waits_for_own_reply_and_keeps_first_cancel(self):
        import types
        client = Mock()
        client.shutdown.return_value = 'message-id'
        client.get_control_msg.side_effect = [
            {'parent_header': {'msg_id': 'unrelated'}},
            {'parent_header': {'msg_id': 'message-id'}, 'header': {'msg_type': 'shutdown_reply'}}]
        module = types.SimpleNamespace(BlockingKernelClient=Mock(return_value=client))
        with patch.dict('sys.modules', {'jupyter_client': module}), patch.object(R, '_connection', return_value={}):
            R._request_shutdown(KID)
        self.assertEqual(client.get_control_msg.call_count, 2)
        client.stop_channels.assert_called_once()
        interrupt = KeyboardInterrupt()
        client.shutdown.side_effect = interrupt
        client.stop_channels.side_effect = OSError('secondary')
        with patch.dict('sys.modules', {'jupyter_client': module}), patch.object(R, '_connection', return_value={}):
            with self.assertRaises(KeyboardInterrupt) as caught:
                R._request_shutdown(KID)
        self.assertIs(caught.exception, interrupt)

    def test_connection_requires_private_local_authenticated_endpoints(self):
        self.prepared()
        value = {'transport': 'tcp', 'ip': '127.0.0.1', 'signature_scheme': 'hmac-sha256', 'key': 'secret'}
        value.update({name: 15000 + i for i, name in enumerate(
            ['shell_port', 'iopub_port', 'stdin_port', 'control_port', 'hb_port'])})
        R._save('runtime/kernel-' + KID + '.json', value)
        self.assertEqual(R._connection(KID), value)
        for name, bad in [('ip', '1.2.3.4'), ('key', ''), ('control_port', True), ('control_port', 0)]:
            R._save('runtime/kernel-' + KID + '.json', dict(value, **{name: bad}))
            with self.assertRaisesRegex(R.LabError, 'LAB_CONNECTION_INVALID'):
                R._connection(KID)

    def test_gone_requires_same_identity_and_no_surviving_group(self):
        saved = self.process(421, kernel=True)
        self.assertTrue(R._gone(saved, {}))
        self.assertTrue(R._gone(saved, {421: dict(saved, state='Z', command='<defunct>')}))
        with self.assertRaisesRegex(R.LabError, 'LAB_PROCESS_IDENTITY_CHANGED'):
            R._gone(saved, {421: dict(saved, start_time='reused')})
        with patch.object(R.M, 'processes', return_value={422: dict(saved, pid=422)}):
            with self.assertRaisesRegex(R.LabError, 'LAB_PROCESS_GROUP_REMAINS'):
                R._wait_gone(saved)

    def test_stop_uninitialized_does_not_prepare_or_adopt_partial_state(self):
        with patch.object(R,'listeners',return_value=[]):
            result=R.stop()
        self.assertEqual(result['state'],'uninitialized');self.assertFalse(self.state.exists())
        self.state.mkdir(mode=0o700)
        with patch.object(R,'listeners',return_value=[]),patch.object(R.M,'processes',return_value={}):
            self.assertEqual(R.stop()['state'],'uninitialized')
        self.assertEqual({path.name for path in self.state.iterdir()},{'owner.lock'})
        (self.state/'token').write_bytes(b'partial initialization')
        with self.assertRaisesRegex(R.LabError,'LAB_STOP_UNCONFIRMED'):R.stop()

    def test_uninitialized_stop_refuses_foreign_listener_or_unrecorded_process(self):
        with patch.object(R,'listeners',return_value=[{'pid':999,'address':'127.0.0.1:18560'}]):
            with self.assertRaisesRegex(R.LabError,'LAB_STOP_UNCONFIRMED'):R.stop()
        self.assertFalse(self.state.exists());self.state.mkdir(mode=0o700)
        with patch.object(R,'listeners',return_value=[]),patch.object(R.M,'processes',return_value={421:self.process(421,kernel=True)}):
            with self.assertRaisesRegex(R.LabError,'LAB_PROCESS_IDENTITY_CHANGED'):R.stop()

    def save_stop(self, record, rows):
        R._save('stop-intent.json', dict(schema_version=1,instance_id=record['instance_id'],
            leader=record['leader'],kernels={KID:rows[421]}))

    def test_stop_resumes_saved_owned_zombie_or_absent_kernel_without_second_shutdown(self):
        for mode in ('zombie','absent'):
            with self.subTest(mode=mode):
                # Fresh private fixture for each continuation.
                if self.state.exists():
                    import shutil
                    shutil.rmtree(self.state)
                record, rows = self.running()
                self.save_stop(record, rows)
                intent = (self.state/'stop-intent.json').read_bytes()
                original = dict(rows[421])
                if mode == 'zombie': rows[421] = dict(original,state='Z',command='<defunct>')
                else: rows.pop(421)
                order = []
                def gone(saved):
                    order.append('gone-'+str(saved['pid']))
                    rows.pop(saved['pid'],None)
                def api(method,path): order.append(method+' '+path)
                with patch.object(R.M,'processes',side_effect=lambda:dict(rows)), \
                     patch.object(R,'listeners',side_effect=[[{'pid':420,'address':'127.0.0.1:18560'}],[]]), \
                     patch.object(R,'_kernels',side_effect=[[KID],[]]), \
                     patch.object(R,'_request_shutdown') as shutdown,patch.object(R,'_wait_gone',side_effect=gone), \
                     patch.object(R,'_api',side_effect=api),patch.object(R,'status',return_value=R._public('stopped',kernels=0)):
                    result = R.stop()
                self.assertTrue(result['cleanup_verified']); shutdown.assert_not_called()
                self.assertEqual(order,['gone-421','DELETE /api/kernels/'+KID,'POST /api/shutdown','gone-420'])
                self.assertEqual((self.state/'stop-intent.json').read_bytes(),intent)

    def test_resume_skips_kernel_api_entry_already_removed(self):
        record, rows = self.running(); self.save_stop(record,rows); rows.pop(421)
        def gone(saved): rows.pop(saved['pid'],None)
        with patch.object(R.M,'processes',side_effect=lambda:dict(rows)), \
             patch.object(R,'listeners',side_effect=[[{'pid':420,'address':'127.0.0.1:18560'}],[]]), \
             patch.object(R,'_kernels',side_effect=[[],[]]),patch.object(R,'_request_shutdown') as shutdown, \
             patch.object(R,'_wait_gone',side_effect=gone),patch.object(R,'_api') as api, \
             patch.object(R,'status',return_value=R._public('stopped',kernels=0)):
            self.assertTrue(R.stop()['cleanup_verified'])
        shutdown.assert_not_called(); api.assert_called_once_with('POST','/api/shutdown')

    def test_saved_stop_for_gone_server_can_finish_without_any_api(self):
        record, rows = self.running(); self.save_stop(record,rows)
        with patch.object(R.M,'processes',return_value={}),patch.object(R,'listeners',return_value=[]), \
             patch.object(R,'status',return_value=R._public('stopped',kernels=0)):
            self.assertTrue(R.stop()['cleanup_verified'])
        self.assertTrue((self.state/'stop-result.json').exists())

    def test_stop_intent_requires_same_instance_and_fixed_original_kernel_identity(self):
        record, rows = self.running(); self.save_stop(record,rows)
        valid=json.loads((self.state/'stop-intent.json').read_bytes())
        from copy import deepcopy
        for field in ('schema','instance','argv','parent','group','uid','boolpid','kernel-id'):
            changed=deepcopy(valid)
            if field=='schema':changed['schema_version']=True
            elif field=='instance':changed['instance_id']='f'*32
            elif field=='kernel-id':changed['kernels']={'not-a-uuid':changed['kernels'][KID]}
            else:
                key={'argv':'command','parent':'ppid','group':'pgid','uid':'uid','boolpid':'pid'}[field]
                changed['kernels'][KID][key]='other' if field=='argv' else True if field=='boolpid' else 999
            R._save('stop-intent.json',changed)
            with self.subTest(field=field),self.assertRaisesRegex(R.LabError,'LAB_STOP_INTENT_INVALID'):
                R._stop_intent(record)

    def test_resume_refuses_reused_pid_unknown_group_or_new_kernel(self):
        record, rows = self.running(); self.save_stop(record,rows)
        previous=R._stop_intent(record)
        variants=[({420:rows[420],421:dict(rows[421],start_time='reused',state='Z',command='<defunct>')},[KID]),
                  ({420:rows[420],422:dict(rows[421],pid=422,command='unknown child')},[KID]),
                  ({420:rows[420]},[KID,'00000000-0000-4000-8000-000000000001'])]
        for current,ids in variants:
            with self.subTest(ids=ids,rows=list(current)),self.assertRaises(R.LabError):
                R._resume_kernels(record,ids,current,previous)

    def test_wait_rechecks_transient_teardown_snapshot_without_treating_it_as_gone(self):
        saved=self.process(421,kernel=True)
        transitional=dict(saved,state='S',command='<defunct>')
        zombie=dict(saved,state='Z',command='<defunct>')
        with patch.object(R.M,'processes',side_effect=[{421:transitional},{421:zombie}]) as reads, \
             patch.object(R.time,'monotonic',side_effect=[0,0,.1]),patch.object(R.time,'sleep') as sleep:
            R._wait_gone(saved)
        self.assertEqual(reads.call_count,2); sleep.assert_called_once_with(.1)

    def test_wait_persistent_identity_mismatch_stays_failed_and_first_cancel_is_preserved(self):
        saved=self.process(421,kernel=True)
        changed=dict(saved,start_time='reused')
        with patch.object(R.M,'processes',return_value={421:changed}), \
             patch.object(R.time,'monotonic',side_effect=[0,0,1,21]),patch.object(R.time,'sleep'):
            with self.assertRaisesRegex(R.LabError,'LAB_PROCESS_IDENTITY_CHANGED'):R._wait_gone(saved)
        first=KeyboardInterrupt('first cancellation')
        with patch.object(R.M,'processes',side_effect=[{421:changed},first]), \
             patch.object(R.time,'monotonic',side_effect=[0,0,.1]),patch.object(R.time,'sleep'):
            with self.assertRaises(KeyboardInterrupt) as caught:R._wait_gone(saved)
        self.assertIs(caught.exception,first)

    def test_main_safe_errors_and_interrupt_propagation(self):
        stdout = io.StringIO()
        with patch.object(R, 'status', side_effect=OSError('secret-token')), patch('sys.stdout', stdout):
            self.assertEqual(R.main(['status']), 1)
        value = json.loads(stdout.getvalue())
        self.assertEqual(value['state'], 'unknown')
        self.assertEqual(value['error_code'], 'LAB_OPERATION_FAILED')
        self.assertNotIn('secret-token', stdout.getvalue())
        interrupt = KeyboardInterrupt()
        with patch.object(R, 'status', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt) as caught:
                R.main(['status'])
        self.assertIs(caught.exception, interrupt)


if __name__ == '__main__':
    unittest.main()
