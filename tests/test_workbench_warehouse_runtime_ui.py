"""Shared LOCAL runtime production handlers through the existing Node adapter."""
import unittest

import test_workbench_database_ui as database_ui


SETUP = r'''
const BASE='/api/v1/warehouses/LOCAL/runtime';
let runtime={warehouse:'LOCAL',physical_state:'chilled',replicas:0,ready_replicas:0,observed_at:'2026-10-29T01:02:03Z',engine_verified:false,engine_verified_at:null,policy:{minutes:15,revision:3},blockers:{runtime_operations:0,admissions:0,unknown:0,creation:{requests:0,effects:0,roots:0}},operation_phase:null,available_actions:['wake','chill','auto-chill','reconcile']};
let detail={id:'operation-one',actor:'owner',action:'WAREHOUSE_WAKE',phase:'RUNNING',timestamps:{accepted:'2026-10-29T01:02:03Z'},error_code:null};
let runtimeRoute=null,actionRoute=null,detailRoute=null;
route=async(path,options)=>{
 if(path===BASE)return runtimeRoute?runtimeRoute(path,options):structuredClone(runtime);
 if(path.startsWith(BASE+'/operations/'))return detailRoute?detailRoute(path,options):structuredClone(detail);
 if(path.startsWith(BASE+'/'))return actionRoute?actionRoute(path,options):{http:202,body:{operation_id:'operation-one',phase:'ACCEPTED'}};
 if(path==='/api/status')return {status:'healthy'};
 return {};
};
const posts=()=>calls.filter(c=>c.options?.method==='POST');
async function start(platform=true){session.platform_admin=platform;await boot();await refreshStatus();}
'''


