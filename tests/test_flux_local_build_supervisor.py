from pathlib import Path
import tempfile
import hashlib
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

    def test_second_attempt_is_toolchain_specific_no_old_scope_writes(self):
        value=B.objective('a'*40,B.R.ROOT/'tmp/local-experiment/flux-local-build/build-test',True)
        self.assertIn('Go1.26.6',value['target_evidence'])
        self.assertIn('Second/final',value['target_evidence'])
        self.assertIn('no old cache writes',value['allowed_scope'])
        self.assertIn('Do not repeat terminated signature tests',value['target_evidence'])
        for key in ('baseline','target_evidence','allowed_scope','stop_conditions','recovery'):
            self.assertTrue(30<=len(value[key])<=650)

    def test_reobservation_accepts_only_exact_stdlib_repair_and_preserves_old_summary(self):
        from tools.local_experiment import capacity as C
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'tmp/local-experiment/flux-local-build/build-aoa39q7w'
            old.mkdir(parents=True);(old/'source').mkdir()
            names=('source-final-diff.stdout','source/go.mod','source/go.sum',
                   'binary-sbom.stdout','binary-sbom-scan-supported-flags.stdout')
            hashes={}
            for name in names:
                (old/name).write_bytes(b'fixture');hashes[name]=hashlib.sha256(b'fixture').hexdigest()
            (old/'summary.json').write_text('original capacity failure')
            record=dict(binary={'sha256':'a'*64},source=dict(
                commit='456fa323ef6a4b977f220cf2c60298bf193497ba',tree='8c7afc98517458261d6713f8c28668c5e2d14107'),hashes=hashes)
            scan={'Results':[{'Vulnerabilities':[dict(Severity='HIGH',PkgName='stdlib',
                InstalledVersion='1.26.5',FixedVersion='1.26.6') for _ in range(8)]}]}
            with mock.patch.object(B,'verify_binary',return_value={'sha256':'a'*64}), \
                 mock.patch.object(B.S,'baseline_file',side_effect=[record,scan]), \
                 mock.patch.object(C,'observe',return_value=dict(used_bytes=int(7.60*1024**3),
                      free_bytes=30*C.GIB,hard_limit_exceeded=False,bulk_writes_allowed=False)):
                result=B.reobserve_first_build(root)
                self.assertFalse(result['old_scope_writes'])
                self.assertFalse(result['operation_replay'])
            self.assertEqual((old/'summary.json').read_text(),'original capacity failure')
            scan['Results'][0]['Vulnerabilities'][0]['PkgName']='unrelated'
            with mock.patch.object(B,'verify_binary',return_value={'sha256':'a'*64}), \
                 mock.patch.object(B.S,'baseline_file',side_effect=[record,scan]):
                with self.assertRaises(B.R.RunnerError):B.reobserve_first_build(root)

    def test_post_worker_read_only_validation_uses_hard_not_bulk_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);lock=root/'tmp/local-experiment/gate-supervisor'
            lock.mkdir(parents=True);(lock/'lock').touch()
            (root/'tmp/local-experiment/gate-batches').mkdir()
            def private(path, error):
                if path.name=='candidate-inputs.json':return {'images':[]}
                if path.name=='stable-tag-observations.json':
                    return {'observations':[{'newer_same_series':False} for _ in range(4)]}
                return {}
            terminal=({'reason':'agent_no_safe_work_claim'},
                      {'reason':'no_safe_work','head_after':'a'*40},'known-session')
            def git(root, *args):
                return 'tools/local_experiment/flux_local_build.py' if args[0]=='diff' else ''
            with mock.patch.object(B.R,'ROOT',root), \
                 mock.patch.object(B.R,'checkout_head',side_effect=['a'*40,'b'*40]), \
                 mock.patch.object(B.S,'no_active_worker'), \
                 mock.patch.object(B.R,'subscription_preflight'), \
                 mock.patch.object(B.S,'check_resume_budget'), \
                 mock.patch.object(B.F,'stopped',return_value=[]), \
                 mock.patch.object(B.F,'inputs',return_value=([],'a'*64)), \
                 mock.patch.object(B.R,'private_json',side_effect=private), \
                 mock.patch.object(B.S,'baseline_file',return_value={'started_at':'2026-10-08T00:00:00Z'}), \
                 mock.patch.object(B.F,'verify_scans',return_value={}), \
                 mock.patch.object(B.hashlib,'sha256',return_value=mock.Mock(hexdigest=lambda:'a'*64)), \
                 mock.patch.object(Path,'read_bytes',return_value=b'fixture'), \
                 mock.patch.object(B.P.Scan,'check_budget') as writer_guard, \
                 mock.patch.object(B.P.Scan,'observation',return_value=dict(used_bytes=7*1024**3+1,
                     free_bytes=30*1024**3,hard_limit_exceeded=False,bulk_writes_allowed=False)), \
                 mock.patch.object(B.S,'terminal',return_value=terminal), \
                 mock.patch.object(B.R,'git',side_effect=git), \
                 mock.patch.object(B.R,'gate_preflight'), \
                 mock.patch.object(B.R,'run',return_value=('agent_no_safe_work_claim',root/'worker')), \
                 mock.patch.object(B,'verify_binary',return_value={'sha256':'a'*64}), \
                 mock.patch.object(B.S,'command',return_value=b'GOOS=linux GOARCH=arm64 github.com/fluxcd/source-controller'):
                B.run(root,root/'vault')
                writer_guard.assert_called_once()  # Only the new empty reservation.
            summary=next((root/'tmp/local-experiment/flux-local-build').glob('build-*/summary.json'))
            self.assertIn('BINARY_METADATA_EVIDENCE_ONLY',summary.read_text())
            self.assertIn('"admission":false',summary.read_text())


if __name__=='__main__':unittest.main()
