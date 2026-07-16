from __future__ import annotations

import base64
import binascii
import json
import re
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path, PurePosixPath

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
    _has_polaris_identity,
    _is_polaris_workload,
    _polaris_workload_manifests,
)

TRINO_COMPONENT = "trino"
POSTGRESQL_COMPONENT = "postgresql"
HELM_RELEASE_IDENTITY_PATHS = (
    ("metadata", "name"),
    ("metadata", "labels", "app.kubernetes.io/name"),
    ("spec", "releaseName"),
    ("spec", "chart", "spec", "chart"),
    ("spec", "chartRef", "name"),
)
HELM_VALUES_SOURCE_KINDS = {"ConfigMap", "Secret"}
HELM_CHART_REF_KINDS = {"ExternalArtifact", "HelmChart", "OCIRepository"}
HELM_IMAGE_VALUE_KEYS = {
    "digest",
    "image",
    "name",
    "reference",
    "registry",
    "repository",
}
HelmResourceKey = tuple[object, ...]
HelmValuesSources = dict[HelmResourceKey, dict[str, str]]
HelmChartReferences = dict[HelmResourceKey, tuple[str, ...]]


def _has_trino_identity(value: str | None) -> bool:
    return value == "trino" or bool(
        value and re.fullmatch(r"trino[-_][a-z0-9_-]+", value)
    )


def _is_helm_image_value_path(path: tuple[str, ...]) -> bool:
    value_path = tuple(part.lower() for part in path[2:])
    if not ({"image", "images"} & set(value_path)):
        return False
    return value_path[-1] in HELM_IMAGE_VALUE_KEYS or (
        len(value_path) >= 2 and value_path[-2] == "images"
    )


def _helm_release_uses_admitted_image(
    scalar_items: list[tuple[tuple[str, ...], str]], admitted_images: set[str]
) -> bool:
    value_items: list[tuple[tuple[str, ...], str]] = []
    for path, value in scalar_items:
        if path[:2] != ("spec", "values"):
            continue
        for expanded_path, expanded_value in _yaml_flow_mapping_items(value, path):
            if _is_helm_image_value_path(expanded_path):
                value_items.append((expanded_path, expanded_value))
    values = {value for _, value in value_items}
    values_by_parent: dict[tuple[str, ...], set[str]] = {}
    for path, value in value_items:
        values_by_parent.setdefault(path[:-1], set()).add(value)

    for reference in admitted_images:
        repository, separator, digest = reference.rpartition("@")
        if reference in values or (
            separator
            and any(
                {repository, digest} <= grouped_values
                for grouped_values in values_by_parent.values()
            )
        ):
            return True
        registry, slash, repository_path = repository.partition("/")
        if separator and slash and any(
            {registry, repository_path, digest} <= grouped_values
            for grouped_values in values_by_parent.values()
        ):
            return True
    return False


def _merge_helm_value_items(
    effective: dict[tuple[str, ...], str],
    items: list[tuple[tuple[str, ...], str]],
) -> None:
    for path, value in items:
        for existing_path in list(effective):
            if (
                existing_path[: len(path)] == path
                or path[: len(existing_path)] == existing_path
            ):
                effective.pop(existing_path)
        effective[path] = value


def _yaml_sequence_values(document: str, target_path: tuple[str, ...]) -> list[str]:
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
            inline_match = re.fullmatch(r"\[(?P<values>.*)\](?:\s+#.*)?", inline_value)
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


def _strip_yaml_scalar(value: str) -> str:
    return value.split(" #", maxsplit=1)[0].strip().strip("'\"")


def _split_yaml_flow_items(value: str) -> list[str]:
    items: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    escaped = False
    for index, character in enumerate(value):
        if escaped:
            escaped = False
            continue
        if quote is not None:
            if character == "\\" and quote == '"':
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
        elif character in "[{":
            depth += 1
        elif character in "]}":
            depth -= 1
        elif character == "," and depth == 0:
            items.append(value[start:index].strip())
            start = index + 1
    items.append(value[start:].strip())
    return [item for item in items if item]


def _yaml_flow_mapping(value: str) -> dict[str, str] | None:
    value = value.strip()
    if not (value.startswith("{") and value.endswith("}")):
        return None
    mapping: dict[str, str] = {}
    for item in _split_yaml_flow_items(value[1:-1]):
        key, separator, raw_value = item.partition(":")
        if not separator or not key.strip() or not raw_value.strip():
            return None
        mapping[_strip_yaml_scalar(key)] = _strip_yaml_scalar(raw_value)
    return mapping


def _yaml_flow_mapping_items(
    value: str, prefix: tuple[str, ...]
) -> list[tuple[tuple[str, ...], str]]:
    mapping = _yaml_flow_mapping(value)
    if mapping is None:
        return [(prefix, _strip_yaml_scalar(value))]
    items: list[tuple[tuple[str, ...], str]] = []
    for key, nested_value in mapping.items():
        items.extend(_yaml_flow_mapping_items(nested_value, (*prefix, key)))
    return items


def _yaml_flow_sequence_mappings(value: str) -> list[dict[str, str]]:
    value = value.strip()
    if not (value.startswith("[") and value.endswith("]")):
        return []
    mappings = [
        _yaml_flow_mapping(item) for item in _split_yaml_flow_items(value[1:-1])
    ]
    if not mappings or any(mapping is None for mapping in mappings):
        return []
    return [mapping for mapping in mappings if mapping is not None]


def _values_from_references(
    document: str,
) -> list[tuple[str, str, str, str | None, bool]]:
    try:
        parsed = json.loads(document)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict):
        values_from = parsed.get("spec", {}).get("valuesFrom", [])
        if not isinstance(values_from, list):
            return []
        references = []
        for item in values_from:
            if not isinstance(item, dict):
                continue
            kind = item.get("kind")
            name = item.get("name")
            values_key = item.get("valuesKey", "values.yaml")
            target_path = item.get("targetPath")
            optional = item.get("optional", False)
            if (
                kind in HELM_VALUES_SOURCE_KINDS
                and isinstance(name, str)
                and isinstance(values_key, str)
                and (target_path is None or isinstance(target_path, str))
                and isinstance(optional, bool)
            ):
                references.append((kind, name, values_key, target_path, optional))
        return references

    references: list[tuple[str, str, str, str | None, bool]] = []
    lines = document.splitlines()
    stack: list[tuple[int, str]] = []
    index = 0
    while index < len(lines):
        raw_line = lines[index]
        match = re.match(
            r"^(?P<indent>[ ]*)(?P<key>[^:#][^:]*):(?P<value>.*)$",
            raw_line,
        )
        if match is None:
            index += 1
            continue
        indent = len(match.group("indent"))
        while stack and indent <= stack[-1][0]:
            stack.pop()
        key = match.group("key").strip().strip("'\"")
        path = tuple(item[1] for item in stack) + (key,)
        if path != ("spec", "valuesFrom"):
            if not match.group("value").strip():
                stack.append((indent, key))
            index += 1
            continue

        inline_value = match.group("value").strip()
        if inline_value:
            inline_match = re.fullmatch(
                r"\[(?P<items>.*)\](?:\s+#.*)?", inline_value
            )
            if inline_match is None:
                return []
            for raw_item in _split_yaml_flow_items(inline_match.group("items")):
                item = _yaml_flow_mapping(raw_item)
                if item is None:
                    return []
                kind = item.get("kind")
                name = item.get("name")
                if kind in HELM_VALUES_SOURCE_KINDS and name:
                    references.append(
                        (
                            kind,
                            name,
                            item.get("valuesKey", "values.yaml"),
                            item.get("targetPath"),
                            item.get("optional", "false").lower() == "true",
                        )
                    )
            index += 1
            continue

        block_indent = indent
        current: dict[str, str] | None = None
        index += 1
        while index < len(lines):
            candidate = lines[index]
            if not candidate.strip() or candidate.lstrip().startswith("#"):
                index += 1
                continue
            candidate_indent = len(candidate) - len(candidate.lstrip(" "))
            if candidate_indent <= block_indent:
                break
            item_match = re.match(
                r"^[ ]*-[ ]*(?P<key>[^:#][^:]*):(?P<value>.*)$",
                candidate,
            )
            field_match = re.match(
                r"^[ ]*(?P<key>[^:#][^:]*):(?P<value>.*)$",
                candidate,
            )
            if item_match is not None:
                if current is not None:
                    kind = current.get("kind")
                    name = current.get("name")
                    if kind in HELM_VALUES_SOURCE_KINDS and name:
                        references.append(
                            (
                                kind,
                                name,
                                current.get("valuesKey", "values.yaml"),
                                current.get("targetPath"),
                                current.get("optional", "false").lower() == "true",
                            )
                        )
                current = {
                    item_match.group("key").strip().strip("'\""): _strip_yaml_scalar(
                        item_match.group("value")
                    )
                }
            elif current is not None and field_match is not None:
                current[field_match.group("key").strip().strip("'\"")] = (
                    _strip_yaml_scalar(field_match.group("value"))
                )
            index += 1
        if current is not None:
            kind = current.get("kind")
            name = current.get("name")
            if kind in HELM_VALUES_SOURCE_KINDS and name:
                references.append(
                    (
                        kind,
                        name,
                        current.get("valuesKey", "values.yaml"),
                        current.get("targetPath"),
                        current.get("optional", "false").lower() == "true",
                    )
                )
    return references


