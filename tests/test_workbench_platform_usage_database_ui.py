"""Production Compute mount and independent manual usage state through Node DOM."""
import unittest
import json
from datetime import datetime, timezone
import test_database_platform_usage as backend
from unittest.mock import patch
import test_workbench_platform_resources_database_ui as resources_ui

USAGE = r"""
const usageReply=()=>({scope:'platform',warehouse:'LOCAL',selected_role:'admin',observed_at:new Date().toISOString(),coverage:{kind:'fixed_local_container_usage',cpu:'window_average',memory:'working_set_sample',capacity:false,database_dedicated:false},availability:'available',code:null,samples:['coordinator','worker'].map(role=>({role,container_ordinal:1,sampled_at:new Date(Date.now()-10000).toISOString(),window_seconds:'30.001',cpu_cores:'0',cpu_millicores:'0',memory_bytes:'0'}))});
const usageCalls=()=>calls.filter(c=>c.path==='/api/v1/bears/platform-usage');
const usageText=()=>visibleText($('observation-usage-output'));
async function usageFetch(reply=usageReply()){
 route=async(path,options)=>{if(path==='/api/v1/bears/platform-usage'){assert.deepEqual(options,{headers:{'X-Shirokuma-Role':'admin'}});return reply;}return runtimeFixtureRoute(path,options);};
 await $('observation-usage-fetch').onclick();
}
async function usageBoot(){session.platform_admin=true;await boot();}
"""

