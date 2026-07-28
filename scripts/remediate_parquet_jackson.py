#!/usr/bin/env python3
"""Build-boundary verifier for the Parquet Jackson 1.17.1 remediation."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import stat
import subprocess
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath


SOURCE_REPOSITORY = "https://github.com/apache/parquet-java"
RELEASE_TAG = "apache-parquet-1.17.1"
RELEASE_TAG_OBJECT = "1f54ba44afb285fecbaf54bde5c0afa259327fc4"
RC_TAG = "apache-parquet-1.17.1-rc0"
RC_TAG_OBJECT = "172d200a7eb81161345bdccaf628af34178fc479"
COMMIT_SHA = "78a8d3230eb4769db93de5f2f2e18363c04cae81"
TREE_SHA = "28b877df95a7a661361b8776f6ebe21d73d8da6d"
ROOT_POM = Path("pom.xml")
ROOT_POM_SIZE = 24_493
ROOT_POM_PREIMAGE = (
    "bfe7519b9886e9df51bfef8be52064b3aadcbf9ae21c77402d8a66837aa5442f"
)
ROOT_POM_POSTIMAGE = (
    "e07982c0f114b592c06c2aba1254df9c280b69a2dd27f3a0739421fe84d12efa"
)
REPLACEMENTS = (
    (
        b"<jackson.version>2.21.3</jackson.version>",
        b"<jackson.version>2.21.4</jackson.version>",
    ),
    (
        b"<jackson-databind.version>2.21.3</jackson-databind.version>",
        b"<jackson-databind.version>2.21.4</jackson-databind.version>",
    ),
)
GROUP_PATH = Path("org/apache/parquet/parquet-jackson/1.17.1")
ARTIFACT_FILES = (
    "parquet-jackson-1.17.1.jar",
    "parquet-jackson-1.17.1.pom",
)
RESOLUTION_ORIGIN_ID = "shirokuma-central"
SEALED_ORIGIN_ID = "shirokuma-parquet-remediation"
EXPECTED_SHADED_PREFIX = "shaded/parquet/com/fasterxml/jackson/"


class RemediationError(ValueError):
    """The source or built artifact crossed the authorized boundary."""


def _fail(message: str) -> None:
    raise RemediationError(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        _fail(f"cannot hash {path}: {error}")
    return digest.hexdigest()


def _regular_file(path: Path) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        _fail(f"cannot inspect {path}: {error}")
    if (
        not stat.S_ISREG(metadata.st_mode)
        or path.is_symlink()
        or metadata.st_nlink != 1
    ):
        _fail(f"expected one regular unlinked file: {path}")
    return metadata


def _git(checkout: Path, *arguments: str) -> str:
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=checkout,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        _fail(f"git {' '.join(arguments)} failed: {error}")


def _verify_git_identity(checkout: Path, *, pristine: bool) -> None:
    expected_status = "" if pristine else "M pom.xml"
    observed = {
        "commit": _git(checkout, "rev-parse", "HEAD"),
        "tree": _git(checkout, "rev-parse", "HEAD^{tree}"),
        "remote": _git(checkout, "remote", "get-url", "origin"),
        "release_tag": _git(checkout, "rev-parse", f"refs/tags/{RELEASE_TAG}"),
        "release_commit": _git(
            checkout, "rev-parse", f"refs/tags/{RELEASE_TAG}^{{}}"
        ),
        "rc_tag": _git(checkout, "rev-parse", f"refs/tags/{RC_TAG}"),
        "rc_commit": _git(checkout, "rev-parse", f"refs/tags/{RC_TAG}^{{}}"),
        "status": _git(
            checkout, "status", "--porcelain=v1", "--untracked-files=all"
        ),
    }
    expected = {
        "commit": COMMIT_SHA,
        "tree": TREE_SHA,
        "remote": SOURCE_REPOSITORY,
        "release_tag": RELEASE_TAG_OBJECT,
        "release_commit": COMMIT_SHA,
        "rc_tag": RC_TAG_OBJECT,
        "rc_commit": COMMIT_SHA,
        "status": expected_status,
    }
    if observed != expected:
        _fail(f"source identity differs: {observed!r}")
    if not pristine:
        changed = _git(checkout, "diff", "--name-only")
        if changed != ROOT_POM.as_posix():
            _fail(f"source remediation changed an unauthorized path: {changed!r}")
        _git(checkout, "diff", "--check")


def prepare_source(checkout: Path) -> None:
    checkout = checkout.resolve()
    _verify_git_identity(checkout, pristine=True)
    pom = checkout / ROOT_POM
    metadata = _regular_file(pom)
    if metadata.st_size != ROOT_POM_SIZE or _sha256(pom) != ROOT_POM_PREIMAGE:
        _fail("root pom.xml preimage differs")
    try:
        payload = pom.read_bytes()
    except OSError as error:
        _fail(f"cannot read {pom}: {error}")
    for before, after in REPLACEMENTS:
        if payload.count(before) != 1 or after in payload:
            _fail(f"authorized replacement precondition differs: {before!r}")
        payload = payload.replace(before, after)
    if len(payload) != ROOT_POM_SIZE:
        _fail("root pom.xml size changed")
    try:
        pom.write_bytes(payload)
        pom.chmod(stat.S_IMODE(metadata.st_mode))
    except OSError as error:
        _fail(f"cannot apply root pom.xml remediation: {error}")
    if _sha256(pom) != ROOT_POM_POSTIMAGE:
        _fail("root pom.xml postimage differs")
    _verify_git_identity(checkout, pristine=False)


def _verify_prepared_source(checkout: Path) -> None:
    checkout = checkout.resolve()
    _verify_git_identity(checkout, pristine=False)
    pom = checkout / ROOT_POM
    if _regular_file(pom).st_size != ROOT_POM_SIZE:
        _fail("prepared root pom.xml size differs")
    if _sha256(pom) != ROOT_POM_POSTIMAGE:
        _fail("prepared root pom.xml postimage differs")


def _verify_built_pom(path: Path) -> None:
    payload = _regular_file(path)
    if payload.st_size == 0:
        _fail("built parquet-jackson POM is empty")
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as error:
        _fail(f"cannot parse built parquet-jackson POM: {error}")
    namespace = {"m": "http://maven.apache.org/POM/4.0.0"}
    artifact = root.findtext("m:artifactId", namespaces=namespace)
    parent_version = root.findtext("m:parent/m:version", namespaces=namespace)
    dependencies = root.findall("m:dependencies/m:dependency", namespace)
    exposed_jackson = [
        (
            dependency.findtext("m:groupId", namespaces=namespace),
            dependency.findtext("m:artifactId", namespaces=namespace),
        )
        for dependency in dependencies
        if (dependency.findtext("m:groupId", namespaces=namespace) or "").startswith(
            "com.fasterxml.jackson"
        )
    ]
    if (
        artifact != "parquet-jackson"
        or parent_version != "1.17.1"
        or exposed_jackson
    ):
        _fail(
            "built parquet-jackson POM is not the dependency-reduced "
            f"1.17.1 POM: jackson={exposed_jackson!r}"
        )


def _verify_built_jar(path: Path) -> None:
    if _regular_file(path).st_size == 0:
        _fail("built parquet-jackson JAR is empty")
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                _fail("built parquet-jackson JAR contains duplicate entries")
            shaded_entries: list[str] = []
            contains_fixed_version = False
            for name in names:
                pure = PurePosixPath(name)
                if (
                    pure.is_absolute()
                    or ".." in pure.parts
                    or "\\" in name
                    or not name
                ):
                    _fail(f"unsafe parquet-jackson JAR entry: {name!r}")
                if name.startswith("com/fasterxml/jackson/"):
                    _fail(f"unshaded Jackson entry is forbidden: {name}")
                if name.startswith(EXPECTED_SHADED_PREFIX) and not name.endswith("/"):
                    shaded_entries.append(name)
                    payload = archive.read(name)
                    if b"2.21.3" in payload:
                        _fail(f"vulnerable Jackson version remains in {name}")
                    contains_fixed_version = (
                        contains_fixed_version or b"2.21.4" in payload
                    )
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        _fail(f"cannot inspect built parquet-jackson JAR: {error}")
    if not shaded_entries or not contains_fixed_version:
        _fail("built parquet-jackson JAR does not prove shaded Jackson 2.21.4")


def _artifact_paths(repository: Path) -> tuple[Path, Path]:
    directory = repository.resolve() / GROUP_PATH
    return tuple(directory / name for name in ARTIFACT_FILES)  # type: ignore[return-value]


def _marker(origin_id: str) -> bytes:
    return (
        "\n".join(f"{name}>{origin_id}=" for name in ARTIFACT_FILES) + "\n"
    ).encode("ascii")


def stage_artifact(
    checkout: Path, build_repository: Path, target_repository: Path
) -> None:
    _verify_prepared_source(checkout)
    jar, pom = _artifact_paths(build_repository)
    _verify_built_jar(jar)
    _verify_built_pom(pom)
    target_jar, target_pom = _artifact_paths(target_repository)
    target_directory = target_jar.parent
    if target_directory.exists() or target_directory.is_symlink():
        _fail(f"target artifact directory already exists: {target_directory}")
    try:
        target_directory.mkdir(parents=True, mode=0o700)
        for source, target in ((jar, target_jar), (pom, target_pom)):
            shutil.copyfile(source, target)
            target.chmod(0o644)
        (target_directory / "_remote.repositories").write_bytes(
            _marker(RESOLUTION_ORIGIN_ID)
        )
        (target_directory / "_remote.repositories").chmod(0o644)
    except OSError as error:
        _fail(f"cannot stage parquet-jackson remediation: {error}")


def seal_artifact(build_repository: Path, target_repository: Path) -> None:
    source_paths = _artifact_paths(build_repository)
    target_paths = _artifact_paths(target_repository)
    for source, target in zip(source_paths, target_paths):
        _regular_file(source)
        _regular_file(target)
        if (
            source.stat().st_size != target.stat().st_size
            or _sha256(source) != _sha256(target)
        ):
            _fail(f"resolved parquet-jackson artifact was replaced: {target}")
    marker = target_paths[0].parent / "_remote.repositories"
    _regular_file(marker)
    try:
        marker.write_bytes(_marker(SEALED_ORIGIN_ID))
        marker.chmod(0o644)
    except OSError as error:
        _fail(f"cannot seal parquet-jackson remediation origin: {error}")


def compare_artifacts(first_repository: Path, second_repository: Path) -> None:
    for first, second in zip(
        _artifact_paths(first_repository),
        _artifact_paths(second_repository),
    ):
        _regular_file(first)
        _regular_file(second)
        if (
            first.stat().st_size != second.stat().st_size
            or _sha256(first) != _sha256(second)
        ):
            _fail(f"independent parquet-jackson builds differ: {first.name}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare-source")
    prepare.add_argument("--checkout", type=Path, required=True)
    stage = commands.add_parser("stage-artifact")
    stage.add_argument("--checkout", type=Path, required=True)
    stage.add_argument("--build-repository", type=Path, required=True)
    stage.add_argument("--target-repository", type=Path, required=True)
    seal = commands.add_parser("seal-artifact")
    seal.add_argument("--build-repository", type=Path, required=True)
    seal.add_argument("--target-repository", type=Path, required=True)
    compare = commands.add_parser("compare-artifacts")
    compare.add_argument("--first-repository", type=Path, required=True)
    compare.add_argument("--second-repository", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "prepare-source":
            prepare_source(args.checkout)
        elif args.command == "stage-artifact":
            stage_artifact(
                args.checkout,
                args.build_repository,
                args.target_repository,
            )
        elif args.command == "seal-artifact":
            seal_artifact(args.build_repository, args.target_repository)
        else:
            compare_artifacts(
                args.first_repository,
                args.second_repository,
            )
    except RemediationError as error:
        print(f"parquet-jackson remediation rejected: {error}", file=os.sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
