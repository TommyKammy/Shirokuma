from __future__ import annotations

import contextlib
import copy
import datetime as dt
import gzip
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from email.message import Message
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import package_trino_maven_dependencies as package  # noqa: E402
import package_trino_bun_dependencies as bun_package  # noqa: E402
import prepare_trino_bun_input as bun  # noqa: E402
import verify_trino_dependency_publisher as verify  # noqa: E402


class MavenSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._original_external_inputs = package.EXTERNAL_INPUTS
        cls._original_bun_input = package.BUN_INPUT
        test_input = {
            **package.BUN_INPUT,
            "sha256": hashlib.sha256(b"bun").hexdigest(),
            "size": 3,
        }
        package.EXTERNAL_INPUTS = [
            test_input,
            package.PARQUET_SOURCE_REMEDIATION,
            package.SCM_METADATA_REMEDIATION,
        ]
        package.BUN_INPUT = test_input

    @classmethod
    def tearDownClass(cls) -> None:
        package.EXTERNAL_INPUTS = cls._original_external_inputs
        package.BUN_INPUT = cls._original_bun_input

    def _repository(
        self,
        root: Path,
        *,
        include_trino_extension: bool = True,
        trino_origin: str = "shirokuma-central",
    ) -> Path:
        repository = root / "repository"
        artifact = repository / "org/example/demo/1.0"
        artifact.mkdir(parents=True)
        (artifact / "demo-1.0.jar").write_bytes(b"jar")
        (artifact / "demo-1.0.jar.sha1").write_text(
            hashlib.sha1(b"jar", usedforsecurity=False).hexdigest() + "\n",
            encoding="ascii",
        )
        (artifact / "demo-1.0.pom").write_text("<project/>\n", encoding="utf-8")
        (artifact / "demo-1.0.jar.lastUpdated").write_text(
            "# resolver attempt metadata\n", encoding="iso-8859-1"
        )
        (artifact / "_remote.repositories").write_text(
            "# generated\n"
            "demo-1.0.jar>shirokuma-central=\n"
            "demo-1.0.pom>shirokuma-central-fallback=\n",
            encoding="iso-8859-1",
        )
        metadata = repository / "io/confluent/sample"
        metadata.mkdir(parents=True)
        (metadata / "maven-metadata-shirokuma-confluent.xml").write_text(
            "<metadata/>\n", encoding="utf-8"
        )
        bun_cache = repository / package.BUN_INPUT["cache_path"]
        bun_cache.parent.mkdir(parents=True)
        bun_cache.write_bytes(b"bun")
        (bun_cache.parent / "_remote.repositories").write_text(
            f"{bun_cache.name}>shirokuma-bun-release=\n",
            encoding="iso-8859-1",
        )
        self._trino_external_dependencies(
            repository,
            include_trino_extension=include_trino_extension,
            trino_origin=trino_origin,
        )
        self._parquet_remediation(repository)
        scm_payloads = {
            package.SCM_METADATA_REMEDIATION["files"][0]["path"]: (
                ROOT
                / "bootstrap/trino/v483/"
                "maven-scm-provider-gitexe-2.2.1-hardened.pom"
            ).read_bytes(),
            package.SCM_METADATA_REMEDIATION["files"][1]["path"]: (
                b"a8630355e52d9c81dbd6ec117820bb58b6355f4a"
            ),
            package.SCM_METADATA_REMEDIATION["files"][2]["path"]: (
                ROOT
                / "bootstrap/trino/v483/"
                "maven-scm-manager-plexus-2.2.1-hardened.pom"
            ).read_bytes(),
            package.SCM_METADATA_REMEDIATION["files"][3]["path"]: (
                b"eb1b7ab169dc923806b0040631a45dc83d0b83e8"
            ),
        }
        for relative, payload in scm_payloads.items():
            target = repository / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            if target.suffix == ".pom":
                (target.parent / "_remote.repositories").write_text(
                    f"{target.name}>shirokuma-central=\n",
                    encoding="iso-8859-1",
                )
        return repository

    def _parquet_remediation(
        self,
        repository: Path,
        *,
        origin: str = "shirokuma-parquet-remediation",
    ) -> Path:
        directory = (
            repository
            / "org/apache/parquet/parquet-jackson/1.17.1"
        )
        directory.mkdir(parents=True)
        for name in (
            "parquet-jackson-1.17.1.jar",
            "parquet-jackson-1.17.1.pom",
        ):
            (directory / name).write_bytes(name.encode("ascii"))
        (directory / "_remote.repositories").write_text(
            "".join(
                f"{name}>{origin}=\n"
                for name in (
                    "parquet-jackson-1.17.1.jar",
                    "parquet-jackson-1.17.1.pom",
                )
            ),
            encoding="iso-8859-1",
        )
        return directory

    def _trino_external_dependencies(
        self,
        repository: Path,
        *,
        include_trino_extension: bool = True,
        trino_origin: str = "shirokuma-central",
    ) -> None:
        for prefix, required_files in package.TRINO_EXTERNAL_ARTIFACTS.items():
            if prefix == package.TRINO_BUILD_EXTENSION_PREFIX:
                continue
            directory = repository.joinpath(*prefix)
            directory.mkdir(parents=True)
            for name in required_files:
                (directory / name).write_bytes(name.encode("ascii"))
            (directory / "_remote.repositories").write_text(
                "".join(
                    f"{name}>shirokuma-central=\n"
                    for name in required_files
                ),
                encoding="iso-8859-1",
            )
        for relative in package.TRINO_EXTERNAL_METADATA_PATHS:
            metadata = repository / relative
            metadata.parent.mkdir(parents=True, exist_ok=True)
            metadata.write_text("<metadata/>\n", encoding="utf-8")
        if include_trino_extension:
            self._trino_build_extension(repository, origin=trino_origin)

    def _trino_build_extension(
        self,
        repository: Path,
        *,
        origin: str = "shirokuma-central",
    ) -> Path:
        extension = repository.joinpath(*package.TRINO_BUILD_EXTENSION_PREFIX)
        extension.mkdir(parents=True)
        for name in package.TRINO_BUILD_EXTENSION_REQUIRED_FILES:
            (extension / name).write_bytes(name.encode("ascii"))
        (extension / "_remote.repositories").write_text(
            "".join(
                f"{name}>{origin}=\n"
                for name in package.TRINO_BUILD_EXTENSION_REQUIRED_FILES
            ),
            encoding="iso-8859-1",
        )
        return extension

    def test_create_is_deterministic_and_verify_reconstructs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_repository = self._repository(root / "first")
            second_repository = self._repository(root / "second")
            first_descriptor = root / "first.json"
            second_descriptor = root / "second.json"
            first_archive = root / "first.tar.gz"
            second_archive = root / "second.tar.gz"
            package.create_snapshot(
                first_repository, first_descriptor, first_archive
            )
            package.create_snapshot(
                second_repository, second_descriptor, second_archive
            )
            self.assertEqual(
                first_descriptor.read_bytes(), second_descriptor.read_bytes()
            )
            self.assertEqual(first_archive.read_bytes(), second_archive.read_bytes())
            extracted = root / "extracted"
            package.verify_snapshot(first_descriptor, first_archive, extracted)
            self.assertEqual(b"jar", (extracted / "org/example/demo/1.0/demo-1.0.jar").read_bytes())

    def test_prune_reactor_outputs_preserves_only_exact_build_extension(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = self._repository(Path(temporary))
            extension = repository.joinpath(
                *package.TRINO_BUILD_EXTENSION_PREFIX
            )
            (extension / "trino-maven-plugin-20.jar.sha1").write_text(
                hashlib.sha1(
                    b"trino-maven-plugin-20.jar",
                    usedforsecurity=False,
                ).hexdigest()
                + "\n",
                encoding="ascii",
            )
            reactor = repository / "io/trino/trino-main/483"
            reactor.mkdir(parents=True)
            (reactor / "trino-main-483.jar").write_bytes(b"reactor")
            (reactor / "maven-metadata-local.xml").write_text(
                "<metadata/>\n", encoding="utf-8"
            )
            wrong_version = (
                repository / "io/trino/trino-maven-plugin/21"
            )
            wrong_version.mkdir(parents=True)
            (wrong_version / "trino-maven-plugin-21.jar").write_bytes(
                b"wrong-version"
            )

            package.prune_reactor_outputs(repository)

            self.assertFalse(reactor.exists())
            self.assertFalse(wrong_version.exists())
            self.assertFalse(
                (extension / "trino-maven-plugin-20.jar.sha1").exists()
            )
            for name in package.TRINO_BUILD_EXTENSION_REQUIRED_FILES:
                self.assertTrue((extension / name).is_file())
            manifest = package.build_manifest(repository)
            retained = {
                record["path"]: record["repository_origin"]
                for record in manifest["files"]
                if record["path"].startswith("io/trino/")
            }
            self.assertEqual(
                {
                    path.as_posix(): package.ALLOWED_REPOSITORIES["central"]
                    for path in package.TRINO_EXTERNAL_REQUIRED_PATHS
                },
                retained,
            )

    def test_prune_reactor_outputs_requires_exact_central_build_extension(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing = self._repository(
                root / "missing",
                include_trino_extension=False,
            )
            wrong_origin = self._repository(
                root / "wrong-origin",
                trino_origin="shirokuma-confluent",
            )
            extra_marker = self._repository(root / "extra-marker")
            extra_extension = extra_marker.joinpath(
                *package.TRINO_BUILD_EXTENSION_PREFIX
            )
            marker = extra_extension / "_remote.repositories"
            marker.write_text(
                marker.read_text(encoding="iso-8859-1")
                + "unexpected.xml>shirokuma-central=\n",
                encoding="iso-8859-1",
            )
            for name, repository in (
                ("missing", missing),
                ("wrong-origin", wrong_origin),
                ("extra-marker", extra_marker),
            ):
                with self.subTest(name=name), self.assertRaises(
                    package.SnapshotError
                ):
                    package.prune_reactor_outputs(repository)

    def test_prune_reactor_outputs_requires_complete_external_closure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing_artifact = self._repository(root / "missing-artifact")
            (
                missing_artifact
                / "io/trino/tempto/tempto-core/204/tempto-core-204.jar"
            ).unlink()
            wrong_origin = self._repository(root / "wrong-origin")
            marker = (
                wrong_origin
                / "io/trino/tempto/tempto-core/204/_remote.repositories"
            )
            marker.write_text(
                marker.read_text(encoding="iso-8859-1").replace(
                    "tempto-core-204.jar>shirokuma-central=",
                    "tempto-core-204.jar>shirokuma-confluent=",
                ),
                encoding="iso-8859-1",
            )
            missing_parent_pom = self._repository(root / "missing-parent-pom")
            (
                missing_parent_pom
                / "io/trino/tempto/tempto-root/204/tempto-root-204.pom"
            ).unlink()
            for name, repository in (
                ("missing-artifact", missing_artifact),
                ("wrong-origin", wrong_origin),
                ("missing-parent-pom", missing_parent_pom),
            ):
                with self.subTest(name=name), self.assertRaises(
                    package.SnapshotError
                ):
                    package.prune_reactor_outputs(repository)

    def test_prune_reactor_outputs_rejects_links_before_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = self._repository(Path(temporary))
            reactor = repository / "io/trino/trino-main/483"
            reactor.mkdir(parents=True)
            (reactor / "linked.jar").symlink_to(
                repository / "org/example/demo/1.0/demo-1.0.jar"
            )
            with self.assertRaises(package.SnapshotError):
                package.prune_reactor_outputs(repository)
            self.assertTrue((reactor / "linked.jar").is_symlink())

    def test_manifest_records_only_closed_origins_and_canonical_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = self._repository(root)
            descriptor = root / "manifest.json"
            archive = root / "snapshot.tar.gz"
            package.create_snapshot(repository, descriptor, archive)
            manifest = json.loads(descriptor.read_text(encoding="utf-8"))
            self.assertEqual(manifest["file_count"], len(manifest["files"]))
            self.assertEqual(
                manifest["total_bytes"],
                sum(record["size"] for record in manifest["files"]),
            )
            self.assertEqual(
                sorted(
                    record["path"].encode("utf-8")
                    for record in manifest["files"]
                ),
                [record["path"].encode("utf-8") for record in manifest["files"]],
            )
            self.assertEqual(
                {"path", "size", "mode", "sha256", "repository_origin"},
                set(manifest["files"][0]),
            )
            self.assertEqual(
                set(package.ALLOWED_ORIGINS),
                {record["repository_origin"] for record in manifest["files"]},
            )
            self.assertEqual(package.EXTERNAL_INPUTS, manifest["external_inputs"])
            self.assertEqual(
                sorted(package.EXCLUDED_RESOLVER_METADATA),
                manifest["excluded_resolver_metadata"],
            )
            self.assertTrue(
                {
                    Path(record["path"]).name
                    for record in manifest["files"]
                }.isdisjoint(package.EXCLUDED_RESOLVER_METADATA_NAMES)
            )
            self.assertFalse(
                any(
                    record["path"].endswith(
                        package.MAVEN_RESOLUTION_STATUS_SUFFIX
                    )
                    for record in manifest["files"]
                )
            )
            checksum = next(
                record
                for record in manifest["files"]
                if record["path"].endswith("demo-1.0.jar.sha1")
            )
            self.assertEqual(
                package.ALLOWED_REPOSITORIES["central"],
                checksum["repository_origin"],
            )
            with gzip.GzipFile(fileobj=io.BytesIO(archive.read_bytes())) as stream:
                stream.read(1)
                self.assertEqual(0, stream.mtime)
            with tarfile.open(archive, "r:gz") as tar:
                for member in tar:
                    self.assertTrue(member.isfile())
                    self.assertEqual((0, 0, 0, 0o644), (
                        member.uid,
                        member.gid,
                        member.mtime,
                        member.mode,
                    ))

    def test_manifest_requires_exact_parquet_source_remediation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = self._repository(root)
            remediation = (
                repository
                / "org/apache/parquet/parquet-jackson/1.17.1"
            )
            (remediation / "parquet-jackson-1.17.1.jar").unlink()
            with self.assertRaisesRegex(
                package.SnapshotError,
                "exact rebuilt parquet-jackson",
            ):
                package.build_manifest(repository)

            repository = self._repository(root / "wrong-origin")
            remediation = (
                repository
                / "org/apache/parquet/parquet-jackson/1.17.1"
            )
            (remediation / "_remote.repositories").write_text(
                "parquet-jackson-1.17.1.jar>shirokuma-central=\n"
                "parquet-jackson-1.17.1.pom>shirokuma-central=\n",
                encoding="iso-8859-1",
            )
            with self.assertRaisesRegex(
                package.SnapshotError,
                "source-remediation origin",
            ):
                package.build_manifest(repository)

    def test_parquet_source_origin_is_path_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = self._repository(Path(temporary))
            artifact = repository / "org/example/demo/1.0"
            (artifact / "_remote.repositories").write_text(
                "demo-1.0.jar>shirokuma-parquet-remediation=\n"
                "demo-1.0.pom>shirokuma-central-fallback=\n",
                encoding="iso-8859-1",
            )
            with self.assertRaisesRegex(
                package.SnapshotError,
                "unauthorized path",
            ):
                package.build_manifest(repository)

    def test_scm_remediation_is_not_a_maven_resolver_origin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = self._repository(Path(temporary))
            artifact = repository / "org/example/demo/1.0"
            (artifact / "_remote.repositories").write_text(
                "demo-1.0.jar>shirokuma-scm-remediation=\n"
                "demo-1.0.pom>shirokuma-central-fallback=\n",
                encoding="iso-8859-1",
            )
            with self.assertRaisesRegex(
                package.SnapshotError,
                "unknown Maven repository id",
            ):
                package.build_manifest(repository)

    def test_scm_metadata_requires_central_resolver_origin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = self._repository(Path(temporary))
            for record in package.SCM_METADATA_REMEDIATION["files"]:
                target = repository / record["path"]
                if target.suffix == ".pom":
                    (target.parent / "_remote.repositories").write_text(
                        f"{target.name}>shirokuma-confluent=\n",
                        encoding="iso-8859-1",
                    )
            with self.assertRaisesRegex(
                package.SnapshotError,
                "must retain the Maven Central resolver origin",
            ):
                package.build_manifest(repository)

    def test_manifest_verification_scopes_scm_metadata_remediation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = self._repository(root)
            manifest = package.build_manifest(repository)
            scm_records = {
                record["path"]: record["repository_origin"]
                for record in manifest["files"]
                if record["path"]
                in {
                    expected["path"]
                    for expected in package.SCM_METADATA_REMEDIATION["files"]
                }
            }
            self.assertEqual(
                scm_records,
                {
                    expected["path"]: package.SCM_METADATA_REMEDIATION[
                        "repository"
                    ]
                    for expected in package.SCM_METADATA_REMEDIATION["files"]
                },
            )
            unrelated = next(
                record
                for record in manifest["files"]
                if record["path"].endswith("demo-1.0.jar")
            )
            unrelated["repository_origin"] = package.SCM_METADATA_REMEDIATION[
                "repository"
            ]
            descriptor = root / "unauthorized-origin.json"
            descriptor.write_bytes(package._manifest_bytes(manifest))
            with self.assertRaisesRegex(
                package.SnapshotError,
                "unauthorized path",
            ):
                package._load_manifest(descriptor)

            manifest = package.build_manifest(repository)
            hardened = next(
                record
                for record in manifest["files"]
                if record["path"]
                == package.SCM_METADATA_REMEDIATION["files"][0]["path"]
            )
            hardened["repository_origin"] = package.ALLOWED_REPOSITORIES[
                "central"
            ]
            descriptor = root / "missing-remediation-origin.json"
            descriptor.write_bytes(package._manifest_bytes(manifest))
            with self.assertRaisesRegex(
                package.SnapshotError,
                "exact SCM metadata remediation set differs",
            ):
                package._load_manifest(descriptor)

    def test_unsafe_repository_entries_fail_closed(self) -> None:
        cases = {}
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            for name in (
                "symlink",
                "hardlink",
                "reactor",
                "partial",
                "unknown",
                "bun-tamper",
                "bun-origin-reuse",
            ):
                repository = self._repository(base / name)
                cases[name] = repository
            symlink_target = cases["symlink"] / "org/example/demo/1.0/demo-1.0.jar"
            (symlink_target.parent / "linked.jar").symlink_to(symlink_target)
            hardlink_target = cases["hardlink"] / "org/example/demo/1.0/demo-1.0.jar"
            os.link(hardlink_target, hardlink_target.parent / "hard.jar")
            reactor = cases["reactor"] / "io/trino/trino-main/483"
            reactor.mkdir(parents=True)
            (reactor / "trino-main-483.pom").write_text("reactor", encoding="utf-8")
            (cases["partial"] / "download.lastUpdated").write_text(
                "partial", encoding="utf-8"
            )
            unknown = cases["unknown"] / "org/example/other/1.0"
            unknown.mkdir(parents=True)
            (unknown / "other.jar").write_bytes(b"unknown")
            (unknown / "_remote.repositories").write_text(
                "other.jar>sonatype-nexus-snapshots=\n",
                encoding="iso-8859-1",
            )
            (
                cases["bun-tamper"] / package.BUN_INPUT["cache_path"]
            ).write_bytes(b"tampered")
            reuse = cases["bun-origin-reuse"] / "org/example/reused/1.0"
            reuse.mkdir(parents=True)
            (reuse / "reused.jar").write_bytes(b"reused")
            (reuse / "_remote.repositories").write_text(
                "reused.jar>shirokuma-bun-release=\n",
                encoding="iso-8859-1",
            )
            for name, repository in cases.items():
                with self.subTest(name=name):
                    with self.assertRaises(package.SnapshotError):
                        package.build_manifest(repository)

    def test_resolution_status_requires_a_safe_allowlisted_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            accepted = self._repository(base / "accepted")
            confluent = accepted / "io/confluent/common-config/8.1.1"
            confluent.mkdir(parents=True)
            (confluent / "common-config-8.1.1.jar").write_bytes(b"confluent")
            (confluent / "common-config-8.1.1.jar.lastUpdated").write_text(
                "# failed fallback attempt\n", encoding="iso-8859-1"
            )
            (confluent / "_remote.repositories").write_text(
                "common-config-8.1.1.jar>shirokuma-confluent=\n",
                encoding="iso-8859-1",
            )
            manifest = package.build_manifest(accepted)
            accepted_records = {
                record["path"]: record for record in manifest["files"]
            }
            accepted_path = (
                "io/confluent/common-config/8.1.1/common-config-8.1.1.jar"
            )
            self.assertEqual(
                package.ALLOWED_REPOSITORIES["confluent"],
                accepted_records[accepted_path]["repository_origin"],
            )
            self.assertNotIn(f"{accepted_path}.lastUpdated", accepted_records)

            cases = {}
            for name in (
                "hardlink",
                "symlink",
                "oversized",
                "orphan",
                "nested-status",
                "temporary-target",
                "conflict",
            ):
                cases[name] = self._repository(base / name)
            artifact = Path("org/example/demo/1.0")
            hardlink = cases["hardlink"] / artifact / "demo-1.0.jar.lastUpdated"
            hardlink.unlink()
            os.link(cases["hardlink"] / artifact / "demo-1.0.jar", hardlink)
            symlink = cases["symlink"] / artifact / "demo-1.0.jar.lastUpdated"
            symlink.unlink()
            symlink.symlink_to("demo-1.0.jar")
            (cases["oversized"] / artifact / "demo-1.0.jar.lastUpdated").write_bytes(
                b"x" * (package.MAX_RESOLVER_METADATA_BYTES + 1)
            )
            (cases["orphan"] / artifact / "orphan.jar.lastUpdated").write_text(
                "orphan", encoding="iso-8859-1"
            )
            (
                cases["nested-status"]
                / artifact
                / "demo-1.0.jar.lastUpdated.lastUpdated"
            ).write_text("nested", encoding="iso-8859-1")
            (
                cases["temporary-target"]
                / artifact
                / "demo-1.0.jar.part.lastUpdated"
            ).write_text("temporary", encoding="iso-8859-1")
            conflict_marker = cases["conflict"] / artifact / "_remote.repositories"
            conflict_marker.write_text(
                conflict_marker.read_text(encoding="iso-8859-1")
                + "demo-1.0.jar.lastUpdated>shirokuma-confluent=\n",
                encoding="iso-8859-1",
            )
            for name, repository in cases.items():
                with self.subTest(name=name):
                    with self.assertRaises(package.SnapshotError):
                        package.build_manifest(repository)

    def test_checksum_sidecars_require_a_valid_target_and_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            cases = {}
            for name in (
                "mismatch",
                "malformed",
                "oversized",
                "orphan",
                "nested",
                "conflict",
            ):
                cases[name] = self._repository(base / name)
            artifact = Path("org/example/demo/1.0")
            (cases["mismatch"] / artifact / "demo-1.0.jar.sha1").write_text(
                "0" * 40 + "\n",
                encoding="ascii",
            )
            (cases["malformed"] / artifact / "demo-1.0.jar.sha1").write_text(
                hashlib.sha1(b"jar", usedforsecurity=False).hexdigest()
                + "  demo-1.0.jar\n",
                encoding="ascii",
            )
            (cases["oversized"] / artifact / "demo-1.0.jar.sha1").write_text(
                "0" * (package.MAX_CHECKSUM_SIDECAR_BYTES + 1),
                encoding="ascii",
            )
            (cases["orphan"] / artifact / "orphan.jar.sha1").write_text(
                hashlib.sha1(b"orphan", usedforsecurity=False).hexdigest() + "\n",
                encoding="ascii",
            )
            (cases["nested"] / artifact / "demo-1.0.jar.sha1.sha1").write_text(
                hashlib.sha1(b"nested", usedforsecurity=False).hexdigest() + "\n",
                encoding="ascii",
            )
            conflict_marker = cases["conflict"] / artifact / "_remote.repositories"
            conflict_marker.write_text(
                conflict_marker.read_text(encoding="iso-8859-1")
                + "demo-1.0.jar.sha1>shirokuma-confluent=\n",
                encoding="iso-8859-1",
            )
            for name, repository in cases.items():
                with self.subTest(name=name):
                    with self.assertRaises(package.SnapshotError):
                        package.build_manifest(repository)

    def test_noncanonical_manifest_types_and_archive_links_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = self._repository(root)
            descriptor = root / "manifest.json"
            archive = root / "snapshot.tar.gz"
            package.create_snapshot(repository, descriptor, archive)
            manifest = json.loads(descriptor.read_text(encoding="utf-8"))
            mutations = {
                "root-array": lambda value: value["files"],
                "boolean-schema": lambda value: {
                    **value,
                    "schema_version": True,
                },
                "boolean-size": lambda value: {
                    **value,
                    "files": [
                        {**value["files"][0], "size": True},
                        *value["files"][1:],
                    ],
                },
                "numeric-path": lambda value: {
                    **value,
                    "files": [
                        {**value["files"][0], "path": 1},
                        *value["files"][1:],
                    ],
                },
                "array-origin": lambda value: {
                    **value,
                    "files": [
                        {**value["files"][0], "repository_origin": []},
                        *value["files"][1:],
                    ],
                },
            }
            for name, mutate in mutations.items():
                malformed = root / f"{name}.json"
                malformed.write_bytes(package._manifest_bytes(mutate(manifest)))
                with self.subTest(name=name):
                    with self.assertRaises(package.SnapshotError):
                        package.verify_snapshot(malformed, archive, None)

            symlink = root / "linked.tar.gz"
            symlink.symlink_to(archive)
            hardlink = root / "hardlinked.tar.gz"
            os.link(archive, hardlink)
            for name, linked_archive in (
                ("symlink", symlink),
                ("hardlink", hardlink),
            ):
                with self.subTest(name=name):
                    with self.assertRaises(package.SnapshotError):
                        package.verify_snapshot(descriptor, linked_archive, None)

    def test_manifest_requires_exact_central_external_closure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing_tempto = self._repository(root / "missing-tempto")
            (
                missing_tempto
                / "io/trino/tempto/tempto-core/204/tempto-core-204.jar"
            ).unlink()
            wrong_tempto_origin = self._repository(
                root / "wrong-tempto-origin"
            )
            marker = (
                wrong_tempto_origin
                / "io/trino/tempto/tempto-core/204/_remote.repositories"
            )
            marker.write_text(
                marker.read_text(encoding="iso-8859-1").replace(
                    "tempto-core-204.jar>shirokuma-central=",
                    "tempto-core-204.jar>shirokuma-confluent=",
                ),
                encoding="iso-8859-1",
            )
            invalid_repositories = {
                "missing": self._repository(
                    root / "missing",
                    include_trino_extension=False,
                ),
                "wrong-origin": self._repository(
                    root / "wrong-origin",
                    trino_origin="shirokuma-confluent",
                ),
                "missing-tempto": missing_tempto,
                "wrong-tempto-origin": wrong_tempto_origin,
            }
            for name, repository in invalid_repositories.items():
                with self.subTest(stage="create", name=name), self.assertRaises(
                    package.SnapshotError
                ):
                    package.build_manifest(repository)

            repository = self._repository(root / "valid")
            descriptor = root / "manifest.json"
            archive = root / "snapshot.tar.gz"
            package.create_snapshot(repository, descriptor, archive)
            manifest = json.loads(descriptor.read_text(encoding="utf-8"))
            required_paths = {
                path.as_posix()
                for path in package.TRINO_BUILD_EXTENSION_REQUIRED_PATHS
            }
            extension_records = [
                record
                for record in manifest["files"]
                if record["path"] in required_paths
            ]
            removed = extension_records[0]
            missing_manifest = {
                **manifest,
                "files": [
                    record
                    for record in manifest["files"]
                    if record["path"] != removed["path"]
                ],
                "file_count": manifest["file_count"] - 1,
                "total_bytes": manifest["total_bytes"] - removed["size"],
            }
            wrong_origin_manifest = {
                **manifest,
                "files": [
                    {
                        **record,
                        "repository_origin": package.ALLOWED_REPOSITORIES[
                            "confluent"
                        ],
                    }
                    if record["path"] == extension_records[0]["path"]
                    else record
                    for record in manifest["files"]
                ],
            }
            for name, mutated in (
                ("missing", missing_manifest),
                ("wrong-origin", wrong_origin_manifest),
            ):
                malformed = root / f"{name}-extension.json"
                malformed.write_bytes(package._manifest_bytes(mutated))
                with self.subTest(stage="verify", name=name), self.assertRaises(
                    package.SnapshotError
                ):
                    package.verify_snapshot(malformed, archive, None)

    def test_tampered_archive_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = self._repository(root)
            descriptor = root / "manifest.json"
            archive = root / "snapshot.tar.gz"
            package.create_snapshot(repository, descriptor, archive)
            tampered = root / "tampered.tar.gz"
            with tarfile.open(archive, "r:gz") as source:
                members = source.getmembers()
                payloads = {
                    member.name: source.extractfile(member).read()
                    for member in members
                }
            first = members[0]
            payloads[first.name] = b"x" * first.size
            with tampered.open("xb") as raw:
                with gzip.GzipFile(
                    filename="", mode="wb", fileobj=raw, mtime=0
                ) as compressed:
                    with tarfile.open(
                        fileobj=compressed, mode="w", format=tarfile.GNU_FORMAT
                    ) as target:
                        for member in members:
                            target.addfile(member, io.BytesIO(payloads[member.name]))
            with self.assertRaises(package.SnapshotError):
                package.verify_snapshot(descriptor, tampered, None)


class BunInputTests(unittest.TestCase):
    def _archive(self, path: Path, payload: bytes = b"bun") -> None:
        with zipfile.ZipFile(path, "w") as archive:
            directory = zipfile.ZipInfo("bun-linux-aarch64/")
            directory.external_attr = (stat.S_IFDIR | 0o755) << 16
            archive.writestr(directory, b"")
            executable = zipfile.ZipInfo("bun-linux-aarch64/bun")
            executable.external_attr = (stat.S_IFREG | 0o755) << 16
            archive.writestr(executable, payload)

    def _contract(self, archive: Path):
        payload = archive.read_bytes()
        return mock.patch.multiple(
            bun,
            BUN_SIZE=len(payload),
            BUN_SHA256=hashlib.sha256(payload).hexdigest(),
        )

    def test_exact_archive_is_staged_at_frontend_plugin_cache_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "bun.zip"
            repository = root / "repository"
            repository.mkdir()
            self._archive(archive)
            with self._contract(archive):
                target = bun.stage_archive(archive, repository)
                self.assertEqual(repository / bun.BUN_CACHE_PATH, target)
                self.assertEqual(archive.read_bytes(), target.read_bytes())
                self.assertEqual(
                    f"{target.name}>{bun.BUN_ORIGIN_ID}=\n",
                    (target.parent / "_remote.repositories").read_text(
                        encoding="iso-8859-1"
                    ),
                )
                with self.assertRaises(bun.BunInputError):
                    bun.stage_archive(archive, repository)

    def test_archive_tampering_and_unsafe_members_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "bun.zip"
            self._archive(archive)
            with self._contract(archive):
                archive.write_bytes(archive.read_bytes() + b"tampered")
                with self.assertRaises(bun.BunInputError):
                    bun.verify_archive(archive)
            unsafe = root / "unsafe.zip"
            with zipfile.ZipFile(unsafe, "w") as bundle:
                bundle.writestr("../bun", b"bun")
            with self._contract(unsafe):
                with self.assertRaises(bun.BunInputError):
                    bun.verify_archive(unsafe)

    def test_download_validates_each_redirect_origin_before_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.zip"
            output = root / "downloaded.zip"
            self._archive(source)
            payload = source.read_bytes()
            redirected = (
                "https://release-assets.githubusercontent.com/"
                "github-production-release-asset/bun.zip?token=reviewed"
            )
            redirect_headers = Message()
            redirect_headers.add_header("Location", redirected)
            redirect_response = mock.Mock(status=302, headers=redirect_headers)
            redirect_connection = mock.Mock()
            final_headers = Message()
            final_headers.add_header("Content-Length", str(len(payload)))
            final_response = mock.Mock(status=200, headers=final_headers)
            final_response.read.side_effect = [payload, b""]
            final_connection = mock.Mock()
            with (
                self._contract(source),
                mock.patch.object(
                    bun,
                    "_open_https",
                    side_effect=[
                        (redirect_connection, redirect_response),
                        (final_connection, final_response),
                    ],
                ) as open_https,
            ):
                origins = bun.download_archive(bun.BUN_URL, output)
            self.assertEqual(payload, output.read_bytes())
            self.assertEqual(
                (
                    "https://github.com",
                    "https://release-assets.githubusercontent.com",
                ),
                origins,
            )
            self.assertEqual(
                [bun.BUN_URL, redirected],
                [call.args[0] for call in open_https.call_args_list],
            )
            redirect_response.close.assert_called_once_with()
            final_response.close.assert_called_once_with()
            redirect_connection.close.assert_called_once_with()
            final_connection.close.assert_called_once_with()

    def test_download_rejects_unallowlisted_redirect_before_request(self) -> None:
        unsafe_redirects = (
            "https://release-assets.githubusercontent.com.evil.invalid/bun.zip",
            "http://release-assets.githubusercontent.com/bun.zip",
            "https://user@release-assets.githubusercontent.com/bun.zip",
        )
        for redirected in unsafe_redirects:
            with self.subTest(redirected=redirected), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / "downloaded.zip"
                headers = Message()
                headers.add_header("Location", redirected)
                response = mock.Mock(status=302, headers=headers)
                connection = mock.Mock()
                with (
                    mock.patch.object(
                        bun,
                        "_open_https",
                        return_value=(connection, response),
                    ) as open_https,
                    self.assertRaises(bun.BunInputError),
                ):
                    bun.download_archive(bun.BUN_URL, output)
                self.assertEqual(1, open_https.call_count)
                self.assertFalse(output.exists())
                response.close.assert_called_once_with()
                connection.close.assert_called_once_with()


class BunScanEvidenceTests(unittest.TestCase):
    LOCKFILES = {
        "webapp/bun.lock": b"webapp lock\n",
        "legacy/bun.lock": b"legacy lock\n",
    }
    PACKAGE_EXPECTATIONS = {
        "webapp/bun.lock": {
            "package_count": 2,
            "required_packages": frozenset({"web-package"}),
        },
        "legacy/bun.lock": {
            "package_count": 1,
            "required_packages": frozenset({"legacy-package"}),
        },
    }

    def _cache_contract(self) -> dict[str, object]:
        return {
            **verify.EXPECTED_BUN_PACKAGE_CACHE,
            "frozen_lockfiles": [
                {
                    "path": path,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
                for path, payload in self.LOCKFILES.items()
            ],
        }

    def _write_lockfiles(self, root: Path) -> None:
        for relative, payload in self.LOCKFILES.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)

    def _overlay_contract(self) -> dict[str, object]:
        return json.loads(json.dumps(verify.EXPECTED_SOURCE_OVERLAY))

    @contextlib.contextmanager
    def _scan_contract(self):
        def load_json(path: Path) -> dict[str, object]:
            if path.as_posix().endswith(verify.CONTRACT_PATH.as_posix()):
                return {}
            return json.loads(path.read_text(encoding="utf-8"))

        with (
            mock.patch.object(
                verify,
                "EXPECTED_BUN_PACKAGE_CACHE",
                self._cache_contract(),
            ),
            mock.patch.object(
                verify,
                "EXPECTED_BUN_SCAN_RESULTS",
                self.PACKAGE_EXPECTATIONS,
            ),
            mock.patch.object(
                verify,
                "EXPECTED_SOURCE_OVERLAY",
                self._overlay_contract(),
            ),
            mock.patch.object(
                verify,
                "_validate_source_overlay_contract",
            ),
            mock.patch.object(
                verify,
                "_load_json",
                side_effect=load_json,
            ),
        ):
            yield

    def _report(
        self,
        root: Path,
        name: str,
        *,
        finding: bool = False,
        missing_sentinel: bool = False,
    ) -> Path:
        packages = {
            "webapp/bun.lock": [
                {"Name": "web-package"},
                {"Name": "transitive-package"},
            ],
            "legacy/bun.lock": [{"Name": "legacy-package"}],
        }
        if missing_sentinel:
            packages["webapp/bun.lock"][0] = {"Name": "different-package"}
        vulnerability = {
            "VulnerabilityID": "CVE-2026-67213",
            "PkgName": "nanoid",
            "InstalledVersion": "3.3.17",
            "FixedVersion": "3.3.18",
            "Severity": "HIGH",
            "PkgIdentifier": {
                "PURL": (
                    "pkg:npm/nanoid@3.3.17"
                )
            },
        }
        report = root / name
        report.write_text(
            json.dumps(
                {
                    "SchemaVersion": 2,
                    "ArtifactType": "filesystem",
                    "Results": [
                        {
                            "Target": target,
                            "Class": "lang-pkgs",
                            "Type": "bun",
                            "Packages": records,
                            **(
                                {"Vulnerabilities": [vulnerability]}
                                if target == "webapp/bun.lock"
                                and finding
                                else {}
                            ),
                        }
                        for target, records in packages.items()
                    ],
                }
            ),
            encoding="utf-8",
        )
        return report

    def test_bun_scan_report_schema_version_is_an_exact_integer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = self._report(Path(temporary), "raw.json")
            document = json.loads(report.read_text(encoding="utf-8"))
            document["SchemaVersion"] = 2.0
            report.write_text(json.dumps(document), encoding="utf-8")

            with self.assertRaisesRegex(
                verify.ContractError,
                "unexpected Trivy report envelope",
            ):
                verify._bun_scan_report(report)

    def test_stage_bun_scan_input_copies_only_hash_bound_lockfiles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checkout = root / "checkout"
            checkout.mkdir()
            self._write_lockfiles(checkout)
            output = root / "scan-input"
            with mock.patch.object(
                verify,
                "EXPECTED_BUN_PACKAGE_CACHE",
                self._cache_contract(),
            ):
                verify.stage_bun_scan_input(checkout, output)
            self.assertEqual(
                set(self.LOCKFILES),
                {
                    path.relative_to(output).as_posix()
                    for path in output.rglob("*")
                    if path.is_file()
                },
            )
            for relative, payload in self.LOCKFILES.items():
                path = output / relative
                self.assertEqual(payload, path.read_bytes())
                self.assertEqual(0o444, stat.S_IMODE(path.stat().st_mode))

    def test_verify_bun_scan_requires_each_expected_package_graph(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scan_input = root / "scan-input"
            scan_input.mkdir()
            self._write_lockfiles(scan_input)
            report = self._report(root, "report.json")
            with self._scan_contract():
                verify.verify_bun_scan(root, scan_input, report)

    def test_verify_bun_scan_rejects_missing_expected_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scan_input = root / "scan-input"
            scan_input.mkdir()
            self._write_lockfiles(scan_input)
            report = self._report(root, "report.json", missing_sentinel=True)
            with (
                self._scan_contract(),
                self.assertRaisesRegex(
                    verify.ContractError,
                    "required packages missing",
                ),
            ):
                verify.verify_bun_scan(root, scan_input, report)

    def test_verify_bun_scan_rejects_any_high_or_critical_finding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scan_input = root / "scan-input"
            scan_input.mkdir()
            self._write_lockfiles(scan_input)
            report = self._report(root, "report.json", finding=True)
            with (
                self._scan_contract(),
                self.assertRaisesRegex(
                    verify.ContractError,
                    "BUN_SCAN_FINDING",
                ),
            ):
                verify.verify_bun_scan(root, scan_input, report)

    def test_verify_bun_snapshot_requires_reviewed_identities(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            descriptor = root / "manifest.json"
            archive = root / "snapshot.tar.gz"
            descriptor.write_bytes(b"reviewed manifest\n")
            archive.write_bytes(b"reviewed archive\n")
            contract = {
                **self._cache_contract(),
                "reviewed_snapshot": {
                    "manifest_sha256": hashlib.sha256(
                        descriptor.read_bytes()
                    ).hexdigest(),
                    "archive_sha256": hashlib.sha256(
                        archive.read_bytes()
                    ).hexdigest(),
                    "archive_size": archive.stat().st_size,
                },
            }
            with mock.patch.object(
                verify,
                "EXPECTED_BUN_PACKAGE_CACHE",
                contract,
            ):
                verify.verify_bun_snapshot_identity(descriptor, archive)
                archive.write_bytes(b"different archive\n")
                with self.assertRaisesRegex(
                    verify.ContractError,
                    "archive identity differs",
                ):
                    verify.verify_bun_snapshot_identity(descriptor, archive)


class MavenScanEvidenceTests(unittest.TestCase):
    JARS = (
        "org/example/alpha/1.0/alpha-1.0.jar",
        "org/example/beta/2.0/beta-2.0.jar",
    )

    def _descriptor(self, root: Path) -> Path:
        records = [
            {
                "mode": "0644",
                "path": path,
                "repository_origin": "https://repo.maven.apache.org/maven2/",
                "sha256": hashlib.sha256(path.encode()).hexdigest(),
                "size": len(path),
            }
            for path in self.JARS
        ]
        descriptor = root / "descriptor.json"
        descriptor.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "file_count": len(records),
                    "files": records,
                }
            ),
            encoding="utf-8",
        )
        return descriptor

    def _repository_descriptor(
        self,
        root: Path,
        archives: dict[str, dict[str, bytes | tuple[bytes, int]]],
    ) -> tuple[Path, Path]:
        repository = root / "repository"
        records = []
        for path, entries in archives.items():
            destination = repository / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(destination, "w") as archive:
                for name, payload in entries.items():
                    if isinstance(payload, tuple):
                        payload, mode = payload
                        entry = zipfile.ZipInfo(name)
                        entry.create_system = 3
                        entry.external_attr = mode << 16
                        archive.writestr(entry, payload)
                    else:
                        archive.writestr(name, payload)
            destination.chmod(0o644)
            payload = destination.read_bytes()
            records.append(
                {
                    "mode": "0644",
                    "path": path,
                    "repository_origin": (
                        "https://repo.maven.apache.org/maven2/"
                    ),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "size": len(payload),
                }
            )
        descriptor = root / "descriptor.json"
        descriptor.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "file_count": len(records),
                    "files": records,
                }
            ),
            encoding="utf-8",
        )
        return repository, descriptor

    def _report(
        self,
        root: Path,
        paths: tuple[str, ...] | None = None,
        extra_packages: tuple[tuple[str, str], ...] = (),
    ) -> Path:
        packages = [
            (path, verify._maven_purl(path))
            for path in (paths if paths is not None else self.JARS)
        ]
        packages.extend(extra_packages)
        report = root / "report.json"
        report.write_text(
            json.dumps(
                {
                    "SchemaVersion": 2,
                    "ArtifactName": "/tmp/maven-repository-a",
                    "ArtifactType": "rootfs",
                    "Results": [
                        {
                            "Target": "Java",
                            "Class": "lang-pkgs",
                            "Type": "jar",
                            "Packages": [
                                {
                                    "Name": Path(path).stem,
                                    "FilePath": path,
                                    "Identifier": {
                                        "PURL": purl,
                                    },
                                }
                                for path, purl in packages
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return report

    def _sbom(self, root: Path, paths: tuple[str, ...] | None = None) -> Path:
        root_ref = "urn:test:maven-root"
        selected_paths = paths if paths is not None else self.JARS
        component_refs = [verify._maven_purl(path) for path in selected_paths]
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
                            "name": "/tmp/maven-repository-a",
                        }
                    },
                    "components": [
                        {
                            "bom-ref": verify._maven_purl(path),
                            "type": "library",
                            "name": Path(path).stem,
                            "purl": verify._maven_purl(path),
                            "properties": [
                                {
                                    "name": "aquasecurity:trivy:FilePath",
                                    "value": path,
                                }
                            ],
                        }
                        for path in selected_paths
                    ],
                    "dependencies": [
                        {"ref": root_ref, "dependsOn": component_refs},
                        *[
                            {"ref": component_ref, "dependsOn": []}
                            for component_ref in component_refs
                        ],
                    ],
                }
            ),
            encoding="utf-8",
        )
        return sbom

    def test_maven_descriptor_identity_types_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            descriptor = self._descriptor(Path(temporary))
            original = json.loads(descriptor.read_text(encoding="utf-8"))
            for field, replacement in (
                ("schema_version", 2.0),
                ("file_count", float(original["file_count"])),
            ):
                altered = copy.deepcopy(original)
                altered[field] = replacement
                descriptor.write_text(json.dumps(altered), encoding="utf-8")
                with self.subTest(field=field):
                    with self.assertRaisesRegex(
                        verify.ContractError,
                        "closed Maven descriptor differs",
                    ):
                        verify._maven_jar_records(descriptor)

            altered = copy.deepcopy(original)
            altered["files"][0]["size"] = True
            descriptor.write_text(json.dumps(altered), encoding="utf-8")
            with self.assertRaisesRegex(
                verify.ContractError,
                "invalid Maven JAR identity",
            ):
                verify._maven_jar_records(descriptor)

            altered = copy.deepcopy(original)
            altered["files"][0]["sha256"] = int("1" * 64)
            descriptor.write_text(json.dumps(altered), encoding="utf-8")
            with self.assertRaisesRegex(
                verify.ContractError,
                "invalid Maven JAR identity",
            ):
                verify._maven_jar_records(descriptor)


    def test_maven_descriptor_scopes_parquet_remediation_origin_to_exact_jar(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parquet_path = verify.EXPECTED_PARQUET_REMEDIATION_JAR_PATH
            record = {
                "mode": "0644",
                "path": parquet_path,
                "repository_origin": (
                    verify.EXPECTED_PARQUET_SOURCE_REMEDIATION["repository"]
                ),
                "sha256": hashlib.sha256(parquet_path.encode()).hexdigest(),
                "size": len(parquet_path),
            }
            descriptor = root / "descriptor.json"

            def write_descriptor() -> None:
                descriptor.write_text(
                    json.dumps(
                        {
                            "schema_version": 2,
                            "file_count": 1,
                            "files": [record],
                        }
                    ),
                    encoding="utf-8",
                )

            write_descriptor()
            self.assertEqual(
                {parquet_path},
                set(verify._maven_jar_records(descriptor)),
            )

            record["repository_origin"] = (
                verify.EXPECTED_REPOSITORIES["central"]
            )
            write_descriptor()
            with self.assertRaisesRegex(
                verify.ContractError,
                "invalid Maven JAR identity",
            ):
                verify._maven_jar_records(descriptor)

            record["repository_origin"] = []
            write_descriptor()
            with self.assertRaisesRegex(
                verify.ContractError,
                "invalid Maven JAR identity",
            ):
                verify._maven_jar_records(descriptor)

            record["path"] = self.JARS[0]
            record["repository_origin"] = (
                verify.EXPECTED_PARQUET_SOURCE_REMEDIATION["repository"]
            )
            write_descriptor()
            with self.assertRaisesRegex(
                verify.ContractError,
                "invalid Maven JAR identity",
            ):
                verify._maven_jar_records(descriptor)

    def test_complete_rootfs_maven_inventory_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            descriptor = self._descriptor(root)
            report = self._report(root)
            sbom = self._sbom(root)
            verify.verify_maven_scan(descriptor, sbom, report)
            incomplete = self._report(root, self.JARS[:1])
            with self.assertRaisesRegex(
                verify.ContractError,
                "MAVEN_SCAN_CLOSURE",
            ):
                verify.verify_maven_scan(descriptor, sbom, incomplete)

    def test_maven_sbom_generation_requires_every_descriptor_jar(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            descriptor = self._descriptor(root)
            incomplete_rootfs = self._sbom(root, self.JARS[:1])
            with self.assertRaisesRegex(
                verify.ContractError,
                "MAVEN_SBOM_ROOTFS",
            ):
                verify.generate_maven_sbom(
                    descriptor,
                    root,
                    incomplete_rootfs,
                    root / "generated-sbom.json",
                )

    def test_maven_sbom_treats_wrong_only_rootfs_purl_as_omission(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            descriptor = self._descriptor(root)
            rootfs = self._sbom(root)
            document = json.loads(rootfs.read_text(encoding="utf-8"))
            beta_path = self.JARS[1]
            beta_component = next(
                component
                for component in document["components"]
                if beta_path in verify._component_file_paths(component)
            )
            beta_component["purl"] = "pkg:maven/org.example/wrong@2.0"
            rootfs.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(
                verify.ContractError,
                "outside the reviewed closed set",
            ):
                verify.generate_maven_sbom(
                    descriptor,
                    root,
                    rootfs,
                    root / "generated-sbom.json",
                )

    def test_maven_sbom_preserves_embedded_purl_on_top_level_path(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            descriptor = self._descriptor(root)
            rootfs = self._sbom(root)
            document = json.loads(rootfs.read_text(encoding="utf-8"))
            beta_path = self.JARS[1]
            beta_component = next(
                component
                for component in document["components"]
                if beta_path in verify._component_file_paths(component)
            )
            embedded_ref = "urn:test:embedded-on-beta-path"
            embedded_component = json.loads(json.dumps(beta_component))
            embedded_component["bom-ref"] = embedded_ref
            embedded_component["purl"] = (
                "pkg:maven/org.example/embedded@3.0"
            )
            document["components"].append(embedded_component)
            root_ref = document["metadata"]["component"]["bom-ref"]
            root_dependency = next(
                dependency
                for dependency in document["dependencies"]
                if dependency["ref"] == root_ref
            )
            root_dependency["dependsOn"].append(embedded_ref)
            document["dependencies"].append(
                {"ref": embedded_ref, "dependsOn": []}
            )
            rootfs.write_text(json.dumps(document), encoding="utf-8")
            generated = root / "generated-sbom.json"
            verify.generate_maven_sbom(
                descriptor,
                root,
                rootfs,
                generated,
            )
            result = json.loads(generated.read_text(encoding="utf-8"))
            self.assertEqual(
                {
                    verify._maven_purl(beta_path),
                    "pkg:maven/org.example/embedded@3.0",
                },
                {
                    component["purl"]
                    for component in result["components"]
                    if beta_path
                    in verify._component_file_paths(component)
                },
            )

    def test_maven_sbom_supplements_classifier_erased_trivy_purl(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            classifier_path = (
                "org/example/beta/2.0/beta-2.0-linux-aarch_64.jar"
            )
            repository, descriptor = self._repository_descriptor(
                root,
                {classifier_path: {"native.bin": b"classifier payload"}},
            )
            rootfs = self._sbom(root, (classifier_path,))
            document = json.loads(rootfs.read_text(encoding="utf-8"))
            exact_purl = verify._maven_purl(classifier_path)
            base_purl = exact_purl.split("?classifier=", 1)[0]
            component = document["components"][0]
            component["bom-ref"] = base_purl
            component["purl"] = base_purl
            for dependency in document["dependencies"]:
                if dependency["ref"] == exact_purl:
                    dependency["ref"] = base_purl
                dependency["dependsOn"] = [
                    base_purl if ref == exact_purl else ref
                    for ref in dependency["dependsOn"]
                ]
            rootfs.write_text(json.dumps(document), encoding="utf-8")
            generated = root / "generated-sbom.json"
            verify.generate_maven_sbom(
                descriptor,
                repository,
                rootfs,
                generated,
            )
            result = json.loads(generated.read_text(encoding="utf-8"))
            path_components = [
                component
                for component in result["components"]
                if classifier_path
                in verify._component_file_paths(component)
            ]
            self.assertEqual(
                {base_purl, exact_purl},
                {component["purl"] for component in path_components},
            )
            canonical = next(
                component
                for component in path_components
                if component["purl"] == exact_purl
            )
            self.assertIn(
                {
                    "name": "shirokuma:rootfs-discovery",
                    "value": "trivy-classifier-erased-purl",
                },
                canonical["properties"],
            )

    def test_maven_sbom_rejects_unrelated_classifier_purl(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            classifier_path = (
                "org/example/beta/2.0/beta-2.0-linux-aarch_64.jar"
            )
            repository, descriptor = self._repository_descriptor(
                root,
                {classifier_path: {"native.bin": b"classifier payload"}},
            )
            rootfs = self._sbom(root, (classifier_path,))
            document = json.loads(rootfs.read_text(encoding="utf-8"))
            document["components"][0]["purl"] = (
                "pkg:maven/org.example/unrelated@2.0"
            )
            rootfs.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(
                verify.ContractError,
                "outside the reviewed closed set",
            ):
                verify.generate_maven_sbom(
                    descriptor,
                    repository,
                    rootfs,
                    root / "generated-sbom.json",
                )

    def test_maven_sbom_rejects_omissions_outside_reviewed_contract(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            alpha = self.JARS[0]
            unknown_sources = (
                "org/example/alpha/1.0/alpha-1.0-sources.jar"
            )
            repository, descriptor = self._repository_descriptor(
                root,
                {
                    alpha: {"org/example/Alpha.class": b"alpha"},
                    unknown_sources: {
                        "org/example/Alpha.java": b"class Alpha {}",
                    },
                },
            )
            with self.assertRaisesRegex(
                verify.ContractError,
                "outside the reviewed closed set",
            ):
                verify.generate_maven_sbom(
                    descriptor,
                    repository,
                    self._sbom(root, (alpha,)),
                    root / "generated.json",
                )

    def test_maven_sbom_authorizes_exact_reviewed_omission_subset(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            alpha = self.JARS[0]
            failsafe = (
                "dev/failsafe/failsafe/3.3.2/failsafe-3.3.2.jar"
            )
            failsafe_sources = (
                "dev/failsafe/failsafe/3.3.2/"
                "failsafe-3.3.2-sources.jar"
            )
            repository, descriptor = self._repository_descriptor(
                root,
                {
                    alpha: {"org/example/Alpha.class": b"alpha"},
                    failsafe: {
                        "dev/failsafe/Failsafe.class": (
                            b"descriptor-bound opaque class payload"
                        ),
                    },
                    failsafe_sources: {
                        "dev/failsafe/Failsafe.java": b"class Failsafe {}",
                    },
                },
            )
            generated = root / "generated.json"
            verify.generate_maven_sbom(
                descriptor,
                repository,
                self._sbom(root, (alpha,)),
                generated,
            )
            result = json.loads(generated.read_text(encoding="utf-8"))
            metadata = {
                prop["name"]: prop["value"]
                for prop in result["metadata"]["properties"]
            }
            self.assertEqual(
                "2",
                metadata[
                    "shirokuma:rootfs-contract-authorized-omissions"
                ],
            )
            self.assertEqual(
                "1",
                metadata["shirokuma:rootfs-contract-supplemental-jars"],
            )
            modes = {
                path: prop["value"]
                for component in result["components"]
                for path in verify._component_file_paths(component)
                for prop in component.get("properties", [])
                if prop.get("name") == "shirokuma:rootfs-discovery"
            }
            self.assertEqual(
                {
                    failsafe: "contract-base-coordinate",
                    failsafe_sources: "contract-supplemental-sources",
                },
                modes,
            )
            identities = tuple(
                (path, component["purl"])
                for component in result["components"]
                for path in verify._component_file_paths(component)
            )
            verify.verify_maven_scan(
                descriptor,
                generated,
                self._report(root, paths=(), extra_packages=identities),
            )

    def test_reviewed_omission_identity_is_derived_from_exact_path(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            alpha = self.JARS[0]
            failsafe = (
                "dev/failsafe/failsafe/3.3.2/failsafe-3.3.2.jar"
            )
            repository, descriptor = self._repository_descriptor(
                root,
                {
                    alpha: {"org/example/Alpha.class": b"alpha"},
                    failsafe: {"dev/failsafe/Failsafe.class": b"opaque"},
                },
            )
            altered = [
                dict(entry)
                for entry in verify.EXPECTED_TRIVY_ROOTFS_OMISSIONS
                if entry["path"] == failsafe
            ]
            altered[0]["purl"] = "pkg:maven/dev.failsafe/other@3.3.2"
            with (
                mock.patch.object(
                    verify,
                    "EXPECTED_TRIVY_ROOTFS_OMISSIONS",
                    altered,
                ),
                self.assertRaisesRegex(
                    verify.ContractError,
                    "differs from its reviewed omission identity",
                ),
            ):
                verify.generate_maven_sbom(
                    descriptor,
                    repository,
                    self._sbom(root, (alpha,)),
                    root / "generated.json",
                )

    def test_maven_sbom_bounds_omitted_jar_decompression(self) -> None:
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("payload.bin", b"x" * 65)
        with (
            mock.patch.object(
                verify,
                "MAX_OMITTED_JAR_MEMBER_BYTES",
                64,
            ),
            self.assertRaisesRegex(
                verify.ContractError,
                "exceeds omitted-JAR decompression limits",
            ),
        ):
            verify._jar_entries(payload.getvalue(), "bounded.jar")

    def test_maven_sbom_bounds_omitted_jar_archive_inventory(
        self,
    ) -> None:
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("A.class", b"opaque class payload")
            archive.writestr("empty.txt", b"")
            archive.comment = b"reviewed-PK\x05\x06-comment"
        archive_payload = payload.getvalue()
        summary = verify._zip_directory_summary(archive_payload)
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(summary[0], 2)
        cases = (
            (
                "MAX_OMITTED_JAR_ARCHIVE_BYTES",
                len(archive_payload) - 1,
            ),
            ("MAX_OMITTED_JAR_MEMBERS", 1),
            (
                "MAX_OMITTED_JAR_CENTRAL_DIRECTORY_BYTES",
                summary[1] - 1,
            ),
        )
        for limit_name, limit_value in cases:
            with (
                self.subTest(limit_name=limit_name),
                mock.patch.object(
                    verify,
                    limit_name,
                    limit_value,
                ),
                self.assertRaisesRegex(
                    verify.ContractError,
                    "exceeds omitted-JAR archive limits",
                ),
            ):
                verify._jar_entries(archive_payload, "bounded.jar")

    def test_maven_sbom_generation_accepts_rootless_trivy_graph(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            descriptor = self._descriptor(root)
            rootfs_sbom = self._sbom(root)
            rootfs_document = json.loads(
                rootfs_sbom.read_text(encoding="utf-8")
            )
            old_root_ref = rootfs_document["metadata"]["component"]["bom-ref"]
            rootfs_document["dependencies"] = [
                dependency
                for dependency in rootfs_document["dependencies"]
                if dependency["ref"] != old_root_ref
            ]
            rootfs_sbom.write_text(
                json.dumps(rootfs_document),
                encoding="utf-8",
            )
            generated = root / "generated-sbom.json"
            verify.generate_maven_sbom(
                descriptor,
                root,
                rootfs_sbom,
                generated,
            )
            document = json.loads(generated.read_text(encoding="utf-8"))
            root_ref = document["metadata"]["component"]["bom-ref"]
            component_refs = {
                component["bom-ref"] for component in document["components"]
            }
            root_dependencies = [
                dependency
                for dependency in document["dependencies"]
                if dependency["ref"] == root_ref
            ]
            self.assertEqual(1, len(root_dependencies))
            self.assertEqual(
                component_refs,
                set(root_dependencies[0]["dependsOn"]),
            )
            verify.verify_maven_scan(
                descriptor,
                generated,
                self._report(root),
            )
            rootfs_document["dependencies"].append(
                {"ref": "urn:test:unreviewed-root", "dependsOn": []}
            )
            rootfs_sbom.write_text(
                json.dumps(rootfs_document),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                verify.ContractError,
                "rootfs dependency references are not closed",
            ):
                verify.generate_maven_sbom(
                    descriptor,
                    root,
                    rootfs_sbom,
                    generated,
                )

    def test_manifest_closure_is_generated_after_rootfs_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            descriptor = self._descriptor(root)
            rootfs_sbom = self._sbom(root)
            rootfs_document = json.loads(
                rootfs_sbom.read_text(encoding="utf-8")
            )
            nested_path = (
                f"{self.JARS[0]}/META-INF/jars/embedded-3.0.jar"
            )
            nested_purl = "pkg:maven/org.example/embedded@3.0"
            nested_ref = "urn:test:embedded"
            rootfs_document["components"].append(
                {
                    "bom-ref": nested_ref,
                    "type": "library",
                    "name": "embedded",
                    "version": "3.0",
                    "purl": nested_purl,
                    "properties": [
                        {
                            "name": "aquasecurity:trivy:FilePath",
                            "value": nested_path,
                        }
                    ],
                }
            )
            rootfs_document["dependencies"][0]["dependsOn"].append(nested_ref)
            rootfs_document["dependencies"].append(
                {"ref": nested_ref, "dependsOn": []}
            )
            rootfs_sbom.write_text(
                json.dumps(rootfs_document),
                encoding="utf-8",
            )
            generated = root / "generated-sbom.json"
            verify.generate_maven_sbom(
                descriptor,
                root,
                rootfs_sbom,
                generated,
            )
            document = json.loads(generated.read_text(encoding="utf-8"))
            self.assertEqual(
                {
                    *(verify._maven_purl(path) for path in self.JARS),
                    nested_purl,
                },
                {component["purl"] for component in document["components"]},
            )
            self.assertIn(
                nested_path,
                {
                    prop["value"]
                    for component in document["components"]
                    for prop in component.get("properties", [])
                    if prop.get("name") == "aquasecurity:trivy:FilePath"
                },
            )
            root_dependency = next(
                dependency
                for dependency in document["dependencies"]
                if dependency["ref"]
                == document["metadata"]["component"]["bom-ref"]
            )
            self.assertIn(nested_ref, root_dependency["dependsOn"])
            with self.assertRaisesRegex(
                verify.ContractError,
                "MAVEN_SCAN_CLOSURE",
            ):
                verify.verify_maven_scan(
                    descriptor,
                    generated,
                    self._report(root),
                )
            verify.verify_maven_scan(
                descriptor,
                generated,
                self._report(
                    root,
                    extra_packages=((nested_path, nested_purl),),
                ),
            )
            rootfs_document["components"][-1]["properties"][0]["value"] = (
                "org/unreviewed/outer/1.0/outer-1.0.jar!/embedded.jar"
            )
            escaped = root / "escaped-rootfs.json"
            escaped.write_text(
                json.dumps(rootfs_document),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                verify.ContractError,
                "MAVEN_SBOM_ROOTFS",
            ):
                verify.generate_maven_sbom(
                    descriptor,
                    root,
                    escaped,
                    generated,
                )

    def test_maven_scan_requires_every_purl_path_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            descriptor = self._descriptor(root)
            sbom = self._sbom(root)
            document = json.loads(sbom.read_text(encoding="utf-8"))
            nested_path = f"{self.JARS[1]}!/embedded-alpha.jar"
            nested_ref = "urn:test:duplicate-purl-at-nested-path"
            duplicate_purl = verify._maven_purl(self.JARS[0])
            document["components"].append(
                {
                    "bom-ref": nested_ref,
                    "type": "library",
                    "name": "embedded-alpha",
                    "purl": duplicate_purl,
                    "properties": [
                        {
                            "name": "aquasecurity:trivy:FilePath",
                            "value": nested_path,
                        }
                    ],
                }
            )
            document["dependencies"][0]["dependsOn"].append(nested_ref)
            document["dependencies"].append(
                {"ref": nested_ref, "dependsOn": []}
            )
            sbom.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(
                verify.ContractError,
                "MAVEN_SCAN_CLOSURE",
            ):
                verify.verify_maven_scan(
                    descriptor,
                    sbom,
                    self._report(root),
                )
            verify.verify_maven_scan(
                descriptor,
                sbom,
                self._report(
                    root,
                    extra_packages=((nested_path, duplicate_purl),),
                ),
            )
            document["components"][-1]["properties"] = []
            sbom.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(
                verify.ContractError,
                "MAVEN_SBOM_CLOSURE",
            ):
                verify.verify_maven_scan(
                    descriptor,
                    sbom,
                    self._report(
                        root,
                        extra_packages=((nested_path, duplicate_purl),),
                    ),
                )

    def test_empty_maven_scan_and_sbom_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            descriptor = self._descriptor(root)
            report = root / "empty-report.json"
            report.write_text(
                json.dumps({"SchemaVersion": 2, "Results": []}),
                encoding="utf-8",
            )
            sbom = root / "empty-sbom.json"
            sbom.write_text(
                json.dumps(
                    {
                        "bomFormat": "CycloneDX",
                        "specVersion": "1.7",
                        "metadata": {},
                        "components": [],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                verify.ContractError,
                "MAVEN_SBOM",
            ):
                verify.verify_maven_scan(descriptor, sbom, self._report(root))
            with self.assertRaisesRegex(
                verify.ContractError,
                "MAVEN_SCAN_REPORT",
            ):
                verify.verify_maven_scan(descriptor, self._sbom(root), report)

    def test_binding_sets_exact_immutable_subject_on_every_document(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            descriptor = self._descriptor(root)
            maven_report = self._report(root)
            maven_sbom = self._sbom(root)
            bun_sbom = self._sbom(root)
            bun_document = json.loads(bun_sbom.read_text(encoding="utf-8"))
            bun_document["dependencies"] = [
                dependency
                for dependency in bun_document["dependencies"]
                if dependency["ref"] != "urn:test:maven-root"
            ]
            bun_sbom.write_text(json.dumps(bun_document), encoding="utf-8")
            bun_report = self._report(root)
            reference = (
                "ghcr.io/tommykammy/shirokuma-trino-maven-dependencies@"
                "sha256:" + "a" * 64
            )
            verify.bind_artifact_evidence(
                reference,
                descriptor,
                maven_sbom,
                maven_report,
                bun_sbom,
                bun_report,
            )
            digest = "sha256:" + "a" * 64
            for path in (maven_report, bun_report):
                document = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(reference, document["ArtifactName"])
                self.assertEqual(digest, document["Metadata"]["ImageID"])
                self.assertEqual(
                    [reference],
                    document["Metadata"]["RepoDigests"],
                )
            for path in (maven_sbom, bun_sbom):
                document = json.loads(path.read_text(encoding="utf-8"))
                component = document["metadata"]["component"]
                self.assertEqual(reference, component["bom-ref"])
                self.assertEqual(
                    [{"alg": "SHA-256", "content": "a" * 64}],
                    component["hashes"],
                )
                root_dependencies = [
                    dependency
                    for dependency in document["dependencies"]
                    if dependency["ref"] == reference
                ]
                self.assertEqual(1, len(root_dependencies))
                self.assertNotIn(
                    "urn:test:maven-root",
                    json.dumps(document),
                )
            document = json.loads(maven_sbom.read_text(encoding="utf-8"))
            root_dependency = next(
                dependency
                for dependency in document["dependencies"]
                if dependency["ref"] == reference
            )
            root_dependency["dependsOn"] = []
            maven_sbom.write_text(
                json.dumps(document),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                verify.ContractError,
                "components are not root-reachable",
            ):
                verify._verify_cyclonedx_subject(
                    maven_sbom,
                    reference,
                    "a" * 64,
                )

    def test_binding_requires_an_exact_trivy_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = self._report(Path(temporary))
            document = json.loads(report.read_text(encoding="utf-8"))
            document["SchemaVersion"] = 2.0
            report.write_text(json.dumps(document), encoding="utf-8")

            with self.assertRaisesRegex(verify.ContractError, "ARTIFACT_SCAN"):
                verify._bind_trivy_report(
                    report,
                    "ghcr.io/example/image@sha256:" + "a" * 64,
                    "sha256:" + "a" * 64,
                )


class ServerDistributionTests(unittest.TestCase):
    def _write_archive(
        self,
        path: Path,
        *,
        extra_plugin: str | None = None,
        extra_root: str | None = None,
        omit: str | None = None,
        required_as_directory: str | None = None,
        required_as_hardlink: str | None = None,
        hardlink_target: str | None = None,
        hardlink_target_as_directory: bool = False,
        unsafe_symlink: bool = False,
        archive_root: str = verify.EXPECTED_SERVER_DISTRIBUTION_ROOT,
        omit_core_marker: bool = False,
        nested_core_member: bool = False,
    ) -> None:
        if hardlink_target is None:
            hardlink_target = f"{archive_root}/lib/hardlink-target.jar"

        def archive_name(expected_name: str) -> str:
            _, separator, relative = expected_name.partition("/")
            if not separator:
                return archive_root
            return f"{archive_root}/{relative}"

        with tarfile.open(path, mode="w:gz") as archive:
            root = tarfile.TarInfo(archive_root)
            root.type = tarfile.DIRTYPE
            archive.addfile(root)
            core_marker = f"{archive_root}/trino-server-core-483"
            if not omit_core_marker:
                marker = tarfile.TarInfo(core_marker)
                marker.type = tarfile.DIRTYPE
                archive.addfile(marker)
            if nested_core_member:
                payload = b"nested"
                member = tarfile.TarInfo(f"{core_marker}/bin/launcher")
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
            if (
                required_as_hardlink is not None
                and hardlink_target != root.name
                and hardlink_target
                not in verify.EXPECTED_SERVER_DISTRIBUTION_FILES
            ):
                payload = b"hardlink target"
                member = tarfile.TarInfo(hardlink_target)
                if hardlink_target_as_directory:
                    member.type = tarfile.DIRTYPE
                    archive.addfile(member)
                else:
                    member.size = len(payload)
                    archive.addfile(member, io.BytesIO(payload))
            for expected_name in sorted(
                verify.EXPECTED_SERVER_DISTRIBUTION_FILES
            ):
                name = archive_name(expected_name)
                if name == omit:
                    continue
                if name == required_as_directory:
                    member = tarfile.TarInfo(name)
                    member.type = tarfile.DIRTYPE
                    archive.addfile(member)
                    continue
                if name == required_as_hardlink:
                    member = tarfile.TarInfo(name)
                    member.type = tarfile.LNKTYPE
                    member.linkname = hardlink_target
                    archive.addfile(member)
                    continue
                payload = name.encode("utf-8")
                member = tarfile.TarInfo(name)
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
            if extra_plugin is not None:
                payload = b"extra"
                member = tarfile.TarInfo(
                    f"{archive_root}/plugin/{extra_plugin}/extra.jar"
                )
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
            if extra_root is not None:
                payload = b"extra"
                member = tarfile.TarInfo(f"{archive_root}/{extra_root}")
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
            if unsafe_symlink:
                member = tarfile.TarInfo(f"{archive_root}/lib/escape")
                member.type = tarfile.SYMTYPE
                member.linkname = "../../outside"
                archive.addfile(member)

    def test_exact_iceberg_only_distribution_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            required = next(iter(verify.EXPECTED_SERVER_DISTRIBUTION_FILES))
            for name, options in (
                ("regular", {}),
                ("hardlink", {"required_as_hardlink": required}),
            ):
                with self.subTest(name=name):
                    path = Path(temporary) / f"{name}.tar.gz"
                    self._write_archive(path, **options)
                    verify.verify_server_distribution(path)

    def test_distribution_rejects_plugin_expansion_missing_files_and_links(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cases = (
                ("extra", {"extra_plugin": "hive"}, "plugin set differs"),
                (
                    "missing",
                    {"omit": next(iter(verify.EXPECTED_SERVER_DISTRIBUTION_FILES))},
                    "required members are missing",
                ),
                (
                    "directory",
                    {
                        "required_as_directory": next(
                            iter(verify.EXPECTED_SERVER_DISTRIBUTION_FILES)
                        )
                    },
                    "required members are not regular files",
                ),
                (
                    "hardlink-to-directory",
                    {
                        "required_as_hardlink": next(
                            iter(verify.EXPECTED_SERVER_DISTRIBUTION_FILES)
                        ),
                        "hardlink_target": (
                            f"{verify.EXPECTED_SERVER_DISTRIBUTION_ROOT}/"
                            "lib/target"
                        ),
                        "hardlink_target_as_directory": True,
                    },
                    "hard link target is not a regular file",
                ),
                (
                    "configuration-plugin",
                    {
                        "extra_root": (
                            "secrets-plugin/keystore-secrets-plugin/plugin.jar"
                        )
                    },
                    "distribution root differs",
                ),
                (
                    "symlink",
                    {"unsafe_symlink": True},
                    "unsafe or unexpected member",
                ),
                (
                    "missing-core-marker",
                    {"omit_core_marker": True},
                    "required empty directories are missing",
                ),
                (
                    "nested-core-payload",
                    {"nested_core_member": True},
                    "distribution root differs",
                ),
            )
            for name, options, error in cases:
                with self.subTest(name=name):
                    path = root / f"{name}.tar.gz"
                    self._write_archive(path, **options)
                    with self.assertRaisesRegex(verify.ContractError, error):
                        verify.verify_server_distribution(path)

    def test_distribution_rejects_a_different_archive_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "wrong-root.tar.gz"
            self._write_archive(path, archive_root="trino-server-core-483")
            with self.assertRaisesRegex(
                verify.ContractError,
                "unsafe or unexpected member",
            ):
                verify.verify_server_distribution(path)


class PublisherContractTests(unittest.TestCase):
    OWNER_HEAD_REF = "agent/issue-63-sequence-6-activation"
    EMPTY_REVIEW_THREAD_SNAPSHOT_SHA256 = verify._review_thread_snapshot_sha256([])

    @staticmethod
    def _owner_workflow_payload(
        *,
        final_head: str = "c" * 40,
        pull_request: int = 153,
        head_ref: str = OWNER_HEAD_REF,
        pull_requests: list[dict[str, int]] | None = None,
    ) -> dict[str, object]:
        runs = []
        for run_id, path in enumerate(
            verify.EXPECTED_OWNER_ONLY_APPROVAL_EXCEPTION["final_head_ci"][
                "workflow_paths"
            ],
            start=1000,
        ):
            runs.append(
                {
                    "id": run_id,
                    "path": path,
                    "head_sha": final_head,
                    "head_branch": head_ref,
                    "event": "pull_request",
                    "status": "completed",
                    "conclusion": "success",
                "created_at": "2026-08-18T15:35:00Z",
                "updated_at": "2026-08-18T15:40:00Z",
                    "repository": {"full_name": "TommyKammy/Shirokuma"},
                    "head_repository": {
                        "full_name": "TommyKammy/Shirokuma"
                    },
                    "pull_requests": copy.deepcopy(
                        [] if pull_requests is None else pull_requests
                    ),
                }
            )
        return {"total_count": len(runs), "workflow_runs": runs}

    @staticmethod
    def _owner_workflow_run(
        run_id: int,
        path: str,
        *,
        final_head: str = "c" * 40,
        pull_request: int = 153,
        head_ref: str = OWNER_HEAD_REF,
        pull_requests: list[dict[str, int]] | None = None,
    ) -> dict[str, object]:
        return {
            "id": run_id,
            "path": path,
            "head_sha": final_head,
            "head_branch": head_ref,
            "event": "pull_request",
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-08-18T15:35:00Z",
            "updated_at": "2026-08-18T15:40:00Z",
            "repository": {"full_name": "TommyKammy/Shirokuma"},
            "head_repository": {"full_name": "TommyKammy/Shirokuma"},
            "pull_requests": copy.deepcopy(
                [] if pull_requests is None else pull_requests
            ),
        }

    @staticmethod
    def _owner_pull(
        *,
        final_head: str = "c" * 40,
        merge_commit: str = "b" * 40,
        pull_request: int = 153,
        head_ref: str = OWNER_HEAD_REF,
        base_sha: str = verify.EXPECTED_PUBLICATION_ATTEMPT["before_sha"],
    ) -> dict[str, object]:
        return {
            "number": pull_request,
            "state": "closed",
            "merged_at": "2026-08-18T16:00:00Z",
            "merge_commit_sha": merge_commit,
            "base": {
                "ref": "main",
                "sha": base_sha,
                "repo": {"full_name": "TommyKammy/Shirokuma"},
            },
            "head": {
                "ref": head_ref,
                "sha": final_head,
                "repo": {"full_name": "TommyKammy/Shirokuma"},
            },
        }

    @staticmethod
    def _active_owner_contract_fixture(
    ) -> tuple[dict[str, object], dict[str, object]]:
        contract = verify._load_json(ROOT / verify.CONTRACT_PATH)
        active_exception = copy.deepcopy(
            verify.EXPECTED_OWNER_ONLY_APPROVAL_EXCEPTION
        )
        active_exception["status"] = "active"
        active_exception.pop("consumed_run", None)
        publication = contract["publication"]
        publication["permitted"] = True
        publication["owner_only_approval_exception"] = copy.deepcopy(
            active_exception
        )
        reauthorization = publication["reauthorization"]
        reauthorization["status"] = "active"
        reauthorization[
            "publication_authorized_after_required_approval"
        ] = True
        reauthorization["next_sequence_authorized"] = True
        return contract, active_exception

    def _active_owner_contract(self) -> dict[str, object]:
        contract, active_exception = self._active_owner_contract_fixture()
        patcher = mock.patch.object(
            verify,
            "EXPECTED_OWNER_ONLY_APPROVAL_EXCEPTION",
            active_exception,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return contract

    @staticmethod
    def _owner_review_thread_payload(
        nodes: list[dict[str, object]] | None = None,
        *,
        repository: str = "TommyKammy/Shirokuma",
        pull_request: int = 153,
        final_head: str = "c" * 40,
        has_next_page: bool = False,
        end_cursor: str | None = None,
        total_count: int | None = None,
    ) -> dict[str, object]:
        page_nodes = [] if nodes is None else nodes
        return {
            "data": {
                "repository": {
                    "nameWithOwner": repository,
                    "pullRequest": {
                        "number": pull_request,
                        "headRefOid": final_head,
                        "reviewThreads": {
                            "totalCount": (
                                len(page_nodes)
                                if total_count is None
                                else total_count
                            ),
                            "nodes": page_nodes,
                            "pageInfo": {
                                "hasNextPage": has_next_page,
                                "endCursor": end_cursor,
                            },
                        },
                    }
                }
            }
        }

    def test_repository_contract_and_workflow_are_closed(self) -> None:
        verify.audit(ROOT)

    def test_maven_policy_directory_inventory_is_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy_directory = root / verify.JVM_CONFIG_PATH.parent
            policy_directory.mkdir(parents=True)
            jvm_config = root / verify.JVM_CONFIG_PATH
            jvm_config.write_text("-Xmx8192m\n", encoding="utf-8")
            verify._validate_maven_policy_inventory(root)

            for relative in (
                "maven.config",
                "extensions.xml",
                ".unreviewed",
                "nested",
            ):
                extra = policy_directory / relative
                if relative == "nested":
                    extra.mkdir()
                else:
                    extra.write_text("unreviewed\n", encoding="utf-8")
                with self.subTest(relative=relative):
                    with self.assertRaisesRegex(
                        verify.ContractError,
                        "must contain only jvm.config",
                    ):
                        verify._validate_maven_policy_inventory(root)
                if extra.is_dir():
                    extra.rmdir()
                else:
                    extra.unlink()

            target = root / "jvm.config"
            target.write_text("-Xmx8192m\n", encoding="utf-8")
            jvm_config.unlink()
            jvm_config.symlink_to(target)
            with self.assertRaisesRegex(
                verify.ContractError,
                "must be one regular",
            ):
                verify._validate_maven_policy_inventory(root)

    def test_repository_audit_rejects_json_number_boolean_aliases(self) -> None:
        contract_path = ROOT / verify.CONTRACT_PATH
        original_load_json = verify._load_json
        contract = original_load_json(contract_path)
        contract["source"]["source_overlay"]["automatic_renewal"] = 0

        def load_json(path: Path) -> dict[str, object]:
            if path == contract_path:
                return contract
            return original_load_json(path)

        with mock.patch.object(verify, "_load_json", side_effect=load_json):
            with self.assertRaisesRegex(verify.ContractError, "SOURCE_OVERLAY"):
                verify.audit(ROOT)

    def test_authorization_is_current_at_each_declared_use_point(self) -> None:
        verify.authorize_use(
            ROOT,
            validation_point="before_source_fetch",
            at=dt.datetime(
                2026,
                9,
                17,
                2,
                15,
                57,
                tzinfo=dt.timezone.utc,
            ),
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "AUTHORIZATION_EXPIRED",
        ):
            verify.authorize_use(
                ROOT,
                validation_point="before_dependency_resolution",
                at=dt.datetime(
                    2026,
                    9,
                    17,
                    2,
                    15,
                    58,
                    tzinfo=dt.timezone.utc,
                ),
            )

        workflow = (
            ROOT
            / ".github/workflows/trino-maven-remediation-feasibility.yml"
        ).read_text(encoding="utf-8")
        self.assertLess(
            workflow.index("--validation-point before_source_fetch"),
            workflow.index("git -C \"${source_dir}\" fetch --depth=1 origin"),
        )
        online = workflow.split(
            "- name: Resolve the selected plugin closure online",
            1,
        )[1].split("- name: Replay the selected plugin closure", 1)[0]
        offline = workflow.split(
            "- name: Replay the selected plugin closure",
            1,
        )[1].split("- name: Finalize and audit", 1)[0]
        for phase in (online, offline):
            self.assertLess(
                phase.index("--authorization-root ."),
                phase.index("docker run --rm"),
            )
        review = workflow.split("- name: Finalize and audit", 1)[1]
        self.assertLess(
            review.index("--validation-point before_evidence_review"),
            review.index("finalize-record"),
        )

    def test_consumed_sequence_6_publication_attempt_is_not_authorized(self) -> None:
        instant = dt.datetime(2026, 8, 18, 6, 0, tzinfo=dt.timezone.utc)
        environment = {
            "GITHUB_EVENT_NAME": verify.EXPECTED_PUBLICATION_ATTEMPT["event_name"],
            "GITHUB_REF": verify.EXPECTED_PUBLICATION_ATTEMPT["ref"],
            "GITHUB_EVENT_BEFORE": verify.EXPECTED_PUBLICATION_ATTEMPT[
                "before_sha"
            ],
            "GITHUB_RUN_ATTEMPT": verify.EXPECTED_PUBLICATION_ATTEMPT[
                "run_attempt"
            ],
        }
        with (
            mock.patch.dict(os.environ, environment, clear=False),
            self.assertRaisesRegex(
                verify.ContractError,
                "no publication attempt is authorized",
            ),
        ):
            verify.authorize_use(
                ROOT,
                validation_point="before_dependency_publication",
                at=instant,
            )

        contract = verify._load_json(ROOT / verify.CONTRACT_PATH)
        with self.assertRaisesRegex(
            verify.ContractError,
            "no publication attempt is authorized",
        ):
            verify._validate_publication_attempt(contract, environment=environment)

        for key, replacement in (
            ("GITHUB_EVENT_NAME", "workflow_dispatch"),
            ("GITHUB_REF", "refs/heads/other"),
            ("GITHUB_EVENT_BEFORE", "0" * 40),
            ("GITHUB_RUN_ATTEMPT", "2"),
        ):
            with self.subTest(key=key):
                altered = {**environment, key: replacement}
                with (
                    mock.patch.dict(os.environ, altered, clear=False),
                    self.assertRaisesRegex(
                        verify.ContractError,
                        "PUBLICATION_ATTEMPT",
                    ),
                ):
                    verify._validate_publication_attempt(
                        contract,
                        environment=altered,
                    )

        contract_path = ROOT / verify.CONTRACT_PATH
        original_load_json = verify._load_json
        contract = original_load_json(contract_path)
        contract["publication"]["authorized_attempt"]["run_attempt"] = "2"

        def load_json(path: Path) -> dict[str, object]:
            if path == contract_path:
                return contract
            return original_load_json(path)

        with (
            mock.patch.object(verify, "_load_json", side_effect=load_json),
            mock.patch.dict(os.environ, environment, clear=False),
            self.assertRaisesRegex(verify.ContractError, "PUBLICATION_ATTEMPT"),
        ):
            verify._validate_publication_attempt(
                contract,
                environment=environment,
            )

    def test_third_publication_reauthorization_record_is_exact(self) -> None:
        contract = verify._load_json(ROOT / verify.CONTRACT_PATH)
        self.assertEqual(
            verify.EXPECTED_PUBLICATION_REAUTHORIZATION,
            contract["publication"]["reauthorization"],
        )

        altered = copy.deepcopy(contract)
        altered["publication"]["reauthorization"]["approval_record"] = (
            "https://github.com/TommyKammy/Shirokuma/issues/63"
            "#issuecomment-5210182460"
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "PUBLICATION_ATTEMPT",
        ):
            verify._validate_publication_attempt(altered)

    def test_admission_owner_exception_must_match_exactly(self) -> None:
        admission_path = ROOT / verify.ADMISSION_PATH
        original_load_json = verify._load_json
        admission = original_load_json(admission_path)
        admission["owner_only_approval_exception"]["pull_request"] = 145

        def load_json(path: Path) -> dict[str, object]:
            if path == admission_path:
                return admission
            return original_load_json(path)

        with (
            mock.patch.object(verify, "_load_json", side_effect=load_json),
            self.assertRaisesRegex(verify.ContractError, "ADMISSION"),
        ):
            verify.audit(ROOT)

    def test_main_publisher_revalidates_and_prunes_each_build(self) -> None:
        workflow = (ROOT / verify.WORKFLOW_PATH).read_text(encoding="utf-8")
        self.assertEqual(
            4,
            workflow.count(
                "python3 scripts/verify_trino_maven_feasibility.py verify-candidate"
            ),
        )
        self.assertEqual(
            2,
            workflow.count(
                "python3 scripts/verify_trino_maven_feasibility.py "
                "prune-vulnerable-inputs"
            ),
        )
        for start, end in (
            (
                "- name: Resolve and package the first closed Maven repository",
                "- name: Independently reconstruct the closed Maven repository",
            ),
            (
                "- name: Independently reconstruct the closed Maven repository",
                "- name: Prove two fresh network-none offline source builds",
            ),
        ):
            phase = workflow.split(start, 1)[1].split(end, 1)[0]
            trino_build = phase.rindex("-am clean install -DskipTests")
            revalidate = phase.index("verify-candidate", trino_build)
            prune = phase.index("prune-vulnerable-inputs", revalidate)
            seal = phase.index("seal-artifact", prune)
            self.assertLess(trino_build, revalidate)
            self.assertLess(revalidate, prune)
            self.assertLess(prune, seal)

        offline = workflow.split(
            "- name: Prove two fresh network-none offline source builds",
            1,
        )[1].split("- name: Verify both closed dependency inventories", 1)[0]
        self.assertLess(
            offline.index("-am clean package -DskipTests"),
            offline.index("verify-candidate"),
        )
        self.assertLess(
            offline.index("verify-candidate"),
            offline.index('output="${offline_source}/core/trino-server/target/'),
        )

    def test_publish_job_rechecks_attempt_and_independent_review(self) -> None:
        workflow = (ROOT / verify.WORKFLOW_PATH).read_text(encoding="utf-8")
        self.assertEqual(
            4,
            workflow.count("--validation-point before_dependency_publication"),
        )
        self.assertEqual(2, workflow.count("verify-independent-review"))
        self.assertEqual(1, workflow.count("verify-independent-review --root ."))
        self.assertEqual(1, workflow.count("final_review_gate=("))
        self.assertEqual(2, workflow.count('"${final_review_gate[@]}"'))
        self.assertEqual(2, workflow.count("GITHUB_TOKEN: ${{ github.token }}"))
        for path in (
            verify.FEASIBILITY_VERIFIER_PATH,
            verify.FEASIBILITY_TEST_PATH,
        ):
            self.assertEqual(2, workflow.splitlines().count(f"      - {path}"))

        publish = workflow.split("  publish:", 1)[1]
        attempt = publish.index("--validation-point before_dependency_publication")
        review = publish.index("verify-independent-review --root .")
        final_review = publish.index("final_review_gate=(")
        first_final_review_run = publish.index('"${final_review_gate[@]}"')
        last_final_review_run = publish.rindex('"${final_review_gate[@]}"')
        push_step_start = publish.index(
            "- name: Publish the immutable run-scoped OCI artifact"
        )
        push_step_end = publish.index(
            "- name: Install pinned Cosign after publication"
        )
        first_final_authorization = publish.index(
            "--validation-point before_dependency_publication",
            first_final_review_run,
        )
        auth = publish.index("oras login ghcr.io")
        last_authorize = publish.rindex(
            "python3 scripts/verify_trino_dependency_publisher.py authorize --root .",
            push_step_start,
            push_step_end,
        )
        last_final_authorization = publish.rindex(
            "--validation-point before_dependency_publication",
            push_step_start,
            push_step_end,
        )
        push = publish.index("oras push")
        self.assertLess(attempt, review)
        self.assertLess(review, final_review)
        self.assertLess(final_review, first_final_review_run)
        self.assertLess(first_final_review_run, first_final_authorization)
        self.assertLess(first_final_authorization, auth)
        self.assertLess(auth, last_final_review_run)
        self.assertLess(last_final_review_run, last_authorize)
        self.assertLess(last_authorize, last_final_authorization)
        self.assertLess(last_final_authorization, push)

    def test_independent_review_requires_non_risk_owner_human_approval(
        self,
    ) -> None:
        contract = self._active_owner_contract()
        commit = "a" * 40
        final_head = "c" * 40
        pulls = [
            {
                "number": 153,
                "state": "closed",
                "merged_at": "2026-08-07T03:00:00Z",
                "merge_commit_sha": commit,
                "base": {
                    "ref": "main",
                    "sha": verify.EXPECTED_PUBLICATION_ATTEMPT["before_sha"],
                },
                "head": {"sha": final_head},
            }
        ]
        owner_review = {
            "id": 1,
            "state": "APPROVED",
            "user": {"login": "TommyKammy", "type": "User"},
            "commit_id": final_head,
            "submitted_at": "2026-08-07T02:59:59Z",
        }
        independent_review = {
            "id": 2,
            "state": "APPROVED",
            "user": {"login": "IndependentHuman", "type": "User"},
            "commit_id": final_head,
            "submitted_at": "2026-08-07T02:59:59Z",
        }
        self.assertEqual(
            "IndependentHuman",
            verify._select_independent_review(
                contract,
                pulls,
                [owner_review, independent_review],
                commit=commit,
            )["reviewer"],
        )
        self.assertEqual(
            "independent_review",
            verify._select_independent_review(
                contract,
                pulls,
                [owner_review, independent_review],
                commit=commit,
            )["approval_mode"],
        )
        with self.assertRaisesRegex(verify.ContractError, "INDEPENDENT_REVIEW"):
            verify._select_independent_review(
                contract,
                pulls,
                [owner_review],
                commit=commit,
            )

        wrong_activation_pull = copy.deepcopy(pulls)
        wrong_activation_pull[0]["number"] = 154
        with self.assertRaisesRegex(
            verify.ContractError,
            "activation pull request differs",
        ):
            verify._select_independent_review(
                contract,
                wrong_activation_pull,
                [independent_review],
                commit=commit,
            )

        for submitted_at in (
            None,
            "2026-08-07T03:00:00Z",
            "2026-08-07T03:00:01Z",
            "not-a-timestamp",
        ):
            candidate = dict(independent_review)
            if submitted_at is None:
                candidate.pop("submitted_at")
            else:
                candidate["submitted_at"] = submitted_at
            with (
                self.subTest(submitted_at=submitted_at),
                self.assertRaises(verify.ContractError),
            ):
                verify._select_independent_review(
                    contract,
                    pulls,
                    [candidate],
                    commit=commit,
                )

        changed_request = {
            "id": 3,
            "state": "CHANGES_REQUESTED",
            "user": {"login": "IndependentHuman", "type": "User"},
            "commit_id": final_head,
        }
        with self.assertRaisesRegex(verify.ContractError, "INDEPENDENT_REVIEW"):
            verify._select_independent_review(
                contract,
                pulls,
                [independent_review, changed_request],
                commit=commit,
            )

        stale_approval = {
            **independent_review,
            "id": 4,
            "commit_id": "d" * 40,
        }
        with self.assertRaisesRegex(verify.ContractError, "INDEPENDENT_REVIEW"):
            verify._select_independent_review(
                contract,
                pulls,
                [stale_approval],
                commit=commit,
            )

    def test_owner_final_head_attestation_is_accepted_for_pr_153(self) -> None:
        contract = self._active_owner_contract()
        commit = "b" * 40
        final_head = "c" * 40
        pull = {
            "number": 153,
            "state": "closed",
            "merged_at": "2026-08-18T16:00:00Z",
            "merge_commit_sha": commit,
            "base": {
                "ref": "main",
                "sha": verify.EXPECTED_PUBLICATION_ATTEMPT["before_sha"],
            },
            "head": {"sha": final_head},
        }
        comment = {
            "id": 5264800000,
            "body": (
                "Owner final-head attestation for PR #153\n\n"
                "Decision: APPROVED\n"
                f"Final head: {final_head}\n"
                "Review-thread snapshot SHA-256: "
                f"{self.EMPTY_REVIEW_THREAD_SNAPSHOT_SHA256}\n"
                "Exception: https://github.com/TommyKammy/Shirokuma/issues/63"
                "#issuecomment-5324238100"
            ),
            "user": {"login": "TommyKammy", "type": "User"},
            "author_association": "OWNER",
            "created_at": "2026-08-18T15:50:00Z",
            "updated_at": "2026-08-18T15:50:00Z",
        }
        hostile_marker_comment = {
            **comment,
            "id": comment["id"] + 1,
            "user": {"login": "UntrustedCommenter", "type": "User"},
            "author_association": "NONE",
        }

        receipt = verify._select_independent_review(
            contract,
            [pull],
            [],
            commit=commit,
            comments=[comment, hostile_marker_comment],
        )
        self.assertEqual("owner_final_head_attestation", receipt["approval_mode"])
        self.assertEqual(153, receipt["pull_request"])
        self.assertEqual(comment["id"], receipt["comment_id"])
        self.assertEqual("TommyKammy", receipt["owner"])
        self.assertEqual(final_head, receipt["attested_head"])
        self.assertEqual(
            verify.EXPECTED_PUBLICATION_ATTEMPT["before_sha"],
            receipt["base_sha"],
        )
        self.assertEqual("2026-08-18T15:50:00Z", receipt["attested_at"])

    def test_owner_final_head_attestation_identity_and_scope_fail_closed(
        self,
    ) -> None:
        contract = self._active_owner_contract()
        commit = "b" * 40
        final_head = "c" * 40
        pull = {
            "number": 153,
            "state": "closed",
            "merged_at": "2026-08-18T16:00:00Z",
            "merge_commit_sha": commit,
            "base": {
                "ref": "main",
                "sha": verify.EXPECTED_PUBLICATION_ATTEMPT["before_sha"],
            },
            "head": {"sha": final_head},
        }
        comment = {
            "id": 5264800000,
            "body": (
                "Owner final-head attestation for PR #153\n\n"
                "Decision: APPROVED\n"
                f"Final head: {final_head}\n"
                "Review-thread snapshot SHA-256: "
                f"{self.EMPTY_REVIEW_THREAD_SNAPSHOT_SHA256}\n"
                "Exception: https://github.com/TommyKammy/Shirokuma/issues/63"
                "#issuecomment-5324238100"
            ),
            "user": {"login": "TommyKammy", "type": "User"},
            "author_association": "OWNER",
            "created_at": "2026-08-18T15:50:00Z",
            "updated_at": "2026-08-18T15:50:00Z",
        }

        cases: tuple[tuple[str, dict[str, object], dict[str, object]], ...] = (
            (
                "wrong-login",
                {},
                {"user": {"login": "OtherOwner", "type": "User"}},
            ),
            (
                "bot",
                {},
                {"user": {"login": "TommyKammy", "type": "Bot"}},
            ),
            (
                "wrong-association",
                {},
                {"author_association": "MEMBER"},
            ),
            (
                "wrong-pr",
                {"number": 144},
                {},
            ),
            (
                "wrong-base-sha",
                {"base": {"ref": "main", "sha": "d" * 40}},
                {},
            ),
            (
                "malformed-base-sha",
                {"base": {"ref": "main", "sha": "E" * 40}},
                {},
            ),
            (
                "wrong-head",
                {},
                {
                    "body": comment["body"].replace(
                        final_head,
                        "d" * 40,
                    )
                },
            ),
            (
                "wrong-body",
                {},
                {
                    "body": comment["body"].replace(
                        "#issuecomment-5324238100",
                        "#issuecomment-5264706436",
                    )
                },
            ),
            (
                "post-merge",
                {},
                {
                    "created_at": "2026-08-18T16:00:01Z",
                    "updated_at": "2026-08-18T16:00:01Z",
                },
            ),
        )
        for name, pull_update, comment_update in cases:
            with self.subTest(name=name):
                altered_pull = {**pull, **pull_update}
                altered_comment = {**comment, **comment_update}
                with self.assertRaisesRegex(
                    verify.ContractError,
                    "INDEPENDENT_REVIEW",
                ):
                    verify._select_independent_review(
                        contract,
                        [altered_pull],
                        [],
                        commit=commit,
                        comments=[altered_comment],
                    )

        altered_contract = copy.deepcopy(contract)
        altered_contract["publication"]["owner_only_approval_exception"][
            "pull_request"
        ] = 144
        with self.assertRaisesRegex(verify.ContractError, "INDEPENDENT_REVIEW"):
            verify._select_independent_review(
                altered_contract,
                [pull],
                [],
                commit=commit,
                comments=[comment],
            )

    def test_active_owner_exception_rejects_consumed_run_state(self) -> None:
        contract, active_exception = self._active_owner_contract_fixture()
        consumed_contract = copy.deepcopy(contract)
        consumed_exception = consumed_contract["publication"][
            "owner_only_approval_exception"
        ]
        consumed_exception["consumed_run"] = {
            "run_id": "31616764771",
            "run_attempt": "1",
            "source_sha": "49a86522d6e6c69f4a552220b30fa510d3a5edd2",
            "result": "failed_closed_before_registry_authentication",
            "rerun_permitted": False,
        }
        with mock.patch.object(
            verify,
            "EXPECTED_OWNER_ONLY_APPROVAL_EXCEPTION",
            active_exception,
        ):
            with self.assertRaisesRegex(
                verify.ContractError,
                "consumed owner approval exception cannot be reused",
            ):
                verify._active_owner_exception(consumed_contract)

    def test_owner_final_head_decisions_are_ordered_by_updated_at(self) -> None:
        contract = self._active_owner_contract()
        commit = "b" * 40
        final_head = "c" * 40
        pull = {
            "number": 153,
            "state": "closed",
            "merged_at": "2026-08-18T16:00:00Z",
            "merge_commit_sha": commit,
            "base": {
                "ref": "main",
                "sha": verify.EXPECTED_PUBLICATION_ATTEMPT["before_sha"],
            },
            "head": {"sha": final_head},
        }

        def decision_comment(
            comment_id: int,
            decision: str,
            *,
            created_at: str,
            updated_at: str,
        ) -> dict[str, object]:
            return {
                "id": comment_id,
                "body": (
                    "Owner final-head attestation for PR #153\n\n"
                    f"Decision: {decision}\n"
                    f"Final head: {final_head}\n"
                    "Review-thread snapshot SHA-256: "
                    f"{self.EMPTY_REVIEW_THREAD_SNAPSHOT_SHA256}\n"
                    "Exception: https://github.com/TommyKammy/Shirokuma/"
                    "issues/63#issuecomment-5324238100"
                ),
                "user": {"login": "TommyKammy", "type": "User"},
                "author_association": "OWNER",
                "created_at": created_at,
                "updated_at": updated_at,
            }

        approval = decision_comment(
            5264800001,
            "APPROVED",
            created_at="2026-08-18T15:40:00Z",
            updated_at="2026-08-18T15:40:00Z",
        )
        older_id_edited_approval = decision_comment(
            5264800000,
            "APPROVED",
            created_at="2026-08-18T15:37:00Z",
            updated_at="2026-08-18T15:50:00Z",
        )
        receipt = verify._select_independent_review(
            contract,
            [pull],
            [],
            commit=commit,
            comments=[approval, older_id_edited_approval],
        )
        self.assertEqual(5264800000, receipt["comment_id"])
        self.assertEqual("2026-08-18T15:50:00Z", receipt["attested_at"])

        older_id_edited_revocation = {
            **older_id_edited_approval,
            "body": older_id_edited_approval["body"].replace(
                "Decision: APPROVED",
                "Decision: REVOKED",
            ),
        }
        with self.assertRaisesRegex(
            verify.ContractError,
            "latest owner attestation is not a pre-merge approval",
        ):
            verify._select_independent_review(
                contract,
                [pull],
                [],
                commit=commit,
                comments=[approval, older_id_edited_revocation],
            )

        tied_revocation = decision_comment(
            5264800002,
            "REVOKED",
            created_at="2026-08-18T15:45:00Z",
            updated_at="2026-08-18T15:50:00Z",
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "latest owner attestation is ambiguous",
        ):
            verify._select_independent_review(
                contract,
                [pull],
                [],
                commit=commit,
                comments=[older_id_edited_approval, tied_revocation],
            )

    def test_pending_repair_proves_three_stable_exact_pull_bindings(self) -> None:
        final_head = "c" * 40
        merge_commit = "b" * 40
        pull = self._owner_pull(
            final_head=final_head,
            merge_commit=merge_commit,
        )

        class Response:
            status = 200

            def __init__(self, payload: object) -> None:
                self.payload = json.dumps(payload).encode()

            def __enter__(self) -> Response:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, limit: int) -> bytes:
                return self.payload[:limit]

        with mock.patch.object(
            verify,
            "urlopen",
            side_effect=[Response([pull]) for _ in range(6)],
        ) as request:
            receipt = verify._github_exact_pull_binding(
                verify.EXPECTED_PENDING_REVIEW_REPAIR["pull_request_binding"],
                pull_request=153,
                merge_commit=merge_commit,
                final_head=final_head,
                expected_base_sha=verify.EXPECTED_PUBLICATION_ATTEMPT[
                    "before_sha"
                ],
                token="ephemeral-token",
            )

        self.assertEqual(
            {
                "pull_request": 153,
                "merge_commit": merge_commit,
                "final_head": final_head,
                "base_sha": verify.EXPECTED_PUBLICATION_ATTEMPT["before_sha"],
                "head_ref": self.OWNER_HEAD_REF,
                "apis": [
                    "rest_merge_commit_associated_pulls",
                    "rest_final_head_associated_pulls",
                    "rest_head_filtered_pulls",
                ],
            },
            receipt,
        )
        self.assertEqual(6, request.call_count)
        urls = [call.args[0].full_url for call in request.call_args_list]
        self.assertEqual(urls[0], urls[1])
        self.assertIn(f"/commits/{merge_commit}/pulls?", urls[0])
        self.assertEqual(urls[2], urls[3])
        self.assertIn(f"/commits/{final_head}/pulls?", urls[2])
        self.assertEqual(urls[4], urls[5])
        self.assertIn("/pulls?state=all", urls[4])
        self.assertIn(
            "head=TommyKammy%3Aagent%2Fissue-63-sequence-6-activation",
            urls[4],
        )
        for url in urls:
            self.assertIn("per_page=100", url)
            self.assertIn("page=1", url)

    def test_pending_pull_binding_identity_fails_closed(self) -> None:
        final_head = "c" * 40
        merge_commit = "b" * 40
        original = self._owner_pull(
            final_head=final_head,
            merge_commit=merge_commit,
        )

        class Response:
            status = 200

            def __init__(self, payload: object) -> None:
                self.payload = json.dumps(payload).encode()

            def __enter__(self) -> Response:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, limit: int) -> bytes:
                return self.payload[:limit]

        cases = (
            ("wrong-pr", ("number",), 149),
            ("open", ("state",), "open"),
            ("wrong-merge", ("merge_commit_sha",), "d" * 40),
            ("wrong-base", ("base", "ref"), "release"),
            ("wrong-base-sha", ("base", "sha"), "d" * 40),
            ("malformed-base-sha", ("base", "sha"), "E" * 40),
            ("wrong-base-repo", ("base", "repo", "full_name"), "Other/Repo"),
            ("wrong-head", ("head", "sha"), "d" * 40),
            ("wrong-head-repo", ("head", "repo", "full_name"), "Other/Repo"),
        )
        for name, path, value in cases:
            with self.subTest(case=name):
                altered = copy.deepcopy(original)
                target = altered
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                with (
                    mock.patch.object(
                        verify,
                        "urlopen",
                        side_effect=[Response([altered]) for _ in range(6)],
                    ),
                    self.assertRaisesRegex(
                        verify.ContractError,
                        "INDEPENDENT_REVIEW",
                    ),
                ):
                    verify._github_exact_pull_binding(
                        verify.EXPECTED_PENDING_REVIEW_REPAIR[
                            "pull_request_binding"
                        ],
                        pull_request=153,
                        merge_commit=merge_commit,
                        final_head=final_head,
                        expected_base_sha=verify.EXPECTED_PUBLICATION_ATTEMPT[
                            "before_sha"
                        ],
                        token="ephemeral-token",
                    )

        with (
            mock.patch.object(
                verify,
                "urlopen",
                side_effect=[Response([original, copy.deepcopy(original)])] * 2,
            ),
            self.assertRaisesRegex(verify.ContractError, "INDEPENDENT_REVIEW"),
        ):
            verify._github_pull_binding_pages(
                verify.EXPECTED_PENDING_REVIEW_REPAIR["pull_request_binding"],
                query="merge_commit",
                commit=merge_commit,
                token="ephemeral-token",
            )

        changed = copy.deepcopy(original)
        changed["head"]["ref"] = "agent/changed"
        with (
            mock.patch.object(
                verify,
                "urlopen",
                side_effect=[Response([original]), Response([changed])],
            ),
            self.assertRaisesRegex(
                verify.ContractError,
                "pull-request binding snapshot is unstable",
            ),
        ):
            verify._github_pull_binding_pages(
                verify.EXPECTED_PENDING_REVIEW_REPAIR["pull_request_binding"],
                query="merge_commit",
                commit=merge_commit,
                token="ephemeral-token",
            )

    def test_owner_final_head_ci_requires_unique_successful_current_runs(
        self,
    ) -> None:
        exception = verify.EXPECTED_OWNER_ONLY_APPROVAL_EXCEPTION
        final_head = "c" * 40
        attested_at = dt.datetime(
            2026,
            8,
            18,
            15,
            50,
            tzinfo=dt.timezone.utc,
        )
        payload = self._owner_workflow_payload(final_head=final_head)
        receipt = verify._validate_owner_final_head_ci(
            exception,
            payload,
            association_policy=verify.EXPECTED_PENDING_REVIEW_REPAIR[
                "pull_request_binding"
            ],
            pull_request=153,
            final_head=final_head,
            head_ref=self.OWNER_HEAD_REF,
            attested_at=attested_at,
        )
        self.assertIs(receipt["completed_before_attestation"], True)
        self.assertEqual(
            set(exception["final_head_ci"]["workflow_paths"]),
            set(receipt["workflow_runs"]),
        )
        altered_policy = copy.deepcopy(exception)
        altered_policy["final_head_ci"][
            "pre_cutoff_run_updated_at_or_after_cutoff_rejected"
        ] = False
        with self.assertRaisesRegex(
            verify.ContractError,
            "final-head workflow cutoff policy differs",
        ):
            verify._validate_owner_final_head_ci(
                altered_policy,
                payload,
                association_policy=verify.EXPECTED_PENDING_REVIEW_REPAIR[
                    "pull_request_binding"
                ],
                pull_request=153,
                final_head=final_head,
                head_ref=self.OWNER_HEAD_REF,
                attested_at=attested_at,
            )
        post_review_payload = copy.deepcopy(payload)
        for run in post_review_payload["workflow_runs"]:
            run["created_at"] = "2026-08-18T15:55:00Z"
            run["updated_at"] = "2026-08-18T16:00:00Z"
        merged_at = dt.datetime(
            2026,
            8,
            18,
            16,
            5,
            tzinfo=dt.timezone.utc,
        )
        independent_receipt = verify._validate_owner_final_head_ci(
            exception,
            post_review_payload,
            association_policy=verify.EXPECTED_PENDING_REVIEW_REPAIR[
                "pull_request_binding"
            ],
            pull_request=153,
            final_head=final_head,
            head_ref=self.OWNER_HEAD_REF,
            attested_at=merged_at,
            cutoff_kind="merge",
        )
        self.assertIs(
            independent_receipt["completed_before_attestation"],
            False,
        )
        self.assertEqual("merge", independent_receipt["completion_cutoff"])
        self.assertEqual(receipt["workflow_runs"], independent_receipt["workflow_runs"])
        with self.assertRaisesRegex(
            verify.ContractError,
            "pre-cutoff final-head workflow changed at or after cutoff",
        ):
            verify._validate_owner_final_head_ci(
                exception,
                post_review_payload,
                association_policy=verify.EXPECTED_PENDING_REVIEW_REPAIR[
                    "pull_request_binding"
                ],
                pull_request=153,
                final_head=final_head,
                head_ref=self.OWNER_HEAD_REF,
                attested_at=dt.datetime(
                    2026,
                    8,
                    18,
                    15,
                    59,
                    tzinfo=dt.timezone.utc,
                ),
                cutoff_kind="merge",
            )
        explicit_payload = self._owner_workflow_payload(
            final_head=final_head,
            pull_requests=[{"number": 153}],
        )
        explicit_receipt = verify._validate_owner_final_head_ci(
            exception,
            explicit_payload,
            association_policy=verify.EXPECTED_PENDING_REVIEW_REPAIR[
                "pull_request_binding"
            ],
            pull_request=153,
            final_head=final_head,
            head_ref=self.OWNER_HEAD_REF,
            attested_at=attested_at,
        )
        self.assertEqual(receipt, explicit_receipt)

        altered = copy.deepcopy(payload)
        altered["workflow_runs"].pop()
        altered["total_count"] = len(altered["workflow_runs"])
        with self.subTest(case="missing"), self.assertRaisesRegex(
            verify.ContractError,
            "INDEPENDENT_REVIEW",
        ):
            verify._validate_owner_final_head_ci(
                exception,
                altered,
                association_policy=verify.EXPECTED_PENDING_REVIEW_REPAIR[
                    "pull_request_binding"
                ],
                pull_request=153,
                final_head=final_head,
                head_ref=self.OWNER_HEAD_REF,
                attested_at=attested_at,
            )

        altered = copy.deepcopy(payload)
        older = altered["workflow_runs"][0]
        older["id"] = 2000
        older["updated_at"] = "2026-08-18T15:37:00Z"
        newer = copy.deepcopy(older)
        newer["id"] = 2001
        newer["created_at"] = "2026-08-18T15:36:00Z"
        newer["updated_at"] = "2026-08-18T15:40:00Z"
        altered["workflow_runs"].append(newer)
        altered["total_count"] = len(altered["workflow_runs"])
        receipt = verify._validate_owner_final_head_ci(
            exception,
            altered,
            association_policy=verify.EXPECTED_PENDING_REVIEW_REPAIR[
                "pull_request_binding"
            ],
            pull_request=153,
            final_head=final_head,
            head_ref=self.OWNER_HEAD_REF,
            attested_at=attested_at,
        )
        self.assertEqual(2001, receipt["workflow_runs"][older["path"]])

        for conclusion, status in (("failure", "completed"), (None, "in_progress")):
            with self.subTest(case=f"newer-{conclusion or status}"):
                failed = copy.deepcopy(altered)
                failed["workflow_runs"][-1]["conclusion"] = conclusion
                failed["workflow_runs"][-1]["status"] = status
                with self.assertRaisesRegex(
                    verify.ContractError,
                    "latest final-head workflow did not pass before attestation",
                ):
                    verify._validate_owner_final_head_ci(
                        exception,
                        failed,
                        association_policy=verify.EXPECTED_PENDING_REVIEW_REPAIR[
                            "pull_request_binding"
                        ],
                        pull_request=153,
                        final_head=final_head,
                        head_ref=self.OWNER_HEAD_REF,
                        attested_at=attested_at,
                    )

        rerun_after_attestation = copy.deepcopy(altered)
        rerun_after_attestation["workflow_runs"][-1]["conclusion"] = "failure"
        rerun_after_attestation["workflow_runs"][-1][
            "updated_at"
        ] = "2026-08-18T15:51:00Z"
        with self.assertRaisesRegex(
            verify.ContractError,
            "pre-cutoff final-head workflow changed at or after cutoff",
        ):
            verify._validate_owner_final_head_ci(
                exception,
                rerun_after_attestation,
                association_policy=verify.EXPECTED_PENDING_REVIEW_REPAIR[
                    "pull_request_binding"
                ],
                pull_request=153,
                final_head=final_head,
                head_ref=self.OWNER_HEAD_REF,
                attested_at=attested_at,
            )

        distinct_post_attestation_run = copy.deepcopy(altered)
        post_attestation = copy.deepcopy(older)
        post_attestation["id"] = 2002
        post_attestation["created_at"] = "2026-08-18T15:51:00Z"
        post_attestation["updated_at"] = "2026-08-18T15:55:00Z"
        post_attestation["conclusion"] = "failure"
        distinct_post_attestation_run["workflow_runs"].append(post_attestation)
        distinct_post_attestation_run["total_count"] = len(
            distinct_post_attestation_run["workflow_runs"]
        )
        receipt = verify._validate_owner_final_head_ci(
            exception,
            distinct_post_attestation_run,
            association_policy=verify.EXPECTED_PENDING_REVIEW_REPAIR[
                "pull_request_binding"
            ],
            pull_request=153,
            final_head=final_head,
            head_ref=self.OWNER_HEAD_REF,
            attested_at=attested_at,
        )
        self.assertEqual(2001, receipt["workflow_runs"][older["path"]])

        reordered = copy.deepcopy(altered)
        reordered["workflow_runs"].reverse()
        receipt = verify._validate_owner_final_head_ci(
            exception,
            reordered,
            association_policy=verify.EXPECTED_PENDING_REVIEW_REPAIR[
                "pull_request_binding"
            ],
            pull_request=153,
            final_head=final_head,
            head_ref=self.OWNER_HEAD_REF,
            attested_at=attested_at,
        )
        self.assertEqual(2001, receipt["workflow_runs"][older["path"]])

        mutations = (
            ("failed", "conclusion", "failure"),
            ("pending", "status", "in_progress"),
            ("stale-head", "head_sha", "d" * 40),
            (
                "not-proven-before-attestation",
                "updated_at",
                "2026-08-18T15:50:00Z",
            ),
            ("post-attestation", "updated_at", "2026-08-18T15:50:01Z"),
            ("wrong-repository", "repository", {"full_name": "Other/Repo"}),
            ("wrong-head-branch", "head_branch", "agent/other"),
            ("wrong-pr", "pull_requests", [{"number": 144}]),
            (
                "multiple-prs",
                "pull_requests",
                [{"number": 153}, {"number": 149}],
            ),
            (
                "duplicate-pr",
                "pull_requests",
                [{"number": 153}, {"number": 153}],
            ),
            ("malformed-pr", "pull_requests", [{"number": "148"}]),
            ("null-pr", "pull_requests", None),
        )
        for name, key, value in mutations:
            with self.subTest(case=name):
                altered = copy.deepcopy(payload)
                altered["workflow_runs"][0][key] = value
                with self.assertRaisesRegex(
                    verify.ContractError,
                    "INDEPENDENT_REVIEW",
                ):
                    verify._validate_owner_final_head_ci(
                        exception,
                        altered,
                        association_policy=verify.EXPECTED_PENDING_REVIEW_REPAIR[
                            "pull_request_binding"
                        ],
                        pull_request=153,
                        final_head=final_head,
                        head_ref=self.OWNER_HEAD_REF,
                        attested_at=attested_at,
                    )

        missing_pull_requests = copy.deepcopy(payload)
        del missing_pull_requests["workflow_runs"][0]["pull_requests"]
        with self.assertRaisesRegex(verify.ContractError, "INDEPENDENT_REVIEW"):
            verify._validate_owner_final_head_ci(
                exception,
                missing_pull_requests,
                association_policy=verify.EXPECTED_PENDING_REVIEW_REPAIR[
                    "pull_request_binding"
                ],
                pull_request=153,
                final_head=final_head,
                head_ref=self.OWNER_HEAD_REF,
                attested_at=attested_at,
            )

    def test_independent_review_uses_owner_attestation_as_ci_cutoff(self) -> None:
        selection = {
            "approval_mode": "independent_review",
            "merged_at": "2026-08-18T16:05:00Z",
            "review_threads_attested_at": "2026-08-18T15:50:00Z",
        }
        self.assertEqual(
            dt.datetime(2026, 8, 18, 15, 50, tzinfo=dt.timezone.utc),
            verify._owner_attestation_ci_cutoff(selection),
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "owner attestation timestamp differs",
        ):
            verify._owner_attestation_ci_cutoff(
                {
                    "approval_mode": "independent_review",
                    "merged_at": "2026-08-18T16:05:00Z",
                }
            )

    def test_owner_final_head_ci_response_completeness_fails_closed(self) -> None:
        exception = verify.EXPECTED_OWNER_ONLY_APPROVAL_EXCEPTION
        final_head = "c" * 40
        attested_at = dt.datetime(
            2026,
            8,
            18,
            15,
            50,
            tzinfo=dt.timezone.utc,
        )
        payloads = (
            {
                "total_count": 100,
                "workflow_runs": [{} for _ in range(100)],
            },
            {
                **self._owner_workflow_payload(final_head=final_head),
                "total_count": 99,
            },
        )
        for payload in payloads:
            with self.subTest(total_count=payload["total_count"]):
                with self.assertRaisesRegex(
                    verify.ContractError,
                    "INDEPENDENT_REVIEW",
                ):
                    verify._validate_owner_final_head_ci(
                        exception,
                        payload,
                        association_policy=verify.EXPECTED_PENDING_REVIEW_REPAIR[
                            "pull_request_binding"
                        ],
                        pull_request=153,
                        final_head=final_head,
                        head_ref=self.OWNER_HEAD_REF,
                        attested_at=attested_at,
                    )

    def test_owner_final_head_workflow_run_pagination_collects_stable_pages(
        self,
    ) -> None:
        exception = verify.EXPECTED_OWNER_ONLY_APPROVAL_EXCEPTION
        final_head = "c" * 40
        filler = [
            self._owner_workflow_run(
                run_id,
                f".github/workflows/irrelevant-{run_id}.yml",
                final_head=final_head,
            )
            for run_id in range(1, 101)
        ]
        for run in filler:
            run["display_title"] = "x" * 12_000
        required = [
            self._owner_workflow_run(run_id, path, final_head=final_head)
            for run_id, path in enumerate(
                exception["final_head_ci"]["workflow_paths"],
                start=101,
            )
        ]
        pages = [
            {"total_count": 104, "workflow_runs": filler},
            {"total_count": 104, "workflow_runs": required},
        ]
        first_page_bytes = len(json.dumps(pages[0]).encode())
        self.assertGreater(first_page_bytes, verify.GITHUB_API_RESPONSE_BYTES)
        self.assertLess(
            first_page_bytes,
            verify.GITHUB_WORKFLOW_RUN_PAGE_RESPONSE_BYTES,
        )
        read_limits: list[int] = []

        class Response:
            status = 200

            def __init__(self, payload: object) -> None:
                self.payload = json.dumps(payload).encode()

            def __enter__(self) -> Response:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, limit: int) -> bytes:
                read_limits.append(limit)
                return self.payload[:limit]

        with mock.patch.object(
            verify,
            "urlopen",
            side_effect=[Response(page) for page in pages + pages],
        ) as request:
            payload = verify._github_workflow_run_pages(
                exception,
                association_policy=verify.EXPECTED_PENDING_REVIEW_REPAIR[
                    "pull_request_binding"
                ],
                pull_request=153,
                final_head=final_head,
                head_ref=self.OWNER_HEAD_REF,
                token="ephemeral-token",
            )

        self.assertEqual(104, payload["total_count"])
        self.assertEqual(104, len(payload["workflow_runs"]))
        self.assertEqual(4, request.call_count)
        self.assertEqual(
            [verify.GITHUB_WORKFLOW_RUN_PAGE_RESPONSE_BYTES + 1] * 4,
            read_limits,
        )
        urls = [call.args[0].full_url for call in request.call_args_list]
        for index, page in enumerate((1, 2, 1, 2)):
            with self.subTest(request=index):
                self.assertIn(f"page={page}", urls[index])
                self.assertIn("per_page=100", urls[index])
                self.assertIn("event=pull_request", urls[index])

                self.assertIn(f"head_sha={final_head}", urls[index])
                self.assertIn(
                    "branch=agent%2Fissue-63-sequence-6-activation",
                    urls[index],
                )
        receipt = verify._validate_owner_final_head_ci(
            exception,
            payload,
            association_policy=verify.EXPECTED_PENDING_REVIEW_REPAIR[
                "pull_request_binding"
            ],
            pull_request=153,
            final_head=final_head,
            head_ref=self.OWNER_HEAD_REF,
            attested_at=dt.datetime(
                2026,
                8,
                18,
                15,
                50,
                tzinfo=dt.timezone.utc,
            ),
        )
        self.assertEqual(
            set(exception["final_head_ci"]["workflow_paths"]),
            set(receipt["workflow_runs"]),
        )

    def test_owner_final_head_workflow_run_page_rejects_oversized_response(
        self,
    ) -> None:
        exception = verify.EXPECTED_OWNER_ONLY_APPROVAL_EXCEPTION
        final_head = "c" * 40
        run = self._owner_workflow_run(
            1,
            ".github/workflows/irrelevant.yml",
            final_head=final_head,
        )
        run["display_title"] = "x" * verify.GITHUB_WORKFLOW_RUN_PAGE_RESPONSE_BYTES
        page = {"total_count": 1, "workflow_runs": [run]}
        read_limits: list[int] = []

        class Response:
            status = 200

            def __init__(self, payload: object) -> None:
                self.payload = json.dumps(payload).encode()

            def __enter__(self) -> Response:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, limit: int) -> bytes:
                read_limits.append(limit)
                return self.payload[:limit]

        with (
            mock.patch.object(verify, "urlopen", return_value=Response(page)),
            self.assertRaisesRegex(verify.ContractError, "response is too large"),
        ):
            verify._github_workflow_run_pages(
                exception,
                association_policy=verify.EXPECTED_PENDING_REVIEW_REPAIR[
                    "pull_request_binding"
                ],
                pull_request=153,
                final_head=final_head,
                head_ref=self.OWNER_HEAD_REF,
                token="ephemeral-token",
            )
        self.assertEqual(
            [verify.GITHUB_WORKFLOW_RUN_PAGE_RESPONSE_BYTES + 1],
            read_limits,
        )

    def test_owner_final_head_workflow_run_pagination_accepts_999_runs(
        self,
    ) -> None:
        exception = verify.EXPECTED_OWNER_ONLY_APPROVAL_EXCEPTION
        final_head = "c" * 40
        runs = [
            self._owner_workflow_run(
                run_id,
                f".github/workflows/irrelevant-{run_id}.yml",
                final_head=final_head,
            )
            for run_id in range(1, 1000)
        ]
        pages = [
            {"total_count": 999, "workflow_runs": runs[offset : offset + 100]}
            for offset in range(0, 999, 100)
        ]

        class Response:
            status = 200

            def __init__(self, payload: object) -> None:
                self.payload = json.dumps(payload).encode()

            def __enter__(self) -> Response:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, limit: int) -> bytes:
                return self.payload[:limit]

        with mock.patch.object(
            verify,
            "urlopen",
            side_effect=[Response(page) for page in pages + pages],
        ) as request:
            payload = verify._github_workflow_run_pages(
                exception,
                association_policy=verify.EXPECTED_PENDING_REVIEW_REPAIR[
                    "pull_request_binding"
                ],
                pull_request=153,
                final_head=final_head,
                head_ref=self.OWNER_HEAD_REF,
                token="ephemeral-token",
            )
        self.assertEqual(999, payload["total_count"])
        self.assertEqual(999, len(payload["workflow_runs"]))
        self.assertEqual(20, request.call_count)

    def test_owner_final_head_workflow_run_pagination_fails_closed(
        self,
    ) -> None:
        exception = verify.EXPECTED_OWNER_ONLY_APPROVAL_EXCEPTION
        final_head = "c" * 40

        altered = copy.deepcopy(exception)
        altered["final_head_ci"]["maximum_page_bytes"] -= 1
        with self.assertRaisesRegex(
            verify.ContractError,
            "final-head workflow pagination policy differs",
        ):
            verify._github_workflow_run_pages(
                altered,
                association_policy=verify.EXPECTED_PENDING_REVIEW_REPAIR[
                    "pull_request_binding"
                ],
                pull_request=153,
                final_head=final_head,
                head_ref=self.OWNER_HEAD_REF,
                token="ephemeral-token",
            )

        class Response:
            status = 200

            def __init__(self, payload: object) -> None:
                self.payload = json.dumps(payload).encode()

            def __enter__(self) -> Response:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, limit: int) -> bytes:
                return self.payload

        full_pages = []
        for page in range(10):
            page_runs = [
                self._owner_workflow_run(
                    page * 100 + offset + 1,
                    f".github/workflows/irrelevant-{page}-{offset}.yml",
                    final_head=final_head,
                )
                for offset in range(100)
            ]
            full_pages.append({"total_count": 999, "workflow_runs": page_runs})
        with (
            mock.patch.object(
                verify,
                "urlopen",
                side_effect=[Response(page) for page in full_pages],
            ) as request,
            self.assertRaisesRegex(
                verify.ContractError,
                "bound exceeded|exhaustion was not proven",
            ),
        ):
            verify._github_workflow_run_pages(
                exception,
                association_policy=verify.EXPECTED_PENDING_REVIEW_REPAIR[
                    "pull_request_binding"
                ],
                pull_request=153,
                final_head=final_head,
                head_ref=self.OWNER_HEAD_REF,
                token="ephemeral-token",
            )
        self.assertEqual(10, request.call_count)

        stable_run = self._owner_workflow_run(
            1,
            ".github/workflows/irrelevant.yml",
            final_head=final_head,
        )
        mutations = (
            ("duplicate-id", "id", 1),
            ("non-positive-id", "id", 0),
            ("changed-path", "path", ".github/workflows/changed.yml"),
            ("changed-head", "head_sha", "d" * 40),
            ("changed-branch", "head_branch", "agent/other"),
            ("changed-status", "status", "in_progress"),
            ("changed-timestamp", "updated_at", "2026-08-18T12:10:01Z"),
            ("changed-pr", "pull_requests", [{"number": 144}]),
            (
                "multiple-prs",
                "pull_requests",
                [{"number": 153}, {"number": 149}],
            ),
            (
                "duplicate-pr",
                "pull_requests",
                [{"number": 153}, {"number": 153}],
            ),
            ("malformed-pr", "pull_requests", [{"number": "148"}]),
        )
        for name, key, value in mutations:
            with self.subTest(case=name):
                first = {"total_count": 1, "workflow_runs": [stable_run]}
                changed_run = copy.deepcopy(stable_run)
                changed_run[key] = value
                if name == "duplicate-id":
                    first = {
                        "total_count": 2,
                        "workflow_runs": [stable_run, copy.deepcopy(stable_run)],
                    }
                    responses = [Response(first)]
                else:
                    second = {"total_count": 1, "workflow_runs": [changed_run]}
                    responses = [Response(first), Response(second)]
                with (
                    mock.patch.object(
                        verify,
                        "urlopen",
                        side_effect=responses,
                    ),
                    self.assertRaisesRegex(verify.ContractError, "INDEPENDENT_REVIEW"),
                ):
                    verify._github_workflow_run_pages(
                        exception,
                        association_policy=verify.EXPECTED_PENDING_REVIEW_REPAIR[
                            "pull_request_binding"
                        ],
                        pull_request=153,
                        final_head=final_head,
                        head_ref=self.OWNER_HEAD_REF,
                        token="ephemeral-token",
                    )

    def test_owner_review_threads_allow_resolved_and_outdated_threads(
        self,
    ) -> None:
        exception = verify.EXPECTED_OWNER_ONLY_APPROVAL_EXCEPTION
        final_head = "c" * 40
        payload = self._owner_review_thread_payload(
            [
                {
                    "id": "thread-0",
                    "isResolved": True,
                    "isOutdated": True,
                },
                {
                    "id": "thread-1",
                    "isResolved": False,
                    "isOutdated": True,
                },
                {
                    "id": "thread-2",
                    "isResolved": True,
                    "isOutdated": False,
                },
            ],
            final_head=final_head,
        )
        receipt = verify._validate_owner_review_threads(
            exception,
            payload,
            pull_request=153,
            final_head=final_head,
        )
        self.assertEqual(
            {
                "total": 3,
                "current_non_outdated": 1,
                "current_unresolved": 0,
                "resolved": 1,
                "outdated": 2,
                "snapshot_sha256": (
                    "9d941cf5d3e3231230662402cb354653147c988ce907e9ec"
                    "8853c3b6963d23a1"
                ),
            },
            receipt,
        )

        unresolved = self._owner_review_thread_payload(
            [
                {
                    "id": "thread-unresolved",
                    "isResolved": False,
                    "isOutdated": False,
                }
            ],
            final_head=final_head,
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "current non-outdated unresolved review-thread count differs",
        ):
            verify._validate_owner_review_threads(
                exception,
                unresolved,
                pull_request=153,
                final_head=final_head,
            )

        resolved_after_merge = [
            {
                "id": "thread-resolution-race",
                "isResolved": True,
                "isOutdated": False,
            }
        ]
        receipt_after_merge = verify._owner_review_thread_receipt(
            exception,
            resolved_after_merge,
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "snapshot differs from the pre-merge owner attestation",
        ):
            verify._validate_review_thread_snapshot_attestation(
                {
                    "review_thread_snapshot_sha256": (
                        verify._review_thread_snapshot_sha256(
                            [
                                {
                                    "id": "thread-resolution-race",
                                    "isResolved": False,
                                    "isOutdated": False,
                                }
                            ]
                        )
                    )
                },
                receipt_after_merge,
            )

        wrong_head = self._owner_review_thread_payload(final_head="d" * 40)
        with self.assertRaisesRegex(
            verify.ContractError,
            "review-thread GraphQL response is truncated or malformed",
        ):
            verify._validate_owner_review_threads(
                exception,
                wrong_head,
                pull_request=153,
                final_head=final_head,
            )

    def test_premerge_review_thread_snapshot_uses_exact_graphql_receipt(
        self,
    ) -> None:
        expected = {
            "total": 1,
            "current_non_outdated": 1,
            "current_unresolved": 0,
            "resolved": 1,
            "outdated": 0,
            "snapshot_sha256": "a" * 64,
        }
        stdout = io.StringIO()
        contract_path = ROOT / verify.CONTRACT_PATH
        original_load_json = verify._load_json
        active_contract, active_exception = self._active_owner_contract_fixture()

        def load_json(path: Path) -> dict[str, object]:
            if path == contract_path:
                return active_contract
            return original_load_json(path)

        with (
            mock.patch.object(verify, "_load_json", side_effect=load_json),
            mock.patch.object(
                verify,
                "EXPECTED_OWNER_ONLY_APPROVAL_EXCEPTION",
                active_exception,
            ),
            mock.patch.object(
                verify,
                "_github_review_threads_pages",
                return_value=expected,
            ) as query,
            contextlib.redirect_stdout(stdout),
        ):
            verify.print_review_thread_snapshot(
                ROOT,
                repository="TommyKammy/Shirokuma",
                pull_request=153,
                final_head="c" * 40,
                token="ephemeral-token",
            )
        self.assertEqual(expected, json.loads(stdout.getvalue()))
        query.assert_called_once_with(
            active_exception,
            pull_request=153,
            final_head="c" * 40,
            token="ephemeral-token",
        )
        for repository, pull_request, final_head in (
            ("Other/Repo", 153, "c" * 40),
            ("TommyKammy/Shirokuma", 152, "c" * 40),
            ("TommyKammy/Shirokuma", 153, "short"),
        ):
            with self.assertRaisesRegex(verify.ContractError, "INDEPENDENT_REVIEW"):
                verify.print_review_thread_snapshot(
                    ROOT,
                    repository=repository,
                    pull_request=pull_request,
                    final_head=final_head,
                    token="ephemeral-token",
                )

    def test_owner_review_thread_graphql_response_fails_closed(self) -> None:
        exception = verify.EXPECTED_OWNER_ONLY_APPROVAL_EXCEPTION
        cases = (
            self._owner_review_thread_payload(has_next_page=True),
            self._owner_review_thread_payload(
                [
                    {"isResolved": True, "isOutdated": False}
                    for _ in range(101)
                ]
            ),
            self._owner_review_thread_payload(pull_request=144),
            {
                **self._owner_review_thread_payload(),
                "errors": [{"message": "truncated query"}],
            },
        )
        for index, payload in enumerate(cases):
            with self.subTest(case=index):
                with self.assertRaisesRegex(
                    verify.ContractError,
                    "INDEPENDENT_REVIEW",
                ):
                    verify._validate_owner_review_threads(
                        exception,
                        payload,
                        pull_request=153,
                        final_head="c" * 40,
                    )

    def test_owner_review_thread_cursor_pagination_collects_every_page(
        self,
    ) -> None:
        exception = verify.EXPECTED_OWNER_ONLY_APPROVAL_EXCEPTION
        final_head = "c" * 40
        def outdated(thread_id: int) -> dict[str, object]:
            return {
                "id": f"thread-{thread_id}",
                "isResolved": False,
                "isOutdated": True,
            }

        pages = [
            self._owner_review_thread_payload(
                [outdated(thread_id) for thread_id in range(100)],
                final_head=final_head,
                has_next_page=True,
                end_cursor="cursor-1",
                total_count=101,
            ),
            self._owner_review_thread_payload(
                [outdated(100)],
                final_head=final_head,
                end_cursor="cursor-2",
                total_count=101,
            ),
        ]
        stable_pages = [
            *pages,
            pages[0],
            self._owner_review_thread_payload(
                [outdated(100)],
                final_head=final_head,
                end_cursor=None,
                total_count=101,
            ),
        ]

        class Response:
            status = 200

            def __init__(self, payload: object) -> None:
                self.payload = json.dumps(payload).encode()

            def __enter__(self) -> Response:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, limit: int) -> bytes:
                return self.payload

        with mock.patch.object(
            verify,
            "urlopen",
            side_effect=[Response(page) for page in stable_pages],
        ) as request:
            receipt = verify._github_review_threads_pages(
                exception,
                pull_request=153,
                final_head=final_head,
                token="ephemeral-token",
            )

        self.assertEqual(
            {
                "total": 101,
                "current_non_outdated": 0,
                "current_unresolved": 0,
                "resolved": 0,
                "outdated": 101,
                "snapshot_sha256": (
                    "e222780208c8e0167d1cb8ac26aec1cf7baff1a3b8fbe070"
                    "ef75553ea6594d4e"
                ),
            },
            receipt,
        )
        self.assertEqual(4, request.call_count)
        requests = [call.args[0] for call in request.call_args_list]
        first = json.loads(requests[0].data)
        second = json.loads(requests[1].data)
        third = json.loads(requests[2].data)
        fourth = json.loads(requests[3].data)
        self.assertIsNone(first["variables"]["after"])
        self.assertEqual("cursor-1", second["variables"]["after"])
        self.assertIsNone(third["variables"]["after"])
        self.assertEqual("cursor-1", fourth["variables"]["after"])
        self.assertIn("after:$after", first["query"])
        self.assertIn("endCursor", first["query"])

    def test_owner_review_thread_cursor_pagination_accepts_exact_limit(
        self,
    ) -> None:
        exception = verify.EXPECTED_OWNER_ONLY_APPROVAL_EXCEPTION
        final_head = "c" * 40

        def page_payload(page: int, *, terminal_cursor: str | None) -> object:
            first_id = (page - 1) * 100
            return self._owner_review_thread_payload(
                [
                    {
                        "id": f"thread-{thread_id}",
                        "isResolved": False,
                        "isOutdated": True,
                    }
                    for thread_id in range(first_id, first_id + 100)
                ],
                final_head=final_head,
                has_next_page=page < 10,
                end_cursor=(
                    f"cursor-{page}" if page < 10 else terminal_cursor
                ),
                total_count=1000,
            )

        first_scan = [
            page_payload(page, terminal_cursor="cursor-10")
            for page in range(1, 11)
        ]
        second_scan = [
            page_payload(page, terminal_cursor=None) for page in range(1, 11)
        ]

        class Response:
            status = 200

            def __init__(self, payload: object) -> None:
                self.payload = json.dumps(payload).encode()

            def __enter__(self) -> Response:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, limit: int) -> bytes:
                return self.payload

        with mock.patch.object(
            verify,
            "urlopen",
            side_effect=[Response(page) for page in first_scan + second_scan],
        ) as request:
            receipt = verify._github_review_threads_pages(
                exception,
                pull_request=153,
                final_head=final_head,
                token="ephemeral-token",
            )

        self.assertEqual(1000, receipt["total"])
        self.assertEqual(1000, receipt["outdated"])
        self.assertEqual(0, receipt["current_non_outdated"])
        self.assertEqual(20, request.call_count)

    def test_owner_review_thread_cursor_pagination_fails_closed(self) -> None:
        exception = verify.EXPECTED_OWNER_ONLY_APPROVAL_EXCEPTION
        final_head = "c" * 40
        def outdated(thread_id: int) -> dict[str, object]:
            return {
                "id": f"thread-{thread_id}",
                "isResolved": False,
                "isOutdated": True,
            }

        class Response:
            status = 200

            def __init__(self, payload: object) -> None:
                self.payload = json.dumps(payload).encode()

            def __enter__(self) -> Response:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, limit: int) -> bytes:
                return self.payload

        malformed_pages = (
            self._owner_review_thread_payload(
                [outdated(0)],
                final_head=final_head,
                has_next_page="true",  # type: ignore[arg-type]
                end_cursor="cursor-1",
                total_count=2,
            ),
            self._owner_review_thread_payload(
                [outdated(0)],
                final_head=final_head,
                has_next_page=True,
                total_count=2,
            ),
            self._owner_review_thread_payload(
                [outdated(0)],
                final_head=final_head,
                end_cursor=7,  # type: ignore[arg-type]
                total_count=1,
            ),
            self._owner_review_thread_payload(
                [outdated(0)],
                final_head=final_head,
                total_count=-1,
            ),
            self._owner_review_thread_payload(
                [outdated(0)],
                final_head=final_head,
                total_count=1001,
            ),
            self._owner_review_thread_payload(
                [],
                final_head=final_head,
                has_next_page=True,
                end_cursor="cursor-1",
                total_count=1,
            ),
            self._owner_review_thread_payload(
                [
                    {
                        "isResolved": False,
                        "isOutdated": True,
                    }
                ],
                final_head=final_head,
                total_count=1,
            ),
            self._owner_review_thread_payload(
                [
                    {
                        "id": "",
                        "isResolved": False,
                        "isOutdated": True,
                    }
                ],
                final_head=final_head,
                total_count=1,
            ),
            self._owner_review_thread_payload(
                [
                    {
                        "id": 7,
                        "isResolved": False,
                        "isOutdated": True,
                    }
                ],
                final_head=final_head,
                total_count=1,
            ),
            self._owner_review_thread_payload(
                ["thread"],  # type: ignore[list-item]
                final_head=final_head,
                total_count=1,
            ),
            self._owner_review_thread_payload(
                [outdated(0)],
                repository="OtherOwner/Shirokuma",
                final_head=final_head,
                total_count=1,
            ),
        )
        for index, page in enumerate(malformed_pages):
            with (
                self.subTest(malformed=index),
                mock.patch.object(verify, "urlopen", return_value=Response(page)),
                self.assertRaisesRegex(verify.ContractError, "INDEPENDENT_REVIEW"),
            ):
                verify._github_review_threads_pages(
                    exception,
                    pull_request=153,
                    final_head=final_head,
                    token="ephemeral-token",
                )

        cursor_cycle = [
            self._owner_review_thread_payload(
                [outdated(0)],
                final_head=final_head,
                has_next_page=True,
                end_cursor="cursor-1",
                total_count=3,
            ),
            self._owner_review_thread_payload(
                [outdated(1)],
                final_head=final_head,
                has_next_page=True,
                end_cursor="cursor-1",
                total_count=3,
            ),
        ]
        with (
            mock.patch.object(
                verify,
                "urlopen",
                side_effect=[Response(page) for page in cursor_cycle],
            ) as request,
            self.assertRaisesRegex(verify.ContractError, "INDEPENDENT_REVIEW"),
        ):
            verify._github_review_threads_pages(
                exception,
                pull_request=153,
                final_head=final_head,
                token="ephemeral-token",
            )
        self.assertEqual(2, request.call_count)

        maximum_pages = [
            self._owner_review_thread_payload(
                [outdated(page)],
                final_head=final_head,
                has_next_page=True,
                end_cursor=f"cursor-{page}",
                total_count=1000,
            )
            for page in range(1, 11)
        ]
        with (
            mock.patch.object(
                verify,
                "urlopen",
                side_effect=[Response(page) for page in maximum_pages],
            ) as request,
            self.assertRaisesRegex(verify.ContractError, "INDEPENDENT_REVIEW"),
        ):
            verify._github_review_threads_pages(
                exception,
                pull_request=153,
                final_head=final_head,
                token="ephemeral-token",
            )
        self.assertEqual(10, request.call_count)

        first_page = self._owner_review_thread_payload(
            [outdated(0)],
            final_head=final_head,
            has_next_page=True,
            end_cursor="cursor-1",
            total_count=2,
        )
        fail_closed_second_pages = (
            self._owner_review_thread_payload(
                [outdated(1)],
                final_head="d" * 40,
                end_cursor="cursor-2",
                total_count=2,
            ),
            self._owner_review_thread_payload(
                [
                    {
                        "id": "thread-1",
                        "isResolved": False,
                        "isOutdated": False,
                    }
                ],
                final_head=final_head,
                end_cursor="cursor-2",
                total_count=2,
            ),
            self._owner_review_thread_payload(
                [outdated(0)],
                final_head=final_head,
                end_cursor="cursor-2",
                total_count=2,
            ),
            self._owner_review_thread_payload(
                [outdated(1)],
                final_head=final_head,
                end_cursor="cursor-2",
                total_count=3,
            ),
            self._owner_review_thread_payload(
                [outdated(1)],
                pull_request=144,
                final_head=final_head,
                end_cursor="cursor-2",
                total_count=2,
            ),
            {
                **self._owner_review_thread_payload(
                    [outdated(1)],
                    final_head=final_head,
                    end_cursor="cursor-2",
                    total_count=2,
                ),
                "errors": [{"message": "page failed"}],
            },
        )
        for index, second_page in enumerate(fail_closed_second_pages):
            with (
                self.subTest(later_page=index),
                mock.patch.object(
                    verify,
                    "urlopen",
                    side_effect=[Response(first_page), Response(second_page)],
                ),
                self.assertRaisesRegex(verify.ContractError, "INDEPENDENT_REVIEW"),
            ):
                verify._github_review_threads_pages(
                    exception,
                    pull_request=153,
                    final_head=final_head,
                    token="ephemeral-token",
                )

        empty_terminal_first_page = self._owner_review_thread_payload(
            [outdated(0)],
            final_head=final_head,
            has_next_page=True,
            end_cursor="cursor-1",
            total_count=1,
        )
        empty_terminal_page = self._owner_review_thread_payload(
            [],
            final_head=final_head,
            total_count=1,
        )
        with (
            mock.patch.object(
                verify,
                "urlopen",
                side_effect=[
                    Response(empty_terminal_first_page),
                    Response(empty_terminal_page),
                ],
            ),
            self.assertRaisesRegex(verify.ContractError, "INDEPENDENT_REVIEW"),
        ):
            verify._github_review_threads_pages(
                exception,
                pull_request=153,
                final_head=final_head,
                token="ephemeral-token",
            )

        incomplete_first_page = self._owner_review_thread_payload(
            [outdated(0)],
            final_head=final_head,
            has_next_page=True,
            end_cursor="cursor-1",
            total_count=3,
        )
        incomplete_terminal_page = self._owner_review_thread_payload(
            [outdated(1)],
            final_head=final_head,
            end_cursor="cursor-2",
            total_count=3,
        )
        with (
            mock.patch.object(
                verify,
                "urlopen",
                side_effect=[
                    Response(incomplete_first_page),
                    Response(incomplete_terminal_page),
                ],
            ),
            self.assertRaisesRegex(verify.ContractError, "INDEPENDENT_REVIEW"),
        ):
            verify._github_review_threads_pages(
                exception,
                pull_request=153,
                final_head=final_head,
                token="ephemeral-token",
            )

        stable_first = self._owner_review_thread_payload(
            [outdated(0), outdated(1)],
            final_head=final_head,
            end_cursor="terminal-1",
            total_count=2,
        )
        reordered_second = self._owner_review_thread_payload(
            [outdated(1), outdated(0)],
            final_head=final_head,
            end_cursor=None,
            total_count=2,
        )
        with (
            mock.patch.object(
                verify,
                "urlopen",
                side_effect=[Response(stable_first), Response(reordered_second)],
            ) as request,
            self.assertRaisesRegex(verify.ContractError, "INDEPENDENT_REVIEW"),
        ):
            verify._github_review_threads_pages(
                exception,
                pull_request=153,
                final_head=final_head,
                token="ephemeral-token",
            )
        self.assertEqual(2, request.call_count)

    def test_owner_attestation_query_checks_ci_and_review_threads(self) -> None:
        commit = "b" * 40
        final_head = "c" * 40
        pulls = [self._owner_pull(final_head=final_head, merge_commit=commit)]
        first_comment_page = [
            {"id": comment_id, "body": "ordinary comment"}
            for comment_id in range(5264799900, 5264800000)
        ]
        second_comment_page = [
            {
                "id": 5264800000,
                "body": (
                    "Owner final-head attestation for PR #153\n\n"
                    "Decision: APPROVED\n"
                    f"Final head: {final_head}\n"
                    "Review-thread snapshot SHA-256: "
                    f"{self.EMPTY_REVIEW_THREAD_SNAPSHOT_SHA256}\n"
                    "Exception: https://github.com/TommyKammy/Shirokuma/"
                    "issues/63#issuecomment-5324238100"
                ),
                "user": {"login": "TommyKammy", "type": "User"},
                "author_association": "OWNER",
                "created_at": "2026-08-18T15:50:00Z",
                "updated_at": "2026-08-18T15:50:00Z",
            }
        ]
        workflow_payload = self._owner_workflow_payload(final_head=final_head)
        thread_payload = self._owner_review_thread_payload()

        class Response:
            status = 200

            def __init__(self, payload: object) -> None:
                self.payload = json.dumps(payload).encode()

            def __enter__(self) -> Response:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, limit: int) -> bytes:
                return self.payload

        stdout = io.StringIO()
        contract_path = ROOT / verify.CONTRACT_PATH
        original_load_json = verify._load_json
        active_contract = self._active_owner_contract()

        def load_json(path: Path) -> dict[str, object]:
            if path == contract_path:
                return active_contract
            return original_load_json(path)

        with (
            mock.patch.object(verify, "_load_json", side_effect=load_json),
            mock.patch.object(
                verify,
                "urlopen",
                side_effect=[
                    Response(pulls),
                    Response(pulls),
                    Response([]),
                    Response([]),
                    Response(first_comment_page),
                    Response(second_comment_page),
                    Response(first_comment_page),
                    Response(second_comment_page),
                    Response(pulls),
                    Response(pulls),
                    Response(pulls),
                    Response(pulls),
                    Response(pulls),
                    Response(pulls),
                    Response(workflow_payload),
                    Response(workflow_payload),
                    Response(thread_payload),
                    Response(thread_payload),
                    Response(first_comment_page),
                    Response(second_comment_page),
                    Response(first_comment_page),
                    Response(second_comment_page),
                    Response(pulls),
                    Response(pulls),
                    Response(pulls),
                    Response(pulls),
                    Response(pulls),
                    Response(pulls),
                    Response(workflow_payload),
                    Response(workflow_payload),
                    Response(thread_payload),
                    Response(thread_payload),
                    Response(first_comment_page),
                    Response(second_comment_page),
                    Response(first_comment_page),
                    Response(second_comment_page),
                ],
            ) as request,
            contextlib.redirect_stdout(stdout),
        ):
            verify.verify_independent_review(
                ROOT,
                repository="TommyKammy/Shirokuma",
                commit=commit,
                token="ephemeral-token",
            )

        receipt = json.loads(stdout.getvalue())
        self.assertEqual("owner_final_head_attestation", receipt["approval_mode"])
        self.assertEqual(0, receipt["review_threads"]["current_non_outdated"])
        self.assertIs(
            receipt["final_head_ci"]["completed_before_attestation"],
            True,
        )
        self.assertIs(
            receipt["owner_decision_revalidated_after_final_api_gates"],
            True,
        )
        self.assertEqual(153, receipt["pull_request_binding"]["pull_request"])
        self.assertEqual(commit, receipt["pull_request_binding"]["merge_commit"])
        self.assertEqual(final_head, receipt["pull_request_binding"]["final_head"])
        self.assertEqual(
            verify.EXPECTED_PUBLICATION_ATTEMPT["before_sha"],
            receipt["pull_request_binding"]["base_sha"],
        )
        self.assertEqual(self.OWNER_HEAD_REF, receipt["pull_request_binding"]["head_ref"])
        self.assertEqual(36, request.call_count)
        requests = [call.args[0] for call in request.call_args_list]
        self.assertIn(f"/commits/{commit}/pulls?per_page=100", requests[0].full_url)
        self.assertIn("/pulls/153/reviews?per_page=100", requests[2].full_url)
        self.assertIn("/pulls/153/reviews?per_page=100", requests[3].full_url)
        self.assertIn(
            "/issues/153/comments?per_page=100&page=1",
            requests[4].full_url,
        )
        self.assertIn(
            "/issues/153/comments?per_page=100&page=2",
            requests[5].full_url,
        )
        self.assertIn(
            "/issues/153/comments?per_page=100&page=1",
            requests[6].full_url,
        )
        self.assertIn(
            "/issues/153/comments?per_page=100&page=2",
            requests[7].full_url,
        )
        self.assertIn(f"/commits/{commit}/pulls?", requests[8].full_url)
        self.assertIn(f"/commits/{final_head}/pulls?", requests[10].full_url)
        self.assertIn("/pulls?state=all", requests[12].full_url)
        self.assertIn(
            f"/actions/runs?event=pull_request&head_sha={final_head}",
            requests[14].full_url,
        )
        self.assertIn(
            "branch=agent%2Fissue-63-sequence-6-activation",
            requests[14].full_url,
        )
        self.assertEqual(requests[14].full_url, requests[15].full_url)
        self.assertEqual("https://api.github.com/graphql", requests[16].full_url)
        self.assertEqual("POST", requests[16].get_method())
        graphql_request = json.loads(requests[16].data)
        second_graphql_request = json.loads(requests[17].data)
        self.assertEqual(
            {
                "owner": "TommyKammy",
                "name": "Shirokuma",
                "number": 153,
                "after": None,
            },
            graphql_request["variables"],
        )
        self.assertEqual(
            graphql_request["variables"],
            second_graphql_request["variables"],
        )
        self.assertIn("headRefOid", graphql_request["query"])
        self.assertIn("after:$after", graphql_request["query"])
        self.assertIn("endCursor", graphql_request["query"])
        self.assertIn(
            "/issues/153/comments?per_page=100&page=1",
            requests[18].full_url,
        )
        self.assertIn(
            "/issues/153/comments?per_page=100&page=2",
            requests[19].full_url,
        )
        self.assertIn(
            "/issues/153/comments?per_page=100&page=1",
            requests[20].full_url,
        )
        self.assertIn(
            "/issues/153/comments?per_page=100&page=2",
            requests[21].full_url,
        )
        self.assertIn(f"/commits/{commit}/pulls?", requests[22].full_url)
        self.assertIn(f"/commits/{final_head}/pulls?", requests[24].full_url)
        self.assertIn("/pulls?state=all", requests[26].full_url)
        self.assertEqual(requests[28].full_url, requests[29].full_url)
        self.assertEqual("https://api.github.com/graphql", requests[30].full_url)
        self.assertEqual(requests[30].full_url, requests[31].full_url)

    def test_owner_comment_pagination_fails_closed(self) -> None:
        comment_url = (
            "https://api.github.com/repos/TommyKammy/Shirokuma/"
            "issues/153/comments"
        )
        full_page = [
            {"id": comment_id, "body": "ordinary comment"}
            for comment_id in range(1, 101)
        ]

        class Response:
            status = 200

            def __init__(self, payload: object) -> None:
                self.payload = json.dumps(payload).encode()

            def __enter__(self) -> Response:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, limit: int) -> bytes:
                return self.payload

        with (
            mock.patch.object(
                verify,
                "urlopen",
                side_effect=[Response(full_page), Response({"comments": []})],
            ) as request,
            self.assertRaisesRegex(
                verify.ContractError,
                "paginated GitHub API response is malformed",
            ),
        ):
            verify._github_api_paginated_list(
                comment_url,
                token="ephemeral-token",
            )
        self.assertEqual(2, request.call_count)

        self.assertIn("per_page=100&page=2", request.call_args.args[0].full_url)

        accepted_pages = [
            [
                {"id": page * 100 + offset + 1, "body": "comment"}
                for offset in range(100 if page < 9 else 99)
            ]
            for page in range(10)
        ]
        with mock.patch.object(
            verify,
            "urlopen",
            side_effect=[
                Response(page)
                for page in [*accepted_pages, *accepted_pages]
            ],
        ) as request:
            comments = verify._github_api_paginated_list(
                comment_url,
                token="ephemeral-token",
            )
        self.assertEqual(verify.GITHUB_OWNER_COMMENT_MAXIMUM_ITEMS, len(comments))
        self.assertEqual(20, request.call_count)

        with (
            mock.patch.object(
                verify,
                "urlopen",
                side_effect=[
                    Response(
                        [
                            {"id": page * 100 + offset + 1, "body": "comment"}
                            for offset in range(100)
                        ]
                    )
                    for page in range(10)
                ],
            ) as request,
            self.assertRaisesRegex(
                verify.ContractError,
                "pagination bound exceeded",
            ),
        ):
            verify._github_api_paginated_list(
                comment_url,
                token="ephemeral-token",
            )
        self.assertEqual(10, request.call_count)
        self.assertIn("per_page=100&page=10", request.call_args.args[0].full_url)

        with (
            mock.patch.object(
                verify,
                "urlopen",
                return_value=Response([{"id": 2}, {"id": 1}]),
            ),
            self.assertRaisesRegex(
                verify.ContractError,
                "comment IDs are not strictly increasing",
            ),
        ):
            verify._github_api_paginated_list(
                comment_url,
                token="ephemeral-token",
            )

        with (
            mock.patch.object(
                verify,
                "urlopen",
                side_effect=[
                    Response([{"id": 1, "body": "before"}]),
                    Response([{"id": 1, "body": "after"}]),
                ],
            ) as request,
            self.assertRaisesRegex(
                verify.ContractError,
                "comment snapshot is unstable",
            ),
        ):
            verify._github_api_paginated_list(
                comment_url,
                token="ephemeral-token",
            )
        self.assertEqual(2, request.call_count)

    def test_owner_attestation_revalidation_rejects_later_revocation(self) -> None:
        contract = self._active_owner_contract()
        commit = "b" * 40
        final_head = "c" * 40
        pull = {
            "number": 153,
            "state": "closed",
            "merged_at": "2026-08-18T16:00:00Z",
            "merge_commit_sha": commit,
            "base": {
                "ref": "main",
                "sha": verify.EXPECTED_PUBLICATION_ATTEMPT["before_sha"],
            },
            "head": {"sha": final_head},
        }
        approved = {
            "id": 5264800000,
            "body": (
                "Owner final-head attestation for PR #153\n\n"
                "Decision: APPROVED\n"
                f"Final head: {final_head}\n"
                "Review-thread snapshot SHA-256: "
                f"{self.EMPTY_REVIEW_THREAD_SNAPSHOT_SHA256}\n"
                "Exception: https://github.com/TommyKammy/Shirokuma/"
                "issues/63#issuecomment-5324238100"
            ),
            "user": {"login": "TommyKammy", "type": "User"},
            "author_association": "OWNER",
            "created_at": "2026-08-18T15:40:00Z",
            "updated_at": "2026-08-18T15:40:00Z",
        }
        revoked = {
            **approved,
            "id": 5264800001,
            "body": approved["body"].replace("Decision: APPROVED", "Decision: REVOKED"),
            "created_at": "2026-08-18T15:50:00Z",
            "updated_at": "2026-08-18T15:50:00Z",
        }
        first = verify._select_owner_final_head_attestation(
            contract,
            pull,
            [approved],
            commit=commit,
            final_head=final_head,
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "latest owner attestation is not a pre-merge approval",
        ):
            verify._revalidate_owner_final_head_decision(
                contract,
                pull,
                first,
                [approved, revoked],
            )

    def test_independent_review_query_is_bounded_and_commit_scoped(self) -> None:
        commit = "b" * 40
        final_head = "c" * 40
        pulls = [
            self._owner_pull(
                pull_request=153,
                final_head=final_head,
                merge_commit=commit,
            )
        ]
        reviews = [
            {
                "id": 9,
                "state": "APPROVED",
                "user": {"login": "IndependentHuman", "type": "User"},
                "commit_id": final_head,
                "submitted_at": "2026-08-07T02:59:59Z",
            }
        ]
        owner_attestation = {
            "id": 5264800000,
            "body": (
                "Owner final-head attestation for PR #153\n\n"
                "Decision: APPROVED\n"
                f"Final head: {final_head}\n"
                "Review-thread snapshot SHA-256: "
                f"{self.EMPTY_REVIEW_THREAD_SNAPSHOT_SHA256}\n"
                "Exception: https://github.com/TommyKammy/Shirokuma/"
                "issues/63#issuecomment-5324238100"
            ),
            "user": {"login": "TommyKammy", "type": "User"},
            "author_association": "OWNER",
            "created_at": "2026-08-18T15:50:00Z",
            "updated_at": "2026-08-18T15:50:00Z",
        }
        thread_payload = self._owner_review_thread_payload(final_head=final_head)

        class Response:
            status = 200

            def __init__(self, payload: object) -> None:
                self.payload = json.dumps(payload).encode()

            def __enter__(self) -> Response:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, limit: int) -> bytes:
                self.assert_limit = limit
                return self.payload

        stdout = io.StringIO()
        contract_path = ROOT / verify.CONTRACT_PATH
        original_load_json = verify._load_json
        active_contract, active_exception = self._active_owner_contract_fixture()

        def load_json(path: Path) -> dict[str, object]:
            if path == contract_path:
                return active_contract
            return original_load_json(path)

        with (
            mock.patch.object(verify, "_load_json", side_effect=load_json),
            mock.patch.object(
                verify,
                "EXPECTED_OWNER_ONLY_APPROVAL_EXCEPTION",
                active_exception,
            ),
            mock.patch.object(
                verify,
                "_verify_final_head_gates",
                return_value={
                    "pull_request_binding": {"pull_request": 153},
                    "final_head_ci": {"required_workflows": 4},
                    "review_threads": {"current_non_outdated": 0},
                },
            ) as final_head_gates,
            mock.patch.object(
                verify,
                "urlopen",
                side_effect=[
                    Response(pulls),
                    Response(pulls),
                    Response(reviews),
                    Response(reviews),
                    Response([owner_attestation]),
                    Response([owner_attestation]),
                    Response([owner_attestation]),
                    Response([owner_attestation]),
                    Response(reviews),
                    Response(reviews),
                    Response([owner_attestation]),
                    Response([owner_attestation]),
                    Response(reviews),
                    Response(reviews),
                ],
            ) as request,
            contextlib.redirect_stdout(stdout),
        ):
            verify.verify_independent_review(
                ROOT,
                repository="TommyKammy/Shirokuma",
                commit=commit,
                token="ephemeral-token",
            )
        receipt = json.loads(stdout.getvalue())
        self.assertEqual(153, receipt["pull_request"])
        self.assertEqual("IndependentHuman", receipt["reviewer"])
        self.assertEqual(final_head, receipt["reviewed_head"])
        self.assertEqual("2026-08-07T02:59:59Z", receipt["reviewed_at"])
        self.assertEqual("2026-08-18T16:00:00Z", receipt["merged_at"])
        self.assertEqual(
            verify.EXPECTED_PUBLICATION_ATTEMPT["before_sha"],
            receipt["base_sha"],
        )
        self.assertEqual(0, receipt["review_threads"]["current_non_outdated"])
        self.assertIs(
            receipt["independent_review_revalidated_after_final_api_gates"],
            True,
        )
        self.assertEqual(2, receipt["stable_final_authorization_passes"])
        self.assertEqual(2, final_head_gates.call_count)
        final_head_gates.assert_any_call(
            active_contract["publication"]["owner_only_approval_exception"],
            mock.ANY,
            association_policy=active_contract["publication"][
                "pending_review_repair"
            ]["pull_request_binding"],
            token="ephemeral-token",
        )
        self.assertEqual(14, request.call_count)
        requested_urls = [call.args[0].full_url for call in request.call_args_list]
        self.assertIn(f"/commits/{commit}/pulls?per_page=100", requested_urls[0])
        self.assertIn(
            "/pulls/153/reviews?per_page=100&page=1",
            requested_urls[2],
        )
        self.assertEqual(requested_urls[2], requested_urls[3])
        self.assertIn("/issues/153/comments", "\n".join(requested_urls))
        self.assertEqual(requested_urls[4], requested_urls[5])
        self.assertEqual(requested_urls[6], requested_urls[7])
        self.assertEqual(requested_urls[8], requested_urls[9])
        self.assertEqual(requested_urls[10], requested_urls[11])
        self.assertEqual(requested_urls[12], requested_urls[13])
        self.assertNotIn("/actions/runs", "\n".join(requested_urls))
        self.assertNotIn("api.github.com/graphql", "\n".join(requested_urls))

    def test_independent_review_revalidation_rejects_dismissal(self) -> None:
        contract = self._active_owner_contract()
        commit = "b" * 40
        final_head = "c" * 40
        pull = self._owner_pull(final_head=final_head, merge_commit=commit)
        approval = {
            "id": 9,
            "state": "APPROVED",
            "user": {"login": "IndependentHuman", "type": "User"},
            "commit_id": final_head,
            "submitted_at": "2026-08-18T15:50:00Z",
        }
        selection = verify._select_independent_review(
            contract,
            [pull],
            [approval],
            commit=commit,
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "no current human approval",
        ):
            verify._revalidate_independent_review_decision(
                contract,
                pull,
                selection,
                [{**approval, "state": "DISMISSED"}],
            )

        receipt = verify._revalidate_independent_review_decision(
            contract,
            pull,
            selection,
            [approval],
        )
        self.assertEqual(
            {"independent_review_revalidated_after_final_api_gates": True},
            receipt,
        )

    def test_composite_final_authorization_snapshot_rejects_drift(self) -> None:
        stable = {
            "pull_request_binding": {"pull_request": 153},
            "final_head_ci": {"run_ids": [1, 2, 3, 4]},
            "review_threads": {"snapshot_sha256": "a" * 64},
            "owner_decision_revalidated_after_final_api_gates": True,
        }
        self.assertEqual(
            stable,
            verify._stable_final_authorization_snapshot([stable, stable]),
        )
        changed = copy.deepcopy(stable)
        changed["final_head_ci"]["run_ids"] = [1, 2, 3, 5]
        with self.assertRaisesRegex(
            verify.ContractError,
            "final authorization snapshot is unstable",
        ):
            verify._stable_final_authorization_snapshot([stable, changed])

    def test_github_review_api_paginates_to_bounded_exhaustion(self) -> None:
        review_url = (
            "https://api.github.com/repos/TommyKammy/Shirokuma/"
            "pulls/143/reviews"
        )
        altered_policy = copy.deepcopy(verify.EXPECTED_INDEPENDENT_REVIEW["reviews"])
        altered_policy["maximum_page_bytes"] -= 1
        with self.assertRaisesRegex(
            verify.ContractError,
            "pull-review pagination policy differs",
        ):
            verify._github_api_paginated_reviews(
                review_url,
                token="ephemeral-token",
                policy=altered_policy,
            )
        first_page = [{"id": review_id} for review_id in range(1, 101)]
        terminal_page = [{"id": 101}]

        class Response:
            status = 200

            def __init__(self, payload: object) -> None:
                self.payload = json.dumps(payload).encode()

            def __enter__(self) -> Response:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, limit: int) -> bytes:
                return self.payload

        with mock.patch.object(
            verify,
            "urlopen",
            side_effect=[
                Response(first_page),
                Response(terminal_page),
                Response(first_page),
                Response(terminal_page),
            ],
        ) as request:
            reviews = verify._github_api_paginated_reviews(
                review_url,
                token="ephemeral-token",
            )
        self.assertEqual(101, len(reviews))
        self.assertEqual(4, request.call_count)
        requested_urls = [call.args[0].full_url for call in request.call_args_list]
        self.assertIn("per_page=100&page=1", requested_urls[0])
        self.assertIn("per_page=100&page=2", requested_urls[1])
        self.assertEqual(requested_urls[:2], requested_urls[2:])

        accepted_pages = [
            [
                {"id": page * 100 + offset + 1}
                for offset in range(100 if page < 9 else 99)
            ]
            for page in range(10)
        ]
        with mock.patch.object(
            verify,
            "urlopen",
            side_effect=[Response(page) for page in accepted_pages + accepted_pages],
        ) as request:
            reviews = verify._github_api_paginated_reviews(
                review_url,
                token="ephemeral-token",
            )
        self.assertEqual(verify.GITHUB_PULL_REVIEW_MAXIMUM_REVIEWS, len(reviews))
        self.assertEqual(20, request.call_count)

        full_pages = [
            Response(
                [
                    {"id": page * 100 + offset + 1}
                    for offset in range(100)
                ]
            )
            for page in range(10)
        ]
        with (
            mock.patch.object(
                verify,
                "urlopen",
                side_effect=full_pages,
            ) as request,
            self.assertRaisesRegex(
                verify.ContractError,
                "pull-review bound exceeded",
            ),
        ):
            verify._github_api_paginated_reviews(
                review_url,
                token="ephemeral-token",
            )
        self.assertEqual(10, request.call_count)

        stable_review = {
            "id": 1,
            "state": "APPROVED",
            "commit_id": "c" * 40,
            "submitted_at": "2026-08-07T02:59:59Z",
            "user": {"login": "IndependentHuman", "type": "User"},
        }
        dismissed_review = {**stable_review, "state": "DISMISSED"}
        with (
            mock.patch.object(
                verify,
                "urlopen",
                side_effect=[Response([stable_review]), Response([dismissed_review])],
            ),
            self.assertRaisesRegex(
                verify.ContractError,
                "pull-review snapshot is unstable",
            ),
        ):
            verify._github_api_paginated_reviews(
                review_url,
                token="ephemeral-token",
            )

        long_review = copy.deepcopy(stable_review)
        long_review["body"] = "x" * 12_000
        long_page = [
            {**copy.deepcopy(long_review), "id": review_id}
            for review_id in range(1, 101)
        ]
        terminal_review = {**stable_review, "id": 101}
        first_page_bytes = len(json.dumps(long_page).encode())
        self.assertGreater(first_page_bytes, verify.GITHUB_API_RESPONSE_BYTES)
        self.assertLess(
            first_page_bytes,
            verify.GITHUB_PULL_REVIEW_PAGE_RESPONSE_BYTES,
        )
        read_limits: list[int] = []

        class BoundedResponse(Response):
            def read(self, limit: int) -> bytes:
                read_limits.append(limit)
                return self.payload[:limit]

        with mock.patch.object(
            verify,
            "urlopen",
            side_effect=[
                BoundedResponse(long_page),
                BoundedResponse([terminal_review]),
                BoundedResponse(long_page),
                BoundedResponse([terminal_review]),
            ],
        ):
            reviews = verify._github_api_paginated_reviews(
                review_url,
                token="ephemeral-token",
            )
        self.assertEqual(101, len(reviews))
        self.assertEqual(
            [verify.GITHUB_PULL_REVIEW_PAGE_RESPONSE_BYTES + 1] * 4,
            read_limits,
        )

        oversized_review = copy.deepcopy(stable_review)
        oversized_review["body"] = (
            "x" * verify.GITHUB_PULL_REVIEW_PAGE_RESPONSE_BYTES
        )
        with (
            mock.patch.object(
                verify,
                "urlopen",
                return_value=BoundedResponse([oversized_review]),
            ),
            self.assertRaisesRegex(verify.ContractError, "response is too large"),
        ):
            verify._github_api_paginated_reviews(
                review_url,
                token="ephemeral-token",
            )

    def test_github_list_api_fails_closed_on_truncated_results(self) -> None:
        class Response:
            status = 200

            def __enter__(self) -> Response:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, limit: int) -> bytes:
                return json.dumps([{}] * 100).encode()

        with (
            mock.patch.object(verify, "urlopen", return_value=Response()),
            self.assertRaisesRegex(
                verify.ContractError,
                "GitHub API result may be truncated",
            ),
        ):
            verify._github_api_list(
                "https://api.github.com/repos/TommyKammy/Shirokuma/"
                "issues/153/comments?per_page=100",
                token="ephemeral-token",
            )

    def test_authorization_rejects_duplicate_json_keys(self) -> None:
        contract = (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        contract = contract.replace(
            '"authorization": {',
            '"authorization": {"automatic_renewal": true},\n'
            '  "authorization": {',
            1,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract_path = root / verify.CONTRACT_PATH
            contract_path.parent.mkdir(parents=True)
            contract_path.write_text(contract, encoding="utf-8")
            with self.assertRaisesRegex(
                verify.ContractError,
                "JSON: duplicate object key: authorization",
            ):
                verify.authorize_use(
                    root,
                    validation_point="before_source_fetch",
                    at=dt.datetime(
                        2026,
                        8,
                        21,
                        22,
                        43,
                        35,
                        tzinfo=dt.timezone.utc,
                    ),
                )

    def test_authorization_dates_are_bound_to_the_approval_record(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        contract["authorization"]["approved_at"] = "2027-01-01T00:00:00Z"
        contract["authorization"]["expires_at"] = "2027-01-31T00:00:00Z"
        with self.assertRaisesRegex(
            verify.ContractError,
            "AUTHORIZATION",
        ):
            verify._validate_authorization(
                contract,
                at=dt.datetime(
                    2027,
                    1,
                    15,
                    tzinfo=dt.timezone.utc,
                ),
            )

    def test_authorization_rejects_json_number_boolean_aliases(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        aliases = (
            (("automatic_renewal",), 0),
            (("review", "required_before_merge"), 1),
            (("maximum_duration_days",), 30.0),
        )
        for path, replacement in aliases:
            altered = copy.deepcopy(contract)
            target = altered["authorization"]
            for field in path[:-1]:
                target = target[field]
            target[path[-1]] = replacement
            with self.subTest(path=path, replacement=replacement):
                with self.assertRaisesRegex(
                    verify.ContractError,
                    "AUTHORIZATION",
                ):
                    verify._validate_authorization(altered, at=None)

    def test_retained_feasibility_expiry_is_an_explicit_freshness_check(
        self,
    ) -> None:
        class RejectWallClock(dt.datetime):
            @classmethod
            def now(cls, tz: dt.tzinfo | None = None) -> dt.datetime:
                raise AssertionError("static evidence audit read the wall clock")

        with mock.patch.object(verify.dt, "datetime", RejectWallClock):
            verify._validate_blocker_evidence(ROOT)
        verify._validate_blocker_evidence(
            ROOT,
            at=dt.datetime(
                2026,
                9,
                1,
                4,
                8,
                13,
                tzinfo=dt.timezone.utc,
            ),
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "BLOCKER_FEASIBILITY_EXPIRED",
        ):
            verify._validate_blocker_evidence(
                ROOT,
                at=dt.datetime(
                    2026,
                    9,
                    1,
                    4,
                    8,
                    14,
                    tzinfo=dt.timezone.utc,
                ),
            )

    def test_retained_blocker_evidence_is_hash_bound_and_recomputed(self) -> None:
        verify._validate_blocker_evidence(ROOT)
        paths = [
            verify.BLOCKER_CLASSIFICATION_PATH,
            *(
                Path(record["path"])
                for record in verify.EXPECTED_BLOCKER_INPUTS.values()
            ),
            Path(verify.EXPECTED_BLOCKER_CANDIDATE["patch_path"]),
            verify.BLOCKER_BASELINE_PATH,
            *verify.EXPECTED_BLOCKER_FEASIBILITY_FILES,
            verify.FEASIBILITY_RETAINED_VERIFIER_PATH,
        ]
        originals = {
            path: (ROOT / path).read_bytes()
            for path in paths
        }
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            for path, payload in originals.items():
                candidate = temporary_root / path
                candidate.parent.mkdir(parents=True, exist_ok=True)
                candidate.write_bytes(payload)

            report_path = (
                temporary_root
                / verify.EXPECTED_BLOCKER_INPUTS["raw_trivy_report"]["path"]
            )
            report_path.write_bytes(report_path.read_bytes() + b"\n")
            with self.assertRaisesRegex(
                verify.ContractError,
                "raw_trivy_report bytes or hash differ",
            ):
                verify._validate_blocker_evidence(temporary_root)
            report_path.write_bytes(
                originals[
                    Path(
                        verify.EXPECTED_BLOCKER_INPUTS[
                            "raw_trivy_report"
                        ]["path"]
                    )
                ]
            )

            classification_path = (
                temporary_root / verify.BLOCKER_CLASSIFICATION_PATH
            )
            classification = json.loads(
                classification_path.read_text(encoding="utf-8")
            )
            classification["summary"]["high_occurrences"] = 4
            classification_path.write_text(
                json.dumps(classification),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                verify.ContractError,
                "classification identity or summary differs",
            ):
                verify._validate_blocker_evidence(temporary_root)

            classification_path.write_bytes(
                originals[verify.BLOCKER_CLASSIFICATION_PATH]
            )
            receipt_path = (
                temporary_root / verify.BLOCKER_FEASIBILITY_RECEIPT_PATH
            )
            receipt_path.write_bytes(receipt_path.read_bytes() + b"\n")
            with self.assertRaisesRegex(
                verify.ContractError,
                "feasibility evidence differs",
            ):
                verify._validate_blocker_evidence(temporary_root)
            receipt_path.write_bytes(
                originals[verify.BLOCKER_FEASIBILITY_RECEIPT_PATH]
            )
            superseded_path = (
                temporary_root / verify.SUPERSEDED_FEASIBILITY_RECORD_PATH
            )
            superseded_path.write_bytes(
                superseded_path.read_bytes() + b"\n"
            )
            with self.assertRaisesRegex(
                verify.ContractError,
                "feasibility evidence differs",
            ):
                verify._validate_blocker_evidence(temporary_root)
            superseded_path.write_bytes(
                originals[verify.SUPERSEDED_FEASIBILITY_RECORD_PATH]
            )
            verifier_path = (
                temporary_root / verify.FEASIBILITY_RETAINED_VERIFIER_PATH
            )
            verifier_path.write_bytes(verifier_path.read_bytes() + b"\n")
            with self.assertRaisesRegex(
                verify.ContractError,
                "retained feasibility receipt differs",
            ):
                verify._validate_blocker_evidence(temporary_root)

        patch = ROOT / verify.EXPECTED_BLOCKER_CANDIDATE["patch_path"]
        verify._validate_zero_context_patch(patch, {"pom.xml"})
        with tempfile.TemporaryDirectory() as temporary:
            noncanonical = Path(temporary) / "candidate.patch"
            noncanonical.write_bytes(
                patch.read_bytes().split(b"\n", 1)[1]
            )
            with self.assertRaisesRegex(
                verify.ContractError,
                "patch paths differ",
            ):
                verify._validate_zero_context_patch(
                    noncanonical,
                    {"pom.xml"},
                )

    def test_retained_blocker_policy_fields_are_closed(self) -> None:
        paths = [
            verify.BLOCKER_CLASSIFICATION_PATH,
            *(
                Path(record["path"])
                for record in verify.EXPECTED_BLOCKER_INPUTS.values()
            ),
            Path(verify.EXPECTED_BLOCKER_CANDIDATE["patch_path"]),
            verify.BLOCKER_BASELINE_PATH,
            *verify.EXPECTED_BLOCKER_FEASIBILITY_FILES,
            verify.FEASIBILITY_RETAINED_VERIFIER_PATH,
        ]
        originals = {path: (ROOT / path).read_bytes() for path in paths}
        classification = json.loads(
            originals[verify.BLOCKER_CLASSIFICATION_PATH]
        )
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            for evidence_path, payload in originals.items():
                copied = temporary_root / evidence_path
                copied.parent.mkdir(parents=True, exist_ok=True)
                copied.write_bytes(payload)
            path = temporary_root / verify.BLOCKER_CLASSIFICATION_PATH
            altered = copy.deepcopy(classification)
            altered["classification"]["vulnerability_waiver_permitted"] = True
            path.write_text(json.dumps(altered), encoding="utf-8")
            with self.assertRaisesRegex(
                verify.ContractError,
                "owner-facing policy boundary differs",
            ):
                verify._validate_blocker_evidence(temporary_root)

            altered = copy.deepcopy(classification)
            altered["classification"][
                "vulnerability_waiver_permitted"
            ] = 0
            path.write_text(json.dumps(altered), encoding="utf-8")
            with self.assertRaisesRegex(
                verify.ContractError,
                "owner-facing policy boundary differs",
            ):
                verify._validate_blocker_evidence(temporary_root)

            altered = copy.deepcopy(classification)
            altered["findings"][0]["dependency_sources"] = []
            path.write_text(json.dumps(altered), encoding="utf-8")
            with self.assertRaisesRegex(
                verify.ContractError,
                "finding policy classification differs",
            ):
                verify._validate_blocker_evidence(temporary_root)

            altered = copy.deepcopy(classification)
            altered["focused_feasibility"]["validation"][
                "authorization_use_permitted"
            ] = True
            path.write_text(json.dumps(altered), encoding="utf-8")
            with self.assertRaisesRegex(
                verify.ContractError,
                "feasibility boundary differs",
            ):
                verify._validate_blocker_evidence(temporary_root)

            altered = copy.deepcopy(classification)
            altered["focused_feasibility"]["validation"][
                "authorization_use_permitted"
            ] = 0
            path.write_text(json.dumps(altered), encoding="utf-8")
            with self.assertRaisesRegex(
                verify.ContractError,
                "feasibility boundary differs",
            ):
                verify._validate_blocker_evidence(temporary_root)

            altered = copy.deepcopy(classification)
            altered["focused_feasibility"]["validation"][
                "revalidation_required_before_authorization"
            ] = False
            path.write_text(json.dumps(altered), encoding="utf-8")
            with self.assertRaisesRegex(
                verify.ContractError,
                "feasibility boundary differs",
            ):
                verify._validate_blocker_evidence(temporary_root)

    def test_retained_blocker_evidence_binds_snapshot_closure(self) -> None:
        paths = [
            verify.BLOCKER_CLASSIFICATION_PATH,
            *(
                Path(record["path"])
                for record in verify.EXPECTED_BLOCKER_INPUTS.values()
            ),
            Path(verify.EXPECTED_BLOCKER_CANDIDATE["patch_path"]),
            verify.BLOCKER_BASELINE_PATH,
            *verify.EXPECTED_BLOCKER_FEASIBILITY_FILES,
            verify.FEASIBILITY_RETAINED_VERIFIER_PATH,
        ]
        originals = {path: (ROOT / path).read_bytes() for path in paths}

        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            for path, payload in originals.items():
                copied = temporary_root / path
                copied.parent.mkdir(parents=True, exist_ok=True)
                copied.write_bytes(payload)

            rootfs_record = verify.EXPECTED_BLOCKER_INPUTS["raw_rootfs_sbom"]
            rootfs_path = temporary_root / rootfs_record["path"]
            rootfs = json.loads(rootfs_path.read_text(encoding="utf-8"))
            rootfs["components"][0]["purl"] += ".different-snapshot"
            rootfs_path.write_text(json.dumps(rootfs), encoding="utf-8")
            rootfs_payload = rootfs_path.read_bytes()
            rootfs_identity = {
                "sha256": hashlib.sha256(rootfs_payload).hexdigest(),
                "bytes": len(rootfs_payload),
            }
            classification_path = (
                temporary_root / verify.BLOCKER_CLASSIFICATION_PATH
            )
            classification = json.loads(
                classification_path.read_text(encoding="utf-8")
            )
            classification["inputs"]["raw_rootfs_sbom"].update(
                rootfs_identity
            )
            classification_path.write_text(
                json.dumps(classification),
                encoding="utf-8",
            )
            with mock.patch.dict(rootfs_record, rootfs_identity):
                with self.assertRaisesRegex(
                    verify.ContractError,
                    "exact rootfs component set",
                ):
                    verify._validate_blocker_evidence(temporary_root)

    def test_retained_blocker_candidate_is_bound_to_postimage(self) -> None:
        paths = [
            verify.BLOCKER_CLASSIFICATION_PATH,
            *(
                Path(record["path"])
                for record in verify.EXPECTED_BLOCKER_INPUTS.values()
            ),
            Path(verify.EXPECTED_BLOCKER_CANDIDATE["patch_path"]),
            verify.BLOCKER_BASELINE_PATH,
            *verify.EXPECTED_BLOCKER_FEASIBILITY_FILES,
            verify.FEASIBILITY_RETAINED_VERIFIER_PATH,
        ]
        originals = {path: (ROOT / path).read_bytes() for path in paths}

        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            for path, payload in originals.items():
                copied = temporary_root / path
                copied.parent.mkdir(parents=True, exist_ok=True)
                copied.write_bytes(payload)

            patch_path = (
                temporary_root
                / verify.EXPECTED_BLOCKER_CANDIDATE["patch_path"]
            )
            patch_payload = patch_path.read_bytes().replace(
                b"2.4.1",
                b"2.4.2",
                1,
            )
            patch_path.write_bytes(patch_payload)
            patch_identity = {
                "patch_sha256": hashlib.sha256(patch_payload).hexdigest(),
                "patch_bytes": len(patch_payload),
            }
            classification_path = (
                temporary_root / verify.BLOCKER_CLASSIFICATION_PATH
            )
            classification = json.loads(
                classification_path.read_text(encoding="utf-8")
            )
            classification["focused_feasibility"]["candidate"].update(
                patch_identity
            )
            classification_path.write_text(
                json.dumps(classification),
                encoding="utf-8",
            )
            with mock.patch.dict(
                verify.EXPECTED_BLOCKER_CANDIDATE,
                patch_identity,
            ):
                with self.assertRaisesRegex(
                    verify.ContractError,
                    "candidate patch postimage differs",
                ):
                    verify._validate_blocker_evidence(temporary_root)

    def test_publication_status_is_blocked_and_records_must_agree(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        admission = json.loads(
            (ROOT / verify.ADMISSION_PATH).read_text(encoding="utf-8")
        )

        self.assertEqual(
            "blocked",
            verify.publication_status(contract, admission),
        )

        altered = json.loads(json.dumps(contract))
        altered["publication"]["permitted"] = True
        with self.assertRaisesRegex(
            verify.ContractError,
            "publication permission records disagree",
        ):
            verify.publication_status(altered, admission)

        for alias in (0, 1):
            aliased_contract = copy.deepcopy(contract)
            aliased_admission = copy.deepcopy(admission)
            aliased_contract["lifecycle"][
                "publication_workflow_permitted"
            ] = alias
            aliased_contract["publication"]["permitted"] = alias
            aliased_admission["repository_state"][
                "publication_workflow_permitted"
            ] = alias
            with self.subTest(alias=alias):
                with self.assertRaisesRegex(
                    verify.ContractError,
                    "publication permission records disagree",
                ):
                    verify.publication_status(
                        aliased_contract,
                        aliased_admission,
                    )

    def test_exact_build_plugin_remediation_authorization_is_bound(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        admission = json.loads(
            (ROOT / verify.ADMISSION_PATH).read_text(encoding="utf-8")
        )

        verify._validate_build_plugin_remediation_contract(
            ROOT,
            contract,
            at=verify._parse_time("2026-08-18T06:00:00Z"),
        )
        self.assertEqual(
            verify.EXPECTED_ADMISSION_BUILD_PLUGIN_REMEDIATION_AUTHORIZATION,
            admission["build_plugin_remediation_authorization"],
        )

        altered = copy.deepcopy(contract)
        altered["source"]["build_plugin_remediation"]["patch"]["sha256"] = (
            "0" * 64
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "exact Maven build-plugin remediation differs",
        ):
            verify._validate_build_plugin_remediation_contract(
                ROOT,
                altered,
                at=None,
            )

        with self.assertRaisesRegex(
            verify.ContractError,
            "BUILD_PLUGIN_REMEDIATION_EXPIRED",
        ):
            verify._validate_build_plugin_remediation_contract(
                ROOT,
                contract,
                at=verify._parse_time("2026-09-17T02:15:58Z"),
            )

    def test_workflow_retains_the_blocked_publication_noop(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        workflow = (ROOT / verify.WORKFLOW_PATH).read_text(encoding="utf-8")
        marker = (
            "Trino dependency publication is blocked pending "
            "new explicit owner authorization"
        )
        self.assertEqual(1, workflow.count(marker))

        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_REQUIRED",
        ):
            verify._validate_workflow(
                contract,
                workflow.replace(marker, "publication unexpectedly active", 1),
            )

    def test_maven_sbom_generation_audits_the_exact_scanned_repository(
        self,
    ) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        workflow = (ROOT / verify.WORKFLOW_PATH).read_text(encoding="utf-8")
        marker = (
            '            --repository "${RUNNER_TEMP}/'
            'maven-repository-a" \\\n'
        )
        self.assertEqual(1, workflow.count(marker))
        altered = workflow.replace(
            marker,
            '            --repository "${RUNNER_TEMP}/'
            'maven-repository-b" \\\n',
            1,
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_MAVEN_SCAN",
        ):
            verify._validate_workflow(contract, altered)

    def test_maven_scan_failure_diagnostics_are_exact_and_non_admitting(
        self,
    ) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        workflow = (ROOT / verify.WORKFLOW_PATH).read_text(encoding="utf-8")
        self.assertEqual(
            1,
            workflow.count(verify.EXPECTED_MAVEN_SCAN_REPORT_BLOCK),
        )
        self.assertEqual(
            1,
            workflow.count(verify.EXPECTED_MAVEN_FAILURE_DIAGNOSTIC_BLOCK),
        )
        self.assertEqual(
            1,
            workflow.count(verify.EXPECTED_CANDIDATE_HIDDEN_UPLOAD_BLOCK),
        )
        self.assertEqual(3, workflow.count("include-hidden-files: true"))
        mutations = (
            (
                verify.EXPECTED_MAVEN_SCAN_REPORT_BLOCK,
                "          exit-code: 0\n",
                "          exit-code: 1\n",
            ),
            (
                verify.EXPECTED_MAVEN_FAILURE_DIAGNOSTIC_BLOCK,
                "          steps.verify_maven_scan.outcome == 'failure'\n",
                "          always()\n",
            ),
            (
                verify.EXPECTED_MAVEN_FAILURE_DIAGNOSTIC_BLOCK,
                "          include-hidden-files: true\n",
                "          include-hidden-files: false\n",
            ),
            (
                verify.EXPECTED_MAVEN_FAILURE_DIAGNOSTIC_BLOCK,
                "            .trino-candidate/trino-maven-rootfs-483.cdx.json\n",
                "",
            ),
            (
                verify.EXPECTED_MAVEN_FAILURE_DIAGNOSTIC_BLOCK,
                "          retention-days: 14\n",
                "          retention-days: 30\n",
            ),
            (
                verify.EXPECTED_CANDIDATE_HIDDEN_UPLOAD_BLOCK,
                "          include-hidden-files: true\n",
                "",
            ),
        )
        for block, original, replacement in mutations:
            altered = workflow.replace(
                block,
                block.replace(original, replacement, 1),
                1,
            )
            with self.subTest(original=original):
                with self.assertRaisesRegex(
                    verify.ContractError,
                    "WORKFLOW_MAVEN_DIAGNOSTICS",
                ):
                    verify._validate_workflow(contract, altered)

    def test_bun_scan_failure_diagnostics_are_exact_and_non_admitting(
        self,
    ) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        workflow = (ROOT / verify.WORKFLOW_PATH).read_text(encoding="utf-8")
        stage = verify.EXPECTED_BUN_SCAN_STAGE_BLOCK
        gate = verify.EXPECTED_BUN_SCAN_GATE_BLOCK
        pair = verify.EXPECTED_BUN_FAILURE_DIAGNOSTIC_PAIR_BLOCK
        diagnostic = verify.EXPECTED_BUN_FAILURE_DIAGNOSTIC_BLOCK
        record = verify.EXPECTED_RECORD_TRIVY_CACHE_BLOCK
        for block in (stage, gate, pair, diagnostic):
            self.assertEqual(1, workflow.count(block))
        self.assertEqual(
            4,
            verify.EXPECTED_ACTIONS[
                "actions/upload-artifact@"
                "ea165f8d65b6e75b540449e92b4886f43607fa02"
            ],
        )
        self.assertEqual(
            1,
            workflow.count(verify.EXPECTED_BUN_DIAGNOSTIC_ARTIFACT_PREFIX),
        )
        self.assertLess(workflow.index(stage), workflow.index(gate))
        self.assertLess(workflow.index(gate), workflow.index(pair))
        self.assertLess(workflow.index(pair), workflow.index(diagnostic))
        self.assertLess(workflow.index(gate), workflow.index(diagnostic))
        self.assertLess(workflow.index(diagnostic), workflow.index(record))
        publish = workflow.split("\n  publish:\n", 1)[1]
        self.assertNotIn(
            verify.EXPECTED_BUN_DIAGNOSTIC_ARTIFACT_PREFIX,
            publish,
        )
        self.assertEqual(
            1,
            workflow.count(verify.EXPECTED_CANDIDATE_DOWNLOAD_BLOCK),
        )

        diagnostic_mutations = (
            (
                "          failure() &&\n",
                "          always() &&\n",
            ),
            (
                "          steps.lifecycle.outputs.active == 'true' &&\n",
                "          success() &&\n",
            ),
            (
                "          steps.verify_bun_scan.outcome == 'failure' &&\n",
                "          steps.verify_bun_scan.outcome != 'cancelled' &&\n",
            ),
            (
                "          steps.verify_bun_diagnostic_pair.outcome == 'success'\n",
                "          steps.verify_bun_diagnostic_pair.outcome != 'cancelled'\n",
            ),
            (
                "          include-hidden-files: true\n",
                "          include-hidden-files: false\n",
            ),
            (
                "            .trino-candidate/trino-bun-dependencies-483.cdx.json\n",
                "",
            ),
            (
                "            .trino-candidate/trivy-bun-vulnerability.json\n",
                "",
            ),
            (
                "          if-no-files-found: error\n",
                "          if-no-files-found: warn\n",
            ),
            (
                "          retention-days: 14\n",
                "          retention-days: 30\n",
            ),
        )
        for original, replacement in diagnostic_mutations:
            altered = workflow.replace(
                diagnostic,
                diagnostic.replace(original, replacement, 1),
                1,
            )
            with self.subTest(original=original):
                with self.assertRaisesRegex(
                    verify.ContractError,
                    "WORKFLOW_BUN_DIAGNOSTICS",
                ):
                    verify._validate_workflow(contract, altered)

        pair_mutations = (
            (
                '            if [[ ! -s "${path}" ]]; then\n',
                '            if [[ ! -e "${path}" ]]; then\n',
            ),
            (
                "              exit 1\n",
                "              continue\n",
            ),
            (
                "            \"trino-bun-dependencies-483.cdx.json\"\n",
                "",
            ),
            (
                "            \"trivy-bun-vulnerability.json\"\n",
                "",
            ),
        )
        for original, replacement in pair_mutations:
            altered = workflow.replace(
                pair,
                pair.replace(original, replacement, 1),
                1,
            )
            with self.subTest(pair_original=original):
                with self.assertRaisesRegex(
                    verify.ContractError,
                    "WORKFLOW_BUN_DIAGNOSTICS",
                ):
                    verify._validate_workflow(contract, altered)

        changed_id = workflow.replace(
            gate,
            gate.replace(
                "        id: verify_bun_scan\n",
                "        id: verify_bun_report\n",
                1,
            ),
            1,
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_BUN_DIAGNOSTICS",
        ):
            verify._validate_workflow(contract, changed_id)

        changed_stage_id = workflow.replace(
            stage,
            stage.replace(
                "        id: stage_bun_scan_input\n",
                "        id: stage_bun_report_input\n",
                1,
            ),
            1,
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_BUN_DIAGNOSTICS",
        ):
            verify._validate_workflow(contract, changed_stage_id)

        for block in (gate,):
            altered_condition = workflow.replace(
                block,
                block.replace(
                    "          steps.stage_bun_scan_input.outcome == 'success'\n",
                    "          success()\n",
                    1,
                ),
                1,
            )
            with self.subTest(stage_condition=block.splitlines()[0]):
                with self.assertRaisesRegex(
                    verify.ContractError,
                    "WORKFLOW_BUN_DIAGNOSTICS",
                ):
                    verify._validate_workflow(contract, altered_condition)

        changed_action = workflow.replace(
            diagnostic,
            diagnostic.replace(
                "actions/upload-artifact@"
                "ea165f8d65b6e75b540449e92b4886f43607fa02",
                "actions/upload-artifact@" + "0" * 40,
                1,
            ),
            1,
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_ACTION",
        ):
            verify._validate_workflow(contract, changed_action)

        reordered = workflow.replace(
            pair + "\n" + diagnostic,
            diagnostic + "\n" + pair,
            1,
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_CLOSED_WORLD",
        ):
            verify._validate_workflow(contract, reordered)

        renamed = workflow.replace(
            "      - name: Retain failed Bun vulnerability diagnostics\n",
            "      - name: Retain Bun scan output\n",
            1,
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_CLOSED_WORLD",
        ):
            verify._validate_workflow(contract, renamed)

        diagnostic_as_publish_input = workflow.replace(
            verify.EXPECTED_CANDIDATE_DOWNLOAD_BLOCK,
            verify.EXPECTED_CANDIDATE_DOWNLOAD_BLOCK.replace(
                "          name: ${{ needs.validate.outputs.candidate_artifact_name }}\n",
                "          name: ${{ format('diagnostic-{0}', github.run_id) }}\n",
                1,
            ),
            1,
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_BUN_DIAGNOSTIC_ISOLATION",
        ):
            verify._validate_workflow(contract, diagnostic_as_publish_input)

    def test_descriptor_records_the_complete_reviewed_external_inputs(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        self.assertEqual(
            contract["dependency_resolution"]["external_inputs"],
            package.EXTERNAL_INPUTS,
        )
        self.assertEqual(
            [
                verify.EXPECTED_BUN_INPUT,
                verify.EXPECTED_PARQUET_SOURCE_REMEDIATION,
                verify.EXPECTED_SCM_METADATA_REMEDIATION,
            ],
            package.EXTERNAL_INPUTS,
        )
        self.assertEqual(
            verify.EXPECTED_BUN_PACKAGE_CACHE,
            contract["dependency_resolution"]["bun_package_cache"],
        )
        self.assertEqual(
            verify.EXPECTED_BUN_PACKAGE_CACHE["frozen_lockfiles"],
            bun_package.LOCKFILES,
        )
        self.assertEqual(
            verify.EXPECTED_BUN_PACKAGE_CACHE["cache_directory"],
            bun_package.BUN_CACHE_DIRECTORY,
        )
        self.assertEqual(
            verify.EXPECTED_BUN_PACKAGE_CACHE["registry"],
            bun_package.BUN_REGISTRY,
        )

    def test_exact_external_trino_build_extension_is_closed(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        self.assertEqual(
            verify.EXPECTED_TRINO_BUILD_EXTENSION,
            contract["dependency_resolution"]["reactor_outputs"][
                "exact_external_build_extension"
            ],
        )
        self.assertEqual(
            tuple(
                (
                    verify.EXPECTED_TRINO_BUILD_EXTENSION["group_id"].replace(
                        ".", "/"
                    )
                    + "/"
                    + verify.EXPECTED_TRINO_BUILD_EXTENSION["artifact_id"]
                    + "/"
                    + verify.EXPECTED_TRINO_BUILD_EXTENSION["version"]
                ).split("/")
            ),
            package.TRINO_BUILD_EXTENSION_PREFIX,
        )

    def test_exact_external_trino_maven_closure_is_closed(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        expected_paths = sorted(
            path.as_posix()
            for path in package.TRINO_EXTERNAL_REQUIRED_PATHS
        )
        self.assertEqual(
            verify.EXPECTED_TRINO_EXTERNAL_MAVEN_INPUTS,
            contract["dependency_resolution"]["reactor_outputs"][
                "exact_external_maven_inputs"
            ],
        )
        self.assertEqual(
            expected_paths,
            verify.EXPECTED_TRINO_EXTERNAL_MAVEN_INPUTS["required_paths"],
        )
        self.assertEqual(19, len(expected_paths))
        for required in (
            "io/trino/tempto/tempto-core/204/tempto-core-204.jar",
            "io/trino/tempto/tempto-root/204/tempto-root-204.pom",
            "io/trino/trino-maven-plugin/20/trino-maven-plugin-20.jar",
        ):
            with self.subTest(required=required):
                self.assertIn(required, expected_paths)
        for absent_prefix in (
            "io/trino/benchto/",
            "io/trino/hive/hive-apache-jdbc/",
            "io/trino/tempto/tempto-kafka/",
            "io/trino/tempto/tempto-ldap/",
            "io/trino/tempto/tempto-runner/",
            "io/trino/trino-root/",
            "io/trino/trino-spi/",
            "io/trino/trino-wasm-python/",
        ):
            with self.subTest(absent_prefix=absent_prefix):
                self.assertFalse(
                    any(
                        path.startswith(absent_prefix)
                        for path in expected_paths
                    )
                )

    def test_each_fresh_repository_uses_the_bounded_reactor_pruner(self) -> None:
        workflow = (ROOT / verify.WORKFLOW_PATH).read_text(encoding="utf-8")
        prune = (
            "python3 scripts/package_trino_maven_dependencies.py \\\n"
            '            prune-reactor-outputs --repository "${repository}"'
        )
        self.assertEqual(2, workflow.count(prune))
        self.assertNotIn('rm -rf "${repository}/io/trino"', workflow)

    def test_bun_trivy_scan_is_bound_to_both_reviewed_lockfiles(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        workflow = (ROOT / verify.WORKFLOW_PATH).read_text(encoding="utf-8")
        self.assertEqual(
            2,
            workflow.count(
                "scan-ref: ${{ runner.temp }}/trino-bun-scan-input"
            ),
        )
        self.assertNotIn(
            "scan-ref: ${{ runner.temp }}/bun-cache-a",
            workflow,
        )
        self.assertEqual(
            2,
            workflow.count('TRIVY_INCLUDE_DEV_DEPS: "true"'),
        )
        self.assertEqual(
            1,
            workflow.count(
                "TRIVY_CACHE_DIR: ${{ github.workspace }}/.cache/trivy"
            ),
        )
        self.assertEqual(3, workflow.count("list-all-pkgs: true"))
        self.assertNotIn("--vex", workflow)
        self.assertNotIn(".openvex.json", workflow)
        self.assertNotIn("trivy-bun-vulnerability-raw.json", workflow)
        self.assertIn("--report \\", workflow)
        altered = workflow.replace(
            "scan-ref: ${{ runner.temp }}/trino-bun-scan-input",
            "scan-ref: ${{ runner.temp }}/bun-cache-a",
            1,
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_BUN_SCAN",
        ):
            verify._validate_workflow(contract, altered)
        altered = workflow.replace(
            verify.EXPECTED_RECORD_TRIVY_CACHE_BLOCK,
            verify.EXPECTED_RECORD_TRIVY_CACHE_BLOCK.replace(
                "          TRIVY_CACHE_DIR: "
                "${{ github.workspace }}/.cache/trivy\n",
                "",
            ),
            1,
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_TRIVY_CACHE",
        ):
            verify._validate_workflow(contract, altered)
    def test_oras_push_uses_only_reviewed_candidate_basenames(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        workflow = (ROOT / verify.WORKFLOW_PATH).read_text(encoding="utf-8")
        self.assertEqual(1, workflow.count(verify.EXPECTED_ORAS_PUSH_BLOCK))
        self.assertNotIn("--disable-path-validation", workflow)
        altered = workflow.replace(
            verify.EXPECTED_ORAS_PUSH_BLOCK,
            verify.EXPECTED_ORAS_PUSH_BLOCK.replace(
                '            cd "${candidate}"',
                '            cd "${RUNNER_TEMP}"',
                1,
            ),
            1,
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_ORAS_PATHS",
        ):
            verify._validate_workflow(contract, altered)
        altered = workflow.replace(
            verify.EXPECTED_ORAS_PUSH_BLOCK,
            verify.EXPECTED_ORAS_PUSH_BLOCK.replace(
                '"maven-dependency-manifest.json:${DESCRIPTOR_MEDIA_TYPE}"',
                '"${candidate}/maven-dependency-manifest.json:'
                '${DESCRIPTOR_MEDIA_TYPE}"',
                1,
            ),
            1,
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_ORAS_PATHS",
        ):
            verify._validate_workflow(contract, altered)
        altered = workflow.replace(
            verify.EXPECTED_ORAS_PUSH_BLOCK,
            verify.EXPECTED_ORAS_PUSH_BLOCK.replace(
                '            oras push \\\n',
                '            oras push --disable-path-validation \\\n',
                1,
            ),
            1,
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_ORAS_PATHS",
        ):
            verify._validate_workflow(contract, altered)

    def test_oras_digest_requires_exact_lowercase_sha256_length(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        workflow = (ROOT / verify.WORKFLOW_PATH).read_text(encoding="utf-8")
        expected = verify.EXPECTED_ORAS_DIGEST_VALIDATION_BLOCK
        self.assertEqual(1, workflow.count(expected))
        for invalid in (
            expected.replace("{64}", "{62}", 1),
            expected.replace("[0-9a-f]", "[0-9A-Fa-f]", 1),
            expected.replace("^sha256:", "sha256:", 1),
            expected.replace("{64}$", "{64}", 1),
        ):
            with self.subTest(invalid=invalid):
                altered = workflow.replace(expected, invalid, 1)
                with self.assertRaisesRegex(
                    verify.ContractError,
                    "WORKFLOW_ORAS_DIGEST",
                ):
                    verify._validate_workflow(contract, altered)

    def test_slsa_attestation_signs_and_attaches_the_exact_v1_statement(
        self,
    ) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        workflow = (ROOT / verify.WORKFLOW_PATH).read_text(encoding="utf-8")
        expected = verify.EXPECTED_SLSA_STATEMENT_ATTESTATION_BLOCK
        self.assertEqual(1, workflow.count(expected))
        parquet_source = (
            verify.EXPECTED_PARQUET_SLSA_RESOLVED_DEPENDENCY_BLOCK
        )
        self.assertEqual(1, workflow.count(parquet_source))
        altered_source = workflow.replace(
            parquet_source,
            parquet_source.replace(
                "PARQUET_RC_TAG_OBJECT",
                "PARQUET_RELEASE_TAG_OBJECT",
                1,
            ),
            1,
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_SLSA_STATEMENT",
        ):
            verify._validate_workflow(contract, altered_source)
        for invalid, error in (
            (
                expected.replace("cosign attest-blob", "cosign attest", 1),
                "WORKFLOW_REQUIRED: cosign attest-blob",
            ),
            (
                expected.replace("--statement", "--predicate", 1),
                "WORKFLOW_SLSA_STATEMENT",
            ),
            (
                expected.replace(
                    '--hash "${PUBLISHED_DIGEST#sha256:}"',
                    '--hash "${PUBLISHED_DIGEST}"',
                    1,
                ),
                "WORKFLOW_SLSA_STATEMENT",
            ),
            (
                expected.replace("cosign attach attestation", "cosign verify", 1),
                "WORKFLOW_REQUIRED: cosign attach attestation",
            ),
        ):
            with self.subTest(invalid=invalid):
                altered = workflow.replace(expected, invalid, 1)
                with self.assertRaisesRegex(
                    verify.ContractError,
                    error,
                ):
                    verify._validate_workflow(contract, altered)

    def test_publication_requires_preexisting_public_package_visibility(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        workflow = (ROOT / verify.WORKFLOW_PATH).read_text(encoding="utf-8")
        public_reference = (
            "ghcr.io/tommykammy/shirokuma-trino-maven-dependencies@"
            "sha256:0394143034298f4c6606c288e8ef97154826978bf3aa97"
            "e1e952499f8af5075c"
        )
        self.assertEqual(
            {
                "required_visibility": "public",
                "sign_and_attest_before_anonymous_pull": True,
                "preexisting_public_reference": public_reference,
                "anonymous_preflight_before_registry_authentication": True,
                "owner_action_on_first_private_run": (
                    "not_applicable_preexisting_package_public"
                ),
                "same_run_visibility_mutation_permitted": False,
                "failed_attempt_admitted": False,
                "user_credential_fallback": False,
            },
            contract["snapshot"]["visibility_bootstrap"],
        )
        preflight = (
            "- name: Prove pre-existing public package visibility\n"
            "        env:\n"
            f"          PUBLIC_REFERENCE: {public_reference}\n"
        )
        self.assertIn(preflight, workflow)
        self.assertLess(
            workflow.index(preflight),
            workflow.index("echo \"${GHCR_TOKEN}\""),
        )

    def test_maven_policy_isolated_from_upstream_project_configuration(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        workflow = (ROOT / verify.WORKFLOW_PATH).read_text(encoding="utf-8")
        preimages = {
            entry["path"]: entry["sha256"]
            for entry in contract["source"]["preimages"]
        }
        self.assertEqual(
            {
                ".mvn/extensions.xml",
                ".mvn/maven.config",
                ".mvn/jvm.config",
                ".mvn/settings.xml",
            },
            set(preimages).intersection(
                {
                    ".mvn/extensions.xml",
                    ".mvn/maven.config",
                    ".mvn/jvm.config",
                    ".mvn/settings.xml",
                }
            ),
        )
        policy_files = {
            entry["path"]: entry["sha256"]
            for entry in contract["policy_files"]
        }
        self.assertEqual(
            preimages[".mvn/jvm.config"],
            policy_files[verify.JVM_CONFIG_PATH.as_posix()],
        )
        self.assertIn("--workdir /policy", workflow)
        self.assertIn("--file /workspace/pom.xml", workflow)
        self.assertNotIn("--workdir /workspace", workflow)
        self.assertEqual(
            (
                verify.EXPECTED_RESOLUTION_COMMAND,
                verify.EXPECTED_RESOLUTION_COMMAND,
            ),
            verify._resolution_maven_commands(workflow),
        )

    def test_each_resolver_command_requires_exact_iceberg_reactor(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        workflow = (ROOT / verify.WORKFLOW_PATH).read_text(encoding="utf-8")
        selection = (
            "              -pl "
            f"'{verify.EXPECTED_PROJECT_SELECTION}' \\\n"
        )
        self.assertEqual(3, workflow.count(selection))
        altered = (
            workflow.replace(
                selection,
                "              -pl ':trino-server,:trino-iceberg' \\\n",
                1,
            )
            + f"\n# misleading occurrence: {selection.strip()}\n"
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_RESOLUTION_COMMAND",
        ):
            verify._validate_workflow(contract, altered)

    def test_each_online_maven_command_ignores_transitive_repositories(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        workflow = (ROOT / verify.WORKFLOW_PATH).read_text(encoding="utf-8")
        option = "              --ignore-transitive-repositories \\\n"
        self.assertEqual(4, workflow.count(option))
        altered = (
            workflow.replace(option, "", 1)
            + "\n# misleading occurrence: --ignore-transitive-repositories\n"
        )
        self.assertEqual(6, altered.count("--ignore-transitive-repositories"))
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_SOURCE_REMEDIATION|WORKFLOW_RESOLUTION_COMMAND",
        ):
            verify._validate_workflow(contract, altered)

    def test_bun_input_is_staged_before_each_parquet_remediation(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        workflow = (ROOT / verify.WORKFLOW_PATH).read_text(encoding="utf-8")
        bun_command = "python3 scripts/prepare_trino_bun_input.py stage"
        parquet_command = (
            "python3 scripts/remediate_parquet_jackson.py stage-artifact"
        )
        self.assertEqual(2, workflow.count(bun_command))
        self.assertEqual(2, workflow.count(parquet_command))
        altered = (
            workflow.replace(bun_command, "__BUN_STAGE__", 1)
            .replace(parquet_command, bun_command, 1)
            .replace("__BUN_STAGE__", parquet_command, 1)
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_SOURCE_REMEDIATION",
        ):
            verify._validate_workflow(contract, altered)

    def test_pull_requests_are_static_only_while_publication_is_blocked(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        workflow = (ROOT / verify.WORKFLOW_PATH).read_text(encoding="utf-8")
        self.assertEqual(3, workflow.count(verify.EXPECTED_PR_SOURCE_CONDITION))
        self.assertEqual(1, workflow.count(verify.EXPECTED_PR_BUN_INPUT_BLOCK))
        for marker in verify.EXPECTED_PR_OVERLAY_BUILD_MARKERS:
            self.assertEqual(1, workflow.count(marker), marker)

        for marker in (
            '                echo "source_validation_active=false" >> '
            '"${GITHUB_OUTPUT}"\n',
            (
                '                echo "Pull requests perform static contract '
                'validation only while publication is blocked"\n'
            ),
            '          bun run --cwd "${modern}" typecheck\n',
            '          bun run --cwd "${legacy}" package:clean\n',
            verify.EXPECTED_PR_BUN_INPUT_BLOCK,
            verify.EXPECTED_PR_SOURCE_CONDITION,
        ):
            with self.subTest(marker=marker):
                altered = workflow.replace(marker, "", 1)
                with self.assertRaisesRegex(
                    verify.ContractError,
                    "WORKFLOW_PR_OVERLAY_VALIDATION",
                ):
                    verify._validate_workflow(contract, altered)

    def test_each_fresh_repository_requires_exact_bun_staging(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        workflow = (ROOT / verify.WORKFLOW_PATH).read_text(encoding="utf-8")
        self.assertEqual(2, workflow.count(verify.EXPECTED_BUN_STAGE_BLOCK))
        altered = workflow.replace(
            verify.EXPECTED_BUN_STAGE_BLOCK,
            verify.EXPECTED_BUN_STAGE_BLOCK.replace(
                "prepare_trino_bun_input.py download",
                "prepare_trino_bun_input.py verify",
            ),
            1,
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_BUN_INPUT",
        ):
            verify._validate_workflow(contract, altered)

    def test_bun_cache_is_frozen_reconstructed_and_read_only_offline(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        workflow = (ROOT / verify.WORKFLOW_PATH).read_text(encoding="utf-8")
        self.assertEqual(5, workflow.count("--env CI=true \\"))
        self.assertEqual(
            3,
            workflow.count(
                '--env BUN_INSTALL_CACHE_DIR="${BUN_CACHE_DIRECTORY}" \\'
            ),
        )
        self.assertEqual(
            3,
            workflow.count('--env BUN_CONFIG_REGISTRY="${BUN_REGISTRY}" \\'),
        )
        self.assertEqual(
            2,
            workflow.count(
                "python3 scripts/package_trino_bun_dependencies.py create"
            ),
        )
        self.assertIn(
            '--volume "${offline_bun_cache}:'
            '${BUN_CACHE_DIRECTORY}:ro" \\',
            workflow,
        )
        self.assertNotIn('${offline_source}/.bun-cache', workflow)
        self.assertEqual(
            1,
            workflow.count(
                "python3 scripts/package_trino_bun_dependencies.py "
                "verify-cache \\"
            ),
        )
        self.assertEqual(
            2,
            workflow.count(
                "python3 scripts/verify_trino_dependency_publisher.py \\\n"
                "            verify-bun-snapshot \\"
            ),
        )
        altered = workflow.replace("--env CI=true \\", "--env CI=false \\", 1)
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_BUN_CACHE",
        ):
            verify._validate_workflow(contract, altered)
        altered = workflow.replace(
            '--volume "${offline_bun_cache}:${BUN_CACHE_DIRECTORY}:ro" \\',
            (
                '--volume "${offline_source}/.bun-cache:'
                '${BUN_CACHE_DIRECTORY}:ro" \\'
            ),
            1,
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_BUN_CACHE",
        ):
            verify._validate_workflow(contract, altered)
        digest_line = (
            f"  BUN_ARCHIVE_SHA256: {verify.EXPECTED_BUN_INPUT['sha256']}"
        )
        self.assertEqual(1, workflow.count(digest_line))
        altered = workflow.replace(
            digest_line,
            f"{digest_line}\n# retained expected digest\n{digest_line}",
            1,
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_BUN_INPUT",
        ):
            verify._validate_workflow(contract, altered)

    def test_offline_workflow_command_is_bound_to_contract(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        workflow = (ROOT / verify.WORKFLOW_PATH).read_text(encoding="utf-8")
        self.assertEqual(
            contract["offline_rebuild"]["command"],
            verify._offline_maven_command(workflow),
        )
        self.assertIn(
            verify.EXPECTED_OFFLINE_RESOLVER_LOCK_ARGUMENT,
            contract["offline_rebuild"]["command"],
        )
        self.assertIn(
            verify.EXPECTED_OFFLINE_LIFECYCLE,
            contract["offline_rebuild"]["command"],
        )
        self.assertNotIn("-Dmaven.install.skip", workflow)
        self.assertEqual(
            verify.EXPECTED_OFFLINE_COMPILER_DEBUG,
            contract["offline_rebuild"]["compiler_debug_information"],
        )
        debug_property = (
            "              -Dmaven.compiler.debuglevel=source,lines \\\n"
        )
        self.assertEqual(1, workflow.count(debug_property))
        self.assertEqual(
            1,
            workflow.count(verify.EXPECTED_OFFLINE_DIGEST_COMMAND),
        )
        distribution_check = (
            '              verify-server-distribution --archive "${output}"'
        )
        self.assertEqual(1, workflow.count(distribution_check))
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_OFFLINE",
        ):
            verify._validate_workflow(
                contract,
                workflow.replace(distribution_check, "", 1),
            )
        filename_bound_digest = (
            '            sha256sum "${output}" \\\n'
            '              > "${candidate}/offline-output-${suffix}.sha256"'
        )
        altered = workflow.replace(
            verify.EXPECTED_OFFLINE_DIGEST_COMMAND,
            filename_bound_digest,
            1,
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_OFFLINE",
        ):
            verify._validate_workflow(contract, altered)
        offline_goal = (
            "              -am clean package -DskipTests \\\n"
            "              -Dmaven.source.skip=true -Dair.check.skip-all\n"
        )
        self.assertEqual(1, workflow.count(offline_goal))
        altered = workflow.replace(
            offline_goal,
            (
                "              -am clean install -DskipTests \\\n"
                "              -Dmaven.source.skip=true -Dair.check.skip-all\n"
            ),
            1,
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_OFFLINE_COMMAND",
        ):
            verify._validate_workflow(contract, altered)
        evidence_marker = '"compiler_debug_level": "source,lines"'
        self.assertEqual(1, workflow.count(evidence_marker))
        altered = workflow.replace(
            evidence_marker,
            '"compiler_debug_level": "source,lines,vars"',
            1,
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_OFFLINE",
        ):
            verify._validate_workflow(contract, altered)
        altered = workflow.replace(
            debug_property,
            "              -Dmaven.compiler.debuglevel=source,lines,vars \\\n",
            1,
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_OFFLINE_COMMAND",
        ):
            verify._validate_workflow(contract, altered)
        altered = workflow.replace(
            debug_property,
            "              -Dmaven.install.skip=true \\\n" + debug_property,
            1,
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_OFFLINE_COMMAND|WORKFLOW_OFFLINE_REPOSITORY",
        ):
            verify._validate_workflow(contract, altered)

    def test_all_maven_invocations_use_exact_repository_settings(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        workflow = (ROOT / verify.WORKFLOW_PATH).read_text(encoding="utf-8")
        self.assertEqual(
            workflow.count(
                "--env MAVEN_OPTS=-Duser.home=/tmp/maven-home"
            ),
            workflow.count("--entrypoint /usr/share/maven/bin/mvn"),
        )
        self.assertEqual(
            6,
            workflow.count(
                "--env MAVEN_OPTS=-Duser.home=/tmp/maven-home"
            ),
        )
        self.assertEqual(
            verify.EXPECTED_OFFLINE_REPOSITORY_SETTINGS,
            contract["offline_rebuild"]["repository_settings"],
        )
        self.assertEqual(
            verify.EXPECTED_OFFLINE_MAVEN_REPOSITORY,
            contract["offline_rebuild"]["maven_repository"],
        )
        self.assertEqual(
            1,
            workflow.count(
                verify.EXPECTED_OFFLINE_MAVEN_REPOSITORY_ASSIGNMENT
            ),
        )
        self.assertEqual(
            1,
            workflow.count(
                verify.EXPECTED_OFFLINE_MAVEN_REPOSITORY_EXTRACTION
            ),
        )
        self.assertEqual(
            1,
            workflow.count(verify.EXPECTED_OFFLINE_MAVEN_REPOSITORY_MOUNT),
        )
        self.assertNotIn('${offline_source}/.m2', workflow)
        self.assertEqual(5, workflow.count(verify.EXPECTED_SETTINGS_MOUNT))
        self.assertEqual(
            5,
            workflow.count(f"{verify.EXPECTED_SETTINGS_ARGUMENT} \\\n"),
        )
        self.assertIn(
            verify.EXPECTED_SETTINGS_ARGUMENT,
            contract["offline_rebuild"]["command"],
        )

        for value in (
            verify.EXPECTED_SETTINGS_MOUNT,
            verify.EXPECTED_SETTINGS_ARGUMENT,
        ):
            with self.subTest(value=value):
                altered = workflow.replace(value, "# removed exact setting", 1)
                with self.assertRaisesRegex(
                    verify.ContractError,
                    "WORKFLOW_SETTINGS",
                ):
                    verify._validate_workflow(contract, altered)

        offline_repository_mutations = (
            workflow.replace(
                verify.EXPECTED_OFFLINE_MAVEN_REPOSITORY_EXTRACTION,
                '--extract-root "${offline_source}/.m2/repository"',
                1,
            ),
            workflow.replace(
                verify.EXPECTED_OFFLINE_MAVEN_REPOSITORY_MOUNT,
                '--volume "${offline_repository}:/m2"',
                1,
            ),
        )
        for index, altered in enumerate(offline_repository_mutations):
            with self.subTest(mutation=index):
                with self.assertRaisesRegex(
                    verify.ContractError,
                    "WORKFLOW_OFFLINE_COMMAND|WORKFLOW_OFFLINE_REPOSITORY",
                ):
                    verify._validate_workflow(contract, altered)

        offline_repository_mount_line = (
            f"              {verify.EXPECTED_OFFLINE_MAVEN_REPOSITORY_MOUNT} "
            "\\\n"
        )
        relocated_offline_repository_mount = workflow.replace(
            offline_repository_mount_line,
            "",
            1,
        )
        relocated_offline_repository_mount += (
            "\n# relocated "
            f"{verify.EXPECTED_OFFLINE_MAVEN_REPOSITORY_MOUNT}\n"
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_OFFLINE_COMMAND|WORKFLOW_OFFLINE_REPOSITORY",
        ):
            verify._validate_workflow(
                contract,
                relocated_offline_repository_mount,
            )

        offline_repository_assignment_line = (
            "            "
            f"{verify.EXPECTED_OFFLINE_MAVEN_REPOSITORY_ASSIGNMENT}\n"
        )
        offline_repository_freshness_line = (
            '            test ! -e "${offline_repository}"\n'
        )
        for line in (
            offline_repository_assignment_line,
            offline_repository_freshness_line,
        ):
            with self.subTest(relocated_preparation=line):
                altered = workflow.replace(line, "", 1)
                altered += f"\n# relocated {line.strip()}\n"
                with self.assertRaisesRegex(
                    verify.ContractError,
                    "WORKFLOW_OFFLINE_COMMAND",
                ):
                    verify._validate_workflow(contract, altered)

        extraction_line = (
            f"              {verify.EXPECTED_OFFLINE_MAVEN_REPOSITORY_EXTRACTION}"
            "\n"
        )
        altered = workflow.replace(
            extraction_line,
            extraction_line
            + '            cp -R "${RUNNER_TEMP}/ambient-m2" '
            '"${offline_repository}"\n',
            1,
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_OFFLINE_COMMAND",
        ):
            verify._validate_workflow(contract, altered)

        for shadow_mount in (
            '              --volume "${RUNNER_TEMP}/shadow:/m2:rw" \\\n',
            (
                "              --mount "
                'type=bind,src="${RUNNER_TEMP}/shadow",dst=/m2 '
                "\\\n"
            ),
        ):
            with self.subTest(shadow_mount=shadow_mount):
                altered = workflow.replace(
                    offline_repository_mount_line,
                    offline_repository_mount_line + shadow_mount,
                    1,
                )
                with self.assertRaisesRegex(
                    verify.ContractError,
                    "WORKFLOW_OFFLINE_COMMAND",
                ):
                    verify._validate_workflow(contract, altered)

        altered_contract = json.loads(json.dumps(contract))
        altered_contract["offline_rebuild"]["maven_repository"][
            "mount"
        ] = "read-write"
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_OFFLINE_REPOSITORY",
        ):
            verify._validate_workflow(altered_contract, workflow)

        altered_contract = json.loads(json.dumps(contract))
        altered_contract["offline_rebuild"]["maven_repository"][
            "lifecycle"
        ] = "clean install"
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_OFFLINE_REPOSITORY",
        ):
            verify._validate_workflow(altered_contract, workflow)

        online_mount_line = (
            f"            {verify.EXPECTED_SETTINGS_MOUNT} \\\n"
        )
        relocated_online_mount = workflow.replace(online_mount_line, "", 1)
        relocated_online_mount += (
            f"\n# relocated {verify.EXPECTED_SETTINGS_MOUNT}\n"
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_SETTINGS|WORKFLOW_RESOLUTION_COMMAND",
        ):
            verify._validate_workflow(contract, relocated_online_mount)

        offline_mount_line = (
            f"              {verify.EXPECTED_SETTINGS_MOUNT} \\\n"
        )
        relocated_offline_mount = workflow.replace(offline_mount_line, "", 1)
        relocated_offline_mount += (
            f"\n# relocated {verify.EXPECTED_SETTINGS_MOUNT}\n"
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "WORKFLOW_SETTINGS|WORKFLOW_OFFLINE_COMMAND",
        ):
            verify._validate_workflow(contract, relocated_offline_mount)

    def test_builder_global_settings_allow_only_inert_defaults(
        self,
    ) -> None:
        namespace = "http://maven.apache.org/SETTINGS/1.2.0"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.xml"
            blocker_settings = (
                "  <mirrors>\n"
                "    <mirror>\n"
                "      <id>maven-default-http-blocker</id>\n"
                "      <mirrorOf>external:http:*</mirrorOf>\n"
                "      <name>Pseudo repository to mirror external "
                "repositories initially using HTTP.</name>\n"
                "      <url>http://0.0.0.0/</url>\n"
                "      <blocked>true</blocked>\n"
                "    </mirror>\n"
                "  </mirrors>\n"
            )
            safe_settings = (
                f'<settings xmlns="{namespace}">\n'
                "  <pluginGroups/>\n"
                "  <proxies/>\n"
                "  <servers/>\n"
                f"{blocker_settings}"
                "  <profiles/>\n"
                "</settings>\n"
            )
            path.write_text(safe_settings, encoding="utf-8")
            with mock.patch.object(
                verify,
                "MAX_BUILDER_SETTINGS_BYTES",
                path.stat().st_size - 1,
            ):
                with self.assertRaisesRegex(
                    verify.ContractError,
                    "BUILDER_SETTINGS",
                ):
                    verify.audit_builder_settings(path)
            verify.audit_builder_settings(path)
            workflow = (ROOT / verify.WORKFLOW_PATH).read_text(encoding="utf-8")
            self.assertNotIn('"global_settings_active_sections": []', workflow)
            self.assertIn(
                '"mirror:maven-default-http-blocker"',
                workflow,
            )
            self.assertIn(
                '"empty-standard-containers-plus-exact-default-http-blocker"',
                workflow,
            )

            unsafe_children = (
                "<activeProfiles/>",
                "<localRepository>/tmp/repository</localRepository>",
                "<offline>false</offline>",
                "<servers><server><id>other</id></server></servers>",
                "<mirrors>unexpected</mirrors>",
                "<profiles/><profiles/>",
                '<servers enabled="true"/>',
                '<foreign:servers xmlns:foreign="urn:foreign"/>',
                "unexpected<servers/>",
                "<servers/>unexpected",
                "<mirrors><mirror><id>other</id></mirror></mirrors>",
                "<mirrors><mirror>"
                "<id>maven-default-http-blocker</id>"
                "<mirrorOf>external:http:*</mirrorOf>"
                "<name>Pseudo repository to mirror external repositories "
                "initially using HTTP.</name>"
                "<url>http://0.0.0.0/</url>"
                "<blocked>false</blocked>"
                "</mirror></mirrors>",
            )
            for child in unsafe_children:
                path.write_text(
                    f'<settings xmlns="{namespace}">{child}</settings>\n',
                    encoding="utf-8",
                )
                with self.subTest(child=child):
                    with self.assertRaises(verify.ContractError):
                        verify.audit_builder_settings(path)

            for unsafe_settings, error in (
                (
                    safe_settings.replace(blocker_settings, "  <mirrors/>\n"),
                    "default HTTP blocker differs",
                ),
                (
                    safe_settings.replace(blocker_settings, ""),
                    "global settings container set differs",
                ),
            ):
                path.write_text(unsafe_settings, encoding="utf-8")
                with self.subTest(unsafe_settings=unsafe_settings):
                    with self.assertRaisesRegex(verify.ContractError, error):
                        verify.audit_builder_settings(path)

    def test_slsa_v1_payload_binds_evidence_and_exact_oci_subject(self) -> None:
        workflow = (ROOT / verify.WORKFLOW_PATH).read_text(encoding="utf-8")
        self.assertEqual(1, workflow.count("--type slsaprovenance1"))
        self.assertNotIn("--type slsaprovenance \\", workflow)
        for evidence in (
            "bun-dependency-manifest.json",
            "maven-dependency-manifest.json",
            "trino-bun-dependencies-483.tar.gz",
            "trino-maven-dependencies-483.tar.gz",
            "trino-bun-dependencies-483.cdx.json",
            "trino-maven-dependencies-483.cdx.json",
            "trivy-bun-vulnerability.json",
            "trivy-vulnerability.json",
            "trivy-version.json",
            "offline-build.json",
            "independent-reconstruction.json",
            "toolchain.json",
        ):
            with self.subTest(evidence=evidence):
                self.assertIn(f'"file:{evidence}"', workflow)
        self.assertIn('"https://in-toto.io/Statement/v1"', workflow)
        self.assertIn('"https://slsa.dev/provenance/v1"', workflow)
        self.assertEqual(1, workflow.count('"file:trivy-version.json"'))
        self.assertIn("statement == expected_statement", workflow)
        self.assertIn('"digest": {"sha256": digest}', workflow)

    def test_source_overlay_boundary_applies_before_postimage_check(
        self,
    ) -> None:
        preimage = b"reviewed preimage\n"
        postimage = b"reviewed postimage\n"
        boundary = {
            "apply_arguments": ["--whitespace=nowarn"],
            "patch": {"path": "candidate.patch"},
            "preimages": {"pom.xml": hashlib.sha256(preimage).hexdigest()},
            "postimages": {"pom.xml": hashlib.sha256(postimage).hexdigest()},
        }
        events: list[str] = []
        state = {"applied": False}

        def read_reviewed_file(path: Path, *, code: str) -> bytes:
            self.assertEqual(Path("/checkout/pom.xml"), path)
            events.append(code)
            return postimage if state["applied"] else preimage

        def run_git(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            self.assertEqual(Path("/checkout"), kwargs["cwd"])
            if "--check" in command:
                self.assertIs(state["applied"], False)
                events.append("git apply --check")
            else:
                self.assertIs(state["applied"], False)
                state["applied"] = True
                events.append("git apply")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with (
            mock.patch.object(
                verify,
                "_read_reviewed_regular_file",
                side_effect=read_reviewed_file,
            ),
            mock.patch.object(verify.subprocess, "run", side_effect=run_git),
        ):
            verify._apply_source_overlay_boundary(
                Path("/policy"),
                Path("/checkout"),
                boundary,
            )

        self.assertEqual(
            [
                "SOURCE_OVERLAY_PREIMAGE",
                "git apply --check",
                "git apply",
                "SOURCE_OVERLAY_POSTIMAGE",
            ],
            events,
        )

    def test_authorization_is_half_open_and_expires_fail_closed(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        verify._validate_authorization(
            contract,
            at=dt.datetime(2026, 8, 18, 5, 58, 59, tzinfo=dt.timezone.utc),
        )
        verify._validate_authorization(
            contract,
            at=dt.datetime(2026, 9, 17, 2, 15, 57, tzinfo=dt.timezone.utc),
        )
        for instant in (
            dt.datetime(2026, 8, 18, 5, 58, 58, tzinfo=dt.timezone.utc),
            dt.datetime(2026, 9, 17, 2, 15, 58, tzinfo=dt.timezone.utc),
        ):
            with self.subTest(instant=instant):
                with self.assertRaises(verify.ContractError):
                    verify._validate_authorization(contract, at=instant)

    def test_source_overlay_authorization_expires_fail_closed(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        verify._validate_source_overlay_contract(
            ROOT,
            contract,
            at=dt.datetime(2026, 9, 17, 2, 15, 57, tzinfo=dt.timezone.utc),
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "SOURCE_OVERLAY_EXPIRED",
        ):
            verify._validate_source_overlay_contract(
                ROOT,
                contract,
                at=dt.datetime(
                    2026,
                    9,
                    17,
                    2,
                    15,
                    58,
                    tzinfo=dt.timezone.utc,
                ),
            )

    def test_source_remediation_authorization_expires_fail_closed(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        verify._validate_source_remediation_contract(
            contract,
            at=dt.datetime(2026, 9, 17, 2, 15, 57, tzinfo=dt.timezone.utc),
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "SOURCE_REMEDIATION_EXPIRED",
        ):
            verify._validate_source_remediation_contract(
                contract,
                at=dt.datetime(
                    2026,
                    9,
                    17,
                    2,
                    15,
                    58,
                    tzinfo=dt.timezone.utc,
                ),
            )

    def test_distribution_remediation_is_exact_and_expires_fail_closed(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        verify._validate_distribution_remediation_contract(
            ROOT,
            contract,
            at=dt.datetime(2026, 9, 17, 2, 15, 57, tzinfo=dt.timezone.utc),
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "DISTRIBUTION_REMEDIATION_EXPIRED",
        ):
            verify._validate_distribution_remediation_contract(
                ROOT,
                contract,
                at=dt.datetime(
                    2026,
                    9,
                    17,
                    2,
                    15,
                    58,
                    tzinfo=dt.timezone.utc,
                ),
            )
        expanded = copy.deepcopy(contract)
        expanded["source"]["distribution_remediation"][
            "selected_projects"
        ].append(":trino-hive")
        with self.assertRaisesRegex(
            verify.ContractError,
            "DISTRIBUTION_REMEDIATION",
        ):
            verify._validate_distribution_remediation_contract(
                ROOT,
                expanded,
                at=None,
            )

    def test_distribution_patch_is_canonical_zero_context_diff(self) -> None:
        remediation = verify.EXPECTED_DISTRIBUTION_REMEDIATION
        patch = ROOT / remediation["patch"]["path"]
        permitted_paths = set(remediation["permitted_paths"])
        verify._validate_zero_context_patch(patch, permitted_paths)
        payload = patch.read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "candidate.patch"
            for suffix in (
                b" \n",
                b"\nChanges:\n",
                b"\n[full diff: rtk git diff --no-compact]\n",
            ):
                candidate.write_bytes(payload + suffix)
                with self.subTest(suffix=suffix):
                    with self.assertRaisesRegex(
                        verify.ContractError,
                        "DISTRIBUTION_REMEDIATION_PATCH",
                    ):
                        verify._validate_zero_context_patch(
                            candidate,
                            permitted_paths,
                        )

    def test_transfer_log_rejects_unknown_repositories_and_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "transfer.log"
            path.write_text(
                "\x1b[1;34m[INFO]\x1b[0m Downloading from central: "
                "https://repo.maven.apache.org/maven2/org/example/demo.pom\n"
                "[INFO] Downloaded from confluent: "
                "https://packages.confluent.io/maven/io/confluent/demo.jar\n",
                encoding="utf-8",
            )
            verify.audit_transfer_log(path)
            for unsafe in (
                "https://repo1.maven.org/maven2/demo.jar",
                "https://user:secret@repo.maven.apache.org/maven2/demo.jar",
                "http://repo.maven.apache.org/maven2/demo.jar",
            ):
                path.write_text(
                    f"[INFO] Downloading from other: {unsafe}\n",
                    encoding="utf-8",
                )
                with self.subTest(unsafe=unsafe):
                    with self.assertRaises(verify.ContractError):
                        verify.audit_transfer_log(path)

    def test_transfer_log_ignores_non_transfer_documentation_urls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "transfer.log"
            path.write_text(
                "[INFO] Downloading from central: "
                "https://repo.maven.apache.org/maven2/org/example/demo.pom\n"
                "[INFO] For more info visit "
                "https://webpack.js.org/guides/code-splitting/\n"
                "[WARNING] See "
                "https://rollupjs.org/configuration-options/#output-manualchunks\n",
                encoding="utf-8",
            )
            verify.audit_transfer_log(path)

    def test_transfer_log_rejects_missing_or_malformed_transfer_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "transfer.log"
            for malformed in (
                "[INFO] For more info visit "
                "https://webpack.js.org/guides/code-splitting/\n",
                "[INFO] Downloading from central:\n",
                "[INFO] Downloaded from central: \n",
            ):
                path.write_text(malformed, encoding="utf-8")
                with self.subTest(malformed=malformed):
                    with self.assertRaises(verify.ContractError):
                        verify.audit_transfer_log(path)

    def test_settings_have_only_closed_allowlisted_origin_mirrors(self) -> None:
        verify._validate_settings(ROOT)
        settings = (ROOT / verify.SETTINGS_PATH).read_text(encoding="utf-8")
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        self.assertEqual(
            verify.EXPECTED_SETTINGS_POLICY,
            contract["dependency_resolution"]["settings_policy"],
        )
        self.assertEqual(3, settings.count("<mirror>"))
        for mirror in verify.EXPECTED_REPOSITORY_MIRRORS:
            for name, value in mirror:
                self.assertIn(f"<{name}>{value}</{name}>", settings)
        for forbidden in (
            "<server>",
            "<proxy>",
            "<username>",
            "<password>",
            "${env.",
            "${settings.",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, settings)

    def test_settings_reject_allowlisted_origin_mirror_drift(self) -> None:
        settings_path = ROOT / verify.SETTINGS_PATH
        original = settings_path.read_text(encoding="utf-8")
        mutations = (
            (
                "<mirrorOf>central</mirrorOf>",
                "<mirrorOf>external:*</mirrorOf>",
            ),
            (
                "<url>https://packages.confluent.io/maven/</url>",
                "<url>https://oss.sonatype.org/content/repositories/snapshots/</url>",
            ),
            ("<mirrorOf>*</mirrorOf>", "<mirrorOf>*,!central</mirrorOf>"),
            (
                "</mirrors>",
                """
    <mirror>
      <id>unexpected-fallback</id>
      <mirrorOf>*</mirrorOf>
      <name>Unexpected fallback</name>
      <url>https://repo.maven.apache.org/maven2/</url>
    </mirror>
  </mirrors>""",
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / verify.SETTINGS_PATH
            target.parent.mkdir(parents=True)
            for old, new in mutations:
                target.write_text(original.replace(old, new, 1), encoding="utf-8")
                with self.subTest(old=old, new=new):
                    with self.assertRaises(verify.ContractError):
                        verify._validate_settings(root)


if __name__ == "__main__":
    unittest.main()
