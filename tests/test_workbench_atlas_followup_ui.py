"""UX-R4 follow-up: Atlas flow layout, identifiers, controls and scroll."""
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
const atlasText=node=>[node.textContent,...node.descendants().map(child=>child.textContent)].join(' ');
const rawDatabaseId='ffaf65d0-bbbb-4c1d-8e2f-f3cd7d8311f8';
const rawSchemaId='43293940-aaaa-bbbb-cccc-dddddddddddd';
function atlasShow(id){documentRoot.append($(id));return $(id);}
'''

class AtlasFlowFollowupUI(unittest.TestCase):
    javascript = dataops_ui.DataopsUI.javascript

    def run_js(self, body):
        self.javascript(PRELUDE + body)

    def test_unavailable_database_and_schema_options_show_only_unavailable(self):
        self.run_js(r'''
await boot();atlasShow('ingestion');atlasShow('transformation');
inventory=[];dbInventories.clear();dbDatabases=[];dbSchemas=[];dbSchemaScope='';dbSchemaCache.clear();
for(const [panel,role] of [['p35-ingest','admin'],['p35-wap','admin']]){
 const p=dataopsPanels.get(panel);p.started=true;p.databases=[];p.schemas=[];p.database=rawDatabaseId;p.schema=rawSchemaId;p.context={database_id:rawDatabaseId,schema_id:rawSchemaId,warehouse_id:'LOCAL',sql_catalog:'sales',sql_schema:'marts',selected_role:role,binding_revision:1,generation_id:'gen',grant_revision:4};p.invalid=false;dataopsRender(p,true);
 for(const id of [panel+'-database',panel+'-schema']){
  const select=$(id),selected=select.options.find(o=>o.value===(id.endsWith('database')?rawDatabaseId:rawSchemaId));
  assert.ok(selected,id);assert.equal(selected.disabled,true,id);assert.equal(selected.textContent,t('Unavailable'),id);
  assert.equal(selected.textContent.includes(rawDatabaseId),false,id);assert.equal(selected.textContent.includes(rawSchemaId),false,id);
 }
 for(const id of [panel+'-database',panel+'-schema'])for(const option of $(id).options){
  assert.equal(option.textContent.includes(rawDatabaseId),false,id);assert.equal(option.textContent.includes(rawSchemaId),false,id);
  assert.equal(option.textContent.includes(' · '),false,id);
 }
}
dbStages.databases=[];dbStages.schemas=[];dbStages.database=rawDatabaseId;dbStages.schema=rawSchemaId;dbStages.invalid=false;dbStagesRender();
for(const id of ['db-stages-database','db-stages-schema']){
 const select=$(id),selected=select.options.find(o=>o.value===(id.endsWith('database')?rawDatabaseId:rawSchemaId));
 assert.ok(selected,id);assert.equal(selected.disabled,true,id);assert.equal(selected.textContent,t('Unavailable'),id);
 for(const option of select.options){assert.equal(option.textContent.includes(rawDatabaseId),false,id);assert.equal(option.textContent.includes(rawSchemaId),false,id);assert.equal(option.textContent.includes(' · '),false,id);}
}
''')

    def test_database_and_schema_options_use_display_names_only(self):
        self.run_js(r'''
