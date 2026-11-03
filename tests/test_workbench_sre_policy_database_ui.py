"""C2A read-only observations: production handlers with fixture-only DOM/fetch."""
import json
import unittest
from unittest.mock import patch

import test_workbench_database_ui as harness
from test_workbench_bears_database_ui import COMMON
from test_workbench_ai_curator_database_ui import CURATOR
from test_workbench_ai_sql_proposals_database_ui import PROPOSAL


OBSERVATIONS = r"""
const observationCalls=()=>calls.filter(c=>c.path.startsWith('/api/v1/bears/context-observations'));
const changeObservation=(kind,name,value)=>{const n=$('observation-'+kind+'-'+name);n.value=value;return n.onchange();};
async function readyObservation(kind,role='admin',database='db',schema='schema'){
 const p=dbObservations[kind];if(p.role!==role)changeObservation(kind,'role',role);
 await dbObservationRefresh(p);changeObservation(kind,'database',database);await dbObservationRefresh(p);changeObservation(kind,'schema',schema);await dbObservationRefresh(p);assert.equal(dbObservationAllowed(p),true);
}
const observationItem=(kind='query',extra={})=>({id:'saved <img src=x> '+kind,kind,state:kind==='query'?'FINISHED':'SUCCEEDED',created_at:'2026-10-30T01:00:00Z',updated_at:kind==='query'?null:'2026-10-30T02:00:00Z',...extra});
const observationReply=(kind='sre')=>({context:structuredClone(dbObservations[kind].context),observed_at:'2026-11-01T01:00:00Z',coverage:{kind:'latest_persisted_own_context',limit_per_source:20,full_history:false,live_runtime:false},sre:{queries:{items:[observationItem()],has_more:false,omitted:0},ai:{items:[observationItem('sql_proposal')],has_more:false,omitted:0}},policy:{context_authorized:true,schema_permissions:{browse:true,query:true,write:false},sql_modes:['read-only'],statement_approval:false,shared_local:true},platform:{allowed:false,code:'PLATFORM_ADMIN_REQUIRED'}});
async function fetchObservation(kind='sre',reply=observationReply(kind)){
 route=async(path,options)=>{assert.ok(path.startsWith('/api/v1/bears/context-observations?'));assert.equal(options.method,undefined);assert.equal(options.body,undefined);assert.deepEqual(Object.keys(options.headers),['X-Shirokuma-Role']);assert.equal(options.headers['X-Shirokuma-Role'],reply.context.selected_role);const params=new URL(path,'http://fixture').searchParams;assert.deepEqual([...params.keys()],['context']);assert.deepEqual(JSON.parse(params.get('context')),reply.context);return reply;};
 await $('observation-'+kind+'-fetch').onclick();
}
const observationText=kind=>visibleText($('observation-'+kind+'-output'));
const durableState=()=>JSON.stringify({worksheets,worksheetId,sql:$('sql').value,doctor:$('bear-query-sql').value,draft:$('copilot-request').value,result:queryResult,explanation:dbExplanation.intents,proposal:dbProposal.intents,curator:dbCurator.intents,forms:dbProposal.forms,wap:[...dataopsPanels].map(([key,p])=>[key,p.context,p.model,p.sources,p.op]),storage:[...storage],writes},(_,v)=>v instanceof Node?{id:v.id,value:v.value}:v);
const otherState=()=>JSON.stringify({worksheets,worksheetId,sql:$('sql').value,doctor:$('bear-query-sql').value,draft:$('copilot-request').value,result:queryResult,runs:[...dbRuns],explorer:dbExplorer,history:dbHistory,explanation:dbExplanation,proposal:dbProposal,curator:dbCurator,wap:[...dataopsPanels].map(([key,p])=>[key,p.context,p.model,p.sources,p.op]),storage:[...storage],writes},(_,v)=>v instanceof Node?{id:v.id,value:v.value}:v);
"""


