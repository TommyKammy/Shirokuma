"""Pure report semantics and saved lifecycle fixtures, with no live dependencies."""
from copy import deepcopy
import hashlib
import unittest
from unittest.mock import patch

from tools.local_experiment.query_diagnose_report import contract as C
from test_query_diagnose_contract import source, snapshot_source, projections
from test_query_diagnose_run import receipt

TOKEN='b'*32


def documents(profile=1, *, identity=TOKEN):
    """Synthetic full saved evidence; reusable by reader/Go integration tests."""
    src=source() if profile==1 else snapshot_source();p=projections(src)
    rows=[];statements=C.D.statements(src)
    qids=['20260918_000000_01000_abcde',p['plan']['query_id'],p['history']['query_id'],p['stats']['query_id'],
          '20260918_000000_01001_abcde','20260918_000000_01002_abcde']
    for index,(label,sql) in enumerate([*statements.items(),('drain',C.inspection_sql(identity))]):
        rows.append({'operation_id':f'00000000-0000-4000-8000-{index+1:012d}','label':label,'query_id':qids[index],
            'sql_sha256':hashlib.sha256(sql.encode()).hexdigest(),'started_at':'2026-09-18T00:00:00.000000Z',
            'result':'finished','response_complete':True,'post_attempted':True,'server_max_run_seconds':30,
            'http_status':200,'row_count':1,'response_bytes':100,'pages':1,'error_code':None})
    queries={'worker':rows[:5],'control':rows[5:]};runtime=receipt()['runtime']
    for role in ('worker','control'):
        owner='shirokuma-query-read-'+('control-' if role=='control' else '')+identity
        runtime[role]={'user':owner,'source':owner,'records':queries[role],'cancellations':[]}
    for read in runtime['catalog']['reads']:read['request_sha256']=C.digest(C.D.base_request(src))
    owner='shirokuma-query-read-'+identity
    drain={'verified':True,'user':owner,'source':owner,'active':[],'cancelled':[],'cancel_errors':[],'errors':[],
           'observed_query_ids':sorted(qids[:5])}
    observed={'state':'verified','source_sha256':C.digest(src),**p,'identity_before':deepcopy(src['identity']),
        'identity_after':deepcopy(src['identity']),'queries':queries,'query_drain':drain,'runtime':runtime,
        'cleanup_verified':True,'error_code':None}
    if profile==2:observed.update(snapshot_catalog_before=deepcopy(src['snapshot_catalog']),snapshot_catalog_after=deepcopy(src['snapshot_catalog']))
    evidence=C.D.build_evidence(src,**p)
    audit={'task_id':'workflow-'+identity,'trace_id':'1'*32,'records':[],'verified':True,'saved_spans_verified':True}
    spans=[]
    for index,action in enumerate(C.ACTIONS):
        sid=f'{index+1:016x}';model=index==1
        audit['records'].append({'event_id':f'00000000-0000-4000-9000-{index+1:012d}',
            'task_id':audit['task_id'],'trace_id':audit['trace_id'],'action_type':action,'status':'success',
            'model_provider':'mtplx' if model else 'none','model_name':'qwen3.8-flash-next' if model else '',
            'prompt_tokens':100 if model else None,'completion_tokens':10 if model else None,
            'inference_cost_estimate':None,'duration_ms':10,'span_id':sid,'audit_delivery':'delivered'})
        spans.append({'name':action,'trace_id':audit['trace_id'],'span_id':sid,'start_time_unix_nano':1,
            'end_time_unix_nano':2,'status':'OK','attributes':{'task_id':audit['task_id']} if index==2 else {}})
    value={'schema_version':1,'evidence_token':identity,'read_token':src['read_token'],
        'started_at':'2026-09-18T09:00:00+09:00','finished_at':'2026-09-18T09:01:00+09:00',
        'result':'passed','error_code':None,'source':src,'observation':observed,'model_evidence':evidence,
        'diagnosis':C.D.expected_diagnosis(evidence),'model_calls':1,'invocations':1,'original_select_reexecuted':False,
        'cloud_calls':0,'retries':0,'audit':audit,'cleanup_verified':True,
        'source_hashes':{k:'d'*64 for k in (C.HISTORICAL_23 if profile==1 else C.HISTORICAL_27)},'limits':list(C.HEADER_LIMITS)}
    return value,spans


