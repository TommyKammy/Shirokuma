"""Production independent Compute capacity panel with exact closed DTOs."""
import unittest
from unittest.mock import patch
import test_workbench_platform_resources_database_ui as resources_ui

CAPACITY = r"""
const capacityReply=()=>{const sampled=new Date(Date.now()-10000).toISOString(),fs={capacity_bytes:'18446744073709551615',available_bytes:'9007199254740993',used_bytes:'9',sampled_at:sampled};return {scope:'platform',warehouse:'LOCAL',selected_role:'admin',observed_at:new Date().toISOString(),coverage:{kind:'configured_shared_kubernetes_vm_node',cpu:'node_capacity_and_allocatable_cores',memory:'kubelet_limit_minus_working_set_estimate',allocatable:'scheduler_pod_budget_not_free',root_fs:'node_root_filesystem',runtime_fs:'independent_optional_filesystems_never_sum',database_dedicated:false,mac_host:false,admission:false},availability:'available',code:null,core:{cpu_capacity_cores:'1.5',cpu_allocatable_cores:'1.25',memory_capacity_bytes:'18446744073709551615',memory_allocatable_bytes:'9007199254740993',memory_available_bytes:'0',memory_sampled_at:sampled,node_started_at:new Date(Date.now()-600000).toISOString(),root_fs:structuredClone(fs)},image_fs:structuredClone(fs),container_fs:structuredClone(fs)};};
const capacityCalls=()=>calls.filter(c=>c.path==='/api/v1/bears/platform-capacity');
const capacityText=()=>visibleText($('observation-capacity-output'));
async function capacityFetch(reply=capacityReply()){
 route=async(path,options)=>{if(path==='/api/v1/bears/platform-capacity'){assert.deepEqual(options,{headers:{'X-Shirokuma-Role':'admin'}});return reply;}return runtimeFixtureRoute(path,options);};
 await $('observation-capacity-fetch').onclick();
}
async function capacityBoot(){session.platform_admin=true;await boot();}
"""

