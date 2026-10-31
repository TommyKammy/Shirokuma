"""Plan explanation UI: production handlers with isolated Node DOM/fetch fixtures."""
import json
import re
import subprocess
import unittest

import test_workbench_database_ui as harness
from test_workbench_bears_database_ui import COMMON


PLAN = r'''
const planEndpoint='/api/v1/bears/plan-explanations';
const planCalls=()=>calls.filter(c=>c.path.startsWith(planEndpoint));
const planPosts=()=>planCalls().filter(c=>c.path===planEndpoint&&c.options?.method==='POST');
const cancelCalls=()=>planCalls().filter(c=>c.path.endsWith('/cancel'));
const op=(operator,children=[],attributes={},remote_sources=[])=>({operator,children,attributes,remote_sources});
const projection=()=>({projection_version:1,fragments:[
 {id:'f0',root:op('Output',[op('Aggregate',[op('RemoteSource',[],{},['f1'])],{phase:'FINAL'})])},
 {id:'f1',root:op('LocalExchange',[op('ScanFilterProject')],{partitioning:'HASH'})}
]});
const planRecord=(extra={})=>({...record(),id:'a254ca08-a6f3-4392-940d-85df9f8a8200',kind:'sql_plan_explanation',
 state:'SUCCEEDED',text:'Captured optimizer explanation',plan_status:'COLLECTED',cancel_requested:false,inference_state:'SUCCEEDED',
 plan:{format:'TRINO_DISTRIBUTED_JSON',query_id:'ba7de711-810a-4a9b-a1a9-2473b7faf13f',backend_query_id:'private-engine-id',
 query_state:'FINISHED',execution_closure:null,captured_at:'2026-10-31T02:30:00Z',operators:projection(),error_code:null},...extra});
const pendingPlan=(extra={})=>planRecord({state:'RUNNING',text:null,plan_status:'NOT_COLLECTED',inference_state:'NOT_STARTED',
 plan:{...planRecord().plan,query_state:'RUNNING',operators:null,captured_at:null},...extra});
const relation=(schema,table,kind='TABLE',columns=[{name:'value',type:'bigint'}])=>({schema,table,kind,columns});
const viewMetadata=()=>({captured_at:'2026-10-31T02:30:00Z',relations:[relation('raw','source_table'),relation('sandbox','report_view','VIEW')],
 view_dependencies:[relation('raw','shared_leaf'),relation('staging','nested_view','VIEW')]});
const startPlan=source=>$('explanation-'+(source||'copilot')+'-plan').onclick();
const recoverPlan=()=>$('explanation-copilot-plan-recover').onclick();
const cancelPlan=()=>$('explanation-doctor-plan-cancel').onclick();
// Independent fixture operations use distinct worksheet identities, as a user can.
const independentWorksheet=()=>{const w={...dbCurrent(),id:crypto.randomUUID(),sql:$('sql').value};worksheets.push(w);worksheetId=w.id;dbControls();};
'''


