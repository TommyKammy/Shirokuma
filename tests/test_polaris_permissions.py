import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('permissions', ROOT / 'tools/local_experiment/polaris_permissions.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class Polaris:
    def __init__(self):
        self.entities = {}
        self.roles = {}
        self.grants = {}
        self.namespaces = {}
        self.writes = []

    def __call__(self, method, path, body=None):
        base = M.MANAGEMENT
        if method == 'GET':
            if path == base + '/catalogs':
                return {'catalogs': [v for k, v in self.entities.items() if k.startswith(base + '/catalogs/') and '/catalog-roles/' not in k]}
            if path.endswith('/namespaces'):
                return {'namespaces': copy.deepcopy(self.namespaces.get(path, []))}
            if path.endswith('/grants'):
                return {'grants': copy.deepcopy(self.grants.get(path, []))}
            if path.endswith('/principal-roles') or '/catalog-roles/' in path and '/principal-roles/' in path:
                return {'roles': copy.deepcopy(self.roles.get(path, []))}
            if path not in self.entities:
                raise M.APIError(404)
            return copy.deepcopy(self.entities[path])
        self.writes.append((method, path, copy.deepcopy(body)))
        if method == 'POST':
            if path.endswith('/namespaces'):
                self.namespaces.setdefault(path, []).append(body['namespace'])
                return {}
            key = next(k for k in ('catalog', 'principal', 'principalRole', 'catalogRole') if k in body)
            entity = copy.deepcopy(body[key])
            if key == 'principal':
                entity['clientId'] = 'id-' + entity['name']
            self.entities[path + '/' + entity['name']] = entity
            if key == 'principal':
                return {'principal': entity, 'credentials': {'clientId': entity['clientId'], 'clientSecret': 'private-secret'}}
            return entity
        if path.endswith('/grants'):
            self.grants.setdefault(path, []).append(copy.deepcopy(body['grant']))
        else:
            self.roles.setdefault(path, []).append(copy.deepcopy(next(iter(body.values()))))
        return {}


class PolarisPermissionTests(unittest.TestCase):
    def setUp(self):
        self.api = Polaris()
        self.state = {'version': 1, 'databases': {}}

    def provision(self, db='sales', persist=lambda value: None):
        M.provision(self.api, db, self.state, persist)

    def test_first_repeat_and_isolated_database_contract(self):
        for db in M.DATABASES:
            self.provision(db)
        writes = copy.deepcopy(self.api.writes)
        for db in M.DATABASES:
            self.provision(db)
        self.assertEqual(self.api.writes, writes)
        self.assertEqual(len(self.state['databases']), 2)
        for db, (_, realm, catalog) in M.DATABASES.items():
            self.assertEqual(self.api.namespaces['/api/catalog/v1/' + catalog + '/namespaces'],
                             [[name] for name in ('raw', 'sandbox', 'marts', 'staging', 'smoke')])
            self.assertEqual(set(self.state['databases'][db]), {'trino', 'iceberg-bootstrap'})
            self.assertEqual(self.state['databases'][db]['trino']['realm'], realm)
            for consumer in ('trino', 'iceberg_bootstrap'):
                prefix = 'shirokuma_' + db + '_' + consumer
                memberships = self.api.roles[M.MANAGEMENT + '/principals/' + prefix + '/principal-roles']
                self.assertEqual(memberships, [{'name': prefix + '_principal'}])
                catalog_roles = self.api.roles[M.MANAGEMENT + '/principal-roles/' + prefix + '_principal/catalog-roles/' + catalog]
                self.assertEqual(catalog_roles, [{'name': prefix + '_content'}])

    def test_bootstrap_grants_only_smoke_and_catalog_read(self):
        grants = M.expected_grants('iceberg-bootstrap')
        self.assertEqual(grants[0], {'type': 'catalog', 'privilege': 'CATALOG_READ_PROPERTIES'})
        self.assertTrue(all(g['type'] == 'namespace' and g['namespace'] == ['smoke'] for g in grants[1:]))
        self.assertFalse(any('DROP' in g['privilege'] or g['privilege'] in ('NAMESPACE_CREATE', 'CATALOG_MANAGE_ACCESS') for g in grants))
        self.assertTrue({'VIEW_CREATE', 'VIEW_DROP', 'NAMESPACE_CREATE', 'TABLE_FULL_METADATA'} <= set(M.TRINO))

    def test_partial_credential_persistence_precedes_next_call(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'credentials.json'
            original = self.api
            def request(method, endpoint, body=None):
                if endpoint == M.MANAGEMENT + '/principal-roles/shirokuma_sales_trino_principal':
                    saved = json.loads(path.read_text())
                    self.assertEqual(saved['databases']['sales']['trino']['client_secret'], 'private-secret')
                    self.assertEqual(saved['databases']['sales']['trino']['principal_name'], 'shirokuma_sales_trino')
                    raise M.Failure('later failure')
                return original(method, endpoint, body)
            with self.assertRaises(M.Failure):
                M.provision(request, 'sales', self.state, lambda value: M.private_save(path, value))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(M.load_credentials(path), self.state)
            # Recovery reuses the issued principal, completing the interrupted work.
            self.provision()

    def test_missing_credentials_and_client_mismatch_refused(self):
        self.provision()
        for saved in (None, {'client_id': 'wrong', 'client_secret': 'private-secret', 'realm': 'SALES'}):
            self.state['databases']['sales']['trino'] = saved
            before = len(self.api.writes)
            with self.assertRaises(M.Failure):
                self.provision()
            self.assertEqual(len(self.api.writes), before)

    def test_legacy_credentials_without_principal_name_need_no_rewrite(self):
        for db in M.DATABASES:
            self.provision(db)
            for saved in self.state['databases'][db].values():
                saved.pop('principal_name')
        before = copy.deepcopy(self.state)
        writes = copy.deepcopy(self.api.writes)
        def unexpected_persist(value):
            self.fail('legacy credentials must not require rewriting')
        for db in M.DATABASES:
            self.provision(db, persist=unexpected_persist)
        self.assertEqual(self.state, before)
        self.assertEqual(self.api.writes, writes)

    def test_operator_promoted_g2_reuses_stable_roles_after_base_deleted(self):
        for db in M.DATABASES:
            self.provision(db)
            for consumer, saved in self.state['databases'][db].items():
                base = saved['principal_name']
                active = base + '__g2'
                path = M.MANAGEMENT + '/principals/'
                del self.api.entities[path + base]
                self.api.roles.pop(path + base + '/principal-roles')
                self.api.entities[path + active] = {'name': active, 'properties': copy.deepcopy(M.OWNER), 'clientId': 'id-' + active}
                self.api.roles[path + active + '/principal-roles'] = [{'name': base + '_principal'}]
                saved.update(principal_name=active, client_id='id-' + active, client_secret='new-private-secret')
            writes = copy.deepcopy(self.api.writes)
            state = copy.deepcopy(self.state)
            calls = []
            def request(method, path, body=None):
                calls.append((method, path))
                return self.api(method, path, body)
            for _ in range(2):
                M.provision(request, db, self.state, lambda value: self.fail('must reuse saved g2 credentials'))
            self.assertEqual(self.api.writes, writes)
            self.assertEqual(self.state, state)
            for consumer, saved in self.state['databases'][db].items():
                path = M.MANAGEMENT + '/principals/' + saved['principal_name']
                self.assertIn(('GET', path), calls)
                self.assertIn(('GET', path + '/principal-roles'), calls)
                self.assertNotIn(('GET', path.removesuffix('__g2')), calls)

    def test_missing_active_g2_never_falls_back_to_existing_base(self):
        self.provision()
        self.state['databases']['sales']['trino']['principal_name'] += '__g2'
        writes = copy.deepcopy(self.api.writes)
        with self.assertRaisesRegex(M.Failure, 'saved credentials exist but principal is missing'):
            self.provision()
        self.assertEqual(self.api.writes, writes)

    def test_active_g2_client_identity_mismatch_refused(self):
        self.provision()
        saved = self.state['databases']['sales']['trino']
        active = saved['principal_name'] + '__g2'
        saved['principal_name'] = active
        self.api.entities[M.MANAGEMENT + '/principals/' + active] = {
            'name': active, 'properties': copy.deepcopy(M.OWNER), 'clientId': 'different-client-id'}
        writes = copy.deepcopy(self.api.writes)
        with self.assertRaisesRegex(M.Failure, 'saved credentials do not match dedicated principal'):
            self.provision()
        self.assertEqual(self.api.writes, writes)

    def test_invalid_saved_active_names_refused(self):
        self.provision()
        base = 'shirokuma_sales_trino'
        for name in (None, 2, False, [], {}, '', 'shirokuma_finance_trino__g2',
                     'shirokuma_sales_iceberg_bootstrap__g2', base + '__g0', base + '__g01',
                     base + '__g-1', base + '__g2/roles', base + '__g2..',
                     base + '__g2\n', base + '__g٢', base + '_g2', base + '__other'):
            with self.subTest(name=name):
                self.state['databases']['sales']['trino']['principal_name'] = name
                writes = copy.deepcopy(self.api.writes)
                with self.assertRaisesRegex(M.Failure, '^invalid saved consumer principal name$'):
                    self.provision()
                self.assertEqual(self.api.writes, writes)

    def test_bootstrap_foreign_namespace_or_drop_grant_refused(self):
        for grant in ({'type': 'namespace', 'namespace': ['raw'], 'privilege': 'TABLE_READ_DATA'},
                      {'type': 'namespace', 'namespace': ['smoke'], 'privilege': 'TABLE_DROP'}):
            self.setUp()
            self.provision()
            path = M.MANAGEMENT + '/catalogs/shirokuma_l1/catalog-roles/shirokuma_sales_iceberg_bootstrap_content/grants'
            self.api.grants[path].append(grant)
            before = len(self.api.writes)
            with self.assertRaisesRegex(M.Failure, 'unexpected consumer grant'):
                self.provision()
            self.assertEqual(len(self.api.writes), before)

    def test_credentials_output_is_required(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            M.main([])

    def test_forbidden_grants_memberships_ownership_and_storage_refused(self):
        for change in ('grant', 'principal', 'catalog', 'owner', 'storage'):
            with self.subTest(change=change):
                self.setUp()
                self.provision()
                prefix = M.MANAGEMENT
                principal = 'shirokuma_sales_trino'
                if change == 'grant':
                    next(iter(self.api.grants.values())).append({'type': 'catalog', 'privilege': 'CATALOG_MANAGE_ACCESS'})
                elif change == 'principal':
                    self.api.roles[prefix + '/principals/' + principal + '/principal-roles'].append({'name': 'service_admin'})
                elif change == 'catalog':
                    self.api.entities[prefix + '/catalogs/foreign'] = {'name': 'foreign'}
                    self.api.roles[prefix + '/principal-roles/' + principal + '_principal/catalog-roles/foreign'] = [{'name': 'foreign'}]
                elif change == 'owner':
                    self.api.entities[prefix + '/principals/' + principal]['properties'] = {}
                else:
                    self.api.entities[prefix + '/catalogs/shirokuma_l1']['storageConfigInfo']['allowedLocations'].append('s3://shirokuma-lakehouse')
                before = len(self.api.writes)
                with self.assertRaises(M.Failure):
                    self.provision()
                self.assertEqual(len(self.api.writes), before)

    def test_failed_private_write_preserves_previous_complete_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'credentials.json'
            M.private_save(path, self.state)
            previous = path.read_bytes()
            def interrupted(value, out, **kwargs):
                out.write('{"partial":')
                raise OSError('interrupted write')
            with patch.object(M.json, 'dump', side_effect=interrupted), self.assertRaises(OSError):
                M.private_save(path, {'new': 'value'})
            self.assertEqual(path.read_bytes(), previous)
            self.assertEqual(M.load_credentials(path), self.state)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertFalse(path.with_name(path.name + '.tmp').exists())

    def test_stdout_and_errors_never_expose_secret_material(self):
        for failure in (None, M.Failure('unexpected consumer grant'), M.APIError(403),
                        RuntimeError('private-secret token secret-body')):
            with tempfile.TemporaryDirectory() as directory:
                self.setUp()
                stdout, stderr = io.StringIO(), io.StringIO()
                @contextlib.contextmanager
                def operator(service, realm):
                    if failure is not None:
                        raise failure
                    yield self.api
                with patch.object(M, 'operator_api', operator), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    self.assertEqual(M.main(['--credentials-out', str(Path(directory) / 'credentials.json')]), int(failure is not None))
                if isinstance(failure, M.Failure):
                    self.assertIn(str(failure), stderr.getvalue())
                elif failure is not None:
                    self.assertIn('output suppressed', stderr.getvalue())
                self.assertNotIn('private-secret', stdout.getvalue() + stderr.getvalue())
                self.assertNotIn('secret-body', stdout.getvalue() + stderr.getvalue())
        error = urllib.error.HTTPError('http://local', 403, 'private-secret', {}, io.BytesIO(b'secret-body'))
        with patch.object(M.urllib.request, 'urlopen', side_effect=error), self.assertRaisesRegex(M.APIError, '^Polaris API returned HTTP 403$'):
            M.http_json('http://local', {}, 'GET', '/catalogs')


if __name__ == '__main__':
    unittest.main()
