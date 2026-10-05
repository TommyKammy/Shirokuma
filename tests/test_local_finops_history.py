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
    def test_saved_history_refuses_escaped_nested_duplicate_keys(self):
        first, second = report(), report(processes())
        first_wire = json.dumps(first, ensure_ascii=True)
        self.assertIn('"model_name":', first_wire)
        first_wire = first_wire.replace('"model_name":', '"model\\u005fname": "ambiguous", "model_name":', 1)
        with patch.object(H, 'render', side_effect=AssertionError('must not render')):
            with self.assertRaises(H.HistoryError):
                H.parse(('[' + first_wire + ',' + json.dumps(second) + ']').encode())
        self.assertIn('保存FinOps観測履歴', H.render(H.parse(encoded([first, second]))))

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

    def test_direct_renderer_refuses_valid_report_changed_after_selection(self):
        entries = H.parse(encoded([report(), report(processes())]))
        index, (original, digest) = entries[0]
        changed = json.loads(json.dumps(original))
        changed['inference']['groups'][0]['model_name'] = 'different-saved-model'
        C.validate_public(changed)
        with self.assertRaises(H.HistoryError):
            H.render([(index, (changed, digest)), entries[1]])
        self.assertIn('保存FinOps観測履歴', H.render(entries))

    def test_direct_renderer_displays_pinned_report_not_later_caller_mutation(self):
        entries = H.parse(encoded([report(), report(processes())]))
        original_time = H.H._time
        changed = False

        def mutate_after_validation(value):
            nonlocal changed
            if not changed:
                changed = True
                entries[0][1][0]['inference']['groups'][0]['model_name'] = 'mutated-after-digest'
            return original_time(value)

        with patch.object(H.H, '_time', side_effect=mutate_after_validation):
            html = H.render(entries)
        self.assertTrue(changed)
        self.assertNotIn('mutated-after-digest', html)

    def test_direct_renderer_checks_one_snapshot_during_validation(self):
        entries = H.parse(encoded([report(), report(processes())]))
        original_validate = C.validate_public
        changed = False

        def change_caller_after_validation(value):
            nonlocal changed
            result = original_validate(value)
            if not changed:
                self.assertIsNot(value, entries[0][1][0])
                changed = True
                entries[0][1][0]['inference']['groups'][0]['model_name'] = 'changed-during-validation'
            return result

        with patch.object(C, 'validate_public', side_effect=change_caller_after_validation):
            html = H.render(entries)
        self.assertTrue(changed)
        self.assertNotIn('changed-during-validation', html)

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

    def test_direct_renderer_preserves_input_order_for_equal_observation_times(self):
        entries = H.parse(encoded([report(), report(processes())]))
        self.assertEqual([index for index, _ in entries], [0, 1])
        self.assertIn('保存FinOps観測履歴', H.render(entries))
        # Equal timestamps must not be used to reorder the original selection.
        with self.assertRaises(H.HistoryError):
            H.render(list(reversed(entries)))

    def test_direct_renderer_refuses_boolean_and_float_selection_aliases(self):
        entries = H.parse(encoded([report(), report(processes())]))
        for index, alias in ((0, False), (0, 0.0), (1, True), (1, 1.0)):
            changed = list(entries)
            changed[index] = (alias, entries[index][1])
            # Both compare equal to integer zero, including in the preliminary
            # set comparison; the explicit index type must still reject them.
            with self.subTest(alias=alias), self.assertRaises(H.HistoryError):
                H.render(changed)
        self.assertIn('保存FinOps観測履歴', H.render(entries))

    def test_direct_renderer_refuses_unhashable_selection_index(self):
        entries = H.parse(encoded([report(), report(processes())]))
        for index in ([], {}, [0]):
            changed = list(entries)
            changed[0] = (index, entries[0][1])
            with self.subTest(index=index), self.assertRaises(H.HistoryError):
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

    def test_reordered_json_members_do_not_make_duplicate_observations(self):
        first = report()
        # The wire bytes differ while the report and its canonical digest do not.
        reordered = dict(reversed(list(first.items())))
        self.assertNotEqual(encoded(first), encoded(reordered))
        with self.assertRaises(H.HistoryError):
            H.parse(encoded([first, reordered]))
        self.assertIn('保存FinOps観測履歴',
                      H.render(H.parse(encoded([first, report(processes())]))))

    def test_unicode_escape_does_not_make_duplicate_saved_observation(self):
        first = report()
        first['inference']['groups'][0]['model_name'] = 'café'
        # The two wire forms differ, but JSON decoding and canonical digesting
        # must treat escaped and literal Unicode as the same saved report.
        literal = encoded(first)
        escaped = json.dumps(first, ensure_ascii=True).encode()
        self.assertNotEqual(literal, escaped)
        with self.assertRaises(H.HistoryError):
            H.parse(b'[' + literal + b',' + escaped + b']')
        self.assertIn('保存FinOps観測履歴',
                      H.render(H.parse(encoded([first, report(processes())]))))

    def test_equivalent_float_lexemes_do_not_make_duplicate_saved_observation(self):
        first = encoded(report())
        self.assertIn(b'0.5', first)
        alternate = first.replace(b'0.5', b'5e-1', 1)
        self.assertNotEqual(first, alternate)
        with self.assertRaises(H.HistoryError):
            H.parse(b'[' + first + b',' + alternate + b']')
        self.assertIn('保存FinOps観測履歴',
                      H.render(H.parse(encoded([report(), report(processes())]))))

    def test_negative_zero_integer_spelling_is_not_an_independent_observation(self):
        first = encoded(report())
        self.assertIn(b'"restart_count": 0,', first)
        alternate = first.replace(b'"restart_count": 0,', b'"restart_count": -0,', 1)
        self.assertNotEqual(first, alternate)
        with patch('tools.local_experiment.finops.run.collect', side_effect=AssertionError('collection')):
            with self.assertRaises(H.HistoryError):
                H.parse(b'[' + first + b',' + alternate + b']')
            self.assertIn('保存FinOps観測履歴',
                          H.render(H.parse(encoded([report(), report(processes())]))))

    def test_signed_float_zero_is_not_an_independent_observation(self):
        first = encoded(report())
        self.assertIn(b'0.5', first)
        positive = first.replace(b'0.5', b'0.0', 1)
        negative = first.replace(b'0.5', b'-0.0', 1)
        self.assertNotEqual(positive, negative)
        with patch('tools.local_experiment.finops.run.collect', side_effect=AssertionError('collection')):
            with self.assertRaises(H.HistoryError):
                H.parse(b'[' + positive + b',' + negative + b']')
            self.assertIn('保存FinOps観測履歴',
                           H.render(H.parse(encoded([report(), report(processes())]))))

    def test_direct_renderer_cannot_relabel_signed_zero_as_distinct_digest(self):
        first = encoded(report())
        positive = json.loads(first.replace(b'0.5', b'0.0', 1))
        negative = json.loads(first.replace(b'0.5', b'-0.0', 1))
        C.validate_public(positive)
        C.validate_public(negative)
        from hashlib import sha256
        digest = sha256(H._canonical_bytes(positive)).hexdigest()
        self.assertEqual(digest, sha256(H._canonical_bytes(negative)).hexdigest())
        with patch('tools.local_experiment.finops.run.collect', side_effect=AssertionError('collection')):
            with self.assertRaises(H.HistoryError):
                H.render([(0, (positive, digest)), (1, (negative, digest))])
            self.assertIn('保存FinOps観測履歴', H.render(H.parse(encoded([report(), report(processes())]))))

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

    def test_utf8_input_limit_is_bytes_not_characters(self):
        valid = encoded([report(), report(processes())])
        self.assertEqual(len(H.parse(valid)), 2)
        # A valid Unicode scalar can occupy four bytes; the parser must bound
        # the wire input before decoding or rendering even when characters fit.
        oversized = valid + ('😀' * ((H.MAX_INPUT_BYTES - len(valid)) // 4 + 1)).encode()
        self.assertGreater(len(oversized), H.MAX_INPUT_BYTES)
        self.assertLess(len(oversized.decode('utf-8')), H.MAX_INPUT_BYTES)
        with patch('tools.local_experiment.finops.run.collect', side_effect=AssertionError('collection')):
            with self.assertRaises(H.HistoryError):
                H.parse(oversized)

    def test_malformed_saved_utf8_cannot_be_rendered_or_trigger_collection(self):
        valid = encoded([report(), report(processes())])
        # Both a stray continuation byte and an incomplete multibyte scalar
        # must be rejected before any saved report is displayed.
        for payload in (valid[:-1] + b'\x80' + valid[-1:],
                        valid[:-1] + b'\xe2\x82' + valid[-1:]):
            with self.subTest(payload=payload[-6:]), patch(
                    'tools.local_experiment.finops.run.collect',
                    side_effect=AssertionError('collection')):
                with self.assertRaises(H.HistoryError):
                    H.parse(payload)
        self.assertEqual(len(H.parse(valid)), 2)

    def test_overlong_saved_utf8_is_not_interpreted_as_report_text(self):
        valid = encoded([report(), report(processes())])
        # C0 AF is a noncanonical overlong spelling of '/'. It must not be
        # normalized into a saved model name or rendered as a valid report.
        self.assertIn(b'"model_name": "', valid)
        forged = valid.replace(b'"model_name": "', b'"model_name": "\xc0\xaf', 1)
        with patch('tools.local_experiment.finops.run.collect', side_effect=AssertionError('collection')):
            with self.assertRaises(H.HistoryError):
                H.parse(forged)
            self.assertEqual(len(H.parse(valid)), 2)

    def test_saved_history_rejects_utf8_bom_without_collection(self):
        valid = encoded([report(), report(processes())])
        with patch('tools.local_experiment.finops.run.collect', side_effect=AssertionError('collection')):
            with self.assertRaises(H.HistoryError):
                H.parse(b'\xef\xbb\xbf' + valid)
            self.assertEqual(len(H.parse(valid)), 2)

    def test_escaped_lone_surrogate_in_saved_model_is_refused(self):
        valid = encoded([report(), report(processes())])
        self.assertIn(b'"model_name": "', valid)
        for escaped in (b'\\ud800', b'\\udc00'):
            forged = valid.replace(b'"model_name": "',
                                   b'"model_name": "' + escaped, 1)
            with self.subTest(escaped=escaped), self.assertRaises(H.HistoryError):
                H.parse(forged)
        pair = valid.replace(b'"model_name": "',
                             b'"model_name": "\\ud83d\\ude00', 1)
        self.assertEqual(len(H.parse(pair)), 2)

    def test_direct_render_output_cap_counts_utf8_and_returns_no_partial_html(self):
        entries = H.parse(encoded([report(), report(processes())]))
        with patch('tools.local_experiment.finops.run.collect', side_effect=AssertionError('collection')):
            html = H.render(entries)
            size = len(html.encode('utf-8'))
            self.assertGreater(size, len(html))
            with patch.object(H, 'MAX_OUTPUT_BYTES', size):
                self.assertEqual(H.render(entries), html)
            # A character-count bound would incorrectly admit this output.
            with patch.object(H, 'MAX_OUTPUT_BYTES', size - 1):
                with self.assertRaises(H.HistoryError):
                    H.render(entries)

    def test_distinct_saved_reports_reach_exact_selection_limit_without_collection(self):
        base = report()
        samples = [C.build_report(base['components'], base['disks'], base['inference'],
                   observed_at=f'2026-09-20T00:00:{second:02d}.000Z')
                   for second in range(H.MAX_REPORTS + 1)]
        with patch('tools.local_experiment.finops.run.collect', side_effect=AssertionError('collection')):
            entries = H.parse(encoded(samples[:H.MAX_REPORTS]))
            html = H.render(entries)
            self.assertEqual([index for index, _ in entries], list(range(H.MAX_REPORTS)))
            self.assertEqual(html.count('<section id="observation-'), H.MAX_REPORTS)
            self.assertLessEqual(len(html.encode('utf-8')), H.MAX_OUTPUT_BYTES)
            with self.assertRaises(H.HistoryError):
                H.parse(encoded(samples))

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

    def test_cli_output_limit_includes_terminal_newline(self):
        data = encoded([report(), report(processes())])
        html = H.render(H.parse(data))
        size = len(html.encode('utf-8'))
        class Input:
            buffer = io.BytesIO(data)
        for limit, expected in ((size, 1), (size + 1, 0)):
            output, errors = io.StringIO(), io.StringIO()
            Input.buffer = io.BytesIO(data)
            with self.subTest(limit=limit), patch.object(H, 'MAX_OUTPUT_BYTES', limit), \
                    patch.object(H.sys, 'stdin', Input()), patch.object(H.sys, 'stdout', output), \
                    patch.object(H.sys, 'stderr', errors), \
                    patch('tools.local_experiment.finops.run.collect', side_effect=AssertionError('collection')):
                self.assertEqual(H.main([]), expected)
            if expected:
                self.assertEqual(output.getvalue(), '')
                self.assertEqual(errors.getvalue().strip(), H.ERROR)
            else:
                self.assertEqual(output.getvalue(), html + '\n')


if __name__ == '__main__': unittest.main()
