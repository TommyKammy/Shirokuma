from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch

from tools.local_experiment.spark import contract as N, run as RUN
from tools.local_experiment.spark_branch import contract as C

TOKEN = 'a' * 32
A, B, D = 1477241556327226373, 2477241556327226373, 3477241556327226373
UUID = '242d28cd-9200-484e-9569-bdfe437d571d'


def file_row(name, count):
    return {'path':C.R.table_location(TOKEN)+'/data/'+name+'.parquet','record_count':count,'file_size_in_bytes':1024}


def snapshot(identity, parent, seq, count, files, *, hashed=False):
    value={'snapshot_id':identity,'parent_snapshot_id':parent,'sequence_number':seq,
           'operation':'append','record_count':count,'file_count':files}
    if hashed:value['sha256']='d'*64
    return value


def inputs(initial=None):
    """Shared pure valid host seal fixture for runner/table fake tests."""
    offset=1 if initial else 0
    snaps=[snapshot(A,initial,1+offset,2,1,hashed=True),snapshot(B,A,2+offset,3,2,hashed=True)]
    if initial:snaps.append(snapshot(initial,None,1,0,0,hashed=True))
    identity={'table_uuid':UUID,'table_location':C.R.table_location(TOKEN),'marker':C.R.marker(TOKEN),
              'schema_sha256':'c'*64,'metadata_location':C.R.table_location(TOKEN)+'/metadata/00003-before.metadata.json',
              'metadata_sha256':'b'*64}
    sealed={'identity':identity,'initial_snapshot_id':initial,'snapshot_a':A,'snapshot_b':B,
            'refs':C.expected_refs(A,B,A),'snapshots':sorted(snaps,key=lambda s:s['snapshot_id']),
            'data_files':{'a':[file_row('base',2)],'b':sorted([file_row('base',2),file_row('main',1)],key=lambda s:s['path'])}}
    return {'build_token':'b'*32,'image':C.R.IMAGE,'table_uuid':UUID,'table_location':C.R.table_location(TOKEN),
            'initial_snapshot_id':B,'polaris_uri':C.R.POLARIS_URI,'polaris_realm':'POLARIS','warehouse':'shirokuma_l1',
            'scope':'PRINCIPAL_ROLE:spark_'+TOKEN,'s3_endpoint':C.R.S3_ENDPOINT,'s3_region':'us-east-1',
            'data_egress':deepcopy(C.R.DATA_EGRESS),'sealed':sealed}


def output(initial=None):
    value=inputs(initial);sealed=value['sealed']
    after=deepcopy(C.safe_snapshots(sealed))+[snapshot(D,A,4 if initial else 3,3,2)]
    return {'schema_version':1,'run_token':TOKEN,'namespace':N.namespace(TOKEN),'scope':C.SCOPE,'mode':'success',
            'configured_image':C.R.IMAGE,'result':'passed','spark_version':'4.1.3','iceberg_version':'1.11.0',
            'master':'k8s://https://kubernetes.default.svc:443','checks':{k:True for k in C.CHECKS},
            'write_outcome':'verified','write_attempts':1,
            'before':{'refs':deepcopy(sealed['refs']),'snapshot_summaries':C.safe_snapshots(sealed),
                      'snapshot_files':deepcopy(sealed['data_files'])},
            'table_identity':{'table_uuid':UUID,'table_location':C.R.table_location(TOKEN),'marker':C.R.marker(TOKEN),
                 'metadata_location':C.R.table_location(TOKEN)+'/metadata/00004-after.metadata.json',
                 'main_snapshot_id':B,'tag_snapshot_id':A,'branch_snapshot_id':D,'parent_snapshot_id':A},
            'snapshot_summaries':sorted(after,key=lambda s:s['snapshot_id']),
            'snapshot_files':{'a':deepcopy(sealed['data_files']['a']),'b':deepcopy(sealed['data_files']['b']),
                              'c':sorted([file_row('base',2),file_row('branch',1)],key=lambda s:s['path'])},
            'dataframe_rows':{'main':deepcopy(C.ROWS_B),'baseline_tag':deepcopy(C.ROWS_A),'audit_branch':deepcopy(C.ROWS_C)},
            'sql_rows':{'main':deepcopy(C.ROWS_B),'baseline_tag':deepcopy(C.ROWS_A),'audit_branch':deepcopy(C.ROWS_C)},
            'groups':[[0,1,10],[1,2,60]],'row_count':3,'shuffle_observed':True,
            'partitions':[{'partition_id':i,'stage_id':20,'task_attempt_id':25+i,'attempt_number':0,
                           'executor_id':'1','hostname':N.executor_name(TOKEN)} for i in range(2)],
            'error_code':None,'error_classes':[]}


