from __future__ import annotations

import base64
import binascii
import json
import re
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Iterator, NamedTuple


ROOT = Path(__file__).resolve().parents[1]
DEPLOY_ROOT = ROOT / "deploy"
CHARTS_ROOT = ROOT / "charts"
RESIDENT_IMAGES = ROOT / "security/resident-images.json"
DEPLOYMENT_SUFFIXES = {".json", ".yaml", ".yml"}
WORKLOAD_KINDS = {"Deployment", "StatefulSet"}
POLARIS_COMPONENT = "polaris"
BOOTSTRAP_KINDS = {"Job", "CronJob"}
BOOTSTRAP_CONTAINER_ROOT_PATHS = (
    ("spec", "template", "spec", "containers"),
    ("spec", "template", "spec", "initContainers"),
    ("spec", "jobTemplate", "spec", "template", "spec", "containers"),
    (
        "spec",
        "jobTemplate",
        "spec",
        "template",
        "spec",
        "initContainers",
    ),
)
HELM_RELEASE_IDENTITY_PATHS = (
    ("metadata", "name"),
    ("metadata", "labels", "app.kubernetes.io/name"),
    ("spec", "releaseName"),
    ("spec", "chart", "spec", "chart"),
    ("spec", "chartRef", "name"),
)
HELM_CHART_REF_KINDS = {"ExternalArtifact", "HelmChart", "OCIRepository"}
HELM_CHART_SOURCE_KINDS = {
    "Bucket",
    "GitRepository",
    "HelmRepository",
    "OCIRepository",
}
HELM_VALUES_SOURCE_KINDS = {"ConfigMap", "Secret"}
KUSTOMIZATION_FILENAMES = ("kustomization.yaml", "kustomization.yml", "Kustomization")

ScalarItems = list[tuple[tuple[str, ...], str]]


class ManifestResource(NamedTuple):
    path: Path
    document: str
    scalar_items: ScalarItems
    namespace: str


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


def _strip_yaml_scalar(value: str) -> str:
    return value.split(" #", maxsplit=1)[0].strip().strip("'\"")


def _flow_mapping_fields(value: str) -> dict[str, str]:
    return {
        match.group("field"): _strip_yaml_scalar(match.group("value"))
        for match in re.finditer(
            r"(?:^|[,{])\s*(?P<field>[A-Za-z][A-Za-z0-9]*)\s*:\s*"
            r"(?P<value>\"[^\"]*\"|'[^']*'|[^,}\]]+)",
            value,
        )
    }


def _yaml_mapping_sequence(
    document: str, target_path: tuple[str, ...]
) -> list[dict[str, str]]:
    try:
        parsed = json.loads(document)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict):
        value: object = parsed
        for part in target_path:
            if not isinstance(value, dict) or part not in value:
                return []
            value = value[part]
        if not isinstance(value, list):
            return []
        return [
            {str(key): str(item) for key, item in entry.items()}
            for entry in value
            if isinstance(entry, dict)
        ]

    lines = document.splitlines()
    stack: list[tuple[int, str]] = []
    for index, raw_line in enumerate(lines):
        match = re.match(
            r"^(?P<indent>[ ]*)(?P<key>[^:#][^:]*):(?P<value>.*)$",
            raw_line,
        )
        if match is None:
            continue
        indent = len(match.group("indent"))
        while stack and indent <= stack[-1][0]:
            stack.pop()
        key = match.group("key").strip().strip("'\"")
        path = tuple(item[1] for item in stack) + (key,)
        if path != target_path:
            if not match.group("value").strip():
                stack.append((indent, key))
            continue

        inline_value = match.group("value").strip()
        if inline_value:
            return [
                _flow_mapping_fields(mapping)
                for mapping in re.findall(r"\{[^{}]*\}", inline_value)
            ]

        entries: list[dict[str, str]] = []
        current: dict[str, str] | None = None
        item_indent: int | None = None
        for candidate in lines[index + 1 :]:
            if not candidate.strip() or candidate.lstrip().startswith("#"):
                continue
            candidate_indent = len(candidate) - len(candidate.lstrip(" "))
            if candidate_indent <= indent:
                break
            item_match = re.match(
                r"^[ ]*-[ ]*(?P<value>.*?)[ ]*$", candidate
            )
            if item_match is not None:
                if current is not None:
                    entries.append(current)
                current = {}
                item_indent = candidate_indent
                item_value = item_match.group("value")
                if item_value.startswith("{"):
                    current.update(_flow_mapping_fields(item_value))
                else:
                    field_match = re.match(
                        r"(?P<field>[^:#][^:]*):(?P<value>.*)$",
                        item_value,
                    )
                    if field_match is not None:
                        current[
                            field_match.group("field").strip().strip("'\"")
                        ] = _strip_yaml_scalar(field_match.group("value"))
                continue
            if current is None or item_indent is None:
                return []
            field_match = re.match(
                r"^[ ]*(?P<field>[^:#][^:]*):(?P<value>.*)$",
                candidate,
            )
            if field_match is None or candidate_indent <= item_indent:
                return []
            current[
                field_match.group("field").strip().strip("'\"")
            ] = _strip_yaml_scalar(field_match.group("value"))
        if current is not None:
            entries.append(current)
        return entries
    return []


