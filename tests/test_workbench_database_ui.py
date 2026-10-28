"""Database UI production handlers, using a small Node DOM/fetch adapter."""
from pathlib import Path
import json
import re
import subprocess
import unittest

from test_catalog_ui import function


HTML = (Path(__file__).resolve().parents[1] / 'tools/local_experiment/workbench/index.html').read_text()
SCRIPT = re.search(r'<script>([\s\S]*?)</script>', HTML)[1]
DATABASE = SCRIPT[SCRIPT.index("const dbWorksheetKey="):SCRIPT.index('let initialLanguage;')]
NAVIGATION = SCRIPT[SCRIPT.index('const tabs='):SCRIPT.index('setInterval(async()=>')]
NAVIGATION_HANDLERS = NAVIGATION[NAVIGATION.index('tabs.forEach'):]
LEGACY_ACTIVATE = SCRIPT[SCRIPT.index('function activate('):SCRIPT.index('const tabs=')].replace('function activate(', 'function legacyActivate(')
PERIODIC_REFRESH = SCRIPT[SCRIPT.index('setInterval(async()=>'):SCRIPT.index('// Small DOM helpers')].replace('setInterval(', 'const periodicRefresh=registerInterval(', 1)
TRANSLATIONS = SCRIPT[SCRIPT.index('const i18n = '):SCRIPT.index("let language = 'en';")]
MENU = re.findall(r'<button id="tab-([^"]+)"[^>]*data-tab="[^"]+"[^>]*>(.*?)</button>', HTML)


