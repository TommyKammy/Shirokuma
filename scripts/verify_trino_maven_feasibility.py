#!/usr/bin/env python3
"""Create and verify the bounded Trino 483 Maven feasibility evidence."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tarfile
import tempfile
import zlib
from pathlib import Path, PurePosixPath
from typing import Any

import verify_trino_dependency_publisher as publisher


WORKFLOW_PATH = Path(
    ".github/workflows/trino-maven-remediation-feasibility.yml"
)
CANDIDATE_PATCH_PATH = Path(
    "docs/design/evidence/trino/"
    "run-30693677356-proposed-source-overlay.patch"
)
EXPECTED_CANDIDATE_PATCH_SHA256 = (
    "731e76f296a725d34ea9e226a1815782168cae3890424e69f76a05530afc15be"
)
EXPECTED_CANDIDATE_PATCH_BYTES = 8163
EXPECTED_BASELINE_SHA256 = (
    "8d342215a3c748f7965f0a82e847cab13587b94171d9d1422922b665475109c1"
)
EXPECTED_POSTIMAGE_SHA256 = (
    "871c6b21cf9fc70c455d21b64d24dd4501a8b5943242418edc2b2f5cfe14fab8"
)
EXPECTED_BUILDER = (
    "docker.io/library/maven@sha256:"
    "7e461cec477077c1d9e50b13df8aef9018764410f4c4cd7c34803f10c4c99e4c"
)
EXPECTED_BUILDER_INDEX_SHA256 = (
    "7e461cec477077c1d9e50b13df8aef9018764410f4c4cd7c34803f10c4c99e4c"
)
EXPECTED_BUILDER_ARM64_MANIFEST = (
    "sha256:5476bfca9d0a6485b7161f6863123f7e6822336de4177273b47b5ec38ffd573a"
)
EXPECTED_SELECTED_REACTOR = (
    ":trino-server,:trino-server-core,:trino-server-main,"
    ":trino-hdfs,:trino-iceberg"
)
EXPECTED_GOAL = "dependency:resolve-plugins -DskipTests"
EXPECTED_MANIFEST_SCHEMA_VERSION = 1
EXPECTED_MANIFEST_MEDIA_TYPE = (
    "application/vnd.shirokuma.maven-feasibility-inputs.v1+json"
)
EXPECTED_REPOSITORY_LAYOUT = "maven2"
EXPECTED_GITHUB_REPOSITORY = "TommyKammy/Shirokuma"
EXPECTED_GITHUB_SERVER_URL = "https://github.com"
EXPECTED_WORKFLOW_REF_PREFIX = (
    "TommyKammy/Shirokuma/.github/workflows/"
    "trino-maven-remediation-feasibility.yml@"
)
EXPECTED_WORKFLOW_TRIGGER_BLOCK = """on:
  pull_request:
    paths:
      - .github/workflows/trino-maven-remediation-feasibility.yml
      - bootstrap/trino/v483/patches/0001-shirokuma-web-ui-security.patch
      - bootstrap/trino/v483/patches/0002-shirokuma-iceberg-only-maven-closure.patch
      - bootstrap/trino/v483/maven-policy/.mvn/jvm.config
      - bootstrap/trino/v483/maven-scm-manager-plexus-2.2.1-hardened.pom
      - bootstrap/trino/v483/maven-scm-provider-gitexe-2.2.1-hardened.pom
      - bootstrap/trino/v483/settings.xml
      - bootstrap/trino/v483/trusted-build-contract.json
      - docs/design/07_ADR/ADR-0028_Keep_Trino_483_publisher_blocked_for_refreshed_Maven_findings.md
      - docs/design/evidence/trino/run-30693677356-post-adr-0027-pom.xml.gz
      - docs/design/evidence/trino/run-30693677356-proposed-source-overlay.patch
      - docs/design/evidence/trino/run-30693677356-maven-vulnerability-classification.json
      - docs/design/evidence/trino/run-30731801825-maven-feasibility-validation.json
      - docs/design/evidence/trino/run-30731801825-maven-feasibility-artifact-receipt.json
      - scripts/verify_trino_dependency_publisher.py
      - scripts/verify_trino_maven_feasibility.py
      - tests/test_trino_dependency_publisher.py
      - tests/test_trino_maven_feasibility.py
  workflow_dispatch:
