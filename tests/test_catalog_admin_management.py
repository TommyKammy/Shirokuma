import contextlib
import base64
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import catalog_admin as admin
import request_auth as auth


class ManagementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        environment = patch.dict(os.environ, SHIROKUMA_STATE_DIR=str(Path(temporary.name).resolve()))
        environment.start()
        self.addCleanup(environment.stop)

    def registry_snapshot(self):
        import control_store
        tables = ('databases', 'schemas', 'catalog_bindings', 'object_bindings', 'grants',
                  'operations', 'database_create_requests', 'registry_register_requests',
                  'warehouse_controls')
        with control_store.connect() as db:
            return {table: [tuple(row) for row in db.execute('SELECT * FROM '+table+' ORDER BY rowid')]
                    for table in tables}

    def reserve_warehouse(self, operation_state, control_state):
        import control_store
        import data_management as dm
        import warehouse_admission as admission
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            dm.record_operation(db, dm.Operation('reserved', 'admin', 'WAREHOUSE_CONFIG_APPLY', (),
                None, operation_state, (), None, (), ()))
            admission._ensure(db, 'LOCAL')
            db.execute('UPDATE warehouse_controls SET state=?,operation_id=? WHERE warehouse_id=?',
                       (control_state, 'reserved', 'LOCAL'))

    def test_new_physical_declarations_reject_reservations_without_partial_objects(self):
        import warehouse_operations as operations
        from tests.test_database_generation import spec
        principal = auth.AuthenticatedPrincipal('admin', 'admin', True)
        for method, payload in ((admin.create_database, dict(sql_catalog='research', profile_id='local-polaris-1.8.0')),
                                (admin.register_spec, spec(2))):
            for operation_state, control_state in (('PREPARED', 'OPEN'), ('UNKNOWN', 'UNKNOWN'),
                                                   ('APPLYING', 'APPLYING'), ('DRAINING', 'DRAINING')):
                with self.subTest(method=method.__name__, state=operation_state), \
                        tempfile.TemporaryDirectory() as directory, \
                        patch.dict(os.environ, SHIROKUMA_STATE_DIR=directory):
                    self.reserve_warehouse(operation_state, control_state)
                    before = self.registry_snapshot()
                    with self.assertRaises(operations.OperationError) as caught:
                        method(principal, 'admin', payload, 'new-key')
                    self.assertEqual((caught.exception.code, caught.exception.status),
                                     ('WAREHOUSE_OPERATION_CONFLICT', 409))
                    self.assertEqual(self.registry_snapshot(), before)

    def test_create_and_register_replays_and_payload_conflicts_survive_reservation(self):
        import warehouse_operations as operations
        from tests.test_database_generation import spec
        principal = auth.AuthenticatedPrincipal('admin', 'admin', True)
        for method, payload, changed in (
                (admin.create_database, dict(sql_catalog='research', profile_id='local-polaris-1.8.0'),
                 dict(sql_catalog='different', profile_id='local-polaris-1.8.0')),
                (admin.register_spec, spec(2), dict(spec(2), generation_id='different'))):
            for operation_state, control_state in (('PREPARED', 'OPEN'), ('UNKNOWN', 'UNKNOWN'), ('APPLYING', 'APPLYING')):
                with self.subTest(method=method.__name__, state=operation_state), \
                        tempfile.TemporaryDirectory() as directory, \
                        patch.dict(os.environ, SHIROKUMA_STATE_DIR=directory):
                    result = method(principal, 'admin', payload, 'saved-key')
                    self.reserve_warehouse(operation_state, control_state)
                    before = self.registry_snapshot()
                    self.assertEqual(method(principal, 'admin', payload, 'saved-key'), result)
                    with self.assertRaisesRegex(operations.OperationError, 'IDEMPOTENCY_CONFLICT'):
                        method(principal, 'admin', changed, 'saved-key')
                    self.assertEqual(self.registry_snapshot(), before)

    def test_closed_control_without_operation_rejects_new_declarations(self):
        import control_store
        import warehouse_admission as admission
        import warehouse_operations as operations
        from tests.test_database_generation import spec
        with control_store.connect() as db:
            admission._ensure(db, 'LOCAL')
            db.execute("UPDATE warehouse_controls SET state='UNKNOWN'")
        before = self.registry_snapshot()
        principal = auth.AuthenticatedPrincipal('admin', 'admin', True)
        for method, payload in ((admin.create_database, dict(sql_catalog='research', profile_id='local-polaris-1.8.0')),
                                (admin.register_spec, spec(1))):
            with self.assertRaisesRegex(operations.OperationError, 'WAREHOUSE_OPERATION_CONFLICT'):
                method(principal, 'admin', payload, 'new-key')
            self.assertEqual(self.registry_snapshot(), before)

    def test_reservation_cannot_interleave_control_check_and_physical_registration(self):
        import sqlite3
        import control_store
        import data_management as dm
        import warehouse_admission as admission
        import warehouse_operations as operations
        from tests.test_database_generation import spec
        principal = auth.AuthenticatedPrincipal('admin', 'admin', True)
        for method, payload in ((admin.create_database, dict(sql_catalog='research', profile_id='local-polaris-1.8.0')),
                                (admin.register_spec, spec(2))):
            with self.subTest(method=method.__name__), tempfile.TemporaryDirectory() as directory, \
                    patch.dict(os.environ, SHIROKUMA_STATE_DIR=directory):
                with control_store.connect() as db:
                    admission._ensure(db, 'LOCAL')
                    dm.record_operation(db, dm.Operation('competitor', 'admin', 'WAREHOUSE_CONFIG_APPLY', (),
                        None, 'APPLYING', (), None, (), ()))
                with contextlib.closing(sqlite3.connect(control_store.root()/'jobs.sqlite3', timeout=0)) as competitor:
                    competitor.row_factory = sqlite3.Row
                    ensure, binding = admission._ensure, dm.create_binding
                    checked_connections = []
                    def assert_write_reserved(db):
                        self.assertTrue(db.in_transaction)
                        with self.assertRaisesRegex(sqlite3.OperationalError, 'locked'):
                            competitor.execute('BEGIN IMMEDIATE')
                    def check_control(db, warehouse_id):
                        assert_write_reserved(db)
                        checked_connections.append(db)
                        return ensure(db, warehouse_id)
                    def insert_binding(db, *args, **kwargs):
                        self.assertIs(db, checked_connections[0])
                        assert_write_reserved(db)
                        return binding(db, *args, **kwargs)
                    with patch.object(admission, '_ensure', side_effect=check_control) as checked, \
                            patch.object(dm, 'create_binding', side_effect=insert_binding) as inserted:
                        method(principal, 'admin', payload, 'first')
                    checked.assert_called_once()
                    self.assertEqual(inserted.call_count, 1 if method is admin.create_database else 2)
                    competitor.execute('BEGIN IMMEDIATE')
                    admission.begin_drain(competitor, operation_id='competitor')
                    competitor.commit()
                before = self.registry_snapshot()
                with self.assertRaisesRegex(operations.OperationError, 'WAREHOUSE_OPERATION_CONFLICT'):
                    admin.create_database(principal, 'admin', dict(sql_catalog='later', profile_id='local-polaris-1.8.0'), 'later')
                self.assertEqual(self.registry_snapshot(), before)

    def test_register_spec_empty_store_roundtrip_preserves_public_identities_and_revisions(self):
        import control_store
        import warehouse_operations
        from tests.test_database_generation import spec
        principal = auth.AuthenticatedPrincipal('admin', 'admin', True)
        value = spec(3)
        value['databases']['opaque_0']['database_revision'] = 7
        value['databases']['opaque_0']['binding']['desired_revision'] = 4
        value['databases']['opaque_0']['schemas'][0]['revision'] = 3
        value['grant_revisions'] = [dict(principal_id='reader', database_id='opaque_1', maximum_role='revoked', revision=8)]
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as recreated:
            with patch.dict(os.environ, SHIROKUMA_STATE_DIR=first):
                registered = admin.register_spec(principal, 'admin', value, 'register-one')
                exported = admin.export_spec(value['generation_id'], value)
                self.assertEqual(registered, admin.register_spec(principal, 'admin', value, 'register-one'))
                changed = dict(value, generation_id='different')
                with self.assertRaisesRegex(warehouse_operations.OperationError, 'IDEMPOTENCY_CONFLICT'):
                    admin.register_spec(principal, 'admin', changed, 'register-one')
                with self.assertRaisesRegex(warehouse_operations.OperationError, 'EMPTY_REGISTRY'):
                    admin.register_spec(principal, 'admin', value, 'new-key')
                self.assertEqual(admin.inspect_operation(principal, 'admin', registered['operation_id'])['database_ids'], sorted(value['databases']))
            with patch.dict(os.environ, SHIROKUMA_STATE_DIR=recreated):
                admin.register_spec(principal, 'admin', exported, 'restore-public-spec')
                self.assertEqual(admin.export_spec(value['generation_id'], exported), exported)
                with control_store.connect() as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM object_bindings').fetchone()[0], 0)
                    self.assertEqual({row[0] for row in db.execute('SELECT applied_revision FROM catalog_bindings')}, {0})
                    self.assertEqual({row[0] for row in db.execute('SELECT state FROM databases')}, {'registered'})
        with self.assertRaises(PermissionError):
            admin.register_spec(auth.AuthenticatedPrincipal('reader', 'bi_viewer', False), 'bi_viewer', value, 'denied')

    def test_register_spec_transaction_rolls_back_on_partial_failure(self):
        import control_store
        import data_management
        from tests.test_database_generation import spec
        principal = auth.AuthenticatedPrincipal('admin', 'admin', True)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, SHIROKUMA_STATE_DIR=directory):
            with patch.object(data_management, 'create_binding', side_effect=ValueError('failed')):
                with self.assertRaises(ValueError):
                    admin.register_spec(principal, 'admin', spec(2), 'rollback')
            with control_store.connect() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM databases').fetchone()[0], 0)
                self.assertEqual(db.execute('SELECT count(*) FROM registry_register_requests').fetchone()[0], 0)

    def test_register_spec_cli_authenticates_explicit_private_headers(self):
        from tests.test_database_generation import spec
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            root.chmod(0o700)
            headers, accounts, source = root/'headers.json', root/'accounts.json', root/'approved.json'
            source.write_text(json.dumps(spec(1)))
            salt = '23'*16
            accounts.write_text(json.dumps({'admin': dict(role='admin', platform_admin=True, salt=salt,
                password_hash=auth.password_hash('registration-password', salt))}))
            token = base64.b64encode(b'admin:registration-password').decode()
            headers.write_text(json.dumps({'Authorization': 'Basic '+token, 'X-Shirokuma-Role': 'admin'}))
            accounts.chmod(0o600)
            headers.chmod(0o600)
            command = ['register-spec', '--spec', str(source), '--idempotency-key', 'cli-restore',
                       '--auth-headers', str(headers)]
            with patch.dict(os.environ, SHIROKUMA_STATE_DIR=str(root/'state'), SHIROKUMA_AUTH_MODE='shared',
                            SHIROKUMA_AUTH_FILE=str(accounts)):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(admin.main(command), 0)
                first = json.loads(output.getvalue())
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(admin.main(command), 0)
                self.assertEqual(json.loads(output.getvalue()), first)
                headers.write_text(json.dumps({'Authorization': 'Basic '+token, 'X-Shirokuma-Role': 'bi_viewer'}))
                with contextlib.redirect_stderr(io.StringIO()) as error:
                    self.assertEqual(admin.main(command), 1)
                self.assertNotIn('registration-password', error.getvalue())

    def test_register_spec_rejects_unknown_fields_profiles_and_secret_content(self):
        import copy
        import warehouse_operations
        from tests.test_database_generation import spec
        principal = auth.AuthenticatedPrincipal('admin', 'admin', True)
        for mutate in (lambda value: value.update(private='never-store'),
                       lambda value: value['profile'].update(id='unregistered'),
                       lambda value: value['databases']['opaque_0']['secret_refs']['root'].update(data='never-store')):
            value = copy.deepcopy(spec(1))
            mutate(value)
            with self.assertRaisesRegex(warehouse_operations.OperationError, 'INVALID_REGISTER_SPEC'):
                admin.register_spec(principal, 'admin', value, 'invalid')

    def test_explicit_platform_admin_and_selected_role(self):
        for principal, role in [(auth.AuthenticatedPrincipal('a', 'admin', False), 'admin'),
                                (auth.AuthenticatedPrincipal('a', 'admin', True), 'data_engineer'),
                                (auth.AuthenticatedPrincipal('a', 'data_engineer', True), 'admin')]:
            with self.assertRaises(PermissionError):
                admin.require_admin(principal, role)

    def test_registered_generation_only_and_server_owned_observations(self):
        core = types.SimpleNamespace(create_plan=Mock(return_value={'id': 'op'}))
        principal = auth.AuthenticatedPrincipal('admin', 'admin', True)
        with tempfile.TemporaryDirectory() as temp, patch.dict(sys.modules, warehouse_operations=core), \
                patch.dict(os.environ, SHIROKUMA_PUBLIC_BUNDLE_ROOT=temp, SHIROKUMA_PUBLIC_OBSERVATIONS_ROOT=temp), \
                patch('database_generation.load_bundle', return_value={'manifest': {'generation_id': 'g'}}) as load:
            Path(temp, 'g.json').write_text('[]')
            self.assertEqual(admin.public_plan(principal, 'admin', 'g', 'key'), {'id': 'op'})
            self.assertEqual(load.call_args.args[0], Path(temp).resolve()/'g')
            self.assertEqual(core.create_plan.call_args.args[:3], ('admin', admin.PLAN_ROUTE, 'key'))
            self.assertEqual(core.create_plan.call_args.args[-1], [])
            for invalid in ('../private', '/private', '', 'g/../g'):
                with self.assertRaises(ValueError):
                    admin.public_plan(principal, 'admin', invalid, 'key')
            self.assertEqual(load.call_count, 1)

    def test_cli_forwards_private_file_and_sanitizes_failure(self):
        with patch('warehouse_operator.run', side_effect=RuntimeError('password-secret')) as run:
            output = io.StringIO()
            with contextlib.redirect_stderr(output):
                self.assertEqual(admin.main(['apply', '--config', '/explicit/config',
                    '--operation-id', 'op', '--expected-revision', '2']), 1)
            self.assertEqual(run.call_args.args[0].config, Path('/explicit/config'))
            self.assertNotIn('password-secret', output.getvalue())

    def test_inspect_does_not_escalate_selected_role(self):
        core = types.SimpleNamespace(inspect=Mock(return_value={}), inspect_actions=Mock(return_value=()))
        with patch.dict(sys.modules, warehouse_operations=core):
            with self.assertRaises(PermissionError):
                admin.inspect_operation(auth.AuthenticatedPrincipal('owner', 'admin', True), 'bi_viewer', 'op')
            core.inspect.assert_not_called()
            core.inspect_actions.assert_not_called()

    def test_query_reconcile_is_thin_authenticated_and_yaml_independent(self):
        principal = auth.AuthenticatedPrincipal('admin', 'admin', True)
        query = types.SimpleNamespace(reconcile_query_from_backend=Mock(return_value={'id': 'q', 'state': 'FAILED'}))
        with tempfile.TemporaryDirectory() as temp, patch.dict(sys.modules, query_service=query, yaml=None), \
                patch.object(auth, 'authenticate_principal', return_value=principal):
            directory = Path(temp).resolve()
            directory.chmod(0o700)
            path = directory/'headers'
            path.write_text('{"Authorization":"Basic fixture","X-Shirokuma-Role":"admin"}')
            path.chmod(0o600)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(admin.main(['reconcile-query', '--auth-headers', str(path), '--query-id', 'q']), 0)
            self.assertIn('FAILED', output.getvalue())
            query.reconcile_query_from_backend.assert_called_once_with(principal, 'q', selected_role='admin')
            query.reconcile_query_from_backend.reset_mock()
            with patch.object(auth, 'authenticate_principal', return_value=auth.AuthenticatedPrincipal('admin', 'admin', False)), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(admin.main(['reconcile-query', '--auth-headers', str(path), '--query-id', 'q']), 0)
            query.reconcile_query_from_backend.assert_called_once_with(
                auth.AuthenticatedPrincipal('admin', 'admin', False), 'q', selected_role='admin')


class ThinAuthTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.directory.chmod(0o700)
        self.headers = self.directory/'headers.json'
        self.accounts = self.directory/'accounts.json'
        salt = '17'*16
        password_hash = auth.password_hash('fixture-password', salt)
        self.users = {
            'owner': dict(role='data_engineer', platform_admin=False, salt=salt, password_hash=password_hash),
            'admin': dict(role='admin', platform_admin=True, salt=salt, password_hash=password_hash)}
        self.save_accounts()
        environment = patch.dict(os.environ, SHIROKUMA_AUTH_MODE='shared', SHIROKUMA_AUTH_FILE=str(self.accounts),
                                 SHIROKUMA_STATE_DIR=str(self.directory/'state'))
        environment.start()
        self.addCleanup(environment.stop)
        def inspect(operation_id, *, actor, is_admin=False):
            if actor != 'owner' and not is_admin:
                raise PermissionError('Other owner')
            return {'id': operation_id, 'actor': 'owner'}
        def reconcile(principal, query_id, *, selected_role):
            if principal.principal_id != 'owner' and not (selected_role == 'admin' and
                    principal.platform_admin and principal.maximum_role == 'admin'):
                raise PermissionError('Other owner')
            return {'id': query_id, 'state': 'UNKNOWN'}
        self.core = types.SimpleNamespace(inspect=Mock(side_effect=inspect), inspect_actions=Mock(return_value=()))
        self.query = types.SimpleNamespace(reconcile_query_from_backend=Mock(side_effect=reconcile))
        unavailable = patch.dict(sys.modules, warehouse_operations=self.core, query_service=self.query,
            warehouse_operator=None, database_generation=None, local_gitops=None, yaml=None)
        unavailable.start()
        self.addCleanup(unavailable.stop)

    def save_accounts(self):
        self.accounts.write_text(json.dumps(self.users))
        self.accounts.chmod(0o600)

    def select(self, username, role):
        token = base64.b64encode((username+':fixture-password').encode()).decode()
        self.headers.write_text(json.dumps({'Authorization': 'Basic '+token, 'X-Shirokuma-Role': role}))
        self.headers.chmod(0o600)

    def execute(self, command):
        arguments = ['--operation-id', 'op'] if command == 'inspect-operation' else ['--query-id', 'q']
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return admin.main([command, '--auth-headers', str(self.headers), *arguments])

    def test_owner_query_reconcile_thin_and_management_inspection_rejected(self):
        self.select('owner', 'data_engineer')
        self.assertEqual(self.execute('inspect-operation'), 1)
        self.assertEqual(self.execute('reconcile-query'), 0)
        self.core.inspect.assert_not_called()
        self.query.reconcile_query_from_backend.assert_called_once_with(
            auth.AuthenticatedPrincipal('owner', 'data_engineer', False), 'q', selected_role='data_engineer')
        self.assertEqual(set(p.name for p in self.directory.iterdir()), {'headers.json', 'accounts.json'})
        self.assertFalse((self.directory/'state').exists())

    def test_selected_downgraded_admin_cannot_access_other_owner(self):
        for role in ('bi_viewer', 'data_engineer'):
            self.select('admin', role)
            for command in ('inspect-operation', 'reconcile-query'):
                self.assertEqual(self.execute(command), 1)
            self.core.inspect.assert_not_called()
            self.query.reconcile_query_from_backend.assert_called_with(
                auth.AuthenticatedPrincipal('admin', 'admin', True), 'q', selected_role=role)
        self.select('admin', 'admin')
        for command in ('inspect-operation', 'reconcile-query'):
            self.assertEqual(self.execute(command), 0)
        self.core.inspect.assert_called_with('op', actor='admin', is_admin=True)

    def test_canonical_single_role_and_current_maximum_and_permissions(self):
        for role in ('viewer', 'engineer', '', 'ADMIN', ['admin'], 'admin'):
            self.select('owner', role)
            for command in ('inspect-operation', 'reconcile-query'):
                self.assertEqual(self.execute(command), 1)
        self.core.inspect.assert_not_called()
        self.query.reconcile_query_from_backend.assert_not_called()
        self.select('admin', 'admin')
        self.users['admin'].update(role='data_engineer', platform_admin=False)
        self.save_accounts()
        self.assertEqual(self.execute('inspect-operation'), 1)
        self.assertEqual(self.execute('reconcile-query'), 1)
        self.select('owner', 'bi_viewer')
        data = self.headers.read_text()
        self.headers.write_text(data[:-1]+',"X-Shirokuma-Role":"admin"}')
        self.assertEqual(self.execute('inspect-operation'), 1)
        self.select('owner', 'data_engineer')
        self.headers.chmod(0o644)
        self.assertEqual(self.execute('reconcile-query'), 1)
