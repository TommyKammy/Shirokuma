from __future__ import annotations

import gc
import hashlib
import json
import sys
import tempfile
import tracemalloc
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import object_storage_backup  # noqa: E402
from object_storage_s3 import S3ClientError, S3Object  # noqa: E402


BUCKET = "shirokuma-lakehouse"


class ExportClient:
    def __init__(
        self, objects: dict[str, bytes], *, fail_on_get: str | None = None
    ) -> None:
        self.objects = objects
        self.fail_on_get = fail_on_get

    def list_objects(self, bucket: str, prefix: str = "") -> list[S3Object]:
        self._assert_bucket(bucket)
        return [
            S3Object(key=key, size=len(body), etag="unused")
            for key, body in sorted(self.objects.items())
            if key.startswith(prefix)
        ]

    def get_object(self, bucket: str, key: str) -> bytes:
        self._assert_bucket(bucket)
        if key == self.fail_on_get:
            raise S3ClientError("injected export download failure")
        return self.objects[key]

    @staticmethod
    def _assert_bucket(bucket: str) -> None:
        if bucket != BUCKET:
            raise AssertionError(f"unexpected bucket: {bucket}")


class BoundedRestoreClient:
    def __init__(self) -> None:
        self.puts: list[str] = []
        self._current_key: str | None = None
        self._current_body: bytes | None = None

    def put_object(self, bucket: str, key: str, body: bytes) -> None:
        if bucket != BUCKET:
            raise AssertionError(f"unexpected bucket: {bucket}")
        self.puts.append(key)
        self._current_key = key
        self._current_body = body

    def get_object(self, bucket: str, key: str) -> bytes:
        if bucket != BUCKET or key != self._current_key or self._current_body is None:
            raise AssertionError("read-back did not match the latest upload")
        body = self._current_body
        self._current_key = None
        self._current_body = None
        return body


def build_export(root: Path, payloads: dict[str, bytes]) -> tuple[Path, dict[str, object]]:
    source = root / "export"
    source.mkdir(mode=0o700)
    records: list[dict[str, object]] = []
    for key, body in payloads.items():
        digest = hashlib.sha256(body).hexdigest()
        relative = Path("objects") / digest[:2] / f"{digest}.bin"
        target = source / relative
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(body)
            target.chmod(0o600)
        records.append(
            {
                "key": key,
                "size": len(body),
                "etag": "unused",
                "sha256": digest,
                "file": relative.as_posix(),
            }
        )
    manifest: dict[str, object] = {
        "schema_version": 1,
        "kind": object_storage_backup.EXPORT_KIND,
        "bucket": BUCKET,
        "prefix": "",
        "object_count": len(records),
        "total_bytes": sum(len(body) for body in payloads.values()),
        "objects": records,
    }
    manifest_path = source / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    manifest_path.chmod(0o600)
    return source, manifest


