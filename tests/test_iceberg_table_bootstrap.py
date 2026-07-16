from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from typing import Iterator


ROOT = Path(__file__).resolve().parents[1]
GITOPS_ROOT = ROOT / "deploy/gitops"
RESIDENT_IMAGES = ROOT / "security/resident-images.json"
WORKLOAD_KINDS = {"Deployment", "StatefulSet"}


def _mapping_scalars(document: str) -> Iterator[tuple[tuple[str, ...], str]]:
    stack: list[tuple[int, str]] = []
    for raw_line in document.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue

        match = re.match(r"^(?P<indent>[ ]*)(?P<key>[^:#][^:]*):(?P<value>.*)$", raw_line)
        if match is None:
            continue

        indent = len(match.group("indent"))
        while stack and indent <= stack[-1][0]:
            stack.pop()

        key = match.group("key").strip()
        value = match.group("value").split(" #", maxsplit=1)[0].strip()
        path = tuple(item[1] for item in stack) + (key,)
        if value:
            yield path, value.strip("'\"")
        else:
            stack.append((indent, key))


def _has_polaris_identity(value: str | None) -> bool:
    return value == "polaris" or bool(value and re.fullmatch(r"polaris[-_][a-z0-9_-]+", value))


def _is_polaris_workload(document: str, admitted_images: set[str]) -> bool:
    scalar_items = list(_mapping_scalars(document))
    scalars = dict(scalar_items)
    container_images = {
        value
        for path, value in scalar_items
        if path == ("spec", "template", "spec", "containers", "image")
    }
    return scalars.get(("kind",)) in WORKLOAD_KINDS and any(
        _has_polaris_identity(scalars.get(path))
        for path in (
            ("metadata", "name"),
            ("metadata", "labels", "app.kubernetes.io/name"),
        )
    ) and bool(container_images & admitted_images)


def _admitted_image_references() -> set[str]:
    ledger = json.loads(RESIDENT_IMAGES.read_text(encoding="utf-8"))
    return {entry["reference"] for entry in ledger["images"]}


def _polaris_workload_manifests() -> list[Path]:
    workloads = []
    admitted_images = _admitted_image_references()
    for path in GITOPS_ROOT.rglob("*.yaml"):
        documents = re.split(r"(?m)^---[ \t]*(?:#.*)?$", path.read_text(encoding="utf-8"))
        if any(_is_polaris_workload(document, admitted_images) for document in documents):
            workloads.append(path.relative_to(ROOT))
    return workloads


class PolarisWorkloadDetectionTests(unittest.TestCase):
    def test_accepts_exact_polaris_workload_names(self) -> None:
        image = "registry.example/polaris@sha256:" + "a" * 64
        for kind, identity in (
            ("Deployment", "  name: polaris"),
            ("StatefulSet", "  labels:\n    app.kubernetes.io/name: polaris"),
        ):
            with self.subTest(kind=kind, identity=identity):
                manifest = (
                    f"apiVersion: apps/v1\nkind: {kind}\nmetadata:\n{identity}\n"
                    "spec:\n"
                    "  template:\n"
                    "    spec:\n"
                    "      containers:\n"
                    "        - name: polaris\n"
                    f"          image: {image}\n"
                )
                self.assertTrue(_is_polaris_workload(manifest, {image}))

    def test_rejects_non_workload_polaris_resources(self) -> None:
        image = "registry.example/polaris@sha256:" + "a" * 64
        for kind in ("ConfigMap", "Service", "Kustomization"):
            with self.subTest(kind=kind):
                manifest = (
                    f"apiVersion: v1\nkind: {kind}\nmetadata:\n"
                    "  name: polaris-config\n"
                    "  labels:\n"
                    "    app.kubernetes.io/name: polaris\n"
                )
                self.assertFalse(_is_polaris_workload(manifest, {image}))

    def test_rejects_workload_without_admitted_image(self) -> None:
        manifest = (
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n"
            "  name: polaris\n"
            "spec:\n"
            "  template:\n"
            "    spec:\n"
            "      containers:\n"
            "        - name: polaris\n"
            "          image: registry.example/polaris:latest\n"
        )
        self.assertFalse(_is_polaris_workload(manifest, set()))


class IcebergTableBootstrapPrerequisiteTests(unittest.TestCase):
    def test_missing_polaris_workload_keeps_bootstrap_blocked(self) -> None:
        self.assertEqual(
            [],
            _polaris_workload_manifests(),
            "Replace this blocker regression with the Iceberg bootstrap checks once "
            "an approved Polaris Deployment or StatefulSet is materialized through "
            "deploy/gitops",
        )


if __name__ == "__main__":
    unittest.main()
