"""Gateway protocol and independent routing checks; fake HTTP only."""
import copy
import io
import json
import os
import unittest
from unittest.mock import patch

from tools.local_experiment.gateway_warehouse import client as C

TOKEN = 'a' * 32


def response(value, status=200):
    return {'status': status, 'body': json.dumps(value).encode()}


def columns(values):
    return [{'name': name, 'type': kind} for name, kind in values]


class FakeClient(C.Client):
    def __init__(self):
        super().__init__(TOKEN)
        self.calls, self.queries = [], {}
        self.ledgers = {'xs': [], 's': []}
        self.mode = None
        self.observer_count = 0
        self.registry = C.backend_records(TOKEN)
        self.health_calls = 0

    def request(self, target, path, *, data=None, headers=None, timeout=10):
        headers = headers or {}
        self.calls.append((target, path, data, dict(headers)))
        if path == '/v1/info':
            return response({'nodeVersion': {'version': '483'}, 'starting': False})
        if path == '/gateway/backend/all':
            values = copy.deepcopy(self.registry)
            if self.mode == 'foreign_registry':
                values[0]['proxyTo'] = 'http://retained-trino:8080'
            if self.mode == 'registration_readback_mismatch' and len(values) == 1:
                values[0]['active'] = False
            return response(values)
        if path == C.REGISTER_PATH:
            assert target == 'gateway'
            assert headers == {'Content-Type': 'application/json'}
            value = json.loads(data)
            assert value in C.backend_records(TOKEN)
            self.registry.append(value)
            if self.mode == 'registration_lost':
                raise TimeoutError('private database details')
            if self.mode == 'registration_interrupted':
                raise KeyboardInterrupt()
            if self.mode == 'registration_bad_body':
                value = dict(value, active=1)
            return response(value, 201 if self.mode == 'registration_bad_status' else 200)
        if path == C.HEALTH_PATH:
            assert target == 'gateway' and data == b'{}'
            self.health_calls += 1
            status = 'PENDING' if self.mode == 'health_pending' and self.health_calls == 1 else 'HEALTHY'
            values = [dict(record, status=status, queued=None, running=None) for record in self.registry]
            if self.mode == 'health_foreign' and values:
                values[0]['proxyTo'] = 'http://retained-trino:8080'
            return response({'code': 200, 'msg': 'Successful.', 'data': values})
        if target != 'gateway':
            assert data == self._history_sql().encode()
            assert b'WHERE source IN (' in data and b'WHERE "user"' not in data and b'AND "user"' not in data
            assert all(C.source(TOKEN, name).encode() in data for name in C.CASE_MAP)
            assert headers['X-Trino-User'] == self.observer
            assert headers['X-Trino-Source'].startswith('shirokuma-vw-' + TOKEN + '-observe-')
            self.observer_count += 1
            values = copy.deepcopy(self.ledgers[target])
            if self.mode == 'history_missing':
                values = []
            elif self.mode == 'history_wrong_source' and values:
                values[0][3] = C.source(TOKEN, 'unknown')
            elif self.mode == 'history_wrong_backend':
                values = copy.deepcopy(self.ledgers['s' if target == 'xs' else 'xs'])
            elif self.mode == 'history_duplicate' and values:
                values.append(values[0])
            elif self.mode == 'history_wrong_error' and values:
                values[1][4] = 'NOT_SUPPORTED'
            elif self.mode == 'history_wrong_user' and values:
                values[0][2] = self.observer
            elif self.mode in {'history_unknown_arrived', 'history_unknown_wrong_user'} and target == 'xs':
                user = 'rewritten-user' if self.mode == 'history_unknown_wrong_user' else self.worker
                values.append(['20260917_120000_99999_abcde', 'FINISHED', user, C.source(TOKEN, 'unknown'), None])
            return response({'id': f'20260917_120000_{100 + self.observer_count:05}_abcde',
                             'columns': columns(C.HISTORY_COLUMNS), 'data': values})
        if data is not None:
            case = next(name for name in C.CASE_MAP if headers['X-Trino-Source'] == C.source(TOKEN, name))
            backend, outcome = C.CASE_MAP[case]
            assert data == (C.DROP if outcome == 'denied' else C.SELECT).encode()
            assert headers['X-Trino-User'] == self.worker
            if outcome == 'unmatched':
                assert headers.get(C.HEADER) == ('UNKNOWN' if case == 'unknown' else None)
                assert headers.get('X-Trino-Routing-Group') == ('local-xs' if case == 'unknown' else None)
                if self.mode == 'unmatched_timeout':
                    raise TimeoutError('sensitive server response')
                return response({'status': 'server error'}, 200 if self.mode == 'unmatched_fallback' else 500)
            assert headers[C.HEADER] == backend.upper()
            query_id = f'20260917_120000_{list(C.CASE_MAP).index(case):05}_abcde'
            self.queries[query_id] = case
            self.ledgers[backend].append([query_id, 'FINISHED' if outcome == 'success' else 'FAILED',
                self.worker, C.source(TOKEN, case), None if outcome == 'success' else 'PERMISSION_DENIED'])
            if self.mode == 'uncaptured':
                raise TimeoutError('sensitive server response')
            if self.mode == 'no_followup':
                return self.final(query_id, case)
            uri = C.ORIGINS['gateway'] + '/v1/statement/queued/' + query_id + '/slug/0'
            if self.mode == 'foreign_next':
                uri = uri.replace(C.ORIGINS['gateway'], C.ORIGINS[backend])
            return response({'id': query_id, 'nextUri': uri})
        query_id = path.split('/')[4]
        case = self.queries[query_id]
        backend, _ = C.CASE_MAP[case]
        assert headers[C.HEADER] == (('S' if backend == 'xs' else 'XS') if case.endswith('_before') else backend.upper())
        if self.mode == 'interrupted':
            raise KeyboardInterrupt()
        return self.final(query_id, case)

    def final(self, query_id, case):
        backend, outcome = C.CASE_MAP[case]
        if self.mode == 'qid_changed':
            query_id = '20260917_120000_99999_abcde'
        if outcome == 'denied':
            return response({'id': query_id, 'error': {'errorName': 'NOT_SUPPORTED' if self.mode == 'connector_denial' else 'PERMISSION_DENIED',
                                                      'message': 'sensitive error not copied'}})
        values = C.expected_rows(TOKEN, 's' if self.mode == 'wrong_node' and backend == 'xs' else backend)
        if self.mode == 'bool_integer':
            values[0][4] = False
        return response({'id': query_id, 'columns': columns(C.SELECT_COLUMNS), 'data': values})


