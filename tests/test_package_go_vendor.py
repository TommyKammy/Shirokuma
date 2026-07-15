from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import lzma
import os
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "package_go_vendor", ROOT / "scripts/package_go_vendor.py"
)
assert SPEC is not None and SPEC.loader is not None
packager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packager)

SOURCE_COMMIT = "a" * 40
GO_IMAGE = "golang:1.25.12-alpine@sha256:" + "b" * 64
GO_VERSION = "1.25.12"


def h1(seed: str) -> str:
    return "h1:" + base64.b64encode(hashlib.sha256(seed.encode()).digest()).decode()


class GoVendorPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.vendor = self.root / "input/vendor"
        (self.vendor / "example.com/alpha").mkdir(parents=True)
        (self.vendor / "example.com/alpha/alpha.go").write_text(
            "package alpha\n", encoding="utf-8"
        )
        tool = self.vendor / "example.com/alpha/generate.sh"
        tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        tool.chmod(0o755)
        (self.vendor / "modules.txt").write_text(
            "# example.com/alpha v1.2.3 => example.com/alpha-fork v1.2.4\n"
            "## explicit; go 1.25\n"
            "example.com/alpha\n"
            "# example.com/zeta v1.0.0\n"
            "## explicit; go 1.25\n"
            "example.com/zeta\n"
            "# example.com/alpha => example.com/alpha-fork v1.2.4\n",
            encoding="utf-8",
        )
        self.go_mod = self.root / "input/go.mod"
        self.go_sum = self.root / "input/go.sum"
        self.go_mod.write_text("module example.test/app\n\ngo 1.25\n", encoding="utf-8")
        self.go_sum.write_text(
            f"example.com/alpha-fork v1.2.4 {h1('alpha replacement')}\n"
            f"example.com/alpha-fork v1.2.4/go.mod {h1('alpha replacement.mod')}\n"
            f"example.com/zeta v1.0.0 {h1('zeta')}\n"
            f"example.com/zeta v1.0.0/go.mod {h1('zeta.mod')}\n",
            encoding="utf-8",
        )
        self.graph = self.root / "module-graph.json"
        records = [
            {
                "Path": "example.com/zeta",
                "Version": "v1.0.0",
                "Sum": h1("zeta"),
                "GoModSum": h1("zeta.mod"),
                "Dir": "/host/cache/must-not-leak",
            },
            {
                "Path": "example.com/alpha",
                "Version": "v1.2.3",
                "Replace": {
                    "Path": "example.com/alpha-fork",
                    "Version": "v1.2.4",
                    "Sum": h1("alpha replacement"),
                    "GoModSum": h1("alpha replacement.mod"),
                    "Zip": "/host/cache/must-not-leak.zip",
                },
            },
        ]
        self.graph.write_text(
            "\n".join(json.dumps(record) for record in records) + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _create(self, suffix: str = "one") -> tuple[Path, Path, dict]:
        archive = self.root / f"vendor-{suffix}.tar.xz"
        manifest = self.root / f"vendor-{suffix}.json"
        result = packager.create_package(
            vendor_dir=self.vendor,
            module_graph_path=self.graph,
            source_commit=SOURCE_COMMIT,
            go_mod_path=self.go_mod,
            go_sum_path=self.go_sum,
            go_image=GO_IMAGE,
            go_version=GO_VERSION,
            archive_path=archive,
            manifest_path=manifest,
        )
        return archive, manifest, result

    def _assert_error(self, code: str, callback) -> None:
        with self.assertRaises(packager.VendorPackageError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def _rewrite_manifest_hash(self, manifest: Path, archive: Path) -> None:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        digest = hashlib.sha256()
        with archive.open("rb") as stream:
            for chunk in iter(lambda: stream.read(65536), b""):
                digest.update(chunk)
        data["archive"]["sha256"] = digest.hexdigest()
        manifest.write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def _write_source_record(
        self, path: Path, archive: Path, manifest: Path, data: dict
    ) -> None:
        path.write_text(
            json.dumps(
                {
                    "commit": SOURCE_COMMIT,
                    "module_inputs": {
                        "bundle": archive.name,
                        "bundle_sha256": data["archive"]["sha256"],
                        "manifest": manifest.name,
                        "manifest_sha256": hashlib.sha256(
                            manifest.read_bytes()
                        ).hexdigest(),
                        "go_mod_sha256": data["source"]["go_mod_sha256"],
                        "go_sum_sha256": data["source"]["go_sum_sha256"],
                        "go_image": GO_IMAGE,
                        "go_version": GO_VERSION,
                        "generator_policy": packager.GENERATOR_POLICY,
                        "module_count": len(data["modules"]),
                        "replacement_count": sum(
                            module["replacement"] is not None
                            for module in data["modules"]
                        ),
                        "file_count": len(data["archive"]["files"]),
                    },
                }
            ),
            encoding="utf-8",
        )

    def _replace_archive(self, archive: Path, members: list[tuple[tarfile.TarInfo, bytes]]) -> None:
        with lzma.LZMAFile(
            archive,
            mode="wb",
            format=lzma.FORMAT_XZ,
            check=lzma.CHECK_CRC64,
            preset=9,
        ) as compressed:
            with tarfile.open(
                fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT
            ) as output:
                for info, content in members:
                    import io

                    output.addfile(info, io.BytesIO(content))

    @staticmethod
    def _regular_info(path: str, content: bytes, mode: int = 0o644) -> tarfile.TarInfo:
        info = tarfile.TarInfo(path)
        info.size = len(content)
        info.mode = mode
        info.mtime = 0
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        return info

    def test_create_is_deterministic_and_manifest_is_closed(self) -> None:
        first_archive, first_manifest, manifest = self._create("first")
        second_archive, second_manifest, second = self._create("second")

        self.assertEqual(first_archive.read_bytes(), second_archive.read_bytes())
        self.assertEqual(first_manifest.read_bytes(), second_manifest.read_bytes())
        self.assertEqual(manifest, second)
        self.assertEqual(set(manifest), packager.TOP_LEVEL_KEYS)
        self.assertEqual(set(manifest["source"]), packager.SOURCE_KEYS)
        self.assertEqual(set(manifest["generator"]), packager.GENERATOR_KEYS)
        self.assertEqual(set(manifest["archive"]), packager.ARCHIVE_KEYS)
        self.assertEqual(manifest["generator"]["go_image"], GO_IMAGE)
        self.assertEqual(manifest["generator"]["go_version"], GO_VERSION)
        self.assertEqual(manifest["generator"]["policy"], packager.GENERATOR_POLICY)
        self.assertEqual(first_archive.read_bytes()[:6], b"\xfd7zXZ\x00")

        self.assertEqual(
            [(module["path"], module["version"]) for module in manifest["modules"]],
            [("example.com/alpha", "v1.2.3"), ("example.com/zeta", "v1.0.0")],
        )
        replacement = manifest["modules"][0]["replacement"]
        self.assertEqual(set(replacement), packager.REPLACEMENT_KEYS)
        self.assertEqual(replacement["path"], "example.com/alpha-fork")
        self.assertNotIn("Dir", json.dumps(manifest))
        self.assertNotIn("Zip", json.dumps(manifest))
        self.assertNotIn("/host/cache", json.dumps(manifest))

        files = manifest["archive"]["files"]
        self.assertEqual([record["path"] for record in files], sorted(record["path"] for record in files))
        self.assertEqual({record["mode"] for record in files}, {"0644", "0755"})
        for record in files:
            self.assertEqual(set(record), packager.FILE_KEYS)

    def test_manifest_retains_only_the_authenticated_vendored_subset(self) -> None:
        unused = {
            "Path": "example.com/unused",
            "Version": "v9.9.9",
            "Sum": h1("unused"),
            "GoModSum": h1("unused.mod"),
        }
        with self.graph.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(unused) + "\n")

        _, _, manifest = self._create("subset")

        self.assertEqual(
            [(module["path"], module["version"]) for module in manifest["modules"]],
            [("example.com/alpha", "v1.2.3"), ("example.com/zeta", "v1.0.0")],
        )

    def test_create_rejects_a_vendored_module_missing_from_pinned_go_sum(
        self,
    ) -> None:
        self.go_sum.write_text(
            f"example.com/alpha-fork v1.2.4 {h1('alpha replacement')}\n"
            f"example.com/alpha-fork v1.2.4/go.mod {h1('alpha replacement.mod')}\n",
            encoding="utf-8",
        )

        self._assert_error("VENDORED_MODULE_SUM", lambda: self._create("missing-sum"))

    def test_verify_accepts_expected_source_and_generator(self) -> None:
        archive, manifest, _ = self._create()
        verified = packager.verify_package(
            archive_path=archive,
            manifest_path=manifest,
            expected_source_commit=SOURCE_COMMIT,
            go_mod_path=self.go_mod,
            go_sum_path=self.go_sum,
            expected_go_image=GO_IMAGE,
            expected_go_version=GO_VERSION,
        )
        self.assertEqual(verified["source"]["commit"], SOURCE_COMMIT)

    def test_create_rejects_symlinks_and_unpinned_replacements(self) -> None:
        symlink = self.vendor / "example.com/alpha/link.go"
        try:
            symlink.symlink_to("alpha.go")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        self._assert_error("VENDOR_ENTRY_TYPE", lambda: self._create("symlink"))
        symlink.unlink()

        self.graph.write_text(
            json.dumps(
                {
                    "Path": "example.com/alpha",
                    "Version": "v1.2.3",
                    "Replace": {"Path": "../alpha"},
                }
            ),
            encoding="utf-8",
        )
        self._assert_error(
            "MODULE_REPLACEMENT_UNPINNED", lambda: self._create("replacement")
        )

    def test_verify_rejects_traversal_duplicate_and_link_members(self) -> None:
        for case in ("traversal", "duplicate", "link"):
            with self.subTest(case=case):
                archive, manifest, data = self._create(case)
                first = data["archive"]["files"][0]
                content = (self.root / "input" / first["path"]).read_bytes()
                valid = self._regular_info(first["path"], content, int(first["mode"], 8))
                if case == "traversal":
                    bad = self._regular_info("vendor/../escape", b"bad")
                    members = [(valid, content), (bad, b"bad")]
                    code = "ARCHIVE_MEMBER_PATH"
                elif case == "duplicate":
                    duplicate = self._regular_info(first["path"], content, int(first["mode"], 8))
                    members = [(valid, content), (duplicate, content)]
                    code = "ARCHIVE_DUPLICATE"
                else:
                    link = tarfile.TarInfo("vendor/link")
                    link.type = tarfile.SYMTYPE
                    link.linkname = first["path"]
                    link.mtime = 0
                    link.uid = 0
                    link.gid = 0
                    members = [(valid, content), (link, b"")]
                    code = "ARCHIVE_MEMBER_TYPE"
                self._replace_archive(archive, members)
                self._rewrite_manifest_hash(manifest, archive)
                self._assert_error(
                    code,
                    lambda archive=archive, manifest=manifest: packager.verify_package(
                        archive_path=archive, manifest_path=manifest
                    ),
                )

    def test_verify_rejects_noncanonical_metadata_and_schema_drift(self) -> None:
        archive, manifest, data = self._create()
        first = data["archive"]["files"][0]
        content = (self.root / "input" / first["path"]).read_bytes()
        member = self._regular_info(first["path"], content, int(first["mode"], 8))
        member.uid = 1000
        self._replace_archive(archive, [(member, content)])
        self._rewrite_manifest_hash(manifest, archive)
        self._assert_error(
            "ARCHIVE_METADATA",
            lambda: packager.verify_package(archive_path=archive, manifest_path=manifest),
        )

        _, clean_manifest, _ = self._create("schema")
        drift = json.loads(clean_manifest.read_text(encoding="utf-8"))
        drift["unexpected"] = True
        clean_manifest.write_text(json.dumps(drift), encoding="utf-8")
        self._assert_error(
            "MANIFEST_SCHEMA",
            lambda: packager.verify_package(
                archive_path=self.root / "vendor-schema.tar.xz",
                manifest_path=clean_manifest,
            ),
        )

    def test_verify_rejects_mismatched_expected_go_image(self) -> None:
        archive, manifest, _ = self._create()
        self._assert_error(
            "EXPECTED_GO_IMAGE",
            lambda: packager.verify_package(
                archive_path=archive,
                manifest_path=manifest,
                expected_go_image="golang:other@sha256:" + "c" * 64,
            ),
        )

    def test_verify_binds_the_closed_source_record_module_inputs(self) -> None:
        archive, manifest, data = self._create()
        source_record = self.root / "source.json"
        self._write_source_record(source_record, archive, manifest, data)
        packager.verify_package(
            archive_path=archive,
            manifest_path=manifest,
            source_record_path=source_record,
        )
        record = json.loads(source_record.read_text(encoding="utf-8"))
        record["module_inputs"]["go_sum_sha256"] = "0" * 64
        source_record.write_text(json.dumps(record), encoding="utf-8")
        self._assert_error(
            "SOURCE_RECORD_BINDING",
            lambda: packager.verify_package(
                archive_path=archive,
                manifest_path=manifest,
                source_record_path=source_record,
            ),
        )

    def test_reproduction_rejects_self_consistent_vendor_tampering(self) -> None:
        reproduced_vendor = self.root / "reproduced-vendor"
        shutil.copytree(self.vendor, reproduced_vendor)
        archive, manifest, data = self._create("reproduction")
        source_record = self.root / "source-reproduction.json"
        self._write_source_record(source_record, archive, manifest, data)
        packager.verify_reproduced_package(
            source_root=self.root / "input",
            source_record_path=source_record,
            archive_path=archive,
            manifest_path=manifest,
            reproduced_vendor_dir=reproduced_vendor,
            reproduced_module_graph_path=self.graph,
        )

        (self.vendor / "example.com/alpha/alpha.go").write_text(
            "package omega\n", encoding="utf-8"
        )
        archive, manifest, data = self._create("tampered")
        self._write_source_record(source_record, archive, manifest, data)
        self._assert_error(
            "REPRODUCED_VENDOR_CONTENT",
            lambda: packager.verify_reproduced_package(
                source_root=self.root / "input",
                source_record_path=source_record,
                archive_path=archive,
                manifest_path=manifest,
                reproduced_vendor_dir=reproduced_vendor,
                reproduced_module_graph_path=self.graph,
            ),
        )

    def test_reproduction_rejects_a_different_authenticated_module_graph(self) -> None:
        archive, manifest, data = self._create("graph-reproduction")
        source_record = self.root / "source-graph-reproduction.json"
        self._write_source_record(source_record, archive, manifest, data)
        different_graph = self.root / "different-module-graph.json"
        records = [json.loads(line) for line in self.graph.read_text().splitlines()]
        records[0]["Sum"] = h1("different authenticated module")
        different_graph.write_text(
            "\n".join(json.dumps(record) for record in records) + "\n",
            encoding="utf-8",
        )
        self._assert_error(
            "VENDORED_MODULE_SUM",
            lambda: packager.verify_reproduced_package(
                source_root=self.root / "input",
                source_record_path=source_record,
                archive_path=archive,
                manifest_path=manifest,
                reproduced_vendor_dir=self.vendor,
                reproduced_module_graph_path=different_graph,
            ),
        )

    def test_reproduction_uses_fresh_authenticated_then_offline_go_boundaries(
        self,
    ) -> None:
        _, _, manifest = self._create("boundary")
        source_root = self.root / "clean-source"
        source_root.mkdir()
        shutil.copy2(self.go_mod, source_root / "go.mod")
        shutil.copy2(self.go_sum, source_root / "go.sum")
        source_record = self.root / "source-boundary.json"
        source_record.write_text(
            json.dumps(
                {
                    "commit": SOURCE_COMMIT,
                    "tree": "d" * 40,
                    "git_archive_sha256": "e" * 64,
                }
            ),
            encoding="utf-8",
        )
        go_phases: list[tuple[str, dict[str, str]]] = []

        def run_checked(command, *, cwd, env, code):
            executable = Path(command[0]).name
            if executable == "git":
                if command[1:3] == ["rev-parse", "HEAD"]:
                    return SOURCE_COMMIT + "\n"
                if command[1:3] == ["rev-parse", "HEAD^{tree}"]:
                    return "d" * 40 + "\n"
                return ""
            if command[1:3] == ["env", "GOVERSION"]:
                return "go1.25.12\n"
            if command[1:3] == ["mod", "vendor"]:
                go_phases.append(("vendor", dict(env)))
                shutil.copytree(self.vendor, Path(command[-1]))
            elif command[1:3] == ["mod", "verify"]:
                go_phases.append(("verify", dict(env)))
            return ""

        with mock.patch.object(
            packager, "verify_package", return_value=manifest
        ), mock.patch.object(
            packager.shutil,
            "which",
            side_effect=lambda name: f"/fake/{name}",
        ), mock.patch.object(
            packager,
            "_run_checked",
            side_effect=run_checked,
        ), mock.patch.object(
            packager,
            "_sha256_command_stdout",
            return_value="e" * 64,
        ):
            packager.reproduce_package(
                source_root=source_root,
                source_record_path=source_record,
                archive_path=self.root / "unused.tar.xz",
                manifest_path=self.root / "unused.json",
            )

        self.assertEqual(
            [name for name, _ in go_phases],
            ["vendor", "verify", "vendor", "verify"],
        )
        authenticated = go_phases[0][1]
        offline = go_phases[2][1]
        self.assertEqual(authenticated["GOPROXY"], "https://proxy.golang.org")
        self.assertEqual(authenticated["GOSUMDB"], "sum.golang.org")
        self.assertEqual(authenticated["GOVCS"], "*:off")
        self.assertEqual(authenticated["GOTOOLCHAIN"], "local")
        self.assertEqual(authenticated["GOWORK"], "off")
        self.assertEqual(authenticated["GOENV"], "off")
        self.assertEqual(authenticated["GOAUTH"], "off")
        self.assertNotEqual(
            authenticated["GOMODCACHE"], os.environ.get("GOMODCACHE")
        )
        for name in ("GOPRIVATE", "GONOPROXY", "GONOSUMDB", "GOINSECURE"):
            self.assertEqual(authenticated[name], "")
        self.assertEqual(offline["GOPROXY"], "off")
        self.assertEqual(offline["GOSUMDB"], "off")
        self.assertEqual(
            {environment["GOMODCACHE"] for _, environment in go_phases},
            {authenticated["GOMODCACHE"]},
        )

    def test_cli_reports_stable_error_code(self) -> None:
        archive, manifest, data = self._create()
        source_record = self.root / "source-cli.json"
        self._write_source_record(source_record, archive, manifest, data)
        exit_code = packager.main(
            [
                "verify",
                "--source-record",
                os.fspath(source_record),
                "--bundle",
                os.fspath(archive),
                "--manifest",
                os.fspath(manifest),
                "--expected-source-commit",
                "c" * 40,
            ]
        )
        self.assertEqual(exit_code, 2)

    def test_create_and_verify_cli_use_the_workflow_interface(self) -> None:
        archive = self.root / "cli-vendor.tar.xz"
        manifest = self.root / "cli-module-inputs.json"
        exit_code = packager.main(
            [
                "create",
                "--source-root",
                os.fspath(self.root / "input"),
                "--module-list",
                os.fspath(self.graph),
                "--source-commit",
                SOURCE_COMMIT,
                "--go-image",
                GO_IMAGE,
                "--go-version",
                GO_VERSION,
                "--bundle",
                os.fspath(archive),
                "--manifest",
                os.fspath(manifest),
            ]
        )
        self.assertEqual(exit_code, 0)
        data = json.loads(manifest.read_text(encoding="utf-8"))
        source_record = self.root / "cli-source.json"
        self._write_source_record(source_record, archive, manifest, data)
        self.assertEqual(
            packager.main(
                [
                    "verify",
                    "--source-record",
                    os.fspath(source_record),
                    "--bundle",
                    os.fspath(archive),
                    "--manifest",
                    os.fspath(manifest),
                ]
            ),
            0,
        )

        with mock.patch.object(packager, "reproduce_package") as reproduce:
            self.assertEqual(
                packager.main(
                    [
                        "reproduce",
                        "--source-root",
                        os.fspath(self.root / "input"),
                        "--source-record",
                        os.fspath(source_record),
                        "--bundle",
                        os.fspath(archive),
                        "--manifest",
                        os.fspath(manifest),
                    ]
                ),
                0,
            )
        reproduce.assert_called_once()


if __name__ == "__main__":
    unittest.main()