class WarehouseRuntimeUI(unittest.TestCase):
    def javascript(self, body):
        database_ui.DatabaseUI.javascript(self, SETUP + '\n' + body)

    def test_physical_observation_and_engine_proof_are_separate(self):
        self.javascript(r'''
await start();assert.match($('quick-warehouse-state').textContent,/Physical Chilled.*2026-10-29/);
assert.match($('runtime-proof').textContent,/unverified/);
runtime.physical_state='active';runtime.replicas=1;runtime.ready_replicas=1;await refreshStatus();
assert.match($('runtime-proof').textContent,/unverified/);assert.doesNotMatch($('quick-warehouse-state').textContent,/Ready/);
runtime.engine_verified=true;await refreshStatus();assert.match($('runtime-proof').textContent,/unverified/);
runtime.engine_verified_at='2026-10-29T01:03:00Z';await refreshStatus();assert.match($('runtime-proof').textContent,/verified at/);
assert.equal(posts().length,0);
for(const call of calls.filter(c=>c.path===BASE)){assert.equal(call.options.headers['X-Shirokuma-Role'],'admin');assert.equal(call.options.body,undefined);}
''')

    def test_platform_authority_and_api_actions_both_required(self):
        self.javascript(r'''
await start(false);assert.equal($('runtime-wake').disabled,true);await dbRuntimeAct('wake');assert.equal(posts().length,0);
session.platform_admin=true;await dbRefreshAccess();await refreshStatus();assert.equal($('runtime-wake').disabled,false);
runtime.available_actions=[];await refreshStatus();assert.equal($('runtime-wake').disabled,true);await dbRuntimeAct('wake');assert.equal(posts().length,0);
for(const role of ['bi_viewer','data_engineer']){currentRole=role;dbApplyRole();await refreshStatus();assert.equal($('runtime-wake').disabled,true);assert.match($('runtime-physical').textContent,/Chilled/);await dbRuntimeAct('wake');}
assert.equal(posts().length,0);
''')

    def test_downgrade_restore_purges_drafts_and_refreshes_actions(self):
        self.javascript(r'''
await start();$('runtime-minutes').value='37';$('runtime-minutes').oninput();
currentRole='bi_viewer';dbApplyRole();assert.equal(dbRuntime.dirty,false);assert.equal(dbRuntime.intent,null);assert.equal($('runtime-minutes').value,'');
await refreshStatus();assert.equal($('runtime-wake').disabled,true);
currentRole='admin';dbApplyRole();await dbRefreshContext();await refreshStatus();assert.equal($('runtime-wake').disabled,false);assert.equal($('runtime-minutes').value,'15');assert.equal(dbRuntime.base,3);
''')

    def test_late_status_cannot_restore_protected_state(self):
        self.javascript(r'''
await start();const delayed=deferred();runtimeRoute=()=>delayed.promise;const old=refreshStatus();await tick();
currentRole='bi_viewer';dbApplyRole();runtime.available_actions=[];runtimeRoute=null;await refreshStatus();
const before=$('runtime-physical').textContent;delayed.resolve({...runtime,physical_state:'active',available_actions:['wake']});await old;
assert.equal($('runtime-wake').disabled,true);assert.equal($('runtime-physical').textContent,before);assert.equal(dbRuntime.intent,null);
''')

    def test_ack_is_acceptance_then_terminal_detail(self):
        self.javascript(r'''
await start();const draft=JSON.stringify(dbInput(dbCurrent()));await $('quick-wake').onclick();
assert.equal(posts().length,1);assert.equal(posts()[0].path,BASE+'/wake');assert.equal(posts()[0].options.body,'{}');assert.ok(posts()[0].options.headers['Idempotency-Key']);
assert.equal(dbRuntime.operation.phase,'ACCEPTED');assert.match($('runtime-guidance').textContent,/completion is not verified/);assert.equal($('runtime-chill').disabled,true);
await dbRuntimeObserve();assert.equal(dbRuntime.operation.phase,'RUNNING');assert.match($('runtime-guidance').textContent,/Starting/);
detail.phase='FINISHED';await dbRuntimeObserve();assert.match($('runtime-guidance').textContent,/refresh the physical observation/);assert.match($('runtime-physical').textContent,/Chilled/);assert.match($('runtime-proof').textContent,/unverified/);
assert.equal(JSON.stringify(dbInput(dbCurrent())),draft);assert.equal(posts().length,1);
''')

    def test_failed_unknown_and_reconcile_never_auto_retry(self):
        self.javascript(r'''
await start();await dbRuntimeAct('wake');detail.phase='FAILED';await dbRuntimeObserve();assert.match($('runtime-guidance').textContent,/FAILED/);
await refreshStatus();assert.equal(posts().length,1);
detail.phase='UNKNOWN';await dbRuntimeObserve();assert.match($('runtime-guidance').textContent,/explicit Reconcile/);await refreshStatus();assert.equal(posts().length,1);
await dbRuntimeAct('reconcile');assert.equal(posts().length,2);assert.equal(posts()[1].path,BASE+'/reconcile');assert.notEqual(posts()[1].options.headers['Idempotency-Key'],posts()[0].options.headers['Idempotency-Key']);
''')

    def test_lost_ack_retains_exact_intent_key_for_explicit_receipt(self):
        self.javascript(r'''
await start();actionRoute=async()=>{throw Error('response lost');};await dbRuntimeAct('chill');
const original=posts()[0],intent=dbRuntime.intent;assert.match($('runtime-message').textContent,/UNKNOWN/);assert.equal($('runtime-receipt').hidden,false);
await refreshStatus();await dbRuntimeAct('wake');assert.equal(posts().length,1);assert.equal(dbRuntime.intent,intent);
actionRoute=null;await dbRuntimeReceipt();const replay=posts()[1];assert.equal(replay.path,original.path);assert.equal(replay.options.body,original.options.body);assert.equal(replay.options.headers['Idempotency-Key'],original.options.headers['Idempotency-Key']);assert.equal(dbRuntime.intent,null);
''')

    def test_dirty_policy_keeps_cas_base_and_focus_across_poll_and_language(self):
        self.javascript(r'''
await start();const input=$('runtime-minutes');input.focus();input.value='47';input.oninput();
for(const lang of ['ja','en']){setLanguage(lang);await refreshStatus();assert.equal(input.value,'47');assert.equal(document.activeElement,input);assert.equal(dbRuntime.base,3);}
runtime.policy={minutes:12,revision:4};await refreshStatus();assert.equal(dbRuntime.base,3);assert.equal(input.value,'47');assert.equal(dbRuntime.conflict,true);assert.equal($('runtime-save').disabled,true);
await dbRuntimeAct('auto-chill');assert.equal(posts().length,0);await $('runtime-reload').onclick();assert.equal(input.value,'12');assert.equal(dbRuntime.base,4);assert.equal(dbRuntime.dirty,false);assert.equal(document.activeElement,input);
''')

    def test_policy_explicit_save_has_revision_and_zero_is_disabled(self):
        self.javascript(r'''
await start();$('runtime-minutes').value='0';$('runtime-minutes').oninput();await dbRuntimeAct('auto-chill');
assert.deepEqual(JSON.parse(posts()[0].options.body),{minutes:0,expected_revision:3});assert.equal(dbRuntime.dirty,true);assert.match($('runtime-policy').textContent,/15 min.*revision 3/);
detail={...detail,action:'WAREHOUSE_AUTO_CHILL',phase:'FINISHED'};await dbRuntimeObserve();runtime.policy={minutes:0,revision:4};await refreshStatus();assert.equal(dbRuntime.dirty,false);assert.equal($('runtime-minutes').value,'0');assert.match($('runtime-policy').textContent,/0 min.*revision 4/);
''')

    def test_policy_conflict_does_not_promote_cas_or_retry(self):
        self.javascript(r'''
await start();$('runtime-minutes').value='100';$('runtime-minutes').oninput();actionRoute=async()=>({http:409,body:{code:'REVISION_CONFLICT'}});await dbRuntimeAct('auto-chill');
assert.equal(dbRuntime.base,3);assert.equal(dbRuntime.conflict,true);assert.equal($('runtime-minutes').value,'100');assert.equal(dbRuntime.intent,null);await refreshStatus();assert.equal(posts().length,1);
''')

    def test_principal_change_and_late_ack_cannot_restore_operation(self):
        self.javascript(r'''
await start();const delayed=deferred();actionRoute=()=>delayed.promise;const old=dbRuntimeAct('wake');await tick();
session.username='next';await dbRefreshAccess();assert.equal(dbRuntime.intent,null);assert.equal(dbRuntime.operation,null);assert.equal($('runtime-wake').disabled,true);
delayed.resolve({operation_id:'old-operation',phase:'ACCEPTED'});await old;assert.equal(dbRuntime.operation,null);assert.equal(dbRuntime.pending,false);
''')

    def test_late_operation_detail_after_role_loss_is_ignored(self):
        self.javascript(r'''
await start();await dbRuntimeAct('wake');const delayed=deferred();detailRoute=()=>delayed.promise;const old=dbRuntimeObserve();await tick();
currentRole='data_engineer';dbApplyRole();delayed.resolve({...detail,phase:'FINISHED'});await old;
assert.equal(dbRuntime.operation,null);assert.equal($('runtime-wake').disabled,true);assert.equal(dbRuntime.dirty,false);
''')

    def test_cold_queued_poll_and_cancel_keep_original_context(self):
        self.javascript(r'''
await start();const draft=JSON.stringify(dbInput(dbCurrent())),pending=deferred();let first;
const prior=route;route=async(path,options)=>{
 if(path==='/api/v1/queries'){first=envelope(dbRuns.get('a'),{state:'QUEUED',backend_query_id:null,columns:[],data:[],next_uri:'/api/v1/queries/query-uuid/pages/pending-1'});return first;}
 if(path.endsWith('/pages/pending-1'))return pending.promise;
 if(path.endsWith('/cancel'))return {id:'query-uuid',state:'CANCELED'};
 return prior(path,options);
};
const run=dbRunQuery();await tick();await tick();assert.equal(dbRuns.get('a').state,'QUEUED');assert.match($('query-meta').textContent,/Waiting for shared LOCAL startup/);assert.equal($('cancel-query').disabled,false);
await dbCancelQuery();pending.resolve({...first,next_uri:'/api/v1/queries/query-uuid/pages/pending-2'});await run;
assert.equal(dbRuns.get('a').state,'CANCELED');assert.equal(JSON.stringify(dbInput(dbCurrent())),draft);assert.equal(posts().filter(c=>c.path===BASE+'/wake').length,0);assert.equal(posts().filter(c=>c.path==='/api/v1/queries').length,1);
''')

    def test_cold_pending_page_progression_finishes_without_sql_resubmission(self):
        self.javascript(r'''
await start();const prior=route;route=async(path,options)=>{
 const run=dbRuns.get('a');
 if(path==='/api/v1/queries')return envelope(run,{backend_query_id:null,state:'QUEUED',columns:[],data:[],next_uri:'/api/v1/queries/query-uuid/pages/one'});
 if(path.endsWith('/pages/one'))return envelope(run,{backend_query_id:null,state:'QUEUED',columns:[],data:[],next_uri:'/api/v1/queries/query-uuid/pages/two'});
 if(path.endsWith('/pages/two'))return envelope(run);
 return prior(path,options);
};await dbRunQuery();assert.equal(dbRuns.get('a').state,'FINISHED');assert.deepEqual(queryResult.rows,[[1]]);assert.equal(posts().filter(c=>c.path==='/api/v1/queries').length,1);
''')

    def test_legacy_actions_and_bears_remain_closed_and_section_switch_is_safe(self):
        self.javascript(r'''
await start();const draft=JSON.stringify(dbInput(dbCurrent()));await dbRuntimeAct('wake');const op=dbRuntime.operation;
activate($('tab-compute'));activate($('tab-warehouse'));assert.equal(dbRuntime.operation,op);assert.equal(JSON.stringify(dbInput(dbCurrent())),draft);
for(const id of ['compute-sre-check','compute-policy-check','copilot-generate'])assert.equal($(id).disabled,true);
for(const path of ['/api/warehouse/wake','/api/warehouse/chill','/api/warehouse/auto-chill','/api/lake/dbt/build'])await assert.rejects(api(path,{}));assert.equal(posts().length,1);
setLanguage('ja');assert.match($('runtime-guidance').textContent,/受付済み/);assert.match($('runtime-wake').title||$('quick-wake').title,/全 DB/);
''')

    def test_unknown_missing_dto_and_unavailable_route_fail_safe(self):
        self.javascript(r'''
await start();runtimeRoute=async()=>({warehouse:'LOCAL',physical_state:'active',ready_replicas:1});await refreshStatus();assert.equal($('runtime-wake').disabled,true);assert.match($('runtime-proof').textContent,/unverified/);
runtimeRoute=async()=>({http:503,body:{error:'WAREHOUSE_UNAVAILABLE'}});await refreshStatus();assert.equal($('runtime-wake').disabled,true);assert.match($('runtime-message').textContent,/unavailable/);assert.equal(posts().length,0);
''')

    def test_malformed_action_choices_and_policy_revision_fail_safe(self):
        self.javascript(r"""
await start();runtime.available_actions='wake';await refreshStatus();assert.equal($('runtime-wake').disabled,true);await dbRuntimeAct('wake');assert.equal(posts().length,0);
runtime.available_actions=['wake','auto-chill'];runtime.policy.revision=Number.MAX_SAFE_INTEGER+1;runtime.engine_verified=true;runtime.engine_verified_at='';runtime.physical_state='active';await refreshStatus();assert.equal($('runtime-minutes').disabled,true);assert.match($('runtime-proof').textContent,/unverified/);
""")

    def test_overlapping_details_are_bounded_and_unknown_phase_is_conservative(self):
        self.javascript(r"""
await start();await dbRuntimeAct('wake');const delayed=deferred();let reads=0;detailRoute=()=>{reads++;return delayed.promise;};
const first=dbRuntimeObserve();await tick();await dbRuntimeObserve();assert.equal(reads,1);delayed.resolve({...detail,phase:'FINISHED'});await first;assert.equal(dbRuntime.operation.phase,'FINISHED');
detailRoute=async()=>({...detail,phase:'UNRECOGNIZED'});await dbRuntimeObserve();assert.equal(dbRuntime.operation.phase,'FINISHED');assert.match($('runtime-observation').textContent,/observation unavailable.*Completed/);assert.match($('runtime-guidance').textContent,/Operation finished/);assert.equal(posts().length,1);
""")

    def test_own_completed_policy_save_reconciles_observed_revision(self):
        self.javascript(r"""
await start();runtime.operation_phase='RUNNING';runtime.operation_action='WAREHOUSE_WAKE';await refreshStatus();
assert.equal($('runtime-wake').disabled,true);assert.equal($('runtime-minutes').disabled,false);
$('runtime-minutes').value='0';$('runtime-minutes').oninput();await dbRuntimeAct('auto-chill');
detail={...detail,action:'WAREHOUSE_AUTO_CHILL',phase:'FINISHED'};runtime.policy={minutes:0,revision:4};await refreshStatus();
assert.equal(dbRuntime.conflict,false);assert.equal(dbRuntime.dirty,false);assert.equal(dbRuntime.base,4);assert.equal($('runtime-minutes').value,'0');assert.match($('runtime-policy').textContent,/0 min.*revision 4/);
""")

    def test_real_dto_202_running_succeeded_requires_separate_engine_proof(self):
        self.javascript(r"""
await start();const draft=JSON.stringify(dbInput(dbCurrent())),sql=$('sql').value;
assert.equal($('run-query').disabled,false);await $('quick-wake').onclick();
assert.equal(dbRuntime.operation.phase,'ACCEPTED');assert.match($('runtime-guidance').textContent,/completion is not verified/);
runtime.operation_id=detail.id;runtime.operation_action='WAREHOUSE_WAKE';runtime.operation_phase='RUNNING';runtime.physical_state='starting';await refreshStatus();
assert.equal(dbRuntime.operation.phase,'RUNNING');assert.match($('runtime-guidance').textContent,/Starting shared LOCAL/);
detail.phase='SUCCEEDED';detail.timestamps.finished='2026-10-29T01:03:00Z';runtime.operation_phase='SUCCEEDED';await refreshStatus();
assert.equal(dbRuntime.operation.phase,'SUCCEEDED');assert.match($('runtime-guidance').textContent,/Operation finished/);assert.doesNotMatch($('runtime-guidance').textContent,/UNKNOWN/);assert.match($('runtime-physical').textContent,/Starting/);assert.match($('runtime-proof').textContent,/unverified/);
runtime.physical_state='active';runtime.replicas=1;runtime.ready_replicas=1;await refreshStatus();assert.match($('runtime-proof').textContent,/unverified/);
runtime.engine_verified=true;runtime.engine_verified_at='2026-10-29T01:04:00Z';await refreshStatus();assert.match($('runtime-proof').textContent,/Engine verified at/);
assert.equal($('run-query').disabled,false);assert.equal(JSON.stringify(dbInput(dbCurrent())),draft);assert.equal($('sql').value,sql);assert.equal(posts().length,1);
""")

    def test_real_policy_succeeded_reconciles_stored_cas_revision(self):
        self.javascript(r"""
await start();$('runtime-minutes').value='0';$('runtime-minutes').oninput();await dbRuntimeAct('auto-chill');assert.equal(dbRuntime.operation.phase,'ACCEPTED');
detail.action='WAREHOUSE_AUTO_CHILL';detail.phase='RUNNING';await dbRuntimeObserve();assert.equal(dbRuntime.dirty,true);
runtime.policy={minutes:0,revision:4};detail.phase='SUCCEEDED';await refreshStatus();
assert.equal(dbRuntime.operation.phase,'SUCCEEDED');assert.equal(dbRuntime.dirty,false);assert.equal(dbRuntime.conflict,false);assert.equal(dbRuntime.base,4);assert.equal($('runtime-minutes').value,'0');assert.match($('runtime-policy').textContent,/0 min.*revision 4/);assert.match($('runtime-proof').textContent,/unverified/);assert.equal(posts().length,1);
""")

    def test_global_safe_operation_fields_distinguish_starting_stopping(self):
        self.javascript(r"""
await start();await dbRuntimeAct('wake');detail.phase='SUCCEEDED';await dbRuntimeObserve();
runtime.operation_id='global-chill-two';runtime.operation_action='WAREHOUSE_CHILL';runtime.operation_phase='RUNNING';runtime.actor='private-actor';runtime.private_metadata={path:'/private/operator-secret'};await refreshStatus();
assert.match($('runtime-guidance').textContent,/Stopping shared LOCAL/);assert.match($('runtime-receipt-info').textContent,/global-chill-two/);assert.equal(dbRuntime.operation.id,'operation-one');
runtime.operation_id='global-wake-three';runtime.operation_action='WAREHOUSE_WAKE';await refreshStatus();assert.match($('runtime-guidance').textContent,/Starting shared LOCAL/);
delete runtime.operation_action;await refreshStatus();assert.doesNotMatch($('runtime-guidance').textContent,/Starting shared|Stopping shared/);
runtime.operation_action='SOMETHING_WAKE_LIKE';await refreshStatus();assert.doesNotMatch($('runtime-guidance').textContent,/Starting shared|Stopping shared/);
const visible=$('runtime-panel').descendants().map(n=>n.textContent).join(' ');assert.doesNotMatch(visible,/private-actor|operator-secret|private_metadata/);assert.equal(posts().length,1);
""")

    def test_only_exact_success_enums_complete_failed_unknown_stay_conservative(self):
        self.javascript(r"""
await start();runtime.physical_state='active';runtime.replicas=runtime.ready_replicas=1;
for(const phase of ['FAILED','UNKNOWN','SUCCESS','succeeded','SUCCEEDED_WITH_WARNINGS','DONE']){
 runtime.operation_id='external-operation';runtime.operation_action='WAREHOUSE_WAKE';runtime.operation_phase=phase;await refreshStatus();
 assert.match($('runtime-guidance').textContent,phase==='FAILED'?/FAILED/:/UNKNOWN/);assert.doesNotMatch($('runtime-guidance').textContent,/Operation finished/);assert.match($('runtime-proof').textContent,/unverified/);
}
await dbRuntimeAct('wake');for(const phase of ['FAILED','UNKNOWN','SUCCESS']){detail.phase=phase;await dbRuntimeObserve();assert.match($('runtime-guidance').textContent,phase==='FAILED'?/FAILED/:/UNKNOWN/);assert.match($('runtime-proof').textContent,/unverified/);if(phase==='SUCCESS'){assert.equal(dbRuntime.operation.phase,'UNKNOWN');assert.match($('runtime-observation').textContent,/observation unavailable/);}}
assert.equal(posts().length,1);
""")

    def test_completed_policy_then_new_draft_survives_refresh_poll_language(self):
        self.javascript(r"""
await start();$('runtime-minutes').value='0';$('runtime-minutes').oninput();await dbRuntimeAct('auto-chill');
detail.action='WAREHOUSE_AUTO_CHILL';detail.phase='SUCCEEDED';runtime.policy={minutes:0,revision:4};await refreshStatus();assert.equal(dbRuntime.operation.policyReconciled,true);
const input=$('runtime-minutes');input.focus();input.value='73';input.oninput();assert.equal(dbRuntime.base,4);
for(const lang of ['ja','en']){setLanguage(lang);await $('runtime-refresh').onclick();await refreshStatus();assert.equal(input.value,'73');assert.equal(dbRuntime.base,4);assert.equal(dbRuntime.dirty,true);assert.equal(dbRuntime.conflict,false);assert.equal(document.activeElement,input);}
runtime.policy={minutes:22,revision:5};await $('runtime-refresh').onclick();await dbRuntimeObserve();assert.equal(input.value,'73');assert.equal(dbRuntime.base,4);assert.equal(dbRuntime.dirty,true);assert.equal(dbRuntime.conflict,true);assert.equal(document.activeElement,input);assert.equal($('runtime-save').disabled,true);assert.equal(posts().length,1);
""")

    def test_terminal_observation_errors_preserve_confirmed_phase_and_recover(self):
        self.javascript(r"""
await start();await dbRuntimeAct('wake');detail.phase='SUCCEEDED';await dbRuntimeObserve();
for(const fault of [()=>{throw Error('lost detail');},()=>({malformed:true}),()=>({...detail,phase:'SUCCESS'}),()=>({...detail,id:'wrong-id'})]){
 detailRoute=fault;await dbRuntimeObserve();assert.equal(dbRuntime.operation.phase,'SUCCEEDED');assert.equal(dbRuntime.operation.observationUnavailable,true);assert.match($('runtime-observation').textContent,/observation unavailable.*Completed/);assert.doesNotMatch($('runtime-guidance').textContent,/UNKNOWN/);
 runtime.operation_id=detail.id;runtime.operation_action=detail.action;runtime.operation_phase='SUCCEEDED';await refreshStatus();assert.match($('runtime-operation').textContent,/Completed/);assert.match($('runtime-proof').textContent,/unverified/);
}
setLanguage('ja');assert.match($('runtime-observation').textContent,/観測を取得できません/);setLanguage('en');
detailRoute=null;await $('runtime-refresh').onclick();assert.equal(dbRuntime.operation.phase,'SUCCEEDED');assert.equal(dbRuntime.operation.observationUnavailable,false);assert.equal($('runtime-observation').textContent,'');assert.equal(posts().length,1);
""")

    def test_pending_observation_error_keeps_guards_without_retry_or_success(self):
        self.javascript(r"""
await start();await dbRuntimeAct('wake');await dbRuntimeObserve();assert.equal(dbRuntime.operation.phase,'RUNNING');detailRoute=()=>{throw Error('lost detail');};await dbRuntimeObserve();
assert.equal(dbRuntime.operation.phase,'RUNNING');assert.match($('runtime-observation').textContent,/Last confirmed phase: Running/);assert.doesNotMatch($('runtime-guidance').textContent,/Operation finished|UNKNOWN/);assert.equal($('runtime-wake').disabled,true);assert.equal($('runtime-chill').disabled,true);assert.equal($('runtime-receipt').hidden,true);
await refreshStatus();await dbRuntimeAct('chill');assert.equal(posts().length,1);assert.equal(dbRuntime.intent,null);assert.equal(dbRuntime.operation.phase,'RUNNING');
detailRoute=null;detail.phase='UNKNOWN';await dbRuntimeObserve();assert.equal(dbRuntime.operation.phase,'UNKNOWN');assert.equal(dbRuntime.operation.observationUnavailable,false);assert.match($('runtime-guidance').textContent,/Operation UNKNOWN/);
""")

    def test_auth_loss_purges_known_receipts_instead_of_preserving_on_read_error(self):
        self.javascript(r"""
await start();for(const status of [401,403]){
 await refreshStatus();await dbRuntimeAct('wake');detail.phase='SUCCEEDED';await dbRuntimeObserve();assert.equal(dbRuntime.operation.phase,'SUCCEEDED');
 $('runtime-minutes').value='80';$('runtime-minutes').oninput();detailRoute=()=>({http:status,body:{code:'AUTHENTICATION_REQUIRED'}});await dbRuntimeObserve();
 assert.equal(dbRuntime.operation,null);assert.equal(dbRuntime.intent,null);assert.equal(dbRuntime.dirty,false);assert.equal(dbRuntime.base,null);assert.equal($('runtime-minutes').value,'');assert.equal($('runtime-receipt-info').textContent,'');assert.equal($('runtime-observation').textContent,'');assert.equal($('runtime-wake').disabled,true);detailRoute=null;
}
await refreshStatus();await dbRuntimeAct('wake');detail.phase='SUCCEEDED';await dbRuntimeObserve();session.maximum_role='bi_viewer';await dbRuntimeObserve();assert.equal(dbRuntime.operation,null);assert.equal($('runtime-observation').textContent,'');assert.equal($('runtime-wake').disabled,true);
""")

    def test_real_nested_blocker_zeroes_enable_chill_without_summing(self):
        self.javascript(r"""
await start();assert.equal($('runtime-chill').disabled,false);assert.equal($('quick-chill').disabled,false);assert.equal($('chill').disabled,false);
assert.equal($('runtime-blockers').textContent,'Blockers: Runtime operations: 0 · Admitted work: 0 · Unresolved admissions (included in admitted work): 0 · Creation requests: 0 · Creation effects: 0 · Creation roots: 0');
setLanguage('ja');assert.match($('runtime-blockers').textContent,/結果不明の受付（受付済み処理の内数）: 0/);assert.match($('runtime-blockers').textContent,/DB 作成要求: 0.*適用処理: 0.*基盤処理: 0/);assert.doesNotMatch($('runtime-blockers').textContent,/取得できません/);
await $('quick-chill').onclick();assert.equal(posts().length,1);assert.equal(posts()[0].path,BASE+'/chill');
""")

    def test_each_nonzero_creation_category_blocks_only_manual_chill(self):
        self.javascript(r"""
await start();for(const key of ['requests','effects','roots']){
 runtime.blockers.creation={requests:0,effects:0,roots:0};runtime.blockers.creation[key]=2;await refreshStatus();
 assert.equal($('runtime-chill').disabled,true);assert.equal($('quick-chill').disabled,true);assert.equal($('chill').disabled,true);assert.equal($('runtime-wake').disabled,false);assert.equal($('runtime-reconcile').disabled,false);
 assert.match($('runtime-blockers').textContent,/Creation .*: 2/);await dbRuntimeAct('chill');assert.equal(posts().length,0);
}
runtime.blockers.creation={requests:0,effects:0,roots:0};runtime.blockers.admissions=3;runtime.blockers.unknown=2;await refreshStatus();assert.match($('runtime-blockers').textContent,/Admitted work: 3.*included in admitted work\): 2/);assert.equal($('runtime-chill').disabled,true);
runtime.blockers.admissions=runtime.blockers.unknown=0;runtime.blockers.runtime_operations=1;await refreshStatus();assert.equal($('runtime-chill').disabled,true);
""")

    def test_malformed_nested_counts_remain_unavailable_and_cannot_chill(self):
        self.javascript(r"""
await start();const valid=structuredClone(runtime.blockers);
for(const value of [null,[],{},-1,0.5,'0',false,Infinity,NaN,Number.MAX_SAFE_INTEGER+1,undefined]){
 runtime.blockers=structuredClone(valid);runtime.blockers.creation.effects=value;await refreshStatus();
 assert.match($('runtime-blockers').textContent,/observation unavailable/);assert.equal($('runtime-chill').disabled,true);assert.equal($('runtime-wake').disabled,false);assert.equal($('runtime-reconcile').disabled,false);await dbRuntimeAct('chill');
}
for(const value of [null,[],{},0,{requests:0,effects:0},{requests:0,effects:0,roots:0,new_blocker:1}]){runtime.blockers={...valid,creation:value};await refreshStatus();assert.equal($('runtime-chill').disabled,true);}
runtime.blockers={...valid,unknown:1};await refreshStatus();assert.match($('runtime-blockers').textContent,/observation unavailable/);assert.equal($('runtime-chill').disabled,true);assert.equal(posts().length,0);
""")

    def test_role_restore_refreshes_nested_counts_without_granting_viewer_actions(self):
        self.javascript(r"""
await start();currentRole='bi_viewer';dbApplyRole();runtime.available_actions=[];await refreshStatus();assert.match($('runtime-blockers').textContent,/Creation roots: 0/);
for(const id of ['runtime-chill','runtime-wake','runtime-reconcile'])assert.equal($(id).disabled,true);
currentRole='admin';dbApplyRole();runtime.available_actions=['wake','chill','auto-chill','reconcile'];await dbRefreshContext();await refreshStatus();
for(const id of ['runtime-chill','runtime-wake','runtime-reconcile'])assert.equal($(id).disabled,false);
assert.equal(dbRuntime.dirty,false);assert.equal(dbRuntime.operation,null);assert.equal(dbRuntime.intent,null);assert.equal($('runtime-minutes').value,'15');
""")

    def test_ja_persistent_enum_labels_global_receipt_and_historical_failure(self):
        self.javascript(r"""
await start();runtime.physical_state='active';runtime.engine_verified=true;runtime.engine_verified_at='2026-10-29T01:03:00Z';runtime.operation_id='global-past-chill';runtime.operation_action='WAREHOUSE_CHILL';runtime.operation_phase='FAILED';await refreshStatus();
const physical=$('runtime-physical'),operation=$('runtime-operation'),receipt=$('runtime-receipt-info');setLanguage('ja');
assert.equal(physical.textContent,'物理状態 稼働中 · 観測 2026-10-29T01:02:03Z');assert.equal(operation.textContent,'共有操作: 休止 · 失敗');assert.match($('runtime-proof').textContent,/エンジン検証済み/);assert.equal(receipt.textContent,'操作 global-past-chill');assert.equal($('runtime-reconcile').textContent,'状態を再検証');assert.match($('runtime-reconcile').title,/SQL は再送しません/);
for(const [state,label] of [['chilled','休止中'],['starting','起動中'],['unknown','不明']]){runtime.physical_state=state;await refreshStatus();assert.match(physical.textContent,new RegExp(label));assert.doesNotMatch($('runtime-proof').textContent,/エンジン検証済み/);}
for(const [action,label] of [['WAREHOUSE_WAKE','起動'],['WAREHOUSE_CHILL','休止'],['WAREHOUSE_AUTO_CHILL','自動休止'],['WAREHOUSE_RECONCILE','状態を再検証']]){runtime.operation_action=action;runtime.operation_phase='RUNNING';await refreshStatus();assert.equal(operation.textContent,'共有操作: '+label+' · 実行中');}
for(const [phase,label] of [['ACCEPTED','受付済み'],['SUCCEEDED','完了'],['FAILED','失敗'],['UNKNOWN','不明'],['RECONCILED','状態検証済み']]){runtime.operation_phase=phase;await refreshStatus();assert.match(operation.textContent,new RegExp(label));}
runtime.operation_action='RAW_UNKNOWN_ACTION';runtime.operation_phase='SUCCESS_LIKE';await refreshStatus();assert.equal(operation.textContent,'共有操作: 不明な操作 · 不明');
setLanguage('en');assert.equal($('runtime-physical'),physical);assert.equal($('runtime-operation'),operation);assert.equal($('runtime-receipt-info'),receipt);assert.equal(operation.textContent,'Shared operation: Unknown action · Unknown');assert.equal(receipt.textContent,'Operation global-past-chill');
runtime.physical_state='arbitrary';await refreshStatus();assert.equal($('quick-warehouse-state').textContent,'Unknown');assert.match(physical.textContent,/Unknown/);assert.match($('runtime-proof').textContent,/unverified/);
""")

    def test_database_hides_whole_legacy_compute_panels_and_keeps_ids(self):
        self.javascript(r"""
const legacyStatus=$('compute-legacy-status'),legacyPolicy=$('compute-legacy-auto-chill');legacyStatus.append($('compute-summary'),$('compute-warehouse'),$('compute-components'));legacyPolicy.append($('auto-chill-minutes'),$('save-auto-chill'),$('auto-chill-state'));$('compute').append(legacyStatus,legacyPolicy);
assert.equal(legacyStatus.hidden,false);assert.equal(legacyPolicy.hidden,false);await start();
assert.equal(legacyStatus.hidden,true);assert.equal(legacyPolicy.hidden,true);assert.equal($('compute-warehouse').parentElement,legacyStatus);assert.equal($('auto-chill-minutes').parentElement,legacyPolicy);assert.equal($('runtime-panel').hidden,false);
for(const lang of ['ja','en']){setLanguage(lang);await $('runtime-refresh').onclick();const notice=t('P-only read-only Platform observations are available. Legacy workload checks, policy audits and other Bear actions remain unavailable.');assert.equal($('database-compute-notice').textContent,notice);assert.equal($('compute').descendants().filter(n=>n.textContent===notice).length,1);assert.equal(legacyStatus.hidden,true);assert.equal(legacyPolicy.hidden,true);}
assert.equal($('compute-sre-check').disabled,true);assert.equal($('compute-policy-check').disabled,true);assert.equal($('copilot-generate').disabled,true);assert.equal($('runtime-minutes').disabled,false);
""")

    def test_legacy_profile_status_does_not_promise_runtime_or_auto_resume(self):
        self.javascript(r"""
api=async path=>(await fetch(path)).json();runtimeRoute=async()=>({http:503,body:{error:'WAREHOUSE_UNAVAILABLE'}});const prior=route;route=async(path,options)=>path==='/api/status'?{status:'healthy',data_mode:'database'}:prior(path,options);
await start();const draft=JSON.stringify(dbInput(dbCurrent())),sql=$('sql').value;
for(const lang of ['en','ja']){setLanguage(lang);await refreshStatus();assert.equal(dbRuntimeSupport(),'unsupported');assert.equal($('runtime-guidance').textContent,t('Shared runtime controls and SQL auto-resume are unsupported for this profile. Use the existing operator workflow.'));assert.equal($('quick-status-error').textContent,$('runtime-guidance').textContent);assert.doesNotMatch($('runtime-guidance').textContent,/automatically|自動起動します/);for(const id of ['runtime-wake','runtime-chill','runtime-reconcile','runtime-save'])assert.equal($(id).disabled,true);}
assert.equal(JSON.stringify(dbInput(dbCurrent())),draft);assert.equal($('sql').value,sql);assert.equal(posts().length,0);
""")

    def test_unknown_support_and_temporary_errors_do_not_mean_unsupported(self):
        self.javascript(r"""
api=async path=>(await fetch(path)).json();runtimeRoute=async()=>{throw Error('temporary status failure');};const prior=route;route=async(path,options)=>{if(path==='/api/status')throw Error('temporary fallback failure');return prior(path,options);};await start();
assert.equal(dbRuntimeSupport(),null);assert.match($('runtime-guidance').textContent,/support is not confirmed/);assert.doesNotMatch($('runtime-guidance').textContent,/unsupported|automatically/);
runtimeRoute=null;await refreshStatus();assert.equal(dbRuntimeSupport(),'logical');assert.match($('runtime-guidance').textContent,/starts shared LOCAL automatically/);
runtimeRoute=async()=>({http:503,body:{error:'WAREHOUSE_UNAVAILABLE'}});await refreshStatus();assert.equal(dbRuntimeSupport(),'logical');assert.match($('runtime-message').textContent,/unavailable/);assert.match($('runtime-guidance').textContent,/starts shared LOCAL automatically/);assert.equal($('runtime-wake').disabled,true);
setLanguage('ja');assert.match($('runtime-guidance').textContent,/自動起動します/);assert.doesNotMatch($('runtime-guidance').textContent,/未対応/);assert.equal(posts().length,0);
""")

    def test_logical_status_confirms_support_when_runtime_observation_fails(self):
        self.javascript(r"""
api=async path=>(await fetch(path)).json();runtimeRoute=async()=>({http:503,body:{error:'WAREHOUSE_UNAVAILABLE'}});const prior=route;let fail=false;route=async(path,options)=>{if(path==='/api/status'){if(fail)throw Error('offline');return {status:'degraded',data_mode:'database',creation_mode:'logical_v1',warehouse:{state:'unavailable'}};}return prior(path,options);};await start();
assert.equal(dbRuntimeSupport(),'logical');assert.match($('runtime-guidance').textContent,/starts shared LOCAL automatically/);assert.equal($('runtime-wake').disabled,true);
fail=true;await refreshStatus();setLanguage('ja');assert.equal(dbRuntimeSupport(),'logical');assert.doesNotMatch($('runtime-guidance').textContent,/未対応/);
""")

    def test_existing_creation_capability_confirms_profile_and_survives_read_loss(self):
        self.javascript(r"""
runtimeRoute=async()=>({http:503,body:{error:'WAREHOUSE_UNAVAILABLE'}});await start();const prior=route;let cap={environment_id:'env-runtime',warehouse_id:'LOCAL',warehouse_revision:3,configured:true,available:true,blocker:null};let fail=false;
route=async(path,options)=>{if(path==='/api/v1/admin/database-creations/capabilities'){if(fail)throw Error('offline');return structuredClone(cap);}return prior(path,options);};
await dbCreationRequest('/api/v1/admin/database-creations/capabilities');assert.equal(dbRuntimeSupport(),'unsupported');assert.match($('runtime-guidance').textContent,/existing operator workflow/);
cap={...cap,creation_mode:'logical_v1',warehouse_state:'STOPPED'};await dbCreationRequest('/api/v1/admin/database-creations/capabilities');assert.equal(dbRuntimeSupport(),'logical');assert.match($('runtime-guidance').textContent,/starts shared LOCAL automatically/);
fail=true;await assert.rejects(dbCreationRequest('/api/v1/admin/database-creations/capabilities'));assert.equal(dbRuntimeSupport(),'logical');assert.equal($('runtime-wake').disabled,true);assert.equal(posts().length,0);
""")

    def test_runtime_sections_have_accessible_labels_and_stable_native_editor(self):
        self.javascript(r"""
await start();const panel=$('runtime-panel'),input=$('runtime-minutes');
assert.equal(panel.getAttribute('role'),'region');assert.equal(panel.getAttribute('aria-labelledby'),'runtime-title');
assert.equal($('runtime-refresh').parentElement,$('runtime-heading'));assert.equal($('runtime-title').parentElement,$('runtime-heading'));
for(const [id,group] of [['runtime-physical','runtime-physical-group'],['runtime-proof','runtime-physical-group'],['runtime-operation','runtime-operation-group'],['runtime-blockers','runtime-safety-section'],['runtime-policy','runtime-policy-section'],['runtime-draft','runtime-policy-section']]){assert.equal($(id).parentElement,$(group));assert.equal($(id).getAttribute('role'),'status');assert.equal($(id).getAttribute('aria-atomic'),'true');}
for(const name of ['controls','safety','policy'])assert.equal($('runtime-'+name+'-section').getAttribute('aria-labelledby'),'runtime-'+name+'-title');
assert.equal($('runtime-minutes-label').htmlFor,'runtime-minutes');assert.equal(input.type,'number');assert.equal(input.min,'0');assert.equal(input.max,'1440');assert.equal(input.step,'1');assert.equal(input.parentElement,$('runtime-minutes-field'));
for(const id of input.getAttribute('aria-describedby').split(' '))assert.ok(panel.descendants().includes($(id)),id);
input.focus();input.value='37';input.oninput();const base=dbRuntime.base;
for(const lang of ['ja','en']){setLanguage(lang);await $('runtime-refresh').onclick();assert.equal($('runtime-minutes'),input);assert.equal(document.activeElement,input);assert.equal(input.value,'37');assert.equal(dbRuntime.base,base);assert.equal($('runtime-save').disabled,false);}
runtime.policy={minutes:90,revision:4};await refreshStatus();assert.equal(input.value,'37');assert.equal(dbRuntime.base,base);assert.equal(dbRuntime.conflict,true);assert.equal(document.activeElement,input);assert.equal($('runtime-save').disabled,true);
""")

    def test_database_uses_one_refresh_and_hides_legacy_compute_ctas(self):
        self.javascript(r"""
const overview=$('compute-legacy-overview'),legacyRefresh=$('compute-refresh'),sre=$('compute-sre-check'),policy=$('compute-policy-check');overview.append(legacyRefresh,sre,policy);$('compute').append(overview);
assert.equal(overview.hidden,false);assert.equal(legacyRefresh.hidden,false);assert.equal(sre.hidden,false);assert.equal(policy.hidden,false);
await start();assert.equal(overview.hidden,true);for(const node of [legacyRefresh,sre,policy])assert.equal(node.hidden,true);assert.equal(sre.disabled,true);assert.equal(policy.disabled,true);assert.equal($('runtime-refresh').hidden,false);
assert.equal($('compute-refresh'),legacyRefresh);assert.equal($('compute-sre-check'),sre);assert.equal($('compute-policy-check'),policy);
for(const lang of ['ja','en']){setLanguage(lang);await $('runtime-refresh').onclick();assert.equal(overview.hidden,true);assert.equal($('runtime-refresh').hidden,false);assert.equal($('database-compute-notice').textContent,t('P-only read-only Platform observations are available. Legacy workload checks, policy audits and other Bear actions remain unavailable.'));}
""")

    def test_grouped_receipt_recovery_stays_hidden_until_uncertain_post(self):
        self.javascript(r"""
await start();const receipt=$('runtime-receipt');assert.equal(receipt.parentElement,$('runtime-actions'));assert.equal(receipt.hidden,true);
actionRoute=()=>{throw Error('lost ACK');};await dbRuntimeAct('wake');const key=dbRuntime.intent.key;assert.equal(receipt.hidden,false);assert.equal(receipt.disabled,false);assert.match($('runtime-message').textContent,/UNKNOWN/);
$('runtime-refresh').focus();for(const lang of ['ja','en']){setLanguage(lang);await refreshStatus();assert.equal(receipt.hidden,false);assert.equal(dbRuntime.intent.key,key);assert.equal(posts().length,1);assert.equal(document.activeElement,$('runtime-refresh'));}
actionRoute=null;await receipt.onclick();assert.equal(receipt.hidden,true);assert.equal(dbRuntime.operation.phase,'ACCEPTED');assert.equal(posts()[1].options.headers['Idempotency-Key'],key);
""")


if __name__ == '__main__':
    unittest.main()
