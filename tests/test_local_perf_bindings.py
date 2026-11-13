"""Independent SQL execution identities and value-domain ORDER contracts."""
import copy
from decimal import Decimal
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import time
import unittest

from benchmarks.local_perf.capacity import Campaign
from benchmarks.local_perf.results import Writer, compare, validate_order
from benchmarks.local_perf.runner import (DuckDBAdapter, TrinoAdapter, reconcile, result_metadata,
                                          run, sql_identity)


class Local(DuckDBAdapter):
    def __init__(self, dataset=None):
        super().__init__(dataset)
        self.sent = []

    def execute(self, sql, out, metadata, deadline, save, check):
        self.sent.append(sql)
        save(dict(id='local-test'))
        w = Writer(out, [dict(name='x', type='integer')], metadata)
        w.row([1])
        w.finish()


def bound():
    pins = dict(format=1, status='SUCCESS', tables=[dict(name='events', snapshot_id='123')])
    target, reference, logical = 'SELECT x FROM events FOR VERSION AS OF 123', 'SELECT 1 AS x', 'SELECT x FROM events'
    q = dict(id='q', sql=target, reference_sql=reference, logical_sql=logical,
             sql_sha256=sql_identity(target), reference_sha256=sql_identity(reference), logical_sha256=sql_identity(logical),
             ordered=False, order_by=[], pair_contract='exact-scalar-projection')
    return dict(format=1, dataset_id='source/snapshot/hash', source_dataset_id='source', snapshot_pins=pins,
                snapshot_set_sha256=sql_identity(json.dumps(pins, sort_keys=True, separators=(',', ':'), ensure_ascii=False)), queries=[q])


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.c = Campaign.create(self.base / 'campaign')
        self.source = self.base / 'source'
        self.source.mkdir()
        (self.source / 'dataset.json').write_text(json.dumps(dict(status='SUCCESS', dataset_id='source')))

    def local(self):
        return Local(self.source)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reference_variant_actual_sql_and_shared_pair_identity(self):
        m = bound()
        a = self.local()
        ref = run(self.c, 'reference', m, a, sql_variant='reference_sql', reference_label='source Parquet')
        attempt = json.loads((ref / 'q-0/attempt.json').read_text())
        self.assertEqual(a.sent, [m['queries'][0]['reference_sql']])
        self.assertEqual(attempt['sql'], a.sent[0])
        self.assertEqual(attempt['sql_sha256'], sql_identity(a.sent[0]))
        self.assertEqual(attempt['target_sql'], m['queries'][0]['sql'])
        self.assertNotEqual(attempt['sql_sha256'], attempt['target_sql_sha256'])
        self.assertNotIn('sql_sha256', result_metadata(m, m['queries'][0]))
        target = self.local()
        out = run(self.c, 'target', m, target, references=ref)
        self.assertEqual(target.sent, [m['queries'][0]['sql']])
        self.assertEqual(json.loads((out / 'run.json').read_text())['settings']['final_slo_pass'], False)
        with self.assertRaises(ValueError):
            reconcile(ref / 'q-0/attempt.json', a) if attempt['status'] == 'UNKNOWN' else self.external_unknown(ref, a)

    def external_unknown(self, ref, a):
        path = ref / 'q-0/attempt.json'
        attempt = json.loads(path.read_text())
        attempt['status'] = 'UNKNOWN'
        path.write_text(json.dumps(attempt))
        return reconcile(path, a)

    def test_all_hashes_and_target_select_guard_before_dispatch(self):
        for field in ('sql_sha256', 'reference_sha256', 'logical_sha256'):
            m = bound()
            m['queries'][0][field] = 'wrong'
            a = self.local()
            with self.assertRaises(ValueError):
                run(self.c, 'bad-' + field, m, a, sql_variant='reference_sql', reference_label='reference')
            self.assertEqual(a.sent, [])
        m = bound()
        m['queries'][0]['sql'] = 'DELETE FROM events'
        m['queries'][0]['sql_sha256'] = sql_identity(m['queries'][0]['sql'])
        with self.assertRaises(ValueError):
            run(self.c, 'bad-select', m, self.local(), sql_variant='reference_sql', allow_arbitrary=True, reference_label='ref')
        with self.assertRaises(ValueError):
            run(self.c, 'bad-adapter', bound(), TrinoAdapter('http://localhost:9999'), sql_variant='reference_sql', reference_label='ref')

    def test_reference_metadata_mismatch_and_frozen_audit_tampering(self):
        m = bound()
        ref = run(self.c, 'reference', m, self.local(), sql_variant='reference_sql', reference_label='ref')
        changed = copy.deepcopy(m)
        changed['queries'][0]['logical_sql'] += ' WHERE true'
        changed['queries'][0]['logical_sha256'] = sql_identity(changed['queries'][0]['logical_sql'])
        a = self.local()
        with self.assertRaises(ValueError):
            run(self.c, 'mismatch', changed, a, references=ref)
        self.assertEqual(a.sent, [])
        frozen = ref / 'queries.json'
        manifest = json.loads(frozen.read_text())
        manifest['queries'][0]['reference_sql'] = 'SELECT 2 AS x'
        frozen.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            run(self.c, 'audit', m, a, reference_label='ref')
        self.assertEqual(a.sent, [])

    def test_ordinary_metadata_shape_and_unbound_variant_refusal(self):
        m = dict(dataset_id='d')
        q = dict(id='q', sql='SELECT 1')
        self.assertEqual(result_metadata(m, q), dict(dataset_id='d', query_id='q', sql_sha256=sql_identity('SELECT 1')))
        with self.assertRaises(ValueError):
            run(self.c, 'bad', dict(format=1, dataset_id='d', queries=[q]), Local(),
                sql_variant='reference_sql', reference_label='ref')

    def stream(self, name, kind, values):
        path = self.base / name
        w = Writer(path, [dict(name='key', type=kind)], {})
        try:
            for v in values:
                w.row([v])
            w.finish()
        finally:
            w.close()
        return path

    def test_numeric_order_ties_null_placement_and_reverse(self):
        ascending = [dict(index=0, descending=False, nulls='last')]
        good = self.stream('good', 'decimal(12,2)', [Decimal('2'), Decimal('2'), Decimal('10'), None])
        validate_order(good, ascending)
        bad = self.stream('bad', 'decimal(12,2)', [Decimal('10'), Decimal('2'), None])
        with self.assertRaises(ValueError):
            validate_order(bad, ascending)
        reverse = self.stream('reverse', 'bigint', [None, 10, 2, 2])
        validate_order(reverse, [dict(index=0, descending=True, nulls='first')])
        with self.assertRaises(ValueError):
            validate_order(reverse, [dict(index=0, descending=True, nulls='last')])

    def test_invalid_order_contract_and_multiset_duplicates(self):
        path = self.stream('rows', 'integer', [1, 2, 2])
        for keys in ([dict(index=-1, descending=False, nulls='last')],
                     [dict(index=True, descending=False, nulls='last')],
                     [dict(index=0, descending=1, nulls='last')],
                     [dict(index=0, descending=False, nulls='invalid')],
                     [dict(index=0, descending=False, nulls='last')] * 2):
            with self.assertRaises(ValueError):
                validate_order(path, keys)
        other = self.stream('permuted', 'integer', [2, 1, 2])
        compare(path, other, ordered=False)
        with self.assertRaises(ValueError):
            validate_order(other, [dict(index=0, descending=False, nulls='last')])
        fewer = self.stream('fewer', 'integer', [1, 2])
        with self.assertRaises(ValueError):
            compare(path, fewer, ordered=False)
        empty = self.stream('empty', 'integer', [])
        validate_order(empty, [])
        with self.assertRaises(ValueError):
            self.stream('nan', 'double', [float('nan')])

    def test_transport_timeout_validation(self):
        for invalid in (True, 0, -1, float('nan'), float('inf'), '30'):
            with self.assertRaises(ValueError):
                TrinoAdapter('http://localhost:1', request_timeout=invalid)
        self.assertEqual(TrinoAdapter('http://localhost:1').request_timeout, 5)
        self.assertEqual(TrinoAdapter('http://localhost:1', request_timeout=30).request_timeout, 30)


    def test_source_dataset_mismatch_and_schema_contract_fail(self):
        m = bound()
        a = self.local()
        (self.source / 'dataset.json').write_text(json.dumps(dict(status='SUCCESS', dataset_id='other')))
        with self.assertRaises(ValueError):
            run(self.c, 'wrong-source', m, a, sql_variant='reference_sql', reference_label='ref')
        self.assertEqual(a.sent, [])
        (self.source / 'dataset.json').write_text(json.dumps(dict(status='SUCCESS', dataset_id='source')))
        m['queries'][0]['columns'] = [dict(name='different', type='integer')]
        with self.assertRaises(ValueError):
            run(self.c, 'wrong-schema', m, a, sql_variant='reference_sql', reference_label='ref')
        self.assertEqual(json.loads((self.c.root / 'wrong-schema/q-0/attempt.json').read_text())['status'], 'FAILED')

    def test_order_before_success_even_without_reference_comparison(self):
        class Unsorted(Local):
            def execute(self, sql, out, metadata, deadline, save, check):
                save(dict(id='local-unsorted'))
                w = Writer(out, [dict(name='x', type='integer')], metadata)
                w.row([2])
                w.row([1])
                w.finish()
        m = dict(format=1, dataset_id='d', queries=[dict(id='q', sql='SELECT 1 AS x', ordered=False,
                 order_by=[dict(index=0, descending=False, nulls='last')])])
        with self.assertRaises(ValueError):
            run(self.c, 'unsorted', m, Unsorted(), reference_label='ref')
        self.assertEqual(json.loads((self.c.root / 'unsorted/q-0/attempt.json').read_text())['status'], 'FAILED')

    def test_order_value_domains_and_capacity_callback(self):
        from datetime import date
        for n, kind, values in [('dates', 'date', [date(2024, 2, 29), date(2024, 3, 1)]),
                                 ('strings', 'varchar', ['', 'a', '日本語']),
                                 ('bools', 'boolean', [False, True]),
                                 ('float', 'double', [-1.5, 0.0, 2.5])]:
            p = self.stream(n, kind, values)
            validate_order(p, [dict(index=0, descending=False, nulls='last')])
        def deadline():
            raise TimeoutError('validation budget')
        with self.assertRaises(TimeoutError):
            validate_order(p, [], deadline)

    def test_request_timeout_reaches_connection_and_socket(self):
        class Socket:
            def __init__(self):
                self.timeouts = []
            def settimeout(self, t):
                self.timeouts.append(t)
        class Response:
            status, length = 200, 2
            def isclosed(self):
                return self.length == 0
            def read1(self, n):
                self.length = 0
                return b'{}'
        class Connection:
            def __init__(self, *args, **kwargs):
                self.timeout, self.sock = kwargs['timeout'], Socket()
            def connect(self):
                pass
            def request(self, *args):
                pass
            def getresponse(self):
                return Response()
            def close(self):
                pass
        for configured in (5, 30):
            with patch('benchmarks.local_perf.runner.http.client.HTTPConnection', side_effect=Connection) as factory:
                adapter = TrinoAdapter('http://localhost:1', request_timeout=configured)
                self.assertEqual(adapter.request('GET', adapter.url, time.monotonic() + 100), {})
                self.assertEqual(factory.call_args.kwargs['timeout'], configured)


    def test_lexicographic_secondary_key_and_ties(self):
        path = self.base / 'two-keys'
        cols = [dict(name='n', type='integer'), dict(name='s', type='varchar')]
        w = Writer(path, cols, {})
        for row in ([1, 'b'], [1, 'b'], [1, 'a'], [2, 'z']):
            w.row(row)
        w.finish()
        keys = [dict(index=0, descending=False, nulls='last'), dict(index=1, descending=True, nulls='first')]
        validate_order(path, keys)
        keys[1]['descending'] = False
        with self.assertRaises(ValueError):
            validate_order(path, keys)


    def test_report_engine_families_reference_evidence_and_missing_report(self):
        from test_local_perf_runner import ScriptedTrino
        from test_local_perf_public import ScriptedPublic
        from benchmarks.local_perf.results import Reader
        m = dict(format=1, dataset_id='d', queries=[dict(id='q', sql='SELECT 1 AS x')])
        duck = run(self.c, 'duck', m, Local(), reference_label='DuckDB reference')
        direct = run(self.c, 'direct', m, ScriptedTrino('ok'), references=duck)
        public = run(self.c, 'public', m, ScriptedPublic(), references=direct)
        d = json.loads((direct / 'run.json').read_text())
        p = json.loads((public / 'run.json').read_text())
        self.assertTrue(d['independent_cross_engine'])
        self.assertEqual(d['reference_evidence']['engine_family'], 'duckdb')
        self.assertEqual(len(d['reference_evidence']['run_sha256']), 64)
        self.assertFalse(p['independent_cross_engine'])
        self.assertEqual(p['engine_family'], 'trino')
        self.assertEqual(p['reference_evidence']['engine_family'], 'trino')
        unknown = self.base / 'unknown-reference'
        (unknown / 'q-0').mkdir(parents=True)
        import shutil
        shutil.copyfile(duck / 'q-0/result.jsonl', unknown / 'q-0/result.jsonl')
        out = run(self.c, 'unknown', m, ScriptedTrino('ok'), references=unknown)
        record = json.loads((out / 'run.json').read_text())
        self.assertFalse(record['independent_cross_engine'])
        self.assertIsNone(record['reference_evidence'])
        (unknown / 'run.json').write_text(json.dumps(dict(adapter_identity=dict(kind='unknown-engine'))))
        out = run(self.c, 'unknown-kind', m, ScriptedTrino('ok'), references=unknown)
        self.assertFalse(json.loads((out / 'run.json').read_text())['independent_cross_engine'])

    def test_cleanup_protects_successful_proof_and_operation_kinds(self):
        for kind in ('public-load', 'snapshot-pins', 'snapshot-check', 'tpch-plan', 'known-answer', 'run', 'attempt'):
            path = self.c.own(kind, kind)
            (path / 'proof.json').write_text('{}')
            self.c.mark(path, 'SUCCESS')
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.c.cleanup(kind)
            self.assertTrue((path / 'proof.json').is_file())
        for kind in ('dataset', 'comparison', 'ingest-preparation'):
            path = self.c.own(kind, kind)
            self.c.mark(path, 'SUCCESS')
            self.c.cleanup(kind)
            self.assertFalse(path.exists())

    def test_generic_table_manifest_runs_reference_with_safe_audit_path(self):
        from benchmarks.local_perf.snapshots import table_manifest
        from benchmarks.local_perf.runner import query_path
        from test_local_perf_ingest import CTX, REF
        pins = dict(format=1, status='SUCCESS', dataset_id='source', source_dataset_id='source', context=CTX,
                    tables=[dict(name='Sales / 日本語', physical_name='p_sales', ref=REF,
                        table_uuid='b69a1029-a887-4675-8159-8bc142a7ef21', snapshot_id='123',
                        columns=[dict(name='x', type='int')])])
        m = table_manifest(pins)
        out = run(self.c, 'table-ref', m, self.local(), sql_variant='reference_sql', reference_label='source')
        qid = m['queries'][0]['id']
        self.assertTrue((out / query_path(qid, 0) / 'attempt.json').is_file())
        audit = json.loads((out / query_path(qid, 0) / 'attempt.json').read_text())
        self.assertEqual(audit['query_id'], qid)
        self.assertEqual(audit['sql'], m['queries'][0]['reference_sql'])


    def test_large_order_validation_batches_checks_without_skipping_tail_or_footer(self):
        count = 60175
        path = self.stream('large-stream', 'integer', range(count))
        keys = [dict(index=0, descending=False, nulls='last')]
        for order in ([], keys):
            calls = []
            validate_order(path, order, lambda: calls.append(None))
            self.assertGreaterEqual(len(calls), 2)
            self.assertLessEqual(len(calls), count // 256 + 2)
        # The last row breaks ordering beyond hundreds of callback intervals.
        late = self.stream('late-unsorted', 'integer', list(range(count - 1)) + [-1])
        with self.assertRaisesRegex(ValueError, 'ORDER BY'):
            validate_order(late, keys)
        # Empty ORDER keys must still drain the entire typed stream and footer.
        lines = path.read_text().splitlines()
        footer = json.loads(lines[-1])
        footer['rows'] -= 1
        malformed = self.base / 'bad-footer'
        malformed.write_text('\n'.join(lines[:-1] + [json.dumps(footer)]) + '\n')
        with self.assertRaisesRegex(ValueError, 'footer'):
            validate_order(malformed, [])
        lines[-2] = json.dumps([['s', str(count - 1)]])
        malformed.write_text('\n'.join(lines) + '\n')
        with self.assertRaisesRegex(ValueError, 'typed cell'):
            validate_order(malformed, [])

    def test_order_validation_interrupts_at_start_and_first_batch(self):
        from benchmarks.local_perf import results
        path = self.stream('interrupt-stream', 'integer', range(2000))
        def stop():
            raise TimeoutError('budget exhausted')
        with patch.object(results, 'Reader', side_effect=AssertionError('stream opened before initial check')):
            with self.assertRaises(TimeoutError):
                validate_order(path, [], stop)
        calls, decoded_rows = [], []
        original = results.decoded
        def decode(value):
            decoded_rows.append(value)
            return original(value)
        def batch_check():
            calls.append(None)
            if len(calls) == 2:
                raise TimeoutError('first batch exhausted budget')
        with patch.object(results, 'decoded', side_effect=decode):
            with self.assertRaises(TimeoutError):
                validate_order(path, [dict(index=0, descending=False, nulls='last')], batch_check)
        self.assertGreater(len(decoded_rows), 0)
        self.assertLessEqual(len(decoded_rows), 256)
        self.assertEqual(len(calls), 2)