class PlatformUsageDatabaseUI(unittest.TestCase):
    def javascript(self,body):
        harness=resources_ui.compute_ui.platform_ui.observations.harness
        run=harness.subprocess.run
        def adapter(args,**kwargs):
            if list(args)==['node','-']:
                kwargs['input']=kwargs['input'].replace('const $=id=>',"const $=id=>id==='observation-usage-panel'&&!nodes[id]?.parentElement?null:",1)
            return run(args,**kwargs)
        with patch.object(harness.subprocess,'run',adapter):
            resources_ui.PlatformResourcesDatabaseUI.javascript(self,USAGE+body)

    def navigation_javascript(self,body):
        resources_ui.compute_ui.platform_ui.observations.SREPolicyDatabaseUI.navigation_javascript(self,body)

    def test_backend_high_precision_projection_renders_exactly(self):
        with patch.object(backend, 'NOW', datetime.now(timezone.utc).replace(microsecond=0)):
            foundation, data = backend.fixture()
        data[3]['items'][0]['containers'][0]['usage'] = dict(
            cpu='1.23456789012345678901234567890123456789e-20',
            memory='0.123456789012345678901234567890123456789Ki')
        current = backend.usage._source(*data[:3], foundation)[0]
        samples = backend.usage._samples(data[3], current, foundation, datetime.now(timezone.utc))
        self.assertGreater(len(samples[0]['cpu_cores'].split('.')[1]), 30)
        self.javascript(r"""
await usageBoot();const r=usageReply();r.samples=""" + json.dumps(samples) + r""";
await usageFetch(r);assert.ok(dbUsage.record);
for(const field of ['cpu_cores','cpu_millicores','memory_bytes']){assert.equal(dbUsage.record.samples[0][field],r.samples[0][field]);assert.ok(usageText().includes(r.samples[0][field]));}
const bad=structuredClone(r);bad.samples[0].cpu_millicores+='1';await usageFetch(bad);assert.equal(dbUsage.record,null);
for(const value of ['0.'+'0'.repeat(30)+'1','0.'+'0'.repeat(94)+'1','1.0','+1','01']){const bad=structuredClone(r);bad.samples[0].memory_bytes=value;await usageFetch(bad);assert.equal(dbUsage.record,null);}
assert.throws(()=>dbUsageDecimal('1'.repeat(97)));assert.throws(()=>dbUsageDecimal('1000000000000000000000000.1'));
""")

    def test_same_authority_auth_notice_and_settings_reference_bilingual(self):
        self.javascript(r"""
await usageBoot();editSQL('SELECT kept SQL');editDoctor('SELECT kept Doctor');$('copilot-request').value='kept Copilot';$('p35-wap-sql').value='SELECT kept WAP';
await usageFetch();const usageDrafts=()=>JSON.stringify([worksheets,worksheetId,$('sql').value,$('bear-query-sql').value,$('copilot-request').value,$('p35-wap-sql').value]);const usageSnapshot=dbUsage.record,before=usageDrafts(),usageCallStart=calls.length;
for(const locale of ['ja','en']){
 setLanguage(locale);await dbUsageRefreshAuth();dbControls();
 const key='Usage authority verified. Select Fetch usage samples.';assert.equal(dbUsage.notice,key);assert.ok(usageText().includes(t(key)));assert.equal(dbUsage.record,usageSnapshot);
 const reference=locale==='ja'?'設定 requests／limits は「LOCAL コンテナの設定リソース」で確認できます。':'Configured requests/limits are shown in “'+t('Configured LOCAL container resources (P-only, read-only)')+'”.';assert.ok(usageText().includes(reference));
 setLanguage(locale==='ja'?'en':'ja');dbControls();assert.ok(usageText().includes(t(key)));assert.equal(usageDrafts(),before);
}
assert.ok(calls.slice(usageCallStart).every(c=>c.path==='/api/session'));assert.doesNotMatch(usageText(),/Resource authority verified/);
""")

    def test_actual_compute_mount_manual_only_without_database_schema_grants(self):
        self.navigation_javascript(r"""
assert.equal($('observation-usage-panel'),null);inventory=[];schemas=[];worksheets=[];worksheetId='';session.platform_admin=true;dbPrincipal=JSON.stringify([session.username,session.authenticated]);dbMaximumRole='admin';dbRuntimeInstall();
const panel=$('observation-usage-panel');assert.equal(panel.parentElement,$('compute-usage-list'));assert.equal(panel.role,'region');assert.equal(panel['aria-labelledby'],'observation-usage-title');assert.equal(panel.className,'panel');
assert.equal($('compute-usage-list').children.indexOf(panel),$('compute-usage-list').children.indexOf($('observation-resources-panel'))+1);
assert.equal(calls.length,0);assert.equal($('observation-usage-fetch').disabled,true);await $('observation-usage-auth').onclick();assert.deepEqual(calls.map(c=>c.path),['/api/session']);await usageFetch();assert.ok(dbUsage.record);assert.equal(dbResources.record,null);assert.equal(dbPlatform.record,null);
const saved=dbUsage.record,count=calls.length;for(const locale of ['ja','en']){setLanguage(locale);$('tab-warehouse').click();$('tab-compute').click();dbRuntimeInstall();dbUsageInstall();assert.equal(dbUsage.record,saved);assert.equal(calls.length,count);}
assert.equal($('compute').descendants().filter(n=>n.id===panel.id).length,1);assert.match($('observation-usage-output').getAttribute('style'),/overflow-wrap:anywhere;min-width:0/);
""")

    def test_true_p_only_independent_busy_handle_and_database_switch(self):
        self.javascript(r"""
await usageBoot();
const delayed=deferred();route=()=>delayed.promise;const pending=dbUsageFetch();await tick();const token=dbUsage.busy;assert.ok(token);
for(let i=0;i<3;i++){dbControls();dbPlatformSync();await dbUsageFetch();await dbUsageRefreshAuth();assert.equal(dbUsage.busy,token);}assert.equal(usageCalls().length,1);
dbEpoch++;dbAuthorizationVersion++;dbInventoryVersion++;dbCurrent().database_id='other';dbControls();assert.equal(dbUsage.busy,token);const before=otherState(),runtimeBefore=runtimeState();delayed.resolve(usageReply());await pending;assert.ok(dbUsage.record);assert.equal(dbUsage.busy,null);
await resourceFetch();assert.ok(dbUsage.record);assert.ok(dbResources.record);assert.equal(runtimeState(),runtimeBefore);assert.equal(otherState(),before);
for(const p of [false,'true',1,null]){session.platform_admin=p;dbAdminSession(session);await dbUsageFetch();assert.equal(dbUsage.record,null);}
session.platform_admin=true;dbAdminSession(session);currentRole='bi_viewer';dbControls();const count=usageCalls().length;await dbUsageFetch();assert.equal(usageCalls().length,count);assert.equal(dbUsage.record,null);
""")

    def test_closed_dto_units_exact_relationship_bounds_and_freshness(self):
        self.javascript(r"""
await usageBoot();const changes=[r=>r.extra=true,r=>delete r.scope,r=>r.scope='database',r=>r.warehouse='other',r=>r.selected_role='data_engineer',r=>r.coverage.extra=true,r=>r.coverage.capacity=true,r=>r.coverage.database_dedicated=true,r=>r.coverage.cpu='instant',r=>r.coverage.memory='rss',r=>r.code='SECRET',r=>r.availability='unknown',r=>r.samples=null,r=>r.samples=[],r=>r.samples.pop(),r=>r.samples.reverse(),r=>r.samples.push({...r.samples[0]}),r=>r.samples[0].name='SECRET',r=>r.samples[0].container_ordinal=0,r=>r.samples[0].role='other',r=>r.samples[0].cpu_millicores='1',r=>r.samples[0].window_seconds='0',r=>r.samples[0].window_seconds='121',r=>r.samples[0].window_seconds='1e1',r=>r.samples[0].window_seconds='1.0000000001',r=>r.observed_at='2026-02-30T00:00:00Z',r=>r.observed_at=new Date(Date.now()-121000).toISOString(),r=>r.samples[0].sampled_at=new Date(Date.now()-121000).toISOString(),r=>r.samples[0].sampled_at=new Date(Date.now()+6000).toISOString(),r=>r.observed_at=new Date(Date.now()+6000).toISOString()];
for(const change of changes){const r=usageReply();change(r);await usageFetch(r);assert.equal(dbUsage.record,null);}
for(const value of [null,0,true,'-1','NaN','Infinity','+0','00','1.0','1e3','1m','1Ki','1 ','1\n','1'.repeat(129),'1000000000000000000000001','0.'+'0'.repeat(30)+'1']){const r=usageReply();r.samples[0].memory_bytes=value;await usageFetch(r);assert.equal(dbUsage.record,null,JSON.stringify(value));}
for(const [cores,milli,memory] of [['0','0','0'],['0.000000001','0.000001','1024'],['0.000001','0.001','1572864'],['0.125','125','2147483648'],['1000','1000000','1000000000000000000000000']]){const r=usageReply();Object.assign(r.samples[0],{cpu_cores:cores,cpu_millicores:milli,memory_bytes:memory});await usageFetch(r);assert.ok(dbUsage.record);assert.ok(Object.isFrozen(dbUsage.record.samples));}
const missing=usageReply();Object.assign(missing,{availability:'unavailable',code:'USAGE_UNAVAILABLE',samples:null});await usageFetch(missing);assert.ok(dbUsage.record);assert.match(usageText(),/Usage source unavailable/);assert.doesNotMatch(usageText(),/CPU cores: 0/);
const bad=usageReply();Object.assign(bad,{availability:'unavailable',code:'USAGE_UNAVAILABLE'});await usageFetch(bad);assert.equal(dbUsage.record,null);
""")

    def test_real_navigation_auth_role_owner_epoch_aba_and_late_body(self):
        self.navigation_javascript(r"""
await usageBoot();$('tab-compute').click();
for(const status of [200,401,403]){
 const old=deferred();route=()=>old.promise;const pending=dbUsageFetch();await tick();$('tab-warehouse').click();$('tab-compute').click();await usageFetch();const fresh=dbUsage.record;old.resolve(status===200?usageReply():{http:status,body:{error:'SECRET'}});await pending;assert.equal(dbUsage.record,fresh);assert.equal(dbAdmin.platform,true);
}
for(const loss of [()=>{currentRole='bi_viewer';dbControls();},()=>{dbPrincipal=JSON.stringify(['other',true]);dbControls();},()=>dbUpdateMaximumRole('bi_viewer'),()=>{session.platform_admin=false;dbAdminSession(session);},()=>dbAdminClear()]){
 session.platform_admin=true;session.maximum_role='admin';dbPrincipal=JSON.stringify(['owner',true]);currentRole='admin';dbUpdateMaximumRole('admin');dbAdminSession(session);await usageFetch();const old=deferred();route=()=>({rawBody:old.promise});const pending=dbUsageFetch();await tick();loss();assert.equal(dbUsage.record,null);assert.doesNotMatch(usageText(),/CPU cores: 0/);
 session.platform_admin=true;session.maximum_role='admin';dbPrincipal=JSON.stringify(['owner',true]);currentRole='admin';dbUpdateMaximumRole('admin');dbAdminSession(session);await usageFetch();const fresh=dbUsage.record;old.resolve(JSON.stringify(usageReply()));await pending;assert.equal(dbUsage.record,fresh);
}
""")

    def test_current_denial_purges_no_body_parse_stale_session_fenced(self):
        self.navigation_javascript(r"""
await usageBoot();$('tab-compute').click();for(const status of [401,403]){session.platform_admin=true;dbAdminSession(session);await usageFetch();let parsed=0;const denied={http:status};Object.defineProperty(denied,'body',{get(){parsed++;throw Error('SECRET');}});await usageFetch(denied);assert.equal(parsed,0);assert.equal(dbUsage.record,null);assert.equal(dbAdmin.platform,false);}
session.platform_admin=true;dbAdminSession(session);
for(const bodyStage of [false,true]){const old=deferred();sessionRoute=()=>bodyStage?{http:200,body:old.promise}:old.promise;const pending=dbUsageRefreshAuth();await tick();$('tab-warehouse').click();$('tab-compute').click();sessionRoute=null;await dbUsageRefreshAuth();await usageFetch();const fresh=dbUsage.record;old.resolve({http:403,body:{...session,platform_admin:false}});await pending;assert.equal(dbUsage.record,fresh);assert.equal(dbAdmin.platform,true);}
""")

    def test_wire_bounds_sanitized_failure_and_bilingual_visible_zero(self):
        self.javascript(r"""
await usageBoot();narrow=true;const raw=JSON.stringify(usageReply());await usageFetch({rawBody:raw+' '.repeat(32768-new TextEncoder().encode(raw).length)});assert.ok(dbUsage.record);
await usageFetch({rawBody:raw+' '.repeat(32769-new TextEncoder().encode(raw).length)});assert.equal(dbUsage.record,null);
await usageFetch({http:500,body:{error:'SECRET'}});assert.equal(dbUsage.record,null);assert.doesNotMatch(usageText(),/SECRET|INVALID_RESPONSE/);
await usageFetch();const saved=dbUsage.record;for(const locale of ['ja','en']){setLanguage(locale);dbControls();const text=usageText();assert.ok(text.includes(t('Observed at')+': '+saved.observed_at));assert.ok(text.includes(t('CPU cores')+': 0 cores'));assert.ok(text.includes(t('CPU millicores')+': 0 mCPU'));assert.ok(text.includes(t('Memory working set')+': 0 '+t('bytes')));assert.ok(text.includes(t('CPU averaging window')+': 30.001 '+t('seconds')));assert.ok(text.includes(t('Shared LOCAL compute; not dedicated to a database. No capacity or headroom measurement.')));if(locale==='ja')assert.notEqual(t('Sampled at'),'Sampled at');assert.equal($('observation-usage-title').textContent,t('Shared LOCAL CPU and memory usage (P-only, read-only)'));}
""")

    def test_same_authority_preserves_drafts_unknown_handles_and_audit(self):
        self.javascript(resources_ui.compute_ui.platform_ui.CURATOR + resources_ui.compute_ui.platform_ui.PROPOSAL + r"""
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
await $('observation-usage-auth').onclick();await usageFetch();const missing=usageReply();Object.assign(missing,{availability:'unavailable',code:'USAGE_UNAVAILABLE',samples:null});await usageFetch(missing);await usageFetch({http:500,body:{error:'SECRET'}});
assert.equal(JSON.stringify(dbAudit),auditBefore);assert.equal(otherState(),before);assert.equal(runtimeState(),runtimeBefore);assert.equal(document.activeElement,input);assert.equal(input.value,'47');assert.equal(dbRuntime.intent,intent);assert.equal(dbRuntime.intent.key,key);assert.equal(wap.op,op);assert.equal(wap.op.ambiguous,true);assert.equal(dbRuns.get(worksheetId).state,'UNKNOWN');assert.equal(dbProposalHeld().length,1);assert.equal(dbCuratorHeld().length,1);assert.equal(dbObservations.sre.record,sre);assert.equal(dbObservations.policy.record,policy);assert.ok(calls.slice(observerStart).every(c=>['/api/session','/api/v1/bears/platform-usage'].includes(c.path)&&!c.options?.method));
""")

if __name__=='__main__': unittest.main()
