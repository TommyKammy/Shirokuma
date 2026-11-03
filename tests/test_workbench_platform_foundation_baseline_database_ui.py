"""Seven managed foundation declaration baseline through production handlers and Node stdin."""
import json
import unittest
from unittest.mock import patch
import test_workbench_platform_resources_database_ui as resources_ui

BASELINE = r"""
const baselineIDs=['observation-sre-foundation-baseline-','observation-policy-foundation-baseline-','observation-foundation-baseline-'];
const baselineCalls=()=>calls.filter(c=>c.path==='/api/v1/bears/platform-foundation-baseline');
const baselineText=(id=baselineIDs[2])=>visibleText($(id+'output'));
const baselineItem=(kind='container',ordinal=1,state='met')=>({kind,ordinal,checks:{read_only_root_filesystem:state,non_root:state,drop_all_capabilities:state,cpu_memory_limits:state}});
const baselineReply=()=>({scope:'platform',warehouse:'LOCAL',selected_role:'admin',observed_at:'2026-11-02T04:00:00.123456Z',baseline_version:'foundation-container-declarations-v1',coverage:{kind:'foundation_workload_container_declarations',pod_template:true,per_container:true,runtime_security:false,admission_decision:false,global_policy:false},baseline:{availability:'available',code:null,observed_at:'2026-11-02T03:59:59Z',workloads:['storage','catalog_database','catalog','coordinator','worker','authorization','catalog_bootstrap'].map(component=>({component,kind:component==='catalog_bootstrap'?'Job':'Deployment',containers:[baselineItem(),baselineItem('init_container')]}))}});
const baselineUnavailable=()=>{const r=baselineReply();r.baseline={availability:'unavailable',code:'FOUNDATION_BASELINE_UNAVAILABLE',observed_at:r.baseline.observed_at,workloads:null};return r;};
async function baselineFetch(reply=baselineReply(),id=baselineIDs[2]){
 route=async(path,options)=>{if(path==='/api/v1/bears/platform-foundation-baseline'){assert.deepEqual(options,{headers:{'X-Shirokuma-Role':'admin'}});return reply;}return runtimeFixtureRoute(path,options);};
 await $(id+'fetch').onclick();
}
async function baselineBoot(){session.platform_admin=true;await boot();}
function allBaselineEmpty(){assert.equal(dbFoundationBaseline.record,null);for(const id of baselineIDs){assert.ok(!baselineText(id).includes('2026-11-02T04:00'));assert.ok(!baselineText(id).includes(t('Foundation declaration matches')));}}
"""


