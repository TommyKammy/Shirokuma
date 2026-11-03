"""Manual telemetry checks through the shipped Workbench JavaScript."""
import unittest
from unittest.mock import patch
import test_workbench_platform_foundation_baseline_database_ui as foundation_ui

resources_ui = foundation_ui.resources_ui
TELEMETRY = r"""
const telemetryKinds=['sre','compute'];
const telemetryRoot=kind=>kind==='sre'?'observation-sre-telemetry-readiness':'observation-telemetry-readiness-panel';
const telemetryID=kind=>'observation-'+kind+'-telemetry-readiness-';
const telemetryCalls=()=>calls.filter(c=>c.path==='/api/v1/bears/platform-telemetry-readiness');
const telemetryText=kind=>visibleText($(telemetryID(kind)+'output'));
const telemetryReply=()=>({agenthouse:{status:'responsive',observed_at:'2026-11-04T00:00:00Z'},loki:{status:'not_ready',observed_at:'2026-11-04T00:00:01Z'}});
const telemetryUnavailable=()=>({...telemetryReply(),loki:{...telemetryReply().loki,status:'unavailable'}});
async function telemetryFetch(kind='compute',reply=telemetryReply()){
 route=async(path,options)=>{if(path==='/api/v1/bears/platform-telemetry-readiness'){assert.deepEqual(options,{headers:{'X-Shirokuma-Role':'admin'}});return reply;}return runtimeFixtureRoute(path,options);};
 await $(telemetryID(kind)+'fetch').onclick();
}
async function telemetryBoot(){session.platform_admin=true;await boot();}
function telemetryEmpty(){for(const kind of telemetryKinds){assert.equal(dbTelemetryReadiness[kind].record,null);assert.ok(!telemetryText(kind).includes('2026-11-04'));}}
"""


