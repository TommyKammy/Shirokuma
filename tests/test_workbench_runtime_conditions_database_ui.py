"""Cached runtime condition explanations through the production UI handlers."""
import unittest

import test_workbench_database_ui as database_ui
from test_workbench_warehouse_runtime_ui import SETUP


class RuntimeConditionsUI(unittest.TestCase):
    def javascript(self, body):
        database_ui.DatabaseUI.javascript(self, SETUP + '\n' + body)

    def test_profile_authority_and_captured_action_priority(self):
        self.javascript(r"""
await start();const saved=dbRuntime.status;
for(const support of ['unsupported',null]){dbRuntime.support=support;dbRuntimeRender();assert.match(dbRuntimeCondition('wake'),support===null?/unconfirmed/:/does not support/);assert.equal($('runtime-wake').disabled,true);}
dbRuntime.support='logical';
for(const [platform,maximum,selected] of [[false,'admin','admin'],[true,'data_engineer','admin'],[true,'admin','bi_viewer']]){dbAdmin.platform=platform;dbMaximumRole=maximum;currentRole=selected;dbRuntimeRender();assert.match(dbRuntimeCondition('wake'),/platform authority/);assert.equal($('runtime-wake').disabled,true);}
dbAdmin.platform=true;dbMaximumRole='admin';currentRole='admin';dbRuntime.status=null;dbRuntimeRender();assert.match(dbRuntimeCondition('wake'),/No runtime observation/);
dbRuntime.status={...saved,available_actions:['wake']};dbRuntimeRender();assert.match(dbRuntimeCondition('chill'),/not advertised/);assert.match(dbRuntimeCondition('wake'),/Selectable/);assert.equal(posts().length,0);
""")

    def test_pending_original_unknown_and_local_operation_priority(self):
        self.javascript(r"""
await start();dbRuntime.intent={action:'wake',body:{},key:'original'};dbRuntime.pending=true;dbRuntimeRender();assert.match(dbRuntimeCondition('wake'),/in flight/);
dbRuntime.pending=false;dbRuntime.operation={phase:'RUNNING'};dbRuntimeRender();assert.match(dbRuntimeCondition('wake'),/Original acceptance is unconfirmed/);assert.match($('runtime-condition-receipt').textContent,/may POST the original body with the same key/);assert.equal($('runtime-receipt').disabled,false);
dbRuntime.intent=null;for(const phase of ['ACCEPTED','RUNNING']){dbRuntime.operation={phase};dbRuntimeRender();assert.match(dbRuntimeCondition('reconcile'),/local operation/);assert.equal($('runtime-save').disabled,true);}
assert.equal(posts().length,0);
""")

    def test_shared_phase_does_not_gate_policy_controls(self):
        self.javascript(r"""
await start();$('runtime-minutes').value='20';$('runtime-minutes').oninput();
for(const phase of ['ACCEPTED','RUNNING','UNKNOWN',null]){dbRuntime.status.operation_phase=phase;dbRuntimeRender();for(const action of ['wake','chill','reconcile'])assert.equal($('runtime-'+action).disabled,['ACCEPTED','RUNNING'].includes(phase));assert.equal($('runtime-save').disabled,false);assert.equal($('runtime-reload').disabled,false);assert.equal($('runtime-minutes').disabled,false);assert.match(dbRuntimeCondition('save'),/Selectable/);}
""")

    def test_disabled_parity_matrix_for_all_existing_controls(self):
        self.javascript(r"""
await start();const original=structuredClone(dbRuntime.status);
for(const allowed of [true,false])for(const pending of [true,false])for(const local of [null,'ACCEPTED','RUNNING','UNKNOWN'])for(const shared of [null,'ACCEPTED','RUNNING','UNKNOWN'])for(const count of [0,1]){
 dbRuntime.status={...structuredClone(original),available_actions:allowed?original.available_actions:[],operation_phase:shared};dbRuntime.status.blockers.admissions=count;dbRuntime.pending=pending;dbRuntime.intent=null;dbRuntime.operation=local?{phase:local}:null;dbRuntime.dirty=true;dbRuntime.base=3;dbRuntime.conflict=false;$('runtime-minutes').value='20';dbRuntimeRender();
 const locked=pending||['ACCEPTED','RUNNING'].includes(local);
 for(const action of ['wake','chill','reconcile']){const disabled=!allowed||locked||action==='chill'&&count>0||['ACCEPTED','RUNNING'].includes(shared);for(const id of ['runtime-'+action,...(action==='reconcile'?[]:[action,'quick-'+action])])assert.equal($(id).disabled,disabled,id);}
 for(const id of ['runtime-save','runtime-minutes','runtime-reload'])assert.equal($(id).disabled,!allowed||locked,id);
 assert.equal($('runtime-receipt').disabled,true);assert.equal($('runtime-receipt').hidden,true);
}
dbRuntime.status=structuredClone(original);dbRuntime.pending=false;dbRuntime.intent=null;dbRuntime.operation=null;dbRuntime.dirty=true;dbRuntime.conflict=false;$('runtime-minutes').value='20';
for(const base of [null,undefined,NaN,1.5,'3']){dbRuntime.base=base;dbRuntimeRender();const oldDisabled=!dbRuntimeAllowed('auto-chill')||dbRuntimeLocked()||false||!dbRuntime.dirty||dbRuntime.conflict;assert.equal($('runtime-save').disabled,oldDisabled);assert.equal(oldDisabled,false);assert.match(dbRuntimeCondition('save'),/Selectable/);await dbRuntimeAct('auto-chill');assert.equal(posts().length,0);}
""")

    def test_each_of_six_chill_categories_is_explained_only_for_chill(self):
        self.javascript(r"""
await start();const zero=structuredClone(dbRuntime.status.blockers);$('runtime-minutes').value='20';$('runtime-minutes').oninput();
for(const [path,label] of [[['runtime_operations'],'Runtime operations'],[['admissions'],'Admitted work'],[['unknown'],'Unresolved admissions'],[['creation','requests'],'Creation requests'],[['creation','effects'],'Creation effects'],[['creation','roots'],'Creation roots']]){
 const counts=structuredClone(zero);if(path.length===2)counts.creation[path[1]]=1;else counts[path[0]]=1;if(path[0]==='unknown')counts.admissions=1;dbRuntime.status.blockers=counts;dbRuntimeRender();assert.equal($('runtime-chill').disabled,true);assert.ok($('runtime-condition-chill').textContent.includes(label));for(const id of ['runtime-wake','runtime-save','runtime-reconcile'])assert.equal($(id).disabled,false,id);
}
""")

    def test_malformed_partial_and_unknown_count_invariants_only_gate_chill(self):
        self.javascript(r"""
await start();const zero=structuredClone(dbRuntime.status.blockers);$('runtime-minutes').value='20';$('runtime-minutes').oninput();
for(const counts of [null,{}, {...zero,extra:0},{...zero,creation:{requests:0,effects:0}},{...zero,admissions:-1},{...zero,unknown:1},{...zero,admissions:0.5},{...zero,runtime_operations:'0'}]){dbRuntime.status.blockers=counts;dbRuntimeRender();assert.equal(dbRuntimeBlockers(),null);assert.equal($('runtime-chill').disabled,true);assert.match(dbRuntimeCondition('chill'),/unavailable/);for(const id of ['runtime-wake','runtime-save','runtime-reconcile'])assert.equal($(id).disabled,false);}
""")

    def test_policy_reason_priority_and_invalid_input_disables_save(self):
        self.javascript(r"""
await start();assert.match(dbRuntimeCondition('save'),/No unsaved/);$('runtime-minutes').value='';$('runtime-minutes').oninput();assert.equal($('runtime-save').disabled,true);assert.match(dbRuntimeCondition('save'),/integer/);
dbRuntime.conflict=true;dbRuntimeRender();assert.match(dbRuntimeCondition('save'),/changed elsewhere/);dbRuntime.status.policy.minutes=-1;dbRuntimeRender();assert.match(dbRuntimeCondition('save'),/stored policy/);assert.equal($('runtime-minutes').disabled,true);assert.equal($('runtime-reload').disabled,false);
""")

    def test_number_compatible_minutes_and_post_bodies(self):
        self.javascript(r"""
await start();for(const text of ['0','1440',' 20 ','1e2','0x10','+2','2.0','01','-0']){
 dbRuntime.operation=null;dbRuntime.intent=null;$('runtime-minutes').value=text;$('runtime-minutes').oninput();assert.equal(dbRuntimeDraftMinutes(),Number(text));assert.equal($('runtime-save').disabled,false,text);await dbRuntimeAct('auto-chill');assert.deepEqual(JSON.parse(posts().at(-1).options.body),JSON.parse(JSON.stringify({minutes:Number(text),expected_revision:3})));
}
""")

    def test_invalid_minutes_never_post(self):
        self.javascript(r"""
await start();for(const text of ['', ' ', '-1','1441','0.5','NaN','Infinity','1e100','abc']){$('runtime-minutes').value=text;$('runtime-minutes').oninput();assert.equal(dbRuntimeDraftMinutes(),null);assert.equal($('runtime-save').disabled,true);await dbRuntimeAct('auto-chill');}assert.equal(posts().length,0);
""")

    def test_old_observation_qualifies_without_new_gates(self):
        self.javascript(r"""
await start();dbRuntime.status.observed_at='2000-01-01T00:00:00Z';dbRuntime.reading=true;dbRuntime.observing=true;dbRuntime.status.operation_phase='UNKNOWN';dbRuntime.operation={phase:'UNKNOWN'};dbRuntimeRender();assert.match($('runtime-conditions-scope').textContent,/2000-01-01.*not current permission, admission, success or health assurance/);assert.match($('runtime-conditions-scope').textContent,/server rechecks/);for(const id of ['runtime-wake','runtime-chill','runtime-reconcile'])assert.equal($(id).disabled,false);assert.equal(posts().length,0);
""")

    def test_four_physical_states_never_infer_stop_cause(self):
        self.javascript(r"""
await start();runtime.physical_state='active';runtime.replicas=1;runtime.ready_replicas=1;await refreshStatus();assert.equal(dbRuntime.status.physical_state,'active');assert.equal(dbRuntime.status.replicas,1);assert.equal(dbRuntime.status.ready_replicas,1);assert.match($('runtime-physical-meaning').textContent,/fixed Deployments matched expected replica counts and generation conditions/);assert.match($('runtime-physical-meaning').textContent,/does not confirm live SQL health/);dbRuntime.status.operation_action='WAREHOUSE_CHILL';dbRuntime.status.operation_phase='SUCCEEDED';dbRuntime.status.policy.minutes=1;dbRuntime.status.engine_verified=true;dbRuntime.status.engine_verified_at='2000-01-01T00:00:00Z';
for(const [state,pattern] of [['active',/fixed Deployments matched expected replica counts and generation conditions/],['chilled',/cause and actual stop time are unconfirmed/],['starting',/starting or stopping/],['unknown',/do not assume it is stopped/]]){dbRuntime.status.physical_state=state;dbRuntimeRender();assert.match($('runtime-physical-meaning').textContent,pattern);assert.match($('runtime-evidence-meaning').textContent,/historical evidence, not live health/);assert.match($('runtime-evidence-meaning').textContent,/Saved shared LOCAL operation history/);assert.match($('runtime-evidence-meaning').textContent,/do not establish the current stop cause/);}
assert.equal(dbRuntimePhysicalLabel('starting'),'Transitioning');assert.equal(t('Transitioning'),'Transitioning');assert.equal(t('Starting'),'Starting');assert.equal(t('Wake'),'Wake');assert.equal(t('Chill'),'Chill');setLanguage('ja');assert.equal(t('Transitioning'),'移行中');assert.equal(dbRuntimePhysicalLabel('starting'),'遷移中');assert.equal(t('Starting'),'起動中');assert.equal(t('Wake'),'起動');assert.equal(t('Chill'),'休止');
""")

    def test_pure_repeated_render_locale_navigation_preserves_intent_drafts_focus(self):
        self.javascript(r"""
await start();const input=$('runtime-minutes');input.value='47';input.oninput();input.focus();dbRuntime.intent={action:'auto-chill',body:{minutes:47,expected_revision:3},key:'original-key'};
const before=JSON.stringify(dbRuntime),draft=JSON.stringify(dbInput(dbCurrent())),beforeCalls=calls.length;
for(const lang of ['ja','en','ja','en']){setLanguage(lang);const beforeWrites=writes.length;dbRuntimeRender();dbRuntimeRender();assert.equal(writes.length,beforeWrites);assert.equal(document.activeElement,input);assert.equal(input.value,'47');assert.equal(JSON.stringify(dbRuntime),before);}
activate($('tab-compute'));activate($('tab-warehouse'));assert.equal(JSON.stringify(dbRuntime),before);assert.equal(JSON.stringify(dbInput(dbCurrent())),draft);assert.equal(calls.length,beforeCalls);assert.equal(posts().length,0);
""")

    def test_original_unknown_explanation_does_not_retarget_or_replay(self):
        self.javascript(r"""
await start();actionRoute=()=>{throw Error('lost ACK');};await dbRuntimeAct('auto-chill');assert.equal(posts().length,0);await dbRuntimeAct('wake');const intent=dbRuntime.intent,first=posts()[0];
for(const lang of ['ja','en']){setLanguage(lang);dbRuntimeRender();assert.equal(dbRuntime.intent,intent);assert.equal(posts().length,1);assert.equal($('runtime-condition-receipt').hidden,false);}
assert.match($('runtime-condition-receipt').textContent,/may POST/);actionRoute=null;await $('runtime-receipt').onclick();assert.equal(posts().length,2);assert.equal(posts()[1].path,first.path);assert.equal(posts()[1].options.body,first.options.body);assert.equal(posts()[1].options.headers['Idempotency-Key'],first.options.headers['Idempotency-Key']);
""")

    def test_bilingual_existing_sections_descriptions_and_impact_preserved(self):
        self.javascript(r"""
await start();const input=$('runtime-minutes');
for(const lang of ['ja','en']){setLanguage(lang);assert.equal($('runtime-panel').parentElement,$('compute'));for(const [action,section] of [['wake','controls'],['reconcile','controls'],['receipt','controls'],['chill','safety'],['save','policy'],['reload','policy']]){const condition=$('runtime-condition-'+action);assert.equal(condition.parentElement,$('runtime-'+section+'-section'));const ids=$('runtime-'+action).getAttribute('aria-describedby').split(' ');assert.equal(new Set(ids).size,ids.length);assert.ok(ids.includes(condition.id));}assert.match($('runtime-condition-wake').textContent,lang==='ja'?/この観測では選択できます。操作時に再確認します。/:/Selectable/);assert.ok($('runtime-conditions-scope').textContent.includes(t('Refresh status')));assert.match($('quick-wake').title,lang==='ja'?/全 DB/:/all databases/);}
for(const id of ['runtime-minutes-help','runtime-policy','runtime-draft'])assert.ok(input.getAttribute('aria-describedby').split(' ').includes(id));assert.equal(posts().length,0);
""")

    def test_scoped_wrapping_rules_and_no_new_fetch_or_store(self):
        html=database_ui.HTML
        self.assertIn('#runtime-panel .runtime-condition{min-width:0;max-width:75ch;overflow-wrap:anywhere;white-space:normal}', html)
        helper=html[html.index('function dbRuntimeDraftMinutes()'):html.index('async function dbRuntimeRequest')]
        for forbidden in ['fetch(', 'setTimeout(', 'setInterval(', 'localStorage', 'dbRuntimeRequest(', 'dbRuntimeObserve(', 'dbRuntimeReceipt(']:
            self.assertNotIn(forbidden, helper)


if __name__ == '__main__':
    unittest.main()
