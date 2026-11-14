"""Offline contract tests; opt-in real Parquet parity uses the isolated venv."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from benchmarks.local_perf import tpch_sql as tpch
from benchmarks.local_perf.capacity import Campaign


def synthetic_manifest():
    # Binding tests need no DuckDB extension SQL or downloaded test assets.
    rules = tpch.contracts()
    source = dict(format=1, dataset_id='test/source', queries=[
        dict(id=str(i), sql='SELECT ' + str(i), sql_sha256=tpch.digest('SELECT ' + str(i)))
        for i in range(1, 23)])
    with patch.object(tpch, 'portable', side_effect=lambda sql, rule: 'SELECT ' + rule['id']):
        manifest = tpch.logical_manifest(source)
    return manifest, rules


def pins_for(manifest):
    ctx = dict(database_id='db', schema_id='schema', warehouse_id='LOCAL', sql_catalog='iceberg',
               sql_schema='bench', selected_role='admin', binding_revision=1, generation_id='g', grant_revision=1)
    return dict(format=1, dataset_id=manifest['source_dataset_id'], context=ctx, tables=[
        dict(name=t, physical_name='run_' + t, snapshot_id='123',
             ref=dict(database_id='db', schema_id='schema'), table_uuid='uuid-' + t) for t in tpch.TABLES])


class Contracts(unittest.TestCase):
    def test_all_contracts_static(self):
        rules = tpch.contracts()
        self.assertEqual(len(rules), 22)
        text = Path(tpch.__file__).with_name('tpch_contracts.json').read_text()
        for forbidden in ('/tmp/', '/Users/', '/var/', 'dataset_id', 'source_result_rows'):
            self.assertNotIn(forbidden, text)
        self.assertEqual({k for k, v in rules.items() if v['limit']}, {'2', '3', '10', '18', '21'})

    def test_malformed_order_rejected(self):
        original = json.loads(Path(tpch.__file__).with_name('tpch_contracts.json').read_text())
        for change in (dict(index=-1), dict(index=True), dict(descending=1), dict(nulls='first'), dict(extra=True)):
            bad = copy.deepcopy(original)
            bad['queries'][0]['order_by'][0].update(change)
            with self.subTest(change=change), patch.object(Path, 'read_text', return_value=json.dumps(bad)):
                with self.assertRaisesRegex(ValueError, 'ordering'):
                    tpch.contracts()

    def test_source_hash_and_count_guards(self):
        with self.assertRaisesRegex(ValueError, 'canonical source'):
            tpch.portable('SELECT 1', tpch.contracts()['1'])
        with self.assertRaisesRegex(ValueError, 'occurrence'):
            tpch.replace('a a', 'a', 'b')
        manifest, _ = synthetic_manifest()
        source = dict(format=1, dataset_id='x', queries=[dict(id=q['id'], sql=q['source_sql'],
                        sql_sha256='bad') for q in manifest['queries']])
        with self.assertRaisesRegex(ValueError, 'fingerprint'):
            tpch.logical_manifest(source)
        source['queries'].pop()
        with self.assertRaisesRegex(ValueError, 'complete source'):
            tpch.logical_manifest(source)

    def test_projection_ordinals_names_and_order(self):
        rule = tpch.contracts()['18']
        sql = tpch.projection('SELECT x;\n', rule)
        self.assertIn('AS "sum(l_quantity)"', sql)
        self.assertIn('AS result(c0, c1, c2, c3, c4, c5)', sql)
        self.assertTrue(sql.endswith('ORDER BY 5 DESC NULLS LAST, 4 ASC NULLS LAST'))
        self.assertNotIn('x;', sql)

    def test_numeric_rewrites(self):
        examples = {
            '1': 'avg(l_quantity), avg(l_extendedprice), avg(l_discount)',
            '8': "    sum(\nCASE WHEN nation = 'BRAZIL' THEN volume ELSE 0 END) / sum(volume)",
            '12': '    sum(\nx END) AS high_line_count,    sum(\nx END) AS low_line_count',
            '14': '100.00 * sum(x END) / sum(l_extendedprice * (1 - l_discount))',
            '15': 'GROUP BY\n        supplier_no',
            '17': 'sum(l_extendedprice) / 7.0 + avg(l_quantity)',
            '22': 'avg(c_acctbal)',
        }
        for nr, sql in examples.items():
            rule = copy.deepcopy(tpch.contracts()[nr])
            rule['source_sql_sha256'] = tpch.digest(sql)
            rendered = tpch.portable(sql, rule)
            if nr in ('1', '17', '22'):
                self.assertIn('avg(CAST(', rendered)
            if nr in ('8', '14'):
                self.assertIn('AS DOUBLE) / CAST(sum(', rendered)
            if nr == '12':
                self.assertEqual(rendered.count('END) AS BIGINT)'), 2)
            if nr == '15':
                self.assertIn('GROUP BY\n        l_suppkey', rendered)
            if nr == '17':
                self.assertIn('CAST(sum(l_extendedprice) AS DOUBLE) / 7.0', rendered)

    def test_limit_ties_and_audit(self):
        con = Mock()
        rule = dict(limit=2, order_by=[dict(index=0)])
        con.execute.return_value.fetchmany.return_value = [(1, 'a'), (2, 'b'), (2, 'c')]
        with self.assertRaisesRegex(ValueError, 'boundary tie'):
            tpch.audit_limit(con, 'SELECT x LIMIT 2;', rule)
        con.execute.return_value.fetchmany.return_value = [(1,), (2,), (3,)]
        audit = tpch.audit_limit(con, 'SELECT x LIMIT 2;', rule)
        self.assertFalse(audit['boundary_tie'])
        self.assertEqual(audit['executed_sql_sha256'], tpch.digest('SELECT x LIMIT 3'))

    def test_raw_bigint_range_checked(self):
        con = Mock()
        con.execute.return_value.description = [('sum', 'HUGEINT')]
        con.execute.return_value.fetchmany.side_effect = [[(2 ** 63,)], []]
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, 'range'):
                tpch._stream(con, 'SELECT sum(x)', Path(root) / 'rows',
                             [dict(name='count', type='bigint')], {}, lambda: None)

    def test_failed_prepare_records_owned_status(self):
        manifest, _ = synthetic_manifest()
        source = dict(format=1, dataset_id='test/source', queries=[])
        meta = dict(status='SUCCESS', suite='tpch', generator_version='1.4.3',
                    dataset_id='test/source', tables=[dict(name=t) for t in tpch.TABLES])
        with tempfile.TemporaryDirectory() as root:
            dataset = Path(root) / 'source'
            dataset.mkdir()
            (dataset / 'dataset.json').write_text(json.dumps(meta))
            (dataset / 'queries.json').write_text(json.dumps(source))
            campaign = Campaign.create(Path(root) / 'campaign')
            with patch.object(tpch, 'logical_manifest', return_value=manifest), \
                    patch.object(tpch, 'connect', side_effect=RuntimeError('engine failed')):
                with self.assertRaises(RuntimeError):
                    tpch.prepare_tpch(campaign, dataset, 'failed')
            record = json.loads((campaign.root / 'failed' / 'plan.json').read_text())
            self.assertEqual(record['status'], 'FAILED')
            campaign.refresh()
            self.assertEqual(campaign.state['owned']['failed'], dict(kind='tpch-plan', status='FAILED'))


class Binding(unittest.TestCase):
    def setUp(self):
        self.manifest, self.rules = synthetic_manifest()
        self.pins = pins_for(self.manifest)
        self.portable = patch.object(tpch, 'portable', side_effect=lambda sql, rule: 'SELECT ' + rule['id'])
        self.portable.start()
        self.addCleanup(self.portable.stop)

    def test_binding_hashes_ctes_and_immutability(self):
        before = copy.deepcopy(self.manifest)
        bound = tpch.bind_snapshots(self.manifest, self.pins)
        self.assertEqual(self.manifest, before)
        self.assertNotEqual(bound['dataset_id'], self.manifest['dataset_id'])
        for q in bound['queries']:
            self.assertEqual(q['sql'].count('FOR VERSION AS OF 123'), 8)
            self.assertIn('"iceberg"."bench"."run_nation"', q['sql'])
            self.assertEqual(q['sql_sha256'], tpch.digest(q['sql']))
            self.assertEqual(q['reference_sql'], q['logical_sql'])
            self.assertNotEqual(q['reference_sha256'], q['target_sha256'])
            self.assertFalse(q['ordered'])
        self.pins['tables'].reverse()
        self.assertEqual(tpch.bind_snapshots(self.manifest, self.pins)['dataset_id'], bound['dataset_id'])
        self.pins['tables'][0]['snapshot_id'] = '124'
        self.assertNotEqual(tpch.bind_snapshots(self.manifest, self.pins)['dataset_id'], bound['dataset_id'])

    def test_reject_invalid_pins(self):
        cases = [lambda p: p['tables'].pop(),
                 lambda p: p.update(dataset_id='other'),
                 lambda p: p['context'].update(sql_schema='bad;sql'),
                 lambda p: p['context'].pop('warehouse_id'),
                 lambda p: p['tables'][0].update(snapshot_id='１２３'),
                 lambda p: p['tables'][0].update(snapshot_id=123),
                 lambda p: p['tables'][0].update(physical_name='x.y'),
                 lambda p: p['tables'][0].update(physical_name='run_lineitem'),
                 lambda p: p['tables'][0]['ref'].update(database_id='other'),
                 lambda p: p['tables'][0]['ref'].update(schema_id='other'),
                 lambda p: p['tables'][0]['ref'].update(sql_catalog='other')]
        for mutate in cases:
            pins = copy.deepcopy(self.pins)
            mutate(pins)
            with self.subTest(pins=pins), self.assertRaises(ValueError):
                tpch.bind_snapshots(self.manifest, pins)

    def test_context_and_sql_tampering(self):
        for field in ('logical_sql', 'logical_sha256', 'source_sha256'):
            manifest = copy.deepcopy(self.manifest)
            manifest['queries'][0][field] = 'changed'
            with self.subTest(field=field), self.assertRaises(ValueError):
                tpch.bind_snapshots(manifest, self.pins)
        self.manifest['context'] = dict(self.pins['context'], generation_id='other')
        with self.assertRaisesRegex(ValueError, 'context mismatch'):
            tpch.bind_snapshots(self.manifest, self.pins)


@unittest.skipUnless(os.environ.get('TPC_H_SOURCE'), 'set TPC_H_SOURCE for existing read-only SF dataset')
class RealParity(unittest.TestCase):
    def test_all_22_and_source_unchanged(self):
        source = Path(os.environ['TPC_H_SOURCE'])
        def fingerprints():
            result = {}
            for p in source.iterdir():
                if p.is_file():
                    with p.open('rb') as stream:
                        result[p.name] = hashlib.file_digest(stream, 'sha256').hexdigest()
            return result
        before = fingerprints()
        with tempfile.TemporaryDirectory(prefix='tpch-plan-test-') as root:
            campaign = Campaign.create(Path(root) / 'campaign')
            out = tpch.prepare_tpch(campaign, source, 'portable')
            plan = json.loads((out / 'plan.json').read_text())
            manifest = json.loads((out / 'queries.json').read_text())
            self.assertEqual(plan['status'], 'SUCCESS')
            self.assertEqual(len(plan['queries']), 22)
            self.assertEqual(len(manifest['queries']), 22)
            self.assertEqual(sum(q['limit_audit'] is not None for q in plan['queries']), 5)
            for q in plan['queries']:
                self.assertEqual(q['source']['rows'], q['portable']['rows'])
                pair = q['equivalence_contract']
                self.assertEqual(pair['source_hash'], q['source']['executed_sql_sha256'])
                self.assertEqual(pair['portable_hash'], q['portable']['executed_sql_sha256'])
            bound = tpch.bind_snapshots(manifest, pins_for(manifest))
            self.assertIn('AS nation', bound['queries'][8]['sql'])
            self.assertIn('WITH revenue AS', bound['queries'][14]['sql'])
        self.assertEqual(before, fingerprints())




class MultifilePreparation(unittest.TestCase):
    def test_v1_direct_child_guard_before_engine_sql(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            dataset = root / "source"
            dataset.mkdir()
            outside = root / "outside.parquet"
            outside.write_bytes(b"outside")
            (dataset / "linked.parquet").symlink_to(outside)
            campaign = Campaign.create(root / "campaign")
            columns = [dict(name="id", type="bigint")]
            tables = [dict(name=name, file=name + ".parquet", schema=columns) for name in tpch.TABLES]
            meta = dict(format=1, status="SUCCESS", suite="tpch", generator_version="1.4.3",
                        dataset_id="source", tables=tables)
            source_text = json.dumps(dict(format=1, dataset_id="source", queries=[]))
            (dataset / "queries.json").write_text(source_text)
            manifest = dict(format=1, dataset_id="source", source_dataset_id="source", queries=[],
                            table_schemas={t["name"]: columns for t in tables})
            con = Mock()
            con.execute.return_value.description = [("id", "BIGINT")]
            with patch.object(tpch, "logical_manifest", return_value=manifest), patch.object(tpch, "connect", return_value=con):
                for case, filename in (("traversal", "../outside.parquet"), ("symlink", "linked.parquet")):
                    with self.subTest(case=case):
                        tables[0]["file"] = filename
                        (dataset / "dataset.json").write_text(json.dumps(meta))
                        con.reset_mock()
                        with self.assertRaisesRegex(ValueError, "^dataset table must be a direct child$"):
                            tpch.prepare_tpch(campaign, dataset, case)
                        con.execute.assert_not_called()
                # Missing direct children are left for the engine to open.
                tables[0]["file"] = tables[0]["name"] + ".parquet"
                (dataset / "dataset.json").write_text(json.dumps(meta))
                con.reset_mock()
                out = tpch.prepare_tpch(campaign, dataset, "missing_direct_children")
                views = [c.args[0] for c in con.execute.call_args_list if c.args[0].startswith("CREATE VIEW")]
                self.assertEqual(views, [
                    "CREATE VIEW " + tpch.ident(t["name"]) + " AS SELECT * FROM read_parquet("
                    + tpch.quote((dataset / t["file"]).resolve()) + ")" for t in tables])
                self.assertEqual(json.loads((out / "queries.json").read_text()), manifest)
                record = json.loads((out / "plan.json").read_text())
                self.assertEqual(record["source_manifest_sha256"], tpch.digest(source_text))
                self.assertEqual(record["status"], "SUCCESS")

    def test_v2_views_check_later_schema_and_keep_query_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            dataset = root / "source"
            dataset.mkdir()
            campaign = Campaign.create(root / "campaign")
            columns = [dict(name="id", type="bigint")]
            tables = []
            for name in tpch.TABLES:
                files = []
                for i in range(2):
                    path = dataset / (name + "-" + str(i) + ".parquet")
                    path.write_bytes(b"parquet")
                    files.append(dict(file=path.name, rows=1, file_bytes=7, physical_bytes=4096, sha256="a" * 64))
                tables.append(dict(name=name, schema=columns, files=files, rows=2, file_bytes=14, physical_bytes=8192))
            meta = dict(format=2, status="SUCCESS", suite="tpch", generator_version="1.4.3", dataset_id="source", tables=tables)
            source_text = json.dumps(dict(format=1, dataset_id="source", queries=[]))
            (dataset / "dataset.json").write_text(json.dumps(meta))
            (dataset / "queries.json").write_text(source_text)
            manifest = dict(format=1, dataset_id="source", source_dataset_id="source", queries=[],
                            table_schemas={t["name"]: columns for t in tables})
            con = Mock()
            con.execute.return_value.fetchall.return_value = [("id", "BIGINT")]
            con.execute.return_value.description = [("id", "BIGINT")]
            with patch.object(tpch, "logical_manifest", return_value=manifest), patch.object(tpch, "connect", return_value=con):
                out = tpch.prepare_tpch(campaign, dataset, "good")
                record = json.loads((out / "plan.json").read_text())
                self.assertEqual(record["source_manifest_sha256"], tpch.digest(source_text))
                views = [c.args[0] for c in con.execute.call_args_list if c.args[0].startswith("CREATE VIEW")]
                self.assertEqual(len(views), len(tables))
                self.assertTrue(all("union_by_name=false" in sql and "-0.parquet" in sql and "-1.parquet" in sql for sql in views))
                con.reset_mock()
                con.execute.return_value.fetchall.side_effect = [[("id", "BIGINT")], [("id", "DOUBLE")]]
                with self.assertRaisesRegex(ValueError, "schema"):
                    tpch.prepare_tpch(campaign, dataset, "bad_schema")
                self.assertFalse(any(c.args[0].startswith("CREATE VIEW") for c in con.execute.call_args_list))
            for invalid in (True, 3, "2"):
                meta["format"] = invalid
                (dataset / "dataset.json").write_text(json.dumps(meta))
                with self.assertRaisesRegex(ValueError, "format"):
                    tpch.prepare_tpch(campaign, dataset, "invalid")


if __name__ == "__main__":
    unittest.main()
