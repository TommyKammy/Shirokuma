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
import runtime_profile
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


class RouteParameterTests(unittest.TestCase):
    """Reject unknown query parameters before any retained backend dispatch."""

    def get(self, path, headers=None):
        handler = object.__new__(wb.WorkbenchHandler)
        handler.path = path
        handler.headers = Message()
        for key, value in (headers or {}).items():
            handler.headers[key] = value
        replies = []
        handler.respond = lambda value, status=200: replies.append((status, value))
        handler.do_GET()
        self.assertEqual(len(replies), 1)
        return replies[0]

    def test_unknown_route_parameters_precede_backend(self):
        with patch.object(wb.request_auth, 'authenticate', return_value=('alice', 'admin')), \
             patch.object(wb.bears_resident, 'models_inventory') as models:
            for path in ('/api/models?mode=runtime', '/api/catalog?mode=runtime',
                         '/api/session?mode=runtime'):
                with self.subTest(path=path):
                    self.assertEqual(self.get(path), (403, {'error': 'Unknown query parameter'}))
            models.assert_not_called()


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

    @unittest.skipUnless(shutil.which('node'), 'JavaScript verification requires Node.js')
    @unittest.skipUnless(shutil.which('node'), 'JavaScript verification requires Node.js')
    @unittest.skipUnless(shutil.which('node'), 'JavaScript verification requires Node.js')
    @unittest.skipUnless(shutil.which('node'), 'JavaScript verification requires Node.js')
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
                  '/api/lake/export', '/api/warehouse/chill']
        with patch.object(wb.bears_resident, 'save_ai_config') as save, \
             patch.object(wb.bears_resident, 'drop_table') as drop, \
             patch.object(wb.lake_ingest, 'ingest') as ingest, \
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

    def test_status_reports_auto_chill_configuration(self):
        auto = dict(auto_chill_minutes=5, idle_seconds=0)
        with patch.object(wb.warehouse_resident, 'auto_chill_status', return_value=auto), \
             patch.object(wb.warehouse_resident, 'status', return_value={}), patch.object(wb, 'components', return_value={}):
            self.assertEqual(self.json_request('/api/status')[1]['auto_chill'], auto)

    def workbench_document(self):
        status, _, body = self.request('/')
        self.assertEqual(status, 200)
        document = WorkbenchHTML(body.decode())
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

    def test_ingest_browser_uploads(self):
        with patch.object(wb.lake_ingest, 'ingest', return_value={'action': 'ingest'}) as call:
            for binary in (False, True):
                body = {'filename': 'orders.csv'}
                body.update({'content_base64': base64.b64encode(b'id\n1').decode()} if binary else {'content': 'id\n1'})
                self.assertEqual(self.json_request('/api/lake/ingest', body), (200, {'action': 'ingest'}))
                expected = dict(filename='orders.csv', content=b'id\n1' if binary else 'id\n1',
                                table=None, mode='replace', format='auto', batch_size=500)
                call.assert_called_with(None, **expected)

    def test_invalid_browser_uploads(self):
        cases = [{}, {'filename': 'x.csv'}, {'content': 'id\n1'}, {'filename': 1, 'content': ''},
                 {'filename': ' ', 'content': ''}, {'filename': 'x', 'content': None},
                 {'filename': 'x', 'content_base64': '!!'}, {'filename': 'x', 'content_base64': 'é'},
                 {'filename': 'x', 'content': '', 'content_base64': ''},
                 {'file': 'x.csv', 'filename': 'x.csv', 'content': ''},
                 {'file': 'x.csv', 'format': 'unknown'}]
        with patch.object(wb.lake_ingest, 'ingest') as call:
            for body in cases:
                with self.subTest(body=body):
                    self.assertEqual(self.json_request('/api/lake/ingest', body)[0], 400)
            call.assert_not_called()

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
                self.assertIn(b"Audit log is available in Database mode.", body)

    def test_chart_static_asset(self):
        for name, mime in (('result_chart.js', 'application/javascript; charset=utf-8'),
                           ('base_i18n.js', 'application/javascript; charset=utf-8'),
                           ('saved_lineage.js', 'application/javascript; charset=utf-8'),
                           ('result_export.js', 'application/javascript; charset=utf-8'),
                           ('db_admin.js', 'application/javascript; charset=utf-8'),
                           ('db_jobs.js', 'application/javascript; charset=utf-8'),
                           ('db_dbt.js', 'application/javascript; charset=utf-8'),
                           ('db_creation.js', 'application/javascript; charset=utf-8'),
                           ('workbench.css', 'text/css; charset=utf-8')):
            path, asset = '/assets/' + name, wb.INDEX.with_name(name)
            with self.subTest(path=path):
                status, headers, body = self.request(path)
                self.assertEqual(status, 200)
                self.assertEqual(headers['Content-Type'], mime)
                self.assertEqual(headers['Cache-Control'], 'no-store')
                self.assertEqual(int(headers['Content-Length']), len(body))
                self.assertEqual(body, asset.read_bytes())
            for suffix in ('?v=1', '?x=1&x=2'):
                with self.subTest(path=path, suffix=suffix):
                    self.assertEqual(self.request(path + suffix)[0], 403)
        for path in ('/assets/unknown.js', '/assets/../index.html',
                     '/assets/%2e%2e/index.html', '/assets/result_chart.js/extra'):
            self.assertEqual(self.request(path)[0], 404)

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
    def test_catalog_search_and_columns(self, catalog):
        catalog.return_value = {"metadata": {"marts": [["fct_orders"]]}}
        for path, action, table in [("/api/catalog", "search", None),
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

    @patch.object(wb.bears_resident, 'models_inventory', side_effect=RuntimeError('unavailable'))
    def test_get_backend_errors_map_to_502(self, _):
        self.assertEqual(self.json_request('/api/models'), (502, {'error': 'unavailable'}))

    def test_removed_legacy_routes_return_not_found(self):
        for path in ('/api/history', '/api/pawprints', '/api/lineage'):
            with self.subTest(path=path):
                self.assertEqual(self.json_request(path), (404, {'error': 'route not found'}))
        for path in ('/api/warehouse/autochill', '/api/lake/preview'):
            with self.subTest(path=path):
                self.assertEqual(self.json_request(path, {}), (404, {'error': 'route not found'}))

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
                initialize.assert_called_once_with()
                raise KeyboardInterrupt()
            server.return_value.__enter__.return_value.serve_forever.side_effect = serve
            with self.subTest(unavailable=unavailable), patch.object(wb.warehouse_resident, 'persistent_trino', tunnel), \
                 patch.object(wb.jobs_resident, 'start_scheduler') as start_schedule, \
                 patch.object(wb.jobs_resident, 'stop_scheduler') as stop_schedule, \
                 patch.object(wb.warehouse_resident, 'initialize_auto_chill') as initialize, \
                 patch.object(wb.warehouse_resident, 'stop_auto_chill_daemon') as stop_chill, \
                 patch.object(runtime_profile, 'configure_process', return_value=False), \
                 patch.object(runtime_profile, 'active', return_value=False), \
                 patch('builtins.print'):
                wb.main(['--port', '0'])
                stop_schedule.assert_called_once_with()
                stop_chill.assert_called_once_with()
            self.assertEqual(events, [] if unavailable else ['open', 'close'])


if __name__ == "__main__":
    unittest.main()
