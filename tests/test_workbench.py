"""Workbench source and direct backend contracts, with no resident lab required."""
from html.parser import HTMLParser
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/local_experiment"))
import ai_config
import workbench_server as wb
from workbench_source import WorkbenchSource


class WorkbenchHTML(HTMLParser):
    """Inspect the served document, including panel ownership and tab labels."""

    def __init__(self, html):
        super().__init__()
        self.html = html
        self.nodes = []
        self.stack = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = dict(attrs)
        node.update(tag=tag, text='', ancestors=tuple(self.stack))
        self.nodes.append(node)
        if tag not in {'input', 'meta', 'link', 'br', 'hr', 'img', 'source', 'wbr'}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]['tag'] == tag:
                del self.stack[i:]
                break

    def handle_data(self, text):
        for node in self.stack:
            node['text'] += text

    def by_id(self, identifier):
        return next(n for n in self.nodes if n.get('id') == identifier)

    def main_script(self):
        """Read body classic scripts from this already-parsed document."""
        return WorkbenchSource(self.html).source



class WorkbenchTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'JavaScript verification requires Node.js')
    def test_calendar_schedule_controls_payloads_readback_and_async_permissions(self):
        doc = self.workbench_document()
        script = doc.main_script()
        shared = 'const scheduleFields=' + script.split('const scheduleFields=', 1)[1].split('function jobAlertTime(', 1)[0]
        jobs = 'function jobSchedulePayload(' + script.split('function jobSchedulePayload(', 1)[1].split("$('job-parameter-example')", 1)[0]
        for prefix in ('job-schedule',):
            for field in ('kind', 'minutes', 'time', 'timezone', 'date', 'day', *('weekday-' + str(day) for day in range(1, 8))):
                self.assertTrue(any(node.get('for') == prefix + '-' + field for node in doc.nodes))
            kind = doc.by_id(prefix + '-kind')
            self.assertEqual([node['value'] for node in doc.nodes if node['tag'] == 'option' and kind in node['ancestors']],
                             ['interval', 'daily', 'once', 'weekly', 'monthly'])
        busy_handler = 'async function busy(' + script.split('async function busy(', 1)[1].split('function aiStatus(', 1)[0]
        harness = r"""
const assert=require('node:assert/strict');
const nodes={};const $=id=>nodes[id]??={value:'',checked:false,disabled:false,hidden:false,dataset:{},children:[],querySelectorAll(){return [];},replaceChildren(...items){this.children=items;},append(...items){this.children.push(...items);},reportValidity(){return true;},setAttribute(){},removeAttribute(){}};
const databaseMode=false;
let language='en',currentRole='admin',stageEpoch=1,jobScheduleRequest=0,jobSelectionEpoch=0,jobScheduleEnabledDirty=false;
const jobsVisible=()=>currentRole!=='bi_viewer';
let selectedJob={name:'orders',can_manage:true},jobSchedules=[];
const t=(key,values={})=>key.replace(/\{(\w+)\}/g,(_,name)=>values[name]??'');
const ui=(node,key,values={})=>{node.textContent=t(key,typeof values==='function'?values():values);};
const pruneUIBindings=()=>{},readJobArguments=()=>({limit:3}),uiBindings=new Map();
const busy=eval('('+BUSY+')');
const document={createElement:()=>({append(){}})};
let respond;const calls=[];const api=(path,body)=>{calls.push({path,body});return new Promise(resolve=>respond=resolve);};
eval(SHARED+JOBS+`
(async()=>{
 const base={kind:'daily',time:'08:30',timezone:'Asia/Tokyo',enabled:true};
 for(const [fields,expected] of [
  [{kind:'interval',every_minutes:'7',enabled:true},{kind:'interval',every_minutes:7,enabled:true}],
  [base,base],
  [{...base,kind:'once',date:'2030-02-28'},{...base,kind:'once',date:'2030-02-28'}],
  [{...base,kind:'weekly',weekdays:[7,1,3]},{...base,kind:'weekly',weekdays:[1,3,7]}],
  [{...base,kind:'monthly',day_of_month:'31'},{...base,kind:'monthly',day_of_month:31}],
  [{...base,kind:'monthly',day_of_month:'last'},{...base,kind:'monthly',day_of_month:'last'}]
 ]){
  assert.deepEqual(jobSchedulePayload('orders',fields,{limit:3}),{name:'orders',...expected,arguments:{limit:3}});
  for(const prefix of ['job-schedule','stage-schedule']){
   fillScheduleFields(prefix,expected);updateScheduleKind(prefix,true);
   assert.deepEqual(schedulePayload(readScheduleFields(prefix)),expected);
   assert.equal($(prefix+'-interval').hidden,fields.kind!=='interval');
   assert.equal($(prefix+'-daily').hidden,fields.kind==='interval');
   for(const kind of ['once','weekly','monthly'])assert.equal($(prefix+'-'+kind).hidden,fields.kind!==kind);
   assert.equal($(prefix+'-date').disabled,fields.kind!=='once');
   assert.equal($(prefix+'-weekday-1').disabled,fields.kind!=='weekly');
   assert.equal($(prefix+'-day').disabled,fields.kind!=='monthly');
   updateScheduleKind(prefix,false);for(const field of scheduleFields)assert.equal($(prefix+'-'+field).disabled,true);
  }
 }
 for(const invalid of [{...base,kind:'once',date:'2030-02-30'},{...base,kind:'weekly',weekdays:[]},{...base,kind:'weekly',weekdays:[1,1]},{...base,kind:'monthly',day_of_month:'32'},{...base,time:'25:00'},{...base,timezone:'Invalid/Zone'},{kind:'interval',every_minutes:0}])assert.throws(()=>schedulePayload(invalid));
 const saved={name:'orders',...base,kind:'weekly',weekdays:[1,5],next_run_at:'2030-01-07T23:30:00Z'};
 jobSchedules=[saved];renderJobSchedule();assert.equal($('job-schedule-kind').value,'weekly');
 assert.equal($('job-schedule-weekday-5').checked,true);assert.equal($('job-schedule-weekday-2').checked,false);
 assert.ok(scheduleNextTime(saved).includes('08:30'));assert.ok(scheduleNextTime(saved).includes('Asia/Tokyo'));
 assert.ok(scheduleSummary(saved).includes('Monday'));assert.ok(scheduleSummary({...saved,kind:'monthly',day_of_month:'last'}).includes('Last day'));
 // Save sends the calendar fields and arguments, then reads the authoritative persisted schedule.
 $('job-schedule-kind').value='monthly';$('job-schedule-day').value='last';
 let pending=saveJobSchedule();assert.deepEqual(calls.at(-1),{path:'/api/jobs/schedule',body:{name:'orders',kind:'monthly',day_of_month:'last',time:'08:30',timezone:'Asia/Tokyo',enabled:true,arguments:{limit:3}}});
 respond({});await new Promise(resolve=>setImmediate(resolve));assert.equal(calls.at(-1).path,'/api/jobs/schedules');
 respond({schedules:[{...saved,kind:'monthly',day_of_month:'last'}]});await pending;assert.equal($('job-schedule-day').value,'last');
 // Pause bypasses malformed edited time/date fields, as before.
 $('job-schedule-enabled').checked=false;$('job-schedule-time').value='bad';
 pending=saveJobSchedule();assert.deepEqual(calls.at(-1).body,{name:'orders',enabled:false});
 respond({});await new Promise(resolve=>setImmediate(resolve));respond({schedules:[{...saved,enabled:false}]});await pending;
 let count=calls.length;currentRole='bi_viewer';await saveJobSchedule();assert.equal(calls.length,count);updateJobScheduleKind();assert.equal($('job-schedule-kind').disabled,true);
 currentRole='admin';selectedJob.can_manage=false;await saveJobSchedule();assert.equal(calls.length,count);
 selectedJob.can_manage=true;fillScheduleFields('job-schedule',saved);pending=saveJobSchedule();
 currentRole='bi_viewer';++stageEpoch;respond({});await pending;assert.equal(calls.length,count+1);assert.equal($('job-schedule-save').disabled,true);
 // The real busy finalizer also respects switching to a definition owned by someone else.
 currentRole='admin';renderJobSchedule();pending=saveJobSchedule();selectedJob={name:'other',can_manage:false};respond({});await pending;assert.equal($('job-schedule-save').disabled,true);
 // A late refresh cannot populate schedules after a role reset, including switching back.
 currentRole='admin';pending=loadJobSchedules();const previous=jobSchedules;currentRole='bi_viewer';++stageEpoch;currentRole='admin';respond({schedules:[{...saved,name:'late'}]});await pending;assert.equal(jobSchedules,previous);
 console.log('calendar schedule behavior complete');
})().catch(error=>{console.error(error);process.exitCode=1;});`);
"""
        source = 'const SHARED=' + json.dumps(shared) + ';const JOBS=' + json.dumps(jobs) + ';const BUSY=' + json.dumps(busy_handler) + ';' + harness
        result = subprocess.run(['node', '-'], input=source, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('calendar schedule behavior complete', result.stdout)

    def test_ai_endpoint_changes_clear_environment_credentials(self):
        for key_env in ('OPENAI_API_KEY', 'BEAR_API_KEY'):
            active = dict(provider='openai', api_url='https://api.openai.com/v1',
                          api_key='', api_key_env=key_env)
            with patch.object(ai_config, 'load_ai_config', side_effect=lambda: dict(active)), \
                 patch.dict(os.environ, {'OPENAI_API_KEY': 'openai-secret',
                                         'BEAR_API_KEY': 'bear-secret', 'NEW_KEY': 'new-secret'}):
                for change in ({'api_url': 'https://new.example/v1'}, {'provider': 'custom'}):
                    for key in (None, '', '********'):
                        supplied = {**change, **({'api_key': key} if key is not None else {})}
                        result = ai_config.merged_ai_config(supplied)
                        self.assertEqual(result['api_key_env'], '')
                        self.assertIsNone(ai_config.resolve_api_key(result))
                    result = ai_config.merged_ai_config({**change, 'api_key_env': 'NEW_KEY'})
                    self.assertEqual(ai_config.resolve_api_key(result), 'new-secret')
                    result = ai_config.merged_ai_config({**change, 'api_key': 'direct-new'})
                    self.assertEqual(ai_config.resolve_api_key(result), 'direct-new')
                self.assertEqual(ai_config.resolve_api_key(ai_config.merged_ai_config({'model': 'new'})),
                                 os.environ[key_env])
                self.assertIsNone(ai_config.resolve_api_key({'api_key_env': ''}))

    def test_resolve_api_key_precedence(self):
        with patch.dict(os.environ, {'CUSTOM_KEY': 'env-secret', 'OPENAI_API_KEY': 'openai-secret'}):
            self.assertEqual(ai_config.resolve_api_key(dict(api_key='direct', api_key_env='CUSTOM_KEY')), 'direct')
            self.assertEqual(ai_config.resolve_api_key(dict(api_key='', api_key_env='CUSTOM_KEY')), 'env-secret')
            self.assertIsNone(ai_config.resolve_api_key(ai_config.load_ai_config()))
            self.assertEqual(ai_config.resolve_api_key(dict(provider='openai', api_key_env='OPENAI_API_KEY')),
                             'openai-secret')
            self.assertIsNone(ai_config.resolve_api_key(dict(api_key_env='MISSING')))

    def test_role_selector_markup_and_translations(self):
        doc = self.workbench_document()
        selector = doc.by_id('role-selector')
        self.assertTrue(any(n.get('for') == 'role-selector' for n in doc.nodes))
        self.assertEqual([n['value'] for n in doc.nodes if n['tag'] == 'option' and selector in n['ancestors']],
                         ['admin', 'data_engineer', 'bi_viewer'])
        self.assertEqual(doc.by_id('role-indicator')['role'], 'status')
        script = doc.main_script()
        for en, ja in [('Admin', '管理者'), ('Data Engineer', 'データ作成者'), ('BI Viewer', '閲覧者')]:
            self.assertIn(f"'{en}':'{ja}'", script)

    @unittest.skipUnless(shutil.which('node'), 'JavaScript verification requires Node.js')
    def test_role_switch_and_api_headers(self):
        script = self.workbench_document().main_script()
        handlers = script.split("let currentRole = 'admin';", 1)[1].split('function message(', 1)[0]
        harness = r"""
const assert=require('node:assert/strict');
const nodes={};const $=id=>nodes[id]??={value:'',disabled:false,hidden:false,options:[]};
const document={documentElement:{dataset:{}}};let selectedTable=null;
$('ai-settings').hidden=true;
$('query-mode').options=['read-only','sandbox-write','admin-write'].map(value=>({value}));
const calls=[];const fetch=async(path,options)=>{calls.push({path,...options});return {ok:true,json:async()=>({})};};
let currentRole='admin',stageEpoch=0;
// Catalog cache and status invalidation have separate harnesses; isolate the role/header contract here.
let catalogGeneration=0;function catalogMutation(){return false;}function catalogIdentity(){return currentRole;}
function invalidateCatalogDetails(){}function observeCatalogRuns(){}function resetCatalogIdentity(){}
function invalidateStatus(){}
function loadCatalog(){}
function workbenchRenderAlerts(){}
function workbenchSyncMobileNav(){}
eval(HANDLERS+`
(async()=>{
 await api('/api/status');assert.equal(calls.at(-1).headers['X-Shirokuma-Role'],'admin');
 $('role-selector').value='bi_viewer';$('role-selector').onchange();
 assert.equal(document.documentElement.dataset.role,'bi_viewer');
 assert.equal($('query-mode').value,'read-only');assert.equal($('query-mode').disabled,true);
 assert.equal($('query-schema').value,'marts');assert.equal($('role-indicator').hidden,false);
 assert.equal($('ai-settings').hidden,true);
 for(const id of ['drop-table-btn','ai-settings-button'])assert.equal($(id).disabled,true);
 await api('/api/warehouse/query',{sql:'SELECT 1'});
 assert.equal(calls.at(-1).headers['X-Shirokuma-Role'],'bi_viewer');
 assert.equal(calls.at(-1).headers['Content-Type'],'application/json');
 $('role-selector').value='data_engineer';$('role-selector').onchange();
 assert.equal($('ai-settings-button').disabled,true);
 assert.equal($('query-mode').options[2].disabled,true);
 $('role-selector').value='admin';$('role-selector').onchange();
 assert.equal($('ai-settings-button').disabled,false);assert.equal($('role-indicator').hidden,true);
 assert.equal($('query-mode').options[2].disabled,false);
 assert.equal($('clone-table-btn').disabled,true);
})().catch(error=>{console.error(error);process.exitCode=1;});`);
"""
        result = subprocess.run(['node', '-'], input='const HANDLERS=' + json.dumps(handlers) + ';' + harness,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'JavaScript verification requires Node.js')
    def test_worksheet_close_behaviors_and_reload(self):
        script = self.workbench_document().main_script()
        dictionary = script[script.index('const i18n'):script.index('function ui(')]
        source = script[script.index("const worksheetKey="):script.index("if(matchMedia('(max-width: 1024px)')")]
        wire = script[script.index('function wireTabs('):script.index('// Views inside a page')]
        harness = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
function boot(saved=null,unavailable=false){
 const nodes={},store=saved?{'shirokuma.worksheets.v1':JSON.stringify(saved)}:{};
 let focused=null,confirmations=[],answer=true,sequence=0;
 class Element{
  constructor(tag='div'){this.tag=tag;this.children=[];this.attrs={};this.dataset={};this.value='';this.selectionStart=0;this.selectionEnd=0;this.selectionDirection='none';this.disabled=false;}
  append(...c){this.children.push(...c);}replaceChildren(...c){this.children=c;}
  setAttribute(k,v){this.attrs[k]=v;}getAttribute(k){return this.attrs[k];}
  addEventListener(k,fn){this['on'+k]=fn;}focus(){focused=this;}click(){if(!this.disabled)this.onclick?.();}
  setSelectionRange(a,b,d){this.selectionStart=a;this.selectionEnd=b;this.selectionDirection=d;}
  querySelectorAll(selector){return this.children.flatMap(c=>[...(selector==='[role=tab]'&&c.attrs.role==='tab'?[c]:[]),...c.querySelectorAll(selector)]);}
 }
 const $=id=>nodes[id]||$('worksheet-tabs').querySelectorAll('[role=tab]').find(n=>n.id===id);
 for(const id of ['sql','worksheet-tabs','worksheet-closers','worksheet-close-select','worksheet-close-action','add-worksheet-tab','query-meta'])nodes[id]=new Element();
 const samples={orders:'SELECT * FROM marts.fct_orders LIMIT 10'};nodes.sql.value=samples.orders;
 const context=vm.createContext({$,samples,document:{createElement:tag=>new Element(tag)},crypto:{randomUUID:()=>String(++sequence)},
  localStorage:{getItem:k=>{if(unavailable)throw Error('blocked');return store[k]??null;},setItem:(k,v)=>{if(unavailable)throw Error('blocked');store[k]=v;}},
  confirm:text=>{confirmations.push(text);return answer;},activate:()=>{},ui:(node,key,values)=>{node.textContent=context.t(key,values);}});
 vm.runInContext(DICTIONARY+WIRE+SOURCE,context);
 return {context,nodes,store,confirmations,answer:value=>answer=value,run:code=>vm.runInContext(code,context),
  tabs:()=>nodes['worksheet-tabs'].querySelectorAll('[role=tab]'),select:()=>nodes['worksheet-close-select'],action:()=>nodes['worksheet-close-action'],focus:()=>focused};
}
const a=boot();assert.equal(a.select().disabled,true);assert.equal(a.action().disabled,true);assert.equal(a.nodes['worksheet-closers'].hidden,true);assert.match(a.action().title,/at least one/);
a.run("closeWorksheet(worksheetId)");assert.equal(a.tabs().length,1);
a.nodes['add-worksheet-tab'].click();a.nodes['add-worksheet-tab'].click();
const ids=a.run('worksheets.map(w=>w.id)');
assert.equal(a.tabs().length,3);assert.equal(a.select().disabled,false);assert.equal(a.action().disabled,true);assert.equal(a.nodes['worksheet-closers'].hidden,false);assert.equal(a.select().children.length,4);
assert.deepEqual(a.select().children.slice(1).map(option=>option.textContent),['Close Worksheet 1','Close Worksheet 2','Close Worksheet 3']);
assert.equal(a.tabs().every(n=>n.attrs.role==='tab'),true);assert.equal(a.nodes['worksheet-tabs'].children.length,a.tabs().length);
// Choosing a target never closes it; only the explicit action button does.
a.select().value=ids[0];a.select().onchange();
assert.equal(a.tabs().length,3);assert.equal(a.confirmations.length,0);assert.equal(a.action().disabled,false);
// Closing a nonactive sample through the explicit action preserves the current draft and selection.
a.nodes.sql.value='SELECT latest';a.nodes.sql.setSelectionRange(2,7,'backward');a.action().click();
assert.equal(a.confirmations.length,0);assert.equal(a.nodes.sql.value,'SELECT latest');assert.equal(a.nodes.sql.selectionStart,2);assert.equal(a.nodes.sql.selectionEnd,7);assert.equal(a.nodes.sql.selectionDirection,'backward');
assert.equal(a.run('worksheets.find(w=>w.id===worksheetId).sql'),'SELECT latest');assert.equal(a.tabs().length,2);assert.equal(a.focus(),a.tabs()[1]);
// Cancellation leaves state and persisted data unchanged, restoring focus to the close action.
a.run("worksheets.find(w=>w.id==='"+ids[1]+"').sql='SELECT keep'");
const before=a.run('JSON.stringify({worksheets,worksheetId})'),stored=a.store['shirokuma.worksheets.v1'];
a.answer(false);a.select().value=ids[1];a.select().onchange();a.action().click();
assert.equal(a.run('JSON.stringify({worksheets,worksheetId})'),before);assert.equal(a.store['shirokuma.worksheets.v1'],stored);assert.equal(a.focus(),a.action());
assert.match(a.confirmations.at(-1),/removes this worksheet’s SQL/);
// Closing the active last tab chooses the remaining tab, confirms, and restores focus there.
a.answer(true);a.select().value=ids[2];a.select().onchange();a.action().click();assert.equal(a.tabs().length,1);assert.equal(a.run('worksheetId'),ids[1]);assert.equal(a.nodes.sql.value,'SELECT keep');assert.equal(a.focus(),a.tabs()[0]);
const reloaded=boot(JSON.parse(a.store['shirokuma.worksheets.v1']));assert.equal(reloaded.tabs().length,1);assert.equal(reloaded.run('worksheetId'),a.run('worksheetId'));
// Numbering follows the largest remaining number, never the tab count.
const b=boot({active:'b',tabs:[{id:'a',name:'Worksheet 1',sql:''},{id:'b',name:'Worksheet 3',sql:'persisted SQL'},{id:'c',name:'Worksheet 4',sql:''}]});
b.answer(false);b.select().value='b';b.select().onchange();b.action().click();assert.equal(b.tabs().length,3);assert.equal(b.confirmations.length,1,'persisted SQL needs confirmation');assert.equal(b.focus(),b.action());
b.answer(true);b.select().value='b';b.select().onchange();b.action().click();assert.equal(b.run('worksheetId'),'c');assert.equal(b.focus(),b.tabs()[1]);
b.nodes['add-worksheet-tab'].click();assert.equal(b.run('worksheets.at(-1).name'),'Worksheet 5');
// Keyboard tab movement receives only role=tab buttons; close remains a labeled select plus explicit action.
let prevented=0;b.tabs()[2].onkeydown({key:'Home',preventDefault(){prevented++;}});assert.equal(b.run('worksheetId'),'a');
b.tabs()[0].onkeydown({key:'End',preventDefault(){prevented++;}});assert.equal(b.run('worksheetId'),'1');
b.tabs()[2].onkeydown({key:'ArrowLeft',preventDefault(){prevented++;}});assert.equal(b.run('worksheetId'),'c');assert.equal(prevented,3);
b.select().value='a';b.select().onchange();assert.equal(b.confirmations.length,2);b.action().click();assert.equal(b.confirmations.length,2,'empty SQL closes directly');
b.run("language='ja';updateWorksheetCloseLabels()");assert.match(b.select().children[1].textContent,/閉じる/);assert.match(b.action().title,/閉じる/);
b.nodes.sql.value='編集 SQL';b.answer(false);b.select().value='c';b.select().onchange();b.action().click();assert.match(b.confirmations.at(-1),/SQL が削除/);assert.equal(b.focus(),b.action());
const c=boot(null,true);c.nodes['add-worksheet-tab'].click();c.select().value=c.run('worksheets[0].id');c.select().onchange();c.action().click();assert.equal(c.tabs().length,1);assert.match(c.nodes['query-meta'].textContent,/storage unavailable/);
'''
        result = subprocess.run(['node', '-'], input='const DICTIONARY=' + json.dumps(dictionary) + ',WIRE=' + json.dumps(wire) + ',SOURCE=' + json.dumps(source) + ';\n' + harness, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'JavaScript verification requires Node.js')
    def test_copilot_generate_preserves_origin_and_intervening_edits(self):
        script = self.workbench_document().main_script()
        helpers = script[script.index('const worksheetKey='):script.index('function worksheetName(')]
        handlers = script[script.index("for(const [id,bear,action] of [['copilot-generate'"):script.index('// Shared authorization epoch')]
        harness = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
async function check(scenario){
 const nodes={},store={},$=id=>nodes[id]??={value:'',textContent:''};
 $('sql').value='SELECT original';$('copilot-request').value='generate a query';
 let resolve,request;
 const response=new Promise(done=>resolve=done);
 const context=vm.createContext({$,t:text=>text,busy:(_node,fn)=>fn(),
  message:(id,text)=>$(id).textContent=text,
  localStorage:{getItem:key=>store[key]??null,setItem:(key,value)=>store[key]=value},
  api:(url,payload)=>{request={url,payload};return response;}});
 const run=code=>vm.runInContext(code,context);
 run(HELPERS+HANDLERS);
 run("worksheets.push({id:'survivor',name:'Worksheet 2',sql:'SELECT survivor'});saveWorksheet()");
 const pending=$('copilot-generate').onclick();
 assert.equal(request.url,'/api/bears/run');
 assert.equal(JSON.stringify(request.payload),JSON.stringify({bear:'data',action:'generate',request:'generate a query'}));
 if(scenario!=='active'&&scenario!=='edited active'){
  run("saveWorksheet();worksheetId='survivor';$('sql').value=worksheets[1].sql;saveWorksheet()");
 }
 if(scenario==='deleted')run('worksheets.splice(0,1);saveWorksheet()');
 if(scenario==='edited active')$('sql').value='SELECT edited active'; // Before an input event saves it.
 if(scenario==='edited inactive')run("worksheets[0].sql='SELECT edited inactive';saveWorksheet()");
 const active=run('worksheetId'),editor=$('sql').value;
 const result={generated_sql:'SELECT generated',detail:'Generation completed'};
 resolve(result);await pending;
 assert.equal(run('worksheetId'),active,scenario+' keeps active selection');
 assert.equal($('sql').value,scenario==='active'?result.generated_sql:editor,scenario+' editor');
 assert.equal(run("worksheets.find(w=>w.id==='survivor').sql"),'SELECT survivor',scenario+' survivor');
 const origin=run("worksheets.find(w=>w.id==='initial')");
 if(scenario==='deleted')assert.equal(origin,undefined);
 else if(scenario==='edited active')assert.equal($('sql').value,'SELECT edited active');
 else assert.equal(origin.sql,scenario==='edited inactive'?'SELECT edited inactive':result.generated_sql,scenario+' origin');
 assert.equal($('copilot-result').textContent,JSON.stringify(result,null,2),scenario+' result remains visible');
 const persisted=JSON.parse(store['shirokuma.worksheets.v1']);
 assert.equal(persisted.active,active,scenario+' persisted selection');
 if(scenario==='active'||scenario==='inactive')assert.equal(persisted.tabs[0].sql,result.generated_sql,scenario+' persisted SQL');
}
(async()=>{for(const scenario of ['active','inactive','deleted','edited active','edited inactive'])await check(scenario);})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        source = 'const HELPERS=' + json.dumps(helpers) + ',HANDLERS=' + json.dumps(handlers) + ';\n' + harness
        result = subprocess.run(['node', '-'], input=source, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_ai_settings_form_and_translations(self):
        doc = self.workbench_document()
        dialog = doc.by_id('ai-settings')
        self.assertEqual(dialog['tag'], 'section')
        self.assertEqual(dialog['aria-labelledby'], 'ai-settings-title')
        self.assertIn('hidden', dialog)
        self.assertTrue(any(n.get('id') == 'compute' for n in dialog['ancestors']))
        for identifier in ('ai-provider', 'ai-model', 'ai-url', 'ai-key', 'ai-timeout'):
            self.assertIn(dialog, doc.by_id(identifier)['ancestors'])
            self.assertTrue(any(n.get('for') == identifier for n in doc.nodes))
        self.assertEqual(doc.by_id('ai-key')['type'], 'password')
        select = doc.by_id('ai-provider')
        self.assertEqual([n['value'] for n in doc.nodes if n['tag'] == 'option' and select in n['ancestors']],
                         ['local-openai', 'openai', 'bedrock', 'custom'])
        script = doc.main_script()
        dictionary = script.split('const i18n = {ja: {', 1)[1].split('}};', 1)[0]
        translations = dict(re.findall(r"'([^']+)':'([^']+)'", dictionary))
        for label in ('AI Model Settings', 'Provider', 'Model Name', 'API URL', 'API Key',
                      'Timeout (seconds)', 'Test Connection', 'Save Settings', 'Close',
                      'Connected {latency}ms', 'Connection failed: {error}', 'Settings saved',
                      'Settings failed: {error}', 'Settings changed; test again.',
                      'Leave blank to keep the saved key or environment credential.'):
            self.assertRegex(translations[label], r'[ぁ-んァ-ヶ一-龯]')

    @unittest.skipUnless(shutil.which('node'), 'JavaScript verification requires Node.js')
    def test_ai_settings_javascript_handlers(self):
        script = self.workbench_document().main_script()
        handlers = script.split('function aiStatus(', 1)[1].split('function table(', 1)[0]
        harness = r"""
const assert=require('node:assert/strict');
new Function(SCRIPT);
const nodes={};const $=id=>nodes[id]??=( {value:'',disabled:false,hidden:false,
 addEventListener(){},focus(){},scrollIntoView(){},reportValidity(){return true;}} );
const ui=(node,key,values)=>{node.label=key;node.values=values;};
const busy=async(button,task)=>{await task();};
const calls=[];
const api=async(path,body)=>{calls.push([path,body]);
 if(path==='/api/ai/config'&&!body)return {provider:'custom',model:'test-model',api_url:'http://localhost/v1',timeout:30,api_key:'********'};
 if(path==='/api/ai/test')return {status:'connected',latency_ms:32};
 return {api_key:'********'};
};
eval('function aiStatus('+HANDLERS);
(async()=>{
 await $('ai-settings-button').onclick();
 assert.equal($('ai-settings').hidden,false);assert.equal($('ai-model').value,'test-model');
 assert.equal($('ai-key').value,'');
 await $('ai-test').onclick();
 assert.equal($('ai-config-status').label,'Connected {latency}ms');
 assert.equal($('ai-config-status').values.latency,32);
 assert.equal(calls.at(-1)[1].api_key,undefined);
 for(const id of ['ai-url','ai-provider']){
  $('ai-key').value='previous-endpoint-secret';
  $('ai-settings-form').oninput({target:{id}});
  assert.equal($('ai-key').value,'');
  await $('ai-test').onclick();
  assert.equal(calls.at(-1)[1].api_key,undefined);
 }
 $('ai-key').value='new-key';
 await $('ai-settings-form').onsubmit({preventDefault(){}});
 assert.equal(calls.at(-1)[0],'/api/ai/config');assert.equal(calls.at(-1)[1].api_key,'new-key');
 assert.equal($('ai-key').value,'');assert.equal($('ai-config-status').label,'Settings saved');
 $('ai-close').onclick();assert.equal($('ai-settings').hidden,true);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        harness = 'const SCRIPT=' + json.dumps(script) + ';const HANDLERS=' + json.dumps(handlers) + ';' + harness
        completed = subprocess.run(['node'], input=harness, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_phase8_controls_and_defaults(self):
        doc = self.workbench_document()
        controls = {
            'warehouse': 'query-schema result-view-table result-view-chart chart-type chart-x-col chart-y-col query-chart',
            'catalog': 'clone-table-btn export-table-format export-table-btn',
        }
        for section, identifiers in controls.items():
            for identifier in identifiers.split():
                self.assertIn(doc.by_id(section), doc.by_id(identifier)['ancestors'])
        options = {
            'query-schema': [('marts', 'marts'), ('raw', 'raw'), ('sandbox', 'sandbox'),
                             ('staging', 'staging'), ('tpch_tiny', 'tpch_tiny'), ('smoke', 'smoke')],
            'chart-type': [('bar', 'Bar Chart'), ('line', 'Line Chart')],
            'export-table-format': [('csv', 'CSV'), ('jsonl', 'JSONL'), ('parquet', 'Parquet')],
        }
        for identifier, expected in options.items():
            select = doc.by_id(identifier)
            self.assertEqual(select['tag'], 'select')
            self.assertEqual([(n.get('value', n['text']), n['text']) for n in doc.nodes
                              if n['tag'] == 'option' and select in n['ancestors']], expected)
        for identifier, text in {
            'result-view-table': 'Table View', 'result-view-chart': 'Chart View',
            'clone-table-btn': 'Clone to Sandbox', 'export-table-btn': 'Export Table',
        }.items():
            self.assertEqual(doc.by_id(identifier)['text'], text)
        self.assertEqual(doc.by_id('result-view-table')['aria-pressed'], 'true')
        self.assertEqual(doc.by_id('result-view-chart')['aria-pressed'], 'false')
        self.assertIn('hidden', doc.by_id('query-chart'))
        for identifier in ('clone-table-btn', 'export-table-btn'):
            self.assertIn('disabled', doc.by_id(identifier))
        self.assertEqual(doc.by_id('export-table-format')['aria-label'], 'Export format')
        for identifier in ('query-schema', 'chart-type', 'chart-x-col', 'chart-y-col'):
            self.assertTrue(any(n.get('for') == identifier for n in doc.nodes))

    def test_phase8_translations_cover_static_and_dynamic_ui(self):
        doc = self.workbench_document()
        script = doc.main_script()
        dictionary = script.split('const i18n = {ja: {', 1)[1].split('}};', 1)[0]
        translations = dict(re.findall(r"'([^']+)':'([^']+)'", dictionary))
        labels = '''Schema|Table View|Chart View|Chart type|Bar Chart|Line Chart|X column|Y column|
            Clone to Sandbox|Clone Snapshot|Export format|Export Table|
            Result chart|
            Choose a numeric Y column to chart visible rows.|Clone audit needs review|
            Cloned {source} to {table}.|Clone failed: {error}|Exported {table} ({row_count} / {total_rows} rows) to {filename}.|
            Export failed: {error}'''
        for label in labels.split('|'):
            with self.subTest(label=label.strip()):
                self.assertRegex(translations[label.strip()], r'[ぁ-んァ-ヶ一-龯]')
        for label in ('CSV', 'JSONL', 'Parquet'):
            self.assertEqual(translations[label], label)

    @unittest.skipUnless(shutil.which('node'), 'JavaScript verification requires Node.js')
    def test_phase8_javascript_execution_contracts(self):
        """Execute production handlers with a small DOM/API adapter; no lab writes."""
        script = self.workbench_document().main_script()
        # Parse the entire script, then execute the query handler itself.
        handler = script.split("$('run-query').onclick=", 1)[1].split("$('sql').onkeydown", 1)[0]
        harness = r"""
const assert=require('node:assert/strict');
new Function(SCRIPT);
const nodes={sql:{value:'SELECT 1; SELECT 2',selectionStart:10,selectionEnd:18},
 'query-schema':{value:'raw'},'query-mode':{value:'read-only'},
 'query-warehouse':{value:'LOCAL'},'max-rows':{value:'1000'},
 'result-filter':{},'query-results':{replaceChildren(){}}};
const $=id=>nodes[id]??{};let payload,queryResult,sortColumn;
const busy=(button,task)=>task(),ui=()=>{},message=()=>{},renderResults=()=>{},
 populateChartColumns=()=>{},rememberQuery=()=>{};
const api=async(path,body)=>{assert.equal(path,'/api/warehouse/query');payload=body;return {rows:[]};};
const run=eval(HANDLER);
(async()=>{
 await run();assert.equal(payload.sql,'SELECT 2');assert.equal(payload.schema,'raw');assert.ok(!('mode' in payload));
 nodes['query-schema'].value='marts';nodes.sql.selectionStart=nodes.sql.selectionEnd=0;
 await run();assert.equal(payload.sql,nodes.sql.value);assert.ok(!('schema' in payload));
 nodes.sql.value='   SELECT 3';nodes.sql.selectionStart=0;nodes.sql.selectionEnd=3;
 nodes['query-mode'].value='sandbox-write';await run();assert.equal(payload.sql,nodes.sql.value);assert.equal(payload.mode,'sandbox-write');
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
        source = 'const SCRIPT=' + json.dumps(script) + ';const HANDLER=' + json.dumps(handler) + ';\n' + harness
        result = subprocess.run(['node'], input=source, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'JavaScript verification requires Node.js')
    def test_result_sorting_keeps_header_focus_and_updates_only_current_aria_sort(self):
        script = self.workbench_document().main_script()
        table_source = script[script.index('function table('):script.index('let statusEpoch=')]
        results_source = script[script.index('let queryResult=null'):script.index('function download(')]
        harness = r'''
const assert=require('node:assert/strict');
let activeElement=null;
class Element{
 constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.attrs={};this.className='';this.textContent='';this.value='';this.cells=[];this.rows=[];}
 append(...nodes){for(const node of nodes){if(node.parentElement){node.parentElement.children=node.parentElement.children.filter(child=>child!==node);}node.parentElement=this;this.children.push(node);if(this.tagName==='TR'&&['TH','TD'].includes(node.tagName)&&!this.cells.includes(node))this.cells.push(node);}}
 prepend(...nodes){for(const node of [...nodes].reverse()){node.parentElement=this;this.children.unshift(node);}}
 replaceChildren(...nodes){this.children=[];this.textContent='';this.append(...nodes);}
 setAttribute(key,value){this.attrs[key]=String(value);}
 getAttribute(key){return this.attrs[key];}
 removeAttribute(key){delete this.attrs[key];}
 createTHead(){const head=new Element('thead');this.append(head);return head;}
 createTBody(){const body=new Element('tbody');this.append(body);return body;}
 get tHead(){return this.children.find(child=>child.tagName==='THEAD');}
 get tBodies(){return this.children.filter(child=>child.tagName==='TBODY');}
 insertRow(){const row=new Element('tr');this.append(row);this.rows.push(row);return row;}
 insertCell(){const cell=new Element('td');this.append(cell);return cell;}
 focus(){activeElement=this;}
 click(){this.onclick?.();}
 querySelector(selector){
  if(selector.startsWith('.'))return this.descendants().find(node=>node.className.split(' ').includes(selector.slice(1)));
  return this.descendants().find(node=>node.tagName===selector.toUpperCase());
 }
 querySelectorAll(selector){return this.descendants().filter(node=>node.tagName===selector.toUpperCase());}
 descendants(){return this.children.flatMap(child=>[child,...child.descendants()]);}
}
const nodes={};
for(const id of ['query-results','query-chart','result-filter','export-csv','export-json','copy-tsv'])nodes[id]??=Object.assign(new Element('div'),{id});
const $=id=>nodes[id]??=Object.assign(new Element('div'),{id});
const document={createElement:tag=>new Element(tag),get activeElement(){return activeElement;}};
const t=key=>key,ui=(node,key)=>{node.textContent=key;},charts=[],renderChart=()=>charts.push(visibleRows().map(row=>row.slice()));
''' + table_source + '\n' + results_source + r'''
queryResult={columns:[{name:'name',type:'text'},'n','payload'],rows:[['b',2,{x:1}],['a',null,{x:2}],['a',1,null],['c',3,{x:1}]]};sortColumn=-1;renderResults();
const container=$('query-results'),grid=container.querySelector('table'),caption=grid.querySelector('caption');
assert.equal(caption.textContent,'Query results');
const headers=[...grid.tHead.rows[0].cells],buttons=headers.map(th=>th.children[0]);
assert.deepEqual(buttons.map(button=>button.getAttribute('aria-label')),['name','n','payload']);
assert.equal(headers.some(th=>Object.hasOwn(th.attrs,'aria-sort')),false);
assert.deepEqual(grid.tBodies[0].children.map(row=>row.children[0].textContent),['b','a','a','c']);
const trs=[...grid.tBodies[0].children],cells=trs.map(row=>[...row.children]);
const first=buttons[0],firstHeader=headers[0];first.focus();
first.click();
assert.equal(document.activeElement,first);assert.equal(buttons[0],first);assert.equal(headers[0],firstHeader);
assert.equal(firstHeader.getAttribute('aria-sort'),'ascending');assert.equal(headers[1].getAttribute('aria-sort'),undefined);
assert.equal(first.children[0].textContent,'name');assert.equal(first.children[1].textContent,' ↑');
assert.deepEqual(grid.tBodies[0].children.map(row=>row.children[0].textContent),['a','a','b','c']);
assert.deepEqual(grid.tBodies[0].children.map(row=>row.children[1].textContent),['NULL','1','2','3']);
assert.deepEqual([...grid.tBodies[0].children],[trs[1],trs[2],trs[0],trs[3]]);
assert.deepEqual(charts.at(-1).map(row=>row[0]),['a','a','b','c']);
assert.deepEqual(trs.map(row=>[...row.children]),cells);
assert.equal(grid.tBodies[0].children[0],trs[1]);assert.equal(grid.tBodies[0].children[1],trs[2]);
first.click();
assert.equal(document.activeElement,first);assert.equal(firstHeader.getAttribute('aria-sort'),'descending');
assert.deepEqual(grid.tBodies[0].children.map(row=>row.children[0].textContent),['c','b','a','a']);
assert.deepEqual(charts.at(-1).map(row=>row[0]),['c','b','a','a']);
assert.deepEqual([...grid.tBodies[0].children],[trs[3],trs[0],trs[1],trs[2]]);
const second=buttons[1],secondHeader=headers[1];second.focus();second.click();
assert.equal(document.activeElement,second);assert.equal(firstHeader.getAttribute('aria-sort'),undefined);assert.equal(secondHeader.getAttribute('aria-sort'),'ascending');
assert.equal(headers.filter(th=>Object.hasOwn(th.attrs,'aria-sort')).length,1);
assert.deepEqual(grid.tBodies[0].children.map(row=>row.children[1].textContent),['NULL','1','2','3']);
assert.deepEqual(charts.at(-1).map(row=>row[0]),['a','a','b','c']);
assert.deepEqual([...grid.tBodies[0].children],[trs[1],trs[2],trs[0],trs[3]]);
$('result-filter').value='nothing';renderResults();
assert.deepEqual(charts.at(-1),[]);
assert.equal(container.querySelector('table'),undefined);
assert.equal(container.children[0].className,'empty');assert.equal(container.children[0].textContent,'No rows returned.');
$('result-filter').value='a';renderResults();
const filtered=container.querySelector('table'),filteredHeader=filtered.tHead.rows[0].cells[1];
assert.notEqual(filtered,grid);assert.equal(filtered.querySelector('caption').textContent,'Query results');
assert.equal(filteredHeader.getAttribute('aria-sort'),'ascending');assert.equal(filtered.tHead.rows[0].cells[0].getAttribute('aria-sort'),undefined);
assert.deepEqual(filtered.tBodies[0].children.map(row=>row.children[0].textContent),['a','a']);
queryResult={columns:['x'],rows:[['z'],['y']]};sortColumn=-1;$('result-filter').value='';renderResults();
const replacement=container.querySelector('table'),newHeader=replacement.tHead.rows[0].cells[0];
assert.notEqual(replacement,filtered);assert.equal(replacement.querySelector('caption').textContent,'Query results');
assert.equal(Object.hasOwn(newHeader.attrs,'aria-sort'),false);
assert.deepEqual(replacement.tBodies[0].children.map(row=>row.children[0].textContent),['z','y']);
assert.equal(['export-csv','export-json','copy-tsv'].every(id=>$(id).disabled===false),true);
queryResult={columns:['n','text'],rows:[[1,'zz_match'],[1,'aa_match'],[1,'mm_match']]};sortColumn=-1;$('result-filter').value='';renderResults();
let canonical=container.querySelector('table');
canonical.tHead.rows[0].cells[1].children[0].click();
assert.deepEqual(canonical.tBodies[0].children.map(row=>row.children[1].textContent),['aa_match','mm_match','zz_match']);
$('result-filter').value='match';renderResults();
canonical=container.querySelector('table');
assert.deepEqual(canonical.tBodies[0].children.map(row=>row.children[1].textContent),['aa_match','mm_match','zz_match']);
const numeric=canonical.tHead.rows[0].cells[0].children[0];numeric.focus();numeric.click();
assert.equal(document.activeElement,numeric);
assert.deepEqual(canonical.tBodies[0].children.map(row=>row.children[1].textContent),['zz_match','aa_match','mm_match']);
assert.deepEqual(visibleRows().map(row=>row[1]),['zz_match','aa_match','mm_match']);
assert.deepEqual(charts.at(-1).map(row=>row[1]),['zz_match','aa_match','mm_match']);
assert.equal(delimited(','),'"n","text"\r\n"1","zz_match"\r\n"1","aa_match"\r\n"1","mm_match"');
'''
        result = subprocess.run(['node', '-'], input=harness, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_narrow_form_controls_use_readable_text_and_contain_page_overflow(self):
        doc = self.workbench_document()
        css = WorkbenchSource(doc.html).css
        narrow = '\n'.join(re.findall(r'@media\(max-width:760px\)\{(.*?)\n\}', css, re.S))
        self.assertNotIn('html,body{max-width:100%;overflow-x:hidden}', narrow)
        self.assertIn(':is(input:not([type=checkbox]):not([type=radio]),select,textarea){font-size:1rem}', narrow)
        for selector in ('.editor', '#database-workspace .explorer-search input',
                         '#database-workspace .explorer-foot select',
                         '#database-context-bar select', '#database-context-bar input'):
            with self.subTest(selector=selector):
                self.assertIn(selector, narrow)
        # Desktop typography remains compact outside the narrow media query.
        self.assertIn('.editor{display:block;width:100%;min-height:200px;height:clamp(200px,32vh,420px);margin:0;padding:12px 14px;border:0;border-radius:0;background:var(--surface);font-size:13px', css)
        self.assertIn('body{margin:0;background:var(--bg);color:var(--ink);font:13px/1.5 var(--sans)', css)
        for identifier in ('job-name',):
            with self.subTest(pattern=identifier):
                pattern = doc.by_id(identifier)['pattern']
                self.assertIn('[a-z0-9_\\-]', pattern)
                self.assertNotIn('[a-z0-9_-]', pattern)
        results = doc.by_id('query-results')
        self.assertEqual(results['role'], 'region')
        self.assertEqual(results['tabindex'], '0')
        self.assertEqual(results['aria-label'], 'Query results')

    def test_top_level_pages_have_one_descriptive_h1(self):
        doc = self.workbench_document()
        for name in ('warehouse', 'catalog', 'ingestion', 'transformation', 'jobs',
                     'bears', 'db-admin', 'pawprints', 'compute'):
            with self.subTest(page=name):
                section = doc.by_id(name)
                headings = [node for node in doc.nodes
                            if node['tag'] == 'h1' and section in node['ancestors']]
                self.assertEqual(len(headings), 1)
                self.assertTrue(headings[0]['text'].strip())

    def test_phase8_api_and_rendering_contracts(self):
        script = self.workbench_document().main_script()
        for contract in (
            "createElementNS(ns,'svg')", "const points=visibleRows().filter",
            "for(const id of ['chart-type','chart-x-col','chart-y-col'])$(id).onchange=renderChart",
            "$('query-results').hidden=view!=='table'", "$('query-chart').hidden=view!=='chart'",
            "const payload={source_table:source}", 'payload.snapshot_id=snapshot',
            "api('/api/catalog/clone',payload)", "actionButton('Clone Snapshot'",
            "api('/api/lake/export',{table:selected,format})", 'atob(result.content_base64)',
            'downloadFile(content,result.filename', 'link.download=filename',
            'row_count:result.row_count,total_rows:result.total_rows',
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, script)
        for action in ('lake.ingest', 'lake.export', 'catalog.clone', 'catalog.drop', 'bear.data_engineer'):
            self.assertNotIn("'" + action + "'", script)

    def workbench_document(self):
        document = WorkbenchHTML(wb.INDEX.read_text())
        ids = [n['id'] for n in document.nodes if 'id' in n]
        self.assertEqual(len(ids), len(set(ids)), 'HTML IDs must remain unique')
        return document

    def test_snowsight_navigation_contract(self):
        doc = self.workbench_document()
        navigation = doc.by_id('workbench-nav')
        self.assertEqual(navigation['tag'], 'aside')
        self.assertEqual(navigation['class'], 'sidebar')
        self.assertEqual(navigation['aria-label'], 'Main navigation')
        self.assertNotIn('role', navigation)
        navtitles = [n['text'].strip() for n in doc.nodes
                     if 'navtitle' in (n.get('class') or '').split() and navigation in n['ancestors']]
        self.assertEqual(navtitles, ['Administration', 'Favorites', 'Workspace'])
        expected = [
            ('home', 'Home', '#home'),
            ('catalog', 'Data Explorer', '#explore'),
            ('warehouse', 'SQL Worksheets', '#sql'),
            ('ingestion', 'Ingestion & Stages', '#ingestion'),
            ('transformation', 'Transformation', '#transformation'),
            ('jobs', 'Jobs', '#jobs'),
            ('bears', 'AI Bears', '#bears'),
            ('db-admin', 'Database Management', '#databases'),
            ('pawprints', 'Audit log', '#audit'),
            ('compute', 'Compute & Governance', '#compute'),
        ]
        links = [n for n in doc.nodes if 'data-tab' in n]
        self.assertEqual([n['data-tab'] for n in links], [name for name, _, _ in expected])
        self.assertTrue(all(n['tag'] == 'a' for n in links))
        for name, label, href in expected:
            with self.subTest(tab=name):
                link = doc.by_id('tab-' + name)
                section = doc.by_id(name)
                self.assertEqual(link['href'], href)
                self.assertIn(label, link['text'])
                self.assertEqual(link['aria-controls'], name)
                self.assertNotIn('role', link)
                self.assertNotIn('aria-selected', link)
                self.assertEqual(section['tag'], 'section')
                self.assertNotIn('role', section)
                self.assertEqual('hidden' in section, name != 'home')
        current = [n for n in links if n.get('aria-current') == 'page']
        self.assertEqual([n['id'] for n in current], ['tab-home'])
        self.assertIn('hidden', doc.by_id('tab-db-admin'))
        identifiers = {n.get('id') for n in doc.nodes}
        for identifier in ('legacy-header-warehouse', 'quick-warehouse-state', 'quick-wake', 'quick-chill', 'quick-status-error'):
            self.assertNotIn(identifier, identifiers)
        entry = doc.by_id('database-compute-status')
        self.assertEqual(entry['tag'], 'button')
        self.assertEqual(entry['aria-controls'], 'compute')
        self.assertNotIn('hidden', entry)
        self.assertTrue(any(n['tag'] == 'header' for n in entry['ancestors']))
        self.assertIn('database-compute-state', identifiers)
        for identifier in ('workbench-command-button', 'workbench-open-sql', 'workbench-environment'):
            self.assertTrue(any(n.get('id') == 'workbench-header' for n in doc.by_id(identifier)['ancestors']), identifier)
        for identifier in ('workbench-alerts-button', 'user-menu', 'role-selector', 'lang-en', 'lang-ja', 'theme-light', 'theme-dark'):
            self.assertTrue(any(n.get('id') == 'workbench-nav' for n in doc.by_id(identifier)['ancestors']), identifier)
        open_sql = doc.by_id('workbench-open-sql')
        self.assertEqual(open_sql['tag'], 'a')
        self.assertEqual(open_sql['text'], 'Open SQL')
        self.assertIn('primary', open_sql['class'].split())
        self.assertEqual(open_sql['href'], '#sql')
        self.assertEqual(doc.by_id('main')['tag'], 'main')
        self.assertFalse(any('environment' in (n.get('class') or '').split() for n in doc.nodes))
        self.assertFalse(any('nav-note' in (n.get('class') or '').split() for n in doc.nodes))

    def test_workbench_controls_belong_to_their_workspaces(self):
        doc = self.workbench_document()
        controls = {
            'warehouse': '''warehouse-state replicas chill wake refresh-status components status-error
                sql query-warehouse query-mode max-rows run-query query-meta query-error query-results
                worksheet-explorer worksheet-tree-filter worksheet-tabs add-worksheet-tab query-history
                copilot-request copilot-generate copilot-explain open-transformation
                result-filter export-csv export-json copy-tsv''',
            'ingestion': '''ingestion-heading ingestion-role-field atlas-ingestion-message ingestion-method
                ingestion-file ingestion-stages''',
            'transformation': '''transformation-heading transformation-role-field atlas-transformation-message
                transformation-atlas-models transformation-publications transformation-atlas-marts
                transformation-atlas-dbt''',
            'catalog': '''refresh-catalog catalog-error catalog-tree catalog-filter table-title
                table-columns open-table-sql drop-table-btn table-preview table-ddl
                table-snapshots snapshot-preview table-lineage''',
            'bears': 'bears-atlas bears-heading bears-form bears-context-fields bears-target-fields bears-sql-field bears-toolbar bears-message bears-explanation bears-proposal bears-curator bears-policy',
            'compute': 'compute-atlas compute-heading compute-runtime-host compute-usage compute-auto-chill compute-diagnostics ai-settings',
        }
        for section, identifiers in controls.items():
            for identifier in identifiers.split():
                with self.subTest(control=identifier):
                    self.assertIn(doc.by_id(section), doc.by_id(identifier)['ancestors'])
        for identifier, label in [('export-csv', 'Download CSV'), ('export-json', 'Download JSON'),
                                  ('copy-tsv', 'Copy TSV')]:
            self.assertEqual(doc.by_id(identifier)['text'], label)

    def test_table_inspector_contract(self):
        doc = self.workbench_document()
        expected = [('columns', 'Columns'), ('preview', 'Data Preview'),
                    ('profile', 'Profile & NULL Stats'), ('ddl', 'DDL'),
                    ('snapshots', 'Snapshots & Time Travel'), ('lineage', 'Table Lineage')]
        tabs = [n for n in doc.nodes if 'data-inspect' in n]
        self.assertEqual([(n['data-inspect'], n['text']) for n in tabs], expected)
        for name, _ in expected:
            tab, panel = doc.by_id('inspect-tab-' + name), doc.by_id('inspect-' + name)
            self.assertEqual(tab['role'], 'tab')
            self.assertEqual(tab['aria-controls'], panel['id'])
            self.assertEqual(panel['role'], 'tabpanel')
            self.assertEqual(panel['aria-labelledby'], tab['id'])
            self.assertEqual('hidden' in panel, name != 'columns')
        self.assertIn('hidden', doc.by_id('drop-table-btn'))

    def test_bilingual_language_switcher_contract(self):
        doc = self.workbench_document()
        switcher = doc.by_id('lang-switcher')
        self.assertEqual((switcher['role'], switcher['aria-label']), ('group', 'Language'))
        profile = doc.by_id('user-menu')
        self.assertIn(profile, switcher['ancestors'])
        environment = doc.by_id('workbench-environment')
        header = doc.by_id('workbench-header')
        self.assertIn(header, environment['ancestors'])
        self.assertIn('sample', environment['class'].split())
        for lang, label, pressed in [('en', 'EN', 'true'), ('ja', '日本語', 'false')]:
            button = doc.by_id('lang-' + lang)
            self.assertIn(switcher, button['ancestors'])
            self.assertIn(profile, button['ancestors'])
            self.assertEqual((button['tag'], button['data-lang'], button['aria-pressed'], button['text']),
                             ('button', lang, pressed, label))
        self.assertEqual(next(n for n in doc.nodes if n['tag'] == 'html')['lang'], 'en')

        script = doc.main_script()
        # Evaluate the pure translation declarations, including Database-mode additions.
        # No page handlers or browser APIs run in this isolated dictionary check.
        dictionary = script[script.index('const i18n = '):script.index("let language = 'en';")]
        # Include the label constants used as computed translation keys, in source order.
        additions = re.findall(
            r'const (?:dbCreationGuidance|dbPlanHelp|dbWorkflowHelp|dbExplanation(?:Help|Lead|Instruction|ContextStep|Fence))=[^\n]*;'
            r'|Object\.assign\(i18n\.ja,\{[\s\S]*?\}\);', script)
        result = subprocess.run(['node', '-'], input=dictionary + '\n' + '\n'.join(additions)
                                 + '\nprocess.stdout.write(JSON.stringify(i18n.ja));',
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        translations = json.loads(result.stdout)
        expected = {
            'Catalog': 'カタログ',
            'SQL Worksheets': 'SQL ワークシート',
            'Ingestion & Stages': '取り込み・ステージ',
            'Transformation': 'データ変換',
            'AI Bears': 'AI Bears',
            'Data Explorer': 'データ探索',
            'Database Management': 'データベース管理',
            'Administration': '運用管理', 'Workspace': 'ワークスペース',
            'Open SQL': 'SQL を開く',
            'Audit Log': '監査ログ',
            'Compute & Governance': 'コンピュート & ガバナンス',
            'Columns': 'カラム定義', 'Data Preview': 'データプレビュー',
            'Profile & NULL Stats': 'プロファイル & NULL統計', 'DDL': 'DDL 定義',
            'Snapshots & Time Travel': 'スナップショット & Time Travel',
            'Table Lineage': 'テーブルリネージ',
        }
        for english, japanese in expected.items():
            with self.subTest(translation=english):
                self.assertEqual(translations[english], japanese)
        # Every section's title and description, plus all specialist copy, is covered.
        # Product and specialist names stay in Latin script; every other title and description is Japanese.
        for node in doc.nodes:
            if node.get('class') == 'page-title':
                for child in doc.nodes:
                    if (node in child['ancestors'] and child['tag'] in {'h1', 'p'}
                            and child['text'].strip() and child['text'] != 'AI Bears'):
                        self.assertRegex(translations[child['text']], r'[ぁ-んァ-ヶ一-龯]')
        for title, description in re.findall(r"name:'([^']+)',description:'([^']+)'", script):
            self.assertIn(title, translations)
            self.assertIn(description, translations)
        self.assertEqual(len(re.findall(r"key:'(?:sre|query|data|catalog|policy)',name:", script)), 5)
        self.assertIn("languageKey = 'shirokuma.lang.v1'", script)
        self.assertIn('localStorage.getItem(languageKey)', script)
        self.assertIn('localStorage.setItem(languageKey, lang)', script)
        self.assertIn("navigator.language?.toLowerCase().startsWith('ja')", script)
        self.assertIn('document.documentElement.lang = lang', script)
        for lang in ('en', 'ja'):
            self.assertIn(f"$('lang-{lang}').onclick = () => setLanguage('{lang}')", script)


if __name__ == "__main__":
    unittest.main()
