"""Workbench HTTP contracts, with no resident lab required."""
import http.client
import base64
import io
from email.message import Message
from contextlib import contextmanager
from http.server import HTTPServer
from html.parser import HTMLParser
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
import sys
import threading
import tempfile
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
    def test_mart_routes_authenticated_dispatch_and_admission(self):
        definition = dict(name='orders', sql='SELECT 1 AS id', min_rows=1, not_null=['id'])
        cases = [('/api/marts', None, 'list_targets', ('alice', 'admin'), {'marts': []}, []),
                 ('/api/marts/save', definition, 'save_target', (definition, 'alice', 'admin'), {'mart': definition}, definition),
                 ('/api/marts/run', {'name': 'orders', 'version': 1}, 'submit_refresh',
                  ('orders', 1, 'alice', 'admin'), {'run': {'id': 'mart-1', 'status': 'queued'}}, {'id': 'mart-1', 'status': 'queued'})]
        with patch.object(wb.request_auth, 'authenticate', return_value=('alice', 'admin')):
            for path, body, method, args, expected, backend in cases:
                with self.subTest(path=path), patch.object(wb.marts_resident, method, return_value=backend) as call:
                    self.assertEqual(self.json_request(path, body), (202 if path.endswith('/run') else 200, expected))
                    call.assert_called_once_with(*args)

    def test_mart_routes_reject_role_auth_and_payload_spoofing(self):
        paths = ('/api/marts', '/api/marts/save', '/api/marts/run', '/api/marts/schedule')
        for role in ('data_engineer', 'bi_viewer'):
            with patch.object(wb.request_auth, 'authenticate', return_value=('alice', role)), patch.object(wb, 'marts_resident') as backend:
                for path in paths:
                    self.assertEqual(self.json_request(path, None if path == paths[0] else {})[0], 403)
                self.assertEqual(backend.mock_calls, [])
        with patch.object(wb.request_auth, 'authenticate', side_effect=wb.request_auth.AuthenticationError('credentials required')):
            for path in paths:
                self.assertEqual(self.json_request(path, None if path == paths[0] else {})[0], 401)
        definition = dict(name='orders', sql='SELECT 1', min_rows=0, not_null=[])
        with patch.object(wb, 'marts_resident') as backend:
            for path, valid in [('/api/marts/save', definition), ('/api/marts/run', dict(name='orders', version=1)),
                                ('/api/marts/schedule', dict(name='orders', enabled=False))]:
                for key in ('actor', 'owner', 'role', 'table', 'auth_mode'):
                    self.assertEqual(self.json_request(path, {**valid, key: 'bob'})[0], 400)
            for body in ({}, {'name': 'orders'}, {'name': 'orders', 'version': ''}, {'name': [], 'version': 1}):
                self.assertEqual(self.json_request('/api/marts/run', body)[0], 400)
            self.assertEqual(backend.mock_calls, [])

    def test_mart_http_registry_edits_are_versioned_without_execution(self):
        definition = dict(name='orders', sql='SELECT 1 AS id', min_rows=1, not_null=['id'])
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ,
                SHIROKUMA_STATE_DIR=temporary, SHIROKUMA_AUTH_MODE='local', SHIROKUMA_AUTH_FILE=''), \
                patch.object(wb.bears_resident, 'WAP_REGISTRY', Path(temporary) / 'wap_models.json'), \
                patch.object(wb.request_auth, 'authenticate', return_value=('alice', 'admin')), \
                patch.object(wb.warehouse_resident, 'execute') as execute, \
                patch.object(wb.bears_resident, 'sql') as sql:
            code, result = self.json_request('/api/marts/save', dict(name='defaults', sql='SELECT 1 AS id'))
            self.assertEqual(code, 200, result)
            self.assertEqual((result['mart']['min_rows'], result['mart']['not_null']), (1, []))
            self.assertEqual(self.json_request('/api/marts/save', {**definition, 'min_rows': 0})[0], 400)
            code, result = self.json_request('/api/marts/save', definition)
            self.assertEqual(code, 200, result)
            saved = result['mart']
            self.assertEqual((saved['owner'], saved['last_run']), ('alice', None))
            self.assertEqual(self.json_request('/api/jobs/runs')[1]['runs'], [])
            code, result = self.json_request('/api/marts/save', {**definition, 'version': saved['version'], 'sql': 'SELECT 2 AS id'})
            self.assertEqual(code, 200, result)
            updated = result['mart']
            self.assertNotEqual(updated['version'], saved['version'])
            self.assertEqual(self.json_request('/api/marts/save', {**definition, 'version': saved['version']})[0], 409)
            self.assertEqual(self.json_request('/api/marts/run', dict(name='orders', version=saved['version']))[0], 409)
            with patch.object(wb.request_auth, 'authenticate', return_value=('bob', 'admin')):
                self.assertFalse(next(mart for mart in self.json_request('/api/marts')[1]['marts'] if mart['name'] == 'orders')['can_manage'])
                self.assertEqual(self.json_request('/api/marts/save', {**definition, 'version': updated['version']})[0], 403)
                self.assertEqual(self.json_request('/api/marts/run', dict(name='orders', version=updated['version']))[0], 403)
            execute.assert_not_called()
            sql.assert_not_called()

    def test_mart_routes_map_backend_errors(self):
        definition = dict(name='orders', sql='SELECT 1', min_rows=0, not_null=[])
        for path, body, method in [('/api/marts', None, 'list_targets'),
                ('/api/marts/save', definition, 'save_target'),
                ('/api/marts/run', dict(name='orders', version=1), 'submit_refresh'),
                ('/api/marts/schedule', dict(name='orders', enabled=False), 'save_schedule')]:
            for error, code in [(PermissionError('owner mismatch'), 403),
                    (wb.jobs_resident.JobNotFound('absent'), 404),
                    (wb.jobs_resident.JobConflict('stale version'), 409), (ValueError('invalid'), 400)]:
                with self.subTest(path=path, error=error), patch.object(wb.marts_resident, method, side_effect=error):
                    self.assertEqual(self.json_request(path, body), (code, {'error': str(error)}))

    def test_mart_schedule_calendar_dispatch_and_strict_fields(self):
        calendars = [dict(kind='interval', every_minutes=1),
                     dict(kind='daily', time='12:30', timezone='Asia/Tokyo'),
                     dict(kind='once', date='2099-01-01', time='12:30', timezone='UTC'),
                     dict(kind='weekly', weekdays=[1, 5], time='12:30', timezone='UTC'),
                     dict(kind='monthly', day_of_month='last', time='12:30', timezone='UTC')]
        with patch.object(wb.request_auth, 'authenticate', return_value=('alice', 'admin')):
            with patch.object(wb.marts_resident, 'save_schedule', return_value={'enabled': True}) as save:
                for calendar in calendars:
                    payload = dict(name='orders', version=1, enabled=True, **calendar)
                    self.assertEqual(self.json_request('/api/marts/schedule', payload), (200, {'schedule': {'enabled': True}}))
                    save.assert_called_with(payload, 'alice', 'admin')
                self.assertEqual(self.json_request('/api/marts/schedule', dict(name='orders', enabled=False))[0], 200)
            with patch.object(wb.marts_resident, 'save_schedule') as save:
                valid = dict(name='orders', version=1, enabled=True, kind='interval', every_minutes=1)
                invalid = [{}, dict(name='orders'), dict(name='orders', enabled=True),
                           {**valid, 'version': True}, {**valid, 'version': '1'},
                           {**valid, 'version': 0}, {**valid, 'enabled': 1},
                           {**valid, 'name': []}, {**valid, 'every_minutes': 0},
                           {**valid, 'kind': 'once'}, {**valid, 'unknown': 1},
                           {**valid, 'time': '12:00'}]
                for payload in invalid:
                    with self.subTest(payload=payload):
                        self.assertEqual(self.json_request('/api/marts/schedule', payload)[0], 400)
                save.assert_not_called()
        self.assertEqual(self.json_request('/api/marts/check', {'name': 'orders'})[0], 404)

    def test_mart_schedule_owner_version_and_watch_tables_without_execution(self):
        definition = dict(name='watched', sql='SELECT * FROM iceberg.raw.orders',
                          watch_tables=['ICEBERG.RAW.ORDERS', 'iceberg.raw.orders'])
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ,
                SHIROKUMA_STATE_DIR=temporary, SHIROKUMA_AUTH_MODE='local', SHIROKUMA_AUTH_FILE=''), \
                patch.object(wb.bears_resident, 'WAP_REGISTRY', Path(temporary) / 'wap_models.json'), \
                patch.object(wb.request_auth, 'authenticate', return_value=('alice', 'admin')), \
                patch.object(wb.warehouse_resident, 'execute') as execute:
            code, saved = self.json_request('/api/marts/save', definition)
            self.assertEqual(code, 200, saved)
            mart = saved['mart']
            self.assertEqual(mart['watch_tables'], ['iceberg.raw.orders'])
            payload = dict(name='watched', version=mart['version'], enabled=True, every_minutes=1)
            self.assertEqual(self.json_request('/api/marts/schedule', {**payload, 'version': 999999})[0], 409)
            with patch.object(wb.request_auth, 'authenticate', return_value=('bob', 'admin')):
                self.assertEqual(self.json_request('/api/marts/schedule', payload)[0], 403)
                self.assertEqual(self.json_request('/api/marts/schedule', dict(name='watched', enabled=False))[0], 403)
            code, result = self.json_request('/api/marts/schedule', payload)
            self.assertEqual(code, 200, result)
            self.assertTrue(result['schedule']['enabled'])
            code, result = self.json_request('/api/marts/schedule', dict(name='watched', enabled=False))
            self.assertEqual(code, 200, result)
            self.assertFalse(result['schedule']['enabled'])
            for watch in ('iceberg.raw.orders', ['iceberg.marts.orders'], ['iceberg.raw.orders; DROP TABLE x'], [1]):
                self.assertEqual(self.json_request('/api/marts/save', {**definition, 'name': 'invalid', 'watch_tables': watch})[0], 400)
            code, manual = self.json_request('/api/marts/save', dict(name='manual_only', sql='SELECT 1 AS id'))
            self.assertEqual(code, 200, manual)
            self.assertEqual(manual['mart']['watch_tables'], [])
            self.assertEqual(self.json_request('/api/marts/schedule', dict(name='manual_only', enabled=True,
                version=manual['mart']['version'], every_minutes=1))[0], 400)
            execute.assert_not_called()

    def test_stage_export_routes_and_strict_payloads(self):
        payload = dict(name='orders', table='iceberg.raw.orders', format='csv', file='orders.csv', version='revision-1')
        outputs = dict(directory='/host/orders/exports', files=[], limits=dict(max_rows=100000, max_file_bytes=26214400))
        with patch.object(wb.request_auth, 'authenticate', return_value=('alice', 'admin')):
            with patch.object(wb.stage_exports, 'submit_export', return_value={'id':'export-1'}) as submit:
                self.assertEqual(self.json_request('/api/stages/export', payload), (202, {'run':{'id':'export-1'}}))
                submit.assert_called_once_with('orders','iceberg.raw.orders','csv','orders.csv','revision-1','alice','admin')
            with patch.object(wb.stage_exports, 'list_exports', return_value=outputs) as listing:
                self.assertEqual(self.json_request('/api/stages/exports', {'name':'orders'}), (200, outputs))
                listing.assert_called_once_with('orders','alice','admin')
        with patch.object(wb, 'stage_exports') as exports:
            for path, valid in [('/api/stages/export',payload),('/api/stages/exports',{'name':'orders'})]:
                invalid = [{}, *[{k:v for k,v in valid.items() if k != key} for key in valid]]
                invalid += [{**valid,key:value} for key in valid for value in ('', ' ', None, True, [], {})]
                invalid += [{**valid,key:'override'} for key in ('actor','role','directory','path','limit','max_rows')]
                for body in invalid:
                    with self.subTest(path=path,body=body):
                        self.assertEqual(self.json_request(path,body)[0],400)
                self.assertEqual(self.json_request(path)[0],404)
                self.assertEqual(self.request(path,valid,method='PUT')[0],501)
                status, _, _ = self.request(path,valid,headers={'Content-Type':'application/json','Origin':'https://outside.example'})
                self.assertEqual(status,403)
                for role in ('data_engineer','bi_viewer'):
                    with patch.object(wb.request_auth,'authenticate',return_value=('alice',role)):
                        self.assertEqual(self.json_request(path,valid)[0],403)
                with patch.object(wb.request_auth,'authenticate',side_effect=wb.request_auth.AuthenticationError('credentials required')):
                    self.assertEqual(self.json_request(path,valid)[0],401)
            self.assertEqual(exports.mock_calls,[])

    def test_stage_export_owner_and_stale_revision_use_real_registry(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary).resolve()
            with patch.dict(os.environ, {'SHIROKUMA_STATE_DIR':str(directory / 'state')}), patch.object(wb.jobs_resident,'_check_account'), patch.object(wb.request_auth,'authenticate',return_value=('alice','admin')):
                definition=dict(name='orders',directory=str(directory),table='raw.orders',mode='append',format='csv')
                stage=wb.stages_resident.save_stage(definition,'alice','admin')
                status, outputs=self.json_request('/api/stages/exports',{'name':'orders'})
                self.assertEqual(status,200)
                self.assertEqual(outputs['directory'],str(directory / 'exports'))
                self.assertEqual(outputs['files'],[])
                payload=dict(name='orders',table='iceberg.raw.orders',format='csv',file='orders.csv',version='stale')
                self.assertEqual(self.json_request('/api/stages/export',payload)[0],400)
                payload['version']=stage['updated_at']
                with patch.object(wb.request_auth,'authenticate',return_value=('bob','admin')):
                    self.assertEqual(self.json_request('/api/stages/export',payload)[0],404)
                    self.assertEqual(self.json_request('/api/stages/exports',{'name':'orders'})[0],404)
                self.assertEqual(self.json_request('/api/stages/exports',{'name':'missing'})[0],404)

    @unittest.skipUnless(shutil.which('node'), 'JavaScript verification requires Node.js')
    def test_stage_export_controls_revisions_and_stale_output_responses(self):
        script = next(node['text'] for node in self.workbench_document().nodes if node['tag'] == 'script')
        handlers = 'let stageEntries=' + script.split('let stageEntries=', 1)[1].split('let selectedModel=null;', 1)[0]
        harness = r"""
const assert=require('node:assert/strict');
const nodes={};const $=id=>nodes[id]??={value:'',checked:false,hidden:false,disabled:false,elements:[],querySelectorAll(){return [];},replaceChildren(){this.textContent='';},setAttribute(){},reset(){},append(){},focus(){}};
const ui=(node,text)=>{node.textContent=text;};const message=(id,text)=>ui($(id),text);const pruneUIBindings=()=>{};const t=value=>value;
let currentRole='admin',activeTab='warehouse',selectedRunId=null;
const calls=[];let respond;const api=(path,body)=>{calls.push({path,body});return new Promise(resolve=>{respond=resolve;});};
const activate=()=>{};const table=()=>{};const actionButton=()=>({});const jobAlertTimestamp=value=>value;
const document={createElement:()=>({append(){}})};
eval(HANDLERS+`
(async()=>{
 const entry={name:'orders',updated_at:'revision-1',can_manage:true,definition:{name:'orders',directory:'/host/orders',mode:'append',table:'raw.orders'},schedule:null};
 editStage(entry);assert.equal($('stage-export-area').hidden,false);assert.equal($('stage-export-table').value,'raw.orders');assert.equal($('stage-export-directory').textContent,'/host/orders/exports');
 $('stage-export-format').value='jsonl';$('stage-export-format').onchange();assert.equal($('stage-export-file').value,'orders.jsonl');
 $('stage-export-table').value='iceberg.marts.results';
 let pending=$('stage-export-form').onsubmit({preventDefault(){}});
 assert.deepEqual(calls.at(-1),{path:'/api/stages/export',body:{name:'orders',table:'iceberg.marts.results',format:'jsonl',file:'orders.jsonl',version:'revision-1'}});
 assert.equal($('stage-export-submit').disabled,true);let count=calls.length;
 $('stage-export-form').onsubmit({preventDefault(){}});assert.equal(calls.length,count);
 respond({run:{id:'export-1'}});await pending;assert.equal(stageRunId,'export-1');assert.equal($('stage-view-run').hidden,false);
 pending=$('stage-exports-refresh').onclick();assert.equal(calls.at(-1).path,'/api/stages');
 respond({stages:[entry]});await Promise.resolve();await Promise.resolve();assert.equal(calls.at(-1).path,'/api/stages/exports');
 respond({directory:'/host/orders/exports',files:[]});await pending;
 assert.equal(stageRunId,'export-1');assert.equal($('stage-exports').textContent,'No exported files.');
 // Saved edits invalidate displayed output and use the new revision on the next export.
 pending=$('stage-exports-refresh').onclick();
 const revised={...entry,updated_at:'revision-2',definition:{...entry.definition,directory:'/host/new'}};
 respond({stages:[revised]});await Promise.resolve();await Promise.resolve();
 assert.equal($('stage-export-directory').textContent,'/host/new/exports');assert.equal(selectedStage.updated_at,'revision-2');
 // Changing stage while the response is in flight must not show an old path or files.
 ++stageEpoch;editStage({...revised,name:'other',definition:{...revised.definition,name:'other',directory:'/host/other'}});
 respond({directory:'/secret/old/exports',files:[{name:'old.csv'}]});await pending;
 assert.equal($('stage-export-directory').textContent,'/host/other/exports');assert.equal($('stage-exports').textContent,'Refresh outputs to list exported files.');
 stagePending=false;$('stage-form').oninput();assert.equal($('stage-export-area').hidden,true);count=calls.length;
 $('stage-export-form').onsubmit({preventDefault(){}});$('stage-exports-refresh').onclick();assert.equal(calls.length,count);
 stageDirty=false;pending=$('stage-export-form').onsubmit({preventDefault(){}});
 currentRole='bi_viewer';resetStageUI();respond({run:{id:'late-secret'}});await pending;
 assert.equal(selectedStage,null);assert.equal(stageRunId,null);assert.equal($('stage-export-area').hidden,true);assert.equal($('stage-export-directory').textContent,'');
})().catch(error=>{console.error(error);process.exitCode=1;});`);
"""
        result=subprocess.run(['node','-e','const HANDLERS='+json.dumps(handlers)+';'+harness],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_stage_export_ui_has_labels_limits_and_translations(self):
        doc=self.workbench_document()
        self.assertIn(doc.by_id('stages-area'),doc.by_id('stage-export-area')['ancestors'])
        for identifier in ('stage-export-table','stage-export-format','stage-export-file'):
            self.assertTrue(any(node.get('for')==identifier for node in doc.nodes))
        self.assertEqual(doc.by_id('stage-export-form')['tag'],'form')
        script=next(node['text'] for node in doc.nodes if node['tag']=='script')
        for key in ('Manual export','Output folder','Source table (qualified name)','Export format','Output filename','Export table','Refresh outputs','Stage export','parquet export requires pyarrow','export filename already exists; choose a new filename'):
            self.assertIn(repr(key)+':',script)
        self.assertIn('100000 rows and 25 MiB',doc.by_id('stage-export-area')['text'])
        self.assertIn('Existing filenames are refused.',doc.by_id('stage-export-area')['text'])
        self.assertIn("run.id===stageExportRunId&&!liveJobStates.has(run.status)",script)
        self.assertIn("stage_export:'Stage export'",script)

    def test_stage_routes_dispatch_authenticated_identity(self):
        definition = dict(name='orders', directory='/host/orders', table='raw.orders', mode='append', format='auto')
        selection = dict(name='orders', file='orders.csv', version='version-1')
        cases = [('/api/stages', None, 'list_stages', ('alice', 'admin'), {'stages': []}, []),
                 ('/api/stages/save', definition, 'save_stage', (definition, 'alice', 'admin'), {'stage': definition}, definition),
                 ('/api/stages/files', {'name': 'orders'}, 'list_files', ('orders', 'alice', 'admin'), {'files': []}, {'files': []}),
                 ('/api/stages/preview', selection, 'preview_file', ('orders', 'orders.csv', 'version-1', 'alice', 'admin'), {'row_count': 3}, {'row_count': 3}),
                 ('/api/stages/import', selection, 'submit_import', ('orders', 'orders.csv', 'version-1', 'alice', 'admin'), {'run': {'id': 'run-1'}}, {'id': 'run-1'}),
                 ('/api/stages/schedule', {'name':'orders','every_minutes':5,'enabled':True,'version':'revision-1'}, 'save_schedule', ({'name':'orders','every_minutes':5,'enabled':True,'version':'revision-1'}, 'alice', 'admin'), {'schedule':{}}, {}),
                 ('/api/stages/schedule', {'name':'orders','enabled':False}, 'save_schedule', ({'name':'orders','enabled':False}, 'alice', 'admin'), {'schedule':{}}, {}),
                 ('/api/stages/scan', {'name':'orders','version':'revision-1'}, 'submit_scan', ('orders','alice','admin','revision-1'), {'run':{'id':'scan-1'}}, {'id':'scan-1'})]
        with patch.object(wb.request_auth, 'authenticate', return_value=('alice', 'admin')):
            for path, body, method, args, expected, backend in cases:
                with self.subTest(path=path), patch.object(wb.stages_resident, method, return_value=backend) as call:
                    self.assertEqual(self.json_request(path, body), (202 if path.endswith(('/import','/scan')) else 200, expected))
                    call.assert_called_once_with(*args)

    def test_stage_routes_reject_non_admin_before_backend(self):
        for role in ('data_engineer', 'bi_viewer'):
            with patch.object(wb.request_auth, 'authenticate', return_value=('alice', role)):
                for path in ('/api/stages', '/api/stages/save', '/api/stages/files', '/api/stages/preview', '/api/stages/import', '/api/stages/schedule', '/api/stages/scan'):
                    with self.subTest(role=role, path=path), patch.object(wb, 'stages_resident') as stages:
                        self.assertEqual(self.json_request(path, None if path == '/api/stages' else {})[0], 403)
                        self.assertEqual(stages.mock_calls, [])
        with patch.object(wb.request_auth, 'authenticate', side_effect=wb.request_auth.AuthenticationError('credentials required')):
            self.assertEqual(self.json_request('/api/stages')[0], 401)

    def test_stage_routes_reject_payload_overrides_before_backend(self):
        definition = dict(name='orders', directory='/host/orders', table='raw.orders', mode='append', format='csv')
        selection = dict(name='orders', file='orders.csv', version='v1')
        with patch.object(wb, 'stages_resident') as stages:
            for path in ('/api/stages/files', '/api/stages/preview', '/api/stages/import'):
                valid = {'name': 'orders'} if path.endswith('/files') else selection
                invalid = [{}, {**valid, 'actor': 'other'}, {**valid, 'directory': '/other'}, {**valid, 'table': 'raw.other'}, {**valid, 'path': '/other'}, {**valid, 'role': 'admin'}]
                invalid += [{**valid, key: value} for key in valid for value in ('', None, 1, [])]
                for body in invalid:
                    with self.subTest(path=path, body=body):
                        self.assertEqual(self.json_request(path, body)[0], 400)
            for body in ({}, {**definition, 'actor': 'other'}, {**definition, 'path': '/other'}, {**definition, 'target': 'raw.other'}):
                self.assertEqual(self.json_request('/api/stages/save', body)[0], 400)
            self.assertEqual(stages.mock_calls, [])

    def test_calendar_schedule_http_roundtrip_validation_and_permissions(self):
        from datetime import datetime, timezone
        now = datetime(2026, 10, 18, tzinfo=timezone.utc)
        cases = [dict(kind='interval', every_minutes=5), dict(kind='daily', time='09:01'),
                 dict(kind='once', date='2026-10-19', time='09:01'),
                 dict(kind='weekly', weekdays=[7, 1], time='09:01'),
                 dict(kind='monthly', day_of_month='last', time='09:01')]
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ,
                SHIROKUMA_STATE_DIR=str(Path(temporary) / 'state'), SHIROKUMA_AUTH_MODE='local',
                SHIROKUMA_AUTH_FILE='', SHIROKUMA_DEFAULT_ROLE='admin'), \
                patch.object(wb.request_auth, 'authenticate', return_value=('alice', 'admin')), \
                patch.object(wb.schedule_resident, '_utc', return_value=now):
            wb.jobs_resident.save_job(dict(name='orders', steps=[dict(action='sql', sql='SELECT 1', mode='read-only')]), 'alice', 'admin')
            stage = wb.stages_resident.save_stage(dict(name='orders', directory=str(Path(temporary).resolve()), table='raw.orders', mode='append', format='csv'), 'alice', 'admin')
            for path in ('/api/jobs/schedule', '/api/stages/schedule'):
                extras = {'version': stage['updated_at']} if '/stages/' in path else {'arguments': {}}
                for options in cases:
                    with self.subTest(path=path, options=options):
                        payload = dict(name='orders', enabled=True, **extras, **options)
                        code, result = self.json_request(path, payload)
                        self.assertEqual(code, 200, result)
                        saved = result['schedule']
                        self.assertEqual(saved['kind'], options['kind'])
                        self.assertEqual(saved['auth_mode'], 'local')
                        self.assertEqual(saved['weekdays'], sorted(options.get('weekdays', [])))
                        self.assertEqual(saved['day_of_month'], options.get('day_of_month'))
                        self.assertEqual(saved['date'], options.get('date'))
                        self.assertIsNotNone(saved['next_run_at'])
                        read_path = '/api/stages' if '/stages/' in path else '/api/jobs/schedules'
                        status, data = self.json_request(read_path)
                        persisted = data['stages'][0]['schedule'] if '/stages/' in path else data['schedules'][0]
                        self.assertEqual(persisted['next_run_at'], saved['next_run_at'])
                        if '/stages/' in path:
                            self.assertEqual(self.json_request(path, {**payload, 'version': 'stale'})[0], 400)
                        invalid = [{**payload, 'owner': 'bob'}, {**payload, 'role': 'admin'},
                                   {**payload, 'auth_mode': 'local'}, {**payload, 'weekdays': []},
                                   {**payload, 'day_of_month': True}, {**payload, 'date': '2026-02-30'}]
                        for body in invalid:
                            self.assertEqual(self.json_request(path, body)[0], 400)
                        self.assertEqual(self.json_request(path, {'name': 'orders', 'enabled': False})[1]['schedule']['enabled'], False)
                for role in ('data_engineer', 'bi_viewer'):
                    with patch.object(wb.request_auth, 'authenticate', return_value=('alice', role)):
                        self.assertEqual(self.json_request(path, dict(name='orders', enabled=True, **extras, **cases[2]))[0], 403 if '/stages/' in path or role == 'bi_viewer' else 200)
                with patch.object(wb.request_auth, 'authenticate', return_value=('bob', 'admin')):
                    self.assertEqual(self.json_request(path, dict(name='orders', enabled=True, **extras, **cases[3]))[0], 404 if '/stages/' in path else 403)
                with patch.object(wb.request_auth, 'authenticate', side_effect=wb.request_auth.AuthenticationError('credentials required')):
                    self.assertEqual(self.json_request(path, dict(name='orders', enabled=True, **extras, **cases[3]))[0], 401)
            # Old stage clients may omit kind; interval saving and minimal pause remain valid.
            legacy = dict(name='orders', every_minutes=3, enabled=True, version=stage['updated_at'])
            self.assertEqual(self.json_request('/api/stages/schedule', legacy)[1]['schedule']['kind'], 'interval')
            accounts = Path(temporary) / 'accounts.json'
            accounts.write_text(json.dumps({'alice': {'role': 'admin'}}))
            with patch.dict(os.environ, SHIROKUMA_AUTH_MODE='shared', SHIROKUMA_AUTH_FILE=str(accounts)):
                for path in ('/api/jobs/schedule', '/api/stages/schedule'):
                    extras = {'version': stage['updated_at']} if '/stages/' in path else {}
                    code, result = self.json_request(path, dict(name='orders', enabled=True, **extras, **cases[3]))
                    self.assertEqual(code, 200, result)
                    self.assertEqual(result['schedule']['auth_mode'], 'shared')
                    accounts.write_text('{}')
                    self.assertEqual(self.json_request(path, {'name': 'orders', 'enabled': False})[0], 403)
                    accounts.write_text(json.dumps({'alice': {'role': 'admin'}}))

    def test_stage_schedule_scan_retry_validation(self):
        selection = dict(name='orders', file='orders.csv', version='v1')
        invalid = {
            '/api/stages/schedule': [{}, {'name':'orders','enabled':True}, {'name':'orders','enabled':True,'every_minutes':5}, {'name':'orders','enabled':True,'every_minutes':5,'version':False}, {'name':'orders','enabled':0}, {'name':'orders','enabled':False,'actor':'other'}] +
                [{'name':'orders','enabled':True,'every_minutes':n} for n in (0,10081,True,1.5,'5',None)],
            '/api/stages/scan': [{}, {'name':''}, {'name':'orders'}, {'name':'orders','version':''}, {'name':'orders','version':True}, {'name':'orders','version':'r1','role':'admin'}, {'name':'orders','version':'r1','table':'raw.other'}],
            '/api/stages/import': [{**selection,'retry':value} for value in (1,'true',None,[],{})],
            '/api/stages/preview': [{**selection,'retry':True}],
        }
        with patch.object(wb, 'stages_resident') as stages:
            for path, bodies in invalid.items():
                for body in bodies:
                    with self.subTest(path=path, body=body):
                        self.assertEqual(self.json_request(path, body)[0], 400)
            self.assertEqual(stages.mock_calls, [])
        with patch.object(wb.stages_resident, 'submit_import', return_value={'id':'retry'}) as submit:
            for retry in (True,False):
                self.assertEqual(self.json_request('/api/stages/import', {**selection,'retry':retry})[0], 202)
                submit.assert_called_with('orders','orders.csv','v1','local','admin',retry=retry)

    def test_stage_http_errors(self):
        for error, code in [(wb.jobs_resident.JobNotFound('missing'), 404), (wb.jobs_resident.JobConflict('busy'), 409), (ValueError('changed file'), 400), (PermissionError('owner only'), 403)]:
            with self.subTest(code=code), patch.object(wb.stages_resident, 'list_files', side_effect=error):
                self.assertEqual(self.json_request('/api/stages/files', {'name': 'orders'})[0], code)

    @unittest.skipUnless(shutil.which('node'), 'JavaScript verification requires Node.js')
    def test_calendar_schedule_controls_payloads_readback_and_async_permissions(self):
        doc = self.workbench_document()
        script = next(node['text'] for node in doc.nodes if node['tag'] == 'script')
        shared = 'const scheduleFields=' + script.split('const scheduleFields=', 1)[1].split('function stageCurrent(', 1)[0]
        jobs = 'function jobSchedulePayload(' + script.split('function jobSchedulePayload(', 1)[1].split("$('job-parameter-example')", 1)[0]
        for prefix in ('job-schedule', 'stage-schedule'):
            for field in ('kind', 'minutes', 'time', 'timezone', 'date', 'day', *('weekday-' + str(day) for day in range(1, 8))):
                self.assertTrue(any(node.get('for') == prefix + '-' + field for node in doc.nodes))
            kind = doc.by_id(prefix + '-kind')
            self.assertEqual([node['value'] for node in doc.nodes if node['tag'] == 'option' and kind in node['ancestors']],
                             ['interval', 'daily', 'once', 'weekly', 'monthly'])
        busy_handler = 'async function busy(' + script.split('async function busy(', 1)[1].split('function aiStatus(', 1)[0]
        harness = r"""
const assert=require('node:assert/strict');
const nodes={};const $=id=>nodes[id]??={value:'',checked:false,disabled:false,hidden:false,children:[],querySelectorAll(){return [];},replaceChildren(...items){this.children=items;},append(...items){this.children.push(...items);},reportValidity(){return true;},setAttribute(){},removeAttribute(){}};
let language='en',currentRole='admin',stageEpoch=1,jobScheduleRequest=0;
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
        result = subprocess.run(['node', '-e', source], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('calendar schedule behavior complete', result.stdout)

    def test_stage_ui_stays_in_ingestion_and_requires_preview(self):
        doc = self.workbench_document()
        area = doc.by_id('stages-area')
        self.assertIn(doc.by_id('ingestion'), area['ancestors'])
        self.assertIn('hidden', area)
        self.assertEqual(doc.by_id('stage-form')['tag'], 'form')
        self.assertEqual(doc.by_id('stage-error')['role'], 'alert')
        self.assertEqual(doc.by_id('stage-state')['role'], 'status')
        self.assertIn('disabled', doc.by_id('stage-import'))
        for identifier in ('stage-name', 'stage-directory', 'stage-table', 'stage-mode', 'stage-format', 'stage-key', 'stage-file'):
            self.assertTrue(any(node.get('for') == identifier for node in doc.nodes))
        script = next(node['text'] for node in doc.nodes if node['tag'] == 'script')
        for text in ("epoch===stageEpoch", "stageEpoch", "invalidateStagePreview()", "if(!stagePreview)return", "stageDirty||!stageImportAllowed()", "'/api/stages/preview'", "'/api/stages/import'", "selectedRunId=stageRunId;activate($('tab-jobs'))", "stage_import:'Stage import'", "'Stage import':'ステージ取込'", "'View run history':'実行履歴を見る'"):
            self.assertIn(text, script)

    def test_stage_localized_display_and_contained_tables(self):
        html = wb.INDEX.read_text()
        doc = self.workbench_document()
        script = next(node['text'] for node in doc.nodes if node['tag'] == 'script')
        for text in ("ui(row.cells[2],entry.definition.mode)", "jobAlertTimestamp(file.modified_at)", "mode:t(definition.mode)", "'Automatic':'自動判定'", '取込済みの内容はファイル名を変更しても除外します。'):
            self.assertIn(text, script)
        format_control = doc.by_id('stage-format')
        automatic = next(node for node in doc.nodes if node.get('value') == 'auto' and format_control in node['ancestors'])
        self.assertEqual(automatic['text'], 'Automatic')
        self.assertIn('#stages-area .table-wrap{max-width:100%;overflow:auto}', html)
        self.assertIn('#stages-list th,#stages-list td,#stage-files th,#stage-files td{white-space:normal;overflow-wrap:anywhere}', html)

    @unittest.skipUnless(shutil.which('node'), 'JavaScript verification requires Node.js')
    def test_stage_preview_invalidation_and_role_change_pending_guard(self):
        script = next(node['text'] for node in self.workbench_document().nodes if node['tag'] == 'script')
        handlers = script.split('let stageEntries=', 1)[1].split('let selectedModel=null;', 1)[0]
        handlers = 'let stageEntries=' + handlers
        harness = r"""
