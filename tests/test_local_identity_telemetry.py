"""Real local OTel export, fake AgentHouse/Loki; no live writes or model calls."""
import copy
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import uuid

from tools.local_experiment.trino_identity import telemetry as T
from tools.local_experiment.agenthouse import manage as AH


def case_fixture(case):
    user, expected = T.CASE_MAP[case]
    user = 'lab_admin' if user == 'control' else user
    index = list(T.CASE_MAP).index(case)
    query_id = None if expected == 'unauthenticated' else f'20260917_123456_{index:05d}_abcde'
    result = {'case': case, 'result': expected, 'query_id': query_id,
              'http_status': 401 if expected == 'unauthenticated' else 200,
              'error_name': 'PERMISSION_DENIED' if expected == 'denied' else None,
              'response_complete': True,
              'rows': T.expected_rows(user) if expected == 'success' else [],
              'sql_sha256': T.digest(T.SELECT.encode())}
    def decision(operation, outcome, *, weak=False):
        return {'case': case, 'decision_id': str(uuid.uuid4()), 'user': user,
                'target_user': 'lab_admin' if operation == 'ImpersonateUser' else None,
                'query_id': None if weak else query_id, 'operation': operation, 'outcome': outcome,
                'correlation': 'serial-case-window-and-user-operation' if weak else 'query-id-and-user'}
    rows = []
    if expected == 'success':
        rows.append(decision('SelectFromColumns', 'allow'))
        if user in {'lab_row', 'lab_both'}:
            rows.append(decision('GetRowFilters', 'row_filter'))
        if user in {'lab_mask', 'lab_both'}:
            rows.append(decision('GetColumnMask', 'column_mask'))
    elif expected == 'denied':
        rows.append(decision('ImpersonateUser' if case.startswith('forge_') else 'ExecuteQuery', 'deny', weak=True))
    return result, rows


class FakeBackend:
    def __init__(self):
        self.events, self.logs = [], []
        self.fail_verify = False
        self.fail_log = False
        self.interrupt = None

    def event(self, **fields):
        return AH.event(**fields)

    def insert(self, event):
        if self.interrupt is not None:
            raise self.interrupt
        self.events.append(copy.deepcopy(event))

    def verify(self, event):
        if self.fail_verify:
            raise RuntimeError('secret remote response must not survive')
        if self.events[-1] != event:
            raise ValueError('readback differs')

    def push_log(self, record):
        if self.fail_log:
            raise RuntimeError('secret remote log response')
        self.logs.append(copy.deepcopy(record))

    def verify_log(self, record):
        if self.logs[-1] != record:
            raise ValueError('log differs')


