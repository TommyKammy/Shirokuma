from pathlib import Path
import tempfile
import unittest
from unittest import mock
from tools.local_experiment import flux_local_build_supervisor as B


class BuildTests(unittest.TestCase):
    def test_binary_is_inspected_without_execution_or_admission(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'source-controller'
            header=bytearray(64);header[:6]=b'\x7fELF\x02\x01';header[18:20]=(183).to_bytes(2,'little')
            B.R.private_file(path,header)
            value=B.verify_binary(path)
            self.assertFalse(value['executed']);self.assertFalse(value['image_acceptance'])
            self.assertEqual(len(value['sha256']),64)
            header[18:20]=(62).to_bytes(2,'little');path.write_bytes(header)
            with self.assertRaises(B.R.RunnerError):B.verify_binary(path)

    def test_symlink_and_executable_mode_reject(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'binary';B.R.private_file(path,b'not-elf')
            link=Path(tmp)/'link';link.symlink_to(path)
            with self.assertRaises(OSError):B.verify_binary(link)
            path.chmod(0o700)
            with self.assertRaises(B.R.RunnerError):B.verify_binary(path)

    def test_scope_is_one_binary_no_runtime_or_approval(self):
        v=B.objective('a'*40,B.R.ROOT/'tmp/local-experiment/flux-local-build/build-fixture')
        self.assertIn('ONE isolated',v['target_evidence'])
        self.assertIn('No approvals',v['stop_conditions'])
        self.assertIn('VM/container starts',v['allowed_scope'])
        for k in ('baseline','target_evidence','allowed_scope','stop_conditions','recovery'):
            self.assertTrue(30<=len(v[k])<=650)

    def test_plan_calls_nothing(self):
        with mock.patch('sys.argv',['build']),mock.patch.object(B,'run') as run:
            self.assertEqual(B.main(),0);run.assert_not_called()


if __name__=='__main__':unittest.main()
