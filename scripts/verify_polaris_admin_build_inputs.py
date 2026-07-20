#!/usr/bin/env python3
"""Fail-closed static audit for the Polaris Admin build-input publisher."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping


CONTRACT_PATH = Path(
    "bootstrap/polaris/v1.6.0/admin-build-inputs-contract.json"
)
SOURCE_PATH = Path("bootstrap/polaris/v1.6.0/source.json")
PARENT_DESCRIPTOR_PATH = Path(
    "bootstrap/polaris/v1.6.0/evidence/gradle-dependency-inputs.json"
)
PARENT_VERIFICATION_PATH = Path(
    "bootstrap/polaris/v1.6.0/evidence/verification-metadata.xml"
)
WORKFLOW_PATH = Path(".github/workflows/polaris-admin-build-inputs.yml")
LEGACY_WORKFLOW_PATH = Path(".github/workflows/polaris-gradle-dependencies.yml")
PACKAGER_PATH = Path("scripts/package_polaris_gradle_dependencies.py")
SOURCE_VALIDATOR_PATH = Path("scripts/validate_polaris_source_archive.py")

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
OCI_REFERENCE_RE = re.compile(
    r"^ghcr\.io/tommykammy/"
    r"shirokuma-polaris-gradle-dependencies@sha256:[0-9a-f]{64}$"
)
REMOTE_ACTION_RE = re.compile(
    r"^\s*(?:-\s+)?uses:\s+([^\s@]+)@([0-9a-f]{40})(?:\s+#.*)?$"
)
USES_LINE_RE = re.compile(r"^\s*(?:-\s+)?uses:")
JOB_RE = re.compile(r"^  ([A-Za-z_][A-Za-z0-9_-]*):\s*$")
STEP_RE = re.compile(r"^      - name:\s+(.+?)\s*$")
STEP_ITEM_RE = re.compile(r"^      -(?:\s+.*)?$")
NESTED_STEP_NAME_RE = re.compile(r"^        name:\s+(.+?)\s*$")

EXPECTED_SOURCE_SHA256 = (
    "7d14b606dd756f501644190c10deb64a1e046d46faacd0f76f92501ccd5185bb"
)
EXPECTED_PARENT_DESCRIPTOR_SHA256 = (
    "3bab7b055d29be1bc59f2fe605960f49bbceee2639ad68086822c62ee8533841"
)
EXPECTED_PARENT_ARCHIVE_SHA256 = (
    "18933bfb895c267302f1ee1c80cfb9712eac736ffcefade48dac53f79e8e3bc0"
)
EXPECTED_PARENT_VERIFICATION_SHA256 = (
    "b8b1fa91bc9d98eaf676dbab76c5452411fcdf6b11a8c9959c131799c71deaf2"
)
EXPECTED_PACKAGER_SHA256 = (
    "fbbe803c7d1e52be02ba81f26f6f35fb0d6824fbe59cf3ab579e87c5488723ab"
)
EXPECTED_SOURCE_VALIDATOR_SHA256 = (
    "00ac3ec84bd9ff48914e0429f517eabbfc9380410740c2e626608bc036f8ebb9"
)
EXPECTED_ADMIN_BUILD_SHA256 = (
    "6e3aabc2090cda72c03608053f41899792a6c62bec382ed18d6b02703574fde9"
)
EXPECTED_OFFLINE_STEP_SHA256 = (
    "e39f094499144d0619452efa6b3ea722842e3ca5ae07af406c53090048481916"
)
EXPECTED_TRUST_GATE_STEP_SHA256 = (
    "712ee8e6e9bd628babd01c01792543b42187b815376b9031bbfaec3ed8e5b086"
)
EXPECTED_BUILDER_PLATFORM = "linux/arm64"
EXPECTED_GRADLE_VERSION = "9.6.0"
EXPECTED_JAVA_MAJOR = 21
EXPECTED_PARENT_REFERENCE = (
    "ghcr.io/tommykammy/shirokuma-polaris-gradle-dependencies@"
    "sha256:fa889d2c0a6e6dc48816d79680a366e21040be333ab6007b88e4ca4dbf6e59d6"
)
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
EXPECTED_NOSQL_SOURCES = [
    (
        "runtime/admin/src/main/java/org/apache/polaris/admintool/nosql/"
        "BaseNoSqlCommand.java",
        "8ed348be717debb68aa62ddeaee28f65c6c0c22c",
    ),
    (
        "runtime/admin/src/main/java/org/apache/polaris/admintool/nosql/"
        "NoSqlCommand.java",
        "639cbbfedabaf8963f7a699d9496e1bd0ab3dcb5",
    ),
    (
        "runtime/admin/src/main/java/org/apache/polaris/admintool/nosql/"
        "maintenance/BaseNoSqlMaintenanceCommand.java",
        "39ab3bfc95f44dd5bd6b81ee54ce1fff290f34f3",
    ),
    (
        "runtime/admin/src/main/java/org/apache/polaris/admintool/nosql/"
        "maintenance/NoSqlMaintenanceInfoCommand.java",
        "03d6c92aeb9de83c16b29ca6850a058b94c9a19a",
    ),
    (
        "runtime/admin/src/main/java/org/apache/polaris/admintool/nosql/"
        "maintenance/NoSqlMaintenanceRunCommand.java",
        "6e415a1adf8f09434f9ef3a16d3d2d9f47cea032",
    ),
]


class ContractError(RuntimeError):
    """Stable, safe-to-print contract failure."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _fail(code: str, detail: str) -> None:
    raise ContractError(code, detail)