class PlatformCapacityDatabaseUI(unittest.TestCase):
    def javascript(self,body):
        harness=resources_ui.compute_ui.platform_ui.observations.harness
        run=harness.subprocess.run
        def adapter(args,**kwargs):
            if list(args)==['node','-']:
                kwargs['input']=kwargs['input'].replace('const $=id=>',"const $=id=>id==='observation-capacity-panel'&&!nodes[id]?.parentElement?null:",1)
            return run(args,**kwargs)
        with patch.object(harness.subprocess,'run',adapter):
            resources_ui.PlatformResourcesDatabaseUI.javascript(self,CAPACITY+body)

    def navigation_javascript(self,body):
        resources_ui.compute_ui.platform_ui.observations.SREPolicyDatabaseUI.navigation_javascript(self,body)

    def test_actual_mount_manual_only_no_database_and_independent_caches(self):
        self.javascript(r"""
assert.equal($('observation-capacity-panel'),null);session.platform_admin=true;inventory=[];schemas=[];worksheets=[];worksheetId='';dbPrincipal=JSON.stringify([session.username,session.authenticated]);dbMaximumRole='admin';dbRuntimeInstall();
const panel=$('observation-capacity-panel');assert.equal(panel.parentElement,$('compute'));assert.equal(panel.role,'region');assert.equal(panel['aria-labelledby'],'observation-capacity-title');assert.equal(calls.length,0);
for(let i=0;i<3;i++){dbRuntimeInstall();dbCapacityInstall();dbControls();}assert.equal($('compute').descendants().filter(n=>n.id===panel.id).length,1);
assert.equal($('observation-capacity-fetch').disabled,true);await $('observation-capacity-auth').onclick();assert.deepEqual(calls.map(c=>c.path),['/api/session']);
await capacityFetch();assert.ok(dbCapacity.record);assert.equal(dbUsage.record,null);assert.equal(dbResources.record,null);assert.equal(dbPlatform.record,null);assert.equal(worksheets.length,0);
const saved=dbCapacity.record;for(const locale of ['en','ja']){setLanguage(locale);dbControls();}dbRuntimeInstall();assert.equal(capacityCalls().length,1);assert.equal(dbCapacity.record,saved);
assert.match(panel.style,/min-width:0/);assert.match(panel.children[1].children[0].style,/flex-wrap:wrap/);
""")

    def test_exact_bytes_bigint_optional_null_and_bilingual_meanings(self):
        self.javascript(r"""
await capacityBoot();const r=capacityReply();await capacityFetch(r);assert.ok(dbCapacity.record);assert.ok(Object.isFrozen(dbCapacity.record.core));
assert.equal(dbCapacityBytes('18446744073709551615'),'17179869184.00 GiB (18446744073709551615 '+t('bytes')+')');assert.ok(dbCapacityBytes('9007199254740993').includes('9007199254740993'));assert.match(dbCapacityBytes('1073741824'),/^1.00 GiB/);assert.match(dbCapacityBytes('536870912'),/^0.50 GiB/);assert.match(dbCapacityBytes('0'),/^0.00 GiB/);
for(const locale of ['ja','en']){setLanguage(locale);dbControls();const txt=capacityText();for(const key of ['Node root filesystem','Image filesystem','Container filesystem','Shared Kubernetes VM node configured for LOCAL; not database dedicated or the Mac host.','Allocatable is the scheduler Pod budget, not free capacity or admission headroom. No CPU free measurement.','Memory available is the kubelet limit-minus-workingSet estimate, not OS free memory or a LOCAL reservation.','Filesystems may share an underlying disk. Never sum them. These are not data-volume, PVC, SeaweedFS or S3 quotas.'])assert.ok(txt.includes(t(key)),key);assert.ok(txt.includes(r.core.memory_sampled_at));assert.ok(txt.includes('1.5 cores'));}
r.image_fs=null;await capacityFetch(r);assert.ok(dbCapacity.record);assert.ok(capacityText().includes(t('Optional source unavailable')));assert.ok(dbCapacity.record.container_fs);
r.container_fs=null;r.core.root_fs.used_bytes=null;await capacityFetch(r);assert.ok(dbCapacity.record);assert.ok(capacityText().includes(t('Not specified')));
""")

    def test_strict_closed_dto_units_bounds_relationships_core_all_null(self):
        self.javascript(r"""
await capacityBoot();const changes=[r=>r.extra=true,r=>delete r.core,r=>r.scope='database',r=>r.warehouse='other',r=>r.selected_role='bi_viewer',r=>r.coverage.extra=true,r=>r.coverage.mac_host=true,r=>r.code='SECRET',r=>r.availability='partial',r=>r.core=null,r=>r.core.extra=true,r=>r.core.cpu_capacity_cores='0',r=>r.core.cpu_allocatable_cores='2',r=>r.core.memory_capacity_bytes='0',r=>r.core.memory_allocatable_bytes='18446744073709551616',r=>r.core.root_fs.available_bytes='18446744073709551616',r=>r.image_fs.extra=true,r=>r.container_fs.used_bytes=true];
for(const v of [0,true,null,'','01','+1','1.0','1e3','-1','18446744073709551616'])changes.push(r=>r.core.memory_available_bytes=v);
for(const v of [1,true,'1.0','01','+1','1e3','0.'+'0'.repeat(30)+'1','9'.repeat(97)])changes.push(r=>r.core.cpu_capacity_cores=v);
for(const change of changes){const r=capacityReply();change(r);await capacityFetch(r);assert.equal(dbCapacity.record,null);assert.doesNotMatch(capacityText(),/SECRET|INVALID_RESPONSE/);}
const u=capacityReply();u.availability='unavailable';u.code='CAPACITY_UNAVAILABLE';u.core=u.image_fs=u.container_fs=null;await capacityFetch(u);assert.ok(dbCapacity.record);assert.ok(capacityText().includes(t('Capacity source unavailable. No measured values.')));
u.image_fs=capacityReply().image_fs;await capacityFetch(u);assert.equal(dbCapacity.record,null);
const z=capacityReply();z.core.cpu_allocatable_cores='0';z.core.memory_allocatable_bytes='0';z.core.root_fs.available_bytes='0';await capacityFetch(z);assert.ok(dbCapacity.record);
""")

    def test_memory_available_exact_capacity_relationship(self):
        self.javascript(r"""
await capacityBoot();
for(const capacity of ['17179869184','9007199254740993','18446744073709551615']){
 const r=capacityReply();r.core.memory_capacity_bytes=capacity;r.core.memory_allocatable_bytes='0';
 for(const available of ['0',capacity]){r.core.memory_available_bytes=available;await capacityFetch(r);assert.ok(dbCapacity.record);assert.equal(dbCapacity.record.core.memory_available_bytes,available);assert.ok(capacityText().includes(dbCapacityBytes(available)));}
 if(BigInt(capacity)<18446744073709551615n){r.core.memory_available_bytes=(BigInt(capacity)+1n).toString();await capacityFetch(r);assert.equal(dbCapacity.record,null);assert.ok(!capacityText().includes(r.core.memory_available_bytes));}
}
""")

    def test_timestamps_exact_nanoseconds_stale_future_and_beforestart(self):
        self.javascript(r"""
await capacityBoot();const changes=[r=>r.observed_at='bad',r=>r.core.memory_sampled_at='2026-02-30T00:00:00Z',r=>r.core.node_started_at=new Date(Date.now()+10000).toISOString(),r=>r.core.root_fs.sampled_at=new Date(Date.now()-121000).toISOString(),r=>r.image_fs.sampled_at=new Date(Date.now()+6000).toISOString(),r=>r.container_fs.sampled_at=new Date(Date.now()-601000).toISOString()];
for(const change of changes){const r=capacityReply();change(r);await capacityFetch(r);assert.equal(dbCapacity.record,null);}
const r=capacityReply(),whole=new Date(Date.now()-10000).toISOString().split('.')[0];r.core.node_started_at=whole+'.123456789Z';r.core.memory_sampled_at=whole+'.123456788Z';await capacityFetch(r);assert.equal(dbCapacity.record,null);
r.core.memory_sampled_at=r.core.node_started_at;for(const fs of [r.core.root_fs,r.image_fs,r.container_fs])fs.sampled_at=r.core.node_started_at;await capacityFetch(r);assert.ok(dbCapacity.record);
""")

    def test_known_true_platform_authority_and_loss_purges(self):
        self.javascript(r"""
await boot();for(const p of [false,'true',1,null]){session.platform_admin=p;dbAdminSession(session);await dbCapacityFetch();assert.equal(capacityCalls().length,0);assert.equal(dbCapacity.record,null);}
session.platform_admin=true;dbAdminSession(session);await capacityFetch();assert.ok(dbCapacity.record);
currentRole='bi_viewer';dbControls();assert.equal(dbCapacity.record,null);assert.ok(!capacityText().includes('18446744073709551615'));await dbCapacityFetch();assert.equal(capacityCalls().length,1);
currentRole='admin';dbUpdateMaximumRole('bi_viewer');dbControls();await dbCapacityFetch();assert.equal(capacityCalls().length,1);
""")

    def test_pending_role_navigation_owner_aba_and_late_body(self):
        self.javascript(r"""
await capacityBoot();
for(const transition of [()=>{currentRole='bi_viewer';dbControls();currentRole='admin';dbControls();},()=>{activeTab='sql';dbControls();activeTab='compute';dbControls();},()=>{const old=dbPrincipal;dbPrincipal='other';dbControls();dbPrincipal=old;dbControls();},()=>{dbAdminClear();dbAdminSession(session);}]){
 let release;route=async(path,options)=>path==='/api/v1/bears/platform-capacity'?await new Promise(resolve=>release=resolve):runtimeFixtureRoute(path,options);
 const pending=dbCapacityFetch();await tick();transition();release(capacityReply());await pending;assert.equal(dbCapacity.record,null);assert.equal(dbCapacity.busy,null);
}
let release;const r=capacityReply();route=async(path,options)=>path==='/api/v1/bears/platform-capacity'?{rawBody:new Promise(resolve=>release=resolve)}:runtimeFixtureRoute(path,options);
const pending=dbCapacityFetch();await tick();currentRole='bi_viewer';dbControls();currentRole='admin';dbControls();release(JSON.stringify(r));await pending;assert.equal(dbCapacity.record,null);

""")

    def test_busy_token_same_authority_refresh_dbswitch_and_drafts(self):
        self.javascript(r"""
await capacityBoot();editSQL('SELECT kept SQL');editDoctor('SELECT kept Doctor');$('copilot-request').value='kept Copilot';$('p35-wap-sql').value='SELECT kept WAP';const drafts=()=>JSON.stringify([worksheets,worksheetId,$('sql').value,$('bear-query-sql').value,$('copilot-request').value,$('p35-wap-sql').value]);dbControls();const before=drafts();
await capacityFetch();const saved=dbCapacity.record;await dbCapacityRefreshAuth();assert.equal(dbCapacity.record,saved);assert.equal(capacityCalls().length,1);
let release;route=async(path,options)=>path==='/api/v1/bears/platform-capacity'?await new Promise(resolve=>release=resolve):runtimeFixtureRoute(path,options);
const pending=dbCapacityFetch();await tick();const token=dbCapacity.busy;await dbCapacityFetch();await dbCapacityRefreshAuth();assert.equal(dbCapacity.busy,token);assert.equal(capacityCalls().length,2);
assert.equal(drafts(),before);dbSelectionGeneration++;dbEpoch++;dbControls();const afterSwitch=drafts();release(capacityReply());await pending;assert.ok(dbCapacity.record);assert.equal(drafts(),afterSwitch);
activeTab='sql';dbControls();activeTab='compute';dbControls();assert.ok(dbCapacity.record);assert.equal(capacityCalls().length,2);
""")

    def test_raw_response_bounds_errors_and_unavailable_no_raw_fallback(self):
        self.javascript(r"""
await capacityBoot();for(const reply of [{http:500,body:{message:'SECRET'}},{http:200,body:capacityReply(),rawBody:'x'.repeat(32769)},{http:200,body:{},rawBody:'SECRET'}]){await capacityFetch(reply);assert.equal(dbCapacity.record,null);assert.doesNotMatch(capacityText(),/SECRET|INVALID_RESPONSE/);}
await capacityFetch({http:403,body:{message:'SECRET'}});assert.equal(dbCapacity.record,null);assert.equal($('observation-capacity-fetch').disabled,true);
""")

    def test_real_navigation_auth_role_owner_epoch_aba_and_late_body(self):
        self.navigation_javascript(r"""
await capacityBoot();$('tab-compute').click();
for(const status of [200,401,403]){
 const old=deferred();route=()=>old.promise;const pending=dbCapacityFetch();await tick();$('tab-warehouse').click();$('tab-compute').click();await capacityFetch();const fresh=dbCapacity.record;old.resolve(status===200?capacityReply():{http:status,body:{error:'SECRET'}});await pending;assert.equal(dbCapacity.record,fresh);assert.equal(dbAdmin.platform,true);
}
for(const loss of [()=>{currentRole='bi_viewer';dbControls();},()=>{dbPrincipal=JSON.stringify(['other',true]);dbControls();},()=>dbUpdateMaximumRole('bi_viewer'),()=>{session.platform_admin=false;dbAdminSession(session);},()=>dbAdminClear()]){
 session.platform_admin=true;session.maximum_role='admin';dbPrincipal=JSON.stringify(['owner',true]);currentRole='admin';dbUpdateMaximumRole('admin');dbAdminSession(session);await capacityFetch();const old=deferred();route=()=>({rawBody:old.promise});const pending=dbCapacityFetch();await tick();loss();assert.equal(dbCapacity.record,null);assert.doesNotMatch(capacityText(),/1.5 cores/);
 session.platform_admin=true;session.maximum_role='admin';dbPrincipal=JSON.stringify(['owner',true]);currentRole='admin';dbUpdateMaximumRole('admin');dbAdminSession(session);await capacityFetch();const fresh=dbCapacity.record;old.resolve(JSON.stringify(capacityReply()));await pending;assert.equal(dbCapacity.record,fresh);
}
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
await $('observation-capacity-auth').onclick();await capacityFetch();const missing=capacityReply();Object.assign(missing,{availability:'unavailable',code:'CAPACITY_UNAVAILABLE',core:null,image_fs:null,container_fs:null});await capacityFetch(missing);await capacityFetch({http:500,body:{error:'SECRET'}});
assert.equal(JSON.stringify(dbAudit),auditBefore);assert.equal(otherState(),before);assert.equal(runtimeState(),runtimeBefore);assert.equal(document.activeElement,input);assert.equal(input.value,'47');assert.equal(dbRuntime.intent,intent);assert.equal(dbRuntime.intent.key,key);assert.equal(wap.op,op);assert.equal(wap.op.ambiguous,true);assert.equal(dbRuns.get(worksheetId).state,'UNKNOWN');assert.equal(dbProposalHeld().length,1);assert.equal(dbCuratorHeld().length,1);assert.equal(dbObservations.sre.record,sre);assert.equal(dbObservations.policy.record,policy);assert.ok(calls.slice(observerStart).every(c=>['/api/session','/api/v1/bears/platform-capacity'].includes(c.path)&&!c.options?.method));
""")

if __name__=='__main__':unittest.main()
