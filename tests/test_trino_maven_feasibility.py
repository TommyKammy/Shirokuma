from __future__ import annotations

import gzip
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_trino_maven_feasibility as feasibility  # noqa: E402


class TrinoMavenFeasibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.replacement_payloads = {
            path: f"reviewed fixture: {path}\n".encode("utf-8")
            for path in feasibility.EXPECTED_REPLACEMENT_INPUTS
        }
        replacement_metadata = {
            path: {
                "mode": "0644",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            }
            for path, payload in self.replacement_payloads.items()
        }
        self.builder_index_text = (
            json.dumps(
                {
                    "schemaVersion": 2,
                    "manifests": [
                        {
                            "digest": feasibility.EXPECTED_BUILDER_ARM64_MANIFEST,
                            "platform": {
                                "os": "linux",
                                "architecture": "arm64",
                            },
                        }
                    ],
                },
                sort_keys=True,
            )
            + "\n"
        )
        patches = (
            mock.patch.object(
                feasibility,
                "EXPECTED_REPLACEMENT_METADATA",
                replacement_metadata,
            ),
            mock.patch.object(
                feasibility,
                "EXPECTED_BUILDER_INDEX_SHA256",
                hashlib.sha256(
                    self.builder_index_text.encode("utf-8")
                ).hexdigest(),
            ),
        )
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _repository(self, root: Path) -> Path:
        repository = root / "repository"
        for relative in feasibility.EXPECTED_REPLACEMENT_INPUTS:
            target = repository / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.replacement_payloads[relative])
        metadata = repository / "org/example/demo/1.0/demo-1.0.pom"
        metadata.parent.mkdir(parents=True)
        metadata.write_text("<project/>\n", encoding="utf-8")
        (repository / "org.example.index").write_text(
            "prefix ordering sentinel\n", encoding="utf-8"
        )
        scm_pom = repository / feasibility.SCM_POM_PATH
        scm_pom.parent.mkdir(parents=True, exist_ok=True)
        scm_pom.write_bytes(
            (ROOT / feasibility.HARDENED_SCM_POM_SOURCE).read_bytes()
        )
        (repository / feasibility.SCM_POM_CHECKSUM_PATH).write_bytes(
            feasibility.SCM_POM_POSTIMAGE_SHA1
        )
        manager_pom = repository / feasibility.SCM_MANAGER_POM_PATH
        manager_pom.parent.mkdir(parents=True, exist_ok=True)
        manager_pom.write_bytes(
            (ROOT / feasibility.HARDENED_SCM_MANAGER_POM_SOURCE).read_bytes()
        )
        (repository / feasibility.SCM_MANAGER_POM_CHECKSUM_PATH).write_bytes(
            feasibility.SCM_MANAGER_POM_POSTIMAGE_SHA1
        )
        return repository

    def _successful_log(self, path: Path) -> None:
        path.write_text(
            "[INFO] org.apache.velocity:velocity-engine-core:jar:2.4.1\n"
            "[INFO] org.codehaus.plexus:plexus-utils:jar:4.0.3\n"
            "[INFO] BUILD SUCCESS\n",
            encoding="utf-8",
        )

    def _finalized_evidence(self, root: Path) -> Path:
        evidence = root / "evidence"
        feasibility.capture_repository(self._repository(root), evidence)
        online = root / "online.log"
        offline = root / "offline.log"
        self._successful_log(online)
        self._successful_log(offline)
        builder_index = root / "builder-index-source.json"
        builder_index.write_text(self.builder_index_text, encoding="utf-8")
        maven_version = root / "maven-version-source.txt"
        maven_version.write_text(
            "Apache Maven 3.9.16\n"
            "Java version: 25, vendor: Eclipse Adoptium\n"
            'Default locale: en, platform encoding: UTF-8, OS name: "linux", '
            'version: "test", arch: "aarch64", family: "unix"\n',
            encoding="utf-8",
        )
        global_settings = root / "maven-global-settings-source.xml"
        global_settings.write_text(
            '<settings xmlns="http://maven.apache.org/SETTINGS/1.2.0">\n'
            "  <pluginGroups/>\n"
            "  <proxies/>\n"
            "  <servers/>\n"
            "  <mirrors>\n"
            "    <mirror>\n"
            "      <id>maven-default-http-blocker</id>\n"
            "      <mirrorOf>external:http:*</mirrorOf>\n"
            "      <name>Pseudo repository to mirror external repositories "
            "initially using HTTP.</name>\n"
            "      <url>http://0.0.0.0/</url>\n"
            "      <blocked>true</blocked>\n"
            "    </mirror>\n"
            "  </mirrors>\n"
            "  <profiles/>\n"
            "</settings>\n",
            encoding="utf-8",
        )
        toolchain = root / "toolchain-source.json"
        toolchain.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "result": "passed",
                    "runner_os": "Linux",
                    "runner_arch": "ARM64",
                    "container_architecture": "aarch64",
                    "native_execution": True,
                    "qemu_binfmt_handlers": [],
                    "builder_index": feasibility.EXPECTED_BUILDER,
                    "builder_arm64_manifest": (
                        feasibility.EXPECTED_BUILDER_ARM64_MANIFEST
                    ),
                    "builder_index_document_sha256": feasibility._sha256(
                        builder_index
                    ),
                    "maven_version_output_sha256": feasibility._sha256(
                        maven_version
                    ),
                    "global_settings_sha256": feasibility._sha256(
                        global_settings
                    ),
                }
            )
            + "\n",
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
                builder_index,
                maven_version,
                global_settings,
            )
        return evidence

    def test_workflow_is_read_only_and_fail_closed(self) -> None:
        feasibility.audit_workflow(ROOT)

    def test_workflow_closes_triggers_steps_and_policy_mounts(self) -> None:
        workflow = (ROOT / feasibility.WORKFLOW_PATH).read_text(
            encoding="utf-8"
        )
        mutations = (
            (
                workflow.replace("  pull_request:\n", "  push:\n", 1),
                "workflow triggers differ",
            ),
            (
                workflow.replace(
                    "      - name: Verify the native arm64 builder substrate\n",
                    "      - name: Replace the reviewed verifier\n"
                    "        shell: bash\n"
                    "        run: true\n\n"
                    "      - name: Verify the native arm64 builder substrate\n",
                    1,
                ),
                "validation steps differ",
            ),
            (
                workflow.replace(
                    "${GITHUB_WORKSPACE}/bootstrap/trino/v483/maven-policy:"
                    "/policy/.mvn:ro",
                    "${RUNNER_TEMP}/maven-policy:/policy/.mvn:ro",
                    1,
                ),
                "Maven Docker execution controls differ",
            ),
        )
        for mutated, error in mutations:
            with self.subTest(error=error):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    target = root / feasibility.WORKFLOW_PATH
                    target.parent.mkdir(parents=True)
                    target.write_text(mutated, encoding="utf-8")
                    with self.assertRaisesRegex(
                        feasibility.EvidenceError,
                        error,
                    ):
                        feasibility.audit_workflow(root)

    def test_workflow_binds_controls_to_offline_docker_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / feasibility.WORKFLOW_PATH
            target.parent.mkdir(parents=True)
            workflow = (ROOT / feasibility.WORKFLOW_PATH).read_text(
                encoding="utf-8"
            )
            workflow = workflow.replace(
                "            --network none \\\n",
                "",
                1,
            )
            target.write_text(
                workflow + "\n# unrelated --network none marker\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                feasibility.EvidenceError,
                "WORKFLOW",
            ):
                feasibility.audit_workflow(root)

    def test_workflow_binds_both_candidate_applications(self) -> None:
        application = (
            "          python3 scripts/verify_trino_maven_feasibility.py "
            "apply-candidate \\\n"
            '            --root . --checkout "${source_dir}"'
        )
        workflow = (ROOT / feasibility.WORKFLOW_PATH).read_text(
            encoding="utf-8"
        )
        self.assertEqual(workflow.count(application), 2)
        positions = [
            workflow.find(application),
            workflow.find(application, workflow.find(application) + 1),
        ]
        for index, position in enumerate(positions):
            with self.subTest(application=index):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    target = root / feasibility.WORKFLOW_PATH
                    target.parent.mkdir(parents=True)
                    mutated = (
                        workflow[:position]
                        + "          true"
                        + workflow[position + len(application) :]
                    )
                    target.write_text(mutated, encoding="utf-8")
                    with self.assertRaisesRegex(
                        feasibility.EvidenceError,
                        "candidate application differs",
                    ):
                        feasibility.audit_workflow(root)

    def test_workflow_rejects_additional_jobs_and_permissions(self) -> None:
        workflow = (ROOT / feasibility.WORKFLOW_PATH).read_text(
            encoding="utf-8"
        )
        mutations = {
            "job": workflow
            + "\n  mutate:\n"
            + "    runs-on: ubuntu-latest\n"
            + "    permissions:\n"
            + "      issues: write\n"
            + "    steps: []\n",
            "permission": workflow.replace(
                "    permissions:\n      contents: read\n",
                "    permissions:\n      contents: read\n      issues: read\n",
                1,
            ),
        }
        for label, mutated in mutations.items():
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    target = root / feasibility.WORKFLOW_PATH
                    target.parent.mkdir(parents=True)
                    target.write_text(mutated, encoding="utf-8")
                    with self.assertRaisesRegex(
                        feasibility.EvidenceError,
                        "WORKFLOW",
                    ):
                        feasibility.audit_workflow(root)

    def test_workflow_rechecks_candidate_immediately_before_maven(self) -> None:
        verification = (
            "          python3 scripts/verify_trino_maven_feasibility.py "
            "verify-candidate \\\n"
            "            --authorization-root . \\\n"
            '            --checkout "${source_dir}"'
        )
        workflow = (ROOT / feasibility.WORKFLOW_PATH).read_text(
            encoding="utf-8"
        )
        self.assertEqual(workflow.count(verification), 2)
        first = workflow.find(verification)
        positions = [first, workflow.find(verification, first + 1)]
        for index, position in enumerate(positions):
            with self.subTest(execution=index):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    target = root / feasibility.WORKFLOW_PATH
                    target.parent.mkdir(parents=True)
                    insertion = position + len(verification)
                    mutated = (
                        workflow[:insertion]
                        + "\n          sed -i.bak 's/2.4.1/2.4.0/' "
                        + '"${source_dir}/pom.xml"'
                        + workflow[insertion:]
                    )
                    target.write_text(mutated, encoding="utf-8")
                    with self.assertRaisesRegex(
                        feasibility.EvidenceError,
                        "candidate postimage check differs",
                    ):
                        feasibility.audit_workflow(root)

    def test_workflow_mounts_candidate_source_read_only(self) -> None:
        workflow = (ROOT / feasibility.WORKFLOW_PATH).read_text(
            encoding="utf-8"
        )
        self.assertEqual(workflow.count('${source_dir}:/workspace:ro'), 2)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / feasibility.WORKFLOW_PATH
            target.parent.mkdir(parents=True)
            target.write_text(
                workflow.replace(
                    '${source_dir}:/workspace:ro',
                    '${source_dir}:/workspace',
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                feasibility.EvidenceError,
                "Maven Docker execution controls differ",
            ):
                feasibility.audit_workflow(root)

    def test_candidate_binds_complete_authorized_source_postimages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            checkout = temporary_root / "checkout"
            checkout.mkdir()
            baseline = gzip.decompress(
                (ROOT / feasibility.publisher.BLOCKER_BASELINE_PATH).read_bytes()
            )
            (checkout / "pom.xml").write_bytes(baseline)
            child = checkout / "module/pom.xml"
            child.parent.mkdir()
            child.write_text("<project/>\n", encoding="utf-8")
            subprocess.run(["git", "init"], cwd=checkout, check=True)
            subprocess.run(["git", "add", "."], cwd=checkout, check=True)
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
            overlay = "<project><overlay/></project>\n"
            child.write_text(overlay, encoding="utf-8")
            source_boundary = {
                "permitted_paths": ["module/pom.xml"],
                "postimages": {
                    "module/pom.xml": hashlib.sha256(
                        overlay.encode("utf-8")
                    ).hexdigest()
                },
            }
            distribution_boundary = {
                "permitted_paths": ["pom.xml"],
                "postimages": {
                    "pom.xml": feasibility.EXPECTED_BASELINE_SHA256
                },
            }
            with (
                mock.patch.object(
                    feasibility.publisher,
                    "apply_source_overlay",
                ),
                mock.patch.object(
                    feasibility.publisher,
                    "EXPECTED_SOURCE_OVERLAY",
                    source_boundary,
                ),
                mock.patch.object(
                    feasibility.publisher,
                    "EXPECTED_DISTRIBUTION_REMEDIATION",
                    distribution_boundary,
                ),
            ):
                feasibility.apply_candidate(ROOT, checkout)
                feasibility.verify_candidate(checkout)
                self.assertEqual(
                    feasibility._sha256(checkout / "pom.xml"),
                    feasibility.EXPECTED_POSTIMAGE_SHA256,
                )
                candidate = (checkout / "pom.xml").read_text(encoding="utf-8")
                self.assertNotIn(
                    "<artifactId>gitflow-incremental-builder</artifactId>",
                    candidate,
                )
                self.assertEqual(
                    subprocess.run(
                        ["git", "diff", "--name-only", "HEAD"],
                        cwd=checkout,
                        check=True,
                        capture_output=True,
                        text=True,
                    ).stdout.splitlines(),
                    ["module/pom.xml", "pom.xml"],
                )
                child.write_text(
                    "<project><tampered/></project>\n",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(
                    feasibility.EvidenceError,
                    "CANDIDATE_SOURCE",
                ):
                    feasibility.verify_candidate(checkout)
                child.write_text(overlay, encoding="utf-8")
                (checkout / "pom.xml").write_text(
                    candidate.replace("2.4.1", "2.4.0", 1),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(
                    feasibility.EvidenceError,
                    "CANDIDATE_POSTIMAGE",
                ):
                    feasibility.verify_candidate(checkout)

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
            self.assertEqual(manifest["file_count"], 8)
            self.assertEqual(
                [record["path"] for record in manifest["files"]],
                sorted(record["path"] for record in manifest["files"]),
            )

    def test_capture_rejects_unreviewed_replacement_jar_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = self._repository(root)
            replacement = repository / feasibility.EXPECTED_REPLACEMENT_INPUTS[0]
            replacement.write_bytes(replacement.read_bytes() + b"tamper")
            with self.assertRaisesRegex(
                feasibility.EvidenceError,
                "REPLACEMENT_INPUT",
            ):
                feasibility.capture_repository(repository, root / "evidence")

    def test_archive_audit_enforces_resource_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = self._repository(root)
            entries = feasibility._repository_entries(repository)
            archive = root / feasibility.ARCHIVE_NAME
            feasibility._write_archive(repository, archive, entries)
            feasibility._archive_entries(archive, entries)
            bounds = (
                ("MAX_ARCHIVE_BYTES", archive.stat().st_size - 1),
                ("MAX_ARCHIVE_MEMBERS", len(entries) - 1),
                (
                    "MAX_ARCHIVE_MEMBER_BYTES",
                    max(entry["bytes"] for entry in entries) - 1,
                ),
                (
                    "MAX_ARCHIVE_FILE_BYTES",
                    sum(entry["bytes"] for entry in entries) - 1,
                ),
                ("MAX_ARCHIVE_COMPRESSION_RATIO", 1),
            )
            for constant, maximum in bounds:
                with self.subTest(constant=constant):
                    with mock.patch.object(feasibility, constant, maximum):
                        with self.assertRaisesRegex(
                            feasibility.EvidenceError,
                            "OFFLINE_ARCHIVE",
                        ):
                            feasibility._archive_entries(archive, entries)

    def test_evidence_json_and_manifest_counts_are_bounded_before_decode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "record.json"
            path.write_text('{"status": "passed"}\n', encoding="utf-8")
            with mock.patch.object(
                feasibility,
                "MAX_JSON_BYTES",
                path.stat().st_size - 1,
            ):
                with self.assertRaisesRegex(
                    feasibility.EvidenceError,
                    "EVIDENCE_JSON",
                ):
                    feasibility._read_json(path)

            files = [
                {
                    "path": f"artifact-{index}.jar",
                    "mode": "0644",
                    "sha256": "0" * 64,
                    "bytes": 1,
                }
                for index in range(2)
            ]
            with mock.patch.object(feasibility, "MAX_ARCHIVE_MEMBERS", 1):
                with self.assertRaisesRegex(
                    feasibility.EvidenceError,
                    "OFFLINE_INPUT",
                ):
                    feasibility._manifest_files({"files": files})

    def test_maven_version_attachment_is_bounded_before_decode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = self._finalized_evidence(Path(temporary))
            version = evidence / feasibility.MAVEN_VERSION_NAME
            with mock.patch.object(
                feasibility,
                "MAX_MAVEN_VERSION_BYTES",
                version.stat().st_size - 1,
            ):
                with self.assertRaisesRegex(
                    feasibility.EvidenceError,
                    "TOOLCHAIN_RECORD",
                ):
                    feasibility.audit_evidence(
                        evidence,
                        require_archive=True,
                    )

    def test_archive_audit_rejects_trailing_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = self._repository(root)
            entries = feasibility._repository_entries(repository)
            archive = root / feasibility.ARCHIVE_NAME
            feasibility._write_archive(repository, archive, entries)
            original = archive.read_bytes()
            trailers = (
                b"unaudited trailer",
                b"\0" * 8,
                gzip.compress(b"concatenated gzip member", mtime=0),
            )
            for trailer in trailers:
                with self.subTest(trailer=trailer[:16]):
                    archive.write_bytes(original + trailer)
                    with self.assertRaisesRegex(
                        feasibility.EvidenceError,
                        "OFFLINE_ARCHIVE",
                    ):
                        feasibility._archive_entries(archive, entries)

    def test_archive_audit_rejects_unmanifested_pax_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = self._repository(root)
            entries = feasibility._repository_entries(repository)
            archive = root / feasibility.ARCHIVE_NAME
            with archive.open("wb") as raw:
                with gzip.GzipFile(
                    filename="",
                    mode="wb",
                    fileobj=raw,
                    mtime=0,
                ) as zipped:
                    with tarfile.open(
                        fileobj=zipped,
                        mode="w",
                        format=tarfile.PAX_FORMAT,
                    ) as retained:
                        for index, entry in enumerate(entries):
                            source = repository / entry["path"]
                            info = retained.gettarinfo(
                                str(source),
                                arcname=entry["path"],
                            )
                            info.uid = 0
                            info.gid = 0
                            info.uname = ""
                            info.gname = ""
                            info.mtime = 0
                            info.mode = int(entry["mode"], 8)
                            info.pax_headers = (
                                {"comment": "unmanifested"}
                                if index == 0
                                else {}
                            )
                            with source.open("rb") as payload:
                                retained.addfile(info, payload)
            with self.assertRaisesRegex(
                feasibility.EvidenceError,
                "OFFLINE_ARCHIVE",
            ):
                feasibility._archive_entries(archive, entries)

    def test_archive_audit_rejects_noncanonical_gzip_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = self._repository(root)
            entries = feasibility._repository_entries(repository)
            archive = root / feasibility.ARCHIVE_NAME
            feasibility._write_archive(repository, archive, entries)
            expanded = gzip.decompress(archive.read_bytes())
            with archive.open("wb") as raw:
                with gzip.GzipFile(
                    filename="unmanifested-name",
                    mode="wb",
                    fileobj=raw,
                    mtime=0,
                ) as noncanonical:
                    noncanonical.write(expanded)
            with self.assertRaisesRegex(
                feasibility.EvidenceError,
                "gzip header is not canonical",
            ):
                feasibility._archive_entries(archive, entries)

    def test_archive_audit_rejects_nonzero_member_padding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = self._repository(root)
            entries = feasibility._repository_entries(repository)
            archive = root / feasibility.ARCHIVE_NAME
            feasibility._write_archive(repository, archive, entries)
            expanded = bytearray(gzip.decompress(archive.read_bytes()))
            first_padding = tarfile.BLOCKSIZE + entries[0]["bytes"]
            self.assertNotEqual(first_padding % tarfile.BLOCKSIZE, 0)
            expanded[first_padding] = 1
            with archive.open("wb") as raw:
                with gzip.GzipFile(
                    filename="",
                    mode="wb",
                    fileobj=raw,
                    mtime=0,
                ) as canonical_gzip:
                    canonical_gzip.write(expanded)
            with self.assertRaisesRegex(
                feasibility.EvidenceError,
                "archive bytes are not canonical",
            ):
                feasibility._archive_entries(archive, entries)

    def test_validation_record_path_is_bound_to_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = self._finalized_evidence(Path(temporary))
            record_path = evidence / feasibility.RECORD_NAME
            record = json.loads(record_path.read_text(encoding="utf-8"))
            record["record_path"] = "../../false.json"
            record_path.write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                feasibility.EvidenceError,
                "EVIDENCE_RECORD",
            ):
                feasibility.audit_evidence(
                    evidence,
                    require_archive=True,
                )

    def test_validation_record_run_identifiers_are_exact_integers(self) -> None:
        for field in ("run_id", "run_attempt"):
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory() as temporary:
                    evidence = self._finalized_evidence(Path(temporary))
                    record_path = evidence / feasibility.RECORD_NAME
                    record = json.loads(
                        record_path.read_text(encoding="utf-8")
                    )
                    record["subject"][field] = True
                    if field == "run_id":
                        record["subject"]["workflow_run"] = (
                            "https://github.com/TommyKammy/Shirokuma/"
                            "actions/runs/True"
                        )
                        record["record_path"] = (
                            "docs/design/evidence/trino/"
                            "run-True-maven-feasibility-validation.json"
                        )
                    record_path.write_text(
                        json.dumps(record, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(
                        feasibility.EvidenceError,
                        "EVIDENCE_SUBJECT",
                    ):
                        feasibility.audit_evidence(
                            evidence,
                            require_archive=True,
                        )

    def test_validation_record_nested_objects_are_closed_world(self) -> None:
        cases = (
            ("subject", lambda record: record["subject"]),
            ("execution", lambda record: record["execution"]),
            ("online", lambda record: record["execution"]["online"]),
            ("offline", lambda record: record["execution"]["offline"]),
            ("offline_inputs", lambda record: record["offline_inputs"]),
        )
        for label, select in cases:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as temporary:
                    evidence = self._finalized_evidence(Path(temporary))
                    record_path = evidence / feasibility.RECORD_NAME
                    record = json.loads(
                        record_path.read_text(encoding="utf-8")
                    )
                    select(record)["unreviewed_claim"] = True
                    record_path.write_text(
                        json.dumps(record, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                    with self.assertRaises(feasibility.EvidenceError):
                        feasibility.audit_evidence(
                            evidence,
                            require_archive=True,
                        )

    def test_audit_rejects_unmanifested_evidence_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = self._finalized_evidence(Path(temporary))
            (evidence / "unmanifested-secret.txt").write_text(
                "not part of the retained artifact\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                feasibility.EvidenceError,
                "EVIDENCE_DIRECTORY",
            ):
                feasibility.audit_evidence(
                    evidence,
                    require_archive=True,
                )

    def test_finalize_retains_online_offline_and_input_identities(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = self._finalized_evidence(root)
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
            self.assertEqual(
                record["execution"]["online"]["network"],
                feasibility.EXPECTED_ONLINE_NETWORK,
            )
            self.assertEqual(
                record["execution"]["toolchain"]["builder_index_document"][
                    "path"
                ],
                feasibility.BUILDER_INDEX_NAME,
            )

    def test_audit_binds_manifest_envelope(self) -> None:
        mutations = {
            "schema_version": 2,
            "media_type": "application/json",
            "repository_layout": "flat",
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory() as temporary:
                    evidence = self._finalized_evidence(Path(temporary))
                    manifest_path = evidence / feasibility.MANIFEST_NAME
                    manifest = json.loads(
                        manifest_path.read_text(encoding="utf-8")
                    )
                    manifest[field] = value
                    manifest_path.write_text(
                        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                    record_path = evidence / feasibility.RECORD_NAME
                    record = json.loads(
                        record_path.read_text(encoding="utf-8")
                    )
                    record["offline_inputs"]["manifest"] = {
                        "path": feasibility.MANIFEST_NAME,
                        "bytes": manifest_path.stat().st_size,
                        "sha256": feasibility._sha256(manifest_path),
                    }
                    record_path.write_text(
                        json.dumps(record, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(
                        feasibility.EvidenceError,
                        "manifest summary differs",
                    ):
                        feasibility.audit_evidence(
                            evidence,
                            require_archive=True,
                        )

    def test_audit_confines_all_identity_paths_to_evidence(self) -> None:
        cases = (
            (
                "online_log",
                lambda record: record["execution"]["online"]["log"],
                feasibility.ONLINE_LOG_NAME,
                False,
            ),
            (
                "offline_log",
                lambda record: record["execution"]["offline"]["log"],
                feasibility.OFFLINE_LOG_NAME,
                False,
            ),
            (
                "toolchain",
                lambda record: record["execution"]["toolchain"]["record"],
                feasibility.TOOLCHAIN_NAME,
                False,
            ),
            (
                "builder_index",
                lambda record: record["execution"]["toolchain"][
                    "builder_index_document"
                ],
                feasibility.BUILDER_INDEX_NAME,
                False,
            ),
            (
                "maven_version",
                lambda record: record["execution"]["toolchain"][
                    "maven_version_output"
                ],
                feasibility.MAVEN_VERSION_NAME,
                False,
            ),
            (
                "global_settings",
                lambda record: record["execution"]["toolchain"][
                    "global_settings"
                ],
                feasibility.GLOBAL_SETTINGS_NAME,
                False,
            ),
            (
                "manifest",
                lambda record: record["offline_inputs"]["manifest"],
                feasibility.MANIFEST_NAME,
                False,
            ),
            (
                "archive_absolute",
                lambda record: record["offline_inputs"]["archive"],
                feasibility.ARCHIVE_NAME,
                True,
            ),
        )
        for label, select, expected_name, absolute in cases:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    evidence = self._finalized_evidence(root)
                    outside = root / f"outside-{expected_name}"
                    outside.write_bytes((evidence / expected_name).read_bytes())
                    record_path = evidence / feasibility.RECORD_NAME
                    record = json.loads(
                        record_path.read_text(encoding="utf-8")
                    )
                    select(record)["path"] = (
                        str(outside.resolve())
                        if absolute
                        else f"../{outside.name}"
                    )
                    record_path.write_text(
                        json.dumps(record, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(
                        feasibility.EvidenceError,
                        "EVIDENCE_(?:IDENTITY|EXECUTION)",
                    ):
                        feasibility.audit_evidence(
                            evidence,
                            require_archive=True,
                        )

    def test_finalize_rejects_the_same_log_for_both_phases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            log = root / "resolve.log"
            self._successful_log(log)
            with self.assertRaisesRegex(
                feasibility.EvidenceError,
                "online and offline Maven logs must be distinct",
            ):
                feasibility.finalize_record(
                    root / "evidence",
                    log,
                    log,
                    log,
                    log,
                    log,
                    log,
                )

    def test_audit_binds_logs_to_distinct_phase_filenames(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = self._finalized_evidence(Path(temporary))
            record_path = evidence / feasibility.RECORD_NAME
            record = json.loads(record_path.read_text(encoding="utf-8"))
            record["execution"]["offline"]["log"] = record["execution"][
                "online"
            ]["log"]
            record_path.write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                feasibility.EvidenceError,
                "EVIDENCE_EXECUTION",
            ):
                feasibility.audit_evidence(evidence, require_archive=True)

    def test_audit_binds_subject_to_official_repository(self) -> None:
        mutations = {
            "workflow_run": "https://evil.invalid/attacker/fork/actions/runs/123456",
            "workflow_ref": (
                "attacker/fork/.github/workflows/"
                "trino-maven-remediation-feasibility.yml@refs/heads/main"
            ),
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory() as temporary:
                    evidence = self._finalized_evidence(Path(temporary))
                    record_path = evidence / feasibility.RECORD_NAME
                    record = json.loads(
                        record_path.read_text(encoding="utf-8")
                    )
                    record["subject"][field] = value
                    record_path.write_text(
                        json.dumps(record, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(
                        feasibility.EvidenceError,
                        "EVIDENCE_SUBJECT",
                    ):
                        feasibility.audit_evidence(
                            evidence,
                            require_archive=True,
                        )

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

    def test_resolution_log_size_is_bounded_before_scanning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "online.log"
            self._successful_log(path)
            with mock.patch.object(
                feasibility,
                "MAX_LOG_BYTES",
                path.stat().st_size - 1,
            ):
                with self.assertRaisesRegex(
                    feasibility.EvidenceError,
                    "RESOLUTION_LOG",
                ):
                    feasibility._vulnerable_lines(path)

            path.write_text(
                "[INFO] org.apache.velocity:velocity-engine-core:jar:2.3\n"
                "[INFO] BUILD SUCCESS\n",
                encoding="utf-8",
            )
            self.assertTrue(feasibility._vulnerable_lines(path))

    def test_vulnerable_classified_jars_in_repository_fail(self) -> None:
        for relative in feasibility.VULNERABLE_INPUTS:
            with self.subTest(relative=relative):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    repository = self._repository(root)
                    vulnerable = repository / relative
                    vulnerable.parent.mkdir(parents=True, exist_ok=True)
                    vulnerable.write_bytes(relative.encode("utf-8"))
                    with self.assertRaisesRegex(
                        feasibility.EvidenceError,
                        "VULNERABLE_INPUT",
                    ):
                        feasibility.capture_repository(
                            repository,
                            root / "evidence",
                        )

    def test_prune_removes_only_classified_jars_before_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = self._repository(root)
            hardened = (
                ROOT / feasibility.HARDENED_SCM_POM_SOURCE
            ).read_bytes()
            preimage = hardened.replace(
                b"      <version>4.0.3</version>\n",
                b"",
                1,
            )
            self.assertEqual(
                hashlib.sha256(preimage).hexdigest(),
                feasibility.SCM_POM_PREIMAGE_SHA256,
            )
            (repository / feasibility.SCM_POM_PATH).write_bytes(preimage)
            (repository / feasibility.SCM_POM_CHECKSUM_PATH).write_bytes(
                feasibility.SCM_POM_PREIMAGE_SHA1
            )
            manager_hardened = (
                ROOT / feasibility.HARDENED_SCM_MANAGER_POM_SOURCE
            ).read_bytes()
            manager_preimage = manager_hardened.replace(
                b"    <dependency>\n"
                b"      <groupId>org.codehaus.plexus</groupId>\n"
                b"      <artifactId>plexus-utils</artifactId>\n"
                b"      <version>4.0.3</version>\n"
                b"    </dependency>\n",
                b"",
                1,
            )
            self.assertEqual(
                hashlib.sha256(manager_preimage).hexdigest(),
                feasibility.SCM_MANAGER_POM_PREIMAGE_SHA256,
            )
            (repository / feasibility.SCM_MANAGER_POM_PATH).write_bytes(
                manager_preimage
            )
            (
                repository / feasibility.SCM_MANAGER_POM_CHECKSUM_PATH
            ).write_bytes(feasibility.SCM_MANAGER_POM_PREIMAGE_SHA1)
            for relative in feasibility.VULNERABLE_INPUTS:
                vulnerable = repository / relative
                vulnerable.parent.mkdir(parents=True, exist_ok=True)
                vulnerable.write_bytes(relative.encode("utf-8"))
            sentinel = repository / "org/example/keep/1.0/keep-1.0.jar"
            sentinel.parent.mkdir(parents=True, exist_ok=True)
            sentinel.write_bytes(b"keep")
            feasibility.prune_vulnerable_inputs(repository, ROOT)
            self.assertTrue(sentinel.is_file())
            self.assertFalse(
                any(
                    (repository / relative).exists()
                    for relative in feasibility.VULNERABLE_INPUTS
                )
            )
            self.assertEqual(
                feasibility._sha256(repository / feasibility.SCM_POM_PATH),
                feasibility.SCM_POM_POSTIMAGE_SHA256,
            )
            feasibility.capture_repository(repository, root / "evidence")

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

    def test_archive_contents_must_match_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = self._finalized_evidence(root)
            attacker_root = root / "attacker"
            repository = self._repository(attacker_root)
            (repository / "org.example.index").write_text(
                "different retained input\n",
                encoding="utf-8",
            )
            attacker = attacker_root / "evidence"
            feasibility.capture_repository(repository, attacker)
            archive = evidence / feasibility.ARCHIVE_NAME
            archive.write_bytes(
                (attacker / feasibility.ARCHIVE_NAME).read_bytes()
            )
            record_path = evidence / feasibility.RECORD_NAME
            record = json.loads(record_path.read_text(encoding="utf-8"))
            record["offline_inputs"]["archive"] = feasibility._identity(archive)
            record_path.write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                feasibility.EvidenceError,
                "OFFLINE_ARCHIVE",
            ):
                feasibility.audit_evidence(evidence, require_archive=True)

    def test_toolchain_record_is_revalidated_during_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = self._finalized_evidence(Path(temporary))
            toolchain_path = evidence / feasibility.TOOLCHAIN_NAME
            toolchain = json.loads(toolchain_path.read_text(encoding="utf-8"))
            toolchain["result"] = "failed"
            toolchain_path.write_text(
                json.dumps(toolchain, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            record_path = evidence / feasibility.RECORD_NAME
            record = json.loads(record_path.read_text(encoding="utf-8"))
            record["execution"]["toolchain"]["result"] = "failed"
            record["execution"]["toolchain"]["record"] = feasibility._identity(
                toolchain_path
            )
            record_path.write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                feasibility.EvidenceError,
                "TOOLCHAIN_RECORD",
            ):
                feasibility.audit_evidence(evidence, require_archive=True)

    def test_toolchain_index_document_is_bound_to_builder_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = self._finalized_evidence(Path(temporary))
            index_path = evidence / feasibility.BUILDER_INDEX_NAME
            index = json.loads(index_path.read_text(encoding="utf-8"))
            index["attacker_annotation"] = "different index"
            index_path.write_text(
                json.dumps(index, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            index_sha256 = feasibility._sha256(index_path)

            toolchain_path = evidence / feasibility.TOOLCHAIN_NAME
            toolchain = json.loads(toolchain_path.read_text(encoding="utf-8"))
            toolchain["builder_index_document_sha256"] = index_sha256
            toolchain_path.write_text(
                json.dumps(toolchain, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            record_path = evidence / feasibility.RECORD_NAME
            record = json.loads(record_path.read_text(encoding="utf-8"))
            embedded = record["execution"]["toolchain"]
            embedded["builder_index_document_sha256"] = index_sha256
            embedded["builder_index_document"] = feasibility._identity(
                index_path
            )
            embedded["record"] = feasibility._identity(toolchain_path)
            record_path.write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                feasibility.EvidenceError,
                "TOOLCHAIN_RECORD",
            ):
                feasibility.audit_evidence(evidence, require_archive=True)

    def test_offline_repository_mount_must_remain_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = self._finalized_evidence(Path(temporary))
            record_path = evidence / feasibility.RECORD_NAME
            record = json.loads(record_path.read_text(encoding="utf-8"))
            record["execution"]["offline"]["repository_mount"] = "read-write"
            record_path.write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                feasibility.EvidenceError,
                "EVIDENCE_EXECUTION",
            ):
                feasibility.audit_evidence(evidence, require_archive=True)


if __name__ == "__main__":
    unittest.main()
