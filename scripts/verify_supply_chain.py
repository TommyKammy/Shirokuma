#!/usr/bin/env python3
"""Deterministic, fail-closed checks for repository supply-chain policy."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


BLOCKING_SEVERITIES = {"HIGH", "CRITICAL"}
SECRET_PATTERNS = (
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b"),
)
# A private key header followed by a base64 body. A bare header string, as used
# by PEM format checks and synthetic test data, is not key material.
PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?" + r"PRIVATE KEY-----"
    r"(?:\\[rn]|\s)*(?:[A-Za-z0-9+/=](?:\\[rn]|\s)*){40,}"
)
SECRET_FILENAME = re.compile(
    r"(^|/)(\.env|[^/]+\.(?:pem|key|p12|pfx|token|secret))$", re.IGNORECASE
)


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
    except FileNotFoundError:
        # Unstaged deletions remain in git ls-files until they are staged.
        return None
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
        if any(pattern.search(content) for pattern in SECRET_PATTERNS) or (
            PRIVATE_KEY_BLOCK.search(content)
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


def high_finding_key(finding: dict[str, Any]) -> tuple[str, str, str, str, str]:
    if finding.get("_category") != "Vulnerabilities":
        raise PolicyError("local-lab exceptions only apply to vulnerability findings")
    identity = tuple(
        str(finding.get(field, "")).strip()
        for field in ("VulnerabilityID", "PkgName", "InstalledVersion")
    )
    if not all(identity):
        raise PolicyError(
            "local-lab High findings require VulnerabilityID, PkgName, and InstalledVersion"
        )
    fixed_version = finding.get("FixedVersion")
    if fixed_version is None:
        fixed_version = ""
    if not isinstance(fixed_version, str):
        raise PolicyError("local-lab High finding FixedVersion must be a string or null")
    return (
        identity[0],
        "HIGH",
        identity[1],
        identity[2],
        fixed_version.strip(),
    )


def format_finding_key(finding: tuple[str, str, str, str, str]) -> str:
    advisory, severity, package, installed_version, fixed_version = finding
    return "/".join(
        (advisory, severity, package, installed_version, fixed_version or "<none>")
    )


def check_trivy(
    report_path: Path,
    expected_image_reference: str | None = None,
    allowed_high: set[tuple[str, str, str, str, str]] | None = None,
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
        observed_high_values = [high_finding_key(finding) for finding in high]
        observed_high = set(observed_high_values)
        if len(observed_high) != len(observed_high_values):
            raise PolicyError("local-lab High findings contain a duplicate exact tuple")
        if observed_high != allowed_high:
            unapproved = sorted(observed_high - allowed_high)
            stale = sorted(allowed_high - observed_high)
            details: list[str] = []
            if unapproved:
                details.append(
                    "unapproved="
                    + ",".join(format_finding_key(finding) for finding in unapproved)
                )
            if stale:
                details.append(
                    "stale="
                    + ",".join(format_finding_key(finding) for finding in stale)
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


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)

    secrets = commands.add_parser("scan-secrets")
    secrets.add_argument("--repo", type=Path, default=Path.cwd())

    trivy = commands.add_parser("check-trivy")
    trivy.add_argument("--report", type=Path, required=True)

    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "scan-secrets":
            scan_secrets(args.repo.resolve())
        elif args.command == "check-trivy":
            check_trivy(args.report)
    except PolicyError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
