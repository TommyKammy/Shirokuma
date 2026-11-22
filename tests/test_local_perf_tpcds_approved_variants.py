"""Static contracts and opt-in private source/dataset/original Spark qualification."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from benchmarks.local_perf import tpcds_approved_variants as approved, tpcds_sql as ds
from benchmarks.local_perf.capacity import Campaign
from benchmarks.local_perf.results import Reader, Writer, column, decoded
from test_local_perf_tpcds_sql import pins_for
from tests.local_perf_host import setUpModule, tearDownModule  # noqa: F401


def synthetic():
    doc = approved.metadata()
    entries = []
    for rule in doc['queries']:
        source, portable = 'effective '+rule['id'], 'portable '+rule['id']
        rule.update(source_sql_sha256=ds.digest(source), effective_sql_sha256=ds.digest(source), cast_sql_sha256=ds.digest(source), portable_sql_sha256=ds.digest(portable))
        entries.append(approved._entry(rule['id'], source, portable, rule))
    identity = dict(dataset_id='test/source', queries_manifest_sha256='a'*64)
    manifest = dict(format=1, suite=approved.SUITE, dataset_id='test/source', source_dataset_id='test/source',
                    table_schemas=ds.table_schemas(), provenance=approved.SOURCE, queries=entries,
                    source_hashes={q['id']: q['source_sha256'] for q in entries}, source_manifest_sha256='a'*64,
                    spark_qualification=dict(status='SUCCESS', dataset_identity=identity, report_sha256='b'*64,
                        profile=doc['oracle_profile'], engine_version='4.1.3',
                        raw_source_hashes={q['id']: q['raw_source_sha256'] for q in doc['queries']}))
    return doc, manifest


class StaticContracts(unittest.TestCase):
    def test_metadata_hashes_no_bodies_or_duplicate_input_schema(self):
        doc = approved.metadata()
        self.assertEqual(len(doc['queries']), 14)
        self.assertEqual(doc['input_schema_sha256'], approved.fingerprint(ds.table_schemas()))
        self.assertEqual(doc['provenance']['commit'], '29434ea766b0fc3c3bf6eaadb43a8f931133649e')
        self.assertEqual(doc['oracle_version'], '4.1.3')
        self.assertFalse(doc['current_v4_template_identity'])
        self.assertFalse(doc['universal_algebraic_equivalence'])
        self.assertFalse(doc['official_tpc_score'])
        def walk(value):
            if isinstance(value, dict):
                self.assertFalse(set(value) & {'raw_sql', 'source_sql', 'effective_sql', 'portable_body', 'table_schemas'})
                for child in value.values(): walk(child)
            elif isinstance(value, list):
                for child in value: walk(child)
        walk(doc)
        q = {r['id']: r for r in doc['queries']}
        self.assertEqual(q['18a']['transformations']['decimal_avg_cast'], 35)
        self.assertEqual(q['27a']['transformations']['decimal_avg_cast'], 9)
        self.assertEqual(q['36a']['transformations']['decimal_division_cast'], 3)
        for r in q.values():
            self.assertEqual(r['source_sql_sha256'], r['cast_sql_sha256'])
            self.assertTrue(r['spark_dtype_evidence'])
            self.assertTrue(r['effective_native_schema'])

    def test_metadata_schema_tamper(self):
        doc = approved.metadata()
        for field, value in [('suite', 'wrong'), ('input_schema_sha256', '0'*64), ('current_v4_template_identity', True)]:
            bad = copy.deepcopy(doc); bad[field] = value
            with patch.object(Path, 'read_text', return_value=json.dumps(bad)), self.assertRaises(ValueError):
                approved.metadata()

    def test_no_optional_imports_coverage_metadata_binding(self):
        code = '''import sys
from benchmarks.local_perf import tpcds_approved_variants as a, tpcds_variants as v, tpcds_sql as ds
a.metadata()
assert v.coverage()['selected_execution_cases'] == 117
try: ds.bind_snapshots({'suite': a.SUITE}, {})
except (ValueError, KeyError): pass
assert not set(('sqlglot','pyspark','duckdb')) & set(sys.modules)
'''
        subprocess.run([sys.executable, '-c', code], check=True, capture_output=True)

    def test_cli(self):
        run = subprocess.run([sys.executable, '-m', 'benchmarks.local_perf', 'prepare-tpcds-approved', '--help'], check=True, text=True, capture_output=True)
        self.assertIn('--spark-reference', run.stdout)
        self.assertIn('--source-dir', run.stdout)

    def test_manifest_lineage_and_sql_tamper(self):
        doc, manifest = synthetic()
        with patch.object(approved, 'metadata', return_value=doc):
            self.assertEqual(approved._validate_structure(manifest), manifest)
            with self.assertRaises(ValueError): approved.validate_manifest(manifest)
            for mutate in (
                lambda m: m.update(suite=ds.SUITE),
                lambda m: m['queries'].pop(),
                lambda m: m['queries'][0].update(source_sql='tampered'),
                lambda m: m['queries'][0].update(logical_sql='tampered'),
                lambda m: m['queries'][0]['source_normalization'].update(raw_source_sha256='0'*64),
                lambda m: m['queries'][0]['source_normalization'].update(effective_body_sha256='0'*64),
                lambda m: m['queries'][0]['source_normalization'].update(portable_body_sha256='0'*64),
                lambda m: m['spark_qualification'].update(profile={}),
                lambda m: m['spark_qualification'].update(status='INCOMPLETE'),
                lambda m: m['spark_qualification']['dataset_identity'].update(queries_manifest_sha256='c'*64),
                lambda m: m['spark_qualification'].update(engine_version='4.0.1'),
            ):
                bad = copy.deepcopy(manifest); mutate(bad)
                with self.assertRaises(ValueError): approved.validate_manifest(bad)

    def test_bind_exact_dispatch_and_context(self):
        doc, manifest = synthetic()
        pins = pins_for(manifest)
        with patch.object(approved, 'metadata', return_value=doc), patch.object(approved, 'validate_manifest', side_effect=approved._validate_structure):
            bound = ds.bind_snapshots(manifest, pins)
            self.assertEqual(len(bound['queries']), 14)
            self.assertIn('FOR VERSION AS OF', bound['queries'][0]['sql'])
            for mutate in (lambda p: p.update(dataset_id='wrong'), lambda p: p['tables'].pop(),
                           lambda p: p['tables'][0].update(snapshot_id='bad'),
                           lambda p: p['tables'][0]['ref'].update(database_id='wrong')):
                bad = copy.deepcopy(pins); mutate(bad)
                with self.assertRaises(ValueError): ds.bind_snapshots(manifest, bad)


class AcceptedArtifacts(unittest.TestCase):
    """Filesystem acceptance fixtures exercise real stdlib validators, no engines."""
    def setUp(self):
        from benchmarks.local_perf import spark_reference as spark
        from benchmarks.local_perf.capacity import write_json
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.doc, self.manifest = synthetic()
        self.source = self.root/'sources'; self.source.mkdir()
        for rule in self.doc['queries']:
            raw = 'SELECT '+repr(rule['id'])+';'
            (self.source/('q'+rule['id']+'.sql')).write_text(raw)
            rule['raw_source_sha256'] = ds.digest(raw)
        # Preserve exact lineage in the structural fixture after replacing private bodies.
        for q, rule in zip(self.manifest['queries'], self.doc['queries']):
            q['source_normalization'] = copy.deepcopy(rule)
        self.patch = patch.object(approved, 'metadata', return_value=self.doc)
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.dataset = self.root/'dataset'; self.dataset.mkdir()
        tables = []
        for name, cols in ds.table_schemas().items():
            path = self.dataset/(name+'.parquet'); path.write_bytes(b'fixture parquet bytes')
            tables.append(dict(name=name, schema=cols, file=path.name, rows=1,
                file_bytes=path.stat().st_size, physical_bytes=path.stat().st_size, sha256=approved._hash(path)))
        write_json(self.dataset/'dataset.json', dict(format=1, suite='tpcds', status='SUCCESS',
            dataset_id='test/source', generator_version='1.4.3', tables=tables))
        write_json(self.dataset/'queries.json', dict(format=1, dataset_id='test/source', queries=[]))
        identity = spark.dataset_identity(self.dataset)
        self.reference = self.root/'reference'; self.reference.mkdir()
        hashes = {q['id']: q['raw_source_sha256'] for q in self.doc['queries']}
        queries = []
        self.campaign = Campaign.create(self.root/'campaign')
        self.out = self.campaign.own('accepted', 'tpcds-plan')
        audits = []
        for q in self.manifest['queries']:
            i = q['id']; cols = q['columns']
            path = self.reference/(i+'-raw.jsonl')
            w = Writer(path, cols, spark._result_metadata(identity, spark._fingerprint(identity), i, hashes))
            w.row([None]*len(cols)); w.finish(); w.close()
            native = dict(type='struct', fields=[dict(name=c['name'], type={'bigint':'long'}.get(c['type'], c['type']), nullable=True, metadata={}) for c in cols])
            queries.append(dict(id=i, status='SUCCESS', source_sha256=hashes[i], raw_source_unmodified=True,
                native_schema=native, canonical_columns=cols, rows=1, result_file=path.name, result_sha256=approved._hash(path)))
        report = dict(format=1, status='SUCCESS', engine='Apache Spark', engine_version=spark.SPARK_VERSION,
            source_release='Spark v4.0.1', source_commit=spark.SOURCE_COMMIT, inputs_unchanged=True,
            dataset=identity, dataset_fingerprint=spark._fingerprint(identity), source_hashes=hashes,
            config=spark.EXPECTED_PROFILE, planned_queries=14, max_rows=1000, queries=queries)
        write_json(self.reference/spark.REPORT_FILE, report)
        qualification = self.manifest['spark_qualification']
        qualification.update(dataset_identity=identity, raw_source_hashes=hashes,
            report_sha256=approved._hash(self.reference/spark.REPORT_FILE))
        self.manifest['source_manifest_sha256'] = identity['queries_manifest_sha256']
        for q, raw in zip(self.manifest['queries'], queries):
            pair = dict(source_hash=q['source_sha256'], portable_hash=q['logical_sha256'], source_dataset_id='test/source')
            audit = dict(id=q['id'], status='SUCCESS', equivalence_contract=pair, source_normalization=q['source_normalization'])
            for label in ('source', 'source_cast', 'portable', 'spark-ordinal'):
                path = self.out/(q['id']+'-'+label+'.jsonl')
                w = Writer(path, q['columns'], pair); w.row([None]*len(q['columns'])); w.finish(); w.close()
                if label != 'spark-ordinal':
                    audit[label] = dict(file=path.name, rows=1, executed_sql_sha256=q['logical_sha256'] if label == 'portable' else q['source_sha256'])
            audit['spark_reference'] = dict(engine='Apache Spark 4.1.3 raw source', raw_sha256=hashes[q['id']],
                result_sha256=raw['result_sha256'], report_sha256=qualification['report_sha256'], rows=1,
                comparisons=['source', 'source_cast', 'portable'], source_cast_same_sql=True, independent_source_cast_proof=False)
            audits.append(audit)
        self.plan = dict(format=1, suite=approved.SUITE, status='SUCCESS', query_count=14, source_dataset_id='test/source',
            source_manifest_sha256=self.manifest['source_manifest_sha256'], abs_tol=1e-8, rel_tol=1e-8,
            dataset_qualified=True, qualification=qualification, queries=audits)
        write_json(self.out/'plan.json', self.plan)
        self.campaign.mark(self.out, 'SUCCESS')
        self.locations = dict(out_dir=str(self.out), campaign_dir=str(self.campaign.root),
            source_dir=str(self.source), dataset_dir=str(self.dataset), reference_dir=str(self.reference))
        acceptance = approved._evidence(self.manifest, self.locations, frozen=False)
        write_json(self.out/'acceptance.json', acceptance)
        self.manifest['acceptance'] = dict(path=str(self.out/'acceptance.json'), sha256=approved._hash(self.out/'acceptance.json'))
        write_json(self.out/'queries.json', self.manifest)
        self.bound = ds.bind_snapshots(self.manifest, pins_for(self.manifest))

    def reject(self, manifest=None):
        from benchmarks.local_perf import runner
        m = self.manifest if manifest is None else manifest
        with self.assertRaises((ValueError, KeyError, OSError, TypeError)):
            ds.bind_snapshots(m, pins_for(m))
        campaign, adapter = Mock(), Mock()
        with self.assertRaises((ValueError, KeyError, OSError, TypeError)):
            runner.run(campaign, 'must-not-own', self.bound if manifest is None else m, adapter)
        campaign.own.assert_not_called(); adapter.assert_not_called()
        self.assertEqual(adapter.mock_calls, [])

    def test_success_artifacts_bind_and_no_optional_imports_python39(self):
        approved.validate_manifest(self.manifest)
        approved.validate_bound_manifest(self.bound)
        docpath = self.root/'metadata.json'; docpath.write_text(json.dumps(self.doc))
        code = """import json,sys