class JavaItems:
    """Deliberately not Python iterable: exercise the actual Py4J-style path."""
    def __init__(self, values):self.values=values;self.closed=False
    def iterator(self):
        index=0
        def has():return index<len(self.values)
        def next_item():
            nonlocal index
            value=self.values[index];index+=1;return value
        return NS(hasNext=has,next=next_item)
    def close(self):self.closed=True


class JobFixture:
    def __init__(self, failure=None):
        self.failure=failure;self.after=False;self.insert_count=0;self.stopped=False;self.active=None
        self.sessions=[];self.tasks=[];self.selected=[];self.sql_calls=[]
        self.seed=inputs();self.expected=output();self.table=self.make_table()
        self.spark=self.session()
        self.spark_module=ModuleType('pyspark.sql')
        self.spark_module.SparkSession=NS(builder=NS(appName=lambda _:NS(getOrCreate=lambda:self.spark)))
        self.spark_module.functions=NS(count=lambda _:NS(alias=lambda _:None),sum=lambda _:NS(alias=lambda _:None))

    def session(self):
        conf={};session=NS(version='4.1.3',_jsparkSession=object())
        self.sessions.append(conf)
        def get_active():
            active=self.active
            return NS(isDefined=lambda:active is not None,get=lambda:active)
        sessions=NS(getActiveSession=get_active,
                    setActiveSession=lambda s:setattr(self,'active',s),clearActiveSession=lambda:setattr(self,'active',None))
        jvm=NS(java=NS(lang=NS(Thread=NS(currentThread=lambda:NS(getContextClassLoader=lambda:NS(loadClass=lambda _:None))))),
               org=NS(apache=NS(spark=NS(sql=NS(SparkSession=sessions)),iceberg=NS(
                   IcebergBuild=NS(version=lambda:'1.11.0'),spark=NS(Spark3Util=NS(loadIcebergTable=lambda s,t:self.table))))))
        session._jvm=jvm
        session.conf=NS(set=lambda k,v:conf.__setitem__(k,v),unset=lambda k:conf.pop(k,None))
        session.sparkContext=NS(master='k8s://https://kubernetes.default.svc:443',
                                getConf=lambda:NS(get=lambda _:C.R.IMAGE))
        session.newSession=self.session;session.catalog=NS(clearCache=lambda:None)
        session.read=NS(format=lambda _:self.reader())
        session.sql=self.sql
        def stop():
            self.stopped=True
            if self.failure=='stop':raise RuntimeError('private-stop-secret')
        session.stop=stop
        return session

    def reader(self):
        selected={}
        def option(key,value):selected[key]=value;return reader
        def load(_):
            self.selected.append(deepcopy(selected));name=next(iter(selected.values()))
            # Iceberg 1.11.0 spark/v4.1 Spark3Util.validateNoLegacyTimeTravel
            # rejects legacy tag/snapshot-id/as-of-timestamp on the load path.
            if set(selected)&{'tag','snapshot-id','as-of-timestamp'}:
                raise ValueError('private-legacy-time-travel-error')
            if set(selected) not in ({'branch'},{'versionAsOf'}):
                raise AssertionError('unexpected read option')
            if self.failure=='read':raise RuntimeError('private-read-secret')
            return self.frame(name)
        reader=NS(option=option,load=load)
        return reader

    def frame(self,name):
        data={'main':C.ROWS_B,'baseline_tag':C.ROWS_A,'audit_branch':C.ROWS_C}[name]
        if self.failure=='rows' and name=='main':data=C.ROWS_C
        frame=NS(schema=NS(simpleString=lambda:'struct<id:int,name:string,bucket:int,amount:bigint>'))
        frame.orderBy=lambda _:frame;frame.limit=lambda _:frame;frame.collect=lambda:deepcopy(data)
        parts=deepcopy(self.expected['partitions'])
        packets=[dict(parts[0],rows=[[0,1,10]]),dict(parts[1],rows=[[1,2,60]])]
        grouped=NS(_jdf=NS(queryExecution=lambda:NS(executedPlan=lambda:NS(toString=lambda:'Exchange'))),
                   rdd=NS(mapPartitionsWithIndex=lambda _:NS(collect=lambda:deepcopy(packets))))
        frame.repartition=lambda *a:NS(groupBy=lambda _:NS(agg=lambda *a:grouped))
        return frame

    def sql(self,sql):
        self.sql_calls.append(sql)
        if sql.startswith('INSERT'):
            self.insert_count+=1
            if self.failure=='write':raise RuntimeError('private-write-secret')
            if self.failure=='interrupt':raise KeyboardInterrupt('private-cancel-secret')
            self.after=True
            return NS()
        name=sql.split(" VERSION AS OF '")[1].split("'")[0]
        return self.frame(name)

    def make_table(self):
        fixed=self.seed['sealed']['identity']
        def snap(row):
            return NS(snapshotId=lambda:row['snapshot_id'],parentId=lambda:row['parent_snapshot_id'],
                      sequenceNumber=lambda:row['sequence_number'],operation=lambda:'append',
                      summary=lambda:{'total-records':str(row['record_count']),'total-data-files':str(row['file_count'])})
        def snapshots():
            data=self.expected['snapshot_summaries'] if self.after else C.safe_snapshots(self.seed['sealed'])
            if self.after and self.failure=='metadata':
                data=deepcopy(data);data[-1]['parent_snapshot_id']=B
            return JavaItems([snap(r) for r in data])
        def refs():
            result=[]
            for key,row in C.expected_refs(A,B,D if self.after else A).items():
                ref=NS(snapshotId=lambda r=row:r['snapshot_id'],isBranch=lambda r=row:r['type']=='branch',
                       maxRefAgeMs=lambda:None,maxSnapshotAgeMs=lambda:None,minSnapshotsToKeep=lambda:None)
                result.append(NS(getKey=lambda k=key:k,getValue=lambda r=ref:r))
            return NS(entrySet=lambda:JavaItems(result))
        def scan():
            chosen={}
            def select(s):chosen['snapshot']=s;return api
            def plan():
                key={A:'a',B:'b',D:'c'}[chosen['snapshot']]
                rows=self.expected['snapshot_files'][key]
                tasks=[]
                for row in rows:
                    file=NS(content=lambda:NS(id=lambda:0),format=lambda:'PARQUET',
                            location=lambda r=row:r['path'],recordCount=lambda r=row:r['record_count'],
                            fileSizeInBytes=lambda r=row:r['file_size_in_bytes'])
                    tasks.append(NS(file=lambda f=file:f,deletes=lambda:JavaItems([])))
                values=JavaItems(tasks);self.tasks.append(values);return values
            api=NS(useSnapshot=select,planFiles=plan);return api
        fields=JavaItems([NS(name=lambda n=n:n,type=lambda t=t:t) for n,t in [('id','int'),('name','string'),('bucket','int'),('amount','long')]])
        return NS(refresh=lambda:None,location=lambda:fixed['table_location'],properties=lambda:{'comment':fixed['marker']},
                  schema=lambda:NS(columns=lambda:fields),spec=lambda:NS(isUnpartitioned=lambda:True),
                  operations=lambda:NS(current=lambda:NS(uuid=lambda:UUID,formatVersion=lambda:2,
                      metadataFileLocation=lambda:self.expected['table_identity']['metadata_location'] if self.after else fixed['metadata_location'])),
                  refs=refs,snapshots=snapshots,currentSnapshot=lambda:snap(C.safe_snapshots(self.seed['sealed'])[1]),newScan=scan)

    def run(self, *, legacy_tag=False):
        namespace={'__name__':'fixed_branch_test'}
        script=C.job_script(TOKEN,self.seed)
        if legacy_tag:
            fixed=".option('branch' if name!='baseline_tag' else 'versionAsOf',name)"
            assert fixed in script
            script=script.replace(fixed,".option('branch' if name!='baseline_tag' else 'tag',name)")
        exec(compile(script,'<fixed-branch-test>','exec'),namespace)
        namespace['Path']=lambda _:NS(read_text=lambda:'private-token-sentinel')
        out=io.StringIO()
        with patch.dict('sys.modules',{'pyspark':ModuleType('pyspark'),'pyspark.sql':self.spark_module}),redirect_stdout(out):
            code=namespace['main']()
        text=out.getvalue()
        parsed=C.parse_output(text,TOKEN,self.seed,executor_pod_name=N.executor_name(TOKEN))
        return code,parsed,text