const assert=require('node:assert/strict');
const nodes={};const $=id=>nodes[id]??={value:'',hidden:false,disabled:false,elements:[],replaceChildren(){this.children=[];},setAttribute(){},reset(){},append(){},focus(){}};
const ui=(node,text)=>{node.textContent=text;};const message=(id,text)=>ui($(id),text);const pruneUIBindings=()=>{};
let currentRole='admin',activeTab='ingestion',selectedRunId=null;
const calls=[];let respond;const api=(path,body)=>{calls.push({path,body});return new Promise(resolve=>{respond=resolve;});};
const activate=()=>{};const table=()=>{throw Error('Stale response must not render a table');};
const document={createElement:()=>({append(){}})};
eval(HANDLERS+`
(async()=>{
 selectedStage={name:'orders',can_manage:true,definition:{mode:'append',table:'raw.orders'}};
 stageFiles=[{name:'orders.csv',version:'v1',available:true}];$('stage-file').value='orders.csv';
 stagePreview={name:'orders',file:'orders.csv',version:'v1'};stageControls();assert.equal($('stage-import').disabled,false);
 $('stage-form').oninput();assert.equal(stagePreview,null);assert.equal($('stage-import').disabled,true);assert.equal(stageDirty,true);
 stageDirty=false;stageControls();
 const pending=$('stage-preview-button').onclick();assert.equal(stagePending,true);assert.equal($('stage-file').disabled,true);
 currentRole='data_engineer';resetStageUI();assert.equal($('stages-area').hidden,true);assert.equal(selectedStage,null);
 respond({columns:[{name:'secret'}],sample_rows:[['secret']],row_count:1});await pending;
 assert.equal(stagePreview,null);assert.equal($('stage-import').disabled,true);assert.equal(stagePending,false);
 assert.equal(calls.length,1);assert.deepEqual(calls[0].body,{name:'orders',file:'orders.csv',version:'v1'});
})().catch(error=>{console.error(error);process.exitCode=1;});`);
"""
        result = subprocess.run(['node', '-e', 'const HANDLERS=' + json.dumps(handlers) + ';' + harness], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'JavaScript verification requires Node.js')
    def test_stage_preview_uses_current_server_settings_after_external_edit(self):
        script = next(node['text'] for node in self.workbench_document().nodes if node['tag'] == 'script')
        handlers = 'let stageEntries=' + script.split('let stageEntries=', 1)[1].split('let selectedModel=null;', 1)[0]
        harness = r"""
