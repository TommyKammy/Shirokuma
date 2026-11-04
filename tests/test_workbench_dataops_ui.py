"""Execute the shipped DataOps handlers with the existing Node DOM/fetch adapter."""
import json
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

    def test_reloaded_selected_cleanup_cancel_needs_no_fresh_grant_or_context_reads(self):
        for state in ('ACTIVE', 'CANCELING'):
            with self.subTest(state=state):
                self.javascript('const state='+json.dumps(state)+r'''
let p=await ready();p.role='data_engineer';await dataopsRefresh(p);const record=await preview(p),original=route;
route=async(path,options)=>path==='/api/v1/ingests'?accepted(JSON.parse(options.body).context,{id:'saved-active',state,result:{},committed:false}):original(path,options);
await $('p35-ingest-confirm').onclick();const context=structuredClone(p.op.context),key=p.op.key;
p.root.remove();dataopsPanels.delete(p.id);dataopsRecoveryOwners.clear();p=dataopsInstallIngest();dataopsRender(p);
assert.equal(p.op,null);assert.equal(p.invalid,true);
// Denied fresh grant/source context must not prevent saved-owner cleanup.
inventory=[];sessionRoute=inventoryRoute=schemasRoute=async()=>{throw Error('cleanup must not read fresh context');};
$('p35-ingest-recovery-select').value=key;$('p35-ingest-recovery-select').onchange();
const restored=dataopsRecoverySelected(p);assert.equal(restored.payload,undefined);assert.equal($('p35-ingest-reconnect').disabled,true);assert.equal($('p35-ingest-recovery-cancel').disabled,false);
const count=calls.length;let cancelCount=0;
route=async(path,options)=>{
 cancelCount++;assert.equal(path,'/api/v1/dataops/saved-active/cancel');assert.equal(options.method,'POST');assert.equal(options.headers['X-Shirokuma-Role'],'data_engineer');assert.deepEqual(JSON.parse(options.body),{context});
 return accepted(context,{id:'saved-active',state:'CANCELED'});
};
await $('p35-ingest-recovery-cancel').onclick();assert.equal(cancelCount,1);assert.equal(calls.length,count+1);assert.equal(p.op,restored);assert.equal(restored.state,'CANCELED');assert.equal(restored.operation,null);
''')

    def test_selected_cleanup_cancel_denies_foreign_header_unknown_and_live_retarget(self):
        self.javascript(r'''
const p=await ready(),owner=dbPrincipal;
const saved={panel:p.id,action:'manual-ingest',path:'/api/v1/ingests',key:'saved',context:structuredClone(p.context),time:Date.now(),id:'saved-active',state:'ACTIVE',ambiguous:false,principal:owner,restored:true};
dataopsRecoveryOwner().ops.push(saved);dataopsRecoverySave(saved);
for(const guard of ['foreign','header','UNKNOWN','child-UNKNOWN','terminal','pending','tracking','live-unresolved']){
 dbPrincipal=owner;currentRole='admin';p.pending=false;p.trackingPending=false;p.op=null;saved.state='ACTIVE';saved.ambiguous=false;p.recoveryOwner=owner;p.recoverySelection=saved.key;
 if(guard==='foreign')dbPrincipal='["another",true]';
 if(guard==='header')currentRole='bi_viewer';
 if(guard==='UNKNOWN'){saved.state='UNKNOWN';saved.ambiguous=true;}
 if(guard==='child-UNKNOWN')dataopsAccept(saved,{id:saved.id,state:'ACTIVE',queries:[{state:'UNKNOWN'}]},false);
 if(guard==='terminal')saved.state='FINISHED';
 if(['pending','tracking','live-unresolved'].includes(guard))p.op={principal:owner,key:'live',id:'live-id',context:saved.context,state:'ACTIVE',ambiguous:false};
 if(guard==='pending')p.pending=true;
 if(guard==='tracking')p.trackingPending=true;
 const live=p.op,before=calls.length;dataopsRecoveryRender(p);assert.equal($('p35-ingest-recovery-cancel').disabled,true,guard);
 await $('p35-ingest-recovery-cancel').onclick();assert.equal(calls.length,before,guard);assert.equal(p.op,live,guard);
 if(guard==='foreign'){assert.equal(p.recoveryContext.textContent,'');assert.equal($('p35-ingest-recovery-select').value,'');}
 if(guard==='terminal')assert.equal(saved.state,'FINISHED');
}
dbPrincipal=owner;currentRole='admin';p.pending=false;p.trackingPending=false;saved.state='ACTIVE';saved.ambiguous=false;p.recoveryOwner=owner;p.recoverySelection=saved.key;
const foreign={principal:'["old-owner",true]',panel:p.id,action:'manual-ingest',path:'/api/v1/ingests',key:'foreign-private',id:'foreign-id',context:{...saved.context,sql_catalog:'foreign_catalog'},state:'ACTIVE',ambiguous:false,time:Date.now()};
dataopsRecoveryOwner(foreign.principal).ops.push(foreign);p.op=foreign;
const count=calls.length;route=async(path,options)=>{assert.equal(path,'/api/v1/dataops/saved-active/cancel');assert.deepEqual(JSON.parse(options.body).context,saved.context);return accepted(saved.context,{id:saved.id,state:'CANCELED'});};
dataopsRecoveryRender(p);assert.equal($('p35-ingest-recovery-cancel').disabled,false);await $('p35-ingest-recovery-cancel').onclick();
assert.equal(calls.length,count+1);assert.equal(p.op,saved);assert.equal(saved.state,'CANCELED');assert.equal(dataopsRecoveryOwners.get(foreign.principal).ops[0],foreign);assert.equal(foreign.state,'ACTIVE');
''')

    def test_actual_principal_switch_purges_private_inputs_but_keeps_inflight_owner_handle(self):
        for loaded in (False, True):
            with self.subTest(loaded=loaded):
                self.javascript('const loaded='+json.dumps(loaded)+r'''
const p=await ready(),wap=dataopsPanels.get('p35-wap');await dataopsRefresh(wap);const record=await preview(p),owner=dbPrincipal;
$('p35-ingest-file').value='private-input.csv';
if(loaded)dataopsLoadModel(wap,{model_id:'private_model',version:1,sql:'SELECT private_loaded FROM source',sources:{source:ref('orders')},target:ref('orders'),min_rows:1,not_null:[]});
$('p35-wap-model-id').value='private_model';$('p35-wap-sql').value='SELECT private_manual_draft';$('p35-wap-name').value='private_target';$('p35-wap-metadata-id').value='private_metadata';
wap.sources[0].alias.value='private_alias';wap.sources[0].table.value='0';wap.metadata={model_id:'private_metadata'};
for(const key of ['build','audit','publication']){$('p35-wap-'+key).add(new Option('private-'+key,'private-'+key));$('p35-wap-'+key).value='private-'+key;}
wap.requests=new Map([['private',{payload:{sql:'private-request'}}]]);wap.actions=new Map([['private',{payload:{sql:'private-action'}}]]);
const wait=deferred(),original=route;route=async(path,options)=>path==='/api/v1/ingests'?wait.promise:original(path,options);
const run=$('p35-ingest-confirm').onclick();await tick();const op=p.op;assert.equal(record.operation,op);
for(const panel of [p,wap]){for(const key of ['database','schema'])$(panel.id+'-'+key).add(new Option('private old '+key,'private-old-'+key));}
session={...session,username:'next-owner'};await dbRefreshAccess();
const afterSwitch=calls.length;dataopsValidate();assert.equal(calls.length,afterSwitch);
for(const panel of [p,wap]){
 assert.deepEqual(panel.databases,[]);assert.deepEqual(panel.schemas,[]);assert.equal(panel.database,'');assert.equal(panel.schema,'');assert.equal(panel.context,null);assert.equal(panel.inventoryContext,null);assert.equal(panel.inventoryReady,false);
 for(const key of ['database','schema']){const n=$(panel.id+'-'+key);assert.equal(n.value,'');assert.equal([...n.options].some(o=>o.value==='db'||o.value==='raw'||o.value==='private-old-'+key||o.textContent.includes('private old')),false);}
 assert.equal($(panel.id+'-accepted-context').textContent,'');
}
// The new owner explicitly chooses its own fixture context after the purge.
for(const panel of [p,wap]){panel.database='db';panel.schema='raw';}
await dataopsRefresh(p);await dataopsRefresh(wap);
assert.notEqual(dbPrincipal,owner);assert.equal(p.file,null);assert.equal($('p35-ingest-file').value,'');assert.equal(p.preview,null);assert.equal(p.requests.size,0);
assert.equal(wap.model,null);assert.equal(wap.metadata,null);assert.equal(wap.requests.size,0);assert.equal(wap.actions.size,0);
for(const key of ['model-id','sql','name','metadata-id'])assert.equal($('p35-wap-'+key).value,'',key);
for(const key of ['build','audit','publication'])assert.equal($('p35-wap-'+key).value,'',key);
assert.equal(wap.sources[0].alias.value,'');assert.equal(wap.sources[0].table.value,'');
assert.throws(()=>dataopsIngestPayload(p));assert.throws(()=>dataopsWapPayload(wap));assert.equal(p.op,op);assert.equal(record.operation,op);
assert.equal(dataopsRecoveryOwners.get(owner).ops[0],op);assert.equal(op.key,record.key);assert.equal(op.payload.content_base64,record.payload.content_base64);
const newer={};p.submitToken=newer;p.pending=true;
wait.resolve(accepted(op.context,{id:'late-original',state:'ACTIVE',result:{private:'old-owner result'}}));await run;
assert.equal(p.op,op);assert.equal(record.operation,op);assert.equal(op.id,'late-original');assert.equal(op.operation,null);assert.equal(op.protected,false);assert.equal(p.pending,true);assert.equal(p.submitToken,newer);assert.equal(p.file,null);
''')

    def test_same_owner_refresh_and_navigation_keep_private_manual_drafts(self):
        self.javascript(r'''
const p=await ready(),record=await preview(p),wap=dataopsPanels.get('p35-wap');await dataopsRefresh(wap);
const file=p.file,owner=dbPrincipal;
$('p35-wap-model-id').value='manual_model';$('p35-wap-sql').value='SELECT unsaved_private_sql';$('p35-wap-name').value='manual_target';wap.sources[0].alias.value='manual_alias';
assert.equal(wap.model,null);
await dataopsRefresh(p);await dataopsRefresh(wap);activate($('tab-warehouse'));activate($('tab-ingestion'));activate($('tab-transformation'));
assert.equal(dbPrincipal,owner);assert.equal(p.file,file);assert.ok([...p.requests.values()].includes(record));
assert.equal($('p35-wap-model-id').value,'manual_model');assert.equal($('p35-wap-sql').value,'SELECT unsaved_private_sql');assert.equal($('p35-wap-name').value,'manual_target');assert.equal(wap.sources[0].alias.value,'manual_alias');assert.equal(submissions.length,0);
''')

    def test_recovery_context_help_controls_and_scoped_long_label_constraints(self):
        html = database_ui.HTML
        for rule in (
            '#p35-ingest-recovery,#p35-wap-recovery{min-width:0;max-width:100%;flex-wrap:wrap}',
            '#p35-ingest-recovery>*,#p35-wap-recovery>*{min-width:0;max-width:100%}',
            '#p35-ingest-recovery select,#p35-wap-recovery select{min-width:0;max-width:100%}',
            '#p35-ingest-recovery .help,#p35-wap-recovery .help{flex-basis:100%;overflow-wrap:anywhere;white-space:normal}',
        ):
            self.assertIn(rule, html)
        self.javascript(r'''
const p=await ready();
assert.equal(dataopsRecoveryText('x\x85'),true);for(const key of ['x\x00','x\x1f','x\x7f'])assert.equal(dataopsRecoveryText(key),false);
const op={panel:p.id,action:'manual-ingest',path:'/api/v1/ingests',key:'x\x85',context:{...p.context,sql_catalog:'original_catalog_'+ 'c'.repeat(80),sql_schema:'original_schema'},time:Date.now(),id:'o'.repeat(128),state:'ACTIVE',ambiguous:false,principal:dbPrincipal,restored:true};
dataopsRecoveryOwner().ops.push(op);assert.equal(dataopsRecoveryWrite(dataopsRecoveryOwner()),true);p.recoverySelection=op.key;const before=calls.length;dataopsRecoveryRender(p);
const help=$('p35-ingest-recovery-context'),cancel=$('p35-ingest-recovery-cancel');
assert.equal(help.parentElement,p.recovery);assert.equal(cancel.parentElement,p.recovery);assert.equal($('p35-ingest-recovery-select').parentElement.parentElement,p.recovery);assert.equal(p.receipt.descendants().includes(help),false);assert.equal(p.receipt.descendants().includes(cancel),false);
assert.equal(help.hidden,false);assert.ok(help.textContent.includes(op.context.sql_catalog+' / original_schema / admin / LOCAL'));assert.match(help.textContent,/Match the original database, schema and execution role manually/);assert.equal($('p35-ingest-reconnect').disabled,true);assert.equal(cancel.disabled,false);
assert.ok($('p35-ingest-recovery-select').options.some(o=>o.textContent.length>128));
assert.ok([...$('p35-ingest-recovery-select').options].find(o=>o.value===op.key).textContent.startsWith(t('Ingest')+' · '));
for(const [action,label] of [['manual-ingest','Ingest'],['manual-wap-build','Stage'],['manual-wap-audit','Audit'],['manual-wap-publish','Publish'],['manual-wap-rollback','Rollback']])assert.equal(dataopsRecoveryActionLabel(action),t(label));
setLanguage('ja');assert.equal(t('Saved original request'),'保存済みの処理');assert.equal($('p35-ingest-remove-handle').textContent,'終了済みの追跡情報を削除');assert.ok(help.textContent.includes(op.context.sql_catalog));assert.match(help.textContent,/手動で選び/);assert.equal(cancel.textContent,'選択した保存済み処理をキャンセル');
dbPrincipal='["foreign",true]';dataopsValidate();setLanguage('en');assert.equal(help.textContent,'');assert.equal(help.hidden,true);assert.equal($('p35-ingest-recovery-select').value,'');assert.equal([...$('p35-ingest-recovery-select').options].some(o=>o.value===op.key||o.textContent.includes(op.id)),false);assert.equal(cancel.disabled,true);assert.equal(calls.length,before);
''')

    def test_all_five_preflight_failures_leave_no_durable_intent_or_mutation(self):
        actions = [('manual-ingest', 'confirm'), ('manual-wap-build', 'build-action'),
                   ('manual-wap-audit', 'audit-action'), ('manual-wap-publish', 'publish'),
                   ('manual-wap-rollback', 'rollback')]
        for action, control in actions:
            for failure in ('session-auth', 'network', 'schema-denied', 'schema-missing', 'context-change'):
                with self.subTest(action=action, failure=failure):
                    self.javascript('const action='+json.dumps(action)+';const control='+json.dumps(control)+';const failure='+json.dumps(failure)+r'''
const p=await ready(action==='manual-ingest'?'p35-ingest':'p35-wap');let record;
if(action==='manual-ingest')record=await preview(p);
else{
 dataopsLoadModel(p,{model_id:'saved',version:1,sql:'SELECT * FROM source',sources:{source:ref('orders')},target:{database_id:'db',schema_id:'raw',physical_name:'target'},min_rows:1,not_null:[]});
 for(const name of ['build','audit','publication']){$(p.id+'-'+name).add(new Option(name,name));$(p.id+'-'+name).value=name;}
 $('p35-wap-metadata-id').value='unsaved input';
}
const old={id:'old-receipt',key:'old-key',principal:dbPrincipal,context:p.context,state:'FINISHED',ambiguous:false,protected:false};p.op=old;
const original=route;let mutations=0;
route=async(path,options)=>{if(path===dataopsRecoveryPaths[action])mutations++;return original(path,options);};
if(failure==='session-auth')sessionRoute=async()=>({http:403,get body(){throw SyntaxError('invalid JSON');}});
if(failure==='network')sessionRoute=async()=>{throw Error('offline');};
if(failure==='schema-denied')schemasRoute=async()=>({items:schemas.map(s=>({...s,capabilities:{...s.capabilities,query:false}}))});
if(failure==='schema-missing')schemasRoute=async()=>({items:[]});
if(failure==='context-change')schemasRoute=async()=>{dataopsChanged(p);return {items:structuredClone(schemas)};};
await $(p.id+'-'+control).onclick();
record??=[...p.actions.values()][0];assert.ok(record.key);assert.notEqual(record.submitted,true);assert.equal(record.operation,undefined);
assert.equal(mutations,0);assert.equal(p.op,old);assert.equal(old.state,'FINISHED');
assert.equal(dataopsRecoveryOwner().ops.length,0);assert.equal(storage.has(dataopsRecoveryKey(dbPrincipal)),false);assert.equal(p.pending,false);
if(action==='manual-ingest'){assert.equal($('p35-ingest-name').value,'chosen_table');assert.ok(p.file);}
else assert.equal($('p35-wap-metadata-id').value,'unsaved input');
''')

    def test_malformed_auth_status_at_each_recovery_boundary_redacts_current_loaded_data(self):
        for boundary in ('session', 'inventory', 'schema', 'by-key', 'operation'):
            for status in (401, 403):
                with self.subTest(boundary=boundary, status=status):
                    self.javascript('const boundary='+json.dumps(boundary)+';const status='+str(status)+r'''
const p=await ready('p35-wap');
dataopsLoadModel(p,{model_id:'protected_model',version:1,sql:'SELECT private_loaded_sql FROM source',sources:{source:ref('orders')},target:ref('orders'),min_rows:1,not_null:[]});
p.metadata={model_id:'private_metadata',count:1};
const op={version:1,panel:p.id,action:'manual-wap-build',path:'/api/v1/wap/build',key:'recover',context:structuredClone(p.context),time:Date.now(),id:boundary==='by-key'?null:'known',state:boundary==='by-key'?'UNCONFIRMED':'FINISHED',ambiguous:boundary==='by-key',principal:dbPrincipal,restored:true,protected:false};
const store=dataopsRecoveryOwner();store.ops.push(op);assert.equal(dataopsRecoveryWrite(store),true);p.recoverySelection=op.key;
const denied=()=>({http:status,get body(){throw SyntaxError('invalid JSON');}});
if(boundary==='session')sessionRoute=async()=>denied();
if(boundary==='inventory')inventoryRoute=async()=>denied();
if(boundary==='schema')schemasRoute=async()=>denied();
if(boundary==='by-key'||boundary==='operation')route=async()=>denied();
await dataopsReconnect(p);
assert.equal(p.invalid,true);assert.equal(p.model,null);assert.equal(p.metadata,null);assert.equal($('p35-wap-sql').value,'');
assert.equal(op.state,boundary==='by-key'?'UNCONFIRMED':'FINISHED');assert.equal(op.operation,null);assert.equal(op.protected,false);
assert.equal(dataopsRecoveryOwner().ops[0],op);
''')

    def test_current_schema_denial_redacts_loaded_metadata_but_keeps_plain_draft(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                self.javascript('const missing='+json.dumps(missing)+r'''
const p=await ready('p35-wap');
const op={panel:p.id,action:'manual-wap-build',path:'/api/v1/wap/build',key:'recover',context:structuredClone(p.context),time:Date.now(),id:'known',state:'FINISHED',ambiguous:false,principal:dbPrincipal,restored:true};
dataopsRecoveryOwner().ops.push(op);dataopsRecoverySave(op);p.recoverySelection=op.key;
dataopsLoadModel(p,{model_id:'loaded',version:1,sql:'SELECT protected_sql FROM source',sources:{source:ref('orders')},target:ref('orders'),min_rows:1,not_null:[]});p.metadata={model_id:'protected'};
schemasRoute=async()=>({items:missing?[]:schemas.map(s=>({...s,capabilities:{...s.capabilities,query:false}}))});
await dataopsReconnect(p);assert.equal(p.invalid,true);assert.equal(p.model,null);assert.equal(p.metadata,null);assert.equal($('p35-wap-sql').value,'');assert.equal(op.state,'FINISHED');
schemasRoute=null;await dataopsRefresh(p);p.recoverySelection=op.key;
$('p35-wap-sql').value='SELECT unsaved_user_draft';assert.equal(p.model,null);
schemasRoute=async()=>({items:[]});await dataopsReconnect(p);assert.equal($('p35-wap-sql').value,'SELECT unsaved_user_draft');
''')

    def test_stale_malformed_denial_and_schema_response_never_purge_newer_loaded_form(self):
        for boundary in ('session', 'inventory', 'schema', 'by-key', 'operation', 'schema-empty'):
            with self.subTest(boundary=boundary):
                self.javascript('const boundary='+json.dumps(boundary)+r'''
const p=await ready('p35-wap');
const op={panel:p.id,action:'manual-wap-build',path:'/api/v1/wap/build',key:'recover',context:structuredClone(p.context),time:Date.now(),id:boundary==='by-key'?null:'known',state:boundary==='by-key'?'UNCONFIRMED':'FINISHED',ambiguous:boundary==='by-key',principal:dbPrincipal,restored:true};
dataopsRecoveryOwner().ops.push(op);dataopsRecoverySave(op);p.recoverySelection=op.key;const wait=deferred();
if(boundary==='session')sessionRoute=()=>wait.promise;
if(boundary==='inventory')inventoryRoute=()=>wait.promise;
if(boundary==='schema'||boundary==='schema-empty')schemasRoute=()=>wait.promise;
if(boundary==='by-key'||boundary==='operation')route=()=>wait.promise;
const work=dataopsReconnect(p);await tick();
dataopsLoadModel(p,{model_id:'newer_model',version:2,sql:'SELECT newer_form_sql FROM source',sources:{source:ref('orders')},target:ref('orders'),min_rows:1,not_null:[]});p.metadata={model_id:'newer_metadata'};
const model=p.model,metadata=p.metadata;
wait.resolve(boundary==='schema-empty'?{items:[]}:{http:403,get body(){throw SyntaxError('invalid JSON');}});await work;
assert.equal(p.model,model);assert.equal(p.metadata,metadata);assert.equal(p.invalid,false);assert.equal($('p35-wap-sql').value,'SELECT newer_form_sql FROM source');
''')

    def test_reconnect_drops_protected_wap_reply_after_subview_roundtrip(self):
        for denied in (False, True):
            with self.subTest(denied=denied):
                self.javascript('const denied='+str(denied).lower()+r'''
const p=await ready('p35-wap');activate($('tab-transformation'));showView('transformation-view-wap');await tick();
const op={panel:p.id,action:'manual-wap-build',path:'/api/v1/wap/build',key:'recover',context:structuredClone(p.context),time:Date.now(),id:'known',state:'FINISHED',ambiguous:false,principal:dbPrincipal,restored:true};
dataopsRecoveryOwner().ops.push(op);dataopsRecoverySave(op);p.recoverySelection=op.key;
const wait=deferred();let reads=0;route=()=>{reads++;return wait.promise;};
const work=dataopsReconnect(p);await tick();assert.equal(reads,1);
showView('transformation-view-models');showView('transformation-view-wap');
wait.resolve(denied?{http:403,body:{code:'FORBIDDEN'}}:accepted(op.context,{id:op.id,state:'FINISHED',result:{private:'late'}}));await work;
assert.equal(p.op,op);assert.equal(op.operation,null);assert.equal(op.protected,false);
assert.equal(p.invalid,false);assert.equal(p.trackingPending,false);assert.equal(reads,1);
''')

    def test_reconnect_wap_metadata_options_preserve_all_selections_and_draft_fields(self):
        for blank in (False, True):
            with self.subTest(blank=blank):
                self.javascript('const blank='+json.dumps(blank)+r'''
const p=await ready('p35-wap');
dataopsLoadModel(p,{model_id:'saved_model',version:2,sql:'SELECT saved_sql FROM source',sources:{source:ref('orders')},target:ref('orders'),min_rows:1,not_null:[]});
for(const key of ['build','audit','publication']){const n=$(p.id+'-'+key);n.replaceChildren();if(!blank)n.add(new Option('old-'+key,'old-'+key));n.value=blank?'':'old-'+key;}
const op={panel:p.id,action:'manual-wap-build',path:'/api/v1/wap/build',key:'recover',context:structuredClone(p.context),time:Date.now(),id:'known',state:'FINISHED',ambiguous:false,principal:dbPrincipal,restored:true};
dataopsRecoveryOwner().ops.push(op);dataopsRecoverySave(op);p.recoverySelection=op.key;
const model=p.model,context=p.context,source=p.sources[0].table.value,target=$('p35-wap-target').value,sql=$('p35-wap-sql').value;
route=async()=>accepted(op.context,{id:'known',state:'FINISHED',result:{build_id:'recovered-build',audit_id:'recovered-audit',publication_id:'recovered-publication',build:{model_id:'recovered_model'}},committed:true});
await dataopsReconnect(p);
for(const key of ['build','audit','publication']){const n=$(p.id+'-'+key);assert.ok([...n.options].some(o=>o.value==='recovered-'+key));assert.equal(n.value,blank?'':'old-'+key);}
assert.equal(p.model,model);assert.equal(p.context,context);assert.equal(p.sources[0].table.value,source);assert.equal($('p35-wap-target').value,target);assert.equal($('p35-wap-sql').value,sql);assert.equal(op.protected,true);
''')

    def test_stale_lookup_cannot_regress_newer_terminal_fact_or_retarget_original_handle(self):
        for stale in ('newer-fact', 'sequence', 'aba'):
            with self.subTest(stale=stale):
                self.javascript('const stale='+json.dumps(stale)+r'''
const p=await ready(),op={panel:p.id,action:'manual-ingest',path:'/api/v1/ingests',key:'original',context:structuredClone(p.context),time:Date.now(),id:null,state:'UNCONFIRMED',ambiguous:true,principal:dbPrincipal,restored:true};
dataopsRecoveryOwner().ops.push(op);dataopsRecoverySave(op);p.recoverySelection=op.key;
const wait=deferred();let getCount=0;route=async(path,options)=>{assert.equal(options?.method,undefined);getCount++;assert.ok(path.includes('/by-key?'));return wait.promise;};
const work=dataopsReconnect(p);await tick();assert.equal(p.op,op);
if(stale!=='aba'){dataopsAccept(op,{id:'newer-terminal',state:'FINISHED'},false);if(stale==='sequence')op.sequence++;}
else{dataopsRecoveryBoundary();dataopsRecoveryBoundary();}
const durable=storage.get(dataopsRecoveryKey(dbPrincipal));
wait.resolve({context:op.context,recovery:{operation_id:'old-active',route:'manual-ingest',state:'ACTIVE'}});await work;
assert.equal(p.op,op);assert.equal(getCount,1);assert.equal(op.id,stale==='aba'?null:'newer-terminal');assert.equal(op.state,stale==='aba'?'UNCONFIRMED':'FINISHED');assert.equal(storage.get(dataopsRecoveryKey(dbPrincipal)),durable);
''')

    def test_foreign_owner_selection_redacted_new_owner_can_submit_and_returning_owner_stays_held(self):
        self.javascript(r'''
const p=await ready(),record=await preview(p),original=route,owner=dbPrincipal;
route=async(path,options)=>path==='/api/v1/ingests'?Promise.reject(Error('lost')):original(path,options);
await $('p35-ingest-confirm').onclick();const old=p.op;p.recoverySelection=old.key;dataopsRecoveryRender(p);
session={...session,username:'next-owner'};await dbRefreshAccess();p.database='db';p.schema='raw';await dataopsRefresh(p);dataopsRender(p);
const select=$('p35-ingest-recovery-select');assert.equal(select.value,'');assert.equal([...select.options].some(o=>o.value===old.key||o.textContent.includes(old.key)),false);assert.equal(p.receipt.children.length,0);
assert.equal($('p35-ingest-observe').hidden,true);assert.equal($('p35-ingest-cancel').hidden,true);assert.equal(dataopsRecoveryHeld(p),false);
route=original;await preview(p);await $('p35-ingest-confirm').onclick();assert.notEqual(p.op,old);assert.equal(p.op.principal,dbPrincipal);assert.equal(p.op.state,'FINISHED');
assert.equal(dataopsRecoveryOwners.get(owner).ops[0],old);assert.equal(old.id,null);assert.equal(old.state,'UNCONFIRMED');
session={...session,username:'owner'};await dbRefreshAccess();p.database='db';p.schema='raw';await dataopsRefresh(p);assert.equal(dbPrincipal,owner);assert.equal(dataopsRecoveryHeld(p),true);assert.equal($('p35-ingest-confirm').disabled,true);
''')

    def test_wap_action_records_are_owned_so_foreign_unresolved_key_does_not_block_new_owner(self):
        self.javascript(r'''
const p=await ready('p35-wap'),original=route,owner=dbPrincipal;
const model={model_id:'saved',version:1,sql:'SELECT * FROM source',sources:{source:ref('orders')},target:{database_id:'db',schema_id:'raw',physical_name:'target'},min_rows:1,not_null:[]};
dataopsLoadModel(p,structuredClone(model));
route=async(path,options)=>path==='/api/v1/wap/build'?Promise.reject(Error('lost')):original(path,options);
await $('p35-wap-build-action').onclick();const old=p.op,oldRecord=[...p.actions.values()][0];assert.equal(oldRecord.operation,old);
session={...session,username:'next-owner'};await dbRefreshAccess();p.database='db';p.schema='raw';await dataopsRefresh(p);dataopsLoadModel(p,structuredClone(model));
route=original;await $('p35-wap-build-action').onclick();assert.notEqual(p.op,old);assert.equal(p.op.principal,dbPrincipal);assert.equal(p.op.state,'FINISHED');assert.notEqual(p.op.key,old.key);assert.equal(oldRecord.operation,old);assert.equal(dataopsRecoveryOwners.get(owner).ops[0],old);
''')

    def test_stale_malformed_live_observe_denial_does_not_purge_after_language_aba(self):
        self.javascript(r'''
const p=await ready('p35-wap');
dataopsLoadModel(p,{model_id:'saved',version:1,sql:'SELECT protected_model_sql FROM source',sources:{source:ref('orders')},target:ref('orders'),min_rows:1,not_null:[]});
const op={panel:p.id,action:'manual-wap-build',path:'/api/v1/wap/build',key:'original',context:structuredClone(p.context),time:Date.now(),id:'known',state:'FINISHED',ambiguous:false,principal:dbPrincipal,protected:false};
dataopsRecoveryOwner().ops.push(op);dataopsRecoverySave(op);p.op=op;const wait=deferred();route=()=>wait.promise;
const work=dataopsObserve(p,false);await tick();setLanguage('ja');setLanguage('en');const model=p.model;
wait.resolve({http:403,get body(){throw SyntaxError('invalid JSON');}});await work;
assert.equal(p.model,model);assert.equal(p.invalid,false);assert.equal($('p35-wap-sql').value,'SELECT protected_model_sql FROM source');assert.equal(op.state,'FINISHED');
''')

    def test_stale_reconnect_finalizer_does_not_clear_new_tracking_busy(self):
        self.javascript(r'''
const p=await ready(),op={panel:p.id,action:'manual-ingest',path:'/api/v1/ingests',key:'original',context:structuredClone(p.context),time:Date.now(),id:null,state:'UNCONFIRMED',ambiguous:true,principal:dbPrincipal,restored:true};
dataopsRecoveryOwner().ops.push(op);dataopsRecoverySave(op);p.recoverySelection=op.key;const wait=deferred();route=()=>wait.promise;
const work=dataopsReconnect(p);await tick();const newer={};p.trackingToken=newer;p.trackingPending=true;op.sequence++;
wait.resolve({context:op.context,recovery:{operation_id:'old',route:'manual-ingest',state:'ACTIVE'}});await work;
assert.equal(p.trackingToken,newer);assert.equal(p.trackingPending,true);assert.equal(p.op,op);assert.equal(op.id,null);assert.equal(op.state,'UNCONFIRMED');
''')

    def test_late_foreign_mutation_keeps_private_identity_and_cannot_clear_new_busy_token(self):
        self.javascript(r'''
const p=await ready(),record=await preview(p),wait=deferred(),original=route;
route=async(path,options)=>path==='/api/v1/ingests'?wait.promise:original(path,options);
const run=$('p35-ingest-confirm').onclick();await tick();const op=p.op,owner=dbPrincipal;
assert.equal(record.operation,op);session={...session,username:'other'};await dbRefreshAccess();await dataopsRefresh(p);
const newer={};p.submitToken=newer;p.pending=true;
wait.resolve(accepted(op.context,{id:'late-original',state:'ACTIVE',result:{private:'old-owner result'}}));await run;
assert.equal(p.submitToken,newer);assert.equal(p.pending,true);assert.equal(record.operation,op);assert.equal(dataopsRecoveryOwners.get(owner).ops[0],op);assert.equal(op.id,'late-original');assert.equal(op.operation,null);assert.equal(op.protected,false);
''')

    def test_valid_disk_drift_and_duplicate_keys_fail_closed_without_overwriting(self):
        for drift in ('valid-other-tab', 'duplicate-disk', 'duplicate-memory'):
            with self.subTest(drift=drift):
                self.javascript('const drift='+json.dumps(drift)+r'''
const p=await ready(),record=await preview(p),store=dataopsRecoveryOwner(),key=dataopsRecoveryKey(dbPrincipal);
const external={version:1,panel:'p35-wap',action:'manual-wap-build',key:'other-tab-unresolved',context:structuredClone(p.context),time:Date.now(),id:null,state:'UNCONFIRMED',uncertain:true};
if(drift==='valid-other-tab')storage.set(key,JSON.stringify([external]));
if(drift==='duplicate-disk')storage.set(key,JSON.stringify([external,external]));
if(drift==='duplicate-memory'){const op={...external,principal:dbPrincipal,ambiguous:true};store.ops=[op,{...op}];}
const before=storage.get(key);await $('p35-ingest-confirm').onclick();
assert.equal(submissions.length,0);assert.notEqual(record.submitted,true);assert.equal(storage.get(key),before);assert.equal(store.error,true);assert.match(p.recoveryNotice.textContent,/storage unavailable/);
if(drift!=='duplicate-memory'){dataopsRecoveryOwners.clear();const reloaded=dataopsRecoveryOwner();assert.equal(reloaded.error,drift==='duplicate-disk');if(drift==='valid-other-tab')assert.equal(reloaded.ops[0].key,external.key);}
''')

    def test_owner_cap_split_between_panels_blocks_fresh_before_io_but_allows_terminal_remove(self):
        self.javascript(r'''
const p=await ready(),record=await preview(p),wap=dataopsPanels.get('p35-wap'),store=dataopsRecoveryOwner();await dataopsRefresh(wap);
for(let i=0;i<25;i++)store.ops.push({panel:i<21?p.id:wap.id,action:i<21?'manual-ingest':'manual-wap-build',path:i<21?'/api/v1/ingests':'/api/v1/wap/build',key:'saved-'+i,context:structuredClone(p.context),time:Date.now(),id:'saved-'+i,state:'FINISHED',ambiguous:false,principal:dbPrincipal});
assert.equal(dataopsRecoveryWrite(store),true);dataopsRender(p);dataopsRender(wap);
assert.match(p.recoveryNotice.textContent,/storage unavailable or full/);assert.match(wap.recoveryNotice.textContent,/storage unavailable or full/);assert.equal($('p35-ingest-confirm').disabled,true);
const count=calls.length;await $('p35-ingest-confirm').onclick();assert.equal(calls.length,count);assert.equal(submissions.length,0);
p.recoverySelection='saved-0';dataopsRecoveryRender(p);assert.equal($('p35-ingest-remove-handle').disabled,false);await $('p35-ingest-remove-handle').onclick();assert.equal(store.ops.length,24);assert.equal(calls.length,count);assert.equal(dataopsRecoveryHeld(p),false);assert.equal(p.recoveryNotice.textContent,'');assert.equal(wap.recoveryNotice.textContent,'');
''')

    def test_all_five_real_actions_save_before_io_then_reload_and_reconnect_original_id(self):
        for action, control in [('manual-ingest', 'confirm'), ('manual-wap-build', 'build-action'),
                                ('manual-wap-audit', 'audit-action'), ('manual-wap-publish', 'publish'),
                                ('manual-wap-rollback', 'rollback')]:
            for observation in (False, True):
                with self.subTest(action=action, observation=observation):
                    self.javascript('const action='+json.dumps(action)+';const observation='+json.dumps(observation)+';const control='+json.dumps(control)+r'''
let p=await ready(action==='manual-ingest'?'p35-ingest':'p35-wap');
if(action==='manual-ingest')await preview(p);
else{
 dataopsLoadModel(p,{model_id:'private_model',version:2,sql:'SELECT private_literal FROM source',sources:{source:ref('orders')},target:{database_id:'db',schema_id:'raw',physical_name:'private_target'},min_rows:1,not_null:[]});
 for(const name of ['build','audit','publication']){$(p.id+'-'+name).add(new Option('private-'+name,'private-'+name));$(p.id+'-'+name).value='private-'+name;}
}
const original=route;let postCount=0;
route=async(path,options)=>{
 if(path===dataopsRecoveryPaths[action]&&options?.method==='POST'){
  postCount++;const durable=JSON.parse(storage.get(dataopsRecoveryKey(dbPrincipal)));
  assert.equal(durable.length,1);assert.equal(durable[0].id,null);assert.equal(durable[0].state,'UNCONFIRMED');
  assert.equal(durable[0].key,options.headers['Idempotency-Key']);assert.equal(durable[0].action,action);
  const context=JSON.parse(options.body).context,operation={id:'original-id',state:'FINISHED',committed:true,result:{private:'private-result',backend_query_id:'backend-secret'},cleanup_error:'private-error'};
  return observation?{http:502,body:{code:'SERVICE_UNAVAILABLE',details:{context,operation}}}:accepted(context,operation);
 }
 return original(path,options);
};
await $(p.id+'-'+control).onclick();assert.equal(postCount,1);assert.equal(p.op.id,'original-id');
const durable=storage.get(dataopsRecoveryKey(dbPrincipal)),h=JSON.parse(durable)[0],originalContext=structuredClone(p.op.context);
assert.deepEqual(Object.keys(h).sort(),['version','panel','action','key','context','time','id','state','uncertain'].sort());
for(const secret of ['content_base64','payload','filename','private_model','private_literal','private_target','private-result','backend-secret','private-error','result','columns','queries'])assert.equal(durable.includes(secret),false,secret);
const before=calls.length;
// Reload the normal panel and storage bootstrap, without retaining its live op/body.
p.root.remove();dataopsPanels.delete(p.id);dataopsRecoveryOwners.clear();
p=action==='manual-ingest'?dataopsInstallIngest():dataopsInstallWap();dataopsRender(p);
assert.equal(p.op,null);assert.equal(p.pending,false);assert.equal(calls.length,before);
p.started=true;await dataopsRefresh(p);
const restored=dataopsRecoveryOwner().ops[0];assert.equal(restored.payload,undefined);assert.equal(restored.restored,true);
const draft=action==='manual-ingest'?$('p35-ingest-name'):$('p35-wap-sql');draft.value='new unsent draft';
const worksheetsBefore=JSON.stringify(worksheets),contextBefore=JSON.stringify(p.context),sqlBefore=$('sql').value;
$(p.id+'-recovery-select').value=h.key;$(p.id+'-recovery-select').onchange();
const reconnectCalls=[];route=async(path,options)=>{reconnectCalls.push({path,options});assert.equal(options?.method,undefined);return accepted(originalContext,{id:'original-id',state:'FINISHED',committed:true,result:{},cleanup_error:null});};
await $(p.id+'-reconnect').onclick();
assert.equal(reconnectCalls.length,1);assert.ok(reconnectCalls[0].path.startsWith('/api/v1/dataops/original-id?'));
assert.deepEqual(JSON.parse(new URL('http://local'+reconnectCalls[0].path).searchParams.get('context')),originalContext);
assert.equal(reconnectCalls[0].options.headers['X-Shirokuma-Role'],originalContext.selected_role);
assert.equal(p.op,restored);assert.equal(p.op.protected,true);assert.equal(draft.value,'new unsent draft');
assert.equal(JSON.stringify(worksheets),worksheetsBefore);assert.equal(JSON.stringify(p.context),contextBefore);assert.equal($('sql').value,sqlBefore);assert.equal(postCount,1);
''')

    def test_pending_reload_lookup_then_id_get_never_posts_or_replays_payload(self):
        self.javascript(r'''
const p=await ready(),record=await preview(p),original=route;
route=async(path,options)=>path==='/api/v1/ingests'?Promise.reject(Error('lost private error')):original(path,options);
await $('p35-ingest-confirm').onclick();assert.equal(p.op.id,null);
dataopsRecoveryOwners.clear();p.op=null;const restored=dataopsRecoveryOwner().ops[0];
assert.equal(restored.payload,undefined);p.recoverySelection=restored.key;let paths=[];
route=async(path,options)=>{
 assert.equal(options?.method,undefined);paths.push(path);
 if(path.includes('/by-key?')){const q=new URL('http://local'+path).searchParams;assert.equal(q.get('key'),record.key);assert.equal(q.get('route'),'manual-ingest');return {context:restored.context,recovery:{operation_id:'found',route:'manual-ingest',state:'ACTIVE'}};}
 return accepted(restored.context,{id:'found',state:'ACTIVE',result:{},queries:[],committed:false});
};
await dataopsReconnect(p);assert.equal(paths.length,2);assert.ok(paths[0].includes('/by-key?'));assert.ok(paths[1].startsWith('/api/v1/dataops/found?'));
assert.equal(p.op,restored);assert.equal(p.op.id,'found');assert.equal($('p35-ingest-confirm').disabled,true);
const count=paths.length;setLanguage('ja');setLanguage('en');dataopsValidate();dataopsOpen('ingestion');assert.equal(paths.length,count);
''')

    def test_missing_delayed_lookup_stays_held_and_known_terminal_failure_stays_terminal(self):
        self.javascript(r'''
const p=await ready(),record=await preview(p),original=route;
route=async(path,options)=>path==='/api/v1/ingests'?Promise.reject(Error('lost')):original(path,options);
await $('p35-ingest-confirm').onclick();dataopsRecoveryOwners.clear();p.op=null;
const op=dataopsRecoveryOwner().ops[0];p.recoverySelection=op.key;let posts=0;
route=async(path,options)=>{if(options?.method)posts++;return {http:404,body:{code:'OPERATION_NOT_FOUND'}};};
await dataopsReconnect(p);assert.equal(op.id,null);assert.equal(op.state,'UNCONFIRMED');assert.equal(dataopsUnresolved(op),true);
assert.equal($('p35-ingest-confirm').disabled,true);assert.equal($('p35-ingest-remove-handle').disabled,true);
for(const state of ['FINISHED','FAILED','CANCELED','REJECTED'])for(const status of [403,404,409]){
 op.state=state;op.ambiguous=false;op.id=state==='REJECTED'?null:'known';dataopsRecoverySave(op);
 route=async(path,options)=>{assert.equal(options?.method,undefined);return {http:status,body:{code:status===403?'DB_ACCESS_REVOKED':'OPERATION_NOT_FOUND',details:{context:op.context,operation:{id:op.id||'unexpected',state:'UNKNOWN'}}}};};
 await dataopsReconnect(p);assert.equal(op.state,state);assert.equal(op.ambiguous,false);assert.equal(op.protected,false);assert.equal(op.operation,null);
}
assert.equal(posts,0);
''')

    def test_malformed_lookup_dtos_never_trigger_receipt_get(self):
        self.javascript(r'''
const p=await ready(),record=await preview(p),original=route;
route=async(path,options)=>path==='/api/v1/ingests'?Promise.reject(Error('lost')):original(path,options);
await $('p35-ingest-confirm').onclick();dataopsRecoveryOwners.clear();p.op=null;const op=dataopsRecoveryOwner().ops[0];p.recoverySelection=op.key;
const good={context:op.context,recovery:{operation_id:'found',route:'manual-ingest',state:'ACTIVE'}};
for(const bad of [{},{context:op.context,recovery:{}},{...good,extra:true},{context:op.context,recovery:{...good.recovery,extra:true}},
 {context:op.context,recovery:{...good.recovery,operation_id:''}},{context:op.context,recovery:{...good.recovery,route:'manual-wap-build'}},
 {context:op.context,recovery:{...good.recovery,state:'RUNNING'}},{...good,context:{...op.context,grant_revision:true}}]){
 let requests=0;route=async(path,options)=>{requests++;assert.equal(options?.method,undefined);assert.ok(path.includes('/by-key?'));return bad;};
 await dataopsReconnect(p);assert.equal(requests,1);assert.equal(op.id,null);assert.equal(dataopsUnresolved(op),true);
}
''')

    def test_storage_failures_block_before_post_preserving_input_and_key(self):
        for failure in ('unavailable', 'corrupt', 'unknown-version', 'quota', 'readback'):
            with self.subTest(failure=failure):
                self.javascript('const failure='+json.dumps(failure)+r'''
const p=await ready(),record=await preview(p),key=record.key,payload=record.payload;
const ownerKey=dataopsRecoveryKey(dbPrincipal),get=localStorage.getItem,set=localStorage.setItem;
if(failure==='unavailable')localStorage.getItem=()=>{throw Error('absent');};
if(failure==='corrupt')storage.set(ownerKey,'{bad');
if(failure==='unknown-version')storage.set(ownerKey,JSON.stringify([{version:99}]));
if(failure==='quota')localStorage.setItem=()=>{throw Error('quota');};
if(failure==='readback')localStorage.setItem=(key,value)=>{};
await $('p35-ingest-confirm').onclick();
assert.equal(submissions.length,0);assert.equal(record.submitted,false);assert.equal(record.key,key);assert.equal(record.payload,payload);
assert.equal($('p35-ingest-name').value,'chosen_table');assert.match(p.recoveryNotice.textContent,/storage unavailable or full/);
localStorage.getItem=get;localStorage.setItem=set;
''')

    def test_owner_cap_never_evicts_and_explicit_terminal_remove_has_no_backend_action(self):
        self.javascript(r'''
const p=await ready(),record=await preview(p),store=dataopsRecoveryOwner();
for(let i=0;i<25;i++)store.ops.push({version:1,panel:p.id,action:'manual-ingest',key:'saved-'+i,context:structuredClone(p.context),time:Date.now(),id:'old-'+i,state:i===24?'FINISHED':'UNKNOWN',ambiguous:i!==24,principal:dbPrincipal,path:'/api/v1/ingests'});
assert.equal(dataopsRecoveryWrite(store),true);const before=storage.get(dataopsRecoveryKey(dbPrincipal));
p.recoverySelection='saved-24';dataopsRender(p);await $('p35-ingest-confirm').onclick();assert.equal(submissions.length,0);assert.equal(store.ops.length,25);assert.equal(storage.get(dataopsRecoveryKey(dbPrincipal)),before);
const callsBefore=calls.length;await $('p35-ingest-remove-handle').onclick();assert.equal(calls.length,callsBefore);assert.equal(store.ops.length,24);
assert.equal(store.ops.filter(op=>op.state==='UNKNOWN').length,24);assert.equal(dataopsRecoveryHeld(p),true);
const heldCalls=calls.length;await $('p35-ingest-confirm').onclick();assert.equal(calls.length,heldCalls);assert.equal(submissions.length,0);assert.equal(p.recoveryNotice.textContent,'');assert.equal(p.recoveryHeldHelp.hidden,false);assert.match(p.recoveryHeldHelp.textContent,/Select the saved operation and reconnect or cancel/);assert.equal(p.recoverySelection,'');
p.recoverySelection='saved-0';await $('p35-ingest-remove-handle').onclick();assert.equal(store.ops.length,24);
''')

    def test_late_persistence_failure_keeps_durable_sentinel_and_memory_effect(self):
        self.javascript(r'''
const p=await ready(),record=await preview(p),wait=deferred(),original=route;
route=async(path,options)=>path==='/api/v1/ingests'?wait.promise:original(path,options);
const run=$('p35-ingest-confirm').onclick();await tick();const op=p.op,durable=storage.get(dataopsRecoveryKey(dbPrincipal));
localStorage.setItem=()=>{throw Error('quota after effect');};
wait.resolve(accepted(op.context,{id:'effect-id',state:'FINISHED',committed:true,result:{},cleanup_error:null}));await run;
assert.equal(p.op,op);assert.equal(record.operation,op);assert.equal(op.id,'effect-id');assert.equal(op.state,'FINISHED');assert.equal(record.submitted,true);
assert.equal(storage.get(dataopsRecoveryKey(dbPrincipal)),durable);assert.equal(JSON.parse(durable)[0].id,null);assert.equal(dataopsRecoveryHeld(p),true);assert.match(p.recoveryNotice.textContent,/storage unavailable/);
''')

    def test_inflight_selection_owner_aba_and_late_body_cannot_retarget_original_record(self):
        self.javascript(r'''
const p=await ready(),record=await preview(p),wait=deferred(),original=route;
route=async(path,options)=>path==='/api/v1/ingests'?wait.promise:original(path,options);
const run=$('p35-ingest-confirm').onclick();await tick();const op=p.op,owner=dbPrincipal;
const terminal={...op,key:'terminal',id:'other',state:'FINISHED',ambiguous:false,payload:undefined};dataopsRecoveryOwner().ops.push(terminal);dataopsRecoverySave(terminal);
$('p35-ingest-recovery-select').value=terminal.key;$('p35-ingest-recovery-select').onchange();assert.equal(p.op,op);assert.equal(record.operation,op);
setLanguage('ja');setLanguage('en');dataopsChanged(p);
dbPrincipal='["other",true]';dataopsValidate();assert.equal(p.receipt.children.length,0);assert.equal($('p35-ingest-observe').hidden,true);assert.equal($('p35-ingest-cancel').hidden,true);
assert.equal(dataopsRecoveryOwner().ops.length,0);dbPrincipal=owner;dataopsRecoveryBoundary();
wait.resolve(accepted(op.context,{id:'late-original',state:'ACTIVE',committed:false,result:{private:'must not resurrect'},cleanup_error:null}));await run;
assert.equal(p.op,op);assert.equal(record.operation,op);assert.equal(op.id,'late-original');assert.equal(op.operation,null);assert.equal(op.protected,false);
assert.equal($('p35-ingest-confirm').disabled,true);assert.equal(dataopsRecoveryOwner().ops.find(h=>h.key===op.key),op);
''')

    def test_reconnect_checks_current_authority_before_and_after_and_saved_cancel_survives_grant_loss(self):
        self.javascript(r'''
const p=await ready(),record=await preview(p);await $('p35-ingest-confirm').onclick();const op=p.op;
op.state='ACTIVE';op.ambiguous=false;op.operation=null;dataopsRecoverySave(op);p.recoverySelection=op.key;
const original=route,wait=deferred();let gets=0,posts=0;
route=async(path,options)=>{if(path.startsWith('/api/v1/dataops/')){if(options?.method){posts++;return accepted(op.context,{id:op.id,state:'CANCELED'});}gets++;return wait.promise;}return original(path,options);};
const work=dataopsReconnect(p);await tick();inventory[0].grant_revision++;await dbRefreshAccess();wait.resolve(accepted(op.context,{id:op.id,state:'ACTIVE',result:{private:'hidden'}}));await work;
assert.equal(gets,1);assert.equal(op.operation,null);assert.equal(op.protected,false);assert.equal(op.state,'ACTIVE');
await dataopsReconnect(p);assert.equal(gets,1);assert.equal(posts,0);
await $('p35-ingest-cancel').onclick();assert.equal(posts,1);assert.equal(op.state,'CANCELED');assert.equal(op.operation,null);
''')

    def test_pending_404_can_only_reconnect_same_key_after_delayed_admission(self):
        self.javascript(r'''
const p=await ready(),record=await preview(p),original=route;
route=async(path,options)=>path==='/api/v1/ingests'?Promise.reject(Error('lost')):original(path,options);
await $('p35-ingest-confirm').onclick();dataopsRecoveryOwners.clear();p.op=null;const op=dataopsRecoveryOwner().ops[0];p.recoverySelection=op.key;
let admitted=false,lookups=0,gets=0;
route=async(path,options)=>{
 assert.equal(options?.method,undefined);
 if(path.includes('/by-key?')){lookups++;assert.equal(new URL('http://local'+path).searchParams.get('key'),record.key);return admitted?{context:op.context,recovery:{operation_id:'delayed',route:'manual-ingest',state:'ACTIVE'}}:{http:404,body:{code:'OPERATION_NOT_FOUND'}};}
 gets++;return accepted(op.context,{id:'delayed',state:'ACTIVE',result:{}});
};
await dataopsReconnect(p);assert.equal(dataopsRecoveryHeld(p),true);assert.equal(gets,0);assert.equal(op.id,null);
admitted=true;await dataopsReconnect(p);assert.equal(lookups,2);assert.equal(gets,1);assert.equal(op.id,'delayed');assert.equal(op.key,record.key);assert.equal(dataopsRecoveryHeld(p),true);
''')

    def test_normal_navigation_language_role_and_context_aba_hide_late_protected_reply(self):
        for change in ('language', 'navigation', 'role', 'context', 'body'):
            with self.subTest(change=change):
                self.javascript('const change='+json.dumps(change)+r'''
const p=await ready();await preview(p);await $('p35-ingest-confirm').onclick();const op=p.op,wait=deferred(),original=route;
p.recoverySelection=op.key;let getCount=0,posts=0;
route=async(path,options)=>{if(path.startsWith('/api/v1/dataops/')){assert.equal(options?.method,undefined);getCount++;return wait.promise;}if(options?.method)posts++;return original(path,options);};
const reconnect=dataopsReconnect(p);await tick();assert.equal(getCount,1);
if(change==='language'){setLanguage('ja');setLanguage('en');}
if(change==='navigation'){activate($('tab-warehouse'));activate($('tab-ingestion'));}
if(change==='role'){
 $('role-selector').value='data_engineer';await $('role-selector').onchange();
 $('role-selector').value='admin';await $('role-selector').onchange();
}
if(change==='context'){p.schema='schema';dataopsChanged(p);p.schema='raw';dataopsChanged(p);}
if(change==='body'){$('p35-ingest-name').value='new draft';$('p35-ingest-name').oninput();}
wait.resolve(accepted(op.context,{id:op.id,state:'FINISHED',result:{private:'late protected result'}}));await reconnect;
assert.equal(p.op,op);assert.equal(op.state,'FINISHED');assert.equal(op.operation,null);assert.equal(op.protected,false);assert.equal(posts,0);assert.equal(getCount,1);
if(change==='body')assert.equal($('p35-ingest-name').value,'new draft');
''')

    def test_reconnect_postreply_current_session_loss_hides_known_terminal_receipt(self):
        self.javascript(r'''
const p=await ready();await preview(p);await $('p35-ingest-confirm').onclick();const op=p.op,owner=dbPrincipal;p.recoverySelection=op.key;
route=async(path,options)=>{assert.equal(options?.method,undefined);session={...session,username:'another-owner'};return accepted(op.context,{id:op.id,state:'FINISHED',result:{private:'protected'}});};
await dataopsReconnect(p);assert.notEqual(dbPrincipal,owner);assert.equal(op.state,'FINISHED');assert.equal(op.operation,null);assert.equal(op.protected,false);assert.equal(p.receipt.children.length,0);
assert.equal(dataopsRecoveryOwners.get(owner).ops[0],op);assert.equal(dataopsRecoveryOwner().ops.length,0);
''')

    def test_unknown_parent_and_child_remain_held_and_cannot_cancel(self):
        self.javascript(r'''
const p=await ready();await preview(p);await $('p35-ingest-confirm').onclick();const op=p.op;
for(const operation of [{id:op.id,state:'UNKNOWN'},{id:op.id,state:'ACTIVE',queries:[{id:'child',state:'UNKNOWN'}]}]){
 dataopsAccept(op,operation,true);dataopsRender(p);assert.equal(dataopsUnresolved(op),true);assert.equal($('p35-ingest-cancel').disabled,true);
 const before=calls.length;await $('p35-ingest-cancel').onclick();assert.equal(calls.length,before);
 dataopsRecoveryOwners.clear();p.op=null;const restored=dataopsRecoveryOwner().ops[0];assert.equal(dataopsUnresolved(restored),true);assert.equal(dataopsCanCancel(restored),false);dataopsRecoveryOwners.get(dbPrincipal).ops=[op];p.op=op;
}
''')

    def test_panel_availability_missing_own_context_and_localized_recovery(self):
        self.javascript(r'''
await boot();
for(const p of dataopsPanels.values()){
 const help=$(p.id+'-availability');
 assert.equal(help.getAttribute('role'),'status');assert.equal(p.context,null);
 assert.match(help.textContent,/Panel context is unavailable.*Refresh this panel context or reselect/);
 assert.equal($('p35-wap-model-id').disabled,true);
 const before=calls.length;
 p.database='';p.schema='';dataopsValidate();
 assert.equal(help.textContent,'Choose an available database and schema.');
 setLanguage('ja');assert.equal(help.textContent,'利用可能なデータベースとスキーマを選択してください。');
 setLanguage('en');assert.equal(calls.length,before);
}
''')

    def test_header_downgrade_renders_unstarted_panel_and_explicit_viewer_reason(self):
        self.javascript(r'''
await boot();const p=dataopsPanels.get('p35-wap'),help=$('p35-wap-availability');
assert.equal(p.context,null);assert.equal(p.role,'admin');
$('role-selector').value='data_engineer';const change=$('role-selector').onchange();
assert.equal(p.role,'admin');assert.equal($('p35-wap-model-id').disabled,true);
assert.match(help.textContent,/Saved execution role exceeds the current role ceiling.*Select an execution role/);
await change;
$('p35-wap-role').value='bi_viewer';await $('p35-wap-role').onchange();
assert.equal(p.role,'bi_viewer');assert.match(help.textContent,/require data_engineer or admin/);
setLanguage('ja');assert.match(help.textContent,/データ作成者または管理者ロールが必要/);setLanguage('en');
$('p35-wap-role').value='data_engineer';await $('p35-wap-role').onchange();
assert.equal(help.hidden,true);assert.equal(help.textContent,'');assert.equal($('p35-wap-model-id').disabled,false);
''')

    def test_delayed_panel_refresh_keeps_ceiling_help_and_operation_status(self):
        self.javascript(r'''
const p=await ready('p35-wap'),help=$('p35-wap-availability');
assert.equal(p.schema,'raw');assert.equal(help.hidden,true);
// Worksheet context remains independent of this panel's raw schema.
dbCurrent().schema_id='schema';await dbRefreshContext();assert.equal(p.schema,'raw');
const wait=deferred();inventoryRoute=()=>wait.promise;
const refresh=$('p35-wap-refresh').onclick();await tick();
assert.match(help.textContent,/Panel context is unavailable/);
$('role-selector').value='data_engineer';const change=$('role-selector').onchange();
assert.equal(p.role,'admin');assert.match(help.textContent,/exceeds the current role ceiling/);
wait.resolve({items:structuredClone(inventory)});await Promise.all([refresh,change]);
assert.equal($('p35-wap-model-id').disabled,true);assert.match(help.textContent,/exceeds the current role ceiling/);
p.op={key:'saved-key',id:'saved-operation',context:structuredClone(p.context),state:'UNKNOWN',ambiguous:true,protected:false};
ui(p.status,'Outcome unknown. Observe with the saved key; do not submit again.');
const status=p.status.textContent;dataopsRender(p);dataopsValidate();
assert.equal(p.status.textContent,status);
assert.ok(p.receipt.descendants().some(n=>n.textContent===': UNKNOWN'));
assert.ok(p.receipt.descendants().some(n=>n.textContent.includes('Results hidden until access is revalidated')));
ui(p.status,'operation error retained');dataopsRender(p);assert.equal(p.status.textContent,'operation error retained');
assert.equal(submissions.length,0);
''')

    def test_unchanged_controls_and_authorization_preserve_open_operation_details(self):
        self.javascript(r'''
const p=await ready();p.role='data_engineer';await dataopsRefresh(p);
const original=route;
route=async(path,options)=>{
 const reply=await original(path,options);
 if(path==='/api/v1/ingests')reply.operation.result={build:{model_id:'daily_orders',version:1,count:1}};
 return reply;
};
await preview(p);await $('p35-ingest-confirm').onclick();
assert.equal(p.invalid,false);assert.equal(p.op.protected,true);assert.equal(p.op.state,'FINISHED');
const details=p.receipt.querySelector('details'),summary=details.querySelector('summary');
// The minimal DOM adapter permits the native details.open property directly.
details.open=true;summary.focus();
const operation=p.op,status=p.status.textContent,before=calls.length,posts=submissions.length;
let receiptWrites=0,availabilityWrites=0;
const replace=p.receipt.replaceChildren;
p.receipt.replaceChildren=function(...items){receiptWrites++;return replace.apply(this,items);};
const help=p.availability;let text=help.textContent;
Object.defineProperty(help,'textContent',{get:()=>text,set:value=>{availabilityWrites++;text=value;}});
const stableReceipt=()=>{
 assert.equal(p.receipt.querySelector('details'),details);assert.equal(details.querySelector('summary'),summary);
 assert.equal(details.open,true);assert.equal(document.activeElement,summary);
 assert.equal(p.op,operation);assert.equal(p.status.textContent,t(status));assert.equal(receiptWrites,0);
};
dbControls();stableReceipt();assert.equal(calls.length,before);
await dataopsRefreshAuthorization();stableReceipt();
assert.deepEqual(calls.slice(before).map(c=>c.path),['/api/session','/api/v1/databases']);
assert.equal(availabilityWrites,0);assert.equal(submissions.length,posts);
setLanguage('ja');stableReceipt();assert.ok(availabilityWrites>0);
setLanguage('en');stableReceipt();assert.equal(submissions.length,posts);
''')

    def test_availability_reason_changes_leave_retained_receipt_untouched(self):
        self.javascript(r'''
await boot();const p=dataopsPanels.get('p35-wap'),help=p.availability;
p.op={key:'retained',id:'completed',context:{sql_catalog:'sales',sql_schema:'raw',selected_role:'admin'},state:'FINISHED',protected:false};
dataopsRender(p);const receipt=[...p.receipt.children],status=p.status.textContent,before=calls.length;
const unchanged=()=>{assert.deepEqual(p.receipt.children,receipt);assert.equal(p.status.textContent,status);};
currentRole='data_engineer';dbControls();unchanged();assert.match(help.textContent,/exceeds the current role ceiling/);
setLanguage('ja');unchanged();assert.equal(help.textContent,t('{reason} {action}',{reason:t('Saved execution role exceeds the current role ceiling.'),action:t('Select an execution role within the current ceiling for this panel.')}));
setLanguage('en');p.role='bi_viewer';dbControls();unchanged();assert.match(help.textContent,/require data_engineer or admin/);
p.role='data_engineer';p.database='';p.schema='';dbControls();unchanged();assert.equal(help.textContent,'Choose an available database and schema.');
p.database='db';p.schema='raw';dbControls();unchanged();assert.match(help.textContent,/Panel context is unavailable/);
assert.equal(calls.length,before);assert.equal(submissions.length,0);
''')

    def test_dedicated_wap_panel_keeps_legacy_gates(self):
        self.javascript(r'''
$('transformation').append(Object.assign($('run-dbt-build'),{tagName:'BUTTON'}),Object.assign($('run-dbt-test'),{tagName:'BUTTON'}),Object.assign($('transformation-view-marts'),{tagName:'BUTTON'}));
const p=await ready('p35-wap');
assert.equal(p.root.parentElement,$('transformation-wap'));
assert.equal($('transformation-wap').hidden,true);
assert.equal($('transformation-models').hidden,false);
assert.equal($('transformation-view-wap').hidden,false);
assert.match($('database-ingestion-notice').textContent,/file ingestion and registered Stages/);
assert.equal($('p35-wap-model-id').disabled,false);assert.equal($('transformation-view-marts').disabled,false);
assert.equal($('db-marts-panel').hidden,false);
assert.ok($('jobs').children.filter(n=>!n.hidden).every(n=>n.id==='db-jobs-panel'));
const before=calls.length;
for(const lang of ['ja','en']){
 setLanguage(lang);dataopsInstall();dbControls();
 assert.equal($('run-dbt-build').disabled,true);assert.equal($('run-dbt-test').disabled,true);
 assert.match($('p35-wap-entry-help').textContent,lang==='ja'?/構築と監査にはデータ作成者または管理者、公開とロールバックには管理者/:/Build and audit require Data Engineer or Admin. Publish and rollback require Admin/);
}
assert.equal(calls.length,before);
// A stale panel remains recoverable without claiming the Worksheet makes it ready.
dataopsInvalidate(p);dataopsRender(p);
assert.equal($('p35-wap-model-id').disabled,true);assert.equal($('p35-wap-refresh').disabled,false);
assert.match($('p35-wap-entry-help').textContent,/Choose a database and schema/);
assert.equal(submissions.length,0);
''')

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
assert.equal(p.op.state,'UNCONFIRMED');assert.equal(p.op.operation,null);assert.equal(p.op.protected,false);
''')

    def test_followups_keep_original_role_and_context_after_grant_loss(self):
        self.javascript(r'''
const p=await ready();p.role='data_engineer';await dataopsRefresh(p);await preview(p);await $('p35-ingest-confirm').onclick();
const frozen=structuredClone(p.op.context);assert.equal(frozen.selected_role,'data_engineer');
p.op.state='ACTIVE';p.op.operation.state='ACTIVE';
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
 if(path==='/api/v1/ingests'){posts.push({body:JSON.parse(options.body),key:options.headers['Idempotency-Key']});if(posts.length===1)throw Error('response lost');return accepted(posts.at(-1).body.context,{id:'recovered',state:'ACTIVE',result:{},committed:false});}
 return original(path,options);
};
await $('p35-ingest-confirm').onclick();const op=p.op;assert.equal(op.id,null);assert.equal($('p35-ingest-observe').disabled,false);
$('p35-ingest-name').value='another_destination';$('p35-ingest-name').oninput();await $('p35-ingest-preview').onclick();
assert.equal(p.preview,null);assert.equal(p.op,op);assert.equal(posts.length,1);
await $('p35-ingest-observe').onclick();assert.equal(posts.length,2);assert.deepEqual(posts[0],posts[1]);
assert.deepEqual(posts[1].body,saved.payload);assert.equal(p.op.id,'recovered');assert.equal(p.op.state,'ACTIVE');assert.equal($('p35-ingest-preview').disabled,true);
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
 if(path==='/api/v1/ingests'){posts.push({body,key:options.headers['Idempotency-Key']});if(posts.length===1)return original.promise;return accepted(context,{id:'active-ingest',state:'ACTIVE',committed:false,result:{},replayed:true});}
 if(path.endsWith('/cancel'))return accepted(context,{id:'active-ingest',state:'CANCELED',committed:false,result:{}});
 throw Error('unexpected '+path);
};
const submit=$('p35-ingest-confirm').onclick();await tick();assert.equal(p.pending,true);assert.equal(p.op.id,null);const op=p.op;assert.equal(saved.operation,op);
assert.equal($('p35-ingest-observe').disabled,false);assert.equal($('p35-ingest-cancel').disabled,true);
await $('p35-ingest-observe').onclick();assert.equal(p.op,op);assert.equal(saved.operation,op);assert.equal(p.pending,true);assert.equal(p.op.id,'active-ingest');assert.deepEqual(posts[0],posts[1]);assert.equal($('p35-ingest-cancel').disabled,false);
assert.equal($('p35-ingest-confirm').disabled,true);await $('p35-ingest-confirm').onclick();assert.equal(posts.length,2);
await $('p35-ingest-cancel').onclick();assert.equal(p.op.state,'CANCELED');assert.equal(p.pending,true);
const cancel=calls.find(c=>c.path.endsWith('/active-ingest/cancel'));assert.equal(cancel.options.headers['X-Shirokuma-Role'],saved.payload.context.selected_role);assert.deepEqual(JSON.parse(cancel.options.body).context,saved.payload.context);
original.resolve(accepted(saved.payload.context,{id:'active-ingest',state:'FINISHED',committed:true,result:{late:'private'}}));await submit;
assert.equal(p.pending,false);assert.equal(p.op,op);assert.equal(saved.operation,op);assert.equal(p.op.state,'CANCELED');assert.equal(p.op.id,'active-ingest');assert.equal(p.op.operation,null);assert.equal(p.receipt.descendants().some(n=>n.textContent.includes('private')),false);
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
assert.equal(posts,2);await $('p35-ingest-confirm').onclick();assert.equal(posts,2);assert.equal(p.op.operation,null);assert.equal(p.op.state,'UNCONFIRMED');assert.equal(p.op.ambiguous,true);assert.equal(p.op.key,saved.key);assert.equal($('p35-ingest-preview').disabled,true);
''')

    def test_mismatched_accepted_error_cannot_prove_rejection_or_expose_results(self):
        self.javascript(r'''
const p=await ready(),saved=await preview(p);let posts=0;
route=async()=>{posts++;return {http:400,body:{code:'INVALID_INPUT',details:{context:{...saved.payload.context,selected_role:'bi_viewer'},operation:{id:'foreign-op',state:'FINISHED',committed:true,result:{private:'protected value'}}}}};};
await $('p35-ingest-confirm').onclick();await $('p35-ingest-confirm').onclick();assert.equal(posts,1);assert.equal(p.op.id,null);assert.equal(p.op.state,'UNCONFIRMED');assert.equal(p.op.ambiguous,true);assert.equal(p.op.operation,null);
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
