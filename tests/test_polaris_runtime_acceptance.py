from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in os.sys.path:
    os.sys.path.insert(0, str(SCRIPTS))

import polaris_runtime_acceptance as acceptance  # noqa: E402


class SecretDecodingTests(unittest.TestCase):
    def test_decode_secret_returns_value_without_changing_contract(self) -> None:
        secret = {"data": {"client_id": base64.b64encode(b"root").decode()}}
        self.assertEqual(acceptance._decode_secret(secret, "client_id"), "root")

    def test_decode_secret_rejects_invalid_base64(self) -> None:
        with self.assertRaisesRegex(acceptance.AcceptanceError, "invalid"):
            acceptance._decode_secret({"data": {"client_id": "%%%"}}, "client_id")


class HostBackupRootTests(unittest.TestCase):
    def test_accepts_private_non_temporary_host_directory(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            root.chmod(0o700)
            with mock.patch.object(acceptance.platform, "system", return_value="Darwin"):
                self.assertEqual(acceptance.host_backup_root(root), root.resolve())

    def test_rejects_group_readable_directory(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            root.chmod(0o750)
            with mock.patch.object(acceptance.platform, "system", return_value="Darwin"):
                with self.assertRaisesRegex(acceptance.AcceptanceError, "0700"):
                    acceptance.host_backup_root(root)

    def test_rejects_symlink_component(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir(mode=0o700)
            link = root / "link"
            link.symlink_to(target, target_is_directory=True)
            with mock.patch.object(acceptance.platform, "system", return_value="Darwin"):
                with self.assertRaisesRegex(acceptance.AcceptanceError, "symlink"):
                    acceptance.host_backup_root(link)


class DatabaseFingerprintTests(unittest.TestCase):
    def test_fingerprint_is_ordered_and_secret_free(self) -> None:
        outputs = iter(
            (
                b"public\tfirst\npublic\tsecond\n",
                b"2\t11111111111111111111111111111111\n",
                b"3\t22222222222222222222222222222222\n",
                b"33333333333333333333333333333333\n",
            )
        )
        with mock.patch.object(
            acceptance, "_postgres_shell", side_effect=lambda *args, **kwargs: next(outputs)
        ):
            result = acceptance.database_fingerprint(
                "colima-mac-studio-solo", "shirokuma-dev", "postgres-0", "polaris"
            )
        self.assertEqual(result["table_count"], 2)
        self.assertEqual(result["row_count"], 5)
        self.assertRegex(result["content_sha256"], r"^[0-9a-f]{64}$")
        self.assertNotIn("password", json.dumps(result).lower())

    def test_invalid_database_name_fails_before_a_command(self) -> None:
        with mock.patch.object(acceptance, "_postgres_shell") as command:
            with self.assertRaisesRegex(acceptance.AcceptanceError, "database name"):
                acceptance.database_fingerprint(
                    "context", "namespace", "pod", "unsafe;drop database"
                )
        command.assert_not_called()


class ReceiptTests(unittest.TestCase):
    def test_atomic_receipt_is_world_readable_and_contains_no_extra_file(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            output = Path(directory) / "receipt.json"
            acceptance._write_receipt(output, {"schema_version": 1, "ok": True})
            self.assertEqual(json.loads(output.read_text()), {"schema_version": 1, "ok": True})
            self.assertEqual(output.stat().st_mode & 0o777, 0o644)
            self.assertEqual(list(output.parent.glob(".*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
