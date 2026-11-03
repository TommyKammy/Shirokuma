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


class PlatformTelemetryLogsDatabaseUI(unittest.TestCase):
    def javascript(self, body):
        readiness.PlatformTelemetryReadinessDatabaseUI.javascript(self, LOGS + body)

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
