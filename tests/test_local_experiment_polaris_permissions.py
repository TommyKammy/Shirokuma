import copy
import importlib.util
import unittest
from pathlib import Path


PATH = Path(__file__).resolve().parents[1] / "tools/local_experiment/polaris_permissions.py"
SPEC = importlib.util.spec_from_file_location("polaris_permissions", PATH)
permissions = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(permissions)


class FakePolaris:
    def __init__(self, role=None, grants=None, bound=False, namespace_exists=True,
                 catalog=None, conflict=False):
        self.role = role
        self.grants = grants or []
        self.bound = bound
        self.namespace_exists = namespace_exists
        self.writes = []
        self.catalog = catalog
        self.conflict = conflict

    def request(self, method, path, body=None):
        if method != "GET":
            self.writes.append((method, path, copy.deepcopy(body)))
        if path == permissions.CATALOG_PATH:
            if method == "PUT":
                if self.conflict or body["currentEntityVersion"] != self.catalog["entityVersion"]:
                    raise permissions.ApiError(method, path, 409)
                self.catalog.update(copy.deepcopy({
                    "properties": body["properties"],
                    "storageConfigInfo": body["storageConfigInfo"],
                    "entityVersion": self.catalog["entityVersion"] + 1,
                }))
            return copy.deepcopy(self.catalog)
        if method == "GET" and "/namespaces/" in path:
            if not self.namespace_exists:
                raise permissions.ApiError(method, path, 404)
            return {"namespace": [permissions.NAMESPACE]}
        if method == "GET" and path == permissions.ROLE_PATH:
            if self.role is None:
                raise permissions.ApiError(method, path, 404)
            return self.role
        if method == "POST" and path == permissions.ROLE_COLLECTION:
            self.role = copy.deepcopy(body["catalogRole"])
            return {}
        if path == permissions.ROLE_PATH + "/grants":
            if method == "GET":
                return {"grants": self.grants}
            self.grants.append(copy.deepcopy(body["grant"]))
            return {}
        if path == permissions.BINDING_PATH:
            if method == "PUT":
                self.bound = True
            return {"roles": [{"name": permissions.ROLE}] if self.bound else []}
        raise AssertionError((method, path, body))


class LocalPolarisPermissionsTests(unittest.TestCase):
    def marked_role(self):
        return {"name": permissions.ROLE, "properties": permissions.MARKER}

    def test_only_missing_namespace_data_privilege_is_added(self):
        client = FakePolaris()
        result = permissions.ensure_permissions(client)
        self.assertEqual(result["result"], "configured")
        grants = [body for _, path, body in client.writes if path.endswith("/grants")]
        self.assertEqual(grants, [{"grant": {
            "type": "namespace", "namespace": ["local_experiment"],
            "privilege": "TABLE_WRITE_DATA",
        }}])
        self.assertEqual(client.writes[-1], (
            "PUT", permissions.BINDING_PATH,
            {"catalogRole": {"name": "local_experiment_purge"}},
        ))
        self.assertNotIn("smoke", str(client.writes))

    def test_second_run_is_read_only(self):
        client = FakePolaris()
        permissions.ensure_permissions(client)
        client.writes.clear()
        self.assertEqual(permissions.ensure_permissions(client)["actions"], [])
        self.assertEqual(client.writes, [])

    def test_existing_broader_grants_are_never_bound(self):
        client = FakePolaris(role=self.marked_role(), grants=[{
            "type": "catalog", "privilege": "CATALOG_MANAGE_CONTENT",
        }])
        with self.assertRaisesRegex(permissions.SafeError, "unexpected grants"):
            permissions.ensure_permissions(client)
        self.assertFalse(client.bound)
        self.assertEqual(client.writes, [])

    def test_retained_fixture_namespace_grant_is_rejected(self):
        grant = dict(permissions.GRANT, namespace=["smoke"])
        client = FakePolaris(role=self.marked_role(), grants=[grant])
        with self.assertRaises(permissions.SafeError):
            permissions.ensure_permissions(client)
        self.assertEqual(client.writes, [])

    def test_foreign_role_name_collision_fails_without_writes(self):
        client = FakePolaris(role={"name": permissions.ROLE, "properties": {}})
        with self.assertRaisesRegex(permissions.SafeError, "unmarked role"):
            permissions.ensure_permissions(client)
        self.assertEqual(client.writes, [])

    def test_missing_namespace_prevents_role_creation(self):
        client = FakePolaris(namespace_exists=False)
        with self.assertRaises(permissions.ApiError):
            permissions.ensure_permissions(client)
        self.assertEqual(client.writes, [])


