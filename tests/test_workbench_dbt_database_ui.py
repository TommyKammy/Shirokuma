"""Database dbt production handlers exercised through the existing Node UI harness."""
import ast
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import test_workbench_database_ui as harness

SETUP = r"""
const p=dbDbt;
const sandbox={id:'sandbox',database_id:'db',display_name:'Sandbox',sql_name:'sandbox',purpose:'SANDBOX',allowed_modes:['read-only','sandbox-write'],capabilities:{query:true,write:true}};
schemas.push(sandbox);
localStorage.removeItem=key=>storage.delete(key);
let plan=null,run=null,pubs={},receipts=new Map(),lose=false,wrong=null,gate=null,observing=null;
const posts=()=>calls.filter(c=>c.path.startsWith('/api/v1/dbt/')&&c.options?.method==='POST');
const readBody=c=>JSON.parse(c.options.body);
const ids=['model.demo.stage','model.demo.first','model.demo.second'];
function makePlan(context,body={}){return {id:'plan-1',kind:'plan',actor:dbDbtOwner(),context:structuredClone(context),state:'READY',compatibility:{version:1,executor:'sql_wap',status:'supported',issues:[]},project_id:'lake_marts',project_name:'Lake marts',source_digest:'bytes-digest',source_schema:'raw',target_schema:'schema',staging_materialization:'ephemeral',created_at:'2026-10-29T00:00:00Z',models:ids.map((id,i)=>({unique_id:id,name:['stage','first','second'][i],materialized:i?'table':'ephemeral',table_properties:i===1?{format:'PARQUET'}:{},relation:['sales','sandbox',['stage','first','second'][i]],depends_on:i?[ids[0]]:['source.demo.orders'],compiled_sql:i?'select * from orders':"select '<img src=x onerror=attack()>' from sales.raw.orders"})),tests:[{unique_id:'test.first',name:'not_null',test_type:'not_null',depends_on:[ids[1]]},{unique_id:'test.second',name:'unique',test_type:'unique',depends_on:[ids[2]]}],source_bindings:[{unique_id:'source.demo.orders',relation:['sales','raw','orders'],ref:{database_id:'db',schema_id:'raw',object_id:'orders',incarnation:1}}],...body};}
function makeOptions(a){return {version:1,plan_id:a.id,source_digest:a.source_digest,executor:'native_table',eligible:true,reason_code:null,nodes:a.models.map((m,i)=>({node_id:m.unique_id,eligible:i===1,reason_code:i===1?null:i===0?'DBT_NATIVE_TABLE_REQUIRED':'DBT_NATIVE_PARQUET_REQUIRED'}))};}
function makeRun(context,body={}){return {id:'run-1',kind:'run',actor:dbDbtOwner(),context:structuredClone(context),plan_id:plan.id,state:'READY',cancel_requested:false,created_at:'2026-10-29T00:01:00Z',models:plan.models.map((m,i)=>({unique_id:m.unique_id,name:m.name,materialized:m.materialized,table_properties:structuredClone(m.table_properties),state:i?'BUILT':'EXPANDED',wap_model_id:'wap-'+i,wap_version:2,build_id:i?'build-'+i:null,candidate:i?{ref:{database_id:'db',schema_id:'sandbox',object_id:'candidate-'+i,incarnation:1},snapshot:'snap-'+i}:null,operation_ids:[]})),tests:plan.tests.map(t=>({...t,state:'PASSED',failure_count:0,operation_id:'op-'+t.unique_id})),operations:[{id:'safe-op',state:'FINISHED'}],...body};}
route=async(path,options)=>{
 const url=new URL('http://local'+path),body=options?.body?JSON.parse(options.body):null,context=body?.context||JSON.parse(url.searchParams.get('context')||'null');
 if(!url.pathname.startsWith('/api/v1/dbt/'))return {};
 if(gate)await gate.promise;
 if(body){
  const key=options.headers['Idempotency-Key'];assert.ok(key);
  assert.equal(Object.keys(options.headers).some(name=>name.toLowerCase()==='x-idempotency-key'),false);
  assert.equal(Object.keys(context).length,9);
  let artifact=receipts.get(key);
  if(!artifact){
   if(url.pathname.endsWith('/plans')){plan=makePlan(context,{...body});delete plan.context.context;artifact={plan};}
   else if(url.pathname.endsWith('/runs')){run=makeRun(context,body.executor==='native_table'?{executor:'native_table',selected_node_id:body.node_id,state:'QUEUED',tests:[],tests_status:'NOT_RUN',publication_allowed:false}:{});artifact={run};}
   else if(url.pathname.endsWith('/cancel')){run={...run,state:'CANCELING',cancel_requested:true};artifact={run};}
   else if(url.pathname.endsWith('/reconcile')){artifact={run};}
   else if(url.pathname.endsWith('/publish')){const publication={id:'pub-'+body.node_id.split('.').at(-1),kind:'publication',actor:dbDbtOwner(),context,run_id:run.id,parent_run_id:run.id,plan_id:run.plan_id,node_id:body.node_id,state:'QUEUED',build_id:'build',audit_id:null,publication_id:null,operation_ids:[]};pubs[publication.id]=publication;artifact={publication};}
   else throw Error('Unexpected POST '+path);
   receipts.set(key,structuredClone(artifact));
  }
  if(lose)throw Error('ACK lost');return {context,...structuredClone(artifact),...wrong};
 }
 if(observing)await observing.promise;
 if(url.pathname.endsWith('/projects'))return {context,projects:[{id:'lake_marts',name:'Lake marts'},{id:'approved_second',name:'Other approved project'}]};
 if(url.pathname.endsWith('/plans'))return {context,items:plan?[structuredClone(plan)]:[]};
 if(url.pathname.endsWith('/runs'))return {context,items:run?[structuredClone(run)]:[]};
 if(url.pathname.includes('/plans/'))return {context,plan:structuredClone(plan),run_options:makeOptions(plan),...wrong};
 if(url.pathname.includes('/runs/'))return {context,run:structuredClone(run),...wrong};
 if(url.pathname.includes('/publications/'))return {context,publication:structuredClone(pubs[url.pathname.split('/').at(-1)]),...wrong};
 throw Error('Unexpected GET '+path);
};
async function openDbt(role='admin'){
 currentRole=role;session.maximum_role=role;
 await boot(Array.from({length:13},(_,i)=>({...input(i?'sheet-'+i:'a'),sql:'SELECT '+i,selected_role:role})));
 await dbDbtRefresh();assert.equal(dbDbtAllowed(),role!=='bi_viewer');
 if(role==='bi_viewer')return;
 for(const [name,value] of [['project','lake_marts'],['source','raw'],['target','schema'],['materialization','ephemeral']])dbDbtNode(name).value=value;
 dbDbtRender();
}
async function compile(){await openDbt();await dbDbtSubmit('compile');assert.equal(p.plan?.id,'plan-1');}
async function build(){await compile();await dbDbtSubmit('build');assert.equal(p.run?.id,'run-1');}
function text(node){return node.textContent+' '+node.children.map(text).join(' ');}
function review(node=ids[1]){p.review=node;dbDbtRender();assert.ok(dbDbtNode('publish'));}
"""


