"""Retirement management flows in the synthetic production JavaScript harness."""
import unittest
import test_workbench_admin_ui as admin

SETUP = admin.SETUP + r'''
const retireOp=(state='RETIRING')=>({id:'retire-op',action:'DATABASE_RETIRE',actor:'owner',database_id:'db',revision:2,state,
 database:{id:'db',sql_catalog:'sales',state:state.toLowerCase(),revision:4},
 dependencies:[{kind:'run',id:'run',state:'UNKNOWN',database_id:'db',parent_id:'job'}],
 blockers:[{kind:'run',id:'run',state:'UNKNOWN',database_id:'db',code:'RUN_UNKNOWN'}],stopped_schedule_ids:['stopped-1']});
const retirePreview=()=>({database:{id:'db',sql_catalog:'sales',state:'READY',revision:3},dependencies:retireOp().dependencies,
 blockers:retireOp().blockers,stopped_schedule_ids:[],operation:null});
'''


class RetirementUI(unittest.TestCase):
    javascript = admin.harness.DatabaseUI.javascript

    def test_preview_start_inspect_reconcile_unknown_and_actual_stopped_ids(self):
        self.javascript(SETUP + r'''
const drafts=JSON.stringify(worksheets),explorer=JSON.stringify(dbExplorer);$('sql').value='SELECT preserved';
adminRoute=async(path,options)=>path.endsWith('/retire')&&options.method==='GET'?retirePreview():retireOp();
await $('db-admin-retire-preview').onclick();
assert.match($('db-admin-retire-summary').textContent,/db · sales · READY · revision 3/);
assert.equal(JSON.stringify($('db-admin-retire-blockers').rows).includes('RUN_UNKNOWN'),true);
assert.equal(JSON.stringify($('db-admin-retire-dependencies').rows).includes('UNKNOWN'),true);
assert.equal($('db-admin-retire-stopped').textContent,'[]');assert.equal(writeCalls().length,0);
await $('db-admin-retire-save').onclick();let c=writeCalls().at(-1);
assert.equal(c.path,'/api/v1/admin/databases/db/retire');assert.deepEqual(JSON.parse(c.options.body),{expected_revision:3});
assert.ok(c.options.headers['Idempotency-Key']);assert.equal(c.options.headers['X-Shirokuma-Role'],'admin');
assert.equal($('db-admin-retire-operation').value,'retire-op');assert.equal($('db-admin-retire-op-revision').value,'2');
assert.match($('db-admin-retire-summary').textContent,/retiring.*RETIRING/);assert.equal($('db-admin-retire-stopped').textContent,'["stopped-1"]');
await $('db-admin-retire-inspect').onclick();
await $('db-admin-retire-reconcile-save').onclick();c=writeCalls().at(-1);
assert.equal(c.path,'/api/v1/operations/retire-op/reconcile');assert.deepEqual(JSON.parse(c.options.body),{expected_revision:2});
assert.equal(dbAdmin.databases[0].state,'REGISTERED');assert.equal(dbAdmin.databases[1].id,'finance');
assert.equal(calls.some(c=>/\/runs\/|\/results|\/artifacts/.test(c.path)),false);
assert.equal(JSON.stringify(worksheets),drafts);assert.equal(JSON.stringify(dbExplorer),explorer);assert.equal($('sql').value,'SELECT preserved');
''')

    def test_reopened_retiring_preview_displays_actual_stopped_ids_read_only(self):
        self.javascript(SETUP + r'''
const op={...retireOp(),stopped_schedule_ids:['schedule-a','schedule-b']};
const preview={...retirePreview(),database:op.database,stopped_schedule_ids:[...op.stopped_schedule_ids],operation:op};
adminRoute=async()=>preview;
for(let i=0;i<2;i++){
 await $('db-admin-retire-preview').onclick();
 assert.deepEqual(dbAdmin.retirement,op);
 assert.match($('db-admin-retire-summary').textContent,/retiring.*RETIRING/);
 assert.equal($('db-admin-retire-stopped').textContent,JSON.stringify(op.stopped_schedule_ids));
}
assert.equal(writeCalls().length,0);
''')

    def test_preview_stopped_ids_require_exact_valid_operation_evidence(self):
        self.javascript(SETUP + r'''
const op=retireOp(),preview={...retirePreview(),database:op.database,stopped_schedule_ids:[...op.stopped_schedule_ids],operation:op};
for(const stopped of [[],['other'],['stopped-1','stopped-1'],[''],[null],[true],[1],'stopped-1',null,{}]){
 assert.equal(dbAdminRetirePreview({...preview,stopped_schedule_ids:stopped}),false);
 // Valid lists remain valid when both envelopes agree.
 if(Array.isArray(stopped)&&(stopped.length===0||stopped[0]==='other'))continue;
 assert.equal(dbAdminRetirePreview({...preview,stopped_schedule_ids:stopped,operation:{...op,stopped_schedule_ids:stopped}}),false);
}
assert.equal(dbAdminRetirePreview({...preview,operation:null}),false);
assert.equal(dbAdminRetirePreview({...preview,stopped_schedule_ids:['b','a'],operation:{...op,stopped_schedule_ids:['a','b']}}),false);
assert.equal(dbAdminRetirePreview(retirePreview()),true);
adminRoute=async()=>({...preview,operation:null});
await $('db-admin-retire-preview').onclick();
assert.equal(dbAdmin.retirement,null);assert.equal($('db-admin-retire-stopped').textContent,'');
assert.equal(writeCalls().length,0);
''')

    def test_uncertain_start_and_reconcile_freeze_same_payload_and_key(self):
        self.javascript(SETUP + r'''
adminRoute=async()=>({malformed:true});await $('db-admin-retire-save').onclick();
const start=writeCalls().at(-1);$('db-admin-retire-revision').value='99';
assert.equal(dbAdmin.requests.get('db-admin-retire').uncertain,true);assert.equal($('db-admin-select').disabled,true);
await $('db-admin-retire-save').onclick();assert.equal(writeCalls().length,1);
adminRoute=async()=>retireOp();await $('db-admin-retire-retry').onclick();let retry=writeCalls().at(-1);
assert.equal(retry.options.body,start.options.body);assert.equal(retry.options.headers['Idempotency-Key'],start.options.headers['Idempotency-Key']);
adminRoute=async()=>{throw Error('synthetic network loss');};await $('db-admin-retire-reconcile-save').onclick();
const reconcile=writeCalls().at(-1);$('db-admin-retire-op-revision').value='87';$('db-admin-retire-operation').value='edited';
adminRoute=async()=>retireOp();await $('db-admin-retire-reconcile-retry').onclick();retry=writeCalls().at(-1);
assert.equal(retry.path,reconcile.path);assert.equal(retry.options.body,reconcile.options.body);assert.equal(retry.options.headers['Idempotency-Key'],reconcile.options.headers['Idempotency-Key']);
''')

    def test_cas_error_visible_and_revision_draft_preserved(self):
        self.javascript(SETUP + r'''
$('db-admin-retire-revision').value='3';const field=$('db-admin-database-db-name');field.value='keep';
adminRoute=async()=>({http:409,body:{code:'REVISION_CONFLICT',message:'Database revision changed',details:{expected_revision:3}}});
await $('db-admin-retire-save').onclick();
assert.match($('db-admin-retire-result').textContent,/REVISION_CONFLICT.*Database revision changed.*expected_revision/);
adminItems[0].revision=9;await dbAdminRefresh();assert.equal($('db-admin-retire-revision').value,'3');assert.equal(field.value,'keep');
assert.equal(dbAdmin.requests.get('db-admin-retire').uncertain,false);
''')

    def test_all_state_management_and_read_selection_fences(self):
        self.javascript(SETUP + r'''
adminItems[0].state='retiring';adminItems[1].state='retired';await dbAdminRefresh();
assert.equal($('db-admin-select').options.length,3);await choose('finance');assert.match($('db-admin-summary').textContent,/retired/);await choose();
let done;adminRoute=async()=>new Promise(resolve=>done=resolve);
const pending=$('db-admin-retire-preview').onclick();await tick();await choose('finance');done(retirePreview());await pending;
assert.equal($('db-admin-retire-summary').textContent,'');assert.equal(dbAdmin.retirement,null);
assert.equal(calls.some(c=>/get_run|\/runs\/|\/results|\/artifacts/.test(c.path)),false);
''')

    def test_current_auth_loss_purges_and_delayed_mutation_is_discarded(self):
        self.javascript(SETUP + r'''
let done;adminRoute=async()=>new Promise(resolve=>done=resolve);
const pending=$('db-admin-retire-save').onclick();await tick();
session.platform_admin=false;await dbAdminRetireRead(true);done(retireOp());await pending;
assert.equal(dbAdmin.requests.size,0);assert.equal(dbAdmin.retirement,null);assert.equal($('db-admin-content').textContent,'');
assert.equal(writeCalls().length,1);assert.equal($('tab-db-admin').hidden,true);
''')

    def test_role_change_and_incomplete_receipt_never_claim_retired(self):
        self.javascript(SETUP + r'''
adminRoute=async()=>({...retireOp('RETIRED'),database:{...retireOp().database,state:'READY'}});
await $('db-admin-retire-save').onclick();assert.equal(dbAdmin.requests.get('db-admin-retire').uncertain,true);
assert.equal(dbAdmin.retirement,null);assert.equal($('db-admin-retire-result').textContent.includes('READY'),false);
currentRole='data_engineer';dbApplyRole();assert.equal(dbAdmin.requests.size,0);assert.equal(dbAdmin.retirement,null);
''')

    def test_preview_started_before_mutation_cannot_restore_ready_observation(self):
        self.javascript(SETUP + r'''
let done;adminRoute=async(path,options)=>options.method==='GET'?new Promise(resolve=>done=resolve):retireOp();
const read=$('db-admin-retire-preview').onclick();await tick();
await $('db-admin-retire-save').onclick();done(retirePreview());await read;
assert.match($('db-admin-retire-summary').textContent,/retiring.*RETIRING/);
assert.equal(dbAdmin.retirement.id,'retire-op');
''')

    def test_inspection_without_previous_receipt_displays_canonical_error(self):
        self.javascript(SETUP + r'''
$('db-admin-retire-operation').value='retire-op';
adminRoute=async()=>({http:409,body:{code:'RETIREMENT_BLOCKED',message:'Await closure',details:{state:'UNKNOWN'}}});
await $('db-admin-retire-inspect').onclick();
assert.match($('db-admin-error').textContent,/RETIREMENT_BLOCKED.*Await closure.*UNKNOWN/);
assert.equal(dbAdmin.retirement,null);
''')

    def test_frozen_scan_model_subject_and_parent_target_links_render(self):
        self.javascript(SETUP + r'''
const rows=[{kind:'stage_scan',id:'scan',state:'UNKNOWN',database_id:'db',stage_id:'stage',scan_id:'scan',run_id:'run',operation_id:'scan-op',schedule_id:'schedule',subject_id:'stage',subject_kind:'stage',parent_id:'run'},
 {kind:'mart_run',id:'mart-run',state:'RUNNING',database_id:'db',job_id:'job',mart_id:'mart',model_id:'model',target_id:'model',target_kind:'model'}];
const op={...retireOp(),dependencies:rows,blockers:[{...rows[0],code:'SCAN_UNKNOWN'}]};
const preview={...retirePreview(),dependencies:rows,blockers:op.blockers,stopped_schedule_ids:op.stopped_schedule_ids,operation:op};
adminRoute=async(path,options)=>options.method==='GET'?preview:op;
await $('db-admin-retire-preview').onclick();assert.equal(dbAdmin.retirement.id,'retire-op');
const displayed=JSON.stringify($('db-admin-retire-dependencies').rows);
for(const field of ['scan_id','model_id','subject_id','subject_kind','parent_id','target_id','target_kind'])assert.equal(displayed.includes(field),true);
assert.equal(JSON.stringify($('db-admin-retire-blockers').rows).includes('SCAN_UNKNOWN'),true);
await $('db-admin-retire-save').onclick();assert.deepEqual(dbAdmin.retirement,op);
assert.equal(dbAdmin.requests.get('db-admin-retire').uncertain,false);
''')

    def test_dependency_links_reject_unknown_and_non_string_metadata(self):
        self.javascript(SETUP + r'''
const base={kind:'run',id:'run',state:'UNKNOWN',database_id:'db'};
const links=['job_id','stage_id','mart_id','model_id','operation_id','run_id','scan_id','schedule_id','subject_id','subject_kind','target_id','target_kind','parent_id'];
const forbidden=['unknown','sql','results','result_url','path','query_id','export_id','parent_kind','parent_run_id','target_model_id'];
const invalid=[...forbidden.map(name=>[name,'synthetic-forbidden']),...links.flatMap(name=>['',null,1,false,[],{}].map(value=>[name,value]))];
for(const field of ['dependencies','blockers'])for(const [name,value] of invalid){
 const row={...base,...(field==='blockers'?{code:'RUN_UNKNOWN'}:{}),[name]:value};
 assert.equal(dbAdminRetireDependencies([row],'db',field==='blockers'),false);
 assert.equal(dbAdminRetireOperation({...retireOp(),[field]:[row]}),false);
 assert.equal(dbAdminRetirePreview({...retirePreview(),[field]:[row]}),false);
}
for(const name of ['kind','id','state','database_id']){const row={...base};delete row[name];assert.equal(dbAdminRetireDependencies([row],'db'),false);}
assert.equal(dbAdminRetireDependencies([{...base,database_id:'other'}],'db'),false);
adminRoute=async()=>({...retireOp(),dependencies:[{...base,path:'synthetic-forbidden'}]});
await $('db-admin-retire-save').onclick();assert.equal(dbAdmin.retirement,null);
assert.equal(dbAdmin.requests.get('db-admin-retire').uncertain,true);
assert.equal($('db-admin-retire-result').textContent.includes('synthetic-forbidden'),false);
''')

    def test_resource_ref_canonical_identity_is_accepted_and_displayed(self):
        self.javascript(SETUP + r'''
const binding='b125d560-a904-4c22-aafa-dd04aeb60676';
const row={kind:'resource_ref',id:binding,state:'RETAINED',database_id:'db',schema_id:'b292e623-4f82-48af-8381-f7776395433d',object_id:binding,incarnation:2};
const op={...retireOp(),dependencies:[row],blockers:[]};
adminRoute=async(path,options)=>options.method==='GET'?{...retirePreview(),dependencies:[row],blockers:[],stopped_schedule_ids:op.stopped_schedule_ids,operation:op}:op;
await $('db-admin-retire-preview').onclick();assert.deepEqual(dbAdmin.retirement,op);
assert.deepEqual($('db-admin-retire-dependencies').rows,[[row.kind,binding,'RETAINED','db',JSON.stringify({schema_id:row.schema_id,object_id:binding,incarnation:2})]]);
assert.equal($('db-admin-retire-dependencies').columns.at(-1),'Identity links');
await $('db-admin-retire-save').onclick();assert.deepEqual(dbAdmin.retirement,op);
assert.equal(dbAdmin.requests.get('db-admin-retire').uncertain,false);
''')

    def test_resource_ref_rejects_malformed_identity_and_blockers(self):
        self.javascript(SETUP + r'''
const binding='b125d560-a904-4c22-aafa-dd04aeb60676';
const row={kind:'resource_ref',id:binding,state:'RETAINED',database_id:'db',schema_id:'b292e623-4f82-48af-8381-f7776395433d',object_id:binding,incarnation:2};
const invalid=Object.keys(row).map(name=>{const bad={...row};delete bad[name];return bad;});
invalid.push(...[true,false,0,-1,1.5,'2',null,[],{},Number.MAX_SAFE_INTEGER+1].map(incarnation=>({...row,incarnation})));
for(const name of ['schema_id','object_id'])for(const value of ['',null,1,false,[],{}])invalid.push({...row,[name]:value});
invalid.push({...row,state:'UNKNOWN'},{...row,object_id:'another-binding'},{...row,database_id:'other'});
for(const name of ['job_id','code','sql','results','path','unknown'])invalid.push({...row,[name]:'forbidden'});
const otherKind=['schema_id','object_id','incarnation'].map(name=>({kind:'run',id:'run',state:'UNKNOWN',database_id:'db',[name]:row[name]}));
invalid.push(...otherKind);
const blockers=[row,{...row,code:'RESOURCE_RETAINED'},...otherKind.map(bad=>({...bad,code:'RUN_UNKNOWN'}))];
for(const [field,rows] of [['dependencies',invalid],['blockers',blockers]])for(const bad of rows){
 assert.equal(dbAdminRetireDependencies([bad],'db',field==='blockers'),false);
 assert.equal(dbAdminRetireOperation({...retireOp(),[field]:[bad]}),false);
 assert.equal(dbAdminRetirePreview({...retirePreview(),[field]:[bad]}),false);
}
adminRoute=async()=>({...retireOp(),dependencies:[{...row,incarnation:true}]});
await $('db-admin-retire-save').onclick();assert.equal(dbAdmin.retirement,null);
assert.equal(dbAdmin.requests.get('db-admin-retire').uncertain,true);
''')


if __name__ == '__main__':
    unittest.main()