class AtomicExportTests(unittest.TestCase):
    def test_duplicate_listing_is_not_published(self) -> None:
        client = ExportClient({"duplicate": b"payload"})
        client.list_objects = mock.Mock(
            return_value=[
                S3Object(key="duplicate", size=7, etag="first"),
                S3Object(key="duplicate", size=7, etag="second"),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            destination = root / "export"
            with self.assertRaisesRegex(
                object_storage_backup.BackupError, "duplicate object key"
            ):
                object_storage_backup.export_bucket(
                    client, BUCKET, "", destination, root
                )
            self.assertFalse(destination.exists())
            self.assertEqual(list(root.glob(".export.staging-*")), [])

    def test_download_failure_removes_staging_and_allows_same_path_retry(self) -> None:
        objects = {"a": b"first", "b": b"second"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            destination = root / "export"
            with self.assertRaisesRegex(S3ClientError, "injected"):
                object_storage_backup.export_bucket(
                    ExportClient(objects, fail_on_get="b"),
                    BUCKET,
                    "",
                    destination,
                    root,
                )
            self.assertFalse(destination.exists())
            self.assertEqual(list(root.glob(".export.staging-*")), [])

            manifest = object_storage_backup.export_bucket(
                ExportClient(objects), BUCKET, "", destination, root
            )
            self.assertEqual(manifest["object_count"], 2)
            self.assertTrue((destination / "manifest.json").is_file())
            self.assertEqual(list(root.glob(".export.staging-*")), [])

    def test_publish_failure_removes_staging_and_final_destination(self) -> None:
        objects = {"only": b"payload"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            destination = root / "export"
            with mock.patch.object(
                object_storage_backup.os,
                "replace",
                side_effect=OSError("injected rename failure"),
            ):
                with self.assertRaisesRegex(
                    object_storage_backup.BackupError, "publish"
                ):
                    object_storage_backup.export_bucket(
                        ExportClient(objects), BUCKET, "", destination, root
                    )
            self.assertFalse(destination.exists())
            self.assertEqual(list(root.glob(".export.staging-*")), [])

            object_storage_backup.export_bucket(
                ExportClient(objects), BUCKET, "", destination, root
            )
            self.assertTrue(destination.is_dir())


class TwoPassRestoreTests(unittest.TestCase):
    def test_manifest_is_opened_without_following_a_final_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, _ = build_export(root, {"object": b"payload"})
            with mock.patch.object(
                object_storage_backup.os,
                "open",
                wraps=object_storage_backup.os.open,
            ) as safe_open:
                restored = object_storage_backup.restore_bucket(
                    BoundedRestoreClient(), BUCKET, source, root
                )
            self.assertEqual(restored, 1)
            manifest_open = next(
                call
                for call in safe_open.call_args_list
                if Path(call.args[0]).name == "manifest.json"
            )
            self.assertTrue(
                manifest_open.args[1] & object_storage_backup.os.O_NOFOLLOW
            )

    def test_restore_memory_is_bounded_by_one_large_object(self) -> None:
        blob_size = 2 * 1024 * 1024
        payloads = {
            f"large-{index}.bin": bytes([index + 1]) * blob_size
            for index in range(8)
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, _ = build_export(root, payloads)
            del payloads
            gc.collect()

            client = BoundedRestoreClient()
            tracemalloc.start()
            try:
                restored = object_storage_backup.restore_bucket(
                    client, BUCKET, source, root
                )
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()

            self.assertEqual(restored, 8)
            self.assertEqual(len(client.puts), 8)
            self.assertLess(
                peak,
                12 * 1024 * 1024,
                f"restore retained more than one large object at a time: peak={peak}",
            )

    def test_corrupt_later_blob_blocks_every_upload(self) -> None:
        payloads = {"first": b"valid-first", "second": b"valid-second"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, manifest = build_export(root, payloads)
            second = manifest["objects"][1]
            corrupt_path = source / second["file"]
            corrupt_path.write_bytes(b"X" * second["size"])

            client = BoundedRestoreClient()
            with self.assertRaisesRegex(
                object_storage_backup.BackupError, "SHA-256"
            ):
                object_storage_backup.restore_bucket(client, BUCKET, source, root)
            self.assertEqual(client.puts, [])

    def test_duplicate_content_deduplicates_and_duplicate_keys_fail_closed(self) -> None:
        payload = b"same-content"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            destination = root / "export"
            manifest = object_storage_backup.export_bucket(
                ExportClient({"first": payload, "second": payload}),
                BUCKET,
                "",
                destination,
                root,
            )
            records = manifest["objects"]
            self.assertEqual(records[0]["file"], records[1]["file"])
            self.assertEqual(len(list(destination.rglob("*.bin"))), 1)

            duplicate = dict(records[0])
            records.append(duplicate)
            manifest["object_count"] = 3
            manifest["total_bytes"] = len(payload) * 3
            (destination / "manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            client = BoundedRestoreClient()
            with self.assertRaisesRegex(
                object_storage_backup.BackupError, "duplicate object key"
            ):
                object_storage_backup.restore_bucket(
                    client, BUCKET, destination, root
                )
            self.assertEqual(client.puts, [])


if __name__ == "__main__":
    unittest.main()
