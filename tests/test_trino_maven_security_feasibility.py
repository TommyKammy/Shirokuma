from __future__ import annotations

import hashlib
import json
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


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
            try:
                feasibility.stage_jar(repository, candidate)
            finally:
                feasibility.DOCKER_JAVA_CENTRAL_JAR_SHA256 = old_hash
                feasibility.DOCKER_JAVA_CENTRAL_JAR_BYTES = old_bytes
                feasibility.DOCKER_JAVA_CANDIDATE_JAR_SHA256 = old_candidate_hash
                feasibility.DOCKER_JAVA_CANDIDATE_JAR_BYTES = old_candidate_bytes

            self.assertEqual(target.read_bytes(), candidate.read_bytes())
            receipt = json.loads(
                target.with_name(
                    target.name + ".shirokuma-source.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(receipt["source_commit"], feasibility.DOCKER_JAVA_COMMIT)
            self.assertEqual(receipt["candidate_sha256"], hashlib.sha256(candidate.read_bytes()).hexdigest())

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

    def test_scan_binds_reported_jars_to_inventory_and_exact_versions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            repository.mkdir()
            docker_jar = repository / "docker-java-transport-zerodep-3.7.1.jar"
            netty_jar = repository / "netty-transport-sctp-4.2.17.Final.jar"
            docker_jar.write_bytes(b"docker candidate")
            netty_jar.write_bytes(b"netty candidate")
            packages = [
                {
                    "Name": name,
                    "Version": version,
                    "FilePath": path.name,
                    "Digest": "sha1:" + hashlib.sha1(path.read_bytes()).hexdigest(),
                }
                for name, version, path in (
                    (
                        "com.github.docker-java:docker-java-transport-zerodep",
                        "3.7.1",
                        docker_jar,
                    ),
                    (
                        "org.apache.httpcomponents.client5:httpclient5",
                        "5.6.4",
                        docker_jar,
                    ),
                    (
                        "org.apache.httpcomponents.core5:httpcore5",
                        "5.4.3",
                        docker_jar,
                    ),
                    (
                        "org.apache.httpcomponents.core5:httpcore5-h2",
                        "5.4.3",
                        docker_jar,
                    ),
                    (
                        "io.netty:netty-transport-sctp",
                        "4.2.17.Final",
                        netty_jar,
                    ),
                )
            ]
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
            feasibility.verify_scan(repository, report)

            packages[0]["FilePath"] = "outside-inventory.jar"
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
                feasibility.FeasibilityError, "TRIVY_INVENTORY"
            ):
                feasibility.verify_scan(repository, report)

    def test_workflow_is_pull_request_only_and_read_only(self) -> None:
        feasibility.audit_workflow(ROOT)

    def test_parquet_origin_is_sealed_only_after_offline_rebuild(self) -> None:
        runner = (ROOT / feasibility.RUNNER_PATH).read_text(encoding="utf-8")
        offline = runner.index("--offline --batch-mode")
        seal = runner.index("${parquet} seal-artifact")
        manifest = runner.index("${verify} manifest-repository")
        self.assertLess(offline, seal)
        self.assertLess(seal, manifest)

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
