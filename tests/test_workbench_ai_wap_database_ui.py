"""Explicit, read-only proposal handoff through the shipped Database UI handlers."""
import unittest

import test_workbench_database_ui as harness
from test_workbench_bears_database_ui import COMMON
from test_workbench_ai_sql_proposals_database_ui import PROPOSAL
from test_workbench_dataops_ui import SETUP


WAP = r'''
const baseWapRoute=route;
const wapCalls=()=>calls.filter(c=>c.path.includes('/wap-draft?'));
const fields=p=>JSON.stringify(['models','model-id','sql','target-schema','name','target','min','not-null','build','audit','publication','metadata-id'].map(k=>$(p.id+'-'+k).value).concat(p.sources.map(s=>[s.alias.value,s.table.value])));
async function selected(count=2,role='admin',sourceSchema='schema'){
 schemas.push({id:'sandbox',database_id:'db',sql_name:'sandbox',purpose:'SANDBOX',display_name:'Sandbox',capabilities:{browse:true,query:true,write:true,wap_models:true}});
 await boot([{...input(),schema_id:sourceSchema,selected_role:role}]);request();objects=Array.from({length:count},(_,i)=>proposalObject(i,sourceSchema));await $('proposal-load').onclick();for(let i=0;i<count;i++){$('proposal-object').value=String(i);await $('proposal-add').onclick();}
 const r=proposalRecord();route=async()=>wrap(r);dbProposal.history=[r];await dbProposalHistory(0);
 assert.equal(dbProposal.wapRecord,dbProposal.record);const p=dataopsPanels.get('p35-wap');
 const before=calls.length;await $('proposal-wap').onclick();assert.equal(calls.length,before);assert.equal(activeTab,'transformation');assert.equal(dbProposal.record,null);
 route=baseWapRoute;return p;
}
async function destination(p){
 p.schema='sandbox';dataopsChanged(p);await dataopsRefresh(p);assert.equal(p.inventoryReady,true);
 // Explicit existing New model after choosing the panel context.
 $('p35-wap-models').value='';await $('p35-wap-models').onchange();assert.ok(p.aiPending);assert.equal(dataopsAiPristine(p),true);
}
function draft(p,extra={}){
 const o=p.aiPending;return {version:1,proposal_id:o.id,source_context:structuredClone(o.context),destination_context:structuredClone(p.context),original_sql:o.original_sql,sql:'SELECT "合計" FROM binding_0',sources:[{alias:'binding_0',ref:structuredClone(o.refs[0]),schema:o.labels[0].schema,table:o.labels[0].table}],request:o.request,proposal_created_at:o.created_at,prepared_at:'2026-11-01T02:00:00Z',...extra};
}
const envelopeDraft=(p,d=draft(p))=>({context:structuredClone(p.aiPending.context),draft:d});
async function load(p,d=draft(p)){route=async()=>envelopeDraft(p,d);await $('p35-wap-ai-load').onclick();}
'''


