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

    def test_javascript_parses(self):
        result = subprocess.run(['node', '--check'], input=SCRIPT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