def _expect(condition: bool, code: str, detail: str) -> None:
    if not condition:
        _fail(code, detail)


def _expect_keys(
    value: Mapping[str, Any],
    expected: set[str],
    code: str,
    location: str,
) -> None:
    actual = set(value)
    _expect(
        actual == expected,
        code,
        f"{location} keys differ: expected={sorted(expected)!r} "
        f"actual={sorted(actual)!r}",
    )


def _load_json(path: Path, *, maximum_bytes: int = 8 * 1024 * 1024) -> Any:
    try:
        size = path.stat().st_size
    except OSError as error:
        _fail("FILE_READ", f"cannot stat {path}: {error}")
    _expect(
        size <= maximum_bytes,
        "FILE_SIZE",
        f"{path} exceeds {maximum_bytes} bytes",
    )
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
        )
    except (OSError, UnicodeError, ValueError) as error:
        _fail("JSON_INVALID", f"cannot load {path}: {error}")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, nested in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = nested
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        _fail("FILE_READ", f"cannot hash {path}: {error}")
    return digest.hexdigest()


def _require_hash(
    root: Path,
    relative: Path,
    expected: str,
    code: str,
) -> None:
    _expect(
        SHA256_RE.fullmatch(expected) is not None,
        code,
        f"invalid expected SHA-256 for {relative}",
    )
    actual = _sha256(root / relative)
    _expect(
        actual == expected,
        code,
        f"{relative} sha256={actual}, expected={expected}",
    )


def _workflow_jobs_and_steps(
    workflow: str,
) -> tuple[
    list[str],
    dict[str, list[str]],
    dict[str, tuple[int, int]],
    dict[str, dict[str, tuple[int, int]]],
]:
    lines = workflow.splitlines()
    try:
        jobs_start = lines.index("jobs:")
    except ValueError:
        _fail("WORKFLOW_JOBS", "jobs block is absent")
    jobs: list[str] = []
    starts: dict[str, int] = {}
    step_item_starts: dict[str, list[int]] = {}
    current: str | None = None
    for index in range(jobs_start + 1, len(lines)):
        job_match = JOB_RE.fullmatch(lines[index])
        if job_match is not None:
            current = job_match.group(1)
            jobs.append(current)
            starts[current] = index
            step_item_starts[current] = []
            continue
        if STEP_ITEM_RE.fullmatch(lines[index]) is not None:
            _expect(
                current is not None,
                "WORKFLOW_STEP_ITEM",
                f"step item appears outside a job at line {index + 1}",
            )
            step_item_starts[current].append(index)
    spans: dict[str, tuple[int, int]] = {}
    for index, job in enumerate(jobs):
        end = starts[jobs[index + 1]] if index + 1 < len(jobs) else len(lines)
        spans[job] = (starts[job], end)
    steps: dict[str, list[str]] = {}
    step_spans: dict[str, dict[str, tuple[int, int]]] = {}
    for job in jobs:
        item_starts = step_item_starts[job]
        job_steps: list[str] = []
        job_step_spans: dict[str, tuple[int, int]] = {}
        for index, start in enumerate(item_starts):
            end = (
                item_starts[index + 1]
                if index + 1 < len(item_starts)
                else spans[job][1]
            )
            names: list[str] = []
            direct_name = STEP_RE.fullmatch(lines[start])
            if direct_name is not None:
                names.append(direct_name.group(1))
            names.extend(
                match.group(1)
                for line in lines[start + 1 : end]
                if (match := NESTED_STEP_NAME_RE.fullmatch(line)) is not None
            )
            _expect(
                len(names) == 1,
                "WORKFLOW_STEP_NAME",
                f"step item at line {start + 1} must have exactly one name",
            )
            name = names[0]
            _expect(
                name not in job_step_spans,
                "WORKFLOW_STEP_NAME",
                f"duplicate step name in {job}: {name}",
            )
            job_steps.append(name)
            job_step_spans[name] = (start, end)
        steps[job] = job_steps
        step_spans[job] = job_step_spans
    return jobs, steps, spans, step_spans


