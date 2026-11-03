"""Compute's C2B projection, with production handlers and Node stdin fixtures."""
import unittest
from unittest.mock import patch

import test_workbench_platform_observations_database_ui as platform_ui
import test_workbench_warehouse_runtime_ui as runtime_ui


COMPUTE = r"""
const runtimeFixtureRoute=route;
const computeText=()=>visibleText($('observation-compute-output'));
async function computeFetch(reply=platformReply()){
 route=async(path,options)=>{
  if(path==='/api/v1/bears/platform-observations'){assert.deepEqual(options,{headers:{'X-Shirokuma-Role':'admin'}});return reply;}
  return runtimeFixtureRoute(path,options);
 };
 return $('observation-compute-platform-fetch').onclick();
}
const runtimeState=()=>JSON.stringify({runtime:dbRuntime,minutes:$('runtime-minutes').value});
"""


class ComputeGovernanceDatabaseUI(unittest.TestCase):
    def javascript(self, body):
        # Keep the established source/DOM adapter, but pass this new suite through
        # stdin so Node 25 never receives the full Workbench script as argv.
        harness = platform_ui.observations.harness
        run = harness.subprocess.run

        def stdin_run(args, **kwargs):
            if list(args) == ['node', '-']:
                kwargs['input'] = kwargs['input'].replace(
                    'const $=id=>',
                    "const $=id=>id==='observation-compute-panel'&&!nodes[id]?.parentElement?null:",
                    1)
            return run(args, **kwargs)

        with patch.object(harness.subprocess, 'run', stdin_run):
            platform_ui.PlatformObservationsDatabaseUI.javascript(
                self, runtime_ui.SETUP.replace('const posts=', 'const runtimePosts=') + COMPUTE + body)

    def navigation_javascript(self, body):
        platform_ui.observations.SREPolicyDatabaseUI.navigation_javascript(self, body)

    def test_first_mount_once_after_runtime_without_database_worksheet_or_auth_prerequisite(self):
        self.javascript(r"""
assert.equal($('observation-compute-panel'),null);session.platform_admin=true;inventory=[];schemas=[];worksheets=[];worksheetId='';
dbPrincipal=JSON.stringify([session.username,session.authenticated]);dbMaximumRole='admin';
dbRuntimeInstall();const panel=$('observation-compute-panel'),output=$('observation-compute-output'),auth=$('observation-compute-auth'),fetchButton=$('observation-compute-platform-fetch');
assert.ok(panel);assert.equal(panel.parentElement,$('compute'));assert.equal(panel.role,'region');assert.equal(panel['aria-labelledby'],'observation-compute-title');
assert.ok($('compute').children.indexOf(panel)>$('compute').children.indexOf($('runtime-panel')));
assert.equal(calls.length,0);assert.equal(dbAdmin.platform,false);assert.equal(fetchButton.disabled,true);assert.equal(auth.disabled,false);
for(let i=0;i<3;i++){dbRuntimeInstall();dbPlatformComputeInstall();dbPlatformSync();}assert.equal($('observation-compute-panel'),panel);assert.equal($('observation-compute-output'),output);assert.equal($('compute').descendants().filter(n=>n.id==='observation-compute-panel').length,1);assert.equal(calls.length,0);
await auth.onclick();assert.deepEqual(calls.map(c=>c.path),['/api/session']);assert.equal(calls[0].options,undefined);assert.equal(dbAdmin.platform,true);assert.equal(fetchButton.disabled,false);
await computeFetch();assert.deepEqual(calls.map(c=>c.path),['/api/session','/api/v1/bears/platform-observations']);assert.ok(dbPlatform.record);assert.equal(worksheets.length,0);assert.equal(worksheetId,'');assert.equal(dbObservations.sre.context,null);assert.equal(dbObservations.policy.context,null);
""")

    def test_non_p_dba_engineer_viewer_and_lowered_p_cannot_fetch_even_directly(self):
        self.javascript(r"""
await start(false);
for(const platform of [false,'true',1,true])for(const role of ['admin','data_engineer','bi_viewer']){
 currentRole=role;session.platform_admin=platform;session.maximum_role=platform===true?'data_engineer':'admin';dbAdminSession(session);dbUpdateMaximumRole(session.maximum_role);dbControls();
 const count=platformCalls().length;await $('observation-compute-auth').onclick();await $('observation-compute-platform-fetch').onclick();assert.equal(platformCalls().length,count);assert.equal($('observation-compute-platform-fetch').disabled,true);assert.equal(dbPlatform.record,null);assert.ok(!computeText().includes('2026-02-28'));
}
session.platform_admin=true;session.maximum_role='admin';currentRole='bi_viewer';dbUpdateMaximumRole('admin');dbAdminSession(session);dbControls();await $('observation-compute-platform-fetch').onclick();assert.equal(platformCalls().length,0);assert.equal($('observation-compute-auth').disabled,true);
""")

    def test_real_three_surface_navigation_language_and_reference_viewer_share_one_cache(self):
        self.navigation_javascript(r"""
session.platform_admin=true;await dualBoot();await readyObservation('sre','bi_viewer','finance','finance-marts');await readyObservation('policy','bi_viewer');await fetchObservation('sre');await fetchObservation('policy');
const sre=dbObservations.sre.record,policy=dbObservations.policy.record,sc=dbObservations.sre.context,pc=dbObservations.policy.context;
editSQL('SELECT kept SQL');editDoctor('SELECT kept Doctor');$('copilot-request').value='kept Copilot';$('p35-wap-sql').value='SELECT kept WAP';const held=[dbExplanation.intents,dbCurator.intents,dbProposal.intents];
$('tab-compute').click();assert.equal(activeTab,'compute');assert.equal(platformCalls().length,0);await computeFetch();const saved=dbPlatform.record;
for(const locale of ['ja','en','ja','en']){
 setLanguage(locale);$('tab-bears').click();$('bear-tab-sre').click();scope('sre');assert.ok(observationText('sre').includes(saved.observed_at));$('bear-tab-policy').click();scope('policy');assert.ok(observationText('policy').includes(saved.observed_at));
 scope('sre','database');scope('policy','database');assert.ok(observationText('sre').includes(sre.observed_at));assert.ok(observationText('policy').includes(policy.observed_at));$('tab-warehouse').click();$('tab-compute').click();assert.equal($('compute').hidden,false);assert.ok(computeText().includes(saved.observed_at));
 dbPlatformComputeInstall();dbPlatformSync();assert.equal(dbPlatform.record,saved);assert.equal(platformCalls().length,1);assert.equal(dbObservations.sre.record,sre);assert.equal(dbObservations.policy.record,policy);assert.equal(dbObservations.sre.context,sc);assert.equal(dbObservations.policy.context,pc);assert.equal(dbObservations.sre.role,'bi_viewer');assert.equal(dbObservations.policy.role,'bi_viewer');assert.equal($('sql').value,'SELECT kept SQL');assert.equal($('bear-query-sql').value,'SELECT kept Doctor');assert.equal($('copilot-request').value,'kept Copilot');assert.equal($('p35-wap-sql').value,'SELECT kept WAP');assert.equal(dbExplanation.intents,held[0]);assert.equal(dbCurator.intents,held[1]);assert.equal(dbProposal.intents,held[2]);
}
""")

    def test_compute_leave_fences_late_success_and_denials_without_erasing_newer_capture(self):
        self.navigation_javascript(r"""
await platformBoot();$('tab-compute').click();
for(const viaBears of [false,true])for(const status of [200,401,403]){
 const old=deferred();route=async(path,options)=>path==='/api/v1/bears/platform-observations'?old.promise:runtimeFixtureRoute(path,options);
 const pending=$('observation-compute-platform-fetch').onclick();await tick();assert.ok(dbPlatform.busy);
 if(viaBears){$('tab-bears').click();$('bear-tab-policy').click();scope('policy');}else $('tab-warehouse').click();
 assert.equal(dbPlatform.busy,null);$('tab-compute').click();const newer=platformReply();newer.saved_admissions.active=17;await computeFetch(newer);const saved=dbPlatform.record;
 old.resolve(status===200?platformReply():{http:status,body:{error:'SECRET stale denial'}});await pending;
 assert.equal(dbPlatform.record,saved);assert.equal(dbAdmin.platform,true);assert.ok(computeText().includes(t('Active admissions')+': 17'));assert.doesNotMatch(computeText(),/SECRET/);
}
""")

    def test_known_authority_loss_unconditionally_clears_hidden_protected_compute_dom(self):
        self.navigation_javascript(r"""
await platformBoot();
const losses=[()=>{session.platform_admin=false;dbAdminSession(session);},()=>{session.authenticated=false;dbAdminSession(session);},()=>{currentRole='bi_viewer';dbControls();},()=>{dbUpdateMaximumRole('data_engineer');},()=>{dbAdminSession({...session,username:'other'});dbPrincipal=JSON.stringify(['other',true]);dbControls();}];
for(const lose of losses){
 session={...session,username:'owner',authenticated:true,platform_admin:true,maximum_role:'admin'};currentRole='admin';dbPrincipal=JSON.stringify(['owner',true]);dbUpdateMaximumRole('admin');dbAdminSession(session);$('tab-compute').click();const reply=platformReply();reply.saved_admissions.active=7654321;await computeFetch(reply);assert.match(computeText(),/7654321/);
 $('tab-warehouse').click();assert.equal($('compute').hidden,true);lose();assert.equal(dbPlatform.record,null);assert.doesNotMatch(computeText(),/7654321|2026-02-28|03:00:00\.123456Z/);assert.equal($('observation-compute-platform-fetch').disabled,true);
}
""")

    def test_current_session_denials_purge_hidden_compute_without_parsing_and_stale_denials_do_not(self):
        self.navigation_javascript(r"""
await platformBoot();
for(const status of [401,403]){
 sessionRoute=null;dbAdminSession(session);$('tab-compute').click();await computeFetch();$('tab-warehouse').click();assert.ok(dbPlatform.record);
 let parsed=0;const denied={http:status,get body(){parsed++;throw SyntaxError('Unexpected end of JSON input');}};sessionRoute=async()=>denied;
 await $('observation-compute-auth').onclick();assert.equal(parsed,0);assert.equal(dbAdmin.platform,false);assert.equal(dbPlatform.record,null);assert.ok(!computeText().includes('2026-02-28'));
 sessionRoute=null;dbAdminSession(session);$('tab-compute').click();await computeFetch();const old=deferred();sessionRoute=()=>old.promise;const pending=$('observation-compute-auth').onclick();await tick();$('tab-warehouse').click();$('tab-compute').click();sessionRoute=null;await $('observation-compute-auth').onclick();await computeFetch();const saved=dbPlatform.record;
 old.resolve(denied);await pending;assert.equal(parsed,0);assert.equal(dbAdmin.platform,true);assert.equal(dbPlatform.record,saved);assert.ok(computeText().includes(saved.observed_at));
 $('tab-bears').click();$('bear-tab-sre').click();scope('sre');assert.equal($('compute').hidden,true);route=async(path,options)=>path==='/api/v1/bears/platform-observations'?denied:runtimeFixtureRoute(path,options);await $('observation-sre-platform-fetch').onclick();assert.equal(parsed,0);assert.equal(dbAdmin.platform,false);assert.equal(dbPlatform.record,null);assert.ok(!computeText().includes('2026-02-28'));
}
""")

    def test_late_success_session_json_cannot_restore_or_remove_newer_compute_authority(self):
        self.navigation_javascript(r"""
await platformBoot();$('tab-compute').click();await computeFetch();
for(const denied of [false,true]){
 const body=deferred();let parsed=0;sessionRoute=async()=>({http:200,get body(){parsed++;return body.promise;}});const pending=$('observation-compute-auth').onclick();await tick();assert.equal(parsed,1);
 $('tab-warehouse').click();$('tab-compute').click();sessionRoute=null;await $('observation-compute-auth').onclick();await computeFetch();const saved=dbPlatform.record;
 if(denied){session.platform_admin=false;dbAdminSession(session);assert.equal(dbPlatform.record,null);}
 body.resolve(denied?{...session,platform_admin:true}:{...session,authenticated:false,platform_admin:false,maximum_role:'bi_viewer'});await pending;
 if(denied){assert.equal(dbAdmin.platform,false);assert.equal(dbPlatform.record,null);assert.ok(!computeText().includes('2026-02-28'));session.platform_admin=true;dbAdminSession(session);}else{assert.equal(dbAdmin.platform,true);assert.equal(dbPlatform.record,saved);assert.ok(computeText().includes(saved.observed_at));}
}
""")

    def test_database_only_context_grant_inventory_changes_keep_completed_and_inflight_capture(self):
        self.javascript(r"""
await platformBoot();await readyObservation('sre','bi_viewer');await computeFetch();const saved=dbPlatform.record;
changeObservation('sre','schema','raw');dbInvalidateReceipt('bi_viewer');dbEpoch++;dbAuthorizationVersion++;dbInventoryVersion++;dbControls();assert.equal(dbPlatform.record,saved);assert.ok(computeText().includes(saved.observed_at));
const old=deferred();route=async(path,options)=>path==='/api/v1/bears/platform-observations'?old.promise:runtimeFixtureRoute(path,options);const pending=$('observation-compute-platform-fetch').onclick();await tick();const token=dbPlatform.busy;
changeObservation('sre','database','');dbEpoch++;dbAuthorizationVersion++;dbInventoryVersion++;dbControls();assert.equal(dbPlatform.busy,token);old.resolve(platformReply());await pending;assert.ok(dbPlatform.record);assert.equal(platformCalls().length,2);
""")

    def test_shared_closed_dto_projection_partial_failures_disabled_policy_and_plain_bilingual_copy(self):
        self.javascript(r"""
await platformBoot();scope('sre');const reply=unavailable(platformReply(),'physical');reply.auto_chill.minutes=0;await computeFetch(reply);
for(const locale of ['en','ja']){
 setLanguage(locale);dbControls();const text=computeText(),lines=$('observation-compute-output').children.map(n=>n.textContent);assert.equal(text,observationText('sre'));assert.equal(lines[0],t('Platform scope')+' · LOCAL · '+t('Global role')+': '+dbObservationLabel('role',currentRole));assert.equal(lines[1],t('Observed at')+': '+reply.observed_at);
 for(const key of ['Deployment source unavailable.','Historical engine proof only; no live engine probe or current SQL health guarantee.','Saved admissions are incomplete blockers; this is not a security audit or permission to chill.','Counts exclude query admissions with verified execution closure; saved UNKNOWN history is unchanged.','Disabled (0 min)'])assert.ok(text.includes(t(key)));
 assert.ok(text.includes(reply.engine_evidence.verified_at));assert.doesNotMatch(text,/PHYSICAL_UNAVAILABLE|generation_id|Policy revision|ポリシー改訂|revision|live_probe|CPU|memory/);
}
const invalid=platformReply();invalid.engine_evidence.verified_at=null;await computeFetch(invalid);assert.equal(dbPlatform.record,null);assert.ok(!computeText().includes('2026-02-28'));
const extra={...platformReply(),secret:'/private/operator'};await computeFetch(extra);assert.equal(dbPlatform.record,null);assert.doesNotMatch(computeText(),/secret|private\/operator/);
const all=platformReply();for(const key of ['physical','engine_evidence','auto_chill','saved_admissions'])unavailable(all,key);await computeFetch(all);assert.ok(dbPlatform.record);assert.doesNotMatch(computeText(),/null|Desired replicas: 0|Active admissions: 0|Idle minutes: 0/);
""")

    def test_explicit_observer_buttons_only_call_session_and_c2b_not_runtime_or_legacy(self):
        self.javascript(r"""
await platformBoot();const observerStart=calls.length,wake=$('runtime-wake').onclick,chill=$('runtime-chill').onclick,save=$('runtime-save').onclick,receipt=$('runtime-receipt').onclick,refresh=$('runtime-refresh').onclick;
await $('observation-compute-auth').onclick();await computeFetch();assert.deepEqual(calls.slice(observerStart).map(c=>c.path),['/api/session','/api/v1/bears/platform-observations']);assert.equal(calls.slice(observerStart).some(c=>c.options?.method==='POST'),false);
assert.equal($('runtime-wake').onclick,wake);assert.equal($('runtime-chill').onclick,chill);assert.equal($('runtime-save').onclick,save);assert.equal($('runtime-receipt').onclick,receipt);assert.equal($('runtime-refresh').onclick,refresh);
const prior=platformCalls().length,count=calls.length;await $('runtime-refresh').onclick();assert.ok(calls.slice(count).some(c=>c.path===BASE));assert.equal(platformCalls().length,prior);assert.ok(calls.slice(count).every(c=>c.path==='/api/session'||c.path===BASE));
for(const id of ['compute-sre-check','compute-policy-check']){assert.equal($(id).disabled,true);assert.equal($(id).hidden,true);}
""")

    def test_runtime_receipt_dirty_cas_conflict_focus_drafts_and_real_unknown_intents_are_unchanged(self):
        self.javascript(platform_ui.CURATOR + platform_ui.PROPOSAL + r"""
const nativeAppend=Node.prototype.append;Node.prototype.append=function(...items){return nativeAppend.apply(this,items.map(n=>typeof n==='string'?Object.assign(new Node('text'),{textContent:n}):n));};
session.platform_admin=true;await boot();request('proposal');await choose();await submit(pendingProposal({state:'UNKNOWN',inference_state:'UNKNOWN'}));assert.equal(dbProposalHeld().length,1);
await curatorContext();objects=[curatorObject()];await dbCuratorLoad();change('object','0');await curatorSubmit(curatorPending({state:'UNKNOWN',inference_state:'UNKNOWN'}));assert.equal(dbCuratorHeld().length,1);
route=async()=>wrap(record({state:'UNKNOWN',text:null,inference_state:'UNKNOWN'}));await $('copilot-explain').onclick();route=async()=>envelope(dbRuns.get(worksheetId),{state:'UNKNOWN'});await dbRunQuery();
const wap=dataopsPanels.get('p35-wap');schemas[0].capabilities.wap_models=true;route=async path=>({context:JSON.parse(new URL(path,'http://fixture').searchParams.get('context')),items:[]});await dataopsRefresh(wap);route=async()=>{throw Error('lost ACK');};await dataopsSubmit(wap,'/api/v1/wap/build',{model_id:'existing-model',version:1},{});
editSQL('SELECT kept SQL');editDoctor('SELECT kept Doctor');request('kept Copilot');$('p35-wap-sql').value='SELECT kept WAP';wap.sources[0].alias.value='kept';
await readyObservation('sre');await readyObservation('policy');await fetchObservation('sre');await fetchObservation('policy');const sre=dbObservations.sre.record,policy=dbObservations.policy.record;
route=runtimeFixtureRoute;await refreshStatus();const input=$('runtime-minutes');input.value='47';input.oninput();actionRoute=async()=>{throw Error('lost runtime ACK');};await dbRuntimeAct('wake');assert.ok(dbRuntime.intent);runtime.policy={minutes:12,revision:4};await refreshStatus();assert.equal(dbRuntime.conflict,true);assert.equal(dbRuntime.base,3);assert.equal(dbRuntime.dirty,true);input.focus();
const before=otherState(),runtimeBefore=runtimeState(),intent=dbRuntime.intent,key=intent.key,op=wap.op,observerStart=calls.length;
await $('observation-compute-auth').onclick();await computeFetch();await computeFetch(unavailable(platformReply(),'saved_admissions'));await computeFetch({http:500,body:{error:'SECRET'}});
assert.equal(otherState(),before);assert.equal(runtimeState(),runtimeBefore);assert.equal(document.activeElement,input);assert.equal(input.value,'47');assert.equal(dbRuntime.intent,intent);assert.equal(dbRuntime.intent.key,key);assert.equal(wap.op,op);assert.equal(wap.op.ambiguous,true);assert.equal(dbRuns.get(worksheetId).state,'UNKNOWN');assert.equal(dbProposalHeld().length,1);assert.equal(dbCuratorHeld().length,1);assert.equal(dbObservations.sre.record,sre);assert.equal(dbObservations.policy.record,policy);assert.ok(calls.slice(observerStart).every(c=>['/api/session','/api/v1/bears/platform-observations'].includes(c.path)&&!c.options?.method));
""")

    def test_native_region_wrapping_and_precise_compute_notice_bilingual(self):
        self.javascript(r"""
await platformBoot();narrow=true;const panel=$('observation-compute-panel');assert.equal(panel.className,'panel');assert.equal(panel.children[0].className,'panel-head');assert.equal(panel.children[1].className,'panel-body');assert.equal(panel.children[1].children[0].className,'toolbar');assert.equal($('observation-compute-output').className,'table-wrap');assert.equal(panel.descendants().filter(n=>n.className==='panel').length,0);assert.equal(panel.descendants().filter(n=>['INPUT','TEXTAREA','SELECT'].includes(n.tagName)).length,0);assert.equal(panel.descendants().filter(n=>n.tagName==='BUTTON').length,2);
for(const locale of ['ja','en']){setLanguage(locale);dbControls();const key='P-only read-only Platform observations are available. Legacy workload checks, policy audits and other Bear actions remain unavailable.';assert.equal($('database-compute-notice').textContent,t(key));assert.equal($('compute-sre-check').title,t(key));assert.equal($('compute-policy-check').title,t(key));assert.equal($('observation-compute-title').textContent,t('Platform observations (P-only, read-only)'));if(locale==='ja')assert.notEqual(t(key),key);}
""")
    def test_maximum_length_times_and_safe_integer_counts_remain_complete_in_both_languages(self):
        self.javascript(r"""
await platformBoot();const reply=platformReply(),time='2026-11-01T03:00:00.'+'1'.repeat(43)+'Z',historical='2024-02-29T01:02:03.'+'2'.repeat(38)+'+09:00',maximum=Number.MAX_SAFE_INTEGER;
assert.equal(time.length,64);assert.equal(historical.length,64);reply.observed_at=time;
for(const key of ['physical','engine_evidence','auto_chill','saved_admissions'])reply[key].observed_at=time;
reply.engine_evidence.verified_at=historical;Object.assign(reply.physical,{state:'transitioning',replicas:maximum,ready_replicas:maximum,deployments:[{role:'coordinator',replicas:maximum-1,ready_replicas:maximum-1},{role:'worker',replicas:1,ready_replicas:1}]});
reply.auto_chill.revision=maximum;reply.saved_admissions.active=maximum;reply.saved_admissions.unknown=maximum;
await computeFetch(reply);assert.ok(dbPlatform.record);assert.equal(dbPlatform.record.auto_chill.revision,maximum);
for(const locale of ['ja','en']){
 setLanguage(locale);dbControls();const output=$('observation-compute-output'),lines=output.children.map(n=>n.textContent),text=computeText();
 assert.equal(lines.filter(line=>line===t('Observed at')+': '+time).length,5);assert.ok(lines.includes(t('Historically verified at')+': '+historical));
 for(const label of ['Desired replicas','Ready replicas','Active admissions','Unknown admissions'])assert.ok(lines.includes(t(label)+': '+maximum));
 assert.ok(lines.includes(t('Coordinator')+' · '+t('Desired replicas')+': '+(maximum-1)+' · '+t('Ready replicas')+': '+(maximum-1)));assert.ok(lines.includes(t('Worker')+' · '+t('Desired replicas')+': 1 · '+t('Ready replicas')+': 1'));
 assert.ok(text.includes(time));assert.ok(text.includes(historical));assert.doesNotMatch(text,/Policy revision|ポリシー改訂|revision|\.\.\.|…/);
 for(const [id,label] of [['observation-compute-auth','Refresh platform authority'],['observation-compute-platform-fetch','Fetch platform observation']]){const button=$(id);assert.equal(button.textContent,t(label));assert.equal(button.hidden,false);assert.equal(button.disabled,false);}
}
""")


if __name__ == '__main__':
    unittest.main()
