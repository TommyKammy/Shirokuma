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
        self.assertEqual(html.count('このschemaには含まれません（停止'), 1)
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

    def test_direct_renderer_rejects_unbound_digest_without_html(self):
        entries = H.parse(encoded([report(), report(processes())]))
        for digest in ('<img src=x onerror=alert(1)>', '0' * 64):
            changed = list(entries)
            index, (value, _) = changed[0]
            changed[0] = (index, (value, digest))
            with self.subTest(digest=digest), self.assertRaises(H.HistoryError):
                H.render(changed)
        self.assertIn('保存FinOps観測履歴', H.render(entries))

    def test_direct_renderer_bounds_malformed_selection_shape(self):
        entries = H.parse(encoded([report(), report(processes())]))
        for malformed in ((), (0,), (0, ()), (0, (entries[0][1][0],)),
                          {'index': 0}, ([], entries[0][1]), ({'index': 0}, entries[0][1])):
            with self.subTest(malformed=str(malformed)[:40]), self.assertRaises(H.HistoryError):
                H.render([malformed, entries[1]])
        self.assertIn('保存FinOps観測履歴', H.render(entries))

    def test_direct_renderer_bounds_malformed_report_fields(self):
        entries = H.parse(encoded([report(), report(processes())]))
        for change in (lambda value: value.pop('components'),
                       lambda value: value['components'][0].pop('component'),
                       lambda value: value.update(disks=[{}])):
            changed = json.loads(json.dumps(entries[0][1][0]))
            change(changed)
            with self.assertRaises(H.HistoryError):
                H.render([(entries[0][0], (changed, entries[0][1][1])), entries[1]])
        self.assertIn('保存FinOps観測履歴', H.render(entries))

    def test_direct_renderer_bounds_cyclic_report_without_partial_html(self):
        entries = H.parse(encoded([report(), report(processes())]))
        changed = json.loads(json.dumps(entries[0][1][0]))
        changed['limits'].append(changed)
        with self.assertRaises(H.HistoryError):
            H.render([(entries[0][0], (changed, entries[0][1][1])), entries[1]])
        self.assertIn('保存FinOps観測履歴', H.render(entries))

    def test_direct_renderer_bounds_deep_untrusted_extension(self):
        entries = H.parse(encoded([report(), report(processes())]))
        changed = json.loads(json.dumps(entries[0][1][0]))
        extension = 0
        for _ in range(1100):
            extension = [extension]
        changed['extension'] = extension
        with self.assertRaises(H.HistoryError):
            H.render([(entries[0][0], (changed, entries[0][1][1])), entries[1]])
        self.assertIn('保存FinOps観測履歴', H.render(entries))

    def test_direct_renderer_requires_complete_ordered_selection(self):
        first = report(); later_time = '2026-09-20T00:00:22.000Z'
        later = C.build_report(first['components'], first['disks'],
                               C.inference([row()], end=later_time), observed_at=later_time)
        entries = H.parse(encoded([later, first]))
        self.assertEqual([item[0] for item in entries], [1, 0])
        self.assertIn('保存FinOps観測履歴', H.render(entries))
        for invalid in ([entries[0], entries[0]], list(reversed(entries)),
                         [(2, entries[0][1]), entries[1]]):
            with self.subTest(indices=[item[0] for item in invalid]), self.assertRaises(H.HistoryError):
                H.render(invalid)

    def test_direct_renderer_refuses_boolean_and_float_selection_aliases(self):
        entries = H.parse(encoded([report(), report(processes())]))
        for alias in (False, 0.0):
            changed = [(alias, entries[0][1]), entries[1]]
            # Both compare equal to integer zero, including in the preliminary
            # set comparison; the explicit index type must still reject them.
            with self.subTest(alias=alias), self.assertRaises(H.HistoryError):
                H.render(changed)
        self.assertIn('保存FinOps観測履歴', H.render(entries))

    def test_repeated_saved_report_is_not_a_second_observation(self):
        first = report()
        payload = encoded([first, first])
        with self.assertRaises(H.HistoryError):
            H.parse(payload)
        from hashlib import sha256
        canonical = json.dumps(first, ensure_ascii=True, sort_keys=True,
                               separators=(',', ':'), allow_nan=False).encode()
        digest = sha256(canonical).hexdigest()
        with self.assertRaises(H.HistoryError):
            H.render([(0, (first, digest)), (1, (first, digest))])
        self.assertIn('保存FinOps観測履歴', H.render(H.parse(encoded([first, report(processes())]))))

    def test_offline_html_declares_no_external_resources_or_script(self):
        html = H.render(H.parse(encoded([report(), report(processes())])))
        self.assertIn('Content-Security-Policy', html)
        self.assertIn("default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'", html)

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

    def test_nested_duplicate_report_claims_cannot_override_saved_observation(self):
        original = encoded([report(), report(processes())])
        self.assertIn(b'"inference": {', original)
        # A last-key-wins decoder would accept the second claim as if the
        # first one had never been reported.
        forged = original.replace(b'"inference": {', b'"inference": {}, "inference": {', 1)
        with self.assertRaises(H.HistoryError):
            H.parse(forged)
        self.assertEqual(len(H.parse(original)), 2)

    def test_escaped_duplicate_key_cannot_override_saved_observation(self):
        original = encoded([report(), report(processes())])
        self.assertIn(b'"inference": {', original)
        # Raw bytes differ; decoded JSON member names are identical. Reject
        # before selecting or digesting either saved report.
        forged = original.replace(b'"inference": {', b'"infer\\u0065nce": {}, "inference": {', 1)
        with self.assertRaises(H.HistoryError):
            H.parse(forged)
        self.assertEqual(len(H.parse(original)), 2)

    def test_nonzero_float_underflow_cannot_become_saved_zero_usage(self):
        original = encoded([report(), report(processes())])
        self.assertIn(b'0.5', original)
        underflow = original.replace(b'0.5', b'1e-999')
        with self.assertRaises(H.HistoryError):
            H.parse(underflow)
        self.assertEqual(len(H.parse(original)), 2)
        self.assertEqual(len(H.parse(original.replace(b'0.5', b'0e-999'))), 2)

    def test_smallest_representable_saved_measurement_is_not_underflow(self):
        original = encoded([report(), report(processes())])
        self.assertIn(b'0.5', original)
        # The transport guard must distinguish a nonzero subnormal from a
        # nonzero lexeme that rounds all the way to zero.
        self.assertEqual(len(H.parse(original.replace(b'0.5', b'5e-324', 1))), 2)
        with self.assertRaises(H.HistoryError):
            H.parse(original.replace(b'0.5', b'2e-324', 1))

    def test_deep_saved_extension_is_bounded_without_partial_html(self):
        original = encoded([report(), report(processes())])
        forged = original.replace(b'"inference": {',
                                  b'"untrusted_extension": ' + b'[' * 1100 + b'0' + b']' * 1100 +
                                  b', "inference": {', 1)
        self.assertNotEqual(original, forged)
        with self.assertRaises(H.HistoryError):
            H.parse(forged)
        self.assertIn('保存FinOps観測履歴', H.render(H.parse(original)))

    def test_overflowing_float_cannot_become_saved_infinite_measurement(self):
        original = encoded([report(), report(processes())])
        self.assertIn(b'0.5', original)
        for exponent in (b'1e999', b'-1e999'):
            with self.subTest(exponent=exponent), self.assertRaises(H.HistoryError):
                H.parse(original.replace(b'0.5', exponent, 1))
        self.assertEqual(len(H.parse(original)), 2)

    def test_cli_failure_has_no_partial_html_or_private_error(self):
        class Input:
            buffer = io.BytesIO(b'[{"secret":"PRIVATE_TOKEN"},{}]')
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(H.sys, 'stdin', Input()), patch.object(H.sys, 'stdout', output), patch.object(H.sys, 'stderr', errors):
            self.assertEqual(H.main([]), 1)
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(errors.getvalue().strip(), H.ERROR)


if __name__ == '__main__': unittest.main()
