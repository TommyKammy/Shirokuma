"""Database SQL explanation UI, executing the production handlers in the Node adapter."""
import json
import re
import unittest

import test_workbench_database_ui as harness


COMMON = r'''
const sourceCalls=()=>calls.filter(c=>c.path.startsWith('/api/v1/bears/query-sources/latest?'));
const sourceReply=(extra={})=>({context:structuredClone(dbContext(dbCurrent())),source:{query_id:'source-query',created_at:'2026-10-31T01:00:00Z',state:'UNKNOWN',sql:'WITH q AS (SELECT 8) SELECT * FROM q UNION SELECT 9',context:structuredClone(dbContext(dbCurrent())),...extra}});
const loadSource=()=>$('explanation-latest').onclick();
const explanationCalls=()=>calls.filter(c=>c.path.startsWith('/api/v1/bears/explanations'));
const posts=()=>explanationCalls().filter(c=>c.options?.method==='POST');
const record=(extra={})=>({id:'explanation-1',kind:'sql_explanation',state:'SUCCEEDED',actor:'owner',
 context:structuredClone(dbContext(dbCurrent())),input:{sql:$('sql').value,language:'en'},
 model:{provider:'local-openai',name:'local-model'},metadata:{captured_at:'2026-10-31T01:00:00Z',relations:[]},
 text:'SQL explanation',error_code:null,plan_status:'NOT_COLLECTED',sql_status:'NOT_EXECUTED',
 created_at:'2026-10-31T01:00:00Z',updated_at:'2026-10-31T01:01:00Z',...extra});
const wrap=explanation=>({context:structuredClone(explanation.context),explanation});
const visibleText=node=>node.textContent+node.children.map(visibleText).join('\n');
const editDoctor=sql=>{$('bear-query-sql').value=sql;$('bear-query-sql').dispatchEvent(new Event('input'));};
const editSQL=sql=>{$('sql').value=sql;$('sql').dispatchEvent(new Event('input'));};
'''


