"""Draft-only Database Copilot, exercising production handlers in the Node adapter."""
import json
import unittest

import test_workbench_database_ui as harness
from test_workbench_bears_database_ui import COMMON


PROPOSAL = r'''
const proposalCalls=()=>calls.filter(c=>c.path.startsWith(dbProposalEndpoint));
const proposalPosts=()=>proposalCalls().filter(c=>c.path===dbProposalEndpoint&&c.options?.method==='POST');
const proposalObject=(i=0,schema='schema',extra={})=>({id:'object-'+i,physical_name:'注文 '+i,display_name:'display '+i,kind:i%2?'VIEW':'TABLE',ref:{database_id:'db',schema_id:schema,object_id:'object-'+i,incarnation:1,binding_revision:1,generation_id:'gen'},capabilities:{resolve:true,columns:true},...extra});
const request=(value='  注文の集計\nそのまま  ')=>{$('copilot-request').value=value;$('copilot-request').dispatchEvent(new Event('input'));};
async function choose(count=1){objects=Array.from({length:count},(_,i)=>proposalObject(i));await $('proposal-load').onclick();for(let i=0;i<count;i++){$('proposal-object').value=String(i);await $('proposal-add').onclick();}}
const proposalRecord=(extra={})=>{
 const body=dbProposal.intents.at(-1)?.body||{context:dbContext(dbCurrent()),request:dbProposal.request,relations:dbProposal.sources.map(s=>s.ref),language};
 return {id:'6b1de711-810a-4a9b-a1a9-2473b7faf13f',kind:'sql_proposal',state:'SUCCEEDED',actor:'owner',context:structuredClone(body.context),input:{request:body.request,relations:structuredClone(body.relations),language:body.language},model:{provider:'local-openai',name:'local-model'},metadata:{captured_at:'2026-11-01T01:00:00Z',relations:body.relations.map((r,i)=>({schema:r.schema_id==='raw'?'raw':'marts',table:'注文 '+i,kind:i%2?'VIEW':'TABLE',columns:[{name:'合計',type:'decimal(12,2)'}]}))},sql:'SELECT "合計" FROM marts."注文 0"',text:'提案の説明 <img src=x onerror=evil()>',error_code:null,cancel_requested:false,inference_state:'SUCCEEDED',sql_status:'NOT_EXECUTED',created_at:'2026-11-01T01:00:00Z',updated_at:'2026-11-01T01:01:00Z',...extra};
};
const pendingProposal=(extra={})=>proposalRecord({state:'RUNNING',inference_state:'RUNNING',sql:null,text:null,...extra});
async function ready(){await boot();request();await choose();}
async function submit(record=null){route=async()=>wrap(record||proposalRecord());await $('copilot-generate').onclick();}
const recoverProposal=()=>$('proposal-recover').onclick();
'''


