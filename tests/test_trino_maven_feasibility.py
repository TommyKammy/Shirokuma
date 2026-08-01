from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_trino_maven_feasibility as feasibility  # noqa: E402


class TrinoMavenFeasibilityTests(unittest.TestCase):
    def _repository(self, root: Path) -> Path:
        repository = root / "repository"
        for relative in feasibility.EXPECTED_REPLACEMENT_INPUTS:
            target = repository / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(relative.encode("utf-8"))
        metadata = repository / "org/example/demo/1.0/demo-1.0.pom"
        metadata.parent.mkdir(parents=True)
        metadata.write_text("<project/>\n", encoding="utf-8")
        (repository / "org.example.index").write_text(
            "prefix ordering sentinel\n", encoding="utf-8"
        )
        return repository

    def _successful_log(self, path: Path) -> None:
        path.write_text(
            "[INFO] org.apache.velocity:velocity-engine-core:jar:2.4.1\n"
            "[INFO] org.codehaus.plexus:plexus-utils:jar:4.0.3\n"
            "[INFO] BUILD SUCCESS\n",
            encoding="utf-8",
        )

    def test_workflow_is_read_only_and_fail_closed(self) -> None:
        feasibility.audit_workflow(ROOT)

    def test_candidate_applies_only_to_the_retained_pom_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            checkout = temporary_root / "checkout"
            checkout.mkdir()
            baseline = gzip.decompress(
                (ROOT / feasibility.publisher.BLOCKER_BASELINE_PATH).read_bytes()
            )
            (checkout / "pom.xml").write_bytes(baseline)
            subprocess.run(["git", "init"], cwd=checkout, check=True)
            subprocess.run(["git", "add", "pom.xml"], cwd=checkout, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.invalid",
                    "commit",
                    "-m",
                    "baseline",
                ],
                cwd=checkout,
                check=True,
                capture_output=True,
            )
            with mock.patch.object(
                feasibility.publisher,
                "apply_source_overlay",
            ):
                feasibility.apply_candidate(ROOT, checkout)
            self.assertEqual(
                feasibility._sha256(checkout / "pom.xml"),
                feasibility.EXPECTED_POSTIMAGE_SHA256,
            )
            self.assertEqual(
                subprocess.run(
                    ["git", "diff", "--name-only", "HEAD"],
                    cwd=checkout,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.splitlines(),
                ["pom.xml"],
            )

    def test_capture_repository_is_complete_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = self._repository(root)
            first = root / "first"
            second = root / "second"
            feasibility.capture_repository(repository, first)
            feasibility.capture_repository(repository, second)
            self.assertEqual(
                feasibility._sha256(first / feasibility.MANIFEST_NAME),
                feasibility._sha256(second / feasibility.MANIFEST_NAME),
            )
            self.assertEqual(
                feasibility._sha256(first / feasibility.ARCHIVE_NAME),
                feasibility._sha256(second / feasibility.ARCHIVE_NAME),
            )
            manifest = json.loads(
                (first / feasibility.MANIFEST_NAME).read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["file_count"], 4)
            self.assertEqual(
                [record["path"] for record in manifest["files"]],
                sorted(record["path"] for record in manifest["files"]),
            )

    def test_finalize_retains_online_offline_and_input_identities(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = self._repository(root)
            evidence = root / "evidence"
            feasibility.capture_repository(repository, evidence)
            online = root / "online.log"
            offline = root / "offline.log"
            self._successful_log(online)
            self._successful_log(offline)
            toolchain = root / "toolchain.json"
            toolchain.write_text(
                json.dumps({"schema_version": 1, "result": "passed"}) + "\n",
                encoding="utf-8",
            )
            environment = {
                "GITHUB_REPOSITORY": "TommyKammy/Shirokuma",
                "GITHUB_RUN_ID": "123456",
                "GITHUB_RUN_ATTEMPT": "1",
                "GITHUB_SERVER_URL": "https://github.com",
                "GITHUB_SHA": "a" * 40,
                "REVIEWED_COMMIT": "b" * 40,
                "GITHUB_WORKFLOW": "Trino 483 Maven remediation feasibility",
                "GITHUB_WORKFLOW_REF": (
                    "TommyKammy/Shirokuma/.github/workflows/"
                    "trino-maven-remediation-feasibility.yml@refs/pull/1/merge"
                ),
                "RUNNER_ARCH": "ARM64",
                "RUNNER_OS": "Linux",
            }
            with mock.patch.dict(os.environ, environment, clear=False):
                feasibility.finalize_record(
                    evidence,
                    online,
                    offline,
                    toolchain,
                )
            feasibility.audit_evidence(evidence, require_archive=True)
            record = json.loads(
                (evidence / feasibility.RECORD_NAME).read_text(encoding="utf-8")
            )
            self.assertTrue(
                record["offline_inputs"]["reproducible_inputs_retained"]
            )
            self.assertEqual(record["subject"]["reviewed_commit"], "b" * 40)
            self.assertEqual(
                record["subject"]["workflow_execution_commit"], "a" * 40
            )
            self.assertFalse(record["boundary"]["publication_permitted"])
            self.assertTrue(record["result"]["owner_decision_still_required"])

    def test_vulnerable_coordinate_in_either_log_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "online.log"
            self._successful_log(path)
            path.write_text(
                path.read_text(encoding="utf-8")
                + "[INFO] org.codehaus.plexus:plexus-utils:jar:4.0.2\n",
                encoding="utf-8",
            )
            self.assertTrue(feasibility._vulnerable_lines(path))

    def test_archive_or_manifest_tamper_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / "evidence"
            feasibility.capture_repository(self._repository(root), evidence)
            # A complete record is not needed to prove that deterministic
            # inputs change identity when repository content changes.
            original = feasibility._sha256(evidence / feasibility.ARCHIVE_NAME)
            archive = evidence / feasibility.ARCHIVE_NAME
            archive.write_bytes(archive.read_bytes() + b"tamper")
            self.assertNotEqual(original, feasibility._sha256(archive))


if __name__ == "__main__":
    unittest.main()
