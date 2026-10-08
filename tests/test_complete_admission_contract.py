import copy
import json
import tempfile
import unittest

import test_admission_contract as fixtures
from tools.local_experiment import admission_contract as A


class CompleteAdmissionContractTests(unittest.TestCase):
    setUp = fixtures.AdmissionContractTests.setUp

    def build(self, staging):
        return A.build_complete(self.root, self.candidate, self.items, self.baseline, staging)

    def test_all_occurrences_commit_records_and_unavailable_references_bound(self):
        with tempfile.TemporaryDirectory() as staging:
            value = self.build(staging)
            self.assertTrue(A.validate_complete(self.root, value, self.candidate,
                                               self.items, self.baseline, staging))
        diagnosis = value['inventory_admission_diagnosis']
        self.assertEqual(diagnosis['image_occurrences'], 9)
        self.assertEqual(diagnosis['unique_image_references'], 8)
        self.assertEqual(len(diagnosis['unavailable_runtime_references']), 19)
        self.assertEqual(value['diagnosis_sha256'], A.C.digest(diagnosis))
        self.assertEqual(value['local_risk_decision'], {'status': 'missing', 'decision': None})
        self.assertEqual(sorted(r['blocking_counts']['HIGH'] for r in diagnosis['images']),
                         [0, 0, 0, 0, 0, 6, 6, 6, 7])
        for row in diagnosis['images']:
            self.assertEqual(row['platform'], 'linux/arm64')
            self.assertFalse(row['image_bytes_verified'])
            self.assertFalse(row['fresh_cryptographic_verification'])
            for artifact in row['artifacts'].values():
                path = self.root/artifact['path']
                A.commit_blob(self.root, self.candidate['identity']['commit'], path, path.read_bytes())
            scan = json.loads((self.root/row['artifacts']['scan_artifact']['path']).read_bytes())
            self.assertEqual(row['blocking_findings'], A.blocking_findings(scan, row['occurrence']['reference']))
            if row['blocking_counts']['HIGH']:
                self.assertEqual(row['checks']['strict_scan']['record_check'], 'blocked')
        for flag in ('runtime_inputs_complete', 'admission_verified', 'activation_authorized', 'gate_acceptance'):
            self.assertFalse(value[flag])
        self.assertIsNone(value['fetched_commit'])
        self.assertIsNone(value['applied_commit'])

    def test_tampering_rejects_even_with_recomputed_hashes(self):
        with tempfile.TemporaryDirectory() as staging:
            original = self.build(staging)
            for change in ('occurrence', 'finding', 'artifact', 'publisher', 'platform',
                           'reference_gap', 'approval', 'activation', 'fetch', 'inventory'):
                value = copy.deepcopy(original)
                diagnosis = value['inventory_admission_diagnosis']
                row = next(r for r in diagnosis['images'] if r['blocking_findings'])
                if change == 'occurrence': diagnosis['images'].pop()
                if change == 'finding': row['blocking_findings'].pop()
                if change == 'artifact': row['artifacts']['sbom_artifact']['sha256'] = '0'*64
                if change == 'publisher': row['recorded_publisher']['commit'] = '0'*40
                if change == 'platform': row['platform'] = 'linux/amd64'
                if change == 'reference_gap': diagnosis['unavailable_runtime_references'].pop()
                if change == 'approval': value['local_risk_decision']['decision'] = 'approved'
                if change == 'activation': value['activation_authorized'] = True
                if change == 'fetch': value['fetched_commit'] = self.candidate['identity']['commit']
                if change == 'inventory': value['transitive_render_inventory']['inventory']['images'].pop()
                value['diagnosis_sha256'] = A.C.digest(diagnosis)
                value['transitive_render_inventory']['inventory_sha256'] = A.C.digest(
                    value['transitive_render_inventory']['inventory'])
                with self.subTest(change=change), self.assertRaisesRegex(A.R.RunnerError, 'COMPLETE_CONTRACT_DRIFT'):
                    A.validate_complete(self.root, value, self.candidate, self.items, self.baseline, staging)

    def test_fresh_inhibition_drift_rejects(self):
        with tempfile.TemporaryDirectory() as staging:
            value = self.build(staging)
            self.items[-3]['spec']['suspend'] = False
            with self.assertRaises(A.R.RunnerError):
                A.validate_complete(self.root, value, self.candidate, self.items, self.baseline, staging)
