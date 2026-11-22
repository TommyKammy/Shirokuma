"""UX-R4: ingestion, saved stages and transformation rebuilt from the Atlas mock-up."""
import re
import unittest

import test_workbench_dataops_ui as dataops_ui
import test_workbench_database_ui as database_ui
import test_workbench_stages_database_ui as stages_ui
from workbench_source import WorkbenchSource

HTML = database_ui.HTML
CSS = WorkbenchSource(HTML).css
COMPACT_CSS = re.sub(r'\s+', '', re.sub(r'/\*[\s\S]*?\*/', '', CSS))

PRELUDE = r'''
const atlasClass=node=>(node?.className||'').split(' ').filter(Boolean);
const atlasHas=(node,name)=>atlasClass(node).includes(name);
const atlasAll=(root,selector)=>root.descendants().filter(node=>selector==='[role=status]'?node.role==='status':node.tagName===selector);
const atlasVisibleStatuses=root=>atlasAll(root,'[role=status]').filter(node=>!node.hidden&&node.textContent.trim());
const atlasText=node=>[node.textContent,...node.descendants().map(child=>child.textContent)].join(' ');
const rawDatabaseId='ffaf65d0-bbb0-4c1d-8e2f-123456789abc';
const rawSchemaId='43293940-aaaa-bbbb-cccc-dddddddddddd';
function atlasShow(id){documentRoot.append($(id));return $(id);}
'''