def _yaml_string_mapping(document: str, field: str) -> dict[str, str]:
    lines = document.splitlines()
    entries: dict[str, str] = {}
    for index, raw_line in enumerate(lines):
        match = re.match(
            rf"^{re.escape(field)}:[ ]*(?:#.*)?$", raw_line
        )
        if match is None:
            continue
        field_indent = 0
        cursor = index + 1
        while cursor < len(lines):
            candidate = lines[cursor]
            if not candidate.strip() or candidate.lstrip().startswith("#"):
                cursor += 1
                continue
            indent = len(candidate) - len(candidate.lstrip(" "))
            if indent <= field_indent:
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
                    block_indent = len(block_line) - len(block_line.lstrip(" "))
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
            decoded[key] = base64.b64decode(value, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            continue
    decoded.update(plain_data)
    return decoded


def _helm_values_sources(
    deploy_root: Path = DEPLOY_ROOT, charts_root: Path = CHARTS_ROOT
) -> HelmValuesSources:
    sources: HelmValuesSources = {}
    ambiguous: set[HelmResourceKey] = set()
    for path in _deployment_manifest_paths(deploy_root, charts_root):
        documents = re.split(
            r"(?m)^---[ \t]*(?:#.*)?$", path.read_text(encoding="utf-8")
        )
        for document in documents:
            scalars = dict(_document_scalars(document))
            kind = scalars.get(("kind",))
            name = scalars.get(("metadata", "name"))
            if kind not in HELM_VALUES_SOURCE_KINDS or not name:
                continue
            namespace = scalars.get(("metadata", "namespace"), "default")
            identity = (path.parent.resolve(), kind, namespace, name)
            if identity in sources or identity in ambiguous:
                sources.pop(identity, None)
                ambiguous.add(identity)
                continue
            sources[identity] = _values_source_data(document, kind)
    return sources


def _helm_chart_references(
    deploy_root: Path = DEPLOY_ROOT, charts_root: Path = CHARTS_ROOT
) -> HelmChartReferences:
    references: HelmChartReferences = {}
    ambiguous: set[HelmResourceKey] = set()
    for path in _deployment_manifest_paths(deploy_root, charts_root):
        documents = re.split(
            r"(?m)^---[ \t]*(?:#.*)?$", path.read_text(encoding="utf-8")
        )
        for document in documents:
            scalars = dict(_document_scalars(document))
            kind = scalars.get(("kind",))
            name = scalars.get(("metadata", "name"))
            if kind not in {
                "Bucket",
                "ExternalArtifact",
                "GitRepository",
                "HelmChart",
                "HelmRepository",
                "OCIRepository",
            } or not name:
                continue
            namespace = scalars.get(("metadata", "namespace"), "default")
            identity = (path.parent.resolve(), kind, namespace, name)
            if identity in references or identity in ambiguous:
                references.pop(identity, None)
                ambiguous.add(identity)
                continue
            values = [name]
            for candidate_path in (("spec", "url"), ("spec", "chart")):
                value = scalars.get(candidate_path)
                if value:
                    values.extend((value, PurePosixPath(value.rstrip("/")).name))
            references[identity] = tuple(dict.fromkeys(values))
    return references


def _scoped_helm_resource(
    resources: dict[HelmResourceKey, object],
    kind: str,
    namespace: str,
    name: str,
    release_path: Path | None,
    allow_shared_namespace: bool = False,
) -> object | None:
    if release_path is not None:
        scoped = resources.get(
            (release_path.parent.resolve(), kind, namespace, name)
        )
        if scoped is not None:
            return scoped
        unscoped = resources.get((kind, namespace, name))
        if unscoped is not None:
            return unscoped
        if not allow_shared_namespace:
            return None
    unscoped = resources.get((kind, namespace, name))
    if unscoped is not None:
        return unscoped
    matches = [
        value
        for key, value in resources.items()
        if len(key) == 4 and key[1:] == (kind, namespace, name)
    ]
    return matches[0] if len(matches) == 1 else None


def _referenced_chart_identities(
    document: str,
    chart_references: HelmChartReferences | None,
    release_path: Path | None,
) -> tuple[str, ...]:
    if not chart_references:
        return ()
    scalars = dict(_document_scalars(document))
    kind = scalars.get(("spec", "chartRef", "kind"))
    name = scalars.get(("spec", "chartRef", "name"))
    if not kind or not name:
        return ()
    namespace = scalars.get(
        ("spec", "chartRef", "namespace"),
        scalars.get(("metadata", "namespace"), "default"),
    )
    values = _scoped_helm_resource(
        chart_references,
        kind,
        namespace,
        name,
        release_path,
        allow_shared_namespace=True,
    )
    return values if isinstance(values, tuple) else ()


def _helm_release_chart_source_resolves(
    document: str,
    chart_references: HelmChartReferences | None,
    release_path: Path | None,
) -> bool:
    scalars = dict(_document_scalars(document))
    release_namespace = scalars.get(("metadata", "namespace"), "default")
    chart = scalars.get(("spec", "chart", "spec", "chart"))
    chart_ref_kind = scalars.get(("spec", "chartRef", "kind"))
    chart_ref_name = scalars.get(("spec", "chartRef", "name"))

    if chart_references is None:
        return True

    has_chart = chart is not None
    has_chart_ref = chart_ref_kind is not None or chart_ref_name is not None
    if has_chart == has_chart_ref:
        return False

    if has_chart:
        kind = scalars.get(("spec", "chart", "spec", "sourceRef", "kind"))
        name = scalars.get(("spec", "chart", "spec", "sourceRef", "name"))
        namespace = scalars.get(
            ("spec", "chart", "spec", "sourceRef", "namespace"),
            release_namespace,
        )
    else:
        if chart_ref_kind not in HELM_CHART_REF_KINDS:
            return False
        kind = chart_ref_kind
        name = chart_ref_name
        namespace = scalars.get(
            ("spec", "chartRef", "namespace"), release_namespace
        )
    if not kind or not name:
        return False
    return (
        _scoped_helm_resource(
            chart_references,
            kind,
            namespace,
            name,
            release_path,
            allow_shared_namespace=True,
        )
        is not None
    )


def _local_chart_root(document: str, charts_root: Path) -> Path | None:
    chart = dict(_document_scalars(document)).get(
        ("spec", "chart", "spec", "chart")
    )
    if not chart:
        return None
    normalized = chart.removeprefix("./")
    if not normalized.startswith("charts/"):
        return None
    candidate = (charts_root.parent / normalized).resolve()
    root = charts_root.resolve()
    if candidate != root and root not in candidate.parents:
        return None
    return candidate


def _local_chart_value_items(
    document: str, charts_root: Path
) -> list[tuple[tuple[str, ...], str]] | None:
    chart_root = _local_chart_root(document, charts_root)
    if chart_root is None:
        return []
    declared_values_files = _yaml_sequence_values(
        document, ("spec", "chart", "spec", "valuesFiles")
    )
    if not chart_root.is_dir():
        return None
    source_root = charts_root.parent.resolve()
    ignore_missing = (
        dict(_document_scalars(document))
        .get(("spec", "chart", "spec", "ignoreMissingValuesFiles"), "false")
        .lower()
        == "true"
    )
    items: list[tuple[tuple[str, ...], str]] = []
    values_files = declared_values_files or ["values.yaml"]
    for values_file in values_files:
        base = source_root if declared_values_files else chart_root
        candidate = (base / values_file).resolve()
        if candidate != base and base not in candidate.parents:
            return None
        if not candidate.is_file():
            if declared_values_files and ignore_missing:
                continue
            if declared_values_files:
                return None
            continue
        items.extend(
            (("spec", "values", *path), value)
            for path, value in _document_scalars(
                candidate.read_text(encoding="utf-8")
            )
        )
    return items


def _postrenderer_images(
    document: str,
) -> tuple[tuple[str, str | None], ...]:
    images: list[dict[str, str]] = []
    try:
        parsed = json.loads(document)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        spec = parsed.get("spec", {})
        renderers = (
            spec.get("postRenderers", []) if isinstance(spec, dict) else []
        )
        if isinstance(renderers, list):
            for renderer in renderers:
                if not isinstance(renderer, dict):
                    continue
                kustomize = renderer.get("kustomize", {})
                raw_images = (
                    kustomize.get("images", [])
                    if isinstance(kustomize, dict)
                    else []
                )
                if isinstance(raw_images, list):
                    images.extend(
                        {
                            key: value
                            for key, value in image.items()
                            if key in {"name", "newName", "newTag", "digest"}
                            and isinstance(value, str)
                        }
                        for image in raw_images
                        if isinstance(image, dict)
                    )
    else:
        images.extend(_yaml_postrenderer_images(document))

    replacements = []
    for image in images:
        name = image.get("name")
        repository = image.get("newName", image.get("name"))
        digest = image.get("digest")
        if name:
            reference = f"{repository}@{digest}" if repository and digest else None
            replacements.append((name, reference))
    return tuple(replacements)


def _yaml_postrenderer_images(document: str) -> list[dict[str, str]]:
    images: list[dict[str, str]] = []
    lines = document.splitlines()
    stack: list[tuple[int, str]] = []
    postrenderers_indent: int | None = None
    for index, raw_line in enumerate(lines):
        match = re.match(
            r"^(?P<indent>[ ]*)(?P<key>[^:#][^:]*):(?P<value>.*)$",
            raw_line,
        )
        if match is None:
            continue
        indent = len(match.group("indent"))
        raw_key = match.group("key").strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        key = raw_key.removeprefix("- ").strip().strip("'\"")
        path = tuple(item[1] for item in stack) + (key,)
        if path == ("spec", "postRenderers"):
            postrenderers_indent = indent
        if not match.group("value").strip():
            stack.append((indent, key))
        if (
            postrenderers_indent is None
            or path[:2] != ("spec", "postRenderers")
            or path[-2:] != ("kustomize", "images")
            or indent <= postrenderers_indent
        ):
            continue

        inline_value = match.group("value").strip()
        if inline_value:
            images.extend(_yaml_flow_sequence_mappings(inline_value))
            continue

        images_indent = indent
        current: dict[str, str] | None = None
        for candidate in lines[index + 1 :]:
            if not candidate.strip() or candidate.lstrip().startswith("#"):
                continue
            candidate_indent = len(candidate) - len(candidate.lstrip(" "))
            if candidate_indent <= images_indent:
                break
            inline_item_match = re.match(
                r"^[ ]*-[ ]*(?P<value>\{.*\})[ ]*(?:#.*)?$",
                candidate,
            )
            item_match = re.match(
                r"^[ ]*-[ ]*(?P<key>[^:#][^:]*):(?P<value>.*)$",
                candidate,
            )
            field_match = re.match(
                r"^[ ]*(?P<key>[^:#][^:]*):(?P<value>.*)$",
                candidate,
            )
            if inline_item_match is not None:
                if current is not None:
                    images.append(current)
                    current = None
                mapping = _yaml_flow_mapping(inline_item_match.group("value"))
                if mapping is not None:
                    images.append(mapping)
            elif item_match is not None:
                if current is not None:
                    images.append(current)
                current = {
                    item_match.group("key").strip().strip("'\""): _strip_yaml_scalar(
                        item_match.group("value")
                    )
                }
            elif current is not None and field_match is not None:
                current[field_match.group("key").strip().strip("'\"")] = (
                    _strip_yaml_scalar(field_match.group("value"))
                )
        if current is not None:
            images.append(current)
    return images


def _effective_helm_value_items(
    document: str,
    values_sources: HelmValuesSources | None,
    release_path: Path | None,
    charts_root: Path,
) -> list[tuple[tuple[str, ...], str]] | None:
    effective: dict[tuple[str, ...], str] = {}
    chart_items = _local_chart_value_items(document, charts_root)
    if chart_items is None:
        return None
    _merge_helm_value_items(effective, chart_items)
    for kind, name, values_key, target_path, optional in _values_from_references(
        document
    ):
        namespace = dict(_document_scalars(document)).get(
            ("metadata", "namespace"), "default"
        )
        source = _scoped_helm_resource(
            values_sources or {}, kind, namespace, name, release_path
        )
        if not isinstance(source, dict):
            if optional:
                continue
            return None
        if values_key not in source:
            return None
        content = source[values_key]
        if target_path:
            path = tuple(part for part in target_path.split(".") if part)
            items = [(('spec', 'values', *path), content.strip())]
        else:
            items = [
                (("spec", "values", *path), value)
                for path, value in _document_scalars(content)
            ]
        _merge_helm_value_items(effective, items)
    inline_items: list[tuple[tuple[str, ...], str]] = []
    for path, value in _document_scalars(document):
        if path[:2] != ("spec", "values"):
            continue
        if path == ("spec", "values"):
            inline_items.extend(_yaml_flow_mapping_items(value, path))
        else:
            inline_items.append((path, value))
    _merge_helm_value_items(effective, inline_items)
    return list(effective.items())


def _is_component_helm_release(
    document: str,
    identity_matcher: Callable[[str | None], bool],
    admitted_images: set[str] | None = None,
    values_sources: HelmValuesSources | None = None,
    release_path: Path | None = None,
    charts_root: Path = CHARTS_ROOT,
    chart_references: HelmChartReferences | None = None,
) -> bool:
    scalar_items = _document_scalars(document)
    scalars = dict(scalar_items)
    identities = []
    for path in HELM_RELEASE_IDENTITY_PATHS:
        value = scalars.get(path)
        identities.append(value)
        if path == ("spec", "chart", "spec", "chart") and value:
            identities.append(PurePosixPath(value.rstrip("/")).name)
    identities.extend(
        _referenced_chart_identities(document, chart_references, release_path)
    )
    if scalars.get(("kind",)) != "HelmRelease" or not any(
        identity_matcher(identity) for identity in identities
    ):
        return False
    if admitted_images is None:
        return True
    if not _helm_release_chart_source_resolves(
        document, chart_references, release_path
    ):
        return False
    effective_items = _effective_helm_value_items(
        document, values_sources, release_path, charts_root
    )
    uses_admitted_image = (
        effective_items is not None
        and _helm_release_uses_admitted_image(effective_items, admitted_images)
    )
    component_replacements = [
        reference
        for name, reference in _postrenderer_images(document)
        if identity_matcher(name) or identity_matcher(PurePosixPath(name).name)
    ]
    if component_replacements:
        return component_replacements[-1] in admitted_images
    return uses_admitted_image


def _is_trino_workload(
    document: str,
    release_path: Path | None = None,
    chart_references: HelmChartReferences | None = None,
    admitted_images: set[str] | None = None,
) -> bool:
    scalar_items = _document_scalars(document)
    scalars = dict(scalar_items)
    container_images = {
        value
        for path, value in scalar_items
        if path == ("spec", "template", "spec", "containers", "image")
    }
    if scalars.get(("kind",)) == "HelmRelease":
        return _is_component_helm_release(
            document,
            _has_trino_identity,
            admitted_images,
            release_path=release_path,
            chart_references=chart_references,
        )
    return (
        scalars.get(("kind",)) in WORKLOAD_KINDS
        and any(
            _has_trino_identity(scalars.get(path))
            for path in (
                ("metadata", "name"),
                ("metadata", "labels", "app.kubernetes.io/name"),
            )
        )
        and (admitted_images is None or bool(container_images & admitted_images))
    )


def _trino_workload_manifests(
    deploy_root: Path = DEPLOY_ROOT,
    charts_root: Path = CHARTS_ROOT,
    admitted_images: set[str] | None = None,
) -> list[Path]:
    workloads = []
    chart_references = _helm_chart_references(deploy_root, charts_root)
    for path in _deployment_manifest_paths(deploy_root, charts_root):
        documents = re.split(
            r"(?m)^---[ \t]*(?:#.*)?$", path.read_text(encoding="utf-8")
        )
        if any(
            _is_trino_workload(
                document,
                path,
                chart_references,
                admitted_images,
            )
            for document in documents
        ):
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


def _is_postgresql_workload(
    document: str,
    admitted_images: set[str],
    values_sources: HelmValuesSources | None = None,
    release_path: Path | None = None,
    charts_root: Path = CHARTS_ROOT,
    chart_references: HelmChartReferences | None = None,
) -> bool:
    scalar_items = _document_scalars(document)
    scalars = dict(scalar_items)
    container_images = {
        value
        for path, value in scalar_items
        if path == ("spec", "template", "spec", "containers", "image")
    }
    if scalars.get(("kind",)) == "HelmRelease":
        return _is_component_helm_release(
            document,
            _has_postgresql_identity,
            admitted_images,
            values_sources,
            release_path,
            charts_root,
            chart_references,
        )
    return (
        scalars.get(("kind",)) in WORKLOAD_KINDS
        and any(
            _has_postgresql_identity(scalars.get(path))
            for path in (
                ("metadata", "name"),
                ("metadata", "labels", "app.kubernetes.io/name"),
            )
        )
        and bool(container_images & admitted_images)
    )


def _is_polaris_prerequisite_workload(
    document: str,
    admitted_images: set[str],
    values_sources: HelmValuesSources | None = None,
    release_path: Path | None = None,
    charts_root: Path = CHARTS_ROOT,
    chart_references: HelmChartReferences | None = None,
) -> bool:
    if _is_polaris_workload(document, admitted_images):
        return True
    return _is_component_helm_release(
        document,
        _has_polaris_identity,
        admitted_images,
        values_sources,
        release_path,
        charts_root,
        chart_references,
    )


def _polaris_prerequisite_workload_manifests(
    deploy_root: Path = DEPLOY_ROOT,
    charts_root: Path = CHARTS_ROOT,
    admitted_images: set[str] | None = None,
) -> list[Path]:
    if admitted_images is None:
        admitted_images = _admitted_polaris_image_references()
    values_sources = _helm_values_sources(deploy_root, charts_root)
    chart_references = _helm_chart_references(deploy_root, charts_root)
    workloads = set(
        _polaris_workload_manifests(
            deploy_root, charts_root, admitted_images=admitted_images
        )
    )
    for path in _deployment_manifest_paths(deploy_root, charts_root):
        documents = re.split(
            r"(?m)^---[ \t]*(?:#.*)?$", path.read_text(encoding="utf-8")
        )
        if any(
            _is_polaris_prerequisite_workload(
                document,
                admitted_images,
                values_sources,
                path,
                charts_root,
                chart_references,
            )
            for document in documents
        ):
            workloads.add(_display_path(path))
    return sorted(workloads)


def _postgresql_workload_manifests(
    deploy_root: Path = DEPLOY_ROOT,
    charts_root: Path = CHARTS_ROOT,
    admitted_images: set[str] | None = None,
) -> list[Path]:
    workloads = []
    if admitted_images is None:
        admitted_images = _admitted_postgresql_image_references()
    values_sources = _helm_values_sources(deploy_root, charts_root)
    chart_references = _helm_chart_references(deploy_root, charts_root)
    for path in _deployment_manifest_paths(deploy_root, charts_root):
        documents = re.split(
            r"(?m)^---[ \t]*(?:#.*)?$", path.read_text(encoding="utf-8")
        )
        if any(
            _is_postgresql_workload(
                document,
                admitted_images,
                values_sources,
                path,
                charts_root,
                chart_references,
            )
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

    def test_admission_aware_detection_requires_the_admitted_image(self) -> None:
        admitted_image = "registry.example/trino@sha256:" + "a" * 64
        manifest = (
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n"
            "  name: trino\n"
            "spec:\n"
            "  template:\n"
            "    spec:\n"
            "      containers:\n"
            "        - name: trino\n"
            f"          image: {admitted_image}\n"
        )

        self.assertFalse(
            _is_trino_workload(
                manifest,
                admitted_images={"registry.example/trino@sha256:" + "b" * 64},
            )
        )
        self.assertTrue(
            _is_trino_workload(manifest, admitted_images={admitted_image})
        )

    def test_admission_aware_helmrelease_requires_the_admitted_image(self) -> None:
        admitted_image = "registry.example/trino@sha256:" + "a" * 64
        manifest = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\n"
            "kind: HelmRelease\n"
            "metadata:\n"
            "  name: trino\n"
            "spec:\n"
            "  chart:\n"
            "    spec:\n"
            "      chart: trino\n"
            "      sourceRef:\n"
            "        kind: HelmRepository\n"
            "        name: trino\n"
            "  values:\n"
            "    image:\n"
            f"      reference: {admitted_image}\n"
        )
        chart_references = {
            ("HelmRepository", "default", "trino"): ("trino",)
        }

        self.assertFalse(
            _is_trino_workload(
                manifest,
                chart_references=chart_references,
                admitted_images={"registry.example/trino@sha256:" + "b" * 64},
            )
        )
        self.assertTrue(
            _is_trino_workload(
                manifest,
                chart_references=chart_references,
                admitted_images={admitted_image},
            )
        )

    def test_accepts_flux_helmrelease_identity(self) -> None:
        identities = (
            "metadata:\n  name: trino\n",
            "metadata:\n  name: query-engine\nspec:\n  releaseName: trino\n",
            (
                "metadata:\n  name: query-engine\nspec:\n  chart:\n"
                "    spec:\n      chart: trino\n"
            ),
            (
                "metadata:\n  name: query-engine\nspec:\n  chart:\n"
                "    spec:\n      chart: ./charts/trino\n"
            ),
            (
                "metadata:\n  name: query-engine\nspec:\n"
                "  chartRef:\n    kind: OCIRepository\n    name: trino\n"
            ),
        )
        for identity in identities:
            with self.subTest(identity=identity):
                manifest = (
                    "apiVersion: helm.toolkit.fluxcd.io/v2\n"
                    "kind: HelmRelease\n"
                    f"{identity}"
                )
                self.assertTrue(_is_trino_workload(manifest))

    def test_rejects_non_workload_trino_resource(self) -> None:
        manifest = "kind: Service\nmetadata:\n  name: trino\n"
        self.assertFalse(_is_trino_workload(manifest))

    def test_resolves_chart_ref_target_identity(self) -> None:
        source = (
            "apiVersion: source.toolkit.fluxcd.io/v1\n"
            "kind: OCIRepository\n"
            "metadata:\n"
            "  name: query-chart\n"
            "spec:\n"
            "  url: oci://registry.example/platform/trino\n"
        )
        release = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\n"
            "kind: HelmRelease\n"
            "metadata:\n"
            "  name: query-engine\n"
            "spec:\n"
            "  chartRef:\n"
            "    kind: OCIRepository\n"
            "    name: query-chart\n"
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            deploy_root = Path(temporary_directory) / "deploy"
            charts_root = Path(temporary_directory) / "charts"
            deploy_root.mkdir()
            charts_root.mkdir()
            manifest = deploy_root / "query.yaml"
            manifest.write_text(source + "---\n" + release, encoding="utf-8")

            workloads = _trino_workload_manifests(deploy_root, charts_root)

        self.assertEqual(workloads, [manifest])


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

    def test_accepts_postgresql_helmrelease_with_admitted_image(self) -> None:
        image = "registry.example/postgresql@sha256:" + "c" * 64
        manifest = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\n"
            "kind: HelmRelease\n"
            "metadata:\n"
            "  name: metadata-store\n"
            "spec:\n"
            "  chart:\n"
            "    spec:\n"
            "      chart: postgresql\n"
            "      sourceRef:\n"
            "        kind: HelmRepository\n"
            "        name: postgresql\n"
            "  values:\n"
            "    image:\n"
            f"      reference: {image}\n"
        )
        chart_references = {
            ("HelmRepository", "default", "postgresql"): ("postgresql",)
        }
        self.assertTrue(
            _is_postgresql_workload(
                manifest, {image}, chart_references=chart_references
            )
        )
        self.assertFalse(
            _is_postgresql_workload(
                manifest, set(), chart_references=chart_references
            )
        )

    def test_accepts_split_postgresql_helm_image_values(self) -> None:
        image = "cgr.dev/chainguard/postgres@sha256:" + "c" * 64
        manifest = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\n"
            "kind: HelmRelease\n"
            "metadata:\n"
            "  name: metadata-store\n"
            "spec:\n"
            "  chartRef:\n"
            "    kind: OCIRepository\n"
            "    name: postgresql\n"
            "  values:\n"
            "    image:\n"
            "      registry: cgr.dev\n"
            "      repository: chainguard/postgres\n"
            f"      digest: sha256:{'c' * 64}\n"
        )
        self.assertTrue(
            _is_postgresql_workload(
                manifest,
                {image},
                chart_references={
                    ("OCIRepository", "default", "postgresql"): ("postgresql",)
                },
            )
        )

    def test_rejects_image_fields_split_across_value_groups(self) -> None:
        image = "registry.example/postgresql@sha256:" + "c" * 64
        manifest = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\n"
            "kind: HelmRelease\n"
            "metadata:\n"
            "  name: postgresql\n"
            "spec:\n"
            "  values:\n"
            "    main:\n"
            "      repository: registry.example/postgresql\n"
            "    sidecar:\n"
            f"      digest: sha256:{'c' * 64}\n"
        )

        self.assertFalse(_is_postgresql_workload(manifest, {image}))

    def test_rejects_admitted_digest_outside_image_values(self) -> None:
        image = "registry.example/postgresql@sha256:" + "c" * 64
        other_image = "registry.example/postgresql@sha256:" + "d" * 64
        manifest = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\n"
            "kind: HelmRelease\n"
            "metadata:\n"
            "  name: postgresql\n"
            "spec:\n"
            "  values:\n"
            "    image:\n"
            f"      reference: {other_image}\n"
            f"      note: {image}\n"
        )

        self.assertFalse(_is_postgresql_workload(manifest, {image}))

    def test_accepts_values_from_configmap_with_admitted_image(self) -> None:
        image = "cgr.dev/chainguard/postgres@sha256:" + "c" * 64
        values = (
            "image:\n"
            "  registry: cgr.dev\n"
            "  repository: chainguard/postgres\n"
            f"  digest: sha256:{'c' * 64}\n"
        )
        source = (
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: postgresql-values\n"
            "  namespace: catalog\n"
            "data:\n"
            "  values.yaml: |\n"
            + "".join(f"    {line}\n" for line in values.splitlines())
        )
        chart_source = (
            "apiVersion: source.toolkit.fluxcd.io/v1\n"
            "kind: GitRepository\n"
            "metadata:\n"
            "  name: shirokuma\n"
            "  namespace: catalog\n"
        )
        release = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\n"
            "kind: HelmRelease\n"
            "metadata:\n"
            "  name: metadata-store\n"
            "  namespace: catalog\n"
            "spec:\n"
            "  chart:\n"
            "    spec:\n"
            "      chart: postgresql\n"
            "      sourceRef:\n"
            "        kind: GitRepository\n"
            "        name: shirokuma\n"
            "  valuesFrom:\n"
            "    - kind: ConfigMap\n"
            "      name: postgresql-values\n"
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            deploy_root = Path(temporary_directory) / "deploy"
            charts_root = Path(temporary_directory) / "charts"
            deploy_root.mkdir()
            charts_root.mkdir()
            manifest = deploy_root / "catalog.yaml"
            manifest.write_text(
                chart_source + "---\n" + source + "---\n" + release,
                encoding="utf-8",
            )

            workloads = _postgresql_workload_manifests(
                deploy_root, charts_root, {image}
            )

        self.assertEqual(workloads, [manifest])

    def test_values_from_is_scoped_to_the_release_overlay(self) -> None:
        image = "registry.example/postgresql@sha256:" + "c" * 64
        source_template = (
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: postgresql-values\n"
            "data:\n"
            "  values.yaml: |\n"
            "    image:\n"
            "      reference: {image}\n"
        )
        release = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\n"
            "kind: HelmRelease\n"
            "metadata:\n"
            "  name: postgresql\n"
            "spec:\n"
            "  chartRef:\n"
            "    kind: OCIRepository\n"
            "    name: postgresql\n"
            "  valuesFrom:\n"
            "    - kind: ConfigMap\n"
            "      name: postgresql-values\n"
        )
        chart_source = (
            "apiVersion: source.toolkit.fluxcd.io/v1\n"
            "kind: OCIRepository\n"
            "metadata:\n"
            "  name: postgresql\n"
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            deploy_root = Path(temporary_directory) / "deploy"
            charts_root = Path(temporary_directory) / "charts"
            overlay_a = deploy_root / "overlay-a"
            overlay_b = deploy_root / "overlay-b"
            overlay_a.mkdir(parents=True)
            overlay_b.mkdir(parents=True)
            charts_root.mkdir()
            admitted_manifest = overlay_a / "catalog.yaml"
            admitted_manifest.write_text(
                source_template.format(image=image)
                + "---\n"
                + chart_source
                + "---\n"
                + release,
                encoding="utf-8",
            )
            (overlay_b / "values.yaml").write_text(
                source_template.format(
                    image="registry.example/other@sha256:" + "d" * 64
                ),
                encoding="utf-8",
            )

            workloads = _postgresql_workload_manifests(
                deploy_root, charts_root, {image}
            )

        self.assertEqual(workloads, [admitted_manifest])

    def test_values_from_does_not_bind_to_another_overlay(self) -> None:
        image = "registry.example/postgresql@sha256:" + "c" * 64
        source = (
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: postgresql-values\n"
            "data:\n"
            "  values.yaml: |\n"
            "    image:\n"
            f"      reference: {image}\n"
        )
        release = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\n"
            "kind: HelmRelease\n"
            "metadata:\n"
            "  name: postgresql\n"
            "spec:\n"
            "  valuesFrom:\n"
            "    - kind: ConfigMap\n"
            "      name: postgresql-values\n"
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            deploy_root = Path(temporary_directory) / "deploy"
            charts_root = Path(temporary_directory) / "charts"
            overlay_a = deploy_root / "overlay-a"
            overlay_b = deploy_root / "overlay-b"
            overlay_a.mkdir(parents=True)
            overlay_b.mkdir(parents=True)
            charts_root.mkdir()
            (overlay_a / "release.yaml").write_text(release, encoding="utf-8")
            (overlay_b / "values.yaml").write_text(source, encoding="utf-8")

            workloads = _postgresql_workload_manifests(
                deploy_root, charts_root, {image}
            )

        self.assertEqual(workloads, [])

    def test_accepts_local_chart_effective_values(self) -> None:
        image = "registry.example/postgresql@sha256:" + "c" * 64
        cases = (
            ("values.yaml", ""),
            (
                "values-arm64.yaml",
                "      valuesFiles:\n"
                "        - charts/postgresql/values-arm64.yaml\n",
            ),
        )
        for values_file, values_files_field in cases:
            with self.subTest(values_file=values_file), tempfile.TemporaryDirectory() as temporary_directory:
                deploy_root = Path(temporary_directory) / "deploy"
                charts_root = Path(temporary_directory) / "charts"
                chart_root = charts_root / "postgresql"
                deploy_root.mkdir()
                chart_root.mkdir(parents=True)
                (chart_root / "values.yaml").write_text("replicas: 1\n", encoding="utf-8")
                (chart_root / values_file).write_text(
                    f"image:\n  reference: {image}\n", encoding="utf-8"
                )
                release = deploy_root / "postgresql.yaml"
                release.write_text(
                    "apiVersion: source.toolkit.fluxcd.io/v1\n"
                    "kind: GitRepository\n"
                    "metadata:\n"
                    "  name: shirokuma\n"
                    "---\n"
                    "apiVersion: helm.toolkit.fluxcd.io/v2\n"
                    "kind: HelmRelease\n"
                    "metadata:\n"
                    "  name: metadata-store\n"
                    "spec:\n"
                    "  chart:\n"
                    "    spec:\n"
                    "      chart: ./charts/postgresql\n"
                    "      sourceRef:\n"
                    "        kind: GitRepository\n"
                    "        name: shirokuma\n"
                    f"{values_files_field}",
                    encoding="utf-8",
                )

                workloads = _postgresql_workload_manifests(
                    deploy_root, charts_root, {image}
                )

            self.assertEqual(workloads, [release])

    def test_inline_values_files_override_default_chart_values(self) -> None:
        image = "registry.example/postgresql@sha256:" + "c" * 64
        other_image = "registry.example/other@sha256:" + "d" * 64
        with tempfile.TemporaryDirectory() as temporary_directory:
            deploy_root = Path(temporary_directory) / "deploy"
            charts_root = Path(temporary_directory) / "charts"
            chart_root = charts_root / "postgresql"
            deploy_root.mkdir()
            chart_root.mkdir(parents=True)
            (chart_root / "values.yaml").write_text(
                f"image:\n  reference: {image}\n", encoding="utf-8"
            )
            (chart_root / "values-arm64.yaml").write_text(
                f"image:\n  reference: {other_image}\n", encoding="utf-8"
            )
            release = deploy_root / "postgresql.yaml"
            release.write_text(
                "apiVersion: helm.toolkit.fluxcd.io/v2\n"
                "kind: HelmRelease\n"
                "metadata:\n"
                "  name: postgresql\n"
                "spec:\n"
                "  chart:\n"
                "    spec:\n"
                "      chart: ./charts/postgresql\n"
                "      valuesFiles: [values-arm64.yaml]\n",
                encoding="utf-8",
            )

            workloads = _postgresql_workload_manifests(
                deploy_root, charts_root, {image}
            )

        self.assertEqual(workloads, [])

    def test_missing_local_chart_rejects_inline_image_values(self) -> None:
        image = "registry.example/postgresql@sha256:" + "c" * 64
        manifest = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\n"
            "kind: HelmRelease\n"
            "metadata:\n"
            "  name: postgresql\n"
            "spec:\n"
            "  chart:\n"
            "    spec:\n"
            "      chart: ./charts/postgresql\n"
            "  values:\n"
            "    image:\n"
            f"      reference: {image}\n"
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            charts_root = Path(temporary_directory) / "charts"
            charts_root.mkdir()

            self.assertFalse(
                _is_postgresql_workload(
                    manifest, {image}, charts_root=charts_root
                )
            )

    def test_rejects_admitted_image_overridden_by_later_helm_values(self) -> None:
        image = "registry.example/postgresql@sha256:" + "c" * 64
        other_image = "registry.example/other@sha256:" + "d" * 64
        sources = {
            ("ConfigMap", "default", "admitted-values"): {
                "values.yaml": f"image:\n  reference: {image}\n"
            },
            ("ConfigMap", "default", "override-values"): {
                "values.yaml": f"image:\n  reference: {other_image}\n"
            },
        }
        releases = (
            (
                "apiVersion: helm.toolkit.fluxcd.io/v2\n"
                "kind: HelmRelease\n"
                "metadata:\n"
                "  name: postgresql\n"
                "spec:\n"
                "  valuesFrom:\n"
                "    - kind: ConfigMap\n"
                "      name: admitted-values\n"
                "    - kind: ConfigMap\n"
                "      name: override-values\n"
            ),
            (
                "apiVersion: helm.toolkit.fluxcd.io/v2\n"
                "kind: HelmRelease\n"
                "metadata:\n"
                "  name: postgresql\n"
                "spec:\n"
                "  valuesFrom:\n"
                "    - kind: ConfigMap\n"
                "      name: admitted-values\n"
                "  values:\n"
                "    image:\n"
                f"      reference: {other_image}\n"
            ),
        )

        for release in releases:
            with self.subTest(release=release):
                self.assertFalse(
                    _is_postgresql_workload(release, {image}, sources)
                )

    def test_accepts_postrenderer_admitted_image(self) -> None:
        image = "registry.example/postgresql@sha256:" + "c" * 64
        release = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\n"
            "kind: HelmRelease\n"
            "metadata:\n"
            "  name: postgresql\n"
            "spec:\n"
            "  postRenderers:\n"
            "    - kustomize:\n"
            "        images:\n"
            "          - name: postgresql\n"
            "            newName: registry.example/postgresql\n"
            f"            digest: sha256:{'c' * 64}\n"
        )

        self.assertTrue(_is_postgresql_workload(release, {image}))

    def test_unrelated_postrenderer_preserves_admitted_component_image(self) -> None:
        image = "registry.example/postgresql@sha256:" + "c" * 64
        release = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\n"
            "kind: HelmRelease\n"
            "metadata:\n"
            "  name: postgresql\n"
            "spec:\n"
            "  values:\n"
            "    image:\n"
            f"      reference: {image}\n"
            "  postRenderers:\n"
            "    - kustomize:\n"
            "        images:\n"
            "          - name: metrics-sidecar\n"
            "            newName: registry.example/metrics-sidecar\n"
            f"            digest: sha256:{'d' * 64}\n"
        )

        self.assertTrue(_is_postgresql_workload(release, {image}))

    def test_component_postrenderer_overrides_admitted_values(self) -> None:
        image = "registry.example/postgresql@sha256:" + "c" * 64
        release = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\n"
            "kind: HelmRelease\n"
            "metadata:\n"
            "  name: postgresql\n"
            "spec:\n"
            "  values:\n"
            "    image:\n"
            f"      reference: {image}\n"
            "  postRenderers:\n"
            "    - kustomize:\n"
            "        images:\n"
            "          - name: postgresql\n"
            "            newName: registry.example/other\n"
            f"            digest: sha256:{'d' * 64}\n"
        )

        self.assertFalse(_is_postgresql_workload(release, {image}))

    def test_inline_postrenderer_overrides_admitted_values(self) -> None:
        image = "registry.example/postgresql@sha256:" + "c" * 64
        inline_images = (
            "        images: [{name: postgresql, newName: registry.example/other, "
            f"digest: sha256:{'d' * 64}}}]\n",
            "        images:\n"
            "          - {name: postgresql, newName: registry.example/other, "
            f"digest: sha256:{'d' * 64}}}\n",
        )
        for images in inline_images:
            with self.subTest(images=images):
                release = (
                    "apiVersion: helm.toolkit.fluxcd.io/v2\n"
                    "kind: HelmRelease\n"
                    "metadata:\n"
                    "  name: postgresql\n"
                    "spec:\n"
                    "  values:\n"
                    "    image:\n"
                    f"      reference: {image}\n"
                    "  postRenderers:\n"
                    "    - kustomize:\n"
                    f"{images}"
                )

                self.assertFalse(_is_postgresql_workload(release, {image}))

    def test_values_from_requires_the_referenced_source_and_namespace(self) -> None:
        image = "registry.example/postgresql@sha256:" + "c" * 64
        release = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\n"
            "kind: HelmRelease\n"
            "metadata:\n"
            "  name: postgresql\n"
            "  namespace: catalog\n"
            "spec:\n"
            "  valuesFrom:\n"
            "    - kind: ConfigMap\n"
            "      name: selected-values\n"
        )
        unbound_sources = {
            ("ConfigMap", "catalog", "other-values"): {"values.yaml": image},
            ("ConfigMap", "other", "selected-values"): {"values.yaml": image},
        }
        self.assertFalse(
            _is_postgresql_workload(release, {image}, unbound_sources)
        )

    def test_values_from_requires_a_discovered_source(self) -> None:
        image = "registry.example/postgresql@sha256:" + "c" * 64
        release = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\n"
            "kind: HelmRelease\n"
            "metadata:\n"
            "  name: postgresql\n"
            "spec:\n"
            "  valuesFrom:\n"
            "    - kind: ConfigMap\n"
            "      name: missing-values\n"
            "  values:\n"
            "    image:\n"
            f"      reference: {image}\n"
        )

        self.assertFalse(_is_postgresql_workload(release, {image}, {}))

    def test_optional_values_from_allows_an_absent_source(self) -> None:
        image = "registry.example/postgresql@sha256:" + "c" * 64
        release = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\n"
            "kind: HelmRelease\n"
            "metadata:\n"
            "  name: postgresql\n"
            "spec:\n"
            "  chartRef:\n"
            "    kind: OCIRepository\n"
            "    name: postgresql\n"
            "  valuesFrom:\n"
            "    - kind: ConfigMap\n"
            "      name: optional-values\n"
            "      optional: true\n"
            "  values:\n"
            "    image:\n"
            f"      reference: {image}\n"
        )

        self.assertTrue(
            _is_postgresql_workload(
                release,
                {image},
                {},
                chart_references={
                    ("OCIRepository", "default", "postgresql"): ("postgresql",)
                },
            )
        )

    def test_accepts_flow_style_values_from(self) -> None:
        image = "registry.example/postgresql@sha256:" + "c" * 64
        release = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\n"
            "kind: HelmRelease\n"
            "metadata:\n"
            "  name: postgresql\n"
            "spec:\n"
            "  chartRef:\n"
            "    kind: OCIRepository\n"
            "    name: postgresql\n"
            "  valuesFrom: [{kind: ConfigMap, name: postgresql-values}]\n"
        )
        sources = {
            ("ConfigMap", "default", "postgresql-values"): {
                "values.yaml": f"image:\n  reference: {image}\n"
            }
        }

        self.assertTrue(
            _is_postgresql_workload(
                release,
                {image},
                sources,
                chart_references={
                    ("OCIRepository", "default", "postgresql"): ("postgresql",)
                },
            )
        )

    def test_accepts_flow_style_inline_values(self) -> None:
        image = "registry.example/postgresql@sha256:" + "c" * 64
        release = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\n"
            "kind: HelmRelease\n"
            "metadata:\n"
            "  name: postgresql\n"
            "spec:\n"
            "  chartRef:\n"
            "    kind: OCIRepository\n"
            "    name: postgresql\n"
            f"  values: {{image: {{reference: {image}}}}}\n"
        )

        self.assertTrue(
            _is_postgresql_workload(
                release,
                {image},
                chart_references={
                    ("OCIRepository", "default", "postgresql"): ("postgresql",)
                },
            )
        )

    def test_accepts_mixed_block_and_flow_inline_values(self) -> None:
        image = "registry.example/postgresql@sha256:" + "c" * 64
        release = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\n"
            "kind: HelmRelease\n"
            "metadata:\n"
            "  name: postgresql\n"
            "spec:\n"
            "  chartRef:\n"
            "    kind: OCIRepository\n"
            "    name: postgresql\n"
            "  values:\n"
            f"    image: {{reference: {image}}}\n"
        )

        self.assertTrue(
            _is_postgresql_workload(
                release,
                {image},
                chart_references={
                    ("OCIRepository", "default", "postgresql"): ("postgresql",)
                },
            )
        )

    def test_ignore_missing_values_files_preserves_inline_values(self) -> None:
        image = "registry.example/postgresql@sha256:" + "c" * 64
        with tempfile.TemporaryDirectory() as temporary_directory:
            charts_root = Path(temporary_directory) / "charts"
            chart_root = charts_root / "postgresql"
            chart_root.mkdir(parents=True)
            (chart_root / "values.yaml").write_text("replicas: 1\n", encoding="utf-8")
            release = (
                "apiVersion: helm.toolkit.fluxcd.io/v2\n"
                "kind: HelmRelease\n"
                "metadata:\n"
                "  name: postgresql\n"
                "spec:\n"
                "  chart:\n"
                "    spec:\n"
                "      chart: ./charts/postgresql\n"
                "      sourceRef:\n"
                "        kind: GitRepository\n"
                "        name: shirokuma\n"
                "      valuesFiles: [env/missing.yaml]\n"
                "      ignoreMissingValuesFiles: true\n"
                "  values:\n"
                "    image:\n"
                f"      reference: {image}\n"
            )

            self.assertTrue(
                _is_postgresql_workload(
                    release,
                    {image},
                    charts_root=charts_root,
                    chart_references={
                        ("GitRepository", "default", "shirokuma"): ("shirokuma",)
                    },
                )
            )

    def test_resolves_values_files_from_the_source_root(self) -> None:
        image = "registry.example/postgresql@sha256:" + "c" * 64
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_root = Path(temporary_directory)
            charts_root = source_root / "charts"
            chart_root = charts_root / "postgresql"
            values_root = source_root / "env"
            chart_root.mkdir(parents=True)
            values_root.mkdir()
            (chart_root / "values.yaml").write_text("replicas: 1\n", encoding="utf-8")
            (values_root / "postgresql.yaml").write_text(
                f"image:\n  reference: {image}\n", encoding="utf-8"
            )
            release = (
                "apiVersion: helm.toolkit.fluxcd.io/v2\n"
                "kind: HelmRelease\n"
                "metadata:\n"
                "  name: postgresql\n"
                "spec:\n"
                "  chart:\n"
                "    spec:\n"
                "      chart: ./charts/postgresql\n"
                "      sourceRef:\n"
                "        kind: GitRepository\n"
                "        name: shirokuma\n"
                "      valuesFiles: [env/postgresql.yaml]\n"
            )

            self.assertTrue(
                _is_postgresql_workload(
                    release,
                    {image},
                    charts_root=charts_root,
                    chart_references={
                        ("GitRepository", "default", "shirokuma"): ("shirokuma",)
                    },
                )
            )

    def test_postrenderer_image_fields_can_precede_name(self) -> None:
        image = "registry.example/postgresql@sha256:" + "c" * 64
        release = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\n"
            "kind: HelmRelease\n"
            "metadata:\n"
            "  name: postgresql\n"
            "spec:\n"
            "  postRenderers:\n"
            "    - kustomize:\n"
            "        images:\n"
            "          - newName: registry.example/postgresql\n"
            f"            digest: sha256:{'c' * 64}\n"
            "            name: postgresql\n"
        )

        self.assertTrue(_is_postgresql_workload(release, {image}))

    def test_checks_every_postrenderer(self) -> None:
        image = "registry.example/postgresql@sha256:" + "c" * 64
        release = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\n"
            "kind: HelmRelease\n"
            "metadata:\n"
            "  name: postgresql\n"
            "spec:\n"
            "  postRenderers:\n"
            "    - kustomize:\n"
            "        images:\n"
            "          - name: metrics-sidecar\n"
            "            newName: registry.example/metrics-sidecar\n"
            f"            digest: sha256:{'d' * 64}\n"
            "    - kustomize:\n"
            "        images:\n"
            "          - name: postgresql\n"
            "            newName: registry.example/postgresql\n"
            f"            digest: sha256:{'c' * 64}\n"
        )

        self.assertTrue(_is_postgresql_workload(release, {image}))

    def test_last_component_postrenderer_override_wins(self) -> None:
        image = "registry.example/postgresql@sha256:" + "c" * 64
        release = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\n"
            "kind: HelmRelease\n"
            "metadata:\n"
            "  name: postgresql\n"
            "spec:\n"
            "  chartRef:\n"
            "    kind: OCIRepository\n"
            "    name: postgresql\n"
            "  postRenderers:\n"
            "    - kustomize:\n"
            "        images:\n"
            "          - name: postgresql\n"
            "            newName: registry.example/postgresql\n"
            f"            digest: sha256:{'c' * 64}\n"
            "    - kustomize:\n"
            "        images:\n"
            "          - name: postgresql\n"
            "            newName: registry.example/postgresql\n"
            f"            digest: sha256:{'d' * 64}\n"
        )

        self.assertFalse(
            _is_postgresql_workload(
                release,
                {image},
                chart_references={
                    ("OCIRepository", "default", "postgresql"): ("postgresql",)
                },
            )
        )

    def test_preserves_json_postrenderer_detection(self) -> None:
        image = "registry.example/postgresql@sha256:" + "c" * 64
        release = json.dumps(
            {
                "apiVersion": "helm.toolkit.fluxcd.io/v2",
                "kind": "HelmRelease",
                "metadata": {"name": "postgresql"},
                "spec": {
                    "postRenderers": [
                        {
                            "kustomize": {
                                "images": [
                                    {
                                        "digest": "sha256:" + "c" * 64,
                                        "newName": "registry.example/postgresql",
                                        "name": "postgresql",
                                    }
                                ]
                            }
                        }
                    ]
                },
            }
        )

        self.assertTrue(_is_postgresql_workload(release, {image}))

    def test_requires_a_resolvable_helm_chart_source(self) -> None:
        image = "registry.example/postgresql@sha256:" + "c" * 64
        releases = (
            (
                "  chart:\n"
                "    spec:\n"
                "      chart: postgresql\n"
            ),
            (
                "  chartRef:\n"
                "    kind: OCIRepository\n"
                "    name: postgresql\n"
            ),
        )
        for chart in releases:
            with self.subTest(chart=chart):
                release = (
                    "apiVersion: helm.toolkit.fluxcd.io/v2\n"
                    "kind: HelmRelease\n"
                    "metadata:\n"
                    "  name: postgresql\n"
                    "spec:\n"
                    f"{chart}"
                    "  values:\n"
                    "    image:\n"
                    f"      reference: {image}\n"
                )
                self.assertFalse(
                    _is_postgresql_workload(
                        release, {image}, chart_references={}
                    )
                )

    def test_rejects_helmrelease_without_chart_reference(self) -> None:
        image = "registry.example/postgresql@sha256:" + "c" * 64
        release = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\n"
            "kind: HelmRelease\n"
            "metadata:\n"
            "  name: postgresql\n"
            "spec:\n"
            "  values:\n"
            "    image:\n"
            f"      reference: {image}\n"
        )

        self.assertFalse(
            _is_postgresql_workload(release, {image}, chart_references={})
        )

    def test_rejects_unsupported_chart_ref_kind(self) -> None:
        image = "registry.example/postgresql@sha256:" + "c" * 64
        release = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\n"
            "kind: HelmRelease\n"
            "metadata:\n"
            "  name: postgresql\n"
            "spec:\n"
            "  chartRef:\n"
            "    kind: GitRepository\n"
            "    name: postgresql\n"
            "  values:\n"
            "    image:\n"
            f"      reference: {image}\n"
        )

        self.assertFalse(
            _is_postgresql_workload(
                release,
                {image},
                chart_references={
                    ("GitRepository", "default", "postgresql"): ("postgresql",)
                },
            )
        )

    def test_resolves_explicitly_namespaced_shared_chart_source(self) -> None:
        image = "registry.example/postgresql@sha256:" + "c" * 64
        release = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\n"
            "kind: HelmRelease\n"
            "metadata:\n"
            "  name: postgresql\n"
            "  namespace: catalog\n"
            "spec:\n"
            "  chart:\n"
            "    spec:\n"
            "      chart: postgresql\n"
            "      sourceRef:\n"
            "        kind: GitRepository\n"
            "        name: flux-system\n"
            "        namespace: flux-system\n"
            "  values:\n"
            "    image:\n"
            f"      reference: {image}\n"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            release_path = root / "catalog" / "release.yaml"
            shared_source_dir = root / "flux-system"
            chart_references = {
                (
                    shared_source_dir.resolve(),
                    "GitRepository",
                    "flux-system",
                    "flux-system",
                ): ("flux-system",)
            }
            self.assertTrue(
                _is_postgresql_workload(
                    release,
                    {image},
                    release_path=release_path,
                    chart_references=chart_references,
                )
            )

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
            (
                "kind: Deployment\n"
                "metadata:\n"
                "  name: metadata-store\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - image: registry.example/other@sha256:"
                + "d" * 64
                + "\n",
                {image},
            ),
            (
                "kind: StatefulSet\n"
                "metadata:\n"
                "  name: unrelated-cache\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                f"        - image: {image}\n",
                {image},
            ),
        )
        for manifest, admitted_images in manifests:
            with self.subTest(manifest=manifest, admitted_images=admitted_images):
                self.assertFalse(
                    _is_postgresql_workload(manifest, admitted_images)
                )