const assert=require('node:assert/strict');
const nodes={};const $=id=>nodes[id]??={value:'',hidden:false,disabled:false,elements:[],replaceChildren(){},setAttribute(){},append(){}};
const ui=(node,text,values)=>{node.textContent=text;node.values=typeof values==='function'?values():values;};const message=(id,text)=>ui($(id),text);const pruneUIBindings=()=>{};const t=value=>value;
let currentRole='admin',activeTab='ingestion',selectedRunId=null;
const calls=[];let respond;const api=(path,body)=>{calls.push({path,body});return new Promise(resolve=>{respond=resolve;});};
const activate=()=>{};const table=()=>{};const document={createElement:()=>({append(){}})};
eval(HANDLERS+`
(async()=>{
 selectedStage={name:'orders',can_manage:true,definition:{name:'orders',mode:'append',table:'raw.old_target'}};
 stageFiles=[{name:'orders.csv',version:'revision-2-file-token',available:true}];$('stage-file').value='orders.csv';
 const pending=$('stage-preview-button').onclick();
 const authoritative={name:'orders',directory:'/host/new',table:'raw.new_target',mode:'replace',format:'csv'};
 respond({stage:authoritative,columns:[{name:'id',type:'bigint'}],sample_rows:[[1]],row_count:1});await pending;
 assert.deepEqual($('stage-confirmation').values,{file:'orders.csv',table:'raw.new_target',mode:'replace',count:1});
 assert.equal(selectedStage.definition.table,'raw.new_target');assert.equal($('stage-table').value,'raw.new_target');assert.equal($('stage-mode').value,'replace');
 assert.equal($('stage-append-notice').hidden,true);assert.equal($('stage-import').disabled,false);
 const importing=$('stage-import').onclick();assert.equal(calls.at(-1).path,'/api/stages/import');
 assert.deepEqual(calls.at(-1).body,{name:'orders',file:'orders.csv',version:'revision-2-file-token'});
 respond({run:{id:'run-new-settings'}});await importing;
 assert.equal(stageRunId,'run-new-settings');assert.equal(stagePreview,null);
})().catch(error=>{console.error(error);process.exitCode=1;});`);
"""
        result = subprocess.run(['node', '-e', 'const HANDLERS=' + json.dumps(handlers) + ';' + harness], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'JavaScript verification requires Node.js')
    def test_stage_schedule_stop_retry_and_stale_async_behavior(self):
        script = next(node['text'] for node in self.workbench_document().nodes if node['tag'] == 'script')
        handlers = 'let stageEntries=' + script.split('let stageEntries=', 1)[1].split('let selectedModel=null;', 1)[0]
        handlers += '\nconst jobStatuses=' + script.split('const jobStatuses=', 1)[1].split(';', 1)[0] + ';'
        harness = r"""