class RebuildIngestTransformUI(unittest.TestCase):
    javascript = dataops_ui.DataopsUI.javascript

    def run_js(self, body):
        self.javascript(PRELUDE + body)

    def test_boot_mounts_each_ingestion_and_transformation_operation_control_once(self):
        self.run_js(r'''
await boot();
atlasShow('ingestion');atlasShow('transformation');
const ingestion=$('ingestion-atlas'),transformation=$('transformation-atlas');
for(const [flow,heading] of [[ingestion,'ingestion-heading'],[transformation,'transformation-heading']]){
 assert.equal(flow.descendants().filter(node=>node.tagName==='H1').length,1);
 assert.equal(flow.descendants().filter(node=>node.tagName==='H1')[0].id,heading);
 assert.equal($(heading).hidden,false);
 assert.equal(atlasHas(flow,'atlas-flow'),true);
 assert.equal(flow.descendants().some(node=>node.tagName==='NAV'&&atlasHas(node,'subnav')),false);
}
for(const [id,parent] of [['ingestion-role-field',ingestion],['atlas-ingestion-message',ingestion],
 ['ingestion-method',ingestion],['ingestion-file',ingestion],['ingestion-stages',ingestion],
 ['transformation-role-field',transformation],['atlas-transformation-message',transformation],
 ['transformation-atlas-models',transformation],['transformation-publications',transformation],['transformation-atlas-dbt',transformation]]){
 const node=$(id);assert.ok(node,id);assert.equal(node.parentElement,parent,id);assert.equal(connected(node),true,id);
}
for(const id of ['ingestion-tab-file','ingestion-tab-stages'])assert.equal($(id).parentElement,$('ingestion-method'),id);
assert.equal($('transformation-atlas-marts').parentElement,$('transformation-publications'));
assert.equal($('ingestion-legacy').hidden,true);assert.equal($('transformation-legacy').hidden,true);
const ingestIds=['p35-ingest-form','p35-ingest-file','p35-ingest-choose-file','p35-ingest-mode','p35-ingest-name','p35-ingest-target',
 'p35-ingest-keys','p35-ingest-preview','p35-ingest-sample','p35-ingest-impact','p35-ingest-confirm','p35-ingest-options'];
const transformIds=['p35-wap-form','transformation-source','p35-wap-model-id','transformation-operation','p35-wap-not-null',
 'p35-wap-save','p35-wap-saved-version','p35-wap-editor','p35-wap-build-action','p35-wap-audit-action','copilot-publish',
 'p35-wap-publish','p35-wap-models','transformation-model-history','db-marts-list','db-marts-save','db-marts-check',
 'db-marts-refresh-run','db-marts-history-refresh','transformation-open-published','transformation-dbt'];
for(const id of ingestIds.concat(transformIds)){const node=$(id);assert.ok(node,id);assert.equal(connected(node),true,id);}
for(const id of ['p35-ingest-role','db-stages-role','p35-wap-role'])assert.equal(connected($(id)),true,id);
documentRoot.append($('warehouse'));documentRoot.append($('bears'));documentRoot.append($('bear-query-sql'));
const ingestBefore=$('p35-ingest-form'),transformBefore=$('p35-wap-form'),stagesBefore=$('db-stages-panel');
dataopsRender(dataopsPanels.get('p35-ingest'));dataopsRender(dataopsPanels.get('p35-wap'));dbStagesRender();dbMartsRender();
setLanguage('ja');setLanguage('en');
assert.equal($('p35-ingest-form'),ingestBefore);assert.equal($('p35-wap-form'),transformBefore);assert.equal($('db-stages-panel'),stagesBefore);
for(const id of ingestIds.concat(transformIds)){assert.ok(nodes[id],id);assert.equal(connected(nodes[id]),true,id);}
connectedOnly=true;
dataopsRender(dataopsPanels.get('p35-ingest'));dataopsRender(dataopsPanels.get('p35-wap'));
for(const id of ingestIds.concat(transformIds)){assert.ok(nodes[id],id);assert.equal(connected(nodes[id]),true,id+' strict');}
connectedOnly=false;
$('p35-ingest-role').value='data_engineer';await $('p35-ingest-role').onchange();
$('p35-wap-role').value='data_engineer';await $('p35-wap-role').onchange();
for(const id of ingestIds.concat(transformIds)){assert.ok(nodes[id],id);assert.equal(connected(nodes[id]),true,id+' role');}
''')

    def test_ingestion_has_the_mock_flow_and_switches_by_hash(self):
        self.run_js(r'''
for(const d of inventory)d.capabilities.stage_admin=true;for(const s of schemas)s.capabilities.stage_admin=true;
await boot();globalThis.location={href:'http://local/#ingestion'};
globalThis.history={pushState:(state,title,url)=>{globalThis.location.href=String(url);},replaceState:(state,title,url)=>{globalThis.location.href=String(url);}};
await activate($('tab-ingestion'));atlasShow('ingestion');await tick();
assert.equal($('ingestion-heading').textContent,'Ingestion & Stages');
const flow=$('ingestion-atlas'),children=flow.children;
assert.deepEqual(children.map(node=>node.id||node.tagName),['ingestion-heading','ingestion-role-field','atlas-ingestion-message','ingestion-method','ingestion-file','ingestion-stages']);
assert.equal($('ingestion-role-field').descendants().includes($('p35-ingest-role')),true);
assert.equal($('ingestion-role-field').descendants().includes($('db-stages-role')),true);
assert.equal($('db-stages-role').parentElement.hidden,true);
assert.equal($('atlas-ingestion-message').descendants().includes($('p35-ingest-status')),true);
assert.equal($('atlas-ingestion-message').descendants().includes($('db-stages-status')),true);
assert.equal($('p35-ingest-status').hidden,false);assert.equal($('db-stages-status').hidden,true);
ui($('p35-ingest-status'),'Ready');
assert.equal(atlasVisibleStatuses($('atlas-ingestion-message')).length,1);
assert.equal($('ingestion-tab-file').getAttribute('aria-current'),'page');
const form=$('p35-ingest-form');
assert.deepEqual(form.children.slice(0,8).map(node=>node.id||node.tagName),['p35-ingest-file-details','p35-ingest-selected-file','p35-ingest-destination','DIV','p35-ingest-sample','p35-ingest-impact','DIV','p35-ingest-options']);
assert.equal($('p35-ingest-context-controls').hidden,true);
assert.equal($('p35-ingest-file-details').tagName,'DETAILS');assert.equal($('p35-ingest-file-details').open,true);
assert.equal($('p35-ingest-options').tagName,'DETAILS');assert.ok(!$('p35-ingest-options').open);
const destination=$('p35-ingest-destination-fields');
for(const id of ['p35-ingest-database','p35-ingest-schema','p35-ingest-mode','p35-ingest-name','p35-ingest-target','p35-ingest-keys'])
 assert.equal($(id).parentElement.parentElement,destination,id);
assert.equal($('p35-ingest-target-summary').parentElement,$('p35-ingest-destination'));
assert.notEqual($('p35-ingest-file-status').getAttribute('role'),'status');
await $('ingestion-tab-stages').onclick({preventDefault(){}});await tick();
assert.equal($('ingestion-file').hidden,true);assert.equal($('ingestion-stages').hidden,false);
assert.equal($('ingestion-tab-stages').getAttribute('aria-selected'),'true');
assert.equal(globalThis.location.href,'http://local/#ingestion/stages');
assert.equal($('db-stages-status').hidden,false);assert.equal(atlasVisibleStatuses($('atlas-ingestion-message')).length,1);
globalThis.location={href:'http://local/#ingestion'};
await workbenchRestorePage(session,{history:true});await tick();
assert.equal($('ingestion-file').hidden,false);assert.equal($('ingestion-stages').hidden,true);
assert.equal($('ingestion-tab-file').getAttribute('aria-current'),'page');
globalThis.location={href:'http://local/#ingestion/stages'};
await workbenchRestorePage(session,{history:true});await tick();
assert.equal($('ingestion-stages').hidden,false);
assert.equal($('db-stages-status').hidden,false);
''')

    def test_stages_use_the_mock_structure_and_closed_details(self):
        self.run_js(r'''
for(const d of inventory)d.capabilities.stage_admin=true;for(const s of schemas)s.capabilities.stage_admin=true;
await boot();await activate($('tab-ingestion'));atlasShow('ingestion');
await $('ingestion-tab-stages').onclick({preventDefault(){}});await tick();
const panel=$('db-stages-panel').children[0],order=id=>panel.descendants().indexOf($(id));
assert.equal($('db-stages-heading').parentElement,panel);
assert.deepEqual($('db-stages-overview-kv').children.filter(node=>node.tagName==='DT').map(node=>node.textContent),['Stage state','Destination','Latest run','Next plan']);
assert.equal($('db-stages-overview-kv').descendants().includes($('db-stages-context')),true);
assert.equal($('db-stages-overview-kv').descendants().includes($('db-stages-output')),true);
assert.equal($('db-stages-overview-kv').descendants().includes($('db-stages-latest')),true);
assert.equal($('db-stages-overview-kv').descendants().includes($('db-stages-next')),true);
assert.equal(order('db-stages-list')<order('db-stages-overview'),true);
assert.equal(order('db-stages-overview')<order('db-stages-schedule-slot'),true);
assert.equal(panel.children.indexOf($('db-stages-schedule-slot'))<panel.children.indexOf($('db-stages-form').parentElement),true);
assert.equal($('db-stages-form').tagName,'FORM');
assert.equal(order('db-stages-scan')<order('db-stages-file'),true);
assert.equal(order('db-stages-file')<order('db-stages-preview'),true);
assert.equal(order('db-stages-preview')<order('db-stages-impact'),true);
assert.equal(order('db-stages-impact')<order('db-stages-confirm'),true);
assert.equal(order('db-stages-confirm')<order('db-stages-records'),true);
assert.equal($('db-stages-records').tagName,'DETAILS');assert.ok(!$('db-stages-records').open);
assert.equal($('db-stages-records').descendants().includes($('db-stages-history')),true);
assert.equal($('db-stages-records').descendants().includes($('db-stages-observe')),true);
assert.equal($('db-stages-status').getAttribute('role'),'status');
''')

    def test_transformation_has_the_mock_flow_and_closed_details(self):
        self.run_js(r'''
await boot();globalThis.location={href:'http://local/#transformation'};
globalThis.history={pushState:(state,title,url)=>{globalThis.location.href=String(url);},replaceState:(state,title,url)=>{globalThis.location.href=String(url);}};
await activate($('tab-transformation'));atlasShow('transformation');await tick();
const flow=$('transformation-atlas');
assert.equal($('transformation-heading').textContent,'Transformation');
assert.deepEqual(flow.children.map(node=>node.id||node.tagName),['transformation-heading','transformation-role-field','atlas-transformation-message','transformation-atlas-models','transformation-publications','transformation-atlas-dbt']);
assert.equal($('transformation-role-field').descendants().includes($('p35-wap-role')),true);
assert.equal($('atlas-transformation-message').descendants().includes($('p35-wap-status')),true);
assert.equal($('p35-wap-status').hidden,false);ui($('p35-wap-status'),'Ready');assert.equal(atlasVisibleStatuses($('atlas-transformation-message')).length,1);
const form=$('p35-wap-form');
assert.deepEqual(form.children.slice(0,8).map(node=>node.id||node.tagName),['transformation-context-fields','transformation-core-fields','transformation-destination-line','transformation-source-details','DIV','p35-wap-saved-version','p35-wap-editor','transformation-candidate']);
assert.equal($('transformation-core-fields').children.length,4);
assert.equal($('transformation-source').parentElement.tagName,'LABEL');
assert.equal($('p35-wap-model-id').parentElement.parentElement,$('transformation-core-fields'));
assert.equal($('p35-wap-not-null').parentElement.parentElement,$('transformation-core-fields'));
assert.equal($('transformation-operation').tagName,'BUTTON');
assert.match($('transformation-destination-line').textContent.replace(/\s+/g,' '),/RAW → .*\.SANDBOX → .*\.MARTS · same database/);
assert.equal($('p35-wap-editor').tagName,'DETAILS');assert.ok(!$('p35-wap-editor').open);
assert.ok(!$('transformation-source-details').open);
assert.ok(!$('transformation-model-history').open);
assert.ok(!$('transformation-rollback').open);
assert.ok(!$('db-marts-form').parentElement.open);
assert.equal($('p35-wap-save').className.includes('primary'),true);
assert.equal($('transformation-candidate').descendants().includes($('p35-wap-build-action')),true);
assert.equal($('transformation-candidate').descendants().includes($('p35-wap-audit-action')),true);
assert.equal($('transformation-candidate').descendants().includes($('copilot-publish')),true);
assert.equal($('transformation-candidate').descendants().includes($('p35-wap-publish')),true);
assert.equal($('transformation-model-history').descendants().includes($('p35-wap-models')),true);
assert.equal($('transformation-publications').descendants().includes($('db-marts-list')),true);
assert.equal($('transformation-publications').descendants().includes($('transformation-open-published')),true);
assert.equal($('transformation-publications').descendants().includes($('db-marts-panel')),true);
assert.equal($('transformation-rollback').descendants().includes($('p35-wap-rollback')),true);
assert.equal($('transformation-atlas-dbt').descendants().includes($('transformation-dbt')),true);
assert.ok(!$('transformation-dbt').open);
''')

    def test_ingestion_and_transformation_never_show_raw_ids_and_update_the_workspace(self):
        self.run_js(r'''
await boot();atlasShow('ingestion');atlasShow('transformation');
dbCurrent().database_id='db';dbCurrent().schema_id='schema';workbenchRenderWorkspaceNav();
assert.equal($('nav-workspace-context-label').textContent,'Sales / Mart');
dbExplorer.database_id='db';dbExplorer.schema_id='schema';dbExplorer.schemas=structuredClone(schemas);dbExplorer.schemaScope=dbSchemaKey(currentRole,'db');dbExplorerControls();
assert.equal($('nav-workspace-context-label').textContent,'Sales / Mart');
inventory=[{...inventory[0],id:rawDatabaseId,display_name:rawDatabaseId,sql_catalog:'sales'}];
schemas=[{...schemas[0],id:rawSchemaId,display_name:rawSchemaId,sql_name:'marts'}];
dbExplorer.database_id=rawDatabaseId;dbExplorer.schema_id=rawSchemaId;dbExplorer.schemas=structuredClone(schemas);dbExplorer.schemaScope=dbSchemaKey(currentRole,rawDatabaseId);dbExplorerControls();
workbenchRenderWorkspaceNav();
assert.equal($('nav-workspace-context-label').textContent.includes(rawDatabaseId),false);
assert.equal($('nav-workspace-context-label').textContent.includes(rawSchemaId),false);
for(const select of ['p35-ingest-database','p35-ingest-schema','db-stages-database','db-stages-schema','p35-wap-database','p35-wap-schema','db-marts-database','db-marts-schema']){
 for(const option of $(select).options){assert.equal(option.textContent.includes(rawDatabaseId),false,select);assert.equal(option.textContent.includes(rawSchemaId),false,select);}
}
const visible=node=>node.hidden?'':[node.textContent,...node.children.map(visible)].join(' ');
assert.equal(visible($('ingestion-atlas')).includes(rawDatabaseId),false);
assert.equal(visible($('transformation-atlas')).includes(rawDatabaseId),false);
dbStages.stage={id:rawDatabaseId,owner:'owner',selected_role:'admin',revision:1,definition:{name:'daily',directory:'/approved',mode:'create',format:'auto',destination:{database_id:'db',schema_id:'schema',physical_name:'codes'}}};
dbStages.schemas=structuredClone(schemas);dbStages.context={database_id:'db',schema_id:'schema',sql_catalog:'sales',sql_schema:'marts',selected_role:'admin'};dbStagesRender();
assert.equal($('db-stages-owner').textContent.includes(rawDatabaseId),false);
''')

    def test_japanese_and_english_copy(self):
        self.run_js(r'''
await boot();atlasShow('ingestion');atlasShow('transformation');setLanguage('ja');
assert.equal($('ingestion-heading').textContent,'取り込み・ステージ');
assert.equal($('transformation-heading').textContent,'データ変換');
assert.equal($('ingestion-tab-file').textContent,'ファイル取込');
assert.equal($('ingestion-tab-stages').textContent,'保存済みステージ');
assert.equal($('p35-ingest-role').parentElement.children[0].textContent,'操作ロール');
assert.equal($('p35-ingest-destination-heading').textContent,'取込先と更新方法');
assert.equal($('p35-ingest-file-details').children[0].textContent,'入力ファイル（CSV / JSON / JSONL · 最大 10 MiB）');
assert.match(atlasText($('p35-ingest-target-summary')),/対象:/);
assert.equal($('p35-ingest-options').children[0].textContent,'ファイル形式と取り込み設定');
assert.equal($('transformation-destination-line').textContent,'RAW → —.SANDBOX → —.MARTS · 同じ DB 内');
assert.equal($('p35-wap-editor').children[0].textContent,'SQL とソースの詳細');
assert.equal($('transformation-source-details').children[0].textContent,'取込元の列と行');
assert.equal($('transformation-model-history').children[0].textContent,'モデル履歴（0 バージョン）');
assert.equal($('transformation-candidate-heading').textContent,'候補と品質検査');
assert.equal($('transformation-publications-heading').textContent,'公開済み MARTS');
assert.equal($('transformation-rollback').children[0].textContent,'公開履歴とロールバック');
assert.equal($('db-marts-form').parentElement.children[0].textContent,'更新のレビューと保存');
for(const phrase of ['Input file','Destination and update method','Source RAW table','Model history','Candidate and quality checks','Review and save updates','Open published table'])
 assert.equal(atlasText($('transformation-atlas')).includes(phrase)||atlasText($('ingestion-atlas')).includes(phrase),false,phrase);
setLanguage('en');
assert.equal($('ingestion-heading').textContent,'Ingestion & Stages');
assert.equal($('transformation-heading').textContent,'Transformation');
assert.equal($('ingestion-tab-file').textContent,'File import');
assert.equal($('transformation-candidate-heading').textContent,'Candidate and quality checks');
''')

    def test_390_and_320px_css_keeps_touch_targets_and_single_column_fields(self):
        for width in ('320','390'):
            with self.subTest(width=width):
                self.assertIn('@media(max-width:600px)', COMPACT_CSS)
        self.assertIn('#ingestion,#transformation{min-width:0;max-width:100%}', COMPACT_CSS)
        self.assertIn('.atlas-flow .atlas-fields .atlas-field{flex-basis:100%}', CSS)
        self.assertIn('.atlas-flow :is(button,select,input:not([type=checkbox]):not([type=radio]),textarea,summary){min-height:44px', CSS)
        self.assertIn('.atlas-flow a[href]{display:inline-flex', CSS)
        self.assertIn('.atlas-flow .kv{grid-template-columns:minmax(0,1fr)', CSS)
        self.assertNotIn('legacy-tabs', CSS)
        self.assertNotIn('#ingestion-legacy', CSS)
        self.assertNotIn('#transformation-legacy', CSS)


