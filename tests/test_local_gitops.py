"""Offline tests: no cluster, credentials, S3 endpoint or publishing."""
import base64
from contextlib import contextmanager
import copy
import io
import json
import os
import subprocess
from pathlib import Path
import tempfile
import types
import sys
import unittest
from unittest.mock import MagicMock, patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import runtime_profile

from tools.local_experiment import local_gitops as gitops


def fixture_files():
    files = {f"{gitops.CHILD_ROOT}/polaris-runtime-generation.yaml": yaml.safe_dump({
        "apiVersion": "v1", "kind": "ConfigMap", "data": {"POLARIS_CREDENTIAL_GENERATION": "42"}}).encode()}
    for name in gitops.CHILD_FILES:
        files[f"{gitops.CHILD_ROOT}/{name}.yaml"] = yaml.safe_dump({
            "apiVersion": "kustomize.toolkit.fluxcd.io/v1", "kind": "Kustomization",
            "metadata": {"name": f"shirokuma-{name}", "namespace": "flux-system"},
            "spec": {"path": f"./deploy/gitops/{name}", "sourceRef": {"kind": "GitRepository", "name": "flux-system"},
                     "dependsOn": [{"name": "keep-dependency"}],
                     "healthChecks": [{"kind": "Job", "name": f"updated-{name}"}],
                     "postBuild": {"substitute": {"KEEP": "retained"}}}}).encode()
    return files


class LocalGitopsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        for mocked in (patch.object(runtime_profile, 'ROOT', Path(temporary.name)),
                       patch.object(runtime_profile, '_current', None),
                       patch.dict(os.environ, {'SHIROKUMA_STATE_DIR': temporary.name})):
            mocked.start()
            self.addCleanup(mocked.stop)
        # Explicit profiles from the invoking shell must never be consumed here.
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop('SHIROKUMA_RUNTIME_PROFILE', None)

    def test_profile_overlay_precedes_digest_and_validation(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
        import runtime_profile
        files = fixture_files()
        overlaid = dict(files, **{'deploy/local-experiment/trino/secure/rules.json': b'{}'})
        with patch.object(runtime_profile, 'configure_process') as configure, \
             patch.object(runtime_profile, 'auth_overlay', return_value=overlaid) as overlay, \
             patch.object(gitops, 'collect_files', return_value=files), \
             patch.object(gitops, 'validate_references') as validate, \
             patch.object(gitops, 'content_digest', return_value='a' * 64) as digest, \
             patch.object(gitops, 'child_definitions'), patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(gitops.main(['prepare']), 0)
            configure.assert_called_once_with()
            overlay.assert_called_once_with(files)
            validate.assert_called_once_with(overlaid)
            digest.assert_called_once_with(overlaid)

    def assert_entrypoint_rejects_missing_profile(self, arguments):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            environment = {key: value for key, value in os.environ.items()
                if not key.startswith('SHIROKUMA_') and key not in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP')}
            environment.update(SHIROKUMA_RUNTIME_PROFILE=str(root / 'missing-profile.json'),
                SHIROKUMA_STATE_DIR=str(root / 'unused-state'), PYTHONDONTWRITEBYTECODE='1')
            result = subprocess.run(['rtk', 'proxy', sys.executable, '-B', *arguments, 'prepare'],
                cwd=gitops.ROOT, env=environment, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, '')
            self.assertEqual(result.stderr,
                'local GitOps command failed; check local prerequisites and snapshot inputs\n')
            for forbidden in ('Traceback', 'ModuleNotFoundError', 'ImportError'):
                self.assertNotIn(forbidden, result.stderr)
            self.assertEqual(list(root.iterdir()), [])

    def test_module_entrypoint_rejects_missing_profile_safely(self):
        self.assert_entrypoint_rejects_missing_profile(['-m', 'tools.local_experiment.local_gitops'])

    def test_script_entrypoint_rejects_missing_profile_safely(self):
        self.assert_entrypoint_rejects_missing_profile([str(Path(gitops.__file__).resolve())])

    def test_allowlist(self):
        for path in ("var/private", ".git/config", "deploy/gitops/../../private", "/deploy/gitops/a",
                     "deploy/gitops/env/a", "deploy/gitops/Secrets/a", "deploy/gitops/.env.dev",
                     "deploy/catalog/server/a", "deploy/local-experiment/gitops/bucket.yaml"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                gitops.checked_path(path)
        self.assertEqual(gitops.checked_path("deploy/gitops/arbitrary/a.yaml"), "deploy/gitops/arbitrary/a.yaml")

    def test_reject_secrets_and_private_keys(self):
        for content in (b"kind: Secret\nstringData: {private: hidden}",
                        b"kind: List\nitems: [{kind: Secret}]", b"secretGenerator: [{name: private}]",
                        b"kind: ConfigMap\n---\nkind: Secret", b"-----BEGIN OPENSSH PRIVATE KEY-----",
                        b"-----BEGIN PGP PRIVATE KEY BLOCK-----"):
            with self.subTest(content=content), self.assertRaises(ValueError):
                gitops.validate_content("deploy/gitops/test.yaml", content)
        gitops.validate_content("a.yaml", b"kind: Deployment\nspec: {secretRef: {name: existing}}")

    def test_collection_and_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for base in gitops.ALLOWED:
                (root / base).mkdir(parents=True)
                (root / base / "kustomization.yaml").write_text("resources: [config.yaml]\n")
                (root / base / "config.yaml").write_text("kind: ConfigMap\n")
            (root / "var").mkdir()
            (root / "var/private").write_text("never collected")
            files = gitops.collect_files(root)
            self.assertEqual(len(files), 6)
            self.assertFalse(any(path.startswith("var/") for path in files))
            (root / gitops.ALLOWED[0] / "link.yaml").symlink_to(root / "var/private")
            with self.assertRaisesRegex(ValueError, "symlink"):
                gitops.collect_files(root)

    def test_exact_references(self):
        files = {"deploy/local-experiment/trino/secure/kustomization.yaml": b"resources: [../deployment.yaml]\nconfigMapGenerator: [{name: test, files: [alias=../config.properties]}]",
                 "deploy/local-experiment/trino/deployment.yaml": b"kind: Deployment",
                 "deploy/local-experiment/trino/config.properties": b"key=value"}
        gitops.validate_references(files)
        for content in (b"resources: [https://example.org/a]", b"resources: [../../../../var/private]",
                        b"resources: [missing.yaml]", b"configMapGenerator: [{name: test, envs: [.env]}]",
                        b"patches: [{patch: 'kind: Secret'}]"):
            with self.subTest(content=content), self.assertRaises(ValueError):
                gitops.validate_references({**files, "deploy/local-experiment/trino/secure/kustomization.yaml": content})

    def test_content_addressing_is_stable_and_unambiguous(self):
        files = {"b": b"second", "a": b"first"}
        self.assertEqual(gitops.content_digest(files), gitops.content_digest(dict(reversed(list(files.items())))))
        self.assertNotEqual(gitops.content_digest(files), gitops.content_digest({**files, "a": b"changed"}))
        self.assertNotEqual(gitops.content_digest({"ab": b"c"}), gitops.content_digest({"a": b"bc"}))
        with self.assertRaises(ValueError):
            gitops.generation_prefix("../invalid")

    def test_render_children_preserves_updated_definitions(self):
        files = fixture_files()
        before = copy.deepcopy(files)
        prefix = gitops.generation_prefix(gitops.content_digest(files))
        docs = gitops.child_definitions(files, prefix, suspend=True)
        self.assertEqual(files, before)
        self.assertEqual(docs, gitops.child_definitions(files, prefix, suspend=True))
        self.assertEqual(len(docs), 7)
        self.assertNotIn("flux-system", [d["metadata"]["name"] for d in docs])
        for doc in docs:
            spec = doc["spec"]
            self.assertEqual(spec["sourceRef"], {"kind": "Bucket", "name": gitops.SOURCE})
            self.assertTrue(spec["path"].startswith("./" + prefix + "deploy/"))
            self.assertTrue(spec["suspend"])
            if doc["metadata"]["name"] in {"shirokuma-" + name for name in ("catalog-database", "catalog-bootstrap", "catalog", "iceberg-bootstrap")}:
                self.assertEqual(spec["postBuild"]["substitute"], {"KEEP": "retained", "POLARIS_CREDENTIAL_GENERATION": "42"})
        for name, doc in zip(gitops.CHILD_FILES, docs):
            self.assertEqual(doc["spec"]["healthChecks"][0]["name"], f"updated-{name}")
        storage = docs[1]["spec"]
        patch_doc = json.loads(storage["patches"][-1]["patch"])
        self.assertEqual(patch_doc["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]["memory"], "8Gi")
        self.assertEqual(patch_doc["spec"]["template"]["spec"]["containers"][0]["resources"]["requests"]["memory"], "1Gi")
        trino = docs[-1]["spec"]
        self.assertTrue(trino["path"].endswith("deploy/local-experiment/trino/secure"))
        self.assertEqual(trino["dependsOn"], [{"name": "shirokuma-iceberg-bootstrap"}])
        self.assertEqual(trino["healthChecks"][0]["name"], "trino-local")

    def test_jwt_generation_substitutes_only_declared_child_keys(self):
        files = fixture_files()
        generation_path = f"{gitops.CHILD_ROOT}/polaris-runtime-generation.yaml"
        data = yaml.safe_load(files[generation_path])
        data["data"].update({"POLARIS_SALES_JWT_KEY_GENERATION": "sales-2",
                             "POLARIS_FINANCE_JWT_KEY_GENERATION": 3,
                             "UNRELATED": "never propagated"})
        files[generation_path] = yaml.safe_dump(data).encode()
        path = f"{gitops.CHILD_ROOT}/catalog.yaml"
        catalog = yaml.safe_load(files[path])
        for key in gitops.GENERATION_KEYS:
            catalog["spec"]["postBuild"]["substitute"][key] = "__REPLACED_BY_ROOT_KUSTOMIZATION__"
        files[path] = yaml.safe_dump(catalog).encode()
        before = copy.deepcopy(files)
        docs = gitops.child_definitions(files, "generations/retained/")
        self.assertEqual(files, before)
        expected = {"KEEP": "retained", "POLARIS_CREDENTIAL_GENERATION": "42",
                    "POLARIS_SALES_JWT_KEY_GENERATION": "sales-2", "POLARIS_FINANCE_JWT_KEY_GENERATION": "3"}
        for doc in docs:
            substitutes = doc["spec"].get("postBuild", {}).get("substitute", {})
            if doc["metadata"]["name"] == "shirokuma-catalog":
                self.assertEqual(substitutes, expected)
            else:
                self.assertNotIn("POLARIS_SALES_JWT_KEY_GENERATION", substitutes)
                self.assertNotIn("POLARIS_FINANCE_JWT_KEY_GENERATION", substitutes)
            self.assertNotIn("UNRELATED", substitutes)
            self.assertTrue(doc["spec"]["path"].startswith("./generations/retained/"))

    def test_missing_placeholder_generation_fails(self):
        for key in (*gitops.GENERATION_KEYS[1:], "UNKNOWN_GENERATION"):
            files = fixture_files()
            path = f"{gitops.CHILD_ROOT}/catalog.yaml"
            doc = yaml.safe_load(files[path])
            doc["spec"]["postBuild"]["substitute"][key] = "__REPLACED_BY_ROOT_KUSTOMIZATION__"
            files[path] = yaml.safe_dump(doc).encode()
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, key):
                gitops.child_definitions(files, "generations/test/")

    def test_explicit_known_generations_are_validated(self):
        path = f"{gitops.CHILD_ROOT}/polaris-runtime-generation.yaml"
        for key in gitops.GENERATION_KEYS:
            for value in (None, True, [], {}, "", "  ", "__REPLACED_BY_ROOT_KUSTOMIZATION__"):
                files = fixture_files()
                doc = yaml.safe_load(files[path])
                doc["data"][key] = value
                files[path] = yaml.safe_dump(doc).encode()
                with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, key):
                    gitops.child_definitions(files, "generations/test/")

    def test_manifest_contract(self):
        docs = gitops.source_definitions(gitops.ROOT, "generations/test/")
        spec = docs[0]["spec"]
        self.assertEqual(spec["prefix"], "generations/test/")
        self.assertEqual(spec["secretRef"], {"name": gitops.SOURCE})
        self.assertEqual(spec["bucketName"], gitops.BUCKET)
        self.assertEqual(spec["endpoint"], "seaweedfs-s3.shirokuma-storage.svc.cluster.local:8333")
        self.assertEqual(spec["region"], "us-east-1")
        self.assertEqual(spec["provider"], "generic")
        self.assertIs(spec["insecure"], True)
        peer = docs[1]["spec"]["ingress"][0]
        self.assertEqual(peer["ports"], [{"protocol": "TCP", "port": 8333}])
        self.assertEqual(peer["from"], [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "flux-system"}},
                                        "podSelector": {"matchLabels": {"app": "source-controller"}}}])

    def test_prepare_is_offline_and_prints_only_summary(self):
        with patch.object(gitops, "collect_files", return_value=fixture_files()), patch.object(gitops, "kubectl") as kubectl, patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(gitops.main(["prepare"]), 0)
            self.assertEqual(set(json.loads(out.getvalue())), {"relativeFilesCount", "digest"})
            kubectl.assert_not_called()

    def test_subprocess_context_rtk_and_private_failure(self):
        with patch.object(gitops.subprocess, "run", return_value=types.SimpleNamespace(returncode=0, stdout="safe")) as run:
            self.assertEqual(gitops.kubectl("get", "bucket"), "safe")
            self.assertEqual(run.call_args.args[0][:5], ["rtk", "proxy", "kubectl", "--context", gitops.CONTEXT])
            self.assertTrue(run.call_args.kwargs["capture_output"])
        with patch.object(gitops.subprocess, "run", return_value=types.SimpleNamespace(returncode=1, stdout="PRIVATE", stderr="PRIVATE")):
            with self.assertRaisesRegex(RuntimeError, "withheld") as error:
                gitops.kubectl("get", "secret")
            self.assertNotIn("PRIVATE", str(error.exception))

    def test_activate_checks_root_snapshot_and_readiness(self):
        files = fixture_files()
        prefix = gitops.generation_prefix(gitops.content_digest(files))
        root = {"spec": {"suspend": True}}
        bucket = {"spec": {"prefix": prefix}, "metadata": {"generation": 3},
                  "status": {"conditions": [{"type": "Ready", "status": "True", "observedGeneration": 3}]}}
        with patch.object(gitops, "kubectl", side_effect=[json.dumps(root), json.dumps(bucket)]), patch.object(gitops, "apply_documents") as apply:
            gitops.activate(files, prefix, True)
            self.assertEqual(len(apply.call_args.args[0]), 7)
        for bad_root, bad_bucket in (({"spec": {"suspend": False}}, bucket), (root, {**bucket, "spec": {"prefix": "old/"}}),
                                     (root, {**bucket, "metadata": {"generation": 4}})):
            with patch.object(gitops, "kubectl", side_effect=[json.dumps(bad_root), json.dumps(bad_bucket)]), patch.object(gitops, "apply_documents") as apply:
                with self.assertRaises(ValueError):
                    gitops.activate(files, prefix)
                apply.assert_not_called()

    def test_operator_selection_only_admin_in_memory(self):
        config = {"identities": [{"name": "flux-reader", "actions": ["Read:shirokuma-gitops/*"], "credentials": [{"accessKey": "reader"}]},
                                 {"name": "operator", "actions": ["Admin"], "credentials": [{"accessKey": "synthetic", "secretKey": "fake"}]}]}
        secret = {"data": {"s3.json": base64.b64encode(json.dumps(config).encode()).decode()}}
        with patch.object(gitops, "kubectl", return_value=json.dumps(secret)) as kubectl:
            self.assertEqual(gitops.operator_credentials()["accessKey"], "synthetic")
            self.assertEqual(kubectl.call_args.args[:5], ("-n", "shirokuma-storage", "get", "secret", "seaweedfs-s3-credentials"))

    def test_snapshot_failure_never_switches_bucket(self):
        @contextmanager
        def forward():
            yield "http://127.0.0.1:1234"
        client = MagicMock()
        boto3 = types.SimpleNamespace(client=MagicMock(return_value=client))
        config_module = types.SimpleNamespace(Config=MagicMock())
        with patch.dict("sys.modules", {"boto3": boto3, "botocore.config": config_module}), patch.object(gitops, "require_suspended_root"), patch.object(gitops, "operator_credentials", return_value={"accessKey": "fake", "secretKey": "fake"}), patch.object(gitops, "s3_port_forward", forward), patch.object(gitops, "upload_files", side_effect=RuntimeError("failure")), patch.object(gitops, "apply_documents") as apply:
            with self.assertRaises(RuntimeError):
                gitops.snapshot(gitops.ROOT, fixture_files(), "prefix/")
            apply.assert_not_called()
            client.close.assert_called_once()

    def test_immutable_upload_and_retry(self):
        class ClientError(Exception):
            def __init__(self, code):
                self.response = {"ResponseMetadata": {"HTTPStatusCode": code}}
        client = MagicMock()
        files = {"deploy/gitops/a.yaml": b"kind: ConfigMap"}
        with patch.dict("sys.modules", {"botocore.exceptions": types.SimpleNamespace(ClientError=ClientError)}):
            gitops.upload_files(client, files, "generation/")
            self.assertEqual(client.put_object.call_args.kwargs["IfNoneMatch"], "*")
            self.assertEqual(client.put_object.call_args.kwargs["Key"], "generation/deploy/gitops/a.yaml")
            client.put_object.side_effect = ClientError(412)
            client.get_object.return_value = {"Body": io.BytesIO(b"kind: ConfigMap")}
            gitops.upload_files(client, files, "generation/")
            client.get_object.return_value = {"Body": io.BytesIO(b"different")}
            with self.assertRaisesRegex(ValueError, "immutable"):
                gitops.upload_files(client, files, "generation/")

    def test_main_redacts_unexpected_errors(self):
        with patch.object(gitops, "collect_files", side_effect=ValueError("PRIVATE-CREDENTIAL")), patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertEqual(gitops.main(["prepare"]), 1)
            self.assertNotIn("PRIVATE", err.getvalue())

    def test_apply_owns_only_selected_documents(self):
        children = gitops.child_definitions(fixture_files(), "generations/test/", True)
        with patch.object(gitops, "kubectl") as kubectl:
            gitops.apply_documents(children)
            self.assertIn("--force-conflicts", kubectl.call_args.args)
            applied = list(yaml.safe_load_all(kubectl.call_args.kwargs["payload"]))
            self.assertEqual(applied, children)
            self.assertTrue(all(doc["kind"] == "Kustomization" and doc["metadata"]["name"] != "flux-system" for doc in applied))

    def test_real_repo_snapshot_is_offline_and_references_complete(self):
        with patch.object(gitops, "kubectl") as kubectl:
            files = gitops.collect_files(gitops.ROOT)
            prefix = gitops.generation_prefix(gitops.content_digest(files))
            children = gitops.child_definitions(files, prefix)
            self.assertEqual(len(children), 7)
            self.assertTrue(all(gitops.checked_path(path) == path for path in files))
            for doc in children:
                path = doc["spec"]["path"].removeprefix("./" + prefix)
                self.assertIn(path + "/kustomization.yaml", files)
            kubectl.assert_not_called()


if __name__ == "__main__":
    unittest.main()
