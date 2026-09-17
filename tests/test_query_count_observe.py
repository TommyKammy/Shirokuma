"""Count observation through real bounded transport and fake HTTP only."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools.local_experiment.query_count import contract as C, run as RUN
from tools.local_experiment.query_snapshots import observe as O
from tools.local_experiment.query_read import client as Q, contract as R
import test_local_query_count as F
import test_query_snapshots_observe as S


class CountFixture(S.ObservationFixture):
    def setup(self):
        super().setup()
        self.request = F.request()
        self.metadata = [F.metadata(), F.metadata()]
        self.count = 2
        self.wire_result = None

    def clients(self, req, runtime_progress):
        labels = ['inventory', 'schema_before', 'select', 'schema_after']
        results, schemas = [], iter(self.schema_results)
        for index, label in enumerate(labels):
            if label == self.query_error_at and self.query_error is not None:
                results.append(self.query_error)
                continue
            if label == 'inventory':
                value = {'columns': ['table_name'], 'column_types': ['varchar'],
                         'rows': [[name] for name in self.inventory_names]}
            elif label.startswith('schema_'):
                value = next(schemas)
            else:
                value = self.wire_result or F.result(self.count)
            results.append(S.Response(S.page(qid=S.IDS[index], rows=value['rows'],
                columns=list(zip(value['columns'], value['column_types'])))))
        outer = self
        class ObservedOpener(S.Opener):
            def open(self, request, timeout):
                outer.events.append(labels[len(self.requests)])
                return super().open(request, timeout)
        self.opener = ObservedOpener(*results)
        self.worker = O.SnapshotClient(S.URL, S.TOKEN, request=req, _contract=C, opener=self.opener)
        self.control = Q.QueryClient(S.URL, S.TOKEN, control=True, opener=S.Opener(S.Response(S.page(
            qid=S.IDS[-1], columns=[(name, 'varchar') for name in ('query_id', 'state', 'user', 'source')],
            rows=[[S.IDS[i], 'FINISHED', *Q.identity(S.TOKEN)] for i in range(4)]))))
        class Catalog:
            def __init__(self): self.auths = self.reads = 0
            def authenticate(self):
                self.auths += 1; outer.events.append('authenticate')
            def load(self, value):
                R.normalize_request(value)
                self.reads += 1; outer.events.append('metadata')
                return deepcopy(outer.metadata[self.reads - 1])
            def receipt(self): return {'authenticated': self.auths == 1, 'reads': self.reads}
        self.catalog = Catalog()
        return self.worker, self.control, self.catalog

    def invoke(self, *, progress=None, drain_fn=None):
        return RUN.observe(self.request, S.TOKEN, progress or self.progress,
                           runtime_factory=self.runtime, drain_fn=drain_fn or self.drain)


class CountObservationTests(CountFixture, unittest.TestCase):
    def setUp(self): self.setup()

    def test_current_exact_count_once_then_real_drain_and_cleanup(self):
        journal, public = self.invoke(drain_fn=Q.drain)
        self.assertEqual(public['count'], '2')
        self.assertEqual(public['snapshot_id'], str(F.HEAD))
        self.assertTrue(journal['cleanup_verified'])
        self.assertEqual(self.events[-1], 'close')
        self.assertEqual(len(self.opener.requests), 4)
        self.assertEqual(len(self.control.receipt()['records']), 1)
        self.assertEqual([x['label'] for x in self.worker.receipt()['records']],
                         ['inventory', 'schema_before', 'select', 'schema_after'])
        data = self.opener.requests[2].data.decode()
        expected = C.count_sql(self.request, F.inventory(), F.schema(), F.window(self.request))
        self.assertEqual(data, expected)
        self.assertIn('COUNT(*)', data)
        self.assertNotIn(' LIMIT ', data)
        self.assertEqual(journal['selection'], C.receipt_projection(public))
        self.assertEqual(journal['queries']['worker'], self.worker.receipt()['records'])

    def test_zero_is_successful_exact_zero_not_missing_observation(self):
        self.count = 0
        journal, public = self.invoke()
        self.assertEqual(public['count'], '0')
        self.assertTrue(public['complete'])
        self.assertEqual(journal['state'], 'verified')
        self.assertEqual(self.events.count('select'), 1)

    def test_historical_filter_exact_sql_and_truthful_current_head(self):
        self.request = F.request(snapshot_id=str(F.OLD), column='id', literal='9007199254740993')
        journal, public = self.invoke()
        expected = C.count_sql(self.request, F.inventory(), F.schema(), F.window(self.request))
        self.assertEqual(self.opener.requests[2].data.decode(), expected)
        self.assertIn("FOR VERSION AS OF BIGINT '" + str(F.OLD) + "'", expected)
        self.assertIn("WHERE \"id\" = BIGINT '9007199254740993'", expected)
        self.assertEqual(public['snapshot_id'], str(F.OLD))
        self.assertEqual(public['current_snapshot_id'], str(F.HEAD))
        self.assertEqual(journal['identity_before']['current_snapshot_id'], str(F.HEAD))
        self.assertNotIn('literal', json.dumps(public))
        self.assertNotIn('9007199254740993', json.dumps(self.saved))
        self.assertNotIn('rows', public)
        self.assertNotIn('COUNT(*)', json.dumps(self.saved))

    def test_unsupported_filter_refuses_before_count_post(self):
        self.request = F.request(column='name', literal='1')
        with self.assertRaisesRegex(O.ObservationError, 'FILTER_TYPE_UNSUPPORTED'):
            self.invoke()
        self.assertEqual(self.events.count('select'), 0)
        self.assertEqual(len(self.opener.requests), 2)
        self.assertIn('drain', self.events)
        self.assertEqual(self.events[-1], 'close')

    def test_missing_snapshot_is_not_zero_and_has_no_count_post(self):
        self.request = F.request(snapshot_id='42')
        with self.assertRaisesRegex(O.ObservationError, 'SNAPSHOT_NOT_RETAINED'):
            self.invoke()
        self.assertEqual(len(self.opener.requests), 2)
        self.assertIsNone(self.saved[-1]['selection'])

    def test_after_metadata_or_schema_drift_discards_count_without_retry(self):
        for kind in ('metadata', 'schema'):
            with self.subTest(kind=kind):
                self.setup()
                if kind == 'metadata':
                    self.metadata[1]['metadata-location'] = self.metadata[1]['metadata-location'].replace('00001', '00002')
                else:
                    self.schema_results[1]['rows'][0][1] = 'integer'
                with self.assertRaises(O.ObservationError): self.invoke()
                self.assertEqual(self.events.count('select'), 1)
                self.assertEqual(len(self.opener.requests), 4)
                self.assertEqual(self.saved[-1]['state'], 'failed')
                self.assertTrue(self.saved[-1]['cleanup_verified'])

    def test_unknown_count_post_is_not_retried_or_followed_by_schema_read(self):
        self.query_error = OSError('private transport detail')
        with self.assertRaises(O.ObservationError): self.invoke()
        self.assertEqual(len(self.opener.requests), 3)
        self.assertNotIn('schema_after', self.events)
        self.assertEqual(self.catalog.reads, 1)
        self.assertIn('drain', self.events)
        self.assertNotIn('private transport detail', json.dumps(self.saved))

    def test_null_or_noninteger_wire_count_never_becomes_zero(self):
        for value in (None, True, '2'):
            with self.subTest(value=value):
                self.setup(); self.wire_result = F.result(value)
                with self.assertRaises(O.ObservationError): self.invoke()
                self.assertEqual(self.events.count('select'), 1)
                self.assertIsNone(self.saved[-1]['selection'])

    def test_first_cancel_survives_drain_context_and_final_save_failures(self):
        cancel = KeyboardInterrupt()
        self.query_error = cancel
        self.drain_error = OSError('drain')
        self.teardown_error = OSError('close')
        def save(value):
            self.progress(value)
            if value['state'] == 'interrupted': raise OSError('save')
        with self.assertRaises(KeyboardInterrupt) as caught: self.invoke(progress=save)
        self.assertIs(caught.exception, cancel)
        self.assertEqual(len(self.opener.requests), 3)
        self.assertIn('drain', self.events)
        self.assertEqual(self.events[-1], 'close')

    def test_unconfirmed_drain_or_runtime_cleanup_never_returns_public(self):
        for boundary in ('drain_verified', 'runtime_cleanup'):
            with self.subTest(boundary=boundary):
                self.setup(); setattr(self, boundary, False)
                with self.assertRaises(O.ObservationError): self.invoke()
                self.assertFalse(self.saved[-1]['cleanup_verified'])
                self.assertEqual(self.events.count('select'), 1)

    def test_data_sql_requires_fresh_binding_and_cannot_repeat_or_expand(self):
        self.clients(self.request, lambda _: None)
        sql = C.count_sql(self.request, F.inventory(), F.schema(), F.window(self.request))
        with self.assertRaises(Q.ClientError): self.worker.query(sql, 'select')
        self.assertEqual(len(self.opener.requests), 0)
        self.worker.query(C.discovery_sql(self.request), 'inventory')
        self.worker.query(C.schema_sql(self.request), 'schema_before')
        self.worker.bind_select(F.inventory(), F.schema(), F.window(self.request))
        with self.assertRaises(Q.ClientError): self.worker.query(sql + ' LIMIT 1', 'select')
        self.worker.query(sql, 'select')
        with self.assertRaises(Q.ClientError): self.worker.query(sql, 'select')
        with self.assertRaises(Q.ClientError): self.worker.query('SELECT * FROM secret', 'select')
        self.assertEqual(len(self.opener.requests), 3)


if __name__ == '__main__':
    unittest.main()
