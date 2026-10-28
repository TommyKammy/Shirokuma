"""Exercise shipped Jobs handlers with the existing DOM/fetch harness."""
import json
import unittest
import test_workbench_database_ui as harness


SETUP = r'''
for(const s of schemas)Object.assign(s.capabilities,{job_sql:true,job_admin:true});
const p=dbJobs;
let timerId=0;const timers=new Map(),visibilityListeners=[];
globalThis.setTimeout=(callback,delay)=>{const id=++timerId;timers.set(id,{callback,delay});return id;};
globalThis.clearTimeout=id=>timers.delete(id);
document.addEventListener=(event,callback)=>{if(event==='visibilitychange')visibilityListeners.push(callback);};
const visibility=async hidden=>{document.hidden=hidden;for(const callback of visibilityListeners)callback();await tick();};
const firePoll=async()=>{assert.equal(timers.size,1);const [id,timer]=[...timers][0];assert.equal(timer.delay,2000);timers.delete(id);timer.callback();await tick();};
const runReads=()=>calls.filter(c=>c.path.startsWith('/api/v1/jobs/runs/run-1?')&&!c.options?.method);
async function showJobs(){activate($('tab-jobs'));await tick();}

let savedJobs=[],jobRuns=[],saveFailure=null,runFailure=false,runGate=null,saveGate=null,loadGate=null;
const jobContext=options=>options?.body?JSON.parse(options.body).context:JSON.parse(new URL('http://local'+options.path).searchParams.get('context'));
const jobReply=(body,id='job-1')=>({id,owner:'owner',selected_role:body.context.selected_role,revision:body.expected_revision+1,context:structuredClone(body.context),definition:structuredClone(body.definition)});
route=async(path,options)=>{
 const url=new URL('http://local'+path),body=options?.body?JSON.parse(options.body):null,context=body?.context||JSON.parse(url.searchParams.get('context')||'null');
 if(url.pathname==='/api/v1/jobs'&&body){
  if(saveGate)await saveGate.promise;if(saveFailure)return {http:409,body:{code:saveFailure}};
  const job=jobReply(body,body.job_id||'job-1');savedJobs=savedJobs.filter(j=>j.id!==job.id).concat(job);return {context,job};
 }
 if(url.pathname==='/api/v1/jobs')return {context,jobs:structuredClone(savedJobs.filter(j=>j.context.database_id===context.database_id&&j.selected_role===context.selected_role))};
 if(url.pathname==='/api/v1/jobs/runs')return {context,runs:structuredClone(jobRuns.filter(r=>r.context.database_id===context.database_id))};
 if(url.pathname==='/api/v1/jobs/job-1/runs'&&body){
  if(runGate)await runGate.promise;
  if(runFailure)throw Error('response lost');
  const run={id:'run-1',job_id:'job-1',job_name:'daily',revision:body.expected_revision,owner:'owner',selected_role:context.selected_role,context,status:'running',steps:[{name:'q',action:'sql',status:'running',operation_ids:['child-1'],summary:{count:1}}]};jobRuns=[run];return {context,run};
 }
 if(url.pathname.startsWith('/api/v1/jobs/runs/')){
  const run=structuredClone(jobRuns[0]);if(url.pathname.endsWith('/cancel'))run.status='canceled';if(url.pathname.endsWith('/reconcile'))run.status='failed';jobRuns=[run];return {context,run};
 }
 if(url.pathname.startsWith('/api/v1/jobs/')){if(loadGate)await loadGate.promise;return {context,job:structuredClone(savedJobs.find(j=>j.id===decodeURIComponent(url.pathname.split('/').at(-1))))};}
 return {};
};
const jobPosts=()=>calls.filter(c=>c.path.startsWith('/api/v1/jobs')&&c.options?.method==='POST');
async function openJobs(){await boot([input(),input('b')]);await dbJobsOpen();assert.equal(p.invalid,false);}
async function createJob(){await openJobs();$('db-jobs-name').value='daily';await dbJobsSave();assert.equal(p.job.id,'job-1');}
'''


