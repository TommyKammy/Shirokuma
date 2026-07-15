from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from scripts.verify_gitops_teardown import (
    EXPECTED_RETAINED_RESOURCES,
    ISSUE_URL,
    OBJECT_STORAGE_MANIFEST,
    PURPOSE,
    ROLLBACK,
    TEARDOWN_MARKER,
    GitOpsTeardownError,
    validate_object_storage_gitops_state,
)


NOW = datetime(2026, 7, 16, 0, 0, 0, tzinfo=timezone.utc)


class GitOpsTeardownContractTests(unittest.TestCase):
    @staticmethod
    def marker() -> dict[str, Any]:
        return {
            "schema_version": 1,
            "issue": ISSUE_URL,
            "purpose": PURPOSE,
            "missing_path": OBJECT_STORAGE_MANIFEST.as_posix(),
            "retained_resources": EXPECTED_RETAINED_RESOURCES,
            "valid_from": "2026-07-15T17:00:00Z",
            "expires_at": "2026-07-30T17:00:00Z",
            "rollback": ROLLBACK,
        }

    @staticmethod
    def write_manifest(root: Path) -> None:
        path = root / OBJECT_STORAGE_MANIFEST
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("apiVersion: example.invalid/v1\n", encoding="utf-8")

    @classmethod
    def write_marker(cls, root: Path, marker: dict[str, Any] | None = None) -> None:
        path = root / TEARDOWN_MARKER
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(marker or cls.marker()), encoding="utf-8")

    def assert_rejected(self, mutate: Callable[[Path], None], message: str) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_marker(root)
            mutate(root)
            with self.assertRaisesRegex(GitOpsTeardownError, message):
                validate_object_storage_gitops_state(root, now=NOW)

    def test_normal_profile_requires_manifest_without_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_manifest(root)
            state = validate_object_storage_gitops_state(root, now=NOW)
            self.assertEqual(state.mode, "normal")

    def test_issue_26_marker_admits_only_the_exact_temporary_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_marker(root)
            state = validate_object_storage_gitops_state(root, now=NOW)
            self.assertEqual(state.mode, "issue-26-teardown")
            self.assertEqual(state.missing_path, OBJECT_STORAGE_MANIFEST.as_posix())

    def test_profile_state_is_exclusive_and_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(GitOpsTeardownError, "exactly one state"):
                validate_object_storage_gitops_state(root, now=NOW)
            self.write_manifest(root)
            self.write_marker(root)
            with self.assertRaisesRegex(GitOpsTeardownError, "exactly one state"):
                validate_object_storage_gitops_state(root, now=NOW)

    def test_marker_schema_and_scope_are_closed(self) -> None:
        mutations = {
            "unexpected field": lambda value: value.__setitem__("scope", "all-storage"),
            "non-integer schema": lambda value: value.__setitem__("schema_version", 1.0),
            "wrong issue": lambda value: value.__setitem__("issue", "#27"),
            "broad purpose": lambda value: value.__setitem__("purpose", "teardown"),
            "wrong path": lambda value: value.__setitem__("missing_path", "deploy/gitops"),
            "broad resources": lambda value: value["retained_resources"].append(
                {"apiVersion": "v1", "kind": "Secret", "namespace": "default", "name": "all"}
            ),
            "wrong rollback": lambda value: value.__setitem__("rollback", "restore later"),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                marker = self.marker()
                marker["retained_resources"] = [dict(item) for item in EXPECTED_RETAINED_RESOURCES]
                mutate(marker)
                self.assert_rejected(
                    lambda root, marker=marker: self.write_marker(root, marker),
                    "marker|Issue|purpose|missing_path|retained_resources|rollback",
                )

    def test_marker_requires_canonical_bounded_active_utc_instants(self) -> None:
        cases = (
            ("noncanonical", "valid_from", "2026-07-15T17:00:00+00:00"),
            ("too long", "expires_at", "2026-08-15T17:00:01Z"),
            ("not yet valid", "valid_from", "2026-07-17T00:00:00Z"),
            ("expired", "expires_at", "2026-07-16T00:00:00Z"),
        )
        for label, field, value in cases:
            with self.subTest(label=label):
                marker = self.marker()
                marker[field] = value
                self.assert_rejected(
                    lambda root, marker=marker: self.write_marker(root, marker),
                    "RFC3339|30 days|not yet valid|expired",
                )

    def test_malformed_duplicate_symlink_and_extra_state_are_rejected(self) -> None:
        def malformed(root: Path) -> None:
            (root / TEARDOWN_MARKER).write_text("{", encoding="utf-8")

        self.assert_rejected(malformed, "valid JSON")

        def duplicate(root: Path) -> None:
            raw = json.dumps(self.marker())
            (root / TEARDOWN_MARKER).write_text(
                raw.replace('{"schema_version": 1,', '{"schema_version": 1, "schema_version": 1,'),
                encoding="utf-8",
            )

        self.assert_rejected(duplicate, "duplicate marker field")

        def extra(root: Path) -> None:
            (root / TEARDOWN_MARKER.parent / "all-storage.json").write_text(
                "{}\n", encoding="utf-8"
            )

        self.assert_rejected(extra, "unexpected GitOps teardown state")

        if hasattr(os, "symlink"):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                target = root / "marker.json"
                target.write_text(json.dumps(self.marker()), encoding="utf-8")
                marker = root / TEARDOWN_MARKER
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.symlink_to(target)
                with self.assertRaisesRegex(GitOpsTeardownError, "symbolic links"):
                    validate_object_storage_gitops_state(root, now=NOW)


if __name__ == "__main__":
    unittest.main()