def failed_documents(*, interrupted=False, cancel=False):
    value,spans=documents();o=value['observation']
    value.update(result='interrupted' if interrupted else 'failed',error_code='INTERRUPTED' if interrupted else 'OBSERVATION_FAILED',
                 model_calls=0,model_evidence=None,diagnosis=None)
    o.update(state='interrupted' if interrupted else 'failed',error_code='INTERRUPTED' if interrupted else 'OBSERVATION_FAILED',
             identity_after=None,plan=None,history=None,stats=None)
    del o['queries']['worker'][2:]
    q=o['queries']['worker'][-1];q.update(result='unknown',response_complete=False,error_code='QUERY_FAILED')
    o['query_drain']['observed_query_ids']=sorted(r['query_id'] for r in o['queries']['worker'])
    if cancel:
        o['query_drain']['cancelled']=[q['query_id']]
        o['runtime']['worker']['cancellations']=[{'query_id':q['query_id'],'result':'accepted','http_status':204}]
    value['audit']['records'].pop(1);spans.pop(1)
    for r in value['audit']['records']:r['status']='failed'
    for s in spans:s['status']='ERROR'
    value['audit'].update(verified=False,saved_spans_verified=False)
    return value,spans


def report(rows, selected=None, **changes):
    inventory={'root':'present','complete':True,'inspected_entries':len(rows),'unrecognized_entries':0,'error_code':None}
    inventory.update(changes)
    return C.build_report(rows,selected,inventory)


