#!/usr/bin/env python3
"""Verify and stage the exact Trino 483 Bun toolchain archive."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import os
import shutil
import ssl
import stat
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlsplit


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
BUN_ALLOWED_HTTPS_ORIGINS = (
    "https://github.com",
    "https://release-assets.githubusercontent.com",
)
BUN_MAX_REDIRECTS = 5
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


def _validated_https_origin(url: str) -> str:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError) as error:
        _fail(f"invalid Bun download URL: {error}")
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname is None
        or port not in {None, 443}
        or not parsed.path.startswith("/")
        or parsed.fragment
    ):
        _fail(f"unsafe Bun download URL: {url!r}")
    origin = f"https://{parsed.hostname}"
    if origin not in BUN_ALLOWED_HTTPS_ORIGINS:
        _fail(f"Bun download origin is not allowlisted: {origin!r}")
    return origin


def _open_https(
    url: str,
    tls_context: ssl.SSLContext,
) -> tuple[http.client.HTTPSConnection, http.client.HTTPResponse]:
    parsed = urlsplit(url)
    target = parsed.path
    if parsed.query:
        target = f"{target}?{parsed.query}"
    connection = http.client.HTTPSConnection(
        parsed.hostname,
        port=parsed.port or 443,
        timeout=60,
        context=tls_context,
    )
    try:
        connection.request(
            "GET",
            target,
            headers={
                "Accept-Encoding": "identity",
                "User-Agent": "Shirokuma-Trino-Bun-Input/1",
            },
        )
        return connection, connection.getresponse()
    except (OSError, http.client.HTTPException) as error:
        connection.close()
        _fail(f"Bun HTTPS request failed: {error}")


def download_archive(url: str, output: Path) -> tuple[str, ...]:
    if url != BUN_URL:
        _fail("Bun download URL differs from the reviewed release URL")
    if not output.parent.is_dir() or output.parent.is_symlink():
        _fail("Bun download directory must be a real directory")
    if output.exists() or output.is_symlink():
        _fail("Bun download output must not already exist")

    tls_context = ssl.create_default_context()
    tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
    current = url
    seen: set[str] = set()
    origins: list[str] = []
    created = False
    redirect_statuses = {301, 302, 303, 307, 308}

    try:
        for redirect_count in range(BUN_MAX_REDIRECTS + 1):
            origin = _validated_https_origin(current)
            if current in seen:
                _fail("Bun download redirect cycle detected")
            seen.add(current)
            origins.append(origin)
            connection, response = _open_https(current, tls_context)
            try:
                if response.status in redirect_statuses:
                    locations = response.headers.get_all("Location", [])
                    if len(locations) != 1:
                        _fail("Bun redirect must contain exactly one Location header")
                    if redirect_count == BUN_MAX_REDIRECTS:
                        _fail("Bun download exceeded the redirect limit")
                    redirected = urljoin(current, locations[0])
                    _validated_https_origin(redirected)
                    current = redirected
                    continue
                if response.status != 200:
                    _fail(f"Bun download returned HTTP {response.status}")
                encodings = response.headers.get_all("Content-Encoding", [])
                if any(value.lower() != "identity" for value in encodings):
                    _fail("Bun download used an unexpected content encoding")
                lengths = response.headers.get_all("Content-Length", [])
                if lengths and (
                    len(lengths) != 1
                    or not lengths[0].isdigit()
                    or int(lengths[0]) != BUN_SIZE
                ):
                    _fail("Bun download Content-Length differs")
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                descriptor = os.open(output, flags, 0o600)
                created = True
                total = 0
                with os.fdopen(descriptor, "wb") as destination:
                    while chunk := response.read(1024 * 1024):
                        total += len(chunk)
                        if total > BUN_SIZE:
                            _fail("Bun download exceeds the reviewed byte size")
                        destination.write(chunk)
                verify_archive(output)
                return tuple(origins)
            finally:
                response.close()
                connection.close()
    except (OSError, http.client.HTTPException, ssl.SSLError) as error:
        _fail(f"cannot download Bun archive: {error}")
    finally:
        if created and output.exists():
            try:
                verify_archive(output)
            except BunInputError:
                output.unlink(missing_ok=True)
    _fail("Bun download did not produce the reviewed archive")


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
    download = commands.add_parser("download")
    download.add_argument("--url", required=True)
    download.add_argument("--archive", type=Path, required=True)
    stage = commands.add_parser("stage")
    stage.add_argument("--archive", type=Path, required=True)
    stage.add_argument("--repository", type=Path, required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        if arguments.command == "verify":
            verify_archive(arguments.archive)
        elif arguments.command == "download":
            origins = download_archive(arguments.url, arguments.archive)
            print(
                "Bun archive acquired through reviewed HTTPS origins: "
                + " -> ".join(origins)
            )
        else:
            stage_archive(arguments.archive, arguments.repository)
    except BunInputError as error:
        raise SystemExit(f"Trino Bun input rejected: {error}") from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
