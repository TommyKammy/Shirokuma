"""Focused refresh request contracts, executed in Node without browser/services."""
import json
import subprocess
import unittest

from test_jobs_ui import SCRIPT, function


class WorkbenchRefreshTests(unittest.TestCase):
    def javascript(self, body, *names, prelude=''):
        source = prelude + r'''
const assert=require('node:assert/strict');
class Node {
 constructor(){this.value='';this.textContent='';this.className='';this.dataset={};this.children=[];}
 replaceChildren(...nodes){this.children=nodes;} append(...nodes){this.children.push(...nodes);}
 querySelectorAll(){return [];}
}
const nodes={},$=id=>nodes[id]??=new Node(),document={createElement:()=>new Node()};
const ui=(node,key)=>{node.textContent=key;},t=x=>x,message=(id,value)=>{$(id).textContent=value;};
const pruneUIBindings=()=>{},table=()=>{},renderComputeSummary=()=>{};
const settle=()=>new Promise(resolve=>setImmediate(resolve));
''' + '\n(async()=>{\n' + '\n'.join(function(name) for name in names) + '\n' + body + '\n})().catch(e=>{console.error(e);process.exitCode=1;});'
        result = subprocess.run(['node', '-e', source], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_status_inflight_context_failure_and_force(self):
        self.javascript(r'''
let requests=[],auto=[];
const api=()=>new Promise((resolve,reject)=>requests.push({resolve,reject}));
const updateAutoChill=data=>auto.push(data);
const report=state=>({warehouse:{state},components:{},auto_chill:state});
let a=refreshStatus(),b=refreshStatus();assert.equal(a,b);assert.equal(requests.length,1);
requests[0].resolve(report('active'));await a;assert.equal(auto.at(-1),'active');
a=refreshStatus();assert.equal(requests.length,2,'settled results are not cached');
invalidateStatus();currentRole='bi_viewer';b=refreshStatus();
requests[2].resolve(report('chilled'));await b;requests[1].resolve(report('starting'));await a;
assert.equal(auto.at(-1),'chilled');
// Away and back to the same role still changes the context epoch.
a=refreshStatus();invalidateStatus();currentRole='admin';invalidateStatus();currentRole='bi_viewer';
b=refreshStatus();requests[4].resolve(report('active'));await b;
requests[3].reject(Error('old context'));await a;assert.equal($('status-error').textContent,'');
// Session changes cannot reuse or render the previous session request.
a=refreshStatus();catalogSession='new-user';b=refreshStatus();assert.equal(requests.length,7);
requests[6].resolve(report('chilled'));await b;requests[5].resolve(report('active'));await a;
assert.equal(auto.at(-1),'chilled');
a=refreshStatus();requests[7].reject(Error('offline'));await a;assert.equal($('status-error').textContent,'offline');
b=refreshStatus();requests[8].resolve(report('active'));await b;assert.equal($('status-error').textContent,'');
// A mutation refresh must bypass earlier in-flight work and ignore its late response.
a=refreshStatus();b=refreshStatus({force:true});assert.equal(requests.length,11);
requests[10].resolve(report('chilled'));await b;requests[9].resolve(report('active'));await a;
assert.equal(auto.at(-1),'chilled');assert.equal(statusPending,null);
''', 'invalidateStatus', 'refreshStatus', prelude="let statusEpoch=0,statusPending=null,currentRole='admin',catalogSession='user';")

    def test_warehouse_mutations_force_status(self):
        wake = next(line for line in SCRIPT.splitlines() if line.startswith("for(const action of ['chill','wake'])"))
        quick = next(line for line in SCRIPT.splitlines() if line.startswith("for(const action of ['wake','chill'])"))
        auto = next(line for line in SCRIPT.splitlines() if line.startswith("$('save-auto-chill').onclick="))
        self.javascript('const HANDLERS=' + json.dumps(wake + '\n' + quick + '\n' + auto) + r''';
const busy=async(node,task)=>task();let calls=[];
const invalidateStatus=()=>calls.push('invalidate'),updateAutoChill=()=>{};
const api=async path=>{calls.push(path);return {};};
const refreshStatus=async options=>{assert.equal(options.force,true);calls.push('status');};
eval(HANDLERS);
for(const id of ['wake','chill','quick-wake','quick-chill','save-auto-chill']){
 calls=[];await $(id).onclick();assert.deepEqual(calls,['invalidate',id==='save-auto-chill'?'/api/warehouse/autochill':'/api/warehouse/'+id.replace('quick-',''),'status']);
}
''')

    def test_jobs_one_schedule_read_and_selection_fences(self):
        self.javascript(r'''
let calls=[],pendingJobs=null,pendingRuns=null,scheduleModes=[],details=[];
const api=async(path,body)=>{
 calls.push(path);
 if(path==='/api/jobs')return pendingJobs?new Promise(resolve=>pendingJobs.resolve=resolve):{jobs:[{name:'job'}]};
 if(path==='/api/jobs/runs')return pendingRuns?new Promise(resolve=>pendingRuns.resolve=resolve):{runs:[{id:'latest',status:'succeeded',steps:[]}]};
 throw Error('unexpected '+path);
};
const jobsVisible=()=>true,stopJobsPolling=()=>{},scheduleJobsPolling=()=>{},renderJobList=()=>{},renderJobRuns=()=>{};
const selectJob=entry=>{selectedJob=entry;},clearSelectedJob=()=>{selectedJob=null;};
const loadJobSchedules=async(preserve=false)=>{calls.push('/api/jobs/schedules');scheduleModes.push(preserve);};
const loadJobAlerts=async()=>{};
const showJobRun=async(id,run)=>details.push({id,run});
await loadJobs();assert.equal(calls.filter(p=>p==='/api/jobs/schedules').length,1);assert.deepEqual(scheduleModes,[false]);
await refreshJobRuns();assert.deepEqual(scheduleModes,[false,true]);assert.equal(details.at(-1).run.id,'latest');
selectedRunId='older';await refreshJobRuns();assert.equal(details.at(-1).run,undefined,'older runs require a fresh detail request');
pendingRuns={};let task=refreshJobRuns();selectedRunId='other';pendingRuns.resolve({runs:[{id:'older'}]});await task;
assert.equal(details.at(-1).id,'older','late history cannot select the previous run');pendingRuns=null;
pendingJobs={};task=loadJobs();currentRole='bi_viewer';++stageEpoch;currentRole='admin';
let reads=calls.length;pendingJobs.resolve({jobs:[{name:'stale'}]});await task;assert.equal(calls.length,reads);assert.equal(jobEntries[0].name,'job');
pendingJobs={};task=loadJobs();++jobSelectionEpoch;pendingJobs.resolve({jobs:[]});await task;assert.equal(jobEntries[0].name,'job');
''', 'loadJobs', 'refreshJobRuns', prelude="let currentRole='admin',stageEpoch=0,jobsRequest=0,jobListRequest=0,jobSelectionEpoch=0,jobDetailRequest=0,jobEntries=[],selectedJob=null,selectedRunId='latest',jobRunCache=[],jobRunState='loading';")

    def test_header_actions_ignore_delayed_status_before_and_during_mutation(self):
        handler = next(line for line in SCRIPT.splitlines() if line.startswith("for(const action of ['wake','chill'])"))
        self.javascript('const HANDLER=' + json.dumps(handler) + r''';
const busy=async(node,task)=>task();
let requests=[],mutation,rendered=[];
const api=path=>{
 if(path==='/api/status')return new Promise(resolve=>requests.push(resolve));
 assert.ok(['/api/warehouse/wake','/api/warehouse/chill'].includes(path));
 return new Promise(resolve=>{mutation=resolve;});
};
const updateAutoChill=state=>rendered.push(state);
const report=state=>({warehouse:{state},components:{},auto_chill:state});
eval(HANDLER);
for(const action of ['wake','chill']){
 requests=[];rendered=[];
 const state=action==='wake'?'active':'chilled',oldState=action==='wake'?'chilled':'active';
 const before=refreshStatus(),clicked=$('quick-'+action).onclick();
 requests[0](report(oldState));await before;
 assert.deepEqual(rendered,[],'pre-action status cannot render during the mutation');
 const during=refreshStatus();
 mutation({});await settle();
 assert.equal(requests.length,3,'successful action must fetch status after mutation, bypassing in-flight work');
 requests[2](report(state));await clicked;
 assert.deepEqual(rendered,[state]);assert.equal(JSON.parse($('compute-warehouse').textContent).state,state);
 requests[1](report(oldState));await during;
 assert.deepEqual(rendered,[state],'late status cannot overwrite post-action state');
 assert.equal(JSON.parse($('compute-warehouse').textContent).state,state);
}
''', 'invalidateStatus', 'refreshStatus', prelude="let statusEpoch=0,statusPending=null,currentRole='admin',catalogSession='user';")

    def test_job_list_reload_survives_run_polling_and_latest_list_wins(self):
        start = SCRIPT.index("$('job-form').onsubmit=")
        handler = SCRIPT[start:SCRIPT.index("$('job-run-form').onsubmit=", start)]
        self.javascript('const HANDLER=' + json.dumps(handler) + r''';
const entry=revision=>({name:'job',can_manage:true,definition:{description:revision,parameters:{},steps:[]}});
jobEntries=[entry('old')];selectedJob=jobEntries[0];
let lists=[],rendered=[],opened=[],saveTask;
const api=async path=>{
 if(path==='/api/jobs/save')return {};
 if(path==='/api/jobs')return new Promise(resolve=>lists.push(resolve));
 if(path==='/api/jobs/runs')return {runs:[]};
 throw Error('unexpected '+path);
};
const busy=(node,task)=>saveTask=task(),syncJobSteps=()=>{};
const jobsVisible=()=>true,stopJobsPolling=()=>{},scheduleJobsPolling=()=>{},renderJobRuns=()=>{};
const renderJobList=()=>rendered.push(jobEntries[0]?.definition.description);
const selectJob=entry=>{selectedJob=entry;},clearSelectedJob=()=>{selectedJob=null;};
const openJob=entry=>{opened.push(entry.definition.description);selectJob(entry);};
const loadJobSchedules=async()=>{},loadJobAlerts=async()=>{};
$('job-name').value='job';$('job-parameters').value='{}';
eval(HANDLER);
$('job-form').onsubmit({preventDefault(){}});await settle();
assert.equal(lists.length,1);
await refreshJobRuns();assert.equal(rendered.at(-1),'old');
lists[0]({jobs:[entry('saved')]});await saveTask;
assert.equal(jobEntries[0].definition.description,'saved');assert.equal(rendered.at(-1),'saved');
assert.deepEqual(opened,['saved'],'post-save handler must reopen the newly fetched definition');
// Independent list reloads retain their own latest-response fence.
const first=loadJobs(),second=loadJobs();
lists[2]({jobs:[entry('newest')]});await second;
const renders=rendered.length;
lists[1]({jobs:[entry('superseded')]});await first;
assert.equal(rendered.length,renders);assert.equal(rendered.at(-1),'newest');
assert.equal(jobEntries[0].definition.description,'newest');assert.equal(selectedJob.definition.description,'newest');
''', 'loadJobs', 'refreshJobRuns', prelude="let currentRole='admin',stageEpoch=0,jobsRequest=0,jobListRequest=0,jobSelectionEpoch=0,jobDetailRequest=0,jobEntries=[],jobSteps=[],selectedJob=null,selectedRunId=null,jobRunCache=[],jobRunState='loading';")

    def test_detail_uses_only_supplied_fresh_run_and_falls_back(self):
        self.javascript(r'''
let reads=0;
const api=async(path,body)=>{assert.equal(path,'/api/jobs/detail');reads++;return {run:{id:body.run_id,status:'active',steps:[]}};};
const jobsVisible=()=>true,renderJobRuns=()=>{},jobStatus=x=>x,statusTone=()=>'',jobEmpty=()=>{};
const liveJobStates=new Set(['active']);
const jobRunCache=[{id:'old',status:'stale'}];
await showJobRun('fresh',{id:'fresh',status:'succeeded',steps:[]});assert.equal(reads,0);
await showJobRun('old');assert.equal(reads,1,'independent selection must ignore the old cache');
await showJobRun('outside',undefined);assert.equal(reads,2);
''', 'showJobRun', prelude="let currentRole='admin',stageEpoch=0,jobDetailRequest=0,selectedRunId=null,stageExportRunId=null;")

    def test_stage_save_reconciles_once_with_fresh_permissions_and_revision(self):
        start = SCRIPT.index("$('stage-form').onsubmit=")
        handler = SCRIPT[start:SCRIPT.index("$('stage-files-refresh')", start)]
        self.javascript('const HANDLER=' + json.dumps(handler) + r''';
let calls=[],updated=null,invalidations=0,cleared=0,stageEntries=[],selectedStage=null,stageFiles=[],stageDirty=false;
const saved={name:'stage',updated_at:1,can_manage:true,owner:'alice',definition:{table:'old'}};
const stageCurrent=epoch=>epoch===stageEpoch&&currentRole==='admin';
const stageTask=async(label,task)=>task(stageEpoch),stageDefinition=()=>({name:'stage'});
const invalidateStagePreview=()=>invalidations++,clearStageExports=()=>cleared++;
const editStage=entry=>{selectedStage=entry;stageFiles=[];invalidateStagePreview();clearStageExports();};
const renderStages=()=>{},renderStageSchedule=()=>{},fillScheduleFields=()=>{};
const api=async(path,body)=>{calls.push({path,body});
 if(path==='/api/stages/save')return {stage:saved};
 if(path==='/api/stages')return {stages:[updated]};
 if(path==='/api/stages/files')return {files:[]};throw Error(path);
};
eval(HANDLER);
const original=$('stage-form').onsubmit;
// stageTask calls the callback synchronously up to its first await; settle drains the chain.
updated={...saved,updated_at:2,definition:{table:'new'},owner:'bob',can_manage:false};
original({preventDefault(){}});await settle();
assert.deepEqual(calls.map(c=>c.path),['/api/stages/save','/api/stages']);
assert.equal(selectedStage.owner,'bob');assert.equal(selectedStage.can_manage,false);assert.equal($('stage-table').value,'new');
assert.ok(invalidations>=2);assert.ok(cleared>=2);
calls=[];updated={...saved,updated_at:3,definition:{table:'latest'}};
original({preventDefault(){}});await settle();
assert.deepEqual(calls.map(c=>c.path),['/api/stages/save','/api/stages','/api/stages/files']);
assert.equal(selectedStage.updated_at,3);assert.equal($('stage-table').value,'latest');
''', 'refreshStageSchedule', 'fetchStageFiles', prelude="let currentRole='admin',stageEpoch=1;")
