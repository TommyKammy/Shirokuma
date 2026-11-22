"""Engine-free reference bundle, dataset identity, and Spark lifecycle contracts."""
import ast
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from benchmarks.local_perf import spark_reference as ref
from benchmarks.local_perf.results import Writer


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def native(names=('value',), kinds=('long',)):
    return dict(type='struct', fields=[dict(name=n, type=t, nullable=True, metadata={})
                                       for n, t in zip(names, kinds)])


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.dataset = self.root / 'dataset'
        self.dataset.mkdir()
        self.sources = {qid: 'SELECT ' + str(i) + ' AS value\n' for i, qid in enumerate(ref.EXPECTED_IDS)}
        self.hashes = {qid: digest(sql.encode()) for qid, sql in self.sources.items()}

    def write(self, path, value):
        path.write_text(json.dumps(value), encoding='utf8')

    def make_dataset(self, version=1, count=24, shards=1):
        tables = []
        for i in range(count):
            parts = []
            for j in range(shards):
                path = self.dataset / ('table_%d_part_%d.parquet' % (i, j))
                raw = ('content-%d-%d' % (i, j)).encode()
                path.write_bytes(raw)
                parts.append(dict(file=path.name, rows=i + j, file_bytes=len(raw),
                                  physical_bytes=len(raw), sha256=digest(raw)))
            table = dict(name='table_' + str(i), schema=[dict(name='value', type='bigint')])
            if version == 1:
                table.update(parts[0])
            else:
                table.update(files=parts, **{key: sum(p[key] for p in parts)
                                            for key in ('rows', 'file_bytes', 'physical_bytes')})
            tables.append(table)
        meta = dict(format=version, status='SUCCESS', suite='tpcds', dataset_id='test/generated', tables=tables)
        self.write(self.dataset / 'dataset.json', meta)
        self.write(self.dataset / 'queries.json', dict(format=1, dataset_id=meta['dataset_id'], queries=[]))
        return meta

    def make_bundle(self, rows=(1, 2), version=1):
        self.make_dataset(version=version)
        identity = ref.dataset_identity(self.dataset)
        bundle = self.root / 'reference'
        bundle.mkdir()
        columns = [dict(name='value', type='bigint')]
        queries = []
        for qid in ref.EXPECTED_IDS:
            path = bundle / (qid + '-raw.jsonl')
            writer = Writer(path, columns, ref._result_metadata(identity, ref._fingerprint(identity), qid, self.hashes))
            for value in rows:
                writer.row([value])
            writer.finish()
            queries.append(dict(id=qid, status='SUCCESS', source_sha256=self.hashes[qid],
                                raw_source_unmodified=True, native_schema=native(), canonical_columns=columns,
                                rows=len(rows), result_file=path.name, result_sha256=digest(path.read_bytes())))
        report = dict(format=1, status='SUCCESS', engine='Apache Spark', engine_version=ref.SPARK_VERSION,
                      source_release='Spark v4.0.1', source_commit=ref.SOURCE_COMMIT, inputs_unchanged=True,
                      dataset=identity, dataset_fingerprint=ref._fingerprint(identity),
                      source_hashes=self.hashes, config=ref.EXPECTED_PROFILE, planned_queries=14, max_rows=1000,
                      queries=queries)
        self.write(bundle / ref.REPORT_FILE, report)
        return bundle, identity, report

    def validate(self, bundle, identity, **kw):
        return ref.validate_reference(bundle, identity, self.hashes, **kw)

    def test_valid_full_bundle_and_every_stream(self):
        bundle, identity, _ = self.make_bundle(rows=tuple(range(513)), version=2)
        check = Mock()
        result = self.validate(bundle, identity, check=check,
                               expected_columns={q: [dict(name='value', type='bigint')] for q in ref.EXPECTED_IDS})
        self.assertEqual(set(result['results']), set(ref.EXPECTED_IDS))
        self.assertEqual(result['report_sha256'], digest((bundle / ref.REPORT_FILE).read_bytes()))
        self.assertEqual(result['results']['77a']['rows'], 513)
        self.assertGreaterEqual(check.call_count, 14 * 4)

    def test_empty_results_have_success_footer(self):
        bundle, identity, _ = self.make_bundle(rows=())
        self.assertTrue(all(q['rows'] == 0 for q in self.validate(bundle, identity)['results'].values()))

    def test_report_tampering_rejected(self):
        bundle, identity, original = self.make_bundle()
        mutations = [
            lambda r: r.update(status='FAILED'),
            lambda r: r.update(status='INCOMPLETE'),
            lambda r: r.update(format=True),
            lambda r: r.update(engine='DuckDB'),
            lambda r: r.update(engine_version='4.0.1'),
            lambda r: r.update(source_release='Spark v4.1.3'),
            lambda r: r.pop('source_commit'),
            lambda r: r.update(source_commit='0' * 40),
            lambda r: r.pop('inputs_unchanged'),
            lambda r: r.update(inputs_unchanged=False),
            lambda r: r.update(inputs_unchanged=1),
            lambda r: r.update(planned_queries=13),
            lambda r: r.update(planned_queries=True),
            lambda r: r.update(max_rows=0),
            lambda r: r.update(max_rows=True),
            lambda r: r.update(max_rows=1),
            lambda r: r['config'].pop('spark.sql.ansi.enabled'),
            lambda r: r['config'].update({'spark.sql.session.timeZone': 'Asia/Tokyo'}),
            lambda r: r['config'].update(extra='true'),
            lambda r: r['dataset'].update(dataset_id='other'),
            lambda r: r['dataset']['files'][next(iter(r['dataset']['files']))].update(rows=999),
            lambda r: r.update(dataset_fingerprint='0' * 64),
            lambda r: r['source_hashes'].update({'5a': '0' * 64}),
            lambda r: r['queries'].pop(),
            lambda r: r['queries'].append(copy.deepcopy(r['queries'][0])),
            lambda r: r['queries'][1].update(id='5a'),
            lambda r: r['queries'][0].update(id='other'),
            lambda r: r['queries'][0].update(status='FAILED'),
            lambda r: r['queries'][0].update(error={'type': 'TimeoutError'}),
            lambda r: r['queries'][0].update(raw_source_unmodified=False),
            lambda r: r['queries'][0].update(source_sha256='0' * 64),
            lambda r: r['queries'][0].update(rows=-1),
            lambda r: r['queries'][0].update(rows=True),
            lambda r: r['queries'][0].update(rows=3),
            lambda r: r['queries'][0].update(truncated=True),
            lambda r: r['queries'][0].update(result_file='../5a-raw.jsonl'),
            lambda r: r['queries'][0].update(result_file=str(bundle / '5a-raw.jsonl')),
            lambda r: r['queries'][1].update(result_file='5a-raw.jsonl'),
            lambda r: r['queries'][0].update(result_sha256='0' * 64),
            lambda r: r['queries'][0].update(canonical_columns=[dict(name='value', type='integer')]),
            lambda r: r['queries'][0]['native_schema']['fields'][0].update(type='integer'),
            lambda r: r['queries'][0]['native_schema']['fields'][0].pop('nullable'),
            lambda r: r.update(cleanup_errors=['stop failed']),
            lambda r: r.update(error={'type': 'TimeoutError'}),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                report = copy.deepcopy(original)
                mutate(report)
                self.write(bundle / ref.REPORT_FILE, report)
                with self.assertRaises((ValueError, KeyError, TypeError)):
                    self.validate(bundle, identity)

    def test_expected_columns_and_source_denominator(self):
        bundle, identity, _ = self.make_bundle()
        for expected in ({}, {q: [dict(name='value', type='integer')] for q in ref.EXPECTED_IDS}):
            with self.assertRaises(ValueError):
                self.validate(bundle, identity, expected_columns=expected)
        for hashes in ({}, dict(self.hashes, extra='0' * 64), dict(self.hashes, **{'5a': 'invalid'})):
            with self.assertRaises(ValueError):
                ref.validate_reference(bundle, identity, hashes)

    def test_result_tampering_even_with_updated_file_sha(self):
        bundle, identity, original = self.make_bundle()
        path = bundle / '5a-raw.jsonl'
        lines = path.read_text().splitlines(True)

        def header_change(key, value):
            header = json.loads(lines[0])
            header[key] = value
            return [json.dumps(header) + '\n'] + lines[1:]

        variants = [lines[:-1], lines + ['[]\n'], lines[:-1] + [lines[-1].rstrip('\n')],
                    [lines[0]] + lines[2:],
                    header_change('columns', [dict(name='wrong', type='bigint')]),
                    header_change('metadata', {}),
                    header_change('metadata', dict(json.loads(lines[0])['metadata'], extra=True)),
                    lines[:-1] + [json.dumps(dict(terminal='SUCCESS', rows=1, sha256='0' * 64)) + '\n']]
        for index, changed in enumerate(variants):
            with self.subTest(index=index):
                path.write_text(''.join(changed))
                report = copy.deepcopy(original)
                report['queries'][0]['result_sha256'] = digest(path.read_bytes())
                self.write(bundle / ref.REPORT_FILE, report)
                with self.assertRaises(ValueError):
                    self.validate(bundle, identity)
        path.unlink()
        with self.assertRaises(ValueError):
            self.validate(bundle, identity)
        (bundle / ref.REPORT_FILE).unlink()
        with self.assertRaises(FileNotFoundError):
            self.validate(bundle, identity)

    def test_identity_v1_v2_actual_bytes_and_no_paths(self):
        for version in (1, 2):
            with self.subTest(version=version):
                meta = self.make_dataset(version, count=3, shards=2 if version == 2 else 1)
                check = Mock()
                identity = ref.dataset_identity(self.dataset, check)
                self.assertEqual(identity['manifest_sha256'], digest((self.dataset / 'dataset.json').read_bytes()))
                self.assertEqual(identity['queries_manifest_sha256'], digest((self.dataset / 'queries.json').read_bytes()))
                self.assertEqual(len(identity['files']), 3 * (2 if version == 2 else 1))
                self.assertNotIn(str(self.root), json.dumps(identity))
                self.assertGreater(check.call_count, len(identity['files']))
                for name, info in identity['files'].items():
                    self.assertEqual(info['sha256'], digest((self.dataset / name).read_bytes()))
                if version == 1:
                    for table in meta['tables']:
                        table.pop('file_bytes')
                        table.pop('sha256')
                    self.write(self.dataset / 'dataset.json', meta)
                    self.assertEqual(ref.dataset_identity(self.dataset)['files'], identity['files'])

    def test_identity_rejects_metadata_missing_reused_and_mutated_files(self):
        for version in (1, 2):
            meta = self.make_dataset(version, count=2)
            mutations = [lambda m: m.update(status='FAILED'), lambda m: m.update(suite='tpch'),
                         lambda m: m.update(format=True), lambda m: m.update(format=3),
                         lambda m: m['tables'].append(copy.deepcopy(m['tables'][0])),
                         lambda m: m.update(parquet_physical_bytes=-1)]
            if version == 1:
                mutations += [lambda m: m['tables'][0].update(file_bytes=999),
                              lambda m: m['tables'][0].update(sha256='0' * 64),
                              lambda m: m['tables'][1].update(file=m['tables'][0]['file']),
                              lambda m: m['tables'][0].update(rows=-1)]
            else:
                mutations += [lambda m: m['tables'][0].update(rows=999),
                              lambda m: m['tables'][0].update(file_bytes=999),
                              lambda m: m['tables'][0]['files'][0].update(sha256='0' * 64),
                              lambda m: m['tables'][0]['files'].append(copy.deepcopy(m['tables'][0]['files'][0]))]
            for index, mutate in enumerate(mutations):
                with self.subTest(version=version, index=index):
                    changed = copy.deepcopy(meta)
                    mutate(changed)
                    self.write(self.dataset / 'dataset.json', changed)
                    with self.assertRaises(ValueError):
                        ref.dataset_identity(self.dataset)
            self.write(self.dataset / 'dataset.json', meta)
            part = meta['tables'][0] if version == 1 else meta['tables'][0]['files'][0]
            path = self.dataset / part['file']
            path.write_bytes(b'x' * part['file_bytes'])
            with self.assertRaises(ValueError):
                ref.dataset_identity(self.dataset)
            path.unlink()
            with self.assertRaises(ValueError):
                ref.dataset_identity(self.dataset)

    def test_identity_hash_checks_bounded_chunks_and_deadline(self):
        meta = self.make_dataset(count=1)
        path = self.dataset / meta['tables'][0]['file']
        path.write_bytes(b'a' * (3 * 1024 * 1024 + 1))
        meta['tables'][0].update(file_bytes=path.stat().st_size, sha256=digest(path.read_bytes()))
        self.write(self.dataset / 'dataset.json', meta)
        check = Mock()
        ref.dataset_identity(self.dataset, check)
        self.assertGreaterEqual(check.call_count, 12)
        with self.assertRaises(TimeoutError):
            ref.dataset_identity(self.dataset, Mock(side_effect=TimeoutError('deadline')))

    def test_queries_manifest_identity_changes_and_dataset_binding(self):
        self.make_dataset(count=1)
        before = ref.dataset_identity(self.dataset)
        path = self.dataset / 'queries.json'
        path.write_bytes(path.read_bytes() + b'\n')
        after = ref.dataset_identity(self.dataset)
        self.assertNotEqual(before['queries_manifest_sha256'], after['queries_manifest_sha256'])
        self.assertEqual(before['files'], after['files'])
        self.write(path, dict(dataset_id='other', queries=[]))
        with self.assertRaises(ValueError):
            ref.dataset_identity(self.dataset)

    def test_native_schema_types_names_and_no_duckdb_quantization(self):
        schema = native(('sum(a)', 'value', 'value', 'float_value', 'time', 'amount', 'count'),
                        ('long', 'string', 'integer', 'float', 'timestamp', 'decimal(38,19)', 'bigint'))
        self.assertEqual(ref._columns(schema), [dict(name=n, type=t) for n, t in
                         [('c0', 'bigint'), ('value', 'varchar'), ('column_2', 'integer'),
                          ('float_value', 'real'), ('time', 'timestamp(6)'), ('amount', 'decimal(38,19)'),
                          ('count', 'bigint')]])
        with self.assertRaises(ValueError):
            ref._columns(native(kinds=('array',)))

    def loader(self, schemas):
        module = types.ModuleType('benchmarks.local_perf.tpcds_approved_variants')
        module.IDS = ref.EXPECTED_IDS
        module.metadata = Mock(return_value=dict(input_schema_sha256=ref._fingerprint(schemas), queries=[]))
        module.read_sources = Mock(side_effect=lambda directory, check: (check(), dict(self.sources))[1])
        return module

    def fake_spark(self, meta, values=(1, 2)):
        schema = Mock()
        schema.jsonValue.return_value = native()
        frame = Mock(schema=schema)
        frame.toLocalIterator.side_effect = lambda: iter([(v,) for v in values])
        session = Mock(version=ref.SPARK_VERSION)
        session.sparkContext.getLocalProperty.return_value = None
        session.conf.get.side_effect = lambda key: ref.EXPECTED_PROFILE[key]
        session.sql.return_value = frame
        by_name = {p['file']: t for t in meta['tables'] for p in
                   ([t] if meta['format'] == 1 else t['files'])}

        def parquet(path):
            part_name = Path(path).name
            table = by_name[part_name]
            source_frame = Mock(schema=schema)
            parts = [table] if meta['format'] == 1 else table['files']
            source_frame.count.return_value = next(p['rows'] for p in parts if p['file'] == part_name)
            source_frame.union.return_value = source_frame
            return source_frame

        session.read.option.return_value.parquet.side_effect = parquet
        caller = Mock()
        caller.sparkContext = session.sparkContext
        caller.newSession.return_value = session
        return caller, session

    def run_export(self, meta, **kw):
        schemas = {t['name']: t['schema'] for t in meta['tables']}
        provider = patch('benchmarks.local_perf.tpcds_sql.table_schemas', return_value=schemas)
        provider.start()
        self.addCleanup(provider.stop)
        loader = self.loader(schemas)
        caller, session = self.fake_spark(meta)
        output = self.root / 'export'
        return loader, caller, session, output

    def mock_sdk(self, session, active_context=None):
        builder = Mock()
        builder.appName.return_value = builder
        builder.config.return_value = builder
        builder.getOrCreate.return_value = session
        sdk = types.ModuleType('pyspark')
        sdk.SparkContext = types.SimpleNamespace(_active_spark_context=active_context)
        sql = types.ModuleType('pyspark.sql')
        sql.SparkSession = types.SimpleNamespace(builder=builder)
        return {'pyspark': sdk, 'pyspark.sql': sql}, builder

    def test_actual_approved_metadata_uses_shared_schema_fingerprint(self):
        from benchmarks.local_perf import tpcds_approved_variants as approved
        from benchmarks.local_perf import tpcds_sql as ds

        doc = approved.metadata()
        schemas = ds.table_schemas()
        self.assertNotIn('table_schemas', doc)
        self.assertEqual(len(schemas), 24)
        self.assertEqual(doc['input_schema_sha256'], ref._fingerprint(schemas))
        self.assertEqual(ref._input_schemas(doc), schemas)
        with self.assertRaisesRegex(ValueError, 'schema fingerprint mismatch'):
            ref._input_schemas(dict(doc, input_schema_sha256='0' * 64))
        changed = copy.deepcopy(schemas)
        changed[next(iter(changed))][0]['type'] = 'varchar'
        with patch.object(ds, 'table_schemas', return_value=changed):
            with self.assertRaisesRegex(ValueError, 'schema fingerprint mismatch'):
                ref._input_schemas(doc)

    def test_export_rejects_repository_root_and_child_before_mkdir(self):
        repo = Path(ref.__file__).resolve().parents[2]
        child = repo / '.spark-reference-output-must-not-exist'
        self.assertFalse(child.exists())
        with patch.object(Path, 'mkdir') as mkdir:
            for output in (repo, child):
                with self.subTest(output=output), self.assertRaisesRegex(ValueError, 'outside repository'):
                    ref.export_reference(self.dataset, self.root / 'sql', output)
            mkdir.assert_not_called()
        self.assertFalse(child.exists())

    def test_implicit_session_rejects_existing_context_without_touching_it(self):
        meta = self.make_dataset()
        loader, caller, session, output = self.run_export(meta)
        active = Mock()
        modules, builder = self.mock_sdk(session, active)
        with patch.dict(sys.modules, dict(modules, **{loader.__name__: loader})):
            with self.assertRaisesRegex(ValueError, 'explicit spark argument'):
                ref.export_reference(self.dataset, self.root / 'sql', output)
        builder.appName.assert_not_called()
        builder.getOrCreate.assert_not_called()
        active.stop.assert_not_called()
        session.stop.assert_not_called()
        self.assertEqual(active.mock_calls, [])
        report = json.loads((output / ref.REPORT_FILE).read_text())
        self.assertEqual(report['status'], 'FAILED')
        self.assertFalse(list(output.glob('.spark-scratch-*')))

    def test_own_session_configures_scratch_before_creating_context(self):
        meta = self.make_dataset()
        loader, caller, session, output = self.run_export(meta)
        modules, builder = self.mock_sdk(session)
        owned_paths = []

        def create():
            settings = dict(c.args for c in builder.config.call_args_list)
            self.assertEqual(settings['spark.sql.catalogImplementation'], 'in-memory')
            local = Path(settings['spark.local.dir'])
            warehouse = Path(settings['spark.sql.warehouse.dir'])
            self.assertEqual(local.name, 'local')
            self.assertEqual(warehouse.name, 'warehouse')
            self.assertEqual(local.parent, warehouse.parent)
            self.assertEqual(local.parent.parent, output)
            for path in (local, warehouse):
                path.mkdir()
                (path / 'owned-file').write_text('temporary')
                owned_paths.append(path)
            return session

        builder.getOrCreate.side_effect = create
        with patch.dict(sys.modules, dict(modules, **{loader.__name__: loader})), patch.object(ref, '_plan', return_value={}):
            ref.export_reference(self.dataset, self.root / 'sql', output)
        builder.getOrCreate.assert_called_once()
        session.stop.assert_called_once()
        self.assertTrue(all(not path.exists() for path in owned_paths))
        self.assertEqual(self.validate(output, ref.dataset_identity(self.dataset))['report']['status'], 'SUCCESS')

    def test_explicit_session_preserves_caller_scratch_and_context(self):
        meta = self.make_dataset()
        loader, caller, session, output = self.run_export(meta)
        caller.sparkContext = session.sparkContext
        scratch = self.root / 'caller-scratch'
        scratch.mkdir()
        sentinel = scratch / 'keep'
        sentinel.write_text('caller data')
        caller.sparkContext.getConf.return_value.get.return_value = str(scratch)
        modules, builder = self.mock_sdk(session, caller.sparkContext)
        with patch.dict(sys.modules, dict(modules, **{loader.__name__: loader})), patch.object(ref, '_plan', return_value={}):
            ref.export_reference(self.dataset, self.root / 'sql', output, spark=caller)
        caller.newSession.assert_called_once()
        caller.stop.assert_not_called()
        session.stop.assert_not_called()
        caller.sparkContext.stop.assert_not_called()
        caller.conf.set.assert_not_called()
        builder.getOrCreate.assert_not_called()
        self.assertEqual(dict(c.args for c in session.conf.set.call_args_list), ref.EXPECTED_PROFILE)
        self.assertEqual(sentinel.read_text(), 'caller data')

    def test_export_unchanged_sql_private_session_profile_and_complete_bundle(self):
        meta = self.make_dataset(version=2, shards=2)
        loader, caller, session, output = self.run_export(meta)
        with patch.dict(sys.modules, {loader.__name__: loader}), patch.object(ref, '_plan', return_value={}):
            self.assertEqual(ref.export_reference(self.dataset, self.root / 'sql', output, spark=caller,
                                                  image_receipt={'digest': 'informational'}), output)
        self.assertEqual([c.args[0] for c in session.sql.call_args_list], list(self.sources.values()))
        self.assertEqual(session.conf.set.call_count, 8)
        self.assertEqual(session.conf.get.call_count, 8)
        self.assertTrue(all(len(c.args) == 1 for c in session.conf.get.call_args_list))
        session.stop.assert_not_called()
        caller.stop.assert_not_called()
        self.assertEqual(loader.read_sources.call_count, 2)
        self.assertFalse(list(output.glob('.spark-scratch-*')))
        report = self.validate(output, ref.dataset_identity(self.dataset))['report']
        self.assertTrue(report['inputs_unchanged'])
        self.assertEqual(report['image_receipt']['provenance'], 'caller supplied; informational')
        self.assertEqual(session.sparkContext.setLocalProperty.call_count, (24 + 14) * 3)

    def test_export_source_mutation_after_known_results_keeps_failed_report_and_streams(self):
        meta = self.make_dataset()
        loader, caller, session, output = self.run_export(meta)
        loader.read_sources.side_effect = [dict(self.sources), dict(self.sources, **{'5a': 'SELECT 999'})]
        with patch.dict(sys.modules, {loader.__name__: loader}), patch.object(ref, '_plan', return_value={}):
            with self.assertRaisesRegex(ValueError, 'changed'):
                ref.export_reference(self.dataset, self.root / 'sql', output, spark=caller)
        report = json.loads((output / ref.REPORT_FILE).read_text())
        self.assertEqual(report['status'], 'FAILED')
        self.assertEqual(len(report['queries']), 14)
        self.assertTrue(all(q['status'] == 'SUCCESS' for q in report['queries']))
        self.assertEqual(len(list(output.glob('*-raw.jsonl'))), 14)

    def test_export_max_rows_preserves_partial_without_footer(self):
        meta = self.make_dataset()
        loader, caller, session, output = self.run_export(meta)
        with patch.dict(sys.modules, {loader.__name__: loader}), patch.object(ref, '_plan', return_value={}):
            with self.assertRaisesRegex(ValueError, 'max_rows'):
                ref.export_reference(self.dataset, self.root / 'sql', output, spark=caller, max_rows=1)
        report = json.loads((output / ref.REPORT_FILE).read_text())
        self.assertEqual(report['status'], 'FAILED')
        self.assertEqual(report['queries'][0]['status'], 'FAILED')
        self.assertNotIn('terminal', (output / '5a-raw.jsonl').read_text())
        session.stop.assert_not_called()

    def test_export_does_not_overwrite_existing_output_or_sources(self):
        self.make_dataset()
        output = self.root / 'existing'
        output.mkdir()
        marker = output / 'marker'
        marker.write_text('keep')
        with self.assertRaises(FileExistsError):
            ref.export_reference(self.dataset, self.root / 'sql', output)
        self.assertEqual(marker.read_text(), 'keep')
        for output in (self.dataset / 'nested-output', self.root / 'sql' / 'nested-output'):
            with self.assertRaisesRegex(ValueError, 'outside input'):
                ref.export_reference(self.dataset, self.root / 'sql', output)
            self.assertFalse(output.exists())

    def test_export_dataset_mutation_after_queries_keeps_known_results(self):
        meta = self.make_dataset()
        loader, caller, session, output = self.run_export(meta)

        def sources(directory, check):
            if loader.read_sources.call_count == 2:
                path = self.dataset / 'queries.json'
                path.write_bytes(path.read_bytes() + b'\n')
            return dict(self.sources)

        loader.read_sources.side_effect = sources
        with patch.dict(sys.modules, {loader.__name__: loader}), patch.object(ref, '_plan', return_value={}):
            with self.assertRaisesRegex(ValueError, 'changed'):
                ref.export_reference(self.dataset, self.root / 'sql', output, spark=caller)
        report = json.loads((output / ref.REPORT_FILE).read_text())
        self.assertEqual(report['status'], 'FAILED')
        self.assertEqual(len(report['queries']), 14)

    def test_export_per_query_post_action_deadline(self):
        meta = self.make_dataset()
        loader, caller, session, output = self.run_export(meta)
        frame = session.sql.return_value
        clock = [0.0]

        def sql(text):
            clock[0] = 3.0
            return frame

        session.sql.side_effect = sql
        with patch.dict(sys.modules, {loader.__name__: loader}), patch.object(ref.time, 'monotonic', side_effect=lambda: clock[0]):
            with self.assertRaisesRegex(TimeoutError, 'action deadline'):
                ref.export_reference(self.dataset, self.root / 'sql', output, spark=caller, query_timeout=2, timeout=20)
        self.assertEqual(json.loads((output / ref.REPORT_FILE).read_text())['status'], 'FAILED')
        frame.toLocalIterator.assert_not_called()

    def test_export_source_hash_deadline_before_sdk(self):
        meta = self.make_dataset()
        loader, caller, session, output = self.run_export(meta)
        clock = [0.0]

        def sources(directory, check):
            clock[0] = 20.0
            check()

        loader.read_sources.side_effect = sources
        with patch.dict(sys.modules, {loader.__name__: loader}), patch.object(ref.time, 'monotonic', side_effect=lambda: clock[0]):
            with self.assertRaises(TimeoutError):
                ref.export_reference(self.dataset, self.root / 'sql', output, spark=caller, timeout=10)
        caller.newSession.assert_not_called()
        self.assertEqual(json.loads((output / ref.REPORT_FILE).read_text())['status'], 'FAILED')

    def test_export_version_profile_schema_count_failures(self):
        meta = self.make_dataset()
        for scenario in ('version', 'profile', 'schema', 'count'):
            loader, caller, session, _ = self.run_export(meta)
            output = self.root / scenario
            if scenario == 'version':
                session.version = '4.0.1'
            elif scenario == 'profile':
                session.conf.get.side_effect = lambda key: 'wrong'
            elif scenario == 'schema':
                session.read.option.return_value.parquet.return_value = Mock()
                session.read.option.return_value.parquet.side_effect = lambda path: Mock(
                    schema=Mock(jsonValue=Mock(return_value=native(kinds=('integer',)))))
            else:
                session.read.option.return_value.parquet.side_effect = lambda path: Mock(
                    schema=Mock(jsonValue=Mock(return_value=native())), count=Mock(return_value=999))
            with self.subTest(scenario=scenario), patch.dict(sys.modules, {loader.__name__: loader}):
                with self.assertRaises(ValueError):
                    ref.export_reference(self.dataset, self.root / 'sql', output, spark=caller)
                self.assertEqual(json.loads((output / ref.REPORT_FILE).read_text())['status'], 'FAILED')
                session.sql.assert_not_called()

    def test_own_session_stop_cleanup_failure_prevents_success(self):
        meta = self.make_dataset()
        loader, caller, session, output = self.run_export(meta)
        session.stop.side_effect = RuntimeError('stop failed')
        modules, builder = self.mock_sdk(session)
        with patch.dict(sys.modules, dict(modules, **{loader.__name__: loader})), patch.object(ref, '_plan', return_value={}):
            with self.assertRaisesRegex(RuntimeError, 'cleanup'):
                ref.export_reference(self.dataset, self.root / 'sql', output)
        session.stop.assert_called_once()
        report = json.loads((output / ref.REPORT_FILE).read_text())
        self.assertEqual(report['status'], 'FAILED')
        self.assertIn('stop failed', report['cleanup_errors'])

    def test_cleanup_keeps_original_query_failure(self):
        meta = self.make_dataset()
        loader, caller, session, output = self.run_export(meta)
        session.sql.side_effect = ValueError('original query failure')
        with patch.dict(sys.modules, {loader.__name__: loader}), patch.object(ref.shutil, 'rmtree', side_effect=OSError('cleanup failed')):
            with self.assertRaisesRegex(ValueError, 'original query failure'):
                ref.export_reference(self.dataset, self.root / 'sql', output, spark=caller)
        report = json.loads((output / ref.REPORT_FILE).read_text())
        self.assertEqual(report['error']['type'], 'ValueError')
        self.assertIn('cleanup failed', report['cleanup_errors'])

    def test_explicit_session_restores_caller_job_properties_on_success_and_failure(self):
        originals = {'spark.jobGroup.id': 'caller-group',
                     'spark.job.description': 'caller description',
                     'spark.job.interruptOnCancel': 'false'}
        for fail in (False, True):
            with self.subTest(fail=fail):
                meta = self.make_dataset()
                loader, caller, session, output = self.run_export(meta)
                output = output.with_name('export-failure' if fail else 'export-success')
                self.assertIs(caller.newSession.return_value.sparkContext, caller.sparkContext)
                properties = dict(originals)
                context = caller.sparkContext
                context.getLocalProperty.side_effect = properties.get
                context.setLocalProperty.side_effect = properties.__setitem__

                def set_group(group, description, interruptOnCancel):
                    properties.update({'spark.jobGroup.id': group,
                                       'spark.job.description': description,
                                       'spark.job.interruptOnCancel': str(interruptOnCancel).lower()})

                context.setJobGroup.side_effect = set_group
                failure = ValueError('caller query failed')
                if fail:
                    session.sql.side_effect = failure
                with patch.dict(sys.modules, {loader.__name__: loader}), patch.object(ref, '_plan', return_value={}):
                    if fail:
                        with self.assertRaises(ValueError) as raised:
                            ref.export_reference(self.dataset, self.root / 'sql', output, spark=caller)
                        self.assertIs(raised.exception, failure)
                    else:
                        ref.export_reference(self.dataset, self.root / 'sql', output, spark=caller)
                self.assertEqual(properties, originals)
                restorations = [c.args for c in context.setLocalProperty.call_args_list]
                self.assertEqual(restorations, list(originals.items()) * context.setJobGroup.call_count)
                self.assertGreater(context.setJobGroup.call_count, 0)
                caller.stop.assert_not_called()
                session.stop.assert_not_called()

    def test_job_restores_unset_local_properties_on_success(self):
        session = Mock()
        session.sparkContext.getLocalProperty.return_value = None
        with ref._job(session, ref.time.monotonic() + 60, lambda: None):
            pass
        self.assertEqual([c.args for c in session.sparkContext.setLocalProperty.call_args_list],
                         [(key, None) for key in ('spark.jobGroup.id', 'spark.job.description', 'spark.job.interruptOnCancel')])

    def test_job_property_snapshot_failure_does_not_mutate_caller(self):
        session = Mock()
        failure = RuntimeError('cannot read caller properties')
        session.sparkContext.getLocalProperty.side_effect = ['caller-group', failure]
        with patch.object(ref.threading, 'Timer') as timer:
            with self.assertRaises(RuntimeError) as raised:
                with ref._job(session, ref.time.monotonic() + 60, lambda: None):
                    self.fail('job started without a complete caller snapshot')
        self.assertIs(raised.exception, failure)
        session.sparkContext.setJobGroup.assert_not_called()
        session.sparkContext.setLocalProperty.assert_not_called()
        session.sparkContext.cancelJobGroup.assert_not_called()
        timer.assert_not_called()

    def test_job_partial_group_failure_restores_exact_caller_properties(self):
        originals = {'spark.jobGroup.id': 'caller-group',
                     'spark.job.description': None,
                     'spark.job.interruptOnCancel': 'false'}
        properties = dict(originals)
        session = Mock()
        context = session.sparkContext
        context.getLocalProperty.side_effect = properties.get
        context.setLocalProperty.side_effect = properties.__setitem__
        failure = RuntimeError('partial group mutation')

        def set_group(group, description, interruptOnCancel):
            properties['spark.jobGroup.id'] = group
            raise failure

        context.setJobGroup.side_effect = set_group
        with patch.object(ref.threading, 'Timer') as timer:
            with self.assertRaises(RuntimeError) as raised:
                with ref._job(session, ref.time.monotonic() + 60, lambda: None):
                    self.fail('job started after group setup failed')
        self.assertIs(raised.exception, failure)
        self.assertEqual(properties, originals)
        self.assertEqual([c.args for c in context.setLocalProperty.call_args_list],
                         list(originals.items()))
        timer.assert_not_called()

    def test_job_timer_start_failure_restores_properties_without_join(self):
        originals = {'spark.jobGroup.id': None,
                     'spark.job.description': 'caller description',
                     'spark.job.interruptOnCancel': 'false'}
        for cleanup_fails in (False, True):
            with self.subTest(cleanup_fails=cleanup_fails):
                properties = dict(originals)
                session = Mock()
                context = session.sparkContext
                context.getLocalProperty.side_effect = properties.get
                context.setLocalProperty.side_effect = properties.__setitem__

                def set_group(group, description, interruptOnCancel):
                    properties.update({'spark.jobGroup.id': group,
                                       'spark.job.description': description,
                                       'spark.job.interruptOnCancel': str(interruptOnCancel).lower()})

                context.setJobGroup.side_effect = set_group
                timer = Mock()
                failure = RuntimeError('timer start failed')
                timer.start.side_effect = failure
                if cleanup_fails:
                    timer.cancel.side_effect = RuntimeError('timer cancel failed')
                with patch.object(ref.threading, 'Timer', return_value=timer):
                    with self.assertRaises(RuntimeError) as raised:
                        with ref._job(session, ref.time.monotonic() + 60, lambda: None):
                            self.fail('job started after timer setup failed')
                self.assertIs(raised.exception, failure)
                self.assertEqual(properties, originals)
                self.assertEqual([c.args for c in context.setLocalProperty.call_args_list],
                                 list(originals.items()))
                timer.cancel.assert_called_once()
                timer.join.assert_not_called()
                timer.is_alive.assert_not_called()
                if cleanup_fails:
                    self.assertEqual(failure.spark_cleanup_errors, ['timer cancel failed'])

    def test_job_timer_cleanup_errors_do_not_prevent_property_restoration(self):
        session = Mock()
        originals = {'spark.jobGroup.id': 'caller-group',
                     'spark.job.description': None,
                     'spark.job.interruptOnCancel': 'false'}
        session.sparkContext.getLocalProperty.side_effect = originals.get
        timer = Mock()
        timer.cancel.side_effect = RuntimeError('timer cancel failed')
        timer.join.side_effect = RuntimeError('timer join failed')
        timer.is_alive.side_effect = RuntimeError('timer status failed')
        failure = ValueError('job failed')
        with patch.object(ref.threading, 'Timer', return_value=timer):
            with self.assertRaises(ValueError) as raised:
                with ref._job(session, ref.time.monotonic() + 60, lambda: None):
                    raise failure
        self.assertIs(raised.exception, failure)
        self.assertEqual(failure.spark_cleanup_errors,
                         ['timer cancel failed', 'timer join failed', 'timer status failed'])
        self.assertEqual([c.args for c in session.sparkContext.setLocalProperty.call_args_list],
                         list(originals.items()))
        timer.join.assert_called_once_with(timeout=1)

    def test_job_timeout_cancels_group_and_clears_standard_local_properties(self):
        session = Mock()
        session.sparkContext.getLocalProperty.return_value = None

        class ImmediateTimer:
            def __init__(self, delay, callback):
                self.callback = callback
            def start(self):
                self.callback()
            def cancel(self):
                pass
            def join(self, timeout):
                pass
            def is_alive(self):
                return False

        with patch.object(ref.threading, 'Timer', ImmediateTimer):
            with self.assertRaises(TimeoutError):
                with ref._job(session, ref.time.monotonic() + 1, lambda: None):
                    pass
        session.sparkContext.cancelJobGroup.assert_called_once()
        self.assertEqual([c.args for c in session.sparkContext.setLocalProperty.call_args_list],
                         [(key, None) for key in ('spark.jobGroup.id', 'spark.job.description', 'spark.job.interruptOnCancel')])

    def test_still_running_cancellation_prevents_job_success(self):
        timer = Mock()
        timer.is_alive.return_value = True
        session = Mock()
        session.sparkContext.getLocalProperty.return_value = None
        with patch.object(ref.threading, 'Timer', return_value=timer):
            with self.assertRaisesRegex(RuntimeError, 'cancellation thread still running'):
                with ref._job(session, ref.time.monotonic() + 60, lambda: None):
                    pass
        timer.join.assert_called_once_with(timeout=1)
        timer.cancel.assert_called_once()

    def test_still_running_cancellation_reports_cleanup_without_masking_timeout(self):
        meta = self.make_dataset()
        loader, caller, session, output = self.run_export(meta)
        timer = Mock()
        timer.is_alive.return_value = True

        def start_timer(delay, callback):
            timer.start.side_effect = callback
            return timer

        with patch.dict(sys.modules, {loader.__name__: loader}), patch.object(ref.threading, 'Timer', side_effect=start_timer):
            with self.assertRaises(TimeoutError) as raised:
                ref.export_reference(self.dataset, self.root / 'sql', output, spark=caller)
        timer.join.assert_called_once_with(timeout=1)
        session.sparkContext.cancelJobGroup.assert_called_once()
        self.assertIn('still running', raised.exception.spark_cleanup_errors[0])
        report = json.loads((output / ref.REPORT_FILE).read_text())
        self.assertEqual(report['status'], 'FAILED')
        self.assertEqual(report['error']['type'], 'TimeoutError')
        self.assertIn('still running', report['cleanup_errors'][0])
        session.stop.assert_not_called()

    def test_job_checks_per_action_deadline_inside_python_work(self):
        clock = [0.0]
        session = Mock()
        session.sparkContext.getLocalProperty.return_value = None
        with patch.object(ref.time, 'monotonic', side_effect=lambda: clock[0]):
            with ref._job(session, 5.0, lambda: None) as check:
                clock[0] = 6.0
                with self.assertRaises(TimeoutError):
                    check()
                clock[0] = 0.0

    def test_plan_preserves_native_expression_and_immediate_child_types(self):
        class Seq:
            def __init__(self, values):
                self.values = values
            def size(self):
                return len(self.values)
            def apply(self, index):
                return self.values[index]

        def expr(kind, sql, datatype, children=()):
            obj = Mock()
            obj.getClass.return_value.getSimpleName.return_value = kind
            obj.sql.return_value = sql
            obj.dataType.return_value.catalogString.return_value = datatype
            obj.children.return_value = Seq(children)
            return obj

        child = expr('AttributeReference', 'amount', 'decimal(7,2)')
        nodes = [expr(kind, kind + '(amount)', 'decimal(11,6)', [child])
                 for kind in ('Average', 'Divide', 'Cast', 'Sum', 'Coalesce', 'CaseWhen', 'SortOrder')]
        nodes[-1].direction.return_value.sql.return_value = 'DESC'
        nodes[-1].nullOrdering.return_value.sql.return_value = 'NULLS LAST'
        plan = Mock()
        plan.toString.return_value = 'Sort [amount DESC NULLS LAST]'
        plan.expressions.return_value = Seq(nodes)
        plan.children.return_value = Seq([])
        frame = Mock()
        frame._jdf.queryExecution.return_value.analyzed.return_value = plan
        result = ref._plan(frame, lambda: None)
        self.assertEqual(result['analyzed_plan'], plan.toString())
        self.assertEqual(len(result['expressions']), 7)
        self.assertEqual(result['expressions'][0]['children'], [dict(sql='amount', dataType='decimal(7,2)')])
        self.assertEqual(result['expressions'][-1]['direction'], 'DESC')
        self.assertEqual(result['expressions'][-1]['null_ordering'], 'NULLS LAST')
        plan.toString.return_value = 'x' * 65537
        with self.assertRaisesRegex(ValueError, 'metadata limit'):
            ref._plan(frame, lambda: None)

    def test_validator_initial_final_and_row_deadlines(self):
        bundle, identity, _ = self.make_bundle(rows=tuple(range(513)))
        check = Mock()
        self.validate(bundle, identity, check=check)
        calls = check.call_count
        for at in (1, calls, 12):
            with self.subTest(at=at):
                counter = [0]

                def stop():
                    counter[0] += 1
                    if counter[0] == at:
                        raise TimeoutError('validation deadline')

                with self.assertRaises(TimeoutError):
                    self.validate(bundle, identity, check=stop)

    def test_py39_syntax_and_no_eager_pyspark_or_sibling_import(self):
        tree = ast.parse(Path(ref.__file__).read_text(), feature_version=(3, 9))
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                self.assertNotIn('pyspark', ast.dump(node))
                self.assertNotIn('tpcds_approved_variants', ast.dump(node))


if __name__ == '__main__':
    unittest.main()
