"""Atlas SQL worksheet (UX-A3): run label, block reasons, layout and samples."""
import re
import unittest

import test_workbench_database_ui as database_ui
from workbench_source import WorkbenchSource, function

SCRIPT = database_ui.SCRIPT
CSS = WorkbenchSource().css


class SQLWorksheetUI(unittest.TestCase):
    def javascript(self, body):
        database_ui.DatabaseUI.javascript(self, body)

    def test_run_label_follows_selection_and_runs_only_the_selection(self):
        self.javascript(r'''
await boot();
assert.equal($('run-query').textContent,'▶ Run all');
$('sql').value='SELECT 1;\nSELECT 2;';dbSave();$('sql').setSelectionRange(0,9);
$('sql').dispatchEvent({type:'select'});
assert.equal($('run-query').textContent,'▶ Run selection');
assert.equal($('run-query').dataset.runLabel,'▶ Run selection');
setLanguage('ja');
assert.equal($('run-query').textContent,'▶ 選択範囲を実行');
$('sql').setSelectionRange(9,9);$('sql').dispatchEvent({type:'keyup'});
assert.equal($('run-query').textContent,'▶ 全文を実行');
$('sql').setSelectionRange(0,9);$('sql').dispatchEvent({type:'mouseup'});
assert.equal($('run-query').textContent,'▶ 選択範囲を実行');
setLanguage('en');
assert.equal($('run-query').textContent,'▶ Run selection');
route=async()=>envelope(dbRuns.get('a'));
await $('run-query').onclick();await tick();
const post=calls.filter(c=>c.path==='/api/v1/queries'&&c.options?.method==='POST').at(-1);
assert.equal(JSON.parse(post.options.body).sql,'SELECT 1;');
$('sql').setSelectionRange(9,9);$('sql').dispatchEvent({type:'select'});
assert.equal($('run-query').textContent,'▶ Run all');
''')

    def test_block_reasons_use_mockup_order_and_sit_beside_the_button(self):
        self.javascript(r'''
await boot();
const reason=$('database-context-state'),button=$('run-query');
assert.equal(reason.parentElement,button.parentElement);
assert.ok(reason.parentElement.children.indexOf(reason)>reason.parentElement.children.indexOf(button));
assert.equal(button.getAttribute('aria-describedby'),'database-context-state sql-next');
assert.equal(reason.getAttribute('role'),'status');
assert.equal(button.disabled,false);assert.equal(reason.textContent,'');

// Busy and unknown are explained in the result panel, not repeated beside the button.
const pending=deferred();route=async()=>pending.promise;
const running=dbRunQuery();await tick();dbControls();
assert.equal(button.disabled,true);assert.equal(reason.textContent,'');
assert.match($('query-meta').textContent,new RegExp(t('QUEUED')+'|'+t('RUNNING')));
pending.resolve(envelope(dbRuns.get('a')));await running;
route=async()=>envelope(dbRuns.get('a'),{state:'UNKNOWN',next_uri:null});await dbRunQuery();dbControls();
assert.equal(dbRuns.get('a').state,'UNKNOWN');
assert.equal(button.disabled,true);assert.equal(reason.textContent,'');
assert.match($('query-error').textContent,/Query outcome unknown/);

// Compute reasons come before the DB and schema selections.
dbRuns.delete('a');dbCurrent().database_id='';
workbenchSetComputeStatus('paused');
assert.equal(button.disabled,true);
assert.equal(reason.textContent,t('Compute is chilled, so SQL cannot run. An administrator wakes it from Compute.'));
for(const state of ['switching','unknown','stale']){
 workbenchSetComputeStatus(state);
 assert.equal(reason.textContent,t('The compute state is not confirmed. An administrator must reconcile it before SQL can run.'),state);
}
workbenchSetComputeStatus('available');
assert.equal(reason.textContent,t('Choose a database (catalog) next.'));

// READY and role checks, then the schema selection.
dbCurrent().database_id='db';dbCurrent().schema_id='';dbControls();
assert.equal(reason.textContent,t('Choose a schema next.'));
inventory[0]={...inventory[0],state:'RETIRING'};await dbRefreshContext();dbControls();
assert.equal(button.disabled,true);
assert.equal(reason.textContent,'');
assert.equal($('sql-next').textContent,t('This worksheet context is unavailable. Choose a database and schema again.'));
inventory[0]={...inventory[0],state:'READY',allowed_roles:['data_engineer']};await dbRefreshContext();dbControls();
assert.equal(reason.textContent,'');
assert.equal($('sql-next').textContent,t('This worksheet context is unavailable. Choose a database and schema again.'));
inventory[0]={...inventory[0],state:'READY',allowed_roles:['admin','data_engineer','bi_viewer']};await dbRefreshContext();
assert.equal($('sql-next').textContent,'');

// Empty SQL and a whitespace-only selection are last.
dbCurrent().schema_id='schema';$('sql').value='';$('sql').dispatchEvent({type:'input'});dbControls();
assert.equal(reason.textContent,t('Enter SQL to run.'));
$('sql').value='   ';$('sql').setSelectionRange(0,3);$('sql').dispatchEvent({type:'select'});
assert.equal(reason.textContent,t('The selection is empty. Select SQL again or clear the selection.'));
assert.equal(button.disabled,true);
''')

    def test_compute_block_uses_only_the_shared_chip_state(self):
        self.javascript(r'''
await boot();
for(const [state,key] of [['paused','Compute is chilled, so SQL cannot run. An administrator wakes it from Compute.'],
 ['switching','The compute state is not confirmed. An administrator must reconcile it before SQL can run.'],
 ['unknown','The compute state is not confirmed. An administrator must reconcile it before SQL can run.'],
 ['stale','The compute state is not confirmed. An administrator must reconcile it before SQL can run.']]){
 workbenchSetComputeStatus(state);
 assert.equal($('run-query').disabled,true,state);
 assert.equal($('database-context-state').textContent,t(key),state);
}
for(const state of ['available','loading','unsupported']){
 workbenchSetComputeStatus(state,state==='available'?'tone-ok':'');
 assert.equal($('run-query').disabled,false,state);
 assert.equal($('database-context-state').textContent,'',state);
}
workbenchSetComputeStatus('available','tone-ok');
assert.match($('database-compute-state').textContent,/LOCAL/);
assert.equal($('sql-mode').textContent,t('read-only'));
assert.match($('sql-warehouse').textContent,/^LOCAL · /);
assert.match($('sql-warehouse').textContent,new RegExp(t('Available')));
$('sql').setSelectionRange(0,3);$('sql').dispatchEvent({type:'select'});
assert.equal($('run-query').textContent,'▶ Run selection');
workbenchSetComputeStatus('available','tone-ok');
setLanguage('ja');dbControls();
assert.equal($('sql-mode').textContent,'読み取り専用');
assert.match($('sql-warehouse').textContent,/^LOCAL · 利用可能/);
setLanguage('en');
''')
        block = function('dbComputeBlockReason', SCRIPT)
        self.assertIn('workbenchComputeStatusKey', block)
        self.assertNotIn('dbRuntime.status', block)
        self.assertNotIn('/api/status', block)
        self.assertIn("state==='paused'", block)

    def test_only_ready_databases_are_offered_and_the_current_one_explains_itself(self):
        self.javascript(r'''
await boot();
inventory.push({...inventory[0],id:'retiring',sql_catalog:'retiring',display_name:'Retiring',state:'RETIRING'});
await dbRefreshContext();dbControls();
assert.deepEqual([...$('query-database').options].map(option=>option.value).filter(Boolean),['db']);
dbCurrent().database_id='retiring';await dbRefreshContext();dbControls();
assert.equal($('query-database').value,'retiring');
const retained=[...$('query-database').options].find(option=>option.value==='retiring');
assert.equal(retained.disabled,true);assert.equal(retained.textContent,'Retiring');
assert.ok(![...$('query-database').options].some(option=>option.textContent.includes('retiring')));
assert.match($('sql-next').textContent,/context is unavailable/);
assert.equal($('run-query').disabled,true);
''')
        self.assertRegex(SCRIPT, r"dbSelectOptions\('query-database',dbReadyDatabases\(w\.selected_role\)")

    def test_explorer_use_in_sql_keeps_database_schema_and_role(self):
        self.javascript(r'''
await dualBoot();await exploreFinance();
assert.equal(dbExplorerReason(),'');
await $('use-explorer-schema').onclick();await tick();
const w=dbCurrent();
assert.equal(w.database_id,'finance');assert.equal(w.schema_id,'finance-marts');
assert.equal(w.selected_role,currentRole);assert.equal(dbReason(),'');
assert.equal(dbRunBlockReason().text,'');
assert.equal($('run-query').disabled,false);
assert.equal($('run-query').getAttribute('aria-describedby'),'database-context-state sql-next');
''')

    def test_mockup_structure_and_order_for_editor_results_and_bottom_parts(self):
        self.javascript(r'''
await boot();
const page=$('warehouse'),pane=$('database-worksheet-pane'),editor=$('database-editor'),results=$('database-results');
const order=[];const walk=node=>{order.push(node);for(const child of node.children)walk(child);};walk(page);
const at=node=>order.indexOf(node),has=(node,name)=>(node.className||'').split(' ').includes(name);
assert.ok(at($('database-page-title'))<at($('worksheet-tabs')));
assert.ok(at($('worksheet-bar'))<at(editor));
assert.ok(has($('worksheet-bar'),'worksheet-bar'));
assert.equal($('worksheet-tabs').getAttribute('role'),'tablist');
assert.equal(has(editor,'panel'),true);assert.equal(has(results,'panel'),true);assert.equal(at(editor)<at(results),true);
const context=$('database-context-bar');assert.equal(context.className,'contextbar sql-context');
assert.equal(has(context,'contextbar'),true);assert.equal(has(context,'sql-context'),true);
assert.ok(context.children.some(node=>node.className==='sql-fixed'));
const fixed=context.children.find(node=>node.className==='sql-fixed');
assert.ok(fixed.descendants().some(node=>node.id==='sql-mode'));assert.ok(fixed.descendants().some(node=>node.id==='sql-warehouse'));
const wrap=$('sql').parentElement;assert.equal(wrap.className,'editor');assert.ok(wrap.children.includes($('sql-linenumbers')));
assert.ok(wrap.children.indexOf($('sql-linenumbers'))<wrap.children.indexOf($('sql')));
const footer=$('run-query').parentElement.parentElement;assert.equal(footer.className,'editorfooter sql-actions');
assert.ok(footer.children.includes($('run-query').parentElement));assert.ok(footer.descendants().includes($('cancel-query')));
assert.equal($('sql-next').className,'sql-note');assert.equal($('sql-next').parentElement,editor);
const head=results.children.find(node=>has(node,'panelhead'));assert.ok(head);assert.equal($('query-meta').className,'resultmeta');
assert.equal($('result-heading').parentElement,head);assert.equal($('result-heading').textContent,'Result');
const tools=$('result-view-table').parentElement.parentElement;assert.equal(tools.parentElement,results);assert.equal(has(tools,'sql-result-tools'),true);
assert.equal($('result-view-table').parentElement.className,'sql-view-switch');
assert.equal($('export-csv').parentElement.className,'sql-downloads');
assert.equal($('query-results').parentElement,results);assert.equal(has($('query-results'),'tablewrap'),true);
assert.equal($('sql-supported').className,'sqlhint');
const copilot=$('copilot-request').closest('details.copilot');assert.equal(has(copilot,'sql-note'),true);
const bottom=[$('sql-supported'),copilot,$('worksheet-explorer'),$('sql-history-details'),$('sql-connection'),$('sample-sql-tools')];
for(let index=1;index<bottom.length;index++)assert.ok(at(bottom[index-1])<at(bottom[index]),index);
assert.equal($('worksheet-explorer').parentElement,pane);assert.equal($('sample-sql-tools').parentElement,pane);
for(const id of ['result-view-table','result-view-chart','copy-tsv','export-csv','export-json','export-tsv'])assert.equal($(id).hidden,false,id);
''')

    def test_execution_role_options_use_display_names_and_keep_internal_values(self):
        self.javascript(r'''
await boot();
const select=$('query-execution-role');
assert.deepEqual([...select.options].map(option=>option.value),['bi_viewer','data_engineer','admin']);
assert.deepEqual([...select.options].map(option=>option.textContent),['BI Viewer','Data Engineer','Admin']);
assert.equal(select.value,'admin');
setLanguage('ja');dbControls();
assert.deepEqual([...select.options].map(option=>option.textContent),['閲覧者','データ作成者','管理者']);
assert.deepEqual([...select.options].map(option=>option.value),['bi_viewer','data_engineer','admin']);
assert.equal(select.value,'admin');
setLanguage('en');dbControls();
assert.deepEqual([...select.options].map(option=>option.textContent),['BI Viewer','Data Engineer','Admin']);
''')

    def test_query_meta_is_not_run_before_execution(self):
        self.javascript(r'''
await boot();
assert.equal($('query-meta').textContent,'Not run yet');
setLanguage('ja');dbRenderRun();
assert.equal($('query-meta').textContent,'未実行');
setLanguage('en');dbRenderRun();
route=async()=>envelope(dbRuns.get('a'));await dbRunQuery();
assert.notEqual($('query-meta').textContent,'Not run yet');
assert.match($('query-meta').textContent,/FINISHED/);
''')

    def test_sample_sql_is_a_select_and_load_into_the_current_worksheet(self):
        self.javascript(r'''
await boot();
assert.deepEqual([...$('sample-query').options].map(option=>option.value),['','orders','revenue','customers']);
const context=dbContext(dbCurrent());
for(const value of ['orders','revenue','customers']){
 $('sample-query').value=value;$('load-query').click();
 assert.equal($('sql').value,samples[value]);assert.equal(dbCurrent().sql,samples[value]);
 assert.equal($('sample-query').value,'');assert.equal(document.activeElement,$('sql'));
 assert.deepEqual(dbContext(dbCurrent()),context);
}
setLanguage('ja');
assert.equal($('sample-query').options.find(option=>option.value==='orders').textContent,'最近の注文');
assert.equal($('load-query').textContent,'SQL を読み込む');
''')

    def test_last_worksheet_is_protected_and_tabs_stay_before_the_context_bar(self):
        self.javascript(r'''
await boot();
assert.equal($('worksheet-closers').hidden,true);
assert.match($('worksheet-close-action').title,/at least one/);
assert.equal($('close-worksheet').disabled,true);
addWorksheet('SELECT 2');await tick();
assert.equal(worksheets.length,2);
assert.equal($('worksheet-closers').hidden,false);
assert.equal($('worksheet-close-action').disabled,true);
assert.equal($('close-worksheet').disabled,false);
const page=$('warehouse'),order=[];const walk=node=>{order.push(node);for(const child of node.children)walk(child);};walk(page);
assert.ok(order.indexOf($('worksheet-bar'))<order.indexOf($('database-workspace')));
assert.equal($('worksheet-tabs').parentElement,$('worksheet-bar'));
$('close-worksheet').click();assert.equal(worksheets.length,1);
assert.equal($('close-worksheet').disabled,true);
''')

    def test_narrow_layout_is_two_columns_with_44px_targets_and_16px_text(self):
        compact = ' '.join(re.sub(r'\s+', ' ', CSS).split())
        self.assertIn('#database-context-bar{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))', compact)
        self.assertIn('#database-context-bar :is(select,input){min-height:44px;font-size:1rem', compact)
        self.assertIn('#database-editor :is(#run-query,#cancel-query){min-height:44px', compact)
        self.assertIn('.editor{font-size:1rem}', compact)
        self.assertIn(':focus-visible{outline:2px solid var(--focus)', compact)
        self.assertIn('button.run::before{content:""', compact)
        self.assertIn('button#run-query.run::before{content:none}', compact)
        self.assertIn('#database-workspace{display:block', compact)
        self.assertIn('.sql-fixed{display:grid;grid-template-columns:minmax(0,1fr)', compact)
        self.assertIn('.sql-fixed>span{white-space:normal}', compact)
        self.assertIn('main :is(button,select,input:not([type=checkbox]):not([type=radio]),textarea,summary)', compact)
        self.assertIn('min-height:44px!important', compact)
        self.assertIn('#query-results', compact)
        narrow = '\n'.join(re.findall(r'@media\(max-width:760px\)\{(.*?)\n\}', CSS, re.S))
        self.assertIn(':is(.topbar,#role-controls,#role-selector,.toolbar,.editor-tabs,#database-workspace-main){min-width:0;max-width:100%}', narrow)
        six = next(block for block in re.findall(r'@media\(max-width:600px\)\{(.*?)\n\}', CSS, re.S)
                   if 'grid-template-columns:repeat(2,minmax(0,1fr))' in block)
        self.assertIn('#warehouse :is(button,select,input:not([type=checkbox])){min-height:44px}', six)
        for selector in ('#database-workspace .explorer-search input,', '#database-workspace .explorer-search select,',
                         '#database-workspace .explorer-foot select,', '#warehouse #refresh-catalog,',
                         '#warehouse .explorer-foot button,', '#warehouse #catalog-tree .table-link,',
                         '#warehouse #query-results th button,'):
            self.assertIn(selector, six)


if __name__ == '__main__':
    unittest.main()
