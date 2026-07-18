from __future__ import annotations

import base64
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

    def test_alternate_bootstrap_namespaces_are_forbidden_while_pending(
        self,
    ) -> None:
        cases = {
            "bootstrap/polaris/v1.6.1/Containerfile": (
                "FROM scratch\n",
                "v1.6.1/Containerfile",
            ),
            "bootstrap/polaris/v1.6.1/Dockerfile": (
                "FROM scratch\n",
                "v1.6.1/Dockerfile",
            ),
            "bootstrap/polaris/v1.6.1/gradle-dependency-inputs.json": (
                "{}\n",
                "v1.6.1/gradle-dependency-inputs.json",
            ),
            "bootstrap/polaris/staging/release-evidence.json": (
                "{}\n",
                "staging/release-evidence.json",
            ),
            "bootstrap/postgresql/v18.5/admission.json": (
                "{}\n",
                "v18.5/admission.json",
            ),
            "bootstrap/polaris-build/v1.6.1/Containerfile": (
                "FROM scratch\n",
                "polaris-build",
            ),
            "bootstrap/postgres-build/v18.5/Containerfile": (
                "FROM scratch\n",
                "postgres-build",
            ),
            "bootstrap/archive/polaris-build/v1.6.1/Containerfile": (
                "FROM scratch\n",
                "archive/polaris-build",
            ),
            "bootstrap/pol/aris/v1.6.1/Containerfile": (
                "FROM scratch\n",
                "pol/aris",
            ),
            "bootstrap/post/gre/s/v18.5/Containerfile": (
                "FROM scratch\n",
                "post/gre/s",
            ),
            "bootstrap/pοlaris-build/v1.6.1/Containerfile": (
                "FROM scratch\n",
                "pοlaris-build",
            ),
            "bootstrap/polaris-Containerfile": (
                "FROM scratch\n",
                "polaris-Containerfile",
            ),
            "bootstrap/archive/polaris-Containerfile": (
                "FROM scratch\n",
                "polaris-Containerfile",
            ),
            "bootstrap/polaris-build/v1.6.1/payload.bin": (
                "opaque build input\n",
                "polaris-build",
            ),
        }
        for relative, (content, detail) in cases.items():
            with self.subTest(relative=relative):
                root = self._fixture()
                candidate = root / relative
                candidate.parent.mkdir(parents=True, exist_ok=True)
                candidate.write_text(content, encoding="utf-8")
                self._assert_code(
                    root,
                    "FORBIDDEN_PATH",
                    detail,
                )

    def test_unrelated_bootstrap_document_names_are_allowed(self) -> None:
        root = self._fixture()
        cases = {
            "bootstrap/seaweedfs/v4.39/docs/postgresql-compatibility.md": (
                "Compatibility notes only.\n"
            ),
            "bootstrap/flux/v2.9.2/docs/polaris-migration-notes.md": (
                "Migration notes only.\n"
            ),
            "bootstrap/flux/v2.9.2/資料/README.md": "Reference notes only.\n",
        }
        for relative, content in cases.items():
            document = root / relative
            document.parent.mkdir(parents=True, exist_ok=True)
            document.write_text(content, encoding="utf-8")
        verifier.audit(root)

    def test_release_evidence_is_forbidden_while_pending(self) -> None:
        root = self._fixture()
        evidence = root / "bootstrap/polaris/v1.6.0/evidence/claim.json"
        evidence.write_text("{}\n", encoding="utf-8")
        self._assert_code(root, "FORBIDDEN_PATH", "evidence/claim.json")

    def test_pending_retained_evidence_paths_fail_closed(self) -> None:
        cases = (
            "security/evidence/polaris-v1.6.0/supply-chain.json",
            "security/evidence/postgresql-v18.4/supply-chain.json",
            "security/evidence/catalog-service/supply-chain.json",
            "security/evidence/catalogService/supply-chain.json",
            "security/evidence/polarisarchive/supply-chain.bin",
        )
        for relative in cases:
            with self.subTest(relative=relative):
                root = self._fixture()
                evidence = root / relative
                evidence.parent.mkdir(parents=True, exist_ok=True)
                evidence.write_text("{}\n", encoding="utf-8")
                self._assert_code(
                    root,
                    "FORBIDDEN_PATH",
                    str(Path(relative).parent),
                )

    def test_pending_retained_evidence_subjects_fail_closed(self) -> None:
        cases = {
            "polaris-subject": {
                "images": [
                    {
                        "component": "metadata-service",
                        "reference": (
                            "ghcr.io/tommykammy/shirokuma-polaris@sha256:"
                            + "0" * 64
                        ),
                    }
                ]
            },
            "postgres-subject": {
                "images": [
                    {
                        "component": "metadata-service",
                        "reference": (
                            "docker.io/library/postgres@sha256:" + "1" * 64
                        ),
                    }
                ]
            },
            "catalog-subject": {
                "subject": [
                    {
                        "name": (
                            "registry.example/iceberg-rest@sha256:" + "2" * 64
                        )
                    }
                ]
            },
            "root-component-reference": {
                "component": "polaris",
                "reference": "registry.example/metadata-service@sha256:"
                + "3" * 64,
            },
            "root-repository": {
                "repository": "docker.io/library/postgres",
                "digest": "sha256:" + "4" * 64,
            },
            "cosign-docker-reference": {
                "critical": {
                    "identity": {
                        "docker-reference": (
                            "ghcr.io/tommykammy/shirokuma-polaris"
                        )
                    }
                }
            },
            "spdx-root-name": {
                "spdxVersion": "SPDX-2.3",
                "name": "polaris-1.6.0-image",
                "packages": [],
            },
            "root-array": [
                {
                    "component": "polaris",
                    "reference": (
                        "registry.example/metadata-service@sha256:" + "6" * 64
                    ),
                }
            ],
            "compact-polaris-subject": {
                "component": "polarisarchive",
                "reference": "registry.example/metadata@sha256:" + "7" * 64,
            },
            "compact-postgres-subject": {
                "component": "postgresarchive",
                "reference": "registry.example/database@sha256:" + "8" * 64,
            },
            "versioned-postgresql-subject": {
                "component": "postgresql18",
                "reference": "registry.example/database@sha256:" + "9" * 64,
            },
            "versioned-polaris-subject": {
                "component": "polaris16",
                "reference": "registry.example/metadata@sha256:" + "a" * 64,
            },
            "postgres-service-subject": {
                "component": "postgresservice",
                "reference": "registry.example/database@sha256:" + "b" * 64,
            },
        }
        for index, (label, document) in enumerate(cases.items()):
            with self.subTest(label=label):
                root = self._fixture()
                evidence = (
                    root
                    / "security/evidence/lakehouse-v1"
                    / f"claim-{index}.json"
                )
                evidence.parent.mkdir(parents=True, exist_ok=True)
                evidence.write_text(
                    json.dumps(document, indent=2) + "\n",
                    encoding="utf-8",
                )
                self._assert_code(
                    root,
                    "FORBIDDEN_PATH",
                    evidence.name,
                )

    def test_pending_dsse_subject_fails_closed(self) -> None:
        root = self._fixture()
        statement = {
            "_type": "https://in-toto.io/Statement/v1",
            "subject": [
                {
                    "name": "ghcr.io/tommykammy/shirokuma-polaris",
                    "digest": {"sha256": "c" * 64},
                }
            ],
            "predicateType": "https://slsa.dev/provenance/v1",
            "predicate": {},
        }
        envelope = {
            "payloadType": "application/vnd.in-toto+json",
            "payload": base64.b64encode(
                json.dumps(statement).encode("utf-8")
            ).decode("ascii"),
            "signatures": [],
        }
        bundle = {
            "mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json",
            "dsseEnvelope": envelope,
            "verificationMaterial": {},
        }
        evidence = root / "security/evidence/lakehouse-v1/attestation.json"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(json.dumps(bundle) + "\n", encoding="utf-8")
        self._assert_code(root, "FORBIDDEN_PATH", "attestation.json")

    def test_unrelated_dsse_subject_is_allowed(self) -> None:
        root = self._fixture()
        statement = {
            "_type": "https://in-toto.io/Statement/v1",
            "subject": [
                {
                    "name": "registry.example/unrelated-controller",
                    "digest": {"sha256": "d" * 64},
                }
            ],
            "predicateType": "https://slsa.dev/provenance/v1",
            "predicate": {},
        }
        envelope = {
            "payloadType": "application/vnd.in-toto+json",
            "payload": base64.b64encode(
                json.dumps(statement).encode("utf-8")
            ).decode("ascii"),
            "signatures": [],
        }
        evidence = root / "security/evidence/unrelated-v1/attestation.json"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(json.dumps(envelope) + "\n", encoding="utf-8")
        verifier.audit(root)

    def test_unknown_retained_evidence_format_fails_closed(self) -> None:
        root = self._fixture()
        evidence = root / "security/evidence/unrelated-v1/attestation.yaml"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(
            "subject: registry.example/unrelated\n",
            encoding="utf-8",
        )
        self._assert_code(
            root,
            "FORBIDDEN_PATH",
            "unsupported retained evidence format",
        )

    def test_pending_markdown_evidence_fails_closed(self) -> None:
        root = self._fixture()
        evidence = root / "security/evidence/misc/receipt.md"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(
            "# Release receipt\n\n"
            "Subject: "
            "ghcr.io/tommykammy/shirokuma-polaris@sha256:"
            + "e" * 64
            + "\n",
            encoding="utf-8",
        )
        self._assert_code(root, "FORBIDDEN_PATH", "receipt.md")

    def test_unrelated_markdown_evidence_is_allowed(self) -> None:
        root = self._fixture()
        evidence = root / "security/evidence/misc/receipt.md"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(
            "# Release receipt\n\n"
            "Subject: registry.example/unrelated-controller@sha256:"
            + "f" * 64
            + "\n",
            encoding="utf-8",
        )
        verifier.audit(root)

    def test_unrelated_retained_evidence_subjects_are_allowed(self) -> None:
        root = self._fixture()
        evidence = root / "security/evidence/unrelated-v1/supply-chain.json"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(
            json.dumps(
                {
                    "metadata": {
                        "component": {
                            "name": "unrelated-controller",
                            "purl": "pkg:oci/unrelated-controller@sha256:abc",
                        }
                    },
                    "components": [
                        {
                            "name": "postgresql",
                            "purl": "pkg:generic/postgresql@18",
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        verifier.audit(root)

    def test_unrelated_retained_evidence_subject_substrings_are_allowed(
        self,
    ) -> None:
        for index, component in enumerate(
            (
                "uncatalogued-settings",
                "catalogue-ui",
                "myicebergish",
                "polarisation-engine",
                "shirokuma-polarisation",
            )
        ):
            with self.subTest(component=component):
                root = self._fixture()
                evidence = (
                    root
                    / "security/evidence/unrelated-v1"
                    / f"claim-{index}.json"
                )
                evidence.parent.mkdir(parents=True, exist_ok=True)
                evidence.write_text(
                    json.dumps(
                        {
                            "component": component,
                            "reference": (
                                f"registry.example/{component}@sha256:"
                                + "5" * 64
                            ),
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                verifier.audit(root)

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

    def test_catalog_markers_inside_runtime_path_segments_fail_closed(
        self,
    ) -> None:
        for segment in (
            "iceberg-catalog",
            "catalog-service",
            "icebergCatalog",
            "catalogservice",
            "icebergcatalog",
            "catalog2",
            "catalogcontroller",
            "cataloggateway",
            "catalogoperator",
            "catalogworker",
            "metastore-db",
            "metastoreDB",
        ):
            with self.subTest(segment=segment):
                root = self._fixture()
                manifest = root / "deploy/gitops" / segment / "deployment.yaml"
                manifest.parent.mkdir(parents=True)
                manifest.write_text(
                    "apiVersion: apps/v1\n"
                    "kind: Deployment\n"
                    "metadata:\n"
                    "  name: neutral\n",
                    encoding="utf-8",
                )
                self._assert_code(root, "RUNTIME_BLOCK", segment)

    def test_unrelated_runtime_path_substrings_are_allowed(self) -> None:
        for segment in (
            "uncatalogued-settings",
            "catalogue-ui",
            "myicebergish",
        ):
            with self.subTest(segment=segment):
                root = self._fixture()
                manifest = root / "deploy/gitops" / segment / "deployment.yaml"
                manifest.parent.mkdir(parents=True)
                manifest.write_text(
                    "apiVersion: apps/v1\n"
                    "kind: Deployment\n"
                    "metadata:\n"
                    "  name: neutral\n",
                    encoding="utf-8",
                )
                verifier.audit(root)

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

    def test_encoded_tagged_and_explicit_secret_kinds_fail_closed(
        self,
    ) -> None:
        cases = {
            "encoded-key.yaml": (
                "apiVersion: v1\n"
                '"k\\u0069nd": Secret\n'
                "metadata:\n"
                "  name: settings\n"
            ),
            "tagged-key.yaml": (
                "apiVersion: v1\n"
                "!!str kind: Secret\n"
                "metadata:\n"
                "  name: settings\n"
            ),
            "explicit-key.yaml": (
                "apiVersion: v1\n"
                "? kind\n"
                ": Secret\n"
                "metadata:\n"
                "  name: settings\n"
            ),
            "encoded-json-key.json": (
                '{"apiVersion":"v1","k\\u0069nd":"Secret",'
                '"metadata":{"name":"settings"}}\n'
            ),
            "aliased-kind.yaml": (
                "apiVersion: v1\n"
                "secret_kind: &k Secret\n"
                "kind: *k\n"
                "metadata:\n"
                "  name: settings\n"
                "stringData:\n"
                "  username: example\n"
                "  password: example\n"
            ),
            "flow-aliased-kind.yaml": (
                "apiVersion: v1\n"
                "metadata:\n"
                "  name: settings\n"
                "  labels: {type: &k Secret}\n"
                "kind: *k\n"
                "stringData: {password: example}\n"
            ),
        }
        for filename, content in cases.items():
            with self.subTest(filename=filename):
                root = self._fixture()
                manifest = root / "deploy/gitops/lakehouse" / filename
                manifest.parent.mkdir(parents=True)
                manifest.write_text(content, encoding="utf-8")
                self._assert_code(root, "RUNTIME_BLOCK", filename)

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

    def test_block_scalar_secret_kinds_fail_closed(self) -> None:
        cases = {
            "object.yaml": (
                "apiVersion: v1\n"
                "kind: >-\n"
                "  Secret\n"
                "stringData:\n"
                "  username: example\n"
                "  password: example\n"
            ),
            "bundle.yaml": (
                "apiVersion: example.io/v1\n"
                "items:\n"
                "  - kind: |2-\n"
                "      ExternalSecret\n"
                "    metadata:\n"
                "      name: settings\n"
            ),
            "tagged.yaml": (
                "apiVersion: v1\n"
                "kind: !!str >-\n"
                "  Secret\n"
            ),
            "anchored.yaml": (
                "apiVersion: example.io/v1\n"
                "kind: &kind >-\n"
                "  ExternalSecret\n"
            ),
            "escaped-key.yaml": (
                "apiVersion: v1\n"
                '"k\\u0069nd": >-\n'
                "  Secret\n"
            ),
            "explicit-key.yaml": (
                "apiVersion: v1\n"
                "? kind\n"
                ": >-\n"
                "  Secret\n"
            ),
            "tagged-explicit-key.yaml": (
                "apiVersion: v1\n"
                "? !!str kind\n"
                ": >-\n"
                "  Secret\n"
            ),
            "tagged-implicit-key.yaml": (
                "apiVersion: v1\n"
                "!!str kind: >-\n"
                "  Secret\n"
            ),
        }
        for filename, content in cases.items():
            with self.subTest(filename=filename):
                root = self._fixture()
                manifest = root / "deploy/gitops/lakehouse" / filename
                manifest.parent.mkdir(parents=True)
                manifest.write_text(content, encoding="utf-8")
                self._assert_code(root, "RUNTIME_BLOCK", filename)

    def test_non_secret_block_scalars_are_allowed(self) -> None:
        cases = {
            "configmap.yaml": "kind: >-\n  ConfigMap\n",
            "description.yaml": "description: >-\n  Secret\nkind: ConfigMap\n",
            "clip-chomping.yaml": "kind: >\n  Secret\n",
            "leading-blank.yaml": "kind: >-\n\n  Secret\n",
            "nested-configmap-data.yaml": (
                "apiVersion: v1\n"
                "kind: ConfigMap\n"
                "data:\n"
                "  kind: >-\n"
                "    Secret\n"
            ),
            "aliased-configmap.yaml": (
                "apiVersion: v1\n"
                "kind_name: &k ConfigMap\n"
                "kind: *k\n"
                "metadata:\n"
                "  name: settings\n"
            ),
        }
        for filename, content in cases.items():
            with self.subTest(filename=filename):
                root = self._fixture()
                manifest = root / "deploy/gitops/lakehouse" / filename
                manifest.parent.mkdir(parents=True)
                manifest.write_text(content, encoding="utf-8")
                verifier.audit(root)

    def test_kustomize_secret_generators_fail_closed(self) -> None:
        cases = {
            "block-yaml": (
                "apiVersion: kustomize.config.k8s.io/v1beta1\n"
                "kind: Kustomization\n"
                "secretGenerator:\n"
                "  - name: database\n"
                "    literals:\n"
                "      - username=example\n"
                "      - password=example\n"
            ),
            "flow-json": (
                '{"secretGenerator":[{"name":"database","literals":'
                '["username=example","password=example"]}]}\n'
            ),
            "escaped-json-key": (
                '{"secret\\u0047enerator":[{"name":"database"}]}\n'
            ),
            "explicit-yaml-key": (
                "apiVersion: kustomize.config.k8s.io/v1beta1\n"
                "kind: Kustomization\n"
                "? secretGenerator\n"
                ": []\n"
            ),
            "escaped-explicit-yaml-key": (
                "apiVersion: kustomize.config.k8s.io/v1beta1\n"
                "kind: Kustomization\n"
                '? "secret\\x47enerator"\n'
                ": []\n"
            ),
            "tagged-explicit-yaml-key": (
                "apiVersion: kustomize.config.k8s.io/v1beta1\n"
                "kind: Kustomization\n"
                "? !!str secretGenerator\n"
                ": []\n"
            ),
            "anchored-explicit-yaml-key": (
                "apiVersion: kustomize.config.k8s.io/v1beta1\n"
                "kind: Kustomization\n"
                "? &sg secretGenerator\n"
                ": []\n"
            ),
            "tagged-implicit-yaml-key": (
                "apiVersion: kustomize.config.k8s.io/v1beta1\n"
                "kind: Kustomization\n"
                "!!str secretGenerator: []\n"
            ),
            "globally-indented-yaml-key": (
                "  apiVersion: kustomize.config.k8s.io/v1beta1\n"
                "  kind: Kustomization\n"
                "  secretGenerator: []\n"
            ),
            "flow-yaml-key": (
                "{secretGenerator: [], "
                "configMapGenerator: [{name: settings}]}\n"
            ),
            "inline-document-flow-yaml-key": (
                "--- {secretGenerator: [], "
                "configMapGenerator: [{name: settings}]}\n"
            ),
            "quoted-flow-yaml-key": (
                "{'secretGenerator': [], "
                "'configMapGenerator': [{'name': 'settings'}]}\n"
            ),
            "escaped-yaml-key": (
                "apiVersion: kustomize.config.k8s.io/v1beta1\n"
                "kind: Kustomization\n"
                '"secret\\u0047enerator": []\n'
            ),
        }
        for label, content in cases.items():
            with self.subTest(label=label):
                root = self._fixture()
                manifest = root / "deploy/gitops/lakehouse/kustomization.yaml"
                manifest.parent.mkdir(parents=True)
                manifest.write_text(content, encoding="utf-8")
                self._assert_code(root, "RUNTIME_BLOCK", "kustomization.yaml")

    def test_non_secret_kustomize_generator_text_is_allowed(self) -> None:
        cases = {
            "yaml": (
                "apiVersion: v1\n"
                "kind: ConfigMap\n"
                "# secretGenerator: disabled while pending\n"
                'notes: [first, "secretGenerator: disabled"]\n'
                "note: |-\n"
                "  secretGenerator: disabled\n"
                "data:\n"
                "  secretGenerator: disabled\n"
                "  flow: {secretGenerator: disabled}\n"
                "secret-generator: disabled\n"
                "configMapGenerator:\n"
                "  - name: settings\n"
            ),
            "json": (
                '{"apiVersion":"v1","kind":"ConfigMap","data":'
                '{"secretGenerator":"disabled"}}\n'
            ),
        }
        for suffix, content in cases.items():
            with self.subTest(suffix=suffix):
                root = self._fixture()
                manifest = (
                    root
                    / "deploy/gitops/lakehouse"
                    / f"kustomization.{suffix}"
                )
                manifest.parent.mkdir(parents=True)
                manifest.write_text(content, encoding="utf-8")
                verifier.audit(root)

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
