"""Manual controller records through the existing production-handler stdin harness.

Authored for host Node execution; no live source, sockets or runtime calls.
"""
import json
import unittest
from unittest.mock import patch
import test_workbench_platform_foundation_baseline_database_ui as foundation_ui

resources_ui = foundation_ui.resources_ui
CONTROLLERS = r"""
const controllerIDs=['observation-sre-foundation-controllers-','observation-foundation-controllers-'];
const controllerCalls=()=>calls.filter(c=>c.path==='/api/v1/bears/platform-foundation-controllers');
const controllerText=(id=controllerIDs[1])=>visibleText($(id+'output'));
const controllerReply=()=>({scope:'platform',warehouse:'LOCAL',selected_role:'admin',observed_at:'2026-11-02T04:00:00.123456Z',controller_version:'foundation-storage-catalog-controllers-v1',coverage:{kind:'deployment_controller_counts',managed_foundation:true,service_connectivity:false,sql_health:false,policy_decision:false},controllers:{availability:'available',code:null,observed_at:'2026-11-02T03:59:59Z',deployments:[{component:'storage',generation:5,observed_generation:null,desired_replicas:0,replicas:null,updated_replicas:0,ready_replicas:3,available_replicas:4,unavailable_replicas:7},{component:'catalog_database',generation:5,observed_generation:0,desired_replicas:1,replicas:2,updated_replicas:3,ready_replicas:4,available_replicas:5,unavailable_replicas:6},{component:'catalog',generation:5,observed_generation:5,desired_replicas:1,replicas:1,updated_replicas:1,ready_replicas:1,available_replicas:1,unavailable_replicas:0}]}});
const controllerUnavailable=()=>{const r=controllerReply();r.controllers={availability:'unavailable',code:'FOUNDATION_CONTROLLERS_UNAVAILABLE',observed_at:r.controllers.observed_at,deployments:null};return r;};
async function controllerFetch(reply=controllerReply(),id=controllerIDs[1]){
 route=async(path,options)=>{if(path==='/api/v1/bears/platform-foundation-controllers'){assert.deepEqual(options,{headers:{'X-Shirokuma-Role':'admin'}});return reply;}return runtimeFixtureRoute(path,options);};
 await $(id+'fetch').onclick();
}
async function controllerBoot(){session.platform_admin=true;await boot();}
function allControllersEmpty(){assert.equal(dbFoundationControllers.record,null);for(const id of controllerIDs){assert.ok(!controllerText(id).includes('2026-11-02T04:00'));assert.ok(!controllerText(id).includes(dbFoundationControllersText('Controller observed generation matches Deployment generation in captured record')));}}
"""


