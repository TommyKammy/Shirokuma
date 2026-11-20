"""UX-A4 ingestion and saved-stage layout, impact copy and READY database gating."""
import unittest

import test_workbench_dataops_ui as dataops_ui
import test_workbench_database_ui as database_ui
import test_workbench_stages_database_ui as stages_ui
from workbench_source import WorkbenchSource


class IngestionStyles(unittest.TestCase):
    def test_ingestion_css_wraps_and_scales_to_touch_widths(self):
        css = WorkbenchSource(database_ui.HTML).css
        self.assertIn('#p35-ingest-panel,#db-stages-panel,#p35-ingest-form,#db-stages-form{min-width:0}', css)
        self.assertIn('#p35-ingest-impact,#db-stages-impact{max-width:100%;overflow-wrap:anywhere}', css)
        self.assertIn('#p35-ingest-form :is(button,select,input,textarea,summary),', css)
        self.assertIn('#db-stages-panel :is(button,select,input,textarea,summary),', css)
        self.assertIn('#db-marts-panel :is(button,select,input,textarea,summary),', css)
        self.assertIn('#transformation :is(button,select,input:not([type=checkbox]),textarea,summary){min-height:44px}', css)
        self.assertIn('#ingestion .subnav button,#transformation .subnav button{min-height:44px}', css)
        self.assertIn('#p35-ingest-form .job-fields,', css)
        self.assertIn('#db-stages-panel .toolbar,', css)
        self.assertIn('#db-marts-panel .toolbar{flex-wrap:wrap}', css)


