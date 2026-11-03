"""Two independent manual recent-failure panels through the shipped JS harness.

Authored for ROOT host Node execution; this worker does not execute tests.
"""
import unittest
from unittest.mock import patch
import test_workbench_platform_foundation_baseline_database_ui as foundation_ui

resources_ui = foundation_ui.resources_ui
RECENT = r"""
const recentQuerySelectorAll=Node.prototype.querySelectorAll;
Node.prototype.querySelectorAll=function(selector){if(selector==='details')return this.descendants().filter(n=>n.tagName==='DETAILS');return recentQuerySelectorAll.call(this,selector);};
const recentKinds=['sre','compute'];
const recentRoot=kind=>kind==='sre'?'observation-sre-recent-failures':'observation-recent-failures-panel';
const recentID=kind=>'observation-'+kind+'-recent-failures-';
const recentCalls=()=>calls.filter(c=>c.path==='/api/v1/bears/platform-recent-failures');
const recentText=kind=>visibleText($(recentID(kind)+'output'));
const recentItem=()=>({event_id:'repeated <img src=x>',timestamp:'2001-02-03T04:05:06.007Z',agent_id:'',action_type:'saved_action',target_system:'',status:'failed',trace_id:'',duration_ms:'18446744073709551615'});
const recentReply=()=>({scope:'platform',warehouse:'LOCAL',selected_role:'admin',observed_at:'2026-11-03T04:00:00.123456Z',source:'agenthouse_pawprints',availability:'available',code:null,coverage:{kind:'recent_failed_pawprints_metadata',limit:20,server_filtered:true,database_correlation:false,raw_logs:false,health_check:false,policy_audit:false,complete_history:false},items:[recentItem()],truncated:false});
const recentUnavailable=()=>({...recentReply(),availability:'unavailable',code:'RECENT_FAILURES_UNAVAILABLE',items:[],truncated:null});
async function recentFetch(kind='compute',reply=recentReply()){
 route=async(path,options)=>{if(path==='/api/v1/bears/platform-recent-failures'){assert.deepEqual(options,{headers:{'X-Shirokuma-Role':'admin'}});return reply;}return runtimeFixtureRoute(path,options);};
 await $(recentID(kind)+'fetch').onclick();
}
async function recentBoot(){session.platform_admin=true;await boot();}
function recentEmpty(){for(const kind of recentKinds){assert.equal(dbRecentFailures[kind].record,null);assert.ok(!recentText(kind).includes('2001-02-03'));assert.ok(!recentText(kind).includes('18446744073709551615'));}}
"""


