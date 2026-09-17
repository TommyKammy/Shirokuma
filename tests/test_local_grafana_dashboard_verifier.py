"""Offline regression coverage for anonymous panel/data verification."""
import copy
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('grafana_verifier_test', ROOT / 'tools/local_experiment/grafana/verify_dashboard.py')
V = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V)


def table(columns, types, rows):
    return {'meta': [{'name': name, 'type': typ} for name, typ in zip(columns, types)],
            'data': [dict(zip(columns, row)) for row in rows], 'rows': len(rows)}


def frame(value):
    canonical = V.reference_table(value, 1000)
    fields = [{'name': name, 'type': 'number' if typ in ('integer', 'float') else typ}
              for name, (typ, _) in zip(canonical['columns'], canonical['types'])]
    return {'results': {'A': {'status': 200, 'frames': [{'schema': {'refId': 'A', 'fields': fields},
            'data': {'values': [[row[i] for row in canonical['rows']] for i in range(len(fields))]}}]}}}


def usage():
    return table(['model_provider', 'model_name', 'inference_events', 'prompt_known_calls', 'prompt_unknown_calls',
                  'known_prompt_tokens', 'completion_known_calls', 'completion_unknown_calls', 'known_completion_tokens',
                  'cost_known_calls', 'cost_unknown_calls', 'known_cost_estimate'],
                 ['String', 'String'] + ['UInt64'] * 3 + ['Nullable(UInt64)'] + ['UInt64'] * 2 + ['Nullable(UInt64)']
                 + ['UInt64'] * 2 + ['Nullable(Float64)'],
                 [['local', 'qwen', '2', '1', '1', '0', '1', '1', '13', '0', '2', None]])


class FakeBackend:
    def __init__(self):
        self.dashboard = V.DASH.dashboard()
        self.targets = V.panel_targets(self.dashboard)
        self.requests, self.sql, self.last_reference = [], [], None
        self.gets = 0
        self.mutate_reply = None
        self.fail_at = None
        self.change_reference = False
        self.drop_known_rows = False
        self.ignore_negative_filter = False
        self.live_drift = False

    def reader(self, sql):
        self.sql.append(sql)
        if sql.startswith('SELECT task_id, trace_id'):
            return table(['task_id', 'trace_id'], ['String', 'String'], [['qwen-test', 'a' * 32]])
        self.assert_query(sql)
        negative = 'shirokuma-dashboard-missing-' in sql or '%27' in sql
        if 'known_prompt_tokens' in sql:
            value = usage()
        elif ' AS time,' in sql:
            value = table(['time', 'success', 'failed', 'blocked'], ["DateTime64(3, 'UTC')", 'UInt64', 'UInt64', 'UInt64'],
                          [['2026-09-17 03:00:00.000', '2', '1', '0']])
        elif 'p95_ms' in sql:
            value = table(['action_type', 'model_provider', 'model_name', 'recorded_events', 'failed_events', 'blocked_events', 'p50_ms', 'p95_ms'],
                          ['LowCardinality(String)', 'String', 'String'] + ['UInt64'] * 5,
                          [['model', 'local', 'qwen', '2', '0', '0', '10', '20']])
        else:
            value = table(['timestamp', 'status', 'duration_ms', 'blocked_by_policy', 'prompt_tokens'],
                          ["DateTime64(3, 'UTC')", 'LowCardinality(String)', 'UInt64', 'Bool', 'Nullable(UInt64)'],
                          [['2026-09-17 03:00:00.123', 'success', '1', False, None]])
        if (negative and not self.ignore_negative_filter) or self.drop_known_rows:
            value['data'], value['rows'] = [], 0
        # Return a changed value only on the confirmation read after /api/ds/query.
        if self.change_reference and self.requests and len(self.sql) == 3:
            value['data'], value['rows'] = [], 0
        self.last_reference = value
        return copy.deepcopy(value)

    @staticmethod
    def assert_query(sql):
        assert sql.startswith('SELECT ')
        assert 'FROM agenthouse.pawprints FINAL' in sql
        assert 'LIMIT ' in sql and sql.endswith('FORMAT JSON')
        assert ';' not in sql and '$__' not in sql and '${' not in sql

    def grafana(self, path, body=None):
        if body is None:
            assert path == '/api/dashboards/uid/' + V.DASH.UID
            self.gets += 1
            value = copy.deepcopy(self.dashboard)
            if self.live_drift and self.gets == 2:
                value['panels'][1]['targets'][0]['rawSql'] += ' '
            return {'dashboard': value, 'meta': {'provisioned': True}}
        assert path == '/api/ds/query'
        self.requests.append(copy.deepcopy(body))
        if self.fail_at == len(self.requests):
            raise RuntimeError('secret-password and raw SQL must never be saved')
        value = frame(self.last_reference)
        if self.mutate_reply:
            self.mutate_reply(value)
        return value


