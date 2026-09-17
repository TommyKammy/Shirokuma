"""Fake HTTP only: streaming bounds, owned query identities, and uncertainty."""
from copy import deepcopy
from decimal import Decimal
import io
import json
import time
import unittest
from unittest.mock import patch
import urllib.error

from tools.local_experiment.spark_benchmark import client as C
from tools.local_experiment.iceberg_sandbox import run as SB

TOKEN = 'a' * 32
QID = '20260917_010203_00001_test'
URL = 'http://127.0.0.1:18080'
NEXT = URL + '/v1/statement/executing/' + QID + '/slug/1'


def page(rows=None, next_uri=None, query_id=QID):
    result = {'id': query_id, 'columns': [{'name': 'value', 'type': 'decimal(12,2)'}],
              'data': rows or [], 'stats': {'state': 'FINISHED' if next_uri is None else 'RUNNING',
                  'elapsedTimeMillis': 4, 'processedRows': len(rows or [])}}
    if next_uri is not None:
        result['nextUri'] = next_uri
    return result


class Response:
    status = 200
    def __init__(self, value):
        self.raw = value if type(value) is bytes else json.dumps(value).encode()
    def read(self, bound): return self.raw[:bound]
    def __enter__(self): return self
    def __exit__(self, *args): return False


class Opener:
    def __init__(self, pages): self.pages, self.calls = list(pages), []
    def open(self, request, timeout):
        self.calls.append((request, timeout))
        value = self.pages.pop(0)
        if isinstance(value, BaseException): raise value
        return Response(value)


