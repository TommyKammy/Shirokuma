"""Offline transport and cleanup tests; no service, model or stored source I/O."""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools.local_experiment.query_diagnose import observe as O
from tools.local_experiment.query_diagnose import contract as D
from tools.local_experiment.query_read import contract as R, client as Q
from test_query_read_contract import request, inventory, identity, schema, metadata, result
from test_query_read_client import Response, Opener, page, URL, QID
from test_query_diagnose_contract import plan_result, history_result, stats_result

TOKEN = 'b' * 32
READ_TOKEN = 'a' * 32


def source():
    selection = R.receipt_projection(R.project_rows(result(), request(), schema(), identity()))
    return D.source_context(READ_TOKEN, request(), inventory(), identity(), selection, QID, 'e' * 64)


def schema_result():
    return {'columns': ['column_name', 'data_type', 'ordinal_position', 'is_nullable'],
            'column_types': ['varchar', 'varchar', 'bigint', 'varchar'],
            'rows': [[s['name'], s['type'], i, 'YES' if s['nullable'] else 'NO'] for i, s in enumerate(schema(), 1)]}


class Fixture:
    def setup(self):
        self.saved, self.events, self.closed = [], [], []
        self.failure = None
        self.failure_at = 'explain'
        self.metadata = [metadata(), metadata()]
        self.schema_results = [schema_result(), schema_result()]
        self.drain_result = {'verified': True}
        self.drain_error = None
        self.cleanup_error = None
        self.runtime_state = None
        self.runtime_cleanup = True
        self.projected = {'plan': {'status': 'observed'}, 'history': {'status': 'not-retained'},
                          'stats': {'status': 'observed'}}
        for name, key in (('project_plan', 'plan'), ('project_history', 'history'), ('project_stats', 'stats')):
            p = patch.object(D, name, side_effect=lambda raw, src, k=key: deepcopy(self.projected[k]))
            p.start(); self.addCleanup(p.stop)

    def progress(self, value):
        self.saved.append(deepcopy(value))

    @contextmanager
    def runtime(self, token, src, progress):
        self.events.append('open')
        outer = self
        class Worker:
            def __init__(self, control):
                self.token, self.control = token, control
                self.user, self.source = Q.identity(token, control)
                self.records = []
            def receipt(self):
                return {'records': deepcopy(self.records)}
            def query(self, sql, label):
                outer.events.append(label)
                self.records.append({'label': label, 'sql_sha256': R.digest(sql), 'response_complete': True})
                if label == outer.failure_at and outer.failure:
                    raise outer.failure
                if label.startswith('schema_'):
                    return deepcopy(outer.schema_results.pop(0))
                return {'rows': [['PRIVATE-RAW-PLAN-SQL-STAT']], 'query_id': QID}
        class Catalog:
            def authenticate(self): outer.events.append('authenticate')
            def load(self, request):
                outer.events.append('metadata')
                return deepcopy(outer.metadata.pop(0))
        self.worker, self.control = Worker(False), Worker(True)
        error = None
        progress({'state': 'ready', 'cleanup': {'verified': False}})
        try:
            yield self.worker, self.control, Catalog()
        except BaseException as caught:
            error = caught
            raise
        finally:
            self.closed.append('closed'); self.events.append('close')
            progress({'state': self.runtime_state or ('verified' if error is None else 'failed'),
                      'cleanup': {'verified': self.runtime_cleanup}})
            if self.cleanup_error: raise self.cleanup_error

    def drain(self, control, worker, token):
        self.events.append('drain')
        self.assertIs(control, self.control); self.assertIs(worker, self.worker)
        self.assertEqual(token, TOKEN)
        self.assertFalse(self.saved[-1]['query_drain']['verified'])
        if self.drain_error: raise self.drain_error
        return deepcopy(self.drain_result)

    def invoke(self, *, progress=None, src=None, token=TOKEN):
        return O.observe(source() if src is None else src, token, progress or self.progress,
                         runtime_factory=self.runtime, drain_fn=self.drain)


