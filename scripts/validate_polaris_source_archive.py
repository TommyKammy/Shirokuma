#!/usr/bin/env python3
"""Validate the authenticated Polaris source archive before extraction."""

from __future__ import annotations

import argparse
import gzip
import posixpath
import sys
import tarfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO


POLARIS_VERSION = "1.6.0"
POLARIS_COMMIT = "dd306009d81a0e15adafe9dcd7d1c6d04d326f34"
POLARIS_SOURCE_ARCHIVE_ROOT = f"apache-polaris-{POLARIS_VERSION}"
POLARIS_SOURCE_ARCHIVE_MAXIMUM_BYTES = 67_108_864
POLARIS_SOURCE_ARCHIVE_MAXIMUM_DECOMPRESSED_BYTES = 536_870_912
POLARIS_SOURCE_ARCHIVE_MAXIMUM_RAW_HEADERS = 20_000
POLARIS_SOURCE_ARCHIVE_MAXIMUM_TAR_CONTROL_BYTES = 4_096
POLARIS_SOURCE_ARCHIVE_MAXIMUM_MEMBERS = 10_000
POLARIS_SOURCE_ARCHIVE_MAXIMUM_MEMBER_BYTES = 67_108_864
POLARIS_SOURCE_ARCHIVE_MAXIMUM_TOTAL_FILE_BYTES = 268_435_456
POLARIS_SOURCE_ARCHIVE_MAXIMUM_PATH_BYTES = 1_024
POLARIS_SOURCE_ARCHIVE_MAXIMUM_PATH_COMPONENT_BYTES = 255
POLARIS_SOURCE_ARCHIVE_MAXIMUM_PATH_COMPONENTS = 64
POLARIS_SOURCE_ARCHIVE_MAXIMUM_LINK_BYTES = 1_024
POLARIS_SOURCE_ARCHIVE_MAXIMUM_PAX_BYTES = 4_096
POLARIS_SOURCE_ARCHIVE_ALLOWED_PAX_HEADERS = {"comment", "linkpath"}

_TAR_BLOCK_BYTES = 512
_TAR_ZERO_BLOCK = b"\0" * _TAR_BLOCK_BYTES
_TAR_PAX_CONTROL_TYPES = {b"g", b"x"}
_TAR_FORBIDDEN_HIDDEN_NAME_TYPES = {b"K", b"L"}
_TAR_FORBIDDEN_SOLARIS_PAX_TYPES = {b"X"}