"""
EXPECTED_WORKFLOW_STEPS = (
    "Check out the reviewed feasibility policy",
    "Validate the fail-closed preauthorization contract",
    "Verify the native arm64 builder substrate",
    "Fetch and prepare the exact candidate source",
    "Resolve the selected plugin closure online",
    "Replay the selected plugin closure with no network",
    "Finalize and audit the retained validation record",
    "Retain the review-only feasibility inputs and outputs",
)
EXPECTED_WORKFLOW_SHA256 = (
    "7b234bd5e435b681ea837db800c35fda43b2a234ae49b95a1fe7057555ef6d73"
)
EXPECTED_ONLINE_NETWORK = "unrestricted; Maven transfers audited separately"
EXPECTED_ONLINE_COMMAND = (
    "mvn --batch-mode --show-version --errors --strict-checksums "
    "--ignore-transitive-repositories --settings /policy/settings.xml "
    "-Dmaven.repo.local=/m2 --file /workspace/pom.xml "
    f"-pl '{EXPECTED_SELECTED_REACTOR}' -am {EXPECTED_GOAL}"
)
EXPECTED_OFFLINE_COMMAND = (
    "mvn --offline --batch-mode --show-version --errors --strict-checksums "
    "--ignore-transitive-repositories --settings /policy/settings.xml "
    "-Dmaven.repo.local=/m2 "
    "-Daether.syncContext.named.basedir.locksDir=/tmp/maven-locks "
    "--file /workspace/pom.xml "
    f"-pl '{EXPECTED_SELECTED_REACTOR}' -am {EXPECTED_GOAL}"
)
EXPECTED_REPLACEMENT_METADATA = {
    (
        "org/apache/velocity/velocity-engine-core/2.4.1/"
        "velocity-engine-core-2.4.1.jar"
    ): {
        "mode": "0644",
        "sha256": (
            "1c19157d1171d560088e485be97c93a7a2f7e9f56e517f0a30273c5c39df6231"
        ),
        "bytes": 515658,
    },
    "org/codehaus/plexus/plexus-utils/4.0.3/plexus-utils-4.0.3.jar": {
        "mode": "0644",
        "sha256": (
            "421ff6ed146c53a9d4eaade8f629b52b12af7187101f1bd06c2c64e904019f3c"
        ),
        "bytes": 193088,
    },
}
EXPECTED_REPLACEMENT_INPUTS = tuple(EXPECTED_REPLACEMENT_METADATA)
HARDENED_SCM_POM_SOURCE = Path(
    "bootstrap/trino/v483/maven-scm-provider-gitexe-2.2.1-hardened.pom"
)
SCM_POM_PATH = (
    "org/apache/maven/scm/maven-scm-provider-gitexe/2.2.1/"
    "maven-scm-provider-gitexe-2.2.1.pom"
)
SCM_POM_CHECKSUM_PATH = f"{SCM_POM_PATH}.sha1"
SCM_POM_PREIMAGE_SHA256 = (
    "81521b7b72ca795c95ef5f377e410e7d2644d2ffbce03e34eeea73246847be08"
)
SCM_POM_PREIMAGE_BYTES = 2689
SCM_POM_PREIMAGE_SHA1 = b"84e3adebd5bcda593e6b9bfd7bb5e3b3d9d17796"
SCM_POM_POSTIMAGE_SHA256 = (
    "0652487bb3cd532ce6ba9fd841c7f2346c1192b3271996a06ddd50f3052186a6"
)
SCM_POM_POSTIMAGE_BYTES = 2720
SCM_POM_POSTIMAGE_SHA1 = b"a8630355e52d9c81dbd6ec117820bb58b6355f4a"
HARDENED_SCM_MANAGER_POM_SOURCE = Path(
    "bootstrap/trino/v483/maven-scm-manager-plexus-2.2.1-hardened.pom"
)
SCM_MANAGER_POM_PATH = (
    "org/apache/maven/scm/maven-scm-manager-plexus/2.2.1/"
    "maven-scm-manager-plexus-2.2.1.pom"
)
SCM_MANAGER_POM_CHECKSUM_PATH = f"{SCM_MANAGER_POM_PATH}.sha1"
SCM_MANAGER_POM_PREIMAGE_SHA256 = (
    "7e1458bc8212c430c269c3d59063640b2164e6750f23539e6d6ca89d7207b3c5"
)
SCM_MANAGER_POM_PREIMAGE_BYTES = 1802
SCM_MANAGER_POM_PREIMAGE_SHA1 = b"7ce2798686f27b4d5056ca967625002fe24fbfb8"
SCM_MANAGER_POM_POSTIMAGE_SHA256 = (
    "4e7b25d9f3dfd21b874593edf794270888c8ef13bc29394b0da1c1cbefa41c43"
)
SCM_MANAGER_POM_POSTIMAGE_BYTES = 1957
SCM_MANAGER_POM_POSTIMAGE_SHA1 = b"eb1b7ab169dc923806b0040631a45dc83d0b83e8"
EXPECTED_HARDENED_METADATA = {
    SCM_POM_PATH: {
        "mode": "0644",
        "sha256": SCM_POM_POSTIMAGE_SHA256,
        "bytes": SCM_POM_POSTIMAGE_BYTES,
    },
    SCM_POM_CHECKSUM_PATH: {
        "mode": "0644",
        "sha256": (
            "a9a85b2193053267f68dfacb62896caa532afe49bafa4540134df7a6abed5beb"
        ),
        "bytes": 40,
    },
    SCM_MANAGER_POM_PATH: {
        "mode": "0644",
        "sha256": SCM_MANAGER_POM_POSTIMAGE_SHA256,
        "bytes": SCM_MANAGER_POM_POSTIMAGE_BYTES,
    },
    SCM_MANAGER_POM_CHECKSUM_PATH: {
        "mode": "0644",
        "sha256": (
            "8f04dcac652c18121420956ca62c7efb0166eefaa24400129d2a01433133de63"
        ),
        "bytes": 40,
    },
}
VULNERABLE_COORDINATES = (
    re.compile(r"commons-io:commons-io:(?:jar:)?2\.8\.0(?:[:\s]|$)"),
    re.compile(
        r"org\.apache\.velocity:velocity-engine-core:(?:jar:)?2\.3(?:[:\s]|$)"
    ),
    re.compile(
        r"org\.codehaus\.plexus:plexus-utils:(?:jar:)?4\.0\.[12](?:[:\s]|$)"
    ),
)
VULNERABLE_INPUTS = (
    "commons-io/commons-io/2.8.0/commons-io-2.8.0.jar",
    "org/apache/velocity/velocity-engine-core/2.3/velocity-engine-core-2.3.jar",
    "org/codehaus/plexus/plexus-utils/4.0.1/plexus-utils-4.0.1.jar",
    "org/codehaus/plexus/plexus-utils/4.0.2/plexus-utils-4.0.2.jar",
)
RECORD_NAME = "validation-record.json"
MANIFEST_NAME = "offline-input-manifest.json"
ARCHIVE_NAME = "offline-maven-repository.tar.gz"
ONLINE_LOG_NAME = "online-resolve-plugins.log"
OFFLINE_LOG_NAME = "offline-resolve-plugins.log"
TOOLCHAIN_NAME = "toolchain.json"
BUILDER_INDEX_NAME = "builder-index.json"
MAVEN_VERSION_NAME = "maven-version.txt"
GLOBAL_SETTINGS_NAME = "maven-global-settings.xml"
EXPECTED_EVIDENCE_FILES = {
    RECORD_NAME,
    MANIFEST_NAME,
    ARCHIVE_NAME,
    ONLINE_LOG_NAME,
    OFFLINE_LOG_NAME,
    TOOLCHAIN_NAME,
    BUILDER_INDEX_NAME,
    MAVEN_VERSION_NAME,
    GLOBAL_SETTINGS_NAME,
}
EXPECTED_GZIP_HEADER = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff"
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000
MAX_ARCHIVE_MEMBER_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_FILE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_EXPANDED_BYTES = 640 * 1024 * 1024
MAX_ARCHIVE_COMPRESSION_RATIO = 100
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_LOG_BYTES = 16 * 1024 * 1024
MAX_MAVEN_VERSION_BYTES = 64 * 1024


class EvidenceError(RuntimeError):
    """Raised when feasibility evidence fails closed."""


def _fail(code: str, detail: str) -> None:
    raise EvidenceError(f"{code}: {detail}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _identity(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        _fail("EVIDENCE_FILE", f"not a regular file: {path}")
    return {
        "path": path.name,
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
    }


def _write_json(path: Path, document: object) -> None:
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_bounded_file(path: Path, *, code: str, max_bytes: int) -> bytes:
    try:
        expected = path.lstat()
    except OSError as error:
        _fail(code, f"{path}: {error}")
    if (
        not stat.S_ISREG(expected.st_mode)
        or expected.st_nlink != 1
        or not 0 < expected.st_size <= max_bytes
    ):
        _fail(code, f"unsafe or oversized file: {path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        _fail(code, f"{path}: {error}")
    try:
        observed = os.fstat(descriptor)
        if (
            not stat.S_ISREG(observed.st_mode)
            or observed.st_nlink != 1
            or observed.st_dev != expected.st_dev
            or observed.st_ino != expected.st_ino
            or observed.st_size != expected.st_size
        ):
            _fail(code, f"file changed while opening: {path}")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            payload = source.read(max_bytes + 1)
        if len(payload) != observed.st_size or len(payload) > max_bytes:
            _fail(code, f"file changed while reading: {path}")
        return payload
    finally:
        os.close(descriptor)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(
            _read_bounded_file(
                path,
                code="EVIDENCE_JSON",
                max_bytes=MAX_JSON_BYTES,
            )
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        _fail("EVIDENCE_JSON", f"{path}: {error}")
    if not isinstance(document, dict):
        _fail("EVIDENCE_JSON", f"top level must be an object: {path}")
    return document


def apply_candidate(root: Path, checkout: Path) -> None:
    root = root.resolve()
    checkout = checkout.resolve()
    publisher.apply_source_overlay(root, checkout)
    pom = checkout / "pom.xml"
    if _sha256(pom) != EXPECTED_BASELINE_SHA256:
        _fail("CANDIDATE_PREIMAGE", "post-ADR-0027 pom.xml hash differs")
    patch = root / CANDIDATE_PATCH_PATH
    if (
        _sha256(patch) != EXPECTED_CANDIDATE_PATCH_SHA256
        or patch.stat().st_size != EXPECTED_CANDIDATE_PATCH_BYTES
    ):
        _fail("CANDIDATE_PATCH", "candidate patch identity differs")
    publisher._validate_zero_context_patch(patch, {"pom.xml"})
    command = [
        "git",
        "apply",
        "--unidiff-zero",
        "--whitespace=error-all",
        str(patch),
    ]
    try:
        subprocess.run(
            [*command[:2], "--check", *command[2:]],
            cwd=checkout,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            command,
            cwd=checkout,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        _fail("CANDIDATE_APPLY", str(error))
    verify_candidate(checkout)


def _expected_candidate_source_postimages() -> dict[str, str]:
    expected: dict[str, str] = {}
    for name, boundary in (
        ("source overlay", publisher.EXPECTED_SOURCE_OVERLAY),
        (
            "distribution remediation",
            publisher.EXPECTED_DISTRIBUTION_REMEDIATION,
        ),
    ):
        permitted = boundary.get("permitted_paths")
        postimages = boundary.get("postimages")
        if (
            not isinstance(permitted, list)
            or not all(isinstance(path, str) for path in permitted)
            or not isinstance(postimages, dict)
            or not all(
                isinstance(path, str) and isinstance(digest, str)
                for path, digest in postimages.items()
            )
            or len(permitted) != len(set(permitted))
            or set(permitted) != set(postimages)
        ):
            _fail("CANDIDATE_SOURCE", f"{name} path identities differ")
        overlap = set(expected) & set(postimages)
        if overlap:
            _fail(
                "CANDIDATE_SOURCE",
                f"authorized source boundaries overlap: {sorted(overlap)}",
            )
        expected.update(postimages)
    if expected.get("pom.xml") != EXPECTED_BASELINE_SHA256:
        _fail(
            "CANDIDATE_SOURCE",
            "candidate preimage is not the authorized overlay postimage",
        )
    expected["pom.xml"] = EXPECTED_POSTIMAGE_SHA256
    return expected


def verify_candidate(
    checkout: Path, *, authorization_root: Path | None = None
) -> None:
    checkout = checkout.resolve()
    pom = checkout / "pom.xml"
    if _sha256(pom) != EXPECTED_POSTIMAGE_SHA256:
        _fail("CANDIDATE_POSTIMAGE", "candidate pom.xml hash differs")
    expected = _expected_candidate_source_postimages()
    tracked = subprocess.run(
        [
            "git",
            "diff",
            "--name-only",
            "--no-ext-diff",
            "--ignore-submodules=none",
            "HEAD",
            "--",
        ],
        cwd=checkout,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    if set(tracked) != set(expected) or len(tracked) != len(expected):
        _fail("CANDIDATE_SOURCE", f"tracked source changes differ: {tracked}")
    for relative, digest in expected.items():
        path = checkout / relative
        try:
            status = path.lstat()
        except OSError as error:
            _fail("CANDIDATE_SOURCE", f"{relative}: {error}")
        if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
            _fail("CANDIDATE_SOURCE", f"not a regular source file: {relative}")
        if _sha256(path) != digest:
            _fail("CANDIDATE_SOURCE", f"source postimage differs: {relative}")
    if subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=checkout,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines():
        _fail("CANDIDATE_APPLY", "candidate created untracked source files")
    if authorization_root is not None:
        publisher.authorize_use(
            authorization_root.resolve(),
            validation_point="before_dependency_resolution",
        )


def _repository_entries(repository: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(repository.rglob("*")):
        relative = path.relative_to(repository).as_posix()
        status = path.lstat()
        if stat.S_ISDIR(status.st_mode):
            continue
        if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
            _fail("OFFLINE_INPUT", f"unsafe repository entry: {relative}")
        entries.append(
            {
                "path": relative,
                "mode": f"{stat.S_IMODE(status.st_mode):04o}",
                "sha256": _sha256(path),
                "bytes": status.st_size,
            }
        )
    entries.sort(key=lambda entry: entry["path"])
    if not entries:
        _fail("OFFLINE_INPUT", "repository is empty")
    observed = {entry["path"] for entry in entries}
    vulnerable = sorted(set(VULNERABLE_INPUTS) & observed)
    if vulnerable:
        _fail("VULNERABLE_INPUT", f"blocked inputs retained: {vulnerable}")
    missing = sorted(set(EXPECTED_REPLACEMENT_INPUTS) - observed)
    if missing:
        _fail("OFFLINE_INPUT", f"replacement inputs missing: {missing}")
    _verify_replacement_metadata(entries)
    _verify_hardened_metadata(entries)
    return entries


def _verify_replacement_metadata(entries: list[dict[str, Any]]) -> None:
    observed = {entry["path"]: entry for entry in entries}
    for path, expected in EXPECTED_REPLACEMENT_METADATA.items():
        entry = observed.get(path)
        if entry is None or {
            key: entry.get(key) for key in ("mode", "sha256", "bytes")
        } != expected:
            _fail("REPLACEMENT_INPUT", f"identity differs: {path}")


def _verify_hardened_metadata(entries: list[dict[str, Any]]) -> None:
    observed = {entry["path"]: entry for entry in entries}
    for path, expected in EXPECTED_HARDENED_METADATA.items():
        entry = observed.get(path)
        if entry is None or {
            key: entry.get(key) for key in ("mode", "sha256", "bytes")
        } != expected:
            _fail("HARDENED_METADATA", f"identity differs: {path}")


def prune_vulnerable_inputs(repository: Path, root: Path = Path(".")) -> None:
    repository = repository.resolve()
    root = root.resolve()
    if not repository.is_dir():
        _fail("OFFLINE_INPUT", f"repository not found: {repository}")
    remediations = (
        (
            HARDENED_SCM_POM_SOURCE,
            SCM_POM_PATH,
            SCM_POM_CHECKSUM_PATH,
            SCM_POM_PREIMAGE_SHA256,
            SCM_POM_PREIMAGE_BYTES,
            SCM_POM_PREIMAGE_SHA1,
            SCM_POM_POSTIMAGE_SHA256,
            SCM_POM_POSTIMAGE_BYTES,
            SCM_POM_POSTIMAGE_SHA1,
        ),
        (
            HARDENED_SCM_MANAGER_POM_SOURCE,
            SCM_MANAGER_POM_PATH,
            SCM_MANAGER_POM_CHECKSUM_PATH,
            SCM_MANAGER_POM_PREIMAGE_SHA256,
            SCM_MANAGER_POM_PREIMAGE_BYTES,
            SCM_MANAGER_POM_PREIMAGE_SHA1,
            SCM_MANAGER_POM_POSTIMAGE_SHA256,
            SCM_MANAGER_POM_POSTIMAGE_BYTES,
            SCM_MANAGER_POM_POSTIMAGE_SHA1,
        ),
    )
    for (
        source_path,
        pom_path,
        checksum_path,
        preimage_sha256,
        preimage_bytes,
        preimage_sha1,
        postimage_sha256,
        postimage_bytes,
        postimage_sha1,
    ) in remediations:
        reviewed = root / source_path
        target_pom = repository / pom_path
        target_checksum = repository / checksum_path
        files = (reviewed, target_pom, target_checksum)
        if any(
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_nlink != 1
            for path in files
        ):
            _fail("HARDENED_METADATA", f"unsafe remediation input: {pom_path}")
        if (
            _sha256(reviewed) != postimage_sha256
            or reviewed.stat().st_size != postimage_bytes
            or _sha256(target_pom) != preimage_sha256
            or target_pom.stat().st_size != preimage_bytes
            or target_checksum.read_bytes() != preimage_sha1
        ):
            _fail("HARDENED_METADATA", f"preimage differs: {pom_path}")
        target_pom.write_bytes(reviewed.read_bytes())
        target_checksum.write_bytes(postimage_sha1)
        if (
            _sha256(target_pom) != postimage_sha256
            or target_pom.stat().st_size != postimage_bytes
            or target_checksum.read_bytes() != postimage_sha1
        ):
            _fail("HARDENED_METADATA", f"postimage differs: {pom_path}")
    removed: list[str] = []
    for relative in VULNERABLE_INPUTS:
        target = repository / relative
        if target.is_symlink():
            _fail("VULNERABLE_INPUT", f"unsafe blocked input: {relative}")
        if not target.exists():
            continue
        status = target.stat()
        if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
            _fail("VULNERABLE_INPUT", f"unsafe blocked input: {relative}")
        target.unlink()
        removed.append(relative)
    print(
        json.dumps(
            {
                "hardened_metadata": sorted(EXPECTED_HARDENED_METADATA),
                "removed_vulnerable_inputs": removed,
            },
            sort_keys=True,
        )
    )


def _write_archive(
    repository: Path,
    archive: Path,
    entries: list[dict[str, Any]],
) -> None:
    if archive.exists() or archive.is_symlink():
        _fail("OFFLINE_ARCHIVE", f"output already exists: {archive}")
    with archive.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as tar:
                for entry in entries:
                    source = repository / entry["path"]
                    info = tar.gettarinfo(str(source), arcname=entry["path"])
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = 0
                    info.mode = int(entry["mode"], 8)
                    info.pax_headers = {}
                    with source.open("rb") as payload:
                        tar.addfile(info, payload)


def capture_repository(repository: Path, evidence: Path) -> None:
    repository = repository.resolve()
    evidence = evidence.resolve()
    if not repository.is_dir():
        _fail("OFFLINE_INPUT", f"repository not found: {repository}")
    evidence.mkdir(parents=True, exist_ok=False)
    entries = _repository_entries(repository)
    manifest = {
        "schema_version": EXPECTED_MANIFEST_SCHEMA_VERSION,
        "media_type": EXPECTED_MANIFEST_MEDIA_TYPE,
        "repository_layout": EXPECTED_REPOSITORY_LAYOUT,
        "selected_reactor": EXPECTED_SELECTED_REACTOR,
        "goal": EXPECTED_GOAL,
        "files": entries,
        "file_count": len(entries),
        "total_bytes": sum(entry["bytes"] for entry in entries),
        "unknown_files_permitted": False,
    }
    _write_json(evidence / MANIFEST_NAME, manifest)
    _write_archive(repository, evidence / ARCHIVE_NAME, entries)


def _vulnerable_lines(path: Path) -> list[str]:
    try:
        status = path.lstat()
    except (OSError, UnicodeDecodeError) as error:
        _fail("RESOLUTION_LOG", f"{path}: {error}")
    if (
        not stat.S_ISREG(status.st_mode)
        or status.st_nlink != 1
        or not 0 < status.st_size <= MAX_LOG_BYTES
    ):
        _fail("RESOLUTION_LOG", f"log bounds differ: {path}")
    success = False
    failure = False
    vulnerable: list[str] = []
    try:
        with path.open(
            "r",
            encoding="utf-8",
            errors="strict",
        ) as source:
            for line in source:
                success = success or "BUILD SUCCESS" in line
                failure = failure or "BUILD FAILURE" in line
                if any(pattern.search(line) for pattern in VULNERABLE_COORDINATES):
                    vulnerable.append(line.rstrip("\r\n"))
    except (OSError, UnicodeDecodeError) as error:
        _fail("RESOLUTION_LOG", f"{path}: {error}")
    if not success or failure:
        _fail("RESOLUTION_LOG", f"successful Maven result missing: {path}")
    return vulnerable


def finalize_record(
    evidence: Path,
    online_log: Path,
    offline_log: Path,
    toolchain: Path,
    builder_index: Path,
    maven_version: Path,
    global_settings: Path,
) -> None:
    evidence = evidence.resolve()
    try:
        same_log = os.path.samefile(online_log, offline_log)
    except OSError as error:
        _fail("EVIDENCE_FILE", f"cannot compare Maven logs: {error}")
    if same_log:
        _fail("EVIDENCE_FILE", "online and offline Maven logs must be distinct")
    for source, name in (
        (online_log, ONLINE_LOG_NAME),
        (offline_log, OFFLINE_LOG_NAME),
        (toolchain, TOOLCHAIN_NAME),
        (builder_index, BUILDER_INDEX_NAME),
        (maven_version, MAVEN_VERSION_NAME),
        (global_settings, GLOBAL_SETTINGS_NAME),
    ):
        target = evidence / name
        if target.exists():
            _fail("EVIDENCE_FILE", f"target already exists: {target}")
        if name in {ONLINE_LOG_NAME, OFFLINE_LOG_NAME} and _vulnerable_lines(
            source
        ):
            _fail("VULNERABLE_COORDINATE", f"candidate log differs: {source}")
        target.write_bytes(source.read_bytes())
    online = evidence / ONLINE_LOG_NAME
    offline = evidence / OFFLINE_LOG_NAME
    if _vulnerable_lines(online) or _vulnerable_lines(offline):
        _fail("VULNERABLE_COORDINATE", "candidate resolution retained a blocker")
    manifest_path = evidence / MANIFEST_NAME
    archive_path = evidence / ARCHIVE_NAME
    manifest = _read_json(manifest_path)
    toolchain_record = _read_json(evidence / TOOLCHAIN_NAME)
    required_environment = {
        "GITHUB_REPOSITORY",
        "GITHUB_RUN_ID",
        "GITHUB_RUN_ATTEMPT",
        "GITHUB_SERVER_URL",
        "GITHUB_SHA",
        "REVIEWED_COMMIT",
        "GITHUB_WORKFLOW",
        "GITHUB_WORKFLOW_REF",
        "RUNNER_ARCH",
        "RUNNER_OS",
    }
    missing_environment = sorted(
        name for name in required_environment if not os.environ.get(name)
    )
    if missing_environment:
        _fail("RUN_IDENTITY", f"environment missing: {missing_environment}")
    run_id = int(os.environ["GITHUB_RUN_ID"])
    run_attempt = int(os.environ["GITHUB_RUN_ATTEMPT"])
    repository = os.environ["GITHUB_REPOSITORY"]
    if (
        repository != EXPECTED_GITHUB_REPOSITORY
        or os.environ["GITHUB_SERVER_URL"] != EXPECTED_GITHUB_SERVER_URL
        or not os.environ["GITHUB_WORKFLOW_REF"].startswith(
            EXPECTED_WORKFLOW_REF_PREFIX
        )
    ):
        _fail("RUN_IDENTITY", "workflow repository identity differs")
    record = {
        "schema_version": 1,
        "record_path": (
            "docs/design/evidence/trino/"
            f"run-{run_id}-maven-feasibility-validation.json"
        ),
        "subject": {
            "issue": "https://github.com/TommyKammy/Shirokuma/issues/63",
            "workflow_run": (
                f"{os.environ['GITHUB_SERVER_URL']}/{repository}/actions/runs/{run_id}"
            ),
            "run_id": run_id,
            "run_attempt": run_attempt,
            "reviewed_commit": os.environ["REVIEWED_COMMIT"],
            "workflow_execution_commit": os.environ["GITHUB_SHA"],
            "workflow": os.environ["GITHUB_WORKFLOW"],
            "workflow_ref": os.environ["GITHUB_WORKFLOW_REF"],
        },
        "boundary": {
            "state": "preauthorization_feasibility_only",
            "source_remediation_activated": False,
            "publication_permitted": False,
            "dependency_artifact_produced": False,
            "image_or_runtime_change_permitted": False,
            "candidate_patch_sha256": EXPECTED_CANDIDATE_PATCH_SHA256,
            "candidate_postimage_sha256": EXPECTED_POSTIMAGE_SHA256,
        },
        "execution": {
            "platform": "linux/arm64",
            "runner_os": os.environ["RUNNER_OS"],
            "runner_arch": os.environ["RUNNER_ARCH"],
            "native_execution": True,
            "builder": EXPECTED_BUILDER,
            "builder_arm64_manifest": EXPECTED_BUILDER_ARM64_MANIFEST,
            "selected_reactor": EXPECTED_SELECTED_REACTOR,
            "online": {
                "command": EXPECTED_ONLINE_COMMAND,
                "network": EXPECTED_ONLINE_NETWORK,
                "exit_status": 0,
                "vulnerable_coordinate_lines": 0,
                "log": _identity(online),
            },
            "offline": {
                "command": EXPECTED_OFFLINE_COMMAND,
                "network": "none",
                "repository_mount": "read-only",
                "exit_status": 0,
                "vulnerable_coordinate_lines": 0,
                "log": _identity(offline),
            },
            "toolchain": {
                **toolchain_record,
                "record": _identity(evidence / TOOLCHAIN_NAME),
                "builder_index_document": _identity(
                    evidence / BUILDER_INDEX_NAME
                ),
                "maven_version_output": _identity(
                    evidence / MAVEN_VERSION_NAME
                ),
                "global_settings": _identity(evidence / GLOBAL_SETTINGS_NAME),
            },
        },
        "offline_inputs": {
            "reproducible_inputs_retained": True,
            "manifest": _identity(manifest_path),
            "archive": _identity(archive_path),
            "file_count": manifest["file_count"],
            "total_bytes": manifest["total_bytes"],
            "replacement_inputs": list(EXPECTED_REPLACEMENT_INPUTS),
            "hardened_metadata": sorted(EXPECTED_HARDENED_METADATA),
        },
        "result": {
            "status": "passed",
            "authorization_use_permitted": False,
            "owner_decision_still_required": True,
            "full_clean_install_not_run": True,
            "fresh_closure_sbom_and_scan_not_run": True,
        },
    }
    _write_json(evidence / RECORD_NAME, record)
    audit_evidence(evidence, require_archive=True)


def _verify_identity(
    directory: Path, identity: object, *, expected_name: str
) -> None:
    if not isinstance(identity, dict) or set(identity) != {
        "path",
        "sha256",
        "bytes",
    }:
        _fail("EVIDENCE_IDENTITY", f"malformed identity: {identity!r}")
    if identity["path"] != expected_name:
        _fail(
            "EVIDENCE_IDENTITY",
            f"identity path differs: {identity['path']!r}",
        )
    path = directory / expected_name
    if _identity(path) != identity:
        _fail("EVIDENCE_IDENTITY", f"identity differs: {path}")


def _manifest_files(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        _fail("OFFLINE_INPUT", "manifest files are missing")
    if len(files) > MAX_ARCHIVE_MEMBERS:
        _fail("OFFLINE_INPUT", "manifest file count exceeds limit")
    expected_keys = {"path", "mode", "sha256", "bytes"}
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != expected_keys:
            _fail("OFFLINE_INPUT", f"malformed manifest entry: {entry!r}")
        path = entry["path"]
        normalized = PurePosixPath(path) if isinstance(path, str) else None
        if (
            normalized is None
            or not path
            or normalized.is_absolute()
            or ".." in normalized.parts
            or normalized.as_posix() != path
            or not isinstance(entry["mode"], str)
            or re.fullmatch(r"[0-7]{4}", entry["mode"]) is None
            or not isinstance(entry["sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is None
            or type(entry["bytes"]) is not int
            or entry["bytes"] < 0
        ):
            _fail("OFFLINE_INPUT", f"invalid manifest entry: {entry!r}")
    if files != sorted(files, key=lambda item: item["path"]) or len(
        {item["path"] for item in files}
    ) != len(files):
        _fail("OFFLINE_INPUT", "manifest files are not canonical")
    observed = {item["path"] for item in files}
    vulnerable = sorted(set(VULNERABLE_INPUTS) & observed)
    if vulnerable:
        _fail("VULNERABLE_INPUT", f"blocked inputs retained: {vulnerable}")
    _verify_replacement_metadata(files)
    _verify_hardened_metadata(files)
    return files


def _archive_entries(
    archive: Path,
    expected_files: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    try:
        archive_status = archive.lstat()
    except OSError as error:
        _fail("OFFLINE_ARCHIVE", f"{archive}: {error}")
    if (
        not stat.S_ISREG(archive_status.st_mode)
        or archive_status.st_nlink != 1
        or not 0 < archive_status.st_size <= MAX_ARCHIVE_BYTES
    ):
        _fail("OFFLINE_ARCHIVE", "compressed archive bounds differ")
    expected_total = sum(entry["bytes"] for entry in expected_files)
    if (
        len(expected_files) > MAX_ARCHIVE_MEMBERS
        or any(
            entry["bytes"] > MAX_ARCHIVE_MEMBER_BYTES
            for entry in expected_files
        )
        or expected_total > MAX_ARCHIVE_FILE_BYTES
    ):
        _fail("OFFLINE_ARCHIVE", "manifest archive bounds exceeded")
    expected = {entry["path"]: entry for entry in expected_files}
    observed: list[dict[str, Any]] = []
    seen: set[str] = set()
    expanded_limit = min(
        MAX_ARCHIVE_EXPANDED_BYTES,
        archive_status.st_size * MAX_ARCHIVE_COMPRESSION_RATIO,
    )
    canonical_temporary = tempfile.TemporaryDirectory()
    canonical_root = Path(canonical_temporary.name) / "repository"
    canonical_root.mkdir()

    try:
        with archive.open("rb") as raw, tempfile.TemporaryFile() as expanded:
            if raw.read(len(EXPECTED_GZIP_HEADER)) != EXPECTED_GZIP_HEADER:
                _fail("OFFLINE_ARCHIVE", "gzip header is not canonical")
            raw.seek(0)
            decompressor = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
            expanded_bytes = 0
            for compressed in iter(lambda: raw.read(1024 * 1024), b""):
                if decompressor.eof:
                    _fail("OFFLINE_ARCHIVE", "compressed archive has trailing data")
                payload = decompressor.decompress(
                    compressed,
                    expanded_limit - expanded_bytes + 1,
                )
                expanded_bytes += len(payload)
                if (
                    expanded_bytes > expanded_limit
                    or decompressor.unconsumed_tail
                ):
                    _fail("OFFLINE_ARCHIVE", "expanded archive bounds exceeded")
                if decompressor.unused_data:
                    _fail("OFFLINE_ARCHIVE", "compressed archive has trailing data")
                expanded.write(payload)
            payload = decompressor.flush(expanded_limit - expanded_bytes + 1)
            expanded_bytes += len(payload)
            if expanded_bytes > expanded_limit:
                _fail("OFFLINE_ARCHIVE", "expanded archive bounds exceeded")
            if not decompressor.eof or decompressor.unused_data:
                _fail("OFFLINE_ARCHIVE", "compressed archive is not canonical")
            expanded.write(payload)
            expanded.seek(0)
            with tarfile.open(fileobj=expanded, mode="r:") as retained:
                for member in retained:
                    expected_pax_headers = (
                        {"path": member.name}
                        if len(member.name.encode("utf-8")) > tarfile.LENGTH_NAME
                        else {}
                    )
                    if (
                        len(observed) >= MAX_ARCHIVE_MEMBERS
                        or member.size > MAX_ARCHIVE_MEMBER_BYTES
                        or not member.isfile()
                        or member.name in seen
                        or member.name not in expected
                        or member.uid != 0
                        or member.gid != 0
                        or member.uname != ""
                        or member.gname != ""
                        or member.mtime != 0
                        or member.type != tarfile.REGTYPE
                        or member.linkname != ""
                        or member.devmajor != 0
                        or member.devminor != 0
                        or member.pax_headers != expected_pax_headers
                    ):
                        _fail(
                            "OFFLINE_ARCHIVE",
                            f"unexpected member: {member.name}",
                        )
                    reference = expected[member.name]
                    mode = f"{stat.S_IMODE(member.mode):04o}"
                    if (
                        member.size != reference["bytes"]
                        or mode != reference["mode"]
                    ):
                        _fail(
                            "OFFLINE_ARCHIVE",
                            f"member metadata differs: {member.name}",
                        )
                    payload = retained.extractfile(member)
                    if payload is None:
                        _fail(
                            "OFFLINE_ARCHIVE",
                            f"member unreadable: {member.name}",
                        )
                    digest = hashlib.sha256()
                    total = 0
                    canonical_path = canonical_root / member.name
                    canonical_path.parent.mkdir(parents=True, exist_ok=True)
                    with canonical_path.open("xb") as canonical_payload:
                        for chunk in iter(
                            lambda: payload.read(1024 * 1024), b""
                        ):
                            digest.update(chunk)
                            canonical_payload.write(chunk)
                            total += len(chunk)
                            if total > MAX_ARCHIVE_MEMBER_BYTES:
                                _fail(
                                    "OFFLINE_ARCHIVE",
                                    f"member bounds exceeded: {member.name}",
                                )
                    entry = {
                        "path": member.name,
                        "mode": mode,
                        "sha256": digest.hexdigest(),
                        "bytes": total,
                    }
                    if entry != reference:
                        _fail(
                            "OFFLINE_ARCHIVE",
                            f"member differs: {member.name}",
                        )
                    seen.add(member.name)
                    observed.append(entry)
                logical_end = retained.offset
            canonical_size = (
                (
                    logical_end
                    + 2 * tarfile.BLOCKSIZE
                    + tarfile.RECORDSIZE
                    - 1
                )
                // tarfile.RECORDSIZE
            ) * tarfile.RECORDSIZE
            expanded.seek(logical_end)
            trailer = expanded.read()
            if expanded_bytes != canonical_size or any(trailer):
                _fail("OFFLINE_ARCHIVE", "tar archive has trailing data")
        canonical_archive = Path(canonical_temporary.name) / ARCHIVE_NAME
        _write_archive(canonical_root, canonical_archive, expected_files)
        if (
            canonical_archive.stat().st_size != archive_status.st_size
            or _sha256(canonical_archive) != _sha256(archive)
        ):
            _fail("OFFLINE_ARCHIVE", "archive bytes are not canonical")
    except (OSError, EOFError, tarfile.TarError, zlib.error) as error:
        _fail("OFFLINE_ARCHIVE", f"{archive}: {error}")
    finally:
        canonical_temporary.cleanup()
    observed.sort(key=lambda entry: entry["path"])
    if observed != expected_files:
        missing = sorted(set(expected) - seen)
        _fail("OFFLINE_ARCHIVE", f"archive manifest differs; missing={missing}")
    return observed


def _verify_toolchain_record(evidence: Path, execution: dict[str, Any]) -> None:
    toolchain = execution.get("toolchain")
    if not isinstance(toolchain, dict):
        _fail("TOOLCHAIN_RECORD", "toolchain result is missing")
    attachment_names = {
        "record": TOOLCHAIN_NAME,
        "builder_index_document": BUILDER_INDEX_NAME,
        "maven_version_output": MAVEN_VERSION_NAME,
        "global_settings": GLOBAL_SETTINGS_NAME,
    }
    for key, expected_name in attachment_names.items():
        _verify_identity(
            evidence,
            toolchain.get(key),
            expected_name=expected_name,
        )
    identity = toolchain["record"]
    retained = _read_json(evidence / identity["path"])
    embedded = {
        key: value
        for key, value in toolchain.items()
        if key not in attachment_names
    }
    if retained != embedded:
        _fail("TOOLCHAIN_RECORD", "retained and embedded records differ")
    expected_keys = {
        "schema_version",
        "result",
        "runner_os",
        "runner_arch",
        "container_architecture",
        "native_execution",
        "qemu_binfmt_handlers",
        "builder_index",
        "builder_arm64_manifest",
        "builder_index_document_sha256",
        "maven_version_output_sha256",
        "global_settings_sha256",
    }
    hashes = (
        retained.get("builder_index_document_sha256"),
        retained.get("maven_version_output_sha256"),
        retained.get("global_settings_sha256"),
    )
    if (
        set(retained) != expected_keys
        or retained.get("schema_version") != 1
        or retained.get("result") != "passed"
        or retained.get("runner_os") != execution.get("runner_os")
        or retained.get("runner_arch") != execution.get("runner_arch")
        or retained.get("container_architecture") != "aarch64"
        or retained.get("native_execution") is not True
        or retained.get("qemu_binfmt_handlers") != []
        or retained.get("builder_index") != EXPECTED_BUILDER
        or retained.get("builder_index_document_sha256")
        != EXPECTED_BUILDER_INDEX_SHA256
        or retained.get("builder_arm64_manifest")
        != EXPECTED_BUILDER_ARM64_MANIFEST
        or any(
            not isinstance(value, str)
            or re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in hashes
        )
    ):
        _fail("TOOLCHAIN_RECORD", "toolchain identity differs")
    attachments = (
        ("builder_index_document", "builder_index_document_sha256"),
        ("maven_version_output", "maven_version_output_sha256"),
        ("global_settings", "global_settings_sha256"),
    )
    if any(
        toolchain[identity_name]["sha256"] != retained[hash_name]
        for identity_name, hash_name in attachments
    ):
        _fail("TOOLCHAIN_RECORD", "toolchain attachment hash differs")
    index = _read_json(evidence / toolchain["builder_index_document"]["path"])
    manifests = index.get("manifests")
    arm64 = (
        [
            descriptor
            for descriptor in manifests
            if isinstance(descriptor, dict)
            and descriptor.get("platform", {}).get("os") == "linux"
            and descriptor.get("platform", {}).get("architecture") == "arm64"
        ]
        if isinstance(manifests, list)
        else []
    )
    if len(arm64) != 1 or arm64[0].get(
        "digest"
    ) != EXPECTED_BUILDER_ARM64_MANIFEST:
        _fail("TOOLCHAIN_RECORD", "builder arm64 descriptor differs")
    version_path = evidence / toolchain["maven_version_output"]["path"]
    try:
        version = _read_bounded_file(
            version_path,
            code="TOOLCHAIN_RECORD",
            max_bytes=MAX_MAVEN_VERSION_BYTES,
        ).decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        _fail("TOOLCHAIN_RECORD", f"{version_path}: {error}")
    version_markers = (
        "Apache Maven 3.9.16",
        "Java version: 25",
        "vendor: Eclipse Adoptium",
        'arch: "aarch64"',
    )
    if any(marker not in version for marker in version_markers):
        _fail("TOOLCHAIN_RECORD", "Maven version output differs")
    publisher.audit_builder_settings(
        evidence / toolchain["global_settings"]["path"]
    )


def audit_evidence(evidence: Path, *, require_archive: bool) -> None:
    evidence = evidence.resolve()
    expected_files = set(EXPECTED_EVIDENCE_FILES)
    if not require_archive:
        expected_files.remove(ARCHIVE_NAME)
    try:
        retained = list(evidence.iterdir())
    except OSError as error:
        _fail("EVIDENCE_DIRECTORY", f"{evidence}: {error}")
    if {path.name for path in retained} != expected_files:
        _fail("EVIDENCE_DIRECTORY", "retained evidence file set differs")
    for path in retained:
        try:
            status = path.lstat()
        except OSError as error:
            _fail("EVIDENCE_DIRECTORY", f"{path}: {error}")
        if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
            _fail("EVIDENCE_DIRECTORY", f"unsafe retained file: {path}")
    record = _read_json(evidence / RECORD_NAME)
    if (
        set(record)
        != {
            "schema_version",
            "record_path",
            "subject",
            "boundary",
            "execution",
            "offline_inputs",
            "result",
        }
        or record.get("schema_version") != 1
        or record.get("result", {}).get("status") != "passed"
    ):
        _fail("EVIDENCE_RECORD", "unexpected record envelope")
    subject = record.get("subject", {})
    if not isinstance(subject, dict):
        _fail("EVIDENCE_SUBJECT", "workflow subject is not an object")
    run_id = subject.get("run_id")
    run_attempt = subject.get("run_attempt")
    sha_fields = (
        subject.get("reviewed_commit"),
        subject.get("workflow_execution_commit"),
    )
    if (
        set(subject)
        != {
            "issue",
            "workflow_run",
            "run_id",
            "run_attempt",
            "reviewed_commit",
            "workflow_execution_commit",
            "workflow",
            "workflow_ref",
        }
        or subject.get("issue")
        != "https://github.com/TommyKammy/Shirokuma/issues/63"
        or type(run_id) is not int
        or run_id < 1
        or type(run_attempt) is not int
        or run_attempt < 1
        or any(
            not isinstance(value, str)
            or len(value) != 40
            or any(character not in "0123456789abcdef" for character in value)
            for value in sha_fields
        )
        or subject.get("workflow_run")
        != (
            f"{EXPECTED_GITHUB_SERVER_URL}/{EXPECTED_GITHUB_REPOSITORY}"
            f"/actions/runs/{run_id}"
        )
        or subject.get("workflow")
        != "Trino 483 Maven remediation feasibility"
        or not subject.get("workflow_ref", "").startswith(
            EXPECTED_WORKFLOW_REF_PREFIX
        )
    ):
        _fail("EVIDENCE_SUBJECT", "workflow subject identity differs")
    if record.get("record_path") != (
        "docs/design/evidence/trino/"
        f"run-{run_id}-maven-feasibility-validation.json"
    ):
        _fail("EVIDENCE_RECORD", "validation record path differs")
    boundary = record.get("boundary")
    if boundary != {
        "state": "preauthorization_feasibility_only",
        "source_remediation_activated": False,
        "publication_permitted": False,
        "dependency_artifact_produced": False,
        "image_or_runtime_change_permitted": False,
        "candidate_patch_sha256": EXPECTED_CANDIDATE_PATCH_SHA256,
        "candidate_postimage_sha256": EXPECTED_POSTIMAGE_SHA256,
    }:
        _fail("EVIDENCE_BOUNDARY", "feasibility boundary differs")
    execution = record.get("execution", {})
    if not isinstance(execution, dict):
        _fail("EVIDENCE_EXECUTION", "execution result is not an object")
    if (
        set(execution)
        != {
            "platform",
            "runner_os",
            "runner_arch",
            "native_execution",
            "builder",
            "builder_arm64_manifest",
            "selected_reactor",
            "online",
            "offline",
            "toolchain",
        }
        or execution.get("platform") != "linux/arm64"
        or execution.get("runner_os") != "Linux"
        or execution.get("runner_arch") != "ARM64"
        or execution.get("native_execution") is not True
        or execution.get("builder") != EXPECTED_BUILDER
        or execution.get("builder_arm64_manifest")
        != EXPECTED_BUILDER_ARM64_MANIFEST
        or execution.get("selected_reactor") != EXPECTED_SELECTED_REACTOR
    ):
        _fail("EVIDENCE_EXECUTION", "execution identity differs")
    _verify_toolchain_record(evidence, execution)
    phase_logs: dict[str, dict[str, Any]] = {}
    for name, command, network, expected_log_name in (
        (
            "online",
            EXPECTED_ONLINE_COMMAND,
            EXPECTED_ONLINE_NETWORK,
            ONLINE_LOG_NAME,
        ),
        ("offline", EXPECTED_OFFLINE_COMMAND, "none", OFFLINE_LOG_NAME),
    ):
        phase = execution.get(name, {})
        if not isinstance(phase, dict):
            _fail("EVIDENCE_EXECUTION", f"{name} result is not an object")
        log_identity = phase.get("log")
        expected_phase_keys = {
            "command",
            "network",
            "exit_status",
            "vulnerable_coordinate_lines",
            "log",
        }
        if name == "offline":
            expected_phase_keys.add("repository_mount")
        if (
            set(phase) != expected_phase_keys
            or phase.get("command") != command
            or phase.get("network") != network
            or phase.get("exit_status") != 0
            or phase.get("vulnerable_coordinate_lines") != 0
            or (
                name == "offline"
                and phase.get("repository_mount") != "read-only"
            )
            or not isinstance(log_identity, dict)
            or log_identity.get("path") != expected_log_name
        ):
            _fail("EVIDENCE_EXECUTION", f"{name} result differs")
        _verify_identity(
            evidence,
            log_identity,
            expected_name=expected_log_name,
        )
        phase_logs[name] = log_identity
        if _vulnerable_lines(evidence / log_identity["path"]):
            _fail("VULNERABLE_COORDINATE", f"{name} log differs")
    if phase_logs["online"] == phase_logs["offline"]:
        _fail("EVIDENCE_EXECUTION", "online and offline log identities coincide")
    offline_inputs = record.get("offline_inputs", {})
    if not isinstance(offline_inputs, dict):
        _fail("OFFLINE_INPUT", "offline inputs are not an object")
    if (
        set(offline_inputs)
        != {
            "reproducible_inputs_retained",
            "manifest",
            "archive",
            "file_count",
            "total_bytes",
            "replacement_inputs",
            "hardened_metadata",
        }
        or offline_inputs.get("reproducible_inputs_retained") is not True
        or offline_inputs.get("hardened_metadata")
        != sorted(EXPECTED_HARDENED_METADATA)
    ):
        _fail("OFFLINE_INPUT", "reproducible inputs are not retained")
    _verify_identity(
        evidence,
        offline_inputs.get("manifest"),
        expected_name=MANIFEST_NAME,
    )
    if require_archive:
        _verify_identity(
            evidence,
            offline_inputs.get("archive"),
            expected_name=ARCHIVE_NAME,
        )
    manifest = _read_json(evidence / offline_inputs["manifest"]["path"])
    if (
        set(manifest)
        != {
            "schema_version",
            "media_type",
            "repository_layout",
            "selected_reactor",
            "goal",
            "file_count",
            "total_bytes",
            "unknown_files_permitted",
            "files",
        }
        or manifest.get("schema_version") != EXPECTED_MANIFEST_SCHEMA_VERSION
        or manifest.get("media_type") != EXPECTED_MANIFEST_MEDIA_TYPE
        or manifest.get("repository_layout") != EXPECTED_REPOSITORY_LAYOUT
        or manifest.get("file_count") != offline_inputs.get("file_count")
        or manifest.get("total_bytes") != offline_inputs.get("total_bytes")
        or manifest.get("selected_reactor") != EXPECTED_SELECTED_REACTOR
        or manifest.get("goal") != EXPECTED_GOAL
        or manifest.get("unknown_files_permitted") is not False
    ):
        _fail("OFFLINE_INPUT", "manifest summary differs")
    files = _manifest_files(manifest)
    if manifest["file_count"] != len(files) or manifest["total_bytes"] != sum(
        entry["bytes"] for entry in files
    ):
        _fail("OFFLINE_INPUT", "manifest aggregate differs")
    if require_archive:
        archive = evidence / offline_inputs["archive"]["path"]
        _archive_entries(archive, files)
    observed = {item["path"] for item in files}
    if not set(EXPECTED_REPLACEMENT_INPUTS).issubset(observed):
        _fail("OFFLINE_INPUT", "replacement inputs differ")
    result = record.get("result")
    if result != {
        "status": "passed",
        "authorization_use_permitted": False,
        "owner_decision_still_required": True,
        "full_clean_install_not_run": True,
        "fresh_closure_sbom_and_scan_not_run": True,
    }:
        _fail("EVIDENCE_RESULT", "result boundary differs")


def _workflow_step_run(workflow: str, step_name: str) -> str:
    marker = f"      - name: {step_name}\n"
    if workflow.count(marker) != 1:
        _fail("WORKFLOW", f"step identity differs: {step_name}")
    step_start = workflow.index(marker)
    next_step = workflow.find("\n      - name: ", step_start + len(marker))
    step = workflow[step_start:] if next_step < 0 else workflow[step_start:next_step]
    run_marker = "        run: |\n"
    if step.count(run_marker) != 1:
        _fail("WORKFLOW", f"step run block differs: {step_name}")
    body = step.split(run_marker, 1)[1]
    lines = body.splitlines()
    if any(line and not line.startswith("          ") for line in lines):
        _fail("WORKFLOW", f"step indentation differs: {step_name}")
    return "\n".join(line[10:] if line else "" for line in lines)


def _workflow_docker_run(workflow: str, step_name: str) -> list[str]:
    body = _workflow_step_run(workflow, step_name)
    logical = re.sub(r"\\\n[ \t]*", " ", body)
    invocations = [
        line.strip()
        for line in logical.splitlines()
        if line.lstrip().startswith("docker run ")
    ]
    if len(invocations) != 1:
        _fail("WORKFLOW", f"Docker invocation differs: {step_name}")
    try:
        return shlex.split(invocations[0], posix=True)
    except ValueError as error:
        _fail("WORKFLOW", f"Docker invocation cannot be parsed: {error}")


def _option_values(tokens: list[str], option: str) -> list[str]:
    values: list[str] = []
    for index, token in enumerate(tokens):
        if token == option:
            if index + 1 >= len(tokens):
                _fail("WORKFLOW", f"Docker option has no value: {option}")
            values.append(tokens[index + 1])
        elif token.startswith(f"{option}="):
            values.append(token.split("=", 1)[1])
    return values


def _audit_workflow_jobs(workflow: str) -> None:
    jobs_match = re.search(r"(?m)^jobs:\s*$", workflow)
    if jobs_match is None or re.search(r"(?m)^permissions:\s*", workflow):
        _fail("WORKFLOW", "top-level jobs or permissions differ")
    jobs_body = workflow[jobs_match.end() :]
    next_top_level = re.search(r"(?m)^\S[^\n]*:\s*$", jobs_body)
    if next_top_level is not None:
        jobs_body = jobs_body[: next_top_level.start()]
    job_matches = list(
        re.finditer(r"(?m)^  ([A-Za-z0-9_-]+):\s*$", jobs_body)
    )
    if [match.group(1) for match in job_matches] != ["validate"]:
        _fail("WORKFLOW", "jobs map differs")
    validate = jobs_body[job_matches[0].end() :]
    permissions = re.search(
        r"(?m)^    permissions:\s*\n((?:      [^\n]*\n?)*)",
        validate,
    )
    if permissions is None or permissions.group(1).splitlines() != [
        "      contents: read"
    ]:
        _fail("WORKFLOW", "validate permissions differ")
    step_heads = re.findall(r"(?m)^      - ([^\n]+)$", validate)
    expected_heads = [f"name: {name}" for name in EXPECTED_WORKFLOW_STEPS]
    if step_heads != expected_heads:
        _fail("WORKFLOW", f"validation steps differ: {step_heads}")


def _audit_workflow_triggers(workflow: str) -> None:
    markers = list(re.finditer(r"(?m)^on:\s*$", workflow))
    start = markers[0].start() if len(markers) == 1 else -1
    end = workflow.find("\nconcurrency:\n", start + 1)
    if (
        start < 0
        or end < 0
        or workflow[start:end] != EXPECTED_WORKFLOW_TRIGGER_BLOCK
    ):
        _fail("WORKFLOW", "workflow triggers differ")


def _audit_candidate_application(workflow: str, step_name: str) -> None:
    body = _workflow_step_run(workflow, step_name)
    logical = re.sub(r"\\\n[ \t]*", " ", body)
    prefix = "python3 scripts/verify_trino_maven_feasibility.py apply-candidate"
    invocations = [
        line.strip()
        for line in logical.splitlines()
        if line.strip().startswith(prefix)
    ]
    expected = [
        "python3",
        "scripts/verify_trino_maven_feasibility.py",
        "apply-candidate",
        "--root",
        ".",
        "--checkout",
        "${source_dir}",
    ]
    try:
        parsed = [
            shlex.split(invocation, posix=True) for invocation in invocations
        ]
    except ValueError as error:
        _fail("WORKFLOW", f"candidate application cannot be parsed: {error}")
    if parsed != [expected]:
        _fail("WORKFLOW", f"candidate application differs: {step_name}")


def _audit_candidate_postimage_before_docker(
    workflow: str, step_name: str
) -> None:
    body = _workflow_step_run(workflow, step_name)
    logical = re.sub(r"\\\n[ \t]*", " ", body)
    lines = [line.strip() for line in logical.splitlines() if line.strip()]
    prefix = "python3 scripts/verify_trino_maven_feasibility.py verify-candidate"
    checks = [index for index, line in enumerate(lines) if line.startswith(prefix)]
    docker = [
        index for index, line in enumerate(lines) if line.startswith("docker run ")
    ]
    expected = [
        "python3",
        "scripts/verify_trino_maven_feasibility.py",
        "verify-candidate",
        "--authorization-root",
        ".",
        "--checkout",
        "${source_dir}",
    ]
    try:
        parsed = [shlex.split(lines[index], posix=True) for index in checks]
    except ValueError as error:
        _fail("WORKFLOW", f"candidate postimage check cannot be parsed: {error}")
    if (
        parsed != [expected]
        or len(docker) != 1
        or checks[0] + 1 != docker[0]
    ):
        _fail("WORKFLOW", f"candidate postimage check differs: {step_name}")


def audit_workflow(root: Path) -> None:
    workflow_path = root.resolve() / WORKFLOW_PATH
    workflow = workflow_path.read_text(encoding="utf-8")
    required = (
        "runs-on: ubuntu-24.04-arm",
        "permissions:\n      contents: read",
        f"BUILDER_IMAGE: {EXPECTED_BUILDER}",
        "-Daether.syncContext.named.basedir.locksDir=/tmp/maven-locks",
        EXPECTED_SELECTED_REACTOR,
        "dependency:resolve-plugins -DskipTests",
        "retention-days: 30",
        "include-hidden-files: false",
        "docker buildx imagetools inspect --raw",
        "verify_trino_maven_feasibility.py capture-repository",
        "verify_trino_maven_feasibility.py prune-vulnerable-inputs",
        "verify_trino_maven_feasibility.py finalize-record",
        "verify_trino_maven_feasibility.py audit-evidence",
        "verify_trino_maven_feasibility.py verify-candidate",
        "--allow-expired-feasibility-refresh",
        "bootstrap/trino/v483/maven-policy/.mvn/jvm.config",
        "docs/design/evidence/trino/"
        "run-30693677356-maven-vulnerability-classification.json",
        "docs/design/evidence/trino/"
        "run-30731801825-maven-feasibility-validation.json",
        "docs/design/evidence/trino/"
        "run-30731801825-maven-feasibility-artifact-receipt.json",
    )
    missing = [marker for marker in required if marker not in workflow]
    forbidden = (
        "packages: write",
        "id-token: write",
        "contents: write",
        "secrets.",
        "docker push",
        "oras push",
        "cosign sign",
        "ghcr.io/",
    )
    present = [marker for marker in forbidden if marker in workflow]
    if missing or present:
        _fail("WORKFLOW", f"missing={missing}, forbidden={present}")
    _audit_workflow_triggers(workflow)
    _audit_workflow_jobs(workflow)
    for step_name in (
        "Fetch and prepare the exact candidate source",
        "Replay the selected plugin closure with no network",
    ):
        _audit_candidate_application(workflow, step_name)
    online = _workflow_docker_run(
        workflow,
        "Resolve the selected plugin closure online",
    )
    offline = _workflow_docker_run(
        workflow,
        "Replay the selected plugin closure with no network",
    )
    for step_name in (
        "Resolve the selected plugin closure online",
        "Replay the selected plugin closure with no network",
    ):
        _audit_candidate_postimage_before_docker(workflow, step_name)
    if _option_values(online, "--network"):
        _fail("WORKFLOW", "online Docker network must remain unrestricted")
    expected_policy_volumes = [
        (
            "${GITHUB_WORKSPACE}/bootstrap/trino/v483/maven-policy:"
            "/policy/.mvn:ro"
        ),
        (
            "${GITHUB_WORKSPACE}/bootstrap/trino/v483/settings.xml:"
            "/policy/settings.xml:ro"
        ),
    ]
    if (
        _option_values(online, "--volume")
        != [
            "${source_dir}:/workspace:ro",
            "${repository}:/m2",
            *expected_policy_volumes,
        ]
        or _option_values(offline, "--network") != ["none"]
        or _option_values(offline, "--volume")
        != [
            "${source_dir}:/workspace:ro",
            "${repository}:/m2:ro",
            *expected_policy_volumes,
        ]
        or offline.count("${RUNNER_TEMP}/offline-resolve-plugins.log") != 1
    ):
        _fail("WORKFLOW", "Maven Docker execution controls differ")
    if workflow.count("jobs:") != 1 or workflow.count("  validate:") != 1:
        _fail("WORKFLOW", "workflow must contain one validation job")
    if _sha256(workflow_path) != EXPECTED_WORKFLOW_SHA256:
        _fail("WORKFLOW", "workflow identity differs")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    apply = commands.add_parser("apply-candidate")
    apply.add_argument("--root", type=Path, default=Path("."))
    apply.add_argument("--checkout", type=Path, required=True)
    verify = commands.add_parser("verify-candidate")
    verify.add_argument("--authorization-root", type=Path)
    verify.add_argument("--checkout", type=Path, required=True)
    capture = commands.add_parser("capture-repository")
    capture.add_argument("--repository", type=Path, required=True)
    capture.add_argument("--evidence", type=Path, required=True)
    prune = commands.add_parser("prune-vulnerable-inputs")
    prune.add_argument("--repository", type=Path, required=True)
    prune.add_argument("--root", type=Path, default=Path("."))
    finalize = commands.add_parser("finalize-record")
    finalize.add_argument("--evidence", type=Path, required=True)
    finalize.add_argument("--online-log", type=Path, required=True)
    finalize.add_argument("--offline-log", type=Path, required=True)
    finalize.add_argument("--toolchain", type=Path, required=True)
    finalize.add_argument("--builder-index", type=Path, required=True)
    finalize.add_argument("--maven-version", type=Path, required=True)
    finalize.add_argument("--global-settings", type=Path, required=True)
    audit = commands.add_parser("audit-evidence")
    audit.add_argument("--evidence", type=Path, required=True)
    audit.add_argument("--require-archive", action="store_true")
    workflow = commands.add_parser("audit-workflow")
    workflow.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    try:
        if args.command == "apply-candidate":
            apply_candidate(args.root, args.checkout)
        elif args.command == "verify-candidate":
            verify_candidate(
                args.checkout,
                authorization_root=args.authorization_root,
            )
        elif args.command == "prune-vulnerable-inputs":
            prune_vulnerable_inputs(args.repository, args.root)
        elif args.command == "capture-repository":
            capture_repository(args.repository, args.evidence)
        elif args.command == "finalize-record":
            finalize_record(
                args.evidence,
                args.online_log,
                args.offline_log,
                args.toolchain,
                args.builder_index,
                args.maven_version,
                args.global_settings,
            )
        elif args.command == "audit-evidence":
            audit_evidence(args.evidence, require_archive=args.require_archive)
        elif args.command == "audit-workflow":
            audit_workflow(args.root)
        else:  # pragma: no cover
            raise AssertionError(args.command)
    except (EvidenceError, publisher.ContractError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
