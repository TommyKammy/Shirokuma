import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('lab_lifecycle', ROOT / 'tools/local_experiment/lab_lifecycle.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def result(code=0, data=None):
    return subprocess.CompletedProcess([], code, json.dumps(data or {}), 'failure' if code else '')


class LabLifecycleTests(unittest.TestCase):
    def test_status_checks_all_services_and_emits_only_summary(self):
        good = [result(data={'status': 'healthy'}), result(data={'result': 'ready'}), result(data={'ready': True})]
        for failed in (None, 0, 1, 2):
            replies = good.copy()
            if failed is not None:
                replies[failed] = result(1)
            with self.subTest(failed=failed), patch.object(M, 'run', side_effect=replies) as run:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(M.main(['status']), int(failed is not None))
                summary = json.loads(output.getvalue())
                self.assertEqual(set(summary), {'profile', 'ready', 'lakehouse', 'agenthouse', 'loki'})
                self.assertEqual(summary['ready'], failed is None)
                self.assertEqual(summary['profile'], 'local-experiment')
                self.assertEqual(run.call_count, 3)

    def test_status_rejects_unhealthy_invalid_and_missing_outputs(self):
        for reply in (result(), result(data=[]), result(data={'ready': False}),
                      subprocess.CompletedProcess([], 0, 'not json', ''), FileNotFoundError()):
            with self.subTest(reply=reply), patch.object(M, 'run', side_effect=[reply] * 3):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(M.status(), 1)

    def test_up_scoped_order_and_recovery(self):
        for codes in ((0,), (1, 0, 0), (1, 1, 0)):
            replies = iter(codes)
            def invoke(*args, **kwargs):
                return result(next(replies)) if args[0] == M.BASELINE else result()
            with self.subTest(codes=codes), patch.object(M, 'run', side_effect=invoke) as run, \
                    patch.object(M, 'status', return_value=0) as status:
                self.assertEqual(M.main(['up']), 0)
                calls = [c.args for c in run.call_args_list]
                self.assertEqual(calls[0], (M.BASELINE, 'status'))
                repairs = [c for c in calls if c[0] == 'colima']
                self.assertEqual(len(repairs), int(codes == (1, 1, 0)))
                if repairs:
                    self.assertEqual(repairs[0][1:5], ('ssh', '--profile', M.PROFILE, '--'))
                kube = [c for c in calls if c[0] == 'kubectl']
                self.assertTrue(all(c[1:3] == ('--context', M.CONTEXT) for c in kube))
                self.assertEqual(sum('scale' in c for c in kube), 8)
                self.assertEqual(sum('rollout' in c for c in kube), 8)
                patches = [c for c in kube if 'patch' in c]
                self.assertEqual(len(patches), 10)
                self.assertFalse(any('kustomization/flux-system' in c for c in patches))
                self.assertTrue(all(c[-1] == '{"spec":{"suspend":false}}' for c in patches[:3]))
                self.assertTrue(all(c[-1] == '{"spec":{"suspend":null}}' for c in patches[3:]))
                self.assertEqual(calls[-2:], [('python3', 'tools/local_experiment/agenthouse/manage.py', 'up'),
                                              ('python3', 'tools/local_experiment/loki/manage.py', 'up')])
                status.assert_called_once_with()

    def test_up_propagates_failed_readiness(self):
        with patch.object(M, 'run', return_value=result()), patch.object(M, 'status', return_value=1):
            self.assertEqual(M.up(), 1)

    def test_down_attempts_all_stops_on_failure(self):
        with patch.object(M, 'run', side_effect=[result(1), FileNotFoundError(), result()]) as run:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(M.main(['down']), 1)
            self.assertEqual([c.args for c in run.call_args_list], [
                ('python3', 'tools/local_experiment/agenthouse/manage.py', 'stop'),
                ('python3', 'tools/local_experiment/loki/manage.py', 'stop'),
                ('colima', 'stop', '--profile', 'mac-studio-solo')])

    def test_repair_changes_only_node_blob_and_restarts_service(self):
        for old in (b'192.168.5.1', b'192.168.5.2'):
            conn = Mock()
            conn.execute.return_value.fetchone.return_value = (42, b'node:' + old)
            db = Mock()
            db.__enter__ = Mock(return_value=conn)
            db.__exit__ = Mock(return_value=False)
            with self.subTest(old=old), patch('sqlite3.connect', return_value=db), \
                    patch('subprocess.check_output', return_value=b'2: eth0 inet 192.168.5.2/24'), \
                    patch('subprocess.run') as run:
                if old.endswith(b'2'):
                    with self.assertRaises(SystemExit):
                        exec(M.REPAIR_NODE_IP, {})
                    run.assert_not_called()
                else:
                    exec(M.REPAIR_NODE_IP, {})
                    conn.execute.assert_called_with('UPDATE kine SET value=? WHERE id=?', (b'node:192.168.5.2', 42))
                    self.assertEqual([c.args[0] for c in run.call_args_list],
                                     [['service', 'k3s', 'stop'], ['service', 'k3s', 'restart']])


if __name__ == '__main__':
    unittest.main()
