import copy
from pathlib import Path
import unittest
from unittest import mock

from tools.local_experiment import admission_diagnosis as D


class AdmissionDiagnosisTests(unittest.TestCase):
    def setUp(self):
        self.candidate = {'identity': {'commit': 'a'*40}, 'controllers': [
            {'name': n, 'images': [n+'@sha256:'+'b'*64]} for n in sorted(D.C.S.FLUX)],
            'evidence': {'admission_verified': False, 'fetched_commit': None}}
        self.ledger = {'schema_version': 1, 'images': [dict(
            component=c['name'], reference=c['images'][0], platform='linux/arm64',
            version='fixture', source='fixture', sbom_artifact='sbom', scan_artifact='scan',
            supply_chain_artifact='chain', sbom_generator='fixture', scanner_version='fixture',
            vulnerability_db_updated_at='fixture') for c in self.candidate['controllers']]}
        self.records = {'images': [dict(component=i['component'], reference=i['reference'],
            signature={'issuer': 'fixture-issuer', 'commit': 'c'*40}) for i in self.ledger['images']]}
        patches = [mock.patch.object(D.C, 'build', return_value=copy.deepcopy(self.candidate)),
                   mock.patch.object(D.V, 'load_json', side_effect=lambda p:
                       self.ledger if p.name == 'resident-images.json' else self.records),
                   mock.patch.object(D.V, 'evidence_artifact_path', return_value=Path('fixture')),
                   mock.patch.object(Path, 'read_bytes', return_value=b'fixture'),
                   mock.patch.object(D.V, 'check_sbom'),
                   mock.patch.object(D.V, 'check_trivy'),
                   mock.patch.object(D.V, 'check_supply_chain_evidence')]
        self.mocks = []
        for patch in patches:
            self.mocks.append(patch.start())
            self.addCleanup(patch.stop)

    def run_diagnosis(self):
        return D.diagnose(Path('.'), self.candidate, [], [])

    def test_successful_records_never_become_admission_or_publisher_crypto_proof(self):
        original = copy.deepcopy(self.candidate)
        value = self.run_diagnosis()
        self.assertEqual(self.candidate, original)
        self.assertTrue(value['strict_scan_all_pass'])
        self.assertFalse(value['admission_verified'])
        self.assertFalse(value['gate_acceptance'])
        self.assertEqual(len(value['controllers']), 4)
        for row in value['controllers']:
            self.assertFalse(row['fresh_cryptographic_verification'])
            self.assertFalse(row['canonical_retained_bundle_check'])
            self.assertEqual(row['recorded_publisher']['commit'], 'c'*40)
        for call in self.mocks[5].call_args_list:
            self.assertNotIn('allowed_high', call.kwargs)

    def test_strict_failure_retained_and_other_evidence_still_evaluated(self):
        self.mocks[5].side_effect = D.V.PolicyError('HIGH=7')
        value = self.run_diagnosis()
        self.assertFalse(value['strict_scan_all_pass'])
        for row in value['controllers']:
            self.assertEqual(row['checks']['strict_scan']['reason'], 'HIGH=7')
            self.assertEqual(row['checks']['publisher_record']['record_check'], 'pass')

    def test_publisher_failure_not_masked_by_valid_pin_or_sbom(self):
        self.mocks[6].side_effect = D.V.PolicyError('publisher mismatch')
        for row in self.run_diagnosis()['controllers']:
            self.assertEqual(row['checks']['publisher_record']['record_check'], 'blocked')

    def test_candidate_drift_rejected_before_artifact_reads(self):
        self.candidate['evidence']['fetched_commit'] = 'a'*40
        with self.assertRaisesRegex(D.C.S.R.RunnerError, 'ADMISSION_CANDIDATE_DRIFT'):
            self.run_diagnosis()
        self.mocks[1].assert_not_called()

    def test_ledger_missing_duplicate_reference_or_platform_drift_rejected(self):
        original = copy.deepcopy(self.ledger)
        for change in ('missing', 'duplicate', 'reference', 'platform'):
            self.ledger = copy.deepcopy(original)
            if change == 'missing': self.ledger['images'].pop(0)
            if change == 'duplicate': self.ledger['images'].append(self.ledger['images'][0])
            if change == 'reference': self.ledger['images'][0]['reference'] = 'other'
            if change == 'platform': self.ledger['images'][0]['platform'] = 'linux/amd64'
            with self.subTest(change=change), self.assertRaises(D.C.S.R.RunnerError):
                self.run_diagnosis()

    def test_missing_publisher_record_is_not_unknown_success(self):
        self.records['images'].pop(0)
        with self.assertRaisesRegex(D.C.S.R.RunnerError, 'ADMISSION_PUBLISHER_IDENTITY_MISSING'):
            self.run_diagnosis()

    def test_artifact_or_ledger_change_during_checks_rejects_hash_binding(self):
        for change in ('artifact', 'ledger'):
            self.mocks[3].side_effect = ([b'fixture']*4+[b'changed']*3 if change == 'artifact'
                                        else [b'fixture']*25+[b'changed'])
            with self.subTest(change=change), self.assertRaisesRegex(
                    D.C.S.R.RunnerError, 'ADMISSION_ARTIFACT_CHANGED' if change == 'artifact'
                    else 'ADMISSION_LEDGER_CHANGED'):
                self.run_diagnosis()
        self.mocks[3].side_effect = None


if __name__ == '__main__': unittest.main()
