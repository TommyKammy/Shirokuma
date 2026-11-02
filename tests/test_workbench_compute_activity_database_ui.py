"""Compute saved activity: production handlers, signed fixture cursors, Node stdin."""
import unittest

import test_workbench_audit_database_ui as audit_ui
from test_workbench_platform_observations_database_ui import PLATFORM
from test_workbench_platform_resources_database_ui import RESOURCES
from test_workbench_warehouse_runtime_ui import SETUP


ACTIVITY = r'''
const activityText=()=>auditText($('observation-activity-panel'));
const activityLists=()=>calls.filter(c=>c.path.startsWith('/api/v1/audit/events?'));
let activityReply=null,activitySources=['query','job_run'];
function activityEnvelope(path,options,extra={}){
 const q=new URLSearchParams(path.split('?')[1]),source=q.get('source');
 const e=auditEvent('event-'+source,{source,source_id:'saved-'+source,database_id:q.get('database_id'),schema_id:'schema',actor:session.username,saved_role:options.headers['X-Shirokuma-Role'],state:source==='query'?'FINISHED':'succeeded'});
 const data=auditEnvelope(path,options,e);data.context.schema_ids=['schema','raw'];
 data.filters={scope:q.get('scope'),database_id:q.get('database_id'),source,state:q.get('state')||'',search:q.get('search')||'',limit:Number(q.get('limit')),from:null,to:null};
 return {...data,...extra};
}
async function activityBoot(platform=false){
 auditCapabilities=(path,options)=>({scopes:['self',...(options.headers['X-Shirokuma-Role']==='admin'?['database']:[])],sources:activitySources});
 auditList=(path,options)=>activityReply?activityReply(path,options):activityEnvelope(path,options);
 await auditBoot(platform);await $('observation-activity-refresh').onclick();
 assert.equal(activityLists().length,0);await activityEdit('database','db');
}
async function activityEdit(key,value){const node=$('observation-activity-'+key);node.value=value;await (node.oninput||node.onchange)();}
const activitySearch=()=> $('observation-activity-submit').onclick();
const activityNext=()=> $('observation-activity-next').onclick();
const activityPrevious=()=> $('observation-activity-previous').onclick();
const activitySnapshot=()=>JSON.stringify({items:dbActivity.items,applied:dbActivity.applied,cursor:dbActivity.cursor,cursors:dbActivity.cursors,next:dbActivity.next});
const activityIndependent=()=>JSON.stringify({audit:{draft:dbAudit.draft,applied:dbAudit.applied,items:dbAudit.items,cursor:dbAudit.cursor,cursors:dbAudit.cursors,next:dbAudit.next,detail:dbAudit.detail,url:dbAudit.url},platform:dbPlatform.record,resources:dbResources.record,runtime:dbRuntime,worksheets:worksheets,sql:$('sql').value,doctor:$('bear-query-sql').value,copilot:$('copilot-request').value,wap:$('p35-wap-sql').value});
'''


