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
    def test_result_sorting_keeps_header_focus_and_updates_only_current_aria_sort(self):
        script = self.workbench_document().main_script()
        table_source = script[script.index('function table('):script.index('function syncRunLabel(')]
        results_source = script[script.index('let queryResult=null'):script.index('let download;')]
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
