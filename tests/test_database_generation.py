import copy
import json
from pathlib import Path
import sys
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/local_experiment'))
import database_generation as generation


def spec(count=2):
    result = dict(version=1, generation_id='generation-one', warehouse_id='LOCAL',
        profile=dict(id=generation.PROFILE_ID, template_fingerprint=generation.profile_fingerprint(ROOT)),
        shared_secret_refs={}, databases={}, grant_revisions=[])
    for role, keys in generation.SHARED_KEYS.items():
        namespace = 'flux-system' if role == 'gitops_source' else 'shirokuma-storage' if role in ('operator', 'seaweed_server') else 'shirokuma-dev'
        result['shared_secret_refs'][role] = dict(namespace=namespace, name=role.replace('_', '-')+'-v1', version='v1', keys=list(keys))
    for index in range(count):
        key = 'opaque_' + str(index)
        catalog = ['sales', 'finance', 'operations'][index] if index < 3 else 'database_' + str(index)
        result['databases'][key] = dict(database_revision=1, sql_catalog=catalog,
            binding=dict(desired_revision=1, polaris_service_ref='polaris-'+str(index), polaris_realm='REALM_'+str(index),
                polaris_catalog='db_'+key, storage_profile_ref='local-s3', credential_profile_ref='local-polaris',
                base_location='s3://shirokuma-lakehouse/databases/'+key+'/'),
            schemas=[dict(id=key+'_'+name, sql_name=name, purpose=name, revision=1) for name in generation.SCHEMAS],
            secret_refs={role: dict(namespace='shirokuma-dev', name=catalog+'-'+role.replace('_','-')+'-v1',
                version='v1', keys=list(keys)) for role, keys in generation.DB_KEYS.items()})
    result['shared_secret_refs']['polaris_bootstrap']['keys'] = sorted(generation.bootstrap_key(key) for key in result['databases'])
    return result


