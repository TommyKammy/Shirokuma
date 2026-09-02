from __future__ import annotations

import hashlib
import json
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_trino_maven_security_feasibility as feasibility  # noqa: E402


class TrinoMavenSecurityFeasibilityTests(unittest.TestCase):
    def _jar(self, path: Path, timestamp: tuple[int, ...]) -> None:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            entries = {
                "META-INF/MANIFEST.MF": b"Manifest-Version: 1.0\n",
                "META-INF/maven/org.apache.httpcomponents.client5/httpclient5/"
                "pom.properties": b"version=5.6.4\n",
                "META-INF/maven/org.apache.httpcomponents.core5/httpcore5/"
                "pom.properties": b"version=5.4.3\n",
                "META-INF/maven/org.apache.httpcomponents.core5/httpcore5-h2/"
                "pom.properties": b"version=5.4.3\n",
                "org/example/Example.class": b"class payload",
            }
            for name, payload in reversed(entries.items()):
                info = zipfile.ZipInfo(name, timestamp)
                info.external_attr = (stat.S_IFREG | 0o600) << 16
                archive.writestr(info, payload)

    def test_two_raw_jars_canonicalize_byte_identically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_raw = root / "first-raw.jar"
            second_raw = root / "second-raw.jar"
            first = root / "first.jar"
            second = root / "second.jar"
            self._jar(first_raw, (2026, 8, 25, 1, 2, 4))
            self._jar(second_raw, (2026, 8, 26, 3, 4, 6))

            feasibility.canonicalize_jar(first_raw, first)
            feasibility.canonicalize_jar(second_raw, second)

            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertNotEqual(first_raw.read_bytes(), second_raw.read_bytes())
            feasibility.verify_remediated_jar(first)

    def test_canonicalization_rejects_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "unsafe.jar"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("../escape", b"blocked")
            with self.assertRaisesRegex(feasibility.FeasibilityError, "JAR_ENTRY"):
                feasibility.canonicalize_jar(source, root / "output.jar")

    def test_candidate_rejects_blocked_httpcore_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            jar = Path(directory) / "candidate.jar"
            self._jar(jar, (2026, 8, 25, 1, 2, 4))
            with zipfile.ZipFile(jar, "a") as archive:
                archive.writestr("blocked.txt", b"5.3.6")
            with self.assertRaisesRegex(feasibility.FeasibilityError, "JAR_VERSION"):
                feasibility.verify_remediated_jar(jar)

    def test_stage_requires_exact_central_preimage_and_writes_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            target = repository / feasibility.DOCKER_JAVA_REPOSITORY_PATH
            target.parent.mkdir(parents=True)
            central = b"reviewed Central preimage"
            target.write_bytes(central)
            candidate = root / "candidate.jar"
            self._jar(candidate, (2026, 8, 25, 1, 2, 4))
            old_hash = feasibility.DOCKER_JAVA_CENTRAL_JAR_SHA256
            old_bytes = feasibility.DOCKER_JAVA_CENTRAL_JAR_BYTES
            old_candidate_hash = feasibility.DOCKER_JAVA_CANDIDATE_JAR_SHA256
            old_candidate_bytes = feasibility.DOCKER_JAVA_CANDIDATE_JAR_BYTES
            feasibility.DOCKER_JAVA_CENTRAL_JAR_SHA256 = hashlib.sha256(
                central
            ).hexdigest()
            feasibility.DOCKER_JAVA_CENTRAL_JAR_BYTES = len(central)
            feasibility.DOCKER_JAVA_CANDIDATE_JAR_SHA256 = hashlib.sha256(
                candidate.read_bytes()
            ).hexdigest()
            feasibility.DOCKER_JAVA_CANDIDATE_JAR_BYTES = candidate.stat().st_size
            receipt_path = root / "candidate.source.json"
            try:
                with self.assertRaisesRegex(
                    feasibility.FeasibilityError,
                    "JAR_RECEIPT",
                ):
                    feasibility.stage_jar(
                        repository,
                        candidate,
                        target.with_name(target.name + ".shirokuma-source.json"),
                    )
                feasibility.stage_jar(repository, candidate, receipt_path)
            finally:
                feasibility.DOCKER_JAVA_CENTRAL_JAR_SHA256 = old_hash
                feasibility.DOCKER_JAVA_CENTRAL_JAR_BYTES = old_bytes
                feasibility.DOCKER_JAVA_CANDIDATE_JAR_SHA256 = old_candidate_hash
                feasibility.DOCKER_JAVA_CANDIDATE_JAR_BYTES = old_candidate_bytes

            self.assertEqual(target.read_bytes(), candidate.read_bytes())
            receipt = json.loads(
                receipt_path.read_text(encoding="utf-8")
            )
            self.assertFalse(target.with_name("_remote.repositories").exists())
            self.assertEqual(
                receipt["source_repository"],
                feasibility.DOCKER_JAVA_SOURCE_REPOSITORY,
            )
            self.assertEqual(receipt["source_commit"], feasibility.DOCKER_JAVA_COMMIT)
            self.assertEqual(receipt["candidate_sha256"], hashlib.sha256(candidate.read_bytes()).hexdigest())
            self.assertFalse(
                any(
                    path.name.endswith(".shirokuma-source.json")
                    for path in repository.rglob("*")
                )
            )

            marker_path = target.with_name("_remote.repositories")
            marker_path.write_text(
                f"{target.name}>shirokuma-central=\n"
                f"{target.name}>{feasibility.DOCKER_JAVA_ORIGIN_ID}=\n"
                "docker-java-transport-zerodep-3.7.1.pom>shirokuma-central=\n",
                encoding="iso-8859-1",
            )
            feasibility.DOCKER_JAVA_CANDIDATE_JAR_SHA256 = hashlib.sha256(
                candidate.read_bytes()
            ).hexdigest()
            feasibility.DOCKER_JAVA_CANDIDATE_JAR_BYTES = candidate.stat().st_size
            try:
                feasibility.seal_jar_origin(repository)
            finally:
                feasibility.DOCKER_JAVA_CANDIDATE_JAR_SHA256 = old_candidate_hash
                feasibility.DOCKER_JAVA_CANDIDATE_JAR_BYTES = old_candidate_bytes
            self.assertEqual(
                marker_path.read_text(encoding="iso-8859-1"),
                f"{target.name}>{feasibility.DOCKER_JAVA_ORIGIN_ID}=\n"
                "docker-java-transport-zerodep-3.7.1.pom>shirokuma-central=\n",
            )

    def test_zero_findings_rejects_high(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            report.write_text(
                json.dumps(
                    {
                        "Results": [
                            {
                                "Vulnerabilities": [
                                    {"VulnerabilityID": "CVE-test", "Severity": "HIGH"}
                                ]
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                feasibility.FeasibilityError, "MAVEN_SCAN_FINDING"
            ):
                feasibility.verify_zero_findings(report)

    def test_manifest_preserves_the_registered_source_origin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def build_manifest(repository: Path) -> dict[str, str]:
                self.assertEqual(repository, root.resolve())
                self.assertEqual(
                    feasibility.packager.ALLOWED_ORIGIN_IDS[
                        feasibility.DOCKER_JAVA_ORIGIN_ID
                    ],
                    feasibility.DOCKER_JAVA_SOURCE_REPOSITORY,
                )
                return {
                    "files": [
                        {
                            "path": feasibility.DOCKER_JAVA_REPOSITORY_PATH.as_posix(),
                            "repository_origin": feasibility.DOCKER_JAVA_SOURCE_REPOSITORY,
                        },
                        {
                            "path": feasibility.DOCKER_JAVA_REPOSITORY_PATH.as_posix() + ".sha1",
                            "repository_origin": feasibility.DOCKER_JAVA_SOURCE_REPOSITORY,
                        },
                    ]
                }

            with mock.patch.object(
                feasibility.packager,
                "build_manifest",
                side_effect=build_manifest,
            ):
                output = root / "manifest.json"
                feasibility.manifest_repository(root, output)

            self.assertEqual(
                json.loads(output.read_text())["files"][0]["repository_origin"],
                feasibility.DOCKER_JAVA_SOURCE_REPOSITORY,
            )
            self.assertEqual(
                feasibility.DOCKER_JAVA_SOURCE_REPOSITORY,
                feasibility.packager.ALLOWED_ORIGIN_IDS[
                    feasibility.DOCKER_JAVA_ORIGIN_ID
                ],
            )

    def test_scan_requires_the_complete_sbom_package_closure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docker_path = (
                "com/github/docker-java/docker-java-transport-zerodep/3.7.1/"
                "docker-java-transport-zerodep-3.7.1.jar"
            )
            netty_path = (
                "io/netty/netty-transport-sctp/4.2.17.Final/"
                "netty-transport-sctp-4.2.17.Final.jar"
            )
            omitted_path = "org/example/omitted/1.0/omitted-1.0.jar"
            descriptor = root / "descriptor.json"
            descriptor.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "file_count": 3,
                        "files": [
                            {
                                "mode": "0644",
                                "path": path,
                                "repository_origin": (
                                    feasibility.DOCKER_JAVA_SOURCE_REPOSITORY
                                    if path == docker_path
                                    else "https://repo.maven.apache.org/maven2/"
                                ),
                                "sha256": (
                                    feasibility.DOCKER_JAVA_CANDIDATE_JAR_SHA256
                                    if path == docker_path
                                    else hashlib.sha256(path.encode()).hexdigest()
                                ),
                                "size": (
                                    feasibility.DOCKER_JAVA_CANDIDATE_JAR_BYTES
                                    if path == docker_path
                                    else len(path)
                                ),
                            }
                            for path in (docker_path, netty_path, omitted_path)
                        ],
                    }
                ),
                encoding="utf-8",
            )
            identities = (
                (
                    "com.github.docker-java:docker-java-transport-zerodep",
                    "3.7.1",
                    docker_path,
                ),
                (
                    "org.apache.httpcomponents.client5:httpclient5",
                    "5.6.4",
                    docker_path,
                ),
                (
                    "org.apache.httpcomponents.core5:httpcore5",
                    "5.4.3",
                    docker_path,
                ),
                (
                    "org.apache.httpcomponents.core5:httpcore5-h2",
                    "5.4.3",
                    docker_path,
                ),
                (
                    "io.netty:netty-transport-sctp",
                    "4.2.17.Final",
                    netty_path,
                ),
                (
                    "org.example:omitted",
                    "1.0",
                    omitted_path,
                ),
            )
            packages = []
            components = []
            for name, version, path in identities:
                group, artifact = name.split(":", 1)
                purl = f"pkg:maven/{group}/{artifact}@{version}"
                packages.append(
                    {
                        "Name": name,
                        "Version": version,
                        "FilePath": path,
                        "Identifier": {"PURL": purl},
                    }
                )
                components.append(
                    {
                        "bom-ref": purl,
                        "type": "library",
                        "name": artifact,
                        "purl": purl,
                        "properties": [
                            {
                                "name": "aquasecurity:trivy:FilePath",
                                "value": path,
                            }
                        ],
                    }
                )
            root_ref = "urn:test:maven-root"
            sbom = root / "sbom.json"
            sbom.write_text(
                json.dumps(
                    {
                        "bomFormat": "CycloneDX",
                        "specVersion": "1.7",
                        "metadata": {
                            "component": {
                                "bom-ref": root_ref,
                                "type": "application",
                                "name": "maven-repository-a",
                            }
                        },
                        "components": components,
                        "dependencies": [
                            {
                                "ref": root_ref,
                                "dependsOn": [
                                    component["bom-ref"] for component in components
                                ],
                            },
                            *[
                                {"ref": component["bom-ref"], "dependsOn": []}
                                for component in components
                            ],
                        ],
                    }
                ),
                encoding="utf-8",
            )
            report = root / "report.json"
            report.write_text(
                json.dumps(
                    {
                        "SchemaVersion": 2,
                        "Results": [
                            {
                                "Packages": packages,
                                "Vulnerabilities": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            feasibility.verify_scan(descriptor, sbom, report)

            packages.pop()
            report.write_text(
                json.dumps(
                    {
                        "SchemaVersion": 2,
                        "Results": [{"Packages": packages, "Vulnerabilities": []}],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                feasibility.publisher.ContractError, "MAVEN_SCAN_CLOSURE"
            ):
                feasibility.verify_scan(descriptor, sbom, report)

    def test_workflow_is_pull_request_only_and_read_only(self) -> None:
        feasibility.audit_workflow(ROOT)

    def test_workflow_checks_out_exact_head_from_reviewed_predecessor(self) -> None:
        workflow = feasibility._regular_file(
            ROOT / feasibility.WORKFLOW_PATH,
            "WORKFLOW",
        ).decode("utf-8")
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha }}",
            workflow,
        )
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn(
            "REVIEWED_PREDECESSOR: 8903beb4a6190953bb2506c8c6a11ab9bde7de98",
            workflow,
        )

    def test_parquet_origin_is_sealed_only_after_offline_rebuild(self) -> None:
        runner = (ROOT / feasibility.RUNNER_PATH).read_text(encoding="utf-8")
        offline = runner.index("--offline --batch-mode")
        seal = runner.index("${parquet} seal-artifact")
        manifest = runner.index("${verify} manifest-repository")
        self.assertLess(offline, seal)
        self.assertLess(seal, manifest)

    def test_central_preimage_fetch_does_not_invoke_a_maven_plugin(self) -> None:
        runner = (ROOT / feasibility.RUNNER_PATH).read_text(encoding="utf-8")
        self.assertNotIn("dependency:get", runner)
        self.assertIn(
            "curl --proto '=https' --tlsv1.2 --fail --silent --show-error",
            runner,
        )
        self.assertIn(
            "https://repo.maven.apache.org/maven2/com/github/docker-java/"
            "docker-java-transport-zerodep/3.7.1/"
            "docker-java-transport-zerodep-3.7.1.jar",
            runner,
        )

    def test_exact_patch_and_source_identities_are_pinned(self) -> None:
        self.assertEqual(
            feasibility.TRINO_POSTIMAGE_SHA256,
            "3d0c79d798c68632a23e94abb899b760485e199f4ead530bcc27c52a2f2854d3",
        )
        self.assertEqual(
            feasibility.DOCKER_JAVA_POM_POSTIMAGE_SHA256,
            "68fee21ce48c9f5d7f6c2ac3a97b6e07df8d7f2f183fcec05f4ae4fb5aa4caf3",
        )
        for patch in (*feasibility.TRINO_PATCHES, feasibility.DOCKER_JAVA_PATCH):
            self.assertTrue((ROOT / patch).is_file(), patch)


if __name__ == "__main__":
    unittest.main()
