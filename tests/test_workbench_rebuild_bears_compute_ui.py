"""UX-R7: AI Bears and Compute & Governance rebuilt from the Atlas mock-up."""
import re
import unittest

import test_workbench_bears_database_ui as bears_ui
import test_workbench_database_ui as harness
import test_workbench_warehouse_runtime_ui as runtime_ui
from test_workbench_ai_sql_proposals_database_ui import PROPOSAL
from workbench_source import WorkbenchSource

HTML = harness.HTML
CSS = WorkbenchSource(HTML).css
COMPACT_CSS = re.sub(r'\s+', '', re.sub(r'/\*[\s\S]*?\*/', '', CSS))

PATCH = r'''
const appendNodes=Node.prototype.append;Node.prototype.append=function(...items){return appendNodes.apply(this,items.map(n=>typeof n==='string'?Object.assign(new Node('text'),{textContent:n}):n));};
const atlasClass=node=>(node?.className||'').split(' ').filter(Boolean);
const atlasHas=(node,name)=>atlasClass(node).includes(name);
const atlasText=node=>[node.textContent,...(node.descendants?.()||[]).map(child=>child.textContent)].join(' ');
const atlasShow=id=>{documentRoot.append($(id));return $(id);};
const until=async check=>{for(let i=0;i<100;i++){if(check())return true;await tick();}throw Error('timeout');};
'''
BEARS = bears_ui.COMMON + PROPOSAL