class ClientTests(unittest.TestCase):
    def client(self, pages):
        opener = Opener(pages)
        return C.Client(URL, TOKEN, opener=opener), opener

    def test_stream_does_not_retain_rows_and_captures_query_before_consumption(self):
        client, opener = self.client([page([['1.00']], NEXT), page([['2.00']])])
        seen, observations = [], []
        client.progress = lambda: observations.append(client.receipt())
        def consume(row):
            self.assertEqual(client.records[0]['query_id'], QID)
            self.assertTrue(any(r[0]['query_id'] == QID for r in observations))
            seen.append(row)
        result = client.stream('SELECT fixed', consume, expected_columns=['value'], expected_types=['decimal(12,2)'])
        self.assertEqual(seen, [['1.00'], ['2.00']])
        self.assertNotIn('rows', result)
        self.assertNotIn('1.00', json.dumps(client.receipt()))
        self.assertEqual(result['row_count'], 2)
        self.assertEqual(result['stats']['processedRows'], 1)
        self.assertIsNone(result['stats']['cpuTimeMillis'])
        self.assertGreater(result['elapsed_ns'], 0)
        self.assertEqual([r.method for r, _ in opener.calls], ['POST', 'GET'])
        self.assertEqual(opener.calls[0][0].headers['X-trino-user'], SB.run_user(TOKEN))
        self.assertTrue(client.records[0]['response_complete'])

    def test_pending_persist_failure_sends_no_post(self):
        for count in (1, 2):
            client, opener = self.client([page()])
            calls = 0
            def fail():
                nonlocal calls
                calls += 1
                if calls == count: raise OSError('private path')
            client.progress = fail
            with self.assertRaisesRegex(C.ClientError, 'QUERY_FAILED_OR_UNKNOWN'):
                client.query('SELECT fixed')
            self.assertFalse(opener.calls)
            self.assertEqual(client.records[0]['result'], 'not-sent')
            self.assertFalse(client.records[0]['post_attempted'])

    def test_actual_trino_decimal_spacing_is_canonical_without_changing_precision(self):
        first = page([['1.00']], NEXT)
        first['columns'][0]['type'] = 'decimal(12, 2)'
        client, _ = self.client([first, page([['2.00']])])
        observed = []
        result = client.stream('SELECT fixed', observed.append,
                               expected_columns=['value'], expected_types=['decimal(12,2)'])
        self.assertEqual(result['column_types'], ['decimal(12,2)'])
        self.assertEqual(observed, [['1.00'], ['2.00']])
        for wrong in ('decimal(13, 2)', 'decimal(12, 3)', 'decimal(12,  2)', 'DECIMAL(12, 2)', 'double'):
            value = page([['1.00']]); value['columns'][0]['type'] = wrong
            client, _ = self.client([value])
            with self.subTest(wrong=wrong), self.assertRaisesRegex(C.ClientError, 'QUERY_SCHEMA_MISMATCH'):
                client.stream('SELECT fixed', lambda _: None,
                              expected_columns=['value'], expected_types=['decimal(12,2)'])

    def test_unknown_post_has_late_horizon_and_never_retries(self):
        client, opener = self.client([TimeoutError('private response')])
        before = time.monotonic()
        with self.assertRaisesRegex(C.ClientError, '^QUERY_FAILED_OR_UNKNOWN$'):
            client.query('CREATE fixed')
        record = client.records[0]
        self.assertIsNone(record['query_id'])
        self.assertEqual(record['result'], 'unknown')
        self.assertGreaterEqual(record['_not_before'], before + 122)
        with self.assertRaisesRegex(C.ClientError, 'QUERY_OUTCOME_UNRESOLVED'):
            client.query('CREATE fixed')
        self.assertEqual(len(opener.calls), 1)
        self.assertNotIn('_not_before', client.receipt()[0])

    def test_origin_and_query_identity_cannot_change(self):
        for next_uri in ('http://remote.invalid/v1/statement/executing/x/a/1',
                         NEXT.replace(QID, 'other'), NEXT+'?token=private', URL+'/arbitrary'):
            client, opener = self.client([page([], next_uri)])
            with self.subTest(next_uri=next_uri), self.assertRaises(C.ClientError):
                client.query('SELECT fixed')
            self.assertEqual(len(opener.calls), 1)
            self.assertFalse(client.records[0]['response_complete'])
        client, _ = self.client([page([], NEXT), page(query_id='other')])
        with self.assertRaisesRegex(C.ClientError, 'QUERY_ID_CHANGED'): client.query('SELECT fixed')

    def test_unknown_poll_keeps_original_query_and_interrupt_signal(self):
        error = KeyboardInterrupt()
        client, opener = self.client([page([], NEXT), error])
        def broken_save():
            if len(opener.calls) == 2: raise OSError('secondary')
        client.progress = broken_save
        with self.assertRaises(KeyboardInterrupt) as captured: client.query('SELECT fixed')
        self.assertIs(captured.exception, error)
        self.assertEqual(client.records[0]['query_id'], QID)
        self.assertEqual(client.records[0]['result'], 'interrupted')
        self.assertFalse(client.records[0]['response_complete'])

    def test_http_error_is_closed_without_message_or_body_retention(self):
        body = io.BytesIO(b'private credential')
        error = urllib.error.HTTPError(URL, 503, 'private secret', {}, body)
        client, _ = self.client([error])
        with self.assertRaisesRegex(C.ClientError, '^QUERY_FAILED_OR_UNKNOWN$'): client.query('SELECT fixed')
        self.assertTrue(body.closed)
        self.assertEqual(client.records[0]['http_status'], 503)
        self.assertNotIn('private', json.dumps(client.receipt()))

    def test_terminal_query_error_is_not_unknown_or_success(self):
        value = page(); value['stats']['state'] = 'FAILED'
        value['error'] = {'message': 'private SQL body', 'errorName': 'anything'}
        client, _ = self.client([value])
        with self.assertRaisesRegex(C.ClientError, '^TRINO_QUERY_FAILED$'): client.query('SELECT fixed')
        self.assertTrue(client.records[0]['response_complete'])
        self.assertEqual(client.records[0]['result'], 'failed')
        self.assertNotIn('private', json.dumps(client.receipt()))

    def test_bounds_and_schema_fail_without_success(self):
        for constant, limit, pages in [('MAX_ROWS', 1, [page([[1], [2]])]),
                                       ('MAX_BYTES', 1, [page()]),
                                       ('PAGE_BYTES', 8, [b' ' * 9]),
                                       ('MAX_PAGES', 1, [page([], NEXT), page()])]:
            client, _ = self.client(pages)
            with self.subTest(constant=constant), patch.object(C, constant, limit), self.assertRaises(C.ClientError):
                client.stream('SELECT fixed', lambda _: None)
            self.assertNotEqual(client.records[0]['result'], 'passed')
        client, _ = self.client([page()])
        with self.assertRaisesRegex(C.ClientError, 'QUERY_SCHEMA_MISMATCH'):
            client.stream('SELECT fixed', lambda _: None, expected_columns=['other'])

    def test_duplicate_json_and_nonfinite_or_malformed_stats_are_rejected(self):
        for value in (b'{"id":"a","id":"b"}', b'{"id":"a","x":NaN}'):
            client, _ = self.client([value])
            with self.assertRaises(C.ClientError): client.query('SELECT fixed')
        for stats in ({'state':'RUNNING'}, {'state':'FINISHED', 'processedRows':True},
                      {'state':'FINISHED', 'cpuTimeMillis':-1}):
            value = page(); value['stats'] = stats
            client, _ = self.client([value])
            with self.assertRaises(C.ClientError): client.query('SELECT fixed')
        self.assertEqual(C.decode(b'{"x":0.1}')['x'], Decimal('0.1'))

    def test_failed_consumer_is_not_success_and_completed_sql_stays_known(self):
        client, _ = self.client([page([[1]])])
        def consume(_): raise ValueError('private row')
        with self.assertRaisesRegex(C.ClientError, '^QUERY_FAILED_OR_UNKNOWN$'):
            client.stream('SELECT fixed', consume)
        self.assertTrue(client.records[0]['response_complete'])
        self.assertEqual(client.records[0]['result'], 'failed')


if __name__ == '__main__':
    unittest.main()
