from __future__ import annotations

import base64
import binascii
import json
import re
import tempfile
import unittest
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
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

ROOT = Path(__file__).resolve().parents[1]
TRINO_COMPONENT = "trino"
POSTGRESQL_COMPONENT = "postgresql"
TRINO_ADMISSION = ROOT / "bootstrap/trino/v483/admission.json"
TRINO_TRUSTED_BUILD_CONTRACT = (
    ROOT / "bootstrap/trino/v483/trusted-build-contract.json"
)
TRINO_476_FEASIBILITY = ROOT / "bootstrap/trino/v476/feasibility.json"
TRINO_SOURCE_BUILD_ADR = (
    ROOT / "docs/design/07_ADR/ADR-0022_Adopt_Trino_483_repository_source_build.md"
)
TRINO_PROVISIONAL_SOURCE_ADR = ROOT / (
    "docs/design/07_ADR/"
    "ADR-0023_Allow_time_boxed_Trino_483_source_identity_exception_for_local_PoC.md"
)
TRINO_PROVISIONAL_APPROVAL_WINDOWS = {
    "https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-5052385803": (
        "2026-07-22T22:43:36Z",
        "2026-08-21T22:43:36Z",
    )
}
TRINO_BUILDER_INDEX = (
    "docker.io/library/maven@"
    "sha256:7e461cec477077c1d9e50b13df8aef9018764410f4c4cd7c34803f10c4c99e4c"
)
TRINO_BUILDER_ARM64_DIGEST = (
    "sha256:5476bfca9d0a6485b7161f6863123f7e6822336de4177273b47b5ec38ffd573a"
)
TRINO_RUNTIME_INDEX = (
    "docker.io/library/amazoncorretto@"
    "sha256:32d81edae73e1670244827c2f12e5bcf0d335f035b538455fe9d02eb0771d41b"
)
TRINO_RUNTIME_ARM64_DIGEST = (
    "sha256:da20e1e0a2004dfb95e963d6ad978b5c0effdfc7000bce6a68836058ef24b427"
)
TRINO_INDEX_REFERENCE = (
    "docker.io/trinodb/trino@"
    "sha256:db58cc93e593a2706553745f276bb119c9810e69918be56ecde088ba7ccb0534"
)
TRINO_ARM64_DIGEST = (
    "sha256:aa18e61b2e7776ab8641ba8baaa8687d0430894e88c639e61010cc46a994ab36"
)
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


def _parse_utc_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value
    ):
        raise ValueError("timestamp must use second-precision UTC Z format")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )


def _provisional_source_authorization_errors(
    admission: dict, *, now: datetime
) -> list[str]:
    errors: list[str] = []
    authorization = admission.get("provisional_source_authorization")
    if not isinstance(authorization, dict):
        return ["provisional_source_authorization must be an object"]

    if authorization.get("status") != "active":
        errors.append("authorization must be active")
    if type(authorization.get("maximum_duration_days")) is not int:
        errors.append("maximum_duration_days must be an integer")
    elif authorization["maximum_duration_days"] != 30:
        errors.append("maximum_duration_days must equal 30")
    if authorization.get("automatic_renewal") is not False:
        errors.append("automatic renewal is forbidden")
    if authorization.get("stacked_vulnerability_exception_permitted") is not False:
        errors.append("stacked vulnerability exceptions are forbidden")

    approval_record = authorization.get("approval_record")
    if not isinstance(approval_record, str) or not approval_record.strip():
        approval_window = None
        errors.append("approval record must be a non-empty URL")
    else:
        approval_window = TRINO_PROVISIONAL_APPROVAL_WINDOWS.get(approval_record)
    if (
        isinstance(approval_record, str)
        and approval_record.strip()
        and approval_window is None
    ):
        errors.append("approval record is not recognized")
    elif approval_window is not None and (
        authorization.get("approved_at"),
        authorization.get("expires_at"),
    ) != approval_window:
        errors.append("authorization timestamps do not match the approval record")

    try:
        approved_at = _parse_utc_timestamp(authorization.get("approved_at"))
        expires_at = _parse_utc_timestamp(authorization.get("expires_at"))
    except ValueError as error:
        errors.append(str(error))
    else:
        if expires_at <= approved_at:
            errors.append("expiry must follow approval")
        if expires_at - approved_at > timedelta(days=30):
            errors.append("authorization exceeds 30 days")
        if now.tzinfo is None or now.utcoffset() is None:
            errors.append("validation time must be timezone-aware")
        elif now < approved_at:
            errors.append("authorization is not yet active")
        elif now >= expires_at:
            errors.append("authorization is expired")

    scope = authorization.get("scope")
    if not isinstance(scope, dict):
        errors.append("scope must be an object")
    else:
        if scope.get("source_binding") != admission.get(
            "source_authentication", {}
        ).get("required_binding"):
            errors.append("provisional source binding must match the required binding")
        if scope.get("profile") != "mac-studio-solo/local-lite":
            errors.append("authorization is limited to mac-studio-solo/local-lite")
        if scope.get("purpose") != "non-production-poc":
            errors.append("authorization is limited to the non-production PoC")
        if scope.get("data_classification") != ["synthetic", "poc"]:
            errors.append("authorization permits only synthetic and PoC data")
        if scope.get("public_service_or_ingress_permitted") is not False:
            errors.append("public Service or Ingress is forbidden")

    risk_owner = authorization.get("risk_owner")
    implementation_author = authorization.get("implementation_author")
    if not isinstance(risk_owner, str) or not risk_owner.strip():
        errors.append("risk_owner must be a non-empty name")
    if not isinstance(implementation_author, str) or not implementation_author.strip():
        errors.append("implementation_author must be a non-empty name")
    if (
        isinstance(risk_owner, str)
        and risk_owner.strip()
        and isinstance(implementation_author, str)
        and implementation_author.strip()
        and risk_owner.strip().casefold() == implementation_author.strip().casefold()
    ):
        errors.append("risk owner and implementation author must differ")
    review = authorization.get("review")
    if not isinstance(review, dict):
        errors.append("review must be an object")
    else:
        if review.get("required_before_merge") is not True:
            errors.append("review must be required before merge")
        if review.get("reviewer_must_differ_from_implementation_author") is not True:
            errors.append("reviewer must differ from implementation author")
        if review.get("enforcement") != "required_pull_request_review_before_merge":
            errors.append("authorization must require pull request review before merge")

    required_controls = {
        "authenticated closed dependency snapshot",
        "network-none reproducible native linux/arm64 build",
        "digest-pinned builder and runtime bases",
        "native linux/arm64 runtime smoke",
        "High=0/Critical=0 fresh vulnerability scan",
        "retained CycloneDX SBOM and scan evidence",
        "Cosign signature and Rekor transparency-log evidence",
        "SLSA provenance bound to the exact source revision",
        "anonymous exact-digest retrieval",
        "separate resident-image admission",
        (
            "credential-safe Flux reconciliation and deterministic "
            "Polaris/Iceberg query acceptance"
        ),
    }
    controls = authorization.get("non_waivable_controls")
    if not isinstance(controls, list) or set(controls) != required_controls:
        errors.append("non-waivable controls are incomplete or unexpected")

    return errors


