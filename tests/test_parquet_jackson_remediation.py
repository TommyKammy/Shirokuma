from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import remediate_parquet_jackson as remediation  # noqa: E402


class ParquetJacksonRemediationTests(unittest.TestCase):
    def _build_repository(
        self,
        root: Path,
        *,
        fixed_version: bytes = b"2.21.4",
    ) -> Path:
        repository = root / "repository"
        artifact = repository / remediation.GROUP_PATH
        artifact.mkdir(parents=True)
        jar = artifact / remediation.ARTIFACT_FILES[0]
        with zipfile.ZipFile(jar, "w") as archive:
            archive.writestr(
                (
                    "shaded/parquet/com/fasterxml/jackson/core/"
                    "json/PackageVersion.class"
                ),
                b"jackson-version=" + fixed_version,
            )
        (artifact / remediation.ARTIFACT_FILES[1]).write_text(
            """\
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <parent>
    <groupId>org.apache.parquet</groupId>
    <artifactId>parquet</artifactId>
    <version>1.17.1</version>
  </parent>
  <modelVersion>4.0.0</modelVersion>
  <artifactId>parquet-jackson</artifactId>
</project>
""",
            encoding="utf-8",
        )
        return repository

    def test_prepare_source_applies_only_the_two_exact_replacements(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary)
            before = (
                b"prefix\n"
                b"<jackson.version>2.21.3</jackson.version>\n"
                b"<jackson-databind.version>2.21.3</jackson-databind.version>\n"
                b"suffix\n"
            )
            after = before.replace(b"2.21.3", b"2.21.4")
            (checkout / "pom.xml").write_bytes(before)
            calls: list[bool] = []
            with (
                mock.patch.object(
                    remediation,
                    "ROOT_POM_SIZE",
                    len(before),
                ),
                mock.patch.object(
                    remediation,
                    "ROOT_POM_PREIMAGE",
                    hashlib.sha256(before).hexdigest(),
                ),
                mock.patch.object(
                    remediation,
                    "ROOT_POM_POSTIMAGE",
                    hashlib.sha256(after).hexdigest(),
                ),
                mock.patch.object(
                    remediation,
                    "_verify_git_identity",
                    side_effect=lambda _, *, pristine: calls.append(pristine),
                ),
            ):
                remediation.prepare_source(checkout)
            self.assertEqual(after, (checkout / "pom.xml").read_bytes())
            self.assertEqual([True, False], calls)

    def test_stage_seal_and_compare_preserve_exact_built_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_build = self._build_repository(root / "first")
            second_build = self._build_repository(root / "second")
            first_target = root / "first-target"
            second_target = root / "second-target"
            checkout = root / "checkout"
            checkout.mkdir()
            with mock.patch.object(remediation, "_verify_prepared_source"):
                remediation.stage_artifact(
                    checkout,
                    first_build,
                    first_target,
                )
                remediation.stage_artifact(
                    checkout,
                    second_build,
                    second_target,
                )
            marker = (
                first_target
                / remediation.GROUP_PATH
                / "_remote.repositories"
            )
            self.assertEqual(
                remediation._marker(remediation.RESOLUTION_ORIGIN_ID),
                marker.read_bytes(),
            )
            remediation.compare_artifacts(first_build, second_build)
            remediation.seal_artifact(first_build, first_target)
            remediation.seal_artifact(second_build, second_target)
            self.assertEqual(
                remediation._marker(remediation.SEALED_ORIGIN_ID),
                marker.read_bytes(),
            )

    def test_rejects_vulnerable_or_replaced_built_jar(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vulnerable = self._build_repository(
                root / "vulnerable",
                fixed_version=b"2.21.3",
            )
            with self.assertRaisesRegex(
                remediation.RemediationError,
                "vulnerable Jackson version",
            ):
                remediation._verify_built_jar(
                    vulnerable
                    / remediation.GROUP_PATH
                    / remediation.ARTIFACT_FILES[0]
                )

            fixed = self._build_repository(root / "fixed")
            target = root / "target"
            checkout = root / "checkout"
            checkout.mkdir()
            with mock.patch.object(remediation, "_verify_prepared_source"):
                remediation.stage_artifact(checkout, fixed, target)
            (
                target
                / remediation.GROUP_PATH
                / remediation.ARTIFACT_FILES[0]
            ).write_bytes(b"replaced")
            with self.assertRaisesRegex(
                remediation.RemediationError,
                "was replaced",
            ):
                remediation.seal_artifact(fixed, target)

    def test_rejects_pom_that_exposes_unshaded_jackson_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = self._build_repository(Path(temporary))
            pom = (
                repository
                / remediation.GROUP_PATH
                / remediation.ARTIFACT_FILES[1]
            )
            text = pom.read_text(encoding="utf-8").replace(
                "</project>",
                """\
  <dependencies>
    <dependency>
      <groupId>com.fasterxml.jackson.core</groupId>
      <artifactId>jackson-databind</artifactId>
    </dependency>
  </dependencies>
</project>""",
            )
            pom.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(
                remediation.RemediationError,
                "dependency-reduced",
            ):
                remediation._verify_built_pom(pom)


if __name__ == "__main__":
    unittest.main()