const assert=require('node:assert/strict');
const nodes={};const $=id=>nodes[id]??={value:'',checked:false,hidden:false,disabled:false,elements:[],querySelectorAll(){return [];},replaceChildren(){},setAttribute(){},reset(){},append(){},focus(){}};
const uiTexts=[];const ui=(node,text)=>{uiTexts.push(text);node.textContent=text;};const message=(id,text)=>ui($(id),text);const pruneUIBindings=()=>{};const t=value=>value;
let currentRole='admin',activeTab='warehouse',selectedRunId=null;
const calls=[];let respond;const api=(path,body)=>{calls.push({path,body});return new Promise(resolve=>{respond=resolve;});};
const activate=()=>{};const table=()=>{};const actionButton=()=>({});const jobAlertTimestamp=value=>value;
const document={createElement:()=>({append(){}})};
eval(HANDLERS+`
(async()=>{
 const entry={name:'orders',updated_at:'revision-1',can_manage:true,definition:{name:'orders',mode:'append',table:'raw.orders'},schedule:null};
 editStage(entry);assert.equal($('stage-schedule-enabled').checked,false);
 $('stage-schedule-minutes').value='7';$('stage-schedule-enabled').checked=true;
 let pending=$('stage-schedule-form').onsubmit({preventDefault(){}});
 assert.deepEqual(calls.at(-1),{path:'/api/stages/schedule',body:{name:'orders',kind:'interval',every_minutes:7,enabled:true,version:'revision-1'}});
 respond({schedule:{name:'orders',enabled:true,every_minutes:7}});await pending;
 assert.equal(selectedStage.schedule.enabled,true);assert.equal(stagePending,false);
 // Each calendar kind is saved explicitly and read back without losing the stage revision.
 for(const calendar of [
  {kind:'daily',time:'09:45',timezone:'Asia/Tokyo',enabled:true},
  {kind:'once',date:'2030-04-21',time:'12:00',timezone:'Europe/London',enabled:true},
  {kind:'weekly',weekdays:[2,6],time:'16:10',timezone:'America/New_York',enabled:true},
  {kind:'monthly',day_of_month:'last',time:'23:00',timezone:'Asia/Tokyo',enabled:true}
 ]){
  fillScheduleFields('stage-schedule',calendar);pending=$('stage-schedule-form').onsubmit({preventDefault(){}});
  assert.deepEqual(calls.at(-1),{path:'/api/stages/schedule',body:{name:'orders',...calendar,version:'revision-1'}});
  respond({schedule:{name:'orders',...calendar}});await pending;
  assert.deepEqual(schedulePayload(readScheduleFields('stage-schedule')),calendar);assert.equal(selectedStage.updated_at,'revision-1');
 }
 fillScheduleFields('stage-schedule',{kind:'interval',every_minutes:7,enabled:true});
 // A completed check is successful even when it imports no files.
 for(const [status,label] of [['success','Succeeded'],['failed','Failed'],['skipped','Skipped'],['unknown','Unknown outcome']]){
  selectedStage.schedule.last_status=status;uiTexts.length=0;renderStageSchedule();assert.ok(uiTexts.includes(label));assert.ok(!uiTexts.includes('Already imported'));
 }
 assert.equal(stageIngestionStatus('success'),'Already imported');
 // Stopping ignores invalid interval and unsaved definition fields.
 $('stage-schedule-minutes').value='bad';$('stage-form').oninput();assert.equal($('stage-schedule-stop').disabled,false);
 pending=$('stage-schedule-stop').onclick();assert.deepEqual(calls.at(-1).body,{name:'orders',enabled:false});
 respond({schedule:{name:'orders',enabled:false,every_minutes:7}});await pending;assert.equal(selectedStage.schedule.enabled,false);
 stageDirty=false;let count=calls.length;
 await $('stage-schedule-form').onsubmit({preventDefault(){}});assert.equal(calls.length,count);
 // Successful/inflight content cannot be imported even by invoking the handler directly.
 for(const status of ['success','queued','running']){stagePreview={name:'orders',file:'a.csv',version:'v1',ingestion_status:status};stageControls();assert.equal($('stage-import').disabled,true);$('stage-import').onclick();assert.equal(calls.length,count);}
 for(const status of ['failed','unknown']){
  stagePreview={name:'orders',file:'a.csv',version:'v1',ingestion_status:status};$('stage-retry').checked=false;stageControls();
  assert.equal($('stage-import').disabled,true);$('stage-import').onclick();assert.equal(calls.length,count);
  $('stage-retry').checked=true;$('stage-retry').onchange();assert.equal($('stage-import').disabled,false);
  $('stage-import').onclick();assert.deepEqual(calls.at(-1).body,{name:'orders',file:'a.csv',version:'v1',retry:true});
  respond({run:{id:'retry-run'}});await new Promise(resolve=>setImmediate(resolve));count=calls.length;
  assert.equal($('stage-retry').checked,false);assert.equal(stagePreview,null);
 }
 // Refreshing an externally edited stage updates the visible definition and its revision together.
 stageRunId='keep-history';$('stage-view-run').hidden=false;
 stagePreview={name:'orders',file:'a.csv',version:'v1',ingestion_status:'new'};
 const revised={...entry,updated_at:'revision-2',definition:{...entry.definition,table:'raw.new_destination',mode:'upsert',key:'id'},schedule:{enabled:false,every_minutes:9}};
 pending=fetchStageFiles(stageEpoch);respond({stages:[revised]});await new Promise(resolve=>setImmediate(resolve));respond({files:[]});await pending;
 assert.equal(selectedStage.updated_at,'revision-2');assert.equal(selectedStage.definition.table,'raw.new_destination');
 assert.equal($('stage-table').value,'raw.new_destination');assert.equal($('stage-mode').value,'upsert');assert.equal($('stage-key').value,'id');
 assert.equal(stagePreview,null);assert.equal(stageRunId,'keep-history');assert.equal($('stage-view-run').hidden,false);
 $('stage-schedule-enabled').checked=true;pending=$('stage-schedule-form').onsubmit({preventDefault(){}});
 assert.deepEqual(calls.at(-1).body,{name:'orders',kind:'interval',every_minutes:9,enabled:true,version:'revision-2'});
 respond({schedule:{enabled:true,every_minutes:9}});await pending;
 pending=$('stage-scan').onclick();assert.deepEqual(calls.at(-1).body,{name:'orders',version:'revision-2'});
 respond({run:{id:'new-scan'}});await new Promise(resolve=>setImmediate(resolve));respond({stages:[revised]});await new Promise(resolve=>setImmediate(resolve));respond({files:[]});await pending;
 assert.equal(stageRunId,'new-scan');assert.equal($('stage-view-run').hidden,false);
 // A late schedule response cannot restore state after role changes, even if admin returns.
 $('stage-schedule-minutes').value='5';pending=$('stage-schedule-form').onsubmit({preventDefault(){}});
 currentRole='bi_viewer';resetStageUI();currentRole='admin';resetStageUI();respond({schedule:{enabled:true}});await pending;
 assert.equal(selectedStage,null);assert.equal($('stage-schedule-enabled').checked,false);assert.equal(stagePending,false);
 // Legacy imports remain inspectable and pausable but cannot bypass missing dedup records.
 editStage({...entry,legacy_history:true,schedule:{enabled:true}});stageControls();
 assert.equal($('stage-legacy-notice').hidden,false);assert.equal($('stage-scan').disabled,true);assert.equal($('stage-schedule-stop').disabled,false);
 stagePreview={name:'orders',file:'a.csv',version:'v1',ingestion_status:'new'};stageControls();assert.equal($('stage-import').disabled,true);
 count=calls.length;$('stage-import').onclick();await $('stage-scan').onclick();await $('stage-schedule-form').onsubmit({preventDefault(){}});assert.equal(calls.length,count);
 // Replace allows pausing an existing schedule but cannot scan or save an enabled schedule.
 editStage({...entry,definition:{...entry.definition,mode:'replace'},schedule:{enabled:true}});stageControls();
 assert.equal($('stage-scan').disabled,true);assert.equal($('stage-schedule-enabled').disabled,true);assert.equal($('stage-schedule-stop').disabled,false);
 count=calls.length;await $('stage-scan').onclick();await $('stage-schedule-form').onsubmit({preventDefault(){}});assert.equal(calls.length,count);
 // Scan is stage-only. A stale response after losing admin cannot show a history link.
 editStage(entry);pending=$('stage-scan').onclick();assert.deepEqual(calls.at(-1),{path:'/api/stages/scan',body:{name:'orders',version:'revision-1'}});
 currentRole='data_engineer';resetStageUI();respond({run:{id:'late-scan'}});await pending;
 assert.equal(stageRunId,null);assert.equal($('stage-view-run').hidden,true);assert.equal($('stages-area').hidden,true);
 console.log('stage behavioral checks complete');
})().catch(error=>{console.error(error);process.exitCode=1;});`);
"""
        result = subprocess.run(['node', '-e', 'const HANDLERS=' + json.dumps(handlers) + ';' + harness], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('stage behavioral checks complete', result.stdout)

    @unittest.skipUnless(shutil.which('node'), 'JavaScript verification requires Node.js')
    def test_stage_refresh_reconciles_schedule_without_discarding_work(self):
        script = next(node['text'] for node in self.workbench_document().nodes if node['tag'] == 'script')
        handlers = 'let stageEntries=' + script.split('let stageEntries=', 1)[1].split('let selectedModel=null;', 1)[0]
        harness = r"""