class PlatformFoundationBaselineDatabaseUI(unittest.TestCase):
    def javascript(self, body):
        harness = resources_ui.compute_ui.platform_ui.observations.harness
        run = harness.subprocess.run

        def baseline_adapter(args, **kwargs):
            if list(args) == ['node', '-']:
                source = kwargs['input'].replace(
                    'const $=id=>',
                    "const $=id=>/^observation-(?:foundation-baseline|sre-foundation-baseline|policy-foundation-baseline|baseline|sre-baseline|policy-baseline|resources|compute|sre|policy)-panel$/.test(id)&&!nodes[id]?.parentElement?null:", 1)
                source = source.replace('if(data?.malformed)',
                                        'if(data?.baselineResponse)return data.baselineResponse;\n if(data?.malformed)', 1)
                kwargs['input'] = source
            return run(args, **kwargs)

        with patch.object(harness.subprocess, 'run', baseline_adapter):
            resources_ui.PlatformResourcesDatabaseUI.javascript(self, BASELINE + body)

    def navigation_javascript(self, body):
        harness = resources_ui.compute_ui.platform_ui.observations.harness
        handlers = harness.SCRIPT[harness.SCRIPT.index('const bearTabs='):harness.SCRIPT.index('let auditEpoch=')]
        self.javascript(r"""
const bearList=new Node();for(const kind of ['sre','query','data','catalog','policy']){const tab=$('bear-tab-'+kind);tab.tagName='BUTTON';tab.dataset.bear=kind;bearList.append(tab);$('bears').append($('bear-panel-'+kind));}$('bears').append(bearList);
eval(""" + json.dumps(handlers) + r""");
""" + body)

    def test_first_mount_native_siblings_manual_auth_without_database_or_grant(self):
        self.javascript(r"""
for(const id of baselineIDs)assert.equal($(id+'panel'),null);
session.platform_admin=true;inventory=[];schemas=[];worksheets=[];worksheetId='';dbPrincipal=JSON.stringify([session.username,session.authenticated]);dbMaximumRole='admin';
dbObservationsInstall();dbRuntimeInstall();assert.equal(calls.length,0);
for(const [i,id] of baselineIDs.entries()){
 const panel=$(id+'panel');assert.equal(panel.role,'region');assert.equal(panel['aria-labelledby'],id+'title');assert.equal(panel.parentElement,i===2?$('compute'):$('bear-panel-'+['sre','policy'][i]));
 assert.equal($(id+'fetch').disabled,true);assert.equal($(id+'auth').disabled,false);
 for(let n=0;n<3;n++){dbFoundationBaselineInstall();dbRuntimeInstall();dbObservationsInstall();}assert.equal($(id+'panel'),panel);assert.equal(panel.parentElement.descendants().filter(n=>n.id===panel.id).length,1);
}
await $(baselineIDs[0]+'auth').onclick();assert.deepEqual(calls.map(c=>c.path),['/api/session']);
await baselineFetch();assert.equal(baselineCalls().length,1);assert.ok(dbFoundationBaseline.record);assert.equal(dbPlatform.record,null);assert.equal(dbResources.record,null);assert.equal(worksheets.length,0);
for(const kind of ['sre','policy']){assert.equal(dbObservations[kind].context,null);const panel=$('observation-'+kind+'-foundation-baseline-panel');scope(kind);dbPlatformRender(dbObservations[kind]);assert.equal(panel.parentElement,$('bear-panel-'+kind));assert.ok(baselineText('observation-'+kind+'-foundation-baseline-').includes(dbFoundationBaseline.record.observed_at));}
""")

    def test_closed_nested_contract_rejects_aliases_extra_missing_and_wrong_types(self):
        self.javascript(r"""
await baselineBoot();assert.ok(Object.isFrozen(dbFoundationBaselineRecord(baselineReply()).baseline.workloads[0].containers[0].checks));
const mutations=[r=>r.extra=true,r=>r.baseline.workloads[6].kind='Deployment',r=>r.baseline.workloads[0].kind='Job',r=>r.baseline.workloads.reverse(),r=>r.scope='database',r=>r.selected_role='bi_viewer',r=>r.warehouse='OTHER',r=>r.baseline_version='v2',r=>r.observed_at='2026-02-30T00:00:00Z',r=>r.coverage.extra=false,r=>r.coverage.runtime_security=true,r=>r.coverage.pod_template=1,r=>delete r.coverage.global_policy,r=>r.baseline.code='raw error',r=>r.baseline.observed_at=null,r=>r.baseline.availability='partial',r=>r.baseline.workloads[0].role='coordinator',r=>delete r.baseline.workloads[0].component,r=>r.baseline.workloads[0].containers[0].index=1,r=>r.baseline.workloads[0].containers[1].kind='init',r=>r.baseline.workloads[0].containers[0].ordinal='1',r=>r.baseline.workloads[0].containers[0].checks.non_root=true,r=>r.baseline.workloads[0].containers[0].checks.non_root='pass',r=>delete r.baseline.workloads[0].containers[0].checks.cpu_memory_limits,r=>r.baseline.workloads[0].containers[0].checks.raw='met',r=>r.baseline.workloads[0].containers[0].securityContext={runAsUser:1000},r=>r.baseline.workloads[0].containers[0].name='private-name'];
for(const mutate of mutations){const r=baselineReply();mutate(r);assert.throws(()=>dbFoundationBaselineRecord(r));}for(const bad of [null,[],false,'raw'])assert.throws(()=>dbFoundationBaselineRecord(bad));
""")

    def test_exact_components_each_ordinary_contiguous_unique_ordinals_and_total_64(self):
        self.javascript(r"""
await baselineBoot();const r=baselineReply();r.baseline.workloads[0].containers=Array.from({length:21},(_,i)=>baselineItem('container',i+1));r.baseline.workloads[1].containers=[...Array.from({length:16},(_,i)=>baselineItem('container',i+1)),...Array.from({length:17},(_,i)=>baselineItem('init_container',i+1))];
assert.equal(r.baseline.workloads.flatMap(d=>d.containers).length,64);await baselineFetch(r);assert.equal(dbFoundationBaseline.record.baseline.workloads[0].containers.length,21);
r.baseline.workloads[1].containers.push(baselineItem('init_container',18));assert.throws(()=>dbFoundationBaselineRecord(r));
for(const mutate of [r=>r.baseline.workloads.pop(),r=>r.baseline.workloads.push(structuredClone(r.baseline.workloads[0])),r=>r.baseline.workloads[1].component='storage',r=>r.baseline.workloads[0].containers=[],r=>r.baseline.workloads[0].containers=[baselineItem('init_container')],r=>r.baseline.workloads[0].containers.push(baselineItem()),r=>r.baseline.workloads[0].containers[1].ordinal=2,r=>r.baseline.workloads[0].containers[0].ordinal=0,r=>r.baseline.workloads[0].containers[0].ordinal=1.5]){const d=baselineReply();mutate(d);assert.throws(()=>dbFoundationBaselineRecord(d));}
""")

    def test_available_not_met_is_known_and_unavailable_atomically_replaces_met(self):
        self.javascript(r"""
await baselineBoot();const r=baselineReply();for(const row of r.baseline.workloads)row.containers=[baselineItem('container',1,'not_met')];await baselineFetch(r);assert.ok(dbFoundationBaseline.record);assert.ok(baselineText().includes(t('Foundation declaration missing or mismatched')));assert.ok(!baselineText().includes(t('Foundation declarations cannot be verified')));
await baselineFetch();assert.ok(baselineText().includes(t('Foundation declaration matches')));await baselineFetch(baselineUnavailable());assert.equal(dbFoundationBaseline.record.baseline.workloads,null);for(const id of baselineIDs){assert.ok(baselineText(id).includes(t('Foundation declarations cannot be verified')));assert.ok(!baselineText(id).includes(t('Foundation declaration matches')));}
for(const mutate of [r=>r.baseline.code=null,r=>r.baseline.workloads=[],r=>r.baseline.workloads=baselineReply().baseline.workloads]){const r=baselineUnavailable();mutate(r);assert.throws(()=>dbFoundationBaselineRecord(r));}
""")

    def test_safe_bilingual_conditions_source_bounds_and_native_controls(self):
        self.javascript(r"""
await baselineBoot();await baselineFetch();const baselineSnapshot=dbFoundationBaseline.record,count=calls.length;
for(const locale of ['ja','en']){setLanguage(locale);dbControls();for(const id of baselineIDs){const text=baselineText(id);assert.ok(text.includes(baselineSnapshot.observed_at));assert.ok(text.includes(baselineSnapshot.baseline.observed_at));for(const [kind,label] of Object.values(dbFoundationBaselineComponents)){assert.ok(text.includes(t(label)));assert.ok(text.includes(kind));}assert.ok(text.includes(t('Init container')+' 1'));assert.ok(text.includes(t('Foundation declaration matches')));for(const label of Object.values(dbFoundationBaselineChecks)){assert.ok(text.includes(t(label)));if(locale==='ja')assert.notEqual(t(label),label);}assert.doesNotMatch(text,/securityContext|runAsUser|"checks"|INVALID_RESPONSE|PASS/);assert.equal($(id+'output').descendants().some(n=>['SCRIPT','IMG','PRE','INPUT','BUTTON'].includes(n.tagName)),false);assert.equal($(id+'fetch').tagName,'BUTTON');$(id+'fetch').focus();assert.equal(document.activeElement,$(id+'fetch'));}assert.equal(calls.length,count);}
assert.equal(dbFoundationBaseline.record,baselineSnapshot);
""")

    def test_real_navigation_scopes_and_language_share_snapshot_time_without_get(self):
        self.navigation_javascript(r"""
session.platform_admin=true;await dualBoot();$('tab-compute').click();await baselineFetch();const baselineSnapshot=dbFoundationBaseline.record,count=calls.length;
for(const locale of ['ja','en','ja','en']){setLanguage(locale);$('tab-bears').click();for(const kind of ['sre','policy']){$('bear-tab-'+kind).click();scope(kind);assert.equal($('observation-'+kind+'-foundation-baseline-panel').hidden,false);assert.ok(baselineText('observation-'+kind+'-foundation-baseline-').includes(baselineSnapshot.observed_at));scope(kind,'database');assert.equal($('observation-'+kind+'-foundation-baseline-panel').hidden,true);} $('tab-warehouse').click();$('tab-compute').click();assert.equal(dbFoundationBaseline.record,baselineSnapshot);assert.ok(baselineText().includes(baselineSnapshot.observed_at));assert.equal(calls.length,count);}
await dbFoundationBaselineRefreshAuth();assert.equal(baselineCalls().length,1);assert.equal(dbFoundationBaseline.record,baselineSnapshot);
""")

    def test_authority_truth_principal_global_roles_and_direct_handlers_no_source(self):
        self.javascript(r"""
await boot();for(const platform of [false,'true',1,null,undefined]){session.platform_admin=platform;dbAdminSession(session);await dbFoundationBaselineRefreshAuth();await dbFoundationBaselineFetch();allBaselineEmpty();}
session.platform_admin=true;dbAdminSession(session);for(const role of ['bi_viewer','data_engineer']){currentRole=role;dbControls();await dbFoundationBaselineRefreshAuth();await dbFoundationBaselineFetch();allBaselineEmpty();}
currentRole='admin';for(const maximum of ['bi_viewer','data_engineer']){session.maximum_role=maximum;dbUpdateMaximumRole(maximum);dbAdminSession(session);await dbFoundationBaselineRefreshAuth();await dbFoundationBaselineFetch();allBaselineEmpty();}
session.maximum_role='admin';dbUpdateMaximumRole('admin');for(const change of [{authenticated:false},{username:'other'},{data_mode:'legacy'}]){dbAdminSession({...session,...change});await dbFoundationBaselineFetch();allBaselineEmpty();}assert.equal(baselineCalls().length,0);
""")

    def test_hidden_outputs_purge_when_other_platform_caches_empty_and_p_already_false(self):
        self.javascript(r"""
await baselineBoot();scope('sre');scope('policy');await baselineFetch();const baselineSnapshot=dbFoundationBaseline.record;scope('sre','database');scope('policy','database');activeTab='warehouse';dbControls();assert.equal(dbPlatform.record,null);assert.equal(dbResources.record,null);assert.ok(baselineSnapshot);
dbAdmin.platform=false;dbAdminSession({...session,platform_admin:false});allBaselineEmpty();for(const id of baselineIDs)assert.equal($(id+'fetch').disabled,true);
dbFoundationBaseline.record=baselineSnapshot;for(const id of baselineIDs)$(id+'output').append(Object.assign(new Node(),{textContent:baselineSnapshot.observed_at}));dbAdminSession({...session,platform_admin:false});allBaselineEmpty();
""")

    def test_current_401_403_revoke_before_body_parse_even_with_broken_body(self):
        self.javascript(r"""
await baselineBoot();for(const status of [401,403]){dbAdminSession(session);await baselineFetch();let parsed=0;route=async()=>({baselineResponse:{status,ok:false,text:async()=>{parsed++;throw Error('secret body');},json:async()=>{parsed++;throw Error('secret body');}}});await dbFoundationBaselineFetch();assert.equal(parsed,0);assert.equal(dbAdmin.platform,false);allBaselineEmpty();}
""")

    def test_current_parse_http_network_failure_clears_only_own_output(self):
        self.javascript(r"""
await baselineBoot();await computeFetch();await resourceFetch();const platform=dbPlatform.record,resources=dbResources.record;
for(const response of [{ok:true,status:200,text:async()=>'{private error'},{ok:false,status:500,text:async()=>JSON.stringify(baselineReply())},{ok:true,status:200,text:async()=>JSON.stringify({...baselineReply(),raw:'private'})}]){await baselineFetch();route=async()=>({baselineResponse:response});await dbFoundationBaselineFetch();allBaselineEmpty();assert.equal(dbAdmin.platform,true);assert.equal(dbPlatform.record,platform);assert.equal(dbResources.record,resources);assert.ok(!baselineText().includes('private'));}
await baselineFetch();route=async()=>{throw Error('private source error');};await dbFoundationBaselineFetch();allBaselineEmpty();assert.ok(!baselineText().includes('private'));
""")

    def test_wire_131072_utf8_limit_including_whitespace_atomic_rejection(self):
        self.javascript(r"""
await baselineBoot();const raw=JSON.stringify(baselineReply()),bytes=new TextEncoder().encode(raw).length;
await baselineFetch({rawBody:raw+' '.repeat(131072-bytes)});assert.ok(dbFoundationBaseline.record);await baselineFetch({rawBody:raw+' '.repeat(131073-bytes)});allBaselineEmpty();await baselineFetch({rawBody:raw+'\u3000'.repeat(44000)});allBaselineEmpty();await baselineFetch({rawBody:JSON.stringify({...baselineReply(),raw:'界'.repeat(44000)})});allBaselineEmpty();
""")

    def test_nav_bear_scope_role_fence_pending_only_stale_denials_cannot_clear_newer(self):
        self.navigation_javascript(r"""
await baselineBoot();$('tab-compute').click();
for(const change of [()=>$('tab-warehouse').click(),()=>{$('tab-bears').click();$('bear-tab-policy').click();},()=>scope('sre'),()=>changeObservation('policy','role','bi_viewer')])for(const status of [200,401,403]){
 await baselineFetch();const held=dbFoundationBaseline.record;const wait=deferred();route=()=>wait.promise;const pending=dbFoundationBaselineFetch();await tick();change();dbControls();assert.equal(dbFoundationBaseline.record,held);await baselineFetch();const newer=dbFoundationBaseline.record;
 wait.resolve(status===200?baselineReply():{baselineResponse:{status,ok:false,text:async()=>{throw Error('must not parse');}}});await pending;assert.equal(dbFoundationBaseline.record,newer);assert.equal(dbAdmin.platform,true);
 $('tab-compute').click();scope('sre','database');changeObservation('policy','role','admin');
}
""")

    def test_postbody_guard_stale_success_and_parse_failure_cannot_clear_new_capture(self):
        self.javascript(r"""
await baselineBoot();for(const reject of [false,true]){const body=deferred(),entered=deferred();route=async()=>({baselineResponse:{ok:true,status:200,text:()=>{entered.resolve();return body.promise;}}});const pending=dbFoundationBaselineFetch();await entered.promise;activeTab='bears';dbControls();await baselineFetch();const current=dbFoundationBaseline.record;if(reject)body.reject(Error('private late parse'));else body.resolve(JSON.stringify(baselineReply()));await pending;assert.equal(dbFoundationBaseline.record,current);activeTab='compute';dbControls();}
""")

    def test_busy_auth_tick_and_database_only_epochs_do_not_cancel_manual_get(self):
        self.javascript(r"""
await baselineBoot();const wait=deferred();route=()=>wait.promise;const pending=dbFoundationBaselineFetch();await tick();const token=dbFoundationBaseline.busy,count=calls.length;
await dbFoundationBaselineRefreshAuth();assert.equal(calls.length,count);dbAdminSession(session);dbUpdateMaximumRole('admin');dbEpoch++;dbAuthorizationVersion++;dbInventoryVersion++;dbControls();assert.equal(dbFoundationBaseline.busy,token);wait.resolve(baselineReply());await pending;assert.ok(dbFoundationBaseline.record);assert.equal(dbFoundationBaseline.busy,null);assert.equal(baselineCalls().length,1);
""")

    def test_late_reauth_cannot_revoke_new_capture_current_bad_parse_purges(self):
        self.javascript(r"""
await baselineBoot();await baselineFetch();const wait=deferred();sessionRoute=()=>wait.promise;const pending=dbFoundationBaselineRefreshAuth();await tick();activeTab='compute';dbControls();await baselineFetch();const current=dbFoundationBaseline.record;wait.resolve({http:403,body:null});await pending;assert.equal(dbFoundationBaseline.record,current);assert.equal(dbAdmin.platform,true);
sessionRoute=async()=>({malformed:true});await dbFoundationBaselineRefreshAuth();allBaselineEmpty();assert.equal(dbAdmin.platform,true);
""")

    def test_preserves_independent_c2a_full_contexts_snapshots_and_four_drafts(self):
        self.javascript(r"""
session.platform_admin=true;await dualBoot();await readyObservation('sre','bi_viewer','finance','finance-marts');await readyObservation('policy','data_engineer');await fetchObservation('sre');await fetchObservation('policy');const sre=dbObservations.sre.record,policy=dbObservations.policy.record,sc=dbObservations.sre.context,pc=dbObservations.policy.context;assert.equal(Object.keys(sc).length,9);assert.equal(Object.keys(pc).length,9);assert.notDeepEqual(sc,pc);
editSQL('SELECT retained worksheet');editDoctor('SELECT retained Doctor');$('copilot-request').value='retained Copilot';$('p35-wap-sql').value='SELECT retained WAP';const before=otherState();await baselineFetch();await dbFoundationBaselineRefreshAuth();assert.equal(otherState(),before);assert.equal(dbObservations.sre.record,sre);assert.equal(dbObservations.policy.record,policy);assert.equal(dbObservations.sre.context,sc);assert.equal(dbObservations.policy.context,pc);assert.equal($('sql').value,'SELECT retained worksheet');assert.equal($('bear-query-sql').value,'SELECT retained Doctor');assert.equal($('copilot-request').value,'retained Copilot');assert.equal($('p35-wap-sql').value,'SELECT retained WAP');
""")

    def test_each_surface_manually_replaces_one_shared_snapshot_independent_of_old_busy(self):
        self.javascript(r"""
await baselineBoot();scope('sre');scope('policy');const oldPlatformBusy={},oldResourcesBusy={};dbPlatform.busy=oldPlatformBusy;dbResources.busy=oldResourcesBusy;
for(const [i,id] of baselineIDs.entries()){const reply=baselineReply();reply.observed_at='2026-11-02T04:00:0'+i+'Z';await baselineFetch(reply,id);assert.equal(baselineCalls().length,i+1);for(const outputID of baselineIDs)assert.ok(baselineText(outputID).includes(reply.observed_at));assert.equal(dbPlatform.busy,oldPlatformBusy);assert.equal(dbResources.busy,oldResourcesBusy);assert.equal(dbPlatform.record,null);assert.equal(dbResources.record,null);}
""")

    def test_every_nested_object_is_closed_and_requires_each_key(self):
        self.javascript(r"""
await baselineBoot();const paths=[[],['coverage'],['baseline'],['baseline','workloads',0],['baseline','workloads',0,'containers',0],['baseline','workloads',0,'containers',0,'checks']];
const at=(r,path)=>path.reduce((o,k)=>o[k],r);
for(const path of paths){const keys=Object.keys(at(baselineReply(),path));for(const key of keys){const r=baselineReply();delete at(r,path)[key];assert.throws(()=>dbFoundationBaselineRecord(r));}const r=baselineReply();at(r,path).private='sensitive';assert.throws(()=>dbFoundationBaselineRecord(r));}
for(const key of ['pod_template','per_container','runtime_security','admission_decision','global_policy'])for(const bad of [0,1,'true',null]){const r=baselineReply();r.coverage[key]=bad;assert.throws(()=>dbFoundationBaselineRecord(r));}
""")

    def test_current_reauth_denials_precede_json_parse_and_postbody_authority_guard(self):
        self.javascript(r"""
await baselineBoot();for(const status of [401,403]){dbAdminSession(session);await baselineFetch();let parsed=0;sessionRoute=async()=>({baselineResponse:{ok:false,status,json:async()=>{parsed++;throw Error('must not parse');}}});await dbFoundationBaselineRefreshAuth();assert.equal(parsed,0);assert.equal(dbAdmin.platform,false);allBaselineEmpty();}
sessionRoute=null;dbAdminSession(session);const body=deferred(),entered=deferred();route=async()=>({baselineResponse:{ok:true,status:200,text:()=>{entered.resolve();return body.promise;}}});const pending=dbFoundationBaselineFetch();await entered.promise;currentRole='bi_viewer';dbControls();body.resolve(JSON.stringify(baselineReply()));await pending;allBaselineEmpty();
""")

    def test_scope_specific_css_and_human_labels(self):
        harness = resources_ui.compute_ui.platform_ui.observations.harness
        self.assertIn('#observation-foundation-baseline-panel,#observation-sre-foundation-baseline-panel,#observation-policy-foundation-baseline-panel', harness.HTML)
        self.assertIn('white-space:normal;overflow-wrap:anywhere', harness.HTML)
        for label in ['基盤宣言に一致', '基盤宣言不足・不一致', '基盤宣言は確認不可']:
            self.assertIn(label, harness.HTML)


    def test_locale_and_role_aba_monotonic_fence_late_body_and_denial(self):
        self.navigation_javascript(r"""
await baselineBoot();$('tab-compute').click();
for(const transition of [()=>{setLanguage('ja');setLanguage('en');},()=>{currentRole='bi_viewer';dbControls();currentRole='admin';dbControls();},()=>{scope('sre');scope('sre','database');},()=>{$('tab-warehouse').click();$('tab-compute').click();}]){
 for(const status of [200,403]){const wait=deferred();route=()=>wait.promise;const pending=dbFoundationBaselineFetch();await tick();transition();await baselineFetch();const newer=dbFoundationBaseline.record;wait.resolve(status===200?baselineReply():{baselineResponse:{status,ok:false,text:async()=>{throw Error('stale');}}});await pending;assert.equal(dbFoundationBaseline.record,newer);assert.equal(dbAdmin.platform,true);}
 const body=deferred(),entered=deferred();route=async()=>({baselineResponse:{ok:true,status:200,text:()=>{entered.resolve();return body.promise;}}});const pending=dbFoundationBaselineFetch();await entered.promise;transition();await baselineFetch();const newer=dbFoundationBaseline.record;body.resolve(JSON.stringify(baselineReply()));await pending;assert.equal(dbFoundationBaseline.record,newer);
}
""")

    def test_independent_c2c1_cache_busy_and_all_downstream_intents(self):
        self.javascript(r"""
await baselineBoot();const old={scope:'platform',warehouse:'LOCAL',selected_role:'admin',observed_at:'2026-11-02T04:00:00Z',baseline_version:'local-container-declarations-v1',coverage:{kind:'deployment_container_declarations',pod_template:true,per_container:true,runtime_security:false,admission_decision:false,global_policy:false},baseline:{availability:'available',code:null,observed_at:'2026-11-02T03:59:59Z',deployments:['coordinator','worker'].map(component=>({component,containers:[baselineItem()]}))}};
dbBaseline.record=dbBaselineRecord(old);dbBaselineSync();const held=dbBaseline.record,busy={};dbBaseline.busy=busy;const drafts=[dbExplanation.intents,dbCurator.intents,dbProposal.intents];const before=otherState();
await baselineFetch();await dbFoundationBaselineRefreshAuth();assert.equal(dbBaseline.record,held);assert.equal(dbBaseline.busy,busy);assert.deepEqual([dbExplanation.intents,dbCurator.intents,dbProposal.intents],drafts);assert.equal(otherState(),before);
const saved=dbFoundationBaseline.record;dbEpoch++;dbAuthorizationVersion++;dbInventoryVersion++;dbCurrent().database_id='other-db';dbControls();assert.equal(dbFoundationBaseline.record,saved);assert.equal(baselineCalls().length,1);
""")

if __name__ == '__main__':
    unittest.main()
