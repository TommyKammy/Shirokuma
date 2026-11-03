"""Production Compute history mount, manual paging, authority and closed rows."""
import unittest
from unittest.mock import patch
import test_workbench_platform_resources_database_ui as resources_ui

HISTORY = r"""
const historyCursor=n=>'eyJwYWdlIj'+n+'.c2lnbmF0dXJl';
const historyRow=(n=1)=>({operation_id:'00000000-0000-4000-8000-'+String(n).padStart(12,'0'),kind:'WAKE',action:'WAREHOUSE_WAKE',saved_state:'SUCCEEDED',origin:'unrecorded',stop_trigger:'unknown',accepted_at:'2026-11-03T01:02:03.123456789Z',started_at:null,completed_at:null,reconciled_at:null,error_category:null,dispatch_marker:'not_recorded',reconciled_observed_state:null});
const historyReply=(n=1)=>({scope:'platform',warehouse:'LOCAL',selected_role:'admin',observed_at:new Date().toISOString(),coverage:{kind:'latest_saved_summary',ordering:'insertion_order',full_transitions:false,liveness:false,current_probe:false},availability:'available',code:null,items:[historyRow(n)],page_cursor:historyCursor(n),next_cursor:null,scanned_count:1,omitted_count:0,limit:20});
const historyCalls=()=>calls.filter(c=>c.path.startsWith('/api/v1/bears/platform-runtime-history'));
const historyText=()=>visibleText($('observation-history-output'));
async function historyBoot(){session.platform_admin=true;await boot();}
async function historyFetch(reply=historyReply(),direction='latest'){
 route=async(path,options)=>path.startsWith('/api/v1/bears/platform-runtime-history')?reply:runtimeFixtureRoute(path,options);
 await dbLifecycleHistoryFetch(direction);
}
"""


