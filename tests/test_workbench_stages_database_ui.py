"""Run shipped Stage handlers against the existing offline Node DOM/fetch harness."""
import json
import unittest
import test_workbench_database_ui as harness
import test_workbench_admin_ui as admin_harness

SETUP = r'''
const p=dbStages;
for(const d of inventory)d.capabilities.stage_admin=true;
for(const s of schemas)s.capabilities.stage_admin=true;
const stageRef=(schema='raw')=>({database_id:'db',schema_id:schema,object_id:'table',incarnation:1});
objectsRoute=async path=>({items:[{kind:'TABLE',physical_name:'orders',ref:stageRef(path.includes('/raw/')?'raw':'schema')},{kind:'VIEW',physical_name:'view',ref:stageRef()}],next_cursor:null});
let savedStages=[],runs=[],saveFailure=false,previewFailure=false,submitFailure=false,gate=null,loadGate=null;
const receipt={export_id:'export-1',stage_id:'stage-1',stage_revision:1,run_id:'run-1',file:'out.csv',owner:'owner',publication_state:'published',format:'csv',source:{snapshot_id:42,ref:stageRef()},expected_count:1,written_count:1,byte_count:4,sha256:'a'.repeat(64)};
route=async(path,options)=>{
 const url=new URL('http://local'+path),body=options?.body?JSON.parse(options.body):null,context=body?.context||JSON.parse(url.searchParams.get('context')||'null'),base='/api/v1/stages';
 if(url.pathname===base&&body){if(saveFailure)return {http:409,body:{code:'REVISION_CONFLICT'}};const stage={id:body.stage_id||'stage-1',owner:'owner',selected_role:'admin',revision:body.expected_revision+1,context,definition:structuredClone(body.definition)};savedStages=savedStages.filter(s=>s.id!==stage.id).concat(stage);return {context,stage};}
 if(url.pathname===base)return {context,stages:structuredClone(savedStages),local_roots:['/approved']};
 if(url.pathname.endsWith('/files'))return {context,stage_id:'stage-1',revision:savedStages[0].revision,files:[{name:'input.csv',size:12,modified_at:'2026-10-21',version:'version-1'}],limits:{}};
 if(url.pathname.endsWith('/preview')){if(gate)await gate.promise;if(previewFailure)return {http:409,body:{code:'PREVIEW_CHANGED'}};return {context,stage_id:'stage-1',revision:savedStages[0].revision,preview:{columns:[{name:'code',type:'varchar'}],rows:[['001']]},confirmation_fingerprint:'confirmed'};}
 if(body&&(url.pathname.endsWith('/imports')||url.pathname.endsWith('/exports'))){if(gate)await gate.promise;if(submitFailure)throw Error('response lost');const run={id:'run-1',stage_id:'stage-1',stage_revision:body.expected_revision,kind:url.pathname.endsWith('/imports')?'ingest':'export',owner:'owner',context,status:'running',operation_id:'operation',committed:null,file:body.file};runs=[run];return {context,stage_id:'stage-1',run};}
 if(url.pathname.endsWith('/runs'))return {context,stage_id:'stage-1',runs:structuredClone(runs)};
 if(url.pathname.includes('/runs/')){const run=structuredClone(runs[0]);if(url.pathname.endsWith('/cancel'))run.status='canceled';runs=[run];return {context,stage_id:'stage-1',run};}
 if(url.pathname.endsWith('/exports'))return {context,stage_id:'stage-1',receipts:[receipt]};
 if(url.pathname.includes('/exports/'))return {context,stage_id:'stage-1',receipt:structuredClone({context,...receipt})};
 if(url.pathname.startsWith(base+'/')){if(loadGate)await loadGate.promise;return {context,stage:structuredClone(savedStages.find(s=>s.id===url.pathname.split('/').at(-1)))};}
 return {};
};
const stagePosts=()=>calls.filter(c=>c.path.startsWith('/api/v1/stages')&&c.options?.method==='POST');
async function openStages(){await boot([input(),input('b')]);p.started=true;await dbStagesRefresh();assert.equal(p.invalid,false);}
async function createStage(){await openStages();$('db-stages-name').value='daily';$('db-stages-directory').value='/approved/input';$('db-stages-destination').value='codes';await dbStagesSave();assert.equal(p.stage.id,'stage-1');await dbStagesFiles();$('db-stages-file').value='input.csv';}
async function previewStage(){await dbStagesPreview();assert.ok(p.preview);}
'''

SCAN_SETUP = r'''
let scanGate=null,scanLost=false,scanListGate=null,scanReplyChange=null;
const scanItem=(id,status,attempt=1)=>({id,file:id+'.csv',input_fingerprint:'fp-'+id,status,attempt,committed:status==='succeeded'||status==='skipped',retryable:status==='failed'});
let scanRows=[];
const oldStageRoute=route;
route=async(path,options)=>{
 const url=new URL('http://local'+path),body=options?.body?JSON.parse(options.body):null;
 if(!url.pathname.includes('/scans'))return oldStageRoute(path,options);
 const context=body?.context||JSON.parse(url.searchParams.get('context'));
 if(!body&&url.pathname.endsWith('/scans')){if(scanListGate)await scanListGate.promise;return {context,scans:structuredClone(scanRows)};}
 if(scanGate)await scanGate.promise;
 if(scanLost)throw Error('lost scan response');
 let scan;
 if(body){
  const retry=url.pathname.endsWith('/retry');
  scan={id:retry?'scan-2':'scan-1',stage_id:'stage-1',stage_revision:body.expected_revision,context,owner:'owner',status:retry?'queued':'partial',committed:!retry,
   items:retry?[scanItem('retry','pending',body.expected_attempt+1)]:[scanItem('first','succeeded'),scanItem('bad','failed'),scanItem('last','not_started',0)]};
  if(retry)Object.assign(scan,{source_scan_id:url.pathname.split('/').at(-2),source_item_id:body.item_id});
  if(!scanRows.some(s=>s.id===scan.id))scanRows.push(structuredClone(scan));
 }else scan=structuredClone(scanRows.find(s=>s.id===url.pathname.split('/').at(-1)));
 if(scanReplyChange)Object.assign(scan,scanReplyChange);
 return {context,stage_id:'stage-1',scan};
};
async function scanStage(mode='append',atomic=true){
 await createStage();$('db-stages-mode').value=mode;$('db-stages-target').value=JSON.stringify(stageRef());$('db-stages-options').value=JSON.stringify({atomic_append:atomic,...(mode==='upsert'?{key_columns:['code']}:{})});
 if(mode==='create')$('db-stages-destination').value='codes';await dbStagesSave();
}
const scanPosts=()=>stagePosts().filter(c=>c.path.includes('/scans'));
'''