class ProjectionTests(unittest.TestCase):
    def test_projection_never_copies_rows_or_sql(self):
        result, decisions = case_fixture('admin_before')
        value = T.project_case('admin_before', result, decisions)
        encoded = json.dumps(value)
        self.assertTrue(value['expected_match'])
        self.assertNotIn('ALGERIA', encoded)
        self.assertNotIn(T.SELECT, encoded)
        self.assertEqual(value['duration_kind'], 'case-observation-including-audit-collection')
        self.assertFalse(value['decision_timestamp_observed'])

    def test_expected_match_separate_from_actual(self):
        result, decisions = case_fixture('admin_before')
        result['rows'] = [['sensitive-column-value']]
        value = T.project_case('admin_before', result, decisions)
        self.assertEqual(value['actual_result'], 'success')
        self.assertFalse(value['expected_match'])
        self.assertNotIn('sensitive-column-value', json.dumps(value))

    def test_weak_correlation_keeps_missing_query_id(self):
        result, rows = case_fixture('denied')
        projected = T.project_case('denied', result, rows)
        self.assertIsNone(projected['decisions'][0]['query_id'])
        self.assertEqual(projected['decisions'][0]['correlation'], 'serial-case-window-and-user-operation')

    def test_foreign_query_user_and_unknown_fields_rejected(self):
        for changed in ({'query_id': '20260917_123456_99999_abcde'}, {'user': 'lab_mask'},
                        {'jwt': 'secret'}, {'outcome': 'free-form'}, {'correlation': 'verified-trace'}):
            result, rows = case_fixture('admin_before')
            rows[0].update(changed)
            with self.assertRaises(T.TelemetryError):
                T.project_case('admin_before', result, rows)

    def test_unauthenticated_cannot_have_policy_decisions(self):
        result, _ = case_fixture('no_token')
        _, rows = case_fixture('admin_before')
        with self.assertRaises(T.TelemetryError):
            T.project_case('no_token', result, rows)

    def test_unmeasured_policy_evidence_is_not_zero_success(self):
        result, _ = case_fixture('admin_before')
        with self.assertRaisesRegex(T.TelemetryError, 'POLICY_EVIDENCE_MISSING'):
            T.project_case('admin_before', result, [])


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.token = uuid.uuid4().hex
        self.output = Path(self.temp.name).resolve() / self.token
        self.output.mkdir(mode=0o700)
        self.backend = FakeBackend()

    def tearDown(self):
        self.temp.cleanup()

    def run_cases(self, audit):
        for case in T.CASE_MAP:
            result, decisions = case_fixture(case)
            with audit.case(case) as observation:
                pending = dict(result, result='pending', response_complete=False, rows=[])
                observation.progress(pending)
                observation.finish(result, decisions)

    def receipt(self):
        return {'run_token': self.token, 'result': 'verified', 'pending': None,
                'cleanup': {'verified': True, 'removed': [], 'conflicts': [], 'deferred': []}}

    def journal(self):
        return json.loads((self.output / 'telemetry.json').read_text())

    def test_full_new_run_has_distinct_denials_and_closed_correlated_spans(self):
        with T.identity_telemetry(self.output, self.token, backend=self.backend) as audit:
            self.run_cases(audit)
            audit.finish(self.receipt())
        journal = self.journal()
        self.assertEqual(journal['result'], 'passed')
        self.assertTrue(journal['saved_spans_verified'])
        self.assertEqual(len(journal['records']), 16)
        events = self.backend.events
        self.assertEqual({row['agent_id'] for row in events}, {'local-trino-identity'})
        self.assertEqual({row['trace_id'] for row in events}, {journal['trace_id']})
        self.assertEqual(sum(row['blocked_by_policy'] for row in events), 3)
        self.assertEqual(sum(row['status'] == 'blocked' for row in events), 9)
        self.assertEqual(events[-1]['status'], 'success')
        self.assertTrue(all(row['prompt_tokens'] is None and row['completion_tokens'] is None
                            and row['inference_cost_estimate'] is None for row in events))
        self.assertEqual(len(self.backend.logs), 16)
        spans = [json.loads(line) for line in (self.output / 'identity-spans.jsonl').read_text().splitlines()]
        self.assertEqual(len(spans), 16)
        for span in spans:
            self.assertGreaterEqual(span['end_time_unix_nano'], span['start_time_unix_nano'])
            if span['name'] != 'trino.identity.run':
                self.assertEqual(span['attributes']['root_span_id'], journal['root_span_id'])
        self.assertNotIn('ALGERIA', json.dumps(journal) + json.dumps(events) + json.dumps(spans))
        for path in (self.output / 'telemetry.json', self.output / 'identity-spans.jsonl'):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_historical_or_existing_telemetry_is_not_rewritten(self):
        (self.output / 'telemetry.json').write_text('historical')
        with self.assertRaisesRegex(T.TelemetryError, 'ALREADY_EXISTS'):
            T.identity_telemetry(self.output, self.token, backend=self.backend)
        self.assertEqual((self.output / 'telemetry.json').read_text(), 'historical')

    def test_partial_progress_and_exception_remain_failed_without_secret(self):
        with self.assertRaisesRegex(RuntimeError, 'secret exception'):
            with T.identity_telemetry(self.output, self.token, backend=self.backend) as audit:
                with audit.case('admin_before') as observation:
                    result, _ = case_fixture('admin_before')
                    observation.progress(dict(result, result='pending', response_complete=False, rows=[]))
                    raise RuntimeError('secret exception containing bearer abc')
        journal = self.journal()
        self.assertEqual(journal['result'], 'failed')
        self.assertEqual(journal['records'][0]['observation']['actual_result'], 'pending')
        self.assertIsNotNone(journal['records'][0]['observation']['query_id'])
        self.assertNotIn('decision_count', journal['records'][0]['observation'])
        combined = ''.join(path.read_text() for path in self.output.iterdir())
        self.assertNotIn('secret exception', combined)
        self.assertTrue(all(event['status'] == 'failed' for event in self.backend.events))

    def test_unfinished_case_cannot_pass(self):
        with self.assertRaises(T.TelemetryError):
            with T.identity_telemetry(self.output, self.token, backend=self.backend) as audit:
                with audit.case('admin_before'):
                    pass
        self.assertEqual(self.journal()['result'], 'failed')
        self.assertIsNone(self.journal()['records'][0]['observation'])

    def test_partial_denial_is_not_claimed_as_verified_opa_rejection(self):
        with self.assertRaises(RuntimeError):
            with T.identity_telemetry(self.output, self.token, backend=self.backend) as audit:
                try:
                    with audit.case('denied') as observation:
                        result, _ = case_fixture('denied')
                        observation.progress(result)
                        raise RuntimeError('OPA evidence unavailable')
                finally:
                    audit.record_cleanup(self.receipt()['cleanup'])
        event = self.backend.events[0]
        self.assertEqual(event['status'], 'failed')
        self.assertFalse(event['blocked_by_policy'])
        journal = self.journal()
        self.assertEqual(journal['records'][0]['observation']['actual_result'], 'denied')
        self.assertTrue(journal['cleanup_verified'])
        self.assertEqual(journal['result'], 'failed')

    def test_cleanup_conflict_stays_failed_and_distinct_from_unknown(self):
        with self.assertRaisesRegex(T.TelemetryError, 'RUN_NOT_COMPLETED'):
            with T.identity_telemetry(self.output, self.token, backend=self.backend) as audit:
                audit.record_cleanup({'verified': False, 'removed': [], 'conflicts': ['owned-pod'], 'deferred': []})
        journal = self.journal()
        self.assertFalse(journal['cleanup_verified'])
        self.assertEqual(journal['cleanup']['conflicts_count'], 1)
        self.assertEqual(journal['result'], 'failed')

    def test_cleanup_must_be_verified_before_root_success(self):
        with self.assertRaisesRegex(T.TelemetryError, 'RUN_NOT_VERIFIED'):
            with T.identity_telemetry(self.output, self.token, backend=self.backend) as audit:
                self.run_cases(audit)
                receipt = self.receipt()
                receipt['cleanup']['verified'] = False
                audit.finish(receipt)
        self.assertEqual(self.backend.events[-1]['status'], 'failed')
        self.assertEqual(self.journal()['result'], 'failed')

    def test_delivery_failure_is_durable_and_not_completed(self):
        self.backend.fail_log = True
        with self.assertRaises(T.TelemetryError):
            with T.identity_telemetry(self.output, self.token, backend=self.backend) as audit:
                with audit.case('admin_before') as observation:
                    observation.finish(*case_fixture('admin_before'))
        journal = self.journal()
        self.assertEqual(journal['result'], 'failed')
        first = journal['records'][0]
        self.assertTrue(first['agenthouse_verified'])
        self.assertFalse(first['loki_verified'])
        self.assertEqual(first['delivery'], 'failed')
        self.assertNotIn('secret remote', json.dumps(journal))

    def test_interruption_preserves_type_and_has_failure_evidence(self):
        class SignalStop(BaseException):
            pass
        self.backend.interrupt = SignalStop()
        with self.assertRaises(SignalStop):
            with T.identity_telemetry(self.output, self.token, backend=self.backend) as audit:
                with audit.case('admin_before') as observation:
                    observation.finish(*case_fixture('admin_before'))
        self.assertEqual(self.journal()['result'], 'failed')
        self.assertEqual(self.journal()['records'][0]['delivery'], 'failed')

    def test_pending_journal_failure_prevents_network_delivery(self):
        original = T.write
        def write(path, value):
            if value.get('records'):
                raise OSError('disk unavailable')
            return original(path, value)
        with patch.object(T, 'write', side_effect=write):
            with self.assertRaises(Exception):
                with T.identity_telemetry(self.output, self.token, backend=self.backend) as audit:
                    with audit.case('admin_before') as observation:
                        observation.finish(*case_fixture('admin_before'))
        self.assertEqual(self.backend.events, [])

    def test_missing_span_export_cannot_publish_successful_root(self):
        with self.assertRaisesRegex(T.TelemetryError, 'FINALIZATION_FAILED'):
            with T.identity_telemetry(self.output, self.token, backend=self.backend) as audit:
                self.run_cases(audit)
                audit.finish(self.receipt())
                audit._verify_spans = lambda: (_ for _ in ()).throw(T.TelemetryError('SPANS_MISSING'))
        self.assertEqual(self.backend.events[-1]['status'], 'failed')

    def test_corrupted_saved_span_correlation_cannot_pass(self):
        with self.assertRaisesRegex(T.TelemetryError, 'FINALIZATION_FAILED'):
            with T.identity_telemetry(self.output, self.token, backend=self.backend) as audit:
                self.run_cases(audit)
                audit.finish(self.receipt())
                rows = [json.loads(line) for line in audit.spans.read_text().splitlines()]
                rows[0]['attributes']['trino.query_id'] = '20260917_123456_99999_abcde'
                audit.spans.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        self.assertEqual(self.backend.events[-1]['status'], 'failed')
        self.assertFalse(self.journal()['saved_spans_verified'])