class PlanDatabaseUI(unittest.TestCase):
    def javascript(self, body):
        harness.DatabaseUI.javascript(self, COMMON + PLAN + body)

    def test_latest_loader_plan_hold_survives_edits_reload_and_uses_original_recovery(self):
        self.javascript(r'''
await boot();const accepted=pendingPlan();route=async()=>wrap(accepted);await startPlan();const original=structuredClone(planPosts()[0]);
editSQL('SELECT new editor');editDoctor('SELECT new doctor');$('explanation-copy').onclick();
for(const reload of [false,true]){
 if(reload){const saved=JSON.parse(storage.get(dbExplanationKey));Object.assign(saved[0],{terminal:true,state:'SUCCEEDED',noAdmission:true});storage.set(dbExplanationKey,JSON.stringify(saved));dbExplanationInstall();}
 assert.equal($('explanation-latest').disabled,true);await dbSourceLoad();assert.equal(sourceCalls().length,0);
}
for(const reply of [pendingPlan({state:'UNKNOWN',input:accepted.input}),pendingPlan({input:accepted.input}),planRecord({input:accepted.input})]){
 route=async(path,options)=>{assert.equal(options?.method,undefined);assert.ok(path.startsWith(planEndpoint+'/'+accepted.id+'?'));assert.equal(new URL(path,'http://local').searchParams.get('context'),JSON.stringify(accepted.context));return wrap(reply);};
 await recoverPlan();assert.equal($('explanation-latest').disabled,reply.state!=='SUCCEEDED','terminal source guard '+reply.state+' '+dbExplanation.notice);
}
assert.equal(planPosts().length,1);assert.equal(JSON.stringify(dbExplanationIntent('sql_plan_explanation').body),original.options.body);assert.equal(dbExplanationIntent('sql_plan_explanation').key,original.options.headers['Idempotency-Key']);
route=async()=>sourceReply();await loadSource();assert.ok(dbSource.origin);
route=async(path,options)=>{const body=JSON.parse(options.body);assert.deepEqual(Object.keys(body).sort(),['context','language','sql']);return wrap(planRecord({input:{sql:body.sql,language:body.language}}));};
await startPlan('doctor');assert.equal(planPosts().length,2);assert.equal(JSON.parse(planPosts()[1].options.body).sql,sourceReply().source.sql);
''')

    def test_latest_loader_both_kinds_hold_until_every_intent_has_fresh_terminal_receipt(self):
        self.javascript(r'''
await boot();const a1=record({state:'RUNNING',text:null}),a2=pendingPlan();
route=async()=>wrap(a1);await $('copilot-explain').onclick();route=async()=>wrap(a2);await startPlan();
assert.equal($('explanation-latest').disabled,true);route=async()=>wrap(planRecord());await recoverPlan();assert.equal($('explanation-latest').disabled,true);
route=async()=>wrap({...a1,state:'FAILED'});await $('explanation-copilot-recover').onclick();assert.equal($('explanation-latest').disabled,false);
dbExplanationInstall();assert.equal($('explanation-latest').disabled,true);route=async()=>wrap(planRecord());await recoverPlan();assert.equal($('explanation-latest').disabled,true);route=async()=>wrap({...a1,state:'FAILED'});await $('explanation-copilot-recover').onclick();assert.equal($('explanation-latest').disabled,false);
''')

    def test_latest_loader_plan_uncertain_ack_remains_frozen_after_reload(self):
        self.javascript(r'''
await boot();route=async()=>{throw Error('lost');};await startPlan();const original=structuredClone(planPosts()[0]);editDoctor('SELECT changed');editSQL('SELECT edited');dbExplanationInstall();
assert.equal($('explanation-latest').disabled,true);await dbSourceLoad();assert.equal(sourceCalls().length,0);
route=async()=>wrap(pendingPlan({input:JSON.parse(original.options.body)}));await recoverPlan();assert.equal(planPosts().length,2);assert.equal(planPosts()[1].options.body,original.options.body);assert.equal(planPosts()[1].options.headers['Idempotency-Key'],original.options.headers['Idempotency-Key']);assert.equal($('explanation-latest').disabled,true);
''')

    def test_unresolved_identity_survives_both_drafts_copy_and_manual_execute(self):
        for source in ['doctor', 'copilot']:
            for state in ['RUNNING', 'UNKNOWN']:
                with self.subTest(source=source, state=state):
                    self.javascript('const source=' + json.dumps(source) + ', state=' + json.dumps(state) + r''';
await boot();const originalSQL='SELECT amount FROM raw.payments WHERE amount > 17';
if(source==='doctor')editDoctor(originalSQL);else editSQL(originalSQL);
const accepted=pendingPlan({state,input:{sql:originalSQL,language:'en'}});route=async()=>wrap(accepted);await startPlan(source);
const intent=dbExplanationIntent('sql_plan_explanation'),saved=JSON.stringify(intent.body),key=intent.key;
queryResult={columns:['retain'],rows:[[8]]};const results=queryResult,run={id:'retained'};dbRuns.set('retained',run);
const mutations=source==='copilot'?[()=>editDoctor('SELECT 2 AS unrelated'),()=>$('explanation-copy').onclick(),()=>editSQL('SELECT 3 AS own'),()=>editDoctor('SELECT 4 AS both')]:
 [()=>editSQL('SELECT 2 AS unrelated'),()=>editDoctor('SELECT 3 AS own'),()=>$('explanation-copy').onclick(),()=>editSQL('SELECT 4 AS both')];
for(const mutate of mutations){
 mutate();assert.equal(dbExplanation.record,null);assert.equal($('explanation-copilot-plan').disabled,true);assert.equal($('explanation-doctor-plan').disabled,true);
 assert.equal($('explanation-copilot-plan-recover').disabled,false);
 await startPlan(source);await dbExplain(source,false,'sql_plan_explanation');
 assert.equal(planPosts().length,1);assert.equal(dbExplanationIntent('sql_plan_explanation'),intent);
 assert.equal(intent.key,key);assert.equal(JSON.stringify(intent.body),saved);assert.equal(intent.source,source);
}
const editor=$('sql').value,doctor=$('bear-query-sql').value;
// Editing the worksheet normally clears its displayed result. Recovery itself must not.
queryResult=results;
route=async(path,options)=>{assert.ok(path.startsWith(planEndpoint+'/'+accepted.id+'?'));assert.equal(options?.method,undefined);return wrap(accepted);};
await recoverPlan();assert.equal(planPosts().length,1);assert.equal(dbExplanation.record.input.sql,originalSQL);
assert.equal($('sql').value,editor);assert.equal($('bear-query-sql').value,doctor);assert.equal(queryResult,results);assert.equal(dbRuns.get('retained'),run);
assert.ok(visibleText($('copilot-result')).includes(originalSQL));
''')

    def test_unknown_ack_replays_frozen_original_across_changed_drafts_and_reload(self):
        self.javascript(r'''
await boot();editDoctor('SELECT product, sum(amount) FROM raw.sales GROUP BY product');
route=async()=>{throw Error('uncertain acknowledgement');};await startPlan('doctor');
const first=structuredClone(planPosts()[0]),original=JSON.parse(first.options.body);
editSQL('SELECT 91 AS changed_editor');editDoctor('SELECT 92 AS changed_doctor');
dbExplanationInstall();const before=calls.length;await tick();await dbExplanationPoll();assert.equal(calls.length,before);
assert.equal($('explanation-doctor-plan').disabled,true);assert.equal($('explanation-copilot-plan-recover').disabled,false);
await dbExplain('copilot',false,'sql_plan_explanation');assert.equal(planPosts().length,1);
route=async(path,options)=>{
 assert.equal(path,planEndpoint);assert.equal(options.body,first.options.body);assert.equal(options.headers['Idempotency-Key'],first.options.headers['Idempotency-Key']);
 return wrap(pendingPlan({input:{sql:original.sql,language:original.language}}));
};
await recoverPlan();assert.equal(planPosts().length,2);assert.equal(dbExplanation.record.input.sql,original.sql);
const intent=dbExplanationIntent('sql_plan_explanation');assert.equal(intent.source,'doctor');
editDoctor('SELECT 93 AS another_doctor');dbExplanationInstall();
assert.equal($('explanation-copilot-plan').disabled,true);const callsBefore=calls.length;await dbExplanationPoll();assert.equal(calls.length,callsBefore);
route=async(path,options)=>{assert.ok(path.startsWith(planEndpoint+'/'+intent.id+'?'));assert.equal(options?.method,undefined);return wrap(pendingPlan({input:{sql:original.sql,language:original.language}}));};
await recoverPlan();assert.equal(planPosts().length,2);
assert.equal($('sql').value,'SELECT 91 AS changed_editor');assert.equal($('bear-query-sql').value,'SELECT 93 AS another_doctor');
''')

    def test_terminal_proof_survives_text_changes_but_never_storage_reload(self):
        self.javascript(r'''
await boot();const completed=planRecord();route=async()=>wrap(completed);await startPlan();
editDoctor('SELECT 7 AS unrelated');editSQL('SELECT 8 AS own');
assert.equal(dbExplanation.record,null);assert.equal($('explanation-copilot-plan').disabled,false);
const oldKey=dbExplanationIntent('sql_plan_explanation').key;
// Persisted terminal/rejection-looking hints carry no authority, even for an accepted id.
const saved=JSON.parse(storage.get(dbExplanationKey));Object.assign(saved[0],{state:'SUCCEEDED',terminal:true,noAdmission:true,recoveryScope:'forged'});
storage.set(dbExplanationKey,JSON.stringify(saved));dbExplanationInstall();
assert.equal($('explanation-copilot-plan').disabled,true);await startPlan();assert.equal(planPosts().length,1);
route=async(path,options)=>{assert.ok(path.startsWith(planEndpoint+'/'+completed.id+'?'));assert.equal(options?.method,undefined);return wrap(completed);};
await recoverPlan();assert.equal($('explanation-copilot-plan').disabled,false);
route=async(path,options)=>wrap(planRecord({id:'877741ff-8097-4856-9f28-6574fb6c5395',input:{sql:JSON.parse(options.body).sql,language:'en'}}));
await startPlan();assert.equal(planPosts().length,2);assert.notEqual(planPosts().at(-1).options.headers['Idempotency-Key'],oldKey);
assert.equal(JSON.parse(planPosts().at(-1).options.body).sql,'SELECT 8 AS own');
''')

    def test_authority_changes_invalidate_terminal_proof_and_recovery_until_read(self):
        self.javascript(r'''
await boot();
for(const [change,restore] of [
 [()=>{dbAuthorizationVersion++;},()=>{}],[()=>{dbEpoch++;},()=>{}],
 [()=>{currentRole='bi_viewer';},()=>{currentRole='admin';}],
 [()=>{dbMaximumRole='data_engineer';},()=>{dbMaximumRole='admin';}],
 [()=>{dbRoleAuthorizationVersions.set(dbTabRole(),dbRoleVersion(dbTabRole())+1);},()=>{}]
]){
 independentWorksheet();const completed=planRecord();route=async()=>wrap(completed);await startPlan();const before=planPosts().length;
 assert.equal($('explanation-copilot-plan').disabled,false);
 change();dbControls();restore();dbControls();
 assert.equal($('explanation-copilot-plan').disabled,true);assert.equal($('explanation-copilot-plan-recover').disabled,true);
 await startPlan();await recoverPlan();assert.equal(planPosts().length,before);
 route=async(path,options)=>{assert.equal(options?.method,undefined);return {context:completed.context,explanations:[completed]};};
 await dbRefreshContext();await dbExplanationHistory('sql_plan_explanation');assert.equal($('explanation-copilot-plan-recover').disabled,false,dbReason());
 assert.equal($('explanation-copilot-plan').disabled,false);
}
''')

    def test_all_saved_unresolved_intents_remain_held_despite_newer_terminal(self):
        self.javascript(r'''
await boot();const capture=structuredClone(dbExplanationCapture()),context=structuredClone(capture.context);
const olderID='877741ff-8097-4856-9f28-6574fb6c5395',newerID=planRecord().id;
const saved=(key,id,sql)=>({key,id,kind:'sql_plan_explanation',endpoint:planEndpoint,source:'copilot',capture,
 body:{context,sql,language:'en'},terminal:true});
storage.set(dbExplanationKey,JSON.stringify([saved('older-key',olderID,'SELECT 1 AS older'),saved('newer-key',newerID,'SELECT 2 AS newer')]));
editSQL('SELECT 3 AS current');dbExplanationInstall();assert.equal(dbExplanationIntent('sql_plan_explanation').key,'newer-key');
const newer=planRecord({input:{sql:'SELECT 2 AS newer',language:'en'}});
route=async()=>({context,explanations:[newer]});await dbExplanationHistory('sql_plan_explanation');
assert.equal(dbExplanationIntent('sql_plan_explanation').key,'older-key');assert.equal($('explanation-copilot-plan').disabled,true);
const older=pendingPlan({id:olderID,state:'UNKNOWN',input:{sql:'SELECT 1 AS older',language:'en'}});
route=async(path,options)=>{assert.ok(path.includes('/'+olderID+'?'));assert.equal(options?.method,undefined);return wrap(older);};
await recoverPlan();assert.equal(dbExplanation.record.id,olderID);assert.equal($('explanation-copilot-plan').disabled,true);
route=async()=>({context,explanations:[newer]});await dbExplanationHistory('sql_plan_explanation');
route=async()=>wrap(newer);await dbExplanationSelect(0);
assert.equal(dbExplanationIntent('sql_plan_explanation').key,'older-key');await startPlan();assert.equal(planPosts().length,0);
''')

    def test_fresh_structured_400_only_is_no_admission_and_reload_reconfirms_original(self):
        self.javascript(r'''
await boot();
for(const code of ['AI_INPUT_INVALID','AI_PLAN_VIEW_UNSUPPORTED'])for(const envelope of ['top','nested']){
 independentWorksheet();editSQL(code==='AI_INPUT_INVALID'?'DELETE FROM raw.payments':'SELECT * FROM raw.payment_view');
 const failure=()=>({http:400,body:envelope==='top'?{code}:{error:{code}}});
 const rejectedSQL=$('sql').value;route=async()=>failure();await startPlan();
 const original=structuredClone(planPosts().at(-1)),before=planPosts().length;
 assert.equal(dbExplanation.record,null);assert.equal($('explanation-copilot-plan').disabled,false);
 editSQL('SELECT amount FROM raw.payments');editDoctor('SELECT 31 AS unrelated');assert.equal($('explanation-copilot-plan').disabled,false);
 const hints=JSON.parse(storage.get(dbExplanationKey));Object.assign(hints.at(-1),{noAdmission:true,terminal:true,state:'FAILED'});storage.set(dbExplanationKey,JSON.stringify(hints));
 dbExplanationInstall();assert.equal($('explanation-copilot-plan').disabled,true);await startPlan();assert.equal(planPosts().length,before);
 route=async(path,options)=>{assert.equal(options.body,original.options.body);assert.equal(JSON.parse(options.body).sql,rejectedSQL);assert.equal(options.headers['Idempotency-Key'],original.options.headers['Idempotency-Key']);return failure();};
 await recoverPlan();assert.equal(planPosts().length,before+1);assert.equal($('explanation-copilot-plan').disabled,false);
 route=async(path,options)=>wrap(planRecord({input:{sql:JSON.parse(options.body).sql,language:'en'}}));await startPlan();
 assert.equal(planPosts().length,before+2);assert.notEqual(planPosts().at(-1).options.headers['Idempotency-Key'],original.options.headers['Idempotency-Key']);
 assert.equal(JSON.parse(planPosts().at(-1).options.body).sql,'SELECT amount FROM raw.payments');
}
''')

    def test_other_failures_and_accepted_id_rejections_never_release_hold(self):
        self.javascript(r'''
await boot();
const failures=[
 ...[401,403,500,503].map(http=>()=>({http,body:{code:'AI_INPUT_INVALID'}})),
 ()=>({http:500,body:{error:{code:'AI_INPUT_INVALID'}}}),()=>({http:503,body:{error:{code:'AI_PLAN_VIEW_UNSUPPORTED'}}}),
 ()=>({http:401,body:{error:{code:'AI_INPUT_INVALID'}}}),()=>({http:403,body:{error:{code:'AI_PLAN_VIEW_UNSUPPORTED'}}}),
 ()=>({http:400,body:{error:{code:'AI_OTHER_FAILURE'}}}),()=>({http:400,body:{error:'AI_INPUT_INVALID'}}),
 ()=>({http:400,body:{message:'AI_INPUT_INVALID'}}),()=>({http:400,body:{error:{message:'AI_PLAN_VIEW_UNSUPPORTED'}}}),
 ()=>({http:400,body:{code:{value:'AI_INPUT_INVALID'}}}),
 ()=>({http:202,body:{code:'AI_INPUT_INVALID'}}),
 ()=>{throw Error('AI_INPUT_INVALID transport failure');}
];
for(const fail of failures){
 independentWorksheet();route=async()=>fail();await startPlan();const before=planPosts().length,intent=dbExplanationIntent('sql_plan_explanation');
 editSQL('SELECT 29 AS valid_sql');editDoctor('SELECT 30 AS other_sql');
 assert.equal($('explanation-copilot-plan').disabled,true);await startPlan();await dbExplain('doctor',false,'sql_plan_explanation');
 assert.equal(planPosts().length,before);assert.equal(dbExplanationIntent('sql_plan_explanation'),intent);
}
independentWorksheet();const accepted=pendingPlan();route=async()=>wrap(accepted);await startPlan();const before=planPosts().length;
route=async()=>({http:400,body:{error:{code:'AI_PLAN_VIEW_UNSUPPORTED'}}});await recoverPlan();
editSQL('SELECT 41 AS edited');assert.equal($('explanation-copilot-plan').disabled,true);await startPlan();assert.equal(planPosts().length,before);
independentWorksheet();route=async()=>({http:202,body:{...wrap(pendingPlan({error_code:'AI_INPUT_INVALID'})),code:'AI_INPUT_INVALID'}});await startPlan();const acceptedCount=planPosts().length;
editSQL('SELECT 42 AS changed');assert.equal($('explanation-copilot-plan').disabled,true);await startPlan();assert.equal(planPosts().length,acceptedCount);
''')

    def test_identity_isolates_actor_worksheet_full9_and_late_recovery_uses_current_capture(self):
        self.javascript(r'''
await boot([input(),input('b')]);route=async()=>{throw Error('uncertain');};await startPlan();const original=structuredClone(planPosts()[0]);
const principal=dbPrincipal,context=dbContext;
for(const [change,restore] of [
 [()=>{worksheetId='b';},()=>{worksheetId='a';}],
 [()=>{dbPrincipal=JSON.stringify(['another',true]);},()=>{dbPrincipal=principal;}],
 ...dbExplanationContextKeys.map(key=>[()=>{dbContext=w=>({...context(w),[key]:String(context(w)[key])+'-other'});},()=>{dbContext=context;}])
]){change();dbControls();assert.equal(dbExplanationIntent('sql_plan_explanation'),undefined);restore();dbControls();}
// Revalidate authority explicitly after these scope transitions, without starting work.
route=async()=>({context:context(dbCurrent()),explanations:[]});await dbExplanationHistory('sql_plan_explanation');
editSQL('SELECT 51 AS current_editor');editDoctor('SELECT 52 AS current_doctor');
const held=deferred();route=(path,options)=>{assert.equal(path,planEndpoint);assert.equal(options.body,original.options.body);assert.equal(options.headers['Idempotency-Key'],original.options.headers['Idempotency-Key']);return held.promise;};
const recovering=recoverPlan();await tick();editDoctor('SELECT 53 AS newer_doctor');
held.resolve(wrap(planRecord({input:{sql:JSON.parse(original.options.body).sql,language:'en'}})));await recovering;
assert.equal(dbExplanation.record,null);assert.equal($('copilot-result').hidden,true);
assert.equal($('sql').value,'SELECT 51 AS current_editor');assert.equal($('bear-query-sql').value,'SELECT 53 AS newer_doctor');
assert.equal($('explanation-copilot-plan').disabled,true);
''')

    def test_exact_post_doctor_and_copilot_context_preserve_worksheet_state(self):
        for source in ['doctor', 'copilot']:
            with self.subTest(source=source):
                self.javascript('const source=' + json.dumps(source) + r''';
await dualBoot();await exploreFinance();
const sql='  WITH c AS (SELECT amount FROM raw.invoices) SELECT sum(amount) FROM c -- original\n';
if(source==='doctor')editDoctor(sql);else editSQL(sql);
const editor=$('sql').value,doctor=$('bear-query-sql').value,draft=JSON.stringify(dbInput(dbCurrent()));
queryResult={rows:[[37]],columns:['retained']};const result=queryResult,run={retained:true};dbRuns.set('retained',run);
const runHandler=$('run-query').onclick,cancelHandler=$('cancel-query').onclick;
route=async(path,options)=>{
 assert.equal(path,planEndpoint);assert.equal(options.method,'POST');
 const body=JSON.parse(options.body);assert.deepEqual(Object.keys(body).sort(),['context','language','sql']);
 assert.equal(body.sql,sql);assert.equal(body.language,'en');assert.deepEqual(body.context,dbContext(dbCurrent()));
 assert.equal(Object.keys(body.context).length,9);assert.equal(body.context.sql_catalog,'sales');
 assert.equal(options.headers['X-Shirokuma-Role'],'admin');
 const saved=JSON.parse(storage.get(dbExplanationKey)).at(-1);
 assert.equal(saved.key,options.headers['Idempotency-Key']);assert.equal(saved.kind,'sql_plan_explanation');
 assert.equal(saved.endpoint,planEndpoint);assert.equal(JSON.stringify(saved.body),options.body);
 return {http:202,body:wrap(planRecord({input:{sql,language:'en'}}))};
};
await startPlan(source);const intent=dbExplanationIntent('sql_plan_explanation');
assert.ok(intent);assert.equal(intent.source,source);assert.equal(Object.isFrozen(intent.body),true);
assert.equal(Object.getOwnPropertyDescriptor(intent,'kind').writable,false);
assert.equal(Object.getOwnPropertyDescriptor(intent,'endpoint').writable,false);
assert.equal(dbExplanation.record.kind,'sql_plan_explanation');assert.equal(planPosts().length,1);
assert.equal($('sql').value,editor);assert.equal($('bear-query-sql').value,doctor);assert.equal(JSON.stringify(dbInput(dbCurrent())),draft);
assert.equal(queryResult,result);assert.equal(dbRuns.get('retained'),run);
assert.equal($('run-query').onclick,runHandler);assert.equal($('cancel-query').onclick,cancelHandler);
assert.equal(calls.some(c=>c.path==='/api/v1/queries'&&c.options?.method==='POST'),false);
''')

    def test_legacy_a1_intent_and_malicious_endpoint_never_retarget(self):
        self.javascript(r'''
await boot();const capture=structuredClone(dbExplanationCapture()),body={context:capture.context,sql:capture.editor,language:'en'};
const legacy={key:'original-a1-key',capture,body,source:'copilot',id:null};
for(const bad of [
 {...legacy,key:'external',endpoint:'https://attacker.invalid/collect'},
 {...legacy,key:'a2-without-kind',endpoint:planEndpoint},
 {...legacy,key:'a1-wrong-kind',kind:'sql_plan_explanation',endpoint:'/api/v1/bears/explanations'},
 {...legacy,key:'missing-plan-endpoint',kind:'sql_plan_explanation'},
 {...legacy,key:'null-kind',kind:null},
 {...legacy,key:'prototype',kind:'toString'}
]){
 storage.set(dbExplanationKey,JSON.stringify([bad]));dbExplanationInstall();
 assert.equal(dbExplanation.intents.length,0);assert.equal($('explanation-copilot-recover').disabled,true);
 await recoverPlan();await $('explanation-copilot-recover').onclick();assert.equal(explanationCalls().length+planCalls().length,0);
}
storage.set(dbExplanationKey,JSON.stringify([legacy]));dbExplanationInstall();
assert.equal(dbExplanation.intents[0].kind,'sql_explanation');assert.equal(dbExplanation.intents[0].endpoint,'/api/v1/bears/explanations');
route=async(path,options)=>{assert.equal(path,'/api/v1/bears/explanations');assert.equal(options.headers['Idempotency-Key'],'original-a1-key');assert.equal(options.body,JSON.stringify(body));return wrap(record());};
await $('explanation-copilot-recover').onclick();assert.equal(posts().length,1);assert.equal(planCalls().length,0);
''')

    def test_uncertain_ack_durable_replay_reload_poll_and_cancel_use_original_record(self):
        self.javascript(r'''
await boot();editSQL(' SELECT category, count(*) FROM raw.events GROUP BY category ');
route=async()=>{throw Error('lost acknowledgement PRIVATE_TRANSPORT');};await startPlan();
const original=structuredClone(planPosts()[0]),key=original.options.headers['Idempotency-Key'];
assert.equal($('explanation-copilot-plan').disabled,true);await startPlan();assert.equal(planPosts().length,1);
dbExplanationInstall();await periodicRefresh();assert.equal(planPosts().length,1);
const accepted=pendingPlan();route=async()=>wrap(accepted);await recoverPlan();
assert.equal(planPosts().length,2);assert.equal(planPosts()[1].options.body,original.options.body);
assert.equal(planPosts()[1].options.headers['Idempotency-Key'],key);
const intent=dbExplanationIntent('sql_plan_explanation');assert.equal(intent.id,accepted.id);
dbExplanationInstall();await periodicRefresh();assert.equal(planCalls().length,2);
route=async(path,options)=>{assert.ok(path.includes('/'+accepted.id+'?'));assert.equal(options?.method,undefined);return wrap(accepted);};
await recoverPlan();await periodicRefresh();assert.equal(planPosts().length,2);
route=async(path,options)=>{
 assert.equal(path,planEndpoint+'/'+accepted.id+'/cancel');assert.equal(options.method,'POST');
 assert.deepEqual(JSON.parse(options.body),{context:accepted.context});assert.equal(options.headers['Idempotency-Key'],undefined);
 return {http:202,body:wrap({...accepted,cancel_requested:true})};
};
await cancelPlan();await cancelPlan();assert.equal(cancelCalls().length,1);
assert.equal(dbExplanationIntent('sql_plan_explanation').key,key);assert.equal(planPosts().length,2);
assert.match(visibleText($('copilot-result')),/Cancellation requested/);
assert.doesNotMatch(visibleText($('copilot-result')),/PRIVATE_TRANSPORT|physically stopped/);
''')

    def test_storage_verification_blocks_plan_post_and_replay_until_durable(self):
        self.javascript(r'''
await boot();const set=localStorage.setItem,get=localStorage.getItem;
for(const failure of ['write','read','mismatch']){
 editSQL('SELECT total FROM raw.receipts WHERE total > '+Math.random());
 localStorage.setItem=(key,value)=>{if(key===dbExplanationKey){if(failure==='write')throw Error('denied');if(failure==='mismatch')return;}set(key,value);};
 localStorage.getItem=key=>{if(key===dbExplanationKey&&failure==='read')throw Error('denied');return get(key);};
 const before=planPosts().length;await startPlan();const intent=dbExplanationIntent('sql_plan_explanation');
 assert.ok(intent);assert.equal(planPosts().length,before);assert.match(visibleText($('copilot-result')),/No request was sent/);
 await recoverPlan();assert.equal(planPosts().length,before);
 localStorage.setItem=set;localStorage.getItem=get;
 route=async(path,options)=>{assert.equal(JSON.stringify(JSON.parse(get(dbExplanationKey)).at(-1).body),options.body);assert.equal(options.headers['Idempotency-Key'],intent.key);return wrap(planRecord());};
 await recoverPlan();assert.equal(planPosts().length,before+1);
}
''')

    def test_phase_progress_and_model_cancel_preserve_plan_without_stoppage_claim(self):
        self.javascript(r'''
await boot();const accepted=pendingPlan();route=async()=>wrap(accepted);await startPlan();
assert.equal($('explanation-copilot-plan-cancel').textContent,'Cancel plan acquisition');
assert.match(visibleText($('copilot-result')),/Plan query: Running · Not collected · Local explanation: Not started/);
const model=planRecord({state:'RUNNING',inference_state:'RUNNING',text:null});route=async()=>wrap(model);await periodicRefresh();
assert.equal($('explanation-copilot-plan-cancel').textContent,'Discard explanation result');
const evidence=JSON.stringify(dbExplanation.record.plan);
route=async()=>wrap({...model,state:'CANCELED',inference_state:'CANCELED',cancel_requested:true});await cancelPlan();
assert.equal(JSON.stringify(dbExplanation.record.plan),evidence);assert.equal(planPosts().length,1);
assert.match(visibleText($('copilot-result')),/Physical model stoppage is not confirmed/);
for(const id of ['copilot-result','explanation-doctor-output']){
 const status='Model result will be discarded. Physical model stoppage is not confirmed.';
 assert.equal($(id).descendants().filter(node=>node.tagName==='P'&&node.textContent===status).length,1);
}
assert.equal($('explanation-copilot-plan-cancel').disabled,true);
setLanguage('ja');assert.match(visibleText($('copilot-result')),/モデル処理自体の停止は確認していません/);
''')

    def test_queued_plan_immediately_polls_recovers_and_enables_cancel_without_history(self):
        self.javascript(r'''
await boot();const queued=pendingPlan({state:'QUEUED',plan:{...pendingPlan().plan,query_state:'QUEUED'}});
route=async()=>wrap(queued);await startPlan();const intent=dbExplanationIntent('sql_plan_explanation'),key=intent.key;
assert.equal($('explanation-copilot-plan-cancel').disabled,false);
assert.equal($('explanation-copilot-plan-recover').disabled,false);
route=async(path,options)=>{assert.ok(path.startsWith(planEndpoint+'/'+queued.id+'?'));assert.equal(options?.method,undefined);return wrap(pendingPlan());};
await periodicRefresh();assert.equal(planCalls().length,2);assert.equal(dbExplanation.record.state,'RUNNING');
route=async()=>wrap(planRecord());await recoverPlan();assert.equal(planPosts().length,1);
assert.equal(dbExplanationIntent('sql_plan_explanation').key,key);assert.equal($('explanation-copilot-plan').disabled,false);
assert.equal($('explanation-copilot-plan').textContent,'Acquire a new plan and explain');
''')

    def test_completion_wins_cancel_race_preserves_answer_without_false_discard(self):
        self.javascript(r'''
await boot();route=async()=>wrap(planRecord({state:'RUNNING',inference_state:'RUNNING',text:null}));await startPlan();
assert.equal($('explanation-copilot-plan-cancel').textContent,'Discard explanation result');
const final=planRecord({text:'Completed before cancellation',cancel_requested:false});
route=async()=>wrap(final);await cancelPlan();
assert.equal(dbExplanation.record.state,'SUCCEEDED');assert.equal(dbExplanation.record.text,final.text);
assert.equal(dbExplanation.record.cancel_requested,false);assert.equal(dbExplanation.notice,'');
for(const locale of ['en','ja']){
 setLanguage(locale);const node=$('copilot-result');assert.equal(node.children[0].textContent,final.text);
 assert.doesNotMatch(visibleText(node),/will be discarded|Cancellation requested|stoppage|破棄します|キャンセルを要求|停止/);
}
assert.equal(cancelCalls().length,1);assert.equal(planPosts().length,1);assert.equal($('explanation-copilot-plan').disabled,false);
''')

    def test_authoritative_terminal_with_null_closure_allows_explicit_fresh_key(self):
        self.javascript(r'''
await boot();
const states=[
 planRecord(),planRecord({state:'FAILED',inference_state:'FAILED',text:null}),
 planRecord({state:'FAILED',inference_state:'NOT_STARTED',plan_status:'FAILED',text:null,plan:{...planRecord().plan,query_state:'FAILED',operators:null,captured_at:null}}),
 pendingPlan({state:'FAILED',plan_status:'FAILED',plan:{...pendingPlan().plan,query_state:'NOT_SUBMITTED',query_id:null}}),
 pendingPlan({state:'CANCELED',inference_state:'CANCELED',plan_status:'CANCELED',cancel_requested:true,plan:{...pendingPlan().plan,query_state:'NOT_SUBMITTED',query_id:null}})
];
for(const accepted of states){
 editSQL('SELECT amount FROM raw.expenses WHERE amount > '+Math.random());accepted.input.sql=$('sql').value;
 route=async()=>wrap(accepted);await startPlan();
 assert.ok(dbExplanation.record);assert.equal($('explanation-copilot-plan').disabled,false);
 assert.equal($('explanation-copilot-plan').textContent,'Acquire a new plan and explain');
 const before=planPosts().length,key=planPosts().at(-1).options.headers['Idempotency-Key'];
 await periodicRefresh();assert.equal(planPosts().length,before);
 await startPlan();assert.equal(planPosts().length,before+1);assert.notEqual(planPosts().at(-1).options.headers['Idempotency-Key'],key);
}
''')

    def test_unknown_active_inference_and_older_terminal_history_never_unlock_latest(self):
        self.javascript(r'''
await boot();
const blocked=[pendingPlan(),pendingPlan({state:'UNKNOWN'}),planRecord({state:'FAILED',inference_state:'UNKNOWN',text:null}),
 pendingPlan({state:'FAILED',inference_state:'RUNNING',plan:{...pendingPlan().plan,query_id:null,query_state:'NOT_SUBMITTED'}})];
for(const accepted of blocked){
 independentWorksheet();
 editSQL('SELECT DISTINCT name FROM raw.accounts WHERE n > '+Math.random());accepted.input.sql=$('sql').value;
 route=async()=>wrap(accepted);await startPlan();const key=dbExplanationIntent('sql_plan_explanation').key,before=planPosts().length;
 assert.equal($('explanation-copilot-plan').disabled,true);await startPlan();assert.equal(planPosts().length,before);
 const past=planRecord({id:'877741ff-8097-4856-9f28-6574fb6c5395'});
 route=async path=>path.includes('/'+past.id+'?')?wrap(past):{context:past.context,explanations:[past]};
 await dbExplanationHistory('sql_plan_explanation');await dbExplanationSelect(0);
 assert.equal($('explanation-copilot-plan').disabled,true);await startPlan();assert.equal(planPosts().length,before);
 assert.equal(dbExplanationIntent('sql_plan_explanation').key,key);
}
''')

    def test_history_kind_routes_are_separate_and_gets_never_create_work(self):
        self.javascript(r'''
await boot();assert.equal(planCalls().length,0);assert.equal(explanationCalls().length,0);const queries=calls.filter(c=>c.path.startsWith('/api/v1/queries')).length;
const old=record(),plan=planRecord();
for(const kind of ['sql_explanation','sql_plan_explanation']){
 const endpoint=dbExplanationEndpoints[kind],selected=kind==='sql_explanation'?old:plan;
 route=async(path,options)=>{assert.equal(options?.method,undefined);assert.ok(path.startsWith(endpoint));return path.includes('/'+selected.id+'?')?wrap(selected):{context:selected.context,explanations:[old,plan]};};
 await dbExplanationHistory(kind);assert.equal(dbExplanation.history.length,1);assert.equal(dbExplanation.history[0].kind,kind);
 await dbExplanationSelect(0);assert.equal(dbExplanation.record.kind,kind);assert.equal(dbExplanation.displayOrigin,'history');
}
assert.equal(planPosts().length,0);assert.equal(posts().length,0);assert.equal(calls.filter(c=>c.path.startsWith('/api/v1/queries')).length,queries);
''')

    def test_older_pending_history_never_polls_newer_intent_until_explicit_recovery(self):
        self.javascript(r'''
await boot();
for(const kind of ['sql_explanation','sql_plan_explanation']){
 const endpoint=dbExplanationEndpoints[kind];
 const newer=kind==='sql_plan_explanation'?pendingPlan():record({state:'RUNNING',text:null});
 const older={...newer,id:kind==='sql_plan_explanation'?'877741ff-8097-4856-9f28-6574fb6c5395':'older-sql-explanation'};
 route=async()=>wrap(newer);
 if(kind==='sql_plan_explanation')await startPlan();else await $('copilot-explain').onclick();
 const intent=dbExplanationIntent(kind),key=intent.key;
 assert.equal(intent.id,newer.id);
 route=async(path,options)=>{
  assert.equal(options?.method,undefined);
  return path.includes('/'+older.id+'?')?wrap(older):{context:older.context,explanations:[older]};
 };
 await dbExplanationHistory(kind);await dbExplanationSelect(0);
 assert.equal(dbExplanation.record.id,older.id);assert.equal(dbExplanation.displayOrigin,'history');
 const before=calls.length;await dbExplanationPoll();
 assert.equal(calls.length,before);assert.equal(dbExplanation.record.id,older.id);
 assert.equal(dbExplanationIntent(kind),intent);assert.equal(intent.key,key);
 route=async(path,options)=>{assert.ok(path.startsWith(endpoint+'/'+newer.id+'?'));assert.equal(options?.method,undefined);return wrap(newer);};
 await dbExplain('copilot',true,kind);
 assert.equal(calls.length,before+1);assert.equal(dbExplanation.record.id,newer.id);assert.equal(dbExplanation.displayOrigin,'current');
 assert.equal(dbExplanationIntent(kind),intent);assert.equal(intent.key,key);
}
''')

    def test_reload_kind_scoped_history_and_late_other_kind_response(self):
        self.javascript(r'''
await boot();route=async()=>{throw Error('uncertain');};await startPlan();
const frozen=JSON.stringify(dbExplanation.intents);dbExplanationInstall();const before=planCalls().length+explanationCalls().length;
await tick();assert.equal(planCalls().length+explanationCalls().length,before);
const plan=planRecord(),a1=record();
route=async(path,options)=>{assert.equal(options?.method,undefined);assert.ok(path.startsWith(planEndpoint));return path.includes('/'+plan.id+'?')?wrap(plan):{context:plan.context,explanations:[plan]};};
await $('explanation-copilot-plan-history').onclick();await dbExplanationSelect(0);assert.equal(dbExplanation.record.kind,'sql_plan_explanation');
route=async(path,options)=>{assert.equal(options?.method,undefined);assert.ok(path.startsWith('/api/v1/bears/explanations'));return {context:a1.context,explanations:[a1]};};
await $('explanation-copilot-history').onclick();assert.equal(dbExplanation.history[0].kind,'sql_explanation');
assert.equal(JSON.stringify(dbExplanation.intents),frozen);assert.equal(planPosts().length,1);assert.equal(posts().length,0);
// Switch history kind while an old A2 read is outstanding in the same captured scope.
const old=deferred();route=()=>old.promise;const loading=$('explanation-copilot-plan-history').onclick();await tick();
assert.equal($('explanation-copilot-history').disabled,false);
route=async()=>({context:a1.context,explanations:[a1]});await $('explanation-copilot-history').onclick();
old.resolve({context:plan.context,explanations:[{...plan,text:'STALE_OTHER_KIND'}]});await loading;
assert.equal(dbExplanation.historyKind,'sql_explanation');assert.equal(dbExplanation.history[0].kind,'sql_explanation');
assert.equal(dbExplanation.record,null);assert.doesNotMatch(visibleText($('copilot-result')),/STALE_OTHER_KIND/);
''')

    def test_response_identity_flags_and_closed_public_projection_on_all_paths(self):
        self.javascript(r'''
await boot();
const invalid=[r=>({...r,kind:'sql_explanation'}),r=>({...r,id:'wrong-id'}),r=>({...r,actor:'another-owner'}),
 r=>({...r,context:{...r.context,generation_id:'other'}}),r=>({...r,context:{...r.context,extra:'private'}}),
 r=>({...r,sql_status:'EXECUTED'}),r=>({...r,cancel_requested:'false'}),r=>({...r,inference_state:'PRIVATE_STATE'}),
 r=>({...r,plan_status:'EXPLAINED'}),r=>({...r,plan:{...r.plan,format:'RAW_JSON'}}),
 r=>({...r,plan:{...r.plan,query_state:'PRIVATE_STATE'}}),r=>({...r,plan:{...r.plan,execution_closure:'TERMINAL'}}),
 r=>({...r,plan:{...r.plan,query_id:'raw-engine-id'}}),r=>({...r,metadata:{...r.metadata,relations:[{schema:'raw',table:'v',kind:'VIEW',columns:[]}]}}),
 r=>({...r,input:{...r.input,sql:''}}),r=>({...r,input:{...r.input,language:'fr'}})];
for(const operation of ['post','get','list','cancel'])for(const change of invalid){
 independentWorksheet();
 editSQL('SELECT n FROM raw.arbitrary WHERE n > '+Math.random());const r=planRecord();
 const before=planPosts().length;
 if(operation==='cancel'){route=async()=>wrap(pendingPlan());await startPlan();}
 const bad=change({...r,text:'PRIVATE_MODEL_RESPONSE'});route=async()=>operation==='list'?{context:r.context,explanations:[bad]}:wrap(bad);
 if(operation==='post')await startPlan();
 if(operation==='get'){dbExplanation.history=[r];dbExplanation.historyKind=r.kind;await dbExplanationSelect(0);}
 if(operation==='list')await dbExplanationHistory(r.kind);
 if(operation==='cancel')await cancelPlan();
 if(operation==='post'||operation==='cancel')assert.equal(planPosts().length,before+1);
 assert.doesNotMatch(visibleText($('copilot-result')),/PRIVATE_MODEL_RESPONSE|another-owner|PRIVATE_STATE/);
 if(operation==='post'||operation==='get')assert.equal(dbExplanation.record,null);
 if(operation==='list')assert.equal(dbExplanation.history.some(item=>item.text==='PRIVATE_MODEL_RESPONSE'),false);
}
// Unknown public fields are dropped, including fields nested in approved metadata.
independentWorksheet();
editSQL('SELECT a FROM raw.example');const secret={url:'PRIVATE_URL',config:{key:'PRIVATE_KEY'},raw_plan:'PRIVATE_RAW'};
const r=planRecord({...secret,input:{sql:$('sql').value,language:'en',...secret},model:{provider:'local-openai',name:'local-model',...secret},
 metadata:{captured_at:'now',relations:[{schema:'raw',table:'example',kind:'TABLE',columns:[{name:'a',type:'bigint',...secret}],...secret}],...secret}});
r.plan={...r.plan,...secret};route=async()=>wrap(r);await startPlan();
assert.ok(dbExplanation.record);assert.doesNotMatch(JSON.stringify(dbExplanation.record),/PRIVATE_|raw_plan|config/);
''')

    def test_complete_operator_allowlist_closed_enum_pairs_and_safe_hierarchy(self):
        self.javascript(r'''
await boot();
const names=['OtherOperator','Output','Project','Filter','TableScan','ScanFilterProject','Values','Aggregate','LocalExchange','RemoteSource','Join','InnerJoin','LeftJoin','RightJoin','FullJoin','CrossJoin','SemiJoin','SpatialJoin','IndexJoin','Sort','TopN','TopNRanking','Window','RowNumber','Limit','DistinctLimit','MarkDistinct','Union','Intersect','Except','EnforceSingleRow','AssignUniqueId','GroupId','Offset','Sample','Exchange','DynamicFilterSource','PatternRecognition'];
assert.equal(names.length,38);
const root=op('Join',[op('Sort',[op('TopN',names.map(name=>op(name)))])],{join_type:'LEFT',distribution:'REPLICATED'});
const safe={projection_version:1,fragments:[{id:'f0',root}]};
route=async()=>wrap(planRecord({plan:{...planRecord().plan,operators:safe}}));await startPlan();
assert.ok(dbExplanation.record);const text=visibleText($('copilot-result'));
for(const name of names)assert.ok(text.includes(name),name);
for(const [key,values] of Object.entries({phase:['PARTIAL','INTERMEDIATE','FINAL','SINGLE'],partitioning:['HASH','SINGLE','ROUND_ROBIN','ARBITRARY','BROADCAST','REPLICATED'],join_type:['INNER','LEFT','RIGHT','FULL','CROSS','SEMI'],distribution:['PARTITIONED','REPLICATED','BROADCAST','GATHER','REPARTITION','REPLICATE']})){
 for(const value of values){const operator=key==='phase'?'Aggregate':'Output';assert.equal(dbPlanProjection({projection_version:1,fragments:[{id:'f0',root:op(operator,[],{[key]:value})}]}).fragments[0].root.attributes[key],value);}
}
assert.throws(()=>dbPlanProjection({projection_version:1,fragments:[{id:'f0',root:op('Join',[],{phase:'FINAL'})}]}));
''')

    def test_projection_unknown_fields_names_values_links_and_bounds_are_rejected(self):
        self.javascript(r'''
await boot();
const mutations=[p=>{p.raw='SECRET_RAW';},p=>{p.projection_version=2;},p=>{p.fragments[0].raw='SECRET_RAW';},
 p=>{p.fragments[0].id='opaque';},p=>{p.fragments[0].root.operator='SECRET_OPERATOR';},
 p=>{p.fragments[0].root.details='SECRET_EXPRESSION';},p=>{p.fragments[0].root.attributes={password:'SECRET_PASSWORD'};},
 p=>{p.fragments[0].root.attributes={partitioning:'SECRET_PATH'};},p=>{p.fragments[0].root.attributes={phase:'FINAL'};},
 p=>{p.fragments[0].root.remote_sources=['opaque'];},p=>{p.fragments[0].root.remote_sources=['f0'];},
 p=>{p.fragments[0].root.remote_sources=['f1','f1'];},p=>{p.fragments[0].root.children=Array.from({length:513},()=>op('Values'));},
 p=>{let root=op('Values');for(let i=0;i<33;i++)root=op('Project',[root]);p.fragments[0].root=root;},
 p=>{p.fragments=Array.from({length:65},(_,i)=>({id:'f'+i,root:op('Values')}));}];
for(const mutate of mutations){
 independentWorksheet();const before=planPosts().length;
 editSQL('SELECT n FROM raw.inputs WHERE n > '+Math.random());const operators=projection();mutate(operators);
 route=async()=>wrap(planRecord({text:'PRIVATE_ANSWER',plan:{...planRecord().plan,operators}}));await startPlan();
 assert.equal(planPosts().length,before+1);
 assert.equal(dbExplanation.record,null);assert.doesNotMatch(visibleText($('copilot-result')),/SECRET_|PRIVATE_ANSWER/);
}
''')

    def test_native_operator_disclosure_survives_same_record_refresh_and_resets_on_identity(self):
        self.javascript(r'''
await boot();const captured=planRecord();route=async()=>wrap(captured);await startPlan();
const tree=id=>$(id).descendants().find(n=>n.tagName==='DETAILS'&&n.children[0].textContent===t('Safe operator tree (read only)'));
const copilot=tree('copilot-result'),doctor=tree('explanation-doctor-output');
assert.ok(copilot);assert.ok(doctor);assert.notEqual(copilot.open,true);assert.notEqual(doctor.open,true);
copilot.open=true;
for(const render of [()=>dbControls(),()=>dbExplanationSync(),()=>periodicRefresh()]){
 await render();assert.equal(tree('copilot-result'),copilot);assert.equal(copilot.open,true);
 assert.equal(tree('explanation-doctor-output'),doctor);assert.notEqual(doctor.open,true);
}
// A guarded same-record GET and an in-flight model update retain the native node.
route=async()=>wrap({...captured,state:'RUNNING',inference_state:'RUNNING',text:null});await recoverPlan();
assert.equal(tree('copilot-result'),copilot);assert.equal(copilot.open,true);
route=async()=>wrap(captured);await dbExplanationPoll();
assert.equal(tree('copilot-result'),copilot);assert.equal(copilot.open,true);
// History reads temporarily clear the displayed record, but a same-record selection keeps disclosure.
route=async path=>path.includes('/'+captured.id+'?')?wrap(captured):{context:captured.context,explanations:[captured]};
await dbExplanationHistory('sql_plan_explanation');await dbExplanationSelect(0);
assert.equal(tree('copilot-result'),copilot);assert.equal(copilot.open,true);
// A genuinely different record starts collapsed, and returning cannot resurrect the old disclosure.
const another={...captured,id:'877741ff-8097-4856-9f28-6574fb6c5395'};
route=async()=>({context:captured.context,explanations:[another,captured]});await dbExplanationHistory('sql_plan_explanation');
route=async()=>wrap(another);await dbExplanationSelect(0);assert.notEqual(tree('copilot-result'),copilot);assert.notEqual(tree('copilot-result').open,true);
route=async()=>wrap(captured);await dbExplanationSelect(1);assert.notEqual(tree('copilot-result'),copilot);assert.notEqual(tree('copilot-result').open,true);
// Switching through A1 invalidates A2 disclosure even when the original A2 id returns.
tree('copilot-result').open=true;route=async()=>wrap(record());await $('copilot-explain').onclick();assert.equal(tree('copilot-result'),undefined);
route=async()=>wrap(captured);await recoverPlan();assert.notEqual(tree('copilot-result').open,true);
// Every captured scope invalidation clears disclosure, including a later return to the old scope.
const context=dbContext;
for(const [change,restore] of [
 [()=>editSQL('SELECT 2 AS other_draft'),()=>editSQL(captured.input.sql)],
 [()=>{dbPrincipal=JSON.stringify(['other',true]);},()=>{dbPrincipal=JSON.stringify(['owner',true]);}],
 [()=>{dbContext=w=>({...context(w),grant_revision:999});},()=>{dbContext=context;}],
 [()=>{dbAuthorizationVersion++;},()=>{}]
]){
 const previous=tree('copilot-result');previous.open=true;change();dbControls();assert.equal(tree('copilot-result'),undefined);
 restore();dbControls();route=async path=>path.includes('/'+captured.id+'?')?wrap(captured):{context:captured.context,explanations:[captured]};
 await dbExplanationHistory('sql_plan_explanation');await dbExplanationSelect(0);
 assert.ok(tree('copilot-result'));assert.notEqual(tree('copilot-result'),previous);assert.notEqual(tree('copilot-result').open,true);
}
''')

    def test_answer_first_collapsed_readonly_tree_human_provenance_and_localized_copy(self):
        self.javascript(r'''
await boot();editDoctor('SELECT x FROM raw.sample -- exact original <img>');
const sql=$('bear-query-sql').value,answer='Topology explanation <script>untrusted()</script>';
route=async()=>wrap(planRecord({text:answer,input:{sql,language:'en'}}));await startPlan('doctor');
for(const locale of ['en','ja']){
 setLanguage(locale);
 for(const id of ['copilot-result','explanation-doctor-output']){
  const node=$(id),text=visibleText(node);assert.equal(node.children[0].textContent,answer);
  assert.ok(text.includes('sales.marts'));assert.ok(text.includes('admin'));assert.ok(text.includes('LOCAL'));assert.ok(text.includes('local-model'));
  assert.ok(text.includes('2026-10-31T02:30:00Z'));assert.ok(text.includes(sql));
  const details=node.descendants().find(n=>n.tagName==='DETAILS');assert.ok(details);assert.notEqual(details.open,true);
  assert.equal(details.children[0].textContent,locale==='ja'?'安全な演算子ツリー（表示のみ）':'Safe operator tree (read only)');
  assert.ok(details.descendants().some(n=>n.tagName==='UL'));assert.doesNotMatch(text,/private-engine-id|ba7de711|a254ca08|projection_version|remote_sources|"operator"|database_id/);
  assert.equal(node.descendants().some(n=>['INPUT','TEXTAREA','BUTTON','SCRIPT','IMG'].includes(n.tagName)),false);
 }
 assert.equal($('explanation-copilot-plan').textContent,locale==='ja'?'新しい実行計画を取得して説明':'Acquire a new plan and explain');
 assert.ok(t(dbPlanHelp).includes(locale==='ja'?'LOCAL が起動':'may wake LOCAL'));
 assert.ok(t(dbPlanHelp).includes(locale==='ja'?'元の SQL は実行しません':'source SQL is not run'));
}
''')

    def test_unknown_query_evidence_and_fixed_view_input_guidance_with_a1_fallback(self):
        self.javascript(r'''
await boot();
for(const code of ['AI_INPUT_INVALID','AI_PLAN_VIEW_UNSUPPORTED']){
 editSQL('SELECT * FROM raw.source_view WHERE marker = '+Math.random());
 route=async()=>({http:400,body:{error:{code,message:'SECRET_SERVER_TEXT',details:{key:'SECRET'}}}});await startPlan();
 const key=dbExplanationIntent('sql_plan_explanation').key;
 for(const locale of ['en','ja']){
  setLanguage(locale);assert.doesNotMatch(visibleText($('copilot-result')),/SECRET/);
  assert.match(visibleText($('copilot-result')),locale==='en'?/Explain SQL/:/SQLの説明/);
 }
 assert.equal($('copilot-explain').disabled,false);setLanguage('en');route=async()=>wrap(record());await $('copilot-explain').onclick();
 assert.equal(dbExplanationIntent('sql_plan_explanation').key,key);assert.equal(dbExplanation.record.kind,'sql_explanation');
}
editSQL('SELECT x FROM raw.normal');route=async()=>wrap(pendingPlan({state:'UNKNOWN',plan:{...pendingPlan().plan,query_state:'UNKNOWN'}}));await startPlan();
const node=$('copilot-result');assert.match(visibleText(node),/Check the original query status with the operator/);
const evidence=node.descendants().find(n=>n.tagName==='DETAILS');assert.notEqual(evidence.open,true);
assert.ok(visibleText(evidence).includes('ba7de711-810a-4a9b-a1a9-2473b7faf13f'));assert.equal($('explanation-copilot-plan').disabled,true);
const unknownCount=planPosts().length;editSQL('SELECT x FROM raw.another');await startPlan();assert.equal(planPosts().length,unknownCount);
independentWorksheet();route=async()=>wrap(pendingPlan({state:'UNKNOWN'}));await startPlan();
assert.match(visibleText($('copilot-result')),/Check the original query status with the operator/);
independentWorksheet();editSQL('SELECT x FROM raw.third');route=async()=>wrap(planRecord({state:'UNKNOWN',inference_state:'UNKNOWN',text:null}));await startPlan();
assert.equal($('explanation-copilot-plan').disabled,true);assert.match(visibleText($('copilot-result')),/Plan query: Completed/);
assert.doesNotMatch(visibleText($('copilot-result')),/Query outcome unknown|no confirmed closure/);
''')

    def test_stale_phase_cancel_history_and_late_model_responses_cannot_restore_output(self):
        self.javascript(r'''
await boot([input(),input('b')]);
const changes=[()=>editSQL('SELECT '+Math.random()+' AS editor_value'),()=>editDoctor('SELECT '+Math.random()+' AS doctor_value'),
 ()=>{dbEpoch++;},()=>{dbAuthorizationVersion++;},()=>{dbMaximumRole='data_engineer';},
 ()=>{currentRole='bi_viewer';},()=>{worksheetId='b';},()=>{dbPrincipal=JSON.stringify(['other',true]);}];
for(const operation of ['phase','cancel','history','lateAI'])for(const change of changes){
 dbMaximumRole='admin';currentRole='admin';worksheetId='a';dbPrincipal=JSON.stringify(['owner',true]);
 await dbRefreshContext();
 independentWorksheet();const postsBefore=planPosts().length;
 editSQL('SELECT price FROM raw.arbitrary WHERE price > '+Math.random());
 route=async()=>wrap(operation==='lateAI'?planRecord({state:'RUNNING',inference_state:'RUNNING',text:null}):pendingPlan());await startPlan();
 assert.equal(planPosts().length,postsBefore+1);
 const held=deferred();route=()=>held.promise;
 const pending=operation==='cancel'?cancelPlan():operation==='history'?dbExplanationHistory('sql_plan_explanation'):recoverPlan();
 await tick();change();dbControls();
 const r=planRecord({text:'STALE_PRIVATE_ANSWER'});
 held.resolve(operation==='history'?{context:r.context,explanations:[r]}:wrap(r));await pending;
 assert.equal(dbExplanation.record,null,operation);assert.deepEqual(dbExplanation.history,[]);
 assert.doesNotMatch(visibleText($('copilot-result')),/STALE_PRIVATE_ANSWER/);
}
''')

    def test_each_context_key_change_fences_late_plan_response(self):
        self.javascript(r'''
await boot();const original=dbContext;
for(const key of dbExplanationContextKeys){
 independentWorksheet();const before=planPosts().length;
 editSQL('SELECT '+Math.random());const accepted=planRecord(),held=deferred();route=()=>held.promise;
 const pending=startPlan();await tick();const c=original(dbCurrent());dbContext=()=>({...c,[key]:String(c[key])+'-changed'});dbControls();
 assert.equal(planPosts().length,before+1);
 held.resolve(wrap(accepted));await pending;assert.equal(dbExplanation.record,null,key);
 assert.equal($('explanation-copilot-plan-recover').disabled,true,key);dbContext=original;
}
''')


    def test_view_mixed_direct_nested_shared_dependency_projection_and_cached_provenance(self):
        self.javascript(r'''
await boot();editSQL('SELECT v.value FROM sandbox.report_view v JOIN raw.source_table t ON v.value=t.value');
const metadata=viewMetadata(),answer='Explain this captured topology';
metadata.view_dependencies[1].columns=[{name:'complex',type:'array(row("Mixed field" map(varchar, decimal(24, 8))))'}];
route=async()=>wrap(planRecord({metadata,text:answer}));await startPlan();
assert.deepEqual(dbExplanation.record.metadata,metadata);
const tree=id=>$(id).descendants().find(n=>n.tagName==='DETAILS');
const retained=tree('copilot-result');assert.notEqual(retained.open,true);
for(const id of ['copilot-result','explanation-doctor-output']){
 const node=$(id),details=tree(id);assert.equal(node.children[0].textContent,answer);
 const outside=node.children.filter(n=>n!==details).map(visibleText).join('\n');
 assert.match(outside,/TABLE · raw.source_table · 1 columns/);assert.match(outside,/VIEW · sandbox.report_view · 1 columns/);
 assert.doesNotMatch(outside,/shared_leaf|nested_view|VIEW dependencies/);
 assert.match(visibleText(details),/VIEW dependencies: 2/);
 assert.match(visibleText(details),/TABLE · raw.shared_leaf · 1 columns/);
 assert.match(visibleText(details),/VIEW · staging.nested_view · 1 columns/);
 assert.match(visibleText(details),/dependency provenance; not an operator-to-relation mapping/);
 assert.doesNotMatch(visibleText(node),/Mixed field|decimal\(24|"relations"/);
 assert.equal(node.descendants().some(n=>['BUTTON','INPUT','TEXTAREA'].includes(n.tagName)),false);
 assert.ok(node.children.some(n=>n.tagName==='PRE'&&n.textContent===$('sql').value));
}
retained.open=true;dbExplanationSync();assert.equal(tree('copilot-result'),retained);assert.equal(retained.open,true);
setLanguage('ja');assert.equal(tree('copilot-result'),retained);assert.equal(retained.open,true);
assert.match(visibleText(retained),/VIEW の依存先: 2/);assert.match(visibleText(retained),/演算子と参照先の対応を示すものではありません/);
''')

    def test_constant_view_empty_dependencies_and_legacy_table_a1_wire(self):
        self.javascript(r'''
await boot();editSQL('SELECT value FROM sandbox.constant_view');
const metadata={captured_at:'now',relations:[relation('sandbox','constant_view','VIEW')],view_dependencies:[]};
route=async()=>wrap(planRecord({metadata}));await startPlan();assert.deepEqual(dbExplanation.record.metadata,metadata);
assert.match(visibleText($('copilot-result')),/VIEW dependencies: 0/);
independentWorksheet();editSQL('SELECT value FROM raw.legacy_table');
const old={captured_at:'then',relations:[relation('raw','legacy_table')]};
route=async()=>wrap(planRecord({metadata:old}));await startPlan();assert.deepEqual(dbExplanation.record.metadata,old);
assert.equal(Object.hasOwn(dbExplanation.record.metadata,'view_dependencies'),false);
assert.deepEqual(Object.keys(JSON.parse(planPosts().at(-1).options.body)).sort(),['context','language','sql']);
assert.doesNotMatch(visibleText($('copilot-result')),/VIEW dependencies|dependency provenance/);
independentWorksheet();const a1={captured_at:'then',relations:[relation('sandbox','a1_view','VIEW')]};
route=async()=>wrap(record({metadata:{...a1,view_dependencies:[relation('raw','must_not_project')]}}));await $('copilot-explain').onclick();
assert.deepEqual(dbExplanation.record.metadata,a1);assert.equal(dbExplanation.record.plan_status,'NOT_COLLECTED');
assert.deepEqual(Object.keys(JSON.parse(posts().at(-1).options.body)).sort(),['context','language','sql']);
assert.doesNotMatch(visibleText($('copilot-result')),/must_not_project|VIEW dependencies|VIEW ·/);
''')

    def test_view_metadata_malformed_closure_rejected_on_every_response_path(self):
        self.javascript(r'''
await boot();
const invalid=[
 m=>{delete m.view_dependencies;},m=>{m.view_dependencies=null;},m=>{m.view_dependencies={};},
 m=>{m.view_dependencies=[null];},m=>{m.view_dependencies=[{...relation('raw','bad'),kind:'MATERIALIZED_VIEW'}];},
 m=>{m.view_dependencies[0].columns=null;},m=>{m.view_dependencies[0].columns=[{name:'v',type:8}];},
 m=>{m.view_dependencies[0].columns=[{name:'v',type:''}];},m=>{m.view_dependencies[0].columns=[{name:'v',type:{type:'bigint'}}];},
 m=>{m.view_dependencies[0].columns=[{name:'',type:'bigint'}];},m=>{m.view_dependencies[0].schema='';},
 m=>{m.view_dependencies[0].table=9;},m=>{m.view_dependencies.push(structuredClone(m.view_dependencies[0]));},
 m=>{m.view_dependencies.push(structuredClone(m.relations[0]));},m=>{m.relations.push(structuredClone(m.relations[1]));},
 m=>{m.relations[0].kind='UNKNOWN';},m=>{m.relations[0].columns=[{name:'v',type:false}];},
 m=>{m.view_dependencies=Array.from({length:63},(_,i)=>relation('raw','leaf_'+i));}
];
for(const operation of ['post','get','list','cancel'])for(const change of invalid){
 independentWorksheet();const metadata=viewMetadata();change(metadata);const bad=planRecord({metadata,text:'REJECTED_VIEW_ANSWER'});
 if(operation==='cancel'){route=async()=>wrap(pendingPlan());await startPlan();}
 route=async()=>operation==='list'?{context:bad.context,explanations:[bad]}:wrap(bad);
 if(operation==='post')await startPlan();
 if(operation==='get'){dbExplanation.history=[bad];dbExplanation.historyKind=bad.kind;await dbExplanationSelect(0);}
 if(operation==='list')await dbExplanationHistory(bad.kind);
 if(operation==='cancel')await cancelPlan();
 assert.doesNotMatch(visibleText($('copilot-result')),/REJECTED_VIEW_ANSWER|shared_leaf|nested_view/);
 if(operation==='list')assert.equal(dbExplanation.history.length,0);
 else assert.notEqual(dbExplanation.record?.text,'REJECTED_VIEW_ANSWER');
}
''')

    def test_view_combined_limits_utf8_and_qualified_identifier_identity(self):
        self.javascript(r'''
await boot();
const parse=metadata=>{const r=planRecord({metadata});return dbExplanationRecord(wrap(r),r.context,{kind:r.kind});};
const m={captured_at:'now',relations:[relation('sandbox','v','VIEW')],view_dependencies:Array.from({length:63},(_,i)=>relation('raw','t'+String(i).padStart(2,'0')))};
assert.equal(parse(m).metadata.view_dependencies.length,63);
m.view_dependencies.push(relation('raw','overflow'));assert.throws(()=>parse(m),/CONTEXT_MISMATCH/);
// Qualified pairs must not be flattened or case-folded: quoted identifiers retain identity.
const distinct={captured_at:'now',relations:[relation('a.b','c','VIEW')],view_dependencies:[relation('a','b.c'),relation('a.b','C')]};
assert.deepEqual(parse(distinct).metadata,distinct);
const bytes={captured_at:'now',relations:[relation('sandbox','v','VIEW')],view_dependencies:[relation('raw','leaf','TABLE',[{name:'v',type:'界'.repeat(22000)}])]};
assert.ok(JSON.stringify(bytes).length<64*1024);assert.ok(new TextEncoder().encode(JSON.stringify(bytes)).length>64*1024);
assert.throws(()=>parse(bytes),/CONTEXT_MISMATCH/);
const boundary={captured_at:'now',relations:[relation('sandbox','v','VIEW')],view_dependencies:[relation('raw','leaf','TABLE',[{name:'v',type:'x'}])]};
const envelope=m=>({relations:m.relations,view_dependencies:m.view_dependencies});
const size=new TextEncoder().encode(JSON.stringify(envelope(boundary))).length;
boundary.view_dependencies[0].columns[0].type+='x'.repeat(64*1024-size);
assert.equal(new TextEncoder().encode(JSON.stringify(envelope(boundary))).length,65536);
assert.ok(new TextEncoder().encode(JSON.stringify(boundary)).length>65536);assert.ok(parse(boundary));
boundary.view_dependencies[0].columns[0].type+='x';
assert.equal(new TextEncoder().encode(JSON.stringify(envelope(boundary))).length,65537);assert.throws(()=>parse(boundary),/CONTEXT_MISMATCH/);
''')

    def test_wide_legacy_table_and_a1_history_keep_prior_validation_and_projection(self):
        self.javascript(r'''
await boot();
const wideType='array(row("value" varchar)) '.repeat(3000);
const descriptor=relation('raw','legacy','TABLE',[{name:'wide',type:wideType}]);
// Old TABLE/A1 records had neither the new byte/count envelope nor relation uniqueness validation.
const original={captured_at:'2026-10-31T02:30:00Z',relations:Array.from({length:65},()=>structuredClone(descriptor))};
assert.ok(new TextEncoder().encode(JSON.stringify(original)).length>65536);
for(const kind of ['sql_plan_explanation','sql_explanation']){
 const endpoint=dbExplanationEndpoints[kind],make=kind==='sql_plan_explanation'?planRecord:record;
 const saved=make({metadata:{...structuredClone(original),private_definition:'NEVER_PROJECT',view_dependencies:'NEVER_PROJECT'}});
 route=async(path,options)=>{assert.equal(options?.method,undefined);assert.ok(path.startsWith(endpoint));
  return path.includes('/'+saved.id+'?')?wrap(saved):{context:saved.context,explanations:[saved]};};
 await dbExplanationHistory(kind);assert.equal(dbExplanation.history.length,1);
 assert.deepEqual(dbExplanation.history[0].metadata,original);
 await dbExplanationSelect(0);assert.equal(dbExplanation.record.kind,kind);
 assert.deepEqual(dbExplanation.record.metadata,original);
 assert.equal(Object.hasOwn(dbExplanation.record.metadata,'view_dependencies'),false);
 assert.equal(dbExplanation.record.metadata.relations[0].columns[0].type,wideType);
 assert.doesNotMatch(JSON.stringify(dbExplanation.record)+visibleText($('copilot-result')),/NEVER_PROJECT/);
}
assert.equal(planPosts().length,0);assert.equal(posts().length,0);
''')

    def test_view_private_metadata_never_projects_or_renders_from_history(self):
        self.javascript(r'''
await boot();const secret={definition:'PRIVATE_SENTINEL_SELECT_LITERAL',dialect:'PRIVATE_SENTINEL_DIALECT',properties:{owner:'PRIVATE_SENTINEL_OWNER'},
 defaults:'PRIVATE_SENTINEL_DEFAULT',digest:'PRIVATE_SENTINEL_DIGEST',uuid:'PRIVATE_SENTINEL_UUID',path:'PRIVATE_SENTINEL_PATH',literal:'PRIVATE_SENTINEL_LITERAL'};
const metadata={...viewMetadata(),...secret};
for(const r of [...metadata.relations,...metadata.view_dependencies]){Object.assign(r,secret);for(const c of r.columns)Object.assign(c,secret);}
const saved=planRecord({metadata}),frozen=structuredClone(saved);
let currentSource=structuredClone(metadata);
route=async(path,options)=>{assert.equal(options?.method,undefined);assert.ok(path.startsWith(planEndpoint));
 return path.includes('/'+saved.id+'?')?wrap(structuredClone(frozen)):{context:saved.context,explanations:[structuredClone(frozen)]};};
await dbExplanationHistory('sql_plan_explanation');
currentSource.relations[1].table='changed_view';currentSource.view_dependencies=[];currentSource=null; // Source changed, then deleted.
await dbExplanationSelect(0);assert.equal(dbExplanation.displayOrigin,'history');
const before=calls.length,node=$('copilot-result'),details=node.descendants().find(n=>n.tagName==='DETAILS');
details.open=true;dbExplanationSync();setLanguage('ja');await dbExplanationPoll();
assert.equal(calls.length,before);assert.equal(planPosts().length,0);assert.equal(posts().length,0);
assert.deepEqual(dbExplanation.record.metadata,viewMetadata());
assert.doesNotMatch(JSON.stringify(dbExplanation.record)+JSON.stringify(dbExplanation.history)+visibleText(node),/PRIVATE_SENTINEL|definition|dialect|properties|digest|uuid|literal/);
assert.match(visibleText(node),/sandbox.report_view/);assert.match(visibleText(node),/raw.shared_leaf/);
assert.equal(node.descendants().find(n=>n.tagName==='DETAILS'),details);assert.equal(details.open,true);
''')

    def test_view_ja_en_conditional_help_and_unsupported_fallback_original_receipt(self):
        self.javascript(r'''
await boot();editSQL('SELECT value FROM sandbox.unsupported_view');
route=async()=>({http:400,body:{code:'AI_PLAN_VIEW_UNSUPPORTED'}});await startPlan();
const original=structuredClone(planPosts()[0]),intent=dbExplanationIntent('sql_plan_explanation');
for(const locale of ['en','ja']){
 setLanguage(locale);const help=t(dbPlanHelp),fence=t(dbExplanationFence),notice=visibleText($('copilot-result'));
 assert.match(help,/INVOKER VIEW/);assert.match(fence,/INVOKER VIEW/);
 assert.match(help,locale==='en'?/current query permissions for all dependencies/:/すべての依存先への現在のクエリ権限/);
 assert.match(help,locale==='en'?/View definitions are checked at acquisition\. Detected changes prevent the explanation from being saved\./:/取得時点の定義を確認します。変更を検出した場合は説明を確定しません。/);
 assert.match(help,locale==='en'?/non-ANALYZE/:/非 ANALYZE/);assert.match(help,locale==='en'?/may wake LOCAL/:/LOCAL が起動/);
 assert.match(notice,locale==='en'?/This VIEW is unsupported/:/この VIEW は/);
 assert.match(notice,locale==='en'?/Explain SQL/:/SQLの説明/);
 assert.doesNotMatch(help,/zero unauthorized|zero internal/);
}
setLanguage('en');editSQL('SELECT 3 AS changed');dbExplanationInstall();
route=async(path,options)=>{assert.equal(options.body,original.options.body);assert.equal(options.headers['Idempotency-Key'],original.options.headers['Idempotency-Key']);return {http:400,body:{code:'AI_PLAN_VIEW_UNSUPPORTED'}};};
await recoverPlan();assert.equal(dbExplanationIntent('sql_plan_explanation').key,intent.key);
route=async()=>wrap(record());await $('copilot-explain').onclick();assert.equal(dbExplanation.record.kind,'sql_explanation');
assert.equal(dbExplanationIntent('sql_plan_explanation').key,intent.key);assert.equal($('sql').value,'SELECT 3 AS changed');
''')

    def test_bilingual_computed_label_dependencies_extract_without_browser(self):
        dictionary = harness.SCRIPT[harness.SCRIPT.index('const i18n = '):harness.SCRIPT.index("let language = 'en';")]
        additions = re.findall(
            r'const (?:dbCreationGuidance|dbPlanHelp|dbExplanation(?:Help|Lead|Instruction|ContextStep|Fence))=[^\n]*;'
            r'|Object\.assign\(i18n\.ja,\{[\s\S]*?\}\);', harness.SCRIPT)
        result = subprocess.run(['node', '-e', dictionary + '\n' + '\n'.join(additions)
                                 + '\nconsole.log(JSON.stringify(i18n.ja));'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        labels = json.loads(result.stdout)
        self.assertEqual(labels['Acquire plan and explain'], '実行計画を取得して説明')
        self.assertEqual(labels['Discard explanation result'], '説明結果を破棄')
        self.assertTrue(any('LOCAL が起動' in value for value in labels.values()))


if __name__ == '__main__':
    unittest.main()
