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
        overlay = json.loads(json.dumps(verify.EXPECTED_SOURCE_OVERLAY))
        overlay["vulnerability_assessment"]["raw_finding"]["target"] = (
            "webapp/bun.lock"
        )
        return overlay

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
        adjusted_finding: bool = False,
        missing_sentinel: bool = False,
        wrong_purl: bool = False,
        inventory_drift: bool = False,
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
        if inventory_drift:
            packages["legacy/bun.lock"][0]["Version"] = "changed"
        finding = {
            "VulnerabilityID": "GHSA-qwww-vcr4-c8h2",
            "PkgName": "react-router",
            "InstalledVersion": "7.18.1",
            "FixedVersion": "8.3.0",
            "Severity": "HIGH",
            "PkgIdentifier": {
                "PURL": (
                    "pkg:npm/react-router@7.18.2"
                    if wrong_purl
                    else "pkg:npm/react-router@7.18.1"
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
                                {"Vulnerabilities": [finding]}
                                if target == "webapp/bun.lock"
                                and (
                                    name.startswith("raw")
                                    or adjusted_finding
                                )
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
            raw = self._report(root, "raw.json")
            adjusted = self._report(root, "adjusted.json")
            with self._scan_contract():
                verify.verify_bun_scan(root, scan_input, raw, adjusted)

    def test_verify_bun_scan_rejects_missing_expected_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scan_input = root / "scan-input"
            scan_input.mkdir()
            self._write_lockfiles(scan_input)
            raw = self._report(root, "raw.json", missing_sentinel=True)
            adjusted = self._report(
                root,
                "adjusted.json",
                missing_sentinel=True,
            )
            with (
                self._scan_contract(),
                self.assertRaisesRegex(
                    verify.ContractError,
                    "required packages missing",
                ),
            ):
                verify.verify_bun_scan(root, scan_input, raw, adjusted)

    def test_verify_bun_scan_rejects_raw_finding_identity_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scan_input = root / "scan-input"
            scan_input.mkdir()
            self._write_lockfiles(scan_input)
            raw = self._report(root, "raw.json", wrong_purl=True)
            adjusted = self._report(root, "adjusted.json")
            with (
                self._scan_contract(),
                self.assertRaisesRegex(
                    verify.ContractError,
                    "BUN_SCAN_RAW_FINDING",
                ),
            ):
                verify.verify_bun_scan(root, scan_input, raw, adjusted)

    def test_verify_bun_scan_rejects_adjusted_finding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scan_input = root / "scan-input"
            scan_input.mkdir()
            self._write_lockfiles(scan_input)
            raw = self._report(root, "raw.json")
            adjusted = self._report(
                root,
                "adjusted.json",
                adjusted_finding=True,
            )
            with (
                self._scan_contract(),
                self.assertRaisesRegex(
                    verify.ContractError,
                    "BUN_SCAN_ADJUSTED_FINDING",
                ),
            ):
                verify.verify_bun_scan(root, scan_input, raw, adjusted)

    def test_verify_bun_scan_rejects_package_inventory_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scan_input = root / "scan-input"
            scan_input.mkdir()
            self._write_lockfiles(scan_input)
            raw = self._report(root, "raw.json")
            adjusted = self._report(
                root,
                "adjusted.json",
                inventory_drift=True,
            )
            with (
                self._scan_contract(),
                self.assertRaisesRegex(
                    verify.ContractError,
                    "BUN_SCAN_INVENTORY",
                ),
            ):
                verify.verify_bun_scan(root, scan_input, raw, adjusted)

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
    CLASS_FILE = bytes.fromhex(
        "CAFEBABE00000034000A"
        "01000141"
        "070001"
        "0100106A6176612F6C616E672F4F626A656374"
        "070003"
        "0100063C696E69743E"
        "010003282956"
        "010004436F6465"
        "0C00050006"
        "0A00040008"
        "002100020004000000000001"
        "0001000500060001000700000011"
        "00010001000000052AB70009B100000000"
        "0000"
    )
    INTERFACE_CLASS_FILE = bytes.fromhex(
        "CAFEBABE000000340005"
        "01000149"
        "070001"
        "0100106A6176612F6C616E672F4F626A656374"
        "070003"
        "0601000200040000000000000000"
    )
    CONCRETE_CLASS_WITHOUT_CODE = bytes.fromhex(
        "CAFEBABE000000340007"
        "01000141"
        "070001"
        "0100106A6176612F6C616E672F4F626A656374"
        "070003"
        "0100016D"
        "010003282956"
        "002100020004000000000001"
        "0001000500060000"
        "0000"
    )
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

    def test_class_file_structure_requires_complete_payload(self) -> None:
        self.assertTrue(verify._valid_class_file(self.CLASS_FILE))
        self.assertTrue(verify._valid_class_file(self.INTERFACE_CLASS_FILE))
        self.assertFalse(
            verify._valid_class_file(self.CONCRETE_CLASS_WITHOUT_CODE)
        )
        for length in range(len(self.CLASS_FILE)):
            with self.subTest(length=length):
                self.assertFalse(
                    verify._valid_class_file(self.CLASS_FILE[:length])
                )
        self.assertFalse(
            verify._valid_class_file(self.CLASS_FILE + b"\x00")
        )

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

    def test_maven_sbom_audits_only_known_rootfs_omissions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            alpha, beta = self.JARS
            sources = (
                "org/example/alpha/1.0/alpha-1.0-sources.jar"
            )
            tests = "org/example/alpha/1.0/alpha-1.0-tests.jar"
            repository, descriptor = self._repository_descriptor(
                root,
                {
                    alpha: {"org/example/Alpha.class": b"alpha"},
                    sources: {"org/example/Alpha.java": b"class Alpha {}"},
                    tests: {
                        "org/example/AlphaTest.class": self.CLASS_FILE,
                    },
                    beta: {
                        "org/example/Beta.class": self.CLASS_FILE,
                        (
                            "META-INF/maven/org.example/beta/"
                            "pom.properties"
                        ): (
                            b"artifactId=beta\n"
                            b"groupId=org.example\n"
                            b"version=2.0\n"
                        ),
                    },
                },
            )
            rootfs = self._sbom(root, (alpha,))
            document = json.loads(rootfs.read_text(encoding="utf-8"))
            nested_beta = f"{alpha}!/META-INF/lib/beta-2.0.jar"
            beta_ref = "urn:test:rootfs-deduplicated-beta"
            document["components"].append(
                {
                    "bom-ref": beta_ref,
                    "type": "library",
                    "name": "beta",
                    "purl": verify._maven_purl(beta),
                    "properties": [
                        {
                            "name": "aquasecurity:trivy:FilePath",
                            "value": nested_beta,
                        }
                    ],
                }
            )
            document["dependencies"][0]["dependsOn"].append(beta_ref)
            document["dependencies"].append(
                {"ref": beta_ref, "dependsOn": []}
            )
            rootfs.write_text(json.dumps(document), encoding="utf-8")
            generated = root / "generated.json"
            verify.generate_maven_sbom(
                descriptor,
                repository,
                rootfs,
                generated,
            )
            result = json.loads(generated.read_text(encoding="utf-8"))
            metadata = {
                prop["name"]: prop["value"]
                for prop in result["metadata"]["properties"]
            }
            self.assertEqual("1", metadata["shirokuma:rootfs-discovered-jars"])
            self.assertEqual("3", metadata["shirokuma:rootfs-audited-omissions"])
            self.assertEqual(
                "2",
                metadata["shirokuma:rootfs-audited-supplemental-jars"],
            )
            self.assertEqual(
                "1",
                metadata["shirokuma:rootfs-purl-deduplicated-jars"],
            )
            self.assertEqual(
                "0",
                metadata["shirokuma:manifest-coordinate-verified-jars"],
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
                    sources: "manifest-supplemental-sources",
                    tests: "manifest-supplemental-tests",
                    beta: "manifest-rootfs-purl-deduplicated",
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

    def test_maven_sbom_rejects_bytecode_free_purl_deduplication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            alpha, beta = self.JARS
            repository, descriptor = self._repository_descriptor(
                root,
                {
                    alpha: {"org/example/Alpha.class": b"alpha"},
                    beta: {
                        (
                            "META-INF/maven/org.example/beta/"
                            "pom.properties"
                        ): (
                            b"artifactId=beta\n"
                            b"groupId=org.example\n"
                            b"version=2.0\n"
                        ),
                    },
                },
            )
            rootfs = self._sbom(root, (alpha,))
            document = json.loads(rootfs.read_text(encoding="utf-8"))
            beta_ref = "urn:test:rootfs-deduplicated-beta"
            document["components"].append(
                {
                    "bom-ref": beta_ref,
                    "type": "library",
                    "name": "beta",
                    "purl": verify._maven_purl(beta),
                    "properties": [
                        {
                            "name": "aquasecurity:trivy:FilePath",
                            "value": (
                                f"{alpha}!/META-INF/lib/beta-2.0.jar"
                            ),
                        }
                    ],
                }
            )
            document["dependencies"][0]["dependsOn"].append(beta_ref)
            document["dependencies"].append(
                {"ref": beta_ref, "dependsOn": []}
            )
            rootfs.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(
                verify.ContractError,
                "contains no bytecode for coordinate verification",
            ):
                verify.generate_maven_sbom(
                    descriptor,
                    repository,
                    rootfs,
                    root / "generated.json",
                )

    def test_maven_sbom_audits_manifest_verified_trivy_omission(self) -> None:
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
                        "META-INF/": (
                            b"",
                            stat.S_IFDIR | 0o755,
                        ),
                        "dev/failsafe/Failsafe.class": self.CLASS_FILE,
                        (
                            "META-INF/maven/dev.failsafe/failsafe/"
                            "pom.properties"
                        ): (
                            b"artifactId=failsafe\n"
                            b"groupId=dev.failsafe\n"
                            b"version=3.3.2\n"
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
            self.assertEqual("1", metadata["shirokuma:rootfs-discovered-jars"])
            self.assertEqual("2", metadata["shirokuma:rootfs-audited-omissions"])
            self.assertEqual(
                "1",
                metadata["shirokuma:rootfs-audited-supplemental-jars"],
            )
            self.assertEqual(
                "0",
                metadata["shirokuma:rootfs-purl-deduplicated-jars"],
            )
            self.assertEqual(
                "1",
                metadata["shirokuma:manifest-coordinate-verified-jars"],
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
                    failsafe: "manifest-coordinate-verified",
                    failsafe_sources: "manifest-supplemental-sources",
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

    def test_maven_sbom_rejects_unsafe_rootfs_omission_evidence(self) -> None:
        cases = (
            (
                "org/example/alpha/1.0/alpha-1.0-javadoc.jar",
                {"org/example/Alpha.html": b"docs"},
                "unreviewed omitted classifier",
            ),
            (
                "org/example/alpha/1.0/alpha-1.0-sources.jar",
                {"org/example/Alpha.class": b"bytecode"},
                "not a source-only classifier",
            ),
            (
                "org/example/alpha/1.0/alpha-1.0-sources.jar",
                {
                    "org/example/Alpha.java": b"class Alpha {}",
                    "lib/nested.jar": b"nested",
                },
                "undiscovered nested JAR",
            ),
            (
                "org/example/alpha/1.0/alpha-1.0-sources.jar",
                {
                    "org/example/Alpha.java": b"class Alpha {}",
                    "lib/nested.JAR": b"nested",
                },
                "undiscovered nested JAR",
            ),
            (
                "org/example/beta/2.0/beta-2.0-sources.jar",
                {"org/example/Beta.java": b"class Beta {}"},
                "no rootfs-discovered or manifest-verified base coordinate",
            ),
            (
                "org/example/beta/2.0/beta-2.0.jar",
                {
                    "org/example/Beta.class": self.CLASS_FILE,
                    (
                        "META-INF/maven/org.example/beta/"
                        "pom.properties"
                    ): (
                        b"artifactId=beta\n"
                        b"groupId=org.example\n"
                        b"version=2.0\n"
                    ),
                    (
                        "META-INF/maven/org.example/other/"
                        "pom.properties"
                    ): (
                        b"artifactId=other\n"
                        b"groupId=org.example\n"
                        b"version=2.0\n"
                    ),
                },
                "does not contain exactly its Maven pom.properties",
            ),
            (
                "org/example/beta/2.0/beta-2.0.jar",
                {
                    "org/example/Beta.class": (
                        b"\xca\xfe\xba\xbe\x00\x00\x00\x34"
                    ),
                    (
                        "META-INF/maven/org.example/beta/"
                        "pom.properties"
                    ): (
                        b"artifactId=beta\n"
                        b"groupId=org.example\n"
                        b"version=2.0\n"
                    ),
                },
                "contains no bytecode for coordinate verification",
            ),
            (
                "org/example/beta/2.0/beta-2.0.jar",
                {
                    "org/example/Beta.class": self.CLASS_FILE,
                    (
                        "META-INF/maven/org.example/beta/"
                        "pom.properties"
                    ): (
                        (
                            b"artifactId=beta\n"
                            b"groupId=org.example\n"
                            b"version=2.0\n"
                        ),
                        stat.S_IFLNK | 0o777,
                    ),
                },
                "contains unsafe, encrypted, duplicate, or special entries",
            ),
            (
                "org/example/beta/2.0/beta-2.0.jar",
                {
                    "org/example/Beta.class": (
                        self.CLASS_FILE,
                        stat.S_IFIFO | 0o644,
                    ),
                    (
                        "META-INF/maven/org.example/beta/"
                        "pom.properties"
                    ): (
                        b"artifactId=beta\n"
                        b"groupId=org.example\n"
                        b"version=2.0\n"
                    ),
                },
                "contains unsafe, encrypted, duplicate, or special entries",
            ),
            (
                "org/example/beta/2.0/beta-2.0.jar",
                {
                    (
                        "META-INF/maven/org.example/beta/"
                        "pom.properties"
                    ): (
                        b"artifactId=beta\n"
                        b"groupId=org.example\n"
                        b"version=2.0\n"
                    ),
                },
                "contains no bytecode for coordinate verification",
            ),
        )
        for path, entries, error in cases:
            with self.subTest(path=path, error=error):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    alpha = self.JARS[0]
                    repository, descriptor = self._repository_descriptor(
                        root,
                        {
                            alpha: {"org/example/Alpha.class": b"alpha"},
                            path: entries,
                        },
                    )
                    with self.assertRaisesRegex(
                        verify.ContractError,
                        error,
                    ):
                        verify.generate_maven_sbom(
                            descriptor,
                            repository,
                            self._sbom(root, (alpha,)),
                            root / "generated.json",
                        )

    def test_maven_sbom_rejects_corrupt_class_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            alpha = self.JARS[0]
            beta = self.JARS[1]
            class_path = "org/example/Beta.class"
            repository, descriptor = self._repository_descriptor(
                root,
                {
                    alpha: {"org/example/Alpha.class": b"alpha"},
                    beta: {
                        class_path: (
                            self.CLASS_FILE + b"\x00" * 8192
                        ),
                        (
                            "META-INF/maven/org.example/beta/"
                            "pom.properties"
                        ): (
                            b"artifactId=beta\n"
                            b"groupId=org.example\n"
                            b"version=2.0\n"
                        ),
                    },
                },
            )
            candidate = repository / beta
            payload = bytearray(candidate.read_bytes())
            with zipfile.ZipFile(candidate) as archive:
                class_entry = archive.getinfo(class_path)
                data_offset = (
                    class_entry.header_offset
                    + len(class_entry.FileHeader())
                )
                payload[
                    data_offset + class_entry.compress_size - 1
                ] ^= 0xFF
            candidate.write_bytes(payload)
            document = json.loads(descriptor.read_text(encoding="utf-8"))
            beta_record = next(
                record
                for record in document["files"]
                if record["path"] == beta
            )
            beta_record["sha256"] = hashlib.sha256(payload).hexdigest()
            descriptor.write_text(
                json.dumps(document),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                verify.ContractError,
                "contains unreadable bytecode",
            ):
                verify.generate_maven_sbom(
                    descriptor,
                    repository,
                    self._sbom(root, (alpha,)),
                    root / "generated.json",
                )

    def test_maven_sbom_rejects_corrupt_source_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            alpha = self.JARS[0]
            sources = "org/example/alpha/1.0/alpha-1.0-sources.jar"
            source_path = "org/example/Alpha.java"
            repository, descriptor = self._repository_descriptor(
                root,
                {
                    alpha: {"org/example/Alpha.class": b"alpha"},
                    sources: {
                        source_path: (
                            b"class Alpha {}\n" + b"x" * 8192
                        ),
                    },
                },
            )
            candidate = repository / sources
            payload = bytearray(candidate.read_bytes())
            with zipfile.ZipFile(candidate) as archive:
                source_entry = archive.getinfo(source_path)
                data_offset = (
                    source_entry.header_offset
                    + len(source_entry.FileHeader())
                )
                payload[
                    data_offset + source_entry.compress_size - 1
                ] ^= 0xFF
            candidate.write_bytes(payload)
            document = json.loads(descriptor.read_text(encoding="utf-8"))
            source_record = next(
                record
                for record in document["files"]
                if record["path"] == sources
            )
            source_record["sha256"] = hashlib.sha256(payload).hexdigest()
            descriptor.write_text(
                json.dumps(document),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                verify.ContractError,
                "contains unreadable source",
            ):
                verify.generate_maven_sbom(
                    descriptor,
                    repository,
                    self._sbom(root, (alpha,)),
                    root / "generated.json",
                )

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
            bun_raw = self._report(root)
            bun_adjusted = self._report(root)
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
                bun_raw,
                bun_adjusted,
            )
            digest = "sha256:" + "a" * 64
            for path in (maven_report, bun_raw, bun_adjusted):
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
    def test_repository_contract_and_workflow_are_closed(self) -> None:
        verify.audit(ROOT)

    def test_publication_status_is_active_and_records_must_agree(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        admission = json.loads(
            (ROOT / verify.ADMISSION_PATH).read_text(encoding="utf-8")
        )

        self.assertEqual(
            "active",
            verify.publication_status(contract, admission),
        )

        altered = json.loads(json.dumps(contract))
        altered["publication"]["permitted"] = False
        with self.assertRaisesRegex(
            verify.ContractError,
            "publication permission records disagree",
        ):
            verify.publication_status(altered, admission)

    def test_workflow_retains_the_blocked_publication_noop(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        workflow = (ROOT / verify.WORKFLOW_PATH).read_text(encoding="utf-8")
        marker = (
            "Trino dependency publication is blocked pending "
            "owner-authorized source remediation"
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
        self.assertEqual(2, workflow.count("include-hidden-files: true"))
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
            3,
            workflow.count('TRIVY_INCLUDE_DEV_DEPS: "true"'),
        )
        self.assertEqual(
            2,
            workflow.count(
                "TRIVY_CACHE_DIR: ${{ github.workspace }}/.cache/trivy"
            ),
        )
        self.assertEqual(3, workflow.count("list-all-pkgs: true"))
        self.assertIn('--vex "${vex}"', workflow)
        self.assertEqual(1, workflow.count("--skip-db-update"))
        self.assertIn("trivy-bun-vulnerability-raw.json", workflow)
        self.assertIn("--adjusted-report \\", workflow)
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
        altered = workflow.replace(
            "          TRIVY_CACHE_DIR: "
            "${{ github.workspace }}/.cache/trivy\n",
            "",
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

    def test_first_private_publication_requires_owner_visibility_bootstrap(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        self.assertEqual(
            {
                "required_visibility": "public",
                "sign_and_attest_before_anonymous_pull": True,
                "owner_action_on_first_private_run": (
                    "set-package-public-and-rerun"
                ),
                "failed_attempt_admitted": False,
                "user_credential_fallback": False,
            },
            contract["snapshot"]["visibility_bootstrap"],
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
            "              -am clean install -DskipTests \\\n"
            "              -Dmaven.source.skip=true -Dair.check.skip-all\n"
        )
        self.assertEqual(1, workflow.count(offline_goal))
        altered = workflow.replace(
            offline_goal,
            (
                "              -am clean package -DskipTests \\\n"
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

    def test_all_maven_invocations_use_exact_repository_settings(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        workflow = (ROOT / verify.WORKFLOW_PATH).read_text(encoding="utf-8")
        self.assertEqual(
            verify.EXPECTED_OFFLINE_REPOSITORY_SETTINGS,
            contract["offline_rebuild"]["repository_settings"],
        )
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

    def test_authorization_is_half_open_and_expires_fail_closed(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        verify._validate_authorization(
            contract,
            at=dt.datetime(2026, 7, 22, 22, 43, 36, tzinfo=dt.timezone.utc),
        )
        verify._validate_authorization(
            contract,
            at=dt.datetime(2026, 8, 21, 22, 43, 35, tzinfo=dt.timezone.utc),
        )
        for instant in (
            dt.datetime(2026, 7, 22, 22, 43, 35, tzinfo=dt.timezone.utc),
            dt.datetime(2026, 8, 21, 22, 43, 36, tzinfo=dt.timezone.utc),
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
            at=dt.datetime(2026, 8, 21, 22, 43, 35, tzinfo=dt.timezone.utc),
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
                    8,
                    21,
                    22,
                    43,
                    36,
                    tzinfo=dt.timezone.utc,
                ),
            )

    def test_source_remediation_authorization_expires_fail_closed(self) -> None:
        contract = json.loads(
            (ROOT / verify.CONTRACT_PATH).read_text(encoding="utf-8")
        )
        verify._validate_source_remediation_contract(
            contract,
            at=dt.datetime(2026, 8, 21, 22, 43, 35, tzinfo=dt.timezone.utc),
        )
        with self.assertRaisesRegex(
            verify.ContractError,
            "SOURCE_REMEDIATION_EXPIRED",
        ):
            verify._validate_source_remediation_contract(
                contract,
                at=dt.datetime(
                    2026,
                    8,
                    21,
                    22,
                    43,
                    36,
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
            at=dt.datetime(2026, 8, 21, 22, 43, 35, tzinfo=dt.timezone.utc),
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
                    8,
                    21,
                    22,
                    43,
                    36,
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
