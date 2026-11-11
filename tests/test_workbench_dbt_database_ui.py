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
function makeGraphOptions(a){return {version:1,executor:'native_graph',plan_id:a.id,source_digest:a.source_digest,eligible:true,reason_code:null,models:a.models.map(m=>({node_id:m.unique_id,materialized:m.materialized,depends_on:structuredClone(m.depends_on)})),model_count:a.models.length,table_count:a.models.filter(m=>m.materialized==='table').length,testability:{eligible:a.tests.length>0,reason_code:a.tests.length?null:'DBT_NATIVE_TEST_EMPTY_SUITE',count:a.tests.length}};}
function makeRun(context,body={}){return {id:'run-1',kind:'run',actor:dbDbtOwner(),context:structuredClone(context),plan_id:plan.id,state:'READY',cancel_requested:false,created_at:'2026-10-29T00:01:00Z',models:plan.models.map((m,i)=>({unique_id:m.unique_id,name:m.name,materialized:m.materialized,...(m.materialized==='view'?{view_security:m.view_security}:{}),table_properties:structuredClone(m.table_properties),state:i?'BUILT':'EXPANDED',wap_model_id:'wap-'+i,wap_version:2,build_id:i?'build-'+i:null,candidate:i?{ref:{database_id:'db',schema_id:'sandbox',object_id:'candidate-'+i,incarnation:1},snapshot:'snap-'+i}:null,operation_ids:[]})),tests:plan.tests.map(t=>({...t,state:'PASSED',failure_count:0,operation_id:'op-'+t.unique_id})),operations:[{id:'safe-op',state:'FINISHED'}],...body};}
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
   else if(url.pathname.endsWith('/runs')){run=makeRun(context,body.executor==='native_table'?{executor:'native_table',selected_node_id:body.node_id,state:'QUEUED',tests:[],tests_status:body.native_tests===true?'QUEUED':'NOT_RUN',...(body.native_tests===true?{native_tests:true}:{}),publication_allowed:false}:{});if(body.executor==='native_graph'){run={...run,executor:'native_graph',source_digest:plan.source_digest,state:'QUEUED',native_tests:body.native_tests===true,native_verified:false,publication_allowed:false,tests_status:body.native_tests===true?'QUEUED':'NOT_RUN',models:run.models.map(m=>({...m,state:m.materialized==='ephemeral'?'EXPANDED':'QUEUED',candidate:null})),tests:body.native_tests===true?run.tests.map(t=>({...t,state:'QUEUED',failure_count:null})):[]};}artifact={run};}
   else if(url.pathname.endsWith('/cancel')){run={...run,state:'CANCELING',cancel_requested:true};artifact={run};}
   else if(url.pathname.endsWith('/reconcile')){artifact={run};}
   else if(url.pathname.endsWith('/publish')){const publication={id:'pub-'+body.node_id.split('.').at(-1),kind:'publication',actor:dbDbtOwner(),context,run_id:run.id,parent_run_id:run.id,plan_id:run.plan_id,source_digest:plan.source_digest,node_id:body.node_id,state:'QUEUED',build_id:'build',audit_id:null,publication_id:null,operation_ids:[]};pubs[publication.id]=publication;artifact={publication};}
   else throw Error('Unexpected POST '+path);
   receipts.set(key,structuredClone(artifact));
  }
  if(lose)throw Error('ACK lost');return {context,...structuredClone(artifact),...wrong};
 }
 if(observing)await observing.promise;
 if(url.pathname.endsWith('/projects'))return {context,projects:[{id:'lake_marts',name:'Lake marts'},{id:'approved_second',name:'Other approved project'}]};
 if(url.pathname.endsWith('/plans'))return {context,items:plan?[structuredClone(plan)]:[]};
 if(url.pathname.endsWith('/runs'))return {context,items:run?[structuredClone(run)]:[]};
 if(url.pathname.endsWith('/publications'))return {context,publications:Object.values(pubs).map(x=>structuredClone(x))};
 if(url.pathname.includes('/plans/'))return {context,plan:structuredClone(plan),run_options:makeOptions(plan),graph_options:makeGraphOptions(plan),...wrong};
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

NATIVE_PUBLICATION = r"""
async function nativeSource(){
 await compile();plan.compatibility.version=2;plan.models[1].relation=['sales','marts','formal_alias'];
 plan.actor='engineer';plan.context={...plan.context,selected_role:'data_engineer',grant_revision:2};
 run=makeRun(plan.context,{actor:'engineer',executor:'native_table',selected_node_id:ids[1],source_digest:plan.source_digest,state:'NATIVE_VERIFIED',native_verified:true,native_tests:true,tests_status:'PASSED',publication_allowed:true});
 run.models=[{...run.models[1],state:'NATIVE_VERIFIED'}];run.tests=[run.tests[0]];p.plan=null;
 await dbDbtObserve('run',run.id);assert.equal(dbDbtCanPublish(p.run.models[0]),true);
}
function nativePreview(){return {version:1,origin:'native_table',run_id:run.id,plan_id:plan.id,node_id:ids[1],source_digest:run.source_digest,context:structuredClone(p.context),source_context:structuredClone(run.context),source_actor:'engineer',publisher_actor:dbDbtOwner(),candidate:structuredClone(run.models[0].candidate),quality:{status:'PASSED',count:1,test_ids:['test.first']},target:{catalog:'sales',schema:'marts',name:'formal_alias'},expected_target:{version:1,destination:{database_id:'db',schema_id:'schema',physical_name:'formal_alias'},target:null}};}
const legacyRoute=route;let previewChange=null,previewHold=null,publishError=null;
route=async(path,options)=>{
 if(path.includes('/publication-preview?')){
  if(previewHold)await previewHold.promise;
  const v=nativePreview();if(previewChange)previewChange(v);
  return {context:structuredClone(p.context),preview:v};
 }
 if(path.endsWith('/publish')&&publishError)throw Object.assign(Error('rejected'),publishError);
 const reply=await legacyRoute(path,options);
 if(reply?.publication&&options?.body){const b=JSON.parse(options.body);if(b.expected_target){reply.publication.origin='native_table';reply.publication.expected_target=structuredClone(b.expected_target);run.models[0].publication_id=reply.publication.id;run.publication_allowed=false;}}
 return reply;
};
"""


GRAPH_PUBLICATION = r"""
function graphProofs(r){
 const result=(id,status,failures=null)=>({node_id:id,status,failures,adapter_response:{}});
 const build=r.models.map(m=>({...result(m.unique_id,'success'),...(m.materialized==='view'?{model_type:'view',view_security:'invoker'}:{}),adapter_response:m.materialized==='view'?{code:'CREATE VIEW'}:m.materialized==='table'?{code:'CREATE TABLE',rows_affected:0}:{}})),tests=r.tests.map(x=>({...result(x.unique_id,'pass',0),adapter_response:{code:'SELECT',rows_affected:1}}));
 r.task_receipts={build:{task:'RunTask',completed:true,success:true,selected:r.models.map(m=>m.unique_id).sort(),nodes:build,results:build.filter(x=>r.models.find(m=>m.unique_id===x.node_id).materialized!=='ephemeral')},test:{task:'TestTask',completed:true,success:true,selected:r.tests.map(x=>x.unique_id).sort(),nodes:tests,results:structuredClone(tests)}};
 r.operations=[];
 for(const m of r.models){m.state=m.materialized!=='ephemeral'?'NATIVE_VERIFIED':'EXPANDED';m.operation_ids=[];
  if(m.materialized==='ephemeral'){m.candidate=null;continue;}
  const id='build-'+m.unique_id;m.operation_ids=[id];r.operations.push({id,phase:'build',node_id:m.unique_id,state:'FINISHED',committed:true});
  m.native_receipt={executor:'native_graph',plan_id:r.plan_id,node_id:m.unique_id,phase:'build',digest:'plan-digest',native_verified:true,candidate:structuredClone(m.candidate),sdk_result:structuredClone(build.find(x=>x.node_id===m.unique_id))};
 }
 for(const x of r.tests){const id='test-'+x.unique_id;x.operation_id=id;r.operations.push({id,phase:'test',node_id:x.unique_id,state:'FINISHED',committed:false});x.native_receipt={executor:'native_graph',plan_id:r.plan_id,node_id:x.unique_id,phase:'test',digest:'plan-digest',test_verified:true,status:'PASSED',failures:0,sdk_result:structuredClone(tests.find(y=>y.node_id===x.unique_id))};}
 return r;
}
async function graphSource(){
 await compile();plan.compatibility.version=2;for(const m of plan.models)if(m.materialized==='table')m.table_properties={format:'PARQUET'};
 plan.models[2].depends_on=[ids[1]];
 plan.tests.push({unique_id:'test.source',name:'source-only',depends_on:['source.demo.orders']},{unique_id:'test.unattached',name:'unattached',depends_on:[]});
 plan.actor='engineer';plan.context={...plan.context,selected_role:'data_engineer',grant_revision:2};
 run=graphProofs(makeRun(plan.context,{actor:'engineer',executor:'native_graph',source_digest:plan.source_digest,state:'NATIVE_VERIFIED',native_verified:true,native_tests:true,tests_status:'PASSED',publication_allowed:true}));p.plan=null;
 await dbDbtObserve('run',run.id);assert.equal(p.run.models.length,3);assert.equal(dbDbtCanPublish(p.run.models[1]),true);assert.equal(dbDbtCanPublish(p.run.models[2]),true);
}
function graphPreview(node){const m=run.models.find(m=>m.unique_id===node),pm=plan.models.find(m=>m.unique_id===node);return {version:1,origin:'native_graph',run_id:run.id,plan_id:plan.id,node_id:node,source_digest:run.source_digest,context:structuredClone(p.context),source_context:structuredClone(run.context),source_actor:run.actor,publisher_actor:dbDbtOwner(),candidate:structuredClone(m.candidate),quality:{status:'PASSED',count:run.tests.length,test_ids:run.tests.map(x=>x.unique_id)},target:{catalog:'sales',schema:'marts',name:pm.relation[2]},expected_target:{version:1,destination:{database_id:'db',schema_id:'schema',physical_name:pm.relation[2]},target:null}};}
const graphRoute=route;let graphPreviewChange=null,graphAckChange=null;
route=async(path,options)=>{
 if(path.includes('/publication-preview?')){const v=graphPreview(new URL('http://local'+path).searchParams.get('node_id'));if(graphPreviewChange)graphPreviewChange(v);return {context:structuredClone(p.context),preview:v};}
 const reply=await graphRoute(path,options);
 if(reply?.publication&&options?.body){const b=JSON.parse(options.body);reply.publication.origin='native_graph';reply.publication.expected_target=structuredClone(b.expected_target);run.models.find(m=>m.unique_id===b.node_id).publication_id=reply.publication.id;run.publication_allowed=run.models.some(m=>m.materialized==='table'&&!m.publication_id);if(graphAckChange)graphAckChange(reply.publication);}
 return reply;
};
"""

VIEW_PUBLICATION = r"""
async function viewSource(){
 await graphSource();plan.models[1].materialized='view';plan.models[1].table_properties={};plan.models[1].view_security='INVOKER';
 run=makeRun(plan.context,{actor:plan.actor,executor:'native_graph',source_digest:plan.source_digest,state:'NATIVE_VERIFIED',native_verified:true,native_tests:true,tests_status:'PASSED',publication_allowed:true});
 run.models[1].candidate={kind:'VIEW',ref:{database_id:'db',schema_id:'sandbox',object_id:'run-view',incarnation:1},view_uuid:'11111111-1111-1111-1111-111111111111',version:1,security:'INVOKER'};graphProofs(run);p.plan=null;await dbDbtObserve('run',run.id);assert.equal(dbDbtGraphPublishable(),true);
}
function publicViews(){return run.models.filter(m=>m.materialized==='view').map(m=>({node_id:m.unique_id,candidate:structuredClone(m.candidate),definition_digest:'a'.repeat(64)}));}
const viewRoute=route;let viewPreviewChange=null,viewAckChange=null;
route=async(path,options)=>{const reply=await viewRoute(path,options);if(reply.preview){reply.preview.views=publicViews();if(viewPreviewChange)viewPreviewChange(reply.preview);}if(reply.publication&&options?.body){reply.publication.views=publicViews();pubs[reply.publication.id]=structuredClone(reply.publication);if(viewAckChange)viewAckChange(reply.publication);}return reply;};
function publishedRecord(){
 const v=graphPreview(ids[2]),intent={origin:v.origin,run_id:v.run_id,plan_id:v.plan_id,node_id:v.node_id,source_digest:v.source_digest,source_actor:v.source_actor,source_context:v.source_context,context:v.context,publisher_actor:v.publisher_actor,candidate:v.candidate,quality:run.tests.map(t=>({status:t.state})),expected_target:v.expected_target};
 return {id:'published-1',kind:'publication',state:'PUBLISHED',actor:dbDbtOwner(),context:structuredClone(p.context),origin:'native_graph',source_digest:plan.source_digest,run_id:run.id,parent_run_id:run.id,plan_id:plan.id,node_id:ids[2],expected_target:v.expected_target,views:publicViews(),committed:true,native_audit:{intent,count:4},native_publication:{intent,target:{snapshot:'published-snapshot'}}};
}
"""


