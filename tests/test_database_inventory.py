"""Offline registry and Iceberg REST exploration contracts."""
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit, unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import control_store
import data_management as registry
import database_inventory as inventory
import request_auth
import runtime_profile
import query_context
import query_service

CATALOG_API = inventory.catalog_api


class DatabaseInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        environment = patch.dict(os.environ, {'SHIROKUMA_STATE_DIR': str(self.root)}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.principal = request_auth.AuthenticatedPrincipal('alice', 'admin', True)
        with control_store.connect() as db:
            self.database = registry.create_database(db, 'arbitrary_catalog', 'Display', '', 'test')
            db.execute("UPDATE databases SET state='READY' WHERE id=?", (self.database.id,))
            self.database = registry.get_database(db, self.database.id)
            self.binding = registry.create_binding(db, self.database.id, 'polaris', 'SALES', 'remote_catalog', 'storage', 'credential', 's3://synthetic/')
            registry.record_generation(db, registry.WarehouseConfigGeneration('generation1', 'LOCAL', 'apply1', (), (), (), 'READY'))
            registry.record_generation(db, registry.WarehouseConfigGeneration('changed', 'LOCAL', 'apply2', (), (), (), 'READY'))
            self.binding = registry.update_binding(db, self.database.id, 1, 1, 'generation1')
            self.schemas = {name: registry.create_schema(db, self.database.id, name, 'marts', name, 'test')
                for name in ('raw', 'sandbox', 'staging', 'marts', 'other')}
        self.schema = self.schemas['raw']
        self.allowed_database_ids = {self.database.id}
        auth = patch.object(request_auth, 'authorize_database', side_effect=self.authorize)
        self.authorization = auth.start()
        self.addCleanup(auth.stop)
        self.objects = {'TABLE': {'a': self.metadata('uuid-a'), 'b': self.metadata('uuid-b'), 'c': self.metadata('uuid-c')},
                        'VIEW': {'v': {'schemas': []}}}
        self.calls = []
        api = patch.object(inventory, 'catalog_api', self.api)
        api.start()
        self.addCleanup(api.stop)

    def authorize(self, principal, database_id, role, **kwargs):
        if database_id not in self.allowed_database_ids:
            raise PermissionError()
        return SimpleNamespace(grant_revision=1)

    @staticmethod
    def metadata(identity):
        return {'table-uuid': identity, 'current-schema-id': 2, 'schemas': [{'schema-id': 2, 'fields': [
            dict(name='id', type='long', required=True), dict(name='note', type='string', required=False)]}],
            'snapshots': [{'snapshot-id': 9223372036854775806, 'parent-snapshot-id': 9223372036854775805,
                'timestamp-ms': 1792300000000, 'summary': {'operation': 'append'}}]}

    @contextmanager
    def api(self, database, binding):
        yield self.rest

    def rest(self, path):
        self.calls.append(path)
        parts = urlsplit(path).path.split('/')
        kind = 'TABLE' if parts[3] == 'tables' else 'VIEW'
        if len(parts) == 4:
            return {'identifiers': [dict(namespace=[unquote(parts[2])], name=n) for n in self.objects[kind]]}
        name = unquote(parts[4])
        if name not in self.objects[kind]:
            inventory.missing()
        return {'metadata': copy.deepcopy(self.objects[kind][name])}

    def list(self, **kwargs):
        return inventory.list_objects(self.principal, 'data_engineer', self.schema.id, **kwargs)

    def test_authorization_precedes_network_and_hides_databases(self):
        with patch.object(request_auth, 'authorize_database', side_effect=PermissionError('hidden')):
            self.assertEqual(inventory.list_databases(self.principal, 'bi_viewer'), {'items': []})
            with self.assertRaises(inventory.InventoryError) as exc:
                self.list()
            self.assertEqual(exc.exception.status, 404)
        self.assertEqual(self.calls, [])

    def test_registered_schema_policy_uses_sql_name(self):
        report = inventory.list_schemas(self.principal, 'bi_viewer', self.database.id)
        self.assertEqual([s['sql_name'] for s in report['items']], ['marts'])
        report = inventory.list_schemas(self.principal, 'data_engineer', self.database.id)
        self.assertEqual({s['sql_name']: s['capabilities']['write'] for s in report['items']},
            dict(raw=True, sandbox=True, staging=False, marts=False))

    def test_job_capabilities_follow_exact_selected_role(self):
        for role, sql, admin in [('bi_viewer', False, False), ('data_engineer', True, False), ('admin', True, True)]:
            database = inventory.list_databases(self.principal, role)['items'][0]
            self.assertEqual((database['capabilities']['job_sql'], database['capabilities']['job_admin']), (sql, admin))
            self.assertEqual(database['capabilities']['stage_admin'], admin)
            schemas = inventory.list_schemas(self.principal, role, self.database.id)['items']
            self.assertTrue(schemas)
            for schema in schemas:
                self.assertEqual((schema['capabilities']['job_sql'], schema['capabilities']['job_admin']), (sql, admin))
                self.assertEqual(schema['capabilities']['stage_admin'], admin)
        self.assertEqual(len(inventory.list_schemas(self.principal, 'admin', self.database.id)['items']), 5)

    def test_schema_allowed_modes_are_canonical_and_role_specific(self):
        for role in ('bi_viewer', 'data_engineer', 'admin'):
            for item in inventory.list_schemas(self.principal, role, self.database.id)['items']:
                expected = ['read-only']
                if role in ('data_engineer', 'admin') and item['sql_name'] in ('raw', 'sandbox'):
                    expected.append('sandbox-write')
                if role == 'admin':
                    expected.append('admin-write')
                with self.subTest(role=role, schema=item['sql_name']):
                    self.assertEqual(item['allowed_modes'], expected)

    def test_mismatched_schema_purpose_is_omitted_and_denied_before_rest(self):
        for role, name in (('data_engineer', 'raw'), ('bi_viewer', 'marts')):
            schema = self.schemas[name]
            with control_store.connect() as db:
                obj = registry.observe_object(db, schema.id, 'a', 'TABLE', 'uuid-a', 'a')
                db.execute("UPDATE schemas SET purpose='different' WHERE id=?", (schema.id,))
            self.calls.clear()
            report = inventory.list_schemas(self.principal, role, self.database.id)
            self.assertNotIn(schema.id, [s['id'] for s in report['items']])
            for action in (
                    lambda: inventory.list_objects(self.principal, role, schema.id),
                    lambda: inventory.resolve_object(self.principal, role, self.database.id, schema.id, 'a'),
                    lambda: inventory.object_detail(self.principal, role, obj.id, detail='columns')):
                with self.subTest(role=role), self.assertRaises(inventory.InventoryError) as error:
                    action()
                self.assertEqual(error.exception.status, 404)
            self.assertEqual(self.calls, [])
            admin = inventory.list_schemas(self.principal, 'admin', self.database.id)
            self.assertIn(schema.id, [s['id'] for s in admin['items']])
            self.assertTrue(inventory.list_objects(self.principal, 'admin', schema.id)['items'])

    def test_native_one_two_three_part_and_quoted_names(self):
        for name in ('order.items', 'say"hello', 'space name'):
            self.objects['TABLE'][name] = self.metadata('uuid-' + name)
        examples = [('a', 'raw', 'a'), ('raw.a', 'raw', 'a'), ('arbitrary_catalog.raw.a', 'raw', 'a'),
            ('ARBITRARY_CATALOG.RAW.A', 'raw', 'a'), (' sandbox . a ', 'sandbox', 'a'),
            ('"arbitrary_catalog" . "raw" . "order.items"', 'raw', 'order.items'),
            ('"say""hello"', 'raw', 'say"hello'), ('"space name"', 'raw', 'space name')]
        for native_name, schema_name, physical_name in examples:
            with self.subTest(name=native_name):
                result = inventory.resolve_object(self.principal, 'data_engineer', self.database.id, self.schema.id, native_name)
                self.assertEqual(result['ref']['schema_id'], self.schemas[schema_name].id)
                self.assertEqual(result['object']['physical_name'], physical_name)
                self.assertEqual(result['ref']['database_id'], self.database.id)

    def test_malformed_native_names_never_reach_rest(self):
        for name in ('', ' ', '.', 'a.', '.a', 'a..b', 'a.b.c.d', '""', '"unclosed', '"a"b',
                     'a b', 'a; SELECT 1', 'a--comment', 'a/*comment*/', 'a()', 'a+b', 'a\x00', '"a\n"', None):
            with self.subTest(name=name), self.assertRaises(ValueError):
                inventory.resolve_object(self.principal, 'data_engineer', self.database.id, self.schema.id, name)
        self.assertEqual(self.calls, [])

    def test_native_missing_registry_and_display_labels_have_no_fallback(self):
        for name in ('absent.a', 'missing_catalog.raw.a', 'Display.raw.a', 'arbitrary_catalog.missing.a'):
            with self.subTest(name=name), self.assertRaises(inventory.InventoryError) as error:
                inventory.resolve_object(self.principal, 'data_engineer', self.database.id, self.schema.id, name)
            self.assertEqual(error.exception.status, 404)
        self.assertEqual(self.calls, [])
        with control_store.connect() as db:
            db.execute("UPDATE schemas SET display_name='schema_label' WHERE id=?", (self.schema.id,))
        with self.assertRaises(inventory.InventoryError):
            inventory.resolve_object(self.principal, 'data_engineer', self.database.id, self.schema.id, 'schema_label.a')
        self.assertEqual(self.calls, [])
        with self.assertRaises(inventory.InventoryError) as error:
            inventory.resolve_object(self.principal, 'data_engineer', self.database.id, self.schema.id, 'raw.absent')
        self.assertEqual(error.exception.status, 404)

    def test_cross_database_resolve_requires_admin_and_rechecks_target(self):
        with control_store.connect() as db:
            other = registry.create_database(db, 'second_catalog', 'Second', '', 'test')
            db.execute("UPDATE databases SET state='READY' WHERE id=?", (other.id,))
            binding = registry.create_binding(db, other.id, 'other-polaris', 'FINANCE', 'other_catalog', 'storage', 'credential', 's3://synthetic/')
            registry.update_binding(db, other.id, binding.revision, 1, 'generation1')
            target = registry.create_schema(db, other.id, 'raw', 'Raw', 'raw', 'test')
        # Both grants are allowed by the synthetic authorizer; selected DB still bounds nonadmins.
        self.allowed_database_ids.add(other.id)
        with self.assertRaises(inventory.InventoryError) as error:
            inventory.resolve_object(self.principal, 'data_engineer', self.database.id, self.schema.id, 'second_catalog.raw.a')
        self.assertEqual(error.exception.status, 404)
        self.assertEqual(self.calls, [])
        self.authorization.reset_mock()
        result = inventory.resolve_object(self.principal, 'admin', self.database.id, self.schema.id, 'second_catalog.raw.a')
        self.assertEqual(result['ref']['database_id'], other.id)
        self.assertEqual(result['ref']['schema_id'], target.id)
        self.authorization.assert_any_call(self.principal, other.id, 'admin', expected_generation_id='generation1')
        self.calls.clear()
        self.allowed_database_ids.remove(other.id)
        with self.assertRaises(inventory.InventoryError):
            inventory.resolve_object(self.principal, 'admin', self.database.id, self.schema.id, 'second_catalog.raw.a')
        self.assertEqual(self.calls, [])
        self.allowed_database_ids.add(other.id)
        with control_store.connect() as db:
            db.execute("UPDATE catalog_bindings SET applied_revision=0 WHERE database_id=?", (other.id,))
        with self.assertRaises(inventory.InventoryError):
            inventory.resolve_object(self.principal, 'admin', self.database.id, self.schema.id, 'second_catalog.raw.a')
        self.assertEqual(self.calls, [])

    def test_empty_schema_and_bounded_page_filter(self):
        self.assertEqual([x['physical_name'] for x in self.list(kind='TABLE', q='b')['items']], ['b'])
        self.calls.clear()
        page = self.list(limit=1)
        self.assertEqual(len(page['items']), 1)
        self.assertEqual(sum('/tables/' in p or '/views/' in p for p in self.calls), 1)
        self.assertIsNotNone(page['next_cursor'])
        page2 = self.list(limit=1, cursor=page['next_cursor'])
        self.assertNotEqual(page['items'][0]['id'], page2['items'][0]['id'])
        self.objects = {'TABLE': {}, 'VIEW': {}}
        self.assertEqual(self.list(), {'items': [], 'next_cursor': None})

    def test_cursor_rejects_tampering_role_schema_revision_and_generation(self):
        token = self.list(limit=1)['next_cursor']
        for cursor in (token[:-3] + 'xxx', '', '!!'):
            with self.assertRaises(inventory.InventoryError):
                self.list(limit=1, cursor=cursor)
        with self.assertRaises(inventory.InventoryError):
            inventory.list_objects(self.principal, 'admin', self.schema.id, limit=1, cursor=token)
        with control_store.connect() as db:
            db.execute('UPDATE schemas SET revision=revision+1 WHERE id=?', (self.schema.id,))
        with self.assertRaises(inventory.InventoryError):
            self.list(limit=1, cursor=token)
        token = self.list(limit=1)['next_cursor']
        with control_store.connect() as db:
            db.execute("UPDATE catalog_bindings SET applied_generation_id='changed' WHERE database_id=?", (self.database.id,))
        with self.assertRaises(inventory.InventoryError):
            self.list(limit=1, cursor=token)

    def test_drop_recreate_assigns_new_id_and_rejects_old(self):
        first = inventory.resolve_object(self.principal, 'data_engineer', self.database.id, self.schema.id, 'a')
        self.objects['TABLE']['a']['table-uuid'] = 'uuid-recreated'
        with self.assertRaises(inventory.InventoryError) as exc:
            inventory.object_detail(self.principal, 'data_engineer', first['ref']['object_id'], detail='columns')
        self.assertEqual(exc.exception.status, 404)
        second = inventory.resolve_object(self.principal, 'data_engineer', self.database.id, self.schema.id, 'a')
        self.assertNotEqual(first['ref']['object_id'], second['ref']['object_id'])
        self.assertEqual(second['ref']['incarnation'], 2)

    def test_view_without_uuid_never_gets_fabricated_identity(self):
        view = self.list(kind='VIEW')['items'][0]
        self.assertIsNone(view['id'])
        self.assertIsNone(view['ref'])
        self.assertFalse(any(view['capabilities'].values()))
        with self.assertRaises(inventory.InventoryError):
            inventory.resolve_object(self.principal, 'data_engineer', self.database.id, self.schema.id, 'v')
        self.objects['VIEW']['v']['view-uuid'] = 'real-view-uuid'
        self.objects['VIEW']['v'].update({'current-version-id': 3, 'versions': [{'version-id': 3, 'schema-id': 4}],
            'schemas': [{'schema-id': 0, 'fields': [dict(name='obsolete', type='long', required=True)]},
                {'schema-id': 4, 'fields': [dict(name='value', type='string', required=False)]}]})
        stable = self.list(kind='VIEW')['items'][0]
        self.assertIsNotNone(stable['id'])
        result = inventory.object_detail(self.principal, 'data_engineer', stable['id'], detail='columns')
        self.assertEqual(result['detail'], 'columns')
        self.assertEqual(result['columns'], [dict(name='value', type='string', nullable=True)])

    def test_columns_and_64bit_snapshots(self):
        obj = self.list(kind='TABLE')['items'][0]
        report = inventory.object_detail(self.principal, 'data_engineer', obj['id'], detail='columns')
        self.assertEqual(set(report), {'object', 'ref', 'detail', 'columns'})
        self.assertEqual(report['detail'], 'columns')
        columns = report['columns']
        self.assertEqual(columns[0], dict(name='id', type='long', nullable=False))
        report = inventory.object_detail(self.principal, 'data_engineer', obj['id'], detail='snapshots')
        self.assertEqual(set(report), {'object', 'ref', 'detail', 'snapshots'})
        self.assertEqual(report['detail'], 'snapshots')
        snapshots = report['snapshots']
        self.assertEqual(snapshots[0]['snapshot_id'], '9223372036854775806')
        self.assertEqual(snapshots[0]['parent_id'], '9223372036854775805')
        self.assertIsInstance(snapshots[0]['timestamp_ms'], str)

    def test_public_refs_keep_authorized_binding_snapshot_after_metadata_read(self):
        with control_store.connect() as db:
            obj = registry.observe_object(db, self.schema.id, 'a', 'TABLE', 'uuid-a', 'a')
        expected = dict(database_id=self.database.id, schema_id=self.schema.id, object_id=obj.id,
            incarnation=obj.incarnation, binding_revision=1, generation_id='generation1')
        load_metadata = inventory._metadata

        def metadata_then_binding_change(*args):
            metadata = load_metadata(*args)
            with control_store.connect() as db:
                db.execute('''UPDATE catalog_bindings SET desired_revision=2, applied_revision=2,
                    applied_generation_id='changed' WHERE database_id=?''', (self.database.id,))
            return metadata

        actions = (
            ('list', lambda: self.list(kind='TABLE', limit=1)),
            ('resolve', lambda: inventory.resolve_object(self.principal, 'data_engineer',
                self.database.id, self.schema.id, 'a')),
            ('detail', lambda: inventory.object_detail(self.principal, 'data_engineer', obj.id, detail='columns')))
        for operation, action in actions:
            with self.subTest(operation=operation):
                with control_store.connect() as db:
                    db.execute('''UPDATE catalog_bindings SET desired_revision=1, applied_revision=1,
                        applied_generation_id='generation1' WHERE database_id=?''', (self.database.id,))
                with patch.object(inventory, '_metadata', side_effect=metadata_then_binding_change) as metadata:
                    result = action()
                metadata.assert_called_once()
                with control_store.connect() as db:
                    current = registry.get_binding(db, self.database.id)
                self.assertEqual((current.desired_revision, current.applied_generation_id), (2, 'changed'))
                if operation == 'list':
                    self.assertEqual(result['items'][0]['ref'], expected)
                else:
                    self.assertEqual(result['ref'], expected)
                    self.assertEqual(result['object']['ref'], expected)

    def test_preview_qualified_sql_snapshot_and_ddl_permissions(self):
        obj = self.list(kind='TABLE')['items'][0]
        context = SimpleNamespace(sql_catalog='arbitrary_catalog', sql_schema='raw', generation_id='generation1')
        envelope = {'id': 'application-id', 'next_uri': None}
        with patch.object(query_context, 'resolve_context', return_value=context), patch.object(query_service, 'submit', return_value=envelope) as submit:
            result = inventory.object_detail(self.principal, 'data_engineer', obj['id'], detail='preview', snapshot_id='9223372036854775806')
            self.assertIs(result['query'], envelope)
            self.assertEqual(set(result), {'object', 'ref', 'detail', 'query'})
            self.assertEqual(result['detail'], 'preview')
            self.assertEqual(submit.call_args.kwargs['mode'], 'read-only')
            self.assertIn('"arbitrary_catalog"."raw"."a" FOR VERSION AS OF 9223372036854775806', submit.call_args.args[2])
            self.assertEqual(submit.call_args.kwargs['max_rows'], 50)
            submit.reset_mock()
            for snapshot in ('1', '9223372036854775806 OR 1=1', 1):
                with self.assertRaises((ValueError, inventory.InventoryError)):
                    inventory.object_detail(self.principal, 'data_engineer', obj['id'], detail='preview', snapshot_id=snapshot)
            submit.assert_not_called()
            result = inventory.object_detail(self.principal, 'data_engineer', obj['id'], detail='ddl')
            self.assertEqual(set(result), {'object', 'ref', 'detail', 'query'})
            self.assertEqual(result['detail'], 'ddl')
            self.assertEqual(submit.call_args.kwargs['mode'], 'read-only')
            self.assertTrue(submit.call_args.args[2].startswith('SHOW CREATE TABLE '))
        marts = self.schemas['marts']
        with control_store.connect() as db:
            record = registry.observe_object(db, marts.id, 'a', 'TABLE', 'uuid-a', 'a')
        with self.assertRaises(inventory.InventoryError):
            inventory.object_detail(self.principal, 'bi_viewer', record.id, detail='ddl')

    def test_binding_credentials_exact_and_private(self):
        path = self.root / 'catalog-private.json'
        entry = dict(service_ref='polaris', realm='SALES', catalog='remote_catalog', generation_id='generation1', client_id='c', client_secret='s')
        path.write_text(json.dumps(dict(version=1, databases={self.database.id: entry})))
        path.chmod(0o600)
        with patch.object(runtime_profile, '_current', SimpleNamespace(catalog_auth_file=str(path))):
            self.assertEqual(inventory._credentials(self.database, self.binding), entry)
            for key in ('realm', 'generation_id', 'service_ref', 'catalog'):
                path.write_text(json.dumps(dict(version=1, databases={self.database.id: dict(entry, **{key: 'different'})})))
                with self.assertRaises(inventory.InventoryError):
                    inventory._credentials(self.database, self.binding)
            path.write_text(json.dumps(dict(version=1, databases={self.database.id: dict(entry, root='forbidden')})))
            with self.assertRaises(inventory.InventoryError):
                inventory._credentials(self.database, self.binding)

    def test_http_errors_suppress_credentials_and_response(self):
        with patch.object(inventory.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = OSError('Bearer synthetic-secret response body')
            with self.assertRaises(inventory.InventoryError) as error:
                inventory._http(inventory.urllib.request.Request('http://127.0.0.1:1234'))
            self.assertNotIn('synthetic', str(error.exception))

    def test_scoped_port_forward_and_cleanup_are_mocked(self):
        entry = dict(service_ref='scoped-polaris', realm='SALES', catalog='remote_catalog', generation_id='generation1', client_id='synthetic-client', client_secret='synthetic-secret')
        with patch.object(inventory, '_credentials', return_value=entry), \
                patch.object(inventory.socket, 'socket') as sock, \
                patch.object(inventory.socket, 'create_connection'), \
                patch.object(inventory.subprocess, 'Popen') as process, \
                patch.object(inventory, '_http', side_effect=[{'access_token': 'synthetic-bearer'}, {'identifiers': []}]) as http:
            sock.return_value.__enter__.return_value.getsockname.return_value = ('127.0.0.1', 12345)
            process.return_value.poll.return_value = None
            with CATALOG_API(self.database, self.binding) as request:
                self.assertEqual(request('/namespaces/raw/tables'), {'identifiers': []})
            args = process.call_args.args[0]
            self.assertEqual(args[:3], ['rtk', 'proxy', 'kubectl'])
            self.assertIn('shirokuma-dev', args)
            self.assertIn('svc/scoped-polaris', args)
            self.assertNotIn('secret', ' '.join(args))
            self.assertNotIn('get', args)
            self.assertEqual(http.call_args.args[0].full_url, 'http://127.0.0.1:12345/api/catalog/v1/remote_catalog/namespaces/raw/tables')
            process.return_value.terminate.assert_called_once_with()
            process.return_value.wait.assert_called_once_with(timeout=5)
            http.side_effect = RuntimeError('Bearer synthetic-secret')
            with self.assertRaises(inventory.InventoryError) as error:
                with CATALOG_API(self.database, self.binding):
                    pass
            self.assertNotIn('synthetic', str(error.exception))
            self.assertEqual(process.return_value.terminate.call_count, 2)


if __name__ == '__main__':
    unittest.main()