SCAN_CONTEXT_SETUP = r'''
inventory.push({...structuredClone(inventory[0]),id:'finance',sql_catalog:'finance',binding_revision:7,generation_id:'finance-gen',grant_revision:9});
const financeSchema={...structuredClone(schemas[1]),id:'finance-raw',database_id:'finance'};
schemasRoute=async path=>({items:path.includes('/finance/')?[financeSchema]:schemas});
const scanCalls=()=>calls.filter(c=>c.path.includes('/scans'));
function hiddenScan(r){
 assert.equal(r.details,null);assert.deepEqual(p.scans,[]);
 assert.equal($('db-stages-scan-list').children.length,0);
 assert.equal($('db-stages-scan-detail').children.length,1); // Safe ID/key/status only.
 assert.equal($('db-stages-scan-observe').disabled,true);
 assert.equal(dbStagesCanRetry(r,scanItem('bad','failed')),false);
 assert.equal($('db-stages-scan-detail').descendants().some(n=>n.tagName==='BUTTON'&&!n.disabled),false);
}
async function noForeignRead(r){
 const count=scanCalls().length;await dbStagesScanObserve();await dbStagesScanRequest(r);await dbStagesScanStart(scanItem('bad','failed'));
 assert.equal(scanCalls().length,count);hiddenScan(r);
}
async function financeContext(r){
 const count=scanCalls().length;
 $('db-stages-database').value='finance';await $('db-stages-database').onchange();
 assert.equal(p.invalid,true);assert.equal(p.context,null);assert.equal(p.stage,null);await noForeignRead(r);
 $('db-stages-schema').value='finance-raw';await $('db-stages-schema').onchange();
 assert.equal(p.invalid,false);assert.equal(p.stage,null);assert.equal(p.context.database_id,'finance');await noForeignRead(r);
 assert.equal(scanCalls().length,count);
}
async function foreignStage(sales,r){
 const foreign={...structuredClone(sales),id:'finance-stage',context:structuredClone(p.context)};
 assert.equal(foreign.definition.name,sales.definition.name);savedStages.push(foreign);
 await dbStagesRefresh();$('db-stages-list').value=foreign.id;$('db-stages-list').onchange();await dbStagesLoad();
 assert.equal(p.stage.id,foreign.id);await noForeignRead(r);
}
async function returnToSales(sales,r){
 const count=scanCalls().length;
 $('db-stages-database').value='db';await $('db-stages-database').onchange();
 $('db-stages-schema').value=sales.context.schema_id;await $('db-stages-schema').onchange();
 await noForeignRead(r);$('db-stages-list').value=sales.id;$('db-stages-list').onchange();await dbStagesLoad(true);
 assert.equal(r.details,null);assert.equal(scanCalls().length,count);assert.equal($('db-stages-scan-observe').disabled,false);
 const key=r.key,context=r.context,posts=scanPosts().length;await dbStagesScanObserve();
 assert.equal(scanCalls().length,count+1);assert.equal(scanPosts().length,posts);
 assert.ok(calls.at(-1).path.includes('/scans/'+r.id+'?'));assert.equal(r.key,key);assert.equal(r.context,context);assert.ok(r.details);
}
'''


