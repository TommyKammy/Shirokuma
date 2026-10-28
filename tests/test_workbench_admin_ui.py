"""Focused management contracts in the existing production-handler Node harness."""
import unittest

import test_workbench_database_ui as harness

HTML = harness.HTML


SETUP = r'''
session.platform_admin=true;
const managed=(id='db')=>({id,sql_catalog:id==='db'?'sales':'finance',display_name:'売上 <img onerror=x> 🐻',description:'説明\n draft',state:'REGISTERED',revision:3,
 schemas:['raw','staging','marts','sandbox'].map(name=>({id:id+'-'+name,sql_name:name,display_name:name,purpose:name,revision:2})),
 binding:{database_id:id,polaris_catalog:'catalog',desired_revision:2,applied_revision:null,applied_generation_id:null,revision:4,password:'must-not-render'}});
let adminItems=[managed(),managed('finance')],grantItems=[],adminRoute=null;
// The shared adapter normally treats /databases as ordinary GET inventory.
// Route registration POST through the management fixture without changing it.
inventoryRoute=async(path,options)=>options?.method==='POST'?(adminRoute?adminRoute(path,options):{}):{items:structuredClone(inventory)};
route=async(path,options)=>{
 if(path==='/api/v1/admin/databases')return {databases:structuredClone(adminItems)};
 if(path.endsWith('/grants')&&options.method==='GET')return {grants:structuredClone(grantItems)};
 return adminRoute?adminRoute(path,options):{};
};
const writeCalls=()=>calls.filter(c=>['PATCH','POST'].includes(c.options?.method));
const choose=async(id='db')=>{$('db-admin-select').value=id;await $('db-admin-select').onchange();};
const save=()=>$('db-admin-database-db-save').onclick();
const metadataResponse={operation_id:'meta-op',state:'APPLIED',resource_type:'database',resource_id:'db',revision:4};
await boot();await dbAdminOpen();await choose();
'''