class AIWapDatabaseUI(unittest.TestCase):
    def javascript(self, body):
        harness.DatabaseUI.javascript(self, SETUP.replace("async function ready(", "async function readyDataops(") + COMMON + PROPOSAL + WAP + body)

    def test_guides_mount_outside_legacy_panels_and_open_existing_copilot_from_details(self):
        # The common notice and guide precede the forms in the Bears section,
        # outside the disabled/hidden legacy Bear tabs.
        self.assertIn("if(prefix==='bears')$('database-bears-notice').after(toolbar)", harness.HTML)
        self.assertIn('id="bear-cards" class="bear-layout"', harness.HTML)
        self.assertNotIn("$('bear-tab-data').disabled=false", harness.HTML)
        self.javascript(r"""
await ready();const cards=$('bear-cards'),legacy=$('bear-data-panel');$('bears').append(cards);cards.append(legacy);legacy.hidden=true;
await activate($('tab-bears'));const guide=$('database-bears-workflows');assert.equal(guide.parentElement,$('bears'));const notice=$('database-bears-notice'),siblings=$('bears').children;assert.equal(notice.parentElement,guide.parentElement);assert.equal(siblings[siblings.indexOf(notice)+1],guide);assert.ok(siblings.indexOf(guide)<siblings.indexOf(cards));assert.ok(!cards.descendants().includes(guide));
for(let n=$('bears-guide-copilot');n;n=n.parentElement){assert.equal(n.hidden,false);assert.equal(n.disabled,false);}
assert.equal($('database-copilot-workflows').parentElement,$('copilot-stage').parentElement);assert.equal($('copilot-stage').disabled,true);assert.equal($('copilot-publish').disabled,true);
request('retained request');editSQL('SELECT retained');queryResult={columns:['retained'],rows:[[9]]};const result=queryResult,w=dbCurrent(),context=JSON.stringify(dbContext(w)),inputs=JSON.stringify(dbInput(w)),count=worksheets.length,proposal=dbProposal.record;
await activate($('tab-catalog'));assert.equal(dbWorkspacePane,'details');const before=calls.length;
await $('bears-guide-copilot').onclick();assert.equal(activeTab,'warehouse');assert.equal(dbWorkspacePane,'worksheet');assert.equal($('copilot-request').closest('details.copilot').open,true);assert.equal(document.activeElement,$('copilot-request'));
assert.equal(dbCurrent(),w);assert.equal(worksheets.length,count);assert.equal(JSON.stringify(dbInput(w)),inputs);assert.equal(JSON.stringify(dbContext(w)),context);assert.equal(queryResult,result);assert.equal(dbProposal.record,proposal);assert.equal($('copilot-request').value,'retained request');assert.equal(calls.length,before);assert.equal(submissions.length,0);
""")

    def test_wap_guide_preserves_dirty_draft_and_original_operation_handles_across_contexts(self):
        self.javascript(r"""
const p=await readyDataops('p35-wap');assert.equal(p.root.parentElement,$('transformation'));assert.ok(!$('transformation-models').descendants().includes(p.root));assert.ok(!$('transformation-marts').descendants().includes(p.root));
// Panel DB, role and full context intentionally differ from the worksheet.
Object.assign(p,{database:'separate-db',schema:'separate-schema',role:'data_engineer',context:{...p.context,database_id:'separate-db',schema_id:'separate-schema',sql_catalog:'separate_catalog',sql_schema:'separate_schema',selected_role:'data_engineer',binding_revision:8,generation_id:'separate-generation',grant_revision:23},model:{model_id:'retained',version:7},modelDirty:true});
for(const [key,value] of Object.entries({'model-id':'retained','sql':'SELECT dirty FROM retained_source','target-schema':'separate-schema',name:'retained_target',target:'1',min:'11','not-null':'code',build:'original-build',audit:'original-audit',publication:'original-publication'}))$('p35-wap-'+key).value=value;
p.sources[0].alias.value='retained_source';p.sources[0].table.value='1';
p.op={state:'UNKNOWN',key:'original-key',context:structuredClone(p.context),operation:{id:'original-operation'}};p.requests=new Map([['original-request',p.op]]);p.actions=new Map([['original-action',p.op]]);p.pending=true;p.trackingPending=true;
const handles=[p.model,p.context,p.sources,p.op,p.requests,p.actions,p.op.operation],beforeFields=fields(p),panel=JSON.stringify([p.database,p.schema,p.role,p.modelDirty,p.pending,p.trackingPending]),worksheet=JSON.stringify(dbInput(dbCurrent())),result=queryResult;
await activate($('tab-bears'));const before=calls.length;
for(const id of ['bears-guide-wap','copilot-guide-wap']){await $(id).onclick();await tick();assert.equal(activeTab,'transformation');assert.equal(document.activeElement,p.root);assert.deepEqual(p.root.scrolled,{block:'nearest'});assert.equal(fields(p),beforeFields);assert.equal(JSON.stringify([p.database,p.schema,p.role,p.modelDirty,p.pending,p.trackingPending]),panel);for(const [i,handle] of [p.model,p.context,p.sources,p.op,p.requests,p.actions,p.op.operation].entries())assert.equal(handle,handles[i]);assert.equal(JSON.stringify(dbInput(dbCurrent())),worksheet);assert.equal(queryResult,result);assert.ok(!p.aiPending);assert.ok(!p.aiOrigin);}
assert.equal(calls.length,before);assert.equal(wapCalls().length,0);assert.equal(submissions.length,0);
""")

    def test_wap_guide_keeps_pending_proposal_and_first_open_only_reads_normal_metadata(self):
        self.javascript(r"""
await ready();const p=dataopsPanels.get('p35-wap');assert.ok(!p.started);const initial=calls.length;await $('bears-guide-wap').onclick();for(let i=0;i<12;i++)await tick();assert.equal(p.started,true);assert.ok(calls.length>initial);assert.ok(calls.slice(initial).every(c=>!c.options?.method&&!c.options?.body));assert.equal(proposalPosts().length,0);assert.equal(wapCalls().length,0);assert.ok(!p.aiPending);assert.ok(!p.aiOrigin);
""")
        self.javascript(r"""
const p=await selected();const pending=p.aiPending;assert.ok(!p.started);await activate($('tab-bears'));const before=calls.length,form=fields(p),context=p.context;await $('bears-guide-wap').onclick();await tick();assert.equal(p.aiPending,pending);assert.equal(p.context,context);assert.equal(fields(p),form);assert.ok(!p.started);assert.ok(!p.aiOrigin);assert.equal(calls.length,before);assert.equal(wapCalls().length,0);assert.equal(submissions.length,0);
""")

    def test_guide_role_rechecks_hidden_marts_denial_locales_and_native_focus(self):
        self.javascript(r"""
await ready();for(const role of ['data_engineer','bi_viewer']){currentRole=role;dbControls();for(const locale of ['ja','en']){setLanguage(locale);assert.ok(visibleText($('database-bears-workflows')).includes(t(dbWorkflowHelp)));assert.notEqual(i18n.ja[dbWorkflowHelp],dbWorkflowHelp);for(const id of ['bears-guide-marts','copilot-guide-marts']){assert.equal($(id).disabled,true);const reason=$(id+'-reason');assert.equal(reason.hidden,false);assert.match(reason.textContent,/admin/);assert.equal($(id).getAttribute('aria-describedby').includes(reason.id),true);}assert.equal($('bears-guide-wap').disabled,role==='bi_viewer');if(role==='bi_viewer')assert.match($('bears-guide-wap-reason').textContent,/E\/A/);}
await activate($('tab-bears'));const before=calls.length;await $('bears-guide-marts').onclick();await $('copilot-guide-marts').onclick();if(role==='bi_viewer')await $('bears-guide-wap').onclick();assert.equal(activeTab,'bears');assert.equal(calls.length,before);}
currentRole='admin';dbControls();const tab=$('transformation-view-marts');for(const flag of ['hidden','disabled']){tab[flag]=true;const before=calls.length;await $('bears-guide-marts').onclick();assert.equal(activeTab,'bears');assert.equal(calls.length,before);assert.equal($('bears-guide-marts').disabled,true);tab[flag]=false;}
dbWorkflowSync();assert.equal($('bears-guide-marts').disabled,false);
// Direct handler invocation after render must consult the current ceiling.
dbMaximumRole='bi_viewer';const before=calls.length;await $('bears-guide-wap').onclick();await $('bears-guide-marts').onclick();assert.equal(activeTab,'bears');assert.equal(calls.length,before);dbMaximumRole='admin';dbControls();
let clicks=0;tab.addEventListener('click',()=>clicks++);await $('bears-guide-marts').onclick();assert.equal(clicks,1);assert.equal(activeTab,'transformation');assert.equal(tab.getAttribute('aria-selected'),'true');assert.equal($('transformation-marts').hidden,false);assert.equal(document.activeElement,$('db-marts-panel'));
for(const id of ['bears-guide-copilot','bears-guide-wap','bears-guide-marts','copilot-guide-wap','copilot-guide-marts']){const n=$(id);assert.equal(n.tagName,'BUTTON');assert.equal(n.type,'button');assert.equal(typeof n.onclick,'function');assert.equal(n.onkeydown,undefined);assert.ok(n.getAttribute('aria-describedby'));}
assert.equal($('copilot-stage').disabled,true);assert.equal($('copilot-publish').disabled,true);assert.equal(submissions.length,0);assert.equal(proposalPosts().length,0);assert.equal(wapCalls().length,0);
""")

    def test_two_step_freezes_and_navigates_without_requests_or_form_changes(self):
        self.javascript(r'''
const p=await selected();const o=p.aiPending;assert.ok(Object.isFrozen(o));assert.ok(Object.isFrozen(o.context));assert.ok(Object.isFrozen(o.refs[0]));assert.equal(wapCalls().length,0);assert.equal(submissions.length,0);assert.equal(p.model,null);assert.equal($('p35-wap-sql').value,'SELECT * FROM source');assert.equal($('p35-wap-ai-load').disabled,true);assert.equal(o.context.sql_schema,'marts');assert.match(visibleText(p.aiBar),/Original proposal context/);assert.ok(visibleText(p.aiBar).includes(o.request));
request('new request');editSQL('SELECT worksheet');editDoctor('SELECT doctor');dbProposal.sources=[];dbProposalChanged();await activate($('tab-bears'));await activate($('tab-transformation'));assert.equal(p.aiPending,o);assert.equal(wapCalls().length,0);
''')

    def test_get_exact_dual_contexts_and_explicit_unsaved_editable_import(self):
        self.javascript(r'''
const p=await selected();await destination(p);const o=p.aiPending,d=draft(p);let seen;
route=async(path,options)=>{seen={path,options};const q=new URL(path,'http://local').searchParams;assert.deepEqual([...q.keys()].sort(),['context','destination_context']);assert.deepEqual(JSON.parse(q.get('context')),o.context);assert.deepEqual(JSON.parse(q.get('destination_context')),p.context);assert.equal(options.method,undefined);assert.equal(options.body,undefined);assert.deepEqual(options.headers,{'X-Shirokuma-Role':'admin'});assert.ok(path.includes('/'+o.id+'/wap-draft?'));return envelopeDraft(p,d);};
await $('p35-wap-ai-load').onclick();assert.ok(seen);assert.equal(wapCalls().length,1);assert.equal(p.aiPending,null);assert.equal(p.aiOrigin.id,o.id);assert.equal(p.aiOrigin.context.sql_schema,'marts');assert.equal(p.aiOrigin.destination_context.sql_schema,'sandbox');assert.equal(p.model,null);assert.equal(p.modelDirty,true);assert.equal($('p35-wap-model-id').value,'');assert.equal($('p35-wap-model-id').readOnly,false);assert.equal($('p35-wap-name').value,'');assert.equal($('p35-wap-target-schema').value,'sandbox');assert.equal($('p35-wap-min').value,1);assert.equal($('p35-wap-not-null').value,'');assert.equal($('p35-wap-sql').value,d.sql);assert.equal(p.sources.length,1);assert.equal(p.sources[0].alias.value,'binding_0');assert.deepEqual(dataopsRef(p.tables[Number(p.sources[0].table.value)]),dataopsRef({ref:o.refs[0]}));assert.equal($('p35-wap-build-action').disabled,true);assert.equal($('p35-wap-sql').disabled,false);assert.equal(submissions.length,0);
$('p35-wap-sql').value='SELECT edited';$('p35-wap-sql').oninput();p.sources[0].alias.value='editable';p.sources[0].alias.oninput();for(const lang of ['ja','en']){setLanguage(lang);dataopsRender(p);assert.equal($('p35-wap-sql').value,'SELECT edited');assert.equal(p.sources[0].alias.value,'editable');assert.ok(p.aiOrigin);}
''')

    def test_original_survives_new_worksheet_and_uses_original_id_and_request(self):
        self.javascript(r'''
const p=await selected();const o=p.aiPending;await addWorksheet();editSQL('SELECT different');request('different');editDoctor('SELECT doctor');await destination(p);assert.equal(p.aiPending,o);await load(p);assert.equal(p.aiOrigin.id,o.id);assert.equal(p.aiOrigin.request,o.request);assert.equal(p.aiOrigin.original_sql,o.original_sql);assert.equal($('sql').value,'SELECT different');assert.equal($('bear-query-sql').value,'SELECT doctor');assert.equal($('copilot-request').value,'different');
''')

    def test_raw_to_sandbox_and_engineer_same_role(self):
        self.javascript(r'''
const p=await selected(1,'data_engineer','raw');await destination(p);assert.equal(p.aiPending.context.sql_schema,'raw');assert.equal(p.context.selected_role,'data_engineer');await load(p);assert.ok(p.aiOrigin);assert.equal(wapCalls()[0].options.headers['X-Shirokuma-Role'],'data_engineer');
''')

    def test_post_only_or_viewer_has_no_handoff(self):
        self.javascript(r'''
await ready();await submit();assert.equal(dbProposal.wapRecord,null);assert.ok(!$('proposal-output').descendants().some(n=>n.id==='proposal-wap'));
const r=proposalRecord({context:{...dbContext(dbCurrent()),selected_role:'bi_viewer'}});dbProposal.record=r;dbProposal.wapRecord=r;dbProposalRender();assert.ok(!$('proposal-output').descendants().some(n=>n.id==='proposal-wap'));dataopsAiSelect();assert.equal(dataopsPanels.get('p35-wap').aiPending,undefined);
''')

    def test_fresh_get_non_success_never_exposes_handoff(self):
        self.javascript(r'''
await ready();for(const r of [pendingProposal(),pendingProposal({state:'UNKNOWN',inference_state:'UNKNOWN'}),pendingProposal({state:'CANCELED',inference_state:'CANCELED'}),pendingProposal({state:'FAILED',inference_state:'FAILED'})]){dbProposal.history=[r];route=async()=>wrap(r);await dbProposalHistory(0);assert.equal(dbProposal.record.state,r.state);assert.ok(!$('proposal-output').descendants().some(n=>n.id==='proposal-wap'));dataopsAiSelect();assert.ok(!dataopsPanels.get('p35-wap').aiPending);}
''')

    def test_context_denial_never_pivots_or_mutates_fields(self):
        for change in ["p.schema='raw'", "p.role='data_engineer'", "p.context={...p.context,database_id:'other'}", "p.context={...p.context,warehouse_id:'REMOTE'}", "p.context={...p.context,grant_revision:99}", "p.context={...p.context,generation_id:'other'}", "p.context={...p.context,binding_revision:99}", "p.context={...p.context,sql_catalog:'other'}", "p.context={...p.context,sql_schema:'other'}", "p.schemas.find(s=>s.id==='sandbox').capabilities.write=false", "p.schemas.find(s=>s.id==='schema').capabilities.query=false"]:
            with self.subTest(change=change):
                self.javascript("const p=await selected();await destination(p);" + change + r''';const before=fields(p),o=p.aiPending;await dataopsAiLoad(p);assert.equal(wapCalls().length,0);assert.equal(fields(p),before);assert.equal(p.aiPending,o);''')

    def test_foreign_private_operation_does_not_block_own_draft_but_owner_and_busy_guards_do(self):
        self.javascript(r'''
const p=await selected();await destination(p);const origin=p.aiPending,context=p.context,form=fields(p),before=calls.length;
const foreign={principal:JSON.stringify(['previous-owner',true]),state:'UNKNOWN',id:'private-old-id',key:'private-old-key',context:{...context,database_id:'private-old-db',sql_catalog:'private_old_catalog',sql_schema:'private_old_schema'},protected:true,operation:{state:'UNKNOWN',sql:'SELECT private_old_sql',result:{rows:[['private_old_result']]}}};p.op=foreign;const retained=JSON.stringify(foreign);
dataopsRender(p);assert.equal(dataopsAiPristine(p),true);assert.equal($('p35-wap-ai-load').disabled,false);assert.doesNotMatch(visibleText(p.root),/private-old|private_old/);assert.equal(p.receipt.children.length,0);assert.equal(p.op,foreign);assert.equal(JSON.stringify(foreign),retained);
// Current owner's unresolved operation still blocks the same empty draft.
p.op={...foreign,principal:dbPrincipal};const own=p.op;dataopsAiRender(p);assert.equal($('p35-wap-ai-load').disabled,true);await dataopsAiLoad(p);assert.equal(p.op,own);assert.equal(calls.length,before);
p.op=foreign;for(const flag of ['pending','trackingPending']){p[flag]=true;dataopsAiRender(p);assert.equal($('p35-wap-ai-load').disabled,true);await dataopsAiLoad(p);assert.equal(calls.length,before);p[flag]=false;}
assert.equal(fields(p),form);assert.equal(p.aiPending,origin);assert.equal(p.context,context);assert.ok(!p.aiOrigin);assert.equal(submissions.length,0);
dataopsAiRender(p);assert.equal($('p35-wap-ai-load').disabled,false);await load(p);assert.equal(wapCalls().length,1);assert.equal(p.aiOrigin.id,origin.id);assert.equal(p.op,foreign);assert.equal(JSON.stringify(foreign),retained);assert.doesNotMatch(visibleText(p.root),/private-old|private_old/);assert.equal(submissions.length,0);
''')

    def test_restored_unresolved_saved_panel_intents_block_explicit_and_forced_import(self):
        for state, operation_id in [('UNCONFIRMED', None), ('UNKNOWN', 'saved-operation')]:
            with self.subTest(state=state):
                self.javascript("const state=" + repr(state) + ",savedID=" + ('null' if operation_id is None else repr(operation_id)) + r''';
const p=await selected();await destination(p);assert.equal(dataopsAiPristine(p),true);p.op=null;
// Restore through owner storage with an original context differing from
// today's panel and from its current recovery selection.
const handle={version:1,panel:p.id,action:'manual-wap-build',key:'saved-original-key',context:{...p.context,database_id:'original-db',schema_id:'original-sandbox',sql_catalog:'original_catalog',sql_schema:'original_sandbox',binding_revision:9,generation_id:'original-generation',grant_revision:17},time:Date.now(),id:savedID,state,uncertain:state==='UNKNOWN'};
const key=dataopsRecoveryKey(dbPrincipal),raw=JSON.stringify([handle]);storage.set(key,raw);dataopsRecoveryOwners.delete(dbPrincipal);const store=dataopsRecoveryOwner(),original=store.ops[0];assert.equal(store.error,false);assert.equal(original.restored,true);assert.equal(original.operation,null);assert.equal(original.id,savedID);assert.equal(original.ambiguous,state==='UNKNOWN');
p.recoverySelection='different-selection';dataopsLoadModel(p,null);assert.equal(p.op,null);assert.equal(dataopsAiPristine(p),false);dataopsAiRender(p);assert.equal($('p35-wap-ai-load').disabled,true);
const origin=p.aiPending,context=p.context,form=fields(p),full9=JSON.stringify(p.context),requests=p.requests,actions=p.actions,selection=p.recoverySelection,before=calls.length;
await dataopsAiLoad(p);assert.equal(calls.length,before);assert.equal(wapCalls().length,0);assert.equal(submissions.length,0);assert.equal(p.aiPending,origin);assert.ok(!p.aiOrigin);assert.equal(p.context,context);assert.equal(JSON.stringify(p.context),full9);assert.equal(fields(p),form);assert.equal(p.requests,requests);assert.equal(p.actions,actions);assert.equal(p.op,null);assert.equal(p.recoverySelection,selection);assert.equal(dataopsRecoveryOwner(),store);assert.equal(store.ops[0],original);assert.equal(storage.get(key),raw);assert.deepEqual(dataopsRecoveryDTO(original),handle);
// Only a terminal, certain handle releases this prerequisite.
original.state='FINISHED';original.ambiguous=false;dataopsAiRender(p);assert.equal(dataopsRecoveryHeld(p),false);assert.equal(dataopsAiPristine(p),true);assert.equal($('p35-wap-ai-load').disabled,false);assert.equal(calls.length,before);
''')

    def test_target_schema_must_match_panel_with_explicit_bilingual_prerequisites(self):
        self.javascript(r'''
const p=await selected();await destination(p);const origin=p.aiPending,context=p.context;assert.equal(dataopsAiPristine(p),true);
$('p35-wap-target-schema').value='raw';dataopsChanged(p);const form=fields(p),before=calls.length;
for(const locale of ['ja','en']){setLanguage(locale);dataopsAiRender(p);assert.equal($('p35-wap-ai-load').disabled,true);const copy=visibleText(p.aiBar);for(const word of ['data_engineer/admin','SANDBOX','Refresh panel context','New model','Load proposal draft'])assert.ok(copy.includes(word),word);assert.ok(copy.includes(locale==='ja'?'対象スキーマ':'target schema'));assert.ok(copy.includes(locale==='ja'?'既定 SQL':'default SQL'));assert.ok(copy.includes(locale==='ja'?'既定ソース1件':'one default source'));await dataopsAiLoad(p);assert.equal(fields(p),form);assert.equal(p.aiPending,origin);assert.equal(p.context,context);assert.ok(!p.aiOrigin);assert.equal(calls.length,before);}
// Matching the field manually enables only the existing explicit Load.
$('p35-wap-target-schema').value=p.schema;dataopsChanged(p);dataopsAiRender(p);assert.equal($('p35-wap-ai-load').disabled,false);assert.equal(p.aiPending,origin);assert.ok(!p.aiOrigin);assert.equal(calls.length,before);assert.equal(submissions.length,0);assert.equal(wapCalls().length,0);
await load(p);assert.equal(wapCalls().length,1);assert.equal(p.aiPending,null);assert.equal(p.aiOrigin.id,origin.id);assert.equal(submissions.length,0);
''')

    def test_all_nonpristine_fields_block_before_get_without_overwriting(self):
        changes=["p.model={model_id:'saved',version:1}", "$('p35-wap-models').value='0'", "$('p35-wap-model-id').value='draft'", "$('p35-wap-name').value='target'", "$('p35-wap-target').value='0'", "$('p35-wap-not-null').value='n'", "$('p35-wap-target-schema').value='raw'", "$('p35-wap-sql').value='SELECT 2'", "$('p35-wap-min').value=2", "p.sources[0].alias.value='different'", "p.sources[0].table.value='0'", "dataopsAddSource(p,'other')", "$('p35-wap-build').value='build'", "$('p35-wap-audit').value='audit'", "$('p35-wap-publication').value='pub'", "$('p35-wap-metadata-id').value='old'", "p.metadata={id:'old'}", "p.pending=true", "p.trackingPending=true", "p.op={state:'UNKNOWN',principal:dbPrincipal}", "p.requests=new Map([['old',{operation:{state:'RUNNING'}}]])", "p.actions=new Map([['old',{operation:{state:'UNKNOWN'}}]])", "p.inventoryLoading=true", "p.inventoryReady=false", "p.inventoryContext={...p.context,grant_revision:99}"]
        for change in changes:
            with self.subTest(change=change):
                self.javascript("const p=await selected();await destination(p);"+change+r''';const before=fields(p);await dataopsAiLoad(p);assert.equal(wapCalls().length,0);assert.equal(fields(p),before);assert.ok(p.aiPending);''')

    def test_terminal_history_and_new_dirty_flag_are_preserved(self):
        self.javascript(r'''
const p=await selected();await destination(p);p.modelDirty=true;p.op={state:'FINISHED',key:'keep'};p.actions=new Map([['old',{operation:{state:'REJECTED'}}]]);const op=p.op,actions=p.actions;await load(p);assert.ok(p.aiOrigin);assert.equal(p.op,op);assert.equal(p.actions,actions);assert.equal(p.op.key,'keep');
''')

    def test_closed_dto_identity_input_dates_refs_aliases_and_labels(self):
        mutations=["d.extra=true", "d.version=2", "d.proposal_id='other'", "d.source_context.sql_schema='other'", "d.destination_context.grant_revision++", "d.original_sql+='changed'", "d.request+='changed'", "d.proposal_created_at='2026-11-01T01:00:01Z'", "d.prepared_at='yesterday'", "d.prepared_at='2026-11-01'", "d.sql='あ'.repeat(21846)", "d.sql=' '", "d.sources=[]", "d.sources.push(structuredClone(d.sources[0]))", "d.sources[0].alias='Upper'", "d.sources[0].alias='a-b'", "d.sources[0].ref.extra=true", "d.sources[0].ref.object_id='other'", "d.sources[0].ref.incarnation++", "d.sources[0].ref.generation_id='old'", "d.sources[0].schema='other'", "d.sources[0].table='other'", "d.sources[0].extra=true", "delete d.sources[0].ref", "d.sources[0].ref.incarnation='1'", "d.sources[0].alias=null"]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.javascript("const p=await selected();await destination(p);const d=draft(p);"+mutation+r''';const before=fields(p);await load(p,d);assert.equal(fields(p),before);assert.ok(!p.aiOrigin);assert.ok(p.aiPending);assert.equal(submissions.length,0);''')

    def test_envelope_is_closed_and_original_context_exact(self):
        for mutation in ["reply.context.schema_id='sandbox'", "reply.private='leak'", "delete reply.context"]:
            self.javascript("const p=await selected();await destination(p);const reply=envelopeDraft(p);"+mutation+r''';const before=fields(p);route=async()=>reply;await dataopsAiLoad(p);assert.equal(fields(p),before);assert.ok(p.aiPending);''')

    def test_current_used_inventory_is_required_no_fallback_or_fabrication(self):
        for mutation in ["p.tables=[]", "p.tables[0].ref.incarnation++", "p.tables[0].ref.binding_revision++", "p.tables[0].physical_name='different'", "p.tables[0].kind='VIEW'", "p.tables.push(structuredClone(p.tables[0]))", "p.tables[0].ref.generation_id='old'", "p.tables[0].capabilities.resolve=false", "p.tables.unshift({...structuredClone(p.tables[0]),ref:{...p.tables[0].ref,binding_revision:99}})"]:
            self.javascript("const p=await selected();await destination(p);const d=draft(p);"+mutation+r''';const before=fields(p),inventoryBefore=JSON.stringify(p.tables);await load(p,d);assert.equal(fields(p),before);assert.equal(JSON.stringify(p.tables),inventoryBefore);assert.ok(p.aiPending);''')

    def test_unused_selected_view_and_missing_object_do_not_block(self):
        self.javascript(r'''
const p=await selected(3);await destination(p);assert.equal(p.aiPending.refs.length,3);p.tables=p.tables.filter(o=>o.ref.object_id==='object-0');await load(p);assert.ok(p.aiOrigin);assert.equal(p.sources.length,1);assert.equal(p.tables.length,1);
''')

    def test_used_view_rejected_with_fixed_copy(self):
        self.javascript(r'''
const p=await selected();await destination(p);const d=draft(p);d.sources[0]={alias:'binding_1',ref:structuredClone(p.aiPending.refs[1]),schema:p.aiPending.labels[1].schema,table:p.aiPending.labels[1].table};const before=fields(p);await load(p,d);assert.equal(fields(p),before);assert.match(p.aiNotice,/supported tables/);
''')

    def test_late_success_and_error_ignored_after_state_changes(self):
        changes=["$('p35-wap-sql').value='SELECT edited';$('p35-wap-sql').oninput()", "p.sources[0].alias.value='changed';p.sources[0].alias.oninput()", "p.role='data_engineer';dataopsChanged(p)", "dbPrincipal=JSON.stringify(['other',true]);dbControls()", "currentRole='data_engineer';dbControls()", "dbMaximumRole='data_engineer';dbControls()", "++dbAuthorizationVersion;dbControls()", "p.context={...p.context,sql_schema:'other'};dataopsRender(p)", "p.context={...p.context,grant_revision:99};dataopsRender(p)", "p.context={...p.context,generation_id:'old'};dataopsRender(p)", "p.context={...p.context,warehouse_id:'other'};dataopsRender(p)", "p.tables=[];dataopsRender(p)", "p.tables=structuredClone(p.tables)", "p.schemas=structuredClone(p.schemas)", "p.inventoryLoading=true;dataopsRender(p)", "await addWorksheet()", "await activate($('tab-bears'));await activate($('tab-transformation'))", "dataopsAiClear(p)", "$('p35-wap-sql').value='SELECT edited';$('p35-wap-sql').oninput();$('p35-wap-sql').value='SELECT * FROM source';$('p35-wap-sql').oninput()", "currentRole='data_engineer';dbControls();currentRole='admin';dbControls()", "p.pending=true;dataopsRender(p);p.pending=false;dataopsRender(p)"]
        for change in changes:
            for error in [False,True]:
                with self.subTest(change=change,error=error):
                    self.javascript("const p=await selected();await destination(p);const reply=envelopeDraft(p),wait=deferred();route=async()=>wait.promise;const loading=dataopsAiLoad(p);await tick();"+change+";const before=fields(p);"+("wait.reject(Error('private late parser'));" if error else "wait.resolve(reply);")+r'''await loading;assert.equal(fields(p),before);assert.ok(!p.aiOrigin);assert.doesNotMatch(visibleText(p.aiBar),/private late parser/);''')

    def test_old_finally_cannot_finish_new_token_or_selection(self):
        self.javascript(r'''
const p=await selected();await destination(p);const reply=envelopeDraft(p),o=p.aiPending,a=deferred(),b=deferred();route=async()=>a.promise;const first=dataopsAiLoad(p);await tick();dataopsAiClear(p);p.aiPending=o;dataopsAiRender(p);route=async()=>b.promise;const second=dataopsAiLoad(p);await tick();const token=p.aiToken;assert.ok(token);a.resolve(reply);await first;assert.equal(p.aiToken,token);assert.equal(p.aiPending,o);b.resolve(reply);await second;assert.ok(p.aiOrigin);
''')

    def test_clear_is_harmless_and_new_model_preserves_pending_clears_loaded(self):
        self.javascript(r'''
const p=await selected();await destination(p);const o=p.aiPending;dataopsLoadModel(p,null);assert.equal(p.aiPending,o);const before=fields(p);dataopsAiClear(p);assert.equal(fields(p),before);assert.equal(p.aiPending,null);p.aiPending=o;dataopsAiRender(p);await load(p);assert.ok(p.aiOrigin);dataopsLoadModel(p,null);assert.equal(p.aiOrigin,null);assert.equal($('p35-wap-sql').value,'SELECT * FROM source');
''')

    def test_origin_literal_html_safe_and_immutable_separate_from_editor(self):
        self.javascript(r'''
const p=await selected();await destination(p);const o=dataopsFreeze({...p.aiPending,request:'<img src=x onerror=evil()>',original_sql:"SELECT '<script>evil()</script>'"});p.aiPending=o;await load(p);assert.ok(p.aiOrigin);assert.equal(p.aiBar.querySelector('img'),undefined);assert.equal(p.aiBar.querySelector('script'),undefined);assert.ok(visibleText(p.aiBar).includes(o.request));assert.ok(visibleText(p.aiBar).includes(o.original_sql));$('p35-wap-sql').value='SELECT changed';dataopsChanged(p);assert.equal(p.aiOrigin.original_sql,o.original_sql);assert.ok(Object.isFrozen(p.aiOrigin));
''')

    def test_auth_invalidation_hides_pending_and_loaded_origin(self):
        for imported in [False,True]:
            for change in ["dbPrincipal=JSON.stringify(['other',true])", "dbPrincipal=JSON.stringify(['owner',false])", "dbMaximumRole='bi_viewer'", "currentRole='bi_viewer'", "++dbAuthorizationVersion"]:
                self.javascript("const p=await selected();await destination(p);"+("await load(p);" if imported else "")+change+r''';dbControls();assert.ok(!p.aiPending);assert.ok(!p.aiOrigin);assert.equal(p.aiBar.hidden,true);''')

    def test_fixed_preparation_errors_no_server_parser_text_and_safe_explicit_retry(self):
        for code in ['AI_WAP_UNAVAILABLE','AI_WAP_UNSUPPORTED','AI_METADATA_CHANGED','AI_WAP_CONTEXT_MISMATCH','INTERNAL_PRIVATE']:
            self.javascript("const p=await selected();await destination(p);const before=fields(p);route=async()=>({http:409,body:{code:"+repr(code)+r''',message:'private parser/provider raw detail'}});await dataopsAiLoad(p);assert.equal(fields(p),before);assert.ok(p.aiPending);assert.doesNotMatch(visibleText(p.aiBar),/private parser|provider raw/);await load(p);assert.ok(p.aiOrigin);assert.equal(wapCalls().length,2);''')

    def test_auth_error_clears_protected_selection_without_form_mutation(self):
        for code in ['SQL_PERMISSION_DENIED','AUTHENTICATION_REQUIRED','ROLE_FORBIDDEN','DB_ACCESS_REVOKED','AI_ACCESS_REVOKED']:
            self.javascript("const p=await selected();await destination(p);const before=fields(p);route=async()=>({http:403,body:{code:"+repr(code)+r''',message:'secret'}});await dataopsAiLoad(p);assert.equal(fields(p),before);assert.equal(p.aiPending,null);assert.ok(!p.aiOrigin);assert.equal(p.aiBar.hidden,true);''')

    def test_http_auth_status_and_known_codes_clear_only_matching_provenance(self):
        replies=["({http:403,body:{code:'PRIVATE_UNKNOWN',message:'private'}})", "({http:401,body:{message:'private'}})", "({http:403,get body(){throw SyntaxError('private malformed JSON');}})", "({http:401,get body(){throw SyntaxError('private malformed JSON');}})", "({http:409,body:{error:{code:'SQL_PERMISSION_DENIED'}}})", "({http:409,body:{code:'AUTHENTICATION_REQUIRED'}})", "({http:409,body:{code:'STALE_CONTEXT'}})", "({http:409,body:{code:'AI_ACCESS_REVOKED'}})"]
        for reply in replies:
            for matching in [False,True]:
                with self.subTest(reply=reply,matching=matching):
                    self.javascript("const p=await selected();await destination(p);const o=p.aiPending;"+("p.aiOrigin=dataopsFreeze({...o,destination_context:p.context});" if matching else "p.aiOrigin=dataopsFreeze({...o,id:'unrelated',destination_context:p.context});")+"const previous=p.aiOrigin,before=fields(p);p.op={state:'FINISHED',receipt:{keep:true}};const op=p.op;route=async()=>"+reply+r''';await dataopsAiLoad(p);assert.equal(p.aiPending,null);assert.equal(fields(p),before);assert.equal(p.op,op);assert.equal(p.invalid,false);'''+("assert.equal(p.aiOrigin,null);assert.equal(p.aiBar.hidden,true);" if matching else "assert.equal(p.aiOrigin,previous);assert.equal(p.aiBar.hidden,false);"))

    def test_auth_error_does_not_clear_same_id_with_different_source_context(self):
        self.javascript(r'''
const p=await selected();await destination(p);p.aiOrigin=dataopsFreeze({...p.aiPending,context:{...p.aiPending.context,schema_id:'raw',sql_schema:'raw'},destination_context:p.context});const old=p.aiOrigin,before=fields(p);route=async()=>({http:403,body:{code:'UNKNOWN'}});await dataopsAiLoad(p);assert.equal(p.aiOrigin,old);assert.equal(p.aiPending,null);assert.equal(fields(p),before);
''')

    def test_late_auth_reply_cannot_clear_new_selection_or_origin(self):
        for malformed in [False,True]:
            self.javascript("const p=await selected();await destination(p);const old=p.aiPending,wait=deferred();route=async()=>wait.promise;const loading=dataopsAiLoad(p);await tick();dataopsAiClear(p);p.aiPending=dataopsFreeze({...old,id:'new-proposal'});p.aiOrigin=dataopsFreeze({...p.aiPending,destination_context:p.context});dataopsAiRender(p);const pending=p.aiPending,origin=p.aiOrigin,before=fields(p);wait.resolve("+("{http:403,get body(){throw SyntaxError('private');}}" if malformed else "{http:401,body:{code:'SQL_PERMISSION_DENIED'}}")+r''');await loading;assert.equal(p.aiPending,pending);assert.equal(p.aiOrigin,origin);assert.equal(fields(p),before);''')

    def test_terminal_options_with_empty_selection_allowed_and_preserved(self):
        self.javascript(r'''
const p=await selected();await destination(p);const options=[];for(const kind of ['build','audit','publication']){const n=$('p35-wap-'+kind);n.add(new Option('Old '+kind,'old-'+kind));n.value='';const old=n.children;options.push(old);Object.defineProperty(n,'options',{get:()=>({length:old.length,[Symbol.iterator]:()=>old[Symbol.iterator]()})});}
p.op={state:'FINISHED',receipt:{keep:true}};const op=p.op;p.actions=new Map([['done',{operation:{state:'FINISHED'}}]]);await load(p);assert.ok(p.aiOrigin);assert.equal(p.op,op);for(const [i,kind] of ['build','audit','publication'].entries()){const n=$('p35-wap-'+kind);assert.equal(n.children,options[i]);assert.equal(n.value,'');}
''')

    def test_native_details_open_survives_render_import_resets_other_origin(self):
        self.javascript(r'''
const p=await selected();await destination(p);const details=p.aiDetails;details.open=true;dataopsRender(p);dbProposalSync();assert.equal(p.aiDetails,details);assert.equal(p.aiDetails.open,true);await load(p);assert.equal(p.aiDetails,details);assert.equal(p.aiDetails.open,true);for(const language of ['ja','en']){setLanguage(language);dataopsRender(p);assert.equal(p.aiDetails,details);assert.equal(p.aiDetails.open,true);}
const origin=p.aiOrigin;dataopsLoadModel(p,null);assert.ok(!p.aiOrigin);assert.equal(p.aiDetails,null);p.aiPending=origin;dataopsAiRender(p);assert.notEqual(p.aiDetails,details);assert.ok(!p.aiDetails.open);p.aiDetails.open=true;const current=p.aiDetails;p.aiPending=dataopsFreeze({...origin,id:'different'});dataopsAiRender(p);assert.notEqual(p.aiDetails,current);assert.ok(!p.aiDetails.open);dbMaximumRole='bi_viewer';dbControls();assert.equal(p.aiDetails,null);
''')

    def test_explicit_same_draft_save_preserves_origin_then_manual_build_and_audit(self):
        self.javascript(r'''
const p=await selected();await destination(p);await load(p);const origin=p.aiOrigin,details=p.aiDetails;const unsavedCaption='Imported as an unsaved draft. Review and edit, then explicitly save a model version before building.',savedCaption='Original proposal (read only). Review the saved model version using the normal Manual WAP actions.';assert.notEqual(i18n.ja[savedCaption],savedCaption);const checkCaption=saved=>{const sql=$('p35-wap-sql').value;for(const locale of ['en','ja']){setLanguage(locale);dataopsRender(p);assert.ok(visibleText(p.aiBar).includes(t(saved?savedCaption:unsavedCaption)));assert.ok(!visibleText(p.aiBar).includes(t(saved?unsavedCaption:savedCaption)));assert.equal($('p35-wap-sql').value,sql);assert.equal(p.aiOrigin,origin);assert.ok(Object.isFrozen(p.aiOrigin));assert.doesNotMatch(visibleText(p.aiBar),/AI[- ]approved|AI承認/);}};checkCaption(false);details.open=true;$('p35-wap-model-id').value='reviewed';$('p35-wap-name').value='reviewed_target';$('p35-wap-sql').value='SELECT edited FROM binding_0';dataopsChanged(p);
const mutations=[];route=async(path,options)=>{const body=JSON.parse(options.body);mutations.push(path);if(path==='/api/v1/wap/models')return {context:body.context,model:{...body,version:1}};return accepted(body.context,{id:'operation-'+mutations.length,state:'FINISHED',committed:true,result:path.endsWith('/build')?{build_id:'manual-build'}:{audit_id:'manual-audit'},cleanup_error:null});};
assert.equal($('p35-wap-build-action').disabled,true);await $('p35-wap-save').onclick();assert.deepEqual(mutations,['/api/v1/wap/models']);assert.equal(p.aiOrigin,origin);assert.equal(p.aiDetails,details);assert.equal(details.open,true);assert.equal(p.model.model_id,'reviewed');assert.equal(p.modelDirty,false);checkCaption(true);assert.equal($('p35-wap-build-action').disabled,false);assert.equal(p.aiOrigin.original_sql,origin.original_sql);assert.equal($('p35-wap-sql').value,'SELECT edited FROM binding_0');await $('p35-wap-build-action').onclick();assert.deepEqual(mutations,['/api/v1/wap/models','/api/v1/wap/build']);assert.equal(p.aiOrigin,origin);checkCaption(true);await $('p35-wap-audit-action').onclick();assert.deepEqual(mutations,['/api/v1/wap/models','/api/v1/wap/build','/api/v1/wap/audit']);assert.equal(p.aiOrigin,origin);assert.equal(details.open,true);
checkCaption(true);$('p35-wap-sql').value='SELECT further_edit FROM binding_0';$('p35-wap-sql').oninput();checkCaption(false);assert.equal($('p35-wap-sql').value,'SELECT further_edit FROM binding_0');assert.equal(p.aiDetails,details);assert.equal(details.open,true);const saved=p.model;p.aiPending=origin;dataopsLoadModel(p,saved);assert.equal(p.aiOrigin,null);assert.equal(p.aiPending,origin);assert.notEqual(p.aiDetails,details);assert.ok(!p.aiDetails.open);dataopsLoadModel(p,null);assert.equal(p.aiPending,origin);assert.ok(!p.aiOrigin);
''')

    def test_source_primary_schema_and_destination_query_write_required(self):
        for change in ["p.schemas.find(s=>s.id==='schema').capabilities.query=false", "p.schemas.find(s=>s.id==='schema').sql_name='renamed'", "p.schemas=p.schemas.filter(s=>s.id!=='schema')", "p.schemas.find(s=>s.id==='sandbox').capabilities.query=false", "p.schemas.find(s=>s.id==='sandbox').capabilities.write=false"]:
            with self.subTest(change=change):
                self.javascript("const p=await selected();await destination(p);const old=p.aiPending;p.aiPending=dataopsFreeze({...old,refs:[{...old.refs[0],schema_id:'raw'}],labels:[{...old.labels[0],schema:'raw'}]});"+change+r''';const before=fields(p);await dataopsAiLoad(p);assert.equal(wapCalls().length,0);assert.equal(fields(p),before);assert.ok(p.aiPending);''')

    def test_preserves_sql_results_b1_and_old_models_history_receipts(self):
        self.javascript(r'''
const p=await selected();await destination(p);editSQL('SELECT worksheet keep');editDoctor('SELECT Doctor keep');queryResult={columns:['keep'],rows:[[42]]};const result=queryResult;const worksheet=JSON.stringify(dbInput(dbCurrent())),intents=JSON.stringify(dbProposal.intents),history=JSON.stringify(dbHistory),r=JSON.stringify(dbProposal.history);p.models=[{model_id:'old',version:3}];p.requests=new Map([['old',{operation:{state:'FINISHED',receipt:{keep:true}}}]]);const oldModels=p.models,requests=p.requests;await load(p);assert.ok(p.aiOrigin);assert.equal(p.models,oldModels);assert.equal(p.requests,requests);assert.equal(queryResult,result);assert.equal(JSON.stringify(dbInput(dbCurrent())),worksheet);assert.equal(JSON.stringify(dbProposal.intents),intents);assert.equal(JSON.stringify(dbProposal.history),r);assert.equal(JSON.stringify(dbHistory),history);assert.equal($('sql').value,'SELECT worksheet keep');assert.equal($('bear-query-sql').value,'SELECT Doctor keep');assert.equal(calls.filter(c=>c.options?.method==='POST').length,0);for(const id of ['copilot-stage','copilot-publish'])assert.equal($(id).disabled,true);
''')


if __name__ == '__main__':
    unittest.main()
