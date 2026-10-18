"""Jobs UI contracts without a live warehouse or browser."""
import json
from html.parser import HTMLParser
from pathlib import Path
import re
import subprocess
import unittest

HTML = (Path(__file__).resolve().parents[1] / 'tools/local_experiment/workbench/index.html').read_text()
SCRIPT = re.search(r'<script>([\s\S]*?)</script>', HTML)[1]


def function(name):
    start = SCRIPT.index('function ' + name + '(')
    if SCRIPT[max(0, start - 6):start] == 'async ':
        start -= 6
    match = re.search(r'\n(?:async )?function ', SCRIPT[start + 1:])
    source = SCRIPT[start:start + 1 + match.start()] if match else SCRIPT[start:]
    return source.split("\n$('", 1)[0]


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = {}
        self.labels = []
        self.feed(HTML)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids[attrs['id']] = (tag, attrs)
        if tag == 'label' and 'for' in attrs:
            self.labels.append(attrs['for'])


class JobsUIContracts(unittest.TestCase):
    def javascript(self, body, *names):
        definitions = SCRIPT[SCRIPT.index('const i18n'):SCRIPT.index('function ui(')]
        source = definitions + '\nlet currentRole="admin",stageEpoch=0;\n' + '\n'.join(function(name) for name in names) + '\n' + body
        result = subprocess.run(['node', '-e', source], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_schedule_inputs_start_disabled_and_off(self):
        parsed = Elements()
        checkbox = parsed.ids['job-schedule-enabled'][1]
        self.assertNotIn('checked', checkbox)
        for name in ('kind', 'minutes', 'time', 'timezone', 'enabled', 'save'):
            self.assertIn('disabled', parsed.ids['job-schedule-' + name][1])
        self.assertEqual(parsed.ids['job-schedule-minutes'][1]['min'], '1')
        self.assertEqual(parsed.ids['job-schedule-minutes'][1]['max'], '10080')
        self.assertEqual(parsed.ids['job-schedule-timezone'][1]['value'], 'Asia/Tokyo')
        self.assertTrue(all(label in parsed.ids for label in parsed.labels))

    def test_interval_and_daily_payload_validation(self):
        self.javascript(r"""
const check=(value,message)=>{if(!value)throw Error(message);};
for(const minutes of [1,10080]){
 const p=jobSchedulePayload('job',{kind:'interval',every_minutes:String(minutes),enabled:false,actor:'fake',role:'admin'},{n:3});
 check(p.every_minutes===minutes&&!p.enabled&&!('actor' in p)&&!('role' in p),'interval boundary/auth fields');
 check(JSON.stringify(Object.keys(p).sort())===JSON.stringify(['arguments','enabled','every_minutes','kind','name']),'payload schema');
}
for(const minutes of [0,10081,1.5,'','bad']){
 let rejected=false;try{jobSchedulePayload('job',{kind:'interval',every_minutes:minutes},{});}catch{rejected=true;}check(rejected,'invalid interval');
}
for(const time of ['00:00','23:59']){const p=jobSchedulePayload('job',{kind:'daily',time,timezone:'Asia/Tokyo',enabled:true},{});check(p.time===time&&p.enabled,'valid daily');}
for(const fields of [{time:'24:00',timezone:'Asia/Tokyo'},{time:'2:00',timezone:'Asia/Tokyo'},{time:'09:00',timezone:'Invalid/Zone'},{time:'09:00',timezone:''}]){
 let rejected=false;try{jobSchedulePayload('job',{kind:'daily',...fields},{});}catch{rejected=true;}check(rejected,'invalid daily');
}
""", 'schedulePayload', 'jobSchedulePayload')

    def test_schedule_permission_and_shared_typed_arguments(self):
        self.javascript(r"""
if(!jobScheduleEditable({can_manage:true})||jobScheduleEditable({can_manage:false})||jobScheduleEditable(null))throw Error('owner rule');
currentRole='bi_viewer';if(jobScheduleEditable({can_manage:true}))throw Error('viewer rule');
const nodes=[{value:'7',dataset:{argument:'n',type:'integer'}},{value:'false',dataset:{argument:'flag',type:'boolean'}},{value:'2026-10-18',dataset:{argument:'day',type:'date'}},{value:'',dataset:{argument:'optional',type:'string'}}];
const $=()=>({querySelectorAll:()=>nodes});
const values=readJobArguments();if(values.n!==7||values.flag!==false||values.day!=='2026-10-18'||'optional' in values)throw Error('typed args');
nodes[0].value='1.2';let rejected=false;try{readJobArguments();}catch{rejected=true;}if(!rejected)throw Error('fractional integer');
""", 'jobScheduleEditable', 'readJobArguments')

    def test_pause_ignores_invalid_edited_arguments_and_schedule(self):
        self.javascript(r"""
currentRole='data_engineer';
const selectedJob={name:'job',can_manage:true};
let jobSchedules=[{name:'job',role:'admin',kind:'daily',enabled:true}],submitted=[],validated=0;
const $=id=>({checked:false,value:'invalid',reportValidity:()=>{validated++;return false;}});
const readJobArguments=()=>{throw Error('edited arguments must not be parsed for pause');};
const jobSchedulePayload=()=>{throw Error('edited time/zone must not be validated for pause');};
const busy=async(button,task)=>task();
const api=async(path,payload)=>{submitted.push({path,payload});};
const loadJobSchedules=async()=>{};
const ui=()=>{};
(async()=>{
 await saveJobSchedule();
 if(validated!==0||submitted.length!==1||submitted[0].path!=='/api/jobs/schedule'||JSON.stringify(submitted[0].payload)!==JSON.stringify({name:'job',enabled:false}))throw Error('minimal pause contract');
 jobSchedules=[{name:'job',enabled:false}];await saveJobSchedule();
 if(validated!==1||submitted.length!==1)throw Error('existing disabled schedule must validate full definition');
 jobSchedules=[];await saveJobSchedule();
 if(validated!==2||submitted.length!==1)throw Error('new disabled schedule must validate full definition');
})().catch(error=>{console.error(error.message);process.exitCode=1;});
""", 'jobScheduleEditable', 'saveJobSchedule')
        parsed = Elements()
        self.assertIn('novalidate', parsed.ids['job-schedule-form'][1])

    def test_stop_button_pauses_saved_schedule_without_touching_drafts(self):
        self.javascript(r"""
const assert=require('node:assert/strict');
let selectedJob={name:'job',can_manage:true},jobSchedules=[{name:'job',enabled:true,kind:'interval',every_minutes:5}];
let jobScheduleEnabledDirty=true,submitted=[],reloaded=0;
const fields={'job-schedule-timezone':{value:'Bad/Zone'},'job-schedule-stop':{disabled:false},'job-schedule-message':{}};
const $=id=>fields[id]??={};
const busy=async(button,task)=>task(),ui=(node,key)=>{node.text=key;};
const api=async(path,payload)=>{submitted.push({path,payload});};
const loadJobSchedules=async preserve=>{reloaded++;assert.equal(preserve,true,'drafts must be preserved');jobSchedules=[{name:'job',enabled:false}];};
(async()=>{
 await stopJobSchedule();
 assert.deepEqual(submitted,[{path:'/api/jobs/schedule',payload:{name:'job',enabled:false}}],'minimal pause payload');
 assert.equal(reloaded,1);assert.equal(jobScheduleEnabledDirty,false);assert.equal($('job-schedule-timezone').value,'Bad/Zone');
 assert.equal($('job-schedule-message').text,'Schedule stopped.');assert.equal($('job-schedule-stop').disabled,true);
 await stopJobSchedule();assert.equal(submitted.length,1,'nothing to stop');
 currentRole='bi_viewer';jobSchedules=[{name:'job',enabled:true}];await stopJobSchedule();assert.equal(submitted.length,1,'viewer cannot stop');
})().catch(error=>{console.error(error);process.exitCode=1;});
""", 'jobScheduleEditable', 'stopJobSchedule')

    def test_unsaved_definition_blocks_manual_run(self):
        self.javascript(r"""
const assert=require('node:assert/strict');
const nodes={},$=id=>nodes[id]??={getAttribute(){return null;}};
const ui=(node,key)=>{node.text=key;};
let selectedJob={name:'job',can_manage:true},editingJob={name:'job'},jobDirty=true;
updateJobRunControls();assert.equal($('job-run').disabled,true);assert.match($('job-run-help').text,/Save the definition/);
editingJob={name:'other'};updateJobRunControls();assert.equal($('job-run').disabled,false,'edits to another job do not block this run');
editingJob={name:'job'};jobDirty=false;updateJobRunControls();assert.equal($('job-run').disabled,false);
selectedJob={name:'job',can_manage:false};updateJobRunControls();assert.equal($('job-run').disabled,true);
selectedJob={name:'job',can_manage:true};$('job-run').getAttribute=()=> 'true';updateJobRunControls();assert.equal($('job-run').disabled,true,'busy run stays disabled');
""", 'updateJobRunControls')

    def test_disabled_schedule_edits_use_full_payload(self):
        self.javascript(r"""
const selectedJob={name:'job',can_manage:true},jobSchedules=[{name:'job',kind:'daily',enabled:false}];
let validated=0,read=0,submitted=[];
const fields={'job-schedule-kind':{value:'interval'},'job-schedule-minutes':{value:'1'},'job-schedule-time':{value:'invalid'},'job-schedule-timezone':{value:'Invalid/Zone'},'job-schedule-enabled':{checked:false}};
const $=id=>fields[id]||{reportValidity:()=>{validated++;return true;}};
const readJobArguments=()=>{read++;return {n:7};};
const busy=async(button,task)=>task(),api=async(path,payload)=>{submitted.push(payload);},loadJobSchedules=async()=>{},ui=()=>{};
(async()=>{
 await saveJobSchedule();
 if(validated!==1||read!==1||submitted.length!==1)throw Error('disabled edit not fully validated');
 require('node:assert/strict').deepEqual(submitted[0],{name:'job',kind:'interval',arguments:{n:7},enabled:false,every_minutes:1},'disabled full payload');
})().catch(error=>{console.error(error.message);process.exitCode=1;});
""", 'readScheduleFields', 'schedulePayload', 'jobSchedulePayload', 'jobScheduleEditable', 'saveJobSchedule')

    def test_schedule_and_history_are_localized(self):
        self.javascript(r"""
for(const key of ['Schedule','Every N minutes','Daily','Enable schedule','Saved role: {role}','Next run (UTC): {time}','Trigger','Scheduled for (UTC)','Manual','Scheduled','Skipped']){
 if(!i18n.en[key]||!i18n.ja[key]||i18n.en[key]===i18n.ja[key])throw Error('missing localized schedule text: '+key);
}
language='ja';if(t('Skipped')!==String.fromCodePoint(0x30b9,0x30ad,0x30c3,0x30d7))throw Error('skip label');
for(const [run,expected] of [
 [{trigger:'manual'},'Manual'],[{trigger:'schedule'},'Scheduled'],
 [{trigger:'stage',steps:[{action:'stage_import'}]},'Stage import'],
 [{trigger:'stage',steps:[{action:'stage_export'}]},'Stage export']]){
 if(jobTrigger(run)!==expected||t(jobTrigger(run))===expected)throw Error('localized trigger: '+expected);
}
""", 'jobTrigger')
        self.assertIn("run.scheduled_for||", SCRIPT)

    def test_alert_confirmation_and_resolution_are_independent(self):
        self.javascript(r"""
const active={can_manage:true,acknowledged_at:null,resolved_at:null,resolution:null};
if(!jobAlertCanAcknowledge(active)||jobAlertResolution(active)!=='Active condition')throw Error('active alert');
const confirmed={...active,acknowledged_at:'2026-10-18T10:00:00Z'};
if(jobAlertCanAcknowledge(confirmed)||jobAlertResolution(confirmed)!=='Active condition')throw Error('ack must not resolve');
const resolved={...active,resolved_at:'2026-10-18T10:00:00Z',resolution:'condition_cleared'};
if(!jobAlertCanAcknowledge(resolved)||jobAlertResolution(resolved)!=='Condition cleared')throw Error('resolve must not acknowledge');
if(jobAlertResolution({...resolved,resolution:'definition_changed'})!=='Definition changed')throw Error('definition change');
if(jobAlertCanAcknowledge({...active,can_manage:false}))throw Error('other owner');
currentRole='data_engineer';if(!jobAlertCanAcknowledge(active))throw Error('engineer owner');
currentRole='bi_viewer';if(jobAlertCanAcknowledge(active))throw Error('viewer');
""", 'jobAlertCanAcknowledge', 'jobAlertResolution')

    def test_alert_summary_and_language_contract(self):
        self.javascript(r"""
language='ja';
const opened=jobSummary({action:'alert',summary:{condition_met:true,alert_status:'opened',alert_id:'123456789abcdef'}});
if(!opened.includes(t('Condition met'))||!opened.includes(t('New notification'))||!opened.includes('12345678')||opened.includes('condition_met'))throw Error('readable alert summary');
const changed=jobSummary({action:'alert',summary:{condition_met:true,alert_status:'definition_changed'}});
if(!changed.includes(t('Closed after rule change'))||changed.includes(t('Condition clear')))throw Error('old snapshot must not claim recovery');
language='en';const clear=jobSummary({action:'alert',summary:{condition_met:false,alert_status:'clear'}});
if(!clear.includes('Condition clear')||!clear.includes('No active notification'))throw Error('English clear summary');
for(const key of ['Evaluate alert','Alert message','Severity','warning','critical','Notifications','Acknowledgement','Resolution','Acknowledge','Up to 100 notifications, unacknowledged first.'])if(!i18n.ja[key]||!i18n.en[key]||i18n.ja[key]===i18n.en[key])throw Error('alert translation');
""", 'jobSummary')

    def test_alert_badge_uses_total_and_never_requests_as_viewer(self):
        start = SCRIPT.index('async function loadJobAlerts(')
        end = SCRIPT.index("\n$('job-alerts-refresh')", start)
        source = SCRIPT[start:end]
        self.javascript(r"""
const badge={hidden:true};let calls=0,display;
const $=()=>badge,jobsVisible=()=>false,api=async()=>{calls++;return {alerts:[{id:'one'}],unacknowledged_count:107};};
const ui=(node,key,values)=>{display=values.count;},pruneUIBindings=()=>{};
""" + source + r"""
(async()=>{
 await loadJobAlerts(false);if(calls!==1||display!==107||badge.hidden)throw Error('must use total count rather than list length');
 currentRole='bi_viewer';await loadJobAlerts(false);if(calls!==1||!badge.hidden)throw Error('viewer request forbidden');
})().catch(error=>{console.error(error.message);process.exitCode=1;});
""")

    def test_alert_editor_has_no_mode_and_preserves_sql_parameters(self):
        source = function('renderJobSteps')
        self.assertIn("!['sql','alert'].includes(option.value)", source)
        alert = source.split(" }else{\n const note=", 1)[1].split(" }\n jobField(options,'Schema'", 1)[0]
        self.assertNotIn("'mode'", alert)
        self.assertIn("message.required=true;message.maxLength=500", alert)
        self.assertIn("step.severity||'warning'", alert)
        self.assertIn("step.schema||'marts'", source)
        self.assertIn("{{name}}", HTML)
        self.assertIn('id="jobs-alert-count"', HTML)
        self.assertIn('id="job-alerts"', HTML)

    def test_alert_table_is_compact_and_preserves_exact_timestamps(self):
        self.javascript(r"""
class Element{constructor(tag='div'){this.tag=tag;this.children=[];this.textContent='';}append(...nodes){this.children.push(...nodes);}}
const document={createElement:tag=>new Element(tag)},root=new Element(),zone=new Element();let rows=[],headers=[];
const $=id=>id==='job-alerts-timezone'?zone:root;
root.querySelectorAll=()=>rows;
const table=(container,columns,values)=>{headers=columns;rows=values.map(values=>{const cells=values.map(value=>{const node=new Element('td');node.textContent=String(value);return node;});return {cells,lastElementChild:cells[cells.length-1]};});};
const ui=(node,key,values={})=>{node.textContent=t(key,typeof values==='function'?values():values);};
const actionButton=(key,callback)=>{const node=new Element('button');node.textContent=t(key);node.callback=callback;return node;};
const exact='2026-10-18T10:11:12.123Z';
renderJobAlerts([{severity:'critical',message:'Check inventory',job_name:'inventory',step_name:'stock',owner:'alice',occurrences:3,first_seen_at:exact,last_seen_at:exact,acknowledged_at:null,resolved_at:null,can_manage:true}]);
if(headers.length!==7||headers.join('|')!=='Importance|Notification|Acknowledgement|Resolution|Occurrences|Last seen|Actions')throw Error('compact alert columns');
if(rows[0].cells[6].children.length!==1||rows[0].cells[6].children[0].tag!=='button')throw Error('acknowledgement action must remain visible');
if(!rows[0].cells[1].children[1].textContent.includes('inventory')||!rows[0].cells[1].children[1].textContent.includes('alice'))throw Error('notification context');
const timestamp=rows[0].cells[5].children[0];if(timestamp.dateTime!==exact||timestamp.title!==exact||timestamp.textContent===exact)throw Error('localized visible time, exact accessible timestamp');
if(!zone.textContent.includes(Intl.DateTimeFormat().resolvedOptions().timeZone))throw Error('timezone note');
if(rows[0].cells[2].children[0].textContent!==t('Unacknowledged')||rows[0].cells[3].textContent!==t('Active condition'))throw Error('separate states');
language='ja';if(!jobAlertTime(exact).includes('2026'))throw Error('localized date');
""", 'jobAlertCanAcknowledge', 'jobAlertResolution', 'jobAlertTime', 'jobAlertTimestamp', 'renderJobAlerts')

    def test_history_refresh_updates_schedule_state_without_discarding_drafts(self):
        enabled_handler = next(line for line in SCRIPT.splitlines() if line.startswith("$('job-schedule-enabled').onchange="))
        self.javascript('const ENABLED_HANDLER=' + json.dumps(enabled_handler) + ';' + r"""
const assert=require('node:assert/strict');
class Element {
 constructor(){this.value='';this.checked=false;this.disabled=false;this.children=[];this.textContent='';}
 append(...items){this.children.push(...items);}
 replaceChildren(...items){this.children=items;}
 querySelectorAll(selector){return [];}
}
const nodes={},$=id=>nodes[id]??=new Element(),document={createElement:()=>new Element()};
eval(ENABLED_HANDLER);
const arg={value:'3',dataset:{argument:'limit',type:'integer'}};
$('job-run-arguments').querySelectorAll=()=>[arg];
const ui=(node,key,values={})=>{node.textContent=t(key,typeof values==='function'?values():values);};
const pruneUIBindings=()=>{},message=(id,text)=>{$(id).textContent=text;},jobEmpty=(id,text)=>{$(id).textContent=text;};
let jobsRequest=0,jobScheduleRequest=0,jobScheduleEnabledDirty=false,selectedRunId=null,visible=true,pollRuns=[],jobRunCache=[];
let selectedJob={name:'orders',can_manage:true};
let jobSchedules=[{name:'orders',kind:'once',date:'2030-01-01',time:'09:00',timezone:'UTC',enabled:true,next_run_at:'2030-01-01T09:00:00Z',arguments:{limit:3}}];
const scheduleFields=['kind','minutes','time','timezone','date','day',...Array.from({length:7},(_,i)=>'weekday-'+(i+1))];
const jobsVisible=()=>visible,stopJobsPolling=()=>{},scheduleJobsPolling=runs=>{pollRuns=runs;},loadJobAlerts=async()=>{},showJobRun=async()=>{};
const runs=[{id:'run',job_name:'orders',status:'succeeded',trigger:'schedule'}];
let tableRows=[];
const table=(container,columns,values)=>{tableRows=values.map(()=>({cells:Array.from({length:columns.length},()=>new Element()),lastElementChild:new Element()}));};
$('job-runs').querySelectorAll=()=>tableRows;
const actionButton=()=>new Element(),jobStatus=status=>status,jobDuration=()=>0,jobStatuses={succeeded:'Succeeded'};
let serverSchedules=[{...jobSchedules[0],enabled:false,next_run_at:null}],pendingSchedule=null,pendingRuns=null,scheduleReads=0;
const api=async path=>{
 if(path==='/api/jobs/runs')return pendingRuns?new Promise(resolve=>pendingRuns.resolve=resolve):{runs};
 if(path==='/api/jobs/schedules'){scheduleReads++;return pendingSchedule?new Promise(resolve=>pendingSchedule.resolve=resolve):{schedules:serverSchedules};}
 throw Error('unexpected request '+path);
};
const stateText=()=>$('job-schedule-state').children.map(child=>child.textContent).join('|');
(async()=>{
 renderJobSchedule();assert.equal($('job-schedule-enabled').checked,true);
 await refreshJobRuns();
 assert.equal(jobSchedules[0].enabled,false,'history refresh must fetch auto-disabled once state');
 assert.equal($('job-schedule-enabled').checked,false,'unchanged enable control must reflect once consumption');
 assert.ok(stateText().includes('Disabled'));assert.ok(stateText().includes(scheduleNextTime(serverSchedules[0])));
 assert.equal(pollRuns[0].status,'succeeded');
 // A recurring schedule advances even when its configuration has not changed.
 serverSchedules=[{...serverSchedules[0],kind:'weekly',weekdays:[1,5],enabled:true,next_run_at:'2030-01-04T09:00:00Z'}];
 await loadJobSchedules();
 $('job-schedule-kind').value='monthly';$('job-schedule-day').value='last';$('job-schedule-time').value='17:45';arg.value='19';
 serverSchedules=[{...serverSchedules[0],next_run_at:'2030-01-07T09:00:00Z'}];
 await refreshJobRuns();
 assert.ok(stateText().includes(scheduleNextTime(serverSchedules[0])),'recurring next time must advance');
 assert.equal($('job-schedule-kind').value,'monthly');assert.equal($('job-schedule-day').value,'last');
 assert.equal($('job-schedule-time').value,'17:45');assert.equal(arg.value,'19');
 // Consumption while calendar fields are edited updates only saved status and the untouched checkbox.
 serverSchedules=[{...serverSchedules[0],enabled:false,next_run_at:null}];
 await refreshJobRuns();assert.equal($('job-schedule-enabled').checked,false);assert.equal(arg.value,'19');assert.equal($('job-schedule-time').value,'17:45');
 // An explicitly edited enable checkbox also remains a draft.
 $('job-schedule-enabled').checked=true;$('job-schedule-enabled').onchange();await refreshJobRuns();assert.equal($('job-schedule-enabled').checked,true);assert.ok(stateText().includes('Disabled'));
 // Keep the same user pause draft even if another tab disables and then reenables.
 serverSchedules=[{...serverSchedules[0],enabled:true}];await loadJobSchedules();
 assert.equal(jobScheduleEnabledDirty,false,'full refresh resets checkbox draft tracking');
 $('job-schedule-enabled').checked=false;$('job-schedule-enabled').onchange();
 serverSchedules=[{...serverSchedules[0],enabled:false}];await refreshJobRuns();
 assert.equal($('job-schedule-enabled').checked,false);
 serverSchedules=[{...serverSchedules[0],enabled:true}];await refreshJobRuns();
 assert.equal($('job-schedule-enabled').checked,false,'repeated server transitions must not erase pause draft');assert.ok(stateText().includes('Enabled'));
 await loadJobSchedules();assert.equal(jobScheduleEnabledDirty,false);assert.equal($('job-schedule-enabled').checked,true);
 // In-flight results must not repaint another selection, even under the same role.
 pendingSchedule={};let pending=loadJobSchedules(true);const previous=jobSchedules;
 selectedJob={name:'other',can_manage:true};renderJobSchedule();arg.value='new job draft';
 pendingSchedule.resolve({schedules:serverSchedules});await pending;
 assert.equal(jobSchedules,previous);assert.equal($('job-schedule-name').textContent,'other');assert.equal(arg.value,'new job draft');
 // Switching role away and back still invalidates the response through the epoch.
 pendingSchedule={};pending=loadJobSchedules(true);currentRole='bi_viewer';++stageEpoch;currentRole='admin';
 pendingSchedule.resolve({schedules:[]});await pending;assert.equal(jobSchedules,previous);
 // A newer explicit schedule refresh takes precedence over an older passive response.
 pendingSchedule={};pending=loadJobSchedules(true);const oldResponse=pendingSchedule;
 pendingSchedule=null;serverSchedules=[];await loadJobSchedules();
 oldResponse.resolve({schedules:previous});await pending;assert.deepEqual(jobSchedules,[]);
 // A history response from before a role reset cannot start a new schedule read.
 pendingRuns={};pending=refreshJobRuns();const reads=scheduleReads;
 currentRole='bi_viewer';++stageEpoch;currentRole='admin';pendingRuns.resolve({runs});await pending;
 assert.equal(scheduleReads,reads);
 // Likewise an older history request cannot override the newer refresh.
 pendingRuns={};pending=refreshJobRuns();const oldRuns=pendingRuns;pendingRuns=null;
 await refreshJobRuns();const freshReads=scheduleReads;oldRuns.resolve({runs});await pending;assert.equal(scheduleReads,freshReads);
})().catch(error=>{console.error(error);process.exitCode=1;});
""", 'refreshJobRuns', 'renderJobRuns', 'loadJobSchedules', 'renderJobSchedule', 'jobScheduleEditable',
                        'updateJobScheduleKind', 'fillScheduleFields', 'updateScheduleKind',
                        'scheduleNextTime', 'scheduleSummary', 'jobTrigger', 'statusTone',
                        'jobAlertTime', 'jobAlertTimestamp')

    def handler(self, start, end):
        begin = SCRIPT.index(start)
        return SCRIPT[begin:SCRIPT.index(end, begin) + len(end)]

    def test_manual_run_response_stays_bound_to_its_submission(self):
        submit = self.handler("$('job-run-form').onsubmit=", "}).finally(updateJobRunControls);};")
        self.javascript('const SUBMIT=' + json.dumps(submit) + ';' + r"""
const assert=require('node:assert/strict');
class Node{constructor(){this.dataset={};this.hidden=true;this.disabled=false;this.attrs={};this.textContent='';this.className='';}
 getAttribute(k){return this.attrs[k]??null;}setAttribute(k,v){this.attrs[k]=String(v);}removeAttribute(k){delete this.attrs[k];}}
const nodes={},$=id=>nodes[id]??=new Node();
const ui=(node,key,values={})=>{node.textContent=t(key,typeof values==='function'?values():values);};
const jobStatus=status=>status;
let selectedJob=null,selectedRunId=null,jobSelectionEpoch=0,refreshes=0,controls=0,calls=[],pending=[];
const readJobArguments=()=>({n:1}),updateJobRunControls=()=>{controls++;},refreshJobRuns=async()=>{refreshes++;};
const busy=async(button,task)=>{if(button.disabled)return;button.disabled=true;button.setAttribute('aria-busy','true');
 try{await task();}finally{button.disabled=false;button.removeAttribute('aria-busy');}};
const api=(path,body)=>{calls.push({path,body});return new Promise((resolve,reject)=>pending.push({resolve,reject}));};
eval(SUBMIT);
const submit=()=>$('job-run-form').onsubmit({preventDefault(){}});
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const A={name:'job_a',can_manage:true},B={name:'job_b',can_manage:true};
const reset=job=>{clearManualRun();selectedJob=job;refreshes=0;controls=0;calls=[];pending=[];};
(async()=>{
 // Accepted: the result, its run ID and the history refresh follow; the button is restored once.
 reset(A);let run=submit();submit();submit();await settle();
 assert.equal(calls.length,1,'no duplicate submission while pending');assert.deepEqual(calls[0].body,{name:'job_a',arguments:{n:1}});
 assert.equal($('job-run').disabled,true);pending[0].resolve({run:{id:'run-a',status:'queued'}});await run;
 assert.equal($('job-run-open').dataset.run,'run-a');assert.equal($('job-run-open').dataset.job,'job_a');assert.equal($('job-run-open').hidden,false);
 assert.match($('job-run-state').textContent,/run-a/);assert.equal(selectedRunId,'run-a');assert.equal(refreshes,1);
 assert.equal($('job-run').disabled,false);assert.equal($('job-run').getAttribute('aria-busy'),null);assert.equal(controls,1);
 // Rejected: shown as a failure for the same selection, without history refresh.
 reset(A);run=submit();await settle();pending[0].reject(Error('server busy'));await run;
 assert.match($('job-run-state').textContent,/Run failed: server busy/);assert.equal($('job-run-open').hidden,true);assert.equal(refreshes,0);assert.equal(controls,1);
 // New job while pending: no exception, no false failure, history still refreshed.
 reset(A);selectedRunId='before';run=submit();await settle();clearManualRun();selectedJob=null;
 pending[0].resolve({run:{id:'run-new',status:'queued'}});await run;
 assert.equal($('job-run-state').textContent,'');assert.equal($('job-run-open').hidden,true);assert.equal($('job-run-open').dataset.run,undefined);
 assert.equal(selectedRunId,'before');assert.equal(refreshes,1);assert.equal($('job-run').disabled,false);
 // Another job while pending: A's run is not attached to B.
 reset(A);run=submit();await settle();clearManualRun();selectedJob=B;pending[0].resolve({run:{id:'run-a2',status:'queued'}});await run;
 assert.equal($('job-run-open').dataset.run,undefined);assert.equal($('job-run-state').textContent,'');
 // A -> B -> A: the earlier submission is still stale for the new selection of A.
 reset(A);run=submit();await settle();clearManualRun();selectedJob=B;clearManualRun();selectedJob=A;
 pending[0].resolve({run:{id:'run-a3',status:'queued'}});await run;assert.equal($('job-run-open').dataset.run,undefined);assert.equal($('job-run-state').textContent,'');
 // Role reset and return: stale even though the job and role look the same again.
 reset(A);run=submit();await settle();currentRole='bi_viewer';++stageEpoch;currentRole='admin';
 pending[0].resolve({run:{id:'run-a4',status:'queued'}});await run;assert.equal($('job-run-open').dataset.run,undefined);
 // A rejection after switching does not paint a failure on the new selection.
 reset(A);run=submit();await settle();clearManualRun();selectedJob=B;pending[0].reject(Error('late'));await run;assert.equal($('job-run-state').textContent,'');
})().catch(error=>{console.error(error);process.exitCode=1;});
""", 'clearManualRun', 'statusTone')

    def test_inspect_button_opens_the_reported_run(self):
        click = self.handler("$('job-run-open').onclick=", "\n};")
        self.javascript('const CLICK=' + json.dumps(click) + ';' + r"""
const assert=require('node:assert/strict');
class Node{constructor(){this.dataset={};this.hidden=false;this.textContent='';this.className='';this.children=[];this.value='';this.options=[];}
 replaceChildren(...c){this.children=c;this.textContent='';}querySelectorAll(){return [];}}
const nodes={},$=id=>nodes[id]??=new Node();
const ui=(node,key,values={})=>{node.textContent=t(key,typeof values==='function'?values():values);};
const message=(id,text)=>{$(id).textContent=text;$(id).hidden=!text;},jobEmpty=(id,key)=>{$(id).textContent=key;};
let rendered=[];const table=(container,columns,rows)=>{container.rows=rows;};
const jobsVisible=()=>true,jobStatus=s=>s,jobStatuses={},liveJobStates=new Set(['queued','running']),jobDuration=()=>0,jobSummary=()=>'',jobActionLabels={};
let selectedJob={name:'job_a'},selectedRunId='run-b',stageExportRunId=null,stageDirty=false,stagePending=false,views=[],renders=0,jobDetailRequest=0;
const showView=id=>views.push(id);
const jobRunCache=[{id:'run-a',job_name:'job_a',status:'success',steps:[]},{id:'run-b',job_name:'job_b',status:'success',steps:[]}];
const jobTrigger=()=>'manual';
let pending={};const api=(path,body)=>new Promise((resolve,reject)=>{pending[body.run_id]={resolve,reject};});
eval(CLICK);
$('job-runs-filter').options=[{value:''},{value:'job_a'},{value:'job_b'}];
const detail=id=>({run:{id,job_name:id==='run-b'?'job_b':'job_a',status:'success',steps:[{name:'s',status:'success'}]}});
(async()=>{
 // B's steps are on screen; A's button must load A even with history filtered to B.
 $('job-detail-state').textContent='Run run-b · success';$('job-run-detail').rows=[['b-step']];$('job-runs-filter').value='job_b';
 $('job-run-open').dataset.run='run-a';$('job-run-open').dataset.job='job_a';
 let opened=$('job-run-open').onclick();
 assert.deepEqual(views,['jobs-view-runs']);assert.equal(selectedRunId,'run-a');assert.ok(pending['run-a'],'details requested for run-a');
 assert.equal($('job-runs-filter').value,'job_a');assert.match($('job-detail-state').textContent,/run-a/);assert.deepEqual($('job-run-detail').children,[]);
 pending['run-a'].resolve(detail('run-a'));await opened;
 assert.match($('job-detail-state').textContent,/Run run-a/);assert.deepEqual($('job-run-detail').rows.map(r=>r[0]),['s']);
 // Completed A remains selected; changing only the filter to B must not leave B's rows when reopening A.
 $('job-runs-filter').value='job_b';renderJobRuns();assert.deepEqual($('job-runs').rows.map(r=>r[0]),['run-b']);
 opened=$('job-run-open').onclick();
 assert.equal(selectedRunId,'run-a');assert.equal($('job-runs-filter').value,'job_a');
 assert.deepEqual($('job-runs').rows.map(r=>r[0]),['run-a']);
 pending['run-a'].resolve(detail('run-a'));await opened;
 assert.match($('job-detail-state').textContent,/Run run-a/);assert.deepEqual($('job-run-detail').rows.map(r=>r[0]),['s']);
 // A failed fetch never leaves another run's steps as the target's details.
 selectedRunId='run-b';$('job-run-detail').rows=[['b-step']];opened=$('job-run-open').onclick();pending['run-a'].reject(Error('gone'));await opened;
 assert.match($('job-detail-state').textContent,/run-a details unavailable: gone/);assert.deepEqual($('job-run-detail').children,[]);
 // Out-of-order responses: the later choice wins.
 const first=showJobRun('run-c'),second=showJobRun('run-d');pending['run-d'].resolve(detail('run-d'));await second;
 pending['run-c'].resolve(detail('run-c'));await first;assert.match($('job-detail-state').textContent,/run-d/);assert.equal(selectedRunId,'run-d');
 // A role reset while loading drops the response.
 const late=showJobRun('run-e');currentRole='bi_viewer';++stageEpoch;currentRole='admin';pending['run-e'].resolve(detail('run-e'));await late;
 assert.doesNotMatch($('job-detail-state').textContent,/Run run-e ·/);
 // Without a reported run the button does nothing.
 delete $('job-run-open').dataset.run;views=[];assert.equal($('job-run-open').onclick(),undefined);assert.deepEqual(views,[]);
})().catch(error=>{console.error(error);process.exitCode=1;});
""", 'showJobRun', 'renderJobRuns', 'statusTone')

    def test_runs_outside_the_loaded_window_are_not_called_never_run(self):
        self.javascript(r"""
const assert=require('node:assert/strict');
class Node{constructor(tag){this.tag=tag;this.children=[];this.textContent='';this.className='';this.value='';this.attrs={};this.dataset={};}
 append(...c){this.children.push(...c);}setAttribute(k,v){this.attrs[k]=v;}}
const nodes={},$=id=>nodes[id]??=new Node('div'),document={createElement:tag=>new Node(tag)};
const ui=(node,key,values={})=>{node.textContent=t(key,typeof values==='function'?values():values);};
let rows=[];const table=(container,columns,values)=>{rows=values.map(v=>({cells:v.map(()=>new Node('td')),setAttribute(){}}));rows.forEach(r=>r.lastElementChild=r.cells.at(-1));container.text='table';};
$('jobs-list').querySelectorAll=()=>rows;$('job-runs').querySelectorAll=()=>rows;
const jobEmpty=(id,key)=>{$(id).text=t(key);},actionButton=()=>new Node('button'),updateRunFilter=()=>{};
const jobStatuses={success:'Succeeded'},jobStatus=s=>s,jobTrigger=()=>'Manual',jobDuration=()=>0;
let jobEntries=[{name:'old_job',owner:'me',can_manage:true,definition:{steps:[{}]}}],jobSchedules=[],selectedJob=null,editingJob=null,selectedRunId=null;
let jobRunCache=[],jobRunState='loading';
const lastRun=()=>rows[0].cells[4];
renderJobList();assert.equal(lastRun().textContent,'Loading…');
jobRunState='failed';renderJobList();assert.equal(lastRun().textContent,'History unavailable');
// 100 newer runs of another job push old_job out of the loaded window.
jobRunState='ready';renderJobList(Array.from({length:100},(_,i)=>({id:'r'+i,job_name:'busy_job',status:'success',started_at:'2026-10-18T10:00:00Z'})));
assert.equal(lastRun().textContent,'Not in the latest 100 runs');assert.doesNotMatch(lastRun().textContent,/Never/);
$('job-runs-filter').value='old_job';renderJobRuns();assert.match($('job-runs').text,/latest 100 runs/);assert.doesNotMatch($('job-runs').text,/yet|never/i);
language='ja';renderJobList();assert.equal(lastRun().textContent,'直近100件に履歴なし');renderJobRuns();assert.match($('job-runs').text,/直近100件/);language='en';
// When the job is in the window, its latest result is shown as before.
renderJobList([{id:'x',job_name:'old_job',status:'success',started_at:'2026-10-18T10:00:00Z'}]);
assert.equal(lastRun().children[0].textContent,'Succeeded');assert.equal(lastRun().children[1].tag,'time');
""", 'renderJobList', 'renderJobRuns', 'statusTone', 'jobAlertTime', 'jobAlertTimestamp', 'scheduleNextTime')

    def test_javascript_parses(self):
        result = subprocess.run(['node', '--check'], input=SCRIPT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