class IngestionUI(unittest.TestCase):
    javascript = dataops_ui.DataopsUI.javascript

    def test_file_section_collapses_and_impact_sits_before_confirm(self):
        self.javascript(r'''
const p=await ready(),details=$('p35-ingest-file-details'),summaryText=()=>details.children[0].textContent;
assert.equal(details.tagName,'DETAILS');assert.equal(details.open,true);
assert.equal(summaryText(),'Input file (or drop here)');
assert.equal($('p35-ingest-dropzone').parentElement,details);
const order=node=>p.form.children.indexOf(node);
assert.ok(order(details)<order($('p35-ingest-destination-fields')));
assert.ok(order($('p35-ingest-preview').parentElement)<order($('p35-ingest-sample')));
assert.ok(order($('p35-ingest-sample'))<order($('p35-ingest-impact')));
assert.ok(order($('p35-ingest-impact'))<order($('p35-ingest-confirm').parentElement));
assert.equal($('p35-ingest-selected-file').hidden,true);
await dataopsReadFile(p,{name:'rows.csv',arrayBuffer:async()=>Uint8Array.from([0x63,0x0a,0x31,0x0a]).buffer});
assert.equal(details.open,false);
assert.equal(summaryText(),'Change input file');
assert.equal($('p35-ingest-selected-file').hidden,false);
assert.match($('p35-ingest-selected-file').textContent,/Selected input: rows\.csv/);
assert.equal($('clear-upload').hidden,false);
await $('clear-upload').onclick();
assert.equal(details.open,true);
assert.equal(summaryText(),'Input file (or drop here)');
assert.equal($('clear-upload').hidden,true);
''')

    def test_impact_copy_confirm_labels_and_danger_state_for_every_mode(self):
        self.javascript(r'''
const p=await ready(),original=route;let impact=null;
setLanguage('ja');
route=async(path,options)=>{
 if(path==='/api/v1/ingests/preview'){const body=JSON.parse(options.body);return {context:body.context,preview:{request_fingerprint:'impact-fingerprint',input_fingerprint:'bytes',columns:[{name:'code',type:'varchar'}],rows:[['001']],total_rows:1,impact}};}
 if(path.startsWith('/api/v1/objects/')&&path.includes('/lineage')){return {confirmed:[{source:{ref:ref('orders')},target:{label:'iceberg.MARTS.orders_daily'},model:{id:'orders_daily'}}]};}
 return original(path,options);
};
const selected=async mode=>{
 $('p35-ingest-mode').value=mode;$('p35-ingest-mode').onchange();
 if(mode==='create'){$('p35-ingest-name').value='chosen_table';}else{$('p35-ingest-target').value='0';}
 if(mode==='upsert')$('p35-ingest-keys').value='code';
 await dataopsReadFile(p,{name:'rows.csv',arrayBuffer:async()=>Uint8Array.from([0x63,0x0a,0x31,0x0a]).buffer});
 await $('p35-ingest-preview').onclick();await tick();
};
const check=async(mode,next,match,label,danger)=>{
 impact=next;await selected(mode);
 assert.equal($('p35-ingest-impact').hidden,false,mode);
 assert.match($('p35-ingest-impact').children[0].textContent,match,mode);
 assert.equal($('p35-ingest-confirm').textContent,label,mode);
 assert.equal($('p35-ingest-confirm').className,danger?'primary danger':'primary',mode);
 assert.equal(p.status.textContent,'',mode);
};
await check('create',{mode:'create',incoming_rows:1,existing_rows:0,after_rows:1},
 /新規テーブルを作成し 1 行を書き込みます/, '作成して取り込む', false);
await check('append',{mode:'append',incoming_rows:1,existing_rows:100,after_rows:101},
 /既存 100 行に 1 行を追加します（合計 101 行）/, '1 行を追加する', false);
setLanguage('en');assert.equal($('p35-ingest-confirm').textContent,'Append 1 rows');setLanguage('ja');
assert.match($('p35-ingest-impact').children[2].textContent,/下流の変換: orders_daily · 再ビルドまで公開内容は変わりません/);
await check('replace',{mode:'replace',incoming_rows:1,existing_rows:100,after_rows:1},
 /既存 100 行をすべて削除し、1 行に置き換えます/, '100 行を削除して置き換える', true);
await check('upsert',{mode:'upsert',incoming_rows:1,existing_rows:100,after_rows:null},
 /1 行をキーで更新・追加します（既存 100 行）/, '1 行を更新・追加する', true);
setLanguage('en');assert.equal($('p35-ingest-confirm').textContent,'Update or add 1 rows');setLanguage('ja');
await check('append',{mode:'append',incoming_rows:1,existing_rows:null,after_rows:null},
 /1 行を追加します。既存の行数を取得できませんでした。/, '1 行を追加する', false);
await check('replace',{mode:'replace',incoming_rows:1,existing_rows:null,after_rows:null},
 /1 行に置き換えます。既存の行数を取得できませんでした。/, '全件を削除して置き換える', true);
''')

    def test_execution_role_labels_use_header_names_and_keep_internal_values(self):
        self.javascript(r'''
const p=await ready(),role=$('p35-ingest-role');
setLanguage('ja');
assert.deepEqual(role.options.filter(o=>o.value).map(o=>o.value),['bi_viewer','data_engineer','admin']);
assert.equal(role.options.find(o=>o.value==='bi_viewer').textContent,'閲覧者');
assert.equal(role.options.find(o=>o.value==='data_engineer').textContent,'データ作成者');
assert.equal(role.options.find(o=>o.value==='admin').textContent,'管理者');
setLanguage('en');
assert.equal(role.options.find(o=>o.value==='bi_viewer').textContent,'BI Viewer');
assert.equal(role.options.find(o=>o.value==='data_engineer').textContent,'Data Engineer');
assert.equal(role.options.find(o=>o.value==='admin').textContent,'Admin');
''')

    def test_default_placeholder_follows_language_switch(self):
        self.javascript(r'''
const p=await ready();
setLanguage('ja');
assert.equal($('p35-ingest-target').options[0].textContent,'一覧から選択');
setLanguage('en');
assert.equal($('p35-ingest-target').options[0].textContent,'Choose from lists');
''')

    def test_non_ready_databases_are_not_offered(self):
        self.javascript(r'''
const p=await ready();
inventory.push({...structuredClone(inventory[0]),id:'retired',display_name:'Retired',sql_catalog:'retired',state:'RETIRED'});
await dbRefreshAccess();await dataopsRefresh(p);
const values=[...$('p35-ingest-database').options].map(o=>o.value);
assert.equal(values.includes('retired'),false);
assert.equal(values.includes('db'),true);
''')

    def test_japanese_labels_for_the_new_sections(self):
        self.javascript(r'''
setLanguage('ja');
assert.equal(t('Destination and update method'),'取込先と更新方法');
assert.equal(t('Input file (or drop here)'),'入力ファイル（ドロップ可）');
assert.equal(t('Change input file'),'取込元ファイルを変更');
setLanguage('en');
assert.equal(t('Destination and update method'),'Destination and update method');
''')


