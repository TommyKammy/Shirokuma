#!/usr/bin/env python3
"""Deterministic, fail-closed checks for repository supply-chain policy."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


BLOCKING_SEVERITIES = {"HIGH", "CRITICAL"}
IMAGE_DIGEST = re.compile(r"^(?P<repository>[^@\s]+)@sha256:[0-9a-f]{64}$")
DEPLOYMENT_SUFFIXES = {".json", ".yaml", ".yml"}
YAML_IMAGE_FIELD = re.compile(r"^\s*(?:-\s*)?image\s*:\s*(?P<value>.*?)\s*$")
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


def is_immutable_image_reference(reference: str) -> bool:
    match = IMAGE_DIGEST.fullmatch(reference)
    if match is None:
        return False
    return ":" not in match.group("repository").rsplit("/", 1)[-1]


def parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
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
        if path.suffix == ".json":
            references.extend(json_image_references(load_json(absolute), relative))
            continue
        try:
            lines = absolute.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as error:
            raise PolicyError(f"cannot read deployment manifest {relative}: {error}") from error
        for line_number, line in enumerate(lines, start=1):
            match = YAML_IMAGE_FIELD.match(line)
            if match is None:
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


def check_images(manifest_path: Path, repository: Path) -> None:
    manifest = load_json(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise PolicyError("resident image manifest requires schema_version 1")
    images = manifest.get("images")
    if not isinstance(images, list):
        raise PolicyError("resident image manifest requires an images list")

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
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "scan-secrets":
            scan_secrets(args.repo.resolve())
        elif args.command == "check-trivy":
            check_trivy(args.report)
        elif args.command == "check-images":
            check_images(args.manifest, args.repo.resolve())
    except PolicyError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
