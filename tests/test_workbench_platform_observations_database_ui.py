"""C2B production UI handlers with the existing Node DOM/fetch fixtures only."""
import unittest

import test_workbench_sre_policy_database_ui as observations
from test_workbench_ai_curator_database_ui import CURATOR
from test_workbench_ai_sql_proposals_database_ui import PROPOSAL


PLATFORM = r"""
const platformCalls=()=>calls.filter(c=>c.path.startsWith('/api/v1/bears/platform-observations'));
const scope=(kind,value='platform')=>{const n=$('observation-'+kind+'-scope');n.value=value;n.onchange();};
const platformReply=()=>({scope:'platform',warehouse:'LOCAL',selected_role:'admin',observed_at:'2026-11-01T03:00:00.123456Z',coverage:{kind:'fixed_platform_observations',live_engine_probe:false,policy_audit:false,complete_blocker_set:false},
 physical:{availability:'available',code:null,observed_at:'2026-11-01T02:59:57Z',state:'chilled',replicas:0,ready_replicas:0,deployments:[{role:'coordinator',replicas:0,ready_replicas:0},{role:'worker',replicas:0,ready_replicas:0}]},
 engine_evidence:{availability:'available',code:null,observed_at:'2026-11-01T02:59:58Z',verified_at:'2026-02-28T01:00:00.123456+09:00',live_probe:false},
 auto_chill:{availability:'available',code:null,observed_at:'2026-11-01T02:59:59Z',minutes:30,revision:7},
 saved_admissions:{availability:'available',code:null,observed_at:'2026-11-01T03:00:00Z',active:4,unknown:2}});
const unavailable=(r,key)=>{const s=r[key];s.availability='unavailable';s.code={physical:'PHYSICAL_UNAVAILABLE',engine_evidence:'ENGINE_EVIDENCE_UNAVAILABLE',auto_chill:'POLICY_UNAVAILABLE',saved_admissions:'ADMISSIONS_UNAVAILABLE'}[key];for(const k of Object.keys(s))if(!['availability','code','observed_at','live_probe'].includes(k))s[k]=null;return r;};
async function getPlatform(reply=platformReply(),kind='sre'){
 route=async(path,options)=>{assert.equal(path,'/api/v1/bears/platform-observations');assert.deepEqual(options,{headers:{'X-Shirokuma-Role':'admin'}});return reply;};
 await $('observation-'+kind+'-platform-fetch').onclick();
}
async function platformBoot(){session.platform_admin=true;await boot();scope('sre');}
const platformText=()=>observationText('sre');
"""