class ContractError(RuntimeError):
    """Stable source-archive error surfaced to tests and CI."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _fail(code: str, detail: str) -> None:
    raise ContractError(code, detail)


def _expect(condition: bool, code: str, detail: str) -> None:
    if not condition:
        _fail(code, detail)


def _source_archive_text_bytes(
    value: str,
    *,
    label: str,
    maximum_bytes: int,
) -> bytes:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError:
        _fail(
            "SOURCE_ARCHIVE",
            f"non-portable Polaris source {label}",
        )
    _expect(
        len(encoded) <= maximum_bytes
        and all(0x20 <= byte <= 0x7E for byte in encoded),
        "SOURCE_ARCHIVE",
        f"non-portable Polaris source {label}",
    )
    return encoded


def _source_archive_member_path(name: str) -> PurePosixPath:
    raw_parts = name.split("/")
    _source_archive_text_bytes(
        name,
        label=f"archive member: {name}",
        maximum_bytes=POLARIS_SOURCE_ARCHIVE_MAXIMUM_PATH_BYTES,
    )
    _expect(
        bool(name)
        and not name.startswith("/")
        and "\\" not in name
        and len(raw_parts) <= POLARIS_SOURCE_ARCHIVE_MAXIMUM_PATH_COMPONENTS
        and all(part not in {"", ".", ".."} for part in raw_parts)
        and all(
            len(part.encode("ascii"))
            <= POLARIS_SOURCE_ARCHIVE_MAXIMUM_PATH_COMPONENT_BYTES
            for part in raw_parts
        ),
        "SOURCE_ARCHIVE",
        f"non-canonical Polaris source archive member: {name}",
    )
    path = PurePosixPath(name)
    _expect(
        bool(path.parts) and path.parts[0] == POLARIS_SOURCE_ARCHIVE_ROOT,
        "SOURCE_ARCHIVE",
        f"Polaris source archive member escaped the release root: {name}",
    )
    return path


def _source_archive_symlink_target(
    member: tarfile.TarInfo,
) -> str:
    linkname = member.linkname
    target_text = linkname[:-1] if linkname.endswith("/") else linkname
    target_parts = target_text.split("/")
    _source_archive_text_bytes(
        linkname,
        label=f"symlink target: {member.name} -> {linkname}",
        maximum_bytes=POLARIS_SOURCE_ARCHIVE_MAXIMUM_LINK_BYTES,
    )
    _expect(
        bool(target_text)
        and not target_text.startswith("/")
        and "\\" not in target_text
        and not linkname.endswith("//")
        and len(target_parts)
        <= POLARIS_SOURCE_ARCHIVE_MAXIMUM_PATH_COMPONENTS
        and all(part not in {"", "."} for part in target_parts)
        and all(
            len(part.encode("ascii"))
            <= POLARIS_SOURCE_ARCHIVE_MAXIMUM_PATH_COMPONENT_BYTES
            for part in target_parts
        )
        and posixpath.normpath(target_text) == target_text,
        "SOURCE_ARCHIVE",
        f"non-canonical Polaris source symlink target: "
        f"{member.name} -> {linkname}",
    )
    target = PurePosixPath(
        posixpath.normpath(
            posixpath.join(
                PurePosixPath(member.name).parent.as_posix(),
                target_text,
            )
        )
    )
    _expect(
        bool(target.parts)
        and target.parts[0] == POLARIS_SOURCE_ARCHIVE_ROOT
        and ".." not in target.parts,
        "SOURCE_ARCHIVE",
        f"Polaris source symlink escaped the release root: "
        f"{member.name} -> {linkname}",
    )
    member_path = PurePosixPath(member.name)
    extracted_path = PurePosixPath(*member_path.parts[1:])
    extracted_target_text = posixpath.normpath(
        posixpath.join(extracted_path.parent.as_posix(), target_text)
    )
    extracted_target = PurePosixPath(extracted_target_text)
    _expect(
        not extracted_target.is_absolute()
        and ".." not in extracted_target.parts,
        "SOURCE_ARCHIVE",
        f"Polaris source symlink escapes after root stripping: "
        f"{member.name} -> {linkname}",
    )
    target_after_stripping = (
        POLARIS_SOURCE_ARCHIVE_ROOT
        if extracted_target_text == "."
        else f"{POLARIS_SOURCE_ARCHIVE_ROOT}/{extracted_target.as_posix()}"
    )
    _expect(
        target.as_posix() == target_after_stripping,
        "SOURCE_ARCHIVE",
        f"Polaris source symlink changes meaning after root stripping: "
        f"{member.name} -> {linkname}",
    )
    return target.as_posix()


def _tar_octal_size(field: bytes) -> int:
    stripped = field.rstrip(b"\0 ").lstrip(b" ")
    _expect(
        not stripped or all(0x30 <= byte <= 0x37 for byte in stripped),
        "SOURCE_ARCHIVE",
        "Polaris source archive uses a non-octal tar size",
    )
    return int(stripped or b"0", 8)


def _validate_raw_pax_payload(payload: bytes) -> None:
    _expect(
        bool(payload),
        "SOURCE_ARCHIVE",
        "Polaris source archive has an empty PAX control record",
    )
    offset = 0
    while offset < len(payload):
        separator = payload.find(b" ", offset)
        _expect(
            separator > offset,
            "SOURCE_ARCHIVE",
            "Polaris source archive has a malformed PAX control record",
        )
        length_text = payload[offset:separator]
        _expect(
            all(0x30 <= byte <= 0x39 for byte in length_text),
            "SOURCE_ARCHIVE",
            "Polaris source archive has a malformed PAX control record",
        )
        record_length = int(length_text)
        record_end = offset + record_length
        _expect(
            str(record_length).encode("ascii") == length_text
            and record_end <= len(payload)
            and record_length > len(length_text) + 3,
            "SOURCE_ARCHIVE",
            "Polaris source archive has a malformed PAX control record",
        )
        record = payload[offset:record_end]
        entry = record[len(length_text) + 1 : -1]
        key, delimiter, value = entry.partition(b"=")
        _expect(
            record.endswith(b"\n")
            and delimiter == b"="
            and bool(key)
            and b"\0" not in key
            and b"\0" not in value,
            "SOURCE_ARCHIVE",
            "Polaris source archive has a malformed PAX control record",
        )
        offset = record_end
    _expect(
        offset == len(payload),
        "SOURCE_ARCHIVE",
        "Polaris source archive has trailing PAX control data",
    )


def _validate_raw_tar_envelope(archive: Path) -> None:
    """Bound decompression and hidden tar control records before tarfile parsing."""

    decompressed_bytes = 0
    raw_headers = 0

    def read_exact(stream: BinaryIO, size: int) -> bytes:
        nonlocal decompressed_bytes
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            chunk = stream.read(min(remaining, 1_048_576))
            _expect(
                bool(chunk),
                "SOURCE_ARCHIVE",
                "truncated Polaris source archive payload",
            )
            decompressed_bytes += len(chunk)
            _expect(
                decompressed_bytes
                <= POLARIS_SOURCE_ARCHIVE_MAXIMUM_DECOMPRESSED_BYTES,
                "SOURCE_ARCHIVE",
                "Polaris source archive exceeds the decompressed-size limit",
            )
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def drain_exact(stream: BinaryIO, size: int) -> None:
        nonlocal decompressed_bytes
        remaining = size
        while remaining:
            chunk = stream.read(min(remaining, 1_048_576))
            _expect(
                bool(chunk),
                "SOURCE_ARCHIVE",
                "truncated Polaris source archive payload",
            )
            decompressed_bytes += len(chunk)
            _expect(
                decompressed_bytes
                <= POLARIS_SOURCE_ARCHIVE_MAXIMUM_DECOMPRESSED_BYTES,
                "SOURCE_ARCHIVE",
                "Polaris source archive exceeds the decompressed-size limit",
            )
            remaining -= len(chunk)

    try:
        with gzip.open(archive, mode="rb") as stream:
            zero_blocks = 0
            while zero_blocks < 2:
                header = read_exact(stream, _TAR_BLOCK_BYTES)
                if header == _TAR_ZERO_BLOCK:
                    zero_blocks += 1
                    continue
                zero_blocks = 0
                raw_headers += 1
                _expect(
                    raw_headers <= POLARIS_SOURCE_ARCHIVE_MAXIMUM_RAW_HEADERS,
                    "SOURCE_ARCHIVE",
                    "Polaris source archive exceeds the raw-header limit",
                )
                size = _tar_octal_size(header[124:136])
                typeflag = header[156:157]
                _expect(
                    typeflag not in _TAR_FORBIDDEN_HIDDEN_NAME_TYPES,
                    "SOURCE_ARCHIVE",
                    "Polaris source archive uses a hidden GNU name record",
                )
                _expect(
                    typeflag not in _TAR_FORBIDDEN_SOLARIS_PAX_TYPES,
                    "SOURCE_ARCHIVE",
                    "Polaris source archive uses an unsupported Solaris "
                    "PAX record",
                )
                maximum_payload = (
                    POLARIS_SOURCE_ARCHIVE_MAXIMUM_TAR_CONTROL_BYTES
                    if typeflag in _TAR_PAX_CONTROL_TYPES
                    else POLARIS_SOURCE_ARCHIVE_MAXIMUM_MEMBER_BYTES
                )
                _expect(
                    size <= maximum_payload,
                    "SOURCE_ARCHIVE",
                    "Polaris source archive raw member payload exceeds its limit",
                )
                padded_size = (
                    (size + _TAR_BLOCK_BYTES - 1)
                    // _TAR_BLOCK_BYTES
                    * _TAR_BLOCK_BYTES
                )
                if typeflag in _TAR_PAX_CONTROL_TYPES:
                    payload = read_exact(stream, size)
                    padding = read_exact(stream, padded_size - size)
                    _expect(
                        not any(padding),
                        "SOURCE_ARCHIVE",
                        "Polaris source archive has non-zero PAX padding",
                    )
                    _validate_raw_pax_payload(payload)
                else:
                    drain_exact(stream, padded_size)

            while True:
                trailing = stream.read(1_048_576)
                if not trailing:
                    break
                decompressed_bytes += len(trailing)
                _expect(
                    decompressed_bytes
                    <= POLARIS_SOURCE_ARCHIVE_MAXIMUM_DECOMPRESSED_BYTES,
                    "SOURCE_ARCHIVE",
                    "Polaris source archive exceeds the decompressed-size limit",
                )
                _expect(
                    not any(trailing),
                    "SOURCE_ARCHIVE",
                    "Polaris source archive has non-zero trailing tar data",
                )
    except (EOFError, OSError) as error:
        _fail("SOURCE_ARCHIVE", f"cannot read Polaris source archive: {error}")


def validate_source_archive(archive: Path) -> tuple[int, int]:
    """Validate an authenticated Polaris source archive before extraction."""

    _expect(
        archive.is_file() and not archive.is_symlink(),
        "SOURCE_ARCHIVE",
        f"Polaris source archive must be a regular file: {archive}",
    )
    try:
        archive_size = archive.stat().st_size
    except OSError as error:
        _fail("SOURCE_ARCHIVE", f"cannot stat Polaris source archive: {error}")
    _expect(
        archive_size <= POLARIS_SOURCE_ARCHIVE_MAXIMUM_BYTES,
        "SOURCE_ARCHIVE",
        "Polaris source archive exceeds the compressed-size limit",
    )
    _validate_raw_tar_envelope(archive)

    by_name: dict[str, tarfile.TarInfo] = {}
    symlinks: dict[str, tarfile.TarInfo] = {}
    paths: dict[str, PurePosixPath] = {}
    total_file_bytes = 0
    member_count = 0
    try:
        with tarfile.open(archive, mode="r|gz") as bundle:
            for member in bundle:
                member_count += 1
                _expect(
                    member_count <= POLARIS_SOURCE_ARCHIVE_MAXIMUM_MEMBERS,
                    "SOURCE_ARCHIVE",
                    "Polaris source archive exceeds the member-count limit",
                )
                path = _source_archive_member_path(member.name)
                _expect(
                    member.name not in by_name,
                    "SOURCE_ARCHIVE",
                    f"duplicate Polaris source archive member: {member.name}",
                )
                _expect(
                    member.type
                    in {
                        tarfile.REGTYPE,
                        tarfile.AREGTYPE,
                        tarfile.DIRTYPE,
                        tarfile.SYMTYPE,
                    },
                    "SOURCE_ARCHIVE",
                    f"forbidden Polaris source archive member type: "
                    f"{member.name}",
                )
                _expect(
                    member.size >= 0
                    and member.size
                    <= POLARIS_SOURCE_ARCHIVE_MAXIMUM_MEMBER_BYTES
                    and (member.isfile() or member.size == 0),
                    "SOURCE_ARCHIVE",
                    f"invalid Polaris source archive member size: {member.name}",
                )
                if member.isfile():
                    total_file_bytes += member.size
                    _expect(
                        total_file_bytes
                        <= POLARIS_SOURCE_ARCHIVE_MAXIMUM_TOTAL_FILE_BYTES,
                        "SOURCE_ARCHIVE",
                        "Polaris source archive exceeds the total file-size limit",
                    )
                pax_headers = member.pax_headers
                _expect(
                    set(pax_headers)
                    <= POLARIS_SOURCE_ARCHIVE_ALLOWED_PAX_HEADERS,
                    "SOURCE_ARCHIVE",
                    f"forbidden Polaris source PAX header: {member.name}",
                )
                pax_size = 0
                for key, value in pax_headers.items():
                    _expect(
                        isinstance(key, str) and isinstance(value, str),
                        "SOURCE_ARCHIVE",
                        f"invalid Polaris source PAX header: {member.name}",
                    )
                    key_bytes = _source_archive_text_bytes(
                        key,
                        label=f"PAX header key: {member.name}",
                        maximum_bytes=POLARIS_SOURCE_ARCHIVE_MAXIMUM_PAX_BYTES,
                    )
                    value_bytes = _source_archive_text_bytes(
                        value,
                        label=f"PAX header value: {member.name}",
                        maximum_bytes=POLARIS_SOURCE_ARCHIVE_MAXIMUM_PAX_BYTES,
                    )
                    pax_size += len(key_bytes) + len(value_bytes)
                _expect(
                    pax_size <= POLARIS_SOURCE_ARCHIVE_MAXIMUM_PAX_BYTES,
                    "SOURCE_ARCHIVE",
                    f"invalid Polaris source PAX header value: {member.name}",
                )
                _expect(
                    "comment" not in pax_headers
                    or pax_headers["comment"] == POLARIS_COMMIT,
                    "SOURCE_ARCHIVE",
                    f"Polaris source PAX comment differs from the pinned commit: "
                    f"{member.name}",
                )
                _expect(
                    "linkpath" not in pax_headers
                    or (
                        member.issym()
                        and pax_headers["linkpath"] == member.linkname
                    ),
                    "SOURCE_ARCHIVE",
                    f"Polaris source PAX linkpath is inconsistent: {member.name}",
                )
                by_name[member.name] = member
                paths[member.name] = path
                if member.issym():
                    symlinks[member.name] = member
    except (OSError, tarfile.TarError) as error:
        _fail("SOURCE_ARCHIVE", f"cannot read Polaris source archive: {error}")

    _expect(
        member_count > 0,
        "SOURCE_ARCHIVE",
        "Polaris source archive is empty",
    )
    root_member = by_name.get(POLARIS_SOURCE_ARCHIVE_ROOT)
    _expect(
        root_member is not None and root_member.isdir(),
        "SOURCE_ARCHIVE",
        "Polaris source archive root directory is missing",
    )

    for name, path in paths.items():
        for parent in path.parents:
            parent_name = parent.as_posix()
            if parent_name == ".":
                break
            parent_member = by_name.get(parent_name)
            _expect(
                parent_member is not None and parent_member.isdir(),
                "SOURCE_ARCHIVE",
                f"Polaris source archive member has a missing or non-directory "
                f"parent: {name}",
            )

    targets = {
        name: _source_archive_symlink_target(member)
        for name, member in symlinks.items()
    }
    resolved_symlinks: dict[str, str] = {}
    for name in symlinks:
        current = name
        trail: list[str] = []
        visited: set[str] = set()
        while current in symlinks and current not in resolved_symlinks:
            _expect(
                current not in visited,
                "SOURCE_ARCHIVE",
                f"Polaris source archive symlink cycle: {name}",
            )
            visited.add(current)
            trail.append(current)
            current = targets[current]
            _expect(
                current in by_name,
                "SOURCE_ARCHIVE",
                f"Polaris source archive symlink target is missing: "
                f"{name} -> {current}",
            )
        terminal = resolved_symlinks.get(current, current)
        for symlink in reversed(trail):
            resolved_symlinks[symlink] = terminal
        if symlinks[name].linkname.endswith("/"):
            _expect(
                by_name[terminal].isdir(),
                "SOURCE_ARCHIVE",
                f"Polaris source directory symlink targets a non-directory: "
                f"{name}",
            )

    return member_count, len(symlinks)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the authenticated Polaris source archive before extraction."
        )
    )
    parser.add_argument("--archive", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        member_count, symlink_count = validate_source_archive(args.archive)
    except ContractError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(
        "polaris-source-archive: "
        f"{member_count} authenticated members validated; "
        f"{symlink_count} in-root relative symlinks"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