class ParserTests(unittest.TestCase):
    def test_strict_percent_encoding_and_literal_roundtrip(self):
        value = "'\t;\\/%${task:raw}$__timeFilter_ms(timestamp) 日本語\n"
        output = V.interpolate("decodeURLComponent('${task:percentencode}')|${trace:percentencode}", value, value)
        encoded = output.split("'")[1]
        self.assertEqual(urllib.parse.unquote(encoded), value)
        self.assertEqual(encoded, urllib.parse.quote(value, safe=''))
        self.assertNotIn('\t', output)
        self.assertNotIn('$__', output)
        self.assertNotIn(';', output)
        self.assertNotIn('${', output)
        for raw in V.DASH.QUERIES.values():
            sql = V.reference_sql(raw, value, value, 1000, 2000)
            self.assertIn('FROM agenthouse.pawprints FINAL', sql)
            self.assertIn('fromUnixTimestamp64Milli(1000)', sql)
            self.assertNotIn('$__', sql)
            self.assertNotIn(';', sql)

    def test_rejects_external_templates_and_ranges(self):
        for args in [(None, ''), ('x' * 257, ''), ('', False)]:
            with self.assertRaises(V.VerificationError):
                V.interpolate('${task:percentencode}', *args)
        with self.assertRaises(V.VerificationError):
            V.interpolate('${task:raw}', 'x', '')
        with self.assertRaises(V.VerificationError):
            V.reference_sql(V.DASH.QUERIES['timeline'], '', '', True, 2000)

    def test_typed_roundtrip_and_exact_millisecond_precision(self):
        raw = table(['stamp', 'status', 'count', 'maybe', 'cost', 'blocked'],
                    ["DateTime64(3, 'UTC')", 'LowCardinality(String)', 'UInt64', 'Nullable(UInt64)', 'Nullable(Float64)', 'Bool'],
                    [['2026-09-17 03:00:00.123', 'success', '0', None, None, False]])
        expected = V.reference_table(raw, 1)
        self.assertEqual(expected['rows'][0], [1789614000123, 'success', 0, None, None, False])
        self.assertEqual(V.frame_table(frame(raw), expected, 1), expected)
        self.assertEqual(V.cell('1970-01-01 00:00:00.001', 'time', False, reference=True), 1)
        with self.assertRaises(V.VerificationError):
            V.cell('2026-09-17 03:00:00.123001', 'time', False, reference=True)

    def test_rejects_bool_as_number_precision_loss_and_nan(self):
        for value in (True, float('nan'), float('inf'), 2**53, 1.5):
            with self.subTest(value=value), self.assertRaises(V.VerificationError):
                V.cell(value, 'integer', False, reference=False)
        for value in (0, 'false'):
            with self.assertRaises(V.VerificationError):
                V.cell(value, 'boolean', False, reference=False)
        with self.assertRaises(V.VerificationError):
            V.kind('Map(String,String)')

    def test_frames_reject_missing_rows_wrong_columns_types_and_errors(self):
        raw = table(['n'], ['UInt64'], [['2']])
        expected = V.reference_table(raw, 1)
        mutations = [lambda r: r['results']['A'].update(error='private error', status=500),
                     lambda r: r['results']['A'].update(frames=[]),
                     lambda r: r['results']['A']['frames'][0]['schema']['fields'][0].update(name='other'),
                     lambda r: r['results']['A']['frames'][0]['schema']['fields'][0].update(type='string'),
                     lambda r: r['results']['A']['frames'][0]['data']['values'].append([])]
        for mutate in mutations:
            with self.subTest(mutate=mutate), self.assertRaises(V.VerificationError):
                value = frame(raw)
                mutate(value)
                V.frame_table(value, expected, 1)
        with self.assertRaises(V.VerificationError):
            V.reference_table({**raw, 'rows': 0}, 1)

    def test_null_usage_is_not_zero_and_totals_are_independent(self):
        value = V.reference_table(usage(), 50)
        V.usage_invariants(value)
        for column, new_value in [('known_cost_estimate', 0.0), ('prompt_unknown_calls', 0)]:
            broken = copy.deepcopy(value)
            broken['rows'][0][broken['columns'].index(column)] = new_value
            with self.assertRaises(V.VerificationError):
                V.usage_invariants(broken)
        value['rows'][0][value['columns'].index('known_prompt_tokens')] = None
        with self.assertRaises(V.VerificationError):
            V.usage_invariants(value)

    def test_plugin_empty_schema_requires_independently_empty_result(self):
        empty = {'results': {'A': {'status': 200, 'frames': [{'schema': {'fields': []}, 'data': {'values': []}}]}}}
        reference = V.reference_table(table(['n'], ['UInt64'], []), 1)
        self.assertEqual(V.frame_table(empty, reference, 1), reference)
        with self.assertRaises(V.VerificationError):
            V.frame_table(empty, {**reference, 'rows': [[1]]}, 1)


