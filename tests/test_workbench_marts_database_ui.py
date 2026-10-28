"""Managed Mart production handlers in the offline Node DOM/fetch harness."""
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_workbench_database_ui as harness

SETUP = r'''
const p=dbMarts;
const source={database_id:'db',schema_id:'raw',object_id:'input',incarnation:1};
const target={database_id:'db',schema_id:'schema',physical_name:'daily'};
let model={database_id:'db',model_id:'explicit',version:3,sources:{a:source,b:source},target,min_rows:2,not_null:['id']};
let saved=[],runs=[],gate=null,loadGate=null,lost=false,conflict=false,watchReject=false,wrong=null;
route=async(path,options)=>{
 const url=new URL('http://local'+path),body=options?.body?JSON.parse(options.body):null,context=body?.context||JSON.parse(url.searchParams.get('context')||'null');
 if(url.pathname==='/api/v1/wap/models')return url.searchParams.has('model_id')?{context,model:structuredClone(model)}:{context,items:[structuredClone(model)]};
 if(!url.pathname.startsWith('/api/v1/marts'))return {};
 if(body&&(/\/(checks|refreshes)$/.test(url.pathname))){
  if(gate)await gate.promise;if(watchReject)return {http:409,body:{code:'WATCH_REQUIRED'}};
  const run={id:'run-1',mart_id:url.pathname.split('/')[4],mart_revision:body.expected_revision,context,owner:'owner',kind:url.pathname.endsWith('/checks')?'check':'refresh',status:'succeeded',committed:!url.pathname.endsWith('/checks'),result:{changed:true,publication_state:'published',baseline_persisted:true,history_complete:true}};
  runs=[run];if(lost)throw Error('response lost');return {context,run:{...run,...wrong}};
 }
 if(url.pathname.endsWith('/runs'))return {context,items:structuredClone(runs)};
 if(url.pathname.includes('/runs/')){if(gate)await gate.promise;return {context,run:{...structuredClone(runs[0]),...wrong}};}
 if(body){if(conflict)return {http:409,body:{code:'REVISION_CONFLICT'}};const mart={id:body.expected_revision?url.pathname.split('/').at(-1):'mart-1',database_id:context.database_id,context,owner:'owner',revision:body.expected_revision+1,definition:structuredClone(body.definition),current_target_ref:saved[0]?.current_target_ref||null,last_success_at:'previous',last_success_baseline:null};saved=saved.filter(m=>m.id!==mart.id).concat(mart);return {context,mart};}
 if(url.pathname==='/api/v1/marts')return {context,items:structuredClone(saved.filter(m=>m.database_id===context.database_id))};
 if(loadGate)await loadGate.promise;return {context,mart:{...structuredClone(saved.find(m=>m.id===url.pathname.split('/').at(-1))),...wrong}};
};
const posts=()=>calls.filter(c=>c.path.startsWith('/api/v1/marts')&&c.options?.method==='POST');
async function openMarts(){await boot([input(),input('b')]);p.started=true;await dbMartsRefresh();$('db-marts-schema').value='schema';await $('db-marts-schema').onchange();assert.equal(p.invalid,false);}
async function createMart(watch='all'){await openMarts();$('db-marts-name').value='daily';$('db-marts-model-id').value='explicit';$('db-marts-version').value='3';await dbMartsLoadModel();$('db-marts-watch').value=watch;await dbMartsSave();assert.equal(p.mart.id,'mart-1');assert.equal(dbMartsMatches(),true);}
function protectedEmpty(){assert.equal(p.model,null);assert.equal(p.mart,null);assert.deepEqual(p.history,[]);assert.equal($('db-marts-model-detail').textContent,'');assert.equal($('db-marts-definition-detail').textContent,'');assert.equal($('db-marts-observe').disabled,true);}
'''


