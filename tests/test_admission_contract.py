import copy
import json
import shutil
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from tools.local_experiment import admission_contract as A
from tools.local_experiment import render_inventory as I
from test_source_candidate import observations


class AdmissionContractTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.items = observations()
        ledger = json.loads((self.root/'security/resident-images.json').read_bytes())
        pins = {i['component']: i['reference'] for i in ledger['images']}
        for obj in self.items:
            if obj['kind'] == 'Deployment' and obj['metadata']['namespace'] == 'flux-system':
                obj['spec']['template']['spec']['containers'][0]['image'] = pins[obj['metadata']['name']]
            if obj['kind'] == 'Pod':
                name = obj['metadata']['name'].removesuffix('-pod')
                obj['spec']['containers'][0]['image'] = pins[name]
        self.baseline = copy.deepcopy(self.items)
        self.candidate = A.C.build(self.root, A.C.git(self.root, 'rev-parse', 'HEAD'),
                                   self.items, self.baseline)

    def build(self):
        return A.build(self.root, self.candidate, self.items, self.baseline)

    def test_real_retained_evidence_exact_findings_and_no_authority(self):
        original = copy.deepcopy(self.candidate)
        value = self.build()
        self.assertEqual(self.candidate, original)
        self.assertFalse(value['activation_authorized'])
        self.assertFalse(value['admission_verified'])
        self.assertFalse(value['gate_acceptance'])
        self.assertIsNone(value['local_risk_decision']['decision'])
        self.assertEqual(value['transitive_render_inventory']['status'], 'missing')
        self.assertEqual(sum(r['blocking_counts']['HIGH'] for r in value['controllers']), 25)
        self.assertEqual(sum(r['blocking_counts']['CRITICAL'] for r in value['controllers']), 0)
        for row in value['controllers']:
            scan = json.loads((self.root/row['artifacts']['scan_artifact']['path']).read_bytes())
            self.assertEqual(row['blocking_findings'], A.blocking_findings(scan, row['reference']))
            self.assertEqual(row['checks']['strict_scan']['record_check'], 'blocked')
            self.assertFalse(row['fresh_cryptographic_verification'])
            self.assertEqual(set(row['artifacts']), {'sbom_artifact', 'scan_artifact', 'supply_chain_artifact'})
        self.assertTrue(A.validate(self.root, value, self.candidate, self.items, self.baseline))

    def test_contract_tampering_rejected(self):
        original = self.build()
        for change in ('approval', 'activation', 'inventory', 'finding', 'hash', 'candidate', 'extra'):
            value = copy.deepcopy(original)
            if change == 'approval': value['local_risk_decision']['decision'] = 'approved'
            if change == 'activation': value['activation_authorized'] = True
            if change == 'inventory': value['transitive_render_inventory']['status'] = 'verified'
            if change == 'finding': value['controllers'][0]['blocking_findings'].pop()
            if change == 'hash': value['controllers'][0]['artifact_sha256']['scan_artifact'] = '0'*64
            if change == 'candidate': value['candidate']['source']['transport'] = 'chosen'
            if change == 'extra': value['approved'] = True
            with self.subTest(change=change), self.assertRaisesRegex(A.R.RunnerError, 'CONTRACT_DRIFT'):
                A.validate(self.root, value, self.candidate, self.items, self.baseline)

    @unittest.skipUnless(shutil.which('kubectl'), 'local kubectl renderer unavailable')
    def test_transitive_inventory_binding_preserves_strict_findings_and_rejects_approval(self):
        original = self.build()
        with tempfile.TemporaryDirectory() as staging:
            bound = I.bind_admission(self.root, original, self.candidate, self.items, self.baseline, staging)
            self.assertEqual(bound['controllers'], original['controllers'])
            self.assertEqual(bound['local_risk_decision'], {'status': 'missing', 'decision': None})
            self.assertFalse(bound['admission_verified'])
            self.assertFalse(bound['activation_authorized'])
            self.assertFalse(bound['gate_acceptance'])
            inventory = bound['transitive_render_inventory']['inventory']
            self.assertTrue(inventory['repository_inventory_complete'])
            self.assertFalse(inventory['runtime_inputs_complete'])
            self.assertEqual(len(inventory['renders']), 7)
            self.assertEqual(len(inventory['workloads']), 9)
            self.assertEqual(len(inventory['images']), 9)
            self.assertEqual(bound['transitive_render_inventory']['inventory_sha256'], A.C.digest(inventory))
            self.assertEqual(original['transitive_render_inventory']['status'], 'missing')
            original['local_risk_decision']['decision'] = 'approved'
            with self.assertRaisesRegex(A.R.RunnerError, 'CONTRACT_DRIFT'):
                I.bind_admission(self.root, original, self.candidate, self.items, self.baseline, staging)

    def test_candidate_inhibition_drift_rejected(self):
        self.items[-3]['spec']['suspend'] = False
        with self.assertRaises(A.R.RunnerError): self.build()

    def test_diagnostic_artifact_hash_drift_rejected(self):
        diagnosis = A.D.diagnose(self.root, self.candidate, self.items, self.baseline)
        diagnosis['controllers'][0]['artifact_sha256']['scan_artifact'] = '0'*64
        with mock.patch.object(A.D, 'diagnose', return_value=diagnosis):
            with self.assertRaisesRegex(A.R.RunnerError, 'ARTIFACT_DRIFT'): self.build()

    def test_worktree_evidence_not_in_candidate_commit_rejected(self):
        with self.assertRaisesRegex(A.R.RunnerError, 'COMMIT_BLOB_DRIFT'):
            A.commit_blob(self.root, self.candidate['identity']['commit'],
                          self.root/'security/resident-images.json', b'changed')

    def test_final_read_change_rejected(self):
        read = Path.read_bytes
        count = 0
        def changed(path):
            nonlocal count
            if path.name == 'resident-images.json':
                count += 1
                if count == 4: return b'changed'
            return read(path)
        with mock.patch.object(Path, 'read_bytes', changed):
            with self.assertRaisesRegex(A.R.RunnerError, 'LOCAL_EVIDENCE_CHANGED'): self.build()

    def test_finding_multiplicity_unfixed_and_critical_preserved(self):
        finding = {'Severity': 'HIGH', 'VulnerabilityID': 'CVE-fixture', 'PkgName': 'pkg',
                   'InstalledVersion': '1', 'FixedVersion': '', 'PkgPath': 'a/b'}
        scan = {'SchemaVersion': 2, 'ArtifactName': 'fixture', 'Results': [
            {'Target': 'target', 'Class': 'lang-pkgs', 'Type': 'gobinary',
             'Vulnerabilities': [finding, finding, dict(finding, Severity='CRITICAL')]}]}
        result = A.blocking_findings(scan, 'fixture')
        self.assertEqual(len(result), 3)
        self.assertEqual(sum(f['record']['Severity'] == 'HIGH' for f in result), 2)
        self.assertEqual(result[0]['record']['FixedVersion'], '')
        result[0]['record']['PkgName'] = 'changed'
        self.assertEqual(finding['PkgName'], 'pkg')

    def test_malformed_or_wrong_image_scan_rejected(self):
        scan = {'SchemaVersion': 2, 'ArtifactName': 'fixture', 'Results': [{'Target': 'target',
                'Vulnerabilities': [{'Severity': 'HIGH'}]}]}
        for value in (None, dict(scan, ArtifactName='other'), dict(scan, Results=[]), scan,
                      dict(scan, Results=[{'Target': 'target', 'Vulnerabilities': {}}])):
            with self.subTest(value=value), self.assertRaises(A.R.RunnerError):
                A.blocking_findings(value, 'fixture')


if __name__ == '__main__': unittest.main()
