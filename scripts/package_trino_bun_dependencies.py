#!/usr/bin/env python3
"""Create and verify the closed Trino 483 Bun package cache."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import stat
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterable, Mapping


SCHEMA_VERSION = 1
COMPONENT = "trino"
VERSION = "483"
PLATFORM = "linux/arm64"
BUN_VERSION = "v1.3.14"
BUN_CACHE_DIRECTORY = "/bun-cache"
BUN_REGISTRY = "https://registry.npmjs.org/"
ARCHIVE_PREFIX = PurePosixPath("bun-cache")
REGULAR_MODES = {"0644", "0755"}
SYMLINK_MODE = "0777"
LOCKFILES = [
    {
        "path": "core/trino-web-ui/src/main/resources/webapp/bun.lock",
        "sha256": "70da1dad7c6f45743637cba7dde948793d787b1ced1382e90966d60fe17dc885",
    },
    {
        "path": "core/trino-web-ui/src/main/resources/webapp-legacy/src/bun.lock",
        "sha256": "0ca8b926ea0a2af3fff339b43c52de03a8f99c4aa9ba1d4c2ecd081bcd715ad3",
    },
]
MAX_FILE_COUNT = 100_000
MAX_SYMLINK_COUNT = 5_000
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_PATH_BYTES = 1_024
MAX_LINK_BYTES = 1_024


class BunSnapshotError(RuntimeError):
    """Raised when the Bun dependency snapshot violates its closed contract."""


def _fail(message: str) -> None:
    raise BunSnapshotError(message)


def _canonical_relative(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        _fail("Bun cache path must be a non-empty string")
    if "\\" in value or "\x00" in value or any(ord(char) < 32 for char in value):
        _fail(f"Bun cache path contains forbidden characters: {value!r}")
    if len(value.encode("utf-8")) > MAX_PATH_BYTES:
        _fail(f"Bun cache path exceeds the byte limit: {value!r}")
    relative = PurePosixPath(value)
    if relative.is_absolute() or relative.as_posix() != value:
        _fail(f"Bun cache path is not canonical: {value!r}")
    if any(part in {"", ".", ".."} for part in relative.parts):
        _fail(f"Bun cache path contains an unsafe segment: {value!r}")
    return relative


def _regular_stat(path: Path) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        _fail(f"cannot inspect Bun cache file {path}: {error}")
    if not stat.S_ISREG(metadata.st_mode):
        _fail(f"Bun cache entry is not a regular file: {path}")
    if metadata.st_size > MAX_FILE_BYTES:
        _fail(f"Bun cache file exceeds the byte limit: {path}")
    return metadata


def _open_regular(path: Path) -> tuple[BinaryIO, os.stat_result]:
    expected = _regular_stat(path)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        observed = os.fstat(descriptor)
    except OSError as error:
        _fail(f"cannot open Bun cache file {path}: {error}")
    if (
        not stat.S_ISREG(observed.st_mode)
        or observed.st_dev != expected.st_dev
        or observed.st_ino != expected.st_ino
        or observed.st_size != expected.st_size
    ):
        os.close(descriptor)
        _fail(f"Bun cache file changed while opening: {path}")
    return os.fdopen(descriptor, "rb"), observed


def _sha256_file(path: Path) -> str:
    stream, _ = _open_regular(path)
    digest = hashlib.sha256()
    with stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_mode(metadata: os.stat_result) -> str:
    return "0755" if stat.S_IMODE(metadata.st_mode) & 0o111 else "0644"


def _validated_link_target(root: Path, path: Path) -> str:
    try:
        target = os.readlink(path)
    except OSError as error:
        _fail(f"cannot read Bun cache symlink {path}: {error}")
    prefix = f"{BUN_CACHE_DIRECTORY}/"
    if (
        not target.startswith(prefix)
        or len(target.encode("utf-8")) > MAX_LINK_BYTES
        or "\\" in target
        or "\x00" in target
        or any(ord(char) < 32 for char in target)
    ):
        _fail(f"Bun cache symlink target is outside {BUN_CACHE_DIRECTORY}: {path}")
    relative = _canonical_relative(target[len(prefix) :])
    destination = root.joinpath(*relative.parts)
    try:
        metadata = destination.lstat()
    except OSError as error:
        _fail(f"Bun cache symlink target is missing for {path}: {error}")
    if not stat.S_ISDIR(metadata.st_mode):
        _fail(f"Bun cache symlink target must be a real directory: {path}")
    return target


def _cache_entries(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    if not root.is_dir() or root.is_symlink():
        _fail("Bun cache root must be a real directory")
    discovered: list[tuple[Path, bool]] = []
    for directory, directory_names, filenames in os.walk(
        root, topdown=True, followlinks=False
    ):
        current = Path(directory)
        retained_directories: list[str] = []
        for name in directory_names:
            child = current / name
            if child.is_symlink():
                discovered.append((child, True))
            else:
                retained_directories.append(name)
        directory_names[:] = retained_directories
        for name in filenames:
            child = current / name
            discovered.append((child, child.is_symlink()))

    discovered.sort(
        key=lambda item: item[0].relative_to(root).as_posix().encode("utf-8")
    )
    records: list[tuple[Path, dict[str, Any]]] = []
    observed: set[str] = set()
    file_count = 0
    symlink_count = 0
    total_bytes = 0
    for path, is_symlink in discovered:
        relative = _canonical_relative(path.relative_to(root).as_posix())
        identity = relative.as_posix().casefold()
        if identity in observed:
            _fail(f"case-insensitive duplicate Bun cache path: {relative}")
        observed.add(identity)
        if is_symlink:
            symlink_count += 1
            if symlink_count > MAX_SYMLINK_COUNT:
                _fail("Bun cache exceeds the symlink-count limit")
            record = {
                "path": relative.as_posix(),
                "type": "symlink",
                "mode": SYMLINK_MODE,
                "target": _validated_link_target(root, path),
            }
        else:
            metadata = _regular_stat(path)
            file_count += 1
            if file_count > MAX_FILE_COUNT:
                _fail("Bun cache exceeds the file-count limit")
            total_bytes += metadata.st_size
            if total_bytes > MAX_TOTAL_BYTES:
                _fail("Bun cache exceeds the total byte limit")
            record = {
                "path": relative.as_posix(),
                "type": "file",
                "mode": _normalized_mode(metadata),
                "size": metadata.st_size,
                "sha256": _sha256_file(path),
            }
        records.append((path, record))
    if not records or not file_count or not symlink_count:
        _fail("Bun cache must contain regular files and cache alias symlinks")
    return records


def _manifest_from_entries(
    entries: Iterable[tuple[Path, Mapping[str, Any]]],
) -> dict[str, Any]:
    records = [record for _, record in entries]
    return {
        "schema_version": SCHEMA_VERSION,
        "component": COMPONENT,
        "version": VERSION,
        "platform": PLATFORM,
        "bun_version": BUN_VERSION,
        "cache_directory": BUN_CACHE_DIRECTORY,
        "registry": BUN_REGISTRY,
        "lockfiles": LOCKFILES,
        "file_count": sum(record["type"] == "file" for record in records),
        "symlink_count": sum(record["type"] == "symlink" for record in records),
        "total_bytes": sum(
            record["size"] for record in records if record["type"] == "file"
        ),
        "entries": records,
    }


def build_manifest(cache: Path) -> dict[str, Any]:
    return _manifest_from_entries(_cache_entries(cache))


def _manifest_bytes(manifest: Mapping[str, Any]) -> bytes:
    return (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _safe_output(path: Path, label: str) -> None:
    if not path.parent.is_dir() or path.parent.is_symlink():
        _fail(f"{label} parent must be a real directory")
    if path.exists() or path.is_symlink():
        _fail(f"{label} must not already exist")


def _tar_info(name: str, mode: int) -> tarfile.TarInfo:
    member = tarfile.TarInfo(name=name)
    member.uid = 0
    member.gid = 0
    member.uname = ""
    member.gname = ""
    member.mtime = 0
    member.mode = mode
    return member


def _write_archive(
    cache: Path,
    records: Iterable[tuple[Path, Mapping[str, Any]]],
    archive: Path,
) -> None:
    del cache
    try:
        with archive.open("xb") as raw:
            with gzip.GzipFile(
                filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0
            ) as compressed:
                with tarfile.open(
                    fileobj=compressed, mode="w", format=tarfile.GNU_FORMAT
                ) as tar:
                    for path, record in records:
                        name = (ARCHIVE_PREFIX / record["path"]).as_posix()
                        if record["type"] == "symlink":
                            member = _tar_info(name, int(SYMLINK_MODE, 8))
                            member.type = tarfile.SYMTYPE
                            member.linkname = record["target"]
                            member.size = 0
                            tar.addfile(member)
                            continue
                        stream, metadata = _open_regular(path)
                        if metadata.st_size != record["size"]:
                            stream.close()
                            _fail(f"Bun cache file size changed: {path}")
                        member = _tar_info(name, int(record["mode"], 8))
                        member.type = tarfile.REGTYPE
                        member.size = metadata.st_size
                        with stream:
                            tar.addfile(member, stream)
    except (OSError, tarfile.TarError) as error:
        archive.unlink(missing_ok=True)
        _fail(f"cannot create Bun cache archive: {error}")


def create_snapshot(cache: Path, descriptor: Path, archive: Path) -> None:
    _safe_output(descriptor, "Bun cache descriptor")
    _safe_output(archive, "Bun cache archive")
    records = _cache_entries(cache)
    manifest = _manifest_from_entries(records)
    try:
        descriptor.write_bytes(_manifest_bytes(manifest))
        _write_archive(cache, records, archive)
        verify_snapshot(descriptor, archive, None)
    except (OSError, BunSnapshotError):
        descriptor.unlink(missing_ok=True)
        archive.unlink(missing_ok=True)
        raise


def _load_manifest(path: Path) -> dict[str, Any]:
    _regular_stat(path)
    try:
        raw = path.read_bytes()
        manifest = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        _fail(f"cannot load Bun cache manifest: {error}")
    if not isinstance(manifest, dict) or raw != _manifest_bytes(manifest):
        _fail("Bun cache manifest must be canonical JSON")
    expected_keys = {
        "schema_version",
        "component",
        "version",
        "platform",
        "bun_version",
        "cache_directory",
        "registry",
        "lockfiles",
        "file_count",
        "symlink_count",
        "total_bytes",
        "entries",
    }
    if set(manifest) != expected_keys:
        _fail("Bun cache manifest root is not closed-world")
    if (
        manifest["schema_version"] != SCHEMA_VERSION
        or manifest["component"] != COMPONENT
        or manifest["version"] != VERSION
        or manifest["platform"] != PLATFORM
        or manifest["bun_version"] != BUN_VERSION
        or manifest["cache_directory"] != BUN_CACHE_DIRECTORY
        or manifest["registry"] != BUN_REGISTRY
        or manifest["lockfiles"] != LOCKFILES
    ):
        _fail("Bun cache manifest identity or origin policy differs")
    entries = manifest["entries"]
    if not isinstance(entries, list) or not entries:
        _fail("Bun cache manifest entries must be a non-empty list")
    if len(entries) > MAX_FILE_COUNT + MAX_SYMLINK_COUNT:
        _fail("Bun cache manifest exceeds the entry-count limit")

    previous: bytes | None = None
    observed: set[str] = set()
    file_count = 0
    symlink_count = 0
    total_bytes = 0
    file_directories: set[str] = set()
    symlink_records: list[Mapping[str, Any]] = []
    for record in entries:
        if not isinstance(record, dict):
            _fail("Bun cache manifest entry must be an object")
        entry_type = record.get("type")
        expected_record_keys = (
            {"path", "type", "mode", "size", "sha256"}
            if entry_type == "file"
            else {"path", "type", "mode", "target"}
            if entry_type == "symlink"
            else set()
        )
        if not expected_record_keys or set(record) != expected_record_keys:
            _fail("Bun cache manifest entry is not closed-world")
        relative = _canonical_relative(record["path"])
        encoded = relative.as_posix().encode("utf-8")
        if previous is not None and encoded <= previous:
            _fail("Bun cache manifest paths are not bytewise sorted and unique")
        previous = encoded
        identity = relative.as_posix().casefold()
        if identity in observed:
            _fail("Bun cache manifest contains a case-insensitive duplicate")
        observed.add(identity)
        if entry_type == "file":
            file_count += 1
            if (
                record["mode"] not in REGULAR_MODES
                or type(record["size"]) is not int
                or record["size"] < 0
                or record["size"] > MAX_FILE_BYTES
                or not isinstance(record["sha256"], str)
                or len(record["sha256"]) != 64
                or any(char not in "0123456789abcdef" for char in record["sha256"])
            ):
                _fail("Bun cache regular-file record is invalid")
            total_bytes += record["size"]
            parent = relative.parent
            while parent != PurePosixPath("."):
                file_directories.add(parent.as_posix())
                parent = parent.parent
        else:
            symlink_count += 1
            target = record["target"]
            prefix = f"{BUN_CACHE_DIRECTORY}/"
            if (
                record["mode"] != SYMLINK_MODE
                or not isinstance(target, str)
                or not target.startswith(prefix)
                or len(target.encode("utf-8")) > MAX_LINK_BYTES
            ):
                _fail("Bun cache symlink record is invalid")
            target_relative = _canonical_relative(target[len(prefix) :])
            if target_relative.as_posix() == relative.as_posix():
                _fail("Bun cache symlink cannot target itself")
            symlink_records.append(record)
    if (
        file_count != manifest["file_count"]
        or symlink_count != manifest["symlink_count"]
        or total_bytes != manifest["total_bytes"]
        or file_count > MAX_FILE_COUNT
        or symlink_count > MAX_SYMLINK_COUNT
        or total_bytes > MAX_TOTAL_BYTES
        or not file_count
        or not symlink_count
    ):
        _fail("Bun cache manifest aggregate counts differ")
    prefix = f"{BUN_CACHE_DIRECTORY}/"
    for record in symlink_records:
        target_relative = record["target"][len(prefix) :]
        if target_relative not in file_directories:
            _fail("Bun cache symlink target has no retained package directory")
    return manifest


def _extract_regular(
    stream: BinaryIO, destination: Path, record: Mapping[str, Any]
) -> str:
    digest = hashlib.sha256()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(destination, flags, int(record["mode"], 8))
        with os.fdopen(descriptor, "wb") as output:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                output.write(chunk)
        destination.chmod(int(record["mode"], 8))
    except OSError as error:
        _fail(f"cannot extract Bun cache file {destination}: {error}")
    return digest.hexdigest()


def verify_snapshot(
    descriptor: Path, archive: Path, extract_root: Path | None
) -> None:
    manifest = _load_manifest(descriptor)
    _regular_stat(archive)
    expected = {
        (ARCHIVE_PREFIX / record["path"]).as_posix(): record
        for record in manifest["entries"]
    }
    if extract_root is not None:
        if extract_root.exists() or extract_root.is_symlink():
            _fail("Bun cache extraction root must not already exist")
        extract_root.mkdir(parents=True, mode=0o700)

    observed: set[str] = set()
    pending_links: list[tuple[Path, str]] = []
    try:
        with tarfile.open(archive, mode="r:gz") as tar:
            for member in tar:
                record = expected.get(member.name)
                if record is None or member.name in observed:
                    _fail(f"Bun cache archive path is unknown or duplicated: {member.name}")
                observed.add(member.name)
                if (
                    member.uid != 0
                    or member.gid != 0
                    or member.uname
                    or member.gname
                    or member.mtime != 0
                    or member.pax_headers
                ):
                    _fail(f"Bun cache archive metadata is not canonical: {member.name}")
                relative = _canonical_relative(record["path"])
                destination = (
                    extract_root.joinpath(*relative.parts)
                    if extract_root is not None
                    else None
                )
                if record["type"] == "symlink":
                    if (
                        not member.issym()
                        or member.size != 0
                        or member.linkname != record["target"]
                        or stat.S_IMODE(member.mode) != int(SYMLINK_MODE, 8)
                    ):
                        _fail(f"Bun cache archive symlink differs: {member.name}")
                    if destination is not None:
                        pending_links.append((destination, record["target"]))
                    continue
                if (
                    not member.isfile()
                    or member.size != record["size"]
                    or stat.S_IMODE(member.mode) != int(record["mode"], 8)
                ):
                    _fail(f"Bun cache archive file differs: {member.name}")
                stream = tar.extractfile(member)
                if stream is None:
                    _fail(f"cannot read Bun cache archive member: {member.name}")
                if destination is not None:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    digest = _extract_regular(stream, destination, record)
                else:
                    digest_builder = hashlib.sha256()
                    while chunk := stream.read(1024 * 1024):
                        digest_builder.update(chunk)
                    digest = digest_builder.hexdigest()
                stream.close()
                if digest != record["sha256"]:
                    _fail(f"Bun cache archive digest differs: {member.name}")
    except (OSError, tarfile.TarError) as error:
        _fail(f"cannot verify Bun cache archive: {error}")
    if observed != set(expected):
        missing = sorted(set(expected) - observed)
        _fail(f"Bun cache archive is incomplete: {missing[:5]!r}")

    if extract_root is not None:
        try:
            for destination, target in pending_links:
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(target, destination)
        except OSError as error:
            _fail(f"cannot extract Bun cache symlink: {error}")
        extracted = build_manifest(extract_root)
        if extracted != manifest:
            _fail("extracted Bun cache differs from the manifest")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--cache", type=Path, required=True)
    create.add_argument("--descriptor", type=Path, required=True)
    create.add_argument("--archive", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--descriptor", type=Path, required=True)
    verify.add_argument("--archive", type=Path, required=True)
    verify.add_argument("--extract-root", type=Path)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        if arguments.command == "create":
            create_snapshot(
                arguments.cache, arguments.descriptor, arguments.archive
            )
        else:
            verify_snapshot(
                arguments.descriptor, arguments.archive, arguments.extract_root
            )
    except BunSnapshotError as error:
        print(f"Trino Bun cache snapshot rejected: {error}", file=os.sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
