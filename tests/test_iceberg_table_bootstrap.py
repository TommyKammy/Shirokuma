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
    ("spec", "chart", "spec", "chart"),
    ("spec", "chartRef", "name"),
)
HELM_VALUE_IDENTITY_FIELDS = {
    "app.kubernetes.io/name",
    "containername",
    "cronjobname",
    "fullnameoverride",
    "image",
    "jobname",
    "nameoverride",
    "resourcename",
    "workloadname",
}
HELM_VALUE_IMAGE_FIELDS = {"repository", "tag"}
HELM_VALUE_IDENTITY_NAME_PARENTS = {
    "container",
    "cronjob",
    "image",
    "job",
    "metadata",
    "resource",
    "workload",
}
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
IdentityState = dict[tuple[str, ...], str]


class ManifestResource(NamedTuple):
    path: Path
    document: str
    scalar_items: ScalarItems
    namespace: str
    name_prefix: str = ""
    name_suffix: str = ""


class HelmValuesReference(NamedTuple):
    kind: str
    name: str
    values_key: str
    target_path: str | None


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
        value = _strip_yaml_comment(match.group("value")).strip()
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
        scalar_items = list(_mapping_scalars(document))
        ordered_items: ScalarItems = []
        for path, value in scalar_items:
            ordered_items.append((path, value))
            if value.strip().startswith(("{", "[")):
                ordered_items.extend(
                    _flow_value_scalar_items(value, path)
                )
        return _resolve_yaml_scalar_aliases(ordered_items)
    return list(_json_scalars(parsed))


def _strip_yaml_comment(value: str) -> str:
    quote: str | None = None
    escaped = False
    for index, character in enumerate(value):
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\" and quote == '"':
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
        elif character == "#" and (
            index == 0 or value[index - 1].isspace()
        ):
            return value[:index]
    return value


def _strip_yaml_scalar(value: str) -> str:
    value = _strip_yaml_comment(value).strip()
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return value[1:-1]
        return parsed if isinstance(parsed, str) else str(parsed)
    if len(value) >= 2 and value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    return value


def _split_flow_collection(value: str) -> list[str]:
    value = value.strip()
    if (
        len(value) < 2
        or (value[0], value[-1]) not in {("{", "}"), ("[", "]")}
    ):
        return []

    items: list[str] = []
    start = 1
    depth = 0
    quote: str | None = None
    escaped = False
    for index, character in enumerate(value[1:-1], start=1):
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\" and quote == '"':
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
    final = value[start:-1].strip()
    if final:
        items.append(final)
    return items


def _split_flow_mapping_item(item: str) -> tuple[str, str] | None:
    depth = 0
    quote: str | None = None
    escaped = False
    for index, character in enumerate(item):
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\" and quote == '"':
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
        elif character == ":" and depth == 0:
            key = _strip_yaml_scalar(item[:index])
            if key:
                return key, item[index + 1 :].strip()
            return None
    return None


def _flow_mapping_entries(value: str) -> list[tuple[str, str]]:
    if not value.strip().startswith("{"):
        return []
    return [
        entry
        for item in _split_flow_collection(value)
        if (entry := _split_flow_mapping_item(item)) is not None
    ]


def _flow_value_scalar_items(
    value: str, path: tuple[str, ...]
) -> ScalarItems:
    value = value.strip()
    if value.startswith("{") and value.endswith("}"):
        return [
            item
            for key, nested_value in _flow_mapping_entries(value)
            for item in _flow_value_scalar_items(
                nested_value, path + (key,)
            )
        ]
    if value.startswith("[") and value.endswith("]"):
        return [
            item
            for nested_value in _split_flow_collection(value)
            for item in _flow_value_scalar_items(nested_value, path)
        ]
    return [(path, _strip_yaml_scalar(value))] if value else []


def _resolve_yaml_scalar_aliases(
    scalar_items: ScalarItems,
) -> ScalarItems:
    anchors: dict[str, str] = {}
    anchor_pattern = re.compile(
        r"^&(?P<name>[A-Za-z0-9_-]+)\s+(?P<value>.+)$"
    )
    resolved: ScalarItems = []
    for path, value in scalar_items:
        stripped = value.strip()
        anchor_match = anchor_pattern.match(stripped)
        alias_match = re.fullmatch(
            r"\*(?P<name>[A-Za-z0-9_-]+)", stripped
        )
        if anchor_match is not None:
            value = _strip_yaml_scalar(anchor_match.group("value"))
            anchors[anchor_match.group("name")] = value
        elif alias_match is not None:
            value = anchors.get(alias_match.group("name"), value)
        resolved.append((path, value))
    return resolved


