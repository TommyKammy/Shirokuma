"""UX-R2: SQL worksheet rebuilt from the Atlas mock-up (structure and data)."""
import re
import unittest

import test_workbench_database_ui as harness
from workbench_source import WorkbenchSource, function

SCRIPT = harness.SCRIPT
CSS = WorkbenchSource(harness.HTML).css
COMPACT_CSS = re.sub(r'\s+', '', re.sub(r'/\*[\s\S]*?\*/', '', CSS))

HELPERS = r"""
const sqlClasses=node=>(node.className||'').split(' ').filter(Boolean);
const sqlHas=(node,name)=>sqlClasses(node).includes(name);
const sqlFlat=root=>{const out=[];const walk=node=>{out.push(node);for(const child of node.children||[])walk(child);};walk(root);return out;};
const sqlAt=(root,node)=>sqlFlat(root).indexOf(node);
const sqlText=id=>{const node=$(id);return [node.textContent,...node.descendants().map(child=>child.textContent)].join(' ');};
const sqlOption=(id,value)=>[...$(id).options].find(option=>option.value===value);
const rawDatabaseId='ffaf65d0-bbb0-4c1d-8e2f-123456789abc';
const rawSchemaId='43293940-aaaa-bbbb-cccc-dddddddddddd';
"""


