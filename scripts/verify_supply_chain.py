#!/usr/bin/env python3
"""Deterministic, fail-closed checks for repository supply-chain policy."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from verify_gitops_image_admission import (
        AdmissionError as FluxAdmissionError,
        GOTK_COMPONENTS_REPOSITORY_PATH,
        resolve_effective_flux_images,
    )
except ModuleNotFoundError:  # pragma: no cover - supports module-style test imports
    from scripts.verify_gitops_image_admission import (
        AdmissionError as FluxAdmissionError,
        GOTK_COMPONENTS_REPOSITORY_PATH,
        resolve_effective_flux_images,
    )


BLOCKING_SEVERITIES = {"HIGH", "CRITICAL"}
LAB_PROFILE = "local-lab"
STRICT_PROFILE = "strict"
MAX_EXCEPTION_DAYS = 30
IMAGE_DIGEST = re.compile(r"^(?P<repository>[^@\s]+)@sha256:[0-9a-f]{64}$")
DEPLOYMENT_SUFFIXES = {".json", ".yaml", ".yml"}
YAML_IMAGE_FIELD = re.compile(
    r'''^\s*(?:-\s*)?(?:image|"image"|'image')\s*:\s*(?P<value>.*?)\s*$'''
)
YAML_INLINE_IMAGE_FIELD = re.compile(r"(?:\{|\[|,)\s*[\"']?image[\"']?\s*:")
SECRET_PATTERNS = (
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b"),
)
SECRET_MARKERS = (
    "-----BEGIN " + "PRIVATE KEY-----",
    "-----BEGIN " + "RSA PRIVATE KEY-----",
    "-----BEGIN " + "OPENSSH PRIVATE KEY-----",
)
SECRET_FILENAME = re.compile(
    r"(^|/)(\.env|[^/]+\.(?:pem|key|p12|pfx|token|secret))$", re.IGNORECASE
)
MINIO_IDENTIFIER = re.compile(r"(^|[/:._-])minio([/:._-]|$)", re.IGNORECASE)
SIGNED_INDEX_EVIDENCE_MODE = "signed_oci_index"
REPOSITORY_SOURCE_BUILD_EVIDENCE_MODE = "repository_source_build"
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
CANONICAL_REPOSITORY_SOURCE_BUILDS = {
    ("seaweedfs", "4.39"): {
        "reference": (
            "ghcr.io/tommykammy/shirokuma-seaweedfs@"
            "sha256:d1339701907587c93c6af8740388226ac2277cbbfd3df581c0e85d815c90e421"
        ),
        "admission": "bootstrap/seaweedfs/v4.39/admission.json",
        "release_evidence": "bootstrap/seaweedfs/v4.39/release-evidence.json",
        "sbom_source": (
            "bootstrap/seaweedfs/v4.39/evidence/seaweedfs-4.39-arm64.cdx.json"
        ),
        "scan_source": "bootstrap/seaweedfs/v4.39/evidence/trivy.json",
    }
}


class PolicyError(RuntimeError):
    pass


def load_json(path: Path) -> Any:
    if path.is_symlink():
        raise PolicyError(f"refusing to read symbolic link {path.name}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PolicyError(f"cannot read valid JSON from {path.name}: {error}") from error


def tracked_files(repository: Path) -> list[str]:
    try:
        output = subprocess.run(
            ["git", "-C", str(repository), "ls-files", "-z"],
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise PolicyError("cannot enumerate tracked files") from error
    return [entry.decode("utf-8") for entry in output.split(b"\0") if entry]


def read_tracked_text(repository: Path, relative: str) -> str | None:
    path = repository / relative
    try:
        if path.is_symlink():
            content = subprocess.run(
                ["git", "-C", str(repository), "cat-file", "blob", f":./{relative}"],
                check=True,
                capture_output=True,
            ).stdout.decode("utf-8")
        else:
            content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
    except (OSError, subprocess.CalledProcessError) as error:
        raise PolicyError(f"cannot read tracked file {relative}") from error
    return content


def scan_secrets(repository: Path) -> None:
    findings: list[str] = []
    for relative in tracked_files(repository):
        if SECRET_FILENAME.search(relative):
            findings.append(f"{relative}: secret-like filename")
            continue
        content = read_tracked_text(repository, relative)
        if content is None:
            continue
        if any(pattern.search(content) for pattern in SECRET_PATTERNS) or any(
            marker in content for marker in SECRET_MARKERS
        ):
            findings.append(f"{relative}: secret-like content")

    if findings:
        raise PolicyError("secret scan rejected tracked files:\n" + "\n".join(findings))


def iter_trivy_findings(report: dict[str, Any]) -> list[dict[str, Any]]:
    if "Results" not in report:
        raise PolicyError("Trivy report is missing Results")
    results = report["Results"]
    if not isinstance(results, list):
        raise PolicyError("Trivy report Results must be a list")

    findings: list[dict[str, Any]] = []
    for result in results:
        if not isinstance(result, dict):
            raise PolicyError("Trivy report contains a malformed result")
        for category in ("Vulnerabilities", "Misconfigurations", "Secrets"):
            entries = result[category] if category in result else []
            if not isinstance(entries, list):
                raise PolicyError(f"Trivy report {category} must be a list")
            for entry in entries:
                if not isinstance(entry, dict):
                    raise PolicyError(f"Trivy report {category} contains malformed data")
                finding = dict(entry)
                finding["_category"] = category
                findings.append(finding)
    return findings


def high_finding_key(finding: dict[str, Any]) -> tuple[str, str, str]:
    if finding.get("_category") != "Vulnerabilities":
        raise PolicyError("local-lab exceptions only apply to vulnerability findings")
    values = tuple(
        str(finding.get(field, "")).strip()
        for field in ("VulnerabilityID", "PkgName", "InstalledVersion")
    )
    if not all(values):
        raise PolicyError(
            "local-lab High findings require VulnerabilityID, PkgName, and InstalledVersion"
        )
    return values


def check_trivy(
    report_path: Path,
    expected_image_reference: str | None = None,
    allowed_high: set[tuple[str, str, str]] | None = None,
) -> None:
    report = load_json(report_path)
    if not isinstance(report, dict):
        raise PolicyError("Trivy report must be a JSON object")
    if expected_image_reference is not None:
        artifact_name = report.get("ArtifactName")
        artifact_reference = artifact_name.strip() if isinstance(artifact_name, str) else ""
        metadata = report.get("Metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise PolicyError("Trivy report Metadata must be an object")
        repository_digests: list[str] = []
        if isinstance(metadata, dict):
            digest_values = metadata.get("RepoDigests", [])
            if not isinstance(digest_values, list) or not all(
                isinstance(value, str) for value in digest_values
            ):
                raise PolicyError("Trivy report Metadata.RepoDigests must be a string list")
            repository_digests = [value.strip() for value in digest_values]
        bound_reference_matches = (
            expected_image_reference in repository_digests
            if repository_digests
            else artifact_reference == expected_image_reference
        )
        if not bound_reference_matches:
            raise PolicyError(
                "Trivy report target does not match ledger reference "
                f"{expected_image_reference}"
            )
    blocking = [
        finding
        for finding in iter_trivy_findings(report)
        if str(finding.get("Severity", "")).upper() in BLOCKING_SEVERITIES
    ]
    critical = [
        finding
        for finding in blocking
        if str(finding.get("Severity", "")).upper() == "CRITICAL"
    ]
    if critical:
        raise PolicyError(f"Trivy blocking threshold crossed: CRITICAL={len(critical)}")

    high = [
        finding
        for finding in blocking
        if str(finding.get("Severity", "")).upper() == "HIGH"
    ]
    if allowed_high is not None:
        observed_high = {high_finding_key(finding) for finding in high}
        if observed_high != allowed_high:
            unapproved = sorted(observed_high - allowed_high)
            stale = sorted(allowed_high - observed_high)
            details: list[str] = []
            if unapproved:
                details.append(
                    "unapproved="
                    + ",".join(f"{cve}/{package}/{version}" for cve, package, version in unapproved)
                )
            if stale:
                details.append(
                    "stale="
                    + ",".join(f"{cve}/{package}/{version}" for cve, package, version in stale)
                )
            raise PolicyError("local-lab High exception mismatch: " + " ".join(details))
        return

    if high:
        counts = {
            severity: sum(
                str(finding.get("Severity", "")).upper() == severity
                for finding in blocking
            )
            for severity in sorted(BLOCKING_SEVERITIES)
        }
        summary = ", ".join(f"{severity}={count}" for severity, count in counts.items() if count)
        raise PolicyError(f"Trivy blocking threshold crossed: {summary}")


def reject_manifest_ancestor_symlinks(
    manifest_path: Path,
    repository: Path,
    field: str,
) -> Path:
    repository_root = repository.absolute()
    manifest_parent = manifest_path.absolute().parent
    if manifest_parent.is_symlink():
        raise PolicyError(
            f"refusing to read {field} through symbolic link ancestor {manifest_parent}"
        )
    try:
        relative_parent = manifest_parent.relative_to(repository_root)
    except ValueError:
        try:
            manifest_parent.resolve(strict=False).relative_to(
                repository_root.resolve(strict=False)
            )
        except ValueError:
            return manifest_parent
        raise PolicyError(f"refusing to read {field} through a symbolic link ancestor")

    current = repository_root
    for part in relative_parent.parts:
        current /= part
        if current.is_symlink():
            raise PolicyError(
                f"refusing to read {field} through symbolic link ancestor {current}"
            )
    return manifest_parent


def evidence_artifact_path(
    manifest_path: Path,
    repository: Path,
    value: str,
    field: str,
) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise PolicyError(f"{field} must be relative to the resident image manifest")

    current = reject_manifest_ancestor_symlinks(manifest_path, repository, field)
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise PolicyError(f"refusing to read symbolic link {field} {value}")
    return current


def repository_artifact_path(repository: Path, value: str, field: str) -> Path:
    relative = Path(value)
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise PolicyError(f"{field} must be a repository-relative path")

    current = repository
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise PolicyError(f"refusing to read symbolic link {field} {value}")
    try:
        current.relative_to(repository)
    except ValueError as error:
        raise PolicyError(f"{field} escapes the repository") from error
    return current


def file_sha256(path: Path, field: str) -> str:
    if path.is_symlink():
        raise PolicyError(f"refusing to hash symbolic link {field} {path.name}")
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise PolicyError(f"cannot read {field} {path.name}: {error}") from error


def checked_repository_binding(
    repository: Path,
    binding: Any,
    field: str,
) -> tuple[Path, str]:
    if not isinstance(binding, dict):
        raise PolicyError(f"repository source-build evidence requires {field} binding")
    value = binding.get("path")
    expected_sha256 = binding.get("sha256")
    if not isinstance(value, str) or not value.strip():
        raise PolicyError(f"repository source-build {field}.path must be non-empty")
    if not isinstance(expected_sha256, str) or not SHA256_HEX.fullmatch(expected_sha256):
        raise PolicyError(f"repository source-build {field}.sha256 must be lowercase SHA-256")
    path = repository_artifact_path(repository, value, f"{field}.path")
    observed_sha256 = file_sha256(path, field)
    if observed_sha256 != expected_sha256:
        raise PolicyError(
            f"repository source-build {field} hash mismatch: "
            f"expected {expected_sha256}, got {observed_sha256}"
        )
    return path, observed_sha256


def check_sbom(sbom_path: Path) -> None:
    sbom = load_json(sbom_path)
    if not isinstance(sbom, dict) or sbom.get("bomFormat") != "CycloneDX":
        raise PolicyError("SBOM artifact must be a CycloneDX JSON object")


def reference_digest(reference: str) -> str:
    return reference.rsplit("@", 1)[1]


def check_signed_index_supply_chain_record(
    record: dict[str, Any],
    *,
    reference: str,
    version: str,
    source: str,
) -> None:
    expected_digest = reference_digest(reference)
    signature = record.get("signature")
    if not isinstance(signature, dict) or signature.get("verified") is not True:
        raise PolicyError("supply-chain evidence requires a verified signature")
    signed_index = signature.get("signed_index")
    if not isinstance(signed_index, str) or not is_immutable_image_reference(signed_index):
        raise PolicyError("supply-chain evidence requires an immutable signed_index")
    if signed_index.rsplit("@", 1)[0] != reference.rsplit("@", 1)[0]:
        raise PolicyError("signed index repository does not match the ledger reference")
    if signature.get("arm64_in_signed_index") is not True:
        raise PolicyError("supply-chain signature must cover an index containing linux/arm64")
    if signature.get("arm64_manifest_digest") != expected_digest:
        raise PolicyError("signed index arm64 digest does not match the ledger digest")
    for field in ("issuer", "identity", "workflow_repository", "workflow_ref", "commit"):
        if not isinstance(signature.get(field), str) or not signature[field].strip():
            raise PolicyError(f"supply-chain signature evidence missing {field}")
    if not isinstance(signature.get("transparency_log_index"), int):
        raise PolicyError("supply-chain signature evidence missing transparency_log_index")

    provenance = record.get("provenance")
    if not isinstance(provenance, dict):
        raise PolicyError("supply-chain evidence requires provenance")
    if provenance.get("predicate_type") != "https://slsa.dev/provenance/v1":
        raise PolicyError("supply-chain provenance must use SLSA provenance v1")
    if provenance.get("subject_digest") != expected_digest:
        raise PolicyError("supply-chain provenance subject does not match the ledger digest")
    if provenance.get("source") != source or provenance.get("version") != version:
        raise PolicyError("supply-chain provenance source/version does not match the ledger")
    for field in ("revision", "builder", "attestation_manifest"):
        if not isinstance(provenance.get(field), str) or not provenance[field].strip():
            raise PolicyError(f"supply-chain provenance missing {field}")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", provenance["attestation_manifest"]):
        raise PolicyError("supply-chain provenance attestation_manifest must be a sha256 digest")

    upstream_sbom = record.get("upstream_sbom")
    if not isinstance(upstream_sbom, dict):
        raise PolicyError("supply-chain evidence requires an upstream SBOM attestation")
    if upstream_sbom.get("predicate_type") != "https://spdx.dev/Document":
        raise PolicyError("upstream SBOM attestation must use SPDX Document")
    if upstream_sbom.get("subject_digest") != expected_digest:
        raise PolicyError("upstream SBOM subject does not match the ledger digest")


def check_repository_source_build_record(
    record: dict[str, Any],
    *,
    repository: Path,
    component: str,
    reference: str,
    version: str,
    source: str,
    sbom_path: Path,
    scan_path: Path,
    sbom_generator: str,
    scanner_version: str,
    vulnerability_db_updated_at: str,
) -> None:
    source_build = record.get("repository_source_build")
    if not isinstance(source_build, dict):
        raise PolicyError("repository_source_build mode requires repository_source_build evidence")

    canonical = CANONICAL_REPOSITORY_SOURCE_BUILDS.get((component, version))
    if canonical is None:
        raise PolicyError(
            f"repository_source_build mode is not approved for {component} {version}"
        )
    if reference != canonical["reference"]:
        raise PolicyError(
            "repository source-build reference does not match the canonical approved digest"
        )

    admission_path, _ = checked_repository_binding(
        repository,
        source_build.get("admission"),
        "repository_source_build.admission",
    )
    release_path, _ = checked_repository_binding(
        repository,
        source_build.get("release_evidence"),
        "repository_source_build.release_evidence",
    )
    if source_build["admission"].get("path") != canonical["admission"]:
        raise PolicyError("repository source-build admission path is not canonical")
    if source_build["release_evidence"].get("path") != canonical["release_evidence"]:
        raise PolicyError("repository source-build release evidence path is not canonical")

    resident_evidence = source_build.get("resident_evidence")
    if not isinstance(resident_evidence, dict):
        raise PolicyError("repository source-build evidence requires resident_evidence")
    bound_sources: dict[str, tuple[Path, str]] = {}
    for name, observed_path in (("sbom", sbom_path), ("scan", scan_path)):
        binding = resident_evidence.get(name)
        if not isinstance(binding, dict):
            raise PolicyError(f"repository source-build evidence requires {name} binding")
        resident_path, resident_sha256 = checked_repository_binding(
            repository,
            binding.get("resident"),
            f"repository_source_build.resident_evidence.{name}.resident",
        )
        source_path, source_sha256 = checked_repository_binding(
            repository,
            binding.get("source"),
            f"repository_source_build.resident_evidence.{name}.source",
        )
        if binding["source"].get("path") != canonical[f"{name}_source"]:
            raise PolicyError(
                f"repository source-build {name} source path is not canonical"
            )
        if resident_path.resolve(strict=False) != observed_path.resolve(strict=False):
            raise PolicyError(
                f"repository source-build {name} resident path does not match the ledger artifact"
            )
        if resident_sha256 != source_sha256:
            raise PolicyError(f"repository source-build {name} hashes do not match")
        try:
            if resident_path.read_bytes() != source_path.read_bytes():
                raise PolicyError(f"repository source-build {name} bytes do not match")
        except OSError as error:
            raise PolicyError(f"cannot compare repository source-build {name}: {error}") from error
        bound_sources[name] = (source_path, source_sha256)

    admission = load_json(admission_path)
    release = load_json(release_path)
    if not isinstance(admission, dict) or admission.get("schema_version") != 2:
        raise PolicyError("repository source-build admission requires schema_version 2")
    if not isinstance(release, dict) or release.get("schema_version") != 2:
        raise PolicyError("repository source-build release evidence requires schema_version 2")

    expected_digest = reference_digest(reference)
    for label, evidence in (("admission", admission), ("release evidence", release)):
        if evidence.get("component") != component or evidence.get("version") != version:
            raise PolicyError(f"repository source-build {label} component/version mismatch")
        if evidence.get("platform") != "linux/arm64":
            raise PolicyError(f"repository source-build {label} platform must be linux/arm64")
    if admission.get("source") != source:
        raise PolicyError("repository source-build admission source does not match the ledger")
    release_source = release.get("source")
    if not isinstance(release_source, dict) or release_source.get("repository") != source:
        raise PolicyError("repository source-build release source does not match the ledger")

    assessment = admission.get("assessment")
    candidate = admission.get("admitted_candidate")
    if not isinstance(assessment, dict) or assessment.get("admission") != "approved":
        raise PolicyError("repository source-build admission must be approved")
    if not isinstance(candidate, dict):
        raise PolicyError("repository source-build admission requires an admitted_candidate")
    if (
        candidate.get("reference") != reference
        or candidate.get("manifest_digest") != expected_digest
    ):
        raise PolicyError("repository source-build admission candidate does not match the ledger")
    release_value = source_build.get("release_evidence")
    if candidate.get("release_evidence") != release_value.get("path"):
        raise PolicyError("repository source-build admission release path mismatch")
    if (
        release.get("admission_status") != "approved"
        or release.get("reference") != reference
        or release.get("digest") != expected_digest
    ):
        raise PolicyError("repository source-build release admission does not match the ledger")

    controls = candidate.get("controls")
    if not isinstance(controls, list):
        raise PolicyError("repository source-build admission requires controls")
    control_records = {
        value.get("control"): value
        for value in controls
        if isinstance(value, dict) and isinstance(value.get("control"), str)
    }
    required_controls = {
        "source_adoption",
        "signature",
        "transparency_log",
        "workflow_revision",
        "slsa_provenance",
        "sbom",
        "vulnerability_scan",
        "runtime_tmp",
        "tag_promotion",
    }
    if len(control_records) != len(controls) or not required_controls.issubset(control_records):
        raise PolicyError("repository source-build admission controls are missing or duplicated")
    for name in required_controls:
        if control_records[name].get("status") != "verified":
            raise PolicyError(f"repository source-build admission control {name} is not verified")

    sbom_source_path, sbom_sha256 = bound_sources["sbom"]
    scan_source_path, scan_sha256 = bound_sources["scan"]
    sbom_control = control_records["sbom"]
    scan_control = control_records["vulnerability_scan"]
    if sbom_control.get("path") != str(sbom_source_path.relative_to(repository)):
        raise PolicyError("repository source-build SBOM control path mismatch")
    if sbom_control.get("sha256") != sbom_sha256:
        raise PolicyError("repository source-build SBOM control hash mismatch")
    if scan_control.get("path") != str(scan_source_path.relative_to(repository)):
        raise PolicyError("repository source-build scan control path mismatch")
    if scan_control.get("sha256") != scan_sha256:
        raise PolicyError("repository source-build scan control hash mismatch")
    if scan_control.get("critical") != 0 or scan_control.get("high") != 0:
        raise PolicyError("repository source-build scan requires Critical=0 and High=0")
    observed_scanner = (
        f"{scan_control.get('scanner_name')} {scan_control.get('scanner_version')}"
    )
    if observed_scanner != scanner_version:
        raise PolicyError("repository source-build scanner version does not match the ledger")
    if scan_control.get("vulnerability_db_updated_at") != vulnerability_db_updated_at:
        raise PolicyError(
            "repository source-build vulnerability DB timestamp does not match the ledger"
        )

    artifacts = release.get("artifacts")
    if not isinstance(artifacts, dict):
        raise PolicyError("repository source-build release evidence requires artifacts")
    for name, source_path, sha256 in (
        ("seaweedfs-4.39-arm64.cdx.json", sbom_source_path, sbom_sha256),
        ("trivy.json", scan_source_path, scan_sha256),
    ):
        artifact = artifacts.get(name)
        if not isinstance(artifact, dict):
            raise PolicyError(f"repository source-build release evidence missing {name}")
        if artifact.get("path") != str(source_path.relative_to(repository)):
            raise PolicyError(f"repository source-build release {name} path mismatch")
        if artifact.get("sha256") != sha256:
            raise PolicyError(f"repository source-build release {name} hash mismatch")
    vulnerabilities = release.get("vulnerabilities")
    if not isinstance(vulnerabilities, dict) or (
        vulnerabilities.get("critical") != 0 or vulnerabilities.get("high") != 0
    ):
        raise PolicyError("repository source-build release requires Critical=0 and High=0")
    scanner = release.get("scanner")
    observed_release_scanner = (
        f"{scanner.get('name')} {scanner.get('version')}"
        if isinstance(scanner, dict)
        else ""
    )
    if observed_release_scanner != scanner_version:
        raise PolicyError("repository source-build release scanner version mismatch")
    vulnerability_db = scanner.get("vulnerability_db")
    if (
        not isinstance(vulnerability_db, dict)
        or vulnerability_db.get("updated_at") != vulnerability_db_updated_at
    ):
        raise PolicyError("repository source-build release vulnerability DB timestamp mismatch")
    toolchain = release.get("toolchain")
    syft = toolchain.get("syft") if isinstance(toolchain, dict) else None
    syft_version = str(syft.get("version", "")).removeprefix("v") if isinstance(syft, dict) else ""
    if f"syft {syft_version}" != sbom_generator:
        raise PolicyError("repository source-build SBOM generator does not match the ledger")


def check_supply_chain_evidence(
    evidence_path: Path,
    *,
    repository: Path,
    component: str,
    reference: str,
    version: str,
    source: str,
    sbom_path: Path,
    scan_path: Path,
    sbom_generator: str,
    scanner_version: str,
    vulnerability_db_updated_at: str,
) -> None:
    evidence = load_json(evidence_path)
    if not isinstance(evidence, dict) or evidence.get("schema_version") != 1:
        raise PolicyError("supply-chain evidence requires schema_version 1")
    records = evidence.get("images")
    if not isinstance(records, list):
        raise PolicyError("supply-chain evidence requires an images list")
    matches = [
        record
        for record in records
        if isinstance(record, dict)
        and record.get("component") == component
        and record.get("reference") == reference
    ]
    if len(matches) != 1:
        raise PolicyError("supply-chain evidence requires one exact component/reference record")
    record = matches[0]
    if record.get("platform") != "linux/arm64":
        raise PolicyError("supply-chain evidence platform must be linux/arm64")
    if record.get("version") != version or record.get("source") != source:
        raise PolicyError("supply-chain evidence version/source does not match the ledger")
    verified_at = record.get("verified_at")
    parsed_verified_at = parse_timestamp(verified_at) if isinstance(verified_at, str) else None
    if parsed_verified_at is None:
        raise PolicyError("supply-chain evidence requires a timezone-qualified verified_at")
    if parsed_verified_at.astimezone(timezone.utc) > datetime.now(timezone.utc):
        raise PolicyError("supply-chain evidence verified_at must not be in the future")

    mode = record.get("evidence_mode", SIGNED_INDEX_EVIDENCE_MODE)
    if mode == SIGNED_INDEX_EVIDENCE_MODE:
        check_signed_index_supply_chain_record(
            record,
            reference=reference,
            version=version,
            source=source,
        )
    elif mode == REPOSITORY_SOURCE_BUILD_EVIDENCE_MODE:
        check_repository_source_build_record(
            record,
            repository=repository,
            component=component,
            reference=reference,
            version=version,
            source=source,
            sbom_path=sbom_path,
            scan_path=scan_path,
            sbom_generator=sbom_generator,
            scanner_version=scanner_version,
            vulnerability_db_updated_at=vulnerability_db_updated_at,
        )
    else:
        raise PolicyError(f"unsupported supply-chain evidence_mode {mode!r}")


def exception_finding_key(value: Any, component: str) -> tuple[str, str, str] | None:
    if not isinstance(value, dict):
        return None
    cve = value.get("id")
    package = value.get("package")
    installed_version = value.get("installed_version")
    if not all(isinstance(field, str) and field.strip() for field in (cve, package, installed_version)):
        return None
    if value.get("severity") != "HIGH":
        raise PolicyError(f"{component}: local-lab exceptions may only allow HIGH severity")
    fixed_version = value.get("fixed_version")
    if not isinstance(fixed_version, str):
        raise PolicyError(f"{component}: exception fixed_version must be a string")
    return cve.strip(), package.strip(), installed_version.strip()


def load_lab_exceptions(
    path: Path,
    repository: Path,
    ledger_references: set[str],
) -> dict[str, set[tuple[str, str, str]]]:
    document = load_json(path)
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise PolicyError("resident image exceptions require schema_version 1")
    if document.get("profile") != LAB_PROFILE:
        raise PolicyError(f"resident image exceptions profile must be {LAB_PROFILE}")
    entries = document.get("exceptions")
    if not isinstance(entries, list):
        raise PolicyError("resident image exceptions require an exceptions list")

    approved: dict[str, set[tuple[str, str, str]]] = {}
    for index, entry in enumerate(entries):
        label = f"exceptions[{index}]"
        if not isinstance(entry, dict):
            raise PolicyError(f"{label}: entry must be an object")
        component = entry.get("component")
        if not isinstance(component, str) or not component.strip():
            raise PolicyError(f"{label}: missing component")
        reference = entry.get("reference")
        if not isinstance(reference, str) or not is_immutable_image_reference(reference):
            raise PolicyError(f"{component}: exception requires an immutable image reference")
        if reference not in ledger_references:
            raise PolicyError(f"{component}: exception reference is not present in the resident ledger")
        if reference in approved:
            raise PolicyError(f"{component}: duplicate exception reference")
        if entry.get("scope") != "mac-studio-solo/local-lab":
            raise PolicyError(f"{component}: exception scope must be mac-studio-solo/local-lab")
        if entry.get("max_severity") != "HIGH":
            raise PolicyError(f"{component}: exception max_severity must be HIGH")
        for field in ("risk_acceptance", "replacement_plan"):
            value = entry.get(field)
            if not isinstance(value, str) or not value.strip():
                raise PolicyError(f"{component}: exception missing {field}")
        controls = entry.get("compensating_controls")
        if not isinstance(controls, list) or len(controls) < 3 or not all(
            isinstance(value, str) and value.strip() for value in controls
        ):
            raise PolicyError(f"{component}: exception requires at least three controls")

        decision_record = entry.get("decision_record")
        if not isinstance(decision_record, str) or not decision_record.strip():
            raise PolicyError(f"{component}: exception missing decision_record")
        decision_path = Path(decision_record)
        if (
            decision_path.is_absolute()
            or ".." in decision_path.parts
            or not decision_record.startswith("docs/design/07_ADR/")
            or not (repository / decision_path).is_file()
        ):
            raise PolicyError(f"{component}: exception decision_record must be an existing ADR")

        try:
            approved_on = date.fromisoformat(str(entry.get("approved_on", "")))
            expires_on = date.fromisoformat(str(entry.get("expires_on", "")))
        except ValueError as error:
            raise PolicyError(f"{component}: exception dates must use YYYY-MM-DD") from error
        if approved_on > date.today():
            raise PolicyError(f"{component}: exception approved_on must not be in the future")
        if expires_on <= date.today():
            raise PolicyError(f"{component}: exception has expired")
        if expires_on > approved_on + timedelta(days=MAX_EXCEPTION_DAYS):
            raise PolicyError(f"{component}: exception may not exceed {MAX_EXCEPTION_DAYS} days")

        cves = entry.get("cves")
        if not isinstance(cves, list) or not cves:
            raise PolicyError(f"{component}: exception requires exact CVE records")
        keys: set[tuple[str, str, str]] = set()
        for cve in cves:
            key = exception_finding_key(cve, component)
            if key is None:
                raise PolicyError(
                    f"{component}: exception CVEs require id, package, and installed_version"
                )
            if key in keys:
                raise PolicyError(f"{component}: duplicate exception CVE record")
            keys.add(key)
        approved[reference] = keys
    return approved


def is_immutable_image_reference(reference: str) -> bool:
    match = IMAGE_DIGEST.fullmatch(reference)
    if match is None:
        return False
    return ":" not in match.group("repository").rsplit("/", 1)[-1]


def parse_timestamp(value: str) -> datetime | None:
    normalized = value.replace("Z", "+00:00")
    normalized = re.sub(
        r"(\.[0-9]{6})[0-9]+(?=[+-][0-9]{2}:[0-9]{2}$)",
        r"\1",
        normalized,
    )
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def is_future_iso_date(value: str) -> bool:
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        return False
    try:
        expiry = date.fromisoformat(value)
    except ValueError:
        return False
    return expiry > date.today()


def json_image_references(value: Any, path: str) -> list[tuple[str, str]]:
    references: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "image":
                if not isinstance(child, str) or not child.strip():
                    raise PolicyError(f"{path}: image field must be a non-empty string")
                references.append((path, child.strip()))
            else:
                references.extend(json_image_references(child, path))
    elif isinstance(value, list):
        for child in value:
            references.extend(json_image_references(child, path))
    return references


def is_minio_image(image: dict[str, Any]) -> bool:
    return any(
        MINIO_IDENTIFIER.search(str(image.get(field, ""))) is not None
        for field in ("component", "reference", "source")
    )


def deployed_image_references(repository: Path) -> list[tuple[str, str]]:
    references: list[tuple[str, str]] = []
    for relative in tracked_files(repository):
        path = Path(relative)
        is_deployment_manifest = relative.startswith("deploy/")
        is_helm_template = relative.startswith("charts/") and "/templates/" in relative
        if not (is_deployment_manifest or is_helm_template) or path.suffix not in DEPLOYMENT_SUFFIXES:
            continue
        absolute = repository / path
        if relative == GOTK_COMPONENTS_REPOSITORY_PATH:
            flux_inputs = {
                "candidates_path": repository / "opentofu/dev/bootstrap-images.json",
                "inventory_path": repository / "bootstrap/flux/v2.9.2/components.json",
                "ledger_path": repository / "security/resident-images.json",
                "customization_path": repository
                / "deploy/gitops/clusters/local-lite/flux-system/kustomization.yaml",
                "components_path": absolute,
                "sync_path": repository
                / "deploy/gitops/clusters/local-lite/flux-system/gotk-sync.yaml",
            }
            for label, flux_path in flux_inputs.items():
                reject_manifest_ancestor_symlinks(flux_path, repository, label)
            try:
                effective = resolve_effective_flux_images(**flux_inputs)
            except FluxAdmissionError as error:
                raise PolicyError(
                    f"{relative}: generated Flux image admission failed: {error}"
                ) from error
            references.extend((relative, reference) for reference in effective.values())
            continue
        if path.suffix == ".json":
            references.extend(json_image_references(load_json(absolute), relative))
            continue
        if absolute.is_symlink():
            raise PolicyError(f"refusing to read symbolic link deployment manifest {relative}")
        try:
            lines = absolute.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as error:
            raise PolicyError(f"cannot read deployment manifest {relative}: {error}") from error
        for line_number, line in enumerate(lines, start=1):
            match = YAML_IMAGE_FIELD.match(line)
            if match is None:
                if YAML_INLINE_IMAGE_FIELD.search(line):
                    raise PolicyError(
                        f"{relative}:{line_number}: inline flow-style image fields are unsupported"
                    )
                continue
            reference = match.group("value").split(" #", 1)[0].strip()
            if len(reference) >= 2 and reference[0] == reference[-1] and reference[0] in "\"'":
                reference = reference[1:-1].strip()
            if not reference:
                raise PolicyError(
                    f"{relative}:{line_number}: image field must be a non-empty string"
                )
            references.append((relative, reference))
    return references


def check_images(
    manifest_path: Path,
    repository: Path,
    *,
    profile: str = STRICT_PROFILE,
    exceptions_path: Path | None = None,
) -> None:
    manifest = load_json(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise PolicyError("resident image manifest requires schema_version 1")
    images = manifest.get("images")
    if not isinstance(images, list):
        raise PolicyError("resident image manifest requires an images list")
    if profile not in {STRICT_PROFILE, LAB_PROFILE}:
        raise PolicyError(f"resident image profile must be {STRICT_PROFILE} or {LAB_PROFILE}")
    if profile == STRICT_PROFILE and exceptions_path is not None:
        raise PolicyError("strict resident image profile does not allow exceptions")
    if profile == LAB_PROFILE and exceptions_path is None:
        raise PolicyError("local-lab resident image profile requires --exceptions")

    errors: list[str] = []
    ledger_references: set[str] = set()
    for index, image in enumerate(images):
        label = f"images[{index}]"
        if not isinstance(image, dict):
            errors.append(f"{label}: entry must be an object")
            continue
        component_value = image.get("component")
        component = (
            component_value.strip()
            if isinstance(component_value, str) and component_value.strip()
            else label
        )
        reference_value = image.get("reference")
        reference = reference_value if isinstance(reference_value, str) else ""
        ledger_references.add(reference)
        if not is_immutable_image_reference(reference):
            errors.append(
                f"{component}: reference requires exact repository@sha256 digest without a tag"
            )
        if image.get("platform") != "linux/arm64":
            errors.append(f"{component}: platform must be linux/arm64")
        for field in (
            "version",
            "source",
            "sbom_artifact",
            "scan_artifact",
            "supply_chain_artifact",
            "sbom_generator",
            "scanner_version",
            "vulnerability_db_updated_at",
        ):
            value = image.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{component}: missing {field}")
        database_timestamp_value = image.get("vulnerability_db_updated_at")
        database_timestamp = (
            database_timestamp_value.strip()
            if isinstance(database_timestamp_value, str)
            else ""
        )
        if database_timestamp:
            parsed_timestamp = parse_timestamp(database_timestamp)
            if parsed_timestamp is None:
                errors.append(
                    f"{component}: vulnerability_db_updated_at requires an ISO-8601 timestamp with timezone"
                )
            elif parsed_timestamp.astimezone(timezone.utc) > datetime.now(timezone.utc):
                errors.append(
                    f"{component}: vulnerability_db_updated_at must not be in the future"
                )
        fallback = image.get("fallback")
        if "fallback" in image and not isinstance(fallback, bool):
            errors.append(f"{component}: fallback must be a boolean")
        if is_minio_image(image) and fallback is not True:
            errors.append(f"{component}: MinIO entries require fallback: true")
        if fallback is True:
            for field in ("cve_risk", "replacement_plan", "expires_on"):
                value = image.get(field)
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"{component}: fallback missing {field}")
            expires_on_value = image.get("expires_on")
            expires_on = expires_on_value.strip() if isinstance(expires_on_value, str) else ""
            if expires_on and not is_future_iso_date(expires_on):
                errors.append(f"{component}: expires_on must be a future YYYY-MM-DD date")

    lab_exceptions: dict[str, set[tuple[str, str, str]]] = {}
    if not errors and exceptions_path is not None:
        try:
            lab_exceptions = load_lab_exceptions(
                exceptions_path,
                repository,
                ledger_references,
            )
        except PolicyError as error:
            errors.append(f"invalid resident image exceptions: {error}")

    if not errors:
        for index, image in enumerate(images):
            component = image.get("component") or f"images[{index}]"
            reference = str(image["reference"])
            for field in ("sbom_artifact", "scan_artifact", "supply_chain_artifact"):
                artifact = str(image[field]).strip()
                try:
                    artifact_path = evidence_artifact_path(
                        manifest_path,
                        repository,
                        artifact,
                        field,
                    )
                    if field == "sbom_artifact":
                        check_sbom(artifact_path)
                    elif field == "scan_artifact":
                        check_trivy(
                            artifact_path,
                            expected_image_reference=reference,
                            allowed_high=lab_exceptions.get(reference),
                        )
                    else:
                        check_supply_chain_evidence(
                            artifact_path,
                            repository=repository,
                            component=str(component),
                            reference=reference,
                            version=str(image["version"]),
                            source=str(image["source"]),
                            sbom_path=evidence_artifact_path(
                                manifest_path,
                                repository,
                                str(image["sbom_artifact"]).strip(),
                                "sbom_artifact",
                            ),
                            scan_path=evidence_artifact_path(
                                manifest_path,
                                repository,
                                str(image["scan_artifact"]).strip(),
                                "scan_artifact",
                            ),
                            sbom_generator=str(image["sbom_generator"]),
                            scanner_version=str(image["scanner_version"]),
                            vulnerability_db_updated_at=str(
                                image["vulnerability_db_updated_at"]
                            ),
                        )
                except PolicyError as error:
                    errors.append(f"{component}: invalid {field} {artifact}: {error}")

    for path, reference in deployed_image_references(repository):
        if reference not in ledger_references:
            errors.append(
                f"{path}: deployed image {reference} is missing from resident image ledger"
            )

    if errors:
        raise PolicyError("resident image policy rejected manifest:\n" + "\n".join(errors))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)

    secrets = commands.add_parser("scan-secrets")
    secrets.add_argument("--repo", type=Path, default=Path.cwd())

    trivy = commands.add_parser("check-trivy")
    trivy.add_argument("--report", type=Path, required=True)

    images = commands.add_parser("check-images")
    images.add_argument("--manifest", type=Path, required=True)
    images.add_argument("--repo", type=Path, default=Path.cwd())
    images.add_argument("--profile", choices=(STRICT_PROFILE, LAB_PROFILE), default=STRICT_PROFILE)
    images.add_argument("--exceptions", type=Path)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "scan-secrets":
            scan_secrets(args.repo.resolve())
        elif args.command == "check-trivy":
            check_trivy(args.report)
        elif args.command == "check-images":
            check_images(
                args.manifest,
                args.repo.resolve(),
                profile=args.profile,
                exceptions_path=args.exceptions,
            )
    except PolicyError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
