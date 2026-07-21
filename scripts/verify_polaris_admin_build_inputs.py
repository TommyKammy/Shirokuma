#!/usr/bin/env python3
"""Fail-closed audit for the reviewed Polaris Admin build-input evidence."""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping


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
EVIDENCE_PATH = Path(
    "bootstrap/polaris/v1.6.0/admin-build-inputs-evidence"
)
WORKFLOW_PATH = Path(".github/workflows/polaris-admin-build-inputs.yml")
LEGACY_WORKFLOW_PATH = Path(".github/workflows/polaris-gradle-dependencies.yml")
PACKAGER_PATH = Path("scripts/package_polaris_gradle_dependencies.py")
SOURCE_VALIDATOR_PATH = Path("scripts/validate_polaris_source_archive.py")

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
CHECKSUM_LINE_RE = re.compile(r"^([0-9a-f]{64})  ([^\s].*)$")

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
EXPECTED_BUILDER_PLATFORM = "linux/arm64"
EXPECTED_GRADLE_VERSION = "9.6.0"
EXPECTED_JAVA_MAJOR = 21
EXPECTED_PARENT_REFERENCE = (
    "ghcr.io/tommykammy/shirokuma-polaris-gradle-dependencies@"
    "sha256:fa889d2c0a6e6dc48816d79680a366e21040be333ab6007b88e4ca4dbf6e59d6"
)
EXPECTED_ARTIFACT_REPOSITORY = (
    "ghcr.io/tommykammy/shirokuma-polaris-admin-gradle-dependencies"
)
EXPECTED_MANIFEST_SHA256 = (
    "7a505defcd78c7a7b978e88cd4c72e0a5d8b69cbb57ddd311c163b09fe789d18"
)
EXPECTED_ARTIFACT_REFERENCE = (
    f"{EXPECTED_ARTIFACT_REPOSITORY}@sha256:{EXPECTED_MANIFEST_SHA256}"
)
EXPECTED_ARCHIVE_SHA256 = (
    "e771fe2ec6b2d0f6940b1247a512eb5cbc78dd0f36e7be247975f2c5fa36fc4d"
)
EXPECTED_ARCHIVE_SIZE = 701_437_153
EXPECTED_DESCRIPTOR_SHA256 = (
    "798802722e730174caa581cbffd4f82e5dd4a43aee92201df26f14db4ab005bc"
)
EXPECTED_DESCRIPTOR_SIZE = 2_175_793
EXPECTED_VERIFICATION_SHA256 = (
    "171ccaf781d4ae63375b332205d25653ebcd29471e9e9c0cfba1b978144065b8"
)
EXPECTED_VERIFICATION_SIZE = 881_256
EXPECTED_PUBLICATION_SHA256 = (
    "a6453655a183528904bde4e295306ae1cdc92abe67f29479a82ee093975ed9bc"
)
EXPECTED_PUBLICATION_SIZE = 3_676
EXPECTED_EVIDENCE_MANIFEST_SHA256 = (
    "026c4d82e9031532323ccb3c31ea83939010982cfcf373644cdcf064e2613409"
)
EXPECTED_EVIDENCE_MANIFEST_SIZE = 953
EXPECTED_SOURCE_SHA = "619d52e0b1db5241867d7775cc8714a30b1a6f38"
EXPECTED_RUN_ID = "29781460117"
EXPECTED_RUN_ATTEMPT = "1"
EXPECTED_REPOSITORY = "TommyKammy/Shirokuma"
EXPECTED_REPOSITORY_URL = "https://github.com/TommyKammy/Shirokuma"
EXPECTED_REF = "refs/heads/main"
EXPECTED_EVENT = "push"
EXPECTED_WORKFLOW_IDENTITY = (
    "https://github.com/TommyKammy/Shirokuma/.github/workflows/"
    "polaris-admin-build-inputs.yml@refs/heads/main"
)
EXPECTED_ISSUER = "https://token.actions.githubusercontent.com"
EXPECTED_CREATED = "2026-07-21T06:46:09+09:00"
EXPECTED_REVIEWED_AT = "2026-07-20T22:27:20Z"
EXPECTED_REVIEW_CHECKPOINT = {
    "repository": "TommyKammy/Shirokuma",
    "pull_request": 87,
    "reviewed_head_commit": "178b5ca03a2575a7100cfb47daede795fbd1d30c",
    "merge_commit": "8e5c6927e95d1027e16fe2ac27ab8322b45359c9",
    "merged_at": "2026-07-20T23:24:12Z",
    "merged_by": "TommyKammy",
    "reviewed_contract_sha256": (
        "26f5259642007aa11a4676ccee918bd5b1e55f8eb5c0025f92a12f1d8ccb37db"
    ),
    "reviewed_evidence_manifest_sha256": EXPECTED_EVIDENCE_MANIFEST_SHA256,
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
EXPECTED_NOSQL_SOURCES = [
    {
        "path": (
            "runtime/admin/src/main/java/org/apache/polaris/admintool/nosql/"
            "BaseNoSqlCommand.java"
        ),
        "git_blob": "8ed348be717debb68aa62ddeaee28f65c6c0c22c",
    },
    {
        "path": (
            "runtime/admin/src/main/java/org/apache/polaris/admintool/nosql/"
            "NoSqlCommand.java"
        ),
        "git_blob": "639cbbfedabaf8963f7a699d9496e1bd0ab3dcb5",
    },
    {
        "path": (
            "runtime/admin/src/main/java/org/apache/polaris/admintool/nosql/"
            "maintenance/BaseNoSqlMaintenanceCommand.java"
        ),
        "git_blob": "39ab3bfc95f44dd5bd6b81ee54ce1fff290f34f3",
    },
    {
        "path": (
            "runtime/admin/src/main/java/org/apache/polaris/admintool/nosql/"
            "maintenance/NoSqlMaintenanceInfoCommand.java"
        ),
        "git_blob": "03d6c92aeb9de83c16b29ca6850a058b94c9a19a",
    },
    {
        "path": (
            "runtime/admin/src/main/java/org/apache/polaris/admintool/nosql/"
            "maintenance/NoSqlMaintenanceRunCommand.java"
        ),
        "git_blob": "6e415a1adf8f09434f9ef3a16d3d2d9f47cea032",
    },
]
SUPERSET_FIELDS = (
    "path",
    "sha256",
    "size",
    "kind",
    "group",
    "module",
    "version",
    "artifact",
)

EVIDENCE_RECORDS = {
    "candidate.sha256": (
        "172f71f466d9b1b009359c7069541154fb00a72d568415a43597c4593c9000b5",
        547,
    ),
    "cosign-signature-bundle.json": (
        "f96675ee16fbbdc478e3d5febc5bab1953f7d70c68a26b7b79afb1ebb3c7811d",
        11_143,
    ),
    "cosign-verify.json": (
        "84a3debbf0f6c3eace8eca839b0577bfc2f5896852872db69b143b80566c3f79",
        355,
    ),
    "gradle-dependency-inputs.json": (
        EXPECTED_DESCRIPTOR_SHA256,
        EXPECTED_DESCRIPTOR_SIZE,
    ),
    "oci-manifest.json": (EXPECTED_MANIFEST_SHA256, 1_083),
    "offline-build.json": (
        "12c027f726e62213605fe094a9b4328bcb3351148bdd90a71e5e38c2b766fa68",
        725,
    ),
    "publication.json": (
        EXPECTED_PUBLICATION_SHA256,
        EXPECTED_PUBLICATION_SIZE,
    ),
    "slsa-verify.json": (
        "687dea8a3ea7d86c5316d32235e7e5a372c6b38861505a887c6fe318966c0741",
        14_334,
    ),
    "superset-proof.json": (
        "afc26c4c6fb48ea423ed4b057a167c2768fe6104a0f88f65533b768877cb80f9",
        413,
    ),
    "toolchain.json": (
        "6a482b37d97d46df1b9c71fd041473aa57f30d1c5325104ac6fb8f3074f74d7f",
        709,
    ),
    "verification-metadata.xml": (
        EXPECTED_VERIFICATION_SHA256,
        EXPECTED_VERIFICATION_SIZE,
    ),
}
EVIDENCE_MANIFEST_RECORD = (
    EXPECTED_EVIDENCE_MANIFEST_SHA256,
    EXPECTED_EVIDENCE_MANIFEST_SIZE,
)
EXPECTED_ARTIFACT_METADATA = {
    "id": 8_477_021_002,
    "name": "polaris-admin-publication-29781460117-1",
    "sha256": (
        "d1d33b14467a58b93796568667ab68ad3f61a12f9f9c3af439bbd6361adee621"
    ),
    "size": 582_463,
    "run_id": EXPECTED_RUN_ID,
    "run_attempt": EXPECTED_RUN_ATTEMPT,
}


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
    value: Any,
    expected: set[str],
    code: str,
    location: str,
) -> None:
    _expect(isinstance(value, Mapping), code, f"{location} must be an object")
    actual = set(value)
    _expect(
        actual == expected,
        code,
        f"{location} keys differ: expected={sorted(expected)!r} "
        f"actual={sorted(actual)!r}",
    )


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, nested in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = nested
    return value


