"""Physical clone boundary tests with real durable ledgers, no engine runtime."""
from dataclasses import replace
from contextlib import ExitStack
from http.client import HTTPResponse
import io
import json
import sys
from pathlib import Path
import unittest
from types import SimpleNamespace
import urllib.parse
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import control_store
import dataops_service as ds
import data_management as dm
import manual_clone
import query_service as qs
from query_context import QueryError
import test_dataops_service as boundary


class CloneTests(unittest.TestCase):
    def setUp(self):
        self.b = boundary.DataOpsTests()
        self.b.setUp()
        self.addCleanup(self.b.doCleanups)
        self.principal = self.b.principal
        self.context = self.b.context(schema='sandbox')
        self.source = self.b.existing(schema='marts')
        self.destination = self.b.locator('copied', schema='sandbox')
        self.metadata = self.b.objects[('sales', 'marts', 'observed_data', 'tables')]
        self.metadata.update({'current-snapshot-id': 102, 'current-schema-id': 2,
            'snapshots': [{'snapshot-id': 101, 'schema-id': 1}, {'snapshot-id': 102, 'schema-id': 2}],
            'schemas': [{'schema-id': 1, 'fields': [dict(id=1, name='old_id', type='long')]},
                        {'schema-id': 2, 'fields': [dict(id=1, name='new_id', type='long')]}]})
        self.posts = []
        self.after_create = lambda: None
        self.zero = False
        self.loss = False
        self.b.start_patch('query_service._transport', side_effect=self.transport)

    def transport(self, url, method, pin, body=None):
        if method == 'DELETE':
            return {}
        if method == 'GET':
            raise OSError('lost page')
        sql = body.decode()
        self.posts.append(sql)
        with control_store.connect() as db:
            row = db.execute('SELECT result,committed FROM dataops_operations').fetchone()
        self.assertFalse(row['committed'])
        self.assertEqual(json.loads(row['result'])['source']['snapshot'], '101' if 'AS OF 101' in sql else '102')
        page = self.b.transport(url, method, pin, body)
        target = self.b.objects[('sales', 'sandbox', 'copied', 'tables')]
        target.update({'current-schema-id': 1, 'schemas': [{'schema-id': 1,
            'fields': [dict(id=1, name='old_id' if 'AS OF 101' in sql else 'new_id', type='long')]}],
            'current-snapshot-id': None if self.zero else 201,
            'snapshots': [] if self.zero else [{'snapshot-id': 201, 'schema-id': 1}]})
        self.after_create()
        if self.loss:
            page['nextUri'] = 'http://127.0.0.1:1234/v1/statement/lost/1'
        return page

    def run_clone(self, key='clone', **kwargs):
        return manual_clone.clone_table(self.principal, kwargs.pop('context', self.context), key=key,
            source=kwargs.pop('source', self.source), destination=kwargs.pop('destination', self.destination), **kwargs)

    def test_current_and_past_frozen_historical_schema(self):
        operation = self.run_clone(snapshot_id='101')
        self.assertTrue(operation['committed'])
        self.assertEqual(operation['result']['phase'], 'VERIFIED')
        self.assertEqual(operation['result']['source']['columns'][0]['name'], 'old_id')
        self.assertEqual(operation['result']['source']['current_schema_id'], 2)
        self.assertIn('FOR VERSION AS OF 101', self.posts[0])
        self.assertTrue(self.posts[0].startswith('CREATE TABLE "sales"."sandbox"."copied"'))
        self.assertNotIn('REPLACE', self.posts[0])
        self.metadata.clear()
        replay = self.run_clone(snapshot_id='101')
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['id'], operation['id'])
        self.assertEqual(len(self.posts), 1)
        with self.assertRaises(QueryError):
            self.run_clone(snapshot_id='102')
        execution = ds.Execution(self.principal, operation['id'])
        with self.assertRaises(QueryError) as denied:
            execution.cleanup(dm.ResourceRef(**operation['result']['target_ref']), step='forbidden')
        self.assertIn(denied.exception.code, ('ADMISSION_CONFLICT', 'OBJECT_NOT_OWNED'))

    def test_current_append_after_acceptance_preserves_input(self):
        def append():
            self.metadata['current-snapshot-id'] = 103
            self.metadata['snapshots'].append({'snapshot-id': 103, 'schema-id': 2})
        self.after_create = append
        operation = self.run_clone()
        self.assertEqual(operation['result']['source']['snapshot'], '102')
        self.assertEqual(operation['result']['phase'], 'VERIFIED')

    def test_schema_change_after_commit_is_warning_and_never_drop(self):
        self.after_create = lambda: self.metadata['schemas'][1]['fields'][0].update(name='changed')
        operation = self.run_clone()
        self.assertTrue(operation['committed'])
        self.assertEqual(operation['result']['warning'], 'EXPORT_SOURCE_CHANGED')
        self.assertEqual(operation['result']['phase'], 'COPIED_UNVERIFIED')
        self.assertEqual(len(self.posts), 1)

    def test_zero_target_preserves_ref_and_committed_warning(self):
        self.zero = True
        operation = self.run_clone()
        self.assertTrue(operation['committed'])
        self.assertIsNone(operation['result']['target'])
        self.assertTrue(operation['result']['target_ref']['object_id'])
        self.assertEqual(operation['result']['warning'], 'EXPORT_SNAPSHOT_UNAVAILABLE')

    def test_target_schema_mismatch_warns_after_commit(self):
        self.after_create = lambda: self.b.objects[('sales', 'sandbox', 'copied', 'tables')]['schemas'][0]['fields'][0].update(name='wrong')
        operation = self.run_clone()
        self.assertTrue(operation['committed'])
        self.assertEqual(operation['result']['warning'], 'CLONE_TARGET_SCHEMA_MISMATCH')
        self.assertEqual(operation['result']['phase'], 'COPIED_UNVERIFIED')

    def test_admin_requires_literal_sandbox_purpose_before_catalog(self):
        owner = self.b.fixture.principal('owner')
        context = self.b.context(schema='sandbox', principal=owner, role='admin')
        for purpose in ('marts', 'SANDBOX'):
            with self.subTest(purpose=purpose):
                with control_store.connect() as db:
                    db.execute('UPDATE schemas SET purpose=? WHERE id=?', (purpose, context.schema_id))
                with patch('database_inventory.catalog_api', side_effect=AssertionError('catalog read')):
                    with self.assertRaises(QueryError) as denied:
                        manual_clone.clone_table(owner, context, key=purpose, source=self.source,
                            destination=self.destination)
                self.assertEqual(denied.exception.code, 'CLONE_SANDBOX_CONTEXT_REQUIRED')
        self.assertFalse(self.posts)

    def test_nested_evolved_types_ignore_reassigned_ids_and_constraints(self):
        source_type = {'type': 'struct', 'fields': [
            {'id': 97, 'name': 'items', 'required': True, 'doc': 'source', 'type':
                {'type': 'list', 'element-id': 103, 'element-required': True, 'element':
                    {'type': 'map', 'key-id': 120, 'key': 'string', 'value-id': 130,
                     'value-required': True, 'value': {'type': 'struct', 'fields': [
                        {'id': 140, 'name': 'amount', 'required': True, 'type': 'decimal(12,2)'}]}}}}]}
        self.metadata['schemas'][1]['fields'] = [dict(id=91, name='evolved', type=source_type, required=True)]
        def target_fields():
            fields = json.loads(json.dumps(self.metadata['schemas'][1]['fields']))
            def reassign(value):
                if isinstance(value, dict):
                    for key in list(value):
                        if key == 'id' or key.endswith('-id'):
                            value[key] += 1000
                        elif key == 'required' or key.endswith('-required'):
                            value[key] = False
                        elif key == 'doc':
                            value[key] = 'target'
                        else:
                            reassign(value[key])
                elif isinstance(value, list):
                    for item in value:
                        reassign(item)
            reassign(fields)
            self.b.objects[('sales', 'sandbox', 'copied', 'tables')]['schemas'][0]['fields'] = fields
        self.after_create = target_fields
        operation = self.run_clone()
        self.assertEqual(operation['state'], 'FINISHED')
        self.assertTrue(operation['committed'])
        self.assertEqual(operation['result']['phase'], 'VERIFIED')
        self.assertNotEqual(operation['result']['source']['table_uuid'], operation['result']['target']['table_uuid'])

    def test_revocation_after_create_denies_public_receipt_but_preserves_commit(self):
        def revoke():
            with control_store.connect() as db:
                dm.update_grant(db, 'alice', self.context.database_id, 1, 'revoked')
        self.after_create = revoke
        with self.assertRaises((QueryError, PermissionError)):
            self.run_clone()
        with control_store.connect() as db:
            row = db.execute('SELECT * FROM dataops_operations').fetchone()
        self.assertTrue(row['committed'])
        self.assertEqual(json.loads(row['result'])['phase'], 'COPIED_UNVERIFIED')
        self.assertIn('warning', json.loads(row['result']))
        self.assertIn(('sales', 'sandbox', 'copied', 'tables'), self.b.objects)
        self.assertEqual(len(self.posts), 1)

    def test_missing_snapshot_never_falls_back(self):
        for snapshot in ('999', '٠١', 101, '', '-1'):
            with self.subTest(snapshot=snapshot), self.assertRaises((QueryError, ValueError)):
                self.run_clone(key=str(snapshot), snapshot_id=snapshot)
        self.assertFalse(self.posts)

    def test_role_wrong_context_cross_database_and_view_before_sql(self):
        cases = [dict(context=self.b.context(schema='marts')),
                 dict(source=replace(self.source, database_id=self.b.databases[1])),
                 dict(destination=self.b.locator('bad', schema='raw')),
                 dict(source=self.b.existing('view_source', schema='marts', kind='VIEW')),
                 dict(source=replace(self.source, incarnation=99))]
        for index, case in enumerate(cases):
            with self.subTest(case=case), self.assertRaises(QueryError):
                self.run_clone(key=str(index), **case)
        self.assertFalse(self.posts)

    def test_no_integer_source_head_unsupported(self):
        self.metadata['current-snapshot-id'] = None
        with self.assertRaises(QueryError) as denied:
            self.run_clone()
        self.assertEqual(denied.exception.code, 'CLONE_SNAPSHOT_UNAVAILABLE')
        self.assertFalse(self.posts)

    def test_collision_table_or_view_never_overwrites(self):
        for index, kind in enumerate(('tables', 'views')):
            self.b.objects[('sales', 'sandbox', 'copied', kind)] = {}
            with self.assertRaises(QueryError) as denied:
                self.run_clone(key=str(index))
            self.assertEqual(denied.exception.code, 'DESTINATION_EXISTS')
            self.b.objects.pop(('sales', 'sandbox', 'copied', kind))
        self.assertFalse(self.posts)

    def test_unknown_replay_restart_keeps_original_and_one_ctas(self):
        self.loss = True
        operation = self.run_clone()
        self.assertEqual(operation['state'], 'UNKNOWN')
        self.assertFalse(operation['committed'])
        ds._live.pop(operation['id'])[2].close()
        replay = self.run_clone()
        self.assertEqual(replay['state'], 'UNKNOWN')
        self.assertEqual(replay['id'], operation['id'])
        self.assertEqual(len(self.posts), 1)

    def test_preflight_write_denial_precedes_catalog(self):
        context = replace(self.context, selected_role='bi_viewer')
        with patch('database_inventory.catalog_api', side_effect=AssertionError('catalog read')):
            with self.assertRaises((QueryError, PermissionError)):
                self.run_clone(context=context)
        self.assertFalse(self.posts)

    def test_pre_ctas_uuid_schema_snapshot_change_denied(self):
        original = ds.Execution.export_measure
        for index, change in enumerate((lambda: self.metadata.update({'table-uuid': '00000000-0000-0000-0000-000000000000'}),
                lambda: self.metadata['schemas'][1]['fields'][0].update(name='changed'),
                lambda: self.metadata.update(snapshots=[]))):
            saved = json.loads(json.dumps(self.metadata))
            def measure(execution, ref, captured=None, **kwargs):
                result = original(execution, ref, captured, **kwargs)
                if captured is None:
                    change()
                return result
            with patch.object(ds.Execution, 'export_measure', measure), self.assertRaises(QueryError):
                self.run_clone(key=str(index))
            self.metadata.clear()
            self.metadata.update(saved)
        self.assertFalse(self.posts)

    def test_existing_active_claim_denies_second_create(self):
        execution = ds.begin(self.principal, self.context, route='manual-table-clone',
            idempotency_key='other', payload={})
        execution._target_claim(self.destination)
        with self.assertRaises(QueryError) as caught:
            self.run_clone()
        self.assertEqual(caught.exception.code, 'TARGET_BUSY')
        self.assertFalse(self.posts)
        execution.cancel()

    def test_cancel_before_create_preserves_no_target_and_after_commit_keeps_target(self):
        original = ds.Execution._absent
        def cancel_before(execution, target):
            original(execution, target)
            execution.cancel()
        with patch.object(ds.Execution, '_absent', cancel_before), self.assertRaises(QueryError):
            self.run_clone(key='before')
        self.assertFalse(self.posts)
        operation = self.run_clone(key='after')
        ds.Execution(self.principal, operation['id']).cancel()
        self.assertIn(('sales', 'sandbox', 'copied', 'tables'), self.b.objects)
        self.assertFalse(any(sql.startswith('DROP') for sql in self.posts))

    def test_revocation_before_create_and_other_owner_observation_denied(self):
        original = ds.Execution._absent
        def revoke(execution, target):
            original(execution, target)
            with control_store.connect() as db:
                dm.update_grant(db, 'alice', self.context.database_id, 1, 'revoked')
        with patch.object(ds.Execution, '_absent', revoke), self.assertRaises((QueryError, PermissionError)):
            self.run_clone()
        self.assertFalse(self.posts)
        with control_store.connect() as db:
            operation_id = db.execute('SELECT id FROM dataops_operations').fetchone()[0]
        with self.assertRaises(QueryError) as denied:
            ds.Execution(self.b.fixture.principal('owner'), operation_id).inspect()
        self.assertEqual(denied.exception.status, 404)

    def test_actual_native_handler_post_observe_cancel_replay_and_strict_input(self):
        import warehouse_resident as wr
        import runtime_profile
        import request_auth
        handlers = []
        class Capture:
            def __init__(self, address, handler): handlers.append(handler)
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def handle_request(self): pass
        with ExitStack() as stack:
            stack.enter_context(patch.object(wr, '_profile_active', return_value=True))
            stack.enter_context(patch('logical_creations.enabled', return_value=False))
            stack.enter_context(patch('http.server.ThreadingHTTPServer', Capture))
            stack.enter_context(patch('database_creation_bridge.configure'))
            stack.enter_context(patch.object(wr, 'warehouse', return_value='LOCAL'))
            wr.serve_endpoint(once=True)
        handler = handlers[0]
        class Connection:
            def __init__(self, incoming): self.incoming, self.outgoing = io.BytesIO(incoming), io.BytesIO()
            def makefile(self, *args): return self.incoming
            def sendall(self, data): self.outgoing.write(data)
        def request(method, path, body=None, key=None):
            payload = json.dumps(body).encode() if body is not None else b''
            raw = (method+' '+path+' HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n'
                'X-Shirokuma-Role: data_engineer\r\nContent-Type: application/json\r\nContent-Length: '+str(len(payload))+'\r\n'
                +('Idempotency-Key: '+key+'\r\n' if key else '')+'\r\n').encode()+payload
            connection = Connection(raw)
            with patch.object(runtime_profile, 'active', return_value=True), \
                    patch.object(request_auth, 'authenticate_principal', return_value=self.principal), \
                    patch.object(handler, 'log_message'):
                handler(connection, ('127.0.0.1', 1), SimpleNamespace())
            response = HTTPResponse(Connection(connection.outgoing.getvalue()))
            response.begin()
            return response.status, json.loads(response.read())
        from dataclasses import asdict
        body = dict(context=asdict(self.context), source=asdict(self.source),
            destination=asdict(self.destination), snapshot_id='101')
        status, reply = request('POST', '/api/v1/table-clones', body, 'wire')
        self.assertEqual(status, 200, reply)
        operation = reply['operation']
        for method, path, payload, key in (
                ('GET', '/api/v1/dataops/'+operation['id']+'?'+urllib.parse.urlencode({'context': json.dumps(body['context'])}), None, None),
                ('POST', '/api/v1/dataops/'+operation['id']+'/cancel', {'context': body['context']}, None),
                ('POST', '/api/v1/table-clones', body, 'wire')):
            status, reply = request(method, path, payload, key)
            self.assertEqual(status, 200, reply)
            self.assertEqual(reply['operation']['id'], operation['id'])
        for payload, key in ((dict(body, sql='SELECT 1'), 'bad'), (body, None), (dict(body, snapshot_id=101), 'bad')):
            status, reply = request('POST', '/api/v1/table-clones', payload, key)
            self.assertEqual(status, 400, reply)
        self.assertEqual(len(self.posts), 1)
        import workbench_server
        handler = workbench_server.WorkbenchHandler
        status, reply = request('POST', '/api/v1/table-clones', body, 'wire')
        self.assertEqual(status, 200, reply)
        self.assertEqual(reply['operation']['id'], operation['id'])


if __name__ == '__main__':
    unittest.main()
