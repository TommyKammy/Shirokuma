#!/usr/bin/env python3
"""Validate the single time-boxed Trivy misconfiguration exception."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IGNORE_FILE = ROOT / ".trivyignore.yaml"
APPROVED_IDS = ("KSV-0041", "KSV-0046")
APPROVED_PATH = "deploy/gitops/clusters/local-lite/flux-system/gotk-components.yaml"
APPROVED_STATEMENTS = (
    "Flux v2.9.2 generates cluster-scoped controller RBAC that reads credential, decryption, and provider Secrets for reconciliation; this path-scoped exception is limited to the single-user local lab while the upstream RBAC surface is reviewed.",
    "Flux v2.9.2 generates cluster-scoped controller RBAC for reconciliation; this path-scoped exception is limited to the single-user local lab while the upstream RBAC surface is reviewed.",
)
APPROVED_EXPIRY = date(2026, 8, 14)
MAX_VALIDITY_DAYS = 30


class ContractError(ValueError):
    """Raised when the ignore document exceeds its reviewed contract."""


def canonical_document() -> bytes:
    if len(APPROVED_IDS) != len(APPROVED_STATEMENTS):
        raise ContractError("approved ID and statement constants must align")
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


def load_document(path: Path) -> bytes:
    if path.is_symlink():
        raise ContractError("ignore file must not be a symbolic link")
    try:
        return path.read_bytes()
    except OSError as error:
        raise ContractError(f"cannot read ignore file: {error}") from error


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


def validate_document(document: bytes, now: datetime) -> datetime:
    if document != canonical_document():
        raise ContractError(
            "ignore file bytes differ from the reviewed canonical document"
        )
    expiry = datetime.combine(
        APPROVED_EXPIRY, datetime.min.time(), tzinfo=timezone.utc
    )
    if expiry < now:
        raise ContractError("exception is expired")
    if expiry > now + timedelta(days=MAX_VALIDITY_DAYS):
        raise ContractError(
            f"exception expiry exceeds the {MAX_VALIDITY_DAYS}-day maximum"
        )
    return expiry


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ignore-file", type=Path, default=DEFAULT_IGNORE_FILE)
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
    except ContractError as error:
        print(f"trivyignore: {error}", file=sys.stderr)
        return 1
    print(
        "trivyignore: ok "
        f"ids={','.join(APPROVED_IDS)} path={APPROVED_PATH} "
        f"expires={expiry.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
