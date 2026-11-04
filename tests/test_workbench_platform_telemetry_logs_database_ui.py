"""Drive the manual telemetry log panels through the shipped Workbench script."""
import unittest

import test_workbench_platform_telemetry_readiness_database_ui as readiness


LOGS = r"""
const logPath='/api/v1/bears/platform-telemetry-logs';
const logID=kind=>'observation-'+kind+'-telemetry-logs-';
const logRoot=kind=>kind==='sre'?'observation-sre-telemetry-logs':'observation-telemetry-logs-panel';
const logButton=(kind,action)=>$(logID(kind)+action);
const logOutput=kind=>visibleText($(logID(kind)+'output'));
const logCalls=()=>calls.filter(call=>call.path===logPath);
const sampled='2026-11-04T01:02:03.123456Z';
const newest='2026-11-04T01:02:03.123456789Z';
const earlier='2026-11-04T01:02:03Z';
const empty=()=>({observed_at:sampled,status:'available',line_count:0,other_count:0,level_counts:{warning:0,error:0,fatal:0},events:[]});
const logReply=()=>({scope:{since_minutes:10,tail_lines:100,event_limit:20,streams:['stdout','stderr']},
 agenthouse:{...empty(),line_count:3,other_count:1,level_counts:{warning:1,error:1,fatal:0},
 events:[{timestamp:newest,severity:'error'},{timestamp:earlier,severity:'warning'}]},
 loki:{...empty(),status:'unavailable'}});
async function logsBoot(){
 await telemetryBoot();
 dbObservations.sre.scope='platform';dbObservationsSync();
}
async function logFetch(kind,reply=logReply()){
 route=(path,options)=>{
  if(path!==logPath)return runtimeFixtureRoute(path,options);
  assert.deepEqual(options,{headers:{'X-Shirokuma-Role':'admin'}});
  return reply;
 };
 await logButton(kind,'fetch').onclick();
}
"""


RAW_LOGS = r"""
const rawPath='/api/v1/bears/platform-telemetry-raw-logs';
const rawNode=key=>$('observation-sre-raw-logs-'+key);
const rawCalls=()=>calls.filter(call=>call.path===rawPath);
const rawText=newest+' <img src=x onerror=alert(1)> 雪🐻  \r\n';
const rawErr=earlier+' standard error\n';
const rawReply=()=>({scope:{since_minutes:10,tail_lines:100,max_bytes:65536},
 agenthouse:{observed_at:sampled,status:'available',line_count:2,stdout:rawText,stderr:rawErr},
 loki:{observed_at:sampled,status:'available',line_count:0,stdout:'',stderr:''}});
const rawBytes=value=>new TextEncoder().encode(JSON.stringify(value)).buffer;
const rawOutput=()=>rawNode('output').descendants();
const rawPre=()=>rawOutput().filter(node=>node.tagName==='PRE');
const rawRoute=read=>route=(path,options)=>{
 if(path!==rawPath)return runtimeFixtureRoute(path,options);
 assert.deepEqual(options,{cache:'no-store',headers:{'X-Shirokuma-Role':'admin'}});
 return {telemetryResponse:read};
};
async function rawBoot(){
 await logsBoot();
 activeTab='bears';dbObservationNavigate('sre');
 sessionRoute=(path,options)=>{
  assert.deepEqual(options,{cache:'no-store',headers:{'X-Shirokuma-Role':'admin'}});
  return {...session,role:'admin'};
 };
}
async function rawFetch(value=rawReply()){
 rawRoute({ok:true,status:200,arrayBuffer:async()=>rawBytes(value)});
 await rawNode('fetch').onclick();
}
"""


