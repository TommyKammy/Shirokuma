"""Explorer details and Compute layout for owner acceptance, through production handlers."""
import unittest

import test_workbench_database_ui as database_ui
from test_workbench_drop_database_ui import SETUP as EXPLORER_SETUP
from test_workbench_warehouse_runtime_ui import SETUP as RUNTIME_SETUP


EXPLORER = r'''
async function explorerReady(){
 await boot();
 Object.assign(dbExplorer,{database_id:'db',schema_id:'raw',binding_revision:1,generation_id:'gen',grant_revision:inventory[0].grant_revision,invalid:false,schemas:structuredClone(schemas),schemaScope:dbSchemaKey(currentRole,'db')});
 dbObject=stable('orders');activate($('tab-catalog'));dbExplorerControls();
}
'''


class ExplorerLayoutUI(unittest.TestCase):
    def javascript(self, body):
        database_ui.DatabaseUI.javascript(self, EXPLORER_SETUP + EXPLORER + body)

    def test_sql_actions_stay_with_title_and_management_is_grouped_apart(self):
        self.javascript(r'''
await explorerReady();
const toolbar=$('open-table-sql').parentElement;
for(const id of ['insert-table-sql','use-explorer-schema','refresh-table-details'])assert.equal($(id).parentElement,toolbar,id);
for(const id of ['clone-table-btn','export-table-format','export-table-btn'])assert.equal($(id).parentElement.parentElement,$('db-manage-copy'),id);
assert.equal($('drop-table-btn').parentElement.parentElement,$('db-manage-remove'));assert.notEqual($('drop-table-btn').parentElement,toolbar);
assert.equal($('db-manage-remove').className,'db-manage-group db-manage-danger');
const children=$('database-details').children,at=id=>children.indexOf($(id));
assert.ok(at('db-table-manage')>at('inspect-columns'));assert.ok(at('db-table-manage')>at('inspect-preview'));
assert.equal($('db-table-manage').tagName,'DETAILS');assert.equal($('db-table-manage').open,false);
for(const id of ['p35-table-export','db-clone-panel','db-drop-panel'])assert.equal($(id).parentElement,$('db-table-manage'));
for(const id of ['db-clone-panel','db-drop-panel']){assert.equal($(id).tagName,'DETAILS');assert.equal($(id).open,false);}
assert.equal($('explorer-open-lineage').hidden,true);assert.equal(typeof $('explorer-open-lineage').onclick,'function');
assert.equal($('p35-table-export').hidden,true);assert.equal($('db-table-manage').hidden,false);
removalReachable($('drop-table-btn'));removalReachable($('open-table-sql'));
assert.equal($('drop-table-btn').disabled,false);assert.equal($('db-manage-remove-reason').textContent,'');
for(let i=0;i<3;i++){dataopsInstall();dbManageInstall();dbExplorerControls();}
assert.equal($('database-details').descendants().filter(n=>n.id==='db-table-manage').length,1);assert.equal($('drop-table-btn').parentElement.parentElement,$('db-manage-remove'));
''')

    def test_management_reasons_follow_role_and_object_and_hide_without_object(self):
        self.javascript(r'''
await explorerReady();
dbObject={...stable('orders'),kind:'VIEW'};dbExplorerControls();
assert.match($('db-manage-copy-reason').textContent,/View export is unsupported/);assert.match($('db-manage-remove-reason').textContent,/VIEW catalog removal is unavailable/);
assert.equal($('drop-table-btn').disabled,true);assert.equal($('export-table-btn').disabled,true);
setLanguage('ja');assert.equal($('db-table-manage-title').textContent,'テーブルの管理');assert.match($('db-manage-remove-reason').textContent,/VIEW/);assert.doesNotMatch($('db-manage-remove-reason').textContent,/VIEW catalog removal is unavailable/);
setLanguage('en');dbObject=stable('orders');dbExplorerControls();assert.equal($('db-manage-remove-reason').textContent,'');
$('table-title').textContent='orders';$('table-title').dataset.object='true';
dbClearExplorer();assert.equal(dbObject,null);assert.equal($('db-table-manage').hidden,true);
const schema=dbExplorer.schemas.find(s=>s.id===dbExplorer.schema_id);assert.equal($('table-title').textContent,schema.display_name);assert.equal($('table-title').dataset.object,'cleared');dbExplorer.schemas=[];dbExplorerControls();assert.equal($('table-title').textContent,dbDatabases.find(d=>d.id===dbExplorer.database_id).display_name);dbShowMetadataOverview('database');assert.equal($('table-title').dataset.object,'');
dbObject=stable('orders');dbExplorer.invalid=true;dbExplorerControls();
assert.ok(dbExplorerReason());assert.equal($('db-manage-copy-reason').textContent,'');assert.equal($('db-manage-remove-reason').textContent,'');
''')

    def test_closed_management_and_recovery_do_not_fetch_or_reset_drafts(self):
        self.javascript(r'''
await explorerReady();const before=calls.length;
$('db-table-manage').open=true;$('db-clone-panel').open=true;
$('db-clone-name').value='draft_copy';$('db-clone-name').focus();
for(let i=0;i<3;i++){dbExplorerControls();setLanguage(i%2?'ja':'en');}
assert.equal($('db-table-manage').open,true);assert.equal($('db-clone-panel').open,true);
assert.equal($('db-clone-name').value,'draft_copy');assert.equal(document.activeElement,$('db-clone-name'));
$('db-clone-close').onclick();assert.equal($('db-clone-panel').open,false);assert.equal(document.activeElement,$('db-clone-summary'));
dbClone.op={principal:dbPrincipal,key:'private-key',id:null,context:{selected_role:'admin'},payload:{source:ref('orders')},state:'UNKNOWN',ambiguous:true};
dbDrop.op={principal:dbPrincipal,key:'private-drop-key',id:null,context:{selected_role:'admin'},payload:{source:ref('orders')},state:'UNKNOWN',ambiguous:true};
dbObject=null;dbExplorerControls();
assert.equal($('db-table-manage').hidden,false);assert.equal($('db-table-manage').open,false);
assert.equal($('db-manage-recovery').hidden,false);
for(const kind of ['clone','drop']){
 assert.equal($('db-'+kind+'-panel').open,false);assert.equal($('db-'+kind+'-recovery').hidden,false);
 assert.match($('db-'+kind+'-recovery').textContent,/UNKNOWN|結果不明/);
 assert.doesNotMatch($('db-'+kind+'-recovery').textContent,/private|orders/);
 $('db-'+kind+'-recovery').onclick();assert.equal($('db-'+kind+'-panel').open,true);
 assert.equal($('db-'+(kind==='clone'?'drop':'clone')+'-panel').open,false);
 assert.equal(document.activeElement,$('db-'+kind+'-observe'));
}
assert.ok(!$('db-clone-operation-details').open);assert.ok(!$('db-drop-operation-details').open);
assert.equal(calls.length,before);
setLanguage('ja');assert.match($('db-drop-recovery').textContent,/保存済みの登録解除/);
''')

    def test_export_receipt_appears_only_for_a_requested_export(self):
        self.javascript(r'''
await explorerReady();assert.equal($('p35-table-export').hidden,true);
dataopsExport={principal:dbPrincipal,id:'op',key:'key',context:{sql_catalog:'db',sql_schema:'raw',selected_role:'admin'},table:'raw.orders',state:'QUEUED',pending:false};dataopsExportRender();
assert.equal($('p35-table-export').hidden,false);assert.equal($('db-export-recovery').hidden,false);assert.match($('db-export-recovery').textContent,/Saved export: QUEUED/);
const count=calls.length;$('db-export-recovery').onclick();assert.equal($('db-table-manage').open,true);assert.equal(document.activeElement,$('p35-export-receipt'));assert.equal(calls.length,count);
assert.equal($('p35-export-observe').textContent,'Look up export operation');
dataopsExport.id=null;dataopsExport.path='/api/v1/table-exports';dataopsExportRender();
assert.equal($('p35-export-observe').textContent,'Resend original request');
assert.match($('p35-export-receipt').children[0].textContent,/it may start the operation/);
dataopsExport=null;dataopsExportRender();assert.equal($('p35-table-export').hidden,true);assert.equal($('db-export-recovery').hidden,true);
''')


