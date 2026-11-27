"""Exercise request/stop ordering with independent resident processes."""
import importlib.util
import errno
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
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


if __name__ == '__main__':
    unittest.main()
