"""No runtime: source/oracle binding, a single sealed snapshot, and owned cleanup."""
from copy import deepcopy
import unittest

from tools.local_experiment.spark_benchmark import table as T
from tools.local_experiment.spark_benchmark import workload as W
from tools.local_experiment.iceberg_sandbox import run as SB

TOKEN = 'a' * 32
UUID = '12345678-1234-4321-8123-123456789abc'
SNAPSHOT = 8088807033386546123
SOURCE = [[1, 1, 'N', 'O', '10.00', '100.00', '0.06', '0.02', '1994-01-02'],
          [2, 1, 'R', 'F', '20.00', '200.00', '0.05', '0.01', '1993-01-02']]


def dataset(rows=SOURCE):
    oracle = W.DatasetOracle()
    for row in rows: oracle.add(row)
    return oracle.finish()


def metadata():
    scope = T.names(TOKEN)
    fields = [{'id': i+1, 'name': name, 'required': False, 'type': kind} for i, (name, kind) in
              enumerate(zip(W.SOURCE_COLUMNS, ['long', 'int', 'string', 'string', 'decimal(12, 2)',
                        'decimal(15, 2)', 'decimal(3, 2)', 'decimal(3, 2)', 'date']))]
    return {'config': {'credential': 'private-never-retain'}, 'metadata-location': scope['location']+'/metadata/00001-'+UUID+'.metadata.json',
            'metadata': {'table-uuid': UUID, 'format-version': 2, 'location': scope['location'],
                'properties': {'comment': scope['marker']}, 'current-schema-id': 0,
                'schemas': [{'type':'struct','schema-id':0,'fields':fields}], 'default-spec-id':0,
                'partition-specs':[{'spec-id':0,'fields':[]}], 'current-snapshot-id':SNAPSHOT,
                'snapshots':[{'snapshot-id':SNAPSHOT,'summary':{'operation':'append','total-records':'2','total-data-files':'1'}}],
                'refs':{'main':{'type':'branch','snapshot-id':SNAPSHOT}}}}


class FakeAdmin:
    def __init__(self): self.value = metadata(); self.calls = []
    def request(self, method, path):
        self.calls.append((method,path))
        if method != 'GET' or path != T.names(TOKEN)['path']: raise AssertionError('unexpected REST call')
        return deepcopy(self.value)


class FakeClient:
    def __init__(self, *, control=False, shared=None):
        self.user = SB.run_user(TOKEN, control)
        self.calls = []
        self.shared = shared if shared is not None else {'present':False}
        self.source = deepcopy(SOURCE)
        self.copied = deepcopy(SOURCE)
        self.raw_types = list(W.RAW_SOURCE_COLUMN_TYPES)
        self.validation = [2,0,0]
        self.create_error = None
        self.bad_measurement = False
        self.file_size = 1024
        self.spaced_types = False

    def result(self, columns, types, rows):
        if self.spaced_types:
            types = [kind.replace(',', ', ') if kind.startswith('decimal(') else kind for kind in types]
        return {'query_id':'query_'+str(len(self.calls)), 'columns':list(columns), 'column_types':list(types),
                'rows':deepcopy(rows), 'elapsed_ns':12345, 'stats':{'cpuTimeMillis':None, 'state':'FINISHED'}}

    def query(self, sql):
        self.calls.append(sql)
        if sql == 'SHOW TABLES FROM iceberg.local_experiment':
            return self.result(['Table'],['varchar'],[[T.names(TOKEN)['name']]] if self.shared['present'] else [])
        if sql.startswith('SHOW COLUMNS'):
            return self.result(['Column','Type','Extra','Comment'],['varchar']*4,
                               [[name,kind,'',''] for name,kind in zip(W.SOURCE_COLUMNS,self.raw_types)])
        if sql == W.source_validation_sql():
            return self.result(W.VALIDATION_COLUMNS,W.VALIDATION_COLUMN_TYPES,[self.validation])
        if sql.startswith('CREATE TABLE '):
            if self.create_error: raise self.create_error
            self.shared['present'] = True
            return self.result([],[],[])
        if '$files' in sql:
            return self.result(['file_path','record_count','file_size_in_bytes'],['varchar','bigint','bigint'],
                               [[T.names(TOKEN)['location']+'/data/0001.parquet',2,self.file_size]])
        if sql.startswith('DROP TABLE '):
            self.shared['present'] = False
            return self.result([],[],[])
        for case in W.CASES:
            if sql == W.query_sql('trino',case,TOKEN,SNAPSHOT):
                rows = dataset()['expected'][case]['rows']
                if self.bad_measurement:
                    rows = deepcopy(rows); rows[0][-1] = 999 if case == 'q1_sums' else '1.000000'
                return self.result(W.RESULT_COLUMNS[case],W.RESULT_COLUMN_TYPES[case],rows)
        raise AssertionError('Unexpected fixed SQL')

    def stream(self, sql, consume, *, expected_columns, expected_types):
        self.calls.append(sql)
        if tuple(expected_columns) != W.SOURCE_COLUMNS or tuple(expected_types) != W.SOURCE_COLUMN_TYPES:
            raise AssertionError('schema not bound')
        if sql == W.source_rows_sql(): values = self.source
        elif sql == W.table_rows_sql(TOKEN,SNAPSHOT): values = self.copied
        else: raise AssertionError('snapshot not bound')
        for row in values: consume(deepcopy(row))
        return self.result(W.SOURCE_COLUMNS,W.SOURCE_COLUMN_TYPES,[]) | {'row_count':len(values)}


