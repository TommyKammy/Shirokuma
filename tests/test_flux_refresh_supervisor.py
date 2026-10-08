from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import tempfile
import unittest
from unittest import mock

from tools.local_experiment import flux_refresh_supervisor as F


class RefreshTests(unittest.TestCase):
    def fixture(self, directory):
        now=datetime.now(timezone.utc)
        images=[dict(component=x, reference='ghcr.io/fluxcd/'+x+'@sha256:'+'a'*64)
                for x in sorted(F.S.FLUX)]
        F.R.save_json(directory/'scanner-version.json',dict(Version='1',
                      VulnerabilityDB={'UpdatedAt':now.isoformat()}))
        for image in images:
            F.R.save_json(directory/(image['component']+'.trivy.json'),dict(
                ArtifactName=image['reference'], CreatedAt=now.isoformat(), Trivy={'Version':'1'},
                Metadata={'RepoDigests':[image['reference']],
                          'ImageConfig':{'architecture':'arm64','os':'linux'}},
                Results=[{'Vulnerabilities':[{'Severity':'HIGH','VulnerabilityID':'CVE-test'}]}]))
        return now,images

    def test_fresh_high_evidence_does_not_grant_admission(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp); now,images=self.fixture(d)
            value=F.verify_scans(d,images,now.timestamp())
            self.assertTrue(value['evidence_refresh_verified'])
            self.assertFalse(value['admission'])
            self.assertFalse(value['risk_approval'])
            self.assertEqual([s['high'] for s in value['scans']],[1]*4)

    def test_wrong_platform_digest_scan_age_and_db_age_reject(self):
        with tempfile.TemporaryDirectory() as tmp:
            for field in ('platform','digest','age','db','scanner','missing'):
                d=Path(tmp)/field; d.mkdir()
                now,images=self.fixture(d)
                p=d/(images[0]['component']+'.trivy.json')
                value=F.S.baseline_file(p)
                if field=='platform': value['Metadata']['ImageConfig']['architecture']='amd64'
                if field=='digest': value['ArtifactName']='ghcr.io/other@sha256:'+'b'*64
                if field=='age': value['CreatedAt']=(now-timedelta(days=55)).isoformat()
                if field=='scanner': value['Trivy']['Version']='wrong'
                if field=='db':
                    (d/'scanner-version.json').write_text(json.dumps(dict(Version='1',
                        VulnerabilityDB={'UpdatedAt':(now-timedelta(days=55)).isoformat()})))
                p.write_text(json.dumps(value))
                if field=='missing': p.unlink()
                with self.assertRaises((F.R.RunnerError,OSError)):
                    F.verify_scans(d,images,now.timestamp())

    def test_vm_running_rejects_without_start_or_stop(self):
        with mock.patch.object(F.S,'command',return_value=b'{"name":"mac-studio-solo","status":"Running"}') as call:
            with self.assertRaises(F.R.RunnerError): F.stopped(Path('/unused'))
            self.assertEqual(call.call_count,1)

    def test_plan_makes_no_calls(self):
        with mock.patch('sys.argv',['refresh']),mock.patch.object(F,'run') as run:
            self.assertEqual(F.main(),0)
            run.assert_not_called()

    def test_persistent_reservation_limit_cannot_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            parent=root/'tmp/local-experiment/flux-refresh'; parent.mkdir(parents=True,mode=0o700)
            for n in range(2): (parent/f'refresh-{n}').mkdir()
            lock=root/'tmp/local-experiment/gate-supervisor'; lock.mkdir()
            (lock/'lock').touch()
            with mock.patch.object(F.R,'run') as launch:
                with self.assertRaisesRegex(F.R.RunnerError,'REFRESH_APPROACH_EXHAUSTED'):
                    F.run(root,root/'vault')
                launch.assert_not_called()

    def test_mission_scope_and_lengths(self):
        value=F.mission('a'*40,F.R.ROOT/'tmp/local-experiment/flux-refresh/refresh-fixture')
        self.assertEqual(value['gate_id'],F.GATE)
        self.assertIn('VM/container starts',value['allowed_scope'])
        self.assertIn('No risk approvals',value['stop_conditions'])
        for key in ('baseline','target_evidence','allowed_scope','stop_conditions','recovery'):
            self.assertTrue(30<=len(value[key])<=650)


if __name__=='__main__': unittest.main()