class ProjectionTests(unittest.TestCase):
    def test_both_source_profiles_recompute_success_without_filesystem_or_current_time(self):
        for profile in (1,2):
            value,spans=documents(profile)
            with patch('builtins.open',side_effect=AssertionError('filesystem forbidden')):
                row=C.project_run(TOKEN,value,spans)
            self.assertEqual(row['state'],'passed')
            self.assertEqual(row['source_profile'],'reader-v1' if profile==1 else 'snapshot-reader-v2')
            self.assertEqual(row['started_at'],'2026-09-18T00:00:00.000000Z')
            self.assertEqual(row['model']['call_intents'],1)
            self.assertEqual(row['model']['recorded_inference'],'success')
            self.assertEqual(row['audit']['events'],3)
            self.assertEqual(row['audit']['correlation'],'matched')
            self.assertEqual(set(row),C.ENTRY_KEYS)
            self.assertEqual(C.validate_public(report([row]))['command_result'],'complete')

    def test_historical_keysets_accept_old_hash_values_but_not_unknown_path(self):
        value,spans=documents();value['source_hashes']={k:'e'*64 for k in C.HISTORICAL_27}
        self.assertEqual(C.project_run(TOKEN,value,spans)['state'],'passed')
        value['source_hashes']['arbitrary.py']='e'*64
        self.assertEqual(C.project_run(TOKEN,value,spans)['state'],'invalid')

    def test_closed_pre_model_failure_and_owned_cancel_are_valid_not_success(self):
        for cancel in (False,True):
            value,spans=failed_documents(cancel=cancel);row=C.project_run(TOKEN,value,spans)
            self.assertEqual(row['state'],'failed');self.assertEqual(row['recorded_result'],'failed')
            self.assertEqual(row['verification'],'saved-evidence');self.assertTrue(row['cleanup_verified'])
            self.assertEqual(row['model']['call_intents'],0);self.assertIsNone(row['model']['prompt_tokens'])
            self.assertEqual(row['audit']['events'],2);self.assertFalse(row['audit']['producer_verified'])
            self.assertIsNone(row['findings']);self.assertEqual(report([row])['command_result'],'complete')

    def test_interrupted_closed_failure_keeps_outcome_and_inspection_success(self):
        value,spans=failed_documents(interrupted=True)
        value['observation']['queries']['worker'][-1].update(result='interrupted',error_code='INTERRUPTED')
        row=C.project_run(TOKEN,value,spans)
        self.assertEqual(row['state'],'interrupted');self.assertEqual(row['error_code'],'INTERRUPTED')
        self.assertEqual(report([row])['command_result'],'complete')

    def test_typed_transport_error_and_not_sent_failure_are_supported_with_strict_bounds(self):
        value,spans=failed_documents()
        row=value['observation']['queries']['worker'][-1]
        row['error_code']='QUERY_CELL_TYPE_INVALID'
        self.assertEqual(C.project_run(TOKEN,value,spans)['state'],'failed')
        row.update(result='not-sent',post_attempted=False,query_id=None,response_complete=False,
                   pages=0,response_bytes=0,row_count=0,http_status=None)
        value['observation']['query_drain']['observed_query_ids']=[value['observation']['queries']['worker'][0]['query_id']]
        self.assertEqual(C.project_run(TOKEN,value,spans)['state'],'failed')
        row['post_attempted']=True
        self.assertEqual(C.project_run(TOKEN,value,spans)['state'],'invalid')

    def test_inference_after_failed_observation_is_a_contradiction(self):
        value,spans=documents();value.update(result='failed',error_code='AUDIT_FAILED')
        value['audit'].update(verified=False,saved_spans_verified=False)
        value['audit']['records'][0]['status']='failed';spans[0]['status']='ERROR'
        self.assertEqual(C.project_run(TOKEN,value,spans)['state'],'invalid')

    def test_model_blocked_retains_known_usage_without_claiming_adopted_diagnosis(self):
        value,spans=documents();value.update(result='failed',error_code='MODEL_REJECTED',diagnosis=None)
        value['audit'].update(verified=False,saved_spans_verified=False)
        for index,status in ((1,'blocked'),(2,'failed')):
            value['audit']['records'][index]['status']=status;spans[index]['status']='ERROR'
        row=C.project_run(TOKEN,value,spans)
        self.assertEqual(row['state'],'failed');self.assertIsNone(row['next_steps'])
        self.assertEqual(row['model']['recorded_inference'],'blocked')
        self.assertEqual(row['model']['prompt_tokens'],100)

    def test_unknown_usage_stays_null_with_explicit_known_subtotals(self):
        value,spans=documents();value['audit']['records'][1].update(prompt_tokens=None,completion_tokens=None)
        row=C.project_run(TOKEN,value,spans);self.assertEqual(row['state'],'passed')
        group=report([row])['groups'][0]
        self.assertEqual(group['known_prompt_tokens'],0);self.assertEqual(group['missing_prompt_usage'],1)
        self.assertIsNone(row['model']['prompt_tokens']);self.assertIsNone(group['inference_cost_estimate'])

    def test_known_costs_use_token_ordered_float64_left_fold(self):
        rows=[]
        for letter,estimate in (('b',0.1),('c',0.2),('d',0.3)):
            value,spans=documents(identity=letter*32)
            value['audit']['records'][1]['inference_cost_estimate']=estimate
            rows.append(C.project_run(letter*32,value,spans))
        actual=report(list(reversed(rows)))
        self.assertEqual(actual['groups'][0]['inference_cost_estimate'],0.6000000000000001)
        self.assertEqual(actual,report(rows))
        self.assertEqual(C.validate_public(actual),actual)
        wrong=deepcopy(actual);wrong['groups'][0]['inference_cost_estimate']=0.6
        with self.assertRaises(C.ReportError):C.validate_public(wrong)

    def test_audit_finish_failure_or_cancel_closes_recorded_success_root_as_error(self):
        for state,code in (('failed','AUDIT_FAILED'),('interrupted','INTERRUPTED')):
            with self.subTest(state=state):
                value,spans=documents();value.update(result=state,error_code=code)
                value['audit'].update(verified=False,saved_spans_verified=False)
                spans[-1]['status']='ERROR'
                row=C.project_run(TOKEN,value,spans)
                self.assertEqual(row['state'],state);self.assertEqual(row['verification'],'saved-evidence')
                self.assertIsNone(row['findings']);self.assertFalse(row['audit']['producer_verified'])
                self.assertEqual(report([row])['command_result'],'complete')
                value['audit']['verified']=True
                self.assertEqual(C.project_run(TOKEN,value,spans)['state'],'invalid')
        value,spans=documents();spans[-1]['status']='ERROR'
        self.assertEqual(C.project_run(TOKEN,value,spans)['state'],'invalid')

    def test_unlocked_running_initial_and_terminal_missing_timestamp_are_incomplete(self):
        value,_=documents();value.update(result='running',finished_at=None,source=None,observation=None,
            model_evidence=None,diagnosis=None,model_calls=0,invocations=0,cleanup_verified=False,audit={'verified':False})
        row=C.project_run(TOKEN,value,[]);self.assertEqual(row['state'],'incomplete')
        self.assertEqual(row['recorded_result'],'running');self.assertEqual(row['verification'],'journal-only')
        self.assertEqual(report([row])['command_result'],'incomplete')
        value['result']='failed';value['error_code']='DIAGNOSE_FAILED';value['invocations']=1
        self.assertEqual(C.project_run(TOKEN,value,[])['state'],'incomplete')

    def test_pre_source_terminal_failure_is_journal_only_and_not_grouped(self):
        value,_=documents();value.update(result='failed',error_code='SOURCE_NOT_FOUND',source=None,observation=None,
            model_evidence=None,diagnosis=None,model_calls=0,cleanup_verified=False,audit={'verified':False})
        row=C.project_run(TOKEN,value,[]);self.assertEqual(row['state'],'failed')
        self.assertEqual(row['verification'],'journal-only');self.assertEqual(report([row])['groups'],[])

    def test_busy_never_reads_payload_or_promotes_stale_success(self):
        value,spans=documents()
        row=C.project_run(TOKEN,value,spans,'active')
        self.assertEqual(row,C.minimal(TOKEN,'active'));self.assertIsNone(row['recorded_result'])
        actual=report([row]);self.assertEqual(actual['command_result'],'complete');self.assertEqual(actual['groups'],[])

    def test_missing_spans_are_incomplete_but_changed_span_is_invalid(self):
        value,spans=documents()
        for missing in (None,[],spans[:-1]):
            row=C.project_run(TOKEN,value,missing);self.assertEqual(row['state'],'incomplete')
            self.assertEqual(row['recorded_result'],'passed');self.assertIsNone(row['findings'])
        for field,wrong in (('status','ERROR'),('trace_id','0'*32),('end_time_unix_nano',True),('attributes',{'sql':'private'})):
            changed=deepcopy(spans);changed[0][field]=wrong
            self.assertEqual(C.project_run(TOKEN,value,changed)['state'],'invalid')

    def test_query_provenance_replay_source_window_and_model_tamper_never_pass(self):
        edits=[lambda v:v.update(model_calls=True),lambda v:v['source'].update(sql_sha256='0'*64),
            lambda v:v.update(invocations=0),lambda v:v['observation'].update(source_sha256=None),
            lambda v:v['observation']['identity_after'].update(metadata_sha256='0'*64),
            lambda v:v['observation']['queries']['worker'][0].update(sql_sha256=v['source']['sql_sha256']),
            lambda v:v['observation']['runtime']['worker'].update(user='foreign'),
            lambda v:v['observation']['query_drain'].update(active=['unknown']),
            lambda v:v['diagnosis'].update(root_cause='known'),
            lambda v:v['model_evidence']['reader'].update(row_count=0),
            lambda v:v['audit']['records'][1].update(model_provider='private provider'),
            lambda v:v.update(raw_sql='private text')]
        for edit in edits:
            value,spans=documents(2);edit(value)
            self.assertEqual(C.project_run(TOKEN,value,spans)['state'],'invalid')

    def test_partial_audit_delivery_stays_failure_not_remote_verification(self):
        value,spans=failed_documents();value['audit']['records'][0]['audit_delivery']='failed'
        row=C.project_run(TOKEN,value,spans)
        self.assertEqual(row['state'],'failed');self.assertEqual(row['audit']['delivered_events'],1)
        self.assertFalse(row['audit']['producer_verified']);self.assertEqual(row['audit']['correlation'],'matched')

    def test_private_entity_raw_plan_rows_and_hashes_never_enter_public(self):
        value,spans=documents(2);row=C.project_run(TOKEN,value,spans)
        text=C.canonical(report([row])).decode()
        for secret in ('fixture_v1','8088807033386546123','1234567890123456789','SELECT ',
                       'metadata_sha256','private-plan-detail','row_count_estimate','schema_sha256','Shirokuma'):
            self.assertNotIn(secret,text)


