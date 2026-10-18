"""Behavior checks for saved refresh targets in the existing Transformation panel."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest
from html.parser import HTMLParser

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "tools/local_experiment/workbench/index.html"


class Document(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.nodes = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.nodes.append(dict(attrs, tag=tag))


@unittest.skipUnless(shutil.which("node"), "Requires Node.js")
class MartsUITests(unittest.TestCase):
    def test_markup_translations_and_script_syntax(self):
        source = HTML.read_text()
        document = Document(source)
        ids = [node.get("id") for node in document.nodes if node.get("id")]
        self.assertEqual(len(ids), len(set(ids)))
        for identifier in ["mart-name", "mart-sql", "mart-min-rows", "mart-not-null"]:
            self.assertTrue(any(node.get("for") == identifier for node in document.nodes))
        self.assertIn('id="marts-admin" hidden', source)
        script = re.search(r"<script>(.*?)</script>", source, re.S)[1]
        dictionary = "const i18n = " + script.split("const i18n = ", 1)[1].split("function t(", 1)[0]
        # Parse the entire script and verify both languages for the new controls.
        result = subprocess.run(["node", "-e", "new Function(" + json.dumps(script) + ");" + dictionary + "console.log(JSON.stringify(i18n))"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        translations = json.loads(result.stdout)
        for key in ["Registered refresh targets", "Save target", "Refresh target", "Last successful refresh", "Inspect run in Jobs", "Minimum rows", "SELECT definition"]:
            self.assertIn(key, translations["en"])
            self.assertIn(key, translations["ja"])
            self.assertNotEqual(translations["ja"][key], key)

    def test_registered_targets_hide_legacy_refresh(self):
        script = re.search(r"<script>(.*?)</script>", HTML.read_text(), re.S)[1]
        helpers = "function managedMartModel" + script.split("function managedMartModel", 1)[1].split("function selectModel", 1)[0]
        source = """
