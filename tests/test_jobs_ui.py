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
    match = re.search(r'\n(?:async )?function ', SCRIPT[start + 1:])
    return SCRIPT[start:start + 1 + match.start()] if match else SCRIPT[start:]


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
        source = definitions + '\nlet currentRole="admin";\n' + '\n'.join(function(name) for name in names) + '\n' + body
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
""", 'jobSchedulePayload')

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
 if(JSON.stringify(submitted[0])!==JSON.stringify({name:'job',kind:'interval',arguments:{n:7},enabled:false,every_minutes:1}))throw Error('disabled full payload');
})().catch(error=>{console.error(error.message);process.exitCode=1;});
""", 'jobSchedulePayload', 'jobScheduleEditable', 'saveJobSchedule')

    def test_schedule_and_history_are_localized(self):
        self.javascript(r"""
for(const key of ['Schedule','Every N minutes','Daily','Enable schedule','Saved role: {role}','Next run (UTC): {time}','Trigger','Scheduled for (UTC)','Manual','Scheduled','Skipped']){
 if(!i18n.en[key]||!i18n.ja[key]||i18n.en[key]===i18n.ja[key])throw Error('missing localized schedule text: '+key);
}
language='ja';if(t('Skipped')!==String.fromCodePoint(0x30b9,0x30ad,0x30c3,0x30d7))throw Error('skip label');
""")
        self.assertIn("run.scheduled_for||", SCRIPT)
        self.assertIn("runs[index].trigger==='schedule'?'Scheduled':'Manual'", SCRIPT)

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

    def test_javascript_parses(self):
        result = subprocess.run(['node', '--check'], input=SCRIPT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