class DbtDatabaseUI(unittest.TestCase):
    def javascript(self, body):
        harness.DatabaseUI.javascript(self, SETUP + body)

    def check_operator_abandoned_publication(self, phase):
        self.javascript(GRAPH_PUBLICATION + VIEW_PUBLICATION + r"""
await viewSource();const publication=publishedRecord();publication.state='UNKNOWN';
delete publication.native_audit;delete publication.native_publication;delete publication.committed;
publication.execution_closure='FENCED_READ_ONLY';
publication.execution_recovery={operation_id:'f94f5de1-8ff0-44cf-b178-c1bec29d8760',phase:PHASE,query_outcome:'UNKNOWN'};
publication.operation_ids=['unrelated-child-operation'];pubs[publication.id]=publication;
const original=structuredClone(publication),before=posts().length,saved=worksheets.map(w=>w.sql),baseRoute=route;
route=async(path,options)=>{if(/\/runs\//.test(path))throw Error('source run forbidden');return baseRoute(path,options);};
await dbDbtObserve('publication',publication.id,undefined,true);await dbDbtHistory();
assert.deepEqual(p.standalone,original);assert.deepEqual(p.publicationHistory,[original]);assert.equal(p.intent,null);
for(const lang of ['en','ja']){
 setLanguage(lang);dbDbtRender();
 for(const name of ['publications','history']){
  const panel=text(dbDbtNode(name));assert.ok(panel.includes(lang==='ja'?'結果不明。読取り実行は運用者が停止・放棄済み':'Outcome unknown. Read execution was stopped and abandoned by the operator.'));
  assert.ok(panel.includes(publication.execution_recovery.operation_id));assert.ok(panel.includes((lang==='ja'?'復旧フェーズ':'Recovery phase')+': '+PHASE));
  assert.ok(panel.includes((lang==='ja'?'クエリ結果':'Query outcome')+': UNKNOWN'));assert.doesNotMatch(panel,/Reconcile existing operations|既存処理を照合/);
  assert.match(panel,PHASE==='CLOSED'?(lang==='ja'?/サービスの健全性は未確認/:/Service health is not confirmed/):(lang==='ja'?/置換先は健全で、受付は再開済み/:/Replacement healthy; admission reopened/));
  assert.doesNotMatch(panel,/Row audit: 0|行数監査: 0|Row count: 0|success|成功|Published Snapshot: [^N]/);
 }
 assert.match(text(dbDbtNode('publications')),/Definition digest: a{64}|定義 digest: a{64}/);
 for(const action of ['build','cancel','reconcile']){assert.equal(dbDbtNode(action).disabled,true);await dbDbtSubmit(action);}
 await dbDbtSubmit('publish',publication.node_id);assert.doesNotMatch(text(dbDbtNode('publications')),/Review publication|Audit and publish|公開内容を確認|監査して公開/);
}
assert.deepEqual(p.standalone,original);assert.deepEqual(p.publicationHistory,[original]);assert.equal(p.standalone.state,'UNKNOWN');
assert.equal('native_audit' in p.standalone,false);assert.equal('row_count' in p.standalone,false);assert.equal('audit_count' in p.standalone,false);
assert.equal(posts().length,before);assert.deepEqual(worksheets.map(w=>w.sql),saved);assert.equal(worksheets.length,13);clearTimeout(p.timer);
""".replace('PHASE', repr(phase)))

    def test_operator_abandoned_publication_closed_display(self):
        self.check_operator_abandoned_publication('CLOSED')

    def test_operator_abandoned_publication_open_display(self):
        self.check_operator_abandoned_publication('OPEN')

    def test_operator_abandoned_publication_invalid_contract_and_legacy(self):
        self.javascript(GRAPH_PUBLICATION + VIEW_PUBLICATION + r"""
await viewSource();const good=publishedRecord();good.state='UNKNOWN';delete good.native_audit;delete good.native_publication;
good.execution_closure='FENCED_READ_ONLY';good.execution_recovery={operation_id:'f94f5de1-8ff0-44cf-b178-c1bec29d8760',phase:'CLOSED',query_outcome:'UNKNOWN'};
const changes=[a=>{delete a.execution_closure;delete a.execution_recovery;},a=>delete a.execution_closure,a=>delete a.execution_recovery,
 a=>a.kind='run',a=>a.kind='plan',a=>a.state='PUBLISHED',a=>a.state='FAILED',a=>a.state='CLOSED',a=>a.execution_closure='CLOSED',a=>a.execution_closure='fenced_read_only',
 ...[null,[],true,'CLOSED',42].map(v=>a=>a.execution_recovery=v),
 ...['operation_id','phase','query_outcome'].map(k=>a=>delete a.execution_recovery[k]),a=>a.execution_recovery.extra=true,
 ...['closed','open','UNKNOWN','PUBLISHED','FAILED','',null,1].map(v=>a=>a.execution_recovery.phase=v),
 ...['FINISHED','FAILED','unknown',null,0].map(v=>a=>a.execution_recovery.query_outcome=v),
 ...['not-a-uuid','f94f5de18ff044cfb178c1bec29d8760','{f94f5de1-8ff0-44cf-b178-c1bec29d8760}','f94f5de1-8ff0-44cf-b178-c1bec29d876g','f94f5de1-8ff0-44cf-b178-c1bec29d8760\n','',null,1].map(v=>a=>a.execution_recovery.operation_id=v),
 a=>a.execution_recovery=Object.assign(Object.create({inherited:true}),a.execution_recovery),a=>a.execution_recovery[Symbol('extra')]=true];
const before=posts().length;
for(const change of changes){const bad=structuredClone(good);change(bad);const original=JSON.stringify(bad);p.run=null;p.standalone=bad;p.publicationHistory=[bad];
 for(const lang of ['en','ja']){setLanguage(lang);dbDbtRender();for(const name of ['publications','history']){
  const panel=text(dbDbtNode(name));assert.doesNotMatch(panel,/stopped and abandoned|停止・放棄済み|Recovery operation ID|復旧操作 ID|physically fenced|物理的に隔離済み/);
  if(bad.state==='UNKNOWN')assert.ok(panel.includes(t('Backend outcome unknown. Reconcile existing operations; publication is blocked.')));
 }}assert.equal(JSON.stringify(bad),original);
}
assert.equal(posts().length,before);clearTimeout(p.timer);
""")

    def test_operator_abandoned_publication_role_and_context_purge(self):
        for control, value in [('role', 'data_engineer'), ('database', '')]:
            with self.subTest(control=control):
                self.javascript(GRAPH_PUBLICATION + VIEW_PUBLICATION + r"""
await viewSource();const publication=publishedRecord();publication.state='UNKNOWN';delete publication.native_audit;delete publication.native_publication;
publication.execution_closure='FENCED_READ_ONLY';publication.execution_recovery={operation_id:'f94f5de1-8ff0-44cf-b178-c1bec29d8760',phase:'OPEN',query_outcome:'UNKNOWN'};pubs[publication.id]=publication;
await dbDbtObserve('publication',publication.id,undefined,true);await dbDbtHistory();const before=posts().length;
assert.match(text(dbDbtNode('history')),/stopped and abandoned/);
dbDbtNode(CONTROL).value=VALUE;await dbDbtNode(CONTROL).onchange();
assert.equal(p.standalone,null);assert.deepEqual(p.publications,{});assert.deepEqual(p.publicationHistory,[]);assert.equal(p.run,null);
for(const name of ['publications','history'])assert.doesNotMatch(text(dbDbtNode(name)),/stopped and abandoned|停止・放棄済み|f94f5de1|OPEN/);
assert.equal(JSON.stringify([p.standalone,p.publications,p.publicationHistory,p.run,p.plan,p.runs,p.plans]).includes('execution_recovery'),false);assert.equal(posts().length,before);clearTimeout(p.timer);
""".replace('CONTROL', repr(control)).replace('VALUE', repr(value)))

    def test_view_frozen_review_facts_stay_visible_through_rerenders(self):
        self.javascript(GRAPH_PUBLICATION + VIEW_PUBLICATION + r"""
function visible(n){return n.textContent+' '+(n.tagName==='DETAILS'&&!n.open?n.children.filter(c=>c.tagName==='SUMMARY'):n.children).map(visible).join(' ');}
function checkFacts(){
 const node=dbDbtNode('confirmation'),shown=visible(node);
 for(const fact of ['Reviewed SANDBOX views','Orders staging view · model.demo.first','View UUID: 11111111-1111-1111-1111-111111111111 · v1 · INVOKER','Definition digest: '+'a'.repeat(64)])assert.ok(shown.includes(fact),fact);
 const proof=node.descendants().find(n=>n.tagName==='PRE');assert.ok(proof);assert.equal(proof.parentElement.tagName,'DETAILS');assert.ok(!proof.parentElement.open);
 assert.doesNotMatch(shown,/source_digest|test_ids|object_id/);
}
function rerenderTwice(){
 const before=calls.length,frozen=p.preview,preview=JSON.stringify(p.preview),intent=JSON.stringify(p.intent);
 checkFacts();
 for(let i=0;i<2;i++){const old=dbDbtNode('confirmation').children[0];dbDbtRender();assert.notEqual(dbDbtNode('confirmation').children[0],old);checkFacts();}
 assert.equal(calls.length,before);assert.equal(p.preview,frozen);assert.equal(JSON.stringify(p.preview),preview);assert.equal(JSON.stringify(p.intent),intent);
}
await viewSource();p.plan=dataopsFreeze({...p.plan,models:p.plan.models.map(m=>m.materialized==='view'?{...m,name:'Orders staging view'}:m)});
await dbDbtReview(p.run.models[2]);assert.ok(Object.isFrozen(p.preview.views[0].candidate));rerenderTwice();
clearTimeout(p.timer);
""")

    def test_view_graph_review_canonical_summary_and_uncertain_ack(self):
        self.javascript(GRAPH_PUBLICATION + VIEW_PUBLICATION + r"""
await viewSource();p.plan=dataopsFreeze({...p.plan,models:p.plan.models.map(m=>m.materialized==='view'?{...m,name:'Orders staging view'}:m)});const m=p.run.models[2];await dbDbtReview(m);assert.equal(p.review,m.unique_id);assert.ok(Object.isFrozen(p.preview.views[0].candidate));
assert.match(text(dbDbtNode('confirmation')),/Reviewed SANDBOX views/);assert.match(text(dbDbtNode('confirmation')),/Orders staging view · model.demo.first/);assert.match(text(dbDbtNode('confirmation')),/View UUID: 11111111.*v1.*INVOKER/);assert.match(text(dbDbtNode('confirmation')),/Definition digest: a{64}/);assert.match(text(dbDbtNode('confirmation')),/sales.marts.second/);assert.match(text(dbDbtNode('confirmation')),/Candidate Snapshot: snap-2/);assert.match(text(dbDbtNode('confirmation')),/Reviewed target Snapshot: New target/);assert.match(text(dbDbtNode('confirmation')),/engineer/);
language='ja';dbDbtRender();assert.match(text(dbDbtNode('confirmation')),/確認済み SANDBOX View/);assert.match(text(dbDbtNode('confirmation')),/新規の行数監査 >= 1/);language='en';
viewAckChange=a=>a.views[0].candidate.version=2;await dbDbtSubmit('publish',m.unique_id);const frozen=p.intent,first=posts().at(-1);assert.ok(frozen);assert.ok(Object.isFrozen(frozen.views[0]));assert.equal(frozen.views[0].candidate.version,1);assert.equal(Object.hasOwn(readBody(first),'views'),false);assert.equal(dbDbtCanCloseRejected(),false);assert.match(p.status.textContent,/Receipt not observed/);
await dbDbtSubmit('publish',m.unique_id);assert.equal(posts().at(-1),first);viewAckChange=null;await dbDbtRecover();assert.equal(p.intent,null);assert.equal(posts().at(-1).options.body,first.options.body);assert.equal(posts().at(-1).options.headers['Idempotency-Key'],frozen.key);assert.deepEqual(p.publications[m.unique_id].views,publicViews());clearTimeout(p.timer);
""")

    def test_view_summary_complete_multiple_views_and_pure_view_publication_denied(self):
        self.javascript(GRAPH_PUBLICATION + VIEW_PUBLICATION + r"""
await viewSource();const models=structuredClone(run.models),second=structuredClone(models[1]);second.unique_id='model.demo.another_view';second.candidate.ref.object_id='second-view';second.candidate.view_uuid='22222222-2222-2222-2222-222222222222';models.push(second);const summaries=[...publicViews(),{node_id:second.unique_id,candidate:second.candidate,definition_digest:'b'.repeat(64)}];assert.equal(dbDbtViewsValid(summaries,models,run.context),true);assert.equal(dbDbtViewsValid(summaries.slice(0,1),models,run.context),false);assert.equal(dbDbtViewsValid([...summaries,summaries[0]],models,run.context),false);assert.equal(dbDbtViewsValid([],[],run.context),false);assert.equal(dbDbtViewsValid(undefined,[],run.context),true);
const original=p.plan;plan.models=plan.models.filter(m=>m.materialized!=='table');run.models=run.models.filter(m=>m.materialized!=='table');graphProofs(run);p.plan=structuredClone(plan);assert.equal(dbDbtGraphRunValid(run,p.plan),true);assert.equal(dbDbtGraphPublishable(run),false);p.plan=original;clearTimeout(p.timer);
""")

    def test_publication_summary_syntax_guard_on_history_and_attached_observation(self):
        self.javascript(GRAPH_PUBLICATION + VIEW_PUBLICATION + r"""
await viewSource();const good=publishedRecord();assert.equal(dbDbtArtifact(good,'publication',p.context),true);
for(const change of [a=>a.views=null,a=>a.views=[],a=>a.views=[null],a=>a.views[0].candidate=null,a=>a.views[0].candidate.version=true,a=>a.views[0].candidate.ref.database_id='other',a=>a.views[0].node_id=a.node_id,a=>a.views[0].definition_digest='bad',a=>a.views.push(a.views[0]),a=>a.origin='native_table',a=>a.run_id='other',a=>a.source_digest='',a=>a.context.selected_role='data_engineer']){const bad=structuredClone(good);change(bad);assert.equal(dbDbtArtifact(bad,'publication',p.context),false);}
pubs[good.id]=good;await dbDbtHistory();assert.equal(p.publicationHistory.length,1);pubs[good.id]={...good,views:[null]};await dbDbtHistory();assert.equal(p.publicationHistory[0].id,good.id);assert.match(text(dbDbtNode('history')),/Publication history unavailable/);await dbDbtObserve('publication',good.id);assert.equal(p.standalone,null);assert.equal(p.run.id,run.id);clearTimeout(p.timer);
""")

    def test_standalone_native_early_states_require_admin_without_intent(self):
        self.javascript(GRAPH_PUBLICATION + VIEW_PUBLICATION + r"""
await graphSource();const good=publishedRecord();delete good.views;delete good.native_audit;delete good.native_publication;
for(const origin of ['native_table','native_graph'])for(const state of ['QUEUED','AUDITING']){
 const a={...structuredClone(good),origin,state};assert.equal(dbDbtStandaloneValid(a,plan),true);
 a.context.selected_role='data_engineer';assert.equal(dbDbtStandaloneValid(a,plan),false);
 pubs[a.id]=a;const before=calls.length,saved=p.run;await dbDbtObserve('publication',a.id,undefined,true);
 assert.equal(p.standalone,null);assert.equal(p.run,saved);assert.equal(calls.slice(before).some(c=>/\/runs\//.test(c.path)),false);assert.equal(calls.slice(before).some(c=>c.options?.method==='POST'),false);
}clearTimeout(p.timer);
""")

    def test_same_actor_standalone_plan_cannot_submit_build(self):
        self.javascript(GRAPH_PUBLICATION + VIEW_PUBLICATION + r"""
await viewSource();plan.actor=dbDbtOwner();plan.context=structuredClone(p.context);run.actor=plan.actor;run.context=structuredClone(plan.context);const a=publishedRecord();pubs[a.id]=a;await dbDbtObserve('publication',a.id,undefined,true);assert.equal(p.standalone.id,a.id);assert.equal(p.plan.actor,dbDbtOwner());assert.equal(dbDbtCanBuild(),false);assert.equal(dbDbtNode('build').disabled,true);const before=posts().length;await dbDbtSubmit('build');assert.equal(posts().length,before);clearTimeout(p.timer);
""")

    def test_view_graph_preview_and_sdk_contract_tampering_denied(self):
        self.javascript(GRAPH_PUBLICATION + VIEW_PUBLICATION + r"""
await viewSource();const m=p.run.models[2],original=p.run;
for(const change of [v=>delete v.views,v=>v.views=[],v=>v.views.push(v.views[0]),v=>v.views[0].node_id=ids[2],v=>v.views[0].definition_digest='A'.repeat(64),v=>v.views[0].candidate.snapshot='fake',v=>v.views[0].candidate.security='DEFINER',v=>v.views[0].candidate.version=true,v=>v.views[0].candidate.view_uuid='bad',v=>v.views[0].candidate.ref.database_id='other',v=>v.views[0].candidate.ref.schema_id='raw']){viewPreviewChange=change;await dbDbtReview(m);assert.equal(p.review,null);assert.equal(p.preview,null);assert.equal(p.intent,null);}
for(const change of [r=>delete r.task_receipts.build.nodes[1].model_type,r=>r.task_receipts.build.nodes[1].view_security='INVOKER',r=>r.task_receipts.build.results.pop(),r=>r.task_receipts.build.nodes[1].snapshot='fake',r=>r.task_receipts.build.nodes[1].failures=0,r=>r.models[1].view_security='DEFINER',r=>r.models[1].native_receipt.sdk_result.model_type='table',r=>r.models[1].operation_ids=[],r=>r.tests[0].failure_count=true]){const bad=structuredClone(original);change(bad);assert.equal(dbDbtGraphPublishable(bad),false);}
const savedPlan=p.plan;p.plan=structuredClone(savedPlan);p.plan.models[1].view_security='DEFINER';assert.equal(dbDbtGraphPublishable(original),false);p.plan=savedPlan;viewPreviewChange=null;await dbDbtReview(m);assert.equal(p.review,m.unique_id);assert.equal(posts().filter(c=>c.path.endsWith('/publish')).length,0);clearTimeout(p.timer);
""")

    def test_standalone_view_publication_direct_url_never_reads_source_run(self):
        self.javascript(GRAPH_PUBLICATION + VIEW_PUBLICATION + r"""
await viewSource();const publication=publishedRecord();pubs[publication.id]=publication;const before=posts().length,saved=worksheets.map(w=>w.sql),baseRoute=route;
route=async(path,options)=>{if(!options?.body&&/\/runs\//.test(path))throw Error('source run must never be read');return baseRoute(path,options);};
globalThis.location={href:'http://local/#transformation?keep=yes&dbt_db=db&dbt_publication=published-1&dbt_run=stale%2Frun&dbt_plan=stale%2Fplan'};globalThis.history={replaceState:(_,__,url)=>location.href=String(url)};
const callsBefore=calls.length;await dbDbtRestore();assert.equal(p.standalone.id,publication.id);assert.equal(p.run,null);assert.equal(posts().length,before);assert.ok(calls.slice(callsBefore).some(c=>c.path.includes('/publications/published-1?')));assert.equal(calls.slice(callsBefore).some(c=>/\/runs\//.test(c.path)),false);assert.match(location.href,/dbt_publication=published-1/);assert.doesNotMatch(location.href,/dbt_run=/);assert.match(location.href,/keep=yes/);assert.deepEqual(worksheets.map(w=>w.sql),saved);
const panel=text(dbDbtNode('publications'));assert.match(panel,/Published/);assert.match(panel,/sales.marts.second/);assert.match(panel,/published-snapshot/);assert.match(panel,/Definition digest: a{64}/);assert.match(panel,/Row audit: 4/);assert.doesNotMatch(panel,/Review publication|Audit and publish/);assert.equal(dbDbtNode('build').disabled,true);assert.equal(dbDbtNode('cancel').disabled,true);assert.equal(dbDbtNode('reconcile').disabled,true);
await dbDbtSubmit('build');assert.equal(posts().length,before);dbDbtPurge();assert.equal(p.standalone,null);assert.doesNotMatch(location.href,/dbt_publication=/);clearTimeout(p.timer);
""")

    def test_startup_restores_publication_only_link_readonly_without_source_run(self):
        self.javascript(GRAPH_PUBLICATION + VIEW_PUBLICATION + r"""
const startup=()=>workbenchRestorePage(session);
await viewSource();plan.actor=dbDbtOwner();plan.context=structuredClone(p.context);run.actor=plan.actor;run.context=structuredClone(plan.context);
const publication=publishedRecord();pubs[publication.id]=publication;
const activated=[],baseRoute=route,saved=worksheets.map(w=>w.sql),before=posts().length;
activate=async button=>activated.push(button.id);
globalThis.location={href:'http://local/'};globalThis.history={replaceState:(_,__,url)=>location.href=String(url)};
for(const hash of ['#catalog?keep=yes','#transformation?dbt_db=db','#transformation?dbt_publication=published-1']){
 location.href='http://local/'+hash;const start=calls.length;await startup();assert.equal(calls.length,start);assert.deepEqual(activated,[hash.startsWith('#catalog')?'tab-catalog':'tab-transformation']);activated.length=0;
}
route=async(path,options)=>{if(/\/runs\//.test(path))throw Error('source run unavailable');return baseRoute(path,options);};
location.href='http://local/#transformation?keep=yes&dbt_db=db&dbt_publication=published-1';
assert.equal(dbDbtURL().plan,null);assert.equal(dbDbtURL().run,null);
selectView($('transformation-view-wap'));
assert.equal($('transformation-wap').hidden,false);
const start=calls.length;await startup();assert.deepEqual(activated,['tab-transformation']);
assert.equal($('transformation-models').hidden,false);assert.equal($('transformation-wap').hidden,true);
assert.equal(p.standalone.id,publication.id);assert.equal(p.standalone.state,'PUBLISHED');assert.equal(p.plan.id,publication.plan_id);assert.equal(p.run,null);
const reads=calls.slice(start);assert.ok(reads.some(c=>c.path.includes('/publications/published-1?')));assert.ok(reads.some(c=>c.path.includes('/plans/plan-1?')));assert.equal(reads.some(c=>/\/runs\//.test(c.path)),false);assert.equal(posts().length,before);
assert.match(text(dbDbtNode('publications')),/Published.*published-snapshot/);assert.doesNotMatch(text(dbDbtNode('publications')),/Review publication|Audit and publish/);
for(const action of ['build','cancel','reconcile'])assert.equal(dbDbtNode(action).disabled,true);
assert.equal(dbDbtCanBuild(),false);await dbDbtSubmit('build');assert.equal(posts().length,before);assert.deepEqual(worksheets.map(w=>w.sql),saved);
assert.match(location.href,/keep=yes/);
await dbDbtObserve('plan',plan.id);assert.equal(p.standalone,null);assert.equal(dbDbtCanBuild(),true);assert.equal(posts().length,before);
route=baseRoute;
for(const [query,kind] of [['dbt_plan=plan-1','plan'],['dbt_run=run-1','run']]){
 location.href='http://local/#transformation?dbt_db=db&'+query;const start=calls.length;await startup();
 assert.equal(activated.at(-1),'tab-transformation');assert.equal(activated.length,kind==='plan'?2:3);assert.equal(p.standalone,null);assert.equal(p.plan.id,plan.id);assert.equal(p.run?.id,kind==='run'?run.id:undefined);
 assert.ok(calls.slice(start).some(c=>c.path.includes(kind==='run'?'/runs/run-1?':'/plans/plan-1?')));assert.equal(posts().length,before);
}
clearTimeout(p.timer);
""")

    def test_publication_history_survives_changed_view_run_collection_failure(self):
        self.javascript(GRAPH_PUBLICATION + VIEW_PUBLICATION + r"""
await viewSource();const publication=publishedRecord();pubs[publication.id]=publication;const baseRoute=route;
route=async(path,options)=>{if(!options?.body&&new URL('http://local'+path).pathname==='/api/v1/dbt/runs')throw Object.assign(Error('changed view'),{code:'DBT_VIEW_CHANGED',status:409});if(!options?.body&&/\/runs\//.test(path))throw Error('source run forbidden');return baseRoute(path,options);};
p.runs=[structuredClone(run)];const selected=p.run,before=posts().length;await dbDbtHistory();assert.equal(p.run,selected);assert.equal(p.runs.length,1);assert.equal(p.publicationHistory.length,1);assert.match(text(dbDbtNode('history')),/Run history unavailable.*DBT_VIEW_CHANGED/);assert.match(text(dbDbtNode('history')),/published-1/);assert.doesNotMatch(text(dbDbtNode('history')),/No saved records/);
await dbDbtObserve('publication',publication.id,undefined,true);assert.equal(p.standalone.id,publication.id);assert.equal(p.run,null);assert.equal(posts().length,before);assert.doesNotMatch(text(dbDbtNode('publications')),/Review publication/);clearTimeout(p.timer);
""")

    def test_standalone_publication_rejects_cross_artifact_and_view_mismatches(self):
        self.javascript(GRAPH_PUBLICATION + VIEW_PUBLICATION + r"""
await viewSource();const good=publishedRecord();assert.equal(dbDbtStandaloneValid(good,plan),true);
for(const change of [a=>a.run_id='other',a=>a.plan_id='other',a=>a.node_id=ids[1],a=>a.source_digest='other',a=>a.origin='native_table',a=>a.context.database_id='other',a=>a.native_publication.intent.source_actor='other',a=>a.native_publication.intent.publisher_actor='other',a=>a.native_publication.intent.source_context.selected_role='admin',a=>a.native_publication.intent.context.selected_role='data_engineer',a=>a.views[0].candidate.security='DEFINER',a=>a.views[0].candidate.version=true,a=>delete a.views,a=>a.views[0].definition_digest='bad',a=>{a.native_publication.intent.candidate.kind='VIEW';},a=>{a.native_publication.intent.candidate.ref.incarnation=true;},a=>{a.expected_target.destination.schema_id='raw';a.native_publication.intent.expected_target=a.expected_target;}]){const bad=structuredClone(good);change(bad);assert.equal(dbDbtStandaloneValid(bad,plan),false);}
const saved=p.run;pubs[good.id]=good;await dbDbtObserve('publication',good.id,undefined,true);assert.equal(p.standalone.id,good.id);await dbDbtObserve('plan',plan.id);assert.equal(p.standalone,null);assert.equal(p.run,null);
await dbDbtObserve('publication',good.id,undefined,true);dbDbtNode('role').value='data_engineer';await dbDbtNode('role').onchange();assert.equal(p.standalone,null);assert.equal(p.publicationHistory.length,0);clearTimeout(p.timer);
""")

    def test_view_graph_options_provenance_publication_and_readonly_history(self):
        self.javascript(GRAPH_PUBLICATION + r"""
await compile();plan.compatibility.version=2;
plan.models[1].materialized='view';plan.models[1].view_security='INVOKER';plan.models[1].table_properties={};
plan.models[2].depends_on=[ids[1]];plan.models[2].table_properties={format:'PARQUET'};
await dbDbtObserve('plan',plan.id);assert.ok(dbDbtGraphOptionsValid(p.graphOptions));
dbDbtNode('method').value='native_graph';dbDbtNode('method').onchange();assert.equal(dbDbtCanRun(),true);
assert.match(text(dbDbtNode('graph-counts')),/TABLE: 1 · VIEW: 1 · EPHEMERAL: 1/);
assert.match(text(dbDbtNode('native-help')),/TABLE\/RAW inputs/);assert.match(text(dbDbtNode('native-help')),/VIEW to VIEW and formal VIEWs are unsupported/);
assert.match(text(dbDbtNode('models')),/downstream TABLE and tests/);assert.match(text(dbDbtNode('native-quality-help')),/NOT_RUN.*PASSED.*TABLE/);
const bad=structuredClone(plan);bad.models[2].materialized='view';bad.models[2].view_security='INVOKER';assert.equal(dbDbtGraphOptionsValid(makeGraphOptions(bad),bad),false);
bad.models[2].materialized='table';bad.models[1].view_security='DEFINER';assert.equal(dbDbtGraphOptionsValid(makeGraphOptions(bad),bad),false);
const denied=structuredClone(p.graphOptions);denied.eligible=false;denied.reason_code='UNSUPPORTED_VIEW_DEPENDENCY';assert.ok(dbDbtGraphOptionsValid(denied));p.graphOptions=denied;dbDbtRender();assert.equal(dbDbtCanRun(),false);assert.match(text(dbDbtNode('graph-reason')),/VIEW to VIEW dependencies are unsupported/);language='ja';dbDbtRender();assert.match(text(dbDbtNode('graph-reason')),/VIEW 間の依存は未対応/);language='en';p.graphOptions=makeGraphOptions(plan);
await dbDbtSubmit('build');assert.equal(p.intent,null);assert.equal(readBody(posts().at(-1)).executor,'native_graph');assert.equal(Object.hasOwn(readBody(posts().at(-1)),'node_id'),false);
run=makeRun(plan.context,{executor:'native_graph',source_digest:plan.source_digest,state:'NATIVE_VERIFIED',native_verified:true,native_tests:true,tests_status:'PASSED',publication_allowed:false});
run.models[1].candidate={kind:'VIEW',ref:{database_id:'db',schema_id:'sandbox',object_id:'run-view',incarnation:1},view_uuid:'11111111-1111-1111-1111-111111111111',version:1,security:'INVOKER'};
run=graphProofs(run);await dbDbtObserve('run',run.id);assert.equal(p.run.id,run.id);assert.ok(dbDbtGraphRunValid(p.run,p.plan));
assert.match(text(dbDbtNode('candidates')),/View UUID: 11111111.*VIEW v1 · INVOKER/);assert.equal(Object.hasOwn(p.run.models[1].candidate,'snapshot'),false);
const before=calls.length,postCount=posts().length;
assert.match(text(dbDbtNode('publications')),/no eligible unreserved TABLE/);
run.publication_allowed=true;await dbDbtObserve('run',run.id);assert.equal(dbDbtGraphPublishable(),true);
assert.equal(dbDbtCanPublish(p.run.models[1]),false);assert.equal(dbDbtCanPublish(p.run.models[2]),true);
for(const status of ['NOT_RUN','FAILED']){const bad=structuredClone(run);bad.native_tests=status!=='NOT_RUN';bad.tests_status=status;assert.equal(dbDbtGraphPublishable(bad),false);}
await dbDbtHistory();assert.equal(posts().length,postCount);assert.match(text(dbDbtNode('history')),/Graph quality passed/);
language='ja';dbDbtRender();assert.match(text(dbDbtNode('publications')),/適格な TABLE 候補/);assert.match(text(dbDbtNode('native-help')),/VIEW 間依存と正式な VIEW は未対応/);
clearTimeout(p.timer);
""")

    def test_view_graph_identity_tampering_retains_original_request(self):
        self.javascript(GRAPH_PUBLICATION + r"""
await compile();plan.compatibility.version=2;plan.models[1].materialized='view';plan.models[1].view_security='INVOKER';plan.models[2].depends_on=[ids[1]];
await dbDbtObserve('plan',plan.id);dbDbtNode('method').value='native_graph';dbDbtNode('method').onchange();lose=true;await dbDbtSubmit('build');const original=structuredClone(p.intent);lose=false;
const base=makeRun(plan.context,{executor:'native_graph',source_digest:plan.source_digest,state:'NATIVE_VERIFIED',native_verified:true,native_tests:false,tests:[],tests_status:'NOT_RUN',publication_allowed:false});
base.models[1].candidate={kind:'VIEW',ref:{database_id:'db',schema_id:'sandbox',object_id:'run-view',incarnation:1},view_uuid:'11111111-1111-1111-1111-111111111111',version:1,security:'INVOKER'};graphProofs(base);
const record=structuredClone(base);for(const change of [m=>delete m.candidate.view_uuid,m=>m.candidate.view_uuid='bad',m=>m.candidate.version=true,m=>m.candidate.security='DEFINER',m=>m.candidate.snapshot='fake',m=>m.candidate={ref:m.candidate.ref,snapshot:'fake'},m=>m.candidate.ref.schema_id='raw',m=>m.native_receipt.candidate.version=9]){
 const bad=structuredClone(record);change(bad.models[1]);if(bad.models[1].native_receipt.candidate.version!==9)bad.models[1].native_receipt.candidate=structuredClone(bad.models[1].candidate);
 assert.equal(dbDbtGraphRunValid(bad,plan),false);wrong={run:bad};await dbDbtRecover();assert.deepEqual(p.intent,original);assert.equal(p.run,null);
}
wrong={run:record};await dbDbtRecover();assert.equal(p.intent,null);assert.equal(p.run.models[1].candidate.security,'INVOKER');
assert.ok(posts().filter(c=>c.path.endsWith('/runs')).every(c=>c.options.headers['Idempotency-Key']===original.key&&c.options.body===posts().find(c=>c.path.endsWith('/runs')).options.body));
clearTimeout(p.timer);
""")

    def test_native_graph_options_exact_projection_gates_and_stale_absence(self):
        self.javascript(r"""
await compile();plan.compatibility.version=2;for(const m of plan.models)if(m.materialized==='table')m.table_properties={format:'PARQUET'};
plan.models[2].depends_on=[ids[1]];
plan.tests.push({unique_id:'test.source',name:'source test',depends_on:['source.demo.orders']},{unique_id:'test.unattached',name:'unattached',depends_on:[]});
await dbDbtObserve('plan',plan.id);const good=p.graphOptions;assert.ok(dbDbtGraphOptionsValid(good));assert.ok(Object.isFrozen(good.models[0].depends_on));
dbDbtNode('method').value='native_graph';dbDbtNode('method').onchange();assert.equal(p.node,'');assert.equal(dbDbtCanRun(),true);
assert.equal(dbDbtNode('node').parentElement.hidden,true);assert.equal(dbDbtNode('native-controls').hidden,false);assert.equal(dbDbtNode('native-tests').disabled,false);
assert.match(text(dbDbtNode('graph-counts')),/3 · TABLE: 2 · VIEW: 0 · EPHEMERAL: 1 · Test count: 4/);
assert.match(text(dbDbtNode('graph-reason')),/All saved/);assert.match(text(dbDbtNode('native-quality-help')),/source and unattached/);
for(const change of [o=>o.version=true,o=>o.executor='native_table',o=>o.plan_id='other',o=>o.source_digest='other',o=>o.model_count=true,o=>o.table_count=3,o=>o.model_count=2,o=>o.models.pop(),o=>o.models[1].node_id=ids[0],o=>o.models[1].materialized='view',o=>o.models[1].depends_on=[],o=>o.models[1].depends_on.push(ids[0]),o=>o.testability.count=2,o=>o.testability.count='4',o=>o.eligible='true',o=>o.testability.eligible=1]){
 const bad=structuredClone(good);change(bad);assert.equal(dbDbtGraphOptionsValid(bad),false);p.graphOptions=bad;assert.equal(dbDbtCanRun(),false);
}
const saved=structuredClone(p.plan),cyclic=structuredClone(saved);cyclic.models[0].depends_on=[ids[2]];assert.equal(dbDbtGraphOptionsValid(makeGraphOptions(cyclic),cyclic),false);
const missing=structuredClone(saved);missing.models[0].depends_on=['model.missing'];assert.equal(dbDbtGraphOptionsValid(makeGraphOptions(missing),missing),false);
const view=structuredClone(saved);view.models[1].materialized='view';view.models[1].view_security='INVOKER';assert.equal(dbDbtGraphOptionsValid(makeGraphOptions(view),view),true);
const denied=structuredClone(good);denied.eligible=false;denied.reason_code='DBT_NATIVE_GRAPH_UNSUPPORTED_MATERIALIZATION';assert.ok(dbDbtGraphOptionsValid(denied));p.graphOptions=denied;dbDbtRender();assert.equal(dbDbtCanRun(),false);assert.match(text(dbDbtNode('graph-reason')),/DBT_NATIVE_GRAPH_/);
p.graphOptions=good;dbDbtNode('native-tests').checked=true;dbDbtNode('native-tests').onchange();assert.equal(dbDbtQuality(),true);
const noTests={...saved,tests:[]};p.plan=noTests;p.graphOptions=makeGraphOptions(noTests);dbDbtRender();assert.equal(dbDbtNode('native-tests').disabled,true);assert.equal(dbDbtCanRun(),false);
p.plan=saved;p.graphOptions=good;wrong={graph_options:undefined};await dbDbtObserve('plan',plan.id);assert.equal(p.graphOptions,null);assert.equal(dbDbtCanRun(),false);assert.match(text(dbDbtNode('graph-reason')),/Inspect or recompile/);
wrong=null;await dbDbtObserve('plan',plan.id);assert.ok(p.graphOptions);
observing=deferred();const pending=dbDbtObserve('plan',plan.id);await tick();dbDbtPurge();assert.equal(p.graphOptions,null);observing.resolve();await pending;assert.equal(p.graphOptions,null);assert.equal(p.plan,null);
clearTimeout(p.timer);
""")

    def test_models_compatibility_follows_graph_mode_and_whole_graph_gate(self):
        self.javascript(r"""
await compile();plan.compatibility.version=2;
for(const m of plan.models)if(m.materialized==='table')m.table_properties={format:'PARQUET'};
plan.models[2].depends_on=[ids[1]];
const single=makeOptions(plan);single.nodes[2].reason_code='DBT_NATIVE_MATERIALIZED_DEPENDENCY';
wrong={run_options:single};await dbDbtObserve('plan',plan.id);
const compatibility=()=>Array.from(dbDbtNode('models').querySelector('tbody').children).map(row=>text(row.children[4]).trim());
const header=()=>text(dbDbtNode('models').querySelector('thead'));
const selectMethod=method=>{dbDbtNode('method').value=method;dbDbtNode('method').onchange();};
selectMethod('native_table');const perNode=compatibility();
assert.match(perNode[0],/Native requires a TABLE model/);assert.match(perNode[2],/TABLE\/VIEW dependencies/);
selectMethod('native_graph');assert.equal(dbDbtCanRun(),true);assert.match(header(),/Native graph compatibility/);
assert.deepEqual(compatibility(),['Expanded into graph SQL; no separate SQL operation.','Graph TABLE candidate; TABLE dependencies supported.','Graph TABLE candidate; TABLE dependencies supported.']);
assert.doesNotMatch(text(dbDbtNode('models')),/Native requires a TABLE|TABLE\/VIEW dependencies/);
language='ja';dbDbtRender();assert.match(header(),/Native graph の互換性/);
assert.match(compatibility()[0],/個別の SQL 実行なし/);assert.match(compatibility()[2],/TABLE への依存も対応/);
language='en';selectMethod('native_table');assert.match(header(),/Native saved-plan compatibility/);assert.deepEqual(compatibility(),perNode);
selectMethod('sql_wap');assert.deepEqual(compatibility(),perNode);
selectMethod('native_graph');
const good=p.graphOptions,denied=structuredClone(good);denied.eligible=false;denied.reason_code='DBT_NATIVE_RAW_REQUIRED';
p.graphOptions=denied;dbDbtRender();assert.equal(dbDbtCanRun(),false);
assert.deepEqual(compatibility(),plan.models.map(()=>dbDbtNativeReasons.DBT_NATIVE_RAW_REQUIRED));
for(const options of [null,{...good,model_count:99}]){
 p.graphOptions=options;dbDbtRender();assert.equal(dbDbtCanRun(),false);
 assert.deepEqual(compatibility(),plan.models.map(()=> 'Inspect or recompile the saved plan to load full graph eligibility.'));
}
assert.equal(posts().length,1);clearTimeout(p.timer);
""")

    def test_malformed_graph_options_observation_preserves_existing_methods(self):
        self.javascript(r"""
await compile();plan.compatibility.version=2;await dbDbtObserve('plan',plan.id);
for(const method of ['sql_wap','native_table']){
 dbDbtNode('method').value=method;dbDbtNode('method').onchange();
 if(method==='native_table'){dbDbtNode('node').value=ids[1];dbDbtNode('node').onchange();}
 for(const entry of [null,1,true,'model.demo.first',[],[{}]]){
  const bad=makeGraphOptions(plan);bad.models[1]=entry;
  assert.equal(dbDbtGraphOptionsValid(bad,plan),false);
  wrong={graph_options:bad};await dbDbtObserve('plan',plan.id);
  assert.equal(p.plan.id,plan.id);assert.equal(p.graphOptions,null);
  assert.equal(dbDbtMethod(),method);assert.equal(dbDbtCanRun(),true,method+' remains usable');
  if(method==='native_table')assert.equal(p.node,ids[1]);
 }
}
dbDbtNode('method').value='native_graph';dbDbtNode('method').onchange();assert.equal(dbDbtCanRun(),false);
clearTimeout(p.timer);
""")

    def test_native_graph_lost_wrong_ack_frozen_suite_reload_and_context_recovery(self):
        self.javascript(r"""
await compile();plan.compatibility.version=2;for(const m of plan.models)if(m.materialized==='table')m.table_properties={format:'PARQUET'};
plan.tests.push({unique_id:'test.source',name:'source only',depends_on:['source.demo.orders']},{unique_id:'test.unattached',name:'unattached',depends_on:[]});await dbDbtObserve('plan',plan.id);
dbDbtNode('method').value='native_graph';dbDbtNode('method').onchange();dbDbtNode('native-tests').checked=true;dbDbtNode('native-tests').onchange();
lose=true;gate=deferred();const submitting=dbDbtSubmit('build');await tick();assert.equal(dbDbtNode('role').disabled,true);assert.equal(dbDbtNode('database').disabled,true);gate.resolve();await submitting;gate=null;
const intent=p.intent,first=posts().at(-1),before=posts().length;assert.equal(Object.isFrozen(intent.graphPlan.models),true);assert.equal(Object.isFrozen(intent.body),true);assert.equal(intent.graphPlan.tests.length,4);
assert.deepEqual(readBody(first),{context:p.context,plan_id:plan.id,executor:'native_graph',native_tests:true});assert.ok(!Object.hasOwn(intent.body,'node_id'));
assert.equal(dbDbtNode('method').disabled,true);assert.equal(dbDbtNode('native-tests').disabled,true);assert.equal(dbDbtNode('role').disabled,false);assert.equal(dbDbtNode('database').disabled,false);
p.intent=null;await dbDbtRefresh();assert.deepEqual(p.intent,JSON.parse(JSON.stringify(intent)));assert.equal(posts().length,before);assert.equal(dbDbtMethod(),'native_graph');
lose=false;
for(const change of [r=>r.executor='sql_wap',r=>r.selected_node_id=ids[1],r=>r.native_tests=false,r=>r.native_tests=1,r=>r.plan_id='other',r=>r.source_digest='other',r=>r.actor='other',r=>r.context={...r.context,selected_role:'data_engineer'},r=>r.models.pop(),r=>r.models[1].unique_id=ids[0],r=>r.models[0].materialized='table',r=>r.tests.pop()]){
 const bad=structuredClone(run);change(bad);wrong={run:bad};await dbDbtRecover();assert.ok(p.intent);assert.equal(posts().at(-1).options.body,first.options.body);assert.equal(posts().at(-1).options.headers['Idempotency-Key'],first.options.headers['Idempotency-Key']);
}
wrong=null;const count=posts().length;
inventory.push({...inventory[0],id:'other',display_name:'Other',sql_catalog:'other'});schemasRoute=async(path)=>({items:schemas.map(s=>({...s,database_id:path.includes('/other/')?'other':'db'}))});
dbDbtNode('role').value='data_engineer';await dbDbtNode('role').onchange();dbDbtNode('database').value='other';await dbDbtNode('database').onchange();assert.equal(dbDbtCanRecover(),false);await dbDbtRecover();assert.equal(posts().length,count);
dbDbtNode('role').value='admin';await dbDbtNode('role').onchange();dbDbtNode('database').value='db';await dbDbtNode('database').onchange();assert.equal(dbDbtCanRecover(),true);
language='ja';await dbDbtRecover();assert.equal(p.intent,null);assert.deepEqual(posts().at(-1),first);assert.equal(receipts.size,2);assert.equal(p.run.executor,'native_graph');
const statusKey=uiBindings.get(p.status).key;
assert.equal(typeof statusKey,'string');assert.match(statusKey,/Native graph execution may take a few minutes/);
assert.equal(p.status.textContent,t(statusKey));assert.match(p.status.textContent,/数分/);assert.doesNotMatch(p.status.textContent,/true|false/);
language='en';assert.match(t(statusKey),/This view only polls saved state/);assert.doesNotMatch(t(statusKey),/true|false/);
clearTimeout(p.timer);
""")

    def test_native_graph_build_quality_results_history_and_publication_blocked(self):
        self.javascript(r"""
function visible(n){return n.textContent+' '+(n.tagName==='DETAILS'&&!n.open?n.children.filter(c=>c.tagName==='SUMMARY'):n.children).map(visible).join(' ');}
await compile();plan.compatibility.version=2;for(const m of plan.models)if(m.materialized==='table')m.table_properties={format:'PARQUET'};plan.models[2].depends_on=[ids[1]];await dbDbtObserve('plan',plan.id);
dbDbtNode('method').value='native_graph';dbDbtNode('method').onchange();await dbDbtSubmit('build');assert.equal(p.intent,null);assert.equal(readBody(posts().at(-1)).executor,'native_graph');assert.ok(!Object.hasOwn(readBody(posts().at(-1)),'native_tests'));
run=makeRun(plan.context,{executor:'native_graph',source_digest:plan.source_digest,state:'FAILED',native_verified:true,native_tests:true,tests_status:'FAILED',publication_allowed:false});run.models=run.models.map(m=>({...m,state:m.materialized==='ephemeral'?'EXPANDED':'NATIVE_VERIFIED',operation_ids:m.materialized==='table'?['graph-op-'+m.name]:[],native_receipt:{deep:'closed-proof'}}));run.tests[0].state='FAILED';run.tests[0].failure_count=3;
const before=posts().length;p.plan=null;await dbDbtObserve('run',run.id);assert.equal(posts().length,before);assert.equal(p.run.models.length,3);assert.match(visible(dbDbtNode('run-detail')),/Graph build verified/);assert.match(visible(dbDbtNode('run-detail')),/Quality tests: FAILED/);assert.match(text(dbDbtNode('tests')),/3/);assert.match(text(dbDbtNode('candidates')),/EXPANDED/);assert.match(text(dbDbtNode('candidates')),/snap-1/);assert.match(text(dbDbtNode('candidates')),/graph-op-second/);assert.match(text(dbDbtNode('candidates')),/model.demo.first/);
assert.doesNotMatch(visible(dbDbtNode('run-detail')),/closed-proof/);assert.ok(dbDbtNode('run-detail').descendants().some(n=>n.tagName==='DETAILS'&&!n.open&&text(n).includes('closed-proof')));
assert.equal(dbDbtTestsPassed(),false);for(const m of p.run.models)assert.equal(dbDbtCanPublish(m),false);await dbDbtReview(p.run.models[1]);await dbDbtSubmit('publish',ids[1]);assert.equal(posts().length,before);assert.match(text(dbDbtNode('publications')),/Graph publication unavailable/);assert.ok(dbDbtNode('publications').descendants().filter(n=>n.tagName==='BUTTON').every(n=>n.disabled));
run={...run,native_verified:false,native_tests:false,tests_status:'NOT_RUN',tests:[],models:run.models.map((m,i)=>({...m,state:i===1?'FAILED':i===2?'SKIPPED':'EXPANDED',candidate:null}))};await dbDbtObserve('run',run.id);assert.match(visible(dbDbtNode('run-detail')),/Graph build not verified/);assert.match(visible(dbDbtNode('run-detail')),/NOT_RUN/);assert.match(text(dbDbtNode('candidates')),/SKIPPED/);
await dbDbtHistory();assert.equal(posts().length,before);assert.match(text(dbDbtNode('history')),/native_graph/);assert.match(text(dbDbtNode('history')),/NOT_RUN/);
language='ja';dbDbtRender();assert.match(text(dbDbtNode('publications')),/グラフ公開/);assert.match(text(dbDbtNode('run-detail')),/グラフ構築は未検証/);assert.equal(posts().length,before);
clearTimeout(p.timer);
""")

    def test_graph_per_table_review_quality_receipts_and_readonly_history(self):
        self.javascript(GRAPH_PUBLICATION + r"""
await graphSource();const before=posts().length;
assert.equal(dbDbtCanPublish(p.run.models[0]),false);assert.match(text(dbDbtNode('publications')),/Admin may review each eligible TABLE/);
await dbDbtReview(p.run.models[1]);assert.equal(posts().length,before);
const graphReview=dbDbtNode('confirmation');assert.match(text(graphReview),/engineer/);assert.ok(text(graphReview).includes('Publisher: '+dbDbtOwner()));assert.match(text(graphReview),/Whole graph quality/);assert.match(text(graphReview),/Only the selected TABLE/);assert.match(text(graphReview),/other TABLEs are not published simultaneously/);assert.match(text(graphReview),/row audit >= 1/);assert.match(text(graphReview),/sales.marts.first/);
const frozen=JSON.stringify(p.preview);language='ja';dbDbtRender();assert.match(text(graphReview),/選択した TABLE だけ/);assert.match(text(graphReview),/依存先や他の TABLE は同時に公開しません/);assert.match(text(graphReview),/グラフ全体の品質/);assert.equal(JSON.stringify(p.preview),frozen);
language='en';await dbDbtSubmit('publish',ids[1]);assert.equal(posts().length,before+1);assert.equal(p.intent,null);assert.equal(p.publications[ids[1]].origin,'native_graph');assert.deepEqual(Object.keys(readBody(posts().at(-1))).sort(),['context','expected_target','node_id']);
assert.equal(dbDbtCanPublish(p.run.models[1]),false);assert.equal(dbDbtCanPublish(p.run.models[2]),true);
await dbDbtObserve('run',run.id);assert.equal(dbDbtCanPublish(p.run.models[2]),true);await dbDbtReview(p.run.models[2]);assert.match(text(dbDbtNode('confirmation')),/sales.marts.second/);assert.equal(p.preview.candidate.ref.object_id,'candidate-2');
await dbDbtSubmit('publish',ids[2]);assert.equal(posts().length,before+2);assert.equal(p.intent,null);assert.equal(run.publication_allowed,false);
await dbDbtObserve('run',run.id);assert.equal(dbDbtCanPublish(p.run.models[2]),false);
const count=posts().length;await dbDbtHistory();await dbDbtObserve('publication',p.publications[ids[2]].id);assert.equal(posts().length,count);assert.match(text(dbDbtNode('publications')),/native_graph/);
language='ja';dbDbtRender();assert.equal(posts().length,count);assert.match(text(dbDbtNode('publications')),/グラフ公開不可/);
clearTimeout(p.timer);
""")

    def test_graph_history_other_or_unselected_plan_keeps_saved_status_without_publish_bypass(self):
        self.javascript(GRAPH_PUBLICATION + r"""
await graphSource();const originalPlan=p.plan,originalRun=p.run,before=posts().length;
assert.equal(Object.hasOwn(originalRun,'graph_task_receipts'),false);
p.plan=makePlan(p.context,{id:'other-plan',source_digest:'other-source'});
await dbDbtHistory();assert.equal(posts().length,before);
assert.match(text(dbDbtNode('history')),/NATIVE_VERIFIED/);
assert.match(text(dbDbtNode('history')),/Saved graph quality tests passed\. Open the original run and plan to check publication conditions\./);
assert.doesNotMatch(text(dbDbtNode('history')),/publication_allowed/);
assert.doesNotMatch(text(dbDbtNode('history')),/missing or inconsistent/);
for(const selected of [p.plan,null]){
 p.plan=selected;dbDbtRender();assert.match(dbDbtState(originalRun),/Saved graph quality tests passed\. Open the original run and plan to check publication conditions\./);
 assert.equal(dbDbtGraphPublishable(),false);
 for(const m of originalRun.models){assert.equal(dbDbtCanPublish(m),false);await dbDbtReview(m);await dbDbtSubmit('publish',m.unique_id);}
 assert.equal(posts().length,before);
 assert.ok(dbDbtNode('publications').descendants().filter(n=>n.tagName==='BUTTON').every(n=>n.disabled));
}
language='ja';dbDbtRender();assert.match(text(dbDbtNode('history')),/保存済みの品質テストは合格済みです。元の実行とプランを開いて公開条件を確認してください。/);assert.doesNotMatch(text(dbDbtNode('history')),/publication_allowed/);assert.doesNotMatch(text(dbDbtNode('history')),/不足しているか一致しません/);
language='en';p.plan=originalPlan;assert.equal(dbDbtCanPublish(originalRun.models[1]),true);
p.run=structuredClone(originalRun);p.run.task_receipts.test.success=false;dbDbtRender();
assert.equal(dbDbtCanPublish(p.run.models[1]),false);assert.match(dbDbtState(p.run),/missing or inconsistent/);
await dbDbtReview(p.run.models[1]);await dbDbtSubmit('publish',ids[1]);assert.equal(posts().length,before);clearTimeout(p.timer);
""")

    def test_graph_publication_fail_closed_for_whole_suite_model_and_task_tampers(self):
        self.javascript(GRAPH_PUBLICATION + r"""
await graphSource();const good=p.run,before=posts().length;
const changes=[r=>r.publication_allowed=false,r=>delete r.publication_allowed,r=>r.native_tests=false,r=>r.native_verified=false,r=>r.state='FAILED',r=>r.tests_status='UNKNOWN',r=>r.tests_status='NOT_RUN',r=>r.tests=[],r=>r.tests.pop(),r=>r.tests[1].state='FAILED',r=>r.tests[1].failure_count=1,r=>r.tests[2].failure_count=false,r=>r.tests[3].failure_count='0',r=>r.models[2].state='FAILED',r=>r.models[0].state='NATIVE_VERIFIED',r=>r.models.pop(),r=>r.models[2].unique_id=ids[1],r=>r.models[2].materialized='ephemeral',r=>r.models[2].table_properties={},r=>r.models[1].candidate.ref.schema_id='raw',r=>r.models[1].native_receipt.candidate.snapshot='wrong',r=>r.models[1].native_receipt.node_id=ids[2],r=>r.models[1].native_receipt.executor='native_table',r=>r.models[1].native_receipt.phase='test',r=>r.models[1].operation_ids=['other'],r=>r.operations[0].node_id=ids[2],r=>r.operations[0].state='RUNNING',r=>r.operations[0].committed=false,r=>r.operations.push({id:'extra',phase:'build',node_id:ids[0],state:'FINISHED',committed:true}),r=>r.operations.push({...r.operations[0],id:'extra'}),r=>r.task_receipts.build.nodes[1].adapter_response.unexpected=true,r=>delete r.task_receipts,r=>r.task_receipts.build.completed=false,r=>r.task_receipts.build.success=false,r=>r.task_receipts.test.success=false,r=>r.task_receipts.test.task='RunTask',r=>r.task_receipts.build.selected.pop(),r=>r.task_receipts.test.selected.pop(),r=>r.task_receipts.build.nodes.pop(),r=>r.task_receipts.build.results.push(r.task_receipts.build.nodes[0]),r=>r.task_receipts.test.results[0].status='fail',r=>r.task_receipts.test.nodes[0].failures=false,r=>r.tests[2].native_receipt.test_verified=false,r=>r.tests[3].native_receipt.sdk_result.node_id='test.other'];
for(const change of changes){const bad=structuredClone(good);change(bad);p.run=bad;assert.equal(dbDbtCanPublish(bad.models[1]),false,String(change));await dbDbtReview(bad.models[1]);await dbDbtSubmit('publish',ids[1]);assert.equal(posts().length,before);}
p.run=good;
for(const change of [v=>v.origin='native_table',v=>v.origin='sql_wap',v=>v.run_id='other',v=>v.plan_id='other',v=>v.node_id=ids[2],v=>v.source_digest='other',v=>v.source_actor='other',v=>v.source_context.grant_revision++,v=>v.source_context.selected_role='admin',v=>v.context.schema_id='raw',v=>v.candidate=structuredClone(good.models[2].candidate),v=>v.quality.count--,v=>v.quality.test_ids.pop(),v=>v.quality.test_ids[0]='test.other',v=>v.expected_target.destination.physical_name='second']){
 graphPreviewChange=change;await dbDbtReview(p.run.models[1]);assert.equal(p.review,null,String(change));assert.equal(p.preview,null);assert.equal(posts().length,before);
}
graphPreviewChange=null;await dbDbtReview(p.run.models[1]);assert.equal(p.review,ids[1]);clearTimeout(p.timer);
""")

    def test_graph_publication_original_intent_reload_origin_node_and_target_ack(self):
        self.javascript(GRAPH_PUBLICATION + r"""
await graphSource();await dbDbtReview(p.run.models[2]);lose=true;await dbDbtSubmit('publish',ids[2]);const intent=p.intent,first=posts().at(-1),before=posts().length;
assert.equal(intent.origin,'native_graph');assert.equal(intent.body.node_id,ids[2]);assert.equal(intent.body.expected_target.destination.physical_name,'second');assert.equal(Object.isFrozen(intent.body.expected_target),true);
p.intent=null;await dbDbtRefresh();assert.deepEqual(p.intent,JSON.parse(JSON.stringify(intent)));assert.equal(posts().length,before);lose=false;
for(const change of [a=>a.origin='native_table',a=>a.origin='sql_wap',a=>a.run_id='other',a=>a.parent_run_id='other',a=>a.plan_id='other',a=>a.node_id=ids[1],a=>a.context={...a.context,selected_role:'data_engineer'},a=>a.expected_target.destination.physical_name='first']){
 graphAckChange=change;await dbDbtRecover();assert.ok(p.intent,String(change));assert.equal(posts().at(-1).options.body,first.options.body);assert.equal(posts().at(-1).options.headers['Idempotency-Key'],first.options.headers['Idempotency-Key']);
}
graphAckChange=null;await dbDbtRecover();assert.equal(p.intent,null);assert.deepEqual(posts().at(-1),first);assert.equal(p.publications[ids[2]].origin,'native_graph');assert.equal(receipts.size,2);clearTimeout(p.timer);
""")

    def test_transformation_disclosures_preserve_focus_and_reset_by_plan_identity(self):
        self.javascript(r"""
await compile();const count=posts().length;
for(const lang of ['ja','en']){
 setLanguage(lang);dbDbtRender();const compiled=dbDbtNode('compiled-details');assert.ok(!compiled.open);
 const sql=dbDbtNode('sql').children[0];assert.ok(!sql.open);compiled.open=true;sql.open=true;sql.children[0].focus();
 dbDbtRender();assert.equal(compiled.open,true);assert.equal(dbDbtNode('sql').children[0].open,true);assert.equal(document.activeElement,dbDbtNode('sql').children[0].children[0]);
 compiled.open=false;dbDbtNode('sql').children[0].open=false;
}
const sql=dbDbtNode('sql').children[0];sql.open=true;dbDbtNode('compiled-details').open=true;
p.plan={...p.plan,id:'different-plan',error_code:'DBT_SOURCE_CHANGED'};dbDbtRender();
assert.ok(!dbDbtNode('compiled-details').open);assert.ok(!dbDbtNode('sql').children[0].open);
assert.ok(dbDbtNode('plan-detail').children.some(n=>n.tagName==='P'&&n.textContent==='DBT_SOURCE_CHANGED'));
assert.equal(posts().length,count);
""")

    def test_native_presentation_concise_facts_and_closed_full_proofs(self):
        self.javascript(NATIVE_PUBLICATION + r"""
function visible(n){return n.textContent+' '+(n.tagName==='DETAILS'&&!n.open?n.children.filter(c=>c.tagName==='SUMMARY'):n.children).map(visible).join(' ');}
function proof(n){const pre=n.descendants().filter(c=>c.tagName==='PRE');assert.equal(pre.length,1);assert.equal(pre[0].parentElement.tagName,'DETAILS');assert.ok(!pre[0].parentElement.open);return pre[0];}
await nativeSource();const before=posts().length,m=p.run.models[0];await dbDbtReview(m);
const preview=JSON.stringify(p.preview),reviewNode=dbDbtNode('confirmation');
assert.match(visible(reviewNode),/Target FQN: sales.marts.formal_alias/);
assert.match(visible(reviewNode),/Origin: native_table/);assert.match(visible(reviewNode),/Builder: engineer/);
assert.match(visible(reviewNode),/Quality tests: PASSED · Test count: 1/);
assert.match(visible(reviewNode),/Candidate Snapshot: snap-1/);assert.match(visible(reviewNode),/reviewed absent/);
assert.deepEqual(JSON.parse(proof(reviewNode).textContent),p.preview);
assert.doesNotMatch(visible(reviewNode),/source_digest|test_ids|object_id/);
language='ja';dbDbtRender();assert.match(visible(dbDbtNode('confirmation')),/候補 Snapshot: snap-1/);
assert.match(visible(dbDbtNode('confirmation')),/品質テスト: PASSED/);
assert.equal(JSON.stringify(p.preview),preview);assert.equal(posts().length,before);
language='en';dbDbtRender();await dbDbtSubmit('publish',m.unique_id);
assert.deepEqual(readBody(posts().at(-1)),{context:p.context,node_id:m.unique_id,expected_target:JSON.parse(preview).expected_target});
const saved=p.publications[m.unique_id],intent={source_actor:'engineer',publisher_actor:dbDbtOwner(),candidate:m.candidate,quality:[{status:'PASSED',failures:0,proof:{nested:'full-quality-proof'}}],build:{pins:{nested:'full-build-proof'}}};
for(const state of ['PUBLISHED','COMMITTED_RECORDING_FAILED','UNKNOWN']){
 const publication={...saved,state,committed:state!=='UNKNOWN',operations:[{id:'publish-op',state:'FINISHED'}],native_audit:{intent,count:48,passed:true},native_publication:{intent,target:{snapshot:'published-snapshot'}}};
 p.publications[m.unique_id]=publication;dbDbtRender();const node=dbDbtNode('publications'),raw=proof(node),shown=visible(node);
 assert.match(shown,/sales.marts.formal_alias/);assert.match(shown,/Quality tests: PASSED · Test count: 1/);
 assert.match(shown,/Row audit: 48 · Minimum rows: 1/);assert.ok(shown.includes('Publication status: '+state));
 assert.ok(shown.includes('Committed: '+publication.committed));assert.match(shown,/Published Snapshot: published-snapshot/);
 assert.doesNotMatch(shown,/full-quality-proof|full-build-proof|publish-op/);
 assert.deepEqual(JSON.parse(raw.textContent).native_publication,publication.native_publication);
 raw.parentElement.open=true;raw.parentElement.children[0].focus();dbDbtRender();
 const retained=dbDbtNode('publications').descendants().find(n=>n.tagName==='DETAILS'&&text(n).includes('Native proof details'));assert.equal(retained.open,true);assert.equal(document.activeElement,retained.children[0]);assert.match(visible(dbDbtNode('publications')),/full-build-proof/);
 retained.open=false;
 const operations=node.descendants().find(n=>n.tagName==='DETAILS'&&text(n).includes('Operation IDs'));assert.ok(operations);assert.ok(!operations.open);
}
const standalone=document.createElement('div');dbDbtNativeFacts(standalone,{...JSON.parse(preview),expected_target:{target:{snapshot:'reviewed-snapshot'}}});
assert.match(visible(standalone),/Reviewed target Snapshot: reviewed-snapshot/);
clearTimeout(p.timer);
""")

    def test_native_publication_review_freeze_ack_recovery_and_observation(self):
        self.javascript(NATIVE_PUBLICATION + r"""
await nativeSource();const m=p.run.models[0],before=posts().length;
await dbDbtReview(m);assert.equal(posts().length,before);assert.equal(p.intent,null);assert.equal(p.review,m.unique_id);
assert.equal(Object.isFrozen(p.preview.expected_target.destination),true);
assert.match(text(dbDbtNode('confirmation')),/engineer/);assert.match(text(dbDbtNode('confirmation')),/formal_alias/);
assert.match(text(dbDbtNode('confirmation')),/New target/);assert.match(text(dbDbtNode('confirmation')),/row audit >= 1/);
lose=true;await dbDbtSubmit('publish',m.unique_id);const intent=p.intent,first=posts().at(-1);
assert.deepEqual(readBody(first).expected_target,intent.body.expected_target);assert.deepEqual(readBody(first).context,p.context);assert.equal(readBody(first).context.selected_role,'admin');assert.equal(p.preview.source_context.selected_role,'data_engineer');assert.equal(Object.isFrozen(intent.body.expected_target.destination),true);
assert.equal(dbDbtNode('role').disabled,false);assert.equal(dbDbtNode('node').disabled,true);
const count=posts().length;p.intent=null;await dbDbtRefresh();assert.deepEqual(p.intent,JSON.parse(JSON.stringify(intent)));assert.equal(posts().length,count);
lose=false;const originalRoute=route;route=async(path,options)=>{const reply=await originalRoute(path,options);if(reply.publication)reply.publication.expected_target={};return reply;};
await dbDbtRecover();assert.ok(p.intent);assert.equal(posts().at(-1).options.body,first.options.body);
route=originalRoute;await dbDbtRecover();assert.equal(p.intent,null);assert.equal(posts().at(-1).options.headers['Idempotency-Key'],first.options.headers['Idempotency-Key']);
const saved=p.publications[ids[1]];await dbDbtObserve('run',run.id);assert.equal(dbDbtCanPublish(p.run.models[0]),false);
for(const state of ['UNKNOWN','COMMITTED_RECORDING_FAILED','PUBLISHED']){
 pubs[saved.id]={...saved,state,origin:'native_table',committed:state!=='UNKNOWN',native_publication:{target:{snapshot:'new-snapshot'}}};
 const n=posts().length;await dbDbtObserve('publication',saved.id);assert.equal(posts().length,n);assert.equal(dbDbtCanPublish(p.run.models[0]),false);assert.equal(dbDbtCanCloseRejected(),false);
 assert.match(text(dbDbtNode('publications')),/native_table/);
}
clearTimeout(p.timer);
""")

    def test_native_saved_suite_ignores_other_model_relationship_dependencies(self):
        self.javascript(NATIVE_PUBLICATION + r"""
await nativeSource();
plan.tests.push({unique_id:'test.other_relationship',name:'relationships',test_type:'relationships',attached_node:ids[2],depends_on:[ids[2],ids[1]]});
plan.test_owners={'test.first':ids[1],'test.second':ids[2],'test.other_relationship':ids[2]};
p.plan=dataopsFreeze(structuredClone(plan));
const m=p.run.models[0],before=posts().length;
assert.equal(dbDbtCanPublish(m),true);
for(const change of [v=>v.quality.test_ids=['test.other_relationship'],v=>{v.quality.count=2;v.quality.test_ids.push('test.other_relationship');},v=>v.quality.test_ids=[],v=>v.quality.count=2]){
 previewChange=change;await dbDbtReview(m);assert.equal(p.preview,null);assert.equal(p.review,null);assert.equal(p.intent,null);assert.equal(posts().length,before);
}
const saved=p.run;
for(const tests of [[],[...saved.tests,...saved.tests],saved.tests.map(t=>({...t,state:'FAILED'})),saved.tests.map(t=>({...t,failure_count:1})),saved.tests.map(t=>({...t,failure_count:false}))]){
 p.run={...saved,tests};assert.equal(dbDbtCanPublish(m),false);
}
p.run=saved;previewChange=null;await dbDbtReview(m);assert.equal(p.review,m.unique_id);
assert.deepEqual(p.preview.quality.test_ids,['test.first']);
await dbDbtSubmit('publish',m.unique_id);assert.equal(posts().length,before+1);assert.equal(p.intent,null);assert.equal(p.publications[m.unique_id].origin,'native_table');
clearTimeout(p.timer);
""")

    def test_native_uncertain_publication_original_context_reselection(self):
        self.javascript(NATIVE_PUBLICATION + r"""
await nativeSource();await dbDbtReview(p.run.models[0]);
gate=deferred();lose=true;const pending=dbDbtSubmit('publish',ids[1]);await tick();
assert.equal(dbDbtNode('role').disabled,true);assert.equal(dbDbtNode('database').disabled,true);
gate.resolve();await pending;gate=null;
const intent=p.intent,first=posts().at(-1),before=posts().length;
assert.equal(dbDbtNode('role').disabled,false);assert.equal(dbDbtNode('database').disabled,false);
inventory.push({...inventory[0],id:'other',display_name:'Other',sql_catalog:'other'});
schemasRoute=async(path)=>({items:schemas.map(s=>({...s,database_id:path.includes('/other/')?'other':'db'}))});
dbDbtNode('role').value='data_engineer';await dbDbtNode('role').onchange();
dbDbtNode('database').value='other';await dbDbtNode('database').onchange();
assert.equal(dbDbtAllowed(),true);assert.equal(p.context.database_id,'other');assert.equal(p.context.selected_role,'data_engineer');
assert.equal(dbDbtCanRecover(),false);assert.equal(dbDbtNode('recover').disabled,true);
await dbDbtRecover();await dbDbtSubmit('publish',ids[1]);assert.equal(posts().length,before);assert.equal(p.intent,intent);
assert.equal(dbDbtNode('role').disabled,false);assert.equal(dbDbtNode('database').disabled,false);
dbDbtNode('role').value='admin';await dbDbtNode('role').onchange();assert.equal(dbDbtCanRecover(),false);
dbDbtNode('database').value='db';await dbDbtNode('database').onchange();
assert.equal(dbDbtCanRecover(),true);assert.equal(dbDbtNode('recover').disabled,false);assert.equal(p.intent,intent);
assert.deepEqual(p.context,intent.context);assert.equal(Object.isFrozen(intent.body.expected_target.destination),true);assert.equal(posts().length,before);
lose=false;await dbDbtRecover();assert.equal(posts().length,before+1);assert.deepEqual(posts().at(-1),first);assert.equal(p.intent,null);
clearTimeout(p.timer);
""")

    def test_native_preview_rejects_stale_malformed_and_shows_replace(self):
        self.javascript(NATIVE_PUBLICATION + r"""
await nativeSource();const m=p.run.models[0],before=posts().length;
for(const change of [v=>v.run_id='other',v=>v.plan_id='other',v=>v.node_id=ids[2],v=>v.source_digest='other',v=>v.context.selected_role='data_engineer',v=>v.source_actor='other',v=>v.publisher_actor='engineer',v=>v.candidate.snapshot='other',v=>v.quality.count=0,v=>v.quality.test_ids=[],v=>v.target.name='first',v=>v.expected_target.destination.physical_name='n_candidate',v=>v.expected_target.target={}]){
 previewChange=change;await dbDbtReview(m);assert.equal(p.review,null);assert.equal(p.preview,null);assert.equal(p.intent,null);
}
previewChange=null;previewHold=deferred();const pending=dbDbtReview(m);await tick();p.node=ids[2];previewHold.resolve();await pending;assert.equal(p.review,null);assert.equal(p.pending,false);
previewHold=null;previewChange=v=>{const ref={database_id:'db',schema_id:'schema',object_id:'formal-ref',incarnation:1};v.expected_target={version:1,destination:ref,target:{ref,snapshot:'7',schema_id:0,columns:[{name:'x',type:'BIGINT'}],schema_fingerprint:'f'.repeat(64)}};};
await dbDbtReview(m);assert.equal(p.review,m.unique_id);assert.match(text(dbDbtNode('confirmation')),/Replace reviewed target/);assert.match(text(dbDbtNode('confirmation')),/formal-ref/);
dbDbtNode('node').value=ids[1];dbDbtNode('node').onchange();assert.equal(p.preview,null);assert.equal(p.review,null);
assert.equal(posts().length,before);
for(const change of [{publication_allowed:false},{native_tests:false},{tests_status:'FAILED'},{state:'UNKNOWN'},{cancel_requested:true}]){const saved=p.run;p.run={...saved,...change};assert.equal(dbDbtCanPublish(p.run.models[0]),false);p.run=saved;}
const saved=p.run;p.run={...saved,tests:[]};assert.equal(dbDbtCanPublish(p.run.models[0]),false);p.run=saved;
clearTimeout(p.timer);
""")

    def test_native_definitive_rejection_explicit_close_retains_original_receipt(self):
        for origin, fixture, source in [('native_table', NATIVE_PUBLICATION, 'nativeSource'),
                                        ('native_graph', GRAPH_PUBLICATION, 'graphSource')]:
            for code in ['STAGE_CHANGED', 'DBT_CANDIDATE_CHANGED', 'DBT_PUBLICATION_REVIEW_CHANGED', 'DBT_GRAPH_VIEW_PUBLICATION_UNSUPPORTED', 'DBT_VIEW_CHANGED']:
                with self.subTest(origin=origin, code=code):
                    self.javascript(fixture + r"""
await SOURCE();const m=p.run.models.find(m=>m.unique_id===ids[1]);await dbDbtReview(m);
const baseRoute=route;route=async(path,options)=>{if(options?.method==='POST'&&path.endsWith('/publish'))throw Object.assign(Error('rejected'),{status:409,code:'CODE'});return baseRoute(path,options);};
const before=posts().length;await dbDbtSubmit('publish',m.unique_id);const original=p.intent,first=posts().at(-1);
assert.equal(original.origin,'ORIGIN');assert.equal(original.rejection,'CODE');assert.equal(Object.isFrozen(original),true);assert.equal(Object.isFrozen(original.body.expected_target.destination),true);
assert.equal(dbDbtCanCloseRejected(),true);assert.match(text(p.status),/rejected before acceptance/);
if(['STAGE_CHANGED','DBT_CANDIDATE_CHANGED'].includes('CODE'))assert.match(text(p.status),/Source or target identity changed/);
assert.equal(dbDbtNode('compile').disabled,true);await dbDbtRecover();await dbDbtSubmit('compile');assert.equal(posts().length,before+1);
await dbDbtNode('close-rejected').onclick();assert.equal(p.intent,null);assert.equal(p.rejectedIntent,original);assert.equal(p.review,null);assert.equal(p.preview,null);assert.equal(posts().length,before+1);
assert.deepEqual(readStorage(dbDbtKey+'.rejected',null),JSON.parse(JSON.stringify(original)));assert.equal(p.rejectedIntent.key,first.options.headers['Idempotency-Key']);
assert.match(text(dbDbtNode('receipt')),new RegExp(original.key));assert.match(text(dbDbtNode('receipt')),/CODE/);assert.equal(dbDbtNode('compile').disabled,false);
route=baseRoute;await dbDbtReview(m);assert.ok(p.preview);assert.equal(p.review,m.unique_id);assert.equal(p.intent,null);assert.equal(posts().length,before+1);
assert.equal(calls.filter(c=>c.path.startsWith('/api/v1/queries')&&c.options?.method==='POST').length,0);clearTimeout(p.timer);
""".replace('SOURCE', source).replace('ORIGIN', origin).replace('CODE', code))

    def test_native_unknown_ack_cannot_be_closed_by_later_identity_rejection(self):
        for origin, fixture, source in [('native_table', NATIVE_PUBLICATION, 'nativeSource'),
                                        ('native_graph', GRAPH_PUBLICATION, 'graphSource')]:
            for failure in ['transport', 'missing', 'mismatch', 'forbidden', 'other_rejection']:
                with self.subTest(origin=origin, failure=failure):
                    self.javascript(fixture + r"""
await SOURCE();const m=p.run.models.find(m=>m.unique_id===ids[1]);await dbDbtReview(m);
const baseRoute=route;route=async(path,options)=>{if(options?.method==='POST'&&path.endsWith('/publish')){
 if('FAILURE'==='transport')throw Error('ACK lost');
 if('FAILURE'==='forbidden')throw Object.assign(Error('denied'),{status:403,code:'FORBIDDEN'});
 if('FAILURE'==='other_rejection')throw Object.assign(Error('rejected'),{status:409,code:'TARGET_CHANGED'});
 const reply=await baseRoute(path,options);if('FAILURE'==='missing')delete reply.publication;else reply.publication.origin='sql_wap';return reply;
}return baseRoute(path,options);};
await dbDbtSubmit('publish',m.unique_id);const original=p.intent,first=posts().at(-1);assert.ok(original);assert.equal(dbDbtCanCloseRejected(),false);dbDbtCloseRejected();assert.equal(p.intent,original);
if(p.invalid)await dbDbtRefresh();
for(const code of ['STAGE_CHANGED','DBT_CANDIDATE_CHANGED','DBT_PUBLICATION_REVIEW_CHANGED','DBT_GRAPH_VIEW_PUBLICATION_UNSUPPORTED','DBT_VIEW_CHANGED']){
 route=async(path,options)=>{if(options?.method==='POST'&&path.endsWith('/publish'))throw Object.assign(Error('rejected'),{status:409,code});return baseRoute(path,options);};
 await dbDbtRecover();assert.equal(dbDbtCanCloseRejected(),false);dbDbtCloseRejected();assert.equal(p.intent,original);assert.equal(p.intent.rejection,undefined);assert.deepEqual(posts().at(-1),first);
 const count=posts().length;await dbDbtSubmit('compile');await dbDbtReview(m);assert.equal(posts().length,count);assert.equal(dbDbtNode('compile').disabled,true);
}
route=baseRoute;await dbDbtRecover();assert.equal(p.intent,null);assert.deepEqual(posts().at(-1),first);assert.equal(p.publications[m.unique_id].origin,'ORIGIN');assert.equal(dbDbtCanCloseRejected(),false);
assert.equal(calls.filter(c=>c.path.startsWith('/api/v1/queries')&&c.options?.method==='POST').length,0);clearTimeout(p.timer);
""".replace('SOURCE', source).replace('ORIGIN', origin).replace('FAILURE', failure))

    def test_native_accepted_publication_failure_is_not_a_closable_rejection(self):
        for origin, fixture, source in [('native_table', NATIVE_PUBLICATION, 'nativeSource'),
                                        ('native_graph', GRAPH_PUBLICATION, 'graphSource')]:
            for state in ['UNKNOWN', 'FAILED', 'COMMITTED_RECORDING_FAILED']:
                with self.subTest(origin=origin, state=state):
                    self.javascript(fixture + r"""
await SOURCE();const m=p.run.models.find(m=>m.unique_id===ids[1]);await dbDbtReview(m);const baseRoute=route;
route=async(path,options)=>{const reply=await baseRoute(path,options);if(reply.publication&&options?.method==='POST')Object.assign(reply.publication,{state:'STATE',error_code:'STAGE_CHANGED'});return reply;};
await dbDbtSubmit('publish',m.unique_id);const saved=p.publications[m.unique_id],before=posts().length;
assert.equal(p.intent,null);assert.equal(saved.state,'STATE');assert.equal(saved.origin,'ORIGIN');assert.equal(dbDbtCanCloseRejected(),false);
dbDbtCloseRejected();assert.equal(p.publications[m.unique_id],saved);assert.equal(dbDbtCanPublish(m),false);await dbDbtSubmit('publish',m.unique_id);assert.equal(posts().length,before);
assert.equal(calls.filter(c=>c.path.startsWith('/api/v1/queries')&&c.options?.method==='POST').length,0);clearTimeout(p.timer);
""".replace('SOURCE', source).replace('ORIGIN', origin).replace('STATE', state))

    def test_view_changed_preview_guidance_has_no_pending_intent(self):
        self.javascript(GRAPH_PUBLICATION + VIEW_PUBLICATION + r"""
await viewSource();const m=p.run.models[2],before=posts().length,baseRoute=route;
route=async(path,options)=>{if(path.includes('/publication-preview?'))throw Object.assign(Error('changed'),{status:409,code:'DBT_VIEW_CHANGED'});return baseRoute(path,options);};
for(const lang of ['en','ja']){setLanguage(lang);await dbDbtReview(m);assert.equal(p.preview,null);assert.equal(p.review,null);assert.equal(p.intent,null);assert.equal(dbDbtCanCloseRejected(),false);assert.equal(posts().length,before);assert.match(text(p.status),/DBT_VIEW_CHANGED/);assert.ok(text(p.status).includes(lang==='en'?'Build and test a fresh graph':'新しい graph を構築・テスト'));}clearTimeout(p.timer);
""")

    def test_view_changed_only_exact_409_fresh_publish_is_closable(self):
        for status, code in [(400, 'DBT_VIEW_CHANGED'), (500, 'DBT_VIEW_CHANGED'), (409, 'OTHER_CONFLICT')]:
            with self.subTest(status=status, code=code):
                self.javascript(GRAPH_PUBLICATION + VIEW_PUBLICATION + r"""
await viewSource();const m=p.run.models[2];await dbDbtReview(m);const baseRoute=route;
route=async(path,options)=>{if(options?.method==='POST'&&path.endsWith('/publish'))throw Object.assign(Error('changed'),{status:STATUS,code:'CODE'});return baseRoute(path,options);};
await dbDbtSubmit('publish',m.unique_id);const original=p.intent;assert.ok(original);assert.equal(dbDbtCanCloseRejected(),false);dbDbtCloseRejected();assert.equal(p.intent,original);assert.equal(p.intent.rejection,undefined);
route=async(path,options)=>{if(options?.method==='POST'&&path.endsWith('/publish'))throw Object.assign(Error('changed'),{status:409,code:'DBT_VIEW_CHANGED'});return baseRoute(path,options);};
await dbDbtRecover();assert.equal(p.intent,original);assert.equal(dbDbtCanCloseRejected(),false);clearTimeout(p.timer);
""".replace('STATUS', str(status)).replace('CODE', code))

    def test_native_preview_stage_changed_uses_existing_identity_guidance_in_both_languages(self):
        for fixture, source in [(NATIVE_PUBLICATION, 'nativeSource'), (GRAPH_PUBLICATION, 'graphSource')]:
            with self.subTest(source=source):
                self.javascript(fixture + r"""
await SOURCE();const m=p.run.models.find(m=>m.unique_id===ids[1]),before=posts().length,baseRoute=route;
route=async(path,options)=>{if(path.includes('/publication-preview?'))throw Object.assign(Error('changed'),{status:409,code:'STAGE_CHANGED'});return baseRoute(path,options);};
for(const lang of ['en','ja']){setLanguage(lang);await dbDbtReview(m);assert.equal(p.preview,null);assert.equal(p.review,null);assert.equal(p.intent,null);assert.equal(posts().length,before);assert.match(text(p.status),/STAGE_CHANGED/);
 assert.ok(text(p.status).includes(lang==='en'?'Source or target identity changed. Refresh context and compile a new plan after inspecting the schemas.':'ソースまたは公開先の識別情報が変わりました。Schema を確認し、コンテキストを更新して新しいプランをコンパイルしてください。'));
}clearTimeout(p.timer);
""".replace('SOURCE', source))

    def test_quality_optional_metadata_uncheck_and_saved_projection(self):
        self.javascript(r"""
await compile();plan.compatibility.version=2;await dbDbtObserve('plan',plan.id);p.method='native_table';p.node=ids[1];dbDbtRender();
assert.equal(dbDbtNode('native-tests').checked,false);assert.equal(dbDbtNode('build').disabled,false);
const old=structuredClone(p.runOptions);
for(const metadata of [undefined,null,{},[],{eligible:1,count:2,reason_code:null},{eligible:true,count:0,reason_code:null},{eligible:true,count:-1,reason_code:null},{eligible:true,count:1,reason_code:'bad'},{eligible:true,count:1.5,reason_code:null},{eligible:false,count:0,reason_code:null}]){
 p.runOptions=structuredClone(old);p.runOptions.nodes[1].testability=metadata;
 assert.equal(dbDbtOptionsValid(p.runOptions),true);p.nativeTests=true;dbDbtRender();assert.equal(dbDbtNode('build').disabled,true);
 assert.equal(dbDbtNode('native-tests').disabled,false);dbDbtNode('native-tests').checked=false;dbDbtNode('native-tests').onchange();assert.equal(dbDbtNode('build').disabled,false);
}
p.runOptions=structuredClone(old);p.runOptions=structuredClone(p.runOptions);p.runOptions.nodes[1].testability={eligible:true,count:2,reason_code:null};
p.runOptions.nodes[2]={node_id:ids[2],eligible:true,reason_code:null,testability:{eligible:false,count:3,reason_code:'DBT_NATIVE_TEST_MATERIALIZED_DEPENDENCY'}};
p.nativeTests=true;dbDbtRender();assert.equal(dbDbtNode('build').disabled,false);
assert.match(dbDbtNode('native-tests-reason').textContent,/2/);
dbDbtNode('node').value=ids[2];dbDbtNode('node').onchange();assert.equal(p.nativeTests,true);assert.equal(dbDbtNode('build').disabled,true);
assert.match(dbDbtNode('native-tests-reason').textContent,/another TABLE\/VIEW/);assert.equal(dbDbtNode('native-tests').disabled,false);
const before=posts().length;await dbDbtSubmit('build');assert.equal(posts().length,before);
dbDbtNode('native-tests').checked=false;dbDbtNode('native-tests').onchange();assert.equal(dbDbtNode('build').disabled,false);
for(const code of ['DBT_NATIVE_TEST_RECOMPILE_REQUIRED','DBT_NATIVE_TEST_EMPTY_SUITE','DBT_NATIVE_TEST_NEW_REASON']){
 p.runOptions.nodes[2].testability={eligible:false,count:0,reason_code:code};dbDbtRender();assert.ok(dbDbtNode('native-tests-reason').textContent.length>10);
}
p.runOptions={...old,source_digest:'wrong'};p.nativeTests=true;dbDbtRender();assert.equal(dbDbtNode('build').disabled,true);
""")

    def test_quality_flag_resets_and_localized_controls(self):
        self.javascript(r"""
await compile();plan.compatibility.version=2;await dbDbtObserve('plan',plan.id);p.method='native_table';p.node=ids[1];
p.runOptions=structuredClone(p.runOptions);p.runOptions.nodes[1].testability={eligible:true,count:2,reason_code:null};p.nativeTests=true;dbDbtRender();
for(const lang of ['ja','en']){
 setLanguage(lang);dbDbtRender();
 for(const selected of [true,false,true]){
  dbDbtNode('native-tests').checked=selected;dbDbtNode('native-tests').onchange();assert.equal(p.nativeTests,selected);
  assert.equal(dbDbtNode('build').textContent,t(selected?'Run native TABLE and test':'Run native TABLE'));
  assert.equal(text(dbDbtNode('native-help')).trim(),lang==='ja'
   ?selected?'同じ DB の SANDBOX に新しい候補を1モデル作成し、付属する品質テスト一式を実行します。品質 PASSED 後、管理者が MARTS 公開を明示確認し、行数監査 >= 1 を行います。実行時に現在のプロジェクト・ポリシー・ソースを再検証します。':'同じ DB の SANDBOX に新しい候補を1モデル作成します。テストは実行せず、公開できません。実行時に現在のプロジェクト・ポリシー・ソースを再検証します。'
   :selected?'Creates one new same-DB SANDBOX candidate and runs the complete attached quality suite. After quality PASSED, admin may explicitly review MARTS publication with row audit >= 1. Execution revalidates current project, policy and sources.':'Creates one new same-DB SANDBOX candidate. Tests are not run; publication is unavailable. Execution revalidates current project, policy and sources.');
  assert.ok(text(dbDbtNode('native-tests').parentElement).includes(t('Run quality tests')));
 }
}
setLanguage('en');for(const name of ['project','source','target','materialization','method']){p.nativeTests=true;dbDbtNode(name).onchange();assert.equal(p.nativeTests,false);}
p.nativeTests=true;dbDbtSelectPlan({...plan,id:'another'},null);assert.equal(p.nativeTests,false);
p.nativeTests=true;dbDbtSelectPlan({...plan,source_digest:'different'},null);assert.equal(p.nativeTests,false);
p.nativeTests=true;dbDbtPurge();assert.equal(p.nativeTests,false);assert.equal(dbDbtNode('native-tests').checked,false);
await dbDbtRefresh();p.nativeTests=true;p.role='bi_viewer';dbDbtPurge();assert.equal(p.nativeTests,false);assert.equal(dbDbtNode('native-controls').hidden,true);assert.equal(dbDbtNode('native-tests').disabled,true);
""")

    def test_quality_true_false_frozen_ack_mismatch_lost_reload_no_replay(self):
        self.javascript(r"""
await compile();plan.compatibility.version=2;await dbDbtObserve('plan',plan.id);
for(const selected of [true,false]){
 p.method='native_table';p.node=ids[1];p.runOptions=structuredClone(p.runOptions);p.runOptions.nodes[1].testability={eligible:true,count:2,reason_code:null};p.nativeTests=selected;lose=true;
 await dbDbtSubmit('build');const original=p.intent,first=posts().at(-1),before=posts().length;
 assert.equal(original.body.native_tests,selected?true:undefined);assert.equal(dbDbtNode('native-tests').disabled,true);
 p.intent=null;await dbDbtRefresh();assert.deepEqual(p.intent,JSON.parse(JSON.stringify(original)));assert.equal(posts().length,before);assert.equal(dbDbtNode('native-tests').checked,selected);
 lose=false;const saved=structuredClone(run);
 for(const flag of [null,0,1,'true',!selected,...(selected?[undefined]:[])]){
  wrong={run:{...saved,native_tests:flag}};await dbDbtRecover();assert.ok(p.intent);assert.equal(p.run,null);
  assert.equal(posts().at(-1).options.body,first.options.body);assert.equal(posts().at(-1).options.headers['Idempotency-Key'],first.options.headers['Idempotency-Key']);
 }
 wrong=null;await dbDbtRecover();assert.equal(p.intent,null);assert.equal(p.run.native_tests===true,selected);
 await dbDbtObserve('plan',plan.id);
}
assert.equal(receipts.size,3);clearTimeout(p.timer);
""")

    def test_quality_stale_options_response_never_enables_action(self):
        self.javascript(r"""
await compile();plan.compatibility.version=2;await dbDbtObserve('plan',plan.id);
p.method='native_table';p.node=ids[1];p.nativeTests=true;
p.runOptions=structuredClone(p.runOptions);p.runOptions.nodes[1].testability={eligible:true,count:2,reason_code:null};dbDbtRender();assert.equal(dbDbtNode('build').disabled,false);
const hold=deferred();observing=hold;const reading=dbDbtObserve('plan',plan.id);await tick();
assert.equal(p.runOptions,null);assert.equal(dbDbtNode('build').disabled,true);assert.equal(dbDbtNode('native-tests').disabled,true);
p.role='bi_viewer';dbDbtPurge();hold.resolve();await reading;
assert.equal(p.runOptions,null);assert.equal(p.nativeTests,false);assert.equal(dbDbtNode('build').disabled,true);assert.equal(posts().length,1);
""")

    def test_quality_saved_result_independent_of_draft_and_materialization(self):
        self.javascript(r"""
await compile();plan.compatibility.version=2;await dbDbtObserve('plan',plan.id);
p.method='native_table';p.node=ids[1];p.runOptions=structuredClone(p.runOptions);p.runOptions.nodes[1].testability={eligible:true,count:2,reason_code:null};p.nativeTests=true;
await dbDbtSubmit('build');assert.equal(readBody(posts().at(-1)).native_tests,true);p.nativeTests=false;p.method='sql_wap';
for(const status of ['QUEUED','RUNNING','PASSED','FAILED','ERROR','CANCELED','UNKNOWN']){
 run={...run,native_tests:true,native_verified:true,tests_status:status,state:status==='PASSED'?'NATIVE_VERIFIED':status==='RUNNING'?'TESTING':'FAILED',tests:[{unique_id:'test.first',name:'not_null',state:status,failure_count:status==='FAILED'?3:0,operation_id:'test-op'}]};
 await dbDbtObserve('run',run.id);assert.match(text(dbDbtNode('run-detail')),new RegExp(status));
 assert.match(text(dbDbtNode('run-detail')),/Native TABLE verified/);assert.match(text(dbDbtNode('run-detail')),/Publication unavailable/);
 assert.equal(dbDbtCanPublish(p.run.models[1]),false);assert.equal(dbDbtNode('publications').children.filter(row=>row.children.some(n=>n.tagName==='BUTTON'&&!n.disabled&&n.textContent===t('Review publication'))).length,0);assert.match(text(dbDbtNode('tests')),/test-op/);
 if(status==='FAILED')assert.match(text(dbDbtNode('run-detail')),/Failure count: 3/);
 await dbDbtHistory();assert.match(text(dbDbtNode('history')),new RegExp(status));
}
run={...run,native_tests:undefined,tests_status:undefined,state:'NATIVE_VERIFIED'};p.nativeTests=true;await dbDbtObserve('run',run.id);
assert.match(text(dbDbtNode('run-detail')),/tests not run; publication unavailable/);clearTimeout(p.timer);
""")

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
wrong={publication:{...original,parent_run_id:'other'}};await dbDbtObserve('publication',original.id);assert.equal(p.publications[ids[1]],original);assert.match(p.status.textContent,/Observation failed|Run history unavailable/);
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
assert.equal(p.run,null);assert.deepEqual(p.publications,{});assert.equal(dbDbtNode('publications').children.filter(row=>row.children.some(n=>n.tagName==='BUTTON'&&!n.disabled&&n.textContent===t('Review publication'))).length,0);
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
 assert.equal(rows[1].children[3].textContent,'View UUID: '+run.models[1].candidate.view_uuid+' · VIEW v7 · INVOKER');
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
assert.equal(rows[1].children[3].textContent,'View UUID: '+t('Not recorded')+' · VIEW '+t('Not recorded'));
assert.equal(rows[2].children[3].textContent,t('Not recorded'));
""")

    def test_operation_ids_native_details_collapsed_with_counts_and_visible_gates(self):
        self.javascript(r"""