class TableTests(unittest.TestCase):
    def setup_table(self, progress=lambda:None):
        worker = FakeClient()
        control = FakeClient(control=True, shared=worker.shared)
        admin = FakeAdmin()
        return T.HostTable(TOKEN, worker, control, admin, progress), worker, control, admin

    def test_prepare_binds_raw_source_decimal_dataset_snapshot_and_files(self):
        host,worker,_,admin = self.setup_table()
        sealed = host.prepare()
        self.assertEqual(set(sealed), {'identity','data_files','dataset'})
        self.assertEqual(sealed['dataset'],dataset())
        self.assertEqual(sealed['identity']['snapshot_id'],SNAPSHOT)
        self.assertEqual(sealed['identity']['marker'],W.marker(TOKEN))
        self.assertEqual(sealed['identity']['table_uuid'],UUID)
        self.assertEqual(sealed['data_files'],[{'path':T.names(TOKEN)['location']+'/data/0001.parquet',
                                              'record_count':2,'file_size_in_bytes':1024}])
        creates = [sql for sql in worker.calls if sql.startswith('CREATE')]
        self.assertEqual(len(creates),1)
        self.assertIn("COMMENT '"+W.marker(TOKEN)+"'",creates[0])
        self.assertIn(W.source_select_sql(),creates[0])
        self.assertIn(W.table_rows_sql(TOKEN,SNAPSHOT),worker.calls)
        self.assertNotIn('private-never-retain',repr(host.receipt()))
        with self.assertRaisesRegex(T.TableError,'RETRY_FORBIDDEN'): host.prepare()

    def test_source_type_or_nonfinite_validation_reject_before_create(self):
        for field in ('schema','numeric','identity','count','bool'):
            host,worker,_,_ = self.setup_table()
            if field=='schema': worker.raw_types[4]='decimal(12,2)'
            if field=='numeric': worker.validation[2]=1
            if field=='identity': worker.validation[1]=1
            if field=='count': worker.validation[0]=0
            if field=='bool': worker.validation[1]=False
            with self.subTest(field=field),self.assertRaises(T.TableError): host.prepare()
            self.assertFalse(any(sql.startswith('CREATE') for sql in worker.calls))
            self.assertTrue(host.cleanup(writers_stopped=True,queries_drained=True)['verified'])

    def test_source_stream_order_or_float_is_not_silently_cast(self):
        for bad in ('order','float'):
            host,worker,_,_ = self.setup_table()
            if bad=='order': worker.source.reverse()
            else: worker.source[0][4]=10.0
            with self.subTest(bad=bad),self.assertRaises(W.WorkloadError): host.prepare()
            self.assertFalse(any(sql.startswith('CREATE') for sql in worker.calls))

    def test_same_count_ctas_different_values_cannot_seal_but_known_uuid_can_clean(self):
        host,worker,control,_ = self.setup_table()
        worker.copied[0][5]='101.00'
        with self.assertRaisesRegex(T.TableError,'CTAS_DATASET_ORACLE_MISMATCH'): host.prepare()
        self.assertIsNone(host.receipt()['sealed'])
        self.assertTrue(host.cleanup(writers_stopped=True,queries_drained=True)['verified'])
        self.assertEqual([s for s in control.calls if s.startswith('DROP')],['DROP TABLE '+W.table_name(TOKEN)])

    def test_unknown_create_and_uuid_mismatch_never_drop_or_retry(self):
        host,worker,control,_ = self.setup_table()
        worker.create_error=TimeoutError('private')
        with self.assertRaises(TimeoutError): host.prepare()
        with self.assertRaisesRegex(T.TableError,'TABLE_CREATE_UNCONFIRMED'):
            host.cleanup(writers_stopped=True,queries_drained=True)
        self.assertFalse(control.calls)
        self.assertEqual(host.receipt()['pending'],'create')
        with self.assertRaises(T.TableError): host.prepare()
        host,_,control,admin = self.setup_table(); host.prepare()
        admin.value['metadata']['table-uuid']='12345678-1234-4321-8123-123456789abd'
        with self.assertRaisesRegex(T.TableError,'TABLE_UUID_CHANGED'):
            host.cleanup(writers_stopped=True,queries_drained=True)
        self.assertFalse(control.calls)

    def test_ctas_intent_persist_failure_precedes_create(self):
        host,worker,_,_ = self.setup_table()
        def fail():
            if host.receipt()['pending']=='create': raise OSError('private write failure')
        host.progress=fail
        with self.assertRaises(OSError):host.prepare()
        self.assertFalse(any(s.startswith('CREATE') for s in worker.calls))

    def test_measure_eight_samples_oracle_stats_and_snapshot_binding(self):
        host,worker,_,_ = self.setup_table(); sealed=host.prepare()
        samples=host.measure()
        self.assertEqual([(s['case_id'],s['sample_index']) for s in samples],[(c,i) for c in W.CASES for i in W.SAMPLE_INDICES])
        self.assertEqual(len(samples),8)
        for sample in samples:
            self.assertEqual(sample['engine'],'trino')
            self.assertEqual(sample['elapsed_ns'],12345)
            self.assertEqual(sample['result'],sealed['dataset']['expected'][sample['case_id']])
            self.assertIsNone(sample['stats']['cpuTimeMillis'])
            self.assertIn('query_',sample['query_id'])
        self.assertEqual(host.guard_seal(),sealed)
        with self.assertRaisesRegex(T.TableError,'MEASUREMENT_RETRY_FORBIDDEN'):host.measure()

    def test_measurement_wrong_result_or_file_drift_is_visible_partial_failure(self):
        host,worker,_,_=self.setup_table();host.prepare();worker.bad_measurement=True
        with self.assertRaisesRegex(T.TableError,'MEASUREMENT_ORACLE_MISMATCH'):host.measure()
        self.assertEqual(host.receipt()['measurements'],[])
        host,worker,_,_=self.setup_table();host.prepare();worker.file_size=1025
        with self.assertRaisesRegex(T.TableError,'SEALED_DATA_FILES_CHANGED'):host.measure()
        self.assertFalse(any(s==W.query_sql('trino','q1_sums',TOKEN,SNAPSHOT) for s in worker.calls))

    def test_spaced_actual_trino_measurement_types_match_same_decimal_oracle(self):
        host,worker,_,_=self.setup_table()
        worker.spaced_types = True
        sealed = host.prepare()
        samples = host.measure()
        self.assertEqual(len(samples), 8)
        for sample in samples:
            self.assertEqual(sample['result'], sealed['dataset']['expected'][sample['case_id']])

    def test_metadata_drift_and_cleanup_prerequisites_stop_writes(self):
        host,_,control,admin=self.setup_table();host.prepare()
        with self.assertRaisesRegex(T.TableError,'NOT_DRAINED'):host.cleanup(writers_stopped=False,queries_drained=True)
        self.assertFalse(control.calls)
        admin.value['metadata']['properties']['new-property']='changed'
        with self.assertRaisesRegex(T.TableError,'SEALED_TABLE_CHANGED'):host.guard_seal()
        self.assertTrue(host.cleanup(writers_stopped=True,queries_drained=True)['verified'])
        with self.assertRaisesRegex(T.TableError,'DROP_RETRY_FORBIDDEN'):host.cleanup(writers_stopped=True,queries_drained=True)

    def test_metadata_schema_refs_path_and_file_bounds(self):
        for key in ('schema','refs','snapshot_bool','location','owner','format','parent'):
            value=metadata();meta=value['metadata']
            if key=='schema':meta['schemas'][0]['fields'][4]['type']='double'
            if key=='refs':meta['refs']['extra']={'type':'tag','snapshot-id':SNAPSHOT}
            if key=='snapshot_bool':meta['current-snapshot-id']=True
            if key=='location':meta['location']+='/../retained'
            if key=='owner':meta['properties']['comment']='human'
            if key=='format':meta['format-version']=True
            if key=='parent':meta['snapshots'][0]['parent-snapshot-id']=123
            with self.subTest(key=key),self.assertRaises(T.TableError):T.project(value,TOKEN)
        host,worker,_,_=self.setup_table();worker.file_size=W.MAX_SOURCE_BYTES+1
        with self.assertRaisesRegex(T.TableError,'DATA_FILES_INVALID'):host.prepare()

    def test_cancel_remains_original_when_failure_receipt_save_also_fails(self):
        host,worker,_,_=self.setup_table()
        cancellation=KeyboardInterrupt()
        worker.create_error=cancellation
        def save():
            if host.journal['steps'] and host.journal['steps'][-1]['result']=='failed-or-unknown':raise OSError('secondary')
        host.progress=save
        with self.assertRaises(KeyboardInterrupt) as result:host.prepare()
        self.assertIs(result.exception,cancellation)


if __name__ == '__main__':
    unittest.main()
