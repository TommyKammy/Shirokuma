"""Canonical, offline rendering helpers for GitOps repository policy tests.

The helpers deliberately delegate YAML decoding and Kustomize/Helm semantics to
the same mature engines used by the delivery toolchain.  They do not read the
checkout's Git remote and they never fetch a remote chart or repository.
"""

from __future__ import annotations

import base64
import functools
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator, Mapping, Sequence
from urllib.parse import urlsplit


KUSTOMIZATION_FILENAMES = (
    "kustomization.yaml",
    "kustomization.yml",
    "Kustomization",
)
MANIFEST_SUFFIXES = {".json", ".yaml", ".yml"}
BOOTSTRAP_KINDS = {"Job", "CronJob"}
POLARIS_WORKLOAD_KINDS = {"Deployment", "StatefulSet"}
EXPECTED_API_VERSIONS = {
    "ConfigMap": "v1",
    "CronJob": "batch/v1",
    "Deployment": "apps/v1",
    "GitRepository": "source.toolkit.fluxcd.io/v1",
    "HelmChart": "source.toolkit.fluxcd.io/v1",
    "HelmRelease": "helm.toolkit.fluxcd.io/v2",
    "Job": "batch/v1",
    "Kustomization": "kustomize.toolkit.fluxcd.io/v1",
    "Secret": "v1",
    "StatefulSet": "apps/v1",
}
RESOURCE_INDEX_CONFLICT_KINDS = frozenset(
    {
        "ConfigMap",
        "GitRepository",
        "HelmChart",
        "HelmRelease",
        "Kustomization",
        "Secret",
    }
)
FLUX_VERSION = "v2.9.2"
HELM_VERSION = "v4.2.2"
KUBECTL_VERSION = "v1.36.2"
KUSTOMIZE_VERSION = "v5.8.1"
_BOOTSTRAP_CONTAINER_PATHS = (
    ("spec", "template", "spec", "containers"),
    ("spec", "template", "spec", "initContainers"),
    ("spec", "jobTemplate", "spec", "template", "spec", "containers"),
    ("spec", "jobTemplate", "spec", "template", "spec", "initContainers"),
)
_POLARIS_CONTAINER_PATHS = (
    ("spec", "template", "spec", "containers"),
    ("spec", "template", "spec", "initContainers"),
)


class RenderError(RuntimeError):
    """The repository cannot be evaluated completely and safely."""


@dataclass(frozen=True)
class RepositoryContext:
    """Explicit provenance boundary for repository-local Flux sources."""

    source_root: Path
    local_git_sources: frozenset[tuple[str, str]]

    @classmethod
    def create(
        cls,
        source_root: Path,
        local_git_urls: Iterable[str],
        local_git_branches: Iterable[str] = ("main",),
    ) -> "RepositoryContext":
        root = source_root.resolve()
        urls = tuple(_canonical_git_url(url) for url in local_git_urls)
        branches = tuple(local_git_branches)
        if not urls or not branches:
            raise RenderError(
                "RepositoryContext requires a Git URL and branch"
            )
        if len(branches) == 1:
            pairs = ((url, branches[0]) for url in urls)
        elif len(urls) == 1:
            pairs = ((urls[0], branch) for branch in branches)
        elif len(urls) == len(branches):
            pairs = zip(urls, branches)
        else:
            raise RenderError(
                "RepositoryContext Git URLs and branches cannot be paired"
            )
        return cls(
            source_root=root,
            local_git_sources=frozenset(pairs),
        )

    @property
    def local_git_urls(self) -> frozenset[str]:
        return frozenset(url for url, _branch in self.local_git_sources)


@dataclass(frozen=True)
class Resource:
    path: Path
    value: dict[str, Any]

    @property
    def kind(self) -> str:
        return str(self.value.get("kind", ""))

    @property
    def namespace(self) -> str:
        metadata = _mapping(self.value.get("metadata"))
        return str(metadata.get("namespace") or "default")

    @property
    def name(self) -> str:
        return str(_mapping(self.value.get("metadata")).get("name") or "")


@dataclass(frozen=True)
class RenderedRepository:
    resources: tuple[Resource, ...]
    excluded_roots: tuple[Path, ...]


