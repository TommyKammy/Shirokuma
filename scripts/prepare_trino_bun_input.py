#!/usr/bin/env python3
"""Verify and stage the exact Trino 483 Bun toolchain archive."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath


BUN_VERSION = "v1.3.14"
BUN_URL = (
    "https://github.com/oven-sh/bun/releases/download/"
    "bun-v1.3.14/bun-linux-aarch64.zip"
)
BUN_SHA256 = "a27ffb63a8310375836e0d6f668ae17fa8d8d18b88c37c821c65331973a19a3b"
BUN_SIZE = 35_700_603
BUN_CACHE_PATH = PurePosixPath(
    "com/github/eirslett/bun/1.3.14/bun-1.3.14.zip"
)
BUN_ORIGIN_ID = "shirokuma-bun-release"
BUN_ARCHIVE_MEMBERS = {
    "bun-linux-aarch64/": stat.S_IFDIR,
    "bun-linux-aarch64/bun": stat.S_IFREG,
}


class BunInputError(ValueError):
    """Raised when the Bun input violates the reviewed closed-world contract."""


def _fail(detail: str) -> None:
    raise BunInputError(detail)


def _regular_file(path: Path) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        _fail(f"cannot stat Bun archive {path}: {error}")
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        _fail("Bun archive must be a single regular file")
    return metadata


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        _fail(f"cannot hash Bun archive {path}: {error}")
    return digest.hexdigest()


def verify_archive(path: Path) -> None:
    metadata = _regular_file(path)
    if metadata.st_size != BUN_SIZE:
        _fail(f"Bun archive size differs: {metadata.st_size}")
    digest = _sha256(path)
    if digest != BUN_SHA256:
        _fail(f"Bun archive SHA-256 differs: {digest}")
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            observed = {member.filename for member in members}
            if observed != set(BUN_ARCHIVE_MEMBERS) or len(members) != len(observed):
                _fail(f"Bun archive members differ: {sorted(observed)!r}")
            for member in members:
                relative = PurePosixPath(member.filename)
                if (
                    relative.is_absolute()
                    or any(part in {"", ".", ".."} for part in relative.parts)
                    or member.flag_bits & 0x1
                ):
                    _fail(f"unsafe Bun archive member: {member.filename!r}")
                file_type = stat.S_IFMT(member.external_attr >> 16)
                if file_type != BUN_ARCHIVE_MEMBERS[member.filename]:
                    _fail(f"unexpected Bun archive member type: {member.filename!r}")
    except (OSError, zipfile.BadZipFile) as error:
        _fail(f"cannot inspect Bun archive: {error}")


def stage_archive(archive: Path, repository: Path) -> Path:
    verify_archive(archive)
    if not repository.is_dir() or repository.is_symlink():
        _fail("Maven repository must be a fresh real directory")
    try:
        if next(repository.iterdir(), None) is not None:
            _fail("Maven repository must be empty before Bun staging")
    except OSError as error:
        _fail(f"cannot inspect Maven repository: {error}")
    target = repository / BUN_CACHE_PATH
    marker = target.parent / "_remote.repositories"
    if target.exists() or target.is_symlink() or marker.exists() or marker.is_symlink():
        _fail("Bun cache target must not already exist")
    target.parent.mkdir(parents=True, mode=0o700)
    try:
        with archive.open("rb") as source, target.open("xb") as destination:
            shutil.copyfileobj(source, destination, length=1024 * 1024)
        target.chmod(0o600)
        marker.write_text(
            f"{target.name}>{BUN_ORIGIN_ID}=\n",
            encoding="iso-8859-1",
        )
        marker.chmod(0o600)
    except OSError as error:
        _fail(f"cannot stage Bun archive: {error}")
    verify_archive(target)
    return target


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--archive", type=Path, required=True)
    stage = commands.add_parser("stage")
    stage.add_argument("--archive", type=Path, required=True)
    stage.add_argument("--repository", type=Path, required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        if arguments.command == "verify":
            verify_archive(arguments.archive)
        else:
            stage_archive(arguments.archive, arguments.repository)
    except BunInputError as error:
        raise SystemExit(f"Trino Bun input rejected: {error}") from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