def _yaml_sequence_values(
    document: str, target_path: tuple[str, ...]
) -> list[str]:
    try:
        parsed = json.loads(document)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict):
        value: object = parsed
        for part in target_path:
            if not isinstance(value, dict) or part not in value:
                return []
            value = value[part]
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, str)]

    lines = document.splitlines()
    stack: list[tuple[int, str]] = []
    for index, raw_line in enumerate(lines):
        match = re.match(
            r"^(?P<indent>[ ]*)(?P<key>[^:#][^:]*):(?P<value>.*)$",
            raw_line,
        )
        if match is None:
            continue
        indent = len(match.group("indent"))
        while stack and indent <= stack[-1][0]:
            stack.pop()
        key = match.group("key").strip().strip("'\"")
        path = tuple(item[1] for item in stack) + (key,)
        if path != target_path:
            if not match.group("value").strip():
                stack.append((indent, key))
            continue

        inline_value = match.group("value").strip()
        if inline_value:
            inline_match = re.fullmatch(
                r"\[(?P<values>.*)\](?:\s+#.*)?", inline_value
            )
            if inline_match is None:
                return []
            values = [
                _strip_yaml_scalar(item)
                for item in inline_match.group("values").split(",")
            ]
            return values if values and all(values) else []

        values: list[str] = []
        for candidate in lines[index + 1 :]:
            if not candidate.strip() or candidate.lstrip().startswith("#"):
                continue
            candidate_indent = len(candidate) - len(candidate.lstrip(" "))
            if candidate_indent <= indent:
                break
            item_match = re.match(r"^[ ]*-[ ]*(?P<value>.+?)\s*$", candidate)
            if item_match is None:
                return []
            values.append(_strip_yaml_scalar(item_match.group("value")))
        return values
    return []


def _yaml_string_mapping(document: str, field: str) -> dict[str, str]:
    lines = document.splitlines()
    entries: dict[str, str] = {}
    for index, raw_line in enumerate(lines):
        if re.match(rf"^{re.escape(field)}:[ ]*(?:#.*)?$", raw_line) is None:
            continue
        cursor = index + 1
        while cursor < len(lines):
            candidate = lines[cursor]
            if not candidate.strip() or candidate.lstrip().startswith("#"):
                cursor += 1
                continue
            indent = len(candidate) - len(candidate.lstrip(" "))
            if indent == 0:
                break
            item_match = re.match(
                r"^(?P<indent>[ ]*)(?P<key>[^:#][^:]*):(?P<value>.*)$",
                candidate,
            )
            if item_match is None:
                cursor += 1
                continue
            key_indent = len(item_match.group("indent"))
            key = item_match.group("key").strip().strip("'\"")
            raw_value = item_match.group("value").strip()
            if raw_value.startswith(("|", ">")):
                block_lines: list[str] = []
                cursor += 1
                while cursor < len(lines):
                    block_line = lines[cursor]
                    block_indent = len(block_line) - len(
                        block_line.lstrip(" ")
                    )
                    if block_line.strip() and block_indent <= key_indent:
                        break
                    block_lines.append(
                        block_line[min(len(block_line), key_indent + 2) :]
                    )
                    cursor += 1
                entries[key] = "\n".join(block_lines)
                continue
            entries[key] = _strip_yaml_scalar(raw_value)
            cursor += 1
        break
    return entries


def _manifest_item_documents(document: str) -> list[str]:
    try:
        parsed = json.loads(document)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        if parsed.get("kind") != "List":
            return [document]
        items = parsed.get("items")
        if not isinstance(items, list):
            return []
        return [
            json.dumps(item)
            for item in items
            if isinstance(item, dict)
        ]

    if dict(_document_scalars(document)).get(("kind",)) != "List":
        return [document]
    lines = document.splitlines()
    items_index = next(
        (
            index
            for index, line in enumerate(lines)
            if re.match(r"^items:[ ]*(?:#.*)?$", line)
        ),
        None,
    )
    if items_index is None:
        return []

    item_documents: list[list[str]] = []
    item_indent: int | None = None
    current: list[str] | None = None
    for line in lines[items_index + 1 :]:
        if not line.strip() or line.lstrip().startswith("#"):
            if current is not None:
                current.append("")
            continue
        indent = len(line) - len(line.lstrip(" "))
        sequence_match = re.match(r"^(?P<indent>[ ]*)-[ ]*(?P<value>.*)$", line)
        if sequence_match is not None and (
            item_indent is None or indent == item_indent
        ):
            if current is not None:
                item_documents.append(current)
            item_indent = indent
            current = [sequence_match.group("value")]
            continue
        if current is None or item_indent is None or indent <= item_indent:
            break
        current.append(line[min(len(line), item_indent + 2) :])
    if current is not None:
        item_documents.append(current)
    return ["\n".join(lines).strip() + "\n" for lines in item_documents]