class PolarisPrerequisiteWorkloadDetectionTests(unittest.TestCase):
    def test_accepts_polaris_helmrelease_with_admitted_image(self) -> None:
        image = "registry.example/polaris@sha256:" + "a" * 64
        manifest = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\n"
            "kind: HelmRelease\n"
            "metadata:\n"
            "  name: catalog\n"
            "spec:\n"
            "  releaseName: polaris\n"
            "  values:\n"
            "    image:\n"
            "      repository: registry.example/polaris\n"
            f"      digest: sha256:{'a' * 64}\n"
        )
        self.assertTrue(_is_polaris_prerequisite_workload(manifest, {image}))
        self.assertFalse(_is_polaris_prerequisite_workload(manifest, set()))

    def test_accepts_polaris_chart_ref_with_referenced_values(self) -> None:
        image = "registry.example/polaris@sha256:" + "a" * 64
        manifest = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\n"
            "kind: HelmRelease\n"
            "metadata:\n"
            "  name: catalog\n"
            "spec:\n"
            "  chartRef:\n"
            "    kind: OCIRepository\n"
            "    name: polaris\n"
            "  valuesFrom:\n"
            "    - kind: Secret\n"
            "      name: polaris-values\n"
        )
        sources = {
            ("Secret", "default", "polaris-values"): {
                "values.yaml": f"image:\n  reference: {image}\n"
            }
        }
        self.assertTrue(
            _is_polaris_prerequisite_workload(
                manifest,
                {image},
                sources,
                chart_references={
                    ("OCIRepository", "default", "polaris"): ("polaris",)
                },
            )
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
        polaris_workloads = _polaris_prerequisite_workload_manifests(
            admitted_images=polaris_images
        )
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
