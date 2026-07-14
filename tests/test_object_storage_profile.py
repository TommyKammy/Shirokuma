from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_INDEX_REFERENCE = (
    "chrislusf/seaweedfs@"
    "sha256:c7d6c721b30ae711db766bbbfd40192776e263d4e51e22f57baef7bef93c12c6"
)
EXPECTED_MANIFEST_DIGEST = (
    "sha256:22fe8c99253508a3d4bf2fb3c66130d9c3e238506b42c41aa3aee3bfbe3a6906"
)
EXPECTED_RELEASE_COMMIT = "db42bb49757b459551607939807017d7a9d5a94a"
EXPECTED_RELEASE_TREE = "da91641fdd520e465c68fa48af3b3ad07ad86822"
BLOCKED_GITOPS_MARKERS = ("seaweedfs", "object-storage", "object_storage")


class ObjectStorageProfileContractTests(unittest.TestCase):
    def test_trusted_arm64_source_build_contract_is_present(self) -> None:
        evidence_path = ROOT / "bootstrap/seaweedfs/v4.39/source.json"
        workflow_path = ROOT / ".github/workflows/seaweedfs-arm64.yml"
        containerfile_path = ROOT / "bootstrap/seaweedfs/v4.39/Containerfile"
        decision_path = (
            ROOT
            / "docs/design/07_ADR/ADR-0020_Adopt_SeaweedFS_4_39_source_for_arm64_build.md"
        )

        required_paths = (evidence_path, workflow_path, containerfile_path, decision_path)
        missing = [path.relative_to(ROOT).as_posix() for path in required_paths if not path.is_file()]
        self.assertFalse(missing, f"missing trusted build contract: {', '.join(missing)}")

        source = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(source["version"], "4.39")
        self.assertEqual(source["commit"], EXPECTED_RELEASE_COMMIT)
        self.assertEqual(source["tree"], EXPECTED_RELEASE_TREE)
        self.assertRegex(source["git_archive_sha256"], r"^[0-9a-f]{64}$")

        workflow = workflow_path.read_text(encoding="utf-8")
        for required in (
            "permissions:",
            "contents: read",
            "packages: write",
            "id-token: write",
            "attestations: write",
            "linux/arm64",
            "cosign sign --yes",
            "actions/attest-build-provenance@",
            "cyclonedx-json",
            "severity: HIGH,CRITICAL",
            EXPECTED_RELEASE_COMMIT,
            EXPECTED_RELEASE_TREE,
        ):
            with self.subTest(required=required):
                self.assertIn(required, workflow)

        action_refs = re.findall(r"^\s*uses:\s*([^\s#]+)", workflow, re.MULTILINE)
        self.assertTrue(action_refs)
        for action_ref in action_refs:
            with self.subTest(action_ref=action_ref):
                self.assertRegex(action_ref, r"^[^@]+@[0-9a-f]{40}$")
        self.assertNotRegex(workflow, r"secrets\.(COSIGN|SIGNING|PRIVATE_KEY)")

    def test_blocked_candidate_is_recorded_without_runtime_manifests(self) -> None:
        admission_path = ROOT / "bootstrap/seaweedfs/v4.39/admission.json"
        self.assertTrue(admission_path.is_file())
        admission = json.loads(admission_path.read_text(encoding="utf-8"))

        self.assertEqual(admission["schema_version"], 1)
        self.assertEqual(admission["component"], "seaweedfs")
        self.assertEqual(admission["version"], "4.39")
        self.assertEqual(admission["platform"], "linux/arm64")
        self.assertEqual(admission["assessment"]["admission"], "blocked")
        self.assertIs(admission["assessment"]["exception_eligible"], False)
        self.assertIs(admission["runtime_manifests"]["permitted"], False)

        self.assertEqual(
            admission["candidate"]["index_reference"],
            EXPECTED_INDEX_REFERENCE,
        )
        self.assertEqual(
            admission["candidate"]["manifest_digest"],
            EXPECTED_MANIFEST_DIGEST,
        )

        blockers = admission["assessment"]["blockers"]
        self.assertEqual(
            {blocker["control"] for blocker in blockers},
            {"signature", "source_revision_signature", "slsa_provenance"},
        )
        for blocker in blockers:
            with self.subTest(control=blocker["control"]):
                self.assertEqual(blocker["status"], "missing")
                self.assertTrue(blocker["evidence"].strip())

        for relative_path in admission["runtime_manifests"]["paths"]:
            path = ROOT / relative_path
            with self.subTest(runtime_manifest=relative_path):
                self.assertFalse(path.exists(), f"blocked candidate emitted {relative_path}")

        gitops_root = ROOT / "deploy/gitops"
        blocked_gitops_matches = []
        for path in gitops_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".json", ".yaml", ".yml"}:
                continue
            relative_path = path.relative_to(ROOT).as_posix()
            searchable = f"{relative_path}\n{path.read_text(encoding='utf-8')}".casefold()
            matched_markers = [
                marker for marker in BLOCKED_GITOPS_MARKERS if marker in searchable
            ]
            if matched_markers:
                blocked_gitops_matches.append(
                    f"{relative_path} ({', '.join(matched_markers)})"
                )
        self.assertEqual(
            blocked_gitops_matches,
            [],
            "blocked SeaweedFS/object-storage resources found in GitOps tree: "
            + "; ".join(blocked_gitops_matches),
        )

        resident_ledger_path = ROOT / "security/resident-images.json"
        resident_ledger = json.loads(resident_ledger_path.read_text(encoding="utf-8"))
        blocked_resident_entries = []
        for image in resident_ledger["images"]:
            searchable = "\n".join(
                str(image.get(field, ""))
                for field in ("component", "source", "reference")
            ).casefold()
            if "seaweedfs" in searchable or EXPECTED_MANIFEST_DIGEST in searchable:
                blocked_resident_entries.append(image.get("component", "<unknown>"))
        self.assertEqual(
            blocked_resident_entries,
            [],
            "blocked SeaweedFS candidate found in resident image ledger: "
            + ", ".join(blocked_resident_entries),
        )

        self.assertEqual(
            admission["next_action"]["mode"],
            "approved-source-build-or-signed-upstream-release",
        )
        self.assertIs(admission["next_action"]["decision_record_required"], True)
        self.assertGreaterEqual(len(admission["next_action"]["requirements"]), 4)


if __name__ == "__main__":
    unittest.main()
