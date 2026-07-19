#!/usr/bin/env python3
"""Create and verify the closed Polaris Gradle dependency snapshot."""

from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import json
import os
import re
import secrets
import stat
import sys
import tarfile
import unicodedata
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterator, Iterable, Mapping


SCHEMA_VERSION = 1
COMPONENT = "polaris-gradle-dependencies"
POLARIS_VERSION = "1.6.0"
GRADLE_VERSION = "9.6.0"
PLATFORM = "linux/arm64"
SOURCE_ARCHIVE_SHA512 = (
    "d69b1a91e16e210a78dec327fc4725983b114fbec5d86d078a3827f35fe7dd5"
    "df3e4b12d18965e5a72eace65ad224aa007004ed61c66f9abb2efafc44ceac95b"
)
ARCHIVE_MEDIA_TYPE = "application/vnd.shirokuma.gradle-cache.v1.tar+gzip"
DESCRIPTOR_MEDIA_TYPE = (
    "application/vnd.shirokuma.gradle-dependency-descriptor.v1+json"
)
VERIFICATION_METADATA_MEDIA_TYPE = (
    "application/vnd.gradle.dependency-verification.v1+xml"
)
ARCHIVE_FILENAME = "polaris-gradle-dependencies-1.6.0.tar.gz"
VERIFICATION_METADATA_FILENAME = "verification-metadata.xml"
ALLOWED_ROOTS = (
    PurePosixPath("caches/modules-2/files-2.1"),
    PurePosixPath("caches/modules-2/metadata-2.107"),
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GRADLE_CACHE_SHA1_RE = re.compile(r"^[1-9a-f][0-9a-f]{0,39}$")
DYNAMIC_VERSION_RE = re.compile(
    r"(?:snapshot|latest|release|[\[\]\(\),+]|\.\*)",
    re.IGNORECASE,
)
VERIFICATION_NAMESPACE = "https://schema.gradle.org/dependency-verification"
MAX_FILES = 10_000
MAX_TOTAL_FILE_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_DESCRIPTOR_BYTES = 64 * 1024 * 1024
MAX_VERIFICATION_METADATA_BYTES = 64 * 1024 * 1024
MAX_PATH_BYTES = 1024
MAX_PATH_COMPONENT_BYTES = 255
MAX_PATH_COMPONENTS = 32
MAX_DIRECTORIES = 100_000
MAX_TAR_CONTROL_BYTES_PER_MEMBER = 4096
CANONICAL_GZIP_HEADER = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff"
FORBIDDEN_CACHE_NAMES = {
    ".netrc",
    "gc.properties",
    "gradle.properties",
    "init.gradle",
    "init.gradle.kts",
}
FORBIDDEN_CACHE_SUFFIXES = {".lck", ".lock", ".tmp"}


class SnapshotError(RuntimeError):
    """Safe-to-print snapshot contract failure."""


def _fail(detail: str) -> None:
    raise SnapshotError(detail)


def _sha256_stream(
    stream: BinaryIO,
    *,
    max_bytes: int | None = None,
) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
        size += len(chunk)
        if max_bytes is not None and size > max_bytes:
            _fail(f"file exceeds the {max_bytes}-byte verification limit")
    return digest.hexdigest(), size


def _sha256_sha1_stream(
    stream: BinaryIO,
    *,
    max_bytes: int | None = None,
) -> tuple[str, str, int]:
    sha256_digest = hashlib.sha256()
    sha1_digest = hashlib.sha1(usedforsecurity=False)
    size = 0
    while chunk := stream.read(1024 * 1024):
        sha256_digest.update(chunk)
        sha1_digest.update(chunk)
        size += len(chunk)
        if max_bytes is not None and size > max_bytes:
            _fail(f"file exceeds the {max_bytes}-byte verification limit")
    return sha256_digest.hexdigest(), sha1_digest.hexdigest(), size


def _open_flags(*, directory: bool = False) -> int:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    return flags


@contextlib.contextmanager
def _open_real_directory(path: Path) -> Iterator[tuple[int, Path]]:
    descriptor: int | None = None
    try:
        resolved = path.resolve(strict=True)
        expected = resolved.stat()
        descriptor = os.open("/", _open_flags(directory=True))
        for part in resolved.parts[1:]:
            following = os.open(
                part,
                _open_flags(directory=True),
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = following
        observed = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(observed.st_mode)
            or observed.st_dev != expected.st_dev
            or observed.st_ino != expected.st_ino
        ):
            _fail(f"directory identity changed while opening: {path}")
    except SnapshotError:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        raise
    except OSError as error:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        _fail(f"cannot safely open directory {path}: {error}")
    if descriptor is None:
        _fail(f"cannot safely open directory {path}")
    try:
        yield descriptor, resolved
    finally:
        os.close(descriptor)


def _temporary_name(prefix: str) -> str:
    return f".{prefix}.tmp-{os.getpid()}-{secrets.token_hex(8)}"


@contextlib.contextmanager
def _open_regular_file(
    path: Path,
    *,
    max_bytes: int | None = None,
) -> Iterator[tuple[BinaryIO, os.stat_result]]:
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            _fail(f"expected a real regular file: {path}")
        if max_bytes is not None and before.st_size > max_bytes:
            _fail(f"file exceeds the {max_bytes}-byte verification limit")
        descriptor = os.open(path, _open_flags())
    except OSError as error:
        _fail(f"cannot open regular file {path}: {error}")
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or opened.st_size != before.st_size
        ):
            _fail(f"regular file identity changed before open: {path}")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            yield stream, opened
        after = os.fstat(descriptor)
        if (
            after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
        ):
            _fail(f"regular file changed while reading: {path}")
    finally:
        os.close(descriptor)


def _sha256_file(
    path: Path,
    *,
    max_bytes: int | None = None,
) -> tuple[str, int]:
    try:
        with _open_regular_file(path, max_bytes=max_bytes) as (stream, _):
            return _sha256_stream(stream, max_bytes=max_bytes)
    except SnapshotError:
        raise
    except (OSError, ValueError) as error:
        _fail(f"cannot hash {path}: {error}")