def _load_json(path: Path, *, maximum_bytes: int = 8 * 1024 * 1024) -> Any:
    try:
        size = path.stat().st_size
    except OSError as error:
        _fail("FILE_READ", f"cannot stat {path}: {error}")
    _expect(size <= maximum_bytes, "FILE_SIZE", f"{path} exceeds {maximum_bytes} bytes")
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
        )
    except (OSError, UnicodeError, ValueError) as error:
        _fail("JSON_INVALID", f"cannot load {path}: {error}")


def _sha256_and_size(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
    except OSError as error:
        _fail("FILE_READ", f"cannot hash {path}: {error}")
    return digest.hexdigest(), size


def _require_file(
    root: Path,
    relative: Path,
    expected_sha256: str,
    expected_size: int,
    code: str,
) -> None:
    actual = _sha256_and_size(root / relative)
    _expect(
        actual == (expected_sha256, expected_size),
        code,
        f"{relative} sha256/size={actual!r}, "
        f"expected={(expected_sha256, expected_size)!r}",
    )


def _is_regular_without_symlink(root: Path, relative: Path) -> bool:
    current = root
    try:
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                return False
        return current.is_file()
    except OSError:
        return False


def _module_records(descriptor: Mapping[str, Any]) -> set[tuple[Any, ...]]:
    records = descriptor.get("files")
    _expect(isinstance(records, list), "DESCRIPTOR", "descriptor files must be a list")
    values = [
        tuple(record.get(field) for field in SUPERSET_FIELDS)
        for record in records
        if isinstance(record, Mapping) and record.get("kind") == "module-artifact"
    ]
    _expect(
        len(values) == len(set(values)),
        "DESCRIPTOR",
        "module-artifact records must be unique",
    )
    return set(values)


def _packager_module(root: Path) -> Any:
    try:
        spec = importlib.util.spec_from_file_location(
            "_polaris_admin_packager_for_evidence",
            root / PACKAGER_PATH,
        )
        if spec is None or spec.loader is None:
            _fail("DESCRIPTOR", "cannot load the reviewed dependency packager")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except (OSError, ImportError, AttributeError) as error:
        _fail("DESCRIPTOR", f"cannot load the reviewed dependency packager: {error}")


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
            "downstream_gates",
        },
        "CONTRACT_SCHEMA",
        "root",
    )
    _expect(
        contract["schema_version"] == 3
        and contract["component"] == "polaris-admin-build-inputs"
        and contract["version"] == "1.6.0"
        and contract["platform"] == EXPECTED_BUILDER_PLATFORM,
        "CONTRACT_IDENTITY",
        "component, version, platform, or schema changed",
    )
    _expect(
        contract["lifecycle"]
        == {
            "state": "admin_image_publication_pending",
            "next_state": "admin_image_evidence_review_pending",
        },
        "LIFECYCLE_STATE",
        repr(contract["lifecycle"]),
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
            "java_major": EXPECTED_JAVA_MAJOR,
            "gradle_version": EXPECTED_GRADLE_VERSION,
            "tasks": EXPECTED_TASKS,
            "admin_build_preimage": {
                "path": "runtime/admin/build.gradle.kts",
                "git_blob": "94bf1dfd2b1039f1ca23d5dd7437429c11db66dd",
                "sha256": EXPECTED_ADMIN_BUILD_SHA256,
                "size": 4_149,
            },
        },
        "SOURCE_CONTRACT",
        "source pins, build preimage, or task closure changed",
    )

    parent = contract["parent_snapshot"]
    _expect(
        parent
        == {
            "use": "reviewed_seed_only",
            "review_checkpoint": {
                "merge_commit": "b12593f27ae4e6ec8b64865f9b6b0bbf114ec654"
            },
            "artifact_reference": EXPECTED_PARENT_REFERENCE,
            "descriptor": {
                "path": PARENT_DESCRIPTOR_PATH.as_posix(),
                "sha256": EXPECTED_PARENT_DESCRIPTOR_SHA256,
            },
            "cache_layer": {
                "filename": "polaris-gradle-dependencies-1.6.0.tar.gz",
                "sha256": EXPECTED_PARENT_ARCHIVE_SHA256,
            },
            "verification_metadata": {
                "path": PARENT_VERIFICATION_PATH.as_posix(),
                "sha256": EXPECTED_PARENT_VERIFICATION_SHA256,
            },
            "required_relationship": "exact-module-artifact-superset",
        },
        "PARENT_SNAPSHOT",
        "reviewed parent identity or required relationship changed",
    )

    surface = contract["admin_dependency_surface"]
    _expect_keys(
        surface,
        {
            "review_state",
            "relational_only",
            "image_publication_decision",
            "source_overlay_permitted",
            "runtime_activation_permitted",
            "reason",
            "unconditional_project_dependencies",
            "unconditional_external_dependencies",
            "main_source_records",
        },
        "ADMIN_SURFACE_SCHEMA",
        "admin_dependency_surface",
    )
    _expect(
        surface["review_state"] == "reviewed_for_image_publication"
        and surface["relational_only"] is False
        and surface["image_publication_decision"]
        == "accepted_unmodified_for_image_publication_only"
        and surface["source_overlay_permitted"] is False
        and surface["runtime_activation_permitted"] is False
        and "unconditionally" in surface["reason"]
        and surface["unconditional_project_dependencies"] == EXPECTED_NOSQL_PROJECTS
        and surface["unconditional_external_dependencies"]
        == ["io.quarkus:quarkus-mongodb-client"]
        and surface["main_source_records"] == EXPECTED_NOSQL_SOURCES
        and all(
            SHA1_RE.fullmatch(record["git_blob"]) is not None
            for record in surface["main_source_records"]
        ),
        "ADMIN_SURFACE",
        "unconditional upstream NoSQL/Mongo surface is not exactly bound to the image-only review decision",
    )

    candidate = contract["candidate_snapshot"]
    _expect_keys(
        candidate,
        {
            "state",
            "admitted",
            "artifact_repository",
            "artifact_reference",
            "artifact_type",
            "descriptor_media_type",
            "archive_media_type",
            "archive",
            "descriptor",
            "verification_metadata",
            "packager",
            "source_archive_validator",
            "superset_proof",
            "offline_proof",
            "retained_evidence",
            "review_checkpoint",
            "publication",
            "review",
            "visibility_bootstrap",
            "tools",
        },
        "CANDIDATE_SCHEMA",
        "candidate_snapshot",
    )
    _expect(
        candidate["state"] == "approved_for_admin_image_build"
        and candidate["admitted"] is False
        and candidate["artifact_repository"] == EXPECTED_ARTIFACT_REPOSITORY
        and candidate["artifact_reference"] == EXPECTED_ARTIFACT_REFERENCE
        and candidate["artifact_type"]
        == "application/vnd.shirokuma.polaris-admin.gradle-dependencies.v1"
        and candidate["descriptor_media_type"]
        == "application/vnd.shirokuma.gradle-dependency-descriptor.v1+json"
        and candidate["archive_media_type"]
        == "application/vnd.shirokuma.gradle-cache.v1.tar+gzip"
        and candidate["archive"]
        == {
            "filename": "polaris-gradle-dependencies-1.6.0.tar.gz",
            "sha256": EXPECTED_ARCHIVE_SHA256,
            "size": EXPECTED_ARCHIVE_SIZE,
        }
        and candidate["descriptor"]
        == {
            "path": (EVIDENCE_PATH / "gradle-dependency-inputs.json").as_posix(),
            "sha256": EXPECTED_DESCRIPTOR_SHA256,
            "size": EXPECTED_DESCRIPTOR_SIZE,
        }
        and candidate["verification_metadata"]
        == {
            "path": (EVIDENCE_PATH / "verification-metadata.xml").as_posix(),
            "sha256": EXPECTED_VERIFICATION_SHA256,
            "size": EXPECTED_VERIFICATION_SIZE,
        }
        and candidate["packager"]
        == {"path": PACKAGER_PATH.as_posix(), "sha256": EXPECTED_PACKAGER_SHA256}
        and candidate["source_archive_validator"]
        == {
            "path": SOURCE_VALIDATOR_PATH.as_posix(),
            "sha256": EXPECTED_SOURCE_VALIDATOR_SHA256,
        },
        "CANDIDATE_SNAPSHOT",
        "reviewed candidate identity, bytes, or trusted tool pin changed",
    )
    _expect(
        candidate["review_checkpoint"] == EXPECTED_REVIEW_CHECKPOINT,
        "REVIEW_CHECKPOINT",
        "PR #87 merge, reviewed evidence, contract, or verifier binding changed",
    )
    _expect(
        candidate["superset_proof"]
        == {
            "scope": "parent-module-artifact-records",
            "match_fields": list(SUPERSET_FIELDS),
            "result": "required",
        },
        "SUPERSET_POLICY",
        repr(candidate["superset_proof"]),
    )
    _expect(
        candidate["offline_proof"]
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
        repr(candidate["offline_proof"]),
    )
    _expect(
        candidate["retained_evidence"]
        == {
            "directory": EVIDENCE_PATH.as_posix(),
            "file_count": 12,
            "checksum_manifest": {
                "path": (EVIDENCE_PATH / "evidence.sha256").as_posix(),
                "sha256": EXPECTED_EVIDENCE_MANIFEST_SHA256,
                "size": EXPECTED_EVIDENCE_MANIFEST_SIZE,
            },
        },
        "RETAINED_EVIDENCE_CONTRACT",
        "retained evidence directory or self-manifest binding changed",
    )
    publication = candidate["publication"]
    _expect(
        publication
        == {
            "record": {
                "path": (EVIDENCE_PATH / "publication.json").as_posix(),
                "sha256": EXPECTED_PUBLICATION_SHA256,
                "size": EXPECTED_PUBLICATION_SIZE,
            },
            "actions_artifact": EXPECTED_ARTIFACT_METADATA,
            "publisher": {
                "path": WORKFLOW_PATH.as_posix(),
                "sha256": (
                    "d4f4b71c993b797c89080c4877ba29a4a1c588c3f84884dacddcb00045057c63"
                ),
                "repository": EXPECTED_REPOSITORY,
                "ref": EXPECTED_REF,
                "source_sha": EXPECTED_SOURCE_SHA,
                "workflow_sha": EXPECTED_SOURCE_SHA,
                "event": EXPECTED_EVENT,
                "retired": True,
            },
        },
        "PUBLICATION_CONTRACT",
        "publication record, GitHub artifact, or retired publisher binding changed",
    )
    _expect(
        candidate["review"]
        == {
            "state": "locally_verified",
            "review_kind": "automated_local_evidence_verification",
            "reviewer": "codex-local-evidence-verifier",
            "reviewed_at": EXPECTED_REVIEWED_AT,
            "human_approval": False,
            "anonymous_retrieval": {
                "artifact_reference": EXPECTED_ARTIFACT_REFERENCE,
                "digest": f"sha256:{EXPECTED_MANIFEST_SHA256}",
                "user_credentials": False,
                "oras_pull": {
                    "version": "1.3.3",
                    "registry_config": {
                        "kind": "temporary-json-file",
                        "initial_contents": "{}",
                    },
                    "manifest_retrieved": True,
                    "descriptor_retrieved": True,
                    "archive_started": True,
                    "archive_completed": False,
                    "error": "HTTP/2 PROTOCOL_ERROR",
                },
                "archive_resume": {
                    "method": (
                        "anonymous-ghcr-pull-bearer-token-http1.1-range"
                    ),
                    "user_credentials": False,
                    "result": "passed",
                },
                "descriptor": {
                    "filename": "gradle-dependency-inputs.json",
                    "sha256": EXPECTED_DESCRIPTOR_SHA256,
                    "size": EXPECTED_DESCRIPTOR_SIZE,
                    "cmp_with_retained_evidence": "passed",
                },
                "archive": {
                    "filename": "polaris-gradle-dependencies-1.6.0.tar.gz",
                    "sha256": EXPECTED_ARCHIVE_SHA256,
                    "size": EXPECTED_ARCHIVE_SIZE,
                    "gzip_test": "passed",
                },
                "result": "passed",
            },
        },
        "REVIEW_RECEIPT",
        "independent anonymous retrieval receipt is absent or not exact-digest-bound",
    )
    _expect(
        candidate["visibility_bootstrap"]
        == {
            "required_visibility": "public",
            "sign_and_attest_before_anonymous_pull": True,
            "owner_action_on_first_private_run": "set-package-public-and-rerun",
            "failed_attempt_admitted": False,
            "user_credential_fallback": False,
            "anonymous_registry_v2_bearer_challenge_permitted": True,
        }
        and candidate["tools"]
        == {
            "oras": {
                "version": "1.3.3",
                "linux_arm64_archive_sha256": (
                    "ac7156f93a21e903f7ad606c792f3560f17e0cd0e36365634701b1e7cc4e4eca"
                ),
            },
            "cosign": {
                "version": "3.1.1",
                "issuer": EXPECTED_ISSUER,
                "identity": EXPECTED_WORKFLOW_IDENTITY,
            },
        },
        "REVIEW_TOOLCHAIN",
        "visibility bootstrap or retained review toolchain changed",
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
        gates["admin_image_publication_enabled"] is True
        and all(
            gates[key] is False
            for key in (
                "admin_image_admitted",
                "admin_runtime_enabled",
                "gitops_resources_enabled",
                "credential_material_permitted",
            )
        )
        and gates["next_checkpoint"]
        == (
            "publish, retain, and review the exact Admin image evidence before "
            "admission or runtime activation"
        ),
        "DOWNSTREAM_GATE",
        "only Admin image publication may be enabled before image evidence review",
    )

    _require_file(root, SOURCE_PATH, EXPECTED_SOURCE_SHA256, 2_798, "SOURCE_HASH")
    _require_file(
        root,
        PARENT_DESCRIPTOR_PATH,
        EXPECTED_PARENT_DESCRIPTOR_SHA256,
        2_172_595,
        "PARENT_DESCRIPTOR_HASH",
    )
    _require_file(
        root,
        PARENT_VERIFICATION_PATH,
        EXPECTED_PARENT_VERIFICATION_SHA256,
        879_926,
        "PARENT_VERIFICATION_HASH",
    )
    _require_file(
        root,
        PACKAGER_PATH,
        EXPECTED_PACKAGER_SHA256,
        69_958,
        "PACKAGER_HASH",
    )
    _require_file(
        root,
        SOURCE_VALIDATOR_PATH,
        EXPECTED_SOURCE_VALIDATOR_SHA256,
        20_634,
        "SOURCE_VALIDATOR_HASH",
    )
    _expect(
        not (root / WORKFLOW_PATH).exists(),
        "PUBLISHER_PRESENT",
        f"one-shot publisher must be retired: {WORKFLOW_PATH}",
    )
    _expect(
        not (root / LEGACY_WORKFLOW_PATH).exists(),
        "LEGACY_WORKFLOW_PRESENT",
        LEGACY_WORKFLOW_PATH.as_posix(),
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
        _expect(
            filename not in records
            and Path(filename).name == filename
            and filename not in {".", ".."},
            code,
            f"unsafe or duplicate checksum filename: {filename!r}",
        )
        records[filename] = digest
    return records


def _audit_evidence_inventory(root: Path) -> None:
    directory = root / EVIDENCE_PATH
    _expect(
        directory.is_dir() and not directory.is_symlink(),
        "EVIDENCE_INVENTORY",
        "Admin dependency evidence root must be a real directory",
    )
    actual = {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    expected = {"evidence.sha256", *EVIDENCE_RECORDS}
    _expect(
        actual == expected,
        "EVIDENCE_INVENTORY",
        f"exact 12-file inventory required; expected={sorted(expected)!r}, "
        f"actual={sorted(actual)!r}",
    )
    for filename in expected:
        relative = EVIDENCE_PATH / filename
        _expect(
            _is_regular_without_symlink(root, relative),
            "EVIDENCE_INVENTORY",
            f"evidence must be a real regular file: {relative}",
        )
    for filename, (digest, size) in EVIDENCE_RECORDS.items():
        _require_file(root, EVIDENCE_PATH / filename, digest, size, "EVIDENCE_BYTES")
    _require_file(
        root,
        EVIDENCE_PATH / "evidence.sha256",
        *EVIDENCE_MANIFEST_RECORD,
        "EVIDENCE_BYTES",
    )
    evidence_manifest = _parse_checksum_manifest(
        directory / "evidence.sha256",
        "EVIDENCE_MANIFEST",
    )
    _expect(
        evidence_manifest
        == {name: digest for name, (digest, _) in EVIDENCE_RECORDS.items()},
        "EVIDENCE_MANIFEST",
        "evidence.sha256 must bind each of the other 11 retained files exactly once",
    )
    candidate_manifest = _parse_checksum_manifest(
        directory / "candidate.sha256",
        "CANDIDATE_MANIFEST",
    )
    _expect(
        candidate_manifest
        == {
            "gradle-dependency-inputs.json": EXPECTED_DESCRIPTOR_SHA256,
            "offline-build.json": EVIDENCE_RECORDS["offline-build.json"][0],
            "polaris-gradle-dependencies-1.6.0.tar.gz": EXPECTED_ARCHIVE_SHA256,
            "superset-proof.json": EVIDENCE_RECORDS["superset-proof.json"][0],
            "toolchain.json": EVIDENCE_RECORDS["toolchain.json"][0],
            "verification-metadata.xml": EXPECTED_VERIFICATION_SHA256,
        },
        "CANDIDATE_MANIFEST",
        "candidate checksum manifest differs from the read-only job output set",
    )


def _audit_descriptor(root: Path) -> Mapping[str, Any]:
    descriptor = _load_json(
        root / EVIDENCE_PATH / "gradle-dependency-inputs.json"
    )
    _expect(
        isinstance(descriptor, Mapping),
        "DESCRIPTOR",
        "descriptor must be an object",
    )
    validator = getattr(_packager_module(root), "_validate_descriptor", None)
    _expect(
        callable(validator),
        "DESCRIPTOR",
        "reviewed packager lacks descriptor validation",
    )
    try:
        validator(descriptor, root / EVIDENCE_PATH / "verification-metadata.xml")
    except Exception as error:
        _fail("DESCRIPTOR", f"descriptor validation failed: {error}")
    _expect(
        descriptor.get("schema_version") == 1
        and descriptor.get("component") == "polaris-gradle-dependencies"
        and descriptor.get("polaris_version") == "1.6.0"
        and descriptor.get("platform") == EXPECTED_BUILDER_PLATFORM
        and descriptor.get("gradle_version") == EXPECTED_GRADLE_VERSION
        and descriptor.get("source_archive_sha512")
        == (
            "d69b1a91e16e210a78dec327fc4725983b114fbec5d86d078a3827f35fe7dd"
            "5df3e4b12d18965e5a72eace65ad224aa007004ed61c66f9abb2efafc44ceac95b"
        )
        and descriptor.get("archive")
        == {
            "filename": "polaris-gradle-dependencies-1.6.0.tar.gz",
            "media_type": "application/vnd.shirokuma.gradle-cache.v1.tar+gzip",
            "sha256": EXPECTED_ARCHIVE_SHA256,
            "size": EXPECTED_ARCHIVE_SIZE,
        }
        and descriptor.get("verification_metadata")
        == {
            "filename": "verification-metadata.xml",
            "media_type": "application/vnd.gradle.dependency-verification.v1+xml",
            "mode": "strict",
            "sha256": EXPECTED_VERIFICATION_SHA256,
            "size": EXPECTED_VERIFICATION_SIZE,
        },
        "DESCRIPTOR",
        "descriptor source, archive, or strict verification binding changed",
    )
    parent = _load_json(root / PARENT_DESCRIPTOR_PATH)
    _expect(
        isinstance(parent, Mapping),
        "SUPERSET",
        "parent descriptor must be an object",
    )
    parent_records = _module_records(parent)
    candidate_records = _module_records(descriptor)
    _expect(
        len(parent_records) == 3_263
        and len(candidate_records) == 3_268
        and parent_records <= candidate_records,
        "SUPERSET",
        "candidate is not the exact parent module-artifact superset",
    )
    mongo_records = {
        record
        for record in candidate_records
        if record[4] == "io.quarkus"
        and record[5] == "quarkus-mongodb-client"
        and record[6] == "3.36.3"
        and record[7]
        in {"quarkus-mongodb-client-3.36.3.jar", "quarkus-mongodb-client-3.36.3.pom"}
    }
    _expect(
        len(mongo_records) == 2,
        "ADMIN_SURFACE_EVIDENCE",
        "descriptor does not retain the unconditional Quarkus MongoDB client surface",
    )
    proof = _load_json(root / EVIDENCE_PATH / "superset-proof.json")
    _expect(
        proof
        == {
            "candidate_module_artifact_count": len(candidate_records),
            "match_fields": list(SUPERSET_FIELDS),
            "parent_descriptor_sha256": EXPECTED_PARENT_DESCRIPTOR_SHA256,
            "parent_module_artifact_count": len(parent_records),
            "relationship": "exact-module-artifact-superset",
            "result": "passed",
            "schema_version": 1,
        },
        "SUPERSET",
        "retained superset proof differs from the computed exact relationship",
    )
    return descriptor


def _audit_oci_manifest(root: Path) -> Mapping[str, Any]:
    manifest = _load_json(root / EVIDENCE_PATH / "oci-manifest.json")
    _expect(isinstance(manifest, Mapping), "OCI_MANIFEST", "manifest must be an object")
    _expect_keys(
        manifest,
        {
            "schemaVersion",
            "mediaType",
            "artifactType",
            "config",
            "layers",
            "annotations",
        },
        "OCI_MANIFEST",
        "manifest",
    )
    _expect(
        manifest["schemaVersion"] == 2
        and manifest["mediaType"] == "application/vnd.oci.image.manifest.v1+json"
        and manifest["artifactType"]
        == "application/vnd.shirokuma.polaris-admin.gradle-dependencies.v1"
        and manifest["config"]
        == {
            "mediaType": "application/vnd.oci.empty.v1+json",
            "digest": (
                "sha256:44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f"
                "61caaff8a"
            ),
            "size": 2,
            "data": "e30=",
        }
        and manifest["annotations"]
        == {
            "org.opencontainers.image.created": EXPECTED_CREATED,
            "org.opencontainers.image.revision": EXPECTED_SOURCE_SHA,
            "org.opencontainers.image.source": EXPECTED_REPOSITORY_URL,
        },
        "OCI_MANIFEST",
        "OCI identity, config, or source annotations changed",
    )
    _expect(
        manifest["layers"]
        == [
            {
                "mediaType": (
                    "application/vnd.shirokuma."
                    "gradle-dependency-descriptor.v1+json"
                ),
                "digest": f"sha256:{EXPECTED_DESCRIPTOR_SHA256}",
                "size": EXPECTED_DESCRIPTOR_SIZE,
                "annotations": {
                    "org.opencontainers.image.title": (
                        "gradle-dependency-inputs.json"
                    )
                },
            },
            {
                "mediaType": "application/vnd.shirokuma.gradle-cache.v1.tar+gzip",
                "digest": f"sha256:{EXPECTED_ARCHIVE_SHA256}",
                "size": EXPECTED_ARCHIVE_SIZE,
                "annotations": {
                    "org.opencontainers.image.title": (
                        "polaris-gradle-dependencies-1.6.0.tar.gz"
                    )
                },
            },
        ],
        "OCI_MANIFEST",
        "OCI descriptor/archive layer order, hash, or size changed",
    )
    return manifest


def _audit_publication(root: Path, contract: Mapping[str, Any]) -> Mapping[str, Any]:
    publication = _load_json(root / EVIDENCE_PATH / "publication.json")
    _expect(
        isinstance(publication, Mapping),
        "PUBLICATION",
        "publication must be an object",
    )
    expected_files = [
        {
            "filename": "gradle-dependency-inputs.json",
            "sha256": EXPECTED_DESCRIPTOR_SHA256,
            "size": EXPECTED_DESCRIPTOR_SIZE,
        },
        {
            "filename": "polaris-gradle-dependencies-1.6.0.tar.gz",
            "sha256": EXPECTED_ARCHIVE_SHA256,
            "size": EXPECTED_ARCHIVE_SIZE,
        },
        {
            "filename": "verification-metadata.xml",
            "sha256": EXPECTED_VERIFICATION_SHA256,
            "size": EXPECTED_VERIFICATION_SIZE,
        },
        *[
            {
                "filename": name,
                "sha256": EVIDENCE_RECORDS[name][0],
                "size": EVIDENCE_RECORDS[name][1],
            }
            for name in (
                "offline-build.json",
                "superset-proof.json",
                "toolchain.json",
                "oci-manifest.json",
                "cosign-signature-bundle.json",
                "cosign-verify.json",
                "slsa-verify.json",
            )
        ],
    ]
    _expect_keys(
        publication,
        {
            "schema_version",
            "state",
            "artifact_repository",
            "artifact_reference",
            "artifact_type",
            "created",
            "event",
            "ref",
            "run_id",
            "run_attempt",
            "source_sha",
            "workflow_sha",
            "parent_artifact_reference",
            "parent_descriptor_sha256",
            "source_admin_build_preimage_sha256",
            "tasks",
            "admin_dependency_surface",
            "downstream_gates",
            "files",
        },
        "PUBLICATION",
        "publication",
    )
    _expect(
        publication["schema_version"] == 1
        and publication["state"] == "admin_dependency_snapshot_review_pending"
        and publication["artifact_repository"] == EXPECTED_ARTIFACT_REPOSITORY
        and publication["artifact_reference"] == EXPECTED_ARTIFACT_REFERENCE
        and publication["artifact_type"]
        == "application/vnd.shirokuma.polaris-admin.gradle-dependencies.v1"
        and publication["created"] == EXPECTED_CREATED
        and publication["event"] == EXPECTED_EVENT
        and publication["ref"] == EXPECTED_REF
        and publication["run_id"] == EXPECTED_RUN_ID
        and publication["run_attempt"] == EXPECTED_RUN_ATTEMPT
        and publication["source_sha"] == EXPECTED_SOURCE_SHA
        and publication["workflow_sha"] == EXPECTED_SOURCE_SHA
        and publication["parent_artifact_reference"] == EXPECTED_PARENT_REFERENCE
        and publication["parent_descriptor_sha256"] == EXPECTED_PARENT_DESCRIPTOR_SHA256
        and publication["source_admin_build_preimage_sha256"]
        == EXPECTED_ADMIN_BUILD_SHA256
        and publication["tasks"] == EXPECTED_TASKS
        and publication["files"] == expected_files,
        "PUBLICATION",
        "publication identity, workflow SHA, task closure, or file bindings changed",
    )
    surface = publication["admin_dependency_surface"]
    _expect(
        surface
        == {
            "review_state": "review_required",
            "relational_only": False,
            "unconditional_project_dependencies": EXPECTED_NOSQL_PROJECTS,
            "unconditional_external_dependencies": [
                "io.quarkus:quarkus-mongodb-client"
            ],
        }
        and surface["relational_only"]
        is contract["admin_dependency_surface"]["relational_only"]
        and surface["unconditional_project_dependencies"]
        == contract["admin_dependency_surface"][
            "unconditional_project_dependencies"
        ]
        and surface["unconditional_external_dependencies"]
        == contract["admin_dependency_surface"][
            "unconditional_external_dependencies"
        ],
        "ADMIN_SURFACE_EVIDENCE",
        "retained publication does not preserve the pre-review NoSQL/Mongo surface",
    )
    _expect(
        publication["downstream_gates"]
        == {
            "admin_image_publication_enabled": False,
            "admin_image_admitted": False,
            "admin_runtime_enabled": False,
            "gitops_resources_enabled": False,
            "credential_material_permitted": False,
        },
        "PUBLICATION_GATES",
        "publication evidence enables an unreviewed downstream gate",
    )
    candidate = contract["candidate_snapshot"]
    _expect(
        candidate["artifact_reference"] == publication["artifact_reference"]
        and candidate["publication"]["record"]
        == {
            "path": (EVIDENCE_PATH / "publication.json").as_posix(),
            "sha256": EXPECTED_PUBLICATION_SHA256,
            "size": EXPECTED_PUBLICATION_SIZE,
        }
        and candidate["review"]["anonymous_retrieval"]["artifact_reference"]
        == publication["artifact_reference"]
        and candidate["review"]["anonymous_retrieval"]["digest"]
        == f"sha256:{EXPECTED_MANIFEST_SHA256}",
        "PUBLICATION_BINDING",
        "contract, independent review receipt, and retained publication differ",
    )
    return publication


def _audit_offline_build(root: Path) -> None:
    offline = _load_json(root / EVIDENCE_PATH / "offline-build.json")
    _expect(
        offline
        == {
            "archive_sha256": EXPECTED_ARCHIVE_SHA256,
            "build_cache": False,
            "configuration_cache": False,
            "dependency_verification": "strict",
            "descriptor_sha256": EXPECTED_DESCRIPTOR_SHA256,
            "fresh_gradle_home": True,
            "fresh_source_tree": True,
            "gradle_offline": True,
            "network": "none",
            "platform": EXPECTED_BUILDER_PLATFORM,
            "result": "passed",
            "schema_version": 1,
            "tasks": EXPECTED_TASKS,
            "verification_metadata_sha256": EXPECTED_VERIFICATION_SHA256,
        },
        "OFFLINE_BUILD",
        "offline build proof is not the reviewed fresh network-none closed build",
    )


def _audit_toolchain(root: Path) -> None:
    toolchain = _load_json(root / EVIDENCE_PATH / "toolchain.json")
    _expect(
        toolchain
        == {
            "builder_image": (
                "docker.io/library/gradle@sha256:"
                "cc583fa5245267fe0e1546c9989e8575473a37336ad9894dc0684a99fea1fb03"
            ),
            "builder_platform": EXPECTED_BUILDER_PLATFORM,
            "curl": (
                "curl 8.5.0 (aarch64-unknown-linux-gnu) libcurl/8.5.0 "
                "OpenSSL/3.0.13 zlib/1.3 brotli/1.1.0 zstd/1.5.5 "
                "libidn2/2.3.7 libpsl/0.21.2 (+libidn2/2.3.7) "
                "libssh/0.10.6/openssl/zlib nghttp2/1.59.0 librtmp/2.3 "
                "OpenLDAP/2.6.10"
            ),
            "docker": "28.0.4",
            "gpg": "gpg (GnuPG) 2.4.4",
            "gradle": EXPECTED_GRADLE_VERSION,
            "java": "21.0.11",
            "java_major": EXPECTED_JAVA_MAJOR,
            "oras": "Version:        1.3.3",
            "platform": "Linux-6.17.0-1020-azure-aarch64-with-glibc2.39",
            "python": "3.12.3",
            "runner": "ubuntu-24.04-arm",
            "schema_version": 1,
            "tar": "tar (GNU tar) 1.35",
        },
        "TOOLCHAIN",
        "resolver toolchain differs from the pre-resolution observations",
    )


def _audit_cosign_verification(root: Path) -> None:
    verification = _load_json(root / EVIDENCE_PATH / "cosign-verify.json")
    _expect(
        verification
        == [
            {
                "critical": {
                    "identity": {"docker-reference": EXPECTED_ARTIFACT_REFERENCE},
                    "image": {
                        "docker-manifest-digest": (
                            f"sha256:{EXPECTED_MANIFEST_SHA256}"
                        )
                    },
                    "type": "https://sigstore.dev/cosign/sign/v1",
                },
                "optional": {},
            }
        ],
        "COSIGN_EVIDENCE",
        "registry verification does not bind the exact Admin artifact manifest",
    )


def _decode_dsse_payload(envelope: Mapping[str, Any]) -> Any:
    _expect_keys(
        envelope,
        {"payload", "payloadType", "signatures"},
        "SLSA_EVIDENCE",
        "DSSE envelope",
    )
    _expect(
        envelope["payloadType"] == "application/vnd.in-toto+json"
        and isinstance(envelope["signatures"], list)
        and len(envelope["signatures"]) == 1,
        "SLSA_EVIDENCE",
        "SLSA DSSE payload type or signature cardinality changed",
    )
    try:
        decoded = base64.b64decode(envelope["payload"], validate=True)
        return json.loads(decoded, object_pairs_hook=_reject_duplicate_pairs)
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        _fail("SLSA_EVIDENCE", f"invalid SLSA DSSE payload: {error}")


def _audit_slsa(root: Path) -> Mapping[str, Any]:
    document = _load_json(root / EVIDENCE_PATH / "slsa-verify.json")
    _expect(
        isinstance(document, list)
        and len(document) == 1
        and isinstance(document[0], Mapping),
        "SLSA_EVIDENCE",
        "SLSA verification must contain exactly one result",
    )
    result = document[0]
    _expect_keys(
        result,
        {"attestation", "verificationResult"},
        "SLSA_EVIDENCE",
        "result",
    )
    verification = result["verificationResult"]
    _expect(
        isinstance(verification, Mapping),
        "SLSA_EVIDENCE",
        "verificationResult missing",
    )
    statement = verification.get("statement")
    certificate = verification.get("signature", {}).get("certificate")
    _expect(
        isinstance(statement, Mapping) and isinstance(certificate, Mapping),
        "SLSA_EVIDENCE",
        "verified statement or certificate is missing",
    )
    expected_certificate = {
        "buildConfigDigest": EXPECTED_SOURCE_SHA,
        "buildConfigURI": EXPECTED_WORKFLOW_IDENTITY,
        "buildSignerDigest": EXPECTED_SOURCE_SHA,
        "buildSignerURI": EXPECTED_WORKFLOW_IDENTITY,
        "buildTrigger": EXPECTED_EVENT,
        "certificateIssuer": "CN=sigstore-intermediate,O=sigstore.dev",
        "githubWorkflowName": "Polaris 1.6.0 Admin build-input snapshot",
        "githubWorkflowRef": EXPECTED_REF,
        "githubWorkflowRepository": EXPECTED_REPOSITORY,
        "githubWorkflowSHA": EXPECTED_SOURCE_SHA,
        "githubWorkflowTrigger": EXPECTED_EVENT,
        "issuer": EXPECTED_ISSUER,
        "runInvocationURI": (
            f"{EXPECTED_REPOSITORY_URL}/actions/runs/{EXPECTED_RUN_ID}/attempts/"
            f"{EXPECTED_RUN_ATTEMPT}"
        ),
        "runnerEnvironment": "github-hosted",
        "sourceRepositoryDigest": EXPECTED_SOURCE_SHA,
        "sourceRepositoryIdentifier": "1289807958",
        "sourceRepositoryOwnerIdentifier": "257892020",
        "sourceRepositoryOwnerURI": "https://github.com/TommyKammy",
        "sourceRepositoryRef": EXPECTED_REF,
        "sourceRepositoryURI": EXPECTED_REPOSITORY_URL,
        "sourceRepositoryVisibilityAtSigning": "public",
        "subjectAlternativeName": EXPECTED_WORKFLOW_IDENTITY,
    }
    _expect(
        certificate == expected_certificate,
        "SLSA_EVIDENCE",
        "SLSA certificate identity, repository, workflow SHA, or run changed",
    )
    expected_predicate = {
        "buildDefinition": {
            "buildType": "https://actions.github.io/buildtypes/workflow/v1",
            "externalParameters": {
                "workflow": {
                    "path": WORKFLOW_PATH.as_posix(),
                    "ref": EXPECTED_REF,
                    "repository": EXPECTED_REPOSITORY_URL,
                }
            },
            "internalParameters": {
                "github": {
                    "event_name": EXPECTED_EVENT,
                    "repository_id": "1289807958",
                    "repository_owner_id": "257892020",
                    "runner_environment": "github-hosted",
                }
            },
            "resolvedDependencies": [
                {
                    "digest": {"gitCommit": EXPECTED_SOURCE_SHA},
                    "uri": f"git+{EXPECTED_REPOSITORY_URL}@{EXPECTED_REF}",
                }
            ],
        },
        "runDetails": {
            "builder": {"id": EXPECTED_WORKFLOW_IDENTITY},
            "metadata": {
                "invocationId": (
                    f"{EXPECTED_REPOSITORY_URL}/actions/runs/"
                    f"{EXPECTED_RUN_ID}/attempts/"
                    f"{EXPECTED_RUN_ATTEMPT}"
                )
            },
        },
    }
    _expect(
        statement
        == {
            "_type": "https://in-toto.io/Statement/v1",
            "subject": [
                {
                    "name": EXPECTED_ARTIFACT_REPOSITORY,
                    "digest": {"sha256": EXPECTED_MANIFEST_SHA256},
                }
            ],
            "predicateType": "https://slsa.dev/provenance/v1",
            "predicate": expected_predicate,
        },
        "SLSA_EVIDENCE",
        "SLSA subject or workflow provenance differs from the reviewed main run",
    )
    bundle = (
        result["attestation"].get("bundle")
        if isinstance(result["attestation"], Mapping)
        else None
    )
    _expect(
        isinstance(bundle, Mapping),
        "SLSA_EVIDENCE",
        "SLSA Sigstore bundle is missing",
    )
    envelope = bundle.get("dsseEnvelope")
    _expect(
        isinstance(envelope, Mapping),
        "SLSA_EVIDENCE",
        "SLSA DSSE envelope is missing",
    )
    _expect(
        _decode_dsse_payload(envelope) == statement,
        "SLSA_EVIDENCE",
        "SLSA DSSE payload differs from the verified statement",
    )
    return bundle


def _run_cosign(root: Path, arguments: list[str], purpose: str) -> None:
    try:
        result = subprocess.run(
            ["cosign", *arguments],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        _fail("SIGSTORE_CRYPTO", f"cannot run Cosign for {purpose}: {error}")
    _expect(
        result.returncode == 0,
        "SIGSTORE_CRYPTO",
        f"Cosign {purpose} failed: {(result.stderr or result.stdout).strip()[-1000:]}",
    )


CryptoVerifier = Callable[[Path, Mapping[str, Any]], None]


def _reverify_sigstore_cryptographically(
    root: Path,
    slsa_bundle: Mapping[str, Any],
) -> None:
    try:
        version = subprocess.run(
            ["cosign", "version"],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        _fail("SIGSTORE_CRYPTO", f"cannot inspect Cosign: {error}")
    _expect(
        version.returncode == 0
        and re.search(r"(?m)^GitVersion:\s+v3\.1\.1\s*$", version.stdout) is not None,
        "SIGSTORE_CRYPTO",
        "Cosign 3.1.1 is required for retained bundle reverification",
    )
    manifest = EVIDENCE_PATH / "oci-manifest.json"
    signature_bundle = EVIDENCE_PATH / "cosign-signature-bundle.json"
    constraints = [
        "--certificate-identity",
        EXPECTED_WORKFLOW_IDENTITY,
        "--certificate-oidc-issuer",
        EXPECTED_ISSUER,
        "--certificate-github-workflow-repository",
        EXPECTED_REPOSITORY,
        "--certificate-github-workflow-ref",
        EXPECTED_REF,
        "--certificate-github-workflow-sha",
        EXPECTED_SOURCE_SHA,
        "--certificate-github-workflow-trigger",
        EXPECTED_EVENT,
    ]
    _run_cosign(
        root,
        [
            "verify-blob",
            "--bundle",
            signature_bundle.as_posix(),
            *constraints,
            manifest.as_posix(),
        ],
        "signature-bundle verification",
    )
    with tempfile.TemporaryDirectory(prefix="polaris-admin-slsa-bundle-") as directory:
        nested = Path(directory) / "bundle.json"
        try:
            nested.write_text(
                json.dumps(slsa_bundle, separators=(",", ":")),
                encoding="utf-8",
            )
        except OSError as error:
            _fail("SIGSTORE_CRYPTO", f"cannot stage retained SLSA bundle: {error}")
        _run_cosign(
            root,
            [
                "verify-blob-attestation",
                "--bundle",
                nested.as_posix(),
                "--type",
                "slsaprovenance1",
                *constraints,
                manifest.as_posix(),
            ],
            "SLSA-bundle verification",
        )


def audit(
    root: Path,
    *,
    crypto_verifier: CryptoVerifier | None = None,
) -> None:
    root = root.resolve()
    contract = _load_json(root / CONTRACT_PATH)
    _expect(
        isinstance(contract, Mapping),
        "CONTRACT_SCHEMA",
        "contract root must be an object",
    )
    _validate_contract(root, contract)
    _audit_evidence_inventory(root)
    _audit_descriptor(root)
    _audit_oci_manifest(root)
    _audit_publication(root, contract)
    _audit_offline_build(root)
    _audit_toolchain(root)
    _audit_cosign_verification(root)
    slsa_bundle = _audit_slsa(root)
    (crypto_verifier or _reverify_sigstore_cryptographically)(root, slsa_bundle)


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
        "polaris-admin-build-inputs: reviewed retained evidence verified; "
        "one-shot dependency publisher absent; Admin image publication is "
        "policy-enabled; admission/runtime/Flux/credentials remain disabled"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