class ComputeLayoutUI(unittest.TestCase):
    def javascript(self, body):
        database_ui.DatabaseUI.javascript(self, RUNTIME_SETUP + '\n' + body)

    def test_state_usage_and_diagnostics_order_and_current_role_gate(self):
        self.javascript(r'''
await start();
const top=$('compute').children.filter(n=>['compute-overview','runtime-panel','compute-usage','observation-activity-panel','compute-diagnostics'].includes(n.id)).map(n=>n.id);
assert.deepEqual(top,['compute-overview','runtime-panel','compute-usage','observation-activity-panel','compute-diagnostics']);
assert.equal($('runtime-panel').parentElement,$('compute'));
for(const [id,group] of [['observation-resources-panel','compute-usage-list'],['observation-usage-panel','compute-usage-list'],['observation-capacity-panel','compute-usage-list'],['observation-compute-panel','compute-diag-runtime'],['observation-history-panel','compute-diag-runtime'],['observation-baseline-panel','compute-diag-policy'],['observation-pod-baseline-panel','compute-diag-policy'],['observation-recent-failures-panel','compute-diag-logs']])assert.equal($(id).parentElement,$(group),id);
assert.equal(dbAdminAllowed(),true);assert.equal($('compute-diagnostics').hidden,false);assert.equal($('compute-usage-list').hidden,false);assert.equal($('compute-usage-note').hidden,true);
assert.match($('runtime-capability').textContent,/as a platform operator/);assert.equal($('runtime-authority').hidden,true);
const before=$('compute').children.slice();for(let i=0;i<3;i++){dbRuntimeInstall();dbComputeLayout();dbRuntimeRender();}assert.deepEqual($('compute').children,before);
assert.equal($('compute').descendants().filter(n=>n.id==='compute-diagnostics').length,1);
currentRole='bi_viewer';dbApplyRole();dbRuntimeRender();
assert.equal(dbAdminAllowed(),false);assert.equal(dbAdmin.platform,true);
assert.equal($('compute-diagnostics').hidden,true);assert.equal($('compute-usage-list').hidden,true);assert.equal($('compute-usage-note').hidden,false);
assert.match($('compute-usage-note').textContent,/Select the admin role/);assert.match($('runtime-capability').textContent,/Read-only with the current role/);
for(const action of ['wake','chill','reconcile','save','reload'])assert.equal($('runtime-condition-'+action).hidden,true,action);assert.equal($('runtime-conditions-scope').hidden,true);
assert.match($('runtime-blockers').textContent,/Blockers:|Blocker observation unavailable/);
currentRole='admin';dbApplyRole();dbRuntimeRender();assert.equal($('compute-diagnostics').hidden,false);assert.equal($('compute-usage-note').hidden,true);
''')

    def test_non_platform_sees_state_and_one_capability_line(self):
        self.javascript(r'''
await start(false);assert.equal(dbAdmin.platform,false);
assert.equal($('compute-diagnostics').hidden,true);assert.equal($('compute-usage-list').hidden,true);
assert.match($('compute-usage-note').textContent,/fetched by platform operators/);assert.match($('runtime-capability').textContent,/^Read-only: you can check the state/);
assert.equal($('runtime-wake').disabled,true);assert.equal($('runtime-chill').disabled,true);
for(const action of ['wake','chill','reconcile','save','reload'])assert.equal($('runtime-condition-'+action).hidden,true,action);
setLanguage('ja');assert.match($('runtime-capability').textContent,/閲覧のみ/);assert.equal($('compute-overview-title').textContent,t('Compute & Governance'));
''')

    def test_summary_keeps_missing_failed_unsupported_stale_and_engine_record_distinct(self):
        self.javascript(r'''
await start();const saved={status:dbRuntime.status,failed:dbRuntime.failed,support:dbRuntime.support};
const read=()=>[$('runtime-state-badge').textContent,$('runtime-state-badge').className,$('runtime-summary').textContent];
dbRuntime.status=null;dbRuntime.failed=false;dbRuntimeRender();assert.deepEqual(read().slice(0,2),[t('Loading…'),'badge']);assert.match(read()[2],/Reading the runtime state/);
dbRuntime.failed=true;dbRuntimeRender();assert.equal(read()[1],'badge tone-bad');assert.match(read()[2],/current state is unknown; this is not the same as stopped or zero usage/);
dbRuntime.support='unsupported';dbRuntimeRender();assert.equal(read()[0],'Not supported');assert.doesNotMatch(read()[2],/Reading/);
Object.assign(dbRuntime,saved);
dbRuntime.status={...saved.status,physical_state:'active',engine_verified:true,engine_verified_at:'2000-01-01T00:00:00Z',observed_at:new Date().toISOString()};dbRuntimeRender();
assert.equal(read()[1],'badge tone-ok');assert.match(read()[2],/separate record and may be older/);assert.doesNotMatch(read()[2],/verified then|outdated/);
dbRuntime.status={...dbRuntime.status,observed_at:new Date(Date.now()-25*60000).toISOString()};dbRuntimeRender();assert.match(read()[2],/Observed 25 min ago\. This observation may be outdated/);
for(const [state,tone,pattern] of [['chilled','badge',/starts shared LOCAL automatically/],['starting','badge tone-run',/Starting or stopping/],['unknown','badge tone-warn',/Do not assume it is stopped/]]){dbRuntime.status={...dbRuntime.status,physical_state:state};dbRuntimeRender();assert.equal(read()[1],tone);assert.match(read()[2],pattern);}
setLanguage('ja');assert.match(read()[2],/休止中とはみなさないでください/);assert.match(read()[2],/古い観測の可能性があります/);
''')

    def test_chill_reason_is_shown_beside_controls_only_while_blocked(self):
        self.javascript(r'''
await start();assert.equal(dbAdminAllowed(),true);
dbRuntime.status.blockers.admissions=1;dbRuntimeRender();assert.equal($('runtime-chill').disabled,true);assert.equal($('runtime-condition-chill').hidden,false);assert.match($('runtime-condition-chill').textContent,/Admitted work: 1/);
dbRuntime.status.blockers.admissions=0;dbRuntime.status.blockers.unknown=0;dbRuntimeRender();
assert.equal($('runtime-chill').disabled,!dbRuntimeAllowed('chill'));assert.equal($('runtime-condition-chill').hidden,!$('runtime-chill').disabled);
assert.equal($('runtime-condition-save').hidden,true);
assert.equal($('runtime-condition-chill').parentElement,$('runtime-safety-section'));assert.equal($('runtime-actions').parentElement,$('runtime-controls-section'));
const controls=$('runtime-controls-section').children;assert.ok(controls.indexOf($('runtime-capability'))<controls.indexOf($('runtime-impact')));assert.ok(controls.indexOf($('runtime-impact'))<controls.indexOf($('runtime-actions')));
''')


if __name__ == '__main__':
    unittest.main()