def _validate_contract(root: Path, contract: Mapping[str, Any]) -> None:
    _expect_keys(
        contract,
        {
            "schema_version",
            "component",
            "version",
            "platform",
            "lifecycle",
            "source",
            "parent_snapshot",
            "admin_dependency_surface",
            "candidate_snapshot",
            "publication_policy",
            "downstream_gates",
        },
        "CONTRACT_SCHEMA",
        "root",
    )
    _expect(
        contract["schema_version"] == 1
        and contract["component"] == "polaris-admin-build-inputs"
        and contract["version"] == "1.6.0"
        and contract["platform"] == "linux/arm64",
        "CONTRACT_IDENTITY",
        "component, version, or platform changed",
    )

    lifecycle = contract["lifecycle"]
    _expect_keys(
        lifecycle,
        {"state", "next_state", "retire_in_evidence_review_pr"},
        "LIFECYCLE_SCHEMA",
        "lifecycle",
    )
    _expect(
        lifecycle
        == {
            "state": "admin_dependency_snapshot_publication_pending",
            "next_state": "admin_dependency_snapshot_review_pending",
            "retire_in_evidence_review_pr": True,
        },
        "LIFECYCLE_STATE",
        repr(lifecycle),
    )

    source = contract["source"]
    _expect_keys(
        source,
        {
            "record",
            "record_sha256",
            "archive_sha512",
            "signature_sha256",
            "signing_key_fingerprint",
            "git_commit",
            "git_tree",
            "builder_index",
            "builder_arm64_manifest",
            "java_major",
            "gradle_version",
            "tasks",
            "admin_build_preimage",
        },
        "SOURCE_SCHEMA",
        "source",
    )
    _expect(
        source["record"] == SOURCE_PATH.as_posix()
        and source["record_sha256"] == EXPECTED_SOURCE_SHA256
        and source["archive_sha512"]
        == (
            "d69b1a91e16e210a78dec327fc4725983b114fbec5d86d078a3827f35fe7dd"
            "5df3e4b12d18965e5a72eace65ad224aa007004ed61c66f9abb2efafc44ceac95b"
        )
        and source["signature_sha256"]
        == "2338e1c2385874e9bf5cf513b4d27732b1cd59e943e1662e62fa995d915e6481"
        and source["signing_key_fingerprint"]
        == "F2EEEB06110BEE1397EC74CBB8960FF52D9B1312"
        and source["git_commit"]
        == "dd306009d81a0e15adafe9dcd7d1c6d04d326f34"
        and source["git_tree"] == "1ad42f42aaebfa767b66a37f522a6c8d6693d841"
        and source["builder_index"]
        == (
            "docker.io/library/gradle@"
            "sha256:ecbf526b4d3c247b4cc61e9850eae2addd5f73a7c849bf026000442808f54b56"
        )
        and source["builder_arm64_manifest"]
        == (
            "docker.io/library/gradle@"
            "sha256:cc583fa5245267fe0e1546c9989e8575473a37336ad9894dc0684a99fea1fb03"
        )
        and source["java_major"] == EXPECTED_JAVA_MAJOR
        and source["gradle_version"] == EXPECTED_GRADLE_VERSION
        and source["tasks"] == EXPECTED_TASKS,
        "SOURCE_CONTRACT",
        "source pins or task closure changed",
    )
    preimage = source["admin_build_preimage"]
    _expect(
        preimage
        == {
            "path": "runtime/admin/build.gradle.kts",
            "git_blob": "94bf1dfd2b1039f1ca23d5dd7437429c11db66dd",
            "sha256": EXPECTED_ADMIN_BUILD_SHA256,
            "size": 4149,
        },
        "ADMIN_BUILD_PREIMAGE",
        repr(preimage),
    )

    parent = contract["parent_snapshot"]
    _expect_keys(
        parent,
        {
            "use",
            "review_checkpoint",
            "artifact_reference",
            "descriptor",
            "cache_layer",
            "verification_metadata",
            "required_relationship",
        },
        "PARENT_SCHEMA",
        "parent_snapshot",
    )
    _expect(
        parent["use"] == "reviewed_seed_only"
        and parent["review_checkpoint"]
        == {
            "merge_commit": "b12593f27ae4e6ec8b64865f9b6b0bbf114ec654"
        }
        and parent["artifact_reference"] == EXPECTED_PARENT_REFERENCE
        and OCI_REFERENCE_RE.fullmatch(parent["artifact_reference"]) is not None
        and parent["descriptor"]
        == {
            "path": PARENT_DESCRIPTOR_PATH.as_posix(),
            "sha256": EXPECTED_PARENT_DESCRIPTOR_SHA256,
        }
        and parent["cache_layer"]
        == {
            "filename": "polaris-gradle-dependencies-1.6.0.tar.gz",
            "sha256": EXPECTED_PARENT_ARCHIVE_SHA256,
        }
        and parent["verification_metadata"]
        == {
            "path": PARENT_VERIFICATION_PATH.as_posix(),
            "sha256": EXPECTED_PARENT_VERIFICATION_SHA256,
        }
        and parent["required_relationship"]
        == "exact-module-artifact-superset",
        "PARENT_SNAPSHOT",
        "reviewed parent identity or relationship changed",
    )

    surface = contract["admin_dependency_surface"]
    _expect_keys(
        surface,
        {
            "review_state",
            "relational_only",
            "reason",
            "unconditional_project_dependencies",
            "unconditional_external_dependencies",
            "main_source_records",
        },
        "ADMIN_SURFACE_SCHEMA",
        "admin_dependency_surface",
    )
    _expect(
        surface["review_state"] == "review_required"
        and surface["relational_only"] is False
        and "unconditionally" in surface["reason"]
        and surface["unconditional_project_dependencies"] == EXPECTED_NOSQL_PROJECTS
        and surface["unconditional_external_dependencies"]
        == ["io.quarkus:quarkus-mongodb-client"]
        and [
            (record.get("path"), record.get("git_blob"))
            for record in surface["main_source_records"]
        ]
        == EXPECTED_NOSQL_SOURCES
        and all(
            set(record) == {"path", "git_blob"}
            for record in surface["main_source_records"]
        )
        and all(
            SHA1_RE.fullmatch(record["git_blob"]) is not None
            for record in surface["main_source_records"]
        ),
        "ADMIN_SURFACE",
        "unconditional upstream NoSQL/Mongo surface is not review-required",
    )

    candidate = contract["candidate_snapshot"]
    _expect_keys(
        candidate,
        {
            "state",
            "artifact_repository",
            "artifact_reference",
            "artifact_type",
            "descriptor_media_type",
            "archive_media_type",
            "archive_filename",
            "descriptor_filename",
            "verification_metadata_filename",
            "packager",
            "source_archive_validator",
            "superset_proof",
            "offline_proof",
        },
        "CANDIDATE_SCHEMA",
        "candidate_snapshot",
    )
    _expect(
        candidate["state"] == "publication_pending"
        and candidate["artifact_repository"]
        == "ghcr.io/tommykammy/shirokuma-polaris-admin-gradle-dependencies"
        and candidate["artifact_reference"] is None
        and candidate["artifact_type"]
        == "application/vnd.shirokuma.polaris-admin.gradle-dependencies.v1"
        and candidate["descriptor_media_type"]
        == "application/vnd.shirokuma.gradle-dependency-descriptor.v1+json"
        and candidate["archive_media_type"]
        == "application/vnd.shirokuma.gradle-cache.v1.tar+gzip"
        and candidate["archive_filename"]
        == "polaris-gradle-dependencies-1.6.0.tar.gz"
        and candidate["descriptor_filename"] == "gradle-dependency-inputs.json"
        and candidate["verification_metadata_filename"]
        == "verification-metadata.xml"
        and candidate["packager"]
        == {
            "path": PACKAGER_PATH.as_posix(),
            "sha256": EXPECTED_PACKAGER_SHA256,
        }
        and candidate["source_archive_validator"]
        == {
            "path": SOURCE_VALIDATOR_PATH.as_posix(),
            "sha256": EXPECTED_SOURCE_VALIDATOR_SHA256,
        },
        "CANDIDATE_SNAPSHOT",
        "candidate identity or trusted tool pin changed",
    )
    superset = candidate["superset_proof"]
    _expect(
        superset
        == {
            "scope": "parent-module-artifact-records",
            "match_fields": [
                "path",
                "sha256",
                "size",
                "kind",
                "group",
                "module",
                "version",
                "artifact",
            ],
            "result": "required",
        },
        "SUPERSET_POLICY",
        repr(superset),
    )
    offline = candidate["offline_proof"]
    _expect(
        offline
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
        repr(offline),
    )

    gates = contract["downstream_gates"]
    _expect_keys(
        gates,
        {
            "admin_image_publication_enabled",
            "admin_image_admitted",
            "admin_runtime_enabled",
            "gitops_resources_enabled",
            "credential_material_permitted",
            "next_checkpoint",
        },
        "DOWNSTREAM_GATE_SCHEMA",
        "downstream_gates",
    )
    _expect(
        all(
            gates[key] is False
            for key in (
                "admin_image_publication_enabled",
                "admin_image_admitted",
                "admin_runtime_enabled",
                "gitops_resources_enabled",
                "credential_material_permitted",
            )
        )
        and "review" in gates["next_checkpoint"]
        and "NoSQL/Mongo" in gates["next_checkpoint"],
        "DOWNSTREAM_GATE",
        "admin image, admission, runtime, GitOps, and credentials must stay disabled",
    )

    publication = contract["publication_policy"]
    _expect_keys(
        publication,
        {
            "workflow",
            "candidate_attempt_policy",
            "visibility_bootstrap",
            "oras",
            "cosign",
            "legacy_workflow",
        },
        "PUBLICATION_SCHEMA",
        "publication_policy",
    )
    attempt_policy = publication["candidate_attempt_policy"]
    _expect(
        attempt_policy
        == {
            "tag_template": "1.6.0-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}",
            "immutable_run_id_run_attempt_tag": True,
            "retries_permitted_only_before_retirement": True,
            "failed_attempt_admitted": False,
        },
        "CANDIDATE_ATTEMPT_POLICY",
        repr(attempt_policy),
    )
    visibility = publication["visibility_bootstrap"]
    _expect(
        visibility
        == {
            "required_visibility": "public",
            "sign_and_attest_before_anonymous_pull": True,
            "owner_action_on_first_private_run": (
                "set-package-public-and-rerun"
            ),
            "failed_attempt_admitted": False,
            "authenticated_fallback": False,
        },
        "VISIBILITY_BOOTSTRAP",
        repr(visibility),
    )
    _expect(
        publication["oras"]
        == {
            "version": "1.3.3",
            "linux_arm64_url": (
                "https://github.com/oras-project/oras/releases/download/"
                "v1.3.3/oras_1.3.3_linux_arm64.tar.gz"
            ),
            "linux_arm64_sha256": (
                "ac7156f93a21e903f7ad606c792f3560f17e0cd0e36365634701b1e7cc4e4eca"
            ),
        }
        and publication["cosign"] == {"version": "v3.1.1"}
        and publication["legacy_workflow"]
        == {
            "path": LEGACY_WORKFLOW_PATH.as_posix(),
            "must_be_absent": True,
        },
        "PUBLICATION_TOOLCHAIN",
        "publisher toolchain or legacy-workflow guard changed",
    )

    _require_hash(root, SOURCE_PATH, EXPECTED_SOURCE_SHA256, "SOURCE_HASH")
    _require_hash(
        root,
        PARENT_DESCRIPTOR_PATH,
        EXPECTED_PARENT_DESCRIPTOR_SHA256,
        "PARENT_DESCRIPTOR_HASH",
    )
    _require_hash(
        root,
        PARENT_VERIFICATION_PATH,
        EXPECTED_PARENT_VERIFICATION_SHA256,
        "PARENT_VERIFICATION_HASH",
    )
    _require_hash(
        root,
        PACKAGER_PATH,
        EXPECTED_PACKAGER_SHA256,
        "PACKAGER_HASH",
    )
    _require_hash(
        root,
        SOURCE_VALIDATOR_PATH,
        EXPECTED_SOURCE_VALIDATOR_SHA256,
        "SOURCE_VALIDATOR_HASH",
    )
    parent_descriptor = _load_json(root / PARENT_DESCRIPTOR_PATH)
    _expect(
        parent_descriptor.get("archive", {}).get("sha256")
        == EXPECTED_PARENT_ARCHIVE_SHA256
        and parent_descriptor.get("verification_metadata", {}).get("sha256")
        == EXPECTED_PARENT_VERIFICATION_SHA256
        and parent_descriptor.get("platform") == "linux/arm64",
        "PARENT_DESCRIPTOR",
        "committed parent descriptor does not bind the reviewed cache layer",
    )
    _expect(
        not (root / LEGACY_WORKFLOW_PATH).exists(),
        "LEGACY_WORKFLOW_PRESENT",
        LEGACY_WORKFLOW_PATH.as_posix(),
    )


