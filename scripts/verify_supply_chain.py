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
IMAGE_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
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


class PolicyError(RuntimeError):
    pass


def load_json(path: Path) -> Any:
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


def scan_secrets(repository: Path) -> None:
    findings: list[str] = []
    for relative in tracked_files(repository):
        if SECRET_FILENAME.search(relative):
            findings.append(f"{relative}: secret-like filename")
            continue
        path = repository / relative
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if any(pattern.search(content) for pattern in SECRET_PATTERNS) or any(
            marker in content for marker in SECRET_MARKERS
        ):
            findings.append(f"{relative}: secret-like content")

    if findings:
        raise PolicyError("secret scan rejected tracked files:\n" + "\n".join(findings))


def iter_trivy_findings(report: dict[str, Any]) -> list[dict[str, Any]]:
    results = report.get("Results")
    if results is None:
        return []
    if not isinstance(results, list):
        raise PolicyError("Trivy report Results must be a list")

    findings: list[dict[str, Any]] = []
    for result in results:
        if not isinstance(result, dict):
            raise PolicyError("Trivy report contains a malformed result")
        for category in ("Vulnerabilities", "Misconfigurations", "Secrets"):
            entries = result.get(category) or []
            if not isinstance(entries, list):
                raise PolicyError(f"Trivy report {category} must be a list")
            for entry in entries:
                if not isinstance(entry, dict):
                    raise PolicyError(f"Trivy report {category} contains malformed data")
                findings.append(entry)
    return findings


def check_trivy(report_path: Path) -> None:
    report = load_json(report_path)
    if not isinstance(report, dict):
        raise PolicyError("Trivy report must be a JSON object")
    blocking = [
        finding
        for finding in iter_trivy_findings(report)
        if str(finding.get("Severity", "")).upper() in BLOCKING_SEVERITIES
    ]
    if blocking:
        counts = {
            severity: sum(
                str(finding.get("Severity", "")).upper() == severity
                for finding in blocking
            )
            for severity in sorted(BLOCKING_SEVERITIES)
        }
        summary = ", ".join(f"{severity}={count}" for severity, count in counts.items() if count)
        raise PolicyError(f"Trivy blocking threshold crossed: {summary}")


def check_images(manifest_path: Path) -> None:
    manifest = load_json(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise PolicyError("resident image manifest requires schema_version 1")
    images = manifest.get("images")
    if not isinstance(images, list):
        raise PolicyError("resident image manifest requires an images list")

    errors: list[str] = []
    for index, image in enumerate(images):
        label = f"images[{index}]"
        if not isinstance(image, dict):
            errors.append(f"{label}: entry must be an object")
            continue
        component = str(image.get("component", label))
        reference = str(image.get("reference", ""))
        if not IMAGE_DIGEST.search(reference):
            errors.append(f"{component}: reference requires a sha256 digest")
        if image.get("platform") != "linux/arm64":
            errors.append(f"{component}: platform must be linux/arm64")
        for field in ("version", "source", "sbom_artifact"):
            if not str(image.get(field, "")).strip():
                errors.append(f"{component}: missing {field}")
        if image.get("fallback"):
            for field in ("cve_risk", "replacement_plan", "expires_on"):
                if not str(image.get(field, "")).strip():
                    errors.append(f"{component}: fallback missing {field}")

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
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "scan-secrets":
            scan_secrets(args.repo.resolve())
        elif args.command == "check-trivy":
            check_trivy(args.report)
        elif args.command == "check-images":
            check_images(args.manifest)
    except PolicyError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