class MartsUI(unittest.TestCase):
    def javascript(self, body):
        harness.DatabaseUI.javascript(self, SETUP + body)

    def test_explicit_model_full_context_header_quality_and_current_target(self):
        self.javascript(r'''
await createMart();const save=posts()[0],body=JSON.parse(save.options.body);
assert.deepEqual(body.context,p.context);assert.equal(Object.keys(body.context).length,9);assert.equal(body.expected_revision,0);assert.equal(body.definition.model_id,'explicit');assert.equal(body.definition.version,3);assert.deepEqual(body.definition.source_refs,[source]);assert.equal(body.definition.min_rows,undefined);assert.equal(save.options.headers['X-Idempotency-Key'].length>0,true);assert.equal(save.options.headers['Idempotency-Key'],undefined);assert.ok($('db-marts-model-detail').textContent.includes('not_null'));
saved[0].current_target_ref={database_id:'db',schema_id:'schema',object_id:'published',incarnation:2};$('db-marts-list').value='mart-1';$('db-marts-list').onchange();await dbMartsLoad();$('db-marts-name').value='new';await dbMartsSave();assert.deepEqual(JSON.parse(posts().at(-1).options.body).definition.target,saved[0].current_target_ref);assert.equal(worksheets.length,2);assert.equal(dbCurrent().sql,'SELECT 1');
''')

    def test_default_unconditional_conditional_check_history_observe(self):
        self.javascript(r'''
await createMart();assert.equal($('db-marts-check-changes').checked,false);await dbMartsSubmit('refresh');assert.equal(JSON.parse(posts().at(-1).options.body).check_changes,false);assert.equal(p.run.details.committed,true);assert.equal(p.mart.last_success_at,'previous');
await dbMartsSubmit('check');assert.equal(JSON.parse(posts().at(-1).options.body).check_changes,undefined);assert.equal(p.run.details.committed,false);assert.equal(p.mart.last_success_at,'previous');
$('db-marts-check-changes').checked=true;await dbMartsSubmit('refresh');assert.equal(JSON.parse(posts().at(-1).options.body).check_changes,true);await dbMartsHistory();assert.equal(p.history.length,1);const count=posts().length;await $('db-marts-history').descendants().find(n=>n.tagName==='BUTTON').onclick();assert.equal(posts().length,count);assert.ok(calls.at(-1).path.includes('/runs/run-1?'));
''')

    def test_new_mart_published_locator_model_uses_current_ref_and_preserves_version(self):
        self.javascript(r'''
model.current_target_ref={database_id:'db',schema_id:'schema',object_id:'published',incarnation:2};
await createMart();const definition=JSON.parse(posts()[0].options.body).definition;
assert.deepEqual(definition.target,model.current_target_ref);assert.equal(definition.model_id,'explicit');assert.equal(definition.version,3);
assert.deepEqual(p.model.target,target);assert.deepEqual(JSON.parse($('db-marts-model-detail').textContent).target,model.current_target_ref);
assert.ok(calls.filter(c=>c.options?.method==='POST').every(c=>c.path.startsWith('/api/v1/marts')));
saved[0].definition.target=structuredClone(target);saved[0].current_target_ref=null;
$('db-marts-list').value='mart-1';$('db-marts-list').onchange();await dbMartsLoad();$('db-marts-name').value='published-update';await dbMartsSave();
assert.deepEqual(JSON.parse(posts().at(-1).options.body).definition.target,model.current_target_ref);
assert.deepEqual(JSON.parse($('db-marts-model-detail').textContent).target,model.current_target_ref);assert.deepEqual(p.model.target,target);
saved[0].current_target_ref={...model.current_target_ref,object_id:'mart-current',incarnation:4};
$('db-marts-list').value='mart-1';$('db-marts-list').onchange();await dbMartsLoad();$('db-marts-name').value='updated';await dbMartsSave();
assert.deepEqual(JSON.parse(posts().at(-1).options.body).definition.target,saved[0].current_target_ref);
assert.deepEqual(JSON.parse($('db-marts-model-detail').textContent).target,saved[0].current_target_ref);assert.deepEqual(p.model.target,target);
''')

    def test_null_model_binding_uses_locator_and_foreign_binding_blocks_save(self):
        self.javascript(r'''
model.current_target_ref=null;await createMart();assert.deepEqual(JSON.parse(posts()[0].options.body).definition.target,target);
assert.deepEqual(JSON.parse($('db-marts-model-detail').textContent).target,target);
for(const bad of [{database_id:'other',schema_id:'schema',object_id:'foreign',incarnation:1},{database_id:'db',schema_id:'raw',object_id:'foreign',incarnation:1}]){
 p.mart=null;p.loadedID=null;p.model={...p.model,current_target_ref:bad};const count=posts().length;await dbMartsSave();assert.equal(posts().length,count);
}
''')

    def test_manual_only_watch_required_never_unchanged(self):
        self.javascript(r'''
await createMart('manual');assert.deepEqual(p.mart.definition.source_refs,[]);await dbMartsSubmit('refresh');assert.equal(p.run.status,'succeeded');watchReject=true;await dbMartsSubmit('check');assert.equal(p.run.status,'rejected');assert.equal(p.run.rejection,'WATCH_REQUIRED');assert.equal(p.run.id,null);assert.equal(p.run.details,null);assert.equal(p.run.payload,null);assert.ok(p.status.textContent.includes('WATCH_REQUIRED'));assert.equal(p.mart.last_success_at,'previous');assert.equal(dbMartsHeld(),false);
''')

    def test_unknown_same_frozen_key_and_no_new_refresh(self):
        self.javascript(r'''
await createMart();lost=true;await dbMartsSubmit('refresh');const r=p.run,first=posts().at(-1),key=r.key;assert.equal(r.status,'unknown');assert.ok(Object.isFrozen(r.payload));assert.ok(Object.isFrozen(r.payload.context));await dbMartsSubmit('refresh');assert.equal(posts().at(-1),first);lost=false;await dbMartsObserve();const next=posts().at(-1);assert.equal(next.options.headers['X-Idempotency-Key'],key);assert.equal(next.options.body,first.options.body);assert.equal(r.id,'run-1');assert.equal(r.payload,null);const count=posts().length;await dbMartsObserve();assert.equal(posts().length,count);
''')

    def test_context_ready_before_choice_same_name_and_return(self):
        self.javascript(r'''
await createMart();await dbMartsSubmit('refresh');await dbMartsHistory();const r=p.run,sales=structuredClone(saved[0]);inventory.push({...structuredClone(inventory[0]),id:'finance',sql_catalog:'finance'});const fs={...structuredClone(schemas[0]),id:'fm',database_id:'finance'};schemasRoute=async path=>({items:path.includes('/finance/')?[fs]:schemas});
$('db-marts-database').value='finance';await $('db-marts-database').onchange();protectedEmpty();$('db-marts-schema').value='fm';await $('db-marts-schema').onchange();assert.equal(p.invalid,false);protectedEmpty();let count=calls.length;await dbMartsObserve();assert.equal(calls.length,count);
const foreign={...sales,id:'foreign',database_id:'finance',context:structuredClone(p.context),definition:{...sales.definition,target:{database_id:'finance',schema_id:'fm',physical_name:'daily'}}};saved.push(foreign);model={...model,database_id:'finance',target:foreign.definition.target};await dbMartsRefresh();$('db-marts-list').value='foreign';$('db-marts-list').onchange();await dbMartsLoad();assert.equal(p.mart.id,'foreign');assert.equal(dbMartsAssociated(r),false);count=calls.length;await dbMartsObserve();assert.equal(calls.length,count);
$('db-marts-database').value='db';await $('db-marts-database').onchange();$('db-marts-schema').value='schema';await $('db-marts-schema').onchange();protectedEmpty();model={...model,database_id:'db',target};$('db-marts-list').value='mart-1';$('db-marts-list').onchange();await dbMartsLoad();assert.equal(dbMartsAssociated(r),true);await dbMartsObserve();assert.ok(r.details);
''')

    def test_late_response_safe_handle_and_grant_purge(self):
        self.javascript(r'''
await createMart();gate=deferred();const pending=dbMartsSubmit('refresh');await tick();const r=p.run;inventory=[];await dbRefreshAccess();protectedEmpty();assert.equal(r.payload,null);gate.resolve();await pending;assert.equal(r.id,'run-1');assert.equal(r.details,null);assert.equal($('db-marts-observe').disabled,true);assert.equal($('db-marts-run-detail').descendants().some(n=>n.textContent.includes('publication_state')),false);
''')

    def test_same_context_refresh_cas_draft_caret_focus(self):
        self.javascript(r'''
await createMart();const field=$('db-marts-name');field.value='retained_draft';field.oninput();field.focus();field.setSelectionRange(2,7);await dbMartsRefresh();assert.equal($('db-marts-name'),field);assert.equal(field.value,'retained_draft');assert.equal(document.activeElement,field);assert.equal(field.selectionStart,2);conflict=true;await dbMartsSave();assert.equal(p.conflict,true);assert.equal(field.value,'retained_draft');assert.equal(field.selectionEnd,7);assert.equal(document.activeElement,field);assert.equal($('db-marts-refresh-run').disabled,true);
''')

    def test_wrong_owner_identity_and_late_load_rejected(self):
        self.javascript(r'''
await createMart();for(const change of [{owner:'platform-admin'},{id:'foreign'},{database_id:'finance'}]){wrong=change;$('db-marts-list').value='mart-1';$('db-marts-list').onchange();await dbMartsLoad();assert.notEqual(p.mart?.owner,'platform-admin');assert.equal($('db-marts-model-detail').textContent.includes('platform-admin'),false);}wrong=null;
loadGate=deferred();const loading=dbMartsLoad();await tick();dbMartsClear();loadGate.resolve();await loading;protectedEmpty();
''')

    def test_save_response_loss_observe_retains_frozen_body_key(self):
        self.javascript(r'''
await openMarts();$('db-marts-name').value='daily';$('db-marts-model-id').value='explicit';$('db-marts-version').value='3';$('db-marts-watch').value='all';await dbMartsLoadModel();const original=route;let first=true;route=async(path,options)=>{const reply=await original(path,options);if(path==='/api/v1/marts'&&options?.body&&first){first=false;throw Error('lost save');}return reply;};await dbMartsSave();const s=p.save,request=posts().at(-1);assert.ok(s);assert.ok(Object.isFrozen(s.body));$('db-marts-name').value='changed_draft';$('db-marts-name').oninput();await dbMartsObserve();assert.equal(posts().at(-1).options.body,request.options.body);assert.equal(posts().at(-1).options.headers['X-Idempotency-Key'],s.key);assert.equal(p.mart.id,'mart-1');assert.equal(p.save,null);assert.equal($('db-marts-name').value,'changed_draft');assert.equal(dbMartsMatches(),false);
''')

    def test_late_submission_navigation_keeps_only_handle_then_explicit_reload(self):
        self.javascript(r'''
await createMart();gate=deferred();const pending=dbMartsSubmit('refresh');await tick();const r=p.run;dbMartsClear();gate.resolve();await pending;assert.equal(r.id,'run-1');assert.equal(r.details,null);protectedEmpty();await dbMartsRefresh();protectedEmpty();const count=calls.length;await dbMartsObserve();assert.equal(calls.length,count);$('db-marts-list').value='mart-1';$('db-marts-list').onchange();await dbMartsLoad();await dbMartsObserve();assert.ok(r.details);assert.equal(r.payload,null);
''')

    def test_bookkeeping_hold_is_per_mart_and_original_recovers_from_history(self):
        self.javascript(r'''
await createMart();const context=structuredClone(p.context),view=activeTab,explorer=JSON.stringify(dbExplorer),worksheetsBefore=JSON.stringify(worksheets);
saved.push({...structuredClone(saved[0]),id:'mart-b'});const original=route;let aRun,bRun;
route=async(path,options)=>{
 const url=new URL('http://local'+path);
 if(url.pathname==='/api/v1/marts/mart-1/runs')return {context,items:[structuredClone(aRun)]};
 if(url.pathname==='/api/v1/marts/mart-1/runs/run-1')return {context,run:structuredClone(aRun)};
 const reply=await original(path,options);
 if(options?.method==='POST'&&url.pathname.endsWith('/refreshes')){
  if(url.pathname.includes('/mart-1/')){aRun={...reply.run,status:'reconciliation_required',committed:true,baseline_saved:false,result:{publication:'A_ONLY_PROTECTED'}};saved[0].reconciliation_required=true;saved[0].blocked_run_id=aRun.id;return {context,run:structuredClone(aRun)};}
  bRun={...reply.run,id:'run-b'};return {context,run:bRun};
 }return reply;
};
await dbMartsSubmit('refresh');const aReceipt=p.run;assert.equal(aReceipt.status,'reconciliation_required');assert.equal(dbMartsHeld(),true);assert.equal($('db-marts-observe').disabled,false);const aPosts=posts().length;await dbMartsSubmit('refresh');assert.equal(posts().length,aPosts);
await dbMartsRefresh();$('db-marts-list').value='mart-1';$('db-marts-list').onchange();await dbMartsLoad();assert.equal(p.mart.reconciliation_required,true);assert.equal(p.mart.blocked_run_id,'run-1');assert.equal($('db-marts-refresh-run').disabled,true);
$('db-marts-list').value='mart-b';$('db-marts-list').onchange();assert.equal(aReceipt.details,null);assert.deepEqual(p.history,[]);await dbMartsLoad();assert.deepEqual(p.context,context);assert.equal(p.mart.id,'mart-b');assert.equal(dbMartsHeld(),false);assert.equal($('db-marts-refresh-run').disabled,false);assert.equal($('db-marts-run-detail').descendants().some(n=>n.textContent.includes('A_ONLY_PROTECTED')),false);
const beforeB=calls.length;await dbMartsObserve();assert.equal(calls.length,beforeB);await dbMartsSubmit('refresh');assert.equal(posts().length,aPosts+1);assert.equal(posts().at(-1).path,'/api/v1/marts/mart-b/refreshes');assert.deepEqual(JSON.parse(posts().at(-1).options.body).context,context);assert.equal(p.run.id,'run-b');assert.equal(p.run.status,'succeeded');
$('db-marts-list').value='mart-1';$('db-marts-list').onchange();await dbMartsLoad();assert.equal($('db-marts-refresh-run').disabled,true);await dbMartsHistory();assert.equal(p.history[0].id,'run-1');const count=posts().length;await $('db-marts-history').descendants().find(n=>n.tagName==='BUTTON').onclick();assert.equal(posts().length,count);assert.ok(calls.at(-1).path.startsWith('/api/v1/marts/mart-1/runs/run-1?'));assert.equal(p.run.details.committed,true);assert.equal(p.run.details.baseline_saved,false);assert.equal(p.run.status,'reconciliation_required');
aRun={...aRun,status:'succeeded',baseline_saved:true};await dbMartsObserve();assert.equal(p.run.status,'succeeded');assert.equal(dbMartsHeld(),true,'loaded backend hold remains until explicitly reloaded');saved[0].reconciliation_required=false;saved[0].blocked_run_id=null;await dbMartsLoad();assert.equal(dbMartsHeld(),false);assert.equal(activeTab,view);assert.equal(JSON.stringify(dbExplorer),explorer);assert.equal(JSON.stringify(worksheets),worksheetsBefore);
''')

    def test_definitive_refresh_cas_rejection_requires_explicit_load_and_new_key(self):
        self.javascript(r'''
await createMart();const original=route;let reject=true;route=async(path,options)=>path.endsWith('/refreshes')&&reject?{http:409,body:{error:{code:'MART_REVISION_CONFLICT',details:{}}}}:original(path,options);
const field=$('db-marts-name');field.focus();field.setSelectionRange(1,3);await dbMartsSubmit('refresh');const rejected=p.run,first=posts().at(-1),count=posts().length;assert.equal(rejected.id,null);assert.equal(rejected.rejection,'MART_REVISION_CONFLICT');assert.equal(rejected.status,'rejected');assert.equal(rejected.details,null);assert.equal(rejected.payload,null);assert.equal(rejected.path,null);assert.deepEqual(runs,[]);assert.deepEqual(p.history,[]);assert.equal(field.value,'daily');assert.equal(document.activeElement,field);assert.equal(field.selectionStart,1);assert.ok(p.status.textContent.includes('not accepted'));assert.equal(p.conflict,true);
await dbMartsObserve();await dbMartsSubmit('refresh');assert.equal(posts().length,count);saved[0]={...saved[0],revision:2};await dbMartsRefresh();assert.equal(field.value,'daily');assert.equal(p.mart.revision,1);await dbMartsSubmit('refresh');assert.equal(posts().length,count);
await dbMartsLoad();assert.equal(p.mart.revision,2);assert.equal(p.conflict,false);assert.equal(posts().length,count,'load never retries');reject=false;await dbMartsSubmit('refresh');const next=posts().at(-1);assert.equal(posts().length,count+1);assert.equal(JSON.parse(next.options.body).expected_revision,2);assert.notEqual(next.options.headers['X-Idempotency-Key'],first.options.headers['X-Idempotency-Key']);assert.equal(p.run.status,'succeeded');
''')

    def test_only_definitive_structured_409_refuses_before_acceptance(self):
        self.javascript(r'''
await createMart();const original=route;
for(const code of ['MART_REVISION_CONFLICT','WATCH_REQUIRED','MODEL_VERSION_CHANGED','MART_BUSY','MART_RECONCILIATION_REQUIRED']){
 route=async(path,options)=>path.endsWith('/refreshes')?{http:409,body:{code,details:{}}}:original(path,options);await dbMartsSubmit('refresh');assert.equal(p.run.rejection,code);assert.equal(p.run.id,null);assert.equal(p.run.details,null);const count=posts().length;await dbMartsObserve();assert.equal(posts().length,count);await dbMartsLoad();
}
for(const response of [{http:500,body:{code:'MART_REVISION_CONFLICT'}},{http:400,body:{code:'WATCH_REQUIRED'}},{http:409,body:{code:'OTHER_CONFLICT'}},{http:409,body:{code:'MART_BUSY',run:{id:'unvalidated'}}},{http:409,body:{code:'MART_BUSY',details:{operation_id:'unvalidated'}}},{malformed:true}]){
 route=async(path,options)=>path.endsWith('/refreshes')?response:original(path,options);await dbMartsSubmit('refresh');const r=p.run,first=posts().at(-1);assert.equal(r.status,'unknown');assert.equal(r.rejection,undefined);assert.ok(Object.isFrozen(r.payload));await dbMartsSubmit('refresh');assert.equal(posts().at(-1),first);await dbMartsObserve();assert.equal(posts().at(-1).options.body,first.options.body);assert.equal(posts().at(-1).options.headers['X-Idempotency-Key'],r.key);route=original;await dbMartsObserve();assert.equal(r.status,'succeeded');await dbMartsLoad();
}
''')

    def test_refusal_code_on_known_run_never_discards_accepted_identity(self):
        self.javascript(r'''
await createMart();await dbMartsSubmit('refresh');const r=p.run,details=r.details,id=r.id,status=r.status,original=route;route=async(path,options)=>path.includes('/runs/')?{http:409,body:{code:'MART_REVISION_CONFLICT'}}:original(path,options);await dbMartsObserve();assert.equal(p.run,r);assert.equal(r.id,id);assert.equal(r.status,status);assert.equal(r.details,details);assert.equal(r.rejection,undefined);assert.equal(r.payload,null);assert.equal($('db-marts-observe').disabled,false);
''')


if __name__ == '__main__':
    unittest.main()
