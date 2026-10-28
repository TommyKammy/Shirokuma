"""Execute the shipped DataOps handlers with the existing Node DOM/fetch adapter."""
import unittest

import test_workbench_database_ui as database_ui


SETUP = r'''
// The adapter intentionally keeps browser APIs small; these support dynamic rows.
Node.prototype.remove=function(){if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(n=>n!==this);};
const appendNodes=Node.prototype.append;Node.prototype.append=function(...items){return appendNodes.apply(this,items.map(n=>typeof n==='string'?Object.assign(new Node('text'),{textContent:n}):n));};
const ref=(id,schema='raw')=>({database_id:'db',schema_id:schema,object_id:id,incarnation:1});
const stable=(id='orders',schema='raw')=>({id,ref:ref(id,schema),kind:'TABLE',physical_name:id,display_name:id,capabilities:{resolve:true,preview:true,export:true}});
for(const schema of schemas)Object.assign(schema.capabilities,{manual_ingest:schema.id==='raw',wap_models:true,wap_build:true,wap_audit:true,wap_publish:true,wap_rollback:true});
objects=[stable(),stable('other')];
const accepted=(context,operation={id:'operation',state:'FINISHED',committed:true,result:{},cleanup_error:null})=>({context:structuredClone(context),operation});
let submissions=[],previewCount=0;
route=async(path,options)=>{
 const body=options?.body?JSON.parse(options.body):null;
 const context=body?.context||JSON.parse(new URL('http://localhost'+path).searchParams.get('context'));
 if(path.startsWith('/api/v1/wap/models')&&!body)return {context,items:[]};
 if(path==='/api/v1/ingests/preview'){previewCount++;return {context,preview:{request_fingerprint:'fingerprint-'+previewCount,input_fingerprint:'bytes',columns:[{name:'code',type:'varchar'}],rows:[['001']],total_rows:1}};}
 if(body)submissions.push({path,body,headers:options.headers});
 return accepted(context);
};
async function ready(id='p35-ingest'){
 await boot([{...input(),schema_id:'raw'}]);
 const p=dataopsPanels.get(id);p.started=true;await dataopsRefresh(p);assert.equal(p.invalid,false);return p;
}
async function preview(p){
 $('p35-ingest-mode').value='create';$('p35-ingest-name').value='chosen_table';
 await dataopsReadFile(p,{name:'different_filename.csv',arrayBuffer:async()=>Uint8Array.from([0x63,0x0a,0x80,0x0a]).buffer});
 await $('p35-ingest-preview').onclick();assert.ok(p.preview);return p.preview;
}
'''