class PlatformTelemetryLogsDatabaseUI(unittest.TestCase):
    def javascript(self, body):
        readiness.PlatformTelemetryReadinessDatabaseUI.javascript(self, LOGS + RAW_LOGS + body)

    def test_manual_authority_scope_and_independent_semantic_outputs(self):
        self.javascript(r"""
await logsBoot();
assert.equal(logCalls().length,0);
for(const kind of ['sre','compute']){
 const root=$(logRoot(kind));
 assert.equal(root.parentElement,kind==='sre'?$('bear-panel-sre'):$('compute'));
 assert.equal(root.role,'region');assert.equal(root['aria-labelledby'],logID(kind)+'title');
 assert.equal(logButton(kind,'fetch').disabled,true);
 assert.equal(logButton(kind,'auth').disabled,false);
 assert.match(visibleText(root),/stdout and stderr.*10 minutes.*100 lines.*20 warning/);
}
await logFetch('compute');assert.equal(logCalls().length,0);
await logButton('compute','auth').onclick();
assert.equal(calls.at(-1).path,'/api/session');assert.equal(logButton('sre','fetch').disabled,true);
await logFetch('compute');assert.equal(logCalls().length,1);
assert.match(logOutput('compute'),/Other lines 1/);
assert.match(logOutput('compute'),/Sample unavailable/);
assert.ok(logOutput('compute').includes(sampled));
assert.ok(logOutput('compute').includes(newest));
assert.ok(logOutput('compute').indexOf(newest)<logOutput('compute').indexOf(earlier));
const held=dbTelemetryLogs.compute.record;
await logButton('sre','auth').onclick();await logFetch('sre',{...logReply(),loki:empty()});
assert.match(logOutput('sre'),/Sample contains zero lines/);
assert.equal(dbTelemetryLogs.compute.record,held);
assert.equal(dbTelemetryReadiness.compute.record,null);
assert.equal(dbObservations.sre.context,null);
language='ja';dbObservationsSync();assert.match(logOutput('sre'),/その他の行 1/);
assert.ok(logOutput('sre').includes(newest));
dbObservations.sre.scope='database';dbObservationsSync();
assert.equal($(logRoot('sre')).hidden,true);
assert.equal($(logRoot('compute')).hidden,false);
assert.equal(logCalls().length,2);
""")

    def test_closed_response_rejects_malformed_scope_counts_timestamps_order_and_payloads(self):
        self.javascript(r"""
await logsBoot();await logButton('compute','auth').onclick();
const mutations=[
 d=>d.scope.tail_lines=101,
 d=>d.scope.streams=['stdout','file'],
 d=>d.loki.line_count=1,
 d=>d.agenthouse.other_count=0,
 d=>d.agenthouse.level_counts.error=1.5,
 d=>d.agenthouse.events.reverse(),
 d=>d.agenthouse.events[0].timestamp='2026-02-29T01:02:03Z',
 d=>d.agenthouse.events[0].timestamp='2026-11-04T01:02:03.1234567890Z',
 d=>d.agenthouse.events[0].timestamp='2026-11-04T01:02:03+00:00',
 d=>d.agenthouse.events[0].timestamp=newest+'\n',
 d=>d.agenthouse.events[0].severity='fatal',
 d=>d.agenthouse.events[0].message='private text',
 d=>d.agenthouse.events.push(...Array(20).fill(d.agenthouse.events[1])),
 d=>d.health=true,
];
for(const change of mutations){
 const reply=logReply();change(reply);await logFetch('compute',reply);
 assert.equal(dbTelemetryLogs.compute.record,null);
 assert.match(logOutput('compute'),/could not be loaded/);
 assert.ok(!logOutput('compute').includes('private text'));
}
""")

    def test_authority_loss_purges_both_and_navigation_aba_discards_late_response(self):
        self.javascript(r"""
await logsBoot();
for(const kind of ['sre','compute']){await logButton(kind,'auth').onclick();await logFetch(kind);}
const held=dbTelemetryLogs.compute.record;
const wait=deferred();route=()=>wait.promise;
const pending=dbTelemetryLogsFetch('sre');await tick();
dbObservations.sre.scope='database';dbObservationsSync();
dbObservations.sre.scope='platform';dbObservationsSync();
const original=dbTelemetryLogs.sre.record;
wait.resolve({...logReply(),agenthouse:empty()});await pending;
assert.equal(dbTelemetryLogs.sre.record,original);
assert.equal(dbTelemetryLogs.compute.record,held);
dbAdminSession({...session,platform_admin:false});
for(const kind of ['sre','compute']){
 assert.equal(dbTelemetryLogs[kind].record,null);
 assert.equal(logButton(kind,'fetch').disabled,true);
 assert.ok(!logOutput(kind).includes(sampled));
}
dbAdminSession(session);
assert.equal(logButton('compute','fetch').disabled,true);
""")


    def test_raw_manual_fetch_literal_separate_streams_and_clear(self):
        self.javascript(r"""
await rawBoot();
assert.equal(rawCalls().length,0);
assert.equal(rawNode('panel').parentElement,$('bear-panel-sre'));
assert.match(visibleText(rawNode('panel')),/AgentHouse and Loki on LOCAL.*64 KiB.*Sensitive, unredacted/s);
assert.equal(rawNode('status').role,'status');assert.equal(rawNode('output')['aria-live'],undefined);
assert.equal(rawNode('fetch').disabled,true);
await rawFetch();assert.equal(rawCalls().length,0);
await rawNode('auth').onclick();assert.equal(rawCalls().length,0);assert.equal(rawNode('fetch').disabled,false);
const writesBefore=writes.length;
await rawFetch();
assert.deepEqual(rawPre().map(pre=>pre.textContent),[rawText,rawErr,'','']);
assert.ok(rawPre().every(pre=>!uiBindings.has(pre)&&pre.children.length===0));
assert.match(visibleText(rawNode('output')),/Available, zero lines/);
assert.equal(writes.length,writesBefore);
rawNode('clear').click();assert.equal(rawOutput().length,0);assert.equal(rawCalls().length,1);
await rawFetch();const refreshing=rawNode('auth').onclick();assert.equal(rawOutput().length,0);await refreshing;
assert.equal(rawCalls().length,2);assert.equal(dbTelemetryLogs.sre.record,null);
language='ja';dbObservationsSync();assert.match(visibleText(rawNode('panel')),/機密データ/);
""")

    def test_raw_authority_refresh_applies_current_ceiling_for_same_principal(self):
        self.javascript(r"""
await rawBoot();await rawNode('auth').onclick();await rawFetch();
sessionRoute=()=>({...session,username:'different',role:'bi_viewer',maximum_role:'bi_viewer'});
await rawNode('auth').onclick();
assert.equal(dbMaximumRole,'admin');assert.equal(rawOutput().length,0);
sessionRoute=()=>({...session,role:'bi_viewer',maximum_role:'bi_viewer'});
await rawNode('auth').onclick();
assert.equal(dbMaximumRole,'bi_viewer');assert.equal(rawNode('fetch').disabled,true);
await rawFetch();assert.equal(rawCalls().length,1);
""")

    def test_raw_rejects_invalid_utf8_framing_and_combined_bounds(self):
        self.javascript(r"""
await rawBoot();await rawNode('auth').onclick();
const invalid=[
 {stderr:'Docker diagnostic\n'}, {stdout:rawText.slice(0,-1)}, {stdout:earlier+' \uD800\n'},
 {stdout:earlier+' '+'界'.repeat(22000)+'\n'},
 {stdout:(earlier+' x\n').repeat(100),line_count:101},
 {status:'unavailable'}, {line_count:0}, {unexpected:'discard'},
 {stdout:'2026-02-30T00:00:00Z invalid\n'}
];
for(const change of invalid){
 const reply=rawReply();Object.assign(reply.agenthouse,change);await rawFetch(reply);
 assert.equal(rawOutput().length,0);assert.match(rawNode('status').textContent,/could not be loaded/);
}
rawRoute({ok:true,status:200,arrayBuffer:async()=>Uint8Array.of(255).buffer});
await rawNode('fetch').onclick();assert.equal(rawOutput().length,0);
const unavailableRaw=rawReply();unavailableRaw.loki.status='unavailable';await rawFetch(unavailableRaw);
assert.match(visibleText(rawNode('output')),/Logs unavailable/);assert.equal(rawPre().length,2);
""")

    def test_raw_late_body_and_stale_denial_do_not_replace_new_context(self):
        self.javascript(r"""
await rawBoot();await rawNode('auth').onclick();await rawFetch();
const body=deferred();rawRoute({ok:true,status:200,arrayBuffer:()=>body.promise});
const old=rawNode('fetch').onclick();assert.equal(rawOutput().length,0);await tick();
activeTab='compute';dbObservationsSync();activeTab='bears';dbObservationsSync();
assert.equal(rawNode('fetch').disabled,true);
await rawNode('auth').onclick();await rawFetch();
body.resolve(rawBytes({...rawReply(),agenthouse:{...rawReply().agenthouse,stdout:earlier+' stale\n'}}));await old;
assert.equal(rawPre()[0].textContent,rawText);

const denial=deferred();route=()=>denial.promise;
const pending=rawNode('fetch').onclick();await tick();
dbAdminSession({});assert.equal(rawOutput().length,0);
dbAdminSession(session);await rawNode('auth').onclick();await rawFetch();
denial.resolve({http:403});await pending;
assert.equal(dbAdmin.platform,true);assert.equal(rawPre()[0].textContent,rawText);

const held=deferred();rawRoute({ok:true,status:200,arrayBuffer:()=>held.promise});
const clearing=rawNode('fetch').onclick();await tick();rawNode('clear').click();
held.resolve(rawBytes(rawReply()));await clearing;assert.equal(rawOutput().length,0);
await rawFetch();const rawScope=$('observation-sre-scope');rawScope.value='database';rawScope.onchange();
assert.equal(rawOutput().length,0);assert.equal(rawNode('panel').hidden,true);
rawScope.value='platform';rawScope.onchange();await rawNode('auth').onclick();await rawFetch();
dbObservationNavigate('policy');dbObservationNavigate('sre');
assert.equal(rawOutput().length,0);assert.equal(rawNode('fetch').disabled,true);
await rawNode('auth').onclick();await rawFetch();
document.hidden=true;dbRawLogsSync();document.hidden=false;dbRawLogsSync();
assert.equal(rawOutput().length,0);assert.equal(rawNode('fetch').disabled,true);
""")

    def test_loki_manual_capture_and_search_are_independent_and_inputless(self):
        self.javascript(r"""
await rawBoot();
const node=key=>$('observation-sre-loki-errors-'+key);
const path='/api/v1/bears/platform-loki-errors';
const searches=()=>calls.filter(call=>call.path===path);
const hit={timestamp:newest,source:'agenthouse',severity:'error'};
const reply=(status,hits=[])=>({operation:'search',status,since_minutes:10,window_end:newest,limit:20,hits});
let query=reply('available',[hit]);
route=(url,options)=>{
 if(url!==path)return runtimeFixtureRoute(url,options);
 const capture=options.method==='POST';
 assert.deepEqual(options,{cache:'no-store',headers:{'X-Shirokuma-Role':'admin'},...(capture?{method:'POST'}:{})});
 return capture?{operation:'capture',status:'accepted',since_minutes:10,window_end:newest,tail_lines:100,max_bytes:65536,
  accepted:1,sources:{agenthouse:'available',loki:'unavailable'}}:query;
};
assert.equal(node('panel').parentElement,$('bear-panel-sre'));
assert.match(node('captureBounds').textContent,/100 lines and 64 KiB/);
assert.match(node('searchBounds').textContent,/20 stored events.*24-hour/);
assert.ok(node('search').disabled&&node('capture').disabled);
await node('auth').onclick();assert.equal(searches().length,0);
await node('search').onclick();
assert.match(visibleText(node('hits')),/AgentHouse · error/);
await node('capture').onclick();
assert.match(node('captureResult').textContent,/accepted by Loki · 1.*Loki: sample unavailable/);
assert.match(visibleText(node('hits')),/AgentHouse · error/);
assert.equal(rawCalls().length,0);assert.equal(logCalls().length,0);
query=reply('available');await node('search').onclick();
assert.match(node('searchResult').textContent,/No stored events/);
query=reply('unavailable');await node('search').onclick();
assert.match(node('searchResult').textContent,/could not be searched/);
query=reply('available',[{...hit,line:'private'}]);await node('search').onclick();
assert.match(node('status').textContent,/request failed/);assert.equal(node('hits').children.length,0);
assert.equal(searches().length,5);
""")

    def test_loki_discards_late_body_on_visibility_and_stale_auth_on_leave(self):
        self.javascript(r"""
await rawBoot();
const node=key=>$('observation-sre-loki-errors-'+key);
const result={operation:'search',status:'available',since_minutes:10,window_end:newest,limit:20,
 hits:[{timestamp:newest,source:'loki',severity:'fatal'}]};
await node('auth').onclick();
const body=deferred(),entered=deferred();
route=()=>({telemetryResponse:{ok:true,status:200,json:()=>{entered.resolve();return body.promise;}}});
const pending=node('search').onclick();await entered.promise;
document.hidden=true;dbLokiErrorsSync();document.hidden=false;dbLokiErrorsSync();
body.resolve(result);await pending;
assert.equal(node('hits').children.length,0);assert.equal(node('search').disabled,true);
await node('auth').onclick();
const denied=deferred();route=()=>denied.promise;
const old=node('capture').onclick();await tick();
dbObservationNavigate('policy');dbObservationNavigate('sre');
assert.equal(node('capture').disabled,true);
await node('auth').onclick();route=()=>result;await node('search').onclick();
denied.resolve({http:403});await old;
assert.equal(dbAdmin.platform,true);assert.match(visibleText(node('hits')),/Loki · fatal/);
dbAdminSession({...session,platform_admin:false});
assert.equal(node('hits').children.length,0);assert.equal(node('capture').disabled,true);
""")
