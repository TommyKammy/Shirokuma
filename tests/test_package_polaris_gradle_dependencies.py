from __future__ import annotations

import gzip
import hashlib
import importlib.util
import io
import json
import os
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from typing import Callable
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PACKAGER_PATH = ROOT / "scripts/package_polaris_gradle_dependencies.py"


def _load_packager() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "package_polaris_gradle_dependencies",
        PACKAGER_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {PACKAGER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


packager = _load_packager()


class PolarisGradleDependencySnapshotTests(unittest.TestCase):
    MODULE_CONTENT = b"authenticated-module\n"
    MODULE_CACHE_IDENTITY = hashlib.sha1(
        MODULE_CONTENT,
        usedforsecurity=False,
    ).hexdigest().lstrip("0")

    def _fixture(
        self,
        *,
        module_content: bytes = MODULE_CONTENT,
    ) -> tuple[Path, Path, Path, Path]:
        root = Path(tempfile.mkdtemp(prefix="polaris-gradle-snapshot-"))
        self.addCleanup(shutil.rmtree, root)
        cache = root / "gradle-home"
        module_cache_identity = hashlib.sha1(
            module_content,
            usedforsecurity=False,
        ).hexdigest().lstrip("0")
        module = (
            cache
            / "caches/modules-2/files-2.1"
            / "org.example/demo/1.2.3"
            / module_cache_identity
            / "demo-1.2.3.jar"
        )
        module.parent.mkdir(parents=True)
        module.write_bytes(module_content)
        metadata_cache = (
            cache
            / "caches/modules-2/metadata-2.107"
            / "descriptors/org.example/demo/1.2.3/descriptor.bin"
        )
        metadata_cache.parent.mkdir(parents=True)
        metadata_cache.write_bytes(b"gradle-metadata-cache\n")
        module_sha256 = hashlib.sha256(module.read_bytes()).hexdigest()
        verification = root / "verification-metadata.xml"
        verification.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<verification-metadata '
            'xmlns="https://schema.gradle.org/dependency-verification">\n'
            "  <configuration>\n"
            "    <verify-metadata>true</verify-metadata>\n"
            "    <verify-signatures>false</verify-signatures>\n"
            "  </configuration>\n"
            "  <components>\n"
            '    <component group="org.example" name="demo" version="1.2.3">\n'
            '      <artifact name="demo-1.2.3.jar">\n'
            f'        <sha256 value="{module_sha256}"/>\n'
            "      </artifact>\n"
            "    </component>\n"
            "  </components>\n"
            "</verification-metadata>\n",
            encoding="utf-8",
        )
        return (
            cache,
            verification,
            root / packager.ARCHIVE_FILENAME,
            root / "gradle-dependency-inputs.json",
        )

    def _create(self) -> tuple[Path, Path, Path, Path]:
        fixture = self._fixture()
        packager.create_snapshot(*fixture)
        return fixture

    def _rewrite_descriptor(
        self,
        descriptor: Path,
        mutate: Callable[[dict[str, object]], None],
    ) -> None:
        value = json.loads(descriptor.read_text(encoding="utf-8"))
        mutate(value)
        descriptor.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _assert_snapshot_error(
        self,
        detail: str,
        callable_: Callable[[], object],
    ) -> None:
        with self.assertRaises(packager.SnapshotError) as raised:
            callable_()
        self.assertIn(detail, str(raised.exception))

    def test_create_verify_and_extract_round_trip(self) -> None:
        _, verification, archive, descriptor = self._create()
        extraction = archive.parent / "extracted"

        value = packager.verify_snapshot(
            descriptor,
            verification,
            archive,
            extraction,
        )

        self.assertEqual(2, len(value["files"]))
        self.assertEqual(
            b"authenticated-module\n",
            (
                extraction
                / "caches/modules-2/files-2.1"
                / "org.example/demo/1.2.3"
                / self.MODULE_CACHE_IDENTITY
                / "demo-1.2.3.jar"
            ).read_bytes(),
        )

    def test_leading_zero_sha1_cache_identity_is_accepted(self) -> None:
        cases = (
            (b"gradle-leading-zero-38\n", 39),
            (b"gradle-leading-zero-43\n", 38),
        )
        for module_content, expected_length in cases:
            with self.subTest(expected_length=expected_length):
                cache_identity = hashlib.sha1(
                    module_content,
                    usedforsecurity=False,
                ).hexdigest().lstrip("0")
                self.assertEqual(expected_length, len(cache_identity))
                cache, verification, archive, descriptor = self._fixture(
                    module_content=module_content,
                )

                packager.create_snapshot(
                    cache,
                    verification,
                    archive,
                    descriptor,
                )
                value = packager.verify_snapshot(
                    descriptor,
                    verification,
                    archive,
                )
                module_record = next(
                    record
                    for record in value["files"]
                    if record["kind"] == "module-artifact"
                )

                self.assertEqual(
                    cache_identity,
                    Path(module_record["path"]).parts[-2],
                )

    def test_aopalliance_sha1_matches_gradle_cache_identity(self) -> None:
        full_sha1 = "0235ba8b489512805ac13a8f9ea77a1ca5ebe3e8"

        self.assertEqual(
            "235ba8b489512805ac13a8f9ea77a1ca5ebe3e8",
            packager._canonical_gradle_cache_sha1(full_sha1),
        )

    def test_archive_is_deterministic_across_mtime_and_mode_drift(self) -> None:
        cache, verification, archive, descriptor = self._create()
        first = archive.read_bytes()
        for path in cache.rglob("*"):
            if path.is_file():
                os.utime(path, ns=(1_000_000_000, 1_000_000_000))
                path.chmod(0o600)
        second_archive = archive.with_name("second.tar.gz")
        second_descriptor = descriptor.with_name("second.json")

        packager.create_snapshot(
            cache,
            verification,
            second_archive,
            second_descriptor,
        )

        self.assertEqual(first, second_archive.read_bytes())
        self.assertEqual(
            json.loads(descriptor.read_text(encoding="utf-8"))["files"],
            json.loads(second_descriptor.read_text(encoding="utf-8"))["files"],
        )

    def test_long_directory_pax_path_round_trip(self) -> None:
        cache, verification, archive, descriptor = self._fixture()
        long_metadata = (
            cache
            / "caches/modules-2/metadata-2.107/descriptors"
            / ("long-" + "a" * 80)
            / "descriptor.bin"
        )
        long_metadata.parent.mkdir(parents=True)
        long_metadata.write_bytes(b"long metadata cache path\n")

        packager.create_snapshot(
            cache,
            verification,
            archive,
            descriptor,
        )
        value = packager.verify_snapshot(
            descriptor,
            verification,
            archive,
        )

        self.assertIn(
            long_metadata.relative_to(cache).as_posix(),
            {record["path"] for record in value["files"]},
        )

    def test_module_hash_must_be_authenticated_by_gradle_metadata(self) -> None:
        cache, verification, archive, descriptor = self._fixture()
        verification.write_text(
            verification.read_text(encoding="utf-8").replace(
                hashlib.sha256(b"authenticated-module\n").hexdigest(),
                "0" * 64,
            ),
            encoding="utf-8",
        )

        self._assert_snapshot_error(
            "unretained artifacts",
            lambda: packager.create_snapshot(
                cache,
                verification,
                archive,
                descriptor,
            ),
        )

    def test_unverified_module_cache_residue_is_excluded(self) -> None:
        cache, verification, archive, descriptor = self._fixture()
        residue_content = b"unused repository probe metadata\n"
        residue_identity = hashlib.sha1(
            residue_content,
            usedforsecurity=False,
        ).hexdigest().lstrip("0")
        residue_relative = (
            Path("caches/modules-2/files-2.1")
            / "com.auth0/java-jwt/4.5.2"
            / residue_identity
            / "java-jwt-4.5.2.pom"
        )
        residue = cache / residue_relative
        residue.parent.mkdir(parents=True)
        residue.write_bytes(residue_content)

        packager.create_snapshot(
            cache,
            verification,
            archive,
            descriptor,
        )
        value = packager.verify_snapshot(
            descriptor,
            verification,
            archive,
            archive.parent / "extracted",
        )

        retained_paths = {record["path"] for record in value["files"]}
        self.assertNotIn(residue_relative.as_posix(), retained_paths)
        self.assertEqual(
            {
                "scanned_file_count": 3,
                "scanned_total_file_bytes": (
                    len(self.MODULE_CONTENT)
                    + len(b"gradle-metadata-cache\n")
                    + len(residue_content)
                ),
                "retained_file_count": 2,
                "retained_total_file_bytes": (
                    len(self.MODULE_CONTENT)
                    + len(b"gradle-metadata-cache\n")
                ),
                "excluded_module_file_count": 1,
                "excluded_module_file_bytes": len(residue_content),
            },
            value["projection"],
        )
        self.assertFalse(
            (archive.parent / "extracted" / residue_relative).exists()
        )
        with tarfile.open(archive, mode="r:gz") as snapshot:
            self.assertNotIn(residue_relative.as_posix(), snapshot.getnames())

    def test_unverified_residue_exclusion_is_extension_independent(self) -> None:
        cache, verification, archive, descriptor = self._fixture()
        residue_paths: list[Path] = []
        for index, suffix in enumerate((".pom", ".module", ".jar", ".zip")):
            content = f"unused-{suffix}-{index}\n".encode()
            identity = hashlib.sha1(
                content,
                usedforsecurity=False,
            ).hexdigest().lstrip("0")
            relative = (
                Path("caches/modules-2/files-2.1")
                / f"org.unused/module-{index}/1.0.0"
                / identity
                / f"module-{index}-1.0.0{suffix}"
            )
            path = cache / relative
            path.parent.mkdir(parents=True)
            path.write_bytes(content)
            residue_paths.append(relative)

        value = packager.create_snapshot(
            cache,
            verification,
            archive,
            descriptor,
        )

        retained_paths = {record["path"] for record in value["files"]}
        self.assertTrue(
            all(path.as_posix() not in retained_paths for path in residue_paths)
        )
        self.assertEqual(4, value["projection"]["excluded_module_file_count"])

    def test_wrong_variant_is_excluded_when_verified_variant_exists(self) -> None:
        cache, verification, archive, descriptor = self._fixture()
        wrong_content = b"alternate repository variant\n"
        wrong_identity = hashlib.sha1(
            wrong_content,
            usedforsecurity=False,
        ).hexdigest().lstrip("0")
        wrong_relative = (
            Path("caches/modules-2/files-2.1")
            / "org.example/demo/1.2.3"
            / wrong_identity
            / "demo-1.2.3.jar"
        )
        wrong = cache / wrong_relative
        wrong.parent.mkdir(parents=True)
        wrong.write_bytes(wrong_content)

        value = packager.create_snapshot(
            cache,
            verification,
            archive,
            descriptor,
        )

        retained_paths = {record["path"] for record in value["files"]}
        self.assertNotIn(wrong_relative.as_posix(), retained_paths)
        self.assertEqual(1, value["projection"]["excluded_module_file_count"])
        self.assertEqual(
            1,
            len(
                [
                    record
                    for record in value["files"]
                    if record["kind"] == "module-artifact"
                ]
            ),
        )

    def test_surplus_does_not_change_canonical_archive(self) -> None:
        cache, verification, archive, descriptor = self._fixture()
        packager.create_snapshot(cache, verification, archive, descriptor)
        original_archive = archive.read_bytes()
        residue_content = b"unused repository probe metadata\n"
        residue_identity = hashlib.sha1(
            residue_content,
            usedforsecurity=False,
        ).hexdigest().lstrip("0")
        residue = (
            cache
            / "caches/modules-2/files-2.1"
            / "com.auth0/java-jwt/4.5.2"
            / residue_identity
            / "java-jwt-4.5.2.pom"
        )
        residue.parent.mkdir(parents=True)
        residue.write_bytes(residue_content)

        second_archive = archive.with_name("second.tar.gz")
        second_descriptor = descriptor.with_name("second.json")
        value = packager.create_snapshot(
            cache,
            verification,
            second_archive,
            second_descriptor,
        )

        self.assertEqual(original_archive, second_archive.read_bytes())
        self.assertEqual(1, value["projection"]["excluded_module_file_count"])

    def test_unverified_residue_still_needs_canonical_identity(self) -> None:
        cache, verification, archive, descriptor = self._fixture()
        residue = (
            cache
            / "caches/modules-2/files-2.1"
            / "com.auth0/java-jwt/4.5.2"
            / ("1" * 40)
            / "java-jwt-4.5.2.pom"
        )
        residue.parent.mkdir(parents=True)
        residue.write_bytes(b"unused repository probe metadata\n")

        self._assert_snapshot_error(
            "cache identity differs from artifact SHA-1",
            lambda: packager.create_snapshot(
                cache,
                verification,
                archive,
                descriptor,
            ),
        )

    def test_unverified_residue_counts_toward_file_limit(self) -> None:
        cache, verification, archive, descriptor = self._fixture()
        residue_content = b"unused repository probe metadata\n"
        residue_identity = hashlib.sha1(
            residue_content,
            usedforsecurity=False,
        ).hexdigest().lstrip("0")
        residue = (
            cache
            / "caches/modules-2/files-2.1"
            / "com.auth0/java-jwt/4.5.2"
            / residue_identity
            / "java-jwt-4.5.2.pom"
        )
        residue.parent.mkdir(parents=True)
        residue.write_bytes(residue_content)

        with mock.patch.object(packager, "MAX_FILES", 2):
            self._assert_snapshot_error(
                "exceeds 2 files",
                lambda: packager.create_snapshot(
                    cache,
                    verification,
                    archive,
                    descriptor,
                ),
            )

    def test_unretained_verification_artifact_is_rejected(self) -> None:
        cache, verification, archive, descriptor = self._fixture()
        xml = verification.read_text(encoding="utf-8").replace(
            "  </components>",
            '    <component group="org.example" name="missing" version="1.0.0">\n'
            '      <artifact name="missing-1.0.0.pom">\n'
            f'        <sha256 value="{"1" * 64}"/>\n'
            "      </artifact>\n"
            "    </component>\n"
            "  </components>",
        )
        verification.write_text(xml, encoding="utf-8")

        self._assert_snapshot_error(
            "unretained artifacts",
            lambda: packager.create_snapshot(
                cache,
                verification,
                archive,
                descriptor,
            ),
        )

    def test_dynamic_dependency_version_is_rejected(self) -> None:
        cache, verification, archive, descriptor = self._fixture()
        verification.write_text(
            verification.read_text(encoding="utf-8").replace(
                'version="1.2.3"',
                'version="1.2.3-SNAPSHOT"',
            ),
            encoding="utf-8",
        )

        self._assert_snapshot_error(
            "dynamic Gradle dependency version",
            lambda: packager.create_snapshot(
                cache,
                verification,
                archive,
                descriptor,
            ),
        )

    def test_symlink_cache_entry_is_rejected(self) -> None:
        cache, verification, archive, descriptor = self._fixture()
        link = (
            cache
            / "caches/modules-2/metadata-2.107"
            / "descriptors/escape"
        )
        link.symlink_to(verification)

        self._assert_snapshot_error(
            "symlink file",
            lambda: packager.create_snapshot(
                cache,
                verification,
                archive,
                descriptor,
            ),
        )

    def test_symlinked_allowed_root_ancestor_is_rejected(self) -> None:
        cache, verification, archive, descriptor = self._fixture()
        caches = cache / "caches"
        external = cache.parent / "external-caches"
        caches.rename(external)
        caches.symlink_to(external, target_is_directory=True)

        self._assert_snapshot_error(
            "symlink directory",
            lambda: packager.create_snapshot(
                cache,
                verification,
                archive,
                descriptor,
            ),
        )

    def test_multiple_checksums_for_one_artifact_are_rejected(self) -> None:
        cache, verification, archive, descriptor = self._fixture()
        verification.write_text(
            verification.read_text(encoding="utf-8").replace(
                "      </artifact>",
                f'        <sha256 value="{"1" * 64}"/>\n'
                "      </artifact>",
            ),
            encoding="utf-8",
        )

        self._assert_snapshot_error(
            "exactly one SHA-256",
            lambda: packager.create_snapshot(
                cache,
                verification,
                archive,
                descriptor,
            ),
        )

    def test_module_cache_identity_must_match_artifact_sha1(
        self,
    ) -> None:
        for cache_identity in ("b" * 39, "b" * 40):
            with self.subTest(cache_identity=cache_identity):
                cache, verification, archive, descriptor = self._fixture()
                original = next(
                    (
                        cache
                        / "caches/modules-2/files-2.1"
                        / "org.example/demo/1.2.3"
                    ).rglob("demo-1.2.3.jar")
                )
                original.parent.rename(
                    original.parents[1] / cache_identity
                )

                self._assert_snapshot_error(
                    "cache identity differs from artifact SHA-1",
                    lambda: packager.create_snapshot(
                        cache,
                        verification,
                        archive,
                        descriptor,
                    ),
                )

    def test_padded_leading_zero_sha1_alias_is_rejected(self) -> None:
        module_content = b"gradle-leading-zero-38\n"
        cache, verification, archive, descriptor = self._fixture(
            module_content=module_content,
        )
        original = next(
            (
                cache
                / "caches/modules-2/files-2.1"
                / "org.example/demo/1.2.3"
            ).rglob("demo-1.2.3.jar")
        )
        original.parent.rename(
            original.parents[1] / ("0" + original.parent.name)
        )

        self._assert_snapshot_error(
            "invalid Gradle module cache identity",
            lambda: packager.create_snapshot(
                cache,
                verification,
                archive,
                descriptor,
            ),
        )

    def test_noncanonical_module_cache_identity_is_rejected(self) -> None:
        for cache_identity in (
            "0" + ("a" * 39),
            "0" * 40,
            "a" * 41,
            "a" * 64,
            "A" * 40,
            "g" * 40,
        ):
            with self.subTest(cache_identity=cache_identity):
                cache, verification, archive, descriptor = self._fixture()
                original = next(
                    (
                        cache
                        / "caches/modules-2/files-2.1"
                        / "org.example/demo/1.2.3"
                    ).rglob("demo-1.2.3.jar")
                )
                destination = original.parents[1] / cache_identity
                original.parent.rename(destination)

                self._assert_snapshot_error(
                    "invalid Gradle module cache identity",
                    lambda: packager.create_snapshot(
                        cache,
                        verification,
                        archive,
                        descriptor,
                    ),
                )

    def test_transient_gradle_lock_is_rejected(self) -> None:
        cache, verification, archive, descriptor = self._fixture()
        lock = (
            cache
            / "caches/modules-2/metadata-2.107"
            / "module-metadata.bin.lock"
        )
        lock.write_text("mutable\n", encoding="utf-8")

        self._assert_snapshot_error(
            "transient or credential-bearing",
            lambda: packager.create_snapshot(
                cache,
                verification,
                archive,
                descriptor,
            ),
        )

    def test_file_count_limit_fails_closed(self) -> None:
        cache, verification, archive, descriptor = self._fixture()

        with mock.patch.object(packager, "MAX_FILES", 1):
            self._assert_snapshot_error(
                "exceeds 1 files",
                lambda: packager.create_snapshot(
                    cache,
                    verification,
                    archive,
                    descriptor,
                ),
            )

    def test_directory_count_limit_fails_closed(self) -> None:
        cache, verification, archive, descriptor = self._fixture()

        with mock.patch.object(packager, "MAX_DIRECTORIES", 1):
            self._assert_snapshot_error(
                "directory count limit",
                lambda: packager.create_snapshot(
                    cache,
                    verification,
                    archive,
                    descriptor,
                ),
            )

    def test_path_component_depth_limit_fails_closed(self) -> None:
        self._assert_snapshot_error(
            "unsafe snapshot path",
            lambda: packager._safe_relative("/".join(["a"] * 33)),
        )

    def test_total_size_limit_is_checked_before_hashing(self) -> None:
        cache, verification, archive, descriptor = self._fixture()

        with (
            mock.patch.object(packager, "MAX_TOTAL_FILE_BYTES", 1),
            mock.patch.object(
                packager,
                "_cache_file_hashes",
                side_effect=AssertionError("hashing must not start"),
            ),
        ):
            self._assert_snapshot_error(
                "uncompressed size limit",
                lambda: packager.create_snapshot(
                    cache,
                    verification,
                    archive,
                    descriptor,
                ),
            )

    def test_compressed_size_limit_removes_partial_archive(self) -> None:
        cache, verification, archive, descriptor = self._fixture()

        with mock.patch.object(packager, "MAX_ARCHIVE_BYTES", 32):
            self._assert_snapshot_error(
                "compressed size limit",
                lambda: packager.create_snapshot(
                    cache,
                    verification,
                    archive,
                    descriptor,
                ),
            )

        self.assertFalse(archive.exists())
        self.assertEqual([], list(archive.parent.glob(f".{archive.name}.tmp-*")))

    def test_archive_byte_mutation_is_rejected(self) -> None:
        _, verification, archive, descriptor = self._create()
        archive.write_bytes(archive.read_bytes() + b"tampered")

        self._assert_snapshot_error(
            "archive differs",
            lambda: packager.verify_snapshot(
                descriptor,
                verification,
                archive,
            ),
        )

    def test_projection_counter_tamper_is_rejected(self) -> None:
        _, verification, archive, descriptor = self._create()

        def mutate(value: dict[str, object]) -> None:
            value["projection"]["excluded_module_file_count"] = 1  # type: ignore[index]

        self._rewrite_descriptor(descriptor, mutate)
        self._assert_snapshot_error(
            "count or size summary",
            lambda: packager.verify_snapshot(
                descriptor,
                verification,
                archive,
            ),
        )

    def test_archive_rebound_to_wrong_cache_identity_is_rejected(
        self,
    ) -> None:
        cache, verification, archive, descriptor = self._create()
        value = json.loads(descriptor.read_text(encoding="utf-8"))
        module_record = next(
            record
            for record in value["files"]
            if record["kind"] == "module-artifact"
        )
        original = cache / module_record["path"]
        wrong_identity = "b" * 40
        original.parent.rename(original.parents[1] / wrong_identity)
        module_record["path"] = module_record["path"].replace(
            self.MODULE_CACHE_IDENTITY,
            wrong_identity,
        )
        value["files"].sort(key=lambda record: record["path"])
        value["directory_count"] = len(
            packager._directory_names(value["files"])
        )

        packager._write_archive(
            cache,
            verification,
            value["files"],
            archive,
        )
        archive_bytes = archive.read_bytes()
        value["archive"]["sha256"] = hashlib.sha256(
            archive_bytes
        ).hexdigest()
        value["archive"]["size"] = len(archive_bytes)
        descriptor.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        self._assert_snapshot_error(
            "archive Gradle cache identity differs from artifact SHA-1",
            lambda: packager.verify_snapshot(
                descriptor,
                verification,
                archive,
            ),
        )

    def test_rebound_trailing_archive_payload_is_rejected(self) -> None:
        _, verification, archive, descriptor = self._create()
        archive.write_bytes(archive.read_bytes() + b"trailing-data")

        def mutate(value: dict[str, object]) -> None:
            value["archive"]["sha256"] = hashlib.sha256(  # type: ignore[index]
                archive.read_bytes()
            ).hexdigest()
            value["archive"]["size"] = archive.stat().st_size  # type: ignore[index]

        self._rewrite_descriptor(descriptor, mutate)
        self._assert_snapshot_error(
            "trailing or concatenated payload",
            lambda: packager.verify_snapshot(
                descriptor,
                verification,
                archive,
            ),
        )

    def test_noncanonical_gzip_envelope_is_rejected(self) -> None:
        _, verification, archive, descriptor = self._create()
        tar_payload = gzip.decompress(archive.read_bytes())
        with archive.open("wb") as raw:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                fileobj=raw,
                compresslevel=9,
                mtime=123,
            ) as compressed:
                compressed.write(tar_payload)

        def mutate(value: dict[str, object]) -> None:
            value["archive"]["sha256"] = hashlib.sha256(  # type: ignore[index]
                archive.read_bytes()
            ).hexdigest()
            value["archive"]["size"] = archive.stat().st_size  # type: ignore[index]

        self._rewrite_descriptor(descriptor, mutate)
        self._assert_snapshot_error(
            "noncanonical gzip envelope",
            lambda: packager.verify_snapshot(
                descriptor,
                verification,
                archive,
            ),
        )

    def test_oversized_pax_control_record_is_rejected(self) -> None:
        _, verification, archive, descriptor = self._create()
        value = json.loads(descriptor.read_text(encoding="utf-8"))
        file_record = value["files"][0]
        malformed = archive.with_name("oversized-pax.tar.gz")
        with malformed.open("wb") as raw:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                fileobj=raw,
                compresslevel=9,
                mtime=0,
            ) as compressed:
                with tarfile.open(
                    fileobj=compressed,
                    mode="w|",
                    format=tarfile.PAX_FORMAT,
                ) as bundle:
                    member = tarfile.TarInfo(file_record["path"])
                    member.size = file_record["size"]
                    member.mode = 0o644
                    member.uid = 0
                    member.gid = 0
                    member.mtime = 0
                    member.pax_headers = {"comment": "x" * 5000}
                    bundle.addfile(
                        member,
                        io.BytesIO(b"x" * file_record["size"]),
                    )

        def mutate(value: dict[str, object]) -> None:
            value["archive"]["sha256"] = hashlib.sha256(  # type: ignore[index]
                malformed.read_bytes()
            ).hexdigest()
            value["archive"]["size"] = malformed.stat().st_size  # type: ignore[index]

        self._rewrite_descriptor(descriptor, mutate)
        self._assert_snapshot_error(
            "PAX control record",
            lambda: packager.verify_snapshot(
                descriptor,
                verification,
                malformed,
            ),
        )

    def test_descriptor_path_traversal_is_rejected(self) -> None:
        _, verification, archive, descriptor = self._create()

        def mutate(value: dict[str, object]) -> None:
            value["files"][0]["path"] = "../escape"  # type: ignore[index]

        self._rewrite_descriptor(descriptor, mutate)
        self._assert_snapshot_error(
            "unsafe snapshot path",
            lambda: packager.verify_snapshot(
                descriptor,
                verification,
                archive,
            ),
        )

    def test_case_insensitive_descriptor_path_collision_is_rejected(
        self,
    ) -> None:
        _, verification, archive, descriptor = self._create()

        def mutate(value: dict[str, object]) -> None:
            duplicate = dict(value["files"][1])  # type: ignore[index]
            duplicate["path"] = str(duplicate["path"]).upper()
            value["files"].append(duplicate)  # type: ignore[index]

        self._rewrite_descriptor(descriptor, mutate)
        self._assert_snapshot_error(
            "case-insensitive descriptor path collision",
            lambda: packager.verify_snapshot(
                descriptor,
                verification,
                archive,
            ),
        )

    def test_duplicate_descriptor_coordinate_is_rejected(self) -> None:
        _, verification, _, descriptor = self._create()
        value = json.loads(descriptor.read_text(encoding="utf-8"))
        module = next(
            record
            for record in value["files"]
            if record["kind"] == "module-artifact"
        )
        duplicate = dict(module)
        duplicate["path"] = duplicate["path"].replace(
            self.MODULE_CACHE_IDENTITY,
            "b" * 40,
        )
        value["files"].append(duplicate)
        value["files"].sort(key=lambda record: record["path"])
        value["file_count"] = len(value["files"])
        value["directory_count"] = len(
            packager._directory_names(value["files"])
        )
        value["total_file_bytes"] += duplicate["size"]
        value["projection"]["scanned_file_count"] = len(value["files"])
        value["projection"]["retained_file_count"] = len(value["files"])
        value["projection"]["scanned_total_file_bytes"] = value[
            "total_file_bytes"
        ]
        value["projection"]["retained_total_file_bytes"] = value[
            "total_file_bytes"
        ]

        self._assert_snapshot_error(
            "multiple descriptor records",
            lambda: packager._validate_descriptor(value, verification),
        )

    def test_duplicate_descriptor_key_is_rejected(self) -> None:
        _, verification, archive, descriptor = self._create()
        text = descriptor.read_text(encoding="utf-8")
        descriptor.write_text(
            text.replace(
                '"schema_version": 1,',
                '"schema_version": 1,\n  "schema_version": 1,',
                1,
            ),
            encoding="utf-8",
        )

        self._assert_snapshot_error(
            "duplicate JSON key",
            lambda: packager.verify_snapshot(
                descriptor,
                verification,
                archive,
            ),
        )

    def test_noncanonical_archive_metadata_is_rejected(self) -> None:
        _, verification, archive, descriptor = self._create()
        value = json.loads(descriptor.read_text(encoding="utf-8"))
        file_record = value["files"][0]
        payload = b"x" * file_record["size"]
        malformed = archive.with_name("malformed.tar.gz")
        with malformed.open("wb") as raw:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                fileobj=raw,
                mtime=0,
            ) as compressed:
                with tarfile.open(fileobj=compressed, mode="w|") as bundle:
                    member = tarfile.TarInfo(file_record["path"])
                    member.size = len(payload)
                    member.mode = 0o644
                    member.uid = 501
                    member.gid = 20
                    member.mtime = 0
                    bundle.addfile(member, io.BytesIO(payload))
        malformed_sha256 = hashlib.sha256(malformed.read_bytes()).hexdigest()

        def mutate(value: dict[str, object]) -> None:
            value["archive"]["sha256"] = malformed_sha256  # type: ignore[index]
            value["archive"]["size"] = malformed.stat().st_size  # type: ignore[index]

        self._rewrite_descriptor(descriptor, mutate)
        self._assert_snapshot_error(
            "ownership or mtime",
            lambda: packager.verify_snapshot(
                descriptor,
                verification,
                malformed,
            ),
        )

    def test_nonempty_extraction_root_is_rejected(self) -> None:
        _, verification, archive, descriptor = self._create()
        extraction = archive.parent / "existing"
        extraction.mkdir()
        (extraction / "keep").write_text("do not overwrite\n", encoding="utf-8")

        self._assert_snapshot_error(
            "must be empty",
            lambda: packager.verify_snapshot(
                descriptor,
                verification,
                archive,
                extraction,
            ),
        )

    def test_invalid_inventory_does_not_modify_extraction_root(self) -> None:
        _, verification, archive, descriptor = self._create()
        malformed = archive.with_name("unexpected-member.tar.gz")
        with malformed.open("wb") as raw:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                fileobj=raw,
                compresslevel=9,
                mtime=0,
            ) as compressed:
                with tarfile.open(
                    fileobj=compressed,
                    mode="w|",
                    format=tarfile.PAX_FORMAT,
                ) as bundle:
                    member = tarfile.TarInfo("rogue")
                    member.type = tarfile.DIRTYPE
                    member.mode = 0o755
                    member.uid = 0
                    member.gid = 0
                    member.mtime = 0
                    bundle.addfile(member)

        def mutate(value: dict[str, object]) -> None:
            value["archive"]["sha256"] = hashlib.sha256(  # type: ignore[index]
                malformed.read_bytes()
            ).hexdigest()
            value["archive"]["size"] = malformed.stat().st_size  # type: ignore[index]

        self._rewrite_descriptor(descriptor, mutate)
        extraction = archive.parent / "must-remain-absent"
        self._assert_snapshot_error(
            "unexpected archive directory",
            lambda: packager.verify_snapshot(
                descriptor,
                verification,
                malformed,
                extraction,
            ),
        )
        self.assertFalse(extraction.exists())

    def test_gradle_verification_bypass_list_is_rejected(self) -> None:
        cache, verification, archive, descriptor = self._fixture()
        verification.write_text(
            verification.read_text(encoding="utf-8").replace(
                "  </configuration>",
                "    <trusted-artifacts/>\n"
                "  </configuration>",
            ),
            encoding="utf-8",
        )

        self._assert_snapshot_error(
            "bypass lists",
            lambda: packager.create_snapshot(
                cache,
                verification,
                archive,
                descriptor,
            ),
        )

    def test_duplicate_configuration_section_is_rejected(self) -> None:
        cache, verification, archive, descriptor = self._fixture()
        verification.write_text(
            verification.read_text(encoding="utf-8").replace(
                "  <components>",
                "  <configuration>\n"
                "    <verify-metadata>true</verify-metadata>\n"
                "    <verify-signatures>false</verify-signatures>\n"
                "    <trusted-artifacts/>\n"
                "  </configuration>\n"
                "  <components>",
            ),
            encoding="utf-8",
        )

        self._assert_snapshot_error(
            "exactly one configuration",
            lambda: packager.create_snapshot(
                cache,
                verification,
                archive,
                descriptor,
            ),
        )

    def test_metadata_size_limit_is_checked_before_hashing(self) -> None:
        _, verification, archive, descriptor = self._create()

        with (
            mock.patch.object(
                packager,
                "MAX_VERIFICATION_METADATA_BYTES",
                1,
            ),
            mock.patch.object(
                packager,
                "_sha256_file",
                side_effect=AssertionError("hashing must not start"),
            ),
        ):
            self._assert_snapshot_error(
                "verification metadata contract is invalid",
                lambda: packager.verify_snapshot(
                    descriptor,
                    verification,
                    archive,
                ),
            )


if __name__ == "__main__":
    unittest.main()