class PlatformObservationsDatabaseUI(unittest.TestCase):
    def javascript(self, body):
        observations.SREPolicyDatabaseUI.javascript(self, PLATFORM + body)

    def navigation_javascript(self, body):
        observations.SREPolicyDatabaseUI.navigation_javascript(self, body)

    def test_fresh_empty_database_and_worksheet_explicit_auth_only_entry(self):
        self.javascript(r"""
session.platform_admin=true;inventory=[];schemas=[];worksheets=[];worksheetId='';
dbPrincipal=JSON.stringify([session.username,session.authenticated]);dbMaximumRole='admin';
assert.equal(dbAdmin.platform,false);dbObservationsInstall();scope('sre');
assert.equal(calls.length,0);assert.equal($('observation-sre-platform-fetch').disabled,true);
assert.equal($('observation-sre-auth').disabled,false);await $('observation-sre-auth').onclick();
assert.deepEqual(calls.map(c=>c.path),['/api/session']);assert.equal(calls[0].options,undefined);
assert.equal(dbObservationPlatform('admin'),true);assert.equal(worksheets.length,0);assert.equal(worksheetId,'');
await getPlatform();assert.equal(platformCalls().length,1);assert.ok(dbPlatform.record);
assert.deepEqual(calls.map(c=>c.path),['/api/session','/api/v1/bears/platform-observations']);
assert.equal(dbObservations.sre.context,null);assert.equal(dbObservations.policy.context,null);assert.deepEqual(dbObservations.sre.databases,[]);
""")

    def test_default_database_manual_shared_cache_and_reference_viewer_independence(self):
        self.javascript(r"""
session.platform_admin=true;await boot();for(const o of Object.values(dbObservations))assert.equal(o.scope,'database');
await readyObservation('sre','bi_viewer');await readyObservation('policy','bi_viewer');await fetchObservation('sre');await fetchObservation('policy');
assert.equal(dbObservations.sre.ceiling.platform,false);const sre=dbObservations.sre.record,policy=dbObservations.policy.record,context=dbObservations.sre.context;
const start=calls.length;scope('sre');assert.equal(calls.length,start);await getPlatform();const shared=dbPlatform.record;
assert.equal(platformCalls().length,1);assert.equal(dbObservations.sre.record,sre);assert.equal(dbObservations.policy.record,policy);
scope('policy');assert.equal(dbPlatform.record,shared);assert.ok(observationText('policy').includes(shared.observed_at));
for(const kind of ['sre','policy']){scope(kind,'database');assert.equal(dbObservations[kind].record,kind==='sre'?sre:policy);scope(kind);}
assert.equal(dbObservations.sre.context,context);assert.equal(calls.length,start+1);
assert.equal($('observation-sre-database-controls').hidden,true);assert.equal($('observation-sre-platform').hidden,false);
""")

    def test_non_p_lowered_role_and_false_like_p_have_zero_platform_get(self):
        self.javascript(r"""
await boot();scope('sre');
for(const value of [false,'true',1,null,undefined]){session.platform_admin=value;dbAdminSession(session);await dbPlatformRefreshAuth();await dbPlatformFetch();assert.equal(dbPlatform.record,null);assert.equal($('observation-sre-platform-fetch').disabled,true);}
session.platform_admin=true;dbAdminSession(session);
for(const role of ['bi_viewer','data_engineer']){currentRole=role;dbControls();await dbPlatformRefreshAuth();await dbPlatformFetch();assert.equal($('observation-sre-auth').disabled,true);assert.equal($('observation-sre-platform-fetch').disabled,true);}
currentRole='admin';session.maximum_role='data_engineer';dbAdminSession(session);dbUpdateMaximumRole('data_engineer');await dbPlatformFetch();
assert.equal(platformCalls().length,0);assert.ok(platformText().includes('Global role: Admin'));currentRole='bi_viewer';dbControls();assert.ok(platformText().includes('Global role: BI Viewer'));for(const locale of ['ja','en']){setLanguage(locale);dbControls();assert.ok(platformText().includes(t('Platform requires a true platform administrator with current and maximum global Admin role. Refresh platform authority explicitly.')));}
""")

    def test_real_navigation_language_and_database_only_changes_retain_all_snapshots(self):
        self.navigation_javascript(r"""
session.platform_admin=true;await dualBoot();await readyObservation('sre','bi_viewer','finance','finance-marts');await readyObservation('policy','bi_viewer');
await fetchObservation('sre');await fetchObservation('policy');const sre=dbObservations.sre.record,policy=dbObservations.policy.record;
$('tab-bears').click();$('bear-tab-sre').click();scope('sre');await getPlatform();const saved=dbPlatform.record,count=calls.length;
for(const locale of ['ja','en','ja','en']){
 setLanguage(locale);$('bear-tab-policy').click();scope('policy');assert.ok(observationText('policy').includes(saved.observed_at));
 $('tab-warehouse').click();$('tab-bears').click();$('bear-tab-sre').click();scope('sre','database');assert.ok(platformText().includes(sre.observed_at));scope('sre');
 assert.equal(dbPlatform.record,saved);assert.equal(dbObservations.sre.record,sre);assert.equal(dbObservations.policy.record,policy);assert.equal(calls.length,count);
}
// DB-only context/grant/inventory changes must not invalidate the fixed LOCAL cache.
changeObservation('sre','database','db');dbInvalidateReceipt('bi_viewer');dbInventoryVersion++;dbEpoch++;dbAuthorizationVersion++;dbControls();
assert.equal(dbPlatform.record,saved);assert.equal(calls.length,count);
""")

    def test_inflight_platform_capture_survives_database_only_authorization_changes(self):
        self.javascript(r"""
await platformBoot();await readyObservation('sre','bi_viewer');
const old=deferred();route=()=>old.promise;const pending=dbPlatformFetch();await tick();const token=dbPlatform.busy;
changeObservation('sre','schema','raw');dbInventoryVersion++;dbEpoch++;dbAuthorizationVersion++;dbControls();
assert.equal(dbPlatform.busy,token);old.resolve(platformReply());await pending;assert.ok(dbPlatform.record);assert.equal(platformCalls().length,1);
""")

    def test_every_navigation_leave_fences_stale_success_and_auth_error_before_new_capture(self):
        self.navigation_javascript(r"""
await platformBoot();$('tab-bears').click();$('bear-tab-sre').click();
const leaves=[()=>{scope('sre','database');scope('sre');},()=>{$('bear-tab-policy').click();$('bear-tab-sre').click();},()=>{$('tab-warehouse').click();$('tab-bears').click();}];
for(const leave of leaves)for(const status of [200,401,403]){
 const old=deferred();route=()=>old.promise;const pending=dbPlatformFetch();await tick();assert.ok(dbPlatform.busy);leave();assert.equal(dbPlatform.busy,null);
 const newer=platformReply();newer.saved_admissions.active=17;await getPlatform(newer);const snapshot=dbPlatform.record;
 old.resolve(status===200?platformReply():{http:status,body:{error:'SECRET stale auth'}});await pending;
 assert.equal(dbPlatform.record,snapshot);assert.equal(dbAdmin.platform,true);assert.equal(dbPlatform.record.saved_admissions.active,17);assert.doesNotMatch(platformText(),/SECRET/);
}
""")

    def test_hidden_privilege_loss_purges_and_never_restores_late_capture(self):
        self.navigation_javascript(r"""
await platformBoot();$('tab-bears').click();$('bear-tab-sre').click();
const losses=[()=>{session.platform_admin=false;dbAdminSession(session);},()=>{currentRole='bi_viewer';dbControls();},()=>{dbUpdateMaximumRole('bi_viewer');},()=>{dbPrincipal=JSON.stringify(['other',true]);dbControls();},()=>{dbAdmin.platform=false;dbAdminClear();}];
for(const lose of losses){
 currentRole='admin';dbPrincipal=JSON.stringify(['owner',true]);session.maximum_role='admin';session.platform_admin=true;dbUpdateMaximumRole('admin');dbAdminSession(session);scope('sre');await getPlatform();
 const old=deferred();route=()=>old.promise;const pending=dbPlatformFetch();await tick();$('tab-warehouse').click();lose();assert.equal(dbPlatform.record,null);
 old.resolve(platformReply());await pending;assert.equal(dbPlatform.record,null);
}
""")

    def test_stale_auth_refresh_cannot_change_newer_authority_or_cache(self):
        self.javascript(r"""
await platformBoot();const old=deferred();sessionRoute=()=>old.promise;const pending=dbPlatformRefreshAuth();await tick();scope('sre','database');scope('sre');
sessionRoute=null;await dbPlatformRefreshAuth();await getPlatform();const saved=dbPlatform.record;
old.resolve({...session,authenticated:false,platform_admin:false,maximum_role:'bi_viewer'});await pending;
assert.equal(dbPlatform.record,saved);assert.equal(dbAdmin.platform,true);assert.equal(dbMaximumRole,'admin');
""")

    def test_current_session_denial_purges_visible_and_hidden_cache_without_parsing_body(self):
        self.navigation_javascript(r"""
await platformBoot();
for(const hidden of [false,true])for(const status of [401,403])for(const message of ['Unexpected end of JSON input','Malformed denied JSON']){
 sessionRoute=null;dbAdminSession(session);$('tab-bears').click();$('bear-tab-sre').click();scope('sre');await getPlatform();assert.ok(dbPlatform.record);assert.equal(dbAdmin.platform,true);
 if(hidden){scope('sre','database');$('tab-warehouse').click();assert.ok(dbPlatform.record);assert.equal($('bears').hidden,true);}
 let parsed=0;const denied={http:status};Object.defineProperty(denied,'body',{get(){parsed++;throw SyntaxError(message);}});sessionRoute=async()=>denied;
 await dbPlatformRefreshAuth();assert.equal(parsed,0);assert.equal(dbPlatform.record,null);assert.equal(dbAdmin.platform,false);assert.equal(dbPlatform.busy,null);
 assert.equal(dbPlatform.notice,'Platform requires a true platform administrator with current and maximum global Admin role. Refresh platform authority explicitly.');
 const count=platformCalls().length;await dbPlatformFetch();assert.equal(platformCalls().length,count);
}
""")

    def test_stale_malformed_session_denial_cannot_purge_newer_authorized_capture(self):
        self.navigation_javascript(r"""
await platformBoot();$('tab-bears').click();$('bear-tab-sre').click();
for(const status of [401,403]){
 await getPlatform();const old=deferred();sessionRoute=()=>old.promise;const pending=dbPlatformRefreshAuth();await tick();
 $('tab-warehouse').click();$('tab-bears').click();sessionRoute=null;await dbPlatformRefreshAuth();await getPlatform();const saved=dbPlatform.record;
 let parsed=0;const denied={http:status};Object.defineProperty(denied,'body',{get(){parsed++;throw SyntaxError('Unexpected end of JSON input');}});old.resolve(denied);await pending;
 assert.equal(parsed,0);assert.equal(dbPlatform.record,saved);assert.equal(dbAdmin.platform,true);assert.equal(dbMaximumRole,'admin');assert.equal(dbPlatform.notice,'');
}
""")

    def test_success_session_body_await_is_fenced_before_applying_late_authority_loss(self):
        self.javascript(r"""
await platformBoot();await getPlatform();const body=deferred();let parsed=0;
sessionRoute=async()=>({http:200,get body(){parsed++;return body.promise;}});
const pending=dbPlatformRefreshAuth();await tick();assert.equal(parsed,1);assert.ok(dbPlatform.busy);
scope('sre','database');scope('sre');sessionRoute=null;await dbPlatformRefreshAuth();await getPlatform();const saved=dbPlatform.record;
body.resolve({...session,authenticated:false,platform_admin:false,maximum_role:'bi_viewer'});await pending;
assert.equal(dbPlatform.record,saved);assert.equal(dbAdmin.platform,true);assert.equal(dbMaximumRole,'admin');assert.equal(dbPlatform.notice,'');
""")

    def test_current_denial_purges_only_platform_and_source_or_shape_error_is_local(self):
        self.javascript(r"""
await platformBoot();await readyObservation('sre');await readyObservation('policy');await fetchObservation('sre');await fetchObservation('policy');
const before=otherState(),sre=dbObservations.sre.record,policy=dbObservations.policy.record,version=dbAuthorizationVersion;
for(const reply of [{http:500,body:{error:'SECRET /host/path'}},{...platformReply(),debug:'SECRET'}, {malformed:true}]){
 await getPlatform(reply);assert.equal(dbPlatform.record,null);assert.equal(dbObservations.sre.record,sre);assert.equal(dbObservations.policy.record,policy);assert.equal(dbAuthorizationVersion,version);assert.equal(otherState(),before);assert.doesNotMatch(platformText(),/SECRET|\/host\/path/);
}
await getPlatform();await getPlatform({http:403,body:{error:'SECRET'}});assert.equal(dbPlatform.record,null);assert.equal(dbAdmin.platform,false);const count=platformCalls().length;await dbPlatformFetch();assert.equal(platformCalls().length,count);
""")

    def test_closed_dto_every_nested_extra_missing_nullness_calendar_and_count_invariants(self):
        self.javascript(r"""
const base=platformReply(),paths=[[],['coverage'],['physical'],['physical','deployments',0],['physical','deployments',1],['engine_evidence'],['auto_chill'],['saved_admissions']];
for(const path of paths){for(const mode of ['extra','missing']){const bad=structuredClone(base);let n=bad;for(const k of path)n=n[k];if(mode==='extra')n.secret='/host/path';else delete n[Object.keys(n)[0]];assert.throws(()=>dbPlatformRecord(bad));}}
const mutations=[r=>r.scope='database',r=>r.warehouse='OTHER',r=>r.selected_role='bi_viewer',r=>r.coverage.live_engine_probe=true,r=>r.coverage.policy_audit=true,r=>r.coverage.complete_blocker_set=true,r=>r.coverage.kind='other',r=>r.engine_evidence.live_probe=true,r=>r.physical.replicas=true,r=>r.physical.replicas=-1,r=>r.physical.replicas=NaN,r=>r.physical.replicas=Infinity,r=>r.physical.replicas=Number.MAX_SAFE_INTEGER+1,r=>r.physical.deployments[0].replicas=1,r=>r.physical.state='active',r=>r.physical.state='stopped',r=>r.physical.deployments.reverse(),r=>r.physical.deployments.pop(),r=>r.auto_chill.minutes=1441,r=>r.auto_chill.minutes=true,r=>r.auto_chill.revision=-1,r=>r.saved_admissions.unknown=0.5,r=>r.saved_admissions.active=null];
for(const mutate of mutations){const bad=structuredClone(base);mutate(bad);assert.throws(()=>dbPlatformRecord(bad),String(mutate));}
for(const key of ['physical','engine_evidence','auto_chill','saved_admissions']){
 const bad=structuredClone(base);bad[key].code='SECRET';assert.throws(()=>dbPlatformRecord(bad));
 const unavailableReply=unavailable(structuredClone(base),key);assert.ok(dbPlatformRecord(unavailableReply));
 for(const field of Object.keys(unavailableReply[key]).filter(k=>!['availability','code','observed_at','live_probe'].includes(k))){const invalid=structuredClone(unavailableReply);invalid[key][field]=base[key][field];assert.throws(()=>dbPlatformRecord(invalid),key+'.'+field);}
 for(const invalidTime of ['2026-02-29T00:00:00Z','2026-04-31T00:00:00Z','2026-01-01T24:00:00Z','2026-01-01T00:00:00','2026-01-01T00:00:00+09:00','2026-01-01T00:00:00.1234567890123456789012345678901234567890123456789Z']){const invalid=structuredClone(base);invalid[key].observed_at=invalidTime;assert.throws(()=>dbPlatformRecord(invalid));}
}
for(const value of ['2026-02-29T00:00:00Z','2026-02-28T00:00:60Z','0000-01-01T00:00:00Z','2026-01-01T00:00:00+24:00',null]){const invalid=structuredClone(base);invalid.engine_evidence.verified_at=value;assert.throws(()=>dbPlatformRecord(invalid));}
for(const value of ['2024-02-29T00:00:00.1Z','2026-01-01T00:00:00.123456789+00:00']){const valid=structuredClone(base);valid.observed_at=value;assert.ok(dbPlatformRecord(valid));}
const active=structuredClone(base);for(const r of active.physical.deployments){r.replicas=2;r.ready_replicas=2;}Object.assign(active.physical,{replicas:4,ready_replicas:4,state:'active'});assert.ok(dbPlatformRecord(active));
active.physical.state='transitioning';assert.ok(dbPlatformRecord(active));active.physical.deployments[1].ready_replicas=1;active.physical.ready_replicas=3;assert.ok(dbPlatformRecord(active));active.physical.state='active';assert.throws(()=>dbPlatformRecord(active));
const frozen=dbPlatformRecord(base);base.saved_admissions.active=99;assert.equal(frozen.saved_admissions.active,4);assert.ok(Object.isFrozen(frozen.physical.deployments));
""")

    def test_partial_unavailability_human_provenance_and_historical_caveats_bilingual(self):
        self.javascript(r"""
await platformBoot();const r=unavailable(unavailable(platformReply(),'physical'),'auto_chill');await getPlatform(r);
for(const locale of ['en','ja']){setLanguage(locale);dbControls();const text=platformText();for(const key of ['Platform scope','Global role','Deployment source unavailable.','Auto-chill policy source unavailable.','Historical engine proof only; no live engine probe or current SQL health guarantee.','Saved admissions are incomplete blockers; this is not a security audit or permission to chill.','Deployment desired/ready counts only; pod stoppage, released resources and current SQL health are not established.']){assert.ok(text.includes(t(key)),key);if(locale==='ja')assert.notEqual(t(key),key);}for(const time of [r.observed_at,...['physical','engine_evidence','auto_chill','saved_admissions'].map(k=>r[k].observed_at),r.engine_evidence.verified_at])assert.ok(text.includes(time));assert.doesNotMatch(text,/PHYSICAL_UNAVAILABLE|POLICY_UNAVAILABLE|generation_id|verified_at|live_probe|null|healthy/);assert.equal($('observation-sre-output').descendants().some(n=>['PRE','A','INPUT','IMG'].includes(n.tagName)),false);}
await getPlatform();for(const locale of ['en','ja']){setLanguage(locale);dbControls();const text=platformText(),lines=$('observation-sre-output').children.map(n=>n.textContent);assert.equal(lines[0],t('Platform scope')+' · LOCAL · '+t('Global role')+': '+dbObservationLabel('role',currentRole));assert.equal(lines[1],t('Observed at')+': '+dbPlatform.record.observed_at);assert.ok(lines.indexOf(t('Deployment desired/ready counts only; pod stoppage, released resources and current SQL health are not established.'))>1);assert.ok(lines.indexOf(t('Deployment summary'))>1);assert.ok(text.includes(locale==='ja'?'希望/Ready=0':'Desired/ready both 0'));assert.ok(text.includes(t('Active admissions')+': 4'));assert.ok(text.includes(t('Unknown admissions')+': 2'));assert.doesNotMatch(text,/Policy revision|ポリシー改訂|revision/);assert.equal(dbPlatform.record.auto_chill.revision,7);const qualification=t('Counts exclude query admissions with verified execution closure; saved UNKNOWN history is unchanged.');assert.equal(lines.filter(v=>v===qualification).length,1);assert.ok(lines.indexOf(qualification)>lines.indexOf(t('Saved admissions')));assert.ok(lines.indexOf(qualification)<lines.indexOf(t('Active admissions')+': 4'));if(locale==='ja'){for(const label of ['保存済み受付','処理中の受付','状態不明の受付','保存済み自動休止設定','アイドル時間（分）','休止可否の判定'])assert.ok(text.includes(label));assert.doesNotMatch(text,/admissions|chill/);}}
const disabled=platformReply();disabled.auto_chill.minutes=0;disabled.auto_chill.revision=912345;await getPlatform(disabled);for(const locale of ['en','ja']){setLanguage(locale);dbControls();const text=platformText();assert.ok(text.includes(locale==='ja'?'無効（0分）':'Disabled (0 min)'));assert.ok(!text.includes(t('Idle minutes')+': 0'));assert.doesNotMatch(text,/912345|Policy revision|ポリシー改訂|revision/);assert.equal(dbPlatform.record.auto_chill.revision,912345);}
const positive=platformReply();positive.auto_chill.minutes=45;await getPlatform(positive);for(const locale of ['en','ja']){setLanguage(locale);dbControls();assert.ok(platformText().includes(t('Idle minutes')+': 45'));assert.ok(!platformText().includes(t('Disabled (0 min)')));}

for(const key of ['physical','engine_evidence','auto_chill','saved_admissions'])unavailable(r,key);await getPlatform(r);assert.ok(dbPlatform.record);assert.doesNotMatch(platformText(),/Desired replicas: 0|Active admissions: 0|Idle minutes: 0/);
""")

    def test_existing_drafts_and_unknown_receipts_exactly_preserved(self):
        self.javascript(CURATOR + PROPOSAL + r"""
const nativeAppend=Node.prototype.append;Node.prototype.append=function(...items){return nativeAppend.apply(this,items.map(n=>typeof n==='string'?Object.assign(new Node('text'),{textContent:n}):n));};
session.platform_admin=true;await boot();request('proposal');await choose();await submit(pendingProposal({state:'UNKNOWN',inference_state:'UNKNOWN'}));assert.equal(dbProposalHeld().length,1);
await curatorContext();objects=[curatorObject()];await dbCuratorLoad();change('object','0');await curatorSubmit(curatorPending({state:'UNKNOWN',inference_state:'UNKNOWN'}));
route=async()=>wrap(record({state:'UNKNOWN',text:null,inference_state:'UNKNOWN'}));await $('copilot-explain').onclick();route=async()=>envelope(dbRuns.get(worksheetId),{state:'UNKNOWN'});await dbRunQuery();
const wap=dataopsPanels.get('p35-wap');schemas[0].capabilities.wap_models=true;route=async path=>({context:JSON.parse(new URL(path,'http://fixture').searchParams.get('context')),items:[]});await dataopsRefresh(wap);route=async()=>{throw Error('lost ACK');};await dataopsSubmit(wap,'/api/v1/wap/build',{model_id:'existing-model',version:1},{});
editSQL('SELECT kept SQL');editDoctor('SELECT kept Doctor');request('kept Copilot');$('p35-wap-sql').value='SELECT kept WAP';wap.sources[0].alias.value='kept';const before=otherState(),op=wap.op;
scope('sre');await getPlatform();scope('policy');await getPlatform(unavailable(platformReply(),'saved_admissions'),'policy');scope('sre','database');scope('sre');await getPlatform({http:500,body:{error:'secret'}});
assert.equal(otherState(),before);assert.equal(wap.op,op);assert.equal(wap.op.ambiguous,true);assert.equal(dbRuns.get(worksheetId).state,'UNKNOWN');assert.equal(dbProposalHeld().length,1);assert.equal(dbCuratorHeld().length,1);
""")

    def test_native_controls_existing_390_wrapping_and_copy_fences(self):
        self.javascript(r"""
await platformBoot();narrow=true;for(const kind of ['sre','policy']){scope(kind);const panel=$('observation-'+kind+'-panel');assert.equal(panel.className,'panel-body');assert.equal($('observation-'+kind+'-scope').tagName,'SELECT');assert.equal($('observation-'+kind+'-platform').className,'toolbar');assert.equal($('observation-'+kind+'-output').className,'table-wrap');assert.equal(panel.descendants().filter(n=>['INPUT','TEXTAREA'].includes(n.tagName)).length,0);assert.equal($('observation-'+kind+'-scope').options.length,2);}
assert.ok(dbExplanationLead.includes('SRE and Policy show read-only Database observations with separate contexts. A separate manual Platform (P-only) scope'));assert.ok(dbExplanationLead.endsWith('Other Bear actions remain unavailable.'));
for(const locale of ['en','ja']){setLanguage(locale);for(const key of [dbExplanationLead,dbExplanationFence]){assert.ok(t(key).includes(locale==='ja'?'Platform（P のみ）':'Platform (P-only)'));assert.doesNotMatch(t(key),/observations remain deferred|観測は未対応/);}assert.ok(t(dbExplanationFence).endsWith(locale==='ja'?'SQL 案はワークシートの Copilot で利用できます。他の Bear 操作・ステージング・公開・AI 設定は未対応です。':'SQL proposals are available in worksheet Copilot. Other Bear actions, staging, publishing and AI settings remain unavailable.'));}
""")


if __name__ == '__main__':
    unittest.main()
