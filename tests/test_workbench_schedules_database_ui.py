"""Shipped database schedule handlers using the existing DOM/fetch adapter."""
import unittest
import test_workbench_database_ui as harness
from test_workbench_jobs_database_ui import SETUP as JOBS

SETUP = JOBS + r'''
let scheduleRows=[],scheduleTicks=[],alerts=[],scheduleFailure=null,scheduleGate=null,replyOwner='owner';
const previousRoute=route;
route=async(path,options)=>{
 const url=new URL('http://local'+path),body=options?.body?JSON.parse(options.body):null,context=body?.context||JSON.parse(url.searchParams.get('context')||'null');
 if(url.pathname==='/api/v1/schedules'&&body){
  if(scheduleGate)await scheduleGate.promise;
  if(scheduleFailure==='lost')throw Error('response lost');
  if(scheduleFailure)return {http:409,body:{code:scheduleFailure}};
  const schedule={id:body.schedule_id||'schedule-1',owner:replyOwner,database_id:context.database_id,selected_role:context.selected_role,context:structuredClone(context),target_kind:body.target_kind,target_id:body.target_id,target_revision:body.target_revision,revision:body.expected_revision+1,spec:structuredClone(body.spec),intent:structuredClone(body.intent),enabled:body.enabled,next_run_at:body.enabled?'2028-10-23T00:00:00Z':null,last_status:'succeeded',paused_reason:null};scheduleRows=scheduleRows.filter(s=>s.id!==schedule.id).concat(schedule);return {context,schedule};
 }
 if(url.pathname==='/api/v1/schedules')return {context,schedules:structuredClone(scheduleRows.filter(s=>s.target_kind===url.searchParams.get('target_kind')&&s.target_id===url.searchParams.get('target_id')))};
 if(url.pathname.endsWith('/ticks'))return {context,ticks:structuredClone(scheduleTicks)};
 if(url.pathname.startsWith('/api/v1/schedules/'))return {context,schedule:structuredClone(scheduleRows.find(s=>s.id===url.pathname.split('/').at(-1)))};
 if(url.pathname==='/api/v1/alerts')return {context,alerts:structuredClone(alerts),unacknowledged_count:alerts.filter(a=>!a.acknowledged_at&&!a.resolved_at).length};
 if(url.pathname.startsWith('/api/v1/alerts/')&&body){const alert=alerts.find(a=>a.id===url.pathname.split('/').at(-2));alert.acknowledged_at='2028-10-22T00:00:00Z';return {context,alert:structuredClone(alert)};}
 return previousRoute(path,options);
};
const schedulePosts=()=>calls.filter(c=>c.path==='/api/v1/schedules'&&c.options?.method==='POST');
const node=name=>$('db-schedule-job-'+name);
async function ready(){await createJob();return dbSchedules.get('job');}
const tickRow=(extra={})=>({id:'00000000-0000-0000-0000-000000000001',schedule_id:'schedule-1',schedule_revision:1,target_revision:1,scheduled_for:'2026-11-11T14:07:00+00:00',status:'succeeded',code:null,run_id:'00000000-0000-0000-0000-000000000002',...extra});
const visibleTicks=()=>node('ticks-main').descendants().map(n=>n.textContent).join('\n');
async function calendarReady(zone='Asia/Tokyo') {const s=await ready();node('kind').value='daily';node('timezone').value=zone;await dbScheduleSave(s,'save',false);return s;}

'''