def _effective_kustomize_namespace(
    path: Path, deploy_root: Path, charts_root: Path
) -> str:
    roots = (deploy_root.resolve(), charts_root.resolve())
    current = path.parent.resolve()
    while any(current == root or current.is_relative_to(root) for root in roots):
        for filename in KUSTOMIZATION_FILENAMES:
            kustomization = current / filename
            if not kustomization.is_file():
                continue
            namespace = dict(
                _document_scalars(kustomization.read_text(encoding="utf-8"))
            ).get(("namespace",))
            if namespace:
                return namespace
        if current in roots:
            break
        current = current.parent
    return "default"


def _has_polaris_identity(value: str | None) -> bool:
    return value == "polaris" or bool(value and re.fullmatch(r"polaris[-_][a-z0-9_-]+", value))


def _has_iceberg_bootstrap_identity(value: str | None) -> bool:
    if not value:
        return False
    identity_tokens = set(re.findall(r"[a-z0-9]+", value.casefold()))
    return {"iceberg", "bootstrap"} <= identity_tokens


def _path_starts_with(path: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    return path[: len(prefix)] == prefix


def _is_bootstrap_container_identity_path(path: tuple[str, ...]) -> bool:
    return any(
        path == root
        or (
            path[:-1] == root
            and path[-1].lstrip("{").casefold() in {"image", "name"}
        )
        for root in BOOTSTRAP_CONTAINER_ROOT_PATHS
    )


def _resource_key(
    scalar_items: ScalarItems,
    effective_namespace: str,
) -> tuple[str, str, str] | None:
    scalars = dict(scalar_items)
    kind = scalars.get(("kind",))
    name = scalars.get(("metadata", "name"))
    if not kind or not name:
        return None
    return (
        kind,
        scalars.get(("metadata", "namespace"), effective_namespace),
        name,
    )


def _helm_values_from_references(
    document: str,
) -> set[tuple[str, str]]:
    references = set()
    for entry in _yaml_mapping_sequence(document, ("spec", "valuesFrom")):
        name = entry.get("name")
        kind = entry.get("kind", "ConfigMap")
        if name and kind in HELM_VALUES_SOURCE_KINDS:
            references.add((kind, name))
    return references


def _resource_has_bootstrap_identity(
    scalar_items: ScalarItems,
) -> bool:
    return any(_has_iceberg_bootstrap_identity(value) for _, value in scalar_items)


def _values_source_data(document: str, kind: str) -> dict[str, str]:
    try:
        parsed = json.loads(document)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict):
        raw_data = parsed.get("data", {})
        string_data = parsed.get("stringData", {})
        encoded_data = (
            {
                str(key): value
                for key, value in raw_data.items()
                if isinstance(value, str)
            }
            if isinstance(raw_data, dict)
            else {}
        )
        plain_data = (
            {
                str(key): value
                for key, value in string_data.items()
                if isinstance(value, str)
            }
            if isinstance(string_data, dict)
            else {}
        )
    else:
        encoded_data = _yaml_string_mapping(document, "data")
        plain_data = _yaml_string_mapping(document, "stringData")

    if kind == "ConfigMap":
        return encoded_data
    decoded = {}
    for key, value in encoded_data.items():
        try:
            decoded[key] = base64.b64decode(
                "".join(value.split()), validate=True
            ).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
    decoded.update(plain_data)
    return decoded


def _values_source_has_bootstrap_identity(
    resource: ManifestResource,
) -> bool:
    if _resource_has_bootstrap_identity(resource.scalar_items):
        return True
    kind = dict(resource.scalar_items).get(("kind",), "")
    return any(
        _has_iceberg_bootstrap_identity(value)
        for value in _values_source_data(resource.document, kind).values()
    )


def _flow_mapping_field(
    scalar_items: list[tuple[tuple[str, ...], str]],
    mapping_path: tuple[str, ...],
    field: str,
) -> str | None:
    pattern = re.compile(
        rf"(?:^|[,{{])\s*{re.escape(field)}\s*:\s*(?P<value>[^,}}\s]+)"
    )
    for path, value in scalar_items:
        if path != mapping_path:
            continue
        match = pattern.search(value)
        if match is not None:
            return match.group("value").strip("'\"")
    return None


def _scoped_resources(
    resources: Mapping[
        tuple[str, str, str],
        Sequence[ManifestResource],
    ],
    kind: str,
    namespace: str,
    name: str,
    release_path: Path,
) -> Sequence[ManifestResource]:
    candidates = resources.get((kind, namespace, name), ())
    local = [
        resource
        for resource in candidates
        if resource.path.parent.resolve() == release_path.parent.resolve()
    ]
    if local:
        return local
    if len(candidates) == 1:
        return (candidates[0],)
    return ()


def _reference_field(
    scalar_items: ScalarItems,
    mapping_path: tuple[str, ...],
    field: str,
) -> str | None:
    scalars = dict(scalar_items)
    return scalars.get(mapping_path + (field,)) or _flow_mapping_field(
        scalar_items, mapping_path, field
    )


