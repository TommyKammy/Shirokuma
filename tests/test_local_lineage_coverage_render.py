"""Coverage semantics, escaping, and offline accessibility; no live services."""
import copy
from html.parser import HTMLParser
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.local_experiment.metadata.coverage_render import RenderError, render


def run(number=1, statuses=('present', 'present'), *, source_status='valid', eligible=True):
    token = f'{number:032x}'
    return {'run_token': token, 'operation': 'name_lengths' if eligible else None,
        'source_status': source_status, 'eligible': eligible,
        'reason_codes': ['RECORDED_RUN_FAILED'] if source_status == 'failed' else
                        ['SOURCE_VERIFIED', 'SOURCE_CHANGED_OR_UNVERIFIABLE'] if source_status == 'incomplete' and eligible else
                        ['SOURCE_VERIFIED'] if source_status == 'valid' else [],
        'source_sha256_before': 'a' * 64 if eligible else None,
        'source_sha256_after': 'a' * 64 if eligible else None,
        'metadata_sha256_before': 'b' * 64 if eligible else None,
        'metadata_sha256_after': 'b' * 64 if eligible else None,
        'edges': [{'target': target, 'from': 'shirokuma_local.iceberg.smoke.fixture_v1' if target == 'stg'
                  else f'shirokuma_local.iceberg.local_experiment.dataops_{token}_stg',
                  'to': f'shirokuma_local.iceberg.local_experiment.dataops_{token}_{target}',
                  'status': status, 'reason_code': status.upper()} for target, status in zip(('stg', 'mart'), statuses)]
                  if eligible else []}


def report(runs=None, unrecognized=0):
    runs = [run()] if runs is None else runs
    counts = {status: sum(edge['status'] == status for item in runs for edge in item['edges'])
              for status in ('present', 'missing', 'conflict', 'unknown')}
    eligible = sum(item['eligible'] for item in runs)
    denominator = eligible * 2
    known = denominator - counts['unknown']
    source_stable = all(item['source_sha256_before'] is not None and
                        item['source_sha256_before'] == item['source_sha256_after'] for item in runs)
    reasons = (['UNVERIFIED_SOURCE_RUNS'] if any(item['source_status'] in ('failed', 'invalid', 'incomplete') for item in runs) else [])
    if unrecognized:
        reasons.append('UNRECOGNIZED_SOURCE_ENTRIES')
    if counts['unknown']:
        reasons.append('UNKNOWN_EXPECTED_EDGES')
    return {'schema_version': 1, 'observed_at': '2026-09-17T07:30:00.000000Z',
        'scope': 'verified-historical-data-plan-lineage',
        'observation': {'status': 'incomplete' if reasons or not source_stable else 'complete',
            'source_stable': source_stable, 'metadata_stable': None if not eligible or counts['unknown'] else True,
            'reason_codes': reasons},
        'inventory': {'entries': len(runs) + unrecognized, 'uuid_runs': len(runs), 'eligible': eligible, 'complete': True,
            **{state: sum(item['source_status'] == state for item in runs)
               for state in ('valid', 'failed', 'incomplete', 'invalid')}, 'unrecognized_entries': unrecognized},
        'coverage': {'denominator': denominator, **counts, 'known': known,
            'rate': counts['present'] / denominator if denominator and not counts['unknown'] else None,
            'observation_rate': known / denominator if denominator else None,
            'complete': denominator > 0 and counts['present'] == denominator},
        'runs': runs, 'limits': ['保存済みData Planの履歴だけを対象とします。']}


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.tags, self.text = [], []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, text):
        self.text.append(text)


