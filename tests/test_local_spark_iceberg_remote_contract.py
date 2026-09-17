"""Fixed profile and job tests. No Spark, Docker, Kubernetes or network calls."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch

from tools.local_experiment.spark import contract as N
from tools.local_experiment.spark_iceberg import remote_contract as C

TOKEN = 'a'*32
UUID = '12345678-1234-4321-8123-123456789abc'
SNAPSHOT = 8088807033386546123
INITIAL = 3831785292040503894
API = [{'ip': '10.43.0.1', 'port': 443}, {'ip': '192.168.5.1', 'port': 55737}]


def inputs(initial_snapshot_id=None):
    return {'build_token': 'b'*32, 'image': C.IMAGE, 'table_uuid': UUID, 'table_location': C.table_location(TOKEN),
            'polaris_uri': C.POLARIS_URI, 'polaris_realm': 'POLARIS', 'warehouse': 'shirokuma_l1',
            'scope': 'PRINCIPAL_ROLE:spark_'+TOKEN, 's3_endpoint': C.S3_ENDPOINT,
            's3_region': 'us-east-1', 'data_egress': deepcopy(C.DATA_EGRESS), 'initial_snapshot_id': initial_snapshot_id}


def partitions():
    return [{'partition_id': i, 'stage_id': 3, 'task_attempt_id': 4+i, 'attempt_number': 0,
             'executor_id': '1', 'hostname': N.executor_name(TOKEN)} for i in range(2)]


def passed(initial_snapshot_id=None):
    return {'schema_version': 1, 'run_token': TOKEN, 'namespace': N.namespace(TOKEN), 'scope': C.SCOPE,
            'mode': 'success', 'configured_image': C.IMAGE, 'result': 'passed', 'spark_version': N.VERSION,
            'iceberg_version': '1.11.0', 'master': 'k8s://https://kubernetes.default.svc:443',
            'checks': {key: True for key in C.CHECKS}, 'row_count': 32, 'dataframe_rows': C.oracle_rows(),
            'sql_rows': C.oracle_rows(), 'groups': C.oracle_groups(), 'partitions': partitions(),
            'shuffle_observed': True, 'table_identity': {'uuid': UUID, 'location': C.table_location(TOKEN),
                'comment': C.marker(TOKEN), 'snapshot_id': SNAPSHOT, 'parent_snapshot_id': initial_snapshot_id, 'operation': 'append',
                'metadata_location': C.table_location(TOKEN)+'/metadata/00001-'+UUID+'.metadata.json'},
            'data_files': [{'path': C.table_location(TOKEN)+'/data/00000-'+UUID+'.parquet',
                            'record_count': 32, 'file_size_in_bytes': 1234}], 'error_code': None, 'error_classes': []}


def validate(value):
    return C.validate_output(value, TOKEN, inputs(), executor_pod_name=N.executor_name(TOKEN))


class ProfileTests(unittest.TestCase):
    def test_only_exact_safe_inputs_and_owned_location_are_accepted(self):
        self.assertEqual(C.validate_inputs(inputs()), inputs())
        for key, bad in [('image', N.IMAGE), ('build_token', '../x'), ('table_uuid', TOKEN),
                         ('polaris_uri', 'https://public.invalid'), ('warehouse', 'other'),
                         ('s3_endpoint', C.S3_ENDPOINT+'/other'), ('s3_region', 'eu-west-1'),
                         ('polaris_realm', 'realm\nheader:value'), ('scope', 'PRINCIPAL_ROLE:ALL'),
                         ('table_location', C.table_location(TOKEN)+'/../fixture'),
                         ('table_location', C.table_location(TOKEN).replace('shirokuma-lakehouse', 'other')),
                         ('polaris_token', 'secret')]:
            value = inputs(); value[key] = bad
            with self.subTest(key=key, bad=bad), self.assertRaises(C.ContractError):
                C.render(TOKEN, inputs=value)

    def test_initial_snapshot_is_null_or_exact_positive_int64(self):
        self.assertEqual(C.validate_inputs(inputs(INITIAL))['initial_snapshot_id'], INITIAL)
        for bad in (True, False, 0, -1, 2**63, float(INITIAL), str(INITIAL), []):
            with self.subTest(bad=bad), self.assertRaises(C.ContractError):
                C.validate_inputs(inputs(bad))
        for key in ('scope', 'table_location'):
            value = inputs(); value[key] = value[key].replace(TOKEN, 'c'*32)
            with self.assertRaises(C.ContractError):
                C.render(TOKEN, inputs=value)

    def test_profile_retains_native_namespace_rbac_resource_caps_and_security(self):
        base = N.render(TOKEN, 'success', api_endpoints=API)
        plan = C.render(TOKEN, api_endpoints=API, inputs=inputs())
        self.assertEqual(len(plan), len(base)+2)
        for obj in base:
            if obj['kind'] not in ('Pod', 'ConfigMap', 'ResourceQuota'):
                self.assertIn(obj, plan)
        quota = next(x for x in plan if x['kind'] == 'ResourceQuota')
        self.assertEqual(quota['spec']['hard']['secrets'], '1')
        self.assertEqual(quota['spec']['hard']['pods'], '2')
        self.assertEqual(quota['spec']['hard']['limits.memory'], '4Gi')
        for role in ('driver', 'executor'):
            spec = C.expected_pod_spec(TOKEN, role, inputs())
            self.assertEqual(spec['securityContext'], N.security_context())
            self.assertEqual(spec['containers'][0]['securityContext'], N.container_security())
            self.assertEqual(spec['containers'][0]['imagePullPolicy'], 'Never')
            self.assertEqual(spec['containers'][0]['resources'], N.resources(role))
        self.assertEqual(C.validate_saved_plan(plan, TOKEN, inputs(), API), plan)

    def test_secret_stub_contains_only_names_executor_has_no_bearer_mount(self):
        plan = C.render(TOKEN, api_endpoints=API, inputs=inputs())
        secret = next(x for x in plan if x['kind'] == 'Secret')
        self.assertEqual(secret['keys'], ['aws-credentials', 'polaris-token'])
        self.assertNotIn('data', secret); self.assertNotIn('stringData', secret)
        executor = C.expected_pod_spec(TOKEN, 'executor', inputs())
        volume = next(v for v in executor['volumes'] if v['name'] == C.SECRET_NAME)
        self.assertEqual(volume['secret']['items'], [{'key': 'aws-credentials', 'path': 'aws-credentials'}])
        self.assertEqual(volume['secret']['defaultMode'], 0o440)
        self.assertFalse(executor['automountServiceAccountToken'])
        driver = C.expected_pod_spec(TOKEN, 'driver', inputs())
        self.assertEqual(len(driver['volumes'][-1]['secret']['items']), 2)

    def test_egress_and_saved_plan_tampering_refused(self):
        for alteration in ('port', 'namespace', 'pod_selector'):
            value = inputs()
            value['data_egress'][0][alteration] = {'port': 80, 'namespace': 'default', 'pod_selector': {}}[alteration]
            with self.assertRaises(C.ContractError):
                C.render(TOKEN, inputs=value)
        for change in ('hostNetwork', 'secret', 'image', 'egress'):
            plan = C.render(TOKEN, api_endpoints=API, inputs=inputs())
            if change == 'hostNetwork': plan[-1]['spec']['hostNetwork'] = True
            if change == 'image': plan[-1]['spec']['containers'][0]['image'] = N.IMAGE
            if change == 'secret': next(x for x in plan if x['kind'] == 'Secret')['stringData'] = {'aws-credentials': 'secret'}
            if change == 'egress': next(x for x in plan if x['metadata']['name'] == 'data-egress')['spec']['egress'] = [{}]
            with self.subTest(change=change), self.assertRaises(C.ContractError):
                C.validate_saved_plan(plan, TOKEN, inputs(), API)

    def test_properties_have_no_values_or_mount_paths_as_credential_options(self):
        props = C.spark_properties(TOKEN, inputs())
        self.assertNotIn('spark.sql.catalog.iceberg.token', props)
        self.assertFalse(any('s3.access-key' in k or 's3.secret-access-key' in k for k in props))
        self.assertEqual(props['spark.sql.catalog.iceberg.rest.auth.type'], 'oauth2')
        self.assertEqual(props['spark.sql.catalog.iceberg.token-refresh-enabled'], 'false')
        self.assertEqual(props['spark.sql.catalog.iceberg.token-exchange-enabled'], 'false')
        self.assertEqual(props['spark.sql.catalog.iceberg.rest.client.max-retries'], '1')
        self.assertEqual(props['spark.sql.catalog.iceberg.rest.client.connection-timeout-ms'], '10000')
        self.assertEqual(props['spark.sql.catalog.iceberg.rest.client.socket-timeout-ms'], '10000')
        self.assertNotIn('spark.sql.catalog.iceberg.http-client.max-retries', props)
        self.assertEqual(props['spark.sql.catalog.iceberg.client.credentials-provider'],
                         'software.amazon.awssdk.auth.credentials.ProfileCredentialsProvider')
        self.assertIn('credential', props['spark.redaction.regex'])
        with self.assertRaises(C.ContractError):
            C.render(TOKEN, 'failure', inputs=inputs())


class OutputTests(unittest.TestCase):
    def test_snapshot_parent_and_new_id_bind_to_host_initial_snapshot(self):
        self.assertEqual(C.validate_output(passed(INITIAL), TOKEN, inputs(INITIAL),
                                          executor_pod_name=N.executor_name(TOKEN)), passed(INITIAL))
        for key, bad in [('parent_snapshot_id', None), ('parent_snapshot_id', True),
                         ('parent_snapshot_id', INITIAL+1), ('snapshot_id', INITIAL)]:
            value = passed(INITIAL); value['table_identity'][key] = bad
            with self.subTest(key=key, bad=bad), self.assertRaises(C.ContractError):
                C.validate_output(value, TOKEN, inputs(INITIAL), executor_pod_name=N.executor_name(TOKEN))

    def test_success_keeps_int64_and_safe_summary_excludes_table_files_rows(self):
        value = C.parse_output(C.RESULT_PREFIX+json.dumps(passed()), TOKEN, inputs(), executor_pod_name=N.executor_name(TOKEN))
        self.assertEqual(value['table_identity']['snapshot_id'], 8088807033386546123)
        summary = C.safe_summary(value, TOKEN, inputs(), executor_pod_name=N.executor_name(TOKEN))
        self.assertEqual(summary['row_count'], 32)
        self.assertFalse({'table_identity', 'partitions', 'data_files', 'groups', 'dataframe_rows', 'sql_rows'} & summary.keys())
        self.assertNotIn(UUID, json.dumps(summary))
        self.assertEqual(C.oracle_groups(), [[0,8,840],[1,8,896],[2,8,952],[3,8,1008]])

    def test_pass_requires_all_evidence_and_correct_executor(self):
        for check in C.CHECKS:
            value = passed(); value['checks'][check] = False
            with self.subTest(check=check), self.assertRaises(C.ContractError): validate(value)
        for key, bad in [('executor_id','driver'), ('hostname','other'), ('attempt_number',1), ('task_attempt_id',True)]:
            value = passed(); value['partitions'][0][key] = bad
            with self.subTest(key=key), self.assertRaises(C.ContractError): validate(value)
        value = passed(); value['partitions'][1]['task_attempt_id'] = value['partitions'][0]['task_attempt_id']
        with self.assertRaises(C.ContractError): validate(value)

    def test_fixed_rows_metadata_and_data_files_are_not_just_counts(self):
        values = []
        for field in ('dataframe_rows','sql_rows','groups'):
            value = passed(); value[field][0][0] = True; values.append(value)
        for field, bad in [('uuid','b'*32), ('snapshot_id',True), ('snapshot_id',2**63),
                           ('location',C.table_location('b'*32)), ('comment','human-owned'),
                           ('operation','overwrite'), ('metadata_location',C.table_location(TOKEN)+'/metadata/../secret')]:
            value = passed(); value['table_identity'][field] = bad; values.append(value)
        for field, bad in [('path',C.table_location('b'*32)+'/data/x.parquet'), ('record_count',31), ('file_size_in_bytes',True)]:
            value = passed(); value['data_files'][0][field] = bad; values.append(value)
        value = passed(); value['data_files'] *= 2; values.append(value)
        for value in values:
            with self.assertRaises(C.ContractError): validate(value)

    def test_unobserved_failed_fields_stay_null_and_raw_errors_rejected(self):
        value = passed()
        for key in ('spark_version','iceberg_version','master','row_count','dataframe_rows','sql_rows','groups',
                    'partitions','table_identity','data_files'): value[key] = None
        value.update(result='failed',error_code='SPARK_START_FAILED',shuffle_observed=False,
                     checks={key:False for key in C.CHECKS}, error_classes=['builtins.RuntimeError'])
        self.assertEqual(validate(value), value)
        for key, bad in [('row_count',0),('error_code','secret error text'),('raw_credentials','private')]:
            altered = deepcopy(value); altered[key] = bad
            with self.assertRaises(C.ContractError): validate(altered)

    def test_strict_bounded_result_lines(self):
        good = C.RESULT_PREFIX+json.dumps(passed())
        for log in (good+'\n'+good, 'x'*(N.MAX_LOG_BYTES+1), C.RESULT_PREFIX+'{"a":1,"a":2}',
                    C.RESULT_PREFIX+'{"a":NaN}', C.RESULT_PREFIX+'[]', '\ud800'):
            with self.assertRaises(C.ContractError):
                C.parse_output(log, TOKEN, inputs(), executor_pod_name=N.executor_name(TOKEN))

    def test_error_classes_cannot_carry_exception_messages_or_unbounded_chains(self):
        for bad in (['java.lang.IllegalArgumentException: private-error'], ['java.lang.X']*9,
                    ['https://private.invalid'], ['outside.package.Custom'], 'java.lang.Error'):
            value = passed(); value.update(result='failed',error_code='PRECREATED_IDENTITY_MISMATCH',error_classes=bad)
            with self.subTest(bad=bad), self.assertRaises(C.ContractError): validate(value)
        value = passed(); value['error_classes'] = ['java.lang.IllegalArgumentException']
        with self.assertRaises(C.ContractError): validate(value)


class JavaOnlyIterator:
    def __init__(self, values): self.values, self.position = values, 0
    def hasNext(self): return self.position < len(self.values)
    def next(self):
        value = self.values[self.position]
        self.position += 1
        return value
    def __iter__(self): raise AssertionError('Java Iterator is not a Python iterable')


class JavaOnlyIterable:
    def __init__(self, values): self.values = values
    def iterator(self): return JavaOnlyIterator(self.values)
    def __iter__(self): raise AssertionError('Use explicit Java iterator()')


class Files(JavaOnlyIterable):
    def __init__(self, values): super().__init__(values); self.closed = False
    def close(self): self.closed = True


class Frame:
    schema = NS(simpleString=lambda:'struct<id:int,name:string,bucket:int,amount:bigint>')
    def __init__(self, spark, grouped=False): self.spark, self.grouped = spark, grouped
    def orderBy(self, *args): return self
    def limit(self, count): return self
    def count(self): return 1 if self.spark.nonempty else 0
    def collect(self):
        if self.spark.read_error: raise self.spark.read_error
        return C.oracle_rows()
    def repartition(self,*args): return self
    def groupBy(self,*args): return self
    def agg(self,*args): return self
    @property
    def _jdf(self): return NS(queryExecution=lambda:NS(executedPlan=lambda:NS(toString=lambda:'Exchange hashpartitioning')))
    @property
    def rdd(self):
        packets = [dict(p, rows=C.oracle_groups()[i::2]) for i,p in enumerate(partitions())]
        return NS(mapPartitionsWithIndex=lambda callback:NS(collect=lambda:packets))


class FakeSpark:
    def __init__(self, initial_snapshot_id=None):
        self.version = N.VERSION
        self.sparkContext = NS(master='k8s://https://kubernetes.default.svc:443', getConf=lambda:NS(get=lambda _:C.IMAGE))
        self.statements, self.tokens, self.conf_values, self.loaded = [], [], {}, []
        self.written, self.nonempty, self.stopped = False, False, False
        self.uuid, self.location, self.comment = UUID, C.table_location(TOKEN), C.marker(TOKEN)
        self.write_error, self.read_error, self.init_error, self.fresh_init_error = None, None, None, None
        self.initial_snapshot_id = initial_snapshot_id
        self.parent_snapshot_id = initial_snapshot_id
        self.initial_summary = {'total-records':'0', 'total-data-files':'0'}
        self.initial_files = False
        self.load_count, self.change_on_second_load = 0, False
        self.extra_snapshot = False
        self.scan_collections = []
        self.fresh_count = 0
        self.conf = NS(set=self.set, unset=lambda k:self.conf_values.pop(k,None))
        self.catalog = NS(clearCache=lambda:None)
        self.read = NS(format=lambda _:NS(load=lambda _:Frame(self)))
        metadata = NS(uuid=lambda:self.uuid, metadataFileLocation=lambda:passed()['table_identity']['metadata_location'], formatVersion=lambda:2)
        snap = NS(snapshotId=lambda:SNAPSHOT, operation=lambda:'append', parentId=lambda:self.parent_snapshot_id)
        initial = NS(snapshotId=lambda:self.initial_snapshot_id, operation=lambda:'append', parentId=lambda:None,
                     summary=lambda:self.initial_summary)
        fields = [NS(name=lambda n=n:n, type=lambda t=t:t) for n,t in [('id','int'),('name','string'),('bucket','int'),('amount','long')]]
        file = NS(location=lambda:passed()['data_files'][0]['path'], recordCount=lambda:32,
                  fileSizeInBytes=lambda:1234, content=lambda:NS(id=lambda:0),format=lambda:'PARQUET')
        self.table_obj = NS(refresh=lambda:None, operations=lambda:NS(current=lambda:metadata), location=lambda:self.location,
            properties=lambda:{'comment':self.comment}, schema=lambda:NS(columns=lambda:JavaOnlyIterable(fields)), spec=lambda:NS(isUnpartitioned=lambda:True),
            currentSnapshot=lambda:snap if self.written else initial if self.initial_snapshot_id is not None else None,
            snapshots=lambda:JavaOnlyIterable(([initial] if self.initial_snapshot_id is not None else []) + ([snap] if self.written else [])
                              + ([NS(snapshotId=lambda:123)] if self.extra_snapshot and self.written else [])),
            newScan=lambda:NS(useSnapshot=lambda _:NS(planFiles=lambda:self.files([NS(file=lambda:file)])),
                              planFiles=lambda:self.files([NS(file=lambda:file)] if self.initial_files else [])))
        self._jsparkSession = object()
        self.active = self._jsparkSession
        self.sessions = {self._jsparkSession: self}
        self.initialized = set()
        self.initializations = []
        active_api = NS(getActiveSession=self.get_active, setActiveSession=self.set_active,
                        clearActiveSession=lambda:self.set_active(None))
        self._jvm = NS(java=NS(lang=NS(Thread=NS(currentThread=lambda:NS(getContextClassLoader=lambda:NS(loadClass=self.loaded.append))))),
            org=NS(apache=NS(spark=NS(sql=NS(SparkSession=active_api)),
                iceberg=NS(IcebergBuild=NS(version=lambda:'1.11.0'),spark=NS(Spark3Util=NS(loadIcebergTable=self.load))))))
    def get_active(self):
        previous = self.active
        return NS(isDefined=lambda:previous is not None, get=lambda:previous)
    def set_active(self, session): self.active = session
    def set(self,key,value): self.tokens.append((key,value)); self.conf_values[key] = value
    def files(self, values):
        result = Files(values)
        self.scan_collections.append(result)
        return result
    def load(self, session_id, table_name):
        if self.active is not session_id:
            raise AssertionError('CatalogManager must read the target active session SQLConf')
        session = self.sessions[session_id]
        if session_id not in self.initialized:
            if session.conf_values.get('spark.sql.catalog.iceberg.token') != 'private.fixed-bearer':
                raise AssertionError('Target session must have the initialization token')
            self.initializations.append(session_id)
            if session is not self and self.fresh_init_error:
                raise self.fresh_init_error
            self.initialized.add(session_id)
        self.load_count += 1
        if self.change_on_second_load and self.load_count == 2:
            self.initial_snapshot_id = 777
        if self.init_error: raise self.init_error
        return self.table_obj
    def appName(self,name): return self
    def getOrCreate(self): return self
    def table(self,name): return Frame(self)
    def sql(self,statement):
        self.statements.append(statement)
        if statement.startswith('INSERT'):
            if self.write_error: raise self.write_error
            self.written = True
        return Frame(self)
    def newSession(self):
        self.fresh_count += 1
        session_id, values = object(), {}
        def set_value(key, value):
            self.tokens.append((key,value))
            values[key] = value
        session = NS(_jsparkSession=session_id, _jvm=self._jvm, conf_values=values,
                     conf=NS(set=set_value,unset=lambda key:values.pop(key,None)),
                     catalog=self.catalog,read=self.read,sql=self.sql)
        self.sessions[session_id] = session
        return session
    def stop(self): self.stopped = True


class GeneratedJobTests(unittest.TestCase):
    def execute(self, spark, *, initial_snapshot_id=None):
        namespace = {'__name__':'remote_fake'}
        safe_inputs = inputs(initial_snapshot_id)
        script = C.job_script(TOKEN, safe_inputs)
        exec(compile(script,'<remote-job>','exec'), namespace)
        module = ModuleType('pyspark.sql'); module.SparkSession = NS(builder=spark)
        module.functions = NS(count=lambda _:NS(alias=lambda _:None),sum=lambda _:NS(alias=lambda _:None))
        output = io.StringIO()
        with patch.dict(sys.modules, {'pyspark':ModuleType('pyspark'),'pyspark.sql':module}), \
                patch.object(Path,'read_text',return_value='private.fixed-bearer'), redirect_stdout(output):
            code = namespace['main']()
        value = C.parse_output(output.getvalue(),TOKEN,safe_inputs,executor_pod_name=N.executor_name(TOKEN))
        self.assertNotIn('private.fixed-bearer', output.getvalue())
        self.assertNotIn('private-error', output.getvalue())
        self.assertTrue(all(not session.conf_values for session in spark.sessions.values()))
        return code, value

    def test_full_generated_job_one_insert_new_session_executor_and_metadata(self):
        spark = FakeSpark(); code,value = self.execute(spark)
        self.assertEqual(code,0); self.assertEqual(value,passed())
        self.assertEqual(spark.fresh_count,1); self.assertTrue(spark.stopped)
        self.assertEqual(len([s for s in spark.statements if s.startswith('INSERT')]),1)
        self.assertFalse(any(s.startswith(('CREATE','DROP','ALTER','DELETE')) for s in spark.statements))
        self.assertEqual(len(spark.tokens),2)

    def test_distinct_fresh_catalog_uses_own_active_sqlconf_and_restores_previous(self):
        for previous_present in (True, False):
            spark = FakeSpark()
            original = spark._jsparkSession if previous_present else None
            spark.active = original
            code, value = self.execute(spark)
            with self.subTest(previous_present=previous_present):
                self.assertEqual((code,value['result']), (0,'passed'))
                self.assertEqual(len(set(spark.initializations)),2)
                self.assertIs(spark.initializations[0],spark._jsparkSession)
                self.assertIsNot(spark.initializations[1],spark._jsparkSession)
                self.assertIs(spark.active,original)

    def test_fresh_initialization_error_restores_active_and_removes_own_token(self):
        for previous_present in (True, False):
            spark = FakeSpark()
            original = spark._jsparkSession if previous_present else None
            spark.active = original
            spark.fresh_init_error = RuntimeError('private-error')
            code, value = self.execute(spark)
            with self.subTest(previous_present=previous_present):
                self.assertEqual((code,value['error_code']), (1,'READ_FAILED'))
                self.assertTrue(value['checks']['write'])
                self.assertFalse(value['checks']['fresh_dataframe'])
                self.assertEqual(len(spark.statements),1)
                self.assertIs(spark.active,original)

    def test_initial_empty_snapshot_is_retained_as_parent_of_one_append(self):
        spark = FakeSpark(INITIAL)
        code, value = self.execute(spark, initial_snapshot_id=INITIAL)
        self.assertEqual((code, value), (0, passed(INITIAL)))
        self.assertEqual(len([s for s in spark.statements if s.startswith('INSERT')]), 1)
        self.assertEqual(len(spark.scan_collections), 3)
        self.assertTrue(all(scan.closed for scan in spark.scan_collections))

    def test_initial_summary_actual_files_and_rows_must_all_be_empty(self):
        for field in ('records', 'files_summary', 'files_scan', 'rows', 'missing_summary'):
            spark = FakeSpark(INITIAL)
            if field == 'records': spark.initial_summary['total-records'] = '1'
            if field == 'files_summary': spark.initial_summary['total-data-files'] = '1'
            if field == 'files_scan': spark.initial_files = True
            if field == 'rows': spark.nonempty = True
            if field == 'missing_summary': spark.initial_summary.pop('total-data-files')
            with self.subTest(field=field):
                code,value = self.execute(spark,initial_snapshot_id=INITIAL)
                self.assertEqual((code,value['error_code']),(1,'TABLE_NOT_EMPTY'))
                self.assertFalse(spark.statements)
                self.assertTrue(all(scan.closed for scan in spark.scan_collections))

    def test_java_only_iteration_is_bounded_and_never_uses_python_iteration(self):
        namespace = {'__name__':'remote_fake'}
        exec(compile(C.job_script(TOKEN,inputs()),'<remote>','exec'),namespace)
        value = JavaOnlyIterable([1,2])
        self.assertEqual(list(namespace['java_items'](value,2,'METADATA_INVALID')),[1,2])
        with self.assertRaisesRegex(namespace['SafeFailure'],'METADATA_INVALID'):
            list(namespace['java_items'](value,1,'METADATA_INVALID'))
        with self.assertRaisesRegex(AssertionError,'Java iterator'):
            list(value)

    def test_initial_snapshot_change_immediately_before_insert_stops_write(self):
        spark = FakeSpark(INITIAL); spark.change_on_second_load = True
        code,value = self.execute(spark,initial_snapshot_id=INITIAL)
        self.assertEqual((code,value['error_code']),(1,'TABLE_NOT_EMPTY'))
        self.assertFalse(spark.statements)

    def test_wrong_append_parent_or_extra_history_remains_failed(self):
        for field in ('parent', 'extra'):
            spark = FakeSpark(INITIAL)
            if field == 'parent': spark.parent_snapshot_id = None
            else: spark.extra_snapshot = True
            with self.subTest(field=field):
                code,value = self.execute(spark,initial_snapshot_id=INITIAL)
                self.assertEqual((code,value['error_code']),(1,'METADATA_INVALID'))
                self.assertTrue(value['checks']['write'])
                self.assertFalse(value['checks']['metadata'])

    def test_uuid_comment_location_mismatch_before_insert_stops_without_ddl(self):
        for field,bad in [('uuid','other'),('location',C.table_location('b'*32)),('comment','human')]:
            spark = FakeSpark(); setattr(spark,field,bad)
            code,value = self.execute(spark)
            self.assertEqual((code,value['error_code']),(1,'PRECREATED_IDENTITY_MISMATCH'))
            self.assertFalse(spark.statements); self.assertTrue(spark.stopped)

    def test_existing_snapshot_is_not_appended_to(self):
        spark = FakeSpark(); spark.nonempty = True
        code,value = self.execute(spark)
        self.assertEqual((code,value['error_code']),(1,'TABLE_NOT_EMPTY'))
        self.assertFalse(spark.statements)

    def test_unknown_write_is_not_retried_or_promoted(self):
        spark = FakeSpark(); spark.write_error = TimeoutError('private-error')
        code,value = self.execute(spark)
        self.assertEqual((code,value['error_code']),(1,'WRITE_FAILED'))
        self.assertEqual(len(spark.statements),1)
        self.assertIsNone(value['row_count']); self.assertFalse(value['checks']['write'])

    def test_catalog_initialization_failure_unsets_token_and_cancellation_fails(self):
        spark = FakeSpark(); spark.init_error = RuntimeError('private-error')
        code,value = self.execute(spark)
        self.assertEqual((code,value['error_code']),(1,'PRECREATED_IDENTITY_MISMATCH'))
        self.assertFalse(spark.statements)
        spark = FakeSpark(); spark.write_error = KeyboardInterrupt()
        code,value = self.execute(spark)
        self.assertEqual((code,value['error_code']),(1,'INTERRUPTED'))
        self.assertTrue(spark.stopped)

    def test_java_class_chain_is_retained_without_reading_messages(self):
        class Java:
            def __init__(self,name,cause=None): self.name,self.cause = name,cause
            def getClass(self): return NS(getName=lambda:self.name)
            def getCause(self): return self.cause
            def getMessage(self): raise AssertionError('message must not be read')
            def toString(self): raise AssertionError('string must not be read')
        Error = type('Py4JJavaError',(Exception,),{'__module__':'py4j.protocol'})
        for attribute in ('java_exception','_origin'):
            error = Error('private-error')
            setattr(error,attribute,Java('java.lang.IllegalArgumentException',Java('java.lang.RuntimeException')))
            spark = FakeSpark(); spark.init_error = error
            code,value = self.execute(spark)
            self.assertEqual(code,1)
            self.assertEqual(value['error_classes'],['py4j.protocol.Py4JJavaError',
                             'java.lang.IllegalArgumentException','java.lang.RuntimeException'])
        namespace = {'__name__':'remote_fake'}
        exec(compile(C.job_script(TOKEN,inputs()),'<remote>','exec'),namespace)
        cyclic = Java('java.lang.IllegalArgumentException'); cyclic.cause = cyclic
        error.java_exception = cyclic
        self.assertEqual(len(namespace['error_classes'](error)),8)

    def test_observe_partition_returns_actual_task_context_not_driver_defaults(self):
        namespace = {'__name__':'remote_fake'}
        exec(compile(C.job_script(TOKEN,inputs()),'<remote>','exec'),namespace)
        mod = ModuleType('pyspark')
        mod.TaskContext = NS(get=lambda:NS(stageId=lambda:7,taskAttemptId=lambda:22,attemptNumber=lambda:0))
        row = NS(bucket=0,total=840)
        class Row:
            bucket=0; total=840
            def __getitem__(self,key): return 8
        with patch.dict(sys.modules,{'pyspark':mod}), patch.dict('os.environ',{'SPARK_EXECUTOR_ID':'1'}), \
                patch('socket.gethostname',return_value=N.executor_name(TOKEN)):
            packet = list(namespace['observe_partition'](1,[Row()]))[0]
        self.assertEqual(packet['task_attempt_id'],22)
        self.assertEqual(packet['hostname'],N.executor_name(TOKEN))
        self.assertEqual(packet['rows'],[[0,8,840]])


if __name__ == '__main__':
    unittest.main()
