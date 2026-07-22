from __future__ import annotations

import base64
import json
import re
import tempfile
import textwrap
import threading
import unittest
from collections.abc import Mapping, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

from gitops_render import (
    RenderError,
    RepositoryContext,
    default_repository_context,
    iceberg_bootstrap_manifests,
    load_yaml_file,
    polaris_workload_manifests,
)


ROOT = Path(__file__).resolve().parents[1]
DEPLOY_ROOT = ROOT / "deploy"
CHARTS_ROOT = ROOT / "charts"
RESIDENT_IMAGES = ROOT / "security/resident-images.json"
LOCAL_GIT_URL = "ssh://git@github.com/TommyKammy/Shirokuma"
DEPLOYMENT_SUFFIXES = {".json", ".yaml", ".yml"}
WORKLOAD_KINDS = {"Deployment", "StatefulSet"}
POLARIS_COMPONENT = "polaris"


# Compatibility surface used by the downstream Trino prerequisite tests.  The
# Iceberg detector below does not use this source-text parser.
def _mapping_scalars(
    document: str,
) -> Iterator[tuple[tuple[str, ...], str]]:
    stack: list[tuple[int, str]] = []
    for raw_line in document.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        match = re.match(
            r"^(?P<indent>[ ]*)(?P<key>[^:#][^:]*):(?P<value>.*)$",
            raw_line,
        )
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
    value: object,
    path: tuple[str, ...] = (),
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
    return value == "polaris" or bool(
        value
        and re.fullmatch(r"polaris[-_][a-z0-9_-]+", value)
    )


def _is_polaris_workload(
    document: str,
    admitted_images: set[str],
) -> bool:
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
    ledger: Mapping[str, Sequence[Mapping[str, str]]],
    component: str,
) -> set[str]:
    return {
        entry["reference"]
        for entry in ledger["images"]
        if entry.get("component") == component
    }


def _deployment_manifest_paths(
    deploy_root: Path = DEPLOY_ROOT,
    charts_root: Path = CHARTS_ROOT,
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


def _admitted_polaris_image_references(
    resident_images: Path = RESIDENT_IMAGES,
) -> set[str]:
    payload = json.loads(resident_images.read_text(encoding="utf-8"))
    return {
        str(image["reference"])
        for image in payload.get("images", [])
        if image.get("component") == "polaris" and image.get("reference")
    }


def _polaris_workload_manifests(
    deploy_root: Path = DEPLOY_ROOT,
    charts_root: Path = CHARTS_ROOT,
    admitted_images: set[str] | None = None,
) -> list[Path]:
    return polaris_workload_manifests(
        deploy_root,
        charts_root,
        (
            _admitted_polaris_image_references()
            if admitted_images is None
            else admitted_images
        ),
    )


def _iceberg_bootstrap_manifests(
    deploy_root: Path = DEPLOY_ROOT,
    charts_root: Path = CHARTS_ROOT,
    repository_context: RepositoryContext | None = None,
) -> list[Path]:
    del charts_root  # Charts are rendered only through repository-local HelmRelease.
    context = repository_context or default_repository_context(
        ROOT,
        deploy_root,
    )
    return iceberg_bootstrap_manifests(deploy_root, context)


class RepositoryFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.deploy_root = self.root / "deploy"
        self.charts_root = self.root / "charts"
        self.deploy_root.mkdir()
        self.charts_root.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, relative: str, text: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            textwrap.dedent(text).strip() + "\n",
            encoding="utf-8",
        )
        return path

    def write_json(self, relative: str, value: Mapping[str, Any]) -> Path:
        def normalize(item: Any) -> Any:
            if isinstance(item, str) and "\n" in item:
                return textwrap.dedent(item).strip() + "\n"
            if isinstance(item, dict):
                return {key: normalize(child) for key, child in item.items()}
            if isinstance(item, list):
                return [normalize(child) for child in item]
            return item

        return self.write(
            relative,
            json.dumps(normalize(value), indent=2, sort_keys=True),
        )

    def context(self, *urls: str) -> RepositoryContext:
        return RepositoryContext.create(
            self.root,
            urls or (LOCAL_GIT_URL,),
        )

    def detect(self, context: RepositoryContext | None = None) -> list[Path]:
        return _iceberg_bootstrap_manifests(
            self.deploy_root,
            self.charts_root,
            context or self.context(),
        )

    def job(
        self,
        relative: str,
        *,
        name: str = "catalog-maintenance",
        image: str = "registry.example/catalog-maintenance:stable",
        container_name: str = "runner",
        label: str = "catalog-maintenance",
        kind: str = "Job",
        init_image: str | None = None,
    ) -> Path:
        containers = [
            {
                "name": container_name,
                "image": image,
            }
        ]
        pod_spec: dict[str, Any] = {
            "restartPolicy": "Never",
            "containers": containers,
        }
        if init_image is not None:
            pod_spec["initContainers"] = [
                {"name": "initializer", "image": init_image}
            ]
        template = {"spec": pod_spec}
        spec: dict[str, Any]
        if kind == "CronJob":
            spec = {
                "schedule": "0 * * * *",
                "jobTemplate": {"spec": {"template": template}},
            }
        else:
            spec = {"template": template}
        return self.write_json(
            relative,
            {
                "apiVersion": "batch/v1",
                "kind": kind,
                "metadata": {"name": name, "labels": {"role": label}},
                "spec": spec,
            },
        )

    def local_source(
        self,
        *,
        url: str = LOCAL_GIT_URL,
        name: str = "source",
        namespace: str = "flux-system",
        relative: str = "deploy/control/source.yaml",
    ) -> Path:
        return self.write_json(
            relative,
            {
                "apiVersion": "source.toolkit.fluxcd.io/v1",
                "kind": "GitRepository",
                "metadata": {"name": name, "namespace": namespace},
                "spec": {
                    "interval": "1m",
                    "url": url,
                    "ref": {"branch": "main"},
                },
            },
        )

    def flux(
        self,
        path: str,
        *,
        name: str = "app",
        namespace: str = "flux-system",
        source_name: str = "source",
        spec: Mapping[str, Any] | None = None,
        relative: str = "deploy/control/flux.yaml",
    ) -> Path:
        value: dict[str, Any] = {
            "interval": "1m",
            "path": path,
            "prune": True,
            "sourceRef": {
                "kind": "GitRepository",
                "name": source_name,
            },
        }
        value.update(spec or {})
        return self.write_json(
            relative,
            {
                "apiVersion": "kustomize.toolkit.fluxcd.io/v1",
                "kind": "Kustomization",
                "metadata": {"name": name, "namespace": namespace},
                "spec": value,
            },
        )

    def kustomization(
        self,
        relative_directory: str,
        **fields: Any,
    ) -> Path:
        value = {
            "apiVersion": "kustomize.config.k8s.io/v1beta1",
            "kind": "Kustomization",
            **fields,
        }
        return self.write_json(
            f"{relative_directory}/kustomization.yaml",
            value,
        )

    def chart(
        self,
        name: str = "catalog-job",
        *,
        values: Mapping[str, Any] | None = None,
        template: str | None = None,
        extra_values: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> Path:
        chart_root = self.charts_root / name
        self.write(
            f"charts/{name}/Chart.yaml",
            f"""
            apiVersion: v2
            name: {name}
            version: 0.1.0
            """,
        )
        defaults = {
            "job": {"name": "catalog-maintenance", "label": "catalog-maintenance"},
            "container": {"name": "runner"},
            "image": {
                "repository": "registry.example/catalog-maintenance",
                "tag": "stable",
            },
        }
        if values:
            for key, value in values.items():
                defaults[key] = value
        self.write_json(f"charts/{name}/values.yaml", defaults)
        self.write(
            f"charts/{name}/templates/job.yaml",
            template
            or """
            apiVersion: batch/v1
            kind: Job
            metadata:
              name: {{ .Values.job.name | quote }}
              labels:
                role: {{ .Values.job.label | quote }}
            spec:
              template:
                spec:
                  restartPolicy: Never
                  containers:
                    - name: {{ .Values.container.name | quote }}
                      image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
            """,
        )
        for filename, content in (extra_values or {}).items():
            self.write_json(f"charts/{name}/{filename}", content)
        return chart_root

    def helm_release(
        self,
        chart: str = "./charts/catalog-job",
        *,
        values: Mapping[str, Any] | None = None,
        values_from: list[Mapping[str, Any]] | None = None,
        post_renderers: list[Mapping[str, Any]] | None = None,
        common_metadata: Mapping[str, Any] | None = None,
        suspend: bool = False,
        name: str = "catalog-job",
        relative: str = "deploy/releases/catalog-job.yaml",
        chart_fields: Mapping[str, Any] | None = None,
        chart_ref: Mapping[str, Any] | None = None,
        spec_fields: Mapping[str, Any] | None = None,
    ) -> Path:
        spec: dict[str, Any] = {"interval": "1m"}
        if chart_ref is None:
            chart_spec = {
                "chart": chart,
                "reconcileStrategy": "Revision",
                "sourceRef": {
                    "kind": "GitRepository",
                    "name": "source",
                    "namespace": "flux-system",
                },
            }
            chart_spec.update(chart_fields or {})
            spec["chart"] = {"spec": chart_spec}
        else:
            spec["chartRef"] = dict(chart_ref)
        if values is not None:
            spec["values"] = dict(values)
        if values_from is not None:
            spec["valuesFrom"] = [
                {"kind": "ConfigMap", **dict(value)}
                for value in values_from
            ]
        if post_renderers is not None:
            spec["postRenderers"] = [dict(value) for value in post_renderers]
        if common_metadata is not None:
            spec["commonMetadata"] = dict(common_metadata)
        if suspend:
            spec["suspend"] = True
        spec.update(spec_fields or {})
        return self.write_json(
            relative,
            {
                "apiVersion": "helm.toolkit.fluxcd.io/v2",
                "kind": "HelmRelease",
                "metadata": {"name": name, "namespace": "flux-system"},
                "spec": spec,
            },
        )


class PolarisWorkloadDetectionTests(RepositoryFixture):
    IMAGE = "registry.example/polaris@sha256:" + "a" * 64

    def workload(
        self,
        relative: str,
        *,
        kind: str = "Deployment",
        name: str = "polaris",
        image: str | None = None,
    ) -> Path:
        return self.write_json(
            relative,
            {
                "apiVersion": "apps/v1",
                "kind": kind,
                "metadata": {"name": name},
                "spec": {
                    "selector": {"matchLabels": {"app": name}},
                    "template": {
                        "metadata": {"labels": {"app": name}},
                        "spec": {
                            "containers": [
                                {
                                    "name": name,
                                    "image": image or self.IMAGE,
                                }
                            ]
                        },
                    },
                },
            },
        )

    def test_accepts_only_admitted_polaris_workloads(self) -> None:
        admitted = self.workload("deploy/polaris.yaml")
        self.workload(
            "deploy/unadmitted.yaml",
            image="registry.example/polaris:mutable",
        )
        self.workload(
            "deploy/wrong-component.yaml",
            name="catalog-api",
        )
        self.assertEqual(
            [admitted.resolve()],
            _polaris_workload_manifests(
                self.deploy_root,
                self.charts_root,
                {self.IMAGE},
            ),
        )

    def test_accepts_deployment_and_statefulset(self) -> None:
        expected = {
            self.workload("deploy/deployment.yaml").resolve(),
            self.workload(
                "deploy/statefulset.yaml",
                kind="StatefulSet",
            ).resolve(),
        }
        self.assertEqual(
            expected,
            set(
                _polaris_workload_manifests(
                    self.deploy_root,
                    self.charts_root,
                    {self.IMAGE},
                )
            ),
        )

    def test_rejects_non_workload_resource(self) -> None:
        self.write_json(
            "deploy/polaris.yaml",
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "polaris"},
                "data": {"image": self.IMAGE},
            },
        )
        self.assertEqual(
            [],
            _polaris_workload_manifests(
                self.deploy_root,
                self.charts_root,
                {self.IMAGE},
            ),
        )

    def test_reads_static_chart_template_without_scanning_values(self) -> None:
        template = self.workload(
            "charts/polaris/templates/deployment.yaml"
        )
        self.write(
            "charts/polaris/values.yaml",
            f"unrelated: {self.IMAGE}",
        )
        self.assertEqual(
            [template.resolve()],
            _polaris_workload_manifests(
                self.deploy_root,
                self.charts_root,
                {self.IMAGE},
            ),
        )