class BearsDatabaseUI(unittest.TestCase):
    def javascript(self, body):
        harness.DatabaseUI.javascript(self, COMMON + body)

    def markup_javascript(self, body):
        # Seed the existing paragraphs from production source, with the Doctor field layout.
        lead = re.search(r'<h1>AI Bears</h1><p id="bears-lead">(.*?)</p>', harness.HTML)[1]
        intro = re.search(r"key:'query',name:'Query Doctor Bear',description:'([^']+)'", harness.SCRIPT)[1]
        fixture = 'const legacyLead=' + json.dumps(lead) + ', legacyIntro=' + json.dumps(intro) + r'''
const lead=document.createElement('p');lead.id='bears-lead';ui(lead,legacyLead);$('bears').append(lead);
const intro=document.createElement('p');ui(intro,legacyIntro);
const content=document.createElement('div'),field=document.createElement('div'),label=document.createElement('label');
field.className='bear-field';field.append(label,$('bear-query-sql'));content.append(intro,field);$('bear-panel-query').append(content);
$('bear-query-sql').before=node=>{field.append(node);field.children.splice(field.children.indexOf(node),1);field.children.splice(field.children.indexOf($('bear-query-sql')),0,node);};
'''
        self.javascript(fixture + body)

    def test_original_a1_explicit_recovery_after_both_drafts_and_reload_uses_only_original_get(self):
        for state in ['RUNNING', 'UNKNOWN']:
            with self.subTest(state=state):
                self.javascript('const initialState=' + json.dumps(state) + r'''
await boot();const accepted=record({state:initialState,text:null});route=async()=>wrap(accepted);await $('copilot-explain').onclick();const original=structuredClone(posts()[0]),saved=storage.get(dbExplanationKey);
editSQL('SELECT edited worksheet');editDoctor('SELECT edited Doctor');dbExplanationInstall();
assert.equal($('explanation-doctor-recover').disabled,false);assert.equal($('explanation-copilot-recover').disabled,false);assert.equal($('explanation-latest').disabled,true);const count=explanationCalls().length;
dbControls();await tick();assert.equal(explanationCalls().length,count);
route=async(path,options)=>{assert.equal(options.method,undefined);assert.ok(path.startsWith('/api/v1/bears/explanations/'+accepted.id+'?'));assert.equal(new URL(path,'http://local').searchParams.get('context'),JSON.stringify(accepted.context));assert.equal(options.headers['X-Shirokuma-Role'],'admin');return wrap({...accepted,state:'SUCCEEDED',text:'OLD SQL ANSWER'});};
await $('explanation-doctor-recover').onclick();assert.equal(explanationCalls().length,count+1);assert.equal(posts().length,1);assert.equal($('sql').value,'SELECT edited worksheet');assert.equal($('bear-query-sql').value,'SELECT edited Doctor');assert.equal(dbExplanation.record,null);assert.deepEqual(dbExplanation.history,[]);assert.equal($('explanation-latest').disabled,false);
for(const locale of ['ja','en']){setLanguage(locale);assert.ok(visibleText($('explanation-doctor-output')).includes(t('Original request refreshed. Current SQL was kept.')));assert.doesNotMatch(visibleText($('explanation-doctor-output')),/OLD SQL ANSWER/);}
assert.equal(storage.get(dbExplanationKey),saved);assert.equal(JSON.stringify(dbExplanation.intents[0].body),original.options.body);assert.equal(dbExplanation.intents[0].key,original.options.headers['Idempotency-Key']);
route=async()=>sourceReply();await loadSource();assert.equal(sourceCalls().length,1);assert.ok(dbSource.origin);
''')

    def test_original_a1_broad_recovery_selection_never_applies_to_automatic_poll(self):
        self.javascript(r'''
await boot();const current=record({state:'RUNNING',text:null});route=async()=>wrap(current);await $('copilot-explain').onclick();
const capture=structuredClone(dbExplanationCapture());capture.editor='SELECT hidden';capture.doctor='SELECT hidden Doctor';
dbExplanation.intents.push(dbExplanationFreezeIntent({kind:'sql_explanation',endpoint:'/api/v1/bears/explanations',source:'copilot',key:'hidden-key',id:'hidden-id',capture,body:{context:capture.context,sql:capture.editor,language:'en'}}));dbExplanationPersist();
assert.equal(dbSourceRecoveryIntent().id,'hidden-id');route=async(path,options)=>{assert.equal(options.method,undefined);assert.ok(path.startsWith('/api/v1/bears/explanations/'+current.id+'?'));return wrap(current);};
await dbExplanationPoll();const count=explanationCalls().length;
await dbExplain('unrelated',true);assert.equal(explanationCalls().length,count+1);
// With neither draft matching an original, unrelated labels cannot recover a hidden intent.
editSQL('SELECT unrelated current');editDoctor('SELECT unrelated Doctor');const editedCount=explanationCalls().length;
await dbExplain('unrelated',true);assert.equal(explanationCalls().length,editedCount);
assert.equal(posts().length,1);assert.equal(dbSourceA1Held(dbExplanation.intents.at(-1)),true);assert.equal($('explanation-latest').disabled,true);
''')

    def test_original_a1_uncertain_ack_explicit_recovery_replays_original_after_edits_reload(self):
        self.javascript(r'''
await boot();route=async()=>{throw Error('lost ACK');};await $('copilot-explain').onclick();const original=structuredClone(posts()[0]);editSQL('SELECT current editor');editDoctor('SELECT current Doctor');dbExplanationInstall();
assert.equal($('explanation-doctor-recover').disabled,false);assert.equal(posts().length,1);
route=async(path,options)=>{assert.equal(path,'/api/v1/bears/explanations');assert.equal(options.body,original.options.body);assert.equal(options.headers['Idempotency-Key'],original.options.headers['Idempotency-Key']);return {http:400,body:{error:{code:'AI_INPUT_INVALID'}}};};
await $('explanation-doctor-recover').onclick();assert.equal(posts().length,2);assert.equal($('explanation-latest').disabled,false);assert.equal(dbExplanation.intents[0].id,null);assert.equal($('sql').value,'SELECT current editor');assert.equal($('bear-query-sql').value,'SELECT current Doctor');assert.equal(dbExplanation.record,null);
dbExplanationInstall();assert.equal($('explanation-latest').disabled,true);assert.equal($('explanation-doctor-recover').disabled,false);await $('explanation-doctor-recover').onclick();assert.equal(posts().length,3);assert.equal($('explanation-latest').disabled,false);
''')

    def test_original_a1_older_than_fifty_history_records_remains_explicitly_recoverable(self):
        self.javascript(r'''
await boot();const context=structuredClone(dbContext(dbCurrent())),capture=structuredClone(dbExplanationCapture());
const saved=Array.from({length:51},(_,i)=>({kind:'sql_explanation',endpoint:'/api/v1/bears/explanations',source:'copilot',id:'saved-'+i,key:'original-key-'+i,capture:{...capture,editor:'SELECT '+i,doctor:'SELECT doctor_'+i},body:{context,sql:'SELECT '+i,language:'en'}}));
storage.set(dbExplanationKey,JSON.stringify(saved));editSQL('SELECT current');editDoctor('SELECT current Doctor');dbExplanationInstall();
const newest=saved.slice(1).map(intent=>record({id:intent.id,input:{sql:intent.body.sql,language:'en'}}));
route=async(path,options)=>{assert.equal(options.method,undefined);return {context,explanations:newest};};await $('explanation-doctor-history').onclick();const count=explanationCalls().length;
assert.equal(dbSourceRecoveryIntent().id,'saved-0');assert.equal($('explanation-doctor-recover').disabled,false);assert.equal($('explanation-latest').disabled,true);
route=async(path,options)=>{assert.equal(options.method,undefined);assert.ok(path.startsWith('/api/v1/bears/explanations/saved-0?'));assert.equal(new URL(path,'http://local').searchParams.get('context'),JSON.stringify(context));return wrap(record({id:'saved-0',input:{sql:'SELECT 0',language:'en'}}));};
await $('explanation-doctor-recover').onclick();assert.equal(explanationCalls().length,count+1);assert.equal(posts().length,0);assert.equal($('explanation-latest').disabled,false);assert.equal(dbExplanation.record,null);assert.equal($('sql').value,'SELECT current');assert.equal($('bear-query-sql').value,'SELECT current Doctor');assert.equal(dbExplanation.intents[0].key,'original-key-0');assert.deepEqual(dbExplanation.intents[0].body,saved[0].body);
route=async()=>sourceReply();await loadSource();assert.ok(dbSource.origin);assert.equal(sourceCalls().length,1);
''')

    def test_original_a1_recovery_identity_fences_actor_full_context_and_worksheet(self):
        self.javascript(r'''
await boot([input('a'),input('b')]);const accepted=record({state:'RUNNING',text:null});route=async()=>wrap(accepted);await $('copilot-explain').onclick();editSQL('SELECT current');editDoctor('SELECT Doctor');dbExplanationInstall();const count=explanationCalls().length;
const changes=[
 [()=>{worksheetId='b';},()=>{worksheetId='a';}],
 [()=>{dbCurrent().schema_id='raw';},()=>{dbCurrent().schema_id='schema';}],
 [()=>{dbCurrent().selected_role='bi_viewer';},()=>{dbCurrent().selected_role='admin';}],
 [()=>{dbPrincipal=JSON.stringify(['another',true]);},()=>{dbPrincipal=JSON.stringify(['owner',true]);}]
];
for(const [change,restore] of changes){change();dbControls();assert.equal($('explanation-doctor-recover').disabled,true);await $('explanation-doctor-recover').onclick();assert.equal(explanationCalls().length,count);restore();dbControls();}
assert.equal($('explanation-doctor-recover').disabled,false);assert.equal($('explanation-latest').disabled,true);assert.equal(posts().length,1);
''')

    def test_original_a1_recovery_late_edits_and_authority_roundtrips_never_prove_closure(self):
        self.javascript(r'''
await boot();
const mutations=[()=>editSQL('SELECT late editor'),()=>editDoctor('SELECT late Doctor'),()=>{const value=$('bear-query-sql').value;editDoctor('SELECT temporary');editDoctor(value);},()=>editDoctor($('bear-query-sql').value),()=>{currentRole='bi_viewer';dbControls();currentRole='admin';dbControls();},()=>{dbMaximumRole='bi_viewer';dbControls();dbMaximumRole='admin';dbControls();},()=>{dbAuthorizationVersion++;dbControls();},()=>{dbEpoch++;dbControls();},()=>{const old=dbPrincipal;dbPrincipal=JSON.stringify(['other',true]);dbControls();dbPrincipal=old;dbControls();},()=>{const w=dbCurrent(),old=w.schema_id;w.schema_id='raw';dbControls();w.schema_id=old;dbControls();}];
for(const mutate of mutations){
 const w={...dbCurrent(),id:crypto.randomUUID()};worksheets.push(w);worksheetId=w.id;await dbRefreshContext();editSQL('SELECT original');editDoctor('SELECT original Doctor');const accepted=record({id:'request-'+w.id,state:'RUNNING',text:null});route=async()=>wrap(accepted);await $('copilot-explain').onclick();editSQL('SELECT current');editDoctor('SELECT current Doctor');dbExplanationInstall();const intent=dbSourceRecoveryIntent(),wait=deferred();route=()=>wait.promise;
 const pending=$('explanation-doctor-recover').onclick();await tick();mutate();const editor=$('sql').value,doctor=$('bear-query-sql').value;
 wait.resolve(wrap({...accepted,state:'SUCCEEDED',text:'STALE'}));await pending;if(dbReason())await dbRefreshContext();assert.equal(dbSourceA1Held(intent),true,'held after '+mutate+' '+dbReason());assert.equal($('explanation-latest').disabled,true);assert.equal(dbExplanation.record,null);assert.equal($('sql').value,editor);assert.equal($('bear-query-sql').value,doctor);assert.doesNotMatch(visibleText($('explanation-doctor-output')),/STALE/);
}
''')

    def test_latest_source_a1_structured_no_admission_is_memory_only_and_original_recovery_exact(self):
        for payload in [{'code': 'AI_INPUT_INVALID'}, {'error': {'code': 'AI_INPUT_INVALID'}}]:
            with self.subTest(payload=payload):
                self.javascript('const rejection=' + json.dumps(payload) + r'''
await boot();route=async()=>({http:400,body:rejection});await $('copilot-explain').onclick();
const original=structuredClone(posts()[0]),intent=dbExplanation.intents[0],saved=storage.get(dbExplanationKey),capture=intent.capture;
assert.equal(intent.id,null);assert.equal(dbExplanation.record,null);assert.equal($('explanation-latest').disabled,false);assert.equal($('copilot-explain').disabled,true);
editDoctor('SELECT user draft');editSQL('SELECT different editor');assert.equal($('copilot-explain').disabled,false);assert.equal($('explanation-latest').disabled,false);
route=async()=>sourceReply();await loadSource();assert.ok(dbSource.origin);assert.equal(sourceCalls().length,1);assert.equal(posts().length,1);assert.equal(storage.get(dbExplanationKey),saved);assert.equal(intent.key,original.options.headers['Idempotency-Key']);assert.equal(JSON.stringify(intent.body),original.options.body);assert.equal(intent.id,null);
const hints=JSON.parse(saved);Object.assign(hints[0],{terminal:true,noAdmission:true,sourceNoAdmission:true,state:'FAILED'});storage.set(dbExplanationKey,JSON.stringify(hints));dbExplanationInstall();assert.equal($('explanation-latest').disabled,true);await dbSourceLoad();assert.equal(sourceCalls().length,1);
editSQL(capture.editor);editDoctor(capture.doctor);route=async()=>({http:400,body:rejection});await $('explanation-copilot-recover').onclick();
assert.equal(posts().length,2);assert.equal(posts()[1].options.body,original.options.body);assert.equal(posts()[1].options.headers['Idempotency-Key'],original.options.headers['Idempotency-Key']);assert.equal(dbExplanation.intents[0].id,null);assert.equal($('explanation-latest').disabled,false);
// A later uncertain recovery revokes the earlier no-admission receipt.
route=async()=>{throw Error('transport');};await $('explanation-copilot-recover').onclick();assert.equal($('explanation-latest').disabled,true);
''')

    def test_latest_source_a1_nonqualifying_rejections_and_accepted_ids_stay_held(self):
        self.javascript(r'''
await boot();
const responses=[
 ()=>({http:400,body:{message:'AI_INPUT_INVALID'}}),()=>({http:400,body:{error:'AI_INPUT_INVALID'}}),
 ()=>({code:'AI_INPUT_INVALID'}),()=>({http:400,body:{error_code:'AI_INPUT_INVALID'}}),
 ()=>({http:400,body:{code:'AI_PLAN_VIEW_UNSUPPORTED'}}),()=>({http:400,body:{code:'OTHER'}}),
 ()=>({http:401,body:{code:'AI_INPUT_INVALID'}}),()=>({http:403,body:{code:'AI_INPUT_INVALID'}}),()=>({http:500,body:{code:'AI_INPUT_INVALID'}}),
 ()=>({http:400,body:{code:'AI_INPUT_INVALID',id:'accepted'}}),()=>({http:400,body:{code:'AI_INPUT_INVALID',explanation:{id:'accepted'}}}),
 ()=>({http:400,body:{error:{code:'AI_INPUT_INVALID',request_id:'accepted'}}}),()=>({http:400,body:{code:'AI_INPUT_INVALID',query_id:'accepted'}}),
 ()=>({malformed:true}),()=>{throw Error('AI_INPUT_INVALID');}
];
for(const reply of responses){
 const w={...dbCurrent(),id:crypto.randomUUID()};worksheets.push(w);worksheetId=w.id;dbControls();
 route=async()=>reply();await $('copilot-explain').onclick();const intent=dbExplanation.intents.at(-1);assert.equal(intent.id,null);assert.equal($('explanation-latest').disabled,true);await dbSourceLoad();assert.equal(sourceCalls().length,0);
}
const w={...dbCurrent(),id:crypto.randomUUID()};worksheets.push(w);worksheetId=w.id;dbControls();const accepted=record({state:'RUNNING',text:null});route=async()=>wrap(accepted);await $('copilot-explain').onclick();
const intent=dbExplanation.intents.at(-1),key=intent.key,body=JSON.stringify(intent.body);route=async()=>({http:400,body:{code:'AI_INPUT_INVALID'}});await $('explanation-copilot-recover').onclick();assert.equal(intent.id,accepted.id);assert.equal(intent.key,key);assert.equal(JSON.stringify(intent.body),body);assert.equal($('explanation-latest').disabled,true);
''')

    def test_latest_source_a1_no_admission_authority_roundtrips_and_stale_response_remain_held(self):
        self.javascript(r'''
await boot();
for(const mutate of [()=>{currentRole='bi_viewer';dbControls();currentRole='admin';dbControls();},()=>{dbMaximumRole='bi_viewer';dbControls();dbMaximumRole='admin';dbControls();},()=>{const w=dbCurrent(),schema=w.schema_id;w.schema_id='raw';dbControls();w.schema_id=schema;dbControls();},()=>{dbAuthorizationVersion++;dbControls();},()=>{dbEpoch++;dbControls();}]){
 const w={...dbCurrent(),id:crypto.randomUUID()};worksheets.push(w);worksheetId=w.id;dbControls();route=async()=>({http:400,body:{code:'AI_INPUT_INVALID'}});await $('copilot-explain').onclick();assert.equal($('explanation-latest').disabled,false,'fresh '+mutate+' '+dbReason());editDoctor('SELECT different doctor');editSQL('SELECT different editor');assert.equal($('explanation-latest').disabled,false,'draft '+mutate+' '+dbReason());
 mutate();assert.equal($('explanation-latest').disabled,true);await dbSourceLoad();assert.equal(sourceCalls().length,0);
}
const w={...dbCurrent(),id:crypto.randomUUID()};worksheets.push(w);worksheetId=w.id;dbControls();await dbRefreshContext();const wait=deferred();route=()=>wait.promise;const pending=$('copilot-explain').onclick();await tick();currentRole='bi_viewer';dbControls();currentRole='admin';dbControls();wait.resolve({http:400,body:{code:'AI_INPUT_INVALID'}});await pending;assert.equal($('explanation-latest').disabled,true);assert.equal(dbSourceProofs.has(dbExplanation.intents.at(-1)),false);
''')

    def test_latest_source_explicit_exact_get_draft_only_projection_and_localized_origin(self):
        self.javascript(r'''
await boot();editDoctor('SELECT manual');
queryResult={columns:['keep'],rows:[[4]]};const results=JSON.stringify(queryResult),runs=JSON.stringify([...dbRuns]),worksheet=JSON.stringify(dbCurrent()),editor=$('sql').value;
assert.equal(sourceCalls().length,0);
for(const state of ['QUEUED','RUNNING','FINISHED','FAILED','CANCELED','UNKNOWN']){
 const reply=sourceReply({state,query_id:'<img src=x>',private_provider:'PRIVATE',backend_id:'PRIVATE'});reply.private_config='PRIVATE';
 route=async(path,options)=>{assert.ok(path.startsWith('/api/v1/bears/query-sources/latest?'));assert.equal(options.method,undefined);assert.equal(options.body,undefined);assert.equal(options.headers['X-Shirokuma-Role'],'admin');assert.equal(new URL(path,'http://local').searchParams.get('context'),JSON.stringify(dbContext(dbCurrent())));return reply;};
 queryResult=JSON.parse(results);await loadSource();assert.equal(JSON.stringify(queryResult),results);assert.equal($('bear-query-sql').value,reply.source.sql);
 assert.deepEqual(Object.keys(dbSource.origin).sort(),['context','created_at','query_id','sql','state']);
 assert.equal(storage.get('shirokuma.sql-explanation-doctor.'+dbPrincipal),JSON.stringify(reply.source.sql));
 for(const locale of ['ja','en']){setLanguage(locale);const text=visibleText($('explanation-source'));assert.ok(text.includes(t('Latest SELECT in this database, schema and role (read only)')));assert.ok(text.includes(dbExplanationStateLabel(state)));assert.ok(text.includes('<img src=x>'));assert.ok(text.includes(reply.source.created_at));assert.doesNotMatch(text,/PRIVATE|current worksheet query|database_id/);assert.equal($('explanation-source').descendants().some(n=>['IMG','INPUT','BUTTON'].includes(n.tagName)),false);assert.equal($('explanation-latest').textContent,locale==='ja'?'最新の SELECT を読み込む':'Load latest SELECT');}
}
assert.equal(posts().length,0);assert.equal(explanationCalls().length,0);assert.equal($('sql').value,editor);assert.equal(JSON.stringify([...dbRuns]),runs);assert.equal(JSON.stringify(dbCurrent()),worksheet);
assert.equal([...storage.values()].some(value=>value.includes('source-query')||value.includes('PRIVATE')),false);
editDoctor($('bear-query-sql').value);assert.equal(dbSource.origin,null);
route=async()=>sourceReply();await loadSource();$('explanation-copy').onclick();assert.equal(dbSource.origin,null);
''')

    def test_latest_source_normal_input_clears_incompatible_display_and_post_stays_exact(self):
        self.javascript(r'''
await boot();route=async()=>wrap(record());await $('copilot-explain').onclick();const durable=storage.get(dbExplanationKey);
assert.ok(dbExplanation.record);route=async()=>sourceReply();await loadSource();
assert.equal(dbExplanation.record,null);assert.deepEqual(dbExplanation.history,[]);assert.equal(dbExplanation.notice,'');assert.equal(storage.get(dbExplanationKey),durable);assert.ok(dbSource.origin);
route=async(path,options)=>wrap(record({input:JSON.parse(options.body)}));await $('bear-query-run').onclick();
const body=JSON.parse(posts().at(-1).options.body);assert.deepEqual(Object.keys(body).sort(),['context','language','sql']);assert.equal(body.sql,sourceReply().source.sql);assert.deepEqual(body.context,dbContext(dbCurrent()));
''')

    def test_latest_source_null_error_and_malformed_keep_manual_draft_fixed_guidance(self):
        self.javascript(r'''
await boot();
const bad=[()=>({context:dbContext(dbCurrent()),source:null}),()=>({http:403,body:{sql:'SECRET',message:'SECRET'}}),()=>({malformed:true}),()=>({}),()=>{const x=sourceReply();delete x.context.grant_revision;return x;},()=>{const x=sourceReply();x.context.sql_schema='other';return x;},()=>sourceReply({context:{...dbContext(dbCurrent()),extra:'SECRET'}}),()=>sourceReply({context:{...dbContext(dbCurrent()),grant_revision:999}}),()=>sourceReply({query_id:''}),()=>sourceReply({created_at:'yesterday'}),()=>sourceReply({state:'SUCCEEDED'}),()=>sourceReply({sql:5})];
for(const [index,reply] of bad.entries()){
 editDoctor('SELECT manual');route=async()=>reply();await loadSource();assert.equal($('bear-query-sql').value,'SELECT manual');assert.equal(dbSource.origin,null);
 for(const locale of ['en','ja']){setLanguage(locale);assert.equal(visibleText($('explanation-source')).trim(),t(index===0?'No eligible SELECT found. Enter SQL or copy it from the worksheet.':'Latest SELECT unavailable. Enter SQL or copy it from the worksheet.'));assert.doesNotMatch(visibleText($('explanation-source')),/SECRET/);}
}
assert.equal(posts().length,0);
''')

    def test_latest_source_late_responses_discard_edits_copy_scope_and_authority_roundtrips(self):
        self.javascript(r'''
await boot([input('a'),input('b')]);
const mutations=[()=>editDoctor('SELECT edited'),()=>{const old=$('bear-query-sql').value;editDoctor('SELECT temp');editDoctor(old);},()=>$('explanation-copy').onclick(),()=>{editSQL('SELECT temp');editSQL('SELECT 1');},()=>{worksheetId='b';dbControls();worksheetId='a';dbControls();},()=>{const w=dbCurrent(),old=w.schema_id;w.schema_id='raw';dbControls();w.schema_id=old;dbControls();},()=>{currentRole='bi_viewer';dbControls();currentRole='admin';dbControls();},()=>{dbMaximumRole='bi_viewer';dbControls();dbMaximumRole='admin';dbControls();},()=>{const old=dbPrincipal;dbPrincipal=JSON.stringify(['other',true]);dbControls();dbPrincipal=old;dbControls();},()=>{dbAuthorizationVersion++;dbControls();},()=>{dbRoleAuthorizationVersions.set(dbTabRole(),dbRoleVersion(dbTabRole())+1);dbControls();},()=>{dbEpoch++;dbControls();}];
for(const mutate of mutations)for(const outcome of ['source','null','error']){
 editDoctor('SELECT manual');const wait=deferred(),reply=sourceReply();route=()=>wait.promise;
 const pending=loadSource();await tick();const count=sourceCalls().length;await loadSource();await dbSourceLoad();assert.equal(sourceCalls().length,count);assert.equal($('explanation-latest').disabled,true);
 assert.equal($('bear-query-run').disabled,true);await dbExplain('doctor');await dbExplain('copilot',false,'sql_plan_explanation');assert.equal(posts().length,0);
 mutate();const draft=$('bear-query-sql').value;dbSource.notice='newer message';
 wait.resolve(outcome==='source'?reply:outcome==='null'?{context:reply.context,source:null}:{http:500,body:{message:'SECRET'}});await pending;
 assert.equal($('bear-query-sql').value,draft);assert.equal(dbSource.notice,'newer message');assert.equal(dbSource.origin,null);assert.equal(dbSource.token,null);
}
''')

    def test_latest_source_a1_identity_hold_after_edits_reload_and_authoritative_terminal(self):
        self.javascript(r'''
await boot();const accepted=record({state:'RUNNING',text:null});route=async()=>wrap(accepted);await $('copilot-explain').onclick();
const original=structuredClone(posts()[0]),intent=dbExplanation.intents[0];
editDoctor('SELECT manual');editSQL('SELECT changed');$('explanation-copy').onclick();
for(const reload of [false,true]){
 if(reload){const saved=JSON.parse(storage.get(dbExplanationKey));Object.assign(saved[0],{state:'SUCCEEDED',terminal:true,noAdmission:true});storage.set(dbExplanationKey,JSON.stringify(saved));dbExplanationInstall();}
 assert.equal($('explanation-latest').disabled,true);const n=sourceCalls().length;await dbSourceLoad();assert.equal(sourceCalls().length,n);
}
// Restore A1's original distinct-input capture for its unchanged recovery path.
editSQL(intent.capture.editor);editDoctor(intent.capture.doctor);
for(const state of ['UNKNOWN','RUNNING','FAILED']){route=async(path,options)=>{assert.notEqual(options?.method,'POST');assert.ok(path.includes('/'+accepted.id+'?'));assert.equal(new URL(path,'http://local').searchParams.get('context'),JSON.stringify(intent.body.context));return wrap({...accepted,state});};await $('explanation-copilot-recover').onclick();editDoctor('SELECT unrelated');assert.equal($('explanation-latest').disabled,state!=='FAILED');editDoctor(intent.capture.doctor);}
assert.equal(posts().length,1);assert.equal(dbExplanation.intents[0].key,original.options.headers['Idempotency-Key']);assert.equal(JSON.stringify(dbExplanation.intents[0].body),original.options.body);
dbAuthorizationVersion++;dbControls();assert.equal($('explanation-latest').disabled,true);
''')

    def test_latest_source_a1_uncertain_ack_original_key_and_distinct_input_unchanged(self):
        self.javascript(r'''
await boot();route=async()=>{throw Error('lost ACK');};await $('copilot-explain').onclick();const original=structuredClone(posts()[0]),capture=dbExplanation.intents[0].capture;
editSQL('SELECT changed');assert.equal($('copilot-explain').disabled,false);assert.equal($('explanation-latest').disabled,true);await dbSourceLoad();assert.equal(sourceCalls().length,0);
dbExplanationInstall();assert.equal($('explanation-latest').disabled,true);editSQL(capture.editor);editDoctor(capture.doctor);
route=async()=>wrap(record({state:'FAILED',text:null}));await $('explanation-copilot-recover').onclick();assert.equal(posts().length,2);assert.equal(posts()[1].options.body,original.options.body);assert.equal(posts()[1].options.headers['Idempotency-Key'],original.options.headers['Idempotency-Key']);assert.equal($('explanation-latest').disabled,false);
''')

    def test_latest_source_own_input_side_effects_cannot_attach_stale_origin(self):
        self.javascript(r'''
await boot();route=async()=>sourceReply();let nested=false;
$('bear-query-sql').addEventListener('input',()=>{if(!nested){nested=true;editDoctor('SELECT user edit from listener');}});
await loadSource();assert.equal($('bear-query-sql').value,'SELECT user edit from listener');assert.equal(dbSource.origin,null);assert.equal(dbSource.notice,'');assert.equal(dbSource.token,null);
assert.equal(storage.get('shirokuma.sql-explanation-doctor.'+dbPrincipal),JSON.stringify('SELECT user edit from listener'));
''')

    def test_latest_source_old_completion_never_finishes_reloaded_request_token(self):
        self.javascript(r'''
await boot();const old=deferred(),fresh=deferred(),reply=sourceReply();route=()=>old.promise;const first=loadSource();await tick();
dbExplanationInstall();route=()=>fresh.promise;const second=loadSource();await tick();const token=dbSource.token;
old.resolve(reply);await first;assert.equal(dbSource.token,token);assert.equal($('explanation-latest').disabled,true);assert.equal(dbSource.origin,null);
fresh.resolve({...reply,source:{...reply.source,sql:'SELECT fresh'}});await second;assert.equal(dbSource.token,null);assert.equal($('bear-query-sql').value,'SELECT fresh');assert.ok(dbSource.origin);
''')

    def test_latest_source_recovery_during_get_remains_available_and_discards_source(self):
        self.javascript(r'''
await boot();const completed=record();route=async()=>wrap(completed);await $('copilot-explain').onclick();const wait=deferred(),reply=sourceReply();route=()=>wait.promise;
const pending=loadSource();await tick();assert.equal($('explanation-copilot-recover').disabled,false);
route=async()=>wrap(completed);await $('explanation-copilot-recover').onclick();const draft=$('bear-query-sql').value;
wait.resolve(reply);await pending;assert.equal($('bear-query-sql').value,draft);assert.equal(dbSource.origin,null);assert.equal(dbExplanation.record.id,completed.id);assert.equal(posts().length,1);
''')

    def test_database_lead_instruction_and_single_metadata_helper_preserve_legacy_copy(self):
        self.markup_javascript(r'''
assert.equal(lead.textContent,'Run the five resident specialists. Inspect their findings, recommendations, and telemetry.');
assert.equal(intro.textContent,'Explain SQL or inspect a recent query for performance advice.');
assert.equal(dbExplanation.installed,false);
setLanguage('ja');assert.match(lead.textContent,/5 体の常駐スペシャリスト/);
setLanguage('en');await boot();const notice=$('database-bears-notice');
for(const locale of ['en','ja']){
 setLanguage(locale);
 assert.equal(lead.textContent,t(dbExplanationLead));
 assert.equal($('bears').querySelector('p'),notice);
 assert.equal(notice.textContent,t(dbExplanationFence));
 assert.notEqual(notice.textContent,lead.textContent);
 assert.equal(intro.textContent,locale==='en'?'Enter SQL or copy it from the active worksheet.':'SQL を入力するか、ワークシートからコピーしてください。');
 assert.doesNotMatch(lead.textContent,/five resident specialists|5 体の常駐スペシャリスト/);
 assert.match(lead.textContent,/Query Doctor/);
 assert.ok(lead.textContent.includes(locale==='en'?'Other Bears are unavailable.':'他の Bears は利用できません。'));
 const helpers=$('bear-panel-query').descendants().filter(n=>n.textContent===t(dbExplanationHelp));assert.equal(helpers.length,1);
 assert.ok(helpers[0].textContent.includes(locale==='en'?'SQL is not executed.':'SQL は実行しません。'));
 assert.equal($('bear-query-sql').disabled,false);assert.equal(explanationCalls().length,0);
}
''')

    def test_doctor_context_uses_active_worksheet_and_updates_without_fetches(self):
        self.markup_javascript(r'''
await dualBoot();await exploreFinance();
const line=$('explanation-doctor-context');
assert.equal(line.className,'status-line');assert.equal(line.parentElement,field);
assert.ok(field.children.indexOf(line)<field.children.indexOf($('bear-query-sql')));
const before=calls.length;dbExplanationSync();assert.equal(calls.length,before);
assert.equal(line.textContent,'sales.raw · admin · LOCAL');assert.doesNotMatch(line.textContent,/finance/);
editDoctor('SELECT own_draft');editSQL('SELECT worksheet_draft');
assert.equal(line.textContent,'sales.raw · admin · LOCAL');
$('query-execution-role').value='bi_viewer';await $('query-execution-role').onchange();
assert.equal(line.textContent,'sales.raw · bi_viewer · LOCAL');
$('query-schema').value='schema';await $('query-schema').onchange();
assert.equal(line.textContent,'sales.marts · bi_viewer · LOCAL');
const schema=dbSchemasFor(dbCurrent()).find(s=>s.id==='schema');schema.sql_name='<img src=x onerror=attack()>';
dbControls();assert.equal(line.textContent,'sales.<img src=x onerror=attack()> · bi_viewer · LOCAL');
assert.equal(line.children.length,0);assert.equal($('bear-query-sql').value,'SELECT own_draft');
assert.equal($('sql').value,'SELECT worksheet_draft');assert.equal(explanationCalls().length,0);
''')

    def test_doctor_invalid_context_has_reason_and_localized_worksheet_next_step(self):
        self.markup_javascript(r'''
await boot();editDoctor('SELECT own_draft');
const line=$('explanation-doctor-context');
for(const locale of ['en','ja']){
 setLanguage(locale);
 for(const invalidate of [()=>{dbCurrent().schema_id='';},()=>{dbCurrent().binding_revision++;},()=>{dbMaximumRole='bi_viewer';}]){
  Object.assign(dbCurrent(),{schema_id:'schema',binding_revision:1});dbMaximumRole='admin';invalidate();
  const before=calls.length;dbControls();assert.equal(calls.length,before);
  assert.ok(dbReason());assert.equal(line.textContent,dbReason()+' '+t(dbExplanationContextStep));
  assert.ok(line.textContent.includes(locale==='en'?'Choose or confirm the database, schema and role in the SQL worksheet.':'SQL ワークシートでデータベース・スキーマ・ロールを選択または確認してください。'));
  assert.equal($('bear-query-run').disabled,true);assert.equal($('copilot-explain').disabled,true);
  assert.doesNotMatch(line.textContent,/database_id|schema_id|generation_id|binding_revision|finance/);
 }
 Object.assign(dbCurrent(),{schema_id:'schema',binding_revision:1});dbMaximumRole='admin';dbControls();
 assert.equal(line.textContent,'sales.marts · admin · LOCAL');
}
assert.equal($('bear-query-sql').value,'SELECT own_draft');assert.equal(explanationCalls().length,0);
''')

    def test_provenance_and_history_use_localized_human_states(self):
        self.javascript(r'''
await boot();
for(const [state,en,ja] of [['SUCCEEDED','Completed','完了'],['RUNNING','Running','実行中'],['QUEUED','Queued','待機中'],['FAILED','Failed','失敗'],['UNKNOWN','Outcome unknown','結果不明']]){
 setLanguage('en');editSQL('SELECT '+state);route=async()=>wrap(record({id:'hidden-record-'+state,state}));await $('copilot-explain').onclick();
 dbExplanation.history=[dbExplanation.record];
 for(const [locale,label] of [['en',en],['ja',ja]]){
  setLanguage(locale);
  for(const id of ['copilot-result','explanation-doctor-output']){
   assert.equal($(id).children[0].textContent,'SQL explanation');
   assert.equal($(id).children[1].textContent,t('Saved explanation (read only)')+' · '+label);
  }
  for(const prefix of ['copilot','doctor']){
   const option=$('explanation-'+prefix+'-saved').children[1];
   assert.equal(option.value,'0');assert.equal(option.textContent,`2026-10-31T01:00:00Z · ${label} · SELECT ${state}`);
   assert.doesNotMatch(option.textContent,/hidden-record-/);
  }
 }
}
''')

    def test_database_defaults_to_doctor_and_hides_legacy_target(self):
        tab_handlers = harness.SCRIPT[harness.SCRIPT.index('const bearTabs='):harness.SCRIPT.index('let auditEpoch=')]
        self.javascript(r'''
const bearList=new Node();
for(const bear of ['sre','query','data','catalog','policy']){
 const tab=$('bear-tab-'+bear);tab.tagName='BUTTON';tab.dataset.bear=bear;bearList.append(tab);
 $('bears').append($('bear-panel-'+bear));
}
$('bears').append(bearList);
const targetField=new Node();targetField.append($('bear-query-target'));$('bear-panel-query').append(targetField);
eval(''' + json.dumps(tab_handlers) + r''');
assert.equal($('bear-panel-sre').hidden,false);await boot();
assert.equal($('bear-panel-query').hidden,false);assert.equal($('bear-tab-query')['aria-selected'],'true');
assert.equal($('bear-panel-sre').hidden,true);assert.equal(targetField.hidden,true);
for(const bear of ['sre','data','catalog','policy'])assert.equal($('bear-tab-'+bear).disabled,true);
''')

    def test_copilot_async_202_get_preserves_editor_and_results(self):
        self.javascript(r'''
await boot();const sql="  SELECT n FROM marts.arbitrary WHERE n = 42 -- keep original\n";
editSQL(sql);queryResult={columns:['keep'],rows:[[27]]};const results=JSON.stringify(queryResult);
let accepted;
route=async(path,options)=>{
 if(options?.method==='POST'){
  const body=JSON.parse(options.body);assert.deepEqual(Object.keys(body).sort(),['context','language','sql']);
  assert.equal(body.sql,sql);assert.equal(body.language,'en');assert.deepEqual(body.context,dbContext(dbCurrent()));
  assert.equal(options.headers['X-Shirokuma-Role'],'admin');assert.ok(options.headers['Idempotency-Key']);
  accepted=record({state:'QUEUED',text:null,input:{sql,language:'en'}});return {http:202,body:wrap(accepted)};
 }
 assert.equal(new URL(path,'http://local').searchParams.get('context'),JSON.stringify(accepted.context));
 return wrap({...accepted,state:'SUCCEEDED',text:'Explanation only'});
};
await $('copilot-explain').onclick();assert.equal(dbExplanation.record.state,'QUEUED');
await $('explanation-copilot-recover').onclick();assert.equal(dbExplanation.record.state,'SUCCEEDED');
assert.equal(posts().length,1);assert.equal(explanationCalls().length,2);
assert.equal($('sql').value,sql);assert.equal(JSON.stringify(queryResult),results);
assert.match(visibleText($('copilot-result')),/Explanation only/);
assert.equal(dbExplanation.intents[0].source,'copilot');
''')

    def test_doctor_explicit_input_and_copy_only_from_worksheet(self):
        self.javascript(r'''
await boot();assert.equal($('bear-query-target').disabled,true);assert.equal($('bear-query-run').disabled,true);
editDoctor('SELECT SUM(amount) FROM raw.custom_sales');
route=async(path,options)=>wrap(record({input:{sql:JSON.parse(options.body).sql,language:'en'}}));
const draft=$('sql').value;await $('bear-query-run').onclick();
assert.equal(JSON.parse(posts()[0].options.body).sql,'SELECT SUM(amount) FROM raw.custom_sales');
assert.equal(dbExplanation.intents[0].source,'doctor');assert.equal($('sql').value,draft);
$('explanation-copy').onclick();assert.equal($('bear-query-sql').value,draft);assert.equal(dbExplanation.record,null);
assert.equal($('sql').value,draft);
''')

    def test_answer_first_concise_provenance_for_copilot_and_doctor_in_both_languages(self):
        for source in ['copilot', 'doctor']:
            for language in ['en', 'ja']:
                with self.subTest(source=source, language=language):
                    self.javascript('const source=' + json.dumps(source) + ', locale=' + json.dumps(language) + r'''
inventory[0].id='opaque-database-id';inventory[0].generation_id='opaque-generation-id';
schemas[0].id='opaque-schema-id';schemas[0].database_id=inventory[0].id;
await boot([{...input(),database_id:inventory[0].id,schema_id:schemas[0].id,generation_id:inventory[0].generation_id}]);
setLanguage(locale);
const sql="  SELECT * FROM raw.sales -- exact original <img src=x>\n";
const answer='Useful model answer\nSecond line\nThird line <script>attack()</script>';
if(source==='doctor')editDoctor(sql);else editSQL(sql);
const editor=$('sql').value,doctor=$('bear-query-sql').value;
queryResult={columns:['keep'],rows:[[23]]};const result=JSON.stringify(queryResult);
const relations=['TABLE','VIEW'].map(kind=>({schema:'raw',table:'source_'+kind,kind,columns:[
 {name:'omitted_column_one',type:'omitted_type_one'},{name:'omitted_column_two',type:'omitted_type_two'}]}));
route=async()=>wrap(record({id:'opaque-record-id',text:answer,input:{sql,language:locale},metadata:{captured_at:'2026-10-31T02:00:00Z',relations}}));
await $(source==='doctor'?'bear-query-run':'copilot-explain').onclick();
for(const id of ['copilot-result','explanation-doctor-output']){
 const node=$(id),text=visibleText(node);
 assert.equal(node.children[0].tagName,'PRE');assert.equal(node.children[0].textContent,answer);
 const origin=locale==='ja'?'保存された説明（表示のみ）':'Saved explanation (read only)';
 const state=locale==='ja'?'完了':'Completed';
 const status=locale==='ja'?'SQL は未実行 · 実行計画は未取得':'SQL not executed · Execution plan not collected';
 const sqlLabel=locale==='ja'?'元の SQL（表示のみ）':'Original SQL (read only)';
 for(const value of [origin,state,status,'sales.marts','admin','LOCAL','local-model','2026-10-31T02:00:00Z','2026-10-31T01:01:00Z',
  locale==='ja'?'メタデータ取得日時':'Captured at',locale==='ja'?'更新日時':'Updated at',sqlLabel,'raw.source_TABLE','raw.source_VIEW',locale==='ja'?'カラム数: 2':'2 columns']){
  assert.ok(text.includes(value),value);assert.ok(text.indexOf(answer)<text.indexOf(value),value);
 }
 const pres=node.descendants().filter(n=>n.tagName==='PRE');assert.equal(pres.length,2);
 assert.equal(pres[1].textContent,sql);assert.ok(text.indexOf(origin)<text.indexOf(sql));
 assert.equal(node.descendants().some(n=>['TEXTAREA','INPUT','BUTTON','SCRIPT','IMG'].includes(n.tagName)),false);
 assert.doesNotMatch(text,/opaque-|database_id|schema_id|warehouse_id|generation_id|binding_revision|grant_revision|"context"|"columns"|omitted_column|omitted_type|Historical explanation|過去の説明/);
}
assert.equal($('sql').value,editor);assert.equal($('bear-query-sql').value,doctor);assert.equal(JSON.stringify(queryResult),result);
''')

    def test_only_explicit_history_selection_uses_historical_origin(self):
        self.javascript(r'''
await boot();route=async()=>wrap(record());await $('copilot-explain').onclick();
for(const id of ['copilot-result','explanation-doctor-output'])assert.match(visibleText($(id)),/Saved explanation \(read only\)/);
const current=structuredClone(dbExplanation.record);
route=async path=>path.includes('/explanation-1?')?wrap(current):{context:current.context,explanations:[current]};
await dbExplanationHistory();await dbExplanationSelect(0);
for(const id of ['copilot-result','explanation-doctor-output']){
 assert.equal($(id).children[0].textContent,current.text);
 assert.match(visibleText($(id)),/Historical explanation \(read only\)/);
 assert.doesNotMatch(visibleText($(id)),/Saved explanation \(read only\)/);
}
setLanguage('ja');
for(const id of ['copilot-result','explanation-doctor-output'])assert.match(visibleText($(id)),/過去の説明（表示のみ）/);
// A guarded recovery of the current request returns to the saved/current label.
await $('explanation-doctor-recover').onclick();
for(const id of ['copilot-result','explanation-doctor-output'])assert.match(visibleText($(id)),/保存された説明（表示のみ）/);
editSQL('SELECT changed');assert.equal(dbExplanation.displayOrigin,null);
for(const id of ['copilot-result','explanation-doctor-output'])assert.equal($(id).hidden,true);
''')

    def test_structured_invalid_input_guidance_retains_original_recovery_intent(self):
        self.javascript(r'''
await boot();editDoctor('DELETE FROM raw.sales; SELECT 1');
route=async()=>({http:400,body:{error:{code:'AI_INPUT_INVALID',message:'PRIVATE_SERVER_TEXT',details:{secret:'PRIVATE_DETAILS'}}}});
await $('bear-query-run').onclick();const first=structuredClone(posts()[0]),intent=dbExplanationIntent();
assert.equal(intent.id,null);assert.equal(dbExplanation.record,null);
assert.equal(Object.isFrozen(intent.body),true);assert.equal(Object.isFrozen(intent.body.context),true);
assert.equal(Object.isFrozen(intent.capture),true);
for(const [locale,guidance] of [['en','SQL explanation supports a single SELECT or CTE. Review and edit the SQL.'],['ja','SQL の説明は単一の SELECT または CTE に対応しています。SQL を確認して編集してください。']]){
 setLanguage(locale);
 for(const id of ['copilot-result','explanation-doctor-output']){
  assert.equal(visibleText($(id)),guidance);
  assert.doesNotMatch(visibleText($(id)),/PRIVATE_|uncertain|stopped|停止/);
 }
 assert.equal($('explanation-doctor-recover').disabled,false);
 assert.equal($('bear-query-run').disabled,true);assert.equal(posts().length,1);
}
const saved=JSON.parse(storage.get(dbExplanationKey)).at(-1);
assert.equal(saved.key,first.options.headers['Idempotency-Key']);assert.equal(JSON.stringify(saved.body),first.options.body);
route=async()=>{throw Error('AI_INPUT_INVALID PRIVATE_TRANSPORT_TEXT');};
await $('explanation-doctor-recover').onclick();
assert.equal(posts().length,2);assert.equal(posts()[1].options.body,first.options.body);
assert.equal(posts()[1].options.headers['Idempotency-Key'],first.options.headers['Idempotency-Key']);
assert.equal(dbExplanationIntent(),intent);assert.equal(intent.id,null);
setLanguage('en');assert.match(visibleText($('copilot-result')),/Explanation outcome uncertain/);
assert.doesNotMatch(visibleText($('copilot-result')),/PRIVATE_|single SELECT/);
route=async()=>wrap(record({input:{sql:intent.body.sql,language:intent.body.language}}));
await $('explanation-doctor-recover').onclick();
assert.equal(posts().length,3);assert.equal(posts()[2].options.body,first.options.body);
assert.equal(posts()[2].options.headers['Idempotency-Key'],first.options.headers['Idempotency-Key']);
assert.equal(dbExplanationIntent(),intent);assert.equal($('bear-query-sql').value,'DELETE FROM raw.sales; SELECT 1');
// The same structured error on an accepted-id read retains conservative read handling.
route=async()=>({http:400,body:{error:{code:'AI_INPUT_INVALID',message:'PRIVATE_READ_TEXT'}}});
await $('explanation-doctor-recover').onclick();assert.equal(posts().length,3);
assert.match(visibleText($('copilot-result')),/Explanation unavailable/);
assert.doesNotMatch(visibleText($('copilot-result')),/PRIVATE_|single SELECT/);
''')

    def test_uncertain_ack_replays_exact_key_body_without_new_request(self):
        self.javascript(r'''
await boot();editDoctor(' SELECT 8 -- original ');
route=async()=>{throw Error('lost ACK with private provider details');};
await $('bear-query-run').onclick();const first=structuredClone(posts()[0]);
assert.equal(dbExplanation.record,null);assert.match(visibleText($('copilot-result')),/uncertain/);
assert.doesNotMatch(visibleText($('copilot-result')),/private provider/);
assert.equal($('copilot-explain').disabled,true);assert.equal($('bear-query-run').disabled,true);
await $('copilot-explain').onclick();assert.equal(posts().length,1);
route=async(path,options)=>wrap(record({input:{sql:JSON.parse(options.body).sql,language:'en'}}));
await $('explanation-doctor-recover').onclick();
assert.equal(posts().length,2);assert.equal(posts()[1].options.body,first.options.body);
assert.equal(posts()[1].options.headers['Idempotency-Key'],first.options.headers['Idempotency-Key']);
''')

    def test_reload_recovery_is_explicit_and_keeps_original_intent(self):
        self.javascript(r'''
await boot();editDoctor('SELECT 93');route=async()=>{throw Error('lost');};
await $('bear-query-run').onclick();const original=structuredClone(posts()[0]);
dbExplanation.intents=[];dbExplanation.record=null;dbExplanation.history=[];
$('bear-query-sql').value='';dbExplanationInstall();
assert.equal($('bear-query-sql').value,'SELECT 93');assert.equal(posts().length,1);
route=async(path,options)=>wrap(record({input:JSON.parse(options.body)}));
await $('explanation-doctor-recover').onclick();
assert.equal(posts()[1].options.body,original.options.body);
assert.equal(posts()[1].options.headers['Idempotency-Key'],original.options.headers['Idempotency-Key']);
''')

    def test_storage_failure_blocks_initial_post_and_recovers_original_intent(self):
        for failure in ['write', 'read', 'mismatch']:
            with self.subTest(failure=failure):
                self.javascript('const failure=' + json.dumps(failure) + r''';
await boot();editDoctor(' SELECT 193 -- retain exact original\n');
const set=localStorage.setItem,get=localStorage.getItem;
localStorage.setItem=(key,value)=>{
 if(key===dbExplanationKey){
  if(failure==='write')throw Error('storage denied');
  if(failure==='mismatch')return; // Silent loss, not an exception.
 }
 set(key,value);
};
localStorage.getItem=key=>{if(key===dbExplanationKey&&failure==='read')throw Error('read denied');return get(key);};
await $('bear-query-run').onclick();
const original=dbExplanation.intents[0],key=original.key,body=JSON.stringify(original.body);
assert.ok(Object.isFrozen(original.body));assert.equal(explanationCalls().length,0);
assert.equal(dbExplanation.busy,false);assert.equal($('explanation-doctor-recover').disabled,false);
assert.match(visibleText($('copilot-result')),/No request was sent/);
setLanguage('ja');assert.match(visibleText($('copilot-result')),/要求は送信していません/);
await $('explanation-doctor-recover').onclick();assert.equal(explanationCalls().length,0);
assert.equal(dbExplanation.busy,false);assert.equal(dbExplanation.intents.length,1);
localStorage.setItem=set;localStorage.getItem=get;
await tick();assert.equal(explanationCalls().length,0);
route=async(path,options)=>{
 const saved=JSON.parse(get(dbExplanationKey)).find(item=>item.key===key);
 assert.equal(JSON.stringify(saved.body),body);assert.equal(options.body,body);
 assert.equal(options.headers['Idempotency-Key'],key);
 return wrap(record({input:JSON.parse(options.body)}));
};
await $('explanation-doctor-recover').onclick();
assert.equal(posts().length,1);assert.equal(dbExplanation.intents[0],original);
assert.equal(dbExplanation.record.state,'SUCCEEDED');assert.equal(dbExplanation.busy,false);
''')

    def test_uncertain_ack_replay_requires_storage_again(self):
        self.javascript(r'''
await boot();route=async()=>{throw Error('lost ACK');};
await $('copilot-explain').onclick();const first=structuredClone(posts()[0]);
const set=localStorage.setItem;
localStorage.setItem=(key,value)=>{if(key===dbExplanationKey)throw Error('full');set(key,value);};
await $('explanation-copilot-recover').onclick();
assert.equal(posts().length,1);assert.equal(dbExplanation.busy,false);
assert.match(visibleText($('copilot-result')),/No request was sent/);
localStorage.setItem=set;await tick();assert.equal(posts().length,1);
route=async()=>wrap(record());await $('explanation-copilot-recover').onclick();
assert.equal(posts().length,2);assert.equal(posts()[1].options.body,first.options.body);
assert.equal(posts()[1].options.headers['Idempotency-Key'],first.options.headers['Idempotency-Key']);
assert.equal(dbExplanation.busy,false);
''')

    def test_accepted_id_persist_failure_keeps_response_get_and_durable_original(self):
        self.javascript(r'''
await boot();const set=localStorage.setItem,get=localStorage.getItem;
route=async()=>{
 localStorage.setItem=(key,value)=>{if(key===dbExplanationKey)throw Error('full');set(key,value);};
 return {http:202,body:wrap(record({state:'RUNNING',text:null}))};
};
await $('copilot-explain').onclick();const first=structuredClone(posts()[0]);
assert.equal(dbExplanation.record.state,'RUNNING');assert.equal(dbExplanation.notice,'');
assert.equal(dbExplanation.busy,false);
const saved=JSON.parse(get(dbExplanationKey))[0];
assert.equal(saved.id,null);assert.equal(saved.key,first.options.headers['Idempotency-Key']);
assert.equal(JSON.stringify(saved.body),first.options.body);
localStorage.getItem=()=>{throw Error('read denied');};
route=async()=>wrap(record());await $('explanation-copilot-recover').onclick();
assert.equal(posts().length,1);assert.match(explanationCalls()[1].path,/explanation-1\?/);
assert.equal(dbExplanation.record.state,'SUCCEEDED');assert.equal(dbExplanation.notice,'');
assert.equal(dbExplanation.busy,false);
localStorage.getItem=get;localStorage.setItem=set;
dbExplanation.intents=[];dbExplanation.record=null;dbExplanationInstall();
await tick();assert.equal(posts().length,1);
await $('explanation-copilot-recover').onclick();
assert.equal(posts().length,2);assert.equal(posts()[1].options.body,first.options.body);
assert.equal(posts()[1].options.headers['Idempotency-Key'],first.options.headers['Idempotency-Key']);
assert.equal(dbExplanation.busy,false);
''')

    def test_saved_history_is_readonly_and_selection_never_posts(self):
        self.javascript(r'''
await boot();editDoctor('SELECT draft');const editor=$('sql').value,doctor=$('bear-query-sql').value;
queryResult={rows:[[10]],columns:['original']};const result=JSON.stringify(queryResult);
const past=record({id:'past-explanation',input:{sql:'SELECT past FROM raw.history',language:'ja'},model:{provider:'local-openai',name:'captured-model'}});
route=async path=>path.includes('/past-explanation?')?wrap(past):{context:past.context,explanations:[past]};
await $('explanation-copilot-history').onclick();assert.equal(dbExplanation.history.length,1);
$('explanation-doctor-saved').value='0';await $('explanation-doctor-saved').onchange();
assert.equal(posts().length,0);assert.equal(explanationCalls().length,2);
assert.equal($('sql').value,editor);assert.equal($('bear-query-sql').value,doctor);assert.equal(JSON.stringify(queryResult),result);
assert.match(visibleText($('copilot-result')),/captured-model/);assert.match(visibleText($('copilot-result')),/SELECT past/);
assert.match(visibleText($('copilot-result')),/2026-10-31/);
''')

    def test_stale_post_responses_cannot_repaint_after_scope_changes(self):
        self.javascript(r'''
await boot([input(),input('b')]);
const changes=[()=>editSQL('SELECT 2'),()=>editDoctor('SELECT 3'),()=>{worksheetId='b';},
 ()=>{currentRole='bi_viewer';},()=>{dbPrincipal='other';},()=>{dbEpoch++;},
 ()=>{dbAuthorizationVersion++;},()=>{dbMaximumRole='bi_viewer';},
 ()=>{dbRoleAuthorizationVersions.set(dbTabRole(),dbRoleVersion(dbTabRole())+1);},
 ()=>{dbCurrent().schema_id='raw';},()=>{dbCurrent().binding_revision++;}];
for(const change of changes){
 worksheetId='a';currentRole='admin';dbPrincipal=JSON.stringify(['owner',true]);dbMaximumRole='admin';
 Object.assign(dbCurrent(),{schema_id:'schema',binding_revision:1});editSQL('SELECT '+Math.random());editDoctor('');await dbRefreshContext();
 const wait=deferred(),accepted=record();route=()=>wait.promise;
 const pending=$('copilot-explain').onclick();await tick();assert.equal(dbExplanation.busy,true);
 change();dbControls();assert.equal(dbExplanation.record,null);assert.equal(dbExplanation.history.length,0);
 wait.resolve(wrap(accepted));await pending;
 assert.equal(dbExplanation.record,null);assert.equal($('copilot-result').hidden,true);
 assert.equal($('explanation-doctor-output').hidden,true);
}
''')

    def test_full9_changes_purge_response_history_and_recovery(self):
        self.javascript(r'''
await boot();
for(const key of Object.keys(dbContext(dbCurrent()))){
 const old=dbContext;let context={...old(dbCurrent())};dbContext=()=>context;
 route=async()=>wrap(record());await $('copilot-explain').onclick();
 dbExplanation.history=[record()];assert.ok(dbExplanation.record);
 context={...context,[key]:String(context[key])+'-changed'};dbControls();
 assert.equal(dbExplanation.record,null,key);assert.deepEqual(dbExplanation.history,[],key);
 assert.equal($('explanation-copilot-recover').disabled,true,key);
 dbContext=old;editSQL('SELECT '+Math.random());
}
''')

    def test_delayed_list_and_get_are_discarded_after_draft_changes(self):
        self.javascript(r'''
await boot();
for(const operation of ['list','get']){
 const wait=deferred(),past=record({id:'past'});route=()=>wait.promise;
 dbExplanation.history=[past];dbExplanationSync();
 const pending=operation==='list'?$('explanation-doctor-history').onclick():dbExplanationSelect(0);
 await tick();editDoctor('SELECT '+operation);wait.resolve(operation==='list'?{context:past.context,explanations:[past]}:wrap(past));await pending;
 assert.equal(dbExplanation.record,null);assert.deepEqual(dbExplanation.history,[]);assert.equal($('copilot-result').hidden,true);
}
''')

    def test_delayed_metadata_revalidation_cannot_restore_old_output(self):
        self.javascript(r'''
await boot();route=async()=>wrap(record());await $('copilot-explain').onclick();
const wait=deferred();schemasRoute=()=>wait.promise;
const pending=dbRefreshContext();await tick();currentRole='bi_viewer';dbApplyRole();
wait.resolve({items:schemas});await pending;
assert.equal(dbExplanation.record,null);assert.deepEqual(dbExplanation.history,[]);
assert.equal($('copilot-result').hidden,true);assert.equal($('copilot-explain').disabled,true);
''')

    def test_busy_survives_periodic_refresh_and_blocks_both_entries(self):
        self.javascript(r'''
await boot();editDoctor('SELECT 2');const wait=deferred(),accepted=record();route=()=>wait.promise;
const pending=$('copilot-explain').onclick();await tick();
for(let i=0;i<3;i++){dbControls();await dbRefreshAccess();assert.equal(dbExplanation.busy,true);
 assert.equal($('copilot-explain').disabled,true);assert.equal($('bear-query-run').disabled,true);
 assert.equal($('explanation-doctor-history').disabled,true);}
await $('bear-query-run').onclick();await dbExplanationHistory();assert.equal(posts().length,1);
wait.resolve({http:202,body:wrap(accepted)});await pending;
assert.equal(dbExplanation.busy,false);assert.equal($('explanation-doctor-recover').disabled,false);
''')

    def test_periodic_poll_uses_get_only_for_current_accepted_pending_record(self):
        self.javascript(r'''
await boot();const accepted=record({state:'RUNNING',text:null});
route=async(path,options)=>options?.method==='POST'?{http:202,body:wrap(accepted)}:wrap({...accepted,state:'SUCCEEDED',text:'Done'});
await $('copilot-explain').onclick();await periodicRefresh();
assert.equal(dbExplanation.record.state,'SUCCEEDED');assert.equal(posts().length,1);assert.equal(explanationCalls().length,2);
editSQL('SELECT changed');await periodicRefresh();assert.equal(explanationCalls().length,2);
''')

    def test_old_response_does_not_clear_new_request_busy_or_output(self):
        self.javascript(r'''
await boot();const a=deferred(),b=deferred(),old=record();route=()=>a.promise;
const first=$('copilot-explain').onclick();await tick();editSQL('SELECT new');
const fresh=record();route=()=>b.promise;const second=$('copilot-explain').onclick();await tick();
a.resolve(wrap(old));await first;assert.equal(dbExplanation.busy,true);assert.equal(dbExplanation.record,null);
b.resolve(wrap(fresh));await second;assert.equal(dbExplanation.record.input.sql,'SELECT new');assert.equal(dbExplanation.busy,false);
assert.notEqual(posts()[0].options.headers['Idempotency-Key'],posts()[1].options.headers['Idempotency-Key']);
''')

    def test_language_and_model_text_remain_safe_and_never_enable_writes(self):
        self.javascript(r'''
await boot();setLanguage('ja');editDoctor("SELECT '<img src=x onerror=attack()>'");
const evil='<script>attack()</script><button id="publish">Publish</button>';
route=async(path,options)=>wrap(record({text:evil,model:{provider:'local-openai',name:evil},input:{sql:JSON.parse(options.body).sql,language:'ja'}}));
await $('bear-query-run').onclick();assert.equal(JSON.parse(posts()[0].options.body).language,'ja');
for(const id of ['copilot-result','explanation-doctor-output']){
 assert.match(visibleText($(id)),/<script>attack/);assert.equal($(id).descendants().some(n=>['SCRIPT','BUTTON','IMG'].includes(n.tagName)),false);
}
assert.equal($('copilot-explain').textContent,'新しい説明を要求');setLanguage('en');assert.equal($('copilot-explain').textContent,'Request new explanation');
assert.match(visibleText($('copilot-result')),/<script>attack/);
for(const id of ['copilot-generate','copilot-stage','copilot-publish','ai-settings-button'])assert.equal($(id).disabled,true,id);
''')

    def test_malformed_wrong_context_or_input_reply_is_not_displayed(self):
        self.javascript(r'''
await boot();
for(const change of [r=>({...r,context:{...r.context,database_id:'other'}}),r=>({...r,input:{sql:'different',language:'en'}}),r=>({...r,plan_status:'COLLECTED'}),r=>({...r,text:{html:'evil'}})]){
 editSQL('SELECT '+Math.random());route=async()=>wrap(change(record()));await $('copilot-explain').onclick();
 assert.equal(dbExplanation.record,null);assert.match(visibleText($('copilot-result')),/uncertain/);
}
''')

    def test_actor_and_authenticated_principal_are_required_on_every_response_path(self):
        self.javascript(r'''
await boot();
const cases=[
 {actor:'another-owner'}, {actor:undefined}, {actor:JSON.stringify(['owner',true])},
 {principal:'owner'}, {principal:'{'}, {principal:JSON.stringify(['owner',false])},
 {principal:JSON.stringify(['',true])}, {principal:JSON.stringify({username:'owner',authenticated:true})},
 {principal:JSON.stringify(['owner',true,'extra'])}, {principal:''}
];
for(const operation of ['post','get','list'])for(const invalid of cases){
 dbPrincipal=invalid.principal??JSON.stringify(['owner',true]);editSQL('SELECT '+Math.random());dbControls();
 const r=record({text:'CROSS_ACTOR_PRIVATE_TEXT',...(Object.hasOwn(invalid,'actor')?{actor:invalid.actor}:{})});
 route=async()=>operation==='list'?{context:r.context,explanations:[r]}:wrap(r);
 if(operation==='post')await $('copilot-explain').onclick();
 if(operation==='get'){dbExplanation.history=[record()];await dbExplanationSelect(0);}
 if(operation==='list')await dbExplanationHistory();
 assert.equal(dbExplanation.record,null,operation+JSON.stringify(invalid));
 if(operation==='list')assert.deepEqual(dbExplanation.history,[]);
 for(const id of ['copilot-result','explanation-doctor-output'])assert.doesNotMatch(visibleText($(id)),/CROSS_ACTOR_PRIVATE_TEXT|another-owner/);
}
''')

    def test_context_requires_exact_full9_keys_for_wrapper_record_and_expected(self):
        self.javascript(r'''
await boot();
const mutations=[c=>{delete c.schema_id;},c=>{c.extra='private';},c=>Object.assign([],c)];
for(const operation of ['post','get','list'])for(const target of ['wrapper','record'])for(const mutate of mutations){
 editSQL('SELECT '+Math.random());const r=record(),data=operation==='list'?{context:structuredClone(r.context),explanations:[r]}:wrap(r);
 const original=target==='wrapper'?data.context:r.context,changed=mutate(original);
 if(changed){if(target==='wrapper')data.context=changed;else r.context=changed;}
 route=async()=>data;
 if(operation==='post')await $('copilot-explain').onclick();
 if(operation==='get'){dbExplanation.history=[record()];await dbExplanationSelect(0);}
 if(operation==='list')await dbExplanationHistory();
 assert.equal(dbExplanation.record,null,operation+target);
 if(operation==='list')assert.deepEqual(dbExplanation.history,[]);
}
const context=dbContext(dbCurrent());
for(const mutate of mutations){const bad=structuredClone(context);const changed=mutate(bad)||bad;
 assert.equal(dbExplanationSameContext(context,changed),false);
 assert.equal(dbExplanationSameContext(changed,context),false);
}
''')

    def test_only_public_fields_survive_post_get_and_history_projection(self):
        self.javascript(r'''
await boot();
for(const operation of ['post','get','list']){
 editSQL('SELECT '+Math.random());
 const secret={credential_ref:'SECRET_CREDENTIAL',provider_url:'SECRET_URL',password:'SECRET_PASSWORD',config:{key:'SECRET_CONFIG'},ref:'SECRET_REF'};
 const relations=['TABLE','VIEW'].map(kind=>({schema:'raw',table:'source_'+kind,kind,...secret,columns:[{name:'amount',type:'decimal(12,2)',...secret}]}));
 const r=record({...secret,input:{sql:$('sql').value,language:'en',...secret},model:{provider:'local-openai',name:'captured-model',...secret},metadata:{captured_at:'2026-10-31T01:00:00Z',relations,...secret}});
 route=async()=>operation==='list'?{context:r.context,explanations:[r]}:wrap(r);
 if(operation==='post')await $('copilot-explain').onclick();
 if(operation==='get'){dbExplanation.history=[record()];await dbExplanationSelect(0);}
 if(operation==='list')await dbExplanationHistory();
 const normalized=operation==='list'?dbExplanation.history[0]:dbExplanation.record;
 assert.ok(normalized);assert.doesNotMatch(JSON.stringify(normalized),/SECRET_|credential_ref|provider_url|password|config/);
 if(operation==='list'){route=async()=>wrap(r);await dbExplanationSelect(0);}
 for(const id of ['copilot-result','explanation-doctor-output']){
  const text=visibleText($(id));assert.doesNotMatch(text,/SECRET_|credential_ref|provider_url|password|config/);
  for(const value of ['raw.source_TABLE','raw.source_VIEW','1 columns','captured-model','2026-10-31'])assert.ok(text.includes(value),value);
 }
}
''')

    def test_malformed_known_public_fields_are_rejected_on_every_response_path(self):
        self.javascript(r'''
await boot();
const relation={schema:'raw',table:'sales',kind:'TABLE',columns:[{name:'amount',type:'bigint'}]};
const invalid=[
 {id:''},{actor:''},{state:'DONE'},{sql_status:'EXECUTED'},{error_code:{}},{created_at:null},{updated_at:[]},
 {model:null},{model:[]},{model:{provider:'remote',name:'model'}},{model:{provider:'local-openai',name:null}},
 {metadata:null},{metadata:[]},{metadata:{captured_at:null,relations:[]}},
 ...[null,{},[null],[{...relation,kind:'MATERIALIZED'}],[{...relation,schema:null}],[{...relation,table:''}],
 [{...relation,columns:null}],[{...relation,columns:{}}],[{...relation,columns:[null]}],
 [{...relation,columns:[{name:'x',type:{private:'secret'}}]}],[{...relation,columns:[{name:null,type:'int'}]}]]
 .map(relations=>({metadata:{captured_at:'2026-10-31',relations}}))
];
for(const operation of ['post','get','list'])for(const extra of invalid){
 editSQL('SELECT '+Math.random());const r=record({...extra,text:'MALFORMED_PRIVATE_TEXT'});
 route=async()=>operation==='list'?{context:r.context,explanations:[r]}:wrap(r);
 if(operation==='post')await $('copilot-explain').onclick();
 if(operation==='get'){dbExplanation.history=[record()];await dbExplanationSelect(0);}
 if(operation==='list')await dbExplanationHistory();
 assert.equal(dbExplanation.record,null,operation+JSON.stringify(extra));
 if(operation==='list')assert.deepEqual(dbExplanation.history,[]);
 assert.doesNotMatch(visibleText($('copilot-result')),/MALFORMED_PRIVATE_TEXT/);
}
''')

    def test_actor_and_worksheet_recovery_never_cross_scopes(self):
        self.javascript(r'''
await boot([input(),input('b')]);route=async()=>{throw Error('lost');};await $('copilot-explain').onclick();
const count=posts().length;
for(const change of [()=>{dbPrincipal='other-owner';},()=>{dbPrincipal=JSON.stringify(['owner',true]);worksheetId='b';},()=>{worksheetId='a';currentRole='bi_viewer';}]){
 change();dbControls();assert.equal($('explanation-doctor-recover').disabled,true);
 await $('explanation-doctor-recover').onclick();assert.equal(posts().length,count);
}
''')

    def test_unsupported_legacy_routes_and_actions_remain_fenced(self):
        self.javascript(r'''
await boot();const before=calls.length;
for(const body of [{bear:'query',target:'latest'},{bear:'query',target:'query-id'},{bear:'sre'},{bear:'data',action:'generate'},{bear:'data',action:'publish'},{bear:'catalog'},{bear:'policy'}])await assert.rejects(api('/api/bears/run',body));
assert.equal(calls.length,before);
for(const id of ['copilot-generate','copilot-stage','copilot-publish','ai-settings-button','wake','chill'])assert.equal($(id).disabled,true,id);
assert.match($('database-bears-notice').textContent,/SQL explanation and execution plan explanation for TABLEs and supported INVOKER VIEWs/);
setLanguage('ja');assert.match($('database-bears-notice').textContent,/SQL の説明と、TABLE・対応する INVOKER VIEW の実行計画の説明/);
''')

    def test_no_global_history_or_explanation_calls_at_boot(self):
        self.javascript(r'''
await boot(Array.from({length:13},(_,i)=>({...input(String(i)),sql:'SELECT '+i})));
assert.equal(worksheets.length,13);assert.equal(explanationCalls().length,0);
assert.equal($('bear-query-target').disabled,true);assert.equal($('copilot-explain').disabled,false);
assert.equal($('bear-query-run').disabled,true);
''')

    def test_context_key_order_is_not_a_wire_dependency(self):
        self.javascript(r'''
await boot();const r=record();r.context=Object.fromEntries(Object.entries(r.context).reverse());
route=async()=>wrap(r);await $('copilot-explain').onclick();assert.equal(dbExplanation.record.id,r.id);
''')

    def test_schema_permission_loss_hides_completed_and_delayed_explanations(self):
        self.javascript(r'''
await boot();route=async()=>wrap(record());await $('copilot-explain').onclick();dbExplanation.history=[record()];
const schema=dbSchemasFor(dbCurrent()).find(s=>s.id===dbCurrent().schema_id);
schema.capabilities.query=false;dbControls();
assert.equal(dbExplanation.record,null);assert.deepEqual(dbExplanation.history,[]);assert.equal($('copilot-result').hidden,true);
schema.capabilities.query=true;editSQL('SELECT later');dbControls();const wait=deferred(),accepted=record();route=()=>wait.promise;
const pending=$('copilot-explain').onclick();await tick();schema.capabilities.query=false;dbControls();
wait.resolve(wrap(accepted));await pending;assert.equal(dbExplanation.record,null);assert.equal($('copilot-explain').disabled,true);
''')

    def test_intent_body_source_and_key_are_immutable(self):
        self.javascript(r'''
await boot();route=async()=>{throw Error('lost');};await $('copilot-explain').onclick();
const intent=dbExplanation.intents[0],body=JSON.stringify(intent.body),key=intent.key;
assert.equal(Object.isFrozen(intent.body),true);assert.equal(Object.isFrozen(intent.body.context),true);
Reflect.set(intent.body,'sql','SELECT replacement');Reflect.set(intent.body.context,'database_id','other');
Reflect.set(intent,'source','doctor');Reflect.set(intent,'key','another');
assert.equal(JSON.stringify(intent.body),body);assert.equal(intent.key,key);assert.equal(intent.source,'copilot');
''')

    def terminal_new_request(self, state):
        self.javascript('const terminalState=' + json.dumps(state) + r''';
await boot();editDoctor('SELECT same_sql');const sql=$('sql').value;
let serial=0;
route=async(path,options)=>{
 if(options?.method==='POST'){
  serial++;const body=JSON.parse(options.body),saved=JSON.parse(storage.get(dbExplanationKey));
  assert.equal(saved.at(-1).key,options.headers['Idempotency-Key']);
  assert.deepEqual(saved.at(-1).body,body);
  return wrap(record({id:'request-'+serial,state:serial===1?terminalState:'QUEUED',text:null,input:{sql:body.sql,language:body.language}}));
 }
 throw Error('Unexpected automatic read');
};
await $('bear-query-run').onclick();const first=structuredClone(posts()[0]);
assert.equal($('bear-query-run').disabled,false);assert.equal($('bear-query-run').textContent,'Request new explanation');
await periodicRefresh();dbControls();assert.equal(posts().length,1);
await $('bear-query-run').onclick();assert.equal(posts().length,2);
assert.equal(posts()[1].options.body,first.options.body);
assert.notEqual(posts()[1].options.headers['Idempotency-Key'],first.options.headers['Idempotency-Key']);
assert.equal(dbExplanation.intents.length,2);assert.equal(dbExplanation.intents[0].id,'request-1');
assert.equal(dbExplanationIntent(),dbExplanation.intents[1]);assert.equal(dbExplanation.record.id,'request-2');
assert.equal($('bear-query-run').disabled,true);assert.equal($('sql').value,sql);
route=async path=>{assert.ok(path.includes('/request-2?'));return wrap(dbExplanation.history[0]);};
dbExplanation.history=[record({id:'request-2',state:'RUNNING',text:null,input:{sql:'SELECT same_sql',language:'en'}})];
await $('explanation-doctor-recover').onclick();assert.equal(posts().length,2);assert.equal(dbExplanation.record.state,'RUNNING');
''')

    def test_failed_allows_explicit_same_sql_new_request(self):
        self.terminal_new_request('FAILED')

    def test_succeeded_allows_explicit_same_sql_new_request(self):
        self.terminal_new_request('SUCCEEDED')

    def test_nonterminal_states_fence_new_requests_and_recover_latest_original(self):
        self.javascript(r'''
await boot();
for(const state of ['QUEUED','RUNNING','UNKNOWN']){
 editSQL('SELECT '+state);const accepted=record({id:'id-'+state,state,text:null});
 route=async(path,options)=>options?.method==='POST'?{http:202,body:wrap(accepted)}:wrap(accepted);
 await $('copilot-explain').onclick();const original=structuredClone(posts().at(-1)),count=posts().length;
 for(const id of ['copilot-explain','bear-query-run']){assert.equal($(id).disabled,true);await $(id).onclick();}
 assert.equal(posts().length,count);
 await $('explanation-copilot-recover').onclick();assert.equal(posts().length,count);
 assert.ok(explanationCalls().at(-1).path.includes('/id-'+state+'?'));
 assert.equal(dbExplanationIntent().key,original.options.headers['Idempotency-Key']);
 assert.equal(JSON.stringify(dbExplanationIntent().body),original.options.body);
}
''')

    def test_older_terminal_history_does_not_unlock_newest_pending_intent(self):
        self.javascript(r'''
await boot();const old=record({id:'old'}),pending=record({id:'new',state:'UNKNOWN',text:null});
route=async()=>wrap(old);await $('copilot-explain').onclick();
route=async()=>wrap(pending);await $('copilot-explain').onclick();const count=posts().length;
route=async path=>path.includes('/old?')?wrap(old):{context:old.context,explanations:[old,pending]};
await dbExplanationHistory();await dbExplanationSelect(0);
assert.equal(dbExplanation.record.id,'old');assert.equal(dbExplanationIntent().id,'new');
assert.equal($('copilot-explain').disabled,true);await $('copilot-explain').onclick();assert.equal(posts().length,count);
route=async path=>{assert.ok(path.includes('/new?'));return wrap(pending);};
await $('explanation-copilot-recover').onclick();assert.equal(dbExplanation.record.id,'new');assert.equal(posts().length,count);
''')

    def test_reload_terminal_hints_require_guarded_get_revalidation(self):
        self.javascript(r'''
await boot();route=async()=>wrap(record());await $('copilot-explain').onclick();
const key=dbExplanationIntent().key;
// Even an injected persisted terminal hint is not server evidence.
const saved=JSON.parse(storage.get(dbExplanationKey));saved[0].state='SUCCEEDED';storage.set(dbExplanationKey,JSON.stringify(saved));
dbExplanationInstall();assert.equal(dbExplanation.record,null);assert.equal(posts().length,1);
assert.equal($('copilot-explain').disabled,true);await $('copilot-explain').onclick();assert.equal(posts().length,1);
for(const state of ['UNKNOWN','RUNNING','QUEUED','FAILED']){
 route=async(path,options)=>{assert.notEqual(options?.method,'POST');return wrap(record({state,text:null}));};
 await $('explanation-copilot-recover').onclick();assert.equal(posts().length,1);
 assert.equal(dbExplanationIntent().key,key);assert.equal($('copilot-explain').disabled,state!=='FAILED');
}
dbExplanationInstall();assert.equal($('copilot-explain').disabled,true);
route=async()=>wrap(record());await $('explanation-copilot-recover').onclick();assert.equal($('copilot-explain').disabled,false);
assert.equal(posts().length,1);
''')

    def test_older_terminal_cannot_unlock_new_uncertain_ack_recovery(self):
        self.javascript(r'''
await boot();const old=record({id:'old'});route=async()=>wrap(old);await $('copilot-explain').onclick();
route=async()=>{throw Error('lost new ACK');};await $('copilot-explain').onclick();
const uncertain=structuredClone(posts()[1]);assert.equal(dbExplanationIntent().id,null);
route=async path=>path.includes('/old?')?wrap(old):{context:old.context,explanations:[old]};
await dbExplanationHistory();await dbExplanationSelect(0);
assert.equal($('copilot-explain').disabled,true);await $('copilot-explain').onclick();assert.equal(posts().length,2);
route=async()=>wrap(record({id:'new',state:'QUEUED',text:null}));
await $('explanation-copilot-recover').onclick();assert.equal(posts().length,3);
assert.equal(posts()[2].options.body,uncertain.options.body);
assert.equal(posts()[2].options.headers['Idempotency-Key'],uncertain.options.headers['Idempotency-Key']);
assert.equal(dbExplanationIntent().id,'new');assert.equal($('copilot-explain').disabled,true);
''')

    def test_stale_terminal_read_cannot_unlock_or_replace_newer_pending(self):
        self.javascript(r'''
await boot();const old=record({id:'old'});route=async()=>wrap(old);await $('copilot-explain').onclick();
const wait=deferred();route=()=>wait.promise;
const read=$('explanation-copilot-recover').onclick();await tick();
editSQL('SELECT temporary');editSQL('SELECT 1');
// Returning to the original capture still requires revalidation; start it explicitly.
const newer=record({id:'new',state:'QUEUED',text:null});
route=async()=>wrap(old);await $('explanation-copilot-recover').onclick();
route=async()=>wrap(newer);await $('copilot-explain').onclick();
assert.equal(dbExplanation.record.id,'new');wait.resolve(wrap(old));await read;
assert.equal(dbExplanation.record.id,'new');assert.equal(dbExplanationIntent().id,'new');
assert.equal($('copilot-explain').disabled,true);assert.equal(posts().length,2);
''')

    def test_auth_epoch_hides_recovery_until_explicit_authorized_history_read(self):
        self.javascript(r'''
await boot();route=async()=>{throw Error('lost');};await $('copilot-explain').onclick();
const original=structuredClone(posts()[0]);dbEpoch++;dbControls();
assert.equal($('explanation-doctor-recover').disabled,true);assert.equal($('copilot-result').hidden,true);
await $('explanation-doctor-recover').onclick();assert.equal(posts().length,1);
await dbRefreshContext();route=async()=>({context:dbContext(dbCurrent()),explanations:[]});
await $('explanation-doctor-history').onclick();assert.equal(posts().length,1);
assert.equal($('explanation-doctor-recover').disabled,false);
route=async(path,options)=>wrap(record({input:{sql:JSON.parse(options.body).sql,language:'en'}}));
await $('explanation-doctor-recover').onclick();assert.equal(posts()[1].options.body,original.options.body);
assert.equal(posts()[1].options.headers['Idempotency-Key'],original.options.headers['Idempotency-Key']);
''')


if __name__ == '__main__':
    unittest.main()