class FlowTests(unittest.TestCase):
    def run_backend(self, backend):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        output = Path(temporary.name) / 'receipt'
        report = V.run(output, reader=backend.reader, grafana=backend.grafana, now_ms=1789615000000)
        return report, output

    def test_all_40_panels_private_receipts_and_exact_queries(self):
        backend = FakeBackend()
        report, output = self.run_backend(backend)
        self.assertEqual(report['result'], 'passed', report)
        self.assertEqual(report['passed_panel_cases'], 40)
        self.assertEqual(len(backend.requests), 40)
        self.assertEqual(len(backend.sql), 81)
        self.assertEqual(output.stat().st_mode & 0o777, 0o700)
        for path in output.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        targets = V.panel_targets(V.DASH.dashboard())
        for i, request in enumerate(backend.requests):
            self.assertEqual(set(request), {'from', 'to', 'queries'})
            target = request['queries'][0]
            original = list(targets.values())[i % 5]
            self.assertEqual({k: v for k, v in target.items() if k not in ('rawSql', 'intervalMs', 'maxDataPoints')},
                             {k: v for k, v in original.items() if k != 'rawSql'})
            self.assertEqual(target['intervalMs'], 60000)
            self.assertEqual(target['maxDataPoints'], 1000)
        negatives = [row for row in report['cases'] if row['case'].startswith(('missing', 'malicious'))]
        self.assertEqual(len(negatives), 20)
        self.assertTrue(all(row['rows'] == 0 for row in negatives))

    def test_midrun_failure_preserves_progress_pending_and_hides_raw_error(self):
        backend = FakeBackend()
        backend.fail_at = 3
        report, output = self.run_backend(backend)
        self.assertEqual(report['result'], 'failed')
        self.assertEqual(len(report['cases']), 2)
        self.assertEqual(report['pending'], {'case': 'all', 'panel': 'phase_latency'})
        raw = (output / 'summary.json').read_text()
        self.assertNotIn('secret-password', raw)
        self.assertEqual(json.loads(raw), report)

    def test_independent_reference_change_and_grafana_value_mismatch_fail(self):
        backend = FakeBackend()
        backend.change_reference = True
        report, _ = self.run_backend(backend)
        self.assertEqual(report['error_code'], 'REFERENCE_CHANGED_DURING_QUERY')
        backend = FakeBackend()
        backend.mutate_reply = lambda r: r['results']['A']['frames'][0]['data']['values'][2].__setitem__(0, 999)
        report, _ = self.run_backend(backend)
        self.assertEqual(report['error_code'], 'GRAFANA_FRAME_DATA_DIFFERS')

    def test_nonempty_known_filters_and_negative_filters_are_required(self):
        backend = FakeBackend()
        backend.drop_known_rows = True
        report, _ = self.run_backend(backend)
        self.assertEqual(report['error_code'], 'KNOWN_FILTER_LOST_ROWS')
        backend = FakeBackend()
        backend.ignore_negative_filter = True
        report, _ = self.run_backend(backend)
        self.assertEqual(report['error_code'], 'UNMATCHED_FILTER_RETURNED_ROWS')

    def test_live_and_local_input_drift_fail(self):
        backend = FakeBackend()
        backend.live_drift = True
        report, _ = self.run_backend(backend)
        self.assertEqual(report['error_code'], 'LIVE_INPUTS_CHANGED_DURING_VERIFICATION')
        original = V.load_inputs()
        changed = (original[0], {**original[1], 'source_sha256': 'x' * 64})
        with mock.patch.object(V, 'load_inputs', side_effect=[original, changed]):
            report, _ = self.run_backend(FakeBackend())
        self.assertEqual(report['error_code'], 'LOCAL_INPUTS_CHANGED_DURING_VERIFICATION')

    def test_signal_keeps_pending_private_evidence(self):
        backend = FakeBackend()
        original = backend.grafana

        def interrupted(path, body=None):
            if body is not None:
                raise V.SESSION.SessionInterrupted(15)
            return original(path, body)

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'receipt'
            with self.assertRaises(V.SESSION.SessionInterrupted):
                V.run(output, reader=backend.reader, grafana=interrupted, now_ms=1789615000000)
            report = json.loads((output / 'summary.json').read_text())
            self.assertEqual(report['result'], 'interrupted')
            self.assertEqual(report['pending'], {'case': 'all', 'panel': 'attention'})


if __name__ == '__main__':
    unittest.main()
