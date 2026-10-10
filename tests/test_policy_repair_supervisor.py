import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from tools.local_experiment import policy_repair_supervisor as S


class Tests(unittest.TestCase):
    def test_default_no_calls(self):
        with mock.patch('sys.argv', ['repair']), mock.patch.object(S, 'run') as run:
            self.assertEqual(S.main(), 0)
            run.assert_not_called()

    def test_execute_requires_explicit_weekly_opt_in(self):
        with mock.patch('sys.argv', ['repair', '--execute']), mock.patch.object(S, 'run') as run:
            with self.assertRaises(S.R.RunnerError): S.main()
            run.assert_not_called()

    def test_mission_bounded_and_no_runtime_authority(self):
        root = Path('/workspace')
        mission = S.mission('a'*40, root/'tmp/local-experiment/policy-snapshot-repair/repair-fixture', root)
        for key in ('baseline', 'target_evidence', 'allowed_scope', 'stop_conditions', 'recovery'):
            self.assertTrue(30 <= len(mission[key]) <= 650, (key, len(mission[key])))
        self.assertIn('No VM/runtime/API mutation', mission['stop_conditions'])
        self.assertIn('Do not mask failing assertions', mission['stop_conditions'])

    def test_capacity_reserves_new_scope_and_all_os_evidence(self):
        self.assertIn('tmp/local-experiment/kustomize-os-image', S.SCOPES)
        self.assertIn('tmp/local-experiment/kustomize-os-scan', S.SCOPES)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(S.P, 'measure', return_value={'charged_bytes':8*S.P.GIB}), \
                 mock.patch.object(S.os, 'statvfs') as fs:
                fs.return_value.f_bavail = 20*S.P.GIB; fs.return_value.f_frsize = 1
                with self.assertRaises(S.R.RunnerError): S.capacity(root)


if __name__ == '__main__': unittest.main()