await boot();atlasShow('ingestion');atlasShow('transformation');
for(const [panel,role] of [['p35-ingest','admin'],['p35-wap','admin']]){
 const p=dataopsPanels.get(panel);p.started=true;p.databases=structuredClone(inventory);p.schemas=structuredClone(schemas);p.database='db';p.schema='schema';p.invalid=false;dataopsRender(p,true);
 assert.equal($(panel+'-database').options.find(o=>o.value==='db').textContent,'Sales');
 assert.equal($(panel+'-schema').options.find(o=>o.value==='schema').textContent,'Mart');
}
dbStages.databases=structuredClone(inventory);dbStages.schemas=structuredClone(schemas);dbStages.database='db';dbStages.schema='schema';dbStages.invalid=false;dbStagesRender();
assert.equal($('db-stages-database').options.find(o=>o.value==='db').textContent,'Sales');
assert.equal($('db-stages-schema').options.find(o=>o.value==='schema').textContent,'Mart');
dbMarts.databases=structuredClone(inventory);dbMarts.schemas=structuredClone(schemas);dbMarts.database='db';dbMarts.schema='schema';dbMarts.role='admin';dbMarts.invalid=false;dbMartsRender();
assert.equal($('db-marts-database').options.find(o=>o.value==='db').textContent,'Sales');
assert.equal($('db-marts-schema').options.find(o=>o.value==='schema').textContent,'Mart');
dbDbt.databases=structuredClone(inventory);dbDbt.schemas=structuredClone(schemas);dbDbt.database='db';dbDbt.invalid=false;dbDbtRender();
assert.equal($('db-dbt-database').options.find(o=>o.value==='db').textContent,'Sales');
for(const id of ['p35-ingest-database','p35-ingest-schema','p35-wap-database','p35-wap-schema','db-stages-database','db-stages-schema','db-marts-database','db-marts-schema','db-dbt-database'])
 for(const option of $(id).options)assert.equal(option.textContent.includes(' · '),false,id);
''')

    def test_transformation_opens_and_navigates_at_scroll_top_without_scroll_requests(self):
        self.run_js(r'''
const scrolls=[],focuses=[],into=[];
globalThis.scrollTo=(x,y)=>scrolls.push([x,y]);
Node.prototype.scrollIntoView=function(){into.push(this.id);};
const originalFocus=Node.prototype.focus;Node.prototype.focus=function(options){focuses.push([this.id,options]);return originalFocus.call(this);};
storage.set(dbWorksheetKey,JSON.stringify({active:'a',tabs:[input()]}));
globalThis.location={href:'http://local/#transformation'};
globalThis.history={replaceState:(state,title,url)=>{globalThis.location.href=String(url);},pushState:(state,title,url)=>{globalThis.location.href=String(url);}};
const settle=async()=>{await new Promise(resolve=>setTimeout(resolve,0));await tick();};
await initializeDatabaseUI(session);await dbPageHydration;await dbRuntimeHydration;await settle();
assert.equal(activeTab,'transformation');assert.equal(document.documentElement.scrollTop,0);
assert.ok(scrolls.length>0);assert.equal(scrolls.every(([x,y])=>x===0&&y===0),true);
assert.deepEqual(into,[]);assert.equal(focuses.some(([id,options])=>!options||options.preventScroll!==true),false);
scrolls.length=0;focuses.length=0;await activate($('tab-ingestion'));await settle();
assert.equal(activeTab,'ingestion');assert.equal(scrolls.every(([x,y])=>x===0&&y===0),true);assert.deepEqual(into,[]);assert.equal(focuses.some(([id,options])=>!options||options.preventScroll!==true),false);
scrolls.length=0;focuses.length=0;await activate($('tab-transformation'));await settle();
assert.equal(activeTab,'transformation');assert.equal(document.documentElement.scrollTop,0);assert.equal(scrolls.every(([x,y])=>x===0&&y===0),true);assert.deepEqual(into,[]);assert.equal(focuses.some(([id,options])=>!options||options.preventScroll!==true),false);
scrolls.length=0;focuses.length=0;globalThis.location={href:'http://local/#ingestion'};await workbenchRestorePage(session,{history:true});await activate($('tab-transformation'));await settle();
assert.equal(scrolls.every(([x,y])=>x===0&&y===0),true);assert.deepEqual(into,[]);assert.equal(focuses.some(([id,options])=>!options||options.preventScroll!==true),false);
''')

    def test_atlas_fields_are_full_width_flex_labels_and_actions_do_not_wrap(self):
        self.assertIn('.atlas-fields label { display:grid; gap:6px; flex:1 1 180px; min-width:0; font-size:13px; }', CSS)
        self.assertIn('.atlas-inline-action{white-space:nowrap', COMPACT_CSS)
        self.assertIn('.atlas-flow #p35-ingest-form,.atlas-flow #p35-ingest-destination,.atlas-flow #p35-ingest-destination-fields{width:100%;max-width:none}', CSS)
        self.assertIn('.atlas-flow .atlas-file-details{width:100%;max-width:none}', CSS)
        self.assertIn('.atlas-flow #p35-ingest-dropzone{max-width:100%;width:100%}', CSS)
        self.run_js(r'''