def _chart_resource_has_bootstrap_identity(
    resource: ManifestResource,
    resources: Mapping[
        tuple[str, str, str],
        Sequence[ManifestResource],
    ],
) -> bool:
    if _resource_has_bootstrap_identity(resource.scalar_items):
        return True
    if dict(resource.scalar_items).get(("kind",)) != "HelmChart":
        return False

    source_ref_path = ("spec", "sourceRef")
    source_name = _reference_field(
        resource.scalar_items, source_ref_path, "name"
    )
    source_kind = _reference_field(
        resource.scalar_items, source_ref_path, "kind"
    )
    source_namespace = (
        _reference_field(
            resource.scalar_items, source_ref_path, "namespace"
        )
        or resource.namespace
    )
    if (
        not source_name
        or source_kind not in HELM_CHART_SOURCE_KINDS
    ):
        return False
    return any(
        _resource_has_bootstrap_identity(source.scalar_items)
        for source in _scoped_resources(
            resources,
            source_kind,
            source_namespace,
            source_name,
            resource.path,
        )
    )


def _local_chart_has_bootstrap_values(
    chart_reference: str | None,
    charts_root: Path,
    release_path: Path,
    release_document: str,
) -> bool:
    if (
        not chart_reference
        or "://" in chart_reference
        or "{{" in chart_reference
        or "}}" in chart_reference
    ):
        return False

    reference = Path(chart_reference)
    candidates: list[Path] = []
    if reference.is_absolute():
        candidates.append(reference)
    else:
        parts = tuple(part for part in reference.parts if part not in {"", "."})
        if parts and parts[0] == charts_root.name:
            candidates.append(charts_root.joinpath(*parts[1:]))
        candidates.extend((charts_root / reference, release_path.parent / reference))

    charts_root_resolved = charts_root.resolve()
    for candidate in candidates:
        candidate = candidate.resolve()
        if not candidate.is_relative_to(charts_root_resolved):
            continue

        declared_values_files = _yaml_sequence_values(
            release_document, ("spec", "chart", "spec", "valuesFiles")
        )
        if declared_values_files:
            source_root = charts_root.parent.resolve()
            values_paths = [
                (source_root / values_file).resolve()
                for values_file in declared_values_files
            ]
            values_paths = [
                values_path
                for values_path in values_paths
                if values_path == source_root
                or values_path.is_relative_to(source_root)
            ]
        else:
            values_paths = [
                candidate / values_name
                for values_name in ("values.yaml", "values.yml")
            ]

        for values_path in values_paths:
            if not values_path.is_file():
                continue
            scalar_items = _document_scalars(
                values_path.read_text(encoding="utf-8")
            )
            if _resource_has_bootstrap_identity(scalar_items):
                return True
    return False


def _helm_release_has_bootstrap_identity(
    release: ManifestResource,
    resources: Mapping[
        tuple[str, str, str],
        Sequence[ManifestResource],
    ],
    charts_root: Path,
) -> bool:
    scalar_items = release.scalar_items
    scalars = dict(scalar_items)
    if any(
        (
            path in HELM_RELEASE_IDENTITY_PATHS
            or _path_starts_with(path, ("spec", "values"))
            or _path_starts_with(path, ("spec", "valuesFrom"))
        )
        and _has_iceberg_bootstrap_identity(value)
        for path, value in scalar_items
    ):
        return True

    release_namespace = scalars.get(
        ("metadata", "namespace"), release.namespace
    )
    chart_ref_name = scalars.get(
        ("spec", "chartRef", "name")
    ) or _flow_mapping_field(
        scalar_items, ("spec", "chartRef"), "name"
    )
    if chart_ref_name:
        chart_ref_kind = (
            scalars.get(("spec", "chartRef", "kind"))
            or _flow_mapping_field(
                scalar_items, ("spec", "chartRef"), "kind"
            )
            or "OCIRepository"
        )
        chart_ref_namespace = (
            scalars.get(("spec", "chartRef", "namespace"))
            or _flow_mapping_field(
                scalar_items, ("spec", "chartRef"), "namespace"
            )
            or release_namespace
        )
        if chart_ref_kind in HELM_CHART_REF_KINDS and any(
            _chart_resource_has_bootstrap_identity(resource, resources)
            for resource in _scoped_resources(
                resources,
                chart_ref_kind,
                chart_ref_namespace,
                chart_ref_name,
                release.path,
            )
        ):
            return True

    chart_source_ref_path = ("spec", "chart", "spec", "sourceRef")
    chart_source_name = _reference_field(
        scalar_items, chart_source_ref_path, "name"
    )
    if chart_source_name:
        chart_source_kind = (
            _reference_field(scalar_items, chart_source_ref_path, "kind")
            or "HelmRepository"
        )
        chart_source_namespace = (
            _reference_field(
                scalar_items, chart_source_ref_path, "namespace"
            )
            or release_namespace
        )
        if chart_source_kind in HELM_CHART_SOURCE_KINDS and any(
            _resource_has_bootstrap_identity(resource.scalar_items)
            for resource in _scoped_resources(
                resources,
                chart_source_kind,
                chart_source_namespace,
                chart_source_name,
                release.path,
            )
        ):
            return True

    chart_reference = scalars.get(
        ("spec", "chart", "spec", "chart")
    ) or _flow_mapping_field(
        scalar_items, ("spec", "chart", "spec"), "chart"
    )
    if _local_chart_has_bootstrap_values(
        chart_reference, charts_root, release.path, release.document
    ):
        return True

    return any(
        _values_source_has_bootstrap_identity(resource)
        for source_kind, source_name in _helm_values_from_references(
            release.document
        )
        for resource in _scoped_resources(
            resources,
            source_kind,
            release_namespace,
            source_name,
            release.path,
        )
    )


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


