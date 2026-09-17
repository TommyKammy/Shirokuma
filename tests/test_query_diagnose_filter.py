"""Filtered saved sources, exact observation SQL and value-free diagnosis."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.query_filter import contract as F, run as FR
from tools.local_experiment.query_diagnose import contract as D, run as DR
from tools.local_experiment.query_diagnose_report import contract as REPORT
from test_query_read_contract import schema, inventory, result
from test_query_snapshots_contract import metadata, OLD, HEAD
from test_query_diagnose_contract import source as old_source, plan_result, projections, IDS
from test_query_diagnose_snapshot_source import receipt as old_receipt
from test_query_diagnose_report_contract import documents


def source(*, current=False, empty=False, hidden=False, literal='1'):
    request = {'command':'select', 'table':'smoke.fixture_v1',
        'columns':['name'] if hidden else ['id','name'], 'limit':2,
        'snapshot_id':None if current else str(OLD), 'where':{'column':'id','literal':literal}}
    window = F.project_window(metadata(), request, schema())
    rows = [] if empty else [['match']] if hidden else [[int(literal), 'match']]
    public = F.project_rows(result(F.base_request(request), rows), request, schema(), window)
    return D.filtered_source_context('a'*32, request, inventory(), window['identity'],
        F.receipt_projection(public), IDS[0], 'b'*64, window['snapshots'])


def receipt():
    value, src = old_receipt(), source()
    value.update(schema_version=3, profile=D.FILTER_PROFILE, request=src['request'],
        request_sha256=D.digest(src['request']), source_hashes={p:'d'*64 for p in FR.sources()})
    observation = value['observation']
    observation.update(request_sha256=value['request_sha256'], selection=src['selection'])
    request = src['request']
    sql = [F.discovery_sql(request), F.schema_sql(request), D.select_sql(src), F.schema_sql(request)]
    for row, statement in zip(observation['queries']['worker'], sql):
        row['sql_sha256'] = hashlib.sha256(statement.encode()).hexdigest()
    observation['queries']['worker'][2]['row_count'] = src['selection']['fetched_row_count']
    for row in observation['runtime']['catalog']['reads']:
        row['request_sha256'] = D.digest(F.base_request(request))
    return value


def report_documents():
    src = source()
    with patch('test_query_diagnose_report_contract.snapshot_source', return_value=src):
        value, spans = documents(2)
    value['source_hashes'] = {p:'d'*64 for p in REPORT.FILTERED_29}
    return value, spans


class FilteredContractTests(unittest.TestCase):
    def test_current_and_retained_identity_stay_distinct_with_bound_predicate(self):
        for current in (False, True):
            src = source(current=current, hidden=True)
            self.assertEqual(D.validate_source(src), src)
            self.assertEqual(src['identity']['current_snapshot_id'], str(HEAD))
            self.assertEqual(src['selection']['snapshot_id'], str(HEAD if current else OLD))
            self.assertEqual(src['selection']['where'], {'column':'id','type':'bigint','operator':'eq'})
            sql = D.select_sql(src)
            self.assertIn(' WHERE ' + F.predicate_sql(src['request'], schema()), sql)
            self.assertNotIn('where', D.base_request(src))
            statements = D.statements(src)
            self.assertEqual(tuple(statements), D.PHASES)
            self.assertEqual(statements['explain'], 'EXPLAIN (TYPE DISTRIBUTED, FORMAT JSON) ' + sql)
            self.assertIn(' WHERE ' + F.predicate_sql(src['request'], schema()), statements['stats'])
            self.assertIn("FOR VERSION AS OF BIGINT '" + src['selection']['snapshot_id'] + "'", statements['stats'])
            self.assertNotIn(' LIMIT ', statements['stats'])
            self.assertNotIn(sql, [statements[k] for k in D.PHASES if k != 'explain'])

    def test_source_mutations_and_foreign_profile_rejected(self):
        for change in (lambda v:v.update(profile=D.SNAPSHOT_PROFILE),
                       lambda v:v['request']['where'].update(literal='2'),
                       lambda v:v['selection']['where'].update(type='integer'),
                       lambda v:v['selection']['where'].update(literal='1'),
                       lambda v:v['selection'].update(snapshot_id=str(HEAD)),
                       lambda v:v.update(schema_version=2)):
            src=source();change(src)
            with self.subTest(change=change), self.assertRaises(D.ContractError):D.validate_source(src)

    def test_filter_composites_check_table_snapshot_limit_and_drop_descriptors(self):
        src=source()
        for kind in ('ScanProject','ScanFilter','ScanFilterProject'):
            for constrained in (False, True):
                raw=plan_result(src);tree=json.loads(raw['rows'][0][0]);node=tree['0']['children'][0]['children'][0]
                node['name']=kind
                if constrained:node['descriptor']['table'] += ' constraint on [id]'
                node['descriptor']['table'] += ' LIMIT 3'
                node['descriptor']['filterPredicate']='private-filter-literal-sentinel'
                raw['rows'][0][0]=json.dumps(tree)
                projected=D.project_plan(raw,src)
                self.assertEqual(projected['format'],D.FILTER_PLAN_FORMAT)
                self.assertNotIn('private-filter-literal',D.canonical(projected).decode())
                for altered in ('iceberg:smoke.foreign$data@'+str(OLD),
                                'iceberg:smoke.fixture_v1$data@'+str(HEAD),
                                'iceberg:smoke.fixture_v1$data@'+str(OLD)+' constraint on [name]',
                                'iceberg:smoke.fixture_v1$data@'+str(OLD)+' constraint on [id, name]',
                                'iceberg:smoke.fixture_v1$data@'+str(OLD)+' LIMIT 4'):
                    other=deepcopy(tree);other['0']['children'][0]['children'][0]['descriptor']['table']=altered
                    with self.subTest(kind=kind,altered=altered),self.assertRaises(D.ContractError):
                        D.project_plan(dict(raw,rows=[[json.dumps(other)]]),src)

    def test_old_plan_format_does_not_accept_new_operators(self):
        src=old_source();raw=plan_result(src);tree=json.loads(raw['rows'][0][0])
        tree['0']['children'][0]['children'][0]['name']='ScanFilter'
        with self.assertRaises(D.ContractError):D.project_plan(dict(raw,rows=[[json.dumps(tree)]]),src)
        filtered=source();parts=projections(filtered);parts['plan']['format']=D.PLAN_FORMAT
        with self.assertRaises(D.ContractError):D.build_evidence(filtered,**parts)

    def test_zero_rows_values_plan_does_not_invent_a_scan(self):
        src=source(empty=True);raw=plan_result(src);tree=json.loads(raw['rows'][0][0])
        tree['0']['children'][0]['children'][0].update(name='Values',descriptor={})
        raw['rows'][0][0]=json.dumps(tree)
        actual=D.project_plan(raw,src)
        self.assertNotIn('TableScan',[x['kind'] for x in actual['operator_counts']])
        with self.assertRaises(D.ContractError):D.project_plan(raw,source())

    def test_model_and_public_summary_do_not_include_where_or_literal(self):
        src=source(literal='918273645');ev=D.build_evidence(src,**projections(src))
        summary=D.public_summary(src,ev,D.expected_diagnosis(ev))
        text=D.canonical([ev,summary]).decode()
        for value in ('918273645','literal','where','smoke.fixture_v1'):self.assertNotIn(value,text)
        self.assertEqual(summary['schema_version'],1);self.assertEqual(len(summary),10)
        self.assertFalse(summary['original_select_reexecuted'])

    def test_saved_report_is_a_distinct_group_and_retains_null_cost(self):
        value,spans=report_documents();actual=REPORT.project_run('b'*32,value,spans)
        self.assertEqual(actual['state'],'passed');self.assertEqual(actual['source_profile'],D.FILTER_PROFILE)
        self.assertIsNone(actual['model']['inference_cost_estimate'])
        self.assertNotIn('where',actual)
        value['source_hashes']={p:'d'*64 for p in REPORT.HISTORICAL_27}
        self.assertEqual(REPORT.project_run('b'*32,value,spans)['state'],'invalid')

    def test_closed_failed_report_also_binds_plan_format_to_source_profile(self):
        value,spans=report_documents();o=value['observation']
        value.update(result='failed',error_code='OBSERVATION_FAILED',model_calls=0,
                     model_evidence=None,diagnosis=None)
        o.update(state='failed',error_code='OBSERVATION_FAILED',identity_after=None,
                 snapshot_catalog_after=None,history=None,stats=None)
        del o['queries']['worker'][3:]
        o['queries']['worker'][-1].update(result='unknown',response_complete=False,error_code='QUERY_FAILED')
        o['query_drain']['observed_query_ids']=sorted(r['query_id'] for r in o['queries']['worker'])
        value['audit']['records'].pop(1);spans.pop(1)
        for row in value['audit']['records']:row['status']='failed'
        for row in spans:row['status']='ERROR'
        value['audit'].update(verified=False,saved_spans_verified=False)
        self.assertEqual(REPORT.project_run('b'*32,value,spans)['state'],'failed')
        o['plan']['format']=D.PLAN_FORMAT
        self.assertEqual(REPORT.project_run('b'*32,value,spans)['state'],'invalid')


class FilteredLeaseTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve();self.root.chmod(0o700)
        self.bases=[]
        for name in ('READ_STATE','SNAPSHOT_READ_STATE','FILTER_READ_STATE'):
            folder=self.root/name;folder.mkdir(mode=0o700);self.bases.append(folder)
            p=patch.object(DR,name,folder);p.start();self.addCleanup(p.stop)
        self.value=receipt();self.token=self.value['evidence_token']
        self.folder=self.bases[2]/self.token;self.folder.mkdir(mode=0o700)
        (self.folder/'run.lock').touch(mode=0o600)
        self.path=self.folder/'summary.json';self.write()

    def write(self):
        self.path.write_bytes(D.canonical(self.value));self.path.chmod(0o600)

    def test_filtered_saved_source_is_verified_without_changing_source(self):
        raw=self.path.read_bytes();fingerprint=DR.fingerprint(self.path.stat())
        with DR.SourceLease(self.token) as lease:
            self.assertEqual(lease.source['profile'],D.FILTER_PROFILE);lease.unchanged()
        self.assertEqual(self.path.read_bytes(),raw);self.assertEqual(DR.fingerprint(self.path.stat()),fingerprint)

    def test_wrong_folder_profile_query_or_literal_rejected_before_environment(self):
        initial=deepcopy(self.value)
        mutations=(lambda v:v.update(profile=D.SNAPSHOT_PROFILE),
                   lambda v:v['request']['where'].update(literal='2'),
                   lambda v:v['observation']['queries']['worker'][2].update(sql_sha256='f'*64))
        for index,change in enumerate(mutations):
            self.value=deepcopy(initial);change(self.value);self.write()
            with self.subTest(index=index),self.assertRaises(DR.DiagnoseError):
                with DR.SourceLease(self.token):pass
        self.value=initial;self.write();self.folder.rename(self.bases[1]/self.token)
        with self.assertRaises(DR.DiagnoseError):
            with DR.SourceLease(self.token):pass

    def test_collision_across_any_two_source_profiles_has_no_fallback(self):
        (self.bases[0]/self.token).mkdir(mode=0o700)
        with self.assertRaisesRegex(DR.DiagnoseError,'SOURCE_INVALID'):
            with DR.SourceLease(self.token):pass


if __name__=='__main__':unittest.main()
