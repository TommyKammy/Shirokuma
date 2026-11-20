"""Atlas data exploration: authorized DB-wide search, deep links and schema DDL."""
import re
import unittest

import test_workbench_database_ui as database_ui
from workbench_source import WorkbenchSource


class ExploreUI(unittest.TestCase):
    PRELUDE = r'''
const ref=(id,schema='schema')=>({database_id:'db',schema_id:schema,object_id:id,incarnation:1});
const stable=(id='orders',schema='schema')=>({id,ref:ref(id,schema),kind:'TABLE',physical_name:id,display_name:id,
 capabilities:{resolve:true,columns:true,preview:true,ddl:true}});
'''

    def javascript(self, body):
        database_ui.DatabaseUI.javascript(self, self.PRELUDE + body)

    def test_cross_database_search_uses_new_api_and_reports_partial_results(self):
        self.javascript(r'''
await boot();
$('catalog-database').value='';await $('catalog-database').onchange();
assert.equal(dbExplorer.database_id,'');assert.equal(dbExplorer.schema_id,'');
dbExplorerControls();
const search={items:[{database_id:'db',sql_catalog:'sales',database_display_name:'Sales',schema_id:'schema',schema_sql_name:'marts',name:'orders',kind:'TABLE'}],
 truncated:true,unavailable_databases:[{id:'offline',sql_catalog:'offline'}]};
route=async path=>path.startsWith('/api/v1/objects/search?')?search:{};
$('explore-search').value=' ord ';$('explore-kind').value='TABLE';
await dbExploreLoad();await tick();
const call=calls.filter(c=>c.path.startsWith('/api/v1/objects/search?')).at(-1);
const params=new URLSearchParams(call.path.split('?')[1]);
assert.equal(params.get('q'),'ord');assert.equal(params.get('kind'),'TABLE');assert.equal(params.get('limit'),'20');
const rows=$('explore-results').children;
assert.equal(rows.length,1);assert.match(rows[0].children[1].children[1].textContent,/sales \/ marts/);
assert.equal($('explore-status').textContent,t('{count} objects',{count:1}));
assert.equal($('explore-unavailable').hidden,false);assert.equal($('explore-unavailable').textContent,t('{count} databases could not be loaded',{count:1}));
assert.equal($('explore-truncated').hidden,false);assert.equal($('explore-truncated').textContent,t('Showing the top {limit} results. Narrow the search.',{limit:20}));
assert.equal($('explore-page').hidden,false);assert.equal($('explore-back').hidden,true);
dbSchemaCache.clear();dbExplorer.schemas=[];
route=async path=>path.startsWith('/api/v1/objects/search?')?search:path.startsWith('/api/v1/objects/resolve?')?
 {object:stable('orders','schema'),ref:stable('orders','schema').ref}:{};
await $('explore-results').children[0].onclick({preventDefault(){},button:0});await tick();
assert.equal(dbObject.id,'orders');assert.equal($('explore-page').hidden,true);
''')

    def test_schema_list_uses_existing_api_and_old_responses_are_discarded(self):
        self.javascript(r'''
await boot();
Object.assign(dbExplorer,{database_id:'db',schema_id:'schema',binding_revision:1,generation_id:'gen',grant_revision:4,invalid:false,schemas:structuredClone(schemas),schemaScope:dbSchemaKey(currentRole,'db')});
dbExplorerControls();const deferred=[];objectsRoute=()=>new Promise(resolve=>deferred.push(resolve));
$('explore-search').value='a';const old=dbExploreLoad();
await tick();assert.equal(deferred.length,1);
++dbExplore.request;deferred[0]({items:[stable('orders')],next_cursor:null});await old;
assert.equal($('explore-results').children.length,0);
$('explore-search').value='b';const fresh=dbExploreLoad();
await tick();assert.equal(deferred.length,2);
deferred[1]({items:[{...stable('refund'),physical_name:'refund',display_name:'Refund'}],next_cursor:null});await fresh;
assert.equal(dbExplore.error,'',dbExplore.error);
assert.equal(dbExplore.items.length,1,JSON.stringify(dbExplore));
assert.equal($('explore-results').children.length,1);
assert.match($('explore-results').children[0].children[1].children[0].textContent,/Refund/);
assert.equal($('explore-status').textContent,t('{database} / {schema} · {shown} of {total} objects',
 {database:'sales',schema:'marts',shown:1,total:1}));
const paths=calls.filter(c=>c.path.includes('/api/v1/schemas/schema/objects?')).slice(-2).map(c=>new URLSearchParams(c.path.split('?')[1]).get('q'));
assert.deepEqual(paths,['a','b']);
''')

    def test_deep_link_resolves_object_and_failure_returns_to_the_list(self):
        self.javascript(r'''
await boot();
assert.deepEqual(dbExploreParse('sales.marts.order%2Eitems'),{catalog:'sales',schema:'marts',name:'order.items'});
assert.equal(dbExplorePath({sql_catalog:'sales'},{sql_name:'marts'},{physical_name:'order.items'}),'sales.marts.order%2Eitems');
route=async path=>path.startsWith('/api/v1/objects/resolve?')?{object:stable('orders'),ref:stable('orders').ref}:{};
await dbRestoreExplore('sales.marts.orders');
assert.equal(dbWorkspacePane,'details');assert.equal($('explore-page').hidden,true);assert.equal($('explore-back').hidden,false);
assert.equal(dbObject.id,'orders');
const resolve=calls.filter(c=>c.path.startsWith('/api/v1/objects/resolve?')).at(-1);
const params=new URLSearchParams(resolve.path.split('?')[1]);
assert.equal(params.get('database_id'),'db');assert.equal(params.get('schema_id'),'schema');assert.equal(params.get('name'),'orders');
route=async path=>{if(path.startsWith('/api/v1/objects/resolve?'))throw Object.assign(Error('not found'),{code:'NOT_FOUND',status:404});return {};};
await dbRestoreExplore('sales.marts.missing');
assert.equal(dbObject,null);assert.equal($('explore-page').hidden,false);assert.equal($('explore-back').hidden,true);
assert.equal($('explore-notice').hidden,false);assert.equal($('explore-notice').textContent,t('This object cannot be opened.'));
assert.equal($('catalog-error').hidden,true);assert.equal($('catalog-error').textContent,'');
''')

    def test_deep_link_route_restores_on_reload(self):
        self.javascript(r'''
await boot();
globalThis.location={href:'http://local/#explore/sales.marts.orders'};
route=async path=>path.startsWith('/api/v1/objects/resolve?')?{object:stable('orders'),ref:stable('orders').ref}:{};
await workbenchRestorePage({username:'owner',authenticated:true,maximum_role:'admin'});
assert.equal(activeTab,'warehouse');assert.equal(dbWorkspacePane,'details');
assert.equal(dbObject.id,'orders');
globalThis.location={href:'http://local/#explore'};
await workbenchRestorePage({username:'owner',authenticated:true,maximum_role:'admin'});await tick();
assert.equal(dbObject,null);assert.equal($('explore-page').hidden,false);
''')

    def test_row_opens_deep_url_and_use_in_sql_hands_off_role(self):
        self.javascript(r'''
await boot();
globalThis.location={href:'http://local/#explore'};
globalThis.history={pushState:(state,title,url)=>{globalThis.location.href=String(url);},replaceState:(state,title,url)=>{globalThis.location.href=String(url);}};
Object.assign(dbExplorer,{database_id:'db',schema_id:'schema',binding_revision:1,generation_id:'gen',grant_revision:4,invalid:false,schemas:structuredClone(schemas),schemaScope:dbSchemaKey(currentRole,'db')});
objectsRoute=async()=>({items:[stable('orders')],next_cursor:null});
dbExplorerControls();$('explore-search').value='orders';await dbExploreLoad();
const row=$('explore-results').children[0];assert.ok(row);
await row.onclick({preventDefault(){},button:0});await tick();
assert.match(globalThis.location.href,/#explore\/sales\.marts\.orders$/);
assert.equal(dbObject.id,'orders');assert.equal($('explore-use-sql').disabled,false);
currentRole='data_engineer';dbInventories.set('data_engineer',{items:structuredClone(dbDatabases)});dbExplorer.schemaScope=dbSchemaKey('data_engineer','db');dbExplorerControls();await dbExploreUseInSql();
assert.equal(activeTab,'warehouse');assert.equal(dbCurrent().database_id,'db');assert.equal(dbCurrent().schema_id,'schema');
assert.equal(dbCurrent().selected_role,'data_engineer');
''')

    def test_schema_tab_contains_ddl_from_the_existing_detail_api(self):
        self.javascript(r'''
await boot();
Object.assign(dbExplorer,{database_id:'db',schema_id:'schema',binding_revision:1,generation_id:'gen',grant_revision:4,invalid:false,schemas:structuredClone(schemas),schemaScope:dbSchemaKey(currentRole,'db')});
const object=stable('orders','schema');
route=async path=>{
 if(path.includes('detail=columns'))return {columns:[{name:'id',type:'bigint',nullable:false}]};
 if(path.includes('detail=ddl'))return {query:{data:[['CREATE TABLE sales.marts.orders (id BIGINT)']],columns:['ddl'],state:'FINISHED'}};
 return {};
};
await dbDescribe(object,'columns');await tick();await tick();
assert.ok($('table-ddl').textContent.includes('CREATE TABLE sales.marts.orders'));
assert.equal(inspectorTabs.some(tab=>tab.dataset.inspect==='ddl'),false);
assert.equal($('inspect-tab-columns').textContent,'Schema');
assert.equal($('inspect-ddl').hidden,true);assert.ok($('inspect-columns').children.includes($('table-ddl')));
assert.equal($('table-ddl-heading').hidden,false);
''')

    def test_search_input_is_debounced_and_unavailable_is_cleared(self):
        self.javascript(r'''
await boot();
Object.assign(dbExplorer,{database_id:'db',schema_id:'schema',binding_revision:1,generation_id:'gen',grant_revision:4,invalid:false,schemas:structuredClone(schemas),schemaScope:dbSchemaKey(currentRole,'db')});
dbExplorerControls();objectsRoute=async()=>({items:[],next_cursor:null});
dbExplore.unavailable=[{id:'offline',sql_catalog:'offline'}];dbExplorePaint();
assert.equal($('explore-unavailable').hidden,false);
const timers=[];globalThis.setTimeout=(callback,delay)=>{timers.push({callback,delay});return timers.length;};
globalThis.clearTimeout=id=>{if(timers[id-1])timers[id-1].cleared=true;};
$('explore-search').value='a';$('explore-search').dispatchEvent({type:'input'});
$('explore-search').value='b';$('explore-search').dispatchEvent({type:'input'});
assert.equal(timers.length,2);assert.equal(timers[0].cleared,true);assert.equal(timers[1].delay,250);
timers[1].callback();await tick();
const params=new URLSearchParams(calls.filter(c=>c.path.includes('/api/v1/schemas/schema/objects?')).at(-1).path.split('?')[1]);
assert.equal(params.get('q'),'b');assert.equal($('explore-unavailable').hidden,true);
''')

    def test_command_palette_search_item_opens_explore_url(self):
        self.javascript(r'''
await boot();
globalThis.location={href:'http://local/#explore'};
globalThis.history={pushState:(state,title,url)=>{globalThis.location.href=String(url);},replaceState:(state,title,url)=>{globalThis.location.href=String(url);}};
dbDatabases=[{...dbDatabases[0],id:'db',sql_catalog:'sales',state:'READY',capabilities:{browse:true,query:true}}];
dbSchemaCache.set(dbSchemaKey(currentRole,'db'),structuredClone(schemas));
route=async path=>path.startsWith('/api/v1/objects/resolve?')?{object:stable('orders','schema'),ref:stable('orders','schema').ref}:{};
const item={database_id:'db',sql_catalog:'sales',database_display_name:'Sales',schema_id:'schema',schema_sql_name:'marts',name:'orders',kind:'TABLE'};
await activate($('tab-catalog'));await dbExploreOpenSearchItem(item);await tick();
assert.equal(dbObject.id,'orders');assert.match(globalThis.location.href,/#explore\/sales\.marts\.orders$/);
''')

    def test_detail_area_is_hidden_until_schema_or_object_is_selected(self):
        self.javascript(r'''
await boot([{...input(),database_id:'',schema_id:''}]);
assert.equal($('database-details').dataset.detailEmpty,'true');
assert.equal($('explore-page').hidden,false);
Object.assign(dbExplorer,{database_id:'db',schema_id:'',binding_revision:1,generation_id:'gen',grant_revision:4,
 invalid:false,schemas:structuredClone(schemas),schemaScope:''});
dbExplorerControls();
assert.equal($('database-details').dataset.detailEmpty,'true');
dbExplorer.schema_id='schema';dbExplorer.schemaScope=dbSchemaKey(currentRole,'db');dbExplorerControls();
assert.equal($('database-details').dataset.detailEmpty,'false');
dbObject=stable('orders');dbExplorerControls();
assert.equal($('database-details').dataset.detailEmpty,'false');
dbObject=null;dbExplorerControls();
assert.equal($('database-details').dataset.detailEmpty,'false');
''')

    def test_empty_detail_css_hides_every_non_explore_child(self):
        css = re.sub(r'/\*[\s\S]*?\*/', '', WorkbenchSource(database_ui.HTML).css)
        self.assertIn('#database-details[data-detail-empty=true] > :not(#explore-page):not(#explore-back)', css)
        self.assertRegex(css, r'#database-details\[data-detail-empty=true\][^{]+\{[^}]*display:none\s*!important')

    def test_role_select_matches_role_selector_labels_in_both_languages(self):
        self.javascript(r'''
await boot();
const options=()=>[...$('explore-role').children];
assert.deepEqual(options().map(option=>option.value),['bi_viewer','data_engineer','admin']);
assert.deepEqual(options().map(option=>option.textContent),['BI Viewer','Data Engineer','Admin']);
setLanguage('ja');
assert.deepEqual(options().map(option=>option.textContent),['閲覧者','データ作成者','管理者']);
assert.deepEqual(options().map(option=>option.value),['bi_viewer','data_engineer','admin']);
setLanguage('en');
assert.deepEqual(options().map(option=>option.textContent),['BI Viewer','Data Engineer','Admin']);
$('explore-role').value='data_engineer';await dbExploreRoleChanged();
assert.equal(currentRole,'data_engineer');assert.equal($('role-selector').value,'data_engineer');
assert.deepEqual(options().map(option=>option.value),['bi_viewer','data_engineer']);
assert.deepEqual(options().map(option=>option.textContent),['BI Viewer','Data Engineer']);
setLanguage('ja');
assert.deepEqual(options().map(option=>option.textContent),['閲覧者','データ作成者']);
assert.equal($('explore-role').parentElement.children[0].textContent,'参照ロール');
''')

    def test_non_ready_databases_are_absent_from_explorer_worksheet_and_palette(self):
        self.javascript(r'''
await boot();
const ready={...dbDatabases[0],id:'ready',sql_catalog:'ready',display_name:'Ready',state:'READY'};
const retiring={...dbDatabases[0],id:'retiring',sql_catalog:'retiring',display_name:'Retiring',state:'RETIRING'};
dbDatabases=[ready,retiring];
dbExplorer.database_id='';dbExplorer.schema_id='';dbExplorer.schemas=[];dbExplorer.schemaScope='';
dbExplorerControls();
assert.deepEqual([...$('catalog-database').options].map(option=>option.value).filter(Boolean),['ready']);
const tree=treeActions('select-database').map(node=>node.dataset.treeKey);
assert.deepEqual(tree,['database:ready']);
const role=dbCurrent().selected_role;
dbInventories.set(role,{items:[ready,retiring],request:1,version:0});
dbControls();
assert.ok([...$('query-database').options].some(option=>option.value==='ready'));
assert.ok(![...$('query-database').options].some(option=>option.value==='retiring'));
$('workbench-command-search').value='Retiring';
assert.deepEqual(workbenchCommandEntries().filter(entry=>entry.kind==='Database'),[]);
dbExplorer.database_id='retiring';dbExplorer.schema_id='';dbExplorerControls();
assert.equal($('catalog-database').value,'');
assert.ok(![...$('catalog-database').options].some(option=>option.value==='retiring'));
dbCurrent().database_id='retiring';dbControls();
assert.equal($('query-database').value,'');
assert.ok(![...$('query-database').options].some(option=>option.value==='retiring'));
$('explore-search').value='x';
const before=calls.length;await dbExploreLoad();
assert.equal(calls.length,before);
assert.equal($('explore-status').textContent,t('Selected context is unavailable for this role. Choose another context.'));
dbDatabases=[retiring];dbExplorerControls();
assert.equal(treeActions('select-database').length,0);
assert.equal($('catalog-tree').textContent,t('No databases available.'));
''')
