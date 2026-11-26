"""Public-only ingestion contracts: no cluster, credentials, downloads, or retries."""
import copy
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.parse

from benchmarks.local_perf.capacity import Campaign, write_json
from benchmarks.local_perf.ingest import (digest, dumps, file_hash, hints_for, load_public,
                                          operation, prepare_ingest, simple, target_name)
from benchmarks.local_perf.public import PublicAdapter
from tests.local_perf_host import setUpModule, tearDownModule  # noqa: F401

CTX = dict(database_id='db', schema_id='schema', warehouse_id='LOCAL', sql_catalog='iceberg',
           sql_schema='raw', selected_role='data_engineer', binding_revision=1,
           generation_id='g1', grant_revision=1)
REF = dict(database_id='db', schema_id='schema', object_id='opaque-registry-id', incarnation=1)


class FakePublic(PublicAdapter):
    def __init__(self, case='ok'):
        super().__init__('http://127.0.0.1:8888', 'client', 'never-log-password', 'data_engineer', CTX)
        self.case, self.calls, self.ops, self.keys = case, [], {}, []
        self.posts = 0
        self.expected_save = lambda: None

    def select(self, database, schema, timeout=5):
        if self.case == 'context':
            self.context['generation_id'] = 'new'
        return self.identity()

    def request(self, method, url, deadline, body=None):
        self.safe(url)
        self.calls.append((method, urllib.parse.urlsplit(url).path, self.request_timeout))
        p = urllib.parse.urlsplit(url)
        if p.path.endswith('/objects'):
            return dict(items=[dict(physical_name='p_events')] if self.case == 'exists' else [], next_cursor=None)
        if p.path.endswith('/resolve'):
            name = urllib.parse.parse_qs(p.query)['name'][0]
            return dict(ref=REF, object=dict(physical_name=name, kind='TABLE'))
        if p.path.endswith('/preview'):
            b = json.loads(body)
            import base64
            raw = base64.b64decode(b['content_base64'])
            return dict(context=CTX, preview=dict(input_fingerprint=digest(raw), request_fingerprint='a' * 64,
                total_rows=len(raw.splitlines()), columns=[dict(name=n, type=t) for n, t in b['schema_hints'].items()]))
        if p.path.endswith('/cancel'):
            if self.case == 'timeout-cancel-fails':
                raise OSError('cancel unavailable')
            op = next(o for o in self.ops.values() if p.path.endswith('/' + o['id'] + '/cancel'))
            op['state'] = 'CANCELED'
            return dict(context=CTX, operation=dict(id=op['id'], state='CANCELED'))
        if method == 'POST':
            self.posts += 1
            self.expected_save()
            key = self.headers['Idempotency-Key']
            self.keys.append(key)
            b = json.loads(body)
            import base64
            raw = base64.b64decode(b['content_base64'])
            rows = len(raw.splitlines())
            cols = [dict(name=n, type=t) for n, t in b['schema_hints'].items()]
            op = dict(id='op' + str(self.posts), state='FINISHED', committed=True, cleanup_error=None,
                result=dict(operation_id="op" + str(self.posts), mode=b["mode"], input_length=len(raw),
                            input_fingerprint=digest(raw), accepted_rows=rows, committed_rows=rows,
                            target=REF, atomic=True, columns=cols, observed_columns=cols,
                            schema_hints=b['schema_hints'], cleanup='finished'))
            if self.case == 'zero':
                raise OSError('never-log-password')
            if self.case == 'failed' or self.case == 'partial' and self.posts == 2:
                op.update(state='FAILED', committed=False)
            if self.case == 'commit-recording':
                op['result']['observed_columns'] = None
            if self.case == 'cleanup':
                op['result']['cleanup'] = 'failed'
            if self.case.startswith('timeout'):
                op['state'] = 'ACTIVE'
            self.ops[key] = op
            if self.case == 'lost':
                raise OSError('never-log-password')
            return dict(context=CTX, operation=op)
        if p.path.endswith('/by-key'):
            q = urllib.parse.parse_qs(p.query)
            if json.loads(q['context'][0]) != CTX or q['route'][0] != 'manual-ingest':
                raise AssertionError('not frozen context/route')
            op = self.ops.get(q['key'][0])
            if op is None:
                raise OSError('missing acceptance')
            return dict(context=CTX if self.case != 'wrong-context' else dict(CTX, generation_id='wrong'),
                        recovery=dict(operation_id=op['id'], route='manual-ingest', state=op['state']))
        op = next(o for o in self.ops.values() if p.path.endswith('/' + o['id']))
        return dict(context=CTX, operation=op)


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.c = Campaign.create(self.base / 'campaign')
        self.source = self.base / 'source.parquet'
        self.source.write_bytes(b'parquet-source')

    def tearDown(self):
        self.tmp.cleanup()

    def prepared(self, chunks=2):
        root = self.c.own('prepared', 'ingest-preparation')
        parts = []
        for i in range(chunks):
            raw = (dumps(dict(id=i, amount=str(Decimal('2.10')), label='' if i == 0 else None)) + '\n').encode()
            (root / (str(i) + '.jsonl')).write_bytes(raw)
            parts.append(dict(path=str(i) + '.jsonl', sha256=digest(raw), bytes=len(raw), rows=1))
        schema = [dict(name='id', type='bigint'), dict(name='amount', type='decimal(8,2)'), dict(name='label', type='varchar')]
        write_json(root / 'input.json', dict(format=1, status='SUCCESS', prefix='p', source_dataset_id='dataset', tables=[dict(
            name='events', physical_name='p_events', schema=schema, schema_hints=hints_for(schema)[1],
            source_file=str(self.source), source_sha256=file_hash(self.source), rows=chunks, parts=parts)]))
        self.c.mark(root, 'SUCCESS')
        return root

    def prepared_v2(self):
        from benchmarks.local_perf.dataset_layout import table_files, file_identity
        prepared = self.prepared()
        dataset = self.c.own("dataset", "dataset")
        meta = json.loads((prepared / "input.json").read_text())
        files = []
        for i in range(2):
            path = dataset / (str(i) + ".parquet")
            path.write_bytes(b"parquet" + bytes([i]))
            files.append(dict(file=path.name, rows=1, file_bytes=path.stat().st_size,
                              physical_bytes=path.stat().st_blocks * 512, sha256=file_hash(path)))
        table = dict(name="events", schema=meta["tables"][0]["schema"], files=files, rows=2,
                     file_bytes=sum(f["file_bytes"] for f in files), physical_bytes=sum(f["physical_bytes"] for f in files))
        write_json(dataset / "dataset.json", dict(format=2, status="SUCCESS", dataset_id="dataset", tables=[table]))
        self.c.mark(dataset, "SUCCESS")
        records = table_files(dataset, table, 2)
        meta.update(format=2, source_manifest_file=str(dataset / "dataset.json"),
                    source_manifest_sha256=file_hash(dataset / "dataset.json"))
        meta["tables"][0].pop("source_file")
        meta["tables"][0].update(source_files=[dict(f, path=str(f["path"])) for f in records],
                                  source_sha256=file_identity(records, 2), source_bytes=table["file_bytes"])
        write_json(prepared / "input.json", meta)
        return prepared, dataset

    def test_v2_later_source_tamper_and_provenance_prevent_all_posts(self):
        prepared, dataset = self.prepared_v2()
        original = (prepared / "input.json").read_text()
        later = dataset / "1.parquet"
        raw = later.read_bytes()
        for case in ("corrupt", "missing", "provenance", "type", "manifest"):
            with self.subTest(case=case):
                later.write_bytes(raw)
                (prepared / "input.json").write_text(original)
                meta = json.loads(original)
                if case == "corrupt":
                    later.write_bytes(b"badbytes")
                elif case == "missing":
                    later.unlink()
                elif case == "provenance":
                    meta["tables"][0]["source_files"][1]["path"] = str(self.source)
                elif case == "type":
                    meta["tables"][0]["schema"][0]["type"] = "double"
                else:
                    meta["source_manifest_sha256"] = "a" * 64
                if case in ("provenance", "type", "manifest"):
                    write_json(prepared / "input.json", meta)
                adapter = FakePublic()
                with self.assertRaises(ValueError):
                    load_public(self.c, prepared, "load_" + case, adapter)
                self.assertFalse(any(method == "POST" for method, _, _ in adapter.calls))
        later.write_bytes(raw)
        (prepared / "input.json").write_text(original)
        journal = json.loads(load_public(self.c, prepared, "good", FakePublic()).read_text())
        self.assertEqual(journal["tables"][0]["source_sha256"], json.loads(original)["tables"][0]["source_sha256"])

    @unittest.skipUnless(os.environ.get("LOCAL_PERF_REAL") == "1", "explicit isolated DuckDB only")
    def test_real_v2_streaming_preserves_duplicates_across_shards(self):
        import duckdb
        root = self.c.own("tiny_v2", "dataset")
        con = duckdb.connect()
        files = []
        for i in range(2):
            path = root / (str(i) + ".parquet")
            con.execute("COPY (SELECT * FROM (VALUES (1::BIGINT, '日本語'), (1, '日本語'), (2, NULL)) t(id,label)) TO '" + str(path) + "' (FORMAT PARQUET)")
            files.append(dict(file=path.name, rows=3, file_bytes=path.stat().st_size,
                              physical_bytes=path.stat().st_blocks * 512, sha256=file_hash(path)))
        con.close()
        table = dict(name="events", schema=[dict(name="id", type="BIGINT"), dict(name="label", type="VARCHAR")],
                     rows=6, files=files, file_bytes=sum(f["file_bytes"] for f in files),
                     physical_bytes=sum(f["physical_bytes"] for f in files))
        write_json(root / "dataset.json", dict(format=2, status="SUCCESS", mode="qualification", dataset_id="tiny_v2",
                   tables=[table], timeout_s=30, peak_bytes=256*1024**2))
        self.c.mark(root, "SUCCESS")
        out = prepare_ingest(self.c, root, "prepared_v2", "p", chunk_bytes=65)
        meta = json.loads((out / "input.json").read_text())
        rows = [json.loads(line) for part in meta["tables"][0]["parts"] for line in (out / part["path"]).read_text().splitlines()]
        self.assertEqual(rows.count(dict(id=1, label="日本語")), 4)
        self.assertEqual(rows.count(dict(id=2, label=None)), 2)
        self.assertEqual(meta["format"], 2)
        self.assertNotIn("source_file", meta["tables"][0])
        self.assertEqual(len(meta["tables"][0]["source_files"]), 2)
        load_public(self.c, out, "loaded_v2", FakePublic())
        from benchmarks.local_perf.runner import DuckDBAdapter
        from benchmarks.local_perf.results import Reader, decoded
        result = self.base / "reference.jsonl"
        DuckDBAdapter(root).execute("SELECT * FROM events", result, {}, time.monotonic() + 30, lambda h: None, lambda: None)
        reader = Reader(result)
        observed = [tuple(decoded(c) for c in row) for row in reader]
        self.assertEqual(observed.count((1, "日本語")), 4)
        self.assertEqual(observed.count((2, None)), 2)
        # DuckDB would widen BIGINT + DOUBLE; each file must match instead.
        later = root / "1.parquet"
        con = duckdb.connect()
        con.execute("COPY (SELECT 1.5::DOUBLE id, '日本語' AS \"label\") TO '" + str(later) + "' (FORMAT PARQUET)")
        con.close()
        files[1].update(rows=1, file_bytes=later.stat().st_size, physical_bytes=later.stat().st_blocks * 512,
                        sha256=file_hash(later))
        table.update(rows=4, file_bytes=sum(f["file_bytes"] for f in files), physical_bytes=sum(f["physical_bytes"] for f in files))
        original = json.loads((root / "dataset.json").read_text())
        original["tables"] = [table]
        write_json(root / "dataset.json", original)
        with self.assertRaisesRegex(ValueError, "schema"):
            prepare_ingest(self.c, root, "bad_schema_v2", "p")
        with self.assertRaisesRegex(ValueError, "schema"):
            DuckDBAdapter(root).execute("SELECT * FROM events", self.base / "bad.jsonl", {},
                                       time.monotonic() + 30, lambda h: None, lambda: None)



    def test_lost_response_reconciles_unique_key_without_resubmitting(self):
        prepared = self.prepared()
        a = FakePublic('lost')
        a.headers['Idempotency-Key'] = 'restore-me'
        def saved():
            j = json.loads((self.c.root / 'load/load.json').read_text())
            p = j['operations'][-1]
            self.assertEqual(p['status'], 'INTENT')
            self.assertEqual(p['key'], a.headers['Idempotency-Key'])
            self.assertIn('request_fingerprint', p['preview'])
            self.assertEqual(len(p['body_sha256']), 64)
        a.expected_save = saved
        path = load_public(self.c, prepared, 'load', a)
        journal = json.loads(path.read_text())
        self.assertEqual(journal['status'], 'SUCCESS')
        self.assertEqual(journal['tables'][0]['committed_rows'], 2)
        self.assertEqual(a.posts, 2)
        self.assertEqual(len(set(a.keys)), 2)
        self.assertEqual(a.headers['Idempotency-Key'], 'restore-me')
        self.assertEqual(a.request_timeout, 5)
        self.assertTrue(all(t == 30 for _, _, t in a.calls))
        self.assertNotIn('never-log-password', path.read_text())
        self.assertNotIn('content_base64', path.read_text())
        with self.assertRaises(FileExistsError):
            load_public(self.c, prepared, 'load', a)

    def test_failure_unknown_cleanup_and_partial_never_continue_or_retry(self):
        for case, status, expected_posts in [('zero', 'UNKNOWN', 1), ('failed', 'FAILED', 1),
                ('commit-recording', 'FAILED', 1), ('partial', 'FAILED', 2), ('cleanup', 'FAILED', 1),
                ('wrong-context', 'FAILED', 1), ('timeout', 'FAILED', 1), ('timeout-cancel-fails', 'UNKNOWN', 1)]:
            self.tmp.cleanup()
            self.setUp()
            with self.subTest(case=case):
                a = FakePublic(case)
                prepared = self.prepared(3)
                with self.assertRaises(Exception):
                    load_public(self.c, prepared, 'load', a, timeout=.15 if case.startswith('timeout') else 5)
                j = json.loads((self.c.root / 'load/load.json').read_text())
                self.assertEqual(j['status'], status)
                self.assertEqual(a.posts, expected_posts)
                self.assertNotIn('Idempotency-Key', a.headers)
                if case == 'zero':
                    self.assertFalse(j['tables'][0]['cleanup_clean'])
                if case in ('commit-recording', 'cleanup', 'wrong-context'):
                    self.assertEqual(j['tables'][0]['committed_rows'], 1)
                if case == 'partial':
                    self.assertEqual(j['tables'][0]['committed_rows'], 1)

    def test_existing_object_and_corrupt_tail_block_before_mutation(self):
        prepared = self.prepared()
        a = FakePublic('exists')
        with self.assertRaises(ValueError):
            load_public(self.c, prepared, 'existing', a)
        self.assertEqual(a.posts, 0)
        (prepared / '1.jsonl').write_text('changed')
        with self.assertRaises(ValueError):
            load_public(self.c, prepared, 'corrupt', FakePublic())
        self.assertFalse((self.c.root / 'corrupt/load.json').read_text().find('never-log-password') >= 0)

    def test_source_mutation_and_nonpublic_adapter_block(self):
        prepared = self.prepared()
        self.source.write_bytes(b'changed')
        a = FakePublic()
        with self.assertRaises(ValueError):
            load_public(self.c, prepared, 'load', a)
        self.assertEqual(a.posts, 0)
        with self.assertRaises(ValueError):
            load_public(self.c, prepared, 'other', object())

    @unittest.skipUnless(os.environ.get('LOCAL_PERF_REAL') == '1', 'explicit isolated DuckDB only')
    def test_real_streaming_parquet_exact_values_and_source_unchanged(self):
        import duckdb
        root = self.c.own('tiny', 'dataset')
        con = duckdb.connect()
        con.execute("CREATE TABLE events AS SELECT i::BIGINT id, (i * 0.01)::DECIMAL(18,2) amount, CASE WHEN i=0 THEN '' WHEN i=1 THEN NULL ELSE '日本語' END AS \"label\", DATE '2024-02-29' AS \"day\", i%2=0 AS flag, (i * 0.125)::REAL AS ratio, TIMESTAMP '2024-02-29 01:02:03.123456' AS occurred FROM range(17) t(i)")
        schema = [dict(name=r[0], type=r[1]) for r in con.execute('DESCRIBE events').fetchall()]
        con.execute("COPY events TO '" + str(root / 'events.parquet') + "' (FORMAT PARQUET)")
        con.close()
        sha = file_hash(root / 'events.parquet')
        write_json(root / 'dataset.json', dict(format=1, status='SUCCESS', mode='qualification', dataset_id='tiny',
            tables=[dict(name='events', file='events.parquet', rows=17, schema=schema)], timeout_s=30, peak_bytes=256*1024**2))
        self.c.mark(root, 'SUCCESS')
        out = prepare_ingest(self.c, root, 'prepared', 'p', chunk_bytes=230)
        meta = json.loads((out / 'input.json').read_text())
        parts = meta['tables'][0]['parts']
        self.assertGreater(len(parts), 2)
        rows = [json.loads(line) for p in parts for line in (out / p['path']).read_text().splitlines()]
        self.assertEqual(len(rows), 17)
        self.assertEqual(rows[0]['label'], '')
        self.assertIsNone(rows[1]['label'])
        self.assertEqual(rows[16]['amount'], '0.16')
        self.assertEqual(rows[0]['day'], '2024-02-29')
        self.assertIs(rows[0]['flag'], True)
        self.assertEqual(meta['tables'][0]['schema_hints']['ratio'], 'REAL')
        self.assertEqual(meta['tables'][0]['schema_hints']['occurred'], 'TIMESTAMP(6)')
        self.assertEqual(rows[3]['ratio'], .375)
        self.assertEqual(rows[0]['occurred'], '2024-02-29T01:02:03.123456')
        parser = self.actual_timestamp_parser()
        self.assertEqual(parser(rows[0]['occurred'], 'TIMESTAMP(6)'), '2024-02-29 01:02:03.123456')
        with self.assertRaises(ValueError):
            parser(rows[0]['occurred'] + '+00:00', 'TIMESTAMP(6)')
        with self.assertRaises(ValueError):
            parser(rows[0]['occurred'], 'TIMESTAMP(3)')
        self.assertTrue(all(p['bytes'] <= 230 for p in parts))
        self.assertEqual(file_hash(root / 'events.parquet'), sha)
        # A real bound reference reads original Parquet while retaining target SQL identity.
        from benchmarks.local_perf.runner import DuckDBAdapter, run, sql_identity
        from benchmarks.local_perf.results import Reader, decoded
        pins = dict(format=1, tables=[dict(name='events', snapshot_id='123')])
        q = dict(id='q', logical_sql='SELECT sum(amount) AS amount FROM events',
                 reference_sql='SELECT sum(amount) AS amount FROM events',
                 sql='SELECT sum(amount) AS amount FROM p_events FOR VERSION AS OF 123')
        for field, hash_field in [('sql', 'sql_sha256'), ('logical_sql', 'logical_sha256'), ('reference_sql', 'reference_sha256')]:
            q[hash_field] = sql_identity(q[field])
        manifest = dict(format=1, dataset_id='tiny/snapshot/hash', source_dataset_id='tiny', snapshot_pins=pins,
                        snapshot_set_sha256=sql_identity(json.dumps(pins, sort_keys=True, separators=(',', ':'), ensure_ascii=False)), queries=[q])
        ref = run(self.c, 'reference', manifest, DuckDBAdapter(root), sql_variant='reference_sql', reference_label='source')
        rows = list(Reader(ref / 'q-0/result.jsonl'))
        self.assertEqual(decoded(rows[0][0]), Decimal('1.36'))


    def test_unknown_load_cannot_be_restarted_with_new_key(self):
        prepared = self.prepared()
        a = FakePublic('zero')
        with self.assertRaises(OSError):
            load_public(self.c, prepared, 'first', a)
        with self.assertRaises(ValueError):
            load_public(self.c, prepared, 'second', a)
        self.assertEqual(a.posts, 1)
        self.assertFalse((self.c.root / 'second').exists())

    def test_all_preflight_schema_and_empty_table_errors_are_nonmutating(self):
        prepared = self.prepared()
        path = prepared / 'input.json'
        original = json.loads(path.read_text())
        for case in ('field', 'schema', 'empty'):
            meta = copy.deepcopy(original)
            t = meta['tables'][0]
            if case == 'field':
                raw = b'{"other":1}\n'
                (prepared / '1.jsonl').write_bytes(raw)
                t['parts'][1].update(sha256=digest(raw), bytes=len(raw))
            elif case == 'schema':
                t['schema_hints']['id'] = 'VARCHAR'
            else:
                t['rows'] = 0
                t['parts'] = [dict(path='empty.jsonl', rows=0, bytes=0, sha256=digest(b''))]
                (prepared / 'empty.jsonl').write_bytes(b'')
            write_json(path, meta)
            a = FakePublic()
            with self.assertRaises(ValueError):
                load_public(self.c, prepared, 'load-' + case, a)
            self.assertEqual(a.posts, 0)


    def test_generic_source_names_get_stable_distinct_simple_targets(self):
        import re
        names = ['Events', 'events', 'Sales Data', '日本語', 'sales-data', '1thing']
        targets = [target_name('pilot', n) for n in names]
        self.assertEqual(len(set(targets)), len(names))
        self.assertTrue(all(re.fullmatch('[a-z_][a-z0-9_]*', n) for n in targets))
        self.assertEqual(targets, [target_name('pilot', n) for n in names])
        self.assertEqual(target_name('pilot', 'events'), 'pilot_events')


    def test_long_targets_keep_stable_source_identity_within_physical_limit(self):
        import re
        prefix = 'qualification'
        names = ['catalog_sales', 'catalog_returns', 'customer_address', 'customer_demographics',
                 'income_band', 'inventory', 'web_sales', 'web_returns',
                 'a' * 80 + 'x', 'a' * 80 + 'y', 'Sales Data', 'sales-data']
        targets = [target_name(prefix, n) for n in names]
        self.assertEqual(len(set(targets)), len(names))
        self.assertEqual(targets, [target_name(prefix, n) for n in names])
        for source, target in zip(names, targets):
            with self.subTest(source=source):
                self.assertRegex(target, r'^[a-z][a-z0-9_]{0,30}$')
                if source != re.sub(r'[^a-z0-9_]', '_', source.lower()).strip('_') or len(prefix + '_' + source) > 31:
                    self.assertTrue(target.endswith('_' + digest(source.encode('utf8'))[:12]))
        self.assertEqual(len(target_name('p' * 16, 'customer_demographics')), 31)
        self.assertEqual(target_name('p' * 16, 'customer_demographics'),
                         'p' * 16 + '_c_' + digest(b'customer_demographics')[:12])

    def test_prefix_boundary_and_existing_tpch_targets(self):
        prefix = 'p' * 17
        self.assertEqual(target_name(prefix, 'part'), prefix + '_part')
        for source in ('Customer', 'customer_demographics'):
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, 'no room'):
                target_name(prefix, source)
        with self.assertRaises(ValueError):
            target_name('_pilot', 'events')
        with self.assertRaisesRegex(ValueError, 'no room'):
            target_name('p' * 21, 'call_center')
        for source in ('region', 'nation', 'supplier', 'customer', 'part', 'partsupp', 'orders', 'lineitem'):
            self.assertEqual(target_name('pilot', source), 'pilot_' + source)
        self.assertEqual(simple('_' + 'a' * 126), '_' + 'a' * 126)

    def test_invalid_legacy_physical_name_blocks_all_adapter_requests(self):
        prepared = self.prepared()
        path = prepared / 'input.json'
        original = json.loads(path.read_text())
        for index, physical in enumerate(('p_customer_demographics_too_long', '_p_events', 'p_Events', 'p_other', 'p_events')):
            meta = copy.deepcopy(original)
            invalid = copy.deepcopy(meta['tables'][0])
            invalid.update(name='events' if physical == 'p_events' else 'customer_demographics',
                           physical_name=physical)
            meta['tables'].append(invalid)  # A valid first table must not start preview/create.
            write_json(path, meta)
            a = FakePublic()
            with self.subTest(physical=physical), self.assertRaises(ValueError):
                load_public(self.c, prepared, 'invalid-' + str(index), a)
            self.assertEqual(a.calls, [])
            self.assertEqual(a.posts, 0)
            self.assertEqual(json.loads(path.read_text()), meta)


    @staticmethod
    def actual_timestamp_parser():
        # Compile only the pure parser from source: no backend imports or authority.
        import ast
        import datetime
        import re
        source = Path(__file__).resolve().parents[1] / 'tools/local_experiment/ingest_types.py'
        tree = ast.parse(source.read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'timestamp_value')
        namespace = dict(re=re, datetime=datetime.datetime)
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)
        return namespace['timestamp_value']

    def test_real_hint_and_naive_timestamp_parser_contract(self):
        from benchmarks.local_perf.results import cell
        schema, hints = hints_for([dict(name='ratio', type='REAL'), dict(name='event', type='TIMESTAMP(3)')])
        self.assertEqual(hints, dict(ratio='REAL', event='TIMESTAMP(3)'))
        parser = self.actual_timestamp_parser()
        self.assertEqual(parser('2024-02-29T01:02:03.123000', hints['event']), '2024-02-29 01:02:03.123')
        with self.assertRaises(ValueError):
            cell('2024-02-29T01:02:03.123001', 'timestamp(3)')

    def test_owned_timeout_cancel_never_retries_and_preserves_commit_receipt(self):
        prepared = self.prepared()
        a = FakePublic('timeout')
        with self.assertRaises(TimeoutError):
            load_public(self.c, prepared, 'timed-out', a, timeout=.15)
        journal = json.loads((self.c.root / 'timed-out/load.json').read_text())
        plan = journal['operations'][0]
        self.assertEqual(journal['status'], 'FAILED')
        self.assertEqual(plan['error_type'], 'TimeoutError')
        self.assertEqual(plan['cancellation']['status'], 'CANCELED')
        self.assertNotIn('result', plan['cancellation']['response'])
        self.assertEqual(plan['receipt']['result']['committed_rows'], 1)
        self.assertEqual(journal['tables'][0]['committed_rows'], 1)
        self.assertEqual(a.posts, 1)
        self.assertEqual(sum(path.endswith('/cancel') for _, path, _ in a.calls), 1)


    def test_cancel_and_terminal_inspection_without_result_preserve_original_data(self):
        class SparseTerminal(FakePublic):
            def request(self, method, url, deadline, body=None):
                reply = super().request(method, url, deadline, body)
                op = reply.get('operation')
                if method == 'GET' and op and op['state'] == 'CANCELED':
                    return dict(context=CTX, operation=dict(id=op['id'], state=op['state']))
                return reply
        prepared = self.prepared()
        a = SparseTerminal('timeout')
        with self.assertRaises(TimeoutError):
            load_public(self.c, prepared, 'sparse', a, timeout=.15)
        j = json.loads((self.c.root / 'sparse/load.json').read_text())
        self.assertEqual(j['status'], 'FAILED')
        self.assertEqual(j['operations'][0]['receipt']['state'], 'CANCELED')
        self.assertTrue(j['operations'][0]['receipt']['committed'])
        self.assertEqual(j['operations'][0]['receipt']['result']['committed_rows'], 1)
        self.assertEqual(a.posts, 1)
