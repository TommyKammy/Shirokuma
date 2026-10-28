import copy
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import database_security as security
import trino_auth


def manifest():
    return {'version': 2, 'platform_admin': {'username': 'shirokuma_admin', 'password': 'admin-secret'},
        'databases': {key: {'sql_catalog': catalog, 'generation_id': 'generation-' + key,
            'roles': {role: {'username': trino_auth.scoped_username(key, role), 'password': key + role}
                for role in ('bi_viewer', 'data_engineer')}}
            for key, catalog in (('opaque/sales', 'sales'), ('opaque/finance', 'finance'))}}


class DatabaseSecurityTests(unittest.TestCase):
    def test_scoped_admin_acl_has_registered_own_schemas_without_global_powers(self):
        config = manifest()
        original_rules = security.render_file_acl(config)
        ordinary = [entry['username'] for db in config['databases'].values() for entry in db['roles'].values()]
        for key, db in config['databases'].items():
            db['roles']['admin'] = dict(username=trino_auth.scoped_username(key, 'admin'), password=key+'admin')
        public = security.public_identities(config)
        self.assertEqual(public['version'], 2)
        rules = security.render_file_acl(config)
        for user in ordinary:
            for section in rules:
                self.assertEqual(
                    [r for r in rules[section] if re.fullmatch(r.get('user', '.*'), user)],
                    [r for r in original_rules[section] if re.fullmatch(r.get('user', '.*'), user)],
                    (section, user))
        def match(section, user, catalog='', schema=''):
            return next(r for r in rules[section] if all(re.fullmatch(r.get(k, '.*'), v)
                for k, v in (('user', user), ('catalog', catalog), ('schema', schema))))
        for db in config['databases'].values():
            user, catalog = db['roles']['admin']['username'], db['sql_catalog']
            for schema in ('raw', 'marts', 'staging', 'sandbox'):
                self.assertIn('OWNERSHIP', match('tables', user, catalog, schema)['privileges'])
                self.assertTrue(match('schemas', user, catalog, schema)['owner'])
                for other in ('finance' if catalog == 'sales' else 'sales', 'system'):
                    self.assertEqual(match('tables', user, other, schema)['privileges'], [])
            self.assertEqual(match('tables', user, catalog, 'information_schema')['privileges'], ['SELECT'])
            self.assertFalse(match('schemas', user, catalog, 'information_schema')['owner'])
            self.assertEqual(match('queries', user)['allow'], ['execute'])
            self.assertEqual(match('system_information', user)['allow'], [])
            self.assertFalse(match('system_session_properties', user)['allow'])
            self.assertEqual(match('procedures', user, catalog, 'raw')['privileges'], [])
        policy = security.render_opa_policy(config)
        self.assertIn('not scoped_admin_catalogs[input.context.identity.user]', policy)
        self.assertIn('scoped_admin_catalogs := ', policy)
        mapping = next(line.split(' := ', 1)[1] for line in policy.splitlines()
            if line.startswith('scoped_admin_catalogs := '))
        self.assertEqual(json.loads(mapping), {db['roles']['admin']['username']: db['sql_catalog']
            for db in config['databases'].values()})
        for user in ordinary:
            self.assertNotIn(user, json.loads(mapping))
        table_rule = next(rule for rule in policy.split('allow if {')
            if '"DropTable"' in rule)
        self.assertIn('"UpdateTableColumns"', table_rule)
        self.assertEqual(policy.count('"UpdateTableColumns"'), 1)
        self.assertNotIn('"DeleteFromTable"', policy)
        self.assertIn('catalog := scoped_admin_catalogs[input.context.identity.user]', table_rule)
        self.assertIn('input.action.resource.table.catalogName == catalog', table_rule)
        self.assertIn('input.action.resource.table.schemaName in {"raw", "sandbox", "staging", "marts"}', table_rule)
        self.assertIn('is_string(input.action.resource.table.tableName)', table_rule)
        self.assertIn('input.action.resource.table.tableName != ""', table_rule)
        self.assertNotIn('admin-secret', policy)

    @unittest.skipUnless(shutil.which('opa'), 'OPA binary unavailable')
    def test_scoped_admin_opa_evaluates_catalog_schema_and_system_boundaries(self):
        config = manifest()
        for key, db in config['databases'].items():
            db['roles']['admin'] = dict(username=trino_auth.scoped_username(key, 'admin'), password=key+'admin')
        user = config['databases']['opaque/sales']['roles']['admin']['username']
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'guard.rego'
            path.write_text(security.render_opa_policy(config))
            for operation in ('SelectFromColumns', 'CreateTable', 'DropTable', 'InsertIntoTable', 'UpdateTableColumns', 'ExecuteTableProcedure', 'ViewQuery', 'SetSystemSessionProperty'):
                for catalog, schema in (('sales', 'marts'), ('sales', 'raw'), ('sales', 'sandbox'), ('sales', 'staging'), ('finance', 'raw'), ('system', 'runtime'), ('sales', 'information_schema')):
                    request = dict(context=dict(identity=dict(user=user)), action=dict(operation=operation,
                        resource=dict(table=dict(catalogName=catalog, schemaName=schema, tableName='t'))))
                    run = subprocess.run([shutil.which('opa'), 'eval', '--stdin-input', '--data', str(path),
                        '--format', 'raw', 'data.shirokuma.allow'], input=json.dumps(request), text=True, capture_output=True)
                    self.assertEqual(run.returncode, 0, run.stderr)
                    allowed = catalog == 'sales' and (
                        operation in ('SelectFromColumns', 'CreateTable', 'DropTable', 'InsertIntoTable', 'UpdateTableColumns', 'ExecuteTableProcedure') and schema in ('raw', 'sandbox', 'staging', 'marts') or
                        operation == 'SelectFromColumns' and schema == 'information_schema')
                    self.assertEqual(run.stdout.strip(), str(allowed).lower(), (operation, catalog, schema))
            for schema in ('raw', 'sandbox', 'staging', 'marts'):
                for operation in ('DropTable', 'UpdateTableColumns'):
                    for name in ({}, {'tableName': ''}, {'tableName': None}, {'tableName': 1},
                            {'tableName': True}, {'tableName': []}, {'tableName': {}}):
                        request = dict(context=dict(identity=dict(user=user)), action=dict(operation=operation,
                            resource=dict(table=dict(catalogName='sales', schemaName=schema, **name))))
                        run = subprocess.run([shutil.which('opa'), 'eval', '--stdin-input', '--data', str(path),
                            '--format', 'raw', 'data.shirokuma.allow'], input=json.dumps(request), text=True, capture_output=True)
                        self.assertEqual(run.returncode, 0, run.stderr)
                        self.assertEqual(run.stdout.strip(), 'false', (operation, schema, name))

    def test_public_identity_renderer_matches_existing_private_api(self):
        private = manifest()
        public = security.public_identities(private)
        self.assertEqual(public['version'], 1)
        self.assertEqual(public['platform_admin'], 'shirokuma_admin')
        self.assertNotIn('password', json.dumps(public))
        self.assertEqual(security.render_file_acl(private), security.render_file_acl_identities(public))
        self.assertEqual(security.render_opa_policy(private), security.render_opa_policy_identities(public))
        for mutate in (
            lambda v: v.update(platform_admin='shirokuma_admin_evil'),
            lambda v: v['databases']['opaque/sales']['roles'].update(bi_viewer='shirokuma_admin'),
            lambda v: v['databases']['opaque/sales'].update(password='private-marker'),
        ):
            value = copy.deepcopy(public)
            mutate(value)
            with self.assertRaises(ValueError) as failure:
                security.render_file_acl_identities(value)
            self.assertNotIn('private-marker', str(failure.exception))

    def test_file_acl_exact_scoping_and_privileges(self):
        config = manifest()
        rules = security.render_file_acl(config)
        def privileges(user, catalog, schema, table='orders'):
            for rule in rules['tables']:
                if all(re.fullmatch(rule.get(key, '.*'), value) for key, value in
                    [('user', user), ('catalog', catalog), ('schema', schema), ('table', table)]):
                    return set(rule['privileges'])
            return set()
        for database in config['databases'].values():
            viewer = database['roles']['bi_viewer']['username']
            engineer = database['roles']['data_engineer']['username']
            catalog = database['sql_catalog']
            self.assertEqual(privileges(viewer, catalog, 'marts'), {'SELECT'})
            self.assertEqual(privileges(viewer, catalog, 'marts', 'orders$history'), {'SELECT'})
            self.assertEqual(privileges(viewer, 'system', 'runtime', 'queries'), set())
            query_rule = next(rule for rule in rules['queries'] if re.fullmatch(rule.get('user', '.*'), viewer))
            self.assertEqual(query_rule['allow'], ['execute'])
            system_rule = next(rule for rule in rules['catalogs']
                if re.fullmatch(rule.get('user', '.*'), viewer)
                and re.fullmatch(rule.get('catalog', '.*'), 'system'))
            self.assertEqual(system_rule['allow'], 'none')
            self.assertEqual(privileges(engineer, catalog, 'marts'), {'SELECT'})
            for schema in ('raw', 'sandbox', 'staging'):
                self.assertEqual(privileges(viewer, catalog, schema), set())
            for schema in ('raw', 'sandbox'):
                # Trino 483 CREATE TABLE uses table OWNERSHIP, not schema ownership.
                self.assertEqual(privileges(engineer, catalog, schema),
                    {'SELECT', 'INSERT', 'DELETE', 'UPDATE', 'OWNERSHIP'})
            for schema in ('staging', 'marts'):
                self.assertEqual(privileges(engineer, catalog, schema), {'SELECT'})
            for user in (viewer, engineer):
                for other in ('finance' if catalog == 'sales' else 'sales', 'system'):
                    for schema in ('raw', 'sandbox', 'staging', 'marts'):
                        self.assertEqual(privileges(user, other, schema), set())
                    self.assertEqual(privileges(user, other, 'marts', 'orders$history'), set())
                schema_rule = next(r for r in rules['schemas'] if re.fullmatch(r.get('user', '.*'), user))
                self.assertFalse(schema_rule['owner'])
        self.assertEqual(privileges('shirokuma_admin_evil', 'sales', 'raw'), set())
        self.assertEqual(rules['impersonation'], [])
        self.assertEqual(rules['catalogs'][-1], {'allow': 'none'})

    def test_admin_auxiliary_privileges_are_exact_and_catalog_scoped(self):
        config = manifest()
        rules = security.render_file_acl(config)
        ordinary = [entry['username'] for db in config['databases'].values() for entry in db['roles'].values()]
        for section, expected in (
                ('functions', {'EXECUTE', 'GRANT_EXECUTE', 'OWNERSHIP'}),
                ('procedures', {'EXECUTE', 'GRANT_EXECUTE'}),
                ('catalog_session_properties', True)):
            for user in ['shirokuma_admin', 'shirokuma_admin_evil', 'unknown'] + ordinary:
                for catalog in ('sales', 'finance', 'unregistered', 'system'):
                    matched = next((rule for rule in rules[section]
                        if re.fullmatch(rule.get('user', '.*'), user)
                        and re.fullmatch(rule.get('catalog', '.*'), catalog)), {})
                    actual = (matched.get('allow', False) if section == 'catalog_session_properties'
                        else set(matched.get('privileges', [])))
                    allowed = user == 'shirokuma_admin' and catalog in ('sales', 'finance')
                    self.assertEqual(actual, expected if allowed else (False if section == 'catalog_session_properties' else set()),
                        (section, user, catalog))
        for section, expected in (('system_information', ['read', 'write']), ('system_session_properties', True)):
            for user in ['shirokuma_admin', 'shirokuma_admin_evil', 'unknown'] + ordinary:
                matched = next((rule for rule in rules[section] if re.fullmatch(rule.get('user', '.*'), user)), {})
                self.assertEqual(matched.get('allow', [] if section == 'system_information' else False),
                    expected if user == 'shirokuma_admin' else ([] if section == 'system_information' else False))
        self.assertEqual(rules['impersonation'], [])
        self.assertEqual(rules['authorization'], [])

    def test_opa_exact_identity_and_registered_table_procedure_gate(self):
        config = manifest()
        policy = security.render_opa_policy(config)
        self.assertIn('default allow := false', policy)
        self.assertIn('is_string(input.action.operation)', policy)
        self.assertIn('input.action.operation != ""', policy)
        self.assertIn('input.action.operation != "ExecuteTableProcedure"', policy)
        self.assertIn('input.context.identity.user == "shirokuma_admin"', policy)
        self.assertIn('registered_catalogs[input.action.resource.table.catalogName]', policy)
        engineer_catalogs = {db['roles']['data_engineer']['username']: db['sql_catalog']
            for db in config['databases'].values()}
        mapping = next(line.split(' := ', 1)[1] for line in policy.splitlines() if line.startswith('engineer_catalogs := '))
        self.assertEqual(json.loads(mapping), engineer_catalogs)
        self.assertIn('engineer_catalogs[input.context.identity.user] == input.action.resource.table.catalogName', policy)
        self.assertIn('input.action.resource.table.schemaName in {"raw", "sandbox"}', policy)
        self.assertNotIn('startswith', policy)
        self.assertNotIn('regex.match', policy)
        for database in config['databases'].values():
            for credential in database['roles'].values():
                self.assertIn(json.dumps(credential['username']), policy)
        self.assertNotIn('admin-secret', policy)

    @unittest.skipUnless(shutil.which('opa'), 'OPA binary unavailable; renderer assertions run separately')
    def test_opa_evaluates_exact_users_and_table_procedures(self):
        config = manifest()
        users = [entry['username'] for db in config['databases'].values() for entry in db['roles'].values()]
        engineers = {db['roles']['data_engineer']['username']: db['sql_catalog'] for db in config['databases'].values()}
        with tempfile.TemporaryDirectory() as temporary:
            policy = Path(temporary) / 'guard.rego'
            policy.write_text(security.render_opa_policy(config))
            for user in users + ['shirokuma_admin', 'shirokuma_admin_evil', 'unknown']:
                for catalog in ('sales', 'finance', 'unregistered'):
                    for schema in ('raw', 'sandbox', 'staging', 'marts'):
                        for operation in ('SelectFromColumns', 'ExecuteTableProcedure'):
                            request = dict(context=dict(identity=dict(user=user)), action=dict(operation=operation,
                                resource=dict(table=dict(catalogName=catalog, schemaName=schema, tableName='t'))))
                            result = subprocess.run([shutil.which('opa'), 'eval', '--stdin-input', '--data', str(policy),
                                '--format', 'raw', 'data.shirokuma.allow'], input=json.dumps(request), text=True, capture_output=True)
                            self.assertEqual(result.returncode, 0, result.stderr)
                            expected = (user in users or user == 'shirokuma_admin') if operation != 'ExecuteTableProcedure' else (
                                (user == 'shirokuma_admin' and catalog in ('sales', 'finance')) or
                                (user in engineers and engineers[user] == catalog and schema in ('raw', 'sandbox')))
                            self.assertEqual(result.stdout.strip(), str(expected).lower(), (user, catalog, schema, operation))
            malformed = [{}, {'action': {}},
                *[dict(context=dict(identity=dict(user='shirokuma_admin')), action=dict(operation=operation))
                  for operation in (None, {}, [], '', 1, True, 'ExecuteTableProcedure')],
                dict(context=dict(identity=dict(user='shirokuma_admin')), action=dict(operation='ExecuteTableProcedure',
                    resource=dict(table=dict(catalogName='sales'))))]
            for request in malformed:
                result = subprocess.run([shutil.which('opa'), 'eval', '--stdin-input', '--data', str(policy),
                    '--format', 'raw', 'data.shirokuma.allow'], input=json.dumps(request), text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), 'false', request)

    def test_shared_manifest_validation(self):
        config = manifest()
        invalid = [dict(config, version=True), dict(config, version=1), dict(config, databases={})]
        for mutate in (
            lambda c: c['databases']['opaque/sales']['roles'].update(admin=c['platform_admin']),
            lambda c: c['databases']['opaque/finance'].update(sql_catalog='sales'),
            lambda c: c['databases']['opaque/sales']['roles']['bi_viewer'].update(username='shirokuma_admin'),
            lambda c: c['databases']['opaque/sales'].update(generation_id=''),
            lambda c: c['databases']['opaque/sales']['roles'].pop('bi_viewer'),
            lambda c: c['databases']['opaque/sales']['roles']['bi_viewer'].update(password=''),
            lambda c: c['databases']['opaque/sales']['roles']['bi_viewer'].update(password=5),
            lambda c: c['databases']['opaque/sales'].update(sql_catalog='system'),
            lambda c: c['databases']['opaque/sales']['roles']['bi_viewer'].update(password='admin-secret')):
            changed = copy.deepcopy(config)
            mutate(changed)
            invalid.append(changed)
        for value in invalid:
            for render in (security.render_file_acl, security.render_opa_policy):
                with self.assertRaises((ValueError, RuntimeError)) as failure:
                    render(value)
                self.assertNotIn('admin-secret', str(failure.exception))