await boot();atlasShow('ingestion');atlasShow('transformation');
await activate($('tab-ingestion'));await tick();
for(const id of ['p35-ingest-database','p35-ingest-schema','p35-ingest-mode','p35-ingest-name','p35-ingest-target','p35-ingest-keys'])
 assert.equal($(id).parentElement.tagName,'LABEL',id);
const destinationChildren=$('p35-ingest-destination-fields').children,position=id=>destinationChildren.indexOf($(id).parentElement);
assert.ok(position('p35-ingest-database')<position('p35-ingest-mode'));
assert.ok(position('p35-ingest-mode')<position('p35-ingest-name'));
assert.equal($('p35-ingest-name').placeholder,'raw_orders');
const visible=$('p35-ingest-destination-fields').children.filter(n=>!n.hidden);
assert.deepEqual(visible.map(n=>n.id||n.tagName),['LABEL','LABEL','LABEL','LABEL','p35-ingest-refresh']);
for(const node of visible.slice(0,4)){assert.equal(node.className,'atlas-field-label');assert.equal(node.children[0].tagName,'SPAN');}
assert.deepEqual(visible.slice(0,4).map(n=>n.children[1].id),['p35-ingest-database','p35-ingest-schema','p35-ingest-mode','p35-ingest-name']);
$('p35-ingest-mode').value='append';$('p35-ingest-mode').onchange();
assert.deepEqual($('p35-ingest-destination-fields').children.filter(n=>!n.hidden).map(n=>n.id||n.tagName),['LABEL','LABEL','LABEL','LABEL','LABEL','p35-ingest-refresh']);
assert.equal($('p35-ingest-destination-fields').children.filter(n=>!n.hidden)[4].children[1].id,'p35-ingest-target');
assert.equal($('p35-ingest-name').parentElement.children[0].textContent,'RAW table name (letters, digits, _; start with a letter)');
setLanguage('ja');assert.equal($('p35-ingest-name').parentElement.children[0].textContent,'RAW テーブル名（英数字・_、先頭は英字）');setLanguage('en');
assert.equal($('p35-ingest-name').parentElement.hidden,false);assert.equal($('p35-ingest-name').value,'');
assert.ok($('p35-ingest-refresh').parentElement.id,'p35-ingest-destination-fields');
assert.equal(atlasHas($('p35-ingest-refresh'),'atlas-inline-action'),true);
await activate($('tab-transformation'));await tick();
for(const id of ['p35-wap-database','p35-wap-schema','p35-wap-model-id','p35-wap-not-null'])
 assert.equal($(id).parentElement.tagName,'LABEL',id);
assert.equal($('p35-wap-refresh').parentElement.id,'transformation-context-fields');
assert.equal(atlasHas($('p35-wap-refresh'),'atlas-inline-action'),true);
assert.equal($('db-stages-refresh').parentElement.id,'db-stages-context-fields');
assert.equal(atlasHas($('db-stages-refresh'),'atlas-inline-action'),true);
''')

    def test_stage_select_label_and_heading_are_not_repeated(self):
        self.run_js(r'''
for(const d of inventory)d.capabilities.stage_admin=true;for(const s of schemas)s.capabilities.stage_admin=true;
await boot();
await activate($('tab-ingestion'));atlasShow('ingestion');await $('ingestion-tab-stages').onclick({preventDefault(){}});await tick();
assert.equal($('db-stages-heading').textContent,'Saved stages');
assert.equal($('db-stages-list').parentElement.children[0].textContent,'Stage');
assert.equal($('db-stages-stage-title').textContent,'No stage selected');
const list=document.createElement('div');list.id='stage-count-probe';
assert.equal($('db-stages-note').textContent,'A stage registers a folder on the Workbench host. Automatic ingestion starts disabled.');
assert.equal([...$('ingestion-atlas').descendants()].filter(n=>n.textContent==='Saved stages').length,2);
''')

    def test_install_runs_with_connected_only_document_and_keeps_destination_order(self):
        self.run_js(r'''