class ObservationTests(Fixture, unittest.TestCase):
    def setUp(self): self.setup()

    def test_same_identity_observations_drained_closed_before_return(self):
        observed = self.invoke()
        self.assertEqual(self.events, ['open', 'schema_before', 'authenticate', 'metadata',
                         'explain', 'history', 'stats', 'schema_after', 'metadata', 'drain', 'close'])
        self.assertEqual(observed['state'], 'verified')
        self.assertTrue(observed['cleanup_verified'])
        self.assertEqual(observed['identity_before'], source()['identity'])
        self.assertEqual(observed['identity_after'], observed['identity_before'])
        self.assertEqual(observed['history']['status'], 'not-retained')
        self.assertEqual([r['label'] for r in observed['queries']['worker']],
                         ['schema_before', 'explain', 'history', 'stats', 'schema_after'])
        self.assertNotIn('PRIVATE-RAW-PLAN-SQL-STAT', json.dumps(self.saved))
        self.assertNotIn('never-persist-credential', json.dumps(self.saved))
        self.assertNotIn('SELECT ', json.dumps(self.saved))
        self.assertEqual(observed, self.saved[-1])

    def test_source_or_same_token_invalid_is_before_runtime(self):
        for bad in ({**source(), 'query_id': 'invalid'}, {**source(), 'sql_sha256': '0'*64},
                    {**source(), 'schema_version': True},
                    {**source(), 'extra': 'SQL'}):
            with self.assertRaises(O.ObservationError): self.invoke(src=bad)
        with self.assertRaises(O.ObservationError): self.invoke(token=READ_TOKEN)
        self.assertEqual(self.events, [])

    def test_changed_before_identity_stops_diagnostics_but_drains(self):
        self.metadata[0]['metadata']['table-uuid'] = '00000000-0000-4000-8000-000000000002'
        with self.assertRaisesRegex(O.ObservationError, '^OBSERVATION_FAILED$'): self.invoke()
        self.assertNotIn('explain', self.events)
        self.assertEqual(self.events[-2:], ['drain', 'close'])
        self.assertEqual(self.saved[-1]['state'], 'failed')

    def test_changed_after_head_is_never_verified(self):
        self.metadata[1]['metadata']['current-snapshot-id'] = 1
        with self.assertRaises(O.ObservationError): self.invoke()
        self.assertIn('stats', self.events)
        self.assertIsNone(self.saved[-1]['identity_after'])
        self.assertEqual(self.saved[-1]['state'], 'failed')

    def test_schema_change_before_or_after_is_rejected(self):
        for index in (0, 1):
            self.schema_results = [schema_result(), schema_result()]
            self.metadata = [metadata(), metadata()]
            self.schema_results[index]['rows'][0][1] = 'integer'
            with self.subTest(index=index), self.assertRaises(O.ObservationError): self.invoke()

    def test_unknown_transport_does_not_retry_or_observe_later_phases(self):
        self.failure = Q.ClientError('QUERY_FAILED')
        with self.assertRaises(O.ObservationError): self.invoke()
        self.assertEqual(self.events.count('explain'), 1)
        self.assertNotIn('history', self.events)
        self.assertEqual(self.events[-2:], ['drain', 'close'])

    def test_first_cancel_preserved_across_drain_teardown_and_save(self):
        first = KeyboardInterrupt('private-first')
        self.failure = first
        self.drain_error = OSError('private-drain')
        self.cleanup_error = SystemExit('private-later')
        with self.assertRaises(KeyboardInterrupt) as caught: self.invoke()
        self.assertIs(caught.exception, first)
        self.assertEqual(self.saved[-1]['state'], 'interrupted')
        self.assertEqual(self.saved[-1]['error_code'], 'INTERRUPTED')
        self.assertFalse(self.saved[-1]['cleanup_verified'])
        self.assertNotIn('private-', json.dumps(self.saved))

    def test_first_cancel_in_drain_outranks_ordinary_query_failure(self):
        self.failure = OSError('private-query')
        first = KeyboardInterrupt()
        self.drain_error = first
        with self.assertRaises(KeyboardInterrupt) as caught: self.invoke()
        self.assertIs(caught.exception, first)

    def test_original_cancel_survives_final_safe_receipt_failure(self):
        first = KeyboardInterrupt('private-first')
        self.failure = first
        def progress(value):
            self.progress(value)
            if value['state'] == 'interrupted': raise SystemExit('private-second')
        with self.assertRaises(KeyboardInterrupt) as caught: self.invoke(progress=progress)
        self.assertIs(caught.exception, first)
        self.assertEqual(self.closed, ['closed'])

    def test_first_cancellation_while_saving_failed_query_is_preserved(self):
        self.failure = OSError('private-query')
        first = KeyboardInterrupt()
        def progress(value):
            self.progress(value)
            if 'explain' in self.events and not value['query_drain']['verified']:
                raise first
        with self.assertRaises(KeyboardInterrupt) as caught: self.invoke(progress=progress)
        self.assertIs(caught.exception, first)
        self.assertIn('drain', self.events)
        self.assertEqual(self.closed, ['closed'])

    def test_failed_or_missing_final_runtime_proof_never_passes(self):
        for state, cleanup in (('failed', True), ('ready', True), ('verified', False)):
            self.metadata = [metadata(), metadata()]
            self.schema_results = [schema_result(), schema_result()]
            self.runtime_state, self.runtime_cleanup = state, cleanup
            with self.subTest(state=state, cleanup=cleanup), self.assertRaises(O.ObservationError): self.invoke()

    def test_drain_failure_is_not_a_stale_success(self):
        self.drain_result = {'verified': False, 'errors': ['DRAIN_UNVERIFIED']}
        with self.assertRaises(O.ObservationError): self.invoke()
        self.assertFalse(self.saved[-1]['cleanup_verified'])

    def test_final_progress_failure_cannot_return_success(self):
        def progress(value):
            self.progress(value)
            if value['state'] == 'verified': raise OSError('private-save')
        with self.assertRaisesRegex(O.ObservationError, '^OBSERVATION_FAILED$'): self.invoke(progress=progress)
        self.assertEqual(self.closed, ['closed'])

    def test_pre_runtime_progress_failure_has_no_queries(self):
        with self.assertRaises(O.ObservationError):
            self.invoke(progress=lambda _: (_ for _ in ()).throw(OSError('private-save')))
        self.assertEqual(self.events, [])