class RebuildBearsComputeUI(unittest.TestCase):
    def bears(self, body):
        harness.DatabaseUI.javascript(self, PATCH + BEARS + body)

    def compute(self, body):
        harness.DatabaseUI.javascript(self, PATCH + runtime_ui.SETUP + '\n' + body)

    def test_bears_requests_objects_once_for_a_selected_context_on_first_display(self):
        self.bears(r"""
let objectReads=0;
objectsRoute=async path=>{if(path==='/api/v1/schemas/schema/objects?limit=100'){objectReads++;return {items:[proposalObject(0)],next_cursor:null};}return {items:[],next_cursor:null};};
await boot();atlasShow('bears');setLanguage('ja');await activate($('tab-bears'));
for(let i=0;i<50&&objectReads<1;i++)await tick();
assert.equal(objectReads,1);for(let i=0;i<20;i++)await tick();
assert.equal(objectReads,1);assert.equal(dbProposal.objects.length,1);
""")

    def test_bears_table_placeholder_follows_the_selected_context_and_load_state(self):
        self.bears(r"""
const empty='No eligible sources. Choose another query-enabled schema.',unavailable='Source objects unavailable. Refresh context and load the chosen schema again.';
const key=()=>JSON.stringify([dbPrincipal,dbCurrent().id,dbCurrent().database_id,dbCurrent().schema_id,dbCurrent().selected_role]);
await boot();atlasShow('bears');setLanguage('ja');workbenchAtlasBearsSync();
dbCurrent().database_id='';dbCurrent().schema_id='';dbCurrent().selected_role='';workbenchAtlasBears.database='';workbenchAtlasBears.schema='';dbProposal.fetchToken=null;dbProposal.objects=[];dbProposal.pickerNotice='';workbenchAtlasBears.loadKey=key();workbenchAtlasBearsSync();
assert.equal($('bears-table').children[0].textContent,t('Select a schema first.'));
dbCurrent().database_id='db';dbCurrent().schema_id='schema';dbCurrent().selected_role='admin';dbProposal.fetchToken=null;dbProposal.objects=[];dbProposal.pickerNotice='';workbenchAtlasBears.loadKey=key();workbenchAtlasBearsSync();
assert.equal($('bears-table').children[0].textContent,t('Select a source table'));
dbProposal.fetchToken={};workbenchAtlasBearsSync();
assert.equal($('bears-table').children[0].textContent,t('Preparing source objects…'));
dbProposal.fetchToken=null;dbProposal.pickerNotice=empty;workbenchAtlasBearsSync();
assert.equal($('bears-table').children[0].textContent,t(empty));
dbProposal.pickerNotice=unavailable;workbenchAtlasBearsSync();
assert.equal($('bears-table').children[0].textContent,t(unavailable));
dbProposal.pickerNotice='';dbProposal.objects=[proposalObject(0)];workbenchAtlasBearsSync();
assert.equal($('bears-table').children[0].textContent,t('Select a source table'));
assert.ok($('bears-table').options.some(o=>o.value==='object-0'));
""")

    def test_bears_message_is_empty_when_the_context_is_complete(self):
        self.bears(r"""
objects=[proposalObject(0)];objectsRoute=async()=>({items:structuredClone(objects),next_cursor:null});
await boot();atlasShow('bears');setLanguage('ja');await activate($('tab-bears'));
for(let i=0;i<50&&!dbProposal.objects.length;i++)await tick();
assert.equal(dbProposal.objects.length,1);assert.equal(dbReason(),'');
assert.equal($('bears-message').textContent,'');assert.equal($('bears-message').hidden,false);
""")
        self.assertIn('#bears-atlas#bears-message:empty{display:none', COMPACT_CSS)

    def test_bears_uses_the_mock_page_without_the_legacy_surface(self):
        self.bears(r'''
await boot();atlasShow('bears');setLanguage('ja');
const atlas=$('bears-atlas');
assert.equal(atlas.hidden,false);assert.equal(documentRoot.descendants().some(n=>/legacy/.test(n.id||'')),false);
assert.equal($('bears-pageheading').className,'pageheading');
assert.equal($('bears-heading').textContent,'AI Bears');assert.equal($('bears-heading').parentElement,$('bears-pageheading'));
assert.equal($('bears-environment').textContent,'LOCAL · ローカルラボ');
assert.equal($('bears').descendants().filter(n=>n.tagName==='H1').length,1);
assert.equal($('bears-form').parentElement,atlas);
assert.equal($('bears-context-fields').className,'atlas-fields');
assert.deepEqual([...$('bears-context-fields').children].map(n=>n.tagName),['LABEL','LABEL','LABEL']);
assert.equal($('bears-target-fields').className,'atlas-fields');
assert.equal($('bears-inherit').parentElement,$('bears-form'));
assert.equal($('bears-sql-field').className,'atlas-sql-field');assert.equal($('bears-sql-field').descendants().includes($('bear-query-sql')),true);
assert.equal($('bears-sql-field').descendants().includes($('explanation-doctor-context')),false);
assert.equal($('bears-explanation-tools').descendants().includes($('explanation-doctor-context')),true);
assert.equal($('bears-toolbar').descendants().includes($('bears-explain')),true);
assert.equal($('bears-toolbar').descendants().includes($('bears-transform')),true);
assert.equal($('bears-message').tagName,'P');assert.equal($('bears-message').role,'status');assert.equal($('bears-message').tabIndex,-1);
assert.deepEqual([...atlas.children].filter(n=>n.tagName==='SECTION').map(n=>n.id),['bears-explanation','bears-proposal','bears-curator','bears-policy']);
for(const id of ['bears-explanation','bears-proposal','bears-curator','bears-policy'])assert.equal($(id).className,'section');
assert.equal($('bears-explanation-title').tagName,'H2');assert.equal($('bears-proposal-title').tagName,'H2');
assert.equal($('bears-curator-facts').className,'kv');assert.equal($('bears-policy-facts').className,'kv');
assert.equal($('bears-curator-propose').parentElement,$('bears-curator-toolbar'));
assert.equal($('bears-compute-link').getAttribute('href'),'#compute');
assert.equal($('bears-evidence-details').tagName,'DETAILS');assert.equal($('bears-evidence-details').open,false);
assert.equal(atlas.descendants().some(n=>atlasHas(n,'bear-layout')||atlasHas(n,'bear-list')||atlasHas(n,'page-head')),false);
''')

    def test_compute_uses_short_facts_three_buttons_and_closed_details(self):
        self.compute(r'''
await start();$('tab-compute').click();atlasShow('compute');setLanguage('ja');
const atlas=$('compute-atlas');
assert.equal(atlas.hidden,false);assert.equal(documentRoot.descendants().some(n=>/legacy/.test(n.id||'')),false);
assert.equal($('compute-overview').className,'pageheading');
assert.equal($('compute-heading').textContent,'コンピュート & ガバナンス');
assert.equal($('compute-environment').textContent,'LOCAL · ローカルラボ');
assert.deepEqual(atlas.children.map(n=>n.id),['compute-overview','compute-runtime-host','compute-usage','compute-auto-chill','compute-diagnostics']);
assert.equal($('runtime-panel').parentElement,$('compute-runtime-host'));
assert.deepEqual([...$('runtime-facts').children].filter(n=>n.tagName==='DT').map(n=>n.textContent),['状態','観測時刻','影響範囲','停止の妨げ']);
assert.deepEqual([...$('runtime-actions').children].map(n=>n.id),['runtime-refresh','runtime-wake','runtime-chill']);
assert.deepEqual([...$('runtime-actions').children].map(n=>n.textContent),['状態を更新','起動','休止']);
assert.equal($('runtime-message').tagName,'P');assert.equal($('runtime-message').role,'status');
assert.equal($('runtime-chill-confirm').tagName,'FORM');assert.equal($('runtime-chill-confirm').hidden,true);
for(const id of ['compute-usage','compute-auto-chill','compute-diagnostics']){assert.equal($(id).tagName,'DETAILS');assert.equal($(id).open,false);}
const wrap=$('compute-resource-table');assert.equal(wrap.className,'tablewrap');assert.equal(wrap.tabIndex,0);assert.equal(wrap.role,'region');
const table=wrap.children[0];assert.equal(table.tagName,'TABLE');
assert.deepEqual([...table.children[1].children[0].children].map(n=>n.textContent),['資源','設定割当','観測使用量','ノード余力']);
assert.equal($('compute-auto-chill').descendants().includes($('runtime-minutes')),true);
assert.equal($('compute-auto-chill').descendants().includes($('runtime-save')),true);
assert.equal($('compute-diagnostics').descendants().includes($('compute-diag-state')),true);
assert.equal($('compute-diagnostics').descendants().includes($('observation-compute-panel')),true);
assert.equal(atlas.descendants().some(n=>atlasHas(n,'page-head')),false);
''')

    def test_labels_and_summaries_switch_language(self):
        self.bears(r'''
await boot();atlasShow('bears');
for(const [lang,heading,env,source,explain,transform] of [['ja','AI Bears','LOCAL · ローカルラボ','元SQLの入力元','SQLを説明','変換案を作る'],['en','AI Bears','LOCAL · Local lab','Source SQL input','Explain the SQL','Create a transform draft']]){
 setLanguage(lang);
 assert.equal($('bears-heading').textContent,heading);assert.equal($('bears-environment').textContent,env);
 assert.equal($('bears-source-label').textContent,source);assert.equal($('bears-explain').textContent,explain);assert.equal($('bears-transform').textContent,transform);
 assert.equal($('bears-explanation-title').textContent,t('SQL explanation'));assert.equal($('bears-proposal-title').textContent,t('Transform draft'));
 assert.equal($('bears-curator-title').textContent,'Catalog Curator');assert.equal($('bears-policy-title').textContent,t('Observation & policy'));
 assert.equal($('bears-explanation-details').children[0].textContent,t('Explanation options and recovery'));
 assert.equal($('bears-evidence-details').children[0].textContent,t('Evidence, history and requests'));
}
''')

    def test_compute_labels_and_aria_switch_language(self):
        self.compute(r'''
await start();$('tab-compute').click();atlasShow('compute');
for(const [lang,heading,refresh,wake,chill,usage,diag] of [['ja','コンピュート & ガバナンス','状態を更新','起動','休止','リソースの割当・使用量・余力','管理者向け診断'],['en','Compute & Governance','Refresh state','Wake','Chill','Resource allocation, usage and headroom','Administrator diagnostics']]){
 setLanguage(lang);
 assert.equal($('compute-heading').textContent,heading);
 assert.equal($('runtime-refresh').textContent,refresh);assert.equal($('runtime-wake').textContent,wake);assert.equal($('runtime-chill').textContent,chill);
 assert.equal($('compute-usage').children[0].textContent,usage);assert.equal($('compute-diagnostics').children[0].textContent,diag);
 assert.equal($('compute-resource-table').getAttribute('aria-label'),t('LOCAL allocation, observed usage and node headroom'));
 assert.equal($('compute-usage-note').textContent,t('Configured allocation, measured usage and node headroom are separate observations. Do not add or compare them directly.'));
}
''')

    def test_bears_transform_runs_the_existing_proposal_and_handoff_flow(self):
        self.bears(r'''
await boot();atlasShow('bears');setLanguage('ja');
objects=[proposalObject(0)];objectsRoute=async()=>({items:structuredClone(objects),next_cursor:null});
await activate($('tab-bears'));$('bears-table').onfocus();
assert.equal(await until(()=>dbProposal.objects.length===1),true);assert.equal($('bears-table').options.some(o=>o.value==='object-0'),true);
$('bears-table').value='object-0';$('bears-table').onchange();assert.equal(dbProposal.sources.length,1);
route=async()=>wrap(proposalRecord());await $('bears-transform').onclick();
assert.ok(dbProposal.record);assert.equal($('bears-proposal').hidden,false);
assert.equal($('bear-handoff-target').textContent,'marts.注文 0');
assert.equal($('bear-handoff-role').textContent,'管理者');
assert.match($('bear-handoff-created').textContent,/JST/);
assert.equal($('bear-handoff-sql').textContent,dbProposal.record.sql);
assert.equal($('bear-handoff-approved').checked,false);
const before=worksheets.length;await $('bear-handoff-worksheet').onclick();assert.equal(worksheets.length,before);
$('bear-handoff-approved').checked=true;await $('bear-handoff-worksheet').onclick();assert.equal(worksheets.length,before+1);
assert.match($('bear-handoff-message').textContent,/下書き/);
assert.equal(proposalPosts().length,1);
''')

    def test_bears_loads_the_database_list_and_schema_without_a_worksheet_context(self):
        self.bears(r'''
const base=inventory[0];
inventory=Array.from({length:148},(_,i)=>({...base,id:'db-'+i,display_name:'DB '+i,sql_catalog:'catalog_'+i}));
schemasRoute=async path=>{const id=decodeURIComponent(path.split('/databases/')[1].split('/schemas')[0]);return {items:[{...schemas[0],id:'schema-'+id,database_id:id,sql_name:'marts',display_name:'Marts'}]};};
objects=[proposalObject(0,'schema-db-0')];objects[0].ref={...objects[0].ref,database_id:'db-0',schema_id:'schema-db-0'};
objectsRoute=async()=>({items:structuredClone(objects),next_cursor:null});
await boot([{...input(),database_id:'',schema_id:'',selected_role:''}]);
await activate($('tab-bears'));$('bears-inherit').onclick();
assert.equal(await until(()=>$('bears-database').options.filter(o=>o.value).length===148),true);
assert.equal($('bears-database').options.filter(o=>o.value).length,148);
assert.doesNotMatch($('bears-message').textContent,/コンテキストを確認中|Checking context/);
$('bears-database').value='db-0';await $('bears-database').onchange();
assert.equal(await until(()=>$('bears-schema').options.some(o=>o.value==='schema-db-0')),true);
$('bears-schema').value='schema-db-0';await $('bears-schema').onchange();
assert.equal(await until(()=>dbProposal.objects.length===1),true);
assert.equal($('bear-query-sql').value,'SELECT 1');
assert.equal($('bears-explain').disabled,false);
assert.equal($('bears-table').options.some(o=>o.value==='object-0'),true);
assert.doesNotMatch($('bears-message').textContent,/コンテキストを確認中|Checking context/);
''')

    def test_bears_lists_stub_named_objects_and_shows_empty_or_failed_reason(self):
        self.bears(r"""
const empty='No eligible sources. Choose another query-enabled schema.',unavailable='Source objects unavailable. Refresh context and load the chosen schema again.';
const candidate={name:'orders',kind:'TABLE',ref:{database_id:'finance',schema_id:'finance-marts',object_id:'22222222-2222-4222-8222-222222222222',incarnation:1,binding_revision:2,generation_id:'finance-gen'}};
await dualBoot();objectsRoute=async path=>path.includes('/finance-marts/')?{items:[structuredClone(candidate)],next_cursor:null}:{items:[],next_cursor:null};atlasShow('bears');setLanguage('ja');await activate($('tab-bears'));
$('bears-database').value='finance';await $('bears-database').onchange();for(let i=0;i<100&&!(dbSchemaScope===dbSchemaKey(dbCurrent().selected_role,dbCurrent().database_id)&&$('bears-schema').options.some(o=>o.value==='finance-marts'));i++)await tick();assert.equal(dbSchemaScope===dbSchemaKey(dbCurrent().selected_role,dbCurrent().database_id)&&$('bears-schema').options.some(o=>o.value==='finance-marts'),true);
$('bears-schema').value='finance-marts';await $('bears-schema').onchange();for(let i=0;i<100&&dbProposal.objects.length!==1;i++)await tick();assert.equal(dbProposal.objects.length,1);
const option=$('bears-table').options.find(o=>o.value==='22222222-2222-4222-8222-222222222222');
assert.ok(option);assert.match(option.textContent,/marts\.orders/);assert.equal(dbProposal.pickerNotice,'');
objectsRoute=async()=>({items:[],next_cursor:null});dbProposal.objects=[];dbProposal.pickerNotice='';workbenchAtlasBears.loadKey='';dbProposal.fetchToken=null;
$('bears-table').onfocus();assert.equal(await until(()=>dbProposal.pickerNotice===empty),true);
assert.equal($('bears-table').children[0].textContent,t(empty));assert.notEqual($('bears-table').children[0].textContent,t('Preparing source objects…'));assert.equal(dbProposal.fetchToken,null);assert.equal(workbenchAtlasBears.autoObjects,false);
objectsRoute=async()=>{throw Error('unavailable');};dbProposal.objects=[];dbProposal.pickerNotice='';workbenchAtlasBears.loadKey='';dbProposal.fetchToken=null;
$('bears-table').onfocus();assert.equal(await until(()=>dbProposal.pickerNotice===unavailable),true);
assert.equal($('bears-table').children[0].textContent,t(unavailable));assert.doesNotMatch($('bears-table').children[0].textContent,/読み込んでいます|Loading/);assert.equal(dbProposal.fetchToken,null);
""")

    def test_bears_refreshes_the_selected_database_on_first_display_and_keeps_one_context_line(self):
        self.bears(r"""
await dualBoot();dbSchemaScope='';dbSchemaCache.clear();dbSchemas=[];dbContextFailure=null;
const old=deferred();let schemaReads=0;schemasRoute=async()=>{schemaReads++;return old.promise;};
atlasShow('bears');setLanguage('ja');$('bears-explanation-details').open=true;
const before=calls.filter(c=>c.path==='/api/v1/databases/db/schemas').length;
await activate($('tab-bears'));assert.equal(await until(()=>schemaReads>0),true);for(let i=0;i<10;i++)await tick();
const shown=node=>{for(let n=node;n;n=n.parentElement)if(n.hidden)return false;return true;};
const lines=[...$('bears').descendants().filter(n=>n.tagName==='P'&&n.getAttribute('role')==='status'&&shown(n))].map(n=>n.textContent).filter(Boolean);
assert.equal(calls.filter(c=>c.path==='/api/v1/databases/db/schemas').length,before+1);
assert.equal($('bears-message').textContent,t('Loading schemas…'));
assert.deepEqual(lines,[t('Loading schemas…')]);
assert.equal($('explanation-doctor-context').hidden,true);assert.equal($('explanation-doctor-action-state').hidden,true);
old.resolve({items:structuredClone(schemas)});assert.equal(await until(()=>!!dbSchemaScope),true);await tick();
""")

    def test_bears_shows_only_the_top_context_controls(self):
        self.bears(r'''
await boot();atlasShow('bears');await activate($('tab-bears'));
const visible=node=>{for(let n=node;n;n=n.parentElement)if(n.hidden)return false;return true;};
for(const id of ['bears-database','bears-schema','bears-role','bears-table'])assert.equal(visible($(id)),true,id);
for(const id of ['curator-role','curator-database','curator-schema','curator-object','curator-refresh','curator-load','curator-more','curator-create'])assert.equal(documentRoot.descendants().some(n=>n.id===id),false,id);
for(const kind of ['sre','policy'])for(const name of ['role','database','schema','refresh'])assert.equal(documentRoot.descendants().some(n=>n.id==='observation-'+kind+'-'+name),false,kind+'-'+name);
assert.equal(visible($('curator-history')),true);assert.equal(visible($('curator-saved')),true);
''')

    def test_compute_wake_and_chill_follow_the_observed_state_and_blocker_text(self):
        self.compute(r'''
await start();$('tab-compute').click();setLanguage('ja');Date.now=()=>Date.parse(runtime.observed_at);
runtime.physical_state='chilled';runtime.replicas=0;runtime.ready_replicas=0;runtime.operation_phase=null;await refreshStatus();
assert.equal($('runtime-wake').disabled,false);assert.equal($('runtime-chill').disabled,true);assert.match($('runtime-action-reason').textContent,/起動すると全利用者/);
runtime.physical_state='active';runtime.replicas=1;runtime.ready_replicas=1;await refreshStatus();
assert.equal($('runtime-wake').disabled,true);assert.equal($('runtime-chill').disabled,false);assert.match($('runtime-action-reason').textContent,/休止すると全利用者/);
assert.equal($('runtime-blockers-fact').textContent,'把握している実行中・結果不明の処理はありません。');
runtime.blockers={runtime_operations:0,admissions:1,unknown:0,creation:{requests:0,effects:0,roots:0}};await refreshStatus();
assert.equal($('runtime-chill').disabled,true);
assert.equal($('runtime-blockers-fact').textContent,'受付済みの処理が1件あります。');
assert.doesNotMatch($('runtime-blockers-fact').textContent,/実行中の操作|結果不明の受付|DB 作成要求|: 0/);
assert.equal($('runtime-action-reason').textContent,'受付済みの処理が1件あるため休止できません。');
''')

    def test_compute_wake_confirmation_uses_the_same_impact_and_consent(self):
        self.compute(r'''
await start();$('tab-compute').click();setLanguage('ja');Date.now=()=>Date.parse(runtime.observed_at);
runtime.physical_state='chilled';runtime.replicas=0;runtime.ready_replicas=0;await refreshStatus();
assert.equal($('runtime-wake').disabled,false);assert.equal($('runtime-chill').disabled,true);
const before=posts().length;await $('runtime-wake').onclick();assert.equal(posts().length,before);
assert.equal($('runtime-chill-confirm').hidden,false);assert.equal($('runtime-chill-confirm-title').textContent,'起動の確認');
assert.equal($('runtime-chill-impact').className,'impact neutral');
assert.equal($('runtime-chill-impact').textContent,t('Starting shared LOCAL returns it to available for all users.'));
assert.equal($('runtime-chill-confirm-submit').textContent,'確認して起動');assert.equal($('runtime-chill-confirm-submit').className,'primary');
$('runtime-chill-confirm-check').checked=true;await $('runtime-chill-confirm-submit').onclick();
assert.equal(posts().length,before+1);assert.equal(posts().at(-1).path,BASE+'/wake');assert.equal(dbRuntime.operation.phase,'ACCEPTED');
assert.equal($('runtime-chill-confirm').hidden,true);
''')

    def test_bears_unknown_explanation_is_not_resent(self):
        self.bears(r'''
await boot();atlasShow('bears');await activate($('tab-bears'));
$('bear-query-sql').value='SELECT 1';$('bear-query-sql').dispatchEvent(new Event('input'));
route=async()=>{throw Error('lost ACK');};await $('bears-explain').onclick();
const count=explanationCalls().length;assert.equal(count,1);assert.equal($('bears-explain').disabled,true);
await $('bears-explain').onclick();await $('bears-explain').onclick();
assert.equal(explanationCalls().length,count);assert.equal(posts().length,1);
assert.match($('bears-message').textContent,/不明|unknown|Uncertain/i);
''')

    def test_compute_unknown_receipt_is_not_resent_and_only_explicit_reconcile_replays(self):
        self.compute(r'''
await start();$('tab-compute').click();setLanguage('ja');Date.now=()=>Date.parse(runtime.observed_at);
actionRoute=async()=>{throw Error('lost ACK');};await $('runtime-wake').onclick();$('runtime-chill-confirm-check').checked=true;await $('runtime-chill-confirm-submit').onclick();
assert.equal(posts().length,1);assert.match($('runtime-message').textContent,/不明|UNKNOWN/i);
assert.equal($('runtime-receipt').hidden,false);assert.equal($('runtime-receipt-facts').hidden,false);
await refreshStatus();await $('runtime-wake').onclick();assert.equal(posts().length,1);
const key=dbRuntime.intent.key;actionRoute=null;await $('runtime-receipt').onclick();
assert.equal(posts().length,2);assert.equal(posts()[1].options.headers['Idempotency-Key'],key);assert.equal($('runtime-receipt').hidden,true);
''')

    def test_compute_pause_confirmation_has_the_impact_and_red_button(self):
        self.compute(r'''
await start();$('tab-compute').click();setLanguage('ja');runtime.physical_state='active';runtime.replicas=1;runtime.ready_replicas=1;await refreshStatus();assert.equal($('runtime-chill').disabled,false);
await $('runtime-chill').onclick();
assert.equal($('runtime-chill-confirm').hidden,false);
assert.equal($('runtime-chill-impact').parentElement,$('runtime-chill-confirm'));
assert.equal($('runtime-chill-impact').className,'impact');
assert.equal($('runtime-chill-impact').textContent,t('Chilling stops all SQL. There are no running processes.'));
assert.equal($('runtime-chill-confirm-submit').className.split(' ').includes('danger'),true);
assert.equal($('runtime-chill-confirm-check').required,true);
const before=posts().length;$('runtime-chill-confirm-check').checked=true;await $('runtime-chill-confirm-submit').onclick();
assert.equal(posts().length,before+1);assert.equal($('runtime-chill-confirm').hidden,true);
''')

    def test_non_admin_sees_one_read_only_line_and_no_operator_actions(self):
        self.compute(r'''
await start();$('tab-compute').click();currentRole='bi_viewer';dbControls();
assert.equal($('compute-diagnostics').hidden,true);assert.equal($('compute-usage-list').hidden,true);assert.equal($('compute-usage-note').hidden,false);
assert.equal($('runtime-capability').hidden,false);assert.match($('runtime-capability').textContent,/閲覧のみ|Read-only/);
for(const id of ['runtime-wake','runtime-chill','runtime-save','runtime-reconcile'])assert.equal($(id).disabled,true,id);
assert.equal($('runtime-refresh').disabled,false);
setLanguage('ja');assert.match($('runtime-capability').textContent,/閲覧のみ/);
''')

    def test_compute_header_chip_sidebar_and_page_state_stay_equal(self):
        self.compute(r'''
await start();$('tab-compute').click();Date.now=()=>Date.parse(runtime.observed_at);
const read=()=>[$('database-compute-state').textContent,$('runtime-state-status').textContent,$('rail-warehouse-state').textContent];
runtime.physical_state='chilled';runtime.operation_phase=null;await refreshStatus();
assert.deepEqual(read(),['LOCAL · Paused','Paused','LOCAL · Paused']);
runtime.physical_state='active';await refreshStatus();
assert.deepEqual(read(),['LOCAL · Available','Available','LOCAL · Available']);
runtime.physical_state='unknown';await refreshStatus();
assert.deepEqual(read(),['LOCAL · Unknown','Unknown','LOCAL · Unknown']);
setLanguage('ja');assert.equal($('runtime-state-status').textContent,t('Unknown'));assert.equal($('rail-warehouse-state').textContent,'LOCAL · '+t('Unknown'));
''')

    def test_raw_identifiers_are_only_inside_closed_disclosures(self):
        self.bears(r'''
await boot();atlasShow('bears');
const proposalId='6b1de711-810a-4a9b-a1a9-2473b7faf13f',curatorId='7c2ef822-921b-4b0c-b2ba-3584c8bcf24a';
objects=[proposalObject(0)];objectsRoute=async()=>({items:structuredClone(objects),next_cursor:null});
await activate($('tab-bears'));$('bears-table').onfocus();assert.equal(await until(()=>dbProposal.objects.length===1),true);$('bears-table').value='object-0';$('bears-table').onchange();
route=async()=>wrap(proposalRecord({id:proposalId}));await $('bears-transform').onclick();
dbCurator.record={id:curatorId,context:{database_id:'db',schema_id:'schema',warehouse_id:'LOCAL',binding_revision:1,generation_id:'gen',grant_revision:4,sql_catalog:'sales',sql_schema:'marts',selected_role:'admin'},metadata:{relation:{schema:'marts',table:'注文 0',kind:'TABLE'},captured_at:'2026-11-01T01:00:00Z'},proposal:{summary:'注文の要約',description:'注文の説明',columns:[]},state:'SUCCEEDED',inference_state:'SUCCEEDED',model:{provider:'local-openai',name:'local-model'},error_code:null,cancel_requested:false};dbCuratorSync();
const visible=[$('bears-message').textContent,$('bear-handoff-target').textContent,$('bear-handoff-role').textContent,$('bear-handoff-created').textContent,$('bears-curator-target').textContent,$('bears-curator-description').textContent].join(' ');
assert.equal(visible.includes(proposalId),false);assert.equal(visible.includes(curatorId),false);
assert.equal($('bears-evidence-details').open,false);
assert.ok(atlasText($('bears-request-ids')).includes(proposalId));assert.ok(atlasText($('bears-request-ids')).includes(curatorId));
''')

    def test_screen_switch_resets_scroll_and_sidebar_links_are_roots(self):
        self.bears(r'''
await boot();atlasShow('bears');
const calls=[];globalThis.scrollTo=(x,y)=>calls.push([x,y]);
assert.equal($('tab-bears').getAttribute('href'),'#bears');
await activate($('tab-bears'));assert.deepEqual(calls.at(-1),[0,0]);
await activate($('tab-warehouse'));assert.deepEqual(calls.at(-1),[0,0]);
await activate($('tab-bears'));assert.deepEqual(calls.at(-1),[0,0]);
assert.equal(activeTab,'bears');
''')

    def test_strict_document_boot_keeps_every_new_control_connected(self):
        self.bears(r'''
await boot();atlasShow('bears');atlasShow('compute');atlasShow('warehouse');atlasShow('transformation');
const ids=['bears-pageheading','bears-heading','bears-environment','bears-form','bears-database','bears-schema','bears-role','bears-table','bears-source','bears-inherit','bear-query-sql','bears-recipe','bears-explain','bears-transform','bears-message',
'bears-explanation','bears-explanation-title','bears-explanation-body','bears-explanation-details','bears-proposal','bears-proposal-title','bears-proposal-body','bear-handoff','bear-handoff-target','bear-handoff-role','bear-handoff-created','bear-handoff-sql','bear-handoff-approved','bear-handoff-worksheet','bear-handoff-transformation',
'bears-curator','bears-curator-title','bears-curator-target','bears-curator-description','bears-curator-propose','bears-curator-body','curator-panel',
'bears-policy','bears-policy-title','bears-policy-facts','bears-sre-fact','bears-policy-fact','bears-sample-fact','bears-compute-link','bears-evidence-details','bears-evidence-body','bears-evidence-sre','bears-evidence-policy','bear-panel-sre','bear-panel-policy','bears-request-ids',
'compute-overview','compute-heading','compute-environment','compute-runtime-host','runtime-panel','runtime-title','runtime-facts','runtime-state-status','runtime-state-badge','runtime-observed-fact','runtime-impact','runtime-blockers-fact','runtime-controls-section','runtime-actions','runtime-refresh','runtime-wake','runtime-chill','runtime-action-reason','runtime-capability','runtime-chill-impact','runtime-chill-confirm','runtime-chill-confirm-title','runtime-chill-confirm-check','runtime-chill-impact-text','runtime-chill-confirm-actions','runtime-chill-confirm-submit','runtime-chill-cancel','runtime-message','runtime-receipt-facts','runtime-receipt-summary','runtime-receipt',
'compute-usage','compute-resource-table','compute-usage-list','compute-usage-note','compute-auto-chill','runtime-policy-section','runtime-policy','runtime-draft','runtime-minutes','runtime-save','runtime-reload','compute-diagnostics','compute-diag-state','compute-diag-history','compute-diag-runtime','compute-diag-policy','compute-diag-logs','compute-ai-settings','ai-settings'];
for(const id of ids){const node=$(id);assert.ok(node,id);assert.equal(connected(node),true,id);}
const before={bears:$('bears'),compute:$('compute'),runtime:$('runtime-panel'),table:$('compute-resource-table')};
workbenchAtlasBearsSync();dbRuntimeSync();setLanguage('ja');setLanguage('en');
assert.equal($('bears'),before.bears);assert.equal($('compute'),before.compute);assert.equal($('runtime-panel'),before.runtime);assert.equal($('compute-resource-table'),before.table);
connectedOnly=true;
workbenchAtlasBearsSync();dbRuntimeSync();
for(const id of ids){assert.ok(nodes[id],id+' strict');assert.equal(connected(nodes[id]),true,id+' strict');}
connectedOnly=false;
''')

    def test_44px_and_table_wrapping_rules_cover_both_pages(self):
        self.assertIn('.atlas-flow:is(button,select,input:not([type=checkbox]):not([type=radio]),textarea,summary){min-height:44px;font-size:16px}', COMPACT_CSS)
        self.assertIn('.atlas-flowa[href]{display:inline-flex;align-items:center;min-height:44px', COMPACT_CSS)
        self.assertIn('overflow-x:auto', COMPACT_CSS)
        self.assertIn('max-width:320px', COMPACT_CSS)
        self.assertIn('overflow-wrap:anywhere', COMPACT_CSS)
        self.assertIn('#bears-atlas#bears-message,#compute-atlas#runtime-message{border-left:3pxsolidvar(--green)', COMPACT_CSS)
        self.assertIn('max-width:1100px', COMPACT_CSS)
        self.assertIn('body{overflow-x:hidden}', COMPACT_CSS)
        self.assertIn('padding:4px00;border-top:1pxsolidvar(--line);margin-top:24px', COMPACT_CSS)
        self.assertIn('grid-template-columns:84pxminmax(0,1fr);gap:9px;font-size:11px;margin:16px0', COMPACT_CSS)
        self.assertIn('font-weight:400;color:var(--muted);font-size:11px', COMPACT_CSS)
        self.assertIn('width:100%;min-height:120px;box-sizing:border-box', COMPACT_CSS)
        self.assertIn('#compute-atlas#runtime-chill-impact.neutral', COMPACT_CSS)
        self.assertNotIn('#runtime-panel#runtime-impact', COMPACT_CSS)
        self.assertNotIn('#compute-diagnostics{border:1px solid', COMPACT_CSS)
        self.assertNotIn('position:absolute;left:0;right:0;top:0', COMPACT_CSS)


if __name__ == '__main__':
    unittest.main()