def _iceberg_bootstrap_manifests(
    deploy_root: Path = DEPLOY_ROOT,
    charts_root: Path = CHARTS_ROOT,
) -> list[Path]:
    manifest_resources: list[ManifestResource] = []
    resources: dict[
        tuple[str, str, str],
        list[ManifestResource],
    ] = {}
    for path in _deployment_manifest_paths(deploy_root, charts_root):
        documents = re.split(
            r"(?m)^---[ \t]*(?:#.*)?$", path.read_text(encoding="utf-8")
        )
        for document in documents:
            for resource_document in _manifest_item_documents(document):
                scalar_items = _document_scalars(resource_document)
                effective_namespace = _effective_kustomize_namespace(
                    path, deploy_root, charts_root
                )
                resource = ManifestResource(
                    path,
                    resource_document,
                    scalar_items,
                    effective_namespace,
                )
                manifest_resources.append(resource)
                resource_key = _resource_key(
                    scalar_items, effective_namespace
                )
                if resource_key is not None:
                    resources.setdefault(resource_key, []).append(resource)

    manifests: set[Path] = set()
    for resource in manifest_resources:
        scalars = dict(resource.scalar_items)
        kind = scalars.get(("kind",))
        is_bootstrap = kind in BOOTSTRAP_KINDS and any(
            (
                identity_path
                in {
                    ("metadata", "name"),
                    ("metadata", "labels", "app.kubernetes.io/name"),
                }
                or _is_bootstrap_container_identity_path(identity_path)
            )
            and _has_iceberg_bootstrap_identity(value)
            for identity_path, value in resource.scalar_items
        )
        if kind == "HelmRelease":
            is_bootstrap = _helm_release_has_bootstrap_identity(
                resource, resources, charts_root
            )
        if is_bootstrap:
            manifests.add(_display_path(resource.path))
    return sorted(manifests, key=str)


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


