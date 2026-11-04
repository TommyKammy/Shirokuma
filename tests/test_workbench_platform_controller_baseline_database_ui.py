"""Controller template baseline handlers through the existing DOM/Node harness; no runtime IO."""
import unittest
from unittest.mock import patch
import test_workbench_platform_foundation_baseline_database_ui as foundation_ui

resources_ui = foundation_ui.resources_ui
CONTROLLERS = r"""
const controllerQuerySelectorAll=Node.prototype.querySelectorAll;
Node.prototype.querySelectorAll=function(selector){if(selector==='details')return this.descendants().filter(n=>n.tagName==='DETAILS');return controllerQuerySelectorAll.call(this,selector);};
const controllerKinds=['policy','compute'];
const controllerRoot=kind=>kind==='policy'?'observation-policy-controller-baseline':'observation-controller-baseline-panel';
const controllerID=kind=>'observation-'+kind+'-controller-baseline-';
const controllerCalls=()=>calls.filter(c=>c.path==='/api/v1/bears/platform-controller-baseline');
const controllerText=kind=>visibleText($(controllerID(kind)+'output'));
const controllerContainer=(kind='container',ordinal=1,state='met',name=kind+'-'+ordinal)=>({name,kind,ordinal,checks:{read_only_root_filesystem:state,non_root:state,drop_all_capabilities:state,cpu_memory_limits:state}});
const controllerReply=()=>({scope:'platform',warehouse:'LOCAL',selected_role:'admin',observed_at:'2026-11-03T04:00:00.123456Z',baseline_version:'namespace-controller-template-declarations-v1',coverage:{kind:'namespace_controller_template_declarations',controller_template:true,per_container:true,runtime_security:false,admission_decision:false,global_policy:false},baseline:{availability:'available',code:null,observed_at:'2026-11-03T03:59:59Z',namespace:'research-zone',controller_count:2,container_count:4,controllers:['z-unlabelled','a-terminating'].map((name,i)=>({kind:i?'CronJob':'DaemonSet',name,containers:[controllerContainer('container',1,'met','main'),controllerContainer('init_container',1,'not_met','setup')]}))}});
const controllerUnavailable=()=>{const r=controllerReply();Object.assign(r.baseline,{availability:'unavailable',code:'CONTROLLER_BASELINE_UNAVAILABLE',namespace:null,controller_count:null,container_count:null,controllers:null});return r;};
async function controllerFetch(kind='compute',reply=controllerReply()){
 route=async(path,options)=>{if(path==='/api/v1/bears/platform-controller-baseline'){assert.deepEqual(options,{headers:{'X-Shirokuma-Role':'admin'}});return reply;}return runtimeFixtureRoute(path,options);};
 await $(controllerID(kind)+'fetch').onclick();
}
async function controllerBoot(){session.platform_admin=true;await boot();}
function controllerEmpty(){for(const kind of controllerKinds){assert.equal(dbControllerBaseline[kind].record,null);assert.ok(!controllerText(kind).includes('research-zone'));assert.ok(!controllerText(kind).includes('z-unlabelled'));}}
"""

