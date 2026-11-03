"""Catalog Curator C1: production JavaScript in the fixture-only Node adapter."""
import unittest
from unittest.mock import patch

import test_workbench_database_ui as harness
from test_workbench_bears_database_ui import COMMON


CURATOR = r'''
const curatorCalls=()=>calls.filter(c=>c.path.startsWith(dbCuratorEndpoint));
const curatorPosts=()=>curatorCalls().filter(c=>c.path===dbCuratorEndpoint&&c.options?.method==='POST');
const curatorObject=(i=0,extra={})=>({id:'20000000-0000-4000-8000-'+String(i).padStart(12,'0'),physical_name:'注文 <img> '+i,kind:i%2?'VIEW':'TABLE',ref:{database_id:'db',schema_id:'schema',object_id:'20000000-0000-4000-8000-'+String(i).padStart(12,'0'),incarnation:1,binding_revision:1,generation_id:'gen'},capabilities:{resolve:true,columns:true},...extra});
const change=(name,value)=>{$('curator-'+name).value=value;return $('curator-'+name).onchange();};
async function curatorContext(role='admin'){
 if(dbCurator.role!==role)change('role',role);
 await dbCuratorRefresh();change('database','db');await dbCuratorRefresh();change('schema','schema');await dbCuratorRefresh();assert.equal(dbCuratorAllowed(),true);
}
async function curatorReady(role='admin'){await boot();await curatorContext(role);objects=[curatorObject(),curatorObject(1)];await dbCuratorLoad();change('object','0');}
const curatorRecord=(extra={},intent=dbCurator.intents.at(-1))=>{
 const body=intent?.body||{context:dbCurator.context,relation:dbCurator.source.ref,language};const source=intent?.source||dbCurator.source;
 return {id:'6b1de711-810a-4a9b-a1a9-2473b7faf13f',kind:'catalog_proposal',state:'SUCCEEDED',actor:'owner',context:structuredClone(body.context),input:{relation:structuredClone(body.relation),language:body.language},model:{provider:'local-openai',name:'local-model'},metadata:{relation:{schema:source.schema,table:source.name,kind:source.kind,columns:[{name:'合計',type:'decimal(12,2)'},{name:'詳細',type:'{"type":"list","element":"string"}'}]},captured_at:'2026-11-01T01:00:00Z'},proposal:{summary:'推測 <img src=x onerror=evil()>',description:'仮の説明',columns:[{name:'詳細',description:'詳細の案'},{name:'合計',description:'金額の案'}]},error_code:null,cancel_requested:false,inference_state:'SUCCEEDED',created_at:'2026-11-01T01:00:00Z',updated_at:'2026-11-01T01:01:00Z',...extra};
};
const curatorPending=(extra={})=>curatorRecord({state:'RUNNING',inference_state:'RUNNING',proposal:null,...extra});
async function curatorSubmit(record=null){route=async()=>wrap(record||curatorRecord());await dbCuratorSubmit();}
'''


