"""Execute the shipped Clone handlers with the existing small DOM adapter."""
import unittest
import test_workbench_database_ui as database_ui
from test_workbench_dataops_ui import SETUP as DATAOPS_SETUP


SETUP = DATAOPS_SETUP + r'''
schemas.push({id:'sandbox',database_id:'db',display_name:'Sandbox',sql_name:'sandbox',revision:1,
 allowed_modes:['read-only','sandbox-write'],capabilities:{browse:true,query:true,write:true}});
const cloneSnapshots=['9007199254740993','9007199254740995'];
const cloneOperation=(context,extra={})=>accepted(context,{id:'clone-op',state:'FINISHED',committed:true,
 result:{phase:'VERIFIED',source:{snapshot:cloneSnapshots[0]},target:{snapshot:'9007199254740999'}},...extra});
async function cloneReady(){
 await boot();
 Object.assign(dbExplorer,{database_id:'db',schema_id:'raw',binding_revision:1,generation_id:'gen',grant_revision:4,invalid:false,schemas:structuredClone(schemas),schemaScope:dbSchemaKey(currentRole,'db')});
 dbObject=stable('orders');dbExplorerControls();
 route=async(path,options)=>{
  if(path.includes('detail=snapshots'))return {snapshots:cloneSnapshots.map(snapshot_id=>({snapshot_id}))};
  const body=options?.body?JSON.parse(options.body):null;
  const context=body?.context||JSON.parse(new URL('http://localhost'+path).searchParams.get('context'));
  submissions.push({path,body,headers:options?.headers});return cloneOperation(context);
 };
 await $('clone-table-btn').onclick();$('db-clone-name').value='new_copy';$('db-clone-name').oninput();
}
'''