class DiagnosisClientTests(unittest.TestCase):
    def client(self, *responses, **kwargs):
        self.opener = Opener(*responses)
        return O.DiagnosisClient(URL, TOKEN, source=source(), opener=self.opener, **kwargs)

    def test_compiled_statements_only_never_original_select_or_retry(self):
        client = self.client(Response(page(columns=[('Query Plan','varchar')], rows=[['plan']])))
        sqls = D.statements(source())
        original = R.select_sql(request(), inventory(), schema(), identity())
        for sql, label in ((original, 'select'), (original, 'explain'), ('EXPLAIN SELECT 1','explain'),
                           (sqls['history'], 'stats'), (sqls['stats']+';', 'stats')):
            with self.assertRaises(Q.ClientError): client.query(sql, label)
        self.assertEqual(self.opener.requests, [])
        client.query(sqls['explain'], 'explain')
        with self.assertRaises(Q.ClientError): client.query(sqls['explain'], 'explain')
        self.assertEqual(len(self.opener.requests), 1)
        self.assertEqual(self.opener.requests[0].data.decode(), sqls['explain'])

    def test_large_plan_and_history_cells_are_bounded_only_in_diagnosis(self):
        for label, maximum in (('explain', O.PLAN_BYTES), ('history', O.HISTORY_BYTES)):
            with self.subTest(label=label):
                client = self.client(Response(page(columns=[('value','varchar')], rows=[['x'*maximum]])))
                client.query(D.statements(source())[label], label)
                too_large = self.client(Response(page(columns=[('value','varchar')], rows=[['x'*(maximum+1)]])))
                with self.assertRaisesRegex(Q.ClientError, 'QUERY_CELL_TYPE_INVALID'):
                    too_large.query(D.statements(source())[label], label)
        client = self.client(Response(page(columns=[('column_name','varchar')], rows=[['x'*4097]])))
        with self.assertRaisesRegex(Q.ClientError, 'QUERY_CELL_TYPE_INVALID'):
            client.query(D.statements(source())['schema_before'], 'schema_before')

    def test_reader_default_policy_and_cell_bound_stay_unchanged(self):
        op = Opener(Response(page(columns=[('table_name','varchar')], rows=[['x'*4097]])))
        client = Q.QueryClient(URL, TOKEN, opener=op)
        with self.assertRaises(Q.ClientError): client.query(D.statements(source())['explain'], 'explain')
        self.assertEqual(op.requests, [])
        with self.assertRaisesRegex(Q.ClientError, 'QUERY_CELL_TYPE_INVALID'):
            client.query(R.discovery_sql(request()), 'inventory')

    def test_bounded_varchar_types_only_for_three_observation_statements(self):
        for label in ('explain', 'stats', 'history'):
            for length in (0, 1, 128, 2147483647):
                with self.subTest(label=label, length=length):
                    client = self.client(Response(page(columns=[('value', 'varchar(%s)' % length)], rows=[['']])))
                    observed = client.query(D.statements(source())[label], label)
                    self.assertEqual(observed['column_types'], ['varchar'])
                    self.assertTrue(client.records[0]['response_complete'])
                    self.assertEqual(len(self.opener.requests), 1)

    def test_malformed_or_out_of_range_bounded_types_are_rejected(self):
        for kind in ('varchar(-1)', 'varchar(01)', 'varchar(2147483648)', 'varchar(99999999999)',
                     'varchar(1.0)', 'varchar( 1)', 'varchar(1) ', 'varchar(１)', 'VARCHAR(1)',
                     'varchar(1)\n', 'char(1)', 'array(varchar)'):
            for label in ('explain', 'stats', 'history'):
                with self.subTest(kind=kind, label=label):
                    client = self.client(Response(page(columns=[('value', kind)], rows=[['x']])))
                    with self.assertRaises(Q.ClientError):
                        client.query(D.statements(source())[label], label)
                    self.assertFalse(client.records[0]['response_complete'])
                    self.assertEqual(len(self.opener.requests), 1)

    def test_bounded_varchar_keeps_phase_cell_limits_and_reader_types(self):
        for label, maximum in (('explain', O.PLAN_BYTES), ('stats', R.MAX_CELL_BYTES), ('history', O.HISTORY_BYTES)):
            client = self.client(Response(page(columns=[('value', 'varchar(2147483647)')], rows=[['x'*(maximum+1)]])))
            with self.subTest(label=label), self.assertRaisesRegex(Q.ClientError, 'QUERY_CELL_TYPE_INVALID'):
                client.query(D.statements(source())[label], label)
        for label in ('schema_before', 'schema_after'):
            client = self.client(Response(page(columns=[('value', 'varchar(1)')], rows=[['x']])))
            with self.subTest(label=label), self.assertRaises(Q.ClientError):
                client.query(D.statements(source())[label], label)
        client = Q.QueryClient(URL, TOKEN, opener=Opener(Response(page(
            columns=[('table_name', 'varchar(1)')], rows=[['x']]))))
        with self.assertRaises(Q.ClientError): client.query(R.discovery_sql(request()), 'inventory')

    def test_pinned_history_timestamp_types_project_present_and_missing_rows(self):
        # QuerySystemTable: TIMESTAMP_TZ_MILLIS (p=3); ToIso8601:
        # varchar(1+6+15+min(p,1)+p+6) = varchar(32), not varchar(35).
        for missing in (False, True):
            raw = history_result(source(), missing=missing)
            types = list(raw['column_types'])
            types[8:11] = ['varchar(32)'] * 3
            client = self.client(Response(page(columns=list(zip(raw['columns'], types)), rows=raw['rows'])))
            observed = client.query(D.statements(source())['history'], 'history')
            projected = D.project_history(observed, source())
            with self.subTest(missing=missing):
                self.assertEqual(observed['column_types'], list(D.HISTORY_TYPES))
                self.assertEqual(projected['status'], 'not-retained' if missing else 'observed')
                self.assertEqual(projected['started_to_end_ms'], None if missing else 1001)
                self.assertTrue(client.records[0]['response_complete'])

    def test_all_five_phases_still_enforce_utf8_byte_limits(self):
        for label in D.PHASES:
            maximum = O.PLAN_BYTES if label == 'explain' else O.HISTORY_BYTES if label == 'history' else R.MAX_CELL_BYTES
            kind = 'varchar' if label.startswith('schema_') else 'varchar(2147483647)'
            client = self.client(Response(page(columns=[('value', kind)], rows=[['日'*(maximum//3+1)]])))
            with self.subTest(label=label), self.assertRaisesRegex(Q.ClientError, 'QUERY_CELL_TYPE_INVALID'):
                client.query(D.statements(source())[label], label)

    def test_durable_intent_precedes_post_and_raw_response_is_not_journaled(self):
        captured = []
        client = self.client(Response(page(columns=[('Query Plan','varchar')], rows=[['PRIVATE-RAW-PLAN']])))
        client.progress = lambda: captured.append(deepcopy(client.receipt()))
        client.query(D.statements(source())['explain'], 'explain')
        self.assertFalse(captured[0]['records'][0]['post_attempted'])
        self.assertTrue(captured[1]['records'][0]['post_attempted'])
        self.assertEqual(captured[1]['records'][0]['result'], 'pending')
        self.assertNotIn('PRIVATE-RAW-PLAN', json.dumps(captured))
        self.assertNotIn('SELECT ', json.dumps(captured))

    def test_cancellation_and_unknown_post_never_repeated(self):
        first = KeyboardInterrupt('private-cancel')
        client = self.client(first)
        client.progress = lambda: (_ for _ in ()).throw(SystemExit('later')) if client.records and client.records[0]['result']=='interrupted' else None
        with self.assertRaises(KeyboardInterrupt) as caught: client.query(D.statements(source())['history'], 'history')
        self.assertIs(caught.exception, first)
        with self.assertRaises(Q.ClientError): client.query(D.statements(source())['history'], 'history')
        self.assertEqual(len(self.opener.requests), 1)


class RuntimeIntegrationTests(Fixture, unittest.TestCase):
    def setUp(self): self.setup()

    def test_actual_runtime_clients_drain_and_hooks_with_fake_external_services(self):
        self.doCleanups()  # This integration uses the real contract projectors.
        ids = ['20260918_010101_%05d_abc12' % i for i in range(2, 7)]
        schema_page = schema_result()
        columns = list(zip(schema_page['columns'], schema_page['column_types']))
        def response(qid, raw, *, literal_types=False, timestamp_types=False):
            types = list(raw['column_types'])
            if literal_types:
                for i, kind in enumerate(types):
                    if kind == 'varchar':
                        types[i] = 'varchar(%s)' % max((len(row[i]) for row in raw['rows'] if row[i] is not None), default=0)
            if timestamp_types:
                types[8:11] = ['varchar(32)'] * 3
            wire = page(qid=qid, columns=list(zip(raw['columns'], types)), rows=raw['rows'])
            return Response(raw=json.dumps(wire, default=float).encode())
        worker_open = Opener(
            Response(page(qid=ids[0], columns=columns, rows=schema_page['rows'])),
            response(ids[1], plan_result(source()), literal_types=True),
            response(ids[2], history_result(source()), timestamp_types=True),
            response(ids[3], stats_result(source()), literal_types=True),
            Response(page(qid=ids[4], columns=columns, rows=schema_page['rows'])))
        control_open = Opener(Response(page(qid='20260918_010101_00007_abc12',
            columns=[(name, 'varchar') for name in ('query_id', 'state', 'user', 'source')],
            rows=[[qid, 'FINISHED', *Q.identity(TOKEN)] for qid in ids])))
        outer = self
        class Catalog:
            def __init__(self, guard, *, progress):
                self.guard, self.progress = guard, progress
                self.reads = 0
                self.auth = False
            def receipt(self): return {'authenticated': self.auth, 'reads': self.reads}
            def authenticate(self): self.guard(); self.auth=True; self.progress()
            def load(self, request):
                self.guard(); self.reads += 1; self.progress()
                return metadata()
            def close(self): outer.closed.append('catalog')
        @contextmanager
        def trino():
            try: yield URL
            finally: self.closed.append('trino')
        @contextmanager
        def polaris(progress):
            progress({'state': 'ready'})
            try: yield lambda: None
            finally: self.closed.append('polaris'); progress({'state': 'verified'})
        with patch.object(Q.REFS.SESSION, 'check_context'), \
             patch.object(Q, 'trino_service_identity', return_value={'uid': 'fixed-service-uid'}), \
             patch.object(Q.REFS.SESSION, 'trino_tunnel', side_effect=trino), \
             patch.object(Q.REFS, 'polaris_tunnel', side_effect=polaris), \
             patch.object(Q, 'CatalogRead', Catalog), \
             patch.object(Q.urllib.request, 'build_opener', side_effect=[worker_open, control_open]):
            observed = O.observe(source(), TOKEN, self.progress)
        self.assertEqual(observed['state'], 'verified')
        self.assertTrue(observed['cleanup_verified'])
        self.assertEqual(observed['query_drain']['observed_query_ids'], ids)
        self.assertEqual(observed['plan']['node_count'], 3)
        self.assertEqual(observed['history']['started_to_end_ms'], 1001)
        self.assertEqual(observed['stats']['row_count_estimate'], 2)
        self.assertEqual(observed['plan']['query_id'], ids[1])
        self.assertEqual(observed['history']['query_id'], ids[2])
        self.assertEqual(observed['stats']['query_id'], ids[3])
        self.assertEqual(observed['runtime']['catalog'], {'authenticated': True, 'reads': 2})
        self.assertEqual(len(observed['queries']['worker']), 5)
        self.assertEqual(len(observed['queries']['control']), 1)
        self.assertEqual(self.closed, ['polaris', 'trino', 'catalog'])
        self.assertEqual([r.get_method() for r in worker_open.requests + control_open.requests], ['POST'] * 6)
        sent = [r.data.decode() for r in worker_open.requests]
        sqls = D.statements(source())
        self.assertEqual(sent, [sqls[label] for label in ('schema_before', 'explain', 'history', 'stats', 'schema_after')])
        self.assertNotIn(R.select_sql(request(), inventory(), schema(), identity()), sent)
        for private in ('private-plan-detail', 'private-low-value', 'private-high-value', 'SELECT '):
            self.assertNotIn(private, json.dumps(self.saved))
        evidence = D.build_evidence(source(), observed['plan'], observed['history'], observed['stats'])
        for private in ('fixture_v1', source()['query_id'], source()['read_token']):
            self.assertNotIn(private, json.dumps(evidence))


if __name__ == '__main__': unittest.main()
