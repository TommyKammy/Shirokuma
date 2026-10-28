"""Offline contracts for permanent Polaris 1.8 adoption; no private inputs."""
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SERVER = "docker.io/apache/polaris@sha256:74e6416c75f24563fc62f4ed52699d4fb872741b6a3e0af77fdc551b4ee72fce"
ADMIN = "docker.io/apache/polaris-admin-tool@sha256:9f85ebe1fdfa9eb1214c4d5d343de29f76fef3dda7b4eb11c6767acc7e6c4fa4"


def command(*args, text=None):
    return subprocess.run(["rtk", "proxy", *args], input=text, text=True,
                          capture_output=True, check=True, cwd=ROOT,
                          env={**os.environ, "KUBECONFIG": os.devnull}).stdout


def parse(text):
    output = command("kubectl", "patch", "--local=true", "--type=merge",
                     "-p", "{}", "-f", "-", "-o", "json", text=text)
    decoder = json.JSONDecoder()
    result = []
    while output.strip():
        item, end = decoder.raw_decode(output.lstrip())
        result.append(item)
        output = output.lstrip()[end:]
    return result


def read(path):
    return (ROOT / path).read_text()


class PolarisAdoptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.servers = parse(command("kubectl", "kustomize", "deploy/gitops/catalog/server"))
        cls.bootstrap = parse(command("kubectl", "kustomize", "deploy/catalog/bootstrap"))
        cls.flux = parse(command("kubectl", "kustomize", "deploy/gitops/clusters/local-lite"))

    def test_independent_servers_and_service_routing(self):
        deployments = {r['metadata']['name']: r for r in self.servers if r['kind'] == 'Deployment'}
        self.assertEqual(set(deployments), {'polaris', 'polaris-finance'})
        for name, realm, slug in [('polaris', 'SALES', 'sales'), ('polaris-finance', 'FINANCE', 'finance')]:
            pod = deployments[name]['spec']['template']
            spec = pod['spec']
            container = spec['containers'][0]
            env = {e['name']: e for e in container['env']}
            self.assertEqual(container['image'], SERVER)
            self.assertNotIn('command', container)
            self.assertEqual(env['POLARIS_REALM_CONTEXT_REALMS']['value'], realm)
            self.assertTrue(env['QUARKUS_DATASOURCE_JDBC_URL']['value'].endswith('/polaris?currentSchema=polaris_schema'))
            for key in ['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_REGION']:
                self.assertEqual(env[key]['valueFrom']['secretKeyRef'], {'name': f'seaweedfs-s3-{slug}-credentials', 'key': key})
            self.assertEqual(env['POLARIS_AUTHENTICATION_TOKEN_BROKER_TYPE']['value'], 'rsa-key-pair')
            for kind in ['PUBLIC', 'PRIVATE']:
                self.assertEqual(env[f'POLARIS_AUTHENTICATION_TOKEN_BROKER_RSA_KEY_PAIR_{kind}_KEY_FILE']['value'], f'/etc/polaris/jwt/{kind.lower()}.pem')
            self.assertEqual(env['QUARKUS_LOG_FILE_ENABLED']['value'], 'false')
            self.assertEqual(env['QUARKUS_LOG_CONSOLE_ENABLED']['value'], 'true')
            self.assertEqual(spec['securityContext']['fsGroup'], 10001)
            self.assertEqual(spec['securityContext']['runAsUser'], 10000)
            jwt = next(v for v in spec['volumes'] if v['name'] == 'jwt')
            self.assertEqual(jwt['secret']['secretName'], 'polaris-jwt-signing-key' if slug == 'sales' else 'polaris-finance-jwt-signing-key')
            self.assertEqual(jwt['secret']['defaultMode'], 0o440)
            self.assertTrue(next(m for m in container['volumeMounts'] if m['name'] == 'jwt')['readOnly'])
            self.assertNotIn('root-credentials', json.dumps(spec))
            self.assertNotIn('polaris-bootstrap-credentials', json.dumps(spec))
            other = 'FINANCE' if realm == 'SALES' else 'SALES'
            self.assertNotIn(f'POLARIS_{other}_JWT_KEY_GENERATION', json.dumps(pod))
            self.assertEqual(pod['metadata']['annotations']['shirokuma.dev/polaris-jwt-key-generation'], 'generation-${POLARIS_' + realm + '_JWT_KEY_GENERATION}')
            service = next(r for r in self.servers if r['kind'] == 'Service' and r['metadata']['name'] == name)
            selected = [n for n, d in deployments.items() if all(d['spec']['template']['metadata']['labels'].get(k) == v for k, v in service['spec']['selector'].items())]
            self.assertEqual(selected, [name])
        policy = next(r for r in self.servers if r['kind'] == 'NetworkPolicy')
        for d in deployments.values():
            self.assertTrue(all(d['spec']['template']['metadata']['labels'].get(k) == v for k, v in policy['spec']['podSelector']['matchLabels'].items()))

    def test_schema_init_and_combined_admin_contract(self):
        job = next(r for r in self.bootstrap if r['kind'] == 'Job')
        spec = job['spec']['template']['spec']
        admin = spec['containers'][0]
        self.assertEqual(admin['image'], ADMIN)
        self.assertNotIn('command', admin)
        self.assertEqual(admin['args'], ['bootstrap', '--credentials-file=/var/run/secrets/shirokuma/polaris/credentials.json'])
        env = {e['name']: e for e in admin['env']}
        self.assertEqual(env['POLARIS_REALM_CONTEXT_REALMS']['value'], 'SALES,FINANCE')
        self.assertTrue(env['QUARKUS_DATASOURCE_JDBC_URL']['value'].endswith('/polaris?currentSchema=polaris_schema'))
        self.assertEqual(next(v for v in spec['volumes'] if v['name'] == 'credentials')['secret']['secretName'], 'polaris-bootstrap-credentials')
        init, = spec['initContainers']
        pg = parse(read('deploy/gitops/catalog/database/statefulset.yaml'))[0]
        self.assertEqual(init['image'], pg['spec']['template']['spec']['containers'][0]['image'])
        self.assertEqual(init['command'], ['psql'])
        self.assertIn('ON_ERROR_STOP=1', init['args'])
        self.assertEqual(init['args'][-1], 'CREATE SCHEMA IF NOT EXISTS polaris_schema;')
        for key in ['PGPASSWORD', 'PGUSER', 'PGDATABASE']:
            self.assertEqual(next(e for e in init['env'] if e['name'] == key)['valueFrom']['secretKeyRef']['name'], 'polaris-postgresql-credentials')
        self.assertNotIn('JWT_KEY_GENERATION', json.dumps(job))

    def test_generation_propagates_only_to_catalog(self):
        flux = {r['metadata']['name']: r for r in self.flux if r['kind'] == 'Kustomization'}
        generation = next(r for r in parse(read('deploy/gitops/clusters/local-lite/polaris-runtime-generation.yaml'))
                          if r['kind'] == 'ConfigMap' and r['metadata']['name'] == 'polaris-runtime-generation')['data']
        for realm in ['SALES', 'FINANCE']:
            key = f'POLARIS_{realm}_JWT_KEY_GENERATION'
            self.assertEqual(flux['shirokuma-catalog']['spec']['postBuild']['substitute'][key], generation[key])
            for name in ['shirokuma-catalog-database', 'shirokuma-catalog-bootstrap']:
                self.assertNotIn(key, json.dumps(flux[name]))
        self.assertEqual({r['name'] for r in flux['shirokuma-catalog']['spec']['healthChecks']}, {'polaris', 'polaris-finance'})
        for name in ['shirokuma-catalog', 'shirokuma-catalog-database', 'shirokuma-catalog-bootstrap']:
            self.assertEqual(flux[name]['spec']['postBuild']['substitute']['POLARIS_CREDENTIAL_GENERATION'],
                             generation['POLARIS_CREDENTIAL_GENERATION'])

    def test_distinct_jwt_generations_do_not_change_database_or_admin(self):
        # Render the checked-in replacement rules with non-secret, distinct tokens.
        resources = json.loads(json.dumps(self.flux))
        generation = next(r for r in resources if r['kind'] == 'ConfigMap' and r['metadata']['name'] == 'polaris-runtime-generation')
        generation['data']['POLARIS_SALES_JWT_KEY_GENERATION'] = '7'
        generation['data']['POLARIS_FINANCE_JWT_KEY_GENERATION'] = '12'
        config = read('deploy/gitops/clusters/local-lite/kustomization.yaml')
        config = config.split('resources:')[0] + 'resources:\n  - fixture.json\nreplacements:' + config.split('replacements:', 1)[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'fixture.json').write_text(json.dumps({'apiVersion': 'v1', 'kind': 'List', 'items': resources}))
            (root / 'kustomization.yaml').write_text(config)
            rendered = parse(command('kubectl', 'kustomize', str(root)))
        flux = {r['metadata']['name']: r for r in rendered if r['kind'] == 'Kustomization'}
        substitutions = flux['shirokuma-catalog']['spec']['postBuild']['substitute']
        self.assertEqual(substitutions['POLARIS_SALES_JWT_KEY_GENERATION'], '7')
        self.assertEqual(substitutions['POLARIS_FINANCE_JWT_KEY_GENERATION'], '12')
        for name in ['shirokuma-catalog-database', 'shirokuma-catalog-bootstrap']:
            self.assertEqual(flux[name], next(r for r in self.flux if r['kind'] == 'Kustomization' and r['metadata']['name'] == name))

    def test_iac_credentials_and_isolation(self):
        catalog = read('opentofu/dev/catalog.tf')
        storage = read('opentofu/dev/object-storage.tf')
        variables = read('opentofu/dev/variables.tf')
        outputs = read('opentofu/dev/outputs.tf')
        for old in ['seaweedfs_s3_application', 'polaris_root_client_secret']:
            self.assertNotIn(old, catalog + storage + variables + outputs)
        for field in ['s3_access_key', 's3_secret_key', 'root_client_secret', 'jwt_private_key_pem', 'jwt_public_key_pem']:
            self.assertRegex(variables, field + r'\s+= string')
        for name in ['polaris-root-credentials', 'polaris-finance-root-credentials', 'polaris-bootstrap-credentials', 'polaris-jwt-signing-key', 'polaris-finance-jwt-signing-key', 'polaris-trino-credentials', 'polaris-finance-trino-credentials', 'polaris-iceberg-bootstrap-credentials', 'polaris-finance-iceberg-bootstrap-credentials']:
            self.assertIn('"' + name + '"', catalog)
        self.assertIn('for_each = local.polaris_databases', catalog)
        self.assertNotIn('for_each = var.polaris_database_credentials', catalog + storage)
        self.assertEqual(catalog.count('ignore_changes = [data]'), 1)
        self.assertIn('"private.pem"', catalog)
        self.assertIn('"public.pem"', catalog)
        self.assertIn('"credentials.json" = jsonencode(local.polaris_root_entries)', catalog)
        for field, value in [('realm', 'SALES'), ('realm', 'FINANCE'),
                             ('catalog', 'shirokuma_l1'), ('catalog', 'finance'),
                             ('prefix', 'databases/sales/'), ('prefix', 'databases/finance/')]:
            self.assertRegex(catalog, field + r'\s+= "' + re.escape(value) + '"')
        self.assertIn('for action in ["Read", "List", "Write", "Tagging"]', storage)
        self.assertIn('${action}:${local.seaweedfs_s3_bucket}/${database.prefix}*', storage)
        for action in ['Read', 'List', 'Write', 'Tagging']:
            self.assertNotIn('"' + action + ':${local.seaweedfs_s3_bucket}"', storage)
        self.assertIn('actions = ["Read:shirokuma-gitops", "List:shirokuma-gitops"]', storage)
        gitops = storage.split('resource "kubernetes_secret_v1" "local_gitops_s3_credentials"')[1].split('\nresource ')[0]
        self.assertRegex(gitops, r'name\s*= local\.generation_mode \? local\.generation_shared\.gitops_source_secret\.name : "shirokuma-local-source"')
        self.assertRegex(gitops, r'namespace\s*= local\.generation_mode \? local\.generation_shared\.gitops_source_secret\.namespace : "flux-system"')
        self.assertRegex(gitops, r'data\s*= local\.generation_mode \? local\.generation_shared_data\.gitops_source_secret : \{')
        self.assertRegex(gitops, r'accesskey\s*= var\.local_gitops_s3_credentials\.access_key')
        self.assertRegex(gitops, r'secretkey\s*= var\.local_gitops_s3_credentials\.secret_key')
        for key in ['accesskey', 'secretkey', 'AWS_REGION']:
            self.assertIn(key, storage)
        self.assertIn('length(distinct(', storage)
        self.assertIn('BEGIN PRIVATE KEY', variables)
        self.assertIn('BEGIN PUBLIC KEY', variables)
        self.assertIn('toset(keys(var.polaris_database_credentials)) == toset(["sales", "finance"])', variables)
        self.assertIn('length(trimspace(value)) > 0', variables)
        for principal in ['trino', 'bootstrap']:
            resource = 'polaris_trino_credentials' if principal == 'trino' else 'polaris_iceberg_bootstrap_credentials'
            block = catalog.split(f'resource "kubernetes_secret_v1" "{resource}"')[1].split('\nresource ')[0]
            for key in ['client_id', 'client_secret']:
                self.assertRegex(block, key + r'\s+= var.polaris_database_credentials\[each.key\]\.' + principal + '_' + key)
            self.assertRegex(block, r'realm\s+= each.value.realm')
        root = catalog.split('resource "kubernetes_secret_v1" "polaris_root_credentials"')[1].split('\nresource ')[0]
        self.assertIn('(each.value.realm) = local.polaris_root_entries[each.value.realm]', root)
        self.assertRegex(root, r'client_id\s+= local.polaris_root_client_id')
        self.assertRegex(root, r'client_secret\s+= var.polaris_database_credentials\[each.key\].root_client_secret')
        self.assertRegex(root, r'realm\s+= each.value.realm')
        self.assertNotIn('ignore_changes', root)
        for realm in ['SALES', 'FINANCE']:
            self.assertIn('local.polaris_runtime_generation.POLARIS_' + realm + '_JWT_KEY_GENERATION', catalog)

    def test_singleton_root_address_moves_only_to_sales(self):
        catalog = read('opentofu/dev/catalog.tf')
        moves = [' '.join(block.split()) for block in re.findall(r'\bmoved\s*\{([^}]+)\}', catalog)]
        self.assertEqual(moves, [
            'from = kubernetes_secret_v1.polaris_postgresql_credentials '
            'to = kubernetes_secret_v1.polaris_postgresql_credentials[0]',
            'from = kubernetes_secret_v1.polaris_root_credentials '
            'to = kubernetes_secret_v1.polaris_root_credentials["sales"]'
        ])

    def test_service_principal_inputs_are_optional_and_nullable(self):
        variables = read('opentofu/dev/variables.tf')
        for principal in ['bootstrap', 'trino']:
            for suffix in ['client_id', 'client_secret']:
                self.assertRegex(variables, principal + '_' + suffix + r'\s+= optional\(string(?:, null)?\)')
        # Required-field validation must not reject the omitted consumer fields.
        self.assertNotIn('for value in values(credentials)', variables)
        for field in ['s3_access_key', 's3_secret_key', 'root_client_secret', 'jwt_private_key_pem', 'jwt_public_key_pem']:
            self.assertIn('credentials.' + field, variables)

    def test_service_principal_validation_requires_complete_nonblank_pairs(self):
        variables = ' '.join(read('opentofu/dev/variables.tf').split())
        for principal in ['bootstrap', 'trino']:
            self.assertIn(f'[credentials.{principal}_client_id, credentials.{principal}_client_secret]', variables)
        # Nested conditionals guard nulls without relying on && short-circuiting.
        self.assertIn('pair[0] == null ? pair[1] == null : ( pair[1] == null ? false :', variables)
        self.assertIn('alltrue([for value in pair : length(trimspace(value)) > 0])', variables)

    def test_consumer_secrets_are_conditional_without_exposing_values(self):
        catalog = read('opentofu/dev/catalog.tf')
        outputs = ' '.join(read('opentofu/dev/outputs.tf').split())
        for principal, resource in [('trino', 'polaris_trino_credentials'), ('bootstrap', 'polaris_iceberg_bootstrap_credentials')]:
            block = catalog.split(f'resource "kubernetes_secret_v1" "{resource}"')[1].split('\nresource ')[0]
            flat = ' '.join(block.split())
            each = flat.split('for_each = ', 1)[1].split(' metadata {', 1)[0]
            self.assertEqual(each,
                '{ for key, database in local.polaris_databases : key => database '
                'if local.generation_mode ? var.warehouse_generation_phase == "full" : try(nonsensitive( '
                f'var.polaris_database_credentials[key].{principal}_client_id != null && '
                f'var.polaris_database_credentials[key].{principal}_client_secret != null ), false) }}')
            role = 'trino_secret' if principal == 'trino' else 'bootstrap_secret'
            self.assertRegex(flat, r'data\s*= local\.generation_mode \? local\.generation_db_data\[each.key\]\.' + role + r' : \{')
            self.assertRegex(flat, r'name\s*= each\.value\.' + role + r'\b')
            self.assertRegex(flat, r'namespace\s*= local\.generation_mode \? local\.generation_databases\[each.key\]\.' + role + r'\.namespace : kubernetes_namespace_v1\.dev\.metadata\[0\]\.name')
            self.assertIn(f'try(kubernetes_secret_v1.{resource}[key].metadata[0].name, null)', outputs)
        self.assertNotIn('warehouse_generation_secrets', outputs)
        self.assertNotRegex(outputs, r'kubernetes_secret_v1\.[^\s]+\.data\b')
        for resource in ['polaris_root_credentials', 'polaris_jwt_signing_key']:
            block = catalog.split(f'resource "kubernetes_secret_v1" "{resource}"')[1].split('\nresource ')[0]
            self.assertIn('for_each = local.polaris_databases', block)
            self.assertNotIn('nonsensitive', block)
        # Only nullable-pair presence and these exact public keysets may be
        # declassified. Balanced extraction retains nested keys(...) calls.
        public_keysets = {
            'keys(var.warehouse_generation_secrets)',
            'keys(local.generation_data["${e.ref.namespace}/${e.ref.name}@${e.ref.version}"])',
        }
        pair_presence = (r'var\.polaris_database_credentials\[key\]\.(trino|bootstrap)_client_id != null && '
                         r'var\.polaris_database_credentials\[key\]\.\1_client_secret != null')
        for call in re.finditer(r'\bnonsensitive\(', catalog):
            depth, end = 1, call.end()
            while depth and end < len(catalog):
                depth += (catalog[end] == '(') - (catalog[end] == ')')
                end += 1
            self.assertEqual(depth, 0)
            expression = ' '.join(catalog[call.end():end - 1].split())
            self.assertTrue(expression in public_keysets or re.fullmatch(pair_presence, expression),
                            'Unsafe nonsensitive expression: ' + expression)


if __name__ == '__main__':
    unittest.main()
