from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from test_iceberg_table_bootstrap import (
    CHARTS_ROOT,
    DEPLOY_ROOT,
    RESIDENT_IMAGES,
    WORKLOAD_KINDS,
    _admitted_polaris_image_references,
    _component_image_references,
    _deployment_manifest_paths,
    _display_path,
    _document_scalars,
    _polaris_workload_manifests,
)

TRINO_COMPONENT = "trino"
POSTGRESQL_COMPONENT = "postgresql"
TRINO_ARTIFACT_KINDS = WORKLOAD_KINDS | {"HelmRelease"}


def _has_trino_identity(value: str | None) -> bool:
    return value == "trino" or bool(
        value and re.fullmatch(r"trino[-_][a-z0-9_-]+", value)
    )


def _is_trino_workload(document: str) -> bool:
    scalars = dict(_document_scalars(document))
    return scalars.get(("kind",)) in TRINO_ARTIFACT_KINDS and any(
        _has_trino_identity(scalars.get(path))
        for path in (
            ("metadata", "name"),
            ("metadata", "labels", "app.kubernetes.io/name"),
        )
    )


def _trino_workload_manifests(
    deploy_root: Path = DEPLOY_ROOT, charts_root: Path = CHARTS_ROOT
) -> list[Path]:
    workloads = []
    for path in _deployment_manifest_paths(deploy_root, charts_root):
        documents = re.split(
            r"(?m)^---[ \t]*(?:#.*)?$", path.read_text(encoding="utf-8")
        )
        if any(_is_trino_workload(document) for document in documents):
            workloads.append(_display_path(path))
    return workloads


def _admitted_trino_image_references() -> set[str]:
    ledger = json.loads(RESIDENT_IMAGES.read_text(encoding="utf-8"))
    return _component_image_references(ledger, TRINO_COMPONENT)


def _admitted_postgresql_image_references() -> set[str]:
    ledger = json.loads(RESIDENT_IMAGES.read_text(encoding="utf-8"))
    return _component_image_references(ledger, POSTGRESQL_COMPONENT)


def _has_postgresql_identity(value: str | None) -> bool:
    return bool(
        value
        and re.fullmatch(r"(?:postgres|postgresql)(?:[-_][a-z0-9_-]+)?", value)
    )


def _is_postgresql_workload(document: str, admitted_images: set[str]) -> bool:
    scalar_items = _document_scalars(document)
    scalars = dict(scalar_items)
    container_images = {
        value
        for path, value in scalar_items
        if path == ("spec", "template", "spec", "containers", "image")
    }
    return scalars.get(("kind",)) in WORKLOAD_KINDS and any(
        _has_postgresql_identity(scalars.get(path))
        for path in (
            ("metadata", "name"),
            ("metadata", "labels", "app.kubernetes.io/name"),
        )
    ) and bool(container_images & admitted_images)


def _postgresql_workload_manifests(
    deploy_root: Path = DEPLOY_ROOT,
    charts_root: Path = CHARTS_ROOT,
    admitted_images: set[str] | None = None,
) -> list[Path]:
    workloads = []
    if admitted_images is None:
        admitted_images = _admitted_postgresql_image_references()
    for path in _deployment_manifest_paths(deploy_root, charts_root):
        documents = re.split(
            r"(?m)^---[ \t]*(?:#.*)?$", path.read_text(encoding="utf-8")
        )
        if any(
            _is_postgresql_workload(document, admitted_images)
            for document in documents
        ):
            workloads.append(_display_path(path))
    return workloads


def _trino_artifacts_violate_polaris_prerequisite(
    trino_images: set[str],
    trino_workloads: list[Path],
    polaris_images: set[str],
    polaris_workloads: list[Path],
    postgresql_images: set[str],
    postgresql_workloads: list[Path],
) -> bool:
    trino_bootstrap_started = bool(trino_images or trino_workloads)
    polaris_runtime_complete = bool(
        polaris_images
        and polaris_workloads
        and postgresql_images
        and postgresql_workloads
    )
    return trino_bootstrap_started and not polaris_runtime_complete


class TrinoWorkloadDetectionTests(unittest.TestCase):
    def test_accepts_trino_deployment_or_statefulset_identity(self) -> None:
        for kind, identity in (
            ("Deployment", "  name: trino"),
            ("StatefulSet", "  labels:\n    app.kubernetes.io/name: trino"),
        ):
            with self.subTest(kind=kind, identity=identity):
                manifest = f"kind: {kind}\nmetadata:\n{identity}\n"
                self.assertTrue(_is_trino_workload(manifest))

    def test_accepts_flux_helmrelease_identity(self) -> None:
        manifest = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\n"
            "kind: HelmRelease\n"
            "metadata:\n"
            "  name: trino\n"
        )
        self.assertTrue(_is_trino_workload(manifest))

    def test_rejects_non_workload_trino_resource(self) -> None:
        manifest = "kind: Service\nmetadata:\n  name: trino\n"
        self.assertFalse(_is_trino_workload(manifest))


