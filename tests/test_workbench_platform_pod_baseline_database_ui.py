"""Actual Pod baseline handlers through the existing DOM/Node harness; no runtime IO."""
import unittest
from unittest.mock import patch
import test_workbench_platform_foundation_baseline_database_ui as foundation_ui

resources_ui = foundation_ui.resources_ui
PODS = r"""
const podQuerySelectorAll=Node.prototype.querySelectorAll;
Node.prototype.querySelectorAll=function(selector){if(selector==='details')return this.descendants().filter(n=>n.tagName==='DETAILS');return podQuerySelectorAll.call(this,selector);};
const podKinds=['policy','compute'];
const podRoot=kind=>kind==='policy'?'observation-policy-pod-baseline':'observation-pod-baseline-panel';
const podID=kind=>'observation-'+kind+'-pod-baseline-';
const podCalls=()=>calls.filter(c=>c.path==='/api/v1/bears/platform-pod-baseline');
const podText=kind=>visibleText($(podID(kind)+'output'));
const podContainer=(kind='container',ordinal=1,state='met',name=kind+'-'+ordinal)=>({name,kind,ordinal,checks:{read_only_root_filesystem:state,non_root:state,drop_all_capabilities:state,cpu_memory_limits:state}});
const podReply=()=>({scope:'platform',warehouse:'LOCAL',selected_role:'admin',observed_at:'2026-11-03T04:00:00.123456Z',baseline_version:'namespace-pod-container-declarations-v1',coverage:{kind:'namespace_pod_container_declarations',pod_spec:true,per_container:true,runtime_security:false,admission_decision:false,global_policy:false},baseline:{availability:'available',code:null,observed_at:'2026-11-03T03:59:59Z',namespace:'research-zone',pod_count:2,container_count:6,pods:['z-unlabelled','a-terminating'].map(name=>({name,containers:[podContainer('container',1,'met','main'),podContainer('init_container',1,'not_met','setup'),podContainer('ephemeral_container',1,'not_met','debug')]}))}});
const podUnavailable=()=>{const r=podReply();Object.assign(r.baseline,{availability:'unavailable',code:'POD_BASELINE_UNAVAILABLE',namespace:null,pod_count:null,container_count:null,pods:null});return r;};
async function podFetch(kind='compute',reply=podReply()){
 route=async(path,options)=>{if(path==='/api/v1/bears/platform-pod-baseline'){assert.deepEqual(options,{headers:{'X-Shirokuma-Role':'admin'}});return reply;}return runtimeFixtureRoute(path,options);};
 await $(podID(kind)+'fetch').onclick();
}
async function podBoot(){session.platform_admin=true;await boot();}
function podEmpty(){for(const kind of podKinds){assert.equal(dbPodBaseline[kind].record,null);assert.ok(!podText(kind).includes('research-zone'));assert.ok(!podText(kind).includes('z-unlabelled'));}}
"""