class AggregateTests(unittest.TestCase):
    def test_all_single_order_and_profile_groups_are_consistent(self):
        rows=[]
        for profile,identity in ((2,'c'*32),(1,TOKEN)):
            v,s=documents(profile,identity=identity);rows.append(C.project_run(identity,v,s))
        actual=report(rows)
        self.assertEqual([r['evidence_token'] for r in actual['runs']],[TOKEN,'c'*32])
        self.assertEqual(len(actual['groups']),2)
        for row in rows:self.assertEqual(report([row],row['evidence_token'])['runs'][0],row)
        self.assertEqual(C.validate_public(actual),actual)

    def test_absent_all_zero_and_requested_missing_have_distinct_command_result(self):
        all_report=report([],root='absent');self.assertEqual(all_report['command_result'],'complete')
        self.assertEqual(all_report['groups'],[])
        single=report([C.minimal(TOKEN,'missing')],TOKEN,root='absent')
        self.assertEqual(single['command_result'],'incomplete');self.assertEqual(single['inventory']['counts']['missing'],1)

    def test_inventory_caps_unknown_names_and_unobserved_root_never_silently_succeed(self):
        for changes in ({'root':'not-observed','complete':False,'error_code':'REPORT_ROOT_INVALID'},
                        {'complete':False,'error_code':'REPORT_ENTRY_LIMIT'},
                        {'complete':False,'error_code':'REPORT_READ_LIMIT'},
                        {'complete':False,'unrecognized_entries':1,'error_code':'REPORT_UNRECOGNIZED_ENTRIES'}):
            self.assertEqual(report([],**changes)['command_result'],'incomplete')
        rows=[C.minimal(f'{i:032x}','active') for i in range(C.MAX_RUNS)]
        self.assertEqual(report(rows,complete=False,error_code='REPORT_ENTRY_LIMIT')['inventory']['inspected_entries'],128)
        with self.assertRaises(C.ReportError):report(rows+[C.minimal('f'*32,'active')])

    def test_unknown_failed_and_active_rows_excluded_from_success_denominators(self):
        v,s=documents();good=C.project_run(TOKEN,v,s)
        rows=[good,C.minimal('c'*32,'active'),C.minimal('d'*32,'invalid'),C.minimal('e'*32,'incomplete')]
        actual=report(rows);self.assertEqual(actual['command_result'],'incomplete')
        self.assertEqual(actual['groups'][0]['terminal_runs'],1)
        self.assertEqual(actual['groups'][0]['known_prompt_tokens'],100)

    def test_duplicate_identity_or_public_count_and_enum_forgery_is_rejected(self):
        v,s=documents();row=C.project_run(TOKEN,v,s)
        with self.assertRaises(C.ReportError):report([row,row])
        edits=[lambda r:r['inventory']['counts'].update(passed=9),lambda r:r.update(command_result='incomplete'),
            lambda r:r['runs'][0]['model'].update(call_intents=True),lambda r:r['groups'][0].update(terminal_runs=True),
            lambda r:r['runs'][0].update(error_code='PRIVATE_ERROR'),lambda r:r.update(raw='private'),
            lambda r:r['runs'][0].update(state='failed',findings=None,next_steps=None)]
        for edit in edits:
            value=report([row]);edit(value)
            with self.assertRaises((C.ReportError,ValueError)):C.validate_public(value)

    def test_cancel_is_not_converted_to_invalid_input(self):
        signal=KeyboardInterrupt();value,spans=documents()
        with patch.object(C.D,'validate_source',side_effect=signal):
            with self.assertRaises(KeyboardInterrupt) as caught:C.project_run(TOKEN,value,spans)
        self.assertIs(caught.exception,signal)

    def test_drain_compiler_matches_existing_pure_transport_statement(self):
        from tools.local_experiment.query_read import client
        self.assertEqual(C.inspection_sql(TOKEN),client.inspection_sql(TOKEN))
        self.assertEqual(C.QUERY_ERROR_CODES,client.ERROR_CODES)

    def test_public_size_failure_has_dedicated_type(self):
        value,spans=documents();row=C.project_run(TOKEN,value,spans)
        with patch.object(C,'MAX_PUBLIC_BYTES',1):
            with self.assertRaises(C.PublicLimitError):report([row])


if __name__=='__main__':unittest.main()
