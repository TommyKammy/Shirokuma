#!/usr/bin/env python3
"""Verify the closed-world SeaweedFS trusted-image admission contract."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import package_go_vendor


CONTRACT_PATH = Path("bootstrap/seaweedfs/v4.39/trusted-build-contract.json")
SOURCE_PATH = Path("bootstrap/seaweedfs/v4.39/source.json")
RELEASE_PATH = Path("bootstrap/seaweedfs/v4.39/release-evidence.json")
ADMISSION_PATH = Path("bootstrap/seaweedfs/v4.39/admission.json")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REKOR_UUID_RE = re.compile(r"^[0-9a-fA-F]{64}(?:[0-9a-fA-F]{16})?$")
REMOTE_ACTION_RE = re.compile(
    r"^\s*(?:-\s+)?uses:\s+([^\s@]+)@([0-9a-f]{40})(?:\s+#.*)?$"
)
USES_LINE_RE = re.compile(r"^\s*(?:-\s+)?uses:")
STEP_NAME_RE = re.compile(r"^ {6}- name:\s*(.+?)\s*$", re.MULTILINE)


class ContractError(RuntimeError):
    """A stable, reviewable contract failure."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _fail(code: str, detail: str) -> None:
    raise ContractError(code, detail)


def _expect(condition: bool, code: str, detail: str) -> None:
    if not condition:
        _fail(code, detail)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _fail("EVIDENCE_MISSING", path.as_posix())
    except json.JSONDecodeError as exc:
        _fail("EVIDENCE_JSON", f"{path.as_posix()}: {exc}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_retained_cosign_bundle(
    contract: Dict[str, Any],
    release: Dict[str, Any],
    bundle_path: Path,
    manifest_path: Path,
) -> None:
    """Cryptographically reverify retained Git evidence with pinned Cosign."""

    cosign = shutil.which("cosign")
    _expect(cosign is not None, "COSIGN_BINARY", "cosign is required")
    expected_version = contract["toolchain"]["cosign"]["version"]
    try:
        version_result = subprocess.run(
            [cosign, "version", "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _fail("COSIGN_VERSION", str(exc))
    _expect(
        version_result.returncode == 0,
        "COSIGN_VERSION",
        version_result.stderr.strip() or "version command failed",
    )
    try:
        version_record = json.loads(version_result.stdout)
    except json.JSONDecodeError as exc:
        _fail("COSIGN_VERSION", str(exc))
    _expect(
        isinstance(version_record, dict),
        "COSIGN_VERSION",
        "version output must be a JSON object",
    )
    actual_version = version_record.get("gitVersion")
    _expect(
        actual_version == expected_version,
        "COSIGN_VERSION",
        f"expected {expected_version}, got {actual_version}",
    )

    builder = release["builder"]
    command = [
        cosign,
        "verify-blob",
        "--bundle",
        str(bundle_path),
        "--certificate-identity",
        release["identity"],
        "--certificate-oidc-issuer",
        release["issuer"],
        "--certificate-github-workflow-name",
        builder["workflow_name"],
        "--certificate-github-workflow-ref",
        builder["ref"],
        "--certificate-github-workflow-repository",
        builder["repository"],
        "--certificate-github-workflow-sha",
        builder["workflow_sha"],
        str(manifest_path),
    ]
    try:
        verification = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _fail("COSIGN_CRYPTO_VERIFY", str(exc))
    _expect(
        verification.returncode == 0,
        "COSIGN_CRYPTO_VERIFY",
        verification.stderr.strip()
        or verification.stdout.strip()
        or "retained bundle verification failed",
    )


def _safe_repo_path(root: Path, relative: str, code: str) -> Path:
    candidate = root / relative
    try:
        candidate.resolve(strict=False).relative_to(root.resolve())
    except ValueError:
        _fail(code, f"path escapes repository: {relative}")
    _expect(not candidate.is_symlink(), code, f"symlink is forbidden: {relative}")
    return candidate


def _digest_hex(reference: str) -> str:
    marker = "@sha256:"
    _expect(marker in reference, "RELEASE_REFERENCE", reference)
    digest = reference.split(marker, 1)[1]
    _expect(bool(SHA256_RE.fullmatch(digest)), "RELEASE_REFERENCE", reference)
    return digest


def _containerfile_logical_instructions(
    lines: Sequence[str],
) -> List[tuple[int, str]]:
    logical_instructions: List[tuple[int, str]] = []
    continued_parts: List[str] = []
    continued_from_line = 0
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not continued_parts and (not line or line.startswith("#")):
            continue
        _expect(
            not (continued_parts and (not line or line.startswith("#"))),
            "CONTAINERFILE_LOGICAL_LINES",
            f"ambiguous blank or comment in continuation at line {line_number}",
        )
        _expect(
            "<<" not in line,
            "CONTAINERFILE_HEREDOC",
            f"heredoc syntax is outside the reviewed grammar at line {line_number}",
        )
        trailing_escapes = len(line) - len(line.rstrip("\\"))
        _expect(
            trailing_escapes <= 1,
            "CONTAINERFILE_LOGICAL_LINES",
            f"ambiguous trailing escapes at line {line_number}",
        )
        if not continued_parts:
            continued_from_line = line_number
        if trailing_escapes == 1:
            continued_parts.append(line[:-1])
            continue
        continued_parts.append(line)
        logical_instruction = "".join(continued_parts)
        _expect(
            "<<" not in logical_instruction,
            "CONTAINERFILE_HEREDOC",
            f"split heredoc syntax at line {continued_from_line}",
        )
        logical_instructions.append(
            (
                continued_from_line,
                logical_instruction,
            )
        )
        continued_parts = []
    _expect(
        not continued_parts,
        "CONTAINERFILE_LOGICAL_LINES",
        f"unterminated continuation from line {continued_from_line}",
    )
    return logical_instructions


def validate_containerfile_build_inputs(
    container_text: str,
    build_inputs: Dict[str, Any],
    frontend: str,
) -> None:
    """Bind reviewed image pins to the Dockerfile instructions that consume them."""

    lines = container_text.splitlines()
    expected_directive = f"# syntax={frontend}"
    parser_directives = [
        line.strip()
        for line in lines
        if re.match(
            r"^#\s*(?:syntax|escape|check)\s*=",
            line.strip(),
            re.IGNORECASE,
        )
    ]
    _expect(
        bool(lines)
        and lines[0] == expected_directive
        and parser_directives == [expected_directive],
        "CONTAINERFILE_SYNTAX_DIRECTIVE",
        "the first line must be the sole reviewed syntax directive",
    )

    logical_instructions = _containerfile_logical_instructions(lines)

    global_args: Dict[str, str] = {}
    from_instructions: List[str] = []
    stage_instructions: List[List[str]] = []
    before_first_from = True
    for line_number, line in logical_instructions:
        keyword = line.split(None, 1)[0].upper()
        if keyword == "FROM":
            before_first_from = False
            from_instructions.append(line)
            stage_instructions.append([])
            continue
        if not before_first_from:
            stage_instructions[-1].append(line)
            continue
        _expect(
            keyword == "ARG",
            "CONTAINERFILE_GLOBAL_ARGS",
            f"unexpected global instruction at line {line_number}",
        )
        match = re.fullmatch(r"ARG\s+([A-Z_][A-Z0-9_]*)=(\S+)", line)
        _expect(
            match is not None,
            "CONTAINERFILE_GLOBAL_ARGS",
            f"invalid global ARG at line {line_number}",
        )
        name, value = match.groups()
        _expect(
            name not in global_args,
            "CONTAINERFILE_GLOBAL_ARGS",
            f"duplicate global ARG {name}",
        )
        global_args[name] = value

    expected_args = {
        "GO_IMAGE": build_inputs["go"],
        "RUNTIME_IMAGE": build_inputs["certificates"],
    }
    _expect(
        global_args == expected_args,
        "CONTAINERFILE_GLOBAL_ARGS",
        "global image ARG defaults differ from reviewed pins",
    )
    _expect(
        from_instructions
        == [
            "FROM --platform=$BUILDPLATFORM ${GO_IMAGE} AS builder",
            "FROM ${RUNTIME_IMAGE} AS certificates",
            "FROM scratch",
        ],
        "CONTAINERFILE_STAGE_PLAN",
        "build stages do not consume the reviewed image ARGs exactly",
    )

    expected_builder_prefix = [
        "ARG TARGETOS",
        "ARG TARGETARCH",
        "ARG SOURCE_COMMIT",
        "ARG GO_VENDOR_BUNDLE_SHA256",
        "WORKDIR /src",
        "COPY . .",
    ]
    builder_instructions = stage_instructions[0]
    _expect(
        len(builder_instructions) == len(expected_builder_prefix) + 1
        and builder_instructions[:-1] == expected_builder_prefix
        and builder_instructions[-1].startswith("RUN --network=none "),
        "CONTAINERFILE_BUILDER_STAGE",
        "builder stage instruction set or order differs",
    )
    expected_builder_run = (
        'RUN --network=none test -n "${SOURCE_COMMIT}" && '
        'test -n "${GO_VENDOR_BUNDLE_SHA256}" && '
        'echo "${GO_VENDOR_BUNDLE_SHA256}  '
        '/src/.shirokuma/go-vendor.tar.xz" | sha256sum -c - && '
        "tar -xJf /src/.shirokuma/go-vendor.tar.xz -C /src && "
        "test -f /src/vendor/modules.txt && rm -rf /src/.shirokuma && "
        "mkdir -p /out/data /out/tmp && cd /src/weed && env "
        'CGO_ENABLED=0 GOOS="${TARGETOS}" GOARCH="${TARGETARCH}" '
        "GOENV=off GOTOOLCHAIN=local GOWORK=off GOFLAGS=-mod=vendor "
        "GOPROXY=off GOSUMDB=off GOPRIVATE= GONOPROXY= GONOSUMDB= "
        "'GOVCS=*:off' go build -buildvcs=false -trimpath "
        '-ldflags="-s -w -extldflags -static -X '
        "github.com/seaweedfs/seaweedfs/weed/util/version.COMMIT="
        '${SOURCE_COMMIT}" -o /out/weed .'
    )
    _expect(
        builder_instructions[-1] == expected_builder_run,
        "CONTAINERFILE_BUILDER_RUN",
        "offline vendor verification and shell command must match exactly",
    )

    _expect(
        stage_instructions[1] == [],
        "CONTAINERFILE_CERTIFICATE_STAGE",
        "certificate stage must only provide the pinned base filesystem",
    )
    expected_final_instructions = [
        "ARG SOURCE_COMMIT",
        "ARG GO_VENDOR_BUNDLE_SHA256",
        (
            'LABEL org.opencontainers.image.title="Shirokuma SeaweedFS" '
            'org.opencontainers.image.version="4.39" '
            'org.opencontainers.image.source="https://github.com/seaweedfs/seaweedfs" '
            'org.opencontainers.image.revision="${SOURCE_COMMIT}" '
            'org.opencontainers.image.licenses="Apache-2.0" '
            'dev.shirokuma.go-vendor-bundle.sha256="${GO_VENDOR_BUNDLE_SHA256}"'
        ),
        (
            "COPY --from=certificates /etc/ssl/certs/ca-certificates.crt "
            "/etc/ssl/certs/ca-certificates.crt"
        ),
        "COPY --from=builder /out/weed /usr/bin/weed",
        "COPY --from=builder --chown=65532:65532 /out/data /data",
        "COPY --from=builder --chown=65532:65532 /out/tmp /tmp",
        (
            "COPY --from=builder --chown=65532:65532 /src/docker/filer.toml "
            "/etc/seaweedfs/filer.toml"
        ),
        "USER 65532:65532",
        "EXPOSE 7333 8333 8888 9333 9340 19333 23646",
        'ENTRYPOINT ["/usr/bin/weed"]',
        'CMD ["mini", "-dir=/data"]',
    ]
    _expect(
        stage_instructions[2] == expected_final_instructions,
        "CONTAINERFILE_FINAL_STAGE",
        "runtime stage instruction set or order differs",
    )


def load_contract(root: Path) -> Dict[str, Any]:
    contract = _load_json(root / CONTRACT_PATH)
    _expect(contract.get("schema_version") == 1, "CONTRACT_SCHEMA", "expected schema 1")
    _expect(contract.get("component") == "seaweedfs", "CONTRACT_COMPONENT", "seaweedfs")
    _expect(contract.get("version") == "4.39", "CONTRACT_VERSION", "4.39")
    _expect(contract.get("platform") == "linux/arm64", "CONTRACT_PLATFORM", "linux/arm64")
    return contract


def validate_static_contract(root: Path) -> Dict[str, Any]:
    root = root.resolve()
    contract = load_contract(root)
    source = _load_json(root / SOURCE_PATH)
    admission = _load_json(root / ADMISSION_PATH)

    source_contract = source.get("trusted_build_contract")
    _expect(
        set(contract.get("source", {}))
        == {"evidence", "containerfile", "module_inputs"},
        "SOURCE_CONTRACT_SCHEMA",
        "trusted source contract has unexpected or missing fields",
    )
    _expect(source.get("schema_version") == 3, "SOURCE_SCHEMA", "expected schema 3")
    _expect(source_contract == CONTRACT_PATH.as_posix(), "SOURCE_CONTRACT", str(source_contract))
    _expect(source.get("component") == contract["component"], "SOURCE_COMPONENT", "mismatch")
    _expect(source.get("version") == contract["version"], "SOURCE_VERSION", "mismatch")

    container = contract["source"]["containerfile"]
    container_path = _safe_repo_path(root, container["path"], "CONTAINERFILE_PATH")
    _expect(container_path.is_file(), "CONTAINERFILE_MISSING", container["path"])
    container_text = container_path.read_text(encoding="utf-8")
    actual_container_hash = _sha256(container_path)
    _expect(SHA256_RE.fullmatch(container["sha256"]) is not None, "CONTAINERFILE_HASH", "invalid")
    _expect(actual_container_hash == container["sha256"], "CONTAINERFILE_HASH", actual_container_hash)
    _expect(source.get("containerfile_sha256") == actual_container_hash, "SOURCE_CONTAINERFILE_HASH", "mismatch")
    build_inputs = source.get("build_inputs", {})
    _expect(
        isinstance(build_inputs, dict)
        and set(build_inputs) == {"dockerfile_frontend", "go", "certificates"},
        "SOURCE_BUILD_INPUTS",
        "expected exactly dockerfile_frontend, go, and certificates",
    )
    _expect(
        build_inputs.get("dockerfile_frontend") == container["frontend"],
        "FRONTEND_PIN",
        "source and contract differ",
    )
    for name, value in build_inputs.items():
        _expect(
            isinstance(value, str)
            and re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", value) is not None,
            "SOURCE_BUILD_INPUT_PIN",
            name,
        )
    validate_containerfile_build_inputs(
        container_text,
        build_inputs,
        container["frontend"],
    )

    module_inputs = source.get("module_inputs")
    contract_module_inputs = contract["source"].get("module_inputs")
    _expect(
        isinstance(module_inputs, dict)
        and set(module_inputs) == package_go_vendor.SOURCE_RECORD_MODULE_INPUT_KEYS
        and module_inputs == contract_module_inputs,
        "MODULE_INPUT_CONTRACT",
        "source and contract must contain the same closed Go module input set",
    )
    _expect(
        module_inputs["go_image"] == build_inputs["go"]
        and module_inputs["generator_policy"]
        == package_go_vendor.GENERATOR_POLICY,
        "MODULE_INPUT_CONTRACT",
        "Go image or vendor generator policy differs",
    )
    for field in (
        "go_mod_sha256",
        "go_sum_sha256",
        "bundle_sha256",
        "manifest_sha256",
    ):
        _expect(
            SHA256_RE.fullmatch(str(module_inputs[field])) is not None,
            "MODULE_INPUT_HASH",
            field,
        )
    _expect(
        module_inputs["module_count"] > 0
        and module_inputs["replacement_count"] >= 0
        and module_inputs["file_count"] > 0,
        "MODULE_INPUT_COUNT",
        "module, replacement, and file counts must be bounded",
    )
    module_root = SOURCE_PATH.parent
    bundle_path = _safe_repo_path(
        root,
        (module_root / module_inputs["bundle"]).as_posix(),
        "MODULE_INPUT_PATH",
    )
    manifest_path = _safe_repo_path(
        root,
        (module_root / module_inputs["manifest"]).as_posix(),
        "MODULE_INPUT_PATH",
    )
    _expect(bundle_path.is_file(), "MODULE_INPUT_MISSING", bundle_path.as_posix())
    _expect(manifest_path.is_file(), "MODULE_INPUT_MISSING", manifest_path.as_posix())
    _expect(
        _sha256(bundle_path) == module_inputs["bundle_sha256"]
        and _sha256(manifest_path) == module_inputs["manifest_sha256"],
        "MODULE_INPUT_HASH",
        "bundle or manifest differs from the trusted contract",
    )
    try:
        package_go_vendor.verify_package(
            archive_path=bundle_path,
            manifest_path=manifest_path,
            source_record_path=root / SOURCE_PATH,
            verify_archive_contents=False,
        )
    except package_go_vendor.VendorPackageError as error:
        _fail("MODULE_INPUT_PACKAGE", f"{error.code}: {error.detail}")
    toolchain = contract.get("toolchain", {})
    expected_tools = {"buildx", "buildkit", "syft", "trivy", "cosign", "crane"}
    _expect(set(toolchain) == expected_tools, "TOOLCHAIN_CLOSED_WORLD", repr(sorted(toolchain)))
    for name, record in toolchain.items():
        version = record.get("version", "")
        _expect(re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", version) is not None, "TOOL_VERSION", name)
    _expect(
        toolchain["cosign"]
        == {
            "version": "v3.1.1",
            "bundle_media_type": "application/vnd.dev.sigstore.bundle.v0.3+json",
            "predicate_type": "https://sigstore.dev/cosign/sign/v1",
            "registry_download_format": "sigstore-bundle-v0.3-jsonl",
            "legacy_signature_records_permitted": False,
            "detached_bundle_role": "bind-image-digest-to-raw-oci-manifest",
            "authoritative_image_verification": "cosign verify IMAGE@DIGEST",
        },
        "COSIGN_FORMAT_CONTRACT",
        "Cosign v3.1.1 bundle and image-verification semantics differ",
    )
    for tool, field in (
        ("buildx", "linux_arm64_sha256"),
        ("crane", "linux_arm64_archive_sha256"),
    ):
        value = toolchain[tool].get(field, "")
        _expect(SHA256_RE.fullmatch(value) is not None, "TOOL_ARCHIVE_HASH", tool)
    buildkit = toolchain["buildkit"]
    buildkit_digest = buildkit.get("image", "").rsplit("@sha256:", 1)[-1]
    _expect(SHA256_RE.fullmatch(buildkit_digest) is not None, "BUILDKIT_IMAGE_PIN", buildkit.get("image", ""))
    _expect(
        re.fullmatch(r"sha256:[0-9a-f]{64}", buildkit.get("linux_arm64_manifest_digest", "")) is not None,
        "BUILDKIT_ARM64_PIN",
        "missing",
    )

    workflow_record = contract["workflow"]
    _expect(
        workflow_record.get("runner_trust_boundary")
        == {
            "provider": "github-hosted",
            "unpinned_components": [
                "ubuntu-base-utilities",
                "docker-engine-and-cli",
                "github-cli",
            ],
            "recorded_evidence": [
                "runner-label-os-arch",
                "docker-client-version",
                "docker-server-version",
                "github-cli-version",
                "git-version",
                "python3-version",
                "curl-version",
                "tar-version",
                "sha256sum-version",
            ],
        },
        "RUNNER_TRUST_BOUNDARY",
        "unexpected provider, unpinned component, or evidence contract",
    )
    workflow_path = _safe_repo_path(root, workflow_record["path"], "WORKFLOW_PATH")
    workflow = workflow_path.read_text(encoding="utf-8")
    workflow_step_matches = list(STEP_NAME_RE.finditer(workflow))
    actual_steps = [match.group(1) for match in workflow_step_matches]
    workflow_step_positions = {
        match.group(1): match.start() for match in workflow_step_matches
    }
    _expect(
        len(workflow_step_positions) == len(workflow_step_matches),
        "WORKFLOW_STEP_CLOSED_WORLD",
        "duplicate step names are forbidden",
    )
    _expect(f"runs-on: {workflow_record['runner']}" in workflow, "WORKFLOW_RUNNER", workflow_record["runner"])
    _expect(
        workflow_record.get("workflow_sha_environment") == "GITHUB_WORKFLOW_SHA"
        and workflow_record.get("source_sha_environment") == "GITHUB_SHA",
        "WORKFLOW_SHA_SEMANTICS",
        "signer and source revisions must be distinct fields",
    )
    image_contract = contract.get("image", {})
    _expect(
        image_contract.get("repository") == "ghcr.io/tommykammy/shirokuma-seaweedfs"
        and image_contract.get("trusted_tag") == f"{contract['version']}-arm64"
        and image_contract.get("trusted_tag_role") == "non_authoritative_pointer"
        and image_contract.get("quarantine_tag_template")
        == "quarantine-{run_id}-{run_attempt}"
        and image_contract.get("registry_visibility_attempts") == 6,
        "IMAGE_CONTRACT",
        "unexpected repository, tag, or registry retry policy",
    )
    _expect(
        workflow_record.get("name") == "SeaweedFS 4.39 trusted arm64 build"
        and workflow_record.get("allowed_triggers") == ["push", "workflow_dispatch"]
        and workflow_record.get("allowed_refs") == ["refs/heads/main"],
        "WORKFLOW_IDENTITY_CONTRACT",
        "workflow name, trigger set, or publisher ref mismatch",
    )
    _expect(
        workflow_record.get("build_cache")
        == {
            "mode": "disabled",
            "no_cache": True,
            "cache_from": [],
            "cache_to": [],
        }
        and "no-cache: true" in workflow
        and "cache-from:" not in workflow
        and "cache-to:" not in workflow,
        "BUILD_CACHE_POLICY",
        "trusted builds must not import or export mutable BuildKit cache",
    )
    expected_build_arguments = [
        "SOURCE_COMMIT",
        "GO_VENDOR_BUNDLE_SHA256",
    ]
    expected_build_action_inputs = [
        "builder",
        "context",
        "file",
        "platforms",
        "push",
        "provenance",
        "sbom",
        "no-cache",
        "tags",
        "build-args",
    ]
    _expect(
        workflow_record.get("build_action_inputs")
        == expected_build_action_inputs
        and workflow_record.get("build_arguments") == expected_build_arguments,
        "BUILD_ACTION_POLICY",
        "build action input or argument contract differs from the closed set",
    )
    build_step_name = "Build and publish only linux/arm64"
    verify_step_name = "Verify the published platform"
    _expect(
        build_step_name in workflow_step_positions
        and verify_step_name in workflow_step_positions
        and workflow_step_positions[build_step_name]
        < workflow_step_positions[verify_step_name],
        "BUILD_ACTION_POLICY",
        "build or verification step boundary is ambiguous",
    )
    actual_build_step = workflow[
        workflow_step_positions[build_step_name] : workflow_step_positions[
            verify_step_name
        ]
    ].rstrip()
    expected_build_step = (
        "      - name: Build and publish only linux/arm64\n"
        "        id: build\n"
        "        uses: docker/build-push-action@"
        "10e90e3645eae34f1e60eeb005ba3a3d33f178e8 # v6\n"
        "        with:\n"
        "          builder: ${{ env.BUILDER_NAME }}\n"
        "          context: seaweedfs-src\n"
        f"          file: {container['path']}\n"
        f"          platforms: {contract['platform']}\n"
        "          push: true\n"
        "          provenance: false\n"
        "          sbom: false\n"
        "          no-cache: true\n"
        "          tags: ${{ env.IMAGE }}:quarantine-"
        "${{ github.run_id }}-${{ github.run_attempt }}\n"
        "          build-args: |\n"
        "            SOURCE_COMMIT=${{ env.SOURCE_COMMIT }}\n"
        "            GO_VENDOR_BUNDLE_SHA256="
        "${{ env.GO_VENDOR_BUNDLE_SHA256 }}"
    )
    _expect(
        actual_build_step == expected_build_step,
        "BUILD_ACTION_POLICY",
        "build action bytes differ from the reviewed closed invocation",
    )
    _expect(
        "      - codex/issue-41" not in workflow
        and workflow.count("github.ref == 'refs/heads/main'") >= 2,
        "MAIN_ONLY_PUBLICATION",
        "write-capable build and promotion jobs must be main-only",
    )
    transparency_log = contract.get("transparency_log", {})
    _expect(
        transparency_log.get("base_url") == "https://rekor.sigstore.dev"
        and transparency_log.get("major_api_version") == 1
        and transparency_log.get("entry_lookup_path")
        == "/api/v1/log/entries?logIndex={log_index}",
        "REKOR_API_CONTRACT",
        "unsupported transparency-log API",
    )
    _expect("imjasonh/setup-crane@" not in workflow, "CRANE_UNVERIFIED_SETUP", "setup-crane is forbidden")
    _expect("docker/setup-buildx-action@" not in workflow, "BUILDX_UNVERIFIED_SETUP", "setup-buildx is forbidden")
    _expect(
        'export DOCKER_CONFIG="${RUNNER_TEMP}/docker-config"' in workflow
        and 'plugin_dir="${DOCKER_CONFIG}/cli-plugins"' in workflow
        and 'echo "DOCKER_CONFIG=${DOCKER_CONFIG}" >> "${GITHUB_ENV}"' in workflow,
        "BUILDX_PLUGIN_DISCOVERY",
        "verified Buildx must be the first Docker CLI plugin candidate",
    )
    _expect(
        '--certificate-github-workflow-sha "${GITHUB_WORKFLOW_SHA}"' in workflow
        and '--signer-digest "${GITHUB_WORKFLOW_SHA}"' in workflow
        and '--source-digest "${GITHUB_SHA}"' in workflow
        and '"workflow_sha": os.environ["GITHUB_WORKFLOW_SHA"]' in workflow
        and '"source_sha": os.environ["GITHUB_SHA"]' in workflow,
        "WORKFLOW_SHA_SEMANTICS",
        "certificate, signer, and source revision fields are conflated",
    )
    _expect(
        f"  IMAGE: {image_contract['repository']}" in workflow
        and f"  TRUSTED_TAG: {image_contract['trusted_tag']}" in workflow,
        "IMAGE_WORKFLOW_BINDING",
        "workflow target differs from contract",
    )

    _expect(
        actual_steps == workflow_record.get("allowed_steps"),
        "WORKFLOW_STEP_CLOSED_WORLD",
        repr(actual_steps),
    )
    uses_lines = [line for line in workflow.splitlines() if USES_LINE_RE.match(line)]
    actual_actions: List[str] = []
    for line in uses_lines:
        match = REMOTE_ACTION_RE.fullmatch(line)
        _expect(match is not None, "ACTION_NOT_SHA_PINNED", line.strip())
        actual_actions.append(f"{match.group(1)}@{match.group(2)}")
    _expect(
        actual_actions == workflow_record.get("allowed_actions"),
        "WORKFLOW_ACTION_CLOSED_WORLD",
        repr(actual_actions),
    )

    required_literals = (
        toolchain["buildx"]["version"],
        toolchain["buildx"]["linux_arm64_url"],
        toolchain["buildx"]["linux_arm64_sha256"],
        toolchain["buildkit"]["version"],
        toolchain["buildkit"]["image"],
        toolchain["buildkit"]["linux_arm64_manifest_digest"],
        toolchain["syft"]["version"],
        toolchain["trivy"]["version"],
        toolchain["cosign"]["version"],
        toolchain["crane"]["version"],
        toolchain["crane"]["linux_arm64_archive_url"],
        toolchain["crane"]["linux_arm64_archive_sha256"],
        "cosign sign --yes --bundle cosign-signature-bundle.json",
        "cosign download signature",
        "registry-signature-bundles.jsonl",
        transparency_log["base_url"],
        "/api/v1/log/entries?logIndex=",
        "GITHUB_WORKFLOW_SHA",
        "seaweedfs-4.39-arm64-candidate-${{ github.run_id }}-${{ github.run_attempt }}",
        "seaweedfs-4.39-arm64-${{ github.run_id }}-${{ github.run_attempt }}",
        "--signer-workflow",
        "cosign-signature-bundle.json",
        "toolchain.json",
        "promotion-evidence.json",
        "scripts/verify_trusted_image.py promotion-preflight",
        'builder_run_id = str(release["builder"]["run_id"])',
        'builder_run_attempt = str(release["builder"]["run_attempt"])',
        f"IMAGE: {image_contract['repository']}",
        f"TRUSTED_TAG: {image_contract['trusted_tag']}",
        image_contract["quarantine_tag_template"].format(
            run_id="${{ github.run_id }}",
            run_attempt="${{ github.run_attempt }}",
        ),
        "for registry_attempt in 1 2 3 4 5 6; do",
    )
    for literal in required_literals:
        _expect(literal in workflow, "WORKFLOW_CONTRACT_LITERAL", literal)
    for allowed_ref in workflow_record["allowed_refs"]:
        _expect(allowed_ref in workflow, "WORKFLOW_REF_CONTRACT", allowed_ref)

    positions: List[int] = []
    for name in workflow_record["gate_order"]:
        _expect(
            name in workflow_step_positions,
            "WORKFLOW_GATE_MISSING",
            name,
        )
        positions.append(workflow_step_positions[name])
    _expect(positions == sorted(positions), "WORKFLOW_GATE_ORDER", "gate order differs from contract")
    _expect("needs: verify" in workflow, "PROMOTION_DEPENDENCY", "promote job must need verify")

    buildx_install = workflow_step_positions[
        "Install and verify pinned Buildx and BuildKit without credentials"
    ]
    quarantine_login = workflow_step_positions[
        "Log in to GHCR for the quarantine push"
    ]
    crane_install = workflow_step_positions[
        "Install and verify pinned Crane without credentials"
    ]
    promotion_login = workflow_step_positions[
        "Log in to GHCR for trusted-tag promotion"
    ]
    promotion_preflight = workflow_step_positions[
        "Revalidate candidate evidence before promotion credentials exist"
    ]
    promote_step = workflow_step_positions[
        "Promote the fully verified digest to the trusted tag"
    ]
    promotion_preflight_command = workflow.find(
        "scripts/verify_trusted_image.py promotion-preflight",
        promotion_preflight,
        crane_install,
    )
    promotion_tag = workflow.find(
        '"${CRANE_BIN}" tag',
        promote_step,
        workflow_step_positions["Verify final evidence after credentials are removed"],
    )
    _expect(buildx_install < quarantine_login, "BUILDX_CREDENTIAL_BOUNDARY", "install must precede login")
    _expect(crane_install < promotion_login, "CRANE_CREDENTIAL_BOUNDARY", "install must precede login")
    _expect(
        promotion_preflight_command >= promotion_preflight
        and promotion_tag >= promote_step
        and promotion_preflight < crane_install < promotion_login < promotion_tag,
        "PROMOTION_PREFLIGHT_ORDER",
        "candidate lineage and digest must be bound before promotion credentials",
    )
    _expect(
        "secrets.GITHUB_TOKEN" not in workflow[:quarantine_login]
        and "docker login" not in workflow[:quarantine_login],
        "BUILDX_CREDENTIAL_BOUNDARY",
        "credential material appears before builder verification",
    )
    promote_job = workflow.index("\n  promote:")
    _expect(
        "secrets.GITHUB_TOKEN" not in workflow[promote_job:promotion_login]
        and "docker login" not in workflow[promote_job:promotion_login],
        "CRANE_CREDENTIAL_BOUNDARY",
        "credential material appears before promotion-tool verification",
    )

    evidence_contract = contract["evidence"]
    candidate_retention = workflow[
        workflow_step_positions[
            "Retain candidate evidence before trusted-tag promotion"
        ] : workflow_step_positions["Remove the ephemeral Buildx builder"]
    ]
    final_retention = workflow[
        workflow_step_positions["Retain final promotion evidence"] :
    ]
    _expect(
        f"retention-days: {evidence_contract['candidate_retention_days']}" in candidate_retention,
        "CANDIDATE_RETENTION",
        "mismatch",
    )
    _expect(
        f"retention-days: {evidence_contract['final_retention_days']}" in final_retention,
        "FINAL_RETENTION",
        "mismatch",
    )

    expected_workflow_hash = workflow_record.get("sha256", "")
    _expect(
        SHA256_RE.fullmatch(expected_workflow_hash) is not None,
        "WORKFLOW_HASH",
        "contract hash is missing or invalid",
    )
    _expect(
        _sha256(workflow_path) == expected_workflow_hash,
        "WORKFLOW_HASH",
        "workflow bytes differ from the reviewed contract",
    )

    admission_contract = contract["admission"]
    _expect(
        set(admission_contract)
        == {
            "approval_state_source",
            "required_approved_state",
            "pending_state",
            "publisher_ref",
            "evidence_transition",
            "runtime_manifests_permitted",
            "runtime_unblocker",
        }
        and admission_contract["approval_state_source"]
        == ADMISSION_PATH.as_posix()
        and admission_contract["required_approved_state"] == "approved"
        and admission_contract["pending_state"] == "pending_main_publication"
        and admission_contract["publisher_ref"] == "refs/heads/main"
        and admission_contract["evidence_transition"]
        == "follow-up-evidence-only-pr",
        "ADMISSION_LIFECYCLE_CONTRACT",
        "publication and evidence transition must be main-only and two-phase",
    )
    _expect(
        admission.get("assessment", {}).get("admission")
        in {
            admission_contract["pending_state"],
            admission_contract["required_approved_state"],
        },
        "ADMISSION_ARTIFACT_STATE",
        "mismatch",
    )
    _expect(
        admission.get("runtime_manifests", {}).get("permitted")
        is admission_contract["runtime_manifests_permitted"],
        "ADMISSION_RUNTIME_STATE",
        "mismatch",
    )
    return contract


def _artifact_file(
    root: Path,
    evidence_dir: Optional[Path],
    name: str,
    metadata: Dict[str, Any],
) -> Path:
    if evidence_dir is not None:
        path = evidence_dir / name
        _expect(path.name == name and not path.is_symlink(), "EVIDENCE_PATH", name)
        return path
    recorded = metadata.get("path")
    _expect(isinstance(recorded, str), "EVIDENCE_PATH", name)
    return _safe_repo_path(root, recorded, "EVIDENCE_PATH")


def _decode_dsse_statement(bundle: Dict[str, Any]) -> Dict[str, Any]:
    envelope = bundle.get("dsseEnvelope")
    _expect(isinstance(envelope, dict), "COSIGN_DSSE", "missing dsseEnvelope")
    try:
        payload = base64.b64decode(envelope["payload"], validate=True)
        statement = json.loads(payload)
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        _fail("COSIGN_DSSE", str(exc))
    _expect(isinstance(statement, dict), "COSIGN_DSSE", "payload is not an object")
    return statement


def _validate_cosign(
    contract: Dict[str, Any],
    release: Dict[str, Any],
    verification_path: Path,
    bundle_path: Path,
    manifest_path: Path,
    registry_bundles_path: Path,
    rekor_path: Path,
    *,
    cryptographic_reverification: bool = False,
) -> Dict[str, Any]:
    verification = _load_json(verification_path)
    _expect(verification.get("schema_version") == 1, "COSIGN_VERIFY_SCHEMA", "expected schema 1")
    _expect(verification.get("reference") == release["reference"], "COSIGN_REFERENCE", "mismatch")
    constraints = verification.get("certificate_constraints", {})
    builder = release["builder"]
    _expect(
        constraints
        == {
            "issuer": release["issuer"],
            "identity": release["identity"],
            "github_workflow_name": builder["workflow_name"],
            "github_workflow_repository": builder["repository"],
            "github_workflow_ref": builder["ref"],
            "github_workflow_sha": builder["workflow_sha"],
            "github_workflow_trigger": builder["trigger"],
        },
        "COSIGN_CERTIFICATE_CONSTRAINT",
        "constraint set differs from the release builder identity",
    )
    payloads = verification.get("verified_payloads")
    _expect(isinstance(payloads, list) and payloads, "COSIGN_VERIFIED_PAYLOAD", "empty")
    digest = _digest_hex(release["reference"])

    def payload_digest(payload: Dict[str, Any]) -> Optional[str]:
        critical = payload.get("critical") or payload.get("Critical") or {}
        image = critical.get("image") or critical.get("Image") or {}
        return image.get("docker-manifest-digest") or image.get("Docker-manifest-digest")

    def payload_reference(payload: Dict[str, Any]) -> Optional[str]:
        critical = payload.get("critical") or payload.get("Critical") or {}
        identity = critical.get("identity") or critical.get("Identity") or {}
        return identity.get("docker-reference") or identity.get("Docker-reference")

    _expect(
        all(
            isinstance(payload, dict)
            and payload_digest(payload) == f"sha256:{digest}"
            and payload_reference(payload) == release["reference"]
            for payload in payloads
        ),
        "COSIGN_VERIFIED_PAYLOAD",
        "every verified payload must match the exact reference and digest",
    )
    _expect(
        verification.get("detached_bundle_verified") is True,
        "COSIGN_DETACHED_VERIFY",
        "missing",
    )
    _expect(verification.get("registry_signature_verified") is True, "COSIGN_REGISTRY_VERIFY", "missing")
    transparency_log = verification.get("transparency_log", {})
    expected_log = contract["transparency_log"]
    _expect(
        transparency_log.get("base_url") == expected_log["base_url"]
        and transparency_log.get("major_api_version") == expected_log["major_api_version"],
        "COSIGN_REKOR_API",
        "contract mismatch",
    )
    _expect(_sha256(manifest_path) == digest, "COSIGN_MANIFEST_DIGEST", "mismatch")

    bundle = _load_json(bundle_path)
    cosign_contract = contract["toolchain"]["cosign"]
    _expect(
        bundle.get("mediaType") == cosign_contract["bundle_media_type"],
        "COSIGN_BUNDLE_MEDIA",
        "v0.3 required",
    )
    try:
        registry_bundles = [
            json.loads(line)
            for line in registry_bundles_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except json.JSONDecodeError as exc:
        _fail("COSIGN_REGISTRY_BUNDLES", str(exc))
    _expect(
        all(
            isinstance(candidate, dict)
            and candidate.get("mediaType") == cosign_contract["bundle_media_type"]
            and "dsseEnvelope" in candidate
            and "messageSignature" not in candidate
            and "Base64Signature" not in candidate
            and "Payload" not in candidate
            for candidate in registry_bundles
        ),
        "COSIGN_REGISTRY_FORMAT",
        "legacy or non-DSSE registry signature record is forbidden",
    )
    _expect(
        len(registry_bundles) == 1 and registry_bundles[0] == bundle,
        "COSIGN_REGISTRY_BUNDLES",
        "the retained registry set must contain only the exact signed bundle",
    )
    exact_matches = sum(candidate == bundle for candidate in registry_bundles)
    _expect(exact_matches == 1, "COSIGN_REGISTRY_BUNDLE_MATCH", str(exact_matches))
    bundle_sha256 = _sha256(bundle_path)
    registry_bundle = verification.get("registry_bundle", {})
    _expect(
        registry_bundle.get("file") == registry_bundles_path.name
        and registry_bundle.get("exact_matches") == exact_matches
        and registry_bundle.get("bundle_sha256") == bundle_sha256,
        "COSIGN_REGISTRY_BUNDLE_RECORD",
        "identity mismatch",
    )
    material = bundle.get("verificationMaterial", {})
    raw_certificate = material.get("certificate", {}).get("rawBytes", "")
    try:
        certificate = base64.b64decode(raw_certificate, validate=True)
    except ValueError as exc:
        _fail("COSIGN_CERTIFICATE", str(exc))
    _expect(len(certificate) > 500, "COSIGN_CERTIFICATE", "certificate is missing")
    tlog_entries = material.get("tlogEntries")
    _expect(isinstance(tlog_entries, list) and len(tlog_entries) == 1, "COSIGN_REKOR", "exactly one tlog entry is required")
    for entry in tlog_entries:
        _expect(str(entry.get("logIndex", "")).isdigit(), "COSIGN_REKOR_INDEX", "missing")
        _expect(str(entry.get("integratedTime", "")).isdigit(), "COSIGN_REKOR_TIME", "missing")
        _expect(bool(entry.get("logId", {}).get("keyId")), "COSIGN_REKOR_LOG_ID", "missing")
        _expect(bool(entry.get("inclusionPromise", {}).get("signedEntryTimestamp")), "COSIGN_REKOR_SET", "missing")
        proof = entry.get("inclusionProof", {})
        _expect(
            bool(proof.get("checkpoint"))
            and bool(proof.get("rootHash"))
            and bool(proof.get("treeSize"))
            and isinstance(proof.get("hashes"), list),
            "COSIGN_REKOR_PROOF",
            "missing",
        )
    statement = _decode_dsse_statement(bundle)
    _expect(
        statement.get("predicateType") == cosign_contract["predicate_type"],
        "COSIGN_PREDICATE",
        "unexpected",
    )
    subjects = statement.get("subject", [])
    _expect(
        isinstance(subjects, list)
        and len(subjects) == 1
        and subjects[0].get("digest") == {"sha256": digest}
        and subjects[0].get("name", "") == ""
        and subjects[0].get("annotations", {}) == {},
        "COSIGN_SUBJECT",
        "exactly one digest-only subject is required",
    )
    recorded_rekor = verification.get("rekor_entries")
    _expect(isinstance(recorded_rekor, list) and len(recorded_rekor) == len(tlog_entries), "COSIGN_REKOR_RECORD", "mismatch")
    expected_lookup = expected_log["entry_lookup_path"].format(
        log_index=recorded_rekor[0].get("log_index", "")
    )
    _expect(
        transparency_log.get("entry_lookup") == expected_lookup,
        "COSIGN_REKOR_API",
        "entry lookup mismatch",
    )
    for recorded, raw in zip(recorded_rekor, tlog_entries):
        _expect(str(recorded.get("log_index")) == str(raw["logIndex"]), "COSIGN_REKOR_RECORD", "log index")
        _expect(str(recorded.get("integrated_time")) == str(raw["integratedTime"]), "COSIGN_REKOR_RECORD", "time")
        _expect(recorded.get("log_id") == raw["logId"]["keyId"], "COSIGN_REKOR_RECORD", "log id")

    rekor_response = _load_json(rekor_path)
    _expect(isinstance(rekor_response, dict) and len(rekor_response) == 1, "COSIGN_REKOR_RESPONSE", "expected one UUID")
    uuid, api_entry = next(iter(rekor_response.items()))
    raw = tlog_entries[0]
    _expect(
        REKOR_UUID_RE.fullmatch(uuid) is not None,
        "COSIGN_REKOR_UUID",
        uuid,
    )
    _expect(str(api_entry.get("logIndex")) == str(raw["logIndex"]), "COSIGN_REKOR_RESPONSE", "log index")
    _expect(str(api_entry.get("integratedTime")) == str(raw["integratedTime"]), "COSIGN_REKOR_RESPONSE", "time")
    _expect(api_entry.get("body") == raw.get("canonicalizedBody"), "COSIGN_REKOR_RESPONSE", "body")
    try:
        bundle_log_id_hex = base64.b64decode(raw["logId"]["keyId"], validate=True).hex()
    except ValueError as exc:
        _fail("COSIGN_REKOR_LOG_ID", str(exc))
    _expect(api_entry.get("logID") == bundle_log_id_hex, "COSIGN_REKOR_RESPONSE", "log id")
    _expect(recorded_rekor[0].get("uuid") == uuid, "COSIGN_REKOR_RECORD", "uuid")
    if cryptographic_reverification:
        _verify_retained_cosign_bundle(
            contract,
            release,
            bundle_path,
            manifest_path,
        )
    return verification


def _slsa_statement(record: Dict[str, Any]) -> Dict[str, Any]:
    try:
        payload = record["attestation"]["bundle"]["dsseEnvelope"]["payload"]
        return json.loads(base64.b64decode(payload))
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        _fail("SLSA_PAYLOAD", str(exc))


def _validate_slsa(release: Dict[str, Any], path: Path, bundles_path: Path) -> None:
    records = _load_json(path)
    _expect(isinstance(records, list) and records, "SLSA_RECORDS", "empty")
    try:
        retained_bundles = [
            json.loads(line)
            for line in bundles_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except json.JSONDecodeError as exc:
        _fail("SLSA_BUNDLES", str(exc))
    _expect(bool(retained_bundles), "SLSA_BUNDLES", "empty")
    retained_bundle_keys = [
        json.dumps(bundle, sort_keys=True, separators=(",", ":"))
        for bundle in retained_bundles
    ]
    _expect(
        len(set(retained_bundle_keys)) == len(retained_bundle_keys),
        "SLSA_BUNDLES",
        "duplicate retained bundle",
    )
    builder = release["builder"]
    repository = builder["repository"]
    workflow_path = builder["workflow"]
    workflow_ref = builder["ref"]
    workflow_sha = builder["workflow_sha"]
    source_sha = builder["source_sha"]
    identity = f"https://github.com/{repository}/{workflow_path}@{workflow_ref}"
    repository_url = f"https://github.com/{repository}"
    invocation = f"{repository_url}/actions/runs/{builder['run_id']}/attempts/{builder['run_attempt']}"
    digest = _digest_hex(release["reference"])
    image_repository = release["reference"].split("@", 1)[0]
    expected_subject = [
        {"name": image_repository, "digest": {"sha256": digest}}
    ]

    expected_bundle_keys: List[str] = []
    for index, record in enumerate(records):
        certificate = record.get("verificationResult", {}).get("signature", {}).get("certificate", {})
        statement = _slsa_statement(record)
        predicate = statement.get("predicate", {})
        definition = predicate.get("buildDefinition", {})
        workflow = definition.get("externalParameters", {}).get("workflow", {})
        details = predicate.get("runDetails", {})
        conditions = (
            statement.get("_type") == "https://in-toto.io/Statement/v1",
            statement.get("predicateType") == "https://slsa.dev/provenance/v1",
            definition.get("buildType") == "https://actions.github.io/buildtypes/workflow/v1",
            certificate.get("issuer") == release["issuer"],
            certificate.get("subjectAlternativeName") == identity,
            certificate.get("githubWorkflowName") == builder["workflow_name"],
            certificate.get("githubWorkflowRepository") == repository,
            certificate.get("githubWorkflowRef") == workflow_ref,
            certificate.get("githubWorkflowSHA") == workflow_sha,
            certificate.get("githubWorkflowTrigger") == builder["trigger"],
            certificate.get("buildTrigger") == builder["trigger"],
            certificate.get("runnerEnvironment") == "github-hosted",
            certificate.get("buildSignerURI") == identity,
            certificate.get("buildSignerDigest") == workflow_sha,
            certificate.get("buildConfigURI") == identity,
            certificate.get("buildConfigDigest") == workflow_sha,
            certificate.get("sourceRepositoryURI") == repository_url,
            certificate.get("sourceRepositoryDigest") == source_sha,
            certificate.get("sourceRepositoryRef") == workflow_ref,
            certificate.get("runInvocationURI") == invocation,
            workflow.get("repository") == repository_url,
            workflow.get("path") == workflow_path,
            workflow.get("ref") == workflow_ref,
            details.get("builder", {}).get("id") == identity,
            details.get("metadata", {}).get("invocationId") == invocation,
            statement.get("subject") == expected_subject,
        )
        _expect(
            all(conditions),
            "SLSA_WORKFLOW_IDENTITY",
            f"record {index} is not bound to the exact workflow, source, run, and subject",
        )
        expected_bundle_keys.append(
            json.dumps(
                record["attestation"]["bundle"],
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    _expect(
        retained_bundle_keys == expected_bundle_keys,
        "SLSA_BUNDLES",
        "retained bundle order or multiset differs from the verified records",
    )


def _validate_sbom(release: Dict[str, Any], contract: Dict[str, Any], path: Path) -> None:
    sbom = _load_json(path)
    _expect(sbom.get("bomFormat") == "CycloneDX", "SBOM_FORMAT", "CycloneDX required")
    _expect(isinstance(sbom.get("components"), list) and sbom["components"], "SBOM_COMPONENTS", "empty")
    component = sbom.get("metadata", {}).get("component", {})
    repository = release["reference"].split("@", 1)[0]
    _expect(
        component.get("type") == "container"
        and component.get("name") == repository
        and component.get("version") == release["digest"],
        "SBOM_SUBJECT",
        "container identity mismatch",
    )
    tools = sbom.get("metadata", {}).get("tools", {}).get("components", [])
    syft_versions = [
        "v" + item.get("version", "").removeprefix("v")
        for item in tools
        if item.get("name") == "syft"
    ]
    _expect(
        syft_versions == [contract["toolchain"]["syft"]["version"]],
        "SBOM_TOOLCHAIN",
        repr(syft_versions),
    )


def _validate_trivy(
    release: Dict[str, Any],
    contract: Dict[str, Any],
    report_path: Path,
    metadata_path: Path,
) -> None:
    report = _load_json(report_path)
    metadata = _load_json(metadata_path)
    _expect(report.get("ArtifactType") == "container_image", "TRIVY_ARTIFACT_TYPE", "unexpected")
    repo_digests = report.get("Metadata", {}).get("RepoDigests") or []
    if repo_digests:
        _expect(release["reference"] in repo_digests, "TRIVY_SUBJECT", "RepoDigests mismatch")
    else:
        _expect(report.get("ArtifactName") == release["reference"], "TRIVY_SUBJECT", "ArtifactName mismatch")
    results = report.get("Results")
    _expect(isinstance(results, list) and results, "TRIVY_RESULTS", "empty")
    severities = [
        vulnerability.get("Severity", "").upper()
        for result in results
        for vulnerability in (result.get("Vulnerabilities") or [])
    ]
    counts = {
        "critical": severities.count("CRITICAL"),
        "high": severities.count("HIGH"),
    }
    _expect(counts == release.get("vulnerabilities"), "TRIVY_VULNERABILITIES", repr(counts))
    _expect(counts == {"critical": 0, "high": 0}, "TRIVY_GATE", repr(counts))

    actual_version = "v" + metadata.get("Version", "").removeprefix("v")
    _expect(
        actual_version == contract["toolchain"]["trivy"]["version"],
        "TRIVY_TOOLCHAIN",
        actual_version,
    )
    db = metadata.get("VulnerabilityDB", {})
    release_scanner = release.get("scanner", {})
    release_db = release_scanner.get("vulnerability_db", {})
    _expect(
        set(release_scanner) == {"name", "version", "vulnerability_db"}
        and set(release_db) == {"version", "updated_at", "downloaded_at"},
        "TRIVY_METADATA",
        "unexpected or missing scanner metadata field",
    )
    _expect(
        release_scanner.get("name") == "trivy"
        and "v" + release_scanner.get("version", "").removeprefix("v") == actual_version
        and release_db.get("version") == db.get("Version")
        and release_db.get("updated_at") == db.get("UpdatedAt")
        and release_db.get("downloaded_at") == db.get("DownloadedAt"),
        "TRIVY_METADATA",
        "release record mismatch",
    )


def _validate_toolchain(contract: Dict[str, Any], path: Path) -> Dict[str, Any]:
    actual = _load_json(path)
    _expect(actual.get("schema_version") == 1, "TOOLCHAIN_SCHEMA", "expected schema 1")
    _expect(
        set(actual)
        == {
            "schema_version",
            "runner",
            "runner_tools",
            "credential_boundary",
            "tools",
        },
        "TOOLCHAIN_RECORD_CLOSED_WORLD",
        "unexpected or missing top-level field",
    )
    runner = actual.get("runner", {})
    _expect(
        runner
        == {
            "label": contract["workflow"]["runner"],
            "os": "Linux",
            "arch": "ARM64",
        },
        "TOOLCHAIN_RUNNER",
        "runner label, OS, or architecture mismatch",
    )
    runner_tools = actual.get("runner_tools", {})
    _expect(
        isinstance(runner_tools, dict)
        and set(runner_tools)
        == {
            "docker_client",
            "docker_server",
            "github_cli",
            "git",
            "python3",
            "curl",
            "tar",
            "sha256sum",
        }
        and all(
            isinstance(value, str) and bool(value.strip())
            for value in runner_tools.values()
        ),
        "TOOLCHAIN_RUNNER_VERSION",
        "runner tool set or version is missing",
    )
    expected = contract["toolchain"]
    _expect(set(actual.get("tools", {})) == set(expected), "TOOLCHAIN_CLOSED_WORLD", "actual tool set differs")
    expected_tools = {
        "buildx": {
            "version": expected["buildx"]["version"],
            "binary_sha256": expected["buildx"]["linux_arm64_sha256"],
            "execution": expected["buildx"]["execution"],
        },
        "buildkit": {
            "version": expected["buildkit"]["version"],
            "image": expected["buildkit"]["image"],
            "linux_arm64_manifest_digest": expected["buildkit"][
                "linux_arm64_manifest_digest"
            ],
        },
        "syft": {"version": expected["syft"]["version"]},
        "trivy": {"version": expected["trivy"]["version"]},
        "cosign": {"version": expected["cosign"]["version"]},
        "crane": {
            "version": expected["crane"]["version"],
            "archive_sha256": expected["crane"]["linux_arm64_archive_sha256"],
            "execution": "deferred_to_promotion_job",
        },
    }
    _expect(
        actual["tools"] == expected_tools,
        "TOOLCHAIN_RECORD_CLOSED_WORLD",
        "tool record differs from the complete pinned contract",
    )
    _expect(
        actual.get("credential_boundary")
        == "standalone archives verified before credentialed execution",
        "TOOL_CREDENTIAL_BOUNDARY",
        "missing",
    )
    return actual


def _validate_runtime(
    release: Dict[str, Any],
    smoke_path: Path,
    inspect_path: Path,
) -> None:
    inspect_payload = _load_json(inspect_path)
    _expect(
        isinstance(inspect_payload, list) and len(inspect_payload) == 1,
        "RUNTIME_INSPECT_SCHEMA",
        "expected exactly one inspected container",
    )
    container = inspect_payload[0]
    _expect(isinstance(container, dict), "RUNTIME_INSPECT_SCHEMA", "container is not an object")
    config = container.get("Config", {})
    host = container.get("HostConfig", {})
    _expect(config.get("User") == "65532:65532", "RUNTIME_INSPECT_USER", "non-root user mismatch")
    _expect(
        (config.get("Labels") or {}).get(
            "dev.shirokuma.go-vendor-bundle.sha256"
        )
        == release["source"]["module_inputs"]["bundle_sha256"],
        "RUNTIME_VENDOR_BUNDLE",
        "image label does not bind the retained Go vendor bundle",
    )
    _expect(container.get("Path") == "/usr/bin/weed", "RUNTIME_INSPECT_COMMAND", "path mismatch")
    _expect(container.get("Args") == ["mini", "-dir=/data"], "RUNTIME_INSPECT_COMMAND", "args mismatch")
    _expect(host.get("ReadonlyRootfs") is True, "RUNTIME_INSPECT_READ_ONLY", "required")

    expected_tmpfs = {
        "/tmp": {"rw", "nosuid", "nodev", "size=16m", "uid=65532", "gid=65532", "mode=1777"},
        "/data": {"rw", "nosuid", "nodev", "size=64m", "uid=65532", "gid=65532", "mode=0755"},
    }
    actual_tmpfs = host.get("Tmpfs")
    _expect(
        isinstance(actual_tmpfs, dict) and set(actual_tmpfs) == set(expected_tmpfs),
        "RUNTIME_INSPECT_TMPFS",
        "mount set mismatch",
    )
    for mount, expected_options in expected_tmpfs.items():
        options = actual_tmpfs[mount]
        _expect(isinstance(options, str), "RUNTIME_INSPECT_TMPFS", f"{mount}: options missing")
        _expect(
            set(options.split(",")) == expected_options,
            "RUNTIME_INSPECT_TMPFS",
            f"{mount}: option set mismatch",
        )

    _expect(host.get("CapDrop") == ["ALL"], "RUNTIME_INSPECT_CAPABILITIES", "CapDrop mismatch")
    security_options = host.get("SecurityOpt")
    _expect(
        isinstance(security_options, list)
        and len(security_options) == 1
        and security_options[0] in {"no-new-privileges", "no-new-privileges:true"},
        "RUNTIME_INSPECT_PRIVILEGES",
        "no-new-privileges is required",
    )
    _expect(host.get("PidsLimit") == 256, "RUNTIME_INSPECT_PIDS", "expected 256")
    _expect(host.get("Memory") == 536870912, "RUNTIME_INSPECT_MEMORY", "expected 512 MiB")

    smoke = _load_json(smoke_path)
    expected_smoke_fields = {
        "schema_version",
        "result",
        "reference",
        "digest",
        "user",
        "command",
        "read_only_rootfs",
        "tmpfs",
        "capabilities_dropped",
        "no_new_privileges",
        "sustained_running_seconds",
        "run_id",
        "run_attempt",
        "runtime_inspect",
    }
    _expect(
        set(smoke) == expected_smoke_fields,
        "RUNTIME_SMOKE_CLOSED_WORLD",
        "unexpected or missing runtime field",
    )
    _expect(smoke.get("schema_version") == 2, "RUNTIME_SMOKE_SCHEMA", "expected schema 2")
    _expect(
        smoke.get("reference") == release["reference"]
        and smoke.get("digest") == release["digest"],
        "RUNTIME_SMOKE_IDENTITY",
        "reference or digest mismatch",
    )
    builder = release["builder"]
    _expect(
        str(smoke.get("run_id")) == str(builder["run_id"])
        and str(smoke.get("run_attempt")) == str(builder["run_attempt"]),
        "RUNTIME_SMOKE_RUN_IDENTITY",
        "builder run mismatch",
    )
    runtime_inspect = smoke.get("runtime_inspect", {})
    _expect(
        runtime_inspect
        == {"file": inspect_path.name, "sha256": _sha256(inspect_path)},
        "RUNTIME_SMOKE_INSPECT_LINK",
        "inspect file or hash mismatch",
    )
    _expect(smoke.get("user") == config["User"], "RUNTIME_SMOKE_USER", "inspect mismatch")
    _expect(
        smoke.get("command") == [container["Path"], *container["Args"]],
        "RUNTIME_SMOKE_COMMAND",
        "inspect mismatch",
    )
    _expect(
        smoke.get("read_only_rootfs") is host["ReadonlyRootfs"],
        "RUNTIME_SMOKE_READ_ONLY",
        "inspect mismatch",
    )
    _expect(
        smoke.get("tmpfs") == ["/tmp", "/data"]
        and set(smoke["tmpfs"]) == set(actual_tmpfs),
        "RUNTIME_SMOKE_TMPFS",
        "inspect mismatch",
    )
    _expect(
        smoke.get("capabilities_dropped") == host["CapDrop"][0],
        "RUNTIME_SMOKE_CAPABILITIES",
        "inspect mismatch",
    )
    _expect(smoke.get("no_new_privileges") is True, "RUNTIME_SMOKE_PRIVILEGES", "required")
    _expect(
        smoke.get("sustained_running_seconds") == 10,
        "RUNTIME_SMOKE_SUSTAINED",
        "expected 10 seconds",
    )
    _expect(smoke.get("result") == "passed", "RUNTIME_SMOKE_RESULT", "not passed")


def _validate_promotion_execution_identity(
    builder: Dict[str, Any],
    promotion_run_id: str,
    promotion_run_attempt: str,
) -> None:
    builder_run_id = str(builder["run_id"])
    builder_run_attempt = str(builder["run_attempt"])
    _expect(
        promotion_run_id.isdigit()
        and promotion_run_attempt.isdigit()
        and int(promotion_run_id) > 0
        and int(promotion_run_attempt) > 0
        and promotion_run_id == builder_run_id,
        "PROMOTION_RUN_IDENTITY",
        "promotion must belong to the candidate workflow run",
    )
    _expect(
        int(promotion_run_attempt) >= int(builder_run_attempt),
        "PROMOTION_ATTEMPT_ORDER",
        "promotion attempt precedes candidate build attempt",
    )


def _validate_promotion(
    release: Dict[str, Any],
    contract: Dict[str, Any],
    path: Path,
    candidate_release_path: Path,
) -> None:
    promotion = _load_json(path)
    _expect(
        set(promotion)
        == {
            "schema_version",
            "status",
            "reference",
            "trusted_tag",
            "trusted_tag_role",
            "trusted_tag_digest",
            "promoted_at",
            "run_id",
            "run_attempt",
            "tool",
            "candidate",
        },
        "PROMOTION_CLOSED_WORLD",
        "unexpected or missing promotion field",
    )
    _expect(promotion.get("schema_version") == 1, "PROMOTION_SCHEMA", "expected schema 1")
    _expect(promotion.get("status") == "verified", "PROMOTION_STATUS", "not verified")
    _expect(
        re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
            r"(?:\.[0-9]+)?Z",
            promotion.get("promoted_at", ""),
        )
        is not None,
        "PROMOTION_TIME",
        "timezone-qualified UTC timestamp required",
    )
    _expect(promotion.get("reference") == release["reference"], "PROMOTION_REFERENCE", "mismatch")
    _expect(promotion.get("trusted_tag_digest") == release["digest"], "PROMOTION_DIGEST", "mismatch")
    builder = release["builder"]
    expected_candidate_artifact = (
        f"seaweedfs-4.39-arm64-candidate-{builder['run_id']}-{builder['run_attempt']}"
    )
    expected_trusted_reference = (
        f"{contract['image']['repository']}:{contract['image']['trusted_tag']}"
    )
    _expect(
        promotion.get("trusted_tag") == expected_trusted_reference,
        "PROMOTION_TRUSTED_TAG",
        "contract mismatch",
    )
    _expect(
        promotion.get("trusted_tag_role") == contract["image"]["trusted_tag_role"],
        "PROMOTION_TRUSTED_TAG_ROLE",
        "tag pointer must not be an admission authority",
    )
    promotion_run_id = str(promotion.get("run_id", ""))
    promotion_run_attempt = str(promotion.get("run_attempt", ""))
    _validate_promotion_execution_identity(
        builder,
        promotion_run_id,
        promotion_run_attempt,
    )
    crane = contract["toolchain"]["crane"]
    tool = promotion.get("tool", {})
    _expect(
        set(tool)
        == {"name", "version", "archive_sha256", "verified_before_registry_login"},
        "PROMOTION_TOOL",
        "unexpected or missing tool field",
    )
    _expect(tool.get("name") == "crane", "PROMOTION_TOOL", "name")
    _expect(tool.get("version") == crane["version"], "PROMOTION_TOOL", "version")
    _expect(
        tool.get("archive_sha256") == crane["linux_arm64_archive_sha256"],
        "PROMOTION_TOOL",
        "archive hash",
    )
    _expect(tool.get("verified_before_registry_login") is True, "PROMOTION_CREDENTIAL_BOUNDARY", "missing")

    candidate = promotion.get("candidate", {})
    _expect(
        set(candidate)
        == {
            "artifact_name",
            "snapshot_file",
            "release_evidence_sha256",
            "contract_sha256",
        },
        "PROMOTION_CANDIDATE_LINK",
        "unexpected or missing candidate field",
    )
    _expect(
        candidate.get("artifact_name") == expected_candidate_artifact,
        "PROMOTION_CANDIDATE_ARTIFACT",
        "run-scoped artifact mismatch",
    )
    _expect(
        candidate.get("snapshot_file") == candidate_release_path.name,
        "PROMOTION_CANDIDATE_SNAPSHOT",
        "snapshot file mismatch",
    )
    _expect(
        candidate.get("release_evidence_sha256") == _sha256(candidate_release_path),
        "PROMOTION_CANDIDATE_HASH",
        "candidate release hash mismatch",
    )
    _expect(
        candidate.get("contract_sha256") == release["contract"]["sha256"],
        "PROMOTION_CANDIDATE_CONTRACT",
        "contract hash mismatch",
    )

    release_promotion = release.get("promotion", {})
    _expect(
        set(release_promotion)
        == {
            "status",
            "run_id",
            "run_attempt",
            "trusted_tag",
            "trusted_tag_role",
            "trusted_tag_digest",
            "evidence",
            "candidate_artifact",
            "candidate_release_evidence",
            "candidate_release_sha256",
        },
        "RELEASE_PROMOTION_CLOSED_WORLD",
        "unexpected or missing promotion field",
    )
    _expect(release_promotion.get("status") == "verified", "PROMOTION_STATUS", "release record")
    _expect(
        str(release_promotion.get("run_id")) == promotion_run_id
        and str(release_promotion.get("run_attempt")) == promotion_run_attempt,
        "RELEASE_PROMOTION_RUN_IDENTITY",
        "release and promotion evidence execution identities differ",
    )
    _expect(
        release_promotion.get("trusted_tag") == contract["image"]["trusted_tag"]
        and release_promotion.get("trusted_tag_role")
        == contract["image"]["trusted_tag_role"]
        and release_promotion.get("trusted_tag_digest") == release["digest"],
        "RELEASE_PROMOTION_TAG",
        "trusted tag mismatch",
    )
    _expect(
        release_promotion.get("evidence") == path.name,
        "RELEASE_PROMOTION_EVIDENCE",
        "evidence file mismatch",
    )
    _expect(
        release_promotion.get("candidate_artifact") == expected_candidate_artifact
        and release_promotion.get("candidate_release_evidence")
        == candidate_release_path.name
        and release_promotion.get("candidate_release_sha256")
        == candidate["release_evidence_sha256"],
        "RELEASE_PROMOTION_CANDIDATE",
        "candidate lineage mismatch",
    )

    candidate_release = _load_json(candidate_release_path)
    _expect(
        candidate_release.get("schema_version") == 2,
        "PROMOTION_CANDIDATE_SCHEMA",
        "expected release schema 2",
    )
    _expect(
        candidate_release.get("component") == release["component"]
        and candidate_release.get("version") == release["version"]
        and candidate_release.get("platform") == release["platform"]
        and candidate_release.get("reference") == release["reference"]
        and candidate_release.get("digest") == release["digest"],
        "PROMOTION_CANDIDATE_IDENTITY",
        "release identity or digest mismatch",
    )
    _expect(
        candidate_release.get("builder") == release["builder"],
        "PROMOTION_CANDIDATE_BUILDER",
        "builder identity mismatch",
    )
    _expect(
        candidate_release.get("contract") == release["contract"],
        "PROMOTION_CANDIDATE_CONTRACT",
        "contract record mismatch",
    )
    _expect(
        candidate_release.get("promotion")
        == {
            "status": "pending",
            "trusted_tag": contract["image"]["trusted_tag"],
            "tool": "crane",
        },
        "PROMOTION_CANDIDATE_STATE",
        "snapshot is not the pre-promotion release",
    )
    _expect(
        candidate_release.get("actions_artifact")
        == {
            "role": contract["evidence"]["actions_artifact_role"],
            "candidate_name": expected_candidate_artifact,
            "retention_days": contract["evidence"]["candidate_retention_days"],
        },
        "PROMOTION_CANDIDATE_ARTIFACT",
        "snapshot candidate artifact mismatch",
    )
    _expect(
        set(candidate_release) == set(release),
        "PROMOTION_CANDIDATE_CLOSED_WORLD",
        "candidate and final release schemas differ",
    )
    mutable_fields = {"promotion", "artifacts", "actions_artifact"}
    _expect(
        all(
            candidate_release[field] == release[field]
            for field in set(release) - mutable_fields
        ),
        "PROMOTION_CANDIDATE_IMMUTABLE_FIELDS",
        "non-promotion release field changed after candidate validation",
    )
    candidate_artifacts = candidate_release.get("artifacts")
    final_artifacts = release.get("artifacts")
    _expect(
        isinstance(candidate_artifacts, dict)
        and isinstance(final_artifacts, dict)
        and candidate_release_path.name in final_artifacts
        and path.name in final_artifacts,
        "PROMOTION_CANDIDATE_ARTIFACTS",
        "promotion lineage artifacts are missing",
    )
    expected_final_artifacts = dict(candidate_artifacts)
    expected_final_artifacts[candidate_release_path.name] = final_artifacts[
        candidate_release_path.name
    ]
    expected_final_artifacts[path.name] = final_artifacts[path.name]
    _expect(
        release.get("artifacts") == expected_final_artifacts,
        "PROMOTION_CANDIDATE_ARTIFACTS",
        "final artifact set is not candidate plus promotion lineage",
    )


def _validate_repository_admission(root: Path, release: Dict[str, Any]) -> None:
    admission = _load_json(root / ADMISSION_PATH)
    _expect(admission.get("schema_version") == 2, "ADMISSION_SCHEMA", "expected schema 2")
    _expect(
        admission.get("component") == release["component"]
        and admission.get("version") == release["version"]
        and admission.get("platform") == release["platform"],
        "ADMISSION_RELEASE_IDENTITY",
        "component, version, or platform mismatch",
    )
    _expect(
        admission.get("source") == release["source"]["repository"]
        and admission.get("release_commit") == release["source"]["commit"]
        and admission.get("assessment", {}).get("admission")
        == release["admission_status"],
        "ADMISSION_SOURCE_AND_STATE",
        "source commit or admission state mismatch",
    )
    admitted = admission.get("admitted_candidate", {})
    _expect(
        admitted.get("reference") == release["reference"],
        "ADMISSION_REFERENCE",
        "release reference mismatch",
    )
    _expect(
        admitted.get("manifest_digest") == release["digest"],
        "ADMISSION_DIGEST",
        "release digest mismatch",
    )
    _expect(
        admitted.get("release_evidence") == RELEASE_PATH.as_posix()
        and admitted.get("source_evidence") == SOURCE_PATH.as_posix(),
        "ADMISSION_EVIDENCE_PATH",
        "release or source evidence path mismatch",
    )
    release_builder = release["builder"]
    expected_builder = {
        "repository": release_builder["repository"],
        "workflow_name": release_builder["workflow_name"],
        "workflow": release_builder["workflow"],
        "ref": release_builder["ref"],
        "workflow_sha": release_builder["workflow_sha"],
        "source_sha": release_builder["source_sha"],
        "trigger": release_builder["trigger"],
        "run_id": str(release_builder["run_id"]),
        "run_attempt": str(release_builder["run_attempt"]),
        "issuer": release["issuer"],
        "identity": release["identity"],
        "run": (
            f"https://github.com/{release_builder['repository']}/actions/runs/"
            f"{release_builder['run_id']}/attempts/{release_builder['run_attempt']}"
        ),
    }
    _expect(
        admitted.get("builder") == expected_builder,
        "ADMISSION_BUILDER_IDENTITY",
        "repository, workflow, ref, SHA, run, issuer, or identity mismatch",
    )

    raw_controls = admitted.get("controls")
    _expect(
        isinstance(raw_controls, list),
        "ADMISSION_CONTROL_SET",
        "controls must be a list",
    )
    controls: Dict[str, Dict[str, Any]] = {}
    for control in raw_controls:
        _expect(
            isinstance(control, dict),
            "ADMISSION_CONTROL_SET",
            "every control must be an object",
        )
        name = control.get("control")
        _expect(
            isinstance(name, str) and name and name not in controls,
            "ADMISSION_CONTROL_SET",
            "control names must be non-empty and unique",
        )
        _expect(
            control.get("status") == "verified"
            and isinstance(control.get("evidence"), str)
            and bool(control["evidence"].strip()),
            "ADMISSION_CONTROL_STATE",
            f"{name} must be verified with non-empty evidence",
        )
        controls[name] = control

    artifacts = release["artifacts"]
    source = release["source"]
    scanner = release["scanner"]
    promotion = release["promotion"]

    def artifact_binding(name: str) -> Dict[str, str]:
        return artifacts[name]

    signature_bundle = artifact_binding("cosign-signature-bundle.json")
    signature_registry = artifact_binding("registry-signature-bundles.jsonl")
    signature_verify = artifact_binding("cosign-verify.json")
    module_manifest = artifact_binding("go-module-inputs.json")
    vendor_bundle = artifact_binding("go-vendor.tar.xz")
    rekor_response = artifact_binding("rekor-entry.json")
    slsa_verify = artifact_binding("slsa-verify.json")
    slsa_bundles = artifact_binding("slsa-bundles.jsonl")
    sbom = artifact_binding("seaweedfs-4.39-arm64.cdx.json")
    trivy = artifact_binding("trivy.json")
    runtime_summary = artifact_binding("runtime-smoke.json")
    runtime_inspect = artifact_binding("runtime-container-inspect.json")
    promotion_evidence = artifact_binding("promotion-evidence.json")
    candidate_evidence = artifact_binding("candidate-release-evidence.json")

    expected_bindings: Dict[str, Dict[str, Any]] = {
        "source_adoption": {
            "source_evidence": source["evidence"],
            "source_evidence_sha256": source["evidence_sha256"],
            "commit": source["commit"],
            "tree": source["tree"],
            "git_archive_sha256": source["git_archive_sha256"],
            "containerfile_sha256": source["containerfile_sha256"],
            "go_mod_sha256": source["module_inputs"]["go_mod_sha256"],
            "go_sum_sha256": source["module_inputs"]["go_sum_sha256"],
            "module_count": source["module_inputs"]["module_count"],
            "replacement_count": source["module_inputs"]["replacement_count"],
            "module_manifest": module_manifest["path"],
            "module_manifest_sha256": module_manifest["sha256"],
            "vendor_bundle": vendor_bundle["path"],
            "vendor_bundle_sha256": vendor_bundle["sha256"],
        },
        "signature": {
            "bundle": signature_bundle["path"],
            "bundle_sha256": signature_bundle["sha256"],
            "registry_bundles": signature_registry["path"],
            "registry_bundles_sha256": signature_registry["sha256"],
            "verification": signature_verify["path"],
            "verification_sha256": signature_verify["sha256"],
            "issuer": release["issuer"],
            "identity": release["identity"],
            "workflow_sha": release_builder["workflow_sha"],
        },
        "transparency_log": {
            "rekor_response": rekor_response["path"],
            "rekor_response_sha256": rekor_response["sha256"],
            "entries": release["transparency_log"]["entries"],
        },
        "workflow_revision": {
            key: release_builder[key]
            for key in (
                "repository",
                "workflow_name",
                "workflow",
                "ref",
                "workflow_sha",
                "source_sha",
                "trigger",
                "run_id",
                "run_attempt",
            )
        },
        "slsa_provenance": {
            "provenance": release["slsa_provenance"],
            "verification": slsa_verify["path"],
            "verification_sha256": slsa_verify["sha256"],
            "bundles": slsa_bundles["path"],
            "bundles_sha256": slsa_bundles["sha256"],
        },
        "sbom": {
            "path": sbom["path"],
            "sha256": sbom["sha256"],
        },
        "vulnerability_scan": {
            "critical": release["vulnerabilities"]["critical"],
            "high": release["vulnerabilities"]["high"],
            "scanner_name": scanner["name"],
            "scanner_version": scanner["version"],
            "vulnerability_db_version": scanner["vulnerability_db"]["version"],
            "vulnerability_db_updated_at": scanner["vulnerability_db"][
                "updated_at"
            ],
            "vulnerability_db_downloaded_at": scanner["vulnerability_db"][
                "downloaded_at"
            ],
            "path": trivy["path"],
            "sha256": trivy["sha256"],
        },
        "runtime_tmp": {
            "run_id": str(release_builder["run_id"]),
            "run_attempt": str(release_builder["run_attempt"]),
            "summary": runtime_summary["path"],
            "summary_sha256": runtime_summary["sha256"],
            "inspect": runtime_inspect["path"],
            "inspect_sha256": runtime_inspect["sha256"],
        },
        "tag_promotion": {
            "run_id": str(promotion["run_id"]),
            "run_attempt": str(promotion["run_attempt"]),
            "digest": promotion["trusted_tag_digest"],
            "trusted_tag": promotion["trusted_tag"],
            "trusted_tag_role": promotion["trusted_tag_role"],
            "promotion": promotion_evidence["path"],
            "promotion_sha256": promotion_evidence["sha256"],
            "candidate": candidate_evidence["path"],
            "candidate_sha256": candidate_evidence["sha256"],
        },
    }
    _expect(
        set(controls) == set(expected_bindings),
        "ADMISSION_CONTROL_SET",
        "admission controls must match the closed required set",
    )
    for name, expected in expected_bindings.items():
        control = controls[name]
        _expect(
            set(control) == {"control", "status", "evidence", *expected},
            "ADMISSION_CONTROL_KEYS",
            f"{name} fields do not match the closed schema",
        )
        _expect(
            all(control.get(key) == value for key, value in expected.items()),
            "ADMISSION_CONTROL_BINDING",
            f"{name} does not bind to the current release evidence",
        )


def _expected_actions_artifact_name(
    release: Dict[str, Any],
    require_promotion: bool,
) -> str:
    artifact_prefix = "seaweedfs-4.39-arm64"
    if require_promotion:
        identity = release.get("promotion", {})
        return (
            f"{artifact_prefix}-{identity.get('run_id')}"
            f"-{identity.get('run_attempt')}"
        )
    identity = release.get("builder", {})
    return (
        f"{artifact_prefix}-candidate-{identity.get('run_id')}"
        f"-{identity.get('run_attempt')}"
    )


def validate_release_bundle(
    root: Path,
    evidence_dir: Optional[Path] = None,
    require_promotion: bool = True,
) -> Dict[str, Any]:
    root = root.resolve()
    contract = validate_static_contract(root)
    if evidence_dir is None:
        release_path = root / RELEASE_PATH
    else:
        evidence_dir = evidence_dir.resolve()
        release_path = evidence_dir / "release-evidence.json"
    repository_mode = evidence_dir is None
    root_source_path = root / SOURCE_PATH
    root_contract_path = root / CONTRACT_PATH
    if repository_mode:
        retained_source_path = root_source_path
        retained_contract_path = root_contract_path
    else:
        retained_source_path = _safe_repo_path(
            evidence_dir,
            SOURCE_PATH.as_posix(),
            "SOURCE_EVIDENCE_PATH",
        )
        retained_contract_path = _safe_repo_path(
            evidence_dir,
            CONTRACT_PATH.as_posix(),
            "CONTRACT_EVIDENCE_PATH",
        )
        _expect(retained_source_path.is_file(), "SOURCE_EVIDENCE_MISSING", SOURCE_PATH.as_posix())
        _expect(
            retained_contract_path.is_file(),
            "CONTRACT_EVIDENCE_MISSING",
            CONTRACT_PATH.as_posix(),
        )
        _expect(
            _sha256(retained_source_path) == _sha256(root_source_path),
            "SOURCE_EVIDENCE_ROOT_MISMATCH",
            "downloaded source record differs from reviewed repository",
        )
        _expect(
            _sha256(retained_contract_path) == _sha256(root_contract_path),
            "CONTRACT_EVIDENCE_ROOT_MISMATCH",
            "downloaded contract differs from reviewed repository",
        )
    release = _load_json(release_path)
    _expect(release.get("schema_version") == 2, "RELEASE_SCHEMA", "expected schema 2")
    _expect(
        set(release)
        == {
            "schema_version",
            "component",
            "version",
            "platform",
            "reference",
            "digest",
            "contract",
            "source",
            "builder",
            "issuer",
            "identity",
            "slsa_provenance",
            "admission_status",
            "vulnerabilities",
            "scanner",
            "toolchain",
            "transparency_log",
            "promotion",
            "artifacts",
            "actions_artifact",
        },
        "RELEASE_CLOSED_WORLD",
        "unexpected or missing release field",
    )
    _expect(release.get("component") == contract["component"], "RELEASE_COMPONENT", "mismatch")
    _expect(release.get("version") == contract["version"], "RELEASE_VERSION", "mismatch")
    _expect(release.get("platform") == contract["platform"], "RELEASE_PLATFORM", "mismatch")
    reference = release.get("reference", "")
    _expect(release.get("digest") == "sha256:" + _digest_hex(reference), "RELEASE_DIGEST", "mismatch")
    _expect(
        reference.split("@", 1)[0] == contract["image"]["repository"],
        "RELEASE_IMAGE",
        "repository mismatch",
    )
    builder = release.get("builder", {})
    _expect(
        set(builder)
        == {
            "repository",
            "workflow_name",
            "workflow",
            "ref",
            "workflow_sha",
            "source_sha",
            "trigger",
            "run_id",
            "run_attempt",
        },
        "RELEASE_BUILDER_CLOSED_WORLD",
        "unexpected or missing builder field",
    )
    _expect(
        re.fullmatch(r"[0-9a-f]{40}", builder.get("workflow_sha", "")) is not None
        and re.fullmatch(r"[0-9a-f]{40}", builder.get("source_sha", "")) is not None,
        "RELEASE_BUILDER_SHA",
        "workflow and source SHAs are required",
    )
    workflow_contract = contract["workflow"]
    expected_identity = (
        f"https://github.com/{workflow_contract['repository']}/"
        f"{workflow_contract['path']}@{builder.get('ref', '')}"
    )
    _expect(
        builder.get("repository") == workflow_contract["repository"]
        and builder.get("workflow_name") == workflow_contract["name"]
        and builder.get("workflow") == workflow_contract["path"]
        and builder.get("ref") in workflow_contract["allowed_refs"]
        and builder.get("trigger") in workflow_contract["allowed_triggers"]
        and str(builder.get("run_id", "")).isdigit()
        and str(builder.get("run_attempt", "")).isdigit()
        and release.get("issuer") == workflow_contract["issuer"]
        and release.get("identity") == expected_identity,
        "RELEASE_BUILDER_IDENTITY",
        "contract mismatch",
    )
    _expect(
        re.fullmatch(
            rf"https://github\.com/{re.escape(workflow_contract['repository'])}/"
            r"attestations/[0-9]+",
            release.get("slsa_provenance", ""),
        )
        is not None,
        "RELEASE_SLSA_URL",
        "attestation URL mismatch",
    )
    source = _load_json(retained_source_path)
    release_source = release.get("source", {})
    _expect(
        release_source
        == {
            "repository": source.get("repository"),
            "commit": source.get("commit"),
            "tree": source.get("tree"),
            "git_archive_sha256": source.get("git_archive_sha256"),
            "evidence": SOURCE_PATH.as_posix(),
            "evidence_sha256": _sha256(retained_source_path),
            "containerfile_sha256": source.get("containerfile_sha256"),
            "build_inputs": source.get("build_inputs"),
            "module_inputs": source.get("module_inputs"),
        },
        "RELEASE_SOURCE",
        "source record mismatch",
    )
    _expect(
        release.get("admission_status") == contract["admission"]["artifact"],
        "RELEASE_ADMISSION_STATUS",
        "contract mismatch",
    )
    _expect(
        release.get("contract")
        == {
            "path": CONTRACT_PATH.as_posix(),
            "sha256": _sha256(retained_contract_path),
        },
        "RELEASE_CONTRACT",
        "path or hash mismatch",
    )

    actions_artifact = release.get("actions_artifact", {})
    expected_artifact_name = _expected_actions_artifact_name(
        release,
        require_promotion,
    )
    if require_promotion:
        actual_artifact_name = actions_artifact.get("final_name")
        expected_retention = contract["evidence"]["final_retention_days"]
    else:
        actual_artifact_name = actions_artifact.get("candidate_name")
        expected_retention = contract["evidence"]["candidate_retention_days"]
    _expect(
        actions_artifact.get("role") == contract["evidence"]["actions_artifact_role"]
        and actual_artifact_name == expected_artifact_name
        and actions_artifact.get("retention_days") == expected_retention,
        "ACTIONS_ARTIFACT_RECORD",
        "phase, name, or retention mismatch",
    )

    artifacts = release.get("artifacts", {})
    required: List[str] = list(contract["evidence"]["candidate_required"])
    if require_promotion:
        required.extend(contract["evidence"]["promotion_required"])
    _expect(
        set(artifacts) == set(required),
        "EVIDENCE_SET",
        "artifact set differs from the closed-world evidence contract",
    )
    paths: Dict[str, Path] = {}
    for name in required:
        metadata = artifacts[name]
        path = _artifact_file(root, evidence_dir, name, metadata)
        _expect(path.is_file(), "EVIDENCE_MISSING", name)
        expected_hash = metadata.get("sha256", "")
        _expect(SHA256_RE.fullmatch(expected_hash) is not None, "EVIDENCE_HASH", name)
        _expect(_sha256(path) == expected_hash, "EVIDENCE_HASH", name)
        paths[name] = path

    try:
        package_go_vendor.verify_package(
            archive_path=paths["go-vendor.tar.xz"],
            manifest_path=paths["go-module-inputs.json"],
            source_record_path=retained_source_path,
        )
    except package_go_vendor.VendorPackageError as error:
        _fail("MODULE_INPUT_PACKAGE", f"{error.code}: {error.detail}")

    cosign_verification = _validate_cosign(
        contract,
        release,
        paths["cosign-verify.json"],
        paths["cosign-signature-bundle.json"],
        paths["image-manifest.json"],
        paths["registry-signature-bundles.jsonl"],
        paths["rekor-entry.json"],
        cryptographic_reverification=repository_mode,
    )
    _expect(
        release.get("transparency_log")
        == {
            "bundle": paths["cosign-signature-bundle.json"].name,
            "rekor_response": paths["rekor-entry.json"].name,
            "entries": cosign_verification["rekor_entries"],
        },
        "RELEASE_TRANSPARENCY_LOG",
        "file or Rekor entry linkage mismatch",
    )
    _validate_slsa(
        release,
        paths["slsa-verify.json"],
        paths["slsa-bundles.jsonl"],
    )
    _validate_sbom(release, contract, paths["seaweedfs-4.39-arm64.cdx.json"])
    _validate_trivy(
        release,
        contract,
        paths["trivy.json"],
        paths["trivy-version.json"],
    )
    toolchain = _validate_toolchain(contract, paths["toolchain.json"])
    _expect(
        release.get("toolchain") == toolchain["tools"],
        "RELEASE_TOOLCHAIN",
        "release toolchain differs from retained evidence",
    )
    _validate_runtime(
        release,
        paths["runtime-smoke.json"],
        paths["runtime-container-inspect.json"],
    )
    if require_promotion:
        _validate_promotion(
            release,
            contract,
            paths["promotion-evidence.json"],
            paths["candidate-release-evidence.json"],
        )
    else:
        _expect(release.get("promotion", {}).get("status") == "pending", "PROMOTION_STATUS", "candidate must be pending")
    if repository_mode:
        _validate_repository_admission(root, release)
    return release


def validate_promotion_preflight(
    root: Path,
    evidence_dir: Path,
    run_id: str,
    run_attempt: str,
    digest: str,
    candidate_artifact: str,
) -> Dict[str, Any]:
    """Bind a validated candidate to the exact promotion execution before login."""

    release = validate_release_bundle(
        root,
        evidence_dir,
        require_promotion=False,
    )
    _validate_promotion_execution_identity(
        release["builder"],
        run_id,
        run_attempt,
    )
    expected_candidate = _expected_actions_artifact_name(
        release,
        require_promotion=False,
    )
    _expect(
        candidate_artifact == expected_candidate
        and release["actions_artifact"]["candidate_name"] == expected_candidate,
        "PROMOTION_CANDIDATE_ARTIFACT",
        "downloaded candidate is not the builder-attempt artifact",
    )
    _expect(
        digest == release["digest"],
        "PROMOTION_TARGET_DIGEST",
        "promotion output digest differs from validated candidate evidence",
    )
    return release


def validate_pending_main_publication(
    root: Path,
    contract: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Validate the fail-closed state between policy merge and main publication."""

    root = root.resolve()
    if contract is None:
        contract = validate_static_contract(root)
    admission = _load_json(root / ADMISSION_PATH)
    _expect(
        set(admission)
        == {
            "schema_version",
            "component",
            "version",
            "platform",
            "upstream_candidate",
            "upstream_assessment",
            "lifecycle",
            "bootstrap_observation",
            "assessment",
            "runtime_manifests",
            "next_action",
        },
        "PENDING_ADMISSION_SCHEMA",
        "unexpected or missing top-level field",
    )
    _expect(
        admission.get("schema_version") == 3
        and admission.get("component") == contract["component"]
        and admission.get("version") == contract["version"]
        and admission.get("platform") == contract["platform"],
        "PENDING_ADMISSION_IDENTITY",
        "schema, component, version, or platform mismatch",
    )

    lifecycle = admission["lifecycle"]
    admission_contract = contract["admission"]
    expected_lifecycle = {
        "phase": admission_contract["pending_state"],
        "publisher_ref": admission_contract["publisher_ref"],
        "workflow": contract["workflow"]["path"],
        "workflow_sha256": contract["workflow"]["sha256"],
        "contract": CONTRACT_PATH.as_posix(),
        "contract_sha256": _sha256(root / CONTRACT_PATH),
        "source": SOURCE_PATH.as_posix(),
        "source_sha256": _sha256(root / SOURCE_PATH),
        "transition": admission_contract["evidence_transition"],
    }
    _expect(
        lifecycle == expected_lifecycle,
        "PENDING_LIFECYCLE_BINDING",
        "pending state does not bind the current source, contract, and workflow",
    )

    assessment = admission["assessment"]
    blockers = assessment.get("blockers")
    _expect(
        assessment.get("admission") == admission_contract["pending_state"]
        and assessment.get("exception_eligible") is False
        and isinstance(assessment.get("rationale"), str)
        and bool(assessment["rationale"].strip())
        and isinstance(blockers, list)
        and len(blockers) == 1
        and blockers[0].get("control") == "main_branch_publication"
        and blockers[0].get("status") == "pending"
        and bool(blockers[0].get("evidence", "").strip()),
        "PENDING_ADMISSION_STATE",
        "main publication must remain an explicit non-waivable blocker",
    )

    observation = admission["bootstrap_observation"]
    _expect(
        set(observation)
        == {
            "run",
            "source_sha",
            "digest",
            "artifact",
            "attestation",
            "disposition",
        }
        and re.fullmatch(
            r"https://github\.com/TommyKammy/Shirokuma/actions/runs/"
            r"[0-9]+/attempts/[0-9]+",
            observation.get("run", ""),
        )
        is not None
        and re.fullmatch(r"[0-9a-f]{40}", observation.get("source_sha", ""))
        is not None
        and re.fullmatch(
            r"sha256:[0-9a-f]{64}", observation.get("digest", "")
        )
        is not None
        and re.fullmatch(
            r"seaweedfs-4\.39-arm64-[0-9]+-[0-9]+",
            observation.get("artifact", ""),
        )
        is not None
        and re.fullmatch(
            r"https://github\.com/TommyKammy/Shirokuma/attestations/[0-9]+",
            observation.get("attestation", ""),
        )
        is not None
        and observation.get("disposition")
        == "not_admitted_branch_publication",
        "BOOTSTRAP_OBSERVATION",
        "branch publication may be recorded but never admitted",
    )

    runtime = admission["runtime_manifests"]
    _expect(
        runtime.get("permitted") is False
        and {
            blocker.get("control")
            for blocker in runtime.get("blockers", [])
            if isinstance(blocker, dict)
        }
        == {"main_branch_release_evidence", "resident_evidence_contract"},
        "PENDING_RUNTIME_STATE",
        "runtime use must remain blocked until main evidence and parent admission",
    )
    for relative in runtime.get("paths", []):
        _expect(
            not _safe_repo_path(root, relative, "PENDING_RUNTIME_PATH").exists(),
            "PENDING_RUNTIME_PATH",
            relative,
        )

    _expect(
        not (root / RELEASE_PATH).exists(),
        "PENDING_STALE_RELEASE",
        "pending state must not retain a release ledger from another contract",
    )
    evidence_dir = root / RELEASE_PATH.parent / "evidence"
    _expect(
        evidence_dir.is_dir(),
        "PENDING_STALE_EVIDENCE",
        "evidence directory is missing",
    )
    generated = {
        path.name
        for path in evidence_dir.iterdir()
        if path.is_file() and path.name != "README.md"
    }
    _expect(
        not generated,
        "PENDING_STALE_EVIDENCE",
        repr(sorted(generated)),
    )
    next_action = admission["next_action"]
    _expect(
        next_action.get("mode") == "publish-from-main-then-evidence-pr"
        and next_action.get("decision_record_required") is False
        and isinstance(next_action.get("requirements"), list)
        and len(next_action["requirements"]) >= 4,
        "PENDING_NEXT_ACTION",
        "two-phase transition is incomplete",
    )
    return admission


def validate_repository_audit(root: Path) -> Dict[str, Any]:
    """Audit either a strict admitted release or the explicit pending state."""

    root = root.resolve()
    contract = validate_static_contract(root)
    admission = _load_json(root / ADMISSION_PATH)
    state = admission.get("assessment", {}).get("admission")
    if state == contract["admission"]["pending_state"]:
        return validate_pending_main_publication(root, contract)
    _expect(
        state == contract["admission"]["required_approved_state"],
        "ADMISSION_ARTIFACT_STATE",
        str(state),
    )
    return validate_release_bundle(root, require_promotion=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=(
            "contract",
            "candidate",
            "promotion-preflight",
            "final",
            "repository",
            "audit",
        ),
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--run-attempt")
    parser.add_argument("--digest")
    parser.add_argument("--candidate-artifact")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.mode == "contract":
            validate_static_contract(args.root)
        elif args.mode in ("candidate", "final"):
            if args.evidence_dir is None:
                _fail("ARGUMENT", f"{args.mode} mode requires --evidence-dir")
            validate_release_bundle(
                args.root,
                args.evidence_dir,
                require_promotion=args.mode == "final",
            )
        elif args.mode == "promotion-preflight":
            required = {
                "evidence-dir": args.evidence_dir,
                "run-id": args.run_id,
                "run-attempt": args.run_attempt,
                "digest": args.digest,
                "candidate-artifact": args.candidate_artifact,
            }
            missing = [name for name, value in required.items() if value is None]
            if missing:
                _fail(
                    "ARGUMENT",
                    "promotion-preflight requires " + ", ".join(missing),
                )
            validate_promotion_preflight(
                args.root,
                args.evidence_dir,
                args.run_id,
                args.run_attempt,
                args.digest,
                args.candidate_artifact,
            )
        elif args.mode == "repository":
            validate_release_bundle(args.root, require_promotion=True)
        else:
            validate_repository_audit(args.root)
    except ContractError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"trusted-image {args.mode} verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