class PlatformTelemetryReadinessDatabaseUI(unittest.TestCase):
    def javascript(self, body):
        harness=resources_ui.compute_ui.platform_ui.observations.harness
        run=harness.subprocess.run
        def telemetry_adapter(args, **kwargs):
            if list(args)==['node','-']:
                source=kwargs['input'].replace('const $=id=>',
                    "const $=id=>/^(?:observation-sre-telemetry-readiness|observation-telemetry-readiness-panel|observation-policy-telemetry-readiness(?:-panel)?)$/.test(id)&&!nodes[id]?.parentElement?null:",1)
                source=source.replace('if(data?.malformed)',
                    'if(data?.telemetryResponse)return data.telemetryResponse;\n if(data?.malformed)',1)
                kwargs['input']=source
            return run(args,**kwargs)
        with patch.object(harness.subprocess,'run',telemetry_adapter):
            foundation_ui.PlatformFoundationBaselineDatabaseUI.javascript(self,TELEMETRY+body)

    def navigation_javascript(self, body):
        foundation_ui.PlatformFoundationBaselineDatabaseUI.navigation_javascript(self,body)

    def test_actual_mounts_without_db_grants_no_policy_and_manual_only_io(self):
        self.javascript(r"""
for(const kind of telemetryKinds)assert.equal($(telemetryRoot(kind)),null);
session.platform_admin=true;inventory=[];schemas=[];worksheets=[];worksheetId='';dbPrincipal=JSON.stringify([session.username,session.authenticated]);dbMaximumRole='admin';
dbObservationsInstall();dbRuntimeInstall();assert.equal(calls.length,0);
for(const kind of telemetryKinds){const root=$(telemetryRoot(kind)),id=telemetryID(kind);assert.equal(root.parentElement,kind==='compute'?$('compute'):$('bear-panel-sre'));assert.equal(root.role,'region');assert.equal(root['aria-labelledby'],id+'title');assert.equal($(id+'fetch').disabled,true);assert.equal($(id+'auth').disabled,false);
 for(let i=0;i<3;i++){dbTelemetryReadinessInstall();dbObservationsInstall();dbRuntimeInstall();}assert.equal($(telemetryRoot(kind)),root);assert.equal(root.parentElement.descendants().filter(n=>n.id===root.id).length,1);
}
assert.equal($('observation-policy-telemetry-readiness'),null);assert.equal($('observation-policy-telemetry-readiness-panel'),null);
await $(telemetryID('sre')+'auth').onclick();assert.deepEqual(calls.map(c=>c.path),['/api/session']);assert.equal(calls[0].options,undefined);
await telemetryFetch();assert.equal(telemetryCalls().length,1);assert.ok(dbTelemetryReadiness.compute.record);assert.equal(dbTelemetryReadiness.sre.record,null);assert.equal(dbObservations.sre.context,null);assert.equal(worksheets.length,0);
const ids=telemetryKinds.flatMap(kind=>$(telemetryRoot(kind)).descendants().map(n=>n.id).filter(Boolean));assert.equal(new Set(ids).size,ids.length);
""")

    def test_per_panel_receipts_busy_and_fetches_are_independent(self):
        self.javascript(r"""
await telemetryBoot();await telemetryFetch('sre');const held=dbTelemetryReadiness.sre.record;
const wait=deferred();route=()=>wait.promise;const pending=dbTelemetryReadinessFetch('sre');await tick();const busy=dbTelemetryReadiness.sre.busy;
const other=telemetryReply();other.loki.observed_at='2026-11-03T05:00:00.000Z';await telemetryFetch('compute',other);assert.equal(dbTelemetryReadiness.sre.record,held);assert.equal(dbTelemetryReadiness.sre.busy,busy);assert.equal(dbTelemetryReadiness.compute.busy,null);assert.equal(dbTelemetryReadiness.compute.record.loki.observed_at,other.loki.observed_at);
const compute=dbTelemetryReadiness.compute.record;wait.resolve(telemetryReply());await pending;assert.equal(dbTelemetryReadiness.compute.record,compute);assert.notEqual(dbTelemetryReadiness.sre.record,compute);
await telemetryFetch('sre',telemetryUnavailable());assert.equal(dbTelemetryReadiness.compute.record,compute);assert.equal(dbTelemetryReadiness.sre.record.loki.status,'unavailable');
""")

    def test_authority_truth_roles_maximum_principal_no_io_and_global_purge(self):
        self.javascript(r"""
await telemetryBoot();for(const kind of telemetryKinds)await telemetryFetch(kind);const telemetrySnapshot=dbTelemetryReadiness.compute.record;
for(const change of [{platform_admin:false},{platform_admin:1},{platform_admin:'true'},{maximum_role:'data_engineer'},{authenticated:false},{username:'other'},{data_mode:'legacy'}]){
 dbAdminSession({...session,...change});telemetryEmpty();const n=telemetryCalls().length;for(const kind of telemetryKinds)await dbTelemetryReadinessFetch(kind);assert.equal(telemetryCalls().length,n);dbAdminSession(session);
}
for(const role of ['bi_viewer','data_engineer']){currentRole=role;dbControls();telemetryEmpty();for(const kind of telemetryKinds)await dbTelemetryReadinessFetch(kind);currentRole='admin';dbControls();}
for(const kind of telemetryKinds)dbTelemetryReadiness[kind].record=telemetrySnapshot;dbAdmin.platform=false;dbAdminSession({...session,platform_admin:false});telemetryEmpty();
""")

    def test_current_denial_purges_both_before_body_even_when_both_pending(self):
        self.javascript(r"""
await telemetryBoot();for(const status of [401,403]){
 dbAdminSession(session);await telemetryFetch('sre');await telemetryFetch('compute');const wait=deferred();route=()=>wait.promise;const old=dbTelemetryReadinessFetch('sre');await tick();let parsed=0;
 route=async()=>({telemetryResponse:{status,ok:false,text:async()=>{parsed++;throw Error('must not parse');}}});await dbTelemetryReadinessFetch('compute');assert.equal(parsed,0);telemetryEmpty();assert.equal(dbAdmin.platform,false);wait.resolve(telemetryReply());await old;telemetryEmpty();
}
""")

    def test_current_failure_clears_only_its_panel_and_sanitizes_output(self):
        self.javascript(r"""
await telemetryBoot();await telemetryFetch('sre');const saved=dbTelemetryReadiness.sre.record;
for(const response of [{ok:true,status:200,text:async()=>'{secret'},{ok:false,status:500,text:async()=>JSON.stringify(telemetryReply())},{ok:true,status:200,text:async()=>JSON.stringify({...telemetryReply(),raw:'secret'})}]){
 await telemetryFetch('compute');route=async()=>({telemetryResponse:response});await dbTelemetryReadinessFetch('compute');assert.equal(dbTelemetryReadiness.compute.record,null);assert.equal(dbTelemetryReadiness.sre.record,saved);assert.equal(dbAdmin.platform,true);assert.ok(!telemetryText('compute').includes('secret'));
}
route=async()=>{throw Error('secret');};await dbTelemetryReadinessFetch('compute');assert.equal(dbTelemetryReadiness.sre.record,saved);
""")

    def test_navigation_scope_role_locale_aba_reject_late_success_and_denial(self):
        self.navigation_javascript(r"""
await telemetryBoot();$('tab-compute').click();
for(const transition of [()=>{$('tab-warehouse').click();$('tab-compute').click();},()=>{scope('sre');scope('sre','database');},()=>{currentRole='bi_viewer';dbControls();currentRole='admin';dbControls();},()=>{setLanguage('ja');setLanguage('en');},()=>{$('tab-bears').click();$('bear-tab-policy').click();$('tab-compute').click();}]){
 for(const status of [200,401,403]){const wait=deferred();route=()=>wait.promise;const pending=dbTelemetryReadinessFetch('compute');await tick();transition();await telemetryFetch('compute');const newer=dbTelemetryReadiness.compute.record;
 wait.resolve(status===200?telemetryReply():{telemetryResponse:{status,ok:false,text:async()=>{throw Error('late denial');}}});await pending;assert.equal(dbTelemetryReadiness.compute.record,newer);assert.equal(dbAdmin.platform,true);}
}
""")

    def test_post_body_await_and_authority_loss_discard_protected_reply(self):
        self.navigation_javascript(r"""
await telemetryBoot();$('tab-compute').click();
for(const reject of [false,true]){const body=deferred(),entered=deferred();route=async()=>({telemetryResponse:{ok:true,status:200,text:()=>{entered.resolve();return body.promise;}}});const pending=dbTelemetryReadinessFetch('compute');await entered.promise;$('tab-warehouse').click();$('tab-compute').click();await telemetryFetch('compute');const newer=dbTelemetryReadiness.compute.record;if(reject)body.reject(Error('secret'));else body.resolve(JSON.stringify(telemetryReply()));await pending;assert.equal(dbTelemetryReadiness.compute.record,newer);}
const body=deferred(),entered=deferred();route=async()=>({telemetryResponse:{ok:true,status:200,text:()=>{entered.resolve();return body.promise;}}});const pending=dbTelemetryReadinessFetch('sre');await entered.promise;dbAdminSession({...session,platform_admin:false});body.resolve(JSON.stringify(telemetryReply()));await pending;telemetryEmpty();
""")

    def test_refresh_authority_is_manual_independent_and_late_denial_ignored(self):
        self.navigation_javascript(r"""
await telemetryBoot();$('tab-compute').click();await telemetryFetch('sre');const held=dbTelemetryReadiness.sre.record;
const wait=deferred();sessionRoute=()=>wait.promise;const pending=dbTelemetryReadinessRefreshAuth('sre');await tick();const busy=dbTelemetryReadiness.sre.busy;await telemetryFetch('compute');assert.equal(dbTelemetryReadiness.sre.busy,busy);assert.equal(dbTelemetryReadiness.sre.record,held);
$('tab-warehouse').click();$('tab-compute').click();const saved=dbTelemetryReadiness.compute.record;wait.resolve({telemetryResponse:{ok:false,status:403,json:async()=>{throw Error('late');}}});await pending;assert.equal(dbTelemetryReadiness.compute.record,saved);assert.equal(dbAdmin.platform,true);
for(const status of [401,403]){dbAdminSession(session);let parsed=0;sessionRoute=async()=>({telemetryResponse:{ok:false,status,json:async()=>{parsed++;throw Error('must not parse');}}});await dbTelemetryReadinessRefreshAuth('compute');assert.equal(parsed,0);telemetryEmpty();}
""")

    def test_real_sre_platform_visibility_navigation_preserves_receipts_options_no_fetch(self):
        self.navigation_javascript(r"""
await telemetryBoot();$('tab-compute').click();await telemetryFetch();const held=dbTelemetryReadiness.compute.record,count=calls.length;
for(const locale of ['ja','en']){setLanguage(locale);$('tab-bears').click();$('bear-tab-sre').click();scope('sre');assert.equal($('observation-sre-telemetry-readiness').hidden,false);assert.equal($('observation-sre-database-controls').hidden,true);const options=$('observation-sre-scope').options.map(o=>o.value);assert.deepEqual(options,['database','platform']);scope('sre','database');assert.equal($('observation-sre-telemetry-readiness').hidden,true);$('bear-tab-policy').click();scope('policy');assert.equal($('observation-policy-telemetry-readiness'),null);$('tab-compute').click();assert.equal(dbTelemetryReadiness.compute.record,held);assert.equal(calls.length,count);}
""")

    def test_preserves_contexts_drafts_unknown_trackers_and_existing_platform_caches(self):
        self.javascript(r"""
session.platform_admin=true;await dualBoot();await readyObservation('sre','bi_viewer','finance','finance-marts');await readyObservation('policy','data_engineer');await fetchObservation('sre');await fetchObservation('policy');
const sc=dbObservations.sre.context,pc=dbObservations.policy.context,sr=dbObservations.sre.record,pr=dbObservations.policy.record;
editSQL('SELECT retained worksheet');editDoctor('SELECT retained Doctor');$('copilot-request').value='retained Copilot';$('p35-wap-sql').value='SELECT retained WAP';
await computeFetch();await resourceFetch();await baselineFetch();const caches=[dbPlatform.record,dbResources.record,dbFoundationBaseline.record],busy={};dbFoundationBaseline.busy=busy;
const intents=[dbExplanation.intents,dbProposal.intents,dbCurator.intents],ops=[...dataopsPanels].map(([key,p])=>p.op),before=otherState();
await telemetryFetch('sre');await telemetryFetch('compute');await dbTelemetryReadinessRefreshAuth('compute');assert.equal(otherState(),before);assert.equal(dbObservations.sre.context,sc);assert.equal(dbObservations.policy.context,pc);assert.equal(dbObservations.sre.record,sr);assert.equal(dbObservations.policy.record,pr);assert.equal(dbFoundationBaseline.busy,busy);
for(const [i,p] of [...dataopsPanels].map(([key,p])=>p).entries())assert.equal(p.op,ops[i]);for(const [i,p] of [dbExplanation,dbProposal,dbCurator].entries())assert.equal(p.intents,intents[i]);for(const [i,p] of [dbPlatform,dbResources,dbFoundationBaseline].entries())assert.equal(p.record,caches[i]);
const snapshot=dbTelemetryReadiness.compute.record;dbEpoch++;dbAuthorizationVersion++;dbInventoryVersion++;dbCurrent().database_id='other';dbControls();assert.equal(dbTelemetryReadiness.compute.record,snapshot);
""")


    def test_closed_dto_and_localized_distinct_statuses(self):
        self.javascript(r"""
await telemetryBoot();
for(const mutate of [r=>r.extra=true,r=>r.agenthouse.raw='private',r=>delete r.loki,r=>r.loki.status='responsive',r=>r.agenthouse.status='ready',r=>r.loki.observed_at='2026-02-30T00:00:00Z']){
 const r=telemetryReply();mutate(r);assert.throws(()=>dbTelemetryReadinessRecord(r));
}
await telemetryFetch('sre');await telemetryFetch('compute',telemetryUnavailable());
assert.ok(telemetryText('sre').includes('Reports not ready'));
assert.ok(telemetryText('compute').includes('Observation unavailable'));
assert.ok(telemetryText('sre').includes('2026-11-04T00:00:00Z'));
assert.ok(telemetryText('sre').includes('2026-11-04T00:00:01Z'));
const count=telemetryCalls().length;setLanguage('ja');
assert.ok(telemetryText('sre').includes('準備未完了'));
assert.ok(telemetryText('compute').includes('応答を確認できません'));
assert.equal(telemetryCalls().length,count);
""")