class WarehouseClientTests(unittest.TestCase):
    def setUp(self):
        self.sleep = patch.object(C.time, 'sleep')
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def prepared(self, mode=None):
        client = FakeClient()
        client.mode = mode
        client.ready(timeout=1)
        return client

    def test_all_eight_cases_sticky_gets_and_six_independent_history_rows(self):
        client, snapshots = self.prepared(), []
        client.run_cases(progress=snapshots.append)
        history = client.verify_history(progress=snapshots.append)
        client.ready(timeout=1)
        receipt = client.receipt()
        self.assertEqual(receipt['result'], 'verified')
        self.assertEqual(len(receipt['cases']), 8)
        self.assertEqual(history['query_count'], 6)
        self.assertEqual([len(history['backends'][name]) for name in ('xs', 's')], [3, 3])
        self.assertEqual(history['unknown_source_counts'], {'xs': 0, 's': 0})
        self.assertEqual(history['missing_source_counts'], {'xs': 0, 's': 0})
        self.assertEqual(len(receipt['readiness']), 2)
        self.assertTrue(receipt['query_cleanup_verified'])
        self.assertEqual(receipt['uncaptured_requests'], 0)
        self.assertIsNone(receipt['pending'])
        self.assertEqual([case['opposite_route_gets'] for case in receipt['cases'][:2]], [1, 1])
        self.assertEqual(len(receipt['requests']), 10)
        self.assertFalse(snapshots[0]['requests'][0]['response_complete'])
        encoded = C.canonical(receipt)
        for raw in (C.SELECT, C.DROP, 'ALGERIA', 'sensitive error'):
            self.assertNotIn(raw, encoded)
        for target, path, data, _ in client.calls:
            if data is not None and target != 'gateway':
                self.assertEqual(data.decode(), client._history_sql())
        receipt['cases'].clear()
        self.assertEqual(len(client.receipt()['cases']), 8)

    def test_registers_only_empty_database_two_fixed_records_then_runs(self):
        client, snapshots = FakeClient(), []
        client.registry = []
        guards = []
        registration = client.register_backends(progress=snapshots.append, guard=lambda: guards.append('same-uid'))
        self.assertEqual(guards, ['same-uid', 'same-uid'])
        self.assertEqual(registration['result'], 'verified')
        self.assertTrue(registration['initial_registry_empty'])
        self.assertTrue(registration['readback_verified'])
        self.assertEqual([row['result'] for row in registration['attempts']], ['confirmed', 'confirmed'])
        self.assertEqual(client.registry, C.backend_records(TOKEN))
        self.assertTrue(any(value['pending'] and value['pending'].get('operation') == 'register-backend'
                            and value['registration']['attempts'][-1]['result'] == 'pending' for value in snapshots))
        self.assertTrue(any(value['registration']['attempts'] and value['registration']['attempts'][-1]['result'] == 'response-observed'
                            for value in snapshots))
        self.assertEqual(client.receipt()['unconfirmed_registrations'], 0)
        client.ready(timeout=1)
        client.run_cases()
        client.verify_history()
        self.assertEqual(client.receipt()['result'], 'verified')
        with self.assertRaisesRegex(C.ExperimentError, 'REGISTRATION_SEQUENCE_INVALID'):
            client.register_backends(guard=lambda: None)

    def test_any_existing_backend_prevents_all_registration_posts(self):
        for values in (C.backend_records(TOKEN), [{'name': 'foreign'}]):
            with self.subTest(values=values):
                client = FakeClient()
                client.registry = values
                with self.assertRaisesRegex(C.ExperimentError, 'REGISTRY_NOT_EMPTY'):
                    client.register_backends(guard=lambda: None)
                self.assertFalse(any(call[1] == C.REGISTER_PATH for call in client.calls))
                self.assertEqual(client.registry, values)
                self.assertEqual(client.receipt()['registration']['attempts'], [])

    def test_registration_uncertain_create_has_no_readback_adoption_or_second_post(self):
        for mode, error in [('registration_lost', TimeoutError), ('registration_interrupted', KeyboardInterrupt)]:
            with self.subTest(mode=mode):
                client = FakeClient()
                client.registry, client.mode = [], mode
                with self.assertRaises(error):
                    client.register_backends(guard=lambda: None)
                receipt = client.receipt()
                self.assertEqual(len(client.registry), 1)
                self.assertEqual(receipt['unconfirmed_registrations'], 1)
                self.assertEqual(receipt['registration']['attempts'][0]['result'], 'pending')
                self.assertFalse(receipt['registration']['readback_verified'])
                self.assertEqual(client.calls[-1][1], C.REGISTER_PATH)
                self.assertEqual(receipt['pending']['operation'], 'register-backend')
                self.assertNotIn('private database details', C.canonical(receipt))

    def test_registration_response_and_readback_must_match_exactly(self):
        for mode, error in [('registration_bad_body', 'REGISTRATION_RESPONSE_INVALID'),
                            ('registration_bad_status', 'REGISTRATION_RESPONSE_INVALID'),
                            ('registration_readback_mismatch', 'REGISTRATION_READBACK_MISMATCH')]:
            with self.subTest(mode=mode):
                client = FakeClient()
                client.registry, client.mode = [], mode
                with self.assertRaisesRegex(C.ExperimentError, error):
                    client.register_backends(guard=lambda: None)
                receipt = client.receipt()
                self.assertEqual(receipt['result'], 'failed')
                self.assertEqual(receipt['unconfirmed_registrations'], 1)
                self.assertEqual(receipt['registration']['attempts'][0]['result'], 'response-observed')
                self.assertEqual(sum(call[1] == C.REGISTER_PATH for call in client.calls), 1)

    def test_registration_progress_write_failure_prevents_post(self):
        client = FakeClient()
        client.registry = []
        def progress(value):
            if value['pending'] and value['pending']['operation'] == 'register-backend':
                raise OSError('private output path')
        with self.assertRaises(OSError):
            client.register_backends(progress=progress, guard=lambda: None)
        self.assertEqual(client.registry, [])
        self.assertFalse(any(call[1] == C.REGISTER_PATH for call in client.calls))
        self.assertEqual(client.receipt()['registration']['attempts'][0]['result'], 'pending')

    def test_registration_requires_scope_guard_and_guard_failure_prevents_post(self):
        client = FakeClient()
        client.registry = []
        with self.assertRaisesRegex(C.ExperimentError, 'REGISTRATION_GUARD_REQUIRED'):
            client.register_backends()
        self.assertEqual(client.calls, [])
        def guard():
            raise C.ExperimentError('POD_UID_CHANGED')
        with self.assertRaisesRegex(C.ExperimentError, 'POD_UID_CHANGED'):
            client.register_backends(guard=guard)
        self.assertFalse(any(call[1] == C.REGISTER_PATH for call in client.calls))
        self.assertEqual(client.registry, [])
        self.assertEqual(client.receipt()['registration']['attempts'][0]['result'], 'pending')

    def test_query_oracle_rejects_misroute_id_change_nonpolicy_deny_and_no_sticky_observation(self):
        for mode, error in [('wrong_node', 'CASE_ROWS_MISMATCH'), ('qid_changed', 'QUERY_ID_MISMATCH'),
                            ('connector_denial', 'QUERY_ERROR_UNEXPECTED'), ('no_followup', 'STICKINESS_UNOBSERVED'),
                            ('foreign_next', 'UNSAFE_NEXT_URI'), ('bool_integer', 'CASE_ROWS_MISMATCH')]:
            with self.subTest(mode=mode):
                client = self.prepared(mode)
                with self.assertRaisesRegex(C.ExperimentError, error):
                    client.run_cases()
                self.assertEqual(client.receipt()['result'], 'failed')
                self.assertFalse(client.receipt()['query_cleanup_verified'])

    def test_negative_requires_http500_and_timeouts_never_match(self):
        for mode in ('unmatched_fallback', 'unmatched_timeout'):
            with self.subTest(mode=mode):
                client = self.prepared(mode)
                with self.assertRaises((C.ExperimentError, TimeoutError)):
                    client.run_cases()
                receipt = client.receipt()
                self.assertEqual(receipt['result'], 'failed')
                self.assertEqual(len(receipt['cases']), 2)
                self.assertIsNone(receipt['history'])
                self.assertNotIn('sensitive', C.canonical(receipt))

    def test_http500_alone_never_proves_unknown_route_absence(self):
        for mode in ('history_unknown_arrived', 'history_unknown_wrong_user'):
            with self.subTest(mode=mode):
                client = self.prepared(mode)
                client.run_cases()
                self.assertEqual(client.receipt()['result'], 'cases-verified')
                with self.assertRaisesRegex(C.ExperimentError, 'HISTORY_UNEXPECTED_QUERY'):
                    client.verify_history()
                self.assertEqual(client.receipt()['result'], 'failed')

    def test_history_rejects_missing_foreign_duplicate_wrong_state_and_wrong_identity(self):
        for mode, error in [('history_missing', 'HISTORY_INCOMPLETE'), ('history_wrong_source', 'HISTORY_IDENTITY_MISMATCH'),
                            ('history_wrong_backend', 'HISTORY_IDENTITY_MISMATCH'), ('history_duplicate', 'HISTORY_UNEXPECTED_QUERY'),
                            ('history_wrong_error', 'HISTORY_OUTCOME_MISMATCH'), ('history_wrong_user', 'HISTORY_IDENTITY_MISMATCH')]:
            with self.subTest(mode=mode):
                client = self.prepared(mode)
                client.run_cases()
                with self.assertRaisesRegex(C.ExperimentError, error):
                    client.verify_history()
                self.assertEqual(client.receipt()['result'], 'failed')
                self.assertFalse(client.receipt()['query_cleanup_verified'])

    def test_callback_failure_stops_before_post_and_preserves_pending(self):
        client = self.prepared()
        def failure(_):
            raise OSError('private path')
        with self.assertRaises(OSError):
            client.run_cases(progress=failure)
        self.assertFalse(any(call[1] == '/v1/statement' and call[2] is not None for call in client.calls))
        self.assertEqual(client.receipt()['uncaptured_requests'], 1)
        self.assertIsNotNone(client.receipt()['pending'])
        self.assertNotIn('private path', C.canonical(client.receipt()))

    def test_lost_post_response_and_captured_id_interruption_keep_uncertainty(self):
        client = self.prepared('uncaptured')
        with self.assertRaises(TimeoutError):
            client.run_cases()
        self.assertEqual(client.receipt()['uncaptured_requests'], 1)
        self.assertEqual(client.receipt()['requests'][0]['query_id'], None)
        client = self.prepared('interrupted')
        with self.assertRaises(KeyboardInterrupt):
            client.run_cases()
        self.assertEqual(client.receipt()['result'], 'interrupted')
        self.assertEqual(client.receipt()['uncaptured_requests'], 0)
        self.assertIsNotNone(client.receipt()['requests'][0]['query_id'])
        self.assertFalse(client.receipt()['requests'][0]['response_complete'])

    def test_registry_wrong_target_stops_before_queries(self):
        client = FakeClient()
        client.mode = 'foreign_registry'
        with self.assertRaisesRegex(C.ExperimentError, 'GATEWAY_REGISTRY_MISMATCH'):
            client.ready(timeout=1)
        self.assertEqual(client.queries, {})

    def test_ready_waits_for_gateway_health_after_active_registration(self):
        client = self.prepared('health_pending')
        self.assertEqual(client.health_calls, 2)
        self.assertEqual(client.receipt()['readiness'][0]['gateway_backend_health'], {'xs': 'HEALTHY', 's': 'HEALTHY'})
        self.assertEqual(client.queries, {})
        with self.assertRaisesRegex(C.ExperimentError, 'GATEWAY_HEALTH_IDENTITY_MISMATCH'):
            self.prepared('health_foreign')

    def test_next_uri_rejects_external_backend_path_query_and_encoded_components(self):
        query_id = '20260917_120000_00001_abcde'
        good = C.ORIGINS['gateway'] + '/v1/statement/executing/' + query_id + '/slug/1'
        self.assertTrue(C.next_uri(good, 'gateway', query_id).startswith('/v1/statement/'))
        variants = [good.replace('18580', '18581'), good.replace('127.0.0.1', 'localhost'),
                    good + '?q=1', good + '#fragment', good.replace('/slug/', '/../../'),
                    good.replace(query_id, '20260917_120000_00002_abcde'), good.replace('/slug/', '/%2F/'),
                    good.replace('http://', 'http://user@'), good.replace('/v1/statement/', '/v1/query/')]
        for value in variants:
            with self.subTest(value=value), self.assertRaises(C.ExperimentError):
                C.next_uri(value, 'gateway', query_id)

    def test_json_rejects_nan_and_duplicate_keys(self):
        for value in (b'{"state":NaN}', b'{"id":1,"id":2}', b'\xff'):
            with self.subTest(value=value), self.assertRaises(C.ExperimentError):
                C.parse_json(value)

    def test_fixed_http_scope_disallows_direct_backend_workload_arbitrary_sql_and_redirects(self):
        client = C.Client(TOKEN)
        with self.assertRaisesRegex(C.ExperimentError, 'QUERY_SCOPE_INVALID'):
            client.request('xs', '/v1/statement', data=C.SELECT.encode())
        with self.assertRaisesRegex(C.ExperimentError, 'QUERY_SCOPE_INVALID'):
            client.request('gateway', '/v1/statement', data=b'SELECT sensitive')
        for target, path in [('remote', '/v1/info'), ('gateway', '//evil/v1/info'), ('xs', '/gateway/backend/all')]:
            with self.assertRaises(C.ExperimentError):
                client.request(target, path)
        with self.assertRaisesRegex(C.ExperimentError, 'REDIRECT_REFUSED'):
            C.NoRedirect().redirect_request(None, None, None, None, None, None)

    def test_http_body_limit_timeout_and_proxy_independence(self):
        class Response(io.BytesIO):
            status = 200
        with patch.dict(os.environ, {'http_proxy': 'http://untrusted.invalid:8080'}):
            client = C.Client(TOKEN)
        with patch.object(client.opener, 'open', return_value=Response(b'x' * (1024**2 + 1))):
            with self.assertRaisesRegex(C.ExperimentError, 'HTTP_RESPONSE_LIMIT'):
                client.request('xs', '/v1/info')
        with patch.object(client.opener, 'open', side_effect=TimeoutError('secret')):
            with self.assertRaisesRegex(C.ExperimentError, '^HTTP_TRANSPORT_FAILED$'):
                client.request('xs', '/v1/info')
        self.assertFalse(any(getattr(handler, 'proxies', {}) for handler in client.opener.handlers))


if __name__ == '__main__':
    unittest.main()
