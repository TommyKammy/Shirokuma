#!/usr/bin/env python3
"""Verify the review-only Trino 483 Maven security remediation candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

import package_trino_maven_dependencies as packager
import verify_trino_dependency_publisher as publisher


WORKFLOW_PATH = Path(
    ".github/workflows/trino-maven-security-remediation-feasibility.yml"
)
RUNNER_PATH = Path("scripts/run_trino_maven_security_feasibility.sh")
TRINO_PATCHES = (
    Path("bootstrap/trino/v483/patches/0001-shirokuma-web-ui-security.patch"),
    Path("bootstrap/trino/v483/patches/0002-shirokuma-iceberg-only-maven-closure.patch"),
    Path("bootstrap/trino/v483/patches/0003-shirokuma-maven-build-plugin-closure.patch"),
    Path("bootstrap/trino/v483/patches/0004-shirokuma-netty-4.2.17.patch"),
)
DOCKER_JAVA_PATCH = Path(
    "bootstrap/trino/v483/patches/0005-shirokuma-docker-java-httpclient-5.6.4.patch"
)
TRINO_PREIMAGE_SHA256 = (
    "871c6b21cf9fc70c455d21b64d24dd4501a8b5943242418edc2b2f5cfe14fab8"
)
TRINO_POSTIMAGE_SHA256 = (
    "3d0c79d798c68632a23e94abb899b760485e199f4ead530bcc27c52a2f2854d3"
)
DOCKER_JAVA_COMMIT = "7b7fabd4567573e4957e549365dc0df8c2e54ab9"
DOCKER_JAVA_TREE = "f6119a3ff6da4b1df34a1054000b849c70f4aae6"
DOCKER_JAVA_SOURCE_REPOSITORY = "https://github.com/docker-java/docker-java"
DOCKER_JAVA_ORIGIN_ID = "shirokuma-docker-java-source-remediation"
DOCKER_JAVA_POM = Path("docker-java-transport-httpclient5/pom.xml")
DOCKER_JAVA_POM_PREIMAGE_SHA256 = (
    "839b9dcb35b9fa58bb61823733e501058b19f877d614cc53db0df4fdafeb2006"
)
DOCKER_JAVA_POM_POSTIMAGE_SHA256 = (
    "68fee21ce48c9f5d7f6c2ac3a97b6e07df8d7f2f183fcec05f4ae4fb5aa4caf3"
)
DOCKER_JAVA_CENTRAL_JAR_SHA256 = (
    "b89bdb1754160323597f9ea32a7fe7a4a3aa8f5b3b43b88e8d71fff3b267ab21"
)
DOCKER_JAVA_CENTRAL_JAR_BYTES = 2_304_500
DOCKER_JAVA_CANDIDATE_JAR_SHA256 = (
    "6898a76926caa2c875d2963ac9e225f2566270a4a0152f8a151785cdaf8769b0"
)
DOCKER_JAVA_CANDIDATE_JAR_BYTES = 2_446_145
DOCKER_JAVA_REPOSITORY_PATH = Path(
    "com/github/docker-java/docker-java-transport-zerodep/3.7.1/"
    "docker-java-transport-zerodep-3.7.1.jar"
)
FIXED_ZIP_TIMESTAMP = (2026, 5, 6, 0, 0, 0)
EXPECTED_EMBEDDED_PROPERTIES = {
    "META-INF/maven/org.apache.httpcomponents.client5/httpclient5/"
    "pom.properties": "version=5.6.4",
    "META-INF/maven/org.apache.httpcomponents.core5/httpcore5/"
    "pom.properties": "version=5.4.3",
    "META-INF/maven/org.apache.httpcomponents.core5/httpcore5-h2/"
    "pom.properties": "version=5.4.3",
}
SIGNATURE_SUFFIXES = (".SF", ".RSA", ".DSA", ".EC")


class FeasibilityError(RuntimeError):
    pass


def _fail(code: str, message: str) -> None:
    raise FeasibilityError(f"{code}: {message}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _regular_file(path: Path, code: str) -> bytes:
    try:
        status = path.lstat()
    except OSError as error:
        _fail(code, str(error))
    if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
        _fail(code, f"unsafe file: {path}")
    return path.read_bytes()


def _git(checkout: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=checkout, check=True, capture_output=True,
            text=True,
        ).stdout.strip()
    except subprocess.CalledProcessError as error:
        _fail("GIT", error.stderr.strip() or str(error))


def _apply_patch(checkout: Path, patch: Path) -> None:
    command = [
        "git",
        "apply",
        "--unidiff-zero",
        "--whitespace=error-all",
        str(patch.resolve()),
    ]
    try:
        subprocess.run(
            [*command[:2], "--check", *command[2:]], cwd=checkout,
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            command, cwd=checkout, check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as error:
        _fail("PATCH", error.stderr.strip() or str(error))


def apply_trino(root: Path, checkout: Path) -> None:
    root = root.resolve()
    checkout = checkout.resolve()
    publisher.audit_source(root, checkout)
    for patch in TRINO_PATCHES[:3]:
        _apply_patch(checkout, root / patch)
    pom = checkout / "pom.xml"
    if _sha256(pom) != TRINO_PREIMAGE_SHA256:
        _fail("TRINO_PREIMAGE", "reviewed sequence-7 pom postimage differs")
    _apply_patch(checkout, root / TRINO_PATCHES[3])
    verify_trino(checkout)


def verify_trino(checkout: Path) -> None:
    pom = checkout.resolve() / "pom.xml"
    if _sha256(pom) != TRINO_POSTIMAGE_SHA256:
        _fail("TRINO_POSTIMAGE", "Netty candidate pom postimage differs")
    text = pom.read_text(encoding="utf-8")
    if text.count("<dep.netty.version>4.2.17.Final</dep.netty.version>") != 1:
        _fail("TRINO_NETTY", "exact Netty property is missing")
    changed = set(_git(checkout, "diff", "--name-only", "HEAD", "--").splitlines())
    expected: set[str] = set()
    for boundary in (
        publisher.EXPECTED_SOURCE_OVERLAY,
        publisher.EXPECTED_DISTRIBUTION_REMEDIATION,
        publisher.EXPECTED_BUILD_PLUGIN_REMEDIATION,
    ):
        expected.update(boundary["postimages"])
    if changed != expected:
        _fail("TRINO_SCOPE", f"changed paths differ: {sorted(changed)!r}")
    if _git(checkout, "ls-files", "--others", "--exclude-standard"):
        _fail("TRINO_SCOPE", "untracked source files are present")


def apply_docker_java(root: Path, checkout: Path) -> None:
    root = root.resolve()
    checkout = checkout.resolve()
    if _git(checkout, "rev-parse", "HEAD") != DOCKER_JAVA_COMMIT:
        _fail("DOCKER_JAVA_SOURCE", "commit differs")
    if _git(checkout, "rev-parse", "HEAD^{tree}") != DOCKER_JAVA_TREE:
        _fail("DOCKER_JAVA_SOURCE", "tree differs")
    pom = checkout / DOCKER_JAVA_POM
    if _sha256(pom) != DOCKER_JAVA_POM_PREIMAGE_SHA256:
        _fail("DOCKER_JAVA_PREIMAGE", "HttpClient POM preimage differs")
    _apply_patch(checkout, root / DOCKER_JAVA_PATCH)
    verify_docker_java(checkout)


def verify_docker_java(checkout: Path) -> None:
    checkout = checkout.resolve()
    pom = checkout / DOCKER_JAVA_POM
    if _sha256(pom) != DOCKER_JAVA_POM_POSTIMAGE_SHA256:
        _fail("DOCKER_JAVA_POSTIMAGE", "HttpClient POM postimage differs")
    if pom.read_text(encoding="utf-8").count("<version>5.6.4</version>") != 1:
        _fail("DOCKER_JAVA_VERSION", "exact HttpClient version is missing")
    changed = _git(checkout, "diff", "--name-only", "HEAD", "--").splitlines()
    if changed != [DOCKER_JAVA_POM.as_posix()]:
        _fail("DOCKER_JAVA_SCOPE", f"changed paths differ: {changed!r}")
    if _git(checkout, "ls-files", "--others", "--exclude-standard"):
        _fail("DOCKER_JAVA_SCOPE", "untracked source files are present")


def _safe_zip_name(name: str) -> PurePosixPath:
    if "\\" in name or "\x00" in name:
        _fail("JAR_ENTRY", f"unsafe name: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        _fail("JAR_ENTRY", f"unsafe name: {name!r}")
    return path


def canonicalize_jar(source: Path, output: Path) -> None:
    _regular_file(source, "JAR_INPUT")
    if output.exists() or output.is_symlink():
        _fail("JAR_OUTPUT", f"output already exists: {output}")
    seen: set[str] = set()
    entries: list[tuple[str, bytes, int]] = []
    try:
        with zipfile.ZipFile(source, "r") as archive:
            for info in archive.infolist():
                path = _safe_zip_name(info.filename)
                if info.filename in seen:
                    _fail("JAR_ENTRY", f"duplicate name: {info.filename}")
                seen.add(info.filename)
                mode = (info.external_attr >> 16) & 0xFFFF
                kind = stat.S_IFMT(mode)
                if kind not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    _fail("JAR_ENTRY", f"special entry: {info.filename}")
                upper = path.name.upper()
                if path.parts[0].upper() == "META-INF" and upper.endswith(SIGNATURE_SUFFIXES):
                    _fail("JAR_SIGNATURE", f"signed input is not rewriteable: {info.filename}")
                payload = b"" if info.is_dir() else archive.read(info)
                entries.append((info.filename, payload, 0o755 if info.is_dir() else 0o644))
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        _fail("JAR_INPUT", str(error))
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, payload, mode in sorted(entries):
            info = zipfile.ZipInfo(name, FIXED_ZIP_TIMESTAMP)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = ((stat.S_IFDIR if name.endswith("/") else stat.S_IFREG) | mode) << 16
            info.flag_bits = 0
            info.extra = b""
            info.comment = b""
            archive.writestr(info, payload, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    verify_remediated_jar(output)


def verify_remediated_jar(path: Path) -> None:
    _regular_file(path, "JAR_CANDIDATE")
    with zipfile.ZipFile(path, "r") as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            _fail("JAR_ENTRY", "duplicate names remain")
        if any(b"5.3.6" in archive.read(name) for name in names if not name.endswith("/")):
            _fail("JAR_VERSION", "blocked HttpCore 5.3.6 marker remains")
        for name, marker in EXPECTED_EMBEDDED_PROPERTIES.items():
            try:
                text = archive.read(name).decode("iso-8859-1")
            except KeyError:
                _fail("JAR_VERSION", f"missing embedded metadata: {name}")
            if marker not in text:
                _fail("JAR_VERSION", f"embedded version differs: {name}")


def verify_reviewed_jar(path: Path) -> None:
    verify_remediated_jar(path)
    if (
        _sha256(path) != DOCKER_JAVA_CANDIDATE_JAR_SHA256
        or path.stat().st_size != DOCKER_JAVA_CANDIDATE_JAR_BYTES
    ):
        _fail("JAR_IDENTITY", "reviewed canonical candidate identity differs")


def stage_jar(repository: Path, candidate: Path, receipt: Path) -> None:
    repository = repository.resolve()
    receipt = receipt.resolve()
    target = repository / DOCKER_JAVA_REPOSITORY_PATH
    if receipt.is_relative_to(repository):
        _fail("JAR_RECEIPT", "source receipt must be outside the Maven repository")
    if receipt.exists() or receipt.is_symlink():
        _fail("JAR_RECEIPT", f"source receipt already exists: {receipt}")
    if _sha256(target) != DOCKER_JAVA_CENTRAL_JAR_SHA256 or target.stat().st_size != DOCKER_JAVA_CENTRAL_JAR_BYTES:
        _fail("CENTRAL_PREIMAGE", "docker-java zerodep Central preimage differs")
    verify_reviewed_jar(candidate)
    target.write_bytes(candidate.read_bytes())
    target.with_name(target.name + ".sha1").write_text(
        hashlib.sha1(target.read_bytes()).hexdigest(), encoding="ascii"
    )
    for suffix in (".md5", ".sha256", ".sha512"):
        target.with_name(target.name + suffix).unlink(missing_ok=True)
    receipt.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_repository": DOCKER_JAVA_SOURCE_REPOSITORY,
                "source_commit": DOCKER_JAVA_COMMIT,
                "source_tree": DOCKER_JAVA_TREE,
                "source_patch": DOCKER_JAVA_PATCH.as_posix(),
                "central_preimage_sha256": DOCKER_JAVA_CENTRAL_JAR_SHA256,
                "candidate_sha256": _sha256(target),
                "candidate_bytes": target.stat().st_size,
                "canonical_zip_timestamp": "2026-05-06T00:00:00Z",
            },
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    if target.read_bytes() != candidate.read_bytes():
        _fail("JAR_STAGE", "staged source-built JAR differs")


def seal_jar_origin(repository: Path) -> None:
    target = repository.resolve() / DOCKER_JAVA_REPOSITORY_PATH
    verify_reviewed_jar(target)
    marker = target.parent / "_remote.repositories"
    entries: dict[str, str] = {}
    if marker.exists():
        for raw in _regular_file(marker, "JAR_ORIGIN").decode(
            "iso-8859-1"
        ).splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            match = re.fullmatch(r"([^<>=\r\n]+)(?:>|<)([^=\r\n]*)=", line)
            if match is None:
                _fail("JAR_ORIGIN", f"malformed Maven origin marker: {raw!r}")
            filename, origin_id = match.groups()
            if filename == target.name:
                continue
            if filename in entries:
                _fail("JAR_ORIGIN", f"duplicate Maven origin for {filename}")
            if origin_id not in packager.ALLOWED_ORIGIN_IDS:
                _fail("JAR_ORIGIN", f"unknown Maven origin ID: {origin_id}")
            entries[filename] = origin_id
    entries[target.name] = DOCKER_JAVA_ORIGIN_ID
    marker.write_text(
        "".join(f"{name}>{entries[name]}=\n" for name in sorted(entries)),
        encoding="iso-8859-1",
    )
    marker.chmod(0o644)


def manifest_repository(repository: Path, output: Path) -> None:
    existing = packager.ALLOWED_ORIGIN_IDS.get(DOCKER_JAVA_ORIGIN_ID)
    if existing not in (None, DOCKER_JAVA_SOURCE_REPOSITORY):
        _fail("JAR_ORIGIN", "docker-java source origin ID is already reassigned")
    packager.ALLOWED_ORIGIN_IDS[DOCKER_JAVA_ORIGIN_ID] = (
        DOCKER_JAVA_SOURCE_REPOSITORY
    )
    try:
        manifest = packager.build_manifest(repository.resolve())
    finally:
        if existing is None:
            del packager.ALLOWED_ORIGIN_IDS[DOCKER_JAVA_ORIGIN_ID]
        else:
            packager.ALLOWED_ORIGIN_IDS[DOCKER_JAVA_ORIGIN_ID] = existing
    source_records = [
        record for record in manifest["files"]
        if record["repository_origin"] == DOCKER_JAVA_SOURCE_REPOSITORY
    ]
    if len(source_records) != 1 or source_records[0]["path"] != (
        DOCKER_JAVA_REPOSITORY_PATH.as_posix()
    ):
        _fail("JAR_ORIGIN", "docker-java source origin is not exact-path scoped")
    output.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def verify_zero_findings(report: Path) -> None:
    document = json.loads(_regular_file(report, "TRIVY_REPORT"))
    findings = [
        vulnerability
        for result in document.get("Results") or []
        for vulnerability in result.get("Vulnerabilities") or []
        if vulnerability.get("Severity") in {"HIGH", "CRITICAL"}
    ]
    if findings:
        identities = sorted(
            {
                (
                    finding.get("VulnerabilityID"),
                    finding.get("PkgName"),
                    finding.get("InstalledVersion"),
                    finding.get("FixedVersion"),
                    finding.get("Severity"),
                    finding.get("PkgPath"),
                )
                for finding in findings
            },
            key=repr,
        )
        _fail(
            "MAVEN_SCAN_FINDING",
            f"High/Critical findings: {len(findings)} identities={identities!r}",
        )


def _with_publisher_source_origin(callback, *args, **kwargs) -> None:
    key = "docker-java-source-remediation"
    existing = publisher.EXPECTED_REPOSITORIES.get(key)
    if existing not in (None, DOCKER_JAVA_SOURCE_REPOSITORY):
        _fail("JAR_ORIGIN", "publisher source origin key is already reassigned")
    publisher.EXPECTED_REPOSITORIES[key] = DOCKER_JAVA_SOURCE_REPOSITORY
    try:
        callback(*args, **kwargs)
    finally:
        if existing is None:
            del publisher.EXPECTED_REPOSITORIES[key]
        else:
            publisher.EXPECTED_REPOSITORIES[key] = existing


def generate_maven_sbom(
    descriptor: Path, repository: Path, rootfs_sbom: Path, output: Path
) -> None:
    _with_publisher_source_origin(
        publisher.generate_maven_sbom,
        descriptor, repository, rootfs_sbom, output,
    )


def verify_scan(descriptor: Path, sbom: Path, report: Path) -> None:
    _with_publisher_source_origin(
        publisher.verify_maven_scan,
        descriptor, sbom, report,
        allow_high_critical=True,
    )
    document = json.loads(_regular_file(report, "TRIVY_REPORT"))
    if type(document.get("SchemaVersion")) is not int:
        _fail("TRIVY_REPORT", "schema version is not an exact integer")
    packages = [
        package
        for result in document.get("Results") or []
        for package in result.get("Packages") or []
    ]
    identities = {(package.get("Name"), package.get("Version")) for package in packages}
    required = {
        ("io.netty:netty-transport-sctp", "4.2.17.Final"),
        (
            "com.github.docker-java:docker-java-transport-zerodep",
            "3.7.1",
        ),
        ("org.apache.httpcomponents.client5:httpclient5", "5.6.4"),
        ("org.apache.httpcomponents.core5:httpcore5", "5.4.3"),
        ("org.apache.httpcomponents.core5:httpcore5-h2", "5.4.3"),
    }
    if not required <= identities:
        _fail("TRIVY_IDENTITY", f"required packages missing: {sorted(required - identities)!r}")
    if any(version in {"4.2.16.Final", "5.3.6"} for _name, version in identities):
        _fail("TRIVY_IDENTITY", "blocked package version remains")
    verify_zero_findings(report)


def audit_workflow(root: Path) -> None:
    workflow = _regular_file(root / WORKFLOW_PATH, "WORKFLOW").decode("utf-8")
    runner = _regular_file(root / RUNNER_PATH, "RUNNER").decode("utf-8")
    if "pull_request:" not in workflow or "workflow_dispatch:" in workflow or "push:" in workflow:
        _fail("WORKFLOW_TRIGGER", "workflow must be pull-request only")
    if workflow.count("permissions:\n      contents: read") != 1:
        _fail("WORKFLOW_PERMISSION", "job must grant contents: read only")
    required = (
        "apply-trino", "apply-docker-java", "canonicalize-jar", "stage-jar",
        "--network none", "generate-maven-sbom", "scan-type: sbom",
        "verify-scan", "cmp ",
        "ref: ${{ github.event.pull_request.head.sha }}", "fetch-depth: 0",
        "REVIEWED_PREDECESSOR: 59f38dc26a1a02203df9c629360d863e4856a2ba",
        'test "$(git rev-parse HEAD)" = "${REVIEWED_HEAD}"',
        'git rev-list --merges "${REVIEWED_PREDECESSOR}..HEAD"',
        'git rev-parse "${first_commit}^"',
    )
    combined = workflow + "\n" + runner
    for marker in required:
        if marker not in combined:
            _fail("WORKFLOW_STEP", f"required operation missing: {marker}")
    forbidden = (
        "docker login", "oras push", "cosign sign", "ghcr.io", "packages: write",
        "id-token: write", "upload-artifact", "workflow_run:", "authorize-use",
    )
    for marker in forbidden:
        if marker in combined:
            _fail("WORKFLOW_WRITE", f"forbidden operation present: {marker}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("apply-trino", "verify-trino", "apply-docker-java", "verify-docker-java"):
        command = commands.add_parser(name)
        command.add_argument("--checkout", type=Path, required=True)
        if name.startswith("apply-"):
            command.add_argument("--root", type=Path, default=Path("."))
    canonicalize = commands.add_parser("canonicalize-jar")
    canonicalize.add_argument("--source", type=Path, required=True)
    canonicalize.add_argument("--output", type=Path, required=True)
    verify_jar = commands.add_parser("verify-jar")
    verify_jar.add_argument("--jar", type=Path, required=True)
    verify_reviewed = commands.add_parser("verify-reviewed-jar")
    verify_reviewed.add_argument("--jar", type=Path, required=True)
    stage = commands.add_parser("stage-jar")
    stage.add_argument("--repository", type=Path, required=True)
    stage.add_argument("--candidate", type=Path, required=True)
    stage.add_argument("--receipt", type=Path, required=True)
    seal = commands.add_parser("seal-jar-origin")
    seal.add_argument("--repository", type=Path, required=True)
    manifest = commands.add_parser("manifest-repository")
    manifest.add_argument("--repository", type=Path, required=True)
    manifest.add_argument("--output", type=Path, required=True)
    sbom = commands.add_parser("generate-maven-sbom")
    sbom.add_argument("--descriptor", type=Path, required=True)
    sbom.add_argument("--repository", type=Path, required=True)
    sbom.add_argument("--rootfs-sbom", type=Path, required=True)
    sbom.add_argument("--output", type=Path, required=True)
    scan = commands.add_parser("verify-zero-findings")
    scan.add_argument("--report", type=Path, required=True)
    complete_scan = commands.add_parser("verify-scan")
    complete_scan.add_argument("--descriptor", type=Path, required=True)
    complete_scan.add_argument("--sbom", type=Path, required=True)
    complete_scan.add_argument("--report", type=Path, required=True)
    audit = commands.add_parser("audit-workflow")
    audit.add_argument("--root", type=Path, default=Path("."))
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "apply-trino":
            apply_trino(args.root, args.checkout)
        elif args.command == "verify-trino":
            verify_trino(args.checkout)
        elif args.command == "apply-docker-java":
            apply_docker_java(args.root, args.checkout)
        elif args.command == "verify-docker-java":
            verify_docker_java(args.checkout)
        elif args.command == "canonicalize-jar":
            canonicalize_jar(args.source, args.output)
        elif args.command == "verify-jar":
            verify_remediated_jar(args.jar)
        elif args.command == "verify-reviewed-jar":
            verify_reviewed_jar(args.jar)
        elif args.command == "stage-jar":
            stage_jar(args.repository, args.candidate, args.receipt)
        elif args.command == "seal-jar-origin":
            seal_jar_origin(args.repository)
        elif args.command == "manifest-repository":
            manifest_repository(args.repository, args.output)
        elif args.command == "generate-maven-sbom":
            generate_maven_sbom(
                args.descriptor, args.repository, args.rootfs_sbom, args.output
            )
        elif args.command == "verify-zero-findings":
            verify_zero_findings(args.report)
        elif args.command == "verify-scan":
            verify_scan(args.descriptor, args.sbom, args.report)
        elif args.command == "audit-workflow":
            audit_workflow(args.root.resolve())
    except (
        FeasibilityError,
        packager.SnapshotError,
        publisher.ContractError,
        OSError,
        json.JSONDecodeError,
        zipfile.BadZipFile,
    ) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
