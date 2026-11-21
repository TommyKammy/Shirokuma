"""UX-R5: database management rebuilt from the Atlas mock-up (list, flow, retirement)."""
import re
import unittest

import test_workbench_database_ui as harness
from workbench_source import WorkbenchSource

HTML = harness.HTML
CSS = WorkbenchSource(HTML).css
COMPACT_CSS = re.sub(r'\s+', '', re.sub(r'/\*[\s\S]*?\*/', '', CSS))

PRELUDE = r'''
const rawDatabaseId='ffaf65d0-bbb0-4c1d-8e2f-123456789abc';
const rawSchemaId='43293940-aaaa-bbbb-cccc-dddddddddddd';
const managed=(id,name,state='READY',team=null,extra={})=>({id,sql_catalog:name,display_name:name==='sales'?'売上':name==='finance'?'財務':name,
 description:name==='sales'?'売上データ':name==='finance'?'財務データ':'',team,state,revision:1,
 schemas:['raw','staging','marts','sandbox'].map(sql_name=>({id:id+'-'+sql_name,sql_name,display_name:sql_name,purpose:sql_name,revision:1})),
 binding:{database_id:id,polaris_catalog:name,desired_revision:1,applied_revision:1,applied_generation_id:'gen-1',revision:1},...extra});
let adminItems=[managed(rawDatabaseId,'sales','READY','Data Team'),managed('finance-id','finance','RETIRING','Finance'),managed('retired-id','retired','RETIRED',null)];
session.platform_admin=true;
const retirePreview={database:{id:rawDatabaseId,sql_catalog:'sales',state:'READY',revision:1},
 dependencies:[{kind:'schedule',id:'schedule-1',state:'ENABLED',database_id:rawDatabaseId,target_kind:'job',target_id:'job-1'},
  {kind:'run',id:'run-1',state:'UNKNOWN',database_id:rawDatabaseId,parent_id:'job-1'}],
 blockers:[{kind:'run',id:'run-1',state:'UNKNOWN',database_id:rawDatabaseId,code:'RUN_UNKNOWN'}],
 stopped_schedule_ids:[],operation:null};
route=async(path,options)=>{
 if(path==='/api/v1/admin/databases')return {databases:structuredClone(adminItems)};
 if(path.endsWith('/grants')&&options?.method==='GET')return {grants:[]};
 if(path.endsWith('/retire')&&options?.method==='GET')return structuredClone(retirePreview);
 if(path.endsWith('/capabilities'))return {configured:true,environment_id:'env-1',creation_mode:'logical_v1',available:true,warehouse_state:'ACTIVE'};
 if(path==='/api/v1/admin/database-creations')return {creations:[]};
 if(path.includes('/api/v1/admin/database-creations/')){const creationId=decodeURIComponent(path.split('/').at(-1));return {creation_id:creationId,database_id:rawDatabaseId,revision:1,request_id:'request-1',accepted_revision:1,progress_code:'READY',lifecycle:'READY',creation_mode:'logical_v1',requester_id:'owner',initial_grants:[],plans:[],next_actions:[]};}
 return {};
};
const listRows=()=>$('db-admin-list-rows').children;
const linkText=id=>$('db-admin-link-'+id)?.textContent;
const rowFor=id=>listRows().find(row=>row.children[0].children[0]?.id==='db-admin-link-'+id);
const visibleText=node=>[node.textContent,...node.descendants().map(child=>child.textContent)].join(' ');
'''


