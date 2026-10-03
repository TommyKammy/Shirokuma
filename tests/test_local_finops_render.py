from copy import deepcopy
from html import escape
from html.parser import HTMLParser
import io
import json
import unittest
from unittest.mock import patch

from tools.local_experiment.finops import contract as C
from tools.local_experiment.finops import render as R
from test_local_finops_contract import NOW, component_args, row


def report():
    return C.build_report([C.project_component(name, **component_args(name)) for name in C.COMPONENTS],
                          [C.disk(name, 1000, 300, observed_at=NOW) for name in C.DISKS],
                          C.inference([row()], end=NOW), observed_at=NOW)


def encoded(value): return json.dumps(value, ensure_ascii=False, allow_nan=False).encode()


class Elements(HTMLParser):
    def __init__(self):
        super().__init__(); self.elements = []; self.data = []

    def handle_starttag(self, tag, attrs): self.elements.append((tag, dict(attrs)))

    def handle_data(self, value): self.data.append(value)


class RenderTests(unittest.TestCase):
    def test_offline_html_declares_no_external_resources_or_script(self):
        html = R.render(report())
        self.assertIn('Content-Security-Policy', html)
        self.assertIn("default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'", html)

    def test_complete_report_is_saved_snapshot_with_limited_coverage_not_live_or_wp_complete(self):
        value = report(); before = deepcopy(value)
        html = R.render(value)
        self.assertEqual(value, before)
        for text in ('保存済みスナップショット', '再取得しません', '全取得項目を観測',
                     'FinOps全体の対応完了を示しません', 'PVC別', 'synthetic記録',
                     '実モデル呼出し数とは限りません', '通貨は未記録', '推論latencyとは限りません'):
            self.assertIn(text, html)
        self.assertIn('2026-09-20 00:00:21 UTC', html)
        for label in ('サービス使用量', 'モデルの記録', '入力トークン', '出力トークン', '物理ストレージ', 'ファイルシステム'):
            self.assertIn(label, html)
        self.assertEqual(html.count('<th scope="row">Trino</th>'), 1)
        self.assertEqual(html.count('<th scope="row">Polaris</th>'), 1)
        self.assertEqual(html.count('<th scope="row">SeaweedFS</th>'), 1)

    def test_zero_measurements_are_visible_and_unknown_is_not_zero(self):
        args = component_args(); args['metrics']['containers'][0]['usage'] = {'cpu':'0','memory':'0'}
        value = report(); value['components'][0] = C.project_component('trino', **args)
        html = R.render(value)
        self.assertIn('>0 cores</span>', html); self.assertIn('>0 MiB</span>', html)
        value['components'][0] = C.unknown_component('trino', 'TARGET_AMBIGUOUS')
        value = C.build_report(value['components'], value['disks'], value['inference'], observed_at=NOW)
        html = R.render(value)
        trino = html.split('<th scope="row">Trino</th>')[1].split('</tr>')[0]
        self.assertNotIn('>0 ', trino); self.assertIn('対象が複数あり特定できず', trino)
        self.assertIn('一部の取得項目が未取得', html)

    def test_disk_exact_thresholds_keep_advice_and_do_not_sum_host_vm(self):
        for percent, label in ((15,'要対応 · 15%以下'), (20,'残量低下 · 20%以下'), (25,'注意 · 25%以下')):
            value = report(); value['disks'][0] = C.disk('host', 100, percent, observed_at=NOW)
            value = C.build_report(value['components'], value['disks'], value['inference'], observed_at=NOW)
            with self.subTest(percent=percent):
                html = R.render(value)
                self.assertIn(label, html); self.assertIn('新しい大容量の取得・実験を控えて', html)
                self.assertIn('2行は合算せず', html)
        value['disks'][0] = C.disk('host', 10**18, 250000000000000001, observed_at=NOW)
        value = C.build_report(value['components'], value['disks'], value['inference'], observed_at=NOW)
        disk_html = R._disks(value)
        self.assertIn('≈ 25 %', disk_html); self.assertIn('25%超', disk_html)
        self.assertNotIn('注意 ·', disk_html)

    def test_unit_conversions_use_decimal_without_float_loss_and_mark_rounding(self):
        self.assertIn('>0.123456789 cores</span>', R._scaled('123456789', 10**9, 'cores', 9))
        self.assertIn('>2,048 MiB</span>', R._scaled('2147483648', 2**20, 'MiB'))
        self.assertIn('≈ 0.123457 cores', R._scaled('123456789', 10**9, 'cores', 6))
        self.assertEqual(R._number('340282366920938463463374607431768211455'),
                         '340,282,366,920,938,463,463,374,607,431,768,211,455')

    def test_unknown_cost_keeps_known_subtotal_and_coverage_not_currency_or_total(self):
        html = R.render(report())
        self.assertIn('全体不明', html); self.assertIn('既知小計 0.5', html)
        self.assertIn('既知 1件 / 未記録 1件', html)
        self.assertIn('費用見積り（通貨未記録）', html)
        self.assertNotIn('$0.5', html); self.assertNotIn('¥0.5', html)

    def test_all_unknown_recorded_metrics_are_not_collection_failure(self):
        value = report(); value['inference'] = C.inference([row(known='0')], end=NOW)
        html = R.render(value)
        self.assertIn('全取得項目を観測', html); self.assertIn('既知小計 不明', html)
        self.assertIn('既知 0件 / 未記録 2件', html)

    def test_empty_event_window_and_unavailable_collection_are_distinct(self):
        value = report(); value['inference'] = C.inference([], end=NOW)
        self.assertIn('モデルの記録は0件', R.render(value))
        value['inference'] = C.inference(None, end=NOW, reason='READ_TIMEOUT')
        value['disks'][1] = C.disk('colima', observed_at=NOW, reason='READ_FAILED')
        value = C.build_report(value['components'], value['disks'], value['inference'], observed_at=NOW)
        html = R.render(value)
        self.assertIn('宣言eventの集計は未取得', html); self.assertIn('取得が時間切れ', html)
        self.assertNotIn('モデルの記録は0件', html)

    def test_timestamp_display_trims_only_fraction_zeros_and_preserves_original(self):
        for stamp, visible in (('2026-09-20T00:00:21.723000000Z', '2026-09-20 00:00:21.723 UTC'),
                               ('2026-09-20T00:00:21.000000000Z', '2026-09-20 00:00:21 UTC'),
                               ('2026-09-20T00:00:21.000000001Z', '2026-09-20 00:00:21.000000001 UTC')):
            value = report(); value['observed_at'] = stamp
            html = R.render(value)
            self.assertIn(f'datetime="{stamp}">{visible}</time>', html)
            self.assertIn(escape('"observed_at": "'+stamp+'"', quote=True), html)

    def test_model_text_and_saved_json_are_escaped_and_full_long_name_remains_copyable(self):
        raw = row(); name = '<script>alert("x")</script>&' + 'very-long-model-'*12
        raw['model_name'] = name; raw['model_provider'] = 'provider" onclick="evil'
        value = report(); value['inference'] = C.inference([raw], end=NOW)
        html = R.render(value); parsed = Elements(); parsed.feed(html)
        self.assertIn(escape(name, quote=True), html)
        self.assertNotIn('<script>', html)
        self.assertTrue(any(name in text for text in parsed.data))
        self.assertFalse(any(key.startswith('on') for _, attrs in parsed.elements for key in attrs))

    def test_semantic_navigation_tables_details_and_no_external_assets_or_scripts(self):
        html = R.render(report()); parsed = Elements(); parsed.feed(html)
        tags = [tag for tag, _ in parsed.elements]
        self.assertIn(('html', {'lang':'ja'}), parsed.elements)
        for tag in ('nav', 'main', 'caption', 'thead', 'tbody', 'details', 'summary', 'time'): self.assertIn(tag, tags)
        for tag in ('script', 'iframe', 'img', 'link', 'form'): self.assertNotIn(tag, tags)
        self.assertTrue(all(attrs['href'].startswith('#') for _, attrs in parsed.elements if 'href' in attrs))
        self.assertIn('@media(max-width:640px)', html); self.assertIn('overflow-wrap:anywhere', html)
        self.assertIn(':focus-visible', html)

    def test_public_inconsistency_and_private_extra_fields_are_rejected_before_html(self):
        for mutate in (lambda v:v.update(result='partial'), lambda v:v.update(secret='PRIVATE'),
                       lambda v:v['disks'][0].update(severity='critical'),
                       lambda v:v['components'][0]['sample'].update(cpu_nanocores=True)):
            value = report(); mutate(value)
            with self.subTest(mutation=mutate), self.assertRaisesRegex(R.RenderError, R.ERROR): R.render(value)

    def test_strict_json_rejects_duplicates_nan_invalid_utf8_and_size(self):
        raw = encoded(report()); self.assertEqual(R.parse_report(raw), report())
        for invalid in (b'', b'\xff', b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e9999}',
                        b' '*R.MAX_INPUT_BYTES+b' ', raw+b' trailing', b'[]'):
            with self.subTest(raw=invalid[:30]), self.assertRaises(R.RenderError): R.parse_report(invalid)

    def test_nonzero_underflow_is_not_displayed_as_zero(self):
        raw = encoded(report())
        self.assertIn(b'0.5', raw)
        with self.assertRaises(R.RenderError):
            R.parse_report(raw.replace(b'0.5', b'1e-999'))
        self.assertEqual(R.parse_report(raw.replace(b'0.5', b'0e-999'))['schema_version'], 1)

    def test_cli_writes_html_only_for_valid_input_and_safe_error_otherwise(self):
        for raw, expected in ((encoded(report()), 0), (b'{"private":"SECRET"}', 1)):
            stdin = io.TextIOWrapper(io.BytesIO(raw), encoding='utf-8')
            with patch('sys.stdin', stdin), patch('sys.stdout', new_callable=io.StringIO) as out, \
                    patch('sys.stderr', new_callable=io.StringIO) as err:
                self.assertEqual(R.main([]), expected)
            if expected == 0:
                self.assertTrue(out.getvalue().startswith('<!doctype html>')); self.assertEqual(err.getvalue(), '')
            else:
                self.assertEqual(out.getvalue(), ''); self.assertEqual(err.getvalue(), R.ERROR+'\n')

    def test_first_cancellation_is_not_transformed_into_report_error(self):
        error = KeyboardInterrupt()
        with patch.object(R.C, 'validate_public', side_effect=error), self.assertRaises(KeyboardInterrupt) as got:
            R.render(report())
        self.assertIs(got.exception, error)


if __name__ == '__main__': unittest.main()