class SavedStagesUI(unittest.TestCase):
    javascript = stages_ui.StagesUI.javascript

    def test_deep_link_restores_saved_stages_and_tab_click_updates_hash(self):
        self.javascript(r'''
storage.set(dbWorksheetKey,JSON.stringify({active:'a',tabs:[input()]}));
const fileTab=$('ingestion-view-file'),stagesTab=$('ingestion-view-stages');
for(const [tab,panel] of [[fileTab,'ingestion-file'],[stagesTab,'ingestion-stages']]){tab.dataset.view='ingestion';tab.setAttribute('aria-controls',panel);$('ingestion').append(tab);tab.onclick=()=>{if(!tab.disabled)selectSubView(tab);};}
fileTab.setAttribute('aria-selected','true');stagesTab.setAttribute('aria-selected','false');
globalThis.location={href:'http://local/#ingestion/stages'};
globalThis.history={replaceState:(state,title,url)=>{globalThis.location.href=String(url);},pushState:(state,title,url)=>{globalThis.location.href=String(url);}};
await initializeDatabaseUI(session);await dbPageHydration;await dbRuntimeHydration;await tick();await tick();await new Promise(resolve=>setTimeout(resolve,0));
assert.equal(activeTab,'ingestion',JSON.stringify({activeTab,page:workbenchRoute().page,hidden:$('ingestion-view-stages').hidden,disabled:$('ingestion-view-stages').disabled}));
assert.equal($('ingestion-view-stages').getAttribute('aria-selected'),'true',JSON.stringify({activeTab,page:workbenchRoute().page,hidden:$('ingestion-view-stages').hidden,disabled:$('ingestion-view-stages').disabled}));
assert.equal($('ingestion-file').hidden,true);assert.equal($('ingestion-stages').hidden,false);
assert.equal($('db-stages-status').textContent,'Loaded');
$('ingestion-view-file').click();
assert.equal(globalThis.location.href,'http://local/#ingestion');
assert.equal($('ingestion-view-file').getAttribute('aria-selected'),'true');
$('ingestion-view-stages').click();
assert.equal(globalThis.location.href,'http://local/#ingestion/stages');
assert.equal($('ingestion-view-stages').getAttribute('aria-selected'),'true');
globalThis.location.href='http://local/#ingestion';
await workbenchRestorePage(session,{history:true});
assert.equal($('ingestion-view-file').getAttribute('aria-selected'),'true');
assert.equal($('ingestion-file').hidden,false);assert.equal($('ingestion-stages').hidden,true);
''')

    def test_saved_stage_order_reconciliation_first_and_impact_before_confirm(self):
        self.javascript(r'''
const panel=dbStages;
await openStages();
assert.equal($('db-stages-status').textContent,'Loaded');
assert.equal($('db-stages-role').options.find(o=>o.value==='admin').textContent,'Admin');
setLanguage('ja');
assert.equal($('db-stages-status').textContent,'読み込み済み');
assert.equal($('db-stages-role').options.find(o=>o.value==='admin').textContent,'管理者');
const order=id=>$('db-stages-panel').descendants().indexOf($(id));
assert.ok(order('db-stages-list')<order('db-stages-observe'));
assert.ok(order('db-stages-observe')<order('db-stages-scan'));
assert.ok(order('db-stages-scan')<order('db-stages-file'));
assert.ok(order('db-stages-file')<order('db-stages-preview'));
assert.ok(order('db-stages-preview')<order('db-stages-impact'));
assert.ok(order('db-stages-impact')<order('db-stages-confirm'));
$('db-stages-name').value='daily';$('db-stages-directory').value='/approved/input';$('db-stages-mode').value='append';$('db-stages-target').value=JSON.stringify(stageRef());await dbStagesSave();await dbStagesFiles();$('db-stages-file').value='input.csv';
const original=route;
route=async(path,options)=>{
 if(path.endsWith('/preview')){const body=JSON.parse(options.body);return {context:body.context,stage_id:'stage-1',revision:savedStages[0].revision,preview:{columns:[{name:'code',type:'varchar'}],rows:[['001']],total_rows:1,impact:{mode:'append',incoming_rows:1,existing_rows:null,after_rows:null}},confirmation_fingerprint:'confirmed'};}
 return original(path,options);
};
await dbStagesPreview();
assert.equal($('db-stages-impact').hidden,false);
assert.match($('db-stages-impact').children[0].textContent,/1 行を追加します。既存の行数を取得できませんでした。/);
assert.equal($('db-stages-confirm').textContent,'1 行を追加する');
assert.equal($('db-stages-confirm').className,'primary');
''')