class ComputeActivityDatabaseUI(unittest.TestCase):
    def javascript(self, body):
        audit_ui.AuditDatabaseUI.javascript(self, audit_ui.AUDIT + PLATFORM + RESOURCES + SETUP.replace('const posts=', 'const activityRuntimePosts=') + 'const activityRuntimeRoute=route;' + ACTIVITY + body)

    def test_mount_single_sibling_exact_sources_and_explicit_ready_target(self):
        self.javascript(r'''
await activityBoot();const panel=$('observation-activity-panel');
assert.equal(panel.parentElement,$('compute'));assert.equal(panel.role,'region');assert.equal(panel['aria-labelledby'],'observation-activity-title');
assert.ok($('compute').children.indexOf(panel)>$('compute').children.indexOf($('observation-resources-panel')));
for(let i=0;i<3;i++)dbActivityInstall();assert.equal($('compute').descendants().filter(n=>n.id===panel.id).length,1);
assert.deepEqual($('observation-activity-source').options.map(o=>o.value),['query','job_run']);assert.equal(dbActivity.source,'query');assert.equal(dbActivity.limit,'20');
assert.deepEqual($('observation-activity-scope').options.map(o=>o.value),['self','database']);
await activityEdit('database','');await activitySearch();assert.equal(activityLists().length,0);
await activityEdit('database','db');await activitySearch();assert.equal(dbActivity.items.length,1);
assert.deepEqual(Object.keys(dbActivity.items[0]),['source_id','actor','schema_id','saved_role','state','created_at','finished_at']);
assert.doesNotMatch(activityText(),/SECRET|https:\/\/never|opaque|fixture-key/);
assert.ok(panel.descendants().filter(n=>n.tagName==='BUTTON').every(n=>!/(detail|export|download|inspect|recover)/i.test(n.textContent)));
assert.equal(calls.filter(c=>c.options?.method).length,0);
''')

    def test_source_states_preserve_case_and_validate_filters_and_rows(self):
        self.javascript(r'''
await activityBoot();
for(const source of ['query','job_run']){
 await activityEdit('source',source);
 assert.deepEqual($('observation-activity-state').options.map(o=>o.value),['',...dbActivityStates[source]]);
 for(const state of dbActivityStates[source]){
  await activityEdit('state',state);activityReply=(p,o)=>{const d=activityEnvelope(p,o);d.items[0].state=state;return d;};await activitySearch();assert.equal(dbActivity.items[0].state,state);
 }
 const count=activityLists().length;await activityEdit('state',source==='query'?'succeeded':'FINISHED');await activitySearch();assert.equal(activityLists().length,count);
 await activityEdit('state','');activityReply=(p,o)=>{const d=activityEnvelope(p,o);d.items[0].state=source==='query'?'finished':'SUCCEEDED';return d;};await activitySearch();assert.equal(dbActivity.applied,null);
}
''')

    def test_self_viewer_engineer_role_actor_and_current_schema_vs_database_admin(self):
        self.javascript(r'''
await activityBoot();
for(const role of ['bi_viewer','data_engineer']){
 await activityEdit('role',role);await dbActivityRefresh();await activityEdit('database','db');await activitySearch();assert.equal(dbActivity.items.length,1);
 for(const extra of [{actor:'other'},{saved_role:'admin'},{saved_role:null},{schema_id:null},{schema_id:'old-schema'}]){
  activityReply=(p,o)=>{const d=activityEnvelope(p,o);Object.assign(d.items[0],extra);return d;};await activitySearch();assert.equal(dbActivity.applied,null);
 }
 activityReply=null;await activityEdit('scope','database');const n=activityLists().length;await activitySearch();assert.equal(activityLists().length,n);await activityEdit('scope','self');
}
await activityEdit('role','admin');await dbActivityRefresh();await activityEdit('database','db');await activityEdit('scope','database');
activityReply=(p,o)=>{const d=activityEnvelope(p,o);Object.assign(d.items[0],{actor:'other',schema_id:null,saved_role:null});return d;};await activitySearch();assert.equal(dbActivity.items[0].actor,'other');assert.equal(dbActivity.items[0].schema_id,null);assert.match(activityText(),/Not recorded/);
''')

    def test_true_platform_admin_null_grant_keeps_database_scope_exception(self):
        self.javascript(r'''
await activityBoot(true);inventory[0].grant_revision=null;await activityEdit('scope','database');await activitySearch();
assert.equal(dbActivity.items.length,1);assert.equal(dbActivity.proof.d.grant_revision,null);assert.equal(dbActivity.proof.database,'db');
assert.equal(new URLSearchParams(activityLists().at(-1).path.split('?')[1]).get('scope'),'database');
await activityEdit('database','');const n=activityLists().length;await activitySearch();assert.equal(activityLists().length,n);assert.equal(dbActivity.applied,null);
''')

    def test_capability_source_absence_and_nonready_query_denial(self):
        self.javascript(r'''
await activityBoot();activitySources=['job_run'];await dbActivityRefresh();const n=activityLists().length;await activitySearch();assert.equal(activityLists().length,n);
assert.equal($('observation-activity-source').options.find(o=>o.value==='query').disabled,true);
await activityEdit('source','job_run');await activitySearch();assert.equal(dbActivity.items.length,1);
for(const change of [{state:'PROVISIONING'},{capabilities:{query:false}},{allowed_roles:[]},{generation_id:null},{binding_revision:0}]){
 const original=structuredClone(inventory[0]);Object.assign(inventory[0],change);const count=activityLists().length;await activitySearch();assert.equal(activityLists().length,count);assert.equal(dbActivity.applied,null);inventory[0]=original;
}
''')

    def test_signed_root_next_previous_replay_survives_inserts(self):
        self.javascript(r'''
await activityBoot();const root=auditCursor(),next=auditCursor(['query',2]);let inserted=false;
activityReply=(p,o)=>{const q=new URLSearchParams(p.split('?')[1]),cursor=q.get('cursor'),d=activityEnvelope(p,o);d.items[0].source_id=cursor===next?'second':cursor===root?'first':inserted?'insert':'first';return {...d,page_cursor:cursor||root,next_cursor:cursor===next?null:next};};
await activitySearch();assert.equal(dbActivity.items[0].source_id,'first');await activityNext();assert.equal(dbActivity.items[0].source_id,'second');assert.deepEqual(dbActivity.cursors,[root]);inserted=true;await activityPrevious();assert.equal(dbActivity.items[0].source_id,'first');assert.deepEqual(dbActivity.cursors,[]);
const requests=activityLists().map(c=>new URLSearchParams(c.path.split('?')[1]));assert.equal(requests[2].get('cursor'),root);assert.equal(requests[1].get('cursor'),next);assert.equal(requests[2].get('role'),null);assert.equal(requests[2].get('observed_at'),null);
''')

    def test_every_filter_clears_page_proof_cursors_and_no_automatic_list(self):
        self.javascript(r'''
await activityBoot();
for(const [key,value] of [['state','FINISHED'],['search','saved'],['limit','1'],['scope','database'],['source','job_run'],['database','finance'],['role','bi_viewer']]){
 await activitySearch();const n=activityLists().length;await activityEdit(key,value);
 assert.equal(dbActivity.applied,null,key);assert.equal(dbActivity.proof,null,key);assert.equal(dbActivity.items.length,0,key);assert.equal(dbActivity.cursor,'');assert.deepEqual(dbActivity.cursors,[]);assert.equal(dbActivity.next,null);assert.equal(activityLists().length,n);
 await activityNext();await activityPrevious();assert.equal(activityLists().length,n);
}
''')

    def test_search_limits_cursor_bounds_and_409_reset(self):
        self.javascript(r'''
await activityBoot();
for(const limit of ['0','101','1.5','', '-1']){await activityEdit('limit',limit);const n=activityLists().length;await activitySearch();assert.equal(activityLists().length,n);}
await activityEdit('limit','100');await activityEdit('search','x'.repeat(257));let n=activityLists().length;await activitySearch();assert.equal(activityLists().length,n);
await activityEdit('search','x'.repeat(256));await activitySearch();assert.equal(dbActivity.items.length,1);
for(const extra of [{page_cursor:''},{page_cursor:'x'.repeat(24001)},{next_cursor:'x'.repeat(24001)},{items:Array(101).fill(auditEvent())}]){activityReply=(p,o)=>activityEnvelope(p,o,extra);await activitySearch();assert.equal(dbActivity.applied,null);}
activityReply=(p,o)=>activityEnvelope(p,o,{next_cursor:auditCursor(['next'])});await activitySearch();activityReply=()=>({http:409,body:{code:'STALE_CURSOR',message:'SECRET'}});await activityNext();assert.equal(dbActivity.applied,null);assert.deepEqual(dbActivity.cursors,[]);assert.match(activityText(),/Cursor expired/);assert.doesNotMatch(activityText(),/SECRET/);assert.ok(dbActivity.databases.some(d=>d.id==='db'));assert.ok(dbActivity.capabilities.sources.includes('query'));assert.equal($('observation-activity-submit').disabled,false);activityReply=null;await activitySearch();assert.equal(dbActivity.items.length,1);
''')

    def test_filter_edits_fence_late_success_and_error_without_erasing_new_page(self):
        self.javascript(r'''
await activityBoot();
for(const failure of [false,true]){
 const held=deferred();let request;activityReply=(p,o)=>{request=[p,o];return held.promise;};const old=activitySearch();await tick();await activityEdit('search','new'+failure);
 activityReply=null;await activitySearch();const snapshot=activitySnapshot();held.resolve(failure?{http:403,body:{code:'DB_ACCESS_REVOKED'}}:activityEnvelope(...request));await old;assert.equal(activitySnapshot(),snapshot);
}
''')

    def test_pre_and_post_binding_context_and_schema_mismatch_purge(self):
        self.javascript(r'''
await activityBoot();await activitySearch();inventory[0].binding_revision++;let n=activityLists().length;await activitySearch();assert.equal(activityLists().length,n);assert.equal(dbActivity.applied,null);
activityReply=(p,o)=>{const d=activityEnvelope(p,o);inventory[0].generation_id='next-generation';return d;};await activitySearch();assert.equal(dbActivity.applied,null);
for(const change of [{database_id:'finance'},{selected_role:'bi_viewer'},{principal_id:'other'},{grant_revision:999},{schema_ids:[]}]){
 activityReply=(p,o)=>{const d=activityEnvelope(p,o);Object.assign(d.context,change);return d;};await activitySearch();assert.equal(dbActivity.applied,null);
}
activityReply=(p,o)=>{const d=activityEnvelope(p,o);d.items[0].database_id='finance';return d;};await activitySearch();assert.equal(dbActivity.applied,null);
activityReply=(p,o)=>{const d=activityEnvelope(p,o);d.items[0].source='job_run';return d;};await activitySearch();assert.equal(dbActivity.applied,null);
''')

    def test_hidden_cache_purges_on_own_refresh_when_platform_caches_empty(self):
        self.javascript(r'''
await activityBoot();await activitySearch();assert.equal(dbPlatform.record,null);assert.equal(dbResources.record,null);$('tab-warehouse').click();
inventory=[];await dbRefreshAccess();assert.equal(dbActivity.applied,null);assert.equal(dbActivity.items.length,0);assert.doesNotMatch(activityText(),/saved-query|2026-10-29/);
''')

    def test_account_loss_and_late_post_authorization_do_not_restore_hidden_rows(self):
        self.javascript(r'''
await activityBoot();await activitySearch();const held=deferred();let count=0;inventoryRoute=()=>++count===2?held.promise:{items:structuredClone(inventory)};
const old=activitySearch();await tick();$('tab-jobs').click();session.maximum_role='bi_viewer';dbUpdateMaximumRole('bi_viewer');held.resolve({items:structuredClone(inventory)});await old;
assert.equal(dbActivity.applied,null);assert.equal(dbActivity.items.length,0);
''')

    def test_real_nav_language_and_authority_refresh_keep_cache_without_list(self):
        self.javascript(r'''
await activityBoot();$('tab-compute').click();await activitySearch();const saved=activitySnapshot(),n=activityLists().length;
for(const locale of ['ja','en']){setLanguage(locale);$('tab-warehouse').click();$('tab-compute').click();await dbActivityRefresh();assert.equal(activitySnapshot(),saved);assert.equal(activityLists().length,n);assert.match(activityText(),/2026-10-29T00:00:00.000Z/);assert.match(activityText(),locale==='ja'?/適用済みアクティビティ/:/Applied activity/);assert.equal($('observation-activity-previous').textContent,locale==='ja'?'前のページ':'Previous page');assert.equal($('observation-activity-next').textContent,locale==='ja'?'次のページ':'Next page');}
const context=$('observation-activity-context');assert.ok($('observation-activity-panel').descendants().indexOf(context)<$('observation-activity-panel').descendants().indexOf($('observation-activity-rows')));
await activityEdit('search','draft');setLanguage('ja');assert.equal(context.textContent,'');assert.equal(dbActivity.applied,null);
''')

    def test_independent_audit_drafts_receipts_blob_platform_resources_runtime_and_four_drafts(self):
        self.javascript(r'''
await activityBoot(true);$('tab-compute').click();
dbAudit.draft={source:'query',search:'audit draft'};dbAudit.applied={source:'query',search:'audit applied'};dbAudit.cursor=auditCursor();dbAudit.cursors=[auditCursor(['old'])];dbAudit.next=auditCursor(['next']);dbAudit.detail={id:'audit detail'};dbAudit.url='blob:kept';
$('sql').value='SQL draft';$('bear-query-sql').value='Doctor draft';$('copilot-request').value='Copilot draft';$('p35-wap-sql').value='WAP draft';
dbPlatform.record=dbPlatformRecord(platformReply());dbResources.record=dbResourcesRecord(resourceReply());
const activityAuditRoute=route;route=(p,o)=>p.startsWith(BASE)||p==='/api/status'?activityRuntimeRoute(p,o):activityAuditRoute(p,o);
await refreshStatus();$('runtime-minutes').value='47';$('runtime-minutes').oninput();assert.equal(dbRuntime.base,3);
actionRoute=async()=>{throw Error('lost acknowledgement');};await dbRuntimeAct('auto-chill');assert.ok(dbRuntime.intent);assert.deepEqual(dbRuntime.intent.body,{minutes:47,expected_revision:3});assert.match($('runtime-message').textContent,/UNKNOWN/);
const receipt=dbRuntime.intent,key=receipt.key;
const saved=activityIndependent();await activitySearch();assert.equal(activityIndependent(),saved);await activityEdit('source','job_run');await activitySearch();assert.equal(activityIndependent(),saved);await dbActivityRefresh();assert.equal(activityIndependent(),saved);
$('tab-warehouse').click();$('tab-compute').click();assert.equal(activityIndependent(),saved);assert.equal(dbRuntime.intent,receipt);assert.equal(dbRuntime.intent.key,key);assert.equal(dbRuntime.base,3);assert.equal($('runtime-minutes').value,'47');assert.equal(activityRuntimePosts().length,1);
''')

    def test_fresh_own_inventory_capabilities_surround_list_and_exact_role_header(self):
        self.javascript(r'''
await activityBoot();await activityEdit('role','bi_viewer');await dbActivityRefresh();await activityEdit('database','db');const readStart=calls.length;await activitySearch();
const reads=calls.slice(readStart);assert.deepEqual(reads.map(c=>c.path.split('?')[0]),['/api/session','/api/v1/databases','/api/v1/audit/capabilities','/api/v1/audit/events','/api/session','/api/v1/databases','/api/v1/audit/capabilities']);
assert.ok(reads.every(c=>c.options.headers['X-Shirokuma-Role']==='bi_viewer'));assert.equal(dbActivity.items.length,1);
const applied=dbActivity.applied,frozen=JSON.stringify(applied);await activityEdit('search','draft');assert.equal(JSON.stringify(applied),frozen);assert.equal(dbActivity.applied,null);
''')

    def test_late_inventory_capability_and_post_errors_cannot_touch_new_epoch(self):
        self.javascript(r'''
await activityBoot();
for(const boundary of ['inventory','capability','post']){
 const held=deferred();let first=true,reads=0;
 inventoryRoute=boundary==='inventory'?()=>first?(first=false,held.promise):{items:structuredClone(inventory)}:null;
 auditCapabilities=()=>boundary==='capability'&&first?(first=false,held.promise):boundary==='post'&&++reads===2?held.promise:{scopes:['self','database'],sources:activitySources};
 const pending=activitySearch();await tick();await activityEdit('search',boundary);inventoryRoute=null;auditCapabilities=()=>({scopes:['self','database'],sources:activitySources});await activitySearch();const saved=activitySnapshot();held.resolve({http:403,body:{code:'DB_ACCESS_REVOKED'}});await pending;assert.equal(activitySnapshot(),saved);
}
''')

    def test_periodic_hook_keeps_pending_list_token_and_genuine_auth_loss_purges(self):
        self.javascript(r'''
await activityBoot();$('tab-compute').click();
for(const loseAuthority of [false,true]){
 const held=deferred();let request;activityReply=(p,o)=>{request=[p,o];return held.promise;};
 const pending=activitySearch();await tick();const token=dbActivity.busy,epoch=dbActivity.epoch,n=activityLists().length;assert.ok(token);
 if(loseAuthority)session.maximum_role='bi_viewer';
 await periodicRefresh();
 if(loseAuthority){assert.equal(dbActivity.busy,null);assert.equal(dbActivity.applied,null);assert.equal(dbActivity.capabilities,null);assert.deepEqual(dbActivity.databases,[]);}
 else{assert.equal(dbActivity.busy,token);assert.equal(dbActivity.epoch,epoch);}
 assert.equal(activityLists().length,n);held.resolve(activityEnvelope(...request));await pending;
 assert.equal(dbActivity.items.length,loseAuthority?0:1);
}
''')

    def test_current_masked_events_404_and_auth_denials_purge_own_options_only(self):
        self.javascript(r'''
await activityBoot(true);$('tab-compute').click();
for(const status of [404,403,401]){
 activityReply=null;await dbActivityRefresh();await activitySearch();assert.equal(dbActivity.items.length,1);assert.equal($('observation-activity-submit').disabled,false);
 const independent=activityIndependent();activityReply=()=>({http:status,body:{code:status===404?'NOT_FOUND':'DB_ACCESS_REVOKED'}});await activitySearch();
 assert.equal(activityIndependent(),independent);assert.deepEqual(dbActivity.databases,[]);assert.equal(dbActivity.capabilities,null);assert.equal(dbActivity.applied,null);assert.equal(dbActivity.proof,null);assert.deepEqual(dbActivity.items,[]);assert.equal(dbActivity.cursor,'');assert.equal(dbActivity.next,null);assert.deepEqual(dbActivity.cursors,[]);
 assert.deepEqual($('observation-activity-database').options.map(o=>o.value),['']);assert.equal($('observation-activity-database').value,'');assert.equal($('observation-activity-submit').disabled,true);assert.doesNotMatch(activityText(),/Sales · db|Finance · finance|saved-query/);
}
''')

    def test_stale_masked_404_preserves_newer_authority_options_and_page(self):
        self.javascript(r'''
await activityBoot();const held=deferred();activityReply=()=>held.promise;const pending=activitySearch();await tick();
await activityEdit('search','new search');activityReply=null;await activitySearch();const saved=activitySnapshot(),databases=dbActivity.databases,capabilities=dbActivity.capabilities,choices=$('observation-activity-database').options;
held.resolve({http:404,body:{code:'NOT_FOUND'}});await pending;
assert.equal(activitySnapshot(),saved);assert.equal(dbActivity.databases,databases);assert.equal(dbActivity.capabilities,capabilities);assert.equal($('observation-activity-database').options,choices);assert.equal($('observation-activity-submit').disabled,false);
''')

    def test_closed_eight_field_filter_echo_must_match_immutable_request(self):
        self.javascript(r'''
await activityBoot();await activityEdit('search','requested search');await activityEdit('state','FINISHED');await activityEdit('limit','2');
for(const change of [{search:'wrong'},{scope:'database'},{database_id:'finance'},{source:'job_run'},{state:'UNKNOWN'},{limit:20},{limit:'2'},{from:''},{to:''},{extra:true}]){
 activityReply=(p,o)=>{const d=activityEnvelope(p,o);Object.assign(d.filters,change);return d;};await activitySearch();assert.equal(dbActivity.applied,null);assert.equal(dbActivity.proof,null);assert.deepEqual(dbActivity.items,[]);assert.equal(dbActivity.cursor,'');assert.deepEqual(dbActivity.cursors,[]);assert.equal($('observation-activity-context').textContent,'');
}
for(const key of ['scope','database_id','source','state','search','limit','from','to']){
 activityReply=(p,o)=>{const d=activityEnvelope(p,o);delete d.filters[key];return d;};await activitySearch();assert.equal(dbActivity.applied,null);
}
activityReply=null;await activitySearch();assert.equal(dbActivity.items.length,1);assert.equal(dbActivity.applied.search,'requested search');assert.match($('observation-activity-context').textContent,/requested search/);
''')

    def test_current_malformed_denials_clear_protected_options_in_list_and_refresh(self):
        self.javascript(r'''
await activityBoot(true);$('tab-compute').click();
for(const boundary of ['events','refresh'])for(const status of [401,403]){
 activityReply=null;auditCapabilities=()=>({scopes:['self','database'],sources:activitySources});await dbActivityRefresh();await activitySearch();assert.equal(dbActivity.items.length,1);
 const independent=activityIndependent();
 // The fixture supplies the actual HTTP status; Response.json fails before dbAPI checks it.
 const malformed=()=>({http:status,get body(){return JSON.parse(status===401?'':'{invalid');}});
 if(boundary==='events')activityReply=malformed;else auditCapabilities=malformed;
 await (boundary==='events'?activitySearch():dbActivityRefresh());
 assert.equal(activityIndependent(),independent);assert.deepEqual(dbActivity.databases,[]);assert.equal(dbActivity.capabilities,null);assert.equal(dbActivity.applied,null);assert.equal(dbActivity.proof,null);assert.deepEqual(dbActivity.items,[]);assert.equal(dbActivity.cursor,'');assert.equal(dbActivity.next,null);assert.deepEqual(dbActivity.cursors,[]);
 assert.deepEqual($('observation-activity-database').options.map(o=>o.value),['']);assert.equal($('observation-activity-database').value,'');assert.equal($('observation-activity-submit').disabled,true);assert.doesNotMatch(activityText(),/Sales · db|Finance · finance|saved-query/);
}
''')

    def test_stale_malformed_denials_cannot_clear_newer_protected_context(self):
        self.javascript(r'''
await activityBoot();
for(const boundary of ['events','refresh'])for(const status of [401,403]){
 const held=deferred();activityReply=null;auditCapabilities=()=>({scopes:['self','database'],sources:activitySources});
 if(boundary==='events')activityReply=()=>held.promise;else auditCapabilities=()=>held.promise;
 const pending=boundary==='events'?activitySearch():dbActivityRefresh();await tick();await activityEdit('search',boundary+status);
 activityReply=null;auditCapabilities=()=>({scopes:['self','database'],sources:activitySources});await activitySearch();
 const saved=activitySnapshot(),databases=dbActivity.databases,capabilities=dbActivity.capabilities,choices=$('observation-activity-database').options;
 held.resolve({http:status,get body(){return JSON.parse(status===401?'':'{invalid');}});await pending;
 assert.equal(activitySnapshot(),saved);assert.equal(dbActivity.databases,databases);assert.equal(dbActivity.capabilities,capabilities);assert.equal($('observation-activity-database').options,choices);assert.equal($('observation-activity-submit').disabled,false);
}
''')