class DataopsUI(unittest.TestCase):
    def javascript(self, body):
        database_ui.DatabaseUI.javascript(self, SETUP + '\n' + body)

    def test_bytes_once_immutable_preview_and_explicit_confirmation(self):
        self.javascript(r'''
const p=await ready();let reads=0;
$('p35-ingest-mode').value='create';$('p35-ingest-name').value='chosen_table';
await dataopsReadFile(p,{name:'not_the_target.csv',arrayBuffer:async()=>{reads++;return Uint8Array.from([0x80,0xff,10]).buffer;}});
assert.equal(p.file.content_base64,Buffer.from([0x80,0xff,10]).toString('base64'));
assert.equal($('p35-ingest-name').value,'chosen_table');assert.equal(submissions.length,0);
await $('p35-ingest-preview').onclick();const saved=p.preview;
assert.equal(reads,1);assert.equal(submissions.length,0);assert.ok(Object.isFrozen(saved.payload));assert.ok(Object.isFrozen(saved.payload.context));
await $('p35-ingest-confirm').onclick();await $('p35-ingest-confirm').onclick();
assert.equal(submissions.length,1);assert.equal(submissions[0].headers['Idempotency-Key'],saved.key);
assert.equal(submissions[0].body.content_base64,saved.payload.content_base64);
assert.equal(submissions[0].body.expected_preview_fingerprint,saved.fingerprint);
assert.deepEqual(submissions[0].body.destination,{database_id:'db',schema_id:'raw',physical_name:'chosen_table'});
assert.equal(reads,1);assert.equal($('p35-ingest-confirm').disabled,true);
''')

    def test_every_ingest_option_invalidates_confirmation(self):
        self.javascript(r'''
const p=await ready();
for(const key of ['mode','name','target','format','encoding','delimiter','hints','keys','atomic','batch']){
 await preview(p);$('p35-ingest-'+key).oninput();assert.equal(p.preview,null,key);assert.equal($('p35-ingest-confirm').disabled,true,key);
}
await preview(p);await dataopsReadFile(p,null);assert.equal(p.preview,null);assert.equal(p.file,null);
await $('p35-ingest-confirm').onclick();assert.equal(submissions.length,0);
''')

    def test_late_preview_and_file_read_cannot_replace_new_inputs(self):
        self.javascript(r'''
const p=await ready(),fileWait=deferred();
const oldRead=dataopsReadFile(p,{name:'old.csv',arrayBuffer:()=>fileWait.promise});
await dataopsReadFile(p,{name:'new.csv',arrayBuffer:async()=>Uint8Array.from([1]).buffer});
fileWait.resolve(Uint8Array.from([2]).buffer);await oldRead;assert.equal(p.file.filename,'new.csv');
$('p35-ingest-mode').value='create';$('p35-ingest-name').value='first';
const wait=deferred(),original=route;route=async(path,options)=>path.endsWith('/preview')?wait.promise:original(path,options);
const work=$('p35-ingest-preview').onclick();await tick();const context=structuredClone(p.context);
$('p35-ingest-name').value='second';$('p35-ingest-name').oninput();
wait.resolve({context,preview:{request_fingerprint:'old',columns:[],rows:[],total_rows:0}});await work;
assert.equal(p.preview,null);assert.equal($('p35-ingest-confirm').disabled,true);assert.equal($('p35-ingest-name').value,'second');
''')

    def test_ambiguous_submit_is_never_reposted(self):
        self.javascript(r'''
const p=await ready(),saved=await preview(p);let posts=0;const original=route;
route=async(path,options)=>{if(path==='/api/v1/ingests'){posts++;throw Error('response lost');}return original(path,options);};
await $('p35-ingest-confirm').onclick();await $('p35-ingest-confirm').onclick();
assert.equal(posts,1);assert.equal(p.op.key,saved.key);assert.equal(p.op.ambiguous,true);assert.equal($('p35-ingest-confirm').disabled,true);
assert.ok(p.receipt.descendants().some(n=>n.textContent.includes('Outcome unknown')));
''')

    def test_followups_keep_original_role_and_context_after_grant_loss(self):
        self.javascript(r'''
const p=await ready();p.role='data_engineer';await dataopsRefresh(p);await preview(p);await $('p35-ingest-confirm').onclick();
const frozen=structuredClone(p.op.context);assert.equal(frozen.selected_role,'data_engineer');
p.op.state='RUNNING';p.op.operation.state='RUNNING';
p.role='admin';dataopsInvalidate(p);let captured;
route=async(path,options)=>{captured={path,options};const c=options?.body?JSON.parse(options.body).context:JSON.parse(new URL('http://localhost'+path).searchParams.get('context'));return accepted(c);};
await $('p35-ingest-cancel').onclick();
assert.equal(captured.options.headers['X-Shirokuma-Role'],'data_engineer');assert.deepEqual(JSON.parse(captured.options.body).context,frozen);
assert.equal(p.op.protected,false);assert.equal(p.op.operation,null);assert.equal($('p35-ingest-confirm').disabled,true);
''')

    def test_panels_and_worksheet_state_are_independent_with_focus_preserved(self):
        self.javascript(r'''
const p=await ready(),w=dataopsPanels.get('p35-wap');await dataopsRefresh(w);
const snapshot=JSON.stringify(worksheets);const context=JSON.stringify(p.context);
$('p35-ingest-encoding').value='shift_jis';$('p35-ingest-encoding').focus();
dataopsValidate();dataopsRender(p);await dbRefreshAccess();
assert.equal(document.activeElement,$('p35-ingest-encoding'));assert.equal($('p35-ingest-encoding').value,'shift_jis');
assert.equal(JSON.stringify(worksheets),snapshot);assert.equal(JSON.stringify(p.context),context);
w.role='data_engineer';await dataopsRefresh(w);assert.equal(p.role,'admin');assert.equal(JSON.stringify(worksheets),snapshot);
''')

    def test_wap_multiple_sources_cas_exact_build_audit_and_admin_context(self):
        self.javascript(r'''
const p=await ready('p35-wap');p.role='data_engineer';
schemas.forEach(s=>Object.assign(s.capabilities,{wap_publish:false,wap_rollback:false}));await dataopsRefresh(p);
const source=p.sources[0];source.table.value='0';source.alias.value='source';
dataopsAddSource(p,'other');p.sources[1].table.value='1';
$('p35-wap-model-id').value='daily_orders';$('p35-wap-name').value='daily_orders';$('p35-wap-target-schema').value='schema';
$('p35-wap-sql').value='SELECT source.* FROM source JOIN other ON source.code = other.code';
let version=0;const original=route;
route=async(path,options)=>{
 const b=options?.body&&JSON.parse(options.body);if(!b)return original(path,options);
 submissions.push({path,body:b,headers:options.headers});
 if(path==='/api/v1/wap/models')return {context:b.context,model:{...b,version:++version}};
 const key=path.endsWith('/build')?'build_id':path.endsWith('/audit')?'audit_id':path.endsWith('/publish')?'publication_id':'id';
 return accepted(b.context,{id:'op-'+version,state:'FINISHED',committed:true,result:{[key]:key+'-record'},cleanup_error:null});
};
await $('p35-wap-save').onclick();assert.equal(submissions[0].body.expected_version,null);
assert.deepEqual(submissions[0].body.sources,{source:ref('orders'),other:ref('other')});assert.equal(p.model.version,1);
await $('p35-wap-build-action').onclick();assert.equal(submissions[1].body.version,1);assert.equal(submissions[1].body.model_id,'daily_orders');
assert.equal($('p35-wap-build').value,'build_id-record');await $('p35-wap-audit-action').onclick();assert.equal(submissions[2].body.build_id,'build_id-record');
assert.equal($('p35-wap-publish').disabled,true);await $('p35-wap-publish').onclick();assert.equal(submissions.length,3);
$('p35-wap-sql').oninput();assert.equal($('p35-wap-build-action').disabled,true);await $('p35-wap-save').onclick();assert.equal(submissions[3].body.expected_version,1);
// Admin loads shared WAP identifiers into an independently resolved context.
p.role='admin';schemas.forEach(s=>Object.assign(s.capabilities,{wap_publish:true,wap_rollback:true}));await dataopsRefresh(p);
$('p35-wap-build').add(new Option('engineer build','build_id-record'));$('p35-wap-build').value='build_id-record';
$('p35-wap-audit').add(new Option('engineer audit','audit_id-record'));$('p35-wap-audit').value='audit_id-record';dataopsRender(p);
await $('p35-wap-publish').onclick();const publish=submissions.at(-1);
assert.equal(publish.body.context.selected_role,'admin');assert.equal(publish.headers['X-Shirokuma-Role'],'admin');assert.equal(publish.body.build_id,'build_id-record');
''')

    def test_grant_revision_hides_cached_samples_models_and_operation_results(self):
        self.javascript(r'''
const p=await ready();await preview(p);await $('p35-ingest-confirm').onclick();
assert.ok(p.op.operation);inventory[0].grant_revision++;
await dbRefreshAccess();assert.equal(p.invalid,true);assert.equal(p.preview,null);assert.equal(p.op.operation,null);assert.equal(p.op.protected,false);assert.equal(p.objects.length,0);
assert.equal($('p35-ingest-sample').children.length,0);assert.equal($('p35-ingest-confirm').disabled,true);
await dataopsRefresh(p);assert.equal(p.invalid,false);assert.equal(p.preview,null);assert.equal($('p35-ingest-confirm').disabled,true);
''')

    def test_export_views_disabled_and_receipt_uses_frozen_explorer_context(self):
        self.javascript(r'''
await ready();await dbRefreshExplorer();dbObject=stable();dbExplorerControls();assert.equal($('export-table-btn').disabled,false);
for(const object of [{...stable(),kind:'VIEW'},{...stable(),kind:'VIEW',ref:null}]){dbObject=object;dbExplorerControls();assert.equal($('export-table-btn').disabled,true);assert.match($('export-table-btn').title,/View export/);}
dbObject=stable();dbExplorerControls();const wait=deferred(),original=route;
route=async(path,options)=>{
 if(path==='/api/v1/table-exports')return wait.promise;
 const c=JSON.parse(new URL('http://localhost'+path).searchParams.get('context'));
 if(path.includes('/table-exports/'))return {context:c,receipt:{owner:'owner',context:c,source:{snapshot_id:'9007199254740993'},format:'jsonl',written_count:123,byte_count:456}};
 if(path.includes('/dataops/'))return accepted(c,{id:'export-operation',state:'FINISHED',committed:false,result:{export_id:'export-record'},cleanup_error:null});
 return original(path,options);
};
$('export-table-format').value='jsonl';const work=dataopsStartExport();await tick();const op=dataopsExport;
dbExplorer.database_id='finance';dbObject=null;
wait.resolve(accepted(op.context,{id:'export-operation',state:'FINISHED',committed:false,result:{export_id:'export-record'},cleanup_error:null}));await work;
assert.equal(op.context.database_id,'db');assert.equal(op.receipt,undefined);assert.equal(op.id,'export-operation');assert.equal(op.state,'FINISHED');
assert.equal($('p35-export-receipt').descendants().some(n=>n.textContent===': 9007199254740993'),false);
assert.equal(calls.filter(c=>c.path.includes('/table-exports/')&&c.path.includes('/content')).length,0);
dbExplorer.database_id='db';dbObject=stable();dbExplorerControls();await dataopsObserveExport();
assert.equal(op.receipt.written_count,123);assert.equal(op.exportId,'export-record');
assert.ok($('p35-export-receipt').descendants().some(n=>n.textContent===': sales'));
''')

    def test_download_failure_clears_receipt_without_blob_fallback(self):
        self.javascript(r'''
await ready();await dbRefreshExplorer();dbObject=stable();dataopsExport={context:structuredClone(dataopsPanels.get('p35-ingest').context),source:ref('orders'),selection:dataopsExportSelection(),principal:dbPrincipal,receipt:{owner:'owner'},exportId:'file',format:'csv',pending:false,invalid:false};
const op=dataopsExport;route=async()=>({http:403,body:{error:{code:'DB_ACCESS_REVOKED'}}});
await dataopsDownload();assert.equal(op.receipt,null);assert.equal(op.invalid,true);assert.ok(!$('p35-export-receipt').descendants().some(n=>n.id==='p35-export-download'));
const request=calls.at(-1);assert.equal(request.options.headers['X-Shirokuma-Role'],op.context.selected_role);
assert.deepEqual(JSON.parse(new URL('http://localhost'+request.path).searchParams.get('context')),op.context);
''')

    def test_bilingual_labels_and_context_response_mismatch(self):
        self.javascript(r'''
const p=await ready();
for(const lang of ['ja','en']){setLanguage(lang);assert.equal($('p35-ingest-confirm').textContent,t('Confirm preview and ingest'));assert.equal($('p35-wap-publish').textContent,t('Publish audited build (admin)'));assert.equal($('p35-export-observe').textContent,t('Observe operation'));}
route=async()=>({context:{...p.context,selected_role:'bi_viewer'},preview:{request_fingerprint:'forged'}});
await preview(p).then(()=>assert.fail('preview must reject mismatched accepted context'),()=>{});
assert.equal(p.preview,null);assert.equal($('p35-ingest-confirm').disabled,true);
''')

    def test_existing_target_modes_type_hints_and_explicit_mode(self):
        self.javascript(r'''
const p=await ready();await dataopsReadFile(p,{name:'input.jsonl',arrayBuffer:async()=>Buffer.from('{"code":"001"}\n').buffer});
assert.throws(()=>dataopsIngestPayload(p),/explicit mode/);
for(const mode of ['replace','append','upsert']){
 $('p35-ingest-mode').value=mode;$('p35-ingest-target').value='1';$('p35-ingest-format').value='jsonl';
 $('p35-ingest-keys').value='code, region';$('p35-ingest-hints').value='code=varchar\namount=decimal(38,9)';
 $('p35-ingest-batch').value='17';$('p35-ingest-atomic').checked=true;
 const body=dataopsIngestPayload(p);assert.deepEqual(body.target,ref('other'));assert.equal(body.destination,undefined);
 assert.deepEqual(body.schema_hints,{code:'varchar',amount:'decimal(38,9)'});assert.deepEqual(body.key_columns,['code','region']);
 assert.equal(body.batch_size,17);assert.equal(body.atomic_append,true);assert.equal(body.format,'jsonl');
}
$('p35-ingest-target').value='';assert.throws(()=>dataopsIngestPayload(p),/explicit mode/);
''')

    def test_delayed_inventory_cannot_retarget_the_panel(self):
        self.javascript(r'''
const p=await ready(),old=deferred();let count=0;
schemasRoute=async()=>++count===1?old.promise:{items:structuredClone(schemas)};
const first=dataopsRefresh(p);await tick();
p.schema='schema';const second=dataopsRefresh(p);await second;
assert.equal(p.context.schema_id,'schema');const chosen=JSON.stringify(p.context);
old.resolve({items:[{...schemas[0],sql_name:'old_schema'}]});await first;
assert.equal(JSON.stringify(p.context),chosen);assert.equal(p.schema,'schema');assert.equal($('p35-ingest-schema').value,'schema');
''')

    def test_late_operation_cannot_restore_data_or_reenable_revoked_controls(self):
        self.javascript(r'''
const p=await ready(),saved=await preview(p),wait=deferred(),original=route;
route=async(path,options)=>path==='/api/v1/ingests'?wait.promise:original(path,options);
const run=$('p35-ingest-confirm').onclick();await tick();inventory[0].grant_revision++;await dbRefreshAccess();
assert.equal(p.invalid,true);wait.resolve(accepted(saved.payload.context,{id:'accepted-before-revocation',state:'FINISHED',committed:true,result:{private:'rows'},cleanup_error:null}));await run;
assert.equal(p.op.id,'accepted-before-revocation');assert.equal(p.op.operation,null);assert.equal(p.op.protected,false);
assert.equal($('p35-ingest-confirm').disabled,true);assert.equal($('p35-ingest-observe').disabled,false);assert.equal($('p35-ingest-cancel').disabled,true);
''')

    def test_model_version_conflict_preserves_edit_for_review(self):
        self.javascript(r'''
const p=await ready('p35-wap');dataopsLoadModel(p,{model_id:'orders',version:2,sql:'SELECT * FROM source',sources:{source:ref('orders')},target:{database_id:'db',schema_id:'schema',physical_name:'orders'},not_null:[],min_rows:0});
$('p35-wap-sql').value='SELECT code FROM source';$('p35-wap-sql').oninput();
route=async()=>({http:409,body:{error:{code:'MODEL_VERSION_CONFLICT'}}});await $('p35-wap-save').onclick();
assert.equal(p.invalid,false);assert.equal(p.model.version,2);assert.equal($('p35-wap-sql').value,'SELECT code FROM source');
assert.match(p.status.textContent,/MODEL_VERSION_CONFLICT/);assert.equal(p.status.getAttribute('role'),'alert');
''')

    def test_content_download_keeps_accepted_role_and_discards_bytes_after_revocation(self):
        self.javascript(r'''
const p=await ready();p.role='data_engineer';await dataopsRefresh(p);
currentRole='data_engineer';await dbRefreshExplorer();dbObject=stable();
const context=structuredClone(p.context),blob=deferred();let downloads=0,reads=0;
Object.prototype.blob=()=>{reads++;return blob.promise;};URL.createObjectURL=()=>{downloads++;return 'blob:download';};
document.body=new Node();dataopsExport={context,source:ref('orders'),selection:dataopsExportSelection(),principal:dbPrincipal,receipt:{owner:'owner'},exportId:'saved-export',format:'jsonl',pending:false,invalid:false};
route=async()=>({});const work=dataopsDownload();await tick();
const content=calls.find(c=>c.path.includes('/saved-export/content'));
assert.equal(content.options.headers['X-Shirokuma-Role'],'data_engineer');assert.deepEqual(JSON.parse(new URL('http://localhost'+content.path).searchParams.get('context')),context);
inventory[0].grant_revision++;await dbRefreshAccess();blob.resolve(new Blob(['private bytes']));await work;
assert.equal(reads,1);assert.equal(downloads,0);assert.equal(dataopsExport.receipt,null);assert.equal(dataopsExport.invalid,true);
''')

    def test_execution_error_retains_authorized_committed_observation(self):
        self.javascript(r'''
const p=await ready(),saved=await preview(p);
route=async()=>({http:502,body:{code:'SERVICE_UNAVAILABLE',details:{context:saved.payload.context,operation:{id:'committed-operation',state:'FINISHED',committed:true,result:{table:ref('orders')},cleanup_error:'cleanup failed'}}}});
await $('p35-ingest-confirm').onclick();assert.equal(p.op.id,'committed-operation');assert.equal(p.op.operation.committed,true);
assert.equal(p.op.operation.cleanup_error,'cleanup failed');assert.equal($('p35-ingest-observe').disabled,false);assert.equal($('p35-ingest-confirm').disabled,true);
assert.equal(p.op.key,saved.key);
''')

    def test_repreview_of_same_payload_keeps_key_and_cannot_resend_unknown(self):
        self.javascript(r'''
const p=await ready(),original=route;let posts=0;
route=async(path,options)=>{
 if(path==='/api/v1/ingests'){posts++;throw Error('response lost');}
 const result=await original(path,options);if(path.endsWith('/preview'))result.preview.request_fingerprint='same-immutable-request';return result;
};
const first=await preview(p);await $('p35-ingest-confirm').onclick();assert.equal(posts,1);
$('p35-ingest-name').oninput();await $('p35-ingest-preview').onclick();
assert.equal(p.preview,null);assert.equal(p.op.key,first.key);assert.equal($('p35-ingest-preview').disabled,true);
await $('p35-ingest-confirm').onclick();assert.equal(posts,1);assert.equal($('p35-ingest-confirm').disabled,true);
''')

    def test_model_ref_matching_ignores_json_property_order(self):
        self.javascript(r'''
const p=await ready('p35-wap');
const reordered={schema_id:'raw',object_id:'other',incarnation:1,database_id:'db'};
dataopsLoadModel(p,{model_id:'loaded',version:7,sql:'SELECT * FROM source',sources:{source:reordered},target:reordered,min_rows:0,not_null:[]});
assert.equal(p.sources[0].table.value,'1');assert.equal($('p35-wap-target').value,'1');
assert.deepEqual(dataopsWapPayload(p).sources.source,ref('other'));assert.deepEqual(dataopsWapPayload(p).target,ref('other'));
''')

    def test_unknown_id_observe_reuses_frozen_submission_and_blocks_new_intent(self):
        self.javascript(r'''
const p=await ready(),saved=await preview(p),original=route;let posts=[];
route=async(path,options)=>{
 if(path==='/api/v1/ingests'){posts.push({body:JSON.parse(options.body),key:options.headers['Idempotency-Key']});if(posts.length===1)throw Error('response lost');return accepted(posts.at(-1).body.context,{id:'recovered',state:'RUNNING',result:{},committed:false});}
 return original(path,options);
};
await $('p35-ingest-confirm').onclick();const op=p.op;assert.equal(op.id,null);assert.equal($('p35-ingest-observe').disabled,false);
$('p35-ingest-name').value='another_destination';$('p35-ingest-name').oninput();await $('p35-ingest-preview').onclick();
assert.equal(p.preview,null);assert.equal(p.op,op);assert.equal(posts.length,1);
await $('p35-ingest-observe').onclick();assert.equal(posts.length,2);assert.deepEqual(posts[0],posts[1]);
assert.deepEqual(posts[1].body,saved.payload);assert.equal(p.op.id,'recovered');assert.equal(p.op.state,'RUNNING');assert.equal($('p35-ingest-preview').disabled,true);
assert.equal($('p35-ingest-cancel').disabled,false);
''')

    def test_accepted_unknown_conflict_preserves_tracking_and_disables_cancel(self):
        self.javascript(r'''
const p=await ready(),saved=await preview(p);
route=async()=>({http:409,body:{error:{code:'UNKNOWN_RECONCILIATION_REQUIRED',details:{context:saved.payload.context,operation:{id:'unknown-op',state:'UNKNOWN',committed:null,result:{},cleanup_error:null}}}}});
await $('p35-ingest-confirm').onclick();const op=p.op;
assert.equal(p.invalid,false);assert.equal(op.id,'unknown-op');assert.equal(op.key,saved.key);assert.equal(op.state,'UNKNOWN');
assert.equal($('p35-ingest-cancel').disabled,true);assert.equal($('p35-ingest-observe').disabled,false);
const n=calls.length;await $('p35-ingest-cancel').onclick();assert.equal(calls.length,n);
await $('p35-ingest-observe').onclick();assert.equal(p.op,op);assert.equal(p.invalid,false);assert.equal(op.operation.state,'UNKNOWN');
assert.ok(p.receipt.descendants().some(n=>n.textContent.includes('manual reconciliation')));
route=async()=>({http:409,body:{error:{code:'OPERATION_CONFLICT'}}});await $('p35-ingest-observe').onclick();
assert.equal(p.invalid,false);assert.equal(op.id,'unknown-op');assert.equal(op.state,'UNKNOWN');assert.equal(op.key,saved.key);
assert.equal(dbError({error:{code:'OPERATION_CONFLICT',details:{example:true}}},409).details.example,true);
''')

    def test_export_unknown_id_recovery_and_explicit_terminal_new_export(self):
        self.javascript(r'''
await ready();await dbRefreshExplorer();dbObject=stable();let posts=[];
route=async(path,options)=>{
 const context=options?.body?JSON.parse(options.body).context:JSON.parse(new URL('http://localhost'+path).searchParams.get('context'));
 if(path==='/api/v1/table-exports'){posts.push({key:options.headers['Idempotency-Key'],body:JSON.parse(options.body)});if(posts.length===1)throw Error('lost');return accepted(context,{id:'export-'+posts.length,state:'FINISHED',result:{export_id:'file'},committed:false});}
 return {context,receipt:{owner:'owner',format:'csv',source:{snapshot:'fixed'},written_count:3,byte_count:10}};
};
await dataopsStartExport();const op=dataopsExport;assert.equal($('p35-export-observe').disabled,false);
dbObject=stable('other');await dataopsStartExport();assert.equal(posts.length,1);assert.equal(dataopsExport,op);
dbObject=stable();await dataopsObserveExport();assert.equal(posts.length,2);assert.deepEqual(posts[0],posts[1]);assert.equal(op.state,'FINISHED');assert.ok(op.receipt);
await dataopsStartExport();assert.equal(posts.length,3);assert.notEqual(posts[2].key,posts[1].key);assert.deepEqual(posts[2].body,posts[1].body);
''')

    def test_download_discards_late_blob_after_object_selection_changes(self):
        self.javascript(r'''
await ready();await dbRefreshExplorer();dbObject=stable();const context=dataopsPanels.get('p35-ingest').context,blob=deferred();let downloads=0;
Object.prototype.blob=()=>blob.promise;URL.createObjectURL=()=>{downloads++;return 'blob:forbidden';};document.body=new Node();
dataopsExport={id:'export-operation',state:'FINISHED',context:structuredClone(context),source:ref('orders'),selection:dataopsExportSelection(),principal:dbPrincipal,receipt:{owner:'owner'},exportId:'file',format:'csv',pending:false,invalid:false};
route=async()=>({});const op=dataopsExport,work=dataopsDownload();await tick();
assert.ok(calls.some(c=>c.path.includes('/file/content')));
dbObject=stable('other');dbExplorerControls();assert.equal(op.receipt,null);assert.equal(op.id,'export-operation');
blob.resolve(new Blob(['old table data']));await work;assert.equal(downloads,0);assert.equal(op.receipt,null);assert.equal(op.state,'FINISHED');
dbObject=stable();dbExplorerControls();assert.equal(op.receipt,null);assert.equal(op.operation,null);
''')

    def test_wap_metadata_summary_defaults_and_terminal_repeat_use_new_key(self):
        self.javascript(r'''
const p=await ready('p35-wap');assert.equal($('p35-wap-min').value,'1');
dataopsLoadModel(p,{model_id:'orders',version:4,sql:'SELECT * FROM source',sources:{source:ref('orders')},target:{database_id:'db',schema_id:'schema',physical_name:'orders'},not_null:[],min_rows:0});
assert.equal($('p35-wap-min').value,0);let keys=[];
route=async(path,options)=>{
 const context=options?.body?JSON.parse(options.body).context:JSON.parse(new URL('http://localhost'+path).searchParams.get('context'));
 if(path==='/api/v1/wap/build'){keys.push(options.headers['Idempotency-Key']);return accepted(context,{id:'build-'+keys.length,state:'FINISHED',committed:true,result:{build_id:'built',build:{model_id:'orders',version:4,stage:{snapshot:'111'},target:{snapshot:'222'}}}});}
 return {context,audit:{id:'audit',model_id:'orders',version:4,stage:{snapshot:'111'},count:3,null_counts:{code:0},passed:true}};
};
await $('p35-wap-build-action').onclick();await $('p35-wap-build-action').onclick();assert.equal(keys.length,2);assert.notEqual(keys[0],keys[1]);
assert.ok(p.receipt.descendants().some(n=>n.textContent===': 111'));assert.ok(p.receipt.descendants().some(n=>n.textContent===': 222'));
$('p35-wap-metadata-kind').value='audit';$('p35-wap-metadata-id').value='audit';await $('p35-wap-metadata-load').onclick();
assert.ok(p.receipt.descendants().some(n=>n.textContent===': Passed'));assert.ok(p.receipt.descendants().some(n=>n.textContent===': code: 0'));
dataopsLoadModel(p,null);assert.equal($('p35-wap-min').value,1);
''')

    def test_busy_finalizer_rechecks_database_capabilities_and_legacy_gate(self):
        self.javascript(database_ui.function('busy') + r'''
const pruneUIBindings=()=>{};Node.prototype.removeAttribute=function(key){delete this[key];};
await ready();const button=$('run-query'),wait=deferred();button.disabled=false;
const run=busy(button,()=>wait.promise);await tick();inventory[0].grant_revision++;await dbRefreshAccess();wait.resolve();await run;
assert.equal(button.disabled,true);
const legacy=$('run-ingest');legacy.dataset.databaseUnavailable='true';legacy.disabled=false;
await busy(legacy,async()=>{});assert.equal(legacy.disabled,true);
''')

    def test_pending_ingest_can_be_observed_and_canceled_without_replacing_intent(self):
        self.javascript(r'''
const p=await ready(),saved=await preview(p),original=deferred();let posts=[];
route=async(path,options)=>{
 const body=options?.body&&JSON.parse(options.body),context=body?.context||saved.payload.context;
 if(path==='/api/v1/ingests'){posts.push({body,key:options.headers['Idempotency-Key']});if(posts.length===1)return original.promise;return accepted(context,{id:'active-ingest',state:'RUNNING',committed:false,result:{},replayed:true});}
 if(path.endsWith('/cancel'))return accepted(context,{id:'active-ingest',state:'CANCELED',committed:false,result:{}});
 throw Error('unexpected '+path);
};
const submit=$('p35-ingest-confirm').onclick();await tick();assert.equal(p.pending,true);assert.equal(p.op.id,null);
assert.equal($('p35-ingest-observe').disabled,false);assert.equal($('p35-ingest-cancel').disabled,true);
await $('p35-ingest-observe').onclick();assert.equal(p.pending,true);assert.equal(p.op.id,'active-ingest');assert.deepEqual(posts[0],posts[1]);assert.equal($('p35-ingest-cancel').disabled,false);
assert.equal($('p35-ingest-confirm').disabled,true);await $('p35-ingest-confirm').onclick();assert.equal(posts.length,2);
await $('p35-ingest-cancel').onclick();assert.equal(p.op.state,'CANCELED');assert.equal(p.pending,true);
const cancel=calls.find(c=>c.path.endsWith('/active-ingest/cancel'));assert.equal(cancel.options.headers['X-Shirokuma-Role'],saved.payload.context.selected_role);assert.deepEqual(JSON.parse(cancel.options.body).context,saved.payload.context);
original.resolve(accepted(saved.payload.context,{id:'active-ingest',state:'FINISHED',committed:true,result:{late:'private'}}));await submit;
assert.equal(p.pending,false);assert.equal(p.op.state,'CANCELED');assert.equal(p.op.operation.state,'CANCELED');assert.equal(p.op.operation.result.late,undefined);
''')

    def test_pending_export_tracking_cancel_survives_late_original_after_navigation(self):
        self.javascript(r'''
await ready();await dbRefreshExplorer();dbObject=stable();const original=deferred();let posts=[];
route=async(path,options)=>{
 const body=options?.body&&JSON.parse(options.body),context=body?.context||dataopsExport.context;
 if(path==='/api/v1/table-exports'){posts.push({body,key:options.headers['Idempotency-Key']});if(posts.length===1)return original.promise;return accepted(context,{id:'active-export',state:'RUNNING',committed:false,result:{},replayed:true});}
 if(path.endsWith('/cancel'))return accepted(context,{id:'active-export',state:'CANCELED',committed:false,result:{}});
 throw Error('unexpected '+path);
};
const submit=dataopsStartExport();await tick();const op=dataopsExport;assert.equal(op.pending,true);assert.equal($('p35-export-observe').disabled,false);
await dataopsObserveExport();assert.deepEqual(posts[0],posts[1]);assert.equal(op.pending,true);assert.equal($('p35-export-cancel').disabled,false);
await dataopsObserveExport(true);assert.equal(op.state,'CANCELED');assert.equal(op.pending,true);
dbObject=stable('other');dbExplorerControls();assert.equal(op.operation,null);
original.resolve(accepted(op.context,{id:'active-export',state:'FINISHED',committed:true,result:{export_id:'late-artifact'}}));await submit;
assert.equal(op.state,'CANCELED');assert.equal(op.operation,null);assert.equal(op.receipt,null);assert.equal(op.exportId,undefined);assert.equal(dataopsExport,op);
assert.equal(calls.some(c=>c.path.includes('/late-artifact')),false);
''')

    def test_definite_pre_admission_rejection_allows_corrected_file_preview(self):
        self.javascript(r'''
const p=await ready(),original=route;let rejection={status:400,code:'INVALID_INPUT'},posts=0;
route=async(path,options)=>path==='/api/v1/ingests'?(posts++,{http:rejection.status,body:{code:rejection.code,message:'Invalid request',details:{}}}):original(path,options);
for(const rejected of [{status:400,code:'INVALID_INPUT'},{status:409,code:'PREVIEW_CHANGED'}]){
 rejection=rejected;await preview(p);await $('p35-ingest-confirm').onclick();const op=p.op;
 assert.equal(op.id,null);assert.equal(op.state,'REJECTED');assert.equal(op.ambiguous,false);assert.equal(dataopsUnresolved(op),false);
 assert.equal($('p35-ingest-observe').disabled,true);assert.equal($('p35-ingest-cancel').disabled,true);
 const count=posts;await $('p35-ingest-observe').onclick();assert.equal(posts,count);
 await dataopsReadFile(p,{name:'smaller.csv',arrayBuffer:async()=>Uint8Array.from([97,10,49,10]).buffer});
 assert.equal($('p35-ingest-preview').disabled,false);await $('p35-ingest-preview').onclick();assert.ok(p.preview);assert.notEqual(p.preview.key,op.key);
 assert.equal($('p35-ingest-confirm').disabled,false);
}
''')

    def test_validation_error_after_acceptance_keeps_durable_operation_unresolved(self):
        self.javascript(r'''
const p=await ready(),saved=await preview(p);
route=async()=>({http:400,body:{code:'INVALID_INPUT',details:{context:saved.payload.context,operation:{id:'accepted-op',state:'UNKNOWN',committed:null,result:{}}}}});
await $('p35-ingest-confirm').onclick();assert.equal(p.op.id,'accepted-op');assert.equal(p.op.state,'UNKNOWN');assert.equal(p.op.ambiguous,true);
assert.equal($('p35-ingest-preview').disabled,true);assert.equal($('p35-ingest-observe').disabled,false);assert.equal($('p35-ingest-cancel').disabled,true);
''')

    def test_failed_replay_cannot_reclassify_original_unknown_as_rejected(self):
        self.javascript(r'''
const p=await ready(),saved=await preview(p);let posts=0;
route=async()=>{if(++posts===1)throw Error('lost response');return {http:400,body:{code:'INVALID_INPUT',details:{}}};};
await $('p35-ingest-confirm').onclick();await $('p35-ingest-observe').onclick();
assert.equal(p.op.state,undefined);assert.equal(p.op.ambiguous,true);assert.equal(p.op.key,saved.key);assert.equal($('p35-ingest-preview').disabled,true);
''')

    def test_mismatched_accepted_error_cannot_prove_rejection_or_expose_results(self):
        self.javascript(r'''
const p=await ready(),saved=await preview(p);
route=async()=>({http:400,body:{code:'INVALID_INPUT',details:{context:{...saved.payload.context,selected_role:'bi_viewer'},operation:{id:'foreign-op',state:'FINISHED',committed:true,result:{private:'protected value'}}}}});
await $('p35-ingest-confirm').onclick();assert.equal(p.op.id,null);assert.equal(p.op.state,undefined);assert.equal(p.op.ambiguous,true);assert.equal(p.op.operation,null);
assert.equal($('p35-ingest-preview').disabled,true);assert.equal(p.receipt.descendants().some(n=>n.textContent.includes('protected value')),false);
''')

    def test_refresh_purges_loaded_model_before_losing_its_protected_handle(self):
        self.javascript(r'''
const p=await ready('p35-wap');
dataopsLoadModel(p,{model_id:'protected_model',version:1,sql:'SELECT private_literal FROM source',sources:{secret_alias:ref('orders')},target:{database_id:'db',schema_id:'raw',physical_name:'private_target'},min_rows:1,not_null:['private_column']});
schemas.forEach(s=>s.capabilities.wap_models=false);p.role='bi_viewer';const wait=deferred();inventoryRoute=()=>wait.promise;
const refresh=dataopsRefresh(p);
assert.equal($('p35-wap-sql').value,'');assert.equal($('p35-wap-model-id').value,'');assert.equal($('p35-wap-not-null').value,'');assert.equal($('p35-wap-name').value,'');assert.equal(p.sources[0].alias.value,'');
wait.resolve({items:structuredClone(inventory)});await refresh;dataopsValidate();assert.equal(p.invalid,true);assert.equal(p.model,null);assert.equal($('p35-wap-sql').value,'');
// A new unsaved draft is user input rather than a cached server model.
inventoryRoute=null;p.role='admin';schemas.forEach(s=>s.capabilities.wap_models=true);await dataopsRefresh(p);dataopsLoadModel(p,null);
$('p35-wap-sql').value='SELECT user_draft FROM source';$('p35-wap-sql').oninput();await dataopsRefresh(p);
assert.equal($('p35-wap-sql').value,'SELECT user_draft FROM source');
''')


if __name__ == '__main__':
    unittest.main()
