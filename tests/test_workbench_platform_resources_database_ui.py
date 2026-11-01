"""Configured LOCAL resources: production handlers through Compute's stdin adapter."""
import unittest
from unittest.mock import patch

import test_workbench_compute_governance_database_ui as compute_ui


RESOURCES = r"""
const resourceReply=()=>({scope:'platform',warehouse:'LOCAL',selected_role:'admin',observed_at:'2026-11-02T01:02:03Z',coverage:{kind:'deployment_container_resource_configuration',pod_template:true,per_container:true,live_usage:false,effective_pod_resources:false,capacity:false},resources:{availability:'available',code:null,observed_at:'2026-11-02T01:02:02Z',deployments:['coordinator','worker'].map(role=>({role,containers:[{kind:'container',index:1,requests:{cpu:'0',memory:null},limits:{cpu:'1.50',memory:'2Gi'}},{kind:'init',index:1,requests:{cpu:'.5',memory:'128Mi'},limits:{cpu:null,memory:'0'}}]}))}});
const resourceCalls=()=>calls.filter(c=>c.path==='/api/v1/bears/platform-resources');
const resourceText=()=>visibleText($('observation-resources-output'));
async function resourceFetch(reply=resourceReply()){
 route=async(path,options)=>{
  if(path==='/api/v1/bears/platform-resources'){assert.deepEqual(options,{headers:{'X-Shirokuma-Role':'admin'}});return reply;}
  return runtimeFixtureRoute(path,options);
 };
 await $('observation-resources-fetch').onclick();
}
async function resourceBoot(){session.platform_admin=true;await boot();}
"""