class LocalCatalogPurgeTests(unittest.TestCase):
    def catalog(self):
        return {
            "name": "shirokuma_l1", "type": "INTERNAL", "entityVersion": 17,
            "properties": {"default-base-location": "s3://retained/l1",
                           "keep-unrelated-setting": "unchanged"},
            "storageConfigInfo": {"storageType": "S3", "region": "us-east-1",
                                  "allowedLocations": ["s3://retained/l1"]},
        }

    def client(self, catalog=None, **kwargs):
        return FakePolaris(grants=[copy.deepcopy(permissions.GRANT)],
                           catalog=catalog or self.catalog(), **kwargs)

    def test_update_preserves_properties_storage_and_uses_current_version(self):
        client = self.client()
        before = copy.deepcopy(client.catalog)
        receipt = permissions.enable_catalog_purge(client)
        self.assertEqual(len(client.writes), 1)
        method, path, body = client.writes[0]
        self.assertEqual((method, path), ("PUT", permissions.CATALOG_PATH))
        self.assertEqual(body["currentEntityVersion"], 17)
        self.assertEqual(body["storageConfigInfo"], before["storageConfigInfo"])
        self.assertEqual({key: value for key, value in body["properties"].items()
                          if key != permissions.PURGE_PROPERTY}, before["properties"])
        self.assertEqual(receipt["previous"], {"present": False, "value": None, "entity_version": 17})
        self.assertEqual((receipt["value"], receipt["entity_version"]), ("true", 18))
        self.assertNotIn("keep-unrelated-setting", str(receipt))

    def test_prior_false_is_recorded_for_rollback(self):
        catalog = self.catalog()
        catalog["properties"][permissions.PURGE_PROPERTY] = "false"
        receipt = permissions.enable_catalog_purge(self.client(catalog))
        self.assertEqual(receipt["previous"], {"present": True, "value": "false", "entity_version": 17})

    def test_enabled_flag_is_idempotent_without_catalog_writes(self):
        client = self.client()
        permissions.enable_catalog_purge(client)
        client.writes.clear()
        receipt = permissions.enable_catalog_purge(client)
        self.assertEqual(receipt["action"], "already-enabled")
        self.assertEqual(client.writes, [])

    def test_version_conflict_does_not_retry_or_clobber_configuration(self):
        client = self.client(conflict=True)
        before = copy.deepcopy(client.catalog)
        with self.assertRaises(permissions.ApiError) as caught:
            permissions.enable_catalog_purge(client)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(client.catalog, before)
        self.assertEqual(len(client.writes), 1)

    def test_scope_mismatch_prevents_catalog_update(self):
        client = self.client()
        client.grants[0]["namespace"] = ["smoke"]
        with self.assertRaises(permissions.SafeError):
            permissions.enable_catalog_purge(client)
        self.assertEqual(client.writes, [])

    def test_missing_version_prevents_unconditional_update(self):
        catalog = self.catalog()
        del catalog["entityVersion"]
        client = self.client(catalog)
        with self.assertRaises(permissions.SafeError):
            permissions.enable_catalog_purge(client)
        self.assertEqual(client.writes, [])


if __name__ == "__main__":
    unittest.main()
