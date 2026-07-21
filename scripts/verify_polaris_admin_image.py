#!/usr/bin/env python3
"""Fail-closed audit for reviewed Polaris Admin linux/arm64 image evidence.

The one-shot publisher is retired. ``audit`` validates the repository-retained
image evidence and repeats the retained Admin dependency snapshot's
cryptographic verification. ``audit-publication-bootstrap`` remains as a
compatibility alias for the static, network-free portion of the reviewed gate.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
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
EVIDENCE_PATH = Path(
    "bootstrap/polaris/v1.6.0/admin-image-evidence"
)
RELEASE_EVIDENCE_PATH = Path(
    "bootstrap/polaris/v1.6.0/admin-release-evidence.json"
)
ADMISSION_PATH = Path("bootstrap/polaris/v1.6.0/admin-admission.json")
ADMISSION_EVIDENCE_PATH = Path("security/evidence/polaris-admin-v1.6.0")
RESIDENT_IMAGE_LEDGER = Path("security/resident-images.json")

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FULL_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
CHECKSUM_LINE_RE = re.compile(r"^([0-9a-f]{64})  ([^\s].*)$")

EXPECTED_SOURCE_SHA256 = (
    "7d14b606dd756f501644190c10deb64a1e046d46faacd0f76f92501ccd5185bb"
)
EXPECTED_ADMIN_INPUT_CONTRACT_SHA256 = (
    "6d56a2b086591f746bf272ff9388529013780b36950834e5233e41c34b16e400"
)
# Rebound after the policy files are stable. These constants deliberately pin
# exact bytes in addition to the semantic checks below.
EXPECTED_CONTRACT_SHA256 = (
    "c5aacf801c54413fcc2e8b7a460527f56dabcc65ef560d1ab879e3c58c33c862"
)
EXPECTED_ADMIN_INPUT_VERIFIER_SHA256 = (
    "5e153aacecaec7c313d9caba5b38ef65ff92f7eed25746e879222a4cdf441a42"
)
EXPECTED_CONTAINERFILE_SHA256 = (
    "cecd7e40f0bd3b2f5b0de90233677772c0c55c745f4f4cc975eda83b42f40112"
)
EXPECTED_PUBLISHER_WORKFLOW_SHA256 = (
    "e064dd4ded373c1529dc59cdaee695791fd6bce356c4383eee7b70746d599d0d"
)
EXPECTED_RELEASE_EVIDENCE_SHA256 = (
    "8d3f4b4550e4cebbd7e9d83d07376c7b5ba5f0013a49a044624d914d70df7c10"
)
EXPECTED_RELEASE_EVIDENCE_SIZE = 6_029
EXPECTED_EVIDENCE_MANIFEST_SHA256 = (
    "f1290ccf0fff852fb965d46ab55c12623ce15e36e15b4bbeb6627999bf11a97f"
)
EXPECTED_EVIDENCE_MANIFEST_SIZE = 3_105
EXPECTED_PUBLICATION_SHA256 = (
    "d6051d8d30c2cf890409c8a484233b2ae56b745369639c3cc680170479647063"
)
EXPECTED_PUBLICATION_SIZE = 4_009
EXPECTED_ADMISSION_SHA256 = (
    "99d1fc36c2960584be7b529c9601e6667deae842f2eb16fd36c949b7c3efaa14"
)
EXPECTED_ADMISSION_EVIDENCE_MANIFEST_SHA256 = (
    "9106bb4e7d2f25c8a2443ee17541c7f3c3586a14e0bac57160af0981bf1389ca"
)
EXPECTED_ADMISSION_PREFLIGHT_SHA256 = (
    "db1b2d3d0f3b17437580c10d74393e5c7acda1ba1a3f87ddea4f03892f1ef86d"
)
EXPECTED_ADMISSION_SUPPLY_CHAIN_SHA256 = (
    "0de48604bbc40cbc012ae12b8bb39d4fe94c4007c6ff05e27057719cc617601a"
)
EXPECTED_ADMISSION_SBOM_SHA256 = (
    "b7c5a9e3fab873b9a655059ab0297e45a70273fff95eef62a1cdd8afa28589e8"
)
EXPECTED_ADMISSION_TRIVY_SHA256 = (
    "a067f022234f60b64f6fe9add3998cc8bc0d26191facaedf8a1112014c5ad91e"
)
EXPECTED_ADMISSION_TRIVY_VERSION_SHA256 = (
    "e1aa93c50d458f6b00081dc00bf03d973ff6cb8f4dd48c73c3bc838e8899efa7"
)

EXPECTED_REPOSITORY = "TommyKammy/Shirokuma"
EXPECTED_REF = "refs/heads/main"
EXPECTED_IMAGE_REPOSITORY = "ghcr.io/tommykammy/shirokuma-polaris-admin"
EXPECTED_TRUSTED_TAG = "1.6.0-arm64"
EXPECTED_IMAGE_DIGEST = (
    "sha256:a56d09406c9dc1602cc49c0e792035c1163abf0e975fe702ef7e775c445317dd"
)
EXPECTED_IMAGE_REFERENCE = EXPECTED_IMAGE_REPOSITORY + "@" + EXPECTED_IMAGE_DIGEST
EXPECTED_TRUSTED_TAG_REFERENCE = EXPECTED_IMAGE_REPOSITORY + ":" + EXPECTED_TRUSTED_TAG
EXPECTED_PUBLISHER_SOURCE_SHA = "a1339e71bc3a19814102bd689fb88bfab4fb71c5"
EXPECTED_PUBLISHER_RUN_ID = "29807128630"
EXPECTED_PUBLISHER_RUN_ATTEMPT = "1"
EXPECTED_PROVENANCE_URL = (
    "https://github.com/TommyKammy/Shirokuma/attestations/36296256"
)
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
    "dc43b39c47f1729dc772a9b8af7222757fac6c8cfa8a0802829af665b1c89925"
)
EXPECTED_RUNTIME_BASE_INDEX = (
    "docker.io/library/amazoncorretto@sha256:"
    "30b1b2246cee9a98c9bf8a11537a04f1eaf8c59279b0c70ae02d7e5b934edeaa"
)
EXPECTED_RUNTIME_BASE_JAVA_VERSION = "21.0.11"
EXPECTED_RUNTIME_BASE_OS = "Alpine Linux"
EXPECTED_RUNTIME_BASE_OS_VERSION = "3.24.1"
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
    "runtime-base-os-version.txt",
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


def _parse_timestamp(value: Any) -> datetime:
    text = str(value)
    match = re.fullmatch(r"(.+?\.\d{6})\d+(Z|[+-]\d{2}:\d{2})", text)
    if match:
        text = "".join(match.groups())
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


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
            "reference",
            "digest",
            "release_evidence",
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
        publication["enabled"] is False
        and publication["state"] == "approved_for_admin_admission"
        and publication["repository"] == EXPECTED_IMAGE_REPOSITORY
        and publication["trusted_tag"] == EXPECTED_TRUSTED_TAG
        and publication["reference"] == EXPECTED_IMAGE_REFERENCE
        and publication["digest"] == EXPECTED_IMAGE_DIGEST
        and publication["release_evidence"]
        == {
            "path": RELEASE_EVIDENCE_PATH.as_posix(),
            "sha256": EXPECTED_RELEASE_EVIDENCE_SHA256,
            "size": EXPECTED_RELEASE_EVIDENCE_SIZE,
        }
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
            "os": EXPECTED_RUNTIME_BASE_OS,
            "os_version": EXPECTED_RUNTIME_BASE_OS_VERSION,
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
            "admission_permitted": True,
            "release_evidence_committed": True,
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
            "sha256": EXPECTED_PUBLISHER_WORKFLOW_SHA256,
            "repository": EXPECTED_REPOSITORY,
            "ref": EXPECTED_REF,
            "oidc_identity": EXPECTED_WORKFLOW_IDENTITY,
            "jobs": ["prepare", "verify", "promote"],
            "artifacts": {
                "build_input_prefix": "polaris-admin-image-build-input-",
                "candidate_prefix": "polaris-admin-image-candidate-",
                "publication_prefix": "polaris-admin-image-publication-",
            },
            "source_sha": EXPECTED_PUBLISHER_SOURCE_SHA,
            "workflow_sha": EXPECTED_PUBLISHER_SOURCE_SHA,
            "run_id": EXPECTED_PUBLISHER_RUN_ID,
            "run_attempt": EXPECTED_PUBLISHER_RUN_ATTEMPT,
            "retired": True,
        },
        "WORKFLOW_CONTRACT",
        "retired publisher checkpoint identity, run, artifacts, or hash changed",
    )


def _validate_evidence(evidence: Mapping[str, Any]) -> None:
    _expect(
        evidence
        == {
            "candidate_retention_days": 30,
            "final_retention_days": 30,
            "actions_artifact_role": (
                "finite-retention transport copy; repository evidence is authoritative"
            ),
            "directory": EVIDENCE_PATH.as_posix(),
            "candidate_required": EXPECTED_CANDIDATE_EVIDENCE,
            "promotion_required": EXPECTED_PROMOTION_EVIDENCE,
            "checksum_manifest": "evidence.sha256",
            "checksum_manifest_sha256": EXPECTED_EVIDENCE_MANIFEST_SHA256,
            "checksum_manifest_size": EXPECTED_EVIDENCE_MANIFEST_SIZE,
            "checksum_manifest_entries": 34,
            "directory_file_count_after_review": 35,
            "review_state": "reviewed_for_admin_admission",
            "raw_logs_permitted": False,
        },
        "EVIDENCE_POLICY",
        "candidate/promotion evidence closure or retention policy changed",
    )
    _expect(
        len(EXPECTED_CANDIDATE_EVIDENCE) == 30
        and len(EXPECTED_PROMOTION_EVIDENCE) == 4
        and len(set(EXPECTED_CANDIDATE_EVIDENCE + EXPECTED_PROMOTION_EVIDENCE))
        == 34,
        "EVIDENCE_POLICY",
        "internal expected evidence inventory is not a 30+4 closed set",
    )


def _validate_downstream(contract: Mapping[str, Any]) -> None:
    _expect(
        contract["admission"]
        == {
            "state": "approved_admin_image_admission",
            "permitted": True,
            "record": ADMISSION_PATH.as_posix(),
        }
        and contract["runtime"]
        == {"state": "pending_admin_runtime_activation", "enabled": False}
        and contract["gitops"]
        == {
            "state": "blocked_admin_runtime_activation",
            "resources_enabled": False,
        }
        and contract["credentials"]
        == {
            "state": "blocked_admin_runtime_activation",
            "material_permitted": False,
        }
        and contract["downstream_gates"]
        == {
            "admin_image_admitted": True,
            "resident_image_ledger_enabled": True,
            "admin_runtime_enabled": False,
            "gitops_resources_enabled": False,
            "credential_material_permitted": False,
            "next_checkpoint": (
                "activate the admitted Admin image through credential-safe Flux resources"
            ),
        },
        "DOWNSTREAM_GATE",
        "admission, resident ledger, runtime, GitOps, or credential gate drifted",
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
        contract["schema_version"] == 3
        and contract["component"] == "polaris-admin"
        and contract["version"] == "1.6.0"
        and contract["platform"] == "linux/arm64",
        "CONTRACT_IDENTITY",
        "schema, component, version, or platform changed",
    )
    _expect(
        contract["lifecycle"]
        == {
            "state": "admin_runtime_activation_pending",
            "next_state": "admin_runtime_acceptance_pending",
        },
        "LIFECYCLE_STATE",
        "Admin image lifecycle skipped runtime activation or acceptance review",
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
        f'dev.shirokuma.runtime-base.os="{EXPECTED_RUNTIME_BASE_OS}"',
        (
            'dev.shirokuma.runtime-base.os-version="'
            f'{EXPECTED_RUNTIME_BASE_OS_VERSION}"'
        ),
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


def _parse_checksum_manifest(path: Path, code: str) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        _fail(code, f"cannot read checksum manifest {path}: {error}")
    records: dict[str, str] = {}
    for line in lines:
        match = CHECKSUM_LINE_RE.fullmatch(line)
        _expect(match is not None, code, f"invalid checksum line: {line!r}")
        digest, filename = match.groups()
        normalized = filename[2:] if filename.startswith("./") else filename
        _expect(
            normalized not in records
            and Path(normalized).name == normalized
            and normalized not in {".", "..", "evidence.sha256"},
            code,
            f"unsafe, self-referential, or duplicate checksum filename: {filename!r}",
        )
        records[normalized] = digest
    return records


def _audit_evidence_inventory(root: Path) -> None:
    directory = root / EVIDENCE_PATH
    _expect(
        directory.is_dir() and not directory.is_symlink(),
        "EVIDENCE_INVENTORY",
        "Admin image evidence root must be a real directory",
    )
    try:
        directory.resolve(strict=True).relative_to(root.resolve())
    except (OSError, ValueError) as error:
        _fail("EVIDENCE_INVENTORY", f"evidence root escapes repository: {error}")
    expected_payloads = set(EXPECTED_CANDIDATE_EVIDENCE + EXPECTED_PROMOTION_EVIDENCE)
    expected = {"evidence.sha256", *expected_payloads}
    actual = {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    _expect(
        actual == expected,
        "EVIDENCE_INVENTORY",
        f"exact 35-file inventory required; expected={sorted(expected)!r}, "
        f"actual={sorted(actual)!r}",
    )
    for filename in expected:
        _require_regular_file(root, EVIDENCE_PATH / filename, "EVIDENCE_INVENTORY")

    manifest_path = directory / "evidence.sha256"
    _expect(
        _sha256(manifest_path) == EXPECTED_EVIDENCE_MANIFEST_SHA256
        and manifest_path.stat().st_size == EXPECTED_EVIDENCE_MANIFEST_SIZE,
        "EVIDENCE_MANIFEST",
        "reviewed evidence.sha256 bytes changed",
    )
    records = _parse_checksum_manifest(manifest_path, "EVIDENCE_MANIFEST")
    _expect(
        set(records) == expected_payloads and len(records) == 34,
        "EVIDENCE_MANIFEST",
        "evidence.sha256 must bind exactly the 30 candidate and four promotion records",
    )
    for filename, digest in records.items():
        _expect(
            _sha256(directory / filename) == digest,
            "EVIDENCE_BYTES",
            f"retained evidence digest mismatch: {filename}",
        )


def _audit_release_evidence(root: Path) -> None:
    release_path = _require_regular_file(root, RELEASE_EVIDENCE_PATH, "RELEASE_EVIDENCE")
    _expect(
        _sha256(release_path) == EXPECTED_RELEASE_EVIDENCE_SHA256
        and release_path.stat().st_size == EXPECTED_RELEASE_EVIDENCE_SIZE,
        "RELEASE_EVIDENCE",
        "admin-release-evidence.json bytes changed",
    )
    release = _load_json(release_path)
    _expect(
        release.get("schema_version") == 1
        and release.get("component") == "polaris-admin"
        and release.get("version") == "1.6.0"
        and release.get("platform") == "linux/arm64"
        and release.get("state") == "approved_for_admin_admission"
        and release.get("admitted") is False
        and release.get("reference") == EXPECTED_IMAGE_REFERENCE
        and release.get("digest") == EXPECTED_IMAGE_DIGEST
        and release.get("trusted_tag") == EXPECTED_TRUSTED_TAG_REFERENCE
        and release.get("trusted_tag_role") == "non_authoritative_pointer"
        and release.get("next_boundary")
        == "admit the exact Admin image before runtime or Flux activation",
        "RELEASE_EVIDENCE",
        "release identity, state, digest, or next boundary changed",
    )
    publisher = release.get("publisher_checkpoint")
    _expect(
        isinstance(publisher, Mapping)
        and publisher.get("repository") == EXPECTED_REPOSITORY
        and publisher.get("workflow") == WORKFLOW_PATH.as_posix()
        and publisher.get("workflow_file_sha256")
        == EXPECTED_PUBLISHER_WORKFLOW_SHA256
        and publisher.get("ref") == EXPECTED_REF
        and publisher.get("event") == "push"
        and publisher.get("source_sha") == EXPECTED_PUBLISHER_SOURCE_SHA
        and publisher.get("workflow_sha") == EXPECTED_PUBLISHER_SOURCE_SHA
        and publisher.get("run_id") == EXPECTED_PUBLISHER_RUN_ID
        and publisher.get("run_attempt") == EXPECTED_PUBLISHER_RUN_ATTEMPT
        and publisher.get("publisher_contract_sha256")
        == "3afb9235d91a1b5a00f861383068fbc91f16464df177183a181434d541e64247"
        and publisher.get("retired") is True,
        "PUBLISHER_CHECKPOINT",
        "retired publisher checkpoint changed",
    )
    evidence = release.get("evidence")
    _expect(
        isinstance(evidence, Mapping)
        and evidence.get("directory") == EVIDENCE_PATH.as_posix()
        and evidence.get("checksum_manifest")
        == (EVIDENCE_PATH / "evidence.sha256").as_posix()
        and evidence.get("checksum_manifest_sha256")
        == EXPECTED_EVIDENCE_MANIFEST_SHA256
        and evidence.get("checksum_manifest_size")
        == EXPECTED_EVIDENCE_MANIFEST_SIZE
        and evidence.get("checksum_manifest_entries") == 34
        and evidence.get("directory_file_count") == 35
        and evidence.get("raw_logs_retained") is False,
        "RELEASE_EVIDENCE",
        "release evidence closure changed",
    )
    gates = release.get("downstream_gates")
    _expect(
        gates
        == {
            "admin_image_admitted": False,
            "resident_image_ledger_enabled": False,
            "admin_runtime_enabled": False,
            "gitops_resources_enabled": False,
            "credential_material_permitted": False,
        },
        "RELEASE_EVIDENCE",
        "release evidence opened a downstream gate",
    )


def _audit_evidence_semantics(root: Path) -> None:
    directory = root / EVIDENCE_PATH
    publication_path = directory / "publication.json"
    _expect(
        _sha256(publication_path) == EXPECTED_PUBLICATION_SHA256
        and publication_path.stat().st_size == EXPECTED_PUBLICATION_SIZE,
        "PUBLICATION_EVIDENCE",
        "publication.json bytes changed",
    )
    publication = _load_json(publication_path)
    _expect(
        publication.get("schema_version") == 1
        and publication.get("component") == "polaris-admin"
        and publication.get("version") == "1.6.0"
        and publication.get("platform") == "linux/arm64"
        and publication.get("state") == "admin_image_evidence_review_pending"
        and publication.get("reference") == EXPECTED_IMAGE_REFERENCE
        and publication.get("trusted_tag") == EXPECTED_TRUSTED_TAG_REFERENCE
        and publication.get("trusted_tag_role") == "non_authoritative_pointer"
        and publication.get("anonymous_pull") is True
        and publication.get("promoted") is True
        and publication.get("promotion_anonymous_verification") is True
        and publication.get("admitted") is False
        and publication.get("dependency_reference") == EXPECTED_DEPENDENCY_REFERENCE
        and publication.get("runtime_base") == EXPECTED_RUNTIME_BASE
        and publication.get("runtime_base_index") == EXPECTED_RUNTIME_BASE_INDEX
        and publication.get("runtime_base_java_version")
        == EXPECTED_RUNTIME_BASE_JAVA_VERSION
        and publication.get("runtime_base_os") == EXPECTED_RUNTIME_BASE_OS
        and publication.get("runtime_base_os_version")
        == EXPECTED_RUNTIME_BASE_OS_VERSION
        and publication.get("slsa_provenance") == EXPECTED_PROVENANCE_URL,
        "PUBLICATION_EVIDENCE",
        "publication identity, gates, runtime base, or provenance changed",
    )
    workflow = publication.get("workflow")
    _expect(
        isinstance(workflow, Mapping)
        and workflow.get("repository") == EXPECTED_REPOSITORY
        and workflow.get("ref") == EXPECTED_REF
        and workflow.get("event") == "push"
        and workflow.get("source_sha") == EXPECTED_PUBLISHER_SOURCE_SHA
        and workflow.get("workflow_sha") == EXPECTED_PUBLISHER_SOURCE_SHA
        and workflow.get("run_id") == EXPECTED_PUBLISHER_RUN_ID
        and workflow.get("run_attempt") == EXPECTED_PUBLISHER_RUN_ATTEMPT,
        "PUBLICATION_EVIDENCE",
        "publication workflow identity or run changed",
    )
    _expect(
        publication.get("downstream_gates")
        == {
            "admin_image_admitted": False,
            "admin_runtime_enabled": False,
            "credential_material_permitted": False,
            "gitops_resources_enabled": False,
            "resident_image_ledger_enabled": False,
        },
        "PUBLICATION_EVIDENCE",
        "publisher evidence opened a downstream gate",
    )
    for filename in (
        "anonymous-image-manifest.json",
        "image-manifest.json",
        "trusted-tag-manifest.json",
    ):
        _expect(
            _sha256(directory / filename) == EXPECTED_IMAGE_DIGEST.removeprefix("sha256:"),
            "IMAGE_MANIFEST",
            f"{filename} does not encode the reviewed image digest",
        )

    cosign = _load_json(directory / "cosign-verify.json")
    constraints = cosign.get("certificate_constraints")
    _expect(
        cosign.get("reference") == EXPECTED_IMAGE_REFERENCE
        and cosign.get("detached_bundle_verified") is True
        and cosign.get("registry_signature_verified") is True
        and isinstance(constraints, Mapping)
        and constraints.get("issuer") == "https://token.actions.githubusercontent.com"
        and constraints.get("identity") == EXPECTED_WORKFLOW_IDENTITY
        and constraints.get("github_workflow_repository") == EXPECTED_REPOSITORY
        and constraints.get("github_workflow_ref") == EXPECTED_REF
        and constraints.get("github_workflow_sha") == EXPECTED_PUBLISHER_SOURCE_SHA
        and constraints.get("github_workflow_trigger") == "push",
        "SIGNATURE_EVIDENCE",
        "signature verification identity or result changed",
    )
    promotion_cosign = (directory / "promotion-cosign-verify.json").read_text(
        encoding="utf-8"
    )
    _expect(
        EXPECTED_IMAGE_REFERENCE in promotion_cosign
        and EXPECTED_IMAGE_DIGEST in promotion_cosign
        and "https://sigstore.dev/cosign/sign/v1" in promotion_cosign
        and "https://slsa.dev/provenance/v1" in promotion_cosign
        and "https://cyclonedx.org/bom" in promotion_cosign
        and "https://shirokuma.dev/attestations/trivy/v1" in promotion_cosign,
        "SIGNATURE_EVIDENCE",
        "post-promotion signature and attestation set changed",
    )
    for filename in ("slsa-verify.json", "promotion-slsa-verify.json"):
        text = (directory / filename).read_text(encoding="utf-8")
        _expect(
            EXPECTED_IMAGE_DIGEST.removeprefix("sha256:") in text
            and EXPECTED_WORKFLOW_IDENTITY in text
            and EXPECTED_PUBLISHER_SOURCE_SHA in text,
            "PROVENANCE_EVIDENCE",
            f"{filename} lost the exact image or publisher identity",
        )

    admin_help = _load_json(directory / "admin-help.json")
    bootstrap_help = _load_json(directory / "admin-bootstrap-help.json")
    inspect = _load_json(directory / "admin-container-inspect.json")
    smoke_policy = _load_json(directory / "admin-smoke-log-policy.json")
    _expect(
        admin_help.get("reference") == EXPECTED_IMAGE_REFERENCE
        and admin_help.get("command") == ["--help"]
        and admin_help.get("exit_code") == 0
        and admin_help.get("network") == "none"
        and admin_help.get("credentials_supplied") is False
        and bootstrap_help.get("reference") == EXPECTED_IMAGE_REFERENCE
        and bootstrap_help.get("command") == ["bootstrap", "--help"]
        and bootstrap_help.get("exit_code") == 0
        and bootstrap_help.get("network") == "none"
        and bootstrap_help.get("credentials_supplied") is False
        and bootstrap_help.get("credential_file_read") is False
        and bootstrap_help.get("print_credentials_requested") is False
        and inspect.get("reference") == EXPECTED_IMAGE_REFERENCE
        and inspect.get("result") == "passed"
        and smoke_policy.get("result") == "passed"
        and smoke_policy.get("credentials_supplied") is False
        and smoke_policy.get("raw_logs_retained") is False,
        "CLI_EVIDENCE",
        "Admin help, bootstrap help, inspect, or credential-safe log policy changed",
    )

    sbom = _load_json(directory / "polaris-admin-1.6.0-arm64.cdx.json")
    components = sbom.get("components")
    _expect(
        sbom.get("bomFormat") == "CycloneDX"
        and sbom.get("specVersion") == "1.7"
        and isinstance(components, list)
        and len(components) == 1_618,
        "SBOM_EVIDENCE",
        "CycloneDX identity or component count changed",
    )
    component_text = json.dumps(components, sort_keys=True).lower()
    _expect(
        "mongodb" in component_text and "polaris-persistence-nosql" in component_text,
        "SBOM_EVIDENCE",
        "required MongoDB or Polaris NoSQL dependency surface is absent",
    )
    trivy = _load_json(directory / "trivy.json")
    results = trivy.get("Results")
    _expect(isinstance(results, list), "TRIVY_EVIDENCE", "Trivy Results missing")
    vulnerabilities = [
        finding
        for result in results
        if isinstance(result, Mapping)
        for finding in (result.get("Vulnerabilities") or [])
        if isinstance(finding, Mapping)
    ]
    high_or_critical = [
        finding
        for finding in vulnerabilities
        if finding.get("Severity") in {"HIGH", "CRITICAL"}
    ]
    metadata = trivy.get("Metadata")
    os_record = metadata.get("OS") if isinstance(metadata, Mapping) else None
    _expect(
        trivy.get("SchemaVersion") == 2
        and trivy.get("ArtifactName") == EXPECTED_IMAGE_REFERENCE
        and trivy.get("ArtifactType") == "container_image"
        and not high_or_critical
        and isinstance(os_record, Mapping)
        and os_record.get("Family") == "alpine"
        and os_record.get("Name") == EXPECTED_RUNTIME_BASE_OS_VERSION,
        "TRIVY_EVIDENCE",
        "Trivy target, Alpine identity, or zero High/Critical gate changed",
    )


def _audit_downstream_files(root: Path) -> None:
    admission_path = _require_regular_file(root, ADMISSION_PATH, "ADMIN_ADMISSION")
    _expect(
        _sha256(admission_path) == EXPECTED_ADMISSION_SHA256,
        "ADMIN_ADMISSION",
        "admin-admission.json bytes differ from the reviewed decision",
    )
    admission = _load_json(admission_path)
    _expect_exact_keys(
        admission,
        {
            "schema_version",
            "component",
            "version",
            "platform",
            "admission",
            "state",
            "decision_at",
            "source",
            "reference",
            "digest",
            "image_contract",
            "release_evidence",
            "reviewed_evidence_manifest",
            "admission_evidence_manifest",
            "anonymous_preflight",
            "vulnerability_database",
            "scans",
            "supply_chain",
            "resident_ledger",
            "runtime",
            "gitops",
            "credentials",
        },
        "ADMIN_ADMISSION",
        "root",
    )
    _expect(
        admission["schema_version"] == 1
        and admission["component"] == "polaris-admin"
        and admission["version"] == "1.6.0"
        and admission["platform"] == "linux/arm64"
        and admission["admission"] == "approved"
        and admission["state"] == "admin_runtime_activation_pending"
        and admission["source"] == "https://github.com/apache/polaris"
        and admission["reference"] == EXPECTED_IMAGE_REFERENCE
        and admission["digest"] == EXPECTED_IMAGE_DIGEST,
        "ADMIN_ADMISSION",
        "Admin admission identity, digest, or lifecycle changed",
    )
    _expect(
        admission["image_contract"]
        == {"path": CONTRACT_PATH.as_posix(), "sha256": EXPECTED_CONTRACT_SHA256}
        and admission["release_evidence"]
        == {
            "path": RELEASE_EVIDENCE_PATH.as_posix(),
            "sha256": EXPECTED_RELEASE_EVIDENCE_SHA256,
        }
        and admission["reviewed_evidence_manifest"]
        == {
            "path": (EVIDENCE_PATH / "evidence.sha256").as_posix(),
            "sha256": EXPECTED_EVIDENCE_MANIFEST_SHA256,
            "size": EXPECTED_EVIDENCE_MANIFEST_SIZE,
            "entries": 34,
        },
        "ADMIN_ADMISSION",
        "reviewed Admin contract or publication evidence binding changed",
    )
    expected_admission_files = {
        "anonymous-preflight.json": EXPECTED_ADMISSION_PREFLIGHT_SHA256,
        "polaris-admin-1.6.0-arm64.cdx.json": EXPECTED_ADMISSION_SBOM_SHA256,
        "supply-chain.json": EXPECTED_ADMISSION_SUPPLY_CHAIN_SHA256,
        "trivy-version.json": EXPECTED_ADMISSION_TRIVY_VERSION_SHA256,
        "trivy.json": EXPECTED_ADMISSION_TRIVY_SHA256,
    }
    evidence_directory = root / ADMISSION_EVIDENCE_PATH
    _expect(
        evidence_directory.is_dir() and not evidence_directory.is_symlink(),
        "ADMIN_ADMISSION_EVIDENCE",
        "Admin admission evidence root must be a real directory",
    )
    actual = {
        path.relative_to(evidence_directory).as_posix()
        for path in evidence_directory.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    _expect(
        actual == {"evidence.sha256", *expected_admission_files},
        "ADMIN_ADMISSION_EVIDENCE",
        "Admin admission evidence must be an exact six-file closed set",
    )
    evidence_manifest = _require_regular_file(
        root, ADMISSION_EVIDENCE_PATH / "evidence.sha256", "ADMIN_ADMISSION_EVIDENCE"
    )
    _expect(
        _sha256(evidence_manifest) == EXPECTED_ADMISSION_EVIDENCE_MANIFEST_SHA256
        and evidence_manifest.stat().st_size == 438
        and admission["admission_evidence_manifest"]
        == {
            "path": evidence_manifest.relative_to(root).as_posix(),
            "sha256": EXPECTED_ADMISSION_EVIDENCE_MANIFEST_SHA256,
            "size": 438,
            "entries": 5,
        },
        "ADMIN_ADMISSION_EVIDENCE",
        "Admin admission evidence manifest binding changed",
    )
    records = _parse_checksum_manifest(evidence_manifest, "ADMIN_ADMISSION_EVIDENCE")
    _expect(
        records == expected_admission_files,
        "ADMIN_ADMISSION_EVIDENCE",
        "Admin admission evidence manifest entries changed",
    )
    for filename, expected_sha256 in expected_admission_files.items():
        path = _require_regular_file(
            root, ADMISSION_EVIDENCE_PATH / filename, "ADMIN_ADMISSION_EVIDENCE"
        )
        _expect(
            _sha256(path) == expected_sha256,
            "ADMIN_ADMISSION_EVIDENCE",
            f"Admin admission evidence bytes changed: {filename}",
        )

    preflight = _load_json(evidence_directory / "anonymous-preflight.json")
    _expect(
        preflight
        == {
            "schema_version": 1,
            "component": "polaris-admin",
            "version": "1.6.0",
            "platform": "linux/arm64",
            "reference": EXPECTED_IMAGE_REFERENCE,
            "preflighted_at": "2026-07-21T09:19:33Z",
            "network_boundary": "anonymous-empty-docker-config",
            "tool": {"name": "crane", "version": "0.21.7"},
            "manifest": {
                "sha256": EXPECTED_IMAGE_DIGEST.removeprefix("sha256:"),
                "size": 2006,
                "media_type": "application/vnd.oci.image.manifest.v1+json",
                "config_digest": (
                    "sha256:e91e9c08b0283b742fa086c0e6af4772babd7bfdac0af20aebdd01a22327f64a"
                ),
                "layer_count": 9,
            },
        }
        and admission["anonymous_preflight"]
        == {
            "path": (ADMISSION_EVIDENCE_PATH / "anonymous-preflight.json").as_posix(),
            "sha256": EXPECTED_ADMISSION_PREFLIGHT_SHA256,
            "preflighted_at": "2026-07-21T09:19:33Z",
            "network_boundary": "anonymous-empty-docker-config",
            "tool": {"name": "crane", "version": "0.21.7"},
        },
        "ADMIN_ADMISSION_PREFLIGHT",
        "anonymous exact-digest preflight changed",
    )
    try:
        decision_at = _parse_timestamp(admission["decision_at"])
        database_updated_at = _parse_timestamp(
            admission["vulnerability_database"]["updated_at"]
        )
    except (KeyError, TypeError, ValueError) as error:
        _fail("ADMIN_ADMISSION_SCAN", f"invalid admission or database timestamp: {error}")
    _expect(
        admission["decision_at"] == "2026-07-21T09:19:33Z"
        and admission["vulnerability_database"]
        == {
            "path": (ADMISSION_EVIDENCE_PATH / "trivy-version.json").as_posix(),
            "sha256": EXPECTED_ADMISSION_TRIVY_VERSION_SHA256,
            "updated_at": "2026-07-21T01:08:43.916306317Z",
            "maximum_age_hours_at_decision": 24,
        }
        and database_updated_at <= decision_at
        and decision_at - database_updated_at <= timedelta(hours=24),
        "ADMIN_ADMISSION_SCAN",
        "Trivy database was not within 24 hours of the admission decision",
    )
    sbom = _load_json(evidence_directory / "polaris-admin-1.6.0-arm64.cdx.json")
    scan = _load_json(evidence_directory / "trivy.json")
    scans = admission["scans"]
    _expect(
        scans["sbom"]
        == {
            "path": (
                ADMISSION_EVIDENCE_PATH / "polaris-admin-1.6.0-arm64.cdx.json"
            ).as_posix(),
            "sha256": EXPECTED_ADMISSION_SBOM_SHA256,
            "format": "CycloneDX",
            "spec_version": "1.7",
            "component_count": 1618,
        }
        and sbom.get("bomFormat") == "CycloneDX"
        and sbom.get("specVersion") == "1.7"
        and len(sbom.get("components", [])) == 1618,
        "ADMIN_ADMISSION_SBOM",
        "resident Admin SBOM identity or component closure changed",
    )
    high_or_critical = [
        vulnerability
        for result in scan.get("Results", [])
        for vulnerability in result.get("Vulnerabilities") or []
        if vulnerability.get("Severity") in {"HIGH", "CRITICAL"}
    ]
    observed_scopes = [
        {
            "class": result.get("Class"),
            "type": result.get("Type"),
            "package_count": len(result.get("Packages") or []),
        }
        for result in scan.get("Results", [])
    ]
    _expect(
        scans["vulnerability_scan"]
        == {
            "path": (ADMISSION_EVIDENCE_PATH / "trivy.json").as_posix(),
            "sha256": EXPECTED_ADMISSION_TRIVY_SHA256,
            "artifact_reference": EXPECTED_IMAGE_REFERENCE,
            "scopes": [
                {"class": "os-pkgs", "type": "alpine", "package_count": 29},
                {"class": "lang-pkgs", "type": "jar", "package_count": 377},
            ],
            "high": 0,
            "critical": 0,
        }
        and scan.get("ArtifactName") == EXPECTED_IMAGE_REFERENCE
        and scan.get("ArtifactType") == "container_image"
        and observed_scopes
        == [
            {"class": "os-pkgs", "type": "alpine", "package_count": 29},
            {"class": "lang-pkgs", "type": "jar", "package_count": 377},
        ]
        and not high_or_critical,
        "ADMIN_ADMISSION_SCAN",
        "resident Admin scan scope or zero High/Critical gate changed",
    )
    _expect(
        admission["supply_chain"]
        == {
            "path": (ADMISSION_EVIDENCE_PATH / "supply-chain.json").as_posix(),
            "sha256": EXPECTED_ADMISSION_SUPPLY_CHAIN_SHA256,
        }
        and admission["resident_ledger"]
        == {"path": RESIDENT_IMAGE_LEDGER.as_posix(), "enabled": True}
        and admission["runtime"]
        == {
            "permitted": False,
            "next_boundary": "admin_runtime_activation_pending",
        }
        and admission["gitops"] == {"resources_permitted": False}
        and admission["credentials"] == {"material_permitted": False},
        "ADMIN_ADMISSION_GATE",
        "resident ledger, runtime, Flux, or credential boundary changed",
    )
    ledger = _load_json(
        _require_regular_file(root, RESIDENT_IMAGE_LEDGER, "ADMIN_ADMISSION_LEDGER")
    )
    entries = [
        entry
        for entry in ledger.get("images", [])
        if isinstance(entry, Mapping) and entry.get("component") == "polaris-admin"
    ]
    _expect(
        entries
        == [
            {
                "component": "polaris-admin",
                "version": "1.6.0",
                "source": "https://github.com/apache/polaris",
                "platform": "linux/arm64",
                "reference": EXPECTED_IMAGE_REFERENCE,
                "sbom_artifact": (
                    "evidence/polaris-admin-v1.6.0/"
                    "polaris-admin-1.6.0-arm64.cdx.json"
                ),
                "scan_artifact": "evidence/polaris-admin-v1.6.0/trivy.json",
                "supply_chain_artifact": (
                    "evidence/polaris-admin-v1.6.0/supply-chain.json"
                ),
                "sbom_generator": "syft 1.46.0",
                "scanner_version": "trivy 0.72.0",
                "vulnerability_db_updated_at": "2026-07-21T01:08:43.916306317Z",
            }
        ],
        "ADMIN_ADMISSION_LEDGER",
        "resident ledger does not contain exactly one canonical Admin image",
    )
    gitops = root / "deploy/gitops"
    if os.path.lexists(gitops):
        _expect(
            not gitops.is_symlink() and gitops.is_dir(),
            "PREMATURE_GITOPS",
            "deploy/gitops must be a real directory while Admin runtime activation is pending",
        )
        for path in gitops.rglob("*"):
            relative = path.relative_to(root).as_posix()
            _expect(
                not path.is_symlink(),
                "PREMATURE_GITOPS",
                f"GitOps symlink is forbidden before Admin runtime activation: {relative}",
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


def _run_cosign(command: list[str]) -> None:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except FileNotFoundError:
        _fail("IMAGE_CRYPTO", "cosign is required for the full Admin image audit")
    except subprocess.TimeoutExpired:
        _fail("IMAGE_CRYPTO", f"cosign timed out: {command[1]}")
    _expect(
        result.returncode == 0,
        "IMAGE_CRYPTO",
        f"cosign {command[1]} failed: {(result.stderr or result.stdout).strip()}",
    )


def _cosign_identity_arguments() -> list[str]:
    return [
        "--certificate-identity",
        EXPECTED_WORKFLOW_IDENTITY,
        "--certificate-oidc-issuer",
        "https://token.actions.githubusercontent.com",
        "--certificate-github-workflow-repository",
        EXPECTED_REPOSITORY,
        "--certificate-github-workflow-ref",
        EXPECTED_REF,
        "--certificate-github-workflow-sha",
        EXPECTED_PUBLISHER_SOURCE_SHA,
        "--certificate-github-workflow-trigger",
        "push",
    ]


def _audit_admin_image_crypto(root: Path) -> None:
    """Reverify the retained image signature and SLSA bundle with Sigstore."""

    directory = root / EVIDENCE_PATH
    manifest = directory / "anonymous-image-manifest.json"
    signature_bundle = directory / "cosign-signature-bundle.json"
    _run_cosign(
        [
            "cosign",
            "verify-blob",
            "--bundle",
            str(signature_bundle),
            *_cosign_identity_arguments(),
            str(manifest),
        ]
    )

    try:
        slsa_records = json.loads(
            (directory / "slsa-verify.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        _fail("IMAGE_CRYPTO", f"cannot parse retained SLSA record: {error}")
    _expect(
        isinstance(slsa_records, list) and len(slsa_records) == 1,
        "IMAGE_CRYPTO",
        "exactly one retained SLSA verification record is required",
    )
    record = slsa_records[0]
    attestation = record.get("attestation") if isinstance(record, Mapping) else None
    bundle = attestation.get("bundle") if isinstance(attestation, Mapping) else None
    _expect(
        isinstance(bundle, Mapping),
        "IMAGE_CRYPTO",
        "retained SLSA verification record has no Sigstore bundle",
    )
    with tempfile.TemporaryDirectory(prefix="shirokuma-admin-slsa-") as temporary:
        bundle_path = Path(temporary) / "slsa.sigstore.json"
        bundle_path.write_text(
            json.dumps(bundle, separators=(",", ":"), sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _run_cosign(
            [
                "cosign",
                "verify-blob-attestation",
                "--bundle",
                str(bundle_path),
                "--type",
                "slsaprovenance1",
                *_cosign_identity_arguments(),
                str(manifest),
            ]
        )

    for filename, predicate_type in (
        ("sbom-attestation-bundle.json", "cyclonedx"),
        (
            "trivy-attestation-bundle.json",
            "https://shirokuma.dev/attestations/trivy/v1",
        ),
    ):
        _run_cosign(
            [
                "cosign",
                "verify-blob-attestation",
                "--bundle",
                str(directory / filename),
                "--type",
                predicate_type,
                *_cosign_identity_arguments(),
                str(manifest),
            ]
        )


def audit_publication_bootstrap(root: Path) -> None:
    """Validate reviewed image evidence without invoking external crypto."""

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
    _expect(
        not os.path.lexists(root / WORKFLOW_PATH),
        "RETIRED_PUBLISHER",
        "retired Admin image publisher was restored",
    )
    _audit_admin_dependency_static(root)
    _audit_containerfile(root)
    _audit_evidence_inventory(root)
    _audit_release_evidence(root)
    _audit_evidence_semantics(root)
    _audit_downstream_files(root)


def audit(
    root: Path,
    *,
    dependency_crypto_auditor: Callable[[Path], None] | None = None,
    image_crypto_auditor: Callable[[Path], None] | None = None,
) -> None:
    audit_publication_bootstrap(root)
    resolved = root.resolve()
    (dependency_crypto_auditor or _audit_admin_dependency_crypto)(resolved)
    (image_crypto_auditor or _audit_admin_image_crypto)(resolved)


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
            message = "static reviewed Admin image evidence verified"
        else:
            audit(args.root)
            message = "reviewed Admin image evidence and dependency trust verified"
    except ContractError as error:
        print(f"polaris-admin-image: {error}", file=sys.stderr)
        return 1
    print(
        f"polaris-admin-image: {message}; exact digest admitted while "
        "runtime/Flux/credentials remain disabled"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
