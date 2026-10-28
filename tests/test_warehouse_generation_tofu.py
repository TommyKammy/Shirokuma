"""Offline source contracts plus reusable synthetic material for root mock plans.

No init, plan, state, profile, provider, or cluster access is performed here.
"""
from pathlib import Path
import re
import unittest

from test_database_material import assembled_fixture, synthetic_fixture

ROOT = Path(__file__).resolve().parents[1]


def resource(source, name):
    match = re.search(r'resource "kubernetes_secret_v1" "' + name + r'" \{', source)
    if match is None:
        raise AssertionError('missing resource ' + name)
    following = re.search(r'\n(?:resource|moved|locals) ', source[match.end():])
    return source[match.start():match.end() + following.start()] if following else source[match.start():]


class GenerationTofuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = (ROOT / 'opentofu/dev/catalog.tf').read_text()
        cls.storage = (ROOT / 'opentofu/dev/object-storage.tf').read_text()
        cls.variables = (ROOT / 'opentofu/dev/variables.tf').read_text()
        cls.outputs = (ROOT / 'opentofu/dev/outputs.tf').read_text()

    def test_generic_three_database_ref_and_private_data_contracts(self):
        built = assembled_fixture()
        variables = built['tofu_vars']
        public = variables['warehouse_generation']
        private = variables['warehouse_generation_secrets']
        self.assertEqual(len(public['databases']), 3)
        for dbid, db in public['databases'].items():
            self.assertNotIn(dbid, ('sales', 'finance'))
            for role in ('root_secret', 'trino_secret', 'bootstrap_secret', 'jwt_secret', 's3_secret'):
                ref = db[role]
                secret = private['{namespace}/{name}@{version}'.format(**ref)]
                self.assertEqual(set(secret['data']), set(ref['keys']))
        for ref in public['shared'].values():
            secret = private['{namespace}/{name}@{version}'.format(**ref)]
            self.assertEqual(set(secret['data']), set(ref['keys']))
        for name, role in [('polaris_root_credentials', 'root_secret'),
                           ('polaris_jwt_signing_key', 'jwt_secret'),
                           ('polaris_trino_credentials', 'trino_secret'),
                           ('polaris_iceberg_bootstrap_credentials', 'bootstrap_secret')]:
            block = resource(self.catalog, name)
            self.assertIn('local.polaris_databases', block)
            self.assertIn('local.generation_db_data[each.key].' + role, block)
            self.assertIn('local.generation_databases[each.key].' + role + '.namespace', block)
            self.assertIn('immutable = local.generation_mode ? true : null', block)
            self.assertIn('shirokuma.dev/secret-version', block)
        s3 = resource(self.storage, 'seaweedfs_s3_database_credentials')
        self.assertIn('local.generation_db_data[each.key].s3_secret', s3)
        self.assertIn('local.generation_databases[each.key].s3_secret.namespace', s3)

    def test_shared_refs_and_exact_data_including_operator(self):
        for source, name, role in [
            (self.catalog, 'polaris_bootstrap_credentials', 'polaris_bootstrap_secret'),
            (self.storage, 'seaweedfs_s3_credentials', 'seaweedfs_server_secret'),
            (self.storage, 'local_gitops_s3_credentials', 'gitops_source_secret')]:
            block = resource(source, name)
            for field in ('name', 'namespace', 'version'):
                self.assertIn('local.generation_shared.' + role + '.' + field, block)
            self.assertIn('local.generation_shared_data.' + role, block)
            self.assertIn('immutable = local.generation_mode ? true : null', block)
        for source, name, role in [(self.catalog, 'generation_trino_auth', 'trino_auth_secret'),
                                   (self.storage, 'generation_s3_operator', 'operator_secret')]:
            block = resource(source, name)
            if role == 'trino_auth_secret':
                self.assertIn('role == "trino_auth_secret" && contains(local.generation_expected_ids,', block)
            else:
                self.assertIn('local.generation_mode ? { ' + role + ' = local.generation_shared.' + role + ' } : {}', block)
            self.assertRegex(block, r'data\s*= local\.generation_shared_data\[each.key\]')
            self.assertIn('immutable = true', block)
            self.assertRegex(block, r'namespace\s*= each\.value\.namespace')

    def test_postgresql_legacy_and_generation_lifecycles_are_exclusive(self):
        legacy = resource(self.catalog, 'polaris_postgresql_credentials')
        generated = resource(self.catalog, 'polaris_generation_postgresql_credentials')
        self.assertRegex(legacy, r'count\s*= local\.generation_mode \? 0 : 1')
        self.assertRegex(generated, r'count\s*= local\.generation_mode \? 1 : 0')
        self.assertRegex(legacy, r'name\s*= "polaris-postgresql-credentials"')
        self.assertRegex(legacy, r'namespace\s*= kubernetes_namespace_v1\.dev\.metadata\[0\]\.name')
        self.assertIn('"shirokuma.dev/polaris-credential-generation" = local.polaris_credential_generation', legacy)
        self.assertRegex(legacy, r'data\s*= \{\s*database\s*= local\.polaris_postgresql_database\s*username\s*= local\.polaris_postgresql_username\s*password\s*= var\.polaris_postgresql_password\s*\}')
        self.assertIn('ignore_changes = [data]', legacy)
        self.assertNotIn('precondition', legacy)
        self.assertNotIn('immutable', legacy)
        self.assertEqual(self.catalog.count('ignore_changes = [data]'), 1)
        self.assertNotIn('ignore_changes', generated)
        self.assertIn('immutable = true', generated)
        for field in ('name', 'namespace', 'version'):
            self.assertIn('local.generation_shared.postgres_secret.' + field, generated)
        self.assertIn('merge(local.generation_annotations, {', generated)
        self.assertRegex(generated, r'data\s*= local\.generation_shared_data\.postgres_secret')
        self.assertEqual(generated.count('precondition {'), 3)
        for integrity in ('toset(local.generation_expected_ids)', 'toset(local.generation_expected_deferred_ids)',
                          'local.generation_db_data[key].root_secret.realm == db.realm',
                          'contains(local.generation_expected_ids,'):
            self.assertIn(integrity, generated)
        moves = [' '.join(block.split()) for block in re.findall(r'\bmoved\s*\{([^}]+)\}', self.catalog)]
        self.assertIn('from = kubernetes_secret_v1.polaris_postgresql_credentials '
                      'to = kubernetes_secret_v1.polaris_postgresql_credentials[0]', moves)
        self.assertRegex(self.outputs, r'value\s*= local\.generation_mode \? kubernetes_secret_v1\.polaris_generation_postgresql_credentials\[0\]\.metadata\[0\]\.name : kubernetes_secret_v1\.polaris_postgresql_credentials\[0\]\.metadata\[0\]\.name')

    def test_validation_and_public_for_each(self):
        # The real ToFu Secrets must satisfy the normal operator's generation
        # verifier, rather than only synthetic Kubernetes response fixtures.
        from warehouse_operator import database_generation
        self.assertIn('"' + database_generation.ANNOTATION + '" = var.warehouse_generation.generation_id', self.catalog)
        self.assertIn('variable "warehouse_generation"', self.variables)
        self.assertIn('default     = null', self.variables)
        self.assertIn('variable "warehouse_generation_secrets"', self.variables)
        self.assertIn('map(object({ data = map(string) }))', self.variables)
        self.assertIn('toset(nonsensitive(keys(var.warehouse_generation_secrets))) == toset(local.generation_expected_ids)', self.catalog)
        self.assertIn('e.ref.namespace == e.namespace', self.catalog)
        self.assertIn('length(trimspace(e.ref.version)) > 0', self.catalog)
        self.assertIn('toset(e.ref.keys) == toset(e.keys)', self.catalog)
        self.assertIn('length(local.generation_ref_ids) == length(distinct(local.generation_ref_ids))', self.catalog)
        self.assertNotRegex(self.catalog + self.storage, r'for_each\s*=\s*(?:var.warehouse_generation_secrets|local.generation_data)')
        for name in ('polaris_trino_credentials', 'polaris_iceberg_bootstrap_credentials'):
            block = resource(self.catalog, name)
            self.assertIn('if local.generation_mode ? contains(local.generation_expected_ids,', block)
            self.assertNotIn('warehouse_generation_phase == "full"', block)
        self.assertIn('nonsensitive(keys(var.warehouse_generation_secrets))', self.catalog)
        self.assertIn('if contains(local.generation_supplied_ids, id)', self.catalog)
        self.assertIn('role == "trino_auth_secret" && contains(local.generation_expected_ids,', resource(self.catalog, 'generation_trino_auth'))

    def test_explicit_two_stage_ref_sets_and_resource_gates(self):
        bootstrap = assembled_fixture(3, phase='bootstrap')['tofu_vars']
        full = assembled_fixture(3)['tofu_vars']
        public = full['warehouse_generation']
        ids = lambda refs: {'{namespace}/{name}@{version}'.format(**ref) for ref in refs}
        initial_ids = ids(public['bootstrap_secret_refs'])
        deferred_ids = ids(public['deferred_secret_refs'])
        self.assertEqual(set(bootstrap['warehouse_generation_secrets']), initial_ids)
        self.assertEqual(set(full['warehouse_generation_secrets']), initial_ids | deferred_ids)
        self.assertFalse(initial_ids & deferred_ids)
        self.assertEqual(len(initial_ids), 14)
        self.assertEqual(len(deferred_ids), 7)
        self.assertIn('variable "warehouse_generation_phase"', self.variables)
        self.assertIn('contains(["bootstrap", "full"], var.warehouse_generation_phase)', self.variables)
        self.assertIn('toset(local.generation_deferred_ids) == toset(local.generation_expected_deferred_ids)', self.catalog)
        self.assertIn('toset(local.generation_bootstrap_ids) == setsubtract(toset(local.generation_ref_ids), toset(local.generation_expected_deferred_ids))', self.catalog)
        self.assertIn('jsonencode(entry.ref)', self.catalog)
        self.assertIn('jsonencode(ref)', self.catalog)
        self.assertRegex(self.catalog, r'namespace = "shirokuma-dev"')
        self.assertNotRegex(self.catalog, r'namespace\s*=\s*"dev"')
        for name in ('polaris_root_credentials', 'polaris_jwt_signing_key', 'polaris_bootstrap_credentials',
                     'polaris_generation_postgresql_credentials'):
            self.assertNotIn('for_each = local.generation_mode && var.warehouse_generation_phase', resource(self.catalog, name))
        for name in ('seaweedfs_s3_credentials', 'seaweedfs_s3_database_credentials',
                     'local_gitops_s3_credentials', 'generation_s3_operator'):
            self.assertNotIn('warehouse_generation_phase', resource(self.storage, name))

    def test_legacy_addresses_names_data_and_optional_pairs_preserved(self):
        self.assertIn('from = kubernetes_secret_v1.polaris_root_credentials\n', self.catalog)
        self.assertIn('to   = kubernetes_secret_v1.polaris_root_credentials["sales"]', self.catalog)
        for value in ('polaris-root-credentials', 'polaris-finance-root-credentials',
                      'polaris-jwt-signing-key', 'polaris-finance-jwt-signing-key',
                      'polaris-trino-credentials', 'polaris-finance-trino-credentials',
                      'polaris-iceberg-bootstrap-credentials', 'polaris-finance-iceberg-bootstrap-credentials',
                      'seaweedfs-s3-sales-credentials', 'seaweedfs-s3-finance-credentials'):
            self.assertIn('"' + value + '"', self.catalog)
        self.assertIn('} : local.legacy_polaris_databases', self.catalog)
        self.assertIn('"credentials.json" = jsonencode(local.polaris_root_entries)', self.catalog)
        self.assertIn('ignore_changes = [data]', self.catalog)
        for component in ('trino', 'bootstrap'):
            self.assertIn('var.polaris_database_credentials[key].' + component + '_client_id != null', self.catalog)
            self.assertIn('var.polaris_database_credentials[key].' + component + '_client_secret != null', self.catalog)
        self.assertIn('var.warehouse_generation != null || toset(keys(var.polaris_database_credentials)) == toset(["sales", "finance"])', self.variables)
        self.assertIn('condition = local.generation_mode ? true : length(distinct(concat([', self.storage)
        self.assertIn('))) == 4', self.storage)
        self.assertIn('actions = [for action in ["Read", "List", "Write", "Tagging"]', self.storage)

    def test_outputs_do_not_expose_private_material_and_generic_map_safe(self):
        self.assertIn('try(kubernetes_secret_v1.polaris_root_credentials["sales"].metadata[0].name, null)', self.outputs)
        self.assertIn('output "warehouse_generation"', self.outputs)
        self.assertRegex(self.outputs, r'value\s*= var\.warehouse_generation')
        self.assertNotIn('warehouse_generation_secrets', self.outputs)
        self.assertNotIn('.data', self.outputs.replace('local.polaris_databases', ''))


if __name__ == '__main__':
    unittest.main()
