"""Workbench HTTP contracts, with no resident lab required."""
import http.client
import base64
import io
from email.message import Message
from contextlib import contextmanager
from http.server import HTTPServer
from html.parser import HTMLParser
import json
import re
import shutil
import subprocess
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/local_experiment"))
import workbench_server as wb


class WorkbenchHTML(HTMLParser):
    """Inspect the served document, including panel ownership and tab labels."""

    def __init__(self, html):
        super().__init__()
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


class WorkbenchTests(unittest.TestCase):
    def test_phase8_controls_and_defaults(self):
        doc = self.workbench_document()
        controls = {
            'warehouse': 'query-schema result-view-table result-view-chart chart-type chart-x-col chart-y-col query-chart',
            'catalog': 'clone-table-btn export-table-format export-table-btn',
            'compute': 'auto-chill-minutes save-auto-chill auto-chill-state',
            'pawprints': 'refresh-history runtime-queries-results load-history-results',
        }
        for section, identifiers in controls.items():
            for identifier in identifiers.split():
                self.assertIn(doc.by_id(section), doc.by_id(identifier)['ancestors'])
        options = {
            'query-schema': [('marts', 'marts'), ('raw', 'raw'), ('sandbox', 'sandbox'),
                             ('staging', 'staging'), ('tpch_tiny', 'tpch_tiny'), ('smoke', 'smoke')],
            'chart-type': [('bar', 'Bar Chart'), ('line', 'Line Chart')],
            'export-table-format': [('csv', 'CSV'), ('jsonl', 'JSONL'), ('parquet', 'Parquet')],
            'auto-chill-minutes': [('0', 'Off'), ('5', '5m'), ('15', '15m'), ('30', '30m'), ('60', '60m')],
        }
        for identifier, expected in options.items():
            select = doc.by_id(identifier)
            self.assertEqual(select['tag'], 'select')
            self.assertEqual([(n.get('value', n['text']), n['text']) for n in doc.nodes
                              if n['tag'] == 'option' and select in n['ancestors']], expected)
        for identifier, text in {
            'result-view-table': 'Table View', 'result-view-chart': 'Chart View',
            'clone-table-btn': 'Clone to Sandbox', 'export-table-btn': 'Export Table',
            'save-auto-chill': 'Apply Auto-Chill', 'refresh-history': 'Refresh History',
            'auto-chill-state': 'Auto-Chill: Off',
        }.items():
            self.assertEqual(doc.by_id(identifier)['text'], text)
        self.assertEqual(doc.by_id('result-view-table')['aria-pressed'], 'true')
        self.assertEqual(doc.by_id('result-view-chart')['aria-pressed'], 'false')
        self.assertIn('hidden', doc.by_id('query-chart'))
        for identifier in ('clone-table-btn', 'export-table-btn'):
            self.assertIn('disabled', doc.by_id(identifier))
        self.assertEqual(doc.by_id('export-table-format')['aria-label'], 'Export format')
        self.assertEqual(doc.by_id('auto-chill-state')['role'], 'status')
        for identifier in ('query-schema', 'chart-type', 'chart-x-col', 'chart-y-col', 'auto-chill-minutes'):
            self.assertTrue(any(n.get('for') == identifier for n in doc.nodes))

    def test_phase8_translations_cover_static_and_dynamic_ui(self):
        doc = self.workbench_document()
        script = next(n['text'] for n in doc.nodes if n['tag'] == 'script')
        dictionary = script.split('const i18n = {ja: {', 1)[1].split('}};', 1)[0]
        translations = dict(re.findall(r"'([^']+)':'([^']+)'", dictionary))
        labels = '''Schema|Table View|Chart View|Chart type|Bar Chart|Line Chart|X column|Y column|
            Clone to Sandbox|Clone Snapshot|Export format|Export Table|Auto-Chill (idle minutes)|
            Off|5m|15m|30m|60m|Apply Auto-Chill|Auto-Chill: Off|Auto-Chill: {minutes}m|
            Query & Load History|Refresh History|Trino Query History|Copy & Data Load History|
            Query ID|User|Source|SQL|Target|Result chart|
            Choose a numeric Y column to chart visible rows.|Clone audit needs review|
            Cloned {source} to {table}.|Clone failed: {error}|Exported {table} to {filename}.|
            Export failed: {error}|Auto-Chill failed: {error}|History failed: {error}'''
        for label in labels.split('|'):
            with self.subTest(label=label.strip()):
                self.assertRegex(translations[label.strip()], r'[ぁ-んァ-ヶ一-龯]')
        for label in ('CSV', 'JSONL', 'Parquet'):
            self.assertEqual(translations[label], label)

    @unittest.skipUnless(shutil.which('node'), 'JavaScript verification requires Node.js')
    def test_phase8_javascript_execution_contracts(self):
        """Execute production handlers with a small DOM/API adapter; no lab writes."""
        script = next(n['text'] for n in self.workbench_document().nodes if n['tag'] == 'script')
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
        result = subprocess.run(['node', '-e', source], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_phase8_api_and_rendering_contracts(self):
        script = next(n['text'] for n in self.workbench_document().nodes if n['tag'] == 'script')
        for contract in (
            "createElementNS(ns,'svg')", "const points=visibleRows().filter",
            "for(const id of ['chart-type','chart-x-col','chart-y-col'])$(id).onchange=renderChart",
            "$('query-results').hidden=view!=='table'", "$('query-chart').hidden=view!=='chart'",
            "const payload={source_table:source}", 'payload.snapshot_id=snapshot',
            "api('/api/catalog/clone',payload)", "actionButton('Clone Snapshot'",
            "api('/api/lake/export',{table:selected,format})", 'atob(result.content_base64)',
            'downloadFile(content,result.filename', 'link.download=filename',
            "if(data.auto_chill)updateAutoChill(data.auto_chill)",
            "document.activeElement!==select", "if(auto_chill_minutes>0)",
            "api('/api/warehouse/autochill',{check:true})",
            "api('/api/warehouse/autochill',{minutes:Number($('auto-chill-minutes').value)})",
            "api('/api/history')", "$('refresh-history').onclick",
            "if(activeTab==='pawprints'){$('refresh-pawprints').click();$('refresh-history').click();}",
            "actionButton('Open in Worksheet',()=>openWorksheet(rows[i][sqlIndex]))",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, script)
        for action in ('lake.ingest', 'lake.export', 'catalog.clone', 'catalog.drop', 'bear.data_engineer'):
            self.assertIn("'" + action + "'", script)

    def test_query_session_schema_and_legacy_default(self):
        with patch.object(wb.warehouse_resident, 'query', return_value={'rows': []}) as query:
            for schema in ('marts', 'raw', 'sandbox', 'staging', 'tpch_tiny', 'smoke'):
                self.assertEqual(self.json_request('/api/warehouse/query', dict(sql='SELECT 1', schema=schema))[0], 200)
                query.assert_called_with('LOCAL', 'SELECT 1', catalog='iceberg', schema=schema, max_rows=1000)
            self.assertEqual(self.json_request('/api/warehouse/query', dict(sql='SELECT 1'))[0], 200)
            query.assert_called_with('LOCAL', 'SELECT 1', catalog='iceberg', schema='marts', max_rows=1000)
            query.reset_mock()
            for schema in ('other', '', None, 1, [], {}):
                code, result = self.json_request('/api/warehouse/query', dict(sql='SELECT 1', schema=schema))
                self.assertEqual((code, result['error']), (400, 'invalid query schema'))
            query.assert_not_called()

    def test_clone_api_inputs_and_snapshot_precision(self):
        with patch.object(wb.bears_resident, 'clone_table',
                          return_value=dict(audit={'snapshot_id': 9007199254740993})) as clone:
            code, result = self.json_request('/api/catalog/clone', dict(source_table='marts.orders'))
            self.assertEqual(code, 200)
            self.assertEqual(result['audit']['snapshot_id'], '9007199254740993')
            clone.assert_called_once_with('marts.orders', target_table=None, snapshot_id=None)
            self.assertEqual(self.json_request('/api/catalog/clone', dict(source_table='raw.orders',
                target_table='raw.copy', snapshot_id='9007199254740993'))[0], 200)
            clone.assert_called_with('raw.orders', target_table='raw.copy', snapshot_id='9007199254740993')
            clone.reset_mock()
            for body in ({}, {'source_table': []}, {'source_table': ''},
                         {'source_table': 'raw.orders', 'target_table': 1},
                         {'source_table': 'raw.orders', 'snapshot_id': 1}):
                self.assertEqual(self.json_request('/api/catalog/clone', body)[0], 400)
            clone.assert_not_called()
            clone.side_effect = ValueError('clone target must be in sandbox or raw schema')
            self.assertEqual(self.json_request('/api/catalog/clone', dict(source_table='raw.orders', target_table='marts.x'))[0], 400)
            clone.side_effect = RuntimeError('clone failed')
            self.assertEqual(self.json_request('/api/catalog/clone', dict(source_table='raw.orders'))[0], 502)

    def test_export_api_defaults_options_and_validation(self):
        with patch.object(wb.lake_ingest, 'export_table', return_value={'content': 'id\n1\n'}) as export:
            self.assertEqual(self.json_request('/api/lake/export', {'table': 'marts.orders'}), (200, {'content': 'id\n1\n'}))
            export.assert_called_once_with('marts.orders', file=None, format='auto', max_rows=10000)
            self.assertEqual(self.json_request('/api/lake/export', dict(table='raw.orders', file='out.jsonl', format='jsonl', max_rows=7))[0], 200)
            export.assert_called_with('raw.orders', file='out.jsonl', format='jsonl', max_rows=7)
            export.reset_mock()
            for body in ({}, {'table': []}, {'table': ''}, {'table': 'raw.t', 'format': []},
                         {'table': 'raw.t', 'format': 'xml'}, {'table': 'raw.t', 'file': 1},
                         {'table': 'raw.t', 'file': ''}, {'table': 'raw.t', 'max_rows': True},
                         {'table': 'raw.t', 'max_rows': 0}, {'table': 'raw.t', 'max_rows': '1'}):
                self.assertEqual(self.json_request('/api/lake/export', body)[0], 400)
            export.assert_not_called()
            export.side_effect = ValueError('invalid table')
            self.assertEqual(self.json_request('/api/lake/export', {'table': 'bad'})[0], 400)
            export.side_effect = RuntimeError('unavailable')
            self.assertEqual(self.json_request('/api/lake/export', {'table': 'raw.t'})[0], 502)

    def test_auto_chill_routes_and_status(self):
        auto = dict(auto_chill_minutes=5, idle_seconds=0)
        with patch.object(wb.warehouse_resident, 'configure_auto_chill', return_value=auto) as configure, \
             patch.object(wb.warehouse_resident, 'check_auto_chill', return_value=dict(chilled=True, **auto)) as check:
            self.assertEqual(self.json_request('/api/warehouse/autochill', {'minutes': 5}), (200, auto))
            configure.assert_called_once_with(5)
            check.assert_not_called()
            self.assertTrue(self.json_request('/api/warehouse/autochill', {'check': True})[1]['chilled'])
            check.assert_called_once_with('LOCAL')
            self.assertEqual(self.json_request('/api/warehouse/autochill', {'minutes': 0, 'check': True})[0], 200)
            configure.assert_called_with(0)
            configure.reset_mock()
            check.reset_mock()
            self.assertEqual(self.json_request('/api/warehouse/autochill', {'check': False})[0], 200)
            for body in ({}, {'check': 1}, {'minutes': True}, {'minutes': -1},
                         {'minutes': 1441}, {'minutes': '1'}, {'minutes': 1, 'check': 'true'}):
                self.assertEqual(self.json_request('/api/warehouse/autochill', body)[0], 400)
            configure.assert_not_called()
            check.assert_not_called()
        with patch.object(wb.warehouse_resident, 'auto_chill_status', return_value=auto), \
             patch.object(wb.warehouse_resident, 'status', return_value={}), patch.object(wb, 'components', return_value={}):
            self.assertEqual(self.json_request('/api/status')[1]['auto_chill'], auto)

    def test_activity_history_route_and_failure(self):
        report = dict(runtime_queries={'rows': []}, query_history=[], load_history=[])
        with patch.object(wb.bears_resident, 'activity_history', return_value=report) as history:
            self.assertEqual(self.json_request('/api/history'), (200, report))
            history.assert_called_once_with()
            history.side_effect = RuntimeError('unavailable')
            self.assertEqual(self.json_request('/api/history')[0], 502)

    def workbench_document(self):
        status, _, body = self.request('/')
        self.assertEqual(status, 200)
        document = WorkbenchHTML(body.decode())
        ids = [n['id'] for n in document.nodes if 'id' in n]
        self.assertEqual(len(ids), len(set(ids)), 'HTML IDs must remain unique')
        return document

    def test_snowsight_navigation_contract(self):
        doc = self.workbench_document()
        navigation = next(n for n in doc.nodes if n['tag'] == 'nav')
        headings = [n['text'] for n in doc.nodes
                    if n['tag'] == 'h2' and navigation in n['ancestors']]
        self.assertEqual(headings, ['Work with Data', 'Catalog', 'Manage & Monitor'])
        expected = [
            ('warehouse', 'SQL Worksheets · Warehouse & SQL'),
            ('ingestion', 'Ingestion · File Upload'),
            ('transformation', 'Transformation · dbt & WAP'),
            ('bears', 'Autonomous Bears'),
            ('catalog', 'Database Explorer · Catalog'),
            ('pawprints', 'AgentHouse Pawprints'),
            ('compute', 'Compute & Governance'),
        ]
        tabs = [n for n in doc.nodes if 'data-tab' in n]
        self.assertEqual([(n['data-tab'], n['text']) for n in tabs], expected)
        for name, _ in expected:
            with self.subTest(tab=name):
                tab, panel = doc.by_id('tab-' + name), doc.by_id(name)
                self.assertEqual(tab['role'], 'tab')
                self.assertEqual(tab['aria-controls'], name)
                self.assertEqual(panel['role'], 'tabpanel')
                self.assertEqual(panel['aria-labelledby'], tab['id'])
                self.assertEqual('hidden' in panel, name != 'warehouse')
        for identifier in ('quick-warehouse-state', 'quick-wake', 'quick-chill'):
            self.assertTrue(any(n['tag'] == 'header' for n in doc.by_id(identifier)['ancestors']))

    def test_workbench_controls_belong_to_their_workspaces(self):
        doc = self.workbench_document()
        controls = {
            'warehouse': '''warehouse-state replicas chill wake refresh-status components status-error
                sql query-warehouse query-mode max-rows run-query query-meta query-error query-results
                worksheet-explorer worksheet-tree-filter worksheet-tabs add-worksheet-tab query-history
                copilot-request copilot-generate copilot-explain copilot-stage-table copilot-stage
                copilot-publish result-filter export-csv export-json copy-tsv''',
            'ingestion': '''ingest-form ingest-file ingest-table ingest-mode ingest-key run-ingest
                ingest-state ingest-error ingest-result ingest-upload-input ingest-dropzone
                preview-ingest ingest-preview''',
            'transformation': '''refresh-models run-dbt-build run-dbt-test dbt-models wap-models
                model-lineage model-sql-preview open-model-sql refresh-wap-model''',
            'catalog': '''refresh-catalog catalog-error catalog-tree catalog-filter table-title
                table-columns lineage open-table-sql drop-table-btn table-preview table-ddl
                table-snapshots snapshot-preview table-lineage''',
            'bears': 'bear-cards',
            'pawprints': '''refresh-pawprints pawprint-meta pawprint-error pawprint-results
                pawprint-filter pawprint-status-filter''',
            'compute': 'compute-warehouse compute-components compute-sre-check compute-policy-check',
        }
        for section, identifiers in controls.items():
            for identifier in identifiers.split():
                with self.subTest(control=identifier):
                    self.assertIn(doc.by_id(section), doc.by_id(identifier)['ancestors'])
        upload = doc.by_id('ingest-upload-input')
        self.assertEqual(upload['type'], 'file')
        self.assertEqual(upload['accept'], '.csv,.tsv,.jsonl,.ndjson,.json,.parquet')
        for identifier, label in [('export-csv', 'Download CSV'), ('export-json', 'Download JSON'),
                                  ('copy-tsv', 'Copy TSV'), ('preview-ingest', 'Preview Schema & Sample Rows')]:
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
        header = next(n for n in switcher['ancestors'] if n['tag'] == 'header')
        environment = next(n for n in doc.nodes if n.get('class') == 'environment')
        self.assertIn(header, environment['ancestors'])
        self.assertLess(doc.nodes.index(environment), doc.nodes.index(switcher))
        for lang, label, pressed in [('en', 'EN', 'true'), ('ja', '日本語', 'false')]:
            button = doc.by_id('lang-' + lang)
            self.assertIn(switcher, button['ancestors'])
            self.assertIn(header, button['ancestors'])
            self.assertEqual((button['tag'], button['data-lang'], button['aria-pressed'], button['text']),
                             ('button', lang, pressed, label))
        self.assertEqual(next(n for n in doc.nodes if n['tag'] == 'html')['lang'], 'en')

        script = next(n['text'] for n in doc.nodes if n['tag'] == 'script')
        dictionary = script.split('const i18n = {ja: {', 1)[1].split('}};', 1)[0]
        translations = dict(re.findall(r"'([^']+)':'([^']+)'", dictionary))
        expected = {
            'Work with Data': 'データを扱う', 'Catalog': 'カタログ',
            'Manage & Monitor': '監視・管理',
            'SQL Worksheets · Warehouse & SQL': 'SQL ワークシート · Warehouse & SQL',
            'Ingestion · File Upload': 'データ取り込み · ファイルアップロード',
            'Transformation · dbt & WAP': 'データ変換 · dbt & WAP',
            'Autonomous Bears': '自律 AI Bears',
            'Database Explorer · Catalog': 'データベース探索 · カタログ',
            'AgentHouse Pawprints': 'AgentHouse 監査ログ (Pawprints)',
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
        for node in doc.nodes:
            if node.get('class') == 'page-title':
                for child in doc.nodes:
                    if node in child['ancestors'] and child['tag'] in {'h1', 'p'}:
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

    @patch.object(wb.bears_resident, 'catalog')
    def test_catalog_full_detail_and_snapshot_preview(self, catalog):
        catalog.return_value = {'metadata': {'profile': {'snapshot_id': 9223372036854775807},
                                           'snapshots': {'rows': [[9223372036854775807, 9007199254740993, 'date']]}}}
        code, result = self.json_request('/api/catalog?table=raw.orders&detail=full')
        self.assertEqual(code, 200)
        self.assertEqual(catalog.call_args.args[0].detail, 'full')
        self.assertEqual(result['metadata']['profile']['snapshot_id'], '9223372036854775807')
        self.assertEqual(result['metadata']['snapshots']['rows'][0], ['9223372036854775807', '9007199254740993', 'date'])
        catalog.return_value = {'action': 'snapshot_preview', 'snapshot_id': '9007199254740993'}
        self.assertEqual(self.json_request('/api/catalog?table=raw.orders&snapshot_id=9007199254740993'),
                         (200, catalog.return_value))
        self.assertEqual(catalog.call_args.args[0].snapshot_id, '9007199254740993')
        catalog.reset_mock()
        self.assertEqual(self.json_request('/api/catalog?snapshot_id=1'),
                         (400, {'error': 'table is required for snapshot_id'}))
        catalog.assert_not_called()
        catalog.side_effect = ValueError('snapshot_id must be digits-only')
        self.assertEqual(self.json_request('/api/catalog?table=raw.orders&snapshot_id=bad')[0], 400)

    def test_preview_and_ingest_browser_uploads(self):
        for endpoint in ('preview', 'ingest'):
            with patch.object(wb.lake_ingest, endpoint, return_value={'action': endpoint}) as call:
                for binary in (False, True):
                    body = {'filename': 'orders.csv'}
                    body.update({'content_base64': base64.b64encode(b'id\n1').decode()} if binary else {'content': 'id\n1'})
                    self.assertEqual(self.json_request('/api/lake/' + endpoint, body), (200, {'action': endpoint}))
                    expected = dict(filename='orders.csv', content=b'id\n1' if binary else 'id\n1', format='auto')
                    expected.update(dict(limit=10) if endpoint == 'preview' else dict(table=None, mode='replace', batch_size=500))
                    call.assert_called_with(None, **expected)
        with patch.object(wb.lake_ingest, 'preview', return_value={}) as preview:
            self.assertEqual(self.json_request('/api/lake/preview', {'file': 'orders.csv', 'limit': 2, 'format': 'csv'})[0], 200)
            preview.assert_called_once_with('orders.csv', format='csv', limit=2)

    def test_invalid_browser_uploads(self):
        cases = [{}, {'filename': 'x.csv'}, {'content': 'id\n1'}, {'filename': 1, 'content': ''},
                 {'filename': ' ', 'content': ''}, {'filename': 'x', 'content': None},
                 {'filename': 'x', 'content_base64': '!!'}, {'filename': 'x', 'content_base64': 'é'},
                 {'filename': 'x', 'content': '', 'content_base64': ''},
                 {'file': 'x.csv', 'filename': 'x.csv', 'content': ''},
                 {'file': 'x.csv', 'format': 'unknown'}]
        for endpoint in ('preview', 'ingest'):
            with patch.object(wb.lake_ingest, endpoint) as call:
                for body in cases:
                    with self.subTest(endpoint=endpoint, body=body):
                        self.assertEqual(self.json_request('/api/lake/' + endpoint, body)[0], 400)
                call.assert_not_called()
        for limit in (0, -1, True, '1', None, 1.5):
            self.assertEqual(self.json_request('/api/lake/preview', {'file': 'x.csv', 'limit': limit})[0], 400)

    def test_models_routes_and_drop(self):
        with patch.object(wb.bears_resident, 'models_inventory', return_value={'dbt_models': [], 'wap_models': [], 'lineage': {}}) as inventory:
            self.assertEqual(self.json_request('/api/models'), (200, inventory.return_value))
            inventory.assert_called_once_with()
        with patch.object(wb.bears_resident, 'drop_table', return_value={'dropped': True}) as drop:
            self.assertEqual(self.json_request('/api/catalog/drop', {'table': 'raw.x'}), (200, drop.return_value))
            drop.assert_called_once_with('raw.x')
            drop.reset_mock()
            for table in (None, '', ' ', 1, []):
                self.assertEqual(self.json_request('/api/catalog/drop', {'table': table})[0], 400)
            drop.assert_not_called()
            drop.side_effect = ValueError('drop is only allowed')
            self.assertEqual(self.json_request('/api/catalog/drop', {'table': 'marts.x'})[0], 400)
        with patch.dict(sys.modules, {'lake_pipeline': Mock()}) as modules:
            run = modules['lake_pipeline'].run
            run.return_value = {'status': 'ok'}
            for body in ({}, {'action': 'build'}, {'action': 'test'}, {'action': 'status'}):
                self.assertEqual(self.json_request('/api/models/build', body), (200, {'status': 'ok'}))
                run.assert_called_with(body.get('action', 'build'))
            run.reset_mock()
            for action in ('publish', None, [], 1):
                self.assertEqual(self.json_request('/api/models/build', {'action': action})[0], 400)
            run.assert_not_called()

    def test_upload_body_limit(self):
        handler = object.__new__(wb.WorkbenchHandler)
        handler.headers = Message()
        handler.headers['Content-Type'] = 'application/json'
        for size in (1024 * 1024 + 1, 10 * 1024 * 1024):
            payload = b'{"content":"' + b'x' * (size - 14) + b'"}'
            payload += b' ' * (size - len(payload))
            self.assertEqual(len(payload), size)
            handler.headers['Content-Length'] = str(len(payload))
            handler.rfile = io.BytesIO(payload)
            self.assertEqual(len(handler.body()['content']), size - 14)
            del handler.headers['Content-Length']
        for size in (0, 10 * 1024 * 1024 + 1):
            handler.headers['Content-Length'] = str(size)
            with self.assertRaisesRegex(ValueError, '10 MiB'):
                handler.body()
            del handler.headers['Content-Length']

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), wb.WorkbenchHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, path, body=None, method=None, headers=None):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        payload = json.dumps(body) if body is not None else None
        conn.request(method or ("POST" if body is not None else "GET"), path, payload,
                     headers or {"Content-Type": "application/json"})
        response = conn.getresponse()
        raw = response.read()
        result = (response.status, dict(response.getheaders()), raw)
        conn.close()
        return result

    def json_request(self, path, body=None, method=None):
        status, headers, raw = self.request(path, body, method)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        return status, json.loads(raw)

    def test_static(self):
        for path in ("/", "/index.html"):
            with self.subTest(path=path):
                status, headers, body = self.request(path)
                self.assertEqual(status, 200)
                self.assertIn("text/html", headers["Content-Type"])
                self.assertIn(b"Warehouse &amp; SQL", body)
                self.assertIn(b"AgentHouse Pawprints", body)

    @patch.object(wb.warehouse_resident, "kubectl")
    @patch.object(wb.warehouse_resident, "status")
    def test_status(self, status, kubectl):
        status.return_value = {"state": "chilled", "replicas": 0, "ready_replicas": 0}
        kubectl.return_value = json.dumps({"items": [{"metadata": {"name": "polaris"},
            "spec": {"replicas": 1}, "status": {"readyReplicas": 1}}]})
        code, result = self.json_request("/api/status")
        self.assertEqual(code, 200)
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["warehouse"]["state"], "chilled")
        self.assertEqual(result["components"]["polaris"]["ready_replicas"], 1)
        status.assert_called_once_with("LOCAL")

    @patch.object(wb, "components", side_effect=OSError("cluster unavailable"))
    @patch.object(wb.warehouse_resident, "status", side_effect=OSError("no cluster"))
    def test_status_unavailable(self, *_):
        code, result = self.json_request("/api/status")
        self.assertEqual(code, 200)
        self.assertEqual(result["status"], "degraded")
        self.assertIn("error", result["warehouse"])

    @patch.object(wb.warehouse_resident, "transition")
    def test_chill_wake(self, transition):
        for action in ("chill", "wake"):
            transition.return_value = {"action": action, "result": "verified"}
            code, result = self.json_request("/api/warehouse/" + action, {})
            self.assertEqual(code, 200)
            self.assertEqual(result["action"], action)
            transition.assert_called_with(action, "LOCAL")

    @patch.object(wb.warehouse_resident, "query")
    def test_query(self, query):
        report = dict(columns=[{"name": "x", "type": "bigint"}], rows=[[42]], truncated=True,
                      query_id="query-42", telemetry="recorded", duration_ms=12)
        query.return_value = report
        code, result = self.json_request("/api/warehouse/query", {"sql": "SELECT 42", "warehouse": "xs", "max_rows": 1})
        self.assertEqual((code, result), (200, report))
        query.assert_called_once_with("XS", "SELECT 42", catalog="iceberg", schema="marts", max_rows=1)

    @patch.object(wb.bears_resident, "catalog")
    def test_catalog_lineage_and_columns(self, catalog):
        catalog.return_value = {"metadata": {"marts": [["fct_orders"]]}}
        for path, action, table in [("/api/catalog", "search", None), ("/api/lineage", "lineage", None),
                                    ("/api/catalog?table=marts.fct_orders", "describe", "marts.fct_orders")]:
            with self.subTest(path=path):
                code, result = self.json_request(path)
                self.assertEqual((code, result), (200, catalog.return_value))
                args = catalog.call_args.args[0]
                self.assertEqual((args.bear, args.action, args.table), ("catalog", action, table))

    @patch.object(wb.warehouse_resident, 'query', return_value={'rows': []})
    def test_query_modes(self, query):
        for mode in ('read-only', 'sandbox-write', 'admin-write'):
            with self.subTest(mode=mode):
                self.assertEqual(self.json_request('/api/warehouse/query', {'sql': 'SELECT 1', 'mode': mode})[0], 200)
                query.assert_called_with('LOCAL', 'SELECT 1', catalog='iceberg', schema='marts', max_rows=1000, mode=mode)
        query.reset_mock()
        for mode in ('unknown', '', None, [], True):
            with self.subTest(mode=mode):
                self.assertEqual(self.json_request('/api/warehouse/query', {'sql': 'SELECT 1', 'mode': mode})[0], 400)
        query.assert_not_called()

    @patch.object(wb.lake_ingest, 'ingest', return_value={'audit': {'row_count': 12}})
    def test_lake_ingest(self, ingest):
        self.assertEqual(self.json_request('/api/lake/ingest', {'file': 'data/orders.csv'}),
                         (200, ingest.return_value))
        ingest.assert_called_once_with('data/orders.csv', table=None, mode='replace', format='auto', batch_size=500)
        self.assertEqual(self.json_request('/api/lake/ingest', {'file': 'data/events.jsonl', 'table': 'raw.events',
            'mode': 'append', 'format': 'jsonl', 'batch_size': 1})[0], 200)
        ingest.assert_called_with('data/events.jsonl', table='raw.events', mode='append', format='jsonl', batch_size=1)

    @patch.object(wb.lake_ingest, 'ingest', return_value={'key': ['order_id'], 'rows_ingested': 2})
    def test_lake_ingest_upsert(self, ingest):
        self.assertEqual(self.json_request('/api/lake/ingest', {
            'file': 'data/orders.csv', 'mode': 'upsert', 'key': 'order_id'}), (200, ingest.return_value))
        ingest.assert_called_once_with('data/orders.csv', table=None, mode='upsert', format='auto',
                                       batch_size=500, key='order_id')

    @patch.object(wb.lake_ingest, 'ingest')
    def test_lake_ingest_rejects_invalid_fields(self, ingest):
        cases = [{}, {'file': ''}, {'file': '   '}, {'file': 42}]
        cases += [dict(file='data.csv', **{key: value}) for key, values in (
            ('table', [None, [], 3]), ('mode', [None, {}, 3]), ('format', [None, True]),
            ('key', [None, [], 3, True]),
            ('batch_size', [None, 0, -1, True, '500', 1.5])) for value in values]
        for body in cases:
            with self.subTest(body=body):
                self.assertEqual(self.json_request('/api/lake/ingest', body)[0], 400)
        ingest.assert_not_called()

    @patch.object(wb.bears_resident, 'run', return_value={'valid': True})
    def test_data_wap_actions(self, run):
        cases = [({'action': 'generate', 'sql': 'SELECT 1'}, 'sql', 'SELECT 1'),
                 ({'action': 'stage', 'sql': 'SELECT 1', 'table': 'summary'}, 'stage_table', 'summary'),
                 ({'action': 'stage', 'request': 'Count orders', 'stage_table': 'custom', 'table': 'other'}, 'stage_table', 'custom'),
                 ({'action': 'publish', 'table': 'summary'}, 'publish', 'summary'),
                 ({'action': 'publish', 'stage_table': 'summary'}, 'publish', 'summary'),
                 ({'action': 'publish', 'publish': 'custom', 'table': 'other'}, 'publish', 'custom'),
                 ({'action': 'refresh', 'table': 'summary'}, 'refresh', 'summary'),
                 ({'action': 'refresh', 'stage_table': 'summary'}, 'refresh', 'summary'),
                 ({'action': 'refresh', 'refresh': 'custom', 'table': 'other'}, 'refresh', 'custom'),
                 ({'action': 'rollback', 'target': 'marts.summary', 'snapshot_id': '9007199254740993'}, 'rollback', 'marts.summary'),
                 ({'action': 'rollback', 'table': 'marts.summary'}, 'rollback', 'marts.summary'),
                 ({'action': 'rollback', 'rollback': 'marts.custom', 'table': 'other'}, 'rollback', 'marts.custom')]
        for fields, key, expected in cases:
            with self.subTest(fields=fields):
                self.assertEqual(self.json_request('/api/bears/run', dict(bear='data', **fields))[0], 200)
                args = run.call_args.args[0]
                self.assertEqual(getattr(args, key), expected)
                if 'snapshot_id' in fields:
                    self.assertEqual(args.snapshot_id, fields['snapshot_id'])
        run.reset_mock()
        for fields in ({'action': 'publish'}, {'action': 'rollback'}, {'action': 'refresh'}, {'refresh': 42}, {'request': ' ', 'sql': '\n'},
                       {'sql': 'SELECT 1', 'stage_table': []}, {'publish': 3}, {'rollback': None}, {'snapshot_id': 42}):
            with self.subTest(fields=fields):
                code, result = self.json_request('/api/bears/run', dict(bear='data', **fields))
                self.assertEqual(code, 400)
                if fields.get('action') in ('publish', 'rollback', 'refresh'):
                    self.assertEqual(result['error'], 'data bear requires a request, sql, publish, rollback, or refresh')
        run.assert_not_called()

    @patch.object(wb.bears_resident, 'catalog')
    def test_snapshot_ids_preserve_digits_for_browser_rollback(self, catalog):
        catalog.return_value = {'metadata': {'snapshots': {'rows': [[9007199254740993, None, '2026-10-17']]}}}
        code, result = self.json_request('/api/catalog?table=marts.summary')
        self.assertEqual(code, 200)
        self.assertEqual(result['metadata']['snapshots']['rows'], [['9007199254740993', None, '2026-10-17']])

    def test_lineage_from_actual_models(self):
        code, result = self.json_request("/api/lineage")
        self.assertEqual(code, 200)
        self.assertIn("stg_tpch_orders", result["metadata"]["lineage"]["fct_orders"])
        self.assertIn("stg_tpch_customers", result["metadata"]["lineage"]["dim_customers"])

    @patch.object(wb.bears_resident, "run")
    def test_all_bears(self, run):
        for bear in ("sre", "query", "data", "catalog", "policy"):
            with self.subTest(bear=bear):
                run.return_value = {"bear": bear, "telemetry": "recorded"}
                payload = dict(bear=bear, sql="SELECT 1", request="Count customers", manifest="example.yaml", target="latest")
                code, result = self.json_request("/api/bears/run", payload)
                self.assertEqual((code, result), (200, run.return_value))
                args = run.call_args.args[0]
                for key, value in payload.items():
                    self.assertEqual(getattr(args, key), value)
                self.assertEqual(args.table, "latest" if bear == "catalog" else None)

    @patch.object(wb.manage, "Client")
    def test_pawprints(self, client):
        rows = [{"timestamp": "2026-10-17", "action_type": "warehouse.query", "duration_ms": 3, "status": "success"}]
        client.return_value.query.return_value = json.dumps({"data": rows})
        self.assertEqual(self.json_request("/api/pawprints"), (200, rows))
        statement = client.return_value.query.call_args.args[0]
        self.assertIn("agenthouse.pawprints", statement)
        self.assertIn("ORDER BY timestamp DESC LIMIT 100", statement)

    @patch.object(wb.warehouse_resident, "query")
    @patch.object(wb.bears_resident, "run")
    def test_invalid_requests_never_call_backends(self, run, query):
        cases = [("/api/warehouse/query", {}), ("/api/warehouse/query", {"sql": "SELECT 1", "max_rows": True}),
                 ("/api/warehouse/query", {"sql": "SELECT 1", "warehouse": "XL"}),
                 ("/api/bears/run", {"bear": "__builtins__"}), ("/api/bears/run", {"bear": "data"}),
                 ("/api/bears/run", {"bear": "policy", "action": "apply"}),
                 ("/api/bears/run", {"bear": "query", "sql": []}), ("/api/warehouse/query", [])]
        for path, body in cases:
            with self.subTest(body=body):
                self.assertEqual(self.json_request(path, body)[0], 400)
        run.assert_not_called()
        query.assert_not_called()

    @patch.object(wb.manage, "Client", side_effect=RuntimeError("ClickHouse unavailable"))
    def test_backend_errors(self, _):
        self.assertEqual(self.json_request("/api/pawprints"), (502, {"error": "ClickHouse unavailable"}))

    def test_unknown_routes(self):
        self.assertEqual(self.json_request("/missing")[0], 404)
        self.assertEqual(self.json_request("/missing", {})[0], 404)

    @patch.object(wb.warehouse_resident, "transition")
    def test_cors(self, transition):
        origin = "http://%s:%s" % self.server.server_address
        code, headers, _ = self.request("/api/status", method="OPTIONS", headers={"Origin": origin})
        self.assertEqual(code, 204)
        self.assertEqual(headers["Access-Control-Allow-Origin"], origin)
        self.assertEqual(self.request("/api/warehouse/wake", {}, headers={"Origin": "https://example.com"})[0], 403)
        transition.assert_not_called()

    def test_malformed_json_and_content_type(self):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        conn.request("POST", "/api/warehouse/query", "{", {"Content-Type": "application/json"})
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()
        self.assertEqual(self.request("/api/warehouse/query", {}, headers={"Content-Type": "text/plain"})[0], 400)

    @patch.object(wb, 'HTTPServer')
    def test_server_owns_persistent_tunnel_and_serves_when_unavailable(self, server):
        for unavailable in (False, True):
            events = []
            @contextmanager
            def tunnel():
                if unavailable:
                    raise OSError('Trino offline')
                events.append('open')
                try:
                    yield 'http://trino'
                finally:
                    events.append('close')
            def serve():
                self.assertEqual(events, [] if unavailable else ['open'])
                raise KeyboardInterrupt()
            server.return_value.__enter__.return_value.serve_forever.side_effect = serve
            with self.subTest(unavailable=unavailable), patch.object(wb.warehouse_resident, 'persistent_trino', tunnel), \
                 patch('builtins.print'):
                wb.main(['--port', '0'])
            self.assertEqual(events, [] if unavailable else ['open', 'close'])


if __name__ == "__main__":
    unittest.main()
