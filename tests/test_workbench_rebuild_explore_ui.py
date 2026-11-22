"""UX-R3: data exploration rebuilt from the Atlas mock-up (list and detail)."""
import re
import unittest

import test_workbench_database_ui as harness
from workbench_source import WorkbenchSource

SCRIPT = harness.SCRIPT
CSS = WorkbenchSource(harness.HTML).css
COMPACT_CSS = re.sub(r'\s+', '', re.sub(r'/\*[\s\S]*?\*/', '', CSS))

PRELUDE = r'''
const ref=(id,schema='schema')=>({database_id:'db',schema_id:schema,object_id:id,incarnation:1});
const stable=(id='orders',schema='schema')=>({id,ref:ref(id,schema),kind:'TABLE',physical_name:id,display_name:id,
 capabilities:{resolve:true,columns:true,preview:true,snapshots:true,ddl:true}});
const listed=(id='orders')=>({...stable(id),label:'Orders table',column_count:4});
const exploreClasses=node=>(node.className||'').split(' ').filter(Boolean);
const exploreHas=(node,name)=>exploreClasses(node).includes(name);
const exploreFlat=root=>{const out=[];const walk=node=>{for(const child of node.children||[]){out.push(child);walk(child);}};walk(root);return out;};
const exploreText=id=>{const node=$(id);return [node.textContent,...node.descendants().map(child=>child.textContent)].join(' ');};
const exploreNodeText=node=>[node.textContent,...node.descendants().map(child=>child.textContent)].join(' ');
const rawDatabaseId='ffaf65d0-bbb0-4c1d-8e2f-123456789abc';
const rawSchemaId='43293940-aaaa-bbbb-cccc-dddddddddddd';
async function exploreContextReady(){
 Object.assign(dbExplorer,{database_id:'db',schema_id:'schema',binding_revision:1,generation_id:'gen',grant_revision:4,
  invalid:false,schemas:structuredClone(schemas),schemaScope:dbSchemaKey(currentRole,'db')});
 dbExplorerControls();
}
async function exploreDetailReady(){
 await boot();await exploreContextReady();activate($('tab-catalog'));
 route=async path=>{
  if(path.includes('detail=snapshots'))return {snapshots:[{snapshot_id:'11',parent_id:null,timestamp_ms:1},{snapshot_id:'10',parent_id:'9',timestamp_ms:2}]};
  if(path.includes('detail=columns'))return {columns:[{name:'id',type:'bigint',nullable:false},{name:'name',type:'varchar',nullable:true},{name:'total',type:'decimal',nullable:true}]};
  if(path.includes('detail=preview'))return {columns:['id','name'],data:[[1,'a']],state:'FINISHED'};
  if(path.includes('/lineage?'))return {focus:{kind:'TABLE',label:'sales.schema.orders',ref:ref('orders'),snapshot:'11'},confirmed:[],declared:[],coverage:{scope:'visible_saved_records',limit:200}};
  return {};
 };
 objectsRoute=async()=>({items:[listed('orders')],next_cursor:null});
 $('explore-search').value='';await dbExploreLoad();
 await $('explore-results').children[0].onclick({preventDefault(){},button:0});await tick();
}
'''