class BranchContractTests(unittest.TestCase):
    def validate(self,value,initial=None):
        return C.validate_output(value,TOKEN,inputs(initial),executor_pod_name=N.executor_name(TOKEN))

    def test_valid_seal_with_optional_empty_snapshot_and_output(self):
        for initial in (None,1477241556327226000):
            self.assertEqual(C._inputs(inputs(initial),TOKEN)['sealed']['initial_snapshot_id'],initial)
            self.assertEqual(self.validate(output(initial),initial)['table_identity']['parent_snapshot_id'],A)

    def test_unknown_keys_wrong_token_endpoints_and_plaintext_inputs_rejected(self):
        for mutate in [lambda v:v.update(token='secret'),lambda v:v.update(scope='PRINCIPAL_ROLE:spark_'+'c'*32),
                       lambda v:v.update(polaris_uri='http://untrusted'),lambda v:v.update(initial_snapshot_id=A)]:
            value=inputs();mutate(value)
            with self.assertRaises(ValueError):C._inputs(value,TOKEN)

    def test_seal_typed_snapshots_refs_parent_and_hashes(self):
        for mutate in [lambda s:s.update(snapshot_a=float(A)),lambda s:s['refs']['audit_branch'].update(snapshot_id=B),
                       lambda s:s['snapshots'][1].update(parent_snapshot_id=None),lambda s:s['snapshots'][0].update(sha256='bad'),
                       lambda s:s['identity'].update(marker='foreign')]:
            value=inputs();mutate(value['sealed'])
            with self.assertRaises(ValueError):C.validate_inputs(value)

    def test_file_inventory_bound_shared_files_and_original_content(self):
        for mutate in [lambda s:s['data_files']['b'][0].update(file_size_in_bytes=2**30),
                       lambda s:s['data_files']['b'][0].update(file_size_in_bytes=222),
                       lambda s:s['data_files']['b'][1].update(path='s3://foreign/data/a.parquet'),
                       lambda s:s['data_files']['b'].reverse()]:
            value=inputs();mutate(value['sealed'])
            with self.assertRaises(ValueError):C.validate_inputs(value)

    def test_main_tag_parent_and_all_prior_snapshot_fields_preserved(self):
        for mutate in [lambda v:v['table_identity'].update(main_snapshot_id=D),
                       lambda v:v['table_identity'].update(tag_snapshot_id=B),
                       lambda v:v['table_identity'].update(parent_snapshot_id=B),
                       lambda v:v['snapshot_summaries'][0].update(sequence_number=7),
                       lambda v:v['snapshot_summaries'].pop(0)]:
            value=output();mutate(value)
            with self.assertRaises(ValueError):self.validate(value)

    def test_before_and_after_file_changes_or_main_file_contamination_rejected(self):
        for mutate in [lambda v:v['before']['snapshot_files']['a'][0].update(file_size_in_bytes=2),
                       lambda v:v['snapshot_files']['b'][0].update(file_size_in_bytes=2),
                       lambda v:v['snapshot_files'].update(c=deepcopy(v['snapshot_files']['b']))]:
            value=output();mutate(value)
            with self.assertRaises(ValueError):self.validate(value)

    def test_different_branch_rows_bool_values_or_retry_cannot_succeed(self):
        for mutate in [lambda v:v['sql_rows'].update(audit_branch=C.ROWS_B),
                       lambda v:v['dataframe_rows']['main'][0].__setitem__(0,True),
                       lambda v:v.update(write_attempts=2),lambda v:v.update(write_outcome='returned'),
                       lambda v:v.update(before=None)]:
            value=output();mutate(value)
            with self.assertRaises(ValueError):self.validate(value)

    def test_parse_duplicate_lines_nonfinite_and_unsafe_error_never_succeed(self):
        text=C.RESULT_PREFIX+json.dumps(output())
        self.assertEqual(C.parse_output(text,TOKEN,inputs(),executor_pod_name=N.executor_name(TOKEN))['result'],'passed')
        for bad in [text+'\n'+text,C.RESULT_PREFIX+'{"schema_version":1,"schema_version":1}',C.RESULT_PREFIX+'{"x":NaN}']:
            with self.assertRaises(ValueError):C.parse_output(bad,TOKEN,inputs(),executor_pod_name=N.executor_name(TOKEN))

    def test_native_profile_dispatch_fixed_and_existing_profiles_unchanged(self):
        self.assertIs(RUN.remote_contract('iceberg-branch'),C)
        self.assertEqual(RUN.validate_profile('iceberg-branch',inputs()),inputs())
        self.assertIs(RUN.remote_contract('iceberg-write'),C.R)
        from tools.local_experiment.spark_benchmark import contract as BMARK
        self.assertIs(RUN.remote_contract('iceberg-benchmark'),BMARK)
        for bad in ('iceberg-other','os','tools.local_experiment.spark_branch.contract'):
            with self.assertRaises(RUN.ExperimentError):RUN.validate_profile(bad,inputs())
        with self.assertRaises(RUN.ExperimentError):RUN.validate_profile('iceberg-branch',inputs(),'executor-failure')

    def test_render_uses_existing_image_secret_stub_limits_and_new_fixed_script_only(self):
        api=[{'ip':'192.168.5.1','port':55737}]
        plan=C.render(TOKEN,inputs=inputs(),api_endpoints=api)
        self.assertEqual(C.validate_saved_plan(plan,TOKEN,inputs(),api),plan)
        secrets=[p for p in plan if p['kind']=='Secret']
        self.assertEqual(len(secrets),1);self.assertEqual(secrets[0]['keys'],['aws-credentials','polaris-token'])
        config=next(p for p in plan if p['kind']=='ConfigMap')['data']
        self.assertIn('branch_audit_branch',config['job.py'])
        self.assertNotIn('private-token-sentinel',json.dumps(plan))
        self.assertNotIn('spark.sql.catalog.iceberg.token ',config['spark-defaults.conf'])
        self.assertEqual(C.expected_pod_spec(TOKEN,'executor',inputs()),C.R.expected_pod_spec(TOKEN,'executor',C.base_inputs(inputs())))

    def test_generated_job_success_fresh_reads_java_iterators_closed_and_one_branch_insert(self):
        fixture=JobFixture();code,value,text=fixture.run()
        self.assertEqual(code,0);self.assertEqual(value['result'],'passed');self.assertEqual(fixture.insert_count,1)
        self.assertTrue(fixture.stopped);self.assertTrue(fixture.tasks);self.assertTrue(all(t.closed for t in fixture.tasks))
        self.assertEqual(value['table_identity']['main_snapshot_id'],B)
        self.assertEqual(fixture.selected[:3],[{'branch':'main'},{'versionAsOf':'baseline_tag'},{'branch':'audit_branch'}])
        self.assertIn('.branch_audit_branch VALUES',fixture.sql_calls[0])
        self.assertTrue(all(not config for config in fixture.sessions))
        self.assertIsNone(fixture.active);self.assertNotIn('private-token-sentinel',text)

    def test_generated_legacy_tag_rejected_after_one_write_while_version_as_of_passes(self):
        legacy=JobFixture();code,value,text=legacy.run(legacy_tag=True)
        self.assertEqual(code,1);self.assertEqual(value['result'],'failed')
        self.assertEqual(value['error_code'],'READ_FAILED')
        self.assertEqual(value['write_outcome'],'returned');self.assertEqual(legacy.insert_count,1)
        self.assertTrue(value['checks']['prewrite_seal']);self.assertTrue(value['checks']['write_returned'])
        self.assertFalse(value['checks']['fresh_dataframe']);self.assertFalse(value['checks']['fresh_sql'])
        self.assertIsNone(value['dataframe_rows']);self.assertIsNone(value['table_identity'])
        self.assertTrue(legacy.stopped);self.assertTrue(all(t.closed for t in legacy.tasks))
        self.assertNotIn('private-',text)
        current=JobFixture();code,value,_=current.run()
        self.assertEqual(code,0);self.assertEqual(value['result'],'passed');self.assertEqual(current.insert_count,1)
        self.assertEqual(value['dataframe_rows']['baseline_tag'],C.ROWS_A)
        self.assertEqual(value['sql_rows']['baseline_tag'],C.ROWS_A)

    def test_generated_job_unknown_write_is_not_retried_or_claimed_rolled_back(self):
        for failure,code in [('write','WRITE_FAILED'),('interrupt','INTERRUPTED')]:
            fixture=JobFixture(failure);exit_code,value,text=fixture.run()
            self.assertEqual(exit_code,1);self.assertEqual(value['write_outcome'],'unknown')
            self.assertEqual(value['error_code'],code);self.assertEqual(fixture.insert_count,1)
            self.assertIsNone(value['table_identity']);self.assertTrue(fixture.stopped)
            self.assertNotIn('private-',text)

    def test_generated_job_returned_write_read_or_metadata_failure_keeps_uncertainty(self):
        for failure in ('read','rows','metadata'):
            fixture=JobFixture(failure);code,value,text=fixture.run()
            self.assertEqual(code,1);self.assertEqual(value['write_outcome'],'returned')
            self.assertEqual(fixture.insert_count,1);self.assertTrue(fixture.stopped)
            self.assertNotIn('private-',text)

    def test_generated_job_verified_write_stop_failure_is_not_success(self):
        fixture=JobFixture('stop');code,value,text=fixture.run()
        self.assertEqual(code,1);self.assertEqual(value['write_outcome'],'verified')
        self.assertEqual(value['error_code'],'SPARK_STOP_FAILED');self.assertNotIn('private-',text)

    def test_job_prewrite_drift_prevents_insert_and_cleans_token(self):
        fixture=JobFixture();fixture.table.properties=lambda:{'comment':'foreign'}
        code,value,_=fixture.run()
        self.assertEqual(code,1);self.assertEqual(value['write_outcome'],'not-attempted')
        self.assertEqual(fixture.insert_count,0);self.assertTrue(all(not c for c in fixture.sessions))

    def test_all_after_snapshot_identity_fields_are_exact_int64(self):
        for key in ('main_snapshot_id','tag_snapshot_id','parent_snapshot_id','branch_snapshot_id'):
            for wrong in (True,1.0,float(output()['table_identity'][key])):
                value=output();value['table_identity'][key]=wrong
                with self.assertRaises(ValueError):self.validate(value)


if __name__=='__main__':unittest.main()