const originalInstall=workbenchAtlasInstall;
workbenchAtlasInstall=function(){};
await boot();
workbenchAtlasInstall=originalInstall;
const top=node=>{let n=node;while(n.parentElement)n=n.parentElement;return n;};
const ingestTop=top(nodes['p35-ingest']);nodes['ingestion']=ingestTop;documentRoot.append(ingestTop);
connectedOnly=true;
workbenchAtlasInstallIngestion(dataopsPanels.get('p35-ingest'));
assert.equal(connected($('p35-ingest-destination-fields')),true);
assert.equal(connected($('p35-ingest-mode')),true);
assert.equal(connected($('p35-ingest-name')),true);
assert.equal(connected($('p35-ingest-target')),true);
assert.equal(connected($('p35-ingest-keys')),true);
const visible=$('p35-ingest-destination-fields').children.filter(n=>!n.hidden);
assert.deepEqual(visible.map(n=>n.id||n.tagName),['LABEL','LABEL','LABEL','LABEL','p35-ingest-refresh']);
assert.deepEqual(visible.slice(0,4).map(n=>n.children[1].id),['p35-ingest-database','p35-ingest-schema','p35-ingest-mode','p35-ingest-name']);
assert.equal($('p35-ingest-name').parentElement.children[0].textContent,'RAW table name (letters, digits, _; start with a letter)');
''')

    def test_active_ingestion_tab_is_current_and_status_is_plain(self):
        self.run_js(r'''
await boot();atlasShow('ingestion');await activate($('tab-ingestion'));await tick();
assert.equal($('ingestion-tab-file').getAttribute('aria-current'),'page');
assert.notEqual($('ingestion-tab-stages').getAttribute('aria-current'),'page');
await $('ingestion-tab-stages').onclick({preventDefault(){}});await tick();
assert.equal($('ingestion-tab-stages').getAttribute('aria-current'),'page');
assert.notEqual($('ingestion-tab-file').getAttribute('aria-current'),'page');
ui($('p35-ingest-status'),'Ready');
const status=$('p35-ingest-status');
assert.equal(atlasHas(status,'status-line'),true);
assert.equal(status.getAttribute('role'),'status');
assert.equal(status.parentElement.className,'atlas-message-slot');
''')

    def test_transformation_has_one_candidate_heading_and_no_old_model_heading(self):
        self.run_js(r'''
const p=await ready('p35-wap');atlasShow('transformation');
assert.equal(connected($('p35-wap-candidate-heading')),false);
assert.equal($('transformation-candidate').descendants().filter(n=>n.tagName==='H2').length,1);
assert.equal($('transformation-candidate').children.filter(n=>n.tagName==='H3').length,0);
const body=$('p35-wap').children[0];
assert.equal(body.children[0].hidden,true);
''')

    def test_css_has_no_atlas_status_border_and_mobile_fields_stack(self):
        self.assertIn('.atlas-message-slot [role=status]{border-left:0;background:none;padding:0;', CSS)
        self.assertIn('.atlas-actions a[aria-current=page]{font-weight:700;text-decoration:underline', CSS)
        self.assertIn('@media(max-width:600px)', COMPACT_CSS)
        self.assertIn('.atlas-fields label { flex-basis:100%; }', CSS)


class AtlasStagesFollowupUI(unittest.TestCase):
    javascript = stages_ui.StagesUI.javascript

    def run_js(self, body):
        self.javascript(PRELUDE + body)

    def test_stage_selected_heading_uses_the_stage_name(self):
        self.run_js(r'''
await openStages();atlasShow('ingestion');
await createStage();assert.equal($('db-stages-stage-title').textContent,'daily');
assert.equal($('db-stages-list').parentElement.children[0].textContent,'Stage');
assert.equal($('db-stages-list').options[0].textContent,'Choose stage');
''')
