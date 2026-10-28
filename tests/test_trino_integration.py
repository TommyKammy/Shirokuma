"""Offline wiring checks; Rego behavior is exercised by opa/policy_test.rego."""
import json
from pathlib import Path
import re
import subprocess
import unittest


TRINO = Path(__file__).resolve().parents[1] / 'deploy/local-experiment/trino'
SECURE = TRINO / 'secure'


def properties(path):
    return dict(line.split('=', 1) for line in path.read_text().splitlines()
                if line and not line.startswith('#'))


class TrinoIntegrationTests(unittest.TestCase):
    def test_generated_configmaps_disable_flux_substitution_and_keep_placeholders(self):
        result = subprocess.run(
            ['kubectl', 'kustomize', '--load-restrictor', 'LoadRestrictionsNone', str(SECURE)],
            text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        configmaps = [document for document in result.stdout.split('---\n')
                      if re.search(r'^kind: ConfigMap$', document, re.MULTILINE)]
        self.assertEqual(len(configmaps), 2)
        for document in configmaps:
            metadata = document.split('\nmetadata:\n', 1)[1]
            self.assertIn('  annotations:\n    kustomize.toolkit.fluxcd.io/substitute: disabled\n', metadata)
        runtime_config = next(document for document in configmaps
                              if re.search(r'^  name: trino-local-config-[\w]+$', document, re.MULTILINE))
        for path in [SECURE / 'config.properties', TRINO / 'iceberg.properties', TRINO / 'finance.properties']:
            placeholders = [line for line in path.read_text().splitlines() if '${ENV:' in line]
            self.assertTrue(placeholders, str(path))
            for line in placeholders:
                self.assertIn(line, runtime_config)

    def test_official_supply_and_preserved_security_mounts(self):
        overlay = (SECURE / 'kustomization.yaml').read_text()
        self.assertIn('newName: docker.io/trinodb/trino\n'
                      '    digest: sha256:aa18e61b2e7776ab8641ba8baaa8687d0430894e88c639e61010cc46a994ab36', overlay)
        patch = (SECURE / 'deployment.yaml').read_text()
        base = (TRINO / 'deployment.yaml').read_text()
        self.assertIn('command: [/usr/lib/trino/bin/run-trino]', patch)
        self.assertNotIn('/opt/trino', base + patch)
        for setting in ['imagePullPolicy: Never', 'runAsUser: 10000',
                        'runAsGroup: 10000', 'fsGroup: 10000',
                        'readOnlyRootFilesystem: true', 'mountPath: /etc/trino',
                        'mountPath: /tmp', 'mountPath: /var/trino']:
            self.assertIn(setting, base)
        self.assertIn('mountPath: /etc/trino-auth\n              readOnly: true', patch)

    def test_dedicated_secret_refs_and_catalog_properties(self):
        deployment = (TRINO / 'deployment.yaml').read_text()
        refs = dict((env, (secret, key)) for env, secret, key in re.findall(
            r'- name: (\w+)\n\s+valueFrom:\n\s+secretKeyRef:\n\s+name: ([\w-]+)\n\s+key: (\w+)', deployment))
        expected = {}
        for catalog, prefix, server, warehouse, principal, s3 in [
            ('iceberg', 'SALES', 'polaris', 'shirokuma_l1',
             'polaris-trino-credentials', 'seaweedfs-s3-sales-credentials'),
            ('finance', 'FINANCE', 'polaris-finance', 'finance',
             'polaris-finance-trino-credentials', 'seaweedfs-s3-finance-credentials'),
        ]:
            expected.update({prefix + '_POLARIS_CLIENT_ID': (principal, 'client_id'),
                             prefix + '_POLARIS_CLIENT_SECRET': (principal, 'client_secret'),
                             prefix + '_S3_REGION': (s3, 'S3_REGION'),
                             prefix + '_S3_ACCESS_KEY_ID': (s3, 'AWS_ACCESS_KEY_ID'),
                             prefix + '_S3_SECRET_ACCESS_KEY': (s3, 'AWS_SECRET_ACCESS_KEY')})
            config = properties(TRINO / (catalog + '.properties'))
            endpoint = 'http://' + server + '.shirokuma-dev.svc.cluster.local:8181/api/catalog'
            self.assertEqual(config['iceberg.rest-catalog.uri'], endpoint)
            self.assertEqual(config['iceberg.rest-catalog.oauth2.server-uri'], endpoint + '/v1/oauth/tokens')
            self.assertEqual(config['iceberg.rest-catalog.warehouse'], warehouse)
            self.assertEqual(config['iceberg.rest-catalog.http-headers'], 'Polaris-Realm:' + prefix)
            self.assertEqual(config['iceberg.rest-catalog.oauth2.credential'],
                             '${ENV:' + prefix + '_POLARIS_CLIENT_ID}:${ENV:' + prefix + '_POLARIS_CLIENT_SECRET}')
            for setting, env in [('s3.region', '_S3_REGION'), ('s3.aws-access-key', '_S3_ACCESS_KEY_ID'),
                                 ('s3.aws-secret-key', '_S3_SECRET_ACCESS_KEY')]:
                self.assertEqual(config[setting], '${ENV:' + prefix + env + '}')
            self.assertEqual(config['s3.endpoint'], 'http://seaweedfs-s3.shirokuma-storage.svc.cluster.local:8333')
            self.assertEqual(config['s3.path-style-access'], 'true')
            self.assertEqual(config['iceberg.rest-catalog.vended-credentials-enabled'], 'false')
        self.assertEqual(refs, expected)
        self.assertNotIn('root-credentials', deployment)
        self.assertNotIn('seaweedfs-s3-application-credentials', deployment)

    def test_generators_and_explicit_mount_items(self):
        for directory, prefix in [(TRINO, ''), (SECURE, '../')]:
            generator = (directory / 'kustomization.yaml').read_text()
            deployment = (directory / 'deployment.yaml').read_text()
            for catalog in ['iceberg', 'finance', 'tpch']:
                filename = catalog + '.properties'
                self.assertIn('- ' + prefix + filename + '\n', generator)
                self.assertIn('- key: ' + filename + '\n                path: catalog/' + filename, deployment)
        generator = (SECURE / 'kustomization.yaml').read_text()
        deployment = (SECURE / 'deployment.yaml').read_text()
        for filename in ['access-control.properties', 'opa-access-control.properties',
                         'password-authenticator.properties', 'rules.json']:
            self.assertIn('- ' + filename + '\n', generator)
            self.assertIn('- key: ' + filename + '\n                path: ' + filename, deployment)
        self.assertIn('- name: trino-local-opa\n    files:\n      - opa/guard.rego\n      - opa/api.rego', generator)
        self.assertIn('name: opa-policy\n          configMap:\n            name: trino-local-opa', deployment)

    def test_both_access_controls_and_loopback_sidecar(self):
        config = properties(SECURE / 'config.properties')
        self.assertEqual(config['access-control.config-files'],
                         '/etc/trino/access-control.properties,/etc/trino/opa-access-control.properties')
        self.assertEqual(properties(SECURE / 'access-control.properties'),
                         {'access-control.name': 'file', 'security.config-file': '/etc/trino/rules.json'})
        self.assertEqual(properties(SECURE / 'opa-access-control.properties'), {
            'access-control.name': 'opa',
            'opa.policy.uri': 'http://127.0.0.1:8182/v1/data/shirokuma/allow',
            'opa.allow-permission-management-operations': 'true',
            'opa.http-client.request-timeout': '5s', 'opa.http-client.connect-timeout': '2s'})
        sidecar = (SECURE / 'deployment.yaml').read_text().split('        - name: opa\n')[1].split('      volumes:')[0]
        for setting in ['image: docker.io/openpolicyagent/opa@sha256:2ce2bacc87ddc25a475d71b67d422d206c20cf85b6ed00d81d520a5a039e22ce',
                        'imagePullPolicy: Never', '--server', '--addr=127.0.0.1:8182',
                        '--authorization=basic', '--log-level=error', '/etc/opa/guard.rego', '/etc/opa/api.rego',
                        'readOnlyRootFilesystem: true', 'allowPrivilegeEscalation: false',
                        'mountPath: /etc/opa\n              readOnly: true',
                        'cpu: 25m', 'memory: 32Mi', 'cpu: 250m', 'memory: 128Mi']:
            self.assertIn(setting, sidecar)
        for forbidden in ['ports:', 'decision_logs', '--log-format', '--watch']:
            self.assertNotIn(forbidden, sidecar)
        self.assertNotIn('8182', (TRINO / 'service.yaml').read_text())

    def test_finance_is_admin_only_and_egress_preserved(self):
        rules = json.loads((SECURE / 'rules.json').read_text())
        for user, expected in [('shirokuma_admin', 'all'), ('shirokuma_engineer', 'none'),
                               ('shirokuma_viewer', 'none')]:
            matching = next(rule for rule in rules['catalogs']
                            if re.fullmatch(rule.get('user', '.*'), user)
                            and re.fullmatch(rule.get('catalog', '.*'), 'finance'))
            self.assertEqual(matching['allow'], expected)
        policy = (TRINO / 'networkpolicy.yaml').read_text()
        catalog_egress = policy.split('app.kubernetes.io/name: polaris\n')[1].split('    - to:')[0]
        self.assertIn('app.kubernetes.io/name: polaris-finance', catalog_egress)
        self.assertEqual(catalog_egress.count('app.kubernetes.io/component: catalog-server'), 2)
        self.assertIn('port: 8181', catalog_egress)
        for intent in ['shirokuma-storage', 'port: 8333', 'kube-system', 'port: 53', 'protocol: UDP']:
            self.assertIn(intent, policy)


if __name__ == '__main__':
    unittest.main()
