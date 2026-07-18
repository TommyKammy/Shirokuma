#!/usr/bin/env python3
"""Fail-closed audit for the pre-publication Polaris/PostgreSQL contract."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping


POLARIS_SOURCE = Path("bootstrap/polaris/v1.6.0/source.json")
POLARIS_CONTRACT = Path("bootstrap/polaris/v1.6.0/trusted-build-contract.json")
POLARIS_ADMISSION = Path("bootstrap/polaris/v1.6.0/admission.json")
POLARIS_KEY = Path(
    "bootstrap/polaris/v1.6.0/apache-polaris-release-signing-key.asc"
)
POLARIS_EVIDENCE = Path("bootstrap/polaris/v1.6.0/evidence")
POSTGRES_ADMISSION = Path("bootstrap/postgresql/v18.4/admission.json")
POSTGRES_EVIDENCE = Path("bootstrap/postgresql/v18.4/evidence")
RESIDENT_LEDGER = Path("security/resident-images.json")

POLARIS_VERSION = "1.6.0"
POLARIS_ARCHIVE_SHA512 = (
    "d69b1a91e16e210a78dec327fc4725983b114fbec5d86d078a3827f35fe7dd5"
    "df3e4b12d18965e5a72eace65ad224aa007004ed61c66f9abb2efafc44ceac95b"
)
POLARIS_SIGNATURE_SHA256 = (
    "2338e1c2385874e9bf5cf513b4d27732b1cd59e943e1662e62fa995d915e6481"
)
POLARIS_KEY_SHA256 = (
    "1c72a1534f69e91ffd379c8f1c15c7de1630b919ace45ac896d8b862de26aaa1"
)
POLARIS_KEY_FINGERPRINT = "F2EEEB06110BEE1397EC74CBB8960FF52D9B1312"
POLARIS_KEY_UID = (
    "Apache Polaris Automated Release Signing <private@polaris.apache.org>"
)
POLARIS_COMMIT = "dd306009d81a0e15adafe9dcd7d1c6d04d326f34"
POLARIS_TREE = "1ad42f42aaebfa767b66a37f522a6c8d6693d841"
POLARIS_SOURCE_SHA256 = (
    "7d14b606dd756f501644190c10deb64a1e046d46faacd0f76f92501ccd5185bb"
)
POLARIS_CONTRACT_SHA256 = (
    "edb6bbc472c6498c64b6740769b15b3b05d58eb830c2301438a19e932817cf8b"
)
GRADLE_DISTRIBUTION_SHA256 = (
    "87a2216cc1f9122192d4e0fe905ffdf1b4c72cff797e9f733b174e157cadd396"
)
BUILDER_INDEX = (
    "docker.io/library/gradle@sha256:"
    "ecbf526b4d3c247b4cc61e9850eae2addd5f73a7c849bf026000442808f54b56"
)
BUILDER_ARM64 = (
    "docker.io/library/gradle@sha256:"
    "cc583fa5245267fe0e1546c9989e8575473a37336ad9894dc0684a99fea1fb03"
)
RUNTIME_INDEX = (
    "registry.access.redhat.com/ubi9/openjdk-21-runtime@sha256:"
    "8e4169812e4598113c3d61fbea6c21c1c8e49b5a38c5cd17be6befe9eec4afc8"
)
RUNTIME_ARM64 = (
    "registry.access.redhat.com/ubi9/openjdk-21-runtime@sha256:"
    "76903aaf7aef43c1572674ac745de54e6c9877796127ac498959697afbc84dd5"
)
POSTGRES_INDEX = (
    "cgr.dev/chainguard/postgres@sha256:"
    "3dc629a917612f1630c6f8e7a17f23a42cbd5917b9b3080972b70b1583daff34"
)
POSTGRES_ARM64 = (
    "cgr.dev/chainguard/postgres@sha256:"
    "c455ec159d05d99ee031d471b8692668562fed8e8c9c37be5e0dbdbee8e5f7b8"
)
POSTGRES_ATTESTATION = (
    "sha256:8f5098343c0fc68d434174753d2ba6cefa9c1c037f5185f52b5cf5fbb4ba559b"
)

FORBIDDEN_PENDING_PATHS = (
    Path("bootstrap/polaris/v1.6.0/Containerfile"),
    Path("bootstrap/polaris/v1.6.0/gradle-dependency-inputs.json"),
    Path("bootstrap/polaris/v1.6.0/release-evidence.json"),
    Path(".github/workflows/polaris-gradle-dependencies.yml"),
    Path(".github/workflows/polaris-arm64.yml"),
)
POLARIS_ALLOWED_PATHS = {
    "admission.json",
    "apache-polaris-release-signing-key.asc",
    "evidence",
    "evidence/README.md",
    "source.json",
    "trusted-build-contract.json",
}
POSTGRES_ALLOWED_PATHS = {
    "admission.json",
    "evidence",
    "evidence/README.md",
}
PENDING_WORKFLOW_INVENTORY = {
    ".github/workflows/ci.yml": (
        "36666a76c07b428adda5fe71e4bd21643d05e66f56043dcef514101add63dd72"
    ),
    ".github/workflows/seaweedfs-arm64.yml": (
        "f097273d79c9595d42be816152ff1aabc862faf2667cb0648434280ce8b8ac06"
    ),
    ".github/workflows/security.yml": (
        "717c0fad0d108b271777ea2f61a69682fb57c2d8947b387c81276f095dd8176c"
    ),
}
POLARIS_BLOCKERS = [
    "Gradle dependency closure is not retained or independently reproducible.",
    "The main-only Polaris publication has not run.",
    "Polaris release evidence has not passed an evidence-only review.",
    "PostgreSQL has not been atomically admitted with Polaris.",
]
BUILD_ENABLEMENT_REQUIREMENTS = [
    "publish a main-generated immutable Gradle dependency snapshot as an OCI artifact",
    "retain and review a per-file SHA-256 descriptor for every Gradle plugin and dependency",
    "prove a clean network-none offline server build using only the digest-pinned snapshot",
    "pin the reviewed Containerfile and every build or release tool",
    "add a main-only two-job publication workflow with no shared cache",
]
RUNTIME_ENABLEMENT_REQUIREMENTS = [
    "publish the Polaris image from refs/heads/main",
    "retain and cryptographically reverify signature, transparency, SLSA, CycloneDX, Trivy, and runtime-smoke evidence",
    "admit Polaris and PostgreSQL atomically in an evidence-only pull request",
]
RUNTIME_ROOTS = (Path("deploy"), Path("charts"), Path("opentofu"))
RUNTIME_GENERATED_DIRS = {".terraform"}
RUNTIME_IDENTITY = re.compile(r"(?:polaris|postgres(?:ql)?)", re.IGNORECASE)
CATALOG_IDENTITY_MARKERS = ("catalog", "iceberg", "metastore")
APPROVED_RUNTIME_CATALOG_LINES = {
    "deploy/gitops/object-storage/statefulset.yaml": (
        "            - -s3.port.iceberg=0"
    ),
}
APPROVED_OPENTOFU_SECRET_FILES = {
    "opentofu/dev/object-storage.tf": (
        "94f2c064b972cf412fde7bae1049006a9a01cebe95993fff2daec4a525fa8524"
    ),
}
RUNTIME_CONTEXT_PATH = re.compile(
    r"(?:^|/)(?:catalog|iceberg)(?:/|$)",
    re.IGNORECASE,
)
RUNTIME_CREDENTIAL_PATH = re.compile(
    r"(?:^|[/_.-])(?:secrets?|credentials?)(?:[/_.-]|$)",
    re.IGNORECASE,
)
RUNTIME_SECRET_KIND = r"(?:[A-Za-z0-9]*Secret[A-Za-z0-9]*)"
RUNTIME_SECRET_MANIFEST = re.compile(
    rf"""(?:^"""
    rf"""\s*(?:-\s*)?["']?kind["']?\s*[:=]\s*"""
    rf"""["']?{RUNTIME_SECRET_KIND}["']?"""
    rf"""\s*,?\s*(?:\#.*)?$"""
    rf"""|[{{,]\s*["']?kind["']?\s*[:=]\s*"""
    rf"""["']?{RUNTIME_SECRET_KIND}["']?(?=\s*[,}}]))""",
    re.IGNORECASE | re.MULTILINE | re.VERBOSE,
)
RUNTIME_POSTGRES_CREDENTIAL_NAME = (
    r"(?:PG(?:HOST|PORT|DATABASE|USER|PASSWORD|PASSFILE|SERVICE|SERVICEFILE)"
    r"|POSTGRES(?:QL)?_(?:HOST|PORT|DB|DATABASE|USER|PASSWORD)"
    r"|DATABASE_URL)"
)
RUNTIME_POSTGRES_CREDENTIAL = re.compile(
    rf"""(?:^"""
    rf"""\s*(?:-\s*)?["']?{RUNTIME_POSTGRES_CREDENTIAL_NAME}["']?\s*[:=]"""
    rf"""|[{{,]\s*["']?{RUNTIME_POSTGRES_CREDENTIAL_NAME}["']?\s*[:=]"""
    rf"""|^\s*(?:-\s*)?["']?name["']?\s*[:=]\s*"""
    rf"""["']?{RUNTIME_POSTGRES_CREDENTIAL_NAME}["']?\s*,?\s*(?:\#.*)?$"""
    rf"""|[{{,]\s*["']?name["']?\s*[:=]\s*"""
    rf"""["']?{RUNTIME_POSTGRES_CREDENTIAL_NAME}["']?(?=\s*[,}}])"""
    rf"""|^\s*(?:(?:-\s*)|(?:(?:export|env)\s+))?["']?"""
    rf"""{RUNTIME_POSTGRES_CREDENTIAL_NAME}\s*="""
    rf"""|[\[{{,]\s*["']?{RUNTIME_POSTGRES_CREDENTIAL_NAME}\s*=)""",
    re.IGNORECASE | re.MULTILINE | re.VERBOSE,
)
RUNTIME_CATALOG_MARKER = re.compile(
    r"(?:catalog|iceberg|metastore)",
    re.IGNORECASE,
)
RUNTIME_OPENTOFU_RESOURCE = re.compile(
    r'^[ \t]*resource[ \t\r\n]+"'
    r'((?:\\.|[^"\\\r\n])*)"[ \t\r\n]+"'
    r'[^"\r\n]+"[ \t\r\n]*\{',
    re.IGNORECASE | re.MULTILINE,
)


class ContractError(RuntimeError):
    """Stable contract error surfaced to tests and CI."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _fail(code: str, detail: str) -> None:
    raise ContractError(code, detail)


def _expect(condition: bool, code: str, detail: str) -> None:
    if not condition:
        _fail(code, detail)


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _load_json(root: Path, relative: Path, code: str) -> Mapping[str, Any]:
    path = root / relative
    _expect(path.is_file(), code, f"missing contract file: {relative}")
    _expect(not path.is_symlink(), code, f"symlink contract file is forbidden: {relative}")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
        )
    except (OSError, UnicodeError, ValueError) as error:
        _fail(code, f"cannot read {relative}: {error}")
    _expect(isinstance(value, dict), code, f"{relative} must be a JSON object")
    return value


def _nested(value: Mapping[str, Any], *path: str) -> Any:
    current: Any = value
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _expect_fields(
    value: Mapping[str, Any],
    expected: Mapping[tuple[str, ...], Any],
    code: str,
) -> None:
    for path, wanted in expected.items():
        actual = _nested(value, *path)
        _expect(
            actual == wanted,
            code,
            f"{'.'.join(path)} must be {wanted!r}, found {actual!r}",
        )


def _expect_keysets(
    value: Mapping[str, Any],
    expected: Mapping[tuple[str, ...], set[str]],
    code: str,
) -> None:
    for path, wanted in expected.items():
        current: Any = value if not path else _nested(value, *path)
        _expect(
            isinstance(current, dict),
            code,
            f"{'.'.join(path) or '<root>'} must be an object",
        )
        actual = set(current)
        _expect(
            actual == wanted,
            code,
            f"{'.'.join(path) or '<root>'} keys must be "
            f"{sorted(wanted)}, found {sorted(actual)}",
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        _fail("KEY", f"cannot read {path}: {error}")
    return digest.hexdigest()


def _decode_ascii_armor(text: str) -> bytes:
    lines = text.splitlines()
    encoded: list[str] = []
    inside = False
    for line in lines:
        if line == "-----BEGIN PGP PUBLIC KEY BLOCK-----":
            inside = True
            continue
        if line == "-----END PGP PUBLIC KEY BLOCK-----":
            break
        if not inside or not line or ":" in line or line.startswith("="):
            continue
        encoded.append(line)
    _expect(bool(encoded), "KEY", "retained signing key is not valid ASCII armor")
    try:
        return base64.b64decode("".join(encoded), validate=True)
    except ValueError as error:
        _fail("KEY", f"retained signing key has invalid base64: {error}")


def _first_packet(data: bytes) -> tuple[int, bytes]:
    _expect(bool(data), "KEY", "retained signing key packet is empty")
    first = data[0]
    _expect(bool(first & 0x80), "KEY", "invalid OpenPGP packet header")
    offset = 1
    if first & 0x40:
        tag = first & 0x3F
        _expect(offset < len(data), "KEY", "truncated OpenPGP packet length")
        marker = data[offset]
        offset += 1
        if marker < 192:
            size = marker
        elif marker < 224:
            _expect(offset < len(data), "KEY", "truncated OpenPGP packet length")
            size = ((marker - 192) << 8) + data[offset] + 192
            offset += 1
        elif marker == 255:
            _expect(offset + 4 <= len(data), "KEY", "truncated OpenPGP packet")
            size = int.from_bytes(data[offset : offset + 4], "big")
            offset += 4
        else:
            _fail("KEY", "partial OpenPGP packet lengths are not accepted")
    else:
        tag = (first >> 2) & 0x0F
        length_type = first & 0x03
        sizes = {0: 1, 1: 2, 2: 4}
        _expect(length_type in sizes, "KEY", "indeterminate OpenPGP key packet")
        width = sizes[length_type]
        _expect(offset + width <= len(data), "KEY", "truncated OpenPGP packet")
        size = int.from_bytes(data[offset : offset + width], "big")
        offset += width
    _expect(offset + size <= len(data), "KEY", "truncated OpenPGP key body")
    return tag, data[offset : offset + size]


def _audit_key(root: Path, source: Mapping[str, Any]) -> None:
    key_path = root / POLARIS_KEY
    _expect(key_path.is_file(), "KEY", f"missing retained key: {POLARIS_KEY}")
    _expect(not key_path.is_symlink(), "KEY", "retained signing key cannot be a symlink")
    _expect(
        _sha256(key_path) == POLARIS_KEY_SHA256,
        "KEY",
        "retained signing key SHA-256 changed",
    )
    try:
        text = key_path.read_text(encoding="ascii")
    except (OSError, UnicodeError) as error:
        _fail("KEY", f"cannot read retained signing key: {error}")
    packet_tag, body = _first_packet(_decode_ascii_armor(text))
    _expect(packet_tag == 6, "KEY", "first retained packet must be a public key")
    _expect(body[:1] == b"\x04", "KEY", "retained key must be OpenPGP v4")
    fingerprint = hashlib.sha1(
        b"\x99" + len(body).to_bytes(2, "big") + body
    ).hexdigest().upper()
    _expect(
        fingerprint == POLARIS_KEY_FINGERPRINT,
        "KEY",
        f"retained key fingerprint changed to {fingerprint}",
    )
    _expect(
        POLARIS_KEY_UID.encode("utf-8") in _decode_ascii_armor(text),
        "KEY",
        "retained key UID is missing",
    )
    _expect_fields(
        source,
        {
            ("source_release", "signing_key", "path"): POLARIS_KEY.as_posix(),
            ("source_release", "signing_key", "sha256"): POLARIS_KEY_SHA256,
            (
                "source_release",
                "signing_key",
                "fingerprint",
            ): POLARIS_KEY_FINGERPRINT,
            ("source_release", "signing_key", "key_id"): "B8960FF52D9B1312",
            ("source_release", "signing_key", "uid"): POLARIS_KEY_UID,
        },
        "KEY",
    )


def _audit_source(root: Path) -> Mapping[str, Any]:
    source = _load_json(root, POLARIS_SOURCE, "SOURCE_PIN")
    _expect_keysets(
        source,
        {
            (): {
                "schema_version",
                "component",
                "version",
                "source_release",
                "git_release",
                "upstream_build_inputs",
                "selected_base_candidates",
                "assessment",
            },
            ("source_release",): {
                "archive_url",
                "signature_url",
                "checksum_url",
                "archive_sha512",
                "signature_sha256",
                "signing_key",
            },
            ("source_release", "signing_key"): {
                "path",
                "sha256",
                "fingerprint",
                "key_id",
                "uid",
            },
            ("git_release",): {
                "repository",
                "tag",
                "tag_object",
                "commit",
                "tree",
            },
            ("upstream_build_inputs",): {
                "java_version",
                "gradle_version",
                "gradle_distribution_url",
                "gradle_distribution_sha256",
                "wrapper_jar_present",
                "dependency_lockfiles_present",
                "dependency_verification_metadata_present",
                "server_tasks",
            },
            ("selected_base_candidates",): {
                "builder_index",
                "builder_arm64_manifest",
                "runtime_index",
                "runtime_arm64_manifest",
            },
            ("assessment",): {
                "state",
                "source_signature",
                "source_checksum",
                "authoritative_for_build",
                "closed_build_input",
                "reason",
            },
        },
        "SOURCE_PIN",
    )
    _expect_fields(
        source,
        {
            ("schema_version",): 1,
            ("component",): "polaris",
            ("version",): POLARIS_VERSION,
            (
                "source_release",
                "archive_url",
            ): "https://downloads.apache.org/polaris/1.6.0/apache-polaris-1.6.0.tar.gz",
            (
                "source_release",
                "signature_url",
            ): "https://downloads.apache.org/polaris/1.6.0/apache-polaris-1.6.0.tar.gz.asc",
            (
                "source_release",
                "checksum_url",
            ): "https://downloads.apache.org/polaris/1.6.0/apache-polaris-1.6.0.tar.gz.sha512",
            ("source_release", "archive_sha512"): POLARIS_ARCHIVE_SHA512,
            ("source_release", "signature_sha256"): POLARIS_SIGNATURE_SHA256,
            ("git_release", "repository"): "https://github.com/apache/polaris",
            ("git_release", "tag"): "apache-polaris-1.6.0",
            (
                "git_release",
                "tag_object",
            ): "8e82f4760aabe9fdc142710b504fe042ff8171d4",
            ("git_release", "commit"): POLARIS_COMMIT,
            ("git_release", "tree"): POLARIS_TREE,
            ("upstream_build_inputs", "java_version"): "21",
            ("upstream_build_inputs", "gradle_version"): "9.6.0",
            (
                "upstream_build_inputs",
                "gradle_distribution_url",
            ): "https://services.gradle.org/distributions/gradle-9.6.0-all.zip",
            (
                "upstream_build_inputs",
                "gradle_distribution_sha256",
            ): GRADLE_DISTRIBUTION_SHA256,
            ("upstream_build_inputs", "wrapper_jar_present"): False,
            (
                "upstream_build_inputs",
                "dependency_lockfiles_present",
            ): False,
            (
                "upstream_build_inputs",
                "dependency_verification_metadata_present",
            ): False,
            ("selected_base_candidates", "builder_index"): BUILDER_INDEX,
            (
                "selected_base_candidates",
                "builder_arm64_manifest",
            ): BUILDER_ARM64,
            ("selected_base_candidates", "runtime_index"): RUNTIME_INDEX,
            (
                "selected_base_candidates",
                "runtime_arm64_manifest",
            ): RUNTIME_ARM64,
            ("assessment", "state"): "dependency_closure_pending",
            (
                "assessment",
                "source_signature",
            ): "observed_verified_not_retained",
            (
                "assessment",
                "source_checksum",
            ): "observed_verified_not_retained",
            ("assessment", "authoritative_for_build"): False,
            ("assessment", "closed_build_input"): False,
        },
        "SOURCE_PIN",
    )
    tasks = _nested(source, "upstream_build_inputs", "server_tasks")
    _expect(
        tasks
        == [
            ":polaris-server:assemble",
            ":polaris-server:quarkusAppPartsBuild",
        ],
        "SOURCE_PIN",
        "server task closure changed",
    )
    _audit_key(root, source)
    return source


def _audit_contract(root: Path) -> Mapping[str, Any]:
    contract = _load_json(root, POLARIS_CONTRACT, "CONTRACT_STATE")
    _expect_keysets(
        contract,
        {
            (): {
                "schema_version",
                "component",
                "version",
                "platform",
                "contract_state",
                "source_record",
                "image",
                "build",
                "publication",
                "runtime",
                "required_before_build_enablement",
                "required_before_runtime_enablement",
                "next_action",
            },
            ("image",): {"repository", "trusted_tag", "trusted_tag_role"},
            ("build",): {
                "enabled",
                "containerfile",
                "dependency_snapshot",
                "network_policy_after_closure",
                "cache_policy",
                "tasks",
            },
            ("build", "dependency_snapshot"): {
                "descriptor",
                "artifact_repository",
                "artifact_reference",
                "transport",
                "generation_workflow",
            },
            ("publication",): {
                "enabled",
                "workflow",
                "allowed_refs",
                "lifecycle",
            },
            ("runtime",): {"enabled", "admission_record", "atomic_peer"},
        },
        "CONTRACT_STATE",
    )
    _expect_fields(
        contract,
        {
            ("schema_version",): 1,
            ("component",): "polaris",
            ("version",): POLARIS_VERSION,
            ("platform",): "linux/arm64",
            ("contract_state",): "dependency_closure_pending",
            ("source_record",): POLARIS_SOURCE.as_posix(),
            (
                "image",
                "repository",
            ): "ghcr.io/tommykammy/shirokuma-polaris",
            ("image", "trusted_tag"): "1.6.0-arm64",
            ("image", "trusted_tag_role"): "non_authoritative_pointer",
            ("build", "enabled"): False,
            (
                "build",
                "containerfile",
            ): "bootstrap/polaris/v1.6.0/Containerfile",
            (
                "build",
                "dependency_snapshot",
                "descriptor",
            ): "bootstrap/polaris/v1.6.0/gradle-dependency-inputs.json",
            (
                "build",
                "dependency_snapshot",
                "artifact_repository",
            ): "ghcr.io/tommykammy/shirokuma-polaris-gradle-dependencies",
            ("build", "dependency_snapshot", "artifact_reference"): None,
            ("build", "dependency_snapshot", "transport"): "oci-artifact",
            (
                "build",
                "dependency_snapshot",
                "generation_workflow",
            ): ".github/workflows/polaris-gradle-dependencies.yml",
            ("build", "network_policy_after_closure"): "offline",
            ("build", "cache_policy"): "no-shared-cache",
            ("publication", "enabled"): False,
            ("publication", "workflow"): ".github/workflows/polaris-arm64.yml",
            ("publication", "allowed_refs"): ["refs/heads/main"],
            (
                "publication",
                "lifecycle",
            ): "quarantine-verify-promote-evidence-only-admission",
            ("runtime", "enabled"): False,
            ("runtime", "admission_record"): POLARIS_ADMISSION.as_posix(),
            ("runtime", "atomic_peer"): POSTGRES_ADMISSION.as_posix(),
            (
                "next_action",
            ): "publish-and-review-gradle-dependency-snapshot",
        },
        "CONTRACT_STATE",
    )
    _expect(
        _nested(contract, "build", "tasks")
        == [
            ":polaris-server:assemble",
            ":polaris-server:quarkusAppPartsBuild",
        ],
        "CONTRACT_STATE",
        "build task closure changed",
    )
    _expect(
        contract.get("required_before_build_enablement")
        == BUILD_ENABLEMENT_REQUIREMENTS,
        "CONTRACT_STATE",
        "build enablement requirements changed",
    )
    _expect(
        contract.get("required_before_runtime_enablement")
        == RUNTIME_ENABLEMENT_REQUIREMENTS,
        "CONTRACT_STATE",
        "runtime enablement requirements changed",
    )
    return contract


def _audit_polaris_admission(root: Path) -> Mapping[str, Any]:
    admission = _load_json(root, POLARIS_ADMISSION, "POLARIS_ADMISSION")
    _expect_keysets(
        admission,
        {
            (): {
                "schema_version",
                "component",
                "version",
                "platform",
                "admission",
                "state",
                "source_record",
                "source_record_sha256",
                "build_contract",
                "build_contract_sha256",
                "upstream_image_assessment",
                "planned_candidate",
                "resident_ledger",
                "runtime_manifests",
                "blockers",
                "next_action",
            },
            ("upstream_image_assessment",): {
                "reference",
                "admission",
                "reason",
            },
            ("planned_candidate",): {
                "repository",
                "reference",
                "release_evidence",
            },
            ("resident_ledger",): {"permitted", "atomic_with"},
            ("runtime_manifests",): {"permitted", "forbidden_roots"},
        },
        "POLARIS_ADMISSION",
    )
    _expect_fields(
        admission,
        {
            ("schema_version",): 1,
            ("component",): "polaris",
            ("version",): POLARIS_VERSION,
            ("platform",): "linux/arm64",
            ("admission",): "blocked",
            ("state",): "dependency_closure_pending",
            ("source_record",): POLARIS_SOURCE.as_posix(),
            ("source_record_sha256",): POLARIS_SOURCE_SHA256,
            ("build_contract",): POLARIS_CONTRACT.as_posix(),
            ("build_contract_sha256",): POLARIS_CONTRACT_SHA256,
            ("upstream_image_assessment", "admission"): "rejected",
            (
                "upstream_image_assessment",
                "reference",
            ): "apache/polaris@sha256:9738b2052dea20aabf0cd42521424ff963fee41b0ee888fef9f512efb256602a",
            (
                "planned_candidate",
                "repository",
            ): "ghcr.io/tommykammy/shirokuma-polaris",
            ("planned_candidate", "reference"): None,
            ("planned_candidate", "release_evidence"): None,
            ("resident_ledger", "permitted"): False,
            ("resident_ledger", "atomic_with"): "postgresql",
            ("runtime_manifests", "permitted"): False,
            (
                "runtime_manifests",
                "forbidden_roots",
            ): ["deploy", "charts", "opentofu"],
            (
                "next_action",
            ): "publish-and-review-gradle-snapshot-before-image-publication",
        },
        "POLARIS_ADMISSION",
    )
    _expect(
        admission.get("blockers") == POLARIS_BLOCKERS,
        "POLARIS_ADMISSION",
        "all four pre-publication blockers must remain explicit",
    )
    _expect(
        _sha256(root / POLARIS_SOURCE) == POLARIS_SOURCE_SHA256,
        "POLARIS_ADMISSION",
        "source record bytes do not match the admitted SHA-256",
    )
    _expect(
        _sha256(root / POLARIS_CONTRACT) == POLARIS_CONTRACT_SHA256,
        "POLARIS_ADMISSION",
        "build contract bytes do not match the admitted SHA-256",
    )
    return admission


def _audit_postgres_admission(root: Path) -> Mapping[str, Any]:
    admission = _load_json(root, POSTGRES_ADMISSION, "POSTGRES_ADMISSION")
    _expect_keysets(
        admission,
        {
            (): {
                "schema_version",
                "component",
                "version",
                "platform",
                "admission",
                "state",
                "source",
                "candidate",
                "observation",
                "evidence_contract",
                "resident_ledger",
                "runtime_manifests",
                "next_action",
            },
            ("candidate",): {
                "index_reference",
                "arm64_reference",
                "attestation_manifest_digest",
                "issuer",
                "identity",
                "workflow_commit",
                "transparency_log_index",
                "availability_preflight_required",
            },
            ("observation",): {
                "observed_at",
                "signature",
                "arm64_index_membership",
                "slsa_provenance",
                "upstream_spdx",
                "upstream_spdx_package_count",
                "trivy_version",
                "vulnerability_db_updated_at",
                "high",
                "critical",
                "authoritative_for_admission",
            },
            ("evidence_contract",): {
                "paths",
                "signature",
                "provenance",
                "upstream_sbom",
                "independent_sbom",
                "vulnerability_scan",
                "cryptographic_reverification",
            },
            ("evidence_contract", "paths"): {
                "index_manifest",
                "arm64_manifest",
                "signature_bundle",
                "attestation_bundles",
                "cyclonedx_sbom",
                "trivy_report",
                "verification",
            },
            ("evidence_contract", "signature"): {
                "issuer",
                "identity",
                "transparency_log_index",
            },
            ("evidence_contract", "provenance"): {
                "predicate_type",
                "subject_reference",
                "builder",
                "build_type",
                "revision",
            },
            ("evidence_contract", "upstream_sbom"): {
                "predicate_type",
                "subject_reference",
                "package_count",
            },
            ("evidence_contract", "independent_sbom"): {"format", "generator"},
            ("evidence_contract", "vulnerability_scan"): {
                "scanner",
                "severity",
                "maximum_high",
                "maximum_critical",
                "fresh_database_required",
            },
            ("evidence_contract", "cryptographic_reverification"): {
                "cosign",
                "offline_retained_bundle_verification",
            },
            ("resident_ledger",): {"permitted", "atomic_with"},
            ("runtime_manifests",): {"permitted"},
        },
        "POSTGRES_ADMISSION",
    )
    _expect_fields(
        admission,
        {
            ("schema_version",): 1,
            ("component",): "postgresql",
            ("version",): "18.4",
            ("platform",): "linux/arm64",
            ("admission",): "blocked",
            ("state",): "candidate_evidence_pending",
            (
                "source",
            ): "https://github.com/chainguard-images/images/tree/main/images/postgres",
            ("candidate", "index_reference"): POSTGRES_INDEX,
            ("candidate", "arm64_reference"): POSTGRES_ARM64,
            (
                "candidate",
                "attestation_manifest_digest",
            ): POSTGRES_ATTESTATION,
            (
                "candidate",
                "issuer",
            ): "https://token.actions.githubusercontent.com",
            (
                "candidate",
                "identity",
            ): "https://github.com/chainguard-images/images/.github/workflows/release.yaml@refs/heads/main",
            (
                "candidate",
                "workflow_commit",
            ): "704e38b436bc40bc9a9d669c05f0d6694bec298b",
            ("candidate", "transparency_log_index"): 2181460214,
            ("candidate", "availability_preflight_required"): True,
            ("observation", "observed_at"): "2026-07-18",
            ("observation", "signature"): "verified-but-not-retained",
            (
                "observation",
                "arm64_index_membership",
            ): "verified-but-not-retained",
            ("observation", "slsa_provenance"): "verified-but-not-retained",
            ("observation", "upstream_spdx"): "verified-but-not-retained",
            ("observation", "upstream_spdx_package_count"): 257,
            ("observation", "trivy_version"): "0.72.0",
            (
                "observation",
                "vulnerability_db_updated_at",
            ): "2026-07-18T07:19:07.874814014Z",
            ("observation", "authoritative_for_admission"): False,
            ("observation", "high"): 0,
            ("observation", "critical"): 0,
            (
                "evidence_contract",
                "paths",
                "index_manifest",
            ): "bootstrap/postgresql/v18.4/evidence/index-manifest.json",
            (
                "evidence_contract",
                "paths",
                "arm64_manifest",
            ): "bootstrap/postgresql/v18.4/evidence/arm64-manifest.json",
            (
                "evidence_contract",
                "paths",
                "signature_bundle",
            ): "bootstrap/postgresql/v18.4/evidence/cosign-signature-bundle.json",
            (
                "evidence_contract",
                "paths",
                "attestation_bundles",
            ): "bootstrap/postgresql/v18.4/evidence/attestation-bundles.jsonl",
            (
                "evidence_contract",
                "paths",
                "cyclonedx_sbom",
            ): "bootstrap/postgresql/v18.4/evidence/postgresql-18.4-arm64.cdx.json",
            (
                "evidence_contract",
                "paths",
                "trivy_report",
            ): "bootstrap/postgresql/v18.4/evidence/trivy.json",
            (
                "evidence_contract",
                "paths",
                "verification",
            ): "bootstrap/postgresql/v18.4/evidence/cryptographic-verification.json",
            (
                "evidence_contract",
                "signature",
                "issuer",
            ): "https://token.actions.githubusercontent.com",
            (
                "evidence_contract",
                "signature",
                "identity",
            ): "https://github.com/chainguard-images/images/.github/workflows/release.yaml@refs/heads/main",
            (
                "evidence_contract",
                "signature",
                "transparency_log_index",
            ): 2181460214,
            (
                "evidence_contract",
                "provenance",
                "predicate_type",
            ): "https://slsa.dev/provenance/v1",
            (
                "evidence_contract",
                "provenance",
                "subject_reference",
            ): POSTGRES_ARM64,
            (
                "evidence_contract",
                "provenance",
                "builder",
            ): "https://github.com/chainguard-dev/terraform-provider-apko",
            (
                "evidence_contract",
                "provenance",
                "build_type",
            ): "https://apko.dev/slsa-build-type@v1",
            (
                "evidence_contract",
                "provenance",
                "revision",
            ): "704e38b436bc40bc9a9d669c05f0d6694bec298b",
            (
                "evidence_contract",
                "upstream_sbom",
                "predicate_type",
            ): "https://spdx.dev/Document",
            (
                "evidence_contract",
                "upstream_sbom",
                "subject_reference",
            ): POSTGRES_ARM64,
            (
                "evidence_contract",
                "upstream_sbom",
                "package_count",
            ): 257,
            (
                "evidence_contract",
                "independent_sbom",
                "format",
            ): "CycloneDX 1.7",
            (
                "evidence_contract",
                "independent_sbom",
                "generator",
            ): "syft 1.46.0",
            (
                "evidence_contract",
                "vulnerability_scan",
                "scanner",
            ): "trivy 0.72.0",
            (
                "evidence_contract",
                "vulnerability_scan",
                "severity",
            ): ["HIGH", "CRITICAL"],
            (
                "evidence_contract",
                "vulnerability_scan",
                "maximum_high",
            ): 0,
            (
                "evidence_contract",
                "vulnerability_scan",
                "maximum_critical",
            ): 0,
            (
                "evidence_contract",
                "vulnerability_scan",
                "fresh_database_required",
            ): True,
            (
                "evidence_contract",
                "cryptographic_reverification",
                "cosign",
            ): "3.1.1",
            (
                "evidence_contract",
                "cryptographic_reverification",
                "offline_retained_bundle_verification",
            ): True,
            ("resident_ledger", "permitted"): False,
            ("resident_ledger", "atomic_with"): "polaris",
            ("runtime_manifests", "permitted"): False,
            (
                "next_action",
            ): "retain-and-reverify-evidence-after-polaris-main-publication",
        },
        "POSTGRES_ADMISSION",
    )
    return admission


def _audit_pending_files(root: Path) -> None:
    for relative in FORBIDDEN_PENDING_PATHS:
        _expect(
            not (root / relative).exists(),
            "FORBIDDEN_PATH",
            f"{relative} must remain absent while dependency closure is pending",
        )
    for relative_root, allowed in (
        (Path("bootstrap/polaris/v1.6.0"), POLARIS_ALLOWED_PATHS),
        (Path("bootstrap/postgresql/v18.4"), POSTGRES_ALLOWED_PATHS),
    ):
        directory = root / relative_root
        _expect(
            directory.is_dir() and not directory.is_symlink(),
            "FORBIDDEN_PATH",
            f"invalid static contract root: {relative_root}",
        )
        actual: set[str] = set()
        for path in directory.rglob("*"):
            _expect(
                not path.is_symlink(),
                "FORBIDDEN_PATH",
                f"symlink is forbidden in static contract: {path.relative_to(root)}",
            )
            actual.add(path.relative_to(directory).as_posix())
        _expect(
            actual == allowed,
            "FORBIDDEN_PATH",
            f"{relative_root} paths must be {sorted(allowed)}, "
            f"found {sorted(actual)}",
        )
    workflow_root = root / ".github/workflows"
    workflow_inventory: dict[str, str] = {}
    if workflow_root.is_dir():
        for workflow in workflow_root.rglob("*"):
            if not workflow.is_file() or workflow.suffix.lower() not in {
                ".yaml",
                ".yml",
            }:
                continue
            _expect(
                not workflow.is_symlink(),
                "FORBIDDEN_PATH",
                f"workflow symlink is forbidden: {workflow.relative_to(root)}",
            )
            relative_workflow = workflow.relative_to(root).as_posix()
            workflow_inventory[relative_workflow] = _sha256(workflow)
            try:
                content = workflow.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as error:
                _fail(
                    "FORBIDDEN_PATH",
                    f"cannot inspect workflow {workflow.relative_to(root)}: {error}",
                )
            normalized = content.lower()
            _expect(
                "polaris" not in normalized
                and "shirokuma-polaris" not in normalized
                and "bootstrap/polaris" not in normalized,
                "FORBIDDEN_PATH",
                "Polaris workflow must remain absent while dependency closure "
                f"is pending: {workflow.relative_to(root)}",
            )
    _expect(
        workflow_inventory == PENDING_WORKFLOW_INVENTORY,
        "FORBIDDEN_PATH",
        "workflow inventory changed while Polaris dependency closure is pending; "
        f"expected {sorted(PENDING_WORKFLOW_INVENTORY)}, "
        f"found {sorted(workflow_inventory)}",
    )
    for evidence_root in (POLARIS_EVIDENCE, POSTGRES_EVIDENCE):
        directory = root / evidence_root
        _expect(
            directory.is_dir(),
            "FORBIDDEN_PATH",
            f"missing evidence checkpoint directory: {evidence_root}",
        )
        _expect(
            not directory.is_symlink(),
            "FORBIDDEN_PATH",
            f"evidence checkpoint directory cannot be a symlink: {evidence_root}",
        )
        retained = sorted(
            path.relative_to(directory).as_posix()
            for path in directory.rglob("*")
            if path.is_file()
        )
        _expect(
            retained == ["README.md"],
            "FORBIDDEN_PATH",
            f"{evidence_root} may contain only README.md while admission is blocked",
        )
        _expect(
            not (directory / "README.md").is_symlink(),
            "FORBIDDEN_PATH",
            f"{evidence_root}/README.md cannot be a symlink",
        )


def _audit_ledger(root: Path) -> None:
    ledger = _load_json(root, RESIDENT_LEDGER, "LEDGER_BLOCK")
    images = ledger.get("images")
    _expect(isinstance(images, list), "LEDGER_BLOCK", "ledger images must be a list")
    aliases = {
        "apachepolaris",
        "chainguardpostgres",
        "polaris",
        "postgres",
        "postgresql",
    }
    reference_markers = {
        "apache/polaris",
        "c455ec159d05d99ee031d471b8692668562fed8e8c9c37be5e0dbdbee8e5f7b8",
        "cgr.dev/chainguard/postgres",
        "ghcr.io/tommykammy/shirokuma-polaris",
        "shirokuma-polaris",
    }
    blocked: list[str] = []
    for index, entry in enumerate(images):
        _expect(
            isinstance(entry, dict),
            "LEDGER_BLOCK",
            f"ledger images[{index}] must be an object",
        )
        component = str(entry.get("component", ""))
        normalized_component = re.sub(r"[^a-z0-9]", "", component.lower())
        serialized = json.dumps(entry, sort_keys=True).lower()
        identity = " ".join(
            str(entry.get(field, ""))
            for field in ("component", "reference", "source")
        ).lower()
        catalog_identity = any(
            marker in str(entry.get(field, "")).lower()
            for field in ("component", "reference", "source")
            for marker in CATALOG_IDENTITY_MARKERS
        )
        if (
            normalized_component in aliases
            or RUNTIME_IDENTITY.search(identity)
            or catalog_identity
            or any(marker in serialized for marker in reference_markers)
        ):
            blocked.append(component or "<unnamed>")
    blocked.sort()
    _expect(
        not blocked,
        "LEDGER_BLOCK",
        f"pending catalog images cannot enter the resident ledger: {blocked}",
    )


def _runtime_files(root: Path) -> Iterable[Path]:
    for relative_root in RUNTIME_ROOTS:
        candidate = root / relative_root
        if not candidate.exists():
            continue
        for path in candidate.rglob("*"):
            candidate_relative = path.relative_to(candidate)
            if any(part in RUNTIME_GENERATED_DIRS for part in candidate_relative.parts):
                continue
            if path.is_symlink() or path.is_file():
                yield path


def _hcl_heredoc_end(relative: str, text: str, start: int) -> int | None:
    opener = re.match(
        r"<<(-?)([^\s]+)[ \t]*\r?\n",
        text[start:],
    )
    if opener is None:
        return None

    delimiter = re.escape(opener.group(2))
    indent = r"[ \t]*" if opener.group(1) else ""
    body_start = start + opener.end()
    closer = re.search(
        rf"(?m)^{indent}{delimiter}[ \t]*\r?$",
        text[body_start:],
    )
    if closer is None:
        _fail(
            "RUNTIME_BLOCK",
            f"unterminated heredoc in OpenTofu configuration {relative}",
        )
    return body_start + closer.end()


def _scan_hcl_template_expression(relative: str, text: str, start: int) -> int:
    index = start
    length = len(text)
    depth = 1
    while index < length:
        if text[index] == '"':
            index = _scan_hcl_quoted_string(relative, text, index)
            continue
        if text[index] == "#" or text.startswith("//", index):
            end = text.find("\n", index)
            index = length if end == -1 else end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end == -1:
                _fail(
                    "RUNTIME_BLOCK",
                    f"unterminated block comment in OpenTofu configuration {relative}",
                )
            index = end + 2
            continue
        if text.startswith("<<", index):
            heredoc_end = _hcl_heredoc_end(relative, text, index)
            if heredoc_end is not None:
                index = heredoc_end
                continue
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1

    _fail(
        "RUNTIME_BLOCK",
        f"unterminated template expression in OpenTofu configuration {relative}",
    )


def _scan_hcl_quoted_string(relative: str, text: str, start: int) -> int:
    index = start + 1
    length = len(text)
    while index < length:
        if text[index] == "\\":
            if index + 1 >= length:
                break
            index += 2
            continue
        if text.startswith(("$${", "%%{"), index):
            index += 3
            continue
        if text.startswith(("${", "%{"), index):
            index = _scan_hcl_template_expression(relative, text, index + 2)
            continue
        if text[index] == '"':
            return index + 1
        if text[index] in "\r\n":
            _fail(
                "RUNTIME_BLOCK",
                f"newline in quoted string in OpenTofu configuration {relative}",
            )
        index += 1

    _fail(
        "RUNTIME_BLOCK",
        f"unterminated quoted string in OpenTofu configuration {relative}",
    )


def _mask_hcl_non_code(relative: str, text: str) -> str:
    masked = list(text)
    length = len(text)

    def mask(start: int, end: int) -> None:
        for index in range(start, end):
            if masked[index] not in "\r\n":
                masked[index] = " "

    index = 0
    while index < length:
        if text[index] == '"':
            index = _scan_hcl_quoted_string(relative, text, index)
            continue
        if text[index] == "#" or text.startswith("//", index):
            end = text.find("\n", index)
            if end == -1:
                end = length
            mask(index, end)
            index = end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end == -1:
                _fail(
                    "RUNTIME_BLOCK",
                    f"unterminated block comment in OpenTofu configuration {relative}",
                )
            end += 2
            mask(index, end)
            index = end
            continue
        if text.startswith("<<", index):
            heredoc_end = _hcl_heredoc_end(relative, text, index)
            if heredoc_end is not None:
                mask(index, heredoc_end)
                index = heredoc_end
                continue
        index += 1

    return "".join(masked)


def _decode_hcl_label(relative: str, raw: str) -> str:
    def replace_long_unicode_escape(match: re.Match[str]) -> str:
        try:
            return chr(int(match.group(1), 16))
        except (ValueError, OverflowError):
            _fail(
                "RUNTIME_BLOCK",
                f"invalid Unicode escape in OpenTofu configuration {relative}",
            )

    expanded = re.sub(r"\\U([0-9A-Fa-f]{8})", replace_long_unicode_escape, raw)
    try:
        decoded = json.loads(f'"{expanded}"')
    except json.JSONDecodeError as error:
        _fail(
            "RUNTIME_BLOCK",
            f"cannot decode OpenTofu resource label in {relative}: {error}",
        )
    _expect(
        isinstance(decoded, str),
        "RUNTIME_BLOCK",
        f"OpenTofu resource label must be a string in {relative}",
    )
    return decoded


def _has_opentofu_secret_resource(relative: str, text: str) -> bool:
    lowered = relative.lower()
    if lowered.endswith((".tf.json", ".tofu.json")):
        try:
            document = json.loads(text)
        except json.JSONDecodeError as error:
            _fail(
                "RUNTIME_BLOCK",
                f"cannot parse OpenTofu JSON configuration {relative}: {error}",
            )
        if not isinstance(document, dict):
            return False
        resources = document.get("resource")
        if not isinstance(resources, dict):
            return False
        return any(
            re.fullmatch(r"kubernetes_secret[a-z0-9_-]*", str(resource_type), re.I)
            for resource_type in resources
        )
    if lowered.endswith((".tf", ".tofu")):
        inspected = _mask_hcl_non_code(relative, text.removeprefix("\ufeff"))
        return any(
            re.fullmatch(
                r"kubernetes_secret[a-z0-9_-]*",
                _decode_hcl_label(relative, match.group(1)),
                re.I,
            )
            for match in RUNTIME_OPENTOFU_RESOURCE.finditer(inspected)
        )
    return False


def _has_unapproved_catalog_marker(relative: str, text: str) -> bool:
    approved_line = APPROVED_RUNTIME_CATALOG_LINES.get(relative)
    inspected = text
    if approved_line is not None:
        lines = text.splitlines()
        if lines.count(approved_line) == 1:
            inspected = "\n".join(line for line in lines if line != approved_line)
    return bool(RUNTIME_CATALOG_MARKER.search(inspected))


def _audit_runtime_absence(root: Path) -> None:
    matches: list[str] = []
    for path in _runtime_files(root):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            matches.append(relative)
            continue
        if (
            RUNTIME_IDENTITY.search(relative)
            or RUNTIME_CONTEXT_PATH.search(relative)
            or RUNTIME_CREDENTIAL_PATH.search(relative)
        ):
            matches.append(relative)
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            _fail(
                "RUNTIME_BLOCK",
                f"cannot inspect runtime-root file as UTF-8 text: {path}: {error}",
            )
        unapproved_opentofu_secret = (
            relative.startswith("opentofu/")
            and _has_opentofu_secret_resource(relative, text)
            and APPROVED_OPENTOFU_SECRET_FILES.get(relative) != _sha256(path)
        )
        if (
            RUNTIME_IDENTITY.search(text)
            or RUNTIME_SECRET_MANIFEST.search(text)
            or RUNTIME_POSTGRES_CREDENTIAL.search(text)
            or _has_unapproved_catalog_marker(relative, text)
            or unapproved_opentofu_secret
        ):
            matches.append(relative)
    _expect(
        not matches,
        "RUNTIME_BLOCK",
        "catalog runtime or credential manifests are forbidden while images are "
        f"pending: {sorted(matches)}",
    )


def audit(root: Path) -> None:
    root = root.resolve()
    _audit_source(root)
    _audit_contract(root)
    _audit_polaris_admission(root)
    _audit_postgres_admission(root)
    _audit_pending_files(root)
    _audit_ledger(root)
    _audit_runtime_absence(root)


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
        print(str(error), file=sys.stderr)
        return 1
    print(
        "polaris-trusted-image: pending contract is fail-closed; "
        "source and image observations remain non-authoritative"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
