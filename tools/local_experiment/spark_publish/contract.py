"""Fixed linear Iceberg branch append for a separate host fast-forward experiment.

The host seals main/branch B and baseline tag A. One INSERT targets branch_audit_branch;
the job cannot create, drop, merge, promote or expire tables/snapshots/references.
Host REST snapshot hashes are retained as provenance; Spark independently checks
all safe snapshot fields, named refs, files and rows. A lost INSERT result is
unknown, never an invitation to repeat it or a claim that rollback occurred.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re

from tools.local_experiment.spark import contract as N
from tools.local_experiment.spark_iceberg import remote_contract as R
RC = R

SCOPE = 'native-spark-publish-branch-write'
RESULT_PREFIX = 'SHIROKUMA_SPARK_PUBLISH_RESULT='
ROWS_A = [[1, 'base-a', 0, 10], [2, 'base-b', 1, 20]]
MAIN_ROW = [3, 'main-only', 0, 30]
BRANCH_ROW = [4, 'branch-only', 1, 40]
ROWS_B = ROWS_A + [MAIN_ROW]
ROWS_C = ROWS_B + [BRANCH_ROW]
CONCURRENT_ROW = [5, 'concurrent-main', 0, 50]
ROWS_D = ROWS_B + [CONCURRENT_ROW]
COLUMNS = ['id', 'name', 'bucket', 'amount']
COLUMN_TYPES = ['integer', 'varchar', 'integer', 'bigint']
CHECKS = ('classes_loaded', 'prewrite_seal', 'write_returned', 'fresh_dataframe',
          'fresh_sql', 'executor', 'metadata')
ERRORS = {'INPUT_INVALID', 'SPARK_START_FAILED', 'VERSION_MISMATCH', 'PREWRITE_MISMATCH',
          'WRITE_FAILED', 'READ_FAILED', 'ORACLE_MISMATCH', 'EXECUTOR_EVIDENCE_INVALID',
          'METADATA_INVALID', 'SPARK_STOP_FAILED', 'INTERRUPTED', 'INTERNAL_ERROR'}
require = N.require


# These fixed pure validators are shared byte-for-byte by the host and inline
# driver script; no caller-provided code or file path is executed.
_SHARED = r'''
def same(a, b):
    return json.dumps(a, sort_keys=True, separators=(',', ':'), allow_nan=False) == json.dumps(
        b, sort_keys=True, separators=(',', ':'), allow_nan=False)

def sid(value):
    require(type(value) is int and 0 < value < 2**63, 'METADATA_INVALID')
    return value

def path(value, location, suffix):
    require(type(value) is str and len(value) <= 1024 and value.startswith(location)
            and re.fullmatch(r'[A-Za-z0-9_./:\-=]+', value) and '..' not in value
            and value.endswith(suffix), 'METADATA_INVALID')

def files(value, location, records):
    require(type(value) is list and 1 <= len(value) <= 8, 'METADATA_INVALID')
    seen = set()
    for row in value:
        require(type(row) is dict and set(row) == {'path','record_count','file_size_in_bytes'}, 'METADATA_INVALID')
        path(row['path'], location+'/data/', '.parquet')
        require(row['path'] not in seen and type(row['record_count']) is int and 0 < row['record_count'] <= 4
                and type(row['file_size_in_bytes']) is int and 0 < row['file_size_in_bytes'] <= 16*1024*1024,
                'METADATA_INVALID')
        seen.add(row['path'])
    require(value == sorted(value, key=lambda r:r['path']) and sum(r['record_count'] for r in value) == records
            and sum(r['file_size_in_bytes'] for r in value) <= 64*1024*1024, 'METADATA_INVALID')

def append_files(before, after):
    old = {r['path']:r for r in before}
    current = {r['path']:r for r in after}
    require(all(k in current and same(v,current[k]) for k,v in old.items()), 'METADATA_INVALID')
    added = [v for k,v in current.items() if k not in old]
    require(len(added) == 1 and added[0]['record_count'] == 1, 'METADATA_INVALID')

def summaries(value, *, hashes=False):
    require(type(value) is list and 2 <= len(value) <= 4, 'METADATA_INVALID')
    keys = {'snapshot_id','parent_snapshot_id','sequence_number','operation','record_count','file_count'}
    seen = set()
    for row in value:
        require(type(row) is dict and set(row) == keys | ({'sha256'} if hashes else set()), 'METADATA_INVALID')
        identity = sid(row['snapshot_id'])
        require(identity not in seen and row['operation']=='append', 'METADATA_INVALID')
        seen.add(identity)
        if row['parent_snapshot_id'] is not None:
            require(sid(row['parent_snapshot_id']) != identity, 'METADATA_INVALID')
        require(type(row['sequence_number']) is int and 0 < row['sequence_number'] < 2**63
                and type(row['record_count']) is int and 0 <= row['record_count'] <= 4
                and type(row['file_count']) is int and 0 <= row['file_count'] <= 8, 'METADATA_INVALID')
        if hashes:
            require(type(row['sha256']) is str and re.fullmatch('[0-9a-f]{64}',row['sha256']), 'METADATA_INVALID')
    require(value == sorted(value,key=lambda r:r['snapshot_id'])
            and len({r['sequence_number'] for r in value}) == len(value), 'METADATA_INVALID')
    return {r['snapshot_id']:r for r in value}

def safe_snapshots(sealed):
    return [{k:v for k,v in row.items() if k!='sha256'} for row in sealed['snapshots']]

def expected_refs(a, b, c):
    return {'main':{'type':'branch','snapshot_id':b},
            'baseline_tag':{'type':'tag','snapshot_id':a},
            'audit_branch':{'type':'branch','snapshot_id':c}}

def validate_sealed(sealed, base, token):
    require(type(sealed) is dict and set(sealed)=={'identity','initial_snapshot_id','snapshot_a','snapshot_b',
                                                 'refs','snapshots','data_files'}, 'INPUT_INVALID')
    identity = sealed['identity']
    require(type(identity) is dict and set(identity)=={'table_uuid','table_location','marker','schema_sha256',
                                                       'metadata_location','metadata_sha256'}, 'INPUT_INVALID')
    require(identity['table_uuid']==base['table_uuid'] and identity['table_location']==base['table_location']
            and identity['marker']=='shirokuma-local-spark-iceberg:'+token, 'INPUT_INVALID')
    for key in ('schema_sha256','metadata_sha256'):
        require(type(identity[key]) is str and re.fullmatch('[0-9a-f]{64}',identity[key]), 'INPUT_INVALID')
    path(identity['metadata_location'],base['table_location']+'/metadata/','.metadata.json')
    a,b = sid(sealed['snapshot_a']),sid(sealed['snapshot_b'])
    require(a!=b and base['initial_snapshot_id']==b and same(sealed['refs'],expected_refs(a,b,b)), 'INPUT_INVALID')
    initial=sealed['initial_snapshot_id']
    if initial is not None: require(sid(initial) not in (a,b), 'INPUT_INVALID')
    snaps=summaries(sealed['snapshots'],hashes=True)
    require(set(snaps)==({a,b} if initial is None else {initial,a,b})
            and snaps[a]['parent_snapshot_id']==initial and snaps[b]['parent_snapshot_id']==a
            and snaps[a]['record_count']==2 and snaps[b]['record_count']==3
            and snaps[b]['sequence_number']>snaps[a]['sequence_number'], 'INPUT_INVALID')
    if initial is not None:
        require(snaps[initial]['parent_snapshot_id'] is None and snaps[initial]['record_count']==0
                and snaps[initial]['file_count']==0 and snaps[initial]['sequence_number']<snaps[a]['sequence_number'],
                'INPUT_INVALID')
    require(type(sealed['data_files']) is dict and set(sealed['data_files'])=={'a','b'}, 'INPUT_INVALID')
    files(sealed['data_files']['a'],base['table_location'],2)
    files(sealed['data_files']['b'],base['table_location'],3)
    require(snaps[a]['file_count']==len(sealed['data_files']['a'])
            and snaps[b]['file_count']==len(sealed['data_files']['b']), 'INPUT_INVALID')
    append_files(sealed['data_files']['a'],sealed['data_files']['b'])

def validate_before(value, sealed):
    expected={'refs':sealed['refs'],'snapshot_summaries':safe_snapshots(sealed),'snapshot_files':sealed['data_files']}
    require(same(value,expected), 'PREWRITE_MISMATCH')

def validate_after(identity, snapshot_summaries, snapshot_files, sealed):
    require(type(identity) is dict and set(identity)=={'table_uuid','table_location','marker','metadata_location',
            'main_snapshot_id','tag_snapshot_id','branch_snapshot_id','parent_snapshot_id'}, 'METADATA_INVALID')
    for key in ('table_uuid','table_location','marker'):
        require(identity[key]==sealed['identity'][key], 'METADATA_INVALID')
    path(identity['metadata_location'],identity['table_location']+'/metadata/','.metadata.json')
    require(identity['metadata_location']!=sealed['identity']['metadata_location'], 'METADATA_INVALID')
    a,b,c=sealed['snapshot_a'],sealed['snapshot_b'],sid(identity['branch_snapshot_id'])
    before={s['snapshot_id']:s for s in safe_snapshots(sealed)}
    require(sid(identity['main_snapshot_id'])==b and sid(identity['tag_snapshot_id'])==a
            and sid(identity['parent_snapshot_id'])==b and c not in before, 'METADATA_INVALID')
    current=summaries(snapshot_summaries)
    require(set(current)==set(before)|{c} and all(same(row,current[k]) for k,row in before.items())
            and current[c]['parent_snapshot_id']==b and current[c]['record_count']==4
            and current[c]['sequence_number']>max(row['sequence_number'] for row in before.values()), 'METADATA_INVALID')
    require(type(snapshot_files) is dict and set(snapshot_files)=={'a','b','c'}, 'METADATA_INVALID')
    for key in ('a','b'):
        require(same(snapshot_files[key],sealed['data_files'][key]), 'METADATA_INVALID')
    files(snapshot_files['c'],identity['table_location'],4)
    require(current[c]['file_count']==len(snapshot_files['c']), 'METADATA_INVALID')
    append_files(snapshot_files['b'],snapshot_files['c'])
'''
exec(compile(_SHARED, '<fixed-publish-validators>', 'exec'), globals())


def validate_inputs(value):
    require(type(value) is dict and set(value) == R.INPUT_KEYS | {'sealed'}, 'PUBLISH_INPUT_KEYS_INVALID')
    base = R.validate_inputs({k:value[k] for k in R.INPUT_KEYS})
    token = base['scope'].removeprefix('PRINCIPAL_ROLE:spark_')
    R._inputs(base, token)
    validate_sealed(value['sealed'], base, token)
    return deepcopy(value)


def _inputs(value, token):
    value = validate_inputs(value)
    require(value['scope'] == 'PRINCIPAL_ROLE:spark_' + N.validate_token(token), 'PUBLISH_TOKEN_MISMATCH')
    return value


def base_inputs(value):
    return {k:deepcopy(value[k]) for k in R.INPUT_KEYS}


def expected_pod_spec(token, role, inputs):
    return R.expected_pod_spec(token, role, base_inputs(_inputs(inputs, token)))


def render(token, mode='success', *, api_endpoints=None, inputs):
    inputs = _inputs(inputs, token)
    require(mode == 'success', 'PUBLISH_MODE_INVALID')
    plan = R.render(token, mode, api_endpoints=api_endpoints, inputs=base_inputs(inputs))
    for obj in plan:
        if obj['kind'] == 'ConfigMap': obj['data']['job.py'] = job_script(token, inputs)
    return plan


def validate_saved_plan(plan, token, inputs, api_endpoints=None):
    require(same(plan, render(token, inputs=inputs, api_endpoints=api_endpoints)), 'PUBLISH_PLAN_CHANGED')
    return deepcopy(plan)


def validate_output(value, token, inputs, *, executor_pod_name):
    inputs = _inputs(inputs, token)
    fixed = {'schema_version':1,'run_token':token,'namespace':N.namespace(token),'scope':SCOPE,
             'mode':'success','configured_image':inputs['image']}
    fields = {'result','spark_version','iceberg_version','master','checks','write_outcome','write_attempts',
              'before','table_identity','snapshot_summaries','snapshot_files','dataframe_rows','sql_rows',
              'groups','partitions','shuffle_observed','row_count','error_code','error_classes'}
    require(type(value) is dict and set(value)==set(fixed)|fields, 'PUBLISH_OUTPUT_KEYS_INVALID')
    for k,v in fixed.items(): require(type(value[k]) is type(v) and value[k]==v, 'PUBLISH_OUTPUT_IDENTITY_INVALID')
    require(value['result'] in ('passed','failed') and type(value['checks']) is dict and set(value['checks'])==set(CHECKS)
            and all(type(v) is bool for v in value['checks'].values()), 'PUBLISH_OUTPUT_INVALID')
    require(type(value['write_attempts']) is int and value['write_attempts'] in (0,1)
            and value['write_outcome'] in ('not-attempted','unknown','returned','verified')
            and (value['write_outcome']=='not-attempted')==(value['write_attempts']==0), 'PUBLISH_WRITE_INVALID')
    require(value['checks']['write_returned']==(value['write_outcome'] in ('returned','verified')), 'PUBLISH_WRITE_INVALID')
    for k,v in [('spark_version','4.1.3'),('iceberg_version','1.11.0'),('master','k8s://https://kubernetes.default.svc:443')]:
        require(value[k] is None or value[k]==v, 'PUBLISH_VERSION_INVALID')
    require(type(value['shuffle_observed']) is bool, 'PUBLISH_EXECUTOR_INVALID')
    if value['before'] is not None: validate_before(value['before'], inputs['sealed'])
    require(not value['checks']['prewrite_seal'] or value['before'] is not None, 'PUBLISH_BEFORE_MISSING')
    expected = {'main':ROWS_B,'baseline_tag':ROWS_A,'audit_branch':ROWS_C}
    for k,check in [('dataframe_rows','fresh_dataframe'),('sql_rows','fresh_sql')]:
        require(value[k] is None or same(value[k],expected), 'PUBLISH_ROWS_INVALID')
        require(not value['checks'][check] or value[k] is not None, 'PUBLISH_ROWS_MISSING')
    require(value['row_count'] is None or type(value['row_count']) is int and value['row_count']==4, 'PUBLISH_ROWS_INVALID')
    require(value['groups'] is None or same(value['groups'],[[0,2,40],[1,2,60]]), 'PUBLISH_GROUPS_INVALID')
    if value['partitions'] is not None:
        native=N.expected_output(token,'success');native['partitions']=value['partitions']
        N.validate_output(native,token,'success',executor_pod_name=executor_pod_name)
    require(not value['checks']['executor'] or value['groups'] is not None and value['partitions'] is not None
            and value['shuffle_observed'], 'PUBLISH_EXECUTOR_MISSING')
    after=(value['table_identity'],value['snapshot_summaries'],value['snapshot_files'])
    if any(v is not None for v in after):
        require(all(v is not None for v in after), 'PUBLISH_METADATA_PARTIAL')
        validate_after(*after,inputs['sealed'])
    require(not value['checks']['metadata'] or all(v is not None for v in after), 'PUBLISH_METADATA_MISSING')
    if value['write_outcome']=='verified':
        require(all(value['checks'][k] for k in ('prewrite_seal','fresh_dataframe','fresh_sql','metadata')), 'PUBLISH_WRITE_INVALID')
    require(type(value['error_classes']) is list and len(value['error_classes'])<=8
            and all(type(v) is str and len(v)<=256 and (v in ('SafeFailure','unknown') or re.fullmatch(R.ERROR_CLASS,v))
                    for v in value['error_classes']), 'PUBLISH_ERRORS_INVALID')
    if value['result']=='passed':
        require(all(value['checks'].values()) and value['write_outcome']=='verified' and value['row_count']==4
                and all(value[k] is not None for k in ('spark_version','iceberg_version','master'))
                and value['error_code'] is None and not value['error_classes'], 'PUBLISH_PASSED_INCOMPLETE')
    else:
        require(value['error_code'] in ERRORS and value['error_classes'], 'PUBLISH_FAILURE_INVALID')
    return deepcopy(value)


def parse_output(logs, token, inputs, *, executor_pod_name):
    require(type(logs) is str and len(logs.encode())<=N.MAX_LOG_BYTES, 'PUBLISH_LOG_BOUND')
    lines=[line[len(RESULT_PREFIX):] for line in logs.splitlines() if line.startswith(RESULT_PREFIX)]
    require(len(lines)==1 and len(lines[0])<=64*1024, 'PUBLISH_RESULT_LINE_INVALID')
    def pairs(items):
        value={}
        for key,item in items:
            require(key not in value,'PUBLISH_DUPLICATE_KEY');value[key]=item
        return value
    try:
        value=json.loads(lines[0],object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
        return validate_output(value,token,inputs,executor_pod_name=executor_pod_name)
    except (ValueError,TypeError,KeyError,UnicodeError,RecursionError):
        raise N.ContractError('PUBLISH_OUTPUT_INVALID') from None


def safe_summary(value, token, inputs, *, executor_pod_name):
    value=validate_output(value,token,inputs,executor_pod_name=executor_pod_name)
    return {k:deepcopy(value[k]) for k in ('schema_version','run_token','scope','result','checks','write_outcome',
            'write_attempts','spark_version','iceberg_version','error_code','error_classes')}


def job_script(token, inputs):
    inputs=_inputs(inputs,token)
    config={'token':token,'namespace':N.namespace(token),'image':inputs['image'],'scope':SCOPE,
            'table':R.table_name(token),'sealed':inputs['sealed'],'executor':N.executor_name(token),
            'checks':list(CHECKS),'prefix':RESULT_PREFIX,'secret_dir':R.SECRET_DIR,
            'error_class_pattern':R.ERROR_CLASS,'rows':{'main':ROWS_B,'baseline_tag':ROWS_A,'audit_branch':ROWS_C}}
    helpers=R._JOB.split('def expected_rows():',1)[0]
    return 'CONFIG = '+repr(config)+'\n'+helpers+_SHARED+_JOB


_JOB = r'''
def observe_partition(partition_id, rows):
    from pyspark import TaskContext
    context=TaskContext.get()
    require(context is not None,'EXECUTOR_EVIDENCE_INVALID')
    yield {'partition_id':partition_id,'stage_id':context.stageId(),'task_attempt_id':context.taskAttemptId(),
           'attempt_number':context.attemptNumber(),'executor_id':os.environ.get('SPARK_EXECUTOR_ID'),
           'hostname':socket.gethostname(),'rows':[[int(r.bucket),int(r['count']),int(r.total)] for r in rows]}

def load(session):
    sessions=session._jvm.org.apache.spark.sql.SparkSession
    previous=sessions.getActiveSession()
    try:
        sessions.setActiveSession(session._jsparkSession)
        return session._jvm.org.apache.iceberg.spark.Spark3Util.loadIcebergTable(session._jsparkSession,CONFIG['table'])
    finally:
        if previous.isDefined(): sessions.setActiveSession(previous.get())
        else: sessions.clearActiveSession()

def initialize(session):
    bearer=Path(CONFIG['secret_dir']+'/polaris-token').read_text()
    require(0<len(bearer)<=16384 and re.fullmatch(r'[A-Za-z0-9._~+/-]+=*',bearer),'INPUT_INVALID')
    key='spark.sql.catalog.iceberg.token'
    try:
        session.conf.set(key,bearer)
        return load(session)
    finally:
        session.conf.unset(key);bearer=None

def observe(table):
    table.refresh()
    fixed=CONFIG['sealed']['identity']
    require(str(table.operations().current().uuid())==fixed['table_uuid']
            and str(table.location())==fixed['table_location']
            and str(table.properties().get('comment'))==fixed['marker'],'PREWRITE_MISMATCH')
    fields=list(java_items(table.schema().columns(),4,'PREWRITE_MISMATCH'))
    require([(str(f.name()),str(f.type())) for f in fields]==[('id','int'),('name','string'),('bucket','int'),('amount','long')]
            and table.spec().isUnpartitioned() and int(table.operations().current().formatVersion())==2,'PREWRITE_MISMATCH')
    refs={}
    for entry in java_items(table.refs().entrySet(),3,'METADATA_INVALID'):
        name=str(entry.getKey());ref=entry.getValue()
        require(name in ('main','baseline_tag','audit_branch') and name not in refs
                and ref.maxRefAgeMs() is None and ref.maxSnapshotAgeMs() is None
                and ref.minSnapshotsToKeep() is None,'METADATA_INVALID')
        refs[name]={'type':'branch' if ref.isBranch() else 'tag','snapshot_id':int(ref.snapshotId())}
    require(set(refs)=={'main','baseline_tag','audit_branch'} and table.currentSnapshot() is not None
            and int(table.currentSnapshot().snapshotId())==refs['main']['snapshot_id'],'METADATA_INVALID')
    snaps=[]
    for snap in java_items(table.snapshots(),4,'METADATA_INVALID'):
        snaps.append({'snapshot_id':int(snap.snapshotId()),
                      'parent_snapshot_id':None if snap.parentId() is None else int(snap.parentId()),
                      'sequence_number':int(snap.sequenceNumber()),'operation':str(snap.operation()),
                      'record_count':int(snap.summary().get('total-records')),
                      'file_count':int(snap.summary().get('total-data-files'))})
    snaps.sort(key=lambda row:row['snapshot_id'])
    return {'refs':refs,'snapshot_summaries':snaps},str(table.operations().current().metadataFileLocation())

def scan_files(table, snapshot_id):
    result=[]
    tasks=table.newScan().useSnapshot(snapshot_id).planFiles()
    try:
        for task in java_items(tasks,8,'METADATA_INVALID'):
            file=task.file()
            require(int(file.content().id())==0 and str(file.format())=='PARQUET'
                    and not task.deletes().iterator().hasNext(),'METADATA_INVALID')
            result.append({'path':str(file.location()),'record_count':int(file.recordCount()),
                           'file_size_in_bytes':int(file.fileSizeInBytes())})
    finally:
        tasks.close()
    return sorted(result,key=lambda row:row['path'])

def before(table):
    observed,location=observe(table)
    require(location==CONFIG['sealed']['identity']['metadata_location'],'PREWRITE_MISMATCH')
    observed['snapshot_files']={key:scan_files(table,CONFIG['sealed']['snapshot_'+key]) for key in ('a','b')}
    validate_before(observed,CONFIG['sealed'])
    return observed

def main():
    output={'schema_version':1,'run_token':CONFIG['token'],'namespace':CONFIG['namespace'],'scope':CONFIG['scope'],
            'mode':'success','configured_image':CONFIG['image'],'result':'failed','spark_version':None,
            'iceberg_version':None,'master':None,'checks':{key:False for key in CONFIG['checks']},
            'write_outcome':'not-attempted','write_attempts':0,'before':None,'table_identity':None,
            'snapshot_summaries':None,'snapshot_files':None,'dataframe_rows':None,'sql_rows':None,
            'groups':None,'partitions':None,'shuffle_observed':False,'row_count':None,'error_code':None,'error_classes':[]}
    spark,stage=None,'start'
    try:
        from pyspark.sql import SparkSession,functions as F
        spark=SparkSession.builder.appName('shirokuma-spark-publish-'+CONFIG['token']).getOrCreate()
        output['spark_version'],output['master']=spark.version,spark.sparkContext.master
        loader=spark._jvm.java.lang.Thread.currentThread().getContextClassLoader()
        for name in ('org.apache.iceberg.spark.SparkCatalog','org.apache.iceberg.rest.RESTCatalog',
                     'org.apache.iceberg.aws.s3.S3FileIO','software.amazon.awssdk.services.s3.S3Client'):
            loader.loadClass(name)
        output['iceberg_version']=str(spark._jvm.org.apache.iceberg.IcebergBuild.version())
        require(output['spark_version']=='4.1.3' and output['iceberg_version']=='1.11.0'
                and output['master']=='k8s://https://kubernetes.default.svc:443'
                and spark.sparkContext.getConf().get('spark.kubernetes.container.image')==CONFIG['image'],'VERSION_MISMATCH')
        output['checks']['classes_loaded']=True
        stage='before'
        output['before']=before(initialize(spark))
        output['checks']['prewrite_seal']=True
        stage='write'
        output['write_attempts'],output['write_outcome']=1,'unknown'
        spark.sql('INSERT INTO '+CONFIG['table']+".branch_audit_branch VALUES (CAST(4 AS INT), 'branch-only', CAST(1 AS INT), CAST(40 AS BIGINT))")
        output['write_outcome']='returned';output['checks']['write_returned']=True
        stage='read'
        fresh=spark.newSession();initialize(fresh);fresh.catalog.clearCache()
        dataframe,sql_rows={},{}
        for name,expected in CONFIG['rows'].items():
            frame=fresh.read.format('iceberg').option('branch' if name!='baseline_tag' else 'versionAsOf',name).load(CONFIG['table'])
            require(frame.schema.simpleString()=='struct<id:int,name:string,bucket:int,amount:bigint>','ORACLE_MISMATCH')
            rows=[list(r) for r in frame.orderBy('id').limit(5).collect()]
            require(same(rows,expected),'ORACLE_MISMATCH');dataframe[name]=rows
            rows=[list(r) for r in fresh.sql('SELECT id,name,bucket,amount FROM '+CONFIG['table']+
                  " VERSION AS OF '"+name+"' ORDER BY id LIMIT 5").collect()]
            require(same(rows,expected),'ORACLE_MISMATCH');sql_rows[name]=rows
        output['dataframe_rows'],output['sql_rows']=dataframe,sql_rows
        output['checks']['fresh_dataframe']=True;output['checks']['fresh_sql']=True;output['row_count']=4
        frame=fresh.read.format('iceberg').option('branch','audit_branch').load(CONFIG['table'])
        grouped=frame.repartition(2,'bucket').groupBy('bucket').agg(F.count('*').alias('count'),F.sum('amount').alias('total'))
        output['shuffle_observed']='Exchange' in grouped._jdf.queryExecution().executedPlan().toString()
        packets=grouped.rdd.mapPartitionsWithIndex(observe_partition).collect()
        groups=sorted((r for p in packets for r in p['rows']),key=lambda r:r[0])
        require(same(groups,[[0,2,40],[1,2,60]]),'ORACLE_MISMATCH')
        parts=sorted(({k:v for k,v in p.items() if k!='rows'} for p in packets),key=lambda p:p['partition_id'])
        require(len(parts)==2 and output['shuffle_observed'] and [p['partition_id'] for p in parts]==[0,1]
                and len({p['stage_id'] for p in parts})==1 and len({p['task_attempt_id'] for p in parts})==2
                and all(p['executor_id']=='1' and p['hostname']==CONFIG['executor'] and p['attempt_number']==0 for p in parts),
                'EXECUTOR_EVIDENCE_INVALID')
        output['groups'],output['partitions']=groups,parts;output['checks']['executor']=True
        stage='metadata'
        table=load(fresh);observed,location=observe(table)
        a,b=CONFIG['sealed']['snapshot_a'],CONFIG['sealed']['snapshot_b']
        c=observed['refs']['audit_branch']['snapshot_id']
        require(same(observed['refs'],expected_refs(a,b,c)),'METADATA_INVALID')
        current={s['snapshot_id']:s for s in observed['snapshot_summaries']}
        require(c in current,'METADATA_INVALID')
        identity={k:CONFIG['sealed']['identity'][k] for k in ('table_uuid','table_location','marker')}
        identity.update(metadata_location=location,main_snapshot_id=observed['refs']['main']['snapshot_id'],
                        tag_snapshot_id=observed['refs']['baseline_tag']['snapshot_id'],branch_snapshot_id=c,
                        parent_snapshot_id=current[c]['parent_snapshot_id'])
        snapshot_files={key:scan_files(table,snapshot) for key,snapshot in [('a',a),('b',b),('c',c)]}
        validate_after(identity,observed['snapshot_summaries'],snapshot_files,CONFIG['sealed'])
        output['table_identity'],output['snapshot_summaries'],output['snapshot_files']=identity,observed['snapshot_summaries'],snapshot_files
        output['checks']['metadata']=True;output['write_outcome']='verified'
    except BaseException as error:
        output['error_classes']=error_classes(error)
        output['error_code']=str(error) if isinstance(error,SafeFailure) else ('INTERRUPTED' if not isinstance(error,Exception)
            else {'start':'SPARK_START_FAILED','before':'PREWRITE_MISMATCH','write':'WRITE_FAILED',
                  'read':'READ_FAILED','metadata':'METADATA_INVALID'}.get(stage,'INTERNAL_ERROR'))
    finally:
        if spark is not None:
            try: spark.stop()
            except BaseException as error:
                if output['error_code'] is None:output['error_code']='SPARK_STOP_FAILED';output['error_classes']=error_classes(error)
    if output['error_code'] is None and all(output['checks'].values()) and output['write_outcome']=='verified':output['result']='passed'
    print(CONFIG['prefix']+json.dumps(output,sort_keys=True,separators=(',',':'),allow_nan=False),flush=True)
    return 0 if output['result']=='passed' else 1

if __name__=='__main__':sys.exit(main())
'''