class AdminUI(unittest.TestCase):
    javascript = harness.DatabaseUI.javascript

    def test_static_navigation_is_database_only(self):
        self.assertIn('id="tab-db-admin"', HTML)
        self.assertIn('data-tab="db-admin" hidden', HTML)
        self.javascript(r'''
await boot();assert.equal($('tab-db-admin').hidden,true);await dbAdminOpen();
assert.equal(calls.some(c=>c.path==='/api/v1/admin/databases'),false);
for(const [platform,maximum,selected] of [[false,'admin','admin'],[true,'data_engineer','admin'],[true,'admin','data_engineer']]){
 session.platform_admin=platform;session.maximum_role=maximum;dbMaximumRole=maximum;currentRole=selected;dbAdminSession(session);
 assert.equal(dbAdminAllowed(),false);assert.equal($('tab-db-admin').hidden,true);
}
''')

    def test_metadata_patch_exact_unicode_schema_and_safe_binding(self):
        self.javascript(SETUP + r'''
assert.equal($('db-admin-database-db-name').value,adminItems[0].display_name);
assert.equal($('db-admin-summary').textContent,'sales · db · REGISTERED · revision 3');
assert.equal($('db-admin-binding').textContent.includes('must-not-render'),false);
assert.equal($('db-admin-binding').textContent.includes('desired_revision'),true);
const name='  売上 <script> 🐻 e\u0301  ',description='\tline 1\r\n説明';
$('db-admin-database-db-name').value=name;$('db-admin-database-db-description').value=description;
adminRoute=async()=>metadataResponse;await save();
let call=writeCalls().at(-1);assert.equal(call.path,'/api/v1/databases/db/display');assert.equal(call.options.method,'PATCH');
assert.deepEqual(JSON.parse(call.options.body),{display_name:name,description,expected_revision:3});
assert.equal(call.options.headers['X-Shirokuma-Role'],'admin');assert.ok(call.options.headers['Idempotency-Key']);
adminRoute=async()=>({...metadataResponse,resource_type:'schema',resource_id:'db-raw',revision:3});
$('db-admin-schema-db-raw-name').value='原本 🐻';await $('db-admin-schema-db-raw-save').onclick();
call=writeCalls().at(-1);assert.equal(call.path,'/api/v1/schemas/db-raw/display');
assert.deepEqual(JSON.parse(call.options.body),{display_name:'原本 🐻',expected_revision:2});
''')

    def test_validation_codepoints_controls_and_exact_revision(self):
        self.javascript(SETUP + r'''
for(const value of ['', '   ', 'bad\u0085', '🐻'.repeat(129)]){
 $('db-admin-database-db-name').value=value;await save();assert.equal(writeCalls().length,0);
}
$('db-admin-database-db-name').value='🐻'.repeat(128);$('db-admin-database-db-description').value='ok\n\r\t';
for(const value of ['0','1.5','1e2','9007199254740992','true']){
 $('db-admin-database-db-revision').value=value;await save();assert.equal(writeCalls().length,0);
}
$('db-admin-database-db-revision').value='3';adminRoute=async()=>metadataResponse;await save();assert.equal(writeCalls().length,1);
$('db-admin-database-db-description').value='bad\u0001';await save();assert.equal(writeCalls().length,1);
''')

    def test_cas_refresh_preserves_draft_focus_expected_revision_and_worksheet(self):
        self.javascript(SETUP + r'''
const field=$('db-admin-database-db-name');field.value='編集中';field.focus();field.setSelectionRange(1,2);
$('sql').value='SELECT unsubmitted';dbSave();const worksheetsBefore=JSON.stringify(worksheets),explorer=JSON.stringify(dbExplorer);
adminRoute=async()=>({http:409,body:{error:'REVISION_CONFLICT'}});await save();
assert.equal(field.value,'編集中');assert.equal(document.activeElement,field);assert.equal(field.selectionStart,1);
adminItems[0].revision=8;adminItems[0].display_name='server';await dbAdminRefresh();
assert.equal($('db-admin-database-db-name'),field);assert.equal(field.value,'編集中');assert.equal($('db-admin-database-db-revision').value,'3');
assert.equal($('db-admin-database-db-current').textContent,'Current revision: 8');
await periodicRefresh();setLanguage('ja');assert.equal(document.activeElement,field);assert.equal(field.value,'編集中');assert.equal(field.selectionStart,1);
assert.equal($('db-admin-database-db-current').textContent,'現在の revision: 8');
assert.equal(JSON.stringify(worksheets),worksheetsBefore);assert.equal(JSON.stringify(dbExplorer),explorer);assert.equal($('sql').value,'SELECT unsubmitted');
$('db-admin-database-db-use').onclick();assert.equal($('db-admin-database-db-revision').value,'8');
assert.equal(writeCalls().length,1);
''')

    def test_uncertain_response_freezes_payload_key_and_no_automatic_retry(self):
        self.javascript(SETUP + r'''
adminRoute=async()=>({malformed:true});await save();const original=writeCalls()[0];
assert.equal($('db-admin-database-db-save').disabled,true);assert.equal($('db-admin-database-db-retry').disabled,false);
$('db-admin-database-db-name').value='new draft';await periodicRefresh();setLanguage('ja');await dbAdminRefresh();
assert.equal(writeCalls().length,1);await save();assert.equal(writeCalls().length,1);
adminRoute=async()=>({http:409,body:{error:'conflict'}});await $('db-admin-database-db-retry').onclick();
assert.equal(dbAdmin.requests.get('db-admin-database-db').uncertain,true);
adminRoute=async()=>metadataResponse;await $('db-admin-database-db-retry').onclick();
for(const c of writeCalls()){assert.equal(c.options.body,original.options.body);assert.equal(c.options.headers['Idempotency-Key'],original.options.headers['Idempotency-Key']);}
assert.equal($('db-admin-database-db-name').value,'new draft');assert.equal(writeCalls().length,3);
''')

    def test_periodic_inventory_metadata_change_keeps_active_admin_draft_and_focus(self):
        self.javascript(SETUP + r'''
activate($('tab-db-admin'));await tick();
const field=$('db-admin-database-db-name'),content=$('db-admin-content'),detail=$('db-admin-detail');
field.value='競合側の未保存 draft';field.focus();field.setSelectionRange(2,5);
const worksheetsBefore=JSON.stringify(worksheets),expected=$('db-admin-database-db-revision').value;
// A different administrator changed metadata. The ordinary 15-second inventory sees its revision first.
inventory[0].revision+=1;inventory[0].display_name='他画面で保存した表示名';adminItems[0].revision+=1;
await periodicRefresh();
assert.equal(activeTab,'db-admin');assert.equal($('tab-db-admin').getAttribute('aria-selected'),'true');
assert.equal($('db-admin').hidden,false);assert.equal(dbAdminAllowed(),true);assert.equal(dbAdmin.database,'db');
assert.equal($('db-admin-content'),content);assert.equal($('db-admin-detail'),detail);assert.equal($('db-admin-database-db-name'),field);
assert.equal(field.value,'競合側の未保存 draft');assert.equal(document.activeElement,field);assert.equal(field.selectionStart,2);assert.equal(field.selectionEnd,5);
assert.equal($('db-admin-database-db-revision').value,expected);assert.equal(JSON.stringify(worksheets),worksheetsBefore);
// Explicit management refresh exposes current CAS revision while preserving that draft and expectation.
await dbAdminRefresh();assert.equal(field.value,'競合側の未保存 draft');assert.equal($('db-admin-database-db-revision').value,expected);
assert.equal($('db-admin-database-db-current').textContent,'Current revision: 4');assert.equal(writeCalls().length,0);
''')

    def test_network_server_and_incomplete_success_remain_uncertain(self):
        for response in ["throw Error('connection lost')", "return {http:503,body:{error:'unavailable'}}", "return {}"]:
            with self.subTest(response=response):
                self.javascript(SETUP + "adminRoute=async()=>{" + response + r'''};await save();
assert.equal(writeCalls().length,1);assert.equal(dbAdmin.requests.get('db-admin-database-db').uncertain,true);
assert.equal($('db-admin-database-db-retry').disabled,false);
''')

    def test_selection_and_authorization_versions_discard_delayed_mutations(self):
        for change in ["++dbAuthorizationVersion", "dbInvalidateReceipt('admin')", "++dbEpoch", "++dbInventoryVersion", "dbPrincipal='other'"]:
            with self.subTest(change=change):
                self.javascript(SETUP + r'''
const held=deferred();adminRoute=()=>held.promise;const saving=save();await tick();assert.equal(writeCalls().length,1);
''' + change + r''';held.resolve(metadataResponse);await saving;
assert.equal($('db-admin-database-db-result').textContent,'');
''')

    def test_admin_loss_purges_payloads_and_delayed_response_and_reentry(self):
        self.javascript(SETUP + r'''
const held=deferred();adminRoute=()=>held.promise;const saving=save();await tick();
session.platform_admin=false;await dbRefreshAccess();assert.equal(dbAdmin.requests.size,0);assert.equal(dbAdmin.drafts.size,0);
assert.equal(dbAdmin.databases.length,0);assert.equal($('db-admin-content').children.length,0);assert.equal($('tab-db-admin').hidden,true);
held.resolve(metadataResponse);await saving;
session.platform_admin=true;await dbRefreshAccess();await dbAdminOpen();await choose('finance');
assert.equal($('db-admin-summary').textContent,'finance · finance · REGISTERED · revision 3');assert.equal(dbAdmin.requests.size,0);
''')

    def test_revalidated_retry_after_discarded_authorization_response(self):
        self.javascript(SETUP + r'''
const held=deferred();adminRoute=()=>held.promise;const saving=save();await tick();
++dbAuthorizationVersion;held.resolve(metadataResponse);await saving;
assert.equal($('db-admin-database-db-result').textContent,'');assert.equal($('db-admin-database-db-retry').disabled,false);
adminRoute=async()=>metadataResponse;await $('db-admin-database-db-retry').onclick();
assert.equal(writeCalls().length,2);assert.equal(writeCalls()[0].options.body,writeCalls()[1].options.body);
assert.equal(writeCalls()[0].options.headers['Idempotency-Key'],writeCalls()[1].options.headers['Idempotency-Key']);
''')

    def test_selection_keeps_uncertain_intent_and_exact_retry_before_switching(self):
        self.javascript(SETUP + r'''
$('db-admin-generation').value='gen';adminRoute=async()=>({malformed:true});await $('db-admin-plan-save').onclick();
const original=writeCalls()[0];assert.equal($('db-admin-plan-save').disabled,true);assert.equal($('db-admin-select').disabled,true);await choose('finance');
assert.equal(dbAdmin.database,'db');assert.equal($('db-admin-select').value,'db');assert.equal(dbAdmin.requests.get('db-admin-plan').uncertain,true);
await $('db-admin-plan-save').onclick();assert.equal(writeCalls().length,1);
adminRoute=async()=>({id:'plan-op',state:'AWAITING_APPLY'});await $('db-admin-plan-retry').onclick();
assert.equal(writeCalls()[1].options.body,original.options.body);assert.equal(writeCalls()[1].options.headers['Idempotency-Key'],original.options.headers['Idempotency-Key']);
assert.equal($('db-admin-select').disabled,false);await choose('finance');assert.equal(dbAdmin.database,'finance');assert.equal(dbAdmin.requests.size,0);
assert.equal($('db-admin-plan-result').textContent,'');assert.equal($('db-admin-generation').value,'gen');
adminItems=adminItems.filter(d=>d.id!=='finance');await dbAdminRefresh();
assert.equal(dbAdmin.database,'');assert.equal($('db-admin-detail').children.length,0);
''')

    def test_pending_mutation_blocks_selection_without_losing_accepted_receipt(self):
        self.javascript(SETUP + r'''
const held=deferred();adminRoute=()=>held.promise;const saving=save();await tick();
assert.equal($('db-admin-select').disabled,true);await choose('finance');assert.equal(dbAdmin.database,'db');
held.resolve(metadataResponse);await saving;assert.equal(dbAdmin.requests.get('db-admin-database-db').result.operation_id,'meta-op');
assert.equal($('db-admin-select').disabled,false);await choose('finance');assert.equal(dbAdmin.database,'finance');assert.equal(writeCalls().length,1);
''')

    def test_double_submit_and_delayed_session_do_not_admit_new_requests(self):
        self.javascript(SETUP + r'''
const held=deferred();adminRoute=()=>held.promise;const saving=save();await tick();await save();assert.equal(writeCalls().length,1);
held.resolve(metadataResponse);await saving;
const auth=deferred();sessionRoute=()=>auth.promise;const next=save();await tick();await chooseWithoutRead();
function chooseWithoutRead(){++dbAdmin.selection;dbAdmin.database='finance';}
auth.resolve(session);await next;assert.equal(writeCalls().length,1);
''')

    def test_delayed_inspection_grant_and_wrong_resource_are_not_accepted(self):
        self.javascript(SETUP + r'''
adminRoute=async()=>({...metadataResponse,resource_id:'other-db'});await save();
assert.equal(dbAdmin.requests.get('db-admin-database-db').uncertain,true);
const held=deferred();adminRoute=()=>held.promise;$('db-admin-operation-id').value='old';const inspect=$('db-admin-inspect').onclick();await tick();
$('db-admin-operation-id').value='new';held.resolve({id:'old',state:'APPLIED'});await inspect;assert.equal($('db-admin-inspection').textContent,'');
adminRoute=async()=>metadataResponse;await $('db-admin-database-db-retry').onclick();
const delayed=deferred(),oldRoute=route;route=async(path,options)=>path.endsWith('/db/grants')?delayed.promise:oldRoute(path,options);
const reading=dbAdminLoadGrants();await tick();await choose('finance');delayed.resolve({grants:[{principal_id:'stale'}]});await reading;
assert.deepEqual($('db-admin-grants').rows,[]);
''')

    def test_session_preflight_blocks_mutation_after_identity_or_role_loss(self):
        for change in ["session.username='other'", "session.maximum_role='data_engineer'", "session.platform_admin=false", "currentRole='data_engineer';dbApplyRole()"]:
            with self.subTest(change=change):
                self.javascript(SETUP + change + r''';await save();assert.equal(writeCalls().length,0);assert.equal(dbAdmin.requests.size,0);assert.equal($('tab-db-admin').hidden,true);''')

    def test_read_mismatch_and_delayed_inventory_are_not_rendered(self):
        self.javascript(SETUP + r'''
const old=route;route=async(path,options)=>path==='/api/v1/admin/databases'?{items:adminItems}:old(path,options);
await dbAdminRefresh();assert.match($('db-admin-error').textContent,/Read model mismatch/);
const held=deferred();route=async(path,options)=>path==='/api/v1/admin/databases'?held.promise:old(path,options);
const reading=dbAdminRefresh();await tick();await choose('finance');held.resolve({databases:[managed('old')]});await reading;
assert.equal(dbAdmin.database,'finance');assert.equal(dbAdmin.databases.some(d=>d.id==='old'),false);
''')

    def test_grant_exact_payload_explicit_revoke_and_statuses(self):
        self.javascript(SETUP + r'''
grantItems=[{principal_id:'new <b>',maximum_role:'data_engineer',effective_role:'revoked',revision:2,applied_revision:null,grant_revision:4},
 {principal_id:'ready',maximum_role:'bi_viewer',effective_role:'bi_viewer',revision:3,applied_revision:3,grant_revision:5},
 {principal_id:'gone',maximum_role:'revoked',effective_role:'revoked',revision:4,applied_revision:3,grant_revision:6}];
await dbAdminLoadGrants();assert.deepEqual($('db-admin-grants').rows.map(r=>r.at(-1)),['Pending backend verification','Effective','Revoked']);
assert.equal($('db-admin-grants').rows[0][0],'new <b>');assert.deepEqual($('db-admin-grants').rows[0].slice(3,6),[2,'—',4]);
$('db-admin-grant-principal').value='alice';$('db-admin-grant-role').value='data_engineer';$('db-admin-grant-revision').value='0';
adminRoute=async()=>({id:'grant-op',state:'AWAITING_APPLY'});await $('db-admin-grant-save').onclick();
assert.deepEqual(JSON.parse(writeCalls().at(-1).options.body),{principal_id:'alice',maximum_role:'data_engineer',expected_revision:0});
$('db-admin-grant-revision').value='1';await $('db-admin-grant-revoke').onclick();
assert.deepEqual(JSON.parse(writeCalls().at(-1).options.body),{principal_id:'alice',maximum_role:'revoked',expected_revision:1});
assert.equal(writeCalls().at(-1).path,'/api/v1/databases/db/grants');assert.equal(writeCalls().length,2);
''')

    def test_public_registration_plan_inspection_and_requires_operator_handoff(self):
        self.javascript(SETUP + r'''
adminRoute=async(path)=>path==='/api/v1/databases'?{operation_id:'create',database_id:'new',state:'REGISTERED'}:
 path.endsWith('/apply')?{id:'handoff',state:'REQUESTED',requires_operator:true}:{id:'plan',state:'PLANNED',revision:2,delivery_digest:'digest'};
$('db-admin-register-name').value='iceberg';await $('db-admin-register-save').onclick();assert.equal(writeCalls().length,0);
for(const name of ['system','information_schema','tpch','tpcds']){$('db-admin-register-name').value=name;await $('db-admin-register-save').onclick();assert.equal(writeCalls().length,0);}
$('db-admin-register-name').value='new_db';await $('db-admin-register-save').onclick();
assert.deepEqual(JSON.parse(writeCalls().at(-1).options.body),{sql_catalog:'new_db',profile_id:'local-polaris-1.8.0'});
$('db-admin-generation').value='gen-2';await $('db-admin-plan-save').onclick();
assert.deepEqual(JSON.parse(writeCalls().at(-1).options.body),{generation_id:'gen-2',phase:'bootstrap',predecessor_id:null});
$('db-admin-phase').value='full';$('db-admin-predecessor').value='bootstrap-op';await $('db-admin-plan-save').onclick();
assert.equal(writeCalls().at(-1).path,'/api/v1/warehouses/LOCAL/plans');assert.deepEqual(JSON.parse(writeCalls().at(-1).options.body),{generation_id:'gen-2',phase:'full',predecessor_id:'bootstrap-op'});
$('db-admin-operation-id').value='plan';await $('db-admin-inspect').onclick();assert.match($('db-admin-inspection').textContent,/PLANNED/);
assert.equal(writeCalls().length,3);$('db-admin-apply-revision').value='2';$('db-admin-delivery').value='digest';await $('db-admin-apply-save').onclick();
assert.equal(writeCalls().at(-1).path,'/api/v1/operations/plan/apply');assert.deepEqual(JSON.parse(writeCalls().at(-1).options.body),{expected_revision:2,delivery_digest:'digest'});
assert.match($('db-admin-apply-state').textContent,/execution and verification pending/);assert.equal(writeCalls().length,4);
assert.equal(dbAdmin.databases[0].state,'REGISTERED');
''')

    def test_navigation_loss_returns_to_preserved_worksheet(self):
        self.javascript(SETUP + r'''
$('sql').value='SELECT keep_draft';dbSave();const before=JSON.stringify(worksheets);
$('tab-db-admin').onclick();await tick();assert.equal(activeTab,'db-admin');assert.equal($('db-admin').hidden,false);
currentRole='data_engineer';dbApplyRole();assert.equal($('db-admin').hidden,true);assert.equal(activeTab,'warehouse');
assert.equal($('sql').value,'SELECT keep_draft');assert.equal(JSON.stringify(worksheets),before);assert.equal(dbAdmin.requests.size,0);
''')

    def test_database_name_scope_and_incomplete_operator_handoff(self):
        self.javascript(SETUP + r'''
adminRoute=async()=>({operation_id:'create',database_id:'new',state:'REGISTERED'});
for(const name of ['raw','staging','marts','sandbox','a'.repeat(31)]){
 $('db-admin-register-name').value=name;await $('db-admin-register-save').onclick();assert.equal(JSON.parse(writeCalls().at(-1).options.body).sql_catalog,name);
}
$('db-admin-register-name').value='a'.repeat(32);await $('db-admin-register-save').onclick();assert.equal(writeCalls().length,5);
adminRoute=async()=>({id:'handoff',state:'REQUESTED',requires_operator:false});
$('db-admin-operation-id').value='plan';$('db-admin-apply-revision').value='2';$('db-admin-delivery').value='digest';await $('db-admin-apply-save').onclick();
assert.equal(dbAdmin.requests.get('db-admin-apply').uncertain,true);assert.equal($('db-admin-apply-save').disabled,true);
assert.equal($('db-admin-apply-result').textContent.includes('requires_operator'),false);
const first=writeCalls().at(-1);$('db-admin-delivery').value='edited draft';
adminRoute=async()=>({id:'handoff',state:'REQUESTED',requires_operator:true});await $('db-admin-apply-retry').onclick();
assert.equal(writeCalls().at(-1).options.body,first.options.body);assert.equal(writeCalls().at(-1).options.headers['Idempotency-Key'],first.options.headers['Idempotency-Key']);
assert.match($('db-admin-apply-state').textContent,/execution and verification pending/);
''')


if __name__ == '__main__':
    unittest.main()
