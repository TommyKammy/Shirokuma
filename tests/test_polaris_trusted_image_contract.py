from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
VERIFIER_PATH = ROOT / "scripts/verify_polaris_trusted_image.py"


def _load_verifier() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "verify_polaris_trusted_image",
        VERIFIER_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {VERIFIER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verifier = _load_verifier()


class PolarisTrustedImageContractTests(unittest.TestCase):
    def _fixture(self) -> Path:
        directory = Path(tempfile.mkdtemp(prefix="polaris-contract-"))
        self.addCleanup(shutil.rmtree, directory)
        for relative in (
            Path("bootstrap/polaris/v1.6.0"),
            Path("bootstrap/postgresql/v18.4"),
            Path(".github/workflows"),
        ):
            destination = directory / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(ROOT / relative, destination)
        ledger = directory / verifier.RESIDENT_LEDGER
        ledger.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / verifier.RESIDENT_LEDGER, ledger)
        return directory

    def _rewrite_json(
        self,
        root: Path,
        relative: Path,
        mutate: Callable[[dict[str, object]], None],
    ) -> None:
        path = root / relative
        value = json.loads(path.read_text(encoding="utf-8"))
        mutate(value)
        path.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _assert_code(
        self,
        root: Path,
        code: str,
        detail: str | None = None,
    ) -> None:
        with self.assertRaises(verifier.ContractError) as raised:
            verifier.audit(root)
        self.assertEqual(code, raised.exception.code)
        if detail is not None:
            self.assertIn(detail, raised.exception.detail)

    def test_repository_pending_contract_is_fail_closed_and_valid(self) -> None:
        verifier.audit(ROOT)

    def test_minimal_pending_fixture_is_valid(self) -> None:
        verifier.audit(self._fixture())

    def test_source_pin_mutation_fails_closed(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["source_release"]["archive_sha512"] = "0" * 128  # type: ignore[index]

        self._rewrite_json(root, verifier.POLARIS_SOURCE, mutate)
        self._assert_code(root, "SOURCE_PIN", "archive_sha512")

    def test_builder_digest_mutation_fails_closed(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["selected_base_candidates"]["builder_arm64_manifest"] = (  # type: ignore[index]
                "docker.io/library/gradle@sha256:" + "0" * 64
            )

        self._rewrite_json(root, verifier.POLARIS_SOURCE, mutate)
        self._assert_code(root, "SOURCE_PIN", "builder_arm64_manifest")

    def test_duplicate_contract_key_fails_closed(self) -> None:
        root = self._fixture()
        source = root / verifier.POLARIS_SOURCE
        text = source.read_text(encoding="utf-8")
        source.write_text(
            text.replace(
                '"schema_version": 1,',
                '"schema_version": 1,\n  "schema_version": 1,',
                1,
            ),
            encoding="utf-8",
        )
        self._assert_code(root, "SOURCE_PIN", "duplicate JSON key")

    def test_retained_signing_key_mutation_fails_closed(self) -> None:
        root = self._fixture()
        key = root / verifier.POLARIS_KEY
        key.write_text(key.read_text(encoding="ascii") + "\n", encoding="ascii")
        self._assert_code(root, "KEY", "SHA-256")

    def test_retained_signing_key_symlink_fails_closed(self) -> None:
        root = self._fixture()
        key = root / verifier.POLARIS_KEY
        replacement = key.with_suffix(".copy")
        key.replace(replacement)
        key.symlink_to(replacement.name)
        self._assert_code(root, "KEY", "symlink")

    def test_build_enablement_before_dependency_closure_fails_closed(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["build"]["enabled"] = True  # type: ignore[index]

        self._rewrite_json(root, verifier.POLARIS_CONTRACT, mutate)
        self._assert_code(root, "CONTRACT_STATE", "build.enabled")

    def test_extra_publication_contract_is_rejected(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["alternate_publication"] = {"enabled": True}

        self._rewrite_json(root, verifier.POLARIS_CONTRACT, mutate)
        self._assert_code(root, "CONTRACT_STATE", "<root> keys")

    def test_empty_build_requirements_are_rejected(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["required_before_build_enablement"] = []

        self._rewrite_json(root, verifier.POLARIS_CONTRACT, mutate)
        self._assert_code(root, "CONTRACT_STATE", "build enablement requirements")

    def test_publication_workflow_is_forbidden_while_pending(self) -> None:
        root = self._fixture()
        workflow = root / ".github/workflows/polaris-arm64.yml"
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_text("name: forbidden\n", encoding="utf-8")
        self._assert_code(root, "FORBIDDEN_PATH", "polaris-arm64.yml")

    def test_alternate_publication_workflow_is_forbidden_while_pending(
        self,
    ) -> None:
        root = self._fixture()
        workflow = root / ".github/workflows/publish-catalog.yml"
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_text(
            "name: publish catalog\n"
            "jobs:\n"
            "  publish:\n"
            "    permissions:\n"
            "      packages: write\n"
            "    steps:\n"
            "      - run: echo bootstrap/polaris/v1.6.0\n",
            encoding="utf-8",
        )
        self._assert_code(root, "FORBIDDEN_PATH", "publish-catalog.yml")

    def test_indirect_write_capable_workflow_is_forbidden_while_pending(
        self,
    ) -> None:
        root = self._fixture()
        workflow = root / ".github/workflows/publish-polaris.yml"
        workflow.write_text(
            "name: publish\n"
            "permissions:\n"
            "  packages: write\n",
            encoding="utf-8",
        )
        self._assert_code(root, "FORBIDDEN_PATH", "workflow inventory changed")

    def test_existing_workflow_byte_drift_is_forbidden_while_pending(self) -> None:
        root = self._fixture()
        workflow = root / ".github/workflows/ci.yml"
        workflow.write_text(
            workflow.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        self._assert_code(root, "FORBIDDEN_PATH", "workflow inventory changed")

    def test_alternate_containerfile_name_is_forbidden_while_pending(
        self,
    ) -> None:
        root = self._fixture()
        dockerfile = root / "bootstrap/polaris/v1.6.0/Dockerfile"
        dockerfile.write_text("FROM scratch\n", encoding="utf-8")
        self._assert_code(root, "FORBIDDEN_PATH", "Dockerfile")

    def test_release_evidence_is_forbidden_while_pending(self) -> None:
        root = self._fixture()
        evidence = root / "bootstrap/polaris/v1.6.0/evidence/claim.json"
        evidence.write_text("{}\n", encoding="utf-8")
        self._assert_code(root, "FORBIDDEN_PATH", "evidence/claim.json")

    def test_polaris_runtime_enablement_fails_closed(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["runtime_manifests"]["permitted"] = True  # type: ignore[index]

        self._rewrite_json(root, verifier.POLARIS_ADMISSION, mutate)
        self._assert_code(root, "POLARIS_ADMISSION", "runtime_manifests.permitted")

    def test_empty_polaris_blockers_are_rejected(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["blockers"] = ["", "", "", ""]

        self._rewrite_json(root, verifier.POLARIS_ADMISSION, mutate)
        self._assert_code(root, "POLARIS_ADMISSION", "blockers")

    def test_source_record_byte_drift_breaks_admission_binding(self) -> None:
        root = self._fixture()
        source = root / verifier.POLARIS_SOURCE
        source.write_text(
            source.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        self._assert_code(root, "POLARIS_ADMISSION", "source record bytes")

    def test_build_contract_byte_drift_breaks_admission_binding(self) -> None:
        root = self._fixture()
        contract = root / verifier.POLARIS_CONTRACT
        contract.write_text(
            contract.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        self._assert_code(root, "POLARIS_ADMISSION", "build contract bytes")

    def test_postgresql_candidate_digest_mutation_fails_closed(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["candidate"]["arm64_reference"] = (  # type: ignore[index]
                "cgr.dev/chainguard/postgres@sha256:" + "0" * 64
            )

        self._rewrite_json(root, verifier.POSTGRES_ADMISSION, mutate)
        self._assert_code(root, "POSTGRES_ADMISSION", "arm64_reference")

    def test_postgresql_evidence_contract_mutation_fails_closed(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["evidence_contract"]["paths"]["signature_bundle"] = (  # type: ignore[index]
                "bootstrap/postgresql/v18.4/evidence/self-asserted.json"
            )

        self._rewrite_json(root, verifier.POSTGRES_ADMISSION, mutate)
        self._assert_code(root, "POSTGRES_ADMISSION", "signature_bundle")

    def test_partial_resident_ledger_admission_fails_closed(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["images"].append({"component": "postgresql"})  # type: ignore[union-attr]

        self._rewrite_json(root, verifier.RESIDENT_LEDGER, mutate)
        self._assert_code(root, "LEDGER_BLOCK", "postgresql")

    def test_resident_ledger_alias_admission_fails_closed(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["images"].append({"component": "postgres"})  # type: ignore[union-attr]

        self._rewrite_json(root, verifier.RESIDENT_LEDGER, mutate)
        self._assert_code(root, "LEDGER_BLOCK", "postgres")

    def test_resident_ledger_reference_alias_fails_closed(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["images"].append(  # type: ignore[union-attr]
                {
                    "component": "metadata-store",
                    "reference": verifier.POSTGRES_ARM64,
                }
            )

        self._rewrite_json(root, verifier.RESIDENT_LEDGER, mutate)
        self._assert_code(root, "LEDGER_BLOCK", "metadata-store")

    def test_alternate_postgres_repository_in_ledger_fails_closed(self) -> None:
        for reference in (
            "docker.io/library/postgres@sha256:" + "0" * 64,
            "docker.io/bitnami/postgresql@sha256:" + "1" * 64,
        ):
            with self.subTest(reference=reference):
                root = self._fixture()

                def mutate(value: dict[str, object]) -> None:
                    value["images"].append(  # type: ignore[union-attr]
                        {
                            "component": "metadata-store",
                            "reference": reference,
                        }
                    )

                self._rewrite_json(root, verifier.RESIDENT_LEDGER, mutate)
                self._assert_code(root, "LEDGER_BLOCK", "metadata-store")

    def test_non_object_postgres_ledger_entry_fails_closed(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["images"].append(  # type: ignore[union-attr]
                "docker.io/library/postgres@sha256:" + "0" * 64
            )

        self._rewrite_json(root, verifier.RESIDENT_LEDGER, mutate)
        self._assert_code(root, "LEDGER_BLOCK", "must be an object")

    def test_neutral_catalog_image_in_ledger_fails_closed(self) -> None:
        cases = (
            (
                "catalog-service",
                "registry.example/neutral@sha256:" + "0" * 64,
            ),
            (
                "metadata-service",
                "registry.example/iceberg-rest@sha256:" + "1" * 64,
            ),
            (
                "catalog-service",
                "registry.example/iceberg-rest@sha256:" + "2" * 64,
            ),
            (
                "catalogservice",
                "registry.example/neutral@sha256:" + "3" * 64,
            ),
            (
                "IcebergCatalog",
                "registry.example/neutral@sha256:" + "4" * 64,
            ),
            (
                "metadata-service",
                "registry.example/icebergrest@sha256:" + "5" * 64,
            ),
            (
                "metastoredb",
                "registry.example/neutral@sha256:" + "6" * 64,
            ),
        )
        for component, reference in cases:
            with self.subTest(component=component, reference=reference):
                root = self._fixture()

                def mutate(value: dict[str, object]) -> None:
                    value["images"].append(  # type: ignore[union-attr]
                        {
                            "component": component,
                            "reference": reference,
                        }
                    )

                self._rewrite_json(root, verifier.RESIDENT_LEDGER, mutate)
                self._assert_code(root, "LEDGER_BLOCK", component)

    def test_unrelated_ledger_identity_is_not_a_catalog_match(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["images"].append(  # type: ignore[union-attr]
                {
                    "component": "unrelated-service",
                    "reference": (
                        "registry.example/unrelated@sha256:" + "0" * 64
                    ),
                }
            )

        self._rewrite_json(root, verifier.RESIDENT_LEDGER, mutate)
        verifier._audit_ledger(root)

    def test_runtime_manifest_before_atomic_admission_fails_closed(self) -> None:
        root = self._fixture()
        manifest = root / "deploy/gitops/catalog/deployment.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n"
            "  name: catalog-api\n"
            "spec:\n"
            "  template:\n"
            "    spec:\n"
            "      containers:\n"
            "        - name: api\n"
            "          image: ghcr.io/example/polaris@sha256:"
            + "0" * 64
            + "\n",
            encoding="utf-8",
        )
        self._assert_code(root, "RUNTIME_BLOCK", "deployment.yaml")

    def test_catalog_path_without_component_words_fails_closed(self) -> None:
        root = self._fixture()
        manifest = root / "deploy/gitops/catalog/secret.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "apiVersion: v1\n"
            "kind: Secret\n"
            "metadata:\n"
            "  name: catalog-db\n"
            "stringData:\n"
            "  username: catalog\n",
            encoding="utf-8",
        )
        self._assert_code(root, "RUNTIME_BLOCK", "secret.yaml")

    def test_catalog_path_with_alternate_suffix_fails_closed(self) -> None:
        root = self._fixture()
        manifest = root / "deploy/gitops/catalog/secret.txt"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "kind=Secret\n"
            "name=catalog-db\n"
            "username=catalog\n",
            encoding="utf-8",
        )
        self._assert_code(root, "RUNTIME_BLOCK", "secret.txt")

    def test_alternate_suffix_content_identity_fails_closed(self) -> None:
        root = self._fixture()
        manifest = root / "deploy/gitops/metadata/database.txt"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "engine=postgresql\n",
            encoding="utf-8",
        )
        self._assert_code(root, "RUNTIME_BLOCK", "database.txt")

    def test_neutral_path_postgres_secret_fails_closed(self) -> None:
        root = self._fixture()
        manifest = root / "deploy/gitops/iceberg/db-secret.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "apiVersion: v1\n"
            "kind: Secret\n"
            "metadata:\n"
            "  name: db\n"
            "stringData:\n"
            "  PGHOST: database\n"
            "  PGPASSWORD: example\n",
            encoding="utf-8",
        )
        self._assert_code(root, "RUNTIME_BLOCK", "db-secret.yaml")

    def test_path_neutral_postgres_assignment_fails_closed(self) -> None:
        root = self._fixture()
        manifest = root / "deploy/gitops/lakehouse/settings.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: settings\n"
            "data:\n"
            "  PGHOST: database\n",
            encoding="utf-8",
        )
        self._assert_code(root, "RUNTIME_BLOCK", "settings.yaml")

    def test_compact_json_postgres_assignment_fails_closed(self) -> None:
        root = self._fixture()
        manifest = root / "deploy/gitops/lakehouse/settings.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            '{"kind":"ConfigMap","data":{"PGHOST":"database"}}\n',
            encoding="utf-8",
        )
        self._assert_code(root, "RUNTIME_BLOCK", "settings.json")

    def test_inline_yaml_postgres_assignment_fails_closed(self) -> None:
        root = self._fixture()
        manifest = root / "deploy/gitops/lakehouse/settings.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "kind: ConfigMap\n"
            "data: {PGHOST: database}\n",
            encoding="utf-8",
        )
        self._assert_code(root, "RUNTIME_BLOCK", "settings.yaml")

    def test_path_neutral_secret_manifest_fails_closed(self) -> None:
        root = self._fixture()
        manifest = root / "deploy/gitops/lakehouse/settings.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "apiVersion: v1\n"
            "kind: Secret\n"
            "metadata:\n"
            "  name: settings\n"
            "stringData:\n"
            "  username: catalog\n"
            "  password: example\n",
            encoding="utf-8",
        )
        self._assert_code(root, "RUNTIME_BLOCK", "settings.yaml")

    def test_plural_secret_path_fails_closed(self) -> None:
        root = self._fixture()
        manifest = root / "deploy/gitops/secrets/settings.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: settings\n",
            encoding="utf-8",
        )
        self._assert_code(root, "RUNTIME_BLOCK", "settings.yaml")

    def test_secret_bearing_crd_kinds_fail_closed(self) -> None:
        for kind in (
            "SealedSecret",
            "ExternalSecret",
            "ClusterExternalSecret",
            "SecretProviderClass",
            "SecretStore",
        ):
            with self.subTest(kind=kind):
                root = self._fixture()
                manifest = root / "deploy/gitops/lakehouse/settings.yaml"
                manifest.parent.mkdir(parents=True)
                manifest.write_text(
                    "apiVersion: example.io/v1\n"
                    f"kind: {kind}\n"
                    "metadata:\n"
                    "  name: settings\n",
                    encoding="utf-8",
                )
                self._assert_code(root, "RUNTIME_BLOCK", "settings.yaml")

    def test_yaml_list_item_secret_kind_fails_closed(self) -> None:
        root = self._fixture()
        manifest = root / "deploy/gitops/lakehouse/settings.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "apiVersion: v1\n"
            "items:\n"
            "  - kind: Secret\n"
            "    metadata:\n"
            "      name: settings\n",
            encoding="utf-8",
        )
        self._assert_code(root, "RUNTIME_BLOCK", "settings.yaml")

    def test_postgres_environment_string_forms_fail_closed(self) -> None:
        cases = {
            "yaml-list": (
                "settings.yaml",
                "environment:\n"
                '  - "PGHOST=database"\n',
            ),
            "shell-export": (
                "settings.env",
                "export PGPASSWORD=example\n",
            ),
            "compact-json-array": (
                "settings.json",
                '{"environment":["PGHOST=database"]}\n',
            ),
        }
        for name, (filename, content) in cases.items():
            with self.subTest(name=name):
                root = self._fixture()
                manifest = root / "deploy/gitops/lakehouse" / filename
                manifest.parent.mkdir(parents=True)
                manifest.write_text(content, encoding="utf-8")
                self._assert_code(root, "RUNTIME_BLOCK", filename)

    def test_neutral_path_catalog_deployment_fails_closed(self) -> None:
        root = self._fixture()
        manifest = root / "deploy/gitops/lakehouse/rest.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n"
            "  name: iceberg-catalog\n"
            "spec:\n"
            "  template:\n"
            "    spec:\n"
            "      containers:\n"
            "        - name: rest\n"
            "          image: registry.example/iceberg-rest@sha256:"
            + "0" * 64
            + "\n",
            encoding="utf-8",
        )
        self._assert_code(root, "RUNTIME_BLOCK", "rest.yaml")

    def test_catalog_identity_fields_fail_closed(self) -> None:
        cases = {
            "name-only.yaml": (
                "kind: Deployment\n"
                "metadata:\n"
                "  name: iceberg-catalog\n"
                "image: registry.example/neutral\n"
            ),
            "image-only.yaml": (
                "kind: Deployment\n"
                "metadata:\n"
                "  name: neutral\n"
                "image: registry.example/iceberg-rest\n"
            ),
            "compact.json": (
                '{"kind":"Deployment","metadata":{"name":"catalog-service"}}\n'
            ),
            "workload.tf": (
                'resource "kubernetes_deployment_v1" "neutral" {\n'
                "  metadata {\n"
                '    name = "iceberg-catalog"\n'
                "  }\n"
                "}\n"
            ),
            "block-name.yaml": (
                "kind: Deployment\n"
                "metadata:\n"
                "  name: >-\n"
                "    iceberg-catalog\n"
            ),
            "block-image.yaml": (
                "kind: Deployment\n"
                "image: |\n"
                "  registry.example/iceberg-rest\n"
            ),
            "anchor-name.yaml": (
                "kind: Deployment\n"
                "metadata:\n"
                "  annotations:\n"
                "    runtime-name: &runtime_name iceberg-catalog\n"
                "  name: *runtime_name\n"
            ),
        }
        for filename, content in cases.items():
            with self.subTest(filename=filename):
                root = self._fixture()
                manifest = root / "deploy/gitops/lakehouse" / filename
                manifest.parent.mkdir(parents=True)
                manifest.write_text(content, encoding="utf-8")
                self._assert_code(root, "RUNTIME_BLOCK", filename)

    def test_neutral_opentofu_secret_resources_fail_closed(self) -> None:
        cases = {
            "db.tf": 'resource "kubernetes_secret_v1" "db" {}\n',
            "db.tofu": (
                'resource\n  "kubernetes_secret_v1_data"\n  "db"\n  {}\n'
            ),
            "db.tf.json": (
                '{"resource":{"kubernetes_secret_v1":{"db":{"data":{}}}}}\n'
            ),
            "db.tofu.json": (
                '{"resource":{"kubernetes_secret_v1_data":{"db":{}}}}\n'
            ),
            "db-block-after-resource.tf": (
                'resource /* formatting */ "kubernetes_secret_v1" "db" {}\n'
            ),
            "db-line-after-type.tofu": (
                'resource "kubernetes_secret_v1" // formatting\n'
                '  "db" {}\n'
            ),
            "db-hash-before-body.tf": (
                'resource "kubernetes_secret_v1" "db" # formatting\n'
                "  {}\n"
            ),
            "db-block-before-body.tofu": (
                'resource "kubernetes_secret_v1" "db" /* formatting */ {}\n'
            ),
            "db-template-comment-markers.tf": (
                "locals {\n"
                '  start = "${format("%s", "/*")}"\n'
                "}\n"
                'resource "kubernetes_secret_v1" "db" {}\n'
                "locals {\n"
                '  end = "${format("%s", "*/")}"\n'
                "}\n"
            ),
            "db-escaped-label.tf": (
                'resource "kubernetes_secret\\u005fv1" "db" {}\n'
            ),
            "db-bom.tf": (
                "\ufeffresource \"kubernetes_secret_v1\" \"db\" {}\n"
            ),
        }
        for filename, content in cases.items():
            with self.subTest(filename=filename):
                root = self._fixture()
                manifest = root / "opentofu/dev" / filename
                manifest.parent.mkdir(parents=True)
                manifest.write_text(content, encoding="utf-8")
                self._assert_code(root, "RUNTIME_BLOCK", filename)

    def test_approved_opentofu_secret_file_is_exact_hash_only(self) -> None:
        root = self._fixture()
        relative = "opentofu/dev/object-storage.tf"
        destination = root / relative
        destination.parent.mkdir(parents=True)
        shutil.copy2(ROOT / relative, destination)
        verifier.audit(root)

        destination.write_text(
            destination.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        self._assert_code(root, "RUNTIME_BLOCK", relative)

    def test_benign_opentofu_resource_is_not_a_secret(self) -> None:
        root = self._fixture()
        manifest = root / "opentofu/dev/namespace.tf"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            'resource "kubernetes_namespace_v1" "lakehouse" {}\n',
            encoding="utf-8",
        )
        verifier.audit(root)

    def test_opentofu_secret_examples_in_non_code_are_ignored(self) -> None:
        cases = {
            "hash-comment.tf": (
                '# resource "kubernetes_secret_v1" "db" {}\n'
            ),
            "slash-comment.tf": (
                '// resource "kubernetes_secret_v1" "db" {}\n'
            ),
            "block-comment.tf": (
                "/*\n"
                'resource "kubernetes_secret_v1" "db" {}\n'
                "*/\n"
            ),
            "heredoc.tf": (
                "locals {\n"
                "  note = <<EOT\n"
                'resource "kubernetes_secret_v1" "db" {}\n'
                "EOT\n"
                "}\n"
            ),
            "unicode-heredoc.tf": (
                "locals {\n"
                "  note = <<Ü\n"
                'resource "kubernetes_secret_v1" "db" {}\n'
                "Ü\n"
                "}\n"
            ),
            "template-comment-markers.tf": (
                "locals {\n"
                '  start = "${format("%s", "/*")}"\n'
                '  end = "${format("%s", "*/")}"\n'
                "}\n"
            ),
        }
        for filename, content in cases.items():
            with self.subTest(filename=filename):
                root = self._fixture()
                manifest = root / "opentofu/dev" / filename
                manifest.parent.mkdir(parents=True)
                manifest.write_text(content, encoding="utf-8")
                verifier.audit(root)

    def test_disabled_iceberg_flag_is_not_a_catalog_identity_field(self) -> None:
        root = self._fixture()
        relative = "deploy/gitops/object-storage/statefulset.yaml"
        destination = root / relative
        destination.parent.mkdir(parents=True)
        shutil.copy2(ROOT / relative, destination)
        verifier.audit(root)

    def test_disabled_iceberg_flag_exception_is_exactly_bounded(self) -> None:
        approved_line = "            - -s3.port.iceberg=0"
        cases = {
            "wrong-path": (
                "deploy/gitops/other/statefulset.yaml",
                approved_line + "\n",
            ),
            "duplicate": (
                "deploy/gitops/object-storage/statefulset.yaml",
                approved_line + "\n" + approved_line + "\n",
            ),
            "changed-value": (
                "deploy/gitops/object-storage/statefulset.yaml",
                approved_line.replace("=0", "=1") + "\n",
            ),
        }
        for label, (relative, content) in cases.items():
            with self.subTest(label=label):
                root = self._fixture()
                destination = root / relative
                destination.parent.mkdir(parents=True)
                destination.write_text(content, encoding="utf-8")
                self._assert_code(root, "RUNTIME_BLOCK", relative)

    def test_postgres_credential_prose_is_not_an_assignment(self) -> None:
        root = self._fixture()
        note = root / "deploy/notes.txt"
        note.parent.mkdir(parents=True)
        note.write_text(
            "documentation: PGHOST is configured externally\n",
            encoding="utf-8",
        )
        verifier.audit(root)


if __name__ == "__main__":
    unittest.main()
