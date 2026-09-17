"""Pure query projection and fake HTTP transport; never call a runtime endpoint."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('query_observe', ROOT / 'tools/local_experiment/query_doctor/observe.py')
O = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(O)
TOKEN = '1234567890abcdef1234567890abcdef'
USER = 'shirokuma-query-doctor-' + TOKEN
CAPTURED = {f'q_{case}_{kind}': {'case_id': case, 'kind': kind} for case in O.CASES for kind in ['select', 'explain']}


def plan_result(case='projection'):
    estimate = {'outputRowCount': 2.0, 'outputSizeInBytes': 18.0, 'cpuCost': 18.0, 'memoryCost': 0.0, 'networkCost': 0.0}
    unknown = dict(estimate, outputRowCount='NaN', outputSizeInBytes='NaN')
    scan = {'id': '0', 'name': 'TableScan' if case == 'projection' else 'ScanFilter',
            'outputs': [{'name': 'id', 'type': 'bigint'}],
            'descriptor': {'table': 'iceberg:smoke.fixture_v1$data@7141845066324476177'},
            'details': ['PRIVATE SQL AND PATH'], 'children': [],
            'estimates': [estimate] if case == 'projection' else [estimate, unknown]}
    if case == 'filtered':
        scan['descriptor']['filterPredicate'] = "(id = bigint '1')"
    root = {'id': '5', 'name': 'Output', 'outputs': [{'name': 'id', 'type': 'bigint'}],
            'descriptor': {'columnNames': '[id]'}, 'details': [], 'children': [scan],
            'estimates': [estimate if case == 'projection' else unknown]}
    return {'query_id': f'q_{case}_explain', 'columns': ['Query Plan'], 'rows': [[json.dumps({'0': root})]]}


def history_result():
    return {'query_id': 'q_history', 'columns': O.HISTORY_COLUMNS[:], 'rows': [
        [query_id, 'FINISHED', USER, 0, 2, 3, '2026-09-17T00:00:00.000Z',
         '2026-09-17T00:00:00.010Z', '2026-09-17T00:00:00.029Z', None, None]
        for query_id in CAPTURED]}


def stats_result():
    return {'query_id': 'q_stats', 'columns': O.STATS_COLUMNS[:], 'rows': [
        ['id', None, 2.0, 0.0, None, 'PRIVATE_MIN', 'PRIVATE_MAX'],
        ['name', 16.0, 2.0, 0.0, None, 'PRIVATE_NAME', 'PRIVATE_NAME'],
        [None, None, None, None, 2.0, None, None]]}


class ProjectionTests(unittest.TestCase):
    def test_real_shape_filtered_plan_preserves_multiple_estimates_and_unknown(self):
        projected = O.project_plan(plan_result('filtered'), 'filtered')
        self.assertFalse(projected['estimates_complete'])
        self.assertEqual([n['kind'] for n in projected['nodes']], ['Output', 'ScanFilter'])
        self.assertTrue(projected['nodes'][1]['filter_present'])
        estimates = projected['nodes'][1]['estimates']
        self.assertEqual(len(estimates), 2)
        self.assertEqual(estimates[0]['rows'], 2.0)
        self.assertIsNone(estimates[1]['rows'])
        self.assertEqual(estimates[1]['memory_cost'], 0.0)
        for private in ['PRIVATE', 'filterPredicate', '$data', 'SELECT', 'details']:
            self.assertNotIn(private, json.dumps(projected))

    def test_missing_estimates_and_nonfinite_values_never_become_zero(self):
        result = plan_result()
        plan = json.loads(result['rows'][0][0])
        for value in ['NaN', float('nan'), float('inf'), 'Infinity', '-Infinity', None]:
            with self.subTest(value=value):
                plan['0']['estimates'][0]['outputRowCount'] = value
                result['rows'] = [[json.dumps(plan)]]
                self.assertIsNone(O.project_plan(result, 'projection')['nodes'][0]['estimates'][0]['rows'])
        plan['0'].pop('estimates')
        result['rows'] = [[json.dumps(plan)]]
        self.assertFalse(O.project_plan(result, 'projection')['estimates_complete'])

    def test_wrong_source_projection_filter_operator_and_boolean_estimate_rejected(self):
        edits = [lambda p: p['0']['children'][0]['descriptor'].update(table='iceberg:private.secret$data@1'),
                 lambda p: p['0']['outputs'][0].update(name='name'),
                 lambda p: p['0']['children'][0]['descriptor'].update(filterPredicate='secret condition'),
                 lambda p: p['0']['children'][0].update(name='Join'),
                 lambda p: p['0']['children'][0].update(id='5'),
                 lambda p: p['0']['estimates'][0].update(outputRowCount=True),
                 lambda p: p['0']['estimates'][0].update(cpuCost=-1),
                 lambda p: p.update({'1': p['0']})]
        for edit in edits:
            value = plan_result()
            raw = json.loads(value['rows'][0][0])
            edit(raw)
            value['rows'] = [[json.dumps(raw)]]
            with self.assertRaises(O.ObservationError):
                O.project_plan(value, 'projection')
        with self.assertRaises(O.ObservationError):
            O.project_plan(plan_result('filtered'), 'projection')

    def test_oversized_and_duplicate_json_plan_rejected(self):
        result = plan_result()
        for raw in ['x' * (O.MAX_PLAN_BYTES + 1), '{"0":{},"0":{}}', 'broken']:
            result['rows'] = [[raw]]
            with self.assertRaises(O.ObservationError):
                O.project_plan(result, 'projection')

    def test_history_is_own_captured_queries_only_and_durations_are_distinct(self):
        projected = O.project_history(history_result(), CAPTURED, USER)
        self.assertEqual(projected['completeness'], 'complete')
        self.assertTrue(projected['durations_complete'])
        for entry in projected['entries']:
            self.assertEqual(entry['created_to_end_ms'], 29)
            self.assertEqual(entry['started_to_end_ms'], 19)
            self.assertEqual(entry['queued_ms'], 0)
            self.assertNotIn('cpu_ms', entry)
            self.assertNotIn('query', entry)
            self.assertNotIn('user', entry)
        self.assertEqual({e['kind'] for e in projected['entries']}, {'select', 'explain'})

    def test_missing_running_and_null_history_are_unknown_not_zero(self):
        result = history_result()
        result['rows'] = result['rows'][:1]
        result['rows'][0][1] = 'RUNNING'
        result['rows'][0][3] = None
        result['rows'][0][8] = None
        projected = O.project_history(result, CAPTURED, USER)
        self.assertEqual(projected['completeness'], 'partial')
        self.assertEqual(len(projected['missing_query_ids']), 3)
        self.assertFalse(projected['durations_complete'])
        self.assertIsNone(projected['entries'][0]['started_to_end_ms'])
        self.assertIsNone(projected['entries'][0]['queued_ms'])
        result['rows'] = []
        self.assertEqual(O.project_history(result, CAPTURED, USER)['completeness'], 'missing')

    def test_history_rejects_foreign_duplicate_reversed_or_invalid_measurements(self):
        for index, value in [(0, 'uncaptured'), (2, 'another-user'), (1, 'UNKNOWN'),
                             (3, True), (3, -1), (3, 'NaN'), (3, 1.5),
                             (7, '2026-09-16T00:00:00Z'), (9, 'private raw text')]:
            result = history_result()
            result['rows'][0][index] = value
            with self.subTest(index=index, value=value), self.assertRaises(O.ObservationError):
                O.project_history(result, CAPTURED, USER)
        result = history_result()
        result['rows'][1] = result['rows'][0]
        with self.assertRaises(O.ObservationError):
            O.project_history(result, CAPTURED, USER)

    def test_failed_history_keeps_safe_error_classification_without_cause_claim(self):
        result = history_result()
        result['rows'][0][1] = 'FAILED'
        result['rows'][0][9:] = ['USER_ERROR', 'QUERY_REJECTED']
        entry = next(e for e in O.project_history(result, CAPTURED, USER)['entries'] if e['state'] == 'FAILED')
        self.assertEqual(entry['error_code'], 'QUERY_REJECTED')
        self.assertNotIn('root_cause', entry)

    def test_statistics_do_not_expose_low_high_values_or_invent_missing_estimates(self):
        projected = O.project_stats(stats_result())
        self.assertEqual(projected['row_count_estimate'], 2)
        self.assertFalse(projected['estimates_complete'])
        self.assertIsNone(projected['columns'][0]['data_size'])
        self.assertNotIn('PRIVATE', json.dumps(projected))
        for index, value in [(0, 'email'), (3, 1.1), (2, -1)]:
            result = stats_result()
            result['rows'][0][index] = value
            with self.assertRaises(O.ObservationError):
                O.project_stats(result)


class FakeObserver(O.QueryObserver):
    def __init__(self):
        super().__init__('http://127.0.0.1:18080', TOKEN)
        self.sql = []

    def _query(self, sql):
        self.sql.append(sql)
        for case, (statement, expected) in O.CASES.items():
            if sql == statement:
                return {'query_id': f'q_{case}_select', 'columns': ['id'], 'rows': copy.deepcopy(expected)}
            if sql == 'EXPLAIN (TYPE DISTRIBUTED, FORMAT JSON) ' + statement:
                return plan_result(case)
        if sql.startswith('SELECT query_id, state, "user",'):
            return history_result()
        if sql == 'SHOW STATS FOR iceberg.smoke.fixture_v1':
            return stats_result()
        raise AssertionError('unexpected SQL')


class ObserverTests(unittest.TestCase):
    def test_collect_and_followup_are_fixed_read_only_and_run_scoped(self):
        observer = FakeObserver()
        result = observer.collect()
        self.assertEqual(len(observer.sql), 5)
        self.assertEqual([c['select']['row_count'] for c in result['cases']], [2, 1])
        self.assertEqual(result['performance_gain'], 'unmeasured')
        history_sql = observer.sql[-1]
        self.assertIn('WHERE "user" = \'' + USER + "' AND query_id IN (", history_sql)
        for query_id in CAPTURED:
            self.assertIn("'" + query_id + "'", history_sql)
        self.assertNotIn(' SELECT query,', history_sql)
        self.assertEqual(observer.followup('fixture_stats')['row_count_estimate'], 2)
        self.assertEqual(observer.followup('own_query_history')['completeness'], 'complete')
        self.assertEqual(len(observer.sql), 7)
        for kind in ['fixture_stats', 'SELECT * FROM private', '../path', 'other_user']:
            with self.assertRaises(O.ObservationError):
                observer.followup(kind)
        with self.assertRaises(O.ObservationError):
            observer.collect()

    def test_independent_row_oracle_rejects_wrong_rows_types_and_query_reuse(self):
        for rows in [[[2]], [[True], [2]], [[1.0], [2]], [[1], [2], [3]]]:
            observer = FakeObserver()
            original = observer._query
            def query(sql):
                result = original(sql)
                if sql == O.CASES['projection'][0]:
                    result['rows'] = rows
                return result
            observer._query = query
            with self.assertRaises(O.ObservationError):
                observer.collect()
            self.assertEqual(len(observer.sql), 1)
        observer = FakeObserver()
        observer._capture({'query_id': 'same'}, 'projection', 'select')
        with self.assertRaises(O.ObservationError):
            observer._capture({'query_id': 'same'}, 'filtered', 'select')

    def test_remote_endpoint_userinfo_and_invalid_token_rejected(self):
        for url in ['http://example.com:18080', 'https://127.0.0.1:18080', 'http://u:p@127.0.0.1:18080',
                    'http://127.0.0.1:18080/private', 'http://127.0.0.1:18080?x=y', 'http://127.0.0.1']:
            with self.assertRaises(O.ObservationError):
                O.QueryObserver(url)
        with self.assertRaises(O.ObservationError):
            O.QueryObserver('http://127.0.0.1:18080', "x'; select")
        with self.assertRaises(O.ObservationError):
            FakeObserver().followup('fixture_stats')


class Response:
    def __init__(self, page):
        self.raw = json.dumps(page).encode()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self, limit):
        return self.raw[:limit]


class Opener:
    def __init__(self, pages):
        self.pages, self.requests = list(pages), []
    def open(self, request, timeout):
        self.requests.append(request)
        return Response(self.pages.pop(0))


class TransportTests(unittest.TestCase):
    def test_partial_query_is_captured_before_timeout_and_receipt_stays_unverified(self):
        observer = O.QueryObserver('http://127.0.0.1:18080', TOKEN)
        observer.opener = Opener([{'id': 'q_started', 'stats': {'state': 'RUNNING'},
            'nextUri': 'http://127.0.0.1:18080/v1/statement/queued/q_started/token/1'}])
        with patch.object(O.time, 'monotonic', side_effect=[0, 1, 36]):
            with self.assertRaisesRegex(O.ObservationError, 'timed out'):
                observer._query(O.CASES['projection'][0])
        self.assertEqual(observer.captured['q_started'], {'case_id': 'projection', 'kind': 'select'})
        receipt = observer.receipt()
        self.assertEqual(receipt['outcome'], 'failed')
        self.assertEqual(receipt['server_max_run_seconds'], 30)
        self.assertFalse(receipt['cleanup_verified'])
        self.assertEqual(receipt['requests'][0]['query_id'], 'q_started')
        self.assertFalse(receipt['requests'][0]['response_complete'])
        self.assertNotIn('SELECT', json.dumps(receipt))

    def test_interruption_without_response_keeps_uncaptured_request_unknown(self):
        class SessionInterrupted(BaseException):
            pass
        for kind in (KeyboardInterrupt, SessionInterrupted):
            observer = O.QueryObserver('http://127.0.0.1:18080', TOKEN)
            def interrupted(*args, **kwargs):
                raise kind
            observer.opener.open = interrupted
            with self.assertRaises(kind):
                observer._query(O.CASES['projection'][0])
            receipt = observer.receipt()
            self.assertEqual(receipt['outcome'], 'interrupted')
            self.assertEqual(receipt['uncaptured_requests'], 1)
            self.assertFalse(receipt['cleanup_verified'])

    def test_normal_collection_requires_history_terminal_and_all_responses_complete(self):
        observer = O.QueryObserver('http://127.0.0.1:18080', TOKEN)
        pages = []
        for case, (_sql, expected) in O.CASES.items():
            pages.append({'id': f'q_{case}_select', 'stats': {'state': 'FINISHED'}, 'columns': [{'name': 'id'}], 'data': expected})
            plan = plan_result(case)
            pages.append({'id': plan['query_id'], 'stats': {'state': 'FINISHED'},
                          'columns': [{'name': 'Query Plan'}], 'data': plan['rows']})
        history = history_result()
        pages.append({'id': history['query_id'], 'stats': {'state': 'FINISHED'},
                      'columns': [{'name': name} for name in O.HISTORY_COLUMNS], 'data': history['rows']})
        observer.opener = Opener(pages)
        observer.collect()
        receipt = observer.receipt()
        self.assertEqual(receipt['outcome'], 'completed')
        self.assertTrue(receipt['cleanup_verified'])
        self.assertTrue(receipt['target_history_verified'])
        self.assertEqual(len(receipt['requests']), 5)
        observer.history_verified = False
        self.assertFalse(observer.receipt()['cleanup_verified'])

    def test_owned_headers_and_same_id_pagination(self):
        observer = O.QueryObserver('http://127.0.0.1:18080', TOKEN)
        observer.opener = Opener([
            {'id': 'q1', 'nextUri': 'http://127.0.0.1:18080/v1/statement/queued/q1/token/1'},
            {'id': 'q1', 'columns': [{'name': 'id'}], 'data': [[1], [2]]}])
        result = observer._query(O.CASES['projection'][0])
        self.assertEqual(result['rows'], [[1], [2]])
        for request in observer.opener.requests:
            headers = {k.lower(): v for k, v in request.header_items()}
            self.assertEqual(headers['x-trino-user'], USER)
            self.assertIn('query_max_run_time=30s', headers['x-trino-session'])

    def test_foreign_origin_path_id_or_empty_polling_link_rejected(self):
        for uri in ['http://remote:8080/v1/statement/queued/q1/token/1',
                    'http://127.0.0.1:18080/v1/query/q1',
                    'http://127.0.0.1:18080/v1/statement/queued/other/token/1', '']:
            observer = O.QueryObserver('http://127.0.0.1:18080', TOKEN)
            observer.opener = Opener([{'id': 'q1', 'nextUri': uri}])
            with self.assertRaises(O.ObservationError):
                observer._query(O.CASES['projection'][0])
            self.assertEqual(len(observer.opener.requests), 1)

    def test_page_identity_mismatch_and_raw_error_not_exposed(self):
        observer = O.QueryObserver('http://127.0.0.1:18080', TOKEN)
        observer.opener = Opener([{'id': 'q1', 'error': {'message': 'PRIVATE credential and SQL'}}])
        with self.assertRaises(O.ObservationError) as caught:
            observer._query(O.CASES['projection'][0])
        self.assertNotIn('PRIVATE', str(caught.exception))
        observer.opener = Opener([{'id': 'q1', 'nextUri': 'http://127.0.0.1:18080/v1/statement/queued/q1/token/1'}, {'id': 'other'}])
        with self.assertRaises(O.ObservationError):
            observer._query(O.CASES['projection'][0])


if __name__ == '__main__':
    unittest.main()