class CuratorDatabaseUI(unittest.TestCase):
    def javascript(self, body, strict_dom=False):
        run = harness.subprocess.run
        def strict_run(args, **kwargs):
            kwargs['input'] = kwargs['input'].replace(
                'const $=id=>strictRemoved',
                "const $=id=>id==='curator-panel'&&!nodes[id]?.parentElement?null:strictRemoved")
            return run(args, **kwargs)
        with patch.object(harness.subprocess, 'run', strict_run if strict_dom else run):
            harness.DatabaseUI.javascript(self, COMMON + CURATOR + body)

    def test_staged_guidance_is_bilingual_and_rendering_has_no_side_effects(self):
        self.javascript(r'''
{
await boot();editSQL('SELECT worksheet keep');editDoctor('SELECT doctor keep');$('copilot-request').value='keep draft';queryResult={rows:[[7]],columns:['keep']};
const stages=[
 [async()=>{},'Refresh context to load Databases for the selected role.','選択したロールの Database を読み込むには、コンテキストを更新してください。'],
 [async()=>{await $('curator-refresh').onclick();},'Choose a Database, then refresh context to load its schemas.','Database を選び、コンテキストを更新してスキーマを読み込んでください。'],
 [async()=>{change('database','db');},'Refresh context to load schemas for the selected Database.','選択した Database のスキーマを読み込むには、コンテキストを更新してください。'],
 [async()=>{await $('curator-refresh').onclick();},'Choose a primary schema, then refresh context to verify it.','主スキーマを選び、コンテキストを更新して確認してください。'],
 [async()=>{change('schema','schema');},'Refresh context to verify the selected primary schema.','選択した主スキーマを確認するには、コンテキストを更新してください。'],
 [async()=>{await $('curator-refresh').onclick();},'Load source objects, then choose one TABLE or VIEW and explicitly request a proposal.','参照元を読み込み、TABLE または VIEW を1件選んでから、案の作成を明示的に要求してください。'],
 [async()=>{objects=[curatorObject()];await $('curator-load').onclick();},'Choose one TABLE or VIEW, then explicitly request a proposal.','TABLE または VIEW を1件選び、案の作成を明示的に要求してください。'],
 [async()=>{change('object','0');},'Select Explain and draft descriptions to explicitly request a proposal for the chosen source.','選択した参照元の案を明示的に要求するには、「説明と説明文案を作成」を選んでください。'],
 [async()=>{route=async()=>{throw Error('lost');};await $('curator-create').onclick();},'Curator outcome unknown. Recover the original request explicitly.','Curator の結果は不明です。元の要求を明示的に復旧してください。']
];
const snapshot=()=>JSON.stringify({curator:dbCurator,storage:[...storage],calls,worksheets:worksheets.map(dbInput),worksheetId,sql:$('sql').value,doctor:$('bear-query-sql').value,draft:$('copilot-request').value,result:queryResult,explorer:dbExplorer,history:dbHistory,explanation:dbExplanation,proposal:dbProposal,wap:[...dataopsPanels].map(([key,p])=>[key,p.context,p.model,p.sources,p.op])},(_,value)=>value instanceof Node?{id:value.id,value:value.value,textContent:value.textContent,disabled:value.disabled}:value);
for(const [setup,en,ja] of stages){
 await setup();
 for(const [locale,expected] of [['en',en],['ja',ja]]){
  setLanguage(locale);queryResult={rows:[[7]],columns:['keep']};const before=snapshot(),result=queryResult,source=dbCurator.source,context=dbCurator.context;
  dbCuratorRender();dbCuratorRender();
  assert.equal($('curator-output').children[0].textContent,expected);assert.deepEqual(snapshot(),before);assert.equal(queryResult,result);assert.equal(dbCurator.source,source);assert.equal(dbCurator.context,context);
 }
}
assert.equal(curatorPosts().length,1);assert.equal($('sql').value,'SELECT worksheet keep');assert.equal($('bear-query-sql').value,'SELECT doctor keep');assert.equal($('copilot-request').value,'keep draft');assert.deepEqual(queryResult,{rows:[[7]],columns:['keep']});
}
''')

    def test_independent_context_without_worksheet_and_explicit_inventory(self):
        self.javascript(r'''
await boot();assert.equal(curatorCalls().length,0);assert.equal(dbCurator.context,null);assert.equal(dbCurator.objects.length,0);
const before=JSON.stringify(dbExplorer);worksheets=[];worksheetId='missing';await curatorContext('bi_viewer');
assert.equal(dbCurator.context.selected_role,'bi_viewer');assert.equal(JSON.stringify(dbExplorer),before);assert.equal(dbCurator.objects.length,0);
objects=[curatorObject()];await dbCuratorLoad();change('object','0');await curatorSubmit();assert.equal(curatorPosts().length,1);assert.equal(dbCurator.record.state,'SUCCEEDED');
''')

    def test_roles_query_rights_and_header_maximum_ceiling(self):
        self.javascript(r'''
await boot();for(const role of ['bi_viewer','data_engineer','admin']){await curatorContext(role);assert.equal(dbCurator.context.selected_role,role);}
currentRole='bi_viewer';dbCuratorSync();assert.equal(dbCuratorAllowed(),false);assert.equal(dbCurator.record,null);await dbCuratorRefresh();assert.equal(dbCurator.context,null);
change('role','bi_viewer');session.maximum_role='bi_viewer';await curatorContext('bi_viewer');assert.equal(dbCuratorAllowed(),true);change('role','admin');await dbCuratorRefresh();assert.equal(dbCuratorAllowed(),false);
''')

    def test_query_disabled_database_or_schema_cannot_establish_context(self):
        self.javascript(r'''
await boot();inventory[0].capabilities.query=false;await dbCuratorRefresh();assert.equal(dbCurator.databases.length,0);change('database','db');change('schema','schema');await dbCuratorRefresh();assert.equal(dbCuratorAllowed(),false);
inventory[0].capabilities.query=true;schemas[0].capabilities.query=false;await dbCuratorRefresh();assert.equal(dbCuratorAllowed(),false);assert.ok(!dbCurator.schemas.some(s=>s.id==='schema'));
''')

    def test_explicit_pagination_and_original_stable_primary_ref(self):
        self.javascript(r'''
await boot();await curatorContext();const before=JSON.stringify(dbInput(dbCurrent()));let count=0;
objectsRoute=async(path,options)=>{assert.equal(options.headers['X-Shirokuma-Role'],'admin');assert.ok(path.startsWith('/api/v1/schemas/schema/objects?'));const params=new URL(path,'http://local').searchParams;assert.equal(params.get('limit'),'100');assert.equal(params.get('cursor'),count?'opaque next':null);return {items:[curatorObject(count++)],next_cursor:count===1?'opaque next':null};};
await dbCuratorLoad();assert.equal(count,1);change('object','0');assert.ok(Object.isFrozen(dbCurator.source.ref));const original=structuredClone(dbCurator.source.ref);await dbCuratorLoad(true);assert.equal(count,2);assert.equal(dbCurator.objects.length,2);assert.equal(dbCurator.source,null);change('object','0');assert.deepEqual(dbCurator.source.ref,original);assert.equal(JSON.stringify(dbInput(dbCurrent())),before);
''')

    def test_picker_rejects_cross_schema_db_unstable_extra_ref_and_unsupported(self):
        self.javascript(r'''
await boot();await curatorContext();const good=curatorObject();objects=[good,...['schema_id','database_id','generation_id','object_id'].map(k=>({...good,ref:{...good.ref,[k]:'wrong'}})),{...good,ref:{...good.ref,incarnation:'1'}},{...good,ref:{...good.ref,extra:true}},{...good,kind:'ROUTINE'},{...good,capabilities:{resolve:true,columns:false}}];await dbCuratorLoad();assert.equal(dbCurator.objects.length,1);
''')

    def test_more_inventory_denial_purges_context_names_results_but_preserves_receipts(self):
        for status in [401, 403, 404]:
            with self.subTest(status=status):
                self.javascript('const status=' + str(status) + ';\n' + r'''
{
await boot();await curatorContext();
objectsRoute=async()=>({items:[curatorObject(),curatorObject(1)],next_cursor:'page2'});
await $('curator-load').onclick();change('object','0');await curatorSubmit();const historical=dbCurator.record;
route=async()=>({context:dbCurator.context,explanations:[historical]});await $('curator-history').onclick();
await curatorSubmit(curatorRecord({id:crypto.randomUUID()}));
assert.equal(dbCurator.objects.length,2);assert.ok(dbCurator.source.ref);assert.ok(dbCurator.context);assert.equal(dbCurator.history.length,1);
assert.ok(visibleText($('curator-output')).includes('仮の説明'));assert.ok(visibleText($('curator-object')).includes('注文'));assert.ok(visibleText($('curator-saved')).includes('注文'));
const receipts=dbCurator.intents.slice(),stored=storage.get(dbCuratorKey),posts=curatorPosts().length,proofs=receipts.map(i=>dbCurator.proofs.get(i));
objectsRoute=async(path,options)=>{assert.equal(new URL(path,'http://local').searchParams.get('cursor'),'page2');assert.equal(options.method,undefined);return {http:status,body:{code:status===404?'NOT_FOUND':'DB_ACCESS_REVOKED',message:'secret denied'}};};
await $('curator-more').onclick();
assert.equal(dbCurator.context,null);assert.equal(dbCurator.inventoryAuth,'');assert.deepEqual(dbCurator.objects,[]);assert.equal(dbCurator.cursor,null);assert.equal(dbCurator.source,null);assert.equal(dbCurator.record,null);assert.equal(dbCurator.origin,null);assert.deepEqual(dbCurator.history,[]);
for(const id of ['curator-object','curator-output','curator-saved'])assert.doesNotMatch(visibleText($(id)),/注文|仮の説明|secret denied|20000000/);
assert.equal($('curator-object').disabled,true);assert.equal($('curator-create').disabled,true);assert.equal($('curator-more').hidden,true);assert.equal(dbCuratorAllowed(),false);
assert.equal(storage.get(dbCuratorKey),stored);assert.equal(curatorPosts().length,posts);receipts.forEach((i,index)=>{assert.equal(dbCurator.intents[index],i);assert.equal(dbCurator.proofs.get(i),proofs[index]);});
}
''')

    def test_late_more_denial_keeps_newer_selection_context_request_and_output(self):
        for status in [401, 403, 404]:
            for changed in ['selection', 'context']:
                with self.subTest(status=status, changed=changed):
                    self.javascript('const status=' + str(status) + ',changed=' + repr(changed) + ';\n' + r'''
{
await boot();await curatorContext();objectsRoute=async()=>({items:[curatorObject(),curatorObject(1)],next_cursor:'page2'});
await $('curator-load').onclick();change('object','0');await curatorSubmit();
const wait=deferred();objectsRoute=async()=>wait.promise;const more=$('curator-more').onclick();await tick();
if(changed==='context'){
 await curatorContext('data_engineer');objectsRoute=async()=>({items:[curatorObject(),curatorObject(1)],next_cursor:'newer-page'});await $('curator-load').onclick();
}
change('object','1');route=async()=>wrap(curatorPending({id:crypto.randomUUID()}));await $('curator-create').onclick();
const context=dbCurator.context,source=dbCurator.source,record=dbCurator.record,objects=dbCurator.objects,cursor=dbCurator.cursor,auth=dbCurator.inventoryAuth,receipt=storage.get(dbCuratorKey),held=dbCuratorHeld().slice(),text=visibleText($('curator-output'));
assert.equal(record.state,'RUNNING');assert.equal(held.length,1);
wait.resolve({http:status,body:{code:status===404?'NOT_FOUND':'DB_ACCESS_REVOKED'}});await more;
assert.equal(dbCurator.context,context);assert.equal(dbCurator.source,source);assert.equal(dbCurator.record,record);assert.equal(dbCurator.objects,objects);assert.equal(dbCurator.cursor,cursor);assert.equal(dbCurator.inventoryAuth,auth);assert.equal(dbCuratorAllowed(),true);
assert.equal(visibleText($('curator-output')),text);assert.equal(storage.get(dbCuratorKey),receipt);assert.deepEqual(dbCuratorHeld(),held);assert.equal($('curator-object').value,'1');
}
''')

    def test_late_inventory_and_object_roundtrip_responses_are_discarded(self):
        self.javascript(r'''
await curatorReady();const wait=deferred();objectsRoute=async()=>wait.promise;const load=dbCuratorLoad();await tick();change('schema','raw');change('schema','schema');wait.resolve({items:[curatorObject(9)],next_cursor:'late'});await load;assert.equal(dbCurator.objects.length,0);assert.equal(dbCurator.cursor,null);
const pending=deferred();inventoryRoute=async()=>pending.promise;const refresh=dbCuratorRefresh();await tick();change('role','bi_viewer');pending.resolve({items:inventory});await refresh;assert.equal(dbCurator.context,null);assert.equal(dbCurator.databases.length,0);
''')

    def test_exact_payload_key_and_durable_original_before_post(self):
        self.javascript(r'''
await curatorReady();const source=dbCurator.source;route=async(path,options)=>{const body=JSON.parse(options.body),saved=JSON.parse(storage.get(dbCuratorKey)).at(-1);assert.equal(path,dbCuratorEndpoint);assert.deepEqual(Object.keys(body).sort(),['context','language','relation']);assert.deepEqual(body,{context:dbCurator.context,relation:source.ref,language});assert.deepEqual(body,saved.body);assert.equal(options.headers['Idempotency-Key'],saved.key);assert.equal(options.headers['X-Shirokuma-Role'],'admin');return wrap(curatorRecord());};await dbCuratorSubmit();assert.equal(curatorPosts().length,1);assert.ok(Object.isFrozen(dbCurator.intents[0].body));
''')

    def test_storage_failure_prevents_post_and_hold(self):
        self.javascript(r'''
await curatorReady();const original=localStorage.setItem;localStorage.setItem=(key,value)=>{if(key===dbCuratorKey)throw Error('secret');original(key,value);};await dbCuratorSubmit();assert.equal(curatorPosts().length,0);assert.equal(dbCuratorHeld().length,0);assert.doesNotMatch(visibleText($('curator-output')),/secret/);
''')

    def test_safe_literal_names_types_normalized_partial_proposal_and_bilingual(self):
        self.javascript(r'''
await curatorReady();await curatorSubmit();const r=dbCurator.record;assert.deepEqual(r.proposal.columns.map(c=>c.name),['合計','詳細']);
for(const locale of ['ja','en']){setLanguage(locale);const node=$('curator-output'),text=visibleText(node);assert.equal(node.children[0].textContent,r.proposal.summary);assert.ok(text.includes(r.metadata.relation.table));assert.ok(text.includes('decimal(12,2)'));assert.ok(text.includes('{"type":"list","element":"string"}'));assert.ok(text.includes(t('Proposed columns: {count} (partial, maximum 8)',{count:2})));assert.ok(text.includes('local-model'));assert.ok(text.includes(r.metadata.captured_at));assert.ok(text.includes('sales.marts · admin · LOCAL'));assert.ok(text.includes(t('Curator current frozen source')));assert.equal(node.querySelector('img'),undefined);assert.doesNotMatch(text,/object_id|binding_revision/);assert.equal($('curator-create').textContent,t('Explain and draft descriptions'));}
''')

    def test_strict_record_and_proposal_rejections(self):
        self.javascript(r'''
await curatorReady();const good=curatorRecord();const invalid=[
{...good,kind:'sql_proposal'},{...good,actor:'other'},{...good,sql:'secret'},{...good,error_code:'provider raw secret'},{...good,cancel_requested:true},
{...good,input:{...good.input,extra:true}},{...good,context:{...good.context,grant_revision:99}},{...good,model:{...good.model,url:'secret'}},{...good,metadata:{...good.metadata,relation:{...good.metadata.relation,definition:'SQL'}}},
...[{summary:''},{summary:'x'.repeat(4097)},{description:'\u0000'},{summary:'\ud800'},{columns:[{name:'unknown',description:'x'}]},{columns:[{name:'合計',description:'x'},{name:'合計',description:'y'}]},{columns:[{name:'合計',description:'x'.repeat(1025)}]},{columns:[{name:'合計',description:'x',reason:'secret'}]},{extra:true}].map(proposal=>({...good,proposal:{...good.proposal,...proposal}}))];
for(const r of invalid)assert.throws(()=>dbCuratorRecord(wrap(r),dbCurator.context));
for(const count of [0,1,2]){const r={...good,proposal:{...good.proposal,columns:good.proposal.columns.slice(0,count)}};assert.equal(dbCuratorRecord(wrap(r),dbCurator.context).proposal.columns.length,count);}
''')

    def test_current_response_must_match_original_name_kind_and_reference(self):
        self.javascript(r'''
await curatorReady();route=async()=>{const r=curatorRecord();r.metadata.relation.table='replacement';return wrap(r);};await dbCuratorSubmit();assert.equal(dbCurator.record,null);assert.equal(dbCuratorHeld().length,1);
const i=dbCurator.intents[0];for(const field of ['schema_id','object_id','generation_id']){const r=curatorRecord();r.input.relation[field]='changed';assert.throws(()=>dbCuratorRecord(wrap(r),i.body.context,i));}
''')

    def test_pending_object_roundtrip_and_late_error_do_not_overwrite_newer_request(self):
        self.javascript(r'''
await curatorReady();const wait=deferred();route=async()=>wait.promise;const submit=dbCuratorSubmit();await tick();const accepted=curatorPending();change('object','1');change('object','0');wait.resolve(wrap(accepted));await submit;assert.equal(dbCurator.record,null);assert.equal(dbCurator.intents[0].id,accepted.id);assert.equal(dbCuratorHeld().length,1);
const stale=deferred();route=async()=>stale.promise;const recovery=dbCuratorSubmit('recover');await tick();change('object','1');route=async()=>wrap(accepted);await dbCuratorSubmit('recover');const text=visibleText($('curator-output'));stale.reject(Error('old secret'));await recovery;assert.equal(visibleText($('curator-output')),text);assert.equal(dbCurator.record.id,accepted.id);
''')

    def test_authority_context_role_and_navigation_hide_late_success_retain_receipt(self):
        self.javascript(r'''
await curatorReady();const wait=deferred();route=async()=>wait.promise;const submit=dbCuratorSubmit();await tick();const r=curatorRecord();++dbAuthorizationVersion;dbCuratorSync();wait.resolve(wrap(r));await submit;assert.equal(dbCurator.record,null);assert.equal(dbCurator.context,null);assert.equal(dbCurator.intents.length,1);assert.doesNotMatch(visibleText($('curator-output')),/仮の説明/);
await curatorContext();assert.equal(dbCuratorHeld().length,1);change('role','bi_viewer');assert.equal(dbCuratorHeld().length,0);await curatorContext();assert.equal(dbCuratorHeld().length,1);
objects=[curatorObject()];await dbCuratorLoad();change('object','0');route=async()=>wrap(r);await dbCuratorSubmit('recover');assert.ok(dbCurator.record);activeTab='bears';dbCuratorSync();assert.equal(dbCurator.record,null);assert.equal(dbCurator.intents.length,1);
''')

    def test_original_accepted_get_after_changed_object_reload_no_automatic_post(self):
        self.javascript(r'''
await curatorReady();await curatorSubmit(curatorPending());const original=structuredClone(curatorPosts()[0]),i=dbCurator.intents[0];change('object','1');dbCuratorInstall();await curatorContext();const count=curatorCalls().length;await dbCuratorSubmit('poll');dbCuratorSync();assert.equal(curatorCalls().length,count);assert.equal(dbCuratorHeld().length,1);
route=async(path,options)=>{assert.equal(options.method,undefined);assert.ok(path.startsWith(dbCuratorEndpoint+'/'+i.id+'?'));assert.equal(new URL(path,'http://local').searchParams.get('context'),JSON.stringify(i.body.context));return wrap(curatorRecord({},i));};await dbCuratorSubmit('recover');assert.equal(curatorPosts().length,1);assert.equal(curatorPosts()[0].options.body,original.options.body);assert.equal(dbCuratorHeld().length,0);assert.equal(dbCurator.source,null);assert.ok(visibleText($('curator-output')).includes(t('Curator historical frozen source; current selection kept')));
''')

    def test_unknown_ack_exact_explicit_original_replay_after_reload(self):
        self.javascript(r'''
await curatorReady();route=async()=>{throw Error('lost secret');};await dbCuratorSubmit();const original=structuredClone(curatorPosts()[0]);change('object','1');dbCuratorInstall();await curatorContext();assert.equal(curatorPosts().length,1);
route=async(path,options)=>{assert.equal(path,dbCuratorEndpoint);assert.equal(options.body,original.options.body);assert.equal(options.headers['Idempotency-Key'],original.options.headers['Idempotency-Key']);return wrap(curatorPending({state:'UNKNOWN',inference_state:'UNKNOWN'}));};await dbCuratorSubmit('recover');assert.equal(curatorPosts().length,2);assert.equal(dbCuratorHeld().length,1);await dbCuratorSubmit();assert.equal(curatorPosts().length,2);assert.doesNotMatch(visibleText($('curator-output')),/secret/);
''')

    def test_history_frozen_result_keeps_picker_and_all_old_originals(self):
        self.javascript(r'''
await curatorReady();route=async()=>{throw Error('lost');};await dbCuratorSubmit();const base=structuredClone(dbCurator.intents[0]);
const intents=Array.from({length:51},(_,index)=>({...structuredClone(base),key:'30000000-0000-4000-8000-'+String(index).padStart(12,'0'),id:'40000000-0000-4000-8000-'+String(index).padStart(12,'0')}));storage.set(dbCuratorKey,JSON.stringify(intents));dbCuratorInstall();await curatorContext();objects=[curatorObject(),curatorObject(1)];await dbCuratorLoad();change('object','1');const source=dbCurator.source;
const records=intents.slice(1).map(i=>curatorRecord({id:i.id},i));route=async()=>({context:dbCurator.context,explanations:records});await dbCuratorHistory();assert.equal(dbCuratorHeld().length,1);assert.equal(dbCuratorHeld()[0].id,intents[0].id);
route=async()=>wrap(records[0]);await dbCuratorHistory(0);assert.equal(dbCurator.source,source);assert.equal($('curator-object').value,'1');assert.ok(visibleText($('curator-output')).includes(t('Curator historical frozen source; current selection kept')));
route=async(path,options)=>{assert.ok(path.includes(intents[0].id));assert.equal(options.method,undefined);return wrap(curatorRecord({id:intents[0].id},intents[0]));};await dbCuratorSubmit('recover');assert.equal(dbCuratorHeld().length,0);assert.equal(curatorPosts().length,1);
''')

    def test_history_detail_preserves_list_updates_record_and_selects_by_stable_id(self):
        self.javascript(r'''
{
await curatorReady();const first=curatorRecord(),second=curatorRecord({id:crypto.randomUUID()});
change('object','1');const source=dbCurator.source,ref=source.ref,stored=storage.get(dbCuratorKey);
route=async()=>({context:dbCurator.context,explanations:[first,second]});await $('curator-history').onclick();
const validated=dbCurator.history,untouched=validated[1],detail={...first,updated_at:'2026-11-01T02:00:00Z',proposal:{...first.proposal,summary:'Checked historical detail'}};
const wait=deferred();route=async(path,options)=>{assert.ok(path.startsWith(dbCuratorEndpoint+'/'+first.id+'?'));assert.equal(options.method,undefined);return wait.promise;};
$('curator-saved').value='0';const loading=$('curator-saved').onchange();await tick();assert.equal(dbCurator.history,validated);assert.equal(dbCurator.history.length,2);
wait.resolve(wrap(detail));await loading;
assert.equal(dbCurator.history.length,2);assert.deepEqual(dbCurator.history.map(r=>r.id),[first.id,second.id]);assert.equal(dbCurator.history[0],dbCurator.record);assert.equal(dbCurator.history[1],untouched);assert.equal(dbCurator.record.proposal.summary,'Checked historical detail');assert.equal(dbCurator.record.updated_at,detail.updated_at);assert.equal($('curator-saved').value,'0');
assert.equal(dbCurator.source,source);assert.equal(dbCurator.source.ref,ref);assert.ok(visibleText($('curator-output')).includes(t('Curator historical frozen source; current selection kept')));
const assertPicker=value=>{dbCuratorSync();assert.equal($('curator-saved').value,value);assert.doesNotMatch(visibleText($('curator-saved')),/Unavailable|利用不可/);assert.equal($('curator-saved').options.length,dbCurator.history.length+1);};
dbCurator.history=[untouched,dbCurator.record];assertPicker('1');
dbCurator.history=[untouched];assertPicker('');assert.equal(dbCurator.record.id,first.id);
dbCurator.history=[];assertPicker('');
dbCurator.history=[untouched,dbCurator.record];assertPicker('1');
route=async()=>wrap(second);$('curator-saved').value='0';await $('curator-saved').onchange();assert.equal(dbCurator.record.id,second.id);assertPicker('0');
route=async()=>({context:dbCurator.context,explanations:[first,second]});await $('curator-history').onclick();assert.equal(dbCurator.record,null);assertPicker('');assert.equal(dbCurator.history.length,2);
change('object','0');assert.deepEqual(dbCurator.history,[]);assertPicker('');
assert.equal(curatorPosts().length,0);assert.equal(storage.get(dbCuratorKey),stored);
}
''')

    def test_cancel_only_own_record_and_pending_model_remains_held(self):
        self.javascript(r'''
await curatorReady();await curatorSubmit(curatorPending());const holds=dbExplanation.intents.slice(),other=dbProposal.intents.slice();
route=async(path,options)=>{assert.ok(path.endsWith('/cancel'));assert.deepEqual(JSON.parse(options.body),{context:dbCurator.context});return wrap(curatorPending({state:'CANCELED',cancel_requested:true}));};await dbCuratorCancel();assert.equal(dbCuratorHeld().length,1);assert.equal($('curator-create').disabled,true);assert.deepEqual(dbExplanation.intents,holds);assert.deepEqual(dbProposal.intents,other);
route=async()=>wrap(curatorPending({state:'CANCELED',inference_state:'CANCELED',cancel_requested:true}));await dbCuratorSubmit('recover');assert.equal(dbCuratorHeld().length,0);
dbCurator.intents=[];dbCurator.record=dataopsFreeze(curatorPending());const count=curatorCalls().length;await dbCuratorCancel();assert.equal(curatorCalls().length,count);
''')

    def test_ambiguous_recovery_errors_and_persisted_terminal_hints_stay_held(self):
        self.javascript(r'''
await curatorReady();route=async()=>{throw Error('ambiguous transport');};await dbCuratorSubmit();assert.equal(dbCuratorHeld().length,1);
for(const status of [400,401,403,404,409,500,502]){route=async()=>({http:status,body:{error:{code:status===409?'AI_METADATA_CHANGED':'AI_INPUT_INVALID'},accepted:true,id:'hidden'}});await dbCuratorSubmit('recover');if(!dbCurator.context)await curatorContext();assert.equal(dbCuratorHeld().length,1);}
const saved=JSON.parse(storage.get(dbCuratorKey));saved[0].terminal=true;storage.set(dbCuratorKey,JSON.stringify(saved));dbCuratorInstall();await curatorContext();assert.equal(dbCurator.intents.length,1);assert.equal(dbCuratorHeld().length,1);assert.equal(curatorCalls().filter(c=>c.options?.method!=='POST').length,0);
''')

    def test_terminal_proof_is_memory_only_and_bound_to_authority(self):
        self.javascript(r'''
await curatorReady();await curatorSubmit();assert.equal(dbCuratorHeld().length,0);change('object','1');assert.equal(dbCuratorHeld().length,0);dbCuratorInstall();await curatorContext();assert.equal(dbCuratorHeld().length,1);route=async()=>wrap(curatorRecord());await dbCuratorSubmit('recover');assert.equal(dbCuratorHeld().length,0);++dbAuthorizationVersion;dbCuratorSync();await curatorContext();assert.equal(dbCuratorHeld().length,1);
''')

    def test_no_sql_draft_result_wap_or_legacy_writes(self):
        self.javascript(r'''
for(const id of ['bear-sre-run','bear-data-run','bear-policy-run','bear-catalog-action','bear-catalog-table']){const n=document.createElement('button');n.id=id;$('bears').append(n);}
await curatorReady();editSQL('SELECT worksheet keep');editDoctor('SELECT doctor keep');$('copilot-request').value='keep request';$('copilot-request').dispatchEvent(new Event('input'));queryResult={rows:[[7]],columns:['keep']};const result=queryResult,before=JSON.stringify(dbInput(dbCurrent())),explorer=JSON.stringify(dbExplorer),history=JSON.stringify(dbHistory),wap=[...dataopsPanels].map(([key,p])=>[key,p.context,p.model,p.sources,p.op]);
await curatorSubmit();assert.equal(queryResult,result);assert.equal($('sql').value,'SELECT worksheet keep');assert.equal($('bear-query-sql').value,'SELECT doctor keep');assert.equal($('copilot-request').value,'keep request');assert.equal(JSON.stringify(dbInput(dbCurrent())),before);assert.equal(JSON.stringify(dbExplorer),explorer);assert.equal(JSON.stringify(dbHistory),history);assert.deepEqual([...dataopsPanels].map(([key,p])=>[key,p.context,p.model,p.sources,p.op]),wap);assert.equal(calls.filter(c=>c.options?.method==='POST'&&!c.path.startsWith(dbCuratorEndpoint)).length,0);assert.equal(dbExplanation.record,null);assert.equal(dbProposal.record,null);
for(const id of ['bear-sre-run','bear-data-run','bear-policy-run','bear-catalog-action','bear-catalog-table','ai-settings-button'])assert.equal($(id).disabled,true,id);assert.equal($('bear-tab-catalog').disabled,false);
''')

    def test_old_finally_cannot_clear_newer_busy_recovery(self):
        self.javascript(r'''
await curatorReady();const older=deferred();route=async()=>older.promise;const first=dbCuratorSubmit();await tick();change('object','1');const newer=deferred();route=async()=>newer.promise;const second=dbCuratorSubmit('recover');await tick();const token=dbCurator.busy;assert.ok(token);older.reject(Error('stale secret'));await first;assert.equal(dbCurator.busy,token);assert.equal(dbCurator.record,null);newer.resolve(wrap(curatorPending()));await second;assert.equal(dbCurator.busy,null);assert.equal(dbCuratorHeld().length,1);
''')

    def test_full9_and_actor_fence_original_recovery(self):
        self.javascript(r'''
await curatorReady();await curatorSubmit(curatorPending());const original=dbCurator.intents[0];change('schema','raw');await dbCuratorRefresh();assert.equal(dbCuratorHeld().length,0);const count=curatorCalls().length;await dbCuratorSubmit('recover');assert.equal(curatorCalls().length,count);change('schema','schema');await dbCuratorRefresh();assert.equal(dbCuratorHeld().length,1);
inventory[0].grant_revision++;await dbCuratorRefresh();assert.equal(dbCuratorHeld().length,0);inventory[0].grant_revision--;await dbCuratorRefresh();assert.equal(dbCuratorHeld().length,1);
dbPrincipal=JSON.stringify(['someone-else',true]);dbCuratorSync();assert.equal(dbCuratorHeld().length,0);assert.equal(dbCurator.intents[0],original);assert.equal(dbCurator.record,null);
''')

    def test_maximum_eight_partial_columns_and_safe_unicode(self):
        self.javascript(r'''
await curatorReady();const good=curatorRecord(),columns=Array.from({length:9},(_,i)=>({name:'列'+i,type:'string'}));good.metadata.relation.columns=columns;good.proposal.summary='推測 😀';good.proposal.columns=columns.slice(0,8).reverse().map(c=>({name:c.name,description:'案 😀'}));assert.equal(dbCuratorRecord(wrap(good),dbCurator.context).proposal.columns.length,8);good.proposal.columns.push({name:'列8',description:'案'});assert.throws(()=>dbCuratorRecord(wrap(good),dbCurator.context));
''')

    def test_malformed_or_wrong_history_never_displays_or_closes_original(self):
        self.javascript(r'''
await curatorReady();await curatorSubmit(curatorPending());const bad=curatorRecord();bad.metadata.relation.table='not original';route=async()=>({context:dbCurator.context,explanations:[bad]});await dbCuratorHistory();assert.equal(dbCuratorHeld().length,1);route=async()=>wrap({...bad,id:'50000000-0000-4000-8000-000000000000'});await dbCuratorHistory(0);assert.equal(dbCurator.record,null);assert.equal(dbCuratorHeld().length,1);assert.doesNotMatch(visibleText($('curator-output')),/not original|仮の説明/);
''')

    def test_first_boot_genuinely_absent_panel_and_repeat_install(self):
        self.javascript(r'''
assert.equal($('curator-panel'),null);
await boot();const parent=$('bear-panel-catalog'),panel=$('curator-panel');
assert.ok(panel);assert.equal(panel.parentElement,parent);assert.equal(panel.dataset.installed,'true');
for(const id of ['curator-role','curator-object','curator-create','curator-recover','curator-output'])assert.ok(panel.descendants().includes($(id)));
assert.equal($('curator-output')['aria-live'],'polite');
dbCuratorInstall();dbCuratorInstall();
assert.equal($('curator-panel'),panel);assert.equal(parent.children.filter(n=>n.id==='curator-panel').length,1);
''', strict_dom=True)

    def test_null_grant_full9_create_recovery_and_storage_compatibility(self):
        self.javascript(r'''
await boot();inventory[0].grant_revision=null;await curatorContext();objects=[curatorObject()];await dbCuratorLoad();change('object','0');
assert.equal(dbCurator.context.grant_revision,null);assert.equal(dbCuratorContext(dbCurator.context),true);
for(const value of [0,-1,'4',undefined,1.5])assert.equal(dbCuratorContext({...dbCurator.context,grant_revision:value}),false);
route=async()=>wrap(curatorPending());await dbCuratorSubmit();const body=curatorPosts()[0].options.body;
dbCuratorInstall();await curatorContext();assert.equal(dbCurator.intents.length,1);assert.equal(dbCuratorHeld().length,1);
route=async()=>wrap(curatorRecord());await dbCuratorSubmit('recover');assert.equal(dbCuratorHeld().length,0);assert.equal(dbCurator.intents[0].body.context.grant_revision,null);assert.equal(JSON.stringify(dbCurator.intents[0].body),body);
''')

    def test_exact_relation_metadata_utf8_limit_excludes_wrapper(self):
        self.javascript(r'''
await curatorReady();const r=curatorRecord(),relation=r.metadata.relation;
relation.columns=[{name:'列',type:'😀'}];r.proposal.columns=[];
const bytes=v=>new TextEncoder().encode(JSON.stringify(v)).length;
relation.columns[0].type='😀'.repeat(Math.floor((49152-bytes(relation)+4)/4));
relation.columns[0].type+='x'.repeat(49152-bytes(relation));
assert.equal(bytes(relation),49152);assert.ok(bytes(r.metadata)>49152);
assert.equal(dbCuratorRecord(wrap(r),dbCurator.context).metadata.relation.columns[0].type,relation.columns[0].type);
relation.columns[0].type+='x';assert.equal(bytes(relation),49153);assert.throws(()=>dbCuratorRecord(wrap(r),dbCurator.context));
''')

    def test_terminal_original_allows_explicit_new_on_different_object(self):
        self.javascript(r'''
await curatorReady();
for(const state of ['SUCCEEDED','FAILED','CANCELED']){
 route=async()=>wrap(state==='SUCCEEDED'?curatorRecord({id:crypto.randomUUID()}):curatorPending({id:crypto.randomUUID(),state,inference_state:state,error_code:state==='FAILED'?'AI_OUTPUT_INVALID':null,cancel_requested:state==='CANCELED'}));
 await dbCuratorSubmit();assert.equal(dbCuratorHeld().length,0);
 change('object',dbCurator.source.kind==='TABLE'?'1':'0');assert.equal(dbCuratorHeld().length,0);assert.equal($('curator-create').disabled,false);
}
route=async()=>wrap(curatorPending({id:crypto.randomUUID()}));await dbCuratorSubmit();const count=curatorPosts().length;change('object','1');await dbCuratorSubmit();assert.equal(curatorPosts().length,count);assert.equal(dbCuratorHeld().length,1);
dbCuratorInstall();await curatorContext();assert.ok(dbCuratorHeld().length>0);await dbCuratorSubmit();assert.equal(curatorPosts().length,count);
''')

    def test_fresh_exact_rejection_tuples_preserve_receipts_and_reload_hold(self):
        self.javascript(r'''
await curatorReady();
for(const [status,code] of [[400,'AI_INPUT_INVALID'],[409,'AI_METADATA_CHANGED']]){
 for(const envelope of [{code,message:'secret diagnostic',details:{},retryable:false},{code,message:'secret diagnostic',request_id:'secret correlation',details:{},retryable:false}]){
  route=async()=>({http:status,body:envelope});await dbCuratorSubmit();
  const i=dbCurator.intents.at(-1),saved=JSON.parse(storage.get(dbCuratorKey)).at(-1);
  assert.doesNotMatch(visibleText($('curator-output')),/secret diagnostic|secret correlation/);assert.doesNotMatch(storage.get(dbCuratorKey),/secret diagnostic|secret correlation|rejection/);assert.equal(i.id,null);assert.equal(dbCuratorHeld().length,0);assert.deepEqual(saved.body,i.body);assert.equal(saved.key,i.key);
  assert.ok(visibleText($('curator-output')).includes(t('Curator request rejected before acceptance. Review the source and refresh metadata before creating a new request.')));
  change('object',$('curator-object').value==='0'?'1':'0');assert.equal($('curator-create').disabled,false);
 }
}
const count=curatorPosts().length,original=curatorPosts()[0];dbCuratorInstall();await curatorContext();assert.equal(dbCuratorHeld().length,4);await dbCuratorSubmit();assert.equal(curatorPosts().length,count);
route=async(path,options)=>{assert.equal(options.body,original.options.body);assert.equal(options.headers['Idempotency-Key'],original.options.headers['Idempotency-Key']);return {http:400,body:{code:'AI_INPUT_INVALID',message:'',details:{},retryable:false}};};
await dbCuratorSubmit('recover');assert.equal(dbCuratorHeld().length,4);assert.equal(curatorPosts().length,count+1);
''')

    def test_nonqualifying_rejections_never_prove_first_admission(self):
        self.javascript(r'''
await curatorReady();const responses=[
[400,{}],[400,{message:'AI_INPUT_INVALID'}],[400,{error:'AI_INPUT_INVALID'}],[400,{code:'OTHER'}],[400,{code:'AI_INPUT_INVALID',error:{}}],[400,{code:'AI_INPUT_INVALID',error:{code:7}}],[400,{code:7,error:{code:'AI_INPUT_INVALID'}}],
[409,{code:'AI_INPUT_INVALID',message:'',details:{},retryable:false}],[400,{code:'AI_METADATA_CHANGED',message:'',details:{},retryable:false}],
...[400,401,403,404,500,502,409].map(status=>[status,{code:status===409?'IDEMPOTENCY_CONFLICT':status===400?'OTHER':'AI_INPUT_INVALID',message:'',details:{},retryable:false}]),
...[{code:'AI_INPUT_INVALID',error:{code:'AI_METADATA_CHANGED'}},
...['id','explanation_id','request_id','query_id','accepted','state','kind','explanation'].map(k=>({code:'AI_INPUT_INVALID',[k]:k==='explanation'?{id:'accepted'}:'accepted'})),
...['id','explanation_id','request_id','query_id','accepted','state','kind'].map(k=>({error:{code:'AI_INPUT_INVALID',[k]:'accepted'}})),
{code:'AI_INPUT_INVALID',explanation:{code:'AI_INPUT_INVALID',id:'accepted'}}].map(body=>[400,body])];
for(const [status,code] of [[400,'AI_INPUT_INVALID'],[409,'AI_METADATA_CHANGED']]){
 const native={code,message:'secret diagnostic',details:{},retryable:false},canonical={...native,request_id:'secret correlation'};
 responses.push(...[{code},{error:{code}},{code,error:{code}}].map(body=>[status,body]));
 for(const wire of [native,canonical]){
  for(const field of Object.keys(wire)){const missing={...wire};delete missing[field];if(field!=='request_id')responses.push([status,missing]);}
  for(const change of [{code:7},{message:null},{message:{}},{details:null},{details:[]},{details:'secret'},{details:{accepted:false}},{retryable:true},{retryable:0},{request_id:''},{request_id:' '},{request_id:null},{request_id:7},{request_id:{}},
   ...['id','explanation_id','query_id','accepted','state','kind','explanation','error','extra'].map(k=>({[k]:k==='explanation'?{id:'accepted'}:'accepted'}))])responses.push([status,{...wire,...change}]);
 }
}
for(const [http,body] of responses){
 dbCuratorInstall();await curatorContext();objects=[curatorObject()];await dbCuratorLoad();change('object','0');
 // Each case is an independent browser fixture request; prior receipts stay in storage.
 dbCurator.intents=[];dbCuratorPersist();route=async()=>({http,body});await dbCuratorSubmit();if(!dbCurator.context)await curatorContext();assert.equal(dbCuratorHeld().length,1,JSON.stringify([http,body]));
}
''')

    def test_ambiguity_recovery_accepted_get_history_cancel_do_not_mint_rejection_proof(self):
        self.javascript(r'''
await curatorReady();route=async()=>{throw Error('timeout');};await dbCuratorSubmit();const i=dbCurator.intents[0],original=curatorPosts()[0];
route=async()=>({http:400,body:{code:'AI_INPUT_INVALID',message:'',details:{},retryable:false}});await dbCuratorSubmit('recover');assert.equal(dbCuratorHeld().length,1);assert.equal(curatorPosts()[1].options.body,original.options.body);assert.equal(curatorPosts()[1].options.headers['Idempotency-Key'],i.key);
await dbCuratorHistory();assert.equal(dbCuratorHeld().length,1);
route=async()=>wrap(curatorPending());await dbCuratorSubmit('recover');assert.ok(i.id);
route=async()=>({http:409,body:{code:'AI_METADATA_CHANGED',message:'',request_id:'correlation',details:{},retryable:false}});await dbCuratorSubmit('recover');assert.equal(dbCuratorHeld().length,1);await dbCuratorCancel();assert.equal(dbCuratorHeld().length,1);
''')

    def test_safe_http_errors_and_matching_create_recover_history_cancel_authority_loss(self):
        self.javascript(r'''
await curatorReady();route=async()=>({http:403,body:{code:'DB_ACCESS_REVOKED',message:'secret provider',url:'secret'}});
await assert.rejects(dbCuratorRequest(dbCuratorEndpoint,dbCurator.context),e=>e.message==='CURATOR_UNAVAILABLE'&&e.status===403&&e.code==='DB_ACCESS_REVOKED'&&!JSON.stringify(e).includes('secret'));
for(const action of ['create','recover','history','cancel']){
 dbCurator.intents=[];dbCuratorPersist();await curatorContext();objects=[curatorObject()];await dbCuratorLoad();change('object','0');await curatorSubmit(curatorPending());
 const receipt=storage.get(dbCuratorKey),original=dbCurator.intents[0],sql=$('sql').value,doctor=$('bear-query-sql').value,query={rows:[[4]]};queryResult=query;
 dbCurator.history=[dbCurator.record];
 if(action==='create'){dbCuratorObserve(curatorRecord({},original));}
 route=async()=>({http:action==='recover'?400:action==='history'?401:403,body:{error:{code:action==='recover'?'STALE_BINDING':'DB_ACCESS_REVOKED',message:'secret'}}});
 if(action==='history')await dbCuratorHistory();else if(action==='cancel')await dbCuratorCancel();else await dbCuratorSubmit(action);
 assert.equal(dbCurator.context,null,action);assert.equal(dbCurator.record,null,action);assert.deepEqual(dbCurator.history,[]);assert.equal(dbCurator.source,null);assert.deepEqual(dbCurator.objects,[]);
 assert.equal(dbCurator.intents[0],original);assert.equal(JSON.parse(storage.get(dbCuratorKey))[0].key,JSON.parse(receipt)[0].key);assert.equal(queryResult,query);assert.equal($('sql').value,sql);assert.equal($('bear-query-sql').value,doctor);assert.doesNotMatch(visibleText($('curator-output')),/secret|仮の説明/);
}
''')

    def test_stale_auth_and_rejection_errors_cannot_clear_newer_context_or_hold(self):
        self.javascript(r'''
await curatorReady();const wait=deferred();route=async()=>wait.promise;const older=dbCuratorSubmit();await tick();change('object','1');
route=async()=>wrap(curatorPending());await dbCuratorSubmit('recover');const activeRecord=dbCurator.record,context=dbCurator.context,source=dbCurator.source;
wait.resolve({http:403,body:{code:'DB_ACCESS_REVOKED'}});await older;assert.equal(dbCurator.record,activeRecord);assert.equal(dbCurator.context,context);assert.equal(dbCurator.source,source);assert.equal(dbCuratorHeld().length,1);
const later=deferred();route=async()=>later.promise;const recover=dbCuratorSubmit('recover');await tick();change('schema','raw');await dbCuratorRefresh();const newer=dbCurator.context;
later.resolve({http:403,body:{code:'STALE_BINDING'}});await recover;assert.equal(dbCurator.context,newer);assert.equal(newer.schema_id,'raw');
change('schema','schema');await dbCuratorRefresh();assert.equal(dbCuratorHeld().length,1);
dbCurator.intents=[];dbCuratorPersist();objects=[curatorObject(),curatorObject(1)];await dbCuratorLoad();change('object','0');
const rejection=deferred();route=async()=>rejection.promise;const fresh=dbCuratorSubmit();await tick();change('object','1');route=async()=>wrap(curatorPending());await dbCuratorSubmit('recover');const newest=dbCurator.record;
rejection.resolve({http:400,body:{code:'AI_INPUT_INVALID',message:'',details:{},retryable:false}});await fresh;assert.equal(dbCurator.record,newest);assert.equal(dbCuratorHeld().length,1);
''')

    def test_cancel_race_unchanged_succeeded_keeps_proposal_and_other_hold(self):
        self.javascript(r'''
await curatorReady();
for(const state of ['QUEUED','RUNNING']){
 dbCurator.intents=[];dbCuratorPersist();await curatorSubmit(curatorPending({state,inference_state:state==='QUEUED'?'NOT_STARTED':'RUNNING'}));
 const i=dbCurator.intents[0],other=dbCuratorFreeze({...structuredClone(i),key:crypto.randomUUID(),id:crypto.randomUUID()});dbCurator.intents.push(other);dbCuratorPersist();
 const succeeded=curatorRecord({},i);route=async()=>wrap(succeeded);await dbCuratorCancel();
 assert.equal(dbCurator.record.state,'SUCCEEDED');assert.equal(dbCurator.record.cancel_requested,false);assert.equal(dbCurator.record.proposal.summary,succeeded.proposal.summary);assert.deepEqual(dbCuratorHeld(),[other]);assert.equal($('curator-create').disabled,true);
}
''')

    def test_late_explorer_denial_synchronizes_curator_without_global_controls(self):
        self.javascript(r'''
await curatorReady();activeTab='bears';dbCuratorSync();await curatorSubmit();
const original=dbCurator.intents[0],receipt=storage.get(dbCuratorKey),sql=$('sql').value;
assert.ok(visibleText($('curator-output')).includes('仮の説明'));
dbExplorer.database_id='other-database';dbExplorer.schema_id='other-schema';
dbExplorerDenied({code:'DB_ACCESS_REVOKED',message:'Access revoked'});
assert.equal(dbCurator.context,null);assert.equal(dbCurator.record,null);assert.deepEqual(dbCurator.history,[]);assert.equal(dbCurator.source,null);
assert.equal(dbCurator.intents[0],original);assert.equal(storage.get(dbCuratorKey),receipt);assert.equal($('sql').value,sql);assert.doesNotMatch(visibleText($('curator-output')),/仮の説明|注文/);
''')

    def test_closed_durable_codes_and_reachable_state_validation(self):
        self.javascript(r'''
await curatorReady();for(const code of ['AI_METADATA_UNAVAILABLE','AI_METADATA_CHANGED','AI_INPUT_INVALID','AI_OUTPUT_INVALID','AI_PROVIDER_UNAVAILABLE','AI_PROVIDER_TIMEOUT','AI_PROVIDER_OUTCOME_UNKNOWN','AI_OWNER_LOST','DB_ACCESS_REVOKED','SQL_PERMISSION_DENIED','UNKNOWN_CONTEXT','STALE_BINDING','CONTEXT_MISMATCH']){
 const r=curatorPending({state:'FAILED',inference_state:'FAILED',error_code:code});assert.equal(dbCuratorRecord(wrap(r),dbCurator.context).error_code,code);
}
for(const extra of [{model:null},{metadata:null},{inference_state:'NOT_STARTED'},{error_code:'RAW_PROVIDER_SECRET'}])assert.throws(()=>dbCuratorRecord(wrap(curatorPending(extra)),dbCurator.context));
''')

    def test_fresh_rejection_proof_exact_identity_authority_and_late_new_hold(self):
        self.javascript(r'''
await curatorReady();route=async()=>({http:400,body:{code:'AI_INPUT_INVALID',message:'',request_id:'correlation',details:{},retryable:false}});await dbCuratorSubmit();const rejected=dbCurator.intents[0];
assert.equal(dbCuratorHeld().length,0);const key=rejected.key;rejected.key=crypto.randomUUID();assert.deepEqual(dbCuratorHeld(),[rejected]);rejected.key=key;
const id=rejected.id;rejected.id=crypto.randomUUID();assert.deepEqual(dbCuratorHeld(),[rejected]);rejected.id=id;
++dbAuthorizationVersion;dbCuratorSync();await curatorContext();assert.deepEqual(dbCuratorHeld(),[rejected]);
route=async()=>wrap(curatorRecord());await dbCuratorSubmit('recover');assert.equal(dbCuratorHeld().length,0);
objects=[curatorObject(),curatorObject(1)];await dbCuratorLoad();change('object','0');
const wait=deferred();route=async()=>wait.promise;const older=dbCuratorSubmit();await tick();change('object','1');
route=async()=>wrap(curatorRecord({id:crypto.randomUUID()}));await dbCuratorSubmit('recover');assert.equal(dbCuratorHeld().length,0);
route=async()=>wrap(curatorPending({id:crypto.randomUUID()}));await dbCuratorSubmit();const pending=dbCurator.intents.at(-1),activeRecord=dbCurator.record;
wait.resolve({http:400,body:{code:'AI_INPUT_INVALID',message:'',details:{},retryable:false}});await older;assert.deepEqual(dbCuratorHeld(),[pending]);assert.equal(dbCurator.record,activeRecord);
''')
