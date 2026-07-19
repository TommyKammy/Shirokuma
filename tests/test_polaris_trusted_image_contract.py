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
    def setUp(self) -> None:
        super().setUp()
        self.dependency_crypto_verifier = mock.create_autospec(
            verifier._reverify_dependency_sigstore_cryptographically,
            spec_set=True,
        )

    def _audit(self, root: Path) -> None:
        verifier.audit(
            root,
            dependency_crypto_verifier=self.dependency_crypto_verifier,
        )

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
            self._audit(root)
        self.assertEqual(code, raised.exception.code)
        if detail is not None:
            self.assertIn(detail, raised.exception.detail)

    def _assert_rebound_workflow_code(
        self,
        root: Path,
        code: str,
        detail: str,
    ) -> None:
        workflow_sha256 = verifier._sha256(
            root / verifier.POLARIS_IMAGE_WORKFLOW
        )

        def bind_workflow(value: dict[str, object]) -> None:
            value["image_publication"]["workflow"][  # type: ignore[index]
                "sha256"
            ] = workflow_sha256

        self._rewrite_json(root, verifier.POLARIS_CONTRACT, bind_workflow)
        contract_sha256 = verifier._sha256(root / verifier.POLARIS_CONTRACT)

        def bind_contract(value: dict[str, object]) -> None:
            value["build_contract_sha256"] = contract_sha256

        self._rewrite_json(root, verifier.POLARIS_ADMISSION, bind_contract)
        inventory = dict(verifier.REVIEW_PENDING_WORKFLOW_INVENTORY)
        inventory[verifier.POLARIS_IMAGE_WORKFLOW.as_posix()] = (
            workflow_sha256
        )
        with mock.patch.object(
            verifier,
            "POLARIS_IMAGE_WORKFLOW_SHA256",
            workflow_sha256,
        ):
            with mock.patch.object(
                verifier,
                "POLARIS_CONTRACT_SHA256",
                contract_sha256,
            ):
                with mock.patch.object(
                    verifier,
                    "REVIEW_PENDING_WORKFLOW_INVENTORY",
                    inventory,
                ):
                    self._assert_code(root, code, detail)

    def _assert_rebound_contract_code(
        self,
        root: Path,
        code: str,
        detail: str,
    ) -> None:
        workflow_sha256 = verifier._sha256(
            root / verifier.POLARIS_IMAGE_WORKFLOW
        )

        def bind_workflow(value: dict[str, object]) -> None:
            value["image_publication"]["workflow"][  # type: ignore[index]
                "sha256"
            ] = workflow_sha256

        self._rewrite_json(root, verifier.POLARIS_CONTRACT, bind_workflow)
        contract_sha256 = verifier._sha256(root / verifier.POLARIS_CONTRACT)

        def bind_contract(value: dict[str, object]) -> None:
            value["build_contract_sha256"] = contract_sha256

        self._rewrite_json(root, verifier.POLARIS_ADMISSION, bind_contract)
        inventory = dict(verifier.REVIEW_PENDING_WORKFLOW_INVENTORY)
        inventory[verifier.POLARIS_IMAGE_WORKFLOW.as_posix()] = workflow_sha256
        with mock.patch.object(
            verifier,
            "POLARIS_IMAGE_WORKFLOW_SHA256",
            workflow_sha256,
        ):
            with mock.patch.object(
                verifier,
                "POLARIS_CONTRACT_SHA256",
                contract_sha256,
            ):
                with mock.patch.object(
                    verifier,
                    "REVIEW_PENDING_WORKFLOW_INVENTORY",
                    inventory,
                ):
                    self._assert_code(root, code, detail)

    def test_repository_review_pending_contract_is_fail_closed_and_valid(
        self,
    ) -> None:
        self._audit(ROOT)
        self.dependency_crypto_verifier.assert_called_once()

    def test_static_publication_bootstrap_never_invokes_external_crypto(
        self,
    ) -> None:
        with mock.patch.object(
            verifier,
            "_reverify_dependency_sigstore_cryptographically",
            side_effect=AssertionError("static policy invoked external crypto"),
        ) as crypto:
            verifier.audit_publication_bootstrap(ROOT)
        crypto.assert_not_called()

    def test_static_publication_bootstrap_cli_is_distinct_from_full_audit(
        self,
    ) -> None:
        stdout = io.StringIO()
        with mock.patch.object(
            verifier,
            "audit_publication_bootstrap",
        ) as bootstrap:
            with mock.patch.object(verifier, "audit") as full_audit:
                with mock.patch.object(verifier.sys, "stdout", stdout):
                    result = verifier.main(
                        [
                            "audit-publication-bootstrap",
                            "--root",
                            str(ROOT),
                        ]
                    )
        self.assertEqual(0, result)
        bootstrap.assert_called_once_with(ROOT)
        full_audit.assert_not_called()
        self.assertIn(
            "cryptographic evidence remains unverified",
            stdout.getvalue(),
        )

    def test_full_audit_cli_does_not_substitute_static_bootstrap(self) -> None:
        stdout = io.StringIO()
        with mock.patch.object(
            verifier,
            "audit_publication_bootstrap",
        ) as bootstrap:
            with mock.patch.object(verifier, "audit") as full_audit:
                with mock.patch.object(verifier.sys, "stdout", stdout):
                    result = verifier.main(["audit", "--root", str(ROOT)])
        self.assertEqual(0, result)
        full_audit.assert_called_once_with(ROOT)
        bootstrap.assert_not_called()

    def test_minimal_review_pending_fixture_is_valid(self) -> None:
        self._audit(self._fixture())

    def test_makefile_separates_unit_and_real_crypto_verification(
        self,
    ) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        unit_target = makefile.split(
            "\ntest-polaris-build-contract:\n",
            1,
        )[1].split("\n\n", 1)[0]
        cosign_target = makefile.split(
            "\nverify-cosign:\n",
            1,
        )[1].split("\n\n", 1)[0]
        verification_target = makefile.split(
            (
                "\nverify-polaris-build-contract: "
                "test-polaris-build-contract verify-cosign\n"
            ),
            1,
        )[1].split("\n\n", 1)[0]

        self.assertIn("test_package_polaris_gradle_dependencies.py", unit_target)
        self.assertIn("test_polaris_trusted_image_contract.py", unit_target)
        self.assertNotIn("COSIGN", unit_target)
        self.assertIn("COSIGN_VERSION ?= v3.1.1", makefile)
        self.assertIn("command -v $(COSIGN)", cosign_target)
        self.assertIn("$(COSIGN) version", cosign_target)
        self.assertIn(
            "scripts/verify_polaris_trusted_image.py audit --root .",
            verification_target,
        )

    def test_image_publication_contract_binds_reviewed_dependency(self) -> None:
        contract = json.loads(
            (ROOT / verifier.POLARIS_CONTRACT).read_text(encoding="utf-8")
        )
        admission = json.loads(
            (ROOT / verifier.POLARIS_ADMISSION).read_text(encoding="utf-8")
        )

        self.assertEqual(5, contract["schema_version"])
        self.assertEqual(
            "image_publication_pending",
            contract["lifecycle"]["state"],
        )
        self.assertEqual(
            "approved_for_image_build",
            contract["dependency_snapshot"]["state"],
        )
        self.assertIs(False, contract["dependency_snapshot"]["admitted"])
        self.assertEqual(
            verifier.POLARIS_DEPENDENCY_REVIEW_MERGE,
            contract["dependency_snapshot"]["review_checkpoint"][
                "merge_commit"
            ],
        )
        self.assertEqual(
            verifier.POLARIS_DEPENDENCY_REFERENCE,
            contract["dependency_snapshot"]["artifact_reference"],
        )
        self.assertEqual(
            verifier.POLARIS_DEPENDENCY_RUN_ID,
            contract["dependency_snapshot"]["publication"][
                "actions_artifact"
            ]["run_id"],
        )
        self.assertTrue(
            contract["dependency_snapshot"]["publication"]["publisher"][
                "retired"
            ]
        )
        self.assertNotIn("workflow", contract["dependency_snapshot"])
        self.assertEqual(
            verifier.POLARIS_IMAGE_WORKFLOW_SHA256,
            contract["image_publication"]["workflow"]["sha256"],
        )
        self.assertEqual(
            verifier.POLARIS_CANDIDATE_EVIDENCE_REQUIRED,
            contract["evidence"]["candidate_required"],
        )
        self.assertEqual(
            verifier.POLARIS_PROMOTION_EVIDENCE_REQUIRED,
            contract["evidence"]["promotion_required"],
        )
        self.assertIs(
            False,
            contract["toolchain"]["cosign"][
                "legacy_signature_records_permitted"
            ],
        )
        self.assertEqual(
            "application/vnd.dev.sigstore.bundle.v0.3+json",
            contract["toolchain"]["cosign"]["bundle_media_type"],
        )
        self.assertEqual(4, admission["schema_version"])
        self.assertEqual(
            verifier.POLARIS_DEPENDENCY_REFERENCE,
            admission["dependency_snapshot"]["reference"],
        )
        self.assertEqual(
            "satisfied",
            admission["blocking_controls"][1]["state"],
        )
        self.assertEqual(
            "pending",
            admission["blocking_controls"][2]["state"],
        )

    def test_candidate_evidence_exact_set_drift_fails_closed(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            evidence = value["evidence"]  # type: ignore[index]
            evidence["candidate_required"] = [  # type: ignore[index]
                *verifier.POLARIS_CANDIDATE_EVIDENCE_REQUIRED,
                "unreviewed-evidence.json",
            ]

        self._rewrite_json(root, verifier.POLARIS_CONTRACT, mutate)
        self._assert_rebound_contract_code(
            root,
            "CONTRACT_STATE",
            "evidence.candidate_required must be",
        )

    def test_cosign_toolchain_extra_control_fails_closed(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            cosign = value["toolchain"]["cosign"]  # type: ignore[index]
            cosign["verification_bypass"] = True  # type: ignore[index]

        self._rewrite_json(root, verifier.POLARIS_CONTRACT, mutate)
        self._assert_rebound_contract_code(
            root,
            "CONTRACT_STATE",
            "toolchain.cosign keys must be",
        )

    def test_cosign_legacy_signature_records_remain_forbidden(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            cosign = value["toolchain"]["cosign"]  # type: ignore[index]
            cosign["legacy_signature_records_permitted"] = True  # type: ignore[index]

        self._rewrite_json(root, verifier.POLARIS_CONTRACT, mutate)
        self._assert_rebound_contract_code(
            root,
            "CONTRACT_STATE",
            "toolchain.cosign.legacy_signature_records_permitted must be False",
        )

    def test_dependency_publisher_reintroduction_fails_closed(self) -> None:
        root = self._fixture()
        publisher = root / verifier.POLARIS_DEPENDENCY_WORKFLOW
        publisher.write_text(
            "name: forbidden publisher\n"
            "permissions:\n"
            "  packages: write\n",
            encoding="utf-8",
        )

        self._assert_code(
            root,
            "CONTRACT_STATE",
            "one-shot dependency publisher must be retired",
        )

    def test_missing_dependency_evidence_fails_closed(self) -> None:
        root = self._fixture()
        (root / verifier.POLARIS_EVIDENCE / "offline-build.json").unlink()

        self._assert_code(
            root,
            "DEPENDENCY_EVIDENCE",
            "inventory must be closed",
        )

    def test_symlinked_dependency_evidence_fails_closed(self) -> None:
        root = self._fixture()
        evidence = root / verifier.POLARIS_EVIDENCE / "toolchain.json"
        evidence.unlink()
        evidence.symlink_to("offline-build.json")

        self._assert_code(
            root,
            "DEPENDENCY_EVIDENCE",
            "real regular file",
        )

    def test_dependency_evidence_byte_drift_fails_closed(self) -> None:
        root = self._fixture()
        evidence = root / verifier.POLARIS_EVIDENCE / "offline-build.json"
        evidence.write_bytes(evidence.read_bytes() + b"\n")

        self._assert_code(
            root,
            "DEPENDENCY_EVIDENCE",
            "differs from the retained publication evidence",
        )

    def test_publication_artifact_metadata_drift_fails_closed(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["dependency_snapshot"]["publication"][  # type: ignore[index]
                "actions_artifact"
            ]["id"] = 1  # type: ignore[index]

        self._rewrite_json(root, verifier.POLARIS_CONTRACT, mutate)
        self._assert_code(
            root,
            "CONTRACT_STATE",
            "publication.actions_artifact",
        )

    def test_oci_manifest_layer_order_is_semantically_closed(self) -> None:
        root = self._fixture()
        manifest_path = (
            root / verifier.POLARIS_EVIDENCE / "oci-manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["layers"].reverse()
        manifest_path.write_text(
            json.dumps(manifest, separators=(",", ":")),
            encoding="utf-8",
        )
        publication = json.loads(
            (
                root / verifier.POLARIS_EVIDENCE / "publication.json"
            ).read_text(encoding="utf-8")
        )

        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_dependency_oci_manifest(root, publication)
        self.assertEqual("DEPENDENCY_EVIDENCE", raised.exception.code)
        self.assertIn("layer order", raised.exception.detail)

    def test_slsa_dsse_payload_must_equal_verified_statement(self) -> None:
        root = self._fixture()
        slsa_path = root / verifier.POLARIS_EVIDENCE / "slsa-verify.json"
        slsa = json.loads(slsa_path.read_text(encoding="utf-8"))
        envelope = slsa[0]["attestation"]["bundle"]["dsseEnvelope"]
        payload = json.loads(base64.b64decode(envelope["payload"]))
        payload["predicate"]["runDetails"]["metadata"]["invocationId"] = (
            "https://github.com/TommyKammy/Shirokuma/actions/runs/1/"
            "attempts/1"
        )
        envelope["payload"] = base64.b64encode(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")
        slsa_path.write_text(json.dumps(slsa), encoding="utf-8")

        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_dependency_slsa(root)
        self.assertEqual("DEPENDENCY_EVIDENCE", raised.exception.code)
        self.assertIn("DSSE payload differs", raised.exception.detail)

    def test_invalid_sigstore_bundle_does_not_reach_crypto_boundary(
        self,
    ) -> None:
        root = self._fixture()
        slsa_document = [
            {
                "attestation": {
                    "bundle": [],
                },
            }
        ]

        with self.assertRaises(verifier.ContractError) as raised:
            verifier._audit_dependency_sigstore(
                root,
                slsa_document,
                self.dependency_crypto_verifier,
            )
        self.assertEqual("DEPENDENCY_EVIDENCE", raised.exception.code)
        self.assertIn(
            "exactly one Sigstore bundle",
            raised.exception.detail,
        )
        self.dependency_crypto_verifier.assert_not_called()

    def test_default_audit_uses_real_crypto_boundary(self) -> None:
        with mock.patch.object(
            verifier,
            "_reverify_dependency_sigstore_cryptographically",
            autospec=True,
        ) as default_crypto_verifier:
            verifier.audit(ROOT)
        default_crypto_verifier.assert_called_once()

    def test_cosign_verification_failure_is_fail_closed(self) -> None:
        root = self._fixture()
        arguments = [
            "verify-blob",
            "--bundle",
            (
                verifier.POLARIS_EVIDENCE
                / "cosign-signature-bundle.json"
            ).as_posix(),
            "--certificate-identity",
            verifier.POLARIS_DEPENDENCY_PUBLISHER_IDENTITY,
            "--certificate-oidc-issuer",
            verifier.POLARIS_DEPENDENCY_PUBLISHER_ISSUER,
            "--certificate-github-workflow-repository",
            verifier.POLARIS_DEPENDENCY_PUBLISHER_REPOSITORY,
            "--certificate-github-workflow-ref",
            verifier.POLARIS_DEPENDENCY_PUBLISHER_REF,
            "--certificate-github-workflow-sha",
            verifier.POLARIS_DEPENDENCY_PUBLISHER_WORKFLOW_SHA,
            "--certificate-github-workflow-trigger",
            verifier.POLARIS_DEPENDENCY_PUBLISHER_TRIGGER,
            (verifier.POLARIS_EVIDENCE / "oci-manifest.json").as_posix(),
        ]
        failed = subprocess.CompletedProcess(
            ["cosign", *arguments],
            1,
            stdout="",
            stderr="invalid signature",
        )

        with mock.patch.object(
            verifier.subprocess,
            "run",
            return_value=failed,
        ) as run:
            with self.assertRaises(verifier.ContractError) as raised:
                verifier._run_cosign(
                    root,
                    arguments,
                    "signature-bundle verification",
                )
        self.assertEqual("DEPENDENCY_EVIDENCE", raised.exception.code)
        self.assertIn("signature-bundle verification", raised.exception.detail)
        self.assertIn("invalid signature", raised.exception.detail)
        run.assert_called_once_with(
            ["cosign", *arguments],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )

    def test_crypto_reverification_pins_exact_publisher_commit(
        self,
    ) -> None:
        root = self._fixture()
        slsa_document = json.loads(
            (
                root
                / verifier.POLARIS_EVIDENCE
                / "slsa-verify.json"
            ).read_text(encoding="utf-8")
        )
        nested_bundle = slsa_document[0]["attestation"]["bundle"]
        verifier._VERIFIED_DEPENDENCY_CRYPTOGRAPHIC_BINDINGS.clear()
        self.addCleanup(
            verifier._VERIFIED_DEPENDENCY_CRYPTOGRAPHIC_BINDINGS.clear
        )

        def complete(
            command: list[str],
            **_: object,
        ) -> subprocess.CompletedProcess[str]:
            stdout = (
                "GitVersion: v3.1.1\n"
                if command == ["cosign", "version"]
                else ""
            )
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=stdout,
                stderr="",
            )

        with mock.patch.object(
            verifier.subprocess,
            "run",
            side_effect=complete,
        ) as run:
            verifier._reverify_dependency_sigstore_cryptographically(
                root,
                verifier.POLARIS_EVIDENCE / "oci-manifest.json",
                (
                    verifier.POLARIS_EVIDENCE
                    / "cosign-signature-bundle.json"
                ),
                nested_bundle,
            )

        self.assertEqual(3, run.call_count)
        self.assertEqual(
            {
                (
                    verifier.POLARIS_DEPENDENCY_EVIDENCE_RECORDS[
                        "cosign-signature-bundle.json"
                    ][0],
                    verifier.POLARIS_DEPENDENCY_EVIDENCE_RECORDS[
                        "slsa-verify.json"
                    ][0],
                    verifier.POLARIS_DEPENDENCY_MANIFEST_SHA256,
                    verifier.POLARIS_DEPENDENCY_PUBLISHER_IDENTITY,
                    verifier.POLARIS_DEPENDENCY_PUBLISHER_ISSUER,
                    verifier.POLARIS_DEPENDENCY_PUBLISHER_REPOSITORY,
                    verifier.POLARIS_DEPENDENCY_PUBLISHER_REF,
                    verifier.POLARIS_DEPENDENCY_PUBLISHER_WORKFLOW_SHA,
                    verifier.POLARIS_DEPENDENCY_PUBLISHER_TRIGGER,
                )
            },
            verifier._VERIFIED_DEPENDENCY_CRYPTOGRAPHIC_BINDINGS,
        )
        for call in run.call_args_list[1:]:
            command = call.args[0]
            constraints = {
                "--certificate-github-workflow-repository": (
                    verifier.POLARIS_DEPENDENCY_PUBLISHER_REPOSITORY
                ),
                "--certificate-github-workflow-ref": (
                    verifier.POLARIS_DEPENDENCY_PUBLISHER_REF
                ),
                "--certificate-github-workflow-sha": (
                    verifier.POLARIS_DEPENDENCY_PUBLISHER_WORKFLOW_SHA
                ),
                "--certificate-github-workflow-trigger": (
                    verifier.POLARIS_DEPENDENCY_PUBLISHER_TRIGGER
                ),
            }
            for flag, expected in constraints.items():
                self.assertEqual(1, command.count(flag))
                self.assertEqual(
                    expected,
                    command[command.index(flag) + 1],
                )

    def test_missing_cosign_is_fail_closed(self) -> None:
        root = self._fixture()
        slsa_document = json.loads(
            (
                root
                / verifier.POLARIS_EVIDENCE
                / "slsa-verify.json"
            ).read_text(encoding="utf-8")
        )
        nested_bundle = slsa_document[0]["attestation"]["bundle"]

        with mock.patch.object(
            verifier.subprocess,
            "run",
            side_effect=FileNotFoundError("cosign"),
        ):
            with self.assertRaises(verifier.ContractError) as raised:
                verifier._reverify_dependency_sigstore_cryptographically(
                    root,
                    verifier.POLARIS_EVIDENCE / "oci-manifest.json",
                    (
                        verifier.POLARIS_EVIDENCE
                        / "cosign-signature-bundle.json"
                    ),
                    nested_bundle,
                )
        self.assertEqual("DEPENDENCY_EVIDENCE", raised.exception.code)
        self.assertIn("cannot inspect Cosign", raised.exception.detail)

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

    def test_image_publication_cannot_be_disabled_after_review(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["image_publication"]["enabled"] = False  # type: ignore[index]

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

    def test_review_pending_policy_values_are_semantically_closed(self) -> None:
        cases = (
            (
                "limit",
                ("dependency_snapshot", "limits", "maximum_files"),
                9_999,
                "dependency_snapshot.limits.maximum_files",
            ),
            (
                "archive",
                ("dependency_snapshot", "archive", "format"),
                "tar",
                "dependency_snapshot.archive.format",
            ),
            (
                "descriptor-media-type",
                ("dependency_snapshot", "descriptor_media_type"),
                "application/json",
                "dependency_snapshot.descriptor_media_type",
            ),
            (
                "visibility",
                (
                    "dependency_snapshot",
                    "visibility_bootstrap",
                    "required_visibility",
                ),
                "private",
                "dependency_snapshot.visibility_bootstrap.required_visibility",
            ),
            (
                "oras",
                ("dependency_snapshot", "tools", "oras", "version"),
                "9.9.9",
                "dependency_snapshot.tools.oras.version",
            ),
            (
                "image-repository",
                ("image_publication", "repository"),
                "ghcr.io/tommykammy/alternate-polaris",
                "image_publication.repository",
            ),
            (
                "image-tag",
                ("image_publication", "trusted_tag"),
                "latest",
                "image_publication.trusted_tag",
            ),
            (
                "runtime-base",
                ("image_publication", "runtime_base", "arm64_manifest"),
                verifier.RUNTIME_ARM64,
                "image_publication.runtime_base.arm64_manifest",
            ),
            (
                "overlay-postimage",
                (
                    "image_publication",
                    "source_overlay",
                    "postimages",
                    "runtime/service/build.gradle.kts",
                ),
                "0" * 64,
                "image_publication.source_overlay.postimages",
            ),
            (
                "vulnerability-threshold",
                ("image_publication", "vulnerability_gate", "maximum_high"),
                1,
                "image_publication.vulnerability_gate.maximum_high",
            ),
            (
                "publication-ref",
                ("image_publication", "publication_boundary", "ref"),
                "refs/heads/feature",
                "image_publication.publication_boundary.ref",
            ),
        )
        for label, path, replacement, detail in cases:
            with self.subTest(label=label):
                root = self._fixture()

                def mutate(value: dict[str, object]) -> None:
                    current: object = value
                    for key in path[:-1]:
                        self.assertIsInstance(current, dict)
                        current = current[key]  # type: ignore[index]
                    self.assertIsInstance(current, dict)
                    current[path[-1]] = replacement  # type: ignore[index]

                self._rewrite_json(root, verifier.POLARIS_CONTRACT, mutate)
                self._assert_code(root, "CONTRACT_STATE", detail)

    def test_publication_workflow_byte_drift_fails_closed(self) -> None:
        root = self._fixture()
        workflow = root / ".github/workflows/polaris-arm64.yml"
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_text("name: forbidden\n", encoding="utf-8")
        self._assert_code(
            root,
            "PUBLICATION_POLICY",
            "polaris-arm64.yml differs",
        )

    def test_containerfile_byte_drift_fails_closed(self) -> None:
        root = self._fixture()
        containerfile = root / verifier.POLARIS_CONTAINERFILE
        containerfile.write_text(
            containerfile.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        self._assert_code(
            root,
            "PUBLICATION_POLICY",
            "Containerfile differs",
        )

    def test_bounded_runtime_overlay_byte_drift_fails_closed(self) -> None:
        root = self._fixture()
        overlay = root / verifier.POLARIS_SOURCE_OVERLAY
        overlay.write_text(
            overlay.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        self._assert_code(
            root,
            "PUBLICATION_POLICY",
            "bounded-runtime.patch differs",
        )

    def test_workflow_semantics_remain_closed_when_hashes_are_rebound(
        self,
    ) -> None:
        root = self._fixture()
        workflow = root / verifier.POLARIS_IMAGE_WORKFLOW
        workflow.write_text(
            workflow.read_text(encoding="utf-8").replace(
                "          network: none\n",
                "          network: host\n",
                1,
            ),
            encoding="utf-8",
        )
        self._assert_rebound_workflow_code(
            root,
            "PUBLICATION_POLICY",
            "missing required controls",
        )

    def test_each_job_cosign_bootstrap_remains_required_when_hashes_are_rebound(
        self,
    ) -> None:
        bootstrap_names = (
            "Install pinned Cosign for dependency evidence revalidation",
            "Install pinned Cosign before write-capable policy revalidation",
            "Install pinned Cosign before promotion policy revalidation",
        )
        for bootstrap_name in bootstrap_names:
            with self.subTest(bootstrap_name=bootstrap_name):
                root = self._fixture()
                workflow = root / verifier.POLARIS_IMAGE_WORKFLOW
                workflow_text = workflow.read_text(encoding="utf-8")
                self.assertEqual(1, workflow_text.count(bootstrap_name))
                workflow.write_text(
                    workflow_text.replace(
                        bootstrap_name,
                        "Install untrusted policy verification tooling",
                        1,
                    ),
                    encoding="utf-8",
                )
                self._assert_rebound_workflow_code(
                    root,
                    "PUBLICATION_POLICY",
                    "missing required controls",
                )

    def test_prepare_cosign_bootstrap_precedes_audit_when_hashes_are_rebound(
        self,
    ) -> None:
        root = self._fixture()
        workflow = root / verifier.POLARIS_IMAGE_WORKFLOW
        bootstrap = (
            "      - name: Install pinned Cosign for dependency evidence "
            "revalidation\n"
            "        if: steps.lifecycle.outputs.active == 'true'\n"
            "        uses: sigstore/cosign-installer@"
            "6f9f17788090df1f26f669e9d70d6ae9567deba6 # v4.1.2\n"
            "        with:\n"
            "          cosign-release: v3.1.1\n\n"
        )
        oras = (
            "      - name: Install checksum-pinned ORAS without credentials\n"
        )
        workflow_text = workflow.read_text(encoding="utf-8")
        self.assertEqual(1, workflow_text.count(bootstrap))
        self.assertEqual(1, workflow_text.count(oras))
        workflow.write_text(
            workflow_text.replace(bootstrap, "", 1).replace(
                oras,
                bootstrap + oras,
                1,
            ),
            encoding="utf-8",
        )
        self._assert_rebound_workflow_code(
            root,
            "PUBLICATION_POLICY",
            "job-local static audit, Cosign bootstrap, or full audit changed order",
        )

    def test_prepare_cosign_bootstrap_cannot_be_duplicated_when_hashes_rebound(
        self,
    ) -> None:
        root = self._fixture()
        workflow = root / verifier.POLARIS_IMAGE_WORKFLOW
        bootstrap = (
            "      - name: Install pinned Cosign for dependency evidence "
            "revalidation\n"
            "        if: steps.lifecycle.outputs.active == 'true'\n"
            "        uses: sigstore/cosign-installer@"
            "6f9f17788090df1f26f669e9d70d6ae9567deba6 # v4.1.2\n"
            "        with:\n"
            "          cosign-release: v3.1.1\n\n"
        )
        oras = (
            "      - name: Install checksum-pinned ORAS without credentials\n"
        )
        workflow_text = workflow.read_text(encoding="utf-8")
        self.assertEqual(1, workflow_text.count(bootstrap))
        self.assertEqual(1, workflow_text.count(oras))
        workflow.write_text(
            workflow_text.replace(
                oras,
                bootstrap + oras,
                1,
            ),
            encoding="utf-8",
        )
        self._assert_rebound_workflow_code(
            root,
            "PUBLICATION_POLICY",
            "each job must contain exactly one policy-scoped Cosign bootstrap",
        )

    def test_downstream_cosign_bootstraps_precede_job_audits_when_rebound(
        self,
    ) -> None:
        cases = (
            (
                "verify",
                (
                    "      - name: Install pinned Cosign before write-capable "
                    "policy revalidation\n"
                    "        uses: sigstore/cosign-installer@"
                    "6f9f17788090df1f26f669e9d70d6ae9567deba6 "
                    "# v4.1.2\n"
                    "        with:\n"
                    "          cosign-release: v3.1.1\n\n"
                ),
                (
                    "      - name: Download the exact read-only-verified "
                    "image build input\n"
                ),
            ),
            (
                "promote",
                (
                    "      - name: Install pinned Cosign before promotion "
                    "policy revalidation\n"
                    "        uses: sigstore/cosign-installer@"
                    "6f9f17788090df1f26f669e9d70d6ae9567deba6 "
                    "# v4.1.2\n"
                    "        with:\n"
                    "          cosign-release: v3.1.1\n\n"
                ),
                "      - name: Download retained candidate evidence\n",
            ),
        )
        for job_name, bootstrap, following_step in cases:
            with self.subTest(job_name=job_name):
                root = self._fixture()
                workflow = root / verifier.POLARIS_IMAGE_WORKFLOW
                workflow_text = workflow.read_text(encoding="utf-8")
                self.assertEqual(1, workflow_text.count(bootstrap))
                self.assertEqual(1, workflow_text.count(following_step))
                workflow.write_text(
                    workflow_text.replace(bootstrap, "", 1).replace(
                        following_step,
                        bootstrap + following_step,
                        1,
                    ),
                    encoding="utf-8",
                )
                self._assert_rebound_workflow_code(
                    root,
                    "PUBLICATION_POLICY",
                    (
                        "job-local static audit, Cosign bootstrap, or full "
                        "audit changed order"
                    ),
                )

    def test_downstream_cosign_bootstraps_cannot_be_duplicated_when_rebound(
        self,
    ) -> None:
        cases = (
            (
                "verify",
                (
                    "      - name: Install pinned Cosign before write-capable "
                    "policy revalidation\n"
                    "        uses: sigstore/cosign-installer@"
                    "6f9f17788090df1f26f669e9d70d6ae9567deba6 "
                    "# v4.1.2\n"
                    "        with:\n"
                    "          cosign-release: v3.1.1\n\n"
                ),
                (
                    "      - name: Download the exact read-only-verified "
                    "image build input\n"
                ),
            ),
            (
                "promote",
                (
                    "      - name: Install pinned Cosign before promotion "
                    "policy revalidation\n"
                    "        uses: sigstore/cosign-installer@"
                    "6f9f17788090df1f26f669e9d70d6ae9567deba6 "
                    "# v4.1.2\n"
                    "        with:\n"
                    "          cosign-release: v3.1.1\n\n"
                ),
                "      - name: Download retained candidate evidence\n",
            ),
        )
        for job_name, bootstrap, following_step in cases:
            with self.subTest(job_name=job_name):
                root = self._fixture()
                workflow = root / verifier.POLARIS_IMAGE_WORKFLOW
                workflow_text = workflow.read_text(encoding="utf-8")
                self.assertEqual(1, workflow_text.count(bootstrap))
                self.assertEqual(1, workflow_text.count(following_step))
                workflow.write_text(
                    workflow_text.replace(
                        following_step,
                        bootstrap + following_step,
                        1,
                    ),
                    encoding="utf-8",
                )
                self._assert_rebound_workflow_code(
                    root,
                    "PUBLICATION_POLICY",
                    "each job must contain exactly one policy-scoped "
                    "Cosign bootstrap",
                )

    def test_alternate_cosign_action_is_rejected_when_hashes_are_rebound(
        self,
    ) -> None:
        root = self._fixture()
        workflow = root / verifier.POLARIS_IMAGE_WORKFLOW
        canonical = (
            "      - name: Install pinned Cosign before write-capable "
            "policy revalidation\n"
            "        uses: sigstore/cosign-installer@"
            "6f9f17788090df1f26f669e9d70d6ae9567deba6 # v4.1.2\n"
            "        with:\n"
            "          cosign-release: v3.1.1\n\n"
        )
        alternate = (
            "      - name: Alternate pinned Cosign install\n"
            "        uses: sigstore/cosign-installer@"
            "6f9f17788090df1f26f669e9d70d6ae9567deba6 # v4.1.2\n"
            "        with:\n"
            "          cosign-release: v3.1.0\n\n"
        )
        workflow_text = workflow.read_text(encoding="utf-8")
        self.assertEqual(1, workflow_text.count(canonical))
        workflow.write_text(
            workflow_text.replace(canonical, alternate + canonical, 1),
            encoding="utf-8",
        )
        self._assert_rebound_workflow_code(
            root,
            "PUBLICATION_POLICY",
            "each job must contain exactly one policy-scoped Cosign bootstrap",
        )

    def test_each_job_requires_static_and_full_audits_when_hashes_rebound(
        self,
    ) -> None:
        static_command = (
            "python3 scripts/verify_polaris_trusted_image.py "
            "audit-publication-bootstrap --root ."
        )
        cases = (
            "Validate the static publication bootstrap policy",
            "Rebind the write-capable job to the static publication policy",
            "Rebind promotion to the static publication policy",
        )
        for step_name in cases:
            with self.subTest(step_name=step_name):
                root = self._fixture()
                workflow = root / verifier.POLARIS_IMAGE_WORKFLOW
                workflow_text = workflow.read_text(encoding="utf-8")
                before, after = workflow_text.split(step_name, maxsplit=1)
                self.assertIn(static_command, after)
                workflow.write_text(
                    before
                    + step_name
                    + after.replace(
                        static_command,
                        "printf '%s\\n' 'static audit removed'",
                        1,
                    ),
                    encoding="utf-8",
                )
                self._assert_rebound_workflow_code(
                    root,
                    "PUBLICATION_POLICY",
                    "each job must contain one static and one full "
                    "cryptographic audit",
                )

    def test_static_audit_cannot_move_after_cosign_when_hashes_rebound(
        self,
    ) -> None:
        static_command = (
            "python3 scripts/verify_polaris_trusted_image.py "
            "audit-publication-bootstrap --root ."
        )
        full_command = (
            "python3 scripts/verify_polaris_trusted_image.py audit --root ."
        )
        cases = (
            "Validate the static publication bootstrap policy",
            "Rebind the write-capable job to the static publication policy",
            "Rebind promotion to the static publication policy",
        )
        for step_name in cases:
            with self.subTest(step_name=step_name):
                root = self._fixture()
                workflow = root / verifier.POLARIS_IMAGE_WORKFLOW
                workflow_text = workflow.read_text(encoding="utf-8")
                before, after = workflow_text.split(step_name, maxsplit=1)
                self.assertIn(static_command, after)
                self.assertIn(full_command, after)
                after = after.replace(static_command, "__STATIC_AUDIT__", 1)
                after = after.replace(full_command, static_command, 1)
                after = after.replace("__STATIC_AUDIT__", full_command, 1)
                workflow.write_text(
                    before + step_name + after,
                    encoding="utf-8",
                )
                self._assert_rebound_workflow_code(
                    root,
                    "PUBLICATION_POLICY",
                    (
                        "job-local static audit, Cosign bootstrap, or full "
                        "audit changed order"
                    ),
                )

    def test_malformed_job_split_fails_with_contract_error(self) -> None:
        root = self._fixture()
        workflow = root / verifier.POLARIS_IMAGE_WORKFLOW
        workflow.write_text(
            workflow.read_text(encoding="utf-8").replace(
                "\n  verify:\n",
                "\n  verify-renamed:\n",
                1,
            ),
            encoding="utf-8",
        )
        self._assert_rebound_workflow_code(
            root,
            "PUBLICATION_POLICY",
            "workflow job closure changed",
        )

    def test_candidate_attestation_capture_is_semantic_when_hashes_rebound(
        self,
    ) -> None:
        root = self._fixture()
        workflow = root / verifier.POLARIS_IMAGE_WORKFLOW
        workflow_text = workflow.read_text(encoding="utf-8")
        self.assertEqual(2, workflow_text.count("cosign attest --yes"))
        workflow.write_text(
            workflow_text.replace(
                "cosign attest --yes",
                "cosign attest --no",
                1,
            ),
            encoding="utf-8",
        )
        self._assert_rebound_workflow_code(
            root,
            "PUBLICATION_POLICY",
            "candidate signature, Rekor, SLSA, or attestation evidence changed",
        )

    def test_candidate_evidence_copy_closure_is_exact_when_hashes_rebound(
        self,
    ) -> None:
        for artifact in (
            "registry-signature-bundles.jsonl",
            "rekor-entry.json",
            "slsa-bundles.jsonl",
            "sbom-attestation-bundle.json",
            "trivy-attestation-bundle.json",
        ):
            with self.subTest(artifact=artifact):
                root = self._fixture()
                workflow = root / verifier.POLARIS_IMAGE_WORKFLOW
                workflow_text = workflow.read_text(encoding="utf-8")
                before, candidate = workflow_text.split(
                    '          evidence_dir="${RUNNER_TEMP}/'
                    'polaris-image-candidate"\n',
                    maxsplit=1,
                )
                candidate_copy, after = candidate.split(
                    "          test -s runtime-smoke.log\n",
                    maxsplit=1,
                )
                copy_line = f"            {artifact} \\\n"
                self.assertEqual(1, candidate_copy.count(copy_line))
                workflow.write_text(
                    before
                    + '          evidence_dir="${RUNNER_TEMP}/'
                    'polaris-image-candidate"\n'
                    + candidate_copy.replace(copy_line, "", 1)
                    + "          test -s runtime-smoke.log\n"
                    + after,
                    encoding="utf-8",
                )
                self._assert_rebound_workflow_code(
                    root,
                    "PUBLICATION_POLICY",
                    "candidate evidence copy closure changed",
                )

        root = self._fixture()
        workflow = root / verifier.POLARIS_IMAGE_WORKFLOW
        workflow_text = workflow.read_text(encoding="utf-8")
        before, candidate = workflow_text.split(
            '          evidence_dir="${RUNNER_TEMP}/'
            'polaris-image-candidate"\n',
            maxsplit=1,
        )
        candidate_copy, after = candidate.split(
            "          test -s runtime-smoke.log\n",
            maxsplit=1,
        )
        destination = '            "${evidence_dir}/"\n'
        self.assertEqual(1, candidate_copy.count(destination))
        workflow.write_text(
            before
            + '          evidence_dir="${RUNNER_TEMP}/'
            'polaris-image-candidate"\n'
            + candidate_copy.replace(
                destination,
                "            unexpected-evidence.json \\\n" + destination,
                1,
            )
            + "          test -s runtime-smoke.log\n"
            + after,
            encoding="utf-8",
        )
        self._assert_rebound_workflow_code(
            root,
            "PUBLICATION_POLICY",
            "candidate evidence copy closure changed",
        )

    def test_promotion_evidence_copy_closure_is_exact_when_hashes_rebound(
        self,
    ) -> None:
        root = self._fixture()
        workflow = root / verifier.POLARIS_IMAGE_WORKFLOW
        workflow_text = workflow.read_text(encoding="utf-8")
        copy_line = (
            "          cp promotion-cosign-verify.json candidate-evidence/\n"
        )
        self.assertEqual(1, workflow_text.count(copy_line))
        workflow.write_text(
            workflow_text.replace(copy_line, "", 1),
            encoding="utf-8",
        )
        self._assert_rebound_workflow_code(
            root,
            "PUBLICATION_POLICY",
            "promotion evidence copy closure changed",
        )

    def test_candidate_crypto_bindings_cannot_be_weakened_when_hashes_rebound(
        self,
    ) -> None:
        cases = (
            (
                "if registry_bundle_matches != 1:",
                "if registry_bundle_matches < 1:",
            ),
            (
                'certificate.get("runInvocationURI") == invocation,',
                'certificate.get("runInvocationURI") is not None,',
            ),
        )
        for original, replacement in cases:
            with self.subTest(original=original):
                root = self._fixture()
                workflow = root / verifier.POLARIS_IMAGE_WORKFLOW
                workflow_text = workflow.read_text(encoding="utf-8")
                self.assertEqual(1, workflow_text.count(original))
                workflow.write_text(
                    workflow_text.replace(original, replacement, 1),
                    encoding="utf-8",
                )
                self._assert_rebound_workflow_code(
                    root,
                    "PUBLICATION_POLICY",
                    "candidate cryptographic evidence binding changed",
                )

    def test_promotion_crypto_bindings_cannot_be_weakened_when_hashes_rebound(
        self,
    ) -> None:
        root = self._fixture()
        workflow = root / verifier.POLARIS_IMAGE_WORKFLOW
        workflow_text = workflow.read_text(encoding="utf-8")
        original = 'and statement.get("predicate") == predicate'
        self.assertEqual(1, workflow_text.count(original))
        workflow.write_text(
            workflow_text.replace(original, "and predicate is not None", 1),
            encoding="utf-8",
        )
        self._assert_rebound_workflow_code(
            root,
            "PUBLICATION_POLICY",
            "promotion cryptographic evidence binding changed",
        )

    def test_promotion_attestation_revalidation_precedes_credentials(
        self,
    ) -> None:
        root = self._fixture()
        workflow = root / verifier.POLARIS_IMAGE_WORKFLOW
        workflow_text = workflow.read_text(encoding="utf-8")
        before, promote = workflow_text.split("\n  promote:\n", maxsplit=1)
        self.assertEqual(1, promote.count("cosign verify-blob-attestation"))
        workflow.write_text(
            before
            + "\n  promote:\n"
            + promote.replace(
                "cosign verify-blob-attestation",
                "cosign verify-attestation",
                1,
            ),
            encoding="utf-8",
        )
        self._assert_rebound_workflow_code(
            root,
            "PUBLICATION_POLICY",
            "credential-free candidate evidence revalidation changed",
        )

    def test_public_fingerprint_remains_gitleaks_safe_when_hash_is_rebound(
        self,
    ) -> None:
        grouped_assignment = (
            "SOURCE_SIGNING_KEY_FINGERPRINT: "
            f"{verifier.POLARIS_KEY_FINGERPRINT_GROUPED}"
        )
        canonical_assignment = (
            "SOURCE_SIGNING_KEY_FINGERPRINT: "
            f"{verifier.POLARIS_KEY_FINGERPRINT}"
        )
        normalization = (
            'export SOURCE_SIGNING_KEY_FINGERPRINT="'
            '${SOURCE_SIGNING_KEY_FINGERPRINT// /}"'
        )
        cases = (
            (
                "canonical-assignment",
                grouped_assignment,
                canonical_assignment,
                verifier.POLARIS_KEY_FINGERPRINT_GROUPED,
            ),
            (
                "missing-normalization",
                normalization,
                'echo "fingerprint normalization removed"',
                normalization,
            ),
        )
        for label, before, after, detail in cases:
            with self.subTest(label=label):
                root = self._fixture()
                workflow = root / verifier.POLARIS_IMAGE_WORKFLOW
                workflow_text = workflow.read_text(encoding="utf-8")
                self.assertIn(before, workflow_text)
                workflow.write_text(
                    workflow_text.replace(before, after, 1),
                    encoding="utf-8",
                )
                workflow_sha256 = verifier._sha256(workflow)
                with mock.patch.object(
                    verifier,
                    "POLARIS_IMAGE_WORKFLOW_SHA256",
                    workflow_sha256,
                ):
                    with self.assertRaises(verifier.ContractError) as raised:
                        verifier._audit_image_publication_files(root)
                self.assertEqual("PUBLICATION_POLICY", raised.exception.code)
                self.assertIn("missing required controls", raised.exception.detail)
                self.assertIn(detail, raised.exception.detail)

    def test_image_admission_cannot_skip_evidence_review(self) -> None:
        root = self._fixture()

        def mutate(value: dict[str, object]) -> None:
            value["image_publication"]["admitted"] = True  # type: ignore[index]

        self._rewrite_json(root, verifier.POLARIS_ADMISSION, mutate)
        self._assert_code(
            root,
            "POLARIS_ADMISSION",
            "image_publication.admitted",
        )

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

    def test_snapshot_reference_cannot_drift_after_main_run(self) -> None:
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
        self._audit(root)

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
        self._audit(root)

    def test_extra_dependency_evidence_is_forbidden_while_review_pending(
        self,
    ) -> None:
        root = self._fixture()
        evidence = root / "bootstrap/polaris/v1.6.0/evidence/claim.json"
        evidence.write_text("{}\n", encoding="utf-8")
        self._assert_code(
            root,
            "DEPENDENCY_EVIDENCE",
            "inventory must be closed",
        )

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
        self._audit(root)

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
        self._audit(root)

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
        self._audit(root)

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
        self._audit(root)

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
        self._audit(root)

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
                self._audit(root)

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
                self._audit(root)

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
                self._audit(root)

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
                self._audit(root)

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
        self._audit(root)

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
        self._audit(root)

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
        self._audit(root)

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
        self._audit(root)

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
                self._audit(root)

    def test_disabled_iceberg_flag_is_not_a_catalog_identity_field(self) -> None:
        root = self._fixture()
        relative = "deploy/gitops/object-storage/statefulset.yaml"
        destination = root / relative
        destination.parent.mkdir(parents=True)
        shutil.copy2(ROOT / relative, destination)
        self._audit(root)

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
        self._audit(root)


if __name__ == "__main__":
    unittest.main()
