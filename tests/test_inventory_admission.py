import copy
import tempfile
import unittest
from unittest import mock

import test_admission_contract as fixtures
from tools.local_experiment import inventory_admission as D


class InventoryAdmissionTests(unittest.TestCase):
    setUp = fixtures.AdmissionContractTests.setUp
    def evaluate(self, inventory=None):
        with tempfile.TemporaryDirectory() as staging:
            if inventory is None:
                inventory = D.I.build(self.root, self.candidate, staging)
            return D.diagnose(self.root, self.candidate, inventory,
                              self.items, self.baseline, staging)

    def test_complete_real_inventory_preserves_all_occurrences_and_findings(self):
        value = self.evaluate()
        self.assertEqual(value['image_occurrences'], 9)
        self.assertEqual(value['unique_image_references'], 8)
        self.assertEqual(len([r for r in value['images'] if r['component'] not in D.C.S.FLUX]), 5)
        self.assertEqual({r['platform'] for r in value['images']}, {'linux/arm64'})
        flux = [r for r in value['images'] if r['component'] in D.C.S.FLUX]
        self.assertEqual(sorted(r['blocking_counts']['HIGH'] for r in flux), [6, 6, 6, 7])
        self.assertEqual(len({(r['namespace'], r['input_kind'], r['input_name'])
                              for r in value['unavailable_runtime_references']}), 5)
        for flag in ('strict_scan_all_pass', 'admission_verified', 'activation_authorized',
                     'gate_acceptance', 'runtime_inputs_complete'):
            self.assertFalse(value[flag])
        self.assertIsNone(value['local_risk_decision']['decision'])
        self.assertTrue(all(not r['fresh_cryptographic_verification'] and
                            not r['image_bytes_verified'] for r in value['images']))

    def test_saved_inventory_omission_or_approval_rejects(self):
        with tempfile.TemporaryDirectory() as staging:
            original = D.I.build(self.root, self.candidate, staging)
        for change in ('omit', 'reference', 'approval'):
            value = copy.deepcopy(original)
            if change == 'omit': value['images'].pop()
            if change == 'reference': value['images'][-1]['reference'] = 'other'
            if change == 'approval': value['local_risk_decision']['decision'] = 'approved'
            with self.subTest(change=change), self.assertRaisesRegex(D.R.RunnerError, 'INVENTORY_DRIFT'):
                self.evaluate(value)

    def test_unlisted_image_remains_gap_not_silent_omission(self):
        with tempfile.TemporaryDirectory() as staging:
            inventory = D.I.build(self.root, self.candidate, staging)
        inventory['images'][-1]['reference'] = 'unlisted@sha256:'+'f'*64
        # Isolate ledger matching after regeneration (tested independently above).
        with mock.patch.object(D.I, 'validate'):
            value = self.evaluate(inventory)
        self.assertEqual(len(value['images']), 9)
        self.assertEqual(value['images'][-1]['gaps'],
                         ['missing_or_ambiguous_exact_reference_ledger_identity'])
        self.assertFalse(value['strict_scan_all_pass'])