class PlatformFoundationControllersDatabaseUI(unittest.TestCase):
    def javascript(self, body):
        harness = resources_ui.compute_ui.platform_ui.observations.harness
        run = harness.subprocess.run
        def controller_adapter(args, **kwargs):
            if list(args) == ['node', '-']:
                source=kwargs['input'].replace('const $=id=>',
                    "const $=id=>/^observation-(?:foundation-controllers|sre-foundation-controllers|policy-foundation-controllers)-panel$/.test(id)&&!nodes[id]?.parentElement?null:",1)
                source=source.replace('if(data?.malformed)',
                    'if(data?.controllerResponse)return data.controllerResponse;\n if(data?.malformed)',1)
                kwargs['input']=source
            return run(args, **kwargs)
        with patch.object(harness.subprocess,'run',controller_adapter):
            foundation_ui.PlatformFoundationBaselineDatabaseUI.javascript(self,CONTROLLERS+body)

    def navigation_javascript(self, body):
        foundation_ui.PlatformFoundationBaselineDatabaseUI.navigation_javascript(self,body)

    def test_two_actual_mounts_policy_absent_empty_database_manual_session_only(self):
        self.javascript(r"""
for(const id of controllerIDs)assert.equal($(id+'panel'),null);
session.platform_admin=true;inventory=[];schemas=[];worksheets=[];worksheetId='';dbPrincipal=JSON.stringify([session.username,session.authenticated]);dbMaximumRole='admin';
dbObservationsInstall();dbRuntimeInstall();assert.equal(calls.length,0);
for(const [i,id] of controllerIDs.entries()){
 const panel=$(id+'panel');assert.equal(panel.role,'region');assert.equal(panel['aria-labelledby'],id+'title');assert.equal(panel.parentElement,i===1?$('compute'):$('bear-panel-sre'));
 assert.equal($(id+'fetch').disabled,true);assert.equal($(id+'auth').disabled,false);
 for(let n=0;n<3;n++){dbFoundationControllersInstall();dbRuntimeInstall();dbObservationsInstall();}assert.equal($(id+'panel'),panel);assert.equal(panel.parentElement.descendants().filter(n=>n.id===panel.id).length,1);
}
assert.equal($('observation-policy-foundation-controllers-panel'),null);
await $(controllerIDs[0]+'auth').onclick();assert.deepEqual(calls.map(c=>c.path),['/api/session']);
await controllerFetch();assert.equal(controllerCalls().length,1);assert.ok(dbFoundationControllers.record);assert.equal(dbPlatform.record,null);assert.equal(dbResources.record,null);assert.equal(worksheets.length,0);assert.equal(dbObservations.sre.context,null);
const allIDs=controllerIDs.flatMap(id=>$(id+'panel').descendants().map(n=>n.id).filter(Boolean));assert.equal(new Set(allIDs).size,allIDs.length);
""")

    def test_exact_closed_dto_every_key_canonical_order_version_and_types(self):
        self.javascript(r"""
await controllerBoot();const snapshot=dbFoundationControllersRecord(controllerReply());assert.ok(Object.isFrozen(snapshot.controllers.deployments[0]));
const at=(r,path)=>path.reduce((o,k)=>o[k],r);
for(const path of [[],['coverage'],['controllers'],['controllers','deployments',0]]){
 for(const key of Object.keys(at(controllerReply(),path))){const r=controllerReply();delete at(r,path)[key];assert.throws(()=>dbFoundationControllersRecord(r));}
 const r=controllerReply();at(r,path).private='secret';assert.throws(()=>dbFoundationControllersRecord(r));
}
for(const mutate of [r=>r.scope='database',r=>r.warehouse='OTHER',r=>r.selected_role='bi_viewer',r=>r.controller_version='v2',r=>r.observed_at='2026-02-30T00:00:00Z',r=>r.controllers.observed_at=null,r=>r.coverage.kind='health',r=>r.coverage.managed_foundation=1,r=>r.coverage.service_connectivity=true,r=>r.coverage.sql_health=true,r=>r.coverage.policy_decision=true,r=>r.controllers.availability='partial',r=>r.controllers.code='raw',r=>r.controllers.deployments.reverse(),r=>r.controllers.deployments.pop(),r=>r.controllers.deployments.push(structuredClone(r.controllers.deployments[0])),r=>r.controllers.deployments[1].component='storage']){const r=controllerReply();mutate(r);assert.throws(()=>dbFoundationControllersRecord(r));}
for(const field of dbFoundationControllersFields)for(const value of [true,false,-1,-0,1.5,'1',{},[],9007199254740992]){const r=controllerReply();r.controllers.deployments[0][field]=value;assert.throws(()=>dbFoundationControllersRecord(r));}
for(const field of ['generation','desired_replicas']){const r=controllerReply();r.controllers.deployments[0][field]=null;assert.throws(()=>dbFoundationControllersRecord(r));}
for(const mutate of [r=>r.controllers.deployments[0].generation=0,r=>r.controllers.deployments[0].observed_generation=6]){const r=controllerReply();mutate(r);assert.throws(()=>dbFoundationControllersRecord(r));}
for(const bad of [null,[],false,'raw'])assert.throws(()=>dbFoundationControllersRecord(bad));
""")

    def test_null_zero_generations_and_all_count_relationships_retained(self):
        self.javascript(r"""
await controllerBoot();for(const observed of [null,0,1,4,5]){
 const r=controllerReply();Object.assign(r.controllers.deployments[0],{generation:5,observed_generation:observed,desired_replicas:0,replicas:2,updated_replicas:9,ready_replicas:4,available_replicas:8,unavailable_replicas:7});await controllerFetch(r);assert.deepEqual(dbFoundationControllers.record,r);
}
for(const value of [null,0,9007199254740991]){const r=controllerReply();for(const field of ['observed_generation','replicas','updated_replicas','ready_replicas','available_replicas','unavailable_replicas'])r.controllers.deployments[0][field]=value;r.controllers.deployments[0].generation=9007199254740991;await controllerFetch(r);assert.deepEqual(dbFoundationControllers.record,r);}
await controllerFetch();await controllerFetch(controllerUnavailable());assert.equal(dbFoundationControllers.record.controllers.deployments,null);
for(const id of controllerIDs)assert.ok(controllerText(id).includes(dbFoundationControllersText('Foundation controller source unavailable; all three records are withheld.')));
for(const mutate of [r=>r.controllers.code=null,r=>r.controllers.deployments=[],r=>r.controllers.deployments=controllerReply().controllers.deployments]){const r=controllerUnavailable();mutate(r);assert.throws(()=>dbFoundationControllersRecord(r));}
""")

    def test_scoped_bilingual_labels_capture_times_not_verdict_and_semantic_controls(self):
        self.javascript(r"""
await controllerBoot();const globalJA=JSON.stringify(i18n.ja);await controllerFetch();const saved=dbFoundationControllers.record,count=calls.length;
for(const locale of ['ja','en']){setLanguage(locale);dbControls();for(const id of controllerIDs){const panel=$(id+'panel'),text=controllerText(id),local=dbFoundationControllersText;
 assert.equal($(id+'title').textContent,local('Foundation Deployment controller records'));assert.ok(text.includes(saved.observed_at));assert.ok(text.includes(saved.controllers.observed_at));
 for(const label of ['API capture time','Controller record capture time','Not reported (zero may be omitted)','Controller observed generation unreported in captured record','Controller observed generation behind Deployment generation in captured record','Controller observed generation matches Deployment generation in captured record'])assert.ok(text.includes(local(label)));
 const expected=locale==='ja'?{
  fields:['Deployment の世代','controller の報告世代'],
  relations:['取得記録の controller 報告世代は未報告','取得記録の controller 報告世代は Deployment の世代より前','取得記録の controller 報告世代は Deployment の世代と一致'],
  missing:'未報告（0省略の場合あり）'
 }:{
  fields:['Deployment generation','Controller observed generation'],
  relations:['Controller observed generation unreported in captured record','Controller observed generation behind Deployment generation in captured record','Controller observed generation matches Deployment generation in captured record'],
  missing:'Not reported (zero may be omitted)'
 };
 assert.equal(local('Deployment generation'),expected.fields[0]);assert.equal(local('Controller observed generation'),expected.fields[1]);
 const outputChildren=Array.from($(id+'output').children),lists=$(id+'output').descendants().filter(n=>n.tagName==='DL');
 assert.equal(saved.controllers.deployments[0].observed_generation,null);assert.equal(saved.controllers.deployments[0].generation,5);
 for(const [index,list] of lists.entries()){
  assert.equal(list.children[0].children[0].textContent,expected.fields[0]);assert.equal(list.children[1].children[0].textContent,expected.fields[1]);
  assert.equal(list.children[0].children[1].textContent,'5');
  assert.equal(list.children[1].children[1].textContent,[expected.missing,'0','5'][index]);
  assert.equal(outputChildren[outputChildren.indexOf(list)-1].textContent,expected.relations[index]);
 }
 assert.doesNotMatch(text,/Generation unreported in captured record|取得記録の世代は未報告|foundation revision|基盤リビジョン|健全|正常|収束/);
 const values=$(id+'output').descendants().filter(n=>n.tagName==='DD').map(n=>n.textContent);assert.ok(values.includes('0'));assert.ok(values.includes('7'));assert.ok(values.includes(local('Not reported (zero may be omitted)')));
 assert.doesNotMatch(text,/healthy|converged|overall ready|success|FOUNDATION_CONTROLLERS_UNAVAILABLE|deployment_controller_counts/i);
 assert.equal($(id+'fetch').tagName,'BUTTON');$(id+'fetch').focus();assert.equal(document.activeElement,$(id+'fetch'));assert.equal(panel.descendants().filter(n=>['INPUT','SELECT','TEXTAREA','PRE','IMG','SCRIPT'].includes(n.tagName)).length,0);
 assert.equal($(id+'output').descendants().filter(n=>n.tagName==='DL').length,3);
}assert.equal(calls.length,count);assert.equal(dbFoundationControllers.record,saved);}
assert.equal(JSON.stringify(i18n.ja),globalJA);
""")

    def test_real_navigation_sre_only_shared_capture_no_auto_get(self):
        self.navigation_javascript(r"""
session.platform_admin=true;await dualBoot();$('tab-compute').click();await controllerFetch();const saved=dbFoundationControllers.record,count=calls.length;
for(const locale of ['ja','en','ja','en']){setLanguage(locale);$('tab-bears').click();$('bear-tab-sre').click();scope('sre');assert.equal($('observation-sre-foundation-controllers-panel').hidden,false);assert.ok(controllerText(controllerIDs[0]).includes(saved.observed_at));scope('sre','database');assert.equal($('observation-sre-foundation-controllers-panel').hidden,true);$('bear-tab-policy').click();scope('policy');assert.equal($('observation-policy-foundation-controllers-panel'),null);$('tab-warehouse').click();$('tab-compute').click();assert.equal(dbFoundationControllers.record,saved);assert.equal(calls.length,count);}
await dbFoundationControllersRefreshAuth();assert.equal(controllerCalls().length,1);assert.equal(dbFoundationControllers.record,saved);
""")


    def test_authority_truth_principal_global_roles_and_direct_handlers_no_source(self):
            self.javascript(r"""
    await boot();for(const platform of [false,'true',1,null,undefined]){session.platform_admin=platform;dbAdminSession(session);await dbFoundationControllersRefreshAuth();await dbFoundationControllersFetch();allControllersEmpty();}
    session.platform_admin=true;dbAdminSession(session);for(const role of ['bi_viewer','data_engineer']){currentRole=role;dbControls();await dbFoundationControllersRefreshAuth();await dbFoundationControllersFetch();allControllersEmpty();}
    currentRole='admin';for(const maximum of ['bi_viewer','data_engineer']){session.maximum_role=maximum;dbUpdateMaximumRole(maximum);dbAdminSession(session);await dbFoundationControllersRefreshAuth();await dbFoundationControllersFetch();allControllersEmpty();}
    session.maximum_role='admin';dbUpdateMaximumRole('admin');for(const change of [{authenticated:false},{username:'other'},{data_mode:'legacy'}]){dbAdminSession({...session,...change});await dbFoundationControllersFetch();allControllersEmpty();}assert.equal(controllerCalls().length,0);
    """)

    def test_hidden_outputs_purge_when_other_platform_caches_empty_and_p_already_false(self):
            self.javascript(r"""
    await controllerBoot();scope('sre');scope('policy');await controllerFetch();const baselineSnapshot=dbFoundationControllers.record;scope('sre','database');scope('policy','database');activeTab='warehouse';dbControls();assert.equal(dbPlatform.record,null);assert.equal(dbResources.record,null);assert.ok(baselineSnapshot);
    dbAdmin.platform=false;dbAdminSession({...session,platform_admin:false});allControllersEmpty();for(const id of controllerIDs)assert.equal($(id+'fetch').disabled,true);
    dbFoundationControllers.record=baselineSnapshot;for(const id of controllerIDs)$(id+'output').append(Object.assign(new Node(),{textContent:baselineSnapshot.observed_at}));dbAdminSession({...session,platform_admin:false});allControllersEmpty();
    """)

    def test_current_401_403_revoke_before_body_parse_even_with_broken_body(self):
            self.javascript(r"""
    await controllerBoot();for(const status of [401,403]){dbAdminSession(session);await controllerFetch();let parsed=0;route=async()=>({controllerResponse:{status,ok:false,text:async()=>{parsed++;throw Error('secret body');},json:async()=>{parsed++;throw Error('secret body');}}});await dbFoundationControllersFetch();assert.equal(parsed,0);assert.equal(dbAdmin.platform,false);allControllersEmpty();}
    """)

    def test_current_parse_http_network_failure_clears_only_own_output(self):
            self.javascript(r"""
    await controllerBoot();await computeFetch();await resourceFetch();const platform=dbPlatform.record,resources=dbResources.record;
    for(const response of [{ok:true,status:200,text:async()=>'{private error'},{ok:false,status:500,text:async()=>JSON.stringify(controllerReply())},{ok:true,status:200,text:async()=>JSON.stringify({...controllerReply(),raw:'private'})}]){await controllerFetch();route=async()=>({controllerResponse:response});await dbFoundationControllersFetch();allControllersEmpty();assert.equal(dbAdmin.platform,true);assert.equal(dbPlatform.record,platform);assert.equal(dbResources.record,resources);assert.ok(!controllerText().includes('private'));}
    await controllerFetch();route=async()=>{throw Error('private source error');};await dbFoundationControllersFetch();allControllersEmpty();assert.ok(!controllerText().includes('private'));
    """)

    def test_wire_65536_utf8_limit_including_whitespace_atomic_rejection(self):
            self.javascript(r"""
    await controllerBoot();const raw=JSON.stringify(controllerReply()),bytes=new TextEncoder().encode(raw).length;
    await controllerFetch({rawBody:raw+' '.repeat(65536-bytes)});assert.ok(dbFoundationControllers.record);await controllerFetch({rawBody:raw+' '.repeat(65537-bytes)});allControllersEmpty();await controllerFetch({rawBody:raw+'\u3000'.repeat(22000)});allControllersEmpty();await controllerFetch({rawBody:JSON.stringify({...controllerReply(),raw:'界'.repeat(22000)})});allControllersEmpty();
    """)

    def test_nav_bear_scope_role_fence_pending_only_stale_denials_cannot_clear_newer(self):
            self.navigation_javascript(r"""
    await controllerBoot();$('tab-compute').click();
    for(const change of [()=>$('tab-warehouse').click(),()=>{$('tab-bears').click();$('bear-tab-policy').click();},()=>scope('sre'),()=>changeObservation('policy','role','bi_viewer')])for(const status of [200,401,403]){
     await controllerFetch();const held=dbFoundationControllers.record;const wait=deferred();route=()=>wait.promise;const pending=dbFoundationControllersFetch();await tick();change();dbControls();assert.equal(dbFoundationControllers.record,held);await controllerFetch();const newer=dbFoundationControllers.record;
     wait.resolve(status===200?controllerReply():{controllerResponse:{status,ok:false,text:async()=>{throw Error('must not parse');}}});await pending;assert.equal(dbFoundationControllers.record,newer);assert.equal(dbAdmin.platform,true);
     $('tab-compute').click();scope('sre','database');changeObservation('policy','role','admin');
    }
    """)

    def test_postbody_guard_stale_success_and_parse_failure_cannot_clear_new_capture(self):
            self.javascript(r"""
    await controllerBoot();for(const reject of [false,true]){const body=deferred(),entered=deferred();route=async()=>({controllerResponse:{ok:true,status:200,text:()=>{entered.resolve();return body.promise;}}});const pending=dbFoundationControllersFetch();await entered.promise;activeTab='bears';dbControls();await controllerFetch();const current=dbFoundationControllers.record;if(reject)body.reject(Error('private late parse'));else body.resolve(JSON.stringify(controllerReply()));await pending;assert.equal(dbFoundationControllers.record,current);activeTab='compute';dbControls();}
    """)

    def test_busy_auth_tick_and_database_only_epochs_do_not_cancel_manual_get(self):
            self.javascript(r"""
    await controllerBoot();const wait=deferred();route=()=>wait.promise;const pending=dbFoundationControllersFetch();await tick();const token=dbFoundationControllers.busy,count=calls.length;
    await dbFoundationControllersRefreshAuth();assert.equal(calls.length,count);dbAdminSession(session);dbUpdateMaximumRole('admin');dbEpoch++;dbAuthorizationVersion++;dbInventoryVersion++;dbControls();assert.equal(dbFoundationControllers.busy,token);wait.resolve(controllerReply());await pending;assert.ok(dbFoundationControllers.record);assert.equal(dbFoundationControllers.busy,null);assert.equal(controllerCalls().length,1);
    """)

    def test_late_reauth_cannot_revoke_new_capture_current_bad_parse_purges(self):
            self.javascript(r"""
    await controllerBoot();await controllerFetch();const wait=deferred();sessionRoute=()=>wait.promise;const pending=dbFoundationControllersRefreshAuth();await tick();activeTab='compute';dbControls();await controllerFetch();const current=dbFoundationControllers.record;wait.resolve({http:403,body:null});await pending;assert.equal(dbFoundationControllers.record,current);assert.equal(dbAdmin.platform,true);
    sessionRoute=async()=>({malformed:true});await dbFoundationControllersRefreshAuth();allControllersEmpty();assert.equal(dbAdmin.platform,true);
    """)

    def test_preserves_independent_c2a_full_contexts_snapshots_and_four_drafts(self):
            self.javascript(r"""
    session.platform_admin=true;await dualBoot();await readyObservation('sre','bi_viewer','finance','finance-marts');await readyObservation('policy','data_engineer');await fetchObservation('sre');await fetchObservation('policy');const sre=dbObservations.sre.record,policy=dbObservations.policy.record,sc=dbObservations.sre.context,pc=dbObservations.policy.context;assert.equal(Object.keys(sc).length,9);assert.equal(Object.keys(pc).length,9);assert.notDeepEqual(sc,pc);
    editSQL('SELECT retained worksheet');editDoctor('SELECT retained Doctor');$('copilot-request').value='retained Copilot';$('p35-wap-sql').value='SELECT retained WAP';const before=otherState();await controllerFetch();await dbFoundationControllersRefreshAuth();assert.equal(otherState(),before);assert.equal(dbObservations.sre.record,sre);assert.equal(dbObservations.policy.record,policy);assert.equal(dbObservations.sre.context,sc);assert.equal(dbObservations.policy.context,pc);assert.equal($('sql').value,'SELECT retained worksheet');assert.equal($('bear-query-sql').value,'SELECT retained Doctor');assert.equal($('copilot-request').value,'retained Copilot');assert.equal($('p35-wap-sql').value,'SELECT retained WAP');
    """)

    def test_each_surface_manually_replaces_one_shared_snapshot_independent_of_old_busy(self):
            self.javascript(r"""
    await controllerBoot();scope('sre');scope('policy');const oldPlatformBusy={},oldResourcesBusy={};dbPlatform.busy=oldPlatformBusy;dbResources.busy=oldResourcesBusy;
    for(const [i,id] of controllerIDs.entries()){const reply=controllerReply();reply.observed_at='2026-11-02T04:00:0'+i+'Z';await controllerFetch(reply,id);assert.equal(controllerCalls().length,i+1);for(const outputID of controllerIDs)assert.ok(controllerText(outputID).includes(reply.observed_at));assert.equal(dbPlatform.busy,oldPlatformBusy);assert.equal(dbResources.busy,oldResourcesBusy);assert.equal(dbPlatform.record,null);assert.equal(dbResources.record,null);}
    """)

    def test_current_reauth_denials_precede_json_parse_and_postbody_authority_guard(self):
            self.javascript(r"""
    await controllerBoot();for(const status of [401,403]){dbAdminSession(session);await controllerFetch();let parsed=0;sessionRoute=async()=>({controllerResponse:{ok:false,status,json:async()=>{parsed++;throw Error('must not parse');}}});await dbFoundationControllersRefreshAuth();assert.equal(parsed,0);assert.equal(dbAdmin.platform,false);allControllersEmpty();}
    sessionRoute=null;dbAdminSession(session);const body=deferred(),entered=deferred();route=async()=>({controllerResponse:{ok:true,status:200,text:()=>{entered.resolve();return body.promise;}}});const pending=dbFoundationControllersFetch();await entered.promise;currentRole='bi_viewer';dbControls();body.resolve(JSON.stringify(controllerReply()));await pending;allControllersEmpty();
    """)

    def test_locale_and_role_aba_monotonic_fence_late_body_and_denial(self):
            self.navigation_javascript(r"""
    await controllerBoot();$('tab-compute').click();
    for(const transition of [()=>{setLanguage('ja');setLanguage('en');},()=>{currentRole='bi_viewer';dbControls();currentRole='admin';dbControls();},()=>{scope('sre');scope('sre','database');},()=>{$('tab-warehouse').click();$('tab-compute').click();}]){
     for(const status of [200,403]){const wait=deferred();route=()=>wait.promise;const pending=dbFoundationControllersFetch();await tick();transition();await controllerFetch();const newer=dbFoundationControllers.record;wait.resolve(status===200?controllerReply():{controllerResponse:{status,ok:false,text:async()=>{throw Error('stale');}}});await pending;assert.equal(dbFoundationControllers.record,newer);assert.equal(dbAdmin.platform,true);}
     const body=deferred(),entered=deferred();route=async()=>({controllerResponse:{ok:true,status:200,text:()=>{entered.resolve();return body.promise;}}});const pending=dbFoundationControllersFetch();await entered.promise;transition();await controllerFetch();const newer=dbFoundationControllers.record;body.resolve(JSON.stringify(controllerReply()));await pending;assert.equal(dbFoundationControllers.record,newer);
    }
    """)

    def test_independent_c2c1_cache_busy_and_all_downstream_intents(self):
            self.javascript(r"""
    await controllerBoot();const old={scope:'platform',warehouse:'LOCAL',selected_role:'admin',observed_at:'2026-11-02T04:00:00Z',baseline_version:'local-container-declarations-v1',coverage:{kind:'deployment_container_declarations',pod_template:true,per_container:true,runtime_security:false,admission_decision:false,global_policy:false},baseline:{availability:'available',code:null,observed_at:'2026-11-02T03:59:59Z',deployments:['coordinator','worker'].map(component=>({component,containers:[baselineItem()]}))}};
    dbBaseline.record=dbBaselineRecord(old);dbBaselineSync();const held=dbBaseline.record,busy={};dbBaseline.busy=busy;const drafts=[dbExplanation.intents,dbCurator.intents,dbProposal.intents];const before=otherState();
    await controllerFetch();await dbFoundationControllersRefreshAuth();assert.equal(dbBaseline.record,held);assert.equal(dbBaseline.busy,busy);assert.deepEqual([dbExplanation.intents,dbCurator.intents,dbProposal.intents],drafts);assert.equal(otherState(),before);
    const saved=dbFoundationControllers.record;dbEpoch++;dbAuthorizationVersion++;dbInventoryVersion++;dbCurrent().database_id='other-db';dbControls();assert.equal(dbFoundationControllers.record,saved);assert.equal(controllerCalls().length,1);
    """)

    def test_existing_foundation_cache_busy_preserved_and_responsive_scoped_css(self):
        self.javascript(r"""
await controllerBoot();await baselineFetch();const saved=dbFoundationBaseline.record,busy={};dbFoundationBaseline.busy=busy;await controllerFetch();await dbFoundationControllersRefreshAuth();assert.equal(dbFoundationBaseline.record,saved);assert.equal(dbFoundationBaseline.busy,busy);
""")
        html=resources_ui.compute_ui.platform_ui.observations.harness.HTML
        self.assertIn('#observation-foundation-controllers-panel,#observation-sre-foundation-controllers-panel',html)
        self.assertIn('repeat(auto-fit,minmax(min(100%,12rem),1fr))',html)
        self.assertIn('未報告（0省略の場合あり）',html)


if __name__ == '__main__':
    unittest.main()
