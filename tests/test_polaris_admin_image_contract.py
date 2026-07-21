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
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)
        return root

    def _assert_contract_code(
        self, expected: str, mutator: Callable[[dict], None]
    ) -> verifier.ContractError:
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._validate_contract(self._mutated_contract(mutator))
        self.assertEqual(expected, raised.exception.code)
        return raised.exception

    def _assert_workflow_code(
        self,
        expected: str,
        replacement: Callable[[str], str],
    ) -> verifier.ContractError:
        root = self._temporary_root(verifier.WORKFLOW_PATH)
        path = root / verifier.WORKFLOW_PATH
        path.write_text(replacement(path.read_text(encoding="utf-8")), encoding="utf-8")
        with mock.patch.object(
            verifier, "EXPECTED_WORKFLOW_SHA256", verifier._sha256(path)
        ):
            with self.assertRaises(verifier.ContractError) as raised:
                verifier._audit_workflow(root)
        self.assertEqual(expected, raised.exception.code)
        return raised.exception

    def test_repository_contract_is_semantically_exact(self) -> None:
        verifier._validate_contract(self._contract())

    def test_repository_static_publication_audit_passes(self) -> None:
        verifier.audit_publication_bootstrap(ROOT)

    def test_full_audit_delegates_to_crypto_boundary(self) -> None:
        crypto = mock.Mock()
        verifier.audit(ROOT, dependency_crypto_auditor=crypto)
        crypto.assert_called_once_with(ROOT.resolve())

    def test_static_cli_does_not_enter_full_crypto_boundary(self) -> None:
        stdout = io.StringIO()
        with mock.patch.object(
            verifier, "_audit_admin_dependency_crypto", autospec=True
        ) as crypto:
            with contextlib.redirect_stdout(stdout):
                result = verifier.main(
                    ["audit-publication-bootstrap", "--root", str(ROOT)]
                )
        self.assertEqual(0, result)
        crypto.assert_not_called()
        self.assertIn("static Admin image publication policy verified", stdout.getvalue())

    def test_lifecycle_cannot_skip_image_evidence_review(self) -> None:
        self._assert_contract_code(
            "LIFECYCLE_STATE",
            lambda value: value["lifecycle"].__setitem__(
                "next_state", "admin_image_admitted"
            ),
        )

    def test_reviewed_dependency_cannot_claim_admission(self) -> None:
        self._assert_contract_code(
            "DEPENDENCY_IDENTITY",
            lambda value: value["dependency_snapshot"].__setitem__(
                "admitted", True
            ),
        )

    def test_pr87_review_checkpoint_is_immutable(self) -> None:
        replacements = {
            "repository": "other/repository",
            "pull_request": 88,
            "reviewed_head_commit": "0" * 40,
            "merge_commit": "0" * 40,
            "merged_at": "2026-07-21T00:00:00Z",
            "merged_by": "other-user",
            "reviewed_contract_sha256": "0" * 64,
            "reviewed_evidence_manifest_sha256": "0" * 64,
            "reviewed_verifier_sha256": "0" * 64,
        }
        for field, replacement in replacements.items():
            with self.subTest(field=field):
                self._assert_contract_code(
                    "REVIEW_CHECKPOINT",
                    lambda value, field=field, replacement=replacement: value[
                        "dependency_snapshot"
                    ]["review_checkpoint"].__setitem__(field, replacement),
                )

    def test_dependency_bytes_and_offline_task_closure_are_exact(self) -> None:
        mutations = (
            (
                "DEPENDENCY_BYTES",
                lambda value: value["dependency_snapshot"]["archive"].__setitem__(
                    "sha256", "0" * 64
                ),
            ),
            (
                "OFFLINE_POLICY",
                lambda value: value["dependency_snapshot"]["offline_proof"].__setitem__(
                    "gradle_offline", False
                ),
            ),
            (
                "OFFLINE_POLICY",
                lambda value: value["dependency_snapshot"]["offline_proof"][
                    "tasks"
                ].remove(":polaris-server:assemble"),
            ),
        )
        for expected, mutation in mutations:
            with self.subTest(expected=expected):
                self._assert_contract_code(expected, mutation)

    def test_nosql_mongo_surface_cannot_be_hidden_or_activated(self) -> None:
        mutations = (
            lambda value: value["admin_dependency_surface"].__setitem__(
                "relational_only", True
            ),
            lambda value: value["admin_dependency_surface"].__setitem__(
                "runtime_activation_permitted", True
            ),
            lambda value: value["admin_dependency_surface"][
                "unconditional_project_dependencies"
            ].pop(),
            lambda value: value["admin_dependency_surface"][
                "required_sbom_terms"
            ].remove("mongodb"),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self._assert_contract_code("ADMIN_SURFACE", mutation)

    def test_closed_build_context_cannot_gain_server_output_or_symlinks(self) -> None:
        for field in ("server_output_permitted", "symlinks_permitted"):
            with self.subTest(field=field):
                self._assert_contract_code(
                    "BUILD_CONTEXT_POLICY",
                    lambda value, field=field: value["image_publication"][
                        "build_context"
                    ].__setitem__(field, True),
                )
        mutations = (
            lambda value: value["image_publication"]["build_context"].__setitem__(
                "containerfile_name", "Containerfile"
            ),
            lambda value: value["image_publication"]["build_context"][
                "allowed_roots"
            ].__setitem__(0, "Containerfile"),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self._assert_contract_code("BUILD_CONTEXT_POLICY", mutation)

    def test_cli_policy_forbids_credential_bearing_defaults(self) -> None:
        mutations = (
            lambda value: value["image_publication"]["cli"].__setitem__(
                "credential_argument_permitted", True
            ),
            lambda value: value["image_publication"]["cli"].__setitem__(
                "default_arguments", ["bootstrap", "--credential=x"]
            ),
            lambda value: value["image_publication"]["cli"].__setitem__(
                "smoke_commands", [["bootstrap"]]
            ),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self._assert_contract_code("CLI_POLICY", mutation)

    def test_vulnerability_gate_cannot_ignore_unfixed_or_allow_exception(self) -> None:
        for field in ("ignore_unfixed", "exception_permitted"):
            with self.subTest(field=field):
                self._assert_contract_code(
                    "VULNERABILITY_GATE",
                    lambda value, field=field: value["image_publication"][
                        "vulnerability_gate"
                    ].__setitem__(field, True),
                )

    def test_evidence_inventory_is_exactly_30_plus_4(self) -> None:
        contract = self._contract()
        self.assertEqual(30, len(verifier.EXPECTED_CANDIDATE_EVIDENCE))
        self.assertEqual(4, len(verifier.EXPECTED_PROMOTION_EVIDENCE))
        self.assertEqual(
            34,
            contract["evidence"]["checksum_manifest_entries"],
        )
        self._assert_contract_code(
            "EVIDENCE_POLICY",
            lambda value: value["evidence"]["candidate_required"].pop(),
        )
        self._assert_contract_code(
            "EVIDENCE_POLICY",
            lambda value: value["evidence"]["promotion_required"].append(
                "unreviewed.json"
            ),
        )

    def test_all_admission_runtime_flux_and_credential_gates_stay_closed(self) -> None:
        mutations = (
            lambda value: value["admission"].__setitem__("permitted", True),
            lambda value: value["runtime"].__setitem__("enabled", True),
            lambda value: value["gitops"].__setitem__("resources_enabled", True),
            lambda value: value["credentials"].__setitem__(
                "material_permitted", True
            ),
            lambda value: value["downstream_gates"].__setitem__(
                "resident_image_ledger_enabled", True
            ),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self._assert_contract_code("DOWNSTREAM_GATE", mutation)

    def test_containerfile_semantics_survive_hash_rebinding(self) -> None:
        root = self._temporary_root(verifier.CONTAINERFILE_PATH)
        path = root / verifier.CONTAINERFILE_PATH
        path.write_text(
            path.read_text(encoding="utf-8") + "\nEXPOSE 8181\n",
            encoding="utf-8",
        )
        with mock.patch.object(
            verifier, "EXPECTED_CONTAINERFILE_SHA256", verifier._sha256(path)
        ):
            with self.assertRaises(verifier.ContractError) as raised:
                verifier._audit_containerfile(root)
        self.assertEqual("CONTAINERFILE_SEMANTICS", raised.exception.code)

    def test_containerfile_requires_alpine_identity_labels_after_hash_rebinding(
        self,
    ) -> None:
        root = self._temporary_root(verifier.CONTAINERFILE_PATH)
        path = root / verifier.CONTAINERFILE_PATH
        text = path.read_text(encoding="utf-8")
        path.write_text(
            text.replace(
                'dev.shirokuma.runtime-base.os-version="3.24.1"',
                'dev.shirokuma.runtime-base.os-version="3.24"',
                1,
            ),
            encoding="utf-8",
        )
        with mock.patch.object(
            verifier, "EXPECTED_CONTAINERFILE_SHA256", verifier._sha256(path)
        ):
            with self.assertRaises(verifier.ContractError) as raised:
                verifier._audit_containerfile(root)
        self.assertEqual("CONTAINERFILE_SEMANTICS", raised.exception.code)

    def test_workflow_rejects_pr_trigger_after_hash_rebinding(self) -> None:
        self._assert_workflow_code(
            "WORKFLOW_SEMANTICS",
            lambda text: text.replace("on:\n", "on:\n  pull_request:\n", 1),
        )

    def test_workflow_requires_full_commit_action_pins(self) -> None:
        self._assert_workflow_code(
            "ACTION_PIN",
            lambda text: re_sub_first_action(text),
        )

    def test_workflow_prepare_job_cannot_gain_registry_write(self) -> None:
        self._assert_workflow_code(
            "WORKFLOW_PERMISSIONS",
            lambda text: text.replace(
                "      contents: read\n", "      contents: read\n      packages: write\n", 1
            ),
        )

    def test_workflow_requires_static_then_full_audit_in_every_job(self) -> None:
        needle = (
            "python3 scripts/verify_polaris_admin_image.py "
            "audit-publication-bootstrap --root ."
        )
        self._assert_workflow_code(
            "WORKFLOW_AUDIT_ORDER",
            lambda text: text.replace(needle, "python3 -m compileall scripts", 1),
        )

    def test_workflow_requires_global_pending_runtime_audit_before_credentials(
        self,
    ) -> None:
        needle = "python3 scripts/verify_polaris_trusted_image.py audit --root ."
        self._assert_workflow_code(
            "WORKFLOW_AUDIT_ORDER",
            lambda text: text.replace(needle, "python3 -m compileall scripts", 1),
        )

    def test_workflow_emitted_records_cannot_restore_stale_review_state(
        self,
    ) -> None:
        self._assert_workflow_code(
            "WORKFLOW_REVIEW_STATE",
            lambda text: text.replace(
                '"review_state": "reviewed_for_image_publication"',
                '"review_state": "review_required"',
                1,
            ),
        )

    def test_workflow_requires_both_dependency_terms_and_promotion_revalidation(
        self,
    ) -> None:
        self._assert_workflow_code(
            "WORKFLOW_DEPENDENCY_SURFACE",
            lambda text: text.replace(
                'required_terms = ["mongodb", "polaris-persistence-nosql"]',
                'required_terms = ["mongodb"]',
                1,
            ),
        )
        self._assert_workflow_code(
            "WORKFLOW_DEPENDENCY_SURFACE",
            lambda text: text.replace(
                "set(matching_components) != set(required_terms)",
                "False",
                1,
            ),
        )

    def test_workflow_revalidates_runtime_index_arm64_java_and_alpine(self) -> None:
        self._assert_workflow_code(
            "WORKFLOW_RUNTIME_BASE",
            lambda text: text.replace(
                'arm64[0].get("digest") != os.environ["RUNTIME_BASE_DIGEST"]',
                "False",
                1,
            ),
        )
        java_marker = '"openjdk version \\"${RUNTIME_BASE_JAVA_VERSION}\\""'
        self._assert_workflow_code(
            "WORKFLOW_RUNTIME_BASE",
            lambda text: text.replace(java_marker, '"openjdk version 21"'),
        )
        self._assert_workflow_code(
            "WORKFLOW_RUNTIME_BASE",
            lambda text: text.replace(
                'cat /etc/alpine-release > runtime-base-os-version.txt',
                'cat /etc/os-release > runtime-base-os-version.txt',
                1,
            ),
        )
        self._assert_workflow_code(
            "WORKFLOW_RUNTIME_BASE",
            lambda text: text.replace(
                'tr -d \'\\r\\n\' < candidate-evidence/runtime-base-os-version.txt',
                'tr -d \'\\r\\n\' < candidate-evidence/runtime-base-java-version.txt',
                1,
            ),
        )
        self._assert_workflow_code(
            "WORKFLOW_RUNTIME_BASE",
            lambda text: text.replace(
                '"runtime_base_os": os.environ["RUNTIME_BASE_OS"]',
                '"runtime_base_os": "unknown"',
                1,
            ),
        )

    def test_workflow_requires_exact_final_evidence_file_count(self) -> None:
        self._assert_workflow_code(
            "WORKFLOW_EVIDENCE_CLOSURE",
            lambda text: text.replace('              "35"\n', '              "34"\n', 1),
        )

    def test_workflow_stages_checksum_manifest_outside_evidence_directory(self) -> None:
        self._assert_workflow_code(
            "WORKFLOW_EVIDENCE_CLOSURE",
            lambda text: text.replace(
                "mktemp ../evidence.sha256.tmp.XXXXXX",
                "mktemp",
                1,
            ),
        )
        self._assert_workflow_code(
            "WORKFLOW_EVIDENCE_CLOSURE",
            lambda text: text.replace(
                '| xargs -0 sha256sum > "${checksum_manifest}"',
                "| xargs -0 sha256sum > evidence.sha256",
                1,
            ),
        )

    def test_workflow_evidence_and_credential_boundaries_survive_rebinding(self) -> None:
        self._assert_workflow_code(
            "WORKFLOW_SEMANTICS",
            lambda text: text.replace("admin-help.json", "admin-help.txt"),
        )
        self._assert_workflow_code(
            "WORKFLOW_CREDENTIAL_BOUNDARY",
            lambda text: text
            + "\n      run: docker run image bootstrap --credentials-file=/tmp/secret\n",
        )

    def test_workflow_rejects_multiline_credential_bearing_invocations(self) -> None:
        cases = (
            ("docker", "run", "--credential=secret"),
            ("podman", "run", "--credentials-file=/tmp/secret"),
            ("nerdctl", "run", "--print-credentials"),
            ("docker", "create", "--credentials-file=/tmp/secret"),
        )
        for runtime, action, option in cases:
            with self.subTest(runtime=runtime, action=action, option=option):
                self._assert_workflow_code(
                    "WORKFLOW_CREDENTIAL_BOUNDARY",
                    lambda text, runtime=runtime, action=action, option=option: text
                    + "\n      run: |\n"
                    + f"        {runtime} {action} --rm \\\n"
                    + "          image bootstrap \\\n"
                    + f"          {option}\n",
                )

    def test_workflow_rejects_credential_invocations_across_yaml_and_shell_forms(
        self,
    ) -> None:
        cases = (
            '      "run": |\n        docker run image --credentials-file=/tmp/secret\n',
            "      run : |\n        docker run image --credential=secret\n",
            "      run: |\n        bash <<'SH'\n        docker run image --print-credentials\n        SH\n",
            "      run: env docker run image --credentials-file=/tmp/secret\n",
            "      run: command docker create image --credential=secret\n",
            "      run: sudo podman run image --print-credentials\n",
            "      run: /usr/bin/nerdctl create image --credentials-file=/tmp/secret\n",
        )
        for payload in cases:
            with self.subTest(payload=payload.splitlines()[0]):
                self._assert_workflow_code(
                    "WORKFLOW_CREDENTIAL_BOUNDARY",
                    lambda text, payload=payload: text + "\n" + payload,
                )

    def test_python_heredoc_evidence_text_is_not_treated_as_shell(self) -> None:
        commands = verifier._workflow_shell_commands(
            "      run: |\n"
            "        python3 - <<'PY'\n"
            '        print("docker run image --credentials-file=/tmp/example")\n'
            "        PY\n"
        )
        self.assertFalse(
            any(verifier._credential_bearing_container_invocation(c) for c in commands)
        )

    def test_premature_evidence_admission_ledger_and_gitops_are_rejected(self) -> None:
        root = self._temporary_root()
        evidence = root / verifier.FUTURE_EVIDENCE_PATH
        evidence.mkdir(parents=True)
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_downstream_files(root)
        self.assertEqual("PREMATURE_EVIDENCE", raised.exception.code)

        evidence.rmdir()
        admission = root / verifier.FUTURE_ADMISSION_PATH
        admission.parent.mkdir(parents=True, exist_ok=True)
        admission.write_text("{}\n", encoding="utf-8")
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_downstream_files(root)
        self.assertEqual("PREMATURE_ADMISSION", raised.exception.code)

        admission.unlink()
        ledger = root / verifier.RESIDENT_IMAGE_LEDGER
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(verifier.EXPECTED_IMAGE_REPOSITORY, encoding="utf-8")
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_downstream_files(root)
        self.assertEqual("PREMATURE_ADMISSION", raised.exception.code)

        ledger.unlink()
        manifest = root / "deploy/gitops/catalog/deployment.yaml"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("image: shirokuma-polaris-admin\n", encoding="utf-8")
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_downstream_files(root)
        self.assertEqual("PREMATURE_GITOPS", raised.exception.code)

    def test_gitops_guard_fails_closed_on_symlinks(self) -> None:
        root = self._temporary_root()
        gitops = root / "deploy/gitops"
        gitops.mkdir(parents=True)
        target = root / "neutral.yaml"
        target.write_text("kind: ConfigMap\n", encoding="utf-8")
        (gitops / "neutral.yaml").symlink_to(target)
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_downstream_files(root)
        self.assertEqual("PREMATURE_GITOPS", raised.exception.code)

    def test_downstream_guard_rejects_broken_symlink_markers(self) -> None:
        for relative, expected in (
            (verifier.FUTURE_EVIDENCE_PATH, "PREMATURE_EVIDENCE"),
            (verifier.FUTURE_ADMISSION_PATH, "PREMATURE_ADMISSION"),
        ):
            with self.subTest(relative=relative):
                root = self._temporary_root()
                marker = root / relative
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.symlink_to(root / "missing-target")
                with self.assertRaises(verifier.ContractError) as raised:
                    verifier._audit_downstream_files(root)
                self.assertEqual(expected, raised.exception.code)

    def test_gitops_guard_rejects_root_symlink(self) -> None:
        root = self._temporary_root()
        gitops = root / "deploy/gitops"
        gitops.parent.mkdir(parents=True)
        gitops.symlink_to(root / "missing-gitops", target_is_directory=True)
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_downstream_files(root)
        self.assertEqual("PREMATURE_GITOPS", raised.exception.code)


def re_sub_first_action(text: str) -> str:
    import re

    return re.sub(
        r"(?m)^(\s*uses:\s*[^@\s]+)@[0-9a-f]{40}",
        r"\1@main",
        text,
        count=1,
    )


if __name__ == "__main__":
    unittest.main()