class RebuildDatabasesUI(unittest.TestCase):
    javascript = harness.DatabaseUI.javascript

    def run_js(self, body):
        self.javascript(PRELUDE + body)

    def test_list_has_the_mock_structure_and_five_columns(self):
        self.run_js(r'''
await boot();await dbAdminOpen();setLanguage('ja');
const list=$('db-admin-list-view'),children=list.children;
assert.equal(list.hidden,false);assert.equal($('db-admin-detail-view').hidden,true);
assert.equal(children[0].className,'pageheading db-heading');
assert.equal(children[0].children[0].id,'db-admin-title');assert.equal(children[0].children[0].tagName,'H1');
assert.equal(children[0].children[0].textContent,'データベース管理');
assert.equal(children[0].children[1].className,'db-actions');
assert.equal($('db-admin-creation-entry').getAttribute('href'),'#databases/create');
assert.equal($('db-admin-creation-entry').className,'primary');
assert.equal(children[1].id,'db-admin-create-reason');assert.equal(children[1].hidden,true);
assert.equal(children[2].id,'db-request-history');assert.equal(children[2].tagName,'DETAILS');
assert.equal(children[2].children[0].textContent,'作成要求・サンプルを再表示');
const form=$('db-admin-list-filters');assert.equal(form.tagName,'FORM');assert.equal(form.getAttribute('role'),'search');
assert.equal(form.children[0].className,'db-search');assert.equal($('db-admin-list-query').parentElement.id,'db-admin-list-query-label');
assert.equal($('db-admin-filter-toggle').getAttribute('aria-controls'),'db-admin-filter-controls');
assert.equal($('db-admin-list-state').parentElement.tagName,'LABEL');
assert.equal($('db-admin-list-team').parentElement.tagName,'LABEL');
assert.equal($('db-admin-list-sort').parentElement.tagName,'LABEL');
assert.equal($('db-admin-list-pinned').type,'checkbox');
assert.equal($('db-admin-list-reset').type,'reset');
assert.equal($('db-admin-paging').children.filter(node=>node.tagName==='BUTTON').length,4);
for(const name of ['first','prev','next','last'])assert.ok($('db-admin-page-'+name));
const table=$('db-admin-list').children[0];assert.equal(table.tagName,'TABLE');assert.equal(table.className,'db-table');
assert.equal($('db-admin-list-view').children[4],$('db-admin-paging'));
const heads=table.children[1].children[0].children;
assert.deepEqual([...heads].map(node=>node.textContent),['DB名','状態','担当チーム','最終更新','ピン留め']);
assert.equal(heads[0].scope,'col');
assert.equal($('db-admin-list').getAttribute('role'),'region');
assert.equal($('db-admin-list').tabIndex,0);
assert.equal($('db-admin-list-empty').hidden,true);
const updated=rowFor(rawDatabaseId).children[3];
assert.equal(updated.textContent,'—');assert.equal(updated.children.length,0);
assert.equal(updated.title,'更新日時はサーバーから取得できません');
assert.equal(updated.getAttribute('aria-label'),'更新日時はサーバーから取得できません');
assert.equal(updated.descendants().some(node=>node.className?.includes('visually-hidden')||node.style?.position==='absolute'),false);
assert.equal($('db-admin-list-head-3').title,'更新日時はサーバーから取得できません');
const updatedNote=$('db-admin-list-updated-note');
assert.equal(updatedNote.hidden,false);
assert.equal(updatedNote.textContent,'最終更新: サーバーから取得できないため「—」と表示');
assert.equal(rowFor(rawDatabaseId).children[1].children[0].className,'status success');
assert.equal(rowFor('finance-id').children[1].children[0].className,'status warning');
assert.equal(rowFor('retired-id').children[1].children[0].className,'status');
assert.equal(rowFor('retired-id').children[0].children.length,1);
assert.equal(rowFor(rawDatabaseId).children[0].children[1].textContent,'sales · 売上データ');
assert.deepEqual($('db-admin-list-sort').options.map(option=>option.value),['name-asc','name-desc']);
assert.equal($('db-admin-list-sort').value,'name-asc');
assert.equal(listRows()[0].children[0].children[0].textContent,[...dbAdmin.databases].map(dbAdminName).sort((a,b)=>a.localeCompare(b,'ja'))[0]);
dbAdmin.databases.find(d=>d.id===rawDatabaseId).updated_at='2026-11-20T03:04:00Z';dbAdminRenderList();
const timed=rowFor(rawDatabaseId).children[3].children[0];
assert.equal(timed.tagName,'TIME');assert.equal(timed.getAttribute('datetime'),'2026-11-20T03:04:00Z');
assert.equal(timed.textContent,'2026-11-20 12:04 JST');
assert.equal($('db-admin-list-head-3').getAttribute('title'),undefined);
assert.equal($('db-admin-list-updated-note').hidden,true);
assert.equal(rowFor(rawDatabaseId).children[3].getAttribute('aria-label'),undefined);
''')

    def test_a_stale_creation_reference_never_replaces_the_list(self):
        self.run_js(r'''
await boot();await dbAdminOpen();
dbCreation.process={creation_id:'creation-1',database_id:rawDatabaseId,progress_code:'VERIFYING',lifecycle:'creating',creation_mode:'logical_v1'};
dbCreation.environment_id='env-1';
globalThis.location={href:'http://local/#databases?creation=creation-1&creation_environment=env-1'};
globalThis.history={replaceState:(_,__,url)=>{globalThis.location.href=String(url);}};
await dbCreationLoad();setLanguage('ja');
assert.equal(globalThis.location.href,'http://local/#databases?creation=creation-1&creation_environment=env-1');
dbAdminApplyRoute();
assert.equal($('db-admin-list-view').hidden,false);assert.equal($('db-admin-create-view').hidden,true);
assert.match(visibleText($('db-admin-list-view')),/DB名|データベース/);
$('db-admin-creation-entry').onclick();
assert.equal($('db-admin-create-view').hidden,false);assert.equal($('db-admin-list-view').hidden,true);
assert.equal(globalThis.location.href,'http://local/#databases/create');
dbCreation.process={creation_id:'done-1',database_id:rawDatabaseId,progress_code:'READY',lifecycle:'READY',creation_mode:'logical_v1'};
dbCreation.environment_id='env-1';dbCreation.storageKey=dbCreationStorageKey();dbCreationSave();
globalThis.location.href='http://local/#databases';
dbAdminApplyRoute();
assert.equal($('db-admin-list-view').hidden,false);assert.equal($('db-admin-create-view').hidden,true);
globalThis.location.href='http://local/#databases/'+rawDatabaseId;
dbAdminApplyRoute();
assert.equal($('db-admin-detail-view').hidden,false);assert.equal($('db-admin-create-view').hidden,true);
assert.equal(globalThis.location.href,'http://local/#databases/'+rawDatabaseId);
await dbCreationLoad();
assert.equal(globalThis.location.href,'http://local/#databases/'+rawDatabaseId);
assert.equal($('db-admin-detail-view').hidden,false);assert.equal($('db-admin-create-view').hidden,true);
dbCreationRememberURL('done-1');dbCreationSave();
assert.equal(globalThis.location.href,'http://local/#databases/'+rawDatabaseId);
for(const route of ['access','operation','metadata','retire']){
 globalThis.location.href='http://local/#databases/'+route+'~'+rawDatabaseId;
 dbCreationRememberURL('done-1');
 assert.equal(globalThis.location.href,'http://local/#databases/'+route+'~'+rawDatabaseId);
}
''')

    def test_filters_sort_pins_reset_and_count(self):
        self.run_js(r'''
await boot();await dbAdminOpen();setLanguage('ja');
const query=$('db-admin-list-query');query.value='finance';query.oninput();
assert.equal(listRows().length,1);assert.equal(linkText('finance-id'),'財務');
query.value='';query.oninput();
$('db-admin-list-team').value='__unset__';$('db-admin-list-team').onchange();
assert.equal(listRows().length,1);assert.equal(listRows()[0].children[2].textContent,'未設定');
$('db-admin-list-team').value='Data Team';$('db-admin-list-team').onchange();
assert.equal(listRows().length,1);assert.equal(linkText(rawDatabaseId),'売上');
$('db-admin-list-team').value='';$('db-admin-list-team').onchange();
$('db-admin-list-state').value='RETIRING';$('db-admin-list-state').onchange();
assert.equal(listRows().length,1);assert.equal(linkText('finance-id'),'財務');
assert.equal($('db-admin-filter-toggle').textContent,'絞り込み (1)');
$('db-admin-list-state').value='';$('db-admin-list-state').onchange();
$('db-admin-list-team').value='';$('db-admin-list-team').onchange();
$('db-admin-list-sort').value='name-asc';$('db-admin-list-sort').onchange();
const expectedFirst=[...dbAdmin.databases].map(dbAdminName).sort((a,b)=>a.localeCompare(b,'ja'))[0];
assert.equal(listRows()[0].children[0].children[0].textContent,expectedFirst);
const pin=$('db-admin-pin-'+rawDatabaseId);assert.equal(pin.getAttribute('aria-pressed'),'false');
assert.match(pin.getAttribute('aria-label'),/売上.*ピン留め/);
pin.onclick();
assert.equal($('db-admin-pin-'+rawDatabaseId).getAttribute('aria-pressed'),'true');
assert.equal(JSON.parse(storage.get(dbAdminPinStorageKey())).includes(rawDatabaseId),true);
dbAdmin.pinKey='';dbAdmin.pins.clear();dbAdminRenderList();
assert.equal($('db-admin-pin-'+rawDatabaseId).getAttribute('aria-pressed'),'true');
$('db-admin-list-pinned').checked=true;$('db-admin-list-pinned').onchange();
assert.equal(listRows().length,1);assert.equal(linkText(rawDatabaseId),'売上');
assert.equal($('db-admin-filter-toggle').textContent,'絞り込み (1)');
$('db-admin-list-filters').onreset({preventDefault(){}});
assert.equal($('db-admin-list-query').value,'');assert.equal($('db-admin-list-state').value,'');
assert.equal($('db-admin-list-team').value,'');assert.equal($('db-admin-list-sort').value,'name-asc');
assert.equal($('db-admin-list-pinned').checked,false);assert.equal(listRows().length,3);
assert.equal($('db-admin-range').textContent,'1–3 / 3件 · 1 / 1ページ');
assert.equal($('db-admin-filter-toggle').textContent,'絞り込み');
query.value='no-such-database';query.oninput();
assert.equal(listRows().length,0);assert.equal($('db-admin-list-empty').hidden,false);
assert.equal($('db-admin-list-empty').textContent,'一致するDBがありません。検索語や条件を変更してください。');
query.value='';query.oninput();
''')

    def test_paging_edges_and_range_stay_in_the_form_and_use_aria_disabled(self):
        self.run_js(r'''
adminItems=Array.from({length:30},(_,i)=>managed('id-'+i,'db'+String(i).padStart(2,'0'),'READY','Team'));
await boot();await dbAdminOpen();setLanguage('ja');
dbAdminSelect('');dbAdmin.listPage=1;dbAdminRenderList();
assert.equal($('db-admin-range').textContent,'1–25 / 30件 · 1 / 2ページ');
assert.equal($('db-admin-page-first').getAttribute('aria-disabled'),'true');
assert.equal($('db-admin-page-prev').getAttribute('aria-disabled'),'true');
assert.equal($('db-admin-page-next').getAttribute('aria-disabled'),'false');
assert.equal($('db-admin-page-last').getAttribute('aria-disabled'),'false');
$('db-admin-page-next').onclick();
assert.equal($('db-admin-range').textContent,'26–30 / 30件 · 2 / 2ページ');
assert.equal($('db-admin-page-first').getAttribute('aria-disabled'),'false');
assert.equal($('db-admin-page-prev').getAttribute('aria-disabled'),'false');
assert.equal($('db-admin-page-next').getAttribute('aria-disabled'),'true');
assert.equal($('db-admin-page-last').getAttribute('aria-disabled'),'true');
$('db-admin-page-first').onclick();
assert.equal($('db-admin-range').textContent,'1–25 / 30件 · 1 / 2ページ');
$('db-admin-page-last').onclick();
assert.equal($('db-admin-range').textContent,'26–30 / 30件 · 2 / 2ページ');
''')

    def test_typing_and_selecting_keep_the_same_control_and_value(self):
        self.run_js(r'''
await boot();await dbAdminOpen();setLanguage('ja');
const query=$('db-admin-list-query'),state=$('db-admin-list-state'),sort=$('db-admin-list-sort');
query.value='売';query.focus();query.oninput();
assert.equal($('db-admin-list-query'),query);assert.equal(query.value,'売');assert.equal(document.activeElement,query);
assert.equal(listRows().length,1);
state.focus();state.value='READY';state.onchange();
assert.equal($('db-admin-list-state'),state);assert.equal(state.value,'READY');assert.equal(document.activeElement,state);
sort.focus();sort.value='name-asc';sort.onchange();
assert.equal($('db-admin-list-sort'),sort);assert.equal(sort.value,'name-asc');assert.equal(document.activeElement,sort);
setLanguage('en');assert.equal(query.value,'売');assert.equal(state.value,'READY');assert.equal(sort.value,'name-asc');
assert.equal($('db-admin-list-query'),query);
''')

    def test_pin_toggle_renders_even_when_storage_fails(self):
        self.run_js(r'''
await boot();await dbAdminOpen();
const setItem=localStorage.setItem;localStorage.setItem=()=>{throw Error('QuotaExceededError');};
$('db-admin-pin-'+rawDatabaseId).onclick();
assert.equal($('db-admin-pin-'+rawDatabaseId).getAttribute('aria-pressed'),'true');
assert.equal(listRows().length,3);
localStorage.setItem=setItem;
''')

    def test_raw_ids_never_appear_in_list_or_detail(self):
        self.run_js(r'''
await boot();await dbAdminOpen();
const listText=visibleText($('db-admin-list-view'));
assert.equal(listText.includes(rawDatabaseId),false);
assert.equal(listText.includes(rawSchemaId),false);
dbAdminSelect(rawDatabaseId);await tick();
assert.equal($('db-admin-detail-view').hidden,false);
const detailText=visibleText($('db-admin-detail-view'));
assert.equal(detailText.includes(rawDatabaseId),false);
assert.equal(detailText.includes(rawSchemaId),false);
assert.equal($('db-admin-binding').textContent.includes(rawDatabaseId),false);
assert.equal($('db-admin-metadata').textContent.includes(rawSchemaId),false);
activeTab='db-admin';
globalThis.location={href:'http://local/#databases/retire~'+rawDatabaseId};
globalThis.history={replaceState:(_,__,url)=>{globalThis.location.href=String(url);}};
await dbAdminApplyRoute();await tick();await tick();await tick();
const retireText=visibleText($('db-admin-retire-view'));
assert.equal(retireText.includes(rawDatabaseId),false);
assert.equal(retireText.includes(rawSchemaId),false);
assert.equal($('db-admin-retire-stopped').textContent.includes('stopped-1'),false);
''')

    def test_detail_links_and_creation_and_retirement_entries(self):
        self.run_js(r'''
await boot();await dbAdminOpen();setLanguage('ja');
dbAdminSelect(rawDatabaseId);await tick();
assert.equal($('db-admin-back').getAttribute('href'),'#databases');
assert.equal($('db-admin-detail-title').textContent,'売上');
assert.equal($('db-admin-detail-state').children[0].textContent,'利用可能');
assert.equal($('db-admin-detail-state').children[0].className,'status success');
assert.equal($('db-admin-use-sql').hidden,false);
assert.equal($('db-admin-access-open').getAttribute('href'),'#databases/access~'+rawDatabaseId);
assert.equal($('db-admin-operation-open').getAttribute('href'),'#databases/operation~'+rawDatabaseId);
assert.equal($('db-admin-metadata-open').getAttribute('href'),'#databases/metadata~'+rawDatabaseId);
assert.equal($('db-admin-retire-open').getAttribute('href'),'#databases/retire~'+rawDatabaseId);
dbAdminStartCreate();await tick();
assert.equal($('db-admin-create-view').hidden,false);
assert.equal($('db-creation').className.includes('db-flow'),true);
assert.equal($('db-creation-list-back').getAttribute('href'),'#databases');
assert.equal($('db-creation-journey').children[0].className,'db-steps');
assert.equal($('db-creation-form-shell').children[0].tagName,'FIELDSET');
assert.ok($('db-creation-form'));
activeTab='db-admin';
globalThis.location={href:'http://local/#databases/retire~'+rawDatabaseId};
globalThis.history={replaceState:(_,__,url)=>{globalThis.location.href=String(url);}};
await dbAdminApplyRoute();await tick();await tick();await tick();
assert.equal($('db-admin-retire-view').hidden,false);assert.equal($('db-admin-list-view').hidden,true);
assert.equal($('db-admin-retire-heading').textContent,'利用終了の影響');
assert.equal($('db-admin-retire-steps').children[0].getAttribute('aria-current'),'step');
assert.equal($('db-admin-retire-back').getAttribute('href'),'#databases/'+rawDatabaseId);
assert.equal($('db-admin-retire-next').getAttribute('href'),'#databases/confirm~'+rawDatabaseId);
assert.match(visibleText($('db-admin-retire-blockers')),/実行結果が未確定|結果の確認/);
globalThis.location.href='http://local/#databases/confirm~'+rawDatabaseId;
dbAdminApplyRoute();await tick();
assert.equal($('db-admin-retire-heading').textContent,'利用終了の確認');
assert.equal($('db-admin-retire-confirm-panel').hidden,false);
assert.equal($('db-admin-retire-confirm').type,'checkbox');
assert.match($('db-admin-retire-save').className,/primary/);assert.match($('db-admin-retire-save').className,/danger/);
assert.equal($('db-admin-retire-save').textContent,'利用を終了する');
''')

    def test_detail_heading_groups_status_and_entry_links_are_text_links(self):
        self.assertIn('.db-title-line{display:flex;', CSS)
        self.assertIn('align-items:baseline', CSS)
        self.assertIn('flex-wrap:wrap', CSS)
        self.assertIn('.db-title-line .status{flex:none}', CSS)
        self.assertIn('.db-heading.db-actions{margin:0;margin-left:auto;flex:none}', COMPACT_CSS)
        self.assertIn('#db-admin-subnava{display:inline-flex;align-items:center;min-height:44px;padding:8px12px;color:var(--accent-ink);text-decoration:none;font-weight:550}', COMPACT_CSS)
        self.assertIn('#db-admin-subnava:hover{text-decoration:underline}', COMPACT_CSS)
        self.assertNotIn('#db-admin-subnav{border', COMPACT_CSS)
        self.run_js(r'''
await boot();await dbAdminOpen();setLanguage('ja');
dbAdminSelect(rawDatabaseId);await tick();
const head=$('db-admin-detail-title-line');
assert.equal(head.className,'db-title-line');
assert.equal(head.parentElement.className,'pageheading db-heading');
assert.equal(head.children[0],$('db-admin-detail-title'));
assert.equal(head.children[1],$('db-admin-detail-state'));
assert.equal($('db-admin-detail-state').children[0].className,'status success');
const pin=$('db-admin-detail-pin');
assert.equal(pin.parentElement,head.parentElement);
assert.equal(pin.children[0].className,'db-pin');
assert.equal(pin.children[0].id,'db-admin-detail-pin-'+rawDatabaseId);
const nav=$('db-admin-subnav');
assert.equal(nav.tagName,'NAV');assert.equal(nav.className,'db-actions');
for(const id of ['db-admin-access-open','db-admin-operation-open','db-admin-metadata-open']){
 const link=$(id);assert.equal(link.parentElement,nav);assert.equal(link.getAttribute('class'),undefined);assert.ok(link.textContent.length>0);
}
const actions=$('db-admin-detail-actions');
assert.equal(actions.children[0],$('db-admin-use-sql'));assert.equal($('db-admin-use-sql').className,'primary');
assert.equal(actions.children[1],$('db-admin-retire-open'));assert.equal($('db-admin-retire-open').getAttribute('class'),undefined);
''')

    def test_sidebar_same_tab_link_returns_from_detail_to_list(self):
        self.run_js(r'''
await boot();
globalThis.location={href:'http://local/#databases/'+rawDatabaseId,origin:'http://local'};
globalThis.history={pushState:(_,__,url)=>{globalThis.location.href=String(url);},replaceState:(_,__,url)=>{globalThis.location.href=String(url);}};
await activate($('tab-db-admin'));await tick();
assert.equal(activeTab,'db-admin');assert.equal($('db-admin-detail-view').hidden,false);
assert.equal(new URL(globalThis.location.href).hash,'#databases/'+rawDatabaseId);
const result=$('tab-db-admin').click();await result;await tick();await tick();
assert.equal(new URL(globalThis.location.href).hash,'#databases');
assert.equal(activeTab,'db-admin');
assert.equal($('db-admin-list-view').hidden,false);
assert.equal($('db-admin-detail-view').hidden,true);
assert.equal($('tab-db-admin').getAttribute('aria-current'),'page');
''')

    def test_japanese_and_english_copy_switch_without_losing_the_list(self):
        self.run_js(r'''
await boot();await dbAdminOpen();setLanguage('ja');
assert.equal($('db-admin-title').textContent,'データベース管理');
assert.equal($('db-admin-list-head-0').textContent,'DB名');
assert.equal($('db-admin-list-head-3').textContent,'最終更新');
assert.equal($('db-admin-list-head-4').textContent,'ピン留め');
assert.equal($('db-admin-filter-toggle').textContent,'絞り込み');
assert.equal($('db-admin-list-query').placeholder,'DB名・表示名・プロジェクト名で検索');
assert.equal($('db-request-history').children[0].textContent,'作成要求・サンプルを再表示');
setLanguage('en');
assert.equal($('db-admin-title').textContent,'Database Management');
assert.equal($('db-admin-list-head-0').textContent,'DB name');
assert.equal($('db-admin-list-head-3').textContent,'Last updated');
assert.equal($('db-admin-list-head-4').textContent,'Pin');
assert.equal($('db-admin-filter-toggle').textContent,'Database filters');
assert.equal($('db-admin-list-query').placeholder,'Search DB, display name or project');
assert.equal($('db-request-history').children[0].textContent,'Creation requests and samples');
assert.equal($('db-admin-list-view').hidden,false);
''')

    def test_strict_connected_document_boots_and_renders_the_list(self):
        self.run_js(r'''
await boot();
documentRoot.append($('db-admin'));
$('db-admin').append($('db-admin-error'),$('db-admin-content'));
await dbAdminOpen();
const ids=['db-admin-content','db-admin-list-view','db-admin-title','db-admin-creation-entry','db-admin-list-filters',
 'db-admin-list-query','db-admin-filter-toggle','db-admin-filter-controls','db-admin-list-state','db-admin-list-team',
 'db-admin-list-sort','db-admin-list-pinned','db-admin-list-reset','db-admin-paging','db-admin-range','db-admin-page-first',
 'db-admin-page-prev','db-admin-page-next','db-admin-page-last','db-admin-list','db-admin-list-rows','db-admin-list-empty',
 'db-admin-detail-view','db-admin-create-view','db-admin-retire-view','db-admin-detail-title','db-admin-back',
 'db-admin-access-open','db-admin-operation-open','db-admin-metadata-open','db-admin-retire-open','db-admin-management',
 'db-admin-select','db-admin-retire-preview','db-admin-retire-save','db-creation','db-creation-title','db-creation-form'];
for(const id of ids){const node=nodes[id];assert.ok(node,id);assert.equal(connected(node),true,id);}
connectedOnly=true;
dbAdminRenderList();dbAdminSelect(rawDatabaseId);dbAdminApplyRoute();
for(const id of ids){assert.ok(nodes[id],id);assert.equal(connected(nodes[id]),true,id+' strict');}
assert.equal($('db-admin-detail-title'),nodes['db-admin-detail-title']);
assert.equal($('db-admin-list-rows'),nodes['db-admin-list-rows']);
''')

    def test_mock_css_is_used_for_tables_filters_paging_and_flow(self):
        for expected in [
            '.db-filters{display:flex;flex-wrap:wrap;gap:12px;align-items:end;margin:20px0;}',
            '.db-filter-toggle{display:inline-flex',
            '.db-paging{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:14px0;}',
            '.db-table{table-layout:fixed;min-width:690px;}',
            '.db-pin[aria-pressed="true"]{background:var(--selected);}',
            '.db-flowfieldset{border:1pxsolidvar(--line);padding:14px;margin:0;}',
            '.db-steps{display:flex;flex-wrap:wrap;gap:8px24px;padding-left:24px;margin:18px0;}',
            '.status{display:inline-flex;align-items:center;gap:5px;font-size:10px;white-space:nowrap;}',
            '.status:before{content:"";width:5px;height:5px;border-radius:50%;background:currentColor;}',
            '.success{color:var(--green);}',
            '.warning{color:var(--amber);}',
        ]:
            self.assertIn(expected, COMPACT_CSS)
        self.assertIn('.tablewrap{position:relative;overflow:auto', COMPACT_CSS)


if __name__ == '__main__':
    unittest.main()