await build();
run.operations=Array.from({length:12},(_,i)=>({id:'full-operation-'+i+'-<img src=x onerror=attack()>',state:i<10?'FINISHED':'FAILED'}));
await dbDbtObserve('run','run-1');
for(const lang of ['en','ja']){
 setLanguage(lang);dbDbtRender();
 const area=dbDbtNode('run-detail'),detail=area.children.find(n=>n.tagName==='DETAILS'&&/Operation IDs|操作 ID/.test(text(n.children[0])));
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
assert.equal(p.plan.state,'COMPILING');assert.match(p.status.textContent,/Observation failed|Run history unavailable/);assert.equal(posts().length,count);
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
 wrong={run:replacement};await dbDbtObserve('run','run-1');assert.equal(p.run,original);assert.match(p.status.textContent,/Observation failed|Run history unavailable/);
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
await dbDbtHistory();assert.deepEqual(p.plans,[]);assert.deepEqual(p.runs,[]);assert.match(p.status.textContent,/Observation failed|Run history unavailable/);
await dbDbtObserve('plan',plan.id);assert.equal(p.plan,null);
await dbDbtObserve('run',run.id);assert.equal(p.run,null);assert.equal(dbDbtNode('sql').children.length,0);
plan={...savedPlan,context:{...savedPlan.context,selected_role:'data_engineer',grant_revision:99}};
run={...savedRun,context:{...plan.context}};
await dbDbtObserve('run',run.id);assert.equal(p.run,null);assert.match(p.status.textContent,/Observation failed|Run history unavailable/);
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
dbDbtPurge();await dbDbtRestore();assert.equal(p.run,null);assert.equal(p.plan,null);assert.match(p.status.textContent,/Observation failed|Run history unavailable/);assert.equal(posts().length,3);
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
const detail=dbDbtNode('plan-detail');assert.equal(dbDbtNode('compatibility').parentElement,detail);assert.ok(detail.children.some(n=>n.tagName==='DETAILS'&&!n.open));
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