class RebuildStagesUI(unittest.TestCase):
    javascript = stages_ui.StagesUI.javascript

    def run_js(self, body):
        self.javascript(PRELUDE + body)

    def test_stage_controls_stay_connected_through_redraw_and_role_change(self):
        self.run_js(r'''
await openStages();atlasShow('ingestion');atlasShow('transformation');
await $('ingestion-tab-stages').onclick({preventDefault(){}});await tick();
const ids=['db-stages-panel','db-stages-heading','db-stages-context-fields','db-stages-database','db-stages-schema','db-stages-refresh',
 'db-stages-list','db-stages-load','db-stages-reload','db-stages-new','db-stages-overview','db-stages-stage-title','db-stages-overview-kv',
 'db-stages-schedule-slot','db-stages-form','db-stages-name','db-stages-directory','db-stages-mode','db-stages-format','db-stages-destination',
 'db-stages-target','db-stages-options','db-stages-save','db-stages-scan','db-stages-scans','db-stages-scan-observe','db-stages-scan-detail',
 'db-stages-scan-list','db-stages-files','db-stages-file','db-stages-preview','db-stages-preview-detail','db-stages-impact','db-stages-confirm',
 'db-stages-export-source','db-stages-export-file','db-stages-export-format','db-stages-export','db-stages-history','db-stages-history-list',
 'db-stages-observe','db-stages-cancel','db-stages-run-detail','db-stages-receipts','db-stages-receipt','db-stages-inspect-receipt',
 'db-stages-download','db-stages-receipt-detail','db-stages-records'];
for(const id of ids){assert.ok($(id),id);assert.equal(connected($(id)),true,id);}
await createStage();await previewStage();dbStagesRender();
const before=$('db-stages-panel');
dbStagesRender();setLanguage('ja');setLanguage('en');
assert.equal($('db-stages-panel'),before);
for(const id of ids){assert.ok(nodes[id],id+' strict');assert.equal(connected(nodes[id]),true,id+' strict');}
''')

    def test_stage_flow_switches_back_to_file_by_hash(self):
        self.run_js(r'''
await openStages();atlasShow('ingestion');globalThis.location={href:'http://local/#ingestion/stages'};
globalThis.history={pushState:(state,title,url)=>{globalThis.location.href=String(url);},replaceState:(state,title,url)=>{globalThis.location.href=String(url);}};
await workbenchRestorePage(session,{history:true});await tick();
assert.equal($('ingestion-stages').hidden,false);assert.equal($('ingestion-file').hidden,true);
assert.equal($('ingestion-tab-stages').getAttribute('aria-selected'),'true');
assert.equal($('db-stages-status').hidden,false);assert.equal($('p35-ingest-status').hidden,true);
assert.equal(globalThis.location.href,'http://local/#ingestion/stages');
await $('ingestion-tab-file').onclick({preventDefault(){}});await tick();
assert.equal(globalThis.location.href,'http://local/#ingestion');
assert.equal($('ingestion-file').hidden,false);assert.equal($('db-stages-status').hidden,true);
''')

    def test_stage_japanese_copy_and_no_raw_ids(self):
        self.run_js(r'''
await createStage();atlasShow('ingestion');setLanguage('ja');
const panel=$('db-stages-panel');
assert.equal($('db-stages-heading').textContent,'保存済みステージ');
assert.deepEqual([...$('db-stages-overview-kv').children].filter(n=>n.tagName==='DT').map(n=>n.textContent),['状態','出力先','最新実行','次回予定']);
assert.equal($('db-stages-records').children[0].textContent,'実行・出力記録');
assert.equal(panel.textContent.includes('stage-1'),false);
for(const option of $('db-stages-database').options)assert.equal(option.textContent.includes('db-'),false);
setLanguage('en');
assert.equal($('db-stages-heading').textContent,'Saved stages');
''')

    def test_stage_definition_details_are_closed(self):
        self.run_js(r'''
await createStage();atlasShow('ingestion');
const details=$('db-stages-form').parentElement;
assert.equal(details.tagName,'DETAILS');assert.ok(!details.open);
assert.ok(!$('db-stages-records').open);
assert.equal($('db-stages-schedule-slot').tagName,'DIV');
''')

    def test_ingestion_css_keeps_stage_details_wrapped_on_mobile(self):
        self.assertIn('min-height:44px', COMPACT_CSS)
        self.assertIn('.atlas-flow .atlas-section-body{padding:0}', CSS)


