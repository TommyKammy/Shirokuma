"""Display edits run only against temporary control stores; no backend execution."""
from dataclasses import asdict, replace
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / 'tools/local_experiment'
sys.path.insert(0, str(TOOLS))
import catalog_admin
import control_store as store
import data_management as dm
import display_management as display
import request_auth
from warehouse_operations import OperationError


class DisplayManagementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        env = patch.dict(os.environ, SHIROKUMA_STATE_DIR=temporary.name)
        env.start()
        self.addCleanup(env.stop)
        self.admin = request_auth.AuthenticatedPrincipal('admin', 'admin', True)
        with store.connect() as db:
            self.database = dm.create_database(db, 'research', 'Research', '', 'user')
            self.binding = dm.create_binding(db, self.database.id, 'service', 'realm', 'physical',
                'storage-private', 'credential-private', 's3://private/base')
            self.schemas = [dm.create_schema(db, self.database.id, name, name, name, 'user')
                for name in ('raw', 'staging', 'marts', 'sandbox')]
        self.payload = dict(display_name='  <b>研究</b> e\u0301 😀 ', description='line\n\r\t', expected_revision=1)

    def mutate(self, payload=None, key='key'):
        return display.mutate(self.admin, 'admin', 'database', self.database.id,
            self.payload if payload is None else payload, key)

    def test_cas_audit_replay_restart_and_binding_immutability(self):
        result = self.mutate()
        self.assertEqual(set(result), {'operation_id', 'state', 'resource_type', 'resource_id', 'revision'})
        self.assertEqual((result['state'], result['revision']), ('APPLIED', 2))
        self.assertEqual(self.mutate(), result)
        with store.connect() as db:
            changed = dm.get_database(db, self.database.id)
            self.assertEqual(changed, replace(self.database, display_name=self.payload['display_name'],
                description=self.payload['description'], revision=2))
            self.assertEqual(dm.get_binding(db, self.database.id), self.binding)
            self.assertEqual([dm.get_schema(db, s.id) for s in self.schemas], self.schemas)
            self.assertEqual(db.execute('SELECT count(*) FROM metadata_requests').fetchone()[0], 1)
        operation = catalog_admin.inspect_operation(self.admin, 'admin', result['operation_id'])
        self.assertEqual(operation['audit']['before']['display_name'], 'Research')
        self.assertEqual(operation['audit']['after']['display_name'], self.payload['display_name'])
        code = 'import json,display_management as d,request_auth as a; print(json.dumps(d.mutate(a.AuthenticatedPrincipal("admin","admin",True),"admin","database",'+repr(self.database.id)+','+repr(self.payload)+',"key")))'
        child = subprocess.run([sys.executable, '-c', code], env=dict(os.environ, PYTHONPATH=str(TOOLS)), capture_output=True, text=True)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(json.loads(child.stdout), result)
        for payload, key, error in ((dict(self.payload, display_name='different'), 'key', 'IDEMPOTENCY_CONFLICT'),
                                    (self.payload, 'new-key', 'REVISION_CONFLICT')):
            with self.assertRaises(OperationError) as raised:
                self.mutate(payload, key)
            self.assertEqual(raised.exception.code, error)

    def test_competing_cas_and_same_key_race(self):
        def run(key):
            try:
                return self.mutate(key=key)
            except OperationError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, ['one', 'two']))
        self.assertEqual(sum(isinstance(r, dict) for r in results), 1)
        self.assertIn('REVISION_CONFLICT', results)
        payload = dict(self.payload, expected_revision=2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            replay = list(pool.map(lambda _: self.mutate(payload, 'same'), range(2)))
        self.assertEqual(replay[0], replay[1])

    def test_auth_current_maximum_and_selected_role_on_read_write_replay_inspect(self):
        result = self.mutate()
        for principal, role in ((replace(self.admin, maximum_role='bi_viewer'), 'admin'),
                (replace(self.admin, platform_admin=False), 'admin'), (self.admin, 'data_engineer')):
            for call in (lambda: display.list_databases(principal, role),
                    lambda: display.mutate(principal, role, 'database', self.database.id, self.payload, 'key'),
                    lambda: catalog_admin.inspect_operation(principal, role, result['operation_id'])):
                with self.assertRaises(PermissionError):
                    call()

    def test_validation_before_any_mutation_and_unknown(self):
        invalid = [dict(self.payload, **{field: value}) for field, values in {
            'display_name': ['', ' \t', 'x\n', 'x\x7f', 1, None, [], 'a'*129, '\ud800', '\udc00'],
            'description': [None, 1, [], 'a'*2049, '\x00', '\x85', '\ud800', '\udc00'],
            'expected_revision': [True, False, 1.0, '1', None, 0, -1]}.items() for value in values]
        invalid += [dict(self.payload, extra='x'), {}, [], dict(display_name='a', expected_revision=1)]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(OperationError) as error:
                self.mutate(payload)
            self.assertEqual(error.exception.status, 400)
        for key in ('', '   ', '\t', 'x'*257, None):
            with self.assertRaises(OperationError) as error:
                self.mutate(key=key)
            self.assertEqual(error.exception.status, 400)
        with self.assertRaises(OperationError) as error:
            display.mutate(self.admin, 'admin', 'database', 'missing', self.payload, 'key')
        self.assertEqual(error.exception.status, 404)
        with store.connect() as db:
            self.assertEqual(dm.get_database(db, self.database.id), self.database)
            self.assertEqual(db.execute('SELECT count(*) FROM operations').fetchone()[0], 0)
        self.mutate(dict(self.payload, display_name='😀'*128, description='a'*2048))

    def test_schema_purpose_and_all_other_fields_immutable_duplicate_labels_allowed(self):
        for schema in self.schemas:
            payload = dict(display_name='Same <label>', expected_revision=1)
            for extra in ('purpose', 'sql_name', 'database_id', 'description'):
                with self.assertRaises(OperationError):
                    display.mutate(self.admin, 'admin', 'schema', schema.id, dict(payload, **{extra:'changed'}), 'key')
            result = display.mutate(self.admin, 'admin', 'schema', schema.id, payload, 'key')
            self.assertEqual(result['revision'], 2)
            with store.connect() as db:
                self.assertEqual(dm.get_schema(db, schema.id), replace(schema, display_name='Same <label>', revision=2))
                self.assertEqual(dm.get_binding(db, self.database.id), self.binding)

    def test_transaction_rolls_back_metadata_when_audit_fails(self):
        with patch.object(display, '_json', side_effect=lambda value: (_ for _ in ()).throw(RuntimeError('audit'))
                if isinstance(value, dict) and set(value) == {'display_name','description','revision'} else json.dumps(value, sort_keys=True)):
            with self.assertRaises(RuntimeError):
                self.mutate()
        with store.connect() as db:
            self.assertEqual(dm.get_database(db, self.database.id), self.database)
            for table in ('operations','metadata_requests','metadata_audit'):
                self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0], 0)

    def test_admin_list_exact_safe_shape_includes_every_state(self):
        for state in ('registered', 'READY', 'FAILED'):
            with store.connect() as db:
                db.execute('UPDATE databases SET state=? WHERE id=?', (state, self.database.id))
            result = display.list_databases(self.admin, 'admin')
            self.assertEqual(set(result), {'databases'})
            item = result['databases'][0]
            self.assertEqual(set(item), {'id','sql_catalog','display_name','description','state','revision','schemas','binding'})
            self.assertEqual(item['state'], state)
            self.assertEqual(len(item['schemas']), 4)
            self.assertEqual(set(item['binding']), {'database_id','polaris_service_ref','polaris_realm','polaris_catalog',
                'desired_revision','applied_revision','applied_generation_id','revision'})
            self.assertNotIn('private', json.dumps(result))