class CoverageRenderTests(unittest.TestCase):
    def test_render_is_pure_and_complete_coverage_is_explicitly_historical(self):
        value = report()
        saved = copy.deepcopy(value)
        html = render(value)
        self.assertEqual(value, saved)
        self.assertEqual(render(value), html)
        self.assertIn('2 / 2 エッジ', html)
        self.assertIn('登録率 100.0%', html)
        self.assertIn('実験の履歴metadata', html)
        self.assertIn('live tableの存在・稼働状態・現在のデータ品質を表しません', html)
        self.assertIn('生成テーブルは実験後に削除', html)
        self.assertIn('観測時点の静的レポート', html)
        self.assertIn('2026-09-17T07:30:00.000000Z', html)

    def test_unknown_keeps_denominator_and_suppresses_success_rate(self):
        value = report([run(1, ('present', 'missing')), run(2, ('conflict', 'unknown'))])
        value['observation']['reason_codes'] = ['UNKNOWN_EXPECTED_EDGES']
        html = render(value)
        self.assertIn('1 / 4 エッジ', html)
        self.assertIn('登録率 算出しない', html)
        self.assertIn('観測率 75.0%', html)
        self.assertIn('観測済みのエッジだけへ分母を縮小しません', html)
        self.assertIn('観測未完了', html)
        document = Document(html)
        self.assertEqual(sum(tag == 'details' and 'open' in attrs for tag, attrs in document.tags), 2)
        for status in ('登録を確認', '欠落', '競合', '不明'):
            self.assertIn(status, ''.join(document.text))

    def test_unknown_edges_require_saved_observation_reason(self):
        value = report([run(1, ('unknown', 'unknown'), source_status='incomplete')])
        value['observation']['source_stable'] = False
        value['runs'][0]['source_sha256_after'] = None
        value['observation']['reason_codes'] = ['UNVERIFIED_SOURCE_RUNS']
        with self.assertRaisesRegex(RenderError, 'INCONSISTENT_UNKNOWN_REASON'):
            render(value)
        value['observation']['reason_codes'].append('UNKNOWN_EXPECTED_EDGES')
        self.assertIn('登録率 算出しない', render(value))

    def test_saved_unknown_reason_requires_unknown_edges(self):
        value = report()
        value['observation'].update(status='incomplete', reason_codes=['UNKNOWN_EXPECTED_EDGES'])
        with self.assertRaisesRegex(RenderError, 'INCONSISTENT_UNKNOWN_REASON'):
            render(value)
        value['observation'].update(status='complete', reason_codes=[])
        self.assertIn('2 / 2 エッジ', render(value))

    def test_saved_unverified_source_reason_requires_failed_invalid_or_incomplete_run(self):
        for runs in ([run()], []):
            value = report(runs)
            value['observation'].update(status='incomplete', reason_codes=['UNVERIFIED_SOURCE_RUNS'])
            with self.subTest(runs=len(runs)), self.assertRaisesRegex(RenderError, 'INCONSISTENT_SOURCE_REASON'):
                render(value)
        value = report([run(1, source_status='invalid', eligible=False)])
        value['observation'].update(status='incomplete', reason_codes=['UNVERIFIED_SOURCE_RUNS'])
        self.assertIn('UNVERIFIED_SOURCE_RUNS', render(value))

    def test_failed_run_requires_saved_observation_reason(self):
        value = report([run(1, source_status='failed', eligible=False)])
        value['observation'].update(status='complete', reason_codes=[])
        with self.assertRaisesRegex(RenderError, 'INCONSISTENT_OBSERVATION_COMPLETION|INCONSISTENT_SOURCE_REASON'):
            render(value)
        value['observation'].update(status='incomplete', reason_codes=['UNVERIFIED_SOURCE_RUNS'])
        self.assertIn('UNVERIFIED_SOURCE_RUNS', render(value))

    def test_failed_run_requires_its_saved_failure_explanation(self):
        value = report([run(1, source_status='failed', eligible=False)])
        value['runs'][0]['reason_codes'] = []
        with self.assertRaisesRegex(RenderError, 'INCONSISTENT_SOURCE_REASON'):
            render(value)
        value['runs'][0]['reason_codes'] = ['RECORDED_RUN_FAILED']
        self.assertIn('RECORDED_RUN_FAILED', render(value))

    def test_valid_run_requires_its_saved_verification_explanation(self):
        value = report()
        value['runs'][0]['reason_codes'] = []
        with self.assertRaisesRegex(RenderError, 'INCONSISTENT_SOURCE_REASON'):
            render(value)
        value['runs'][0]['reason_codes'] = ['SOURCE_VERIFIED']
        self.assertIn('SOURCE_VERIFIED', render(value))

    def test_eligible_demoted_run_requires_saved_drift_explanation(self):
        value = report([run(1, ('unknown', 'unknown'), source_status='incomplete')])
        value['runs'][0]['source_sha256_after'] = None
        value['runs'][0]['reason_codes'] = ['SOURCE_VERIFIED']
        value['observation']['source_stable'] = False
        with self.assertRaisesRegex(RenderError, 'INCONSISTENT_SOURCE_REASON'):
            render(value)
        value['runs'][0]['reason_codes'].append('SOURCE_CHANGED_OR_UNVERIFIABLE')
        self.assertIn('SOURCE_CHANGED_OR_UNVERIFIABLE', render(value))

    def test_eligible_demoted_run_retains_initial_verification_explanation(self):
        value = report([run(1, ('unknown', 'unknown'), source_status='incomplete')])
        value['runs'][0]['source_sha256_after'] = None
        value['observation']['source_stable'] = False
        value['runs'][0]['reason_codes'] = ['SOURCE_CHANGED_OR_UNVERIFIABLE']
        with self.assertRaisesRegex(RenderError, 'INCONSISTENT_SOURCE_REASON'):
            render(value)
        value['runs'][0]['reason_codes'].insert(0, 'SOURCE_VERIFIED')
        self.assertIn('SOURCE_VERIFIED', render(value))

    def test_invalid_or_incomplete_source_requires_saved_observation_reason(self):
        for status in ('invalid', 'incomplete'):
            value = report([run(1, source_status=status, eligible=False)])
            value['observation'].update(status='incomplete', reason_codes=[])
            with self.subTest(status=status), self.assertRaisesRegex(RenderError, 'INCONSISTENT_SOURCE_REASON'):
                render(value)
            value['observation']['reason_codes'].append('UNVERIFIED_SOURCE_RUNS')
            self.assertIn('UNVERIFIED_SOURCE_RUNS', render(value))

    def test_unrecognized_entries_require_saved_observation_reason(self):
        value = report([], unrecognized=2)
        value['observation']['reason_codes'] = ['SOURCE_UNAVAILABLE']
        with self.assertRaisesRegex(RenderError, 'INCONSISTENT_INVENTORY_REASON'):
            render(value)
        value['observation']['reason_codes'].append('UNRECOGNIZED_SOURCE_ENTRIES')
        self.assertIn('識別できない項目は2件', render(value))
        value = report([])
        value['observation'].update(status='incomplete', reason_codes=['UNRECOGNIZED_SOURCE_ENTRIES'])
        with self.assertRaisesRegex(RenderError, 'INCONSISTENT_INVENTORY_REASON'):
            render(value)

    def test_zero_denominator_is_not_zero_or_full_coverage(self):
        html = render(report([]))
        self.assertIn('集計対象なし', html)
        self.assertIn('分母が0のため登録率・観測率は算出できません', html)
        self.assertNotIn('登録率 100.0%', html)
        self.assertNotIn('登録率 0.0%', html)
        self.assertIn('集計対象の実験履歴はありません', html)

    def test_incomplete_observation_needs_a_failure_or_unstable_source(self):
        for runs in ([run()], []):
            value = report(runs)
            value['observation']['status'] = 'incomplete'
            with self.subTest(runs=len(runs)), self.assertRaisesRegex(RenderError, 'INCONSISTENT_OBSERVATION_COMPLETION'):
                render(value)
            value['inventory']['unrecognized_entries'] = 1
            value['inventory']['entries'] += 1
            value['observation']['reason_codes'] = ['UNRECOGNIZED_SOURCE_ENTRIES']
            self.assertIn('観測未完了', render(value))

    def test_partial_inventory_requires_saved_inventory_failure_reason(self):
        value = report([])
        value['inventory']['complete'] = False
        value['observation'].update(status='incomplete', source_stable=False, reason_codes=[])
        with self.assertRaisesRegex(RenderError, 'INCONSISTENT_INVENTORY_REASON'):
            render(value)
        value['observation']['reason_codes'] = ['SOURCE_INVENTORY_UNAVAILABLE']
        self.assertIn('保存証跡の一覧取得は未完了', render(value))

    def test_inventory_failure_reason_cannot_claim_complete_inventory(self):
        for reason in ('SOURCE_INVENTORY_CHANGED', 'SOURCE_INVENTORY_UNAVAILABLE'):
            value = report()
            value['observation'].update(status='incomplete', reason_codes=[reason])
            with self.subTest(reason=reason), self.assertRaisesRegex(RenderError, 'INCONSISTENT_INVENTORY_REASON'):
                render(value)
            value['inventory']['complete'] = False
            value['observation']['source_stable'] = False
            for edge in value['runs'][0]['edges']:
                edge.update(status='unknown', reason_code=reason)
            value['coverage'].update(present=0, unknown=2, known=0, rate=None, observation_rate=0.0, complete=False)
            value['observation']['reason_codes'].append('UNKNOWN_EXPECTED_EDGES')
            self.assertIn('保存証跡の一覧取得は未完了', render(value))

    def test_zero_eligible_catalog_stability_is_unobserved(self):
        for stable in (True, False):
            value = report([])
            value['observation']['status'] = 'incomplete'
            value['observation']['metadata_stable'] = stable
            with self.subTest(stable=stable), self.assertRaises(RenderError):
                render(value)
        self.assertIn('未観測', render(report([])))

    def test_eligible_edges_follow_fixed_run_identity(self):
        for target, field, replacement in (('stg', 'from', 'shirokuma_local.iceberg.smoke.other'),
                                           ('stg', 'to', 'shirokuma_local.iceberg.local_experiment.dataops_00000000000000000000000000000002_stg'),
                                           ('mart', 'from', 'shirokuma_local.iceberg.smoke.fixture_v1'),
                                           ('mart', 'to', 'shirokuma_local.iceberg.local_experiment.dataops_00000000000000000000000000000002_mart')):
            value = report()
            next(edge for edge in value['runs'][0]['edges'] if edge['target'] == target)[field] = replacement
            with self.subTest(target=target, field=field), self.assertRaises(RenderError):
                render(value)
        self.assertIn('2 / 2 エッジ', render(report()))

    def test_malformed_edge_target_fails_as_render_error(self):
        for target in ([], {}, None, 42):
            value = report()
            value['runs'][0]['edges'][0]['target'] = target
            with self.subTest(target=target), self.assertRaises(RenderError):
                render(value)
        self.assertIn('2 / 2 エッジ', render(report()))

    def test_excluded_failed_invalid_and_incomplete_sources_remain_visible(self):
        runs = [run(), run(2, source_status='failed', eligible=False),
                run(3, source_status='invalid', eligible=False), run(4, source_status='incomplete', eligible=False)]
        runs[2]['reason_codes'] = ['SOURCE_INVALID']
        html = render(report(runs, unrecognized=2))
        self.assertIn('3件を対象外', html)
        self.assertIn('識別できない項目は2件', html)
        self.assertIn('SOURCE_INVALID', html)
        self.assertEqual(html.count('分母から除外'), 3)
        for item in runs:
            self.assertIn(item['run_token'], html)

    def test_changed_eligible_source_remains_two_unknown_edges(self):
        value = report([run(1, ('unknown', 'unknown'), source_status='incomplete')])
        value['observation']['source_stable'] = False
        value['runs'][0]['source_sha256_after'] = 'c' * 64
        html = render(value)
        self.assertIn('0 / 2 エッジ', html)
        self.assertIn('分母を保持 / 2エッジ不明', html)
        self.assertIn('観測途中に証跡が変化', html)

    def test_incomplete_eligible_source_cannot_claim_unchanged_final_snapshot(self):
        value = report([run(1, ('unknown', 'unknown'), source_status='incomplete')])
        value['observation'].update(status='incomplete', source_stable=False)
        with self.assertRaises(RenderError):
            render(value)
        value['runs'][0]['source_sha256_after'] = None
        self.assertIn('分母を保持 / 2エッジ不明', render(value))

    def test_source_status_must_match_original_eligibility(self):
        excluded_valid = report([run(1, source_status='valid', eligible=False)])
        included_invalid = report([run(1, ('unknown', 'unknown'), source_status='invalid')])
        for value in (excluded_valid, included_invalid):
            with self.subTest(source=value['runs'][0]['source_status']), self.assertRaises(RenderError):
                render(value)

    def test_source_stability_claim_requires_matching_saved_snapshots(self):
        for after in (None, 'c' * 64):
            value = report()
            value['runs'][0]['source_sha256_after'] = after
            with self.subTest(after=after), self.assertRaises(RenderError):
                render(value)
        self.assertIn('2 / 2 エッジ', render(report()))

    def test_eligible_run_requires_initial_evidence_and_valid_run_cannot_drift(self):
        for before, after in ((None, None), ('a' * 64, 'c' * 64)):
            value = report()
            value['runs'][0]['source_sha256_before'] = before
            value['runs'][0]['source_sha256_after'] = after
            value['observation'].update(status='incomplete', source_stable=False,
                                        reason_codes=['SOURCE_INVENTORY_UNAVAILABLE'])
            with self.subTest(before=before, after=after), self.assertRaises(RenderError):
                render(value)

    def test_valid_source_requires_final_saved_evidence_even_in_incomplete_observation(self):
        value = report(unrecognized=1)
        value['runs'][0]['source_sha256_after'] = None
        value['observation'].update(status='incomplete', source_stable=False,
                                     reason_codes=['UNRECOGNIZED_SOURCE_ENTRIES'])
        with self.assertRaises(RenderError):
            render(value)
        value['runs'][0]['source_sha256_after'] = 'a' * 64
        value['observation']['source_stable'] = True
        self.assertIn('2 / 2 エッジ', render(value))

    def test_catalog_stability_claim_requires_matching_saved_snapshots(self):
        for after in (None, 'c' * 64):
            value = report()
            value['runs'][0]['metadata_sha256_after'] = after
            with self.subTest(after=after), self.assertRaises(RenderError):
                render(value)
        self.assertIn('2 / 2 エッジ', render(report()))

    def test_known_edges_require_matching_catalog_snapshots_without_global_stability_claim(self):
        for before, after in ((None, None), ('b' * 64, None), ('b' * 64, 'c' * 64)):
            value = report(unrecognized=1)
            value['observation'].update(status='incomplete', metadata_stable=None,
                                         reason_codes=['UNRECOGNIZED_SOURCE_ENTRIES'])
            value['runs'][0]['metadata_sha256_before'] = before
            value['runs'][0]['metadata_sha256_after'] = after
            with self.subTest(before=before, after=after), self.assertRaises(RenderError):
                render(value)
        value['runs'][0]['metadata_sha256_before'] = 'b' * 64
        value['runs'][0]['metadata_sha256_after'] = 'b' * 64
        self.assertIn('2 / 2 エッジ', render(value))

    def test_catalog_instability_cannot_accompany_fully_known_edges(self):
        value = report()
        value['observation'].update(status='incomplete', metadata_stable=False,
                                    reason_codes=['UNKNOWN_EXPECTED_EDGES'])
        with self.assertRaises(RenderError):
            render(value)

    def test_complete_inventory_and_equal_source_snapshots_cannot_claim_instability(self):
        value = report()
        value['observation'].update(status='incomplete', source_stable=False,
                                    reason_codes=['SOURCE_INVENTORY_UNAVAILABLE'])
        with self.assertRaises(RenderError):
            render(value)
        value['inventory']['complete'] = False
        for edge in value['runs'][0]['edges']:
            edge.update(status='unknown', reason_code='SOURCE_INVENTORY_CHANGED')
        value['coverage'].update(present=0, unknown=2, known=0, rate=None,
                                 observation_rate=0.0, complete=False)
        value['observation']['reason_codes'].append('UNKNOWN_EXPECTED_EDGES')
        self.assertIn('保存証跡の一覧取得は未完了', render(value))

    def test_known_edge_reason_cannot_contradict_saved_status(self):
        for code in ('VERIFIED_ABSENT', 'CATALOG_CONFLICT', 'CATALOG_UNAVAILABLE',
                     'CATALOG_CHANGED', 'SOURCE_CHANGED'):
            value = report()
            value['runs'][0]['edges'][0]['reason_code'] = code
            with self.subTest(code=code), self.assertRaises(RenderError):
                render(value)
        self.assertIn('2 / 2 エッジ', render(report()))

    def test_partial_inventory_is_not_presented_as_the_complete_population(self):
        value = report()
        value['inventory']['complete'] = False
        value['observation']['status'] = 'incomplete'
        value['observation']['source_stable'] = False
        value['observation']['reason_codes'] = ['SOURCE_INVENTORY_CHANGED', 'UNKNOWN_EXPECTED_EDGES']
        for edge in value['runs'][0]['edges']:
            edge.update(status='unknown', reason_code='SOURCE_INVENTORY_CHANGED')
        value['coverage'].update(present=0, unknown=2, known=0, rate=None,
                                 observation_rate=0.0, complete=False)
        html = render(value)
        self.assertIn('保存証跡の一覧取得は未完了です', html)
        self.assertIn('取得できた範囲の集計', html)
        self.assertIn('全実験を網羅した結果とは扱えません', html)

    def test_incomplete_inventory_cannot_claim_stable_source_population(self):
        value = report()
        value['inventory']['complete'] = False
        value['observation'].update(status='incomplete', reason_codes=['SOURCE_INVENTORY_CHANGED', 'UNKNOWN_EXPECTED_EDGES'])
        for edge in value['runs'][0]['edges']:
            edge.update(status='unknown', reason_code='SOURCE_INVENTORY_CHANGED')
        value['coverage'].update(present=0, unknown=2, known=0, rate=None,
                                 observation_rate=0.0, complete=False)
        with self.assertRaises(RenderError):
            render(value)
        value['observation']['source_stable'] = False
        self.assertIn('保存証跡の一覧取得は未完了', render(value))

    def test_unverified_inventory_and_changed_eligible_run_cannot_claim_present_edges(self):
        value = report()
        value['inventory']['complete'] = False
        value['observation'].update(status='incomplete', source_stable=False)
        with self.assertRaises(RenderError):
            render(value)

        value = report([run(1, ('present', 'present'), source_status='incomplete')])
        value['observation'].update(status='incomplete', source_stable=False)
        value['runs'][0]['source_sha256_after'] = None
        with self.assertRaises(RenderError):
            render(value)

    def test_complete_observation_requires_verified_source_and_no_failures(self):
        for mutate in (lambda value: value['observation'].update(source_stable=False),
                       lambda value: value['observation'].update(reason_codes=['SOURCE_INVENTORY_CHANGED']),
                       lambda value: value['observation'].update(metadata_stable=False),
                       lambda value: value['observation'].update(metadata_stable=None)):
            value = report()
            mutate(value)
            with self.assertRaises(RenderError):
                render(value)

    def test_eligible_source_requires_saved_operation(self):
        value = report()
        value['runs'][0]['operation'] = None
        with self.assertRaises(RenderError):
            render(value)
        self.assertIn('2 / 2 エッジ', render(report()))

    def test_complete_observation_cannot_hide_unverified_population(self):
        for runs, unrecognized in (([run(), run(2, source_status='failed', eligible=False)], 0),
                                   ([run(), run(2, source_status='invalid', eligible=False)], 0),
                                   ([run(), run(2, source_status='incomplete', eligible=False)], 0),
                                   ([run()], 1)):
            value = report(runs, unrecognized=unrecognized)
            for item in value['runs']:
                item['source_sha256_before'] = item['source_sha256_after'] = 'a' * 64
            value['observation']['status'] = 'complete'
            value['observation']['source_stable'] = True
            with self.subTest(runs=len(runs), unrecognized=unrecognized), self.assertRaises(RenderError):
                render(value)

    def test_all_data_strings_are_text_not_active_markup(self):
        payload = '<img src="https://foreign.invalid/x" onerror="alert(1)"><script>alert(2)</script>&'
        value = report()
        value['runs'][0]['edges'][0]['reason_code'] = payload
        value['runs'][0]['reason_codes'] = ['SOURCE_VERIFIED', payload]
        value['limits'] = [payload]
        html = render(value)
        document = Document(html)
        self.assertIn('&lt;img', html)
        self.assertNotIn(payload, html)
        self.assertFalse(any(tag in ('script', 'img', 'iframe', 'object', 'embed', 'link') for tag, _ in document.tags))
        self.assertFalse(any(key.startswith('on') for _, attrs in document.tags for key in attrs))
        self.assertIn(payload, ''.join(document.text))

    def test_semantic_tables_keyboard_details_and_no_external_assets(self):
        html = render(report())
        document = Document(html)
        self.assertIn(('html', {'lang': 'ja'}), document.tags)
        self.assertTrue(any(tag == 'meta' and attrs.get('http-equiv') == 'Content-Security-Policy'
                            and "default-src 'none'" in attrs.get('content', '') for tag, attrs in document.tags))
        self.assertTrue(any(tag == 'main' for tag, _ in document.tags))
        self.assertEqual(sum(tag == 'caption' for tag, _ in document.tags), 2)
        self.assertTrue(any(tag == 'summary' for tag, _ in document.tags))
        self.assertTrue(any(tag == 'div' and attrs.get('tabindex') == '0' and attrs.get('aria-label') for tag, attrs in document.tags))
        self.assertTrue(all(attrs.get('href', '').startswith('#') for tag, attrs in document.tags if tag == 'a'))
        self.assertFalse(any(key in attrs for _, attrs in document.tags for key in ('src', 'srcset')))

    def test_inconsistent_counts_rates_and_denominator_are_rejected(self):
        mutations = [lambda value: value['coverage'].update(present=1),
                     lambda value: value['coverage'].update(denominator=1),
                     lambda value: value['coverage'].update(known=0),
                     lambda value: value['coverage'].update(rate=0.5),
                     lambda value: value['coverage'].update(rate=True),
                     lambda value: value['coverage'].update(complete=False),
                     lambda value: value['inventory'].update(eligible=0),
                     lambda value: value['inventory'].update(valid=0),
                     lambda value: value['inventory'].update(entries=99)]
        for mutate in mutations:
            value = report()
            mutate(value)
            with self.assertRaises(RenderError):
                render(value)
        value = report([run(1, ('present', 'unknown'))])
        value['coverage']['rate'] = 1.0
        with self.assertRaises(RenderError):
            render(value)

    def test_invalid_operation_duplicate_run_and_unrecognized_status_do_not_render(self):
        values = [report(), report(), report(), report()]
        values[0]['runs'][0]['operation'] = '<script>private</script>'
        values[1]['runs'].append(copy.deepcopy(values[1]['runs'][0]))
        values[2]['runs'][0]['edges'][0]['status'] = 'healthy'
        values[3]['runs'][0]['run_token'] = '<img src=x>'
        for value in values:
            with self.assertRaises(RenderError):
                render(value)


if __name__ == '__main__':
    unittest.main()