def _flow_mapping_fields(value: str) -> dict[str, str]:
    return {
        key: _strip_yaml_scalar(field_value)
        for key, field_value in _flow_mapping_entries(value)
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
        inline_value = _strip_yaml_comment(
            match.group("value")
        ).strip()
        if path != target_path:
            if not inline_value:
                stack.append((indent, key))
            continue

        if inline_value:
            return [
                _flow_mapping_fields(mapping)
                for mapping in re.findall(r"\{[^{}]*\}", inline_value)
            ]

        entries: list[dict[str, str]] = []
        current: dict[str, str] | None = None
        item_indent: int | None = None
        field_indent: int | None = None
        for candidate in lines[index + 1 :]:
            if not candidate.strip() or candidate.lstrip().startswith("#"):
                continue
            candidate_indent = len(candidate) - len(candidate.lstrip(" "))
            item_match = re.match(
                r"^[ ]*-[ ]*(?P<value>.*?)[ ]*$", candidate
            )
            if candidate_indent < indent or (
                candidate_indent == indent and item_match is None
            ):
                break
            if item_match is not None and (
                item_indent is None or candidate_indent == item_indent
            ):
                if current is not None:
                    entries.append(current)
                current = {}
                item_indent = candidate_indent
                field_indent = None
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
            if item_match is not None:
                continue
            field_match = re.match(
                r"^[ ]*(?P<field>[^:#][^:]*):(?P<value>.*)$",
                candidate,
            )
            if field_match is None or candidate_indent <= item_indent:
                return []
            if field_indent is None:
                field_indent = candidate_indent
            if candidate_indent != field_indent:
                continue
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
        inline_value = _strip_yaml_comment(
            match.group("value")
        ).strip()
        if path != target_path:
            if not inline_value:
                stack.append((indent, key))
            continue

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
            item_match = re.match(r"^[ ]*-[ ]*(?P<value>.+?)\s*$", candidate)
            if candidate_indent < indent or (
                candidate_indent == indent and item_match is None
            ):
                break
            if item_match is None:
                return []
            values.append(_strip_yaml_scalar(item_match.group("value")))
        return values
    return []


def _yaml_sequence_blocks(
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
        return [
            json.dumps(item)
            for item in value
            if isinstance(item, dict)
        ] if isinstance(value, list) else []

    lines = document.splitlines()
    stack: list[tuple[int, str]] = []
    target_index: int | None = None
    target_indent = 0
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
        inline_value = _strip_yaml_comment(
            match.group("value")
        ).strip()
        if path == target_path:
            if inline_value:
                return []
            target_index = index
            target_indent = indent
            break
        if not inline_value:
            stack.append((indent, key))
    if target_index is None:
        return []

    blocks: list[list[str]] = []
    current: list[str] | None = None
    item_indent: int | None = None
    for raw_line in lines[target_index + 1 :]:
        if not raw_line.strip():
            if current is not None:
                current.append("")
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        item_match = re.match(r"^[ ]*-[ ]*", raw_line)
        if indent < target_indent or (
            indent == target_indent and item_match is None
        ):
            break
        if (
            item_match is not None
            and (item_indent is None or indent == item_indent)
        ):
            if current is not None:
                blocks.append(current)
            item_indent = indent
            current = [raw_line[item_indent:]]
            continue
        if current is not None and item_indent is not None:
            current.append(raw_line[min(len(raw_line), item_indent) :])
    if current is not None:
        blocks.append(current)
    return ["\n".join(block) for block in blocks]


def _yaml_string_mapping(document: str, field: str) -> dict[str, str]:
    lines = document.splitlines()
    entries: dict[str, str] = {}
    for index, raw_line in enumerate(lines):
        field_match = re.match(
            rf"^{re.escape(field)}:[ ]*(?P<value>.*)$", raw_line
        )
        if field_match is None:
            continue
        inline_value = field_match.group("value").strip()
        if inline_value and not inline_value.startswith("#"):
            return {
                key: _strip_yaml_scalar(value)
                for key, value in _flow_mapping_entries(inline_value)
            }
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


def _yaml_generator_entries(
    document: str, field: str
) -> list[dict[str, object]]:
    try:
        parsed = json.loads(document)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        entries = parsed.get(field)
        return [
            {str(key): value for key, value in entry.items()}
            for entry in entries
            if isinstance(entry, dict)
        ] if isinstance(entries, list) else []

    lines = document.splitlines()
    field_index = next(
        (
            index
            for index, line in enumerate(lines)
            if re.match(rf"^{re.escape(field)}:[ ]*(?:#.*)?$", line)
        ),
        None,
    )
    if field_index is None:
        return []

    entries: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    item_indent: int | None = None
    active_list: str | None = None
    active_list_indent: int | None = None
    for line in lines[field_index + 1 :]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        sequence_match = re.match(
            r"^[ ]*-[ ]*(?P<value>.*?)[ ]*$", line
        )
        is_outer_item = (
            sequence_match is not None
            and (item_indent is None or indent == item_indent)
        )
        if indent == 0 and not is_outer_item:
            break
        if is_outer_item:
            if current is not None:
                entries.append(current)
            current = {}
            item_indent = indent
            active_list = None
            active_list_indent = None
            value = sequence_match.group("value")
            field_match = re.match(
                r"(?P<field>[^:#][^:]*):(?P<value>.*)$", value
            )
            if field_match is not None:
                current[field_match.group("field").strip()] = (
                    _strip_yaml_scalar(field_match.group("value"))
                )
            continue
        if current is None or item_indent is None:
            return []
        if (
            sequence_match is not None
            and active_list is not None
            and active_list_indent is not None
            and indent >= active_list_indent
        ):
            values = current.setdefault(active_list, [])
            if isinstance(values, list):
                values.append(
                    _strip_yaml_scalar(sequence_match.group("value"))
                )
            continue
        mapping_match = re.match(
            r"^[ ]*(?P<field>[^:#][^:]*):(?P<value>.*)$", line
        )
        if mapping_match is None or indent <= item_indent:
            return []
        key = mapping_match.group("field").strip()
        value = mapping_match.group("value").strip()
        if key in {"envs", "files", "literals"}:
            active_list = key
            active_list_indent = indent
            current[key] = (
                [
                    _strip_yaml_scalar(item)
                    for item in value.strip("[]").split(",")
                    if item.strip()
                ]
                if value
                else []
            )
        else:
            active_list = None
            active_list_indent = None
            current[key] = _strip_yaml_scalar(value)
    if current is not None:
        entries.append(current)
    return entries


def _read_scoped_text_file(
    source_root: Path, relative_path: str
) -> str | None:
    candidate = (source_root / relative_path).resolve()
    if (
        candidate != source_root
        and not candidate.is_relative_to(source_root)
    ) or not candidate.is_file():
        return None
    return candidate.read_text(encoding="utf-8")


def _dotenv_data(document: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for raw_line in document.splitlines():
        line = raw_line.removesuffix("\r")
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", maxsplit=1)
        if key:
            data[key] = value
    return data


def _generated_values_resources(
    path: Path,
    document: str,
    deploy_root: Path,
    charts_root: Path,
) -> list[ManifestResource]:
    scalars = dict(_document_scalars(document))
    is_kustomization_file = path.name in KUSTOMIZATION_FILENAMES
    if (
        scalars.get(("kind",)) != "Kustomization"
        and not is_kustomization_file
    ):
        return []
    if (
        scalars.get(
            ("generatorOptions", "disableNameSuffixHash"), "false"
        ).casefold()
        != "true"
    ):
        return []

    namespace = scalars.get(
        ("namespace",),
        _effective_kustomize_namespace(path, deploy_root, charts_root),
    )
    prefix, suffix = _effective_kustomize_name_parts(
        path, deploy_root, charts_root
    )
    source_root = path.parent.resolve()
    generated: list[ManifestResource] = []
    for field, kind in (
        ("configMapGenerator", "ConfigMap"),
        ("secretGenerator", "Secret"),
    ):
        for entry in _yaml_generator_entries(document, field):
            raw_name = entry.get("name")
            if not isinstance(raw_name, str) or not raw_name:
                continue
            data: dict[str, str] = {}
            for literal in entry.get("literals", []):
                if not isinstance(literal, str) or "=" not in literal:
                    continue
                key, value = literal.split("=", maxsplit=1)
                data[key] = value
            for file_entry in entry.get("files", []):
                if not isinstance(file_entry, str):
                    continue
                if "=" in file_entry:
                    key, relative_path = file_entry.split("=", maxsplit=1)
                else:
                    relative_path = file_entry
                    key = Path(relative_path).name
                file_content = _read_scoped_text_file(
                    source_root, relative_path
                )
                if file_content is None:
                    continue
                data[key] = file_content
            env_files = entry.get("envs", [])
            if isinstance(env_files, str):
                env_files = [env_files]
            elif not isinstance(env_files, list):
                env_files = []
            legacy_env = entry.get("env")
            if isinstance(legacy_env, str) and legacy_env:
                env_files = [*env_files, legacy_env]
            for env_file in env_files:
                if not isinstance(env_file, str):
                    continue
                env_content = _read_scoped_text_file(
                    source_root, env_file
                )
                if env_content is not None:
                    data.update(_dotenv_data(env_content))
            serialized_data = (
                {
                    key: base64.b64encode(value.encode("utf-8")).decode(
                        "ascii"
                    )
                    for key, value in data.items()
                }
                if kind == "Secret"
                else data
            )
            generated_document = json.dumps(
                {
                    "apiVersion": "v1",
                    "kind": kind,
                    "metadata": {
                        "name": f"{prefix}{raw_name}{suffix}",
                        "namespace": namespace,
                    },
                    "data": serialized_data,
                }
            )
            generated.append(
                ManifestResource(
                    path,
                    generated_document,
                    _document_scalars(generated_document),
                    namespace,
                )
            )
    return generated


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


def _effective_kustomize_name_parts(
    path: Path, deploy_root: Path, charts_root: Path
) -> tuple[str, str]:
    roots = (deploy_root.resolve(), charts_root.resolve())
    current = path.parent.resolve()
    prefixes: list[str] = []
    suffixes: list[str] = []
    while any(current == root or current.is_relative_to(root) for root in roots):
        for filename in KUSTOMIZATION_FILENAMES:
            kustomization = current / filename
            if not kustomization.is_file():
                continue
            scalars = dict(
                _document_scalars(kustomization.read_text(encoding="utf-8"))
            )
            prefix = scalars.get(("namePrefix",))
            suffix = scalars.get(("nameSuffix",))
            if prefix:
                prefixes.append(prefix)
            if suffix:
                suffixes.append(suffix)
            break
        if current in roots:
            break
        current = current.parent
    return "".join(reversed(prefixes)), "".join(suffixes)


def _has_polaris_identity(value: str | None) -> bool:
    return value == "polaris" or bool(value and re.fullmatch(r"polaris[-_][a-z0-9_-]+", value))


def _has_iceberg_bootstrap_identity(value: str | None) -> bool:
    if not value:
        return False
    identity_tokens = set(re.findall(r"[a-z0-9]+", value.casefold()))
    return {"iceberg", "bootstrap"} <= identity_tokens


def _path_starts_with(path: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    return path[: len(prefix)] == prefix


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


def _resource_keys(
    resource: ManifestResource,
) -> set[tuple[str, str, str]]:
    raw_key = _resource_key(resource.scalar_items, resource.namespace)
    if raw_key is None:
        return set()
    kind, namespace, name = raw_key
    return {
        raw_key,
        (
            kind,
            namespace,
            f"{resource.name_prefix}{name}{resource.name_suffix}",
        ),
    }


def _helm_values_from_references(
    document: str,
) -> list[HelmValuesReference]:
    references = []
    for entry in _yaml_mapping_sequence(document, ("spec", "valuesFrom")):
        name = entry.get("name")
        kind = entry.get("kind", "ConfigMap")
        if name and kind in HELM_VALUES_SOURCE_KINDS:
            references.append(
                HelmValuesReference(
                    kind,
                    name,
                    entry.get("valuesKey", "values.yaml"),
                    entry.get("targetPath"),
                )
            )
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
    values_key: str = "values.yaml",
) -> bool:
    kind = dict(resource.scalar_items).get(("kind",), "")
    value = _values_source_data(resource.document, kind).get(values_key)
    return _has_iceberg_bootstrap_identity(value)


def _values_source_items(
    resource: ManifestResource,
    reference: HelmValuesReference,
) -> ScalarItems:
    kind = dict(resource.scalar_items).get(("kind",), "")
    value = _values_source_data(resource.document, kind).get(
        reference.values_key
    )
    if value is None:
        return []
    prefix = _split_helm_target_path(reference.target_path)
    items = _document_scalars(value)
    if prefix and not items:
        return [(prefix, value)]
    return [
        (prefix + path, scalar)
        for path, scalar in items
    ]


def _split_helm_target_path(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    parts: list[str] = []
    current: list[str] = []
    escaped = False
    for character in value:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == ".":
            if current:
                parts.append("".join(current))
                current = []
        else:
            current.append(character)
    if escaped:
        current.append("\\")
    if current:
        parts.append("".join(current))
    return tuple(parts)


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


def _helm_value_identity_path(path: tuple[str, ...]) -> bool:
    if not path:
        return False
    field = path[-1].casefold()
    if field in HELM_VALUE_IDENTITY_FIELDS:
        return True
    if field in HELM_VALUE_IMAGE_FIELDS:
        return len(path) > 1 and path[-2].casefold() in {
            "image",
            "images",
        }
    return (
        field == "name"
        and len(path) > 1
        and path[-2].casefold() in HELM_VALUE_IDENTITY_NAME_PARENTS
    )


def _effective_helm_release_name(release: ManifestResource) -> str | None:
    scalars = dict(release.scalar_items)
    explicit_name = _reference_field(
        release.scalar_items, ("spec",), "releaseName"
    )
    if explicit_name:
        return explicit_name

    resource_name = scalars.get(
        ("metadata", "name")
    ) or _flow_mapping_field(
        release.scalar_items, ("metadata",), "name"
    )
    if not resource_name:
        return None
    transformed_name = (
        f"{release.name_prefix}{resource_name}{release.name_suffix}"
    )
    target_namespace = _reference_field(
        release.scalar_items, ("spec",), "targetNamespace"
    )
    return (
        f"{target_namespace}-{transformed_name}"
        if target_namespace
        else transformed_name
    )


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


def _git_origin_url(root: Path = ROOT) -> str | None:
    git_path = root / ".git"
    if git_path.is_file():
        marker = git_path.read_text(encoding="utf-8").strip()
        if not marker.startswith("gitdir:"):
            return None
        git_directory = (
            root / marker.removeprefix("gitdir:").strip()
        ).resolve()
    elif git_path.is_dir():
        git_directory = git_path.resolve()
    else:
        return None

    common_directory = git_directory
    common_marker = git_directory / "commondir"
    if common_marker.is_file():
        common_directory = (
            git_directory
            / common_marker.read_text(encoding="utf-8").strip()
        ).resolve()
    config_path = common_directory / "config"
    if not config_path.is_file():
        return None

    in_origin = False
    for raw_line in config_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            in_origin = line.casefold() == '[remote "origin"]'
            continue
        if in_origin:
            match = re.match(r"url\s*=\s*(?P<url>.+)", line)
            if match is not None:
                return match.group("url").strip()
    return None


def _repository_url_identity(url: str) -> str:
    normalized = url.strip().removesuffix("/").removesuffix(".git")
    scp_match = re.match(
        r"(?:[^@]+@)?(?P<host>[^:]+):(?P<path>.+)", normalized
    )
    if scp_match is not None and "://" not in normalized:
        normalized = (
            f"{scp_match.group('host')}/{scp_match.group('path')}"
        )
    else:
        normalized = re.sub(
            r"^[a-z][a-z0-9+.-]*://", "", normalized, flags=re.I
        )
        normalized = normalized.rsplit("@", maxsplit=1)[-1]
    return normalized.casefold()


def _git_repository_source_is_local(
    resources: Mapping[
        tuple[str, str, str],
        Sequence[ManifestResource],
    ],
    namespace: str,
    name: str,
    release_path: Path,
) -> bool:
    origin_url = _git_origin_url()
    if not origin_url:
        return False
    origin_identity = _repository_url_identity(origin_url)
    return any(
        _repository_url_identity(source_url) == origin_identity
        for source in _scoped_resources(
            resources,
            "GitRepository",
            namespace,
            name,
            release_path,
        )
        for source_url in (
            _reference_field(source.scalar_items, ("spec",), "url"),
        )
        if source_url
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


def _local_chart_value_items(
    chart_reference: str | None,
    charts_root: Path,
    release_document: str,
    allow_local_chart_values: bool,
) -> ScalarItems:
    if (
        not allow_local_chart_values
        or not chart_reference
        or "://" in chart_reference
        or "{{" in chart_reference
        or "}}" in chart_reference
    ):
        return []

    reference = Path(chart_reference)
    source_root = charts_root.parent.resolve()
    candidates: list[Path] = []
    if reference.is_absolute():
        candidates.append(reference)
    else:
        parts = tuple(part for part in reference.parts if part not in {"", "."})
        if parts and parts[0] == charts_root.name:
            candidates.append(charts_root.joinpath(*parts[1:]))
        candidates.extend(
            (
                source_root / reference,
                charts_root / reference,
            )
        )

    for candidate in candidates:
        candidate = candidate.resolve()
        if (
            candidate != source_root
            and not candidate.is_relative_to(source_root)
        ):
            continue
        if not candidate.is_dir():
            continue

        values_files_path = (
            ("spec", "valuesFiles")
            if dict(_document_scalars(release_document)).get(("kind",))
            == "HelmChart"
            else ("spec", "chart", "spec", "valuesFiles")
        )
        declared_values_files = _yaml_sequence_values(
            release_document, values_files_path
        )
        if declared_values_files:
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
            values_paths = [candidate / "values.yaml"]

        items: ScalarItems = []
        for values_path in values_paths:
            if not values_path.is_file():
                continue
            items.extend(
                _document_scalars(values_path.read_text(encoding="utf-8"))
            )
        return items
    return []


def _merged_helm_values_have_bootstrap_identity(
    release: ManifestResource,
    resources: Mapping[
        tuple[str, str, str],
        Sequence[ManifestResource],
    ],
    charts_root: Path,
    release_namespace: str,
    chart_reference: str | None,
    allow_local_chart_values: bool,
    chart_values_document: str | None = None,
) -> bool:
    merged: dict[tuple[str, ...], str] = dict(
        _local_chart_value_items(
            chart_reference,
            charts_root,
            chart_values_document or release.document,
            allow_local_chart_values,
        )
    )
    for reference in _helm_values_from_references(release.document):
        for resource in _scoped_resources(
            resources,
            reference.kind,
            release_namespace,
            reference.name,
            release.path,
        ):
            merged.update(_values_source_items(resource, reference))

    merged.update(
        {
            path[2:]: value
            for path, value in release.scalar_items
            if _path_starts_with(path, ("spec", "values")) and len(path) > 2
        }
    )
    return any(
        _helm_value_identity_path(path)
        and _has_iceberg_bootstrap_identity(value)
        for path, value in merged.items()
    )


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
    if _has_iceberg_bootstrap_identity(
        _effective_helm_release_name(release)
    ):
        return True
    if any(
        (
            path in HELM_RELEASE_IDENTITY_PATHS
            or path
            == (
                "spec",
                "commonMetadata",
                "labels",
                "app.kubernetes.io/name",
            )
            or _path_starts_with(path, ("spec", "postRenderers"))
        )
        and _has_iceberg_bootstrap_identity(value)
        for path, value in scalar_items
    ):
        return True
    if _has_iceberg_bootstrap_identity(
        _flow_mapping_field(
            scalar_items,
            ("spec", "commonMetadata", "labels"),
            "app.kubernetes.io/name",
        )
        or _flow_mapping_field(
            scalar_items,
            ("spec", "commonMetadata"),
            "app.kubernetes.io/name",
        )
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
        if chart_ref_kind in HELM_CHART_REF_KINDS:
            chart_resources = _scoped_resources(
                resources,
                chart_ref_kind,
                chart_ref_namespace,
                chart_ref_name,
                release.path,
            )
            if any(
                _chart_resource_has_bootstrap_identity(resource, resources)
                for resource in chart_resources
            ):
                return True
            for chart_resource in chart_resources:
                if (
                    dict(chart_resource.scalar_items).get(("kind",))
                    != "HelmChart"
                ):
                    continue
                chart_reference = _reference_field(
                    chart_resource.scalar_items, ("spec",), "chart"
                )
                chart_source_kind = _reference_field(
                    chart_resource.scalar_items,
                    ("spec", "sourceRef"),
                    "kind",
                )
                chart_source_name = _reference_field(
                    chart_resource.scalar_items,
                    ("spec", "sourceRef"),
                    "name",
                )
                chart_source_namespace = (
                    _reference_field(
                        chart_resource.scalar_items,
                        ("spec", "sourceRef"),
                        "namespace",
                    )
                    or chart_resource.namespace
                )
                allow_local_chart_values = chart_source_kind is None or (
                    chart_source_kind == "GitRepository"
                    and chart_source_name is not None
                    and _git_repository_source_is_local(
                        resources,
                        chart_source_namespace,
                        chart_source_name,
                        chart_resource.path,
                    )
                )
                if _merged_helm_values_have_bootstrap_identity(
                    release,
                    resources,
                    charts_root,
                    release_namespace,
                    chart_reference,
                    allow_local_chart_values,
                    chart_resource.document,
                ):
                    return True

    chart_source_ref_path = ("spec", "chart", "spec", "sourceRef")
    chart_source_name = _reference_field(
        scalar_items, chart_source_ref_path, "name"
    )
    chart_source_kind: str | None = None
    allow_local_chart_values = chart_source_name is None
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
        allow_local_chart_values = (
            chart_source_kind == "GitRepository"
            and _git_repository_source_is_local(
                resources,
                chart_source_namespace,
                chart_source_name,
                release.path,
            )
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
    return _merged_helm_values_have_bootstrap_identity(
        release,
        resources,
        charts_root,
        release_namespace,
        chart_reference,
        allow_local_chart_values,
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
        if path.is_file() and (
            path.suffix in DEPLOYMENT_SUFFIXES
            or path.name in KUSTOMIZATION_FILENAMES
        ):
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


def _is_flux_kustomization(resource: ManifestResource) -> bool:
    scalars = dict(resource.scalar_items)
    return (
        scalars.get(("kind",)) == "Kustomization"
        and scalars.get(("apiVersion",), "").startswith(
            "kustomize.toolkit.fluxcd.io/"
        )
    )


def _flux_kustomization_source_is_local(
    resource: ManifestResource,
    resources: Mapping[
        tuple[str, str, str],
        Sequence[ManifestResource],
    ],
) -> bool:
    source_ref_path = ("spec", "sourceRef")
    source_name = _reference_field(
        resource.scalar_items, source_ref_path, "name"
    )
    if not source_name:
        return True
    source_kind = (
        _reference_field(
            resource.scalar_items, source_ref_path, "kind"
        )
        or "GitRepository"
    )
    if source_kind != "GitRepository":
        return False
    source_namespace = (
        _reference_field(
            resource.scalar_items, source_ref_path, "namespace"
        )
        or resource.namespace
    )
    return _git_repository_source_is_local(
        resources,
        source_namespace,
        source_name,
        resource.path,
    )


def _flux_kustomization_target_root(
    resource: ManifestResource, source_root: Path
) -> Path | None:
    target_path = _reference_field(
        resource.scalar_items, ("spec",), "path"
    ) or "."
    reference = Path(target_path)
    if reference.is_absolute():
        return None
    target_root = (source_root / reference).resolve()
    if (
        target_root != source_root
        and not target_root.is_relative_to(source_root)
    ):
        return None
    return target_root


def _applicable_flux_kustomizations(
    resource: ManifestResource,
    kustomizations: Sequence[ManifestResource],
    source_root: Path,
) -> list[ManifestResource]:
    resource_path = resource.path.resolve()
    applicable = []
    for kustomization in kustomizations:
        target_root = _flux_kustomization_target_root(
            kustomization, source_root
        )
        if target_root is not None and (
            resource_path == target_root
            or resource_path.is_relative_to(target_root)
        ):
            applicable.append(kustomization)
    return applicable


def _resource_identity_names(resource: ManifestResource) -> set[str]:
    scalars = dict(resource.scalar_items)
    name = scalars.get(
        ("metadata", "name")
    ) or _flow_mapping_field(
        resource.scalar_items, ("metadata",), "name"
    )
    if not name:
        return set()
    return {
        name,
        f"{resource.name_prefix}{name}{resource.name_suffix}",
    }


def _kustomize_target_name_matches(
    pattern: str, resource: ManifestResource
) -> bool:
    for name in _resource_identity_names(resource):
        try:
            if re.fullmatch(pattern, name):
                return True
        except re.error:
            return pattern == name
    return False


def _metadata_mapping_values(
    resource: ManifestResource, field: str
) -> dict[str, str]:
    prefix = ("metadata", field)
    values = {
        path[-1]: value
        for path, value in resource.scalar_items
        if len(path) == 3 and path[:2] == prefix
    }
    scalars = dict(resource.scalar_items)
    inline = scalars.get(prefix)
    if inline:
        values.update(_flow_mapping_fields(inline))
    return values


def _selector_matches(
    selector: str, values: Mapping[str, str]
) -> bool:
    requirements = re.split(r",(?![^()]*\))", selector)
    for raw_requirement in requirements:
        requirement = raw_requirement.strip()
        if not requirement:
            continue
        set_match = re.fullmatch(
            r"(?P<key>[^\s!=,]+)\s+"
            r"(?P<operator>in|notin)\s*"
            r"\((?P<values>[^)]*)\)",
            requirement,
            flags=re.IGNORECASE,
        )
        if set_match is not None:
            key = set_match.group("key")
            candidates = {
                item.strip()
                for item in set_match.group("values").split(",")
                if item.strip()
            }
            present = key in values and values[key] in candidates
            if (
                set_match.group("operator").casefold() == "in"
                and not present
            ) or (
                set_match.group("operator").casefold() == "notin"
                and present
            ):
                return False
            continue
        comparison_match = re.fullmatch(
            r"(?P<key>[^!=\s]+)\s*"
            r"(?P<operator>==|=|!=)\s*"
            r"(?P<value>.*)",
            requirement,
        )
        if comparison_match is not None:
            key = comparison_match.group("key")
            expected = comparison_match.group("value").strip()
            actual = values.get(key)
            if comparison_match.group("operator") == "!=":
                if actual == expected:
                    return False
            elif actual != expected:
                return False
            continue
        if requirement.startswith("!"):
            if requirement[1:] in values:
                return False
        elif requirement not in values:
            return False
    return True


def _flux_patch_parts(
    patch_document: str,
) -> tuple[dict[str, str], str, bool]:
    try:
        parsed = json.loads(patch_document)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        target = parsed.get("target")
        patch = parsed.get("patch")
        return (
            {
                str(key): str(value)
                for key, value in target.items()
            }
            if isinstance(target, dict)
            else {},
            patch if isinstance(patch, str) else "",
            isinstance(target, dict),
        )

    normalized = re.sub(r"^-[ ]*", "  ", patch_document, count=1)
    target_match = re.search(
        r"(?ms)^[ ]{2}target:[ ]*(?P<inline>\{[^\n]*\})?[ ]*\n?"
        r"(?P<body>(?:[ ]{4,}.*(?:\n|$))*)",
        normalized,
    )
    target_fields = (
        _flow_mapping_fields(target_match.group("inline") or "")
        if target_match is not None
        else {}
    )
    target_body = (
        target_match.group("body") if target_match is not None else ""
    )
    for field in (
        "annotationSelector",
        "group",
        "kind",
        "labelSelector",
        "name",
        "namespace",
        "version",
    ):
        match = re.search(
            rf"(?m)^[ ]+{field}:[ ]*(?P<value>.+?)\s*$",
            target_body,
        )
        if match is not None:
            target_fields[field] = _strip_yaml_scalar(
                match.group("value")
            )

    patch_match = re.search(
        r"(?ms)^[ ]{2}patch:[ ]*(?P<patch>.*)$", normalized
    )
    if patch_match is None:
        return target_fields, "", target_match is not None
    patch = patch_match.group("patch")
    patch_lines = patch.splitlines()
    if patch_lines and re.fullmatch(
        r"[|>][+-]?[1-9]?", patch_lines[0].strip()
    ):
        patch_lines = patch_lines[1:]
        non_empty_indents = [
            len(line) - len(line.lstrip(" "))
            for line in patch_lines
            if line.strip()
        ]
        if non_empty_indents:
            indent = min(non_empty_indents)
            patch = "\n".join(
                line[indent:] if line.strip() else ""
                for line in patch_lines
            )
        else:
            patch = ""
    else:
        patch = _strip_yaml_scalar(patch)
    return target_fields, patch, target_match is not None


def _flux_patch_target_matches(
    target_fields: Mapping[str, str],
    resource: ManifestResource,
) -> bool:
    scalars = dict(resource.scalar_items)
    resource_kind = scalars.get(("kind",), "")
    if (
        target_fields.get("kind")
        and target_fields["kind"] != resource_kind
    ):
        return False
    if target_fields.get("name") and not _kustomize_target_name_matches(
        target_fields["name"], resource
    ):
        return False

    api_version = scalars.get(("apiVersion",), "")
    if "/" in api_version:
        resource_group, resource_version = api_version.split("/", 1)
    else:
        resource_group, resource_version = "", api_version
    if (
        target_fields.get("group")
        and target_fields["group"] != resource_group
    ) or (
        target_fields.get("version")
        and target_fields["version"] != resource_version
    ):
        return False

    namespace = scalars.get(
        ("metadata", "namespace"), resource.namespace
    )
    if (
        target_fields.get("namespace")
        and target_fields["namespace"] != namespace
    ):
        return False
    if target_fields.get("labelSelector") and not _selector_matches(
        target_fields["labelSelector"],
        _metadata_mapping_values(resource, "labels"),
    ):
        return False
    return not target_fields.get(
        "annotationSelector"
    ) or _selector_matches(
        target_fields["annotationSelector"],
        _metadata_mapping_values(resource, "annotations"),
    )


def _yaml_json_patch_operations(
    patch: str,
) -> list[dict[str, object]]:
    try:
        parsed = json.loads(patch)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, list):
        return [
            entry
            for entry in parsed
            if isinstance(entry, dict)
        ]

    wrapped = "items:\n" + "\n".join(
        f"  {line}" for line in patch.splitlines()
    )
    operations: list[dict[str, object]] = []
    for block in _yaml_sequence_blocks(wrapped, ("items",)):
        normalized = re.sub(r"^-[ ]*", "", block, count=1)
        if normalized.startswith("{") and normalized.endswith("}"):
            fields: dict[str, object] = _flow_mapping_fields(
                normalized
            )
        else:
            fields = {
                path[-1]: value
                for path, value in _document_scalars(normalized)
                if len(path) == 1
            }
        if "op" in fields and "path" in fields:
            operations.append(fields)
    return operations


def _json_patch_identity_key(
    path: str,
) -> tuple[str, ...] | None:
    parts = tuple(
        part.replace("~1", "/").replace("~0", "~")
        for part in path.strip("/").split("/")
        if part
    )
    if parts in {
        ("metadata", "name"),
        ("metadata", "labels", "app.kubernetes.io/name"),
    }:
        return parts
    for root in BOOTSTRAP_CONTAINER_ROOT_PATHS:
        if (
            len(parts) == len(root) + 2
            and parts[: len(root)] == root
            and parts[len(root)].isdigit()
            and parts[-1].casefold() in {"image", "name"}
        ):
            return parts
    return None


def _applicable_flux_patch_payloads(
    kustomization: ManifestResource,
    resource: ManifestResource,
) -> list[str]:
    resource_kind = dict(resource.scalar_items).get(("kind",))
    payloads: list[str] = []
    for patch_document in _yaml_sequence_blocks(
        kustomization.document, ("spec", "patches")
    ):
        target_fields, patch, has_explicit_target = _flux_patch_parts(
            patch_document
        )
        if not patch:
            continue
        patch_scalars = _document_scalars(patch)
        patch_scalar_map = dict(patch_scalars)
        if not has_explicit_target:
            patch_kind = patch_scalar_map.get(("kind",))
            if patch_kind and patch_kind != resource_kind:
                continue
            patch_name = patch_scalar_map.get(("metadata", "name"))
            if patch_name and not _kustomize_target_name_matches(
                patch_name, resource
            ):
                continue
        elif not _flux_patch_target_matches(target_fields, resource):
            continue
        payloads.append(patch)
    return payloads


def _resource_identity_state(
    resource: ManifestResource,
) -> IdentityState:
    state: IdentityState = {}
    scalars = dict(resource.scalar_items)
    name = scalars.get(
        ("metadata", "name")
    ) or _flow_mapping_field(
        resource.scalar_items, ("metadata",), "name"
    )
    if name:
        state[("metadata", "name")] = (
            f"{resource.name_prefix}{name}{resource.name_suffix}"
        )

    label = scalars.get(
        ("metadata", "labels", "app.kubernetes.io/name")
    ) or _flow_mapping_field(
        resource.scalar_items,
        ("metadata", "labels"),
        "app.kubernetes.io/name",
    )
    if label:
        state[
            ("metadata", "labels", "app.kubernetes.io/name")
        ] = label

    for root in BOOTSTRAP_CONTAINER_ROOT_PATHS:
        entries = _yaml_mapping_sequence(resource.document, root)
        if entries:
            for index, entry in enumerate(entries):
                for field in ("name", "image"):
                    value = entry.get(field)
                    if value:
                        state[root + (str(index), field)] = value
            continue

        field_indexes = {"name": 0, "image": 0}
        for path, value in resource.scalar_items:
            if path[:-1] != root:
                continue
            field = path[-1].lstrip("{").casefold()
            if field not in field_indexes:
                continue
            index = field_indexes[field]
            state[root + (str(index), field)] = value
            field_indexes[field] += 1
    return state


def _identity_container_key(
    root: tuple[str, ...],
    index: str,
    field: str,
) -> tuple[str, ...]:
    return root + (index, field)


def _next_identity_container_index(
    state: Mapping[tuple[str, ...], str],
    root: tuple[str, ...],
) -> str:
    indexes = [
        int(key[len(root)])
        for key in state
        if (
            len(key) == len(root) + 2
            and key[: len(root)] == root
            and key[len(root)].isdigit()
        )
    ]
    return str(max(indexes, default=-1) + 1)


def _strategic_patch_container_entries(
    state: IdentityState,
    patch: str,
) -> None:
    for root in BOOTSTRAP_CONTAINER_ROOT_PATHS:
        for entry in _yaml_mapping_sequence(patch, root):
            name = entry.get("name")
            directive = entry.get("$patch", "").casefold()
            if not name and directive in {"delete", "replace"}:
                for key in tuple(state):
                    if (
                        len(key) == len(root) + 2
                        and key[: len(root)] == root
                    ):
                        state.pop(key)
                continue
            if not name:
                continue
            indexes = {
                key[len(root)]
                for key, value in state.items()
                if (
                    len(key) == len(root) + 2
                    and key[: len(root)] == root
                    and key[-1] == "name"
                    and value == name
                )
            }
            if not indexes:
                indexes = {_next_identity_container_index(state, root)}
            if directive == "delete":
                for index in indexes:
                    for field in ("name", "image"):
                        state.pop(
                            _identity_container_key(
                                root, index, field
                            ),
                            None,
                        )
                continue
            for index in indexes:
                state[
                    _identity_container_key(
                        root, index, "name"
                    )
                ] = name
                image = entry.get("image")
                if image:
                    state[
                        _identity_container_key(
                            root, index, "image"
                        )
                    ] = image


def _apply_json_identity_patch(
    state: IdentityState,
    operation: Mapping[str, object],
) -> None:
    operation_name = str(operation.get("op", "")).casefold()
    path = operation.get("path")
    if not isinstance(path, str):
        return
    key = _json_patch_identity_key(path)
    if key is not None:
        if operation_name == "remove":
            state.pop(key, None)
        elif operation_name in {"add", "replace"}:
            value = operation.get("value")
            if isinstance(value, str):
                state[key] = value
        return

    parts = tuple(
        part.replace("~1", "/").replace("~0", "~")
        for part in path.strip("/").split("/")
        if part
    )
    label_key = (
        "metadata",
        "labels",
        "app.kubernetes.io/name",
    )
    if parts == ("metadata", "labels"):
        if operation_name in {"remove", "replace"}:
            state.pop(label_key, None)
        value = operation.get("value")
        if (
            operation_name in {"add", "replace"}
            and isinstance(value, dict)
            and isinstance(
                value.get("app.kubernetes.io/name"), str
            )
        ):
            state[label_key] = value["app.kubernetes.io/name"]
        return

    for root in BOOTSTRAP_CONTAINER_ROOT_PATHS:
        if (
            len(parts) == len(root) + 1
            and parts[: len(root)] == root
            and (parts[-1].isdigit() or parts[-1] == "-")
        ):
            index = (
                _next_identity_container_index(state, root)
                if parts[-1] == "-"
                else parts[-1]
            )
            if operation_name in {"remove", "replace"}:
                for field in ("name", "image"):
                    state.pop(root + (index, field), None)
            value = operation.get("value")
            if operation_name in {"add", "replace"} and isinstance(
                value, dict
            ):
                for field in ("name", "image"):
                    field_value = value.get(field)
                    if isinstance(field_value, str):
                        state[root + (index, field)] = field_value
            return


def _apply_flux_identity_patches(
    state: IdentityState,
    kustomization: ManifestResource,
    resource: ManifestResource,
) -> tuple[IdentityState, bool]:
    effective = dict(state)
    for patch in _applicable_flux_patch_payloads(
        kustomization, resource
    ):
        operations = _yaml_json_patch_operations(patch)
        if operations:
            for operation in operations:
                _apply_json_identity_patch(effective, operation)
            continue

        patch_scalars = _document_scalars(patch)
        scalar_map = dict(patch_scalars)
        patch_directive = scalar_map.get(("$patch",), "").casefold()
        if patch_directive == "delete":
            return {}, True
        if patch_directive == "replace":
            effective = {
                key: value
                for key, value in effective.items()
                if key == ("metadata", "name")
            }

        label_key = (
            "metadata",
            "labels",
            "app.kubernetes.io/name",
        )
        label = scalar_map.get(label_key)
        if label is not None:
            if label.casefold() in {"null", "~"}:
                effective.pop(label_key, None)
            else:
                effective[label_key] = label
        _strategic_patch_container_entries(effective, patch)
    return effective, False


def _image_reference_name(reference: str) -> str:
    name = reference.split("@", maxsplit=1)[0]
    final_slash = name.rfind("/")
    final_colon = name.rfind(":")
    if final_colon > final_slash:
        name = name[:final_colon]
    return name


def _image_reference_suffix(reference: str) -> str:
    if "@" in reference:
        return f"@{reference.split('@', maxsplit=1)[1]}"
    final_slash = reference.rfind("/")
    final_colon = reference.rfind(":")
    return reference[final_colon:] if final_colon > final_slash else ""


def _image_reference_matches(rule: str, current: str) -> bool:
    if "@" in rule:
        return current == rule
    final_slash = rule.rfind("/")
    if rule.rfind(":") > final_slash:
        return current == rule
    return _image_reference_name(current) == rule


def _rewrite_image_reference(
    current_image: str, entry: Mapping[str, str]
) -> str:
    current_name = _image_reference_name(current_image)
    effective_image = entry.get("newName", current_name)
    if entry.get("digest"):
        return f"{effective_image}@{entry['digest']}"
    if entry.get("newTag"):
        return f"{effective_image}:{entry['newTag']}"
    return f"{effective_image}{_image_reference_suffix(current_image)}"


def _apply_flux_image_transforms(
    state: IdentityState,
    kustomization: ManifestResource,
) -> IdentityState:
    effective = dict(state)
    for entry in _yaml_mapping_sequence(
        kustomization.document, ("spec", "images")
    ):
        original_name = entry.get("name")
        if not original_name:
            continue
        for key, current_image in tuple(effective.items()):
            if not any(
                len(key) == len(root) + 2
                and key[: len(root)] == root
                and key[-1] == "image"
                for root in BOOTSTRAP_CONTAINER_ROOT_PATHS
            ):
                continue
            if not _image_reference_matches(
                original_name, current_image
            ):
                continue
            effective[key] = _rewrite_image_reference(
                current_image, entry
            )
    return effective


def _apply_flux_common_metadata(
    state: IdentityState, kustomization: ManifestResource
) -> IdentityState:
    effective = dict(state)
    common_label = dict(kustomization.scalar_items).get(
        (
            "spec",
            "commonMetadata",
            "labels",
            "app.kubernetes.io/name",
        )
    )
    if common_label:
        effective[
            ("metadata", "labels", "app.kubernetes.io/name")
        ] = common_label
    return effective


def _effective_flux_workload_identity_values(
    resource: ManifestResource,
    kustomizations: Sequence[ManifestResource],
    source_root: Path,
) -> set[str]:
    base_state = _resource_identity_state(resource)
    applicable = _applicable_flux_kustomizations(
        resource, kustomizations, source_root
    )
    if not applicable:
        return set(base_state.values())

    effective_values: set[str] = set()
    for kustomization in applicable:
        state, deleted = _apply_flux_identity_patches(
            base_state, kustomization, resource
        )
        if deleted:
            continue
        state = _apply_flux_image_transforms(state, kustomization)
        state = _apply_flux_common_metadata(state, kustomization)
        effective_values.update(state.values())
    return effective_values


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
        effective_namespace = _effective_kustomize_namespace(
            path, deploy_root, charts_root
        )
        name_prefix, name_suffix = _effective_kustomize_name_parts(
            path, deploy_root, charts_root
        )
        documents = re.split(
            r"(?m)^---[ \t]*(?:#.*)?$", path.read_text(encoding="utf-8")
        )
        for document in documents:
            for generated_resource in _generated_values_resources(
                path, document, deploy_root, charts_root
            ):
                manifest_resources.append(generated_resource)
                for resource_key in _resource_keys(generated_resource):
                    resources.setdefault(resource_key, []).append(
                        generated_resource
                    )
            for resource_document in _manifest_item_documents(document):
                scalar_items = _document_scalars(resource_document)
                resource = ManifestResource(
                    path,
                    resource_document,
                    scalar_items,
                    effective_namespace,
                    name_prefix,
                    name_suffix,
                )
                manifest_resources.append(resource)
                for resource_key in _resource_keys(resource):
                    resources.setdefault(resource_key, []).append(resource)

    flux_kustomizations = [
        resource
        for resource in manifest_resources
        if _is_flux_kustomization(resource)
        and _flux_kustomization_source_is_local(resource, resources)
    ]
    source_root = charts_root.parent.resolve()
    manifests: set[Path] = set()
    for resource in manifest_resources:
        scalars = dict(resource.scalar_items)
        kind = scalars.get(("kind",))
        identity_values = (
            _effective_flux_workload_identity_values(
                resource,
                flux_kustomizations,
                source_root,
            )
            if kind in BOOTSTRAP_KINDS
            else set()
        )
        is_bootstrap = kind in BOOTSTRAP_KINDS and any(
            _has_iceberg_bootstrap_identity(value)
            for value in identity_values
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
            [
                HelmValuesReference(
                    "Secret", "catalog-values", "values.yaml", None
                )
            ],
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
            (deploy_root / "flow-job.yaml").write_text(
                "apiVersion: batch/v1\n"
                "kind: Job\n"
                "metadata:\n"
                "  annotations: {bootstrap-name: &flow-bootstrap "
                "iceberg-table-bootstrap}\n"
                "  name: *flow-bootstrap\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - name: worker\n"
                "          image: registry.example/worker:v1\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [
                    deploy_root / "flow-job.yaml",
                    deploy_root / "job.yaml",
                ],
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

    def test_detects_bootstrap_identity_in_helm_post_renderer_patch(self) -> None:
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
                "  postRenderers:\n"
                "    - kustomize:\n"
                "        patches:\n"
                "          - target:\n"
                "              kind: Job\n"
                "            patch: |\n"
                "              - op: replace\n"
                "                path: /metadata/name\n"
                "                value: iceberg-table-bootstrap\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [deploy_root / "release.yaml"],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_honors_values_key_for_values_from_reference(self) -> None:
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
                "  safe.yaml: |\n"
                "    fullnameOverride: catalog-task\n"
                "  bootstrap.yaml: |\n"
                "    fullnameOverride: iceberg-table-bootstrap\n",
                encoding="utf-8",
            )
            (deploy_root / "safe-release.yaml").write_text(
                "apiVersion: helm.toolkit.fluxcd.io/v2\n"
                "kind: HelmRelease\n"
                "metadata:\n"
                "  name: safe-catalog\n"
                "spec:\n"
                "  valuesFrom:\n"
                "    - kind: ConfigMap\n"
                "      name: catalog-values\n"
                "      valuesKey: safe.yaml\n",
                encoding="utf-8",
            )
            (deploy_root / "bootstrap-release.yaml").write_text(
                "apiVersion: helm.toolkit.fluxcd.io/v2\n"
                "kind: HelmRelease\n"
                "metadata:\n"
                "  name: bootstrap-catalog\n"
                "spec:\n"
                "  valuesFrom:\n"
                "    - kind: ConfigMap\n"
                "      name: catalog-values\n"
                "      valuesKey: bootstrap.yaml\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [deploy_root / "bootstrap-release.yaml"],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_inline_values_override_bootstrap_chart_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            chart_root = charts_root / "catalog-task"
            deploy_root.mkdir()
            chart_root.mkdir(parents=True)
            (chart_root / "values.yaml").write_text(
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
                "  values:\n"
                "    fullnameOverride: catalog-task\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_applies_kustomize_name_prefix_and_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            deploy_root.mkdir()
            charts_root.mkdir()
            (deploy_root / "kustomization.yaml").write_text(
                "apiVersion: kustomize.config.k8s.io/v1beta1\n"
                "kind: Kustomization\n"
                "namePrefix: iceberg-\n"
                "nameSuffix: -bootstrap\n"
                "resources:\n"
                "  - job.yaml\n",
                encoding="utf-8",
            )
            (deploy_root / "job.yaml").write_text(
                "apiVersion: batch/v1\n"
                "kind: Job\n"
                "metadata:\n"
                "  name: table\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - name: worker\n"
                "          image: registry.example/worker@sha256:"
                + "c" * 64
                + "\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [deploy_root / "job.yaml"],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_detects_helmrelease_common_metadata_label(self) -> None:
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
                "  commonMetadata:\n"
                "    labels:\n"
                "      app.kubernetes.io/name: iceberg-table-bootstrap\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [deploy_root / "release.yaml"],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_indexes_kustomize_generated_values_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            deploy_root.mkdir()
            charts_root.mkdir()
            (deploy_root / "kustomization.yaml").write_text(
                "apiVersion: kustomize.config.k8s.io/v1beta1\n"
                "kind: Kustomization\n"
                "resources:\n"
                "  - release.yaml\n"
                "configMapGenerator:\n"
                "- name: catalog-values\n"
                "  literals:\n"
                "  - values.yaml=fullnameOverride: "
                "iceberg-table-bootstrap\n"
                "generatorOptions:\n"
                "  disableNameSuffixHash: true\n",
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

    def test_detects_flow_style_metadata_label_identity(self) -> None:
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
                "  labels: {app.kubernetes.io/name: "
                "iceberg-table-bootstrap}\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - name: worker\n"
                "          image: registry.example/worker@sha256:"
                + "d" * 64
                + "\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [deploy_root / "job.yaml"],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_detects_flow_style_inline_helm_values(self) -> None:
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
                "  values: {fullnameOverride: "
                "iceberg-table-bootstrap}\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [deploy_root / "release.yaml"],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_reads_flow_style_values_source_data(self) -> None:
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
                'data: {values.yaml: "fullnameOverride: '
                'iceberg-table-bootstrap"}\n',
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

    def test_indexes_flow_style_resource_metadata_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            deploy_root.mkdir()
            charts_root.mkdir()
            (deploy_root / "values.yaml").write_text(
                "apiVersion: v1\n"
                "kind: ConfigMap\n"
                "metadata: {name: catalog-values}\n"
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

    def test_ignores_unselected_values_yml_chart_sidecar(self) -> None:
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
            (chart_root / "values.yml").write_text(
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
                "      chart: ./charts/catalog-task\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_does_not_read_local_defaults_for_remote_chart_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            chart_root = charts_root / "catalog-task"
            deploy_root.mkdir()
            chart_root.mkdir(parents=True)
            (chart_root / "values.yaml").write_text(
                "fullnameOverride: iceberg-table-bootstrap\n",
                encoding="utf-8",
            )
            (deploy_root / "source.yaml").write_text(
                "apiVersion: source.toolkit.fluxcd.io/v1\n"
                "kind: HelmRepository\n"
                "metadata:\n"
                "  name: external-charts\n"
                "spec:\n"
                "  url: https://charts.example.invalid\n",
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
                "      chart: catalog-task\n"
                "      sourceRef:\n"
                "        kind: HelmRepository\n"
                "        name: external-charts\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_uses_transformed_helmrelease_name_as_default_release_name(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            deploy_root.mkdir()
            charts_root.mkdir()
            (deploy_root / "kustomization.yaml").write_text(
                "apiVersion: kustomize.config.k8s.io/v1beta1\n"
                "kind: Kustomization\n"
                "namePrefix: iceberg-\n"
                "nameSuffix: -bootstrap\n"
                "resources:\n"
                "  - release.yaml\n",
                encoding="utf-8",
            )
            (deploy_root / "release.yaml").write_text(
                "apiVersion: helm.toolkit.fluxcd.io/v2\n"
                "kind: HelmRelease\n"
                "metadata:\n"
                "  name: table\n"
                "spec:\n"
                "  chart:\n"
                "    spec:\n"
                "      chart: catalog-task\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [deploy_root / "release.yaml"],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_indexes_kustomize_transformed_source_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            deploy_root.mkdir()
            charts_root.mkdir()
            (deploy_root / "kustomization.yaml").write_text(
                "apiVersion: kustomize.config.k8s.io/v1beta1\n"
                "kind: Kustomization\n"
                "namePrefix: tenant-\n"
                "nameSuffix: -v1\n"
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
                "      name: tenant-catalog-values-v1\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [deploy_root / "release.yaml"],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_uses_explicit_release_name_instead_of_object_metadata(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            deploy_root.mkdir()
            charts_root.mkdir()
            (deploy_root / "named-release.yaml").write_text(
                "apiVersion: helm.toolkit.fluxcd.io/v2\n"
                "kind: HelmRelease\n"
                "metadata:\n"
                "  name: iceberg-table-bootstrap\n"
                "spec:\n"
                "  releaseName: catalog-task\n"
                "  chart:\n"
                "    spec:\n"
                "      chart: catalog-task\n",
                encoding="utf-8",
            )
            (deploy_root / "labelled-release.yaml").write_text(
                "apiVersion: helm.toolkit.fluxcd.io/v2\n"
                "kind: HelmRelease\n"
                "metadata:\n"
                "  name: catalog-task\n"
                "  labels:\n"
                "    app.kubernetes.io/name: iceberg-table-bootstrap\n"
                "spec:\n"
                "  releaseName: catalog-task\n"
                "  chart:\n"
                "    spec:\n"
                "      chart: catalog-task\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_reads_values_from_after_comment_only_mapping_key(self) -> None:
        self.assertEqual(
            "'iceberg # bootstrap'",
            _strip_yaml_comment("'iceberg # bootstrap'"),
        )
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
                "    fullnameOverride: iceberg-table-bootstrap\n",
                encoding="utf-8",
            )
            (deploy_root / "release.yaml").write_text(
                "apiVersion: helm.toolkit.fluxcd.io/v2\n"
                "kind: HelmRelease\n"
                "metadata:\n"
                "  name: catalog-task\n"
                "spec: # release configuration\n"
                "  valuesFrom: # merged values\n"
                "    - kind: ConfigMap\n"
                "      name: catalog-values\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [deploy_root / "release.yaml"],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_ignores_non_identity_helm_values(self) -> None:
        for identity_path in (
            ("image", "repository"),
            ("image", "tag"),
            ("job", "name"),
        ):
            with self.subTest(identity_path=identity_path):
                self.assertTrue(_helm_value_identity_path(identity_path))
        for configuration_path in (
            ("database", "tag"),
            ("source", "repository"),
            ("env", "name"),
        ):
            with self.subTest(configuration_path=configuration_path):
                self.assertFalse(
                    _helm_value_identity_path(configuration_path)
                )

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
                "  values:\n"
                "    env:\n"
                "      - name: ICEBERG_TABLE_BOOTSTRAP_DISABLED\n"
                "        value: 'true'\n"
                "    featureGate: iceberg-table-bootstrap-disabled\n"
                "    database:\n"
                "      tag: iceberg-table-bootstrap-disabled\n"
                "    source:\n"
                "      repository: iceberg-table-bootstrap-examples\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_reads_selected_helmchart_values_from_chart_ref(self) -> None:
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
            (deploy_root / "source.yaml").write_text(
                "apiVersion: source.toolkit.fluxcd.io/v1\n"
                "kind: GitRepository\n"
                "metadata:\n"
                "  name: catalog-source\n"
                "spec:\n"
                "  url: "
                "https://github.com/TommyKammy/Shirokuma.git\n"
                "---\n"
                "apiVersion: source.toolkit.fluxcd.io/v1\n"
                "kind: HelmChart\n"
                "metadata:\n"
                "  name: catalog-chart\n"
                "spec:\n"
                "  chart: ./charts/catalog-task\n"
                "  valuesFiles:\n"
                "    - ./charts/catalog-task/bootstrap-values.yaml\n"
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

    def test_indexes_kindless_kustomization_generators(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            deploy_root.mkdir()
            charts_root.mkdir()
            (deploy_root / "kustomization.yaml").write_text(
                "resources:\n"
                "  - release.yaml\n"
                "configMapGenerator:\n"
                "- name: catalog-values\n"
                "  literals:\n"
                "  - values.yaml=fullnameOverride: "
                "iceberg-table-bootstrap\n"
                "generatorOptions:\n"
                "  disableNameSuffixHash: true\n",
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

    def test_loads_kustomize_env_generator_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            deploy_root.mkdir()
            charts_root.mkdir()
            (deploy_root / "config.env").write_text(
                "values.yaml=fullnameOverride: iceberg-table-bootstrap\n",
                encoding="utf-8",
            )
            (deploy_root / "secret.env").write_text(
                "values.yaml=fullnameOverride: iceberg-table-bootstrap\n",
                encoding="utf-8",
            )
            (deploy_root / "kustomization.yaml").write_text(
                "apiVersion: kustomize.config.k8s.io/v1beta1\n"
                "kind: Kustomization\n"
                "resources:\n"
                "  - config-release.yaml\n"
                "  - secret-release.yaml\n"
                "configMapGenerator:\n"
                "- name: config-values\n"
                "  envs:\n"
                "  - config.env\n"
                "secretGenerator:\n"
                "- name: secret-values\n"
                "  env: secret.env\n"
                "generatorOptions:\n"
                "  disableNameSuffixHash: true\n",
                encoding="utf-8",
            )
            for filename, kind, name in (
                ("config-release.yaml", "ConfigMap", "config-values"),
                ("secret-release.yaml", "Secret", "secret-values"),
            ):
                (deploy_root / filename).write_text(
                    "apiVersion: helm.toolkit.fluxcd.io/v2\n"
                    "kind: HelmRelease\n"
                    "metadata:\n"
                    f"  name: {name}-release\n"
                    "spec:\n"
                    "  valuesFrom:\n"
                    f"    - kind: {kind}\n"
                    f"      name: {name}\n",
                    encoding="utf-8",
                )

            self.assertEqual(
                [
                    deploy_root / "config-release.yaml",
                    deploy_root / "secret-release.yaml",
                ],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_preserves_kustomize_env_file_values_verbatim(self) -> None:
        self.assertEqual(
            {
                "values.yaml": (
                    '"fullnameOverride: iceberg-table-bootstrap"'
                ),
                "export values.yaml": (
                    "fullnameOverride: iceberg-table-bootstrap"
                ),
            },
            _dotenv_data(
                'values.yaml="fullnameOverride: '
                'iceberg-table-bootstrap"\n'
                "export values.yaml=fullnameOverride: "
                "iceberg-table-bootstrap\n"
            ),
        )

    def test_reads_git_source_chart_outside_charts_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            chart_root = deploy_root / "catalog-task"
            deploy_root.mkdir()
            charts_root.mkdir()
            chart_root.mkdir()
            (chart_root / "values.yaml").write_text(
                "fullnameOverride: iceberg-table-bootstrap\n",
                encoding="utf-8",
            )
            source_path = deploy_root / "source.yaml"
            source_document = (
                "apiVersion: source.toolkit.fluxcd.io/v1\n"
                "kind: GitRepository\n"
                "metadata:\n"
                "  name: catalog-source\n"
                "spec:\n"
                "  url: "
                "https://github.com/TommyKammy/Shirokuma.git\n"
            )
            source_path.write_text(
                source_document,
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
                "      chart: ./deploy/catalog-task\n"
                "      sourceRef:\n"
                "        kind: GitRepository\n"
                "        name: catalog-source\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [deploy_root / "release.yaml"],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )
            source_path.write_text(
                source_document.replace(
                    "github.com/TommyKammy/Shirokuma",
                    "github.example/external/catalog",
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                [],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_applies_flux_kustomization_common_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            app_root = deploy_root / "apps"
            app_root.mkdir(parents=True)
            charts_root.mkdir()
            (app_root / "job.yaml").write_text(
                "apiVersion: batch/v1\n"
                "kind: Job\n"
                "metadata:\n"
                "  name: catalog-task\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - name: worker\n"
                "          image: registry.example/worker:v1\n",
                encoding="utf-8",
            )
            (deploy_root / "outside-job.yaml").write_text(
                "apiVersion: batch/v1\n"
                "kind: Job\n"
                "metadata:\n"
                "  name: catalog-task\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - name: worker\n"
                "          image: registry.example/worker:v1\n",
                encoding="utf-8",
            )
            (deploy_root / "flux.yaml").write_text(
                "apiVersion: kustomize.toolkit.fluxcd.io/v1\n"
                "kind: Kustomization\n"
                "metadata:\n"
                "  name: catalog\n"
                "spec:\n"
                "  path: ./deploy/apps\n"
                "  commonMetadata:\n"
                "    labels:\n"
                "      app.kubernetes.io/name: iceberg-table-bootstrap\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [app_root / "job.yaml"],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_scopes_flux_transforms_to_local_git_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            app_root = deploy_root / "apps"
            app_root.mkdir(parents=True)
            charts_root.mkdir()
            (app_root / "job.yaml").write_text(
                "apiVersion: batch/v1\n"
                "kind: Job\n"
                "metadata:\n"
                "  name: catalog-task\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - name: worker\n"
                "          image: registry.example/worker:v1\n",
                encoding="utf-8",
            )
            source_path = deploy_root / "source.yaml"
            source_document = (
                "apiVersion: source.toolkit.fluxcd.io/v1\n"
                "kind: GitRepository\n"
                "metadata:\n"
                "  name: catalog-source\n"
                "spec:\n"
                "  url: https://github.example/external/catalog.git\n"
            )
            source_path.write_text(source_document, encoding="utf-8")
            (deploy_root / "flux.yaml").write_text(
                "apiVersion: kustomize.toolkit.fluxcd.io/v1\n"
                "kind: Kustomization\n"
                "metadata:\n"
                "  name: catalog\n"
                "spec:\n"
                "  sourceRef:\n"
                "    kind: GitRepository\n"
                "    name: catalog-source\n"
                "  path: ./deploy/apps\n"
                "  commonMetadata:\n"
                "    labels:\n"
                "      app.kubernetes.io/name: "
                "iceberg-table-bootstrap\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )
            source_path.write_text(
                source_document.replace(
                    "github.example/external/catalog",
                    "github.com/TommyKammy/Shirokuma",
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                [app_root / "job.yaml"],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_flux_common_metadata_overrides_existing_label(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            app_root = deploy_root / "apps"
            app_root.mkdir(parents=True)
            charts_root.mkdir()
            (app_root / "job.yaml").write_text(
                "apiVersion: batch/v1\n"
                "kind: Job\n"
                "metadata:\n"
                "  name: catalog-task\n"
                "  labels:\n"
                "    app.kubernetes.io/name: iceberg-table-bootstrap\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - name: worker\n"
                "          image: registry.example/worker:v1\n",
                encoding="utf-8",
            )
            (deploy_root / "flux.yaml").write_text(
                "apiVersion: kustomize.toolkit.fluxcd.io/v1\n"
                "kind: Kustomization\n"
                "metadata:\n"
                "  name: catalog\n"
                "spec:\n"
                "  path: ./deploy/apps\n"
                "  commonMetadata:\n"
                "    labels:\n"
                "      app.kubernetes.io/name: catalog-task\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_applies_flux_kustomization_job_patches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            app_root = deploy_root / "apps"
            app_root.mkdir(parents=True)
            charts_root.mkdir()
            (app_root / "job.yaml").write_text(
                "apiVersion: batch/v1\n"
                "kind: Job\n"
                "metadata:\n"
                "  name: catalog-task\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - name: worker\n"
                "          image: registry.example/worker:v1\n",
                encoding="utf-8",
            )
            (deploy_root / "flux.yaml").write_text(
                "apiVersion: kustomize.toolkit.fluxcd.io/v1\n"
                "kind: Kustomization\n"
                "metadata:\n"
                "  name: catalog\n"
                "spec:\n"
                "  path: ./deploy/apps\n"
                "  patches:\n"
                "  - target:\n"
                "      kind: Job\n"
                "    patch: |\n"
                "      - op: replace\n"
                "        path: /metadata/name\n"
                "        value: iceberg-table-bootstrap\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [app_root / "job.yaml"],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_applies_flux_strategic_merge_identity_patch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            app_root = deploy_root / "apps"
            app_root.mkdir(parents=True)
            charts_root.mkdir()
            (app_root / "job.yaml").write_text(
                "apiVersion: batch/v1\n"
                "kind: Job\n"
                "metadata:\n"
                "  name: catalog-task\n"
                "  labels:\n"
                "    role: catalog\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - name: worker\n"
                "          image: registry.example/worker:v1\n",
                encoding="utf-8",
            )
            (deploy_root / "flux.yaml").write_text(
                "apiVersion: kustomize.toolkit.fluxcd.io/v1\n"
                "kind: Kustomization\n"
                "metadata:\n"
                "  name: catalog\n"
                "spec:\n"
                "  path: ./deploy/apps\n"
                "  patches:\n"
                "    - target:\n"
                "        kind: Job\n"
                "        labelSelector: role=catalog\n"
                "      patch: |\n"
                "        metadata:\n"
                "          labels:\n"
                "            app.kubernetes.io/name: "
                "iceberg-table-bootstrap\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [app_root / "job.yaml"],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_ignores_non_identity_and_unmatched_flux_patches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            app_root = deploy_root / "apps"
            app_root.mkdir(parents=True)
            charts_root.mkdir()
            (app_root / "job.yaml").write_text(
                "apiVersion: batch/v1\n"
                "kind: Job\n"
                "metadata:\n"
                "  name: catalog-task\n"
                "  labels:\n"
                "    role: catalog\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - name: worker\n"
                "          image: registry.example/worker:v1\n",
                encoding="utf-8",
            )
            (deploy_root / "flux.yaml").write_text(
                "apiVersion: kustomize.toolkit.fluxcd.io/v1\n"
                "kind: Kustomization\n"
                "metadata:\n"
                "  name: catalog\n"
                "spec:\n"
                "  path: ./deploy/apps\n"
                "  patches:\n"
                "    - target:\n"
                "        kind: Job\n"
                "        name: another-job\n"
                "      patch: |\n"
                "        - op: replace\n"
                "          path: /metadata/name\n"
                "          value: iceberg-table-bootstrap\n"
                "    - target:\n"
                "        kind: Job\n"
                "        labelSelector: role=another\n"
                "      patch: |\n"
                "        metadata:\n"
                "          labels:\n"
                "            app.kubernetes.io/name: "
                "iceberg-table-bootstrap\n"
                "    - target:\n"
                "        kind: Job\n"
                "        name: catalog-task\n"
                "      patch: |\n"
                "        - op: test\n"
                "          path: /spec/template/spec/containers/0/"
                "env/0/value\n"
                "          value: iceberg-table-bootstrap\n"
                "    - target:\n"
                "        kind: Job\n"
                "        name: catalog-task\n"
                "      patch: |\n"
                "        metadata:\n"
                "          name: iceberg-table-bootstrap\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_flux_identity_uses_final_rendered_state(self) -> None:
        scenarios = (
            (
                "name-replaced",
                "iceberg-table-bootstrap",
                "  patches:\n"
                "    - target:\n"
                "        kind: Job\n"
                "        name: iceberg-table-bootstrap\n"
                "      patch: |\n"
                "        - op: replace\n"
                "          path: /metadata/name\n"
                "          value: catalog-task\n",
            ),
            (
                "image-rewritten",
                "catalog-task",
                "  patches:\n"
                "    - target:\n"
                "        kind: Job\n"
                "      patch: |\n"
                "        - op: replace\n"
                "          path: /spec/template/spec/containers/0/image\n"
                "          value: registry.example/"
                "iceberg-table-bootstrap:v1\n"
                "  images:\n"
                "    - name: registry.example/"
                "iceberg-table-bootstrap\n"
                "      newName: registry.example/catalog-task\n"
                "      newTag: v1\n",
            ),
            (
                "label-overridden",
                "catalog-task",
                "  patches:\n"
                "    - target:\n"
                "        kind: Job\n"
                "      patch: |\n"
                "        metadata:\n"
                "          labels:\n"
                "            app.kubernetes.io/name: "
                "iceberg-table-bootstrap\n"
                "  commonMetadata:\n"
                "    labels:\n"
                "      app.kubernetes.io/name: catalog-task\n",
            ),
            (
                "resource-deleted",
                "catalog-task",
                "  patches:\n"
                "    - target:\n"
                "        kind: Job\n"
                "      patch: |\n"
                "        $patch: delete\n"
                "        metadata:\n"
                "          labels:\n"
                "            app.kubernetes.io/name: "
                "iceberg-table-bootstrap\n",
            ),
        )
        for scenario, job_name, transforms in scenarios:
            with self.subTest(scenario=scenario):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    root = Path(temporary_directory)
                    deploy_root = root / "deploy"
                    charts_root = root / "charts"
                    app_root = deploy_root / "apps"
                    app_root.mkdir(parents=True)
                    charts_root.mkdir()
                    (app_root / "job.yaml").write_text(
                        "apiVersion: batch/v1\n"
                        "kind: Job\n"
                        "metadata:\n"
                        f"  name: {job_name}\n"
                        "spec:\n"
                        "  template:\n"
                        "    spec:\n"
                        "      containers:\n"
                        "        - name: worker\n"
                        "          image: registry.example/worker:v1\n",
                        encoding="utf-8",
                    )
                    (deploy_root / "flux.yaml").write_text(
                        "apiVersion: "
                        "kustomize.toolkit.fluxcd.io/v1\n"
                        "kind: Kustomization\n"
                        "metadata:\n"
                        "  name: catalog\n"
                        "spec:\n"
                        "  path: ./deploy/apps\n"
                        f"{transforms}",
                        encoding="utf-8",
                    )

                    self.assertEqual(
                        [],
                        _iceberg_bootstrap_manifests(
                            deploy_root, charts_root
                        ),
                    )

    def test_flux_strategic_merge_replaces_container_list(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            app_root = deploy_root / "apps"
            app_root.mkdir(parents=True)
            charts_root.mkdir()
            (app_root / "job.yaml").write_text(
                "apiVersion: batch/v1\n"
                "kind: Job\n"
                "metadata:\n"
                "  name: catalog-task\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - name: iceberg-table-bootstrap\n"
                "          image: registry.example/"
                "iceberg-table-bootstrap:v1\n",
                encoding="utf-8",
            )
            (deploy_root / "flux.yaml").write_text(
                "apiVersion: kustomize.toolkit.fluxcd.io/v1\n"
                "kind: Kustomization\n"
                "metadata:\n"
                "  name: catalog\n"
                "spec:\n"
                "  path: ./deploy/apps\n"
                "  patches:\n"
                "    - target:\n"
                "        kind: Job\n"
                "      patch: |\n"
                "        spec:\n"
                "          template:\n"
                "            spec:\n"
                "              containers:\n"
                "                - $patch: replace\n"
                "                - name: worker\n"
                "                  image: registry.example/worker:v1\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_applies_flux_kustomization_image_rewrites(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            app_root = deploy_root / "apps"
            app_root.mkdir(parents=True)
            charts_root.mkdir()
            (app_root / "job.yaml").write_text(
                "apiVersion: batch/v1\n"
                "kind: Job\n"
                "metadata:\n"
                "  name: catalog-task\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - name: worker\n"
                "          image: registry.example/catalog-task:v1\n",
                encoding="utf-8",
            )
            (deploy_root / "flux.yaml").write_text(
                "apiVersion: kustomize.toolkit.fluxcd.io/v1\n"
                "kind: Kustomization\n"
                "metadata:\n"
                "  name: catalog\n"
                "spec:\n"
                "  path: ./deploy/apps\n"
                "  images:\n"
                "  - name: registry.example/catalog-task\n"
                "    newName: registry.example/iceberg-table-bootstrap\n"
                "    newTag: v1\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [app_root / "job.yaml"],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_flux_image_rule_does_not_suffix_match_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            app_root = deploy_root / "apps"
            app_root.mkdir(parents=True)
            charts_root.mkdir()
            (app_root / "job.yaml").write_text(
                "apiVersion: batch/v1\n"
                "kind: Job\n"
                "metadata:\n"
                "  name: catalog-task\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - name: worker\n"
                "          image: "
                "registry.example/team/catalog-task:v1\n",
                encoding="utf-8",
            )
            (deploy_root / "flux.yaml").write_text(
                "apiVersion: kustomize.toolkit.fluxcd.io/v1\n"
                "kind: Kustomization\n"
                "metadata:\n"
                "  name: catalog\n"
                "spec:\n"
                "  path: ./deploy/apps\n"
                "  images:\n"
                "    - name: catalog-task\n"
                "      newName: "
                "registry.example/iceberg-table-bootstrap\n"
                "      newTag: v1\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_flux_image_rewrite_replaces_original_image(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            app_root = deploy_root / "apps"
            app_root.mkdir(parents=True)
            charts_root.mkdir()
            (app_root / "job.yaml").write_text(
                "apiVersion: batch/v1\n"
                "kind: Job\n"
                "metadata:\n"
                "  name: catalog-task\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - name: worker\n"
                "          image: "
                "registry.example/iceberg-table-bootstrap:v1\n",
                encoding="utf-8",
            )
            (deploy_root / "flux.yaml").write_text(
                "apiVersion: kustomize.toolkit.fluxcd.io/v1\n"
                "kind: Kustomization\n"
                "metadata:\n"
                "  name: catalog\n"
                "spec:\n"
                "  path: ./deploy/apps\n"
                "  images:\n"
                "    - name: "
                "registry.example/iceberg-table-bootstrap\n"
                "      newName: registry.example/catalog-task\n"
                "      newTag: v2\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_flux_image_digest_ignores_new_tag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            deploy_root = root / "deploy"
            charts_root = root / "charts"
            app_root = deploy_root / "apps"
            app_root.mkdir(parents=True)
            charts_root.mkdir()
            (app_root / "job.yaml").write_text(
                "apiVersion: batch/v1\n"
                "kind: Job\n"
                "metadata:\n"
                "  name: catalog-task\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - name: worker\n"
                "          image: registry.example/catalog-task:v1\n",
                encoding="utf-8",
            )
            (deploy_root / "flux.yaml").write_text(
                "apiVersion: kustomize.toolkit.fluxcd.io/v1\n"
                "kind: Kustomization\n"
                "metadata:\n"
                "  name: catalog\n"
                "spec:\n"
                "  path: ./deploy/apps\n"
                "  images:\n"
                "    - name: registry.example/catalog-task\n"
                "      newName: registry.example/catalog-task\n"
                "      newTag: iceberg-table-bootstrap\n"
                "      digest: sha256:deadbeef\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_preserves_escaped_helm_target_path_components(self) -> None:
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
                "  identity: iceberg-table-bootstrap\n",
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
                "      name: catalog-values\n"
                "      valuesKey: identity\n"
                '      targetPath: "metadata.labels.'
                'app\\\\.kubernetes\\\\.io/name"\n',
                encoding="utf-8",
            )

            self.assertEqual(
                [deploy_root / "release.yaml"],
                _iceberg_bootstrap_manifests(deploy_root, charts_root),
            )

    def test_resolves_yaml_scalar_aliases_for_job_identity(self) -> None:
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
                "  annotations:\n"
                "    bootstrap-name: &bootstrap "
                "iceberg-table-bootstrap\n"
                "  name: *bootstrap\n"
                "  labels:\n"
                "    alias-reset: &bootstrap catalog-task\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - name: worker\n"
                "          image: registry.example/worker:v1\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [deploy_root / "job.yaml"],
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