class JobsUI(unittest.TestCase):
    def javascript(self, body):
        harness.DatabaseUI.javascript(self, SETUP + body)

    def test_auto_observes_queued_running_terminal_with_original_context_and_draft(self):
        self.javascript(r'''
await createJob();await showJobs();await dbJobsRun();const original=structuredClone(p.run.context),job=p.job;
$('db-jobs-description').value='unfinished draft';$('db-jobs-description').oninput();const editor=$('db-jobs-description');editor.focus();editor.setSelectionRange(2,7);
jobRuns[0].status='queued';await firePoll();assert.equal(p.run.status,'queued');assert.equal(timers.size,1);
jobRuns[0].status='running';await firePoll();assert.equal(p.run.status,'running');assert.equal(timers.size,1);assert.equal(p.job,job);assert.equal(p.selection,'job-1');assert.equal(document.activeElement,editor);assert.equal(editor.value,'unfinished draft');assert.equal(editor.selectionStart,2);
$('db-jobs-schema').value='raw';await $('db-jobs-schema').onchange();$('db-jobs-name').value='other context draft';$('db-jobs-name').oninput();
jobRuns[0].status='succeeded';await firePoll();assert.equal(p.run.status,'succeeded');assert.equal(p.run.details.status,'succeeded');assert.equal(timers.size,0);assert.equal($('db-jobs-name').value,'other context draft');
assert.equal(runReads().length,3);for(const read of runReads()){assert.deepEqual(JSON.parse(new URL('http://local'+read.path).searchParams.get('context')),original);assert.equal(read.options.headers['X-Shirokuma-Role'],'admin');}assert.equal(jobPosts().length,2);
''')

    def test_auto_visibility_and_section_suspend_resume(self):
        self.javascript(r'''
await createJob();await dbJobsRun();assert.equal(timers.size,0);await showJobs();assert.equal(timers.size,1);dbJobsRender();dbJobsOpen();assert.equal(timers.size,1);
const stale=[...timers.values()][0].callback;await visibility(true);assert.equal(timers.size,0);stale();await tick();assert.equal(runReads().length,0);
await visibility(false);assert.equal(timers.size,1);const resumed=p.pollTimer;stale();await tick();assert.equal(p.pollTimer,resumed);assert.equal(runReads().length,0);activate($('tab-warehouse'));assert.equal(timers.size,0);await visibility(true);await visibility(false);assert.equal(timers.size,0);
await showJobs();await firePoll();assert.equal(runReads().length,1);assert.equal(timers.size,1);
''')

    def test_auto_shares_observe_cancel_lock_without_disabling_editor(self):
        self.javascript(r'''
await createJob();await showJobs();await dbJobsRun();let gate=deferred();const originalRoute=route;route=async(path,options)=>{if(path.startsWith('/api/v1/jobs/runs/run-1?'))await gate.promise;return originalRoute(path,options);};
const editor=$('db-jobs-description');editor.focus();await firePoll();assert.equal(p.tracking,true);assert.equal(timers.size,0);assert.equal($('db-jobs-observe').disabled,true);assert.equal($('db-jobs-cancel').disabled,true);assert.equal(editor.disabled,false);assert.equal(document.activeElement,editor);
editor.value='typing while observing';editor.oninput();assert.equal(editor.disabled,false);assert.equal(document.activeElement,editor);
await dbJobsObserve();await dbJobsObserve('cancel');assert.equal(runReads().length,1);assert.equal(jobPosts().length,2);
gate.resolve();await tick();assert.equal(p.tracking,false);assert.equal(timers.size,1);assert.equal($('db-jobs-observe').disabled,false);assert.equal($('db-jobs-cancel').disabled,false);assert.equal(editor.disabled,false);assert.equal(document.activeElement,editor);
gate=deferred();const manual=dbJobsObserve();await tick();assert.equal(timers.size,0);gate.resolve();await manual;assert.equal(timers.size,1);await dbJobsObserve('cancel');assert.equal(p.run.status,'canceled');assert.equal(timers.size,0);assert.equal(jobPosts().length,3);
''')

    def test_auto_never_replays_no_id_or_unknown_and_stops_on_failure(self):
        self.javascript(r'''
await createJob();await showJobs();runFailure=true;await dbJobsRun();assert.equal(p.run.id,null);assert.equal(timers.size,0);dbJobsRender();await visibility(true);await visibility(false);assert.equal(jobPosts().length,2);assert.equal(timers.size,0);
runFailure=false;await dbJobsObserve();assert.equal(timers.size,1);jobRuns[0].status='unknown';await firePoll();assert.equal(p.run.status,'unknown');assert.equal(timers.size,0);assert.equal(jobPosts().length,3);
jobRuns[0].status='running';await dbJobsObserve();const accepted=p.run,details=accepted.details;const originalRoute=route;route=async()=>{throw Error('offline');};await firePoll();assert.equal(p.run,accepted);assert.equal(p.run.status,'running');assert.equal(p.run.details,details);assert.equal(timers.size,0);dbJobsRender();await visibility(true);await visibility(false);assert.equal(timers.size,0);assert.match(p.status.textContent,/outcome unknown/);
route=originalRoute;await dbJobsObserve();assert.equal(timers.size,1);
''')

    def test_auto_rejects_other_run_receipt_and_does_not_restore_purged_details(self):
        self.javascript(r'''
await createJob();await showJobs();await dbJobsRun();const originalRoute=route;route=async(path,options)=>{const reply=await originalRoute(path,options);if(path.startsWith('/api/v1/jobs/runs/run-1?'))reply.run.id='different-run';return reply;};
await firePoll();assert.equal(p.run.id,'run-1');assert.equal(p.run.status,'running');assert.equal(timers.size,0);
route=originalRoute;jobRuns[0].id='run-1';await dbJobsObserve();const gate=deferred();route=async(path,options)=>{if(path.startsWith('/api/v1/jobs/runs/run-1?'))await gate.promise;return originalRoute(path,options);};await firePoll();inventory=[];await dbRefreshAccess();assert.equal(p.run.purged,true);assert.equal(p.run.details,null);assert.equal(timers.size,0);gate.resolve();await tick();assert.equal(p.run.details,null);assert.equal(p.drafts.size,0);assert.equal(timers.size,0);
''')

    def test_late_auto_get_cannot_restore_private_details_after_actor_or_role_loss(self):
        for change in ("session.username='different';await dbRefreshAccess();", "currentRole='bi_viewer';dbApplyRole();"):
            with self.subTest(change=change):
                self.javascript(r'''
await createJob();await showJobs();await dbJobsRun();const accepted=p.run,gate=deferred(),originalRoute=route;
route=async(path,options)=>{if(path.startsWith('/api/v1/jobs/runs/run-1?'))await gate.promise;return originalRoute(path,options);};
await firePoll();assert.equal(p.tracking,true);CHANGE
assert.equal($('db-jobs-name').value,'');assert.equal(accepted.details,null);gate.resolve();await tick();
assert.equal(accepted.details,null);assert.equal(p.run?.details??null,null);assert.equal($('db-jobs-name').value,'');assert.equal(p.drafts.size,0);assert.equal(timers.size,0);assert.equal(jobPosts().length,2);await visibility(true);await visibility(false);assert.equal(timers.size,0);
'''.replace('CHANGE',change))

    def test_auto_timer_rechecks_actor_role_binding_and_run(self):
        for change in ("session.username='different';await dbRefreshAccess();", "currentRole='bi_viewer';dbApplyRole();", "inventory[0].binding_revision=99;await dbRefreshAccess();", "p.run={...p.run,id:'another'};"):
            with self.subTest(change=change):
                self.javascript(r'''
await createJob();await showJobs();await dbJobsRun();const callback=[...timers.values()][0].callback;CHANGE
timers.clear();callback();await tick();assert.equal(runReads().length,0);assert.equal(timers.size,0);
'''.replace('CHANGE', change))

    def test_fresh_preadmission_marker_releases_attempt_for_explicit_correction(self):
        self.javascript(r'''
await createJob();$('db-jobs-arguments').value='{"n":"wrong"}';
$('db-jobs-add-input').onclick();p.files[0].name.value='source';p.files[0].file.files=[{name:'data.csv',arrayBuffer:async()=>new Uint8Array([42]).buffer}];
const originalRoute=route;let rejected;
route=async(path,options)=>{if(path==='/api/v1/jobs/job-1/runs'){rejected=p.run;return {http:400,body:{code:'JOB_INPUT_REJECTED',details:{admission:'NOT_ATTEMPTED'}}};}return originalRoute(path,options);};
await dbJobsRun();const first=jobPosts().at(-1);assert.equal(p.run,null);assert.equal(rejected.payload,null);assert.equal(p.pending,false);assert.equal($('db-jobs-run').disabled,false);assert.equal($('db-jobs-arguments').value,'{"n":"wrong"}');assert.equal(p.job.revision,1);assert.equal(jobPosts().length,2);assert.equal(jobRuns.length,0);assert.match($('db-jobs-status').textContent,/rejected before admission/);
setLanguage('ja');assert.equal($('db-jobs-status').textContent,'実行受付前に Job の入力が拒否されました。入力を修正して、もう一度実行してください。');
await periodicRefresh();assert.equal(jobPosts().length,2);await dbJobsObserve();assert.equal(jobPosts().length,2);
route=originalRoute;$('db-jobs-arguments').value='{"n":7}';await dbJobsRun();const second=jobPosts().at(-1);assert.notEqual(second.options.headers['Idempotency-Key'],first.options.headers['Idempotency-Key']);assert.equal(JSON.parse(second.options.body).arguments.n,7);assert.deepEqual(JSON.parse(second.options.body).inputs,{source:{filename:'data.csv',content_base64:'Kg=='}});assert.equal(p.run.status,'running');
''')

    def test_generic_missing_wrong_and_admitted_markers_remain_unknown(self):
        cases = [
            (400, {'code': 'INVALID_INPUT'}),
            (400, {'code': 'INVALID_INPUT', 'details': {'admission': 'NOT_ATTEMPTED'}}),
            (400, {'code': 'JOB_INPUT_REJECTED'}),
            (400, {'code': 'JOB_INPUT_REJECTED', 'details': {'admission': 'ATTEMPTED'}}),
            (500, {'code': 'JOB_INPUT_REJECTED', 'details': {'admission': 'NOT_ATTEMPTED'}}),
            (400, {'code': 'JOB_INPUT_REJECTED', 'details': {'admission': 'NOT_ATTEMPTED', 'run': {'id': 'admitted'}}}),
            (400, {'code': 'JOB_INPUT_REJECTED', 'details': {'admission': 'NOT_ATTEMPTED', 'operation_id': 'child'}}),
            (400, {'code': 'JOB_INPUT_REJECTED', 'details': {'admission': 'NOT_ATTEMPTED'}, 'run': {'id': 'admitted'}}),
            (400, {'code': 'JOB_INPUT_REJECTED', 'details': {'admission': 'NOT_ATTEMPTED'}, 'operation_id': 'child'}),
            (400, {'details': {'admission': 'NOT_ATTEMPTED'}, 'error': {'code': 'JOB_INPUT_REJECTED', 'details': {'admission': 'NOT_ATTEMPTED', 'run_id': 'admitted'}}}),
        ]
        for status, body in cases:
            with self.subTest(status=status, body=body):
                self.javascript(r'''
await createJob();const originalRoute=route;
route=async(path,options)=>path==='/api/v1/jobs/job-1/runs'?REJECTION:originalRoute(path,options);
await dbJobsRun();const first=jobPosts().at(-1),payload=p.run.payload;assert.equal(p.run.status,'unknown');assert.equal(p.run.id,null);assert.ok(payload);assert.equal($('db-jobs-run').disabled,true);await dbJobsRun();assert.equal(jobPosts().at(-1),first);assert.equal(p.run.payload,payload);
'''.replace('REJECTION', json.dumps({'http': status, 'body': body})))

    def test_marker_on_response_loss_replay_keeps_original_unknown_envelope(self):
        self.javascript(r'''
await createJob();runFailure=true;$('db-jobs-arguments').value='{"n":1}';await dbJobsRun();const attempt=p.run,payload=attempt.payload,first=jobPosts().at(-1);assert.equal(attempt.sequence,1);
const originalRoute=route;route=async(path,options)=>path==='/api/v1/jobs/job-1/runs'?{http:400,body:{code:'JOB_INPUT_REJECTED',details:{admission:'NOT_ATTEMPTED'}}}:originalRoute(path,options);
$('db-jobs-arguments').value='{"n":2}';await dbJobsObserve();const replay=jobPosts().at(-1);assert.equal(p.run,attempt);assert.equal(attempt.sequence,2);assert.equal(attempt.status,'unknown');assert.equal(attempt.payload,payload);assert.equal(replay.options.body,first.options.body);assert.equal(replay.options.headers['Idempotency-Key'],first.options.headers['Idempotency-Key']);assert.equal($('db-jobs-run').disabled,true);await dbJobsRun();assert.equal(jobPosts().at(-1),replay);
''')

    def test_marker_cannot_clear_accepted_or_unknown_run_receipt(self):
        self.javascript(r'''
await createJob();await dbJobsRun();jobRuns[0].status='unknown';await dbJobsObserve();const accepted=p.run;assert.equal(accepted.id,'run-1');assert.equal(accepted.status,'unknown');
const originalRoute=route;route=async(path,options)=>path.startsWith('/api/v1/jobs/runs/run-1')?{http:400,body:{code:'JOB_INPUT_REJECTED',details:{admission:'NOT_ATTEMPTED'}}}:originalRoute(path,options);
await dbJobsObserve();assert.equal(p.run,accepted);assert.equal(p.run.id,'run-1');assert.equal(p.run.status,'unknown');assert.equal($('db-jobs-run').disabled,true);
''')

    def test_typed_save_cas_run_inputs_and_accepted_editing(self):
        self.javascript(r'''
await openJobs();$('db-jobs-name').value='daily';
$('db-jobs-parameters').value=JSON.stringify({n:{type:'integer',required:true},flag:{type:'boolean',required:true},day:{type:'date',required:true},word:{type:'string',required:true}});
$('db-jobs-steps').value=JSON.stringify([{name:'q',action:'sql',sql:'SELECT {{n}} AS n, {{flag}} AS enabled, {{day}} AS day, {{word}} AS word',mode:'read-only'}]);
await dbJobsSave();let save=JSON.parse(jobPosts()[0].options.body);assert.equal(save.expected_revision,0);assert.equal(save.job_id,undefined);assert.equal(save.definition.parameters.n.type,'integer');assert.equal(save.context.selected_role,'admin');
assert.equal($('db-jobs-owner').textContent.includes('v1'),false);assert.match($('db-jobs-owner').textContent,/Revision: 1/);
$('db-jobs-arguments').value=JSON.stringify({n:5,flag:true,day:'2026-10-21',word:'hello'});
$('db-jobs-add-input').onclick();p.files[0].name.value='source';p.files[0].file.files=[{name:'data.csv',arrayBuffer:async()=>new Uint8Array([0,255,10]).buffer}];
await dbJobsRun();const run=JSON.parse(jobPosts().at(-1).options.body),original=structuredClone(run.context);assert.equal(run.expected_revision,1);assert.equal(run.arguments.flag,true);assert.deepEqual(run.inputs,{source:{filename:'data.csv',content_base64:'AP8K'}});assert.equal(p.run.status,'running');
$('db-jobs-description').value='new description';await dbJobsSave();save=JSON.parse(jobPosts().at(-1).options.body);assert.equal(save.job_id,'job-1');assert.equal(save.expected_revision,1);assert.equal(p.job.revision,2);assert.equal(p.run.revision,1);assert.deepEqual(p.run.context,original);
await dbJobsRun();assert.equal(jobPosts().filter(c=>c.path.endsWith('/runs')).length,1);
assert.ok($('db-jobs-run-detail').descendants().some(n=>n.textContent===': child-1'));assert.equal($('query-results').rows,undefined);
''')

    def test_save_conflict_and_mismatched_receipt_retain_draft_revision_key(self):
        self.javascript(r'''
await createJob();$('db-jobs-description').value='retain draft';saveFailure='REVISION_CONFLICT';
await dbJobsSave();const first=jobPosts().at(-1);await dbJobsSave();const second=jobPosts().at(-1);assert.equal(first.options.body,second.options.body);assert.equal(first.options.headers['Idempotency-Key'],second.options.headers['Idempotency-Key']);assert.equal(p.job.revision,1);assert.equal($('db-jobs-description').value,'retain draft');assert.equal($('db-jobs-save').disabled,false);
saveFailure=null;const originalRoute=route;route=async(path,options)=>{const r=await originalRoute(path,options);if(path==='/api/v1/jobs'&&options?.body)r.context={...r.context,grant_revision:999};return r;};
await dbJobsSave();assert.equal(p.job.revision,1);assert.equal($('db-jobs-description').value,'retain draft');
''')

    def test_periodic_refresh_keeps_focus_selection_and_independent_drafts(self):
        self.javascript(r'''
await createJob();$('db-jobs-steps').value='[draft in progress';$('db-jobs-steps').oninput();$('db-jobs-steps').focus();$('db-jobs-steps').setSelectionRange(3,8);const textarea=$('db-jobs-steps');
await periodicRefresh();assert.equal($('db-jobs-steps'),textarea);assert.equal(document.activeElement,textarea);assert.equal(textarea.value,'[draft in progress');assert.equal(textarea.selectionStart,3);assert.equal(p.database,'db');assert.equal(p.job.id,'job-1');
$('db-jobs-new').onclick();$('db-jobs-name').value='another';$('db-jobs-name').oninput();
$('db-jobs-list').value='job-1';$('db-jobs-list').onchange();await dbJobsLoad();assert.equal($('db-jobs-steps').value,'[draft in progress');
$('db-jobs-new').onclick();assert.equal($('db-jobs-name').value,'another');assert.equal(jobPosts().length,1);
$('worksheet-tabs').querySelectorAll('[role=tab]').find(n=>n.dataset.worksheet==='b').onclick();await tick();assert.equal(p.database,'db');assert.equal(p.schema,'schema');assert.equal(worksheets.length,2);
''')

    def test_response_loss_same_payload_key_double_click_and_unknown_reconcile(self):
        self.javascript(r'''
await createJob();runFailure=true;runGate=deferred();$('db-jobs-arguments').value='{"n":1}';const first=dbJobsRun();await tick();await dbJobsRun();assert.equal(jobPosts().filter(c=>c.path.endsWith('/runs')).length,1);runGate.resolve();await first;runGate=null;
assert.equal(p.run.status,'unknown');const original=jobPosts().at(-1);$('db-jobs-arguments').value='{"n":9}';await dbJobsRun();assert.equal(jobPosts().at(-1),original);
runFailure=false;await dbJobsObserve();const replay=jobPosts().at(-1);assert.equal(replay.options.body,original.options.body);assert.equal(replay.options.headers['Idempotency-Key'],original.options.headers['Idempotency-Key']);assert.equal(p.run.id,'run-1');assert.equal(p.run.payload,null);
jobRuns[0].status='unknown';await dbJobsObserve();assert.equal(p.run.status,'unknown');await dbJobsRun();assert.equal(jobPosts().filter(c=>c.path.endsWith('/runs')).length,2);await dbJobsObserve('reconcile');assert.equal(p.run.status,'failed');assert.equal(jobPosts().at(-1).path,'/api/v1/jobs/runs/run-1/reconcile');assert.equal(jobPosts().filter(c=>c.path.endsWith('/runs')).length,2);
''')

    def test_grant_loss_late_acceptance_purges_bytes_and_original_cancel(self):
        self.javascript(r'''
await createJob();$('db-jobs-add-input').onclick();p.files[0].name.value='s';p.files[0].file.files=[{name:'private.csv',arrayBuffer:async()=>new Uint8Array([42]).buffer}];runGate=deferred();const running=dbJobsRun();await tick();const original=structuredClone(p.run.context);
inventory=[];await dbRefreshAccess();assert.equal(p.invalid,true);assert.equal(p.run.payload,null);assert.equal(p.run.details,null);assert.equal(p.drafts.size,0);assert.equal($('db-jobs-name').value,'');
runGate.resolve();await running;assert.equal(p.run.id,'run-1');assert.equal(p.run.details,null);assert.equal(p.run.purged,true);assert.equal($('db-jobs-cancel').disabled,false);
currentRole='bi_viewer';dbApplyRole();await dbJobsObserve('cancel');const cancel=jobPosts().at(-1);assert.equal(cancel.path,'/api/v1/jobs/runs/run-1/cancel');assert.equal(cancel.options.headers['X-Shirokuma-Role'],'admin');assert.deepEqual(JSON.parse(cancel.options.body),{context:original});assert.equal(p.run.status,'canceled');assert.equal(p.run.details,null);
''')

    def test_owner_saved_role_viewer_and_legacy_gates(self):
        self.javascript(r'''
await createJob();p.job.owner='another';dbJobsRender();assert.equal($('db-jobs-save').disabled,true);await dbJobsSave();await dbJobsRun();assert.equal(jobPosts().length,1);
p.job.owner='owner';p.job.selected_role='data_engineer';await dbJobsRun();assert.equal(jobPosts().length,1);
p.job.selected_role='admin';schemas[0].capabilities.job_sql=false;await dbJobsRefreshAuthorization();assert.equal(p.invalid,true);assert.equal(p.job,null);assert.equal($('db-jobs-steps').value,'');
await assert.rejects(()=>api('/api/jobs'));await assert.rejects(()=>api('/api/jobs/schedules'));assert.equal(calls.some(c=>c.path==='/api/jobs'||c.path==='/api/jobs/schedules'),false);
currentRole='bi_viewer';dbApplyRole();await dbJobsRefresh();assert.equal(p.invalid,true);assert.equal($('db-jobs-run').disabled,true);assert.equal($('db-jobs-save').disabled,true);
setLanguage('ja');assert.equal($('db-jobs-save').textContent,'Job を保存');assert.equal($('db-jobs-steps').rows,12);assert.equal($('db-jobs-status').getAttribute('role'),'status');
''')

    def test_same_name_database_ids_explorer_and_context_drafts(self):
        self.javascript(r'''
inventory.push({...inventory[0],id:'finance',display_name:'Sales',sql_catalog:'finance',binding_revision:2,generation_id:'fin',grant_revision:9});
schemasRoute=async path=>({items:path.includes('/finance/')?[{...schemas[0],id:'fin-schema',database_id:'finance'}]:schemas});
await openJobs();$('db-jobs-name').value='sales_draft';$('db-jobs-name').oninput();
assert.deepEqual($('db-jobs-database').options.map(o=>o.value),['','db','finance']);
$('db-jobs-database').value='finance';await $('db-jobs-database').onchange();$('db-jobs-schema').value='fin-schema';await $('db-jobs-schema').onchange();assert.equal(p.context.database_id,'finance');assert.equal(dbCurrent().database_id,'db');assert.equal(dbExplorer.database_id,'db');
$('db-jobs-name').value='finance_draft';$('db-jobs-name').oninput();$('db-jobs-database').value='db';await $('db-jobs-database').onchange();$('db-jobs-schema').value='schema';await $('db-jobs-schema').onchange();assert.equal($('db-jobs-name').value,'sales_draft');assert.equal(jobPosts().length,0);
''')

    def test_late_definition_and_history_do_not_restore_after_role_or_binding_loss(self):
        self.javascript(r'''
await createJob();loadGate=deferred();$('db-jobs-list').value='job-1';const loading=dbJobsLoad();await tick();inventory[0].binding_revision=2;inventory[0].generation_id='changed';await dbRefreshAccess();loadGate.resolve();await loading;assert.equal(p.job,null);assert.equal(p.jobs.length,0);assert.equal(p.history.length,0);assert.equal($('db-jobs-name').value,'');
''')

    def test_accepted_run_keeps_original_context_across_panel_selection(self):
        self.javascript(r'''
await createJob();await dbJobsRun();const original=structuredClone(p.run.context);
$('db-jobs-schema').value='raw';await $('db-jobs-schema').onchange();assert.equal(p.context.schema_id,'raw');assert.equal(p.run.context.schema_id,'schema');
await dbJobsObserve();await dbJobsObserve('cancel');const posts=jobPosts();assert.deepEqual(JSON.parse(posts.at(-1).options.body),{context:original});assert.equal(posts.at(-1).options.headers['X-Shirokuma-Role'],'admin');assert.equal(p.run.status,'canceled');assert.equal(jobPosts().filter(c=>c.path.endsWith('/runs')).length,1);
''')

    def test_identity_loss_clears_handles_and_engineer_cannot_save_admin_steps(self):
        self.javascript(r'''
await createJob();await dbJobsRun();session.username='different';await dbRefreshAccess();assert.equal(p.run,null);assert.equal(p.job,null);assert.equal(p.drafts.size,0);assert.equal($('db-jobs-run-detail').children.length,0);assert.equal($('db-jobs-cancel').disabled,true);
p.role='data_engineer';p.database='db';p.schema='schema';await dbJobsRefresh();$('db-jobs-name').value='export_job';$('db-jobs-steps').value=JSON.stringify([{name:'export',action:'table_export',source:{database_id:'db',schema_id:'schema',object_id:'o',incarnation:1},format:'csv'}]);
const before=jobPosts().length;await dbJobsSave();assert.equal(jobPosts().length,before);assert.match($('db-jobs-status').textContent,/inputs are invalid/);
''')

    def test_db_scoped_saved_job_load_uses_its_saved_schema_with_current_epoch(self):
        self.javascript(r'''
await createJob();const savedContext=structuredClone(p.context);$('db-jobs-schema').value='raw';await $('db-jobs-schema').onchange();assert.equal(p.schema,'raw');
$('db-jobs-list').value='job-1';await dbJobsLoad();assert.equal(p.schema,'schema');assert.equal(p.context.schema_id,'schema');
const read=calls.filter(c=>c.path.startsWith('/api/v1/jobs/job-1?')).at(-1);assert.deepEqual(JSON.parse(new URL('http://local'+read.path).searchParams.get('context')),savedContext);assert.equal(dbCurrent().schema_id,'schema');
''')

    def test_definition_edits_block_handler_and_button_but_arguments_do_not(self):
        self.javascript(r'''
await createJob();const original=Object.fromEntries(dbJobsFields.map(k=>[k,$('db-jobs-'+k).value]));
for(const [field,value] of Object.entries({name:'renamed',description:'changed',parameters:'{"n":{"type":"integer"}}',steps:'[{"name":"q","action":"sql","sql":"SELECT 2","mode":"read-only"}]'})){
 $('db-jobs-'+field).value=value;$('db-jobs-'+field).oninput();assert.equal($('db-jobs-run').disabled,true);await dbJobsRun();assert.equal(jobPosts().length,1);assert.match($('db-jobs-status').textContent,/Save or load/);
 $('db-jobs-'+field).value=original[field];$('db-jobs-'+field).oninput();assert.equal($('db-jobs-run').disabled,false);
}
$('db-jobs-arguments').value='{"n":5}';$('db-jobs-arguments').oninput();assert.equal($('db-jobs-run').disabled,false);await dbJobsRun();assert.deepEqual(JSON.parse(jobPosts().at(-1).options.body).arguments,{n:5});
const envelope=structuredClone(p.run);$('db-jobs-description').value='revision two';await dbJobsSave();assert.equal(p.job.revision,2);assert.deepEqual(p.run,envelope);
''')

    def test_explicit_current_revision_recovers_stale_cas_without_post(self):
        self.javascript(r'''
await createJob();$('db-jobs-description').value='my draft';$('db-jobs-description').oninput();
savedJobs[0]=structuredClone(savedJobs[0]);savedJobs[0].revision=2;savedJobs[0].definition.description='server revision';saveFailure='REVISION_CONFLICT';await dbJobsSave();const oldKey=jobPosts().at(-1).options.headers['Idempotency-Key'];
const failedSave=p.save;assert.equal($('db-jobs-status').textContent,'Job request failed. Draft retained.');dbJobsRender();assert.equal($('db-jobs-status').textContent,'Job request failed. Draft retained.');
await periodicRefresh();assert.equal(p.job.revision,1);assert.equal($('db-jobs-description').value,'my draft');assert.equal(p.save,failedSave);assert.equal(p.save.key,oldKey);assert.equal(p.save.payload.expected_revision,1);assert.equal($('db-jobs-status').textContent,'Job request failed. Draft retained.');assert.equal($('db-jobs-run').disabled,true);
$('db-jobs-list').value='job-1';await dbJobsLoad();assert.equal(p.job.revision,1);assert.equal($('db-jobs-description').value,'my draft');
assert.equal(p.save,failedSave);assert.equal($('db-jobs-status').textContent,'Save or load the definition before running.');
const other={...structuredClone(savedJobs[0]),id:'job-2',definition:{...savedJobs[0].definition,name:'other'}};p.jobs.push(other);
const before=jobPosts().length;await $('db-jobs-reload').onclick();assert.equal(jobPosts().length,before);assert.equal(p.job.revision,2);assert.equal($('db-jobs-description').value,'server revision');assert.equal(p.save,null);
assert.equal(p.selection,'job-1');assert.equal($('db-jobs-list').value,'job-1');assert.equal($('db-jobs-list').options.find(o=>o.value==='job-1').textContent,'daily · v2');assert.match($('db-jobs-owner').textContent,/Revision: 2/);assert.equal($('db-jobs-status').textContent,'Ready');assert.equal($('db-jobs-run').disabled,false);assert.equal(p.jobs.find(j=>j.id==='job-2'),other);
dbJobsRender();await periodicRefresh();assert.equal($('db-jobs-status').textContent,'Ready');assert.equal($('db-jobs-list').options.find(o=>o.value==='job-1').textContent,'daily · v2');
saveFailure=null;$('db-jobs-description').value='retry';await dbJobsSave();assert.equal(JSON.parse(jobPosts().at(-1).options.body).expected_revision,2);assert.notEqual(jobPosts().at(-1).options.headers['Idempotency-Key'],oldKey);assert.equal(p.job.revision,3);
''')

    def test_late_latest_load_cannot_replace_changed_selection_or_context(self):
        for change in ('selection', 'context'):
            with self.subTest(change=change):
                self.javascript(r'''
await createJob();$('db-jobs-description').value='retained draft';$('db-jobs-description').oninput();
savedJobs[0]=structuredClone(savedJobs[0]);savedJobs[0].revision=2;savedJobs[0].definition.description='late revision';
const other={...structuredClone(savedJobs[0]),id:'job-2',definition:{...savedJobs[0].definition,name:'other'}};p.jobs.push(other);
loadGate=deferred();const loading=dbJobsLoad(true);await tick();
if(CHANGE==='selection'){$('db-jobs-list').value='job-2';$('db-jobs-list').onchange();}
else{$('db-jobs-schema').value='raw';await $('db-jobs-schema').onchange();$('db-jobs-name').value='new context draft';$('db-jobs-name').oninput();}
const selected=p.selection,context=p.context,job=p.job,jobs=p.jobs,status=$('db-jobs-status').textContent,name=$('db-jobs-name').value,description=$('db-jobs-description').value,posts=jobPosts().length;
loadGate.resolve();await loading;
assert.equal(p.selection,selected);assert.equal(p.context,context);assert.equal(p.job,job);assert.equal(p.jobs,jobs);assert.equal($('db-jobs-status').textContent,status);assert.equal($('db-jobs-name').value,name);assert.equal($('db-jobs-description').value,description);assert.equal(jobPosts().length,posts);assert.ok([...p.drafts.values()].some(d=>d.values.description==='retained draft'));
'''.replace('CHANGE', json.dumps(change)))

    def test_cas_visible_status_with_text_node_bindings_in_both_languages(self):
        for language in ('en', 'ja'):
            with self.subTest(language=language):
                self.javascript(r'''
await createJob();
// Model shipped ui(): the status element renders a Text child, and only that child owns its binding.
const setBinding=uiBindings.set.bind(uiBindings);uiBindings.delete(p.status);
Object.defineProperty(p.status,'textContent',{configurable:true,get(){return this.children[0]?.textContent||'';},set(value){this.children[0].textContent=value;}});
uiBindings.set=function(node,binding){
 if(node===p.status){for(const child of node.children)child.isConnected=false;const text={nodeType:3,textContent:'',isConnected:true,descendants:()=>[]};node.children=[text];return setBinding(text,binding);}
 return setBinding(node,binding);
};
setLanguage(LANGUAGE);
const hint='Save or load the definition before running.',failure='Job request failed. Draft retained.';
for(const neutral of ['Ready','Job saved.']){
 ui(p.status,neutral);$('db-jobs-description').value='dirty';$('db-jobs-description').oninput();
 assert.equal(uiBindings.has(p.status),false);assert.equal(uiBindings.get(p.status.children[0]).key,hint);assert.equal(p.status.textContent,t(hint));assert.equal($('db-jobs-run').disabled,true);
 $('db-jobs-description').value='';$('db-jobs-description').oninput();assert.equal(p.status.textContent,t('Ready'));
}
$('db-jobs-description').value='keep draft';$('db-jobs-description').oninput();
savedJobs[0]=structuredClone(savedJobs[0]);savedJobs[0].revision=2;savedJobs[0].definition.description='current revision';saveFailure='REVISION_CONFLICT';
await dbJobsSave();const frozen=p.save;assert.equal(p.status.textContent,t(failure));dbJobsRender();await periodicRefresh();assert.equal(p.status.textContent,t(failure));assert.equal(p.save,frozen);assert.equal(p.job.revision,1);assert.equal($('db-jobs-description').value,'keep draft');
const before=jobPosts().length;await $('db-jobs-reload').onclick();assert.equal(jobPosts().length,before);assert.equal(p.status.textContent,t('Ready'));assert.equal(uiBindings.has(p.status),false);assert.equal(p.selection,'job-1');assert.equal($('db-jobs-list').value,'job-1');assert.equal($('db-jobs-list').options.find(o=>o.value==='job-1').textContent,'daily · v2');assert.equal(p.job.revision,2);assert.equal($('db-jobs-description').value,'current revision');assert.match($('db-jobs-owner').textContent,new RegExp(t('Revision')+': 2'));assert.equal($('db-jobs-run').disabled,false);
dbJobsRender();await periodicRefresh();assert.equal(p.status.textContent,t('Ready'));
'''.replace('LANGUAGE', json.dumps(language)))

    def test_original_role_revoked_while_other_database_role_selected(self):
        self.javascript(r'''
inventory.push({...inventory[0],id:'finance',sql_catalog:'finance'});
schemasRoute=async path=>({items:schemas.map(s=>({...s,database_id:path.includes('/finance/')?'finance':'db'}))});
await createJob();await dbJobsRun();const original=structuredClone(p.run.context);
$('db-jobs-role').value='data_engineer';await $('db-jobs-role').onchange();$('db-jobs-database').value='finance';await $('db-jobs-database').onchange();assert.equal(p.run.purged,false);assert.equal(p.schema,'');assert.ok(p.schemas.length);
$('db-jobs-schema').value='schema';await $('db-jobs-schema').onchange();assert.equal(p.invalid,false);
let revoked=false;inventoryRoute=async(path,options)=>({items:structuredClone(options.headers['X-Shirokuma-Role']==='admin'&&revoked?inventory.filter(d=>d.id!=='db'):inventory)});
revoked=true;await dbJobsRefreshAuthorization();assert.equal(p.invalid,false);assert.equal(p.run.purged,true);assert.equal(p.run.details,null);assert.equal(p.run.payload,null);assert.equal(p.run.id,'run-1');
await dbJobsObserve('cancel');assert.deepEqual(JSON.parse(jobPosts().at(-1).options.body),{context:original});assert.equal(jobPosts().at(-1).options.headers['X-Shirokuma-Role'],'admin');
''')

    def test_late_original_schema_response_cannot_restore_revoked_run(self):
        self.javascript(r'''
await createJob();await dbJobsRun();$('db-jobs-role').value='data_engineer';await $('db-jobs-role').onchange();
const gate=deferred();let delayed=true;schemasRoute=async(path,options)=>{const items=structuredClone(schemas);if(options.headers['X-Shirokuma-Role']==='admin'&&delayed){delayed=false;await gate.promise;}return {items};};
const refreshing=dbJobsRefreshAuthorization();await tick();assert.equal(delayed,false);
schemas[0].capabilities.job_sql=false;await dbJobsRefreshAuthorization();assert.equal(p.run.purged,true);gate.resolve();await refreshing;assert.equal(p.run.purged,true);assert.equal(p.run.details,null);assert.equal(p.run.payload,null);assert.deepEqual(p.run.schemas,[]);assert.equal($('db-jobs-observe').disabled,true);
''')

    def test_late_original_run_details_stay_purged_while_panel_elsewhere(self):
        self.javascript(r'''
await createJob();await dbJobsRun();$('db-jobs-role').value='data_engineer';await $('db-jobs-role').onchange();
const gate=deferred(),originalRoute=route;route=async(path,options)=>{const response=await originalRoute(path,options);if(path.startsWith('/api/v1/jobs/runs/run-1?'))await gate.promise;return response;};
const observing=dbJobsObserve();await tick();
inventoryRoute=async(path,options)=>({items:structuredClone(options.headers['X-Shirokuma-Role']==='admin'?[]:inventory)});
await dbJobsRefreshAuthorization();assert.equal(p.run.purged,true);gate.resolve();await observing;assert.equal(p.run.id,'run-1');assert.equal(p.run.details,null);assert.equal(p.run.payload,null);assert.equal($('db-jobs-cancel').disabled,false);
''')


if __name__ == '__main__':
    unittest.main()
