#!/usr/bin/env python3
"""Export, inventory, and restore the local-lite S3 bucket without an SDK."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import sys
import tempfile
from pathlib import Path

from object_storage_s3 import (
    DEFAULT_ENDPOINT,
    DEFAULT_REGION,
    S3ClientError,
    SigV4S3Client,
    load_credentials,
)


DEFAULT_BUCKET = "shirokuma-lakehouse"
EXPORT_KIND = "shirokuma-object-storage-export"
BUCKET_NAME = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
OBJECT_FILE = re.compile(r"^objects/[0-9a-f]{2}/[0-9a-f]{64}\.bin$")
IO_CHUNK_SIZE = 1024 * 1024


class BackupError(RuntimeError):
    """Safe-to-print backup contract error."""


def _has_symlink_component(path: Path) -> bool:
    absolute = path.expanduser().absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if os.path.lexists(current) and current.is_symlink():
            return True
    return False


def host_export_root(environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    configured = env.get("SHIROKUMA_HOST_EXPORT_ROOT")
    if not configured:
        raise BackupError("SHIROKUMA_HOST_EXPORT_ROOT is required")
    if platform.system() != "Darwin":
        raise BackupError("backup files must be handled on the macOS host, outside Colima")
    candidate = Path(configured).expanduser()
    unchecked = candidate.resolve(strict=False)
    temporary_roots = {Path("/tmp").resolve(), Path("/private/tmp").resolve()}
    if any(
        unchecked == temporary or temporary in unchecked.parents
        for temporary in temporary_roots
    ):
        raise BackupError("host export root must be durable, not a temporary directory")
    if _has_symlink_component(candidate):
        raise BackupError("host export root must not traverse a symlink")
    try:
        root = candidate.resolve(strict=True)
    except OSError as error:
        raise BackupError("host export root must already exist") from error
    if not root.is_dir():
        raise BackupError("host export root must be a directory")
    colima_root = (Path.home() / ".colima").resolve()
    if root == colima_root or colima_root in root.parents:
        raise BackupError("host export root must not be inside the Colima runtime")
    return root


def guarded_path(path: Path, root: Path, *, must_exist: bool) -> Path:
    expanded = path.expanduser()
    if _has_symlink_component(expanded):
        raise BackupError("backup path must not traverse a symlink")
    try:
        resolved = expanded.resolve(strict=must_exist)
    except OSError as error:
        raise BackupError("backup path does not exist") from error
    try:
        relative = resolved.relative_to(root)
    except ValueError as error:
        raise BackupError("backup path must be inside SHIROKUMA_HOST_EXPORT_ROOT") from error
    if relative == Path("."):
        raise BackupError("backup path must be a child of SHIROKUMA_HOST_EXPORT_ROOT")
    return resolved


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise BackupError("cannot sync export directory") from error


def _write_new_file(path: Path, body: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        raise BackupError("cannot write export file") from error


def _inspect_export_object(
    path: Path, export_root: Path, *, collect_body: bool
) -> tuple[int, str, bytes | None]:
    if _has_symlink_component(path):
        raise BackupError("export object path must not traverse a symlink")
    try:
        path.resolve(strict=True).relative_to(export_root)
    except (OSError, ValueError) as error:
        raise BackupError("cannot read export object") from error

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise BackupError("export object must be a regular non-symlink file")
            digest = hashlib.sha256()
            size = 0
            chunks: list[bytes] | None = [] if collect_body else None
            while True:
                chunk = stream.read(IO_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
                if chunks is not None:
                    chunks.append(chunk)
    except OSError as error:
        raise BackupError("cannot read export object") from error

    body = b"".join(chunks) if chunks is not None else None
    return size, digest.hexdigest(), body


def validate_bucket(bucket: str) -> str:
    if not BUCKET_NAME.fullmatch(bucket) or ".." in bucket:
        raise BackupError("bucket name is invalid")
    return bucket


def client_from_environment() -> SigV4S3Client:
    return SigV4S3Client(
        os.environ.get("S3_ENDPOINT", DEFAULT_ENDPOINT),
        load_credentials(),
        os.environ.get("S3_REGION", DEFAULT_REGION),
    )


def inventory(
    client: SigV4S3Client, bucket: str, prefix: str
) -> dict[str, object]:
    objects = sorted(client.list_objects(bucket, prefix), key=lambda item: item.key)
    return {
        "schema_version": 1,
        "kind": "shirokuma-object-storage-inventory",
        "bucket": bucket,
        "prefix": prefix,
        "object_count": len(objects),
        "total_bytes": sum(item.size for item in objects),
        "objects": [
            {"key": item.key, "size": item.size, "etag": item.etag}
            for item in objects
        ],
    }


def export_bucket(
    client: SigV4S3Client,
    bucket: str,
    prefix: str,
    output: Path,
    root: Path,
) -> dict[str, object]:
    destination = guarded_path(output, root, must_exist=False)
    if os.path.lexists(destination):
        raise BackupError("export output must not already exist")
    staging: Path | None = None
    published = False
    try:
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".{destination.name}.staging-", dir=destination.parent
            )
        )
        staging = guarded_path(staging, root, must_exist=True)

        listed = sorted(client.list_objects(bucket, prefix), key=lambda item: item.key)
        records: list[dict[str, object]] = []
        seen_keys: set[str] = set()
        total_bytes = 0
        for item in listed:
            if item.key in seen_keys:
                raise BackupError("S3 inventory contains a duplicate object key")
            seen_keys.add(item.key)
            body = client.get_object(bucket, item.key)
            if len(body) != item.size:
                raise BackupError("downloaded object size does not match S3 inventory")
            digest = hashlib.sha256(body).hexdigest()
            relative = Path("objects") / digest[:2] / f"{digest}.bin"
            target = staging / relative
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if os.path.lexists(target):
                size, existing_digest, _ = _inspect_export_object(
                    target, staging, collect_body=False
                )
                if size != len(body) or existing_digest != digest:
                    raise BackupError("deduplicated export object failed content binding")
            else:
                _write_new_file(target, body)
            records.append(
                {
                    "key": item.key,
                    "size": item.size,
                    "etag": item.etag,
                    "sha256": digest,
                    "file": relative.as_posix(),
                }
            )
            total_bytes += item.size

        manifest = {
            "schema_version": 1,
            "kind": EXPORT_KIND,
            "created_at": dt.datetime.now(dt.timezone.utc)
            .replace(microsecond=0)
            .isoformat(),
            "bucket": bucket,
            "prefix": prefix,
            "object_count": len(records),
            "total_bytes": total_bytes,
            "objects": records,
        }
        manifest_body = (
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        _write_new_file(staging / "manifest.json", manifest_body)
        for directory, _, _ in os.walk(staging, topdown=False):
            _fsync_directory(Path(directory))

        if os.path.lexists(destination):
            raise BackupError("export output appeared while export was running")
        os.replace(staging, destination)
        published = True
        staging = None
        _fsync_directory(destination.parent)
        return manifest
    except BaseException as error:
        cleanup = destination if published else staging
        if cleanup is not None and os.path.lexists(cleanup):
            shutil.rmtree(cleanup, ignore_errors=True)
            try:
                _fsync_directory(destination.parent)
            except BackupError:
                pass
        if isinstance(error, OSError):
            raise BackupError("cannot create or publish export") from error
        raise


def _load_manifest(source: Path) -> dict[str, object]:
    manifest_path = source / "manifest.json"
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise BackupError("platform cannot safely open export manifest")
    descriptor = -1
    try:
        descriptor = os.open(manifest_path, os.O_RDONLY | no_follow)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise BackupError("export manifest must be a regular non-symlink file")
        stream = os.fdopen(descriptor, "r", encoding="utf-8")
        descriptor = -1
        with stream:
            value = json.load(stream)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BackupError("cannot parse export manifest") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(value, dict):
        raise BackupError("export manifest root must be an object")
    if value.get("schema_version") != 1 or value.get("kind") != EXPORT_KIND:
        raise BackupError("export manifest schema is unsupported")
    return value


def restore_bucket(
    client: SigV4S3Client,
    bucket: str,
    source: Path,
    root: Path,
) -> int:
    export_root = guarded_path(source, root, must_exist=True)
    if not export_root.is_dir():
        raise BackupError("restore input must be an export directory")
    manifest = _load_manifest(export_root)
    if manifest.get("bucket") != bucket:
        raise BackupError("restore bucket must exactly match the export manifest")
    records = manifest.get("objects")
    if not isinstance(records, list):
        raise BackupError("export manifest objects must be an array")
    object_count = manifest.get("object_count")
    if (
        type(object_count) is not int
        or object_count < 0
        or object_count != len(records)
    ):
        raise BackupError("export manifest object count is inconsistent")

    validated: list[tuple[str, Path, int, str]] = []
    seen_keys: set[str] = set()
    declared_total_bytes = 0
    validated_blobs: dict[Path, tuple[int, str]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise BackupError("export manifest object entry is malformed")
        key = record.get("key")
        relative = record.get("file")
        digest = record.get("sha256")
        size = record.get("size")
        if (
            not isinstance(key, str)
            or not key
            or not isinstance(relative, str)
            or not OBJECT_FILE.fullmatch(relative)
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or type(size) is not int
            or size < 0
        ):
            raise BackupError("export manifest object entry is malformed")
        if key in seen_keys:
            raise BackupError("export manifest contains a duplicate object key")
        seen_keys.add(key)
        declared_total_bytes += size
        if relative != f"objects/{digest[:2]}/{digest}.bin":
            raise BackupError("export object path is not bound to its digest")
        object_path = export_root / relative
        if object_path not in validated_blobs:
            actual_size, actual_digest, _ = _inspect_export_object(
                object_path, export_root, collect_body=False
            )
            validated_blobs[object_path] = (actual_size, actual_digest)
        actual_size, actual_digest = validated_blobs[object_path]
        if actual_size != size or actual_digest != digest:
            raise BackupError("export object failed size or SHA-256 verification")
        validated.append((key, object_path, size, digest))
    total_bytes = manifest.get("total_bytes")
    if (
        type(total_bytes) is not int
        or total_bytes < 0
        or total_bytes != declared_total_bytes
    ):
        raise BackupError("export manifest total bytes is inconsistent")
    if client.list_objects(bucket, ""):
        raise BackupError("restore target bucket must be empty")

    restored = 0
    for key, object_path, size, digest in validated:
        actual_size, actual_digest, body = _inspect_export_object(
            object_path, export_root, collect_body=True
        )
        if body is None or actual_size != size or actual_digest != digest:
            raise BackupError("export object changed after preflight verification")
        client.put_object(bucket, key, body)
        read_back = client.get_object(bucket, key)
        if len(read_back) != size or hashlib.sha256(read_back).hexdigest() != digest:
            raise BackupError("restored object failed read-back verification")
        restored += 1
        del body, read_back
    return restored


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="SeaweedFS local-lite export, restore, and inventory"
    )
    subparsers = root.add_subparsers(dest="command", required=True)
    for name in ("inventory", "export", "restore"):
        command = subparsers.add_parser(name)
        command.add_argument(
            "--bucket", default=os.environ.get("S3_BUCKET", DEFAULT_BUCKET)
        )
        if name in {"inventory", "export"}:
            command.add_argument("--prefix", default="")
        if name == "export":
            command.add_argument("--output", type=Path, required=True)
        if name == "restore":
            command.add_argument("--input", type=Path, required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        bucket = validate_bucket(args.bucket)
        client = client_from_environment()
        if args.command == "inventory":
            print(json.dumps(inventory(client, bucket, args.prefix), indent=2, sort_keys=True))
        elif args.command == "export":
            manifest = export_bucket(
                client, bucket, args.prefix, args.output, host_export_root()
            )
            print(
                "object-storage-backup: exported "
                f"objects={manifest['object_count']} bytes={manifest['total_bytes']}"
            )
        elif args.command == "restore":
            restored = restore_bucket(client, bucket, args.input, host_export_root())
            print(f"object-storage-backup: restored objects={restored}")
        return 0
    except (BackupError, S3ClientError) as error:
        print(f"object-storage-backup: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