def _mapping(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _sequence(value: object) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _tool(name: str) -> str:
    configured = os.environ.get(name.upper(), name)
    executable = shutil.which(configured)
    if executable is None:
        raise RenderError(f"required renderer is unavailable: {configured}")
    return executable


@functools.lru_cache(maxsize=None)
def _verified_tool(name: str) -> str:
    executable = _tool(name)
    if name == "flux":
        actual = _run((executable, "version", "--client")).strip()
        expected = f"flux: {FLUX_VERSION}"
        if actual != expected:
            raise RenderError(f"expected {expected}, found {actual or 'unknown'}")
    elif name == "helm":
        actual = _run((executable, "version", "--short")).strip()
        if not actual.startswith(f"{HELM_VERSION}+"):
            raise RenderError(
                f"expected helm {HELM_VERSION}, found {actual or 'unknown'}"
            )
    elif name == "kubectl":
        raw = _run((executable, "version", "--client", "-o", "json"))
        try:
            version = json.loads(raw)
        except json.JSONDecodeError as error:
            raise RenderError("kubectl returned invalid version JSON") from error
        client = _mapping(version.get("clientVersion"))
        actual_client = str(client.get("gitVersion") or "")
        actual_kustomize = str(version.get("kustomizeVersion") or "")
        if (
            actual_client != KUBECTL_VERSION
            or actual_kustomize != KUSTOMIZE_VERSION
        ):
            raise RenderError(
                "expected kubectl "
                f"{KUBECTL_VERSION}/Kustomize {KUSTOMIZE_VERSION}, found "
                f"{actual_client or 'unknown'}/{actual_kustomize or 'unknown'}"
            )
    return executable


def _run(
    args: Sequence[str],
    *,
    input_text: str | None = None,
    cwd: Path | None = None,
) -> str:
    try:
        completed = subprocess.run(
            list(args),
            cwd=cwd,
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=60,
            env={
                **os.environ,
                "GIT_TERMINAL_PROMPT": "0",
                "KUBECONFIG": os.devnull,
                "LC_ALL": "C",
                "LANG": "C",
            },
        )
    except subprocess.TimeoutExpired as error:
        raise RenderError(f"{' '.join(args)} timed out") from error
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RenderError(f"{' '.join(args)} failed: {detail}")
    return completed.stdout


def _json_stream(text: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    offset = 0
    documents: list[dict[str, Any]] = []
    while offset < len(text):
        while offset < len(text) and text[offset].isspace():
            offset += 1
        if offset >= len(text):
            break
        try:
            value, offset = decoder.raw_decode(text, offset)
        except json.JSONDecodeError as error:
            raise RenderError(f"kubectl returned invalid JSON: {error}") from error
        if not isinstance(value, dict):
            raise RenderError("kubectl returned a non-object YAML document")
        documents.extend(_flatten_resource(value))
    return documents


def _flatten_resource(value: dict[str, Any]) -> list[dict[str, Any]]:
    if value.get("kind") == "List":
        flattened: list[dict[str, Any]] = []
        for item in _sequence(value.get("items")):
            if not isinstance(item, dict):
                raise RenderError("Kubernetes List contains a non-object item")
            flattened.extend(_flatten_resource(item))
        return flattened
    return [value]


def load_yaml_text(text: str) -> list[dict[str, Any]]:
    """Decode Kubernetes YAML through kubectl's production YAML stack."""

    if not text.strip():
        return []
    output = _run(
        (
            _verified_tool("kubectl"),
            "patch",
            "--local=true",
            "--type=merge",
            "-p",
            "{}",
            "-f",
            "-",
            "-o",
            "json",
        ),
        input_text=text,
    )
    return _json_stream(output)


def load_yaml_file(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise RenderError(f"cannot read manifest {path}: {error}") from error
    return load_yaml_text(text)


def _is_native_kustomize_config(value: Mapping[str, Any]) -> bool:
    return (
        value.get("kind") in {"Component", "Kustomization"}
        and str(value.get("apiVersion") or "").startswith(
            "kustomize.config.k8s.io/"
        )
    )


def _validate_resource_api(
    value: Mapping[str, Any],
    path: Path,
) -> None:
    kind = str(value.get("kind") or "")
    expected = EXPECTED_API_VERSIONS.get(kind)
    if expected is not None and value.get("apiVersion") != expected:
        raise RenderError(
            f"unsupported apiVersion for {kind} in {path}: "
            f"{value.get('apiVersion') or 'missing'}"
        )


def _manifest_paths(root: Path) -> list[Path]:
    if not root.exists():
        return []
    paths: list[Path] = []
    for path in root.rglob("*"):
        if (
            not path.is_file()
            or path.suffix.lower() not in MANIFEST_SUFFIXES
        ):
            continue
        if not _path_within(path.resolve(), root.resolve()):
            raise RenderError(f"manifest symlink escapes scan root: {path}")
        paths.append(path)
    return sorted(paths)


def load_manifest_resources(root: Path) -> list[Resource]:
    resources: list[Resource] = []
    for path in _manifest_paths(root):
        for value in load_yaml_file(path):
            if _is_native_kustomize_config(value):
                continue
            _validate_resource_api(value, path)
            resources.append(Resource(path.resolve(), value))
    return resources


def _canonical_git_url(value: str) -> str:
    raw = value.strip()
    scp_match = re.fullmatch(r"(?P<user>[^@/\s]+)@(?P<host>[^:/\s]+):(?P<path>.+)", raw)
    if scp_match:
        host = scp_match.group("host").lower()
        path = scp_match.group("path")
    else:
        parsed = urlsplit(raw)
        if not parsed.hostname:
            raise RenderError(f"unsupported Git repository URL: {value}")
        host = parsed.hostname.lower()
        default_port = {
            "http": 80,
            "https": 443,
            "ssh": 22,
        }.get(parsed.scheme.lower())
        try:
            port = parsed.port
        except ValueError as error:
            raise RenderError(
                f"unsupported Git repository URL: {value}"
            ) from error
        if port is not None and port != default_port:
            host = f"{host}:{port}"
        path = parsed.path
    normalized_path = path.strip("/").removesuffix(".git")
    if not normalized_path:
        raise RenderError(f"unsupported Git repository URL: {value}")
    return f"{host}/{normalized_path}"


def default_repository_context(source_root: Path, deploy_root: Path) -> RepositoryContext:
    """Read the checked-in Flux source contract, never ambient .git/config."""

    sync_path = (
        deploy_root
        / "gitops"
        / "clusters"
        / "local-lite"
        / "flux-system"
        / "gotk-sync.yaml"
    )
    if not sync_path.is_file():
        raise RenderError(
            "temporary or alternate repositories must provide RepositoryContext"
        )
    sources: list[tuple[str, str]] = []
    for resource in load_yaml_file(sync_path):
        if (
            resource.get("kind") != "GitRepository"
            or resource.get("apiVersion")
            != EXPECTED_API_VERSIONS["GitRepository"]
        ):
            continue
        spec = _mapping(resource.get("spec"))
        url = spec.get("url")
        branch = _mapping(spec.get("ref")).get("branch")
        if isinstance(url, str) and isinstance(branch, str) and branch:
            sources.append((url, branch))
    if not sources:
        raise RenderError(f"no GitRepository URL found in {sync_path}")
    return RepositoryContext.create(
        source_root,
        (url for url, _branch in sources),
        (branch for _url, branch in sources),
    )


def _resource_key(
    kind: str,
    namespace: str,
    name: str,
) -> tuple[str, str, str]:
    return kind, namespace or "default", name


def _mapping_has_expected_api_version(value: Mapping[str, Any]) -> bool:
    kind = str(value.get("kind") or "")
    expected = EXPECTED_API_VERSIONS.get(kind)
    return expected is None or value.get("apiVersion") == expected


def _has_expected_api_version(resource: Resource) -> bool:
    return _mapping_has_expected_api_version(resource.value)


def _resource_index(
    resources: Iterable[Resource],
    *,
    reject_conflicts: bool = True,
) -> dict[tuple[str, str, str], Resource]:
    result: dict[tuple[str, str, str], Resource] = {}
    for resource in resources:
        if (
            resource.kind
            and resource.name
            and _has_expected_api_version(resource)
        ):
            key = _resource_key(
                resource.kind,
                resource.namespace,
                resource.name,
            )
            previous = result.get(key)
            if (
                reject_conflicts
                and resource.kind in RESOURCE_INDEX_CONFLICT_KINDS
                and previous is not None
                and previous.value != resource.value
            ):
                raise RenderError(
                    "conflicting resources share one logical identity: "
                    f"{resource.kind}/{resource.namespace}/{resource.name}"
                )
            result[key] = resource
    return result


def _is_flux_kustomization(resource: Resource) -> bool:
    return (
        resource.kind == "Kustomization"
        and resource.value.get("apiVersion")
        == EXPECTED_API_VERSIONS["Kustomization"]
    )


def _is_helm_release(resource: Resource) -> bool:
    return (
        resource.kind == "HelmRelease"
        and resource.value.get("apiVersion")
        == EXPECTED_API_VERSIONS["HelmRelease"]
    )


def _path_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _safe_source_path(context: RepositoryContext, value: str) -> Path:
    relative = value.strip() or "."
    if Path(relative).is_absolute():
        raise RenderError(f"repository path must be relative: {value}")
    candidate = (context.source_root / relative.removeprefix("./")).resolve()
    if not _path_within(candidate, context.source_root):
        raise RenderError(f"repository path escapes source root: {value}")
    return candidate


def _flux_path_value(spec: Mapping[str, Any]) -> str:
    if "path" not in spec:
        return "."
    value = spec["path"]
    if not isinstance(value, str):
        raise RenderError("Flux Kustomization path must be a string")
    return value or "."


def _native_kustomization_path(directory: Path) -> Path | None:
    candidates = [
        directory / name
        for name in KUSTOMIZATION_FILENAMES
        if (directory / name).is_file()
    ]
    if len(candidates) > 1:
        raise RenderError(
            f"multiple native Kustomization files in {directory}"
        )
    return candidates[0] if candidates else None


def _is_remote_reference(reference: str) -> bool:
    return bool(
        re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", reference)
        or re.match(r"^[^@/\s]+@[^:/\s]+:", reference)
        or re.match(
            r"^(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?::\d+)?/",
            reference,
        )
        or "::" in reference
        or "//" in reference
        or "?" in reference
        or "#" in reference
    )


def _local_reference_path(
    value: str,
    *,
    base: Path,
    context: RepositoryContext,
    generator_entry: bool = False,
    allow_missing: bool = False,
) -> Path:
    reference = value
    if generator_entry and "=" in reference:
        _key, reference = reference.split("=", 1)
    if not reference or "\n" in reference or "\r" in reference:
        raise RenderError(f"invalid local Kustomize path: {value!r}")
    if Path(reference).is_absolute():
        raise RenderError(f"Kustomize path must be relative: {value}")
    if _is_remote_reference(reference):
        raise RenderError(f"remote Kustomize path is not allowed: {value}")
    candidate = (base / reference).resolve()
    if not _path_within(candidate, context.source_root):
        raise RenderError(f"Kustomize path escapes source root: {value}")
    if not candidate.exists() and not allow_missing:
        raise RenderError(
            f"Kustomize remote or missing path is not allowed: {value}"
        )
    return candidate


def _validate_native_kustomization_graph(
    target: Path,
    context: RepositoryContext,
) -> None:
    visited: set[Path] = set()
    active: set[Path] = set()
    autogenerated_visited: set[Path] = set()
    autogenerated_active: set[Path] = set()

    def validate_path(
        value: object,
        *,
        base: Path,
        generator_entry: bool = False,
    ) -> Path:
        if not isinstance(value, str):
            raise RenderError("Kustomize path reference must be a string")
        return _local_reference_path(
            value,
            base=base,
            context=context,
            generator_entry=generator_entry,
        )

    def validate_config(directory: Path) -> None:
        config_path = _native_kustomization_path(directory)
        if config_path is None:
            raise RenderError(
                f"referenced Kustomize directory has no Kustomization: {directory}"
            )
        resolved_config = config_path.resolve()
        if not _path_within(resolved_config, context.source_root):
            raise RenderError(
                f"native Kustomization escapes source root: {config_path}"
            )
        if resolved_config in active:
            raise RenderError(f"Kustomize resource graph contains a cycle: {directory}")
        if resolved_config in visited:
            return
        active.add(resolved_config)
        documents = load_yaml_file(config_path)
        if len(documents) != 1 or documents[0].get("kind") not in {
            "Kustomization",
            "Component",
        }:
            raise RenderError(f"invalid native Kustomization: {config_path}")
        config = documents[0]
        if _sequence(config.get("helmCharts")):
            raise RenderError(
                f"Kustomize Helm inflator is not allowed offline: {config_path}"
            )
        for field in ("generators", "transformers", "validators"):
            if _sequence(config.get(field)):
                raise RenderError(
                    "Kustomize file plugins are outside the offline "
                    f"rendering contract: {field} in {config_path}"
                )

        for field in ("resources", "bases", "components"):
            for reference in _sequence(config.get(field)):
                child = validate_path(reference, base=directory)
                if child.is_dir():
                    validate_config(child)

        for field in (
            "configurations",
            "crds",
            "patchesStrategicMerge",
        ):
            for reference in _sequence(config.get(field)):
                if field == "patchesStrategicMerge" and not isinstance(
                    reference, str
                ):
                    continue
                child = validate_path(reference, base=directory)
                if child.is_dir():
                    raise RenderError(
                        f"Kustomize {field} entry is not a file: {reference}"
                    )

        for field in ("patches", "patchesJson6902"):
            for patch_value in _sequence(config.get(field)):
                patch = _mapping(patch_value)
                path = patch.get("path")
                if path is not None:
                    child = validate_path(path, base=directory)
                    if child.is_dir():
                        raise RenderError(
                            f"Kustomize patch path is not a file: {path}"
                        )

        for replacement_value in _sequence(config.get("replacements")):
            replacement = _mapping(replacement_value)
            path = replacement.get("path")
            if path is not None:
                child = validate_path(path, base=directory)
                if child.is_dir():
                    raise RenderError(
                        f"Kustomize replacement path is not a file: {path}"
                    )

        openapi = _mapping(config.get("openapi"))
        if openapi.get("path") is not None:
            child = validate_path(openapi["path"], base=directory)
            if child.is_dir():
                raise RenderError(
                    f"Kustomize OpenAPI path is not a file: {child}"
                )

        for generator_field in ("configMapGenerator", "secretGenerator"):
            for generator_value in _sequence(config.get(generator_field)):
                generator = _mapping(generator_value)
                for input_field in ("env",):
                    if generator.get(input_field) is not None:
                        child = validate_path(
                            generator[input_field],
                            base=directory,
                            generator_entry=True,
                        )
                        if child.is_dir():
                            raise RenderError(
                                f"Kustomize generator input is not a file: {child}"
                            )
                for input_field in ("envs", "files"):
                    for reference in _sequence(generator.get(input_field)):
                        child = validate_path(
                            reference,
                            base=directory,
                            generator_entry=True,
                        )
                        if child.is_dir():
                            raise RenderError(
                                f"Kustomize generator input is not a file: {child}"
                            )

        active.remove(resolved_config)
        visited.add(resolved_config)

    def validate_autogenerated(directory: Path) -> None:
        resolved_directory = directory.resolve()
        if not _path_within(resolved_directory, context.source_root):
            raise RenderError(
                f"autogenerated Kustomize path escapes source root: {directory}"
            )
        if resolved_directory in autogenerated_active:
            raise RenderError(
                f"autogenerated Kustomize graph contains a cycle: {directory}"
            )
        if resolved_directory in autogenerated_visited:
            return
        autogenerated_active.add(resolved_directory)
        try:
            children = sorted(directory.iterdir())
        except OSError as error:
            raise RenderError(
                f"cannot read autogenerated Kustomize directory {directory}: {error}"
            ) from error
        for child in children:
            if child.is_symlink() and not _path_within(
                child.resolve(),
                context.source_root,
            ):
                raise RenderError(f"symlink escapes source root: {child}")
            if not child.is_dir():
                continue
            if _native_kustomization_path(child):
                validate_config(child)
            else:
                validate_autogenerated(child)
        autogenerated_active.remove(resolved_directory)
        autogenerated_visited.add(resolved_directory)

    if _native_kustomization_path(target):
        validate_config(target)
    else:
        validate_autogenerated(target)


def _validate_flux_build_inputs(
    resource: Resource,
    target: Path,
    context: RepositoryContext,
) -> None:
    _validate_native_kustomization_graph(target, context)
    spec = _mapping(resource.value.get("spec"))
    ignore_missing = spec.get("ignoreMissingComponents", False)
    if not isinstance(ignore_missing, bool):
        raise RenderError(
            "Flux ignoreMissingComponents must be a boolean"
        )
    for component in _sequence(spec.get("components")):
        if not isinstance(component, str):
            raise RenderError("Flux component path must be a string")
        component_path = _local_reference_path(
            component,
            base=target,
            context=context,
            allow_missing=ignore_missing,
        )
        if not component_path.exists():
            continue
        if not component_path.is_dir():
            raise RenderError(f"Flux component is not a directory: {component}")
        _validate_native_kustomization_graph(component_path, context)
    post_build = _mapping(spec.get("postBuild"))
    if _sequence(post_build.get("substituteFrom")):
        raise RenderError(
            "Flux postBuild.substituteFrom requires unavailable cluster state"
        )
    if _mapping(spec.get("decryption")):
        raise RenderError(
            "Flux decryption requires unavailable controller key state"
        )


def _git_source_matches_context_url(
    resource: Resource,
    context: RepositoryContext,
) -> bool:
    if (
        resource.kind != "GitRepository"
        or not _has_expected_api_version(resource)
    ):
        return False
    spec = _mapping(resource.value.get("spec"))
    url = spec.get("url")
    if not isinstance(url, str):
        return False
    try:
        canonical_url = _canonical_git_url(url)
    except RenderError:
        return False
    if canonical_url not in context.local_git_urls:
        return False
    return True


def _is_local_git_source(resource: Resource, context: RepositoryContext) -> bool:
    if not _git_source_matches_context_url(resource, context):
        return False
    spec = _mapping(resource.value.get("spec"))
    if bool(spec.get("suspend")):
        raise RenderError(
            "repository-local GitRepository is suspended and has no current artifact"
        )
    unsupported_fields = [
        field
        for field in (
            "ignore",
            "include",
            "recurseSubmodules",
            "sparseCheckout",
            "verify",
        )
        if field in spec and spec[field] not in (None, False, [], {})
    ]
    if unsupported_fields:
        raise RenderError(
            "repository-local GitRepository uses unsupported artifact shaping: "
            + ", ".join(sorted(unsupported_fields))
        )
    if (context.source_root / ".sourceignore").exists():
        raise RenderError(
            "repository-local GitRepository requires unsupported .sourceignore"
        )
    if (context.source_root / ".gitmodules").exists():
        raise RenderError(
            "repository-local GitRepository requires unsupported submodule state"
        )
    source_ref = _mapping(spec.get("ref"))
    if (
        set(source_ref) != {"branch"}
        or not isinstance(source_ref.get("branch"), str)
        or not source_ref["branch"]
    ):
        raise RenderError(
            "repository-local GitRepository requires one explicit branch ref"
        )
    canonical_url = _canonical_git_url(str(spec.get("url") or ""))
    if (canonical_url, source_ref["branch"]) not in context.local_git_sources:
        raise RenderError(
            "repository-local GitRepository URL/branch pair is outside "
            "RepositoryContext"
        )
    return True


def _source_for(
    owner: Resource,
    source_ref: Mapping[str, Any],
    index: Mapping[tuple[str, str, str], Resource],
    *,
    allow_api_version: bool = True,
    allow_namespace: bool = True,
) -> Resource | None:
    allowed_fields = {"kind", "name"}
    if allow_namespace:
        allowed_fields.add("namespace")
    if allow_api_version:
        allowed_fields.add("apiVersion")
    unsupported = set(source_ref) - allowed_fields
    if unsupported:
        raise RenderError(
            "unsupported Flux source reference field: "
            + ", ".join(sorted(unsupported))
        )
    kind = source_ref.get("kind")
    name = source_ref.get("name")
    namespace_value = source_ref.get("namespace")
    if not isinstance(kind, str) or not kind:
        raise RenderError("Flux source reference kind is required")
    if not isinstance(name, str) or not name:
        raise RenderError("Flux source reference name is required")
    if namespace_value is not None and not isinstance(namespace_value, str):
        raise RenderError("Flux source reference namespace must be a string")
    api_version = source_ref.get("apiVersion")
    if api_version is not None:
        if not isinstance(api_version, str) or not api_version:
            raise RenderError(
                "Flux source reference apiVersion must be a string"
            )
        expected_api_version = EXPECTED_API_VERSIONS.get(kind)
        if (
            expected_api_version is not None
            and api_version != expected_api_version
        ):
            raise RenderError(
                "Flux source reference apiVersion does not match kind"
            )
    namespace = namespace_value or owner.namespace
    return index.get(_resource_key(kind, namespace, name))


def _write_kustomization(directory: Path, value: Mapping[str, Any]) -> None:
    try:
        (directory / "kustomization.yaml").write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        raise RenderError(f"cannot stage Kustomize overlay: {error}") from error


def _flux_transform(spec: Mapping[str, Any]) -> dict[str, Any]:
    transform: dict[str, Any] = {}
    for field in ("patches", "images", "namePrefix", "nameSuffix"):
        if field in spec:
            transform[field] = spec[field]
    target_namespace = spec.get("targetNamespace")
    if isinstance(target_namespace, str) and target_namespace:
        transform["namespace"] = target_namespace
    common_metadata = _mapping(spec.get("commonMetadata"))
    labels = _mapping(common_metadata.get("labels"))
    annotations = _mapping(common_metadata.get("annotations"))
    if labels:
        transform["labels"] = [
            {
                "pairs": labels,
                "includeSelectors": False,
                "includeTemplates": False,
            }
        ]
    if annotations:
        transform["commonAnnotations"] = annotations
    return transform


def _apply_kustomize_transform(rendered: str, transform: Mapping[str, Any]) -> str:
    if not transform or not rendered.strip():
        return rendered
    with tempfile.TemporaryDirectory(prefix="shirokuma-transform-") as temporary:
        overlay = Path(temporary)
        (overlay / "base.yaml").write_text(rendered, encoding="utf-8")
        kustomization = {
            "apiVersion": "kustomize.config.k8s.io/v1beta1",
            "kind": "Kustomization",
            "resources": ["base.yaml"],
            **transform,
        }
        _write_kustomization(overlay, kustomization)
        return _run((_verified_tool("kubectl"), "kustomize", str(overlay)))


def _render_flux_kustomization(
    resource: Resource,
    context: RepositoryContext,
    local_sources: str,
) -> tuple[list[Resource], Path]:
    spec = _mapping(resource.value.get("spec"))
    target = _safe_source_path(context, _flux_path_value(spec))
    if bool(spec.get("suspend")):
        return [], target
    if not target.is_dir():
        raise RenderError(f"Flux reconciliation path is not a directory: {target}")
    _validate_flux_build_inputs(resource, target, context)
    flux = _verified_tool("flux")
    with tempfile.TemporaryDirectory(prefix="shirokuma-flux-") as temporary:
        kustomization_file = Path(temporary) / "flux-kustomization.json"
        kustomization_file.write_text(
            json.dumps(resource.value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        rendered = _run(
            (
                flux,
                "build",
                "kustomization",
                resource.name,
                "--namespace",
                resource.namespace,
                "--dry-run",
                "--path",
                str(target),
                "--kustomization-file",
                str(kustomization_file),
                "--local-sources",
                local_sources,
            )
        )
    values = load_yaml_text(rendered)
    for value in values:
        _validate_resource_api(value, resource.path)
    return [Resource(resource.path, value) for value in values], target


def render_repository(
    deploy_root: Path,
    context: RepositoryContext,
) -> RenderedRepository:
    """Render every repository-local Flux reconciliation to its final state."""

    raw_resources = load_manifest_resources(deploy_root)
    index = _resource_index(raw_resources, reject_conflicts=False)
    candidates: list[
        tuple[Resource, Resource | None, Path | None, RenderError | None]
    ] = []
    excluded_roots: set[Path] = set()
    for candidate in raw_resources:
        if not _is_flux_kustomization(candidate):
            continue
        spec = _mapping(candidate.value.get("spec"))
        source = _source_for(candidate, _mapping(spec.get("sourceRef")), index)
        target: Path | None = None
        path_error: RenderError | None = None
        if source is not None and _git_source_matches_context_url(
            source,
            context,
        ):
            try:
                target = _safe_source_path(
                    context,
                    _flux_path_value(spec),
                )
            except RenderError as error:
                path_error = error
        candidates.append((candidate, source, target, path_error))

    roots: list[tuple[Resource, Resource]] = []
    for candidate, source, target, path_error in candidates:
        is_nested = any(
            other is not candidate
            and other_target is not None
            and _path_within(candidate.path, other_target)
            for other, _other_source, other_target, _other_error in candidates
        )
        if target is not None:
            excluded_roots.add(target)
        if is_nested:
            continue
        spec = _mapping(candidate.value.get("spec"))
        if bool(spec.get("suspend")):
            continue
        if source is None:
            raise RenderError(
                "Flux Kustomization source is unresolved: "
                f"{candidate.namespace}/{candidate.name}"
            )
        if not _is_local_git_source(source, context):
            raise RenderError(
                "Flux Kustomization source is outside RepositoryContext: "
                f"{candidate.namespace}/{candidate.name}"
            )
        source_key = _resource_key(
            source.kind,
            source.namespace,
            source.name,
        )
        _resource_index(
            resource
            for resource in raw_resources
            if _resource_key(
                resource.kind,
                resource.namespace,
                resource.name,
            )
            == source_key
        )
        if path_error is not None:
            raise path_error
        if target is None:
            raise RenderError(
                "Flux Kustomization has no repository-local path: "
                f"{candidate.namespace}/{candidate.name}"
            )
        roots.append((candidate, source))
    structurally_reachable = {id(candidate) for candidate, _source in roots}
    changed = True
    while changed:
        changed = False
        for candidate, _source, _target, _path_error in candidates:
            if id(candidate) in structurally_reachable:
                continue
            if any(
                id(parent) in structurally_reachable
                and parent_target is not None
                and _path_within(candidate.path, parent_target)
                for (
                    parent,
                    _parent_source,
                    parent_target,
                    _parent_error,
                ) in candidates
                if parent is not candidate
            ):
                structurally_reachable.add(id(candidate))
                changed = True
    unreachable = [
        candidate
        for candidate, _source, target, _path_error in candidates
        if (
            target is not None
            and not bool(_mapping(candidate.value.get("spec")).get("suspend"))
            and id(candidate) not in structurally_reachable
        )
    ]
    if unreachable:
        raise RenderError(
            "active repository-local Flux reconciliations form an "
            "unreachable or cyclic root graph: "
            + ", ".join(
                f"{candidate.namespace}/{candidate.name}"
                for candidate in unreachable
            )
        )

    rendered_by_plan: list[Resource] = []
    discovered: list[Resource] = []
    pending: list[Resource] = []
    for candidate, source in roots:
        discovered.extend((candidate, source))
        pending.append(candidate)
    processed: dict[tuple[str, str, str, str, str], str] = {}
    processed_targets: dict[Path, tuple[str, str]] = {}
    while pending:
        candidate = pending.pop(0)
        spec = _mapping(candidate.value.get("spec"))
        source_ref = _mapping(spec.get("sourceRef"))
        logical_key = (
            candidate.namespace,
            candidate.name,
            str(source_ref.get("kind") or "GitRepository"),
            str(source_ref.get("name") or ""),
            _flux_path_value(spec),
        )
        spec_fingerprint = json.dumps(spec, sort_keys=True)
        previous = processed.get(logical_key)
        if previous is not None:
            if previous != spec_fingerprint:
                raise RenderError(
                    "Flux reconciliation has conflicting rendered definitions: "
                    f"{candidate.namespace}/{candidate.name}"
                )
            continue
        processed[logical_key] = spec_fingerprint

        # A rendered reconciliation supersedes its raw bootstrap anchors.
        # Conflicts in the effective resource graph are checked after traversal.
        discovered_index = _resource_index(
            discovered,
            reject_conflicts=False,
        )
        source = _source_for(candidate, source_ref, discovered_index)
        if bool(spec.get("suspend")):
            if source is not None and _git_source_matches_context_url(
                source,
                context,
            ):
                target = _safe_source_path(
                    context,
                    _flux_path_value(spec),
                )
                excluded_roots.add(target)
            continue
        if source is None:
            raise RenderError(
                "Flux Kustomization source is unresolved after rendering: "
                f"{candidate.namespace}/{candidate.name}"
            )
        if not _is_local_git_source(source, context):
            raise RenderError(
                "Flux Kustomization source is outside RepositoryContext: "
                f"{candidate.namespace}/{candidate.name}"
            )
        target = _safe_source_path(context, _flux_path_value(spec))
        target_owner = (candidate.namespace, candidate.name)
        previous_owner = processed_targets.get(target)
        if previous_owner is not None and previous_owner != target_owner:
            raise RenderError(
                "multiple reachable Flux reconciliations share one target "
                f"path {target}: "
                f"{previous_owner[0]}/{previous_owner[1]}, "
                f"{target_owner[0]}/{target_owner[1]}"
            )
        processed_targets[target] = target_owner
        excluded_roots.add(target)
        discovered_sources = {
            (resource.namespace, resource.name)
            for resource in discovered
            if _is_local_git_source(resource, context)
        }
        local_source_argument = ",".join(
            (
                f"GitRepository/{namespace}/{name}="
                f"{context.source_root}"
            )
            for namespace, name in sorted(discovered_sources)
        )
        rendered, _ = _render_flux_kustomization(
            candidate,
            context,
            local_source_argument,
        )
        rendered_by_plan.extend(rendered)
        discovered.extend(rendered)
        pending.extend(
            resource
            for resource in rendered
            if _is_flux_kustomization(resource)
        )

    effective: list[Resource] = []
    for resource in raw_resources:
        if any(_path_within(resource.path, root) for root in excluded_roots):
            continue
        effective.append(resource)
    effective.extend(rendered_by_plan)
    return RenderedRepository(
        resources=tuple(effective),
        excluded_roots=tuple(sorted(excluded_roots, key=str)),
    )


def _nested(value: Mapping[str, Any], path: Sequence[str]) -> object:
    current: object = value
    for component in path:
        if not isinstance(current, dict):
            return None
        current = current.get(component)
    return current


def _identity_values(resource: Mapping[str, Any]) -> Iterator[str]:
    metadata = _mapping(resource.get("metadata"))
    name = metadata.get("name")
    if isinstance(name, str):
        yield name
    for value in _mapping(metadata.get("labels")).values():
        if isinstance(value, (str, int, float, bool)):
            yield str(value)
    for path in _BOOTSTRAP_CONTAINER_PATHS:
        for container in _sequence(_nested(resource, path)):
            mapping = _mapping(container)
            for field in ("name", "image"):
                value = mapping.get(field)
                if isinstance(value, str):
                    yield value


def has_iceberg_bootstrap_identity(resource: Mapping[str, Any]) -> bool:
    if (
        resource.get("kind") not in BOOTSTRAP_KINDS
        or not _mapping_has_expected_api_version(resource)
    ):
        return False
    return any(
        "iceberg" in value.lower() and "bootstrap" in value.lower()
        for value in _identity_values(resource)
    )


def _chart_source(
    release: Resource,
    index: Mapping[tuple[str, str, str], Resource],
) -> tuple[str, Resource, list[str], bool] | None:
    spec = _mapping(release.value.get("spec"))
    if spec.get("chart") is not None and spec.get("chartRef") is not None:
        raise RenderError("HelmRelease must select exactly one chart source")
    direct = _mapping(spec.get("chart"))
    if direct:
        chart_spec = _mapping(direct.get("spec"))
        chart = chart_spec.get("chart")
        source = _source_for(
            release,
            _mapping(chart_spec.get("sourceRef")),
            index,
        )
        if (
            chart_spec.get("reconcileStrategy") or "ChartVersion"
        ) != "Revision":
            raise RenderError(
                "local Helm chart requires reconcileStrategy: Revision"
            )
        if (
            "valuesFiles" in chart_spec
            and not isinstance(chart_spec["valuesFiles"], list)
        ):
            raise RenderError("Helm chart valuesFiles must be a list")
        raw_values_files = _sequence(chart_spec.get("valuesFiles"))
        if any(not isinstance(value, str) for value in raw_values_files):
            raise RenderError("Helm chart valuesFiles entries must be strings")
        values_files = list(raw_values_files)
        if (
            "ignoreMissingValuesFiles" in chart_spec
            and not isinstance(chart_spec["ignoreMissingValuesFiles"], bool)
        ):
            raise RenderError(
                "Helm chart ignoreMissingValuesFiles must be a boolean"
            )
        if isinstance(chart, str) and source is not None:
            return (
                chart,
                source,
                values_files,
                bool(chart_spec.get("ignoreMissingValuesFiles")),
            )
        return None

    chart_ref = _mapping(spec.get("chartRef"))
    if not chart_ref:
        return None
    referenced = _source_for(
        release,
        chart_ref,
        index,
        allow_api_version=True,
    )
    if referenced is None or referenced.kind != "HelmChart":
        return None
    chart_spec = _mapping(referenced.value.get("spec"))
    if bool(chart_spec.get("suspend")):
        raise RenderError(
            "referenced HelmChart is suspended and has no current artifact"
        )
    if (
        chart_spec.get("reconcileStrategy") or "ChartVersion"
    ) != "Revision":
        raise RenderError(
            "local HelmChart requires reconcileStrategy: Revision"
        )
    chart = chart_spec.get("chart")
    source = _source_for(
        referenced,
        _mapping(chart_spec.get("sourceRef")),
        index,
        allow_namespace=False,
    )
    if (
        "valuesFiles" in chart_spec
        and not isinstance(chart_spec["valuesFiles"], list)
    ):
        raise RenderError("HelmChart valuesFiles must be a list")
    raw_values_files = _sequence(chart_spec.get("valuesFiles"))
    if any(not isinstance(value, str) for value in raw_values_files):
        raise RenderError("HelmChart valuesFiles entries must be strings")
    values_files = list(raw_values_files)
    if (
        "ignoreMissingValuesFiles" in chart_spec
        and not isinstance(chart_spec["ignoreMissingValuesFiles"], bool)
    ):
        raise RenderError(
            "HelmChart ignoreMissingValuesFiles must be a boolean"
        )
    if isinstance(chart, str) and source is not None:
        return (
            chart,
            source,
            values_files,
            bool(chart_spec.get("ignoreMissingValuesFiles")),
        )
    return None


def _values_source_text(
    release: Resource,
    reference: Mapping[str, Any],
    index: Mapping[tuple[str, str, str], Resource],
) -> str | None:
    allowed_fields = {
        "kind",
        "name",
        "valuesKey",
        "targetPath",
        "optional",
        "literal",
    }
    if set(reference) - allowed_fields:
        raise RenderError(
            "unsupported Helm valuesFrom field: "
            + ", ".join(sorted(set(reference) - allowed_fields))
        )
    kind = reference.get("kind")
    name = reference.get("name")
    if kind not in {"ConfigMap", "Secret"}:
        raise RenderError(
            "Helm valuesFrom kind must be ConfigMap or Secret"
        )
    if not isinstance(name, str) or not name:
        raise RenderError("Helm valuesFrom name is required")
    for field in ("valuesKey", "targetPath"):
        if field in reference and not isinstance(reference[field], str):
            raise RenderError(f"Helm valuesFrom {field} must be a string")
    for field in ("optional", "literal"):
        if field in reference and not isinstance(reference[field], bool):
            raise RenderError(f"Helm valuesFrom {field} must be a boolean")
    namespace = release.namespace
    source = index.get(_resource_key(kind, namespace, name))
    if source is None:
        if bool(reference.get("optional")):
            return None
        raise RenderError(
            f"required Helm values source is missing: {kind}/{namespace}/{name}"
        )
    key = str(reference.get("valuesKey") or "values.yaml")
    if kind == "ConfigMap":
        value = _mapping(source.value.get("data")).get(key)
        if not isinstance(value, str):
            raise RenderError(f"ConfigMap {namespace}/{name} has no {key}")
        return value
    if kind == "Secret":
        encoded = _mapping(source.value.get("data")).get(key)
        string_value = _mapping(source.value.get("stringData")).get(key)
        if isinstance(string_value, str):
            return string_value
        if not isinstance(encoded, str):
            raise RenderError(f"Secret {namespace}/{name} has no {key}")
        try:
            normalized = "".join(encoded.split())
            return base64.b64decode(normalized, validate=True).decode("utf-8")
        except (ValueError, UnicodeError) as error:
            raise RenderError(
                f"Secret {namespace}/{name} contains invalid {key}"
            ) from error
    raise RenderError(f"unsupported Helm values source kind: {kind}")


_DYNAMIC_HELM_FUNCTIONS = frozenset(
    {
        "ago",
        "bcrypt",
        "date",
        "dateInZone",
        "dateModify",
        "date_in_zone",
        "date_modify",
        "durationRound",
        "encryptAES",
        "genCA",
        "genCAWithKey",
        "genPrivateKey",
        "genSelfSignedCert",
        "genSelfSignedCertWithKey",
        "genSignedCert",
        "genSignedCertWithKey",
        "getHostByName",
        "htpasswd",
        "htmlDate",
        "htmlDateInZone",
        "now",
        "randAlpha",
        "randAlphaNum",
        "randAscii",
        "randBytes",
        "randInt",
        "randNumeric",
        "shuffle",
        "uuidv4",
    }
)


def _helm_template_actions(template: str) -> str:
    """Extract Go-template actions without trusting delimiters in literals."""

    actions: list[str] = []
    offset = 0
    while True:
        start = template.find("{{", offset)
        if start < 0:
            break
        cursor = start + 2
        quote: str | None = None
        escaped = False
        comment = False
        while cursor < len(template):
            if comment:
                if template.startswith("*/", cursor):
                    comment = False
                    cursor += 2
                    continue
                cursor += 1
                continue
            character = template[cursor]
            if quote is not None:
                if quote == '"' and escaped:
                    escaped = False
                elif quote == '"' and character == "\\":
                    escaped = True
                elif character == quote:
                    quote = None
                cursor += 1
                continue
            if template.startswith("/*", cursor):
                comment = True
                cursor += 2
                continue
            if character in {'"', "'", "`"}:
                quote = character
                cursor += 1
                continue
            if template.startswith("}}", cursor):
                actions.append(template[start : cursor + 2])
                offset = cursor + 2
                break
            cursor += 1
        else:
            raise RenderError("Helm template contains an unterminated action")
    return "\n".join(actions)


def _strip_helm_action_literals(actions: str) -> str:
    """Blank strings and comments while preserving executable action text."""

    normalized: list[str] = []
    cursor = 0
    quote: str | None = None
    escaped = False
    comment = False
    while cursor < len(actions):
        if comment:
            if actions.startswith("*/", cursor):
                normalized.extend((" ", " "))
                comment = False
                cursor += 2
                continue
            normalized.append(" ")
            cursor += 1
            continue
        character = actions[cursor]
        if quote is not None:
            normalized.append(" ")
            if quote == '"' and escaped:
                escaped = False
            elif quote == '"' and character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            cursor += 1
            continue
        if actions.startswith("/*", cursor):
            normalized.extend((" ", " "))
            comment = True
            cursor += 2
            continue
        if character in {'"', "'", "`"}:
            normalized.append(" ")
            quote = character
            cursor += 1
            continue
        normalized.append(character)
        cursor += 1
    return "".join(normalized)


def _preflight_helm_chart_tree(
    chart_root: Path,
    context: RepositoryContext,
) -> None:
    """Reject filesystem entries that could escape the repository boundary."""

    visited: set[Path] = set()
    active: set[Path] = set()

    def walk(directory: Path) -> None:
        try:
            resolved_directory = directory.resolve(strict=True)
        except OSError as error:
            raise RenderError(f"cannot resolve Helm chart path {directory}: {error}") from error
        if not _path_within(resolved_directory, context.source_root):
            raise RenderError(f"Helm chart symlink escapes source root: {directory}")
        if resolved_directory in active:
            raise RenderError(f"Helm chart tree contains a symlink cycle: {directory}")
        if resolved_directory in visited:
            return
        active.add(resolved_directory)
        try:
            children = sorted(directory.iterdir(), key=lambda child: child.name)
        except OSError as error:
            raise RenderError(f"cannot read Helm chart directory {directory}: {error}") from error
        for child in children:
            try:
                resolved_child = child.resolve(strict=True)
            except OSError as error:
                raise RenderError(f"cannot resolve Helm chart path {child}: {error}") from error
            if not _path_within(resolved_child, context.source_root):
                raise RenderError(f"Helm chart symlink escapes source root: {child}")
            if child.is_dir():
                walk(child)
                continue
            if not child.is_file():
                raise RenderError(f"unsupported Helm chart filesystem entry: {child}")
        active.remove(resolved_directory)
        visited.add(resolved_directory)

    walk(chart_root)


def _stage_helm_chart(
    chart_root: Path,
    staging: Path,
) -> Path:
    """Use Helm's loader as the oracle for .helmignore and chart contents."""

    package_directory = staging / "package"
    package_directory.mkdir()
    _run(
        (
            _verified_tool("helm"),
            "package",
            str(chart_root),
            "--destination",
            str(package_directory),
        )
    )
    archives = sorted(package_directory.glob("*.tgz"))
    if len(archives) != 1:
        raise RenderError("Helm package did not produce exactly one chart archive")

    extracted = staging / "chart"
    extracted.mkdir()
    roots: set[str] = set()
    entries: list[tuple[tarfile.TarInfo, tuple[str, ...]]] = []
    try:
        with tarfile.open(archives[0], mode="r:gz") as bundle:
            for member in bundle.getmembers():
                path = PurePosixPath(member.name)
                parts = path.parts
                if (
                    path.is_absolute()
                    or not parts
                    or any(part in {"", ".", ".."} for part in parts)
                    or not (member.isdir() or member.isfile())
                ):
                    raise RenderError(
                        f"unsafe Helm package entry: {member.name}"
                    )
                roots.add(parts[0])
                entries.append((member, parts))
            if len(roots) != 1:
                raise RenderError("Helm package has an ambiguous chart root")
            for member, parts in entries:
                target = extracted.joinpath(*parts)
                if not _path_within(target.resolve(), extracted.resolve()):
                    raise RenderError(
                        f"Helm package entry escapes staging: {member.name}"
                    )
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                source = bundle.extractfile(member)
                if source is None:
                    raise RenderError(
                        f"cannot read Helm package entry: {member.name}"
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read())
    except (OSError, tarfile.TarError) as error:
        raise RenderError(f"cannot stage Helm chart: {error}") from error
    return extracted / next(iter(roots))


def _validate_staged_helm_chart(
    chart_root: Path,
    staging: Path,
) -> None:
    """Reject inputs whose output depends on runtime, cluster, or history."""

    def validate_schema_node(value: object, path: Path) -> None:
        if isinstance(value, list):
            for item in value:
                validate_schema_node(item, path)
            return
        if not isinstance(value, dict):
            return
        reference = value.get("$ref")
        if reference is not None and (
            not isinstance(reference, str)
            or not reference.startswith("#")
        ):
            raise RenderError(
                f"Helm values schema has a non-local $ref: {path}"
            )
        for identifier in ("$id", "id"):
            identifier_value = value.get(identifier)
            if identifier_value is not None and (
                not isinstance(identifier_value, str)
                or not identifier_value.startswith("#")
            ):
                raise RenderError(
                    "Helm values schema has a non-local identifier: "
                    f"{path}"
                )
        for item in value.values():
            validate_schema_node(item, path)

    for schema_path in sorted(chart_root.rglob("values.schema.json")):
        try:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RenderError(
                f"cannot parse Helm values schema {schema_path}: {error}"
            ) from error
        validate_schema_node(schema, schema_path)

    bare_identifier_prefix = r"(?<![A-Za-z0-9_.$])"
    function_pattern = re.compile(
        bare_identifier_prefix
        + r"(?:"
        + "|".join(
            sorted(
                (re.escape(name) for name in _DYNAMIC_HELM_FUNCTIONS),
                key=len,
                reverse=True,
            )
        )
        + r")\b"
    )
    dynamic_tokens = (
        re.compile(bare_identifier_prefix + r"lookup\b"),
        re.compile(bare_identifier_prefix + r"tpl\b"),
        re.compile(r"\.Capabilities\b"),
        re.compile(
            r"\.(?:APIVersions|KubeVersion|HelmVersion|"
            r"IsInstall|IsUpgrade|Revision)\b"
        ),
        re.compile(
            bare_identifier_prefix
            + r"(?:dig|pluck|pick|omit|keys|values|merge|"
            r"mergeOverwrite|deepCopy|mustDeepCopy|set|unset)\b"
        ),
        re.compile(
            bare_identifier_prefix
            + r"(?:index|get|hasKey)\b"
            r"(?!\s+\$?\.Values(?:\b|\.)).*"
        ),
        function_pattern,
    )
    stable_root_selector = re.compile(
        r"(?<![A-Za-z0-9_])\$?\."
        r"(?:Values|Chart|Files|Template)"
        r"(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
    )
    stable_release_selector = re.compile(
        r"(?<![A-Za-z0-9_])\$?\.Release\."
        r"(?:Name|Namespace|Service)\b"
    )

    def uses_unsupported_root_context(actions: str) -> bool:
        supported = stable_root_selector.sub(" STABLE ", actions)
        supported = stable_release_selector.sub(" STABLE ", supported)
        return bool(
            re.search(r"(?<![A-Za-z0-9_$])\.", supported)
            or re.search(r"\$(?![A-Za-z0-9_])", supported)
        )

    for child in sorted(chart_root.rglob("*"), key=str):
        if not child.is_file():
            continue
        if child.suffix.lower() == ".tgz":
            raise RenderError(
                f"packaged Helm dependency cannot be inspected offline: {child}"
            )
        if "templates" not in child.relative_to(chart_root).parts:
            continue
        try:
            template = child.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise RenderError(f"cannot read Helm template {child}: {error}") from error
        actions = _helm_template_actions(template)
        normalized_actions = _strip_helm_action_literals(actions)
        if uses_unsupported_root_context(normalized_actions) or any(
            pattern.search(normalized_actions) for pattern in dynamic_tokens
        ):
            raise RenderError(
                "Helm template requires unsupported cluster, lifecycle, "
                f"or nondeterministic state: {child}"
            )

    chart_metadata = chart_root / "Chart.yaml"
    if chart_metadata.is_file():
        metadata = _parse_helm_values(staging, [chart_metadata])
        if metadata.get("kubeVersion") not in (None, ""):
            raise RenderError(
                f"Helm chart requires unsupported cluster capabilities: {chart_metadata}"
            )


def _helm_values_parser_chart(staging: Path) -> Path:
    parser_chart = staging / "values-parser"
    parser_template = parser_chart / "templates" / "values.yaml"
    if parser_template.is_file():
        return parser_chart
    parser_template.parent.mkdir(parents=True)
    (parser_chart / "Chart.yaml").write_text(
        "apiVersion: v2\nname: values-parser\nversion: 0.1.0\n",
        encoding="utf-8",
    )
    parser_template.write_text(
        """
apiVersion: v1
kind: ConfigMap
metadata:
  name: rendered-values
data:
  values.json: {{ toJson .Values | quote }}
""".lstrip(),
        encoding="utf-8",
    )
    return parser_chart


def _parse_helm_values(
    staging: Path,
    values_files: Sequence[Path],
    assignment: tuple[str, str] | None = None,
) -> dict[str, Any]:
    parser_chart = _helm_values_parser_chart(staging)
    command = [
        _verified_tool("helm"),
        "template",
        "values-parser",
        str(parser_chart),
        "--show-only",
        "templates/values.yaml",
    ]
    for values_file in values_files:
        command.extend(("--values", str(values_file)))
    if assignment is not None:
        command.extend(assignment)
    documents = load_yaml_text(_run(command))
    if len(documents) != 1:
        raise RenderError("Helm values parser returned unexpected output")
    encoded = _mapping(documents[0].get("data")).get("values.json")
    if not isinstance(encoded, str):
        raise RenderError("Helm values parser omitted rendered values")
    try:
        values = json.loads(encoded)
    except json.JSONDecodeError as error:
        raise RenderError("Helm values parser returned invalid JSON") from error
    if not isinstance(values, dict):
        raise RenderError("Helm values parser returned non-mapping values")
    return values


def _helm_values_snapshot(
    staging: Path,
    values_files: Sequence[Path],
    target_overrides: Sequence[tuple[str, str, bool]],
) -> Path | None:
    """Apply targetPath entries in order using Helm's own strvals parser."""

    if not target_overrides:
        return None
    current_files = list(values_files)
    for position, (target_path, text, literal) in enumerate(target_overrides):
        flag = "--set"
        rendered_text = text
        if literal:
            rendered_text = (
                rendered_text.replace("\\", "\\\\")
                .replace(",", "\\,")
                .replace("[", "\\[")
                .replace("]", "\\]")
                .replace("{", "\\{")
                .replace("}", "\\}")
            )
            flag = "--set-string"
        elif (
            rendered_text.startswith(("'", '"'))
            and rendered_text.endswith(("'", '"'))
        ):
            rendered_text = rendered_text.strip("'\"")
            flag = "--set-string"
        values = _parse_helm_values(
            staging,
            current_files,
            (flag, f"{target_path}={rendered_text}"),
        )
        snapshot = staging / f"target-path-{position}.json"
        snapshot.write_text(
            json.dumps(values, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        current_files = [snapshot]
    return current_files[0]


def _helm_resource_reconciles(
    value: Mapping[str, Any],
    release_spec: Mapping[str, Any],
) -> bool:
    annotations = _mapping(_mapping(value.get("metadata")).get("annotations"))
    raw_hook = annotations.get("helm.sh/hook")
    if not isinstance(raw_hook, str) or not raw_hook.strip():
        return True
    phases = {
        phase.strip()
        for phase in raw_hook.split(",")
        if phase.strip()
    }
    known_phases = {
        "pre-install",
        "post-install",
        "pre-upgrade",
        "post-upgrade",
        "pre-delete",
        "post-delete",
        "pre-rollback",
        "post-rollback",
        "test",
        "test-success",
    }
    unknown = phases - known_phases
    if unknown:
        raise RenderError(
            "Helm resource uses unsupported hook phase: "
            + ", ".join(sorted(unknown))
        )
    active_controller = (
        value.get("kind") in {"Kustomization", "HelmRelease"}
        and _mapping_has_expected_api_version(value)
        and not bool(_mapping(value.get("spec")).get("suspend"))
    )
    install_hooks = not bool(
        _mapping(release_spec.get("install")).get("disableHooks")
    )
    upgrade_hooks = not bool(
        _mapping(release_spec.get("upgrade")).get("disableHooks")
    )
    if install_hooks and phases & {"pre-install", "post-install"}:
        if active_controller:
            raise RenderError(
                "Helm install hook contains an active nested GitOps controller"
            )
        if has_iceberg_bootstrap_identity(value):
            raise RenderError(
                "bootstrap identity depends on Helm install hook history"
            )
        return False
    if upgrade_hooks and phases & {"pre-upgrade", "post-upgrade"}:
        if active_controller:
            raise RenderError(
                "Helm upgrade hook contains an active nested GitOps controller"
            )
        if has_iceberg_bootstrap_identity(value):
            raise RenderError(
                "bootstrap identity depends on Helm upgrade hook history"
            )
        return False
    if (
        bool(_mapping(release_spec.get("test")).get("enable"))
        and phases & {"test", "test-success"}
    ):
        test_spec = _mapping(release_spec.get("test"))
        filters = _sequence(test_spec.get("filters"))
        excluded = {
            str(_mapping(item).get("name"))
            for item in filters
            if bool(_mapping(item).get("exclude"))
        }
        included = {
            str(_mapping(item).get("name"))
            for item in filters
            if not bool(_mapping(item).get("exclude"))
        }
        name = str(_mapping(value.get("metadata")).get("name") or "")
        selected = name not in excluded and (not included or name in included)
        if selected and active_controller:
            raise RenderError(
                "Helm test hook contains an active nested GitOps controller"
            )
        return selected
    return False


_POST_RENDER_PATCH_TARGET_FIELDS = frozenset(
    {
        "annotationSelector",
        "group",
        "kind",
        "labelSelector",
        "name",
        "namespace",
        "version",
    }
)
_POST_RENDER_IMAGE_FIELDS = frozenset(
    {"digest", "name", "newName", "newTag"}
)


def _helm_post_renderer_transform(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RenderError("Helm post-renderer must be a mapping")
    if set(value) != {"kustomize"}:
        raise RenderError("unsupported Helm post-renderer")
    kustomize = value.get("kustomize")
    if not isinstance(kustomize, dict):
        raise RenderError("Helm Kustomize post-renderer must be a mapping")
    if set(kustomize) - {"patches", "images"}:
        raise RenderError("unsupported Helm Kustomize post-renderer field")

    transform: dict[str, Any] = {}
    if "patches" in kustomize:
        patches = kustomize["patches"]
        if not isinstance(patches, list):
            raise RenderError("Helm post-renderer patches must be a list")
        validated_patches: list[dict[str, Any]] = []
        for patch in patches:
            if not isinstance(patch, dict):
                raise RenderError("Helm post-renderer patch must be a mapping")
            if set(patch) - {"patch", "target"} or "patch" not in patch:
                raise RenderError("unsupported Helm post-renderer patch field")
            if not isinstance(patch["patch"], str):
                raise RenderError("Helm post-renderer patch must be inline text")
            validated = {"patch": patch["patch"]}
            if "target" in patch:
                target = patch["target"]
                if not isinstance(target, dict):
                    raise RenderError(
                        "Helm post-renderer patch target must be a mapping"
                    )
                if set(target) - _POST_RENDER_PATCH_TARGET_FIELDS:
                    raise RenderError(
                        "unsupported Helm post-renderer patch target field"
                    )
                if any(not isinstance(item, str) for item in target.values()):
                    raise RenderError(
                        "Helm post-renderer patch target values must be strings"
                    )
                validated["target"] = dict(target)
            validated_patches.append(validated)
        transform["patches"] = validated_patches

    if "images" in kustomize:
        images = kustomize["images"]
        if not isinstance(images, list):
            raise RenderError("Helm post-renderer images must be a list")
        validated_images: list[dict[str, str]] = []
        for image in images:
            if not isinstance(image, dict):
                raise RenderError("Helm post-renderer image must be a mapping")
            if (
                set(image) - _POST_RENDER_IMAGE_FIELDS
                or "name" not in image
                or not isinstance(image["name"], str)
                or not image["name"]
            ):
                raise RenderError("unsupported Helm post-renderer image field")
            if any(not isinstance(item, str) for item in image.values()):
                raise RenderError(
                    "Helm post-renderer image values must be strings"
                )
            validated_images.append(dict(image))
        transform["images"] = validated_images
    return transform


def _safe_repository_values_file(
    context: RepositoryContext,
    value: str,
) -> Path:
    if not value or _is_remote_reference(value):
        raise RenderError(f"remote Helm values file is not allowed: {value}")
    return _safe_source_path(context, value)


def _render_helm_release(
    release: Resource,
    index: Mapping[tuple[str, str, str], Resource],
    context: RepositoryContext,
) -> list[Resource]:
    raw_spec = release.value.get("spec")
    if not isinstance(raw_spec, dict):
        raise RenderError("HelmRelease spec must be a mapping")
    spec = raw_spec
    if "suspend" in spec and not isinstance(spec["suspend"], bool):
        raise RenderError("HelmRelease suspend must be a boolean")
    for field in ("valuesFrom", "postRenderers"):
        if field in spec and not isinstance(spec[field], list):
            raise RenderError(f"HelmRelease {field} must be a list")
    for field in ("values", "commonMetadata", "install", "upgrade", "test"):
        if field in spec and not isinstance(spec[field], dict):
            raise RenderError(f"HelmRelease {field} must be a mapping")
    for action in ("install", "upgrade"):
        action_spec = _mapping(spec.get(action))
        if (
            "disableHooks" in action_spec
            and not isinstance(action_spec["disableHooks"], bool)
        ):
            raise RenderError(
                f"HelmRelease {action}.disableHooks must be a boolean"
            )
    test_spec = _mapping(spec.get("test"))
    if set(test_spec) - {"enable", "filters", "ignoreFailures", "timeout"}:
        raise RenderError("unsupported HelmRelease test field")
    if (
        "enable" in test_spec
        and not isinstance(test_spec["enable"], bool)
    ):
        raise RenderError("HelmRelease test.enable must be a boolean")
    if (
        "ignoreFailures" in test_spec
        and not isinstance(test_spec["ignoreFailures"], bool)
    ):
        raise RenderError(
            "HelmRelease test.ignoreFailures must be a boolean"
        )
    if (
        "timeout" in test_spec
        and not isinstance(test_spec["timeout"], str)
    ):
        raise RenderError("HelmRelease test.timeout must be a string")
    if "filters" in test_spec:
        filters = test_spec["filters"]
        if not isinstance(filters, list):
            raise RenderError("HelmRelease test.filters must be a list")
        for filter_value in filters:
            if not isinstance(filter_value, dict):
                raise RenderError(
                    "HelmRelease test filter must be a mapping"
                )
            if (
                set(filter_value) - {"name", "exclude"}
                or not isinstance(filter_value.get("name"), str)
                or not 1 <= len(filter_value["name"]) <= 253
            ):
                raise RenderError("unsupported HelmRelease test filter")
            if (
                "exclude" in filter_value
                and not isinstance(filter_value["exclude"], bool)
            ):
                raise RenderError(
                    "HelmRelease test filter exclude must be a boolean"
                )
    upgrade_spec = _mapping(spec.get("upgrade"))
    if (
        "preserveValues" in upgrade_spec
        and not isinstance(upgrade_spec["preserveValues"], bool)
    ):
        raise RenderError(
            "HelmRelease upgrade.preserveValues must be a boolean"
        )
    if bool(upgrade_spec.get("preserveValues")):
        raise RenderError(
            "HelmRelease upgrade.preserveValues depends on release history"
        )
    if bool(spec.get("suspend")):
        return []
    post_renderer_transforms = [
        _helm_post_renderer_transform(value)
        for value in _sequence(spec.get("postRenderers"))
    ]
    raw_post_render_strategy = spec.get("postRenderStrategy")
    if (
        raw_post_render_strategy is not None
        and not isinstance(raw_post_render_strategy, str)
    ):
        raise RenderError("HelmRelease postRenderStrategy must be a string")
    post_render_strategy = raw_post_render_strategy or "combined"
    if post_render_strategy != "combined":
        raise RenderError(
            "unsupported Helm postRenderStrategy: "
            f"{post_render_strategy}"
        )
    chart_source = _chart_source(release, index)
    if chart_source is None:
        raise RenderError(
            "HelmRelease chart source is unresolved: "
            f"{release.namespace}/{release.name}"
        )
    (
        chart_name,
        source,
        selected_values_files,
        ignore_missing_values_files,
    ) = chart_source
    if not _is_local_git_source(source, context):
        raise RenderError(
            "HelmRelease chart source is outside RepositoryContext: "
            f"{release.namespace}/{release.name}"
        )
    chart_root = _safe_source_path(context, chart_name)
    if not (chart_root / "Chart.yaml").is_file():
        raise RenderError(f"local Helm chart is missing Chart.yaml: {chart_root}")
    _preflight_helm_chart_tree(chart_root, context)

    raw_release_name = spec.get("releaseName")
    if raw_release_name is not None and (
        not isinstance(raw_release_name, str) or not raw_release_name
    ):
        raise RenderError("HelmRelease releaseName must be a non-empty string")
    raw_namespace = spec.get("targetNamespace")
    if raw_namespace is not None and (
        not isinstance(raw_namespace, str) or not raw_namespace
    ):
        raise RenderError(
            "HelmRelease targetNamespace must be a non-empty string"
        )
    release_name = raw_release_name or release.name
    namespace = raw_namespace or release.namespace
    if not release_name:
        raise RenderError("HelmRelease has no effective release name")

    with tempfile.TemporaryDirectory(prefix="shirokuma-helm-") as temporary:
        staging = Path(temporary)
        render_chart = _stage_helm_chart(chart_root, staging)
        _validate_staged_helm_chart(
            render_chart,
            staging / "metadata-parser",
        )
        selected_files: list[Path] = []
        for selected in selected_values_files:
            path = _safe_repository_values_file(context, selected)
            if not path.is_file():
                if ignore_missing_values_files:
                    continue
                raise RenderError(f"selected Helm values file is missing: {path}")
            selected_files.append(path)
        if selected_values_files:
            merged_chart_values = _parse_helm_values(staging, selected_files)
            try:
                (render_chart / "values.yaml").write_text(
                    json.dumps(merged_chart_values, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            except OSError as error:
                raise RenderError(
                    f"cannot stage Helm chart values: {error}"
                ) from error

        values_files: list[Path] = []

        target_overrides: list[tuple[str, str, bool]] = []
        for position, reference_value in enumerate(_sequence(spec.get("valuesFrom"))):
            reference = _mapping(reference_value)
            text = _values_source_text(release, reference, index)
            if text is None:
                continue
            target_path = reference.get("targetPath")
            if isinstance(target_path, str) and target_path:
                target_overrides.append(
                    (
                        target_path,
                        text,
                        bool(reference.get("literal")),
                    )
                )
                continue
            path = staging / f"values-from-{position}.yaml"
            path.write_text(text, encoding="utf-8")
            values_files.append(path)

        inline_values = spec.get("values")
        if isinstance(inline_values, dict):
            path = staging / "inline-values.json"
            path.write_text(
                json.dumps(inline_values, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            values_files.append(path)
        if target_overrides:
            snapshot = _helm_values_snapshot(
                staging,
                values_files,
                target_overrides,
            )
            if snapshot is None:
                raise RenderError("Helm targetPath values were not rendered")
            values_files = [snapshot]

        command = [
            _verified_tool("helm"),
            "template",
            release_name,
            str(render_chart),
            "--namespace",
            namespace,
            "--include-crds",
        ]
        if not bool(_mapping(spec.get("test")).get("enable")):
            command.append("--skip-tests")
        for values_file in values_files:
            command.extend(("--values", str(values_file)))
        rendered = _run(command)

    for transform in post_renderer_transforms:
        rendered = _apply_kustomize_transform(rendered, transform)

    common_metadata = _mapping(spec.get("commonMetadata"))
    if common_metadata:
        rendered = _apply_kustomize_transform(
            rendered,
            _flux_transform({"commonMetadata": common_metadata}),
        )
    rendered = _apply_kustomize_transform(
        rendered,
        _flux_transform(
            {
                "commonMetadata": {
                    "labels": {
                        "helm.toolkit.fluxcd.io/name": release.name,
                        "helm.toolkit.fluxcd.io/namespace": release.namespace,
                    }
                }
            }
        ),
    )
    values = load_yaml_text(rendered)
    for value in values:
        _validate_resource_api(value, release.path)
    return [
        Resource(release.path, value)
        for value in values
        if _helm_resource_reconciles(value, spec)
    ]


def iceberg_bootstrap_manifests(
    deploy_root: Path,
    context: RepositoryContext,
) -> list[Path]:
    repository = render_repository(deploy_root, context)
    resources = list(repository.resources)
    index = _resource_index(resources)
    rendered_helm: list[Resource] = []
    for resource in resources:
        if _is_helm_release(resource):
            rendered = _render_helm_release(resource, index, context)
            for child in rendered:
                if (
                    (_is_flux_kustomization(child) or _is_helm_release(child))
                    and not bool(_mapping(child.value.get("spec")).get("suspend"))
                ):
                    raise RenderError(
                        "Helm output contains an active nested GitOps controller: "
                        f"{child.kind}/{child.namespace}/{child.name}"
                    )
            rendered_helm.extend(rendered)
    paths = {
        resource.path
        for resource in (*resources, *rendered_helm)
        if has_iceberg_bootstrap_identity(resource.value)
    }
    return sorted(paths, key=str)


def _polaris_identity_values(resource: Mapping[str, Any]) -> Iterator[str]:
    metadata = _mapping(resource.get("metadata"))
    name = metadata.get("name")
    if isinstance(name, str):
        yield name
    for value in _mapping(metadata.get("labels")).values():
        if isinstance(value, str):
            yield value
    for path in _POLARIS_CONTAINER_PATHS:
        for container in _sequence(_nested(resource, path)):
            mapping = _mapping(container)
            name = mapping.get("name")
            if isinstance(name, str):
                yield name


def _container_images(
    resource: Mapping[str, Any],
    paths: Sequence[Sequence[str]],
) -> Iterator[str]:
    for path in paths:
        for container in _sequence(_nested(resource, path)):
            image = _mapping(container).get("image")
            if isinstance(image, str):
                yield image


def polaris_workload_manifests(
    deploy_root: Path,
    charts_root: Path,
    admitted_images: set[str],
) -> list[Path]:
    resources = load_manifest_resources(deploy_root)
    if charts_root.exists():
        for path in sorted(charts_root.rglob("*")):
            if (
                not path.is_file()
                or path.suffix.lower() not in MANIFEST_SUFFIXES
                or path.name in {"Chart.yaml", "values.yaml"}
            ):
                continue
            text = path.read_text(encoding="utf-8")
            if "{{" in text or "}}" in text:
                continue
            for value in load_yaml_text(text):
                _validate_resource_api(value, path)
                resources.append(Resource(path.resolve(), value))
    paths = {
        resource.path
        for resource in resources
        if resource.kind in POLARIS_WORKLOAD_KINDS
        and _has_expected_api_version(resource)
        and any("polaris" in value.lower() for value in _polaris_identity_values(resource.value))
        and any(
            image in admitted_images
            for image in _container_images(resource.value, _POLARIS_CONTAINER_PATHS)
        )
    }
    return sorted(paths, key=str)
