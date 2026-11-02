"""Run the shipped profile UI with the shared small Node DOM/fetch adapter."""
import unittest
import test_workbench_database_ui as database_ui
from test_workbench_dataops_ui import SETUP as DATAOPS_SETUP


SETUP = DATAOPS_SETUP + r'''
const profileSnapshots=['9007199254740993','9223372036854775807'];
const profileOperation=(context,extra={})=>accepted(context,{id:'profile-op',state:'FINISHED',committed:false,
 result:{source:{snapshot:profileSnapshots[0]},row_count:'9007199254740993',column_count:'2',
 columns:[{name:'名"前',type:'long',null_count:'3002399751580331',null_percent:'33.33'},
 {name:'nested',type:{type:'list',element:'string'},null_count:'6004799503160662',null_percent:'66.67'}]},queries:[],...extra});
async function profileReady(){
 await boot();
 Object.assign(dbExplorer,{database_id:'db',schema_id:'raw',binding_revision:1,generation_id:'gen',grant_revision:4,invalid:false,schemas:structuredClone(schemas),schemaScope:dbSchemaKey(currentRole,'db')});
 dbObject=stable('orders');dbWorkspacePane='details';dbExplorerControls();
 route=async(path,options)=>{
  if(path.includes('detail=snapshots'))return {snapshots:profileSnapshots.map(snapshot_id=>({snapshot_id}))};
  const body=options?.body?JSON.parse(options.body):null;
  const context=body?.context||JSON.parse(new URL('http://localhost'+path).searchParams.get('context'));
  submissions.push({path,body,headers:options?.headers});return profileOperation(context);
 };
 await dbDescribe(dbObject,'profile');assert.equal(submissions.length,0);
 await dbProfileRefresh();assert.equal($('table-profile-run').disabled,false);
}
const profileText=id=>[$(id).textContent,...$(id).descendants().map(n=>n.textContent)].join(' ');
'''