class SchedulesUI(unittest.TestCase):
    def javascript(self, body):
        harness.DatabaseUI.javascript(self, SETUP + body)

    def test_disabled_calendar_save_enable_stop_and_independent_cas(self):
        self.javascript(r'''
const s=await ready();assert.equal(node('enable').disabled,true);node('kind').value='weekly';node('kind').onchange();node('time').value='08:30';node('timezone').value='UTC';node('weekday-3').checked=true;node('arguments').value='{"n":7,"enabled":false}';
await dbScheduleSave(s,'save',false);let body=JSON.parse(schedulePosts().at(-1).options.body);assert.equal(body.enabled,false);assert.equal(body.expected_revision,0);assert.equal(body.target_revision,1);assert.equal(body.schedule_id,undefined);assert.deepEqual(body.spec,{kind:'weekly',time:'08:30',timezone:'UTC',weekdays:[1,3]});assert.deepEqual(body.intent,{arguments:{n:7,enabled:false}});assert.equal(s.record.revision,1);assert.equal(p.job.revision,1);assert.equal(node('enable').disabled,false);
await dbScheduleSave(s,'enable',true);body=JSON.parse(schedulePosts().at(-1).options.body);assert.equal(body.expected_revision,1);assert.equal(body.target_revision,1);assert.equal(body.schedule_id,'schedule-1');assert.equal(body.enabled,true);assert.equal(s.record.revision,2);assert.equal(p.job.revision,1);
await dbScheduleSave(s,'stop',false);body=JSON.parse(schedulePosts().at(-1).options.body);assert.equal(body.expected_revision,2);assert.equal(body.enabled,false);assert.equal(s.record.revision,3);
for(const [kind,extra] of [['interval',{every_minutes:60}],['daily',{time:'08:30',timezone:'UTC'}],['once',{time:'08:30',timezone:'UTC',date:'2028-12-01'}],['monthly',{time:'08:30',timezone:'UTC',day_of_month:'last'}]]){node('kind').value=kind;node('time').value='08:30';node('timezone').value='UTC';node('date').value='2028-12-01';node('day').value='last';await dbScheduleSave(s,'save',false);assert.deepEqual(JSON.parse(schedulePosts().at(-1).options.body).spec,{kind,...extra});}
assert.equal(schedulePosts().every(c=>!!c.options.headers['X-Idempotency-Key']),true);assert.equal(calls.some(c=>c.path.startsWith('/api/jobs/schedules')),false);
''')

    def test_dirty_fields_caret_survive_authorization_and_history_refresh(self):
        self.javascript(r'''
const s=await ready();await dbScheduleSave(s,'save',false);node('arguments').value='{"draft';node('arguments').oninput();node('arguments').focus();node('arguments').setSelectionRange(2,5);const editor=node('arguments');
await periodicRefresh();await dbJobsListHistory();await dbJobsRefresh();assert.equal(node('arguments'),editor);assert.equal(editor.value,'{"draft');assert.equal(document.activeElement,editor);assert.equal(editor.selectionStart,2);assert.equal(editor.selectionEnd,5);assert.equal(node('enable').disabled,true);assert.equal(s.dirty,true);assert.equal(schedulePosts().length,1);assert.equal(node('minutes').parentElement.parentElement.hidden,false);
setLanguage('ja');assert.equal(node('save').textContent,'スケジュールジョブを保存');assert.equal(node('status').getAttribute('role'),'status');
''')

    def test_lost_post_and_conflict_require_get_reload_no_retry(self):
        self.javascript(r'''
const s=await ready();scheduleFailure='lost';await dbScheduleSave(s,'save',false);const attempt=s.attempt;assert.equal(s.blocked,true);assert.ok(attempt.key);assert.equal(node('save').disabled,true);await dbScheduleSave(s,'save',false);await periodicRefresh();assert.equal(schedulePosts().length,1);assert.equal(s.attempt,attempt);
scheduleFailure=null;await dbScheduleReload(s);assert.equal(schedulePosts().length,1);assert.equal(s.blocked,false);await dbScheduleSave(s,'save',false);assert.notEqual(schedulePosts().at(-1).options.headers['X-Idempotency-Key'],attempt.key);
scheduleFailure='REVISION_CONFLICT';await dbScheduleSave(s,'enable',true);assert.equal(s.blocked,true);assert.equal(s.record.enabled,false);await dbScheduleSave(s,'enable',true);assert.equal(schedulePosts().length,3);scheduleFailure=null;await dbScheduleReload(s);assert.equal(s.record,null);assert.equal(node('enable').disabled,true);node('list').value='schedule-1';await dbScheduleLoad(s);assert.equal(s.record.revision,1);assert.equal(node('enable').disabled,false);
''')

    def test_owner_role_mismatch_and_grant_loss_clear_protected_state(self):
        self.javascript(r'''
const s=await ready();replyOwner='another';await dbScheduleSave(s,'save',false);assert.equal(s.record,null);assert.equal(s.blocked,true);replyOwner='owner';scheduleRows=[];await dbScheduleReload(s);await dbScheduleSave(s,'save',false);assert.ok(s.record);
scheduleRows[0].selected_role='data_engineer';await dbScheduleReload(s);assert.equal(node('status').textContent.includes('Reload'),true);scheduleRows[0].selected_role='admin';
inventory=[];await dbRefreshAccess();assert.equal(s.record,null);assert.equal(s.records.length,0);assert.equal(s.ticks.length,0);assert.equal(s.attempt,null);assert.equal(node('arguments').value,'{}');assert.equal(node('enable').disabled,true);assert.equal(node('detail').textContent,'');assert.equal(node('list').options.some(o=>o.value==='schedule-1'),false);
''')

    def test_late_save_after_navigation_cannot_reenable_or_restore(self):
        self.javascript(r'''
const s=await ready();scheduleGate=deferred();const saving=dbScheduleSave(s,'save',false);await tick();$('db-jobs-new').onclick();assert.equal(s.scope,'');scheduleGate.resolve();await saving;assert.equal(s.record,null);assert.equal(node('enable').disabled,true);assert.equal(node('detail').textContent,'');
''')

    def test_late_reply_after_grant_or_role_change_and_definition_revision(self):
        self.javascript(r'''
const s=await ready();await dbScheduleSave(s,'save',false);$('db-jobs-description').value='changed';await dbJobsSave();assert.equal(p.job.revision,2);assert.equal(s.record,null);assert.equal(node('enable').disabled,true);await dbScheduleReload(s);node('list').value='schedule-1';await dbScheduleLoad(s);assert.equal(s.record.target_revision,1);assert.equal(node('enable').disabled,true);await dbScheduleSave(s,'save',false);assert.equal(s.record.target_revision,2);assert.equal(s.record.revision,2);
scheduleGate=deferred();const saving=dbScheduleSave(s,'enable',true);await tick();inventory=[];await dbRefreshAccess();scheduleGate.resolve();await saving;assert.equal(s.record,null);assert.equal(s.attempt,null);assert.equal(node('enable').disabled,true);
''')

    def test_tick_true_false_history_and_alert_ack_scope(self):
        self.javascript(r'''
const s=await ready();await dbScheduleSave(s,'save',false);const context=structuredClone(p.context);scheduleTicks=[{id:'00000000-0000-0000-0000-000000000001',schedule_id:'schedule-1',schedule_revision:1,target_revision:1,scheduled_for:'2026-10-22T00:00:00Z',status:'succeeded',code:null,run_id:null,results:[{triggered:true},{triggered:false}]}];await dbScheduleTicks(s);assert.match(node('ticks-detail').textContent,/"triggered": true/);assert.match(node('ticks-detail').textContent,/"triggered": false/);
scheduleTicks[0].schedule_id='another';await dbScheduleTicks(s);assert.equal(s.ticks[0].schedule_id,'schedule-1');
alerts=[{id:'alert-1',owner:'owner',database_id:context.database_id,selected_role:context.selected_role,context,acknowledged_at:null,resolved_at:null,occurrences:2}];await dbScheduleAlerts(s);assert.equal(s.alerts.length,1);await dbScheduleAlerts(s,s.alerts[0]);const ack=calls.find(c=>c.path==='/api/v1/alerts/alert-1/ack');assert.deepEqual(JSON.parse(ack.options.body),{context});assert.equal(ack.options.headers['X-Shirokuma-Role'],'admin');assert.ok(ack.options.headers['X-Idempotency-Key']);assert.match(node('alerts-count').textContent,/: 0/);
currentRole='bi_viewer';dbApplyRole();assert.equal(s.alerts.length,0);assert.equal(node('alerts-detail').children.length,0);assert.equal(node('alerts-refresh').disabled,true);
''')

    def test_stage_and_mart_use_independent_targets_and_required_intent(self):
        self.javascript(r'''
await ready();const context=structuredClone(p.context);for(const kind of ['stage','mart']){const panel=dbSchedulePanel(kind);panel.installed=true;panel.invalid=false;panel.principal=dbPrincipal;panel.context=context;panel.role='admin';panel.database=context.database_id;panel.schema=context.schema_id;panel.schemas=schemas;panel.selection=kind+'-1';panel[kind]={id:kind+'-1',owner:'owner',selected_role:'admin',database_id:context.database_id,context,revision:5,definition:kind==='stage'?{name:'inbox',directory:'/approved',mode:'append',atomic_append:true,format:'csv',destination:{}}:{name:'mart',source_refs:[{database_id:'db'}],target:{schema_id:'schema'}}};}
inventory[0].capabilities.stage=true;for(const sc of schemas)sc.capabilities.stage=true;
// Use the public capability key exported by the Stage panel.
inventory[0].capabilities[DB_STAGES_CAPABILITY]=true;for(const sc of schemas)sc.capabilities[DB_STAGES_CAPABILITY]=true;const items=await dbReadInventory('admin');dbApplyInventory(items,'admin');
const originalStagesMatches=dbStagesMatches,originalMartsMatches=dbMartsMatches;dbStagesMatches=()=>true;dbMartsMatches=()=>true;
for(const kind of ['stage','mart']){dbScheduleSync(kind);const state=dbSchedules.get(kind);assert.ok(dbScheduleTarget(kind),kind+' target '+JSON.stringify({invalid:dbSchedulePanel(kind).invalid,context:dbSchedulePanel(kind).context,target:dbSchedulePanel(kind)[kind],allowed:dbScheduleAllowed(kind)}));assert.equal(dbScheduleNode(state,'save').disabled,false,kind+' save disabled');await dbScheduleSave(state,'save',false);const body=JSON.parse(schedulePosts().at(-1).options.body);assert.equal(body.target_kind,kind);assert.equal(body.target_id,kind+'-1');assert.equal(body.target_revision,5);assert.deepEqual(body.intent,kind==='stage'?{action:'scan'}:{action:'refresh',check_changes:true});assert.equal(body.enabled,false);setLanguage('ja');assert.equal(dbScheduleNode(state,'save').textContent,'スケジュールを保存');}
dbStagesMatches=originalStagesMatches;dbMartsMatches=originalMartsMatches;
''')

    def test_old_saved_context_drift_reloads_cas_updates_reenables_and_acks(self):
        self.javascript(r'''
const s=await ready();await dbScheduleSave(s,'save',false);const current=structuredClone(p.context),old={...current,binding_revision:9,generation_id:'old-generation',grant_revision:2};scheduleRows[0].context=old;scheduleRows[0].paused_reason='STALE_BINDING';
await dbScheduleReload(s);assert.equal(s.records.length,1);node('list').value='schedule-1';await dbScheduleLoad(s);assert.equal(s.record.context.generation_id,'old-generation');assert.match(node('detail').textContent,/STALE_BINDING/);assert.equal(s.record.enabled,false);
await dbScheduleSave(s,'save',false);let body=JSON.parse(schedulePosts().at(-1).options.body);assert.deepEqual(body.context,current);assert.equal(body.expected_revision,1);assert.equal(s.record.revision,2);await dbScheduleSave(s,'enable',true);body=JSON.parse(schedulePosts().at(-1).options.body);assert.deepEqual(body.context,current);assert.equal(body.expected_revision,2);assert.equal(body.enabled,true);
alerts=[{id:'old-alert',owner:'owner',database_id:current.database_id,selected_role:current.selected_role,context:old,acknowledged_at:null,resolved_at:null}];await dbScheduleAlerts(s);assert.equal(s.alerts.length,1);await dbScheduleAlerts(s,s.alerts[0]);const ack=calls.find(c=>c.path==='/api/v1/alerts/old-alert/ack');assert.deepEqual(JSON.parse(ack.options.body),{context:current});assert.equal(s.alertPending,false);
for(const field of ['database_id','schema_id','warehouse_id','sql_catalog','sql_schema','selected_role'])assert.equal(dbScheduleScoped({...alerts[0],context:{...old,[field]:'wrong'}},current),false);
''')

    def test_initial_alert_sequence_success_error_and_ack_pending_release(self):
        self.javascript(r'''
const s=await ready();assert.equal(Number.isInteger(s.alertSequence),true);const seq=s.sequence;await dbScheduleAlerts(s);assert.equal(s.alertPending,false);assert.equal(s.sequence,seq);assert.equal(node('alerts-refresh').disabled,false);
const previous=route;route=async(path,options)=>path.startsWith('/api/v1/alerts?')?{http:502,body:{code:'SERVICE_UNAVAILABLE'}}:previous(path,options);await dbScheduleAlerts(s);assert.equal(s.alertPending,false);assert.equal(node('alerts-refresh').disabled,false);route=previous;
alerts=[{id:'fresh-alert',owner:'owner',database_id:p.context.database_id,selected_role:p.context.selected_role,context:structuredClone(p.context),acknowledged_at:null,resolved_at:null}];await dbScheduleAlerts(s);await dbScheduleAlerts(s,s.alerts[0]);assert.equal(s.alertPending,false);assert.equal(s.ackAttempt,null);assert.equal(s.sequence,seq);assert.equal(s.alerts[0].acknowledged_at!==null,true);
''')

    def test_stage_eligibility_and_fixed_mart_intent_controls(self):
        self.javascript(r'''
await ready();const context=structuredClone(p.context),panel=dbStages;panel.installed=true;panel.invalid=false;panel.principal=dbPrincipal;panel.context=context;panel.role='admin';panel.schemas=schemas;panel.selection='stage-1';panel.stage={id:'stage-1',owner:'owner',context,revision:1,definition:{name:'inbox',mode:'replace'}};inventory[0].capabilities[DB_STAGES_CAPABILITY]=true;for(const sc of schemas)sc.capabilities[DB_STAGES_CAPABILITY]=true;const items=await dbReadInventory('admin');dbApplyInventory(items,'admin');const original=dbStagesMatches;dbStagesMatches=()=>true;
for(const [definition,eligible] of [[{mode:'create'},false],[{mode:'replace'},false],[{mode:'append'},false],[{mode:'append',atomic_append:true},true],[{mode:'upsert',key_columns:[]},false],[{mode:'upsert',key_columns:['id']},true]]){panel.stage.definition={name:'inbox',...definition};dbScheduleSync('stage');assert.equal(dbScheduleEligible('stage'),eligible);assert.equal(dbScheduleNode(dbSchedules.get('stage'),'save').disabled,!eligible);if(!eligible){const before=schedulePosts().length;await dbScheduleSave(dbSchedules.get('stage'),'save',false);assert.equal(schedulePosts().length,before);}}
dbStagesMatches=original;assert.equal(node('check-changes').disabled,true);assert.equal(node('check-changes').checked,true);setLanguage('ja');assert.equal(t('Refresh when sources change'),'ソース変更時に更新');
''')

    def test_alert_ack_loss_never_reposts_and_late_read_cannot_restore(self):
        self.javascript(r'''
const s=await ready(),context=structuredClone(p.context);alerts=[{id:'alert-1',owner:'owner',database_id:context.database_id,selected_role:context.selected_role,context,acknowledged_at:null,resolved_at:null}];await dbScheduleAlerts(s);const originalRoute=route;let gate=null,lose=true;
route=async(path,options)=>{if(path==='/api/v1/alerts/alert-1/ack'&&lose)throw Error('response lost');if(path.startsWith('/api/v1/alerts?')&&gate)await gate.promise;return originalRoute(path,options);};
await dbScheduleAlerts(s,s.alerts[0]);const attempt=s.ackAttempt;assert.ok(attempt);await dbScheduleAlerts(s,s.alerts[0]);assert.equal(calls.filter(c=>c.path==='/api/v1/alerts/alert-1/ack').length,1);assert.match(node('alerts-count').textContent,/alert-1/);assert.ok(node('alerts-count').textContent.includes(attempt.key));
lose=false;await dbScheduleAlerts(s);assert.equal(s.ackAttempt,null);gate=deferred();const reading=dbScheduleAlerts(s);await tick();inventory=[];await dbRefreshAccess();gate.resolve();await reading;assert.equal(s.alerts.length,0);assert.equal(node('alerts-detail').children.length,0);assert.equal(node('alerts-refresh').disabled,true);
''')


    def test_loaded_jst_times_rollover_draft_locale_and_raw_diagnostics(self):
        self.javascript(r'''
const s=await calendarReady();scheduleRows[0].next_run_at='2026-11-11T14:07:00+00:00';await dbScheduleReload(s);node('list').value='schedule-1';await dbScheduleLoad(s);
assert.match(node('detail').textContent,/2026-11-11 23:07:00 Asia\/Tokyo \(UTC\+09:00\)/);assert.match(node('detail').textContent,/Disabled/);
scheduleTicks=[tickRow(),tickRow({id:'00000000-0000-0000-0000-000000000003',scheduled_for:'2026-11-11T18:07:00Z',schedule_revision:8,target_revision:7,status:'skipped',code:'<img src=x onerror=alert(1)>',results:[{triggered:false,extra:'retained'}]})];await dbScheduleTicks(s);
assert.match(visibleTicks(),/2026-11-12 03:07:00 Asia\/Tokyo \(UTC\+09:00\)/);assert.match(node('ticks-diagnostics').descendants().map(n=>n.textContent).join('\n'),/older revisions may have used another timezone/);
const cells=node('ticks-main').descendants().filter(n=>n.tagName==='TD');assert.equal(cells[3].textContent,scheduleTicks[0].run_id);assert.equal(cells[6].textContent,scheduleTicks[1].code);assert.equal(cells[6].children.length,0);assert.ok(node('ticks-main').descendants().some(n=>n.className==='table-wrap'));
assert.ok(!node('ticks-diagnostics').open);assert.ok(!node('record-diagnostics').open);assert.deepEqual(JSON.parse(node('ticks-detail').textContent),scheduleTicks);assert.equal(JSON.parse(node('ticks-detail').textContent)[1].schedule_revision,8);assert.equal(JSON.parse(node('ticks-detail').textContent)[1].target_revision,7);assert.equal(JSON.parse(node('record-detail').textContent).next_run_at,'2026-11-11T14:07:00+00:00');
node('timezone').value='America/New_York';node('timezone').oninput();node('arguments').value='{"draft';node('arguments').oninput();node('arguments').focus();node('arguments').setSelectionRange(2,5);node('ticks-diagnostics').open=true;node('record-diagnostics').open=true;
const table=node('ticks-main').querySelector('table'),editor=node('arguments');await periodicRefresh();dbScheduleControls(s);assert.equal(node('ticks-main').querySelector('table'),table);assert.equal(node('ticks-diagnostics').open,true);assert.equal(node('record-diagnostics').open,true);const before=calls.length;setLanguage('ja');assert.equal(calls.length,before);assert.match(visibleTicks(),/表示タイムゾーン: Asia\/Tokyo/);assert.match(visibleTicks(),/スキップ/);assert.match(node('detail').textContent,/次回実行: 2026-11-11 23:07:00 Asia\/Tokyo/);assert.match(node('detail').textContent,/無効/);
assert.equal(node('arguments'),editor);assert.equal(editor.value,'{"draft');assert.equal(document.activeElement,editor);assert.equal(editor.selectionStart,2);assert.equal(editor.selectionEnd,5);assert.equal(node('timezone').value,'America/New_York');assert.equal(node('ticks-diagnostics').open,true);assert.equal(node('record-diagnostics').open,true);
setLanguage('en');assert.equal(calls.length,before);assert.match(visibleTicks(),/skipped/);assert.match(visibleTicks(),/Asia\/Tokyo/);
scheduleTicks=[tickRow({status:'reserved'}),tickRow({id:'00000000-0000-0000-0000-000000000003',status:'accepted'}),tickRow({id:'00000000-0000-0000-0000-000000000004',status:'paused'})];await dbScheduleTicks(s);setLanguage('ja');assert.match(visibleTicks(),/予約済み/);assert.match(visibleTicks(),/受付済み/);assert.match(visibleTicks(),/一時停止/);
''')

    def test_non_jst_dst_and_explicit_utc_fallback(self):
        self.javascript(r'''
const s=await calendarReady('America/New_York');scheduleTicks=[tickRow({scheduled_for:'2026-01-11T14:07:00Z'}),tickRow({id:'00000000-0000-0000-0000-000000000003',scheduled_for:'2026-07-11T14:07:00Z'})];await dbScheduleTicks(s);
assert.match(visibleTicks(),/2026-01-11 09:07:00 America\/New_York \(UTC-05:00\)/);assert.match(visibleTicks(),/2026-07-11 10:07:00 America\/New_York \(UTC-04:00\)/);
for(const spec of [{kind:'interval',every_minutes:60,timezone:'Asia/Tokyo'},{kind:'daily',time:'09:00'},{kind:'daily',time:'09:00',timezone:'invalid/Zone'},{kind:'daily',time:'09:00',timezone:'+01:00'}]){dbSchedulePopulate(s,{...s.record,spec});await dbScheduleTicks(s);assert.match(visibleTicks(),/UTC fallback/);assert.match(visibleTicks(),/2026-01-11 14:07:00 UTC \(UTC\+00:00\)/);setLanguage('ja');assert.match(visibleTicks(),/UTC で表示/);setLanguage('en');}
''')

    def test_history_empty_new_load_save_revision_and_zone_transitions(self):
        self.javascript(r'''
const s=await calendarReady();await dbScheduleTicks(s);assert.match(visibleTicks(),/No ticks recorded/);assert.equal(node('ticks-detail').textContent,'[]');setLanguage('ja');assert.match(visibleTicks(),/実行履歴はありません/);setLanguage('en');
scheduleTicks=[tickRow()];await dbScheduleTicks(s);node('timezone').value='America/New_York';node('timezone').oninput();await dbScheduleSave(s,'save',false);assert.equal(s.record.revision,2);assert.equal(s.ticks.length,0);assert.equal(node('ticks-detail').textContent,'');assert.doesNotMatch(visibleTicks(),/23:07/);assert.match(visibleTicks(),/Inspect ticks to load history/);
await dbScheduleTicks(s);assert.match(visibleTicks(),/2026-11-11 09:07:00 America\/New_York/);assert.equal(JSON.parse(node('ticks-detail').textContent)[0].schedule_revision,1);
node('list').value='schedule-1';await dbScheduleLoad(s);assert.equal(s.ticks.length,0);assert.equal(node('ticks-detail').textContent,'');await dbScheduleTicks(s);node('new').onclick();assert.equal(s.record,null);assert.equal(node('ticks-main').children.length,0);assert.equal(node('ticks-detail').textContent,'');assert.equal(node('record-detail').textContent,'');
''')

    def test_stale_tick_reply_and_denial_clear_all_rendered_protected_data(self):
        self.javascript(r'''
const s=await calendarReady();scheduleTicks=[tickRow()];await dbScheduleTicks(s);const original=route;let gate=deferred();route=async(path,options)=>{if(path.includes('/ticks')){await gate.promise;}return original(path,options);};
const reading=dbScheduleTicks(s);await tick();$('db-jobs-new').onclick();assert.equal(s.scope,'');gate.resolve();await reading;assert.equal(s.ticks.length,0);assert.equal(node('ticks-main').children.length,0);assert.equal(node('ticks-detail').textContent,'');assert.equal(node('record-detail').textContent,'');
''')
        self.javascript(r'''
const s=await calendarReady(),original=route;scheduleTicks=[tickRow()];await dbScheduleTicks(s);assert.ok(node('ticks-main').children.length);route=async(path,options)=>path.includes('/ticks')?{http:403,body:{code:'FORBIDDEN'}}:original(path,options);await dbScheduleTicks(s);
assert.equal(s.record,null);assert.equal(s.ticks.length,0);assert.equal(node('ticks-main').children.length,0);assert.equal(node('ticks-detail').textContent,'');assert.equal(node('record-detail').textContent,'');const before=calls.length;setLanguage('ja');assert.equal(calls.length,before);assert.equal(node('ticks-main').children.length,0);
''')


if __name__ == '__main__': unittest.main()