class PlatformPodBaselineDatabaseUI(unittest.TestCase):
    def javascript(self, body):
        harness=resources_ui.compute_ui.platform_ui.observations.harness
        run=harness.subprocess.run
        def pod_adapter(args, **kwargs):
            if list(args)==['node','-']:
                source=kwargs['input'].replace('const $=id=>',
                    "const $=id=>/^(?:observation-policy-pod-baseline|observation-pod-baseline-panel|observation-sre-pod-baseline)$/.test(id)&&!nodes[id]?.parentElement?null:",1)
                source=source.replace('if(data?.malformed)',
                    'if(data?.podResponse)return data.podResponse;\n if(data?.malformed)',1)
                kwargs['input']=source
            return run(args,**kwargs)
        with patch.object(harness.subprocess,'run',pod_adapter):
            foundation_ui.PlatformFoundationBaselineDatabaseUI.javascript(self,PODS+body)

    def navigation_javascript(self, body):
        foundation_ui.PlatformFoundationBaselineDatabaseUI.navigation_javascript(self,body)

    def test_per_panel_receipts_busy_and_fetches_are_independent(self):
        self.javascript(r"""
await podBoot();await podFetch('policy');const held=dbPodBaseline.policy.record;
const wait=deferred();route=()=>wait.promise;const pending=dbPodBaselineFetch('policy');await tick();const busy=dbPodBaseline.policy.busy;
const other=podReply();other.observed_at='2026-11-03T05:00:00.000Z';await podFetch('compute',other);assert.equal(dbPodBaseline.policy.record,held);assert.equal(dbPodBaseline.policy.busy,busy);assert.equal(dbPodBaseline.compute.busy,null);assert.equal(dbPodBaseline.compute.record.observed_at,other.observed_at);
const compute=dbPodBaseline.compute.record;wait.resolve(podReply());await pending;assert.equal(dbPodBaseline.compute.record,compute);assert.notEqual(dbPodBaseline.policy.record,compute);
await podFetch('policy',podUnavailable());assert.equal(dbPodBaseline.compute.record,compute);assert.equal(dbPodBaseline.policy.record.baseline.availability,'unavailable');
""")

    def test_authority_truth_roles_maximum_principal_no_io_and_global_purge(self):
        self.javascript(r"""
await podBoot();for(const kind of podKinds)await podFetch(kind);const podSnapshot=dbPodBaseline.compute.record;
for(const change of [{platform_admin:false},{platform_admin:1},{platform_admin:'true'},{maximum_role:'data_engineer'},{authenticated:false},{username:'other'},{data_mode:'legacy'}]){
 dbAdminSession({...session,...change});podEmpty();const n=podCalls().length;for(const kind of podKinds)await dbPodBaselineFetch(kind);assert.equal(podCalls().length,n);dbAdminSession(session);
}
for(const role of ['bi_viewer','data_engineer']){currentRole=role;dbControls();podEmpty();for(const kind of podKinds)await dbPodBaselineFetch(kind);currentRole='admin';dbControls();}
for(const kind of podKinds)dbPodBaseline[kind].record=podSnapshot;dbAdmin.platform=false;dbAdminSession({...session,platform_admin:false});podEmpty();
""")

    def test_current_denial_purges_both_before_body_even_when_both_pending(self):
        self.javascript(r"""
await podBoot();for(const status of [401,403]){
 dbAdminSession(session);await podFetch('policy');await podFetch('compute');const wait=deferred();route=()=>wait.promise;const old=dbPodBaselineFetch('policy');await tick();let parsed=0;
 route=async()=>({podResponse:{status,ok:false,text:async()=>{parsed++;throw Error('must not parse');}}});await dbPodBaselineFetch('compute');assert.equal(parsed,0);podEmpty();assert.equal(dbAdmin.platform,false);wait.resolve(podReply());await old;podEmpty();
}
""")

    def test_current_failure_clears_only_its_panel_and_sanitizes_output(self):
        self.javascript(r"""
await podBoot();await podFetch('policy');const saved=dbPodBaseline.policy.record;
for(const response of [{ok:true,status:200,text:async()=>'{secret'},{ok:false,status:500,text:async()=>JSON.stringify(podReply())},{ok:true,status:200,text:async()=>JSON.stringify({...podReply(),raw:'secret'})}]){
 await podFetch('compute');route=async()=>({podResponse:response});await dbPodBaselineFetch('compute');assert.equal(dbPodBaseline.compute.record,null);assert.equal(dbPodBaseline.policy.record,saved);assert.equal(dbAdmin.platform,true);assert.ok(!podText('compute').includes('secret'));
}
route=async()=>{throw Error('secret');};await dbPodBaselineFetch('compute');assert.equal(dbPodBaseline.policy.record,saved);
""")

    def test_navigation_scope_role_locale_aba_reject_late_success_and_denial(self):
        self.navigation_javascript(r"""
await podBoot();$('tab-compute').click();
for(const transition of [()=>{$('tab-warehouse').click();$('tab-compute').click();},()=>{scope('policy');scope('policy','database');},()=>{currentRole='bi_viewer';dbControls();currentRole='admin';dbControls();},()=>{setLanguage('ja');setLanguage('en');},()=>{$('tab-bears').click();$('bear-tab-policy').click();$('tab-compute').click();}]){
 for(const status of [200,401,403]){const wait=deferred();route=()=>wait.promise;const pending=dbPodBaselineFetch('compute');await tick();transition();await podFetch('compute');const newer=dbPodBaseline.compute.record;
 wait.resolve(status===200?podReply():{podResponse:{status,ok:false,text:async()=>{throw Error('late denial');}}});await pending;assert.equal(dbPodBaseline.compute.record,newer);assert.equal(dbAdmin.platform,true);}
}
""")

    def test_post_body_await_and_authority_loss_discard_protected_reply(self):
        self.navigation_javascript(r"""
await podBoot();$('tab-compute').click();
for(const reject of [false,true]){const body=deferred(),entered=deferred();route=async()=>({podResponse:{ok:true,status:200,text:()=>{entered.resolve();return body.promise;}}});const pending=dbPodBaselineFetch('compute');await entered.promise;$('tab-warehouse').click();$('tab-compute').click();await podFetch('compute');const newer=dbPodBaseline.compute.record;if(reject)body.reject(Error('secret'));else body.resolve(JSON.stringify(podReply()));await pending;assert.equal(dbPodBaseline.compute.record,newer);}
const body=deferred(),entered=deferred();route=async()=>({podResponse:{ok:true,status:200,text:()=>{entered.resolve();return body.promise;}}});const pending=dbPodBaselineFetch('policy');await entered.promise;dbAdminSession({...session,platform_admin:false});body.resolve(JSON.stringify(podReply()));await pending;podEmpty();
""")

    def test_refresh_authority_is_manual_independent_and_late_denial_ignored(self):
        self.navigation_javascript(r"""
await podBoot();$('tab-compute').click();await podFetch('policy');const held=dbPodBaseline.policy.record;
const wait=deferred();sessionRoute=()=>wait.promise;const pending=dbPodBaselineRefreshAuth('policy');await tick();const busy=dbPodBaseline.policy.busy;await podFetch('compute');assert.equal(dbPodBaseline.policy.busy,busy);assert.equal(dbPodBaseline.policy.record,held);
$('tab-warehouse').click();$('tab-compute').click();const saved=dbPodBaseline.compute.record;wait.resolve({podResponse:{ok:false,status:403,json:async()=>{throw Error('late');}}});await pending;assert.equal(dbPodBaseline.compute.record,saved);assert.equal(dbAdmin.platform,true);
for(const status of [401,403]){dbAdminSession(session);let parsed=0;sessionRoute=async()=>({podResponse:{ok:false,status,json:async()=>{parsed++;throw Error('must not parse');}}});await dbPodBaselineRefreshAuth('compute');assert.equal(parsed,0);podEmpty();}
""")

    def test_preserves_contexts_drafts_unknown_trackers_and_existing_platform_caches(self):
        self.javascript(r"""
session.platform_admin=true;await dualBoot();await readyObservation('sre','bi_viewer','finance','finance-marts');await readyObservation('policy','data_engineer');await fetchObservation('sre');await fetchObservation('policy');
const sc=dbObservations.sre.context,pc=dbObservations.policy.context,sr=dbObservations.sre.record,pr=dbObservations.policy.record;
editSQL('SELECT retained worksheet');editDoctor('SELECT retained Doctor');$('copilot-request').value='retained Copilot';$('p35-wap-sql').value='SELECT retained WAP';
await computeFetch();await resourceFetch();await baselineFetch();const caches=[dbPlatform.record,dbResources.record,dbFoundationBaseline.record],busy={};dbFoundationBaseline.busy=busy;
const intents=[dbExplanation.intents,dbProposal.intents,dbCurator.intents],ops=[...dataopsPanels].map(([key,p])=>p.op),before=otherState();
await podFetch('policy');await podFetch('compute');await dbPodBaselineRefreshAuth('compute');assert.equal(otherState(),before);assert.equal(dbObservations.sre.context,sc);assert.equal(dbObservations.policy.context,pc);assert.equal(dbObservations.sre.record,sr);assert.equal(dbObservations.policy.record,pr);assert.equal(dbFoundationBaseline.busy,busy);
for(const [i,p] of [...dataopsPanels].map(([key,p])=>p).entries())assert.equal(p.op,ops[i]);for(const [i,p] of [dbExplanation,dbProposal,dbCurator].entries())assert.equal(p.intents,intents[i]);for(const [i,p] of [dbPlatform,dbResources,dbFoundationBaseline].entries())assert.equal(p.record,caches[i]);
const snapshot=dbPodBaseline.compute.record;dbEpoch++;dbAuthorizationVersion++;dbInventoryVersion++;dbCurrent().database_id='other';dbControls();assert.equal(dbPodBaseline.compute.record,snapshot);
""")

    def test_legacy_audit_preview_contract_stays_separate(self):
        # The existing Audit preview still owns its latest-100 endpoint/filter.
        html=resources_ui.compute_ui.platform_ui.observations.harness.HTML
        self.assertIn('/api/v1/audit/telemetry',html)
        self.javascript(r"""
await podBoot();dbAuditInstall();
const podAuditItems=dbInventories.get('admin').items;assert.ok(dbInventoryCurrent(podAuditItems));
dbAudit.role='admin';dbAudit.scope='platform';dbAudit.database='';
dbAudit.capabilities={scopes:['self','database','platform'],sources:['query']};
dbAudit.proof={scope:'platform',database:'',role:'admin',items:podAuditItems,d:undefined,binding:dbAuditBinding(undefined),capabilities:dbAudit.capabilities};
dbAudit.draft={source:'query',state:'UNKNOWN',search:'pending audit draft',from:'',to:'',limit:'2'};
dbAudit.applied={source:'query',state:'UNKNOWN',search:'saved audit search',from:'',to:'',limit:2};
dbAudit.cursors=['previous-page'];dbAudit.cursor='current-page';dbAudit.next='next-page';
dbAudit.detail=dbAuditEvent({id:'query:retained',source:'query',source_id:'retained-query',action:'execute',actor:session.username,database_id:'db',schema_id:'schema',warehouse_id:'LOCAL',saved_role:'admin',state:'UNKNOWN',created_at:'2026-11-01T01:00:00Z',updated_at:null,finished_at:null,revision:1,committed:false,has_cleanup_error:false,links:[]});
dbAudit.items=[dbAudit.detail];dbAudit.selected=dbAudit.detail.id;
dbAudit.preview={availability:'available',truncated:false,items:[{event_id:'retained-audit-preview',timestamp:'2001-02-03 04:05:06.007',agent_id:'saved-agent',action_type:'saved-action',target_system:'saved-target',status:'success',trace_id:'saved-trace',duration_ms:'9007199254740993'}]};
dbAudit.previewStamp=dbAuditStamp();dbAuditChoices(podAuditItems);dbAuditValidate();assert.equal(dbAuditReason(),'');assert.equal(dbAuditTelemetryAllowed(),true);
dbAuditRenderRows();dbAuditRenderDetail(dbAudit.detail);dbAuditRenderTelemetry();
const podAuditFields=['preview','previewStamp','draft','applied','cursors','detail','role','scope','database','items','selected','cursor','next','proof','capabilities'];
const podAuditSnapshot=Object.fromEntries(podAuditFields.map(key=>[key,dbAudit[key]])),podAuditData=JSON.stringify(podAuditSnapshot),podAuditStart=calls.length;
const podAuditDOM=['db-audit-preview','db-audit-rows','db-audit-detail'].map(id=>$(id).children[0]);assert.ok(podAuditDOM.every(Boolean));assert.ok(visibleText($('db-audit-preview')).includes('retained-audit-preview'));
await podFetch('policy');dbControls();await podFetch('compute');dbControls();
for(const key of podAuditFields)assert.equal(dbAudit[key],podAuditSnapshot[key]);assert.equal(JSON.stringify(Object.fromEntries(podAuditFields.map(key=>[key,dbAudit[key]]))),podAuditData);
for(const [index,id] of ['db-audit-preview','db-audit-rows','db-audit-detail'].entries())assert.equal($(id).children[0],podAuditDOM[index]);
assert.equal(calls.slice(podAuditStart).filter(c=>c.path.startsWith('/api/v1/audit/')||/^\/api\/(pawprints|history|query|load)/.test(c.path)).length,0);
""")


    def test_mounts_policy_and_compute_only_manual_independent_no_database_grants(self):
        self.javascript(r"""
for(const kind of podKinds)assert.equal($(podRoot(kind)),null);
session.platform_admin=true;inventory=[];schemas=[];worksheets=[];worksheetId='';dbPrincipal=JSON.stringify([session.username,session.authenticated]);dbMaximumRole='admin';
dbObservationsInstall();dbRuntimeInstall();assert.equal(calls.length,0);
for(const kind of podKinds){const root=$(podRoot(kind)),id=podID(kind);assert.equal(root.parentElement,kind==='compute'?$('compute'):$('bear-panel-policy'));assert.equal(root.role,'region');assert.equal(root['aria-labelledby'],id+'title');assert.equal($(id+'fetch').disabled,true);assert.equal($(id+'auth').disabled,false);
 for(let i=0;i<3;i++){dbPodBaselineInstall();dbObservationsInstall();dbRuntimeInstall();}assert.equal($(podRoot(kind)),root);assert.equal(root.parentElement.descendants().filter(n=>n.id===root.id).length,1);
}
assert.equal($('observation-sre-pod-baseline'),null);
await $(podID('policy')+'auth').onclick();assert.deepEqual(calls.map(c=>c.path),['/api/session']);assert.equal(calls[0].options,undefined);
await podFetch();assert.equal(podCalls().length,1);assert.ok(dbPodBaseline.compute.record);assert.equal(dbPodBaseline.policy.record,null);assert.equal(worksheets.length,0);
const ids=podKinds.flatMap(kind=>$(podRoot(kind)).descendants().map(n=>n.id).filter(Boolean));assert.equal(new Set(ids).size,ids.length);
""")

    def test_closed_dto_every_key_nested_shape_and_relationship(self):
        self.javascript(r"""
await podBoot();assert.ok(Object.isFrozen(dbPodBaselineRecord(podReply()).baseline.pods[0].containers[0].checks));
const at=(r,path)=>path.reduce((o,k)=>o[k],r),paths=[[],['coverage'],['baseline'],['baseline','pods',0],['baseline','pods',0,'containers',0],['baseline','pods',0,'containers',0,'checks']];
for(const path of paths){for(const key of Object.keys(at(podReply(),path))){const r=podReply();delete at(r,path)[key];assert.throws(()=>dbPodBaselineRecord(r));}const r=podReply();at(r,path).raw='secret';assert.throws(()=>dbPodBaselineRecord(r));}
for(const mutate of [r=>r.scope='database',r=>r.warehouse='OTHER',r=>r.selected_role='bi_viewer',r=>r.observed_at='2026-02-30T00:00:00Z',r=>r.baseline_version='old',r=>r.coverage.kind='pod_template',r=>r.coverage.pod_spec=1,r=>r.coverage.per_container=false,...['runtime_security','admission_decision','global_policy'].map(k=>r=>r.coverage[k]=true),r=>r.baseline.namespace='',r=>r.baseline.namespace='other.zone',r=>r.baseline.namespace='UPPER',r=>r.baseline.namespace='x'.repeat(64),r=>r.baseline.observed_at='bad',r=>r.baseline.pod_count=1,r=>r.baseline.container_count=5,r=>r.baseline.pod_count=true,r=>r.baseline.container_count=6.1,r=>r.baseline.pods[1].name=r.baseline.pods[0].name,r=>r.baseline.pods[0].name='<img src=x>',r=>r.baseline.pods[0].containers[0].name='bad.name',r=>r.baseline.pods[0].containers[1].name='main',r=>r.baseline.pods[0].containers[0].ordinal=2,r=>r.baseline.pods[0].containers[0].ordinal=true,r=>r.baseline.pods[0].containers[0].kind='init',r=>r.baseline.pods[0].containers.reverse(),r=>r.baseline.pods[0].containers[0].checks.non_root='allow',r=>r.baseline.pods[0].containers=[],r=>r.baseline.pods[0].containers=[podContainer('init_container')]]){const r=podReply();mutate(r);assert.throws(()=>dbPodBaselineRecord(r));}
for(const value of [null,[],true,'raw'])assert.throws(()=>dbPodBaselineRecord(value));
for(const key of ['namespace','pod_count','container_count','pods']){const r=podUnavailable();r.baseline[key]=podReply().baseline[key];assert.throws(()=>dbPodBaselineRecord(r));}
""")

    def test_empty_outage_and_multiple_pods_distinct_known_not_met(self):
        self.javascript(r"""
await podBoot();await podFetch();let text=podText('compute');for(const v of ['research-zone','z-unlabelled','a-terminating','main','setup','debug'])assert.ok(text.includes(v));
for(const key of ['Container','Init container','Ephemeral container','Declaration matches','Declaration missing or mismatched'])assert.ok(text.includes(dbPodBaselineText(key)));
const empty=podReply();Object.assign(empty.baseline,{pods:[],pod_count:0,container_count:0});await podFetch('compute',empty);assert.ok(podText('compute').includes(dbPodBaselineText('No Pods returned by the source. This does not establish an aggregate allow decision.')));assert.equal(dbPodBaseline.compute.record.baseline.pod_count,0);
await podFetch('compute',podUnavailable());assert.ok(podText('compute').includes(dbPodBaselineText('Pod source unavailable; no declarations can be verified.')));assert.ok(!podText('compute').includes('Pods: 0'));assert.equal(dbPodBaseline.compute.record.baseline.pods,null);
""")

    def test_actual_names_escape_text_and_arbitrary_source_object_key_order(self):
        self.javascript(r"""
await podBoot();const r=podReply(),c=r.baseline.pods[0].containers[0];c.checks={cpu_memory_limits:'not_met',drop_all_capabilities:'met',non_root:'not_met',read_only_root_filesystem:'met'};
await podFetch('compute',r);const cells=$(podID('compute')+'output').descendants().filter(n=>n.tagName==='TD').slice(1,5);assert.deepEqual(cells.map(n=>n.textContent),['met','not_met','met','not_met'].map(v=>dbPodBaselineText(v==='met'?'Declaration matches':'Declaration missing or mismatched')));
// Render remains text-safe even if an internal caller bypasses the strict response validator.
const unsafe=podReply();unsafe.baseline.pods[0].name='<img src=x>';unsafe.baseline.pods[0].containers[0].name='<script>secret</script>';dbPodBaseline.compute.record=unsafe;dbPodBaselineSync();
assert.ok(podText('compute').includes('<img src=x>'));assert.ok(podText('compute').includes('<script>secret</script>'));assert.equal($(podRoot('compute')).descendants().filter(n=>['IMG','SCRIPT'].includes(n.tagName)).length,0);
""")

    def test_disclosure_focus_same_cache_dom_locale_preserved_new_receipt_resets(self):
        self.javascript(r"""
await podBoot();for(const kind of podKinds)await podFetch(kind);
for(const kind of podKinds){const output=$(podID(kind)+'output'),record=dbPodBaseline[kind].record,details=output.querySelectorAll('details'),second=details[1],summary=second.querySelector('summary');second.open=true;summary.focus();const count=calls.length;
for(let i=0;i<3;i++)dbControls();assert.equal(output.querySelectorAll('details')[1],second);assert.equal(document.activeElement,summary);
for(const locale of ['ja','en']){setLanguage(locale);dbControls();const rows=output.querySelectorAll('details');assert.equal(!!rows[0].open,false);assert.equal(rows[1].open,true);assert.equal(document.activeElement,rows[1].querySelector('summary'));assert.equal(dbPodBaseline[kind].record,record);const same=rows[1];dbControls();assert.equal(output.querySelectorAll('details')[1],same);}
assert.equal(calls.length,count);await podFetch(kind);assert.notEqual(dbPodBaseline[kind].record,record);assert.ok(output.querySelectorAll('details').every(d=>!d.open));}
dbAdminSession({...session,platform_admin:false});podEmpty();for(const kind of podKinds)assert.equal($(podID(kind)+'output').querySelectorAll('details').length,0);
""")

    def test_real_policy_navigation_no_sre_panel_preserves_receipts(self):
        self.navigation_javascript(r"""
await podBoot();$('tab-compute').click();await podFetch();const held=dbPodBaseline.compute.record,count=calls.length;
for(const locale of ['ja','en']){setLanguage(locale);$('tab-bears').click();$('bear-tab-policy').click();scope('policy');assert.equal($(podRoot('policy')).hidden,false);assert.equal($('observation-policy-database-controls').hidden,true);scope('policy','database');assert.equal($(podRoot('policy')).hidden,true);$('bear-tab-sre').click();scope('sre');assert.equal($('observation-sre-pod-baseline'),null);$('tab-compute').click();assert.equal(dbPodBaseline.compute.record,held);assert.equal(calls.length,count);}
""")

    def test_bilingual_native_controls_and_specific_responsive_styles(self):
        self.javascript(r"""
await podBoot();for(const kind of podKinds)await podFetch(kind);const n=calls.length,ja=JSON.stringify(i18n.ja);
for(const locale of ['ja','en']){setLanguage(locale);dbControls();for(const kind of podKinds){const root=$(podRoot(kind)),id=podID(kind);for(const suffix of ['auth','fetch']){assert.equal($(id+suffix).tagName,'BUTTON');$(id+suffix).focus();assert.equal(document.activeElement,$(id+suffix));}assert.equal($(id+'output')['aria-live'],'polite');assert.ok(root.descendants().filter(n=>n.tagName==='TH').every(n=>n.scope==='col'));for(const label of ['API snapshot creation time','Source snapshot creation time','Read-only root filesystem','Non-root execution','Drop ALL capabilities','Positive CPU and memory limits','Init container','Ephemeral container']){assert.ok(podText(kind).includes(dbPodBaselineText(label)));if(locale==='ja')assert.notEqual(dbPodBaselineText(label),label);}assert.equal(root.descendants().filter(n=>['IMG','SCRIPT','INPUT','SELECT','TEXTAREA','PRE'].includes(n.tagName)).length,0);}}
assert.equal(calls.length,n);assert.equal(JSON.stringify(i18n.ja),ja);
""")
        html=resources_ui.compute_ui.platform_ui.observations.harness.HTML
        self.assertIn(':is(#observation-policy-pod-baseline,#observation-pod-baseline-panel) .panel-body p{max-inline-size:min(75ch,100%)}',html)
        self.assertIn('table-layout:fixed;width:100%;min-width:0',html)
        self.assertIn('white-space:normal;overflow-wrap:anywhere',html)

    def test_wire_and_dto_caps_no_partial_list(self):
        self.javascript(r"""
await podBoot();const raw=JSON.stringify(podReply()),bytes=new TextEncoder().encode(raw).length;
await podFetch('compute',{rawBody:raw+' '.repeat(65536-bytes)});assert.ok(dbPodBaseline.compute.record);
await podFetch('compute',{rawBody:raw+' '.repeat(65537-bytes)});assert.equal(dbPodBaseline.compute.record,null);
for(const size of [64,65]){const r=podReply();r.baseline.pods=Array.from({length:size},(_,i)=>({name:'p-'+i,containers:[podContainer('container',1,'met','c')]}));r.baseline.pod_count=size;r.baseline.container_count=size;if(size===64)assert.ok(dbPodBaselineRecord(r));else assert.throws(()=>dbPodBaselineRecord(r));}
for(const size of [256,257]){const r=podReply();r.baseline.pods=[{name:'one',containers:Array.from({length:size},(_,i)=>podContainer('container',i+1,'met','c-'+i))}];r.baseline.pod_count=1;r.baseline.container_count=size;if(size===256)assert.ok(dbPodBaselineRecord(r));else assert.throws(()=>dbPodBaselineRecord(r));}
""")


if __name__ == '__main__':
    unittest.main()