class SREPolicyDatabaseUI(unittest.TestCase):
    def javascript(self, body):
        # Unlike the convenience adapter, optional panels genuinely do not exist
        # until production installation appends them. This catches first-load bugs.
        run = harness.subprocess.run
        def strict_run(args, **kwargs):
            kwargs['input'] = kwargs['input'].replace(
                'const $=id=>strictRemoved',
                "const $=id=>(/^observation-(sre|policy)-panel$/.test(id)||id==='curator-panel')&&!nodes[id]?.parentElement?null:strictRemoved")
            return run(args, **kwargs)
        with patch.object(harness.subprocess, 'run', strict_run):
            harness.DatabaseUI.javascript(self, COMMON + OBSERVATIONS + r"for(const id of ['bear-data-run','bear-tab-data']){const n=document.createElement('button');n.id=id;$('bears').append(n);}" + body)

    def test_genuine_first_install_once_staged_bilingual_and_native_focus(self):
        self.javascript(r"""
assert.equal($('observation-sre-panel'),null);assert.equal($('observation-policy-panel'),null);await boot();
for(const kind of ['sre','policy']){
 const p=dbObservations[kind],panel=$('observation-'+kind+'-panel'),role=$('observation-'+kind+'-role');assert.ok(panel);role.focus();
 const stages=[
 [()=>{},'Refresh context to load Databases for the selected role.'],
 [()=>dbObservationRefresh(p),'Choose a Database, then refresh context to load its schemas.'],
 [()=>changeObservation(kind,'database','db'),'Refresh context to load schemas for the selected Database.'],
 [()=>dbObservationRefresh(p),'Choose a primary schema, then refresh context to verify it.'],
 [()=>changeObservation(kind,'schema','schema'),'Refresh context to verify the selected primary schema.'],
 [()=>dbObservationRefresh(p),'Context verified. Select Fetch observation.']];
 for(const [setup,key] of stages){await setup();for(const locale of ['ja','en']){setLanguage(locale);dbControls();dbObservationRender(p);assert.equal($('observation-'+kind+'-output').children[0].textContent,t(key));assert.notEqual(t(key),locale==='ja'?key:'');assert.equal(document.activeElement,role);}}
 const count=calls.length;for(let i=0;i<3;i++){dbObservationsInstall();dbControls();}assert.equal(calls.length,count);assert.equal($('observation-'+kind+'-panel'),panel);assert.equal($('observation-'+kind+'-role'),role);assert.equal($('bear-panel-'+kind).descendants().filter(n=>n.id===panel.id).length,1);
 assert.equal($('observation-'+kind+'-fetch').disabled,false);
}
assert.equal(observationCalls().length,0);assert.notEqual(dbObservations.sre.context,dbObservations.policy.context);
""")

    def test_two_database_role_null_grant_contexts_are_independent_of_worksheet_and_curator(self):
        self.javascript(r"""
await dualBoot();inventory[0].grant_revision=null;
await readyObservation('sre','bi_viewer');await readyObservation('policy','data_engineer','finance','finance-marts');
const sre=dbObservations.sre,policy=dbObservations.policy;assert.equal(sre.context.grant_revision,null);assert.equal(policy.context.database_id,'finance');assert.equal(policy.context.selected_role,'data_engineer');
const policyContext=policy.context,policyInventory=policy.databases;const before=otherState();
await fetchObservation('sre');await fetchObservation('policy');assert.equal(otherState(),before);assert.equal(policy.context,policyContext);assert.equal(policy.databases,policyInventory);
const policyRecord=policy.record;changeObservation('sre','schema','raw');assert.equal(sre.record,null);assert.equal(policy.record,policyRecord);assert.equal(policy.context,policyContext);
dbCurrent().database_id='unrelated';dbCurrent().schema_id='different';dbControls();assert.equal(policy.record,policyRecord);assert.equal(dbObservationAllowed(policy),true);assert.equal(sre.schema,'raw');
changeObservation('sre','role','admin');assert.equal(sre.databases.length,0);assert.equal(sre.database,'');assert.equal(policy.context,policyContext);
""")

    def test_role_ceiling_and_schema_query_permission_never_auto_select(self):
        self.javascript(r"""
await boot();
for(const role of ['bi_viewer','data_engineer','admin']){
 currentRole=role;session.maximum_role=role;dbMaximumRole=role;dbControls();changeObservation('sre','role',role);await dbObservationRefresh(dbObservations.sre);assert.equal(dbObservations.sre.database,'');
 for(const option of $('observation-sre-role').options)if(option.value)assert.equal(option.disabled,!dbRoleWithinHeader(option.value));
 changeObservation('sre','database','db');schemas[0].capabilities.query=false;await dbObservationRefresh(dbObservations.sre);assert.equal(dbObservations.sre.schemas.some(s=>s.id==='schema'),false);changeObservation('sre','schema','schema');await dbObservationRefresh(dbObservations.sre);assert.equal(dbObservationAllowed(dbObservations.sre),false);schemas[0].capabilities.query=true;
}
currentRole='bi_viewer';dbControls();changeObservation('sre','role','admin');const count=calls.length;await dbObservationRefresh(dbObservations.sre);await dbObservationFetch(dbObservations.sre);assert.equal(calls.length,count);assert.equal($('observation-sre-refresh').disabled,true);
""")

    def test_exact_get_window_copy_stored_states_and_safe_dom(self):
        self.javascript(r"""
await boot();await readyObservation('sre');const reply=observationReply();reply.sre.queries.has_more=true;reply.sre.queries.omitted=3;reply.sre.ai.items=[];reply.sre.ai.omitted=20;
const before=calls.length;await fetchObservation('sre',reply);assert.equal(calls.length,before+1);assert.ok(dbObservations.sre.record);
for(const locale of ['en','ja']){setLanguage(locale);dbControls();const text=observationText('sre');assert.ok(text.includes(locale==='ja'?'完了':'Finished'));assert.ok(text.includes(reply.sre.queries.items[0].created_at));assert.ok(text.includes(reply.observed_at));assert.ok(text.includes(t('Displayed query records')+': 1'));assert.ok(text.includes(t('Non-displayable records in this fetched window (first 20 time-eligible candidates) omitted: {count}. No diagnosis is provided.',{count:3})));assert.ok(text.includes(t('Older candidate records exist; eligible older records are not guaranteed.')));assert.ok(text.includes(t('No displayable matching own records in this bounded window.')));assert.ok(text.includes(t('Saved own-context metadata; first 20 candidates per source only. Stored states are not current health.')));assert.doesNotMatch(text,/grant_revision:|generation_id:|binding_revision:/);assert.equal($('observation-sre-output').descendants().some(n=>['PRE','IMG','A','BUTTON'].includes(n.tagName)),false);assert.ok(text.includes('<img src=x>'));}
const snapshot=otherState(),count=calls.length;dbControls();await tick();assert.equal(calls.length,count);assert.equal(otherState(),snapshot);assert.equal($('observation-sre-output').tagName,'DIV');
""")

    def test_policy_permission_ceiling_modes_and_platform_flag_are_observations_only(self):
        self.javascript(r"""
session.platform_admin=true;await boot();schemas[1].purpose='RAW';schemas[1].allowed_modes=['read-only','sandbox-write','admin-write'];
for(const role of ['bi_viewer','data_engineer','admin'])for(const allowed of [false,true]){
 if(allowed&&role!=='admin')continue;
 await readyObservation('policy',role,'db','raw');const reply=observationReply('policy');reply.platform={allowed,code:allowed?'PLATFORM_OBSERVATION_DEFERRED':'PLATFORM_ADMIN_REQUIRED'};reply.policy.sql_modes=role==='admin'?['read-only','sandbox-write','admin-write']:role==='data_engineer'?['read-only','sandbox-write']:['read-only'];reply.policy.schema_permissions.write=role!=='bi_viewer';
 const before=calls.length;await fetchObservation('policy',reply);assert.ok(dbObservations.policy.record);assert.equal(calls.length,before+1);
 for(const locale of ['ja','en']){setLanguage(locale);dbControls();const text=observationText('policy');assert.ok(text.includes(t('Current schema permission ceiling; statement and resource guards still apply. This is not statement approval.')));assert.ok(text.includes(t('Shared LOCAL: these Database permissions do not imply dedicated compute or platform control.')));assert.ok(text.includes(t(allowed?'This Database report omits platform sources. Use the separate Platform (P-only) scope with global Admin authority.':'This Database report omits platform sources. The separate Platform (P-only) scope requires platform administrator and global Admin authority.')));assert.ok(text.includes(locale==='ja'?'読み取り専用':'Read only'));assert.equal($('observation-policy-panel').descendants().filter(n=>n.tagName==='SELECT').length,4);}
}
assert.equal(calls.some(c=>c.options?.method==='POST'),false);
""")

    def test_closed_dto_rejects_unknown_keys_at_every_level_before_any_display(self):
        self.javascript(r"""
await boot();await readyObservation('sre');const base=observationReply();
const paths=[[],['context'],['coverage'],['sre'],['sre','queries'],['sre','ai'],['sre','queries','items',0],['sre','ai','items',0],['policy'],['policy','schema_permissions'],['platform']];
for(const path of paths){const bad=structuredClone(base);let node=bad;for(const key of path)node=node[key];node.private='RAW SQL PROMPT SECRET';route=async()=>bad;await dbObservationFetch(dbObservations.sre);assert.equal(dbObservations.sre.record,null);assert.doesNotMatch(observationText('sre'),/SECRET|RAW SQL|PROMPT/);assert.throws(()=>dbObservationRecord(bad,base.context,dbObservations.sre.ceiling));}
for(const path of paths.filter(p=>p.length)){const bad=structuredClone(base);let node=bad;for(const key of path)node=node[key];delete node[Object.keys(node)[0]];assert.throws(()=>dbObservationRecord(bad,base.context,dbObservations.sre.ceiling));}
""")

    def test_strict_types_kinds_limits_timestamps_and_platform_mapping(self):
        self.javascript(r"""
await boot();await readyObservation('sre');const base=observationReply();
const mutations=[
 r=>r.context.grant_revision=undefined,r=>r.context.grant_revision=true,r=>r.context.binding_revision=true,r=>r.context.binding_revision=1.1,r=>r.context.warehouse_id='XS',r=>r.context.selected_role='bi_viewer',r=>r.context.database_id=4,
 r=>r.observed_at='yesterday',r=>r.observed_at='2026-02-30T01:00:00Z',r=>r.observed_at='2026-11-01T24:00:00Z',r=>r.observed_at='2026-11-01T01:00:00+09:00',r=>r.observed_at='x'.repeat(65),r=>r.coverage.limit_per_source='20',r=>r.coverage.full_history=0,r=>r.coverage.live_runtime=true,r=>r.coverage.kind='global',
 r=>r.sre.queries.items={},r=>r.sre.queries.has_more=1,r=>r.sre.queries.omitted=true,r=>r.sre.queries.omitted=-1,r=>r.sre.queries.omitted=0.5,r=>r.sre.queries.omitted=21,r=>r.sre.queries.omitted=20,r=>r.sre.queries.items=Array(21).fill(observationItem()),
 r=>r.sre.queries.items[0].id='',r=>r.sre.queries.items[0].id='   ',r=>r.sre.queries.items[0].id='x'.repeat(257),r=>r.sre.queries.items[0].id='x\nsecret',r=>r.sre.queries.items[0].id=1,r=>r.sre.queries.items[0].created_at=null,r=>r.sre.queries.items[0].updated_at=r.observed_at,r=>r.sre.queries.items[0].state='SUCCEEDED',r=>r.sre.queries.items[0].kind='sql_explanation',
 r=>r.sre.ai.items[0].kind='unknown',r=>r.sre.ai.items[0].state='FINISHED',r=>r.sre.ai.items[0].updated_at=null,r=>r.sre.ai.items[0].created_at='1',r=>r.sre.ai.items[0].error_code=null,
 r=>r.policy.context_authorized=false,r=>r.policy.statement_approval=true,r=>r.policy.shared_local=1,r=>r.policy.schema_permissions.query=1,r=>r.policy.sql_modes=['read-only','read-only'],r=>r.policy.sql_modes=['READ_ONLY'],r=>r.policy.sql_modes='read-only',
 r=>r.platform.allowed=1,r=>r.platform.code='PLATFORM_OBSERVATION_DEFERRED',r=>r.platform={allowed:true,code:'PLATFORM_ADMIN_REQUIRED'}];
for(const mutate of mutations){const r=structuredClone(base);mutate(r);assert.throws(()=>dbObservationRecord(r,base.context,dbObservations.sre.ceiling),String(mutate));}
for(const state of ['QUEUED','RUNNING','FINISHED','FAILED','CANCELED','UNKNOWN']){const r=structuredClone(base);r.sre.queries.items[0].state=state;assert.equal(dbObservationRecord(r,base.context,dbObservations.sre.ceiling).sre.queries.items[0].state,state);}
for(const kind of ['sql_explanation','sql_plan_explanation','saved_plan_explanation','sql_proposal','catalog_proposal'])for(const state of ['QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELED','UNKNOWN']){const r=structuredClone(base);r.sre.ai.items[0].kind=kind;r.sre.ai.items[0].state=state;assert.equal(dbObservationRecord(r,base.context,dbObservations.sre.ceiling).sre.ai.items[0].state,state);}
const maximum=structuredClone(base);maximum.sre.queries.items=Array.from({length:20},(_,n)=>observationItem('query',{id:'q'+n}));maximum.sre.ai.items=[];maximum.sre.ai.omitted=20;assert.ok(dbObservationRecord(maximum,base.context,dbObservations.sre.ceiling));
""")

    def test_late_success_and_error_context_roundtrips_cannot_overwrite_newer_request(self):
        self.javascript(r"""
await boot();await readyObservation('sre');await readyObservation('policy');await fetchObservation('policy');const policyRecord=dbObservations.policy.record;
for(const reject of [false,true]){
 const old=deferred(),reply=observationReply();route=()=>old.promise;const first=dbObservationFetch(dbObservations.sre);await tick();changeObservation('sre','schema','raw');await dbObservationRefresh(dbObservations.sre);changeObservation('sre','schema','schema');await dbObservationRefresh(dbObservations.sre);
 const fresh=deferred();route=()=>fresh.promise;const second=dbObservationFetch(dbObservations.sre);await tick();const token=dbObservations.sre.busy;
 if(reject)old.resolve({http:403,body:{error:{code:'DB_ACCESS_REVOKED',message:'STALE SECRET'}}});else old.resolve(reply);await first;assert.equal(dbObservations.sre.busy,token);assert.ok(dbObservations.sre.context);assert.equal(dbObservations.sre.record,null);assert.equal(dbObservations.policy.record,policyRecord);
 fresh.resolve(reply);await second;assert.ok(dbObservations.sre.record);assert.doesNotMatch(observationText('sre'),/SECRET/);
}
""")

    def test_role_actor_auth_navigation_roundtrips_fence_both_outcomes(self):
        self.javascript(r"""
await boot();
const mutations=[
 ()=>{changeObservation('sre','role','bi_viewer');changeObservation('sre','role','admin');},
 ()=>{currentRole='bi_viewer';dbControls();currentRole='admin';dbControls();},
 ()=>{dbMaximumRole='bi_viewer';dbControls();dbMaximumRole='admin';dbControls();},
 ()=>{const old=dbPrincipal;dbPrincipal=JSON.stringify(['other',true]);dbControls();dbPrincipal=old;dbControls();},
 ()=>{dbAuthorizationVersion++;dbControls();},()=>{dbEpoch++;dbControls();},
 ()=>{activeTab='bears';dbControls();activeTab='warehouse';dbControls();},
 ()=>{dbObservationNavigate('sre');dbObservationNavigate('policy');dbObservationNavigate('sre');},
 ()=>{const p=dbObservations.sre;p.databases=structuredClone(p.databases);},
 ()=>{const p=dbObservations.sre;p.schemas=structuredClone(p.schemas);}
];
for(const mutate of mutations)for(const failure of [false,true]){
 await readyObservation('sre');const old=deferred(),reply=observationReply();route=()=>old.promise;const pending=dbObservationFetch(dbObservations.sre);await tick();mutate();
 old.resolve(failure?{http:403,body:{error:{code:'DB_ACCESS_REVOKED',message:'old secret'}}}:reply);await pending;assert.equal(dbObservations.sre.record,null,String(mutate));assert.doesNotMatch(observationText('sre'),/old secret/);
}
""")

    def test_each_refresh_await_is_fenced_and_loading_text_is_bilingual(self):
        self.javascript(r"""
await boot();
for(const source of ['session','inventory','schemas']){
 await readyObservation('sre');const old=deferred();let pending;
 if(source==='session')sessionRoute=()=>old.promise;else if(source==='inventory')inventoryRoute=()=>old.promise;else schemasRoute=()=>old.promise;
 pending=dbObservationRefresh(dbObservations.sre);await tick();for(const locale of ['en','ja']){setLanguage(locale);dbControls();assert.ok(observationText('sre').includes(t('Refreshing observation context.')));}
 changeObservation('sre','role','bi_viewer');changeObservation('sre','role','admin');sessionRoute=null;inventoryRoute=null;schemasRoute=null;
 await readyObservation('sre');const newer=dbObservations.sre.context;
 old.resolve(source==='session'?session:source==='inventory'?{items:inventory}:{items:schemas});await pending;assert.equal(dbObservations.sre.context,newer);assert.equal(dbObservations.sre.record,null);
}
await readyObservation('sre');const old=deferred();route=()=>old.promise;const pending=dbObservationFetch(dbObservations.sre);await tick();for(const locale of ['ja','en']){setLanguage(locale);dbControls();assert.ok(observationText('sre').includes(t('Fetching saved Database observation.')));}old.reject(Error('private provider raw prompt'));await pending;assert.doesNotMatch(observationText('sre'),/private|provider|prompt/);
""")

    def test_current_denials_clear_sensitive_data_without_other_draft_or_receipt_changes(self):
        self.javascript(r"""
await boot();await readyObservation('sre');await readyObservation('policy');await fetchObservation('sre');await fetchObservation('policy');
const before=durableState();dbInvalidateReceipt('admin');assert.equal(dbObservations.sre.context,null);assert.equal(dbObservations.policy.context,null);assert.deepEqual(dbObservations.sre.databases,[]);assert.doesNotMatch(observationText('sre'),/saved <img/);assert.equal(durableState(),before);
for(const [status,code] of [[401,'UNRECOGNIZED'],[403,'SQL_PERMISSION_DENIED'],[403,'DB_ACCESS_REVOKED'],[404,'NOT_FOUND']]){
 await readyObservation('sre');const snapshot=durableState();route=async()=>({http:status,body:{error:{code,message:'SECRET RAW ERROR'}}});await dbObservationFetch(dbObservations.sre);assert.equal(dbObservations.sre.context,null);assert.equal(dbObservations.sre.record,null);assert.deepEqual(dbObservations.sre.databases,[]);assert.deepEqual(dbObservations.sre.schemas,[]);assert.doesNotMatch(observationText('sre'),/SECRET|RAW ERROR/);assert.equal(durableState(),snapshot);
}
""")

    def test_unrelated_role_observation_survives_explorer_role_denial_and_account_purges_both(self):
        self.javascript(r"""
await boot();await readyObservation('sre','bi_viewer');await readyObservation('policy','admin');await fetchObservation('sre');await fetchObservation('policy');const policyRecord=dbObservations.policy.record,policyContext=dbObservations.policy.context;
dbInvalidateReceipt('bi_viewer');assert.equal(dbObservations.sre.context,null);assert.equal(dbObservations.policy.record,policyRecord);assert.equal(dbObservations.policy.context,policyContext);
dbInvalidateReceipt('admin',true);assert.equal(dbObservations.policy.record,null);assert.equal(dbObservations.policy.context,null);
""")

    def test_held_unknown_requests_and_all_drafts_are_unchanged_by_repeated_reads(self):
        self.javascript(r"""
await boot();route=async()=>wrap(record({state:'UNKNOWN',text:null,inference_state:'UNKNOWN'}));await $('copilot-explain').onclick();editSQL('SELECT kept worksheet');editDoctor('SELECT kept doctor');$('copilot-request').value='kept Copilot draft';$('copilot-request').dispatchEvent(new Event('input'));queryResult={rows:[[9]],columns:['kept']};
await readyObservation('sre');await readyObservation('policy');const snapshot=otherState(),start=calls.length;
for(const kind of ['sre','policy']){await fetchObservation(kind);await fetchObservation(kind);route=async()=>({http:503,body:{error:{code:'AUDIT_STORE_UNAVAILABLE',message:'secret store'}}});await dbObservationFetch(dbObservations[kind]);assert.equal(dbObservations[kind].record,null);assert.doesNotMatch(observationText(kind),/secret store/);await fetchObservation(kind);}
assert.equal(otherState(),snapshot);assert.equal(calls.length-start,8);assert.ok(calls.slice(start).every(c=>c.path.startsWith('/api/v1/bears/context-observations?')&&!c.options?.method));assert.equal(dbExplanation.intents[0].id,'explanation-1');assert.equal(dbSourceA1Held(dbExplanation.intents[0]),true);
for(const id of ['bear-sre-run','bear-policy-run','bear-policy-action','bear-policy-manifest','bear-data-run','ai-settings-button'])assert.equal($(id).disabled,true,id);
assert.equal($('bear-tab-data').disabled,true);for(const id of ['bear-sre-run','bear-policy-run','bear-policy-action','bear-policy-manifest'])assert.equal($(id).hidden,true,id);
""")

    def test_real_unknown_sql_curator_proposal_and_wap_intents_survive_observation_reads(self):
        self.javascript(CURATOR + PROPOSAL + r"""
const nativeAppend=Node.prototype.append;Node.prototype.append=function(...items){return nativeAppend.apply(this,items.map(n=>typeof n==='string'?Object.assign(new Node('text'),{textContent:n}):n));};
await boot();request('original proposal');await choose();await submit(pendingProposal({state:'UNKNOWN',inference_state:'UNKNOWN'}));assert.equal(dbProposalHeld().length,1);
await curatorContext();objects=[curatorObject()];await dbCuratorLoad();change('object','0');await curatorSubmit(curatorPending({state:'UNKNOWN',inference_state:'UNKNOWN'}));assert.equal(dbCuratorHeld().length,1);
route=async()=>envelope(dbRuns.get(worksheetId),{state:'UNKNOWN'});await dbRunQuery();assert.equal(dbRuns.get(worksheetId).state,'UNKNOWN');
const p=dataopsPanels.get('p35-wap');schemas[0].capabilities.wap_models=true;route=async path=>({context:JSON.parse(new URL(path,'http://fixture').searchParams.get('context')),items:[]});await dataopsRefresh(p);assert.equal(p.invalid,false);
route=async()=>{throw Error('lost WAP acknowledgement');};await dataopsSubmit(p,'/api/v1/wap/build',{model_id:'existing-model',version:1},{});assert.equal(p.op.ambiguous,true);const wapIntent=p.op;
$('p35-wap-sql').value='SELECT kept WAP draft';p.sources[0].alias.value='kept_alias';editSQL('SELECT kept SQL draft');editDoctor('SELECT kept Doctor draft');request('kept Copilot draft');
await readyObservation('sre');await readyObservation('policy');const before=otherState(),start=calls.length,wapDraft=$('p35-wap-sql').value;
for(const kind of ['sre','policy']){await fetchObservation(kind);route=async()=>{throw Error('read lost');};await dbObservationFetch(dbObservations[kind]);await fetchObservation(kind);}
assert.equal(otherState(),before);assert.equal(p.op,wapIntent);assert.equal(p.op.ambiguous,true);assert.equal($('p35-wap-sql').value,wapDraft);assert.equal(p.sources[0].alias.value,'kept_alias');assert.equal(dbProposalHeld().length,1);assert.equal(dbCuratorHeld().length,1);assert.equal(dbRuns.get(worksheetId).state,'UNKNOWN');assert.equal(calls.length-start,6);assert.ok(calls.slice(start).every(c=>c.path.startsWith('/api/v1/bears/context-observations?')&&!c.options.method));
""")

    def test_production_bear_tab_mouse_keyboard_navigation_fences_late_reads(self):
        handlers = harness.SCRIPT[harness.SCRIPT.index('const bearTabs='):harness.SCRIPT.index('let auditEpoch=')]
        self.javascript(r"""
const bearList=new Node();for(const kind of ['sre','query','data','catalog','policy']){const tab=$('bear-tab-'+kind);tab.tagName='BUTTON';tab.dataset.bear=kind;bearList.append(tab);$('bears').append($('bear-panel-'+kind));}$('bears').append(bearList);
eval(""" + json.dumps(handlers) + r""");await boot();assert.equal($('bear-tab-query')['aria-selected'],'true');
await readyObservation('sre');$('bear-tab-sre').click();assert.equal(dbObservations.sre.selected,true);const old=deferred(),reply=observationReply();route=()=>old.promise;const pending=dbObservationFetch(dbObservations.sre);await tick();
$('bear-tab-sre').onkeydown({key:'ArrowDown',preventDefault(){}});assert.equal($('bear-tab-query')['aria-selected'],'true');assert.equal(dbObservations.sre.selected,false);$('bear-tab-sre').click();old.resolve(reply);await pending;assert.equal(dbObservations.sre.record,null);assert.ok(dbObservations.sre.context);
$('bear-tab-data').click();assert.equal($('bear-tab-sre')['aria-selected'],'true');assert.equal($('bear-tab-data').disabled,true);
""")

    def test_own_denials_leave_other_database_bear_and_all_other_state_byte_equal(self):
        self.javascript(CURATOR + PROPOSAL + r"""
const nativeAppend=Node.prototype.append;Node.prototype.append=function(...items){return nativeAppend.apply(this,items.map(n=>typeof n==='string'?Object.assign(new Node('text'),{textContent:n}):n));};
await dualBoot();objectsRoute=null;request('original proposal');await choose();await submit(pendingProposal({state:'UNKNOWN',inference_state:'UNKNOWN'}));
await curatorContext();objects=[curatorObject()];await dbCuratorLoad();change('object','0');await curatorSubmit(curatorPending({state:'UNKNOWN',inference_state:'UNKNOWN'}));
route=async()=>wrap(record({state:'UNKNOWN',text:null,inference_state:'UNKNOWN'}));await $('copilot-explain').onclick();
route=async()=>envelope(dbRuns.get(worksheetId),{state:'UNKNOWN'});await dbRunQuery();
const wap=dataopsPanels.get('p35-wap');schemas[0].capabilities.wap_models=true;route=async path=>({context:JSON.parse(new URL(path,'http://fixture').searchParams.get('context')),items:[]});await dataopsRefresh(wap);route=async()=>{throw Error('lost ACK');};await dataopsSubmit(wap,'/api/v1/wap/build',{model_id:'existing-model',version:1},{});
editSQL('SELECT kept SQL');editDoctor('SELECT kept Doctor');request('kept Copilot');$('p35-wap-sql').value='SELECT kept WAP';wap.sources[0].alias.value='kept';queryResult={rows:[[9]],columns:['kept']};
for(const [own,other,status,code] of [['sre','policy',403,'SQL_PERMISSION_DENIED'],['policy','sre',404,'NOT_FOUND'],['sre','policy',401,'AUTHENTICATION_REQUIRED'],['policy','sre',409,'STALE_BINDING']]){
 await readyObservation('sre');await readyObservation('policy','admin','finance','finance-marts');await fetchObservation('sre');await fetchObservation('policy');
 const retained=dbObservations[other],record=retained.record,context=retained.context,inventory=retained.databases,schemas=retained.schemas,before=otherState(),auth=[dbAuthorizationVersion,dbRoleVersion('admin')],start=calls.length;
 route=async()=>({http:status,body:{error:{code,message:'SECRET DENIAL'}}});await dbObservationFetch(dbObservations[own]);
 assert.equal(dbObservations[own].context,null);assert.equal(dbObservations[own].record,null);assert.deepEqual(dbObservations[own].databases,[]);assert.deepEqual(dbObservations[own].schemas,[]);assert.ok(observationText(own).includes(t('Observation unavailable. Refresh context, then explicitly fetch again.')));assert.doesNotMatch(observationText(own),/SECRET/);
 assert.equal(retained.record,record);assert.equal(retained.context,context);assert.equal(retained.databases,inventory);assert.equal(retained.schemas,schemas);assert.equal(otherState(),before);assert.deepEqual([dbAuthorizationVersion,dbRoleVersion('admin')],auth);assert.equal(calls.length,start+1);assert.equal($('p35-wap-sql').value,'SELECT kept WAP');assert.equal(wap.sources[0].alias.value,'kept');
}
""")

    def test_actual_platform_session_flag_change_purges_visible_and_inflight_observations(self):
        self.javascript(r"""
session.platform_admin=true;await boot();
for(const inflight of [false,true]){
 await readyObservation('sre');await readyObservation('policy');const reply=observationReply();reply.platform={allowed:true,code:'PLATFORM_OBSERVATION_DEFERRED'};await fetchObservation('sre',reply);const policyReply=observationReply('policy');policyReply.platform={allowed:true,code:'PLATFORM_OBSERVATION_DEFERRED'};await fetchObservation('policy',policyReply);
 const before=durableState(),versions=[dbEpoch,dbAuthorizationVersion,dbRoleVersion('admin')],old=deferred();let pending;
 if(inflight){route=()=>old.promise;pending=dbObservationFetch(dbObservations.sre);await tick();}
 session.platform_admin=false;dbAdminSession(session);
 for(const p of Object.values(dbObservations)){assert.equal(p.context,null);assert.equal(p.record,null);assert.deepEqual(p.databases,[]);assert.deepEqual(p.schemas,[]);assert.doesNotMatch(observationText(p.kind),/saved <img|browse: true/);}
 assert.deepEqual([dbEpoch,dbAuthorizationVersion,dbRoleVersion('admin')],versions);assert.equal(durableState(),before);
 // Even a same-principal P roundtrip cannot make the old response current.
 session.platform_admin=true;dbAdminSession(session);await readyObservation('sre');const newer=dbObservations.sre.context;
 if(pending){old.resolve(reply);await pending;assert.equal(dbObservations.sre.context,newer);assert.equal(dbObservations.sre.record,null);}
}
""")

    def test_captured_schema_role_and_platform_ceiling_rejects_contradictory_dtos(self):
        self.javascript(r"""
session.platform_admin=true;await boot();inventory[0].grant_revision=null;schemas[0].purpose='MARTS';schemas[0].allowed_modes=['read-only','sandbox-write','admin-write'];schemas[1].purpose='RAW';schemas[1].allowed_modes=['read-only','sandbox-write','admin-write'];
for(const role of ['bi_viewer','data_engineer','admin'])for(const schema of ['schema','raw']){
 await readyObservation('sre',role,'db',schema);const p=dbObservations.sre,base=observationReply(),ceiling=p.ceiling;assert.equal(base.context.grant_revision,null);assert.ok(Object.isFrozen(ceiling));assert.ok(Object.isFrozen(ceiling.source.allowed_modes));
 base.policy.schema_permissions={...ceiling.permissions};base.policy.sql_modes=[...ceiling.modes];await fetchObservation('sre',base);assert.ok(p.record);
 for(const mode of ['read-only','sandbox-write','admin-write']){const r=structuredClone(base);r.policy.sql_modes=[mode];if(ceiling.modes.includes(mode))assert.ok(dbObservationRecord(r,base.context,ceiling));else assert.throws(()=>dbObservationRecord(r,base.context,ceiling));}
 const elevated=structuredClone(base);elevated.policy.schema_permissions.write=true;if(!ceiling.permissions.write)assert.throws(()=>dbObservationRecord(elevated,base.context,ceiling));
 const platform=structuredClone(base);platform.platform={allowed:true,code:'PLATFORM_OBSERVATION_DEFERRED'};if(role==='admin')assert.ok(dbObservationRecord(platform,base.context,ceiling));else assert.throws(()=>dbObservationRecord(platform,base.context,ceiling));
}
await readyObservation('sre','bi_viewer');const viewer=observationReply();viewer.policy.schema_permissions.write=true;viewer.policy.sql_modes=['admin-write'];viewer.platform={allowed:true,code:'PLATFORM_OBSERVATION_DEFERRED'};await fetchObservation('sre',viewer);assert.equal(dbObservations.sre.record,null);
// An unrelated writable schema or Bear never supplies the selected schema ceiling.
await readyObservation('policy','admin','db','raw');await readyObservation('sre','data_engineer');const wrong=observationReply();wrong.policy.sql_modes=['sandbox-write'];wrong.policy.schema_permissions.write=true;await fetchObservation('sre',wrong);assert.equal(dbObservations.sre.record,null);
await readyObservation('sre');schemas[0].capabilities.browse=false;schemas[0].capabilities.write=false;schemas[0].allowed_modes=['read-only'];await dbObservationRefresh(dbObservations.sre);const capped=observationReply();capped.policy.schema_permissions.browse=true;await fetchObservation('sre',capped);assert.equal(dbObservations.sre.record,null);
session.platform_admin=false;dbAdminSession(session);await readyObservation('sre');const noP=observationReply();noP.platform={allowed:true,code:'PLATFORM_OBSERVATION_DEFERRED'};await fetchObservation('sre',noP);assert.equal(dbObservations.sre.record,null);
""")

    def test_late_primary_schema_capability_changes_cannot_install_response(self):
        self.javascript(r"""
await boot();schemas[1].purpose='RAW';
for(const change of [s=>s.capabilities.write=false,s=>s.capabilities.query=false,s=>s.allowed_modes=['read-only'],s=>s.purpose='MARTS']){
 await readyObservation('sre','data_engineer','db','raw');const old=deferred(),reply=observationReply(),p=dbObservations.sre;route=()=>old.promise;const pending=dbObservationFetch(p);await tick();const selected=p.schemas.find(s=>s.id==='raw');change(selected);dbControls();assert.equal(p.context,null);old.resolve(reply);await pending;assert.equal(p.record,null);assert.deepEqual(p.databases,[]);
}
""")

    def test_literal_calendar_offset_and_long_fraction_timestamps_are_preserved(self):
        self.javascript(r"""
await boot();await readyObservation('sre');const base=observationReply(),ceiling=dbObservations.sre.ceiling;
const invalid=['2026-02-30T01:00:00Z','2025-02-29T01:00:00Z','1900-02-29T01:00:00Z','0000-01-01T01:00:00Z','2026-00-01T01:00:00Z','2026-13-01T01:00:00Z','2026-01-00T01:00:00Z','2026-04-31T01:00:00Z','2026-01-01T24:00:00Z','2026-01-01T01:60:00Z','2026-01-01T01:00:60Z','2026-01-01T01:00:00+24:00','2026-01-01T01:00:00+01:60','2026-01-01T01:00:00-24:00','2026-01-01T01:00:00-01:60','2026-01-01T01:00:00+0900','2026-01-01T01:00:00','now','yesterday','2026-01-01','2026-01-01 01:00:00Z','2026-01-01T01:00:00.Z'];
for(const value of invalid){assert.equal(dbObservationTime(value),false,value);for(const field of ['created_at','updated_at']){const r=structuredClone(base);r.sre.ai.items[0][field]=value;assert.throws(()=>dbObservationRecord(r,base.context,ceiling),value);}const r=structuredClone(base);r.observed_at=value;assert.throws(()=>dbObservationRecord(r,base.context,ceiling));}
const valid=['2000-02-29T23:59:59Z','2024-02-29T01:02:03.1+09:00','2026-01-01T01:02:03.12345678901234567890-05:30','2026-01-01T01:02:03+23:59','2026-01-01T01:02:03.'+'1'.repeat(43)+'Z'];
for(const value of valid){assert.equal(dbObservationTime(value),true,value);const r=structuredClone(base);r.sre.ai.items[0].created_at=value;r.sre.ai.items[0].updated_at=value;r.sre.queries.items[0].created_at=value;await fetchObservation('sre',r);assert.ok(dbObservations.sre.record);assert.equal(dbObservations.sre.record.sre.ai.items[0].updated_at,value);assert.ok(observationText('sre').includes(value));}
assert.equal(dbObservationTime('2026-01-01T01:02:03.'+'1'.repeat(44)+'Z'),false);
for(const zone of ['Z','+00:00','-00:00']){const r=structuredClone(base);r.observed_at='2026-01-01T01:02:03.123456789'+zone;assert.ok(dbObservationRecord(r,base.context,ceiling));}
for(const zone of ['+00:01','-00:01','+09:00']){const r=structuredClone(base);r.observed_at='2026-01-01T01:02:03'+zone;assert.throws(()=>dbObservationRecord(r,base.context,ceiling));}
""")

    def test_human_bilingual_headers_precede_full_windows_without_mutating_wire_or_other_state(self):
        self.javascript(r"""
session.platform_admin=true;await boot();schemas[1].purpose='RAW';schemas[1].allowed_modes=['read-only','sandbox-write','admin-write'];
await readyObservation('sre');await readyObservation('policy','admin','db','raw');
const sre=observationReply(),policy=observationReply('policy');sre.sre.queries.items=Array.from({length:20},(_,i)=>observationItem('query',{id:'query-trace-'+i,state:['QUEUED','RUNNING','FINISHED','FAILED','CANCELED','UNKNOWN'][i%6]}));
const kinds=['sql_explanation','sql_plan_explanation','saved_plan_explanation','sql_proposal','catalog_proposal'];sre.sre.ai.items=Array.from({length:20},(_,i)=>observationItem(kinds[i%5],{id:'ai-trace-'+i,state:['QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELED','UNKNOWN'][i%6]}));
sre.sre.queries.omitted=0;sre.sre.queries.has_more=true;policy.policy.schema_permissions.write=true;policy.policy.sql_modes=['read-only','sandbox-write','admin-write'];
await fetchObservation('sre',sre);await fetchObservation('policy',policy);const wire=JSON.stringify([dbObservations.sre.record,dbObservations.policy.record]),original=JSON.stringify([sre,policy]);assert.equal(wire,original);
for(const locale of ['ja','en']){
 setLanguage(locale);const before=otherState(),start=calls.length;dbControls();dbObservationRender(dbObservations.sre);dbObservationRender(dbObservations.policy);
 const output=$('observation-sre-output'),lines=output.children.map(n=>n.textContent),text=observationText('sre'),policyLines=$('observation-policy-output').children.map(n=>n.textContent),policyText=observationText('policy');
 assert.equal(lines[0],t('Captured Database context')+': sales.marts · '+(locale==='ja'?'管理者':'Admin')+' · LOCAL');assert.equal(lines[1],t('Observed at')+': '+sre.observed_at);assert.ok(lines.findIndex(line=>line.includes('query-trace-0'))>1);assert.ok(text.indexOf(sre.observed_at)<text.indexOf('query-trace-0'));
 assert.equal(policyLines[0],t('Captured Database context')+': sales.raw · '+(locale==='ja'?'管理者':'Admin')+' · LOCAL');assert.equal(policyLines[1],t('Observed at')+': '+policy.observed_at);assert.ok(policyLines.findIndex(line=>line.startsWith(locale==='ja'?'参照:':'Browse:'))>1);
 for(const value of ['database_id','schema_id','generation_id','binding_revision','grant_revision','selected_role','gen','schema']){assert.ok(!lines.slice(0,2).join('\n').includes(value),value);}
 assert.doesNotMatch(text,/binding_revision:|grant_revision:|generation_id:|FINISHED|SUCCEEDED|QUEUED|RUNNING|FAILED|CANCELED|UNKNOWN/);assert.doesNotMatch(policyText,/browse: true|query: true|write: true|read-only|sandbox-write|admin-write/);
 const expectedKinds=locale==='ja'?['クエリ','SQL の説明','実行計画の説明','保存済みプランの説明','SQL 案','カタログの説明文案']:['Query','SQL explanation','Execution plan explanation','Saved plan explanation','SQL proposal','Catalog proposal'];for(const label of expectedKinds)assert.ok(text.includes(label),label);
 const expectedStates=locale==='ja'?['待機中','実行中','完了','成功','失敗','キャンセル済み','結果不明']:['Queued','Running','Finished','Succeeded','Failed','Canceled','Outcome unknown'];for(const label of expectedStates)assert.ok(text.includes(label),label);
 assert.ok(lines.includes(locale==='ja'?'クエリ · 完了':'Query · Finished'));assert.ok(lines.includes(locale==='ja'?'保存済みプランの説明 · 成功':'Saved plan explanation · Succeeded'));
 assert.ok(text.includes((locale==='ja'?'レコード ID':'Record ID')+': query-trace-0'));assert.ok(text.includes(t('Older candidate records exist; eligible older records are not guaranteed.')));
 const permissionLabels=locale==='ja'?['参照: 許可','クエリ: 許可','書き込み: 許可']:['Browse: Allowed','Query: Allowed','Write: Allowed'];for(const label of permissionLabels)assert.ok(policyLines.includes(label));for(const mode of locale==='ja'?['読み取り専用','サンドボックス書き込み','管理者書き込み']:['Read only','Sandbox write','Admin write'])assert.ok(policyText.includes(mode));
 assert.equal(policyLines.filter(line=>line===t('Current schema permission ceiling; statement and resource guards still apply. This is not statement approval.')).length,1);
 for(const [value,en,ja] of [['bi_viewer','BI Viewer','BI 閲覧者'],['data_engineer','Data Engineer','データエンジニア'],['admin','Admin','管理者']])assert.equal($('observation-sre-role').options.find(o=>o.value===value).textContent,locale==='ja'?ja:en);
 assert.equal(JSON.stringify([dbObservations.sre.record,dbObservations.policy.record]),wire);assert.equal(JSON.stringify([sre,policy]),original);assert.equal(otherState(),before);assert.equal(calls.length,start);
}
// False permissions have human labels too, with unchanged boolean wire values.
const denied=observationReply('policy');await fetchObservation('policy',denied);for(const locale of ['en','ja']){setLanguage(locale);const before=otherState(),start=calls.length;dbObservationRender(dbObservations.policy);assert.ok(observationText('policy').includes(locale==='ja'?'書き込み: 許可なし':'Write: Not allowed'));assert.equal(dbObservations.policy.record.policy.schema_permissions.write,false);assert.equal(otherState(),before);assert.equal(calls.length,start);}
""")

    def test_sql_name_mode_parity_accepts_admin_custom_purpose_but_not_nonadmin_denied_schema(self):
        self.javascript(r"""
await boot();schemas[1].sql_name='sandbox';schemas[1].purpose='MARTS';schemas[1].allowed_modes=['read-only','sandbox-write','admin-write'];
await readyObservation('sre','admin','db','raw');const p=dbObservations.sre,reply=observationReply();reply.policy.schema_permissions.write=true;reply.policy.sql_modes=['read-only','sandbox-write','admin-write'];
const original=JSON.stringify(reply),context=p.context,before=otherState(),start=calls.length;await fetchObservation('sre',reply);assert.ok(p.record);assert.equal(p.context,context);assert.equal(p.context.sql_schema,'sandbox');assert.deepEqual(p.record.policy.sql_modes,schemas[1].allowed_modes);assert.equal(JSON.stringify(p.record),original);assert.equal(JSON.stringify(reply),original);assert.equal(otherState(),before);assert.equal(calls.length,start+1);
// A nonadmin must still pass server inventory query eligibility for this purpose/name pair.
schemas[1].capabilities.query=false;schemas[1].capabilities.write=false;
for(const role of ['data_engineer','bi_viewer']){
 changeObservation('sre','role',role);await dbObservationRefresh(p);changeObservation('sre','database','db');await dbObservationRefresh(p);changeObservation('sre','schema','raw');await dbObservationRefresh(p);assert.equal(p.context,null);assert.equal(dbObservationAllowed(p),false);assert.equal($('observation-sre-fetch').disabled,true);const count=calls.length;await dbObservationFetch(p);assert.equal(calls.length,count);assert.equal(p.record,null);
}
// Captured server modes must stay canonical and unique, with no guessed fallback.
changeObservation('sre','role','admin');schemas[1].capabilities.query=true;schemas[1].capabilities.write=true;
for(const modes of [['read-only','sandbox-write','sandbox-write'],['read-only','SANDBOX_WRITE']]){
 schemas[1].allowed_modes=modes;await dbObservationRefresh(p);changeObservation('sre','database','db');await dbObservationRefresh(p);changeObservation('sre','schema','raw');await dbObservationRefresh(p);assert.equal(p.context,null);assert.equal(p.ceiling,null);const count=calls.length;await dbObservationFetch(p);assert.equal(calls.length,count);
}
assert.equal(JSON.stringify(reply),original);
""")

    def test_guidance_disallowed_role_requests_permitted_role_without_side_effects(self):
        self.javascript(r"""
await boot();await readyObservation('sre');editSQL('SELECT kept worksheet');editDoctor('SELECT kept Doctor');currentRole='bi_viewer';dbControls();const p=dbObservations.sre;assert.equal(p.role,'admin');
for(const locale of ['en','ja']){setLanguage(locale);const before=otherState(),state=JSON.stringify(p),count=calls.length,key='Choose an execution role within the current role ceiling.';assert.equal(dbObservationGuidance(p),key);dbObservationRender(p);assert.equal($('observation-sre-output').children[0].textContent,locale==='ja'?'現在のロール上限内で実行ロールを選んでください。':key);assert.equal($('observation-sre-refresh').disabled,true);assert.equal($('observation-sre-fetch').disabled,true);assert.equal(JSON.stringify(p),state);assert.equal(otherState(),before);assert.equal(calls.length,count);}
changeObservation('sre','role','bi_viewer');assert.equal($('observation-sre-refresh').disabled,false);assert.equal($('observation-sre-fetch').disabled,true);await readyObservation('sre','bi_viewer');assert.equal($('observation-sre-fetch').disabled,false);
""")

    def test_guidance_revoked_nonempty_database_requests_authorized_database(self):
        self.javascript(r"""
await dualBoot();await readyObservation('sre');inventory=inventory.filter(d=>d.id!=='db');await dbObservationRefresh(dbObservations.sre);const p=dbObservations.sre;assert.equal(p.database,'db');assert.deepEqual(p.databases.map(d=>d.id),['finance']);
for(const locale of ['ja','en']){setLanguage(locale);const before=otherState(),state=JSON.stringify(p),count=calls.length,key='Choose a Database, then refresh context to load its schemas.';assert.equal(dbObservationGuidance(p),key);dbObservationRender(p);assert.equal($('observation-sre-output').children[0].textContent,t(key));assert.equal($('observation-sre-fetch').disabled,true);assert.equal(JSON.stringify(p),state);assert.equal(otherState(),before);assert.equal(calls.length,count);}
changeObservation('sre','database','finance');assert.equal($('observation-sre-fetch').disabled,true);await dbObservationRefresh(p);assert.equal($('observation-sre-fetch').disabled,true);changeObservation('sre','schema','finance-marts');assert.equal($('observation-sre-fetch').disabled,true);await dbObservationRefresh(p);assert.equal($('observation-sre-fetch').disabled,false);
""")

    def test_guidance_removed_nonempty_schema_requests_current_primary_schema(self):
        self.javascript(r"""
await boot();await readyObservation('sre');schemas=schemas.filter(s=>s.id!=='schema');await dbObservationRefresh(dbObservations.sre);const p=dbObservations.sre;assert.equal(p.schema,'schema');assert.deepEqual(p.schemas.map(s=>s.id),['raw']);
for(const locale of ['en','ja']){setLanguage(locale);const before=otherState(),state=JSON.stringify(p),count=calls.length,key='Choose a primary schema, then refresh context to verify it.';assert.equal(dbObservationGuidance(p),key);dbObservationRender(p);assert.equal($('observation-sre-output').children[0].textContent,t(key));assert.equal($('observation-sre-fetch').disabled,true);assert.equal(JSON.stringify(p),state);assert.equal(otherState(),before);assert.equal(calls.length,count);}
changeObservation('sre','schema','raw');assert.equal($('observation-sre-fetch').disabled,true);await dbObservationRefresh(p);assert.equal($('observation-sre-fetch').disabled,false);
""")

    def navigation_javascript(self, body):
        # The adapter extracts setLanguage from production (test_catalog_ui.function),
        # and installs production main-tab handlers. Add the real Bear wireTabs chain.
        handlers = harness.SCRIPT[harness.SCRIPT.index('const bearTabs='):harness.SCRIPT.index('let auditEpoch=')]
        self.javascript(r"""
const bearList=new Node();for(const kind of ['sre','query','data','catalog','policy']){const tab=$('bear-tab-'+kind);tab.tagName='BUTTON';tab.dataset.bear=kind;bearList.append(tab);$('bears').append($('bear-panel-'+kind));}$('bears').append(bearList);
eval(""" + json.dumps(handlers) + r""");
""" + body)

    def test_real_tabs_main_navigation_and_production_language_keep_both_dated_snapshots(self):
        self.navigation_javascript(r"""
await dualBoot();await readyObservation('sre','admin','finance','finance-marts');await readyObservation('policy');$('tab-bears').click();$('bear-tab-sre').click();
const sreReply=observationReply('sre');sreReply.observed_at='2026-11-01T01:00:00Z';await fetchObservation('sre',sreReply);$('bear-tab-policy').click();const policyReply=observationReply('policy');policyReply.observed_at='2026-11-01T02:00:00Z';await fetchObservation('policy',policyReply);
const sre=dbObservations.sre.record,policy=dbObservations.policy.record,sc=dbObservations.sre.context,pc=dbObservations.policy.context,count=calls.length;
editSQL('SELECT kept worksheet');editDoctor('SELECT kept Doctor');$('copilot-request').value='kept request';const held=dbExplanation.intents,curatorIntents=dbCurator.intents,proposalIntents=dbProposal.intents,saved=new Map([...storage].filter(([key])=>key!==languageKey));
for(const locale of ['ja','en','ja','en']){
 setLanguage(locale);assert.equal(document.documentElement.lang,locale);
 $('bear-tab-sre').click();assert.equal($('bear-tab-sre')['aria-selected'],'true');assert.equal($('bear-panel-sre').hidden,false);assert.ok(observationText('sre').includes('finance.marts'));assert.ok(observationText('sre').includes(sre.observed_at));assert.ok(observationText('sre').includes(locale==='ja'?'クエリ · 完了':'Query · Finished'));
 $('bear-tab-policy').click();assert.equal($('bear-tab-policy')['aria-selected'],'true');assert.equal($('bear-panel-policy').hidden,false);assert.ok(observationText('policy').includes('sales.marts'));assert.ok(observationText('policy').includes(policy.observed_at));assert.ok(observationText('policy').includes(locale==='ja'?'参照: 許可':'Browse: Allowed'));
 $('tab-warehouse').click();assert.equal(activeTab,'warehouse');$('tab-bears').click();assert.equal(activeTab,'bears');
 assert.equal(dbObservations.sre.record,sre);assert.equal(dbObservations.policy.record,policy);assert.equal(dbObservations.sre.context,sc);assert.equal(dbObservations.policy.context,pc);assert.equal(sre.context.database_id,'finance');assert.equal(policy.context.database_id,'db');assert.equal(sre.observed_at,'2026-11-01T01:00:00Z');assert.equal(policy.observed_at,'2026-11-01T02:00:00Z');assert.equal(calls.length,count);
 assert.equal($('sql').value,'SELECT kept worksheet');assert.equal($('bear-query-sql').value,'SELECT kept Doctor');assert.equal($('copilot-request').value,'kept request');assert.equal(dbExplanation.intents,held);assert.equal(dbCurator.intents,curatorIntents);assert.equal(dbProposal.intents,proposalIntents);assert.deepEqual(new Map([...storage].filter(([key])=>key!==languageKey)),saved);
}
""")

    def test_real_navigation_still_rejects_late_get_and_late_403_without_wiping_other_bear(self):
        self.navigation_javascript(r"""
await dualBoot();await readyObservation('sre','admin','finance','finance-marts');await readyObservation('policy');$('tab-bears').click();$('bear-tab-policy').click();await fetchObservation('policy');const retained=dbObservations.policy.record,context=dbObservations.policy.context;
for(const main of [false,true])for(const failure of [false,true]){
 $('bear-tab-sre').click();const old=deferred(),reply=observationReply('sre');route=()=>old.promise;const pending=dbObservationFetch(dbObservations.sre);await tick();
 if(main){$('tab-warehouse').click();$('tab-bears').click();}else{$('bear-tab-policy').click();$('bear-tab-sre').click();}
 old.resolve(failure?{http:403,body:{error:{code:'DB_ACCESS_REVOKED',message:'old private denial'}}}:reply);await pending;
 assert.equal(dbObservations.sre.record,null);assert.equal(dbObservations.policy.record,retained);assert.equal(dbObservations.policy.context,context);assert.doesNotMatch(observationText('sre'),/old private denial/);assert.ok(dbObservations.sre.context);
}
""")

    def test_retained_snapshots_still_purge_on_real_auth_platform_and_schema_hooks(self):
        self.navigation_javascript(r"""
session.platform_admin=true;await dualBoot();
async function populate(){await readyObservation('sre','bi_viewer','finance','finance-marts');await readyObservation('policy','admin');$('tab-bears').click();$('bear-tab-sre').click();await fetchObservation('sre');$('bear-tab-policy').click();await fetchObservation('policy');$('bear-tab-sre').click();}
await populate();const policy=dbObservations.policy.record,pc=dbObservations.policy.context;
dbInvalidateReceipt('bi_viewer');assert.equal(dbObservations.sre.record,null);assert.equal(dbObservations.sre.context,null);assert.equal(dbObservations.policy.record,policy);assert.equal(dbObservations.policy.context,pc);
await readyObservation('sre','bi_viewer','finance','finance-marts');await fetchObservation('sre');$('bear-tab-policy').click();$('bear-tab-sre').click();assert.ok(dbObservations.sre.record);changeObservation('sre','schema','');assert.equal(dbObservations.sre.record,null);assert.equal(dbObservations.sre.context,null);assert.equal(dbObservations.policy.record,policy);
await readyObservation('sre','bi_viewer','finance','finance-marts');await fetchObservation('sre');const removedSchema=dbObservations.sre.schemas.find(s=>s.id==='finance-marts');removedSchema.capabilities.query=false;dbControls();assert.equal(dbObservations.sre.record,null);assert.equal(dbObservations.sre.context,null);assert.equal(dbObservations.policy.record,policy);removedSchema.capabilities.query=true;
await readyObservation('sre','bi_viewer','finance','finance-marts');await fetchObservation('sre');session.platform_admin=false;dbAdminSession(session);for(const p of Object.values(dbObservations)){assert.equal(p.record,null);assert.equal(p.context,null);}
session.platform_admin=true;dbAdminSession(session);await populate();dbInvalidateReceipt('admin',true);for(const p of Object.values(dbObservations)){assert.equal(p.record,null);assert.equal(p.context,null);}
""")

    def test_existing_wrapping_scroll_components_and_no_new_css_or_product_inputs(self):
        self.javascript(r"""
await boot();narrow=true;for(const kind of ['sre','policy']){const panel=$('observation-'+kind+'-panel');assert.equal(panel.className,'panel-body');assert.equal($('observation-'+kind+'-database-controls').className,'table-wrap');assert.equal($('observation-'+kind+'-database-controls').children[0].className,'toolbar');assert.equal($('observation-'+kind+'-output').className,'table-wrap');assert.equal(panel.descendants().filter(n=>n.tagName==='INPUT'||n.tagName==='TEXTAREA').length,0);assert.equal(panel.descendants().filter(n=>n.tagName==='SELECT').length,4);assert.equal(panel.descendants().filter(n=>n.tagName==='BUTTON').length,4);}
""")


if __name__ == '__main__':
    unittest.main()
