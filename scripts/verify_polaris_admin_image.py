#!/usr/bin/env python3
"""Fail-closed policy audit for the Polaris Admin linux/arm64 publisher.

``audit-publication-bootstrap`` is intentionally static and safe to run before
Cosign is installed. ``audit`` repeats the static boundary and then performs
the retained Admin dependency snapshot's cryptographic verification.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shlex
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Mapping


CONTRACT_PATH = Path("bootstrap/polaris/v1.6.0/admin-image-contract.json")
ADMIN_INPUT_CONTRACT_PATH = Path(
    "bootstrap/polaris/v1.6.0/admin-build-inputs-contract.json"
)
ADMIN_INPUT_VERIFIER_PATH = Path("scripts/verify_polaris_admin_build_inputs.py")
SOURCE_PATH = Path("bootstrap/polaris/v1.6.0/source.json")
CONTAINERFILE_PATH = Path("bootstrap/polaris/v1.6.0/Containerfile.admin")
WORKFLOW_PATH = Path(".github/workflows/polaris-admin-arm64.yml")
RETIRED_ADMIN_INPUT_WORKFLOW = Path(
    ".github/workflows/polaris-admin-build-inputs.yml"
)
FUTURE_EVIDENCE_PATH = Path(
    "bootstrap/polaris/v1.6.0/admin-image-evidence"
)
FUTURE_ADMISSION_PATH = Path("bootstrap/polaris/v1.6.0/admin-admission.json")
RESIDENT_IMAGE_LEDGER = Path("security/admission/resident-images.json")

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FULL_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")

EXPECTED_SOURCE_SHA256 = (
    "7d14b606dd756f501644190c10deb64a1e046d46faacd0f76f92501ccd5185bb"
)
EXPECTED_ADMIN_INPUT_CONTRACT_SHA256 = (
    "6d56a2b086591f746bf272ff9388529013780b36950834e5233e41c34b16e400"
)
# Rebound after the policy files are stable. These constants deliberately pin
# exact bytes in addition to the semantic checks below.
EXPECTED_CONTRACT_SHA256 = (
    "c87156046ba1025c64521a6022aae95dcf653e4e20979b1c8d96c276c30e047c"
)
EXPECTED_ADMIN_INPUT_VERIFIER_SHA256 = (
    "5e153aacecaec7c313d9caba5b38ef65ff92f7eed25746e879222a4cdf441a42"
)
EXPECTED_CONTAINERFILE_SHA256 = (
    "39ab3fa250600d144d1a4deb00ac7d6277707994fd9b53b3a7f0968c279f6b72"
)
EXPECTED_WORKFLOW_SHA256 = (
    "cf72c732e57f593ce25aa08a076ed7b221cfc07768b307ea923423d4db856398"
)

EXPECTED_REPOSITORY = "TommyKammy/Shirokuma"
EXPECTED_REF = "refs/heads/main"
EXPECTED_IMAGE_REPOSITORY = "ghcr.io/tommykammy/shirokuma-polaris-admin"
EXPECTED_TRUSTED_TAG = "1.6.0-arm64"
EXPECTED_WORKFLOW_IDENTITY = (
    "https://github.com/TommyKammy/Shirokuma/.github/workflows/"
    "polaris-admin-arm64.yml@refs/heads/main"
)
EXPECTED_DEPENDENCY_REFERENCE = (
    "ghcr.io/tommykammy/shirokuma-polaris-admin-gradle-dependencies@"
    "sha256:7a505defcd78c7a7b978e88cd4c72e0a5d8b69cbb57ddd311c163b09fe789d18"
)
EXPECTED_RUNTIME_BASE = (
    "docker.io/library/amazoncorretto@sha256:"
    "ba1fe4a3fd4c6b70360183fccd1f0a168c3ea6f73709e8f81945cb9087431ff2"
)
EXPECTED_RUNTIME_BASE_INDEX = (
    "docker.io/library/amazoncorretto@sha256:"
    "d3a3476c19cbe37b2e3e46a2116ff197ab37c7072baad55ee0ad07f3b97e8d02"
)
EXPECTED_RUNTIME_BASE_JAVA_VERSION = "21.0.11"
EXPECTED_REVIEW_CHECKPOINT = {
    "repository": EXPECTED_REPOSITORY,
    "pull_request": 87,
    "reviewed_head_commit": "178b5ca03a2575a7100cfb47daede795fbd1d30c",
    "merge_commit": "8e5c6927e95d1027e16fe2ac27ab8322b45359c9",
    "merged_at": "2026-07-20T23:24:12Z",
    "merged_by": "TommyKammy",
    "reviewed_contract_sha256": (
        "26f5259642007aa11a4676ccee918bd5b1e55f8eb5c0025f92a12f1d8ccb37db"
    ),
    "reviewed_evidence_manifest_sha256": (
        "026c4d82e9031532323ccb3c31ea83939010982cfcf373644cdcf064e2613409"
    ),
    "reviewed_verifier_sha256": (
        "ef1fad340179e61f7d1291d9e3fd44c793c4761af4b094a0b232ea663c7f41c9"
    ),
}
EXPECTED_TASKS = [
    ":polaris-admin:assemble",
    ":polaris-admin:quarkusAppPartsBuild",
    ":polaris-server:assemble",
    ":polaris-server:quarkusAppPartsBuild",
]
EXPECTED_NOSQL_PROJECTS = [
    ":polaris-persistence-nosql-api",
    ":polaris-persistence-nosql-maintenance-api",
    ":polaris-persistence-nosql-metastore",
    ":polaris-persistence-nosql-cdi-quarkus",
    ":polaris-persistence-nosql-cdi-quarkus-distcache",
    ":polaris-persistence-nosql-maintenance-impl",
    ":polaris-persistence-nosql-metastore-maintenance",
]
EXPECTED_CANDIDATE_EVIDENCE = [
    "anonymous-image-manifest.json",
    "builder-metadata.json",
    "build-context.sha256",
    "build-input.json",
    "dependency-input.json",
    "offline-build.json",
    "source-authentication.json",
    "cosign-signature-bundle.json",
    "cosign-verify.json",
    "admin-help.json",
    "admin-bootstrap-help.json",
    "admin-container-inspect.json",
    "admin-smoke-log-policy.json",
    "image-config.json",
    "image-manifest.json",
    "polaris-admin-1.6.0-arm64.cdx.json",
    "registry-signature-bundles.jsonl",
    "rekor-entry.json",
    "runtime-base-index.json",
    "runtime-base-java-version.txt",
    "runtime-base-manifest.json",
    "sbom-attestation-bundle.json",
    "sbom-policy.json",
    "slsa-bundles.jsonl",
    "slsa-verify.json",
    "toolchain.json",
    "trivy-attestation-bundle.json",
    "trivy-version.json",
    "trivy.json",
]
EXPECTED_PROMOTION_EVIDENCE = [
    "promotion-cosign-verify.json",
    "promotion-slsa-verify.json",
    "publication.json",
    "trusted-tag-manifest.json",
]


class ContractError(RuntimeError):
    """An exact Admin image publication contract invariant failed."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _fail(code: str, detail: str) -> None:
    raise ContractError(code, detail)