class DbtDatabaseUI(unittest.TestCase):
    def javascript(self, body):
        harness.DatabaseUI.javascript(self, SETUP + body)

    def test_native_saved_options_explicit_selection_payload_and_sql_default(self):
        self.javascript(r"""
await compile();const sqlBody={plan_id:plan.id};assert.equal(p.method,'sql_wap');
assert.equal(dbDbtNode('native-controls').hidden,true);
dbDbtNode('method').value='native_table';await dbDbtNode('method').onchange();
assert.equal(dbDbtNode('build').disabled,true);assert.match(dbDbtNode('native-reason').textContent,/Inspect the saved plan/);
await dbDbtSubmit('build');assert.equal(posts().length,1);
plan.compatibility.version=2;await dbDbtObserve('plan',plan.id);
assert.equal(p.node,'');assert.equal(dbDbtNode('build').disabled,true);
assert.equal(dbDbtNode('node').options.find(n=>n.value===ids[0]).disabled,true);
assert.match(dbDbtNode('node').options.find(n=>n.value===ids[2]).textContent,/PARQUET/);
assert.equal(dbDbtNode('node').options.find(n=>n.value===ids[1]).disabled,false);
dbDbtNode('node').value=ids[1];await dbDbtNode('node').onchange();
assert.equal(dbDbtNode('build').disabled,false);
// Compile draft controls cannot change the selected saved plan or node.
dbDbtNode('project').value='approved_second';dbDbtNode('materialization').value='table';
await dbDbtSubmit('build');const sent=readBody(posts().at(-1));
assert.deepEqual(Object.keys(sent).sort(),['context','executor','node_id','plan_id']);
assert.equal(sent.executor,'native_table');assert.equal(sent.node_id,ids[1]);assert.equal(sent.plan_id,plan.id);
assert.equal(p.run.executor,'native_table');assert.equal(p.run.tests_status,'NOT_RUN');assert.equal(dbDbtCanPublish(p.run.models[1]),false);
p.method='sql_wap';dbDbtRender();assert.match(text(dbDbtNode('run-detail')),/native_table/);assert.match(text(dbDbtNode('run-detail')),new RegExp(ids[1]));
await dbDbtObserve('plan',plan.id);dbDbtNode('method').value='sql_wap';await dbDbtNode('method').onchange();
await dbDbtSubmit('build');const oldBody=readBody(posts().at(-1));delete oldBody.context;assert.deepEqual(oldBody,sqlBody);
clearTimeout(p.timer);
""")

    def test_native_default_view_plan_has_no_compatible_model_and_changes_never_post(self):
        self.javascript(r"""
await compile();plan.compatibility.version=2;
plan.models=plan.models.map(m=>({...m,materialized:'view'}));
wrong={run_options:{...makeOptions(plan),nodes:ids.map(node_id=>({node_id,eligible:false,reason_code:'DBT_NATIVE_TABLE_REQUIRED'}))}};
await dbDbtObserve('plan',plan.id);const count=posts().length;
dbDbtNode('method').value='native_table';await dbDbtNode('method').onchange();
for(const lang of ['ja','en']){
 setLanguage(lang);dbDbtRender();
 assert.match(dbDbtNode('native-reason').textContent,lang==='ja'?/native 対応モデルがありません/:/No compatible native model/);
 assert.match(dbDbtNode('native-reason').textContent,/RAW/);assert.match(dbDbtNode('native-reason').textContent,/EPHEMERAL/);
 assert.equal(dbDbtNode('build').disabled,true);assert.equal(p.node,'');
 assert.equal(dbDbtNode('node').disabled,true);
 assert.ok(dbDbtNode('node').options.filter(o=>o.value).every(o=>o.disabled));
 assert.equal($('db-dbt-title').textContent,'Database dbt');
}
setLanguage('en');dbDbtNode('node').value=ids[0];await dbDbtNode('node').onchange();
assert.equal(posts().length,count);await dbDbtSubmit('build');assert.equal(posts().length,count);
assert.ok(plan.models.every(m=>m.materialized==='view'));
// Empty saved models, global incompatibility and an unselected eligible model have distinct guidance.
p.plan={...p.plan,models:[]};p.runOptions={...makeOptions(p.plan),nodes:[]};
assert.match(dbDbtNativeReason(''),/has no models/);
p.runOptions={...p.runOptions,eligible:false,reason_code:'DBT_PLAN_NOT_READY'};
assert.match(dbDbtNativeReason(''),/requires a READY/);
p.plan=plan;p.runOptions=makeOptions(plan);assert.match(dbDbtNativeReason(''),/Choose one/);
""")

    def test_prepare_different_run_only_after_definite_closure(self):
        self.javascript(r"""
await build();const count=posts().length;
const preparation=()=>dbDbtNode('run-detail').children.find(n=>n.tagName==='BUTTON'&&n.textContent===t('Inspect plan'));
for(const lang of ['ja','en']){
 setLanguage(lang);
 for(const state of ['QUEUED','COMPILING','BUILDING','TESTING','AUDITING','PUBLISHING','CANCELING','UNKNOWN','COMMITTED_RECORDING_FAILED']){
  p.run={...run,state};dbDbtRender();assert.equal(preparation(),undefined);
  assert.equal(text(dbDbtNode('run-detail')).includes(t('Inspect the saved plan again to prepare a different explicit run.')),false);
  assert.equal(dbDbtNode('observe').disabled,false);assert.equal(dbDbtNode('history-refresh').disabled,false);
 }
 for(const state of ['READY','NATIVE_VERIFIED','FAILED','CANCELED','PUBLISHED']){
  p.run={...run,state};dbDbtRender();assert.ok(preparation());assert.equal(preparation().disabled,false);
 }
}
p.run={...run,executor:'native_table',state:'UNKNOWN'};dbDbtRender();
assert.match(text(dbDbtNode('run-detail')),/Reconcile existing operations/);assert.equal(preparation(),undefined);
p.run={...run,state:'FAILED'};lose=true;await dbDbtSubmit('reconcile');assert.ok(p.intent);
assert.equal(preparation(),undefined);assert.equal(text(dbDbtNode('run-detail')).includes(t('Inspect the saved plan again to prepare a different explicit run.')),false);
assert.equal(posts().length,count+1);clearTimeout(p.timer);
""")

    def test_native_invalid_missing_and_stale_options_fail_closed(self):
        self.javascript(r"""
await compile();plan.compatibility.version=2;
for(const change of [undefined,null,{...makeOptions(plan),plan_id:'wrong'},{...makeOptions(plan),source_digest:'wrong'},
 {...makeOptions(plan),executor:'sql_wap'},{...makeOptions(plan),version:9},
 {...makeOptions(plan),nodes:[]},{...makeOptions(plan),nodes:[{node_id:ids[1],eligible:true,reason_code:null}]}]){
 wrong={run_options:change};await dbDbtObserve('plan',plan.id);p.method='native_table';p.node=ids[1];dbDbtRender();
 assert.equal(dbDbtNode('build').disabled,true);assert.equal(p.runOptions,null);await dbDbtSubmit('build');assert.equal(posts().length,1);
}
wrong=null;await dbDbtObserve('plan',plan.id);p.method='native_table';p.node=ids[1];
const hold=deferred();observing=hold;const reading=dbDbtObserve('plan',plan.id);await tick();
assert.equal(p.runOptions,null);assert.equal(dbDbtNode('build').disabled,true);
p.database='other';dbDbtPurge();hold.resolve();await reading;
assert.equal(p.plan,null);assert.equal(p.runOptions,null);assert.equal(p.node,'');
assert.equal(posts().length,1);
""")

    def test_native_ack_executor_node_and_original_frozen_recovery_after_reload(self):
        self.javascript(r"""
await compile();plan.compatibility.version=2;await dbDbtObserve('plan',plan.id);
p.method='native_table';p.node=ids[1];lose=true;await dbDbtSubmit('build');const original=p.intent,first=posts().at(-1);
assert.ok(original);assert.equal(dbDbtNode('method').disabled,true);assert.equal(dbDbtNode('node').disabled,true);
// A reload restores the original method/node/body/key without launching it.
p.intent=null;lose=false;await dbDbtRefresh();assert.deepEqual(p.intent,JSON.parse(JSON.stringify(original)));
assert.equal(dbDbtNode('method').value,'native_table');assert.equal(dbDbtNode('node').value,ids[1]);assert.equal(posts().length,2);
const saved=structuredClone(run);
for(const change of [{executor:undefined},{executor:'sql_wap'},{selected_node_id:undefined},{selected_node_id:ids[2]},{plan_id:'wrong'},{actor:'other'},{context:{...saved.context,grant_revision:99}}]){
 wrong={run:{...saved,...change}};await dbDbtRecover();assert.ok(p.intent);assert.equal(p.run,null);
 const last=posts().at(-1);assert.equal(last.options.body,first.options.body);assert.equal(last.options.headers['Idempotency-Key'],first.options.headers['Idempotency-Key']);
}
wrong=null;await dbDbtRecover();assert.equal(p.intent,null);assert.equal(p.run.executor,'native_table');
assert.equal(receipts.size,2);clearTimeout(p.timer);
""")

    def test_native_uncertain_intent_is_retained_but_node_hidden_from_viewer(self):
        self.javascript(r"""
await compile();plan.compatibility.version=2;await dbDbtObserve('plan',plan.id);
p.method='native_table';p.node=ids[1];lose=true;await dbDbtSubmit('build');const original=p.intent;
p.role='bi_viewer';dbDbtPurge();assert.equal(p.intent,original);assert.equal(p.runOptions,null);
assert.equal(dbDbtNode('native-controls').hidden,true);assert.equal(dbDbtNode('node').value,'');
assert.equal(dbDbtNode('node').options.some(o=>o.value===ids[1]),false);
assert.equal(dbDbtNode('sql').children.length,0);assert.equal(posts().length,2);
""")

    def test_sql_ack_cannot_masquerade_as_native_and_unknown_never_auto_retries(self):
        self.javascript(r"""
await compile();const base=route;route=async(path,options)=>{const reply=await base(path,options);if(options?.method==='POST'&&path.endsWith('/runs'))reply.run={...reply.run,executor:'native_table',selected_node_id:ids[1]};return reply;};
await dbDbtSubmit('build');assert.ok(p.intent);assert.equal(p.intent.body.executor,undefined);assert.equal(p.run,null);
const first=posts().at(-1);route=base;await dbDbtRecover();assert.equal(p.intent,null);assert.equal(posts().at(-1).options.body,first.options.body);
run={...run,executor:'native_table',selected_node_id:ids[1],state:'UNKNOWN',tests:[]};await dbDbtObserve('run',run.id);
const count=posts().length;dbDbtSchedule();assert.equal(p.timer,null);await dbDbtSubmit('build');assert.equal(posts().length,count);
await dbDbtHistory();assert.equal(posts().length,count);assert.match(text(dbDbtNode('history')),/native_table/);
""")

    def test_native_controls_localized_labels_reasons_role_plan_and_no_autoselect(self):
        self.javascript(r"""
await compile();plan.compatibility.version=2;await dbDbtObserve('plan',plan.id);p.method='native_table';dbDbtRender();
for(const lang of ['ja','en']){
 setLanguage(lang);dbDbtRender();assert.equal(dbDbtNode('build').textContent,t('Run native TABLE'));
 for(const id of ['method','node'])assert.ok(dbDbtNode(id).parentElement.children.some(n=>n.tagName==='LABEL'&&n.htmlFor===dbDbtNode(id).id));
 assert.equal(dbDbtNode('node').value,'');assert.equal(dbDbtNode('native-controls').hidden,false);
 assert.match(text(dbDbtNode('native-help')),/SANDBOX/);assert.match(text(dbDbtNode('native-help')),lang==='ja'?/テストは実行せず/:/Tests are not run/);
 for(const reason of Object.values(dbDbtNativeReasons))assert.notEqual(i18n.ja[reason],undefined);
 assert.match(dbDbtNode('native-reason').textContent,lang==='ja'?/1つ選択/:/Choose one/);
}
p.node=ids[1];plan={...plan,id:'new-plan'};await dbDbtObserve('plan',plan.id);assert.equal(p.method,'sql_wap');assert.equal(p.node,'');
p.role='bi_viewer';dbDbtPurge();assert.equal(p.runOptions,null);assert.equal(p.node,'');assert.equal(dbDbtNode('sql').children.length,0);
assert.equal(dbDbtNode('method').disabled,true);assert.equal(worksheets.length,13);
""")

    def test_mock_header_contract_matches_shared_backend_key_adapter(self):
        # Execute the actual adapter function without importing unrelated services.
        tree = ast.parse((ROOT / 'tools/local_experiment/dataops_routes.py').read_text())
        key_function = next(node for node in tree.body
                            if isinstance(node, ast.FunctionDef) and node.name == '_key')
        namespace = {}
        exec(compile(ast.Module(body=[key_function], type_ignores=[]),
                     'dataops_routes.py', 'exec'), namespace)
        headers = Message()
        headers['Idempotency-Key'] = 'original-receipt-key'
        self.assertEqual(namespace['_key'](SimpleNamespace(headers=headers)),
                         'original-receipt-key')
        legacy_headers = Message()
        legacy_headers['X-Idempotency-Key'] = 'original-receipt-key'
        with self.assertRaises(ValueError):
            namespace['_key'](SimpleNamespace(headers=legacy_headers))
        self.javascript(r"""
await compile();
const first=posts()[0],key=first.options.headers['Idempotency-Key'];
assert.ok(key);assert.equal(Object.hasOwn(first.options.headers,'X-Idempotency-Key'),false);
// The mock must reject a legacy-only request and a request with both headers.
for(const headers of [{'X-Idempotency-Key':key},{...first.options.headers,'X-Idempotency-Key':key}]){
 await assert.rejects(()=>route(first.path,{...first.options,headers}),{name:'AssertionError'});
}
assert.equal(posts().length,1);assert.equal(receipts.size,1);
""")

    def test_engineer_query_only_marts_target_default_and_explicit_selection(self):
        self.javascript(r"""
schemas[0].capabilities.write=false;schemas[0].purpose='MARTS';
await openDbt('data_engineer');
assert.equal(p.schemas.find(s=>s.id==='schema').capabilities.write,false);
assert.equal(p.schemas.find(s=>s.id==='sandbox').capabilities.write,true);
assert.equal(dbDbtNode('target').children.find(n=>n.value==='schema').disabled,false);
assert.equal(dbDbtNode('target').value,'schema');assert.equal(dbDbtNode('compile').disabled,false);
dbDbtNode('target').value='raw';dbDbtRender();dbDbtNode('target').value='schema';await dbDbtNode('target').onchange();
await dbDbtSubmit('compile');assert.equal(readBody(posts()[0]).target_schema,'schema');
assert.equal(readBody(posts()[0]).context.schema_id,'sandbox');assert.equal(dbDbtCanPublish(null),false);
""")

    def test_admin_own_publication_reconcile_refreshes_children_with_get_only(self):
        for state in ['PUBLISHED', 'COMMITTED_RECORDING_FAILED']:
            with self.subTest(state=state):
                self.javascript(r"""
inventory[0].grant_revision=1;await openDbt('data_engineer');await dbDbtSubmit('compile');await dbDbtSubmit('build');
currentRole='admin';session.maximum_role='admin';dbMaximumRole='admin';session.username='administrator';dbPrincipal=JSON.stringify(['administrator',true]);inventory[0].grant_revision=9;
p.role='admin';await dbDbtRefresh();await dbDbtObserve('run','run-1');review();await dbDbtSubmit('publish',ids[1]);
pubs['pub-first'].state='UNKNOWN';run.state='UNKNOWN';run.models[1].publication_id='pub-first';await dbDbtObserve('run','run-1');await dbDbtObserve('publication','pub-first');
assert.equal(dbDbtNode('reconcile').disabled,false);assert.equal(dbDbtNode('cancel').disabled,true);assert.equal(dbDbtNode('build').disabled,true);
const before=posts().length;await dbDbtSubmit('cancel');await dbDbtSubmit('build');assert.equal(posts().length,before);
const baseRoute=route;route=async(path,options)=>{if(options?.method==='POST'&&path.endsWith('/reconcile')){pubs['pub-first'].state='FINAL_STATE';run.state='READY';}return baseRoute(path,options);};
const mark=calls.length;await dbDbtNode('reconcile').onclick();
assert.equal(p.intent,null);assert.equal(p.run.state,'READY');assert.equal(p.publications[ids[1]].state,'FINAL_STATE');
const tail=calls.slice(mark);assert.equal(tail.filter(c=>c.options?.method==='POST').length,1);assert.match(tail[0].path,/reconcile$/);
assert.ok(tail.some(c=>c.path.includes('/publications/pub-first?')&&!c.options.method));
assert.equal(readBody(posts().at(-1)).context.grant_revision,9);assert.equal(p.run.context.grant_revision,1);
const count=posts().length;const observe=dbDbtNode('publications').descendants().find(n=>n.tagName==='BUTTON'&&n.textContent==='Observe saved state');
assert.ok(observe);await observe.onclick();assert.equal(posts().length,count);assert.equal(p.publications[ids[1]].state,'FINAL_STATE');
""".replace('FINAL_STATE', state))

    def test_admin_reconcile_rejects_wrong_publication_association_owner_and_context(self):
        self.javascript(r"""
await openDbt('data_engineer');await dbDbtSubmit('compile');await dbDbtSubmit('build');
currentRole='admin';session.maximum_role='admin';dbMaximumRole='admin';session.username='administrator';dbPrincipal=JSON.stringify(['administrator',true]);p.role='admin';await dbDbtRefresh();await dbDbtObserve('run','run-1');review();await dbDbtSubmit('publish',ids[1]);
const original=structuredClone(p.publications[ids[1]]),count=posts().length;
p.schemas.find(s=>s.id==='schema').capabilities.write=false;assert.equal(dbDbtCanPublish(p.run.models[2]),false);p.schemas.find(s=>s.id==='schema').capabilities.write=true;
for(const change of [{run_id:'other'},{parent_run_id:'other'},{plan_id:'other'},{actor:'other'},{node_id:'other'},{context:{...original.context,grant_revision:99}}]){
 p.publications[ids[1]]={...original,...change};dbDbtRender();assert.equal(dbDbtCanReconcile(),false);assert.equal(dbDbtNode('reconcile').disabled,true);await dbDbtSubmit('reconcile');assert.equal(posts().length,count);
}
p.publications[ids[1]]=original;assert.equal(dbDbtCanReconcile(),true);
wrong={publication:{...original,parent_run_id:'other'}};await dbDbtObserve('publication',original.id);assert.equal(p.publications[ids[1]],original);assert.match(p.status.textContent,/Observation failed/);
""")

    def test_known_preaccept_project_rejection_explicit_close_retains_receipt(self):
        self.javascript(r"""
await openDbt();const baseRoute=route;route=async(path,options)=>{if(options?.method==='POST'&&path.endsWith('/plans'))throw Object.assign(Error('missing'),{status:404,code:'DBT_PROJECT_NOT_FOUND'});return baseRoute(path,options);};
await dbDbtSubmit('compile');const original=p.intent,first=posts()[0];assert.ok(dbDbtCanCloseRejected());assert.match(p.status.textContent,/rejected before acceptance/);
await dbDbtRefresh();assert.equal(p.intent,original);await dbDbtRecover();assert.equal(posts().length,1);
assert.equal(dbDbtNode('compile').disabled,true);assert.equal(dbDbtNode('project').disabled,true);
await dbDbtNode('close-rejected').onclick();assert.equal(p.intent,null);assert.deepEqual(p.rejectedIntent,original);assert.deepEqual(readStorage(dbDbtKey+'.rejected',null),JSON.parse(JSON.stringify(original)));
assert.equal(dbDbtNode('compile').disabled,false);assert.equal(dbDbtNode('project').disabled,false);assert.equal(posts().length,1);
assert.match(text(dbDbtNode('receipt')),new RegExp(first.options.headers['Idempotency-Key']));assert.match(text(dbDbtNode('receipt')),/DBT_PROJECT_NOT_FOUND/);
assert.equal(calls.filter(c=>c.path.startsWith('/api/v1/queries')&&c.options?.method==='POST').length,0);
setLanguage('ja');assert.match(dbDbtNode('close-rejected').textContent,/要求を閉じて選び直す/);
""")

    def test_reconcile_child_get_late_response_cannot_restore_purged_context(self):
        self.javascript(r"""
await build();review();await dbDbtSubmit('publish',ids[1]);
const baseRoute=route,child=deferred();let reading=false;
route=async(path,options)=>{if(!options?.method&&path.includes('/publications/')){reading=true;await child.promise;}return baseRoute(path,options);};
const pending=dbDbtSubmit('reconcile');await tick();assert.equal(reading,true);
p.database='other';dbDbtPurge();child.resolve();await pending;
assert.equal(p.run,null);assert.deepEqual(p.publications,{});assert.equal(dbDbtNode('publications').children.length,0);
assert.equal(posts().filter(c=>c.path.endsWith('/publish')).length,1);assert.equal(posts().filter(c=>c.path.endsWith('/reconcile')).length,1);
""")

    def test_uncertain_or_invalid_ack_cannot_close_even_after_recovery_404(self):
        for failure in ['transport', 'mismatch', 'forbidden']:
            with self.subTest(failure=failure):
                self.javascript(r"""
await openDbt();const baseRoute=route;
route=async(path,options)=>{if(options?.method==='POST'){
 if('FAILURE'==='transport')throw Error('lost');
 if('FAILURE'==='forbidden')throw Object.assign(Error('denied'),{status:403,code:'FORBIDDEN'});
 const reply=await baseRoute(path,options);reply.plan.context.grant_revision=99;return reply;
}return baseRoute(path,options);};
await dbDbtSubmit('compile');const original=p.intent,first=posts()[0];assert.ok(original);assert.equal(dbDbtCanCloseRejected(),false);
dbDbtCloseRejected();assert.equal(p.intent,original);assert.equal(dbDbtNode('compile').disabled,true);
route=async(path,options)=>{if(options?.method==='POST')throw Object.assign(Error('missing'),{status:404,code:'DBT_PROJECT_NOT_FOUND'});return baseRoute(path,options);};
if(p.invalid)await dbDbtRefresh();await dbDbtRecover();assert.equal(dbDbtCanCloseRejected(),false);assert.equal(p.intent,original);assert.deepEqual(posts().at(-1),first);
route=baseRoute;await dbDbtRecover();assert.equal(p.intent,null);assert.deepEqual(posts().at(-1),first);clearTimeout(p.timer);
""".replace('FAILURE', failure))

    def test_full_multimodel_flow_compile_sql_lineage_tests_and_per_model_publish(self):
        self.javascript(r"""
await compile();
assert.equal(posts().length,1);assert.equal(posts()[0].path,'/api/v1/dbt/plans');
assert.deepEqual(readBody(posts()[0]),{context:p.context,project_id:'lake_marts',source_schema:'raw',target_schema:'schema',staging_materialization:'ephemeral'});
assert.equal(p.context.schema_id,'sandbox');assert.equal(p.context.sql_schema,'sandbox');
assert.match(text(dbDbtNode('models')),/expanded into SQL/);assert.match(text(dbDbtNode('sql')),/source.demo.orders/);
assert.match(text(dbDbtNode('sql')),/<img src=x onerror=attack\(\)>/);
assert.equal(dbDbtNode('sql').descendants().some(n=>n.tagName==='IMG'),false);
assert.match(text(dbDbtNode('plan-detail')),/bytes-digest/);
await dbDbtSubmit('build');assert.equal(posts()[1].path,'/api/v1/dbt/runs');assert.equal(readBody(posts()[1]).plan_id,'plan-1');
assert.match(text(dbDbtNode('run-detail')),/Tests passed, awaiting publication/);
assert.match(text(dbDbtNode('candidates')),/snap-2/);assert.match(text(dbDbtNode('tests')),/PASSED/);
assert.equal(dbDbtCanPublish(p.run.models[0]),false);assert.equal(dbDbtCanPublish(p.run.models[1]),true);
review();assert.match(text(dbDbtNode('confirmation')),/sales.marts.first/);assert.match(text(dbDbtNode('confirmation')),/snap-1/);
await dbDbtSubmit('publish',ids[1]);assert.equal(posts().length,3);assert.equal(readBody(posts()[2]).node_id,ids[1]);
assert.equal(dbDbtCanPublish(p.run.models[1]),false);assert.equal(dbDbtCanPublish(p.run.models[2]),true);
pubs['pub-first'].state='PUBLISHED';await dbDbtObserve('publication','pub-first');
assert.match(text(dbDbtNode('publications')),/Published/);assert.equal(p.run.state,'READY');assert.equal(posts().length,3);
const drafts=worksheets.map(w=>w.sql);assert.equal(drafts.length,13);assert.deepEqual(drafts,Array.from({length:13},(_,i)=>'SELECT '+i));
""")

    def test_sandbox_view_contract_version_and_table_only_publication_bilingual(self):
        self.javascript(r"""
await build();plan.compatibility.version=2;
plan.models[1].materialized='view';plan.models[1].table_properties={};
run.models[1].materialized='view';run.models[1].table_properties={};run.models[1].build_id=null;
run.models[1].candidate={kind:'VIEW',ref:run.models[1].candidate.ref,view_uuid:'view-uuid',version:7,security:'INVOKER'};
await dbDbtObserve('plan','plan-1');await dbDbtObserve('run','run-1');
for(const lang of ['en','ja']){
 setLanguage(lang);
 assert.match(text(dbDbtNode('compatibility')),/SANDBOX INVOKER VIEW/);
 assert.match(text(dbDbtNode('compatibility')),lang==='ja'?/公開は TABLE のみ/:/TABLE-only publication/);
 assert.match(text(dbDbtNode('candidates')),/VIEW v7 · INVOKER/);
 assert.match(text(dbDbtNode('candidates')),/INVOKER/);
 assert.equal(dbDbtFormat(run.models[1]),t('Not applicable'));
 assert.equal(dbDbtCanPublish(p.run.models[1]),false);
 const buttons=dbDbtNode('publications').descendants().filter(n=>n.tagName==='BUTTON'&&n.textContent===t('Review publication'));
 assert.equal(buttons.length,1);
}
const count=posts().length;await dbDbtSubmit('publish',ids[1]);assert.equal(posts().length,count);
currentRole='bi_viewer';dbDbtValidate();assert.equal(p.run,null);assert.equal(p.plan,null);
assert.equal(text(dbDbtNode('candidates')).trim(),'');
""")

    def test_candidate_version_column_and_expandable_ref_details_bilingual(self):
        self.javascript(r"""
await build();
const snapshot='9223372036854775806',ref=structuredClone(run.models[1].candidate.ref);
run.models[1].materialized='view';
run.models[1].candidate={kind:'VIEW',ref,view_uuid:'view-uuid-<img src=x onerror=attack()>',version:7,security:'INVOKER',snapshot:'must-not-be-view-version'};
run.models[2].candidate.snapshot=snapshot;
run.models[2].candidate.version=99;
await dbDbtObserve('run','run-1');
for(const lang of ['en','ja']){
 setLanguage(lang);dbDbtRender();
 const candidate=dbDbtNode('candidates'),headers=candidate.querySelector('thead').querySelector('tr').children;
 assert.equal(headers[3].textContent,lang==='ja'?'TABLE snapshot / VIEW バージョン':'TABLE snapshot / VIEW version');
 assert.equal(headers[4].textContent,lang==='ja'?'候補 Ref の詳細':'Candidate Ref details');
 const rows=candidate.querySelector('tbody').children;
 assert.equal(rows[0].children[3].textContent,'N/A');
 assert.equal(rows[1].children[3].textContent,'VIEW v7 · INVOKER');
 assert.equal(rows[2].children[3].textContent,snapshot);
 for(const [i,m] of run.models.entries()){
  const cell=rows[i].children[4],detail=cell.children[0];
  assert.equal(detail.tagName,'DETAILS');assert.equal(Boolean(detail.open),false);
  assert.equal(detail.children[0].tagName,'SUMMARY');
  assert.equal(detail.children[0].textContent,t('Candidate Ref details'));
  const saved=JSON.parse(detail.querySelector('pre').textContent);
  assert.equal(saved.node_id,m.unique_id);assert.equal(saved.wap_model_id,m.wap_model_id);
  assert.equal(saved.wap_version,m.wap_version);assert.equal(saved.build_id,m.build_id);
  if(m.candidate)for(const [key,value] of Object.entries(m.candidate))assert.deepEqual(saved[key],value);
 }
 assert.equal(candidate.descendants().some(n=>n.tagName==='IMG'),false);
}
// Missing candidates retain the pending/legacy presentation rather than inventing a version.
run.models[1].candidate=null;run.models[2].candidate=null;await dbDbtObserve('run','run-1');
const rows=dbDbtNode('candidates').querySelector('tbody').children;
assert.equal(rows[1].children[3].textContent,'VIEW '+t('Not recorded'));
assert.equal(rows[2].children[3].textContent,t('Not recorded'));
""")

    def test_operation_ids_native_details_collapsed_with_counts_and_visible_gates(self):
        self.javascript(r"""
await build();
run.operations=Array.from({length:12},(_,i)=>({id:'full-operation-'+i+'-<img src=x onerror=attack()>',state:i<10?'FINISHED':'FAILED'}));
await dbDbtObserve('run','run-1');
for(const lang of ['en','ja']){
 setLanguage(lang);dbDbtRender();
 const area=dbDbtNode('run-detail'),detail=area.children.find(n=>n.tagName==='DETAILS');
 assert.ok(detail);assert.equal(Boolean(detail.open),false);assert.equal(detail.children[0].tagName,'SUMMARY');
 assert.match(text(detail.children[0]),lang==='ja'?/操作 ID \(12件\)/:/Operation IDs \(12\)/);
 const summary=text(detail.children[0]).replace(/\s+/g,' ');
 assert.ok(summary.includes(t('FINISHED')+' : 10'));assert.ok(summary.includes(t('FAILED')+' : 2'));
 assert.equal(detail.children.filter(n=>n.tagName==='P').length,12);
 for(const op of run.operations)assert.ok(text(detail).includes(op.id+' · '+op.state));
 assert.equal(area.descendants().some(n=>n.tagName==='IMG'),false);
 assert.ok(area.children.some(n=>n.tagName==='P'&&n.textContent===t('Tests passed, awaiting publication')));
 assert.match(text(dbDbtNode('tests')),/PASSED/);assert.equal(dbDbtCanPublish(p.run.models[1]),true);
 assert.ok(dbDbtNode('reason').textContent.includes(t('Publication requires admin and every planned test to pass. Each model publishes independently.')));
 run.state='FAILED';run.error_code='DBT_TEST_FAILED';await dbDbtObserve('run','run-1');
 assert.ok(area.children.some(n=>n.tagName==='P'&&n.textContent===t('FAILED')));
 assert.ok(area.children.some(n=>n.tagName==='P'&&n.textContent==='DBT_TEST_FAILED'));
 assert.equal(dbDbtCanPublish(p.run.models[1]),false);
 run.state='READY';delete run.error_code;await dbDbtObserve('run','run-1');
}
""")

    def test_default_override_not_silently_changed_and_project_options(self):
        self.javascript(r"""
await openDbt();dbDbtNode('materialization').value='';await dbDbtSubmit('compile');
assert.equal(readBody(posts()[0]).staging_materialization,null);
assert.match(text(dbDbtNode('plan-detail')),/Project default/);
assert.deepEqual(p.projects.map(p=>p.id),['lake_marts','approved_second']);
assert.equal(dbDbtNode('project').tagName,'SELECT');
assert.match(text(dbDbtNode('panel')),/defaults to views/);
""")

    def test_compile_async_ack_get_polling_and_observation_failure_is_not_unknown(self):
        self.javascript(r"""
await compile();plan.state='COMPILING';await dbDbtObserve('plan',plan.id);assert.equal(p.plan.state,'COMPILING');
assert.ok(p.timer);clearTimeout(p.timer);const count=posts().length;
wrong={plan:{...plan,id:'wrong-id'}};await dbDbtObserve('plan',plan.id);
assert.equal(p.plan.state,'COMPILING');assert.match(p.status.textContent,/Observation failed/);assert.equal(posts().length,count);
wrong=null;plan.state='READY';await dbDbtObserve('plan',plan.id);assert.equal(p.plan.state,'READY');assert.equal(posts().length,count);
""")

    def test_failed_missing_duplicate_tests_canceled_unknown_never_publish(self):
        self.javascript(r"""
await build();const original=structuredClone(run);
for(const replacement of [{state:'FAILED'},{state:'CANCELED'},{state:'UNKNOWN'},{cancel_requested:true},{tests:[]},{tests:[original.tests[0],original.tests[0]]},{tests:[{...original.tests[0],failure_count:2,state:'FAILED'},original.tests[1]]}]){
 run={...original,...replacement};await dbDbtObserve('run','run-1');assert.equal(dbDbtTestsPassed(),false);assert.equal(dbDbtCanPublish(p.run.models[1]),false);p.review=ids[1];await dbDbtSubmit('publish',ids[1]);
}
assert.equal(posts().length,2);
""")

    def test_lost_ack_same_key_frozen_body_context_and_explicit_recovery(self):
        self.javascript(r"""
await openDbt();lose=true;await dbDbtSubmit('compile');const first=posts()[0];const intent=p.intent;
assert.equal(p.plan,null);assert.match(p.status.textContent,/Receipt not observed/);assert.ok(storage.has(dbDbtKey));
dbDbtNode('source').value='schema';await dbDbtSubmit('compile');assert.equal(posts().length,1);
lose=false;await dbDbtRecover();assert.equal(posts().length,2);assert.deepEqual(posts()[1],first);assert.equal(p.plan.source_schema,'raw');assert.equal(p.intent,null);assert.equal(storage.has(dbDbtKey),false);
""")

    def test_late_compile_ack_and_principal_change_purge_protected_content(self):
        self.javascript(r"""
await openDbt();gate=deferred();const pending=dbDbtSubmit('compile');await tick();
currentRole='bi_viewer';dbDbtValidate();assert.equal(p.plan,null);assert.equal(p.pending,false);gate.resolve();await pending;
assert.equal(p.plan,null);assert.equal(dbDbtNode('sql').children.length,0);assert.equal(dbDbtNode('recover').disabled,true);
assert.equal(posts().length,1);currentRole='admin';dbPrincipal=JSON.stringify(['other',true]);dbDbtValidate();await dbDbtRecover();assert.equal(posts().length,1);
assert.equal(dbDbtNode('receipt').textContent,'');
""")

    def test_wrong_db_context_id_and_delayed_get_rejected(self):
        self.javascript(r"""
await build();const original=p.run;
for(const replacement of [{...run,id:'wrong'},{...run,context:{...run.context,database_id:'other'}},{...run,actor:'other',context:{...run.context,selected_role:'bi_viewer'}}]){
 wrong={run:replacement};await dbDbtObserve('run','run-1');assert.equal(p.run,original);assert.match(p.status.textContent,/Observation failed/);
}
wrong=null;observing=deferred();const pending=dbDbtObserve('run','run-1');await tick();p.database='other';dbDbtPurge();observing.resolve();await pending;
assert.equal(p.run,null);assert.equal(p.plan,null);assert.equal(dbDbtNode('sql').children.length,0);assert.equal(posts().length,2);
""")

    def test_engineer_build_admin_inspection_publication_viewer_and_role_cap(self):
        self.javascript(r"""
await openDbt('data_engineer');await dbDbtSubmit('compile');await dbDbtSubmit('build');
assert.equal(dbDbtOwnRun(),true);assert.equal(dbDbtCanPublish(p.run.models[1]),false);assert.match(dbDbtNode('reason').textContent,/requires admin/);
const saved=structuredClone(run);currentRole='admin';session.maximum_role='admin';dbMaximumRole='admin';dbDbtValidate();p.role='admin';await dbDbtRefresh();await dbDbtObserve('run',saved.id);
assert.equal(p.run.context.selected_role,'data_engineer');assert.equal(dbDbtOwnRun(),false);assert.equal(dbDbtCanPublish(p.run.models[1]),true);
review();await dbDbtSubmit('publish',ids[1]);assert.equal(readBody(posts().at(-1)).context.selected_role,'admin');
currentRole='bi_viewer';dbDbtValidate();assert.equal(p.plan,null);assert.equal(p.run,null);assert.equal(dbDbtNode('sql').children.length,0);assert.equal(dbDbtNode('compile').disabled,true);
assert.match(dbDbtNode('reason').textContent,/Viewer access/);assert.equal(dbDbtNode('role').children.filter(n=>n.value==='admin').every(n=>n.disabled),true);
""")


    def test_admin_inspects_engineer_revision_and_publishes_with_current_authority(self):
        for revision in ('null', '9'):
            with self.subTest(admin_revision=revision):
                self.javascript(r"""
inventory[0].grant_revision=1;
await openDbt('data_engineer');await dbDbtSubmit('compile');await dbDbtSubmit('build');
const savedPlan=structuredClone(plan),savedRun=structuredClone(run),count=posts().length;
currentRole='admin';session.maximum_role='admin';dbMaximumRole='admin';session.username='administrator';dbPrincipal=JSON.stringify(['administrator',true]);
inventory[0].grant_revision=ADMIN_REV;p.role='admin';await dbDbtRefresh();
const adminContext=structuredClone(p.context);assert.equal(adminContext.grant_revision,ADMIN_REV);
await dbDbtHistory();assert.deepEqual(p.plans,[savedPlan]);assert.deepEqual(p.runs,[savedRun]);
await dbDbtNode('history').descendants().filter(n=>n.tagName==='BUTTON')[0].onclick();
assert.deepEqual(p.plan,savedPlan);assert.equal(p.run,null);assert.equal(dbDbtCanBuild(),false);
await dbDbtNode('history').descendants().filter(n=>n.tagName==='BUTTON')[1].onclick();
assert.deepEqual(p.run,savedRun);assert.deepEqual(p.context,adminContext);assert.equal(posts().length,count);
assert.equal(dbDbtOwnRun(),false);assert.equal(dbDbtCanReconcile(),false);assert.equal(dbDbtNode('cancel').disabled,true);
await dbDbtSubmit('build');await dbDbtSubmit('cancel');await dbDbtSubmit('reconcile');assert.equal(posts().length,count);
for(const node of ids.slice(1)){
 review(node);await dbDbtSubmit('publish',node);assert.equal(p.intent,null);
 assert.deepEqual(readBody(posts().at(-1)).context,adminContext);
 assert.equal(p.publications[node].actor,'administrator');assert.deepEqual(p.publications[node].context,adminContext);
 assert.deepEqual(p.run,savedRun);assert.deepEqual(p.plan,savedPlan);
}
assert.equal(dbDbtCanReconcile(),true);assert.equal(dbDbtNode('reconcile').disabled,false);
lose=true;await dbDbtSubmit('reconcile');const original=posts().at(-1);assert.ok(p.intent);
// A reload restores only the frozen receipt, without adopting the engineer's authority.
p.intent=null;dbDbtPurge();lose=false;await dbDbtRefresh();assert.ok(p.intent);assert.equal(p.run,null);
await dbDbtRecover();assert.deepEqual(posts().at(-1),original);assert.equal(p.intent,null);
assert.deepEqual(p.run,savedRun);assert.deepEqual(p.context,adminContext);clearTimeout(p.timer);
""".replace('ADMIN_REV', revision))

    def test_own_post_receipts_reject_wrong_owner_and_context_with_same_key_recovery(self):
        for action, setup in [('compile', 'await openDbt();'), ('build', 'await compile();'), ('publish', 'await build();review();')]:
            for replacement in ["a.actor='another-actor'", "a.context.database_id='other'", "a.context.grant_revision=99", "a.context.selected_role='data_engineer'"]:
                with self.subTest(action=action, replacement=replacement):
                    self.javascript(setup + r"""
const originalRoute=route;
route=async(path,options)=>{const reply=await originalRoute(path,options);if(options?.method==='POST'){const a=reply.plan||reply.run||reply.publication;MUTATE;}return reply;};
await dbDbtSubmit('ACTION',ids[1]);assert.ok(p.intent);assert.match(p.status.textContent,/Receipt not observed/);
const original=posts().at(-1),intent=p.intent;assert.equal(intent.key,original.options.headers['Idempotency-Key']);
route=originalRoute;await dbDbtRecover();assert.deepEqual(posts().at(-1),original);assert.equal(p.intent,null);clearTimeout(p.timer);
""".replace('ACTION', action).replace('MUTATE', replacement))

    def test_engineer_cannot_inspect_saved_admin_or_other_grant_metadata(self):
        self.javascript(r"""
await build();const savedPlan=structuredClone(plan),savedRun=structuredClone(run);
currentRole='data_engineer';session.maximum_role='data_engineer';dbMaximumRole='data_engineer';p.role='data_engineer';await dbDbtRefresh();
await dbDbtHistory();assert.deepEqual(p.plans,[]);assert.deepEqual(p.runs,[]);assert.match(p.status.textContent,/Observation failed/);
await dbDbtObserve('plan',plan.id);assert.equal(p.plan,null);
await dbDbtObserve('run',run.id);assert.equal(p.run,null);assert.equal(dbDbtNode('sql').children.length,0);
plan={...savedPlan,context:{...savedPlan.context,selected_role:'data_engineer',grant_revision:99}};
run={...savedRun,context:{...plan.context}};
await dbDbtObserve('run',run.id);assert.equal(p.run,null);assert.match(p.status.textContent,/Observation failed/);
assert.equal(posts().length,2);
""")

    def test_history_frozen_artifact_and_worksheet_changes_do_not_retarget_mutation(self):
        self.javascript(r"""
await build();await dbDbtHistory();assert.equal(p.plans.length,1);assert.equal(p.runs.length,1);
assert.match(text(dbDbtNode('history')),/sandbox.*admin.*2026-10-29/);
const count=posts().length;await dbDbtNode('history').descendants().filter(n=>n.tagName==='BUTTON').at(-1).onclick();assert.equal(posts().length,count);
dbCurrent().database_id='elsewhere';dbCurrent().schema_id='else-schema';$('query-database').value='elsewhere';
await dbDbtSubmit('cancel');assert.equal(readBody(posts().at(-1)).context.database_id,'db');assert.equal(readBody(posts().at(-1)).context.schema_id,'sandbox');
assert.equal(p.run.state,'CANCELING');assert.equal(dbDbtNode('cancel').disabled,true);const n=posts().length;await dbDbtSubmit('cancel');assert.equal(posts().length,n);
await dbDbtSubmit('reconcile');assert.match(posts().at(-1).path,/\/reconcile$/);assert.deepEqual(Object.keys(readBody(posts().at(-1))),['context']);
""")

    def test_cancel_lost_ack_replay_latches_and_unknown_reconcile_does_not_build(self):
        self.javascript(r"""
await build();lose=true;await dbDbtSubmit('cancel');const cancel=posts().at(-1);assert.equal(dbDbtNode('build').disabled,true);assert.equal(dbDbtNode('cancel').disabled,true);
lose=false;await dbDbtRecover();assert.deepEqual(posts().at(-1),cancel);run.state='UNKNOWN';await dbDbtObserve('run','run-1');
assert.match(text(dbDbtNode('run-detail')),/Backend outcome unknown/);await dbDbtSubmit('reconcile');assert.equal(p.run.state,'UNKNOWN');assert.match(posts().at(-1).path,/reconcile$/);assert.equal(posts().filter(c=>c.path==='/api/v1/dbt/runs').length,1);
""")

    def test_deep_link_reload_get_only_no_elevation_preserves_hash_and_drafts(self):
        self.javascript(r"""
await build();const savedDrafts=worksheets.map(w=>w.sql),count=posts().length;
globalThis.location={href:'http://local/#transformation?creation=keep&dbt_db=db&dbt_plan=plan-1&dbt_run=run-1'};
globalThis.history={replaceState:(_,__,url)=>{globalThis.location.href=String(url);}};
dbDbtPurge();await dbDbtRestore();assert.equal(p.run.id,'run-1');assert.equal(posts().length,count);assert.equal(p.role,'admin');assert.deepEqual(worksheets.map(w=>w.sql),savedDrafts);assert.match(location.href,/creation=keep/);assert.doesNotMatch(location.href,/SELECT|credential|compiled_sql/);
const gets=calls.filter(c=>c.path.includes('/api/v1/dbt/')&&!c.options.method);assert.ok(gets.some(c=>c.path.includes('/runs/run-1?')));
""")

    def test_publication_commit_record_failure_is_terminal_and_never_republished(self):
        self.javascript(r"""
await build();review();await dbDbtSubmit('publish',ids[1]);pubs['pub-first'].state='COMMITTED_RECORDING_FAILED';await dbDbtObserve('publication','pub-first');
assert.match(text(dbDbtNode('publications')),/committed; recording failed/);assert.equal(dbDbtCanPublish(p.run.models[1]),false);assert.equal(p.timer,null);
const count=posts().length;p.review=ids[1];await dbDbtSubmit('publish',ids[1]);assert.equal(posts().length,count);assert.equal(p.run.state,'READY');
""")

    def test_reload_retains_unacknowledged_intent_and_never_automatically_posts(self):
        self.javascript(r"""
await openDbt();lose=true;await dbDbtSubmit('compile');const original=posts()[0];
p.intent=null;dbDbtPurge();lose=false;await dbDbtRefresh();
assert.equal(posts().length,1);assert.equal(p.plan,null);assert.ok(p.intent);assert.equal(dbDbtNode('recover').disabled,false);
await dbDbtRecover();assert.deepEqual(posts()[1],original);assert.equal(p.plan.id,'plan-1');
""")

    def test_role_maximum_binding_and_grant_change_purge_then_fail_closed(self):
        self.javascript(r"""
await build();dbUpdateMaximumRole('data_engineer');assert.equal(p.run,null);assert.equal(p.plan,null);assert.equal(dbDbtNode('sql').children.length,0);
session.maximum_role='admin';dbMaximumRole='admin';await dbDbtRefresh();await dbDbtObserve('run','run-1');assert.ok(p.run);
inventory[0].grant_revision++;await dbDbtRefreshAuthorization();assert.equal(p.run,null);assert.equal(dbDbtNode('build').disabled,true);
assert.equal(posts().length,2);
""")

    def test_role_cap_purges_admin_inventory_without_losing_refresh_selection(self):
        self.javascript(r"""
inventory.push({...inventory[0],id:'private-db',display_name:'Admin finance',sql_catalog:'admin_finance',allowed_roles:['admin']});
schemas.push({...schemas[0],id:'private-schema',display_name:'Admin secrets',sql_name:'admin_secrets'});
await compile();
assert.match(text(dbDbtNode('database')),/Admin finance/);assert.match(text(dbDbtNode('source')),/Admin secrets/);
assert.match(text(dbDbtNode('sql')),/select.*orders/);
currentRole='bi_viewer';dbDbtValidate();
assert.deepEqual(p.databases,[]);assert.deepEqual(p.schemas,[]);assert.equal(p.database,'db');
for(const name of ['database','source','target'])assert.deepEqual(dbDbtNode(name).options.map(o=>o.value),['']);
assert.equal(p.plan,null);assert.equal(p.run,null);assert.equal(text(dbDbtNode('sql')).trim(),'');
assert.equal(dbDbtNode('compile').disabled,true);assert.equal(dbDbtNode('build').disabled,true);
const count=calls.length;await dbDbtNode('refresh').onclick();assert.equal(calls.length,count);
inventory=inventory.filter(d=>d.id==='db');schemas=schemas.filter(s=>s.id!=='private-schema');
currentRole='admin';dbDbtValidate();await dbDbtNode('refresh').onclick();
assert.equal(dbDbtAllowed(),true);assert.equal(dbDbtNode('database').value,'db');
assert.doesNotMatch(text(dbDbtNode('database')),/Admin finance/);assert.doesNotMatch(text(dbDbtNode('source')),/Admin secrets/);
assert.equal(p.plan,null);assert.equal(text(dbDbtNode('sql')).trim(),'');assert.equal(posts().length,1);
""")

    def test_auth_failure_purges_inventory_and_preserves_frozen_receipt_recovery(self):
        for status in (401, 403):
            with self.subTest(status=status):
                self.javascript(r"""
inventory.push({...inventory[0],id:'private-db',display_name:'Admin finance',sql_catalog:'admin_finance',allowed_roles:['admin']});
schemas.push({...schemas[0],id:'private-schema',display_name:'Admin secrets',sql_name:'admin_secrets'});
await compile();lose=true;await dbDbtSubmit('build');
const original=posts().at(-1),intent=p.intent;assert.ok(intent);assert.match(text(dbDbtNode('sql')),/select.*orders/);
schemasRoute=async()=>({http:STATUS,body:{error:{code:'FORBIDDEN',message:'denied'}}});
await dbDbtRefreshAuthorization();
assert.deepEqual(p.databases,[]);assert.deepEqual(p.schemas,[]);assert.equal(p.database,'db');assert.equal(p.intent,intent);
for(const name of ['database','source','target'])assert.deepEqual(dbDbtNode(name).options.map(o=>o.value),['']);
assert.equal(p.plan,null);assert.equal(p.run,null);assert.equal(text(dbDbtNode('sql')).trim(),'');assert.equal(dbDbtNode('recover').disabled,true);
inventory=inventory.filter(d=>d.id==='db');schemas=schemas.filter(s=>s.id!=='private-schema');schemasRoute=null;lose=false;
await dbDbtNode('refresh').onclick();assert.equal(dbDbtAllowed(),true);assert.equal(p.intent,intent);
assert.equal(dbDbtNode('database').value,'db');assert.equal(text(dbDbtNode('sql')).trim(),'');assert.equal(posts().length,2);
await dbDbtRecover();assert.deepEqual(posts().at(-1),original);assert.equal(p.run.id,'run-1');assert.equal(p.intent,null);
""".replace('STATUS', str(status)))

    def test_dbt_role_and_database_onchange_refresh_authorized_inventory(self):
        self.javascript(r"""
await compile();inventory.push({...inventory[0],id:'other',display_name:'Other',sql_catalog:'other'});
schemasRoute=async(path)=>({items:schemas.map(s=>({...s,database_id:path.includes('/other/')?'other':'db'}))});
dbDbtNode('role').value='data_engineer';await dbDbtNode('role').onchange();
assert.equal(p.role,'data_engineer');assert.equal(dbDbtAllowed(),true);assert.equal(p.database,'db');assert.equal(p.plan,null);
assert.equal(text(dbDbtNode('sql')).trim(),'');assert.ok(dbDbtNode('database').options.some(o=>o.value==='other'));
dbDbtNode('database').value='other';await dbDbtNode('database').onchange();
assert.equal(p.database,'other');assert.equal(dbDbtNode('database').value,'other');assert.equal(p.context.database_id,'other');assert.equal(dbDbtAllowed(),true);
assert.ok(calls.some(c=>c.path==='/api/v1/databases/other/schemas'&&c.options.headers['X-Shirokuma-Role']==='data_engineer'));
await dbDbtNode('refresh').onclick();assert.equal(p.database,'other');assert.equal(dbDbtAllowed(),true);assert.equal(posts().length,1);
""")

    def test_deeplink_wrong_parent_rejects_and_history_run_change_clears_publications(self):
        self.javascript(r"""
await build();review();await dbDbtSubmit('publish',ids[1]);assert.ok(p.publications[ids[1]]);
run={...run,id:'run-2'};await dbDbtObserve('run','run-2');assert.deepEqual(p.publications,{});
globalThis.location={href:'http://local/#transformation?dbt_db=db&dbt_plan=wrong-plan&dbt_run=run-2'};
dbDbtPurge();await dbDbtRestore();assert.equal(p.run,null);assert.equal(p.plan,null);assert.match(p.status.textContent,/Observation failed/);assert.equal(posts().length,3);
""")

    def test_compile_failed_identity_has_actionable_copy_and_keeps_no_candidate(self):
        self.javascript(r"""
await compile();plan={...plan,state:'FAILED',error_code:'STALE_RESOURCE'};await dbDbtObserve('plan','plan-1');
assert.match(text(dbDbtNode('plan-detail')),/Refresh context and compile a new plan/);assert.equal(dbDbtNode('build').disabled,true);assert.equal(p.run,null);assert.equal(dbDbtNode('compile').disabled,false);
""")

    def test_cancel_wrong_receipt_stays_unacknowledged(self):
        self.javascript(r"""
await build();wrong={run:{...run,id:'different-run'}};await dbDbtSubmit('cancel');assert.equal(p.run.state,'READY');assert.ok(p.intent);assert.equal(dbDbtNode('cancel').disabled,true);
wrong=null;await dbDbtRecover();assert.equal(p.run.state,'CANCELING');assert.equal(p.intent,null);
""")

    def test_async_acks_enable_only_get_polling_until_plan_and_tests_ready(self):
        self.javascript(r"""
await openDbt();const originalRoute=route;
route=async(path,options)=>{const reply=await originalRoute(path,options);if(options?.method==='POST'&&path.endsWith('/plans')){reply.plan.state='QUEUED';plan.state='COMPILING';}if(options?.method==='POST'&&path.endsWith('/runs')){reply.run.state='QUEUED';run.state='BUILDING';}return reply;};
await dbDbtSubmit('compile');assert.equal(p.plan.state,'QUEUED');assert.ok(p.timer);assert.equal(dbDbtNode('build').disabled,true);
assert.match(p.status.textContent,/does not execute engine SQL/);await dbDbtObserve('plan','plan-1');assert.equal(p.plan.state,'COMPILING');
plan.state='READY';await dbDbtObserve('plan','plan-1');assert.equal(dbDbtNode('build').disabled,false);await dbDbtSubmit('build');assert.equal(p.run.state,'QUEUED');assert.match(p.status.textContent,/few minutes/);assert.equal(dbDbtTestsPassed(),false);
run.state='TESTING';await dbDbtObserve('run','run-1');assert.equal(dbDbtCanPublish(p.run.models[1]),false);run.state='READY';await dbDbtObserve('run','run-1');assert.equal(dbDbtTestsPassed(),true);
assert.equal(posts().length,2);assert.equal(p.timer,null);assert.equal(calls.filter(c=>c.path.startsWith('/api/v1/queries')&&c.options?.method==='POST').length,0);
""")

    def test_publication_ack_wrong_model_keeps_original_publish_receipt(self):
        self.javascript(r"""
await build();review();const originalRoute=route;
route=async(path,options)=>{const reply=await originalRoute(path,options);if(options?.method==='POST'&&path.endsWith('/publish'))reply.publication.node_id=ids[2];return reply;};
await dbDbtSubmit('publish',ids[1]);assert.deepEqual(p.publications,{});assert.ok(p.intent);const first=posts().at(-1);assert.equal(p.intent.body.node_id,ids[1]);
route=originalRoute;await dbDbtRecover();assert.deepEqual(posts().at(-1),first);assert.equal(p.publications[ids[1]].node_id,ids[1]);assert.equal(p.intent,null);clearTimeout(p.timer);
""")

    def test_missing_contract_ack_polls_original_plan_until_supported_or_blocked(self):
        for language in ['ja', 'en']:
            for terminal in ['READY', 'FAILED']:
                with self.subTest(language=language, terminal=terminal):
                    self.javascript(r"""
await openDbt();setLanguage('LANGUAGE');
const originalRoute=route,originalTimeout=setTimeout,originalClearTimeout=clearTimeout;
// Capture and invoke the production scheduler callback, without waiting on wall time.
globalThis.setTimeout=callback=>({callback,unref(){}});globalThis.clearTimeout=()=>{};
const originalObserve=dbDbtObserve;let observation;
dbDbtObserve=(...args)=>(observation=originalObserve(...args));
route=async(path,options)=>{
 const reply=await originalRoute(path,options);
 if(options?.method==='POST'&&path.endsWith('/plans')){
  reply.plan.state='QUEUED';delete reply.plan.compatibility;
  plan.state='COMPILING';delete plan.compatibility;
 }
 return reply;
};
await dbDbtSubmit('compile');const savedContext=structuredClone(p.context),savedId=p.plan.id;
function assertPending(state){
 assert.equal(p.plan.state,state);assert.equal(p.plan.id,savedId);
 assert.deepEqual(p.plan.context,savedContext);assert.deepEqual(p.context,savedContext);
 assert.equal(dbDbtCompatibility(p.plan),'pending');assert.equal(dbDbtNode('build').disabled,true);
 const detail=text(dbDbtNode('compatibility')),reason=dbDbtNode('reason').textContent;
 assert.match(detail,'LANGUAGE'==='ja'?/設定の互換性を検証中/:/Validating settings compatibility/);
 const wait='LANGUAGE'==='ja'?/元の保存済みプランの検証完了をお待ちください/:/Wait for validation of the original saved plan/;
 assert.match(detail,wait);assert.match(reason,wait);
 assert.doesNotMatch(detail+reason,/Legacy plan|旧プラン|Recompile to validate|新規構築の前に再コンパイル/);
 assert.equal(posts().length,1);assert.ok(p.timer?.callback);
}
assertPending('QUEUED');await dbDbtSubmit('build');assert.equal(posts().length,1);
let mark=calls.length;p.timer.callback();await observation;assertPending('COMPILING');
assert.equal(calls.length,mark+1);assert.match(calls.at(-1).path,/\/plans\/plan-1\?/);assert.equal(calls.at(-1).options.method,undefined);
await dbDbtSubmit('build');assert.equal(posts().length,1);
plan.state='TERMINAL';plan.compatibility={version:1,executor:'sql_wap',status:'TERMINAL'==='READY'?'supported':'blocked',issues:'TERMINAL'==='READY'?[]:[{node_id:ids[1],code:'UNSUPPORTED_MODEL_CONFIG',setting:'on_schema_change'}]};
mark=calls.length;p.timer.callback();await observation;
assert.equal(calls.length,mark+1);assert.equal(posts().length,1);assert.equal(p.timer,null);
assert.equal(p.plan.id,savedId);assert.deepEqual(p.plan.context,savedContext);assert.deepEqual(p.context,savedContext);
assert.equal(p.plan.state,'TERMINAL');assert.equal(dbDbtNode('build').disabled,'TERMINAL'!=='READY');
assert.equal(dbDbtCanBuild(),'TERMINAL'==='READY');
const detail=text(dbDbtNode('compatibility')),reason=dbDbtNode('reason').textContent;
if('TERMINAL'==='READY'){
 assert.match(detail,'LANGUAGE'==='ja'?/対応済み: SQL \/ WAP/:/Supported: SQL \/ WAP/);
 assert.match(detail,'LANGUAGE'==='ja'?/保存プランと下記の適用テーブル形式/:/Build candidates and test using the saved plan/);
}else{
 assert.match(detail,'LANGUAGE'==='ja'?/停止中: 未対応の設定/:/Blocked: unsupported settings/);
 assert.match(detail,/on_schema_change/);
 assert.match(reason,'LANGUAGE'==='ja'?/設定を確認し、対応範囲のプロジェクト/:/Review the settings and compile a supported project/);
 await dbDbtSubmit('build');assert.equal(posts().length,1);
}
globalThis.setTimeout=originalTimeout;globalThis.clearTimeout=originalClearTimeout;
""".replace('LANGUAGE', language).replace('TERMINAL', terminal))

    def test_present_in_progress_contracts_fail_closed_instead_of_pending(self):
        self.javascript(r"""
await compile();
for(const state of ['QUEUED','COMPILING'])for(const compatibility of [null,{},[],{version:99,executor:'sql_wap',status:'supported',issues:[]},{version:1,executor:'native',status:'supported',issues:[]},{version:1,executor:'sql_wap',status:'unknown',issues:[]},{version:1,executor:'sql_wap',status:'supported',issues:[{}]},{version:1,executor:'sql_wap',status:'blocked',issues:[{node_id:ids[0],code:'UNSUPPORTED_MATERIALIZATION',setting:'materialized'}]}]){
 plan={...plan,state,compatibility};await dbDbtObserve('plan',plan.id);
 for(const lang of ['ja','en']){
  setLanguage(lang);const blocked=compatibility?.status==='blocked';
  assert.equal(dbDbtCompatibility(p.plan),blocked?'blocked':'invalid');
  assert.match(text(dbDbtNode('compatibility')),blocked?(lang==='ja'?/停止中: 未対応の設定/:/Blocked: unsupported settings/):(lang==='ja'?/互換性を検証できません/:/Compatibility not validated/));
  assert.match(dbDbtNode('reason').textContent,blocked?(lang==='ja'?/ephemeral を明示指定/:/explicitly choose ephemeral/):(lang==='ja'?/新規構築の前に再コンパイル/:/Recompile to validate settings/));
  assert.equal(dbDbtNode('build').disabled,true);assert.equal(dbDbtCanBuild(),false);
 }
 await dbDbtSubmit('build');assert.equal(posts().length,1);
}
clearTimeout(p.timer);
""")

    def test_supported_saved_contract_and_actual_plan_run_table_formats(self):
        self.javascript(r"""
await compile();assert.equal(dbDbtCanBuild(),true);
for(const lang of ['ja','en']){
 setLanguage(lang);
 const detail=text(dbDbtNode('compatibility'));
 assert.match(detail,/SQL \/ WAP/);assert.match(detail,/COUNT\(\*\)/);
 assert.match(detail,lang==='ja'?/UI／API／CLI は互換性のある保存済み1モデル/:/UI\/API\/CLI supports native TABLE execution for one compatible saved model/);
 assert.match(text(dbDbtNode('models')),lang==='ja'?/適用テーブル形式/:/Applied table format/);
 assert.match(text(dbDbtNode('models')),/PARQUET/);
 assert.match(text(dbDbtNode('models')),lang==='ja'?/コネクタ既定/:/Connector default/);
}
const detail=dbDbtNode('plan-detail');assert.ok(detail.children.indexOf(dbDbtNode('compatibility'))<detail.children.length-1);
await dbDbtSubmit('build');assert.match(text(dbDbtNode('candidates')),/PARQUET/);
assert.match(text(dbDbtNode('candidates')),/Connector default/);
// Run format is read from the run, never inferred from a subsequently inspected plan.
p.plan={...p.plan,models:p.plan.models.map(m=>({...m,table_properties:{}}))};dbDbtRender();
assert.match(text(dbDbtNode('candidates')),/PARQUET/);
assert.equal(posts().length,2);assert.equal(calls.some(c=>c.path.startsWith('/api/v1/queries')&&c.options?.method==='POST'),false);
""")

    def test_blocked_view_and_test_settings_are_localized_and_actionable(self):
        self.javascript(r"""
await compile();const selection=dbDbtNode('materialization').value;
plan={...plan,state:'FAILED',models:[],compatibility:{version:1,executor:'sql_wap',status:'blocked',issues:[{node_id:'model.demo.stage',code:'UNSUPPORTED_MATERIALIZATION',setting:'materialized'},{node_id:'test.demo.policy',code:'UNSUPPORTED_TEST_CONFIG',setting:'test.severity'}]}};
await dbDbtObserve('plan',plan.id);
for(const lang of ['ja','en']){
 setLanguage(lang);const detail=text(dbDbtNode('compatibility'));
 assert.match(detail,/model.demo.stage/);assert.match(detail,/test.demo.policy/);assert.match(detail,/test.severity/);
 assert.match(detail,lang==='ja'?/未対応の materialization/:/Unsupported materialization/);
 assert.match(detail,lang==='ja'?/テストは error・COUNT\(\*\)・失敗件数0が必要/:/Test requires error severity, COUNT\(\*\) and zero failures/);
 assert.match(dbDbtNode('reason').textContent,lang==='ja'?/ephemeral を明示指定/:/explicitly choose ephemeral/);
 assert.doesNotMatch(dbDbtNode('reason').textContent,/Only the original owner/);
 assert.equal(dbDbtNode('build').disabled,true);assert.equal(dbDbtNode('compile').disabled,false);
}
assert.equal(dbDbtNode('materialization').value,selection);await dbDbtSubmit('build');assert.equal(posts().length,1);
plan.compatibility.issues.shift();await dbDbtObserve('plan',plan.id);
assert.match(dbDbtNode('reason').textContent,/Review the settings and compile a supported project/);
""")

    def test_unknown_or_malformed_compatibility_cannot_build(self):
        self.javascript(r"""
await compile();
for(const compatibility of [null,{},[],{version:99,executor:'sql_wap',status:'supported',issues:[]},{version:'1',executor:'sql_wap',status:'supported',issues:[]},{version:1,executor:'native',status:'supported',issues:[]},{version:1,executor:'sql_wap',status:'unknown',issues:[]},{version:1,executor:'sql_wap',status:'supported'},{version:1,executor:'sql_wap',status:'supported',issues:[{}]}]){
 plan={...plan,compatibility};await dbDbtObserve('plan',plan.id);
 assert.ok(p.plan);assert.equal(dbDbtCanBuild(),false);assert.equal(dbDbtNode('build').disabled,true);
 assert.match(text(dbDbtNode('compatibility')),/Compatibility not validated/);
 assert.match(dbDbtNode('reason').textContent,/Recompile to validate settings/);
 await dbDbtSubmit('build');
}
plan={...plan,compatibility:{version:1,executor:'sql_wap',status:'blocked',issues:[]}};
await dbDbtObserve('plan',plan.id);assert.equal(dbDbtCanBuild(),false);assert.equal(posts().length,1);
""")

    def test_legacy_plan_get_history_and_existing_run_publication_are_preserved(self):
        self.javascript(r"""
await compile();delete plan.compatibility;for(const m of plan.models)delete m.table_properties;
await dbDbtHistory();await dbDbtObserve('plan',plan.id);
assert.ok(p.plan);assert.match(text(dbDbtNode('history')),/plan-1/);
for(const state of ['READY','FAILED']){
 plan.state=state;await dbDbtObserve('plan',plan.id);
 for(const lang of ['ja','en']){
  setLanguage(lang);
  assert.equal(dbDbtCompatibility(p.plan),'legacy');
  assert.match(text(dbDbtNode('compatibility')),lang==='ja'?/旧プラン: 再コンパイルが必要/:/Legacy plan: recompile required/);
  assert.match(dbDbtNode('reason').textContent,lang==='ja'?/新規構築の前に再コンパイル/:/Recompile to validate settings/);
  assert.equal(dbDbtNode('build').disabled,true);
 }
}
await dbDbtSubmit('build');assert.equal(posts().length,1);
// An already admitted run is observed through GET and retains all publication gates.
run=makeRun(p.context);for(const m of run.models)delete m.table_properties;
await dbDbtObserve('run',run.id);assert.equal(dbDbtTestsPassed(),true);
assert.match(text(dbDbtNode('candidates')),/Not recorded/);assert.equal(dbDbtCanPublish(p.run.models[1]),true);
review();await dbDbtSubmit('publish',ids[1]);assert.ok(p.publications[ids[1]]);assert.equal(posts().length,2);
""")

    def test_diagnostics_name_every_semantic_setting_without_rendering_values(self):
        self.javascript(r"""
await compile();
const shared=['pre_hook','post_hook','on_run_start','on_run_end','hooks','grants','persist_docs','sql_header','store_failures','store_failures_as','incremental_strategy','full_refresh','column_types','unique_key','partition_by','cluster_by','table_format','file_format','location','catalog_name','indexes','bind','dist','sort','sql_where','contract'];
const modelOnly=['materialized','properties','on_table_exists','on_schema_change','on_configuration_change','lookback','batch_size','begin','event_time','concurrent_batches','on_error'];
const settings=[...shared,...shared.map(s=>'test.'+s),...modelOnly,'test.severity','test.fail_calc','test.error_if','test.warn_if','test.limit'];
const secret="SELECT private_token FROM '/private/dbt' <img src=x onerror=attack()>";
plan={...plan,state:'FAILED',compatibility:{version:1,executor:'sql_wap',status:'blocked',issues:settings.map(setting=>({node_id:setting.startsWith('test.')?'test.demo.policy':ids[1],code:setting.startsWith('test.')?'UNSUPPORTED_TEST_CONFIG':'UNSUPPORTED_MODEL_CONFIG',setting,value:secret,config:{injected:secret}}))}};
await dbDbtObserve('plan',plan.id);
for(const lang of ['ja','en']){
 setLanguage(lang);const detail=text(dbDbtNode('compatibility'));
 const table=dbDbtNode('compatibility').descendants().find(n=>n.tagName==='TABLE');
 const rows=table.children.find(n=>n.tagName==='TBODY').children;
 assert.deepEqual(rows.map(row=>row.children[1].textContent),settings);
 assert.doesNotMatch(detail,/Unknown setting|不明な設定|private_token|private\/dbt|onerror|injected/);
 assert.equal(dbDbtNode('build').disabled,true);
}
await dbDbtSubmit('build');assert.equal(posts().length,1);
""")

    def test_diagnostics_allowlist_hides_injected_values_paths_sql_and_logs(self):
        self.javascript(r"""
await compile();const secret="SELECT secret_token FROM '/private/dbt/project' <img src=x onerror=attack()>";
plan={...plan,state:'FAILED',compatibility:{version:1,executor:'sql_wap',status:'blocked',raw_logs:secret,issues:[
 {node_id:ids[1],code:'UNSUPPORTED_TABLE_PROPERTIES',setting:'properties',value:secret,path:secret,sql:secret},
 {node_id:null,code:'UNSUPPORTED_MODEL_CONFIG',setting:'on_table_exists',config:secret},
 {node_id:secret,code:secret,setting:secret},
 {node_id:ids[2],code:'__proto__',setting:'column_types'},null
]}};
plan.models[1].table_properties={format:secret};plan.models[2].table_properties={format:'PARQUET',secret};
await dbDbtObserve('plan',plan.id);
const detail=text(dbDbtNode('plan-detail')),models=text(dbDbtNode('models'));
assert.match(detail,/Only fixed format=PARQUET is supported/);assert.match(detail,/Unsupported model setting/);
assert.match(detail,/Unknown compatibility issue/);assert.match(detail,/Unknown setting/);
assert.doesNotMatch(detail+models,/secret_token|private\/dbt|onerror|raw_logs/);
assert.match(models,/Not validated/);assert.equal(dbDbtNode('build').disabled,true);
assert.equal(calls.some(c=>c.path.startsWith('/api/v1/queries')&&c.options?.method==='POST'),false);assert.equal(posts().length,1);
""")

    def test_compatibility_diagnostics_purge_with_role_and_context_and_fit_narrow_structure(self):
        self.javascript(r"""
narrow=true;await compile();assert.ok(dbDbtNode('compatibility').children.length);
const sections=dbDbtNode('compatibility');assert.equal(sections.tagName,'SECTION');assert.equal(sections.getAttribute('aria-labelledby'),'db-dbt-compatibility-title');
plan.compatibility={version:1,executor:'sql_wap',status:'blocked',issues:[{node_id:ids[0],code:'UNSUPPORTED_MATERIALIZATION',setting:'materialized'}]};
await dbDbtObserve('plan',plan.id);assert.equal(dbDbtNode('compatibility').children.find(n=>n.className==='table-wrap').children[0].tagName,'TABLE');
currentRole='bi_viewer';dbDbtValidate();assert.equal(p.plan,null);assert.equal(dbDbtNode('plan-detail').children.length,0);
assert.equal(dbDbtNode('models').children.length,0);assert.equal(dbDbtNode('build').disabled,true);
currentRole='admin';dbDbtValidate();await dbDbtRefresh();assert.equal(dbDbtNode('plan-detail').children.some(n=>n.id==='db-dbt-compatibility'),false);
await dbDbtObserve('plan',plan.id);assert.match(text(dbDbtNode('compatibility')),/Unsupported materialization/);
inventory[0].generation_id='changed';await dbDbtRefreshAuthorization();assert.equal(p.plan,null);assert.equal(dbDbtNode('plan-detail').children.length,0);
assert.equal(posts().length,1);
""")
        self.assertIn('#db-dbt-panel :is(p,td,th,summary){overflow-wrap:anywhere}', harness.HTML)
        self.assertIn('#db-dbt-panel .table-wrap{max-width:100%;overflow-x:auto}', harness.HTML)

    def test_native_saved_history_detail_localization_and_publication_block(self):
        self.javascript(r"""
await compile();run=makeRun(p.context,{executor:'native_table',selected_node_id:ids[1],state:'NATIVE_VERIFIED',native_verified:true,tests_status:'NOT_RUN',publication_allowed:false,tests:[]});
run.models=[run.models[1]];await dbDbtHistory();await dbDbtObserve('run',run.id);
assert.equal(p.plan.compatibility.executor,'sql_wap');assert.equal(p.run.executor,'native_table');
for(const node of ['run-detail','history']){assert.match(text(dbDbtNode(node)),/native_table/);assert.match(text(dbDbtNode(node)),/model.demo.first/);assert.match(text(dbDbtNode(node)),/Native TABLE verified; tests not run; publication unavailable/);}
assert.equal(dbDbtTestsPassed(),false);assert.equal(dbDbtCanPublish(p.run.models[0]),false);
setLanguage('ja');for(const node of ['run-detail','history'])assert.match(text(dbDbtNode(node)),/Native TABLE 検証済み。テスト未実行、公開不可/);
setLanguage('en');const count=posts().length;
run={...run,state:'READY',tests:structuredClone(plan.tests).map(t=>({...t,state:'PASSED',failure_count:0}))};
await dbDbtObserve('run',run.id);assert.equal(dbDbtTestsPassed(),false);assert.equal(dbDbtCanPublish(p.run.models[0]),false);
p.review=ids[1];await dbDbtSubmit('publish',ids[1]);assert.equal(posts().length,count);
assert.doesNotMatch(text(dbDbtNode('run-detail')),/Tests passed, awaiting publication/);
for(const state of ['BUILDING','UNKNOWN','FAILED']){run={...run,state};await dbDbtObserve('run',run.id);assert.match(text(dbDbtNode('run-detail')),new RegExp(state));assert.match(text(dbDbtNode('run-detail')),/Tests not run; publication unavailable/);}
run={...run,state:'BUILDING'};await dbDbtObserve('run',run.id);assert.equal(dbDbtNode('cancel').disabled,false);
await dbDbtSubmit('cancel');assert.equal(readBody(posts().at(-1)).context.database_id,p.context.database_id);
run={...run,state:'UNKNOWN'};await dbDbtObserve('run',run.id);assert.equal(dbDbtNode('reconcile').disabled,false);
await dbDbtSubmit('reconcile');assert.match(posts().at(-1).path,/reconcile$/);
assert.ok(!text(dbDbtNode('panel')).includes('Native dbt execution is unsupported.'));
clearTimeout(p.timer);
""")

    def test_native_history_plan_mismatch_does_not_replace_selected_run(self):
        self.javascript(r"""
await build();const saved=p.run;
run=makeRun(p.context,{id:'native-run',executor:'native_table',selected_node_id:ids[1],state:'NATIVE_VERIFIED',plan_id:'different-plan'});
await dbDbtObserve('run',run.id,'plan-1');assert.equal(p.run,saved);
assert.match(p.status.textContent,/failed/i);assert.equal(posts().length,2);
""")

    def test_japanese_english_state_copy_and_native_responsive_structure(self):
        self.javascript(r"""
await build();setLanguage('ja');assert.match(text(dbDbtNode('run-detail')),/テスト成功・公開待ち/);assert.match(dbDbtNode('reason').textContent,/モデルごとに独立/);
assert.equal(dbDbtNode('compile').textContent,'プロジェクトをコンパイル');review();assert.equal(dbDbtNode('publish').textContent,'監査して公開');
setLanguage('en');assert.match(text(dbDbtNode('run-detail')),/Tests passed, awaiting publication/);assert.equal(dbDbtNode('compile').textContent,'Compile project');
assert.equal(dbDbtNode('role').tagName,'SELECT');assert.equal(dbDbtNode('models').className,'table-wrap');assert.equal(dbDbtNode('panel').getAttribute('aria-labelledby'),'db-dbt-title');
""")
        self.assertIn('@media(max-width:600px){#db-dbt-panel .toolbar>div{flex-basis:100%}', harness.HTML)
        self.assertIn('#db-dbt-panel{min-width:0;max-width:100%}', harness.HTML)
        self.assertIn('overflow-wrap:anywhere', harness.HTML)
        dbt = harness.DATABASE.split('// Database dbt:')[1].split('// Database schedules')[0]
        self.assertNotIn('innerHTML', dbt)
        self.assertNotIn('/api/dbt', dbt)


if __name__ == '__main__':
    unittest.main()