class IcebergBootstrapDetectionTests(unittest.TestCase):
    def test_detects_bootstrap_job_without_flagging_storage_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            deploy_root.mkdir()
            charts_root.mkdir()
            (deploy_root / "storage.yaml").write_text(
                "apiVersion: apps/v1\n"
                "kind: StatefulSet\n"
                "metadata:\n"
                "  name: seaweedfs\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - name: seaweedfs\n"
                "          args:\n"
                "            - -s3.port.iceberg=0\n",
                encoding="utf-8",
            )
            (deploy_root / "bootstrap.yaml").write_text(
                "apiVersion: batch/v1\n"
                "kind: Job\n"
                "metadata:\n"
                "  name: iceberg-table-bootstrap\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - name: bootstrap\n"
                "          image: registry.example/bootstrap@sha256:" + "a" * 64 + "\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [deploy_root / "bootstrap.yaml"],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_scans_every_job_and_cronjob_container_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            deploy_root.mkdir()
            charts_root.mkdir()
            (deploy_root / "job.yaml").write_text(
                "apiVersion: batch/v1\n"
                "kind: Job\n"
                "metadata:\n"
                "  name: catalog-task\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - name: iceberg-table-bootstrap\n"
                "          image: registry.example/bootstrap@sha256:" + "a" * 64 + "\n"
                "        - name: sidecar\n"
                "          image: registry.example/sidecar@sha256:" + "b" * 64 + "\n",
                encoding="utf-8",
            )
            (deploy_root / "cronjob.yaml").write_text(
                "apiVersion: batch/v1\n"
                "kind: CronJob\n"
                "metadata:\n"
                "  name: catalog-task\n"
                "spec:\n"
                "  jobTemplate:\n"
                "    spec:\n"
                "      template:\n"
                "        spec:\n"
                "          containers:\n"
                "            - name: iceberg-table-bootstrap\n"
                "              image: registry.example/bootstrap@sha256:"
                + "c" * 64
                + "\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [deploy_root / "cronjob.yaml", deploy_root / "job.yaml"],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_detects_bootstrap_helmrelease_chart_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            deploy_root.mkdir()
            charts_root.mkdir()
            (deploy_root / "release.yaml").write_text(
                "apiVersion: helm.toolkit.fluxcd.io/v2\n"
                "kind: HelmRelease\n"
                "metadata:\n"
                "  name: catalog-task\n"
                "spec:\n"
                "  chart:\n"
                "    spec:\n"
                "      chart: charts/iceberg-table-bootstrap\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [deploy_root / "release.yaml"],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_detects_bootstrap_identity_in_helm_template_helper(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            deploy_root.mkdir()
            template_root = charts_root / "catalog" / "templates"
            template_root.mkdir(parents=True)
            (template_root / "job.yaml").write_text(
                "apiVersion: batch/v1\n"
                "kind: Job\n"
                "metadata:\n"
                '  name: {{ include "iceberg-table-bootstrap.fullname" . }}\n'
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - name: bootstrap\n"
                "          image: registry.example/bootstrap@sha256:"
                + "a" * 64
                + "\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [template_root / "job.yaml"],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_detects_bootstrap_image_and_flow_style_container_identities(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            deploy_root.mkdir()
            charts_root.mkdir()
            (deploy_root / "image-job.yaml").write_text(
                "apiVersion: batch/v1\n"
                "kind: Job\n"
                "metadata:\n"
                "  name: catalog-task\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - name: bootstrap\n"
                "          image: registry.example/iceberg-table-bootstrap@sha256:"
                + "b" * 64
                + "\n",
                encoding="utf-8",
            )
            (deploy_root / "flow-cronjob.yaml").write_text(
                "apiVersion: batch/v1\n"
                "kind: CronJob\n"
                "metadata:\n"
                "  name: catalog-task\n"
                "spec:\n"
                "  jobTemplate:\n"
                "    spec:\n"
                "      template:\n"
                "        spec:\n"
                "          containers: [{name: iceberg-table-bootstrap, "
                "image: registry.example/bootstrap@sha256:"
                + "c" * 64
                + "}]\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [
                    deploy_root / "flow-cronjob.yaml",
                    deploy_root / "image-job.yaml",
                ],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_resolves_bootstrap_helmrelease_chart_ref_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            deploy_root.mkdir()
            charts_root.mkdir()
            (deploy_root / "source.yaml").write_text(
                "apiVersion: source.toolkit.fluxcd.io/v1\n"
                "kind: OCIRepository\n"
                "metadata:\n"
                "  name: catalog-chart\n"
                "spec:\n"
                "  url: oci://registry.example/iceberg-table-bootstrap\n",
                encoding="utf-8",
            )
            (deploy_root / "release.yaml").write_text(
                "apiVersion: helm.toolkit.fluxcd.io/v2\n"
                "kind: HelmRelease\n"
                "metadata:\n"
                "  name: catalog-task\n"
                "spec:\n"
                "  chartRef: {kind: OCIRepository, name: catalog-chart}\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [deploy_root / "release.yaml"],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_detects_helmrelease_values_and_values_from_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            deploy_root.mkdir()
            charts_root.mkdir()
            (deploy_root / "values.yaml").write_text(
                "apiVersion: v1\n"
                "kind: ConfigMap\n"
                "metadata:\n"
                "  name: catalog-values\n"
                "data:\n"
                "  values.yaml: |\n"
                "    containerName: iceberg-table-bootstrap\n",
                encoding="utf-8",
            )
            (deploy_root / "direct-release.yaml").write_text(
                "apiVersion: helm.toolkit.fluxcd.io/v2\n"
                "kind: HelmRelease\n"
                "metadata:\n"
                "  name: catalog-task\n"
                "spec:\n"
                "  values:\n"
                "    fullnameOverride: iceberg-table-bootstrap\n",
                encoding="utf-8",
            )
            (deploy_root / "referenced-release.yaml").write_text(
                "apiVersion: helm.toolkit.fluxcd.io/v2\n"
                "kind: HelmRelease\n"
                "metadata:\n"
                "  name: catalog-task\n"
                "spec:\n"
                "  valuesFrom: [{kind: ConfigMap, name: catalog-values}]\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [
                    deploy_root / "direct-release.yaml",
                    deploy_root / "referenced-release.yaml",
                ],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_ignores_nested_container_names_with_bootstrap_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            deploy_root.mkdir()
            charts_root.mkdir()
            (deploy_root / "worker.yaml").write_text(
                "apiVersion: batch/v1\n"
                "kind: Job\n"
                "metadata:\n"
                "  name: catalog-worker\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - name: worker\n"
                "          image: registry.example/worker@sha256:" + "a" * 64 + "\n"
                "          env:\n"
                "            - name: ICEBERG_TABLE_BOOTSTRAP_DISABLED\n"
                "              value: 'true'\n"
                "          ports:\n"
                "            - name: iceberg-table-bootstrap-metrics\n"
                "              containerPort: 8080\n"
                "          volumeMounts:\n"
                "            - name: iceberg-table-bootstrap-config\n"
                "              mountPath: /config\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_decodes_bootstrap_values_from_secret_data(self) -> None:
        encoded_values = base64.b64encode(
            b"fullnameOverride: iceberg-table-bootstrap\n"
        ).decode("ascii")
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            deploy_root.mkdir()
            charts_root.mkdir()
            (deploy_root / "values.yaml").write_text(
                "apiVersion: v1\n"
                "kind: Secret\n"
                "metadata:\n"
                "  name: catalog-values\n"
                "data:\n"
                f"  values.yaml: {encoded_values}\n",
                encoding="utf-8",
            )
            (deploy_root / "release.yaml").write_text(
                "apiVersion: helm.toolkit.fluxcd.io/v2\n"
                "kind: HelmRelease\n"
                "metadata:\n"
                "  name: catalog-task\n"
                "spec:\n"
                "  valuesFrom: [{kind: Secret, name: catalog-values}]\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [deploy_root / "release.yaml"],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_honors_values_from_kind_for_same_name_sources(self) -> None:
        benign_values = base64.b64encode(
            b"fullnameOverride: catalog-worker\n"
        ).decode("ascii")
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            deploy_root.mkdir()
            charts_root.mkdir()
            (deploy_root / "values.yaml").write_text(
                "apiVersion: v1\n"
                "kind: ConfigMap\n"
                "metadata:\n"
                "  name: catalog-values\n"
                "data:\n"
                "  values.yaml: |\n"
                "    fullnameOverride: iceberg-table-bootstrap\n"
                "---\n"
                "apiVersion: v1\n"
                "kind: Secret\n"
                "metadata:\n"
                "  name: catalog-values\n"
                "data:\n"
                f"  values.yaml: {benign_values}\n",
                encoding="utf-8",
            )
            (deploy_root / "release.yaml").write_text(
                "apiVersion: helm.toolkit.fluxcd.io/v2\n"
                "kind: HelmRelease\n"
                "metadata:\n"
                "  name: catalog-task\n"
                "spec:\n"
                "  valuesFrom:\n"
                "    - kind: Secret\n"
                "      name: catalog-values\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_resolves_bootstrap_spec_chart_source_ref_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            deploy_root.mkdir()
            charts_root.mkdir()
            (deploy_root / "source.yaml").write_text(
                "apiVersion: source.toolkit.fluxcd.io/v1\n"
                "kind: GitRepository\n"
                "metadata:\n"
                "  name: catalog-source\n"
                "spec:\n"
                "  url: https://github.example/iceberg-table-bootstrap.git\n",
                encoding="utf-8",
            )
            (deploy_root / "release.yaml").write_text(
                "apiVersion: helm.toolkit.fluxcd.io/v2\n"
                "kind: HelmRelease\n"
                "metadata:\n"
                "  name: catalog-task\n"
                "spec:\n"
                "  chart:\n"
                "    spec:\n"
                "      chart: ./charts/catalog-task\n"
                "      sourceRef:\n"
                "        kind: GitRepository\n"
                "        name: catalog-source\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [deploy_root / "release.yaml"],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_detects_bootstrap_defaults_in_local_chart_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            chart_root = charts_root / "catalog-task"
            deploy_root.mkdir()
            (chart_root / "templates").mkdir(parents=True)
            (chart_root / "values.yaml").write_text(
                "fullnameOverride: iceberg-table-bootstrap\n",
                encoding="utf-8",
            )
            (chart_root / "templates" / "job.yaml").write_text(
                "apiVersion: batch/v1\n"
                "kind: Job\n"
                "metadata:\n"
                "  name: {{ .Values.fullnameOverride }}\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - name: worker\n"
                "          image: registry.example/worker@sha256:" + "b" * 64 + "\n",
                encoding="utf-8",
            )
            (deploy_root / "release.yaml").write_text(
                "apiVersion: helm.toolkit.fluxcd.io/v2\n"
                "kind: HelmRelease\n"
                "metadata:\n"
                "  name: catalog-task\n"
                "spec:\n"
                "  chart:\n"
                "    spec:\n"
                "      chart: ./charts/catalog-task\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [deploy_root / "release.yaml"],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_preserves_name_first_values_from_kind(self) -> None:
        release = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\n"
            "kind: HelmRelease\n"
            "metadata:\n"
            "  name: catalog-task\n"
            "spec:\n"
            "  valuesFrom:\n"
            "    - name: catalog-values\n"
            "      kind: Secret\n"
        )

        self.assertEqual(
            {("Secret", "catalog-values")},
            _helm_values_from_references(release),
        )

    def test_decodes_line_wrapped_secret_data(self) -> None:
        encoded_values = base64.b64encode(
            b"fullnameOverride: iceberg-table-bootstrap\n"
        ).decode("ascii")
        midpoint = len(encoded_values) // 2
        secret = (
            "apiVersion: v1\n"
            "kind: Secret\n"
            "metadata:\n"
            "  name: catalog-values\n"
            "data:\n"
            "  values.yaml: |\n"
            f"    {encoded_values[:midpoint]}\n"
            f"    {encoded_values[midpoint:]}\n"
        )
        resource = ManifestResource(
            Path("secret.yaml"),
            secret,
            _document_scalars(secret),
            "default",
        )

        self.assertTrue(_values_source_has_bootstrap_identity(resource))

    def test_resolves_values_from_with_kustomize_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            deploy_root.mkdir()
            charts_root.mkdir()
            (deploy_root / "kustomization.yaml").write_text(
                "apiVersion: kustomize.config.k8s.io/v1beta1\n"
                "kind: Kustomization\n"
                "namespace: shirokuma-lake\n"
                "resources:\n"
                "  - values.yaml\n"
                "  - release.yaml\n",
                encoding="utf-8",
            )
            (deploy_root / "values.yaml").write_text(
                "apiVersion: v1\n"
                "kind: ConfigMap\n"
                "metadata:\n"
                "  name: catalog-values\n"
                "data:\n"
                "  values.yaml: |\n"
                "    fullnameOverride: iceberg-table-bootstrap\n",
                encoding="utf-8",
            )
            (deploy_root / "release.yaml").write_text(
                "apiVersion: helm.toolkit.fluxcd.io/v2\n"
                "kind: HelmRelease\n"
                "metadata:\n"
                "  name: catalog-task\n"
                "spec:\n"
                "  valuesFrom:\n"
                "    - kind: ConfigMap\n"
                "      name: catalog-values\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [deploy_root / "release.yaml"],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_detects_flow_mapping_container_list_item(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            deploy_root.mkdir()
            charts_root.mkdir()
            (deploy_root / "job.yaml").write_text(
                "apiVersion: batch/v1\n"
                "kind: Job\n"
                "metadata:\n"
                "  name: catalog-task\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - {name: iceberg-table-bootstrap, "
                "image: registry.example/bootstrap@sha256:"
                + "a" * 64
                + "}\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [deploy_root / "job.yaml"],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_follows_helmchart_source_ref_from_chart_ref_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            deploy_root.mkdir()
            charts_root.mkdir()
            (deploy_root / "source.yaml").write_text(
                "apiVersion: source.toolkit.fluxcd.io/v1\n"
                "kind: GitRepository\n"
                "metadata:\n"
                "  name: catalog-source\n"
                "spec:\n"
                "  url: https://github.example/iceberg-table-bootstrap.git\n"
                "---\n"
                "apiVersion: source.toolkit.fluxcd.io/v1\n"
                "kind: HelmChart\n"
                "metadata:\n"
                "  name: catalog-chart\n"
                "spec:\n"
                "  chart: ./charts/catalog-task\n"
                "  sourceRef:\n"
                "    kind: GitRepository\n"
                "    name: catalog-source\n",
                encoding="utf-8",
            )
            (deploy_root / "release.yaml").write_text(
                "apiVersion: helm.toolkit.fluxcd.io/v2\n"
                "kind: HelmRelease\n"
                "metadata:\n"
                "  name: catalog-task\n"
                "spec:\n"
                "  chartRef:\n"
                "    kind: HelmChart\n"
                "    name: catalog-chart\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [deploy_root / "release.yaml"],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_scans_helmrelease_selected_local_values_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            chart_root = charts_root / "catalog-task"
            deploy_root.mkdir()
            chart_root.mkdir(parents=True)
            (chart_root / "values.yaml").write_text(
                "fullnameOverride: catalog-task\n",
                encoding="utf-8",
            )
            (chart_root / "bootstrap-values.yaml").write_text(
                "fullnameOverride: iceberg-table-bootstrap\n",
                encoding="utf-8",
            )
            (deploy_root / "release.yaml").write_text(
                "apiVersion: helm.toolkit.fluxcd.io/v2\n"
                "kind: HelmRelease\n"
                "metadata:\n"
                "  name: catalog-task\n"
                "spec:\n"
                "  chart:\n"
                "    spec:\n"
                "      chart: ./charts/catalog-task\n"
                "      valuesFiles:\n"
                "        - ./charts/catalog-task/bootstrap-values.yaml\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [deploy_root / "release.yaml"],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_unwraps_bootstrap_job_from_kubernetes_list(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            deploy_root.mkdir()
            charts_root.mkdir()
            (deploy_root / "list.yaml").write_text(
                "apiVersion: v1\n"
                "kind: List\n"
                "items:\n"
                "  - apiVersion: batch/v1\n"
                "    kind: Job\n"
                "    metadata:\n"
                "      name: iceberg-table-bootstrap\n"
                "    spec:\n"
                "      template:\n"
                "        spec:\n"
                "          containers:\n"
                "            - name: bootstrap\n"
                "              image: registry.example/bootstrap@sha256:"
                + "b" * 64
                + "\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [deploy_root / "list.yaml"],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_ignores_non_bootstrap_iceberg_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            deploy_root.mkdir()
            charts_root.mkdir()
            (deploy_root / "catalog-smoke.yaml").write_text(
                "apiVersion: batch/v1\n"
                "kind: Job\n"
                "metadata:\n"
                "  name: iceberg-catalog-smoke\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - name: iceberg-maintenance\n"
                "          image: registry.example/maintenance@sha256:" + "d" * 64 + "\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )


class IcebergTableBootstrapPrerequisiteTests(unittest.TestCase):
    def test_missing_polaris_workload_keeps_bootstrap_blocked(self) -> None:
        self.assertEqual(
            [],
            _polaris_workload_manifests(),
            "Replace this blocker regression with the Iceberg bootstrap checks once "
            "an approved Polaris Deployment or StatefulSet is materialized through "
            "deploy or a Helm chart template",
        )
        self.assertEqual(
            [],
            _iceberg_bootstrap_manifests(),
            "Iceberg namespace/table bootstrap resources must remain absent until "
            "an admitted Polaris workload and its catalog readiness evidence exist",
        )


if __name__ == "__main__":
    unittest.main()
