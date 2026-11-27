"""UX-A6b: Atlas-shaped AI Bears handoff and shared compute controls."""
import json
import re
import unittest

import test_workbench_database_ui as harness
from test_workbench_ai_sql_proposals_database_ui import PROPOSAL
from test_workbench_bears_database_ui import COMMON
from test_workbench_warehouse_runtime_ui import SETUP as RUNTIME_SETUP
from workbench_source import WorkbenchSource, function as source_function


HTML = harness.HTML
SOURCE = WorkbenchSource(HTML)
CSS = SOURCE.css
RUNTIME_STAMP = source_function('dbRuntimeImpact', SOURCE.sources)
RUNTIME_CAN_CHILL = source_function('dbRuntimeCanChill', SOURCE.sources)
RUNTIME_ORDER = source_function('dbComputeLayout', SOURCE.sources)
PATCH = r'''
const appendNodes=Node.prototype.append;Node.prototype.append=function(...items){return appendNodes.apply(this,items.map(n=>typeof n==='string'?Object.assign(new Node('text'),{textContent:n}):n));};
'''


class BearsComputeAtlasUI(unittest.TestCase):
    def bears(self, body):
        harness.DatabaseUI.javascript(self, PATCH + COMMON + PROPOSAL + body)

    def compute(self, body):
        harness.DatabaseUI.javascript(self, PATCH + RUNTIME_SETUP + '\n' + body)

    def test_static_shell_moves_ai_settings_into_compute_and_keeps_ids(self):
        self.assertNotIn('id="bears-lead"', HTML)
        for identifier in ('ai-settings', 'ai-settings-form', 'ai-key', 'ai-model',
                           'ai-save', 'ai-test', 'ai-close', 'ai-config-status',
                           'ai-shared-help', 'ai-settings-button'):
            self.assertIn('id="' + identifier + '"', HTML)
        self.assertLess(HTML.index('id="compute"'), HTML.index('id="ai-settings"'))
        self.assertLess(HTML.index('id="compute-diagnostics-body"'), HTML.index('id="ai-settings"'))
        self.assertIn('id="warehouse-compute-link"', HTML)
        self.assertIn('id="chill" class="small" hidden', HTML)
        self.assertIn('id="wake" class="small" hidden', HTML)
        button = re.search(r'<button id="ai-settings-button"[^>]*>', HTML).group(0)
        self.assertIn('aria-controls="compute"', button)
        self.assertNotIn('aria-haspopup="dialog"', button)
        self.assertIn("'compute-ai-settings'", RUNTIME_ORDER)
        self.assertIn('compute-ai-settings', self.markup_css())
        self.assertIn('dbJobs', source_function('loadJobAlerts', SOURCE.sources))
        timer = next(line for line in SOURCE.source.splitlines() if line.startswith('setInterval(async()=>'))
        self.assertIn('databaseMode', timer)
        self.assertIn('dbRefreshAccess', timer)

    def markup_css(self):
        return CSS

    def test_bears_handoff_waits_for_approval_and_inherits_worksheet_context(self):
        self.bears(r"""
await ready();route=async()=>wrap(proposalRecord());await $('copilot-generate').onclick();
const p=dbProposal.record;assert.equal(p.state,'SUCCEEDED');
setLanguage('ja');assert.match($('bear-handoff-context').textContent,/Sales · sales \/ marts \/ 管理者/);
assert.ok($('bear-handoff'));assert.equal($('bear-handoff-sql').textContent,p.sql);
assert.equal($('bear-handoff-worksheet').className,'primary');
assert.notEqual($('bear-handoff-transformation').className,'primary');
const before=worksheets.length;
await $('bear-handoff-worksheet').onclick();
assert.equal(worksheets.length,before);
assert.match($('bear-handoff-message').textContent,/承認のチェック/);
$('bear-handoff-approved').checked=true;
await $('bear-handoff-worksheet').onclick();
assert.equal(worksheets.length,before+1);
const w=worksheets.at(-1);
assert.equal(w.sql,p.sql);assert.equal(w.database_id,p.context.database_id);
assert.equal(w.schema_id,p.context.schema_id);assert.equal(w.selected_role,p.context.selected_role);
assert.equal(w.binding_revision,p.context.binding_revision);assert.equal(w.generation_id,p.context.generation_id);
assert.equal(activeTab,'warehouse');assert.equal(worksheetId,w.id);
""")

    def test_bears_transformation_handoff_is_explicit_and_does_not_save_a_model(self):
        self.bears(r"""
await ready();route=async()=>wrap(proposalRecord());await $('copilot-generate').onclick();
const p=dbProposal.record,wap=dataopsPanels.get('p35-wap');
assert.ok(!wap.aiPending);
const before=calls.filter(c=>c.options?.method==='POST'&&c.path.startsWith('/api/v1/wap/')).length;
$('bear-handoff-approved').checked=true;
await $('bear-handoff-transformation').onclick();
assert.equal(activeTab,'transformation');
assert.equal(wap.model,null);assert.equal(wap.modelDirty,true);
assert.ok(wap.aiPending);assert.equal(wap.aiPending.original_sql,p.sql);
assert.equal(wap.aiPending.context.database_id,p.context.database_id);
assert.equal(wap.aiPending.context.schema_id,p.context.schema_id);
assert.equal(wap.aiPending.context.selected_role,p.context.selected_role);
assert.equal(calls.filter(c=>c.options?.method==='POST'&&c.path.startsWith('/api/v1/wap/')).length,before);
""")

    def test_bears_labels_use_transformation_wording_in_both_languages(self):
        self.bears(r"""
await boot();for(const locale of ['ja','en']){
 setLanguage(locale);
 for(const id of ['bear-handoff','bear-handoff-title','bear-handoff-create','bear-handoff-worksheet','bear-handoff-transformation'])assert.ok($(id));
 assert.doesNotMatch($('bear-handoff').textContent,/\bWAP\b/);
 assert.ok($('bear-handoff-transformation').textContent.includes(locale==='ja'?'データ変換':'Transformation'));
 assert.equal(t('WAP navigation requires data_engineer or admin (E/A).').includes('WAP'),false);
}
""")

    def test_compute_state_controls_auto_chill_ai_settings_then_diagnostics(self):
        self.compute(r"""
await start();$('tab-compute').click();
const atlas=$('compute-atlas');
assert.equal(atlas.hidden,false);
assert.deepEqual(atlas.children.map(n=>n.id),['compute-overview','compute-runtime-host','compute-usage','compute-auto-chill','compute-diagnostics']);
assert.equal($('compute-heading').parentElement,$('compute-overview'));
assert.equal($('compute-environment').parentElement,$('compute-overview'));
assert.equal($('runtime-panel').parentElement,$('compute-runtime-host'));
const panel=$('runtime-panel');
assert.equal(panel.className,'section');assert.equal(panel.getAttribute('role'),'region');
assert.deepEqual(panel.children.map(n=>n.id),['runtime-title','runtime-facts','runtime-controls-section']);
assert.deepEqual([...$('runtime-actions').children].map(n=>n.id),['runtime-refresh','runtime-wake','runtime-chill']);
assert.equal($('runtime-action-reason').className,'small');
assert.equal($('runtime-chill-confirm').tagName,'FORM');
assert.ok($('runtime-controls-section').descendants().includes($('runtime-chill-impact')));
assert.equal($('runtime-details').parentElement.id,'compute-diag-state');assert.equal($('runtime-blockers').parentElement,$('runtime-safety-section'));
assert.ok($('compute').descendants().includes($('runtime-wake')));
assert.ok($('compute').descendants().includes($('runtime-chill')));
assert.ok($('compute').descendants().includes($('runtime-save')));
assert.equal($('runtime-state-status').className.split(' ')[0],'status');
assert.equal($('runtime-state-badge').hidden,true);

assert.equal($('ai-settings').parentElement,$('compute-ai-settings'));
assert.equal($('compute-ai-settings').parentElement,$('compute-diagnostics-body'));
assert.equal($('ai-settings').hidden,true,'closed until requested');assert.equal($('ai-settings-readonly').hidden,true,'readonly');
assert.equal($('compute-ai-settings').descendants().includes($('compute-ai-settings-open')),true);
assert.equal($('compute-diagnostics').open,false);
assert.ok($('compute-usage').descendants().includes($('compute-resource-table')));
assert.match($('runtime-summary').textContent,/JST/);
assert.match($('runtime-summary-detail').textContent,/Observed|observed/);
assert.match($('runtime-conditions-scope').textContent,/JST/);
for(const id of ['observation-resources-panel','observation-usage-panel','observation-capacity-panel'])assert.ok($('compute-usage').descendants().includes($(id)),id);
for(const id of ['observation-compute-panel','observation-history-panel','observation-activity-panel'])assert.ok($('compute-diagnostics').descendants().includes($(id)),id);
""")

    def test_chill_confirmation_shows_one_impact_line_and_red_button(self):
        self.compute(r"""
await start();$('tab-compute').click();setLanguage('ja');
runtime.physical_state='active';runtime.replicas=1;runtime.ready_replicas=1;await refreshStatus();
assert.equal($('runtime-chill').disabled,false);
assert.match($('runtime-chill-impact').textContent,/休止すると SQL を実行できなくなります/);
assert.match($('runtime-chill-impact').textContent,/実行中の処理はありません/);
assert.ok($('runtime-chill-confirm-submit').className.split(' ').includes('danger'));
const before=posts().length;
await $('runtime-chill').onclick();
assert.equal(posts().length,before);assert.equal($('runtime-chill-confirm').hidden,false);
assert.equal($('runtime-chill-confirm-submit').disabled,false);
$('runtime-chill-confirm-check').checked=true;
await $('runtime-chill-confirm-submit').onclick();
assert.equal(posts().length,before+1);assert.equal(posts().at(-1).path,BASE+'/chill');
assert.equal($('runtime-chill-confirm').hidden,true);
""")

    def test_chill_blockers_show_count_and_only_confirmed_worksheet_and_job_names(self):
        self.compute(r"""
await start();$('tab-compute').click();setLanguage('ja');
runtime.blockers={runtime_operations:1,admissions:2,unknown:1,creation:{requests:0,effects:0,roots:0}};
dbRuns.set(worksheets[0].id,{state:'RUNNING'});
route=async(path,options)=>{
 if(path.startsWith('/api/v1/jobs/runs'))return {context:structuredClone(dbContext(dbCurrent())),runs:[{id:'run-1',job_id:'job-1',job_name:'daily-load',status:'running'},{id:'run-2',job_id:'job-2',job_name:'finished-load',status:'succeeded'}]};
 return structuredClone(runtime);
};
await dbRuntimeRefresh();
const impact=$('runtime-chill-impact').textContent;
assert.match(impact,/実行中の処理 3 件/);
assert.equal(impact,'実行中の処理 3 件があるため休止できません。確認できた処理: ジョブ daily-load・ワークシート Worksheet 1');
assert.equal((impact.match(/確認できた処理:/g)||[]).length,1);
assert.equal(/  +/.test(impact),false);
assert.equal(impact.includes('確認できた実行中の'),false);
setLanguage('en');assert.equal($('runtime-chill-impact').textContent,'Chill is blocked because 3 processes are running. Confirmed processes: job daily-load, worksheet Worksheet 1');
assert.equal(impact.includes('finished-load'),false);
assert.equal($('runtime-chill').disabled,true);
assert.equal($('runtime-chill-confirm').hidden,true);
""")

    def test_warehouse_busy_refreshes_counts_and_keeps_one_reason(self):
        self.compute(r"""
await start();$('tab-compute').click();setLanguage('ja');
const originalRoute=route;
actionRoute=async()=>({http:409,body:{code:'WAREHOUSE_BUSY'}});
route=async(path,options)=>{
 if(path===BASE){const next=structuredClone(runtime);next.blockers={runtime_operations:0,admissions:3,unknown:0,creation:{requests:0,effects:0,roots:0}};return next;}
 if(path.startsWith('/api/v1/jobs/runs'))return {context:structuredClone(dbContext(dbCurrent())),runs:[]};
 return originalRoute(path,options);
};
const gets=calls.filter(c=>c.path===BASE).length;
await dbRuntimeAct('chill');
assert.equal(calls.filter(c=>c.path===BASE).length,gets+1);
assert.match($('runtime-message').textContent,/拒否されました|rejected/i);
assert.match($('runtime-chill-impact').textContent,/実行中の処理 3 件/);
assert.equal($('runtime-message').textContent.includes('UNKNOWN'),false);
""")

    def test_viewer_can_refresh_state_but_not_change_compute(self):
        self.compute(r"""
await start();$('tab-compute').click();
currentRole='bi_viewer';dbControls();
const postsBefore=posts().length,getsBefore=calls.filter(c=>c.path===BASE).length;
await $('runtime-refresh').onclick();
assert.equal(posts().length,postsBefore);
assert.ok(calls.filter(c=>c.path===BASE).length>getsBefore);
assert.equal($('runtime-state-badge').textContent,$('database-compute-state').textContent.replace('LOCAL · ',''));
for(const id of ['runtime-wake','runtime-chill','runtime-save','runtime-reconcile'])assert.equal($(id).disabled,true,id);
assert.match($('runtime-capability').textContent,/Read-only/);
assert.doesNotMatch($('runtime-guidance').textContent,/automatically|自動で起動/);
""")

    def test_ai_settings_opens_from_compute_and_user_menu_is_navigation_only(self):
        self.compute(r"""
Node.prototype.showModal=function(){this.open=true;};
Node.prototype.close=function(){this.open=false;this.dispatchEvent({type:'close'});};
session.platform_admin=true;
route=async(path,options)=>path.endsWith('/ai-config')&&options?.method!=='POST'?{provider:'local-openai',model:'test-model',api_url:'http://127.0.0.1:8001/v1',timeout:30}:path.endsWith('/ai-test')?{status:'connected',latency_ms:7}:structuredClone(runtime);
await boot();await refreshStatus();
assert.equal($('ai-settings').parentElement,$('compute-ai-settings'));
assert.equal($('ai-settings-button').getAttribute('aria-controls'),'compute');
assert.equal($('ai-settings-button').getAttribute('aria-haspopup'),undefined);
const posted=posts().length;
await $('ai-settings-button').onclick();
assert.equal(activeTab,'compute');assert.equal($('ai-settings').hidden,false);
assert.equal($('ai-settings').parentElement.id,'compute-ai-settings');
assert.equal($('ai-model').value,'test-model');assert.equal(posts().length,posted);
assert.ok(calls.some(c=>c.path==='/api/v1/bears/ai-config'));
""")

    def test_language_switch_redraws_impact_and_handoff_without_english_only_copy(self):
        self.compute(r"""
await start();$('tab-compute').click();
for(const locale of ['ja','en','ja']){
 setLanguage(locale);
 assert.equal($('runtime-chill-impact').textContent,t('Chilling stops all SQL. There are no running processes.'));
 assert.match($('runtime-physical').textContent,/JST/);
 assert.doesNotMatch($('runtime-guidance').textContent,/starts shared LOCAL automatically/);
}
""")

    def test_reconciled_history_is_not_a_chill_decision_input(self):
        self.assertNotIn('RECONCILED', RUNTIME_CAN_CHILL)
        self.assertNotIn('照合済み', RUNTIME_STAMP)
        self.compute(r"""
await start();$('tab-compute').click();setLanguage('ja');
runtime.blockers={runtime_operations:0,admissions:1,unknown:0,creation:{requests:0,effects:0,roots:0}};
dbLifecycle={history:[{saved_state:'RECONCILED'}]};
await dbRuntimeRefresh();
assert.equal($('runtime-chill').disabled,true);
assert.match($('runtime-chill-impact').textContent,/実行中の処理 1 件/);
assert.equal($('runtime-chill-impact').textContent.includes('照合済み'),false);
""")

    def test_direct_compute_confirms_context_and_fetches_jobs_once(self):
        self.compute(r"""
await start();$('tab-compute').click();setLanguage('ja');
runtime.blockers={runtime_operations:0,admissions:1,unknown:0,creation:{requests:0,effects:0,roots:0}};
dbSchemaScope='';
const state=()=>JSON.stringify([worksheetId,$('sql').value,dbCurrent().database_id,dbCurrent().schema_id,dbCurrent().selected_role]),before=state();
const originalRoute=route;let jobs=0;
route=async(path,options)=>{if(path.startsWith('/api/v1/jobs/runs')){jobs++;return {context:structuredClone(dbContext(dbCurrent())),runs:[{id:'run-1',job_id:'job-1',job_name:'daily_orders',status:'running'}]};}return originalRoute(path,options);};
await dbRuntimeRefresh();if(dbRuntime.contextPromise)await dbRuntime.contextPromise;await tick();
assert.match($('runtime-chill-impact').textContent,/ジョブ daily_orders/);
assert.doesNotMatch($('runtime-chill-impact').textContent,/確認中/);
assert.equal(jobs,1);assert.equal(state(),before);
""")

    def test_unconfirmed_context_becomes_a_confirmed_unavailable_message(self):
        self.compute(r"""
await start();$('tab-compute').click();setLanguage('ja');
runtime.blockers={runtime_operations:0,admissions:1,unknown:0,creation:{requests:0,effects:0,roots:0}};
inventory=[];schemas=[];dbSchemaScope='';
await dbRuntimeRefresh();if(dbRuntime.contextPromise)await dbRuntime.contextPromise;await tick();
assert.doesNotMatch($('runtime-chill-impact').textContent,/確認中/);
assert.match($('runtime-chill-impact').textContent,/この文脈ではジョブ実行を確認できませんでした/);
assert.equal(calls.filter(c=>c.path.startsWith('/api/v1/jobs/runs')).length,0);
""")

    def test_missing_worksheet_context_is_reported_as_unconfirmed(self):
        self.compute(r"""
await start();$('tab-compute').click();setLanguage('ja');
runtime.blockers={runtime_operations:0,admissions:1,unknown:0,creation:{requests:0,effects:0,roots:0}};
worksheets=[];worksheetId='';
await dbRuntimeRefresh();if(dbRuntime.contextPromise)await dbRuntime.contextPromise;await tick();
assert.doesNotMatch($('runtime-chill-impact').textContent,/確認中/);
assert.match($('runtime-chill-impact').textContent,/この文脈ではジョブ実行を確認できませんでした/);
assert.equal(calls.filter(c=>c.path.startsWith('/api/v1/jobs/runs')).length,0);
""")

    def test_context_pending_is_neutral_then_jobs_load_once(self):
        self.compute(r"""
await start();$('tab-compute').click();setLanguage('ja');
runtime.blockers={runtime_operations:0,admissions:1,unknown:0,creation:{requests:0,effects:0,roots:0}};
dbSchemaScope='';
const oldInventory=inventoryRoute,gate=deferred();inventoryRoute=()=>gate.promise;
const originalRoute=route;let jobs=0;
route=async(path,options)=>{if(path.startsWith('/api/v1/jobs/runs')){jobs++;return {context:structuredClone(dbContext(dbCurrent())),runs:[{id:'run-1',job_id:'job-1',job_name:'daily_orders',status:'running'}]};}return originalRoute(path,options);};
const refreshing=dbRuntimeRefresh();await tick();
assert.match($('runtime-chill-impact').textContent,/確認できた処理名を確認中/);
gate.resolve({items:structuredClone(inventory)});inventoryRoute=oldInventory;
await refreshing;if(dbRuntime.contextPromise)await dbRuntime.contextPromise;await tick();
assert.match($('runtime-chill-impact').textContent,/ジョブ daily_orders/);
assert.doesNotMatch($('runtime-chill-impact').textContent,/確認中/);
assert.equal(jobs,1);
""")

    def test_non_busy_409_uses_generic_rejection_and_refreshes(self):
        self.compute(r"""
await start();$('tab-compute').click();setLanguage('ja');
const originalRoute=route;
actionRoute=async()=>({http:409,body:{code:'ADMISSION_CONFLICT'}});
route=async(path,options)=>{
 if(path===BASE){const next=structuredClone(runtime);next.blockers={runtime_operations:0,admissions:1,unknown:0,creation:{requests:0,effects:0,roots:0}};return next;}
 return originalRoute(path,options);
};
await dbRuntimeAct('chill');
assert.match($('runtime-message').textContent,/受付処理の競合/);
assert.doesNotMatch($('runtime-message').textContent,/実行中のため/);
assert.match($('runtime-chill-impact').textContent,/実行中の処理 1 件/);
""")

    def test_runtime_guidance_is_state_aware_and_active_does_not_ask_to_wake(self):
        self.compute(r"""
await start();$('tab-compute').click();setLanguage('ja');
runtime.physical_state='active';runtime.operation_phase=null;await refreshStatus();
assert.match($('runtime-guidance').textContent,/共有 LOCAL は稼働中/);
assert.doesNotMatch($('runtime-guidance').textContent,/起動してから|Wake shared LOCAL/);
runtime.physical_state='chilled';await refreshStatus();
assert.match($('runtime-guidance').textContent,/起動してから/);
runtime.physical_state='starting';await refreshStatus();
assert.match($('runtime-guidance').textContent,/状態を移行中/);
assert.doesNotMatch($('runtime-guidance').textContent,/起動してから/);
dbRuntime.status.policy={minutes:15,revision:1};dbRuntimeRender();
assert.match($('runtime-policy').textContent,/保存済み自動休止: 15 分 · 版 1/);
assert.equal($('runtime-policy').textContent.includes('revision'),false);
""")

    def test_japanese_admin_copy_has_no_revision_or_untranslated_wap_actions(self):
        self.bears(r"""
await boot();setLanguage('ja');
assert.equal($('bears-guide-wap').textContent,'データ変換で候補を確認');
assert.equal($('copilot-guide-wap').textContent,'データ変換を開く');
assert.match($('bears-guide-wap-reason').textContent,/データ作成者または管理者/);
const help=$('database-bears-workflows-help').textContent;
for(const text of ['Refresh panel context','New model','Load proposal draft','Audit','Publish','Rollback','data_engineer/admin'])assert.equal(help.includes(text),false,text);
assert.equal(help.includes('データ変換'),true);
""")

    def test_database_mode_does_not_load_legacy_bears_or_compute_handlers(self):
        self.bears(r"""
await boot();await activate($('tab-bears'));await activate($('tab-compute'));
await activate($('tab-warehouse'));await activate($('tab-bears'));
const legacy=calls.filter(c=>['/api/bears/run','/api/warehouse/query','/api/jobs/runs','/api/lake/ingest'].some(prefix=>c.path.startsWith(prefix)));
assert.equal(legacy.length,0);
assert.equal(nodes['bears-lead'],undefined);
""")
