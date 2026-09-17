from copy import deepcopy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.local_experiment.spark import contract as N
from tools.local_experiment.spark_iceberg import remote_contract as REMOTE
from tools.local_experiment.spark_benchmark import contract as C, workload as W, report as R

TOKEN = 'a' * 32
SNAPSHOT = 8013311425425878260
TABLE_UUID = '242d28cd-9200-484e-9569-bdfe437d571d'
START = '2026-09-17T13:00:00Z'
END = '2026-09-17T13:05:00Z'


def save(path, value):
    path.write_bytes(R.canonical(value))
    path.chmod(0o600)


def fixture_documents(token=TOKEN):
    """Pure synthetic terminal evidence, usable by the fake-SDK importer tests."""
    oracle = W.DatasetOracle()
    oracle.add([1, 1, 'N', 'O', '10.00', '100.00', '0.06', '0.02', '1994-06-01'])
    identity = {'table_uuid': TABLE_UUID, 'table_location': REMOTE.table_location(token),
                'marker': C.marker(token), 'snapshot_id': SNAPSHOT, 'parent_snapshot_id': None,
                'metadata_location': REMOTE.table_location(token) + '/metadata/00000-test.metadata.json',
                'operation': 'append'}
    sealed = {'identity': identity, 'data_files': [{'path': REMOTE.table_location(token) + '/data/one.parquet',
                'record_count': 1, 'file_size_in_bytes': 1024}], 'dataset': oracle.finish()}
    inputs = {'build_token': 'b'*32, 'image': REMOTE.IMAGE, 'table_uuid': TABLE_UUID,
              'table_location': REMOTE.table_location(token), 'initial_snapshot_id': SNAPSHOT,
              'polaris_uri': REMOTE.POLARIS_URI, 'polaris_realm': 'POLARIS', 'warehouse': 'shirokuma_l1',
              'scope': 'PRINCIPAL_ROLE:spark_' + token, 's3_endpoint': REMOTE.S3_ENDPOINT,
              's3_region': 'us-east-1', 'data_egress': deepcopy(REMOTE.DATA_EGRESS), 'sealed': sealed}
    stats = {k: 0 for k in R.STATS}; stats.update(state='FINISHED', physicalInputBytes=None)
    values, queries = {}, []
    for engine in W.ENGINES:
        values[engine] = []
        for case_index, case in enumerate(W.CASES):
            for index in W.SAMPLE_INDICES:
                qid = f'20260917_130000_{case_index*4+index:05d}_abcde' if engine == 'trino' else None
                row = {'engine': engine, 'case_id': case, 'sample_index': index,
                       'sql_sha256': hashlib.sha256(W.query_sql(engine, case, token, SNAPSHOT).encode()).hexdigest(),
                       'elapsed_ns': [20_000_001, 9_000_003, 15_000_005, 7_000_007][index],
                       'result': deepcopy(sealed['dataset']['expected'][case]), 'query_id': qid,
                       'stats': deepcopy(stats) if engine == 'trino' else None}
                values[engine].append(row)
                if engine == 'trino':
                    queries.append({'query_id': qid, 'result': 'passed', 'response_complete': True,
                                    'sql_sha256': row['sql_sha256'], 'elapsed_ns': row['elapsed_ns'],
                                    'stats': deepcopy(row['stats']), 'started_at': START})
    clean = {'verified': True, 'action': 'same-uuid-drop', 'table_uuid': TABLE_UUID}
    service = {'name': 'polaris', 'namespace': 'shirokuma-dev',
               'uid': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'spec_sha256': 'c'*64}
    access = {'schema_version': 1, 'run_token': token, 'polaris_access': 'read-only',
              'grants': ['TABLE_LIST', 'TABLE_READ_PROPERTIES'], 'pending': None,
              'entities': {}, 'bindings': [], 'policies': {}, 'events': [{'action': 'create-entity'}],
              'cleanup': {'verified': True, 'removed': [], 'deferred': []},
              'table': {'table_uuid': TABLE_UUID, 'table_location': REMOTE.table_location(token)}}
    spark = {'schema_version': 1, 'run_token': token, 'namespace': N.namespace(token), 'scope': C.SCOPE,
             'mode': 'success', 'configured_image': REMOTE.IMAGE, 'workload_id': W.WORKLOAD_ID,
             'result': 'passed', 'spark_version': '4.1.3', 'iceberg_version': '1.11.0',
             'master': 'k8s://https://kubernetes.default.svc:443', 'startup_ns': 4_000_000_001,
             'checks': {key: True for key in C.CHECKS}, 'measurements': values['spark'],
             'sealed_before': {k: deepcopy(sealed[k]) for k in ('identity', 'data_files')},
             'sealed_after': {k: deepcopy(sealed[k]) for k in ('identity', 'data_files')},
             'partitions': [{'partition_id': i, 'stage_id': 20, 'task_attempt_id': 25+i, 'attempt_number': 0,
                             'executor_id': '1', 'hostname': N.executor_name(token)} for i in range(2)],
             'error_code': None, 'error_classes': []}
    pods, known, resources = {}, [], {}
    documents = {}
    for i, role in enumerate(('driver', 'executor'), 1):
        uid = f'00000000-0000-4000-8000-{i:012d}'
        name = 'driver' if role == 'driver' else N.executor_name(token)
        pods[role] = {'kind': 'Pod', 'role': role, 'name': name, 'uid': uid}
        known.append({'kind': 'Pod', 'name': name, 'uid': uid})
        resources[role] = {'requests': {'cpu': '500m' if role == 'driver' else '1', 'memory': '2Gi',
                                        'ephemeral-storage': '256Mi'},
                           'limits': {'cpu': '1', 'memory': '2Gi', 'ephemeral-storage': '2Gi'}}
        documents['native/' + role + '-pod.json'] = {'apiVersion': 'v1', 'kind': 'Pod',
            'metadata': {'name': name, 'namespace': N.namespace(token), 'uid': uid},
            'spec': {'containers': [{'name': 'spark-' + role, 'image': REMOTE.IMAGE, 'resources': resources[role]}]}}
    documents['native/plan.json'] = [{'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': N.namespace(token)}}]
    native = {'schema_version': 1, 'run_token': token, 'profile': 'iceberg-benchmark', 'mode': 'success',
              'namespace': N.namespace(token), 'result': 'passed', 'started_at': START, 'finished_at': END,
              'workload_verified': True, 'driver_exit_code': 0, 'pending': None,
              'profile_inputs': inputs, 'output': spark, 'pods': pods, 'known': known, 'automatic_endpoints': [],
              'cleanup': {'verified': True, 'deferred': None, 'deleted': deepcopy(known)},
              'plan_sha256': R.digest(documents['native/plan.json'])}
    root = {'schema_version': 1, 'run_token': token, 'scope': C.SCOPE, 'workload_id': W.WORKLOAD_ID,
            'result': 'passed', 'started_at': START, 'finished_at': END, 'errors': [],
            'source_hashes': {key: 'd'*64 for key in R.SOURCE_PATHS}, 'limits': deepcopy(R.FIXED_ROOT_LIMITS),
            'timing_scope': deepcopy(R.TIMING_SCOPE), 'services': [service], 'profile_inputs': inputs,
            'queries': {'worker': queries, 'control': []}, 'trino_measurements': values['trino'],
            'table': {'schema_version': 1, 'run_token': token, 'created': True, 'pending': None,
                      'sealed': sealed, 'source_dataset': deepcopy(sealed['dataset']),
                      'source_validation': {'schema': [list(v) for v in zip(W.SOURCE_COLUMNS, W.RAW_SOURCE_COLUMN_TYPES)],
                                            'row_count': 1, 'invalid_identity_rows': 0, 'invalid_numeric_rows': 0},
                      'identity': {'identity': deepcopy(identity), 'record_count': 1, 'file_count': 1},
                      'measurements': values['trino'], 'cleanup': clean},
            'access': access, 'access_cleanup': access['cleanup'], 'cleanup': clean,
            'query_drain': {'verified': True, 'user': 'shirokuma-local-iceberg-' + token,
                            'active': [], 'errors': [], 'cancel_errors': []},
            'fixture_before': {'immutable': 'original'}, 'fixture_after': {'immutable': 'original'},
            'fixture_unchanged': True, 'spark_stopped': True, 'spark_output': spark,
            'spark_evidence': 'tmp/local-experiment/spark-runs/' + token, 'native_total_ns': 90_000_000_003}
    comparison_rows = []
    for case in W.CASES:
        times = {engine: [r['elapsed_ns'] for r in values[engine] if r['case_id'] == case] for engine in W.ENGINES}
        comparison_rows.append({'case_id': case, 'expected_sha256': sealed['dataset']['expected'][case]['sha256'],
                                'trino_elapsed_ns': times['trino'], 'spark_elapsed_ns': times['spark'],
                                'trino_repeat_median_ns': 9_000_003, 'spark_repeat_median_ns': 9_000_003})
    root['comparison'] = {'verified': True, 'measurements': 16, 'snapshot_id': SNAPSHOT, 'table_uuid': TABLE_UUID,
                          'row_count': 1, 'logical_sha256': sealed['dataset']['logical_sha256'], 'queries': comparison_rows}
    documents.update({'summary.json': root, 'access.json': access, 'tunnel.json': {'port': 18181, 'state': 'verified',
                        'service': service}, 'native/summary.json': native})
    return {name: deepcopy(value) for name, value in documents.items()}


