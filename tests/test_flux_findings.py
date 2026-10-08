import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tools.local_experiment import flux_findings as F

ROOT = Path(__file__).resolve().parents[1]
COMMIT = '0486612d0671c01c16d40ce445e39788d86de69b'


class FluxFindingsTests(unittest.TestCase):
    def setUp(self):
        ledger = json.loads((ROOT/'security/resident-images.json').read_text())
        self.items = [{'kind': 'Deployment', 'metadata': {'namespace': 'flux-system',
                       'name': i['component'], 'uid': i['component']},
                       'spec': {'template': {'metadata': {'labels': {'app': i['component']}},
                                'spec': {'containers': [{'image': i['reference']}]}}}}
                      for i in ledger['images'] if i['component'] in F.S.FLUX]
        self.network = {'kind': 'List', 'items': []}
        self.now = datetime(2026, 10, 8, tzinfo=timezone.utc)

    def diagnose(self):
        # Runtime inhibition has its own complete live tests; this fixture tests
        # retained records, binding, clocks and non-approval output, not readiness.
        with patch.object(F.S, 'validate'):
            return F.diagnose(ROOT, COMMIT, self.items, [], self.network, self.now)

    def test_exact_retained_findings_and_no_approval(self):
        result = self.diagnose()
        counts = {r['component']: r['blocking_counts']['HIGH'] for r in result['controllers']}
        self.assertEqual(counts, {'source-controller': 7, 'helm-controller': 6,
                                 'kustomize-controller': 6, 'notification-controller': 6})
        self.assertTrue(all(r['freshness']['db_age_seconds'] > 54*86400 for r in result['controllers']))
        self.assertIsNone(result['riskDecision'])
        self.assertFalse(result['activation_authorized'])
        source = next(r for r in result['controllers'] if r['component'] == 'source-controller')
        self.assertIn(('CVE-2026-49478', 'v1.8.5', '1.8.6'),
                      [(f['record']['VulnerabilityID'], f['record']['InstalledVersion'],
                        f['record']['FixedVersion']) for f in source['blocking_findings']])

    def test_wrong_live_digest_rejects(self):
        self.items[0]['spec']['template']['spec']['containers'][0]['image'] += 'bad'
        with self.assertRaises(F.R.RunnerError):
            self.diagnose()

    def test_naive_future_and_unknown_dates_reject(self):
        for stamp in (None, 'bad', '2026-08-14T00:00:00', '2026-10-09T00:00:00Z'):
            with self.subTest(stamp=stamp), self.assertRaises(F.R.RunnerError):
                F.age(stamp, self.now)

    def test_network_scope_and_pagination_reject(self):
        for network in ({'kind': 'List', 'metadata': {'continue': 'next'}, 'items': []},
                        {'kind': 'List', 'items': [{'kind': 'Service', 'metadata': {'namespace': 'other'}}]}):
            self.network = copy.deepcopy(network)
            with self.assertRaises(F.R.RunnerError):
                self.diagnose()


if __name__ == '__main__':
    unittest.main()