class RebuildTransformUI(unittest.TestCase):
    javascript = dataops_ui.DataopsUI.javascript

    def run_js(self, body):
        self.javascript(PRELUDE + body)

    def test_publication_controls_stay_connected_through_redraw(self):
        self.run_js(r'''
const p=await ready('p35-wap');atlasShow('transformation');
const ids=['transformation-publications-heading','transformation-publication-fields','db-marts-list','db-marts-load',
 'db-marts-reload','db-marts-new','transformation-publication-summary','transformation-open-published','transformation-rollback',
 'db-marts-history','db-marts-history-refresh','db-marts-run-detail','db-marts-panel','db-marts-form','db-marts-name','db-marts-models',
 'db-marts-model-id','db-marts-version','db-marts-load-model','db-marts-watch','db-marts-save','db-marts-check','db-marts-check-changes',
 'db-marts-refresh-run','db-marts-observe','db-marts-status','transformation-dbt','db-dbt-panel','db-dbt-compile','db-dbt-build'];
for(const id of ids){assert.ok($(id),id);assert.equal(connected($(id)),true,id);}
const before=$('db-marts-panel');documentRoot.append($('warehouse'));documentRoot.append($('bears'));documentRoot.append($('bear-query-sql'));
connectedOnly=true;
dbMartsRender();dataopsRender(p);
assert.equal($('db-marts-panel'),before);
for(const id of ids){assert.ok(nodes[id],id);assert.equal(connected(nodes[id]),true,id+' strict');}
connectedOnly=false;
setLanguage('ja');setLanguage('en');
for(const id of ids){assert.equal(connected($(id)),true,id+' language');}
''')

    def test_publication_section_matches_mock_order_and_links_the_target(self):
        self.run_js(r'''
const p=await ready('p35-wap');atlasShow('transformation');
const section=$('transformation-publications'),order=id=>section.children.indexOf($(id));
assert.equal(order('transformation-publication-fields')<order('db-marts-target-summary'),true);
assert.equal(order('db-marts-target-summary')<order('transformation-open-published'),true);
assert.equal(order('transformation-open-published')<order('transformation-rollback'),true);
assert.equal(order('transformation-rollback')<section.children.indexOf($('db-marts-form').parentElement),true);
assert.equal($('transformation-rollback').children[0].textContent,'Publication history and rollback');
assert.equal($('db-marts-form').parentElement.children[0].textContent,'Review and save updates');
assert.equal($('db-marts-form').parentElement.descendants().includes($('db-marts-database')),true);
assert.equal($('db-marts-role').parentElement.hidden,true);
assert.equal($('transformation-open-published').hidden,true);
''')

    def test_transformation_operation_opens_the_closed_sql_details(self):
        self.run_js(r'''
const p=await ready('p35-wap');atlasShow('transformation');
assert.ok(!$('p35-wap-editor').open);
$('transformation-operation').onclick();
assert.ok($('p35-wap-editor').open);
''')

    def test_transformation_japanese_copy_has_no_english_only_sentences(self):
        self.run_js(r'''
const p=await ready('p35-wap');atlasShow('transformation');setLanguage('ja');
assert.equal($('p35-wap-role').parentElement.children[0].textContent,'操作ロール');
assert.equal($('transformation-core-fields').children[1].children[0].textContent,'モデル名（英数字・_、先頭は英字）');
assert.equal($('transformation-core-fields').children[2].children[0].textContent,'変換処理');
assert.equal($('transformation-core-fields').children[3].children[0].textContent,'品質検査のキー列（NULL 不可）');
const text=atlasText($('transformation-atlas'));
for(const phrase of ['Source RAW table','Edit SQL','Quality key columns','Model history','SQL and sources','Review and save updates','Open published table'])assert.equal(text.includes(phrase),false,phrase);
''')
