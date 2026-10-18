"""Exercise request/stop ordering with independent resident processes."""
import importlib.util
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import nullcontext
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / 'tools/local_experiment/warehouse_resident.py'
sys.path.insert(0, str(MODULE.parent))
SPEC = importlib.util.spec_from_file_location('warehouse_concurrency', MODULE)
w = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(w)


class WarehouseConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.TemporaryDirectory()
        self.addCleanup(self.state.cleanup)
        env = patch.dict(os.environ, {'SHIROKUMA_STATE_DIR': self.state.name})
        env.start()
        self.addCleanup(env.stop)
        path = w.activity_path()
        path.parent.mkdir(parents=True)
        path.write_text('{"last_activity": 0}')
        path.with_name('auto_chill.json').write_text('{"auto_chill_minutes": 1}')

    def child(self, body):
        script = f'''
import importlib.util, json, sys
from contextlib import nullcontext
from unittest.mock import patch
sys.path.insert(0, {str(MODULE.parent)!r})
spec = importlib.util.spec_from_file_location('resident', {str(MODULE)!r})
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)
w._LAST_ACTIVITY = 0
{body}
'''
        process = subprocess.Popen([sys.executable, '-c', script], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def cleanup():
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
        self.addCleanup(cleanup)
        return process

    def test_stop_already_committed_blocks_new_request_until_wake(self):
        child = self.child('''
def scale(*args):
    print('scale-ready', flush=True)
    sys.stdin.readline()
    return ''
with patch.object(w, 'status', return_value={'replicas': 1, 'state': 'active'}), \
     patch.object(w, 'trino', return_value=nullcontext('url')), \
     patch.object(w, 'execute', return_value={'rows': [[0]]}), \
     patch.object(w, 'kubectl', side_effect=scale), \
     patch.object(w, 'wait_for'), patch.object(w, 'telemetry'):
    print(json.dumps(w.check_auto_chill()), flush=True)
''')
        self.assertEqual(child.stdout.readline().strip(), 'scale-ready')
        accepted, started = threading.Event(), threading.Event()
        results, errors = [], []
        def query():
            started.set()
            try:
                results.append(w.query('LOCAL', 'SELECT 1'))
            except Exception as exc:
                errors.append(exc)
        def execute(*args):
            accepted.set()
            return {'rows': [[1]]}
        with patch.object(w, 'status', return_value={'replicas': 0}), \
             patch.object(w, 'transition') as wake, \
             patch.object(w, 'trino', return_value=nullcontext('url')), \
             patch.object(w, 'execute', side_effect=execute), patch.object(w, 'telemetry'):
            thread = threading.Thread(target=query)
            thread.start()
            self.assertTrue(started.wait(3))
            self.assertFalse(accepted.wait(0.2))
            child.stdin.write('continue\n')
            child.stdin.flush()
            thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(results[0]['rows'], [[1]])
            wake.assert_called_once_with('wake', 'LOCAL')
        out, err = child.communicate(timeout=5)
        self.assertEqual(child.returncode, 0, err)
        self.assertTrue(json.loads(out)['chilled'])

    def test_accepted_request_finishes_before_idle_check_and_prevents_stop(self):
        child = self.child('''
def execute(*args):
    print('query-running', flush=True)
    sys.stdin.readline()
    return {'rows': [[1]]}
with patch.object(w, 'status', return_value={'replicas': 1}), \
     patch.object(w, 'trino', return_value=nullcontext('url')), \
     patch.object(w, 'execute', side_effect=execute), patch.object(w, 'telemetry'):
    w.query('LOCAL', 'SELECT 1')
print('query-done', flush=True)
''')
        self.assertEqual(child.stdout.readline().strip(), 'query-running')
        # Simulate a request whose execution has exceeded the idle threshold.
        w.activity_path().write_text('{"last_activity": 0}')
        finished, started = threading.Event(), threading.Event()
        results = []
        def check():
            started.set()
            results.append(w.check_auto_chill())
            finished.set()
        with patch.object(w, '_LAST_ACTIVITY', 0), \
             patch.object(w, 'status', return_value={'replicas': 1}) as status, \
             patch.object(w, 'transition') as stop:
            thread = threading.Thread(target=check)
            thread.start()
            self.assertTrue(started.wait(3))
            self.assertFalse(finished.wait(0.2))
            child.stdin.write('continue\n')
            child.stdin.flush()
            thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertFalse(results[0]['chilled'])
            stop.assert_not_called()
            status.assert_not_called()
        out, err = child.communicate(timeout=5)
        self.assertEqual(child.returncode, 0, err)
        self.assertIn('query-done', out)

    def test_active_requests_can_overlap_and_release_on_error(self):
        entered = threading.Event()
        def second():
            with w.warehouse_activity():
                entered.set()
        with w.warehouse_activity():
            thread = threading.Thread(target=second)
            thread.start()
            self.assertTrue(entered.wait(3))
            thread.join(3)
        with self.assertRaisesRegex(RuntimeError, 'failed'):
            with w.warehouse_activity():
                raise RuntimeError('failed')
        with w._warehouse_gate(exclusive=True):
            self.assertEqual(w._WAREHOUSE_GATE.mode, 'exclusive')

    def test_read_only_existing_gate_still_coordinates_requests(self):
        with w._warehouse_gate():
            pass
        original = Path.open
        for code in (errno.EACCES, errno.EROFS):
            def open_path(path, mode='r', *args, **kwargs):
                if path.name == 'warehouse.lock' and mode == 'a':
                    raise OSError(code, 'read-only state')
                return original(path, mode, *args, **kwargs)
            with self.subTest(errno=code), patch.object(Path, 'open', open_path), w.warehouse_activity():
                self.assertEqual(w._WAREHOUSE_GATE.mode, 'shared')

    def test_unavailable_gate_rejects_before_any_backend_work(self):
        with patch.object(Path, 'open', side_effect=PermissionError('unavailable gate')), \
             patch.object(w, 'status') as status, patch.object(w, 'execute') as execute:
            with self.assertRaises(PermissionError):
                w.query('LOCAL', 'SELECT 1')
        status.assert_not_called()
        execute.assert_not_called()


if __name__ == '__main__':
    unittest.main()