const assert=require('node:assert/strict');
const nodes={};const $=id=>nodes[id]??={value:'',checked:false,hidden:false,disabled:false,elements:[],children:[],querySelectorAll(){return [];},replaceChildren(...children){this.children=children;this.textContent='';if(this===nodes['stage-file'])this.value='';},setAttribute(){},reset(){},append(...children){this.children.push(...children);},focus(){}};
const ui=(node,text)=>{node.textContent=text;};const message=(id,text)=>ui($(id),text);const pruneUIBindings=()=>{};const t=value=>value;
let currentRole='admin',activeTab='warehouse',selectedRunId=null;
let respond;const calls=[];const api=(path,body)=>{calls.push({path,body});return new Promise(resolve=>{respond=resolve;});};
const activate=()=>{};const table=()=>{};const actionButton=()=>({});const jobAlertTimestamp=value=>value;const jobStatuses={success:'Succeeded'};
const document={createElement:()=>({append(){}})};
eval(HANDLERS+`
(async()=>{
 const entry={name:'orders',updated_at:'revision-1',can_manage:true,definition:{name:'orders',directory:'/host/orders',table:'raw.orders',mode:'append'},schedule:{enabled:false,every_minutes:7}};
 editStage(entry);stageFiles=[{name:'orders.csv',available:true}];$('stage-file').value='orders.csv';
 const preview={name:'orders',file:'orders.csv',version:'file-v1',ingestion_status:'new'};stagePreview=preview;
 $('stage-preview').replaceChildren('preview');$('stage-exports').replaceChildren('output-list');
 $('stage-export-table').value='raw.custom';$('stage-export-file').value='custom.csv';stageRunId='keep-history';$('stage-view-run').hidden=false;
 // A second browser can enable or pause without changing the definition revision.
 for(const enabled of [true,false]){
  const fresh={...entry,schedule:{enabled,every_minutes:9,last_status:'success'}};
  const pending=$('stages-refresh').onclick();respond({stages:[fresh]});await pending;
  assert.equal(selectedStage.schedule.enabled,enabled);assert.equal($('stage-schedule-enabled').checked,enabled);
  assert.equal($('stage-schedule-minutes').value,9);assert.equal($('stage-schedule-stop').disabled,!enabled);
  assert.equal($('stage-schedule-state').children[0].textContent,enabled?'Enabled':'Disabled');
  assert.equal(stagePreview,preview);assert.deepEqual($('stage-preview').children,['preview']);assert.equal($('stage-import').disabled,false);
  assert.equal($('stage-file').value,'orders.csv');assert.deepEqual($('stage-exports').children,['output-list']);
  assert.equal($('stage-export-table').value,'raw.custom');assert.equal($('stage-export-file').value,'custom.csv');assert.equal(stageRunId,'keep-history');
 }
 // A calendar-only edit from another tab replaces all visible schedule fields and retains the preview.
 let pending=$('stages-refresh').onclick();respond({stages:[{...entry,schedule:{kind:'weekly',enabled:true,time:'11:20',timezone:'Europe/London',weekdays:[2,4]}}]});await pending;
 assert.equal($('stage-schedule-kind').value,'weekly');assert.equal($('stage-schedule-time').value,'11:20');assert.equal($('stage-schedule-timezone').value,'Europe/London');
 assert.equal($('stage-schedule-weekday-2').checked,true);assert.equal($('stage-schedule-weekday-1').checked,false);assert.equal(stagePreview,preview);
 pending=$('stages-refresh').onclick();respond({stages:[{...entry,schedule:{enabled:false,every_minutes:9}}]});await pending;
 // Unchanged server schedule values do not overwrite an unsaved interval or checkbox.
 $('stage-schedule-minutes').value='13';$('stage-schedule-enabled').checked=true;
 pending=$('stages-refresh').onclick();respond({stages:[{...selectedStage,schedule:{...selectedStage.schedule,last_status:'skipped'}}]});await pending;
 assert.equal($('stage-schedule-minutes').value,'13');assert.equal($('stage-schedule-enabled').checked,true);assert.equal(selectedStage.schedule.enabled,false);
 // Schedule-only changes preserve dirty definition fields while updating the pause control.
 $('stage-table').value='raw.unsaved';$('stage-form').oninput();
 pending=$('stages-refresh').onclick();respond({stages:[{...entry,schedule:{enabled:true,every_minutes:11}}]});await pending;
 assert.equal(stageDirty,true);assert.equal($('stage-table').value,'raw.unsaved');assert.equal($('stage-schedule-stop').disabled,false);
 assert.equal($('stage-schedule-save').disabled,true);assert.equal(stageRunId,'keep-history');
 // Adopting a new directory revision invalidates all previous input/output choices, retaining run history.
 stageFiles=[{name:'old.csv'}];$('stage-file').value='old.csv';$('stage-file').replaceChildren('old-option');$('stage-file').value='old.csv';$('stage-files').replaceChildren('old-list');
 $('stage-exports').replaceChildren('old-output');stagePreview=preview;
 const revised={...entry,updated_at:'revision-2',definition:{...entry.definition,directory:'/host/new',table:'raw.new'}};
 pending=refreshStageSchedule(stageEpoch);respond({stages:[revised]});await pending;
 assert.equal(selectedStage.updated_at,'revision-2');assert.equal(stageDirty,false);assert.equal($('stage-directory').value,'/host/new');
 assert.deepEqual(stageFiles,[]);assert.equal($('stage-file').value,'');assert.deepEqual($('stage-file').children,[]);assert.deepEqual($('stage-files').children,[]);
 assert.equal(stagePreview,null);assert.deepEqual($('stage-preview').children,[]);assert.deepEqual($('stage-exports').children,[]);
 assert.equal($('stage-export-directory').textContent,'/host/new/exports');assert.equal(stageRunId,'keep-history');assert.equal($('stage-view-run').hidden,false);
 // A refresh issued under an earlier admin epoch cannot restore selection after a role reset.
 pending=$('stages-refresh').onclick();currentRole='bi_viewer';resetStageUI();currentRole='admin';resetStageUI();respond({stages:[entry]});await pending;
 assert.equal(selectedStage,null);assert.deepEqual(stageEntries,[]);assert.equal(stagePending,false);assert.equal($('stage-schedule-enabled').checked,false);
 assert.ok(calls.every(call=>call.path==='/api/stages'));
 console.log('stage refresh behavioral checks complete');
})().catch(error=>{console.error(error);process.exitCode=1;});`);
"""
        result = subprocess.run(['node', '-e', 'const HANDLERS=' + json.dumps(handlers) + ';' + harness], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('stage refresh behavioral checks complete', result.stdout)

    def test_invalid_roles_rejected_on_get_and_post(self):
        with patch.object(wb.bears_resident, 'load_ai_config') as load, \
             patch.object(wb.warehouse_resident, 'transition') as transition:
            for role in ('unknown', 'admn', '', 'administrator'):
                for route, body in (('/api/ai/config', None), ('/api/warehouse/wake', {})):
                    with self.subTest(role=role, route=route):
                        self.assertEqual(self.json_request(route + '?role=' + role, body)[0], 403)
                        self.assertEqual(self.request(route, body, headers={
                            'Content-Type': 'application/json', 'X-Shirokuma-Role': role})[0], 403)
            self.assertEqual(self.request('/api/ai/config?role=admn', headers={
                'X-Shirokuma-Role': 'admin'})[0], 403)
            load.assert_not_called()
            transition.assert_not_called()

    def test_role_fallback_and_explicit_precedence(self):
        with patch.object(wb.bears_resident, 'load_ai_config', return_value={}), \
             patch.dict(os.environ, {'SHIROKUMA_DEFAULT_ROLE': 'bi_viewer'}):
            self.assertEqual(self.json_request('/api/ai/config')[0], 403)
            for user, expected in (('admin', 403), ('root', 403), ('powerbi', 403), ('bi_reader', 403)):
                self.assertEqual(self.request('/api/ai/config', headers={'X-Trino-User': user})[0], expected)
            self.assertEqual(self.request('/api/ai/config', headers={
                'X-Trino-User': 'bi_reader', 'X-Shirokuma-Role': 'admin'})[0], 403)
            self.assertEqual(self.json_request('/api/ai/config?role=admin')[0], 403)

    def test_restricted_default_cannot_be_overridden_by_selectors(self):
        with patch.dict(os.environ, {'SHIROKUMA_DEFAULT_ROLE': 'bi_viewer'}), \
             patch.object(wb.bears_resident, 'load_ai_config') as load, \
             patch.object(wb.warehouse_resident, 'query') as query:
            for user in ('', 'ordinary_person', 'bi_reader', 'unknown', 'admin'):
                for selector in ('admin', 'data_engineer'):
                    headers = {'X-Trino-User': user, 'X-Shirokuma-Role': selector}
                    self.assertEqual(self.request('/api/ai/config', headers=headers)[0], 403)
                    self.assertEqual(self.request('/api/ai/config?role=' + selector,
                        headers={'X-Trino-User': user})[0], 403)
                    self.assertEqual(self.request('/api/warehouse/query',
                        {'sql': 'SELECT * FROM raw.orders'}, headers={
                            **headers, 'Content-Type': 'application/json'})[0], 403)
            load.assert_not_called()
            query.assert_not_called()

    def test_catalog_bear_restricts_all_table_reads_before_dispatch(self):
        with patch.object(wb.bears_resident, 'run', return_value={}) as run:
            for action in ('describe', 'search', 'lineage'):
                for field in ('table', 'target'):
                    for schema in ('raw', 'sandbox', 'staging'):
                        for prefix in ('', 'iceberg.'):
                            body = {'bear': 'catalog', 'action': action,
                                    field: prefix + schema + '.orders', 'snapshot_id': '123'}
                            code, data = self.json_request('/api/bears/run?role=bi_viewer', body)
                            self.assertEqual(code, 403, data)
                            self.assertIn('marts', data['error'])
            run.assert_not_called()
            self.assertEqual(self.json_request('/api/bears/run?role=bi_viewer', {
                'bear': 'catalog', 'action': 'describe', 'table': 'marts.orders',
                'snapshot_id': '123'})[0], 200)
            self.assertEqual(run.call_args.args[0].role, 'bi_viewer')

        from types import SimpleNamespace
        with patch.object(wb.bears_resident.wr, 'trino') as trino:
            for action in ('describe', 'lineage', 'search'):
                args = SimpleNamespace(bear='catalog', role='bi_viewer', action=action,
                                       table='raw.orders', snapshot_id='123')
                for dispatch in (wb.bears_resident.catalog, wb.bears_resident.run):
                    with self.assertRaises(PermissionError):
                        dispatch(args)
            trino.assert_not_called()

    def test_role_typos_escalation_and_catalog_boundaries(self):
        with patch.object(wb.bears_resident, 'catalog', return_value={}) as catalog:
            for user in ('bi_reader', 'powerbi', 'bi_team'):
                for role in ('admin', 'data_engineer', 'editor'):
                    self.assertEqual(self.request('/api/catalog?role=' + role,
                                     headers={'X-Trino-User': user})[0], 403)
            for selector in ('roel=admin', 'Role=admin', 'roles=admin'):
                self.assertEqual(self.json_request('/api/catalog?' + selector)[0], 403)
            for schema in ('raw', 'staging', 'sandbox'):
                for prefix in ('', 'iceberg.'):
                    self.assertEqual(self.json_request(
                        '/api/catalog?role=bi_viewer&detail=full&table=' + prefix + schema + '.orders')[0], 403)
            catalog.assert_not_called()
            self.assertEqual(self.json_request('/api/catalog?role=bi_viewer&table=marts.orders')[0], 200)

    def test_decimal_json_preview_serializes_exactly(self):
        for extension, content in (
                ('json', '[{"amount":12.34},{"amount":9007199254740993.25}]'),
                ('jsonl', '{"amount":12.34}\n{"amount":9007199254740993.25}')):
            with self.subTest(extension=extension):
                code, result = self.json_request('/api/lake/preview', {
                    'filename': 'orders.' + extension, 'content': content})
                self.assertEqual(code, 200, result)
                self.assertIn('12.34', json.dumps(result))
                self.assertIn('9007199254740993.25', json.dumps(result))

    def test_ai_routes_are_admin_only(self):
        with patch.object(wb.bears_resident, 'load_ai_config', return_value={}) as load, \
             patch.object(wb.bears_resident, 'save_ai_config', return_value={}) as save, \
             patch.object(wb.bears_resident, 'test_ai_connection', return_value={}) as connection:
            for role in ('data_engineer', 'editor', 'bi_viewer', 'viewer', 'bi_reader'):
                for route, body in (('/api/ai/config', None), ('/api/ai/config', {}), ('/api/ai/test', {})):
                    with self.subTest(role=role, route=route, body=body):
                        self.assertEqual(self.json_request(route + '?role=' + role, body)[0], 403)
            for mock in (load, save, connection):
                mock.assert_not_called()
            for route, body in (('/api/ai/config', None), ('/api/ai/config', {}), ('/api/ai/test', {})):
                self.assertEqual(self.json_request(route + '?role=admin', body)[0], 200)

    def test_drop_schema_permissions(self):
        with patch.object(wb.bears_resident, 'drop_table', return_value={'dropped': True}) as drop:
            for role in ('data_engineer', 'editor'):
                for table in ('marts.summary', 'iceberg.marts.summary', 'scratch.t', 'other.t', 'summary'):
                    self.assertEqual(self.json_request('/api/catalog/drop?role=' + role, {'table': table})[0], 403)
            drop.assert_not_called()
            for role in ('admin', 'data_engineer'):
                for schema in ('sandbox', 'staging', 'raw'):
                    for prefix in ('', 'iceberg.'):
                        table = prefix + schema + '.arbitrary_table'
                        self.assertEqual(self.json_request('/api/catalog/drop?role=' + role, {'table': table})[0], 200)
                        drop.assert_called_with(table)
            for table in ('marts.summary', 'iceberg.marts.summary'):
                self.assertEqual(self.json_request('/api/catalog/drop?role=admin', {'table': table})[0], 200)

    def test_autochill_configuration_requires_admin(self):
        with patch.object(wb.warehouse_resident, 'configure_auto_chill', return_value={}) as configure, \
             patch.object(wb.warehouse_resident, 'auto_chill_status', return_value={}) as status, \
             patch.object(wb.warehouse_resident, 'check_auto_chill', return_value={}) as check:
            for role in ('data_engineer', 'bi_viewer'):
                for body in ({'minutes': 10}, {'minutes': 10, 'check': True}):
                    self.assertEqual(self.json_request('/api/warehouse/autochill?role=' + role, body)[0], 403)
            configure.assert_not_called()
            for role, expected in (('admin', 200), ('data_engineer', 200), ('bi_viewer', 403)):
                self.assertEqual(self.json_request('/api/warehouse/autochill?role=' + role, {'check': False})[0], expected)
                self.assertEqual(self.json_request('/api/warehouse/autochill?role=' + role, {'check': True})[0], expected)
            self.assertEqual(status.call_count, 4)
            self.assertEqual(check.call_count, 2)
            self.assertEqual(self.json_request('/api/warehouse/autochill?role=admin', {'minutes': 10})[0], 200)
            configure.assert_called_once_with(10)

    def test_ai_key_isolation_when_endpoint_changes(self):
        active = dict(provider='openai', api_url='https://old.example/v1', api_key='old-secret')
        for change in ({'provider': 'custom'}, {'api_url': 'https://new.example/v1'},
                       {'provider': 'custom', 'api_url': 'https://new.example/v1'}):
            for key in (None, '********', '', 'new-secret'):
                supplied = {**change, **({'api_key': key} if key is not None else {})}
                with self.subTest(change=change, key=key), \
                     patch.object(wb.bears_resident, 'load_ai_config', return_value=dict(active)), \
                     patch.dict(wb.bears_resident._ai_config, {}, clear=True), \
                     patch.object(wb.bears_resident.wap, 'persist_json') as persist:
                    result = wb.bears_resident.save_ai_config(supplied)
                    self.assertEqual(result['api_key'], 'new-secret' if key == 'new-secret' else '')
                    self.assertEqual(persist.call_args.args[1]['api_key'], result['api_key'])
        for change in ({'model': 'new'}, {'api_url': active['api_url']}, {'api_key': '********'}):
            with patch.object(wb.bears_resident, 'load_ai_config', return_value=dict(active)), \
                 patch.dict(wb.bears_resident._ai_config, {}, clear=True), \
                 patch.object(wb.bears_resident.wap, 'persist_json'):
                self.assertEqual(wb.bears_resident.save_ai_config(change)['api_key'], 'old-secret')

    def test_ai_endpoint_changes_clear_environment_credentials(self):
        bears = wb.bears_resident
        for key_env in ('OPENAI_API_KEY', 'BEAR_API_KEY'):
            active = dict(provider='openai', api_url='https://api.openai.com/v1',
                          api_key='', api_key_env=key_env)
            with patch.object(bears, 'load_ai_config', side_effect=lambda: dict(active)), \
                 patch.dict(os.environ, {'OPENAI_API_KEY': 'openai-secret',
                                         'BEAR_API_KEY': 'bear-secret', 'NEW_KEY': 'new-secret'}):
                for change in ({'api_url': 'https://new.example/v1'}, {'provider': 'custom'}):
                    for key in (None, '', '********'):
                        supplied = {**change, **({'api_key': key} if key is not None else {})}
                        result = bears.merged_ai_config(supplied)
                        self.assertEqual(result['api_key_env'], '')
                        self.assertIsNone(bears.resolve_api_key(result))
                    result = bears.merged_ai_config({**change, 'api_key_env': 'NEW_KEY'})
                    self.assertEqual(bears.resolve_api_key(result), 'new-secret')
                    result = bears.merged_ai_config({**change, 'api_key': 'direct-new'})
                    self.assertEqual(bears.resolve_api_key(result), 'direct-new')
                self.assertEqual(bears.resolve_api_key(bears.merged_ai_config({'model': 'new'})),
                                 os.environ[key_env])
                self.assertIsNone(bears.resolve_api_key({'api_key_env': ''}))

    def test_role_endpoint_permissions_before_dispatch(self):
        routes = ['/api/catalog/drop', '/api/models/build', '/api/lake/ingest',
                  '/api/catalog/clone', '/api/ai/config', '/api/ai/test',
                  '/api/lake/preview', '/api/lake/export', '/api/warehouse/chill']
        with patch.object(wb.bears_resident, 'save_ai_config') as save, \
             patch.object(wb.bears_resident, 'drop_table') as drop, \
             patch.object(wb.lake_ingest, 'ingest') as ingest, \
             patch.object(wb.lake_ingest, 'preview') as preview, \
             patch.object(wb.lake_ingest, 'export_table') as export, \
             patch.object(wb.warehouse_resident, 'transition') as transition:
            for route in routes:
                for selector in ('?role=bi_viewer', '?role=viewer'):
                    with self.subTest(route=route, selector=selector):
                        self.assertEqual(self.json_request(route + selector, {})[0], 403)
            self.assertEqual(self.request('/api/ai/config?role=admin', {}, headers={
                'Content-Type': 'application/json', 'X-Shirokuma-Role': 'data_engineer'})[0], 403)
            save.assert_not_called()
            drop.assert_not_called()
            ingest.assert_not_called()
            preview.assert_not_called()
            export.assert_not_called()
            transition.assert_not_called()

    @patch.object(wb.warehouse_resident, 'query', return_value={'rows': []})
    def test_role_query_enforcement(self, query):
        code, _ = self.json_request('/api/warehouse/query?role=bi_viewer',
                                    {'sql': 'SELECT * FROM marts.t', 'mode': 'admin-write'})
        self.assertEqual(code, 200)
        self.assertEqual(query.call_args.kwargs['mode'], 'read-only')
        query.reset_mock()
        for body in ({'sql': 'SELECT * FROM raw.t'}, {'sql': 'SELECT * FROM iceberg.sandbox.t'},
                     {'sql': 'SELECT * FROM tpch_tiny.orders'},
                     {'sql': 'SELECT 1', 'schema': 'raw'},
                     {'sql': 'INSERT INTO iceberg.marts.t VALUES (1)'},
                     {'sql': 'SELECT 1; SELECT 2'}):
            with self.subTest(body=body):
                self.assertEqual(self.json_request('/api/warehouse/query?role=bi_viewer', body)[0], 403)
        for body in ({'sql': 'SELECT 1', 'mode': 'admin-write'},
                     {'sql': 'CREATE TABLE iceberg.marts.t AS SELECT 1', 'mode': 'sandbox-write'}):
            self.assertEqual(self.json_request('/api/warehouse/query?role=data_engineer', body)[0], 403)
        query.assert_not_called()
        for schema in ('raw', 'sandbox'):
            body = {'sql': f'CREATE TABLE iceberg.{schema}.t AS SELECT 1', 'mode': 'sandbox-write'}
            self.assertEqual(self.json_request('/api/warehouse/query?role=editor', body)[0], 200)

    @patch.object(wb.bears_resident, 'run')
    def test_role_bear_write_bypasses_blocked(self, run):
        for role, actions in [('bi_viewer', ['stage', 'publish', 'refresh', 'rollback']),
                              ('data_engineer', ['publish', 'refresh', 'rollback'])]:
            for action in actions:
                self.assertEqual(self.json_request('/api/bears/run?role=' + role,
                    {'bear': 'data', 'action': action, 'table': 't'})[0], 403)
            self.assertEqual(self.json_request('/api/bears/run?role=' + role,
                {'bear': 'data', 'action': 'generate', 'publish': 't'})[0], 403)
        self.assertEqual(self.json_request('/api/bears/run?role=bi_viewer',
            {'bear': 'data', 'action': 'generate', 'stage_table': 't', 'sql': 'SELECT 1'})[0], 403)
        run.assert_not_called()

    @patch.object(wb.bears_resident, 'save_ai_config', side_effect=RuntimeError('failed to persist AI config: denied'))
    def test_failed_persistence_is_not_http_success(self, save):
        code, data = self.json_request('/api/ai/config', {'model': 'new'})
        self.assertEqual(code, 502)
        self.assertIn('failed to persist AI config', data['error'])

    def test_role_selector_markup_and_translations(self):
        doc = self.workbench_document()
        selector = doc.by_id('role-selector')
        self.assertTrue(any(n.get('for') == 'role-selector' for n in doc.nodes))
        self.assertEqual([n['value'] for n in doc.nodes if n['tag'] == 'option' and selector in n['ancestors']],
                         ['admin', 'data_engineer', 'bi_viewer'])
        self.assertEqual(doc.by_id('role-indicator')['role'], 'status')
        script = next(n['text'] for n in doc.nodes if n['tag'] == 'script')
        for en, ja in [('Admin', '管理者'), ('Data Engineer', 'データ作成者'), ('BI Viewer', '閲覧者')]:
            self.assertIn(f"'{en}':'{ja}'", script)

    @unittest.skipUnless(shutil.which('node'), 'JavaScript verification requires Node.js')
    def test_role_switch_and_api_headers(self):
        script = next(n['text'] for n in self.workbench_document().nodes if n['tag'] == 'script')
        handlers = script.split("let currentRole = 'admin';", 1)[1].split('function message(', 1)[0]
        harness = r"""