def _expect(condition: bool, code: str, detail: str) -> None:
    if not condition:
        _fail(code, detail)


def _no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate key {key!r}")
        value[key] = item
    return value


def _load_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_no_duplicate_object,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        _fail("JSON_INVALID", f"cannot load {path}: {error}")
    _expect(isinstance(value, Mapping), "JSON_INVALID", f"{path} must be an object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        _fail("FILE_READ", f"cannot hash {path}: {error}")
    return digest.hexdigest()


def _require_regular_file(root: Path, relative: Path, code: str) -> Path:
    path = root / relative
    _expect(path.is_file() and not path.is_symlink(), code, f"missing or unsafe {relative}")
    try:
        path.resolve(strict=True).relative_to(root.resolve())
    except (OSError, ValueError) as error:
        _fail(code, f"{relative} escapes the repository root: {error}")
    return path


def _expect_exact_keys(
    value: Mapping[str, Any], expected: set[str], code: str, label: str
) -> None:
    actual = set(value)
    _expect(
        actual == expected,
        code,
        f"{label} keys differ: missing={sorted(expected - actual)}, extra={sorted(actual - expected)}",
    )


def _validate_source(source: Mapping[str, Any]) -> None:
    _expect(
        source
        == {
            "record": SOURCE_PATH.as_posix(),
            "record_sha256": EXPECTED_SOURCE_SHA256,
            "archive_sha512": (
                "d69b1a91e16e210a78dec327fc4725983b114fbec5d86d078a3827f35fe7dd"
                "5df3e4b12d18965e5a72eace65ad224aa007004ed61c66f9abb2efafc44ceac95b"
            ),
            "signature_sha256": (
                "2338e1c2385874e9bf5cf513b4d27732b1cd59e943e1662e62fa995d915e6481"
            ),
            "signing_key_fingerprint": "F2EEEB06110BEE1397EC74CBB8960FF52D9B1312",
            "git_commit": "dd306009d81a0e15adafe9dcd7d1c6d04d326f34",
            "git_tree": "1ad42f42aaebfa767b66a37f522a6c8d6693d841",
            "builder_index": (
                "docker.io/library/gradle@sha256:"
                "ecbf526b4d3c247b4cc61e9850eae2addd5f73a7c849bf026000442808f54b56"
            ),
            "builder_arm64_manifest": (
                "docker.io/library/gradle@sha256:"
                "cc583fa5245267fe0e1546c9989e8575473a37336ad9894dc0684a99fea1fb03"
            ),
            "java_major": 21,
            "gradle_version": "9.6.0",
            "tasks": EXPECTED_TASKS,
            "admin_build_preimage": {
                "path": "runtime/admin/build.gradle.kts",
                "git_blob": "94bf1dfd2b1039f1ca23d5dd7437429c11db66dd",
                "sha256": (
                    "6e3aabc2090cda72c03608053f41899792a6c62bec382ed18d6b02703574fde9"
                ),
                "size": 4_149,
            },
        },
        "SOURCE_CONTRACT",
        "authenticated source, builder, task closure, or Admin build preimage changed",
    )


def _validate_dependency_snapshot(snapshot: Mapping[str, Any]) -> None:
    _expect_exact_keys(
        snapshot,
        {
            "contract",
            "state",
            "admitted",
            "artifact_reference",
            "artifact_repository",
            "artifact_type",
            "descriptor_media_type",
            "archive_media_type",
            "descriptor",
            "archive",
            "verification_metadata",
            "review_checkpoint",
            "offline_proof",
        },
        "DEPENDENCY_SCHEMA",
        "dependency_snapshot",
    )
    _expect(
        snapshot["contract"]
        == {
            "path": ADMIN_INPUT_CONTRACT_PATH.as_posix(),
            "sha256": EXPECTED_ADMIN_INPUT_CONTRACT_SHA256,
        }
        and snapshot["state"] == "approved_for_admin_image_build"
        and snapshot["admitted"] is False
        and snapshot["artifact_reference"] == EXPECTED_DEPENDENCY_REFERENCE
        and snapshot["artifact_repository"]
        == "ghcr.io/tommykammy/shirokuma-polaris-admin-gradle-dependencies"
        and snapshot["artifact_type"]
        == "application/vnd.shirokuma.polaris-admin.gradle-dependencies.v1"
        and snapshot["descriptor_media_type"]
        == "application/vnd.shirokuma.gradle-dependency-descriptor.v1+json"
        and snapshot["archive_media_type"]
        == "application/vnd.shirokuma.gradle-cache.v1.tar+gzip",
        "DEPENDENCY_IDENTITY",
        "reviewed Admin dependency contract, OCI identity, or admission state changed",
    )
    _expect(
        snapshot["descriptor"]
        == {
            "path": (
                "bootstrap/polaris/v1.6.0/admin-build-inputs-evidence/"
                "gradle-dependency-inputs.json"
            ),
            "sha256": (
                "798802722e730174caa581cbffd4f82e5dd4a43aee92201df26f14db4ab005bc"
            ),
            "size": 2_175_793,
        }
        and snapshot["archive"]
        == {
            "filename": "polaris-gradle-dependencies-1.6.0.tar.gz",
            "sha256": (
                "e771fe2ec6b2d0f6940b1247a512eb5cbc78dd0f36e7be247975f2c5fa36fc4d"
            ),
            "size": 701_437_153,
        }
        and snapshot["verification_metadata"]
        == {
            "path": (
                "bootstrap/polaris/v1.6.0/admin-build-inputs-evidence/"
                "verification-metadata.xml"
            ),
            "sha256": (
                "171ccaf781d4ae63375b332205d25653ebcd29471e9e9c0cfba1b978144065b8"
            ),
            "size": 881_256,
        },
        "DEPENDENCY_BYTES",
        "reviewed Admin dependency descriptor, archive, or verification metadata changed",
    )
    _expect(
        snapshot["review_checkpoint"] == EXPECTED_REVIEW_CHECKPOINT,
        "REVIEW_CHECKPOINT",
        "PR #87 merge and reviewed bytes are not immutable",
    )
    _expect(
        snapshot["offline_proof"]
        == {
            "fresh_source_tree": True,
            "fresh_gradle_home": True,
            "container_network": "none",
            "gradle_offline": True,
            "dependency_verification": "strict",
            "build_cache": False,
            "configuration_cache": False,
            "tasks": EXPECTED_TASKS,
        },
        "OFFLINE_POLICY",
        "Admin/server offline regression task closure changed",
    )


def _validate_admin_surface(surface: Mapping[str, Any]) -> None:
    _expect(
        surface
        == {
            "review_state": "reviewed_for_image_publication",
            "relational_only": False,
            "image_publication_decision": (
                "accepted_unmodified_for_image_publication_only"
            ),
            "source_overlay_permitted": False,
            "runtime_activation_permitted": False,
            "unconditional_project_dependencies": EXPECTED_NOSQL_PROJECTS,
            "unconditional_external_dependencies": [
                "io.quarkus:quarkus-mongodb-client"
            ],
            "required_sbom_terms": ["mongodb", "polaris-persistence-nosql"],
            "exclusion_permitted": False,
        },
        "ADMIN_SURFACE",
        "NoSQL/Mongo visibility or image-only decision changed",
    )


def _validate_image_publication(publication: Mapping[str, Any]) -> None:
    _expect_exact_keys(
        publication,
        {
            "enabled",
            "state",
            "repository",
            "trusted_tag",
            "containerfile",
            "build_context",
            "runtime_base",
            "cli",
            "publication_boundary",
            "vulnerability_gate",
            "workflow",
        },
        "IMAGE_SCHEMA",
        "image_publication",
    )
    _expect(
        publication["enabled"] is True
        and publication["state"] == "pending_main_publication"
        and publication["repository"] == EXPECTED_IMAGE_REPOSITORY
        and publication["trusted_tag"] == EXPECTED_TRUSTED_TAG
        and publication["containerfile"]
        == {
            "path": CONTAINERFILE_PATH.as_posix(),
            "sha256": EXPECTED_CONTAINERFILE_SHA256,
        },
        "IMAGE_IDENTITY",
        "Admin image repository, tag, state, or Containerfile changed",
    )
    _expect(
        publication["build_context"]
        == {
            "containerfile_name": "Containerfile.admin",
            "admin_application_source": "runtime/admin/build/quarkus-app",
            "admin_application_destination": "build/quarkus-app",
            "license_source": "LICENSE",
            "notice_source": "NOTICE",
            "allowed_roots": [
                "Containerfile.admin",
                "build/quarkus-app",
                "distribution/LICENSE",
                "distribution/NOTICE",
            ],
            "checksum_manifest": "build-context.sha256",
            "checksum_format": "sha256-two-space-posix-path-v1",
            "closed_world": True,
            "symlinks_permitted": False,
            "hardlinks_permitted": False,
            "special_files_permitted": False,
            "path_traversal_permitted": False,
            "server_output_permitted": False,
            "dependency_library_closure": "reviewed-admin-descriptor",
        },
        "BUILD_CONTEXT_POLICY",
        "closed Admin Quarkus build-context policy changed",
    )
    _expect(
        publication["runtime_base"]
        == {
            "distribution": "Amazon Corretto",
            "java_version": EXPECTED_RUNTIME_BASE_JAVA_VERSION,
            "index": EXPECTED_RUNTIME_BASE_INDEX,
            "arm64_manifest": EXPECTED_RUNTIME_BASE,
            "user": "10000:10001",
        },
        "RUNTIME_BASE",
        "runtime base, platform, Java, or non-root user changed",
    )
    _expect(
        publication["cli"]
        == {
            "entrypoint": [
                "/usr/bin/java",
                "-jar",
                "/deployments/quarkus-run.jar",
            ],
            "default_arguments": ["--help"],
            "smoke_commands": [["--help"], ["bootstrap", "--help"]],
            "required_output_markers": [
                "Usage: polaris-admin-tool.jar [-hV] [COMMAND]",
                "Polaris administration & maintenance tool",
            ],
            "credential_file_option": "--credentials-file",
            "credential_option": "--credential",
            "print_credentials_option": "--print-credentials",
            "bootstrap_help_evidence_mode": (
                "network-none-read-only-help-invocation"
            ),
            "credential_file_runtime_only": True,
            "credential_file_image_default_permitted": False,
            "credential_argument_permitted": False,
            "print_credentials_permitted": False,
        },
        "CLI_POLICY",
        "Admin entrypoint, help-only smoke, or credential boundary changed",
    )
    _expect(
        publication["publication_boundary"]
        == {
            "repository": EXPECTED_REPOSITORY,
            "ref": EXPECTED_REF,
            "workflow_sha_equals_source_sha": True,
            "quarantine_before_promotion": True,
            "anonymous_exact_digest_verification": True,
            "credential_fallback_permitted": False,
            "admission_permitted": False,
            "release_evidence_committed": False,
        },
        "PUBLICATION_BOUNDARY",
        "main-only quarantine, anonymous verification, or fail-closed gate changed",
    )
    _expect(
        publication["vulnerability_gate"]
        == {
            "scanner": "trivy",
            "version": "0.72.0",
            "vulnerability_types": "os,library",
            "scanners": "vuln",
            "severity": "HIGH,CRITICAL",
            "ignore_unfixed": False,
            "maximum_high": 0,
            "maximum_critical": 0,
            "exception_permitted": False,
        },
        "VULNERABILITY_GATE",
        "full OS/library High/Critical zero-finding gate changed",
    )
    _expect(
        publication["workflow"]
        == {
            "path": WORKFLOW_PATH.as_posix(),
            "sha256": EXPECTED_WORKFLOW_SHA256,
            "repository": EXPECTED_REPOSITORY,
            "ref": EXPECTED_REF,
            "oidc_identity": EXPECTED_WORKFLOW_IDENTITY,
            "jobs": ["prepare", "verify", "promote"],
            "artifacts": {
                "build_input_prefix": "polaris-admin-image-build-input-",
                "candidate_prefix": "polaris-admin-image-candidate-",
                "publication_prefix": "polaris-admin-image-publication-",
            },
        },
        "WORKFLOW_CONTRACT",
        "publisher workflow identity, jobs, artifacts, or hash changed",
    )


def _validate_evidence(evidence: Mapping[str, Any]) -> None:
    _expect(
        evidence
        == {
            "candidate_retention_days": 30,
            "final_retention_days": 30,
            "actions_artifact_role": (
                "finite-retention transport copy pending evidence review"
            ),
            "future_directory": FUTURE_EVIDENCE_PATH.as_posix(),
            "candidate_required": EXPECTED_CANDIDATE_EVIDENCE,
            "promotion_required": EXPECTED_PROMOTION_EVIDENCE,
            "checksum_manifest": "evidence.sha256",
            "checksum_manifest_entries": 33,
            "directory_file_count_after_review": 34,
            "raw_logs_permitted": False,
        },
        "EVIDENCE_POLICY",
        "candidate/promotion evidence closure or retention policy changed",
    )
    _expect(
        len(EXPECTED_CANDIDATE_EVIDENCE) == 29
        and len(EXPECTED_PROMOTION_EVIDENCE) == 4
        and len(set(EXPECTED_CANDIDATE_EVIDENCE + EXPECTED_PROMOTION_EVIDENCE))
        == 33,
        "EVIDENCE_POLICY",
        "internal expected evidence inventory is not a 29+4 closed set",
    )


def _validate_downstream(contract: Mapping[str, Any]) -> None:
    _expect(
        contract["admission"]
        == {
            "state": "blocked_admin_image_evidence_review",
            "permitted": False,
            "record": FUTURE_ADMISSION_PATH.as_posix(),
        }
        and contract["runtime"]
        == {"state": "blocked_admin_image_admission", "enabled": False}
        and contract["gitops"]
        == {
            "state": "blocked_admin_image_admission",
            "resources_enabled": False,
        }
        and contract["credentials"]
        == {
            "state": "blocked_admin_runtime_activation",
            "material_permitted": False,
        }
        and contract["downstream_gates"]
        == {
            "admin_image_admitted": False,
            "resident_image_ledger_enabled": False,
            "admin_runtime_enabled": False,
            "gitops_resources_enabled": False,
            "credential_material_permitted": False,
            "next_checkpoint": (
                "retain and independently review the exact published Admin image evidence"
            ),
        },
        "DOWNSTREAM_GATE",
        "admission, resident ledger, runtime, GitOps, or credentials opened early",
    )


def _validate_contract(contract: Mapping[str, Any]) -> None:
    _expect_exact_keys(
        contract,
        {
            "schema_version",
            "component",
            "version",
            "platform",
            "lifecycle",
            "source",
            "dependency_snapshot",
            "admin_dependency_surface",
            "image_publication",
            "evidence",
            "admission",
            "runtime",
            "gitops",
            "credentials",
            "downstream_gates",
        },
        "CONTRACT_SCHEMA",
        "root",
    )
    _expect(
        contract["schema_version"] == 1
        and contract["component"] == "polaris-admin"
        and contract["version"] == "1.6.0"
        and contract["platform"] == "linux/arm64",
        "CONTRACT_IDENTITY",
        "schema, component, version, or platform changed",
    )
    _expect(
        contract["lifecycle"]
        == {
            "state": "admin_image_publication_pending",
            "next_state": "admin_image_evidence_review_pending",
        },
        "LIFECYCLE_STATE",
        "Admin image lifecycle skipped publication or evidence review",
    )
    _validate_source(contract["source"])
    _validate_dependency_snapshot(contract["dependency_snapshot"])
    _validate_admin_surface(contract["admin_dependency_surface"])
    _validate_image_publication(contract["image_publication"])
    _validate_evidence(contract["evidence"])
    _validate_downstream(contract)


def _audit_containerfile(root: Path) -> None:
    path = _require_regular_file(root, CONTAINERFILE_PATH, "CONTAINERFILE")
    text = path.read_text(encoding="utf-8")
    _expect(
        _sha256(path) == EXPECTED_CONTAINERFILE_SHA256,
        "CONTAINERFILE",
        "Containerfile.admin bytes differ from the reviewed policy",
    )
    required = {
        f"FROM {EXPECTED_RUNTIME_BASE}",
        "WORKDIR /deployments",
        "USER 10000:10001",
        'ENTRYPOINT ["/usr/bin/java", "-jar", "/deployments/quarkus-run.jar"]',
        'CMD ["--help"]',
        "COPY --chown=10000:10001 build/quarkus-app/lib/ /deployments/lib/",
        "COPY --chown=10000:10001 distribution/LICENSE /deployments/LICENSE",
        "COPY --chown=10000:10001 distribution/NOTICE /deployments/NOTICE",
    }
    missing = sorted(token for token in required if token not in text)
    forbidden = [
        token
        for token in ("\nADD ", "\nRUN ", "\nEXPOSE ", "\nHEALTHCHECK ", "\nVOLUME ")
        if token in "\n" + text
    ]
    _expect(
        not missing and not forbidden and text.count("\nFROM ") == 1,
        "CONTAINERFILE_SEMANTICS",
        f"missing required controls={missing}, forbidden directives={forbidden}",
    )


def _job_blocks(text: str) -> dict[str, str]:
    marker = re.search(r"(?m)^jobs:\s*$", text)
    _expect(marker is not None, "WORKFLOW_SEMANTICS", "jobs mapping missing")
    tail = text[marker.end() :]
    starts = list(re.finditer(r"(?m)^  ([a-z][a-z0-9_-]*):\s*$", tail))
    blocks: dict[str, str] = {}
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(tail)
        blocks[match.group(1)] = tail[match.start() : end]
    return blocks


def _workflow_shell_commands(text: str) -> list[str]:
    """Return logical shell commands from YAML ``run`` blocks.

    Backslash continuations are joined before policy matching. Python here-doc
    payloads are skipped because they contain evidence data rather than shell,
    while every other here-doc is treated as shell input and inspected.
    """
    lines = text.splitlines()
    commands: list[str] = []
    index = 0
    while index < len(lines):
        match = re.match(
            r'''^(\s*)(?:run|["']run["'])\s*:\s*(.*)$''', lines[index]
        )
        if match is None:
            index += 1
            continue
        base_indent = len(match.group(1))
        value = match.group(2).strip()
        index += 1
        if value and not re.fullmatch(r"[|>][-+]?", value):
            commands.append(value)
            continue

        block: list[str] = []
        while index < len(lines):
            line = lines[index]
            if line.strip() and len(line) - len(line.lstrip()) <= base_indent:
                break
            block.append(line)
            index += 1

        heredoc_delimiter: str | None = None
        skip_heredoc_payload = False
        pending: list[str] = []
        for raw_line in block:
            line = raw_line.strip()
            if heredoc_delimiter is not None:
                if line == heredoc_delimiter:
                    heredoc_delimiter = None
                    skip_heredoc_payload = False
                    if pending:
                        commands.append(" ".join(part for part in pending if part))
                        pending = []
                    continue
                if skip_heredoc_payload:
                    continue
            if not line or line.startswith("#"):
                continue
            pending.append(line[:-1].rstrip() if line.endswith("\\") else line)
            if line.endswith("\\"):
                continue
            command = " ".join(part for part in pending if part)
            pending = []
            commands.append(command)
            heredoc = re.search(
                r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1(?:\s|$)",
                command,
            )
            if heredoc is not None:
                heredoc_delimiter = heredoc.group(2)
                prefix = command[: heredoc.start()]
                try:
                    prefix_tokens = shlex.split(prefix, comments=False, posix=True)
                except ValueError:
                    prefix_tokens = prefix.split()
                skip_heredoc_payload = any(
                    token.rsplit("/", 1)[-1]
                    in {"python", "python3", "python3.11", "python3.12", "python3.13"}
                    for token in prefix_tokens
                )
        if pending:
            commands.append(" ".join(part for part in pending if part))
    return commands


def _credential_bearing_container_invocation(command: str) -> bool:
    """Detect forbidden credentials on docker-compatible run/create commands.

    Tokenizing logical command segments makes wrapper commands (``env``,
    ``command``, ``sudo``) and path-qualified runtimes equivalent to a direct
    invocation without relying on fragile prefix matching.
    """

    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        # The workflow itself must remain valid shell. A conservative fallback
        # still catches the policy-bearing spellings in malformed text.
        tokens = re.split(r"\s+|(?=[;&|()])|(?<=[;&|()])", command)

    segment: list[str] = []
    segments: list[list[str]] = []
    for token in tokens:
        if token and all(character in ";&|()" for character in token):
            if segment:
                segments.append(segment)
                segment = []
            continue
        if token:
            segment.append(token)
    if segment:
        segments.append(segment)

    runtimes = {"docker", "podman", "nerdctl"}
    forbidden_options = {
        "--credential",
        "--credentials-file",
        "--print-credentials",
    }
    for logical in segments:
        for runtime_index, token in enumerate(logical):
            if token.rsplit("/", 1)[-1] not in runtimes:
                continue
            action_index = next(
                (
                    index
                    for index in range(runtime_index + 1, len(logical))
                    if logical[index] in {"run", "create"}
                ),
                None,
            )
            if action_index is None:
                continue
            for option in logical[action_index + 1 :]:
                name = option.split("=", 1)[0]
                if name in forbidden_options:
                    return True
    return False


def _workflow_record_before_output(
    block: str,
    output_marker: str,
    *,
    anchor: str | None = None,
) -> str:
    search_from = 0
    if anchor is not None:
        search_from = block.find(anchor)
        _expect(
            search_from >= 0,
            "WORKFLOW_RECORD_POLICY",
            f"workflow record anchor is missing: {anchor}",
        )
    output_offset = block.find(output_marker, search_from)
    _expect(
        output_offset >= 0,
        "WORKFLOW_RECORD_POLICY",
        f"workflow record output is missing: {output_marker}",
    )
    record_offset = block.rfind("record = {", search_from, output_offset)
    _expect(
        record_offset >= 0,
        "WORKFLOW_RECORD_POLICY",
        f"workflow record body is missing before: {output_marker}",
    )
    return block[record_offset:output_offset]


def _audit_workflow(root: Path) -> None:
    path = _require_regular_file(root, WORKFLOW_PATH, "WORKFLOW_FILE")
    text = path.read_text(encoding="utf-8")
    _expect(
        _sha256(path) == EXPECTED_WORKFLOW_SHA256,
        "WORKFLOW_FILE",
        "Admin image publisher bytes differ from the reviewed policy",
    )
    _expect(
        not re.search(r"(?m)^\s*(pull_request|pull_request_target|workflow_call):", text),
        "WORKFLOW_SEMANTICS",
        "PR-triggered or reusable publication workflow is forbidden",
    )
    _expect(
        "refs/heads/main" in text
        and "admin_image_publication_pending" in text
        and "workflow_dispatch:" in text
        and re.search(r"(?m)^\s+push:\s*$", text) is not None,
        "WORKFLOW_SEMANTICS",
        "publisher must be main-only and lifecycle-guarded",
    )
    blocks = _job_blocks(text)
    _expect(
        set(blocks) == {"prepare", "verify", "promote"},
        "WORKFLOW_SEMANTICS",
        f"expected prepare/verify/promote only, found {sorted(blocks)}",
    )
    _expect(
        "needs: prepare" in blocks["verify"]
        and re.search(r"(?ms)^    needs:\s*\n\s+- prepare\s*\n\s+- verify", blocks["promote"])
        is not None,
        "WORKFLOW_SEMANTICS",
        "publisher job dependency chain is not prepare -> verify -> promote",
    )
    _expect(
        blocks["prepare"].count("packages: write") == 0
        and blocks["prepare"].count("id-token: write") == 0
        and blocks["verify"].count("packages: write") == 1
        and blocks["verify"].count("id-token: write") == 1
        and blocks["promote"].count("packages: write") == 1
        and blocks["promote"].count("id-token: write") == 0,
        "WORKFLOW_PERMISSIONS",
        "write/OIDC permissions are not job-local and least-privilege",
    )
    for job, block in blocks.items():
        global_audit = "python3 scripts/verify_polaris_trusted_image.py audit --root ."
        global_audit_offset = block.find(global_audit)
        credential_offsets = [
            offset
            for marker in ("docker/login-action@", "secrets.GITHUB_TOKEN")
            if (offset := block.find(marker)) >= 0
        ]
        _expect(
            block.count("runs-on: ubuntu-24.04-arm") == 1,
            "WORKFLOW_PLATFORM",
            f"{job} must use the native arm64 runner",
        )
        _expect(
            block.count(
                "python3 scripts/verify_polaris_admin_image.py "
                "audit-publication-bootstrap --root ."
            )
            >= 1,
            "WORKFLOW_AUDIT_ORDER",
            f"{job} is missing the pre-Cosign static audit",
        )
        _expect(
            block.count(
                "python3 scripts/verify_polaris_admin_image.py audit --root ."
            )
            >= 1,
            "WORKFLOW_AUDIT_ORDER",
            f"{job} is missing the post-Cosign full audit",
        )
        _expect(
            global_audit_offset >= 0
            and (
                not credential_offsets
                or global_audit_offset < min(credential_offsets)
            ),
            "WORKFLOW_AUDIT_ORDER",
            f"{job} must run the global pending-runtime audit before credentials",
        )
    for match in re.finditer(r"(?m)^\s*uses:\s*([^\s#]+)", text):
        use = match.group(1)
        _expect("@" in use, "ACTION_PIN", f"action is not pinned: {use}")
        reference = use.rsplit("@", 1)[1]
        _expect(
            FULL_COMMIT_RE.fullmatch(reference) is not None,
            "ACTION_PIN",
            f"action is not pinned to a full commit: {use}",
        )
    required_tokens = {
        EXPECTED_IMAGE_REPOSITORY,
        EXPECTED_TRUSTED_TAG,
        EXPECTED_DEPENDENCY_REFERENCE,
        EXPECTED_RUNTIME_BASE_INDEX,
        EXPECTED_RUNTIME_BASE,
        f"RUNTIME_BASE_JAVA_VERSION: {EXPECTED_RUNTIME_BASE_JAVA_VERSION}",
        CONTAINERFILE_PATH.as_posix(),
        "runtime/admin/build/quarkus-app",
        "build-context.sha256",
        "--network none",
        "--offline",
        "--dependency-verification strict",
        ":polaris-admin:assemble",
        ":polaris-admin:quarkusAppPartsBuild",
        ":polaris-server:assemble",
        ":polaris-server:quarkusAppPartsBuild",
        "polaris-admin-image-build-input-",
        "polaris-admin-image-candidate-",
        "polaris-admin-image-publication-",
        "run-scoped quarantine push",
        "HIGH,CRITICAL",
        "os,library",
        "ignore-unfixed: false",
        "--help",
        "bootstrap",
    }
    required_tokens.update(EXPECTED_NOSQL_PROJECTS)
    required_tokens.update(
        {
            "io.quarkus:quarkus-mongodb-client",
            "mongodb",
            "polaris-persistence-nosql",
            "runtime-base-index.json",
        }
    )
    required_tokens.update(EXPECTED_CANDIDATE_EVIDENCE)
    required_tokens.update(EXPECTED_PROMOTION_EVIDENCE)
    missing = sorted(token for token in required_tokens if token not in text)
    _expect(
        not missing,
        "WORKFLOW_SEMANTICS",
        f"publisher is missing required controls or evidence names: {missing}",
    )

    dependency_record = _workflow_record_before_output(
        blocks["prepare"],
        'Path(os.environ["RUNNER_TEMP"], "dependency-input.json").write_text',
    )
    offline_record = _workflow_record_before_output(
        blocks["prepare"],
        'Path(os.environ["RUNNER_TEMP"], "offline-build.json").write_text',
    )
    build_input_record = _workflow_record_before_output(
        blocks["prepare"],
        '(artifact / "build-input.json").write_text',
    )
    sbom_record = _workflow_record_before_output(
        blocks["verify"],
        'Path("sbom-policy.json").write_text',
    )
    publication_record = _workflow_record_before_output(
        blocks["promote"],
        "path.write_text(",
        anchor='path = root / "publication.json"',
    )
    current_surface_tokens = {
        '"review_state": "reviewed_for_image_publication"',
        '"image_publication_decision": (',
        '"accepted_unmodified_for_image_publication_only"',
    }
    for name, record in (
        ("offline-build.json", offline_record),
        ("build-input.json", build_input_record),
        ("sbom-policy.json", sbom_record),
        ("publication.json", publication_record),
    ):
        _expect(
            all(token in record for token in current_surface_tokens),
            "WORKFLOW_REVIEW_STATE",
            f"{name} does not emit the reviewed image-only dependency decision",
        )
    _expect(
        'historical_surface.get("review_state") != "review_required"'
        in blocks["prepare"]
        and '"current_review_state": "reviewed_for_image_publication"'
        in dependency_record
        and '"image_publication_decision": (' in dependency_record,
        "WORKFLOW_REVIEW_STATE",
        "dependency-input.json must distinguish historical and current review state",
    )
    checkpoint_tokens = {'"review_checkpoint": {', '"pull_request": 87'}
    checkpoint_tokens.update(
        f'"{value}"'
        for value in EXPECTED_REVIEW_CHECKPOINT.values()
        if isinstance(value, str)
    )
    for name, record in (
        ("dependency-input.json", dependency_record),
        ("build-input.json", build_input_record),
        ("publication.json", publication_record),
    ):
        _expect(
            all(token in record for token in checkpoint_tokens),
            "WORKFLOW_REVIEW_STATE",
            f"{name} does not bind the exact PR #87 review checkpoint",
        )

    required_terms_literal = '["mongodb", "polaris-persistence-nosql"]'
    _expect(
        f"required_terms = {required_terms_literal}" in blocks["prepare"]
        and f"required_dependency_terms = {required_terms_literal}"
        in blocks["prepare"]
        and f"required_terms = {required_terms_literal}" in blocks["verify"]
        and f"required_terms = {required_terms_literal}" in blocks["promote"]
        and '"required_dependency_terms": required_terms' in offline_record
        and '"matching_dependency_files_by_term": (' in offline_record
        and '"required_dependency_terms": required_dependency_terms'
        in build_input_record
        and '"context_dependency_files_by_term": (' in build_input_record
        and '"required_component_terms": required_terms' in sbom_record
        and '"matching_components_by_term": matching_components_by_term'
        in sbom_record
        and 'set(matching_components) != set(required_terms)'
        in blocks["promote"]
        and 'any(not matching_components[term] for term in required_terms)'
        in blocks["promote"]
        and 'build_input.get("context_dependency_files_by_term")'
        in blocks["promote"]
        and 'offline.get("matching_dependency_files_by_term")'
        in blocks["promote"],
        "WORKFLOW_DEPENDENCY_SURFACE",
        "Admin NoSQL/Mongo matching sets are not emitted and revalidated exactly",
    )

    _expect(
        'docker buildx imagetools inspect --raw "${RUNTIME_BASE_INDEX}"'
        in blocks["verify"]
        and 'arm64[0].get("digest") != os.environ["RUNTIME_BASE_DIGEST"]'
        in blocks["verify"]
        and '"index": os.environ["RUNTIME_BASE_INDEX"]' in blocks["verify"]
        and '"linux_arm64_manifest": os.environ["RUNTIME_BASE"]'
        in blocks["verify"]
        and '"java_version": os.environ["RUNTIME_BASE_JAVA_VERSION"]'
        in blocks["verify"]
        and 'runtime_index = load("runtime-base-index.json")'
        in blocks["promote"]
        and 'builder.get("runtime_base") != {' in blocks["promote"]
        and 'arm64[0].get("digest") != os.environ["RUNTIME_BASE_DIGEST"]'
        in blocks["promote"]
        and '"openjdk version \\"${RUNTIME_BASE_JAVA_VERSION}\\""'
        in blocks["promote"]
        and '"runtime_base_index": os.environ["RUNTIME_BASE_INDEX"]'
        in publication_record
        and '"runtime_base": os.environ["RUNTIME_BASE"]' in publication_record
        and '"runtime_base_java_version": os.environ[' in publication_record,
        "WORKFLOW_RUNTIME_BASE",
        "runtime index, arm64 descriptor, or exact Java evidence is not revalidated",
    )
    _expect(
        re.search(
            r'(?s)test "\$\(find \. -mindepth 1 -maxdepth 1 -type f '
            r'\| wc -l \| tr -d \' \'\)" =\s*\\\s*"34"',
            blocks["promote"],
        )
        is not None,
        "WORKFLOW_EVIDENCE_CLOSURE",
        "final evidence directory must contain exactly 33 payloads plus evidence.sha256",
    )
    credential_bearing_invocations = [
        command
        for command in _workflow_shell_commands(text)
        if _credential_bearing_container_invocation(command)
    ]
    _expect(
        "--disable-path-validation" not in text
        and "pull_request_target" not in text
        and "credential_fallback" not in text
        and '"credentials_supplied": False' in text
        and '"credential_argument_permitted": False' in text
        and '"credential_file_read": False' in text
        and '"print_credentials_requested": False' in text
        and not credential_bearing_invocations,
        "WORKFLOW_CREDENTIAL_BOUNDARY",
        "unsafe path or credential-bearing Admin run/create invocation is present: "
        f"{credential_bearing_invocations}",
    )


def _audit_downstream_files(root: Path) -> None:
    _expect(
        not os.path.lexists(root / FUTURE_EVIDENCE_PATH),
        "PREMATURE_EVIDENCE",
        "Admin image evidence may only be committed by the later evidence review",
    )
    _expect(
        not os.path.lexists(root / FUTURE_ADMISSION_PATH),
        "PREMATURE_ADMISSION",
        "Admin image admission record exists before evidence review",
    )
    if (root / RESIDENT_IMAGE_LEDGER).is_file():
        ledger = (root / RESIDENT_IMAGE_LEDGER).read_text(encoding="utf-8")
        _expect(
            EXPECTED_IMAGE_REPOSITORY not in ledger,
            "PREMATURE_ADMISSION",
            "Admin image entered the resident-image ledger before evidence review",
        )
    gitops = root / "deploy/gitops"
    if os.path.lexists(gitops):
        _expect(
            not gitops.is_symlink() and gitops.is_dir(),
            "PREMATURE_GITOPS",
            "deploy/gitops must be a real directory while Admin admission is pending",
        )
        for path in gitops.rglob("*"):
            relative = path.relative_to(root).as_posix()
            _expect(
                not path.is_symlink(),
                "PREMATURE_GITOPS",
                f"GitOps symlink is forbidden while Admin admission is pending: {relative}",
            )
            if path.is_dir():
                continue
            _expect(
                path.is_file(),
                "PREMATURE_GITOPS",
                f"GitOps entry is not a regular file: {relative}",
            )
            _expect(
                0 <= path.stat().st_size <= 1024 * 1024,
                "PREMATURE_GITOPS",
                f"GitOps text file exceeds the inspection bound: {relative}",
            )
            try:
                text = path.read_text(encoding="utf-8").lower()
            except (OSError, UnicodeError) as error:
                _fail("PREMATURE_GITOPS", f"cannot inspect {relative}: {error}")
            _expect(
                "shirokuma-polaris-admin" not in text
                and "polaris-admin" not in text
                and "admin-image-contract.json" not in text,
                "PREMATURE_GITOPS",
                f"Admin runtime/credential GitOps content exists early: {relative}",
            )


def _load_admin_input_verifier(root: Path) -> ModuleType:
    path = _require_regular_file(root, ADMIN_INPUT_VERIFIER_PATH, "INPUT_VERIFIER")
    try:
        spec = importlib.util.spec_from_file_location(
            "_polaris_admin_input_verifier_for_image_policy", path
        )
        if spec is None or spec.loader is None:
            _fail("INPUT_VERIFIER", f"cannot load {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except (OSError, ImportError, AttributeError) as error:
        _fail("INPUT_VERIFIER", f"cannot load {path}: {error}")


def _audit_admin_dependency_static(root: Path) -> None:
    module = _load_admin_input_verifier(root)
    module.audit(root, crypto_verifier=lambda *_: None)


def _audit_admin_dependency_crypto(root: Path) -> None:
    module = _load_admin_input_verifier(root)
    module.audit(root)


def audit_publication_bootstrap(root: Path) -> None:
    """Validate the publication policy without invoking external crypto."""

    root = root.resolve()
    contract_path = _require_regular_file(root, CONTRACT_PATH, "CONTRACT_FILE")
    contract = _load_json(contract_path)
    _validate_contract(contract)
    _expect(
        _sha256(contract_path) == EXPECTED_CONTRACT_SHA256,
        "CONTRACT_FILE",
        "admin-image-contract.json bytes differ from the reviewed policy",
    )
    _expect(
        _sha256(_require_regular_file(root, SOURCE_PATH, "SOURCE_FILE"))
        == EXPECTED_SOURCE_SHA256,
        "SOURCE_FILE",
        "source.json bytes changed",
    )
    _expect(
        _sha256(
            _require_regular_file(root, ADMIN_INPUT_CONTRACT_PATH, "INPUT_CONTRACT")
        )
        == EXPECTED_ADMIN_INPUT_CONTRACT_SHA256,
        "INPUT_CONTRACT",
        "reviewed Admin build-input contract bytes changed",
    )
    _expect(
        _sha256(
            _require_regular_file(root, ADMIN_INPUT_VERIFIER_PATH, "INPUT_VERIFIER")
        )
        == EXPECTED_ADMIN_INPUT_VERIFIER_SHA256,
        "INPUT_VERIFIER",
        "Admin build-input verifier bytes changed",
    )
    _expect(
        not (root / RETIRED_ADMIN_INPUT_WORKFLOW).exists(),
        "RETIRED_PUBLISHER",
        "retired Admin dependency publisher was restored",
    )
    _audit_admin_dependency_static(root)
    _audit_containerfile(root)
    _audit_workflow(root)
    _audit_downstream_files(root)


def audit(
    root: Path,
    *,
    dependency_crypto_auditor: Callable[[Path], None] | None = None,
) -> None:
    audit_publication_bootstrap(root)
    (dependency_crypto_auditor or _audit_admin_dependency_crypto)(root.resolve())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    static_parser = subparsers.add_parser("audit-publication-bootstrap")
    static_parser.add_argument("--root", type=Path, default=Path("."))
    audit_parser = subparsers.add_parser("audit")
    audit_parser.add_argument("--root", type=Path, default=Path("."))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "audit-publication-bootstrap":
            audit_publication_bootstrap(args.root)
            message = "static Admin image publication policy verified"
        else:
            audit(args.root)
            message = "Admin image publication policy and dependency trust verified"
    except ContractError as error:
        print(f"polaris-admin-image: {error}", file=sys.stderr)
        return 1
    print(
        f"polaris-admin-image: {message}; admission/runtime/Flux/credentials remain disabled"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