def _validate_workflow(contract: Mapping[str, Any], workflow: str) -> None:
    workflow_record = contract["publication_policy"]["workflow"]
    _expect_keys(
        workflow_record,
        {
            "path",
            "runner",
            "allowed_events",
            "publication_event",
            "publication_ref",
            "workflow_sha_equals_source_sha",
            "privileged_job",
            "first_privileged_step",
            "allowed_jobs",
            "allowed_steps",
            "allowed_actions",
            "action_counts",
        },
        "WORKFLOW_SCHEMA",
        "publication_policy.workflow",
    )
    _expect(
        workflow_record["path"] == WORKFLOW_PATH.as_posix()
        and workflow_record["runner"] == "ubuntu-24.04-arm"
        and workflow_record["allowed_events"] == ["pull_request", "push"]
        and workflow_record["publication_event"] == "push"
        and workflow_record["publication_ref"] == "refs/heads/main"
        and workflow_record["workflow_sha_equals_source_sha"] is True
        and workflow_record["privileged_job"] == "publish"
        and workflow_record["first_privileged_step"]
        == "Enforce the main-source trust boundary"
        and workflow_record["allowed_jobs"] == ["validate", "publish"],
        "WORKFLOW_POLICY",
        "workflow identity, event, runner, or privileged boundary changed",
    )

    _expect(
        "pull_request_target:" not in workflow
        and "workflow_dispatch:" not in workflow
        and workflow.count("  pull_request:") == 1
        and workflow.count("  push:") == 1
        and workflow.count("      - main") == 1,
        "WORKFLOW_TRIGGER",
        "only pull_request validation and main push publication are allowed",
    )
    workflow_lines = workflow.splitlines()
    for dependency_path in (
        PARENT_DESCRIPTOR_PATH,
        PARENT_VERIFICATION_PATH,
    ):
        _expect(
            workflow_lines.count(
                f"      - {dependency_path.as_posix()}"
            )
            == 2,
            "WORKFLOW_PATH_FILTER",
            (
                f"{dependency_path.as_posix()} must trigger both "
                "pull-request validation and main publication"
            ),
        )
    _expect(
        workflow_lines.count("permissions:") == 1
        and workflow_lines.count("    permissions:") == 2
        and workflow_lines.count("      contents: read") == 2
        and workflow_lines.count("  contents: read") == 1
        and workflow_lines.count("      packages: write") == 1
        and workflow_lines.count("      id-token: write") == 1
        and workflow_lines.count("      attestations: write") == 1
        and "actions: write" not in workflow
        and "contents: write" not in workflow,
        "WORKFLOW_PERMISSIONS",
        "read-only validation and minimal publication permissions are required",
    )
    _expect(
        "${{ secrets." not in workflow
        and "pull_request_target" not in workflow
        and "@main" not in workflow
        and "@latest" not in workflow,
        "WORKFLOW_UNTRUSTED_INPUT",
        "workflow contains a secret or floating/untrusted reference",
    )

    jobs, steps, spans, step_spans = _workflow_jobs_and_steps(workflow)
    _expect(
        jobs == workflow_record["allowed_jobs"],
        "WORKFLOW_JOB_CLOSED_WORLD",
        repr(jobs),
    )
    _expect(
        steps == workflow_record["allowed_steps"],
        "WORKFLOW_STEP_CLOSED_WORLD",
        repr(steps),
    )
    lines = workflow.splitlines()
    static_audit_name = "Validate the publication-pending contract"
    lifecycle_name = "Check the admin dependency publication lifecycle"
    _expect(
        steps["validate"][:3]
        == [
            "Check out the reviewed admin dependency policy",
            static_audit_name,
            lifecycle_name,
        ],
        "WORKFLOW_STATIC_AUDIT_ORDER",
        repr(steps["validate"][:3]),
    )
    static_start, static_end = step_spans["validate"][static_audit_name]
    static_lines = lines[static_start:static_end]
    _expect(
        not any(line.startswith("        if:") for line in static_lines),
        "WORKFLOW_STATIC_AUDIT_ORDER",
        "static audit and tests must run before the lifecycle gate",
    )
    for heavy_step_name in steps["validate"][3:]:
        heavy_start, heavy_end = step_spans["validate"][heavy_step_name]
        heavy_lines = lines[heavy_start:heavy_end]
        _expect(
            heavy_lines.count(
                "        if: steps.lifecycle.outputs.active == 'true'"
            )
            == 1,
            "WORKFLOW_HEAVY_STEP_GATE",
            heavy_step_name,
        )

    publish_start, publish_end = spans["publish"]
    first_step_name = steps["publish"][0] if steps["publish"] else None
    _expect(
        first_step_name == workflow_record["first_privileged_step"],
        "PUBLISH_TRUST_GATE_ORDER",
        "privileged job must start with the inline trust gate",
    )
    first_step_start, first_step_end = step_spans["publish"][first_step_name]
    _expect(
        first_step_start >= publish_start
        and first_step_end <= publish_end
        and not any(USES_LINE_RE.match(value) for value in lines[publish_start:first_step_start]),
        "PUBLISH_TRUST_GATE_ORDER",
        "no action may execute before the privileged inline trust gate",
    )
    trust_gate = "\n".join(lines[first_step_start:first_step_end])
    _expect(
        'test "${GITHUB_REPOSITORY}" = "TommyKammy/Shirokuma"' in trust_gate
        and 'test "${GITHUB_EVENT_NAME}" = "push"' in trust_gate
        and 'test "${GITHUB_REF}" = "refs/heads/main"' in trust_gate
        and 'test "${GITHUB_SHA}" = "${GITHUB_WORKFLOW_SHA}"' in trust_gate
        and "case \"${GITHUB_SHA}\" in" in trust_gate,
        "WORKFLOW_TRUST_GATE",
        "publisher does not bind repository, event, ref, source SHA, and workflow SHA",
    )
    _expect(
        hashlib.sha256((trust_gate + "\n").encode("utf-8")).hexdigest()
        == EXPECTED_TRUST_GATE_STEP_SHA256,
        "WORKFLOW_TRUST_GATE_STEP",
        "privileged trust-gate step differs from the reviewed exact command body",
    )
    publish_header = "\n".join(lines[publish_start:first_step_start])
    _expect(
        "github.repository == 'TommyKammy/Shirokuma'" in publish_header
        and "github.event_name == 'push'" in publish_header
        and "github.ref == 'refs/heads/main'" in publish_header
        and "github.sha == github.workflow_sha" in publish_header
        and "needs.validate.outputs.active == 'true'" in publish_header,
        "WORKFLOW_PUBLISH_CONDITION",
        "write-capable job is not main-only and source/workflow-SHA-bound",
    )
    digest_validation = (
        'if ! [[ "${digest}" =~ ^sha256:[0-9a-f]{64}$ ]]; then'
    )
    digest_export = 'echo "PUBLISHED_TAG=${tag}" >> "${GITHUB_ENV}"'
    _expect(
        workflow.count(digest_validation) == 1
        and workflow.count(digest_export) == 1
        and "sha256:????????" not in workflow
        and workflow.index(digest_validation) < workflow.index(digest_export),
        "WORKFLOW_DIGEST_VALIDATION",
        "ORAS digest must be strictly validated before workflow-file export",
    )

    offline_step_name = (
        "Prove a fresh network-none offline admin and server build"
    )
    offline_start, offline_end = step_spans["validate"][offline_step_name]
    offline_step = "\n".join(lines[offline_start:offline_end]) + "\n"
    _expect(
        hashlib.sha256(offline_step.encode("utf-8")).hexdigest()
        == EXPECTED_OFFLINE_STEP_SHA256,
        "WORKFLOW_OFFLINE_STEP",
        "offline proof step differs from the reviewed exact command body",
    )

    toolchain_step_name = "Verify the exact builder toolchain"
    toolchain_start, toolchain_end = step_spans["validate"][
        toolchain_step_name
    ]
    toolchain_lines = {
        line.strip() for line in lines[toolchain_start:toolchain_end]
    }
    required_toolchain_lines = {
        'docker pull --quiet --platform linux/arm64 "${BUILDER_IMAGE}" \\',
        "--format '{{.Os}}/{{.Architecture}}' \\",
        "builder_gradle_output=$(",
        'awk \'/^Gradle / {print $2}\' <<< "${builder_gradle_output}"',
        "builder_java_output=$(",
        '<<< "${builder_java_output}"',
        'test "${builder_platform}" = "${EXPECTED_BUILDER_PLATFORM}"',
        'test "${builder_gradle}" = "${EXPECTED_GRADLE_VERSION}"',
        'test "${builder_java_major}" = "${EXPECTED_JAVA_MAJOR}"',
        'echo "OBSERVED_BUILDER_PLATFORM=${builder_platform}"',
        'echo "OBSERVED_BUILDER_GRADLE=${builder_gradle}"',
        'echo "OBSERVED_BUILDER_JAVA=${builder_java}"',
        'echo "OBSERVED_BUILDER_JAVA_MAJOR=${builder_java_major}"',
    }
    resolver_start, _ = step_spans["validate"][
        "Resolve the admin superset with strict verification metadata"
    ]
    _expect(
        required_toolchain_lines <= toolchain_lines
        and toolchain_end <= resolver_start
        and workflow.count(
            "  EXPECTED_BUILDER_PLATFORM: linux/arm64"
        )
        == 1
        and workflow.count("  EXPECTED_GRADLE_VERSION: 9.6.0") == 1
        and workflow.count('  EXPECTED_JAVA_MAJOR: "21"') == 1
        and 'gradle --version \\\n              | awk' not in workflow
        and 'java -version 2>&1 \\\n              | awk' not in workflow,
        "WORKFLOW_BUILDER_TOOLCHAIN",
        "exact builder platform, Gradle, and Java must be gated before resolution",
    )
    evidence_start, evidence_end = step_spans["validate"][
        "Record the dependency resolver evidence"
    ]
    evidence_block = "\n".join(lines[evidence_start:evidence_end])
    _expect(
        'os.environ["OBSERVED_BUILDER_PLATFORM"]' in evidence_block
        and 'os.environ["OBSERVED_BUILDER_GRADLE"]' in evidence_block
        and 'os.environ["OBSERVED_BUILDER_JAVA"]' in evidence_block
        and 'os.environ["OBSERVED_BUILDER_JAVA_MAJOR"]' in evidence_block
        and "docker run" not in evidence_block,
        "WORKFLOW_BUILDER_EVIDENCE",
        "toolchain evidence must preserve the pre-resolution observations",
    )

    upload_start, upload_end = step_spans["validate"][
        "Retain the read-only-verified candidate"
    ]
    upload_block = "\n".join(lines[upload_start:upload_end])
    download_start, download_end = step_spans["publish"][
        "Download the exact read-only-verified candidate"
    ]
    download_block = "\n".join(lines[download_start:download_end])
    _expect(
        workflow.count(
            "      candidate_artifact_name: "
            "${{ steps.candidate.outputs.candidate_artifact_name }}"
        )
        == 1
        and '"candidate_artifact_name": (' in evidence_block
        and "os.environ['GITHUB_RUN_ID']" in evidence_block
        and "os.environ['GITHUB_RUN_ATTEMPT']" in evidence_block
        and (
            "name: ${{ steps.candidate.outputs."
            "candidate_artifact_name }}"
        )
        in upload_block
        and (
            "name: ${{ needs.validate.outputs."
            "candidate_artifact_name }}"
        )
        in download_block,
        "WORKFLOW_CANDIDATE_ARTIFACT_BINDING",
        "publish must download the successful validate attempt artifact",
    )

    push_start, push_end = step_spans["publish"][
        "Publish the run-scoped immutable OCI artifact"
    ]
    push_block = "\n".join(lines[push_start:push_end])
    _expect(
        (
            'tag="${ARTIFACT_REPOSITORY}:1.6.0-'
            '${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"'
        )
        in push_block
        and "run-scoped OCI tag already exists; refusing overwrite"
        in push_block,
        "WORKFLOW_CANDIDATE_ATTEMPT",
        "candidate tags must be immutable and scoped to run ID and attempt",
    )

    anonymous_start, anonymous_end = step_spans["publish"][
        "Prove anonymous exact-digest retrieval"
    ]
    anonymous_block = "\n".join(lines[anonymous_start:anonymous_end])
    sign_start, _ = step_spans["publish"][
        "Keyless-sign the exact OCI manifest"
    ]
    attest_start, _ = step_spans["publish"][
        "Publish SLSA provenance for the exact OCI manifest"
    ]
    publication_step_name = "Record the review-required publication"
    publication_start, publication_end = step_spans["publish"][
        publication_step_name
    ]
    _expect(
        sign_start < anonymous_start
        and attest_start < anonymous_start
        and anonymous_end <= publication_start
        and "oras logout ghcr.io" in anonymous_block
        and "printf '{}\\n' > \"${anonymous_config}\"" in anonymous_block
        and '--registry-config "${anonymous_config}"' in anonymous_block
        and "oras login" not in anonymous_block
        and "GHCR_TOKEN" not in anonymous_block
        and "github.token" not in anonymous_block,
        "WORKFLOW_VISIBILITY_BOOTSTRAP",
        "anonymous public retrieval must fail closed without credential fallback",
    )
    publication_lines = lines[publication_start:publication_end]
    credential_gate = '              "credential_material_permitted": False,'
    _expect(
        publication_lines.count(credential_gate) == 1
        and sum(
            "credential_material_permitted" in line
            for line in publication_lines
        )
        == 1,
        "WORKFLOW_CREDENTIAL_GATE",
        "publication evidence must carry the disabled credential gate exactly once",
    )

    uses_lines = [
        line for line in workflow.splitlines() if USES_LINE_RE.match(line)
    ]
    actual_actions: list[str] = []
    for line in uses_lines:
        match = REMOTE_ACTION_RE.fullmatch(line)
        _expect(match is not None, "ACTION_NOT_SHA_PINNED", line.strip())
        actual_actions.append(f"{match.group(1)}@{match.group(2)}")
    _expect(
        actual_actions == workflow_record["allowed_actions"],
        "WORKFLOW_ACTION_CLOSED_WORLD",
        repr(actual_actions),
    )
    action_counts = Counter(action.split("@", 1)[0] for action in actual_actions)
    _expect(
        dict(action_counts) == workflow_record["action_counts"],
        "WORKFLOW_ACTION_COUNT",
        repr(dict(action_counts)),
    )

    required_literals = [
        EXPECTED_PARENT_REFERENCE,
        EXPECTED_PARENT_DESCRIPTOR_SHA256,
        EXPECTED_PARENT_ARCHIVE_SHA256,
        EXPECTED_PARENT_VERIFICATION_SHA256,
        EXPECTED_ADMIN_BUILD_SHA256,
        "admin_dependency_snapshot_publication_pending",
        "admin_dependency_snapshot_review_pending",
        "exact-module-artifact-superset",
        "--write-verification-metadata sha256",
        "--network none",
        "--offline",
        "--dependency-verification strict",
        "--no-build-cache",
        "--no-configuration-cache",
        "GITHUB_WORKFLOW_SHA",
        "github.sha == github.workflow_sha",
        "needs.validate.outputs.archive_sha256",
        "needs.validate.outputs.descriptor_sha256",
        "needs.validate.outputs.metadata_sha256",
        "needs.validate.outputs.offline_sha256",
        "needs.validate.outputs.superset_sha256",
        "needs.validate.outputs.toolchain_sha256",
        "downloaded candidate bytes differ from read-only job outputs",
        "ghcr.io/tommykammy/shirokuma-polaris-admin-gradle-dependencies",
        "application/vnd.shirokuma.polaris-admin.gradle-dependencies.v1",
        "review_required",
        '"relational_only": False',
        '"admin_image_publication_enabled": False',
        '"admin_image_admitted": False',
        '"admin_runtime_enabled": False',
        '"gitops_resources_enabled": False',
        '"credential_material_permitted": False',
    ]
    required_literals.extend(EXPECTED_TASKS)
    required_literals.extend(EXPECTED_NOSQL_PROJECTS)
    for literal in required_literals:
        _expect(
            literal in workflow,
            "WORKFLOW_REQUIRED_LITERAL",
            literal,
        )
    for task in EXPECTED_TASKS:
        _expect(
            workflow.count(task) >= 4,
            "WORKFLOW_TASK_CLOSURE",
            f"{task} occurs only {workflow.count(task)} times",
        )
    _expect(
        workflow.count("Install checksum-pinned ORAS") == 2
        and workflow.count("Install pinned Cosign") == 1
        and workflow.count("sigstore/cosign-installer@") == 1,
        "WORKFLOW_INSTALLER_COUNT",
        "installer steps/actions must have an exact non-duplicated count",
    )
    _expect(
        workflow.count('"${ORAS_URL}" --output "${archive}"') == 2
        and workflow.count(
            'tar --extract --gzip --file "${archive}" '
        )
        == 2
        and workflow.count(
            'test "$("${install_dir}/oras" version '
        )
        == 2,
        "WORKFLOW_INSTALLER_COUNT",
        "ORAS installer commands must occur exactly once per isolated job",
    )
    cosign_start_marker = (
        "      - name: Keyless-sign the exact OCI manifest"
    )
    cosign_end_marker = (
        "      - name: Publish SLSA provenance for the exact OCI manifest"
    )
    _expect(
        workflow.count(cosign_start_marker) == 1
        and workflow.count(cosign_end_marker) == 1,
        "WORKFLOW_COSIGN_BINDING",
        "Cosign signing and provenance step boundaries changed",
    )
    cosign_block = workflow[
        workflow.index(cosign_start_marker) : workflow.index(cosign_end_marker)
    ]
    cosign_lines = [line.strip() for line in cosign_block.splitlines()]
    _expect(
        sum(line.startswith("cosign sign ") for line in cosign_lines) == 1
        and sum(
            line.startswith("cosign verify-blob ")
            for line in cosign_lines
        )
        == 1
        and sum(
            line.startswith("cosign verify ")
            for line in cosign_lines
        )
        == 1
        and cosign_block.count(
            '--bundle "${candidate_dir}/cosign-signature-bundle.json"'
        )
        == 2
        and cosign_block.count(
            '"${candidate_dir}/oci-manifest.json"'
        )
        == 1,
        "WORKFLOW_COSIGN_BINDING",
        "detached Cosign bundle is not bound to the exact OCI manifest",
    )
    sign_index = next(
        index
        for index, line in enumerate(cosign_lines)
        if line.startswith("cosign sign ")
    )
    blob_index = next(
        index
        for index, line in enumerate(cosign_lines)
        if line.startswith("cosign verify-blob ")
    )
    registry_index = next(
        index
        for index, line in enumerate(cosign_lines)
        if line.startswith("cosign verify ")
    )
    _expect(
        sign_index < blob_index < registry_index,
        "WORKFLOW_COSIGN_BINDING",
        "Cosign bundle and registry verification order changed",
    )
    cosign_constraints = (
        '--certificate-identity "${identity}"',
        "--certificate-oidc-issuer",
        '--certificate-github-workflow-name "${GITHUB_WORKFLOW}"',
        (
            '--certificate-github-workflow-repository '
            '"${GITHUB_REPOSITORY}"'
        ),
        '--certificate-github-workflow-ref "${GITHUB_REF}"',
        '--certificate-github-workflow-sha "${GITHUB_WORKFLOW_SHA}"',
        '--certificate-github-workflow-trigger "${GITHUB_EVENT_NAME}"',
    )
    _expect(
        cosign_block.count(
            'identity="https://github.com/${GITHUB_REPOSITORY}/'
            ".github/workflows/polaris-admin-build-inputs.yml@"
            '${GITHUB_REF}"'
        )
        == 1
        and all(
            cosign_block.count(constraint) == 2
            for constraint in cosign_constraints
        )
        and cosign_block.count("--output json") == 1,
        "WORKFLOW_COSIGN_IDENTITY",
        "Cosign verification is not bound to the exact workflow identity",
    )


def audit(root: Path) -> None:
    root = root.resolve()
    contract = _load_json(root / CONTRACT_PATH)
    _expect(
        isinstance(contract, dict),
        "CONTRACT_SCHEMA",
        "contract root must be an object",
    )
    _validate_contract(root, contract)
    try:
        workflow = (root / WORKFLOW_PATH).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        _fail("WORKFLOW_READ", str(error))
    _validate_workflow(contract, workflow)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    audit_parser = subparsers.add_parser("audit")
    audit_parser.add_argument("--root", type=Path, default=Path("."))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        audit(args.root)
    except ContractError as error:
        print(f"polaris-admin-build-inputs: {error}", file=sys.stderr)
        return 1
    print(
        "polaris-admin-build-inputs: publication-pending contract verified; "
        "admin image/runtime remain disabled"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