class SQLProposalsDatabaseUI(unittest.TestCase):
    def javascript(self, body):
        harness.DatabaseUI.javascript(self, COMMON + PROPOSAL + body)

    def test_boot_no_inference_and_only_copilot_generate_is_enabled(self):
        self.javascript(r'''
await boot();assert.equal(proposalCalls().length,0);await dbExplanationPoll();assert.equal(proposalCalls().length,0);
assert.equal($('copilot-generate').disabled,true);assert.equal($('copilot-request').disabled,false);
request();await choose();
for(const lang of ['ja','en']){setLanguage(lang);await dbRefreshContext();dbControls();assert.equal($('copilot-generate').disabled,false);assert.equal($('copilot-generate').getAttribute('title'),undefined);assert.equal($('copilot-request').getAttribute('title'),undefined);}
for(const id of ['copilot-stage','copilot-publish','copilot-stage-table','ai-settings-button'])assert.equal($(id).disabled,true,id);
assert.equal(proposalCalls().length,0);
''')

    def test_picker_chosen_schema_role_more_exact_reference_and_no_context_mutation(self):
        self.javascript(r'''
await boot([{...input(),selected_role:'data_engineer'}]);request();const before=JSON.stringify(dbInput(dbCurrent())),explorer=JSON.stringify(dbExplorer);assert.equal(dbProposal.schema,'schema');
$('proposal-schema').value='raw';await $('proposal-schema').onchange();let raw;
objectsRoute=async(path,options)=>{assert.equal(options.headers['X-Shirokuma-Role'],'data_engineer');const params=new URL(path,'http://local').searchParams;assert.equal(params.get('limit'),'100');assert.ok(path.startsWith('/api/v1/schemas/raw/objects?'));raw=proposalObject(params.has('cursor')?1:0,'raw');return {items:[raw],next_cursor:params.has('cursor')?null:'next opaque'};};
await $('proposal-load').onclick();const ref=dbProposal.objects[0].ref;assert.deepEqual(ref,raw.ref);$('proposal-object').value='0';await $('proposal-add').onclick();assert.equal(dbProposal.sources[0].ref,ref);assert.ok(Object.isFrozen(ref));
await $('proposal-more').onclick();assert.equal(dbProposal.objects.length,2);assert.equal(dbProposal.cursor,null);assert.equal(JSON.stringify(dbInput(dbCurrent())),before);assert.equal(JSON.stringify(dbExplorer),explorer);
const text=visibleText($('proposal-sources'));assert.match(text,/raw.注文 0 · TABLE/);assert.doesNotMatch(text,/object-0|generation_id|binding_revision|incarnation/);
assert.equal(calls.filter(c=>c.path.includes('/objects/')||c.path.includes('preview')).length,0);
''')

    def test_picker_only_valid_current_exact_six_refs_and_query_schemas(self):
        self.javascript(r'''
schemas.push({...schemas[0],id:'denied',sql_name:'denied',capabilities:{query:false}});await boot();request();
objects=[proposalObject(),proposalObject(1,'schema',{ref:{...proposalObject(1).ref,id:'extra'}}),proposalObject(2,'raw'),proposalObject(3,'schema',{ref:{...proposalObject(3).ref,incarnation:'1'}}),proposalObject(4,'schema',{ref:{...proposalObject(4).ref,generation_id:'old'}}),proposalObject(5,'schema',{capabilities:{resolve:true,columns:false}}),proposalObject(6,'schema',{kind:'ROUTINE'}),proposalObject(7,'schema',{ref:null}),proposalObject(8,'schema',{ref:{...proposalObject(8).ref,database_id:'other'}})];
await $('proposal-load').onclick();assert.equal(dbProposal.objects.length,1);assert.ok(!$('proposal-schema').options.some(o=>o.value==='denied'));
''')

    def test_add_deduplicates_stable_identity_caps_eight_and_remove(self):
        self.javascript(r'''
await boot();request();await choose(9);assert.equal(dbProposal.sources.length,8);$('proposal-object').value='0';await dbProposalAdd();assert.equal(dbProposal.sources.length,8);
$('proposal-sources').children[0].children[1].onclick();assert.equal(dbProposal.sources.length,7);$('proposal-object').value='1';await dbProposalAdd();assert.equal(dbProposal.sources.length,7);$('proposal-object').value='8';await dbProposalAdd();assert.equal(dbProposal.sources.length,8);assert.match($('proposal-count').textContent,/8/);
''')

    def test_stale_picker_schema_and_authorization_responses_never_repopulate(self):
        self.javascript(r'''
await boot();const wait=deferred();objectsRoute=async()=>wait.promise;const load=$('proposal-load').onclick();await tick();$('proposal-schema').value='raw';await $('proposal-schema').onchange();wait.resolve({items:[proposalObject()],next_cursor:'stale'});await load;assert.equal(dbProposal.objects.length,0);assert.equal(dbProposal.cursor,null);
const wait2=deferred();objectsRoute=async()=>wait2.promise;const next=$('proposal-load').onclick();await tick();++dbAuthorizationVersion;dbControls();wait2.resolve({items:[proposalObject(0,'raw')],next_cursor:'late'});await next;assert.equal(dbProposal.objects.length,0);assert.equal(dbProposal.cursor,null);
''')

    def test_raw_unicode_byte_limit_verbatim_body_and_persist_before_post(self):
        self.javascript(r'''
await ready();const original=' \t依頼 👩‍💻\n  ';request(original);const ref=dbProposal.sources[0].ref;let body;
route=async(path,options)=>{body=JSON.parse(options.body);assert.deepEqual(Object.keys(body).sort(),['context','language','relations','request']);assert.equal(body.request,original);assert.deepEqual(body.relations,[ref]);assert.deepEqual(body.context,dbContext(dbCurrent()));assert.equal(options.headers['Idempotency-Key'],JSON.parse(storage.get(dbProposalKey)).at(-1).key);assert.deepEqual(JSON.parse(storage.get(dbProposalKey)).at(-1).body,body);return wrap(proposalRecord());};
await $('copilot-generate').onclick();assert.equal(proposalPosts().length,1);assert.equal(dbProposal.intents[0].body.relations[0],ref);assert.ok(Object.isFrozen(dbProposal.intents[0].body));
''')

    def test_unicode_bounds_and_empty_never_post(self):
        self.javascript(r'''
await ready();for(const text of ['',' \n\t','あ'.repeat(1366),'😀'.repeat(1025)]){request(text);await $('copilot-generate').onclick();assert.equal(proposalPosts().length,0);}
request('😀'.repeat(1024));assert.equal($('copilot-generate').disabled,false);await submit();assert.equal(proposalPosts().length,1);
''')

    def test_storage_failure_sends_nothing_and_does_not_hold(self):
        self.javascript(r'''
await ready();const original=localStorage.setItem;localStorage.setItem=(key,value)=>{if(key===dbProposalKey)throw Error('private details');original(key,value);};await $('copilot-generate').onclick();assert.equal(proposalPosts().length,0);assert.equal(dbProposalHeld().length,0);assert.doesNotMatch(visibleText($('proposal-output')),/private details/);
''')

    def test_readonly_sql_first_bilingual_asof_sources_and_html_is_text(self):
        self.javascript(r'''
await ready();await submit();const r=dbProposal.record;
for(const locale of ['en','ja']){setLanguage(locale);const node=$('proposal-output'),text=visibleText(node);assert.equal(node.children[0].tagName,'PRE');assert.equal(node.children[0].textContent,r.sql);assert.equal(node.children[1].textContent,r.text);assert.match(text,/2026-11-01T01:00:00Z/);assert.match(text,/marts.注文 0 · TABLE/);assert.ok(text.includes(t('{count} columns',{count:1})));assert.ok(text.includes(t('LOCAL · SQL not executed')));assert.ok(visibleText($('proposal-panel')).includes(t(dbProposalHelp)));assert.equal(node.querySelector('img'),undefined);assert.doesNotMatch(text,/object-0|generation_id/);}
const before=visibleText($('proposal-output'));dbExplanationSync();assert.equal(visibleText($('proposal-output')),before);
''')

    def test_no_editor_result_history_wap_or_legacy_api_writes(self):
        self.javascript(r'''
await ready();editDoctor('SELECT independent Doctor');queryResult={columns:['keep'],rows:[[7]]};const result=queryResult,editor=$('sql').value,doctor=$('bear-query-sql').value,worksheet=JSON.stringify(dbInput(dbCurrent())),oldHistory=JSON.stringify(dbHistory);dbRuns.set('keep',{marker:7});const runs=JSON.stringify([...dbRuns]);
await submit();assert.equal($('sql').value,editor);assert.equal($('bear-query-sql').value,doctor);assert.equal(queryResult,result);assert.equal(JSON.stringify(dbInput(dbCurrent())),worksheet);assert.equal(JSON.stringify([...dbRuns]),runs);assert.equal(JSON.stringify(dbHistory),oldHistory);assert.equal(dbExplanation.record,null);
assert.ok(proposalCalls().every(c=>c.path.startsWith(dbProposalEndpoint)));assert.equal(calls.filter(c=>c.options?.method==='POST'&&!c.path.startsWith(dbProposalEndpoint)).length,0);
''')

    def test_accepted_original_get_after_both_drafts_request_sources_navigation_reload(self):
        self.javascript(r'''
await ready();await submit(pendingProposal());const original=structuredClone(proposalPosts()[0]);editSQL('SELECT new worksheet');editDoctor('SELECT new doctor');request('different raw request');$('proposal-sources').children[0].children[1].onclick();await activate($('tab-bears'));await activate($('tab-warehouse'));dbProposalInstall();assert.equal(dbProposalHeld().length,1);assert.equal($('copilot-generate').disabled,true);
route=async(path,options)=>{assert.equal(options.method,undefined);assert.ok(path.startsWith(dbProposalEndpoint+'/'+pendingProposal().id+'?'));return wrap(proposalRecord());};await recoverProposal();assert.equal(proposalPosts().length,1);assert.equal($('sql').value,'SELECT new worksheet');assert.equal($('bear-query-sql').value,'SELECT new doctor');assert.equal($('copilot-request').value,'different raw request');assert.equal(dbProposal.sources.length,0);assert.equal(dbProposalHeld().length,0);assert.equal(proposalPosts()[0].options.body,original.options.body);
''')

    def test_unknown_ack_replay_only_exact_explicit_recover_after_reload(self):
        self.javascript(r'''
await ready();route=async()=>{throw Error('lost ACK secret');};await $('copilot-generate').onclick();const original=structuredClone(proposalPosts()[0]);request('changed');editSQL('SELECT changed');editDoctor('SELECT Doctor changed');dbProposalInstall();
const count=proposalCalls().length;await dbProposalSubmit('poll');await dbProposalSubmit('unrelated');await dbExplanationPoll();assert.equal(proposalCalls().length,count);
route=async(path,options)=>{assert.equal(path,dbProposalEndpoint);assert.equal(options.body,original.options.body);assert.equal(options.headers['Idempotency-Key'],original.options.headers['Idempotency-Key']);return wrap(pendingProposal());};await recoverProposal();assert.equal(proposalPosts().length,2);assert.equal(dbProposalHeld().length,1);assert.doesNotMatch(visibleText($('proposal-output')),/secret/);
''')

    def test_history_scans_all_held_originals_older_than_fifty(self):
        self.javascript(r'''
await ready();route=async()=>{throw Error('lost');};await $('copilot-generate').onclick();const base=structuredClone(dbProposal.intents[0]);
const items=Array.from({length:51},(_,i)=>({...structuredClone(base),key:'00000000-0000-4000-8000-'+String(i).padStart(12,'0'),id:'10000000-0000-4000-8000-'+String(i).padStart(12,'0'),body:{...structuredClone(base.body),request:'request '+i}}));storage.set(dbProposalKey,JSON.stringify(items));dbProposalInstall();assert.equal(dbProposalHeld().length,51);
const terminal=items.slice(1).map(i=>proposalRecord({id:i.id,input:{request:i.body.request,relations:i.body.relations,language:i.body.language}}));route=async()=>({context:dbContext(dbCurrent()),explanations:terminal});await $('proposal-history').onclick();assert.equal(dbProposalHeld().length,1);assert.equal(dbProposalHeld()[0].key,items[0].key);assert.equal($('proposal-original').value,items[0].key);
route=async(path,options)=>{assert.equal(options.method,undefined);assert.ok(path.includes(items[0].id));return wrap(proposalRecord({id:items[0].id,input:{request:items[0].body.request,relations:items[0].body.relations,language:items[0].body.language}}));};await recoverProposal();assert.equal(dbProposalHeld().length,0);assert.equal(proposalPosts().length,1);
''')

    def test_persisted_terminal_hints_never_unlock_and_auth_roundtrip_invalidates(self):
        self.javascript(r'''
await ready();await submit();assert.equal(dbProposalHeld().length,0);const saved=JSON.parse(storage.get(dbProposalKey));Object.assign(saved[0],{terminal:true,state:'SUCCEEDED',inference_state:'SUCCEEDED'});storage.set(dbProposalKey,JSON.stringify(saved));dbProposalInstall();assert.equal(dbProposalHeld().length,1);
route=async()=>wrap(proposalRecord());await recoverProposal();assert.equal(dbProposalHeld().length,0);request('edit alone');assert.equal(dbProposalHeld().length,0);
currentRole='bi_viewer';dbControls();currentRole='admin';dbControls();assert.equal(dbProposalHeld().length,1);assert.equal($('copilot-generate').disabled,true);await recoverProposal();assert.equal(dbProposalHeld().length,0);
''')

    def test_only_displayed_accepted_id_is_polled_and_history_is_get_only(self):
        self.javascript(r'''
await ready();await submit(pendingProposal());const accepted=dbProposal.record;route=async(path,options)=>{assert.equal(options.method,undefined);assert.ok(path.includes(accepted.id));return wrap(pendingProposal());};await dbProposalSubmit('poll');assert.equal(proposalPosts().length,1);
request('other');const count=proposalCalls().length;await dbProposalSubmit('poll');assert.equal(proposalCalls().length,count);
route=async()=>({context:dbContext(dbCurrent()),explanations:[proposalRecord()]});await $('proposal-history').onclick();route=async(path,options)=>{assert.equal(options.method,undefined);return wrap(proposalRecord());};$('proposal-saved').value='0';await $('proposal-saved').onchange();assert.equal(dbProposal.origin,'history');const after=proposalCalls().length;await dbProposalSubmit('poll');assert.equal(proposalCalls().length,after);assert.equal(proposalPosts().length,1);assert.equal($('copilot-request').value,'other');
''')

    def test_explicit_recovery_selection_and_all_held_hold(self):
        self.javascript(r'''
await ready();route=async()=>{throw Error('lost');};await $('copilot-generate').onclick();const first=dbProposal.intents[0],second=dbProposalFreeze({...structuredClone(first),key:'ffffffff-ffff-4fff-8fff-ffffffffffff',body:{...structuredClone(first.body),request:'second'}});dbProposal.intents.push(second);dbProposalPersist();dbProposalSync();
$('proposal-original').value=second.key;await $('proposal-original').onchange();route=async(path,options)=>{assert.equal(JSON.parse(options.body).request,'second');return wrap(proposalRecord({input:{request:'second',relations:second.body.relations,language:second.body.language}}));};await recoverProposal();assert.equal(dbProposalHeld().length,1);assert.equal(dbProposalHeld()[0],first);assert.equal($('copilot-generate').disabled,true);
''')

    def test_failed_unknown_canceled_lifecycle_and_own_cancel_completion_race(self):
        self.javascript(r'''
await ready();await submit(pendingProposal());const accepted=dbProposal.record;route=async(path,options)=>{assert.equal(path,dbProposalEndpoint+'/'+accepted.id+'/cancel');assert.deepEqual(JSON.parse(options.body),{context:accepted.context});return wrap(proposalRecord({cancel_requested:true}));};await $('proposal-cancel').onclick();assert.equal(dbProposal.record.state,'SUCCEEDED');assert.ok(dbProposal.record.sql);assert.equal(dbProposalHeld().length,0);
assert.equal(calls.filter(c=>c.path.includes('/queries/')&&c.options?.method==='POST').length,0);
''')

    def test_unknown_and_canceled_running_do_not_release_hold(self):
        self.javascript(r'''
await ready();await submit(pendingProposal({state:'UNKNOWN',inference_state:'UNKNOWN',error_code:'AI_PROVIDER_OUTCOME_UNKNOWN'}));assert.equal(dbProposalHeld().length,1);
route=async()=>wrap(pendingProposal({state:'CANCELED',cancel_requested:true,inference_state:'RUNNING'}));await recoverProposal();assert.equal(dbProposalHeld().length,1);assert.ok(visibleText($('proposal-output')).includes(t('Model result will be discarded. Physical model stoppage is not confirmed.')));
route=async()=>wrap(pendingProposal({state:'CANCELED',cancel_requested:true,inference_state:'CANCELED'}));await recoverProposal();assert.equal(dbProposalHeld().length,0);
''')

    def test_fixed_error_messages_no_generic_status_unlock(self):
        self.javascript(r'''
await ready();for(const status of [403,409,404]){route=async()=>({http:status,body:{code:'AI_INPUT_INVALID',message:'secret parser excerpt'}});if(!proposalPosts().length)await $('copilot-generate').onclick();else await recoverProposal();assert.equal(dbProposalHeld().length,1);assert.doesNotMatch(visibleText($('proposal-output')),/secret|excerpt/);}
route=async()=>wrap(pendingProposal({state:'FAILED',inference_state:'FAILED',error_code:'AI_OUTPUT_INVALID'}));await recoverProposal();assert.equal(dbProposalHeld().length,0);assert.match(visibleText($('proposal-output')),/AI_OUTPUT_INVALID/);
''')

    def test_dto_closed_shape_identity_context_input_metadata_and_lifecycle_rejected(self):
        mutations = [
            "r.kind='sql_explanation'", "r.actor='other'", "r.id='bad'", "r.context.grant_revision++",
            "r.context.extra=true", "r.input.request+='different'", "r.input.extra=true",
            "r.input.relations[0].object_id='different'", "r.input.relations[0].extra=true",
            "r.input.relations[0].incarnation='1'", "r.input.relations[0].database_id='other'",
            "r.metadata.view_dependencies=[]", "r.metadata.relations[0].columns[0].private='leak'",
            "r.metadata.relations[0].kind='OTHER'", "r.model.provider='remote'", "r.source_snapshot={}",
            "r.plan=null", "r.query_id='query'", "r.sql_status='EXECUTED'", "r.inference_state='RUNNING'",
            "r.sql='あ'.repeat(5462)", "r.text='😀'.repeat(1025)", "r.created_at='bad'",
            "r.error_code='private arbitrary text'", "r.state='FAILED'", "r.metadata.relations=[]",
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.javascript("await ready();route=async()=>{const r=proposalRecord();" + mutation + r''';return wrap(r);};await $('copilot-generate').onclick();assert.equal(dbProposal.record,null);assert.equal(dbProposalHeld().length,1);assert.doesNotMatch(visibleText($('proposal-output')),/private arbitrary text|leak/);
''')

    def test_late_completion_cannot_display_or_prove_after_edits(self):
        for edit in ["request('edited')", "editSQL('SELECT edited')", "editDoctor('SELECT Doctor edited')", "++dbAuthorizationVersion;dbControls()", "dbPrincipal=JSON.stringify(['other',true]);dbControls()", "dbMaximumRole='bi_viewer';dbControls()", "dbCurrent().max_rows=99;dbControls()", "await activate($('tab-bears'))"]:
            with self.subTest(edit=edit):
                self.javascript("await ready();const wait=deferred();route=async()=>wait.promise;const pending=$('copilot-generate').onclick();await tick();const r=proposalRecord();" + edit + r''';wait.resolve(wrap(r));await pending;assert.equal(dbProposal.record,null);assert.equal(dbProposal.proofs.size,0);assert.equal(dbProposal.intents.length,1);
''')

    def test_old_finally_never_clears_new_busy_token_after_reload(self):
        self.javascript(r'''
await ready();const old=deferred();route=async()=>old.promise;const first=$('copilot-generate').onclick();await tick();const oldRecord=proposalRecord();dbProposalInstall();const next=deferred();route=async()=>next.promise;const second=recoverProposal();await tick();const token=dbProposal.busy;assert.ok(token);old.resolve(wrap(oldRecord));await first;assert.equal(dbProposal.busy,token);assert.equal(dbProposal.record,null);next.resolve(wrap(pendingProposal()));await second;assert.equal(dbProposal.busy,null);assert.equal(dbProposal.record.state,'RUNNING');
''')

    def test_forms_survive_language_render_and_navigation_without_ai(self):
        self.javascript(r'''
await ready();const raw=$('copilot-request').value,ref=dbProposal.sources[0].ref;setLanguage('ja');dbControls();await activate($('tab-bears'));await activate($('tab-warehouse'));assert.equal($('copilot-request').value,raw);assert.equal(dbProposal.sources[0].ref,ref);assert.equal(proposalCalls().length,0);
dbProposalInstall();assert.equal($('copilot-request').value,raw);assert.equal(proposalCalls().length,0);
''')

    def test_refresh_replacement_invalidates_selected_ref_without_restamping(self):
        self.javascript(r'''
await ready();const original=dbProposal.sources[0].ref;objects=[proposalObject(0,'schema',{ref:{...proposalObject().ref,incarnation:2}})];await $('proposal-load').onclick();assert.equal(dbProposal.sources.length,0);assert.equal(original.incarnation,1);assert.equal(dbProposal.objects[0].ref.incarnation,2);assert.equal($('copilot-generate').disabled,true);assert.equal(proposalCalls().length,0);
''')

    def test_recovery_identity_never_crosses_actor_worksheet_or_full_nine_context(self):
        for change in ["dbPrincipal=JSON.stringify(['other',true])", "worksheetId='other'", "dbCurrent().schema_id='raw'", "dbCurrent().warehouse='OTHER'", "dbCurrent().binding_revision=99", "dbCurrent().generation_id='other'", "inventory[0].grant_revision=99;dbInventories.get('admin').items[0].grant_revision=99", "dbInventories.get('admin').items[0].sql_catalog='other'", "dbSchemasFor(dbCurrent())[0].sql_name='other'", "dbCurrent().selected_role='bi_viewer'"]:
            with self.subTest(change=change):
                self.javascript("await ready();await submit(pendingProposal());const count=proposalCalls().length;" + change + r''';dbControls();await recoverProposal();assert.equal(proposalCalls().length,count);assert.equal(dbProposal.record,null);assert.equal(dbProposal.intents.length,1);
''')

    def test_reordered_server_fields_are_valid_and_failure_has_actionable_ja_en(self):
        self.javascript(r'''
await ready();route=async()=>{const r=pendingProposal({state:'FAILED',inference_state:'FAILED',error_code:'AI_OUTPUT_INVALID'});r.context=Object.fromEntries(Object.entries(r.context).reverse());r.input=Object.fromEntries(Object.entries(r.input).reverse());r.input.relations=r.input.relations.map(ref=>Object.fromEntries(Object.entries(ref).reverse()));return wrap(r);};await $('copilot-generate').onclick();assert.equal(dbProposal.record.state,'FAILED');assert.equal(dbProposalHeld().length,0);for(const locale of ['ja','en']){setLanguage(locale);assert.ok(visibleText($('proposal-output')).includes(t(dbProposalFailed)));}
''')

    def test_authority_change_does_not_restamp_existing_selected_sources(self):
        self.javascript(r'''
await ready();const ref=dbProposal.sources[0].ref;++dbAuthorizationVersion;dbControls();assert.equal(dbProposal.sources.length,0);assert.equal(ref.incarnation,1);assert.equal($('copilot-generate').disabled,true);await $('copilot-generate').onclick();assert.equal(proposalPosts().length,0);await choose();assert.equal(dbProposal.sources.length,1);assert.equal($('copilot-generate').disabled,false);
''')

    def test_unique_fresh_post_rejection_proves_no_admission_and_allows_explicit_new_key(self):
        for status, code in [(400, 'AI_INPUT_INVALID'), (409, 'AI_METADATA_CHANGED')]:
            for nested in [False, True]:
                with self.subTest(status=status, nested=nested):
                    self.javascript('const status='+str(status)+',code='+json.dumps(code)+',nested='+json.dumps(nested)+r''';
await ready();route=async()=>({http:status,body:nested?{error:{code,message:'secret parser/provider'}}:{code,message:'secret parser/provider'}});await $('copilot-generate').onclick();const original=dbProposal.intents[0],body=JSON.stringify(original.body),key=original.key;
assert.equal(dbProposalHeld().length,0);assert.equal(original.id,null);assert.deepEqual(JSON.parse(storage.get(dbProposalKey))[0].body,JSON.parse(body));assert.equal(JSON.parse(storage.get(dbProposalKey))[0].key,key);assert.doesNotMatch(visibleText($('proposal-output')),/secret parser/);
for(const locale of ['ja','en']){setLanguage(locale);const message=status===400?'Request rejected before acceptance. Review the request and selected sources, then explicitly request a new SQL draft.':'Source metadata changed before acceptance. Reload source objects, remove and re-add changed sources, then explicitly request a new SQL draft.';assert.ok(visibleText($('proposal-output')).includes(t(message)));}
request('explicitly edited new request');assert.equal($('copilot-generate').disabled,false);assert.equal(proposalPosts().length,1);route=async()=>wrap(proposalRecord());await $('copilot-generate').onclick();assert.equal(proposalPosts().length,2);assert.notEqual(dbProposal.intents[1].key,key);assert.equal(dbProposal.intents[1].body.request,'explicitly edited new request');assert.equal(JSON.stringify(original.body),body);assert.equal(original.key,key);
''')

    def test_rejection_proof_is_memory_only_reload_replays_same_original(self):
        for status, code in [(400, 'AI_INPUT_INVALID'), (409, 'AI_METADATA_CHANGED')]:
            with self.subTest(status=status):
                self.javascript('const status='+str(status)+',code='+json.dumps(code)+r''';
await ready();route=async()=>({http:status,body:{error:{code}}});await $('copilot-generate').onclick();const original=structuredClone(proposalPosts()[0]);assert.equal(dbProposalHeld().length,0);
const saved=JSON.parse(storage.get(dbProposalKey));Object.assign(saved[0],{noAdmission:true,terminal:true,rejection:code,state:'FAILED'});storage.set(dbProposalKey,JSON.stringify(saved));request('edited after rejection');dbProposalInstall();assert.equal(dbProposalHeld().length,1);assert.equal($('copilot-generate').disabled,true);
route=async(path,options)=>{assert.equal(path,dbProposalEndpoint);assert.equal(options.body,original.options.body);assert.equal(options.headers['Idempotency-Key'],original.options.headers['Idempotency-Key']);return {http:status,body:{code}};};await recoverProposal();assert.equal(dbProposalHeld().length,0);assert.equal($('copilot-request').value,'edited after rejection');assert.equal(proposalPosts().length,2);
''')

    def test_only_exact_status_structured_code_without_contradictory_acceptance_is_proof(self):
        cases = [
            [400, {'code': 'AI_METADATA_CHANGED'}], [409, {'code': 'AI_INPUT_INVALID'}],
            [403, {'code': 'AI_INPUT_INVALID'}], [404, {'code': 'AI_INPUT_INVALID'}],
            [502, {'code': 'AI_INPUT_INVALID'}], [503, {'code': 'AI_METADATA_CHANGED'}],
            [409, {'code': 'IDEMPOTENCY_CONFLICT'}], [400, {'error': 'AI_INPUT_INVALID'}],
            [400, {'message': 'AI_INPUT_INVALID'}], [409, {'error': {'message': 'AI_METADATA_CHANGED'}}],
            [400, {'code': 'AI_INPUT_INVALID', 'error': {'code': 'AI_METADATA_CHANGED'}}],
            [400, {'code': 'AI_INPUT_INVALID', 'id': 'accepted-id'}],
            [409, {'code': 'AI_METADATA_CHANGED', 'explanation': {'id': 'accepted-id'}}],
            [400, {'code': 'AI_INPUT_INVALID', 'explanation': {'state': 'RUNNING'}}],
        ]
        for status, code in [(400, 'AI_INPUT_INVALID'), (409, 'AI_METADATA_CHANGED')]:
            for location in ['top', 'error', 'explanation']:
                for key in ['id', 'explanation_id', 'request_id', 'query_id']:
                    accepted = {key: '6b1de711-810a-4a9b-a1a9-2473b7faf13f'}
                    response = {'code': code, **accepted} if location == 'top' else ({'error': {'code': code, **accepted}} if location == 'error' else {'code': code, 'explanation': accepted})
                    cases.append([status, response])
        for status, response in cases:
            with self.subTest(status=status, response=response):
                self.javascript('const response='+json.dumps({'http': status, 'body': response})+r''';
await ready();route=async()=>response;await $('copilot-generate').onclick();assert.equal(dbProposalHeld().length,1);assert.equal(dbProposal.proofs.size,0);assert.equal($('copilot-generate').disabled,true);assert.equal(dbProposal.record,null);const original=structuredClone(proposalPosts()[0]);request('edited while held');await $('copilot-generate').onclick();assert.equal(proposalPosts().length,1);assert.equal(dbProposalHeld().length,1);assert.equal(dbProposal.intents.length,1);assert.equal(dbProposal.intents[0].key,original.options.headers['Idempotency-Key']);assert.equal(JSON.stringify(dbProposal.intents[0].body),original.options.body);assert.deepEqual(JSON.parse(storage.get(dbProposalKey))[0].body,JSON.parse(original.options.body));
''')

    def test_accepted_id_get_and_transport_never_make_rejection_proof(self):
        self.javascript(r'''
await ready();await submit(pendingProposal());request('edited');route=async()=>({http:400,body:{code:'AI_INPUT_INVALID'}});await recoverProposal();assert.equal(dbProposalHeld().length,1);assert.equal(proposalPosts().length,1);route=async()=>({http:409,body:{error:{code:'AI_METADATA_CHANGED'}}});await recoverProposal();assert.equal(dbProposalHeld().length,1);assert.equal(proposalPosts().length,1);route=async()=>{throw Object.assign(Error('network secret'),{rejection:'AI_INPUT_INVALID',noAdmission:true});};await recoverProposal();assert.equal(dbProposalHeld().length,1);assert.doesNotMatch(visibleText($('proposal-output')),/network secret/);
''')

    def test_late_rejection_cannot_unlock_after_form_auth_or_new_token(self):
        for status, code in [(400, 'AI_INPUT_INVALID'), (409, 'AI_METADATA_CHANGED')]:
            for edit in ["request('edited')", "++dbAuthorizationVersion;dbControls()", "currentRole='bi_viewer';dbControls();currentRole='admin';dbControls()"]:
                with self.subTest(status=status, edit=edit):
                    self.javascript('const status='+str(status)+',code='+json.dumps(code)+";await ready();const wait=deferred();route=async()=>wait.promise;const pending=$('copilot-generate').onclick();await tick();"+edit+r''';wait.resolve({http:status,body:{code}});await pending;assert.equal(dbProposalHeld().length,1);assert.equal(dbProposal.proofs.size,0);assert.equal(dbProposal.record,null);
''')
        self.javascript(r'''
await ready();const firstWait=deferred();route=async()=>firstWait.promise;const first=$('copilot-generate').onclick();await tick();dbProposalInstall();const secondWait=deferred();route=async()=>secondWait.promise;const second=recoverProposal();await tick();const token=dbProposal.busy;firstWait.resolve({http:409,body:{code:'AI_METADATA_CHANGED'}});await first;assert.equal(dbProposal.busy,token);assert.equal(dbProposalHeld().length,1);assert.equal(dbProposal.proofs.size,0);secondWait.resolve(wrap(pendingProposal()));await second;assert.equal(dbProposalHeld().length,1);
''')

    def test_recovered_and_historical_provenance_full_request_is_literal_and_never_overwrites(self):
        self.javascript(r'''
await ready();const raw='  <img src=x onerror=evil()>\n'+('元の要求 '.repeat(30))+'  ';request(raw);await submit(pendingProposal());const accepted=pendingProposal(),originalRef=structuredClone(accepted.input.relations),editor=$('sql').value,doctor=$('bear-query-sql').value;
request('current different request');$('proposal-sources').children[0].children[1].onclick();objects=[proposalObject(9)];await $('proposal-load').onclick();$('proposal-object').value='0';await $('proposal-add').onclick();const selected=JSON.stringify(dbProposal.sources);
route=async()=>wrap(proposalRecord());await recoverProposal();assert.equal(dbProposal.origin,'recovered');assert.equal(dbProposal.record.input.request,raw);assert.deepEqual(dbProposal.record.input.relations,originalRef);
for(const locale of ['en','ja']){setLanguage(locale);const node=$('proposal-output');assert.equal(node.children[0].textContent,dbProposal.record.sql);assert.equal(node.children[1].textContent,dbProposal.record.text);assert.ok(node.children.some(child=>child.tagName==='PRE'&&child.textContent===raw));assert.ok(visibleText(node).includes(t('Recovered SQL draft (read only)')));assert.ok(visibleText(node).includes('marts.注文 0 · TABLE'));assert.ok(!visibleText(node).includes('注文 9'));assert.equal(node.querySelector('img'),undefined);}
assert.equal($('copilot-request').value,'current different request');assert.equal(JSON.stringify(dbProposal.sources),selected);assert.equal($('sql').value,editor);assert.equal($('bear-query-sql').value,doctor);
route=async()=>({context:dbContext(dbCurrent()),explanations:[proposalRecord()]});await $('proposal-history').onclick();route=async()=>wrap(proposalRecord());$('proposal-saved').value='0';await $('proposal-saved').onchange();assert.equal(dbProposal.origin,'history');for(const locale of ['en','ja']){setLanguage(locale);assert.ok(visibleText($('proposal-output')).includes(t('Historical SQL draft (read only)')));assert.ok($('proposal-output').children.some(child=>child.tagName==='PRE'&&child.textContent===raw));}const count=proposalCalls().length;await dbProposalSubmit('poll');assert.equal(proposalCalls().length,count);assert.equal($('copilot-request').value,'current different request');assert.equal(JSON.stringify(dbProposal.sources),selected);
''')

    def test_recovered_label_survives_own_id_poll_and_current_label_is_honest(self):
        self.javascript(r'''
await ready();await submit(pendingProposal());assert.equal(dbProposal.origin,'current');assert.ok(visibleText($('proposal-output')).includes(t('SQL draft (read only)')));request('edited');route=async()=>wrap(pendingProposal());await recoverProposal();assert.equal(dbProposal.origin,'recovered');route=async(path,options)=>{assert.equal(options.method,undefined);return wrap(proposalRecord());};await dbProposalSubmit('poll');assert.equal(dbProposal.origin,'recovered');assert.ok(visibleText($('proposal-output')).includes(t('Recovered SQL draft (read only)')));assert.equal(proposalPosts().length,1);
''')

    def test_availability_copy_is_worksheet_copilot_only_after_language_role_refresh(self):
        self.javascript(r'''
const mainDataAction=document.createElement('button');mainDataAction.id='bear-data-run';$('bears').append(mainDataAction);await ready();
for(const locale of ['en','ja']){setLanguage(locale);await dbRefreshContext();dbControls();assert.equal($('bears-guide-copilot').textContent,t('Draft SQL in Copilot'));assert.equal($('bears-guide-copilot').disabled,false);assert.equal($('bear-data-legacy').hidden,true);assert.equal($('database-bears-notice').textContent,t('Legacy direct Bear and Copilot execution is unavailable. Shared AI settings require platform administrator authority and global Admin.'));assert.equal($('copilot-generate').disabled,false);assert.equal($('copilot-generate').getAttribute('title'),undefined);assert.equal($('copilot-request').getAttribute('title'),undefined);for(const id of ['copilot-stage','copilot-publish','ai-settings-button','bear-data-run'])assert.equal($(id).disabled,true,id);currentRole='bi_viewer';dbControls();currentRole='admin';dbControls();await choose();assert.equal($('copilot-generate').disabled,false);}
''')

    def test_durable_authority_failure_codes_close_original_after_restored_authority(self):
        for code in ['SQL_PERMISSION_DENIED', 'UNKNOWN_CONTEXT', 'STALE_BINDING']:
            with self.subTest(code=code):
                self.javascript('const code='+json.dumps(code)+r''';
await ready();$('proposal-sources').children[0].children[1].onclick();$('proposal-schema').value='raw';await $('proposal-schema').onchange();objects=[proposalObject(0,'raw')];await $('proposal-load').onclick();$('proposal-object').value='0';await $('proposal-add').onclick();await submit(pendingProposal());const accepted=structuredClone(dbProposal.record),original=structuredClone(proposalPosts()[0]),context=structuredClone(dbContext(dbCurrent()));
request('current edited request');editSQL('SELECT current worksheet');editDoctor('SELECT current Doctor');queryResult={columns:['keep'],rows:[[11]]};const result=queryResult,editor=$('sql').value,doctor=$('bear-query-sql').value;
const secondary=dbSchemasFor(dbCurrent()).find(s=>s.id==='raw'),allowed=secondary.capabilities.query;secondary.capabilities.query=false;++dbAuthorizationVersion;dbControls();route=async()=>({http:403,body:{code:'SQL_PERMISSION_DENIED',message:'private denial'}});await recoverProposal();assert.equal(dbProposalHeld().length,1);
secondary.capabilities.query=allowed;++dbAuthorizationVersion;dbControls();assert.deepEqual(dbContext(dbCurrent()),context);
route=async(path,options)=>{assert.equal(options.method,undefined);assert.ok(path.startsWith(dbProposalEndpoint+'/'+accepted.id+'?'));assert.equal(new URL(path,'http://local').searchParams.get('context'),JSON.stringify(context));return wrap({...accepted,state:'FAILED',inference_state:'FAILED',sql:null,text:null,error_code:code});};await recoverProposal();
assert.equal(dbProposal.record.state,'FAILED');assert.equal(dbProposal.record.error_code,code);assert.equal(dbProposal.record.sql,null);assert.equal(dbProposal.record.text,null);assert.deepEqual(dbProposal.record.input,accepted.input);assert.equal(dbProposalHeld().length,0);assert.equal(proposalPosts().length,1);assert.equal(dbProposal.intents[0].key,original.options.headers['Idempotency-Key']);assert.equal(JSON.stringify(dbProposal.intents[0].body),original.options.body);
assert.equal($('copilot-request').value,'current edited request');assert.equal($('sql').value,editor);assert.equal($('bear-query-sql').value,doctor);assert.equal(queryResult,result);assert.equal(calls.filter(c=>c.options?.method==='POST'&&!c.path.startsWith(dbProposalEndpoint)).length,0);
objects=[proposalObject(0,'raw')];await $('proposal-load').onclick();$('proposal-object').value='0';await $('proposal-add').onclick();assert.equal($('copilot-generate').disabled,false);route=async()=>wrap(proposalRecord({id:'6b1de711-810a-4a9b-a1a9-2473b7faf140'}));await $('copilot-generate').onclick();assert.equal(proposalPosts().length,2);assert.notEqual(dbProposal.intents[1].key,dbProposal.intents[0].key);assert.equal(dbProposal.intents[1].body.request,'current edited request');assert.equal($('sql').value,editor);assert.equal($('bear-query-sql').value,doctor);assert.equal(queryResult,result);
''')

    def test_picker_preload_instruction_differs_from_completed_empty_listing(self):
        self.javascript(r'''
await boot();const instruction='Load source objects for the chosen schema.',empty='No eligible sources. Choose another query-enabled schema.';
for(const locale of ['ja','en']){setLanguage(locale);assert.equal($('proposal-picker-status').textContent,t(instruction));}
objects=[];await $('proposal-load').onclick();for(const locale of ['ja','en']){setLanguage(locale);assert.equal($('proposal-picker-status').textContent,t(empty));}
$('proposal-schema').value='raw';await $('proposal-schema').onchange();assert.equal($('proposal-picker-status').textContent,t(instruction));await $('proposal-load').onclick();assert.equal($('proposal-picker-status').textContent,t(empty));++dbAuthorizationVersion;dbControls();assert.equal($('proposal-picker-status').textContent,t(instruction));assert.equal(proposalCalls().length,0);
''')


if __name__ == '__main__':
    unittest.main()