class RawBootstrapIdentityTests(RepositoryFixture):
    def test_detects_only_final_job_and_cronjob_identity_fields(self) -> None:
        cases = (
            ("name", {"name": "iceberg-table-bootstrap"}),
            ("label", {"label": "iceberg-table-bootstrap"}),
            (
                "container-name",
                {"container_name": "iceberg-table-bootstrap"},
            ),
            (
                "image",
                {"image": "registry.example/iceberg-table:bootstrap"},
            ),
            (
                "init-image",
                {"init_image": "registry.example/iceberg-table:bootstrap"},
            ),
        )
        expected: set[Path] = set()
        for kind in ("Job", "CronJob"):
            for label, fields in cases:
                expected.add(
                    self.job(
                        f"deploy/{kind.lower()}-{label}.yaml",
                        kind=kind,
                        **fields,
                    ).resolve()
                )
        self.assertEqual(expected, set(self.detect()))

    def test_ignores_non_identity_nested_values(self) -> None:
        self.write_json(
            "deploy/job.yaml",
            {
                "apiVersion": "batch/v1",
                "kind": "Job",
                "metadata": {
                    "name": "maintenance",
                    "annotations": {
                        "note": "iceberg-table-bootstrap",
                    },
                },
                "spec": {
                    "template": {
                        "spec": {
                            "restartPolicy": "Never",
                            "containers": [
                                {
                                    "name": "runner",
                                    "image": "registry.example/runner:stable",
                                    "env": [
                                        {
                                            "name": "NOTE",
                                            "value": "iceberg-table-bootstrap",
                                        }
                                    ],
                                }
                            ],
                        }
                    }
                },
            },
        )
        self.assertEqual([], self.detect())

    def test_unwraps_list_and_parses_flow_style_and_aliases(self) -> None:
        listed = self.write_json(
            "deploy/list.json",
            {
                "apiVersion": "v1",
                "kind": "List",
                "items": [
                    {
                        "apiVersion": "batch/v1",
                        "kind": "Job",
                        "metadata": {"name": "iceberg-table-bootstrap"},
                        "spec": {
                            "template": {
                                "spec": {
                                    "restartPolicy": "Never",
                                    "containers": [
                                        {
                                            "name": "runner",
                                            "image": "registry.example/runner:stable",
                                        }
                                    ],
                                }
                            }
                        },
                    }
                ],
            },
        )
        aliased = self.write(
            "deploy/alias.yaml",
            """
            apiVersion: batch/v1
            kind: Job
            metadata: &identity {name: iceberg-table-bootstrap}
            spec:
              template:
                metadata: *identity
                spec: {restartPolicy: Never, containers: [{name: runner, image: registry.example/runner:stable}]}
            """,
        )
        self.assertEqual(
            {listed.resolve(), aliased.resolve()},
            set(self.detect()),
        )

    def test_ignores_jobs_without_combined_identity(self) -> None:
        self.job(
            "deploy/iceberg-only.yaml",
            name="iceberg-maintenance",
        )
        self.job(
            "deploy/bootstrap-only.yaml",
            image="registry.example/bootstrap:stable",
        )
        self.assertEqual([], self.detect())

    def test_wrong_api_version_fails_closed(self) -> None:
        self.write_json(
            "deploy/bad-version.yaml",
            {
                "apiVersion": "batch/v999",
                "kind": "Job",
                "metadata": {"name": "iceberg-table-bootstrap"},
                "spec": {
                    "template": {
                        "spec": {
                            "restartPolicy": "Never",
                            "containers": [
                                {
                                    "name": "runner",
                                    "image": "registry.example/runner:stable",
                                }
                            ],
                        }
                    }
                },
            },
        )
        with self.assertRaises(RenderError):
            self.detect()