class PlatformResourcesDatabaseUI(unittest.TestCase):
    def javascript(self, body):
        harness = compute_ui.platform_ui.observations.harness
        run = harness.subprocess.run

        def resource_adapter(args, **kwargs):
            if list(args) == ['node', '-']:
                source = kwargs['input'].replace(
                    'const $=id=>',
                    "const $=id=>id==='observation-resources-panel'&&!nodes[id]?.parentElement?null:", 1)
                # Add Response.text for the new endpoint's UTF-8 wire-size bound.
                source = source.replace('json:async()=>data.body',
                    'text:async()=>data.rawBody??JSON.stringify(data.body),json:async()=>data.body')
                source = source.replace('json:async()=>data};',
                    'text:async()=>data.rawBody??JSON.stringify(data),json:async()=>data};')
                kwargs['input'] = source
            return run(args, **kwargs)

        with patch.object(harness.subprocess, 'run', resource_adapter):
            compute_ui.ComputeGovernanceDatabaseUI.javascript(self, RESOURCES + body)

    def navigation_javascript(self, body):
        compute_ui.platform_ui.observations.SREPolicyDatabaseUI.navigation_javascript(self, body)

    def test_first_empty_database_auth_only_entry_and_single_sibling_mount(self):
        self.javascript(r"""
assert.equal($('observation-resources-panel'),null);session.platform_admin=true;inventory=[];schemas=[];worksheets=[];worksheetId='';
dbPrincipal=JSON.stringify([session.username,session.authenticated]);dbMaximumRole='admin';dbRuntimeInstall();
const panel=$('observation-resources-panel');assert.equal(panel.parentElement,$('compute'));assert.equal(panel.role,'region');assert.equal(panel['aria-labelledby'],'observation-resources-title');
assert.equal($('compute').children.indexOf(panel),$('compute').children.indexOf($('observation-compute-panel'))+1);
assert.equal(calls.length,0);assert.equal($('observation-resources-fetch').disabled,true);assert.equal($('observation-resources-auth').disabled,false);
for(let i=0;i<3;i++){dbRuntimeInstall();dbResourcesInstall();dbPlatformSync();}assert.equal($('observation-resources-panel'),panel);assert.equal($('compute').descendants().filter(n=>n.id===panel.id).length,1);
await $('observation-resources-auth').onclick();assert.deepEqual(calls.map(c=>c.path),['/api/session']);assert.equal(calls[0].options,undefined);
await resourceFetch();assert.ok(dbResources.record);assert.deepEqual(calls.map(c=>c.path),['/api/session','/api/v1/bears/platform-resources']);assert.equal(dbPlatform.record,null);assert.equal(worksheets.length,0);assert.equal(worksheetId,'');assert.equal(dbObservations.sre.context,null);
""")

    def test_non_p_false_like_p_and_lowered_role_direct_handler_gates(self):
        self.javascript(r"""
await boot();
for(const p of [false,'true',1,null,true])for(const role of ['admin','data_engineer','bi_viewer']){
 session.platform_admin=p;session.maximum_role=p===true?'data_engineer':'admin';currentRole=role;dbUpdateMaximumRole(session.maximum_role);dbAdminSession(session);dbControls();
 await dbResourcesRefreshAuth();await dbResourcesFetch();assert.equal(dbResources.record,null);assert.equal($('observation-resources-fetch').disabled,true);
}
assert.equal(resourceCalls().length,0);session.platform_admin=true;session.maximum_role='admin';dbUpdateMaximumRole('admin');currentRole='bi_viewer';dbAdminSession(session);await dbResourcesRefreshAuth();await dbResourcesFetch();assert.equal(resourceCalls().length,0);
""")

    def test_closed_dto_positive_quantities_null_and_per_kind_ordinals(self):
        self.javascript(r"""
await resourceBoot();
const values=['0','+0','0001','1.','1.50','.5','+.5','1n','1u','1m','1k','1M','1G','1T','1P','1E','1Ki','1Mi','1Gi','1Ti','1Pi','1Ei','1e0','1E+9','1e-9','9'.repeat(64),null];
for(const value of values){const r=resourceReply();for(const row of r.resources.deployments)for(const item of row.containers)for(const k of ['requests','limits'])for(const f of ['cpu','memory'])item[k][f]=value;await resourceFetch(r);assert.equal(dbResources.record.resources.deployments[0].containers[0].requests.cpu,value);assert.ok(Object.isFrozen(dbResources.record.resources.deployments[0].containers));}
const max=resourceReply();for(const row of max.resources.deployments)row.containers=Array.from({length:16},(_,i)=>({...structuredClone(row.containers[0]),kind:i<8?'container':'init',index:i%8+1})).reverse();await resourceFetch(max);assert.equal(dbResources.record.resources.deployments[1].containers.length,16);
const ordinary=resourceReply();for(const row of ordinary.resources.deployments)row.containers.length=1;await resourceFetch(ordinary);assert.ok(dbResources.record);
""")

    def test_quantity_grammar_rejects_malformed_non_ascii_negative_and_overlong(self):
        self.javascript(r"""
await resourceBoot();for(const value of ['', ' ', ' 1','1 ','1\n','1\r','1\t','１','1µ','1\u0000','-0','-1','-.5','NaN','Infinity',0,1,{},[],true,'.','+','1e','1e+','1e1.2','1mi','1ki','1kiB','1GiB','1b','1K','1N','1U','1mm','1e2M','1..2','1_000','0x10','9'.repeat(65)]){
 const r=resourceReply();r.resources.deployments[0].containers[0].requests.cpu=value;await resourceFetch(r);assert.equal(dbResources.record,null,JSON.stringify(value));assert.doesNotMatch(resourceText(),/INVALID_RESPONSE|NaN|SECRET/);
}
""")

    def test_closed_dto_negative_structure_coverage_roles_caps_indices_and_unavailable(self):
        self.javascript(r"""
await resourceBoot();const changes=[
 r=>r.extra=true,r=>delete r.scope,r=>r.scope='database',r=>r.warehouse='OTHER',r=>r.selected_role='bi_viewer',r=>r.observed_at='bad',r=>r.observed_at='2026-02-30T00:00:00Z',r=>r.coverage.extra=true,r=>r.coverage.kind='other',
 ...['pod_template','per_container'].map(k=>r=>r.coverage[k]=false),...['live_usage','effective_pod_resources','capacity'].map(k=>r=>r.coverage[k]=true),
 r=>r.resources.extra=true,r=>r.resources.observed_at=null,r=>r.resources.availability='unknown',r=>r.resources.code='SECRET',r=>r.resources.deployments=null,r=>r.resources.deployments.pop(),r=>r.resources.deployments.reverse(),r=>r.resources.deployments[1].role='coordinator',r=>r.resources.deployments[0].name='SECRET',
 r=>r.resources.deployments[0].containers=[],r=>r.resources.deployments[0].containers=Array(17).fill(r.resources.deployments[0].containers[0]),r=>r.resources.deployments[0].containers.shift(),
 ...[0,-1,1.5,'1',2].map(v=>r=>r.resources.deployments[0].containers[0].index=v),r=>r.resources.deployments[0].containers.push(structuredClone(r.resources.deployments[0].containers[0])),
 r=>r.resources.deployments[0].containers.push(structuredClone(r.resources.deployments[0].containers[1])),r=>r.resources.deployments[0].containers[1].index=2,r=>r.resources.deployments[0].containers[0].kind='sidecar',r=>r.resources.deployments[0].containers[0].name='SECRET',r=>r.resources.deployments[0].containers[0].requests.extra='1',r=>delete r.resources.deployments[0].containers[0].limits.cpu,
 r=>{r.resources.availability='unavailable';r.resources.code='RESOURCE_CONFIGURATION_UNAVAILABLE';}
];
for(const change of changes){const r=resourceReply();change(r);await resourceFetch(r);assert.equal(dbResources.record,null);}
const missing=resourceReply();missing.resources={availability:'unavailable',code:'RESOURCE_CONFIGURATION_UNAVAILABLE',observed_at:missing.observed_at,deployments:null};await resourceFetch(missing);assert.ok(dbResources.record);assert.match(resourceText(),/Resource configuration source unavailable/);assert.doesNotMatch(resourceText(),/CPU request: 0|Not specified/);
""")

    def test_utf8_wire_65536_boundary_and_stale_body_read(self):
        self.navigation_javascript(r"""
await resourceBoot();$('tab-compute').click();const raw=JSON.stringify(resourceReply());const exact=raw+' '.repeat(65536-new TextEncoder().encode(raw).length);await resourceFetch({rawBody:exact});assert.ok(dbResources.record);
await resourceFetch({rawBody:exact+' '});assert.equal(dbResources.record,null);
// Multi-byte input uses encoded bytes, not JS character count, and fails closed.
await resourceFetch({rawBody:raw+' '.repeat(65536-raw.length-1)+'é'});assert.equal(dbResources.record,null);
const body=deferred();route=async()=>({rawBody:body.promise});const pending=dbResourcesFetch();await tick();$('tab-warehouse').click();$('tab-compute').click();await resourceFetch();const saved=dbResources.record;body.resolve('malformed SECRET');await pending;assert.equal(dbResources.record,saved);
""")

    def test_render_human_bilingual_early_time_null_explicit_zero_and_raw_values(self):
        self.javascript(r"""
await resourceBoot();narrow=true;await resourceFetch();const r=dbResources.record,panel=$('observation-resources-panel');assert.equal(panel.className,'panel');assert.equal(panel.children[1].children[0].className,'toolbar');assert.equal(panel.descendants().filter(n=>n.tagName==='BUTTON').length,2);assert.equal(panel.descendants().filter(n=>['INPUT','TEXTAREA','SELECT'].includes(n.tagName)).length,0);
for(const locale of ['ja','en']){setLanguage(locale);dbControls();const output=$('observation-resources-output'),lines=output.children.map(n=>n.textContent),text=resourceText();assert.equal(lines[1],t('Observed at')+': '+r.observed_at);assert.ok(text.includes(t('Not specified')));assert.ok(lines.includes(t('CPU request')+': 0'));assert.ok(lines.includes(t('CPU limit')+': 1.50'));assert.ok(lines.includes(t('Memory limit')+': 2Gi'));assert.ok(text.includes(t('Init container')+' 1'));assert.ok(text.includes(t('Deployment Pod-template settings for each container, including init containers, by ordinal.')));assert.ok(text.includes(t('These configured values do not measure live usage, effective Pod resources or capacity. Resizing is not provided here.')));assert.equal($('observation-resources-title').textContent,t('Configured LOCAL container resources (P-only, read-only)'));assert.doesNotMatch(text,/deployment_container_resource_configuration|RESOURCE_CONFIGURATION_UNAVAILABLE|requests|effective_pod_resources/);if(locale==='ja')assert.notEqual(t('Not specified'),'Not specified');}
const bad=resourceReply();bad.resources.deployments[0].containers[0].requests.cpu='<img src=x>';await resourceFetch(bad);assert.equal(dbResources.record,null);assert.doesNotMatch(resourceText(),/<img/);assert.equal(panel.descendants().filter(n=>n.tagName==='IMG').length,0);
""")

    def test_c2b_busy_and_get_are_independent_and_database_changes_keep_capture(self):
        self.javascript(r"""
await resourceBoot();const old=deferred();route=()=>old.promise;const pending=dbPlatformFetch();await tick();const token=dbPlatform.busy;
await resourceFetch();assert.ok(dbResources.record);assert.equal(dbPlatform.busy,token);assert.equal(platformCalls().length,1);assert.equal(resourceCalls().length,1);old.resolve(platformReply());await pending;const c2b=dbPlatform.record;
const delayed=deferred();route=()=>delayed.promise;const reading=dbResourcesFetch();await tick();const own=dbResources.busy;await computeFetch();assert.equal(dbResources.busy,own);assert.ok(dbPlatform.record);const current=dbPlatform.record;
dbEpoch++;dbAuthorizationVersion++;dbInventoryVersion++;dbCurrent().database_id='different';dbControls();assert.equal(dbResources.busy,own);delayed.resolve(resourceReply());await reading;assert.ok(dbResources.record);assert.equal(dbPlatform.record,current);assert.notEqual(c2b,current);
""")

    def test_real_navigation_language_cache_retained_manual_only_and_stale_success_denied(self):
        self.navigation_javascript(r"""
await resourceBoot();$('tab-compute').click();assert.equal(resourceCalls().length,0);await resourceFetch();const saved=dbResources.record,count=resourceCalls().length;
for(const locale of ['ja','en','ja','en']){setLanguage(locale);$('tab-bears').click();$('bear-tab-policy').click();$('tab-warehouse').click();$('tab-compute').click();assert.equal(dbResources.record,saved);assert.ok(resourceText().includes(saved.observed_at));assert.equal(resourceCalls().length,count);}
for(const status of [200,401,403]){const old=deferred();route=()=>old.promise;const pending=dbResourcesFetch();await tick();$('tab-warehouse').click();assert.equal(dbResources.busy,null);$('tab-compute').click();await resourceFetch();const fresh=dbResources.record;old.resolve(status===200?resourceReply():{http:status,body:{error:'SECRET'}});await pending;assert.equal(dbResources.record,fresh);assert.equal(dbAdmin.platform,true);}
""")

    def test_hidden_purge_even_c2b_empty_and_stale_callbacks_cannot_restore(self):
        self.navigation_javascript(r"""
await resourceBoot();const losses=[()=>{session.platform_admin=false;dbAdminSession(session);},()=>{currentRole='bi_viewer';dbControls();},()=>dbUpdateMaximumRole('bi_viewer'),()=>{dbPrincipal=JSON.stringify(['other',true]);dbControls();},()=>{dbAdmin.platform=false;dbAdminClear();}];
for(const lose of losses){currentRole='admin';dbPrincipal=JSON.stringify(['owner',true]);session.platform_admin=true;session.maximum_role='admin';dbUpdateMaximumRole('admin');dbAdminSession(session);$('tab-compute').click();await resourceFetch();assert.equal(dbPlatform.record,null);const old=deferred();route=()=>old.promise;const pending=dbResourcesFetch();await tick();$('tab-warehouse').click();lose();assert.equal(dbResources.record,null);assert.doesNotMatch(resourceText(),/CPU request: 0|2026-11-02/);old.resolve(resourceReply());await pending;assert.equal(dbResources.record,null);}
""")

    def test_current_resource_denial_no_body_parse_and_current_session_denial_purges(self):
        self.javascript(r"""
await resourceBoot();for(const status of [401,403])for(const kind of ['resource','session']){sessionRoute=null;dbAdminSession(session);await resourceFetch();let parsed=0;const denied={http:status};Object.defineProperty(denied,'body',{get(){parsed++;throw SyntaxError('SECRET');}});
if(kind==='resource')await resourceFetch(denied);else{sessionRoute=()=>denied;await dbResourcesRefreshAuth();}assert.equal(parsed,0);assert.equal(dbResources.record,null);assert.equal(dbAdmin.platform,false);assert.doesNotMatch(resourceText(),/SECRET/);}
""")

    def test_stale_auth_response_and_body_cannot_downgrade_or_restore(self):
        self.navigation_javascript(r"""
await resourceBoot();$('tab-compute').click();for(const bodyStage of [false,true])for(const denied of [false,true]){
const old=deferred();sessionRoute=()=>bodyStage?{http:200,body:old.promise}:old.promise;const pending=dbResourcesRefreshAuth();await tick();$('tab-warehouse').click();$('tab-compute').click();sessionRoute=null;await dbResourcesRefreshAuth();await resourceFetch();const saved=dbResources.record;
old.resolve(denied?{http:403,body:{platform_admin:false}}:{...session,authenticated:false,platform_admin:false,maximum_role:'bi_viewer'});await pending;assert.equal(dbResources.record,saved);assert.equal(dbAdmin.platform,true);assert.equal(dbMaximumRole,'admin');}
""")

    def test_same_authority_preserves_four_drafts_unknown_runtime_receipts_and_audit_filters(self):
        self.javascript(compute_ui.platform_ui.CURATOR + compute_ui.platform_ui.PROPOSAL + r"""
const nativeAppend=Node.prototype.append;Node.prototype.append=function(...items){return nativeAppend.apply(this,items.map(n=>typeof n==='string'?Object.assign(new Node('text'),{textContent:n}):n));};
session.platform_admin=true;await boot();request('proposal');await choose();await submit(pendingProposal({state:'UNKNOWN',inference_state:'UNKNOWN'}));assert.equal(dbProposalHeld().length,1);
await curatorContext();objects=[curatorObject()];await dbCuratorLoad();change('object','0');await curatorSubmit(curatorPending({state:'UNKNOWN',inference_state:'UNKNOWN'}));assert.equal(dbCuratorHeld().length,1);
route=async()=>wrap(record({state:'UNKNOWN',text:null,inference_state:'UNKNOWN'}));await $('copilot-explain').onclick();route=async()=>envelope(dbRuns.get(worksheetId),{state:'UNKNOWN'});await dbRunQuery();
const wap=dataopsPanels.get('p35-wap');schemas[0].capabilities.wap_models=true;route=async path=>({context:JSON.parse(new URL(path,'http://fixture').searchParams.get('context')),items:[]});await dataopsRefresh(wap);route=async()=>{throw Error('lost ACK');};await dataopsSubmit(wap,'/api/v1/wap/build',{model_id:'existing-model',version:1},{});
editSQL('SELECT kept SQL');editDoctor('SELECT kept Doctor');request('kept Copilot');$('p35-wap-sql').value='SELECT kept WAP';wap.sources[0].alias.value='kept';
await readyObservation('sre');await readyObservation('policy');await fetchObservation('sre');await fetchObservation('policy');const sre=dbObservations.sre.record,policy=dbObservations.policy.record;
route=runtimeFixtureRoute;await refreshStatus();const input=$('runtime-minutes');input.value='47';input.oninput();actionRoute=async()=>{throw Error('lost runtime ACK');};await dbRuntimeAct('wake');assert.ok(dbRuntime.intent);runtime.policy={minutes:12,revision:4};await refreshStatus();assert.equal(dbRuntime.conflict,true);assert.equal(dbRuntime.base,3);assert.equal(dbRuntime.dirty,true);input.focus();
dbAuditInstall();$('db-audit-source').value='ai';$('db-audit-source').oninput();$('db-audit-search').value='sql_proposal';$('db-audit-search').oninput();const auditBefore=JSON.stringify(dbAudit);
const before=otherState(),runtimeBefore=runtimeState(),intent=dbRuntime.intent,key=intent.key,op=wap.op,observerStart=calls.length;
await $('observation-resources-auth').onclick();await resourceFetch();const missing=resourceReply();Object.assign(missing.resources,{availability:'unavailable',code:'RESOURCE_CONFIGURATION_UNAVAILABLE',deployments:null});await resourceFetch(missing);await resourceFetch({http:500,body:{error:'SECRET'}});
assert.equal(JSON.stringify(dbAudit),auditBefore);assert.equal(otherState(),before);assert.equal(runtimeState(),runtimeBefore);assert.equal(document.activeElement,input);assert.equal(input.value,'47');assert.equal(dbRuntime.intent,intent);assert.equal(dbRuntime.intent.key,key);assert.equal(wap.op,op);assert.equal(wap.op.ambiguous,true);assert.equal(dbRuns.get(worksheetId).state,'UNKNOWN');assert.equal(dbProposalHeld().length,1);assert.equal(dbCuratorHeld().length,1);assert.equal(dbObservations.sre.record,sre);assert.equal(dbObservations.policy.record,policy);assert.ok(calls.slice(observerStart).every(c=>['/api/session','/api/v1/bears/platform-resources'].includes(c.path)&&!c.options?.method));
""")

if __name__ == '__main__':
    unittest.main()