class ReadbackTests(unittest.TestCase):
    def test_readback_checks_actor_policy_flag_and_unknown_usage(self):
        event = AH.event('trino.identity.run', 'trino-opa', agent_id=T.ACTOR, duration_ms=23)
        backend = object.__new__(T.Backend)
        backend.ah_guard = lambda: None
        for key, altered in [('agent_id', 'local-mcp-gateway'), ('blocked_by_policy', True),
                             ('prompt_tokens', 0), ('duration_ms', False)]:
            value = dict(event)
            value[key] = altered
            backend.client = types.SimpleNamespace(query=lambda sql: json.dumps({'data': [value]}))
            with self.assertRaises(T.TelemetryError):
                backend.verify(event)
        value = dict(event, duration_ms='23')
        backend.client = types.SimpleNamespace(query=lambda sql: json.dumps({'data': [value]}))
        backend.verify(event)

    def test_runtime_replacement_prevents_writes(self):
        called = []
        ah = types.SimpleNamespace(insert=lambda client, event: called.append('insert'))
        loki = types.SimpleNamespace(container=lambda: {'Id': 'replacement', 'State': {'Running': True}},
                                     push_record=lambda value: called.append('loki'))
        backend = object.__new__(T.Backend)
        backend.ah, backend.loki, backend.loki_id = ah, loki, 'original'
        backend.client = object()
        backend.ah_guard = lambda: None
        with self.assertRaisesRegex(T.TelemetryError, 'LOKI_IDENTITY_CHANGED'):
            backend.insert({})
        self.assertEqual(called, [])
        loki.container = lambda: {'Id': 'original', 'State': {'Running': True}}
        backend.ah_guard = lambda: (_ for _ in ()).throw(RuntimeError('AH changed'))
        with self.assertRaises(RuntimeError):
            backend.push_log({})
        self.assertEqual(called, [])


if __name__ == '__main__':
    unittest.main()