class StagesUI(unittest.TestCase):
    def javascript(self, body):
        harness.DatabaseUI.javascript(self, SETUP + SCAN_SETUP + body)

    def test_scan_modes_ordered_report_retry_capability_and_frozen_cas(self):
        self.javascript(r'''
await scanStage();assert.equal(p.stage.revision,2);assert.equal(p.preview,null);await dbStagesScanStart();assert.equal(scanPosts().length,1);assert.equal(p.run,null);
const old=p.scan,report=structuredClone(old.details);assert.equal(old.status,'partial');assert.deepEqual(old.details.items.map(i=>i.file),['first.csv','bad.csv','last.csv']);
assert.equal(dbStagesCanRetry(old,old.details.items[0]),false);assert.equal(dbStagesCanRetry(old,old.details.items[1]),true);assert.equal(dbStagesCanRetry(old,old.details.items[2]),false);
assert.equal(dbStagesCanRetry(old,{...old.details.items[1],retryable:false}),false);assert.equal(dbStagesCanRetry(old,{...old.details.items[1],committed:true}),false);
assert.equal(dbStagesCanRetry({...old,status:'unknown'},old.details.items[1]),false);assert.equal(dbStagesCanRetry({...old,details:{items:[scanItem('u','unknown')]}},old.details.items[1]),false);
await dbStagesScanStart(old.details.items[1]);const post=scanPosts().at(-1),body=JSON.parse(post.options.body);
assert.equal(post.path,'/api/v1/stages/stage-1/scans/scan-1/retry');assert.deepEqual(body,{context:old.context,expected_revision:2,item_id:'bad',expected_attempt:1});
assert.notEqual(post.options.headers['Idempotency-Key'],old.key);assert.equal(p.scan.id,'scan-2');assert.equal(p.scan.details.source_scan_id,'scan-1');assert.equal(p.scan.details.items.length,1);assert.equal(p.scan.details.items[0].attempt,2);assert.deepEqual(old.details,report);
const count=stagePosts().length;await dbStagesSubmit('export');await dbStagesScanStart();assert.equal(stagePosts().length,count);
await dbStagesScans();assert.deepEqual(p.scans.map(s=>s.id),['scan-1','scan-2']);assert.equal(worksheets.length,2);assert.equal(dbCurrent().sql,'SELECT 1');
''')

    def test_scan_denies_create_replace_non_atomic_append_before_send(self):
        for mode, atomic in [('create', True), ('replace', True), ('append', False), ('upsert', False)]:
            self.javascript(r'''
await scanStage(MODE,ATOMIC);const count=scanPosts().length;await dbStagesScanStart();
if(MODE==='upsert'){assert.equal(scanPosts().length,count+1);assert.equal(p.scan.id,'scan-1');}
else {assert.equal(scanPosts().length,count);assert.equal($('db-stages-scan').disabled,true);}
'''.replace('MODE', json.dumps(mode)).replace('ATOMIC', json.dumps(atomic)))

    def test_scan_sales_details_never_follow_finance_selection_or_same_named_stage(self):
        self.javascript(SCAN_CONTEXT_SETUP + r'''
await scanStage();await dbStagesScanStart();
// The observed completed SALES scan skips both previously imported files.
scanRows[0].status='succeeded';scanRows[0].items=[{...scanItem('a','skipped'),file:'a.csv'},{...scanItem('b','skipped'),file:'b.jsonl'}];
await dbStagesScanObserve();await dbStagesScans();const r=p.scan,sales=structuredClone(p.stage);
assert.equal(r.details.items.length,2);assert.equal(p.scans.length,1);
await financeContext(r);await foreignStage(sales,r);
// A newer current Stage revision must not hide immutable accepted history.
savedStages[0].revision++;await returnToSales(sales,r);
assert.equal(p.stage.revision,3);assert.equal(r.details.stage_revision,2);assert.equal(r.details.items[1].file,'b.jsonl');
''')

    def test_scan_late_submission_get_and_list_cannot_resurrect_foreign_details(self):
        for response in ('submission', 'get', 'list'):
            with self.subTest(response=response):
                self.javascript(SCAN_CONTEXT_SETUP + r'''
await scanStage();const sales=structuredClone(p.stage);let pending;
if(RESPONSE==='submission'){scanGate=deferred();pending=dbStagesScanStart();}
else {await dbStagesScanStart();await dbStagesScans();if(RESPONSE==='get'){scanGate=deferred();pending=dbStagesScanObserve();}else{scanListGate=deferred();pending=dbStagesScans();}}
await tick();const r=p.scan,key=r.key,context=r.context;await financeContext(r);
(scanGate||scanListGate).resolve();await pending;
assert.equal(r.id,'scan-1');assert.equal(r.payload,null);assert.equal(r.key,key);assert.equal(r.context,context);hiddenScan(r);
scanGate=null;scanListGate=null;await foreignStage(sales,r);await returnToSales(sales,r);
'''.replace('RESPONSE', json.dumps(response)))

    def test_scan_stage_selection_and_every_frozen_context_field_gate_read_and_retry(self):
        self.javascript(r'''
await scanStage();await dbStagesScanStart();await dbStagesScans();const r=p.scan,item=r.details.items[1],sales=structuredClone(p.stage);
const details=r.details,list=structuredClone(p.scans);await dbStagesRefresh();assert.equal(r.details,details);assert.deepEqual(p.scans,list);
const other={...structuredClone(sales),id:'stage-other'};savedStages.push(other);await dbStagesRefresh();
// Refresh retains editor drafts/focus; selection alone removes protected scan state.
await dbStagesScanObserve();await dbStagesScans();$('db-stages-list').value=other.id;$('db-stages-list').onchange();
assert.equal(r.details,null);assert.deepEqual(p.scans,[]);assert.equal($('db-stages-scan-observe').disabled,true);
let count=calls.length;await dbStagesScanObserve();await dbStagesScanStart(item);assert.equal(calls.length,count);
await dbStagesLoad();assert.equal(p.stage.id,other.id);assert.equal(r.details,null);assert.equal($('db-stages-scan-observe').disabled,true);
count=calls.length;await dbStagesScanObserve();assert.equal(calls.length,count);
$('db-stages-list').value=sales.id;$('db-stages-list').onchange();await dbStagesLoad();await dbStagesScanObserve();
const context=p.context;assert.equal(Object.keys(context).length,9);
for(const key of Object.keys(context)){
 p.context=dataopsFreeze({...context,[key]:typeof context[key]==='number'?context[key]+1:context[key]+'-other'});dbStagesRender();
 assert.equal(r.details,null,key);assert.equal($('db-stages-scan-observe').disabled,true,key);assert.equal(dbStagesCanRetry(r,item),false,key);
 count=calls.length;await dbStagesScanObserve();await dbStagesScanRequest(r);await dbStagesScanStart(item);assert.equal(calls.length,count,key);
 p.context=context;dbStagesRender();await dbStagesScanObserve();assert.ok(r.details,key);
}
''')

    def test_scan_unknown_and_ambiguous_retry_observe_exact_original_key_body(self):
        self.javascript(r'''
await scanStage();scanLost=true;await dbStagesScanStart();const r=p.scan,first=scanPosts()[0];assert.equal(r.status,'unknown');assert.ok(Object.isFrozen(r.payload));assert.ok(Object.isFrozen(r.context));
await dbStagesScanStart();assert.equal(scanPosts().length,1);await dbStagesScanObserve();assert.equal(scanPosts().length,2);assert.equal(scanPosts()[1].options.body,first.options.body);assert.equal(scanPosts()[1].options.headers['Idempotency-Key'],r.key);
scanLost=false;await dbStagesScanObserve();assert.equal(p.scan.id,'scan-1');const count=scanPosts().length;await dbStagesScanObserve();assert.equal(scanPosts().length,count);assert.ok(calls.at(-1).path.includes('/scans/scan-1?'));
scanLost=true;await dbStagesScanStart(p.scan.details.items[1]);const retry=p.scan,retryPost=scanPosts().at(-1);assert.equal(retry.id,null);assert.equal(retry.status,'unknown');
scanLost=false;await dbStagesScanObserve();assert.equal(p.scan,retry);assert.equal(p.scan.id,'scan-2');assert.equal(scanPosts().at(-1).path,retryPost.path);assert.equal(scanPosts().at(-1).options.body,retryPost.options.body);assert.equal(scanPosts().at(-1).options.headers['Idempotency-Key'],retry.key);
scanRows[1].status='unknown';scanRows[1].items[0].status='unknown';scanRows[1].items[0].retryable=true;await dbStagesScanObserve();assert.equal(p.scan.status,'unknown');assert.equal(dbStagesCanRetry(p.scan,p.scan.details.items[0]),false);assert.equal($('db-stages-scan').disabled,true);
''')

    def test_scan_refresh_inspect_preserves_editor_focus_draft_revision_and_tabs(self):
        self.javascript(r'''
await scanStage();await dbStagesScanStart();const field=$('db-stages-options'),stage=p.stage;field.value='{"atomic_append":true,"encoding":"utf-8"}';field.oninput();field.focus();field.setSelectionRange(3,9);const draft=field.value,rev=p.stage.revision;
await dbStagesScans();await dbStagesScanInspect(p.scans[0]);await dbStagesScanObserve();await dbStagesRefresh();
assert.equal(p.stage,stage);assert.equal(p.stage.revision,rev);assert.equal(field.value,draft);assert.equal(document.activeElement,field);assert.equal(field.selectionStart,3);assert.equal(field.selectionEnd,9);assert.equal(worksheets.length,2);assert.equal(dbCurrent().sql,'SELECT 1');assert.equal(dbExplorer.database_id,'db');
''')

    def test_scan_and_list_late_responses_cannot_restore_revoked_or_new_principal_data(self):
        for loss in ('grant', 'principal', 'context'):
            self.javascript(r'''
await scanStage();scanGate=deferred();const pending=dbStagesScanStart();await tick();
if(LOSS==='grant'){inventory=[];await dbRefreshAccess();}else if(LOSS==='principal'){dbPrincipal=JSON.stringify(['other',true,'admin']);dbStagesValidate();}else{$('db-stages-schema').value='raw';await $('db-stages-schema').onchange();}
scanGate.resolve();await pending;assert.ok(!p.scan||p.scan.details===null);assert.equal($('db-stages-scan-detail').children.length<=1,true);assert.deepEqual(p.scans,[]);
if(LOSS!=='context'){assert.ok(!p.scan||p.scan.payload===null);const count=scanPosts().length;await dbStagesScanObserve();assert.equal(scanPosts().length,count);}
'''.replace('LOSS', json.dumps(loss)))
            self.javascript(r'''
await scanStage();await dbStagesScanStart();scanListGate=deferred();const listing=dbStagesScans();await tick();
if(LOSS==='grant'){schemas[0].capabilities.stage_admin=false;await dbStagesRefreshAuthorization();}else if(LOSS==='principal'){dbPrincipal=JSON.stringify(['other',true,'admin']);dbStagesValidate();}else{$('db-stages-schema').value='raw';await $('db-stages-schema').onchange();}
scanListGate.resolve();await listing;assert.deepEqual(p.scans,[]);assert.equal($('db-stages-scan-list').children.length,0);
'''.replace('LOSS', json.dumps(loss)))

    def test_retry_response_must_have_new_scan_source_and_incremented_attempt(self):
        for change in ({'id': 'scan-1'}, {'source_scan_id': 'other'}, {'source_item_id': 'other'}, {'items': []}):
            self.javascript(r'''
await scanStage();await dbStagesScanStart();scanReplyChange=CHANGE;await dbStagesScanStart(p.scan.details.items[1]);assert.equal(p.scan.id,null);assert.equal(p.scan.status,'unknown');assert.equal(p.scan.details,null);assert.ok(p.scan.payload);assert.equal(scanPosts().length,2);
'''.replace('CHANGE', json.dumps(change)))

    def test_late_retry_acceptance_retains_only_safe_handle_after_access_loss(self):
        self.javascript(r'''
await scanStage();await dbStagesScanStart();scanGate=deferred();const retry=dbStagesScanStart(p.scan.details.items[1]);await tick();inventory=[];await dbRefreshAccess();assert.equal(p.scan.payload,null);scanGate.resolve();await retry;
assert.equal(p.scan.id,'scan-2');assert.equal(p.scan.details,null);assert.equal(p.scan.payload,null);assert.equal(p.scan.purged,true);assert.equal($('db-stages-scan-observe').disabled,true);assert.equal($('db-stages-scan-detail').children.length,1);
''')

    def test_scan_authorized_observe_recovers_safe_accepted_handle_without_post(self):
        self.javascript(r'''
await scanStage();scanGate=deferred();const submitting=dbStagesScanStart();await tick();const originalInventory=inventory;inventory=[];await dbRefreshAccess();scanGate.resolve();await submitting;assert.equal(p.scan.id,'scan-1');assert.equal(p.scan.details,null);assert.equal(p.scan.payload,null);
inventory=originalInventory;await dbStagesRefresh();const posts=scanPosts().length;assert.equal(p.scan.details,null);assert.equal($('db-stages-scan-observe').disabled,true);
const reads=calls.length;await dbStagesScanObserve();assert.equal(calls.length,reads);$('db-stages-list').value='stage-1';$('db-stages-list').onchange();await dbStagesLoad();
assert.equal($('db-stages-scan-observe').disabled,false);await dbStagesScanObserve();assert.equal(scanPosts().length,posts);assert.equal(p.scan.id,'scan-1');assert.equal(p.scan.details.items.length,3);assert.equal(p.scan.purged,false);
''')

    def test_scan_labels_follow_existing_live_english_japanese_bindings(self):
        self.javascript(r'''
await scanStage();await dbStagesScanStart();const binding=key=>[...uiBindings].find(([node,entry])=>entry.key===key&&node.isConnected)[0];
setLanguage('ja');assert.equal($('db-stages-scan').textContent,'手動スキャン');assert.equal($('db-stages-scan-observe').textContent,'スキャンを照会');assert.equal(binding('Scan ID').textContent,'スキャン ID');assert.equal(binding('Attempt').textContent,'試行');assert.equal(binding('Retry failed item').textContent,'失敗した項目を再試行');
setLanguage('en');assert.equal($('db-stages-scan').textContent,'Manual scan');assert.equal(binding('Attempt').textContent,'Attempt');assert.equal(binding('Retry failed item').textContent,'Retry failed item');
''')

    def test_duplicate_manual_import_rejects_old_context_and_extra_protected_fields(self):
        for change in ({'operation_id': 'fake'}, {'summary': {'old': 'private'}}, {'deduplicated': False}, {'context': {'old': 'protected'}}):
            self.javascript(r'''
await createStage();await previewStage();const original=route;route=async(path,options)=>{if(path.endsWith('/imports')){const context=JSON.parse(options.body).context;return {context,run:{id:null,status:'skipped',committed:true,deduplicated:true,context,...CHANGE}};}return original(path,options);};
await dbStagesSubmit('ingest');assert.equal(p.run.status,'unknown');assert.equal(p.run.details,null);assert.equal(p.run.id,null);assert.equal(dbStagesBusy(),true);assert.ok(p.run.payload);
'''.replace('CHANGE', json.dumps(change)))

    def test_minimal_duplicate_manual_import_is_skipped_and_releases_lock_without_fake_ids(self):
        self.javascript(r'''
await createStage();await previewStage();const original=route;route=async(path,options)=>{if(path.endsWith('/imports')){const context=JSON.parse(options.body).context;return {context,run:{id:null,status:'skipped',committed:true,deduplicated:true,context}};}return original(path,options);};
await dbStagesSubmit('ingest');assert.equal(p.run.status,'skipped');assert.equal(p.run.id,null);assert.equal(p.run.operation_id,null);assert.equal(p.run.details,null);assert.equal(p.run.payload,null);assert.equal(p.pending,false);assert.equal(dbStagesBusy(),false);assert.equal($('db-stages-observe').disabled,true);
await previewStage();assert.equal($('db-stages-confirm').disabled,false);assert.equal($('db-stages-run-detail').children.some(n=>n.textContent.includes('UNKNOWN')),false);
''')

    def test_preview_explicit_confirm_full_context_and_no_double_submit(self):
        self.javascript(r'''
await createStage();assert.equal($('db-stages-roots').textContent,'/approved');await previewStage();assert.equal(stagePosts().filter(c=>c.path.endsWith('/imports')).length,0);
assert.deepEqual($('db-stages-preview-detail').children[1].rows,[['001']]);
await dbStagesSubmit('ingest');await dbStagesSubmit('ingest');const posts=stagePosts().filter(c=>c.path.endsWith('/imports'));assert.equal(posts.length,1);
const body=JSON.parse(posts[0].options.body);assert.equal(body.file,'input.csv');assert.equal(body.version,'version-1');assert.equal(body.expected_preview_fingerprint,'confirmed');assert.deepEqual(body.context,p.run.context);assert.equal(p.run.details.context.selected_role,'admin');assert.ok(Object.isFrozen(p.run.context));assert.equal(p.preview,null);assert.equal($('db-stages-confirm').disabled,true);
assert.equal(worksheets.length,2);assert.equal(dbCurrent().sql,'SELECT 1');assert.equal(dbExplorer.database_id,'db');assert.equal($('db-stages-status').getAttribute('role'),'status');
''')

    def test_all_definition_and_file_changes_invalidate_preview(self):
        self.javascript(r'''
await createStage();for(const key of ['name','directory','mode','format','destination','target','options']){await previewStage();$('db-stages-'+key).oninput();assert.equal(p.preview,null,key);assert.equal($('db-stages-confirm').disabled,true,key);}
await previewStage();$('db-stages-file').onchange();assert.equal(p.preview,null);
await previewStage();previewFailure=true;await dbStagesPreview();assert.equal(p.preview,null);await dbStagesSubmit('ingest');assert.equal(stagePosts().filter(c=>c.path.endsWith('/imports')).length,0);
''')

    def test_cas_failure_preserves_draft_focus_key_and_explicit_latest_load(self):
        self.javascript(r'''
await createStage();const field=$('db-stages-options');field.value='{"encoding":"utf-8"}';field.oninput();field.focus();field.setSelectionRange(2,6);saveFailure=true;await dbStagesSave();const failed=p.save,key=failed.key,status=$('db-stages-status').textContent;
await periodicRefresh();assert.equal(p.stage.revision,1);assert.equal(field.value,'{"encoding":"utf-8"}');assert.equal(document.activeElement,field);assert.equal(field.selectionStart,2);assert.equal(p.save,failed);assert.equal(p.save.key,key);assert.equal($('db-stages-status').textContent,status);
await dbStagesSave();assert.equal(stagePosts().at(-1).options.headers['Idempotency-Key'],key);
savedStages[0]=structuredClone(savedStages[0]);savedStages[0].revision=2;savedStages[0].definition.name='renamed';$('db-stages-list').value='stage-1';await dbStagesLoad();assert.equal(p.stage.revision,1);assert.equal(field.value,'{"encoding":"utf-8"}');
await dbStagesLoad(true);assert.equal(p.stage.revision,2);assert.equal($('db-stages-name').value,'renamed');assert.equal(p.save,null);assert.match($('db-stages-list').options.find(o=>o.value==='stage-1').textContent,/v2/);
''')

    def test_unknown_explicit_observe_keeps_payload_key_and_original_context(self):
        self.javascript(r'''
await createStage();await previewStage();submitFailure=true;await dbStagesSubmit('ingest');const attempt=p.run,key=attempt.key,context=structuredClone(attempt.context),first=stagePosts().at(-1);assert.equal(attempt.status,'unknown');await dbStagesSubmit('export');assert.equal(stagePosts().filter(c=>c.path.endsWith('/imports')).length,1);$('db-stages-export-file').value='changed.csv';await dbStagesObserve();assert.equal(p.run,attempt);assert.equal(stagePosts().filter(c=>c.path.endsWith('/imports')).length,2);assert.equal(stagePosts().at(-1).options.body,first.options.body);assert.equal(stagePosts().at(-1).options.headers['Idempotency-Key'],key);assert.equal(attempt.key,key);
attempt.id='run-1';runs=[{id:'run-1',stage_id:'stage-1',stage_revision:1,owner:'owner',context,status:'unknown',committed:true}];$('db-stages-schema').value='raw';await $('db-stages-schema').onchange();await dbStagesObserve();await dbStagesObserve(true);const cancel=stagePosts().at(-1);assert.equal(cancel.path,'/api/v1/stages/stage-1/runs/run-1/cancel');assert.deepEqual(JSON.parse(cancel.options.body),{context});assert.equal(cancel.options.headers['X-Shirokuma-Role'],'admin');assert.equal(p.run.status,'canceled');
''')

    def test_revocation_and_late_acceptance_purge_protected_data(self):
        self.javascript(r'''
await createStage();await previewStage();gate=deferred();const submitting=dbStagesSubmit('ingest');await tick();inventory=[];await dbRefreshAccess();assert.equal(p.invalid,true);assert.equal(p.drafts.size,0);assert.equal(p.files.length,0);assert.equal(p.preview,null);assert.equal($('db-stages-name').value,'');
gate.resolve();await submitting;assert.equal(p.run.id,'run-1');assert.equal(p.run.details,null);assert.equal(p.run.purged,true);assert.equal($('db-stages-cancel').disabled,false);
currentRole='bi_viewer';dbApplyRole();await dbStagesObserve(true);assert.equal(p.run.status,'canceled');assert.equal(p.run.details,null);
''')

    def test_late_preview_load_and_role_loss_cannot_restore_data(self):
        self.javascript(r'''
await createStage();gate=deferred();const previewing=dbStagesPreview();await tick();schemas[0].capabilities.stage_admin=false;await dbStagesRefreshAuthorization();gate.resolve();await previewing;assert.equal(p.preview,null);assert.equal(p.stage,null);assert.equal(p.drafts.size,0);assert.equal($('db-stages-preview-detail').children.length,0);
schemas[0].capabilities.stage_admin=true;await dbStagesRefresh();$('db-stages-list').value='stage-1';loadGate=deferred();const loading=dbStagesLoad();await tick();currentRole='data_engineer';dbApplyRole();loadGate.resolve();await loading;assert.equal(p.stage,null);assert.equal(p.invalid,true);assert.equal($('db-stages-name').value,'');
''')

    def test_owner_and_both_capabilities_are_required_without_fallback(self):
        self.javascript(r'''
await createStage();p.stage.owner='other';dbStagesRender();const before=stagePosts().length;await dbStagesSave();await dbStagesPreview();assert.equal(stagePosts().length,before);assert.equal($('db-stages-save').disabled,true);p.stage.owner='owner';
inventory[0].capabilities.stage_admin=false;await dbStagesRefreshAuthorization();assert.equal(p.invalid,true);assert.equal(p.stage,null);
inventory[0].capabilities.stage_admin=true;schemas[0].capabilities.stage_admin=false;await dbStagesRefresh();assert.equal(p.invalid,true);assert.equal($('db-stages-save').disabled,true);assert.equal(schemas[0].capabilities.write,true);
''')

    def test_independent_scoped_drafts_and_cross_schema_table_export(self):
        self.javascript(r'''
await createStage();$('db-stages-options').value='{"encoding":"utf-8"}';$('db-stages-options').oninput();const context=structuredClone(dbCurrent()),explorer=dbExplorer.schema_id;
$('db-stages-schema').value='raw';await $('db-stages-schema').onchange();$('db-stages-name').value='raw_draft';$('db-stages-name').oninput();$('db-stages-schema').value='schema';await $('db-stages-schema').onchange();$('db-stages-list').value='stage-1';await dbStagesLoad();assert.equal($('db-stages-options').value,'{"encoding":"utf-8"}');assert.equal(dbCurrent().schema_id,context.schema_id);assert.equal(dbExplorer.schema_id,explorer);
savedStages[0]=structuredClone(savedStages[0]);savedStages[0].definition.mode='append';delete savedStages[0].definition.destination;savedStages[0].definition.target={incarnation:1,object_id:'table',schema_id:'raw',database_id:'db'};
await dbStagesLoad(true);assert.equal($('db-stages-target').value,JSON.stringify(stageRef('raw')));assert.equal($('db-stages-target').options.find(o=>o.value===$('db-stages-target').value).disabled,false);$('db-stages-export-source').value=JSON.stringify(stageRef('raw'));$('db-stages-export-file').value='all.jsonl';$('db-stages-export-format').value='jsonl';await dbStagesSubmit('export');const post=stagePosts().at(-1);assert.equal(post.path,'/api/v1/stages/stage-1/exports');assert.equal(JSON.parse(post.options.body).source.schema_id,'raw');assert.equal(JSON.parse(post.options.body).format,'jsonl');assert.equal(p.objects.some(o=>o.kind==='VIEW'),false);assert.equal($('db-stages-export-format').options.some(o=>o.value==='parquet'),false);
''')

    def test_p3_receipt_metadata_gating_and_no_legacy_scan(self):
        self.javascript(r'''
await createStage();await dbStagesRead('exports');$('db-stages-receipt').value='export-1';await dbStagesReceipt();assert.equal(p.receipt.written_count,1);assert.equal($('db-stages-download').disabled,false);assert.ok($('db-stages-receipt-detail').descendants().some(n=>n.textContent===': 42'));
receipt.publication_state='partial';await dbStagesReceipt();assert.equal($('db-stages-download').disabled,true);receipt.publication_state='published';receipt.format='parquet';await dbStagesReceipt();assert.equal($('db-stages-download').disabled,true);await assert.rejects(()=>api('/api/stages'));assert.equal(calls.some(c=>c.path==='/api/stages'),false);
''')

    def test_localization_uses_real_text_node_bindings_and_management_admin_option(self):
        self.javascript(r'''
await createStage();const save=$('db-stages-save'),status=$('db-stages-status'),setBinding=uiBindings.set.bind(uiBindings);uiBindings.delete(status);
Object.defineProperty(status,'textContent',{configurable:true,get(){return this.children[0]?.textContent||'';},set(value){this.children[0].textContent=value;}});
uiBindings.set=function(node,binding){if(node===status){for(const child of node.children)child.isConnected=false;node.children=[{nodeType:3,textContent:'',isConnected:true,descendants:()=>[]}];return setBinding(node.children[0],binding);}return setBinding(node,binding);};
ui(status,'Stage request failed. Draft retained.');setLanguage('ja');assert.equal(save.textContent,'Stage を保存');assert.equal($('db-stages-mode').options.find(o=>o.value==='create').textContent,'新規作成');assert.equal(status.textContent,'Stage 要求が失敗しました。下書きは保持されています。');dbStagesRender();await periodicRefresh();assert.equal(status.textContent,'Stage 要求が失敗しました。下書きは保持されています。');setLanguage('en');assert.equal(status.textContent,'Stage request failed. Draft retained.');
session.platform_admin=true;await dbRefreshAccess();dbAdmin.databases=[{id:'db',sql_catalog:'sales',display_name:'Sales',description:'',state:'READY',revision:1,schemas:[],binding:{}}];dbAdmin.database='db';dbAdminRenderDetail();const role=$('db-admin-grant-role');assert.deepEqual(role.options.map(o=>o.value),['bi_viewer','data_engineer','admin']);setLanguage('ja');assert.equal(role.options.at(-1).textContent,'管理者');assert.match($('db-admin-grant-admin-help').textContent,/既存の全体ロール上限 admin/);
''')

    def test_management_admin_rejection_keeps_principal_cas_draft_and_focus(self):
        harness.DatabaseUI.javascript(self, admin_harness.SETUP + r'''
const principal=$('db-admin-grant-principal'),role=$('db-admin-grant-role'),revision=$('db-admin-grant-revision');principal.value='engineer_target';role.value='admin';revision.value='4';principal.focus();principal.setSelectionRange(2,5);
adminRoute=async()=>({http:400,body:{error:'Target must already have global admin ceiling'}});await $('db-admin-grant-save').onclick();
const call=writeCalls().at(-1);assert.deepEqual(JSON.parse(call.options.body),{principal_id:'engineer_target',maximum_role:'admin',expected_revision:4});assert.match($('db-admin-grant-state').textContent,/Request rejected/);assert.match($('db-admin-grant-result').textContent,/global admin ceiling/);assert.equal(principal.value,'engineer_target');assert.equal(revision.value,'4');assert.equal(role.value,'admin');assert.equal(document.activeElement,principal);assert.equal(principal.selectionStart,2);assert.equal($('db-admin-grant-principal'),principal);assert.equal(calls.some(c=>c.path.includes('/principals')||c.path.includes('/accounts')),false);
''')

    def test_same_named_databases_keep_drafts_and_worksheet_context_independent(self):
        self.javascript(r'''
inventory.push({...structuredClone(inventory[0]),id:'finance',sql_catalog:'finance'});schemasRoute=async path=>({items:schemas.map(s=>({...s,database_id:path.includes('/finance/')?'finance':'db'}))});
await openStages();$('db-stages-name').value='sales_draft';$('db-stages-name').oninput();
$('db-stages-database').value='finance';await $('db-stages-database').onchange();$('db-stages-schema').value='schema';await $('db-stages-schema').onchange();assert.equal(p.context.database_id,'finance');$('db-stages-name').value='finance_draft';$('db-stages-name').oninput();
$('db-stages-database').value='db';await $('db-stages-database').onchange();$('db-stages-schema').value='schema';await $('db-stages-schema').onchange();assert.equal($('db-stages-name').value,'sales_draft');assert.equal(dbCurrent().database_id,'db');assert.equal(dbExplorer.database_id,'db');assert.equal(worksheets.length,2);assert.equal(stagePosts().length,0);
''')

    def test_new_stage_normalizes_empty_or_invalid_selects_after_database_refresh(self):
        for carryover in ('', 'unsupported'):
            with self.subTest(carryover=carryover):
                self.javascript(r'''
inventory.push({...structuredClone(inventory[0]),id:'finance',sql_catalog:'finance',binding_revision:7,generation_id:'finance-gen',grant_revision:9});
const financeSchema={...structuredClone(schemas[1]),id:'finance-raw',database_id:'finance'};
schemasRoute=async path=>({items:path.includes('/finance/')?[financeSchema]:schemas});
await createStage();const salesStage=structuredClone(p.stage);
$('db-stages-database').value='finance';await $('db-stages-database').onchange();assert.equal(p.context,null);assert.equal(p.invalid,true);
$('db-stages-schema').value='finance-raw';await $('db-stages-schema').onchange();assert.equal(p.invalid,false);
// Reproduce the cleared/invalid select carryover before the explicit Refresh -> New Stage sequence.
for(const key of ['mode','format','export-format'])$('db-stages-'+key).value=CARRYOVER;
await $('db-stages-refresh').onclick();assert.equal(p.drafts.get(dbStagesKey()).values.mode,CARRYOVER);
await $('db-stages-new').onclick();
for(const [key,value] of [['mode','create'],['format','auto'],['export-format','csv']])assert.equal($('db-stages-'+key).value,value,key);
const context={database_id:'finance',schema_id:'finance-raw',warehouse_id:'LOCAL',sql_catalog:'finance',sql_schema:'raw',selected_role:'admin',binding_revision:7,generation_id:'finance-gen',grant_revision:9};assert.deepEqual(p.context,context);
$('db-stages-name').value='finance_daily';$('db-stages-directory').value='/approved/finance';$('db-stages-destination').value='codes';
const options={schema_hints:{code:'VARCHAR',amount:'DECIMAL(20,4)'},encoding:'utf-8',delimiter:';',batch_size:73,atomic_append:true};$('db-stages-options').value=JSON.stringify(options);$('db-stages-options').oninput();
const before=stagePosts().length;await $('db-stages-save').onclick();assert.equal(stagePosts().length,before+1);
const body=JSON.parse(stagePosts().at(-1).options.body);assert.deepEqual(body,{context,expected_revision:0,definition:{...options,name:'finance_daily',directory:'/approved/finance',mode:'create',format:'auto',destination:{database_id:'finance',schema_id:'finance-raw',physical_name:'codes'}}});
assert.equal(p.stage.revision,1);assert.equal($('db-stages-status').textContent,t('Stage saved.'));assert.equal($('db-stages-export-format').value,'csv');
assert.deepEqual(p.drafts.get(dbStagesKey(salesStage.context,salesStage.id)).stage,salesStage);assert.equal(dbCurrent().database_id,'db');assert.equal(dbExplorer.database_id,'db');
'''.replace('CARRYOVER', json.dumps(carryover)))

    def test_new_stage_restores_valid_selects_options_focus_and_run_identity(self):
        self.javascript(r'''
await createStage();await previewStage();await dbStagesSubmit('ingest');runs[0].status='succeeded';await dbStagesObserve();
const accepted=p.run,key=accepted.key,context=accepted.context,stageId=accepted.stage_id,revision=accepted.stage_revision;
assert.equal(Object.keys(context).length,9);assert.ok(Object.isFrozen(context));
await $('db-stages-new').onclick();
for(const [mode,format,exportFormat] of [['replace','csv','csv'],['append','json','jsonl'],['upsert','jsonl','csv']]){
 $('db-stages-mode').value=mode;$('db-stages-format').value=format;$('db-stages-export-format').value=exportFormat;
 $('db-stages-target').value=JSON.stringify(stageRef());const field=$('db-stages-options'),options=JSON.stringify({schema_hints:{code:'VARCHAR'},key_columns:['code'],batch_size:17});field.value=options;field.oninput();field.focus();field.setSelectionRange(2,7);
 await $('db-stages-refresh').onclick();await $('db-stages-new').onclick();
 assert.equal(p.stage,null);assert.equal($('db-stages-mode').value,mode);assert.equal($('db-stages-format').value,format);assert.equal($('db-stages-export-format').value,exportFormat);assert.equal($('db-stages-target').value,JSON.stringify(stageRef()));
 assert.equal(field.value,options);assert.equal(document.activeElement,field);assert.equal(field.selectionStart,2);assert.equal(field.selectionEnd,7);
 assert.equal(p.run,accepted);assert.equal(accepted.key,key);assert.equal(accepted.context,context);assert.equal(accepted.stage_id,stageId);assert.equal(accepted.stage_revision,revision);assert.equal(accepted.accepted,true);assert.equal(accepted.status,'succeeded');
}
''')

    def test_late_preview_after_edit_and_identity_loss_cannot_restore_protected_state(self):
        self.javascript(r'''
await createStage();gate=deferred();const loading=dbStagesPreview();await tick();$('db-stages-options').value='{"delimiter":";"}';$('db-stages-options').oninput();gate.resolve();await loading;assert.equal(p.preview,null);assert.equal($('db-stages-options').value,'{"delimiter":";"}');
$('db-stages-options').value='{}';$('db-stages-options').oninput();gate=null;await previewStage();await dbStagesSubmit('ingest');await dbStagesRead('exports');$('db-stages-receipt').value='export-1';await dbStagesReceipt();assert.ok(p.receipt);session.username='other';await dbRefreshAccess();assert.equal(p.run,null);assert.equal(p.receipt,null);assert.equal(p.drafts.size,0);assert.equal(p.stage,null);assert.equal($('db-stages-receipt-detail').children.length,0);assert.equal($('db-stages-options').value,'');
''')

    def test_fresh_exact_rejection_releases_only_for_explicit_repreview_en_ja(self):
        for status in (400, 409):
            for language in ('en', 'ja'):
                with self.subTest(status=status, language=language):
                    self.javascript(r'''
await createStage();await previewStage();const status=$('db-stages-status'),setBinding=uiBindings.set.bind(uiBindings);uiBindings.delete(status);
Object.defineProperty(status,'textContent',{configurable:true,get(){return this.children[0]?.textContent||'';},set(value){this.children[0].textContent=value;}});
uiBindings.set=function(node,binding){if(node===status){for(const child of node.children)child.isConnected=false;node.children=[{nodeType:3,textContent:'',isConnected:true,descendants:()=>[]}];return setBinding(node.children[0],binding);}return setBinding(node,binding);};setLanguage(LANGUAGE);
const original=route;let rejected;route=async(path,options)=>{if(path.endsWith('/imports')){rejected=p.run;return {http:HTTP_STATUS,body:{code:'STAGE_INPUT_REJECTED',details:{admission:'NOT_ATTEMPTED',reason:'STAGE_FILE_CHANGED'}}};}return original(path,options);};
await dbStagesSubmit('ingest');const first=stagePosts().at(-1);assert.equal(p.run,null);assert.equal(rejected.sequence,1);assert.ok(Object.isFrozen(rejected.payload));assert.ok(Object.isFrozen(rejected.payload.context));assert.equal(rejected.path,'/api/v1/stages/stage-1/imports');assert.equal(p.preview,null);assert.equal(status.textContent,t('Stage input rejected before admission. Preview again before submitting.'));assert.equal(uiBindings.get(status.children[0]).key,'Stage input rejected before admission. Preview again before submitting.');
dbStagesRender();await periodicRefresh();assert.equal(status.textContent,t('Stage input rejected before admission. Preview again before submitting.'));await dbStagesObserve();await dbStagesSubmit('ingest');assert.equal(stagePosts().at(-1),first);
route=original;await previewStage();await dbStagesSubmit('ingest');const next=stagePosts().at(-1);assert.notEqual(next.options.headers['Idempotency-Key'],first.options.headers['Idempotency-Key']);assert.equal(p.run.status,'running');
'''.replace('HTTP_STATUS', str(status)).replace('LANGUAGE', json.dumps(language)))

    def test_generic_auth_or_impure_rejections_remain_unknown(self):
        marker = dict(code='STAGE_INPUT_REJECTED', details=dict(admission='NOT_ATTEMPTED', reason='INVALID_INPUT'))
        cases = [
            (400, {'code': 'INVALID_INPUT'}), (409, {'code': 'STAGE_FILE_CHANGED'}),
            (500, marker), (401, marker), (403, marker),
            (400, {'code': 'STAGE_INPUT_REJECTED'}),
            (400, dict(marker, details=dict(marker['details'], extra=True))),
            (409, dict(marker, details=dict(admission='ATTEMPTED', reason='INVALID_INPUT'))),
            (400, dict(marker, details=dict(admission='NOT_ATTEMPTED', reason='AUTHENTICATION_REQUIRED'))),
            (400, dict(marker, details=dict(admission='NOT_ATTEMPTED', reason='unsafe reason'))),
            (400, dict(marker, run={'id': 'accepted'})),
            (409, dict(marker, operation_id='accepted')),
            (400, dict(marker, error=dict(marker, run_id='accepted'))),
            (400, dict(marker, error=dict(marker, error={'run': {'id': 'accepted'}}))),
        ]
        for status, body in cases:
            with self.subTest(status=status, body=body):
                self.javascript(r'''
await createStage();await previewStage();const original=route;route=async(path,options)=>path.endsWith('/imports')?REJECTION:original(path,options);
await dbStagesSubmit('ingest');const attempt=p.run,first=stagePosts().at(-1);assert.ok(attempt);assert.equal(attempt.status,'unknown');assert.equal(attempt.id,null);assert.ok(attempt.payload);assert.equal(attempt.ambiguous,true);assert.equal(attempt.sequence,1);assert.equal($('db-stages-confirm').disabled,true);await dbStagesSubmit('ingest');assert.equal(stagePosts().at(-1),first);
'''.replace('REJECTION', json.dumps(dict(http=status, body=body))))

    def test_marker_during_observe_never_releases_unknown_or_accepted_request(self):
        for accepted in (False, True):
            with self.subTest(accepted=accepted):
                self.javascript(r'''
await createStage();await previewStage();submitFailure=!ACCEPTED;await dbStagesSubmit('ingest');const attempt=p.run,payload=attempt.payload,path=attempt.path,key=attempt.key,context=attempt.context;if(ACCEPTED){assert.equal(payload,null);assert.equal(path,null);}else{assert.ok(payload);assert.ok(Object.isFrozen(payload));assert.ok(Object.isFrozen(payload.context));assert.equal(path,'/api/v1/stages/stage-1/imports');}const original=route;
route=async(url,options)=>(url===path||url.includes('/runs/'))?{http:409,body:{code:'STAGE_INPUT_REJECTED',details:{admission:'NOT_ATTEMPTED',reason:'STAGE_FILE_CHANGED'}}}:original(url,options);
await dbStagesObserve();assert.equal(p.run,attempt);assert.equal(attempt.payload,payload);assert.equal(attempt.path,path);assert.equal(attempt.key,key);assert.equal(attempt.context,context);assert.equal(attempt.sequence,2);assert.equal(attempt.status,ACCEPTED?'running':'unknown');assert.equal(attempt.accepted,ACCEPTED);assert.equal($('db-stages-export').disabled,true);const posts=stagePosts().length;await dbStagesSubmit('export');assert.equal(stagePosts().length,posts);
'''.replace('ACCEPTED', 'true' if accepted else 'false'))

    def test_revoked_minimal_cancel_keeps_safe_handle_without_accepted_payload(self):
        self.javascript(r'''
await createStage();await previewStage();await dbStagesSubmit('ingest');const r=p.run,payload=r.payload,path=r.path,key=r.key,context=r.context,revision=r.stage_revision;assert.equal(payload,null);assert.equal(path,null);inventory=[];await dbRefreshAccess();assert.equal(r.purged,true);assert.equal(r.payload,null);assert.equal(r.path,null);assert.equal(r.details,null);
const original=route;route=async(url,options)=>url.endsWith('/cancel')?{context,stage_id:r.stage_id,run:{id:r.id,stage_id:r.stage_id,owner:'owner',status:'canceled',operation_id:'operation'}}:original(url,options);
currentRole='bi_viewer';dbApplyRole();await dbStagesObserve(true);assert.equal(r.status,'canceled');assert.equal(r.details,null);assert.equal(r.context,context);assert.equal(r.stage_revision,revision);assert.equal(r.payload,payload);assert.equal(r.path,path);assert.equal(r.key,key);assert.equal(r.operation_id,'operation');assert.deepEqual(JSON.parse(stagePosts().at(-1).options.body),{context});assert.equal(stagePosts().at(-1).options.headers['X-Shirokuma-Role'],'admin');assert.equal($('db-stages-run-detail').descendants().some(n=>n.textContent.includes('input.csv')),false);
''')

    def test_minimal_cancel_wrong_identity_context_and_normal_get_are_rejected(self):
        cases = [('cancel', {'id': 'wrong'}, False), ('cancel', {'stage_id': 'wrong'}, False),
                 ('cancel', {'owner': 'wrong'}, False), ('cancel', {'status': 'unsafe'}, False),
                 ('cancel', {}, True), ('observe', {}, False)]
        for action, change, wrong_context in cases:
            with self.subTest(action=action, change=change, wrong_context=wrong_context):
                self.javascript(r'''
await createStage();await previewStage();await dbStagesSubmit('ingest');const r=p.run,context=r.context,revision=r.stage_revision,original=route;
route=async(url,options)=>url.includes('/runs/')?{context:WRONG_CONTEXT?{...context,grant_revision:999}:context,run:{id:r.id,stage_id:r.stage_id,owner:'owner',status:'canceled',operation_id:'operation',...CHANGE}}:original(url,options);
await dbStagesObserve(CANCEL);assert.equal(p.run,r);assert.equal(r.status,'running');assert.equal(r.context,context);assert.equal(r.stage_revision,revision);assert.notEqual(r.details?.status,'canceled');
'''.replace('WRONG_CONTEXT', 'true' if wrong_context else 'false').replace('CHANGE', json.dumps(change)).replace('CANCEL', 'true' if action == 'cancel' else 'false'))

    def test_canonical_save_and_old_saved_epochs_keep_editor_focus_and_options(self):
        self.javascript(r'''
await openStages();$('db-stages-name').value='daily';$('db-stages-directory').value='/approved/../approved/input';$('db-stages-destination').value='codes';const field=$('db-stages-options');field.value='{"encoding":"utf-8"}';field.oninput();field.focus();field.setSelectionRange(2,7);const original=route;
route=async(url,options)=>{const reply=await original(url,options);if(url==='/api/v1/stages'&&options?.body){reply.stage.context={...reply.stage.context,generation_id:'old',grant_revision:1,binding_revision:99};reply.stage.definition.directory='/approved/input';savedStages=[structuredClone(reply.stage)];}return reply;};
await dbStagesSave();assert.equal(p.stage.revision,1);assert.equal($('db-stages-directory').value,'/approved/input');assert.equal($('db-stages-options'),field);assert.equal(field.value,'{"encoding":"utf-8"}');assert.equal(field.selectionStart,2);assert.equal(document.activeElement,field);assert.equal(dbStagesMatches(),true);assert.notEqual(p.stage.context.generation_id,p.context.generation_id);
$('db-stages-list').value='stage-1';await dbStagesLoad(true);assert.equal(p.stage.context.generation_id,'old');assert.equal(p.context.generation_id,'gen');assert.equal(dbStagesMatches(),true);$('db-stages-file').value='input.csv';await previewStage();
''')

    def test_load_rejects_wrong_immutable_identity_without_exposing_response(self):
        cases = [{'id': 'wrong'}, {'owner': 'wrong'}, {'selected_role': 'data_engineer'},
                 {'context': {'database_id': 'other'}}, {'context': {'schema_id': 'raw'}},
                 {'context': {'selected_role': 'data_engineer'}}]
        for change in cases:
            with self.subTest(change=change):
                self.javascript(r'''
await createStage();const stage=p.stage,field=$('db-stages-name');field.value='retained_draft';field.oninput();const original=route;
route=async(url,options)=>{const reply=await original(url,options);if(url.startsWith('/api/v1/stages/stage-1?')){reply.stage=structuredClone(reply.stage);const change=CHANGE;if(change.context)reply.stage.context={...reply.stage.context,...change.context};else Object.assign(reply.stage,change);reply.stage.definition.name='must_not_display';}return reply;};
$('db-stages-list').value='stage-1';await dbStagesLoad(true);assert.equal(p.stage,stage);assert.equal(field.value,'retained_draft');assert.equal($('db-stages-status').textContent,t('Stage request failed. Draft retained.'));
'''.replace('CHANGE', json.dumps(change)))

    def test_receipt_metadata_and_explicit_publication_block_content_fetch(self):
        mutations = [dict(publication_state=None), dict(publication_state='partial'), dict(publication_state='PUBLISHED'),
                     dict(stage_id='wrong'), dict(export_id='wrong'), dict(stage_revision=2), dict(run_id=''),
                     dict(file='../bad.csv'), dict(owner='wrong'), dict(context={'database_id': 'other'}),
                     dict(source={'ref': {'database_id': 'other'}})]
        for change in mutations:
            with self.subTest(change=change):
                self.javascript(r'''
await createStage();Object.assign(receipt,CHANGE);await dbStagesRead('exports');$('db-stages-receipt').value='export-1';await dbStagesReceipt(true);assert.equal(calls.some(c=>c.path.includes('/content?')),false);assert.equal($('db-stages-download').disabled,true);
'''.replace('CHANGE', json.dumps(change)))

    def test_reference_order_normalizes_existing_draft_and_export_selection(self):
        self.javascript(r'''
await createStage();const reordered=JSON.stringify({incarnation:1,object_id:'table',schema_id:'raw',database_id:'db'});$('db-stages-export-source').value=reordered;dbStagesCapture();const draft=p.drafts.get(dbStagesKey());draft.values.target=reordered;draft.values['export-source']=reordered;dbStagesEditor(p.stage);
for(const key of ['target','export-source']){const node=$('db-stages-'+key);assert.equal(node.value,JSON.stringify(stageRef()));assert.equal(node.options.find(o=>o.value===node.value).disabled,false);assert.equal(node.options.some(o=>o.textContent.includes(t('Unavailable'))),false);}
''')

    def test_stage_navigation_tracks_ready_admin_database_capability_and_header(self):
        self.javascript(r'''
delete inventory[0].capabilities.stage_admin;await boot();const tab=$('ingestion-view-stages');assert.equal(tab.disabled,true);assert.equal(p.started,false);
inventory[0].capabilities.stage_admin=false;await dbRefreshAccess();assert.equal(tab.disabled,true);
inventory[0].capabilities.stage_admin=true;inventory[0].state='REGISTERED';await dbRefreshAccess();assert.equal(tab.disabled,true);
inventory[0].state='READY';inventory[0].allowed_roles=['data_engineer'];await dbRefreshAccess();assert.equal(tab.disabled,true);
inventory[0].allowed_roles=['admin','data_engineer','bi_viewer'];await dbRefreshAccess();assert.equal(tab.disabled,false,'restored admin grant');assert.equal(p.started,false);
currentRole='data_engineer';dbApplyRole();assert.equal(tab.disabled,true);currentRole='admin';dbApplyRole();assert.equal(tab.disabled,true,'header change clears current authorization inventory');await dbRefreshAccess();assert.equal(tab.disabled,false,'refreshed admin header and grant');
inventory[0].capabilities.stage_admin=false;await dbRefreshAccess();assert.equal(tab.disabled,true);inventory[0].capabilities.stage_admin=true;await dbRefreshAccess();assert.equal(tab.disabled,false,'restored Stage capability');assert.equal(p.started,false);
''')

    def test_pending_request_survives_purge_but_validated_late_id_drops_payload(self):
        self.javascript(r'''
await createStage();await previewStage();gate=deferred();const submitting=dbStagesSubmit('ingest');await tick();const r=p.run,payload=r.payload,path=r.path,key=r.key,context=r.context;assert.equal(r.id,null);assert.ok(Object.isFrozen(payload));assert.ok(Object.isFrozen(payload.context));assert.equal(path,'/api/v1/stages/stage-1/imports');
inventory=[];await dbRefreshAccess();assert.equal(r.purged,true);assert.equal(r.payload,payload);assert.equal(r.path,path);assert.equal(r.key,key);assert.equal(r.context,context);assert.equal($('db-stages-run-detail').descendants().some(n=>n.textContent.includes('input.csv')),false);
gate.resolve();await submitting;assert.equal(r.id,'run-1');assert.equal(r.accepted,true);assert.equal(r.payload,null);assert.equal(r.path,null);assert.equal(r.details,null);assert.equal(r.key,key);assert.equal(r.context,context);assert.equal($('db-stages-cancel').disabled,false);
''')


if __name__ == '__main__':
    unittest.main()