def write_fixture(base, native_base, token=TOKEN, documents=None):
    """Create private synthetic files; no runtime or retained source is read."""
    base, native_base = Path(base), Path(native_base)
    for parent in (base, native_base):
        parent.mkdir(mode=0o700, exist_ok=True); parent.chmod(0o700)
    for parent in (base/token, native_base/token):
        parent.mkdir(mode=0o700, exist_ok=True); parent.chmod(0o700)
    (base/token/'run.lock').touch(mode=0o600)
    for name, value in (documents or fixture_documents(token)).items():
        parent, filename = (native_base/token, name[7:]) if name.startswith('native/') else (base/token, name)
        save(parent/filename, value)


class BenchmarkReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.parent = Path(self.tmp.name).resolve()
        self.base, self.native = self.parent/'bench', self.parent/'native'
        self.docs = fixture_documents()
        self.write()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self):
        write_fixture(self.base, self.native, documents=self.docs)

    def read(self):
        return R.read_run(TOKEN, base=self.base, native_base=self.native)

    def reject(self):
        self.write(); value = self.read()
        self.assertFalse(value['eligible'], value)
        self.assertFalse(value['groups'])
        return value

    def fail(self, result='failed'):
        self.docs['summary.json'].update(result=result, failure_code='BENCH_NATIVE_NOT_VERIFIED')

    def test_four_groups_exact_nanoseconds_int64_and_unknowns(self):
        report = R.collect(self.base, self.native)
        self.assertEqual(report['result'], 'complete')
        self.assertEqual(report['inventory']['eligible'], 1)
        self.assertEqual([(v['engine'], v['case_id']) for v in report['sources']],
                         [('trino','q1_sums'),('trino','q6'),('spark','q1_sums'),('spark','q6')])
        for v in report['sources']:
            self.assertEqual(v['snapshot_id'], str(SNAPSHOT))
            self.assertEqual([s['elapsed_ns'] for s in v['samples']], [20_000_001,9_000_003,15_000_005,7_000_007])
            self.assertEqual(v['first_ns'], 20_000_001)
            self.assertEqual(v['repeat_median_ns'], 9_000_003)
            self.assertEqual(v['observed_samples'],4)
            self.assertEqual(v['timings']['native_total_ns'], 90_000_000_003)
            self.assertIsNone(v['inference_cost_estimate']); self.assertIsNone(v['model_usage'])
            if v['engine']=='trino':
                self.assertIsNone(v['observed_version']); self.assertEqual(v['expected_version'],'483')
                self.assertEqual(v['samples'][0]['stats']['cpuTimeMillis'],0)
                self.assertIsNone(v['samples'][0]['stats']['physicalInputBytes'])
                self.assertEqual(v['samples'][0]['source_started_at'],START)
            else:
                self.assertEqual(v['observed_version'],'4.1.3')
                self.assertIsNone(v['samples'][0]['source_started_at'])

    def test_repeat_and_projection_are_pure_and_leave_source_bytes_modes_times_unchanged(self):
        paths = list(self.base.rglob('*'))+list(self.native.rglob('*'))
        def before(): return {p:(p.stat().st_mode,p.stat().st_mtime_ns,p.read_bytes() if p.is_file() else None) for p in paths}
        saved=before(); first=R.collect(self.base,self.native); second=R.collect(self.base,self.native)
        self.assertEqual(first,second);self.assertEqual(saved,before())
        row=self.read(); groups=R.projection(row);groups[0]['samples'].clear()
        self.assertEqual(len(row['groups'][0]['samples']),4)

    def test_source_hashes_are_bytes_hashes_and_change_source_identity(self):
        first=self.read()
        for name,h in first['artifact_sha256'].items():
            p=self.native/TOKEN/name[7:] if name.startswith('native/') else self.base/TOKEN/name
            self.assertEqual(h,hashlib.sha256(p.read_bytes()).hexdigest())
        self.docs['summary.json']['extra_unprojected_field']='safe-change';self.write()
        second=self.read();self.assertTrue(second['eligible'])
        self.assertEqual(first['groups'][0]['source_id'],second['groups'][0]['source_id'])
        self.assertNotEqual(first['groups'][0]['source_key'],second['groups'][0]['source_key'])

    def test_failed_source_with_completed_queries_remains_failed_not_comparable(self):
        self.fail();self.write();value=self.read()
        self.assertTrue(value['eligible'])
        self.assertTrue(all(g['measurement_complete'] for g in value['groups']))
        self.assertTrue(all(not g['comparison_eligible'] and g['source_result']=='failed' for g in value['groups']))

    def test_interrupted_partial_has_null_median_and_missing_samples_not_zero(self):
        self.fail('interrupted'); root=self.docs['summary.json']; native=self.docs['native/summary.json']
        root['table']['measurements']=root['table']['measurements'][:2]
        root.pop('trino_measurements');root.pop('spark_output');root.pop('comparison')
        native.update(result='interrupted',workload_verified=False,driver_exit_code=None,output=None)
        self.write();row=self.read();self.assertTrue(row['eligible'],row['issues'])
        a,b,c,d=row['groups'];self.assertEqual(a['observed_samples'],2);self.assertIsNone(a['repeat_median_ns'])
        for g in (b,c,d):
            self.assertEqual(g['observed_samples'],0);self.assertEqual(g['unobserved_samples'],4)
            self.assertIsNone(g['first_ns']);self.assertIsNone(g['repeat_median_ns'])
        self.assertIsNone(c['observed_version']);self.assertIsNone(c['timings']['spark_session_startup_ns'])

    def test_failed_before_seal_and_native_keeps_four_unobserved_groups(self):
        self.fail();root=self.docs['summary.json']
        for key in ('profile_inputs','spark_output','spark_evidence','comparison','native_total_ns',
                    'trino_measurements','access','access_cleanup'):
            root.pop(key,None)
        root['table']={'schema_version':1,'run_token':TOKEN,'created':False,'pending':None,
                       'measurements':[],'cleanup':{'verified':True,'action':'not-created'}}
        root['cleanup']=deepcopy(root['table']['cleanup'])
        root['queries']={'worker':[],'control':[]}
        self.docs={k:v for k,v in self.docs.items() if not k.startswith('native/') and k!='access.json'}
        for path in (self.native/TOKEN).iterdir():path.unlink()
        (self.native/TOKEN).rmdir();(self.base/TOKEN/'access.json').unlink()
        for name,value in self.docs.items():save(self.base/TOKEN/name,value)
        row=self.read();self.assertTrue(row['eligible'],row['issues'])
        for group in row['groups']:
            self.assertEqual(group['source_result'],'failed');self.assertEqual(group['observed_samples'],0)
            for key in ('snapshot_id','table_uuid','row_count','logical_sha256','expected_result_sha256','first_ns','repeat_median_ns'):
                self.assertIsNone(group[key])
            self.assertEqual(group['conditions']['resource_bounds'],{'driver':None,'executor':None,'trino':None})

    def test_failed_cleanup_remains_visible_without_verified_comparison(self):
        self.fail();root=self.docs['summary.json'];root['cleanup']['verified']=False
        self.write();value=R.collect(self.base,self.native)
        self.assertEqual(value['result'],'complete');self.assertEqual(value['inventory']['failed'],1)
        self.assertTrue(all(g['source_result']=='failed' and not g['cleanup_verified']
                            and not g['comparison_eligible'] for g in value['sources']))

    def test_running_and_terminal_without_finished_time_are_incomplete(self):
        for mode in ('running','failed'):
            self.docs=fixture_documents();self.docs['summary.json']['result']=mode
            if mode=='failed':self.docs['summary.json'].pop('finished_at')
            row=self.reject();self.assertEqual(row['integrity'],'incomplete')

    def test_lock_busy_is_visible_active_and_report_does_not_wait_or_write(self):
        with open(self.base/TOKEN/'run.lock','rb') as stream:
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            value=R.collect(self.base,self.native)
            self.assertEqual(value['inventory']['active'],1);self.assertEqual(value['sources'],[])

    def test_unknown_entries_missing_receipt_and_nonprivate_source_visible(self):
        (self.base/'unexpected').mkdir(mode=0o700)
        self.assertEqual(R.collect(self.base,self.native)['inventory']['unrecognized_entries'],1)
        (self.base/TOKEN/'summary.json').unlink()
        self.assertEqual(self.read()['integrity'],'invalid')
        self.write();(self.base/TOKEN/'summary.json').chmod(0o644)
        self.assertEqual(self.read()['issues'],['NONPRIVATE_SOURCE_FILE'])

    def test_symlinks_hardlinks_and_nonprivate_directories_rejected(self):
        path=self.base/TOKEN/'access.json';other=self.parent/'access';path.rename(other);path.symlink_to(other)
        self.assertEqual(self.read()['issues'],['UNSAFE_SOURCE_PATH'])
        path.unlink();os.link(other,path)
        self.assertEqual(self.read()['issues'],['NONPRIVATE_SOURCE_FILE'])
        path.unlink();other.rename(path);(self.native/TOKEN).chmod(0o755)
        self.assertEqual(self.read()['issues'],['NONPRIVATE_SOURCE_DIRECTORY'])

    def test_duplicate_json_nonfinite_and_oversized_files_rejected(self):
        p=self.base/TOKEN/'summary.json'
        for content in [b'{"result":1,"result":2}', b'{"value":NaN}', b'x'*(R.MAX_FILE+1)]:
            p.write_bytes(content);row=self.read();self.assertFalse(row['eligible']);self.assertEqual(row['groups'],[])

    def test_source_changed_during_read_is_incomplete(self):
        original=R.read_file;calls=0
        def change(path):
            nonlocal calls
            value=original(path);calls+=1
            if calls==7:
                p=self.base/TOKEN/'summary.json';p.write_bytes(p.read_bytes()+b'\n')
            return value
        with patch.object(R,'read_file',side_effect=change):
            self.assertEqual(self.read()['issues'],['SOURCE_CHANGED'])

    def test_inventory_change_is_not_reported_complete(self):
        original=R.read_run
        def change(*a,**kw):
            row=original(*a,**kw);(self.base/'later').mkdir(mode=0o700);return row
        with patch.object(R,'read_run',side_effect=change):
            value=R.collect(self.base,self.native)
        self.assertEqual(value['result'],'incomplete');self.assertFalse(value['inventory']['complete'])

    def test_result_oracle_hash_mismatch_rejected(self):
        self.docs['summary.json']['table']['measurements'][0]['result']['sha256']='e'*64
        self.reject()

    def test_bool_duration_precision_snapshot_and_duplicate_sample_rejected(self):
        for mutate in [lambda d:d['summary.json']['table']['measurements'][0].update(elapsed_ns=True),
                       lambda d:d['summary.json']['profile_inputs'].update(initial_snapshot_id=float(SNAPSHOT)),
                       lambda d:d['summary.json']['table']['measurements'][1].update(sample_index=0)]:
            self.docs=fixture_documents();mutate(self.docs);self.reject()

    def test_trino_journal_id_timing_stats_must_match_and_unknown_stats_rejected(self):
        for key,value in [('query_id','20260917_130000_99999_abcde'),('elapsed_ns',4),('stats',{'cpuTimeMillis':1})]:
            self.docs=fixture_documents();self.docs['summary.json']['queries']['worker'][0][key]=value;self.reject()

    def test_stats_bool_negative_float_and_extra_fields_rejected(self):
        for key,value in [('cpuTimeMillis',True),('processedRows',-1),('wallTimeMillis',1.2),('unexpected',3)]:
            self.docs=fixture_documents()
            for target in [self.docs['summary.json']['table']['measurements'][0]['stats'],
                           self.docs['summary.json']['queries']['worker'][0]['stats']]:target[key]=value
            self.reject()

    def test_cross_file_identity_and_source_code_hash_mismatch_rejected(self):
        for mutate in [lambda d:d['access.json'].update(run_token='c'*32),
                       lambda d:d['tunnel.json']['service'].update(uid='cccccccc-cccc-4ccc-8ccc-cccccccccccc'),
                       lambda d:d['native/summary.json'].update(run_token='c'*32),
                       lambda d:d['summary.json']['source_hashes'].pop(next(iter(R.SOURCE_PATHS)))]:
            self.docs=fixture_documents();mutate(self.docs);self.reject()

    def test_same_name_different_pod_uid_or_image_rejected(self):
        for mutate in [lambda d:d['native/executor-pod.json']['metadata'].update(uid='eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee'),
                       lambda d:d['native/driver-pod.json']['spec']['containers'][0].update(image='foreign:latest')]:
            self.docs=fixture_documents();mutate(self.docs);self.reject()

    def test_plan_hash_native_output_and_cleanup_uid_mismatch_rejected(self):
        for mutate in [lambda d:d['native/plan.json'].append({'unexpected':True}),
                       lambda d:d['native/summary.json']['output'].update(startup_ns=999),
                       lambda d:d['native/summary.json']['cleanup']['deleted'].pop()]:
            self.docs=fixture_documents();mutate(self.docs);self.reject()

    def test_success_rejects_missing_cleanup_drain_fixture_and_comparison(self):
        for mutate in [lambda d:d['summary.json']['cleanup'].update(verified=False),
                       lambda d:d['summary.json']['query_drain'].update(active=['unfinished']),
                       lambda d:d['summary.json'].update(fixture_unchanged=False),
                       lambda d:d['summary.json']['comparison']['queries'][0].update(trino_repeat_median_ns=1)]:
            self.docs=fixture_documents();mutate(self.docs);self.reject()

    def test_timing_claim_and_access_write_privilege_cannot_enter_report(self):
        self.docs['summary.json']['timing_scope']['trino']='pure CPU time';self.reject()
        self.docs=fixture_documents()
        self.docs['access.json']['grants'].append('TABLE_WRITE_DATA');self.reject()

    def test_unsafe_fields_and_raw_rows_are_never_projected(self):
        sentinel='private-credential-or-sql-sentinel'
        for filename in ('summary.json','native/driver-pod.json','native/plan.json'):
            doc=self.docs[filename]
            if isinstance(doc,list):doc[0]['extra']=sentinel
            else:doc['raw']=sentinel
        self.docs['native/summary.json']['plan_sha256']=R.digest(self.docs['native/plan.json'])
        self.write();value=R.collect(self.base,self.native)
        self.assertEqual(value['result'],'complete');text=json.dumps(value)
        self.assertNotIn(sentinel,text)
        self.assertNotIn('sum_disc_price',text);self.assertNotIn('INSERT INTO',text)

    def test_projection_invalid_has_no_groups_and_return_copy(self):
        self.assertEqual(R.projection({'integrity':'invalid','eligible':False,'groups':[{'secret':'x'}]}),[])


if __name__ == '__main__':
    unittest.main()