class GenerationTests(unittest.TestCase):
    def test_normal_export_upgrades_roles_and_versions_auth_material_only(self):
        import catalog_admin
        from test_warehouse_operations import OperationTests
        fixture = OperationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        old = generation.validate_spec(fixture.spec)
        new = catalog_admin.export_spec('new-generation', old)
        self.assertEqual(new['version'], 2)
        self.assertEqual(new['profile']['template_fingerprint'], generation.profile_fingerprint(version=2))
        self.assertNotEqual(new['shared_secret_refs']['trino_auth'], old['shared_secret_refs']['trino_auth'])
        for role in set(old['shared_secret_refs']) - {'trino_auth'}:
            self.assertEqual(new['shared_secret_refs'][role], old['shared_secret_refs'][role])
        for key in old['databases']:
            self.assertEqual(new['databases'][key]['secret_refs'], old['databases'][key]['secret_refs'])
        again = catalog_admin.export_spec('another-generation', new)
        self.assertEqual(again['shared_secret_refs'], new['shared_secret_refs'])
        generation.render(new)

    def test_released_v1_bytes_and_delivery_digest_are_unchanged(self):
        bundle = generation.render(spec(), ROOT)
        self.assertEqual(bundle['manifest']['delivery_digest'],
            '81823860e598f80119cf6f2c2ddc27921d2fc303629997da00c7eda3d7594afe')
        for path, digest in {
            'identity-public.json': 'dde2813b89d4cffbd91b1efa07c7594b96416544d7e59151f0698e9d3e8f8198',
            'deploy/local-experiment/trino/rules.json': '07f55b898a1c6d602201d574474cb41ee8ad8c54590addf2eade29cbaf6fb80d',
            'deploy/local-experiment/trino/opa/guard.rego': '70ddb6ba3ba18d8eefa1f8f988f81eaafd5d17acdc93761f3e926e5c8fa68045',
        }.items():
            self.assertEqual(bundle['manifest']['file_digests'][path], digest)

    def test_v2_explicit_three_role_generation_and_v1_rejects_admin(self):
        from trino_auth import scoped_username
        value = spec(3)
        value['grant_revisions'] = [dict(principal_id='db-owner', database_id='opaque_0', maximum_role='admin', revision=1)]
        with self.assertRaises(ValueError):
            generation.render(value)
        value['version'] = 2
        value['profile']['template_fingerprint'] = generation.profile_fingerprint(version=2)
        bundle = generation.render(value)
        identity = json.loads(bundle['files']['identity-public.json'])
        self.assertEqual(identity['version'], 2)
        for key, db in identity['databases'].items():
            self.assertEqual(set(db['roles']), {'bi_viewer', 'data_engineer', 'admin'})
            self.assertEqual(db['roles']['admin'], scoped_username(key, 'admin'))
            self.assertNotEqual(db['roles']['admin'], 'shirokuma_admin')
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'bundle'
            generation.write_bundle(bundle, path)
            self.assertEqual(generation.load_bundle(path), bundle)

    def test_two_and_three_databases_are_complete_and_deterministic(self):
        for count in (2, 3):
            value = spec(count)
            bundle = generation.render(value, ROOT)
            self.assertEqual(bundle, generation.render(copy.deepcopy(value), ROOT))
            self.assertEqual(set(bundle['tofu']['databases']), set(value['databases']))
            identities = json.loads(bundle['files']['identity-public.json'])
            self.assertEqual(set(identities['databases']), set(value['databases']))
            self.assertEqual(len(bundle['manifest']['child_files']), 7)
            for key, db in value['databases'].items():
                entry = bundle['tofu']['databases'][key]
                self.assertEqual(entry['catalog'], 'db_'+key)
                self.assertEqual(entry['bootstrap_credentials_key'], generation.bootstrap_key(key))
                self.assertTrue(all(action.endswith('/'+entry['prefix']+'*') for action in entry['s3_actions']))
                self.assertIn(db['sql_catalog']+'.properties', '\n'.join(bundle['files']))
            self.assertNotIn('deploy/local-experiment/trino/iceberg.properties', bundle['files'])
            self.assertEqual(len(set(db['postgres_database'] for db in bundle['tofu']['databases'].values())), count)

    def test_bundle_roundtrip_rejects_tamper_and_template_drift(self):
        bundle = generation.render(spec(), ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)/'bundle'
            generation.write_bundle(bundle, directory)
            self.assertEqual(generation.load_bundle(directory, ROOT), bundle)
            target = directory/'identity-public.json'
            target.write_text('{}')
            with self.assertRaises(ValueError):
                generation.load_bundle(directory, ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary)/'checkout'
            for name, content in generation._profile_files(ROOT).items():
                target = checkout/name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            directory = Path(temporary)/'bundle'
            generation.write_bundle(bundle, directory)
            self.assertEqual(generation.load_bundle(directory, checkout), bundle)
            target = checkout/'deploy/local-experiment/trino/node.properties'
            target.write_bytes(target.read_bytes() + b'\n# changed approved profile\n')
            with self.assertRaises(ValueError):
                generation.load_bundle(directory, checkout)

    def test_rejects_inconsistent_or_secret_bearing_input(self):
        base = spec()
        mutations = [
            lambda v: v.update(password='must-not-leak'),
            lambda v: v['profile'].update(template_fingerprint='0'*64),
            lambda v: v['databases']['opaque_1'].update(sql_catalog='sales'),
            lambda v: v['databases']['opaque_0'].update(sql_catalog='system'),
            lambda v: v['databases']['opaque_0']['binding'].update(polaris_catalog='shirokuma_l1'),
            lambda v: v['databases']['opaque_1']['binding'].update(base_location=v['databases']['opaque_0']['binding']['base_location']),
            lambda v: v['databases']['opaque_1']['binding'].update(polaris_realm='REALM_0'),
            lambda v: v['databases']['opaque_0']['binding'].update(polaris_service_ref='polaris-'),
            lambda v: v['databases']['opaque_0']['secret_refs']['jwt'].update(version='v2'),
            lambda v: v['databases']['opaque_0']['secret_refs']['s3']['keys'].pop(),
            lambda v: v['databases']['opaque_0']['schemas'][0].update(purpose='marts'),
            lambda v: v['databases']['opaque_0']['schemas'][0].update(sql_name='custom_raw', purpose='custom_raw'),
        ]
        for change in mutations:
            value = copy.deepcopy(base)
            change(value)
            with self.assertRaises(ValueError) as failure:
                generation.render(value, ROOT)
            self.assertNotIn('must-not-leak', str(failure.exception))

    def test_catalog_names_do_not_overwrite_trino_server_config(self):
        value = spec(3)
        catalogs = ('config', 'node', 'log')
        for database, catalog in zip(value['databases'].values(), catalogs):
            database['sql_catalog'] = catalog
        bundle = generation.render(value, ROOT)
        base = 'deploy/local-experiment/trino/'
        deployment = json.loads(bundle['files'][base + 'deployment.yaml'])
        volume = next(volume['configMap'] for volume in deployment['spec']['template']['spec']['volumes']
                      if volume['name'] == 'config')
        paths = {item['key']: item['path'] for item in volume['items']}
        self.assertEqual(len(paths), len(volume['items']))
        for catalog in catalogs:
            server_file = catalog + '.properties'
            catalog_file = 'catalog-' + server_file
            self.assertEqual(paths[server_file], server_file)
            self.assertEqual(paths[catalog_file], 'catalog/' + server_file)
            self.assertIn(b'connector.name=iceberg\n', bundle['files'][base + catalog_file])
            self.assertNotIn(b'connector.name=iceberg\n', bundle['files'][base + server_file])
        for directory, prefix in (('', ''), ('secure/', '../')):
            kustomization = json.loads(bundle['files'][base + directory + 'kustomization.yaml'])
            sources = kustomization['configMapGenerator'][0]['files']
            self.assertEqual(len(sources), len(set(sources)))
            for catalog in catalogs:
                self.assertIn(prefix + catalog + '.properties', sources)
                self.assertIn(prefix + 'catalog-' + catalog + '.properties', sources)

    def test_every_declared_secret_consumer_uses_frozen_names(self):
        value = spec(3)
        bundle = generation.render(value, ROOT)
        names = {ref['name'] for ref in value['shared_secret_refs'].values()}
        names.update(ref['name'] for db in value['databases'].values() for ref in db['secret_refs'].values())
        def visit(node):
            if isinstance(node, dict):
                if 'secretKeyRef' in node:
                    self.assertIn(node['secretKeyRef']['name'], names)
                if 'secretName' in node:
                    self.assertIn(node['secretName'], names)
                for child in node.values(): visit(child)
            elif isinstance(node, list):
                for child in node: visit(child)
        for path, content in bundle['files'].items():
            if path.endswith('.yaml'):
                for doc in yaml.safe_load_all(content): visit(doc)

    @unittest.skipUnless(shutil.which('kubectl'), 'kubectl unavailable for offline Kustomize render')
    def test_delivery_roots_render_complete_resources_and_catalog_mounts(self):
        bundle = generation.render(spec(3), ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)/'bundle'
            generation.write_bundle(bundle, directory)
            targets = [json.loads(bundle['files'][path])['spec']['path'][2:]
                       for path in bundle['manifest']['child_files']]
            targets.append('deploy/local-experiment/trino')
            rendered = {}
            for target in targets:
                run = subprocess.run(['rtk', 'proxy', 'kubectl', 'kustomize', str(directory/target),
                    '--load-restrictor=LoadRestrictionsNone'], text=True, capture_output=True)
                self.assertEqual(run.returncode, 0, run.stderr)
                rendered[target] = list(yaml.safe_load_all(run.stdout))
            for target in ('deploy/local-experiment/trino', 'deploy/local-experiment/trino/secure'):
                docs = rendered[target]
                deployment = next(doc for doc in docs if doc['kind'] == 'Deployment')
                pod = deployment['spec']['template']['spec']
                self.assertEqual({entry['name'] for entry in pod['containers']}, {'trino', 'opa'})
                config = next(volume['configMap'] for volume in pod['volumes'] if volume['name'] == 'config')
                self.assertEqual({item['path'] for item in config['items'] if item['path'].startswith('catalog/')},
                    {'catalog/sales.properties', 'catalog/finance.properties', 'catalog/operations.properties', 'catalog/tpch.properties'})
                policy = next(doc for doc in docs if doc['kind'] == 'NetworkPolicy')
                rule = next(rule for rule in policy['spec']['egress'] if any(p['port'] == 8181 for p in rule.get('ports', [])))
                self.assertEqual({peer['podSelector']['matchLabels']['app.kubernetes.io/name'] for peer in rule['to']},
                                 {'polaris-0', 'polaris-1', 'polaris-2'})
            jobs = [doc for doc in rendered['deploy/catalog/bootstrap'] if doc['kind'] == 'Job']
            self.assertEqual(len(jobs), 3)
            for job in jobs:
                pod = job['spec']['template']['spec']
                self.assertEqual([c['name'] for c in pod['initContainers']], ['create-database', 'create-schema'])
                credentials = next(v['secret'] for v in pod['volumes'] if v['name'] == 'credentials')
                self.assertEqual(len(credentials['items']), 1)
            self.assertTrue(all(doc['kind'] == 'ConfigMap' for doc in rendered['deploy/gitops/iceberg/bootstrap']))

    def test_bootstrap_and_full_secret_sets_do_not_reissue_credentials(self):
        bundle = generation.render(spec(3), ROOT)
        metadata = bundle['tofu']
        def names(refs): return {ref['namespace'] + '/' + ref['name'] for ref in refs}
        first, later = names(metadata['bootstrap_secret_refs']), names(metadata['deferred_secret_refs'])
        self.assertFalse(first & later)
        self.assertEqual(len(later), 7)
        self.assertEqual(len(first | later), 6 + 5 * 3)
        self.assertEqual(metadata['derived_secret_roles'], ['polaris_bootstrap_secret', 'seaweedfs_server_secret'])
        self.assertNotIn('password.db', '\n'.join(bundle['files']))

    def test_bootstrap_job_identity_changes_only_for_its_actual_inputs(self):
        first = spec()
        second = copy.deepcopy(first)
        second['generation_id'] = 'generation-two'
        def jobs(value):
            bundle = generation.render(value, ROOT)
            documents = [json.loads(content) for path, content in bundle['files'].items()
                         if path.startswith('deploy/catalog/bootstrap/') and path.endswith('-job.yaml')]
            return {doc['metadata']['name']: doc['spec']['template'] for doc in documents}
        self.assertEqual(jobs(first), jobs(second))
        reference = second['shared_secret_refs']['polaris_bootstrap']
        reference.update(version='v2', name='polaris-bootstrap-v2')
        self.assertFalse(set(jobs(first)) & set(jobs(second)))
        self.assertTrue(all(generation.ANNOTATION not in row['metadata'].get('annotations', {})
                            for row in jobs(second).values()))


if __name__ == '__main__':
    unittest.main()