from pathlib import Path
from benchmarks.local_perf import tpcds_approved_variants as a, tpcds_sql as ds
a.metadata=lambda: json.loads(Path(sys.argv[1]).read_text())
m=json.loads(Path(sys.argv[2]).read_text())
a.validate_manifest(m)
a.validate_bound_manifest(ds.bind_snapshots(m,json.loads(Path(sys.argv[3]).read_text())))
assert not set(('duckdb','sqlglot','pyspark')) & set(sys.modules)
"""
        pinpath = self.root/'pins.json'; pinpath.write_text(json.dumps(pins_for(self.manifest)))
        subprocess.run(['/usr/bin/python3', '-c', code, str(docpath), str(self.out/'queries.json'), str(pinpath)], check=True, capture_output=True)

    def test_every_current_artifact_tampering_rejects_bind_and_new_dispatch(self):
        from benchmarks.local_perf import spark_reference as spark
        paths = [self.out/'plan.json', self.campaign.path, self.source/'q5a.sql', self.dataset/'dataset.json',
            self.dataset/'queries.json', self.dataset/'store.parquet', self.reference/spark.REPORT_FILE,
            self.reference/'5a-raw.jsonl', self.out/'acceptance.json', self.out/'queries.json']
        paths += [self.out/('5a-'+label+'.jsonl') for label in ('source','source_cast','portable','spark-ordinal')]
        for path in paths:
            original = path.read_bytes()
            with self.subTest(path=path.name, change='missing'):
                path.unlink(); self.reject(); path.write_bytes(original)
            with self.subTest(path=path.name, change='bytes'):
                path.write_bytes(original+b'changed'); self.reject(); path.write_bytes(original)
        cases = [
            (self.out/'plan.json', lambda d: d.update(status='FAILED')),
            (self.out/'plan.json', lambda d: d['queries'][0].update(status='FAILED')),
            (self.out/'plan.json', lambda d: d['queries'][0]['source'].update(executed_sql_sha256='0'*64)),
            (self.out/'plan.json', lambda d: d['queries'][0]['source'].update(rows=2)),
            (self.out/'plan.json', lambda d: d['queries'][0]['spark_reference'].update(comparisons=[])),
            (self.campaign.path, lambda d: d['owned']['accepted'].update(status='FAILED')),
            (self.campaign.path, lambda d: d['owned']['accepted'].update(kind='run')),
            (self.reference/spark.REPORT_FILE, lambda d: d.update(config={})),
            (self.reference/spark.REPORT_FILE, lambda d: d['dataset'].update(dataset_id='wrong')),
        ]
        for path, change in cases:
            original = path.read_bytes(); value=json.loads(original); change(value)
            with self.subTest(change=repr(value)[:100]):
                path.write_text(json.dumps(value)); self.reject(); path.write_bytes(original)
        for label in ('source','source_cast','portable','spark-ordinal'):
            path=self.out/('5a-'+label+'.jsonl'); original=path.read_bytes()
            lines=original.decode().splitlines(True)
            for change in ('footer','rows','schema','header'):
                bad=list(lines)
                if change == 'footer': bad=bad[:-1]
                elif change == 'rows': bad.insert(1,bad[1])
                else:
                    header=json.loads(bad[0])
                    if change == 'schema': header['columns'][0]['type']='integer'
                    else: header['metadata']={}
                    bad[0]=json.dumps(header)+'\n'
                with self.subTest(label=label,change=change):
                    path.write_text(''.join(bad)); self.reject(); path.write_bytes(original)
        for change in (lambda m:m.update(source_manifest_sha256='0'*64),
                       lambda m:m['spark_qualification']['dataset_identity'].update(dataset_id='wrong')):
            bad=copy.deepcopy(self.manifest); change(bad); self.reject(bad)

    def test_full_snapshot_wrapper_and_fabricated_success_rejected(self):
        from benchmarks.local_perf import runner
        for mutate in (lambda m:m.update(extra='forged'), lambda m:m['queries'][0].update(sql='SELECT 1',sql_sha256=ds.digest('SELECT 1'),target_sha256=ds.digest('SELECT 1')),
                       lambda m:m['snapshot_pins']['tables'][0].update(snapshot_id='999')):
            bad=copy.deepcopy(self.bound); mutate(bad)
            with self.assertRaises(ValueError): approved.validate_bound_manifest(bad)
            with self.assertRaises(ValueError): runner.run(Mock(), 'no-intent', bad, Mock())
        bad=copy.deepcopy(self.manifest); bad.pop('acceptance'); self.reject(bad)

    def test_freeze_failure_after_publication_invalidates_written_acceptance(self):
        checks=[0]
        def check():
            checks[0]+=1
            # Trigger precisely after atomic qualified queries publication.
            if (self.out/'queries.json').exists() and approved._load(self.out/'queries.json').get('acceptance'):
                raise TimeoutError('final deadline')
        # Finalization starts with the common unqualified output.
        unqualified=copy.deepcopy(self.manifest); unqualified.pop('acceptance'); unqualified['spark_qualification']['status']='INCOMPLETE'
        from benchmarks.local_perf.capacity import write_json
        write_json(self.out/'queries.json',unqualified)
        with self.assertRaisesRegex(TimeoutError,'final deadline'):
            approved._finalize_approved(self.out,self.campaign,unqualified,self.source,self.dataset,self.reference,lambda:None,check)
        self.assertEqual(approved._load(self.out/'plan.json')['status'],'FAILED')
        self.campaign.refresh(); self.assertEqual(self.campaign.state['owned']['accepted']['status'],'FAILED')
        self.reject()

    def test_freeze_input_drift_after_common_success_marks_both_owners_failed(self):
        from benchmarks.local_perf.capacity import write_json
        for fail_on in (1,2):
            with self.subTest(final_boundary=fail_on):
                unqualified=copy.deepcopy(self.manifest);unqualified.pop('acceptance')
                unqualified['spark_qualification']['status']='INCOMPLETE'
                write_json(self.out/'queries.json',unqualified)
                write_json(self.out/'plan.json',self.plan)
                self.campaign.mark(self.out,'SUCCESS')
                calls=[0]
                def drift():
                    calls[0]+=1
                    if calls[0]==fail_on: raise ValueError('qualification input changed at freeze')
                with self.assertRaisesRegex(ValueError,'input changed at freeze'):
                    approved._finalize_approved(self.out,self.campaign,unqualified,self.source,self.dataset,self.reference,drift,lambda:None)
                self.assertEqual(approved._load(self.out/'plan.json')['status'],'FAILED')
                self.campaign.refresh();self.assertEqual(self.campaign.state['owned']['accepted']['status'],'FAILED')
                self.assertEqual(approved._load(self.out/'queries.json')['spark_qualification']['status'],'INCOMPLETE')
                self.reject()

    def test_unknown_recovery_ignores_missing_live_qualification_and_never_posts(self):
        from benchmarks.local_perf import runner
        from tests.test_local_perf_runner import ScriptedTrino
        adapter=ScriptedTrino('cancel-fails'); captured={}
        receipt=self.campaign.root/'interrupted/5a-0/attempt.json'
        write=runner.write_json
        def capture(path,value):
            write(path,value)
            if path==receipt and value['status']=='RUNNING' and not captured:
                for p in (receipt,receipt.parent.parent/'run.json',receipt.parent.parent/'queries.json'):
                    captured[p]=p.read_bytes()
        with patch.object(runner,'write_json',side_effect=capture), self.assertRaises(TimeoutError):
            runner.run(self.campaign,'interrupted',self.bound,adapter,reference_label='test only')
        for path,raw in captured.items(): path.write_bytes(raw)
        self.assertTrue(captured)
        (self.source/'q5a.sql').unlink(); (self.reference/'5a-raw.jsonl').unlink()
        fresh=ScriptedTrino('cancel-fails')
        self.assertEqual(runner.reconcile(receipt,fresh)['status'],'UNKNOWN')
        with patch.object(fresh,'request',side_effect=[{}, {'state':'CANCELED'}]) as request:
            self.assertEqual(runner.reconcile(receipt,fresh)['reconciliation'],'CANCELED')
            self.assertEqual([c.args[0] for c in request.call_args_list],['DELETE','GET'])
        self.assertEqual(fresh.posts,0)

    def test_failed_external_comparison_saved_manifest_cannot_bind_or_dispatch(self):
        from contextlib import nullcontext
        from benchmarks.local_perf import runner
        from benchmarks.local_perf.capacity import write_json
        manifest=copy.deepcopy(self.manifest); manifest.pop('acceptance'); manifest['spark_qualification']['status']='INCOMPLETE'
        rules={q['id']:dict(q,portable_body=q['logical_sql']) for q in manifest['queries']}
        def stream(con,sql,path,columns,pair,check):
            w=Writer(path,columns,pair);w.row([None]*len(columns));w.finish();w.close()
            return dict(file=path.name,rows=1,executed_sql_sha256=ds.digest(sql))
        with patch.object(ds,'connect',return_value=Mock()), patch.object(ds,'bounded',side_effect=lambda *args:nullcontext()), \
             patch.object(ds,'_stream',side_effect=stream), patch.object(ds,'projection',side_effect=lambda body,rule:rule['source_sql']), \
             patch.object(ds,'audit_limit',return_value={}), self.assertRaisesRegex(ValueError,'external mismatch'):
            ds._prepare_parity(self.campaign,self.dataset,'failed-external',dict(dataset_id='test/source',tables=[]),1,
                (self.dataset/'dataset.json').read_text(),(self.dataset/'queries.json').read_text(),manifest,rules,10,1e-8,1e-8,
                external_compare=Mock(side_effect=ValueError('external mismatch')))
        failed=approved._load(self.campaign.root/'failed-external/queries.json')
        self.assertEqual(failed['spark_qualification']['status'],'INCOMPLETE')
        self.reject(failed)
        # A manually bound wrapper cannot launder the failed unqualified body.
        with patch.object(approved,'validate_manifest',side_effect=approved._validate_structure):
            forged=ds.bind_snapshots(failed,pins_for(failed))
        with self.assertRaises((ValueError,KeyError)): runner.run(Mock(),'no-intent',forged,Mock())


class SourcesAndDeadline(unittest.TestCase):
    def test_sources_exact_set_and_bytes(self):
        doc = approved.metadata()
        for q in doc['queries']: q['raw_source_sha256'] = ds.digest('source '+q['id'])
        for mode in ('valid', 'missing', 'extra', 'nested', 'duplicate', 'tamper'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp, patch.object(approved, 'metadata', return_value=doc):
                root = Path(tmp)
                for i in approved.IDS: (root / ('q'+i+'.sql')).write_text('source '+i)
                if mode == 'missing': (root/'q5a.sql').unlink()
                if mode == 'extra': (root/'q99a.SQL').touch()
                if mode in ('nested', 'duplicate'):
                    (root/'nested').mkdir(); (root/'nested/q5a.sql').write_text('source 5a')
                    if mode == 'nested': (root/'q5a.sql').unlink()
                if mode == 'tamper': (root/'q5a.sql').write_text('source 5a\n')
                if mode == 'valid': self.assertEqual(set(approved.read_sources(root)), set(approved.IDS))
                else:
                    with self.assertRaises(ValueError): approved.read_sources(root)

    def test_invalid_tolerances_timeout_before_inputs(self):
        for kwargs in ({'timeout': 0}, {'timeout': float('inf')}, {'abs_tol': 1e-7}, {'rel_tol': float('nan')}, {'abs_tol': -1}):
            with patch.object(approved, 'read_sources') as read, self.assertRaises(ValueError):
                approved.prepare_approved(Mock(), 'dataset', 'plan', 'source', 'ref', **kwargs)
            read.assert_not_called()

    def test_one_deadline_includes_preflight_campaign_check(self):
        campaign = Mock()
        clock = [0]
        campaign.check.side_effect = lambda **kwargs: clock.__setitem__(0, 2)
        with patch.object(approved.time, 'monotonic', side_effect=lambda: clock[0]), self.assertRaises(TimeoutError):
            approved.prepare_approved(campaign, 'dataset', 'plan', 'source', 'ref', timeout=1)
        campaign.own.assert_not_called()

    def test_owned_failure_retained_scratch_cleaned(self):
        # Exercise shared lifecycle without an SDK: failure after an owned DB exists.
        doc, manifest = synthetic()
        with tempfile.TemporaryDirectory() as tmp:
            camp = Campaign.create(Path(tmp)/'campaign')
            original = copy.deepcopy(camp.state['baseline'])
            def fail(path, spill):
                Path(path).touch(); Path(spill).mkdir(); (Path(spill)/'partial').touch()
                raise ValueError('independent Spark compare failed')
            with patch.object(ds, 'connect', side_effect=fail), self.assertRaisesRegex(ValueError, 'Spark compare'):
                ds._prepare_parity(camp, Path(tmp), 'failed',  {'dataset_id': 'test/source'}, 1, '', '', manifest, {}, 1, 1e-8, 1e-8, absolute_deadline=time.monotonic()+5)
            out = Path(tmp)/'campaign/failed'
            self.assertEqual(json.loads((out/'plan.json').read_text())['status'], 'FAILED')
            self.assertFalse((out/'parity.duckdb').exists()); self.assertFalse((out/'spill').exists())
            camp.refresh(); self.assertEqual(camp.state['baseline'], original)
            self.assertEqual(camp.state['owned']['failed']['status'], 'FAILED')

    def test_ordinal_copy_preserves_decimal_and_float(self):
        cols = [column('d', 'decimal(38,11)'), column('f', 'double')]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'native.jsonl'; target = Path(tmp)/'neutral.jsonl'
            from decimal import Decimal
            w = Writer(path, cols, {'engine': 'Apache Spark 4.1.3 raw source'})
            w.row([Decimal('12345678901234567890.12345678901'), 0.123456789012345]); w.finish()
            r = Reader(path); w = Writer(target, cols, {'neutral': True})
            for row in r: w.row([decoded(v) for v in row])
            w.finish()
            self.assertEqual(list(Reader(path)), list(Reader(target)))


class ReferenceIntegration(unittest.TestCase):
    def invoke(self, root, parity, *, identity_change=False, proof_change=False):
        from benchmarks.local_perf import spark_reference as spark
        doc, manifest = synthetic()
        dataset = root/'dataset'; dataset.mkdir()
        meta = dict(format=1, status='SUCCESS', suite='tpcds', generator_version='1.4.3', dataset_id='test/source',
                    tables=[dict(name=t, schema=cols) for t, cols in ds.table_schemas().items()])
        (dataset/'dataset.json').write_text(json.dumps(meta)); (dataset/'queries.json').write_text('{}')
        identity = dict(dataset_id='test/source', queries_manifest_sha256=ds.digest('{}'))
        proof = dict(report={}, report_sha256='b'*64, results={})
        for q in doc['queries']:
            path = root/(q['id']+'-raw.jsonl')
            w = Writer(path, q['columns'], {'engine': 'Apache Spark 4.1.3 raw source'})
            w.row([None]*len(q['columns'])); w.finish()
            proof['results'][q['id']] = dict(path=path, columns=q['columns'], rows=1, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        with patch.object(approved, 'metadata', return_value=doc), patch.object(approved, 'read_sources', return_value={}), \
             patch.object(ds, 'logical_manifest', return_value={'source_dataset_id': 'test/source'}), \
             patch.object(approved, 'compile_sources', return_value=(manifest['queries'], {}, {})), \
             patch.object(spark, 'dataset_identity', side_effect=[identity, {}] if identity_change else None, return_value=identity), \
             patch.object(spark, 'validate_reference', side_effect=[proof, {}] if proof_change else None, return_value=proof) as validate, \
             patch.object(ds, '_prepare_parity', side_effect=parity), \
             patch.object(approved, '_finalize_approved', side_effect=lambda out, *args: out):
            result = approved.prepare_approved(Mock(), dataset, 'plan', root, root, timeout=12)
            self.assertEqual(validate.call_args.kwargs['expected_columns'], {q['id']: q['columns'] for q in doc['queries']})
            return result

    def test_deadline_and_proof_recheck_dispatch(self):
        def parity(*args, **kwargs):
            self.assertLessEqual(kwargs['absolute_deadline']-time.monotonic(), 12)
            args[12]()  # end-of-parity input/reference recheck
            return 'complete'
        # Positional recheck index is 12: campaign through rel_tol occupy 0..11.
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.invoke(Path(tmp), parity), 'complete')

    def test_input_or_reference_changed_rejected(self):
        for field in ('identity_change', 'proof_change'):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                def parity(*args, **kwargs): args[12]()
                with self.assertRaises(ValueError): self.invoke(Path(tmp), parity, **{field: True})

    def test_external_hook_full_comparison_and_bad_footer(self):
        for corrupt in (False, True):
            with self.subTest(corrupt=corrupt), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                def parity(*args, **kwargs):
                    manifest = args[7]; q = manifest['queries'][0]
                    pair = dict(source_hash=q['source_sha256'], portable_hash=q['logical_sha256'], source_dataset_id='test/source')
                    out = root/'out'; out.mkdir()
                    audit = {}
                    for label in ('source', 'source_cast', 'portable'):
                        path = out/(label+'.jsonl'); w = Writer(path,q['columns'],pair)
                        w.row([None]*len(q['columns'])); w.finish(); audit[label] = {'file': path.name}
                    if corrupt:
                        path = root/(q['id']+'-raw.jsonl'); path.write_text(path.read_text().rsplit('\n',2)[0]+'\n')
                    result = kwargs['external_compare'](out,q,audit,pair,lambda:None)
                    self.assertEqual(result['engine'], 'Apache Spark 4.1.3 raw source')
                    self.assertFalse(result['independent_source_cast_proof'])
                    self.assertEqual(result['comparisons'], ['source','source_cast','portable'])
                if corrupt:
                    with self.assertRaises(ValueError): self.invoke(root, parity)
                else: self.invoke(root, parity)


@unittest.skipUnless(importlib.util.find_spec('sqlglot') and importlib.util.find_spec('duckdb'), 'isolated optional SDKs required')
class NormalizationSemantics(unittest.TestCase):
    def test_ansi_cast_and_division_no_try_nullif(self):
        import duckdb
        sql = "SELECT TRY_CAST(v AS INT) AS n, 1 / d AS ratio FROM t ORDER BY n"
        _, body, counts, _ = approved.normalize(sql, 'test')
        self.assertEqual(counts['try_cast'], 1)
        self.assertNotIn('TRY_CAST', body); self.assertNotIn('NULLIF', body)
        self.assertIn('NULLS FIRST', body)
        c = duckdb.connect()
        try:
            c.execute('CREATE TABLE t(v VARCHAR,d INT)'); c.execute("INSERT INTO t VALUES ('2',2),(NULL,3),('1',4)")
            self.assertEqual(c.execute(body).fetchall(), [(None, 1/3), (1, .25), (2, .5)])
            c.execute("INSERT INTO t VALUES ('invalid',0)")
            with self.assertRaises(duckdb.ConversionException): c.execute(body).fetchall()
            # DuckDB yields infinity on division by zero; strict typed results reject it.
            from benchmarks.local_perf.results import cell
            with self.assertRaises(ValueError): cell(c.execute('SELECT 1.0/0').fetchone()[0], 'double')
        finally: c.close()

    def test_decimal_avg_rounding_and_double_average_preserved(self):
        import duckdb
        from decimal import Decimal
        c = duckdb.connect()
        try:
            c.execute('CREATE TABLE t(agg1 INT, agg2 DECIMAL(7,2), agg3 DECIMAL(7,2), agg4 DECIMAL(7,2))')
            c.execute('INSERT INTO t VALUES (1,1.23,-1.23,0.01),(2,1.24,-1.24,0.02),(NULL,NULL,NULL,NULL)')
            exprs = ['AVG(agg1) AS a'+str(i) for i in range(3)]
            exprs += ['AVG(agg'+str(i)+') AS d'+str(i)+'_'+str(j) for i in (2,3,4) for j in range(3)]
            _, body, counts, changes = approved.normalize('SELECT '+','.join(exprs)+' FROM t', '27a')
            self.assertEqual(counts['avg'], 12); self.assertEqual(changes['decimal_avg_cast'], 9)
            row = c.execute(body).fetchone()
            self.assertEqual(row[:3], (1.5,1.5,1.5))
            self.assertTrue(all(isinstance(x, float) for x in row[:3]))
            self.assertEqual(row[3:], (Decimal('1.235'),)*3+(Decimal('-1.235'),)*3+(Decimal('0.015'),)*3)
            c.execute('CREATE TABLE d(v DECIMAL(12,2))'); c.execute('INSERT INTO d VALUES (1.00),(2.00),(2.00),(NULL)')
            sql = 'SELECT '+','.join('AVG(v) AS a'+str(i) for i in range(35))+' FROM d'
            _, body, _, _ = approved.normalize(sql, '18a')
            self.assertEqual(c.execute(body).fetchone(), (Decimal('1.666667'),)*35)
        finally: c.close()

    def test_rounded_division_before_rank_changes_peer_groups(self):
        import duckdb
        # Three divisions matches the reviewed 36a AST, arbitrary ordinary inputs.
        sql = '''SELECT x, RANK() OVER (ORDER BY margin) r FROM
        (SELECT 1 x, a/b margin FROM t UNION ALL SELECT 2, c/d FROM t
         UNION ALL SELECT 3, e/f FROM t) v ORDER BY x'''
        _, body, _, changes = approved.normalize(sql, '36a')
        self.assertEqual(changes['decimal_division_cast'], 3)
        c = duckdb.connect()
        try:
            c.execute('CREATE TABLE t(a DOUBLE,b DOUBLE,c DOUBLE,d DOUBLE,e DOUBLE,f DOUBLE)')
            c.execute('INSERT INTO t VALUES (1.000000000001,1,1.000000000002,1,2,1)')
            self.assertEqual(c.execute(body).fetchall(), [(1,1),(2,1),(3,3)])
            self.assertEqual(c.execute(sql).fetchall(), [(1,1),(2,2),(3,3)])
        finally: c.close()


@unittest.skipUnless(os.environ.get('SHIROKUMA_TPCDS_APPROVED_SOURCE'), 'set SHIROKUMA_TPCDS_APPROVED_SOURCE to private pinned SQL directory')
class ActualSources(unittest.TestCase):
    def test_reproduce_all_static_contracts(self):
        sources = approved.read_sources(os.environ['SHIROKUMA_TPCDS_APPROVED_SOURCE'])
        entries, rules, bodies = approved.compile_sources(sources, approved.metadata(), lambda:None, time.monotonic()+300)
        self.assertEqual(len(entries), 14)
        self.assertEqual(set(bodies), set(approved.IDS))

    @unittest.skipUnless(os.environ.get('SHIROKUMA_TPCDS_APPROVED_DATASET') and os.environ.get('SHIROKUMA_TPCDS_APPROVED_SPARK_REFERENCE'), 'set SHIROKUMA_TPCDS_APPROVED_DATASET and SHIROKUMA_TPCDS_APPROVED_SPARK_REFERENCE to independent oracle bundle')
    def test_full_original_spark_qualification(self):
        with tempfile.TemporaryDirectory(prefix='approved-', dir=os.environ.get('SHIROKUMA_TPCDS_APPROVED_OUTPUT_DIR')) as tmp:
            camp = Campaign.create(Path(tmp)/'campaign')
            baseline = copy.deepcopy(camp.state['baseline'])
            out = approved.prepare_approved(camp, os.environ['SHIROKUMA_TPCDS_APPROVED_DATASET'], 'plan', os.environ['SHIROKUMA_TPCDS_APPROVED_SOURCE'], os.environ['SHIROKUMA_TPCDS_APPROVED_SPARK_REFERENCE'])
            plan = json.loads((out/'plan.json').read_text())
            self.assertEqual(plan['status'], 'SUCCESS'); self.assertEqual(plan['query_count'], 14)
            accepted = json.loads((out/'queries.json').read_text())
            approved.validate_manifest(accepted)
            approved.validate_bound_manifest(ds.bind_snapshots(accepted, pins_for(accepted)))
            self.assertTrue(all(q['spark_reference']['comparisons'] == ['source','source_cast','portable'] for q in plan['queries']))
            camp.refresh(); self.assertEqual(camp.state['baseline'], baseline)
            self.assertFalse((out/'parity.duckdb').exists()); self.assertFalse((out/'spill').exists())


if __name__ == '__main__': unittest.main()
