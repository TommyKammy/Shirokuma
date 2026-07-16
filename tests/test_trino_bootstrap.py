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


def _has_trino_identity(value: str | None) -> bool:
    return value == "trino" or bool(
        value and re.fullmatch(r"trino[-_][a-z0-9_-]+", value)
    )


def _is_trino_workload(document: str) -> bool:
    scalars = dict(_document_scalars(document))
    return scalars.get(("kind",)) in WORKLOAD_KINDS and any(
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


def _trino_artifacts_violate_polaris_prerequisite(
    trino_images: set[str],
    trino_workloads: list[Path],
    polaris_images: set[str],
    polaris_workloads: list[Path],
) -> bool:
    trino_bootstrap_started = bool(trino_images or trino_workloads)
    polaris_runtime_complete = bool(polaris_images and polaris_workloads)
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

    def test_rejects_non_workload_trino_resource(self) -> None:
        manifest = "kind: Service\nmetadata:\n  name: trino\n"
        self.assertFalse(_is_trino_workload(manifest))


class TrinoBootstrapPrerequisiteTests(unittest.TestCase):
    def test_polaris_only_change_does_not_trigger_trino_gate(self) -> None:
        self.assertFalse(
            _trino_artifacts_violate_polaris_prerequisite(
                set(),
                [],
                {"registry.example/polaris@sha256:" + "a" * 64},
                [Path("deploy/polaris.yaml")],
            )
        )

    def test_trino_artifact_without_complete_polaris_runtime_is_rejected(self) -> None:
        trino_image = "registry.example/trino@sha256:" + "b" * 64
        polaris_image = "registry.example/polaris@sha256:" + "a" * 64
        cases = (
            ({trino_image}, [], set(), []),
            (set(), [Path("deploy/trino.yaml")], {polaris_image}, []),
        )
        for trino_images, trino_workloads, polaris_images, polaris_workloads in cases:
            with self.subTest(
                trino_images=trino_images, trino_workloads=trino_workloads
            ):
                self.assertTrue(
                    _trino_artifacts_violate_polaris_prerequisite(
                        trino_images,
                        trino_workloads,
                        polaris_images,
                        polaris_workloads,
                    )
                )

    def test_complete_polaris_runtime_allows_trino_artifacts(self) -> None:
        self.assertFalse(
            _trino_artifacts_violate_polaris_prerequisite(
                {"registry.example/trino@sha256:" + "b" * 64},
                [Path("deploy/trino.yaml")],
                {"registry.example/polaris@sha256:" + "a" * 64},
                [Path("deploy/polaris.yaml")],
            )
        )

    def test_repository_trino_artifacts_respect_polaris_prerequisite(self) -> None:
        trino_images = _admitted_trino_image_references()
        trino_workloads = _trino_workload_manifests()
        polaris_images = _admitted_polaris_image_references()
        polaris_workloads = _polaris_workload_manifests()

        self.assertFalse(
            _trino_artifacts_violate_polaris_prerequisite(
                trino_images,
                trino_workloads,
                polaris_images,
                polaris_workloads,
            ),
            "POLARIS_RUNTIME_PREREQUISITE_MISSING "
            f"trino_images={sorted(trino_images)} "
            f"trino_workloads={trino_workloads} "
            f"polaris_images={sorted(polaris_images)} "
            f"polaris_workloads={polaris_workloads}",
        )


if __name__ == "__main__":
    unittest.main()
