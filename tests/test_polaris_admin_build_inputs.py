from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Callable
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
VERIFIER_PATH = ROOT / "scripts/verify_polaris_admin_build_inputs.py"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verifier = _load_module("verify_polaris_admin_build_inputs", VERIFIER_PATH)


class PolarisAdminBuildInputsTests(unittest.TestCase):
    REQUIRED_FILES = (
        verifier.CONTRACT_PATH,
        verifier.SOURCE_PATH,
        verifier.PARENT_DESCRIPTOR_PATH,
        verifier.PARENT_VERIFICATION_PATH,
        verifier.PACKAGER_PATH,
        verifier.SOURCE_VALIDATOR_PATH,
        *(
            verifier.EVIDENCE_PATH / name
            for name in {"evidence.sha256", *verifier.EVIDENCE_RECORDS}
        ),
    )

    def setUp(self) -> None:
        super().setUp()
        self.crypto_verifier = mock.create_autospec(
            verifier._reverify_sigstore_cryptographically,
            spec_set=True,
        )

    def _copy_root(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for relative in self.REQUIRED_FILES:
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)
        return root

    def _audit(self, root: Path) -> None:
        verifier.audit(root, crypto_verifier=self.crypto_verifier)

    @staticmethod
    def _mutate_json(
        root: Path,
        relative: Path,
        mutator: Callable[[dict], None],
    ) -> None:
        path = root / relative
        value = json.loads(path.read_text(encoding="utf-8"))
        mutator(value)
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def _assert_code(self, root: Path, expected: str) -> verifier.ContractError:
        with self.assertRaises(verifier.ContractError) as raised:
            self._audit(root)
        self.assertEqual(expected, raised.exception.code)
        return raised.exception

    def test_repository_contract_passes_with_mocked_crypto_boundary(self) -> None:
        self._audit(ROOT)
        self.crypto_verifier.assert_called_once()

    def test_default_audit_uses_real_crypto_boundary(self) -> None:
        with mock.patch.object(
            verifier,
            "_reverify_sigstore_cryptographically",
            autospec=True,
        ) as crypto:
            verifier.audit(ROOT)
        crypto.assert_called_once()

    def test_main_reports_reviewed_evidence_state(self) -> None:
        stdout = io.StringIO()
        with mock.patch.object(verifier, "audit") as audit:
            with contextlib.redirect_stdout(stdout):
                result = verifier.main(["audit", "--root", str(ROOT)])
        self.assertEqual(0, result)
        audit.assert_called_once_with(ROOT)
        self.assertIn("one-shot publisher absent", stdout.getvalue())
        self.assertIn("admin image/runtime remain disabled", stdout.getvalue())

    def test_duplicate_contract_key_is_rejected(self) -> None:
        root = self._copy_root()
        path = root / verifier.CONTRACT_PATH
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                '{\n  "schema_version": 2,',
                '{\n  "schema_version": 999,\n  "schema_version": 2,',
                1,
            ),
            encoding="utf-8",
        )
        self._assert_code(root, "JSON_INVALID")

    def test_lifecycle_cannot_skip_admin_image_publication_checkpoint(self) -> None:
        root = self._copy_root()
        self._mutate_json(
            root,
            verifier.CONTRACT_PATH,
            lambda value: value["lifecycle"].__setitem__(
                "next_state", "admin_runtime_admitted"
            ),
        )
        self._assert_code(root, "LIFECYCLE_STATE")

    def test_publication_policy_cannot_survive_publisher_retirement(self) -> None:
        root = self._copy_root()
        self._mutate_json(
            root,
            verifier.CONTRACT_PATH,
            lambda value: value.__setitem__("publication_policy", {}),
        )
        self._assert_code(root, "CONTRACT_SCHEMA")

    def test_evidence_self_manifest_contract_binding_is_exact(self) -> None:
        root = self._copy_root()

        def mutate(value: dict) -> None:
            value["candidate_snapshot"]["retained_evidence"][
                "checksum_manifest"
            ]["sha256"] = "0" * 64

        self._mutate_json(root, verifier.CONTRACT_PATH, mutate)
        self._assert_code(root, "RETAINED_EVIDENCE_CONTRACT")

    def test_actions_artifact_metadata_is_exact(self) -> None:
        root = self._copy_root()

        def mutate(value: dict) -> None:
            value["candidate_snapshot"]["publication"]["actions_artifact"][
                "id"
            ] = 1

        self._mutate_json(root, verifier.CONTRACT_PATH, mutate)
        self._assert_code(root, "PUBLICATION_CONTRACT")

    def test_anonymous_review_receipt_cannot_use_user_credentials(self) -> None:
        root = self._copy_root()

        def mutate(value: dict) -> None:
            value["candidate_snapshot"]["review"]["anonymous_retrieval"][
                "user_credentials"
            ] = True

        self._mutate_json(root, verifier.CONTRACT_PATH, mutate)
        self._assert_code(root, "REVIEW_RECEIPT")

    def test_oras_partial_archive_failure_is_recorded_truthfully(self) -> None:
        root = self._copy_root()

        def mutate(value: dict) -> None:
            value["candidate_snapshot"]["review"]["anonymous_retrieval"][
                "oras_pull"
            ]["archive_completed"] = True

        self._mutate_json(root, verifier.CONTRACT_PATH, mutate)
        self._assert_code(root, "REVIEW_RECEIPT")

    def test_archive_resume_cannot_use_user_credentials(self) -> None:
        root = self._copy_root()

        def mutate(value: dict) -> None:
            value["candidate_snapshot"]["review"]["anonymous_retrieval"][
                "archive_resume"
            ]["user_credentials"] = True

        self._mutate_json(root, verifier.CONTRACT_PATH, mutate)
        self._assert_code(root, "REVIEW_RECEIPT")

    def test_resumed_archive_requires_exact_hash_size_and_gzip(self) -> None:
        root = self._copy_root()

        def mutate(value: dict) -> None:
            value["candidate_snapshot"]["review"]["anonymous_retrieval"][
                "archive"
            ]["gzip_test"] = "failed"

        self._mutate_json(root, verifier.CONTRACT_PATH, mutate)
        self._assert_code(root, "REVIEW_RECEIPT")

    def test_anonymous_descriptor_must_match_retained_evidence(self) -> None:
        root = self._copy_root()

        def mutate(value: dict) -> None:
            value["candidate_snapshot"]["review"]["anonymous_retrieval"][
                "descriptor"
            ]["cmp_with_retained_evidence"] = "failed"

        self._mutate_json(root, verifier.CONTRACT_PATH, mutate)
        self._assert_code(root, "REVIEW_RECEIPT")

    def test_visibility_bootstrap_forbids_user_credential_fallback(self) -> None:
        root = self._copy_root()

        def mutate(value: dict) -> None:
            value["candidate_snapshot"]["visibility_bootstrap"][
                "user_credential_fallback"
            ] = True

        self._mutate_json(root, verifier.CONTRACT_PATH, mutate)
        self._assert_code(root, "REVIEW_TOOLCHAIN")

    def test_anonymous_registry_bearer_challenge_remains_permitted(self) -> None:
        root = self._copy_root()

        def mutate(value: dict) -> None:
            value["candidate_snapshot"]["visibility_bootstrap"][
                "anonymous_registry_v2_bearer_challenge_permitted"
            ] = False

        self._mutate_json(root, verifier.CONTRACT_PATH, mutate)
        self._assert_code(root, "REVIEW_TOOLCHAIN")

    def test_anonymous_review_receipt_binds_exact_manifest(self) -> None:
        root = self._copy_root()

        def mutate(value: dict) -> None:
            value["candidate_snapshot"]["review"]["anonymous_retrieval"][
                "digest"
            ] = "sha256:" + "0" * 64

        self._mutate_json(root, verifier.CONTRACT_PATH, mutate)
        self._assert_code(root, "REVIEW_RECEIPT")

    def test_relational_only_claim_remains_forbidden(self) -> None:
        root = self._copy_root()
        self._mutate_json(
            root,
            verifier.CONTRACT_PATH,
            lambda value: value["admin_dependency_surface"].__setitem__(
                "relational_only", True
            ),
        )
        self._assert_code(root, "ADMIN_SURFACE")

    def test_admin_image_gate_remains_disabled(self) -> None:
        root = self._copy_root()
        self._mutate_json(
            root,
            verifier.CONTRACT_PATH,
            lambda value: value["downstream_gates"].__setitem__(
                "admin_image_publication_enabled", True
            ),
        )
        self._assert_code(root, "DOWNSTREAM_GATE")

    def test_retired_publisher_cannot_be_reintroduced(self) -> None:
        root = self._copy_root()
        workflow = root / verifier.WORKFLOW_PATH
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_text(
            "name: forbidden one-shot publisher\npermissions:\n  packages: write\n",
            encoding="utf-8",
        )
        self._assert_code(root, "PUBLISHER_PRESENT")

    def test_legacy_publisher_cannot_be_reintroduced(self) -> None:
        root = self._copy_root()
        workflow = root / verifier.LEGACY_WORKFLOW_PATH
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_text("name: forbidden legacy publisher\n", encoding="utf-8")
        self._assert_code(root, "LEGACY_WORKFLOW_PRESENT")

    def test_missing_evidence_file_fails_closed(self) -> None:
        root = self._copy_root()
        (root / verifier.EVIDENCE_PATH / "offline-build.json").unlink()
        self._assert_code(root, "EVIDENCE_INVENTORY")

    def test_extra_evidence_file_fails_closed(self) -> None:
        root = self._copy_root()
        (root / verifier.EVIDENCE_PATH / "unreviewed.json").write_text(
            "{}\n", encoding="utf-8"
        )
        self._assert_code(root, "EVIDENCE_INVENTORY")

    def test_symlinked_evidence_file_fails_closed(self) -> None:
        root = self._copy_root()
        evidence = root / verifier.EVIDENCE_PATH / "toolchain.json"
        evidence.unlink()
        evidence.symlink_to("offline-build.json")
        self._assert_code(root, "EVIDENCE_INVENTORY")

    def test_evidence_byte_drift_fails_closed(self) -> None:
        root = self._copy_root()
        evidence = root / verifier.EVIDENCE_PATH / "offline-build.json"
        evidence.write_bytes(evidence.read_bytes() + b"\n")
        self._assert_code(root, "EVIDENCE_BYTES")

    def test_evidence_manifest_rejects_duplicate_filename(self) -> None:
        root = self._copy_root()
        manifest = root / verifier.EVIDENCE_PATH / "evidence.sha256"
        first = manifest.read_text(encoding="utf-8").splitlines()[0]
        manifest.write_text(first + "\n" + first + "\n", encoding="utf-8")
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._parse_checksum_manifest(manifest, "EVIDENCE_MANIFEST")
        self.assertEqual("EVIDENCE_MANIFEST", raised.exception.code)

    def test_oci_layer_order_is_semantically_closed(self) -> None:
        root = self._copy_root()
        path = root / verifier.EVIDENCE_PATH / "oci-manifest.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["layers"].reverse()
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_oci_manifest(root)
        self.assertEqual("OCI_MANIFEST", raised.exception.code)
        self.assertIn("layer order", raised.exception.detail)

    def test_exact_parent_superset_is_recomputed(self) -> None:
        root = self._copy_root()
        path = root / verifier.EVIDENCE_PATH / "gradle-dependency-inputs.json"
        candidate = json.loads(path.read_text(encoding="utf-8"))
        parent = json.loads(
            (root / verifier.PARENT_DESCRIPTOR_PATH).read_text(encoding="utf-8")
        )
        parent_record = next(
            record for record in parent["files"] if record["kind"] == "module-artifact"
        )
        candidate["files"].remove(parent_record)
        path.write_text(json.dumps(candidate), encoding="utf-8")
        packager = SimpleNamespace(_validate_descriptor=lambda *_: None)
        with mock.patch.object(verifier, "_packager_module", return_value=packager):
            with self.assertRaises(verifier.ContractError) as raised:
                verifier._audit_descriptor(root)
        self.assertEqual("SUPERSET", raised.exception.code)

    def test_superset_proof_counts_must_match_computation(self) -> None:
        root = self._copy_root()
        path = root / verifier.EVIDENCE_PATH / "superset-proof.json"
        proof = json.loads(path.read_text(encoding="utf-8"))
        proof["candidate_module_artifact_count"] += 1
        path.write_text(json.dumps(proof), encoding="utf-8")
        packager = SimpleNamespace(_validate_descriptor=lambda *_: None)
        with mock.patch.object(verifier, "_packager_module", return_value=packager):
            with self.assertRaises(verifier.ContractError) as raised:
                verifier._audit_descriptor(root)
        self.assertEqual("SUPERSET", raised.exception.code)
        self.assertIn("computed exact relationship", raised.exception.detail)

    def test_offline_build_cannot_gain_network_access(self) -> None:
        root = self._copy_root()
        self._mutate_json(
            root,
            verifier.EVIDENCE_PATH / "offline-build.json",
            lambda value: value.__setitem__("network", "bridge"),
        )
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_offline_build(root)
        self.assertEqual("OFFLINE_BUILD", raised.exception.code)

    def test_toolchain_gradle_version_is_exact(self) -> None:
        root = self._copy_root()
        self._mutate_json(
            root,
            verifier.EVIDENCE_PATH / "toolchain.json",
            lambda value: value.__setitem__("gradle", "9.6.1"),
        )
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_toolchain(root)
        self.assertEqual("TOOLCHAIN", raised.exception.code)

    def test_publication_workflow_sha_is_exact(self) -> None:
        root = self._copy_root()
        self._mutate_json(
            root,
            verifier.EVIDENCE_PATH / "publication.json",
            lambda value: value.__setitem__("workflow_sha", "0" * 40),
        )
        contract = json.loads((root / verifier.CONTRACT_PATH).read_text())
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_publication(root, contract)
        self.assertEqual("PUBLICATION", raised.exception.code)

    def test_publication_preserves_nosql_mongo_review_surface(self) -> None:
        root = self._copy_root()

        def mutate(value: dict) -> None:
            value["admin_dependency_surface"]["relational_only"] = True

        self._mutate_json(
            root,
            verifier.EVIDENCE_PATH / "publication.json",
            mutate,
        )
        contract = json.loads((root / verifier.CONTRACT_PATH).read_text())
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_publication(root, contract)
        self.assertEqual("ADMIN_SURFACE_EVIDENCE", raised.exception.code)

    def test_cosign_registry_verification_binds_manifest_digest(self) -> None:
        root = self._copy_root()
        path = root / verifier.EVIDENCE_PATH / "cosign-verify.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value[0]["critical"]["image"]["docker-manifest-digest"] = (
            "sha256:" + "0" * 64
        )
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_cosign_verification(root)
        self.assertEqual("COSIGN_EVIDENCE", raised.exception.code)

    def test_slsa_certificate_pins_exact_workflow_sha(self) -> None:
        root = self._copy_root()
        path = root / verifier.EVIDENCE_PATH / "slsa-verify.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value[0]["verificationResult"]["signature"]["certificate"][
            "githubWorkflowSHA"
        ] = "0" * 40
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_slsa(root)
        self.assertEqual("SLSA_EVIDENCE", raised.exception.code)
        self.assertIn("workflow SHA", raised.exception.detail)

    def test_slsa_dsse_payload_must_equal_verified_statement(self) -> None:
        root = self._copy_root()
        path = root / verifier.EVIDENCE_PATH / "slsa-verify.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        envelope = value[0]["attestation"]["bundle"]["dsseEnvelope"]
        payload = json.loads(base64.b64decode(envelope["payload"]))
        payload["predicate"]["runDetails"]["metadata"]["invocationId"] = (
            "https://github.com/TommyKammy/Shirokuma/actions/runs/1/attempts/1"
        )
        envelope["payload"] = base64.b64encode(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_slsa(root)
        self.assertEqual("SLSA_EVIDENCE", raised.exception.code)
        self.assertIn("DSSE payload differs", raised.exception.detail)

    def test_crypto_reverification_uses_exact_identity_and_workflow_sha(self) -> None:
        bundle = verifier._audit_slsa(ROOT)

        def complete(
            command: list[str],
            **_: object,
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    "GitVersion: v3.1.1\n"
                    if command == ["cosign", "version"]
                    else "verified\n"
                ),
                stderr="",
            )

        with mock.patch.object(
            verifier.subprocess,
            "run",
            side_effect=complete,
        ) as run:
            verifier._reverify_sigstore_cryptographically(ROOT, bundle)
        self.assertEqual(3, run.call_count)
        for call in run.call_args_list[1:]:
            command = call.args[0]
            for flag, expected in (
                ("--certificate-identity", verifier.EXPECTED_WORKFLOW_IDENTITY),
                ("--certificate-oidc-issuer", verifier.EXPECTED_ISSUER),
                (
                    "--certificate-github-workflow-repository",
                    verifier.EXPECTED_REPOSITORY,
                ),
                ("--certificate-github-workflow-ref", verifier.EXPECTED_REF),
                ("--certificate-github-workflow-sha", verifier.EXPECTED_SOURCE_SHA),
                ("--certificate-github-workflow-trigger", verifier.EXPECTED_EVENT),
            ):
                self.assertEqual(1, command.count(flag))
                self.assertEqual(expected, command[command.index(flag) + 1])

    def test_cosign_failure_is_fail_closed(self) -> None:
        failed = subprocess.CompletedProcess(
            ["cosign", "verify-blob"],
            1,
            stdout="",
            stderr="invalid signature",
        )
        with mock.patch.object(verifier.subprocess, "run", return_value=failed):
            with self.assertRaises(verifier.ContractError) as raised:
                verifier._run_cosign(ROOT, ["verify-blob"], "signature verification")
        self.assertEqual("SIGSTORE_CRYPTO", raised.exception.code)
        self.assertIn("invalid signature", raised.exception.detail)

    def test_missing_cosign_is_fail_closed(self) -> None:
        bundle = verifier._audit_slsa(ROOT)
        with mock.patch.object(
            verifier.subprocess,
            "run",
            side_effect=FileNotFoundError("cosign"),
        ):
            with self.assertRaises(verifier.ContractError) as raised:
                verifier._reverify_sigstore_cryptographically(ROOT, bundle)
        self.assertEqual("SIGSTORE_CRYPTO", raised.exception.code)
        self.assertIn("cannot inspect Cosign", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
