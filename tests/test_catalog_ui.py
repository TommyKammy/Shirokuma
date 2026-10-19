"""Catalog inspector behavior in Node VM, without a server or browser."""
import json
from pathlib import Path
import re
import subprocess
import unittest

HTML = (Path(__file__).resolve().parents[1] / 'tools/local_experiment/workbench/index.html').read_text()
SCRIPT = re.search(r'<script>([\s\S]*?)</script>', HTML)[1]


def function(name):
    start = SCRIPT.index('function ' + name + '(')
    if SCRIPT[max(0, start - 6):start] == 'async ':
        start -= 6
    end = re.search(r'\n(?:async )?function ', SCRIPT[start + 1:])
    source = SCRIPT[start:start + 1 + end.start()] if end else SCRIPT[start:]
    return source.split("\n$('", 1)[0]


class CatalogUI(unittest.TestCase):
    def javascript(self, body):
        names = ['catalogIdentity', 'catalogKey', 'cachedCatalogDetail',
                 'invalidateCatalogDetails', 'resetCatalogIdentity', 'catalogMutation',
                 'observeCatalogRuns', 'fetchCatalogDetail', 'describeTable', 'loadCatalog',
                 'resetCatalogSelection', 'selectInspector', 'cloneTable', 'validateCatalogSession', 'api']
        state = SCRIPT[SCRIPT.index('let columnRequest='):SCRIPT.index('function catalogIdentity(')]
        source = r"""
const assert=require('node:assert/strict');
let currentRole='admin',catalogMetadata={},selectedTable=null,now=0,calls=[],pending=[],renders=[],loading=[];
class Node {
 constructor(){this.children=[];this.dataset={};this.value='';this.textContent='';this.hidden=false;this.disabled=false;}
 replaceChildren(...children){this.children=children;this.textContent='';delete this.rows;delete this.trs;}
 append(...children){this.children.push(...children);}
 setAttribute(){} querySelectorAll(){return this.trs||[];}
}
const nodes={},$=id=>nodes[id]??=new Node();
$('role-selector').options=['bi_viewer','data_engineer','admin'].map(value=>({value,disabled:false}));
const applyRole=()=>resetCatalogIdentity();
const document={createElement:()=>new Node()};
const inspectorTabs=['columns','preview','profile','ddl','snapshots','lineage'].map(section=>Object.assign(new Node(),{dataset:{inspect:section}}));
const ui=(node,key)=>{node.textContent=key;if(key==='Loading…')loading.push(node);};
const t=key=>key,message=(id,text)=>{$(id).textContent=text;$(id).hidden=!text;};
const table=(node,columns,rows)=>{node.rows=rows;node.trs=rows.map(()=>({lastElementChild:new Node()}));renders.push({node,rows});};
const actionButton=(label,task)=>({label,task}),confirm=()=>true;
const renderData=(node,data)=>table(node,data?.columns||[],data?.rows||[]);
const renderLineage=(node,up,name)=>{node.textContent=name;};
let trees=0;const renderCatalogTrees=()=>{trees++;$('catalog-tree').replaceChildren();$('worksheet-tree').replaceChildren();};
const fetch=(path,options)=>{calls.push({path,options});return new Promise((resolve,reject)=>pending.push({path,resolve:(data,status=200)=>resolve({ok:status<400,status,json:async()=>data}),reject}));};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const detail=(value='ok')=>({metadata:{columns:[[value]],preview:{columns:['x'],rows:[[value]]},profile:{row_count:1},definition:[[value]],snapshots:{rows:[]},lineage_detail:{upstream:[]}}});
""" + state + '\n' + '\n'.join(function(name) for name in names) + '\n' + r"""
Date.now=()=>now;
const session={username:'owner',authenticated:true,maximum_role:'admin',role:'admin'};
catalogSession=JSON.stringify([session.username,session.authenticated,session.maximum_role]);
""" + '\n(async()=>{\n' + body + '\n})().catch(error=>{console.error(error);process.exitCode=1;});'
        runner = "require('node:vm').runInNewContext(" + json.dumps(source) + ", {require,console,process,setImmediate});"
        result = subprocess.run(['node', '-e', runner], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_lazy_tabs_and_validated_cached_reselection(self):
        self.javascript(r"""
let first=describeTable('raw','a');assert.equal(calls.length,1);assert.match(calls[0].path,/detail=columns$/);
pending.shift().resolve(detail('a'));await first;
let count=loading.length;first=describeTable('raw','a');
assert.equal(loading.length,count);assert.equal($('table-columns').rows,undefined);
assert.equal(pending[0].path,'/api/session');pending.shift().resolve(session);await first;
assert.deepEqual($('table-columns').rows,[['a']]);assert.equal(calls.length,2);
for(const section of ['preview','profile','ddl','snapshots','lineage']){
 const load=selectInspector(inspectorTabs.find(tab=>tab.dataset.inspect===section));
 assert.match(calls.at(-1).path,new RegExp('detail='+section+'$'));
 pending.shift().resolve(detail());await load;
}
assert.equal(calls.length,7);assert.equal(calls.filter(c=>c.path==='/api/session').length,1);
""")

    def test_inflight_absolute_ttl_eviction_and_failure_retry(self):
        self.javascript(r"""
const a=fetchCatalogDetail('raw.a','columns'),b=fetchCatalogDetail('raw.a','columns');assert.equal(a,b);
pending.shift().resolve(detail());await a;now=14999;
let hit=fetchCatalogDetail('raw.a','columns');pending.shift().resolve(session);await hit;assert.equal(calls.length,2);
now=15000;let load=fetchCatalogDetail('raw.a','columns');assert.equal(calls.length,3);
pending.shift().reject(Error('offline'));await assert.rejects(load,/offline/);
load=fetchCatalogDetail('raw.a','columns');pending.shift().resolve(detail());await load;
for(let i=0;i<64;i++){load=fetchCatalogDetail('raw.t'+i,'columns');pending.shift().resolve(detail());await load;}
assert.equal(catalogCache.size,64);assert.equal(cachedCatalogDetail(catalogKey('raw.a','columns')),undefined);
load=fetchCatalogDetail('raw.bad','profile');pending.shift().resolve({metadata:{profile:{error:'no stats'}}});await assert.rejects(load,/no stats/);
assert.equal(catalogCache.has(catalogKey('raw.bad','profile')),false);
""")

    def test_stale_selection_errors_and_invalidation(self):
        self.javascript(r"""
let a=describeTable('raw','a'),b=describeTable('raw','b');
pending[1].resolve(detail('b'));await b;pending[0].reject(Error('old error'));await a;
assert.equal($('catalog-error').textContent,'');assert.deepEqual($('table-columns').rows,[['b']]);pending=[];
a=describeTable('raw','c');const old=pending.shift();invalidateCatalogDetails();
b=describeTable('raw','c');old.resolve(detail('old'));await a;
assert.equal(catalogCache.size,0);pending.shift().resolve(detail('new'));await b;
assert.deepEqual($('table-columns').rows,[['new']]);
invalidateCatalogDetails();a=describeTable('raw','c');pending.shift().reject(Error('retry'));await a;
assert.equal($('catalog-error').textContent,'retry');
a=describeTable('raw','c');pending.shift().resolve(detail('retried'));await a;
assert.equal($('catalog-error').textContent,'');assert.deepEqual($('table-columns').rows,[['retried']]);
""")

    def test_role_reset_clears_trees_and_pending_list_details_errors(self):
        self.javascript(r"""
catalogMetadata={raw:[['secret']]};let old=loadCatalog();const list=pending.shift();
let detailLoad=describeTable('raw','secret');const detailRequest=pending.shift();
currentRole='bi_viewer';resetCatalogIdentity();currentRole='admin';resetCatalogIdentity();
assert.equal(selectedTable,null);assert.deepEqual(catalogMetadata,{});assert.equal(catalogPending.size,0);
assert.equal($('table-columns').children.length,0);assert.equal($('catalog-tree').children.length,0);
assert.equal($('refresh-table-details').disabled,true);
list.resolve({metadata:{raw:[['secret']]}});detailRequest.reject(Error('secret error'));await old;await detailLoad;
assert.deepEqual(catalogMetadata,{});assert.equal($('catalog-error').textContent,'');assert.equal(pending.length,0);
catalogSession='first';const key=catalogKey('raw.a','columns');catalogSession='second';assert.notEqual(catalogKey('raw.a','columns'),key);
""")

    def test_refresh_reloads_only_a_listed_selection_and_guards_list_errors(self):
        self.javascript(r"""
selectedTable='raw.a';activeInspector='preview';let load=loadCatalog();
pending.shift().resolve({metadata:{raw:[['a']]}});await tick();assert.match(pending[0].path,/detail=preview$/);
pending.shift().resolve(detail());await tick();pending.shift().resolve({metadata:{lineage:{}}});await load;
load=loadCatalog();pending.shift().resolve({metadata:{raw:[]}});await tick();
assert.equal(selectedTable,null);assert.equal(pending[0].path,'/api/lineage');pending.shift().resolve({metadata:{lineage:{}}});await load;
const old=loadCatalog(),oldRequest=pending.shift();load=loadCatalog();pending.shift().resolve({metadata:{raw:[['b']]}});await tick();
pending.shift().resolve({metadata:{lineage:{}}});await load;oldRequest.reject(Error('late'));await old;
assert.deepEqual(catalogMetadata,{raw:[['b']]});assert.equal($('catalog-error').textContent,'');
load=loadCatalog();const stale=pending.shift();invalidateCatalogDetails();stale.resolve({metadata:{raw:[['stale']]}});await load;
assert.deepEqual(catalogMetadata,{raw:[['b']]});assert.equal(pending.length,0);
""")

    def test_delayed_catalog_lists_explain_run_observation_invalidation(self):
        self.javascript(r"""
for(const path of ['/api/jobs/runs','/api/marts'])for(const status of ['running','success'])for(const reject of [false,true]){
 if(status==='success'){
  const seed=api(path);pending.shift().resolve(path==='/api/jobs/runs'?{runs:[{id:'r',status:'running'}]}:{marts:[{last_run:{id:'r',status:'running'}}]});await seed;
 }
 const load=loadCatalog(),list=pending.shift();
 const observation=api(path),run=pending.shift();
 run.resolve(path==='/api/jobs/runs'?{runs:[{id:'r',status}]}:{marts:[{last_run:{id:'r',status}}]});await observation;
 if(reject)list.reject(Error('late list failure'));else list.resolve({metadata:{raw:[['stale']]}});await load;
 assert.deepEqual(catalogMetadata,{});assert.equal(trees,0);assert.equal(pending.length,0,'no automatic retry or lineage read');
 for(const id of ['catalog-tree','worksheet-tree'])assert.match($(id).textContent,/Refresh catalog in Database Explorer/);
 assert.equal($('catalog-error').textContent,'');
 // Make each combination observe a new state, including the initial nonempty history.
 catalogRunStates.clear();
}
""")

    def test_delayed_catalog_observation_guidance_keeps_request_and_role_fences(self):
        self.javascript(r"""
for(const reject of [false,true])for(const fence of ['request','role']){
 const old=loadCatalog(),list=pending.shift(),observation=api('/api/jobs/runs');
 pending.shift().resolve({runs:[{id:String(catalogGeneration),status:'running'}]});await observation;
 let fresh;
 if(fence==='request')fresh=loadCatalog();else{currentRole=currentRole==='admin'?'bi_viewer':'admin';resetCatalogIdentity();}
 $('catalog-tree').textContent='current tree';$('worksheet-tree').textContent='current worksheet';
 if(reject)list.reject(Error('old error'));else list.resolve({metadata:{raw:[['old']]}});await old;
 assert.equal($('catalog-tree').textContent,'current tree');assert.equal($('worksheet-tree').textContent,'current worksheet');
 assert.deepEqual(catalogMetadata,{});assert.equal($('catalog-error').textContent,'');
 if(fresh){pending.shift().resolve({metadata:{}});await tick();pending.shift().resolve({metadata:{lineage:{}}});await fresh;}
}
""")

    def test_mutations_invalidate_before_and_finally_and_poll_changes(self):
        self.javascript(r"""
for(const [path,body] of [
 ['/api/warehouse/query',{sql:'select 1'}],['/api/lake/ingest',{}],['/api/catalog/drop',{}],['/api/catalog/clone',{}],
 ['/api/bears/run',{bear:'data',action:'stage'}],['/api/bears/run',{bear:'data',publish:'x'}],
 ['/api/models/build',{action:'build'}],['/api/jobs/run',{}],['/api/marts/run',{}],
 ['/api/stages/import',{}],['/api/stages/scan',{}],['/api/stages/export',{}]]){
 const before=catalogGeneration,load=api(path,body);assert.equal(catalogGeneration,before+1);
 pending.shift().reject(Error('failed'));await assert.rejects(load);assert.equal(catalogGeneration,before+2);
}
for(const [path,body] of [['/api/lake/preview',{}],['/api/bears/run',{bear:'data',action:'generate'}],['/api/models/build',{action:'test'}],['/api/jobs/save',{}]])assert.equal(catalogMutation(path,body),false);
for(const [path,body,make] of [
 ['/api/jobs/runs',undefined,status=>({runs:[{id:'r',status}]})],
 ['/api/marts',undefined,status=>({marts:[{last_run:{id:'r',status}}]})],
 ['/api/stages/files',{name:'stage'},status=>({files:[{last_run_id:'r',ingestion_status:status}]})]]){
 const before=catalogGeneration;observeCatalogRuns(path,body,make('running'));assert.equal(catalogGeneration,before+1);
 observeCatalogRuns(path,body,make('running'));assert.equal(catalogGeneration,before+1);
 observeCatalogRuns(path,body,make('success'));assert.equal(catalogGeneration,before+2);
}
""")

    def test_clone_keeps_audit_notification_after_inspector_reload(self):
        self.javascript(r"""
selectedTable='raw.a';let load=cloneTable('raw.a');
pending.shift().resolve({table:'sandbox.copy',audit:{quality_passed:false}});await tick();
pending.shift().resolve({metadata:{raw:[['a']]}});await tick();
pending.shift().resolve(detail());await tick();pending.shift().resolve({metadata:{lineage:{}}});await load;
assert.equal($('catalog-error').textContent,'Clone audit needs review');assert.equal($('catalog-error').hidden,false);
""")

    def test_snapshot_controls_and_stale_time_travel_response(self):
        self.javascript(r"""
activeInspector='snapshots';let load=describeTable('raw','a');
const data=detail();data.metadata.snapshots.rows=[['123',null,'today']];pending.shift().resolve(data);await load;
let buttons=$('table-snapshots').trs[0].lastElementChild.children;
assert.deepEqual(buttons.map(b=>b.label),['Preview Snapshot','Rollback','Clone Snapshot']);
let preview=buttons[0].task();assert.match(pending[0].path,/snapshot_id=123$/);
pending.shift().resolve({row_count:2,current_row_count:3,preview:{columns:['x'],rows:[[1],[2]]}});await preview;
assert.equal($('snapshot-preview').children.length,2);
preview=buttons[0].task();const stale=pending.shift();invalidateCatalogDetails();stale.reject(Error('late snapshot'));await preview;
assert.equal($('snapshot-preview').textContent,'');
load=describeTable('raw','a');pending.shift().resolve(data);await load;buttons=$('table-snapshots').trs[0].lastElementChild.children;
let rollback=buttons[1].task();assert.equal(calls.at(-1).path,'/api/bears/run');
assert.equal(JSON.parse(calls.at(-1).options.body).snapshot_id,'123');
pending.shift().resolve({valid:true});await tick();assert.match(pending[0].path,/detail=snapshots$/);
pending.shift().resolve(data);await rollback;
assert.equal($('table-snapshots').trs.length,1);
""")

    def test_markup_wiring_and_javascript_syntax(self):
        self.assertNotIn('column-snapshots', HTML)
        self.assertIn("wireTabs(inspectorTabs,selectInspector)", SCRIPT)
        self.assertIn("$('refresh-table-details').onclick=()=>busy($('refresh-table-details'),loadCatalog)", SCRIPT)
        self.assertIn("'Refresh details':'詳細を更新'", SCRIPT)
        self.assertIn('catalogSession=JSON.stringify([session.username,session.authenticated,session.maximum_role])', SCRIPT)
        result = subprocess.run(['node', '--check'], input=SCRIPT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_session_validation_coalesces_across_cached_selections_and_tabs(self):
        self.javascript(r"""
for(const section of ['columns','preview']){
 const load=fetchCatalogDetail('raw.a',section);pending.shift().resolve(detail(section));await load;
}
let first=describeTable('raw','a');const validation=pending.shift();
let second=selectInspector(inspectorTabs[1]);
assert.equal(pending.length,0);assert.equal(calls.filter(c=>c.path==='/api/session').length,1);
assert.equal(calls.at(-1).options,undefined);
assert.equal($('table-columns').rows,undefined);assert.equal($('table-preview').rows,undefined);
const count=loading.length;validation.resolve(session);await first;await second;
assert.deepEqual($('table-preview').rows,[['preview']]);assert.equal($('table-columns').rows,undefined);
assert.equal(calls.length,3);assert.equal(loading.length,count);
""")

    def test_session_username_and_privilege_changes_reset_and_refetch(self):
        self.javascript(r"""
for(const change of [{username:'other'},{maximum_role:'bi_viewer',role:'bi_viewer'}]){
 currentRole='admin';catalogSession=JSON.stringify([session.username,session.authenticated,session.maximum_role]);
 resetCatalogIdentity();catalogMetadata={raw:[['a']]};
 let load=describeTable('raw','a');pending.shift().resolve(detail('secret'));await load;
 load=describeTable('raw','a');const validation=pending.shift();
 // Sending the old selected admin role would make a downgraded server return 403.
 assert.equal(calls.at(-1).path,'/api/session');assert.equal(calls.at(-1).options,undefined);
 validation.resolve({...session,...change});await tick();
 assert.equal(selectedTable,null);assert.deepEqual(catalogMetadata,{});assert.equal(catalogCache.size,0);
 assert.equal(catalogPending.size,0);assert.equal($('table-columns').rows,undefined);
 assert.equal(pending[0].path,'/api/catalog');
 if(change.maximum_role){assert.equal(calls.at(-1).options.headers['X-Shirokuma-Role'],'bi_viewer');assert.equal(currentRole,'bi_viewer');assert.equal($('role-selector').value,'bi_viewer');assert.equal($('role-selector').options[2].disabled,true);}
 pending.shift().resolve({metadata:{marts:[['authorized']]}});await tick();
 pending.shift().resolve({metadata:{lineage:{}}});await load;
 assert.deepEqual(catalogMetadata,{marts:[['authorized']]});assert.equal(selectedTable,null);
}
""")

    def test_auth_denials_clear_cached_and_fresh_details_but_metadata_errors_do_not(self):
        self.javascript(r"""
for(const status of [401,403])for(const cached of [true,false]){
 resetCatalogIdentity();catalogMetadata={raw:[['a']]};
 let load=describeTable('raw','a');pending.shift().resolve(detail('secret'));await load;
 if(!cached)invalidateCatalogDetails();
 load=describeTable('raw','a');assert.equal(pending[0].path==='/api/session',cached);
 pending.shift().resolve({error:'unauthorized'},status);await load;
 assert.equal(selectedTable,null);assert.deepEqual(catalogMetadata,{});assert.equal(catalogCache.size,0);
 assert.equal(catalogPending.size,0);assert.equal(catalogSessionValidation,null);
 assert.equal($('table-columns').rows,undefined);assert.equal($('table-columns').textContent,'');
 assert.equal($('catalog-tree').children.length,0);assert.equal($('worksheet-tree').children.length,0);
 assert.equal($('catalog-error').hidden,false);assert.match($('catalog-error').textContent,/Refresh the page/);
}
catalogMetadata={raw:[['a']]};activeInspector='ddl';let load=describeTable('raw','a');
pending.shift().resolve({metadata:{definition:{error:'ordinary metadata error'}}});await load;
assert.equal(selectedTable,'raw.a');assert.deepEqual(catalogMetadata,{raw:[['a']]});
assert.equal($('catalog-error').textContent,'ordinary metadata error');
// An SQL authorization failure does not clear the authorized catalog tree.
load=api('/api/warehouse/query',{sql:'select 1'});pending.shift().resolve({error:'SQL denied'},403);await assert.rejects(load);
assert.deepEqual(catalogMetadata,{raw:[['a']]});assert.equal(selectedTable,'raw.a');
""")

    def test_stale_session_and_catalog_auth_responses_cannot_reset_new_role(self):
        self.javascript(r"""
for(const response of [{status:403,data:{error:'late'}},{status:200,data:{...session,username:'late-account'}}]){
 resetCatalogIdentity();currentRole='admin';
 let load=describeTable('raw','a');pending.shift().resolve(detail());await load;
 let old=describeTable('raw','a');const stale=pending.shift();
 currentRole='bi_viewer';resetCatalogIdentity();
 load=describeTable('marts','b');pending.shift().resolve(detail('new'));await load;
 stale.resolve(response.data,response.status);await old;
 assert.equal(currentRole,'bi_viewer');assert.equal(selectedTable,'marts.b');assert.deepEqual($('table-columns').rows,[['new']]);
 assert.equal(catalogSession,JSON.stringify([session.username,session.authenticated,session.maximum_role]));
}
let old=describeTable('raw','uncached'),stale=pending.shift();resetCatalogIdentity();
let load=describeTable('marts','c');pending.shift().resolve(detail('current'));await load;
stale.resolve({error:'old denial'},401);await old;
assert.equal(selectedTable,'marts.c');assert.deepEqual($('table-columns').rows,[['current']]);
// Invalidation detaches a pending session check even when the role stays the same.
old=describeTable('marts','c');stale=pending.shift();invalidateCatalogDetails();
load=describeTable('marts','c');pending.shift().resolve(detail('latest'));await load;
stale.resolve({...session,username:'stale-account'});await old;
assert.deepEqual($('table-columns').rows,[['latest']]);assert.equal(pending.length,0);
""")

    def test_late_run_observations_leave_refresh_state_and_disabled_actions(self):
        self.javascript(r"""
for(const [path,data] of [['/api/jobs/runs',{runs:[{id:'j',status:'success'}]}],['/api/marts',{marts:[{last_run:{id:'m',status:'success'}}]}]]){
 const old=api(path),late=pending.shift();
 let load=describeTable('raw','a');pending.shift().resolve(detail());await load;
 late.resolve(data);await old;
 assert.equal($('table-columns').textContent,'Details changed. Refresh details to continue.');
 for(const id of ['clone-table-btn','drop-table-btn','export-table-btn','open-table-sql'])assert.equal($(id).disabled,true,id);
 assert.equal($('refresh-table-details').disabled,false);assert.equal(pending.length,0);
}
""")
        self.assertIn("'Details changed. Refresh details to continue.':'詳細が変更されました。詳細を更新して続行してください。'", SCRIPT)

    def test_snapshot_preview_latest_request_wins_for_results_and_errors(self):
        self.javascript(r"""
activeInspector='snapshots';let load=describeTable('raw','a');const data=detail();
data.metadata.snapshots.rows=[['1',null,'today'],['2','1','today']];pending.shift().resolve(data);await load;
const buttons=$('table-snapshots').trs.map(row=>row.lastElementChild.children[0]);
const preview=value=>({row_count:1,preview:{columns:['x'],rows:[[value]]}});
for(const reject of [false,true]){
 const a=buttons[0].task(),b=buttons[1].task();const first=pending.shift(),second=pending.shift();
 second.resolve(preview('B'));await b;
 if(reject)first.reject(Error('late A error'));else first.resolve(preview('A'));await a;
 assert.deepEqual($('snapshot-preview').children[1].rows,[['B']]);assert.equal($('snapshot-preview').textContent,'');
}
const old=buttons[0].task(),stale=pending.shift();load=describeTable('raw','b');pending.shift().resolve(data);await load;
stale.resolve(preview('old selection'));await old;assert.equal($('snapshot-preview').children.length,0);
""")


if __name__ == '__main__':
    unittest.main()