const assert=require('node:assert/strict');
const nodes={};const $=id=>nodes[id]??={value:'',disabled:false,hidden:false,options:[]};
const document={documentElement:{dataset:{}}};let selectedTable=null;
$('ai-settings').open=true;$('ai-settings').close=()=>{$('ai-settings').open=false;};
$('query-mode').options=['read-only','sandbox-write','admin-write'].map(value=>({value}));
$('bear-data-action').options=['generate','stage','publish','refresh','rollback'].map(value=>({value}));
$('bear-data-action').selectedOptions=[$('bear-data-action').options[2]];
const calls=[];const fetch=async(path,options)=>{calls.push({path,...options});return {ok:true,json:async()=>({})};};
let currentRole='admin',synced=[];function updateDataBearForm(){synced.push($('bear-data-action').value);}
// Catalog cache and status invalidation have separate harnesses; isolate the role/header contract here.
let catalogGeneration=0;function catalogMutation(){return false;}function catalogIdentity(){return currentRole;}
function invalidateCatalogDetails(){}function observeCatalogRuns(){}function resetCatalogIdentity(){}
function invalidateStatus(){}
function loadCatalog(){}
eval(HANDLERS+`
(async()=>{
 await api('/api/status');assert.equal(calls.at(-1).headers['X-Shirokuma-Role'],'admin');
 $('bear-data-action').value='publish';$('role-selector').value='bi_viewer';$('role-selector').onchange();
 assert.equal(document.documentElement.dataset.role,'bi_viewer');assert.equal(synced.at(-1),'generate','form follows the corrected action');
 assert.equal($('query-mode').value,'read-only');assert.equal($('query-mode').disabled,true);
 assert.equal($('query-schema').value,'marts');assert.equal($('role-indicator').hidden,false);
 assert.equal($('ai-settings').open,false);
 for(const id of ['run-dbt-build','drop-table-btn','run-ingest','ai-settings-button'])assert.equal($(id).disabled,true);
 await api('/api/warehouse/query',{sql:'SELECT 1'});
 assert.equal(calls.at(-1).headers['X-Shirokuma-Role'],'bi_viewer');
 assert.equal(calls.at(-1).headers['Content-Type'],'application/json');
 $('role-selector').value='data_engineer';$('role-selector').onchange();
 assert.equal($('run-ingest').disabled,false);assert.equal($('ai-settings-button').disabled,true);
 assert.equal($('query-mode').options[2].disabled,true);
 $('role-selector').value='admin';$('role-selector').onchange();
 assert.equal($('ai-settings-button').disabled,false);assert.equal($('role-indicator').hidden,true);
 assert.equal($('query-mode').options[2].disabled,false);
 assert.equal($('clone-table-btn').disabled,true);
})().catch(error=>{console.error(error);process.exitCode=1;});`);
"""
        result = subprocess.run(['node', '-e', 'const HANDLERS=' + json.dumps(handlers) + ';' + harness],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'JavaScript verification requires Node.js')
    def test_data_bear_completion_uses_current_action_and_role(self):
        script = next(n['text'] for n in self.workbench_document().nodes if n['tag'] == 'script')
        dictionary = script[script.index('const i18n'):script.index('function ui(')]
        helpers = script[script.index('const dataBearActions='):script.index('// One list of specialists')]
        busy = script[script.index('async function busy('):script.index('function aiStatus(')]
        handler = script[script.index(' const show=(label,tone)=>', script.index('for(const bear of bears)')):script.index("\n}\n$('bear-data-action').onchange")]
        role = script[script.index(" for(const option of $('bear-data-action').options)"):script.index("\n}\n$('role-selector').onchange")]
        harness = r'''
const assert=require('node:assert/strict');
class Element{constructor(){this.value='';this.disabled=false;this.attrs={};this.parentElement={};this.labels=[{}];this.children=[];this.textContent='';}
 setAttribute(k,v){this.attrs[k]=v;}removeAttribute(k){delete this.attrs[k];}getAttribute(k){return this.attrs[k]??null;}
 replaceChildren(...nodes){this.children=nodes;this.firstChild=nodes[0];}checkValidity(){return true;}}
const nodes={},$=id=>nodes[id]??=new Element(),document={createElement:()=>new Element()};
const ui=(node,key)=>{node.textContent=t(key);},pruneUIBindings=()=>{};
const button=$('bear-data-run'),state=new Element(),tabState=new Element(),output=new Element();
const fields=['action','request','sql','stage_table','publish','refresh','rollback','snapshot_id'];
const bear={key:'data',fields:fields.map(key=>[key])};
const content={querySelectorAll:()=>fields.map(key=>$('bear-data-'+key))};
let calls=[],pending=[],currentRole='admin';
const api=(path,payload)=>{calls.push(payload);return new Promise((resolve,reject)=>pending.push({resolve,reject}));};
$('bear-data-action').options=['generate','stage','publish','refresh','rollback'].map(value=>({value}));
Object.defineProperty($('bear-data-action'),'selectedOptions',{get(){return this.options.filter(o=>o.value===this.value);}});
const applyRole=()=>{const viewer=currentRole==='bi_viewer',engineer=currentRole==='data_engineer';eval(ROLE);};
eval(HANDLER);
for(const [key,value] of Object.entries({request:'draft',sql:'SELECT 1',stage_table:'sandbox.t',publish:'sandbox.p',refresh:'marts.f',rollback:'marts.r',snapshot_id:'9223372036854775807'}))$('bear-data-'+key).value=value;
(async()=>{
 for(const fail of [false,true]){
  $('bear-data-action').value='generate';updateDataBearForm();const count=calls.length;
  const run=button.onclick();assert.equal(button.disabled,true);assert.equal(button.getAttribute('aria-busy'),'true');
  $('bear-data-action').value='rollback';updateDataBearForm();await button.onclick();
  assert.equal(calls.length,count+1);assert.equal(button.disabled,true);
  assert.deepEqual(calls.at(-1),{bear:'data',action:'generate',request:'draft',sql:'SELECT 1'});
  fail?pending.at(-1).reject(Error('failed')):pending.at(-1).resolve({status:'ok'});await run;
  assert.equal(button.disabled,false);assert.equal(button.getAttribute('aria-busy'),null);
  assert.equal(button.textContent,t('Roll back to snapshot'));assert.equal($('bear-data-action').value,'rollback');
  assert.equal($('bear-data-sql').disabled,true);assert.equal($('bear-data-sql').value,'SELECT 1');
  const rollback=button.onclick();assert.deepEqual(calls.at(-1),{bear:'data',action:'rollback',rollback:'marts.r',snapshot_id:'9223372036854775807'});
  pending.at(-1).resolve({status:'ok'});await rollback;
 }
 for(const role of ['bi_viewer','data_engineer']){
  currentRole='admin';$('bear-data-action').value='rollback';applyRole();const run=button.onclick();
  currentRole=role;applyRole();await button.onclick();assert.equal(button.disabled,true);
  assert.equal($('bear-data-action').value,'generate');assert.equal($('bear-data-rollback').disabled,true);
  pending.at(-1).reject(Error('old role request'));await run;
  assert.equal(button.textContent,t('Generate SQL'));assert.equal(button.disabled,false);
  assert.equal($('bear-data-request').disabled,false);assert.equal($('bear-data-snapshot_id').value,'9223372036854775807');
 }
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        source = dictionary + helpers + busy + '\nconst HANDLER=' + json.dumps(handler) + ',ROLE=' + json.dumps(role) + ';\n' + harness
        result = subprocess.run(['node', '-e', source], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'JavaScript verification requires Node.js')
    def test_worksheet_close_behaviors_and_reload(self):
        script = next(n['text'] for n in self.workbench_document().nodes if n['tag'] == 'script')
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
  querySelectorAll(selector){return this.children.flatMap(c=>[...(selector==='[role=tab]'&&c.attrs.role==='tab'||selector==='.worksheet-close'&&c.className==='worksheet-close'?[c]:[]),...c.querySelectorAll(selector)]);}
 }
 const $=id=>nodes[id]||$('worksheet-tabs').querySelectorAll('[role=tab]').find(n=>n.id===id);
 for(const id of ['sql','worksheet-tabs','add-worksheet-tab','query-meta'])nodes[id]=new Element();
 const samples={orders:'SELECT * FROM marts.fct_orders LIMIT 10'};nodes.sql.value=samples.orders;
 const context=vm.createContext({$,samples,document:{createElement:tag=>new Element(tag)},crypto:{randomUUID:()=>String(++sequence)},
  localStorage:{getItem:k=>{if(unavailable)throw Error('blocked');return store[k]??null;},setItem:(k,v)=>{if(unavailable)throw Error('blocked');store[k]=v;}},
  confirm:text=>{confirmations.push(text);return answer;},activate:()=>{},ui:(node,key,values)=>{node.textContent=context.t(key,values);}});
 vm.runInContext(DICTIONARY+WIRE+SOURCE,context);
 return {context,nodes,store,confirmations,answer:value=>answer=value,run:code=>vm.runInContext(code,context),
  tabs:()=>nodes['worksheet-tabs'].querySelectorAll('[role=tab]'),closes:()=>nodes['worksheet-tabs'].querySelectorAll('.worksheet-close'),focus:()=>focused};
}
const a=boot();assert.equal(a.closes()[0].disabled,true);assert.match(a.closes()[0].title,/at least one/);
a.run("closeWorksheet(worksheetId)");assert.equal(a.tabs().length,1);
a.nodes['add-worksheet-tab'].click();a.nodes['add-worksheet-tab'].click();
assert.equal(a.tabs().length,3);assert.equal(a.closes()[0].tag,'button');assert.equal(a.tabs()[0].children.length,0);
// Closing a nonactive sample preserves the current draft and selection, even before an input event.
a.nodes.sql.value='SELECT latest';a.nodes.sql.setSelectionRange(2,7,'backward');a.closes()[0].click();
assert.equal(a.confirmations.length,0);assert.equal(a.nodes.sql.value,'SELECT latest');assert.equal(a.nodes.sql.selectionStart,2);assert.equal(a.nodes.sql.selectionEnd,7);assert.equal(a.nodes.sql.selectionDirection,'backward');
assert.equal(a.run('worksheets.find(w=>w.id===worksheetId).sql'),'SELECT latest');
// Cancellation leaves state, persisted data and focus unchanged.
const before=a.run('JSON.stringify({worksheets,worksheetId})'),stored=a.store['shirokuma.worksheets.v1'],focus=a.focus();
a.answer(false);a.closes()[1].click();assert.equal(a.run('JSON.stringify({worksheets,worksheetId})'),before);assert.equal(a.store['shirokuma.worksheets.v1'],stored);assert.equal(a.focus(),focus);
assert.match(a.confirmations.at(-1),/removes this worksheet’s SQL/);
// Active last tab chooses left; the replacement active ID and removal survive reload.
a.answer(true);a.closes()[1].click();assert.equal(a.tabs().length,1);assert.equal(a.nodes.sql.value,a.context.samples.orders);assert.equal(a.focus(),a.tabs()[0]);
const reloaded=boot(JSON.parse(a.store['shirokuma.worksheets.v1']));assert.equal(reloaded.tabs().length,1);assert.equal(reloaded.run('worksheetId'),a.run('worksheetId'));
// Numbering follows the largest remaining number, never the tab count.
const b=boot({active:'b',tabs:[{id:'a',name:'Worksheet 1',sql:''},{id:'b',name:'Worksheet 3',sql:'persisted SQL'},{id:'c',name:'Worksheet 4',sql:''}]});
b.answer(false);b.closes()[1].click();assert.equal(b.tabs().length,3);assert.equal(b.confirmations.length,1,'persisted SQL needs confirmation');
b.answer(true);b.closes()[1].click();assert.equal(b.run('worksheetId'),'c');assert.equal(b.focus(),b.tabs()[1]);
b.nodes['add-worksheet-tab'].click();assert.equal(b.run('worksheets.at(-1).name'),'Worksheet 5');
// Keyboard tab movement receives only role=tab buttons; close uses native button activation.
let prevented=0;b.tabs()[2].onkeydown({key:'Home',preventDefault(){prevented++;}});assert.equal(b.run('worksheetId'),'a');
b.tabs()[0].onkeydown({key:'End',preventDefault(){prevented++;}});assert.equal(b.run('worksheetId'),'1');
b.tabs()[2].onkeydown({key:'ArrowLeft',preventDefault(){prevented++;}});assert.equal(b.run('worksheetId'),'c');assert.equal(prevented,3);
b.closes()[0].click();assert.equal(b.confirmations.length,2,'empty SQL closes directly');
b.run("language='ja';updateWorksheetCloseLabels()");assert.match(b.closes()[0].title,/閉じる/);
b.nodes.sql.value='編集 SQL';b.answer(false);b.closes()[0].click();assert.match(b.confirmations.at(-1),/SQL が削除/);
const c=boot(null,true);c.nodes['add-worksheet-tab'].click();c.closes()[0].click();assert.equal(c.tabs().length,1);assert.match(c.nodes['query-meta'].textContent,/storage unavailable/);
'''
        result = subprocess.run(['node', '-e', 'const DICTIONARY=' + json.dumps(dictionary) + ',WIRE=' + json.dumps(wire) + ',SOURCE=' + json.dumps(source) + ';\n' + harness], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'JavaScript verification requires Node.js')
    def test_copilot_generate_preserves_origin_and_intervening_edits(self):
        script = next(n['text'] for n in self.workbench_document().nodes if n['tag'] == 'script')
        helpers = script[script.index('const worksheetKey='):script.index('function worksheetName(')]
        handlers = script[script.index("for(const [id,bear,action] of [['copilot-generate'"):script.index('let browserFile=null;')]
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
        result = subprocess.run(['node', '-e', source], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'JavaScript verification requires Node.js')
    def test_data_bear_sends_only_the_current_action_inputs(self):
        script = next(n['text'] for n in self.workbench_document().nodes if n['tag'] == 'script')
        dictionary = script[script.index('const i18n'):script.index('function ui(')]
        helpers = script[script.index('const dataBearActions='):script.index('// One list of specialists')]
        harness = r'''
const assert=require('node:assert/strict');
const nodes={};const $=id=>nodes[id]??={id,value:'',disabled:false,required:false,parentElement:{hidden:false},labels:[{}]};
const ui=(node,key)=>{node.text=t(key);};
const values={request:'revenue by nation',sql:'SELECT 1',stage_table:'stage_t',publish:'pub_t',refresh:'ref_t',rollback:'marts.roll_t',snapshot_id:'9223372036854775807'};
const expected={generate:{request:'revenue by nation',sql:'SELECT 1'},stage:{request:'revenue by nation',sql:'SELECT 1',stage_table:'stage_t'},
 publish:{publish:'pub_t'},refresh:{refresh:'ref_t',sql:'SELECT 1'},rollback:{rollback:'marts.roll_t',snapshot_id:'9223372036854775807'}};
for(const [action,fields] of Object.entries(expected))assert.deepEqual(dataBearPayload(action,values),{bear:'data',action,...fields},action);
assert.equal(typeof dataBearPayload('rollback',values).snapshot_id,'string');
assert.deepEqual(dataBearPayload('refresh',{...values,sql:'  '}),{bear:'data',action:'refresh',refresh:'ref_t'},'empty SQL uses the saved SQL');
for(const [action,bad,message] of [['generate',{},/description|SQL/],['stage',{request:'x'},/table name/],['publish',{stage_table:'x'},/table name/],
 ['rollback',{rollback:'marts.t',snapshot_id:'12a'},/digits only/],['rollback',{rollback:'marts.t',snapshot_id:'1e3'},/digits only/],['other',values,/WAP action/]])
 assert.throws(()=>dataBearPayload(action,bad),message,action);
// Visible inputs follow the action; hidden ones are disabled and keep their drafts.
for(const key of Object.keys(values))$('bear-data-'+key).value=values[key];
const visible=()=>Object.keys(values).filter(key=>!$('bear-data-'+key).parentElement.hidden);
for(const [action,spec] of Object.entries(dataBearActions)){
 $('bear-data-action').value=action;updateDataBearForm();
 assert.deepEqual(visible(),Object.keys(values).filter(key=>spec.fields.includes(key)),action);
 for(const key of Object.keys(values))assert.equal($('bear-data-'+key).disabled,!spec.fields.includes(key),key);
 assert.equal($('bear-data-run').text,spec.button);assert.equal($('bear-data-help').text,spec.help);
 if(spec.target)assert.equal($('bear-data-'+spec.target).required,true);assert.equal($('bear-data-request').required,false);
}
assert.equal($('bear-data-publish').value,'pub_t','drafts survive action changes');
language='ja';$('bear-data-action').value='rollback';updateDataBearForm();assert.equal($('bear-data-run').text,'指定スナップショットへ戻す');
$('bear-data-action').value='refresh';updateDataBearForm();assert.match($('bear-data-sql').labels[0].text,/保存済み/);
'''
        result = subprocess.run(['node', '-e', dictionary + ';\n' + helpers + '\n' + harness], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @patch.object(wb.bears_resident, 'load_ai_config')
    def test_get_ai_config_masks_key_without_mutating_source(self, load):
        config = dict(provider='openai', model='test', api_key='private-key', timeout=30)
        load.return_value = config
        code, data = self.json_request('/api/ai/config')
        self.assertEqual(code, 200)
        self.assertEqual(data, {**config, 'api_key': '********'})
        self.assertEqual(config['api_key'], 'private-key')
        load.return_value = {**config, 'api_key': ''}
        self.assertEqual(self.json_request('/api/ai/config')[1]['api_key'], '')

    @patch.object(wb.bears_resident, 'save_ai_config')
    def test_save_ai_config_and_preserve_masked_credentials(self, save):
        config = dict(provider='custom', model='test', api_url='http://localhost:8001/v1',
                      api_key='private-key', timeout=45)
        save.return_value = config
        code, data = self.json_request('/api/ai/config', config)
        self.assertEqual(code, 200)
        save.assert_called_once_with(config)
        self.assertEqual(data['api_key'], '********')
        self.json_request('/api/ai/config', {**config, 'api_key': '********'})
        save.assert_called_with({key: value for key, value in config.items() if key != 'api_key'})
        self.json_request('/api/ai/config', {'model': 'new-model'})
        save.assert_called_with({'model': 'new-model'})

    @patch.object(wb.bears_resident, 'test_ai_connection')
    def test_ai_connection_status_and_latency(self, connection):
        for report in (dict(status='connected', latency_ms=32),
                       dict(status='failed', latency_ms=5, error='unavailable')):
            connection.return_value = report
            body = dict(provider='local-openai', model='test', timeout=20)
            self.assertEqual(self.json_request('/api/ai/test', body), (200, report))
            connection.assert_called_with(body)

    def test_ai_config_invalid_inputs_and_origin(self):
        with patch.object(wb.bears_resident, 'save_ai_config') as save, \
                patch.object(wb.bears_resident, 'test_ai_connection') as connection:
            for route in ('/api/ai/config', '/api/ai/test'):
                for body in ({'provider': 'unknown'}, {'model': ''}, {'api_url': 'file:///tmp/key'},
                             {'api_key': 123}, {'timeout': True}, {'timeout': 0},
                             {'timeout': 601}, {'timeout': 1.5}):
                    with self.subTest(route=route, body=body):
                        self.assertEqual(self.json_request(route, body)[0], 400)
                code, _, _ = self.request(route, {}, headers={
                    'Content-Type': 'application/json', 'Origin': 'https://other.example'})
                self.assertEqual(code, 403)
            save.assert_not_called()
            connection.assert_not_called()

    def test_ai_settings_form_and_translations(self):
        doc = self.workbench_document()
        dialog = doc.by_id('ai-settings')
        self.assertEqual(dialog['tag'], 'dialog')
        self.assertEqual(dialog['aria-labelledby'], 'ai-settings-title')
        for identifier in ('ai-provider', 'ai-model', 'ai-url', 'ai-key', 'ai-timeout'):
            self.assertIn(dialog, doc.by_id(identifier)['ancestors'])
            self.assertTrue(any(n.get('for') == identifier for n in doc.nodes))
        self.assertEqual(doc.by_id('ai-key')['type'], 'password')
        select = doc.by_id('ai-provider')
        self.assertEqual([n['value'] for n in doc.nodes if n['tag'] == 'option' and select in n['ancestors']],
                         ['local-openai', 'openai', 'bedrock', 'custom'])
        script = next(n['text'] for n in doc.nodes if n['tag'] == 'script')
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
        script = next(n['text'] for n in self.workbench_document().nodes if n['tag'] == 'script')
        handlers = script.split('function aiStatus(', 1)[1].split('function table(', 1)[0]
        harness = r"""
