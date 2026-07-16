from __future__ import annotations

import json
import re
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Iterator


ROOT = Path(__file__).resolve().parents[1]
DEPLOY_ROOT = ROOT / "deploy"
CHARTS_ROOT = ROOT / "charts"
RESIDENT_IMAGES = ROOT / "security/resident-images.json"
DEPLOYMENT_SUFFIXES = {".json", ".yaml", ".yml"}
WORKLOAD_KINDS = {"Deployment", "StatefulSet"}
POLARIS_COMPONENT = "polaris"


def _mapping_scalars(document: str) -> Iterator[tuple[tuple[str, ...], str]]:
    stack: list[tuple[int, str]] = []
    for raw_line in document.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue

        match = re.match(r"^(?P<indent>[ ]*)(?P<key>[^:#][^:]*):(?P<value>.*)$", raw_line)
        if match is None:
            continue

        indent = len(match.group("indent"))
        raw_key = match.group("key").strip()
        is_sequence_item = raw_key.startswith("- ")
        while stack and (
            indent < stack[-1][0]
            or (indent == stack[-1][0] and not is_sequence_item)
        ):
            stack.pop()

        key = raw_key.removeprefix("- ").strip().strip("'\"")
        value = match.group("value").split(" #", maxsplit=1)[0].strip()
        path = tuple(item[1] for item in stack) + (key,)
        if value:
            yield path, value.strip("'\"")
        else:
            stack.append((indent, key))


def _json_scalars(
    value: object, path: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], str]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield from _json_scalars(item, path + (str(key),))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            yield from _json_scalars(item, path)
    elif value is not None:
        yield path, str(value)


def _document_scalars(document: str) -> list[tuple[tuple[str, ...], str]]:
    try:
        parsed = json.loads(document)
    except json.JSONDecodeError:
        return list(_mapping_scalars(document))
    return list(_json_scalars(parsed))


def _has_polaris_identity(value: str | None) -> bool:
    return value == "polaris" or bool(value and re.fullmatch(r"polaris[-_][a-z0-9_-]+", value))


def _is_polaris_workload(document: str, admitted_images: set[str]) -> bool:
    scalar_items = _document_scalars(document)
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


def _component_image_references(
    ledger: Mapping[str, Sequence[Mapping[str, str]]], component: str
) -> set[str]:
    return {
        entry["reference"]
        for entry in ledger["images"]
        if entry.get("component") == component
    }


def _admitted_polaris_image_references() -> set[str]:
    ledger = json.loads(RESIDENT_IMAGES.read_text(encoding="utf-8"))
    return _component_image_references(ledger, POLARIS_COMPONENT)


def _deployment_manifest_paths(
    deploy_root: Path = DEPLOY_ROOT, charts_root: Path = CHARTS_ROOT
) -> Iterator[Path]:
    for path in deploy_root.rglob("*"):
        if path.is_file() and path.suffix in DEPLOYMENT_SUFFIXES:
            yield path

    for path in charts_root.rglob("*"):
        relative_parts = path.relative_to(charts_root).parts
        if (
            path.is_file()
            and path.suffix in DEPLOYMENT_SUFFIXES
            and "templates" in relative_parts
        ):
            yield path


def _display_path(path: Path) -> Path:
    try:
        return path.relative_to(ROOT)
    except ValueError:
        return path


def _polaris_workload_manifests(
    deploy_root: Path = DEPLOY_ROOT,
    charts_root: Path = CHARTS_ROOT,
    admitted_images: set[str] | None = None,
) -> list[Path]:
    workloads = []
    if admitted_images is None:
        admitted_images = _admitted_polaris_image_references()
    for path in _deployment_manifest_paths(deploy_root, charts_root):
        documents = re.split(r"(?m)^---[ \t]*(?:#.*)?$", path.read_text(encoding="utf-8"))
        if any(_is_polaris_workload(document, admitted_images) for document in documents):
            workloads.append(_display_path(path))
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

    def test_accepts_image_as_first_container_field(self) -> None:
        image = "registry.example/polaris@sha256:" + "a" * 64
        manifest = (
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n"
            "  name: polaris\n"
            "spec:\n"
            "  template:\n"
            "    spec:\n"
            "      containers:\n"
            f"        - image: {image}\n"
            "          name: polaris\n"
        )
        self.assertTrue(_is_polaris_workload(manifest, {image}))

    def test_accepts_quoted_image_keys(self) -> None:
        image = "registry.example/polaris@sha256:" + "a" * 64
        for quoted_key in ('"image"', "'image'"):
            with self.subTest(quoted_key=quoted_key):
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
                    f"          {quoted_key}: {image}\n"
                )
                self.assertTrue(_is_polaris_workload(manifest, {image}))

    def test_accepts_indentless_container_sequence(self) -> None:
        image = "registry.example/polaris@sha256:" + "a" * 64
        manifest = (
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n"
            "  name: polaris\n"
            "spec:\n"
            "  template:\n"
            "    spec:\n"
            "      containers:\n"
            f"      - image: {image}\n"
            "        name: polaris\n"
        )
        self.assertTrue(_is_polaris_workload(manifest, {image}))

    def test_scans_deploy_and_helm_template_manifest_suffixes(self) -> None:
        image = "registry.example/polaris@sha256:" + "a" * 64
        yaml_manifest = (
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n"
            "  name: polaris\n"
            "spec:\n"
            "  template:\n"
            "    spec:\n"
            "      containers:\n"
            f"      - 'image': {image}\n"
        )
        json_manifest = json.dumps(
            {
                "apiVersion": "apps/v1",
                "kind": "StatefulSet",
                "metadata": {"name": "polaris"},
                "spec": {
                    "template": {
                        "spec": {"containers": [{"image": image}]}
                    }
                },
            }
        )

        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            deploy_root = fixture_root / "deploy"
            charts_root = fixture_root / "charts"
            deploy_manifest = deploy_root / "polaris.json"
            chart_manifest = charts_root / "polaris" / "templates" / "deployment.yml"
            ignored_chart_file = charts_root / "polaris" / "values.yaml"
            for path, content in (
                (deploy_manifest, json_manifest),
                (chart_manifest, yaml_manifest),
                (ignored_chart_file, yaml_manifest),
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

            workloads = _polaris_workload_manifests(
                deploy_root, charts_root, admitted_images={image}
            )

        self.assertEqual({deploy_manifest, chart_manifest}, set(workloads))

    def test_admits_only_polaris_component_images(self) -> None:
        polaris_image = "registry.example/polaris@sha256:" + "a" * 64
        seaweedfs_image = "registry.example/seaweedfs@sha256:" + "b" * 64
        ledger = {
            "images": (
                {"component": "seaweedfs", "reference": seaweedfs_image},
                {"component": POLARIS_COMPONENT, "reference": polaris_image},
            )
        }

        self.assertEqual(
            {polaris_image},
            _component_image_references(ledger, POLARIS_COMPONENT),
        )

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
            "deploy or a Helm chart template",
        )


if __name__ == "__main__":
    unittest.main()
