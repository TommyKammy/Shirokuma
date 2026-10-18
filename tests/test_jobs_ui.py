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

    def test_javascript_parses(self):
        result = subprocess.run(['node', '--check'], input=SCRIPT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
