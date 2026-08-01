#!/usr/bin/env python3
"""Create and verify the bounded Trino 483 Maven feasibility evidence."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
from pathlib import Path
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
    "7bb92a92ee492fbf1fc238c5e1ec6a90c4b3088f98f3d05652853f7b874221d8"
)
EXPECTED_CANDIDATE_PATCH_BYTES = 6633
EXPECTED_BASELINE_SHA256 = (
    "8d342215a3c748f7965f0a82e847cab13587b94171d9d1422922b665475109c1"
)
EXPECTED_POSTIMAGE_SHA256 = (
    "8d66505ee8ad90d11bf887dfe25a355d815f904d9ea90184b2089b0b68869626"
)
EXPECTED_BUILDER = (
    "docker.io/library/maven@sha256:"
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
EXPECTED_REPLACEMENT_INPUTS = (
    "org/apache/velocity/velocity-engine-core/2.4.1/"
    "velocity-engine-core-2.4.1.jar",
    "org/codehaus/plexus/plexus-utils/4.0.3/plexus-utils-4.0.3.jar",
)
VULNERABLE_COORDINATES = (
    re.compile(r"commons-io:commons-io:(?:jar:)?2\.8\.0(?:[:\s]|$)"),
    re.compile(
        r"org\.codehaus\.plexus:plexus-utils:(?:jar:)?4\.0\.[12](?:[:\s]|$)"
    ),
)
RECORD_NAME = "validation-record.json"
MANIFEST_NAME = "offline-input-manifest.json"
ARCHIVE_NAME = "offline-maven-repository.tar.gz"
ONLINE_LOG_NAME = "online-resolve-plugins.log"
OFFLINE_LOG_NAME = "offline-resolve-plugins.log"
TOOLCHAIN_NAME = "toolchain.json"


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


def _read_json(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
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
    if _sha256(pom) != EXPECTED_POSTIMAGE_SHA256:
        _fail("CANDIDATE_POSTIMAGE", "candidate pom.xml hash differs")
    if subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=checkout,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines():
        _fail("CANDIDATE_APPLY", "candidate created untracked source files")


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
    missing = sorted(set(EXPECTED_REPLACEMENT_INPUTS) - observed)
    if missing:
        _fail("OFFLINE_INPUT", f"replacement inputs missing: {missing}")
    return entries


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
        "schema_version": 1,
        "media_type": "application/vnd.shirokuma.maven-feasibility-inputs.v1+json",
        "repository_layout": "maven2",
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
        text = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError) as error:
        _fail("RESOLUTION_LOG", f"{path}: {error}")
    if "BUILD SUCCESS" not in text or "BUILD FAILURE" in text:
        _fail("RESOLUTION_LOG", f"successful Maven result missing: {path}")
    return [
        line
        for line in text.splitlines()
        if any(pattern.search(line) for pattern in VULNERABLE_COORDINATES)
    ]


def finalize_record(
    evidence: Path,
    online_log: Path,
    offline_log: Path,
    toolchain: Path,
) -> None:
    evidence = evidence.resolve()
    for source, name in (
        (online_log, ONLINE_LOG_NAME),
        (offline_log, OFFLINE_LOG_NAME),
        (toolchain, TOOLCHAIN_NAME),
    ):
        target = evidence / name
        if target.exists():
            _fail("EVIDENCE_FILE", f"target already exists: {target}")
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
            "reviewed_commit": os.environ["GITHUB_SHA"],
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
                "network": "allowlisted Maven endpoints only",
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
            },
        },
        "offline_inputs": {
            "reproducible_inputs_retained": True,
            "manifest": _identity(manifest_path),
            "archive": _identity(archive_path),
            "file_count": manifest["file_count"],
            "total_bytes": manifest["total_bytes"],
            "replacement_inputs": list(EXPECTED_REPLACEMENT_INPUTS),
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


def _verify_identity(directory: Path, identity: object) -> None:
    if not isinstance(identity, dict) or set(identity) != {
        "path",
        "sha256",
        "bytes",
    }:
        _fail("EVIDENCE_IDENTITY", f"malformed identity: {identity!r}")
    path = directory / identity["path"]
    if _identity(path) != identity:
        _fail("EVIDENCE_IDENTITY", f"identity differs: {path}")


def audit_evidence(evidence: Path, *, require_archive: bool) -> None:
    evidence = evidence.resolve()
    record = _read_json(evidence / RECORD_NAME)
    if record.get("schema_version") != 1 or record.get("result", {}).get(
        "status"
    ) != "passed":
        _fail("EVIDENCE_RECORD", "unexpected record envelope")
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
    if (
        execution.get("platform") != "linux/arm64"
        or execution.get("native_execution") is not True
        or execution.get("builder") != EXPECTED_BUILDER
        or execution.get("builder_arm64_manifest")
        != EXPECTED_BUILDER_ARM64_MANIFEST
        or execution.get("selected_reactor") != EXPECTED_SELECTED_REACTOR
    ):
        _fail("EVIDENCE_EXECUTION", "execution identity differs")
    for name, command, network in (
        ("online", EXPECTED_ONLINE_COMMAND, "allowlisted Maven endpoints only"),
        ("offline", EXPECTED_OFFLINE_COMMAND, "none"),
    ):
        phase = execution.get(name, {})
        if (
            phase.get("command") != command
            or phase.get("network") != network
            or phase.get("exit_status") != 0
            or phase.get("vulnerable_coordinate_lines") != 0
        ):
            _fail("EVIDENCE_EXECUTION", f"{name} result differs")
        _verify_identity(evidence, phase.get("log"))
        if _vulnerable_lines(evidence / phase["log"]["path"]):
            _fail("VULNERABLE_COORDINATE", f"{name} log differs")
    offline_inputs = record.get("offline_inputs", {})
    if offline_inputs.get("reproducible_inputs_retained") is not True:
        _fail("OFFLINE_INPUT", "reproducible inputs are not retained")
    _verify_identity(evidence, offline_inputs.get("manifest"))
    if require_archive:
        _verify_identity(evidence, offline_inputs.get("archive"))
    manifest = _read_json(evidence / offline_inputs["manifest"]["path"])
    if (
        manifest.get("file_count") != offline_inputs.get("file_count")
        or manifest.get("total_bytes") != offline_inputs.get("total_bytes")
        or manifest.get("selected_reactor") != EXPECTED_SELECTED_REACTOR
        or manifest.get("goal") != EXPECTED_GOAL
        or manifest.get("unknown_files_permitted") is not False
    ):
        _fail("OFFLINE_INPUT", "manifest summary differs")
    files = manifest.get("files")
    if not isinstance(files, list) or files != sorted(
        files, key=lambda item: item["path"]
    ):
        _fail("OFFLINE_INPUT", "manifest files are not canonical")
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


def audit_workflow(root: Path) -> None:
    workflow_path = root.resolve() / WORKFLOW_PATH
    workflow = workflow_path.read_text(encoding="utf-8")
    required = (
        "runs-on: ubuntu-24.04-arm",
        "permissions:\n      contents: read",
        f"BUILDER_IMAGE: {EXPECTED_BUILDER}",
        "--network none",
        '"${repository}:/m2:ro"',
        "-Daether.syncContext.named.basedir.locksDir=/tmp/maven-locks",
        EXPECTED_SELECTED_REACTOR,
        "dependency:resolve-plugins -DskipTests",
        "retention-days: 30",
        "include-hidden-files: false",
        "verify_trino_maven_feasibility.py capture-repository",
        "verify_trino_maven_feasibility.py finalize-record",
        "verify_trino_maven_feasibility.py audit-evidence",
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
    if workflow.count("jobs:") != 1 or workflow.count("  validate:") != 1:
        _fail("WORKFLOW", "workflow must contain one validation job")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    apply = commands.add_parser("apply-candidate")
    apply.add_argument("--root", type=Path, default=Path("."))
    apply.add_argument("--checkout", type=Path, required=True)
    capture = commands.add_parser("capture-repository")
    capture.add_argument("--repository", type=Path, required=True)
    capture.add_argument("--evidence", type=Path, required=True)
    finalize = commands.add_parser("finalize-record")
    finalize.add_argument("--evidence", type=Path, required=True)
    finalize.add_argument("--online-log", type=Path, required=True)
    finalize.add_argument("--offline-log", type=Path, required=True)
    finalize.add_argument("--toolchain", type=Path, required=True)
    audit = commands.add_parser("audit-evidence")
    audit.add_argument("--evidence", type=Path, required=True)
    audit.add_argument("--require-archive", action="store_true")
    workflow = commands.add_parser("audit-workflow")
    workflow.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    try:
        if args.command == "apply-candidate":
            apply_candidate(args.root, args.checkout)
        elif args.command == "capture-repository":
            capture_repository(args.repository, args.evidence)
        elif args.command == "finalize-record":
            finalize_record(
                args.evidence,
                args.online_log,
                args.offline_log,
                args.toolchain,
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