class PlatformControllerBaselineDatabaseUI(unittest.TestCase):
    def javascript(self, body):
        harness=resources_ui.compute_ui.platform_ui.observations.harness
        run=harness.subprocess.run
        def controller_adapter(args, **kwargs):
            if list(args)==['node','-']:
                source=kwargs['input'].replace('const $=id=>',
                    "const $=id=>/^(?:observation-policy-controller-baseline|observation-controller-baseline-panel|observation-sre-controller-baseline)$/.test(id)&&!nodes[id]?.parentElement?null:",1)
                source=source.replace('if(data?.malformed)',
                    'if(data?.controllerResponse)return data.controllerResponse;\n if(data?.malformed)',1)
                kwargs['input']=source
            return run(args,**kwargs)
        with patch.object(harness.subprocess,'run',controller_adapter):
            foundation_ui.PlatformFoundationBaselineDatabaseUI.javascript(self,CONTROLLERS+body)

    def navigation_javascript(self, body):
        foundation_ui.PlatformFoundationBaselineDatabaseUI.navigation_javascript(self,body)

    def test_per_panel_receipts_busy_and_fetches_are_independent(self):
        self.javascript(r"""
await controllerBoot();await controllerFetch('policy');const held=dbControllerBaseline.policy.record;
const wait=deferred();route=()=>wait.promise;const pending=dbControllerBaselineFetch('policy');await tick();const busy=dbControllerBaseline.policy.busy;
const other=controllerReply();other.observed_at='2026-11-03T05:00:00.000Z';await controllerFetch('compute',other);assert.equal(dbControllerBaseline.policy.record,held);assert.equal(dbControllerBaseline.policy.busy,busy);assert.equal(dbControllerBaseline.compute.busy,null);assert.equal(dbControllerBaseline.compute.record.observed_at,other.observed_at);
const compute=dbControllerBaseline.compute.record;wait.resolve(controllerReply());await pending;assert.equal(dbControllerBaseline.compute.record,compute);assert.notEqual(dbControllerBaseline.policy.record,compute);
await controllerFetch('policy',controllerUnavailable());assert.equal(dbControllerBaseline.compute.record,compute);assert.equal(dbControllerBaseline.policy.record.baseline.availability,'unavailable');
""")

    def test_authority_truth_roles_maximum_principal_no_io_and_global_purge(self):
        self.javascript(r"""
await controllerBoot();for(const kind of controllerKinds)await controllerFetch(kind);const controllerSnapshot=dbControllerBaseline.compute.record;
for(const change of [{platform_admin:false},{platform_admin:1},{platform_admin:'true'},{maximum_role:'data_engineer'},{authenticated:false},{username:'other'},{data_mode:'legacy'}]){
 dbAdminSession({...session,...change});controllerEmpty();const n=controllerCalls().length;for(const kind of controllerKinds)await dbControllerBaselineFetch(kind);assert.equal(controllerCalls().length,n);dbAdminSession(session);
}
for(const role of ['bi_viewer','data_engineer']){currentRole=role;dbControls();controllerEmpty();for(const kind of controllerKinds)await dbControllerBaselineFetch(kind);currentRole='admin';dbControls();}
for(const kind of controllerKinds)dbControllerBaseline[kind].record=controllerSnapshot;dbAdmin.platform=false;dbAdminSession({...session,platform_admin:false});controllerEmpty();
""")

    def test_current_denial_purges_both_before_body_even_when_both_pending(self):
        self.javascript(r"""
await controllerBoot();for(const status of [401,403]){
 dbAdminSession(session);await controllerFetch('policy');await controllerFetch('compute');const wait=deferred();route=()=>wait.promise;const old=dbControllerBaselineFetch('policy');await tick();let parsed=0;
 route=async()=>({controllerResponse:{status,ok:false,text:async()=>{parsed++;throw Error('must not parse');}}});await dbControllerBaselineFetch('compute');assert.equal(parsed,0);controllerEmpty();assert.equal(dbAdmin.platform,false);wait.resolve(controllerReply());await old;controllerEmpty();
}
""")

    def test_current_failure_clears_only_its_panel_and_sanitizes_output(self):
        self.javascript(r"""
await controllerBoot();await controllerFetch('policy');const saved=dbControllerBaseline.policy.record;
for(const response of [{ok:true,status:200,text:async()=>'{secret'},{ok:false,status:500,text:async()=>JSON.stringify(controllerReply())},{ok:true,status:200,text:async()=>JSON.stringify({...controllerReply(),raw:'secret'})}]){
 await controllerFetch('compute');route=async()=>({controllerResponse:response});await dbControllerBaselineFetch('compute');assert.equal(dbControllerBaseline.compute.record,null);assert.equal(dbControllerBaseline.policy.record,saved);assert.equal(dbAdmin.platform,true);assert.ok(!controllerText('compute').includes('secret'));
}
route=async()=>{throw Error('secret');};await dbControllerBaselineFetch('compute');assert.equal(dbControllerBaseline.policy.record,saved);
""")

    def test_navigation_scope_role_locale_aba_reject_late_success_and_denial(self):
        self.navigation_javascript(r"""
await controllerBoot();$('tab-compute').click();
for(const transition of [()=>{$('tab-warehouse').click();$('tab-compute').click();},()=>{scope('policy');scope('policy','database');},()=>{currentRole='bi_viewer';dbControls();currentRole='admin';dbControls();},()=>{setLanguage('ja');setLanguage('en');},()=>{$('tab-bears').click();$('bear-tab-policy').click();$('tab-compute').click();}]){
 for(const status of [200,401,403]){const wait=deferred();route=()=>wait.promise;const pending=dbControllerBaselineFetch('compute');await tick();transition();await controllerFetch('compute');const newer=dbControllerBaseline.compute.record;
 wait.resolve(status===200?controllerReply():{controllerResponse:{status,ok:false,text:async()=>{throw Error('late denial');}}});await pending;assert.equal(dbControllerBaseline.compute.record,newer);assert.equal(dbAdmin.platform,true);}
}
""")

    def test_post_body_await_and_authority_loss_discard_protected_reply(self):
        self.navigation_javascript(r"""
await controllerBoot();$('tab-compute').click();
for(const reject of [false,true]){const body=deferred(),entered=deferred();route=async()=>({controllerResponse:{ok:true,status:200,text:()=>{entered.resolve();return body.promise;}}});const pending=dbControllerBaselineFetch('compute');await entered.promise;$('tab-warehouse').click();$('tab-compute').click();await controllerFetch('compute');const newer=dbControllerBaseline.compute.record;if(reject)body.reject(Error('secret'));else body.resolve(JSON.stringify(controllerReply()));await pending;assert.equal(dbControllerBaseline.compute.record,newer);}
const body=deferred(),entered=deferred();route=async()=>({controllerResponse:{ok:true,status:200,text:()=>{entered.resolve();return body.promise;}}});const pending=dbControllerBaselineFetch('policy');await entered.promise;dbAdminSession({...session,platform_admin:false});body.resolve(JSON.stringify(controllerReply()));await pending;controllerEmpty();
""")

    def test_refresh_authority_is_manual_independent_and_late_denial_ignored(self):
        self.navigation_javascript(r"""
await controllerBoot();$('tab-compute').click();await controllerFetch('policy');const held=dbControllerBaseline.policy.record;
const wait=deferred();sessionRoute=()=>wait.promise;const pending=dbControllerBaselineRefreshAuth('policy');await tick();const busy=dbControllerBaseline.policy.busy;await controllerFetch('compute');assert.equal(dbControllerBaseline.policy.busy,busy);assert.equal(dbControllerBaseline.policy.record,held);
$('tab-warehouse').click();$('tab-compute').click();const saved=dbControllerBaseline.compute.record;wait.resolve({controllerResponse:{ok:false,status:403,json:async()=>{throw Error('late');}}});await pending;assert.equal(dbControllerBaseline.compute.record,saved);assert.equal(dbAdmin.platform,true);
for(const status of [401,403]){dbAdminSession(session);let parsed=0;sessionRoute=async()=>({controllerResponse:{ok:false,status,json:async()=>{parsed++;throw Error('must not parse');}}});await dbControllerBaselineRefreshAuth('compute');assert.equal(parsed,0);controllerEmpty();}
""")

    def test_preserves_contexts_drafts_unknown_trackers_and_existing_platform_caches(self):
        self.javascript(r"""
session.platform_admin=true;await dualBoot();await readyObservation('sre','bi_viewer','finance','finance-marts');await readyObservation('policy','data_engineer');await fetchObservation('sre');await fetchObservation('policy');
const sc=dbObservations.sre.context,pc=dbObservations.policy.context,sr=dbObservations.sre.record,pr=dbObservations.policy.record;
editSQL('SELECT retained worksheet');editDoctor('SELECT retained Doctor');$('copilot-request').value='retained Copilot';$('p35-wap-sql').value='SELECT retained WAP';
await computeFetch();await resourceFetch();await baselineFetch();const caches=[dbPlatform.record,dbResources.record,dbFoundationBaseline.record],busy={};dbFoundationBaseline.busy=busy;
const intents=[dbExplanation.intents,dbProposal.intents,dbCurator.intents],ops=[...dataopsPanels].map(([key,p])=>p.op),before=otherState();
await controllerFetch('policy');await controllerFetch('compute');await dbControllerBaselineRefreshAuth('compute');assert.equal(otherState(),before);assert.equal(dbObservations.sre.context,sc);assert.equal(dbObservations.policy.context,pc);assert.equal(dbObservations.sre.record,sr);assert.equal(dbObservations.policy.record,pr);assert.equal(dbFoundationBaseline.busy,busy);
for(const [i,p] of [...dataopsPanels].map(([key,p])=>p).entries())assert.equal(p.op,ops[i]);for(const [i,p] of [dbExplanation,dbProposal,dbCurator].entries())assert.equal(p.intents,intents[i]);for(const [i,p] of [dbPlatform,dbResources,dbFoundationBaseline].entries())assert.equal(p.record,caches[i]);
const snapshot=dbControllerBaseline.compute.record;dbEpoch++;dbAuthorizationVersion++;dbInventoryVersion++;dbCurrent().database_id='other';dbControls();assert.equal(dbControllerBaseline.compute.record,snapshot);
""")


    def test_mounts_policy_and_compute_only_manual_independent_no_database_grants(self):
        self.javascript(r"""
for(const kind of controllerKinds)assert.equal($(controllerRoot(kind)),null);
session.platform_admin=true;inventory=[];schemas=[];worksheets=[];worksheetId='';dbPrincipal=JSON.stringify([session.username,session.authenticated]);dbMaximumRole='admin';
dbObservationsInstall();dbRuntimeInstall();assert.equal(calls.length,0);
for(const kind of controllerKinds){const root=$(controllerRoot(kind)),id=controllerID(kind);assert.equal(root.parentElement,kind==='compute'?$('compute'):$('bear-panel-policy'));assert.equal(root.role,'region');assert.equal(root['aria-labelledby'],id+'title');assert.equal($(id+'fetch').disabled,true);assert.equal($(id+'auth').disabled,false);
 for(let i=0;i<3;i++){dbControllerBaselineInstall();dbObservationsInstall();dbRuntimeInstall();}assert.equal($(controllerRoot(kind)),root);assert.equal(root.parentElement.descendants().filter(n=>n.id===root.id).length,1);
}
assert.equal($('observation-sre-controller-baseline'),null);
await $(controllerID('policy')+'auth').onclick();assert.deepEqual(calls.map(c=>c.path),['/api/session']);assert.equal(calls[0].options,undefined);
await controllerFetch();assert.equal(controllerCalls().length,1);assert.ok(dbControllerBaseline.compute.record);assert.equal(dbControllerBaseline.policy.record,null);assert.equal(worksheets.length,0);
const ids=controllerKinds.flatMap(kind=>$(controllerRoot(kind)).descendants().map(n=>n.id).filter(Boolean));assert.equal(new Set(ids).size,ids.length);
""")

    def test_closed_dto_every_key_nested_shape_and_relationship(self):
        self.javascript(r"""
await controllerBoot();assert.ok(Object.isFrozen(dbControllerBaselineRecord(controllerReply()).baseline.controllers[0].containers[0].checks));
const at=(r,path)=>path.reduce((o,k)=>o[k],r),paths=[[],['coverage'],['baseline'],['baseline','controllers',0],['baseline','controllers',0,'containers',0],['baseline','controllers',0,'containers',0,'checks']];
for(const path of paths){for(const key of Object.keys(at(controllerReply(),path))){const r=controllerReply();delete at(r,path)[key];assert.throws(()=>dbControllerBaselineRecord(r));}const r=controllerReply();at(r,path).raw='secret';assert.throws(()=>dbControllerBaselineRecord(r));}
for(const mutate of [r=>r.scope='database',r=>r.warehouse='OTHER',r=>r.selected_role='bi_viewer',r=>r.observed_at='2026-02-30T00:00:00Z',r=>r.baseline_version='old',r=>r.coverage.kind='pod_template',r=>r.coverage.controller_template=1,r=>r.coverage.per_container=false,...['runtime_security','admission_decision','global_policy'].map(k=>r=>r.coverage[k]=true),r=>r.baseline.namespace='',r=>r.baseline.namespace='other.zone',r=>r.baseline.namespace='UPPER',r=>r.baseline.namespace='x'.repeat(64),r=>r.baseline.observed_at='bad',r=>r.baseline.controller_count=1,r=>r.baseline.container_count=5,r=>r.baseline.controller_count=true,r=>r.baseline.container_count=6.1,r=>Object.assign(r.baseline.controllers[1],{name:r.baseline.controllers[0].name,kind:r.baseline.controllers[0].kind}),r=>r.baseline.controllers[0].kind='Pod',r=>r.baseline.controllers[0].name='<img src=x>',r=>r.baseline.controllers[0].containers[0].name='bad.name',r=>r.baseline.controllers[0].containers[1].name='main',r=>r.baseline.controllers[0].containers[0].ordinal=2,r=>r.baseline.controllers[0].containers[0].ordinal=true,r=>r.baseline.controllers[0].containers[0].kind='init',r=>r.baseline.controllers[0].containers.reverse(),r=>r.baseline.controllers[0].containers[0].checks.non_root='allow',r=>r.baseline.controllers[0].containers=[],r=>r.baseline.controllers[0].containers=[controllerContainer('init_container')]]){const r=controllerReply();mutate(r);assert.throws(()=>dbControllerBaselineRecord(r));}
for(const value of [null,[],true,'raw'])assert.throws(()=>dbControllerBaselineRecord(value));
for(const key of ['namespace','controller_count','container_count','controllers']){const r=controllerUnavailable();r.baseline[key]=controllerReply().baseline[key];assert.throws(()=>dbControllerBaselineRecord(r));}
""")

    def test_empty_outage_and_multiple_controllers_distinct_known_not_met(self):
        self.javascript(r"""
await controllerBoot();await controllerFetch();let text=controllerText('compute');for(const v of ['research-zone','z-unlabelled','a-terminating','main','setup','DaemonSet','CronJob'])assert.ok(text.includes(v));
for(const key of ['Container','Init container','Declaration matches','Declaration missing or mismatched'])assert.ok(text.includes(dbControllerBaselineText(key)));
const empty=controllerReply();Object.assign(empty.baseline,{controllers:[],controller_count:0,container_count:0});await controllerFetch('compute',empty);assert.ok(controllerText('compute').includes(dbControllerBaselineText('No controllers returned by the source.')));assert.equal(dbControllerBaseline.compute.record.baseline.controller_count,0);
await controllerFetch('compute',controllerUnavailable());assert.ok(controllerText('compute').includes(dbControllerBaselineText('Controller source unavailable; no template declarations can be verified.')));assert.ok(!controllerText('compute').includes('Controllers: 0'));assert.equal(dbControllerBaseline.compute.record.baseline.controllers,null);
""")


    def test_disclosure_focus_same_cache_dom_locale_preserved_new_receipt_resets(self):
        self.javascript(r"""
await controllerBoot();for(const kind of controllerKinds)await controllerFetch(kind);
for(const kind of controllerKinds){const output=$(controllerID(kind)+'output'),record=dbControllerBaseline[kind].record,details=output.querySelectorAll('details'),second=details[1],summary=second.querySelector('summary');second.open=true;summary.focus();const count=calls.length;
for(let i=0;i<3;i++)dbControls();assert.equal(output.querySelectorAll('details')[1],second);assert.equal(document.activeElement,summary);
for(const locale of ['ja','en']){setLanguage(locale);dbControls();const rows=output.querySelectorAll('details');assert.equal(!!rows[0].open,false);assert.equal(rows[1].open,true);assert.equal(document.activeElement,rows[1].querySelector('summary'));assert.equal(dbControllerBaseline[kind].record,record);const same=rows[1];dbControls();assert.equal(output.querySelectorAll('details')[1],same);}
assert.equal(calls.length,count);await controllerFetch(kind);assert.notEqual(dbControllerBaseline[kind].record,record);assert.ok(output.querySelectorAll('details').every(d=>!d.open));}
dbAdminSession({...session,platform_admin:false});controllerEmpty();for(const kind of controllerKinds)assert.equal($(controllerID(kind)+'output').querySelectorAll('details').length,0);
""")

    def test_real_policy_navigation_no_sre_panel_preserves_receipts(self):
        self.navigation_javascript(r"""
await controllerBoot();$('tab-compute').click();await controllerFetch();const held=dbControllerBaseline.compute.record,count=calls.length;
for(const locale of ['ja','en']){setLanguage(locale);$('tab-bears').click();$('bear-tab-policy').click();scope('policy');assert.equal($(controllerRoot('policy')).hidden,false);assert.equal($('observation-policy-database-controls').hidden,true);scope('policy','database');assert.equal($(controllerRoot('policy')).hidden,true);$('bear-tab-sre').click();scope('sre');assert.equal($('observation-sre-controller-baseline'),null);$('tab-compute').click();assert.equal(dbControllerBaseline.compute.record,held);assert.equal(calls.length,count);}
""")

    def test_bilingual_native_controls_and_specific_responsive_styles(self):
        self.javascript(r"""
await controllerBoot();for(const kind of controllerKinds)await controllerFetch(kind);const n=calls.length,ja=JSON.stringify(i18n.ja);
for(const locale of ['ja','en']){setLanguage(locale);dbControls();for(const kind of controllerKinds){const root=$(controllerRoot(kind)),id=controllerID(kind);for(const suffix of ['auth','fetch']){assert.equal($(id+suffix).tagName,'BUTTON');$(id+suffix).focus();assert.equal(document.activeElement,$(id+suffix));}assert.equal($(id+'output')['aria-live'],'polite');assert.ok(root.descendants().filter(n=>n.tagName==='TH').every(n=>n.scope==='col'));for(const label of ['API snapshot creation time','Source snapshot creation time','Read-only root filesystem','Non-root execution','Drop ALL capabilities','Positive CPU and memory limits','Init container']){assert.ok(controllerText(kind).includes(dbControllerBaselineText(label)));if(locale==='ja')assert.notEqual(dbControllerBaselineText(label),label);}assert.equal(root.descendants().filter(n=>['IMG','SCRIPT','INPUT','SELECT','TEXTAREA','PRE'].includes(n.tagName)).length,0);}}
assert.equal(calls.length,n);assert.equal(JSON.stringify(i18n.ja),ja);
""")
        html=resources_ui.compute_ui.platform_ui.observations.harness.HTML
        self.assertIn(':is(#observation-policy-controller-baseline,#observation-controller-baseline-panel) .panel-body p{max-inline-size:min(75ch,100%)}',html)
        self.assertIn('table-layout:fixed;width:100%;min-width:0',html)
        self.assertIn('white-space:normal;overflow-wrap:anywhere',html)

    def test_wire_and_dto_caps_no_partial_list(self):
        self.javascript(r"""
await controllerBoot();const raw=JSON.stringify(controllerReply()),bytes=new TextEncoder().encode(raw).length;
await controllerFetch('compute',{rawBody:raw+' '.repeat(65536-bytes)});assert.ok(dbControllerBaseline.compute.record);
await controllerFetch('compute',{rawBody:raw+' '.repeat(65537-bytes)});assert.equal(dbControllerBaseline.compute.record,null);
for(const size of [64,65]){const r=controllerReply();r.baseline.controllers=Array.from({length:size},(_,i)=>({kind:'Job',name:'p-'+i,containers:[controllerContainer('container',1,'met','c')]}));r.baseline.controller_count=size;r.baseline.container_count=size;if(size===64)assert.ok(dbControllerBaselineRecord(r));else assert.throws(()=>dbControllerBaselineRecord(r));}
for(const size of [256,257]){const r=controllerReply();r.baseline.controllers=[{kind:'Deployment',name:'one',containers:Array.from({length:size},(_,i)=>controllerContainer('container',i+1,'met','c-'+i))}];r.baseline.controller_count=1;r.baseline.container_count=size;if(size===256)assert.ok(dbControllerBaselineRecord(r));else assert.throws(()=>dbControllerBaselineRecord(r));}
""")


if __name__ == '__main__':
    unittest.main()