class PlatformRecentFailuresDatabaseUI(unittest.TestCase):
    def javascript(self, body):
        harness=resources_ui.compute_ui.platform_ui.observations.harness
        run=harness.subprocess.run
        def recent_adapter(args, **kwargs):
            if list(args)==['node','-']:
                source=kwargs['input'].replace('const $=id=>',
                    "const $=id=>/^(?:observation-sre-recent-failures|observation-recent-failures-panel|observation-policy-recent-failures(?:-panel)?)$/.test(id)&&!nodes[id]?.parentElement?null:",1)
                source=source.replace('if(data?.malformed)',
                    'if(data?.recentResponse)return data.recentResponse;\n if(data?.malformed)',1)
                kwargs['input']=source
            return run(args,**kwargs)
        with patch.object(harness.subprocess,'run',recent_adapter):
            foundation_ui.PlatformFoundationBaselineDatabaseUI.javascript(self,RECENT+body)

    def navigation_javascript(self, body):
        foundation_ui.PlatformFoundationBaselineDatabaseUI.navigation_javascript(self,body)

    def test_actual_mounts_without_db_grants_no_policy_and_manual_only_io(self):
        self.javascript(r"""
for(const kind of recentKinds)assert.equal($(recentRoot(kind)),null);
session.platform_admin=true;inventory=[];schemas=[];worksheets=[];worksheetId='';dbPrincipal=JSON.stringify([session.username,session.authenticated]);dbMaximumRole='admin';
dbObservationsInstall();dbRuntimeInstall();assert.equal(calls.length,0);
for(const kind of recentKinds){const root=$(recentRoot(kind)),id=recentID(kind);assert.equal(root.parentElement,kind==='compute'?$('compute'):$('bear-panel-sre'));assert.equal(root.role,'region');assert.equal(root['aria-labelledby'],id+'title');assert.equal($(id+'fetch').disabled,true);assert.equal($(id+'auth').disabled,false);
 for(let i=0;i<3;i++){dbRecentFailuresInstall();dbObservationsInstall();dbRuntimeInstall();}assert.equal($(recentRoot(kind)),root);assert.equal(root.parentElement.descendants().filter(n=>n.id===root.id).length,1);
}
assert.equal($('observation-policy-recent-failures'),null);assert.equal($('observation-policy-recent-failures-panel'),null);
await $(recentID('sre')+'auth').onclick();assert.deepEqual(calls.map(c=>c.path),['/api/session']);assert.equal(calls[0].options,undefined);
await recentFetch();assert.equal(recentCalls().length,1);assert.ok(dbRecentFailures.compute.record);assert.equal(dbRecentFailures.sre.record,null);assert.equal(dbObservations.sre.context,null);assert.equal(worksheets.length,0);
const ids=recentKinds.flatMap(kind=>$(recentRoot(kind)).descendants().map(n=>n.id).filter(Boolean));assert.equal(new Set(ids).size,ids.length);
""")

    def test_exact_closed_dto_all_keys_nested_shapes_and_bounded_fields(self):
        self.javascript(r"""
await recentBoot();const r=recentReply();assert.ok(Object.isFrozen(dbRecentFailuresRecord(r).items[0]));
const at=(r,path)=>path.reduce((o,k)=>o[k],r);
for(const path of [[],['coverage'],['items',0]]){for(const key of Object.keys(at(recentReply(),path))){const r=recentReply();delete at(r,path)[key];assert.throws(()=>dbRecentFailuresRecord(r));}const r=recentReply();at(r,path).raw='secret';assert.throws(()=>dbRecentFailuresRecord(r));}
for(const mutate of [r=>r.scope='database',r=>r.warehouse='OTHER',r=>r.selected_role='bi_viewer',r=>r.source='legacy_pawprints',r=>r.observed_at='2026-02-30T00:00:00Z',r=>r.availability='partial',r=>r.code='raw',r=>r.coverage.kind='health',r=>r.coverage.limit=21,r=>r.coverage.server_filtered=1,...['database_correlation','raw_logs','health_check','policy_audit','complete_history'].map(k=>r=>r.coverage[k]=true),r=>r.items=null,r=>r.items=Array(21).fill(recentItem()),r=>r.items[0].status='success',r=>r.truncated=null,r=>r.truncated=true]){const r=recentReply();mutate(r);assert.throws(()=>dbRecentFailuresRecord(r));}
for(const key of ['event_id','agent_id','action_type','target_system','trace_id'])for(const value of [null,1,true,{},[],'x'.repeat(257),'bad\u0000','bad\n','\ud800']){const r=recentReply();r.items[0][key]=value;assert.throws(()=>dbRecentFailuresRecord(r));}
for(const bad of [null,[],true,'raw'])assert.throws(()=>dbRecentFailuresRecord(bad));
""")

    def test_uint64_exact_decimal_text_never_number_rounding(self):
        self.javascript(r"""
await recentBoot();for(const value of ['0','9007199254740993','18446744073709551615']){const r=recentReply();r.items[0].duration_ms=value;await recentFetch('compute',r);assert.equal(dbRecentFailures.compute.record.items[0].duration_ms,value);assert.ok(recentText('compute').includes(value));const cells=$('observation-compute-recent-failures-output').descendants().filter(n=>n.tagName==='TD');assert.equal(cells.at(-1).textContent,value);}
for(const value of [0,9007199254740992,1.5,true,false,null,{},[],'18446744073709551616','01','+1','-0','1.0','1e3','１','9'.repeat(21)]){const r=recentReply();r.items[0].duration_ms=value;assert.throws(()=>dbRecentFailuresRecord(r));}
""")

    def test_old_event_times_empty_strings_duplicates_and_all_twenty_records(self):
        self.javascript(r"""
await recentBoot();const r=recentReply();r.items=Array.from({length:20},recentItem);r.items[0].event_id='';r.items[0].action_type='';r.truncated=true;await recentFetch('sre',r);
assert.equal(dbRecentFailures.sre.record.items.length,20);assert.equal(dbRecentFailures.sre.record.items[1].event_id,dbRecentFailures.sre.record.items[2].event_id);assert.equal($(recentID('sre')+'output').descendants().filter(n=>n.tagName==='TBODY')[0].children.length,20);assert.ok(recentText('sre').includes('2001-02-03T04:05:06.007Z'));assert.ok(recentText('sre').includes(dbRecentFailuresText('(empty)')));assert.ok(recentText('sre').includes(dbRecentFailuresText('More saved failed records exist beyond these 20.')));
for(const timestamp of ['2001-02-29T00:00:00.000Z','2001-02-03 04:05:06.007','2001-02-03T04:05:06Z','2001-02-03T04:05:06.007+00:00',null]){const r=recentReply();r.items[0].timestamp=timestamp;assert.throws(()=>dbRecentFailuresRecord(r));}
""")

    def test_available_zero_differs_from_unavailable_without_health_claim(self):
        self.javascript(r"""
await recentBoot();const zero={...recentReply(),items:[]};await recentFetch('compute',zero);assert.equal(dbRecentFailures.compute.record.truncated,false);assert.ok(recentText('compute').includes(dbRecentFailuresText('No failed records returned by the source. This does not establish current health.')));
await recentFetch('compute',recentUnavailable());assert.equal(dbRecentFailures.compute.record.truncated,null);assert.ok(recentText('compute').includes(dbRecentFailuresText('Failed record source unavailable; no records can be verified.')));assert.ok(!recentText('compute').includes(dbRecentFailuresText('Source records')+': 0'));
for(const mutate of [r=>r.code=null,r=>r.items=[recentItem()],r=>r.truncated=false]){const r=recentUnavailable();mutate(r);assert.throws(()=>dbRecentFailuresRecord(r));}
assert.doesNotMatch(recentText('compute'),/healthy|policy allow|RECENT_FAILURES_UNAVAILABLE|secret/);
""")

    def test_per_panel_receipts_busy_and_fetches_are_independent(self):
        self.javascript(r"""
await recentBoot();await recentFetch('sre');const held=dbRecentFailures.sre.record;
const wait=deferred();route=()=>wait.promise;const pending=dbRecentFailuresFetch('sre');await tick();const busy=dbRecentFailures.sre.busy;
const other=recentReply();other.observed_at='2026-11-03T05:00:00.000Z';await recentFetch('compute',other);assert.equal(dbRecentFailures.sre.record,held);assert.equal(dbRecentFailures.sre.busy,busy);assert.equal(dbRecentFailures.compute.busy,null);assert.equal(dbRecentFailures.compute.record.observed_at,other.observed_at);
const compute=dbRecentFailures.compute.record;wait.resolve(recentReply());await pending;assert.equal(dbRecentFailures.compute.record,compute);assert.notEqual(dbRecentFailures.sre.record,compute);
await recentFetch('sre',recentUnavailable());assert.equal(dbRecentFailures.compute.record,compute);assert.equal(dbRecentFailures.sre.record.availability,'unavailable');
""")

    def test_authority_truth_roles_maximum_principal_no_io_and_global_purge(self):
        self.javascript(r"""
await recentBoot();for(const kind of recentKinds)await recentFetch(kind);const recentSnapshot=dbRecentFailures.compute.record;
for(const change of [{platform_admin:false},{platform_admin:1},{platform_admin:'true'},{maximum_role:'data_engineer'},{authenticated:false},{username:'other'},{data_mode:'legacy'}]){
 dbAdminSession({...session,...change});recentEmpty();const n=recentCalls().length;for(const kind of recentKinds)await dbRecentFailuresFetch(kind);assert.equal(recentCalls().length,n);dbAdminSession(session);
}
for(const role of ['bi_viewer','data_engineer']){currentRole=role;dbControls();recentEmpty();for(const kind of recentKinds)await dbRecentFailuresFetch(kind);currentRole='admin';dbControls();}
for(const kind of recentKinds)dbRecentFailures[kind].record=recentSnapshot;dbAdmin.platform=false;dbAdminSession({...session,platform_admin:false});recentEmpty();
""")

    def test_current_denial_purges_both_before_body_even_when_both_pending(self):
        self.javascript(r"""
await recentBoot();for(const status of [401,403]){
 dbAdminSession(session);await recentFetch('sre');await recentFetch('compute');const wait=deferred();route=()=>wait.promise;const old=dbRecentFailuresFetch('sre');await tick();let parsed=0;
 route=async()=>({recentResponse:{status,ok:false,text:async()=>{parsed++;throw Error('must not parse');}}});await dbRecentFailuresFetch('compute');assert.equal(parsed,0);recentEmpty();assert.equal(dbAdmin.platform,false);wait.resolve(recentReply());await old;recentEmpty();
}
""")

    def test_current_failure_clears_only_its_panel_and_sanitizes_output(self):
        self.javascript(r"""
await recentBoot();await recentFetch('sre');const saved=dbRecentFailures.sre.record;
for(const response of [{ok:true,status:200,text:async()=>'{secret'},{ok:false,status:500,text:async()=>JSON.stringify(recentReply())},{ok:true,status:200,text:async()=>JSON.stringify({...recentReply(),raw:'secret'})}]){
 await recentFetch('compute');route=async()=>({recentResponse:response});await dbRecentFailuresFetch('compute');assert.equal(dbRecentFailures.compute.record,null);assert.equal(dbRecentFailures.sre.record,saved);assert.equal(dbAdmin.platform,true);assert.ok(!recentText('compute').includes('secret'));
}
route=async()=>{throw Error('secret');};await dbRecentFailuresFetch('compute');assert.equal(dbRecentFailures.sre.record,saved);
""")

    def test_navigation_scope_role_locale_aba_reject_late_success_and_denial(self):
        self.navigation_javascript(r"""
await recentBoot();$('tab-compute').click();
for(const transition of [()=>{$('tab-warehouse').click();$('tab-compute').click();},()=>{scope('sre');scope('sre','database');},()=>{currentRole='bi_viewer';dbControls();currentRole='admin';dbControls();},()=>{setLanguage('ja');setLanguage('en');},()=>{$('tab-bears').click();$('bear-tab-policy').click();$('tab-compute').click();}]){
 for(const status of [200,401,403]){const wait=deferred();route=()=>wait.promise;const pending=dbRecentFailuresFetch('compute');await tick();transition();await recentFetch('compute');const newer=dbRecentFailures.compute.record;
 wait.resolve(status===200?recentReply():{recentResponse:{status,ok:false,text:async()=>{throw Error('late denial');}}});await pending;assert.equal(dbRecentFailures.compute.record,newer);assert.equal(dbAdmin.platform,true);}
}
""")

    def test_post_body_await_and_authority_loss_discard_protected_reply(self):
        self.navigation_javascript(r"""
await recentBoot();$('tab-compute').click();
for(const reject of [false,true]){const body=deferred(),entered=deferred();route=async()=>({recentResponse:{ok:true,status:200,text:()=>{entered.resolve();return body.promise;}}});const pending=dbRecentFailuresFetch('compute');await entered.promise;$('tab-warehouse').click();$('tab-compute').click();await recentFetch('compute');const newer=dbRecentFailures.compute.record;if(reject)body.reject(Error('secret'));else body.resolve(JSON.stringify(recentReply()));await pending;assert.equal(dbRecentFailures.compute.record,newer);}
const body=deferred(),entered=deferred();route=async()=>({recentResponse:{ok:true,status:200,text:()=>{entered.resolve();return body.promise;}}});const pending=dbRecentFailuresFetch('sre');await entered.promise;dbAdminSession({...session,platform_admin:false});body.resolve(JSON.stringify(recentReply()));await pending;recentEmpty();
""")

    def test_refresh_authority_is_manual_independent_and_late_denial_ignored(self):
        self.navigation_javascript(r"""
await recentBoot();$('tab-compute').click();await recentFetch('sre');const held=dbRecentFailures.sre.record;
const wait=deferred();sessionRoute=()=>wait.promise;const pending=dbRecentFailuresRefreshAuth('sre');await tick();const busy=dbRecentFailures.sre.busy;await recentFetch('compute');assert.equal(dbRecentFailures.sre.busy,busy);assert.equal(dbRecentFailures.sre.record,held);
$('tab-warehouse').click();$('tab-compute').click();const saved=dbRecentFailures.compute.record;wait.resolve({recentResponse:{ok:false,status:403,json:async()=>{throw Error('late');}}});await pending;assert.equal(dbRecentFailures.compute.record,saved);assert.equal(dbAdmin.platform,true);
for(const status of [401,403]){dbAdminSession(session);let parsed=0;sessionRoute=async()=>({recentResponse:{ok:false,status,json:async()=>{parsed++;throw Error('must not parse');}}});await dbRecentFailuresRefreshAuth('compute');assert.equal(parsed,0);recentEmpty();}
""")

    def test_wire_utf8_bound_and_no_production_fixture_specific_acceptance(self):
        self.javascript(r"""
await recentBoot();const raw=JSON.stringify(recentReply()),bytes=new TextEncoder().encode(raw).length;
await recentFetch('compute',{rawBody:raw+' '.repeat(65536-bytes)});assert.ok(dbRecentFailures.compute.record);
for(const rawBody of [raw+' '.repeat(65537-bytes),raw+'界'.repeat(22000)]){await recentFetch('compute',{rawBody});assert.equal(dbRecentFailures.compute.record,null);}
for(const duration of ['1','123456789','9007199254740995']){const r=recentReply();r.items[0]={...recentItem(),event_id:'arbitrary-event-'+duration,agent_id:'arbitrary-agent',action_type:'arbitrary-action',target_system:'arbitrary-target',trace_id:'arbitrary-trace',duration_ms:duration};await recentFetch('sre',r);assert.deepEqual(dbRecentFailures.sre.record,r);}
""")

    def test_bilingual_native_controls_focus_responsive_safe_render_and_no_auto_fetch(self):
        self.javascript(r"""
await recentBoot();for(const kind of recentKinds)await recentFetch(kind);const saved=recentKinds.map(k=>dbRecentFailures[k].record),count=calls.length,globalJA=JSON.stringify(i18n.ja);
for(const locale of ['ja','en']){setLanguage(locale);dbControls();for(const kind of recentKinds){const id=recentID(kind),root=$(recentRoot(kind)),text=recentText(kind);assert.equal($(id+'title').textContent,dbRecentFailuresText('Recent failed Pawprint records'));assert.ok(text.includes(saved[0].observed_at));assert.ok(text.includes(saved[0].items[0].timestamp));assert.ok(text.includes('18446744073709551615'));assert.equal($(id+'fetch').tagName,'BUTTON');assert.equal($(id+'auth').tagName,'BUTTON');$(id+'fetch').focus();assert.equal(document.activeElement,$(id+'fetch'));assert.equal($(id+'output')['aria-live'],'polite');assert.equal(root.descendants().filter(n=>['IMG','SCRIPT','INPUT','SELECT','TEXTAREA','PRE'].includes(n.tagName)).length,0);assert.equal(root.descendants().filter(n=>n.tagName==='TH').every(n=>n.scope==='col'),true);for(const label of ['Historical event time (UTC)','API snapshot creation time','Duration (ms, exact)','Source records','(empty)']){assert.ok(text.includes(dbRecentFailuresText(label)));if(locale==='ja')assert.notEqual(dbRecentFailuresText(label),label);}}
 assert.equal(calls.length,count);for(const [i,kind] of recentKinds.entries())assert.equal(dbRecentFailures[kind].record,saved[i]);}
assert.equal(JSON.stringify(i18n.ja),globalJA);
""")
        html=resources_ui.compute_ui.platform_ui.observations.harness.HTML
        self.assertIn('#observation-recent-failures-panel,#observation-sre-recent-failures',html)
        self.assertIn('table-layout:fixed;width:100%;min-width:0',html)
        self.assertIn('white-space:normal;overflow-wrap:anywhere',html)

    def test_duplicate_record_disclosures_keep_position_focus_and_same_language_dom(self):
        self.javascript(r"""
await recentBoot();const reply=recentReply();reply.items=[recentItem(),recentItem()];
for(const kind of recentKinds)await recentFetch(kind,reply);
for(const kind of recentKinds){
 const output=$(recentID(kind)+'output'),record=dbRecentFailures[kind].record;
 const details=Array.from(output.querySelectorAll('details')),second=details[1],summary=second.querySelector('summary');
 assert.equal(record.items[0].event_id,record.items[1].event_id);second.open=true;summary.focus();const count=calls.length;
 for(let i=0;i<3;i++)dbControls();
 assert.equal(output.querySelectorAll('details')[1],second);assert.equal(second.querySelector('summary'),summary);assert.equal(second.open,true);assert.equal(document.activeElement,summary);
 for(const locale of ['ja','en']){
  setLanguage(locale);dbControls();const rows=Array.from(output.querySelectorAll('details')),focusedSummary=rows[1].querySelector('summary');
  assert.equal(!!rows[0].open,false);assert.equal(rows[1].open,true);assert.equal(document.activeElement,focusedSummary);assert.equal(dbRecentFailures[kind].record,record);
  dbControls();assert.equal(output.querySelectorAll('details')[1],rows[1]);assert.equal(document.activeElement,focusedSummary);
 }
 assert.equal(calls.length,count);
 const oldSummary=document.activeElement;await recentFetch(kind,reply);
 assert.notEqual(dbRecentFailures[kind].record,record);assert.ok(Array.from(output.querySelectorAll('details')).every(d=>!d.open));assert.ok(!output.descendants().includes(oldSummary));assert.ok(!output.descendants().includes(document.activeElement));
}
dbAdminSession({...session,platform_admin:false});recentEmpty();
for(const kind of recentKinds){const output=$(recentID(kind)+'output');assert.equal(output.querySelectorAll('details').length,0);assert.equal(dbRecentFailures[kind].rendered.record,null);assert.ok(!recentText(kind).includes('repeated <img src=x>'));}
""")

    def test_real_sre_platform_visibility_navigation_preserves_receipts_options_no_fetch(self):
        self.navigation_javascript(r"""
await recentBoot();$('tab-compute').click();await recentFetch();const held=dbRecentFailures.compute.record,count=calls.length;
for(const locale of ['ja','en']){setLanguage(locale);$('tab-bears').click();$('bear-tab-sre').click();scope('sre');assert.equal($('observation-sre-recent-failures').hidden,false);assert.equal($('observation-sre-database-controls').hidden,true);const options=$('observation-sre-scope').options.map(o=>o.value);assert.deepEqual(options,['database','platform']);scope('sre','database');assert.equal($('observation-sre-recent-failures').hidden,true);$('bear-tab-policy').click();scope('policy');assert.equal($('observation-policy-recent-failures'),null);$('tab-compute').click();assert.equal(dbRecentFailures.compute.record,held);assert.equal(calls.length,count);}
""")

    def test_preserves_contexts_drafts_unknown_trackers_and_existing_platform_caches(self):
        self.javascript(r"""
session.platform_admin=true;await dualBoot();await readyObservation('sre','bi_viewer','finance','finance-marts');await readyObservation('policy','data_engineer');await fetchObservation('sre');await fetchObservation('policy');
const sc=dbObservations.sre.context,pc=dbObservations.policy.context,sr=dbObservations.sre.record,pr=dbObservations.policy.record;
editSQL('SELECT retained worksheet');editDoctor('SELECT retained Doctor');$('copilot-request').value='retained Copilot';$('p35-wap-sql').value='SELECT retained WAP';
await computeFetch();await resourceFetch();await baselineFetch();const caches=[dbPlatform.record,dbResources.record,dbFoundationBaseline.record],busy={};dbFoundationBaseline.busy=busy;
const intents=[dbExplanation.intents,dbProposal.intents,dbCurator.intents],ops=[...dataopsPanels].map(([key,p])=>p.op),before=otherState();
await recentFetch('sre');await recentFetch('compute');await dbRecentFailuresRefreshAuth('compute');assert.equal(otherState(),before);assert.equal(dbObservations.sre.context,sc);assert.equal(dbObservations.policy.context,pc);assert.equal(dbObservations.sre.record,sr);assert.equal(dbObservations.policy.record,pr);assert.equal(dbFoundationBaseline.busy,busy);
for(const [i,p] of [...dataopsPanels].map(([key,p])=>p).entries())assert.equal(p.op,ops[i]);for(const [i,p] of [dbExplanation,dbProposal,dbCurator].entries())assert.equal(p.intents,intents[i]);for(const [i,p] of [dbPlatform,dbResources,dbFoundationBaseline].entries())assert.equal(p.record,caches[i]);
const snapshot=dbRecentFailures.compute.record;dbEpoch++;dbAuthorizationVersion++;dbInventoryVersion++;dbCurrent().database_id='other';dbControls();assert.equal(dbRecentFailures.compute.record,snapshot);
""")

    def test_legacy_audit_preview_contract_stays_separate(self):
        # The existing Audit preview still owns its latest-100 endpoint/filter.
        html=resources_ui.compute_ui.platform_ui.observations.harness.HTML
        self.assertIn('/api/v1/audit/telemetry',html)
        self.javascript(r"""
await recentBoot();dbAuditInstall();
const recentAuditItems=dbInventories.get('admin').items;assert.ok(dbInventoryCurrent(recentAuditItems));
dbAudit.role='admin';dbAudit.scope='platform';dbAudit.database='';
dbAudit.capabilities={scopes:['self','database','platform'],sources:['query']};
dbAudit.proof={scope:'platform',database:'',role:'admin',items:recentAuditItems,d:undefined,binding:dbAuditBinding(undefined),capabilities:dbAudit.capabilities};
dbAudit.draft={source:'query',state:'UNKNOWN',search:'pending audit draft',from:'',to:'',limit:'2'};
dbAudit.applied={source:'query',state:'UNKNOWN',search:'saved audit search',from:'',to:'',limit:2};
dbAudit.cursors=['previous-page'];dbAudit.cursor='current-page';dbAudit.next='next-page';
dbAudit.detail=dbAuditEvent({id:'query:retained',source:'query',source_id:'retained-query',action:'execute',actor:session.username,database_id:'db',schema_id:'schema',warehouse_id:'LOCAL',saved_role:'admin',state:'UNKNOWN',created_at:'2026-11-01T01:00:00Z',updated_at:null,finished_at:null,revision:1,committed:false,has_cleanup_error:false,links:[]});
dbAudit.items=[dbAudit.detail];dbAudit.selected=dbAudit.detail.id;
dbAudit.preview={availability:'available',truncated:false,items:[{event_id:'retained-audit-preview',timestamp:'2001-02-03 04:05:06.007',agent_id:'saved-agent',action_type:'saved-action',target_system:'saved-target',status:'success',trace_id:'saved-trace',duration_ms:'9007199254740993'}]};
dbAudit.previewStamp=dbAuditStamp();dbAuditChoices(recentAuditItems);dbAuditValidate();assert.equal(dbAuditReason(),'');assert.equal(dbAuditTelemetryAllowed(),true);
dbAuditRenderRows();dbAuditRenderDetail(dbAudit.detail);dbAuditRenderTelemetry();
const recentAuditFields=['preview','previewStamp','draft','applied','cursors','detail','role','scope','database','items','selected','cursor','next','proof','capabilities'];
const recentAuditSnapshot=Object.fromEntries(recentAuditFields.map(key=>[key,dbAudit[key]])),recentAuditData=JSON.stringify(recentAuditSnapshot),recentAuditStart=calls.length;
const recentAuditDOM=['db-audit-preview','db-audit-rows','db-audit-detail'].map(id=>$(id).children[0]);assert.ok(recentAuditDOM.every(Boolean));assert.ok(visibleText($('db-audit-preview')).includes('retained-audit-preview'));
await recentFetch('sre');dbControls();await recentFetch('compute');dbControls();
for(const key of recentAuditFields)assert.equal(dbAudit[key],recentAuditSnapshot[key]);assert.equal(JSON.stringify(Object.fromEntries(recentAuditFields.map(key=>[key,dbAudit[key]]))),recentAuditData);
for(const [index,id] of ['db-audit-preview','db-audit-rows','db-audit-detail'].entries())assert.equal($(id).children[0],recentAuditDOM[index]);
assert.equal(calls.slice(recentAuditStart).filter(c=>c.path.startsWith('/api/v1/audit/')||/^\/api\/(pawprints|history|query|load)/.test(c.path)).length,0);
""")


if __name__ == '__main__':
    unittest.main()
