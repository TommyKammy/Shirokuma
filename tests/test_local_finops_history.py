import json
import io
import unittest
from unittest.mock import patch

from tools.local_experiment.finops import history as H
from tools.local_experiment.finops import contract as C
from test_local_finops_contract import row
from test_local_finops_process_report import processes, report


def encoded(values):
    return json.dumps(values, ensure_ascii=False, allow_nan=False).encode()


class HistoryTests(unittest.TestCase):
    def test_mixed_v1_v2_preserves_scope_and_null_stopped_usage(self):
        local = processes()
        local['components'][0].update(state='stopped', reason='STOPPED', identity=None, sample=None)
        newer = report(local)
        older = report()
        html = H.render(H.parse(encoded([older, newer])))
        self.assertEqual(html.count('このschemaには含まれません'), 1)
        self.assertIn('停止中（数値なし）', html)
        self.assertNotIn('0 cores</td>', html.split('停止中（数値なし）')[0])
        self.assertIn('重複windowの件数を合算・差し引きしません', html)
        self.assertIn('通貨未記録', html)
        self.assertEqual(html.count('id="observation-2-local-processes"'), 1)
        self.assertNotIn('id="local-processes"', html)
        self.assertEqual(html, H.render(H.parse(encoded([older, newer]))))

    def test_order_by_observation_time_and_equal_time_preserves_selection_order(self):
        first = report(); second = report(processes())
        later_time = '2026-09-20T00:00:22.000Z'
        later = C.build_report(first['components'], first['disks'], C.inference([row()], end=later_time), observed_at=later_time)
        self.assertEqual([entry[0] for entry in H.parse(encoded([later, second]))], [1, 0])
        self.assertEqual([entry[0] for entry in H.parse(encoded([first, second]))], [0, 1])

    def test_escape_model_names_and_never_collect(self):
        first = report()
        # Rebuild inference/public report with its validated public inputs rather than forge the report.
        from test_local_finops_contract import NOW, row
        base = row(); base['model_name'] = '<script>alert(1)</script>'
        first['inference'] = C.inference([base], end=NOW)
        first = C.build_report(first['components'], first['disks'], first['inference'], observed_at=NOW)
        with patch('tools.local_experiment.finops.run.collect', side_effect=AssertionError('collection')):
            html = H.render(H.parse(encoded([first, report(processes())])))
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>', html)
        self.assertNotIn('<form', html)

    def test_reject_invalid_duplicates_missing_and_input_limits_without_partial_output(self):
        good = encoded([report(), report(processes())])
        invalid = [b'[]', b'[{}]', good.replace(b'"schema_version": 1', b'"schema_version": 3', 1),
                   good.replace(b'"schema_version": 1', b'"schema_version": 1, "schema_version": 1', 1),
                   good.replace(b'"schema_version": 1', b'"schema_version": NaN', 1),
                   b'[' + b'0,' * 10 + b'0]', b'x'*(H.MAX_INPUT_BYTES+1)]
        for payload in invalid:
            with self.subTest(payload=payload[:40]), self.assertRaises(H.HistoryError): H.parse(payload)
        too_many = encoded([report()] * 9)
        with self.assertRaises(H.HistoryError): H.parse(too_many)

    def test_cli_failure_has_no_partial_html_or_private_error(self):
        class Input:
            buffer = io.BytesIO(b'[{"secret":"PRIVATE_TOKEN"},{}]')
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(H.sys, 'stdin', Input()), patch.object(H.sys, 'stdout', output), patch.object(H.sys, 'stderr', errors):
            self.assertEqual(H.main([]), 1)
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(errors.getvalue().strip(), H.ERROR)


if __name__ == '__main__': unittest.main()