class RebuildExploreUI(unittest.TestCase):
    javascript = harness.DatabaseUI.javascript

    def run_js(self, body):
        self.javascript(PRELUDE + body)

    def test_database_boot_mounts_each_explore_control_once(self):
        self.run_js(r'''
await boot();
const attached=$('warehouse').descendants();
const ids=['explore-page','explore-detail','explore-heading','explore-environment','catalog-database','catalog-schema','explore-role',
 'explore-search','explore-kind','explore-status','explore-notice','explore-unavailable','explore-truncated','explore-results',
 'explore-empty','explore-more','explore-back','explore-eyebrow','table-title','explore-favorite','open-table-sql',
 'explore-detail-role','explore-permission','explore-detail-meta','inspector-context','inspector-context-name','snapshot-context',
 'inspector-tabs','inspect-tab-preview','inspect-tab-columns','inspect-tab-snapshots',
 'inspect-tab-profile','inspect-tab-lineage','inspect-preview','inspect-columns','inspect-snapshots',
 'inspect-profile','inspect-lineage','table-preview','table-columns','table-ddl',
 'table-snapshots','snapshot-preview','explore-snapshot-select','explore-snapshot-open','table-profile-panel-body','table-null-results','table-null-hint','explore-more-actions','explore-management','db-table-manage','db-manage-copy',
 'db-manage-remove','table-operations','table-operations-body','catalog-tree','catalog-filter','worksheet-explorer','workspace-details','database-page-heading'];
for(const id of ids){
 const matches=attached.filter(node=>node.id===id);
 assert.equal(matches.length,1,id+' count='+matches.length);
 assert.equal($(id),matches[0],id);
}
assert.equal($('db-table-manage').parentElement,$('explore-management'));
assert.equal($('explore-management').parentElement,$('database-details'));
assert.equal($('table-operations').parentElement,$('database-details'));
assert.equal($('table-operations-body').parentElement,$('table-operations'));
assert.equal($('drop-table-btn').parentElement.parentElement,$('db-manage-remove'));
assert.equal($('catalog-database').parentElement.tagName,'LABEL');
assert.equal($('catalog-schema').parentElement.tagName,'LABEL');
''')

    def test_list_structure_and_order_match_the_mock(self):
        self.run_js(r'''
await boot();$('tab-catalog').onclick();
const page=$('explore-page'),children=page.children;
assert.equal(page.className,'explore-page');
assert.equal(children[0].className,'pageheading');
assert.equal(children[0].children[0].children[0].id,'explore-heading');
assert.equal(children[0].children[0].children[0].tagName,'H1');
assert.equal(children[0].children[1].id,'explore-environment');assert.equal(children[0].children[1].className,'tiny muted');
assert.equal($('database-page-heading').hidden,true);
assert.equal($('explore-page').descendants().filter(node=>node.tagName==='H1'&&!node.hidden).length,1);
assert.equal(children[1].className,'explore-controls');
assert.equal(children[2].className,'explore-search');
assert.equal(children[3],$('explore-status'));
assert.equal($('explore-status').getAttribute('role'),'status');
assert.equal(children[4],$('explore-notice'));
assert.equal(children[5],$('explore-unavailable'));
assert.equal(children[6],$('explore-truncated'));
assert.equal(children[7],$('explore-results'));
assert.equal($('explore-results').className,'explore-rows');
assert.equal(children[8],$('explore-empty'));
assert.equal(children[9],$('explore-more'));
assert.equal($('catalog-database').parentElement.parentElement,children[1]);
assert.equal($('catalog-schema').parentElement.parentElement,children[1]);
assert.equal($('explore-role').parentElement.parentElement,children[1]);
assert.equal($('explore-search').parentElement.parentElement,children[2]);
assert.equal($('explore-kind').parentElement.parentElement,children[2]);
assert.equal($('catalog-database').parentElement.textContent.includes('DB'),true);
assert.equal($('catalog-database').className.includes('field'),true);
assert.equal($('catalog-filter').parentElement.parentElement.id,'worksheet-explorer');
assert.equal(page.descendants().some(node=>node.id==='catalog-tree'),false);
''')

    def test_detail_structure_order_tabs_and_management(self):
        self.run_js(r'''
await exploreDetailReady();
const detail=$('explore-detail'),children=detail.children;
assert.equal(detail.hidden,false);assert.equal($('explore-page').hidden,true);
assert.equal($('database-page-heading').hidden,true);
assert.equal(detail.descendants().filter(node=>node.tagName==='H1'&&!node.hidden).length,1);
assert.equal(children[0],$('explore-back'));assert.equal($('explore-back').className,'backlink');
assert.equal(children[1].className,'pageheading');
assert.equal(children[2].className,'explore-controls');
assert.equal(children[3].className,'toolbar');
assert.equal(children[4],$('explore-more-actions'));assert.equal($('explore-more-actions').tagName,'DETAILS');assert.equal($('explore-more-actions').open,false);
assert.equal($('insert-table-sql').parentElement.parentElement,$('explore-more-actions'));
assert.equal(children[5],$('inspector-context'));
assert.equal($('inspector-context').className,'inspector-context');
assert.equal(children[6],$('inspector-tabs'));
assert.equal($('inspector-tabs').className,'tabletabs');
const heading=children[1],title=heading.children[0],actions=heading.children[1];
assert.equal(title.children[0].id,'explore-eyebrow');assert.equal(title.children[0].className,'eyebrow');
assert.equal(title.children[1].id,'table-title');assert.equal(title.children[1].tagName,'H1');assert.equal(title.children[1].textContent,'orders');
assert.equal(actions.children[0],$('explore-favorite'));
assert.equal(actions.children[1],$('open-table-sql'));assert.equal($('open-table-sql').className,'primary');
assert.equal($('explore-favorite').getAttribute('aria-pressed'),'false');
assert.equal($('explore-permission').className,'status success');assert.equal($('explore-permission').textContent,'Readable');
assert.equal($('explore-detail-role').parentElement.tagName,'LABEL');
const tabs=[...$('inspector-tabs').children];
assert.deepEqual(tabs.map(tab=>tab.textContent),['Data preview','Schema','History & time travel','Profile & NULL stats','Lineage']);
assert.deepEqual(tabs.map(tab=>tab.dataset.inspect),['preview','columns','snapshots','profile','lineage']);
for(const tab of tabs){assert.equal(tab.getAttribute('role'),'tab');assert.equal($(tab.getAttribute('aria-controls')).getAttribute('role'),'tabpanel');}
assert.equal($('inspect-preview').hidden,false);assert.equal($('inspect-columns').hidden,true);
assert.equal($('inspect-snapshots').children.includes($('snapshot-preview')),true);
assert.equal($('inspect-snapshots').children.includes($('table-snapshots')),true);
assert.equal($('inspect-profile').children.includes($('table-profile-panel-body')),true);
assert.equal($('inspect-profile').children.includes($('table-null-results')),true);
const panels=detail.children.filter(node=>exploreHas(node,'inspector-panel'));
assert.deepEqual(panels.map(panel=>panel.id),['inspect-preview','inspect-columns','inspect-snapshots','inspect-profile','inspect-lineage']);
assert.equal(detail.children.includes($('explore-management')),false);
assert.equal($('database-details').children.includes($('explore-management')),true);
assert.equal($('database-details').children.includes($('table-operations')),true);
assert.equal($('table-operations-summary').parentElement,$('table-operations'));
assert.equal(detail.descendants().some(node=>node.id==='catalog-tree'),false);
assert.equal(detail.descendants().some(node=>node.id==='worksheet-explorer'),false);
assert.equal(document.title,'orders · Data Explorer · Shirokuma Workbench');
documentRoot.append($('warehouse'));
for(const id of [...Object.values(inspectorTargets),'snapshot-preview','table-null-results','table-null-hint','table-profile-panel-body','explore-detail','explore-more-actions','explore-snapshot-select','explore-snapshot-open']){
 const node=nodes[id];assert.ok(node,id);assert.equal(connected(node),true,id+' detail');
}
''')

    def test_details_pane_is_single_column_and_hides_the_tree(self):
        self.run_js(r'''
await boot();$('tab-catalog').onclick();
assert.equal($('database-workspace').dataset.pane,'details');
assert.equal($('database-workspace').hidden,false);
assert.equal($('database-details').hidden,false);
assert.equal($('database-worksheet-pane').hidden,true);
assert.equal($('worksheet-bar').hidden,true);
assert.equal($('explore-page').hidden,false);assert.equal($('explore-detail').hidden,true);
''')
        self.assertIn('#database-workspace[data-pane=details]{grid-template-columns:minmax(0,1fr)}', COMPACT_CSS)
        self.assertIn('#database-workspace[data-pane=details]>.explorer{display:none}', COMPACT_CSS)

    def test_status_is_one_neutral_line_and_never_prints_raw_ids(self):
        self.run_js(r'''
await boot();$('tab-catalog').onclick();
dbExplorer.database_id='';dbExplorer.schema_id='';dbExplorer.schemas=[];dbExplorer.schemaScope='';
$('explore-search').value='';$('explore-kind').value='';await dbExploreLoad();
assert.equal($('explore-status').textContent,'Next, choose a database.');
assert.equal($('explore-notice').hidden,true);
assert.equal($('explore-unavailable').hidden,true);
assert.equal(nodes['explorer-task-context'],undefined);
assert.equal($('explore-status').descendants().length,0);
await exploreContextReady();
objectsRoute=async()=>({items:[listed('orders')],next_cursor:null});
await dbExploreLoad();await tick();
assert.equal($('explore-status').textContent,t('{database} / {schema} · {shown} of {total} objects',{database:'Sales',schema:'Mart',shown:1,total:1}));
assert.equal($('catalog-database').options.map(option=>option.textContent).some(value=>value.includes('db')),false);
dbDatabases=[{...inventory[0],id:rawDatabaseId,display_name:rawDatabaseId,sql_catalog:'sales'}];
dbExplorer.database_id='db';dbExplorer.schema_id='schema';dbExplorer.schemaScope=dbSchemaKey(currentRole,'db');dbExplorer.schemas=structuredClone(schemas);dbExplorerControls();
const neutral=$('catalog-database').options.find(option=>option.value===rawDatabaseId);
assert.equal(neutral.textContent, t('Unavailable database'));
assert.equal(neutral.textContent.includes(rawDatabaseId),false);
assert.equal(neutral.disabled,false);
dbDatabases=structuredClone(inventory);
dbExplorer.database_id=rawDatabaseId;dbExplorer.schema_id=rawSchemaId;dbExplorer.schemaScope=dbSchemaKey(currentRole,rawDatabaseId);dbExplorer.schemas=[];dbExplorerControls();
for(const select of ['catalog-database','catalog-schema']){
 for(const option of $(select).options){assert.equal(option.textContent.includes(rawDatabaseId),false,select);assert.equal(option.textContent.includes(rawSchemaId),false,select);}
}
const retainedDatabase=$('catalog-database').options.find(option=>option.value===rawDatabaseId);
const retainedSchema=$('catalog-schema').options.find(option=>option.value===rawSchemaId);
assert.equal(retainedDatabase.textContent,t('Unavailable'));assert.equal(retainedDatabase.disabled,true);
assert.equal(retainedSchema.textContent,t('Unavailable'));assert.equal(retainedSchema.disabled,true);
assert.equal($('explore-status').textContent.includes(rawDatabaseId),false);
''')

    def test_invalid_context_uses_the_neutral_one_line_without_repeated_guidance(self):
        self.run_js(r'''
await boot();$('tab-catalog').onclick();
Object.assign(dbExplorer,{database_id:'db',schema_id:'schema',binding_revision:1,generation_id:'gen',grant_revision:4,
 invalid:true,schemas:structuredClone(schemas),schemaScope:dbSchemaKey(currentRole,'db')});
await dbExploreLoad();
assert.equal($('explore-status').textContent,'This context is not available. Choose another database and schema.');
assert.equal($('explore-notice').hidden,true);
assert.equal($('explore-unavailable').hidden,true);
assert.equal($('explore-truncated').hidden,true);
assert.doesNotMatch($('database-details').textContent,/Selected context is unavailable|Choose another context|Browsing selection/);
''')

    def test_hash_navigation_list_detail_and_browser_back_forward(self):
        self.run_js(r'''
await boot();globalThis.location={href:'http://local/#explore'};
globalThis.history={pushState:(state,title,url)=>{globalThis.location.href=String(url);},replaceState:(state,title,url)=>{globalThis.location.href=String(url);}};
await exploreContextReady();objectsRoute=async()=>({items:[listed('orders')],next_cursor:null});
route=async path=>path.includes('detail=preview')?{columns:['id'],data:[[1]],state:'FINISHED'}:path.includes('/objects/resolve?')?
 {object:stable('orders'),ref:stable('orders').ref}:{};
await dbExploreLoad();assert.equal($('explore-page').hidden,false);assert.equal($('explore-detail').hidden,true);
await $('explore-results').children[0].onclick({preventDefault(){},button:0});await tick();
assert.match(globalThis.location.href,/#explore\/sales\.marts\.orders$/);
assert.equal(dbObject.id,'orders');assert.equal($('explore-page').hidden,true);assert.equal($('explore-detail').hidden,false);
globalThis.location={href:'http://local/#explore'};
await workbenchRestorePage(session);await tick();
assert.equal(dbObject,null);assert.equal($('explore-page').hidden,false);assert.equal($('explore-detail').hidden,true);
globalThis.location={href:'http://local/#explore/sales.marts.orders'};
await workbenchRestorePage(session);await tick();
assert.equal(dbObject.id,'orders');assert.equal($('explore-page').hidden,true);assert.equal($('explore-detail').hidden,false);
$('explore-back').onclick({preventDefault(){}});await tick();
assert.equal(dbObject,null);assert.equal($('explore-page').hidden,false);assert.equal($('explore-detail').hidden,true);
''')

    def test_tabs_load_the_existing_detail_endpoints_and_time_travel(self):
        self.run_js(r'''
await exploreDetailReady();
const tab=name=>inspectorTabs.find(button=>button.dataset.inspect===name);
const callsFor=part=>calls.filter(call=>call.path.includes(part)).length;
await tab('preview').click();await tick();
assert.equal($('inspect-preview').hidden,false);assert.deepEqual($('table-preview').rows,[[1,'a']]);
assert.equal(callsFor('detail=preview')>=1,true);
await tab('columns').click();await tick();
assert.equal($('inspect-columns').hidden,false);assert.deepEqual($('table-columns').rows,[['id','bigint',false],['name','varchar',true],['total','decimal',true]]);
assert.equal($('table-ddl-heading').parentElement,$('inspect-columns'));
assert.equal(callsFor('detail=columns')>=1,true);
assert.equal($('explore-detail-meta').textContent.startsWith('3 columns'),true);
await tab('snapshots').click();await tick();
assert.equal($('inspect-snapshots').hidden,false);assert.deepEqual($('table-snapshots').rows,[['11',null,1],['10','9',2]]);
const previewButton=[...$('table-snapshots').children].find(node=>node.tagName==='BUTTON');
assert.ok(previewButton);
await previewButton.onclick();await tick();
assert.equal(activeInspector,'snapshots');assert.equal($('inspect-snapshots').hidden,false);assert.deepEqual($('snapshot-preview').rows,[[1,'a']]);
assert.match($('snapshot-context').textContent,/11/);
assert.equal($('inspect-snapshots').descendants().some(node=>exploreHas(node,'snapshot-controls')),true);
assert.deepEqual([...$('explore-snapshot-select').options].map(option=>option.value),['11','10']);
assert.equal($('explore-snapshot-select').value,'11');assert.equal($('explore-snapshot-open').textContent,'Preview Snapshot');
tab('profile').click();await tick();assert.equal($('inspect-profile').hidden,false);assert.ok($('table-profile-run'));
assert.equal($('inspect-profile').children.includes($('table-null-results')),true);assert.ok($('table-null-hint'));
tab('lineage').click();await tick();assert.equal($('inspect-lineage').hidden,false);assert.ok($('table-lineage').descendants().length>0);
assert.equal(calls.filter(call=>call.options?.method==='POST').length,0);
''')

    def test_detail_opens_with_preview_loaded(self):
        self.run_js(r'''
await exploreDetailReady();
assert.equal(activeInspector,'preview');assert.equal($('inspect-preview').hidden,false);
assert.deepEqual($('table-preview').rows,[[1,'a']]);
assert.equal(calls.filter(call=>call.path.includes('detail=preview')).length>=1,true);
''')

    def test_connected_boot_has_every_inspector_node_in_the_document(self):
        self.run_js(r'''
const detached=document.createElement('div');detached.id='detached-probe';documentRoot.append(detached);detached.remove();assert.equal(connected(detached),false);
storage.set(dbWorksheetKey,JSON.stringify({active:'a',tabs:[input()]}));
await initializeSession();await dbPageHydration;await dbRuntimeHydration;
await activate($('tab-catalog'));await tick();
documentRoot.append($('warehouse'));
const ids=[...Object.values(inspectorTargets),'snapshot-preview','table-null-results','table-null-hint','table-profile-panel-body',
 'explore-page','explore-detail','explore-heading','explore-environment','explore-back','explore-eyebrow','table-title','explore-favorite',
 'open-table-sql','explore-detail-role','explore-permission','explore-detail-meta','inspector-context','inspector-context-name','snapshot-context',
 'inspector-tabs','explore-more-actions','insert-table-sql','use-explorer-schema','explore-use-sql','refresh-table-details',
 'explore-snapshot-select','explore-snapshot-open','explore-management','db-table-manage','table-operations','table-operations-body',
 'catalog-tree','catalog-filter','worksheet-explorer','workspace-details','database-page-heading'];
for(const id of ids){const node=nodes[id];assert.ok(node,id);assert.equal(connected(node),true,id);}
documentRoot.append(nodes['warehouse']);connectedOnly=true;
assert.equal($('snapshot-preview'),nodes['snapshot-preview']);
assert.equal($('table-profile-panel-body'),nodes['table-profile-panel-body']);
dbClearExplorer();dbExplorerControls();
for(const id of ids){const node=nodes[id];assert.ok(node,id);assert.equal(connected(node),true,id+' strict');}
''')

    def test_snapshot_preview_is_captured_before_the_history_panel_is_replaced(self):
        capture = SCRIPT.index("const snapshotPreview=$('snapshot-preview')")
        replace = SCRIPT.index('historyPanel.replaceChildren(')
        self.assertLess(capture, replace)

    def test_favorite_adds_removes_from_sidebar_and_opens_the_detail(self):
        self.run_js(r'''
await exploreDetailReady();
const favorite=$('explore-favorite');assert.equal(favorite.getAttribute('aria-pressed'),'false');
favorite.onclick();
assert.equal(favorite.getAttribute('aria-pressed'),'true');assert.equal($('workbench-favorites').hidden,false);
assert.equal($('workbench-favorite-list').children.length,1);
const link=$('workbench-favorite-list').children[0];
assert.equal(link.href,'#explore/sales.marts.orders');assert.match(link.children[1].textContent,/orders/);
const saved=JSON.parse(storage.get('shirokuma.favorites.v1.local'));
assert.deepEqual(saved,[{label:'orders',href:'#explore/sales.marts.orders'}]);
favorite.onclick();
assert.equal(favorite.getAttribute('aria-pressed'),'false');assert.equal($('workbench-favorites').hidden,true);
assert.equal(JSON.parse(storage.get('shirokuma.favorites.v1.local')).length,0);
favorite.onclick();
globalThis.location={href:'http://local/#explore'};globalThis.history={pushState:(s,t,url)=>{globalThis.location.href=String(url);},replaceState:(s,t,url)=>{globalThis.location.href=String(url);}};
await workbenchRestorePage(session);await tick();
assert.equal($('explore-detail').hidden,true);
globalThis.location={href:new URL(link.href,'http://local/').href};
route=async path=>path.includes('/objects/resolve?')?{object:stable('orders'),ref:stable('orders').ref}:path.includes('detail=preview')?{columns:['id'],data:[[1]],state:'FINISHED'}:{};
await workbenchRestorePage(session);await tick();
assert.equal(dbObject.id,'orders');assert.equal($('explore-detail').hidden,false);
''')

    def test_command_palette_search_lands_on_the_new_detail(self):
        self.run_js(r'''
await boot();globalThis.location={href:'http://local/#explore'};
globalThis.history={pushState:(state,title,url)=>{globalThis.location.href=String(url);},replaceState:(state,title,url)=>{globalThis.location.href=String(url);}};
dbDatabases=[{...dbDatabases[0],id:'db',sql_catalog:'sales',display_name:'Sales',state:'READY',capabilities:{browse:true,query:true}}];
dbSchemaCache.set(dbSchemaKey(currentRole,'db'),structuredClone(schemas));
route=async path=>path.startsWith('/api/v1/objects/resolve?')?{object:stable('orders'),ref:stable('orders').ref}:path.includes('detail=preview')?{columns:['id'],data:[[1]],state:'FINISHED'}:{};
const item={database_id:'db',sql_catalog:'sales',database_display_name:'Sales',schema_id:'schema',schema_sql_name:'schema',name:'orders',kind:'TABLE'};
await activate($('tab-catalog'));await dbExploreOpenSearchItem(item);await tick();
assert.equal(dbWorkspacePane,'details');assert.equal(dbObject.id,'orders');
assert.match(globalThis.location.href,/#explore\/sales\.marts\.orders$/);
assert.equal($('explore-detail').hidden,false);assert.equal($('table-title').textContent,'orders');
''')

    def test_japanese_labels_have_no_english_only_ui_text(self):
        self.run_js(r'''
await boot();setLanguage('ja');$('tab-catalog').onclick();
assert.equal($('explore-heading').textContent,'データ探索');
assert.equal($('catalog-database').parentElement.textContent.includes('DB'),true);
assert.equal($('catalog-database').options[0].textContent,'データベースを選択');
dbExplorer.database_id='';dbExplorer.schema_id='';dbExplorer.schemas=[];await dbExploreLoad();
assert.equal($('explore-status').textContent,'次にDBを選択してください。');
assert.deepEqual([...$('explore-role').options].map(option=>option.textContent),['閲覧者','データ作成者','管理者']);
await exploreContextReady();
objectsRoute=async()=>({items:[listed('orders')],next_cursor:null});
await dbExploreLoad();await tick();
const row=$('explore-results').children[0];
assert.match(exploreNodeText(row),/テーブル/);assert.match(exploreNodeText(row),/4 列/);assert.match(exploreNodeText(row),/参照可能/);
assert.doesNotMatch(exploreNodeText(row),/TABLE|Readable/);
await row.onclick({preventDefault(){},button:0});await tick();
assert.deepEqual([...$('inspector-tabs').children].map(tab=>tab.textContent),['データプレビュー','スキーマ','履歴・タイムトラベル','プロファイル・NULL統計','リネージ']);
assert.equal($('explore-permission').textContent,'参照可能');
assert.match($('explore-detail-meta').textContent,/管理者/);
assert.match($('explore-favorite').textContent,/お気に入り/);
assert.equal($('explore-back').textContent,'← データ探索');
assert.equal($('explore-more-actions').children[0].textContent,'その他の操作');
assert.doesNotMatch(exploreNodeText($('explore-more-actions')),/Schema/);
assert.equal(document.title,'orders · データ探索 · Shirokuma Workbench');
setLanguage('en');
assert.equal($('explore-heading').textContent,'Data Explorer');
assert.equal($('explore-back').textContent,'← Data Explorer');
''')

    def test_390_and_320px_layout_css_keeps_horizontal_scroll_inside_tables(self):
        for width in ('320', '390'):
            with self.subTest(width=width):
                self.assertIn('@media(max-width:600px)', COMPACT_CSS)
        self.assertIn('.explore-controls,.explore-search{display:flex;flex-wrap:wrap', COMPACT_CSS)
        self.assertIn('.tabletabs{overflow-x:auto;max-width:100%', COMPACT_CSS)
        self.assertIn('.tabletabsbutton{min-height:44px;white-space:nowrap}', COMPACT_CSS)
        self.assertIn('.explore-rows.objectrow{min-height:44px}', COMPACT_CSS)
        self.assertIn('.tablewrap{position:relative;overflow:auto;max-width:100%', COMPACT_CSS)
        self.assertIn('.pageheading{flex-wrap:wrap}', COMPACT_CSS)
        self.assertIn('#explore-detail#table-title{font-family:var(--sans)', COMPACT_CSS)


if __name__ == '__main__':
    unittest.main()