class PostgreSQLWorkloadDetectionTests(unittest.TestCase):
    def test_accepts_postgresql_workload_with_admitted_image(self) -> None:
        image = "registry.example/postgresql@sha256:" + "c" * 64
        for kind, identity in (
            ("Deployment", "  name: postgresql"),
            ("StatefulSet", "  labels:\n    app.kubernetes.io/name: postgres"),
        ):
            with self.subTest(kind=kind, identity=identity):
                manifest = (
                    f"kind: {kind}\nmetadata:\n{identity}\n"
                    "spec:\n"
                    "  template:\n"
                    "    spec:\n"
                    "      containers:\n"
                    f"        - image: {image}\n"
                )
                self.assertTrue(_is_postgresql_workload(manifest, {image}))

    def test_rejects_postgresql_resource_without_workload_or_admission(self) -> None:
        image = "registry.example/postgresql@sha256:" + "c" * 64
        manifests = (
            (
                "kind: Service\n"
                "metadata:\n"
                "  name: postgresql\n",
                {image},
            ),
            (
                "kind: StatefulSet\n"
                "metadata:\n"
                "  name: postgresql\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                f"        - image: {image}\n",
                set(),
            ),
        )
        for manifest, admitted_images in manifests:
            with self.subTest(manifest=manifest, admitted_images=admitted_images):
                self.assertFalse(
                    _is_postgresql_workload(manifest, admitted_images)
                )


class TrinoBootstrapPrerequisiteTests(unittest.TestCase):
    def test_polaris_only_change_does_not_trigger_trino_gate(self) -> None:
        self.assertFalse(
            _trino_artifacts_violate_polaris_prerequisite(
                set(),
                [],
                {"registry.example/polaris@sha256:" + "a" * 64},
                [Path("deploy/polaris.yaml")],
                {"registry.example/postgresql@sha256:" + "c" * 64},
                [Path("deploy/postgresql.yaml")],
            )
        )

    def test_trino_artifact_without_complete_polaris_runtime_is_rejected(self) -> None:
        trino_image = "registry.example/trino@sha256:" + "b" * 64
        polaris_image = "registry.example/polaris@sha256:" + "a" * 64
        postgresql_image = "registry.example/postgresql@sha256:" + "c" * 64
        cases = (
            ({trino_image}, [], set(), [], set(), []),
            (
                set(),
                [Path("deploy/trino.yaml")],
                {polaris_image},
                [],
                {postgresql_image},
                [Path("deploy/postgresql.yaml")],
            ),
            (
                {trino_image},
                [],
                {polaris_image},
                [Path("deploy/polaris.yaml")],
                set(),
                [],
            ),
            (
                {trino_image},
                [],
                {polaris_image},
                [Path("deploy/polaris.yaml")],
                {postgresql_image},
                [],
            ),
        )
        for (
            trino_images,
            trino_workloads,
            polaris_images,
            polaris_workloads,
            postgresql_images,
            postgresql_workloads,
        ) in cases:
            with self.subTest(
                trino_images=trino_images, trino_workloads=trino_workloads
            ):
                self.assertTrue(
                    _trino_artifacts_violate_polaris_prerequisite(
                        trino_images,
                        trino_workloads,
                        polaris_images,
                        polaris_workloads,
                        postgresql_images,
                        postgresql_workloads,
                    )
                )

    def test_complete_polaris_runtime_allows_trino_artifacts(self) -> None:
        self.assertFalse(
            _trino_artifacts_violate_polaris_prerequisite(
                {"registry.example/trino@sha256:" + "b" * 64},
                [Path("deploy/trino.yaml")],
                {"registry.example/polaris@sha256:" + "a" * 64},
                [Path("deploy/polaris.yaml")],
                {"registry.example/postgresql@sha256:" + "c" * 64},
                [Path("deploy/postgresql.yaml")],
            )
        )

    def test_repository_trino_artifacts_respect_polaris_prerequisite(self) -> None:
        trino_images = _admitted_trino_image_references()
        trino_workloads = _trino_workload_manifests()
        polaris_images = _admitted_polaris_image_references()
        polaris_workloads = _polaris_workload_manifests()
        postgresql_images = _admitted_postgresql_image_references()
        postgresql_workloads = _postgresql_workload_manifests(
            admitted_images=postgresql_images
        )

        self.assertFalse(
            _trino_artifacts_violate_polaris_prerequisite(
                trino_images,
                trino_workloads,
                polaris_images,
                polaris_workloads,
                postgresql_images,
                postgresql_workloads,
            ),
            "POLARIS_RUNTIME_PREREQUISITE_MISSING "
            f"trino_images={sorted(trino_images)} "
            f"trino_workloads={trino_workloads} "
            f"polaris_images={sorted(polaris_images)} "
            f"polaris_workloads={polaris_workloads} "
            f"postgresql_images={sorted(postgresql_images)} "
            f"postgresql_workloads={postgresql_workloads}",
        )


if __name__ == "__main__":
    unittest.main()