class CloneUI(unittest.TestCase):
    def javascript(self, body):
        database_ui.DatabaseUI.javascript(self, SETUP + '\n' + body)

    def test_own_sandbox_context_and_exact_snapshot_preserve_other_forms(self):
        self.javascript(r'''
await cloneReady();
const worksheet=JSON.stringify(worksheets),sql=$('sql').value,ingest=$('p35-ingest-name').value,wap=$('p35-wap-sql').value;
assert.equal(dbExplorer.schema_id,'raw');assert.equal(dbClone.context.schema_id,'sandbox');
assert.equal(dbClone.context.sql_schema,'sandbox');assert.equal($('db-clone-submit').disabled,false);
assert.ok($('db-clone-snapshot').options.some(o=>o.value==='9007199254740993'));
$('db-clone-snapshot').value=cloneSnapshots[0];await $('db-clone-submit').onclick();
const sent=submissions[0];assert.equal(sent.path,'/api/v1/table-clones');
assert.equal(sent.body.context.schema_id,'sandbox');assert.equal(sent.body.source.schema_id,'raw');
assert.equal(sent.body.destination.physical_name,'new_copy');assert.equal(sent.body.snapshot_id,'9007199254740993');
assert.equal(JSON.stringify(worksheets),worksheet);assert.equal($('sql').value,sql);
assert.equal($('p35-ingest-name').value,ingest);assert.equal($('p35-wap-sql').value,wap);
assert.equal(dbClone.op.operation.result.phase,'VERIFIED');assert.equal(submissions.length,1);
for(const locale of ['ja','en']){setLanguage(locale);dbCloneControls();assert.ok($('db-clone-receipt').descendants().some(n=>n.textContent.includes(t('VERIFIED'))));}
''')

    def test_name_rule_limits_reserved_names_and_localized_guidance_without_requests(self):
        self.javascript(r'''
await cloneReady();const before=calls.length,name=$('db-clone-name');
assert.equal(name.getAttribute('maxlength'),'31');
assert.equal(name.getAttribute('aria-describedby'),'db-clone-name-rule db-clone-name-invalid');
assert.equal($('db-clone-name-rule').className,'help');
assert.equal($('db-clone-name-invalid').getAttribute('role'),'status');
for(const value of ['a','a'.repeat(31),'a_0']){
 name.value=value;name.oninput();assert.equal($('db-clone-submit').disabled,false);
 assert.equal(name.getAttribute('aria-invalid'),'false');assert.equal($('db-clone-name-invalid').hidden,true);
}
for(const value of ['', 'a'.repeat(32),'_first','Uppercase','invalid-name','system','information_schema','tpch','tpcds']){
 name.value=value;name.oninput();assert.equal($('db-clone-submit').disabled,true,value);
 assert.equal(name.getAttribute('aria-invalid'),String(!!value));
 assert.equal($('db-clone-name-invalid').hidden,!value);await dbCloneRun();
 assert.equal(dbClone.op,null);assert.equal(calls.length,before);
}
for(const locale of ['ja','en']){
 name.value='_invalid';setLanguage(locale);dbCloneControls();
 const rule=$('db-clone-name-rule').textContent,guidance=$('db-clone-name-invalid').textContent;
 assert.equal($('db-clone-name-rule').hidden,false);assert.equal($('db-clone-name-invalid').hidden,false);
 if(locale==='ja'){assert.match(rule,/1〜31文字/);assert.match(rule,/先頭は英小文字/);assert.match(guidance,/テーブル名を入力/);}
 else {assert.match(rule,/1–31 characters/);assert.match(rule,/start with a lowercase letter/);assert.match(guidance,/follows the rule above/);}
 for(const reserved of ['system','information_schema','tpch','tpcds'])assert.ok(rule.includes(reserved));
 assert.equal(calls.length,before);
}
name.value='a'.repeat(31);name.oninput();await dbCloneRun();
assert.equal(submissions.length,1);assert.equal(submissions[0].body.destination.physical_name,'a'.repeat(31));
''')

    def test_current_is_single_localized_default_and_submits_null(self):
        self.javascript(r'''
await cloneReady();const before=calls.length;
for(const locale of ['ja','en']){
 setLanguage(locale);dbCloneControls();const snapshot=$('db-clone-snapshot');
 assert.equal(snapshot.options.length,cloneSnapshots.length+1);
 assert.equal(snapshot.options.filter(o=>o.value==='').length,1);assert.equal(snapshot.value,'');
 const selected=snapshot.options.filter(o=>o.value===snapshot.value);assert.equal(selected.length,1);
 assert.equal(selected[0].textContent,locale==='ja'?'CURRENT（受付時に固定）':'CURRENT (freeze at acceptance)');
 assert.deepEqual(snapshot.options.slice(1).map(o=>o.value),cloneSnapshots);
 assert.equal(calls.length,before);
}
await dbCloneRun();assert.equal(submissions.length,1);assert.equal(submissions[0].body.snapshot_id,null);
''')

    def test_lost_response_freezes_intent_and_reload_recovers_same_key_body(self):
        self.javascript(r'''
await cloneReady();const original=route;let first=true;
route=async(path,options)=>{if(first){first=false;submissions.push({path,body:JSON.parse(options.body),headers:options.headers});throw Error('lost');}return original(path,options);};
await dbCloneRun();const op=dbClone.op,key=op.key,body=JSON.stringify(op.payload);
assert.equal(op.ambiguous,true);assert.equal($('db-clone-submit').disabled,true);
$('db-clone-name').value='edited';$('db-clone-snapshot').value=cloneSnapshots[1];
dbClone.op=null;dbCloneInstall();assert.equal(dbClone.op.key,key);assert.equal(JSON.stringify(dbClone.op.payload),body);
assert.equal(dbClone.op.operation,null);await dbCloneRun(true);
assert.equal(submissions.length,2);assert.equal(JSON.stringify(submissions[1].body),body);
assert.equal(submissions[1].headers['Idempotency-Key'],key);assert.equal(dbClone.op.id,'clone-op');
await dbCloneRun(true);assert.match(submissions[2].path,/\/dataops\/clone-op/);assert.equal(submissions[2].body,null);
''')

    def test_explicit_engineer_under_admin_and_reactive_role_ceiling(self):
        self.javascript(r'''
await cloneReady();assert.equal(currentRole,'admin');
$('db-clone-role').value='data_engineer';$('db-clone-role').onchange();await dbCloneRefresh();
assert.equal(dbClone.context.selected_role,'data_engineer');assert.equal(currentRole,'admin');
assert.equal(dbClone.context.schema_id,'sandbox');await dbCloneRun();
assert.equal(submissions[0].headers['X-Shirokuma-Role'],'data_engineer');
assert.equal(submissions[0].body.context.selected_role,'data_engineer');
$('db-clone-new').onclick();currentRole='data_engineer';dbCloneControls();
assert.equal($('db-clone-role').options.some(o=>o.value==='admin'&&!o.disabled),false);
currentRole='admin';dbCloneControls();assert.equal($('db-clone-role').options.some(o=>o.value==='admin'&&!o.disabled),true);
''')

    def test_late_acceptance_keeps_handle_without_retargeting_or_protected_receipt(self):
        self.javascript(r'''
await cloneReady();const wait=deferred();route=async()=>wait.promise;
const work=dbCloneRun();const op=dbClone.op,context=structuredClone(op.context);
$('db-clone-name').value='other';dbObject=stable('other');++dbSelectionGeneration;
currentRole='bi_viewer';++dbAuthorizationVersion;dbExplorerControls();
wait.resolve(cloneOperation(context));await work;
assert.equal(op.id,'clone-op');assert.equal(op.payload.destination.physical_name,'new_copy');
assert.equal(op.operation,null);assert.equal($('clone-table-btn').disabled,true);
assert.equal($('db-clone-receipt').descendants().some(n=>n.textContent.includes('9007199254740999')),false);
assert.equal($('db-clone-observe').disabled,true);
''')

    def test_view_no_snapshot_no_sandbox_and_stale_source_gates(self):
        self.javascript(r'''
await cloneReady();dbObject={...dbObject,kind:'VIEW'};dbExplorerControls();assert.equal($('clone-table-btn').disabled,true);
dbObject=stable('orders');route=async()=>({snapshots:[]});await dbCloneRefresh();
assert.equal($('db-clone-submit').disabled,true);assert.match($('db-clone-notice').textContent,/No integer snapshot/);
schemas.find(s=>s.id==='sandbox').capabilities.write=false;await dbCloneRefresh();
assert.match($('db-clone-notice').textContent,/writable SANDBOX/);assert.equal(dbClone.context,null);
schemas.find(s=>s.id==='sandbox').capabilities.write=true;route=async()=>({snapshots:cloneSnapshots.map(snapshot_id=>({snapshot_id}))});await dbCloneRefresh();
dbObject=stable('another');dbExplorerControls();assert.equal($('db-clone-submit').disabled,true);
''')

    def test_unknown_cancel_disabled_and_committed_warning_is_truthful(self):
        self.javascript(r'''
await cloneReady();route=async(path,options)=>cloneOperation(JSON.parse(options.body).context,{state:'UNKNOWN',committed:false,result:{phase:'PENDING'}});
await dbCloneRun();assert.equal($('db-clone-cancel').disabled,true);assert.equal($('db-clone-new').disabled,true);
const op=dbClone.op;route=async()=>cloneOperation(op.context,{result:{phase:'COPIED_UNVERIFIED',warning:'TABLE_OBSERVATION_REQUIRED',target:null}});
await dbCloneRun(true);assert.equal(op.operation.committed,true);
assert.equal(op.operation.result.phase,'COPIED_UNVERIFIED');assert.equal($('db-clone-new').disabled,false);
''')

    def test_first_definite_rejection_allows_new_intent_but_lost_response_replay_does_not(self):
        self.javascript(r'''
await cloneReady();const originalRoute=route;
const rejected={http:400,body:{code:'INVALID_INPUT',message:'Invalid input',details:{}}};
route=async()=>rejected;await dbCloneRun();
assert.equal(dbClone.op.sequence,1);assert.equal(dataopsState(dbClone.op),'REJECTED');
assert.equal($('db-clone-new').disabled,false);assert.equal($('db-clone-observe').disabled,true);
$('db-clone-new').onclick();route=originalRoute;await dbCloneRefresh();
route=async()=>{throw Error('response lost');};await dbCloneRun();
const op=dbClone.op,key=op.key,body=JSON.stringify(op.payload);
assert.equal(op.ambiguous,true);route=async()=>rejected;await dbCloneRun(true);
assert.equal(op.sequence,2);assert.equal(op.ambiguous,true);assert.notEqual(dataopsState(op),'REJECTED');
assert.equal(op.key,key);assert.equal(JSON.stringify(op.payload),body);assert.equal($('db-clone-new').disabled,true);
''')

    def test_owner_switch_late_reply_preserves_both_handles_and_newer_busy_state(self):
        self.javascript(r'''
await cloneReady();const owner=dbPrincipal,bob=JSON.stringify(['bob',true]),original=route,a=deferred(),b=deferred();
route=async()=>a.promise;const first=dbCloneRun(),opA=dbClone.op;
const keyA=opA.key,bodyA=JSON.stringify(opA.payload);
session.username='bob';dbPrincipal=bob;++dbAuthorizationVersion;dbExplorer.schemaScope=dbSchemaKey(currentRole,'db');dbExplorerControls();
assert.equal(dbClone.op,null);assert.equal(dbClone.busy,false);
assert.equal($('db-clone-receipt').descendants().some(n=>n.textContent.includes(keyA)),false);
route=original;await dbCloneRefresh();$('db-clone-name').value='b_copy';dbCloneControls();
assert.equal($('db-clone-submit').disabled,false);route=async()=>b.promise;
const second=dbCloneRun(),opB=dbClone.op,busyB=dbClone.busy;
a.resolve(cloneOperation(opA.context,{id:'a-op',state:'ACTIVE',committed:false,result:{phase:'PENDING'}}));await first;
assert.equal(dbClone.op,opB);assert.equal(dbClone.busy,busyB);assert.equal($('db-clone-submit').disabled,true);
assert.equal(opA.id,'a-op');assert.equal(opA.operation,null);assert.equal(opA.key,keyA);assert.equal(JSON.stringify(opA.payload),bodyA);
assert.equal($('db-clone-receipt').descendants().some(n=>n.textContent.includes('a-op')),false);
b.resolve(cloneOperation(opB.context,{id:'b-op'}));await second;
const saved=dbCloneSaved();assert.equal(saved[owner].key,keyA);assert.equal(saved[bob].key,opB.key);
// Simulate a fresh page with only localStorage, then return to the first owner.
dbClone.op=null;dbClone.handles.clear();session.username='owner';dbPrincipal=owner;++dbAuthorizationVersion;
dbExplorer.schemaScope=dbSchemaKey(currentRole,'db');dbCloneInstall();
assert.equal(dbClone.op.id,'a-op');assert.equal(dbClone.op.key,keyA);assert.equal(JSON.stringify(dbClone.op.payload),bodyA);
assert.equal(dbClone.op.operation,null);assert.equal($('db-clone-new').disabled,true);
route=original;await dbCloneRun(true);assert.match(submissions.at(-1).path,/dataops\/a-op/);
assert.equal(dbClone.op.operation.result.phase,'VERIFIED');
session.username='bob';dbPrincipal=bob;++dbAuthorizationVersion;dbExplorer.schemaScope=dbSchemaKey(currentRole,'db');dbCloneControls();
assert.equal(dbClone.op.id,'b-op');assert.equal(dbClone.op.key,opB.key);
''')

    def test_switch_back_before_stale_acceptance_does_not_restore_protected_data(self):
        self.javascript(r'''
await cloneReady();const owner=dbPrincipal,wait=deferred();route=async()=>wait.promise;
const work=dbCloneRun(),op=dbClone.op,key=op.key,body=JSON.stringify(op.payload);
for(const principal of ['bob',owner]){dbPrincipal=principal;++dbAuthorizationVersion;dbExplorer.schemaScope=dbSchemaKey(currentRole,'db');dbCloneControls();}
wait.resolve(cloneOperation(op.context));await work;
assert.equal(dbClone.op,op);assert.equal(op.id,'clone-op');assert.equal(op.operation,null);
assert.equal(op.key,key);assert.equal(JSON.stringify(op.payload),body);
assert.equal($('db-clone-receipt').descendants().some(n=>n.textContent.includes(key)||n.textContent.includes('9007199254740999')),false);
''')

    def test_scope_and_role_auth_loss_redact_draft_options_and_receipt(self):
        for change in ("currentRole='bi_viewer'", "++dbAuthorizationVersion", "dbObject=stable('other')"):
            with self.subTest(change=change):
                self.javascript(r'''
await cloneReady();await dbCloneRun();const op=dbClone.op;
assert.ok($('db-clone-source').textContent);assert.ok($('db-clone-snapshot').options.length>1);
''' + change + r''';dbCloneControls();
assert.equal(dbClone.source,null);assert.equal(dbClone.context,null);assert.equal(dbClone.snapshots.length,0);
assert.equal($('db-clone-source').textContent,'');assert.equal($('db-clone-context').textContent,'');
assert.equal($('db-clone-snapshot').options.some(o=>cloneSnapshots.includes(o.value)),false);
assert.equal(op.operation,null);assert.equal(op.protected,false);
const text=$('db-clone-receipt').descendants().map(n=>n.textContent).join(' ');
for(const value of [op.key,op.id,'sales / sandbox','9007199254740999','new_copy'])assert.equal(text.includes(value),false);
''')

    def assert_cached_auth_failure_redacts_draft(self, observe):
        for code in ('DB_ACCESS_REVOKED', 'SQL_PERMISSION_DENIED'):
            with self.subTest(code=code):
                self.javascript(r'''
await cloneReady();
''' + ("await dbCloneRun();" if observe else "") + r'''
const originalContext=structuredClone(dbClone.context),principal=dbPrincipal;
assert.equal(dataopsExportAuthorized({context:originalContext,principal}),true);
$('db-clone-snapshot').value=cloneSnapshots[0];
assert.ok($('db-clone-source').textContent.includes('orders'));
assert.ok($('db-clone-context').textContent.includes('sandbox'));
const before=calls.length,previous=dbClone.op;
route=async()=>({http:403,body:{code:''' + repr(code) + r''',message:'Access revoked'}});
''' + ("await dbCloneRun(true);" if observe else "await dbCloneRun();") + r'''
const op=dbClone.op,key=op.key,body=JSON.stringify(op.payload),id=op.id,sequence=op.sequence;
assert.equal(calls.length,before+1);
''' + (r"assert.equal(op,previous);assert.equal(id,'clone-op');assert.equal(sequence,2);assert.match(calls.at(-1).path,/\/dataops\/clone-op\?/);assert.equal(calls.at(-1).options.method,undefined);" if observe else r"assert.equal(previous,null);assert.equal(id,null);assert.equal(sequence,1);assert.equal(calls.at(-1).path,'/api/v1/table-clones');assert.equal(calls.at(-1).options.headers['Idempotency-Key'],key);assert.equal(calls.at(-1).options.body,body);") + r'''
assert.equal(op.ambiguous,true);assert.equal(op.operation,null);assert.equal(op.protected,false);
assert.equal(JSON.stringify(op.context),JSON.stringify(originalContext));
// A generic schema denial leaves cached inventory authorized: local purge must suffice.
''' + ("assert.equal(dataopsExportAuthorized(op),true);" if code == 'SQL_PERMISSION_DENIED' else "assert.equal(dataopsExportAuthorized(op),false);") + r'''
for(let i=0;i<2;i++){
 dbCloneControls();
 assert.equal(dbClone.source,null);assert.equal(dbClone.context,null);assert.equal(dbClone.scope,'');assert.deepEqual(dbClone.snapshots,[]);
 assert.equal($('db-clone-source').textContent,'');assert.equal($('db-clone-context').textContent,'');assert.equal($('db-clone-name').value,'');
 assert.equal($('db-clone-snapshot').options.some(o=>cloneSnapshots.includes(o.value)),false);
 assert.equal($('db-clone-snapshot').value,'');assert.equal($('db-clone-submit').disabled,true);
 const text=$('db-clone-receipt').descendants().map(n=>n.textContent).join(' ');
 for(const value of [key,'orders','sales / sandbox','new_copy',...cloneSnapshots])assert.equal(text.includes(value),false);
}
const saved=dbCloneSaved()[principal];assert.equal(saved.key,key);assert.equal(saved.id,id);assert.equal(saved.sequence,sequence);assert.equal(saved.ambiguous,true);assert.equal(JSON.stringify(saved.payload),body);
await dbCloneRun();await dbCloneRun(true,true);
assert.equal(calls.length,before+1);assert.equal(dbClone.op,op);assert.equal(op.key,key);assert.equal(op.sequence,sequence);assert.equal(JSON.stringify(op.payload),body);
''')

    def test_cached_inventory_initial_post403_redacts_draft_preserves_receipt(self):
        self.assert_cached_auth_failure_redacts_draft(False)

    def test_cached_inventory_saved_id_observe403_redacts_draft_preserves_receipt(self):
        self.assert_cached_auth_failure_redacts_draft(True)

    def test_stale403_cannot_invalidate_newer_owner_operation_or_source(self):
        for change in ('owner', 'source', 'switch_back'):
            with self.subTest(change=change):
                self.javascript(r'''
await cloneReady();const original=route,owner=dbPrincipal,wait=deferred();route=async()=>wait.promise;
const work=dbCloneRun(),old=dbClone.op,key=old.key,body=JSON.stringify(old.payload);
''' + (r'''
session.username='bob';dbPrincipal=JSON.stringify(['bob',true]);++dbAuthorizationVersion;
dbExplorer.schemaScope=dbSchemaKey(currentRole,'db');dbCloneControls();
route=original;await dbCloneRefresh();$('db-clone-name').value='bob_copy';dbCloneControls();
await dbCloneRun();assert.ok(dbClone.op.operation);assert.notEqual(dbClone.op,old);
''' if change == 'owner' else r'''
dbObject=stable('other');dbExplorerControls();$('db-clone-name').value='newer_draft';
''' if change == 'source' else r'''
for(const principal of ['bob',owner]){dbPrincipal=principal;++dbAuthorizationVersion;dbExplorer.schemaScope=dbSchemaKey(currentRole,'db');dbCloneControls();}
$('db-clone-name').value='returned_owner_draft';
''') + r'''
const current=dbClone.op,busy=dbClone.busy,roleVersion=dbRoleVersion(old.context.selected_role),authVersion=dbAuthorizationVersion;
const draft=JSON.stringify([dbClone.source,dbClone.context,dbClone.scope,dbClone.snapshots,$('db-clone-source').textContent,$('db-clone-context').textContent,$('db-clone-name').value]);
const operation=current.operation,inventoryBefore=JSON.stringify(dbRoleItems(old.context.selected_role)),before=calls.length;
wait.resolve({http:403,body:{code:'DB_ACCESS_REVOKED',message:'Old access revoked'}});await work;
assert.equal(dbClone.op,current);assert.equal(dbRoleVersion(old.context.selected_role),roleVersion);assert.equal(dbAuthorizationVersion,authVersion);
assert.equal(JSON.stringify(dbRoleItems(old.context.selected_role)),inventoryBefore);
assert.equal(JSON.stringify([dbClone.source,dbClone.context,dbClone.scope,dbClone.snapshots,$('db-clone-source').textContent,$('db-clone-context').textContent,$('db-clone-name').value]),draft);
assert.equal(current.operation,operation);assert.equal(calls.length,before);
assert.equal(old.key,key);assert.equal(JSON.stringify(old.payload),body);assert.equal(dbCloneSaved()[owner].key,key);
''' + ("assert.equal(dbClone.busy,busy);assert.equal(current.protected,true);" if change == 'owner' else "") + r'''
''')

    def test_active_parent_unknown_child_error_observation_survives_reload(self):
        self.javascript(r'''
await cloneReady();const context=structuredClone(dbClone.context);
const observation=cloneOperation(context,{state:'ACTIVE',committed:false,queries:[{id:'q',state:'UNKNOWN'}],result:{phase:'PENDING'}}).operation;
route=async()=>({http:409,body:{code:'OPERATION_UNKNOWN',message:'unknown child',details:{context,operation:observation}}});
await dbCloneRun();const op=dbClone.op,key=op.key,body=JSON.stringify(op.payload);
assert.equal(op.state,'ACTIVE');assert.equal(op.childUnknown,true);assert.equal(op.operation.queries[0].state,'UNKNOWN');
for(const id of ['cancel','new','submit'])assert.equal($('db-clone-'+id).disabled,true);
let posts=0;route=async()=>{posts++;throw Error('must not cancel');};await dbCloneRun(true,true);assert.equal(posts,0);
// Clear protected results, keeping child UNKNOWN in the original saved handle.
++dbAuthorizationVersion;dbCloneControls();assert.equal(op.operation,null);dbClonePersist();
dbClone.op=null;dbClone.handles.clear();dbCloneInstall();assert.equal(dbClone.op.childUnknown,true);
assert.equal(dbClone.op.key,key);assert.equal(JSON.stringify(dbClone.op.payload),body);
assert.equal($('db-clone-cancel').disabled,true);assert.equal($('db-clone-new').disabled,true);
route=async()=>({http:409,body:{code:'OPERATION_UNKNOWN',message:'still unknown',details:{context,operation:{...observation,queries:undefined}}}});
await dbCloneRun(true);assert.equal(dbClone.op.childUnknown,true);assert.equal($('db-clone-cancel').disabled,true);
route=async()=>cloneOperation(context,{queries:[{id:'q',state:'FINISHED'}]});await dbCloneRun(true);
assert.equal(dbClone.op.childUnknown,false);assert.equal($('db-clone-new').disabled,false);
''')

    def test_reload_after_response_loss_cannot_turn_replay_400_into_rejection(self):
        self.javascript(r'''
await cloneReady();route=async()=>{throw Error('lost');};await dbCloneRun();
const key=dbClone.op.key,body=JSON.stringify(dbClone.op.payload);assert.equal(dbClone.op.sequence,1);
dbClone.op=null;dbClone.handles.clear();dbCloneInstall();
assert.equal(dbClone.op.sequence,1);assert.equal(dbClone.op.ambiguous,true);
route=async()=>({http:400,body:{code:'INVALID_INPUT',message:'Invalid input',details:{}}});
await dbCloneRun(true);assert.equal(dbClone.op.sequence,2);assert.equal(dbClone.op.ambiguous,true);
assert.notEqual(dbClone.op.state,'REJECTED');assert.equal(dbClone.op.key,key);assert.equal(JSON.stringify(dbClone.op.payload),body);
assert.equal($('db-clone-new').disabled,true);assert.equal($('db-clone-cancel').disabled,true);
''')

    def test_clone_notices_and_pending_recovery_relocalize_without_new_requests(self):
        self.javascript(r'''
await cloneReady();route=async()=>({snapshots:[]});await dbCloneRefresh();
const key='No integer snapshot is available. This source cannot be cloned.';
assert.equal(dbClone.notice,key);setLanguage('ja');assert.equal($('db-clone-notice').textContent,t(key));
setLanguage('en');assert.equal($('db-clone-notice').textContent,key);
route=async()=>({snapshots:cloneSnapshots.map(snapshot_id=>({snapshot_id}))});await dbCloneRefresh();
route=async()=>{throw Error('response lost');};await dbCloneRun();const before=calls.length;
assert.equal($('db-clone-resend-warning').parentElement,$('db-clone-panel'));
assert.equal($('db-clone-resend-warning').hidden,false);
assert.equal($('db-clone-observe').getAttribute('aria-describedby'),'db-clone-resend-warning');
assert.ok(!$('db-clone-operation-details').open);
for(const locale of ['ja','en']){
 setLanguage(locale);
 const keys=['After reload or a selection change, reselect the original source TABLE in Explorer, then select Observe operation to recover the original request with its saved key and inputs.',
 'Cancellation is unavailable until an acceptance ID is received. Recover the original request first.',
 'Copying can take several minutes. Refresh the original operation manually; do not submit another copy.',
 'Clone details are hidden until the original owner and current access are confirmed. After reload or a selection change, reselect the original source TABLE in Explorer, then select Observe operation to display the saved clone receipt.'];
 const text=$('db-clone-receipt').descendants().map(n=>n.textContent);
 for(const key of keys){assert.ok(i18n.ja[key]);assert.ok(text.includes(t(key)));}
 for(const node of $('db-clone-panel').descendants()){
  const key=uiBindings.get(node)?.key;if(key){assert.ok(i18n.ja[key],key);assert.equal(node.textContent,t(key));}
 }
}
assert.equal(calls.length,before);
''')


if __name__ == '__main__':
    unittest.main()