class DatabaseUI(unittest.TestCase):
    def javascript(self, body):
        source = r'''
const assert=require('node:assert/strict');
const nodes={},storage=new Map(),calls=[],writes=[];
class Node {
 constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.value='';this.textContent='';this.disabled=false;this.hidden=false;this.selectionStart=0;this.selectionEnd=0;this.listeners={};}
 set id(value){this._id=value;nodes[value]=this;} get id(){return this._id;}
 append(...items){for(const item of items){if(item.parentElement)item.parentElement.children=item.parentElement.children.filter(c=>c!==item);this.children.push(item);item.parentElement=this;if(item.id)nodes[item.id]=item;}}
 prepend(...items){this.append(...items);this.children=[...items,...this.children.filter(c=>!items.includes(c))];} before(item){if(item.id)nodes[item.id]=item;}
 after(...items){for(const item of items)if(item.id)nodes[item.id]=item;}
 replaceChildren(...items){this.children=[];delete this.rows;this.textContent='';this.append(...items);}
 add(item){this.append(item);} get options(){return this.children;}
 setAttribute(key,value){this[key]=value;} getAttribute(key){return this[key];}
 addEventListener(key,fn){(this.listeners[key]??=[]).push(fn);} focus(){document.activeElement=this;}
 click(){return this.onclick?.();}
 dispatchEvent(event){for(const fn of this.listeners[event.type]||[])fn(event);}
 setRangeText(value,start,end){this.value=this.value.slice(0,start)+value+this.value.slice(end);}
 setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end;}
 querySelectorAll(selector){const all=this.descendants();return all.filter(c=>selector==='[role=tab]'?c.role==='tab':selector==='[data-tree-key]'?!!c.dataset.treeKey:selector==='.worksheet-close'?c.className==='worksheet-close':selector==='th'?false:['BUTTON','INPUT','SELECT','TEXTAREA'].includes(c.tagName));}
 querySelector(selector){const all=this.children.flatMap(c=>[c,...c.descendants()]);return all.find(c=>selector.startsWith('.')?c.className?.split(' ').includes(selector.slice(1)):c.tagName===selector.toUpperCase());}
 descendants(){return this.children.flatMap(c=>[c,...c.descendants()]);}
}
let strictRemoved=false;
const $=id=>strictRemoved&&['worksheet-tree','worksheet-tree-filter'].includes(id)&&!nodes['worksheet-explorer']?.descendants().some(node=>node.id===id)?null:nodes[id]??=Object.assign(new Node(),{id});
const sampleButtons=['orders','revenue','customers'].map(sample=>Object.assign(new Node('button'),{dataset:{sample}}));
const document={createElement:tag=>new Node(tag),documentElement:{dataset:{}},querySelectorAll:selector=>selector==='[data-sample]'?sampleButtons:selector==='[data-tab]'?tabs:[]};
let narrow=false;const mediaListeners=[];const matchMedia=()=>({matches:narrow,addEventListener:(event,listener)=>mediaListeners.push(listener)});
const Option=function(text,value){return Object.assign(new Node('option'),{textContent:text,value});};
''' + TRANSLATIONS + r'''
const uiBindings=new Map(),uiAttributes=[],languageKey='language';
let language='en';const renderChart=()=>{};
const ui=(node,key,values={})=>{uiBindings.set(node,{key,values});node.isConnected=true;node.textContent=t(key,values);},message=(id,text)=>{$(id).textContent=text;$(id).hidden=!text;};
const table=(node,columns,rows)=>{node.rows=rows;node.columns=columns;};
let currentRole='admin',databaseMode=true,worksheets=[],worksheetId='',queryResult=null,activeTab='warehouse',activeInspector='columns';
const inspectorTargets={columns:'table-columns',preview:'table-preview',snapshots:'table-snapshots',ddl:'table-ddl',profile:'inspect-profile',lineage:'table-lineage'};
const inspectorTabs=['columns','preview','profile','ddl','snapshots','lineage'].map(inspect=>Object.assign(new Node('button'),{dataset:{inspect}}));
const tabs=''' + json.dumps([(name, re.sub('<[^>]+>', '', label)) for name, label in MENU]) + r'''.map(([tab,label])=>{const node=Object.assign(new Node('button'),{id:'tab-'+tab,dataset:{tab},textContent:label});ui(node,label);return node;});
const samples={orders:'SELECT * FROM marts.fct_orders LIMIT 10'};
const confirm=()=>true,populateChartColumns=()=>{},stopJobsPolling=()=>{},stopMartsPolling=()=>{};
const localStorage={getItem:key=>storage.get(key)??null,setItem:(key,value)=>{storage.set(key,value);writes.push(key);}};
const readStorage=(key,fallback)=>{try{return JSON.parse(localStorage.getItem(key))??fallback;}catch{return fallback;}};
const writeStorage=(key,value)=>localStorage.setItem(key,JSON.stringify(value));
const historyPlaceholder=()=>new Option('Restore','');
let renderResults=()=>{for(const id of ['export-csv','export-json','copy-tsv'])$(id).disabled=!queryResult;if(queryResult)table($('query-results'),queryResult.columns,queryResult.rows);};
let api=async()=>({status:'healthy'}),applyRole=()=>{},loadCatalog=()=>{},activate=()=>{},download=()=>{},refreshStatus=()=>{};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
let session={username:'owner',authenticated:true,maximum_role:'admin',platform_admin:false,data_mode:'database'};
let inventory=[{id:'db',display_name:'Sales',sql_catalog:'sales',state:'READY',revision:1,binding_revision:1,generation_id:'gen',grant_revision:4,allowed_roles:['admin','data_engineer','bi_viewer'],capabilities:{browse:true,query:true,manage:false}}];
let schemas=[{id:'schema',database_id:'db',display_name:'Mart',sql_name:'marts',revision:1,allowed_modes:['read-only','sandbox-write'],capabilities:{browse:true,query:true,write:true}},{id:'raw',database_id:'db',display_name:'Raw',sql_name:'raw',revision:1,allowed_modes:['read-only','sandbox-write'],capabilities:{browse:true,query:true,write:true}}];
let objects=[],serverHistory=[],sessionRoute=null,inventoryRoute=null,historyRoute=null,objectsRoute=null,schemasRoute=null,inspectRoute=null,route=async()=>({});
const fetch=async(path,options)=>{
 calls.push({path,options});let data;
 if(path==='/api/session')data=sessionRoute?await sessionRoute(path,options):structuredClone(session);
 else if(path==='/api/v1/databases')data=inventoryRoute?await inventoryRoute(path,options):{items:structuredClone(inventory)};
 else if(path.endsWith('/schemas'))data=schemasRoute?await schemasRoute(path,options):{items:structuredClone(schemas)};
 else if(path.includes('/objects?'))data=objectsRoute?await objectsRoute(path,options):{items:structuredClone(objects),next_cursor:null};
 else if(path.startsWith('/api/v1/queries?'))data=historyRoute?await historyRoute(path,options):{queries:structuredClone(serverHistory)};
 else if(/^\/api\/v1\/queries\/[^/]+$/.test(path)&&!options?.method)data=inspectRoute?await inspectRoute(path,options):structuredClone(serverHistory.find(q=>q.id===path.split('/').at(-1)));
 else data=await route(path,options);
 if(data?.malformed)return {ok:true,status:200,json:async()=>{throw SyntaxError('Malformed response');}};
 if(data?.http)return {ok:false,status:data.http,json:async()=>data.body};
 return {ok:true,status:200,json:async()=>data};
};
$('query-schema').parentElement=new Node();
const layout=Object.assign(new Node(),{className:'sql-layout'}),main=Object.assign(new Node(),{className:'sql-main'}),editor=new Node(),results=new Node(),strip=new Node(),inspector=new Node(),head=new Node(),actions=new Node(),filter=new Node();
$('warehouse').append(layout);layout.append($('worksheet-explorer'),main);$('worksheet-explorer').append(new Node('summary'),$('worksheet-tree-filter'),$('worksheet-tree'));
main.append(editor,results);editor.append(strip,$('sql'));strip.append($('worksheet-tabs'),$('add-worksheet-tab'));results.append($('query-results'));
head.append($('table-title'),actions);inspector.append(head,$('inspector-tabs'),...Object.values(inspectorTargets).map($),$('snapshot-preview'));actions.append($('open-table-sql'));
filter.append($('catalog-filter'));$('catalog').append(filter,$('catalog-tree'),inspector,$('catalog-error'));
for(const role of ['admin','data_engineer','bi_viewer'])$('role-selector').add(new Option(role,role));
''' + '\n'.join(function(name).split('// Register only source UI nodes')[0] for name in ['t', 'setLanguage', 'worksheetName', 'updateWorksheetCloseLabels', 'closeWorksheet', 'renderWorksheets', 'saveWorksheet', 'addWorksheet', 'wireTabs']) + '\n' + DATABASE + '\n' + NAVIGATION_HANDLERS + '\n' + LEGACY_ACTIVATE + '\nconst registerInterval=callback=>callback;\n' + PERIODIC_REFRESH + r'''
const legacySQLListener=saveWorksheet;$('sql').addEventListener('input',legacySQLListener);
const input=(id='a')=>({id,name:'Worksheet '+(id==='a'?1:2),sql:'SELECT 1',database_id:'db',schema_id:'schema',binding_revision:1,generation_id:'gen',warehouse:'LOCAL',mode:'read-only',max_rows:100});
async function boot(tabs=[input()]){
 storage.set(dbWorksheetKey,JSON.stringify({active:tabs[0].id,tabs}));
 await initializeDatabaseUI(session);
}
const envelope=(run,extra={})=>({id:'query-uuid',backend_query_id:'engine-id',request_id:run.origin.request_id,worksheet_id:run.origin.worksheet_id,context:{...run.origin.context},columns:[{name:'n',type:'bigint'}],data:[[1]],state:'FINISHED',next_uri:null,cancel_uri:'/api/v1/queries/query-uuid/cancel',...extra});
const historyEntry=(extra={})=>({id:'past',backend_query_id:'engine-past',request_id:'past-request',worksheet_id:'past-worksheet',sql:'SELECT 99',mode:'sandbox-write',context:{...dbContext(dbCurrent()),schema_id:'raw',sql_schema:'raw'},state:'FINISHED',created_at:'2026-10-19T00:00:00Z',...extra});
const salesObject={id:'sales-object',ref:{database_id:'db',schema_id:'raw',id:'sales-object'},physical_name:'orders',display_name:'Orders',kind:'TABLE',capabilities:{resolve:true,columns:true,preview:true}};
const financeObject={...salesObject,id:'finance-object',ref:{database_id:'finance',schema_id:'finance-marts',id:'finance-object'}};
const financeSchema={id:'finance-marts',database_id:'finance',sql_name:'marts',display_name:'Finance marts',revision:1,allowed_modes:['read-only'],capabilities:{browse:true,query:true,write:false}};
async function dualBoot(){
 inventory.push({...inventory[0],id:'finance',sql_catalog:'finance',display_name:'Finance',binding_revision:2,generation_id:'finance-gen',grant_revision:9});
 schemasRoute=async path=>({items:path.includes('/finance/')?[financeSchema]:schemas});
 objectsRoute=async path=>({items:[path.includes('/finance-marts/')?financeObject:salesObject],next_cursor:null});
 await boot([{...input(),schema_id:'raw'}]);
}
async function exploreFinance(){
 $('catalog-database').value='finance';await $('catalog-database').onchange();
 $('catalog-schema').value='finance-marts';await $('catalog-schema').onchange();
}
const treeActions=action=>$('catalog-tree').descendants().filter(node=>node.dataset.action===action);
const objectButtons=()=>treeActions('select-object').filter(node=>node.dataset.treeKey.startsWith('object:'+dbExplorer.database_id+':'+dbExplorer.schema_id+':'));
''' + '\n(async()=>{\n' + body + '\n})().catch(error=>{console.error(error);process.exitCode=1;});'
        result = subprocess.run(['node', '-e', source], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_status_refresh_clears_recovered_error_and_retains_current_failure(self):
        self.javascript(r'''
api=async path=>(await fetch(path)).json();
await boot();
route=async()=>{throw Error('Failed to fetch');};
await refreshStatus();
assert.equal($('status-error').textContent,'Failed to fetch');assert.equal($('status-error').hidden,false);
message('quick-status-error','Existing quick action error');$('quick-status-error').title='Existing quick action error';
route=async()=>({status:'healthy'});
await refreshStatus();
assert.equal($('warehouse-state').textContent,'Ready');
assert.equal($('quick-warehouse-state').textContent,'Database mode supports LOCAL only.');
assert.equal($('status-error').textContent,'');assert.equal($('status-error').hidden,true);
assert.equal($('quick-status-error').textContent,'Existing quick action error');assert.equal($('quick-status-error').hidden,false);
assert.equal($('quick-status-error').title,'Existing quick action error');
route=async()=>{throw Error('Current status failure');};
await refreshStatus();
assert.equal($('status-error').textContent,'Current status failure');assert.equal($('status-error').hidden,false);
assert.equal(calls.filter(c=>c.path==='/api/status').length,3);
''')

    def test_old_identity_status_success_and_error_cannot_repaint_current_status(self):
        self.javascript(r'''
api=async path=>(await fetch(path)).json();
await boot();
const snapshot=()=>['warehouse-state','quick-warehouse-state','status-error','quick-status-error'].map(id=>({text:$(id).textContent,hidden:$(id).hidden,title:$(id).title}));
for(const change of [()=>{dbPrincipal+='-next';},()=>{currentRole=currentRole==='admin'?'bi_viewer':'admin';},()=>{dbEpoch++;}]){
 for(const outcome of ['healthy','unhealthy','error']){
  for(const currentFailure of [false,true]){
   const old=deferred();route=()=>old.promise;
   const pending=refreshStatus();await tick();
   change();
   route=async()=>({status:'healthy'});await refreshStatus();
   if(currentFailure){route=async()=>{throw Error('New identity failure');};await refreshStatus();}
   assert.equal($('warehouse-state').textContent,'Ready');
   assert.equal($('status-error').textContent,currentFailure?'New identity failure':'');
   assert.equal($('status-error').hidden,!currentFailure);
   const before=snapshot();
   if(outcome==='error')old.reject(Error('Old identity failure'));else old.resolve({status:outcome});
   await pending;
   assert.deepEqual(snapshot(),before);
  }
 }
}
''')

    def test_navigation_dom_order_keyboard_and_legacy_page(self):
        self.javascript(r'''
assert.deepEqual(tabs.slice(0,2).map(b=>b.id),['tab-catalog','tab-warehouse']);
assert.deepEqual(tabs.slice(0,2).map(b=>b.textContent),['Data Explorer','SQL Worksheets']);
let refreshes=0;$('refresh-catalog').onclick=()=>refreshes++;
activate=legacyActivate;tabs[0].onclick();assert.equal(activeTab,'catalog');assert.equal($('catalog').hidden,false);assert.equal($('warehouse').hidden,true);assert.equal(refreshes,1);
const key=key=>({key,preventDefault(){}});
tabs[0].onkeydown(key('ArrowDown'));assert.equal(document.activeElement,tabs[1]);assert.equal(activeTab,'warehouse');
tabs[1].onkeydown(key('ArrowLeft'));assert.equal(document.activeElement,tabs[0]);assert.equal(activeTab,'catalog');
tabs[0].onkeydown(key('End'));assert.equal(document.activeElement,tabs.at(-1));
tabs.at(-1).onkeydown(key('Home'));assert.equal(document.activeElement,tabs[0]);
assert.equal(tabs.filter(b=>b.tabIndex===0).length,1);assert.equal(tabs.filter(b=>b.getAttribute('aria-selected')==='true').length,1);
databaseMode=false;setLanguage('ja');assert.equal(tabs[0].textContent,'データ探索');assert.equal(tabs[1].textContent,'SQL ワークシート');
''')

    def test_workspace_navigation_real_pane_switches_and_languages(self):
        self.javascript(r'''
await dualBoot();
const check=(pane)=>{const details=pane==='details',selected=details?'tab-catalog':'tab-warehouse';assert.equal(dbWorkspacePane,pane);assert.equal($('warehouse').getAttribute('aria-labelledby'),selected);assert.equal(tabs.filter(b=>b.getAttribute('aria-selected')==='true').length,1);assert.equal(tabs.filter(b=>b.tabIndex===0).length,1);assert.equal($(selected).getAttribute('aria-selected'),'true');assert.equal($(selected).tabIndex,0);assert.equal($('database-page-title').textContent,t(details?'Data Explorer':'SQL Worksheets'));assert.equal($('database-page-description').textContent,t(details?'Browse Iceberg schemas, inspect columns, and follow dbt model dependencies.':'Query Iceberg tables with the resident Trino warehouse.'));};
check('worksheet');
const key=key=>({key,preventDefault(){}});
$('tab-warehouse').onkeydown(key('ArrowUp'));check('details');assert.equal(document.activeElement,$('tab-catalog'));
$('tab-catalog').onkeydown(key('ArrowRight'));check('worksheet');assert.equal(document.activeElement,$('tab-warehouse'));
$('tab-warehouse').onkeydown(key('End'));assert.equal(document.activeElement,tabs.at(-1));
tabs.at(-1).onkeydown(key('Home'));check('details');assert.equal(document.activeElement,$('tab-catalog'));$('tab-warehouse').onclick();
for(const lang of ['ja','en']){
 dbTreeExpanded.delete('database:finance');dbTreeExpanded.delete('schema:finance:finance-marts');dbRenderTree();
 setLanguage(lang);check('worksheet');
 $('tab-catalog').onclick();check('details');
 $('worksheet-'+worksheetId).onclick();await tick();check('worksheet');
 $('workspace-details').onclick();check('details');
 $('tab-warehouse').onclick();check('worksheet');
 await treeActions('select-database').find(n=>n.dataset.treeKey==='database:finance').onclick({preventDefault(){}});check('details');
 await treeActions('select-schema').find(n=>n.dataset.treeKey==='schema:finance:finance-marts').onclick({preventDefault(){}});check('details');
 $('tab-warehouse').onclick();await objectButtons()[0].onclick();check('details');
 $('tab-warehouse').onclick();$('explorer-schema-details').onclick();check('details');
 await $('open-table-sql').onclick();await tick();check('worksheet');
 const history=historyEntry({context:{...dbContext(dbCurrent())},mode:'read-only'});serverHistory=[history];await dbLoadHistory();$('query-history').value='past';await $('query-history').onchange();check('worksheet');
}
assert.equal(calls.filter(c=>c.options?.method==='POST').length,0);
''')

    def test_sidebar_preserves_editor_results_context_and_explorer(self):
        self.javascript(r'''
await dualBoot();await exploreFinance();await objectButtons()[0].onclick();
$('tab-warehouse').onclick();$('sql').value='SELECT 42';$('sql').setSelectionRange(3,8);dbSave();route=async()=>envelope(dbRuns.get(worksheetId));await dbRunQuery();
const draft=JSON.stringify(worksheets),run=dbRuns.get(worksheetId),origin=run.origin,result=queryResult,rows=JSON.stringify($('query-results').rows),explorer=JSON.stringify(dbExplorer),selection=dbTreeSelection,expanded=[...dbTreeExpanded],object=dbObject;
$('catalog-filter').value='orders';const count=calls.length,writesBefore=writes.length,sql=$('sql').value;
for(let i=0;i<3;i++){$('tab-catalog').onclick();$('tab-warehouse').onclick();}
assert.equal(JSON.stringify(worksheets),draft);assert.equal($('sql').value,sql);assert.equal($('sql').selectionStart,3);assert.equal($('sql').selectionEnd,8);assert.equal(dbRuns.get(worksheetId),run);assert.equal(run.origin,origin);assert.equal(queryResult,result);assert.equal(JSON.stringify($('query-results').rows),rows);
assert.equal(JSON.stringify(dbExplorer),explorer);assert.equal(dbTreeSelection,selection);assert.equal(dbObject,object);assert.deepEqual([...dbTreeExpanded],expanded);assert.equal($('catalog-filter').value,'orders');assert.equal(calls.length,count);assert.equal(writes.length,writesBefore);
''')

    def test_refresh_and_language_keep_focus_and_background_navigation(self):
        self.javascript(r'''
await boot();$('tab-catalog').onclick();$('workspace-details').focus();
const detailsTab=$('workspace-details'),title=$('database-page-title'),binding=uiBindings.get(title);
await periodicRefresh();setLanguage('ja');assert.equal(document.activeElement,detailsTab);assert.equal($('workspace-details'),detailsTab);assert.equal(uiBindings.get(title),binding);assert.equal($('tab-catalog').getAttribute('aria-selected'),'true');assert.equal(title.textContent,'データ探索');
$('tab-jobs').onclick();$('tab-jobs').focus();
await periodicRefresh();dbControls();dbShowWorkspace('worksheet');renderWorksheets();await tick();setLanguage('en');
assert.equal(activeTab,'jobs');assert.equal(document.activeElement,$('tab-jobs'));assert.equal($('jobs').hidden,false);assert.equal($('warehouse').hidden,true);assert.equal($('tab-jobs').getAttribute('aria-selected'),'true');assert.equal($('tab-jobs').tabIndex,0);assert.equal(tabs.filter(b=>b.tabIndex===0).length,1);assert.equal(tabs.filter(b=>b.getAttribute('aria-selected')==='true').length,1);
$('tab-catalog').onclick();assert.equal($('database-page-title').textContent,'Data Explorer');assert.equal($('warehouse').hidden,false);
''')

    def test_reload_uses_saved_worksheet_without_pane_persistence(self):
        self.javascript(r'''
storage.set(dbWorksheetKey,JSON.stringify({active:'b',tabs:[input(),{...input('b'),sql:'SELECT saved'}]}));
await initializeDatabaseUI(session);assert.equal(worksheetId,'b');assert.equal(dbWorkspacePane,'worksheet');assert.equal($('sql').value,'SELECT saved');assert.equal($('warehouse').getAttribute('aria-labelledby'),'tab-warehouse');
$('tab-catalog').onclick();const saved=JSON.parse(storage.get(dbWorksheetKey));assert.equal(saved.active,'b');assert.deepEqual(Object.keys(saved).sort(),['active','tabs']);assert.equal(saved.tabs[1].sql,'SELECT saved');
''')

    def test_old_object_intent_and_background_refresh_cannot_replace_new_selection(self):
        self.javascript(r'''
await dualBoot();await exploreFinance();const oldButton=objectButtons()[0],sales=dbDatabases.find(d=>d.id==='db'),mart=schemas.find(s=>s.id==='schema');
await dbSelectTreeSchema(sales,schemas.find(s=>s.id==='raw'));
const held=deferred(),currentObject={...salesObject,id:'current-object',display_name:'Current Sales object',ref:{database_id:'db',schema_id:'schema',id:'current-object'}};
objectsRoute=async path=>path.includes('/finance-marts/')?held.promise:{items:[currentObject],next_cursor:null};
route=async()=>({columns:[{name:'current_column',type:'int',nullable:true}]});
const selectingOld=oldButton.onclick();await tick();assert.equal(dbExplorer.database_id,'finance');
await dbSelectTreeDatabase(sales);await dbSelectTreeSchema(sales,mart);await $('use-explorer-schema').onclick();await objectButtons()[0].onclick();
const selected=dbTreeSelection.key,object=dbObject,title=$('table-title').textContent,details=JSON.stringify($('table-columns').rows),draft=JSON.stringify(dbInput(dbCurrent()));
held.resolve({items:[financeObject],next_cursor:null});await selectingOld;
assert.equal(dbExplorer.database_id,'db');assert.equal(dbExplorer.schema_id,'schema');assert.equal(dbTreeSelection.key,selected);assert.equal(dbObject,object);
assert.equal($('table-title').textContent,title);assert.equal(JSON.stringify($('table-columns').rows),details);assert.equal(JSON.stringify(dbInput(dbCurrent())),draft);
assert.equal(calls.filter(c=>c.path.startsWith('/api/v1/objects/finance-object')).length,0);
dbObject=financeObject;dbExplorerControls();assert.equal(dbExplorerCanOpen(),false);assert.equal($('open-table-sql').disabled,true);assert.equal($('insert-table-sql').disabled,true);
const tabs=worksheets.length,sql=$('sql').value;await $('open-table-sql').onclick();$('insert-table-sql').onclick();assert.equal(worksheets.length,tabs);assert.equal($('sql').value,sql);
dbObject=object;dbExplorerControls();assert.equal(dbExplorerCanOpen(),true);
const background=deferred();schemasRoute=()=>background.promise;const refreshing=dbRefreshExplorer();await tick();
await dbSelectTreeSchema(sales,mart);await objectButtons()[0].onclick();const newerObject=dbObject,newerSelection=dbTreeSelection.key;
background.resolve({items:[]});await refreshing;assert.equal(dbObject,newerObject);assert.equal(dbTreeSelection.key,newerSelection);assert.equal(dbExplorer.schema_id,'schema');assert.equal(dbExplorerReason(),'');
assert.equal(calls.filter(c=>c.options?.method==='POST').length,0);
''')

    def test_old_more_intent_cannot_forward_cursor_after_schema_navigation(self):
        self.javascript(r'''
await dualBoot();await exploreFinance();objectsRoute=async()=>({items:[financeObject],next_cursor:'finance-cursor'});await dbLoadObjects();
const oldMore=treeActions('more-objects').find(n=>n.dataset.treeKey==='more:schema:finance:finance-marts'),sales=dbDatabases.find(d=>d.id==='db'),mart=schemas.find(s=>s.id==='schema');
objectsRoute=async()=>({items:[salesObject],next_cursor:null});await dbSelectTreeSchema(sales,schemas.find(s=>s.id==='raw'));
const held=deferred();objectsRoute=async path=>path.includes('/finance-marts/')?held.promise:{items:[],next_cursor:null};
const loadingOld=oldMore.onclick();await tick();assert.equal(dbExplorer.database_id,'finance');
await dbSelectTreeDatabase(sales);await dbSelectTreeSchema(sales,mart);await $('use-explorer-schema').onclick();
const selected=dbTreeSelection.key,title=$('table-title').textContent,draft=JSON.stringify(dbInput(dbCurrent()));
held.resolve({items:[financeObject],next_cursor:'finance-cursor'});await loadingOld;
assert.equal(dbExplorer.database_id,'db');assert.equal(dbExplorer.schema_id,'schema');assert.equal(dbTreeSelection.key,selected);assert.equal(dbObject,null);assert.equal($('table-title').textContent,title);
assert.equal(JSON.stringify(dbInput(dbCurrent())),draft);assert.equal(calls.filter(c=>c.path.includes('cursor=finance-cursor')).length,0);
assert.equal(calls.filter(c=>c.options?.method==='POST').length,0);
''')

    def test_removed_legacy_nodes_are_null_through_database_boot_and_refresh(self):
        self.javascript(r'''
strictRemoved=true;await boot();assert.equal($('worksheet-tree'),null);assert.equal($('worksheet-tree-filter'),null);
await dbRefreshAccess();await dbRefreshContext();dbClearWorksheetObjects();dbClearCatalog();
$('role-selector').value='data_engineer';await $('role-selector').onchange();await dbRefreshAccess();
activate($('tab-catalog'));activate($('tab-warehouse'));dbClearCatalog();
assert.equal($('worksheet-tree'),null);assert.equal($('worksheet-tree-filter'),null);
assert.equal($('catalog-tree').parentElement,$('worksheet-explorer'));
''')

    def test_parallel_initial_header_and_saved_role_reads_in_both_orders(self):
        for first in ['admin', 'data_engineer']:
            with self.subTest(first=first):
                self.javascript('const first=' + repr(first) + ';\n' + r'''
inventoryRoute=async(path,options)=>({items:inventory.map(d=>({...d,grant_revision:options.headers['X-Shirokuma-Role']==='admin'?null:7}))});
await boot([{...input(),selected_role:'data_engineer'},{...input('b'),selected_role:'data_engineer'}]);
dbInventories.clear();dbSchemaCache.clear();const gates={admin:deferred(),data_engineer:deferred()};
schemasRoute=async(path,options)=>gates[options.headers['X-Shirokuma-Role']].promise;
const explorer=dbRefreshExplorer(),worksheet=dbRefreshContext();await tick();
gates[first].resolve({items:structuredClone(schemas)});await tick();
gates[first==='admin'?'data_engineer':'admin'].resolve({items:structuredClone(schemas)});
await explorer;await worksheet;
assert.equal(dbSchemaScope,dbSchemaKey('data_engineer','db'));assert.equal(dbExplorer.schemaScope,dbSchemaKey('admin','db'));
assert.equal(dbReason(),'');assert.equal(dbExplorerReason(),'');assert.equal($('run-query').disabled,false);
assert.equal(dbInventoryCurrent(dbRoleItems('admin')),true);assert.equal(dbInventoryCurrent(dbRoleItems('data_engineer')),true);
assert.equal(dbRoleItems('admin')[0].grant_revision,null);assert.equal(dbContext(dbCurrent()).grant_revision,7);
assert.equal(calls.filter(c=>c.options?.method==='POST').length,0);
''')

    def test_header_upgrade_hides_every_snapshot_until_saved_role_proof(self):
        self.javascript(r'''
await boot([{...input(),selected_role:'data_engineer'}]);currentRole='data_engineer';dbApplyRole();await dbRefreshExplorer();await dbRefreshContext();
route=async()=>envelope(dbRuns.get('a'));await dbRunQuery();const run=dbRuns.get('a'),origin=run.origin;
assert.deepEqual(queryResult.rows,[[1]]);const fresh=deferred();inventoryRoute=()=>fresh.promise;
currentRole='admin';dbApplyRole();assert.equal(run.authorized,false);assert.equal(queryResult,null);assert.deepEqual(run.rows,[[1]]);
assert.equal($('request-provenance').children.length,0);assert.ok(!$('query-meta').textContent.includes('sales'));
const checking=dbRevalidateRun(run);await tick();assert.equal(run.authorized,false);
fresh.resolve({items:structuredClone(inventory)});await checking;assert.equal(run.authorized,true);assert.deepEqual(queryResult.rows,[[1]]);
inventoryRoute=async()=>({items:[]});await dbRevalidateRun(run);assert.equal(run.authorized,false);assert.deepEqual(run.rows,[]);
inventoryRoute=null;await dbRevalidateRun(run);assert.equal(run.origin,origin);assert.equal(run.id,'query-uuid');assert.equal(queryResult,null);assert.deepEqual(run.rows,[]);
''')

    def test_grant_change_marks_all_same_role_tabs_before_updating_stamp(self):
        self.javascript(r'''
inventory.push({...inventory[0],id:'other',sql_catalog:'other'});
inventoryRoute=async(path,options)=>({items:inventory.map(d=>({...d,grant_revision:options.headers['X-Shirokuma-Role']==='admin'?null:d.grant_revision}))});
await boot([{...input(),selected_role:'data_engineer'},{...input('b'),selected_role:'data_engineer'},{...input('c'),selected_role:'admin'},{...input('d'),selected_role:'data_engineer',database_id:'other'}]);
inventory[0].grant_revision=5;dbApplyInventory(await dbReadInventory('data_engineer'),'data_engineer');
for(const id of ['a','b'])assert.equal(worksheets.find(w=>w.id===id)._grantChanged,true);
for(const id of ['c','d'])assert.equal(worksheets.find(w=>w.id===id)._grantChanged,undefined);
await dbReselect();assert.equal(worksheets[0]._grantChanged,false);assert.equal(worksheets[1]._grantChanged,true);
const granted=structuredClone(inventory);inventory=inventory.filter(d=>d.id!=='db');dbApplyInventory(await dbReadInventory('data_engineer'),'data_engineer');
inventory=granted;dbApplyInventory(await dbReadInventory('data_engineer'),'data_engineer');
for(const id of ['a','b'])assert.equal(worksheets.find(w=>w.id===id)._grantChanged,true);
for(const id of ['c','d'])assert.equal(worksheets.find(w=>w.id===id)._grantChanged,undefined);
''')

    def test_same_maximum_new_principal_immediately_clears_all_role_metadata(self):
        self.javascript(r'''
await boot([{...input(),selected_role:'data_engineer'}]);route=async()=>envelope(dbRuns.get('a'));await dbRunQuery();const run=dbRuns.get('a'),draft=JSON.stringify(dbInput(dbCurrent()));
assert.ok(dbInventories.size);assert.ok(dbSchemaCache.size);const old=deferred();inventoryRoute=()=>old.promise;
const read=dbReadInventory('data_engineer');await tick();session.username='another-owner';inventoryRoute=null;await dbRefreshAccess();
assert.equal(dbMaximumRole,'admin');assert.equal(dbPrincipal,JSON.stringify(['another-owner',true]));
assert.equal(dbInventories.size,0);assert.equal(dbSchemaCache.size,0);assert.equal(dbTreeObjects.size,0);assert.equal(dbTreePending.size,0);assert.equal(dbDetails.size,0);
assert.equal(dbObservedGrants.size,0);assert.equal(dbInventoryRequests.size,0);assert.equal(dbRoleAuthorizationVersions.size,0);
assert.ok(!$('query-database').options.some(o=>o.textContent.includes('Sales')));assert.equal(treeActions('select-database').length,0);
assert.equal(queryResult,null);assert.deepEqual(run.rows,[]);assert.equal(dbCanCancel(run),false);assert.equal(JSON.stringify(dbInput(dbCurrent())),draft);
old.resolve({items:structuredClone(inventory)});await assert.rejects(read,error=>error.code==='AUTH_READ_STALE');assert.equal(dbInventories.size,0);
''')

    def test_lazy_hierarchy_overviews_search_and_selection_never_post_sql(self):
        self.javascript(r'''
await dualBoot();const draft=JSON.stringify(dbInput(dbCurrent()));
assert.equal(calls.filter(c=>c.path==='/api/v1/databases/finance/schemas').length,0);
await treeActions('select-database').find(n=>n.dataset.treeKey==='database:finance').onclick({preventDefault(){}});
assert.equal(dbWorkspacePane,'details');assert.equal(dbExplorer.schema_id,'');assert.equal($('open-table-sql').disabled,true);
assert.deepEqual($('table-columns').rows,[['Finance marts · marts','']]);assert.equal(calls.filter(c=>c.path.includes('/schemas/finance-marts/objects?')).length,0);
await treeActions('select-schema').find(n=>n.dataset.treeKey==='schema:finance:finance-marts').onclick({preventDefault(){}});
assert.equal(objectButtons().length,1);assert.equal(dbTreeSelection.kind,'schema');
assert.equal(calls.filter(c=>c.path.startsWith('/api/v1/objects/')).length,0);
route=async()=>({columns:[{name:'n',type:'int',nullable:true}]});await objectButtons()[0].onclick();assert.equal(dbTreeSelection.kind,'object');
const selected=dbTreeSelection.key,expanded=[...dbTreeExpanded],rawReads=calls.filter(c=>c.path.includes('/schemas/raw/objects?')).length;
$('catalog-filter').value='orders';await $('catalog-filter').oninput();dbShowWorkspace('worksheet');dbShowWorkspace('details');
assert.equal(dbTreeSelection.key,selected);assert.deepEqual([...dbTreeExpanded],expanded);assert.equal($('catalog-filter').value,'orders');
assert.equal(objectButtons()[0].getAttribute('aria-current'),'true');assert.match($('insert-table-sql').textContent,/Worksheet 1/);
assert.equal(calls.filter(c=>c.path.includes('/schemas/raw/objects?')).length,rawReads);
assert.equal(JSON.stringify(dbInput(dbCurrent())),draft);assert.equal(calls.filter(c=>c.options?.method==='POST').length,0);
$('catalog-filter').value='missing';await $('catalog-filter').oninput();assert.ok($('catalog-tree').descendants().some(n=>n.textContent==='No matching objects in this schema.'));
''')

    def test_empty_schema_new_sql_is_separate_from_saved_role_target_action(self):
        self.javascript(r'''
await dualBoot();$('query-execution-role').value='data_engineer';await $('query-execution-role').onchange();
const empty={...financeSchema,id:'empty',sql_name:'empty',display_name:'Empty'};
schemasRoute=async path=>({items:path.includes('/finance/')?[empty]:schemas});objectsRoute=async()=>({items:[]});
inventoryRoute=async(path,options)=>({items:structuredClone(options.headers['X-Shirokuma-Role']==='data_engineer'?inventory.filter(d=>d.id==='db'):inventory)});
await treeActions('select-database').find(n=>n.dataset.treeKey==='database:finance').onclick({preventDefault(){}});
await treeActions('select-schema').find(n=>n.dataset.treeKey==='schema:finance:empty').onclick({preventDefault(){}});
assert.ok($('catalog-tree').descendants().some(n=>n.textContent==='No objects in this schema.'));assert.equal($('open-table-sql').disabled,false);
const draft=JSON.stringify(dbInput(dbCurrent()));await $('use-explorer-schema').onclick();assert.equal(JSON.stringify(dbInput(dbCurrent())),draft);
await $('open-table-sql').onclick();assert.equal(worksheets.length,2);assert.equal(JSON.stringify(dbInput(worksheets[0])),draft);
assert.equal(dbCurrent().sql,'');assert.equal(dbCurrent().database_id,'finance');assert.equal(dbCurrent().schema_id,'empty');assert.equal(dbCurrent().selected_role,'admin');assert.equal(dbCurrent().mode,'read-only');
assert.equal(dbRuns.has(worksheetId),false);assert.equal(calls.filter(c=>c.options?.method==='POST').length,0);
''')

    def test_details_and_worksheet_tab_keyboard_contract_and_narrow_collapse(self):
        self.javascript(r'''
narrow=true;await boot([input(),input('b'),input('c'),input('d')]);assert.equal($('worksheet-explorer').open,false);
const tabs=$('worksheet-tabs').querySelectorAll('[role=tab]');assert.equal(tabs.length,5);assert.equal(tabs[0].id,'workspace-details');
const key=value=>({key:value,preventDefault(){}});$('worksheet-a').onkeydown(key('Home'));
assert.equal(dbWorkspacePane,'details');assert.equal(document.activeElement,$('workspace-details'));assert.equal($('workspace-details').getAttribute('aria-selected'),'true');
assert.equal($('database-details').getAttribute('role'),'tabpanel');assert.equal($('database-details').getAttribute('aria-labelledby'),'workspace-details');assert.equal($('database-details').hidden,false);
assert.equal($('workspace-details').getAttribute('aria-controls'),'database-details');assert.equal($('database-worksheet-pane').hidden,true);
$('workspace-details').onkeydown(key('End'));await tick();assert.equal(worksheetId,'d');assert.equal(dbWorkspacePane,'worksheet');
assert.equal($('database-worksheet-pane').getAttribute('aria-labelledby'),'worksheet-d');assert.equal($('worksheet-d').getAttribute('aria-controls'),'database-worksheet-pane');
assert.equal($('worksheet-tabs').querySelectorAll('[role=tab]').filter(b=>b.getAttribute('aria-selected')==='true').length,1);
assert.equal($('worksheet-tabs').querySelectorAll('[role=tab]').filter(b=>b.tabIndex===0).length,1);
$('worksheet-explorer').open=true;mediaListeners[0]({matches:true});assert.equal($('worksheet-explorer').open,false);
assert.equal($('role-selector-label').textContent,'Role ceiling');setLanguage('ja');assert.equal($('role-selector-label').textContent,'利用role上限');
''')

    def test_saved_roles_use_separate_inventories_and_grant_stamps(self):
        self.javascript(r'''
inventoryRoute=async(path,options)=>({items:inventory.map(d=>({...d,grant_revision:options.headers['X-Shirokuma-Role']==='admin'?null:7}))});
await boot([{...input(),selected_role:'bi_viewer'},{...input('b'),selected_role:'admin'}]);
assert.equal(currentRole,'admin');assert.equal(dbCurrent().selected_role,'bi_viewer');assert.equal(dbContext(dbCurrent()).grant_revision,7);
assert.equal(dbRoleItems('admin')[0].grant_revision,null);assert.equal(dbRoleItems('bi_viewer')[0].grant_revision,7);
assert.equal(worksheets[1]._grantChanged,undefined);assert.equal(dbReason(),'');
session.role='bi_viewer';route=async()=>envelope(dbRuns.get('a'));await dbRunQuery();
const post=calls.find(c=>c.options?.method==='POST');assert.equal(post.options.headers['X-Shirokuma-Role'],'bi_viewer');
assert.deepEqual(Object.keys(JSON.parse(post.options.body).context).sort(),['database_id','schema_id','warehouse_id','binding_revision','generation_id'].sort());
assert.equal(dbRuns.get('a').origin.context.grant_revision,7);assert.deepEqual(queryResult.rows,[[1]]);
inventoryRoute=async(path,options)=>({items:inventory.map(d=>({...d,grant_revision:options.headers['X-Shirokuma-Role']==='admin'?null:8}))});
const admin=await dbReadInventory('admin');dbApplyInventory(admin,'admin');assert.equal(dbCurrent()._grantChanged,undefined);
const lower=await dbReadInventory('bi_viewer');dbApplyInventory(lower,'bi_viewer');
assert.equal(dbCurrent()._grantChanged,true);assert.equal(worksheets[1]._grantChanged,undefined);assert.deepEqual(dbRuns.get('a').rows,[]);
assert.equal(dbSchemaCache.has(dbSchemaKey('bi_viewer','db')),false);assert.ok([...dbSchemaCache.keys()].some(key=>JSON.parse(key)[2]==='admin'));
''')

    def test_breadcrumb_keeps_nodes_and_focus_through_periodic_refresh(self):
        self.javascript(r'''
await dualBoot();await exploreFinance();await objectButtons()[0].onclick();await tick();
const crumb=$('database-breadcrumb'),schemaCrumb=$('explorer-schema-details'),before=[...crumb.children];
assert.ok(before.includes(schemaCrumb));assert.equal(schemaCrumb.textContent,'Finance marts');schemaCrumb.focus();
// The 15-second access check re-renders controls; an unchanged path keeps the same nodes and focus.
await dbRefreshAccess();await dbRefreshContext();dbControls();dbExplorerControls();await tick();
assert.deepEqual([...crumb.children],before);assert.equal(document.activeElement,schemaCrumb);
// A real change (here the language) rebuilds and returns focus to the crumb with the same key.
const dbCrumb=crumb.children.find(node=>node.dataset.crumbKey==='database:finance');dbCrumb.focus();
language='ja';dbExplorerControls();const rebuilt=crumb.children.find(node=>node.dataset.crumbKey==='database:finance');
assert.notEqual(rebuilt,dbCrumb);assert.equal(document.activeElement,rebuilt);
// A kept database crumb acts on the current inventory entry, not the one captured when it was drawn.
inventory[1]={...inventory[1],revision:2};await dbRefreshAccess();await tick();
const current=crumb.children.find(node=>node.dataset.crumbKey==='database:finance');await current.onclick();await tick();
assert.equal(dbExplorer.database_id,'finance');assert.equal(dbExplorer.schema_id,'');assert.equal(dbDatabases.find(d=>d.id==='finance').revision,2);
// Focus is not returned to a crumb that disappeared with the selection.
schemaCrumb.focus();language='en';dbExplorerControls();
assert.equal(schemaCrumb.hidden,true);assert.equal(crumb.children.some(node=>node.dataset.crumbKey?.startsWith('schema:')&&!node.hidden),false);
''')

    def test_periodic_access_checks_saved_role_without_a_run(self):
        self.javascript(r'''
let grant=7;inventoryRoute=async(path,options)=>({items:inventory.map(d=>({...d,grant_revision:options.headers['X-Shirokuma-Role']==='admin'?null:grant}))});
await boot([{...input(),selected_role:'bi_viewer'},input('b')]);serverHistory=[historyEntry()];await dbLoadHistory();assert.equal(dbHistory.length,1);
grant=8;await dbRefreshAccess();assert.equal(dbHistory.length,0);assert.equal(dbCurrent()._grantChanged,true);assert.equal(worksheets[1]._grantChanged,undefined);
assert.equal(dbRoleItems('admin')[0].grant_revision,null);assert.equal(dbRoleItems('bi_viewer')[0].grant_revision,8);assert.equal($('run-query').disabled,true);
''')

    def test_saved_role_ceiling_roundtrip_and_explicit_role_change(self):
        self.javascript(r'''
await boot([{...input(),selected_role:'data_engineer'},input('b')]);
const other=JSON.stringify(dbInput(worksheets[1]));
$('role-selector').value='bi_viewer';await $('role-selector').onchange();
assert.equal(dbCurrent().selected_role,'data_engineer');assert.equal($('run-query').disabled,true);assert.match(dbReason(),/role ceiling/);
const before=calls.filter(c=>c.options?.method==='POST').length;await dbRunQuery();assert.equal(calls.filter(c=>c.options?.method==='POST').length,before);
$('role-selector').value='admin';await $('role-selector').onchange();assert.equal(dbCurrent().selected_role,'data_engineer');assert.equal(dbReason(),'');
route=async()=>envelope(dbRuns.get('a'));await dbRunQuery();const run=dbRuns.get('a');
$('query-execution-role').value='bi_viewer';await $('query-execution-role').onchange();
assert.equal(currentRole,'admin');assert.equal(dbCurrent().selected_role,'bi_viewer');assert.equal(run.origin.role,'data_engineer');
assert.deepEqual(queryResult.rows,[[1]]);assert.match($('query-meta').textContent,/Current inputs differ/);
assert.equal(JSON.stringify(dbInput(worksheets[1])),other);assert.equal(JSON.parse(storage.get(dbWorksheetKey)).tabs[0].selected_role,'bi_viewer');
''')

    def test_schema_apply_preserves_inputs_origin_and_other_tabs(self):
        self.javascript(r'''
await dualBoot();addWorksheet('SELECT second');await dbRefreshContext();
$('worksheet-tabs').querySelectorAll('[role=tab]').find(n=>n.dataset.worksheet==='a').onclick();await dbRefreshContext();
route=async()=>envelope(dbRuns.get('a'));await dbRunQuery();const run=dbRuns.get('a'),before={...dbInput(dbCurrent())},other=JSON.stringify(dbInput(worksheets[1]));
dbCurrent().mode='sandbox-write';dbCurrent().max_rows=29;dbSave();const preserved={...dbInput(dbCurrent())};
await exploreFinance();$('explorer-schema-details').onclick();assert.equal(dbWorkspacePane,'details');
await $('use-explorer-schema').onclick();
assert.deepEqual(dbInput(dbCurrent()),{...preserved,database_id:'finance',schema_id:'finance-marts',binding_revision:2,generation_id:'finance-gen'});
assert.equal(dbRuns.get('a'),run);assert.equal(run.origin.context.database_id,before.database_id);assert.deepEqual(queryResult.rows,[[1]]);
assert.equal(JSON.stringify(dbInput(worksheets[1])),other);assert.equal(dbWorkspacePane,'details');assert.equal(calls.filter(c=>c.options?.method==='POST').length,1);
assert.equal($('run-query').disabled,true); // Existing write mode is preserved, even if the new schema only supports read-only.
''')

    def test_history_restore_saved_role_does_not_change_header_or_other_tab(self):
        self.javascript(r'''
await boot([{...input(),selected_role:'bi_viewer'},input('b')]);const other=JSON.stringify(dbInput(worksheets[1]));
serverHistory=[historyEntry()];await dbLoadHistory();$('query-history').value='past';await dbRestoreHistory();
assert.equal(currentRole,'admin');assert.equal($('role-selector').value,'admin');assert.equal(dbCurrent().selected_role,'bi_viewer');
assert.equal($('sql').value,'SELECT 99');assert.equal(dbCurrent().schema_id,'raw');assert.equal(dbCurrent().mode,'sandbox-write');
assert.equal(JSON.stringify(dbInput(worksheets[1])),other);assert.equal(calls.filter(c=>c.options?.method==='POST').length,0);
assert.equal(calls.find(c=>c.path==='/api/v1/queries/past').options.headers['X-Shirokuma-Role'],'bi_viewer');
''')

    def test_header_aba_purges_rows_and_late_reads_cannot_resurrect_them(self):
        self.javascript(r'''
await boot([{...input(),selected_role:'data_engineer'}]);route=async()=>envelope(dbRuns.get('a'));await dbRunQuery();const run=dbRuns.get('a');
dbDetails.set('sensitive',{rows:[['private']]});const late=deferred();inventoryRoute=()=>late.promise;
const reading=dbReadInventory('data_engineer');await tick();
currentRole='bi_viewer';dbApplyRole();assert.equal(run.rowsPurged,true);assert.deepEqual(run.rows,[]);assert.equal(dbDetails.size,0);
currentRole='admin';dbApplyRole();inventoryRoute=null;await dbRefreshExplorer();await dbRefreshContext();
late.resolve({items:structuredClone(inventory)});await assert.rejects(reading,error=>error.code==='AUTH_READ_STALE');
await dbRevalidateRun(run);dbRenderRun();assert.equal(queryResult,null);assert.deepEqual(run.rows,[]);assert.equal(run.authorized,false);assert.equal(dbReason(),'');
serverHistory=[historyEntry()];await dbLoadHistory();assert.equal(dbHistory.length,1);assert.equal(queryResult,null);
''')

    def test_unknown_identity_and_origin_cancel_survive_permission_cleanup(self):
        self.javascript(r'''
await boot([{...input(),selected_role:'data_engineer'}]);route=async()=>envelope(dbRuns.get('a'),{state:'UNKNOWN',next_uri:null});await dbRunQuery();const run=dbRuns.get('a'),origin=run.origin;
assert.equal(run.state,'UNKNOWN');assert.equal(run.settled,false);
$('role-selector').value='bi_viewer';await $('role-selector').onchange();dbShowWorkspace('details');dbShowWorkspace('worksheet');
assert.equal(dbRuns.get('a'),run);assert.equal(run.origin,origin);assert.equal(run.state,'UNKNOWN');assert.deepEqual(run.rows,[]);assert.equal($('cancel-query').disabled,false);
$('role-selector').value='admin';await $('role-selector').onchange();assert.equal($('run-query').disabled,true);assert.equal(run.state,'UNKNOWN');
route=async()=>({id:run.id,state:'CANCELED'});await dbCancelQuery();assert.equal(run.state,'CANCELED');
assert.equal(calls.filter(c=>c.path.endsWith('/cancel')).at(-1).options.headers['X-Shirokuma-Role'],'data_engineer');
''')

    def test_legacy_is_default_and_v2_storage_is_input_only(self):
        self.assertIn("if(session.data_mode==='database')", SCRIPT)
        self.assertIn("const worksheetKey='shirokuma.worksheets.v1'", SCRIPT)
        self.javascript(r'''
storage.set('shirokuma.worksheets.v1',JSON.stringify({tabs:[{id:'old',sql:'old private SQL'}]}));
await boot();assert.equal($('sql').value,'SELECT 1');
const v1=storage.get('shirokuma.worksheets.v1');
storage.set('shirokuma.query_history.v1','legacy history');
$('sql').value='SELECT 22';$('sql').dispatchEvent({type:'input'});$('query-mode').value='sandbox-write';$('query-mode').onchange();
dbRuns.set('a',{rows:[['never persist']],secret:'never persist'});dbPersist();
const saved=JSON.parse(storage.get(dbWorksheetKey));assert.equal(saved.tabs[0].sql,'SELECT 22');assert.equal(saved.tabs[0].mode,'sandbox-write');
assert.deepEqual(Object.keys(saved.tabs[0]).sort(),dbInputFields.slice().sort());assert.ok(!storage.get(dbWorksheetKey).includes('never persist'));
assert.equal(storage.get('shirokuma.worksheets.v1'),v1);assert.ok(writes.every(k=>k===dbWorksheetKey));
assert.equal(storage.get('shirokuma.query_history.v1'),'legacy history');
''')

    def test_per_tab_inputs_and_new_tab_inheritance_and_history(self):
        self.javascript(r'''
await boot([input(),{...input('b'),sql:'SELECT 2',schema_id:'raw',mode:'sandbox-write',max_rows:17}]);
const switchTo=id=>$('worksheet-tabs').querySelectorAll('[role=tab]').find(n=>n.dataset.worksheet===id).onclick();
$('sql').value='SELECT 11';dbSave();switchTo('b');await tick();
assert.equal($('sql').value,'SELECT 2');assert.equal($('query-schema').value,'raw');assert.equal($('query-mode').value,'sandbox-write');assert.equal($('max-rows').value,17);
switchTo('a');await tick();assert.equal($('sql').value,'SELECT 11');assert.equal($('query-schema').value,'schema');
addWorksheet('SELECT 3');await tick();assert.equal(dbCurrent().database_id,'db');assert.equal(dbCurrent().schema_id,'schema');assert.equal(dbCurrent().mode,'read-only');
const before=calls.filter(c=>c.options?.method==='POST').length;
serverHistory=[historyEntry()];await dbLoadHistory();$('query-history').value='past';await dbRestoreHistory();
assert.equal(dbCurrent().schema_id,'raw');assert.equal(dbCurrent().mode,'sandbox-write');assert.equal(currentRole,'admin');assert.equal($('sql').value,'SELECT 99');
assert.equal(calls.filter(c=>c.options?.method==='POST').length,before);
''')

    def test_role_preserves_unavailable_values_until_explicit_selection(self):
        self.javascript(r'''
await boot([{...input(),schema_id:'raw',mode:'sandbox-write'}]);
schemas=schemas.filter(s=>s.id==='schema').map(s=>({...s,allowed_modes:['read-only']}));
currentRole='bi_viewer';dbApplyRole();await dbRefreshContext();
assert.equal(dbCurrent().schema_id,'raw');assert.equal(dbCurrent().mode,'sandbox-write');assert.equal($('sql').value,'SELECT 1');
assert.equal($('query-schema').value,'raw');assert.equal($('query-schema').options.find(o=>o.value==='raw').disabled,true);assert.equal($('run-query').disabled,true);
assert.equal(dbCurrent().selected_role,'admin');
$('query-execution-role').value='bi_viewer';await $('query-execution-role').onchange();
$('query-schema').value='schema';await $('query-schema').onchange();
assert.equal($('query-mode').value,'sandbox-write');assert.equal($('query-mode').disabled,false);assert.equal($('run-query').disabled,true);
$('query-mode').value='read-only';$('query-mode').onchange();assert.equal(dbReason(),'');assert.equal($('run-query').disabled,false);
inventory=[];await dbRefreshContext();assert.equal($('query-database').value,'db');assert.equal($('query-database').options.find(o=>o.value==='db').disabled,true);
''')

    def test_delayed_query_uses_origin_and_ignores_deleted_tab(self):
        self.javascript(r'''
await boot([input(),{...input('b'),sql:'SELECT 2'}]);const submit=deferred();route=async()=>submit.promise;
const running=dbRunQuery();await tick();const origin=dbRuns.get('a');
const post=calls.find(c=>c.options?.method==='POST');const body=JSON.parse(post.options.body);assert.equal(body.sql,'SELECT 1');assert.equal(body.worksheet_id,'a');assert.equal(post.options.headers['X-Shirokuma-Role'],'admin');
$('worksheet-tabs').querySelectorAll('[role=tab]').find(n=>n.dataset.worksheet==='b').onclick();await tick();
submit.resolve(envelope(origin));await running;
assert.deepEqual(dbRuns.get('a').rows,[[1]]);assert.equal(dbRuns.has('b'),false);assert.equal(queryResult,null);
assert.equal($('sql').value,'SELECT 2');assert.equal(dbCurrent().schema_id,'schema');
const next=deferred();route=async()=>next.promise;const runningB=dbRunQuery();await tick();const removed=dbRuns.get('b');closeWorksheet('b');await tick();
next.resolve(envelope(removed));await runningB;assert.equal(dbLive(removed),false);assert.deepEqual(removed.rows,[]);assert.equal(worksheetId,'a');
''')

    def test_polling_aggregation_and_cancel_capture_without_replay(self):
        self.javascript(r'''
await boot();const second=deferred();
route=async(path,options)=>{const run=dbRuns.get('a');if(path==='/api/v1/queries')return envelope(run,{state:'RUNNING',next_uri:'/api/v1/queries/query-uuid/pages/token'});if(path.endsWith('/pages/token'))return second.promise;if(path.endsWith('/cancel'))return {id:'query-uuid',state:'CANCELED'};throw Error(path);};
const running=dbRunQuery();await tick();const run=dbRuns.get('a');assert.equal(run.pending,true);assert.deepEqual(run.rows,[[1]]);
dbCurrent().schema_id='raw';currentRole='bi_viewer';dbApplyRole();await dbRefreshContext();
await dbCancelQuery();const cancel=calls.find(c=>c.path.endsWith('/cancel'));assert.equal(cancel.options.headers['X-Shirokuma-Role'],'admin');assert.equal(cancel.options.body,'{}');assert.equal(run.state,'CANCELED');
second.resolve(envelope(run,{data:[[2]]}));await running;assert.deepEqual(run.rows,[]);assert.equal(run.state,'CANCELED');
assert.equal(calls.filter(c=>c.path==='/api/v1/queries'&&c.options?.method==='POST').length,1);assert.equal($('run-query').disabled,true);
currentRole='admin';dbApplyRole();await dbRefreshContext();
assert.equal(dbReason(),'');
route=async(path)=>envelope(dbRuns.get('a'),path.includes('/pages/')?{data:[[3]],next_uri:null}:{state:'RUNNING',next_uri:'/api/v1/queries/query-uuid/pages/next'});
await dbRunQuery();assert.deepEqual(dbRuns.get('a').rows,[[1],[3]]);
for(const call of calls.filter(c=>c.path.startsWith('/api/v1/')))assert.deepEqual(Object.keys(call.options.headers).filter(k=>k.toLowerCase()==='x-shirokuma-role'),['X-Shirokuma-Role']);
''')

    def test_unknown_and_sql_denial_never_replay_or_revoke_database(self):
        self.javascript(r'''
await boot();for(const code of ['QUERY_CONTEXT_LOST','UNKNOWN_CONTEXT','SQL_PERMISSION_DENIED']){
 dbRuns.delete('a');
 route=async()=>({http:code==='SQL_PERMISSION_DENIED'?403:409,body:{code,message:'safe message'}});
 const before=calls.length;await dbRunQuery();const run=dbRuns.get('a');assert.match(run.error,new RegExp(code));
 assert.equal(calls.slice(before).filter(c=>c.path==='/api/v1/queries').length,1);assert.equal(dbDatabases[0].id,'db');assert.equal(dbReason(),'');
 assert.equal($('run-query').disabled,code==='QUERY_CONTEXT_LOST');
 if(code==='QUERY_CONTEXT_LOST'){assert.equal(run.state,'UNKNOWN');const n=calls.length;await dbRunQuery();assert.equal(calls.length,n);}
}
''')

    def test_binding_and_grant_invalidate_cache_without_adopting_revision(self):
        self.javascript(r'''
await boot();const object={id:'object',ref:{id:'object'},physical_name:'orders',display_name:'Orders',kind:'TABLE',capabilities:{columns:true}};
let detailCalls=0;route=async()=>{detailCalls++;return {columns:[{name:'n',type:'int',nullable:true}]};};
await dbDescribe(object);await dbDescribe(object);assert.equal(detailCalls,1);assert.equal(dbDetails.size,1);
inventory[0].grant_revision=5;await dbDescribe(object);assert.equal(dbDetails.size,0);assert.equal(dbCurrent()._grantChanged,true);assert.equal($('run-query').disabled,true);
await dbReselect();assert.equal(dbReason(),'');inventory[0].binding_revision=2;inventory[0].generation_id='gen2';await dbRefreshContext();
assert.equal(dbCurrent().binding_revision,1);assert.equal(dbCurrent().generation_id,'gen');assert.equal($('run-query').disabled,true);
await dbReselect();assert.equal(dbCurrent().binding_revision,2);assert.equal(dbCurrent().generation_id,'gen2');assert.equal(dbReason(),'');
await dbRefreshExplorer();await dbReselectExplorer();await dbDescribe(object);assert.equal(detailCalls,2);assert.ok(calls.filter(c=>c.path==='/api/v1/databases').length>=5);
''')

    def test_late_detail_auth_error_and_result_cannot_clear_new_context(self):
        self.javascript(r'''
await boot();const delayed=deferred(),object={id:'old',ref:{id:'old'},display_name:'Old',capabilities:{columns:true}};
route=async()=>delayed.promise;const old=dbDescribe(object);await tick();
currentRole='bi_viewer';dbApplyRole();await dbRefreshExplorer();await dbRefreshContext();
delayed.resolve({http:403,body:{code:'DB_ACCESS_REVOKED'}});await old;assert.equal(dbDatabases[0].id,'db');assert.equal($('catalog-error').textContent,'');assert.equal(dbDetails.size,0);
const later=deferred();route=async()=>later.promise;const detail=dbDescribe(object);await tick();dbCurrent().schema_id='raw';dbClearCatalog();await dbRefreshContext();
later.resolve({columns:[{name:'stale',type:'int'}]});await detail;assert.equal(dbDetails.size,0);assert.equal($('table-columns').rows,undefined);
''')

    def test_revoked_results_hidden_and_cancel_remains_available(self):
        self.javascript(r'''
await boot();const page=deferred();route=async path=>path==='/api/v1/queries'?envelope(dbRuns.get('a'),{state:'RUNNING',next_uri:'/api/v1/queries/query-uuid/pages/pending'}):path.endsWith('/cancel')?{id:'query-uuid',state:'CANCELED'}:page.promise;
const running=dbRunQuery();await tick();assert.deepEqual(queryResult.rows,[[1]]);
inventory=[];await dbRefreshContext();assert.equal(queryResult,null);assert.equal($('cancel-query').disabled,false);assert.equal($('run-query').disabled,true);
page.resolve({http:403,body:{code:'DB_ACCESS_REVOKED'}});await running;
assert.equal(dbRuns.get('a').pending,false);assert.equal(dbRuns.get('a').state,'UNKNOWN');assert.equal($('cancel-query').disabled,false);
await dbCancelQuery();assert.equal(dbRuns.get('a').state,'CANCELED');
assert.equal(queryResult,null);assert.equal($('run-query').disabled,true);assert.equal(calls.filter(c=>c.path==='/api/v1/queries').length,1);
''')

    def test_detail_query_pages_and_unresolved_views_and_disabled_actions(self):
        self.javascript(r'''
for(const panel of ['ingestion','transformation','jobs','bears','compute','pawprints'])$(panel).append(new Node('button'),new Node('input'));
await boot();for(const panel of ['ingestion','transformation','jobs','bears','compute','pawprints'])assert.ok($(panel).querySelectorAll('button,input,select,textarea').filter(n=>!['p35-ingest','p35-wap','db-jobs-panel'].some(id=>$(id).descendants().includes(n))&&n.id!=='ingestion-view-file').every(n=>n.disabled));
for(const id of ['p35-ingest-role','p35-ingest-database','p35-ingest-schema','p35-wap-role','p35-wap-database','p35-wap-schema'])assert.equal($(id).disabled,false,id);
for(const id of ['copilot-generate','copilot-stage','copilot-publish','ai-settings-button','export-csv','export-json','copy-tsv','export-table-btn'])assert.equal($(id).disabled,true,id);
await assert.rejects(api('/api/jobs/run',{}),/Unavailable/);const n=calls.length;download('csv');await $('copy-tsv').onclick();assert.equal(calls.length,n);
const view={id:null,ref:null,display_name:'Unresolved',capabilities:{columns:false}};await dbDescribe(view);assert.equal(calls.length,n);assert.match($('table-columns').textContent,/Unavailable/);
const context=dbContext(dbCurrent()),first={id:'detail',request_id:'detail-request',worksheet_id:'inventory:object:preview',context,columns:[{name:'n'}],data:[[7]],next_uri:'/api/v1/queries/detail/pages/next',state:'RUNNING'};
route=async path=>path.includes('/api/v1/objects/')?{query:first}:{...first,data:[[8]],next_uri:null,state:'FINISHED'};
await dbDescribe({id:'object',ref:{id:'object'},display_name:'Orders',capabilities:{preview:true}},'preview');
assert.deepEqual($('table-preview').rows,[[7],[8]]);assert.equal(calls.filter(c=>c.options?.method==='POST').length,0);
assert.equal($('export-table-btn').disabled,true);
''')

    def test_preflight_binding_change_blocks_post_and_keeps_run_disabled(self):
        self.javascript(r'''
await boot();inventory[0].binding_revision=2;inventory[0].generation_id='replacement';
await dbRunQuery();assert.equal(calls.filter(c=>c.options?.method==='POST').length,0);
assert.equal(dbCurrent().binding_revision,1);assert.equal($('run-query').disabled,true);
assert.match(dbRuns.get('a').error,/STALE_BINDING/);
''')

    def test_principal_change_hides_rows_and_unknown_fetch_can_be_canceled(self):
        self.javascript(r'''
await boot();
route=async path=>path==='/api/v1/queries'?envelope(dbRuns.get('a'),{state:'RUNNING',next_uri:'/api/v1/queries/query-uuid/pages/lost'}):path.endsWith('/cancel')?{id:'query-uuid',state:'CANCELED'}:{http:409,body:{code:'QUERY_CONTEXT_LOST'}};
await dbRunQuery();assert.equal(dbRuns.get('a').state,'UNKNOWN');assert.equal($('cancel-query').disabled,false);
await dbCancelQuery();assert.equal(dbRuns.get('a').state,'CANCELED');
route=async()=>envelope(dbRuns.get('a'));await dbRunQuery();assert.deepEqual(queryResult.rows,[[1]]);
session.username='another-owner';await dbRefreshContext();
assert.equal(queryResult,null);assert.equal(dbRuns.get('a').authorized,false);assert.equal($('run-query').disabled,true);assert.equal(dbDetails.size,0);
''')

    def test_canonical_default_and_arbitrary_allowed_canonical_modes(self):
        self.javascript(r'''
await initializeDatabaseUI(session);assert.equal(dbCurrent().mode,'read-only');
dbCurrent().database_id='db';dbCurrent().schema_id='schema';dbCurrent().binding_revision=1;dbCurrent().generation_id='gen';
schemas[0].allowed_modes=['read-only','admin-write'];await dbRefreshContext();
assert.ok($('query-mode').options.some(o=>o.value==='admin-write'));assert.ok(!$('query-mode').options.some(o=>['read','write'].includes(o.value)));
$('query-mode').value='admin-write';$('query-mode').onchange();
route=async()=>envelope(dbRuns.get(worksheetId));await dbRunQuery();
const post=calls.find(c=>c.path==='/api/v1/queries'&&c.options?.method==='POST');
assert.equal(JSON.parse(post.options.body).mode,'admin-write');
''')

    def test_server_history_stale_roles_databases_and_revocation(self):
        self.javascript(r'''
await boot();serverHistory=[historyEntry(),{...historyEntry({id:'missing-mode'}),mode:undefined},historyEntry({id:'bad-context',context:{database_id:'db'}})];
await dbLoadHistory();assert.deepEqual(dbHistory.map(q=>q.id),['past']);assert.equal($('query-history').disabled,false);
const delayed=deferred(),oldEntry=historyEntry({id:'old-role'});historyRoute=()=>delayed.promise;
const old=dbLoadHistory();assert.equal($('query-history').disabled,true);
currentRole='data_engineer';dbApplyRole();assert.equal($('query-history').disabled,true);
assert.equal(dbCurrent().selected_role,'admin');$('query-execution-role').value='data_engineer';await $('query-execution-role').onchange();
const fresh={...oldEntry,id:'fresh-role',context:{...oldEntry.context,selected_role:'data_engineer'}};
historyRoute=async()=>({queries:[fresh]});await dbRefreshContext();
delayed.resolve({queries:[oldEntry]});await old;assert.deepEqual(dbHistory.map(q=>q.id),['fresh-role']);
assert.equal(calls.filter(c=>c.path.startsWith('/api/v1/queries?')).at(-1).options.headers['X-Shirokuma-Role'],'data_engineer');
const late=deferred();historyRoute=()=>late.promise;const loading=dbLoadHistory();
dbCurrent().database_id='different';const change=dbRefreshContext();assert.equal($('query-history').disabled,true);
late.resolve({queries:[fresh]});await loading;await change;assert.deepEqual(dbHistory,[]);
dbCurrent().database_id='db';historyRoute=async()=>({queries:[fresh]});await dbRefreshContext();assert.equal($('query-history').disabled,false);
inventory=[];await dbRefreshContext();assert.deepEqual(dbHistory,[]);assert.equal($('query-history').disabled,true);
''')

    def test_history_inspect_restore_full_context_no_post_and_late_inspect(self):
        self.javascript(r'''
await boot();const saved=historyEntry();serverHistory=[saved];await dbLoadHistory();$('query-history').value='past';
await dbRestoreHistory();
assert.equal(dbCurrent().sql,saved.sql);assert.equal(dbCurrent().schema_id,'raw');assert.equal(dbCurrent().warehouse,'LOCAL');
assert.equal(dbCurrent().mode,'sandbox-write');assert.equal(dbCurrent().binding_revision,saved.context.binding_revision);assert.equal(dbCurrent().generation_id,saved.context.generation_id);
assert.deepEqual(dbRuns.get('a').origin.context,saved.context);assert.equal(calls.filter(c=>c.options?.method==='POST').length,0);
assert.equal(calls.find(c=>c.path==='/api/v1/queries/past').options.headers['X-Shirokuma-Role'],'admin');
const pending=deferred();inspectRoute=()=>pending.promise;await dbLoadHistory();$('query-history').value='past';
const restoring=dbRestoreHistory();await tick();$('sql').value='SELECT current draft';dbSave();inventory=[];await dbRefreshContext();
pending.resolve(saved);await restoring;assert.equal($('sql').value,'SELECT current draft');assert.equal($('query-history').disabled,true);
assert.equal(calls.filter(c=>c.options?.method==='POST').length,0);
''')

    def test_accepted_post_transport_parser_and_context_uncertainty_no_second_post(self):
        self.javascript(r'''
await boot();
for(const failure of ['network','parser','context']){
 dbRuns.delete('a');route=async()=>{
  if(failure==='network')throw Error('Connection lost after acceptance');
  if(failure==='parser')return {malformed:true};
  return envelope(dbRuns.get('a'),{context:{...dbRuns.get('a').origin.context,schema_id:'other'}});
 };
 const before=calls.filter(c=>c.options?.method==='POST').length;await dbRunQuery();
 const run=dbRuns.get('a');assert.equal(run.postStarted,true);assert.equal(run.state,'UNKNOWN');assert.equal(run.settled,false);
 assert.equal($('run-query').disabled,true);await dbRunQuery();assert.equal(calls.filter(c=>c.options?.method==='POST').length,before+1);
 if(failure==='context'){assert.equal(run.id,'query-uuid');assert.equal($('cancel-query').disabled,false);}
}
''')

    def test_late_old_query_revocation_does_not_clear_current_database_history(self):
        self.javascript(r'''
await boot();const page=deferred();
route=async path=>path==='/api/v1/queries'?envelope(dbRuns.get('a'),{state:'RUNNING',next_uri:'/api/v1/queries/query-uuid/pages/old'}):page.promise;
const running=dbRunQuery();await tick();const run=dbRuns.get('a');
inventory.push({...inventory[0],id:'other',sql_catalog:'other'});dbCurrent().database_id='other';await dbRefreshContext();
serverHistory=[historyEntry({id:'other-history'})];await dbLoadHistory();assert.equal($('query-history').disabled,false);
page.resolve({http:403,body:{code:'DB_ACCESS_REVOKED'}});await running;await tick();
assert.equal(run.state,'UNKNOWN');assert.ok(dbDatabases.some(d=>d.id==='other'));assert.deepEqual(dbHistory.map(q=>q.id),['other-history']);
assert.equal(dbCurrent().database_id,'other');assert.equal($('run-query').disabled,true);
''')

    def test_missing_cancel_ack_is_unknown_and_owner_can_cancel_after_downgrade(self):
        self.javascript(r'''
await boot();let ack={};
route=async path=>path==='/api/v1/queries'?envelope(dbRuns.get('a'),{state:'RUNNING',next_uri:'/api/v1/queries/query-uuid/pages/lost'}):path.endsWith('/cancel')?ack:{http:403,body:{code:'DB_ACCESS_REVOKED',message:'Grant revoked'}};
await dbRunQuery();const run=dbRuns.get('a');assert.equal(run.pending,false);assert.equal(run.state,'UNKNOWN');
session.maximum_role='bi_viewer';await dbRefreshContext();
assert.equal(currentRole,'admin');assert.equal(dbCurrent().mode,'read-only');assert.equal($('role-selector').options.find(o=>o.value==='admin').disabled,true);
assert.equal($('role-selector').options.find(o=>o.value==='bi_viewer').disabled,false);
assert.equal($('cancel-query').disabled,false);await dbCancelQuery();
assert.equal(run.state,'UNKNOWN');assert.equal(run.settled,false);assert.equal($('run-query').disabled,true);
const cancel=calls.find(c=>c.path.endsWith('/cancel'));assert.equal(cancel.options.headers['X-Shirokuma-Role'],'admin');
ack={id:run.id,state:'CANCELED'};await dbCancelQuery();assert.equal(run.state,'CANCELED');assert.equal(run.settled,true);
$('role-selector').value='bi_viewer';$('role-selector').onchange();await tick();assert.equal(currentRole,'bi_viewer');
''')

    def test_truncated_canceled_rows_and_incomplete_pages_remain_truthful(self):
        self.javascript(r'''
await boot();route=async path=>envelope(dbRuns.get('a'),path.includes('/pages/')?{data:[[2]],state:'CANCELED',truncated:false}:{state:'RUNNING',truncated:true,next_uri:'/api/v1/queries/query-uuid/pages/limit'});
await dbRunQuery();const run=dbRuns.get('a');assert.equal(run.state,'CANCELED');assert.equal(run.truncated,true);assert.deepEqual(queryResult.rows,[[1],[2]]);
assert.match($('query-meta').textContent,/CANCELED/);assert.match($('query-meta').textContent,/Row limit reached/);assert.match($('query-meta').textContent,/Partial results/);
assert.equal($('run-query').disabled,false);assert.ok(!storage.get(dbWorksheetKey).includes('"rows"'));
route=async()=>envelope(dbRuns.get('a'),{state:'RUNNING',next_uri:null});await dbRunQuery();
assert.equal(dbRuns.get('a').state,'UNKNOWN');assert.equal($('run-query').disabled,true);assert.deepEqual(queryResult.rows,[[1]]);
''')

    def test_unknown_detail_not_cached_and_language_hook_preserves_inputs_focus(self):
        self.javascript(r'''
await boot();serverHistory=[historyEntry()];await dbLoadHistory();
const first={id:'detail',request_id:'detail-request',worksheet_id:'inventory:object:preview',context:dbContext(dbCurrent()),columns:[{name:'n'}],data:[[7]],state:'RUNNING',next_uri:null};
route=async()=>({query:first});
await dbDescribe({id:'object',ref:{id:'object'},display_name:'Orders',capabilities:{preview:true}},'preview');
assert.deepEqual($('table-preview').rows,[[7]]);assert.equal(dbDetails.size,0);assert.match($('table-preview').children.at(-1).textContent,/UNKNOWN/);
route=async()=>envelope(dbRuns.get('a'),{state:'CANCELED',truncated:true});await dbRunQuery();
await dbLoadHistory();const inputs=JSON.stringify(dbInput(dbCurrent())),posts=calls.filter(c=>c.options?.method==='POST').length;
$('sql').focus();setLanguage('ja');
assert.equal(document.activeElement,$('sql'));assert.equal(JSON.stringify(dbInput(dbCurrent())),inputs);
assert.match($('query-meta').textContent,/取消済み/);assert.match($('query-meta').textContent,/行数の上限/);assert.match($('query-history').title,/サーバー/);
assert.match($('query-schema').options[0].textContent,/スキーマ/);
setLanguage('en');assert.equal(document.activeElement,$('sql'));assert.equal(JSON.stringify(dbInput(dbCurrent())),inputs);assert.match($('query-meta').textContent,/CANCELED/);
assert.equal(calls.filter(c=>c.options?.method==='POST').length,posts);
''')

    def test_history_denial_immediately_hides_rows_catalog_and_retains_inputs(self):
        self.javascript(r'''
await boot();
for(const code of ['DB_ACCESS_REVOKED','AUTHENTICATION_REQUIRED','STALE_BINDING']){
 historyRoute=null;await dbRefreshContext();await dbRefreshExplorer();await dbReselectExplorer();route=async()=>envelope(dbRuns.get('a'));await dbRunQuery();
 serverHistory=[historyEntry()];await dbLoadHistory();
 objects=[{id:null,ref:null,display_name:'Visible object',physical_name:'visible',kind:'VIEW',capabilities:{}}];await dbLoadObjects();
 dbDetails.set('cached',{columns:['private']});dbPending.set('pending',Promise.resolve({}));
 const inputs=JSON.stringify(dbInput(dbCurrent())),sql=$('sql').value;
 assert.deepEqual(queryResult.rows,[[1]]);assert.ok($('catalog-tree').children.length);
 historyRoute=async()=>({http:403,body:{code}});await dbLoadHistory();
 assert.equal(queryResult,null);assert.equal(dbRuns.get('a').authorized,false);
 for(const id of ['catalog-tree','worksheet-tree','table-columns','query-chart'])assert.equal($(id).children.length,0);
 assert.equal($('query-results').rows,undefined);assert.equal(dbDetails.size,0);assert.equal(dbPending.size,0);assert.deepEqual(dbObjects,[]);assert.equal(dbObject,null);
 assert.deepEqual(dbHistory,[]);assert.equal($('query-history').disabled,true);assert.equal(dbDatabases.length,0);assert.equal($('run-query').disabled,true);
 assert.equal(JSON.stringify(dbInput(dbCurrent())),inputs);assert.equal($('sql').value,sql);
 assert.match($('database-context-state').textContent,new RegExp(code));
}
historyRoute=null;await dbRefreshContext();route=async()=>envelope(dbRuns.get('a'));await dbRunQuery();
historyRoute=async()=>({http:403,body:{code:'SQL_PERMISSION_DENIED'}});await dbLoadHistory();
assert.deepEqual(queryResult.rows,[[1]]);assert.equal(dbDatabases[0].id,'db');assert.equal($('run-query').disabled,false);
historyRoute=async()=>({http:403,body:{code:'DB_ACCESS_REVOKED'}});await dbRefreshContext();
assert.equal(queryResult,null);assert.equal(dbRuns.get('a').authorized,false);assert.equal($('run-query').disabled,true);
''')

    def test_history_auth_failure_during_refresh_leaves_explicit_unavailable_state(self):
        self.javascript(r'''
await boot([input(),input('b')]);route=async()=>envelope(dbRuns.get('a'));await dbRunQuery();
const inputs=JSON.stringify(worksheets.map(dbInput)),sql=worksheets.map(w=>w.sql);assert.deepEqual(queryResult.rows,[[1]]);
historyRoute=async()=>({http:403,body:{code:'DB_ACCESS_REVOKED'}});await dbRefreshContext();
// A periodic controls render must not turn an aborted refresh into an endless spinner.
dbControls();assert.notEqual(dbReason(),t('Checking context…'));assert.match(dbReason(),/DB_ACCESS_REVOKED/);
assert.match($('database-context-state').textContent,/unavailable/);assert.equal($('run-query').disabled,true);
assert.equal(queryResult,null);assert.equal(dbRuns.get('a').authorized,false);assert.deepEqual(dbHistory,[]);assert.equal(dbSchemaScope,'');
assert.equal(JSON.stringify(worksheets.map(dbInput)),inputs);assert.deepEqual(worksheets.map(w=>w.sql),sql);
// Recovery uses the existing explicit Refresh control and clears no saved tab inputs.
historyRoute=null;await dbRefreshContext();assert.equal(dbReason(),'');assert.equal($('run-query').disabled,false);
assert.equal(JSON.stringify(worksheets.map(dbInput)),inputs);assert.deepEqual(worksheets.map(w=>w.sql),sql);
''')

    def test_object_list_denial_immediately_hides_rows_catalog_and_retains_inputs(self):
        self.javascript(r'''
await boot();
for(const code of ['DB_ACCESS_REVOKED','AUTHENTICATION_REQUIRED','STALE_CONTEXT']){
 objectsRoute=null;await dbRefreshContext();await dbRefreshExplorer();await dbReselectExplorer();route=async()=>envelope(dbRuns.get('a'));await dbRunQuery();
 objects=[{id:null,ref:null,display_name:'Visible object',physical_name:'visible',kind:'VIEW',capabilities:{}}];await dbLoadObjects();
 serverHistory=[historyEntry()];await dbLoadHistory();dbDetails.set('cached',{});dbPending.set('pending',Promise.resolve({}));
 const inputs=JSON.stringify(dbInput(dbCurrent())),sql=$('sql').value;
 objectsRoute=async()=>({http:403,body:{code}});await dbLoadObjects();
 assert.equal(queryResult,null);assert.equal($('query-results').rows,undefined);assert.equal(dbRuns.get('a').authorized,false);
 assert.equal($('catalog-tree').children.length,0);assert.equal($('worksheet-tree').children.length,0);assert.equal(dbDetails.size,0);assert.equal(dbPending.size,0);
 assert.deepEqual(dbObjects,[]);assert.deepEqual(dbHistory,[]);assert.equal($('query-history').disabled,true);assert.equal(dbDatabases.length,0);assert.equal($('run-query').disabled,true);
 assert.equal(JSON.stringify(dbInput(dbCurrent())),inputs);assert.equal($('sql').value,sql);assert.match($('catalog-error').textContent,new RegExp(code));
}
objectsRoute=null;await dbRefreshContext();await dbRefreshExplorer();await dbReselectExplorer();route=async()=>envelope(dbRuns.get('a'));await dbRunQuery();await dbLoadObjects();
objectsRoute=async()=>({http:403,body:{code:'SQL_PERMISSION_DENIED'}});await dbLoadObjects();
assert.deepEqual(queryResult.rows,[[1]]);assert.equal(dbDatabases[0].id,'db');assert.ok($('catalog-tree').children.length);assert.equal($('run-query').disabled,false);
''')

    def test_late_old_history_and_object_denials_leave_new_context_visible(self):
        self.javascript(r'''
await boot();const history=deferred(),list=deferred();
historyRoute=()=>history.promise;objectsRoute=()=>list.promise;
const oldHistory=dbLoadHistory(),oldList=dbLoadObjects();
inventory.push({...inventory[0],id:'other',sql_catalog:'other'});dbCurrent().database_id='other';
historyRoute=null;objectsRoute=null;await dbRefreshContext();
route=async()=>envelope(dbRuns.get('a'));await dbRunQuery();
serverHistory=[historyEntry({id:'new-history'})];await dbLoadHistory();
objects=[{id:null,ref:null,display_name:'New object',physical_name:'new',kind:'VIEW',capabilities:{}}];await dbLoadObjects();
dbDetails.set('new-cache',{});const inputs=JSON.stringify(dbInput(dbCurrent()));
history.resolve({http:403,body:{code:'DB_ACCESS_REVOKED'}});list.resolve({http:401,body:{code:'AUTHENTICATION_REQUIRED'}});await oldHistory;await oldList;
assert.deepEqual(queryResult.rows,[[1]]);assert.equal(dbRuns.get('a').authorized,true);assert.equal(dbCurrent().database_id,'other');
assert.deepEqual(dbHistory.map(q=>q.id),['new-history']);assert.equal($('query-history').disabled,false);assert.equal(dbDetails.has('new-cache'),true);
assert.equal(objectButtons()[0].textContent,'New object · Display only');assert.equal(objectButtons()[0].dataset.kind,'VIEW');assert.equal(objectButtons()[0]['aria-label'],'New object · VIEW · Display only');assert.equal($('run-query').disabled,false);
assert.equal(JSON.stringify(dbInput(dbCurrent())),inputs);assert.equal($('catalog-error').textContent,'');
''')

    def test_common_explorer_filter_persists_between_details_and_worksheet(self):
        self.javascript(r'''
await boot();const inputs=JSON.stringify(dbInput(dbCurrent())),sql=$('sql').value;
$('catalog-filter').value='old catalog';await $('catalog-filter').oninput();
dbShowWorkspace('details');assert.equal($('catalog-filter').value,'old catalog');
dbShowWorkspace('worksheet');assert.equal($('catalog-filter').value,'old catalog');
$('catalog-filter').value='next catalog';await $('catalog-filter').oninput();
let request=calls.filter(c=>c.path.includes('/objects?')).at(-1);assert.equal(new URLSearchParams(request.path.split('?')[1]).get('q'),'next catalog');
assert.equal($('worksheet-explorer').descendants().includes($('worksheet-tree-filter')),false);
assert.equal($('worksheet-explorer').descendants().includes($('catalog-filter')),true);
assert.equal(JSON.stringify(dbInput(dbCurrent())),inputs);assert.equal($('sql').value,sql);assert.equal(calls.filter(c=>c.options?.method==='POST').length,0);
''')

    def test_database_header_and_fixed_samples_disabled_with_bilingual_reasons(self):
        buttons = re.findall(r'<button\b[^>]*(?:id="quick-(?:wake|chill)"|data-sample="[^"]+")[^>]*>', HTML)
        self.assertEqual(len(buttons), 5)
        self.assertTrue(all('disabled' not in button for button in buttons))
        self.javascript(r'''
const controls=[$('quick-wake'),$('quick-chill'),...sampleButtons];
assert.equal(sampleButtons.length,3);assert.ok(controls.every(node=>!node.disabled&&!node.title));
databaseMode=false;setLanguage('en');assert.ok(controls.every(node=>!node.disabled&&!node.title));
databaseMode=true;await boot();const inputs=JSON.stringify(dbInput(dbCurrent()));
assert.ok(controls.every(node=>node.disabled&&/unavailable in Database mode/.test(node.title)));
assert.match(sampleButtons[0].title,/Enter SQL for the selected database/);
setLanguage('ja');assert.ok(controls.every(node=>node.disabled&&/利用できません/.test(node.title)));
assert.match(sampleButtons[0].title,/SQL を入力/);
currentRole='data_engineer';dbApplyRole();await dbRefreshContext();setLanguage('en');
assert.ok(controls.every(node=>node.disabled&&/unavailable in Database mode/.test(node.title)));
assert.equal(JSON.stringify(dbInput(dbCurrent())),inputs);assert.equal($('sql').disabled,false);
assert.equal($('add-worksheet-tab').disabled,false);assert.equal($('query-schema').disabled,false);assert.equal($('query-mode').disabled,false);
assert.equal(calls.filter(c=>c.options?.method==='POST').length,0);
''')

    def test_catalog_selectors_are_independent_of_worksheet_and_tab_switches(self):
        self.javascript(r'''
await dualBoot();route=async path=>path.startsWith('/api/v1/objects/')?{columns:[{name:'finance_column',type:'int',nullable:true}]}:envelope(dbRuns.get('a'));
await dbRunQuery();serverHistory=[historyEntry()];await dbLoadHistory();
const original=JSON.stringify(dbInput(dbCurrent())),run=dbRuns.get('a'),history=dbHistory;
for(const id of ['catalog-database','catalog-schema']){assert.equal($(id).tagName,'SELECT');assert.ok($(id).parentElement.children.some(n=>n.tagName==='LABEL'&&n.htmlFor===id));}
$('catalog-database').value='finance';await $('catalog-database').onchange();
assert.equal($('catalog-schema').value,'');assert.equal(dbExplorer.schema_id,'');assert.equal($('open-table-sql').disabled,true);
$('catalog-schema').value='finance-marts';await $('catalog-schema').onchange();
await objectButtons()[0].onclick();
assert.deepEqual($('table-columns').rows,[['finance_column','int',true]]);assert.equal(dbObject.id,'finance-object');assert.equal(dbDetails.size,1);
assert.equal(JSON.stringify(dbInput(dbCurrent())),original);assert.equal(dbRuns.get('a'),run);assert.deepEqual(queryResult.rows,[[1]]);assert.equal(dbHistory,history);
assert.equal(dbWorkspacePane,'details');assert.equal(dbObjects[0].id,'finance-object');assert.equal($('worksheet-tabs').parentElement.parentElement,main);
const selected=dbObject;addWorksheet('SELECT another draft');await tick();
assert.equal(dbExplorer.database_id,'finance');assert.equal(dbExplorer.schema_id,'finance-marts');assert.equal(dbObject,selected);assert.equal(dbDetails.size,1);
$('worksheet-tabs').querySelectorAll('[role=tab]').find(n=>n.dataset.worksheet==='a').onclick();await tick();
assert.equal(JSON.stringify(dbInput(dbCurrent())),original);assert.deepEqual(dbRuns.get('a').rows,[[1]]);assert.equal(dbObject,selected);
assert.equal(calls.filter(c=>c.options?.method==='POST').length,1);
''')

    def test_same_name_cache_and_late_explorer_response_denials_are_scoped(self):
        self.javascript(r'''
await dualBoot();const oldDetail=deferred(),oldList=deferred();
route=async path=>path==='/api/v1/queries'?envelope(dbRuns.get('a')):path.includes('detail=preview')?oldDetail.promise:{columns:[{name:path.includes('finance-object')?'finance_column':'sales_column',type:'int',nullable:true}]};
await dbRunQuery();serverHistory=[historyEntry()];await dbLoadHistory();const history=dbHistory,inputs=JSON.stringify(dbInput(dbCurrent()));
await dbDescribe(salesObject);const salesKey=[...dbDetails.keys()][0];assert.deepEqual($('table-columns').rows,[['sales_column','int',true]]);
const detail=dbDescribe(salesObject,'preview');await tick();
objectsRoute=async path=>path.includes('/finance-marts/')?{items:[financeObject],next_cursor:null}:oldList.promise;
const list=dbLoadObjects();await exploreFinance();await dbDescribe(financeObject,'columns');
const financeKey=[...dbDetails.keys()][0];assert.notEqual(financeKey,salesKey);assert.ok(financeKey.includes('finance-object'));assert.ok(financeKey.includes('finance-gen'));
assert.deepEqual($('table-columns').rows,[['finance_column','int',true]]);
oldDetail.resolve({http:403,body:{code:'DB_ACCESS_REVOKED'}});oldList.resolve({http:403,body:{code:'DB_ACCESS_REVOKED'}});await detail;await list;
assert.equal(dbObject.id,'finance-object');assert.deepEqual($('table-columns').rows,[['finance_column','int',true]]);assert.equal($('catalog-error').textContent,'');
assert.equal(JSON.stringify(dbInput(dbCurrent())),inputs);assert.equal(dbHistory,history);assert.deepEqual(queryResult.rows,[[1]]);
objectsRoute=async()=>({http:403,body:{code:'DB_ACCESS_REVOKED'}});await dbLoadObjects();
assert.equal(dbExplorer.database_id,'finance');assert.equal($('catalog-database').value,'finance');assert.equal($('catalog-database').options.find(o=>o.value==='finance').disabled,true);
assert.equal(dbObject,null);assert.equal(dbDetails.size,0);assert.equal(dbPending.size,0);assert.equal($('open-table-sql').disabled,true);
assert.deepEqual(queryResult.rows,[[1]]);assert.equal(dbHistory,history);assert.equal($('run-query').disabled,false);assert.equal(JSON.stringify(dbInput(dbCurrent())),inputs);
''')

    def test_explorer_binding_grant_change_requires_explicit_reselection(self):
        self.javascript(r'''
await dualBoot();route=async path=>path==='/api/v1/queries'?envelope(dbRuns.get('a')):{columns:[{name:'n',type:'int',nullable:false}]};
await dbRunQuery();serverHistory=[historyEntry()];await dbLoadHistory();await exploreFinance();await dbDescribe(financeObject);
const history=dbHistory,inputs=JSON.stringify(dbInput(dbCurrent()));
const finance=inventory.find(d=>d.id==='finance');finance.binding_revision=3;finance.generation_id='finance-gen-2';finance.grant_revision=10;
await dbRefreshExplorer();
assert.equal(dbExplorer.binding_revision,2);assert.equal(dbExplorer.generation_id,'finance-gen');assert.equal(dbExplorer.grant_revision,9);assert.ok(dbExplorerReason());
assert.equal($('catalog-database').value,'finance');assert.equal($('catalog-schema').value,'finance-marts');assert.equal(dbDetails.size,0);assert.equal($('open-table-sql').disabled,true);
assert.equal(JSON.stringify(dbInput(dbCurrent())),inputs);assert.equal(dbHistory,history);assert.deepEqual(queryResult.rows,[[1]]);
await $('catalog-context-reselect').onclick();
assert.equal(dbExplorer.binding_revision,3);assert.equal(dbExplorer.generation_id,'finance-gen-2');assert.equal(dbExplorer.grant_revision,10);assert.equal(dbExplorerReason(),'');
inventory=inventory.filter(d=>d.id!=='finance');await dbRefreshExplorer();
assert.equal($('catalog-database').value,'finance');assert.equal($('catalog-database').options.find(o=>o.value==='finance').disabled,true);
assert.equal($('catalog-schema').value,'finance-marts');assert.equal($('catalog-schema').options.find(o=>o.value==='finance-marts').disabled,true);
assert.equal(JSON.stringify(dbInput(dbCurrent())),inputs);assert.deepEqual(queryResult.rows,[[1]]);
''')

    def test_open_explorer_object_creates_new_bound_worksheet_and_preserves_origin(self):
        self.javascript(r'''
await dualBoot();route=async path=>path==='/api/v1/queries'?envelope(dbRuns.get('a')):{columns:[{name:'n',type:'int',nullable:true}]};
await dbRunQuery();await exploreFinance();await dbDescribe(financeObject);
dbCurrent().mode='admin-write';dbControls();assert.equal($('run-query').disabled,true);assert.equal($('open-table-sql').disabled,false);
const original=JSON.stringify(dbInput(dbCurrent())),run=dbRuns.get('a'),object=dbObject,posts=calls.filter(c=>c.options?.method==='POST').length;
await $('open-table-sql').onclick();await tick();
assert.equal(worksheets.length,2);assert.notEqual(worksheetId,'a');assert.equal(dbCurrent().database_id,'finance');assert.equal(dbCurrent().schema_id,'finance-marts');
assert.equal(dbCurrent().binding_revision,2);assert.equal(dbCurrent().generation_id,'finance-gen');assert.equal(dbCurrent().warehouse,'LOCAL');assert.equal(dbCurrent().mode,'read-only');
assert.equal($('sql').value,'SELECT * FROM "finance"."marts"."orders" LIMIT 50');
assert.equal(JSON.stringify(dbInput(worksheets.find(w=>w.id==='a'))),original);assert.equal(dbRuns.get('a'),run);assert.deepEqual(run.rows,[[1]]);assert.equal(dbObject,object);
assert.equal(calls.filter(c=>c.options?.method==='POST').length,posts);
await dbDescribe({id:null,ref:null,physical_name:'view',display_name:'Unresolved view',kind:'VIEW',capabilities:{resolve:false,columns:false}});
assert.equal($('open-table-sql').disabled,true);assert.ok($('open-table-sql').title);await $('open-table-sql').onclick();assert.equal(worksheets.length,2);
''')

    def test_shared_tree_details_and_explicit_insertion_use_explorer_context(self):
        self.javascript(r'''
await dualBoot();await exploreFinance();const inputs=JSON.stringify(dbInput(dbCurrent()));
$('catalog-filter').value='orders';await $('catalog-filter').oninput();
const request=calls.filter(c=>c.path.includes('/objects?')).at(-1);
assert.match(request.path,/schemas\/finance-marts\//);assert.equal(new URLSearchParams(request.path.split('?')[1]).get('q'),'orders');
assert.equal($('catalog-filter').value,'orders');assert.equal(JSON.stringify(dbInput(dbCurrent())),inputs);
const context={database_id:dbCurrent().database_id,schema_id:dbCurrent().schema_id};$('sql').selectionStart=$('sql').selectionEnd=7;$('sql').dispatchEvent({type:'blur'});
await objectButtons()[0].onclick();assert.equal(JSON.stringify(dbInput(dbCurrent())),inputs);assert.equal(dbWorkspacePane,'details');
$('insert-table-sql').onclick();assert.equal($('sql').value,'SELECT "finance"."marts"."orders"1');assert.equal(dbWorkspacePane,'details');assert.equal(calls.filter(c=>c.options?.method==='POST').length,0);
assert.equal(dbCurrent().database_id,context.database_id);assert.equal(dbCurrent().schema_id,context.schema_id);assert.equal(dbExplorer.database_id,'finance');
''')

    def test_database_unsupported_profile_lineage_are_disabled_and_explained(self):
        self.javascript(r'''
await dualBoot();const inputs=JSON.stringify(dbInput(dbCurrent())),before=calls.length;
const tab=section=>inspectorTabs.find(b=>b.dataset.inspect===section),key=key=>({key,preventDefault(){}});
tab('columns').onkeydown(key('ArrowRight'));assert.equal(activeInspector,'preview');
tab('preview').onkeydown(key('ArrowRight'));assert.equal(activeInspector,'ddl');assert.equal(document.activeElement,tab('ddl'));
tab('ddl').onkeydown(key('Home'));assert.equal(activeInspector,'columns');
for(const section of ['profile','lineage']){
 const button=inspectorTabs.find(b=>b.dataset.inspect===section);assert.equal(button.disabled,true);assert.match(button.title,/Unavailable/);
 await dbSelectInspector(button);assert.equal(activeInspector,'columns');assert.match($(inspectorTargets[section]).textContent,/Unavailable/);
}
assert.match($('lineage').textContent,/Unavailable/);assert.ok(!$('lineage').textContent.includes('Loading'));
setLanguage('ja');for(const section of ['profile','lineage'])assert.match(inspectorTabs.find(b=>b.dataset.inspect===section).title,/利用できません/);
assert.equal(JSON.stringify(dbInput(dbCurrent())),inputs);assert.equal(calls.length,before);
''')

    def test_original_run_denials_invalidate_old_allow_after_same_worksheet_db_switch(self):
        for catch_path in ['query', 'revalidate']:
            with self.subTest(catch_path=catch_path):
                self.javascript("const catchPath=" + repr(catch_path) + ";\n" + r'''
await dualBoot();const page=deferred(),oldAllow=deferred();
route=async path=>path==='/api/v1/queries'?envelope(dbRuns.get('a'),catchPath==='query'?{state:'RUNNING',next_uri:'/api/v1/queries/query-uuid/pages/next'}:{}):page.promise;
const running=dbRunQuery();await tick();if(catchPath==='revalidate')await running;
const run=dbRuns.get('a');assert.deepEqual(queryResult.rows,[[1]]);
Object.assign(dbCurrent(),{database_id:'finance',schema_id:'finance-marts',binding_revision:2,generation_id:'finance-gen'});
await dbRefreshContext();await exploreFinance();
serverHistory=[historyEntry({id:'finance-history'})];await dbLoadHistory();
dbDetails.set('finance-cache',{columns:[]});const draft=JSON.stringify(dbInput(dbCurrent())),allowed=structuredClone(inventory);
let hold=true;inventoryRoute=()=>hold?(hold=false,oldAllow.promise):Promise.resolve({items:structuredClone(inventory)});
const earlier=dbRevalidateRun(run);await tick();assert.equal(hold,false);
inventory=inventory.filter(d=>d.id!=='db');const version=dbAuthorizationVersion+dbRoleVersion('admin');
if(catchPath==='query'){
 page.resolve({http:403,body:{code:'DB_ACCESS_REVOKED'}});await running;await tick();
}else{
 inventoryRoute=async()=>({http:401,body:{code:'AUTHENTICATION_REQUIRED'}});
 await dbRevalidateRun(run);
 // The inventory read and its consumer catch must both invalidate receipts.
 assert.equal(dbAuthorizationVersion+dbRoleVersion('admin'),version+2);
}
assert.ok(dbAuthorizationVersion+dbRoleVersion('admin')>version);assert.equal(queryResult,null);assert.equal(run.authorized,false);
assert.ok(dbDatabases.some(d=>d.id==='finance'));assert.equal(dbDetails.has('finance-cache'),true);
assert.deepEqual(dbHistory.map(q=>q.id),['finance-history']);assert.equal($('query-history').disabled,false);
oldAllow.resolve({items:allowed});await earlier;
assert.equal(queryResult,null);assert.equal(run.authorized,false);assert.equal($('query-results').rows,undefined);
assert.equal(JSON.stringify(dbInput(dbCurrent())),draft);assert.equal(dbCurrent().database_id,'finance');
assert.deepEqual(dbHistory.map(q=>q.id),['finance-history']);assert.equal(dbDetails.has('finance-cache'),true);
assert.equal(calls.filter(c=>c.path==='/api/v1/queries'&&c.options?.method==='POST').length,1);
''')

    def test_pending_poll_old_inventory_allow_cannot_restore_revoked_rows(self):
        self.javascript(r'''
await boot();const page=deferred(),oldAllow=deferred(),allowed=structuredClone(inventory);
route=async path=>path==='/api/v1/queries'?envelope(dbRuns.get('a'),{state:'RUNNING',next_uri:'/api/v1/queries/query-uuid/pages/next'}):page.promise;
const running=dbRunQuery();await tick();const run=dbRuns.get('a');assert.deepEqual(queryResult.rows,[[1]]);
let hold=true;inventoryRoute=()=>hold?(hold=false,oldAllow.promise):Promise.resolve({items:structuredClone(inventory)});
page.resolve(envelope(run,{data:[[2]],state:'RUNNING',next_uri:'/api/v1/queries/query-uuid/pages/last'}));await tick();assert.equal(hold,false);
inventory=[];await dbRefreshAccess();assert.equal(queryResult,null);assert.equal(run.authorized,false);
oldAllow.resolve({items:allowed});await running;
assert.equal(queryResult,null);assert.equal(run.authorized,false);assert.equal($('query-results').rows,undefined);assert.equal($('run-query').disabled,true);
assert.equal(dbDatabases.length,0);assert.equal(calls.filter(c=>c.path==='/api/v1/queries'&&c.options?.method==='POST').length,1);
''')

    def test_history_inspect_denial_invalidates_pending_poll_allow_and_keeps_draft(self):
        self.javascript(r'''
await boot();const page=deferred(),oldAllow=deferred(),inspect=deferred(),allowed=structuredClone(inventory);
route=async path=>path==='/api/v1/queries'?envelope(dbRuns.get('a'),{state:'RUNNING',next_uri:'/api/v1/queries/query-uuid/pages/next'}):page.promise;
const running=dbRunQuery();await tick();const run=dbRuns.get('a');assert.deepEqual(queryResult.rows,[[1]]);
serverHistory=[historyEntry()];await dbLoadHistory();
let hold=true;inventoryRoute=()=>hold?(hold=false,oldAllow.promise):Promise.resolve({items:structuredClone(inventory)});
page.resolve(envelope(run,{data:[[2]],state:'RUNNING',next_uri:'/api/v1/queries/query-uuid/pages/last'}));await tick();assert.equal(hold,false);
inspectRoute=()=>inspect.promise;$('query-history').value='past';const restoring=dbRestoreHistory();await tick();
assert.ok(calls.some(c=>c.path==='/api/v1/queries/past'));assert.equal($('query-history').disabled,true);
$('sql').value='SELECT 43 AS current_draft';$('sql').dispatchEvent({type:'input'});
const draft=JSON.stringify(dbInput(dbCurrent())),version=dbAuthorizationVersion+dbRoleVersion('admin');
inventory=[];inspect.resolve({http:403,body:{code:'DB_ACCESS_REVOKED'}});await restoring;
assert.ok(dbAuthorizationVersion+dbRoleVersion('admin')>version);assert.equal(queryResult,null);assert.equal(run.authorized,false);
assert.equal($('query-history').disabled,true);assert.equal($('run-query').disabled,true);assert.equal(JSON.stringify(dbInput(dbCurrent())),draft);
oldAllow.resolve({items:allowed});await running;
assert.equal(queryResult,null);assert.equal(run.authorized,false);assert.equal($('query-results').rows,undefined);
assert.equal(dbDatabases.length,0);assert.equal($('query-history').disabled,true);assert.equal(JSON.stringify(dbInput(dbCurrent())),draft);
assert.equal($('sql').value,'SELECT 43 AS current_draft');assert.equal(dbCurrent().schema_id,'schema');
assert.equal(calls.filter(c=>c.path==='/api/v1/queries'&&c.options?.method==='POST').length,1);
''')

    def test_pending_poll_old_session_cannot_raise_downgraded_maximum_role(self):
        self.javascript(r'''
await boot();const page=deferred(),oldSession=deferred(),admin=structuredClone(session);
route=async path=>path==='/api/v1/queries'?envelope(dbRuns.get('a'),{state:'RUNNING',next_uri:'/api/v1/queries/query-uuid/pages/next'}):page.promise;
const running=dbRunQuery();await tick();const run=dbRuns.get('a');assert.deepEqual(queryResult.rows,[[1]]);
let hold=true;sessionRoute=()=>hold?(hold=false,oldSession.promise):Promise.resolve(structuredClone(session));
page.resolve(envelope(run,{data:[[2]],state:'RUNNING',next_uri:'/api/v1/queries/query-uuid/pages/last'}));await tick();assert.equal(hold,false);
session.maximum_role='bi_viewer';await dbRefreshAccess();assert.equal(dbMaximumRole,'bi_viewer');assert.equal(queryResult,null);
oldSession.resolve(admin);await running;
assert.equal(dbMaximumRole,'bi_viewer');assert.equal($('role-selector').options.find(o=>o.value==='admin').disabled,true);
assert.equal(currentRole,'admin');assert.equal(run.authorized,false);assert.equal(queryResult,null);assert.equal($('run-query').disabled,true);
''')

    def test_scoped_explorer_denial_invalidates_old_poll_read_without_starving_sales(self):
        self.javascript(r'''
await dualBoot();await exploreFinance();const page=deferred(),oldAllow=deferred(),allowed=structuredClone(inventory);
route=async path=>path==='/api/v1/queries'?envelope(dbRuns.get('a'),{state:'RUNNING',next_uri:'/api/v1/queries/query-uuid/pages/next'}):page.promise;
const running=dbRunQuery();await tick();const run=dbRuns.get('a');
let hold=true;inventoryRoute=()=>hold?(hold=false,oldAllow.promise):Promise.resolve({items:structuredClone(inventory)});
page.resolve(envelope(run,{data:[[2]],state:'FINISHED',next_uri:null}));await tick();assert.equal(hold,false);
inventory=inventory.filter(d=>d.id!=='finance');objectsRoute=async()=>({http:403,body:{code:'DB_ACCESS_REVOKED'}});
await dbLoadObjects();assert.equal(dbExplorer.invalid,true);assert.ok(!dbDatabases.some(d=>d.id==='finance'));
oldAllow.resolve({items:allowed});await running;
assert.equal(run.authorized,true);assert.equal(run.state,'FINISHED');assert.deepEqual(queryResult.rows,[[1],[2]]);
assert.equal(dbExplorer.invalid,true);assert.ok(!dbDatabases.some(d=>d.id==='finance'));
assert.equal(calls.filter(c=>c.path==='/api/v1/queries'&&c.options?.method==='POST').length,1);
''')

    def test_concurrent_valid_query_inventory_reads_can_complete_out_of_order(self):
        self.javascript(r'''
await boot([input(),{...input('b'),sql:'SELECT 2'}]);const pages={a:deferred(),b:deferred()};
route=async(path,options)=>{
 if(path==='/api/v1/queries'){const w=JSON.parse(options.body).worksheet_id,run=dbRuns.get(w);return envelope(run,{id:w,cancel_uri:'/api/v1/queries/'+w+'/cancel',state:'RUNNING',next_uri:'/api/v1/queries/'+w+'/pages/end'});}
 return pages[path.split('/')[4]].promise;
};
const first=dbRunQuery();await tick();
$('worksheet-tabs').querySelectorAll('[role=tab]').find(n=>n.dataset.worksheet==='b').onclick();await tick();
const second=dbRunQuery();await tick();let requests=[];inventoryRoute=()=>{const d=deferred();requests.push(d);return d.promise;};
for(const w of ['a','b'])pages[w].resolve(envelope(dbRuns.get(w),{id:w,cancel_uri:'/api/v1/queries/'+w+'/cancel',data:[[w]],state:'FINISHED',next_uri:null}));
await tick();assert.equal(requests.length,2);inventoryRoute=null;
requests[1].resolve({items:structuredClone(inventory)});await tick();requests[0].resolve({items:structuredClone(inventory)});
await first;await second;
for(const w of ['a','b']){assert.equal(dbRuns.get(w).authorized,true);assert.equal(dbRuns.get(w).state,'FINISHED');assert.deepEqual(dbRuns.get(w).rows,[[1],[w]]);}
assert.equal(calls.filter(c=>c.path==='/api/v1/queries'&&c.options?.method==='POST').length,2);
''')

    def test_history_inspect_preserves_same_context_edits_and_reenables_select(self):
        self.javascript(r'''
await boot();const saved=historyEntry();serverHistory=[saved];await dbLoadHistory();
for(const edit of ['sql','mode','both']){
 const held=deferred();inspectRoute=()=>held.promise;$('query-history').value='past';
 const restoring=dbRestoreHistory();await tick();assert.equal($('query-history').disabled,true);
 if(edit!=='mode'){$('sql').value='SELECT draft '+edit;$('sql').dispatchEvent({type:'input'});}
 if(edit!=='sql'){$('query-mode').value=dbCurrent().mode==='read-only'?'sandbox-write':'read-only';$('query-mode').onchange();}
 const draft=JSON.stringify(dbInput(dbCurrent())),sql=$('sql').value;
 held.resolve(saved);await restoring;
 assert.equal(JSON.stringify(dbInput(dbCurrent())),draft);assert.equal($('sql').value,sql);assert.equal(dbCurrent().schema_id,'schema');
 assert.equal($('query-history').disabled,false);assert.equal(calls.filter(c=>c.options?.method==='POST').length,0);
}
inspectRoute=null;$('query-history').value='past';await dbRestoreHistory();
assert.equal($('sql').value,saved.sql);assert.equal(dbCurrent().schema_id,'raw');assert.equal(dbCurrent().mode,saved.mode);
assert.equal($('query-history').disabled,false);assert.equal(calls.filter(c=>c.options?.method==='POST').length,0);
''')


if __name__ == '__main__':
    unittest.main()
