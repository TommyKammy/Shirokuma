#!/usr/bin/env python3
"""Validate the closed-world Issue #26 object-storage GitOps profile state."""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


OBJECT_STORAGE_MANIFEST = Path(
    "deploy/gitops/clusters/local-lite/object-storage.yaml"
)
MARKER_DIRECTORY = Path("security/gitops-teardown")
TEARDOWN_MARKER = MARKER_DIRECTORY / "issue-26-object-storage.json"
ISSUE_URL = "https://github.com/TommyKammy/Shirokuma/issues/26"
PURPOSE = "acceptance-only-non-destructive-object-storage-teardown"
ROLLBACK = (
    "Restore deploy/gitops/clusters/local-lite/object-storage.yaml in a reviewed "
    "PR and reconcile Flux before this marker expires."
)
EXPECTED_RETAINED_RESOURCES = [
    {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "namespace": "shirokuma-storage",
        "name": "seaweedfs-data-seaweedfs-0",
    },
    {
        "apiVersion": "v1",
        "kind": "Secret",
        "namespace": "shirokuma-storage",
        "name": "seaweedfs-s3-credentials",
    },
    {
        "apiVersion": "v1",
        "kind": "Secret",
        "namespace": "shirokuma-dev",
        "name": "seaweedfs-s3-application-credentials",
    },
]
EXPECTED_MARKER_FIELDS = {
    "schema_version",
    "issue",
    "purpose",
    "missing_path",
    "retained_resources",
    "valid_from",
    "expires_at",
    "rollback",
}
RFC3339_UTC = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
MAX_VALIDITY = timedelta(days=30)


class GitOpsTeardownError(ValueError):
    """The repository is not in an admitted object-storage GitOps state."""


@dataclass(frozen=True)
class GitOpsProfileState:
    mode: str
    manifest_path: Path
    marker_path: Path
    missing_path: str | None = None


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _reject_symlink_path(root: Path, path: Path) -> None:
    root = root.absolute()
    path = path.absolute()
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise GitOpsTeardownError(f"path escapes repository: {path}") from error
    current = root
    if current.is_symlink():
        raise GitOpsTeardownError("repository root must not be a symbolic link")
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise GitOpsTeardownError(
                f"symbolic links are forbidden in GitOps profile state: {current.relative_to(root)}"
            )


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GitOpsTeardownError(f"duplicate marker field: {key}")
        result[key] = value
    return result


def _load_marker(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise GitOpsTeardownError(f"cannot read teardown marker: {error}") from error
    try:
        marker = json.loads(raw, object_pairs_hook=_object_without_duplicates)
    except GitOpsTeardownError:
        raise
    except json.JSONDecodeError as error:
        raise GitOpsTeardownError(f"teardown marker is not valid JSON: {error}") from error
    if not isinstance(marker, dict):
        raise GitOpsTeardownError("teardown marker must be a JSON object")
    return marker


def _parse_rfc3339_utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or RFC3339_UTC.fullmatch(value) is None:
        raise GitOpsTeardownError(
            f"{field} must be canonical RFC3339 UTC with whole seconds"
        )
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as error:
        raise GitOpsTeardownError(f"{field} is not a valid UTC instant") from error
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise GitOpsTeardownError(f"{field} is not canonical RFC3339 UTC")
    return parsed


def _validate_marker(marker: dict[str, Any], now: datetime) -> None:
    if set(marker) != EXPECTED_MARKER_FIELDS:
        unexpected = sorted(set(marker) - EXPECTED_MARKER_FIELDS)
        missing = sorted(EXPECTED_MARKER_FIELDS - set(marker))
        raise GitOpsTeardownError(
            f"teardown marker fields are not closed: missing={missing}, unexpected={unexpected}"
        )
    if type(marker["schema_version"]) is not int or marker["schema_version"] != 1:
        raise GitOpsTeardownError("teardown marker requires schema_version 1")
    if marker["issue"] != ISSUE_URL:
        raise GitOpsTeardownError("teardown marker must be tied to Issue #26")
    if marker["purpose"] != PURPOSE:
        raise GitOpsTeardownError("teardown marker purpose is broader than the acceptance scope")
    if marker["missing_path"] != OBJECT_STORAGE_MANIFEST.as_posix():
        raise GitOpsTeardownError("teardown marker missing_path is not the exact child manifest")
    if marker["retained_resources"] != EXPECTED_RETAINED_RESOURCES:
        raise GitOpsTeardownError("teardown marker retained_resources do not match the exact contract")
    if marker["rollback"] != ROLLBACK:
        raise GitOpsTeardownError("teardown marker rollback is not the exact reviewed-PR contract")

    valid_from = _parse_rfc3339_utc(marker["valid_from"], "valid_from")
    expires_at = _parse_rfc3339_utc(marker["expires_at"], "expires_at")
    if expires_at <= valid_from or expires_at - valid_from > MAX_VALIDITY:
        raise GitOpsTeardownError("teardown marker validity must be positive and no more than 30 days")
    if now.tzinfo is None or now.utcoffset() is None:
        raise GitOpsTeardownError("validator clock must be timezone-aware")
    now_utc = now.astimezone(timezone.utc)
    if now_utc < valid_from:
        raise GitOpsTeardownError("teardown marker is not yet valid")
    if now_utc >= expires_at:
        raise GitOpsTeardownError("teardown marker has expired")


def validate_object_storage_gitops_state(
    root: Path,
    *,
    now: datetime | None = None,
) -> GitOpsProfileState:
    root = root.absolute()
    manifest = root / OBJECT_STORAGE_MANIFEST
    marker_directory = root / MARKER_DIRECTORY
    marker = root / TEARDOWN_MARKER

    _reject_symlink_path(root, manifest)
    if _lexists(marker_directory):
        _reject_symlink_path(root, marker_directory)
        if not marker_directory.is_dir():
            raise GitOpsTeardownError(f"{MARKER_DIRECTORY} must be a directory")
        entries = sorted(path.name for path in marker_directory.iterdir())
        unexpected = [name for name in entries if name != TEARDOWN_MARKER.name]
        if unexpected:
            raise GitOpsTeardownError(
                f"unexpected GitOps teardown state entries: {unexpected}"
            )
    _reject_symlink_path(root, marker)

    manifest_present = _lexists(manifest)
    marker_present = _lexists(marker)
    if manifest_present and not manifest.is_file():
        raise GitOpsTeardownError(f"{OBJECT_STORAGE_MANIFEST} must be a regular file")
    if marker_present and not marker.is_file():
        raise GitOpsTeardownError(f"{TEARDOWN_MARKER} must be a regular file")
    if manifest_present == marker_present:
        raise GitOpsTeardownError(
            "exactly one state is required: normal object-storage manifest or Issue #26 teardown marker"
        )
    if manifest_present:
        return GitOpsProfileState("normal", manifest, marker)

    marker_data = _load_marker(marker)
    _validate_marker(marker_data, now or datetime.now(timezone.utc))
    return GitOpsProfileState(
        "issue-26-teardown",
        manifest,
        marker,
        OBJECT_STORAGE_MANIFEST.as_posix(),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        state = validate_object_storage_gitops_state(args.root)
    except GitOpsTeardownError as error:
        parser.error(str(error))
    print(f"object-storage GitOps state: {state.mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
