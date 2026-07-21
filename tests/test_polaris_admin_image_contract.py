from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from typing import Callable
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
VERIFIER_PATH = ROOT / "scripts/verify_polaris_admin_image.py"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verifier = _load_module("verify_polaris_admin_image", VERIFIER_PATH)


class PolarisAdminImageContractTests(unittest.TestCase):
    @staticmethod
    def _contract() -> dict:
        return json.loads((ROOT / verifier.CONTRACT_PATH).read_text(encoding="utf-8"))

    @staticmethod
    def _mutated_contract(mutator: Callable[[dict], None]) -> dict:
        value = PolarisAdminImageContractTests._contract()
        mutator(value)
        return value

    def _temporary_root(self, *paths: Path) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for relative in paths:
            source = ROOT / relative
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, destination)
            else:
                shutil.copy2(source, destination)
        return root

    def _evidence_root(self) -> Path:
        return self._temporary_root(verifier.EVIDENCE_PATH)

    def _release_root(self) -> Path:
        return self._temporary_root(verifier.RELEASE_EVIDENCE_PATH)

    def _admission_root(self) -> Path:
        return self._temporary_root(
            verifier.ADMISSION_PATH,
            verifier.ADMISSION_EVIDENCE_PATH,
            verifier.RESIDENT_IMAGE_LEDGER,
        )

    def _assert_contract_code(
        self, expected: str, mutator: Callable[[dict], None]
    ) -> verifier.ContractError:
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._validate_contract(self._mutated_contract(mutator))
        self.assertEqual(expected, raised.exception.code)
        return raised.exception

    def _mutate_evidence_json(
        self,
        filename: str,
        mutator: Callable[[dict], None],
        expected: str,
    ) -> verifier.ContractError:
        root = self._evidence_root()
        path = root / verifier.EVIDENCE_PATH / filename
        document = json.loads(path.read_text(encoding="utf-8"))
        mutator(document)
        path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_evidence_semantics(root)
        self.assertEqual(expected, raised.exception.code)
        return raised.exception

    def test_repository_contract_is_semantically_exact(self) -> None:
        verifier._validate_contract(self._contract())

    def test_repository_static_reviewed_evidence_audit_passes(self) -> None:
        verifier.audit_publication_bootstrap(ROOT)

    def test_full_audit_delegates_to_both_crypto_boundaries(self) -> None:
        dependency_crypto = mock.Mock()
        image_crypto = mock.Mock()
        verifier.audit(
            ROOT,
            dependency_crypto_auditor=dependency_crypto,
            image_crypto_auditor=image_crypto,
        )
        dependency_crypto.assert_called_once_with(ROOT.resolve())
        image_crypto.assert_called_once_with(ROOT.resolve())

    def test_static_cli_does_not_enter_full_crypto_boundary(self) -> None:
        stdout = io.StringIO()
        with mock.patch.object(
            verifier, "_audit_admin_dependency_crypto", autospec=True
        ) as dependency_crypto, mock.patch.object(
            verifier, "_audit_admin_image_crypto", autospec=True
        ) as image_crypto:
            with contextlib.redirect_stdout(stdout):
                result = verifier.main(
                    ["audit-publication-bootstrap", "--root", str(ROOT)]
                )
        self.assertEqual(0, result)
        dependency_crypto.assert_not_called()
        image_crypto.assert_not_called()
        self.assertIn("static reviewed Admin image evidence verified", stdout.getvalue())

    def test_image_crypto_reverifies_signature_and_all_attestations(self) -> None:
        with mock.patch.object(verifier, "_run_cosign", autospec=True) as run:
            verifier._audit_admin_image_crypto(ROOT)
        self.assertEqual(4, run.call_count)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual("verify-blob", commands[0][1])
        self.assertEqual(
            [
                "slsaprovenance1",
                "cyclonedx",
                "https://shirokuma.dev/attestations/trivy/v1",
            ],
            [command[command.index("--type") + 1] for command in commands[1:]],
        )
        for command in commands:
            self.assertIn(verifier.EXPECTED_WORKFLOW_IDENTITY, command)
            self.assertIn(verifier.EXPECTED_PUBLISHER_SOURCE_SHA, command)
            self.assertEqual(
                str(
                    ROOT
                    / verifier.EVIDENCE_PATH
                    / "anonymous-image-manifest.json"
                ),
                command[-1],
            )

    def test_lifecycle_cannot_skip_admin_runtime_activation(self) -> None:
        self._assert_contract_code(
            "LIFECYCLE_STATE",
            lambda value: value["lifecycle"].__setitem__(
                "state", "admin_runtime_acceptance_pending"
            ),
        )

    def test_image_publication_is_retired_and_exact_digest_bound(self) -> None:
        for key, value in (
            ("enabled", True),
            ("state", "pending_main_publication"),
            ("reference", "ghcr.io/example/admin@sha256:" + "0" * 64),
            ("digest", "sha256:" + "0" * 64),
        ):
            with self.subTest(key=key):
                self._assert_contract_code(
                    "IMAGE_IDENTITY",
                    lambda document, key=key, value=value: document[
                        "image_publication"
                    ].__setitem__(key, value),
                )

    def test_release_evidence_contract_binding_is_exact(self) -> None:
        self._assert_contract_code(
            "IMAGE_IDENTITY",
            lambda value: value["image_publication"]["release_evidence"].__setitem__(
                "sha256", "0" * 64
            ),
        )

    def test_reviewed_dependency_stays_non_admitted(self) -> None:
        self._assert_contract_code(
            "DEPENDENCY_IDENTITY",
            lambda value: value["dependency_snapshot"].__setitem__("admitted", True),
        )

    def test_nosql_mongo_surface_cannot_be_hidden_or_activated(self) -> None:
        for mutator in (
            lambda value: value["admin_dependency_surface"].__setitem__(
                "relational_only", True
            ),
            lambda value: value["admin_dependency_surface"].__setitem__(
                "runtime_activation_permitted", True
            ),
            lambda value: value["admin_dependency_surface"][
                "required_sbom_terms"
            ].remove("mongodb"),
        ):
            self._assert_contract_code("ADMIN_SURFACE", mutator)

    def test_evidence_inventory_is_exactly_34_payloads_plus_manifest(self) -> None:
        self._assert_contract_code(
            "EVIDENCE_POLICY",
            lambda value: value["evidence"].__setitem__(
                "directory_file_count_after_review", 34
            ),
        )
        self._assert_contract_code(
            "EVIDENCE_POLICY",
            lambda value: value["evidence"]["candidate_required"].pop(),
        )

    def test_only_admission_and_resident_ledger_gates_are_open(self) -> None:
        for section, key, opened in (
            ("admission", "permitted", False),
            ("runtime", "enabled", True),
            ("gitops", "resources_enabled", True),
            ("credentials", "material_permitted", True),
            ("downstream_gates", "admin_image_admitted", False),
            ("downstream_gates", "resident_image_ledger_enabled", False),
        ):
            with self.subTest(section=section, key=key):
                self._assert_contract_code(
                    "DOWNSTREAM_GATE",
                    lambda value, section=section, key=key, opened=opened: value[
                        section
                    ].__setitem__(key, opened),
                )

    def test_retained_evidence_inventory_and_manifest_pass(self) -> None:
        verifier._audit_evidence_inventory(ROOT)

    def test_admission_evidence_and_resident_ledger_pass(self) -> None:
        verifier._audit_downstream_files(ROOT)

    def test_admission_evidence_inventory_and_bytes_fail_closed(self) -> None:
        root = self._admission_root()
        extra = root / verifier.ADMISSION_EVIDENCE_PATH / "unexpected.json"
        extra.write_text("{}\n", encoding="utf-8")
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_downstream_files(root)
        self.assertEqual("ADMIN_ADMISSION_EVIDENCE", raised.exception.code)

        root = self._admission_root()
        preflight = (
            root / verifier.ADMISSION_EVIDENCE_PATH / "anonymous-preflight.json"
        )
        preflight.write_text("{}\n", encoding="utf-8")
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_downstream_files(root)
        self.assertEqual("ADMIN_ADMISSION_EVIDENCE", raised.exception.code)

    def test_admin_resident_ledger_reference_fails_closed(self) -> None:
        root = self._admission_root()
        ledger = root / verifier.RESIDENT_IMAGE_LEDGER
        value = json.loads(ledger.read_text(encoding="utf-8"))
        admin = next(
            image
            for image in value["images"]
            if image["component"] == "polaris-admin"
        )
        admin["reference"] = "ghcr.io/example/admin@sha256:" + "0" * 64
        ledger.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_downstream_files(root)
        self.assertEqual("ADMIN_ADMISSION_LEDGER", raised.exception.code)

    def test_missing_and_extra_evidence_fail_closed(self) -> None:
        root = self._evidence_root()
        (root / verifier.EVIDENCE_PATH / "admin-help.json").unlink()
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_evidence_inventory(root)
        self.assertEqual("EVIDENCE_INVENTORY", raised.exception.code)

        root = self._evidence_root()
        (root / verifier.EVIDENCE_PATH / "extra.json").write_text("{}\n")
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_evidence_inventory(root)
        self.assertEqual("EVIDENCE_INVENTORY", raised.exception.code)

    def test_evidence_symlink_fails_closed(self) -> None:
        root = self._evidence_root()
        path = root / verifier.EVIDENCE_PATH / "admin-help.json"
        path.unlink()
        path.symlink_to(ROOT / verifier.EVIDENCE_PATH / "admin-help.json")
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_evidence_inventory(root)
        self.assertEqual("EVIDENCE_INVENTORY", raised.exception.code)

    def test_self_referential_or_rebound_manifest_fails_closed(self) -> None:
        root = self._evidence_root()
        manifest = root / verifier.EVIDENCE_PATH / "evidence.sha256"
        manifest.write_text(
            manifest.read_text(encoding="utf-8")
            + f"{'0' * 64}  ./evidence.sha256\n",
            encoding="utf-8",
        )
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_evidence_inventory(root)
        self.assertEqual("EVIDENCE_MANIFEST", raised.exception.code)

    def test_release_evidence_semantics_and_bytes_are_exact(self) -> None:
        verifier._audit_release_evidence(ROOT)
        root = self._release_root()
        path = root / verifier.RELEASE_EVIDENCE_PATH
        document = json.loads(path.read_text(encoding="utf-8"))
        document["admitted"] = True
        path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
        with mock.patch.object(
            verifier, "EXPECTED_RELEASE_EVIDENCE_SHA256", verifier._sha256(path)
        ), mock.patch.object(
            verifier, "EXPECTED_RELEASE_EVIDENCE_SIZE", path.stat().st_size
        ):
            with self.assertRaises(verifier.ContractError) as raised:
                verifier._audit_release_evidence(root)
        self.assertEqual("RELEASE_EVIDENCE", raised.exception.code)

    def test_publication_run_and_downstream_gates_are_immutable(self) -> None:
        self._mutate_evidence_json(
            "publication.json",
            lambda value: value["workflow"].__setitem__("run_id", "1"),
            "PUBLICATION_EVIDENCE",
        )
        self._mutate_evidence_json(
            "publication.json",
            lambda value: value["downstream_gates"].__setitem__(
                "admin_runtime_enabled", True
            ),
            "PUBLICATION_EVIDENCE",
        )

    def test_signature_identity_is_immutable(self) -> None:
        self._mutate_evidence_json(
            "cosign-verify.json",
            lambda value: value["certificate_constraints"].__setitem__(
                "identity", "https://example.invalid/workflow"
            ),
            "SIGNATURE_EVIDENCE",
        )

    def test_cli_evidence_cannot_claim_credentials_or_network(self) -> None:
        self._mutate_evidence_json(
            "admin-help.json",
            lambda value: value.__setitem__("credentials_supplied", True),
            "CLI_EVIDENCE",
        )
        self._mutate_evidence_json(
            "admin-bootstrap-help.json",
            lambda value: value.__setitem__("credential_file_read", True),
            "CLI_EVIDENCE",
        )

    def test_sbom_requires_disclosed_nosql_and_mongodb_surface(self) -> None:
        self._mutate_evidence_json(
            "polaris-admin-1.6.0-arm64.cdx.json",
            lambda value: value.__setitem__("components", []),
            "SBOM_EVIDENCE",
        )

    def test_trivy_high_or_critical_finding_fails_closed(self) -> None:
        def add_high(value: dict) -> None:
            value["Results"][0]["Vulnerabilities"] = [
                {"Severity": "HIGH", "VulnerabilityID": "CVE-test"}
            ]

        self._mutate_evidence_json("trivy.json", add_high, "TRIVY_EVIDENCE")

    def test_retired_image_publisher_cannot_be_restored(self) -> None:
        root = self._temporary_root(
            verifier.CONTRACT_PATH,
            verifier.SOURCE_PATH,
            verifier.ADMIN_INPUT_CONTRACT_PATH,
            verifier.ADMIN_INPUT_VERIFIER_PATH,
        )
        workflow = root / verifier.WORKFLOW_PATH
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_text("name: restored\n", encoding="utf-8")
        with self.assertRaises(verifier.ContractError) as raised:
            verifier.audit_publication_bootstrap(root)
        self.assertEqual("RETIRED_PUBLISHER", raised.exception.code)

    def test_missing_admission_ledger_and_premature_gitops_are_rejected(self) -> None:
        root = self._temporary_root()
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_downstream_files(root)
        self.assertEqual("ADMIN_ADMISSION", raised.exception.code)

        root = self._temporary_root(
            verifier.ADMISSION_PATH,
            verifier.ADMISSION_EVIDENCE_PATH,
        )
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_downstream_files(root)
        self.assertEqual("ADMIN_ADMISSION_LEDGER", raised.exception.code)

        root = self._admission_root()
        manifest = root / "deploy/gitops/catalog/deployment.yaml"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("image: shirokuma-polaris-admin\n", encoding="utf-8")
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_downstream_files(root)
        self.assertEqual("PREMATURE_GITOPS", raised.exception.code)

    def test_downstream_guard_rejects_symlinks(self) -> None:
        root = self._temporary_root()
        admission = root / verifier.ADMISSION_PATH
        admission.parent.mkdir(parents=True, exist_ok=True)
        admission.symlink_to(root / "missing")
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_downstream_files(root)
        self.assertEqual("ADMIN_ADMISSION", raised.exception.code)

        root = self._admission_root()
        gitops = root / "deploy/gitops"
        gitops.parent.mkdir(parents=True, exist_ok=True)
        gitops.symlink_to(root / "missing-gitops", target_is_directory=True)
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_downstream_files(root)
        self.assertEqual("PREMATURE_GITOPS", raised.exception.code)


if __name__ == "__main__":
    unittest.main()
