"""Supplemental static pins and lifecycle; private sources stay opt-in."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from benchmarks.local_perf import tpcds_sql as ds, tpcds_variants as variants
from benchmarks.local_perf.capacity import Campaign
from test_local_perf_tpcds_sql import pins_for


def synthetic():
    doc = variants.metadata()
    entries = []
    for rule in doc['queries']:
        source, logical = 'private source ' + rule['id'], 'private rendered ' + rule['id']
        rule.update(source_sql_sha256=ds.digest(source), portable_sql_sha256=ds.digest(logical))
        rule['source_normalization']['raw_source_sha256'] = ds.digest(source)
        entries.append(variants._entry(rule['id'], source, logical, rule))
    manifest = dict(format=1, suite=variants.SUITE, dataset_id='source/test', source_dataset_id='source/test',
        table_schemas=doc['table_schemas'], provenance=doc['provenance'], queries=entries,
        source_hashes={q['id']: q['source_sha256'] for q in entries})
    return doc, manifest


class Inventory(unittest.TestCase):
    def test_categories_and_no_false_complete(self):
        c = variants.coverage()
        self.assertEqual((c['baseline']['count'], c['supplement']['count'], c['approved_alternative_templates']['count']), (99, 4, 14))
        # Expanded corpus includes the fourteen pinned Spark alternatives.
        self.assertEqual(c['selected_execution_cases'], 117)
        self.assertFalse(c['official_tpc_score'])
        self.assertFalse(c['all_variants_complete'])
        self.assertEqual(c['baseline']['iterations'], {'14': 1, '23': 2, '24': 1, '39': 1})
        self.assertTrue(all(c['status'] == 'pending' for c in c['approved_alternative_templates']['candidates']))
        self.assertNotIn('registration', json.dumps(c))

    def test_static_schema_hashes_and_provenance(self):
        doc = variants.metadata()
        self.assertEqual([len(q['columns']) for q in doc['queries']], [12, 1, 4, 10])
        for q in doc['queries']:
            self.assertEqual(len(set(c['name'] for c in q['columns'])), len(q['columns']))
            for k in ('source_sql_sha256', 'portable_sql_sha256', 'portable_body_sha256'):
                self.assertRegex(q[k], r'^[0-9a-f]{64}$')
            self.assertNotIn('portable_body', q)
            self.assertNotIn('DuckDB 1.4.3 tpcds_queries', q['rewrite_provenance']['source'])
        p = doc['provenance']
        self.assertEqual(p['commit'], '4cb6bd30df3d80cea0b504e7250d65777a021bca')
        self.assertFalse(p['license_observations']['query_headers'])
        self.assertTrue(p['license_observations']['query_paths_excluded_from_RAT'])
        self.assertEqual(doc['queries'][1]['columns'], [dict(name='c0', type='decimal(38,2)')])

    def test_default_cli_no_optional_import_or_network(self):
        code = "import sys,socket; socket.socket=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network')); from benchmarks.local_perf.tpcds_variants import coverage; coverage(); assert 'sqlglot' not in sys.modules; assert 'duckdb' not in sys.modules"
        subprocess.run([sys.executable, '-c', code], check=True, capture_output=True)
        run = subprocess.run([sys.executable, '-m', 'benchmarks.local_perf', 'tpcds-coverage'], check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(run.stdout)['supplement']['ids'], list(variants.IDS))

    def test_dedicated_requirements(self):
        self.assertEqual(Path(variants.__file__).with_name('requirements-tpcds-variants.txt').read_text().splitlines(), ['duckdb==1.4.3', 'sqlglot==30.31.0'])


class SourceValidation(unittest.TestCase):
    def test_exact_private_files_and_byte_tamper(self):
        doc, manifest = synthetic()
        with tempfile.TemporaryDirectory() as tmp, patch.object(variants, 'metadata', return_value=doc):
            root = Path(tmp)
            for q in manifest['queries']:
                (root / ('query'+q['id']+'.sql')).write_text(q['source_sql'])
            self.assertEqual(set(variants.read_sources(root)), set(variants.IDS))
            path = root / 'query14b.sql'
            path.write_text('tamper')
            with self.assertRaisesRegex(ValueError, 'bytes mismatch'):
                variants.read_sources(root)

    def test_missing_extra_nested_duplicate(self):
        for mode in ('missing', 'extra', 'nested', 'duplicate'):
            doc, manifest = synthetic()
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp, patch.object(variants, 'metadata', return_value=doc):
                root = Path(tmp)
                for q in manifest['queries']:
                    (root / ('query'+q['id']+'.sql')).write_text(q['source_sql'])
                if mode == 'missing':
                    (root/'query14b.sql').unlink()
                elif mode == 'extra':
                    (root/'query14a.SQL').touch()
                else:
                    (root/'nested').mkdir()
                    (root/'nested/query14b.sql').touch()
                    if mode == 'nested':
                        (root/'query14b.sql').unlink()
                with self.assertRaises(ValueError):
                    variants.read_sources(root)

    def test_bad_sources_rejected_before_sdk_or_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            campaign = Mock()
            with self.assertRaises(ValueError):
                variants.prepare_supplement(campaign, tmp, 'bad', tmp)
            campaign.own.assert_not_called()


class Binding(unittest.TestCase):
    def test_bind_and_runner_contract(self):
        doc, manifest = synthetic()
        from benchmarks.local_perf.runner import validate_sql_contract
        with patch.object(variants, 'metadata', return_value=doc):
            bound = ds.bind_snapshots(manifest, pins_for(manifest))
        self.assertEqual(len(bound['queries']), 4)
        for q in bound['queries']:
            validate_sql_contract(bound, q)
            self.assertEqual(q['reference_sql'], q['logical_sql'])
            self.assertIn('FOR VERSION AS OF 123', q['sql'])
        self.assertEqual(manifest['dataset_id'], 'source/test')

    def test_all_entry_tampering_rejected(self):
        changes = [lambda m: m['queries'].pop(),
            lambda m: m['queries'][1].update(id='14b'),
            lambda m: m['queries'][0].update(id='14'),
            lambda m: m.update(suite=ds.SUITE),
            lambda m: m.update(source_hashes={}),
            lambda m: m.update(table_schemas={}),
            lambda m: m.update(provenance={}),
            lambda m: m.update(dataset_id='other')]
        for key in ('source_sql', 'logical_sql', 'reference_sql', 'sql'):
            def change(m, k=key):
                m['queries'][0][k] += ' changed'
                h = {'source_sql': 'source_sha256', 'logical_sql': 'logical_sha256', 'reference_sql': 'reference_sha256', 'sql': 'sql_sha256'}[k]
                m['queries'][0][h] = ds.digest(m['queries'][0][k])
            changes.append(change)
        for key, value in (('columns', []), ('order_by', []), ('limit', 3), ('rewrite_provenance', {}), ('ordered', True)):
            changes.append(lambda m, k=key, v=value: m['queries'][0].update({k: v}))
        for change in changes:
            doc, manifest = synthetic()
            pins = pins_for(manifest)
            change(manifest)
            with self.subTest(change=change), patch.object(variants, 'metadata', return_value=doc), self.assertRaises(ValueError):
                ds.bind_snapshots(manifest, pins)

    def test_normalization_lineage_tamper_rejected_without_parser(self):
        doc, manifest = synthetic()
        lineage = manifest['queries'][0]['source_normalization']
        self.assertEqual(lineage['raw_source_sha256'], manifest['queries'][0]['source_sha256'])
        self.assertEqual((lineage['parser'], lineage['version'], lineage['comments']), ('SQLGlot', '30.31.0', False))
        for key in lineage:
            changed = copy.deepcopy(manifest)
            changed['queries'][0]['source_normalization'][key] = 'tampered'
            with self.subTest(key=key), patch.object(variants, 'metadata', return_value=doc), self.assertRaises(ValueError):
                ds.bind_snapshots(changed, pins_for(manifest))
        del manifest['queries'][0]['source_normalization']
        with patch.object(variants, 'metadata', return_value=doc), self.assertRaises(ValueError):
            variants.validate_manifest(manifest)

    def test_snapshot_identity_guards(self):
        changes = [lambda p: p.update(dataset_id='other'), lambda p: p['tables'].pop(),
            lambda p: p['tables'][0].update(columns=[]), lambda p: p['tables'][0].update(snapshot_id='-1'),
            lambda p: p['tables'][0].update(table_uuid=''), lambda p: p['tables'][0]['ref'].update(database_id='other'),
            lambda p: p['tables'][0].update(physical_name=p['tables'][1]['physical_name'])]
        for change in changes:
            doc, manifest = synthetic()
            pins = pins_for(manifest)
            change(pins)
            with patch.object(variants, 'metadata', return_value=doc), self.assertRaises(ValueError):
                ds.bind_snapshots(manifest, pins)

    def test_old_99_still_strict(self):
        _, manifest = synthetic()
        with self.assertRaises(ValueError):
            ds.logical_manifest(dict(format=1, dataset_id='source', queries=[dict(id=q['id'], sql=q['source_sql'], sql_sha256=q['source_sha256']) for q in manifest['queries']]))


class Lifecycle(unittest.TestCase):
    def test_shared_parity_failure_retains_evidence_cleans_scratch(self):
        doc, manifest = synthetic()
        manifest['queries'] = manifest['queries'][:1]
        manifest['source_manifest_sha256'] = ds.digest('{}')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            dataset = root/'source'
            dataset.mkdir()
            for f in ('dataset.json', 'queries.json'):
                (dataset/f).write_text('{}')
            campaign = Campaign.create(root/'campaign')
            con = Mock()
            def connect(db, spill):
                db.touch(); spill.mkdir(); (spill/'owned').touch()
                return con
            with patch.object(ds, 'connect', side_effect=connect), patch.object(ds, '_stream', side_effect=RuntimeError('probe failure')):
                with self.assertRaisesRegex(RuntimeError, 'probe failure'):
                    ds._prepare_parity(campaign, dataset, 'failed', dict(dataset_id='source/test', tables=[]), 1,
                        '{}', '{}', manifest, {q['id']: q for q in doc['queries']}, 10, 0, 0)
            out = campaign.root/'failed'
            con.close.assert_called_once()
            self.assertFalse((out/'parity.duckdb').exists())
            self.assertFalse((out/'spill').exists())
            self.assertEqual(json.loads((out/'plan.json').read_text())['status'], 'FAILED')
            self.assertTrue((out/'queries.json').exists())

    def test_preflight_hash_timeout_aborts_before_parity_dispatch(self):
        doc = variants.metadata()
        clock = [0.0]
        real_sha256 = hashlib.sha256
        def timed_hash(*args, **kwargs):
            h = real_sha256(*args, **kwargs)
            def update(chunk):
                h.update(chunk)
                clock[0] = 2.0  # A slow preflight chunk exhausts the caller's one second.
            return Mock(update=update, hexdigest=h.hexdigest)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'part.parquet').write_bytes(b'dataset bytes')
            meta = dict(status='SUCCESS', suite='tpcds', generator_version='1.4.3', dataset_id='test',
                tables=[dict(name=t, schema=cols, file='part.parquet', rows=1) for t, cols in doc['table_schemas'].items()])
            (root/'dataset.json').write_text(json.dumps(meta))
            (root/'queries.json').write_text('{}')
            campaign = Mock()
            with patch.object(variants, 'read_sources', return_value={}), \
                    patch.object(ds, 'logical_manifest', return_value=dict(source_dataset_id='test')), \
                    patch.object(variants.time, 'monotonic', side_effect=lambda: clock[0]), \
                    patch.object(variants.hashlib, 'sha256', side_effect=timed_hash), \
                    patch.object(ds, '_prepare_parity') as parity:
                with self.assertRaisesRegex(TimeoutError, 'supplement deadline'):
                    variants.prepare_supplement(campaign, root, 'timeout', root, timeout=1)
                parity.assert_not_called()
                campaign.own.assert_not_called()
                campaign.check.assert_any_call(additional=2*1024**3)

    def test_dataset_byte_identity_rejects_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root/'part.parquet'
            path.write_bytes(b'one')
            meta = dict(tables=[dict(file=path.name, rows=1)])
            before = variants._dataset_identity(root, meta, 1, lambda: None)
            path.write_bytes(b'two')
            self.assertNotEqual(before, variants._dataset_identity(root, meta, 1, lambda: None))


@unittest.skipUnless(os.environ.get('SHIROKUMA_TPCDS_VARIANTS_SOURCE') and os.environ.get('SHIROKUMA_TPCDS_VARIANTS_DATASET'), 'explicit private source/dataset opt-in required')
class RealSupplement(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('SHIROKUMA_TPCDS_VARIANTS_SYNTHETIC_DATASET'),
                         'explicit synthetic expression dataset opt-in required')
    def test_nonempty_synthetic_expression_parity(self):
        from benchmarks.local_perf.results import Reader
        import math

        dataset = Path(os.environ['SHIROKUMA_TPCDS_VARIANTS_SYNTHETIC_DATASET'])
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in dataset.iterdir() if p.is_file()}
        meta = json.loads((dataset/'dataset.json').read_text())
        self.assertEqual(meta['source_kind'], 'synthetic expression qualification using pinned schemas')
        self.assertEqual(len(meta['tables']), 24)
        self.assertEqual(sum(t['rows'] for t in meta['tables']), 19)
        self.assertEqual(sum(t['rows'] > 0 for t in meta['tables']), 9)
        root = os.environ.get('SHIROKUMA_TPCDS_VARIANTS_OUTPUT_DIR')
        tmp = Path(tempfile.mkdtemp(dir=root, prefix='synthetic-opt-in-'))
        campaign = Campaign.create(tmp/'synthetic-campaign', drain_bytes=2*1024**3)
        out = variants.prepare_supplement(campaign, dataset, 'nonempty-expression-plan',
                                         os.environ['SHIROKUMA_TPCDS_VARIANTS_SOURCE'])
        print('synthetic supplement proof:', out)
        record = json.loads((out/'plan.json').read_text())
        self.assertEqual((record['status'], record['query_count'], record['nonempty_queries'], record['result_rows']),
                         ('SUCCESS', 4, 3, 3))
        manifest = json.loads((out/'queries.json').read_text())
        expected = {'14b': [], '23a': [[None]],
                    '24b': [[['s', 'Fixture'], ['s', 'Case'], ['s', 'FixtureStore'], ['d', '100']]]}
        for q, audit in zip(manifest['queries'], record['queries']):
            self.assertEqual(audit['id'], q['id'])
            self.assertEqual(audit['status'], 'SUCCESS')
            self.assertEqual(audit['source_normalization'], q['source_normalization'])
            self.assertEqual(audit['source']['executed_sql_sha256'], q['source_sha256'])
            streams = []
            for label in ('source', 'source_cast', 'portable'):
                self.assertEqual(audit[label]['file'], q['id']+'-'+label+'.jsonl')
                reader = Reader(out/audit[label]['file'])
                self.assertEqual(reader.header['columns'], q['columns'])
                rows = list(reader)  # Validates typed cells and SUCCESS footer/hash.
                streams.append(rows)
                self.assertEqual(audit[label]['rows'], 0 if q['id'] == '14b' else 1)
                if q['id'] in expected:
                    self.assertEqual(rows, expected[q['id']])
                else:
                    self.assertEqual([c['name'] for c in q['columns']],
                        ['w_warehouse_sk', 'i_item_sk', 'd_moy', 'mean', 'cov',
                         'column_5', 'column_6', 'column_7', 'column_8', 'column_9'])
                    self.assertEqual(len(rows), 1)
                    row = rows[0]
                    for index, value in ((0, '1'), (1, '1'), (2, '1'), (5, '1'), (6, '1'), (7, '2')):
                        self.assertEqual(row[index], ['i', value])
                    for index, value in ((3, 333.0), (8, 333.0), (4, math.sqrt(3)), (9, math.sqrt(3))):
                        self.assertEqual(row[index][0], 'f')
                        self.assertAlmostEqual(float(row[index][1]), value, places=12)
            self.assertEqual(streams[0], streams[1])
            self.assertEqual(streams[0], streams[2])
            self.assertIn('limit_audit', audit)
            self.assertIn('portable_limit_audit', audit)
        self.assertEqual(len(ds.bind_snapshots(manifest, pins_for(manifest))['queries']), 4)
        self.assertEqual(before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in dataset.iterdir() if p.is_file()})

    def test_full_parity_and_bind(self):
        root = os.environ.get('SHIROKUMA_TPCDS_VARIANTS_OUTPUT_DIR')
        tmp = tempfile.mkdtemp(dir=root, prefix='opt-in-')
        campaign = Campaign.create(Path(tmp)/'campaign', drain_bytes=2*1024**3)
        out = variants.prepare_supplement(campaign, os.environ['SHIROKUMA_TPCDS_VARIANTS_DATASET'], 'plan',
            os.environ['SHIROKUMA_TPCDS_VARIANTS_SOURCE'])
        print('supplement proof:', out)
        record = json.loads((out/'plan.json').read_text())
        self.assertEqual((record['status'], record['query_count']), ('SUCCESS', 4))
        manifest = json.loads((out/'queries.json').read_text())
        for q, audit in zip(manifest['queries'], record['queries']):
            lineage = q['source_normalization']
            self.assertEqual(audit['source_normalization'], lineage)
            self.assertEqual(audit['source']['executed_sql_sha256'], lineage['raw_source_sha256'])
            self.assertNotEqual(audit['source_cast']['executed_sql_sha256'], lineage['normalized_source_body_sha256'])
            if q['limit'] is not None:
                self.assertEqual(audit['limit_audit']['input_sql_sha256'], lineage['normalized_source_body_sha256'])
        bound = ds.bind_snapshots(manifest, pins_for(manifest))
        self.assertEqual(len(bound['queries']), 4)