class PlatformLifecycleHistoryDatabaseUI(unittest.TestCase):
    def javascript(self,body):
        harness=resources_ui.compute_ui.platform_ui.observations.harness
        run=harness.subprocess.run
        def adapter(args,**kwargs):
            if list(args)==['node','-']:
                kwargs['input']=kwargs['input'].replace('const $=id=>',"const $=id=>id==='observation-history-panel'&&!nodes[id]?.parentElement?null:",1)
            return run(args,**kwargs)
        with patch.object(harness.subprocess,'run',adapter):
            resources_ui.PlatformResourcesDatabaseUI.javascript(self,HISTORY+body)

    def navigation_javascript(self,body):
        resources_ui.compute_ui.platform_ui.observations.SREPolicyDatabaseUI.navigation_javascript(self,body)

    def test_actual_compute_mount_manual_authority_no_database_and_unique(self):
        self.javascript(r"""
assert.equal($('observation-history-panel'),null);session.platform_admin=true;inventory=[];schemas=[];worksheets=[];worksheetId='';dbPrincipal=JSON.stringify([session.username,session.authenticated]);dbMaximumRole='admin';dbRuntimeInstall();
const panel=$('observation-history-panel');assert.equal(panel.parentElement,$('compute'));assert.equal(panel.role,'region');assert.equal(panel['aria-labelledby'],'observation-history-title');assert.equal(calls.length,0);
for(let i=0;i<3;i++){dbRuntimeInstall();dbLifecycleHistoryInstall();dbControls();}assert.equal($('compute').descendants().filter(n=>n.id===panel.id).length,1);
assert.equal($('observation-history-fetch').disabled,true);await $('observation-history-auth').onclick();assert.deepEqual(calls.map(c=>c.path),['/api/session']);
await historyFetch();assert.ok(dbLifecycleHistory.record);assert.equal(dbCapacity.record,null);assert.equal(dbUsage.record,null);assert.equal(dbResources.record,null);assert.equal(dbPlatform.record,null);assert.equal(worksheets.length,0);
assert.match(panel.style,/min-width:0/);assert.match(panel.children[1].children[0].style,/flex-wrap:wrap/);
assert.deepEqual(historyCalls()[0].options,{headers:{'X-Shirokuma-Role':'admin'}});assert.equal(historyCalls()[0].path,'/api/v1/bears/platform-runtime-history?limit=20');
""")

    def test_navigation_locale_and_reinstall_never_autofetch(self):
        self.javascript(r"""
await historyBoot();await historyFetch();const saved=dbLifecycleHistory.record;
for(const locale of ['en','ja']){setLanguage(locale);dbControls();}for(const tab of ['sql','compute','bears','compute']){activeTab=tab;dbControls();}dbRuntimeInstall();dbLifecycleHistoryInstall();
assert.equal(historyCalls().length,1);assert.equal(dbLifecycleHistory.record,saved);assert.ok(Object.isFrozen(saved.items[0]));
assert.equal($('observation-history-title').textContent,t('Saved shared LOCAL operation history'));
""")

    def test_compact_collapsed_semantic_rows_and_bilingual_coverage(self):
        self.javascript(r"""
await historyBoot();const r=historyReply();r.items=['WAKE','CHILL','AUTO_CHILL','POLICY_SAVE','RECONCILE'].map((kind,i)=>({...historyRow(i+1),kind,action:kind==='POLICY_SAVE'?'WAREHOUSE_AUTO_CHILL':'WAREHOUSE_'+kind,stop_trigger:({CHILL:'stop_request',AUTO_CHILL:'auto_idle_request'}[kind]||'unknown'),saved_state:i===4?'RECONCILED':'SUCCEEDED',reconciled_observed_state:i===4?'active':null}));r.scanned_count=5;await historyFetch(r);
const output=$('observation-history-output'),all=output.descendants(),details=all.filter(n=>n.tagName==='DETAILS');
assert.equal(details.length,6);assert.ok(details.every(n=>!n.open));assert.equal(all.filter(n=>n.tagName==='LI').length,5);
assert.equal(output.children.filter(n=>n.tagName==='P'&&!n.hidden).length,4); // Two explanations, observed and page counts.
for(const locale of ['ja','en']){setLanguage(locale);dbControls();const text=historyText();for(const key of ['Wake request','Chill request','Automatic chill','Policy save','Reconcile','History coverage','Saved details','Saved summaries in insertion order; not a full transition log or a liveness check.','Paging excludes later inserts; saved row updates remain visible. Refresh Latest to include new operations.','Times are saved phase timestamps, not actual boot or stop times. Missing times are unrecorded.','Succeeded can include no-op or renewed-activity busy outcomes. A dispatch marker records only the saved physical_started flag, not an effect or proof of no dispatch.','Reconciled records a saved observation, not original outcome success or current health.','Saved request category follows the saved action, not actual stop cause, effect, minutes, human intent or SQL cause.','Unrecorded Wake does not imply manual start or SQL autoresume; current policy does not explain historic reasons.'])assert.ok(text.includes(t(key)),key);assert.ok(text.includes(r.items[0].accepted_at));}
for(let i=0;i<r.items.length;i++){const row=all.filter(n=>n.tagName==='LI')[i],text=visibleText(row);assert.ok(text.includes(r.items[i].operation_id));assert.ok(text.includes(r.items[i].action));assert.ok(text.includes(t('Saved chill request category')));assert.ok(text.includes(t('Saved dispatch marker')));assert.ok(!text.includes(t('Started time')+': '+t('Unrecorded')));assert.match(row.style,/min-width:0;overflow-wrap:anywhere/);}
""")

    def test_disclosure_nodes_focus_locale_and_controls_preserved_without_reads(self):
        self.javascript(r"""
for(const [id,key] of [['quick-wake','Wake'],['quick-chill','Chill'],['wake','Wake'],['chill','Chill']])ui($(id),key);
await historyBoot();const labels=()=>{assert.equal(t('Wake'),language==='ja'?'起動':'Wake');assert.equal(t('Chill'),language==='ja'?'休止':'Chill');assert.equal($('runtime-wake').textContent,t('Wake'));assert.equal($('runtime-chill').textContent,t('Chill'));assert.equal(dbRuntimeActionLabel('WAREHOUSE_WAKE'),t('Wake'));assert.equal(dbRuntimeActionLabel('WAREHOUSE_CHILL'),t('Chill'));for(const [id,key] of [['quick-wake','Wake'],['quick-chill','Chill'],['wake','Wake'],['chill','Chill']])assert.equal($(id).textContent,t(key));};
for(const locale of ['ja','en']){setLanguage(locale);dbControls();labels();}
await historyFetch();const output=$('observation-history-output'),details=output.descendants().filter(n=>n.tagName==='DETAILS'),summary=details[1].children[0];
for(const d of details)d.open=true;summary.focus();const before=calls.length;
for(const locale of ['ja','en','ja']){setLanguage(locale);dbControls();dbLifecycleHistorySync();labels();assert.equal(document.activeElement,summary);assert.deepEqual(output.descendants().filter(n=>n.tagName==='DETAILS'),details);assert.ok(details.every(n=>n.open));}
for(const d of details){d.open=false;d.dispatchEvent({type:'toggle'});d.open=true;d.dispatchEvent({type:'toggle'});}dbLifecycleHistorySync();assert.equal(calls.length,before);assert.equal(document.activeElement,summary);
""")

    def test_response_freshness_only_historical_times_and_fixed_error_labels(self):
        self.javascript(r"""
await historyBoot();const good=historyReply();good.items[0].accepted_at='2000-01-01T00:00:00.123456789Z';good.items[0].completed_at='2000-01-02T00:00:00Z';await historyFetch(good);assert.ok(dbLifecycleHistory.record);assert.ok(historyText().includes(good.items[0].accepted_at));
for(const seconds of [-121,6]){const bad=historyReply();bad.observed_at=new Date(Date.now()+seconds*1000).toISOString();await historyFetch(bad);assert.equal(dbLifecycleHistory.record,null);}
const codes=['WAKE_FAILED','ADMISSION_CONFLICT','ENGINE_ACCESS_DENIED','WAREHOUSE_BUSY','WAREHOUSE_OBSERVATION_FAILED','WAREHOUSE_WAKE_FAILED','WAKE_CANCELED','FOUNDATION_NOT_READY','POLICY_WRITE_FAILED','WAREHOUSE_STOP_UNKNOWN'];
const r=historyReply();r.items=codes.map((code,i)=>({...historyRow(i+1),error_category:code}));r.scanned_count=codes.length;await historyFetch(r);assert.ok(dbLifecycleHistory.record);
for(const locale of ['ja','en']){setLanguage(locale);dbControls();for(const key of ['Wake failed','Admission conflict','Engine access denied','Warehouse busy','Warehouse observation failed','Wake canceled','Foundation not ready','Policy write failed','Warehouse stop unknown'])assert.ok(historyText().includes(t(key)),key);}
""")

    def test_safe_error_code_in_correct_collapsed_row_details_no_fetch(self):
        self.javascript(r"""
await historyBoot();const r=historyReply();r.items=['WAKE_FAILED','WAREHOUSE_WAKE_FAILED',null].map((code,i)=>({...historyRow(i+1),error_category:code}));r.scanned_count=3;await historyFetch(r);
const rows=$('observation-history-output').descendants().filter(n=>n.tagName==='LI');
const details=rows.map(row=>row.children.find(n=>n.tagName==='DETAILS'));
assert.ok(details.every(d=>!d.open));
for(let i=0;i<2;i++){assert.ok(visibleText(details[i]).includes(r.items[i].operation_id));assert.ok(visibleText(details[i]).includes(t('Saved error code')+': '+r.items[i].error_category));assert.ok(rows[i].children.filter(n=>n.tagName!=='DETAILS').some(n=>n.textContent.includes(t('Wake failed'))));}
assert.ok(!visibleText(details[2]).includes(t('Saved error code')));
const before=calls.length,summary=details[0].children[0];summary.focus();details[0].open=true;details[0].dispatchEvent({type:'toggle'});
for(const locale of ['ja','en']){setLanguage(locale);dbLifecycleHistorySync();assert.equal(rows[0].children.find(n=>n.tagName==='DETAILS'),details[0]);assert.equal(details[0].open,true);assert.equal(document.activeElement,summary);assert.ok(visibleText(details[0]).includes(t('Saved error code')+': WAKE_FAILED'));assert.ok(visibleText(details[1]).includes(t('Saved error code')+': WAREHOUSE_WAKE_FAILED'));assert.ok(!visibleText(details[2]).includes(t('Saved error code')));}
assert.equal(calls.length,before);
""")

    def test_pagination_manual_bounded_stack_same_anchor_refresh(self):
        self.javascript(r"""
await historyBoot();const first=historyReply();first.items=Array.from({length:20},(_,i)=>historyRow(i+1));first.scanned_count=20;first.next_cursor=historyCursor(2);await historyFetch(first);
assert.equal($('observation-history-newer').disabled,true);assert.equal($('observation-history-older').disabled,false);
const second=historyReply(21);second.page_cursor=first.next_cursor;await historyFetch(second,'older');assert.equal(dbLifecycleHistory.index,1);assert.deepEqual(dbLifecycleHistory.stack,[first.page_cursor,second.page_cursor]);assert.ok(historyCalls()[1].path.endsWith('&cursor='+first.next_cursor));
await historyFetch(first,'newer');assert.equal(dbLifecycleHistory.index,0);assert.ok(historyCalls()[2].path.endsWith('&cursor='+first.page_cursor));assert.equal($('observation-history-newer').disabled,true);
await historyFetch(historyReply(50));assert.equal(dbLifecycleHistory.index,0);assert.deepEqual(dbLifecycleHistory.stack,[historyCursor(50)]);assert.equal(historyCalls()[3].path,'/api/v1/bears/platform-runtime-history?limit=20');
""")

    def test_zero_unavailable_and_omitted_candidates_distinct(self):
        self.javascript(r"""
await historyBoot();const zero=historyReply();zero.items=[];zero.scanned_count=0;await historyFetch(zero);assert.ok(dbLifecycleHistory.record);assert.ok(historyText().includes(t('No saved operations on this page.')));
zero.scanned_count=3;zero.omitted_count=3;await historyFetch(zero);assert.ok(historyText().includes('3 / 3'));
const u=historyReply();Object.assign(u,{availability:'unavailable',code:'HISTORY_UNAVAILABLE',items:null,page_cursor:null,next_cursor:null,scanned_count:0});await historyFetch(u);assert.ok(dbLifecycleHistory.record);assert.ok(historyText().includes(t('History source unavailable.')));assert.ok(!historyText().includes(t('No saved operations on this page.')));
""")

    def test_strict_top_level_coverage_counts_cursors_and_unavailable(self):
        self.javascript(r"""
await historyBoot();const changes=[r=>r.extra=true,r=>delete r.items,r=>r.scope='database',r=>r.warehouse='OTHER',r=>r.selected_role='bi_viewer',r=>r.observed_at='2026-02-30T00:00:00Z',r=>r.coverage.extra=true,r=>r.coverage.full_transitions=true,r=>r.coverage.liveness=true,r=>r.coverage.current_probe=true,r=>r.coverage.ordering='time',r=>r.availability='partial',r=>r.code='SECRET',r=>r.limit=50,r=>r.items=null,r=>r.scanned_count=0,r=>r.omitted_count=-1,r=>r.scanned_count=21,r=>r.omitted_count=true,r=>r.page_cursor='',r=>r.page_cursor='x'.repeat(2049),r=>r.next_cursor=r.page_cursor,r=>r.next_cursor=historyCursor(2),r=>r.items.push(r.items[0])];
for(const change of changes){const r=historyReply();change(r);await historyFetch(r);assert.equal(dbLifecycleHistory.record,null);assert.equal(dbLifecycleHistory.stack.length,0);assert.doesNotMatch(historyText(),/SECRET|INVALID_RESPONSE/);}
const u=historyReply();Object.assign(u,{availability:'unavailable',code:'HISTORY_UNAVAILABLE',items:null,page_cursor:null,next_cursor:null,scanned_count:0});u.omitted_count=1;await historyFetch(u);assert.equal(dbLifecycleHistory.record,null);
""")

    def test_strict_closed_rows_enums_uuid_timestamps_and_relationships(self):
        self.javascript(r"""
await historyBoot();const changes=[r=>r.actor='SECRET',r=>delete r.kind,r=>r.operation_id='actor-secret',r=>r.kind='OTHER',r=>r.action='WAREHOUSE_CHILL',r=>r.saved_state='CANCELED',r=>r.origin='manual',r=>r.stop_trigger='sql_autoresume',r=>r.dispatch_marker='sent',r=>r.error_category='SECRET_UPPERCASE',r=>r.reconciled_observed_state='healthy',r=>r.reconciled_observed_state='active',r=>r.accepted_at='2026-02-30T00:00:00Z',r=>r.started_at=true,r=>r.completed_at='2026-01-01T00:00:00.1234567890Z',r=>r.stop_trigger='stop_request',r=>{r.kind='AUTO_CHILL';r.action='WAREHOUSE_AUTO_CHILL';r.origin='explicit_request';}];
for(const change of changes){const r=historyReply();change(r.items[0]);await historyFetch(r);assert.equal(dbLifecycleHistory.record,null);assert.doesNotMatch(historyText(),/SECRET|actor-secret|healthy|sql_autoresume/);}
const good=historyReply();good.items[0].saved_state='RECONCILED';good.items[0].reconciled_observed_state='chilled';good.items[0].error_category='OTHER_RUNTIME_ERROR';await historyFetch(good);assert.ok(dbLifecycleHistory.record);
""")

    def test_true_p_only_false_like_and_current_global_roles(self):
        self.javascript(r"""
await boot();for(const p of [false,'true',1,null]){session.platform_admin=p;dbAdminSession(session);await dbLifecycleHistoryFetch();assert.equal(historyCalls().length,0);assert.equal(dbLifecycleHistory.record,null);}
session.platform_admin=true;dbAdminSession(session);await historyFetch();assert.ok(dbLifecycleHistory.record);
currentRole='bi_viewer';dbControls();assert.equal(dbLifecycleHistory.record,null);assert.equal(dbLifecycleHistory.stack.length,0);assert.ok(!historyText().includes('00000000-0000'));await dbLifecycleHistoryFetch();assert.equal(historyCalls().length,1);
currentRole='admin';dbUpdateMaximumRole('data_engineer');dbControls();await dbLifecycleHistoryFetch();assert.equal(historyCalls().length,1);
""")

    def test_pending_owner_role_navigation_admin_aba_and_late_body(self):
        self.javascript(r"""
await historyBoot();
for(const transition of [()=>{currentRole='bi_viewer';dbControls();currentRole='admin';dbControls();},()=>{activeTab='sql';dbControls();activeTab='compute';dbControls();},()=>{const old=dbPrincipal;dbPrincipal='other';dbControls();dbPrincipal=old;dbControls();},()=>{dbAdminClear();dbAdminSession(session);}]){
 let release;route=async(path,options)=>path.startsWith('/api/v1/bears/platform-runtime-history')?await new Promise(resolve=>release=resolve):runtimeFixtureRoute(path,options);
 const pending=dbLifecycleHistoryFetch();await tick();transition();release(historyReply());await pending;assert.equal(dbLifecycleHistory.record,null);assert.equal(dbLifecycleHistory.busy,null);assert.equal(dbLifecycleHistory.stack.length,0);
}
let release;route=async(path,options)=>path.startsWith('/api/v1/bears/platform-runtime-history')?{rawBody:new Promise(resolve=>release=resolve)}:runtimeFixtureRoute(path,options);
const pending=dbLifecycleHistoryFetch();await tick();currentRole='bi_viewer';dbControls();currentRole='admin';dbControls();release(JSON.stringify(historyReply()));await pending;assert.equal(dbLifecycleHistory.record,null);assert.equal(dbLifecycleHistory.busy,null);
""")

    def test_old_busy_token_cannot_clear_new_request(self):
        self.javascript(r"""
await historyBoot();let firstRelease,secondRelease,n=0;route=async(path,options)=>path.startsWith('/api/v1/bears/platform-runtime-history')?await new Promise(resolve=>{if(n++===0)firstRelease=resolve;else secondRelease=resolve;}):runtimeFixtureRoute(path,options);
const first=dbLifecycleHistoryFetch();await tick();activeTab='sql';dbControls();activeTab='compute';dbControls();const second=dbLifecycleHistoryFetch();await tick();const token=dbLifecycleHistory.busy;firstRelease(historyReply(1));await first;assert.equal(dbLifecycleHistory.busy,token);assert.equal(dbLifecycleHistory.record,null);secondRelease(historyReply(2));await second;assert.equal(dbLifecycleHistory.record.items[0].operation_id,historyRow(2).operation_id);assert.equal(dbLifecycleHistory.busy,null);
""")

    def test_stale_409_clears_paging_manual_refresh_and_authloss_purge(self):
        self.javascript(r"""
await historyBoot();const first=historyReply();first.items=Array.from({length:20},(_,i)=>historyRow(i+1));first.scanned_count=20;first.next_cursor=historyCursor(2);await historyFetch(first);
await historyFetch({http:409,body:{code:'STALE_CURSOR',message:'SECRET'}},'older');assert.equal(dbLifecycleHistory.record,null);assert.equal(dbLifecycleHistory.stack.length,0);assert.ok(historyText().includes(t('History unavailable. Select Latest saved history to retry.')));assert.doesNotMatch(historyText(),/SECRET|STALE_CURSOR/);const count=historyCalls().length;dbControls();assert.equal(historyCalls().length,count);
await historyFetch();assert.ok(dbLifecycleHistory.record);await historyFetch({http:403,body:{code:'SECRET'}});assert.equal(dbLifecycleHistory.record,null);assert.equal(dbLifecycleHistory.stack.length,0);assert.ok(!historyText().includes('00000000-0000'));
""")

    def test_database_selection_independent_and_other_drafts_preserved(self):
        self.javascript(r"""
{
await historyBoot();await historyFetch();const historySaved=dbLifecycleHistory.record;const runtime=dbRuntime,activity=dbActivity,resources=dbResources,usage=dbUsage,capacity=dbCapacity;
const query=worksheets[0]?.sql;const historyWs=worksheets.find(w=>w.id===worksheetId),historyDb=historyWs?.database_id;if(historyWs)historyWs.database_id='other-db';dbControls();if(historyWs)historyWs.database_id=historyDb;dbControls();assert.equal(dbLifecycleHistory.record,historySaved);assert.equal(historyCalls().length,1);assert.equal(dbRuntime,runtime);assert.equal(dbActivity,activity);assert.equal(dbResources,resources);assert.equal(dbUsage,usage);assert.equal(dbCapacity,capacity);assert.equal(worksheets[0]?.sql,query);
}
""")

    def test_oversize_wire_and_response_page_anchor_rejected(self):
        self.javascript(r"""
await historyBoot();await historyFetch({rawBody:' '.repeat(32769)});assert.equal(dbLifecycleHistory.record,null);
const first=historyReply();first.items=Array.from({length:20},(_,i)=>historyRow(i+1));first.scanned_count=20;first.next_cursor=historyCursor(2);await historyFetch(first);await historyFetch(historyReply(999),'older');assert.equal(dbLifecycleHistory.record,null);assert.equal(dbLifecycleHistory.stack.length,0);
""")

    def test_real_navigation_old_headers_and_body_cannot_replace_new_cache(self):
        self.navigation_javascript(r"""
await historyBoot();$('tab-compute').click();
for(const status of [200,401,403]){
 const old=deferred();route=()=>old.promise;const pending=dbLifecycleHistoryFetch();await tick();$('tab-warehouse').click();$('tab-compute').click();await historyFetch();const fresh=dbLifecycleHistory.record;old.resolve(status===200?historyReply():{http:status,body:{error:'SECRET'}});await pending;assert.equal(dbLifecycleHistory.record,fresh);assert.equal(dbAdmin.platform,true);
}
for(const loss of [()=>{currentRole='bi_viewer';dbControls();},()=>{dbPrincipal=JSON.stringify(['other',true]);dbControls();},()=>dbUpdateMaximumRole('bi_viewer'),()=>{session.platform_admin=false;dbAdminSession(session);},()=>dbAdminClear()]){
 session.platform_admin=true;session.maximum_role='admin';dbPrincipal=JSON.stringify(['owner',true]);currentRole='admin';dbUpdateMaximumRole('admin');dbAdminSession(session);await historyFetch();const old=deferred();route=()=>({rawBody:old.promise});const pending=dbLifecycleHistoryFetch();await tick();loss();assert.equal(dbLifecycleHistory.record,null);assert.doesNotMatch(historyText(),/00000000-0000/);
 session.platform_admin=true;session.maximum_role='admin';dbPrincipal=JSON.stringify(['owner',true]);currentRole='admin';dbUpdateMaximumRole('admin');dbAdminSession(session);await historyFetch();const fresh=dbLifecycleHistory.record;old.resolve(JSON.stringify(historyReply()));await pending;assert.equal(dbLifecycleHistory.record,fresh);
}
""")

    def test_existing_unknown_handles_runtime_drafts_and_audit_preserved(self):
        self.javascript(resources_ui.compute_ui.platform_ui.CURATOR+resources_ui.compute_ui.platform_ui.PROPOSAL+r"""
const nativeAppend=Node.prototype.append;Node.prototype.append=function(...items){return nativeAppend.apply(this,items.map(n=>typeof n==='string'?Object.assign(new Node('text'),{textContent:n}):n));};
session.platform_admin=true;await boot();request('proposal');await choose();await submit(pendingProposal({state:'UNKNOWN',inference_state:'UNKNOWN'}));assert.equal(dbProposalHeld().length,1);
await curatorContext();objects=[curatorObject()];await dbCuratorLoad();change('object','0');await curatorSubmit(curatorPending({state:'UNKNOWN',inference_state:'UNKNOWN'}));assert.equal(dbCuratorHeld().length,1);
route=async()=>wrap(record({state:'UNKNOWN',text:null,inference_state:'UNKNOWN'}));await $('copilot-explain').onclick();route=async()=>envelope(dbRuns.get(worksheetId),{state:'UNKNOWN'});await dbRunQuery();
const wap=dataopsPanels.get('p35-wap');schemas[0].capabilities.wap_models=true;route=async path=>({context:JSON.parse(new URL(path,'http://fixture').searchParams.get('context')),items:[]});await dataopsRefresh(wap);route=async()=>{throw Error('lost ACK');};await dataopsSubmit(wap,'/api/v1/wap/build',{model_id:'existing-model',version:1},{});
editSQL('SELECT kept SQL');editDoctor('SELECT kept Doctor');request('kept Copilot');$('p35-wap-sql').value='SELECT kept WAP';wap.sources[0].alias.value='kept';
route=runtimeFixtureRoute;await refreshStatus();const input=$('runtime-minutes');input.value='47';input.oninput();actionRoute=async()=>{throw Error('lost runtime ACK');};await dbRuntimeAct('wake');assert.ok(dbRuntime.intent);runtime.policy={minutes:12,revision:4};await refreshStatus();assert.equal(dbRuntime.conflict,true);input.focus();
dbAuditInstall();$('db-audit-source').value='ai';$('db-audit-source').oninput();$('db-audit-search').value='sql_proposal';$('db-audit-search').oninput();const auditBefore=JSON.stringify(dbAudit);
const before=otherState(),runtimeBefore=runtimeState(),intent=dbRuntime.intent,key=intent.key,op=wap.op,observerStart=calls.length;
await $('observation-history-auth').onclick();await historyFetch();const missing=historyReply();Object.assign(missing,{availability:'unavailable',code:'HISTORY_UNAVAILABLE',items:null,page_cursor:null,next_cursor:null,scanned_count:0});await historyFetch(missing);await historyFetch({http:500,body:{error:'SECRET'}});
assert.equal(JSON.stringify(dbAudit),auditBefore);assert.equal(otherState(),before);assert.equal(runtimeState(),runtimeBefore);assert.equal(document.activeElement,input);assert.equal(input.value,'47');assert.equal(dbRuntime.intent,intent);assert.equal(dbRuntime.intent.key,key);assert.equal(wap.op,op);assert.equal(wap.op.ambiguous,true);assert.equal(dbRuns.get(worksheetId).state,'UNKNOWN');assert.equal(dbProposalHeld().length,1);assert.equal(dbCuratorHeld().length,1);assert.ok(calls.slice(observerStart).every(c=>['/api/session','/api/v1/bears/platform-runtime-history?limit=20'].includes(c.path)&&!c.options?.method));
""")


if __name__=='__main__':unittest.main()