class ProfileUI(unittest.TestCase):
    def javascript(self, body):
        database_ui.DatabaseUI.javascript(self, SETUP + '\n' + body)

    def test_explicit_allrows_primary_source_context_and_exact_strings_ja_en(self):
        self.javascript(r'''
await profileReady();
const worksheet=JSON.stringify(worksheets),sql=$('sql').value,ingest=$('p35-ingest-name').value,wap=$('p35-wap-sql').value;
assert.equal(dbProfile.context.schema_id,'raw');assert.equal(dbProfile.source.schema_id,'raw');
for(const locale of ['ja','en']){
 setLanguage(locale);dbProfileControls();const select=$('table-profile-snapshot');
 assert.equal(select.options.filter(o=>o.value==='').length,1);assert.equal(select.value,'');
 assert.deepEqual(select.options.slice(1).map(o=>o.value),profileSnapshots);
 assert.equal(submissions.length,0);
}
$('table-profile-snapshot').value=profileSnapshots[0];$('table-profile-snapshot').onchange();
await $('table-profile-run').onclick();
assert.equal(submissions.length,1);assert.equal(submissions[0].path,'/api/v1/table-profiles');
assert.deepEqual(Object.keys(submissions[0].body).sort(),['context','snapshot_id','source']);
assert.equal(submissions[0].body.context.schema_id,'raw');assert.equal(submissions[0].body.snapshot_id,profileSnapshots[0]);
for(const locale of ['ja','en']){
 setLanguage(locale);dbProfileControls();const out=$('table-profile-results');
 assert.ok(profileText(out.id).includes('9007199254740993'));
 const grid=out.children.find(n=>n.rows);assert.equal(grid.rows[0][2],'3002399751580331');assert.equal(grid.rows[0][3],'33.33%');
 assert.equal(grid.rows[1][3],'66.67%');
}
assert.equal(JSON.stringify(worksheets),worksheet);assert.equal($('sql').value,sql);
assert.equal($('p35-ingest-name').value,ingest);assert.equal($('p35-wap-sql').value,wap);
const saved=JSON.parse(storage.get(dbProfileKey));assert.ok(saved.owners[dbPrincipal].key);
assert.equal(JSON.stringify(saved).includes('null_count'),false);assert.equal(JSON.stringify(saved).includes('row_count'),false);
''')

    def test_nested_type_cells_show_logical_types_without_metadata(self):
        self.javascript(r'''
await profileReady();
const metadata={id:123456,'element-id':234567,'key-id':345678,'value-id':456789,required:true,'element-required':false,'value-required':true,doc:'internal documentation'};
const field=(name,type)=>({...metadata,name,type});
const types=[
 'decimal(38, 9)',
 {...metadata,type:'list',element:{...metadata,type:'struct',fields:[field('customer','string'),field('tags',{...metadata,type:'map',key:'string',value:'long'})]}},
 {...metadata,type:'array',items:{type:'row',fields:[field('active','boolean'),field('scores',{type:'list',element:'double'})]}},
 {...metadata,type:'map',key:'string',value:{type:'struct',fields:[field('<img src=x onerror=alert(1)>','timestamp')]}},
 {...metadata,type:'future',payload:'private metadata'},null,42,[],{type:'struct',fields:'broken'},
 {type:'list'},{type:'row',fields:[null,field('kept',null)]}
];
const columns=types.map((type,i)=>({name:'column_'+i,type,null_count:'9007199254740993',null_percent:i?'33.33':null}));
route=async()=>profileOperation(dbProfile.context,{result:{source:{snapshot:profileSnapshots[0]},row_count:'9223372036854775807',column_count:String(columns.length),columns}});
await dbProfileRun();
const rows=$('table-profile-results').children.find(n=>n.rows).rows;
assert.deepEqual(rows.map(row=>row[0]),columns.map(c=>c.name));
assert.deepEqual(rows.map(row=>row[1]),[
 'decimal(38, 9)',
 'list<struct<customer: string, tags: map<string, long>>>',
 'array<row<active: boolean, scores: list<double>>>',
 'map<string, struct<<img src=x onerror=alert(1)>: timestamp>>',
 'Unknown type','Unknown type','Unknown type','Unknown type','Unknown type',
 'list<Unknown type>','row<Unknown field, kept: Unknown type>'
]);
const typeCells=rows.map(row=>row[1]).join(' ');
for(const hidden of ['123456','234567','345678','456789','element-id','key-id','value-id','required','false','true','doc','internal documentation','payload','private metadata'])assert.equal(typeCells.includes(hidden),false,hidden);
assert.ok(rows.every(row=>row[2]==='9007199254740993'));
assert.equal(rows[0][3],'—');assert.ok(rows.slice(1).every(row=>row[3]==='33.33%'));
assert.ok(profileText('table-profile-results').includes('9223372036854775807'));
''')

    def test_current_empty_and_zero_rows_dash_no_badge(self):
        self.javascript(r'''
await profileReady();route=async path=>{if(path.includes('detail=snapshots'))return {snapshots:[]};return profileOperation(dbProfile.context,{result:{source:{snapshot:null},row_count:'0',column_count:'1',columns:[{name:'x',type:'long',null_count:'0',null_percent:null}]}});};
await dbProfileRefresh();assert.equal($('table-profile-run').disabled,false);
assert.equal($('table-profile-snapshot').options.length,1);assert.equal($('table-profile-snapshot').value,'');
await dbProfileRun();assert.equal(dbProfile.op.payload.snapshot_id,null);
assert.equal($('table-profile-results').children.find(n=>n.rows).rows[0][3],'—');
assert.equal($('table-profile-results').descendants().some(n=>String(n.className).includes('badge')),false);
''')

    def test_controls_rebuild_after_inspector_clear_and_no_auto_sql(self):
        self.javascript(r'''
await profileReady();await dbProfileRun();const op=dbProfile.op;
dbClearExplorer();assert.equal(op.operation,null);
dbObject=stable('orders');await dbDescribe(dbObject,'profile');
assert.ok($('inspect-profile').descendants().some(n=>n.id==='table-profile-run'));
assert.ok($('inspect-profile').descendants().some(n=>n.id==='table-profile-observe'));
assert.equal(submissions.length,1);assert.equal($('table-profile-observe').disabled,false);
await dbProfileRun(true);assert.equal(submissions.length,2);assert.match(submissions[1].path,/\/dataops\/profile-op/);
''')

    def test_pending_lost_response_immutable_original_key_reload_recovery(self):
        self.javascript(r'''
await profileReady();let first=true;const original=route;
route=async(path,options)=>{if(first){first=false;submissions.push({path,body:JSON.parse(options.body),headers:options.headers});throw Error('lost');}return original(path,options);};
await dbProfileRun();const op=dbProfile.op,key=op.key,body=JSON.stringify(op.payload);
assert.equal(op.ambiguous,true);assert.equal($('table-profile-new').disabled,true);assert.equal($('table-profile-cancel').disabled,true);
dbProfile.owner=null;dbProfile.handles.clear();dbProfileControls();
assert.equal(dbProfile.op.key,key);assert.equal(JSON.stringify(dbProfile.op.payload),body);assert.equal(dbProfile.op.operation,null);
assert.ok(profileText('table-profile-status').includes('Reselect the original source'));
await dbProfileRun(true);assert.equal(submissions.length,2);
assert.equal(submissions[1].headers['Idempotency-Key'],key);assert.equal(JSON.stringify(submissions[1].body),body);
assert.equal($('table-profile-new').disabled,false);
''')

    def test_terminal_observe_409_or_403_purges_statistics_but_allows_new_intent(self):
        self.javascript(r'''
await profileReady();await dbProfileRun();const op=dbProfile.op;
for(const status of [409,403]){
 route=async()=>{throw Object.assign(Error('source expired'),{status,code:status===409?'EXPORT_SNAPSHOT_UNAVAILABLE':'SQL_PERMISSION_DENIED'});};
 await dbProfileRun(true);assert.equal(op.state,'FINISHED');assert.equal(op.ambiguous,false);
 assert.equal(op.operation,null);assert.equal(profileText('table-profile-results').includes('9007199254740993'),false);
 assert.equal($('table-profile-new').disabled,false);
}
$('table-profile-new').onclick();assert.equal(dbProfile.op,null);assert.equal($('table-profile-run').disabled,true);
route=async path=>path.includes('detail=snapshots')?{snapshots:profileSnapshots.map(snapshot_id=>({snapshot_id}))}:profileOperation(dbProfile.context);
await dbProfileRefresh();assert.equal($('table-profile-run').disabled,false);await dbProfileRun();assert.notEqual(dbProfile.op.key,op.key);
''')

    def test_current_403_with_cached_inventory_never_submits(self):
        self.javascript(r'''
await profileReady();await dbProfileRun();assert.ok(dbProfile.op.operation);
inventoryRoute=()=>{throw Object.assign(Error('revoked'),{status:403,code:'DB_ACCESS_REVOKED'});};
await dbProfileRun(true);assert.equal(submissions.length,1);assert.equal(dbProfile.op.operation,null);
assert.equal(profileText('table-profile-results').includes('9007199254740993'),false);
''')

    def test_stale_owner_reply_updates_original_handle_only(self):
        self.javascript(r'''
await profileReady();const wait=deferred();route=async()=>wait.promise;
const work=dbProfileRun();await tick();const old=dbProfile.op,context=structuredClone(old.context);
dbPrincipal='other-owner';dbProfileControls();const busy={};dbProfile.busy=busy;dbProfile.notice='other draft';
wait.resolve(profileOperation(context));await work;
assert.equal(old.id,'profile-op');assert.equal(old.operation,null);assert.equal(dbProfile.op,null);assert.equal(dbProfile.busy,busy);assert.equal(dbProfile.notice,'other draft');
assert.ok(JSON.parse(storage.get(dbProfileKey)).owners[old.principal]);
dbPrincipal=old.principal;dbProfileControls();assert.equal(dbProfile.op.key,old.key);assert.equal(dbProfile.op.operation,null);
''')

    def test_stale_source_snapshot_revision_navigation_and_role_purge(self):
        self.javascript(r'''
await profileReady();await dbProfileRun();const op=dbProfile.op;
const changes=[()=>{dbObject=stable('other');},()=>{dbProfile.snapshot=profileSnapshots[1];},()=>{++dbAuthorizationVersion;},()=>{dbWorkspacePane='worksheet';},()=>{currentRole='bi_viewer';}];
for(const change of changes){
 dbObject=stable('orders');currentRole='admin';dbWorkspacePane='details';dbProfile.snapshot='';
 op.operation=profileOperation(op.context).operation;op.protected=true;op.receiptScope=dbProfileScope();op.receiptSnapshot='';
 change();dbProfileControls();assert.equal(op.operation,null);assert.equal(profileText('table-profile-results').includes('9007199254740993'),false);
}
''')

    def test_unknown_child_held_and_view_denied(self):
        self.javascript(r'''
await profileReady();route=async()=>profileOperation(dbProfile.context,{state:'ACTIVE',result:{},queries:[{state:'UNKNOWN'}]});
await dbProfileRun();assert.equal(dbProfile.op.childUnknown,true);assert.equal($('table-profile-new').disabled,true);assert.equal($('table-profile-cancel').disabled,true);
dbObject={...stable(),kind:'VIEW'};dbExplorerControls();assert.equal(inspectorTabs.find(b=>b.dataset.inspect==='profile').disabled,true);
assert.equal($('table-profile-observe').disabled,false);assert.equal($('table-profile-run').disabled,true);
assert.equal(inspectorTabs.find(b=>b.dataset.inspect==='lineage').disabled,true);
''')

    def test_late_scope_snapshot_revision_role_and_navigation_replies_hide_statistics(self):
        for change in ("dbObject=stable('other')", "dbProfile.snapshot=profileSnapshots[1]",
                       '++dbAuthorizationVersion', "currentRole='bi_viewer'", "dbShowWorkspace('worksheet')"):
            with self.subTest(change=change):
                self.javascript(r'''
await profileReady();const wait=deferred();route=async()=>wait.promise;
const work=dbProfileRun();await tick();const op=dbProfile.op,context=structuredClone(op.context);
''' + change + r''';dbProfileControls();
wait.resolve(profileOperation(context));await work;
assert.equal(op.id,'profile-op');assert.equal(op.operation,null);assert.equal(op.payload.snapshot_id,null);
assert.equal(profileText('table-profile-results').includes('9007199254740993'),false);
''')

    def test_owner_roundtrip_during_request_keeps_one_live_handle_without_stuck_busy(self):
        self.javascript(r'''
await profileReady();const wait=deferred();route=async()=>wait.promise;
const work=dbProfileRun();await tick();const op=dbProfile.op,owner=dbPrincipal;
dbPrincipal='second-owner';dbProfileControls();assert.equal(dbProfile.op,null);
dbPrincipal=owner;dbProfileControls();assert.equal(dbProfile.op,op);
wait.resolve(profileOperation(op.context));await work;
assert.equal(op.pending,null);assert.equal(dbProfile.busy,null);assert.equal(op.operation,null);
assert.equal($('table-profile-observe').disabled,false);assert.equal($('table-profile-new').disabled,false);
''')

    def test_readable_three_roles_and_keyboard_profile_tab_without_sql(self):
        self.javascript(r'''
await profileReady();const profile=inspectorTabs.find(b=>b.dataset.inspect==='profile'),previewTab=inspectorTabs.find(b=>b.dataset.inspect==='preview');
activeInspector='preview';previewTab.onkeydown({key:'ArrowRight',preventDefault(){}});assert.equal(activeInspector,'profile');assert.equal(document.activeElement,profile);
assert.equal(submissions.length,0);
for(const role of ['admin','data_engineer','bi_viewer']){
 currentRole=role;dbInventories.set(role,{items:structuredClone(inventory)});dbExplorer.schemaScope=dbSchemaKey(role,'db');dbDatabases=structuredClone(inventory);
 dbExplorerControls();assert.equal(profile.disabled,false);assert.equal(submissions.length,0);
}
''')

    def test_dynamic_inspector_keyboard_skips_view_profile_and_disabled_or_hidden_tabs(self):
        self.javascript(r'''
await profileReady();
const tab=section=>inspectorTabs.find(b=>b.dataset.inspect===section);
const worksheet=JSON.stringify(worksheets),sql=$('sql').value,ingest=$('p35-ingest-name').value,wap=$('p35-wap-sql').value;
const key=(section,value,expected)=>{
 tab(section).onkeydown({key:value,preventDefault(){}});
 assert.equal(activeInspector,expected);assert.equal(document.activeElement,tab(expected));
 assert.equal(document.activeElement.disabled,false);assert.equal(document.activeElement.hidden,false);
};
key('preview','ArrowRight','profile');await tick();
dbObject={...stable('orders_view'),kind:'VIEW'};await dbDescribe(dbObject,'preview');
assert.equal(tab('profile').disabled,true);
key('preview','ArrowRight','ddl');await tick();
tab('snapshots').disabled=true;
key('ddl','ArrowRight','columns');key('columns','ArrowLeft','ddl');
key('preview','End','ddl');key('ddl','Home','columns');await tick();
for(const role of ['data_engineer','bi_viewer','admin']){
 currentRole=role;dbInventories.set(role,{items:structuredClone(inventory)});dbExplorer.schemaScope=dbSchemaKey(role,'db');dbDatabases=structuredClone(inventory);
 dbExplorerControls();assert.equal(tab('profile').disabled,true);
 key('preview','ArrowRight','ddl');await tick();
 dbObject=stable('orders');dbExplorerControls();assert.equal(tab('profile').disabled,false);
 key('preview','ArrowRight','profile');await tick();
 dbObject={...stable('orders_view'),kind:'VIEW'};dbExplorerControls();
}
tab('ddl').hidden=true;key('preview','ArrowRight','columns');
const focused=document.activeElement;tab('profile').onkeydown({key:'Home',preventDefault(){throw Error('disabled key');}});
assert.equal(document.activeElement,focused);
assert.equal(submissions.filter(s=>s.path.includes('table-profiles')||s.path.includes('/queries')).length,0);
assert.equal(JSON.stringify(worksheets),worksheet);assert.equal($('sql').value,sql);
assert.equal($('p35-ingest-name').value,ingest);assert.equal($('p35-wap-sql').value,wap);
''')

    def test_navigation_and_pane_aba_late_post_and_observe_only_update_handle(self):
        for observe in (False, True):
            for navigation in (False, True):
                with self.subTest(observe=observe, navigation=navigation):
                    self.javascript(r'''
await profileReady();dbProfile.snapshots=[];
''' + (r'''
route=async()=>profileOperation(dbProfile.context,{state:'ACTIVE',result:null});await dbProfileRun();
''' if observe else '') + r'''
const wait=deferred();route=async()=>wait.promise;
const work=dbProfileRun(''' + str(observe).lower() + r''');await tick();const op=dbProfile.op,revision=dbProfile.revision;
''' + (r'''
activate($('tab-warehouse'));activate($('tab-catalog'));
''' if navigation else r'''
dbShowWorkspace('worksheet');dbShowWorkspace('details');
''') + r'''
assert.ok(dbProfile.revision>revision);assert.equal(dbProfile.snapshot,'');
wait.resolve(profileOperation(op.context));await work;
assert.equal(op.id,'profile-op');assert.equal(op.state,'FINISHED');assert.equal(op.operation,null);
assert.equal(profileText('table-profile-results').includes('9007199254740993'),false);
assert.equal(op.pending,null);assert.equal(dbProfile.busy,null);
''')

    def test_snapshot_locked_to_original_intent_and_reload_restores_past(self):
        for past in (False, True):
            with self.subTest(past=past):
                self.javascript(r'''
await profileReady();
''' + (r'''
$('table-profile-snapshot').value=profileSnapshots[0];$('table-profile-snapshot').onchange();
''' if past else '') + r'''
await dbProfileRun();const op=dbProfile.op,original=op.payload.snapshot_id||'';
assert.equal($('table-profile-snapshot').disabled,true);
$('table-profile-snapshot').value=original?'':profileSnapshots[1];$('table-profile-snapshot').onchange();
assert.equal(dbProfile.snapshot,original);assert.equal($('table-profile-snapshot').value,original);
await dbProfileRun(true);assert.equal(dbProfile.op.payload.snapshot_id,original||null);
assert.equal(submissions.filter(s=>s.path==='/api/v1/table-profiles').length,1);
dbProfile.owner=null;dbProfile.handles.clear();dbProfileControls();
assert.equal(dbProfile.snapshot,original);assert.equal($('table-profile-snapshot').value,original);
await dbProfileRefresh();assert.equal(dbProfile.snapshot,original);assert.equal(submissions.length,2);
$('table-profile-new').onclick();await dbProfileRefresh();assert.equal($('table-profile-snapshot').disabled,false);
$('table-profile-snapshot').value=profileSnapshots[1];$('table-profile-snapshot').onchange();
assert.equal(dbProfile.snapshot,profileSnapshots[1]);assert.equal(submissions.length,2);
''')

    def test_minimal_failed_after_source_drift_clears_old_unknown_and_allows_new_intent(self):
        self.javascript(r'''
await profileReady();route=async()=>profileOperation(dbProfile.context,{state:'ACTIVE',result:null,queries:[{state:'UNKNOWN'}]});
await dbProfileRun();const op=dbProfile.op;assert.equal(op.childUnknown,true);
route=async()=>{throw Object.assign(Error('source changed'),{status:409,code:'EXPORT_SOURCE_CHANGED',
 observation:{id:op.id,state:'FAILED',child_unknown:false}});};
await dbProfileRun(true);assert.equal(op.state,'FAILED');assert.equal(op.childUnknown,false);assert.equal(op.ambiguous,false);
assert.equal(op.operation,null);assert.equal($('table-profile-new').disabled,false);
$('table-profile-new').onclick();assert.equal(dbProfile.op,null);assert.equal($('table-profile-run').disabled,true);
''')

    def test_lost_initial_response_recreated_source_replays_original_intent_minimal_status(self):
        for state, child_unknown in (('FAILED', False), ('CANCELED', False), ('ACTIVE', False), ('UNKNOWN', True)):
            with self.subTest(state=state, child_unknown=child_unknown):
                self.javascript(r'''
await profileReady();
$('table-profile-snapshot').value=profileSnapshots[0];$('table-profile-snapshot').onchange();
route=async(path,options)=>{submissions.push({path,body:JSON.parse(options.body),headers:options.headers});throw Error('response lost');};
await dbProfileRun();const op=dbProfile.op,key=op.key,body=JSON.stringify(op.payload);
assert.equal(op.id,null);assert.equal(op.ambiguous,true);
dbProfile.owner=null;dbProfile.handles.clear();dbProfileControls();const recovered=dbProfile.op;
dbObject={...stable('orders'),ref:{...stable('orders').ref,object_id:'recreated-orders',incarnation:'new-incarnation'}};
dbProfileControls();assert.equal(dbProfileAuthorized(recovered),false);assert.equal($('table-profile-observe').disabled,false);
assert.equal($('table-profile-run').disabled,true);assert.equal($('table-profile-new').disabled,true);
route=async(path,options)=>{
 submissions.push({path,body:JSON.parse(options.body),headers:options.headers});
 throw Object.assign(Error('original source changed'),{status:409,code:'EXPORT_SOURCE_CHANGED',
 observation:{id:'recovered-profile',state:''' + repr(state) + r''',child_unknown:''' + str(child_unknown).lower() + r'''}});
};
await dbProfileRun(true);
assert.equal(submissions.length,2);assert.equal(submissions[1].path,'/api/v1/table-profiles');
assert.equal(submissions[1].headers['Idempotency-Key'],key);assert.equal(JSON.stringify(submissions[1].body),body);
assert.equal(JSON.stringify(recovered.payload),body);assert.equal(recovered.key,key);assert.equal(recovered.id,'recovered-profile');
assert.equal(recovered.state,''' + repr(state) + r''');assert.equal(recovered.childUnknown,''' + str(child_unknown).lower() + r''');
assert.equal(recovered.operation,null);assert.equal(recovered.protected,false);
assert.equal(profileText('table-profile-results').includes('9007199254740993'),false);
assert.equal($('table-profile-new').disabled,''' + str(state not in ('FAILED', 'CANCELED')).lower() + r''');
assert.equal($('table-profile-run').disabled,true);
'''+ (r'''
$('table-profile-new').onclick();assert.equal(dbProfile.op,null);assert.equal($('table-profile-run').disabled,true);
assert.equal(submissions.length,2);
''' if state in ('FAILED', 'CANCELED') else ''))

    def test_known_observe_other_selection_updates_handle_and_discards_protected_counts(self):
        self.javascript(r'''
await profileReady();await dbProfileRun();const op=dbProfile.op,body=JSON.stringify(op.payload),key=op.key;
assert.ok(op.operation.result);dbObject=stable('other');dbProfileControls();
assert.equal(dbProfileAuthorized(op),false);assert.equal($('table-profile-observe').disabled,false);
route=async(path,options)=>{
 submissions.push({path,body:JSON.parse(new URL('http://localhost'+path).searchParams.get('context')),headers:options.headers});
 return profileOperation(op.context,{state:'CANCELED',child_unknown:false});
};
await dbProfileRun(true);assert.equal(submissions.length,2);assert.match(submissions[1].path,/\/dataops\/profile-op\?/);
assert.deepEqual(submissions[1].body,op.context);assert.equal(op.state,'CANCELED');assert.equal(op.operation,null);assert.equal(op.protected,false);
assert.equal(profileText('table-profile-results').includes('9007199254740993'),false);
assert.equal(JSON.stringify(op.payload),body);assert.equal(op.key,key);assert.equal($('table-profile-new').disabled,false);
''')

    def test_observe_owner_header_and_saved_database_availability_guards(self):
        for denial in ('owner', 'header', 'database', 'grant', 'generation', 'binding', 'role'):
            with self.subTest(denial=denial):
                self.javascript(r'''
await profileReady();await dbProfileRun();const op=dbProfile.op;
''' + {
    'owner': "op.principal='foreign';",
    'header': "currentRole='bi_viewer';",
    'database': "dbInventories.set('admin',{items:[]});",
    'grant': "dbInventories.get('admin').items[0].grant_revision++;",
    'generation': "dbInventories.get('admin').items[0].generation_id='new-generation';",
    'binding': "dbInventories.get('admin').items[0].binding_revision++;",
    'role': "dbInventories.get('admin').items[0].allowed_roles=[];",
}[denial] + r'''
dbProfileControls();assert.equal($('table-profile-observe').disabled,true);assert.equal(dbProfileAuthorized(op),false);
await dbProfileRun(true);assert.equal(submissions.length,1);assert.equal(op.operation,null);
assert.equal(profileText('table-profile-results').includes('9007199254740993'),false);
''')

    def test_cancel_uses_original_context_without_source_or_inventory_and_no_metrics(self):
        for unavailable in ('unselected', 'deleted', 'grant'):
            with self.subTest(unavailable=unavailable):
                self.javascript(r'''
await profileReady();route=async()=>profileOperation(dbProfile.context,{state:'ACTIVE',result:null});await dbProfileRun();
const op=dbProfile.op,context=JSON.stringify(op.context);let inventories=0;
inventoryRoute=()=>{inventories++;throw Object.assign(Error('revoked'),{status:403,code:'DB_ACCESS_REVOKED'});};
''' + {'unselected': 'dbObject=null;', 'deleted': "dbObject=null;dbExplorer.schemas=[];dbDatabases=[];", 'grant': 'dbDatabases=[];dbInventories.clear();'}[unavailable] + r'''
dbProfileControls();assert.equal($('table-profile-cancel').disabled,false);
assert.equal($('table-profile-observe').disabled,''' + str(unavailable == 'grant').lower() + r''');
let sent;route=async(path,options)=>{sent={path,body:JSON.parse(options.body)};return accepted(op.context,{id:op.id,state:'CANCELED'});};
await dbProfileRun(true,true);assert.equal(inventories,0);assert.match(sent.path,/\/dataops\/profile-op\/cancel$/);
assert.equal(JSON.stringify(sent.body.context),context);assert.equal(op.state,'CANCELED');assert.equal(op.operation,null);
assert.equal(profileText('table-profile-results').includes('9007199254740993'),false);
''')

    def test_cancel_header_owner_unknown_guards_and_unknown_error_hold(self):
        self.javascript(r'''
await profileReady();route=async()=>profileOperation(dbProfile.context,{state:'ACTIVE',result:null});await dbProfileRun();
const op=dbProfile.op,owner=dbPrincipal;
currentRole='bi_viewer';dbProfileControls();assert.equal($('table-profile-cancel').disabled,true);
currentRole='admin';op.childUnknown=true;dbProfileControls();assert.equal($('table-profile-cancel').disabled,true);
op.childUnknown=false;dbPrincipal='foreign';dbProfileControls();assert.equal($('table-profile-cancel').disabled,true);
dbPrincipal=owner;dbProfileControls();assert.equal(dbProfile.op,op);assert.equal($('table-profile-cancel').disabled,false);
let count=0;route=async()=>{count++;throw Object.assign(Error('unresolved'),{status:409,code:'UNKNOWN_RECONCILIATION_REQUIRED',observation:{id:op.id,state:'ACTIVE'}});};
await dbProfileRun(true,true);assert.equal(count,1);assert.equal(op.ambiguous,true);assert.equal(op.childUnknown,true);
assert.equal($('table-profile-cancel').disabled,true);assert.equal($('table-profile-new').disabled,true);
await dbProfileRun(true,true);assert.equal(count,1);
''')

    def test_current_auth_failure_clears_source_context_options_and_stale_failure_does_not(self):
        self.javascript(r'''
await profileReady();await dbProfileRun();
inventoryRoute=()=>{throw Object.assign(Error('denied'),{status:403,code:'SQL_PERMISSION_DENIED'});};
await dbProfileRun(true);assert.equal(dbProfile.context,null);assert.equal(dbProfile.source,null);assert.deepEqual(dbProfile.snapshots,[]);
assert.equal($('table-profile-snapshot').options.length,1);assert.equal($('table-profile-source').textContent,'');
inventoryRoute=null;
''')
        self.javascript(r'''
await profileReady();const wait=deferred();route=async()=>wait.promise;const work=dbProfileRun();await tick();const op=dbProfile.op;
dbPrincipal='other';dbProfileControls();dbProfile.notice='new draft';const context={draft:true},source={draft:true};dbProfile.context=context;dbProfile.source=source;
wait.reject(Object.assign(Error('old denied'),{status:403,code:'DB_ACCESS_REVOKED'}));await work;
assert.equal(dbProfile.notice,'new draft');assert.equal(dbProfile.context,context);assert.equal(dbProfile.source,source);assert.equal(dbProfile.op,null);
assert.equal(op.operation,null);assert.equal(op.ambiguous,true);
''')


if __name__ == '__main__':
    unittest.main()