const assert=require('node:assert/strict');const button={hidden:false};const $=()=>button;
let selectedModel={name:'iceberg.marts.sales',layer:'wap',registered_refresh_target:true};
""" + helpers + """
updateLegacyRefresh();assert.equal(button.hidden,true);
selectedModel={name:'sales',layer:'wap',registered_refresh_target:true};updateLegacyRefresh();assert.equal(button.hidden,true);
selectedModel={name:'legacy_sales',layer:'wap'};updateLegacyRefresh();assert.equal(button.hidden,false);
selectedModel={name:'legacy_sales',layer:'dbt'};updateLegacyRefresh();assert.equal(button.hidden,true);
"""
        result = subprocess.run(["node", "-e", source], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_actual_handlers_preserve_drafts_and_gate_execution(self):
        script = re.search(r"<script>(.*?)</script>", HTML.read_text(), re.S)[1]
        handlers = "let martEntries=" + script.split("let martEntries=", 1)[1].split("let selectedModel=null;", 1)[0]
        harness = r'''
const assert=require('node:assert/strict');
const nodes={};const $=id=>nodes[id]??={value:'',hidden:false,disabled:false,textContent:'',replaceChildren(){this.textContent='';},querySelectorAll(){return [];},reset(){for(const id of ['mart-name','mart-sql','mart-not-null'])$(id).value='';$('mart-min-rows').value=1;},reportValidity(){return true;}};
const t=(key,values={})=>key.replace(/\{(\w+)\}/g,(_,k)=>values[k]??'');
const ui=(node,key,values={})=>{node.textContent=t(key,typeof values==='function'?values():values);};
const message=(id,text)=>{$(id).hidden=!text;$(id).textContent=text;};
const table=()=>{};const actionButton=()=>({});const pruneUIBindings=()=>{};
let currentRole='admin',activeTab='transformation',selectedRunId=null,selectedModel=null;const document={hidden:false};
const liveJobStates=new Set(['queued','running']);const jobStatuses={success:'Success',failed:'Failed',unknown:'Unknown',queued:'Queued',running:'Running'};const jobStatus=status=>jobStatuses[status]||'Unknown';
const activate=()=>{};
let calls=[],reply={marts:[]},resolveReply=null,apiOverride=null;
const api=async(path,body)=>{calls.push({path,body});if(apiOverride)return apiOverride(path,body);if(resolveReply==='defer')return new Promise(resolve=>{resolveReply=resolve;});return reply;};
const entry={name:'sales',table:'iceberg.marts.sales',sql:'SELECT 1 AS id',min_rows:1,not_null:['id'],version:4,owner:'operator',can_manage:true,last_run:null};
(async()=>{
 eval(HANDLERS+`;globalThis.handlers={loadMarts,resetMartsUI,selectMart,readMartDefinition,getSelected:()=>selectedMart,getDirty:()=>martDirty};`);
 const h=globalThis.handlers;
 h.selectMart(entry);assert.equal($('mart-run').disabled,false);
 $('mart-sql').value='SELECT 2 AS id';$('mart-form').oninput();assert.equal($('mart-run').disabled,true);
 reply={marts:[{...entry,last_run:{id:'r1',status:'success'}}]};await h.loadMarts();assert.equal($('mart-sql').value,'SELECT 2 AS id');assert.equal(h.getDirty(),true);
 reply={marts:[{...entry,version:5}]};await h.loadMarts();assert.equal($('mart-save').disabled,true);assert.equal($('mart-run').disabled,true);
 h.selectMart({...entry,can_manage:false});assert.equal($('mart-save').disabled,true);assert.equal($('mart-sql').disabled,true);
 h.selectMart(entry);$('mart-min-rows').value='1.5';assert.throws(h.readMartDefinition,/positive integer/);
 $('mart-min-rows').value='0';assert.throws(h.readMartDefinition,/positive integer/);$('mart-min-rows').value='1';$('mart-not-null').value='id, country, ';assert.deepEqual(h.readMartDefinition().not_null,['id','country']);
 reply={mart:{...entry,version:5,min_rows:1}};calls=[];
 $('mart-form').onsubmit({preventDefault(){}});await new Promise(resolve=>setImmediate(resolve));
 assert.equal(calls[0].path,'/api/marts/save');assert.equal(calls[0].body.version,4);assert.equal(calls[0].body.min_rows,1);assert.equal(calls.some(c=>c.path==='/api/marts/run'),false);
 h.selectMart(entry);reply={run:{id:'r2',status:'queued'},marts:[{...entry,last_run:{id:'r2',status:'queued'}}]};calls=[];
 $('mart-run').onclick();await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(calls[0],{path:'/api/marts/run',body:{name:'sales',version:4}});assert.equal($('mart-run').disabled,true);assert.equal($('mart-view-run').hidden,false);
 // Keep the save busy through its delayed registry reload. A refresh cannot start,
 // and neither New target nor selection may replace the saved target mid-action.
 h.resetMartsUI();h.selectMart(entry);calls=[];let finishReload;
 apiOverride=async path=>path==='/api/marts/save'?{mart:{...entry,version:5}}:new Promise(resolve=>{finishReload=resolve;});
 $('mart-form').onsubmit({preventDefault(){}});await new Promise(resolve=>setImmediate(resolve));
 assert.equal($('mart-save').disabled,true);assert.equal($('mart-run').disabled,true);assert.equal($('mart-new').disabled,true);
 $('mart-new').onclick();h.selectMart({...entry,name:'other'});$('mart-run').onclick();
 assert.equal(h.getSelected().name,'sales');assert.equal(calls.some(c=>c.path==='/api/marts/run'),false);
 finishReload({marts:[{...entry,version:5}]});await new Promise(resolve=>setImmediate(resolve));assert.equal($('mart-run').disabled,false);
 let finishRun;apiOverride=async path=>path==='/api/marts/run'?new Promise(resolve=>{finishRun=resolve;}):{marts:[{...entry,version:5,last_run:{id:'held-run',status:'queued'}}]};
 $('mart-run').onclick();await new Promise(resolve=>setImmediate(resolve));h.selectMart({...entry,name:'other'});assert.equal(h.getSelected().name,'sales');
 finishRun({run:{id:'held-run',status:'queued'}});await new Promise(resolve=>setImmediate(resolve));assert.equal(h.getSelected().name,'sales');assert.equal(h.getSelected().last_run.id,'held-run');apiOverride=null;
 h.resetMartsUI();h.selectMart(entry);resolveReply='defer';const pending=h.loadMarts();const resolve=resolveReply;
 currentRole='bi_viewer';h.resetMartsUI();resolve({marts:[entry]});await pending;assert.equal(h.getSelected(),null);assert.equal($('marts-admin').hidden,true);assert.equal($('marts-list').textContent,'');
 const before=calls.length;await h.loadMarts();assert.equal(calls.length,before);
 currentRole='admin';activeTab='warehouse';h.resetMartsUI();h.selectMart(null);$('mart-name').value='new_mart';$('mart-sql').value='SELECT 1';reply={mart:{...entry,name:'new_mart'}};resolveReply=null;calls=[];
 $('mart-form').onsubmit({preventDefault(){}});await new Promise(resolve=>setImmediate(resolve));assert.equal(Object.hasOwn(calls[0].body,'version'),false);
 console.log('handler behavior passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run(["node", "-e", "const HANDLERS=" + json.dumps(handlers) + ";" + harness], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("handler behavior passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