@contextlib.contextmanager
def _open_cache_regular(
    cache_root: Path,
    relative: PurePosixPath,
    *,
    max_bytes: int | None = None,
) -> Iterator[tuple[BinaryIO, os.stat_result]]:
    relative = _safe_relative(relative.as_posix())
    if not _is_under_allowed_root(relative):
        _fail(f"cache file is outside allowed roots: {relative}")
    descriptors: list[int] = []
    try:
        descriptor = os.open(cache_root, _open_flags(directory=True))
        descriptors.append(descriptor)
        for part in relative.parts[:-1]:
            descriptor = os.open(
                part,
                _open_flags(directory=True),
                dir_fd=descriptor,
            )
            descriptors.append(descriptor)
        file_descriptor = os.open(
            relative.parts[-1],
            _open_flags(),
            dir_fd=descriptors[-1],
        )
        descriptors.append(file_descriptor)
        metadata = os.fstat(file_descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            _fail(f"cache member is not a regular file: {relative}")
        if max_bytes is not None and metadata.st_size > max_bytes:
            _fail(f"cache member exceeds the size limit: {relative}")
        with os.fdopen(file_descriptor, "rb", closefd=False) as stream:
            yield stream, metadata
        after = os.fstat(file_descriptor)
        if (
            after.st_size != metadata.st_size
            or after.st_mtime_ns != metadata.st_mtime_ns
        ):
            _fail(f"Gradle cache file changed while reading: {relative}")
    except SnapshotError:
        raise
    except OSError as error:
        _fail(f"cannot safely open Gradle cache file {relative}: {error}")
    finally:
        for descriptor in reversed(descriptors):
            with contextlib.suppress(OSError):
                os.close(descriptor)


def _cache_file_hashes(
    cache_root: Path,
    relative: PurePosixPath,
) -> tuple[str, str, int]:
    with _open_cache_regular(
        cache_root,
        relative,
        max_bytes=MAX_TOTAL_FILE_BYTES,
    ) as (stream, _):
        return _sha256_sha1_stream(
            stream,
            max_bytes=MAX_TOTAL_FILE_BYTES,
        )


def _reject_duplicate_pairs(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: Path) -> Mapping[str, Any]:
    try:
        with _open_regular_file(
            path,
            max_bytes=MAX_DESCRIPTOR_BYTES,
        ) as (stream, _):
            value = json.loads(
                stream.read().decode("utf-8"),
                object_pairs_hook=_reject_duplicate_pairs,
            )
    except (OSError, UnicodeError, ValueError) as error:
        _fail(f"cannot load descriptor {path}: {error}")
    if not isinstance(value, Mapping):
        _fail("descriptor root must be an object")
    return value


def _expect_keys(
    value: Mapping[str, Any],
    expected: set[str],
    location: str,
) -> None:
    actual = set(value)
    if actual != expected:
        _fail(
            f"{location} keys must be {sorted(expected)}, "
            f"found {sorted(actual)}"
        )


def _safe_relative(value: str) -> PurePosixPath:
    if (
        not value
        or len(value.encode("utf-8")) > MAX_PATH_BYTES
        or len(value.split("/")) > MAX_PATH_COMPONENTS
        or any(
            len(part.encode("utf-8")) > MAX_PATH_COMPONENT_BYTES
            for part in value.split("/")
        )
        or "\\" in value
        or unicodedata.normalize("NFC", value) != value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        _fail(f"unsafe snapshot path: {value!r}")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        _fail(f"unsafe snapshot path: {value!r}")
    return path


def _is_under_allowed_root(path: PurePosixPath) -> bool:
    return any(path == root or root in path.parents for root in ALLOWED_ROOTS)


def _require_real_directory_chain(
    cache_root: Path,
    relative_root: PurePosixPath,
) -> Path:
    current = cache_root
    for part in relative_root.parts:
        current /= part
        try:
            metadata = current.lstat()
        except OSError as error:
            _fail(f"cannot inspect required Gradle cache directory: {error}")
        if stat.S_ISLNK(metadata.st_mode):
            _fail(
                "symlink directory is forbidden in Gradle snapshot: "
                f"{current.relative_to(cache_root)}"
            )
        if not stat.S_ISDIR(metadata.st_mode):
            _fail(
                "required Gradle cache path is not a directory: "
                f"{current.relative_to(cache_root)}"
            )
    return current


def _regular_files(cache_root: Path) -> list[Path]:
    if not cache_root.is_dir() or cache_root.is_symlink():
        _fail("Gradle cache root must be a real directory")
    files: list[Path] = []
    total_size = 0
    for relative_root in ALLOWED_ROOTS:
        root = _require_real_directory_chain(cache_root, relative_root)
        for directory, directories, names in os.walk(
            root,
            topdown=True,
            followlinks=False,
        ):
            current = Path(directory)
            directories.sort()
            names.sort()
            for name in directories:
                candidate = current / name
                if candidate.is_symlink():
                    _fail(
                        "symlink directory is forbidden in Gradle snapshot: "
                        f"{candidate.relative_to(cache_root)}"
                    )
            for name in names:
                candidate = current / name
                try:
                    metadata = candidate.lstat()
                except OSError as error:
                    _fail(f"cannot inspect {candidate}: {error}")
                if stat.S_ISLNK(metadata.st_mode):
                    _fail(
                        "symlink file is forbidden in Gradle snapshot: "
                        f"{candidate.relative_to(cache_root)}"
                    )
                if not stat.S_ISREG(metadata.st_mode):
                    _fail(
                        "special file is forbidden in Gradle snapshot: "
                        f"{candidate.relative_to(cache_root)}"
                    )
                if (
                    candidate.name.casefold() in FORBIDDEN_CACHE_NAMES
                    or candidate.suffix.casefold() in FORBIDDEN_CACHE_SUFFIXES
                ):
                    _fail(
                        "transient or credential-bearing Gradle cache file is "
                        f"forbidden: {candidate.relative_to(cache_root)}"
                    )
                files.append(candidate)
                if len(files) > MAX_FILES:
                    _fail(
                        f"Gradle dependency snapshot exceeds {MAX_FILES} files"
                    )
                total_size += metadata.st_size
                if total_size > MAX_TOTAL_FILE_BYTES:
                    _fail(
                        "Gradle dependency snapshot exceeds the uncompressed "
                        "size limit"
                    )
    files.sort(key=lambda path: path.relative_to(cache_root).as_posix())
    if not files:
        _fail("Gradle dependency snapshot cannot be empty")
    return files


def _parse_verification_metadata(
    path: Path,
) -> dict[tuple[str, str, str, str], set[str]]:
    if not path.is_file() or path.is_symlink():
        _fail("Gradle verification metadata must be a regular file")
    try:
        with _open_regular_file(
            path,
            max_bytes=MAX_VERIFICATION_METADATA_BYTES,
        ) as (stream, _):
            root = ET.parse(stream).getroot()
    except (OSError, ET.ParseError) as error:
        _fail(f"cannot parse Gradle verification metadata: {error}")
    namespace = f"{{{VERIFICATION_NAMESPACE}}}"
    if root.tag != f"{namespace}verification-metadata":
        _fail("unexpected Gradle verification metadata namespace or root")
    configurations = root.findall(f"{namespace}configuration")
    component_sets = root.findall(f"{namespace}components")
    if len(configurations) != 1 or len(component_sets) != 1:
        _fail(
            "Gradle verification metadata needs exactly one configuration "
            "and components section"
        )
    if len(list(root)) != 2:
        _fail("unexpected Gradle verification metadata root section")
    configuration = configurations[0]
    components = component_sets[0]
    forbidden = {
        f"{namespace}trusted-artifacts",
        f"{namespace}ignored-keys",
        f"{namespace}trusted-keys",
        f"{namespace}key-servers",
    }
    if any(element.tag in forbidden for element in root.iter()):
        _fail("Gradle verification bypass lists are forbidden")
    configuration_tags = [child.tag for child in configuration]
    if sorted(configuration_tags) != sorted(
        [
            f"{namespace}verify-metadata",
            f"{namespace}verify-signatures",
        ]
    ):
        _fail("Gradle verification configuration is not closed-world")
    verify_metadata = configuration.findtext(f"{namespace}verify-metadata")
    verify_signatures = configuration.findtext(
        f"{namespace}verify-signatures"
    )
    if verify_metadata != "true" or verify_signatures != "false":
        _fail(
            "Gradle verification metadata needs metadata checks and "
            "checksum-only verification"
        )
    records: dict[tuple[str, str, str, str], set[str]] = {}
    for component in components.findall(f"{namespace}component"):
        group = component.get("group", "")
        name = component.get("name", "")
        version = component.get("version", "")
        if not group or not name or not version:
            _fail("Gradle verification component coordinates are incomplete")
        if DYNAMIC_VERSION_RE.search(version):
            _fail(
                "dynamic Gradle dependency version is forbidden: "
                f"{group}:{name}:{version}"
            )
        artifacts = component.findall(f"{namespace}artifact")
        if not artifacts:
            _fail(
                "Gradle verification component has no artifacts: "
                f"{group}:{name}:{version}"
            )
        for artifact in artifacts:
            artifact_name = artifact.get("name", "")
            if not artifact_name or "/" in artifact_name or "\\" in artifact_name:
                _fail("invalid Gradle verification artifact name")
            checksum_nodes = list(artifact)
            if (
                len(checksum_nodes) != 1
                or checksum_nodes[0].tag != f"{namespace}sha256"
                or not SHA256_RE.fullmatch(
                    checksum_nodes[0].get("value", "")
                )
            ):
                _fail(
                    "every Gradle verification artifact needs exactly one "
                    "SHA-256: "
                    f"{group}:{name}:{version}:{artifact_name}"
                )
            checksums = {checksum_nodes[0].get("value", "")}
            key = (group, name, version, artifact_name)
            if key in records:
                _fail(f"duplicate Gradle verification artifact: {key}")
            records[key] = checksums
    if not records:
        _fail("Gradle verification metadata cannot be empty")
    return records


def _canonical_gradle_cache_sha1(sha1_digest: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", sha1_digest):
        _fail("computed Gradle cache SHA-1 is not canonical")
    identity = sha1_digest.lstrip("0")
    if not identity:
        _fail("computed Gradle cache SHA-1 has no nonzero digits")
    return identity


def _module_cache_entry(
    relative: PurePosixPath,
) -> tuple[tuple[str, str, str, str], str] | None:
    files_root = ALLOWED_ROOTS[0]
    if files_root not in relative.parents:
        return None
    tail = relative.relative_to(files_root)
    if len(tail.parts) != 5:
        _fail(f"unexpected Gradle module cache path: {relative}")
    group, module, version, cache_digest, artifact = tail.parts
    if (
        not group
        or not module
        or not version
        or not GRADLE_CACHE_SHA1_RE.fullmatch(cache_digest)
    ):
        _fail(f"invalid Gradle module cache identity: {relative}")
    if DYNAMIC_VERSION_RE.search(version):
        _fail(
            "dynamic Gradle dependency version is forbidden in cache: "
            f"{group}:{module}:{version}"
        )
    return (group, module, version, artifact), cache_digest


def _coordinate_for_file(
    relative: PurePosixPath,
) -> tuple[str, str, str, str] | None:
    entry = _module_cache_entry(relative)
    return None if entry is None else entry[0]


def _file_records(
    cache_root: Path,
    files: Iterable[Path],
    verification: Mapping[tuple[str, str, str, str], set[str]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    records: list[dict[str, Any]] = []
    observed_coordinates: set[tuple[str, str, str, str]] = set()
    observed_path_identities: set[str] = set()
    scanned_file_count = 0
    scanned_total_file_bytes = 0
    excluded_module_file_count = 0
    excluded_module_file_bytes = 0
    for path in files:
        relative = PurePosixPath(path.relative_to(cache_root).as_posix())
        normalized_relative = _safe_relative(relative.as_posix())
        identity = normalized_relative.as_posix().casefold()
        if identity in observed_path_identities:
            _fail(
                "case-insensitive Gradle cache path collision is forbidden: "
                f"{normalized_relative}"
            )
        observed_path_identities.add(identity)
        module_entry = _module_cache_entry(relative)
        digest, sha1_digest, size = _cache_file_hashes(
            cache_root,
            normalized_relative,
        )
        scanned_file_count += 1
        scanned_total_file_bytes += size
        if scanned_total_file_bytes > MAX_TOTAL_FILE_BYTES:
            _fail(
                "Gradle dependency snapshot exceeds the uncompressed size limit"
            )
        coordinate = None if module_entry is None else module_entry[0]
        if (
            module_entry is not None
            and module_entry[1]
            != _canonical_gradle_cache_sha1(sha1_digest)
        ):
            _fail(
                "Gradle cache identity differs from artifact SHA-1: "
                f"{relative}"
            )
        record: dict[str, Any] = {
            "path": relative.as_posix(),
            "size": size,
            "sha256": digest,
            "kind": "gradle-metadata-cache",
        }
        if coordinate is not None:
            allowed = verification.get(coordinate)
            # Gradle's files-2.1 store is a superset of the artifacts used by
            # the resolved graph. Repository probes can leave canonical files
            # that are intentionally absent from verification metadata.
            if allowed is None or digest not in allowed:
                excluded_module_file_count += 1
                excluded_module_file_bytes += size
                continue
            if coordinate in observed_coordinates:
                _fail(
                    "multiple Gradle cache files map to one verified artifact: "
                    f"{coordinate}"
                )
            observed_coordinates.add(coordinate)
            record.update(
                {
                    "kind": "module-artifact",
                    "group": coordinate[0],
                    "module": coordinate[1],
                    "version": coordinate[2],
                    "artifact": coordinate[3],
                }
            )
        records.append(record)
    unbound = sorted(set(verification) - observed_coordinates)
    if unbound:
        _fail(
            "Gradle verification metadata contains unretained artifacts: "
            f"{unbound[:5]}"
        )
    projection = {
        "scanned_file_count": scanned_file_count,
        "scanned_total_file_bytes": scanned_total_file_bytes,
        "retained_file_count": len(records),
        "retained_total_file_bytes": sum(
            int(record["size"]) for record in records
        ),
        "excluded_module_file_count": excluded_module_file_count,
        "excluded_module_file_bytes": excluded_module_file_bytes,
    }
    return records, projection


def _directory_names(records: Iterable[Mapping[str, Any]]) -> list[str]:
    directories: set[PurePosixPath] = set(ALLOWED_ROOTS)
    for record in records:
        path = _safe_relative(str(record["path"]))
        directories.update(path.parents)
        if len(directories) > MAX_DIRECTORIES:
            _fail(
                "Gradle dependency snapshot exceeds the directory count limit"
            )
    directories.discard(PurePosixPath("."))
    return sorted(path.as_posix() for path in directories)


class _BoundedWriter:
    def __init__(self, stream: BinaryIO, limit: int) -> None:
        self._stream = stream
        self._limit = limit
        self._written = 0

    def write(self, value: bytes) -> int:
        if self._written + len(value) > self._limit:
            _fail("Gradle dependency archive exceeds the compressed size limit")
        written = self._stream.write(value)
        self._written += written
        return written

    def flush(self) -> None:
        self._stream.flush()

    def tell(self) -> int:
        return self._stream.tell()


class _DigestingReader:
    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._digest = hashlib.sha256()
        self.size = 0

    def read(self, size: int = -1) -> bytes:
        value = self._stream.read(size)
        self._digest.update(value)
        self.size += len(value)
        return value

    @property
    def sha256(self) -> str:
        return self._digest.hexdigest()


def _write_archive(
    cache_root: Path,
    verification_metadata: Path,
    records: list[Mapping[str, Any]],
    output: Path,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _open_real_directory(output.parent) as (
            parent_descriptor,
            _,
        ):
            try:
                existing = os.stat(
                    output.name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if stat.S_ISLNK(existing.st_mode):
                    _fail(f"archive output cannot be a symlink: {output}")
            except FileNotFoundError:
                pass
            temporary_name = _temporary_name(output.name)
            output_descriptor = os.open(
                temporary_name,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=parent_descriptor,
            )
            try:
                os.fchmod(output_descriptor, 0o644)
                with os.fdopen(
                    output_descriptor,
                    "wb",
                    closefd=False,
                ) as raw:
                    bounded = _BoundedWriter(raw, MAX_ARCHIVE_BYTES)
                    with gzip.GzipFile(
                        filename="",
                        mode="wb",
                        fileobj=bounded,
                        compresslevel=9,
                        mtime=0,
                    ) as compressed:
                        with tarfile.open(
                            fileobj=compressed,
                            mode="w|",
                            format=tarfile.PAX_FORMAT,
                        ) as archive:
                            for name in _directory_names(records):
                                member = tarfile.TarInfo(name)
                                member.type = tarfile.DIRTYPE
                                member.mode = 0o755
                                member.uid = 0
                                member.gid = 0
                                member.uname = ""
                                member.gname = ""
                                member.mtime = 0
                                member.size = 0
                                archive.addfile(member)
                            metadata_sha256, metadata_size = _sha256_file(
                                verification_metadata,
                                max_bytes=MAX_VERIFICATION_METADATA_BYTES,
                            )
                            metadata_member = tarfile.TarInfo(
                                VERIFICATION_METADATA_FILENAME
                            )
                            metadata_member.mode = 0o644
                            metadata_member.uid = 0
                            metadata_member.gid = 0
                            metadata_member.uname = ""
                            metadata_member.gname = ""
                            metadata_member.mtime = 0
                            metadata_member.size = metadata_size
                            with _open_regular_file(
                                verification_metadata,
                                max_bytes=MAX_VERIFICATION_METADATA_BYTES,
                            ) as (stream, metadata):
                                if metadata.st_size != metadata_size:
                                    _fail(
                                        "Gradle verification metadata size "
                                        "changed during packaging"
                                    )
                                observed_metadata = _DigestingReader(stream)
                                archive.addfile(
                                    metadata_member,
                                    observed_metadata,
                                )
                            if (
                                observed_metadata.size != metadata_size
                                or observed_metadata.sha256 != metadata_sha256
                            ):
                                _fail(
                                    "Gradle verification metadata changed "
                                    "during packaging"
                                )
                            for record in records:
                                relative = _safe_relative(str(record["path"]))
                                member = tarfile.TarInfo(
                                    relative.as_posix()
                                )
                                member.mode = 0o644
                                member.uid = 0
                                member.gid = 0
                                member.uname = ""
                                member.gname = ""
                                member.mtime = 0
                                member.size = int(record["size"])
                                with _open_cache_regular(
                                    cache_root,
                                    relative,
                                    max_bytes=MAX_TOTAL_FILE_BYTES,
                                ) as (stream, metadata):
                                    if metadata.st_size != member.size:
                                        _fail(
                                            "Gradle cache file size changed "
                                            f"during packaging: {relative}"
                                        )
                                    observed = _DigestingReader(stream)
                                    archive.addfile(member, observed)
                                if (
                                    observed.size != member.size
                                    or observed.sha256 != record["sha256"]
                                ):
                                    _fail(
                                        "Gradle cache file changed during "
                                        f"packaging: {relative}"
                                    )
                    raw.flush()
                    os.fsync(raw.fileno())
                os.replace(
                    temporary_name,
                    output.name,
                    src_dir_fd=parent_descriptor,
                    dst_dir_fd=parent_descriptor,
                )
                temporary_name = ""
            finally:
                os.close(output_descriptor)
                if temporary_name:
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(
                            temporary_name,
                            dir_fd=parent_descriptor,
                        )
    except SnapshotError:
        raise
    except (OSError, tarfile.TarError) as error:
        _fail(f"cannot create deterministic Gradle archive: {error}")


def create_snapshot(
    cache_root: Path,
    verification_metadata: Path,
    archive: Path,
    descriptor: Path,
) -> Mapping[str, Any]:
    if cache_root.is_symlink():
        _fail("Gradle cache root cannot be a symlink")
    if verification_metadata.is_symlink():
        _fail("Gradle verification metadata cannot be a symlink")
    cache_root = cache_root.resolve(strict=True)
    verification_metadata = verification_metadata.resolve(strict=True)
    verification = _parse_verification_metadata(verification_metadata)
    records, projection = _file_records(
        cache_root,
        _regular_files(cache_root),
        verification,
    )
    _write_archive(cache_root, verification_metadata, records, archive)
    archive_sha256, archive_size = _sha256_file(
        archive,
        max_bytes=MAX_ARCHIVE_BYTES,
    )
    metadata_sha256, metadata_size = _sha256_file(
        verification_metadata,
        max_bytes=MAX_VERIFICATION_METADATA_BYTES,
    )
    directories = _directory_names(records)
    value: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "component": COMPONENT,
        "polaris_version": POLARIS_VERSION,
        "gradle_version": GRADLE_VERSION,
        "platform": PLATFORM,
        "source_archive_sha512": SOURCE_ARCHIVE_SHA512,
        "cache_roots": [path.as_posix() for path in ALLOWED_ROOTS],
        "file_count": len(records),
        "directory_count": len(directories),
        "total_file_bytes": sum(int(record["size"]) for record in records),
        "projection": projection,
        "archive": {
            "filename": ARCHIVE_FILENAME,
            "media_type": ARCHIVE_MEDIA_TYPE,
            "sha256": archive_sha256,
            "size": archive_size,
        },
        "verification_metadata": {
            "filename": VERIFICATION_METADATA_FILENAME,
            "media_type": VERIFICATION_METADATA_MEDIA_TYPE,
            "sha256": metadata_sha256,
            "size": metadata_size,
            "mode": "strict",
        },
        "files": records,
    }
    descriptor.parent.mkdir(parents=True, exist_ok=True)
    encoded_descriptor = (
        json.dumps(value, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if len(encoded_descriptor) > MAX_DESCRIPTOR_BYTES:
        _fail("Gradle dependency descriptor exceeds the size limit")
    try:
        with _open_real_directory(descriptor.parent) as (
            parent_descriptor,
            _,
        ):
            try:
                existing = os.stat(
                    descriptor.name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if stat.S_ISLNK(existing.st_mode):
                    _fail(
                        f"descriptor output cannot be a symlink: {descriptor}"
                    )
            except FileNotFoundError:
                pass
            temporary_name = _temporary_name(descriptor.name)
            descriptor_fd = os.open(
                temporary_name,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=parent_descriptor,
            )
            try:
                os.fchmod(descriptor_fd, 0o644)
                with os.fdopen(
                    descriptor_fd,
                    "wb",
                    closefd=False,
                ) as stream:
                    stream.write(encoded_descriptor)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(
                    temporary_name,
                    descriptor.name,
                    src_dir_fd=parent_descriptor,
                    dst_dir_fd=parent_descriptor,
                )
                temporary_name = ""
            finally:
                os.close(descriptor_fd)
                if temporary_name:
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(
                            temporary_name,
                            dir_fd=parent_descriptor,
                        )
    except OSError as error:
        _fail(f"cannot write dependency descriptor atomically: {error}")
    return value


def _validate_descriptor(
    descriptor: Mapping[str, Any],
    verification_metadata: Path,
) -> list[Mapping[str, Any]]:
    _expect_keys(
        descriptor,
        {
            "schema_version",
            "component",
            "polaris_version",
            "gradle_version",
            "platform",
            "source_archive_sha512",
            "cache_roots",
            "file_count",
            "directory_count",
            "total_file_bytes",
            "projection",
            "archive",
            "verification_metadata",
            "files",
        },
        "descriptor",
    )
    expected_scalars = {
        "schema_version": SCHEMA_VERSION,
        "component": COMPONENT,
        "polaris_version": POLARIS_VERSION,
        "gradle_version": GRADLE_VERSION,
        "platform": PLATFORM,
        "source_archive_sha512": SOURCE_ARCHIVE_SHA512,
        "cache_roots": [path.as_posix() for path in ALLOWED_ROOTS],
    }
    for field, expected in expected_scalars.items():
        if descriptor.get(field) != expected:
            _fail(f"descriptor {field} differs from the closed contract")
    archive = descriptor.get("archive")
    metadata = descriptor.get("verification_metadata")
    projection = descriptor.get("projection")
    if (
        not isinstance(archive, Mapping)
        or not isinstance(metadata, Mapping)
        or not isinstance(projection, Mapping)
    ):
        _fail(
            "descriptor archive, verification_metadata, and projection "
            "must be objects"
        )
    _expect_keys(
        archive,
        {"filename", "media_type", "sha256", "size"},
        "descriptor.archive",
    )
    _expect_keys(
        metadata,
        {"filename", "media_type", "sha256", "size", "mode"},
        "descriptor.verification_metadata",
    )
    _expect_keys(
        projection,
        {
            "scanned_file_count",
            "scanned_total_file_bytes",
            "retained_file_count",
            "retained_total_file_bytes",
            "excluded_module_file_count",
            "excluded_module_file_bytes",
        },
        "descriptor.projection",
    )
    if any(
        not isinstance(projection.get(field), int)
        or int(projection[field]) < 0
        for field in projection
    ):
        _fail("descriptor projection counters must be nonnegative integers")
    if (
        archive.get("filename") != ARCHIVE_FILENAME
        or archive.get("media_type") != ARCHIVE_MEDIA_TYPE
        or not isinstance(archive.get("size"), int)
        or int(archive["size"]) <= 0
        or int(archive["size"]) > MAX_ARCHIVE_BYTES
        or not isinstance(archive.get("sha256"), str)
        or not SHA256_RE.fullmatch(str(archive["sha256"]))
    ):
        _fail("descriptor archive contract is invalid")
    if (
        metadata.get("filename") != VERIFICATION_METADATA_FILENAME
        or metadata.get("media_type") != VERIFICATION_METADATA_MEDIA_TYPE
        or metadata.get("mode") != "strict"
        or not isinstance(metadata.get("size"), int)
        or int(metadata["size"]) <= 0
        or int(metadata["size"]) > MAX_VERIFICATION_METADATA_BYTES
        or not isinstance(metadata.get("sha256"), str)
        or not SHA256_RE.fullmatch(str(metadata["sha256"]))
    ):
        _fail("descriptor verification metadata contract is invalid")
    actual_metadata_sha256, actual_metadata_size = _sha256_file(
        verification_metadata,
        max_bytes=MAX_VERIFICATION_METADATA_BYTES,
    )
    if (
        metadata["sha256"] != actual_metadata_sha256
        or metadata["size"] != actual_metadata_size
    ):
        _fail("Gradle verification metadata differs from descriptor")

    files = descriptor.get("files")
    if not isinstance(files, list) or not files:
        _fail("descriptor files must be a non-empty array")
    if (
        not isinstance(descriptor.get("file_count"), int)
        or not isinstance(descriptor.get("directory_count"), int)
        or not isinstance(descriptor.get("total_file_bytes"), int)
    ):
        _fail("descriptor count and size summaries must be integers")
    expected_record_keys = {
        "path",
        "size",
        "sha256",
        "kind",
    }
    module_record_keys = expected_record_keys | {
        "group",
        "module",
        "version",
        "artifact",
    }
    paths: list[str] = []
    path_identities: set[str] = set()
    total_size = 0
    for index, record in enumerate(files):
        if not isinstance(record, Mapping):
            _fail(f"descriptor file {index} must be an object")
        kind = record.get("kind")
        _expect_keys(
            record,
            module_record_keys
            if kind == "module-artifact"
            else expected_record_keys,
            f"descriptor.files[{index}]",
        )
        relative = _safe_relative(str(record.get("path", "")))
        identity = relative.as_posix().casefold()
        if identity in path_identities:
            _fail(f"case-insensitive descriptor path collision: {relative}")
        path_identities.add(identity)
        if not _is_under_allowed_root(relative):
            _fail(f"descriptor path is outside allowed cache roots: {relative}")
        if (
            not isinstance(record.get("size"), int)
            or int(record["size"]) < 0
            or not isinstance(record.get("sha256"), str)
            or not SHA256_RE.fullmatch(str(record["sha256"]))
            or kind not in {"module-artifact", "gradle-metadata-cache"}
        ):
            _fail(f"invalid descriptor record for {relative}")
        total_size += int(record["size"])
        coordinate = _coordinate_for_file(relative)
        if coordinate is None and kind != "gradle-metadata-cache":
            _fail(f"metadata cache file has module identity: {relative}")
        if coordinate is not None:
            if kind != "module-artifact":
                _fail(f"module artifact lacks coordinate identity: {relative}")
            actual_coordinate = (
                record.get("group"),
                record.get("module"),
                record.get("version"),
                record.get("artifact"),
            )
            if actual_coordinate != coordinate:
                _fail(f"module coordinates differ from cache path: {relative}")
        paths.append(relative.as_posix())
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        _fail("descriptor file paths must be sorted and unique")
    if (
        len(files) > MAX_FILES
        or total_size > MAX_TOTAL_FILE_BYTES
        or descriptor.get("file_count") != len(files)
        or descriptor.get("directory_count") != len(_directory_names(files))
        or descriptor.get("total_file_bytes") != total_size
        or projection.get("retained_file_count") != len(files)
        or projection.get("retained_total_file_bytes") != total_size
        or projection.get("scanned_file_count")
        != len(files) + projection.get("excluded_module_file_count")
        or projection.get("scanned_total_file_bytes")
        != total_size + projection.get("excluded_module_file_bytes")
        or int(projection["scanned_file_count"]) > MAX_FILES
        or int(projection["scanned_total_file_bytes"])
        > MAX_TOTAL_FILE_BYTES
    ):
        _fail("descriptor count or size summary differs from file records")

    verification = _parse_verification_metadata(verification_metadata)
    observed: set[tuple[str, str, str, str]] = set()
    for record in files:
        if record["kind"] != "module-artifact":
            continue
        coordinate = (
            str(record["group"]),
            str(record["module"]),
            str(record["version"]),
            str(record["artifact"]),
        )
        if str(record["sha256"]) not in verification.get(coordinate, set()):
            _fail(
                "descriptor artifact is not authenticated by Gradle "
                f"verification metadata: {coordinate}"
            )
        if coordinate in observed:
            _fail(
                "multiple descriptor records map to one verified artifact: "
                f"{coordinate}"
            )
        observed.add(coordinate)
    if observed != set(verification):
        _fail("descriptor and Gradle verification metadata differ")
    return files


def _expected_archive_members(
    records: Iterable[Mapping[str, Any]],
) -> list[str]:
    return (
        _directory_names(records)
        + [VERIFICATION_METADATA_FILENAME]
        + [str(record["path"]) for record in records]
    )


def _verify_canonical_gzip_header(archive_path: Path) -> None:
    with _open_regular_file(
        archive_path,
        max_bytes=MAX_ARCHIVE_BYTES,
    ) as (stream, _):
        header = stream.read(len(CANONICAL_GZIP_HEADER))
    if header != CANONICAL_GZIP_HEADER:
        _fail("Gradle dependency archive has a noncanonical gzip envelope")


def _verify_single_bounded_gzip_member(
    archive_path: Path,
    *,
    maximum_uncompressed_bytes: int,
) -> int:
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    total = 0
    reached_eof = False
    try:
        with _open_regular_file(
            archive_path,
            max_bytes=MAX_ARCHIVE_BYTES,
        ) as (stream, _):
            while chunk := stream.read(1024 * 1024):
                remaining = maximum_uncompressed_bytes - total
                output = decompressor.decompress(
                    chunk,
                    min(1024 * 1024, remaining + 1),
                )
                total += len(output)
                if total > maximum_uncompressed_bytes:
                    _fail(
                        "Gradle dependency archive exceeds the bounded "
                        "uncompressed size"
                    )
                while decompressor.unconsumed_tail:
                    remaining = maximum_uncompressed_bytes - total
                    output = decompressor.decompress(
                        decompressor.unconsumed_tail,
                        min(1024 * 1024, remaining + 1),
                    )
                    total += len(output)
                    if total > maximum_uncompressed_bytes:
                        _fail(
                            "Gradle dependency archive exceeds the bounded "
                            "uncompressed size"
                        )
                if decompressor.eof:
                    reached_eof = True
                    if decompressor.unused_data or stream.read(1):
                        _fail(
                            "Gradle dependency archive contains trailing "
                            "or concatenated payload"
                        )
                    break
    except zlib.error as error:
        _fail(f"invalid Gradle dependency gzip stream: {error}")
    if not reached_eof:
        _fail("Gradle dependency gzip stream is truncated")
    if total == 0 or total % tarfile.RECORDSIZE != 0:
        _fail("Gradle dependency tar stream has noncanonical record padding")
    return total


def _verify_bounded_tar_control_records(archive_path: Path) -> None:
    zero_block = b"\0" * tarfile.BLOCKSIZE
    zero_blocks = 0
    header_count = 0

    def read_exact(stream: BinaryIO, size: int) -> bytes:
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            chunk = stream.read(min(remaining, 1024 * 1024))
            if not chunk:
                _fail("Gradle dependency tar stream is truncated")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def discard_exact(stream: BinaryIO, size: int) -> None:
        remaining = size
        while remaining:
            chunk = stream.read(min(remaining, 1024 * 1024))
            if not chunk:
                _fail("Gradle dependency tar stream is truncated")
            remaining -= len(chunk)

    try:
        with _open_regular_file(
            archive_path,
            max_bytes=MAX_ARCHIVE_BYTES,
        ) as (stream, _):
            with gzip.GzipFile(fileobj=stream, mode="rb") as uncompressed:
                while True:
                    header = read_exact(uncompressed, tarfile.BLOCKSIZE)
                    if header == zero_block:
                        zero_blocks += 1
                        if zero_blocks == 2:
                            while trailing := uncompressed.read(1024 * 1024):
                                if any(trailing):
                                    _fail(
                                        "Gradle dependency tar stream has "
                                        "nonzero trailing records"
                                    )
                            break
                        continue
                    if zero_blocks:
                        _fail(
                            "Gradle dependency tar stream resumes after EOF"
                        )
                    header_count += 1
                    if header_count > 2 * (
                        MAX_FILES + MAX_DIRECTORIES + 1
                    ):
                        _fail(
                            "Gradle dependency tar stream has too many "
                            "control records"
                        )
                    try:
                        member = tarfile.TarInfo.frombuf(
                            header,
                            encoding="utf-8",
                            errors="surrogateescape",
                        )
                    except tarfile.HeaderError as error:
                        _fail(f"invalid Gradle dependency tar header: {error}")
                    if member.type == tarfile.XHDTYPE:
                        if member.size > MAX_TAR_CONTROL_BYTES_PER_MEMBER:
                            _fail(
                                "Gradle dependency PAX control record exceeds "
                                "the size limit"
                            )
                    elif member.type in {
                        tarfile.REGTYPE,
                        tarfile.AREGTYPE,
                    }:
                        if member.size > MAX_TOTAL_FILE_BYTES:
                            _fail(
                                "Gradle dependency tar member exceeds the "
                                "uncompressed size limit"
                            )
                    elif member.type == tarfile.DIRTYPE:
                        if member.size != 0:
                            _fail(
                                "Gradle dependency tar directory has payload"
                            )
                    else:
                        _fail(
                            "Gradle dependency tar control type is forbidden"
                        )
                    padded_size = (
                        (member.size + tarfile.BLOCKSIZE - 1)
                        // tarfile.BLOCKSIZE
                        * tarfile.BLOCKSIZE
                    )
                    if padded_size:
                        discard_exact(uncompressed, padded_size)
    except SnapshotError:
        raise
    except (OSError, EOFError, zlib.error) as error:
        _fail(f"cannot preflight Gradle dependency tar stream: {error}")


def _verify_archive_inventory(
    archive_path: Path,
    records: list[Mapping[str, Any]],
    metadata_record: Mapping[str, Any],
) -> None:
    expected_records = {str(record["path"]): record for record in records}
    expected_records[VERIFICATION_METADATA_FILENAME] = {
        "path": VERIFICATION_METADATA_FILENAME,
        "size": metadata_record["size"],
        "sha256": metadata_record["sha256"],
    }
    expected_members = _expected_archive_members(records)
    expected_directories = set(_directory_names(records))
    observed_members: list[str] = []
    try:
        with _open_regular_file(
            archive_path,
            max_bytes=MAX_ARCHIVE_BYTES,
        ) as (stream, _):
            with tarfile.open(fileobj=stream, mode="r:gz") as bundle:
                for member in bundle:
                    name = _safe_relative(member.name).as_posix()
                    observed_members.append(name)
                    if len(observed_members) > len(expected_members):
                        _fail("archive contains more members than the contract")
                    if member.uid != 0 or member.gid != 0 or member.mtime != 0:
                        _fail(
                            f"noncanonical archive ownership or mtime: {name}"
                        )
                    if member.uname or member.gname:
                        _fail(f"noncanonical archive owner name: {name}")
                    expected_pax_path = (
                        f"{name}/" if member.isdir() else name
                    )
                    if member.pax_headers and (
                        set(member.pax_headers) != {"path"}
                        or member.pax_headers["path"] != expected_pax_path
                    ):
                        _fail(f"noncanonical PAX metadata: {name}")
                    if member.isdir():
                        if (
                            name not in expected_directories
                            or member.mode != 0o755
                            or member.size != 0
                        ):
                            _fail(
                                f"noncanonical or unexpected archive "
                                f"directory: {name}"
                            )
                        continue
                    if not member.isfile() or member.mode != 0o644:
                        _fail(
                            "links and special archive members are forbidden: "
                            f"{name}"
                        )
                    record = expected_records.get(name)
                    if record is None or member.size != record["size"]:
                        _fail(f"unexpected archive file or size: {name}")
                    file_stream = bundle.extractfile(member)
                    if file_stream is None:
                        _fail(f"cannot read archive member: {name}")
                    with file_stream:
                        digest, sha1_digest, size = _sha256_sha1_stream(
                            file_stream,
                            max_bytes=int(record["size"]),
                        )
                    if (
                        size != record["size"]
                        or digest != record["sha256"]
                    ):
                        _fail(
                            "archive member hash differs from descriptor: "
                            f"{name}"
                        )
                    module_entry = _module_cache_entry(
                        PurePosixPath(name)
                    )
                    if (
                        module_entry is not None
                        and module_entry[1]
                        != _canonical_gradle_cache_sha1(sha1_digest)
                    ):
                        _fail(
                            "archive Gradle cache identity differs from "
                            f"artifact SHA-1: {name}"
                        )
    except SnapshotError:
        raise
    except (OSError, EOFError, tarfile.TarError) as error:
        _fail(f"cannot verify Gradle dependency archive: {error}")
    if observed_members != expected_members:
        _fail("archive member inventory or order differs from descriptor")


def _validate_extraction_target_at(
    parent_descriptor: int,
    name: str,
) -> None:
    try:
        metadata = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        _fail("snapshot extraction root must be a real directory")
    descriptor = os.open(
        name,
        _open_flags(directory=True),
        dir_fd=parent_descriptor,
    )
    try:
        with os.scandir(descriptor) as entries:
            if next(entries, None) is not None:
                _fail("snapshot extraction root must be empty")
    finally:
        os.close(descriptor)


def _validate_extraction_target(extract_root: Path) -> None:
    try:
        with _open_real_directory(extract_root.parent) as (
            parent_descriptor,
            _,
        ):
            _validate_extraction_target_at(
                parent_descriptor,
                extract_root.name,
            )
    except OSError as error:
        _fail(f"cannot inspect snapshot extraction target: {error}")


def _clear_directory_fd(descriptor: int) -> None:
    for entry in list(os.scandir(descriptor)):
        if entry.is_dir(follow_symlinks=False):
            child = os.open(
                entry.name,
                _open_flags(directory=True),
                dir_fd=descriptor,
            )
            try:
                _clear_directory_fd(child)
            finally:
                os.close(child)
            os.rmdir(entry.name, dir_fd=descriptor)
        else:
            os.unlink(entry.name, dir_fd=descriptor)


@contextlib.contextmanager
def _open_or_create_staging_directory(
    root_descriptor: int,
    parts: tuple[str, ...],
) -> Iterator[int]:
    current = os.dup(root_descriptor)
    try:
        for part in parts:
            try:
                os.mkdir(part, mode=0o755, dir_fd=current)
            except FileExistsError:
                pass
            following = os.open(
                part,
                _open_flags(directory=True),
                dir_fd=current,
            )
            os.close(current)
            current = following
        yield current
    except OSError as error:
        _fail(f"cannot create safe extraction directory: {error}")
    finally:
        with contextlib.suppress(OSError):
            os.close(current)


def _extract_validated_archive(
    archive_path: Path,
    extract_root: Path,
    records: list[Mapping[str, Any]],
    metadata_record: Mapping[str, Any],
    archive_sha256: str,
) -> None:
    expected_records = {str(record["path"]): record for record in records}
    expected_records[VERIFICATION_METADATA_FILENAME] = {
        "path": VERIFICATION_METADATA_FILENAME,
        "size": metadata_record["size"],
        "sha256": metadata_record["sha256"],
    }
    expected_members = _expected_archive_members(records)
    try:
        with _open_real_directory(extract_root.parent) as (
            target_parent_descriptor,
            _,
        ):
            _validate_extraction_target_at(
                target_parent_descriptor,
                extract_root.name,
            )
            staging_name = (
                f".{extract_root.name}.snapshot-"
                f"{os.getpid()}-{secrets.token_hex(8)}"
            )
            os.mkdir(
                staging_name,
                mode=0o700,
                dir_fd=target_parent_descriptor,
            )
            staging_descriptor = os.open(
                staging_name,
                _open_flags(directory=True),
                dir_fd=target_parent_descriptor,
            )
            staging_identity = os.fstat(staging_descriptor)
            try:
                observed_members: list[str] = []
                with _open_regular_file(
                    archive_path,
                    max_bytes=MAX_ARCHIVE_BYTES,
                ) as (stream, _):
                    with tarfile.open(fileobj=stream, mode="r:gz") as bundle:
                        for member in bundle:
                            relative = _safe_relative(member.name)
                            name = relative.as_posix()
                            observed_members.append(name)
                            if member.isdir():
                                with _open_or_create_staging_directory(
                                    staging_descriptor,
                                    relative.parts,
                                ):
                                    pass
                                continue
                            record = expected_records.get(name)
                            if (
                                record is None
                                or not member.isfile()
                                or member.size != record["size"]
                            ):
                                _fail(
                                    "validated archive changed before "
                                    f"extraction: {name}"
                                )
                            file_stream = bundle.extractfile(member)
                            if file_stream is None:
                                _fail(f"cannot read archive member: {name}")
                            digest = hashlib.sha256()
                            size = 0
                            with _open_or_create_staging_directory(
                                staging_descriptor,
                                relative.parts[:-1],
                            ) as parent_descriptor:
                                file_descriptor = os.open(
                                    relative.parts[-1],
                                    os.O_WRONLY
                                    | os.O_CREAT
                                    | os.O_EXCL
                                    | getattr(os, "O_CLOEXEC", 0)
                                    | getattr(os, "O_NOFOLLOW", 0),
                                    0o600,
                                    dir_fd=parent_descriptor,
                                )
                                try:
                                    with (
                                        file_stream,
                                        os.fdopen(
                                            file_descriptor,
                                            "wb",
                                            closefd=False,
                                        ) as output,
                                    ):
                                        while chunk := file_stream.read(
                                            1024 * 1024
                                        ):
                                            digest.update(chunk)
                                            size += len(chunk)
                                            if size > int(record["size"]):
                                                _fail(
                                                    "archive member grew "
                                                    "before extraction: "
                                                    f"{name}"
                                                )
                                            output.write(chunk)
                                    os.fchmod(file_descriptor, 0o644)
                                finally:
                                    os.close(file_descriptor)
                            if (
                                size != record["size"]
                                or digest.hexdigest() != record["sha256"]
                            ):
                                _fail(
                                    "archive member changed before extraction: "
                                    f"{name}"
                                )
                if observed_members != expected_members:
                    _fail(
                        "validated archive inventory changed before extraction"
                    )
                post_sha256, _ = _sha256_file(
                    archive_path,
                    max_bytes=MAX_ARCHIVE_BYTES,
                )
                if post_sha256 != archive_sha256:
                    _fail(
                        "validated archive changed before extraction completed"
                    )
                named_identity = os.stat(
                    staging_name,
                    dir_fd=target_parent_descriptor,
                    follow_symlinks=False,
                )
                if (
                    named_identity.st_dev != staging_identity.st_dev
                    or named_identity.st_ino != staging_identity.st_ino
                ):
                    _fail("snapshot staging directory identity changed")
                _validate_extraction_target_at(
                    target_parent_descriptor,
                    extract_root.name,
                )
                os.replace(
                    staging_name,
                    extract_root.name,
                    src_dir_fd=target_parent_descriptor,
                    dst_dir_fd=target_parent_descriptor,
                )
                staging_name = ""
            finally:
                if staging_name:
                    with contextlib.suppress(OSError):
                        named_identity = os.stat(
                            staging_name,
                            dir_fd=target_parent_descriptor,
                            follow_symlinks=False,
                        )
                        if (
                            named_identity.st_dev
                            == staging_identity.st_dev
                            and named_identity.st_ino
                            == staging_identity.st_ino
                        ):
                            _clear_directory_fd(staging_descriptor)
                            os.rmdir(
                                staging_name,
                                dir_fd=target_parent_descriptor,
                            )
                os.close(staging_descriptor)
    except SnapshotError:
        raise
    except (OSError, EOFError, tarfile.TarError) as error:
        _fail(f"cannot extract verified Gradle dependency archive: {error}")


def verify_snapshot(
    descriptor_path: Path,
    verification_metadata: Path,
    archive_path: Path,
    extract_root: Path | None = None,
) -> Mapping[str, Any]:
    descriptor = _load_json(descriptor_path)
    records = _validate_descriptor(descriptor, verification_metadata)
    archive = descriptor["archive"]
    actual_sha256, actual_size = _sha256_file(
        archive_path,
        max_bytes=MAX_ARCHIVE_BYTES,
    )
    if archive["sha256"] != actual_sha256 or archive["size"] != actual_size:
        _fail("Gradle dependency archive differs from descriptor")
    metadata_record = descriptor["verification_metadata"]
    _verify_canonical_gzip_header(archive_path)
    member_count = (
        int(descriptor["file_count"])
        + int(descriptor["directory_count"])
        + 1
    )
    maximum_tar_bytes = (
        int(descriptor["total_file_bytes"])
        + int(metadata_record["size"])
        + member_count * MAX_TAR_CONTROL_BYTES_PER_MEMBER
        + tarfile.RECORDSIZE
    )
    _verify_single_bounded_gzip_member(
        archive_path,
        maximum_uncompressed_bytes=maximum_tar_bytes,
    )
    _verify_bounded_tar_control_records(archive_path)
    if extract_root is not None:
        _validate_extraction_target(extract_root)
    _verify_archive_inventory(archive_path, records, metadata_record)
    if extract_root is not None:
        _extract_validated_archive(
            archive_path,
            extract_root,
            records,
            metadata_record,
            actual_sha256,
        )
    return descriptor


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--cache-root", type=Path, required=True)
    create.add_argument("--verification-metadata", type=Path, required=True)
    create.add_argument("--archive", type=Path, required=True)
    create.add_argument("--descriptor", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--descriptor", type=Path, required=True)
    verify.add_argument("--verification-metadata", type=Path, required=True)
    verify.add_argument("--archive", type=Path, required=True)
    verify.add_argument("--extract-root", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "create":
            value = create_snapshot(
                args.cache_root,
                args.verification_metadata,
                args.archive,
                args.descriptor,
            )
            print(
                "polaris-gradle-snapshot: created "
                f"scanned={value['projection']['scanned_file_count']} "
                f"retained={value['projection']['retained_file_count']} "
                f"excluded={value['projection']['excluded_module_file_count']} "
                f"archive_sha256={value['archive']['sha256']}"
            )
        else:
            value = verify_snapshot(
                args.descriptor,
                args.verification_metadata,
                args.archive,
                args.extract_root,
            )
            print(
                "polaris-gradle-snapshot: verified "
                f"files={len(value['files'])} "
                f"archive_sha256={value['archive']['sha256']}"
            )
    except SnapshotError as error:
        print(f"polaris-gradle-snapshot: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
