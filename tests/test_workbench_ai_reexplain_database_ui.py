"""Saved-plan inference UI: real production handlers using the established Node adapter."""
import json
import unittest

import test_workbench_database_ui as harness
from test_workbench_bears_database_ui import COMMON
from test_workbench_ai_plan_database_ui import PLAN


SAVED = r'''
const savedEndpoint='/api/v1/bears/saved-plan-explanations';
const savedCalls=()=>calls.filter(c=>c.path.startsWith(savedEndpoint));
const savedPosts=()=>savedCalls().filter(c=>c.path===savedEndpoint&&c.options?.method==='POST');
const savedCancels=()=>savedCalls().filter(c=>c.path.endsWith('/cancel'));
const startSaved=(prefix='copilot')=>$('explanation-'+prefix+'-saved-plan').onclick();
const recoverSaved=()=>$('explanation-doctor-saved-plan-recover').onclick();
const historySaved=()=>$('explanation-copilot-saved-plan-history').onclick();
const cancelSaved=()=>$('explanation-doctor-saved-plan-cancel').onclick();
const savedRecord=(source,extra={})=>({id:'4a5de711-810a-4a9b-a1a9-2473b7faf13f',kind:'saved_plan_explanation',state:'SUCCEEDED',actor:'owner',context:structuredClone(source.context),
 input:{source_explanation_id:source.id,language:'en'},model:{provider:'local-openai',name:'new-local-model'},metadata:structuredClone(source.metadata),
 source_snapshot:{explanation_id:source.id,query_id:source.plan.query_id,state:source.state,query_state:'FINISHED',format:'TRINO_DISTRIBUTED_JSON',captured_at:source.plan.captured_at,sql:source.input.sql,operators:structuredClone(source.plan.operators)},
 text:'NEW SAVED PLAN ANSWER',error_code:null,cancel_requested:false,inference_state:'SUCCEEDED',sql_status:'NOT_EXECUTED',created_at:'2026-10-31T03:00:00Z',updated_at:'2026-10-31T03:01:00Z',...extra});
const savedPending=(source,extra={})=>savedRecord(source,{state:'RUNNING',inference_state:'RUNNING',text:null,...extra});
async function showSource(source=planRecord()){
 route=async()=>({context:structuredClone(source.context),explanations:[source]});await dbExplanationHistory('sql_plan_explanation');
 route=async()=>wrap(source);await dbExplanationSelect(0);return source;
}
async function newSaved(source=planRecord(),extra={}){
 await showSource(source);const result=savedRecord(source,extra);route=async()=>wrap(result);await startSaved();return result;
}
'''


