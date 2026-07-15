#!/usr/bin/env python3
"""Fail closed unless the generated Flux bootstrap resolves to admitted images."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "opentofu/dev/bootstrap-images.json"
INVENTORY = ROOT / "bootstrap/flux/v2.9.2/components.json"
LEDGER = ROOT / "security/resident-images.json"
FLUX_SYSTEM = ROOT / "deploy/gitops/clusters/local-lite/flux-system"
CUSTOMIZATION = FLUX_SYSTEM / "kustomization.yaml"
GOTK_COMPONENTS = FLUX_SYSTEM / "gotk-components.yaml"
GOTK_SYNC = FLUX_SYSTEM / "gotk-sync.yaml"
GOTK_COMPONENTS_REPOSITORY_PATH = (
    "deploy/gitops/clusters/local-lite/flux-system/gotk-components.yaml"
)
EXPECTED_FLUX_VERSION = "v2.9.2"
STANDARD_CONTROLLERS = (
    "source-controller",
    "kustomize-controller",
    "helm-controller",
    "notification-controller",
)
EXPECTED_SYNC_URL = "ssh://git@github.com/TommyKammy/Shirokuma"
EXPECTED_SYNC_PATH = "./deploy/gitops/clusters/local-lite"
CANONICAL_CUSTOMIZATION_SHA256 = (
    "6ae842182f60f07621c519666238612bcdc7f5a235adcc5fc3b9998eea53534a"
)
CANONICAL_COMPONENTS_SHA256 = (
    "ed307189fd1f9e49819a50843bb6f3c9257fe6d4d8359d1950b38207c26c3854"
)
CANONICAL_SYNC_SHA256 = (
    "b1083278d11f3512e06e4fcb7d5c048ad25e8b365f498721b4d8cad4365f1a47"
)
DIGEST_REFERENCE = re.compile(
    r"^(?P<repository>[^:@\s]+(?:/[^:@\s]+)+)@(?P<digest>sha256:[0-9a-f]{64})$"
)
TAG_WITH_DIGEST = re.compile(
    r"^(?P<version>[^@\s]+)@(?P<digest>sha256:[0-9a-f]{64})$"
)
TAGGED_REFERENCE = re.compile(
    r"^(?P<repository>[^:@\s]+(?:/[^:@\s]+)+):(?P<version>[^:@\s]+)$"
)
YAML_IMAGE_FIELD = re.compile(
    r'''^(?P<indent> *)(?:-\s*)?(?:image|"image"|'image')\s*:\s*(?P<value>.*?)\s*$'''
)
YAML_INLINE_IMAGE_FIELD = re.compile(r"(?:\{|\[|,)\s*[\"']?image[\"']?\s*:")
RENDERED_IMAGE_FIELD = re.compile(
    r"^\s*(?:-\s+)?image:\s*(?P<value>\S+)\s*$"
)
PATCH_BLOCK = re.compile(
    r"^  - patch: \|\n(?P<patch>(?: {6}.*\n)+)"
    r" {4}target:\n(?P<target>(?: {6}.*(?:\n|$))+)",
    re.MULTILINE,
)


class AdmissionError(ValueError):
    """The generated Flux bootstrap cannot be interpreted or admitted safely."""


def _display_path(path: Path) -> Path:
    try:
        return path.relative_to(ROOT)
    except ValueError:
        return path


def _reject_symlink_path(path: Path) -> None:
    if path.is_symlink():
        raise AdmissionError(f"refusing to read symbolic link {_display_path(path)}")
    absolute = path.absolute()
    root = ROOT.absolute()
    try:
        relative = absolute.relative_to(root)
    except ValueError:
        return
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise AdmissionError(
                f"refusing to read {_display_path(path)} through symbolic link ancestor "
                f"{_display_path(current)}"
            )


def load_text(path: Path, label: str) -> str:
    _reject_symlink_path(path)
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise AdmissionError(f"cannot read {label} {_display_path(path)}: {error}") from error
    if "\t" in content:
        raise AdmissionError(f"{label} {_display_path(path)} contains unsupported tab indentation")
    return content


def load_canonical_text(path: Path, label: str, expected_sha256: str) -> str:
    """Read a generated bootstrap input only when its exact reviewed bytes match."""

    _reject_symlink_path(path)
    try:
        content = path.read_bytes()
    except OSError as error:
        raise AdmissionError(f"cannot read {label} {_display_path(path)}: {error}") from error
    actual_sha256 = hashlib.sha256(content).hexdigest()
    if actual_sha256 != expected_sha256:
        raise AdmissionError(
            f"{label} {_display_path(path)} does not match its canonical byte SHA-256"
        )
    try:
        decoded = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise AdmissionError(f"cannot decode {label} {_display_path(path)}: {error}") from error
    if "\t" in decoded:
        raise AdmissionError(f"{label} {_display_path(path)} contains unsupported tab indentation")
    return decoded


def load_json(path: Path) -> object:
    try:
        return json.loads(load_text(path, "trusted JSON input"))
    except json.JSONDecodeError as error:
        raise AdmissionError(
            f"cannot read trusted JSON input {_display_path(path)}: {error}"
        ) from error


def _single_match(pattern: str, content: str, label: str) -> str:
    matches = re.findall(pattern, content, re.MULTILINE)
    if len(matches) != 1:
        raise AdmissionError(f"{label} must occur exactly once")
    return matches[0]


def _split_yaml_documents(content: str, label: str) -> list[list[str]]:
    documents: list[list[str]] = []
    current: list[str] | None = None
    preamble: list[str] = []
    for line in content.splitlines():
        if line == "---":
            if current is not None:
                if not any(item.strip() and not item.lstrip().startswith("#") for item in current):
                    raise AdmissionError(f"{label} contains an empty YAML document")
                documents.append(current)
            elif any(item.strip() and not item.lstrip().startswith("#") for item in preamble):
                raise AdmissionError(f"{label} has unsupported content before its first document")
            current = []
            continue
        if line == "...":
            raise AdmissionError(f"{label} uses an unsupported YAML document terminator")
        if current is None:
            preamble.append(line)
        else:
            current.append(line)
    if current is None:
        raise AdmissionError(f"{label} does not contain an explicit YAML document")
    if not any(item.strip() and not item.lstrip().startswith("#") for item in current):
        raise AdmissionError(f"{label} contains an empty YAML document")
    documents.append(current)
    return documents


def _top_level_scalar(lines: list[str], key: str, label: str) -> str:
    return _single_match(
        rf"^{re.escape(key)}:\s*([^\s#][^#]*?)\s*$",
        "\n".join(lines),
        f"{label} top-level {key}",
    )


def _metadata_scalar(lines: list[str], key: str, label: str) -> str:
    content = "\n".join(lines)
    metadata = re.search(
        r"^metadata:[ ]*$\n(?P<body>(?:^(?:  .*|[ ]*|#.*)$\n?)*)",
        content,
        re.MULTILINE,
    )
    if metadata is None:
        raise AdmissionError(f"{label} requires a top-level metadata mapping")
    return _single_match(
        rf"^  {re.escape(key)}:\s*([^\s#][^#]*?)\s*$",
        metadata.group("body"),
        f"{label} metadata.{key}",
    )


def _mapping_document(lines: list[str], label: str) -> dict[tuple[str, ...], str]:
    """Parse the mapping-only YAML subset emitted by `flux bootstrap` for gotk-sync."""

    result: dict[tuple[str, ...], str] = {}
    stack: list[str] = []
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("-") or any(
            token in line for token in ("{", "}", "[", "]", "&", "*")
        ):
            raise AdmissionError(
                f"{label}:{line_number} uses unsupported YAML structure"
            )
        match = re.fullmatch(r"(?P<indent> *)(?P<key>[A-Za-z0-9_.-]+):(?:\s*(?P<value>.*))?", line)
        if match is None or len(match.group("indent")) % 2:
            raise AdmissionError(f"{label}:{line_number} is not canonical mapping YAML")
        depth = len(match.group("indent")) // 2
        if depth > len(stack):
            raise AdmissionError(f"{label}:{line_number} skips a mapping level")
        stack = stack[:depth]
        key = match.group("key")
        path = tuple([*stack, key])
        if path in result:
            raise AdmissionError(f"{label} contains duplicate mapping key {'.'.join(path)}")
        value = (match.group("value") or "").strip()
        if value.startswith(("|", ">", "!")) or " #" in value:
            raise AdmissionError(f"{label}:{line_number} uses unsupported scalar syntax")
        result[path] = value
        stack.append(key)
    return result


def validate_gotk_sync(path: Path) -> None:
    content = load_canonical_text(
        path, "Flux sync manifest", CANONICAL_SYNC_SHA256
    )
    documents = _split_yaml_documents(content, "Flux sync manifest")
    if len(documents) != 2:
        raise AdmissionError("Flux sync manifest must contain exactly two resources")
    by_kind: dict[str, dict[tuple[str, ...], str]] = {}
    for lines in documents:
        kind = _top_level_scalar(lines, "kind", "Flux sync resource")
        if kind in by_kind:
            raise AdmissionError(f"Flux sync manifest contains duplicate {kind} resources")
        by_kind[kind] = _mapping_document(lines, f"Flux sync {kind}")

    expected: dict[str, dict[tuple[str, ...], str]] = {
        "GitRepository": {
            ("apiVersion",): "source.toolkit.fluxcd.io/v1",
            ("kind",): "GitRepository",
            ("metadata",): "",
            ("metadata", "name"): "flux-system",
            ("metadata", "namespace"): "flux-system",
            ("spec",): "",
            ("spec", "interval"): "1m0s",
            ("spec", "ref"): "",
            ("spec", "ref", "branch"): "main",
            ("spec", "secretRef"): "",
            ("spec", "secretRef", "name"): "flux-system",
            ("spec", "url"): EXPECTED_SYNC_URL,
        },
        "Kustomization": {
            ("apiVersion",): "kustomize.toolkit.fluxcd.io/v1",
            ("kind",): "Kustomization",
            ("metadata",): "",
            ("metadata", "name"): "flux-system",
            ("metadata", "namespace"): "flux-system",
            ("spec",): "",
            ("spec", "interval"): "10m0s",
            ("spec", "path"): EXPECTED_SYNC_PATH,
            ("spec", "prune"): "true",
            ("spec", "sourceRef"): "",
            ("spec", "sourceRef", "kind"): "GitRepository",
            ("spec", "sourceRef", "name"): "flux-system",
        },
    }
    if set(by_kind) != set(expected):
        raise AdmissionError(
            "Flux sync manifest must contain exactly GitRepository and Kustomization"
        )
    for kind, required in expected.items():
        actual = by_kind[kind]
        if actual != required:
            missing = sorted(".".join(path) for path in set(required) - set(actual))
            extra = sorted(".".join(path) for path in set(actual) - set(required))
            drift = sorted(
                ".".join(path)
                for path in set(actual) & set(required)
                if actual[path] != required[path]
            )
            details = []
            if missing:
                details.append("missing=" + ",".join(missing))
            if extra:
                details.append("unexpected=" + ",".join(extra))
            if drift:
                details.append("drift=" + ",".join(drift))
            raise AdmissionError(f"Flux sync {kind} is not canonical: {' '.join(details)}")


def load_customized_images(path: Path) -> dict[str, str]:
    content = load_canonical_text(
        path,
        "Flux bootstrap customization",
        CANONICAL_CUSTOMIZATION_SHA256,
    )
    top_level_keys = re.findall(r"^([A-Za-z][A-Za-z0-9_-]*):", content, re.MULTILINE)
    if top_level_keys != ["apiVersion", "kind", "resources", "patches"]:
        raise AdmissionError(
            "Flux customization must contain only canonical apiVersion, kind, "
            "resources, and patches fields"
        )
    if _top_level_scalar(content.splitlines(), "apiVersion", "Flux customization") != (
        "kustomize.config.k8s.io/v1beta1"
    ):
        raise AdmissionError("Flux customization apiVersion is not canonical")
    if _top_level_scalar(content.splitlines(), "kind", "Flux customization") != "Kustomization":
        raise AdmissionError("Flux customization kind is not Kustomization")
    resources_match = re.search(
        r"^resources:\s*$\n(?P<body>(?:^  - \S+\s*$\n?)+)", content, re.MULTILINE
    )
    if resources_match is None:
        raise AdmissionError("Flux customization requires a canonical resources list")
    resources = re.findall(r"^  - (\S+)\s*$", resources_match.group("body"), re.MULTILINE)
    if resources != ["gotk-components.yaml", "gotk-sync.yaml"]:
        raise AdmissionError(
            "Flux customization resources must be exactly gotk-components.yaml and gotk-sync.yaml"
        )

    patch_markers = len(re.findall(r"^  - patch:\s*\|\s*$", content, re.MULTILINE))
    matches = list(PATCH_BLOCK.finditer(content))
    if patch_markers != len(matches):
        raise AdmissionError("Flux customization contains an uninterpretable image patch")

    images: dict[str, str] = {}
    for match in matches:
        patch = match.group("patch")
        target = match.group("target")
        operation = _single_match(
            r"^\s*- op:\s*(\S+)\s*$", patch, "Flux image patch operation"
        )
        image_path = _single_match(
            r"^\s*path:\s*(\S+)\s*$", patch, "Flux image patch path"
        )
        value = _single_match(
            r"^\s*value:\s*(\S+)\s*$", patch, "Flux image patch value"
        )
        kind = _single_match(
            r"^\s*kind:\s*(\S+)\s*$", target, "Flux image patch target kind"
        )
        name = _single_match(
            r"^\s*name:\s*(\S+)\s*$", target, "Flux image patch target name"
        )
        patch_lines = [line.strip() for line in patch.splitlines() if line.strip()]
        target_lines = [line.strip() for line in target.splitlines() if line.strip()]
        if patch_lines != [
            f"- op: {operation}",
            f"path: {image_path}",
            f"value: {value}",
        ] or target_lines != [f"kind: {kind}", f"name: {name}"]:
            raise AdmissionError("Flux image patch has unsupported extra or reordered fields")
        if operation != "replace" or image_path != "/spec/template/spec/containers/0/image":
            raise AdmissionError(f"Flux image patch for {name} does not replace containers/0/image")
        if kind != "Deployment":
            raise AdmissionError(f"Flux image patch for {name} does not target a Deployment")
        if name in images:
            raise AdmissionError(f"duplicate Flux image customization for {name}")
        images[name] = value
    return images


def load_generated_controller_images(path: Path) -> dict[str, str]:
    content = load_canonical_text(
        path,
        "generated Flux components manifest",
        CANONICAL_COMPONENTS_SHA256,
    )
    if re.search(r"(?:^|[\s\[{,])(?:&|\*)[A-Za-z0-9_-]+|^\s*<<\s*:", content, re.MULTILINE):
        raise AdmissionError(
            "generated Flux components use unsupported YAML anchors, aliases, or merge keys"
        )
    if content.count(f"# Flux Version: {EXPECTED_FLUX_VERSION}") != 1:
        raise AdmissionError(
            f"generated Flux components must declare exactly Flux Version {EXPECTED_FLUX_VERSION}"
        )
    components_header = "# Components: " + ",".join(STANDARD_CONTROLLERS)
    if content.count(components_header) != 1:
        raise AdmissionError(
            "generated Flux components must declare exactly the standard controllers"
        )

    documents = _split_yaml_documents(content, "generated Flux components manifest")
    deployments: dict[str, str] = {}
    total_images = 0
    for document_number, lines in enumerate(documents, start=1):
        label = f"generated Flux document {document_number}"
        kind = _top_level_scalar(lines, "kind", label)
        image_fields: list[tuple[int, re.Match[str]]] = []
        for line_number, line in enumerate(lines, start=1):
            match = YAML_IMAGE_FIELD.fullmatch(line)
            if match is not None:
                image_fields.append((line_number, match))
            elif YAML_INLINE_IMAGE_FIELD.search(line):
                raise AdmissionError(
                    f"{label}:{line_number} contains unsupported inline image YAML"
                )
        total_images += len(image_fields)
        if kind != "Deployment":
            if image_fields:
                raise AdmissionError(f"{label} contains an image outside a controller Deployment")
            continue

        if _top_level_scalar(lines, "apiVersion", label) != "apps/v1":
            raise AdmissionError(f"{label} must use apps/v1")
        name = _metadata_scalar(lines, "name", label)
        namespace = _metadata_scalar(lines, "namespace", label)
        if namespace != "flux-system":
            raise AdmissionError(f"generated Flux Deployment {name} must use flux-system")
        if name in deployments:
            raise AdmissionError(f"generated Flux components contain duplicate Deployment {name}")
        if name not in STANDARD_CONTROLLERS:
            raise AdmissionError(f"generated Flux components contain unexpected Deployment {name}")

        document = "\n".join(lines)
        versions = re.findall(
            r"^(?: {4}| {8})app\.kubernetes\.io/version:\s*(\S+)\s*$",
            document,
            re.MULTILINE,
        )
        if versions != [EXPECTED_FLUX_VERSION, EXPECTED_FLUX_VERSION]:
            raise AdmissionError(
                f"generated Flux Deployment {name} has non-canonical Flux version labels"
            )
        container_keys = re.findall(
            r'''^\s*(?:containers|"containers"|'containers')\s*:.*$''',
            document,
            re.MULTILINE,
        )
        init_container_keys = re.findall(
            r'''^\s*(?:initContainers|"initContainers"|'initContainers')\s*:.*$''',
            document,
            re.MULTILINE,
        )
        if init_container_keys:
            raise AdmissionError(f"generated Flux Deployment {name} must not use initContainers")
        if container_keys != ["      containers:"]:
            raise AdmissionError(
                f"generated Flux Deployment {name} requires one canonical containers list"
            )
        container_start = lines.index("      containers:")
        container_end = len(lines)
        for index in range(container_start + 1, len(lines)):
            line = lines[index]
            if line.strip() and not line.lstrip().startswith("#"):
                indent = len(line) - len(line.lstrip(" "))
                if indent <= 6 and not line.startswith("      - "):
                    container_end = index
                    break
        container_items = [
            line
            for line in lines[container_start + 1 : container_end]
            if line.startswith("      - ")
        ]
        if len(container_items) != 1:
            raise AdmissionError(
                f"generated Flux Deployment {name} must contain exactly one container"
            )
        if len(image_fields) != 1:
            raise AdmissionError(
                f"generated Flux Deployment {name} must contain exactly one image"
            )
        image_line_number, image_match = image_fields[0]
        if image_line_number - 1 <= container_start or image_line_number - 1 >= container_end:
            raise AdmissionError(f"generated Flux Deployment {name} image is outside containers")
        if image_match.group("indent") != "        ":
            raise AdmissionError(
                f"generated Flux Deployment {name} image indentation is not canonical"
            )
        image = image_match.group("value").strip()
        if not TAGGED_REFERENCE.fullmatch(image):
            raise AdmissionError(
                f"generated Flux Deployment {name} requires an exact repository:version image"
            )
        container_names = re.findall(
            r"^        name:\s*(\S+)\s*$",
            "\n".join(lines[container_start + 1 : container_end]),
            re.MULTILINE,
        )
        if container_names != ["manager"]:
            raise AdmissionError(
                f"generated Flux Deployment {name} must contain only the manager container"
            )
        deployments[name] = image

    if set(deployments) != set(STANDARD_CONTROLLERS):
        missing = sorted(set(STANDARD_CONTROLLERS) - set(deployments))
        raise AdmissionError(
            "generated Flux components are missing standard controllers: " + ", ".join(missing)
        )
    if total_images != len(STANDARD_CONTROLLERS):
        raise AdmissionError("generated Flux components contain unexpected or duplicate images")
    return deployments


def _validated_candidates(value: object) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict):
        raise AdmissionError("candidate root must be an object")
    if set(value) != set(STANDARD_CONTROLLERS):
        raise AdmissionError("candidate set must be exactly the four standard Flux controllers")
    result: dict[str, dict[str, str]] = {}
    for component in STANDARD_CONTROLLERS:
        candidate = value.get(component)
        if not isinstance(candidate, dict):
            raise AdmissionError(f"{component}: candidate must be an object")
        version = candidate.get("version")
        reference = candidate.get("reference")
        repository = candidate.get("repository")
        tag = candidate.get("tag")
        if not all(
            isinstance(item, str) and item
            for item in (version, reference, repository, tag)
        ):
            raise AdmissionError(
                f"{component}: candidate requires version, repository, tag, and reference"
            )
        reference_match = DIGEST_REFERENCE.fullmatch(reference)
        tag_match = TAG_WITH_DIGEST.fullmatch(tag)
        if reference_match is None:
            raise AdmissionError(f"{component}: exact repository@sha256 reference is required")
        if "@" in repository or ":" in repository.rsplit("/", 1)[-1]:
            raise AdmissionError(f"{component}: repository must be untagged")
        if tag_match is None:
            raise AdmissionError(f"{component}: tag must be version@sha256")
        if reference_match.group("repository") != repository:
            raise AdmissionError(f"{component}: reference repository does not match repository")
        if tag_match.group("version") != version:
            raise AdmissionError(f"{component}: tag version does not match version")
        if tag_match.group("digest") != reference_match.group("digest"):
            raise AdmissionError(f"{component}: tag digest does not match reference")
        result[component] = {
            "version": version,
            "repository": repository,
            "tag": tag,
            "reference": reference,
        }
    return result


def _validate_inventory(value: object, candidates: dict[str, dict[str, str]]) -> None:
    if not isinstance(value, dict) or value.get("flux_version") != EXPECTED_FLUX_VERSION:
        raise AdmissionError(f"Flux inventory must declare {EXPECTED_FLUX_VERSION}")
    components = value.get("components")
    if not isinstance(components, list) or len(components) != len(STANDARD_CONTROLLERS):
        raise AdmissionError("Flux inventory must contain exactly four components")
    by_name: dict[str, dict[str, object]] = {}
    for item in components:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise AdmissionError("Flux inventory component is malformed")
        name = item["name"]
        if name in by_name:
            raise AdmissionError(f"Flux inventory contains duplicate component {name}")
        by_name[name] = item
    if set(by_name) != set(STANDARD_CONTROLLERS):
        raise AdmissionError("Flux inventory component set is not the standard four controllers")
    for name, candidate in candidates.items():
        item = by_name[name]
        if (
            item.get("version") != candidate["version"]
            or item.get("reference") != candidate["reference"]
        ):
            raise AdmissionError(f"Flux inventory for {name} does not match its candidate")


def _validate_ledger(value: object, candidates: dict[str, dict[str, str]]) -> None:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise AdmissionError("resident image ledger requires schema_version 1")
    images = value.get("images")
    if not isinstance(images, list):
        raise AdmissionError("resident image ledger is malformed")
    by_component: dict[str, list[dict[str, object]]] = {
        component: [] for component in STANDARD_CONTROLLERS
    }
    for image in images:
        if not isinstance(image, dict):
            raise AdmissionError("resident image ledger contains a malformed image entry")
        component = image.get("component")
        if component in by_component:
            by_component[component].append(image)
    for component, candidate in candidates.items():
        matches = by_component[component]
        if len(matches) != 1:
            raise AdmissionError(
                f"{component}: resident image ledger requires exactly one component entry"
            )
        ledger_image = matches[0]
        if ledger_image.get("version") != candidate["version"]:
            raise AdmissionError(f"{component}: ledger version does not match candidate")
        if ledger_image.get("reference") != candidate["reference"]:
            raise AdmissionError(f"{component}: deployed image is not admitted by the ledger")


def render_customized_images(
    customization_path: Path,
    components_path: Path,
    sync_path: Path,
) -> list[str]:
    """Render the pinned bootstrap and enumerate every normalized image field."""

    paths = (customization_path, components_path, sync_path)
    if [path.name for path in paths] != [
        "kustomization.yaml",
        "gotk-components.yaml",
        "gotk-sync.yaml",
    ] or len({path.absolute().parent for path in paths}) != 1:
        raise AdmissionError(
            "Flux render inputs must be canonical sibling bootstrap paths"
        )

    try:
        result = subprocess.run(
            ["kubectl", "kustomize", str(customization_path.absolute().parent)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            check=False,
            timeout=60,
        )
    except FileNotFoundError as error:
        raise AdmissionError("Flux render executable is unavailable: kubectl") from error
    except subprocess.TimeoutExpired as error:
        raise AdmissionError("Flux render timed out after 60 seconds") from error
    except (OSError, UnicodeError) as error:
        raise AdmissionError(f"Flux render could not be executed safely: {error}") from error
    if result.returncode != 0:
        raise AdmissionError(
            f"Flux render failed closed with exit status {result.returncode}"
        )
    if not result.stdout.strip():
        raise AdmissionError("Flux render returned no resources")

    images: list[str] = []
    for line_number, line in enumerate(result.stdout.splitlines(), start=1):
        if not re.match(r"^\s*(?:-\s+)?image\s*:", line):
            continue
        match = RENDERED_IMAGE_FIELD.fullmatch(line)
        if match is None:
            raise AdmissionError(
                f"Flux render:{line_number} contains an uninterpretable image field"
            )
        images.append(match.group("value"))
    return images


def validate_rendered_image_multiset(
    rendered_images: list[str], expected_images: list[str]
) -> None:
    """Require every rendered image, including duplicates, to be admitted exactly once."""

    expected = Counter(expected_images)
    actual = Counter(rendered_images)
    if actual == expected:
        return
    missing = sorted((expected - actual).elements())
    unexpected = sorted((actual - expected).elements())
    details = [
        f"expected={sum(expected.values())}",
        f"actual={sum(actual.values())}",
    ]
    if missing:
        details.append("missing=" + ",".join(missing))
    if unexpected:
        details.append("unexpected=" + ",".join(unexpected))
    raise AdmissionError(
        "Flux rendered image multiset is not exactly the admitted four: "
        + " ".join(details)
    )


def resolve_effective_flux_images(
    *,
    candidates_path: Path = CANDIDATES,
    inventory_path: Path = INVENTORY,
    ledger_path: Path = LEDGER,
    customization_path: Path = CUSTOMIZATION,
    components_path: Path = GOTK_COMPONENTS,
    sync_path: Path = GOTK_SYNC,
) -> dict[str, str]:
    candidates = _validated_candidates(load_json(candidates_path))
    _validate_inventory(load_json(inventory_path), candidates)
    _validate_ledger(load_json(ledger_path), candidates)
    customized_images = load_customized_images(customization_path)
    generated_images = load_generated_controller_images(components_path)
    validate_gotk_sync(sync_path)

    if set(customized_images) != set(STANDARD_CONTROLLERS):
        raise AdmissionError("Flux customization must patch exactly the four standard controllers")
    effective: dict[str, str] = {}
    for component in STANDARD_CONTROLLERS:
        candidate = candidates[component]
        expected_tagged = f"{candidate['repository']}:{candidate['version']}"
        if generated_images[component] != expected_tagged:
            raise AdmissionError(
                f"{component}: generated image {generated_images[component]} does not match "
                f"candidate {expected_tagged}"
            )
        if customized_images[component] != candidate["reference"]:
            raise AdmissionError(
                f"{component}: bootstrap customization deploys "
                f"{customized_images[component]!r}, expected {candidate['reference']}"
            )
        effective[component] = candidate["reference"]

    rendered_images = render_customized_images(
        customization_path,
        components_path,
        sync_path,
    )
    validate_rendered_image_multiset(rendered_images, list(effective.values()))
    return effective


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify that generated Flux images resolve to admitted digests."
    )
    parser.add_argument("--candidates", type=Path, default=CANDIDATES)
    parser.add_argument("--inventory", type=Path, default=INVENTORY)
    parser.add_argument("--ledger", type=Path, default=LEDGER)
    parser.add_argument("--customization", type=Path, default=CUSTOMIZATION)
    parser.add_argument("--components", type=Path, default=GOTK_COMPONENTS)
    parser.add_argument("--sync", type=Path, default=GOTK_SYNC)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        effective = resolve_effective_flux_images(
            candidates_path=args.candidates,
            inventory_path=args.inventory,
            ledger_path=args.ledger,
            customization_path=args.customization,
            components_path=args.components,
            sync_path=args.sync,
        )
    except AdmissionError as error:
        print(f"gitops-image-admission: blocked: {error}")
        return 1

    print(f"gitops-image-admission: ok images={len(effective)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