def _github_workflow_paths(root: Path = ROOT) -> list[Path]:
    workflows = root / ".github/workflows"
    return sorted(
        path
        for path in workflows.iterdir()
        if path.is_file() and path.suffix.casefold() in {".yaml", ".yml"}
    )


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
    values_sources: HelmValuesSources | None = None,
    charts_root: Path = CHARTS_ROOT,
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
            values_sources,
            release_path,
            charts_root,
            chart_references,
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
    values_sources = _helm_values_sources(deploy_root, charts_root)
    chart_references = _helm_chart_references(deploy_root, charts_root)
    for path in _deployment_manifest_paths(deploy_root, charts_root):
        documents = re.split(
            r"(?m)^---[ \t]*(?:#.*)?$", path.read_text(encoding="utf-8")
        )
        if any(
            _is_trino_workload(
                document,
                release_path=path,
                chart_references=chart_references,
                admitted_images=admitted_images,
                values_sources=values_sources,
                charts_root=charts_root,
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

    def test_accepts_values_from_configmap_with_admitted_image(self) -> None:
        image = "registry.example/trino@sha256:" + "a" * 64
        chart_source = (
            "apiVersion: source.toolkit.fluxcd.io/v1\n"
            "kind: HelmRepository\n"
            "metadata:\n"
            "  name: trino\n"
            "  namespace: query\n"
        )
        values_source = (
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: trino-values\n"
            "  namespace: query\n"
            "data:\n"
            "  values.yaml: |\n"
            "    image:\n"
            f"      reference: {image}\n"
        )
        release = (
            "apiVersion: helm.toolkit.fluxcd.io/v2\n"
            "kind: HelmRelease\n"
            "metadata:\n"
            "  name: trino\n"
            "  namespace: query\n"
            "spec:\n"
            "  chart:\n"
            "    spec:\n"
            "      chart: trino\n"
            "      sourceRef:\n"
            "        kind: HelmRepository\n"
            "        name: trino\n"
            "  valuesFrom:\n"
            "    - kind: ConfigMap\n"
            "      name: trino-values\n"
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            deploy_root = Path(temporary_directory) / "deploy"
            charts_root = Path(temporary_directory) / "charts"
            deploy_root.mkdir()
            charts_root.mkdir()
            manifest = deploy_root / "trino.yaml"
            manifest.write_text(
                chart_source + "---\n" + values_source + "---\n" + release,
                encoding="utf-8",
            )

            workloads = _trino_workload_manifests(
                deploy_root, charts_root, {image}
            )

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


class TrinoAdmissionBlockerTests(unittest.TestCase):
    @staticmethod
    def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key: {key}")
            result[key] = value
        return result

    def _admission(self) -> dict:
        return json.loads(
            TRINO_ADMISSION.read_text(encoding="utf-8"),
            object_pairs_hook=self._reject_duplicate_keys,
        )

    def _feasibility(self) -> dict:
        return json.loads(
            TRINO_476_FEASIBILITY.read_text(encoding="utf-8"),
            object_pairs_hook=self._reject_duplicate_keys,
        )

    def _trusted_build_contract(self) -> dict:
        return json.loads(
            TRINO_TRUSTED_BUILD_CONTRACT.read_text(encoding="utf-8"),
            object_pairs_hook=self._reject_duplicate_keys,
        )

    def test_476_signed_distribution_is_rejected(self) -> None:
        feasibility = self._feasibility()
        self.assertEqual(
            {
                "schema_version",
                "component",
                "version",
                "review_type",
                "assessed_on",
                "platform",
                "source",
                "distribution",
                "archive",
                "runtime",
                "vulnerability_assessment",
                "decision",
            },
            set(feasibility),
        )
        self.assertEqual(1, feasibility["schema_version"])
        self.assertEqual("trino", feasibility["component"])
        self.assertEqual("476", feasibility["version"])
        self.assertEqual(
            "signed_distribution_feasibility", feasibility["review_type"]
        )
        self.assertEqual("2026-07-23", feasibility["assessed_on"])
        self.assertEqual("linux/arm64", feasibility["platform"])
        self.assertEqual(
            {
                "repository": "https://github.com/trinodb/trino",
                "release_tag": "476",
                "tag_object_sha": "ecb143d60e11131d167b3d3e1d726e053745aa6f",
                "commit_sha": "7f3746a7fa0b27ace2470340e848feaf3ee73f48",
                "tree_sha": "74ac3497643a111798df430355077f3a9a9d6da5",
                "tag_signature": "unsigned",
                "commit_signature": "unsigned",
            },
            feasibility["source"],
        )

        distribution = feasibility["distribution"]
        self.assertEqual(821045832, distribution["bytes"])
        self.assertEqual(
            "cfd5accde17e8ebd251eeeb78aed1f490e77bb3a164d95a0f454bf8a7c1cbd3f",
            distribution["sha256"],
        )
        signature = distribution["detached_signature"]
        self.assertEqual(
            {
                "algorithm",
                "created_at",
                "issuer_fingerprint",
                "issuer_key_id",
                "cryptographic_verification",
                "trust_root_status",
                "key_source",
                "public_material_sha256",
                "limitations",
            },
            set(signature),
        )
        self.assertEqual(
            "passed_with_pgpy_0.6.0",
            signature["cryptographic_verification"],
        )
        self.assertEqual("unapproved", signature["trust_root_status"])
        self.assertEqual(
            "C328250FE23A2420814521EC0EB69F76FD171538",
            signature["issuer_fingerprint"],
        )
        self.assertEqual(
            "e37a6a94215760b0bfa695eedd12ff70962df737a8aa648643b710c0660850b3",
            signature["public_material_sha256"],
        )
        self.assertGreaterEqual(len(signature["limitations"]), 3)

        assessment = feasibility["vulnerability_assessment"]
        self.assertEqual(
            {"UNKNOWN": 2, "LOW": 9, "MEDIUM": 55, "HIGH": 52, "CRITICAL": 2},
            assessment["severity_counts"],
        )
        findings = {
            finding["id"]: finding
            for finding in assessment["blocking_findings"]
        }
        self.assertEqual(
            {"CVE-2025-68121", "CVE-2025-59059", "CVE-2026-34214"},
            set(findings),
        )
        self.assertEqual("CRITICAL", findings["CVE-2025-68121"]["severity"])
        self.assertEqual("CRITICAL", findings["CVE-2025-59059"]["severity"])
        self.assertEqual("HIGH", findings["CVE-2026-34214"]["severity"])
        self.assertEqual(
            "io.trino:trino-iceberg",
            findings["CVE-2026-34214"]["package"],
        )
        self.assertEqual("480", findings["CVE-2026-34214"]["fixed_version"])
        self.assertIs(assessment["raw_artifacts_retained"], False)
        self.assertIs(
            assessment["fresh_scan_required_for_future_candidate"], True
        )

        decision = feasibility["decision"]
        self.assertEqual("rejected", decision["status"])
        for key in (
            "exception_eligible",
            "admission_permitted",
            "publication_workflow_permitted",
            "resident_ledger_permitted",
            "runtime_manifests_permitted",
        ):
            self.assertIs(decision[key], False)
        self.assertEqual(
            "bootstrap/trino/v476/feasibility.json",
            decision["allowed_path"],
        )

    def test_476_archive_review_does_not_claim_runtime_acceptance(self) -> None:
        feasibility = self._feasibility()
        archive = feasibility["archive"]
        self.assertEqual(6732, archive["entries"])
        self.assertEqual(5713, archive["hard_links"])
        self.assertEqual(454, archive["unique_hard_link_targets"])
        self.assertEqual(0, archive["symbolic_links"])
        self.assertEqual(0, archive["special_files"])
        self.assertEqual(0, archive["unsafe_paths"])
        self.assertEqual(0, archive["missing_hard_link_targets"])
        self.assertIn("linux-arm64", archive["native_launchers"])
        self.assertEqual(
            "plugin/iceberg/io.trino_trino-iceberg-476.jar",
            archive["iceberg_module"],
        )
        self.assertEqual("24.0.1", feasibility["runtime"]["minimum_java"])
        self.assertEqual(
            "not_smoked",
            feasibility["runtime"]["java_25_alpine_3_24_compatibility"],
        )

    def test_blocker_checkpoint_is_closed_world_and_immutable(self) -> None:
        admission = self._admission()
        self.assertEqual(
            {
                "schema_version",
                "component",
                "version",
                "source",
                "platform",
                "candidate",
                "assessment",
                "source_authentication",
                "provisional_source_authorization",
                "repository_state",
                "next_action",
            },
            set(admission),
        )
        self.assertIs(type(admission["schema_version"]), int)
        self.assertEqual(2, admission["schema_version"])
        self.assertEqual("trino", admission["component"])
        self.assertEqual("483", admission["version"])
        self.assertEqual("linux/arm64", admission["platform"])
        self.assertEqual(
            {
                "repository": "https://github.com/trinodb/trino",
                "release_tag": "483",
                "tag_object_sha": "32d4f28e8311ea6f67edca209df59a0493d869fa",
                "commit_sha": "50b0b50b75abd47f830b7805ee1b51716eb4065e",
                "tag_signature": "unsigned",
                "commit_signature": "unsigned",
                "server_asset": {
                    "url": (
                        "https://github.com/trinodb/trino/releases/download/483/"
                        "trino-server-483.tar.gz"
                    ),
                    "sha256": (
                        "4f3978428f26f36398c94b85a3e03b5301394919c8a4271b"
                        "497b0fcd1698d0cb"
                    ),
                    "bytes": 851844304,
                    "role": "evaluated_upstream_binary_not_approved_build_input",
                },
            },
            admission["source"],
        )
        self.assertIs(type(admission["source"]["server_asset"]["bytes"]), int)
        self.assertEqual(
            {
                "index_reference": TRINO_INDEX_REFERENCE,
                "manifest_digest": TRINO_ARM64_DIGEST,
                "observed_platforms": [
                    "linux/amd64",
                    "linux/arm64",
                    "linux/ppc64le",
                ],
                "attestation_manifest_count": 0,
            },
            admission["candidate"],
        )
        self.assertIs(
            type(admission["candidate"]["attestation_manifest_count"]), int
        )

    def test_missing_trust_controls_cannot_be_waived_by_exception(self) -> None:
        assessment = self._admission()["assessment"]
        self.assertEqual(
            {
                "assessed_on": "2026-07-22",
                "scope": "mac-studio-solo/local-lite",
                "admission": "blocked",
                "exception_eligible": False,
                "blockers": [
                    {
                        "control": "upstream_image_signature",
                        "status": "missing",
                        "evidence": (
                            "the immutable 483 image index exposes only runtime "
                            "platform manifests and no trusted signer is documented"
                        ),
                    },
                    {
                        "control": "source_tag_signature",
                        "status": "missing",
                        "evidence": (
                            "GitHub reports annotated tag object "
                            "32d4f28e8311ea6f67edca209df59a0493d869fa as unsigned"
                        ),
                    },
                    {
                        "control": "source_commit_signature",
                        "status": "missing",
                        "evidence": (
                            "GitHub reports source commit "
                            "50b0b50b75abd47f830b7805ee1b51716eb4065e as unsigned"
                        ),
                    },
                    {
                        "control": "slsa_provenance",
                        "status": "missing",
                        "evidence": (
                            "no trusted provenance statement binds the upstream image "
                            "or server asset to commit "
                            "50b0b50b75abd47f830b7805ee1b51716eb4065e"
                        ),
                    },
                    {
                        "control": "repository_source_build",
                        "status": "not_retained",
                        "evidence": (
                            "no reviewed dependency closure, offline build, signature, "
                            "SBOM, scan, or runtime-smoke evidence exists in this repository"
                        ),
                    },
                ],
                "rationale": (
                    "ADR-0019 does not waive source identity, image signature, "
                    "transparency-log, provenance, or evidence requirements. ADR-0023 "
                    "separately accepts only the exact Trino 483 source-identity risk "
                    "for a time-boxed local PoC; the upstream image and server asset "
                    "remain rejected, all image controls remain mandatory, and "
                    "re-signing untrusted upstream bytes is forbidden."
                ),
            },
            assessment,
        )

    def test_source_authentication_is_only_provisionally_authorized(self) -> None:
        admission = self._admission()
        self.assertEqual(
            {
                "status": "provisionally_authorized_for_local_poc",
                "authorization_record": (
                    "docs/design/07_ADR/"
                    "ADR-0023_Allow_time_boxed_Trino_483_source_identity_exception_for_local_PoC.md"
                ),
                "required_binding": {
                    "repository": "https://github.com/trinodb/trino",
                    "release_tag": "483",
                    "commit_sha": "50b0b50b75abd47f830b7805ee1b51716eb4065e",
                    "tree_sha": "3b5414292a614b12393bb4605ea2d4c588a5b8ee",
                },
                "accepted_evidence_classes": [
                    "verified upstream signature from a separately approved Trino "
                    "release identity over the exact tag, or over the exact commit "
                    "plus an authenticated release-to-commit binding",
                    "signed upstream source release verified against a separately "
                    "approved Trino release trust root and signer identity, whose "
                    "digest and extracted tree bind to the exact commit and tree",
                    "trusted upstream provenance statement whose subject and source "
                    "claims bind to the exact repository, tag, commit, and tree",
                ],
                "sha_only_is_sufficient": False,
            },
            admission["source_authentication"],
        )
        self.assertIs(
            admission["repository_state"]["publication_workflow_permitted"], True
        )

    def test_provisional_source_authorization_is_bounded_and_fail_closed(self) -> None:
        admission = self._admission()
        authorization = admission["provisional_source_authorization"]
        self.assertEqual(
            {
                "status",
                "authorization_type",
                "decision_record",
                "approval_record",
                "issue",
                "approved_at",
                "expires_at",
                "maximum_duration_days",
                "automatic_renewal",
                "risk_owner",
                "implementation_author",
                "review",
                "scope",
                "accepted_risk",
                "non_waivable_controls",
                "stacked_vulnerability_exception_permitted",
                "expiry_action",
            },
            set(authorization),
        )
        self.assertEqual(
            "time_boxed_source_identity_risk_acceptance",
            authorization["authorization_type"],
        )
        self.assertEqual(
            "https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-5052385803",
            authorization["approval_record"],
        )
        self.assertEqual(
            [
                "the exact source binding lacks a qualifying upstream publisher "
                "signature or provenance statement"
            ],
            authorization["accepted_risk"],
        )
        self.assertEqual(
            "fail_closed_before_dependency_or_image_publication_"
            "resident_admission_or_runtime_reconciliation",
            authorization["expiry_action"],
        )
        self.assertEqual(
            [],
            _provisional_source_authorization_errors(
                admission, now=datetime.now(timezone.utc)
            ),
        )

    def test_provisional_source_authorization_rejects_policy_drift(self) -> None:
        mutations: list[tuple[str, Callable[[dict], None], str]] = [
            (
                "expired",
                lambda record: None,
                "authorization is expired",
            ),
            (
                "over-30-day",
                lambda record: record["provisional_source_authorization"].__setitem__(
                    "expires_at", "2026-08-21T22:43:37Z"
                ),
                "authorization exceeds 30 days",
            ),
            (
                "automatic-renewal",
                lambda record: record["provisional_source_authorization"].__setitem__(
                    "automatic_renewal", True
                ),
                "automatic renewal is forbidden",
            ),
            (
                "in-place-renewal",
                lambda record: record["provisional_source_authorization"].update(
                    {
                        "approved_at": "2026-07-23T22:43:36Z",
                        "expires_at": "2026-08-22T22:43:36Z",
                    }
                ),
                "authorization timestamps do not match the approval record",
            ),
            (
                "unrecognized-approval-record",
                lambda record: record["provisional_source_authorization"].__setitem__(
                    "approval_record",
                    "https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-0",
                ),
                "approval record is not recognized",
            ),
            (
                "blank-risk-owner",
                lambda record: record["provisional_source_authorization"].__setitem__(
                    "risk_owner", "   "
                ),
                "risk_owner must be a non-empty name",
            ),
            (
                "owner-author-collision",
                lambda record: record["provisional_source_authorization"].__setitem__(
                    "implementation_author", "TommyKammy"
                ),
                "risk owner and implementation author must differ",
            ),
            (
                "source-mismatch",
                lambda record: record["provisional_source_authorization"]["scope"][
                    "source_binding"
                ].__setitem__("commit_sha", "0" * 40),
                "provisional source binding must match the required binding",
            ),
            (
                "stacked-vulnerability-exception",
                lambda record: record["provisional_source_authorization"].__setitem__(
                    "stacked_vulnerability_exception_permitted", True
                ),
                "stacked vulnerability exceptions are forbidden",
            ),
        ]
        for name, mutate, expected_error in mutations:
            admission = json.loads(json.dumps(self._admission()))
            mutate(admission)
            now = datetime(2026, 8, 21, 22, 43, 36, tzinfo=timezone.utc)
            if name != "expired":
                now = datetime(2026, 7, 23, tzinfo=timezone.utc)
            with self.subTest(mutation=name):
                self.assertIn(
                    expected_error,
                    _provisional_source_authorization_errors(admission, now=now),
                )

    def test_provisional_source_authorization_rejects_preapproval_use(self) -> None:
        self.assertIn(
            "authorization is not yet active",
            _provisional_source_authorization_errors(
                self._admission(),
                now=datetime(2026, 7, 22, 22, 43, 35, tzinfo=timezone.utc),
            ),
        )

    def test_blocked_candidate_cannot_publish_admit_or_materialize(self) -> None:
        admission = self._admission()
        repository_state = admission["repository_state"]
        self.assertEqual(
            {
                "dependency_snapshot_contract_permitted": True,
                "publication_workflow_permitted": True,
                "dependency_artifact_present": False,
                "resident_ledger_permitted": False,
                "runtime_manifests_permitted": False,
                "allowed_paths": [
                    ".github/workflows/trino-maven-dependencies.yml",
                    "bootstrap/trino/v483/admission.json",
                    "bootstrap/trino/v483/maven-policy/.mvn/jvm.config",
                    "bootstrap/trino/v483/settings.xml",
                    "bootstrap/trino/v483/trusted-build-contract.json",
                    "scripts/package_trino_maven_dependencies.py",
                    "scripts/verify_polaris_trusted_image.py",
                    "scripts/verify_trino_dependency_publisher.py",
                    "tests/test_trino_dependency_publisher.py",
                    "Makefile",
                ],
                "forbidden_paths": [
                    ".github/workflows/trino-arm64.yml",
                    "bootstrap/trino/v483/Containerfile",
                    "security/evidence/trino-v483",
                    "deploy/trino",
                    "deploy/gitops/trino",
                    "charts/trino",
                ],
            },
            repository_state,
        )
        for key in (
            "resident_ledger_permitted",
            "runtime_manifests_permitted",
        ):
            self.assertIs(repository_state[key], False)
        self.assertIs(repository_state["publication_workflow_permitted"], True)
        self.assertIs(
            repository_state["dependency_snapshot_contract_permitted"], True
        )
        bootstrap_inventory = {
            path.relative_to(ROOT).as_posix()
            for path in TRINO_ADMISSION.parent.rglob("*")
            if path.is_file() or path.is_symlink()
        }
        self.assertTrue(
            bootstrap_inventory <= set(repository_state["allowed_paths"])
        )
        all_trino_bootstrap_paths = {
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "bootstrap").rglob("*")
            if (path.is_file() or path.is_symlink())
            and "trino" in path.relative_to(ROOT).as_posix().casefold()
        }
        expected_trino_bootstrap_paths = bootstrap_inventory | {
            self._feasibility()["decision"]["allowed_path"]
        }
        self.assertEqual(
            expected_trino_bootstrap_paths, all_trino_bootstrap_paths
        )
        for relative in repository_state["forbidden_paths"]:
            with self.subTest(forbidden_path=relative):
                self.assertFalse((ROOT / relative).exists())

        ledger = json.loads(RESIDENT_IMAGES.read_text(encoding="utf-8"))
        self.assertEqual(
            [],
            [
                image.get("component", "<unknown>")
                for image in ledger["images"]
                if image.get("component") == TRINO_COMPONENT
                or image.get("reference") == TRINO_INDEX_REFERENCE
                or image.get("reference", "").endswith(TRINO_ARM64_DIGEST)
            ],
        )
        self.assertEqual([], _trino_workload_manifests())
        for path in _deployment_manifest_paths(DEPLOY_ROOT, CHARTS_ROOT):
            text = path.read_text(encoding="utf-8")
            with self.subTest(manifest=path):
                self.assertNotIn(TRINO_INDEX_REFERENCE, text)
                self.assertNotIn(TRINO_ARM64_DIGEST, text)
                self.assertNotIn("trinodb/trino", text.casefold())
                self.assertNotIn("shirokuma-trino", text.casefold())
        for path in _github_workflow_paths():
            workflow = path.read_text(encoding="utf-8").casefold()
            with self.subTest(workflow=path):
                if path.name == "trino-maven-dependencies.yml":
                    self.assertIn(
                        "https://github.com/trinodb/trino", workflow
                    )
                    self.assertIn(
                        "shirokuma-trino-maven-dependencies", workflow
                    )
                    self.assertNotIn(TRINO_INDEX_REFERENCE, workflow)
                    self.assertNotIn("docker.io/trinodb/trino", workflow)
                else:
                    self.assertNotIn("trinodb/trino", workflow)
                    self.assertNotIn("shirokuma-trino", workflow)

    def test_blocked_candidate_scans_both_workflow_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workflows = root / ".github/workflows"
            workflows.mkdir(parents=True)
            for name in ("publication.yml", "publication.yaml", "ignored.json"):
                (workflows / name).write_text("name: fixture\n", encoding="utf-8")

            self.assertEqual(
                ["publication.yaml", "publication.yml"],
                [path.name for path in _github_workflow_paths(root)],
            )

    def test_dependency_snapshot_contract_is_closed_world_and_source_bound(
        self,
    ) -> None:
        contract = self._trusted_build_contract()
        admission = self._admission()
        self.assertEqual(
            {
                "schema_version",
                "component",
                "version",
                "platform",
                "lifecycle",
                "authorization",
                "policy_files",
                "source",
                "toolchain",
                "dependency_resolution",
                "snapshot",
                "offline_rebuild",
                "publication",
                "downstream_gates",
            },
            set(contract),
        )
        self.assertEqual(1, contract["schema_version"])
        self.assertEqual("trino", contract["component"])
        self.assertEqual("483", contract["version"])
        self.assertEqual("linux/arm64", contract["platform"])

        required_binding = admission["source_authentication"]["required_binding"]
        source = contract["source"]
        self.assertEqual(
            {
                "repository": source["repository"],
                "release_tag": source["release_tag"],
                "commit_sha": source["commit_sha"],
                "tree_sha": source["tree_sha"],
            },
            required_binding,
        )
        self.assertEqual(
            "32d4f28e8311ea6f67edca209df59a0493d869fa",
            source["tag_object_sha"],
        )
        self.assertEqual(
            "provisionally_accepted_risk_not_authenticated",
            source["publisher_identity_status"],
        )
        self.assertEqual(
            {
                "repository",
                "release_tag",
                "tag_object_sha",
                "commit_sha",
                "tree_sha",
                "publisher_identity_status",
                "unmodified_source_required",
                "preimages",
                "forbidden_build_inputs",
            },
            set(source),
        )
        self.assertIs(source["unmodified_source_required"], True)
        self.assertEqual(8, len(source["preimages"]))
        self.assertEqual(
            {
                "mvnw": (
                    "cae96cef89ebea3531221f4ae17c23cf8edf67d00eae8306d4186ae1bbed4d02"
                ),
                ".mvn/wrapper/maven-wrapper.properties": (
                    "488e1b3f2e641779d4636abf9390845f901e64607261bc3c0b0bfe4fe96e6706"
                ),
                "pom.xml": (
                    "e1ba9a61315097e3a7133238c778ec161ac6097fe77a660fc5455a3e84568820"
                ),
                "core/trino-server/pom.xml": (
                    "663d8bc33313160b26df9c80d4f1e5a3d970700573a914fb22db3462ac0e06d2"
                ),
                ".mvn/extensions.xml": (
                    "5d034f440781f43f035303fe029c6a6a751f207a88e7b2f1fe57ff2029b325fb"
                ),
                ".mvn/maven.config": (
                    "d79502f51b88441a7fc7d30e99b9ff979d9cbd2f23f924d579bfa81763566a02"
                ),
                ".mvn/jvm.config": (
                    "46b658da8d190179af6f8c1328e44388f0ff2807507bf580e236e31580c73d48"
                ),
                ".mvn/settings.xml": (
                    "d06dc6c2d5e027397bd1188b8d8f72caa58a1789f5f642d8f48e832ef0c2ebe3"
                ),
            },
            {
                entry["path"]: entry["sha256"]
                for entry in source["preimages"]
            },
        )
        self.assertEqual(
            {
                "docker.io/trinodb/trino:483",
                TRINO_INDEX_REFERENCE,
                (
                    "https://github.com/trinodb/trino/releases/download/483/"
                    "trino-server-483.tar.gz"
                ),
            },
            set(source["forbidden_build_inputs"]),
        )

    def test_dependency_snapshot_contract_revalidates_the_exact_authorization(
        self,
    ) -> None:
        admission = self._admission()
        contract = self._trusted_build_contract()
        admitted = admission["provisional_source_authorization"]
        authorization = contract["authorization"]
        self.assertEqual(
            {
                "type",
                "decision_record",
                "approval_record",
                "issue",
                "approved_at",
                "expires_at",
                "maximum_duration_days",
                "automatic_renewal",
                "risk_owner",
                "implementation_author",
                "review",
                "validation_points",
                "scope",
                "accepted_risk",
                "stacked_vulnerability_exception_permitted",
                "expiry_action",
            },
            set(authorization),
        )
        self.assertEqual(
            admitted["authorization_type"], authorization["type"]
        )
        for key in (
            "decision_record",
            "approval_record",
            "issue",
            "approved_at",
            "expires_at",
            "maximum_duration_days",
            "automatic_renewal",
            "risk_owner",
            "implementation_author",
            "review",
            "scope",
            "stacked_vulnerability_exception_permitted",
            "expiry_action",
        ):
            with self.subTest(key=key):
                self.assertEqual(admitted[key], authorization[key])
        self.assertEqual(admitted["accepted_risk"], [authorization["accepted_risk"]])
        self.assertEqual(
            [
                "before_source_fetch",
                "before_source_execution",
                "before_dependency_resolution",
                "before_dependency_publication",
                "before_evidence_review",
            ],
            authorization["validation_points"],
        )
        self.assertEqual(
            [],
            _provisional_source_authorization_errors(
                admission, now=datetime.now(timezone.utc)
            ),
        )

    def test_dependency_snapshot_contract_closes_network_and_archive_inputs(
        self,
    ) -> None:
        contract = self._trusted_build_contract()
        builder = contract["toolchain"]["builder"]
        self.assertEqual(
            {"builder", "future_runtime_base"},
            set(contract["toolchain"]),
        )
        self.assertEqual(
            {
                "index",
                "arm64_manifest",
                "maven_version",
                "java_vendor",
                "java_major",
                "os",
                "architecture",
                "maven_executable",
                "maven_wrapper_permitted",
            },
            set(builder),
        )
        self.assertEqual(TRINO_BUILDER_INDEX, builder["index"])
        self.assertEqual(
            TRINO_BUILDER_ARM64_DIGEST, builder["arm64_manifest"]
        )
        self.assertEqual("3.9.16", builder["maven_version"])
        self.assertEqual("Eclipse Temurin", builder["java_vendor"])
        self.assertEqual(25, builder["java_major"])
        self.assertEqual("arm64", builder["architecture"])
        self.assertEqual("mvn", builder["maven_executable"])
        self.assertIs(builder["maven_wrapper_permitted"], False)

        runtime = contract["toolchain"]["future_runtime_base"]
        self.assertEqual(
            {"index", "arm64_manifest", "java_major", "os", "usage"},
            set(runtime),
        )
        self.assertEqual(TRINO_RUNTIME_INDEX, runtime["index"])
        self.assertEqual(
            TRINO_RUNTIME_ARM64_DIGEST, runtime["arm64_manifest"]
        )
        self.assertEqual("Alpine 3.24", runtime["os"])
        self.assertEqual(
            "downstream_image_only_not_authorized_by_this_contract",
            runtime["usage"],
        )

        resolution = contract["dependency_resolution"]
        self.assertEqual(
            {
                "network_policy",
                "repositories",
                "maven_local_repository",
                "repository_origin_capture_required",
                "transitive_dependency_repositories_ignored",
                "non_allowlisted_repository_mirror",
                "settings_policy",
                "transfer_audit",
                "reactor_outputs",
            },
            set(resolution),
        )
        self.assertEqual(
            [
                "https://repo.maven.apache.org/maven2/",
                "https://packages.confluent.io/maven/",
            ],
            resolution["repositories"],
        )
        self.assertEqual(
            "allowlisted_https_repositories_only",
            resolution["network_policy"],
        )
        self.assertEqual(
            "fresh_empty_workflow_owned_directory",
            resolution["maven_local_repository"],
        )
        self.assertIs(resolution["repository_origin_capture_required"], True)
        self.assertIs(
            resolution["transitive_dependency_repositories_ignored"],
            True,
        )
        self.assertEqual(
            {
                "id": "shirokuma-central-fallback",
                "mirror_of": "*,!central,!confluent",
                "url": "https://repo.maven.apache.org/maven2/",
            },
            resolution["non_allowlisted_repository_mirror"],
        )
        self.assertEqual(
            {
                "repository_owned_settings_only": True,
                "user_settings_permitted": False,
                "ambient_maven_home_permitted": False,
                "extensions_permitted": False,
                "mirrors_permitted": True,
                "mirror_escape_hatch_permitted": False,
                "mirror_policy": (
                    "exact_non_allowlisted_repository_ids_to_central_only"
                ),
                "proxies_permitted": False,
                "credentials_permitted": False,
            },
            resolution["settings_policy"],
        )
        self.assertEqual(
            {
                "complete_repository_origin_log_required": True,
                "unknown_repository_fails_closed": True,
                "redirect_outside_allowlist_fails_closed": True,
            },
            resolution["transfer_audit"],
        )
        self.assertEqual(
            {
                "repository_path_prefix": "io/trino/",
                "dependency_input_permitted": False,
                "rebuild_from_reviewed_source_required": True,
            },
            resolution["reactor_outputs"],
        )

        snapshot = contract["snapshot"]
        self.assertEqual(
            {
                "state",
                "packaging",
                "artifact_repository",
                "reference_policy",
                "mutable_tags_permitted",
                "visibility_bootstrap",
                "artifact_type",
                "descriptor_media_type",
                "archive_media_type",
                "archive_filename",
                "manifest_filename",
                "manifest",
                "archive",
                "forbidden_entries",
                "independent_reconstruction",
                "authentication",
                "evidence",
            },
            set(snapshot),
        )
        self.assertEqual("publication_pending_not_admitted", snapshot["state"])
        self.assertEqual(
            "ghcr.io/tommykammy/shirokuma-trino-maven-dependencies",
            snapshot["artifact_repository"],
        )
        self.assertEqual(
            "immutable_digest_only_after_publication",
            snapshot["reference_policy"],
        )
        self.assertIs(snapshot["mutable_tags_permitted"], False)
        self.assertEqual(
            "application/vnd.shirokuma.trino.maven-dependencies.v1",
            snapshot["artifact_type"],
        )
        self.assertEqual(
            "application/vnd.shirokuma.maven-dependency-manifest.v1+json",
            snapshot["descriptor_media_type"],
        )
        self.assertEqual(
            "application/vnd.shirokuma.maven-repository.v1.tar+gzip",
            snapshot["archive_media_type"],
        )
        self.assertEqual(
            "trino-maven-dependencies-483.tar.gz",
            snapshot["archive_filename"],
        )
        self.assertEqual(
            "maven-dependency-manifest.json",
            snapshot["manifest_filename"],
        )
        self.assertEqual(
            [
                "path",
                "size",
                "mode",
                "sha256",
                "repository_origin",
            ],
            snapshot["manifest"]["entry_fields"],
        )
        self.assertEqual(
            ["file_count", "total_bytes"],
            snapshot["manifest"]["aggregate_fields"],
        )
        self.assertIs(snapshot["manifest"]["closed_world"], True)
        self.assertEqual(
            ["_remote.repositories", "resolver-status.properties"],
            snapshot["manifest"]["excluded_resolver_metadata"],
        )
        self.assertEqual(250000, snapshot["manifest"]["maximum_file_count"])
        self.assertEqual(8589934592, snapshot["manifest"]["maximum_total_bytes"])
        self.assertIs(snapshot["manifest"]["canonical_paths_required"], True)
        self.assertIs(
            snapshot["manifest"]["sorted_unique_paths_required"], True
        )
        self.assertEqual(
            {
                "regular_files_only": True,
                "canonical_owner": "0:0",
                "canonical_mtime": 0,
                "canonical_order": "bytewise_path",
                "compression": "gzip",
                "gzip_header_timestamp": 0,
            },
            snapshot["archive"],
        )
        self.assertEqual(
            {
                "symlink",
                "hardlink",
                "special_file",
                "lock_file",
                "partial_download",
                "unknown_repository_origin",
                "duplicate_path",
                "io/trino/**",
            },
            set(snapshot["forbidden_entries"]),
        )
        self.assertEqual(
            {
                "required": True,
                "fresh_empty_repository_required": True,
                "same_allowlisted_repositories_required": True,
                "complete_manifest_equality_required": True,
            },
            snapshot["independent_reconstruction"],
        )
        self.assertEqual(
            {
                "cosign_keyless_signature_required": True,
                "rekor_transparency_log_required": True,
                "slsa_provenance_required": True,
                "anonymous_exact_digest_pull_required": True,
                "separate_evidence_review_required": True,
                "sigstore_identity": {
                    "oidc_issuer": "https://token.actions.githubusercontent.com",
                    "certificate_identity": (
                        "https://github.com/TommyKammy/Shirokuma/.github/"
                        "workflows/trino-maven-dependencies.yml@refs/heads/main"
                    ),
                    "repository": "TommyKammy/Shirokuma",
                    "ref": "refs/heads/main",
                    "workflow_path": (
                        ".github/workflows/trino-maven-dependencies.yml"
                    ),
                    "workflow_sha_environment": "GITHUB_WORKFLOW_SHA",
                    "source_sha_environment": "GITHUB_SHA",
                    "workflow_sha_must_equal_source_sha": True,
                },
                "provenance": {
                    "predicate_type": "https://slsa.dev/provenance/v1",
                    "subject_must_equal_artifact_digest": True,
                    "source_repository": (
                        "https://github.com/TommyKammy/Shirokuma"
                    ),
                    "source_ref": "refs/heads/main",
                    "source_sha_must_equal_publisher_commit": True,
                    "build_workflow_identity": (
                        "https://github.com/TommyKammy/Shirokuma/.github/"
                        "workflows/trino-maven-dependencies.yml@refs/heads/main"
                    ),
                    "build_workflow_sha_must_equal_source_sha": True,
                    "build_definition_must_bind_source_repository_ref_and_sha": True,
                    "generator_workflow_must_be_commit_pinned": True,
                    "trino_source_resolved_dependency": {
                        "claim_path": (
                            "predicate.buildDefinition.resolvedDependencies"
                        ),
                        "required_uri": (
                            "git+https://github.com/trinodb/trino@refs/tags/483"
                        ),
                        "required_digest": {
                            "gitTagObject": (
                                "32d4f28e8311ea6f67edca209df59a0493d869fa"
                            ),
                            "gitCommit": (
                                "50b0b50b75abd47f830b7805ee1b51716eb4065e"
                            ),
                            "gitTree": (
                                "3b5414292a614b12393bb4605ea2d4c588a5b8ee"
                            ),
                        },
                        "exactly_one_matching_descriptor_required": True,
                        "source_checkout_must_match_descriptor": True,
                    },
                },
            },
            snapshot["authentication"],
        )
        resolved_dependency = snapshot["authentication"]["provenance"][
            "trino_source_resolved_dependency"
        ]
        source = contract["source"]
        self.assertEqual(
            f"git+{source['repository']}@refs/tags/{source['release_tag']}",
            resolved_dependency["required_uri"],
        )
        self.assertEqual(
            {
                "gitTagObject": source["tag_object_sha"],
                "gitCommit": source["commit_sha"],
                "gitTree": source["tree_sha"],
            },
            resolved_dependency["required_digest"],
        )
        self.assertEqual(
            {
                "cyclonedx_sbom_required": True,
                "vulnerability_scan_required": True,
                "maximum_vulnerability_database_age_hours": 24,
                "maximum_high": 0,
                "maximum_critical": 0,
                "ignore_unfixed": False,
                "artifact_binding": {
                    "digest_source": "publisher_oras_push_digest_output",
                    "immutable_reference_required": True,
                    (
                        "cyclonedx_document_subject_must_equal_"
                        "artifact_digest"
                    ): True,
                    (
                        "cyclonedx_attestation_subject_must_equal_"
                        "artifact_digest"
                    ): True,
                    (
                        "vulnerability_scan_document_subject_must_equal_"
                        "artifact_digest"
                    ): True,
                    (
                        "vulnerability_scan_attestation_subject_must_equal_"
                        "artifact_digest"
                    ): True,
                    (
                        "binding_verification_required_before_evidence_review"
                    ): True,
                },
            },
            snapshot["evidence"],
        )
        self.assertEqual(
            contract["offline_rebuild"]["snapshot_input"]["reference_source"],
            snapshot["evidence"]["artifact_binding"]["digest_source"],
        )

    def test_dependency_snapshot_contract_requires_network_none_rebuild(
        self,
    ) -> None:
        contract = self._trusted_build_contract()
        rebuild = contract["offline_rebuild"]
        self.assertEqual(
            {
                "required",
                "independent_clean_verifier_required",
                "builder_index",
                "builder_arm64_manifest",
                "network",
                "fresh_builder_required",
                "fresh_source_checkout_required",
                "runner",
                "command",
                "maven_wrapper_permitted",
                "snapshot_input",
                "maven_repository",
                "expected_output",
                "retained_output_evidence",
            },
            set(rebuild),
        )
        self.assertIs(rebuild["required"], True)
        self.assertIs(rebuild["independent_clean_verifier_required"], True)
        self.assertEqual(TRINO_BUILDER_INDEX, rebuild["builder_index"])
        self.assertEqual(
            TRINO_BUILDER_ARM64_DIGEST,
            rebuild["builder_arm64_manifest"],
        )
        self.assertEqual("none", rebuild["network"])
        self.assertIs(rebuild["fresh_builder_required"], True)
        self.assertIs(rebuild["fresh_source_checkout_required"], True)
        self.assertEqual(
            {
                "required_platform": "linux/arm64",
                "required_runner_arch": "ARM64",
                "required_host_uname_machine": "aarch64",
                "required_container_architecture": "arm64",
                "native_execution_required": True,
                "emulation_permitted": False,
                "qemu_binfmt_handlers_permitted": False,
                "observations_retained_as_evidence": True,
                "verification_failure_action": (
                    "fail_closed_before_offline_rebuild"
                ),
            },
            rebuild["runner"],
        )
        self.assertEqual(
            contract["platform"],
            rebuild["runner"]["required_platform"],
        )
        self.assertEqual(
            contract["toolchain"]["builder"]["architecture"],
            rebuild["runner"]["required_container_architecture"],
        )
        self.assertEqual(
            (
                "mvn --offline --ignore-transitive-repositories "
                "-Dmaven.repo.local=/workspace/.m2/repository "
                "--file /workspace/pom.xml "
                "-pl '!:trino-docs' "
                "clean install -DskipTests"
            ),
            rebuild["command"],
        )
        self.assertIs(rebuild["maven_wrapper_permitted"], False)
        self.assertEqual(
            {
                "artifact_repository": (
                    "ghcr.io/tommykammy/shirokuma-trino-maven-dependencies"
                ),
                "reference_source": "publisher_oras_push_digest_output",
                "required_reference_format": (
                    "ghcr.io/tommykammy/shirokuma-trino-maven-dependencies"
                    "@sha256:<64-lowercase-hex>"
                ),
                "same_run_digest_required": True,
                "future_evidence_must_pin_exact_reference": True,
                "anonymous_pull_required": True,
                "extraction_root": "/workspace/dependency-snapshot",
                "repository_root_after_extraction": (
                    "/workspace/dependency-snapshot/repository"
                ),
                "manifest_path": (
                    "/workspace/dependency-snapshot/"
                    "maven-dependency-manifest.json"
                ),
                "archive_path": (
                    "/workspace/dependency-snapshot/"
                    "trino-maven-dependencies-483.tar.gz"
                ),
                "manifest_equality_required_before_build": True,
            },
            rebuild["snapshot_input"],
        )
        self.assertEqual(
            {
                "path": "/workspace/.m2/repository",
                "initialization": (
                    "copy_verified_snapshot_repository_to_empty_path_"
                    "before_network_none_builder_start"
                ),
                "sole_dependency_repository": True,
                "maven_args": (
                    "-Dmaven.repo.local=/workspace/.m2/repository"
                ),
                "ambient_cache_mounts_permitted": False,
                "ambient_home_permitted": False,
                "prebuild_manifest_must_equal_snapshot": True,
                (
                    "postbuild_io_trino_entries_are_verifier_outputs_"
                    "not_dependency_inputs"
                ): True,
            },
            rebuild["maven_repository"],
        )
        self.assertIn(
            rebuild["maven_repository"]["maven_args"],
            rebuild["command"],
        )
        self.assertEqual(
            "core/trino-server/target/trino-server-483.tar.gz",
            rebuild["expected_output"],
        )
        self.assertEqual(
            ["sha256", "size", "reproducible_build_comparison"],
            rebuild["retained_output_evidence"],
        )

    def test_dependency_snapshot_contract_does_not_authorize_publication(
        self,
    ) -> None:
        contract = self._trusted_build_contract()
        lifecycle = contract["lifecycle"]
        self.assertEqual(
            {
                "state": "dependency_snapshot_publication_pending",
                "contract_only": False,
                "dependency_artifact_present": False,
                "publication_workflow_permitted": True,
                "image_publication_permitted": False,
                "resident_admission_permitted": False,
                "runtime_reconciliation_permitted": False,
            },
            lifecycle,
        )
        self.assertEqual(
            {
                "permitted": True,
                "workflow_present": True,
                "workflow": (
                    ".github/workflows/trino-maven-dependencies.yml"
                ),
                "allowed_ref": "refs/heads/main",
                "artifact_role": "review_pending_dependency_evidence",
                "retire_in_evidence_review_pr": True,
                "separate_evidence_only_pr_required": True,
                "image_publisher_permitted_before_evidence_review": False,
                "anonymous_exact_digest_pull_required": True,
                "pull_request_behavior": "static_read_only_contract_validation",
                "publication_event": "push",
                "runner": "ubuntu-24.04-arm",
                "run_scoped_tag": "run-<github.run_id>-<github.run_attempt>",
                "retained_evidence": [
                    "closed Maven dependency manifest and deterministic archive digest",
                    "independent reconstruction equality",
                    "two network-none native arm64 build output comparisons",
                    "CycloneDX dependency SBOM",
                    "fresh High=0/Critical=0 Trivy result and database metadata",
                    "Cosign signature and Rekor bundle",
                    "SLSA v1 provenance with predicate.buildDefinition.resolvedDependencies",
                    "anonymous exact-digest retrieval proof",
                ],
            },
            contract["publication"],
        )
        self.assertEqual(
            {
                "dependency_artifact_published": False,
                "dependency_evidence_admitted": False,
                "network_none_source_build_verified": False,
                "runtime_image_published": False,
                "native_arm64_smoke_verified": False,
                "high_zero_critical_zero_scan_verified": False,
                "cyclonedx_sbom_retained": False,
                "cosign_rekor_signature_verified": False,
                "slsa_provenance_verified": False,
                "anonymous_exact_digest_pull_verified": False,
                "resident_image_admitted": False,
                "flux_runtime_reconciled": False,
            },
            contract["downstream_gates"],
        )
        self.assertTrue((ROOT / contract["publication"]["workflow"]).is_file())

    def test_next_action_is_dependency_snapshot_publication_pending(self) -> None:
        next_action = self._admission()["next_action"]
        self.assertEqual(
            {
                "mode",
                "decision_record_required",
                "decision_record",
                "phase",
                "requirements",
            },
            set(next_action),
        )
        self.assertEqual(
            "repository-owned-reproducible-source-build",
            next_action["mode"],
        )
        self.assertIs(next_action["decision_record_required"], False)
        self.assertEqual(
            "docs/design/07_ADR/"
            "ADR-0023_Allow_time_boxed_Trino_483_source_identity_exception_for_local_PoC.md",
            next_action["decision_record"],
        )
        self.assertEqual(
            "dependency_snapshot_publication_pending",
            next_action["phase"],
        )
        self.assertEqual(
            [
                "active and unexpired provisional source authorization bound to the "
                "exact source repository, tag, commit, and tree",
                "authenticated closed dependency snapshot",
                "network-none reproducible linux/arm64 build",
                "digest-pinned builder and runtime bases",
                "native linux/arm64 runtime smoke",
                "Cosign signature and transparency-log evidence",
                "SLSA provenance bound to the source revision",
                "retained SBOM and fresh vulnerability scan",
                "anonymous exact-digest retrieval before separate admission review",
            ],
            next_action["requirements"],
        )

    def test_provisional_source_decision_authorizes_only_the_next_boundary(self) -> None:
        decision = TRINO_PROVISIONAL_SOURCE_ADR.read_text(encoding="utf-8")
        normalized_decision = " ".join(decision.split())
        front_matter = decision.split("---", 2)[1]
        self.assertIn('\ndoc_id: "ADR-0023"\n', front_matter)
        self.assertIn("\nstatus: accepted\n", front_matter)
        for required in (
            "2026-07-22T22:43:36Z",
            "2026-08-21T22:43:36Z",
            "maximum duration is 30 days",
            "automatic renewal is forbidden",
            "mac-studio-solo/local-lite",
            "synthetic or PoC data",
            "no public Service or Ingress",
            "owner/reviewer separation",
            "dependency_snapshot_publication_pending",
            "High=0/Critical=0",
            "Do not stack this authorization with an ADR-0019 vulnerability exception",
            "Fail closed at expiry",
            "upstream Trino OCI image and server archive",
            "This decision supersedes only ADR-0022",
        ):
            with self.subTest(required=required):
                self.assertIn(" ".join(required.split()), normalized_decision)

        context = json.loads(
            (ROOT / "docs/design/context-manifest.json").read_text(encoding="utf-8")
        )
        self.assertIn(
            {
                "source": (
                    "07_ADR/"
                    "ADR-0023_Allow_time_boxed_Trino_483_source_identity_exception_for_local_PoC.md"
                ),
                "target": (
                    "docs/design/07_ADR/"
                    "ADR-0023_Allow_time_boxed_Trino_483_source_identity_exception_for_local_PoC.md"
                ),
            },
            context["documents"],
        )

    def test_source_build_decision_closes_only_the_decision_boundary(self) -> None:
        contract = self._trusted_build_contract()
        decision = TRINO_SOURCE_BUILD_ADR.read_text(encoding="utf-8")
        normalized_decision = " ".join(decision.split())
        front_matter = decision.split("---", 2)[1]
        self.assertIn('\ndoc_id: "ADR-0022"\n', front_matter)
        self.assertIn("\nstatus: accepted\n", front_matter)
        for required in (
            "50b0b50b75abd47f830b7805ee1b51716eb4065e",
            "3b5414292a614b12393bb4605ea2d4c588a5b8ee",
            "cae96cef89ebea3531221f4ae17c23cf8edf67d00eae8306d4186ae1bbed4d02",
            "488e1b3f2e641779d4636abf9390845f901e64607261bc3c0b0bfe4fe96e6706",
            "e1ba9a61315097e3a7133238c778ec161ac6097fe77a660fc5455a3e84568820",
            "663d8bc33313160b26df9c80d4f1e5a3d970700573a914fb22db3462ac0e06d2",
            "Maven 3.9.16",
            "Eclipse Temurin 25",
            TRINO_BUILDER_INDEX,
            TRINO_BUILDER_ARM64_DIGEST,
            TRINO_RUNTIME_INDEX,
            TRINO_RUNTIME_ARM64_DIGEST,
            "https://repo.maven.apache.org/maven2/",
            "https://packages.confluent.io/maven/",
            "unchecked wrapper download path is forbidden",
            "`io/trino/**` artifacts fail closed",
            "core/trino-server/target/trino-server-483.tar.gz",
            "High=0/Critical=0",
            "main-only",
            "separate evidence-only PR",
            "predicate.buildDefinition.resolvedDependencies",
            "`RUNNER_ARCH=ARM64`",
            "SBOM and vulnerability-scan documents and their attestations",
            "A SHA, HTTPS transport, GitHub account attribution, release page, or "
            "Shirokuma re-signature alone is insufficient.",
            "A self-selected or merely embedded signing key is not a trust root.",
            "The next review boundary is source-authentication evidence",
        ):
            with self.subTest(required=required):
                self.assertIn(" ".join(required.split()), normalized_decision)
        self.assertIn(
            " ".join(contract["offline_rebuild"]["command"].split()),
            normalized_decision,
        )
        self.assertIn(
            "Reject the upstream Trino 483 OCI image and the upstream server tarball "
            "as resident or repository-build inputs.",
            normalized_decision,
        )
        self.assertIn(
            "Do not add a Trino workflow, dependency artifact, Containerfile, "
            "resident",
            normalized_decision,
        )

        context = json.loads(
            (ROOT / "docs/design/context-manifest.json").read_text(encoding="utf-8")
        )
        self.assertIn(
            {
                "source": "07_ADR/ADR-0022_Adopt_Trino_483_repository_source_build.md",
                "target": (
                    "docs/design/07_ADR/"
                    "ADR-0022_Adopt_Trino_483_repository_source_build.md"
                ),
            },
            context["documents"],
        )
        self.assertIn(
            {
                "source": (
                    "10_Research/107_Trino_476_Signed_Distribution_Feasibility.md"
                ),
                "target": (
                    "docs/design/10_Research/"
                    "107_Trino_476_Signed_Distribution_Feasibility.md"
                ),
            },
            context["documents"],
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