class ReexplainDatabaseUI(unittest.TestCase):
    def javascript(self, body):
        harness.DatabaseUI.javascript(self, COMMON + PLAN + SAVED + body)

    def test_action_requires_fresh_get_collected_finished_original_including_unknown(self):
        self.javascript(r'''
await boot();assert.equal($('explanation-copilot-saved-plan').disabled,true);await startSaved();assert.equal(savedPosts().length,0);
route=async()=>wrap(planRecord());await startPlan();assert.equal($('explanation-copilot-saved-plan').disabled,true);
for(const state of ['SUCCEEDED','FAILED','CANCELED','UNKNOWN']){
 const source=planRecord({state,inference_state:state,text:state==='SUCCEEDED'?'old answer':null});await showSource(source);
 assert.equal($('explanation-doctor-saved-plan').disabled,false,state);assert.equal(dbExplanation.record.state,state);
}
for(const source of [pendingPlan(),pendingPlan({state:'QUEUED'}),pendingPlan({state:'UNKNOWN',inference_state:'UNKNOWN'}),planRecord({state:'RUNNING',inference_state:'RUNNING',text:null})]){
 await showSource(source);assert.equal($('explanation-copilot-saved-plan').disabled,true);await startSaved();
}
assert.equal(savedPosts().length,0);
''')

    def test_exact_frozen_source_body_is_persisted_before_post_and_inputs_results_untouched(self):
        self.javascript(r'''
await boot();editDoctor('SELECT doctor draft');const source=await showSource(planRecord({input:{sql:'SELECT saved_original',language:'ja'}}));
const editor=$('sql').value,doctor=$('bear-query-sql').value,worksheet=JSON.stringify(dbInput(dbCurrent()));
setLanguage('ja');queryResult={columns:['keep'],rows:[[7]]};dbRuns.set('keep',{marker:7});const results=queryResult,runs=JSON.stringify([...dbRuns]);
route=async(path,options)=>{
 assert.equal(path,savedEndpoint);const body=JSON.parse(options.body),stored=JSON.parse(storage.get(dbExplanationKey)).at(-1);
 assert.deepEqual(Object.keys(body).sort(),['context','language','source_explanation_id']);assert.deepEqual(body,{context:dbContext(dbCurrent()),source_explanation_id:source.id,language:'ja'});
 assert.equal(stored.key,options.headers['Idempotency-Key']);assert.deepEqual(stored.body,body);assert.equal(options.headers['X-Shirokuma-Role'],'admin');
 return wrap(savedRecord(source,{input:{source_explanation_id:source.id,language:'ja'}}));
};await startSaved('doctor');assert.equal(savedPosts().length,1);assert.equal(planPosts().length,0);
assert.equal($('sql').value,editor);assert.equal($('bear-query-sql').value,doctor);assert.equal(queryResult,results);assert.equal(JSON.stringify([...dbRuns]),runs);assert.equal(JSON.stringify(dbInput(dbCurrent())),worksheet);
const intent=dbSavedIntent();assert.ok(Object.isFrozen(intent.body));assert.ok(Object.isFrozen(intent.body.context));assert.ok(Object.isFrozen(intent.capture));
assert.equal(dbExplanation.record.kind,'saved_plan_explanation');assert.equal($('explanation-copilot-saved-plan').disabled,true);await startSaved();assert.equal(savedPosts().length,1);
''')

    def test_saved_answer_first_historical_unknown_phase_safe_tree_and_view_disclosure(self):
        self.javascript(r'''
await boot();const source=planRecord({state:'UNKNOWN',inference_state:'UNKNOWN',text:null,metadata:viewMetadata()});await newSaved(source);
for(const locale of ['en','ja']){
 setLanguage(locale);const node=$('explanation-doctor-output'),text=visibleText(node);
 assert.equal(node.children[0].textContent,'NEW SAVED PLAN ANSWER');assert.ok(text.includes(t('Saved plan, as of saved plan time')));assert.ok(text.includes(t('Original explanation outcome')+': '+dbExplanationStateLabel('UNKNOWN')));
 assert.ok(text.includes(source.plan.captured_at));assert.ok(text.includes(source.input.sql));assert.ok(text.includes(t('Local explanation')+': '+dbExplanationStateLabel('SUCCEEDED')));assert.ok(!text.includes(t('Plan query')+':'));
 assert.match(text,/ScanFilterProject/);assert.match(text,/shared_leaf/);assert.match(text,/nested_view/);assert.doesNotMatch(text,/private-engine-id|backend_query_id|rawOperator|originalmodel/);
 const details=node.querySelector('details');details.open=true;dbExplanationSync();assert.equal(node.querySelector('details'),details);assert.equal(details.open,true);
 route=async()=>wrap(savedRecord(source));await recoverSaved();assert.equal(node.querySelector('details'),details);assert.equal(details.open,true);
}
''')

    def test_accepted_recovery_get_only_after_both_drafts_reload_and_new_source(self):
        for state in ['QUEUED', 'RUNNING', 'UNKNOWN']:
            with self.subTest(state=state):
                self.javascript('const state='+json.dumps(state)+r''';
await boot();const source=await showSource(),accepted=savedPending(source,{state,inference_state:state==='QUEUED'?'NOT_STARTED':state});route=async()=>wrap(accepted);await startSaved();const original=structuredClone(savedPosts()[0]);
editSQL('SELECT different editor');editDoctor('SELECT different Doctor');assert.equal(dbExplanation.record,null);
const other=planRecord({id:'bb7de711-810a-4a9b-a1a9-2473b7faf13f'});await showSource(other);assert.equal($('explanation-copilot-saved-plan').disabled,true);await startSaved();assert.equal(savedPosts().length,1);
dbExplanationInstall();assert.equal($('explanation-doctor-saved-plan-recover').disabled,false);assert.equal($('explanation-latest').disabled,true);const count=savedCalls().length;await dbExplanationPoll();dbControls();assert.equal(savedCalls().length,count);
route=async(path,options)=>{assert.ok(path.startsWith(savedEndpoint+'/'+accepted.id+'?'));assert.equal(options.method,undefined);assert.equal(new URL(path,'http://local').searchParams.get('context'),JSON.stringify(accepted.context));return wrap(accepted);};await recoverSaved();
assert.equal(savedPosts().length,1);assert.equal($('explanation-latest').disabled,true);assert.equal($('sql').value,'SELECT different editor');assert.equal($('bear-query-sql').value,'SELECT different Doctor');assert.equal(savedPosts()[0].options.body,original.options.body);
''')

    def test_uncertain_ack_exact_replay_after_drafts_source_switch_and_reload(self):
        self.javascript(r'''
await boot();const source=await showSource();route=async()=>{throw Error('lost acknowledgement');};await startSaved();const original=structuredClone(savedPosts()[0]);
editSQL('SELECT edited editor');editDoctor('SELECT edited Doctor');await showSource(planRecord({id:'bb7de711-810a-4a9b-a1a9-2473b7faf13f'}));assert.equal($('explanation-copilot-saved-plan').disabled,true);
dbExplanationInstall();const count=calls.length;await dbExplanationPoll();dbControls();assert.equal(calls.length,count);
route=async(path,options)=>{assert.equal(path,savedEndpoint);assert.equal(options.body,original.options.body);assert.equal(options.headers['Idempotency-Key'],original.options.headers['Idempotency-Key']);return wrap(savedPending(source));};
await recoverSaved();assert.equal(savedPosts().length,2);assert.equal(dbSavedIntent().id,savedPending(source).id);assert.equal(dbSavedIntent().body.source_explanation_id,source.id);assert.equal($('explanation-latest').disabled,true);
''')

    def test_reload_drops_terminal_hints_until_fresh_terminal_receipt(self):
        self.javascript(r'''
await boot();const source=await showSource();route=async()=>wrap(savedPending(source));await startSaved();const id=dbSavedIntent().id;
const hints=JSON.parse(storage.get(dbExplanationKey));Object.assign(hints.at(-1),{state:'SUCCEEDED',terminal:true,noAdmission:true,inference_state:'SUCCEEDED'});storage.set(dbExplanationKey,JSON.stringify(hints));
dbExplanationInstall();assert.equal($('explanation-latest').disabled,true);await dbSourceLoad();assert.equal(sourceCalls().length,0);
route=async()=>wrap(savedRecord(source,{id}));await recoverSaved();assert.equal($('explanation-latest').disabled,false);
dbExplanationInstall();assert.equal($('explanation-latest').disabled,true);route=async()=>({context:dbContext(dbCurrent()),explanations:[savedRecord(source,{id})]});await historySaved();assert.equal($('explanation-latest').disabled,false);assert.equal(savedPosts().length,1);
''')

    def test_history_list_and_detail_are_kind_scoped_get_only_with_no_boot_submission(self):
        self.javascript(r'''
await boot();dbExplanationInstall();dbControls();await dbExplanationPoll();assert.equal(savedCalls().length,0);
const source=planRecord(),saved=savedRecord(source);route=async(path,options)=>{assert.ok(path.startsWith(savedEndpoint+'?'));assert.equal(options.method,undefined);return {context:dbContext(dbCurrent()),explanations:[saved,source,record()]};};await historySaved();
assert.equal(dbExplanation.history.length,1);assert.equal(dbExplanation.historyKind,'saved_plan_explanation');assert.match($('explanation-doctor-saved').options[1].textContent,/SELECT/);
route=async(path,options)=>{assert.ok(path.startsWith(savedEndpoint+'/'+saved.id+'?'));assert.equal(options.method,undefined);return wrap(saved);};await dbExplanationSelect(0);
assert.equal(dbExplanation.record.id,saved.id);assert.equal(dbExplanation.displayOrigin,'history');assert.equal($('explanation-doctor-saved-plan').disabled,true);const count=savedCalls().length;await dbExplanationPoll();assert.equal(savedCalls().length,count);assert.equal(savedPosts().length,0);
''')

    def test_poll_only_displayed_own_id_never_replays_a_hidden_intent(self):
        self.javascript(r'''
await boot();const source=await showSource();route=async()=>wrap(savedPending(source));await startSaved();const shown=dbExplanation.record,first=dbSavedIntent();
// A second restored held intent must never become a hidden polling target.
const capture=structuredClone(first.capture),second=dbExplanationFreezeIntent({key:crypto.randomUUID(),capture,source:'doctor',kind:'saved_plan_explanation',endpoint:savedEndpoint,body:{...first.body,source_explanation_id:'bb7de711-810a-4a9b-a1a9-2473b7faf13f'},id:null});dbExplanation.intents.push(second);
route=async(path,options)=>{assert.ok(path.startsWith(savedEndpoint+'/'+shown.id+'?'));assert.equal(options.method,undefined);return wrap(savedPending(source));};await dbExplanationPoll();assert.equal(savedPosts().length,1);assert.equal(dbExplanation.record.id,shown.id);
editDoctor('SELECT changed');const count=savedCalls().length;await dbExplanationPoll();assert.equal(savedCalls().length,count);assert.equal(first.id,shown.id);assert.equal(second.id,null);
''')

    def test_no_admission_only_fresh_structured_original_post_409_memory_proof(self):
        for envelope in ['top', 'nested']:
            with self.subTest(envelope=envelope):
                self.javascript('const envelope='+json.dumps(envelope)+r''';
await boot();const source=await showSource();route=async()=>({http:409,body:envelope==='top'?{code:'AI_SAVED_PLAN_UNAVAILABLE'}:{error:{code:'AI_SAVED_PLAN_UNAVAILABLE'}}});await startSaved();
const intent=dbSavedIntent(),original=structuredClone(savedPosts()[0]);assert.equal(intent.id,null);assert.equal(dbSavedProven(intent),true);assert.equal($('explanation-latest').disabled,false);
editSQL('SELECT changed');editDoctor('SELECT changed Doctor');assert.equal(dbSavedProven(intent),true);
dbExplanationInstall();assert.equal(dbSavedProven(dbSavedIntent()),false);assert.equal($('explanation-latest').disabled,true);
route=async(path,options)=>{assert.equal(options.body,original.options.body);assert.equal(options.headers['Idempotency-Key'],original.options.headers['Idempotency-Key']);return {http:409,body:{code:'AI_SAVED_PLAN_UNAVAILABLE'}};};await recoverSaved();assert.equal(dbSavedProven(dbSavedIntent()),true);
route=async()=>{throw Error('uncertain');};await recoverSaved();assert.equal(dbSavedProven(dbSavedIntent()),false);assert.equal($('explanation-latest').disabled,true);
''')

    def test_untrusted_rejections_and_accepted_get404_never_unlock(self):
        self.javascript(r'''
await boot();const responses=[
 {http:404,body:{code:'AI_SAVED_PLAN_UNAVAILABLE'}},{http:400,body:{code:'AI_SAVED_PLAN_UNAVAILABLE'}},{http:401,body:{code:'AI_SAVED_PLAN_UNAVAILABLE'}},{http:403,body:{code:'AI_SAVED_PLAN_UNAVAILABLE'}},{http:500,body:{code:'AI_SAVED_PLAN_UNAVAILABLE'}},
 {code:'AI_SAVED_PLAN_UNAVAILABLE'},{http:409,body:{message:'AI_SAVED_PLAN_UNAVAILABLE'}},{http:409,body:{error:{message:'AI_SAVED_PLAN_UNAVAILABLE'}}},
 {http:409,body:{code:'AI_SAVED_PLAN_UNAVAILABLE',id:'accepted'}},{http:409,body:{code:'AI_SAVED_PLAN_UNAVAILABLE',explanation:{id:'accepted'}}},{http:409,body:{error:{code:'AI_SAVED_PLAN_UNAVAILABLE',request_id:'accepted'}}}
];
for(const reply of responses){independentWorksheet();await showSource();route=async()=>reply;await startSaved();assert.equal(dbSavedProven(dbSavedIntent()),false);assert.equal($('explanation-latest').disabled,true);}
independentWorksheet();const source=await showSource();route=async()=>wrap(savedPending(source));await startSaved();route=async()=>({http:404,body:{code:'AI_SAVED_PLAN_UNAVAILABLE'}});await recoverSaved();assert.equal(dbSavedProven(dbSavedIntent()),false);
''')

    def test_cancel_targets_only_own_id_and_completion_wins_with_answer(self):
        self.javascript(r'''
await boot();const source=await showSource(planRecord({state:'UNKNOWN',inference_state:'UNKNOWN',text:null}));route=async()=>wrap(savedPending(source));await startSaved();const own=dbExplanation.record;
route=async(path,options)=>{assert.equal(path,savedEndpoint+'/'+own.id+'/cancel');assert.deepEqual(JSON.parse(options.body),{context:own.context});assert.equal(options.headers['X-Shirokuma-Role'],'admin');return wrap(savedRecord(source,{cancel_requested:true}));};await cancelSaved();
assert.equal(savedCancels().length,1);assert.equal(cancelCalls().length,0);assert.equal(dbExplanation.record.text,'NEW SAVED PLAN ANSWER');assert.equal(dbSavedProven(dbSavedIntent()),true);assert.equal($('explanation-doctor-saved-plan-cancel').disabled,true);assert.match(visibleText($('explanation-doctor-output')),/NEW SAVED PLAN ANSWER/);assert.doesNotMatch(visibleText($('explanation-doctor-output')),/Physical model stoppage/);
''')

    def test_cancel_unknown_keeps_hold_and_result_discard_has_no_stop_claim(self):
        self.javascript(r'''
await boot();const source=await showSource();route=async()=>wrap(savedPending(source));await startSaved();
route=async()=>wrap(savedPending(source,{cancel_requested:true}));await cancelSaved();assert.ok(visibleText($('explanation-doctor-output')).includes(t('Model result will be discarded. Physical model stoppage is not confirmed.')));assert.equal($('explanation-doctor-saved-plan-cancel').disabled,true);assert.equal(dbSavedProven(dbSavedIntent()),false);
route=async()=>wrap(savedPending(source,{state:'UNKNOWN',inference_state:'UNKNOWN',cancel_requested:true}));await recoverSaved();assert.equal($('explanation-latest').disabled,true);assert.equal($('explanation-doctor-saved-plan-cancel').disabled,true);
route=async()=>wrap(savedRecord(source,{state:'CANCELED',inference_state:'CANCELED',text:null,cancel_requested:true}));await recoverSaved();assert.equal($('explanation-latest').disabled,false);assert.equal(cancelCalls().length,0);
''')

    def test_original_a2_unknown_stays_unknown_and_new_plan_held_after_a5_success(self):
        self.javascript(r'''
await boot();const source=planRecord({state:'UNKNOWN',inference_state:'UNKNOWN',text:null});route=async()=>wrap(source);await startPlan();route=async()=>wrap(source);await recoverPlan();
const original=dbExplanationIntent('sql_plan_explanation'),frozen=JSON.stringify(original),proof=dbPlanProofs.get(original);assert.equal($('explanation-copilot-saved-plan').disabled,false);assert.equal($('explanation-copilot-plan').disabled,true);
route=async()=>wrap(savedRecord(source));await startSaved();assert.equal(JSON.stringify(original),frozen);assert.equal(dbPlanProofs.get(original),proof);assert.equal(dbPlanProven(original),false);assert.equal($('explanation-copilot-plan').disabled,true);assert.equal($('explanation-latest').disabled,true);assert.equal(planPosts().length,1);
route=async()=>wrap(source);await recoverPlan();assert.equal(dbExplanation.record.state,'UNKNOWN');assert.equal(dbExplanation.record.inference_state,'UNKNOWN');assert.equal($('explanation-copilot-saved-plan').disabled,false);
''')

    def test_storage_failure_blocks_post_and_retains_original_recovery_key(self):
        self.javascript(r'''
await boot();const source=await showSource(),write=localStorage.setItem;localStorage.setItem=()=>{throw Error('blocked storage');};await startSaved();const intent=dbSavedIntent();assert.equal(savedPosts().length,0);assert.ok(intent);assert.match(dbExplanation.notice,/No request was sent/);
localStorage.setItem=write;route=async(path,options)=>{assert.equal(options.headers['Idempotency-Key'],intent.key);return wrap(savedPending(source));};await recoverSaved();assert.equal(savedPosts().length,1);assert.equal(dbSavedIntent(),intent);
''')

    def test_endpoint_storage_install_kind_validation_preserves_legacy_intents(self):
        self.javascript(r'''
await boot();route=async()=>wrap(record({state:'RUNNING',text:null}));await $('copilot-explain').onclick();const a1=JSON.parse(storage.get(dbExplanationKey))[0];delete a1.kind;delete a1.endpoint;
await showSource();route=async()=>{throw Error('lost');};await startSaved();const a5=JSON.parse(storage.get(dbExplanationKey)).at(-1),invalid=[
 {...a5,key:'bad-url',endpoint:'/api/v1/queries'}, {...a5,key:'bad-extra',body:{...a5.body,sql:'SELECT raw'}}, {...a5,key:'bad-id',body:{...a5.body,source_explanation_id:'not-uuid'}}, {...a5,key:'bad-kind',kind:'unknown_kind'}
];storage.set(dbExplanationKey,JSON.stringify([a1,a5,...invalid]));dbExplanationInstall();assert.equal(dbExplanation.intents.length,2);assert.equal(dbExplanation.intents[0].kind,'sql_explanation');assert.equal(dbSavedIntent().endpoint,savedEndpoint);assert.equal($('explanation-latest').disabled,true);
''')

    def test_text_is_literal_and_private_fields_never_reach_the_rendered_dto(self):
        self.javascript(r'''
await boot();const source=await showSource();const result=savedRecord(source,{text:'<img src=x onerror=bad()>',private_config:'SECRET',backend_id:'SECRET',model:{provider:'local-openai',name:'<b>model</b>',private_config:'SECRET'}});route=async()=>wrap(result);await startSaved();
const node=$('explanation-doctor-output');assert.equal(node.children[0].tagName,'PRE');assert.equal(node.children[0].textContent,result.text);assert.equal(node.descendants().some(n=>n.tagName==='IMG'),false);assert.doesNotMatch(JSON.stringify(dbExplanation.record),/SECRET|private_config|backend_id/);assert.doesNotMatch(visibleText(node),/SECRET/);
''')


    def test_unrelated_recovery_and_hidden_poll_never_replay_unknown_ack(self):
        self.javascript(r'''
await boot();const source=await showSource();route=async()=>{throw Error('lost');};await startSaved();const original=structuredClone(savedPosts()[0]);
editDoctor('SELECT new doctor');editSQL('SELECT new editor');await showSource(planRecord({id:'bb7de711-810a-4a9b-a1a9-2473b7faf13f'}));const count=savedCalls().length;
for(const label of ['doctor','copilot','unrelated','poll'])await dbSavedExplain(label,true);
await dbExplanationPoll();assert.equal(savedCalls().length,count);assert.equal(savedPosts().length,1);
route=async(path,options)=>{assert.equal(options.body,original.options.body);assert.equal(options.headers['Idempotency-Key'],original.options.headers['Idempotency-Key']);return wrap(savedPending(source));};await recoverSaved();assert.equal(savedPosts().length,2);
''')

    def test_install_inflight_invalidates_old_token_without_finishing_new_recovery(self):
        for response in ['success', 'error', 'get']:
            with self.subTest(response=response):
                self.javascript('const response='+json.dumps(response)+r''';
await boot();const source=planRecord({state:'UNKNOWN',inference_state:'UNKNOWN',text:null});route=async()=>wrap(source);await startPlan();route=async()=>wrap(source);await recoverPlan();const a2=dbExplanationIntent('sql_plan_explanation');
if(response==='get'){route=async()=>wrap(savedPending(source));await startSaved();}
const old=deferred();route=()=>old.promise;const pending=response==='get'?recoverSaved():startSaved();await tick();const oldToken=dbSavedBusy;assert.ok(oldToken);
const original=structuredClone(savedPosts()[0]);dbExplanationInstall();assert.equal(dbSavedBusy,null);assert.equal(dbExplanation.busy,false);assert.equal($('explanation-doctor-saved-plan-recover').disabled,false);
const fresh=deferred();route=()=>fresh.promise;const recovering=recoverSaved();await tick();const newToken=dbSavedBusy;assert.ok(newToken);assert.notEqual(newToken,oldToken);const intent=dbSavedIntent(),a2Proof=dbPlanProofs.get(a2);
if(response==='get')assert.equal(savedPosts().length,1);else{assert.equal(savedPosts().length,2);assert.equal(savedPosts()[1].options.body,original.options.body);assert.equal(savedPosts()[1].options.headers['Idempotency-Key'],original.options.headers['Idempotency-Key']);}
old.resolve(response==='error'?{http:409,body:{code:'AI_SAVED_PLAN_UNAVAILABLE'}}:wrap(savedRecord(source,{text:'OLD RESPONSE'})));await pending;
assert.equal(dbSavedBusy,newToken);assert.equal(dbExplanation.busy,true);assert.equal(dbSavedProven(intent),false);assert.equal(dbExplanation.record?.text??null,null);assert.equal(dbPlanProofs.get(a2),a2Proof);
fresh.resolve(wrap(savedRecord(source)));await recovering;assert.equal(dbSavedBusy,null);assert.equal(dbExplanation.busy,false);assert.equal(dbExplanation.record.text,'NEW SAVED PLAN ANSWER');assert.equal(dbSavedProven(intent),true);assert.equal($('explanation-copilot-plan').disabled,true);assert.equal($('explanation-latest').disabled,true);assert.equal(dbPlanProofs.get(a2),a2Proof);assert.equal(source.state,'UNKNOWN');
''')

    def test_late_full_capture_authority_draft_and_selection_changes_discard(self):
        self.javascript(r'''
await boot();const mutations=[
 ()=>editDoctor('SELECT late Doctor'),()=>editSQL('SELECT late editor'),()=>editDoctor($('bear-query-sql').value),
 ()=>{const before=$('sql').value;editSQL('SELECT temp');editSQL(before);},
 ()=>{currentRole='bi_viewer';dbControls();currentRole='admin';dbControls();},
 ()=>{dbMaximumRole='bi_viewer';dbControls();dbMaximumRole='admin';dbControls();},
 ()=>{const before=dbPrincipal;dbPrincipal=JSON.stringify(['other',true]);dbControls();dbPrincipal=before;dbControls();},
 ()=>{dbAuthorizationVersion++;dbControls();},()=>{dbEpoch++;dbControls();},
 ()=>{const w=dbCurrent(),before=w.schema_id;w.schema_id='raw';dbControls();w.schema_id=before;dbControls();},
 ()=>{const before=worksheetId;worksheetId='b';dbControls();worksheetId=before;dbControls();},
 ()=>{dbExplanation.record=null;++dbExplanation.sequence;dbExplanationSync();}
];
for(const mutate of mutations){
 independentWorksheet();await dbRefreshContext();editSQL('SELECT original');editDoctor('SELECT doctor');const source=await showSource(),wait=deferred();route=()=>wait.promise;const pending=startSaved();await tick();const intent=dbSavedIntent();assert.ok(dbSavedBusy);
 mutate();assert.equal($('copilot-explain').disabled,true);assert.equal($('explanation-copilot-plan').disabled,true);
 wait.resolve(wrap(savedRecord(source)));await pending;if(dbReason())await dbRefreshContext();dbControls();
 assert.equal(dbSavedProven(intent),false,'unproven '+mutate);assert.equal(dbExplanation.record,null,'no stale display '+mutate);assert.equal($('explanation-latest').disabled,true);assert.equal(dbSavedBusy,null);
}
''')

    def test_invalid_actor_context_id_source_language_snapshot_and_operators_rejected(self):
        self.javascript(r'''
await boot();const corruptions=[
 r=>r.actor='another',r=>r.kind='sql_plan_explanation',r=>r.id='bad-id',r=>r.id=r.input.source_explanation_id,
 r=>r.context.schema_id='other',r=>r.context.extra='forbidden',r=>r.input.language='fr',r=>r.input.sql='injected',
 r=>r.input.source_explanation_id='bb7de711-810a-4a9b-a1a9-2473b7faf13f',r=>r.source_snapshot.explanation_id='bad',r=>r.source_snapshot.query_id='bad',
 r=>r.source_snapshot.query_state='UNKNOWN',r=>r.source_snapshot.state='RUNNING',r=>r.source_snapshot.format='raw',r=>r.source_snapshot.sql='',
 r=>r.source_snapshot.backend_id='PRIVATE',r=>r.source_snapshot.text='ORIGINAL ANSWER',r=>r.source_snapshot.plan={},r=>delete r.source_snapshot.captured_at,
 r=>r.source_snapshot.operators.fragments[0].root.operator='rawOperator',r=>r.source_snapshot.operators.fragments[0].root.attributes.raw='PRIVATE',
 r=>r.source_snapshot.operators.fragments[0].root.extra='PRIVATE',r=>r.metadata.extra='PRIVATE',r=>r.metadata.relations=[{...relation('raw','table'),definition:'PRIVATE'}],
 r=>r.metadata.relations=[relation('raw','table','TABLE',[{name:'n',type:'bigint',private:'PRIVATE'}])],r=>r.plan_status='COLLECTED',r=>r.plan={},r=>r.query={},r=>r.sql_status='EXECUTED',r=>r.inference_state='invented'
];
for(const corrupt of corruptions){
 independentWorksheet();await showSource();const source=dbExplanation.record,result=savedRecord(source);corrupt(result);route=async()=>wrap(result);await startSaved();
 assert.equal(dbExplanation.record,null,'reject '+corrupt);assert.equal(dbSavedProven(dbSavedIntent()),false);assert.equal($('explanation-latest').disabled,true);
}
''')

    def test_view_metadata_closed_bounds_and_invalid_history_cancel_detail_rejected(self):
        self.javascript(r'''
await boot();const source=planRecord({metadata:viewMetadata()});const corruptions=[
 r=>delete r.metadata.view_dependencies,r=>r.metadata.view_dependencies.push(r.metadata.relations[0]),
 r=>r.metadata.view_dependencies=[...Array(65)].map((_,i)=>relation('raw','leaf'+i)),
 r=>r.metadata.view_dependencies=[relation('raw','huge','TABLE',[{name:'x'.repeat(65536),type:'bigint'}])],
 r=>r.metadata.view_dependencies[0].private='PRIVATE',r=>r.metadata.view_dependencies[0].columns[0].definition='PRIVATE'
];
for(const corrupt of corruptions){independentWorksheet();await showSource(planRecord({metadata:viewMetadata()}));const result=savedRecord(dbExplanation.record);corrupt(result);route=async()=>wrap(result);await startSaved();assert.equal(dbExplanation.record,null);assert.equal(dbSavedProven(dbSavedIntent()),false);}
independentWorksheet();await showSource(source);route=async()=>wrap(savedPending(source));await startSaved();const accepted=dbExplanation.record,intent=dbSavedIntent();
route=async()=>({context:dbContext(dbCurrent()),explanations:[{...savedRecord(source),actor:'other'}]});await historySaved();assert.deepEqual(dbExplanation.history,[]);assert.equal(dbSavedProven(intent),false);
route=async()=>({context:dbContext(dbCurrent()),explanations:[savedPending(source)]});await historySaved();route=async()=>wrap({...savedPending(source),id:'bb7de711-810a-4a9b-a1a9-2473b7faf13f'});await dbExplanationSelect(0);assert.equal(dbExplanation.record,null);
route=async()=>wrap(accepted);await recoverSaved();route=async()=>wrap({...savedRecord(source),actor:'other'});await cancelSaved();assert.equal(dbSavedProven(intent),false);assert.equal(dbExplanation.record.state,'RUNNING');assert.equal(cancelCalls().length,0);
''')


    def test_full9_authority_roundtrip_and_terminal_inference_are_independent_guards(self):
        self.javascript(r'''
await boot();
for(const key of dbExplanationContextKeys){
 independentWorksheet();const source=await showSource(),wait=deferred();route=()=>wait.promise;const pending=startSaved();await tick();const intent=dbSavedIntent(),w=dbCurrent();
 // Each frozen context field must invalidate even when restored before the response.
 const target=['grant_revision','sql_catalog'].includes(key)?dbRoleItems(w.selected_role).find(d=>d.id===w.database_id):key==='sql_schema'?dbSchemasFor(w).find(s=>s.id===w.schema_id):w;
 const field={warehouse_id:'warehouse',sql_schema:'sql_name'}[key]||key;
 const value=target[field];target[field]='different';dbControls();target[field]=value;dbControls();
 wait.resolve(wrap(savedRecord(source)));await pending;
 assert.equal(dbSavedProven(intent),false,key);assert.equal(dbExplanation.record,null,key);assert.equal($('explanation-latest').disabled,true,key);
}
independentWorksheet();const source=await showSource();route=async()=>wrap(savedPending(source));await startSaved();const intent=dbSavedIntent();
for(const state of ['FAILED','CANCELED'])for(const inference_state of ['RUNNING','UNKNOWN']){
 route=async()=>wrap(savedRecord(source,{state,inference_state,text:null}));await recoverSaved();assert.equal(dbSavedProven(intent),false);assert.equal($('explanation-latest').disabled,true);
}
route=async()=>wrap(savedRecord(source,{state:'FAILED',inference_state:'FAILED',text:null}));await recoverSaved();assert.equal(dbSavedProven(intent),true);assert.equal($('explanation-latest').disabled,false);
dbAuthorizationVersion++;dbControls();assert.equal(dbSavedProven(intent),false);assert.equal($('explanation-latest').disabled,true);
''')
