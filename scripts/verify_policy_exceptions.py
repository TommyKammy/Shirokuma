#!/usr/bin/env python3
"""Validate repository-owned Kyverno PolicyException metadata fail closed."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIRECTORY = ROOT / "policies/exceptions"
DEFAULT_POLICY_BUNDLE = ROOT / "policies/kyverno/baseline.yaml"
ISSUE_URL = re.compile(r"^https://github\.com/TommyKammy/Shirokuma/issues/[1-9][0-9]*$")
NAME = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")
METADATA_EQUALITY = re.compile(
    r"object\.metadata\.(?P<field>name|namespace|labels\[['\"][a-zA-Z0-9._/-]+['\"]\])"
    r"\s*==\s*['\"][^'\"*\s][^'\"*]*['\"]"
)
REQUIRED_ANNOTATIONS = (
    "shirokuma.dev/exception-owner",
    "shirokuma.dev/exception-reviewer",
    "shirokuma.dev/exception-issue",
    "shirokuma.dev/exception-expires-at",
    "shirokuma.dev/exception-reason",
)


def parse_timestamp(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("timestamp requires an explicit timezone")
    return parsed.astimezone(timezone.utc)


def load_json(path: Path) -> object:
    if path.is_symlink():
        raise ValueError("symbolic links are forbidden")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid JSON: {error}") from error


def load_policy_names(path: Path) -> set[str]:
    if path.is_symlink():
        raise ValueError("policy bundle symbolic links are forbidden")
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"cannot read policy bundle: {error}") from error
    names = set(
        re.findall(
            r"(?m)^kind: ValidatingPolicy\s*$\n^metadata:\s*$\n^  name: ([a-z0-9][-a-z0-9]*)\s*$",
            content,
        )
    )
    if not names:
        raise ValueError("policy bundle contains no ValidatingPolicy resources")
    return names


def is_narrow_metadata_expression(expression: object) -> bool:
    if not isinstance(expression, str):
        return False
    clauses = re.split(r"\s*&&\s*", expression.strip())
    matches = [METADATA_EQUALITY.fullmatch(clause) for clause in clauses]
    if not matches or any(match is None for match in matches):
        return False
    fields = {match.group("field") for match in matches if match is not None}
    return "namespace" in fields and any(field != "namespace" for field in fields)


def validate_exception(
    path: Path, now: datetime, max_days: int, policy_names: set[str]
) -> list[str]:
    errors: list[str] = []
    try:
        document = load_json(path)
    except ValueError as error:
        return [str(error)]
    if not isinstance(document, dict):
        return ["document root must be an object"]
    if document.get("apiVersion") != "policies.kyverno.io/v1":
        errors.append("apiVersion must be policies.kyverno.io/v1")
    if document.get("kind") != "PolicyException":
        errors.append("kind must be PolicyException")

    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        return errors + ["metadata must be an object"]
    resource_name = metadata.get("name")
    if not isinstance(resource_name, str) or not NAME.fullmatch(resource_name):
        errors.append("metadata.name must be a valid deterministic name")
    if metadata.get("namespace") != "policy-exceptions":
        errors.append("metadata.namespace must be policy-exceptions")
    annotations = metadata.get("annotations")
    if not isinstance(annotations, dict):
        return errors + ["metadata.annotations must be an object"]
    for annotation in REQUIRED_ANNOTATIONS:
        value = annotations.get(annotation)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"missing non-empty annotation {annotation}")

    owner = annotations.get("shirokuma.dev/exception-owner")
    reviewer = annotations.get("shirokuma.dev/exception-reviewer")
    if (
        isinstance(owner, str)
        and isinstance(reviewer, str)
        and owner.strip() == reviewer.strip()
    ):
        errors.append("exception owner and reviewer must differ")
    issue = annotations.get("shirokuma.dev/exception-issue")
    if isinstance(issue, str) and not ISSUE_URL.fullmatch(issue):
        errors.append("exception issue must be a Shirokuma GitHub issue URL")
    expires_at = annotations.get("shirokuma.dev/exception-expires-at")
    if isinstance(expires_at, str) and expires_at.strip():
        try:
            expiry = parse_timestamp(expires_at)
            if expiry <= now:
                errors.append("exception is expired")
            if expiry > now + timedelta(days=max_days):
                errors.append(f"exception expiry exceeds {max_days} days")
        except ValueError as error:
            errors.append(f"invalid exception expiry: {error}")

    spec = document.get("spec")
    if not isinstance(spec, dict):
        return errors + ["spec must be an object"]
    policy_refs = spec.get("policyRefs")
    if not isinstance(policy_refs, list) or not policy_refs:
        errors.append("spec.policyRefs must be a non-empty list")
    else:
        for index, policy_ref in enumerate(policy_refs):
            if not isinstance(policy_ref, dict):
                errors.append(f"spec.policyRefs[{index}] must be an object")
                continue
            name = policy_ref.get("name")
            if not isinstance(name, str) or not NAME.fullmatch(name):
                errors.append(f"spec.policyRefs[{index}].name must be an exact policy name")
            elif name not in policy_names:
                errors.append(
                    f"spec.policyRefs[{index}].name must reference a policy in the bundle"
                )
            if policy_ref.get("kind") != "ValidatingPolicy":
                errors.append(f"spec.policyRefs[{index}].kind must be ValidatingPolicy")

    conditions = spec.get("matchConditions")
    if not isinstance(conditions, list) or not conditions:
        errors.append("spec.matchConditions must be a non-empty list")
    else:
        for index, condition in enumerate(conditions):
            if not isinstance(condition, dict):
                errors.append(f"spec.matchConditions[{index}] must be an object")
                continue
            condition_name = condition.get("name")
            if not isinstance(condition_name, str) or not NAME.fullmatch(condition_name):
                errors.append(
                    f"spec.matchConditions[{index}].name must be a valid deterministic name"
                )
            expression = condition.get("expression")
            if not is_narrow_metadata_expression(expression):
                errors.append(
                    f"spec.matchConditions[{index}].expression must narrowly match resource metadata"
                )
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--policy-bundle", type=Path, default=DEFAULT_POLICY_BUNDLE)
    parser.add_argument("--max-days", type=int, default=30)
    parser.add_argument("--now", help="RFC 3339 timestamp used by deterministic tests")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.max_days < 1:
        print("policy-exceptions: max-days must be positive")
        return 1
    try:
        now = parse_timestamp(args.now) if args.now else datetime.now(timezone.utc)
    except ValueError as error:
        print(f"policy-exceptions: invalid --now: {error}")
        return 1
    if not args.directory.is_dir():
        print(f"policy-exceptions: directory does not exist: {args.directory}")
        return 1
    try:
        policy_names = load_policy_names(args.policy_bundle)
    except ValueError as error:
        print(f"policy-exceptions: invalid policy bundle: {error}")
        return 1

    failures = 0
    entries = sorted(args.directory.iterdir())
    unsupported = [
        path for path in entries if path.name != "README.md" and path.suffix != ".json"
    ]
    if unsupported:
        for path in unsupported:
            print(f"policy-exceptions: unsupported entry: {path}")
        return 1
    files = [path for path in entries if path.suffix == ".json"]
    for path in files:
        for error in validate_exception(path, now, args.max_days, policy_names):
            failures += 1
            print(f"policy-exceptions: {path}: {error}")
    if failures:
        return 1
    print(f"policy-exceptions: ok files={len(files)} max_days={args.max_days}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
