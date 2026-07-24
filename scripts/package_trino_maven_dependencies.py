#!/usr/bin/env python3
"""Create and verify the closed Trino Maven dependency snapshot."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import stat
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterable, Mapping


SCHEMA_VERSION = 1
COMPONENT = "trino"
VERSION = "483"
ARCHIVE_PREFIX = PurePosixPath("repository")
CANONICAL_MODE = "0644"
ALLOWED_REPOSITORIES = {
    "central": "https://repo.maven.apache.org/maven2/",
    "confluent": "https://packages.confluent.io/maven/",
}
ALLOWED_ORIGIN_IDS = {
    **ALLOWED_REPOSITORIES,
    "shirokuma-central-fallback": ALLOWED_REPOSITORIES["central"],
}
EXCLUDED_RESOLVER_METADATA = {
    "_remote.repositories",
    "resolver-status.properties",
}
FORBIDDEN_SUFFIXES = (
    ".lastUpdated",
    ".lock",
    ".part",
    ".partial",
    ".tmp",
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_FILE_COUNT = 250_000
MAX_TOTAL_BYTES = 8 * 1024 * 1024 * 1024


class SnapshotError(ValueError):
    """Raised when a dependency snapshot violates the closed-world contract."""


def _fail(message: str) -> None:
    raise SnapshotError(message)


def _canonical_relative(value: str) -> PurePosixPath:
    if not isinstance(value, str):
        _fail("repository path must be a string")
    path = PurePosixPath(value)
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != value
    ):
        _fail(f"non-canonical repository path: {value!r}")
    return path


def _regular_stat(path: Path) -> os.stat_result:
    try:
        result = path.lstat()
    except OSError as error:
        _fail(f"cannot stat {path}: {error}")
    if not stat.S_ISREG(result.st_mode):
        _fail(f"snapshot entries must be regular files: {path}")
    if result.st_nlink != 1:
        _fail(f"hard-linked snapshot entry is forbidden: {path}")
    return result


def _sha256_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    _regular_stat(path)
    try:
        with path.open("rb") as stream:
            return _sha256_stream(stream)
    except OSError as error:
        _fail(f"cannot hash {path}: {error}")


def _marker_origins(directory: Path) -> dict[str, str]:
    marker = directory / "_remote.repositories"
    if not marker.exists():
        return {}
    _regular_stat(marker)
    result: dict[str, str] = {}
    try:
        lines = marker.read_text(encoding="iso-8859-1").splitlines()
    except OSError as error:
        _fail(f"cannot read Maven origin marker {marker}: {error}")
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([^<>=\r\n]+)(?:>|<)([^=\r\n]*)=", line)
        if match is None:
            _fail(f"unrecognized Maven origin marker entry in {marker}: {raw!r}")
        filename, repository_id = match.groups()
        if filename in result:
            _fail(f"duplicate Maven origin marker for {filename} in {marker}")
        if repository_id not in ALLOWED_ORIGIN_IDS:
            _fail(
                f"unknown Maven repository id {repository_id!r} "
                f"for {directory / filename}"
            )
        result[filename] = ALLOWED_ORIGIN_IDS[repository_id]
    return result


def _origin(path: Path, markers: Mapping[Path, Mapping[str, str]]) -> str:
    marker = markers.get(path.parent, {})
    origin = marker.get(path.name)
    if origin is None:
        metadata = re.fullmatch(r"maven-metadata-([A-Za-z0-9_.-]+)\.xml(?:\.sha1)?", path.name)
        if metadata is not None:
            repository_id = metadata.group(1)
            origin = ALLOWED_ORIGIN_IDS.get(repository_id)
    if origin is None:
        _fail(f"missing closed Maven repository origin for {path}")
    return origin


def _repository_files(root: Path) -> list[Path]:
    if not root.is_dir() or root.is_symlink():
        _fail("Maven repository root must be a real directory")
    files: list[Path] = []
    for directory, directory_names, filenames in os.walk(
        root, topdown=True, followlinks=False
    ):
        current = Path(directory)
        for name in directory_names:
            child = current / name
            if child.is_symlink():
                _fail(f"symlinked Maven repository directory is forbidden: {child}")
        for name in filenames:
            files.append(current / name)
    files.sort(key=lambda value: value.relative_to(root).as_posix().encode("utf-8"))
    if len(files) > MAX_FILE_COUNT:
        _fail("Maven dependency snapshot exceeds the file-count limit")
    return files


def build_manifest(repository: Path) -> dict[str, Any]:
    files = _repository_files(repository)
    markers = {
        path.parent: _marker_origins(path.parent)
        for path in files
        if path.name == "_remote.repositories"
    }
    records: list[dict[str, Any]] = []
    observed: set[str] = set()
    total_bytes = 0
    for path in files:
        relative = _canonical_relative(path.relative_to(repository).as_posix())
        if path.name in EXCLUDED_RESOLVER_METADATA:
            continue
        identity = relative.as_posix().casefold()
        if identity in observed:
            _fail(f"case-insensitive duplicate repository path: {relative}")
        observed.add(identity)
        if relative.parts[:2] == ("io", "trino"):
            _fail(f"Trino reactor output is forbidden in dependency input: {relative}")
        if path.name.endswith(FORBIDDEN_SUFFIXES):
            _fail(f"partial, lock, or temporary Maven file is forbidden: {relative}")
        metadata = _regular_stat(path)
        total_bytes += metadata.st_size
        if total_bytes > MAX_TOTAL_BYTES:
            _fail("Maven dependency snapshot exceeds the byte limit")
        records.append(
            {
                "path": relative.as_posix(),
                "size": metadata.st_size,
                "mode": CANONICAL_MODE,
                "sha256": _sha256_file(path),
                "repository_origin": _origin(path, markers),
            }
        )
    if not records:
        _fail("Maven dependency snapshot must not be empty")
    return {
        "schema_version": SCHEMA_VERSION,
        "component": COMPONENT,
        "version": VERSION,
        "repositories": ALLOWED_REPOSITORIES,
        "excluded_resolver_metadata": sorted(EXCLUDED_RESOLVER_METADATA),
        "file_count": len(records),
        "total_bytes": total_bytes,
        "files": records,
    }


def _manifest_bytes(manifest: Mapping[str, Any]) -> bytes:
    return (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_archive(
    repository: Path, manifest: Mapping[str, Any], archive: Path
) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    try:
        with archive.open("xb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.GNU_FORMAT) as tar:
                    for record in manifest["files"]:
                        source = repository / record["path"]
                        info = tarfile.TarInfo(
                            (ARCHIVE_PREFIX / record["path"]).as_posix()
                        )
                        info.size = record["size"]
                        info.mode = int(CANONICAL_MODE, 8)
                        info.uid = 0
                        info.gid = 0
                        info.uname = ""
                        info.gname = ""
                        info.mtime = 0
                        info.pax_headers = {}
                        with source.open("rb") as stream:
                            tar.addfile(info, stream)
    except (OSError, tarfile.TarError) as error:
        _fail(f"cannot create deterministic Maven archive: {error}")


def create_snapshot(repository: Path, descriptor: Path, archive: Path) -> None:
    if descriptor.exists() or archive.exists():
        _fail("snapshot outputs must not already exist")
    manifest = build_manifest(repository)
    descriptor.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor.write_bytes(_manifest_bytes(manifest))
    except OSError as error:
        _fail(f"cannot write Maven manifest: {error}")
    _write_archive(repository, manifest, archive)


def _load_manifest(path: Path) -> dict[str, Any]:
    _regular_stat(path)
    try:
        raw = path.read_bytes()
        manifest = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        _fail(f"cannot load Maven manifest: {error}")
    if not isinstance(manifest, dict):
        _fail("Maven manifest root must be an object")
    if raw != _manifest_bytes(manifest):
        _fail("Maven manifest is not canonical JSON")
    expected_keys = {
        "schema_version",
        "component",
        "version",
        "repositories",
        "excluded_resolver_metadata",
        "file_count",
        "total_bytes",
        "files",
    }
    if set(manifest) != expected_keys:
        _fail("Maven manifest root is not closed-world")
    if (
        type(manifest["schema_version"]) is not int
        or manifest["schema_version"] != SCHEMA_VERSION
        or manifest["component"] != COMPONENT
        or manifest["version"] != VERSION
        or manifest["repositories"] != ALLOWED_REPOSITORIES
        or manifest["excluded_resolver_metadata"]
        != sorted(EXCLUDED_RESOLVER_METADATA)
    ):
        _fail("Maven manifest identity or origin policy differs")
    records = manifest["files"]
    if not isinstance(records, list) or not records:
        _fail("Maven manifest files must be a non-empty list")
    if (
        type(manifest["file_count"]) is not int
        or manifest["file_count"] != len(records)
        or len(records) > MAX_FILE_COUNT
    ):
        _fail("Maven manifest file count differs")
    if (
        type(manifest["total_bytes"]) is not int
        or manifest["total_bytes"] < 0
    ):
        _fail("Maven manifest total byte count is invalid")
    expected_record_keys = {
        "path",
        "size",
        "mode",
        "sha256",
        "repository_origin",
    }
    previous: bytes | None = None
    total_bytes = 0
    for record in records:
        if not isinstance(record, dict) or set(record) != expected_record_keys:
            _fail("Maven manifest file record is not closed-world")
        relative = _canonical_relative(record["path"])
        encoded = relative.as_posix().encode("utf-8")
        if previous is not None and encoded <= previous:
            _fail("Maven manifest paths are not bytewise sorted and unique")
        previous = encoded
        if relative.parts[:2] == ("io", "trino"):
            _fail("Maven manifest contains a Trino reactor output")
        if record["mode"] != CANONICAL_MODE:
            _fail("Maven manifest mode is not canonical")
        if (
            type(record["size"]) is not int
            or record["size"] < 0
            or not isinstance(record["sha256"], str)
            or SHA256_RE.fullmatch(record["sha256"]) is None
        ):
            _fail("Maven manifest size or digest is invalid")
        if (
            not isinstance(record["repository_origin"], str)
            or record["repository_origin"] not in ALLOWED_REPOSITORIES.values()
        ):
            _fail("Maven manifest contains an unknown repository origin")
        total_bytes += record["size"]
    if total_bytes != manifest["total_bytes"] or total_bytes > MAX_TOTAL_BYTES:
        _fail("Maven manifest total byte count differs")
    return manifest


def verify_snapshot(
    descriptor: Path, archive: Path, extract_root: Path | None
) -> None:
    manifest = _load_manifest(descriptor)
    _regular_stat(archive)
    expected = {
        (ARCHIVE_PREFIX / record["path"]).as_posix(): record
        for record in manifest["files"]
    }
    if extract_root is not None:
        if extract_root.exists() or extract_root.is_symlink():
            _fail("extraction root must not already exist")
        extract_root.mkdir(parents=True, mode=0o700)
    observed: set[str] = set()
    try:
        with tarfile.open(archive, mode="r:gz") as tar:
            for member in tar:
                if not member.isfile():
                    _fail(f"archive contains a non-regular entry: {member.name}")
                if (
                    member.uid != 0
                    or member.gid != 0
                    or member.uname
                    or member.gname
                    or member.mtime != 0
                    or stat.S_IMODE(member.mode) != int(CANONICAL_MODE, 8)
                    or member.pax_headers
                ):
                    _fail(f"archive metadata is not canonical: {member.name}")
                record = expected.get(member.name)
                if record is None or member.name in observed:
                    _fail(f"archive path is unknown or duplicated: {member.name}")
                observed.add(member.name)
                if member.size != record["size"]:
                    _fail(f"archive size differs for {member.name}")
                stream = tar.extractfile(member)
                if stream is None:
                    _fail(f"cannot read archive member: {member.name}")
                digest = hashlib.sha256()
                destination = None
                if extract_root is not None:
                    relative = _canonical_relative(record["path"])
                    destination = extract_root.joinpath(*relative.parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    output = destination.open("xb")
                else:
                    output = None
                try:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                        if output is not None:
                            output.write(chunk)
                finally:
                    stream.close()
                    if output is not None:
                        output.close()
                        destination.chmod(int(CANONICAL_MODE, 8))
                if digest.hexdigest() != record["sha256"]:
                    _fail(f"archive digest differs for {member.name}")
    except (OSError, tarfile.TarError) as error:
        _fail(f"cannot verify Maven archive: {error}")
    if observed != set(expected):
        missing = sorted(set(expected) - observed)
        _fail(f"archive is incomplete: {missing[:5]!r}")
    if extract_root is not None:
        extracted = {
            path.relative_to(extract_root).as_posix(): path
            for path in _repository_files(extract_root)
        }
        records = {
            record["path"]: record
            for record in manifest["files"]
        }
        if set(extracted) != set(records):
            _fail("extracted Maven repository path set differs from the manifest")
        for relative, path in extracted.items():
            metadata = _regular_stat(path)
            record = records[relative]
            if (
                metadata.st_size != record["size"]
                or _sha256_file(path) != record["sha256"]
            ):
                _fail(
                    "extracted Maven repository content differs from the manifest: "
                    f"{relative}"
                )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--repository", type=Path, required=True)
    create.add_argument("--descriptor", type=Path, required=True)
    create.add_argument("--archive", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--descriptor", type=Path, required=True)
    verify.add_argument("--archive", type=Path, required=True)
    verify.add_argument("--extract-root", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "create":
            create_snapshot(args.repository, args.descriptor, args.archive)
        else:
            verify_snapshot(args.descriptor, args.archive, args.extract_root)
    except SnapshotError as error:
        print(f"trino dependency snapshot rejected: {error}", file=os.sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
