import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('polaris_permissions', ROOT / 'tools/local_experiment/polaris_permissions.py')
permissions = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(permissions)


class PolarisPermissionTests(unittest.TestCase):
    def test_bootstrap_and_repeat_add_only_missing_namespace_data_privileges(self):
        roles, memberships, grants, mutations = [], [], [], []

        def request(method, path, body=None):
            if method == 'GET':
                if path.endswith('/grants'):
                    return {'grants': list(grants)}
                return {'roles': list(memberships if '/principal-roles/' in path else roles)}
            mutations.append((method, path, body))
            if path.endswith('/grants'):
                grants.append(body['grant'])
            elif '/principal-roles/' in path:
                memberships.append(body['catalogRole'])
            else:
                roles.append(body['catalogRole'])
            return {}

        self.assertEqual(permissions.ensure_permissions(request), ['raw', 'sandbox', 'marts', 'staging'])
        self.assertEqual(len(mutations), 6)
        self.assertEqual({grant['privilege'] for grant in grants}, {'TABLE_WRITE_DATA'})
        self.assertTrue(all(grant['type'] == 'namespace' for grant in grants))
        self.assertEqual(permissions.ensure_permissions(request), [])
        self.assertEqual(len(mutations), 6)

    def test_existing_role_and_unrelated_namespace_grant_are_preserved(self):
        existing = [dict(type='namespace', namespace=['local_experiment'], privilege='TABLE_WRITE_DATA')]
        writes = []

        def request(method, path, body=None):
            if method == 'GET':
                if path.endswith('/grants'):
                    return {'grants': list(existing)}
                return {'roles': [{'name': permissions.ROLE}]}
            writes.append(body['grant'])
            existing.append(body['grant'])
            return {}

        permissions.ensure_permissions(request)
        self.assertEqual(len(writes), 4)
        self.assertEqual(existing[0]['namespace'], ['local_experiment'])
        self.assertEqual(len(existing), 5)

    def test_failed_readback_is_reported(self):
        def request(method, path, body=None):
            if method == 'GET':
                return {'grants': []} if path.endswith('/grants') else {'roles': [{'name': permissions.ROLE}]}
            return {}

        with self.assertRaisesRegex(RuntimeError, 'verification failed'):
            permissions.ensure_permissions(request)


if __name__ == '__main__':
    unittest.main()