class RebuildSQLUI(unittest.TestCase):
    javascript = harness.DatabaseUI.javascript

    def test_initialize_session_boots_database_ui_without_detached_nodes(self):
        self.javascript(HELPERS + r"""
storage.set(dbWorksheetKey,JSON.stringify({active:'a',tabs:[input()]}));
await initializeSession();
await dbPageHydration;await dbRuntimeHydration;
const attached=$('warehouse').descendants(),byId=id=>attached.find(node=>node.id===id)||null;
for(const id of ['query-database','query-schema','query-execution-role','max-rows','query-mode','query-warehouse','sql','run-query','cancel-query','worksheet-tabs','result-view-table','export-csv','copilot-request','sql-history-details','open-context-example','sample-sql-tools']){
 assert.equal(attached.filter(node=>node.id===id).length,1,id);
 assert.ok(byId(id),id);
}
assert.equal($('database-page-title').parentElement.className,'pageheading');
assert.equal($('warehouse').querySelector('.page-head').hidden,true);
""")

    def test_mockup_structure_and_order(self):
        self.assertIn('<h1 id="database-page-title">', harness.HTML)
        self.assertIn("pageheading.className='pageheading'", SCRIPT)
        self.javascript(HELPERS + r"""
await boot();
const page=$('warehouse'),pane=$('database-worksheet-pane'),editor=$('database-editor'),results=$('database-results');
const all=sqlFlat(page),pageheading=all.find(node=>sqlHas(node,'pageheading')),bar=all.find(node=>sqlHas(node,'worksheet-bar'));
assert.ok(pageheading);assert.equal(pageheading.children[0],$('database-page-title'));
assert.equal($('database-page-title').textContent,'SQL Worksheets');
assert.ok(bar);assert.equal(bar.children.includes($('worksheet-tabs')),true);
assert.equal($('worksheet-tabs').role,'tablist');assert.equal(bar.children.includes($('add-worksheet-tab')),true);assert.equal(bar.children.includes($('close-worksheet')),true);
assert.equal($('add-worksheet-tab').textContent,'＋');
assert.ok(sqlAt(page,bar)<sqlAt(page,editor));assert.ok(sqlAt(page,editor)<sqlAt(page,results));
assert.equal(sqlHas(editor,'panel'),true);assert.equal(sqlHas(results,'panel'),true);assert.equal(pane.children.includes(editor),true);assert.equal(pane.children.includes(results),true);
const context=$('database-context-bar');assert.equal(context.className,'contextbar sql-context');assert.equal(editor.children[0],context);
const fixed=context.descendants().find(node=>sqlHas(node,'sql-fixed'));assert.ok(fixed);
assert.ok(fixed.descendants().some(node=>node.id==='sql-mode'));assert.ok(fixed.descendants().some(node=>node.id==='sql-warehouse'));
const wrap=$('sql').parentElement;assert.equal(wrap.className,'editor');assert.equal(wrap.children.includes($('sql-linenumbers')),true);
assert.ok(wrap.children.indexOf($('sql-linenumbers'))<wrap.children.indexOf($('sql')));
const footer=$('run-query').parentElement.parentElement;assert.equal(footer.className,'editorfooter sql-actions');
assert.equal(footer.parentElement,editor);assert.ok(footer.descendants().includes($('run-query')));assert.ok(footer.descendants().includes($('cancel-query')));
assert.equal($('sql-next').className,'sql-note');assert.equal($('sql-next').parentElement,editor);
const head=results.children.find(node=>sqlHas(node,'panelhead'));assert.ok(head);assert.equal($('result-heading').parentElement,head);
assert.equal($('result-heading').textContent,'Result');assert.equal($('query-meta').className,'resultmeta');
const tools=$('result-view-table').parentElement.parentElement;assert.equal(tools.parentElement,results);assert.equal(sqlHas(tools,'sql-result-tools'),true);
assert.equal($('result-view-table').parentElement.className,'sql-view-switch');
assert.equal($('export-csv').parentElement.className,'sql-downloads');assert.equal($('export-json').parentElement,$('export-csv').parentElement);assert.equal($('export-tsv').parentElement,$('export-csv').parentElement);
assert.equal($('sql-row-count').parentElement,tools);assert.equal($('sql-row-count').getAttribute('role'),'status');
assert.equal($('query-results').parentElement,results);assert.equal(sqlHas($('query-results'),'tablewrap'),true);
assert.equal($('sql-supported').className,'sqlhint');
const copilot=$('copilot-request').closest('details.copilot');assert.equal(sqlHas(copilot,'sql-note'),true);
const bottom=[$('sql-supported'),copilot,$('worksheet-explorer'),$('sql-history-details'),$('sql-connection'),$('sample-sql-tools')];
for(let index=1;index<bottom.length;index++)assert.ok(sqlAt(page,bottom[index-1])<sqlAt(page,bottom[index]),index);
assert.equal($('worksheet-explorer').parentElement,pane);assert.equal($('sample-sql-tools').parentElement,pane);
assert.equal(all.some(node=>node.id==='sql-context-state'),false);assert.equal(all.some(node=>node.id==='worksheet-task-context'),false);
""")

    def test_database_boot_keeps_one_attached_copy_of_each_sql_control(self):
        self.javascript(HELPERS + r"""
await boot();
const root=$('warehouse'),attached=root.descendants();
const byId=id=>attached.find(node=>node.id===id)||null;
const ids=['database-page-title','database-page-description','worksheet-bar','worksheet-tabs','add-worksheet-tab','close-worksheet',
 'database-context-bar','query-database','query-schema','query-execution-role','max-rows','query-mode','query-warehouse',
 'sql-mode','sql-warehouse','warehouse-state','warehouse-compute-link','refresh-status','chill','wake','sql','sql-linenumbers','run-query','cancel-query','sql-next',
 'result-heading','query-meta','query-results','result-view-table','result-view-chart','copy-tsv','export-csv','export-json','export-tsv','sql-row-count',
 'copilot-request','sql-history-details','query-history','sql-connection','open-context-example','sample-sql-tools',
 'worksheet-explorer','sql-supported','workspace-details'];
for(const id of ids){
 const matches=attached.filter(node=>node.id===id);
 assert.equal(matches.length,1,id+' count='+matches.length);
 assert.equal(byId(id),matches[0],id);
}
assert.equal(byId('database-editor').children[0],byId('database-context-bar'));
assert.equal(typeof byId('query-execution-role').onchange,'function');
assert.equal(typeof byId('query-database').onchange,'function');
assert.equal(typeof byId('query-schema').onchange,'function');
assert.equal(typeof byId('sql').onkeydown,'function');
assert.equal(byId('copilot-request').closest('details.copilot').parentElement,byId('database-worksheet-pane'));
""")


    def test_context_fields_are_labels_wrapping_select_field(self):
        self.javascript(HELPERS + r"""
await boot();setLanguage('ja');dbControls();
const context=$('database-context-bar');
for(const [id,label] of [['query-database','DB（カタログ）'],['query-schema','スキーマ'],['query-execution-role','実行ロール'],['max-rows','最大行数']]){
 const control=$(id),field=control.parentElement;
 assert.equal(control.tagName,'SELECT');assert.equal(sqlHas(control,'field'),true);
 assert.equal(field.tagName,'LABEL');assert.equal(field.htmlFor,id);assert.equal(field.parentElement,context);
 assert.equal(field.hidden,false);assert.equal(sqlHas(field,'field'),false);
 const caption=field.children.find(node=>sqlHas(node,'sql-context-label'));
 assert.ok(caption);assert.equal(caption.textContent,label);
}
for(const id of ['query-mode','query-warehouse']){
 assert.equal($(id).parentElement.tagName,'LABEL');assert.equal($(id).parentElement.hidden,true);
}
assert.equal(context.descendants().some(node=>node.tagName==='SPAN'&&sqlHas(node,'field')),false);
""")

    def test_selected_worksheet_tab_uses_mockup_fill_and_keeps_actions_at_the_end(self):
        self.assertIn('#worksheet-tabsbutton[aria-selected="true"]{background:var(--ink);border-color:var(--ink);color:var(--surface,#fff);font-weight:600}', COMPACT_CSS)
        self.assertIn('#worksheet-tabs{flex-wrap:nowrap}', COMPACT_CSS)
        self.assertIn('#worksheet-tabs{display:flex;gap:4px;overflow-x:auto;min-width:0;flex:1;padding:3px;}', COMPACT_CSS)
        self.assertIn('#worksheet-tabsbutton{flex:none;white-space:nowrap;}', COMPACT_CSS)
        self.assertIn('.worksheet-bar{display:flex;gap:6px;align-items:center;margin-bottom:12px;min-width:0;}', COMPACT_CSS)
        self.javascript(HELPERS + r"""
await boot([input()]);
for(let index=2;index<=9;index++)addWorksheet('SELECT '+index);
await tick();
assert.equal(worksheets.length,9);assert.equal($('worksheet-tabs').children.length,9);
assert.equal($('worksheet-tabs').parentElement,$('worksheet-bar'));
assert.equal($('worksheet-bar').children.indexOf($('worksheet-tabs'))<$('worksheet-bar').children.indexOf($('add-worksheet-tab')),true);
assert.equal($('worksheet-bar').children.indexOf($('close-worksheet')),$('worksheet-bar').children.length-1);
assert.equal($('worksheet-tabs').querySelectorAll('[role=tab]').filter(tab=>tab.getAttribute('aria-selected')==='true').length,1);
""")

    def test_mode_and_warehouse_use_colon_between_label_and_value(self):
        self.javascript(HELPERS + r"""
await boot();dbControls();setLanguage('ja');
assert.equal($('sql-mode-label').textContent,'モード: ');
assert.equal($('sql-warehouse-label').textContent,'ウェアハウス: ');
assert.equal($('sql-mode').textContent,'読み取り専用');
assert.ok($('sql-warehouse').textContent.startsWith('LOCAL · '));
setLanguage('en');
assert.equal($('sql-mode-label').textContent,'Mode: ');
assert.equal($('sql-warehouse-label').textContent,'Warehouse: ');
assert.equal($('sql-mode').textContent,'read-only');
""")

    def test_unrun_result_panel_hides_tools_and_shows_one_message(self):
        self.javascript(HELPERS + r"""
await boot();setLanguage('ja');
assert.equal($('sql-result-tools').hidden,true);assert.equal($('sql-request-details').hidden,true);
assert.equal($('query-meta').textContent,'未実行');
assert.equal($('query-results').children.length,1);
assert.equal($('query-results').children[0].className,'resultmessage');
assert.equal($('query-results').children[0].textContent,'未実行');
dbCurrent().database_id='';dbCurrent().schema_id='';dbRenderRun();
assert.equal($('query-results').children[0].textContent,'DBとスキーマを選び、実行してください。');
dbCurrent().database_id='db';dbCurrent().schema_id='schema';dbRenderRun();
route=async()=>envelope(dbRuns.get(worksheetId),{result_page_id:'page-1'});
await $('run-query').onclick();await tick();
assert.equal($('sql-result-tools').hidden,false);assert.equal($('sql-request-details').hidden,false);
assert.equal($('sql-row-count').hidden,false);
""")

    def test_bottom_details_start_closed_and_keep_mockup_order(self):
        self.javascript(HELPERS + r"""
await boot();
const pane=$('database-worksheet-pane'),copilot=$('copilot-request').closest('details.copilot');
for(const node of [$('sql-supported'),copilot,$('worksheet-explorer'),$('sql-history-details'),$('sql-connection')])assert.ok(!node.open,node.id||node.className);
const order=[$('sql-supported'),copilot,$('worksheet-explorer'),$('sql-history-details'),$('sql-connection'),$('sample-sql-tools')].map(node=>pane.children.indexOf(node));
assert.deepEqual(order.slice().sort((a,b)=>a-b),order);assert.ok(order[0]>=0);
assert.equal($('sql-supported').querySelector('summary').textContent,'Supported SQL');
""")

    def test_compute_detail_strip_is_not_shown(self):
        self.javascript(HELPERS + r"""
await boot();
const strip=$('warehouse').querySelector('.warehouse-strip');
assert.ok(strip);assert.equal(strip.hidden,true);assert.equal(strip.getAttribute('aria-hidden'),'true');
assert.equal($('sql-mode').parentElement.parentElement.parentElement,$('database-context-bar'));
""")
        source = function('dbInstallWorkspace', SCRIPT)
        self.assertIn('computeStrip.hidden=true', source)

    def test_deleted_and_unavailable_contexts_never_show_raw_ids(self):
        self.javascript(HELPERS + r"""
await boot();setLanguage('ja');
dbCurrent().database_id=rawDatabaseId;dbCurrent().schema_id=rawSchemaId;
dbSchemas=[];dbSchemaScope=dbSchemaKey('admin',rawDatabaseId);dbControls();
let databaseOption=sqlOption('query-database',rawDatabaseId),schemaOption=sqlOption('query-schema',rawSchemaId);
assert.ok(databaseOption);assert.equal(databaseOption.disabled,true);assert.equal(databaseOption.textContent,'削除済みのデータベース');
assert.ok(schemaOption);assert.equal(schemaOption.disabled,true);assert.equal(schemaOption.textContent,'利用できないスキーマ');
let visible=[...$('query-database').options,...$('query-schema').options].map(option=>option.textContent).join(' ');
assert.ok(!visible.includes(rawDatabaseId)&&!visible.includes(rawSchemaId),visible);assert.ok(!visible.includes('利用不可'),visible);

inventory[0]={...inventory[0],id:'old',display_name:'Sales Archive',state:'RETIRING'};
dbDatabases=structuredClone(inventory);dbInventories.set('admin',{items:structuredClone(inventory),request:1,version:0});
const retiredSchema={...schemas[0],id:'old-schema',database_id:'old',display_name:'Archive Mart',capabilities:{...schemas[0].capabilities,query:false}};
dbSchemas=[retiredSchema];dbSchemaCache.set(dbSchemaKey('admin','old'),dbSchemas);dbSchemaScope=dbSchemaKey('admin','old');
dbCurrent().database_id='old';dbCurrent().schema_id='old-schema';dbControls();
databaseOption=sqlOption('query-database','old');schemaOption=sqlOption('query-schema','old-schema');
assert.equal(databaseOption.textContent,'Sales Archive');assert.equal(databaseOption.disabled,true);
assert.equal(schemaOption.textContent,'Archive Mart');assert.equal(schemaOption.disabled,true);
visible=[...$('query-database').options,...$('query-schema').options].map(option=>option.textContent).join(' ');
assert.ok(!visible.includes('old-schema')&&!visible.includes('Sales Archive · old'),visible);
assert.equal($('run-query').disabled,true);
assert.equal($('sql-next').textContent,'このワークシートの実行先を利用できません。データベースとスキーマを選び直してください。');
""")

    def test_role_mode_and_neutral_note_are_japanese(self):
        self.javascript(HELPERS + r"""
await boot();setLanguage('ja');dbControls();
assert.deepEqual([...$('query-execution-role').options].map(option=>option.textContent),['閲覧者','データ作成者','管理者']);
assert.equal($('sql-mode').textContent,'読み取り専用');
assert.equal(sqlOption('query-mode','read-only').textContent,'読み取り専用');
assert.ok($('sql-warehouse').textContent.startsWith('LOCAL · '));
dbCurrent().database_id=rawDatabaseId;dbCurrent().schema_id=rawSchemaId;dbSchemas=[];dbSchemaScope=dbSchemaKey('admin',rawDatabaseId);dbControls();
assert.equal($('sql-next').textContent,'このワークシートの実行先を利用できません。データベースとスキーマを選び直してください。');
assert.equal($('database-context-state').textContent,'');
const chrome=sqlText('database-context-bar')+' '+sqlText('sql-next');
assert.ok(!chrome.includes(rawDatabaseId)&&!chrome.includes(rawSchemaId),chrome);
assert.ok(!chrome.includes('admin'),chrome);assert.ok(!chrome.includes(' / '),chrome);assert.ok(!chrome.includes('利用不可'),chrome);
assert.ok(!/Choose database|Choose schema|Read only|Unavailable/.test(chrome),chrome);
setLanguage('en');dbControls();
assert.equal($('sql-next').textContent,'This worksheet context is unavailable. Choose a database and schema again.');
assert.deepEqual([...$('query-execution-role').options].map(option=>option.textContent),['BI Viewer','Data Engineer','Admin']);
""")

    def test_existing_run_cancel_unknown_export_and_graph_features(self):
        self.javascript(HELPERS + r"""
await boot([input(),{...input('b'),name:'Worksheet 2'}]);
assert.equal($('worksheet-tabs').children.length,2);assert.equal($('worksheet-tabs').role,'tablist');
$('worksheet-b').click();await tick();assert.equal(worksheetId,'b');assert.equal($('sql').value,'SELECT 1');
$('worksheet-a').click();await tick();assert.equal(worksheetId,'a');
addWorksheet('SELECT 3');await tick();assert.equal(worksheets.length,3);
assert.equal($('close-worksheet').disabled,false);

const shortcut=$('run-query');let clicks=0;const click=shortcut.click;shortcut.click=()=>{clicks++;};
$('sql').onkeydown({metaKey:true,key:'Enter',preventDefault(){}});
assert.equal(clicks,1);shortcut.click=click;

$('sql').value='SELECT 1;\nSELECT 2;';dbSave();$('sql').setSelectionRange(0,9);
route=async()=>envelope(dbRuns.get(worksheetId),{result_page_id:'page-1'});
await $('run-query').onclick();await tick();
const post=calls.filter(call=>call.path==='/api/v1/queries'&&call.options?.method==='POST').at(-1);
assert.equal(JSON.parse(post.options.body).sql,'SELECT 1;');
assert.equal(dbRuns.get(worksheetId).state,'FINISHED');
assert.equal($('sql-row-count').hidden,false);assert.equal($('sql-row-count').textContent,'Received 1 rows / showing 1 rows');
assert.equal(['export-csv','export-json','export-tsv'].every(id=>$(id).disabled===false),true);
$('result-view-chart').click();assert.equal($('query-results').hidden,true);assert.equal($('query-chart').hidden,false);
$('result-view-table').click();assert.equal($('query-results').hidden,false);assert.equal($('query-chart').hidden,true);

const run=dbRuns.get(worksheetId);run.id='cancel-me';run.cancel_uri='/api/v1/queries/cancel-me/cancel';run.pending=true;run.settled=false;dbControls();
route=async()=>({id:'cancel-me',state:'CANCELED'});
await dbCancelQuery();assert.equal(dbRuns.get(worksheetId).state,'CANCELED');
assert.equal(calls.some(call=>call.path==='/api/v1/queries/cancel-me/cancel'&&call.options?.method==='POST'),true);

route=async()=>envelope(dbRuns.get(worksheetId),{state:'UNKNOWN',next_uri:null});
await dbRunQuery();await tick();
assert.equal(dbRuns.get(worksheetId).state,'UNKNOWN');assert.match($('query-error').textContent,/unknown|不明/i);
assert.equal($('run-query').disabled,true);
assert.equal(calls.filter(call=>call.path==='/api/v1/queries'&&call.options?.method==='POST').length,2);
""")

    def test_new_labels_switch_between_japanese_and_english(self):
        self.javascript(HELPERS + r"""
await boot();setLanguage('ja');
assert.equal($('database-page-title').textContent,'SQL ワークシート');
assert.equal($('result-heading').textContent,'結果');
assert.equal($('close-worksheet').textContent,'閉じる');
assert.equal($('add-worksheet-tab').getAttribute('aria-label'),'新しいSQLワークシート');
assert.equal($('sql-supported').querySelector('summary').textContent,'対応しているSQL');
assert.equal($('sql-history-details').querySelector('summary').textContent,'実行履歴');
assert.equal($('sql-connection').querySelector('summary').textContent,'接続先');
assert.equal($('copilot-request').closest('details.copilot').querySelector('summary').textContent,'SQL 提案と説明');
assert.ok(sqlText('database-worksheet-pane').includes('DB（カタログ）'));
assert.ok(sqlText('database-worksheet-pane').includes('実行ロール'));
assert.ok(sqlText('database-worksheet-pane').includes('最大行数'));
const labels=['database-page-title','result-heading','close-worksheet','sql-supported','sql-history-details','sql-connection'].map(sqlText).join(' ');
assert.ok(!/Result|Close|Supported SQL|Execution history|Connection/.test(labels),labels);
setLanguage('en');
assert.equal($('database-page-title').textContent,'SQL Worksheets');assert.equal($('result-heading').textContent,'Result');
assert.equal($('close-worksheet').textContent,'Close');assert.equal($('sql-supported').querySelector('summary').textContent,'Supported SQL');
assert.equal($('sql-history-details').querySelector('summary').textContent,'Execution history');
""")

    def test_narrow_layout_has_no_page_level_overflow_and_44px_targets(self):
        self.assertIn('#database-workspace{display:block', COMPACT_CSS)
        self.assertIn('#database-worksheet-pane{display:flex;flex-direction:column;gap:0;padding:0', COMPACT_CSS)
        self.assertIn('#database-worksheet-pane>.panel{margin-top:20px', COMPACT_CSS)
        self.assertIn('@media(max-width:600px){#database-context-bar{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))', COMPACT_CSS)
        self.assertIn('.sql-fixed{display:grid;grid-template-columns:minmax(0,1fr)', COMPACT_CSS)
        self.assertIn('.sql-fixed>span{white-space:normal}', COMPACT_CSS)
        self.assertIn('.worksheet-bar{flex-wrap:nowrap;min-width:0}', COMPACT_CSS)
        self.assertIn('#database-worksheet-pane>#worksheet-explorer#catalog-tree{max-height:260px}', COMPACT_CSS)
        self.assertIn('main:is(button,select,input:not([type=checkbox]):not([type=radio]),textarea,summary)', COMPACT_CSS)
        self.assertIn('min-height:44px!important', COMPACT_CSS)
        self.assertIn('#query-results', COMPACT_CSS)
        self.assertIn('overflow:auto', COMPACT_CSS)
        self.assertIn('body{overflow-x:hidden}', COMPACT_CSS)


if __name__ == '__main__':
    unittest.main()
