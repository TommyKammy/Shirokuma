from __future__ import annotations

import base64
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from typing import Callable
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
VERIFIER_PATH = ROOT / "scripts/verify_polaris_trusted_image.py"
SOURCE_ARCHIVE_VALIDATOR_PATH = (
    ROOT / "scripts/validate_polaris_source_archive.py"
)


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        name,
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verifier = _load_module("verify_polaris_trusted_image", VERIFIER_PATH)
source_archive_validator = _load_module(
    "validate_polaris_source_archive",
    SOURCE_ARCHIVE_VALIDATOR_PATH,
)


class PolarisTrustedImageContractTests(unittest.TestCase):
    def _source_archive(
        self,
        entries: list[tuple[str, str, bytes | str | None]],
        *,
        archive_format: int = tarfile.PAX_FORMAT,
    ) -> Path:
        directory = Path(tempfile.mkdtemp(prefix="polaris-source-archive-"))
        self.addCleanup(shutil.rmtree, directory)
        archive = directory / "apache-polaris-1.6.0.tar.gz"
        with tarfile.open(
            archive,
            mode="w:gz",
            format=archive_format,
        ) as bundle:
            for name, kind, payload in entries:
                member = tarfile.TarInfo(name)
                member.mtime = 0
                member.uid = 0
                member.gid = 0
                if kind == "file":
                    data = payload if isinstance(payload, bytes) else b""
                    member.type = tarfile.REGTYPE
                    member.mode = 0o644
                    member.size = len(data)
                    bundle.addfile(member, io.BytesIO(data))
                elif kind == "directory":
                    member.type = tarfile.DIRTYPE
                    member.mode = 0o755
                    bundle.addfile(member)
                elif kind == "symlink":
                    member.type = tarfile.SYMTYPE
                    member.mode = 0o777
                    member.linkname = str(payload)
                    bundle.addfile(member)
                elif kind == "hardlink":
                    member.type = tarfile.LNKTYPE
                    member.linkname = str(payload)
                    bundle.addfile(member)
                elif kind == "fifo":
                    member.type = tarfile.FIFOTYPE
                    bundle.addfile(member)
                elif kind == "pax-file":
                    member.type = tarfile.REGTYPE
                    member.mode = 0o644
                    member.pax_headers = {"unexpected": str(payload)}
                    bundle.addfile(member)
                elif kind == "pax-comment-file":
                    data = payload if isinstance(payload, bytes) else b""
                    member.type = tarfile.REGTYPE
                    member.mode = 0o644
                    member.size = len(data)
                    member.pax_headers = {
                        "comment": source_archive_validator.POLARIS_COMMIT
                    }
                    bundle.addfile(member, io.BytesIO(data))
                elif kind == "solaris-pax":
                    data = payload if isinstance(payload, bytes) else b""
                    member.type = tarfile.SOLARIS_XHDTYPE
                    member.size = len(data)
                    bundle.addfile(member, io.BytesIO(data))
                elif kind == "raw-pax":
                    data = payload if isinstance(payload, bytes) else b""
                    member.type = tarfile.XHDTYPE
                    member.size = len(data)
                    bundle.addfile(member, io.BytesIO(data))
                else:
                    raise ValueError(f"unknown fixture member kind: {kind}")
        return archive

    @staticmethod
    def _valid_source_archive_entries(
    ) -> list[tuple[str, str, bytes | str | None]]:
        root = source_archive_validator.POLARIS_SOURCE_ARCHIVE_ROOT
        return [
            (root, "directory", None),
            (f"{root}/target.txt", "file", b"authenticated\n"),
            (f"{root}/nested", "directory", None),
            (f"{root}/docs", "symlink", "guides/"),
            (f"{root}/guides", "directory", None),
            (f"{root}/nested/target-link", "symlink", "../target.txt"),
            (f"{root}/nested/chain", "symlink", "target-link"),
            (f"{root}/a", "directory", None),
            (f"{root}/a/b", "directory", None),
            (f"{root}/a/b/c", "directory", None),
            (f"{root}/a/b/c/deep-link", "symlink", "../../../target.txt"),
        ]

    def _fixture(self) -> Path:
        directory = Path(tempfile.mkdtemp(prefix="polaris-contract-"))
        self.addCleanup(shutil.rmtree, directory)
        for relative in (
            Path("bootstrap/polaris/v1.6.0"),
            Path("bootstrap/postgresql/v18.4"),
            Path(".github/workflows"),
            Path("scripts"),
        ):
            destination = directory / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(
                ROOT / relative,
                destination,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        ledger = directory / verifier.RESIDENT_LEDGER
        ledger.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / verifier.RESIDENT_LEDGER, ledger)
        return directory

    def test_authenticated_in_root_source_symlinks_are_accepted(self) -> None:
        archive = self._source_archive(self._valid_source_archive_entries())

        self.assertEqual(
            (11, 4),
            source_archive_validator.validate_source_archive(archive),
        )

        result = subprocess.run(
            [
                sys.executable,
                str(SOURCE_ARCHIVE_VALIDATOR_PATH),
                "--archive",
                str(archive),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("11 authenticated members", result.stdout)
        self.assertIn("4 in-root relative symlinks", result.stdout)

    def test_source_symlink_escape_and_missing_target_fail_closed(
        self,
    ) -> None:
        root = source_archive_validator.POLARIS_SOURCE_ARCHIVE_ROOT
        cases = {
            "absolute": "/etc/passwd",
            "escape": "../../outside",
            "missing": "missing",
            "strip-components-escape": "../apache-polaris-1.6.0/target.txt",
        }
        for case, target in cases.items():
            with self.subTest(case=case):
                entries = [
                    (root, "directory", None),
                    (f"{root}/target.txt", "file", b"authenticated\n"),
                    (f"{root}/link", "symlink", target),
                ]
                archive = self._source_archive(entries)
                with self.assertRaises(
                    source_archive_validator.ContractError
                ) as raised:
                    source_archive_validator.validate_source_archive(archive)
                self.assertEqual("SOURCE_ARCHIVE", raised.exception.code)
                self.assertRegex(
                    raised.exception.detail,
                    "escape|missing|non-canonical",
                )

    def test_source_symlink_cycles_fail_closed(self) -> None:
        root = source_archive_validator.POLARIS_SOURCE_ARCHIVE_ROOT
        archive = self._source_archive(
            [
                (root, "directory", None),
                (f"{root}/a", "symlink", "b"),
                (f"{root}/b", "symlink", "a"),
            ]
        )

        with self.assertRaises(
            source_archive_validator.ContractError
        ) as raised:
            source_archive_validator.validate_source_archive(archive)
        self.assertEqual("SOURCE_ARCHIVE", raised.exception.code)
        self.assertIn("cycle", raised.exception.detail)

    def test_source_member_below_symlink_fails_closed(self) -> None:
        root = source_archive_validator.POLARIS_SOURCE_ARCHIVE_ROOT
        archive = self._source_archive(
            [
                (root, "directory", None),
                (f"{root}/target", "directory", None),
                (f"{root}/alias", "symlink", "target"),
                (f"{root}/alias/payload", "file", b"write-through"),
            ]
        )

        with self.assertRaises(
            source_archive_validator.ContractError
        ) as raised:
            source_archive_validator.validate_source_archive(archive)
        self.assertEqual("SOURCE_ARCHIVE", raised.exception.code)
        self.assertIn("missing or non-directory parent", raised.exception.detail)

    def test_missing_and_regular_source_parents_fail_closed(self) -> None:
        root = source_archive_validator.POLARIS_SOURCE_ARCHIVE_ROOT
        cases = {
            "missing": [
                (root, "directory", None),
                (f"{root}/parent/child", "file", b"implicit-parent"),
            ],
            "regular": [
                (root, "directory", None),
                (f"{root}/parent", "file", b"not-a-directory"),
                (f"{root}/parent/child", "file", b"write-through"),
            ],
        }
        for case, entries in cases.items():
            with self.subTest(case=case):
                archive = self._source_archive(entries)
                with self.assertRaises(
                    source_archive_validator.ContractError
                ) as raised:
                    source_archive_validator.validate_source_archive(archive)
                self.assertEqual("SOURCE_ARCHIVE", raised.exception.code)
                self.assertIn(
                    "missing or non-directory parent",
                    raised.exception.detail,
                )

    def test_source_directory_symlink_must_target_directory(self) -> None:
        root = source_archive_validator.POLARIS_SOURCE_ARCHIVE_ROOT
        archive = self._source_archive(
            [
                (root, "directory", None),
                (f"{root}/target", "file", b"regular-file"),
                (f"{root}/link", "symlink", "target/"),
            ]
        )

        with self.assertRaises(
            source_archive_validator.ContractError
        ) as raised:
            source_archive_validator.validate_source_archive(archive)
        self.assertEqual("SOURCE_ARCHIVE", raised.exception.code)
        self.assertIn("targets a non-directory", raised.exception.detail)

    def test_duplicate_and_noncanonical_source_members_fail_closed(
        self,
    ) -> None:
        root = source_archive_validator.POLARIS_SOURCE_ARCHIVE_ROOT
        cases = {
            "duplicate": [
                (root, "directory", None),
                (f"{root}/same", "file", b"first"),
                (f"{root}/same", "file", b"second"),
            ],
            "traversal": [
                (root, "directory", None),
                (f"{root}/../outside", "file", b"escape"),
            ],
            "backslash": [
                (root, "directory", None),
                (f"{root}\\outside", "file", b"ambiguous"),
            ],
        }
        for case, entries in cases.items():
            with self.subTest(case=case):
                archive = self._source_archive(entries)
                with self.assertRaises(
                    source_archive_validator.ContractError
                ) as raised:
                    source_archive_validator.validate_source_archive(archive)
                self.assertEqual("SOURCE_ARCHIVE", raised.exception.code)
                self.assertRegex(
                    raised.exception.detail,
                    "duplicate|non-canonical",
                )

    def test_nonportable_source_paths_and_link_targets_fail_closed(
        self,
    ) -> None:
        root = source_archive_validator.POLARIS_SOURCE_ARCHIVE_ROOT
        cases = {
            "unicode-path": [
                (root, "directory", None),
                (f"{root}/café", "file", b"ambiguous"),
            ],
            "normalized-link": [
                (root, "directory", None),
                (f"{root}/target", "file", b"target"),
                (f"{root}/link", "symlink", "sub/../target"),
            ],
        }
        for case, entries in cases.items():
            with self.subTest(case=case):
                archive = self._source_archive(entries)
                with self.assertRaises(
                    source_archive_validator.ContractError
                ) as raised:
                    source_archive_validator.validate_source_archive(archive)
                self.assertEqual("SOURCE_ARCHIVE", raised.exception.code)
                self.assertRegex(
                    raised.exception.detail,
                    "non-portable|non-canonical",
                )

    def test_hardlinks_and_special_source_members_fail_closed(self) -> None:
        root = source_archive_validator.POLARIS_SOURCE_ARCHIVE_ROOT
        for kind, payload in (
            ("hardlink", f"{root}/target"),
            ("fifo", None),
        ):
            with self.subTest(kind=kind):
                archive = self._source_archive(
                    [
                        (root, "directory", None),
                        (f"{root}/target", "file", b"target"),
                        (f"{root}/forbidden", kind, payload),
                    ]
                )
                with self.assertRaises(
                    source_archive_validator.ContractError
                ) as raised:
                    source_archive_validator.validate_source_archive(archive)
                self.assertEqual("SOURCE_ARCHIVE", raised.exception.code)
                self.assertIn(
                    "forbidden Polaris source archive member type",
                    raised.exception.detail,
                )

    def test_unknown_source_pax_headers_fail_closed(self) -> None:
        root = source_archive_validator.POLARIS_SOURCE_ARCHIVE_ROOT
        archive = self._source_archive(
            [
                (root, "directory", None),
                (f"{root}/payload", "pax-file", "not-reviewed"),
            ]
        )

        with self.assertRaises(
            source_archive_validator.ContractError
        ) as raised:
            source_archive_validator.validate_source_archive(archive)
        self.assertEqual("SOURCE_ARCHIVE", raised.exception.code)
        self.assertIn("forbidden Polaris source PAX header", raised.exception.detail)

    def test_source_archive_numeric_limits_fail_closed(self) -> None:
        root = source_archive_validator.POLARIS_SOURCE_ARCHIVE_ROOT
        payload = b"authenticated\n"
        archive = self._source_archive(
            [
                (root, "directory", None),
                (f"{root}/target", "file", payload),
            ]
        )
        cases = (
            (
                "compressed-size",
                "POLARIS_SOURCE_ARCHIVE_MAXIMUM_BYTES",
                archive.stat().st_size - 1,
                "compressed-size limit",
            ),
            (
                "decompressed-size",
                "POLARIS_SOURCE_ARCHIVE_MAXIMUM_DECOMPRESSED_BYTES",
                1_024,
                "decompressed-size limit",
            ),
            (
                "raw-headers",
                "POLARIS_SOURCE_ARCHIVE_MAXIMUM_RAW_HEADERS",
                1,
                "raw-header limit",
            ),
            (
                "members",
                "POLARIS_SOURCE_ARCHIVE_MAXIMUM_MEMBERS",
                1,
                "member-count limit",
            ),
            (
                "member-bytes",
                "POLARIS_SOURCE_ARCHIVE_MAXIMUM_MEMBER_BYTES",
                len(payload) - 1,
                "raw member payload",
            ),
            (
                "total-file-bytes",
                "POLARIS_SOURCE_ARCHIVE_MAXIMUM_TOTAL_FILE_BYTES",
                len(payload) - 1,
                "total file-size limit",
            ),
        )
        for case, constant, limit, detail in cases:
            with self.subTest(case=case):
                with mock.patch.object(
                    source_archive_validator,
                    constant,
                    limit,
                ):
                    with self.assertRaises(
                        source_archive_validator.ContractError
                    ) as raised:
                        source_archive_validator.validate_source_archive(
                            archive
                        )
                self.assertEqual("SOURCE_ARCHIVE", raised.exception.code)
                self.assertIn(detail, raised.exception.detail)

    def test_source_archive_path_and_link_limits_fail_closed(self) -> None:
        root = source_archive_validator.POLARIS_SOURCE_ARCHIVE_ROOT
        member_path = f"{root}/target"
        path_cases = (
            (
                "path-bytes",
                "POLARIS_SOURCE_ARCHIVE_MAXIMUM_PATH_BYTES",
                len(root),
                "non-portable",
            ),
            (
                "path-components",
                "POLARIS_SOURCE_ARCHIVE_MAXIMUM_PATH_COMPONENTS",
                1,
                "non-canonical",
            ),
        )
        for case, constant, limit, detail in path_cases:
            with self.subTest(case=case):
                with mock.patch.object(
                    source_archive_validator,
                    constant,
                    limit,
                ):
                    with self.assertRaises(
                        source_archive_validator.ContractError
                    ) as raised:
                        source_archive_validator._source_archive_member_path(
                            member_path
                        )
                self.assertIn(detail, raised.exception.detail)

        with self.assertRaises(
            source_archive_validator.ContractError
        ) as raised:
            source_archive_validator._source_archive_member_path(
                f"{root}/{'a' * 256}"
            )
        self.assertIn("non-canonical", raised.exception.detail)

        link_archive = self._source_archive(
            [
                (root, "directory", None),
                (f"{root}/target", "file", b"target"),
                (f"{root}/link", "symlink", "target"),
            ]
        )
        with mock.patch.object(
            source_archive_validator,
            "POLARIS_SOURCE_ARCHIVE_MAXIMUM_LINK_BYTES",
            3,
        ):
            with self.assertRaises(
                source_archive_validator.ContractError
            ) as raised:
                source_archive_validator.validate_source_archive(link_archive)
        self.assertIn("non-portable", raised.exception.detail)

        long_link_archive = self._source_archive(
            [
                (root, "directory", None),
                (f"{root}/link", "symlink", "a" * 256),
            ]
        )
        with self.assertRaises(
            source_archive_validator.ContractError
        ) as raised:
            source_archive_validator.validate_source_archive(long_link_archive)
        self.assertIn("non-canonical", raised.exception.detail)

    def test_source_archive_control_and_pax_limits_fail_closed(self) -> None:
        root = source_archive_validator.POLARIS_SOURCE_ARCHIVE_ROOT
        pax_archive = self._source_archive(
            [
                (root, "directory", None),
                (f"{root}/payload", "pax-comment-file", b"payload"),
            ]
        )
        with mock.patch.object(
            source_archive_validator,
            "POLARIS_SOURCE_ARCHIVE_MAXIMUM_TAR_CONTROL_BYTES",
            8,
        ):
            with self.assertRaises(
                source_archive_validator.ContractError
            ) as raised:
                source_archive_validator.validate_source_archive(pax_archive)
        self.assertIn("raw member payload", raised.exception.detail)

        with mock.patch.object(
            source_archive_validator,
            "POLARIS_SOURCE_ARCHIVE_MAXIMUM_PAX_BYTES",
            8,
        ):
            with self.assertRaises(
                source_archive_validator.ContractError
            ) as raised:
                source_archive_validator.validate_source_archive(pax_archive)
        self.assertIn("PAX header", raised.exception.detail)

        oversized_control = self._source_archive(
            [
                (root, "directory", None),
                (f"{root}/payload", "pax-file", "x" * 5_000),
            ]
        )
        with self.assertRaises(
            source_archive_validator.ContractError
        ) as raised:
            source_archive_validator.validate_source_archive(oversized_control)
        self.assertIn("raw member payload", raised.exception.detail)

    def test_hidden_gnu_long_name_record_fails_closed(self) -> None:
        root = source_archive_validator.POLARIS_SOURCE_ARCHIVE_ROOT
        archive = self._source_archive(
            [
                (root, "directory", None),
                (f"{root}/{'a' * 256}", "file", b"unextractable"),
            ],
            archive_format=tarfile.GNU_FORMAT,
        )

        with self.assertRaises(
            source_archive_validator.ContractError
        ) as raised:
            source_archive_validator.validate_source_archive(archive)
        self.assertIn("hidden GNU name record", raised.exception.detail)

    def test_solaris_pax_control_record_fails_closed(self) -> None:
        root = source_archive_validator.POLARIS_SOURCE_ARCHIVE_ROOT
        archive = self._source_archive(
            [
                (root, "directory", None),
                (
                    f"{root}/PaxHeaders/payload",
                    "solaris-pax",
                    b"x" * 5_000,
                ),
                (f"{root}/payload", "file", b"payload"),
            ]
        )

        with self.assertRaises(
            source_archive_validator.ContractError
        ) as raised:
            source_archive_validator.validate_source_archive(archive)
        self.assertIn("unsupported Solaris PAX record", raised.exception.detail)

    def test_lowercase_pax_trailing_data_fails_closed(self) -> None:
        root = source_archive_validator.POLARIS_SOURCE_ARCHIVE_ROOT
        entry = (
            b"comment="
            + source_archive_validator.POLARIS_COMMIT.encode("ascii")
            + b"\n"
        )
        record_length = len(entry) + 3
        while True:
            record = str(record_length).encode("ascii") + b" " + entry
            if len(record) == record_length:
                break
            record_length = len(record)
        archive = self._source_archive(
            [
                (root, "directory", None),
                (
                    f"{root}/PaxHeaders/payload",
                    "raw-pax",
                    record + b"\0",
                ),
                (f"{root}/payload", "file", b"payload"),
            ]
        )

        with self.assertRaises(
            source_archive_validator.ContractError
        ) as raised:
            source_archive_validator.validate_source_archive(archive)
        self.assertIn("malformed PAX control record", raised.exception.detail)

    def _rewrite_json(
        self,
        root: Path,
        relative: Path,
        mutate: Callable[[dict[str, object]], None],
    ) -> None:
        path = root / relative
        value = json.loads(path.read_text(encoding="utf-8"))
        mutate(value)
        path.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _assert_code(
        self,
        root: Path,
        code: str,
        detail: str | None = None,
    ) -> None:
        with self.assertRaises(verifier.ContractError) as raised:
            verifier.audit(root)
        self.assertEqual(code, raised.exception.code)
        if detail is not None:
            self.assertIn(detail, raised.exception.detail)

    def test_repository_pending_contract_is_fail_closed_and_valid(self) -> None:
        verifier.audit(ROOT)

    def test_minimal_pending_fixture_is_valid(self) -> None:
        verifier.audit(self._fixture())

    def test_source_pin_mutation_fails_closed(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["source_release"]["archive_sha512"] = "0" * 128  # type: ignore[index]

        self._rewrite_json(root, verifier.POLARIS_SOURCE, mutate)
        self._assert_code(root, "SOURCE_PIN", "archive_sha512")

    def test_builder_digest_mutation_fails_closed(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["selected_base_candidates"]["builder_arm64_manifest"] = (  # type: ignore[index]
                "docker.io/library/gradle@sha256:" + "0" * 64
            )

        self._rewrite_json(root, verifier.POLARIS_SOURCE, mutate)
        self._assert_code(root, "SOURCE_PIN", "builder_arm64_manifest")

    def test_duplicate_contract_key_fails_closed(self) -> None:
        root = self._fixture()
        source = root / verifier.POLARIS_SOURCE
        text = source.read_text(encoding="utf-8")
        source.write_text(
            text.replace(
                '"schema_version": 1,',
                '"schema_version": 1,\n  "schema_version": 1,',
                1,
            ),
            encoding="utf-8",
        )
        self._assert_code(root, "SOURCE_PIN", "duplicate JSON key")

    def test_retained_signing_key_mutation_fails_closed(self) -> None:
        root = self._fixture()
        key = root / verifier.POLARIS_KEY
        key.write_text(key.read_text(encoding="ascii") + "\n", encoding="ascii")
        self._assert_code(root, "KEY", "SHA-256")

    def test_retained_signing_key_symlink_fails_closed(self) -> None:
        root = self._fixture()
        key = root / verifier.POLARIS_KEY
        replacement = key.with_suffix(".copy")
        key.replace(replacement)
        key.symlink_to(replacement.name)
        self._assert_code(root, "KEY", "symlink")

    def test_image_enablement_before_snapshot_review_fails_closed(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["image_publication"]["enabled"] = True  # type: ignore[index]

        self._rewrite_json(root, verifier.POLARIS_CONTRACT, mutate)
        self._assert_code(
            root,
            "CONTRACT_STATE",
            "image_publication.enabled",
        )

    def test_extra_publication_contract_is_rejected(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["alternate_publication"] = {"enabled": True}

        self._rewrite_json(root, verifier.POLARIS_CONTRACT, mutate)
        self._assert_code(root, "CONTRACT_STATE", "<root> keys")

    def test_shared_cache_enablement_is_rejected(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["dependency_snapshot"]["workflow"]["no_shared_cache"] = False  # type: ignore[index]

        self._rewrite_json(root, verifier.POLARIS_CONTRACT, mutate)
        self._assert_code(root, "CONTRACT_STATE", "no_shared_cache")

    def test_module_cache_identity_contract_drift_fails_closed(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["dependency_snapshot"]["module_cache_identity"][  # type: ignore[index]
                "encoding"
            ] = "fixed-width-lowercase-hex"

        self._rewrite_json(root, verifier.POLARIS_CONTRACT, mutate)
        self._assert_code(
            root,
            "CONTRACT_STATE",
            "module_cache_identity.encoding",
        )

    def test_module_cache_retention_contract_drift_fails_closed(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["dependency_snapshot"]["module_cache_identity"][  # type: ignore[index]
                "retention"
            ] = "all-observed-cache-files"

        self._rewrite_json(root, verifier.POLARIS_CONTRACT, mutate)
        self._assert_code(
            root,
            "CONTRACT_STATE",
            "module_cache_identity.retention",
        )

    def test_publication_workflow_is_forbidden_while_pending(self) -> None:
        root = self._fixture()
        workflow = root / ".github/workflows/polaris-arm64.yml"
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_text("name: forbidden\n", encoding="utf-8")
        self._assert_code(root, "FORBIDDEN_PATH", "polaris-arm64.yml")

    def test_alternate_publication_workflow_is_forbidden_while_pending(
        self,
    ) -> None:
        root = self._fixture()
        workflow = root / ".github/workflows/publish-catalog.yml"
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_text(
            "name: publish catalog\n"
            "jobs:\n"
            "  publish:\n"
            "    permissions:\n"
            "      packages: write\n"
            "    steps:\n"
            "      - run: echo bootstrap/polaris/v1.6.0\n",
            encoding="utf-8",
        )
        self._assert_code(root, "FORBIDDEN_PATH", "publish-catalog.yml")

    def test_indirect_write_capable_workflow_is_forbidden_while_pending(
        self,
    ) -> None:
        root = self._fixture()
        workflow = root / ".github/workflows/publish-polaris.yml"
        workflow.write_text(
            "name: publish\n"
            "permissions:\n"
            "  packages: write\n",
            encoding="utf-8",
        )
        self._assert_code(root, "FORBIDDEN_PATH", "workflow inventory changed")

    def test_existing_workflow_byte_drift_is_forbidden_while_pending(self) -> None:
        root = self._fixture()
        workflow = root / ".github/workflows/ci.yml"
        workflow.write_text(
            workflow.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        self._assert_code(root, "FORBIDDEN_PATH", "workflow inventory changed")

    def test_dependency_workflow_byte_drift_fails_closed(self) -> None:
        root = self._fixture()
        workflow = root / verifier.POLARIS_DEPENDENCY_WORKFLOW
        workflow.write_text(
            workflow.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        self._assert_code(
            root,
            "CONTRACT_STATE",
            "dependency workflow bytes",
        )

    def test_dependency_workflow_step_order_is_semantically_closed(self) -> None:
        root = self._fixture()
        workflow = root / verifier.POLARIS_DEPENDENCY_WORKFLOW
        text = workflow.read_text(encoding="utf-8")
        first = "      - name: Install checksum-pinned ORAS"
        second = "      - name: Install pinned Cosign"
        workflow.write_text(
            text.replace(first, "      - name: temporary-step", 1)
            .replace(second, first, 1)
            .replace("      - name: temporary-step", second, 1),
            encoding="utf-8",
        )
        contract = json.loads(
            (root / verifier.POLARIS_CONTRACT).read_text(encoding="utf-8")
        )
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_dependency_workflow_semantics(root, contract)
        self.assertIn("ordered step inventory", raised.exception.detail)

    def test_dependency_workflow_oras_layers_must_be_relative(self) -> None:
        for layer_path in (
            "${candidate_dir}/gradle-dependency-inputs.json",
            "$PWD/gradle-dependency-inputs.json",
            "$(pwd)/gradle-dependency-inputs.json",
            "../gradle-dependency-inputs.json",
            "/tmp/gradle-dependency-inputs.json",
        ):
            with self.subTest(layer_path=layer_path):
                root = self._fixture()
                workflow = root / verifier.POLARIS_DEPENDENCY_WORKFLOW
                workflow.write_text(
                    workflow.read_text(encoding="utf-8").replace(
                        "gradle-dependency-inputs.json:"
                        "${DESCRIPTOR_MEDIA_TYPE}",
                        f"{layer_path}:${{DESCRIPTOR_MEDIA_TYPE}}",
                        1,
                    ),
                    encoding="utf-8",
                )
                contract = json.loads(
                    (root / verifier.POLARIS_CONTRACT).read_text(
                        encoding="utf-8"
                    )
                )
                with self.assertRaises(verifier.ContractError) as raised:
                    verifier._audit_dependency_workflow_semantics(
                        root,
                        contract,
                    )
                self.assertIn(
                    "exact candidate-scoped relative push",
                    raised.exception.detail,
                )

    def test_dependency_workflow_cannot_hide_a_second_oras_push(self) -> None:
        root = self._fixture()
        workflow = root / verifier.POLARIS_DEPENDENCY_WORKFLOW
        workflow.write_text(
            workflow.read_text(encoding="utf-8").replace(
                "          )\n"
                "          digest=$(oras resolve \"${tag}\")",
                "          )\n"
                "          oras push \"${tag}\" /tmp/unreviewed\n"
                "          digest=$(oras resolve \"${tag}\")",
                1,
            ),
            encoding="utf-8",
        )
        contract = json.loads(
            (root / verifier.POLARIS_CONTRACT).read_text(encoding="utf-8")
        )
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_dependency_workflow_semantics(root, contract)
        self.assertIn(
            "exact candidate-scoped relative push",
            raised.exception.detail,
        )

    def test_dependency_workflow_cannot_disable_oras_path_validation(
        self,
    ) -> None:
        root = self._fixture()
        workflow = root / verifier.POLARIS_DEPENDENCY_WORKFLOW
        workflow.write_text(
            workflow.read_text(encoding="utf-8").replace(
                "              --image-spec v1.1 \\\n",
                "              --disable-path-validation \\\n"
                "              --image-spec v1.1 \\\n",
                1,
            ),
            encoding="utf-8",
        )
        contract = json.loads(
            (root / verifier.POLARIS_CONTRACT).read_text(encoding="utf-8")
        )
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_dependency_workflow_semantics(root, contract)
        self.assertIn(
            "path validation cannot be disabled",
            raised.exception.detail,
        )

    def test_dependency_workflow_registry_verify_rejects_bundle_flag(
        self,
    ) -> None:
        root = self._fixture()
        workflow = root / verifier.POLARIS_DEPENDENCY_WORKFLOW
        workflow.write_text(
            workflow.read_text(encoding="utf-8").replace(
                "          cosign verify \\\n"
                "            --certificate-identity \\\n",
                "          cosign verify \\\n"
                "            --bundle "
                '"${candidate_dir}/cosign-signature-bundle.json" \\\n'
                "            --certificate-identity \\\n",
                1,
            ),
            encoding="utf-8",
        )
        contract = json.loads(
            (root / verifier.POLARIS_CONTRACT).read_text(encoding="utf-8")
        )
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_dependency_workflow_semantics(root, contract)
        self.assertIn(
            "without a bundle flag",
            raised.exception.detail,
        )

    def test_dependency_workflow_signing_must_retain_bundle(self) -> None:
        root = self._fixture()
        workflow = root / verifier.POLARIS_DEPENDENCY_WORKFLOW
        workflow.write_text(
            workflow.read_text(encoding="utf-8").replace(
                "          cosign sign --yes \\\n"
                "            --bundle "
                '"${candidate_dir}/cosign-signature-bundle.json" \\\n',
                "          cosign sign --yes \\\n",
                1,
            ),
            encoding="utf-8",
        )
        contract = json.loads(
            (root / verifier.POLARIS_CONTRACT).read_text(encoding="utf-8")
        )
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_dependency_workflow_semantics(root, contract)
        self.assertIn(
            "signature bundle",
            raised.exception.detail,
        )

    def test_dependency_workflow_registry_verify_bindings_are_closed(
        self,
    ) -> None:
        mutations = (
            (
                (
                    "https://github.com/${GITHUB_REPOSITORY}/.github/"
                    "workflows/polaris-gradle-dependencies.yml@${GITHUB_REF}"
                ),
                (
                    "https://github.com/${GITHUB_REPOSITORY}/.github/"
                    "workflows/other.yml@${GITHUB_REF}"
                ),
            ),
            (
                "https://token.actions.githubusercontent.com",
                "https://oauth2.sigstore.dev/auth",
            ),
            (
                "          cosign verify \\\n"
                "            --certificate-identity \\\n"
                "              \"https://github.com/${GITHUB_REPOSITORY}/"
                ".github/workflows/polaris-gradle-dependencies.yml@"
                "${GITHUB_REF}\" \\\n"
                "            --certificate-oidc-issuer \\\n"
                "              \"https://token.actions.githubusercontent.com\" "
                "\\\n"
                "            \"${PUBLISHED_REFERENCE}\" \\\n",
                "          cosign verify \\\n"
                "            --certificate-identity \\\n"
                "              \"https://github.com/${GITHUB_REPOSITORY}/"
                ".github/workflows/polaris-gradle-dependencies.yml@"
                "${GITHUB_REF}\" \\\n"
                "            --certificate-oidc-issuer \\\n"
                "              \"https://token.actions.githubusercontent.com\" "
                "\\\n"
                "            \"${PUBLISHED_TAG}\" \\\n",
            ),
        )
        for original, replacement in mutations:
            with self.subTest(replacement=replacement):
                root = self._fixture()
                workflow = root / verifier.POLARIS_DEPENDENCY_WORKFLOW
                text = workflow.read_text(encoding="utf-8")
                self.assertIn(original, text)
                workflow.write_text(
                    text.replace(original, replacement, 1),
                    encoding="utf-8",
                )
                contract = json.loads(
                    (root / verifier.POLARIS_CONTRACT).read_text(
                        encoding="utf-8"
                    )
                )
                with self.assertRaises(verifier.ContractError) as raised:
                    verifier._audit_dependency_workflow_semantics(
                        root,
                        contract,
                    )
                self.assertIn(
                    "exact signed reference",
                    raised.exception.detail,
                )

    def test_dependency_workflow_cosign_evidence_must_be_retained(
        self,
    ) -> None:
        root = self._fixture()
        workflow = root / verifier.POLARIS_DEPENDENCY_WORKFLOW
        workflow.write_text(
            workflow.read_text(encoding="utf-8").replace(
                "            ${{ runner.temp }}/polaris-gradle-candidate/"
                "cosign-signature-bundle.json\n",
                "",
                1,
            ),
            encoding="utf-8",
        )
        contract = json.loads(
            (root / verifier.POLARIS_CONTRACT).read_text(encoding="utf-8")
        )
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_dependency_workflow_semantics(root, contract)
        self.assertIn(
            "hash-bound and retained",
            raised.exception.detail,
        )

    def test_dependency_workflow_signing_step_cannot_rebind_exact_reference(
        self,
    ) -> None:
        root = self._fixture()
        workflow = root / verifier.POLARIS_DEPENDENCY_WORKFLOW
        text = workflow.read_text(encoding="utf-8")
        signing_start = text.index(
            "      - name: Keyless-sign the exact OCI manifest"
        )
        original = (
            '          candidate_dir="${RUNNER_TEMP}/'
            'polaris-gradle-candidate"\n'
        )
        mutation_at = text.index(original, signing_start)
        workflow.write_text(
            text[:mutation_at]
            + original
            + '          PUBLISHED_REFERENCE="${PUBLISHED_TAG}"\n'
            + text[mutation_at + len(original) :],
            encoding="utf-8",
        )
        contract = json.loads(
            (root / verifier.POLARIS_CONTRACT).read_text(encoding="utf-8")
        )
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_dependency_workflow_semantics(root, contract)
        self.assertIn(
            "closed-world",
            raised.exception.detail,
        )

    def test_dependency_workflow_signing_step_cannot_wrap_cosign(
        self,
    ) -> None:
        root = self._fixture()
        workflow = root / verifier.POLARIS_DEPENDENCY_WORKFLOW
        text = workflow.read_text(encoding="utf-8")
        signing_start = text.index(
            "      - name: Keyless-sign the exact OCI manifest"
        )
        original = '          echo "${GHCR_TOKEN}" \\\n'
        mutation_at = text.index(original, signing_start)
        workflow.write_text(
            text[:mutation_at]
            + "          cosign() {\n"
            + '            command cosign "$@"\n'
            + '            command cosign ver"ify" --bun"dle" '
            + '"${candidate_dir}/cosign-signature-bundle.json" '
            + '"${PUBLISHED_REFERENCE}"\n'
            + "          }\n"
            + text[mutation_at:],
            encoding="utf-8",
        )
        contract = json.loads(
            (root / verifier.POLARIS_CONTRACT).read_text(encoding="utf-8")
        )
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_dependency_workflow_semantics(root, contract)
        self.assertIn(
            "closed-world",
            raised.exception.detail,
        )

    def test_dependency_workflow_action_contract_cannot_self_rebind(
        self,
    ) -> None:
        root = self._fixture()
        contract = json.loads(
            (root / verifier.POLARIS_CONTRACT).read_text(encoding="utf-8")
        )
        contract["dependency_snapshot"]["workflow"]["action_uses"][0] = (  # type: ignore[index]
            "resolve|Check out the reviewed dependency policy|"
            "actions/checkout@" + "0" * 40
        )
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_dependency_workflow_semantics(root, contract)
        self.assertIn("Action pins", raised.exception.detail)

    def test_dependency_workflow_unnamed_step_is_rejected(self) -> None:
        root = self._fixture()
        workflow = root / verifier.POLARIS_DEPENDENCY_WORKFLOW
        workflow.write_text(
            workflow.read_text(encoding="utf-8").replace(
                "    steps:\n"
                "      - name: Check out the reviewed dependency policy",
                "    steps:\n"
                "      - run: echo unnamed-step\n"
                "      - name: Check out the reviewed dependency policy",
                1,
            ),
            encoding="utf-8",
        )
        contract = json.loads(
            (root / verifier.POLARIS_CONTRACT).read_text(encoding="utf-8")
        )
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_dependency_workflow_semantics(root, contract)
        self.assertIn("unnamed or extra step entry", raised.exception.detail)

    def test_dependency_workflow_extra_trigger_is_rejected(self) -> None:
        root = self._fixture()
        workflow = root / verifier.POLARIS_DEPENDENCY_WORKFLOW
        workflow.write_text(
            workflow.read_text(encoding="utf-8").replace(
                "  workflow_dispatch:\n",
                "  workflow_dispatch:\n"
                "  schedule:\n"
                "    - cron: '0 0 * * *'\n",
                1,
            ),
            encoding="utf-8",
        )
        contract = json.loads(
            (root / verifier.POLARIS_CONTRACT).read_text(encoding="utf-8")
        )
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_dependency_workflow_semantics(root, contract)
        self.assertIn("trigger topology", raised.exception.detail)

    def test_dependency_workflow_created_date_cannot_include_commit_diff(
        self,
    ) -> None:
        root = self._fixture()
        workflow = root / verifier.POLARIS_DEPENDENCY_WORKFLOW
        workflow.write_text(
            workflow.read_text(encoding="utf-8").replace(
                'git show -s --no-show-signature --format=%cI "${GITHUB_SHA}"',
                'git show --no-show-signature --format=%cI "${GITHUB_SHA}"',
                1,
            ),
            encoding="utf-8",
        )
        contract = json.loads(
            (root / verifier.POLARIS_CONTRACT).read_text(encoding="utf-8")
        )
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_dependency_workflow_semantics(root, contract)
        self.assertIn(
            "required publication semantic",
            raised.exception.detail,
        )

    def test_dependency_workflow_cannot_skip_source_archive_validation(
        self,
    ) -> None:
        root = self._fixture()
        workflow = root / verifier.POLARIS_DEPENDENCY_WORKFLOW
        workflow.write_text(
            workflow.read_text(encoding="utf-8").replace(
                "          python3 scripts/validate_polaris_source_archive.py \\\n",
                "          python3 scripts/verify_polaris_trusted_image.py \\\n",
                1,
            ),
            encoding="utf-8",
        )
        contract = json.loads(
            (root / verifier.POLARIS_CONTRACT).read_text(encoding="utf-8")
        )
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_dependency_workflow_semantics(root, contract)
        self.assertIn(
            "source authentication step lost",
            raised.exception.detail,
        )

    def test_each_source_extraction_retains_scoped_hardening(self) -> None:
        cases = (
            (
                "online",
                (
                    '            --directory "${online_source}" '
                    "--strip-components 1 \\\n"
                    "            --no-same-owner --no-same-permissions"
                ),
                '            --directory "${online_source}"',
                "online source extraction",
            ),
            (
                "offline",
                (
                    '            --directory "${offline_source}" '
                    "--strip-components 1 \\\n"
                    "            --no-same-owner --no-same-permissions"
                ),
                '            --directory "${offline_source}"',
                "offline source extraction",
            ),
        )
        for case, original, replacement, detail in cases:
            with self.subTest(case=case):
                root = self._fixture()
                workflow = root / verifier.POLARIS_DEPENDENCY_WORKFLOW
                text = workflow.read_text(encoding="utf-8")
                self.assertIn(original, text)
                workflow.write_text(
                    text.replace(original, replacement, 1),
                    encoding="utf-8",
                )
                contract = json.loads(
                    (root / verifier.POLARIS_CONTRACT).read_text(
                        encoding="utf-8"
                    )
                )
                with self.assertRaises(verifier.ContractError) as raised:
                    verifier._audit_dependency_workflow_semantics(
                        root,
                        contract,
                    )
                self.assertIn(detail, raised.exception.detail)

    def test_read_only_verifier_cannot_receive_implicit_write_token(
        self,
    ) -> None:
        root = self._fixture()
        workflow = root / verifier.POLARIS_DEPENDENCY_WORKFLOW
        workflow.write_text(
            workflow.read_text(encoding="utf-8").replace(
                "    timeout-minutes: 30\n"
                "    permissions:\n"
                "      contents: read\n"
                "    outputs:",
                "    timeout-minutes: 30\n"
                "    env:\n"
                "      GH_TOKEN: ${{ github.token }}\n"
                "    permissions:\n"
                "      contents: read\n"
                "    outputs:",
                1,
            ),
            encoding="utf-8",
        )
        contract = json.loads(
            (root / verifier.POLARIS_CONTRACT).read_text(encoding="utf-8")
        )
        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_dependency_workflow_semantics(root, contract)
        self.assertIn("write credentials", raised.exception.detail)

    def test_dependency_packager_byte_drift_fails_closed(self) -> None:
        root = self._fixture()
        packager = root / verifier.POLARIS_DEPENDENCY_PACKAGER
        packager.write_text(
            packager.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        self._assert_code(
            root,
            "CONTRACT_STATE",
            "dependency packager bytes",
        )

    def test_source_archive_validator_byte_drift_fails_closed(self) -> None:
        root = self._fixture()
        validator = root / verifier.POLARIS_SOURCE_ARCHIVE_VALIDATOR
        validator.write_text(
            validator.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        self._assert_code(
            root,
            "CONTRACT_STATE",
            "source archive validator bytes",
        )

    def test_snapshot_lifecycle_cannot_skip_evidence_review(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["lifecycle"]["state"] = "pending_main_publication"  # type: ignore[index]

        self._rewrite_json(root, verifier.POLARIS_CONTRACT, mutate)
        self._assert_code(root, "CONTRACT_STATE", "lifecycle.state")

    def test_snapshot_reference_cannot_be_admitted_before_main_run(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["dependency_snapshot"]["artifact_reference"] = (  # type: ignore[index]
                "ghcr.io/tommykammy/"
                "shirokuma-polaris-gradle-dependencies@sha256:"
                + "0" * 64
            )

        self._rewrite_json(root, verifier.POLARIS_CONTRACT, mutate)
        self._assert_code(
            root,
            "CONTRACT_STATE",
            "artifact_reference",
        )

    def test_dependency_descriptor_is_forbidden_before_main_run(self) -> None:
        root = self._fixture()
        descriptor = (
            root
            / "bootstrap/polaris/v1.6.0/gradle-dependency-inputs.json"
        )
        descriptor.write_text("{}\n", encoding="utf-8")
        self._assert_code(
            root,
            "FORBIDDEN_PATH",
            "gradle-dependency-inputs.json",
        )

    def test_privileged_workflow_executable_drift_is_forbidden_while_pending(
        self,
    ) -> None:
        for relative in (
            "scripts/package_go_vendor.py",
            "scripts/verify_trusted_image.py",
        ):
            with self.subTest(relative=relative):
                root = self._fixture()
                executable = root / relative
                executable.write_text(
                    executable.read_text(encoding="utf-8") + "\n",
                    encoding="utf-8",
                )
                self._assert_code(root, "FORBIDDEN_PATH", relative)

    def test_privileged_workflow_executable_parent_symlink_is_forbidden(
        self,
    ) -> None:
        root = self._fixture()
        scripts = root / "scripts"
        target = root / "trusted-scripts"
        scripts.rename(target)
        scripts.symlink_to(target.name, target_is_directory=True)
        self._assert_code(
            root,
            "CONTRACT_STATE",
            "source archive validator bytes",
        )

    def test_scripts_import_shadow_additions_are_forbidden(self) -> None:
        cases = {
            "scripts/argparse.py": "raise RuntimeError('shadowed')\n",
            "scripts/argparse.pyc": "not bytecode\n",
            "scripts/argparse/__init__.py": "raise RuntimeError('shadowed')\n",
        }
        for relative, content in cases.items():
            with self.subTest(relative=relative):
                root = self._fixture()
                shadow = root / relative
                shadow.parent.mkdir(parents=True, exist_ok=True)
                shadow.write_text(content, encoding="utf-8")
                self._assert_code(root, "FORBIDDEN_PATH", "scripts inventory")

    def test_tracked_scripts_pycache_is_forbidden(self) -> None:
        root = self._fixture()
        subprocess.run(
            ["git", "init", "--quiet"],
            cwd=root,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "add", "."],
            cwd=root,
            check=True,
            capture_output=True,
        )
        cached = root / "scripts/__pycache__/argparse.cpython-313.pyc"
        cached.parent.mkdir(parents=True)
        cached.write_bytes(b"malicious unchecked bytecode")
        subprocess.run(
            ["git", "add", "--force", str(cached.relative_to(root))],
            cwd=root,
            check=True,
            capture_output=True,
        )
        self._assert_code(
            root,
            "FORBIDDEN_PATH",
            "tracked scripts inventory changed",
        )

    def test_tracked_runtime_inventory_is_closed_world(self) -> None:
        root = self._fixture()
        for relative in verifier.PENDING_RUNTIME_FILE_INVENTORY:
            source = ROOT / relative
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        subprocess.run(
            ["git", "init", "--quiet"],
            cwd=root,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "add", "."],
            cwd=root,
            check=True,
            capture_output=True,
        )
        verifier.audit(root)

        with self.subTest(case="tracked-mutation"):
            runtime_file = root / "deploy/gitops/dev/smoke-configmap.yaml"
            runtime_file.write_text(
                runtime_file.read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )
            self._assert_code(
                root,
                "RUNTIME_BLOCK",
                str(runtime_file.relative_to(root)),
            )

        shutil.copy2(
            ROOT / "deploy/gitops/dev/smoke-configmap.yaml",
            runtime_file,
        )
        with self.subTest(case="tracked-addition"):
            addition = root / "deploy/gitops/dev/neutral.yaml"
            addition.write_text("kind: ConfigMap\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", str(addition.relative_to(root))],
                cwd=root,
                check=True,
                capture_output=True,
            )
            self._assert_code(
                root,
                "RUNTIME_BLOCK",
                "tracked runtime inventory changed",
            )

    def test_alternate_containerfile_name_is_forbidden_while_pending(
        self,
    ) -> None:
        root = self._fixture()
        dockerfile = root / "bootstrap/polaris/v1.6.0/Dockerfile"
        dockerfile.write_text("FROM scratch\n", encoding="utf-8")
        self._assert_code(root, "FORBIDDEN_PATH", "Dockerfile")

    def test_alternate_bootstrap_namespaces_are_forbidden_while_pending(
        self,
    ) -> None:
        cases = {
            "bootstrap/polaris/v1.6.1/Containerfile": (
                "FROM scratch\n",
                "v1.6.1/Containerfile",
            ),
            "bootstrap/polaris/v1.6.1/Dockerfile": (
                "FROM scratch\n",
                "v1.6.1/Dockerfile",
            ),
            "bootstrap/polaris/v1.6.1/gradle-dependency-inputs.json": (
                "{}\n",
                "v1.6.1/gradle-dependency-inputs.json",
            ),
            "bootstrap/polaris/staging/release-evidence.json": (
                "{}\n",
                "staging/release-evidence.json",
            ),
            "bootstrap/postgresql/v18.5/admission.json": (
                "{}\n",
                "v18.5/admission.json",
            ),
            "bootstrap/polaris-build/v1.6.1/Containerfile": (
                "FROM scratch\n",
                "polaris-build",
            ),
            "bootstrap/postgres-build/v18.5/Containerfile": (
                "FROM scratch\n",
                "postgres-build",
            ),
            "bootstrap/archive/polaris-build/v1.6.1/Containerfile": (
                "FROM scratch\n",
                "archive/polaris-build",
            ),
            "bootstrap/pol/aris/v1.6.1/Containerfile": (
                "FROM scratch\n",
                "pol/aris",
            ),
            "bootstrap/post/gre/s/v18.5/Containerfile": (
                "FROM scratch\n",
                "post/gre/s",
            ),
            "bootstrap/pοlaris-build/v1.6.1/Containerfile": (
                "FROM scratch\n",
                "pοlaris-build",
            ),
            "bootstrap/polaris-Containerfile": (
                "FROM scratch\n",
                "polaris-Containerfile",
            ),
            "bootstrap/archive/polaris-Containerfile": (
                "FROM scratch\n",
                "polaris-Containerfile",
            ),
            "bootstrap/polaris-build/v1.6.1/payload.bin": (
                "opaque build input\n",
                "polaris-build",
            ),
        }
        for relative, (content, detail) in cases.items():
            with self.subTest(relative=relative):
                root = self._fixture()
                candidate = root / relative
                candidate.parent.mkdir(parents=True, exist_ok=True)
                candidate.write_text(content, encoding="utf-8")
                self._assert_code(
                    root,
                    "FORBIDDEN_PATH",
                    detail,
                )

    def test_unrelated_bootstrap_document_names_are_allowed(self) -> None:
        root = self._fixture()
        cases = {
            "bootstrap/seaweedfs/v4.39/docs/postgresql-compatibility.md": (
                "Compatibility notes only.\n"
            ),
            "bootstrap/flux/v2.9.2/docs/polaris-migration-notes.md": (
                "Migration notes only.\n"
            ),
            "bootstrap/flux/v2.9.2/資料/README.md": "Reference notes only.\n",
        }
        for relative, content in cases.items():
            document = root / relative
            document.parent.mkdir(parents=True, exist_ok=True)
            document.write_text(content, encoding="utf-8")
        verifier.audit(root)

    def test_release_evidence_is_forbidden_while_pending(self) -> None:
        root = self._fixture()
        evidence = root / "bootstrap/polaris/v1.6.0/evidence/claim.json"
        evidence.write_text("{}\n", encoding="utf-8")
        self._assert_code(root, "FORBIDDEN_PATH", "evidence/claim.json")

    def test_pending_retained_evidence_paths_fail_closed(self) -> None:
        cases = (
            "security/evidence/polaris-v1.6.0/supply-chain.json",
            "security/evidence/postgresql-v18.4/supply-chain.json",
            "security/evidence/catalog-service/supply-chain.json",
            "security/evidence/catalogService/supply-chain.json",
            "security/evidence/polarisarchive/supply-chain.bin",
        )
        for relative in cases:
            with self.subTest(relative=relative):
                root = self._fixture()
                evidence = root / relative
                evidence.parent.mkdir(parents=True, exist_ok=True)
                evidence.write_text("{}\n", encoding="utf-8")
                self._assert_code(
                    root,
                    "FORBIDDEN_PATH",
                    str(Path(relative).parent),
                )

    def test_pending_retained_evidence_subjects_fail_closed(self) -> None:
        cases = {
            "polaris-subject": {
                "images": [
                    {
                        "component": "metadata-service",
                        "reference": (
                            "ghcr.io/tommykammy/shirokuma-polaris@sha256:"
                            + "0" * 64
                        ),
                    }
                ]
            },
            "postgres-subject": {
                "images": [
                    {
                        "component": "metadata-service",
                        "reference": (
                            "docker.io/library/postgres@sha256:" + "1" * 64
                        ),
                    }
                ]
            },
            "catalog-subject": {
                "subject": [
                    {
                        "name": (
                            "registry.example/iceberg-rest@sha256:" + "2" * 64
                        )
                    }
                ]
            },
            "root-component-reference": {
                "component": "polaris",
                "reference": "registry.example/metadata-service@sha256:"
                + "3" * 64,
            },
            "root-repository": {
                "repository": "docker.io/library/postgres",
                "digest": "sha256:" + "4" * 64,
            },
            "cosign-docker-reference": {
                "critical": {
                    "identity": {
                        "docker-reference": (
                            "ghcr.io/tommykammy/shirokuma-polaris"
                        )
                    }
                }
            },
            "spdx-root-name": {
                "spdxVersion": "SPDX-2.3",
                "name": "polaris-1.6.0-image",
                "packages": [],
            },
            "root-array": [
                {
                    "component": "polaris",
                    "reference": (
                        "registry.example/metadata-service@sha256:" + "6" * 64
                    ),
                }
            ],
            "compact-polaris-subject": {
                "component": "polarisarchive",
                "reference": "registry.example/metadata@sha256:" + "7" * 64,
            },
            "compact-postgres-subject": {
                "component": "postgresarchive",
                "reference": "registry.example/database@sha256:" + "8" * 64,
            },
            "versioned-postgresql-subject": {
                "component": "postgresql18",
                "reference": "registry.example/database@sha256:" + "9" * 64,
            },
            "versioned-polaris-subject": {
                "component": "polaris16",
                "reference": "registry.example/metadata@sha256:" + "a" * 64,
            },
            "postgres-service-subject": {
                "component": "postgresservice",
                "reference": "registry.example/database@sha256:" + "b" * 64,
            },
        }
        for index, (label, document) in enumerate(cases.items()):
            with self.subTest(label=label):
                root = self._fixture()
                evidence = (
                    root
                    / "security/evidence/lakehouse-v1"
                    / f"claim-{index}.json"
                )
                evidence.parent.mkdir(parents=True, exist_ok=True)
                evidence.write_text(
                    json.dumps(document, indent=2) + "\n",
                    encoding="utf-8",
                )
                self._assert_code(
                    root,
                    "FORBIDDEN_PATH",
                    evidence.name,
                )

    def test_pending_dsse_subject_fails_closed(self) -> None:
        root = self._fixture()
        statement = {
            "_type": "https://in-toto.io/Statement/v1",
            "subject": [
                {
                    "name": "ghcr.io/tommykammy/shirokuma-polaris",
                    "digest": {"sha256": "c" * 64},
                }
            ],
            "predicateType": "https://slsa.dev/provenance/v1",
            "predicate": {},
        }
        envelope = {
            "payloadType": "application/vnd.in-toto+json",
            "payload": base64.b64encode(
                json.dumps(statement).encode("utf-8")
            ).decode("ascii"),
            "signatures": [],
        }
        bundle = {
            "mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json",
            "dsseEnvelope": envelope,
            "verificationMaterial": {},
        }
        evidence = root / "security/evidence/lakehouse-v1/attestation.json"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(json.dumps(bundle) + "\n", encoding="utf-8")
        self._assert_code(root, "FORBIDDEN_PATH", "attestation.json")

    def test_pending_oci_reference_in_arbitrary_json_value_fails_closed(
        self,
    ) -> None:
        root = self._fixture()
        evidence = root / "security/evidence/misc/receipt.json"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(
            json.dumps(
                {
                    "annotations": {
                        "org.opencontainers.image.ref.name": (
                            "ghcr.io/tommykammy/shirokuma-polaris@sha256:"
                            + "d" * 64
                        )
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self._assert_code(root, "FORBIDDEN_PATH", "receipt.json")

    def test_generic_pending_oci_reference_in_arbitrary_json_value_fails_closed(
        self,
    ) -> None:
        for reference in (
            "registry.example/iceberg-rest@sha256:" + "e" * 64,
            "registry.example/iceberg-rest:v1@sha256:" + "e" * 64,
            "polaris@sha256:" + "e" * 64,
            "ghcr.io/acme/polaris:1.6.0",
            "docker.io/library/postgres:18",
        ):
            with self.subTest(reference=reference):
                root = self._fixture()
                evidence = root / "security/evidence/misc/receipt.json"
                evidence.parent.mkdir(parents=True, exist_ok=True)
                evidence.write_text(
                    json.dumps({"metadata": {"arbitrary": reference}}) + "\n",
                    encoding="utf-8",
                )
                self._assert_code(root, "FORBIDDEN_PATH", "receipt.json")

    def test_unrelated_oci_reference_in_arbitrary_json_value_is_allowed(
        self,
    ) -> None:
        root = self._fixture()
        evidence = root / "security/evidence/misc/receipt.json"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(
            json.dumps(
                {
                    "annotations": {
                        "org.opencontainers.image.ref.name": (
                            "registry.example/unrelated-controller:v1@sha256:"
                            + "f" * 64
                        )
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )
        verifier.audit(root)

    def test_unrelated_oci_reference_with_pending_prose_is_allowed(self) -> None:
        root = self._fixture()
        evidence = root / "security/evidence/misc/receipt.json"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(
            json.dumps(
                {
                    "note": (
                        "Polaris compatibility metadata for "
                        "ghcr.io/acme/unrelated-controller@sha256:"
                        + "a" * 64
                    )
                }
            )
            + "\n",
            encoding="utf-8",
        )
        verifier.audit(root)

    def test_unrelated_dsse_subject_is_allowed(self) -> None:
        root = self._fixture()
        statement = {
            "_type": "https://in-toto.io/Statement/v1",
            "subject": [
                {
                    "name": "registry.example/unrelated-controller",
                    "digest": {"sha256": "d" * 64},
                }
            ],
            "predicateType": "https://slsa.dev/provenance/v1",
            "predicate": {},
        }
        envelope = {
            "payloadType": "application/vnd.in-toto+json",
            "payload": base64.b64encode(
                json.dumps(statement).encode("utf-8")
            ).decode("ascii"),
            "signatures": [],
        }
        evidence = root / "security/evidence/unrelated-v1/attestation.json"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(json.dumps(envelope) + "\n", encoding="utf-8")
        verifier.audit(root)

    def test_unknown_retained_evidence_format_fails_closed(self) -> None:
        root = self._fixture()
        evidence = root / "security/evidence/unrelated-v1/attestation.yaml"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(
            "subject: registry.example/unrelated\n",
            encoding="utf-8",
        )
        self._assert_code(
            root,
            "FORBIDDEN_PATH",
            "unsupported retained evidence format",
        )

    def test_pending_markdown_evidence_fails_closed(self) -> None:
        root = self._fixture()
        evidence = root / "security/evidence/misc/receipt.md"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(
            "# Release receipt\n\n"
            "Subject: "
            "ghcr.io/tommykammy/shirokuma-polaris@sha256:"
            + "e" * 64
            + "\n",
            encoding="utf-8",
        )
        self._assert_code(root, "FORBIDDEN_PATH", "receipt.md")

    def test_unrelated_markdown_evidence_is_allowed(self) -> None:
        root = self._fixture()
        evidence = root / "security/evidence/misc/receipt.md"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(
            "# Release receipt\n\n"
            "Subject: registry.example/unrelated-controller@sha256:"
            + "f" * 64
            + "\n",
            encoding="utf-8",
        )
        verifier.audit(root)

    def test_unrelated_retained_evidence_subjects_are_allowed(self) -> None:
        root = self._fixture()
        evidence = root / "security/evidence/unrelated-v1/supply-chain.json"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(
            json.dumps(
                {
                    "metadata": {
                        "component": {
                            "name": "unrelated-controller",
                            "purl": "pkg:oci/unrelated-controller@sha256:abc",
                        }
                    },
                    "components": [
                        {
                            "name": "postgresql",
                            "purl": "pkg:generic/postgresql@18",
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        verifier.audit(root)

    def test_unrelated_retained_evidence_subject_substrings_are_allowed(
        self,
    ) -> None:
        for index, component in enumerate(
            (
                "uncatalogued-settings",
                "catalogue-ui",
                "myicebergish",
                "polarisation-engine",
                "shirokuma-polarisation",
            )
        ):
            with self.subTest(component=component):
                root = self._fixture()
                evidence = (
                    root
                    / "security/evidence/unrelated-v1"
                    / f"claim-{index}.json"
                )
                evidence.parent.mkdir(parents=True, exist_ok=True)
                evidence.write_text(
                    json.dumps(
                        {
                            "component": component,
                            "reference": (
                                f"registry.example/{component}@sha256:"
                                + "5" * 64
                            ),
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                verifier.audit(root)

    def test_polaris_runtime_enablement_fails_closed(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["runtime_manifests"]["permitted"] = True  # type: ignore[index]

        self._rewrite_json(root, verifier.POLARIS_ADMISSION, mutate)
        self._assert_code(root, "POLARIS_ADMISSION", "runtime_manifests.permitted")

    def test_polaris_blocking_control_cannot_self_approve(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["blocking_controls"][0]["state"] = "approved"  # type: ignore[index]

        self._rewrite_json(root, verifier.POLARIS_ADMISSION, mutate)
        self._assert_code(root, "POLARIS_ADMISSION", "blocking control")

    def test_source_record_byte_drift_breaks_admission_binding(self) -> None:
        root = self._fixture()
        source = root / verifier.POLARIS_SOURCE
        source.write_text(
            source.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        self._assert_code(root, "POLARIS_ADMISSION", "source record bytes")

    def test_build_contract_byte_drift_breaks_admission_binding(self) -> None:
        root = self._fixture()
        contract = root / verifier.POLARIS_CONTRACT
        contract.write_text(
            contract.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        self._assert_code(root, "POLARIS_ADMISSION", "build contract bytes")

    def test_postgresql_candidate_digest_mutation_fails_closed(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["candidate"]["arm64_reference"] = (  # type: ignore[index]
                "cgr.dev/chainguard/postgres@sha256:" + "0" * 64
            )

        self._rewrite_json(root, verifier.POSTGRES_ADMISSION, mutate)
        self._assert_code(root, "POSTGRES_ADMISSION", "arm64_reference")

    def test_postgresql_evidence_contract_mutation_fails_closed(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["evidence_contract"]["paths"]["signature_bundle"] = (  # type: ignore[index]
                "bootstrap/postgresql/v18.4/evidence/self-asserted.json"
            )

        self._rewrite_json(root, verifier.POSTGRES_ADMISSION, mutate)
        self._assert_code(root, "POSTGRES_ADMISSION", "signature_bundle")

    def test_partial_resident_ledger_admission_fails_closed(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["images"].append({"component": "postgresql"})  # type: ignore[union-attr]

        self._rewrite_json(root, verifier.RESIDENT_LEDGER, mutate)
        self._assert_code(root, "LEDGER_BLOCK", "postgresql")

    def test_resident_ledger_alias_admission_fails_closed(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["images"].append({"component": "postgres"})  # type: ignore[union-attr]

        self._rewrite_json(root, verifier.RESIDENT_LEDGER, mutate)
        self._assert_code(root, "LEDGER_BLOCK", "postgres")

    def test_resident_ledger_reference_alias_fails_closed(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["images"].append(  # type: ignore[union-attr]
                {
                    "component": "metadata-store",
                    "reference": verifier.POSTGRES_ARM64,
                }
            )

        self._rewrite_json(root, verifier.RESIDENT_LEDGER, mutate)
        self._assert_code(root, "LEDGER_BLOCK", "metadata-store")

    def test_alternate_postgres_repository_in_ledger_fails_closed(self) -> None:
        for reference in (
            "docker.io/library/postgres@sha256:" + "0" * 64,
            "docker.io/bitnami/postgresql@sha256:" + "1" * 64,
        ):
            with self.subTest(reference=reference):
                root = self._fixture()

                def mutate(value: dict[str, object]) -> None:
                    value["images"].append(  # type: ignore[union-attr]
                        {
                            "component": "metadata-store",
                            "reference": reference,
                        }
                    )

                self._rewrite_json(root, verifier.RESIDENT_LEDGER, mutate)
                self._assert_code(root, "LEDGER_BLOCK", "metadata-store")

    def test_non_object_postgres_ledger_entry_fails_closed(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["images"].append(  # type: ignore[union-attr]
                "docker.io/library/postgres@sha256:" + "0" * 64
            )

        self._rewrite_json(root, verifier.RESIDENT_LEDGER, mutate)
        self._assert_code(root, "LEDGER_BLOCK", "must be an object")

    def test_neutral_catalog_image_in_ledger_fails_closed(self) -> None:
        cases = (
            (
                "catalog-service",
                "registry.example/neutral@sha256:" + "0" * 64,
            ),
            (
                "metadata-service",
                "registry.example/iceberg-rest@sha256:" + "1" * 64,
            ),
            (
                "catalog-service",
                "registry.example/iceberg-rest@sha256:" + "2" * 64,
            ),
            (
                "catalogservice",
                "registry.example/neutral@sha256:" + "3" * 64,
            ),
            (
                "IcebergCatalog",
                "registry.example/neutral@sha256:" + "4" * 64,
            ),
            (
                "metadata-service",
                "registry.example/icebergrest@sha256:" + "5" * 64,
            ),
            (
                "metastoredb",
                "registry.example/neutral@sha256:" + "6" * 64,
            ),
        )
        for component, reference in cases:
            with self.subTest(component=component, reference=reference):
                root = self._fixture()

                def mutate(value: dict[str, object]) -> None:
                    value["images"].append(  # type: ignore[union-attr]
                        {
                            "component": component,
                            "reference": reference,
                        }
                    )

                self._rewrite_json(root, verifier.RESIDENT_LEDGER, mutate)
                self._assert_code(root, "LEDGER_BLOCK", component)

    def test_unrelated_ledger_identity_is_not_a_catalog_match(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["images"].append(  # type: ignore[union-attr]
                {
                    "component": "unrelated-service",
                    "reference": (
                        "registry.example/unrelated@sha256:" + "0" * 64
                    ),
                }
            )

        self._rewrite_json(root, verifier.RESIDENT_LEDGER, mutate)
        verifier._audit_ledger(root)

    def test_runtime_manifest_before_atomic_admission_fails_closed(self) -> None:
        root = self._fixture()
        manifest = root / "deploy/gitops/catalog/deployment.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n"
            "  name: catalog-api\n"
            "spec:\n"
            "  template:\n"
            "    spec:\n"
            "      containers:\n"
            "        - name: api\n"
            "          image: ghcr.io/example/polaris@sha256:"
            + "0" * 64
            + "\n",
            encoding="utf-8",
        )
        self._assert_code(root, "RUNTIME_BLOCK", "deployment.yaml")

    def test_catalog_path_without_component_words_fails_closed(self) -> None:
        root = self._fixture()
        manifest = root / "deploy/gitops/catalog/secret.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "apiVersion: v1\n"
            "kind: Secret\n"
            "metadata:\n"
            "  name: catalog-db\n"
            "stringData:\n"
            "  username: catalog\n",
            encoding="utf-8",
        )
        self._assert_code(root, "RUNTIME_BLOCK", "secret.yaml")

    def test_catalog_path_with_alternate_suffix_fails_closed(self) -> None:
        root = self._fixture()
        manifest = root / "deploy/gitops/catalog/secret.txt"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "kind=Secret\n"
            "name=catalog-db\n"
            "username=catalog\n",
            encoding="utf-8",
        )
        self._assert_code(root, "RUNTIME_BLOCK", "secret.txt")

    def test_catalog_markers_inside_runtime_path_segments_fail_closed(
        self,
    ) -> None:
        for segment in (
            "iceberg-catalog",
            "catalog-service",
            "icebergCatalog",
            "catalogservice",
            "icebergcatalog",
            "catalog2",
            "catalogcontroller",
            "cataloggateway",
            "catalogoperator",
            "catalogworker",
            "metastore-db",
            "metastoreDB",
        ):
            with self.subTest(segment=segment):
                root = self._fixture()
                manifest = root / "deploy/gitops" / segment / "deployment.yaml"
                manifest.parent.mkdir(parents=True)
                manifest.write_text(
                    "apiVersion: apps/v1\n"
                    "kind: Deployment\n"
                    "metadata:\n"
                    "  name: neutral\n",
                    encoding="utf-8",
                )
                self._assert_code(root, "RUNTIME_BLOCK", segment)

    def test_unrelated_runtime_path_substrings_are_allowed(self) -> None:
        for segment in (
            "uncatalogued-settings",
            "catalogue-ui",
            "myicebergish",
        ):
            with self.subTest(segment=segment):
                root = self._fixture()
                manifest = root / "deploy/gitops" / segment / "deployment.yaml"
                manifest.parent.mkdir(parents=True)
                manifest.write_text(
                    "apiVersion: apps/v1\n"
                    "kind: Deployment\n"
                    "metadata:\n"
                    "  name: neutral\n",
                    encoding="utf-8",
                )
                verifier.audit(root)

    def test_alternate_suffix_content_identity_fails_closed(self) -> None:
        root = self._fixture()
        manifest = root / "deploy/gitops/metadata/database.txt"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "engine=postgresql\n",
            encoding="utf-8",
        )
        self._assert_code(root, "RUNTIME_BLOCK", "database.txt")

    def test_neutral_path_postgres_secret_fails_closed(self) -> None:
        root = self._fixture()
        manifest = root / "deploy/gitops/iceberg/db-secret.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "apiVersion: v1\n"
            "kind: Secret\n"
            "metadata:\n"
            "  name: db\n"
            "stringData:\n"
            "  PGHOST: database\n"
            "  PGPASSWORD: example\n",
            encoding="utf-8",
        )
        self._assert_code(root, "RUNTIME_BLOCK", "db-secret.yaml")

    def test_path_neutral_postgres_assignment_fails_closed(self) -> None:
        root = self._fixture()
        manifest = root / "deploy/gitops/lakehouse/settings.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: settings\n"
            "data:\n"
            "  PGHOST: database\n",
            encoding="utf-8",
        )
        self._assert_code(root, "RUNTIME_BLOCK", "settings.yaml")

    def test_compact_json_postgres_assignment_fails_closed(self) -> None:
        root = self._fixture()
        manifest = root / "deploy/gitops/lakehouse/settings.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            '{"kind":"ConfigMap","data":{"PGHOST":"database"}}\n',
            encoding="utf-8",
        )
        self._assert_code(root, "RUNTIME_BLOCK", "settings.json")

    def test_inline_yaml_postgres_assignment_fails_closed(self) -> None:
        root = self._fixture()
        manifest = root / "deploy/gitops/lakehouse/settings.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "kind: ConfigMap\n"
            "data: {PGHOST: database}\n",
            encoding="utf-8",
        )
        self._assert_code(root, "RUNTIME_BLOCK", "settings.yaml")

    def test_path_neutral_secret_manifest_fails_closed(self) -> None:
        root = self._fixture()
        manifest = root / "deploy/gitops/lakehouse/settings.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "apiVersion: v1\n"
            "kind: Secret\n"
            "metadata:\n"
            "  name: settings\n"
            "stringData:\n"
            "  username: catalog\n"
            "  password: example\n",
            encoding="utf-8",
        )
        self._assert_code(root, "RUNTIME_BLOCK", "settings.yaml")

    def test_encoded_tagged_and_explicit_secret_kinds_fail_closed(
        self,
    ) -> None:
        cases = {
            "encoded-key.yaml": (
                "apiVersion: v1\n"
                '"k\\u0069nd": Secret\n'
                "metadata:\n"
                "  name: settings\n"
            ),
            "tagged-key.yaml": (
                "apiVersion: v1\n"
                "!!str kind: Secret\n"
                "metadata:\n"
                "  name: settings\n"
            ),
            "explicit-key.yaml": (
                "apiVersion: v1\n"
                "? kind\n"
                ": Secret\n"
                "metadata:\n"
                "  name: settings\n"
            ),
            "encoded-json-key.json": (
                '{"apiVersion":"v1","k\\u0069nd":"Secret",'
                '"metadata":{"name":"settings"}}\n'
            ),
            "aliased-kind.yaml": (
                "apiVersion: v1\n"
                "secret_kind: &k Secret\n"
                "kind: *k\n"
                "metadata:\n"
                "  name: settings\n"
                "stringData:\n"
                "  username: example\n"
                "  password: example\n"
            ),
            "flow-aliased-kind.yaml": (
                "apiVersion: v1\n"
                "metadata:\n"
                "  name: settings\n"
                "  labels: {type: &k Secret}\n"
                "kind: *k\n"
                "stringData: {password: example}\n"
            ),
        }
        for filename, content in cases.items():
            with self.subTest(filename=filename):
                root = self._fixture()
                manifest = root / "deploy/gitops/lakehouse" / filename
                manifest.parent.mkdir(parents=True)
                manifest.write_text(content, encoding="utf-8")
                self._assert_code(root, "RUNTIME_BLOCK", filename)

    def test_plural_secret_path_fails_closed(self) -> None:
        root = self._fixture()
        manifest = root / "deploy/gitops/secrets/settings.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: settings\n",
            encoding="utf-8",
        )
        self._assert_code(root, "RUNTIME_BLOCK", "settings.yaml")

    def test_secret_bearing_crd_kinds_fail_closed(self) -> None:
        for kind in (
            "SealedSecret",
            "ExternalSecret",
            "ClusterExternalSecret",
            "SecretProviderClass",
            "SecretStore",
        ):
            with self.subTest(kind=kind):
                root = self._fixture()
                manifest = root / "deploy/gitops/lakehouse/settings.yaml"
                manifest.parent.mkdir(parents=True)
                manifest.write_text(
                    "apiVersion: example.io/v1\n"
                    f"kind: {kind}\n"
                    "metadata:\n"
                    "  name: settings\n",
                    encoding="utf-8",
                )
                self._assert_code(root, "RUNTIME_BLOCK", "settings.yaml")

    def test_yaml_list_item_secret_kind_fails_closed(self) -> None:
        root = self._fixture()
        manifest = root / "deploy/gitops/lakehouse/settings.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "apiVersion: v1\n"
            "items:\n"
            "  - kind: Secret\n"
            "    metadata:\n"
            "      name: settings\n",
            encoding="utf-8",
        )
        self._assert_code(root, "RUNTIME_BLOCK", "settings.yaml")

    def test_block_scalar_secret_kinds_fail_closed(self) -> None:
        cases = {
            "object.yaml": (
                "apiVersion: v1\n"
                "kind: >-\n"
                "  Secret\n"
                "stringData:\n"
                "  username: example\n"
                "  password: example\n"
            ),
            "bundle.yaml": (
                "apiVersion: example.io/v1\n"
                "items:\n"
                "  - kind: |2-\n"
                "      ExternalSecret\n"
                "    metadata:\n"
                "      name: settings\n"
            ),
            "tagged.yaml": (
                "apiVersion: v1\n"
                "kind: !!str >-\n"
                "  Secret\n"
            ),
            "anchored.yaml": (
                "apiVersion: example.io/v1\n"
                "kind: &kind >-\n"
                "  ExternalSecret\n"
            ),
            "escaped-key.yaml": (
                "apiVersion: v1\n"
                '"k\\u0069nd": >-\n'
                "  Secret\n"
            ),
            "explicit-key.yaml": (
                "apiVersion: v1\n"
                "? kind\n"
                ": >-\n"
                "  Secret\n"
            ),
            "tagged-explicit-key.yaml": (
                "apiVersion: v1\n"
                "? !!str kind\n"
                ": >-\n"
                "  Secret\n"
            ),
            "tagged-implicit-key.yaml": (
                "apiVersion: v1\n"
                "!!str kind: >-\n"
                "  Secret\n"
            ),
        }
        for filename, content in cases.items():
            with self.subTest(filename=filename):
                root = self._fixture()
                manifest = root / "deploy/gitops/lakehouse" / filename
                manifest.parent.mkdir(parents=True)
                manifest.write_text(content, encoding="utf-8")
                self._assert_code(root, "RUNTIME_BLOCK", filename)

    def test_non_secret_block_scalars_are_allowed(self) -> None:
        cases = {
            "configmap.yaml": "kind: >-\n  ConfigMap\n",
            "description.yaml": "description: >-\n  Secret\nkind: ConfigMap\n",
            "clip-chomping.yaml": "kind: >\n  Secret\n",
            "leading-blank.yaml": "kind: >-\n\n  Secret\n",
            "nested-configmap-data.yaml": (
                "apiVersion: v1\n"
                "kind: ConfigMap\n"
                "data:\n"
                "  kind: >-\n"
                "    Secret\n"
            ),
            "aliased-configmap.yaml": (
                "apiVersion: v1\n"
                "kind_name: &k ConfigMap\n"
                "kind: *k\n"
                "metadata:\n"
                "  name: settings\n"
            ),
        }
        for filename, content in cases.items():
            with self.subTest(filename=filename):
                root = self._fixture()
                manifest = root / "deploy/gitops/lakehouse" / filename
                manifest.parent.mkdir(parents=True)
                manifest.write_text(content, encoding="utf-8")
                verifier.audit(root)

    def test_kustomize_secret_generators_fail_closed(self) -> None:
        cases = {
            "block-yaml": (
                "apiVersion: kustomize.config.k8s.io/v1beta1\n"
                "kind: Kustomization\n"
                "secretGenerator:\n"
                "  - name: database\n"
                "    literals:\n"
                "      - username=example\n"
                "      - password=example\n"
            ),
            "flow-json": (
                '{"secretGenerator":[{"name":"database","literals":'
                '["username=example","password=example"]}]}\n'
            ),
            "escaped-json-key": (
                '{"secret\\u0047enerator":[{"name":"database"}]}\n'
            ),
            "explicit-yaml-key": (
                "apiVersion: kustomize.config.k8s.io/v1beta1\n"
                "kind: Kustomization\n"
                "? secretGenerator\n"
                ": []\n"
            ),
            "escaped-explicit-yaml-key": (
                "apiVersion: kustomize.config.k8s.io/v1beta1\n"
                "kind: Kustomization\n"
                '? "secret\\x47enerator"\n'
                ": []\n"
            ),
            "tagged-explicit-yaml-key": (
                "apiVersion: kustomize.config.k8s.io/v1beta1\n"
                "kind: Kustomization\n"
                "? !!str secretGenerator\n"
                ": []\n"
            ),
            "anchored-explicit-yaml-key": (
                "apiVersion: kustomize.config.k8s.io/v1beta1\n"
                "kind: Kustomization\n"
                "? &sg secretGenerator\n"
                ": []\n"
            ),
            "tagged-implicit-yaml-key": (
                "apiVersion: kustomize.config.k8s.io/v1beta1\n"
                "kind: Kustomization\n"
                "!!str secretGenerator: []\n"
            ),
            "globally-indented-yaml-key": (
                "  apiVersion: kustomize.config.k8s.io/v1beta1\n"
                "  kind: Kustomization\n"
                "  secretGenerator: []\n"
            ),
            "flow-yaml-key": (
                "{secretGenerator: [], "
                "configMapGenerator: [{name: settings}]}\n"
            ),
            "inline-document-flow-yaml-key": (
                "--- {secretGenerator: [], "
                "configMapGenerator: [{name: settings}]}\n"
            ),
            "quoted-flow-yaml-key": (
                "{'secretGenerator': [], "
                "'configMapGenerator': [{'name': 'settings'}]}\n"
            ),
            "escaped-yaml-key": (
                "apiVersion: kustomize.config.k8s.io/v1beta1\n"
                "kind: Kustomization\n"
                '"secret\\u0047enerator": []\n'
            ),
        }
        for label, content in cases.items():
            with self.subTest(label=label):
                root = self._fixture()
                manifest = root / "deploy/gitops/lakehouse/kustomization.yaml"
                manifest.parent.mkdir(parents=True)
                manifest.write_text(content, encoding="utf-8")
                self._assert_code(root, "RUNTIME_BLOCK", "kustomization.yaml")

    def test_non_secret_kustomize_generator_text_is_allowed(self) -> None:
        cases = {
            "yaml": (
                "apiVersion: v1\n"
                "kind: ConfigMap\n"
                "# secretGenerator: disabled while pending\n"
                'notes: [first, "secretGenerator: disabled"]\n'
                "note: |-\n"
                "  secretGenerator: disabled\n"
                "data:\n"
                "  secretGenerator: disabled\n"
                "  flow: {secretGenerator: disabled}\n"
                "secret-generator: disabled\n"
                "configMapGenerator:\n"
                "  - name: settings\n"
            ),
            "json": (
                '{"apiVersion":"v1","kind":"ConfigMap","data":'
                '{"secretGenerator":"disabled"}}\n'
            ),
        }
        for suffix, content in cases.items():
            with self.subTest(suffix=suffix):
                root = self._fixture()
                manifest = (
                    root
                    / "deploy/gitops/lakehouse"
                    / f"kustomization.{suffix}"
                )
                manifest.parent.mkdir(parents=True)
                manifest.write_text(content, encoding="utf-8")
                verifier.audit(root)

    def test_yaml_merge_keys_fail_closed_in_runtime_documents(self) -> None:
        cases = {
            "block-direct.yaml": (
                "apiVersion: kustomize.config.k8s.io/v1beta1\n"
                "kind: Kustomization\n"
                "<<: &generated\n"
                "  secretGenerator:\n"
                "    - name: database\n"
                "      literals: [username=example, password=example]\n"
            ),
            "flow.yaml": (
                "{<<: &generated {secretGenerator: []}, "
                "configMapGenerator: []}\n"
            ),
            "sequence.yaml": (
                "<<: [&first {configMapGenerator: []}, "
                "&second {secretGenerator: []}]\n"
            ),
            "tagged.yaml": (
                "!!merge <<: &generated {secretGenerator: []}\n"
            ),
            "explicit.yaml": (
                "? !!merge <<\n"
                ": &generated {secretGenerator: []}\n"
            ),
            "quoted.yaml": (
                '"<<": &generated {secretGenerator: []}\n'
            ),
        }
        for filename, content in cases.items():
            with self.subTest(filename=filename):
                root = self._fixture()
                manifest = root / "deploy/gitops/lakehouse" / filename
                manifest.parent.mkdir(parents=True)
                manifest.write_text(content, encoding="utf-8")
                self._assert_code(root, "RUNTIME_BLOCK", filename)

    def test_yaml_merge_key_examples_in_data_are_allowed(self) -> None:
        root = self._fixture()
        manifest = root / "deploy/gitops/lakehouse/settings.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "# <<: *example\n"
            "metadata:\n"
            '  annotations: {example: \"<<: *example\"}\n'
            "data:\n"
            '  inline: \"<<: *example\"\n'
            "  prose: merge <<: *example\n"
            "  literal: |-\n"
            "    <<: *example\n",
            encoding="utf-8",
        )
        verifier.audit(root)

    def test_pending_local_helm_chart_sources_fail_closed(self) -> None:
        root = self._fixture()
        chart = root / "charts/neutral"
        templates = chart / "templates"
        templates.mkdir(parents=True)
        (chart / "Chart.yaml").write_text(
            "apiVersion: v2\nname: neutral\nversion: 0.1.0\n",
            encoding="utf-8",
        )
        (chart / "values.yaml").write_text(
            "target: ConfigMap\n",
            encoding="utf-8",
        )
        (templates / "runtime.yaml").write_text(
            "apiVersion: v1\nkind: {{ .Values.target }}\n",
            encoding="utf-8",
        )
        self._assert_code(root, "FORBIDDEN_PATH", "Helm chart sources")

    def test_helm_release_runtime_manifests_fail_closed(self) -> None:
        cases = {
            "release.yaml": (
                "apiVersion: helm.toolkit.fluxcd.io/v2\n"
                "kind: HelmRelease\n"
                "metadata:\n"
                "  name: neutral\n"
                "spec:\n"
                "  values:\n"
                "    target: &target Secret\n"
                "    kind: *target\n"
            ),
            "release.json": json.dumps(
                {
                    "apiVersion": "helm.toolkit.fluxcd.io/v2",
                    "kind": "HelmRelease",
                    "metadata": {"name": "neutral"},
                    "spec": {"suspend": True},
                }
            )
            + "\n",
        }
        for filename, content in cases.items():
            with self.subTest(filename=filename):
                root = self._fixture()
                manifest = root / "deploy/gitops/lakehouse" / filename
                manifest.parent.mkdir(parents=True)
                manifest.write_text(content, encoding="utf-8")
                self._assert_code(root, "RUNTIME_BLOCK", filename)

    def test_nested_helm_release_schema_name_is_allowed(self) -> None:
        root = self._fixture()
        manifest = root / "deploy/gitops/lakehouse/crd.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "apiVersion: apiextensions.k8s.io/v1\n"
            "kind: CustomResourceDefinition\n"
            "metadata:\n"
            "  name: examples.example.test\n"
            "spec:\n"
            "  names:\n"
            "    kind: HelmRelease\n"
            "    plural: examples\n",
            encoding="utf-8",
        )
        verifier.audit(root)

    def test_postgres_environment_string_forms_fail_closed(self) -> None:
        cases = {
            "yaml-list": (
                "settings.yaml",
                "environment:\n"
                '  - "PGHOST=database"\n',
            ),
            "shell-export": (
                "settings.env",
                "export PGPASSWORD=example\n",
            ),
            "compact-json-array": (
                "settings.json",
                '{"environment":["PGHOST=database"]}\n',
            ),
        }
        for name, (filename, content) in cases.items():
            with self.subTest(name=name):
                root = self._fixture()
                manifest = root / "deploy/gitops/lakehouse" / filename
                manifest.parent.mkdir(parents=True)
                manifest.write_text(content, encoding="utf-8")
                self._assert_code(root, "RUNTIME_BLOCK", filename)

    def test_neutral_path_catalog_deployment_fails_closed(self) -> None:
        root = self._fixture()
        manifest = root / "deploy/gitops/lakehouse/rest.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n"
            "  name: iceberg-catalog\n"
            "spec:\n"
            "  template:\n"
            "    spec:\n"
            "      containers:\n"
            "        - name: rest\n"
            "          image: registry.example/iceberg-rest@sha256:"
            + "0" * 64
            + "\n",
            encoding="utf-8",
        )
        self._assert_code(root, "RUNTIME_BLOCK", "rest.yaml")

    def test_catalog_identity_fields_fail_closed(self) -> None:
        cases = {
            "name-only.yaml": (
                "kind: Deployment\n"
                "metadata:\n"
                "  name: iceberg-catalog\n"
                "image: registry.example/neutral\n"
            ),
            "image-only.yaml": (
                "kind: Deployment\n"
                "metadata:\n"
                "  name: neutral\n"
                "image: registry.example/iceberg-rest\n"
            ),
            "compact.json": (
                '{"kind":"Deployment","metadata":{"name":"catalog-service"}}\n'
            ),
            "workload.tf": (
                'resource "kubernetes_deployment_v1" "neutral" {\n'
                "  metadata {\n"
                '    name = "iceberg-catalog"\n'
                "  }\n"
                "}\n"
            ),
            "block-name.yaml": (
                "kind: Deployment\n"
                "metadata:\n"
                "  name: >-\n"
                "    iceberg-catalog\n"
            ),
            "block-image.yaml": (
                "kind: Deployment\n"
                "image: |\n"
                "  registry.example/iceberg-rest\n"
            ),
            "anchor-name.yaml": (
                "kind: Deployment\n"
                "metadata:\n"
                "  annotations:\n"
                "    runtime-name: &runtime_name iceberg-catalog\n"
                "  name: *runtime_name\n"
            ),
        }
        for filename, content in cases.items():
            with self.subTest(filename=filename):
                root = self._fixture()
                manifest = root / "deploy/gitops/lakehouse" / filename
                manifest.parent.mkdir(parents=True)
                manifest.write_text(content, encoding="utf-8")
                self._assert_code(root, "RUNTIME_BLOCK", filename)

    def test_neutral_opentofu_secret_resources_fail_closed(self) -> None:
        cases = {
            "db.tf": 'resource "kubernetes_secret_v1" "db" {}\n',
            "db.tofu": (
                'resource\n  "kubernetes_secret_v1_data"\n  "db"\n  {}\n'
            ),
            "db.tf.json": (
                '{"resource":{"kubernetes_secret_v1":{"db":{"data":{}}}}}\n'
            ),
            "db.tofu.json": (
                '{"resource":{"kubernetes_secret_v1_data":{"db":{}}}}\n'
            ),
            "db-block-after-resource.tf": (
                'resource /* formatting */ "kubernetes_secret_v1" "db" {}\n'
            ),
            "db-line-after-type.tofu": (
                'resource "kubernetes_secret_v1" // formatting\n'
                '  "db" {}\n'
            ),
            "db-hash-before-body.tf": (
                'resource "kubernetes_secret_v1" "db" # formatting\n'
                "  {}\n"
            ),
            "db-block-before-body.tofu": (
                'resource "kubernetes_secret_v1" "db" /* formatting */ {}\n'
            ),
            "db-template-comment-markers.tf": (
                "locals {\n"
                '  start = "${format("%s", "/*")}"\n'
                "}\n"
                'resource "kubernetes_secret_v1" "db" {}\n'
                "locals {\n"
                '  end = "${format("%s", "*/")}"\n'
                "}\n"
            ),
            "db-escaped-label.tf": (
                'resource "kubernetes_secret\\u005fv1" "db" {}\n'
            ),
            "db-bom.tf": (
                "\ufeffresource \"kubernetes_secret_v1\" \"db\" {}\n"
            ),
            "db-unquoted.tf": (
                "resource kubernetes_secret_v1 db {}\n"
            ),
        }
        for filename, content in cases.items():
            with self.subTest(filename=filename):
                root = self._fixture()
                manifest = root / "opentofu/dev" / filename
                manifest.parent.mkdir(parents=True)
                manifest.write_text(content, encoding="utf-8")
                self._assert_code(root, "RUNTIME_BLOCK", filename)

    def test_encoded_generic_opentofu_manifests_fail_closed(self) -> None:
        encoded_secret = base64.b64encode(
            json.dumps(
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {"name": "db"},
                }
            ).encode("utf-8")
        ).decode("ascii")
        cases = {
            "generic.tf": (
                'resource "kubernetes_manifest" "encoded" {\n'
                f'  manifest = jsondecode(base64decode("{encoded_secret}"))\n'
                "}\n"
            ),
            "generic-unquoted.tf": (
                "resource kubernetes_manifest encoded {\n"
                f'  manifest = jsondecode(base64decode("{encoded_secret}"))\n'
                "}\n"
            ),
            "generic-unquoted-unicode-name.tf": (
                "resource kubernetes_manifest 証跡 {\n"
                f'  manifest = jsondecode(base64decode("{encoded_secret}"))\n'
                "}\n"
            ),
            "generic.tf.json": json.dumps(
                {
                    "resource": {
                        "kubernetes_manifest": {
                            "encoded": {
                                "manifest": (
                                    "${jsondecode(base64decode("
                                    f'"{encoded_secret}"'
                                    "))}"
                                )
                            }
                        }
                    }
                }
            )
            + "\n",
            "kubectl.tf": (
                'resource "kubectl_manifest" "encoded" {\n'
                f'  yaml_body = base64decode("{encoded_secret}")\n'
                "}\n"
            ),
            "helm-release.tf": (
                'resource "helm_release" "encoded" {\n'
                '  name = "neutral"\n'
                '  chart = "./neutral"\n'
                f'  values = [base64decode("{encoded_secret}")]\n'
                "}\n"
            ),
        }
        for filename, content in cases.items():
            with self.subTest(filename=filename):
                root = self._fixture()
                manifest = root / "opentofu/dev" / filename
                manifest.parent.mkdir(parents=True)
                manifest.write_text(content, encoding="utf-8")
                self._assert_code(root, "RUNTIME_BLOCK", filename)

    def test_opentofu_provisioners_fail_closed(self) -> None:
        encoded_command = base64.b64encode(
            b"kubectl apply -f neutral-secret.yaml"
        ).decode("ascii")
        cases = {
            "terraform-data.tf": (
                'resource "terraform_data" "apply" {\n'
                '  provisioner "local-exec" {\n'
                f'    command = base64decode("{encoded_command}")\n'
                "  }\n"
                "}\n"
            ),
            "null-resource.tf": (
                'resource "null_resource" "apply" {\n'
                '  provisioner "remote-exec" {\n'
                '    inline = ["true"]\n'
                "  }\n"
                '  provisioner "file" {\n'
                '    source = "neutral"\n'
                '    destination = "/tmp/neutral"\n'
                "  }\n"
                "}\n"
            ),
            "unquoted.tofu": (
                "resource terraform_data apply {\n"
                "  provisioner local-exec {\n"
                '    command = "true"\n'
                "  }\n"
                "}\n"
            ),
            "terraform-data.tf.json": json.dumps(
                {
                    "resource": {
                        "terraform_data": {
                            "apply": {
                                "provisioner": [
                                    {
                                        "local-exec": {
                                            "command": (
                                                "${base64decode("
                                                f'"{encoded_command}"'
                                                ")}"
                                            )
                                        }
                                    },
                                    {"remote-exec": {"inline": ["true"]}},
                                    {
                                        "file": {
                                            "source": "neutral",
                                            "destination": "/tmp/neutral",
                                        }
                                    },
                                ]
                            }
                        }
                    }
                }
            )
            + "\n",
        }
        for filename, content in cases.items():
            with self.subTest(filename=filename):
                root = self._fixture()
                manifest = root / "opentofu/dev" / filename
                manifest.parent.mkdir(parents=True)
                manifest.write_text(content, encoding="utf-8")
                self._assert_code(root, "RUNTIME_BLOCK", filename)

    def test_approved_opentofu_secret_file_is_exact_hash_only(self) -> None:
        root = self._fixture()
        relative = "opentofu/dev/object-storage.tf"
        destination = root / relative
        destination.parent.mkdir(parents=True)
        shutil.copy2(ROOT / relative, destination)
        verifier.audit(root)

        destination.write_text(
            destination.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        self._assert_code(root, "RUNTIME_BLOCK", relative)

    def test_benign_opentofu_resource_is_not_a_secret(self) -> None:
        root = self._fixture()
        manifest = root / "opentofu/dev/namespace.tf"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            'resource "kubernetes_namespace_v1" "lakehouse" {}\n',
            encoding="utf-8",
        )
        verifier.audit(root)

    def test_opentofu_secret_examples_in_non_code_are_ignored(self) -> None:
        cases = {
            "hash-comment.tf": (
                '# resource "kubernetes_secret_v1" "db" {}\n'
            ),
            "slash-comment.tf": (
                '// resource "kubernetes_secret_v1" "db" {}\n'
            ),
            "block-comment.tf": (
                "/*\n"
                'resource "kubernetes_secret_v1" "db" {}\n'
                "*/\n"
            ),
            "heredoc.tf": (
                "locals {\n"
                "  note = <<EOT\n"
                'resource "kubernetes_secret_v1" "db" {}\n'
                'resource "kubernetes_manifest" "encoded" {}\n'
                'provisioner "local-exec" {}\n'
                "EOT\n"
                "}\n"
            ),
            "unicode-heredoc.tf": (
                "locals {\n"
                "  note = <<Ü\n"
                'resource "kubernetes_secret_v1" "db" {}\n'
                "Ü\n"
                "}\n"
            ),
            "template-comment-markers.tf": (
                "locals {\n"
                '  start = "${format("%s", "/*")}"\n'
                '  end = "${format("%s", "*/")}"\n'
                "}\n"
            ),
            "provisioner-comments.tf": (
                '# provisioner "local-exec" {}\n'
                '// provisioner "remote-exec" {}\n'
                '/* provisioner "file" {} */\n'
            ),
            "provisioner-attribute.tf": (
                "locals {\n"
                '  provisioner = "local-exec"\n'
                "}\n"
            ),
            "provisioner-metadata.tf.json": json.dumps(
                {
                    "resource": {
                        "terraform_data": {
                            "metadata": {
                                "input": {
                                    "metadata": {
                                        "annotations": {
                                            "provisioner": "documentation"
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            )
            + "\n",
        }
        for filename, content in cases.items():
            with self.subTest(filename=filename):
                root = self._fixture()
                manifest = root / "opentofu/dev" / filename
                manifest.parent.mkdir(parents=True)
                manifest.write_text(content, encoding="utf-8")
                verifier.audit(root)

    def test_disabled_iceberg_flag_is_not_a_catalog_identity_field(self) -> None:
        root = self._fixture()
        relative = "deploy/gitops/object-storage/statefulset.yaml"
        destination = root / relative
        destination.parent.mkdir(parents=True)
        shutil.copy2(ROOT / relative, destination)
        verifier.audit(root)

    def test_disabled_iceberg_flag_exception_is_exactly_bounded(self) -> None:
        approved_line = "            - -s3.port.iceberg=0"
        cases = {
            "wrong-path": (
                "deploy/gitops/other/statefulset.yaml",
                approved_line + "\n",
            ),
            "duplicate": (
                "deploy/gitops/object-storage/statefulset.yaml",
                approved_line + "\n" + approved_line + "\n",
            ),
            "changed-value": (
                "deploy/gitops/object-storage/statefulset.yaml",
                approved_line.replace("=0", "=1") + "\n",
            ),
        }
        for label, (relative, content) in cases.items():
            with self.subTest(label=label):
                root = self._fixture()
                destination = root / relative
                destination.parent.mkdir(parents=True)
                destination.write_text(content, encoding="utf-8")
                self._assert_code(root, "RUNTIME_BLOCK", relative)

    def test_postgres_credential_prose_is_not_an_assignment(self) -> None:
        root = self._fixture()
        note = root / "deploy/notes.txt"
        note.parent.mkdir(parents=True)
        note.write_text(
            "documentation: PGHOST is configured externally\n",
            encoding="utf-8",
        )
        verifier.audit(root)


if __name__ == "__main__":
    unittest.main()
