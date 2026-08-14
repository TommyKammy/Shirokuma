#!/usr/bin/env python3
"""Validate the exact, owner-approved Trivy misconfiguration exceptions."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IGNORE_FILE = ROOT / ".trivyignore.yaml"
DEFAULT_MANIFEST_FILE = (
    ROOT / "deploy/gitops/clusters/local-lite/flux-system/gotk-components.yaml"
)
DEFAULT_SCAN_REPORT_FILE = (
    ROOT
    / "security/evidence/flux-v2.9.2/gotk-components-v2.9.2.trivy-config.json"
)
DEFAULT_TRIVY_METADATA_FILE = (
    ROOT
    / "security/evidence/flux-v2.9.2/gotk-components-v2.9.2.trivy-version.json"
)
APPROVED_IDS = ("KSV-0041", "KSV-0046")
APPROVED_PATH = "deploy/gitops/clusters/local-lite/flux-system/gotk-components.yaml"
APPROVED_STATEMENTS = (
    "Flux v2.9.2 generates cluster-scoped controller RBAC that reads credential, decryption, and provider Secrets for reconciliation; this path-scoped exception is limited to the single-user local lab while the upstream RBAC surface is reviewed.",
    "Flux v2.9.2 generates cluster-scoped controller RBAC for reconciliation; this path-scoped exception is limited to the single-user local lab while the upstream RBAC surface is reviewed.",
)
APPROVAL_COMMENT_ID = 5290345820
APPROVAL_COMMENT_URL = (
    "https://github.com/TommyKammy/Shirokuma/issues/150"
    "#issuecomment-5290345820"
)
APPROVAL_CREATED_AT = datetime(2026, 8, 14, 6, 43, 14, tzinfo=timezone.utc)
APPROVED_ON = date(2026, 8, 14)
APPROVED_EXPIRY = date(2026, 9, 13)
APPROVED_MANIFEST_SHA256 = (
    "ed307189fd1f9e49819a50843bb6f3c9257fe6d4d8359d1950b38207c26c3854"
)
APPROVED_FINDINGS = (
    (
        "KSV-0041",
        "CRITICAL",
        "Manage secrets",
        "FAIL",
        "builtin.kubernetes.KSV041",
        "data.builtin.kubernetes.KSV041.deny",
        1,
    ),
    (
        "KSV-0046",
        "CRITICAL",
        "Manage all resources",
        "FAIL",
        "builtin.kubernetes.KSV046",
        "data.builtin.kubernetes.KSV046.deny",
        8,
    ),
)
TRIVY_VERSION = "0.72.0"
TRIVY_CHECK_BUNDLE_DIGEST = (
    "sha256:1583562f8b90ed2a071b99f0e5ffff6b57e4ceb6ca3e4796577b4e6a339eb74c"
)
APPROVED_SCAN_REPORT_PATH = (
    "security/evidence/flux-v2.9.2/gotk-components-v2.9.2.trivy-config.json"
)
APPROVED_TRIVY_METADATA_PATH = (
    "security/evidence/flux-v2.9.2/"
    "gotk-components-v2.9.2.trivy-version.json"
)
APPROVED_SCAN_REPORT_SHA256 = (
    "00e87fef815ac9a99401f2a450e71c47555fd991ec6d2cfc7313e1f0dbe3bd7a"
)
APPROVED_TRIVY_METADATA_SHA256 = (
    "a82d05e076fd54c9bd2e57fd1be00891a2384a3f618e9d72037bfd940a5406ea"
)
APPROVED_SCAN_CREATED_AT = "2026-08-14T15:26:22.35115+09:00"
MAX_VALIDITY_DAYS = 30


class ContractError(ValueError):
    """Raised when the ignore document exceeds its reviewed contract."""


def canonical_document() -> bytes:
    if len(APPROVED_IDS) != len(APPROVED_STATEMENTS):
        raise ContractError("approved ID and statement constants must align")
    if tuple(finding[0] for finding in APPROVED_FINDINGS) != APPROVED_IDS:
        raise ContractError("approved ID and finding constants must align")
    lines = ["misconfigurations:"]
    for exception_id, statement in zip(APPROVED_IDS, APPROVED_STATEMENTS):
        lines.extend(
            (
                f"  - id: {exception_id}",
                "    paths:",
                f"      - {APPROVED_PATH}",
                f'    statement: "{statement}"',
                f"    expired_at: {APPROVED_EXPIRY.isoformat()}",
            )
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


def load_regular_file(path: Path, label: str) -> bytes:
    if path.is_symlink():
        raise ContractError(f"{label} must not be a symbolic link")
    try:
        return path.read_bytes()
    except OSError as error:
        raise ContractError(f"cannot read {label}: {error}") from error


def load_document(path: Path) -> bytes:
    return load_regular_file(path, "ignore file")


def parse_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ContractError(f"{field} must be a canonical RFC 3339 UTC timestamp")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as error:
        raise ContractError(
            f"{field} must be a canonical RFC 3339 UTC timestamp"
        ) from error
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ContractError(f"{field} must be a canonical RFC 3339 UTC timestamp")
    return parsed


def parse_offset_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ContractError(f"{field} must be an RFC 3339 timestamp")
    match = re.fullmatch(
        r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})",
        value,
    )
    if match is None:
        raise ContractError(f"{field} must be an RFC 3339 timestamp")
    fraction = (match.group(2) or "0")[:6].ljust(6, "0")
    offset = "+00:00" if match.group(3) == "Z" else match.group(3)
    try:
        return datetime.fromisoformat(f"{match.group(1)}.{fraction}{offset}")
    except ValueError as error:
        raise ContractError(f"{field} must be an RFC 3339 timestamp") from error


def validate_authorization_window(
    approved_on: date,
    approval_created_at: datetime,
    expiry_date: date,
) -> datetime:
    if approval_created_at.tzinfo != timezone.utc:
        raise ContractError("owner approval time must use UTC")
    if approval_created_at.date() != approved_on:
        raise ContractError("owner approval time and approved_on date differ")
    approval_date_start = datetime.combine(
        approved_on, datetime.min.time(), tzinfo=timezone.utc
    )
    expiry = datetime.combine(expiry_date, datetime.min.time(), tzinfo=timezone.utc)
    if expiry <= approval_date_start:
        raise ContractError("exception expiry must be after approved_on")
    if expiry > approval_date_start + timedelta(days=MAX_VALIDITY_DAYS):
        raise ContractError(
            f"exception expiry exceeds the {MAX_VALIDITY_DAYS}-day maximum"
        )
    return expiry


def validate_manifest(manifest: bytes) -> str:
    digest = hashlib.sha256(manifest).hexdigest()
    if digest != APPROVED_MANIFEST_SHA256:
        raise ContractError(
            "Flux manifest SHA-256 differs from the owner-approved RBAC identity"
        )
    return digest


def expected_trivy_metadata() -> dict[str, Any]:
    return {
        "Version": TRIVY_VERSION,
        "VulnerabilityDB": {
            "Version": 2,
            "NextUpdate": "2026-08-15T01:10:44.597550041Z",
            "UpdatedAt": "2026-08-14T01:10:44.597550261Z",
            "DownloadedAt": "2026-08-14T05:41:43.632299Z",
        },
        "JavaDB": {
            "Version": 1,
            "NextUpdate": "2026-08-02T01:21:17.429288431Z",
            "UpdatedAt": "2026-07-30T01:21:17.429288551Z",
            "DownloadedAt": "2026-07-30T06:17:06.469173Z",
        },
        "CheckBundle": {
            "Digest": TRIVY_CHECK_BUNDLE_DIGEST,
            "DownloadedAt": "2026-08-14T05:44:25.140738Z",
        },
    }


def parse_json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError(f"{label} must be valid UTF-8 JSON: {error}") from error
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be a JSON object")
    return value


def validate_scan_evidence(
    report_raw: bytes,
    trivy_metadata_raw: bytes,
    *,
    expected_report_sha256: str = APPROVED_SCAN_REPORT_SHA256,
    expected_trivy_metadata_sha256: str = APPROVED_TRIVY_METADATA_SHA256,
) -> str:
    report_digest = hashlib.sha256(report_raw).hexdigest()
    if report_digest != expected_report_sha256:
        raise ContractError(
            "retained Trivy config report SHA-256 differs from the reviewed evidence"
        )

    metadata_digest = hashlib.sha256(trivy_metadata_raw).hexdigest()
    if metadata_digest != expected_trivy_metadata_sha256:
        raise ContractError(
            "retained Trivy metadata SHA-256 differs from the reviewed evidence"
        )
    metadata = parse_json_object(trivy_metadata_raw, "retained Trivy metadata")
    if metadata != expected_trivy_metadata():
        raise ContractError("retained Trivy metadata content changed")

    report = parse_json_object(report_raw, "retained Trivy config report")
    if set(report) != {
        "SchemaVersion",
        "CreatedAt",
        "ArtifactName",
        "ArtifactType",
        "Results",
        "ReportID",
        "Trivy",
    }:
        raise ContractError("retained Trivy config report top-level fields changed")
    if report.get("SchemaVersion") != 2:
        raise ContractError("retained Trivy config report schema must be 2")
    if report.get("ArtifactName") != APPROVED_PATH:
        raise ContractError("retained Trivy config report path changed")
    if report.get("ArtifactType") != "filesystem":
        raise ContractError("retained Trivy config report artifact type changed")
    if report.get("Trivy") != {"Version": TRIVY_VERSION}:
        raise ContractError("retained Trivy config report scanner version changed")
    if report.get("CreatedAt") != APPROVED_SCAN_CREATED_AT:
        raise ContractError("retained Trivy config report creation time changed")
    created_at = parse_offset_timestamp(
        APPROVED_SCAN_CREATED_AT, "reviewed Trivy config creation time"
    )
    if created_at.tzinfo is None:
        raise ContractError("retained Trivy config creation time requires a timezone")
    try:
        bundle_downloaded_at = parse_offset_timestamp(
            metadata["CheckBundle"]["DownloadedAt"],
            "retained Trivy check-bundle download time",
        )
        vulnerability_db_updated_at = parse_offset_timestamp(
            metadata["VulnerabilityDB"]["UpdatedAt"],
            "retained Trivy vulnerability DB update time",
        )
    except (KeyError, TypeError, ContractError) as error:
        raise ContractError("retained Trivy metadata timestamps are invalid") from error
    if (
        bundle_downloaded_at > created_at.astimezone(timezone.utc)
        or vulnerability_db_updated_at > created_at.astimezone(timezone.utc)
    ):
        raise ContractError("retained Trivy metadata postdates the config scan")

    results = report.get("Results")
    if not isinstance(results, list) or len(results) != 1:
        raise ContractError("retained Trivy config report must contain one result")
    result = results[0]
    if not isinstance(result, dict):
        raise ContractError("retained Trivy config result must be an object")
    if (
        result.get("Target") != "gotk-components.yaml"
        or result.get("Class") != "config"
        or result.get("Type") != "kubernetes"
    ):
        raise ContractError("retained Trivy config result identity changed")
    if result.get("MisconfSummary") != {"Successes": 13, "Failures": 9}:
        raise ContractError("retained Trivy config summary changed")
    findings = result.get("Misconfigurations")
    if not isinstance(findings, list):
        raise ContractError("retained Trivy config findings must be a list")

    observed: Counter[tuple[str, str, str, str, str, str]] = Counter()
    for finding in findings:
        if not isinstance(finding, dict):
            raise ContractError("retained Trivy config finding must be an object")
        values = tuple(
            finding.get(field)
            for field in ("ID", "Severity", "Title", "Status", "Namespace", "Query")
        )
        if not all(isinstance(value, str) and value for value in values):
            raise ContractError("retained Trivy config finding identity is incomplete")
        observed[values] += 1

    expected = Counter(
        {
            (finding_id, severity, title, status, namespace, query): count
            for (
                finding_id,
                severity,
                title,
                status,
                namespace,
                query,
                count,
            ) in APPROVED_FINDINGS
        }
    )
    if observed != expected:
        raise ContractError("retained Trivy config finding set changed")
    return report_digest


def validate_document(document: bytes, now: datetime) -> datetime:
    if document != canonical_document():
        raise ContractError(
            "ignore file bytes differ from the reviewed canonical document"
        )
    expiry = validate_authorization_window(
        APPROVED_ON,
        APPROVAL_CREATED_AT,
        APPROVED_EXPIRY,
    )
    if now < APPROVAL_CREATED_AT:
        raise ContractError("owner approval is not yet effective")
    if expiry <= now:
        raise ContractError("exception is expired")
    return expiry


def format_approved_findings() -> str:
    return ",".join(
        f"{finding_id}:{severity}:{count}"
        for (
            finding_id,
            severity,
            _title,
            _status,
            _namespace,
            _query,
            count,
        ) in APPROVED_FINDINGS
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ignore-file", type=Path, default=DEFAULT_IGNORE_FILE)
    parser.add_argument("--manifest-file", type=Path, default=DEFAULT_MANIFEST_FILE)
    parser.add_argument(
        "--scan-report-file", type=Path, default=DEFAULT_SCAN_REPORT_FILE
    )
    parser.add_argument(
        "--trivy-metadata-file", type=Path, default=DEFAULT_TRIVY_METADATA_FILE
    )
    parser.add_argument(
        "--now",
        help="canonical RFC 3339 UTC timestamp used only by deterministic tests",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        now = (
            parse_timestamp(args.now, "--now")
            if args.now is not None
            else datetime.now(timezone.utc)
        )
        expiry = validate_document(load_document(args.ignore_file), now)
        manifest_digest = validate_manifest(
            load_regular_file(args.manifest_file, "Flux manifest")
        )
        scan_digest = validate_scan_evidence(
            load_regular_file(args.scan_report_file, "Trivy config report"),
            load_regular_file(args.trivy_metadata_file, "Trivy metadata"),
        )
    except ContractError as error:
        print(f"trivyignore: {error}", file=sys.stderr)
        return 1
    print(
        "trivyignore: ok "
        f"ids={','.join(APPROVED_IDS)} path={APPROVED_PATH} "
        f"approved_findings={format_approved_findings()} "
        f"manifest_sha256={manifest_digest} trivy={TRIVY_VERSION} "
        f"check_bundle={TRIVY_CHECK_BUNDLE_DIGEST} "
        f"scan_sha256={scan_digest} "
        f"trivy_metadata_sha256={APPROVED_TRIVY_METADATA_SHA256} "
        f"approval_comment={APPROVAL_COMMENT_ID} "
        f"approved_at={APPROVAL_CREATED_AT.strftime('%Y-%m-%dT%H:%M:%SZ')} "
        f"expires={expiry.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