class FluxKustomizeRenderingTests(RepositoryFixture):
    def setUp(self) -> None:
        super().setUp()
        self.local_source()

    def test_plain_kustomization_patch_is_part_of_final_state(self) -> None:
        self.job("deploy/app/job.yaml")
        self.kustomization(
            "deploy/app",
            resources=["job.yaml"],
            patches=[
                {
                    "target": {"kind": "Job"},
                    "patch": """
                    - op: replace
                      path: /metadata/name
                      value: iceberg-table-bootstrap
                    """,
                }
            ],
        )
        flux = self.flux("./deploy/app")
        self.assertEqual([flux.resolve()], self.detect())

    def test_flux_plan_named_kustomization_yaml_is_not_skipped(self) -> None:
        self.job(
            "deploy/app/job.yaml",
            name="iceberg-table-bootstrap",
        )
        self.kustomization("deploy/app", resources=["job.yaml"])
        flux = self.flux(
            "./deploy/app",
            relative="deploy/control/kustomization.yaml",
        )
        self.assertEqual([flux.resolve()], self.detect())

    def test_flux_source_reference_accepts_only_the_exact_api_version(self) -> None:
        self.job("deploy/app/job.yaml")
        self.kustomization("deploy/app", resources=["job.yaml"])
        self.flux(
            "./deploy/app",
            spec={
                "sourceRef": {
                    "apiVersion": "source.toolkit.fluxcd.io/v1",
                    "kind": "GitRepository",
                    "name": "source",
                }
            },
        )
        self.assertEqual([], self.detect())
        self.flux(
            "./deploy/app",
            spec={
                "sourceRef": {
                    "apiVersion": "source.toolkit.fluxcd.io/v1beta2",
                    "kind": "GitRepository",
                    "name": "source",
                }
            },
        )
        with self.assertRaises(RenderError):
            self.detect()

    def test_flux_repository_path_must_be_relative(self) -> None:
        self.job("deploy/app/job.yaml")
        self.kustomization("deploy/app", resources=["job.yaml"])
        self.flux(str(self.root / "deploy/app"))
        with self.assertRaises(RenderError):
            self.detect()
        self.flux("./deploy/app", spec={"path": 7})
        with self.assertRaises(RenderError):
            self.detect()

    def test_native_kustomize_references_must_be_relative(self) -> None:
        job = self.job("deploy/app/job.yaml")
        self.kustomization("deploy/app", resources=[str(job)])
        self.flux("./deploy/app")
        with self.assertRaises(RenderError):
            self.detect()

    def test_ignore_missing_components_only_skips_local_absence(self) -> None:
        self.job("deploy/app/job.yaml")
        self.kustomization("deploy/app", resources=["job.yaml"])
        self.flux(
            "./deploy/app",
            spec={
                "components": ["missing-component"],
                "ignoreMissingComponents": True,
            },
        )
        self.assertEqual([], self.detect())
        self.flux(
            "./deploy/app",
            spec={
                "components": ["missing-component"],
                "ignoreMissingComponents": False,
            },
        )
        with self.assertRaises(RenderError):
            self.detect()

    def test_reachable_flux_plans_cannot_share_one_target_path(self) -> None:
        self.job("deploy/app/job.yaml")
        self.kustomization("deploy/app", resources=["job.yaml"])
        self.flux(
            "./deploy/app",
            name="first",
            relative="deploy/control/first.yaml",
        )
        self.flux(
            "./deploy/app",
            name="second",
            relative="deploy/control/second.yaml",
        )
        with self.assertRaises(RenderError):
            self.detect()

    def test_unlisted_flux_plans_do_not_create_target_conflicts(self) -> None:
        self.write_json(
            "deploy/root/selected.yaml",
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "selected"},
            },
        )
        for name in ("first", "second"):
            self.flux(
                "./deploy/app",
                name=name,
                relative=f"deploy/root/{name}.yaml",
            )
        self.kustomization("deploy/root", resources=["selected.yaml"])
        self.flux(
            "./deploy/root",
            name="parent",
            relative="deploy/control/parent.yaml",
        )
        self.assertEqual([], self.detect())

    def test_disconnected_flux_cycle_cannot_hide_behind_a_valid_root(self) -> None:
        self.write_json(
            "deploy/app/configmap.yaml",
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "selected"},
            },
        )
        self.kustomization("deploy/app", resources=["configmap.yaml"])
        self.flux(
            "./deploy/app",
            name="root",
            relative="deploy/control/root.yaml",
        )
        for name in ("cycle-b", "cycle-c"):
            self.flux(
                "./deploy/cycle",
                name=name,
                relative=f"deploy/cycle/{name}.yaml",
            )
        with self.assertRaises(RenderError):
            self.detect()

    def test_resource_list_excludes_unreferenced_workload_from_flux_transform(self) -> None:
        self.job("deploy/app/unlisted-job.yaml")
        self.write_json(
            "deploy/app/configmap.yaml",
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "selected"},
            },
        )
        self.kustomization(
            "deploy/app",
            resources=["configmap.yaml"],
        )
        self.flux(
            "./deploy/app",
            spec={
                "commonMetadata": {
                    "labels": {"role": "iceberg-table-bootstrap"}
                }
            },
        )
        self.assertEqual([], self.detect())

    def test_suspended_kustomization_does_not_reconcile(self) -> None:
        self.job("deploy/app/job.yaml")
        self.kustomization("deploy/app", resources=["job.yaml"])
        self.flux(
            "./deploy/app",
            spec={
                "suspend": True,
                "commonMetadata": {
                    "labels": {"role": "iceberg-table-bootstrap"}
                },
            },
        )
        self.assertEqual([], self.detect())

    def test_suspended_child_does_not_validate_its_source_artifact(self) -> None:
        self.job(
            "deploy/app/job.yaml",
            name="iceberg-table-bootstrap",
        )
        self.kustomization("deploy/app", resources=["job.yaml"])
        self.write_json(
            "deploy/root/child-source.yaml",
            {
                "apiVersion": "source.toolkit.fluxcd.io/v1",
                "kind": "GitRepository",
                "metadata": {
                    "name": "child-source",
                    "namespace": "flux-system",
                },
                "spec": {
                    "interval": "1m",
                    "url": LOCAL_GIT_URL,
                    "ref": {"branch": "main"},
                    "ignore": "/*",
                },
            },
        )
        self.flux(
            "./deploy/app",
            name="child",
            source_name="child-source",
            spec={"suspend": True},
            relative="deploy/root/child.yaml",
        )
        self.kustomization(
            "deploy/root",
            resources=["child-source.yaml", "child.yaml"],
        )
        self.flux(
            "./deploy/root",
            name="parent",
            relative="deploy/control/parent.yaml",
        )
        self.assertEqual([], self.detect())

    def test_no_kustomization_uses_flux_autogeneration(self) -> None:
        self.job("deploy/app/nested/job.yaml")
        flux = self.flux(
            "./deploy/app",
            spec={
                "commonMetadata": {
                    "labels": {"role": "iceberg-table-bootstrap"}
                }
            },
        )
        self.assertEqual([flux.resolve()], self.detect())

    def test_flow_style_flux_patch_is_applied(self) -> None:
        self.job("deploy/app/job.yaml")
        self.kustomization("deploy/app", resources=["job.yaml"])
        flux = self.write(
            "deploy/control/flux.yaml",
            """
            apiVersion: kustomize.toolkit.fluxcd.io/v1
            kind: Kustomization
            metadata: {name: app, namespace: flux-system}
            spec:
              interval: 1m
              path: ./deploy/app
              sourceRef: {kind: GitRepository, name: source}
              patches: [{target: {kind: Job}, patch: '[{"op":"replace","path":"/metadata/name","value":"iceberg-table-bootstrap"}]'}]
            """,
        )
        self.assertEqual([flux.resolve()], self.detect())

    def test_mapping_valued_json_patch_is_preserved(self) -> None:
        self.job("deploy/app/job.yaml")
        self.kustomization("deploy/app", resources=["job.yaml"])
        flux = self.flux(
            "./deploy/app",
            spec={
                "patches": [
                    {
                        "target": {"kind": "Job"},
                        "patch": """
                        - op: add
                          path: /metadata/labels
                          value:
                            role: iceberg-table-bootstrap
                        """,
                    }
                ]
            },
        )
        self.assertEqual([flux.resolve()], self.detect())

    def test_whole_container_array_replacement_is_preserved(self) -> None:
        self.job("deploy/app/job.yaml")
        self.kustomization("deploy/app", resources=["job.yaml"])
        flux = self.flux(
            "./deploy/app",
            spec={
                "patches": [
                    {
                        "target": {"kind": "Job"},
                        "patch": """
                        - op: replace
                          path: /spec/template/spec/containers
                          value:
                            - name: iceberg-table-bootstrap
                              image: registry.example/runner:stable
                        """,
                    }
                ]
            },
        )
        self.assertEqual([flux.resolve()], self.detect())

    def test_array_remove_and_replace_uses_shifted_final_state(self) -> None:
        self.write_json(
            "deploy/app/job.yaml",
            {
                "apiVersion": "batch/v1",
                "kind": "Job",
                "metadata": {"name": "maintenance"},
                "spec": {
                    "template": {
                        "spec": {
                            "restartPolicy": "Never",
                            "containers": [
                                {
                                    "name": "safe",
                                    "image": "registry.example/safe:stable",
                                },
                                {
                                    "name": "iceberg-table-bootstrap",
                                    "image": "registry.example/bootstrap:stable",
                                },
                            ],
                        }
                    }
                },
            },
        )
        self.kustomization("deploy/app", resources=["job.yaml"])
        self.flux(
            "./deploy/app",
            spec={
                "patches": [
                    {
                        "target": {"kind": "Job"},
                        "patch": """
                        - op: remove
                          path: /spec/template/spec/containers/0
                        - op: replace
                          path: /spec/template/spec/containers/0
                          value:
                            name: safe
                            image: registry.example/safe:stable
                        """,
                    }
                ]
            },
        )
        self.assertEqual([], self.detect())

    def test_plain_then_flux_patch_uses_only_final_state(self) -> None:
        self.job("deploy/app/job.yaml")
        self.kustomization(
            "deploy/app",
            resources=["job.yaml"],
            patches=[
                {
                    "target": {"kind": "Job"},
                    "patch": """
                    - op: replace
                      path: /metadata/name
                      value: iceberg-table-bootstrap
                    """,
                }
            ],
        )
        self.flux(
            "./deploy/app",
            spec={
                "patches": [
                    {
                        "target": {"kind": "Job"},
                        "patch": """
                        - op: replace
                          path: /metadata/name
                          value: catalog-maintenance
                        """,
                    }
                ]
            },
        )
        self.assertEqual([], self.detect())

    def test_native_name_transform_and_flux_patch_share_one_build(self) -> None:
        self.job("deploy/app/job.yaml", name="maintenance")
        self.kustomization(
            "deploy/app",
            resources=["job.yaml"],
            namePrefix="catalog-",
        )
        flux = self.flux(
            "./deploy/app",
            spec={
                "patches": [
                    {
                        "target": {"kind": "Job", "name": "maintenance"},
                        "patch": """
                        - op: replace
                          path: /spec/template/spec/containers/0/image
                          value: registry.example/iceberg-table:bootstrap
                        """,
                    }
                ]
            },
        )
        self.assertEqual([flux.resolve()], self.detect())

    def test_recursive_parent_uses_patched_child_reconciliation(self) -> None:
        self.job("deploy/app/job.yaml")
        self.kustomization("deploy/app", resources=["job.yaml"])
        self.flux(
            "./deploy/app",
            name="child",
            relative="deploy/root/child.yaml",
        )
        self.kustomization(
            "deploy/root",
            resources=["child.yaml"],
        )
        parent = self.flux(
            "./deploy/root",
            name="parent",
            relative="deploy/control/parent.yaml",
            spec={
                "patches": [
                    {
                        "target": {
                            "group": "kustomize.toolkit.fluxcd.io",
                            "kind": "Kustomization",
                            "name": "child",
                        },
                        "patch": """
                        - op: add
                          path: /spec/commonMetadata
                          value:
                            labels:
                              role: iceberg-table-bootstrap
                        """,
                    }
                ]
            },
        )
        self.assertEqual([parent.resolve()], self.detect())

    def test_recursive_parent_does_not_build_suspended_child(self) -> None:
        self.job(
            "deploy/app/job.yaml",
            name="iceberg-table-bootstrap",
        )
        self.kustomization("deploy/app", resources=["job.yaml"])
        self.flux(
            "./deploy/app",
            name="child",
            relative="deploy/root/child.yaml",
            spec={"suspend": True},
        )
        self.kustomization(
            "deploy/root",
            resources=["child.yaml"],
        )
        self.flux(
            "./deploy/root",
            name="parent",
            relative="deploy/control/parent.yaml",
        )
        self.assertEqual([], self.detect())

    def test_image_rewrites_are_mutations_not_additional_tokens(self) -> None:
        cases = (
            (
                "away",
                "registry.example/iceberg-table:bootstrap",
                {
                    "name": "registry.example/iceberg-table",
                    "newName": "registry.example/catalog-maintenance",
                    "newTag": "stable",
                },
                False,
            ),
            (
                "toward",
                "registry.example/catalog-maintenance:stable",
                {
                    "name": "registry.example/catalog-maintenance",
                    "newName": "registry.example/iceberg-table",
                    "newTag": "bootstrap",
                },
                True,
            ),
        )
        for label, original, image_rule, expected in cases:
            with self.subTest(label=label):
                directory = f"deploy/{label}"
                self.job(f"{directory}/job.yaml", image=original)
                self.kustomization(directory, resources=["job.yaml"])
                self.flux(
                    f"./{directory}",
                    name=label,
                    relative=f"deploy/control/{label}.yaml",
                    spec={"images": [image_rule]},
                )
        detected = {path.name for path in self.detect()}
        self.assertNotIn("away.yaml", detected)
        self.assertIn("toward.yaml", detected)

    def test_name_and_common_metadata_transform_final_identity(self) -> None:
        self.job("deploy/app/job.yaml")
        self.kustomization("deploy/app", resources=["job.yaml"])
        flux = self.flux(
            "./deploy/app",
            spec={
                "namePrefix": "iceberg-",
                "nameSuffix": "-bootstrap",
                "commonMetadata": {
                    "labels": {"role": "catalog-maintenance"}
                },
            },
        )
        self.assertEqual([flux.resolve()], self.detect())

    def test_unmatched_and_non_identity_patches_do_not_detect(self) -> None:
        self.job("deploy/app/job.yaml")
        self.kustomization("deploy/app", resources=["job.yaml"])
        self.flux(
            "./deploy/app",
            spec={
                "patches": [
                    {
                        "target": {"kind": "Deployment"},
                        "patch": """
                        - op: add
                          path: /metadata/labels/role
                          value: iceberg-table-bootstrap
                        """,
                    },
                    {
                        "target": {"kind": "Job"},
                        "patch": """
                        - op: add
                          path: /metadata/annotations/note
                          value: iceberg-table-bootstrap
                        """,
                    },
                ]
            },
        )
        self.assertEqual([], self.detect())

    def test_repository_path_escape_fails_closed(self) -> None:
        self.flux("../../outside")
        with self.assertRaises(RenderError):
            self.detect()

    def test_cluster_dependent_flux_inputs_fail_closed(self) -> None:
        self.job("deploy/app/job.yaml")
        self.kustomization("deploy/app", resources=["job.yaml"])
        self.flux(
            "./deploy/app",
            spec={
                "decryption": {
                    "provider": "sops",
                    "secretRef": {"name": "sops-key"},
                }
            },
        )
        with self.assertRaises(RenderError):
            self.detect()

    def test_json_patch_array_and_null_operations_use_final_state(self) -> None:
        null_directory = "deploy/null"
        self.job(
            f"{null_directory}/job.yaml",
            label="iceberg-table-bootstrap",
        )
        self.kustomization(null_directory, resources=["job.yaml"])
        self.flux(
            f"./{null_directory}",
            name="null",
            relative="deploy/control/null.yaml",
            spec={
                "patches": [
                    {
                        "target": {"kind": "Job"},
                        "patch": """
                        - op: replace
                          path: /metadata/labels/role
                          value: null
                        """,
                    }
                ]
            },
        )

        append_directory = "deploy/append"
        self.job(f"{append_directory}/job.yaml")
        self.kustomization(append_directory, resources=["job.yaml"])
        appended = self.flux(
            f"./{append_directory}",
            name="append",
            relative="deploy/control/append.yaml",
            spec={
                "patches": [
                    {
                        "target": {"kind": "Job"},
                        "patch": """
                        - op: add
                          path: /spec/template/spec/containers/-
                          value:
                            name: iceberg-table-bootstrap
                            image: registry.example/runner:stable
                        """,
                    }
                ]
            },
        )

        remove_directory = "deploy/remove"
        self.job(
            f"{remove_directory}/job.yaml",
            container_name="iceberg-table-bootstrap",
        )
        self.kustomization(remove_directory, resources=["job.yaml"])
        self.flux(
            f"./{remove_directory}",
            name="remove",
            relative="deploy/control/remove.yaml",
            spec={
                "patches": [
                    {
                        "target": {"kind": "Job"},
                        "patch": """
                        - op: remove
                          path: /spec/template/spec/containers/0
                        - op: add
                          path: /spec/template/spec/containers/-
                          value:
                            name: runner
                            image: registry.example/runner:stable
                        """,
                    }
                ]
            },
        )
        self.assertEqual([appended.resolve()], self.detect())

    def test_external_flux_source_fails_closed(self) -> None:
        self.local_source(url="ssh://git@github.com/example/external")
        self.flux("./deploy/app")
        with self.assertRaises(RenderError):
            self.detect(self.context(LOCAL_GIT_URL))

    def test_unreachable_child_source_is_not_resolved_from_raw_files(self) -> None:
        self.job(
            "deploy/app/job.yaml",
            name="iceberg-table-bootstrap",
        )
        self.kustomization("deploy/app", resources=["job.yaml"])
        self.local_source(
            name="child-source",
            relative="deploy/root/unlisted-source.yaml",
        )
        self.flux(
            "./deploy/app",
            name="child",
            source_name="child-source",
            relative="deploy/root/child.yaml",
        )
        self.kustomization("deploy/root", resources=["child.yaml"])
        self.flux(
            "./deploy/root",
            name="parent",
            relative="deploy/control/parent.yaml",
        )
        with self.assertRaises(RenderError):
            self.detect()

    def test_wrong_api_group_cannot_impersonate_flux_source(self) -> None:
        self.write_json(
            "deploy/control/fake-source.yaml",
            {
                "apiVersion": "example.com/v1",
                "kind": "GitRepository",
                "metadata": {
                    "name": "fake",
                    "namespace": "flux-system",
                },
                "spec": {"url": LOCAL_GIT_URL},
            },
        )
        self.flux("./deploy/app", source_name="fake")
        with self.assertRaises(RenderError):
            self.detect()

    def test_local_source_artifact_contract_fails_closed(self) -> None:
        cases = (
            ("ignore", {"ignore": ""}),
            ("include", {"include": [{"fromPath": "deploy"}]}),
            ("submodules", {"recurseSubmodules": True}),
            ("sparse", {"sparseCheckout": [{"path": "deploy"}]}),
            (
                "verify",
                {
                    "verify": {
                        "mode": "HEAD",
                        "secretRef": {"name": "git-signing-keys"},
                    }
                },
            ),
            ("suspended", {"suspend": True}),
            ("tag", {"ref": {"tag": "v1.0.0"}}),
            ("other-branch", {"ref": {"branch": "other"}}),
        )
        self.job("deploy/app/job.yaml")
        self.kustomization("deploy/app", resources=["job.yaml"])
        self.flux("./deploy/app")
        for label, source_fields in cases:
            with self.subTest(label=label):
                self.write_json(
                    "deploy/control/source.yaml",
                    {
                        "apiVersion": "source.toolkit.fluxcd.io/v1",
                        "kind": "GitRepository",
                        "metadata": {
                            "name": "source",
                            "namespace": "flux-system",
                        },
                        "spec": {
                            "interval": "1m",
                            "url": LOCAL_GIT_URL,
                            "ref": {"branch": "main"},
                            **source_fields,
                        },
                    },
                )
                with self.assertRaises(RenderError):
                    self.detect()
        self.local_source()
        self.write_json(
            "deploy/control/source.yaml",
            {
                "apiVersion": "source.toolkit.fluxcd.io/v1",
                "kind": "GitRepository",
                "metadata": {
                    "name": "source",
                    "namespace": "flux-system",
                },
                "spec": {
                    "interval": "1m",
                    "url": LOCAL_GIT_URL,
                    "ref": {"branch": "main"},
                },
            },
        )
        self.assertEqual([], self.detect())
        for filename, content in (
            (".sourceignore", "deploy/**"),
            (".gitmodules", "[submodule \"dependency\"]"),
        ):
            with self.subTest(filename=filename):
                path = self.write(filename, content)
                with self.assertRaises(RenderError):
                    self.detect()
                path.unlink()

    def test_unlisted_external_flux_object_is_outside_resource_graph(self) -> None:
        self.write_json(
            "deploy/root/selected.yaml",
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "selected"},
            },
        )
        self.kustomization(
            "deploy/root",
            resources=["selected.yaml"],
        )
        self.local_source(
            url="ssh://git@github.com/example/external",
            name="external",
            relative="deploy/root/external-source.yaml",
        )
        self.flux(
            "./deploy/app",
            name="unlisted",
            source_name="external",
            relative="deploy/root/unlisted.yaml",
        )
        self.flux(
            "./deploy/root",
            name="parent",
            relative="deploy/control/parent.yaml",
        )
        self.assertEqual([], self.detect(self.context(LOCAL_GIT_URL)))

    def test_remote_native_kustomize_inputs_fail_before_build(self) -> None:
        (self.root / "deploy/app/github.com/example/base").mkdir(
            parents=True,
        )
        self.kustomization(
            "deploy/app",
            resources=["github.com/example/base"],
        )
        self.flux("./deploy/app")
        with self.assertRaises(RenderError):
            self.detect()

    def test_remote_validator_and_generator_graphs_fail_before_build(self) -> None:
        self.kustomization(
            "deploy/app",
            validators=["https://github.com/example/policy.yaml"],
        )
        self.flux("./deploy/app")
        with self.assertRaises(RenderError):
            self.detect()

        self.kustomization(
            "deploy/app",
            replacements=[
                {
                    "path": (
                        "http://127.0.0.1:65535/replacement.yaml"
                    )
                }
            ],
        )
        with self.assertRaises(RenderError):
            self.detect()

        self.kustomization(
            "deploy/app",
            generators=["generator"],
        )
        self.kustomization(
            "deploy/app/generator",
            resources=["github.com/example/generator"],
        )
        (self.root / "deploy/app/generator/github.com/example/generator").mkdir(
            parents=True,
        )
        with self.assertRaises(RenderError):
            self.detect()

    def test_flux_component_graph_cannot_hide_remote_resources(self) -> None:
        self.job("deploy/app/job.yaml")
        self.kustomization("deploy/app", resources=["job.yaml"])
        self.write_json(
            "deploy/component/kustomization.yaml",
            {
                "apiVersion": "kustomize.config.k8s.io/v1alpha1",
                "kind": "Component",
                "resources": ["github.com/example/component"],
            },
        )
        (self.root / "deploy/component/github.com/example/component").mkdir(
            parents=True,
        )
        self.flux(
            "./deploy/app",
            spec={"components": ["../component"]},
        )
        with self.assertRaises(RenderError):
            self.detect()

    def test_kustomize_file_plugin_cannot_fetch_nested_remote_input(self) -> None:
        hits: list[str] = []

        class PatchHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                hits.append(self.path)
                content = textwrap.dedent(
                    """
                    apiVersion: batch/v1
                    kind: Job
                    metadata:
                      name: catalog-maintenance
                      labels:
                        role: iceberg-table-bootstrap
                    """
                ).lstrip().encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/yaml")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def log_message(self, _format: str, *args: object) -> None:
                del args

        server = ThreadingHTTPServer(("127.0.0.1", 0), PatchHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            self.job("deploy/app/job.yaml")
            self.write(
                "deploy/app/transformer.yaml",
                f"""
                apiVersion: builtin
                kind: PatchTransformer
                metadata:
                  name: remote
                path: http://127.0.0.1:{port}/patch.yaml
                target:
                  kind: Job
                """,
            )
            self.kustomization(
                "deploy/app",
                resources=["job.yaml"],
                transformers=["transformer.yaml"],
            )
            self.flux("./deploy/app")
            with self.assertRaises(RenderError):
                self.detect()
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
        self.assertEqual([], hits)

    def test_native_kustomization_symlink_cannot_escape_source_root(self) -> None:
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        outside_path = Path(outside.name) / "kustomization.yaml"
        outside_path.write_text(
            """
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources: [job.yaml]
""".lstrip(),
            encoding="utf-8",
        )
        self.job("deploy/app/job.yaml")
        (self.root / "deploy/app/kustomization.yaml").symlink_to(outside_path)
        self.flux("./deploy/app")
        with self.assertRaises(RenderError):
            self.detect()

    def test_manifest_symlink_cannot_escape_scan_root(self) -> None:
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        outside_path = Path(outside.name) / "job.yaml"
        outside_path.write_text(
            """
apiVersion: batch/v1
kind: Job
metadata:
  name: iceberg-table-bootstrap
""".lstrip(),
            encoding="utf-8",
        )
        (self.root / "deploy/escaped.yaml").symlink_to(outside_path)
        with self.assertRaises(RenderError):
            self.detect()

    def test_invalid_native_kustomization_fails_closed(self) -> None:
        self.job("deploy/app/job.yaml")
        self.write(
            "deploy/app/kustomization.yaml",
            """
            apiVersion: kustomize.config.k8s.io/v1beta1
            kind: Kustomization
            resources: [missing.yaml]
            """,
        )
        self.flux("./deploy/app")
        with self.assertRaises(RenderError):
            self.detect()


class HelmRenderingTests(RepositoryFixture):
    def setUp(self) -> None:
        super().setUp()
        self.local_source()
        self.chart()

    def test_generated_values_flow_through_reachable_flux_state(self) -> None:
        self.write(
            "deploy/app/bootstrap.env",
            "jobName=iceberg-table-bootstrap",
        )
        self.helm_release(
            relative="deploy/app/release.yaml",
            values_from=[
                {
                    "name": "generated",
                    "valuesKey": "jobName",
                    "targetPath": "job.name",
                }
            ],
        )
        self.kustomization(
            "deploy/app",
            resources=["release.yaml"],
            configMapGenerator=[
                {"name": "generated", "envs": ["bootstrap.env"]}
            ],
            generatorOptions={"disableNameSuffixHash": True},
        )
        flux = self.flux(
            "./deploy/app",
            spec={"targetNamespace": "flux-system"},
        )
        self.assertEqual([flux.resolve()], self.detect())

    def test_parent_name_transform_changes_helm_release_identity(self) -> None:
        self.chart(
            template="""
            apiVersion: batch/v1
            kind: Job
            metadata:
              name: {{ .Release.Name }}
            spec:
              template:
                spec:
                  restartPolicy: Never
                  containers:
                    - name: runner
                      image: registry.example/runner:stable
            """,
        )
        self.helm_release(
            relative="deploy/app/release.yaml",
            name="catalog-maintenance",
        )
        self.kustomization("deploy/app", resources=["release.yaml"])
        flux = self.flux(
            "./deploy/app",
            spec={
                "namePrefix": "iceberg-",
                "nameSuffix": "-bootstrap",
            },
        )
        self.assertEqual([flux.resolve()], self.detect())

    def test_repository_local_chart_can_live_outside_charts_directory(self) -> None:
        chart = self.root / "charts/catalog-job"
        outside = self.root / "platform/job-chart"
        outside.parent.mkdir()
        chart.rename(outside)
        self.write_json(
            "platform/job-chart/values.yaml",
            {
                "job": {
                    "name": "iceberg-table-bootstrap",
                    "label": "catalog-maintenance",
                },
                "container": {"name": "runner"},
                "image": {
                    "repository": "registry.example/catalog-maintenance",
                    "tag": "stable",
                },
            },
        )
        release = self.helm_release(chart="./platform/job-chart")
        self.assertEqual([release.resolve()], self.detect())

    def test_split_repository_and_tag_are_rendered_as_one_image(self) -> None:
        release = self.helm_release(
            values={
                "image": {
                    "repository": "registry.example/iceberg-table",
                    "tag": "bootstrap",
                }
            }
        )
        self.assertEqual([release.resolve()], self.detect())

    def test_chart_and_source_url_tokens_are_not_workload_identity(self) -> None:
        token_url = "ssh://git@github.com/example/iceberg-bootstrap-platform"
        self.local_source(url=token_url)
        self.helm_release(chart="./charts/catalog-job")
        self.assertEqual([], self.detect(self.context(token_url)))

    def test_helm_controller_origin_labels_are_final_identity(self) -> None:
        release = self.helm_release(
            name="iceberg-table-bootstrap",
            relative="deploy/releases/origin.yaml",
        )
        self.assertEqual([release.resolve()], self.detect())

    def test_helm_origin_labels_override_common_metadata_collision(self) -> None:
        release = self.helm_release(
            name="iceberg-table-bootstrap",
            relative="deploy/releases/origin.yaml",
            common_metadata={
                "labels": {
                    "helm.toolkit.fluxcd.io/name": "catalog-maintenance"
                }
            },
        )
        self.assertEqual([release.resolve()], self.detect())

    def test_explicit_context_is_independent_of_checkout_origin(self) -> None:
        self.write(
            ".git/config",
            """
            [remote "origin"]
                url = ssh://git@github.com/example/unrelated-fork
            """,
        )
        self.helm_release(
            values={
                "job": {
                    "name": "iceberg-table-bootstrap",
                    "label": "catalog-maintenance",
                }
            }
        )
        self.assertTrue(self.detect(self.context(LOCAL_GIT_URL)))

    def test_external_source_does_not_read_same_named_local_chart(self) -> None:
        self.local_source(url="ssh://git@github.com/example/external")
        self.helm_release(
            values={
                "job": {
                    "name": "iceberg-table-bootstrap",
                    "label": "catalog-maintenance",
                }
            }
        )
        with self.assertRaises(RenderError):
            self.detect(self.context(LOCAL_GIT_URL))

    def test_inline_values_override_chart_defaults(self) -> None:
        self.chart(
            values={
                "job": {
                    "name": "iceberg-table-bootstrap",
                    "label": "iceberg-table-bootstrap",
                }
            }
        )
        self.helm_release(
            values={
                "job": {
                    "name": "catalog-maintenance",
                    "label": "catalog-maintenance",
                }
            }
        )
        self.assertEqual([], self.detect())

    def test_values_from_and_inline_values_combine_before_render(self) -> None:
        self.write_json(
            "deploy/releases/image-values.yaml",
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {
                    "name": "image-values",
                    "namespace": "flux-system",
                },
                "data": {
                    "values.yaml": """
                    image:
                      repository: registry.example/iceberg-table
                    """
                },
            },
        )
        release = self.helm_release(
            values={"image": {"tag": "bootstrap"}},
            values_from=[{"name": "image-values"}],
        )
        self.assertEqual([release.resolve()], self.detect())

    def test_target_path_overrides_inline_values_in_final_merge(self) -> None:
        self.write_json(
            "deploy/releases/tag.yaml",
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "tag", "namespace": "flux-system"},
                "data": {"value.yaml": "bootstrap"},
            },
        )
        release = self.helm_release(
            values={
                "image": {
                    "repository": "registry.example/iceberg-table",
                    "tag": "stable",
                }
            },
            values_from=[
                {
                    "name": "tag",
                    "valuesKey": "value.yaml",
                    "targetPath": "image.tag",
                }
            ],
        )
        self.assertEqual([release.resolve()], self.detect())

    def test_later_values_sources_and_inline_values_remove_prior_identity(self) -> None:
        self.write_json(
            "deploy/releases/bootstrap.yaml",
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {
                    "name": "bootstrap",
                    "namespace": "flux-system",
                },
                "data": {
                    "values.yaml": """
                    job:
                      name: iceberg-table-bootstrap
                    """
                },
            },
        )
        self.write_json(
            "deploy/releases/generic.yaml",
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {
                    "name": "generic",
                    "namespace": "flux-system",
                },
                "data": {
                    "values.yaml": """
                    job:
                      name: catalog-maintenance
                    """
                },
            },
        )
        self.helm_release(
            name="later-source",
            relative="deploy/releases/later-source.yaml",
            values_from=[
                {"name": "bootstrap"},
                {"name": "generic"},
            ],
        )
        self.helm_release(
            name="inline",
            relative="deploy/releases/inline.yaml",
            values_from=[{"name": "bootstrap"}],
            values={
                "job": {
                    "name": "catalog-maintenance",
                    "label": "catalog-maintenance",
                }
            },
        )
        self.assertEqual([], self.detect())

    def test_target_path_uses_helm_strvals_and_literal_semantics(self) -> None:
        self.write_json(
            "deploy/releases/repository.yaml",
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {
                    "name": "repository",
                    "namespace": "flux-system",
                },
                "data": {
                    "value.yaml": (
                        "registry.example/iceberg-table,side=bootstrap"
                    )
                },
            },
        )
        self.helm_release(
            name="parsed",
            relative="deploy/releases/parsed.yaml",
            values_from=[
                {
                    "name": "repository",
                    "valuesKey": "value.yaml",
                    "targetPath": "image.repository",
                }
            ],
        )
        literal = self.helm_release(
            name="literal",
            relative="deploy/releases/literal.yaml",
            values_from=[
                {
                    "name": "repository",
                    "valuesKey": "value.yaml",
                    "targetPath": "image.repository",
                    "literal": True,
                }
            ],
        )
        self.assertEqual([literal.resolve()], self.detect())

    def test_quoted_target_path_value_remains_a_string(self) -> None:
        self.chart(
            values={"flag": True},
            template="""
            apiVersion: batch/v1
            kind: Job
            metadata:
              name: catalog-maintenance
              labels:
                role: {{ ternary "iceberg-table-bootstrap" "catalog-maintenance" (kindIs "string" .Values.flag) | quote }}
            spec:
              template:
                spec:
                  restartPolicy: Never
                  containers:
                    - name: runner
                      image: registry.example/runner:stable
            """,
        )
        for name, value in (
            ("quoted", "'false'"),
            ("unquoted", "false"),
        ):
            self.write_json(
                f"deploy/releases/{name}-value.yaml",
                {
                    "apiVersion": "v1",
                    "kind": "ConfigMap",
                    "metadata": {
                        "name": f"{name}-value",
                        "namespace": "flux-system",
                    },
                    "data": {"value.yaml": value},
                },
            )
            self.helm_release(
                name=name,
                relative=f"deploy/releases/{name}.yaml",
                values_from=[
                    {
                        "name": f"{name}-value",
                        "valuesKey": "value.yaml",
                        "targetPath": "flag",
                    }
                ],
            )
        self.assertEqual(
            [(self.root / "deploy/releases/quoted.yaml").resolve()],
            self.detect(),
        )

    def test_target_path_applies_after_inline_values(self) -> None:
        self.write_json(
            "deploy/releases/name.yaml",
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {
                    "name": "name",
                    "namespace": "flux-system",
                },
                "data": {"value.yaml": "catalog-maintenance"},
            },
        )
        self.helm_release(
            values={
                "job": {
                    "name": "iceberg-table-bootstrap",
                    "label": "catalog-maintenance",
                }
            },
            values_from=[
                {
                    "name": "name",
                    "valuesKey": "value.yaml",
                    "targetPath": "job.name",
                }
            ],
        )
        self.assertEqual([], self.detect())

    def test_secret_values_are_decoded_before_render(self) -> None:
        encoded = base64.b64encode(
            b"job:\n  name: iceberg-table-bootstrap\n"
        ).decode("ascii")
        self.write_json(
            "deploy/releases/secret.yaml",
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {
                    "name": "release-values",
                    "namespace": "flux-system",
                },
                "data": {"values.yaml": encoded},
            },
        )
        release = self.helm_release(
            values_from=[
                {"kind": "Secret", "name": "release-values"}
            ]
        )
        self.assertEqual([release.resolve()], self.detect())

    def test_wrapped_secret_data_and_string_data_are_supported(self) -> None:
        encoded = base64.b64encode(
            b"job:\n  name: iceberg-table-bootstrap\n"
        ).decode("ascii")
        wrapped = "\n".join(
            encoded[offset : offset + 16]
            for offset in range(0, len(encoded), 16)
        )
        self.write_json(
            "deploy/releases/wrapped-secret.yaml",
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {
                    "name": "wrapped",
                    "namespace": "flux-system",
                },
                "data": {"values.yaml": wrapped},
            },
        )
        wrapped_release = self.helm_release(
            values_from=[{"kind": "Secret", "name": "wrapped"}],
            name="wrapped",
            relative="deploy/releases/wrapped.yaml",
        )
        self.write_json(
            "deploy/releases/string-secret.yaml",
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {
                    "name": "string",
                    "namespace": "flux-system",
                },
                "stringData": {
                    "values.yaml": """
                    job:
                      name: iceberg-table-bootstrap
                    """
                },
            },
        )
        string_release = self.helm_release(
            values_from=[{"kind": "Secret", "name": "string"}],
            name="string",
            relative="deploy/releases/string.yaml",
        )
        self.assertEqual(
            {wrapped_release.resolve(), string_release.resolve()},
            set(self.detect()),
        )

    def test_selected_values_files_are_rendered_and_sidecars_are_ignored(self) -> None:
        self.chart(
            extra_values={
                "bootstrap.yaml": {
                    "job": {
                        "name": "iceberg-table-bootstrap",
                        "label": "catalog-maintenance",
                    },
                    "container": {"name": "runner"},
                    "image": {
                        "repository": "registry.example/catalog-maintenance",
                        "tag": "stable",
                    },
                },
                "unselected.yaml": {
                    "job": {
                        "name": "iceberg-unselected-bootstrap",
                        "label": "catalog-maintenance",
                    }
                },
            }
        )
        selected = self.helm_release(
            chart_fields={
                "valuesFiles": ["./charts/catalog-job/bootstrap.yaml"]
            },
            relative="deploy/releases/selected.yaml",
            name="selected",
        )
        self.assertEqual([selected.resolve()], self.detect())

    def test_direct_chart_source_reference_uses_exact_api_version(self) -> None:
        source_ref = {
            "apiVersion": "source.toolkit.fluxcd.io/v1",
            "kind": "GitRepository",
            "name": "source",
            "namespace": "flux-system",
        }
        self.helm_release(chart_fields={"sourceRef": source_ref})
        self.assertEqual([], self.detect())
        self.helm_release(
            chart_fields={
                "sourceRef": {
                    **source_ref,
                    "apiVersion": "source.toolkit.fluxcd.io/v1beta2",
                }
            }
        )
        with self.assertRaises(RenderError):
            self.detect()

    def test_helm_chart_and_values_paths_must_be_relative(self) -> None:
        self.helm_release(chart=str(self.root / "charts/catalog-job"))
        with self.assertRaises(RenderError):
            self.detect()
        self.helm_release(
            chart_fields={
                "valuesFiles": [
                    str(self.root / "charts/catalog-job/values.yaml")
                ]
            },
        )
        with self.assertRaises(RenderError):
            self.detect()

    def test_helm_fields_follow_crd_types_and_enum_casing(self) -> None:
        cases = (
            {"chart_fields": {"reconcileStrategy": "revision"}},
            {"spec_fields": {"postRenderStrategy": "Combined"}},
            {"spec_fields": {"releaseName": 7}},
            {"spec_fields": {"targetNamespace": 7}},
        )
        for fields in cases:
            with self.subTest(fields=fields):
                self.helm_release(**fields)
                with self.assertRaises(RenderError):
                    self.detect()

    def test_chart_ref_resolves_local_helmchart_provenance(self) -> None:
        self.write_json(
            "deploy/releases/chart.yaml",
            {
                "apiVersion": "source.toolkit.fluxcd.io/v1",
                "kind": "HelmChart",
                "metadata": {
                    "name": "catalog-chart",
                    "namespace": "flux-system",
                },
                "spec": {
                    "chart": "./charts/catalog-job",
                    "reconcileStrategy": "Revision",
                    "sourceRef": {
                        "apiVersion": "source.toolkit.fluxcd.io/v1",
                        "kind": "GitRepository",
                        "name": "source",
                    },
                },
            },
        )
        release = self.helm_release(
            chart_ref={
                "apiVersion": "source.toolkit.fluxcd.io/v1",
                "kind": "HelmChart",
                "name": "catalog-chart",
            },
            values={
                "job": {
                    "name": "iceberg-table-bootstrap",
                    "label": "catalog-maintenance",
                }
            },
        )
        self.assertEqual([release.resolve()], self.detect())

    def test_helmchart_source_reference_rejects_invalid_shape(self) -> None:
        cases = (
            {
                "apiVersion": "source.toolkit.fluxcd.io/v1beta2",
                "kind": "GitRepository",
                "name": "source",
            },
            {
                "apiVersion": "source.toolkit.fluxcd.io/v1",
                "kind": "GitRepository",
                "name": "source",
                "namespace": "flux-system",
            },
        )
        for source_ref in cases:
            with self.subTest(source_ref=source_ref):
                self.write_json(
                    "deploy/releases/chart.yaml",
                    {
                        "apiVersion": "source.toolkit.fluxcd.io/v1",
                        "kind": "HelmChart",
                        "metadata": {
                            "name": "catalog-chart",
                            "namespace": "flux-system",
                        },
                        "spec": {
                            "chart": "./charts/catalog-job",
                            "reconcileStrategy": "Revision",
                            "sourceRef": source_ref,
                        },
                    },
                )
                self.helm_release(
                    chart_ref={
                        "kind": "HelmChart",
                        "name": "catalog-chart",
                    },
                )
                with self.assertRaises(RenderError):
                    self.detect()

    def test_chart_ref_honors_ignore_missing_values_files(self) -> None:
        self.chart(
            values={"job": {"name": "iceberg-table-bootstrap"}},
            template="""
            {{- $job := default dict .Values.job }}
            apiVersion: batch/v1
            kind: Job
            metadata:
              name: {{ default "catalog-maintenance" $job.name | quote }}
            spec:
              template:
                spec:
                  restartPolicy: Never
                  containers:
                    - name: runner
                      image: registry.example/runner:stable
            """,
        )
        self.write_json(
            "deploy/releases/chart.yaml",
            {
                "apiVersion": "source.toolkit.fluxcd.io/v1",
                "kind": "HelmChart",
                "metadata": {
                    "name": "catalog-chart",
                    "namespace": "flux-system",
                },
                "spec": {
                    "chart": "./charts/catalog-job",
                    "reconcileStrategy": "Revision",
                    "ignoreMissingValuesFiles": True,
                    "valuesFiles": ["missing.yaml"],
                    "sourceRef": {
                        "kind": "GitRepository",
                        "name": "source",
                    },
                },
            },
        )
        self.helm_release(
            chart_ref={
                "kind": "HelmChart",
                "name": "catalog-chart",
            }
        )
        self.assertEqual([], self.detect())

    def test_chart_ref_requires_a_live_revision_artifact(self) -> None:
        self.helm_release(
            chart_ref={
                "kind": "HelmChart",
                "name": "catalog-chart",
            }
        )
        for label, contract in (
            (
                "suspended",
                {"reconcileStrategy": "Revision", "suspend": True},
            ),
            (
                "chart-version",
                {"reconcileStrategy": "ChartVersion"},
            ),
        ):
            with self.subTest(label=label):
                self.write_json(
                    "deploy/releases/chart.yaml",
                    {
                        "apiVersion": "source.toolkit.fluxcd.io/v1",
                        "kind": "HelmChart",
                        "metadata": {
                            "name": "catalog-chart",
                            "namespace": "flux-system",
                        },
                        "spec": {
                            "chart": "./charts/catalog-job",
                            "sourceRef": {
                                "kind": "GitRepository",
                                "name": "source",
                            },
                            **contract,
                        },
                    },
                )
                with self.assertRaises(RenderError):
                    self.detect()

    def test_post_renderer_mapping_patch_mutates_final_job(self) -> None:
        release = self.helm_release(
            post_renderers=[
                {
                    "kustomize": {
                        "patches": [
                            {
                                "target": {"kind": "Job"},
                                "patch": """
                                - op: add
                                  path: /metadata/labels
                                  value:
                                    role: iceberg-table-bootstrap
                                """,
                            }
                        ]
                    }
                }
            ]
        )
        self.assertEqual([release.resolve()], self.detect())

    def test_post_renderer_rejects_fields_outside_flux_api(self) -> None:
        self.helm_release(
            post_renderers=[
                {
                    "kustomize": {
                        "namePrefix": "iceberg-",
                        "nameSuffix": "-bootstrap",
                    }
                }
            ]
        )
        with self.assertRaises(RenderError):
            self.detect()

    def test_post_renderer_non_identity_patch_is_not_a_token_scan(self) -> None:
        self.helm_release(
            post_renderers=[
                {
                    "kustomize": {
                        "patches": [
                            {
                                "target": {"kind": "Job"},
                                "patch": """
                                - op: add
                                  path: /metadata/annotations/note
                                  value: iceberg-table-bootstrap
                                """,
                            }
                        ]
                    }
                }
            ]
        )
        self.assertEqual([], self.detect())

    def test_post_renderer_target_must_match_rendered_workload(self) -> None:
        self.helm_release(
            post_renderers=[
                {
                    "kustomize": {
                        "patches": [
                            {
                                "target": {"kind": "ConfigMap"},
                                "patch": """
                                - op: add
                                  path: /metadata/annotations/note
                                  value: iceberg-table-bootstrap
                                """,
                            }
                        ]
                    }
                }
            ]
        )
        self.assertEqual([], self.detect())

    def test_post_renderer_cannot_fetch_a_patch_path(self) -> None:
        hits: list[str] = []

        class PatchHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                hits.append(self.path)
                content = textwrap.dedent(
                    """
                    apiVersion: batch/v1
                    kind: Job
                    metadata:
                      name: catalog-maintenance
                      labels:
                        role: iceberg-table-bootstrap
                    """
                ).lstrip().encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/yaml")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def log_message(self, _format: str, *args: object) -> None:
                del args

        server = ThreadingHTTPServer(("127.0.0.1", 0), PatchHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            self.helm_release(
                post_renderers=[
                    {
                        "kustomize": {
                            "patches": [
                                {
                                    "path": (
                                        f"http://127.0.0.1:{port}/patch.yaml"
                                    ),
                                    "target": {"kind": "Job"},
                                }
                            ]
                        }
                    }
                ]
            )
            with self.assertRaises(RenderError):
                self.detect()
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
        self.assertEqual([], hits)

    def test_post_renderer_fields_match_the_flux_api(self) -> None:
        invalid_renderers = (
            {
                "kustomize": {
                    "patches": [
                        {
                            "patch": "{}",
                            "target": {"kind": "Job", "path": "patch.yaml"},
                        }
                    ]
                }
            },
            {
                "kustomize": {
                    "images": [
                        {
                            "name": "registry.example/catalog-maintenance",
                            "newTag": 1,
                        }
                    ]
                }
            },
        )
        for renderer in invalid_renderers:
            with self.subTest(renderer=renderer):
                self.helm_release(post_renderers=[renderer])
                with self.assertRaises(RenderError):
                    self.detect()

    def test_post_renderer_image_rewrites_use_final_value(self) -> None:
        self.helm_release(
            values={
                "image": {
                    "repository": "registry.example/iceberg-table",
                    "tag": "bootstrap",
                }
            },
            post_renderers=[
                {
                    "kustomize": {
                        "images": [
                            {
                                "name": "registry.example/iceberg-table",
                                "newName": "registry.example/catalog-maintenance",
                                "newTag": "stable",
                            }
                        ]
                    }
                }
            ],
            name="away",
            relative="deploy/releases/away.yaml",
        )
        toward = self.helm_release(
            post_renderers=[
                {
                    "kustomize": {
                        "images": [
                            {
                                "name": "registry.example/catalog-maintenance",
                                "newName": "registry.example/iceberg-table",
                                "newTag": "bootstrap",
                            }
                        ]
                    }
                }
            ],
            name="toward",
            relative="deploy/releases/toward.yaml",
        )
        self.assertEqual([toward.resolve()], self.detect())

    def test_common_metadata_applies_after_post_renderers(self) -> None:
        release = self.helm_release(
            post_renderers=[
                {
                    "kustomize": {
                        "patches": [
                            {
                                "target": {"kind": "Job"},
                                "patch": """
                                - op: add
                                  path: /metadata/labels/role
                                  value: catalog-maintenance
                                """,
                            }
                        ]
                    }
                }
            ],
            common_metadata={
                "labels": {"role": "iceberg-table-bootstrap"}
            },
        )
        self.assertEqual([release.resolve()], self.detect())

    def test_suspended_helm_release_does_not_render(self) -> None:
        self.helm_release(
            values={
                "job": {
                    "name": "iceberg-table-bootstrap",
                    "label": "catalog-maintenance",
                }
            },
            suspend=True,
        )
        self.assertEqual([], self.detect())

    def test_unused_values_and_template_helpers_are_not_identity(self) -> None:
        self.write(
            "charts/catalog-job/templates/_helpers.tpl",
            """
            {{- define "unused.identity" -}}
            iceberg-table-bootstrap
            {{- end -}}
            """,
        )
        self.helm_release(
            values={"unrelated": "iceberg-table-bootstrap"}
        )
        self.assertEqual([], self.detect())

    def test_helm_test_hook_is_not_part_of_reconciled_workload(self) -> None:
        self.write(
            "charts/catalog-job/templates/test.yaml",
            """
            apiVersion: batch/v1
            kind: Job
            metadata:
              name: iceberg-table-bootstrap
              annotations:
                helm.sh/hook: test
            spec:
              template:
                spec:
                  restartPolicy: Never
                  containers:
                    - name: runner
                      image: registry.example/runner:stable
            """,
        )
        self.helm_release()
        self.assertEqual([], self.detect())

    def test_non_reconcile_hooks_do_not_count_as_current_workloads(self) -> None:
        self.write(
            "charts/catalog-job/templates/job.yaml",
            """
            apiVersion: batch/v1
            kind: Job
            metadata:
              name: iceberg-table-bootstrap
              annotations:
                helm.sh/hook: pre-delete,post-rollback
            spec:
              template:
                spec:
                  restartPolicy: Never
                  containers:
                    - name: runner
                      image: registry.example/runner:stable
            """,
        )
        self.helm_release()
        self.assertEqual([], self.detect())

    def test_bootstrap_install_hook_fails_closed_without_release_history(self) -> None:
        self.write(
            "charts/catalog-job/templates/job.yaml",
            """
            apiVersion: batch/v1
            kind: Job
            metadata:
              name: iceberg-table-bootstrap
              annotations:
                helm.sh/hook: pre-install
            spec:
              template:
                spec:
                  restartPolicy: Never
                  containers:
                    - name: runner
                      image: registry.example/runner:stable
            """,
        )
        self.helm_release()
        with self.assertRaises(RenderError):
            self.detect()

    def test_disabled_install_hook_is_not_reconciled(self) -> None:
        self.write(
            "charts/catalog-job/templates/job.yaml",
            """
            apiVersion: batch/v1
            kind: Job
            metadata:
              name: iceberg-table-bootstrap
              annotations:
                helm.sh/hook: pre-install
            spec:
              template:
                spec:
                  restartPolicy: Never
                  containers:
                    - name: runner
                      image: registry.example/runner:stable
            """,
        )
        self.helm_release(
            spec_fields={"install": {"disableHooks": True}},
        )
        self.assertEqual([], self.detect())

    def test_enabled_helm_test_hook_is_part_of_controller_lifecycle(self) -> None:
        self.write(
            "charts/catalog-job/templates/job.yaml",
            """
            apiVersion: batch/v1
            kind: Job
            metadata:
              name: iceberg-table-bootstrap
              annotations:
                helm.sh/hook: test
            spec:
              template:
                spec:
                  restartPolicy: Never
                  containers:
                    - name: runner
                      image: registry.example/runner:stable
            """,
        )
        release = self.helm_release(
            spec_fields={"test": {"enable": True}},
        )
        self.assertEqual([release.resolve()], self.detect())

    def test_helm_test_filters_follow_exclude_then_include_order(self) -> None:
        self.write(
            "charts/catalog-job/templates/job.yaml",
            """
            apiVersion: batch/v1
            kind: Job
            metadata:
              name: iceberg-table-bootstrap
              annotations:
                helm.sh/hook: test
            spec:
              template:
                spec:
                  restartPolicy: Never
                  containers:
                    - name: runner
                      image: registry.example/runner:stable
            """,
        )
        cases = (
            (
                "excluded",
                [{"name": "iceberg-table-bootstrap", "exclude": True}],
                False,
            ),
            (
                "not-included",
                [{"name": "another-test"}],
                False,
            ),
            (
                "included",
                [{"name": "iceberg-table-bootstrap"}],
                True,
            ),
            (
                "exclude-wins",
                [
                    {"name": "iceberg-table-bootstrap"},
                    {
                        "name": "iceberg-table-bootstrap",
                        "exclude": True,
                    },
                ],
                False,
            ),
        )
        for label, filters, expected in cases:
            with self.subTest(label=label):
                release = self.helm_release(
                    spec_fields={
                        "test": {"enable": True, "filters": filters}
                    },
                )
                actual = self.detect()
                self.assertEqual(
                    [release.resolve()] if expected else [],
                    actual,
                )

    def test_helm_test_filter_shape_fails_closed(self) -> None:
        cases = (
            "not-a-list",
            [{}],
            [{"name": "test", "exclude": "true"}],
            [{"name": "test", "unknown": True}],
        )
        for filters in cases:
            with self.subTest(filters=filters):
                self.helm_release(
                    spec_fields={
                        "test": {"enable": True, "filters": filters}
                    },
                )
                with self.assertRaises(RenderError):
                    self.detect()

    def test_upgrade_preserve_values_fails_without_release_history(self) -> None:
        self.helm_release(
            spec_fields={"upgrade": {"preserveValues": True}},
        )
        with self.assertRaises(RenderError):
            self.detect()

    def test_non_combined_post_render_strategy_fails_closed(self) -> None:
        self.helm_release(
            spec_fields={"postRenderStrategy": "nohooks"},
        )
        with self.assertRaises(RenderError):
            self.detect()

    def test_escaped_target_path_components_are_preserved(self) -> None:
        self.write_json(
            "deploy/releases/label.yaml",
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "label", "namespace": "flux-system"},
                "data": {"value.yaml": "iceberg-table-bootstrap"},
            },
        )
        template = """
        apiVersion: batch/v1
        kind: Job
        metadata:
          name: catalog-maintenance
          labels:
            role: {{ index .Values.labels "app.kubernetes.io/name" | quote }}
        spec:
          template:
            spec:
              restartPolicy: Never
              containers:
                - name: runner
                  image: registry.example/runner:stable
        """
        self.chart(
            values={
                "labels": {
                    "app.kubernetes.io/name": "catalog-maintenance"
                }
            },
            template=template,
        )
        release = self.helm_release(
            values_from=[
                {
                    "name": "label",
                    "valuesKey": "value.yaml",
                    "targetPath": r"labels.app\.kubernetes\.io/name",
                }
            ]
        )
        self.assertEqual([release.resolve()], self.detect())

    def test_literal_target_preserves_path_escapes_and_metacharacters(self) -> None:
        literal_value = r"a,b=[c]\d"
        self.write_json(
            "deploy/releases/literal-config.yaml",
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {
                    "name": "literal-config",
                    "namespace": "flux-system",
                },
                "data": {"value.yaml": literal_value},
            },
        )
        self.chart(
            values={
                "externalConfig": {
                    "application.yml": {"content": "generic"}
                }
            },
            template=f"""
            apiVersion: batch/v1
            kind: Job
            metadata:
              name: catalog-maintenance
              labels:
                role: {{{{ ternary "iceberg-table-bootstrap" "catalog-maintenance" (eq (index .Values.externalConfig "application.yml" "content") {json.dumps(literal_value)}) | quote }}}}
            spec:
              template:
                spec:
                  restartPolicy: Never
                  containers:
                    - name: runner
                      image: registry.example/runner:stable
            """,
        )
        release = self.helm_release(
            values_from=[
                {
                    "name": "literal-config",
                    "valuesKey": "value.yaml",
                    "targetPath": r"externalConfig.application\.yml.content",
                    "literal": True,
                }
            ]
        )
        self.assertEqual([release.resolve()], self.detect())

    def test_wrong_api_group_cannot_supply_helm_values(self) -> None:
        self.write_json(
            "deploy/releases/fake-values.yaml",
            {
                "apiVersion": "example.com/v1",
                "kind": "ConfigMap",
                "metadata": {
                    "name": "fake-values",
                    "namespace": "flux-system",
                },
                "data": {
                    "values.yaml": """
                    job:
                      name: iceberg-table-bootstrap
                    """
                },
            },
        )
        self.helm_release(values_from=[{"name": "fake-values"}])
        with self.assertRaises(RenderError):
            self.detect()

    def test_values_reference_cannot_escape_release_namespace(self) -> None:
        self.write_json(
            "deploy/releases/other-values.yaml",
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {
                    "name": "other-values",
                    "namespace": "other",
                },
                "data": {
                    "values.yaml": """
                    job:
                      name: iceberg-table-bootstrap
                    """
                },
            },
        )
        self.helm_release(
            values_from=[
                {
                    "name": "other-values",
                    "namespace": "other",
                }
            ]
        )
        with self.assertRaises(RenderError):
            self.detect()

    def test_values_reference_requires_a_typed_kind(self) -> None:
        for label, reference in (
            ("missing", {"name": "values"}),
            ("unknown", {"kind": "Example", "name": "values"}),
        ):
            with self.subTest(label=label):
                self.helm_release(
                    spec_fields={"valuesFrom": [reference]},
                )
                with self.assertRaises(RenderError):
                    self.detect()

    def test_conflicting_values_resources_fail_closed(self) -> None:
        for filename, name in (
            ("first", "catalog-maintenance"),
            ("second", "iceberg-table-bootstrap"),
        ):
            self.write_json(
                f"deploy/releases/{filename}.yaml",
                {
                    "apiVersion": "v1",
                    "kind": "ConfigMap",
                    "metadata": {
                        "name": "values",
                        "namespace": "flux-system",
                    },
                    "data": {
                        "values.yaml": f"job:\n  name: {name}\n"
                    },
                },
            )
        self.helm_release(
            values_from=[{"name": "values"}],
        )
        with self.assertRaises(RenderError):
            self.detect()

    def test_git_url_non_default_port_is_part_of_source_identity(self) -> None:
        source_url = "ssh://git@example.com:2222/platform/shirokuma.git"
        other_port = "ssh://git@example.com:2223/platform/shirokuma.git"
        self.local_source(url=source_url)
        self.helm_release()
        with self.assertRaises(RenderError):
            self.detect(self.context(other_port))

    def test_repository_context_authorizes_url_branch_pairs(self) -> None:
        first = "ssh://git@example.com/platform/first.git"
        second = "ssh://git@example.com/platform/second.git"
        context = RepositoryContext.create(
            self.root,
            (first, second),
            ("main", "release"),
        )
        self.write_json(
            "deploy/control/source.yaml",
            {
                "apiVersion": "source.toolkit.fluxcd.io/v1",
                "kind": "GitRepository",
                "metadata": {
                    "name": "source",
                    "namespace": "flux-system",
                },
                "spec": {
                    "interval": "1m",
                    "url": first,
                    "ref": {"branch": "release"},
                },
            },
        )
        self.helm_release()
        with self.assertRaises(RenderError):
            self.detect(context)
        self.write_json(
            "deploy/control/source.yaml",
            {
                "apiVersion": "source.toolkit.fluxcd.io/v1",
                "kind": "GitRepository",
                "metadata": {
                    "name": "source",
                    "namespace": "flux-system",
                },
                "spec": {
                    "interval": "1m",
                    "url": second,
                    "ref": {"branch": "release"},
                },
            },
        )
        self.assertEqual([], self.detect(context))

    def test_helm_chart_symlink_cannot_escape_source_root(self) -> None:
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        outside_template = Path(outside.name) / "job.yaml"
        outside_template.write_text(
            """
apiVersion: batch/v1
kind: Job
metadata:
  name: iceberg-table-bootstrap
""".lstrip(),
            encoding="utf-8",
        )
        template = self.root / "charts/catalog-job/templates/job.yaml"
        template.unlink()
        template.symlink_to(outside_template)
        self.helm_release()
        with self.assertRaises(RenderError):
            self.detect()

    def test_cluster_dependent_helm_templates_fail_closed(self) -> None:
        cases = {
            "lookup": (
                '{{- if not (lookup "v1" "ConfigMap" "default" "state") }}'
            ),
            "capabilities": (
                "{{- if .Capabilities.APIVersions }}"
            ),
            "release-history": (
                "{{- if .Release.IsUpgrade }}"
            ),
            "dynamic-template": (
                '{{- if (tpl "{{ lookup }}" .) }}'
            ),
            "indirect-capabilities": (
                '{{- $caps := index . "Capabilities" }}'
                "{{- if $caps.APIVersions }}"
            ),
            "random": (
                '{{- if eq (randNumeric 1) "0" }}'
            ),
            "random-int": (
                "{{- if eq (randInt 0 2) 0 }}"
            ),
            "dynamic-capabilities-root": (
                '{{- $capKey := printf "%s%s" "Capabil" "ities" }}'
                "{{- $caps := index . $capKey }}"
                '{{- if $caps.APIVersions.Has "iceberg.example/v1" }}'
            ),
            "dynamic-release-root": (
                '{{- $releaseKey := printf "%s%s" "Rel" "ease" }}'
                "{{- $release := index . $releaseKey }}"
                "{{- if $release.IsUpgrade }}"
            ),
            "serialized-root-context": (
                "{{- $context := toJson . }}"
                '{{- if contains "iceberg.example/v1" $context }}'
            ),
            "serialized-release-context": (
                "{{- $release := toJson .Release }}"
                r'{{- if contains "\"IsUpgrade\":true" $release }}'
            ),
            "quoted-closing-delimiter": (
                '{{- if and (printf "}}") '
                '(lookup "v1" "ConfigMap" "default" "gate") }}'
            ),
            "quoted-comment-delimiters": (
                '{{- if and (printf "/*") '
                '(lookup "v1" "ConfigMap" "default" "gate") '
                '(printf "*/") }}'
            ),
        }
        for label, expression in cases.items():
            with self.subTest(label=label):
                self.write(
                    "charts/catalog-job/templates/job.yaml",
                    f"""
                    {expression}
                    apiVersion: batch/v1
                    kind: Job
                    metadata:
                      name: catalog-maintenance
                    {{"{{"}}- end {{"}}"}}
                    """,
                )
                self.helm_release()
                with self.assertRaises(RenderError):
                    self.detect()

    def test_nonhermetic_helm_function_families_fail_closed(self) -> None:
        expressions = {
            "elapsed-time": (
                '{{ ago (toDate "2006-01-02" "2020-01-01") }}'
            ),
            "duration-round": (
                '{{ durationRound (toDate "2006-01-02" "2020-01-01") }}'
            ),
            "date-zone": (
                '{{ dateInZone "2006" '
                '(toDate "2006-01-02" "2020-01-01") "UTC" }}'
            ),
            "certificate-with-key": (
                '{{ genCAWithKey "ca" 1 "private-key" }}'
            ),
        }
        for label, expression in expressions.items():
            with self.subTest(label=label):
                self.write(
                    "charts/catalog-job/templates/runtime.conf",
                    expression,
                )
                self.helm_release()
                with self.assertRaises(RenderError):
                    self.detect()

    def test_function_like_values_and_literals_remain_deterministic(self) -> None:
        self.chart(
            values={
                "date": "catalog-maintenance",
                "lookup": "ordinary-value",
                "keys": "ordinary-keys",
            },
            template="""
            apiVersion: batch/v1
            kind: Job
            metadata:
              name: catalog-maintenance
              labels:
                role: {{ .Values.date | quote }}
              annotations:
                lookup: {{ .Values.lookup | quote }}
                keys: {{ .Values.keys | quote }}
                note: {{ ".Capabilities" | quote }}
            spec:
              template:
                spec:
                  restartPolicy: Never
                  containers:
                    - name: runner
                      image: registry.example/runner:stable
            """,
        )
        self.helm_release()
        self.assertEqual([], self.detect())

    def test_chart_kube_version_constraint_fails_closed(self) -> None:
        self.helm_release()
        for label, metadata in (
            (
                "block",
                """
                apiVersion: v2
                name: catalog-job
                version: 0.1.0
                kubeVersion: ">= 1.30.0"
                """,
            ),
            (
                "flow",
                """
                {"apiVersion":"v2","name":"catalog-job","version":"0.1.0","kubeVersion":">= 1.30.0"}
                """,
            ),
        ):
            with self.subTest(label=label):
                self.write(
                    "charts/catalog-job/Chart.yaml",
                    metadata,
                )
                with self.assertRaises(RenderError):
                    self.detect()

    def test_helm_values_schema_cannot_fetch_remote_references(self) -> None:
        hits: list[str] = []

        class SchemaHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                hits.append(self.path)
                content = b'{"type":"object"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/schema+json")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def log_message(self, _format: str, *args: object) -> None:
                del args

        server = ThreadingHTTPServer(("127.0.0.1", 0), SchemaHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            self.write_json(
                "charts/catalog-job/values.schema.json",
                {"$ref": f"http://127.0.0.1:{port}/schema.json"},
            )
            self.helm_release()
            with self.assertRaises(RenderError):
                self.detect()
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
        self.assertEqual([], hits)

    def test_helm_values_schema_identifiers_must_be_fragment_local(self) -> None:
        for field in ("$id", "id"):
            with self.subTest(field=field):
                self.write_json(
                    "charts/catalog-job/values.schema.json",
                    {
                        field: "https://schemas.example/catalog.json",
                        "type": "object",
                    },
                )
                self.helm_release()
                with self.assertRaises(RenderError):
                    self.detect()

    def test_cluster_dependency_in_arbitrary_template_extension_fails_closed(self) -> None:
        self.write(
            "charts/catalog-job/templates/runtime.conf",
            '{{ lookup "v1" "ConfigMap" "default" "state" }}',
        )
        self.helm_release()
        with self.assertRaises(RenderError):
            self.detect()

    def test_helmignore_excludes_unloaded_dynamic_templates(self) -> None:
        self.write(
            "charts/catalog-job/.helmignore",
            "templates/ignored.yaml",
        )
        self.write(
            "charts/catalog-job/templates/ignored.yaml",
            """
            {{- if .Capabilities.APIVersions }}
            apiVersion: batch/v1
            kind: Job
            metadata:
              name: iceberg-table-bootstrap
            {{- end }}
            """,
        )
        self.helm_release()
        self.assertEqual([], self.detect())

    def test_packaged_helm_dependency_fails_closed(self) -> None:
        self.write(
            "charts/catalog-job/charts/dependency.tgz",
            "not-a-real-archive",
        )
        self.helm_release()
        with self.assertRaises(RenderError):
            self.detect()

    def test_active_controller_rendered_by_helm_fails_closed(self) -> None:
        controller_templates = {
            "kustomization": """
                apiVersion: kustomize.toolkit.fluxcd.io/v1
                kind: Kustomization
                metadata:
                  name: nested
                spec:
                  interval: 1m
                  path: ./deploy/app
                  sourceRef:
                    kind: GitRepository
                    name: source
            """,
            "helm-release": """
                apiVersion: helm.toolkit.fluxcd.io/v2
                kind: HelmRelease
                metadata:
                  name: nested
                spec:
                  interval: 1m
                  chart:
                    spec:
                      chart: ./charts/catalog-job
                      sourceRef:
                        kind: GitRepository
                        name: source
            """,
            "hook-kustomization": """
                apiVersion: kustomize.toolkit.fluxcd.io/v1
                kind: Kustomization
                metadata:
                  name: nested
                  annotations:
                    helm.sh/hook: pre-install
                spec:
                  interval: 1m
                  path: ./deploy/app
                  sourceRef:
                    kind: GitRepository
                    name: source
            """,
        }
        for label, template in controller_templates.items():
            with self.subTest(label=label):
                self.chart(template=template)
                self.helm_release()
                with self.assertRaises(RenderError):
                    self.detect()

    def test_suspended_controller_rendered_by_helm_is_inert(self) -> None:
        self.chart(
            template="""
            apiVersion: kustomize.toolkit.fluxcd.io/v1
            kind: Kustomization
            metadata:
              name: nested
            spec:
              interval: 1m
              suspend: true
              path: ./deploy/app
              sourceRef:
                kind: GitRepository
                name: source
            """,
        )
        self.helm_release()
        self.assertEqual([], self.detect())

    def test_disabled_controller_hook_rendered_by_helm_is_inert(self) -> None:
        self.chart(
            template="""
            apiVersion: kustomize.toolkit.fluxcd.io/v1
            kind: Kustomization
            metadata:
              name: nested
              annotations:
                helm.sh/hook: pre-install
            spec:
              interval: 1m
              path: ./deploy/app
              sourceRef:
                kind: GitRepository
                name: source
            """,
        )
        self.helm_release(
            spec_fields={"install": {"disableHooks": True}},
        )
        self.assertEqual([], self.detect())

    def test_missing_required_values_source_fails_closed(self) -> None:
        self.helm_release(values_from=[{"name": "missing"}])
        with self.assertRaises(RenderError):
            self.detect()

    def test_optional_values_source_only_ignores_a_missing_object(self) -> None:
        self.helm_release(
            values_from=[{"name": "missing", "optional": True}],
        )
        self.assertEqual([], self.detect())

        for kind, payload in (
            ("ConfigMap", {"data": {"other": "value"}}),
            (
                "Secret",
                {
                    "data": {
                        "other": base64.b64encode(b"value").decode()
                    }
                },
            ),
        ):
            with self.subTest(kind=kind):
                name = f"existing-{kind.lower()}"
                self.write_json(
                    f"deploy/releases/{name}.yaml",
                    {
                        "apiVersion": "v1",
                        "kind": kind,
                        "metadata": {
                            "name": name,
                            "namespace": "flux-system",
                        },
                        **payload,
                    },
                )
                self.helm_release(
                    values_from=[
                        {
                            "kind": kind,
                            "name": name,
                            "valuesKey": "wanted.yaml",
                            "optional": True,
                        }
                    ],
                )
                with self.assertRaises(RenderError):
                    self.detect()


class IcebergTableBootstrapPrerequisiteTests(unittest.TestCase):
    def test_polaris_workload_and_bootstrap_are_both_reconciled(
        self,
    ) -> None:
        self.assertEqual(
            [
                (
                    ROOT
                    / "deploy/gitops/catalog/server/deployment.yaml"
                ).resolve()
            ],
            _polaris_workload_manifests(),
            "Only the exact-digest Polaris Deployment may satisfy the static "
            "workload prerequisite",
        )
        self.assertEqual(
            [
                (
                    ROOT
                    / "deploy/gitops/clusters/local-lite/flux-system/gotk-sync.yaml"
                ).resolve()
            ],
            _iceberg_bootstrap_manifests(),
            "Only the reviewed Iceberg table bootstrap Job may satisfy the "
            "materialized bootstrap contract",
        )

    def test_bootstrap_job_uses_only_admitted_images_and_secret_references(
        self,
    ) -> None:
        job = load_yaml_file(
            ROOT / "deploy/gitops/iceberg/bootstrap/job.yaml"
        )[0]
        pod = job["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertFalse(pod["enableServiceLinks"])
        self.assertEqual("OnFailure", pod["restartPolicy"])
        self.assertEqual(600, job["spec"]["activeDeadlineSeconds"])
        self.assertEqual(3, job["spec"]["backoffLimit"])
        container = pod["containers"][0]
        self.assertEqual(
            "ghcr.io/tommykammy/shirokuma-polaris@sha256:"
            "db403e2db7afbe4e8a62261500e229f6d796a420e814564b49f3e14217fd6c9e",
            container["image"],
        )
        self.assertEqual(["/usr/bin/java"], container["command"])
        self.assertEqual(
            "-Djava.util.logging.manager=org.jboss.logmanager.LogManager",
            container["args"][0],
        )
        self.assertEqual(
            "/deployments/lib/boot/*:/deployments/lib/main/*:/deployments/app/*",
            container["args"][2],
        )
        self.assertFalse(
            any(argument.startswith("-Dshirokuma.") for argument in container["args"])
        )
        environment = {item["name"]: item for item in container["env"]}
        expected_secret_refs = {
            "POLARIS_CLIENT_ID": ("polaris-root-credentials", "client_id"),
            "POLARIS_CLIENT_SECRET": (
                "polaris-root-credentials",
                "client_secret",
            ),
            "POLARIS_REALM": ("polaris-root-credentials", "realm"),
            "AWS_REGION": (
                "seaweedfs-s3-application-credentials",
                "S3_REGION",
            ),
            "AWS_ACCESS_KEY_ID": (
                "seaweedfs-s3-application-credentials",
                "AWS_ACCESS_KEY_ID",
            ),
            "AWS_SECRET_ACCESS_KEY": (
                "seaweedfs-s3-application-credentials",
                "AWS_SECRET_ACCESS_KEY",
            ),
        }
        for name, (secret_name, key) in expected_secret_refs.items():
            with self.subTest(name=name):
                reference = environment[name]["valueFrom"]["secretKeyRef"]
                self.assertEqual(secret_name, reference["name"])
                self.assertEqual(key, reference["key"])
                self.assertNotIn("value", environment[name])
        self.assertEqual(
            {
                "POLARIS_URI": (
                    "http://polaris.shirokuma-dev.svc.cluster.local:8181"
                ),
                "S3_ENDPOINT": (
                    "http://seaweedfs-s3.shirokuma-storage.svc.cluster.local:8333"
                ),
            },
            {
                name: environment[name]["value"]
                for name in ("POLARIS_URI", "S3_ENDPOINT")
            },
        )

    def test_bootstrap_job_is_non_root_read_only_and_network_bounded(
        self,
    ) -> None:
        job = load_yaml_file(
            ROOT / "deploy/gitops/iceberg/bootstrap/job.yaml"
        )[0]
        pod = job["spec"]["template"]["spec"]
        container = pod["containers"][0]
        self.assertEqual(
            {
                "allowPrivilegeEscalation": False,
                "capabilities": {"drop": ["ALL"]},
                "readOnlyRootFilesystem": True,
                "runAsNonRoot": True,
                "runAsUser": 10000,
                "runAsGroup": 10001,
            },
            container["securityContext"],
        )
        self.assertEqual(10000, pod["securityContext"]["runAsUser"])
        self.assertEqual(
            "true",
            job["spec"]["template"]["metadata"]["labels"][
                "shirokuma.dev/object-storage-client"
            ],
        )
        policy = load_yaml_file(
            ROOT / "deploy/gitops/iceberg/bootstrap/networkpolicy.yaml"
        )[0]
        self.assertEqual([], policy["spec"]["ingress"])
        self.assertEqual(3, len(policy["spec"]["egress"]))
        self.assertEqual([8181, 8333, 53], [
            rule["ports"][0]["port"] for rule in policy["spec"]["egress"]
        ])

    def test_flux_orders_bootstrap_after_catalog_and_waits_for_job(
        self,
    ) -> None:
        flux = load_yaml_file(
            ROOT
            / "deploy/gitops/clusters/local-lite/iceberg-bootstrap.yaml"
        )[0]
        self.assertEqual(
            [{"name": "shirokuma-catalog"}], flux["spec"]["dependsOn"]
        )
        self.assertTrue(flux["spec"]["prune"])
        self.assertTrue(flux["spec"]["force"])
        self.assertTrue(flux["spec"]["wait"])
        self.assertEqual(
            {
                "apiVersion": "batch/v1",
                "kind": "Job",
                "name": "iceberg-table-bootstrap",
                "namespace": "shirokuma-dev",
            },
            flux["spec"]["healthChecks"][0],
        )

    def test_source_implements_create_write_read_and_idempotence(self) -> None:
        source = (
            ROOT
            / "deploy/gitops/iceberg/bootstrap/IcebergBootstrap.java"
        ).read_text(encoding="utf-8")
        for expected in (
            "ensureManagementCatalog",
            "catalog.namespaceExists",
            "catalog.tableExists",
            "writeFixture",
            "table.newAppend().appendFile(dataFile).commit()",
            "verifyFixtureRead",
            "catalog.listTables(namespace)",
            "table.currentSnapshot() == null",
            '"--cleanup"',
            "CatalogUtil.dropTableData",
            "catalog.dropTable(identifier, false)",
            "hasSingleAllowedLocation",
            'storage.path("endpointInternal")',
            'storage.path("pathStyleAccess")',
            "error instanceof BootstrapException",
            '"operation failed"',
            "credential_material_retained",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, source)
        for forbidden in (
            "http://169.254.169.254",
            "Runtime.getRuntime().exec",
            "ProcessBuilder",
            "latest",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        self.assertLess(
            source.index("catalog.dropTable(identifier, false)"),
            source.index("CatalogUtil.dropTableData"),
            "Catalog metadata must be detached before object deletion so a "
            "failed catalog drop cannot leave a live table pointing at deleted data",
        )
        self.assertNotIn(
            "Set.of(environment.clientSecret()",
            source,
            "Redaction must tolerate equal credential values without disclosing "
            "the duplicate through an exception",
        )

    def test_runbook_bounds_host_ssd_and_export_impact(self) -> None:
        runbook = (
            ROOT
            / "docs/design/08_Runbooks/"
            "RB-014_Verify_and_recover_Iceberg_table_bootstrap.md"
        ).read_text(encoding="utf-8")
        for expected in (
            "six objects totaling 16,547",
            "at most eight objects and 1 MiB",
            "at least 128 MiB free in the Colima data filesystem",
            "at least\n  128 MiB free on the host outside Colima",
            "20Gi SeaweedFS PVC request",
            "400GB `solo-lite` VM disk commitment",
            "object_count",
            "total_bytes",
            "--bucket shirokuma-lakehouse --prefix l1/",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, runbook)


if __name__ == "__main__":
    unittest.main()