const assert=require('node:assert/strict');
new Function(SCRIPT);
const nodes={};const $=id=>nodes[id]??=( {value:'',disabled:false,
 addEventListener(){},showModal(){this.open=true;},close(){this.open=false;},reportValidity(){return true;}} );
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
 assert.equal($('ai-settings').open,true);assert.equal($('ai-model').value,'test-model');
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
 $('ai-close').onclick();assert.equal($('ai-settings').open,false);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        harness = 'const SCRIPT=' + json.dumps(script) + ';const HANDLERS=' + json.dumps(handlers) + ';' + harness
        completed = subprocess.run(['node', '-e', harness], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)

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
            Cloned {source} to {table}.|Clone failed: {error}|Exported {table} ({row_count} / {total_rows} rows) to {filename}.|
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
            'row_count:result.row_count,total_rows:result.total_rows',
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
            export.assert_called_once_with('marts.orders', file=None, format='auto', max_rows=None)
            self.assertEqual(self.json_request('/api/lake/export', dict(table='raw.orders', max_rows=None))[0], 200)
            export.assert_called_with('raw.orders', file=None, format='auto', max_rows=None)
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
            ('warehouse', 'SQL Worksheets'),
            ('ingestion', 'Ingestion & Stages'),
            ('transformation', 'Transformation'),
            ('jobs', 'Jobs'),
            ('bears', 'AI Bears'),
            ('catalog', 'Database Explorer'),
            ('pawprints', 'Audit Log'),
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
            'SQL Worksheets': 'SQL ワークシート',
            'Ingestion & Stages': '取り込み・ステージ',
            'Transformation': 'データ変換',
            'AI Bears': 'AI Bears（専門AI）',
            'Database Explorer': 'データベース探索',
            'Audit Log': '監査ログ',
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

    def test_models_http_marks_registered_targets_without_exposing_owner(self):
        registry = {
            'managed': dict(owner='alice', version=2, table='iceberg.marts.managed',
                            sql='SELECT id FROM raw.orders', sources=['raw.orders']),
            'legacy': dict(staged_table='iceberg.sandbox.legacy', sql='SELECT 1', sources=[]),
        }
        with patch.object(wb.bears_resident, 'load_wap_registry', return_value=registry):
            code, inventory = self.json_request('/api/models')
        self.assertEqual(code, 200)
        models = {item['name']: item for item in inventory['wap_models']}
        for name, registered in [('managed', True), ('legacy', False)]:
            self.assertIs(models[name]['registered_refresh_target'], registered)
            self.assertEqual(models[name]['layer'], 'wap')
            self.assertEqual(models[name]['sql'], registry[name]['sql'])
            self.assertEqual(models[name]['sources'], registry[name]['sources'])
            self.assertNotIn('owner', models[name])
            self.assertNotIn('version', models[name])
        self.assertEqual(models['managed']['table'], 'iceberg.marts.managed')
        self.assertEqual(models['legacy']['table'], 'iceberg.sandbox.legacy')
        self.assertEqual(inventory['lineage']['managed'], ['raw.orders'])

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
                self.assertIn(b"SQL Worksheets", body)
                self.assertIn(b"AgentHouse Pawprints", body)

    @patch.object(wb.warehouse_resident, "kubectl")
    def test_status(self, kubectl):
        for replicas, ready, state in [(0, 0, 'chilled'), (1, 0, 'starting'), (1, 1, 'active')]:
            for component_ready in (0, 1):
                with self.subTest(state=state, component_ready=component_ready):
                    kubectl.reset_mock()
                    kubectl.return_value = json.dumps({'items': [
                        {'kind': 'Deployment', 'metadata': {'name': 'trino-local'},
                         'spec': {'replicas': replicas}, 'status': {'readyReplicas': ready}},
                        {'kind': 'StatefulSet', 'metadata': {'name': 'polaris'},
                         'spec': {'replicas': 1}, 'status': {'readyReplicas': component_ready}}]})
                    code, result = self.json_request('/api/status')
                    self.assertEqual(code, 200)
                    self.assertEqual(result['status'], 'degraded' if ready < replicas or not component_ready else 'healthy')
                    self.assertEqual(result['warehouse']['state'], state)
                    self.assertEqual(result['components']['polaris']['ready_replicas'], component_ready)
                    kubectl.assert_called_once_with('get', 'deploy,sts', '-o', 'json')

    @patch.object(wb.warehouse_resident, "kubectl", side_effect=OSError("no cluster"))
    def test_status_unavailable(self, *_):
        code, result = self.json_request("/api/status")
        self.assertEqual(code, 200)
        self.assertEqual(result["status"], "degraded")
        self.assertIn("error", result["warehouse"])
        self.assertEqual(result['components'], {'error': 'no cluster'})

    @patch.object(wb.warehouse_resident, 'kubectl')
    def test_status_missing_trino_and_component_failure(self, kubectl):
        kubectl.return_value = json.dumps({'items': [{'kind': 'StatefulSet', 'metadata': {'name': 'polaris'}}]})
        _, result = self.json_request('/api/status')
        self.assertEqual(result['status'], 'degraded')
        self.assertIn('not found', result['warehouse']['error'])
        self.assertEqual(result['components']['polaris'], {'replicas': 1, 'ready_replicas': 0})
        kubectl.return_value = json.dumps({'items': [{'kind': 'Deployment', 'metadata': {'name': 'trino-local'}}]})
        with patch.object(wb, 'components', side_effect=ValueError('bad component')):
            _, result = self.json_request('/api/status')
        self.assertEqual(result['status'], 'degraded')
        self.assertEqual(result['warehouse']['state'], 'starting')
        self.assertEqual(result['components'], {'error': 'bad component'})

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

    def test_catalog_sections_keep_role_and_table_boundary(self):
        sections = ('columns', 'preview', 'profile', 'ddl', 'snapshots', 'lineage')
        for role in ('admin', 'data_engineer', 'bi_viewer'):
            with patch.object(wb.request_auth, 'authenticate', return_value=('alice', role)):
                for section in sections:
                    with self.subTest(role=role, section=section):
                        def describe(args):
                            self.assertEqual(args.role, role)
                            self.assertEqual(wb.request_auth.current_role(), role)
                            self.assertEqual(args.table, 'marts.orders')
                            self.assertEqual(args.detail, section)
                            self.assertFalse(args.include_config)
                            return {'metadata': {}}
                        with patch.object(wb.bears_resident, 'catalog', side_effect=describe) as catalog:
                            self.assertEqual(self.json_request(
                                '/api/catalog?table=marts.orders&detail=' + section)[0], 200)
                            catalog.assert_called_once()
                        if role == 'bi_viewer':
                            with patch.object(wb.bears_resident, 'catalog') as catalog:
                                for schema in ('raw', 'sandbox', 'staging', 'tpch_tiny'):
                                    self.assertEqual(self.json_request(
                                        '/api/catalog?table=' + schema + '.orders&detail=' + section)[0], 403)
                                catalog.assert_not_called()

    def test_catalog_section_session_revalidation_observes_revocation(self):
        with patch.object(wb.request_auth, 'authenticate', return_value=('alice', 'admin')) as auth, \
             patch.object(wb.bears_resident, 'catalog', return_value={'metadata': {'columns': []}}) as catalog:
            self.assertEqual(self.json_request('/api/catalog?table=raw.orders&detail=columns')[0], 200)
            self.assertEqual(self.json_request('/api/session')[1]['maximum_role'], 'admin')
            auth.return_value = ('alice', 'bi_viewer')
            self.assertEqual(self.json_request('/api/session')[1]['maximum_role'], 'bi_viewer')
            self.assertEqual(self.json_request('/api/catalog?table=raw.orders&detail=columns')[0], 403)
            self.assertEqual(self.request('/api/session', headers={'X-Shirokuma-Role': 'admin'})[0], 403)
            auth.side_effect = wb.request_auth.AuthenticationError('credentials revoked')
            self.assertEqual(self.json_request('/api/session')[0], 401)
            self.assertEqual(self.json_request('/api/catalog?table=marts.orders&detail=columns')[0], 401)
            catalog.assert_called_once()

    def test_catalog_section_validation_and_snapshot_digits(self):
        with patch.object(wb.bears_resident, 'catalog') as catalog:
            for query in ('detail=unknown&table=marts.orders', 'detail=columns',
                          'detail=&table=marts.orders', 'detail=snapshots'):
                self.assertEqual(self.json_request('/api/catalog?' + query)[0], 400)
            catalog.assert_not_called()
            catalog.return_value = {'metadata': {'snapshots': {'rows': [
                [9007199254740993, 9007199254740995, '2026-10-19']]}}}
            code, result = self.json_request('/api/catalog?table=marts.orders&detail=snapshots')
            self.assertEqual(code, 200)
            self.assertEqual(result['metadata']['snapshots']['rows'][0][:2],
                             ['9007199254740993', '9007199254740995'])

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
                start_schedule.assert_called_once_with()
                raise KeyboardInterrupt()
            server.return_value.__enter__.return_value.serve_forever.side_effect = serve
            with self.subTest(unavailable=unavailable), patch.object(wb.warehouse_resident, 'persistent_trino', tunnel), \
                 patch.object(wb.jobs_resident, 'start_scheduler') as start_schedule, \
                 patch.object(wb.jobs_resident, 'stop_scheduler') as stop_schedule, \
                 patch('builtins.print'):
                wb.main(['--port', '0'])
                stop_schedule.assert_called_once_with()
            self.assertEqual(events, [] if unavailable else ['open', 'close'])


if __name__ == "__main__":
    unittest.main()
