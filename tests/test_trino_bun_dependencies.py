from __future__ import annotations

import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts import package_trino_bun_dependencies as package


class TrinoBunDependencySnapshotTest(unittest.TestCase):
    def _cache(self, root: Path) -> Path:
        cache = root / "cache"
        package_root = cache / "example@1.2.3@@@1"
        package_root.mkdir(parents=True)
        (package_root / "package.json").write_text(
            '{"name":"example","version":"1.2.3"}\n',
            encoding="utf-8",
        )
        executable = package_root / "bin.js"
        executable.write_text("#!/usr/bin/env bun\n", encoding="utf-8")
        executable.chmod(0o755)
        alias_root = cache / "example"
        alias_root.mkdir()
        os.symlink(
            "/bun-cache/example@1.2.3@@@1",
            alias_root / "1.2.3@@@1",
        )
        return cache

    def _create(
        self, root: Path, cache: Path | None = None, stem: str = "snapshot"
    ) -> tuple[Path, Path]:
        cache = cache or self._cache(root)
        descriptor = root / f"{stem}.json"
        archive = root / f"{stem}.tar.gz"
        package.create_snapshot(cache, descriptor, archive)
        return descriptor, archive

    def test_round_trip_extracts_regular_files_and_cache_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            descriptor, archive = self._create(root)
            extraction = root / "extracted"
            package.verify_snapshot(descriptor, archive, extraction)

            self.assertEqual(
                (extraction / "example@1.2.3@@@1/package.json").read_text(
                    encoding="utf-8"
                ),
                '{"name":"example","version":"1.2.3"}\n',
            )
            self.assertTrue(
                (extraction / "example@1.2.3@@@1/bin.js").stat().st_mode
                & 0o111
            )
            self.assertEqual(
                os.readlink(extraction / "example/1.2.3@@@1"),
                "/bun-cache/example@1.2.3@@@1",
            )

    def test_two_fresh_caches_produce_identical_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_root = root / "first"
            second_root = root / "second"
            first_root.mkdir()
            second_root.mkdir()
            first = self._create(first_root)
            second = self._create(second_root)
            self.assertEqual(first[0].read_bytes(), second[0].read_bytes())
            self.assertEqual(first[1].read_bytes(), second[1].read_bytes())

    def test_manifest_binds_lockfiles_registry_and_platform(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            descriptor, _ = self._create(root)
            manifest = json.loads(descriptor.read_text(encoding="utf-8"))
            self.assertEqual(manifest["lockfiles"], package.LOCKFILES)
            self.assertEqual(manifest["registry"], "https://registry.npmjs.org/")
            self.assertEqual(manifest["platform"], "linux/arm64")
            self.assertEqual(manifest["file_count"], 2)
            self.assertEqual(manifest["symlink_count"], 1)

    def test_rejects_hard_linked_cache_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = self._cache(root)
            source = cache / "example@1.2.3@@@1/package.json"
            os.link(source, source.with_name("hard-link.json"))
            with self.assertRaisesRegex(
                package.BunSnapshotError, "hard-linked Bun cache file"
            ):
                package.build_manifest(cache)

    def test_rejects_transient_cache_files(self) -> None:
        for filename in (
            ".lock",
            "download.part",
            "download.partial",
            "download.tmp",
            "download.download",
            "download.crdownload",
        ):
            with (
                self.subTest(filename=filename),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                cache = self._cache(root)
                (cache / "example@1.2.3@@@1" / filename).write_text(
                    "transient\n", encoding="utf-8"
                )
                with self.assertRaisesRegex(
                    package.BunSnapshotError, "transient Bun cache file"
                ):
                    package.build_manifest(cache)

    def test_rejects_relative_symlink_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = self._cache(root)
            link = cache / "example/1.2.3@@@1"
            link.unlink()
            os.symlink("../example@1.2.3@@@1", link)
            with self.assertRaisesRegex(
                package.BunSnapshotError, "outside /bun-cache"
            ):
                package.build_manifest(cache)

    def test_rejects_symlink_target_outside_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = self._cache(root)
            link = cache / "example/1.2.3@@@1"
            link.unlink()
            os.symlink("/etc", link)
            with self.assertRaisesRegex(
                package.BunSnapshotError, "outside /bun-cache"
            ):
                package.build_manifest(cache)

    def test_rejects_missing_symlink_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = self._cache(root)
            link = cache / "example/1.2.3@@@1"
            link.unlink()
            os.symlink("/bun-cache/missing@1.0.0@@@1", link)
            with self.assertRaisesRegex(
                package.BunSnapshotError, "target is missing"
            ):
                package.build_manifest(cache)

    def test_rejects_symlinked_cache_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = self._cache(root)
            linked = root / "linked-cache"
            linked.symlink_to(cache, target_is_directory=True)
            with self.assertRaisesRegex(
                package.BunSnapshotError, "root must be a real directory"
            ):
                package.build_manifest(linked)

    def test_rejects_manifest_identity_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            descriptor, archive = self._create(root)
            manifest = json.loads(descriptor.read_text(encoding="utf-8"))
            manifest["registry"] = "https://registry.example.invalid/"
            descriptor.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                package.BunSnapshotError, "identity or origin policy differs"
            ):
                package.verify_snapshot(descriptor, archive, None)

    def test_rejects_manifest_transient_file_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            descriptor, archive = self._create(root)
            manifest = json.loads(descriptor.read_text(encoding="utf-8"))
            file_record = next(
                record
                for record in manifest["entries"]
                if record["type"] == "file"
            )
            file_record["path"] = (
                "example@1.2.3@@@1/download.partial"
            )
            descriptor.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                package.BunSnapshotError, "transient Bun cache file"
            ):
                package.verify_snapshot(descriptor, archive, None)

    def test_verify_cache_rejects_post_build_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            descriptor, archive = self._create(root)
            extracted = root / "extracted"
            package.verify_snapshot(descriptor, archive, extracted)
            package.verify_cache(descriptor, extracted)
            target = next(
                path
                for path in extracted.rglob("*")
                if path.is_file() and not path.is_symlink()
            )
            target.write_bytes(target.read_bytes() + b"mutated")
            with self.assertRaisesRegex(
                package.BunSnapshotError,
                "differs from the reviewed manifest",
            ):
                package.verify_cache(descriptor, extracted)

    def test_rejects_archive_symlink_target_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            descriptor, archive = self._create(root)
            rewritten = root / "rewritten.tar.gz"
            with tarfile.open(archive, "r:gz") as source:
                members = source.getmembers()
                payloads = {
                    member.name: source.extractfile(member).read()
                    for member in members
                    if member.isfile()
                }
            with tarfile.open(rewritten, "w:gz", format=tarfile.GNU_FORMAT) as target:
                for member in members:
                    if member.issym():
                        member.linkname = "/bun-cache/other@9.9.9@@@1"
                        target.addfile(member)
                    else:
                        target.addfile(member, io.BytesIO(payloads[member.name]))
            with self.assertRaisesRegex(
                package.BunSnapshotError, "archive metadata is not canonical|symlink differs"
            ):
                package.verify_snapshot(descriptor, rewritten, None)

    def test_rejects_existing_extraction_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            descriptor, archive = self._create(root)
            extraction = root / "extracted"
            extraction.mkdir()
            with self.assertRaisesRegex(
                package.BunSnapshotError, "must not already exist"
            ):
                package.verify_snapshot(descriptor, archive, extraction)


if __name__ == "__main__":
    unittest.main()
