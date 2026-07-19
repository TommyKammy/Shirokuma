#!/usr/bin/env python3
"""Fail-closed audit for the Polaris dependency-publication checkpoint."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


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
POLARIS_SOURCE_ARCHIVE_ROOT = f"apache-polaris-{POLARIS_VERSION}"
POLARIS_SOURCE_ARCHIVE_MAXIMUM_BYTES = 67_108_864
POLARIS_SOURCE_ARCHIVE_MAXIMUM_DECOMPRESSED_BYTES = 536_870_912
POLARIS_SOURCE_ARCHIVE_MAXIMUM_RAW_HEADERS = 20_000
POLARIS_SOURCE_ARCHIVE_MAXIMUM_TAR_CONTROL_BYTES = 4_096
POLARIS_SOURCE_ARCHIVE_MAXIMUM_MEMBERS = 10_000
POLARIS_SOURCE_ARCHIVE_MAXIMUM_MEMBER_BYTES = 67_108_864
POLARIS_SOURCE_ARCHIVE_MAXIMUM_TOTAL_FILE_BYTES = 268_435_456
POLARIS_SOURCE_ARCHIVE_MAXIMUM_PATH_BYTES = 1_024
POLARIS_SOURCE_ARCHIVE_MAXIMUM_PATH_COMPONENT_BYTES = 255
POLARIS_SOURCE_ARCHIVE_MAXIMUM_PATH_COMPONENTS = 64
POLARIS_SOURCE_ARCHIVE_MAXIMUM_LINK_BYTES = 1_024
POLARIS_SOURCE_ARCHIVE_MAXIMUM_PAX_BYTES = 4_096
POLARIS_SOURCE_ARCHIVE_ALLOWED_PAX_HEADERS = {"comment", "linkpath"}
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
    "255dab3f052572baf9d32ec4cbb7b0512b11b862384eb44104dbd21f6ffa53e6"
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
    Path(".github/workflows/polaris-arm64.yml"),
)
POLARIS_DEPENDENCY_WORKFLOW = Path(
    ".github/workflows/polaris-gradle-dependencies.yml"
)
POLARIS_DEPENDENCY_WORKFLOW_SHA256 = (
    "2a594cb49e8a2be4154395a6c30836f8ffc7fafd933cac41ad04574353af91d3"
)
POLARIS_DEPENDENCY_PACKAGER = Path(
    "scripts/package_polaris_gradle_dependencies.py"
)
POLARIS_DEPENDENCY_PACKAGER_SHA256 = (
    "fbbe803c7d1e52be02ba81f26f6f35fb0d6824fbe59cf3ab579e87c5488723ab"
)
POLARIS_SOURCE_ARCHIVE_VALIDATOR = Path(
    "scripts/validate_polaris_source_archive.py"
)
POLARIS_SOURCE_ARCHIVE_VALIDATOR_SHA256 = (
    "00ac3ec84bd9ff48914e0429f517eabbfc9380410740c2e626608bc036f8ebb9"
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
POLARIS_PENDING_PATHS = {"v1.6.0"} | {
    f"v1.6.0/{relative}" for relative in POLARIS_ALLOWED_PATHS
}
POSTGRES_PENDING_PATHS = {"v18.4"} | {
    f"v18.4/{relative}" for relative in POSTGRES_ALLOWED_PATHS
}
PENDING_BOOTSTRAP_NAMESPACES = {
    "polaris": "polaris",
    "postgres": "postgresql",
}
PENDING_BOOTSTRAP_ARTIFACT_MARKERS = {
    "admission",
    "attestation",
    "containerfile",
    "contract",
    "dependency",
    "dockerfile",
    "evidence",
    "gradle",
    "key",
    "provenance",
    "release",
    "sbom",
    "scan",
    "signature",
    "source",
}
PENDING_WORKFLOW_INVENTORY = {
    ".github/workflows/ci.yml": (
        "36666a76c07b428adda5fe71e4bd21643d05e66f56043dcef514101add63dd72"
    ),
    ".github/workflows/polaris-gradle-dependencies.yml": (
        POLARIS_DEPENDENCY_WORKFLOW_SHA256
    ),
    ".github/workflows/seaweedfs-arm64.yml": (
        "f097273d79c9595d42be816152ff1aabc862faf2667cb0648434280ce8b8ac06"
    ),
    ".github/workflows/security.yml": (
        "717c0fad0d108b271777ea2f61a69682fb57c2d8947b387c81276f095dd8176c"
    ),
}
PENDING_SCRIPT_FILE_INVENTORY = {
    "scripts/bound_evidence.py": (
        "a80bff20847cdf3410f2d1846e8511188a7bb4fd72a87503167cc8ec13285b72"
    ),
    "scripts/colima_baseline.sh": (
        "a28b2d328b4731ff1457acae7517bc38a9c04a88071caff5520d9570affa17c1"
    ),
    "scripts/object_storage_backup.py": (
        "f6f6624f6bb58ac77e05b3a447ea03f75e5d71c1da79af57719d2122f0216452"
    ),
    "scripts/object_storage_s3.py": (
        "4b7318016c85276886aa3769a0cbf06c166004109e08fb81279cad718c0db872"
    ),
    "scripts/object_storage_smoke.sh": (
        "4bba287743ddde6cd74b9d3f4f2c528ed6ca86e34e0a508bfc8135f901af5c3f"
    ),
    "scripts/package_polaris_gradle_dependencies.py": (
        POLARIS_DEPENDENCY_PACKAGER_SHA256
    ),
    "scripts/validate_polaris_source_archive.py": (
        POLARIS_SOURCE_ARCHIVE_VALIDATOR_SHA256
    ),
    "scripts/package_go_vendor.py": (
        "ff2da02c6f1927522ed0852beb0f6373f38c4bbaf0ac6597d9acefe1402ffec4"
    ),
    "scripts/preflight_supervisor_issues.py": (
        "fcd3f9ea30a8448ef53c1e37874f187cc5598f43f7c82fd24b11896ecfa9ac64"
    ),
    "scripts/verify_design_context.py": (
        "114590d9cc6e13d8d6006ef3549ea344227dab6a69aac14ed7521b1fc6866835"
    ),
    "scripts/verify_gitops_image_admission.py": (
        "48d35babd03c9283758d7fa7c0a14fde12d4908244fff8f0fd18e80631e62b1a"
    ),
    "scripts/verify_gitops_teardown.py": (
        "346624c428cdaff12dd58acec5e39acc7fefb569c273386336baffa24478d5af"
    ),
    "scripts/verify_policy_exceptions.py": (
        "6c15a5dd0d79029d941cbebf29bc32163a5788bb8d5e095dd9cceede6d84b862"
    ),
    "scripts/verify_repository_skeleton.py": (
        "b6bbbd383c74b190872bdcf144ede8126d8da5dbeb03e291027aaf276c62c955"
    ),
    "scripts/verify_supply_chain.py": (
        "480facf04d483314a930d91ca5ff7c238829bb5665af05e3351f816b17e504ed"
    ),
    "scripts/verify_trivyignore.py": (
        "75cee002d5749c0ec91629edb905c27362bee5c0813b0cbefcb59f161734f445"
    ),
    "scripts/verify_trusted_image.py": (
        "cc569a5ee10400ad657f7648ccc2c14e8fd21691adfdc9e155212b16dc0afba0"
    ),
}
PENDING_SCRIPT_SELF = "scripts/verify_polaris_trusted_image.py"
PENDING_SCRIPT_PATHS = set(PENDING_SCRIPT_FILE_INVENTORY) | {
    PENDING_SCRIPT_SELF
}
PENDING_CHART_PATHS = {"AGENTS.md", "README.md"}
POLARIS_BLOCKING_CONTROLS = [
    {"id": "POLARIS-DEP-SNAPSHOT-REVIEW", "state": "pending"},
    {"id": "POLARIS-MAIN-PUBLICATION", "state": "pending"},
    {"id": "POLARIS-IMAGE-EVIDENCE-REVIEW", "state": "pending"},
    {"id": "POLARIS-POSTGRES-ATOMIC-ADMISSION", "state": "pending"},
]
POLARIS_SERVER_TASKS = [
    ":polaris-server:assemble",
    ":polaris-server:quarkusAppPartsBuild",
]
POLARIS_DEPENDENCY_WORKFLOW_STEPS = {
    "resolve": [
        "Check out the reviewed dependency policy",
        "Check the dependency publication lifecycle",
        "Validate the publication-pending contract",
        "Fetch and authenticate the ASF source release",
        "Resolve dependencies and generate strict verification metadata",
        "Package the canonical dependency snapshot",
        "Prove a fresh network-none offline server build",
        "Record the dependency resolver toolchain",
        "Retain the verified candidate for publication",
    ],
    "verify": [
        "Check out the reviewed verification policy",
        "Download the exact candidate into the read-only verifier",
        "Reverify the publication candidate without write credentials",
    ],
    "publish": [
        "Check out the reviewed publication policy",
        "Download the exact read-only-verified candidate",
        "Bind candidate bytes to the read-only verification",
        "Install checksum-pinned ORAS",
        "Install pinned Cosign",
        "Publish the run-scoped immutable OCI artifact",
        "Bind the raw OCI manifest to both artifact layers",
        "Keyless-sign the exact OCI manifest",
        "Publish SLSA provenance for the exact OCI manifest",
        "Prove anonymous exact-digest retrieval",
        "Verify the exact workflow provenance",
        "Record the review-pending publication",
        "Retain review-pending publication evidence",
        "Remove registry credentials",
    ],
}
POLARIS_DEPENDENCY_WORKFLOW_ACTIONS = [
    (
        "resolve|Check out the reviewed dependency policy|"
        "actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10"
    ),
    (
        "resolve|Retain the verified candidate for publication|"
        "actions/upload-artifact@"
        "ea165f8d65b6e75b540449e92b4886f43607fa02"
    ),
    (
        "verify|Check out the reviewed verification policy|"
        "actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10"
    ),
    (
        "verify|Download the exact candidate into the read-only verifier|"
        "actions/download-artifact@"
        "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"
    ),
    (
        "publish|Check out the reviewed publication policy|"
        "actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10"
    ),
    (
        "publish|Download the exact read-only-verified candidate|"
        "actions/download-artifact@"
        "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"
    ),
    (
        "publish|Install pinned Cosign|sigstore/cosign-installer@"
        "6f9f17788090df1f26f669e9d70d6ae9567deba6"
    ),
    (
        "publish|Publish SLSA provenance for the exact OCI manifest|"
        "actions/attest-build-provenance@"
        "977bb373ede98d70efdf65b84cb5f73e068dcc2a"
    ),
    (
        "publish|Retain review-pending publication evidence|"
        "actions/upload-artifact@"
        "ea165f8d65b6e75b540449e92b4886f43607fa02"
    ),
]
POLARIS_DEPENDENCY_WORKFLOW_ENVIRONMENT = {
    "SOURCE_ARCHIVE_URL": (
        "https://downloads.apache.org/polaris/1.6.0/"
        "apache-polaris-1.6.0.tar.gz"
    ),
    "SOURCE_SIGNATURE_URL": (
        "https://downloads.apache.org/polaris/1.6.0/"
        "apache-polaris-1.6.0.tar.gz.asc"
    ),
    "SOURCE_CHECKSUM_URL": (
        "https://downloads.apache.org/polaris/1.6.0/"
        "apache-polaris-1.6.0.tar.gz.sha512"
    ),
    "SOURCE_ARCHIVE_SHA512": POLARIS_ARCHIVE_SHA512,
    "SOURCE_SIGNATURE_SHA256": POLARIS_SIGNATURE_SHA256,
    "SOURCE_SIGNING_KEY_FINGERPRINT": POLARIS_KEY_FINGERPRINT,
    "BUILDER_IMAGE": BUILDER_ARM64,
    "ARTIFACT_REPOSITORY": (
        "ghcr.io/tommykammy/shirokuma-polaris-gradle-dependencies"
    ),
    "ARTIFACT_TYPE": (
        "application/vnd.shirokuma.polaris.gradle-dependencies.v1"
    ),
    "DESCRIPTOR_MEDIA_TYPE": (
        "application/vnd.shirokuma.gradle-dependency-descriptor.v1+json"
    ),
    "ARCHIVE_MEDIA_TYPE": (
        "application/vnd.shirokuma.gradle-cache.v1.tar+gzip"
    ),
    "ORAS_VERSION": "1.3.3",
    "ORAS_URL": (
        "https://github.com/oras-project/oras/releases/download/v1.3.3/"
        "oras_1.3.3_linux_arm64.tar.gz"
    ),
    "ORAS_ARCHIVE_SHA256": (
        "ac7156f93a21e903f7ad606c792f3560f17e0cd0e36365634701b1e7cc4e4eca"
    ),
    "COSIGN_VERSION": "v3.1.1",
}
POLARIS_DEPENDENCY_WORKFLOW_PUSH_PATHS = [
    ".github/workflows/polaris-gradle-dependencies.yml",
    "bootstrap/polaris/v1.6.0/source.json",
    "bootstrap/polaris/v1.6.0/trusted-build-contract.json",
    "bootstrap/polaris/v1.6.0/admission.json",
    "bootstrap/polaris/v1.6.0/apache-polaris-release-signing-key.asc",
    "scripts/package_polaris_gradle_dependencies.py",
    "scripts/validate_polaris_source_archive.py",
    "scripts/verify_polaris_trusted_image.py",
    "tests/test_package_polaris_gradle_dependencies.py",
    "tests/test_polaris_trusted_image_contract.py",
]
POLARIS_DEPENDENCY_WORKFLOW_PERMISSIONS = {
    "resolve": {"contents": "read"},
    "verify": {"contents": "read"},
    "publish": {
        "attestations": "write",
        "contents": "read",
        "id-token": "write",
        "packages": "write",
    },
}
RUNTIME_ROOTS = (Path("deploy"), Path("charts"), Path("opentofu"))
RUNTIME_GENERATED_DIRS = {".terraform"}
RETAINED_EVIDENCE_ROOT = Path("security/evidence")
RETAINED_EVIDENCE_JSON_SUFFIXES = {".json", ".jsonl"}
RETAINED_EVIDENCE_DOCUMENT_SUFFIXES = {".md"}
MAX_DSSE_PAYLOAD_BYTES = 16 * 1024 * 1024
RUNTIME_IDENTITY = re.compile(r"(?:polaris|postgres(?:ql)?)", re.IGNORECASE)
CATALOG_IDENTITY_MARKERS = ("catalog", "iceberg", "metastore")
CATALOG_PATH_CONTEXT_WORDS = {
    "api",
    "controller",
    "db",
    "gateway",
    "hive",
    "job",
    "metadata",
    "operator",
    "rest",
    "server",
    "service",
    "store",
    "v",
    "worker",
}
PENDING_EVIDENCE_PATH_TOKENS = {
    "polaris",
    "postgres",
    "postgresql",
    *CATALOG_IDENTITY_MARKERS,
}
PENDING_EVIDENCE_CONTEXT_WORDS = CATALOG_PATH_CONTEXT_WORDS | {
    "archive",
    "attestation",
    "bundle",
    "evidence",
    "image",
    "provenance",
    "release",
    "sbom",
    "scan",
    "signature",
    "supplychain",
}
PENDING_IMAGE_REFERENCE_MARKERS = {
    "apache/polaris",
    "c455ec159d05d99ee031d471b8692668562fed8e8c9c37be5e0dbdbee8e5f7b8",
    "cgr.dev/chainguard/postgres",
    "ghcr.io/tommykammy/shirokuma-polaris",
    "shirokuma-polaris",
}
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
PENDING_RUNTIME_FILE_INVENTORY = {
    "charts/AGENTS.md": (
        "ea89f9be52c63608f6e4029a0559ceeee2631cf40d48d53c82694182840757f7"
    ),
    "charts/README.md": (
        "89926b09c2e3253cd20b1515eff66396d47355fbbfa7c4b4ae5981bd4b750e29"
    ),
    "deploy/README.md": (
        "e5fe9c28019256e460c8f79904e7523a33ca3836a4feda503ed96153e1a74125"
    ),
    "deploy/gitops/clusters/local-lite/dev.yaml": (
        "c1c872b4cb148482106960ad978a12a566d6454b18498735f0c9cb768e798a54"
    ),
    "deploy/gitops/clusters/local-lite/flux-system/gotk-components.yaml": (
        "ed307189fd1f9e49819a50843bb6f3c9257fe6d4d8359d1950b38207c26c3854"
    ),
    "deploy/gitops/clusters/local-lite/flux-system/gotk-sync.yaml": (
        "b1083278d11f3512e06e4fcb7d5c048ad25e8b365f498721b4d8cad4365f1a47"
    ),
    "deploy/gitops/clusters/local-lite/flux-system/kustomization.yaml": (
        "6ae842182f60f07621c519666238612bcdc7f5a235adcc5fc3b9998eea53534a"
    ),
    "deploy/gitops/clusters/local-lite/object-storage.yaml": (
        "cc487647bb872a4f0b3b4649957d16912d6716eab429e03b70d19280e5589979"
    ),
    "deploy/gitops/dev/kustomization.yaml": (
        "58e0f98487664eb6104fc266808d361f35e248fa5eca186e3670792c2fee75b3"
    ),
    "deploy/gitops/dev/smoke-configmap.yaml": (
        "f17f662c0f6fc7d804ced0346146a29fd1fa6685e4699c92d9a0c5d4990d66f4"
    ),
    "deploy/gitops/object-storage/contract-configmap.yaml": (
        "c233ec0915b40b3e01f16e5d995f048e1e2e8dcdc6fbdf87f6b56da5c2240c18"
    ),
    "deploy/gitops/object-storage/kustomization.yaml": (
        "e5bafeec039a50fe0e3a47dc192d7fbcf11cd787cab09d5699a117a67a2d0c5d"
    ),
    "deploy/gitops/object-storage/networkpolicy.yaml": (
        "50be544fffce9c6e049699d47836186fa9a672e5e782bfb4b7cd5c871657e1ba"
    ),
    "deploy/gitops/object-storage/service.yaml": (
        "94d1401bfbbcbcc0bc8d8832b7a35f2d9dbfde49e1a4aa456dba34a52dad9c52"
    ),
    "deploy/gitops/object-storage/statefulset.yaml": (
        "55f7e30e45ee100bd031ef02b1ebe0d59220fb29525969f7e67d7ebb1c3dc4cc"
    ),
    "opentofu/README.md": (
        "4cf3f54f0a970b99f15e0b2ec242415bd850de45313a0c96d20c7a4cfcc50f53"
    ),
    "opentofu/dev/.terraform.lock.hcl": (
        "155179caa6064e3ab72e2939371410e02e42d8f0b622ba36db1d627770471fa0"
    ),
    "opentofu/dev/bootstrap-images.json": (
        "f00a249a6c0d48ba0017923e0a5f68bd7eb9e76467aa0aa91019411fc4576903"
    ),
    "opentofu/dev/main.tf": (
        "d6b737d466a70ac7f547a8ca381d3efcde6bff2644d5e7803a15c57b6e709bfd"
    ),
    "opentofu/dev/object-storage.tf": (
        "94f2c064b972cf412fde7bae1049006a9a01cebe95993fff2daec4a525fa8524"
    ),
    "opentofu/dev/outputs.tf": (
        "d8a3a96f45f1ef571f7f2e7d6d2ccb2d4b161a0ccdd9861902a3064e93d95d01"
    ),
    "opentofu/dev/variables.tf": (
        "7546eb24b1c52cdb6d92bd304af99c5e3eec26c2718dc6dfd3c095adecb2b1c4"
    ),
    "opentofu/dev/versions.tf": (
        "cdc92d2859ade98aed3b8fbc910da8e12e5f8a800943a4d7ba016911bbd18296"
    ),
}
PATH_CAMEL_BOUNDARY = re.compile(
    r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])"
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
RUNTIME_BLOCK_SCALAR_VALUE = re.compile(
    r"""(?:(?:![^\s]+|&[^\s]+)\s+)*"""
    r"""(?P<style>[>|])(?P<modifier>[1-9]?[+-]?|[+-]?[1-9]?)?\s*$""",
    re.IGNORECASE | re.VERBOSE,
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
    r'^[ \t]*resource[ \t\r\n]+'
    r'(?:"(?P<quoted_type>(?:\\.|[^"\\\r\n])*)"'
    r'|(?P<bare_type>[^"{}\s]+))'
    r'[ \t\r\n]+(?:"(?:\\.|[^"\\\r\n])*"|[^"{}\s]+)'
    r'[ \t\r\n]*\{',
    re.IGNORECASE | re.MULTILINE,
)
RUNTIME_OPENTOFU_PROVISIONER = re.compile(
    r'^[ \t]*provisioner[ \t\r\n]+'
    r'(?:"(?:\\.|[^"\\\r\n])*"|[^"{}\s]+)'
    r'[ \t\r\n]*\{',
    re.IGNORECASE | re.MULTILINE,
)
RUNTIME_OPENTOFU_GENERIC_MANIFEST_RESOURCE = re.compile(
    r"(?:kubernetes|kubectl)_manifest[a-z0-9_-]*",
    re.IGNORECASE,
)
RETAINED_EVIDENCE_OCI_REFERENCE = re.compile(
    r"(?<![a-z0-9:+./-])"
    r"(?:(?:docker|oci)://)?"
    r"(?:"
    r"(?:localhost(?::[0-9]+)?"
    r"|[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::[0-9]+)?)"
    r"/(?:[a-z0-9._-]+/)*[a-z0-9._-]+"
    r"|[a-z0-9]+(?:[._-][a-z0-9]+)*"
    r")"
    r"(?:"
    r"(?::[a-z0-9_][a-z0-9_.-]{0,127})?"
    r"@sha256:[0-9a-f]{64}"
    r"|:[a-z0-9_][a-z0-9_.-]{0,127}"
    r")"
    r"(?![a-z0-9_.@/-])",
    re.IGNORECASE,
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


def _workflow_step_block(text: str, name: str) -> str:
    marker = f"      - name: {name}\n"
    _expect(
        text.count(marker) == 1,
        "CONTRACT_STATE",
        f"dependency workflow step is missing or duplicated: {name}",
    )
    start = text.index(marker)
    end = text.find("\n      - name: ", start + len(marker))
    return text[start:] if end == -1 else text[start:end]


def _audit_dependency_workflow_semantics(
    root: Path,
    contract: Mapping[str, Any],
) -> None:
    workflow_contract = _nested(contract, "dependency_snapshot", "workflow")
    _expect(
        _nested(workflow_contract, "step_order")
        == POLARIS_DEPENDENCY_WORKFLOW_STEPS,
        "CONTRACT_STATE",
        "dependency workflow step order differs from the semantic contract",
    )
    _expect(
        _nested(workflow_contract, "action_uses")
        == POLARIS_DEPENDENCY_WORKFLOW_ACTIONS,
        "CONTRACT_STATE",
        "dependency workflow Action pins differ from the semantic contract",
    )
    _expect(
        _nested(workflow_contract, "environment")
        == POLARIS_DEPENDENCY_WORKFLOW_ENVIRONMENT,
        "CONTRACT_STATE",
        "dependency workflow environment differs from the semantic contract",
    )
    _expect(
        _nested(workflow_contract, "permissions")
        == POLARIS_DEPENDENCY_WORKFLOW_PERMISSIONS,
        "CONTRACT_STATE",
        "dependency workflow permissions differ from the semantic contract",
    )
    _expect(
        _nested(workflow_contract, "push_paths")
        == POLARIS_DEPENDENCY_WORKFLOW_PUSH_PATHS,
        "CONTRACT_STATE",
        "dependency workflow push paths differ from the semantic contract",
    )
    workflow_path = root / POLARIS_DEPENDENCY_WORKFLOW
    try:
        text = workflow_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        _fail(
            "CONTRACT_STATE",
            f"cannot read dependency workflow semantics: {error}",
        )

    jobs: list[str] = []
    steps: dict[str, list[str]] = {}
    step_entry_counts: dict[str, int] = {}
    unnamed_steps: list[str] = []
    actions: list[str] = []
    environment: dict[str, str] = {}
    runners: dict[str, str] = {}
    permissions: dict[str, dict[str, str]] = {}
    in_environment = False
    in_jobs = False
    in_steps = False
    permission_job: str | None = None
    current_job: str | None = None
    current_step: str | None = None
    for line in text.splitlines():
        if line == "env:":
            in_environment = True
            continue
        if line == "jobs:":
            in_environment = False
            in_jobs = True
            continue
        if in_environment:
            matched_environment = re.fullmatch(
                r"  ([A-Z][A-Z0-9_]+): (.+)",
                line,
            )
            if matched_environment:
                environment[matched_environment.group(1)] = (
                    matched_environment.group(2)
                )
            continue
        if not in_jobs:
            continue
        matched_job = re.fullmatch(r"  ([a-z][a-z0-9_-]*):", line)
        if matched_job:
            current_job = matched_job.group(1)
            current_step = None
            permission_job = None
            in_steps = False
            jobs.append(current_job)
            steps[current_job] = []
            step_entry_counts[current_job] = 0
            permissions[current_job] = {}
            continue
        if current_job is None:
            continue
        matched_runner = re.fullmatch(r"    runs-on: (.+)", line)
        if matched_runner:
            runners[current_job] = matched_runner.group(1)
        if line == "    permissions:":
            permission_job = current_job
            continue
        if line == "    steps:":
            in_steps = True
            current_step = None
            permission_job = None
            continue
        if permission_job is not None:
            matched_permission = re.fullmatch(
                r"      ([a-z-]+): (read|write|none)",
                line,
            )
            if matched_permission:
                permissions[permission_job][matched_permission.group(1)] = (
                    matched_permission.group(2)
                )
                continue
            if line and not line.startswith("      "):
                permission_job = None
        matched_step_entry = (
            re.fullmatch(r"      - (.+)", line) if in_steps else None
        )
        if matched_step_entry:
            step_entry_counts[current_job] += 1
            matched_step = re.fullmatch(
                r"name: (.+)",
                matched_step_entry.group(1),
            )
            if matched_step is None:
                current_step = None
                unnamed_steps.append(
                    f"{current_job}:{matched_step_entry.group(1)}"
                )
                continue
            current_step = matched_step.group(1)
            steps[current_job].append(current_step)
            continue
        matched_action = re.fullmatch(r"        uses: ([^ ]+)(?: .*)?", line)
        if matched_action and current_step is not None:
            actions.append(
                f"{current_job}|{current_step}|{matched_action.group(1)}"
            )

    _expect(
        jobs == ["resolve", "verify", "publish"],
        "CONTRACT_STATE",
        "dependency workflow job order changed",
    )
    _expect(
        steps == POLARIS_DEPENDENCY_WORKFLOW_STEPS,
        "CONTRACT_STATE",
        "dependency workflow ordered step inventory changed",
    )
    _expect(
        not unnamed_steps
        and step_entry_counts
        == {
            job: len(expected_steps)
            for job, expected_steps in POLARIS_DEPENDENCY_WORKFLOW_STEPS.items()
        },
        "CONTRACT_STATE",
        "dependency workflow contains an unnamed or extra step entry",
    )
    _expect(
        actions == POLARIS_DEPENDENCY_WORKFLOW_ACTIONS
        and all(
            re.fullmatch(r".+@[0-9a-f]{40}", action.rsplit("|", 1)[1])
            for action in actions
        ),
        "CONTRACT_STATE",
        "dependency workflow Action pin inventory changed",
    )
    _expect(
        environment == POLARIS_DEPENDENCY_WORKFLOW_ENVIRONMENT,
        "CONTRACT_STATE",
        "dependency workflow top-level environment changed",
    )
    _expect(
        runners
        == {
            "resolve": "ubuntu-24.04-arm",
            "verify": "ubuntu-24.04-arm",
            "publish": "ubuntu-24.04-arm",
        }
        and permissions == POLARIS_DEPENDENCY_WORKFLOW_PERMISSIONS,
        "CONTRACT_STATE",
        "dependency workflow runner or job permissions changed",
    )
    _expect(
        "permissions:\n  contents: read\n\nconcurrency:" in text,
        "CONTRACT_STATE",
        "dependency workflow top-level permissions changed",
    )
    expected_trigger = "\n".join(
        [
            "on:",
            "  push:",
            "    branches:",
            "      - main",
            "    paths:",
            *[
                f"      - {path}"
                for path in POLARIS_DEPENDENCY_WORKFLOW_PUSH_PATHS
            ],
            "  workflow_dispatch:",
        ]
    )
    try:
        trigger_start = text.index("on:\n")
        trigger_end = text.index("\npermissions:\n", trigger_start)
    except ValueError:
        _fail(
            "CONTRACT_STATE",
            "dependency workflow trigger topology changed",
        )
    _expect(
        text[trigger_start:trigger_end].rstrip() == expected_trigger,
        "CONTRACT_STATE",
        "dependency workflow trigger topology changed",
    )
    forbidden_markers = (
        "pull_request:",
        "pull_request_target:",
        "actions/cache@",
        "persist-credentials: true",
        "cache-from:",
        "cache-to:",
        "--network host",
        "--dependency-verification off",
    )
    _expect(
        not any(marker in text for marker in forbidden_markers),
        "CONTRACT_STATE",
        "dependency workflow enabled a forbidden trigger, cache, or bypass",
    )
    required_markers = (
        "dependency_snapshot_publication_pending)",
        "dependency_snapshot_review_pending)",
        'echo "active=false" >> "${GITHUB_OUTPUT}"',
        "--write-verification-metadata sha256",
        "--network none",
        "--offline",
        "--dependency-verification strict",
        "expected_offline_keys = {",
        '"descriptor_sha256": descriptor_sha256',
        '"archive_sha256": archive_sha256',
        '"verification_metadata_sha256": metadata_sha256',
        'toolchain["gradle"] != "9.6.0"',
        'toolchain["builder_image"] != os.environ["BUILDER_IMAGE"]',
        "needs.verify.outputs.descriptor_sha256",
        "cannot prove that the run-scoped OCI tag is absent",
        'git show -s --no-show-signature --format=%cI "${GITHUB_SHA}"',
        '"org.opencontainers.image.created=${created}"',
        '"PUBLISHED_CREATED=${created}"',
        "set(config) != {\"mediaType\", \"digest\", \"size\", \"data\"}",
        'manifest["annotations"]',
        "cosign sign --yes",
        "actions/attest-build-provenance@",
        "oras logout ghcr.io",
        "--registry-config \"${anonymous_config}\"",
        "gh attestation verify",
        '"offline_build": file_record("offline-build.json")',
        '"toolchain": file_record("toolchain.json")',
        '"verification_metadata": file_record(',
        '"created": os.environ["PUBLISHED_CREATED"]',
    )
    _expect(
        all(marker in text for marker in required_markers),
        "CONTRACT_STATE",
        "dependency workflow lost a required publication semantic",
    )
    authentication_step = _workflow_step_block(
        text,
        "Fetch and authenticate the ASF source release",
    )
    _expect(
        "\n".join(
            [
                "          python3 scripts/validate_polaris_source_archive.py \\",
                (
                    "            --archive "
                    '"${source_dir}/apache-polaris-1.6.0.tar.gz"'
                ),
            ]
        )
        in authentication_step,
        "CONTRACT_STATE",
        "source authentication step lost the pinned archive validator",
    )
    online_step = _workflow_step_block(
        text,
        "Resolve dependencies and generate strict verification metadata",
    )
    _expect(
        "\n".join(
            [
                '          test ! -e "${online_source}"',
                '          test ! -L "${online_source}"',
                (
                    '          install -d -m 0700 "${online_source}" '
                    '"${gradle_home}"'
                ),
                (
                    '          tar --extract --gzip --file "${source_archive}" \\'
                ),
                (
                    '            --directory "${online_source}" '
                    "--strip-components 1 \\"
                ),
                "            --no-same-owner --no-same-permissions",
            ]
        )
        in online_step,
        "CONTRACT_STATE",
        "online source extraction lost fresh-directory hardening",
    )
    offline_step = _workflow_step_block(
        text,
        "Prove a fresh network-none offline server build",
    )
    _expect(
        "\n".join(
            [
                '          test ! -e "${offline_source}"',
                '          test ! -L "${offline_source}"',
                '          install -d -m 0700 "${offline_source}"',
            ]
        )
        in offline_step
        and "\n".join(
            [
                "          tar --extract --gzip \\",
                "            --file \\",
                (
                    '              "${RUNNER_TEMP}/polaris-source/'
                    'apache-polaris-1.6.0.tar.gz" \\'
                ),
                (
                    '            --directory "${offline_source}" '
                    "--strip-components 1 \\"
                ),
                "            --no-same-owner --no-same-permissions",
            ]
        )
        in offline_step,
        "CONTRACT_STATE",
        "offline source extraction lost fresh-directory hardening",
    )
    verify_start = text.index("  verify:")
    publish_start = text.index("  publish:")
    verify_text = text[verify_start:publish_start]
    _expect(
        "github.token" not in verify_text
        and "packages: write" not in verify_text
        and "id-token: write" not in verify_text,
        "CONTRACT_STATE",
        "read-only candidate verification gained write credentials",
    )
    publish_steps = POLARIS_DEPENDENCY_WORKFLOW_STEPS["publish"]
    publish_step = _workflow_step_block(
        text,
        "Publish the run-scoped immutable OCI artifact",
    )
    relative_oras_push = "\n".join(
        [
            "          (",
            '            cd "${candidate_dir}"',
            "            oras push \\",
            "              --image-spec v1.1 \\",
            '              --artifact-type "${ARTIFACT_TYPE}" \\',
            "              --annotation \\",
            (
                '                "org.opencontainers.image.source='
                'https://github.com/${GITHUB_REPOSITORY}" \\'
            ),
            (
                '              --annotation "org.opencontainers.image.revision='
                '${GITHUB_SHA}" \\'
            ),
            (
                '              --annotation "org.opencontainers.image.created='
                '${created}" \\'
            ),
            '              --export-manifest "oci-manifest.json" \\',
            '              "${tag}" \\',
            (
                '              "gradle-dependency-inputs.json:'
                '${DESCRIPTOR_MEDIA_TYPE}" \\'
            ),
            (
                '              "polaris-gradle-dependencies-1.6.0.tar.gz:'
                '${ARCHIVE_MEDIA_TYPE}"'
            ),
            "          )",
        ]
    )
    _expect(
        "--disable-path-validation" not in publish_step,
        "CONTRACT_STATE",
        "ORAS path validation cannot be disabled",
    )
    _expect(
        publish_step.count(relative_oras_push) == 1
        and publish_step.count("oras push") == 1
        and text.count("oras push") == 1,
        "CONTRACT_STATE",
        "ORAS publication must use the exact candidate-scoped relative push",
    )
    _expect(
        publish_steps.index("Keyless-sign the exact OCI manifest")
        < publish_steps.index(
            "Publish SLSA provenance for the exact OCI manifest"
        )
        < publish_steps.index("Prove anonymous exact-digest retrieval"),
        "CONTRACT_STATE",
        "publication must be signed and attested before anonymous exposure",
    )


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
                "lifecycle",
                "source",
                "dependency_snapshot",
                "image_publication",
                "runtime",
            },
            ("lifecycle",): {"state", "next_state"},
            ("source",): {
                "record",
                "record_sha256",
                "archive_sha512",
                "archive_member_policy",
                "archive_validator",
                "git_commit",
                "git_tree",
                "builder_index",
                "builder_arm64_manifest",
                "java_major",
                "gradle_version",
                "tasks",
            },
            ("source", "archive_member_policy"): {
                "root",
                "strip_components",
                "allowed_member_types",
                "path_charset",
                "tar_size_encoding",
                "hidden_name_records_permitted",
                "solaris_pax_records_permitted",
                "explicit_directory_parents_required",
                "symlink_target_policy",
                "symlink_chains_permitted",
                "symlink_cycles_permitted",
                "members_below_symlink_paths_permitted",
                "duplicate_paths_permitted",
                "hardlinks_permitted",
                "special_files_permitted",
                "maximum_archive_bytes",
                "maximum_decompressed_bytes",
                "maximum_raw_headers",
                "maximum_tar_control_bytes",
                "maximum_members",
                "maximum_member_bytes",
                "maximum_total_regular_file_bytes",
                "maximum_path_bytes",
                "maximum_path_component_bytes",
                "maximum_path_components",
                "maximum_link_bytes",
                "maximum_pax_bytes_per_member",
                "allowed_pax_headers",
                "pax_record_encoding",
                "pax_comment_policy",
            },
            ("source", "archive_validator"): {"path", "sha256"},
            ("dependency_snapshot",): {
                "state",
                "artifact_repository",
                "artifact_reference",
                "artifact_type",
                "descriptor",
                "verification_metadata",
                "cache_roots",
                "module_cache_identity",
                "limits",
                "archive",
                "descriptor_media_type",
                "verification_metadata_media_type",
                "packager",
                "workflow",
                "visibility_bootstrap",
                "tools",
                "offline_proof",
            },
            ("dependency_snapshot", "module_cache_identity"): {
                "algorithm",
                "encoding",
                "bound_to_artifact_bytes",
                "authentication",
                "retention",
            },
            ("dependency_snapshot", "limits"): {
                "maximum_files",
                "maximum_total_file_bytes",
                "maximum_archive_bytes",
                "maximum_descriptor_bytes",
                "maximum_verification_metadata_bytes",
                "maximum_directories",
                "maximum_path_bytes",
                "maximum_path_component_bytes",
                "maximum_path_components",
                "maximum_tar_control_bytes_per_member",
            },
            ("dependency_snapshot", "archive"): {
                "filename",
                "format",
                "media_type",
                "mtime",
                "uid",
                "gid",
                "file_mode",
                "directory_mode",
            },
            ("dependency_snapshot", "packager"): {"path", "sha256"},
            ("dependency_snapshot", "workflow"): {
                "path",
                "sha256",
                "runner",
                "allowed_events",
                "allowed_refs",
                "push_paths",
                "job_order",
                "permissions",
                "step_order",
                "action_uses",
                "environment",
                "lifecycle_gate",
                "credential_boundary",
                "created_annotation",
                "no_shared_cache",
                "run_scoped_tag",
            },
            ("dependency_snapshot", "workflow", "permissions"): {
                "resolve",
                "verify",
                "publish",
            },
            (
                "dependency_snapshot",
                "workflow",
                "permissions",
                "resolve",
            ): {"contents"},
            (
                "dependency_snapshot",
                "workflow",
                "permissions",
                "verify",
            ): {"contents"},
            (
                "dependency_snapshot",
                "workflow",
                "permissions",
                "publish",
            ): {"attestations", "contents", "id-token", "packages"},
            ("dependency_snapshot", "workflow", "step_order"): {
                "resolve",
                "verify",
                "publish",
            },
            ("dependency_snapshot", "workflow", "environment"): set(
                POLARIS_DEPENDENCY_WORKFLOW_ENVIRONMENT
            ),
            ("dependency_snapshot", "workflow", "lifecycle_gate"): {
                "active_state",
                "skip_state",
                "retire_in_evidence_review_pr",
            },
            ("dependency_snapshot", "workflow", "credential_boundary"): {
                "candidate_verification_job",
                "candidate_verification_permissions",
                "publish_byte_binding_step",
                "registry_login_step",
            },
            (
                "dependency_snapshot",
                "workflow",
                "credential_boundary",
                "candidate_verification_permissions",
            ): {"contents"},
            (
                "dependency_snapshot",
                "workflow",
                "created_annotation",
            ): {"key", "value_source"},
            ("dependency_snapshot", "visibility_bootstrap"): {
                "required_visibility",
                "sign_and_attest_before_anonymous_pull",
                "owner_action_on_first_private_run",
                "failed_attempt_admitted",
            },
            ("dependency_snapshot", "tools"): {"oras", "cosign"},
            ("dependency_snapshot", "tools", "oras"): {
                "version",
                "linux_arm64_archive_sha256",
            },
            ("dependency_snapshot", "tools", "cosign"): {
                "version",
                "issuer",
                "identity",
            },
            ("dependency_snapshot", "offline_proof"): {
                "container_network",
                "gradle_offline",
                "dependency_verification",
                "build_cache",
                "configuration_cache",
                "tasks",
            },
            ("image_publication",): {
                "state",
                "enabled",
                "repository",
                "trusted_tag",
                "containerfile",
                "workflow",
            },
            ("runtime",): {
                "state",
                "enabled",
                "admission_record",
                "atomic_peer",
            },
        },
        "CONTRACT_STATE",
    )
    _expect_fields(
        contract,
        {
            ("schema_version",): 2,
            ("component",): "polaris",
            ("version",): POLARIS_VERSION,
            ("platform",): "linux/arm64",
            (
                "lifecycle",
                "state",
            ): "dependency_snapshot_publication_pending",
            (
                "lifecycle",
                "next_state",
            ): "dependency_snapshot_review_pending",
            ("source", "record"): POLARIS_SOURCE.as_posix(),
            ("source", "record_sha256"): POLARIS_SOURCE_SHA256,
            ("source", "archive_sha512"): POLARIS_ARCHIVE_SHA512,
            (
                "source",
                "archive_member_policy",
                "root",
            ): POLARIS_SOURCE_ARCHIVE_ROOT,
            (
                "source",
                "archive_member_policy",
                "strip_components",
            ): 1,
            (
                "source",
                "archive_member_policy",
                "allowed_member_types",
            ): [
                "regular-file",
                "directory",
                "relative-symlink",
            ],
            (
                "source",
                "archive_member_policy",
                "path_charset",
            ): "printable-ascii-posix",
            (
                "source",
                "archive_member_policy",
                "tar_size_encoding",
            ): "canonical-octal",
            (
                "source",
                "archive_member_policy",
                "hidden_name_records_permitted",
            ): False,
            (
                "source",
                "archive_member_policy",
                "solaris_pax_records_permitted",
            ): False,
            (
                "source",
                "archive_member_policy",
                "explicit_directory_parents_required",
            ): True,
            (
                "source",
                "archive_member_policy",
                "symlink_target_policy",
            ): "existing-in-root-member-before-and-after-strip",
            (
                "source",
                "archive_member_policy",
                "symlink_chains_permitted",
            ): True,
            (
                "source",
                "archive_member_policy",
                "symlink_cycles_permitted",
            ): False,
            (
                "source",
                "archive_member_policy",
                "members_below_symlink_paths_permitted",
            ): False,
            (
                "source",
                "archive_member_policy",
                "duplicate_paths_permitted",
            ): False,
            (
                "source",
                "archive_member_policy",
                "hardlinks_permitted",
            ): False,
            (
                "source",
                "archive_member_policy",
                "special_files_permitted",
            ): False,
            (
                "source",
                "archive_member_policy",
                "maximum_archive_bytes",
            ): POLARIS_SOURCE_ARCHIVE_MAXIMUM_BYTES,
            (
                "source",
                "archive_member_policy",
                "maximum_decompressed_bytes",
            ): POLARIS_SOURCE_ARCHIVE_MAXIMUM_DECOMPRESSED_BYTES,
            (
                "source",
                "archive_member_policy",
                "maximum_raw_headers",
            ): POLARIS_SOURCE_ARCHIVE_MAXIMUM_RAW_HEADERS,
            (
                "source",
                "archive_member_policy",
                "maximum_tar_control_bytes",
            ): POLARIS_SOURCE_ARCHIVE_MAXIMUM_TAR_CONTROL_BYTES,
            (
                "source",
                "archive_member_policy",
                "maximum_members",
            ): POLARIS_SOURCE_ARCHIVE_MAXIMUM_MEMBERS,
            (
                "source",
                "archive_member_policy",
                "maximum_member_bytes",
            ): POLARIS_SOURCE_ARCHIVE_MAXIMUM_MEMBER_BYTES,
            (
                "source",
                "archive_member_policy",
                "maximum_total_regular_file_bytes",
            ): POLARIS_SOURCE_ARCHIVE_MAXIMUM_TOTAL_FILE_BYTES,
            (
                "source",
                "archive_member_policy",
                "maximum_path_bytes",
            ): POLARIS_SOURCE_ARCHIVE_MAXIMUM_PATH_BYTES,
            (
                "source",
                "archive_member_policy",
                "maximum_path_component_bytes",
            ): POLARIS_SOURCE_ARCHIVE_MAXIMUM_PATH_COMPONENT_BYTES,
            (
                "source",
                "archive_member_policy",
                "maximum_path_components",
            ): POLARIS_SOURCE_ARCHIVE_MAXIMUM_PATH_COMPONENTS,
            (
                "source",
                "archive_member_policy",
                "maximum_link_bytes",
            ): POLARIS_SOURCE_ARCHIVE_MAXIMUM_LINK_BYTES,
            (
                "source",
                "archive_member_policy",
                "maximum_pax_bytes_per_member",
            ): POLARIS_SOURCE_ARCHIVE_MAXIMUM_PAX_BYTES,
            (
                "source",
                "archive_member_policy",
                "allowed_pax_headers",
            ): sorted(POLARIS_SOURCE_ARCHIVE_ALLOWED_PAX_HEADERS),
            (
                "source",
                "archive_member_policy",
                "pax_record_encoding",
            ): "strict-length-prefixed-lines",
            (
                "source",
                "archive_member_policy",
                "pax_comment_policy",
            ): "if-present-equals-pinned-git-commit",
            (
                "source",
                "archive_validator",
                "path",
            ): POLARIS_SOURCE_ARCHIVE_VALIDATOR.as_posix(),
            (
                "source",
                "archive_validator",
                "sha256",
            ): POLARIS_SOURCE_ARCHIVE_VALIDATOR_SHA256,
            ("source", "git_commit"): POLARIS_COMMIT,
            ("source", "git_tree"): POLARIS_TREE,
            ("source", "builder_index"): BUILDER_INDEX,
            ("source", "builder_arm64_manifest"): BUILDER_ARM64,
            ("source", "java_major"): 21,
            ("source", "gradle_version"): "9.6.0",
            ("dependency_snapshot", "state"): "publication_pending",
            (
                "dependency_snapshot",
                "artifact_repository",
            ): "ghcr.io/tommykammy/shirokuma-polaris-gradle-dependencies",
            ("dependency_snapshot", "artifact_reference"): None,
            (
                "dependency_snapshot",
                "artifact_type",
            ): "application/vnd.shirokuma.polaris.gradle-dependencies.v1",
            ("dependency_snapshot", "descriptor"): None,
            ("dependency_snapshot", "verification_metadata"): None,
            (
                "dependency_snapshot",
                "cache_roots",
            ): [
                "caches/modules-2/files-2.1",
                "caches/modules-2/metadata-2.107",
            ],
            (
                "dependency_snapshot",
                "module_cache_identity",
                "algorithm",
            ): "sha1",
            (
                "dependency_snapshot",
                "module_cache_identity",
                "encoding",
            ): "lowercase-hex-leading-zeroes-stripped",
            (
                "dependency_snapshot",
                "module_cache_identity",
                "bound_to_artifact_bytes",
            ): True,
            (
                "dependency_snapshot",
                "module_cache_identity",
                "authentication",
            ): "gradle-verification-metadata-sha256",
            (
                "dependency_snapshot",
                "module_cache_identity",
                "retention",
            ): "verification-metadata-sha256-closure",
            (
                "dependency_snapshot",
                "limits",
                "maximum_files",
            ): 10_000,
            (
                "dependency_snapshot",
                "limits",
                "maximum_total_file_bytes",
            ): 2_147_483_648,
            (
                "dependency_snapshot",
                "limits",
                "maximum_archive_bytes",
            ): 1_073_741_824,
            (
                "dependency_snapshot",
                "limits",
                "maximum_descriptor_bytes",
            ): 67_108_864,
            (
                "dependency_snapshot",
                "limits",
                "maximum_verification_metadata_bytes",
            ): 67_108_864,
            (
                "dependency_snapshot",
                "limits",
                "maximum_directories",
            ): 100_000,
            (
                "dependency_snapshot",
                "limits",
                "maximum_path_bytes",
            ): 1_024,
            (
                "dependency_snapshot",
                "limits",
                "maximum_path_component_bytes",
            ): 255,
            (
                "dependency_snapshot",
                "limits",
                "maximum_path_components",
            ): 32,
            (
                "dependency_snapshot",
                "limits",
                "maximum_tar_control_bytes_per_member",
            ): 4_096,
            (
                "dependency_snapshot",
                "archive",
                "filename",
            ): "polaris-gradle-dependencies-1.6.0.tar.gz",
            ("dependency_snapshot", "archive", "format"): "pax+gzip",
            (
                "dependency_snapshot",
                "archive",
                "media_type",
            ): "application/vnd.shirokuma.gradle-cache.v1.tar+gzip",
            ("dependency_snapshot", "archive", "mtime"): 0,
            ("dependency_snapshot", "archive", "uid"): 0,
            ("dependency_snapshot", "archive", "gid"): 0,
            ("dependency_snapshot", "archive", "file_mode"): "0644",
            ("dependency_snapshot", "archive", "directory_mode"): "0755",
            (
                "dependency_snapshot",
                "descriptor_media_type",
            ): "application/vnd.shirokuma.gradle-dependency-descriptor.v1+json",
            (
                "dependency_snapshot",
                "verification_metadata_media_type",
            ): "application/vnd.gradle.dependency-verification.v1+xml",
            (
                "dependency_snapshot",
                "packager",
                "path",
            ): POLARIS_DEPENDENCY_PACKAGER.as_posix(),
            (
                "dependency_snapshot",
                "packager",
                "sha256",
            ): POLARIS_DEPENDENCY_PACKAGER_SHA256,
            (
                "dependency_snapshot",
                "workflow",
                "path",
            ): POLARIS_DEPENDENCY_WORKFLOW.as_posix(),
            (
                "dependency_snapshot",
                "workflow",
                "sha256",
            ): POLARIS_DEPENDENCY_WORKFLOW_SHA256,
            (
                "dependency_snapshot",
                "workflow",
                "runner",
            ): "ubuntu-24.04-arm",
            (
                "dependency_snapshot",
                "workflow",
                "allowed_events",
            ): ["push", "workflow_dispatch"],
            (
                "dependency_snapshot",
                "workflow",
                "allowed_refs",
            ): ["refs/heads/main"],
            (
                "dependency_snapshot",
                "workflow",
                "push_paths",
            ): POLARIS_DEPENDENCY_WORKFLOW_PUSH_PATHS,
            (
                "dependency_snapshot",
                "workflow",
                "job_order",
            ): ["resolve", "verify", "publish"],
            (
                "dependency_snapshot",
                "workflow",
                "permissions",
                "resolve",
                "contents",
            ): "read",
            (
                "dependency_snapshot",
                "workflow",
                "permissions",
                "verify",
                "contents",
            ): "read",
            (
                "dependency_snapshot",
                "workflow",
                "permissions",
                "publish",
                "attestations",
            ): "write",
            (
                "dependency_snapshot",
                "workflow",
                "permissions",
                "publish",
                "contents",
            ): "read",
            (
                "dependency_snapshot",
                "workflow",
                "permissions",
                "publish",
                "id-token",
            ): "write",
            (
                "dependency_snapshot",
                "workflow",
                "permissions",
                "publish",
                "packages",
            ): "write",
            (
                "dependency_snapshot",
                "workflow",
                "no_shared_cache",
            ): True,
            (
                "dependency_snapshot",
                "workflow",
                "run_scoped_tag",
            ): "1.6.0-${github.run_id}-${github.run_attempt}",
            (
                "dependency_snapshot",
                "workflow",
                "lifecycle_gate",
                "active_state",
            ): "dependency_snapshot_publication_pending",
            (
                "dependency_snapshot",
                "workflow",
                "lifecycle_gate",
                "skip_state",
            ): "dependency_snapshot_review_pending",
            (
                "dependency_snapshot",
                "workflow",
                "lifecycle_gate",
                "retire_in_evidence_review_pr",
            ): True,
            (
                "dependency_snapshot",
                "workflow",
                "credential_boundary",
                "candidate_verification_job",
            ): "verify",
            (
                "dependency_snapshot",
                "workflow",
                "credential_boundary",
                "candidate_verification_permissions",
                "contents",
            ): "read",
            (
                "dependency_snapshot",
                "workflow",
                "credential_boundary",
                "publish_byte_binding_step",
            ): "Bind candidate bytes to the read-only verification",
            (
                "dependency_snapshot",
                "workflow",
                "credential_boundary",
                "registry_login_step",
            ): "Publish the run-scoped immutable OCI artifact",
            (
                "dependency_snapshot",
                "workflow",
                "created_annotation",
                "key",
            ): "org.opencontainers.image.created",
            (
                "dependency_snapshot",
                "workflow",
                "created_annotation",
                "value_source",
            ): "git-committer-date-of-github-sha",
            (
                "dependency_snapshot",
                "visibility_bootstrap",
                "required_visibility",
            ): "public",
            (
                "dependency_snapshot",
                "visibility_bootstrap",
                "sign_and_attest_before_anonymous_pull",
            ): True,
            (
                "dependency_snapshot",
                "visibility_bootstrap",
                "owner_action_on_first_private_run",
            ): "set-package-public-and-rerun",
            (
                "dependency_snapshot",
                "visibility_bootstrap",
                "failed_attempt_admitted",
            ): False,
            (
                "dependency_snapshot",
                "tools",
                "oras",
                "version",
            ): "1.3.3",
            (
                "dependency_snapshot",
                "tools",
                "oras",
                "linux_arm64_archive_sha256",
            ): "ac7156f93a21e903f7ad606c792f3560f17e0cd0e36365634701b1e7cc4e4eca",
            (
                "dependency_snapshot",
                "tools",
                "cosign",
                "version",
            ): "3.1.1",
            (
                "dependency_snapshot",
                "tools",
                "cosign",
                "issuer",
            ): "https://token.actions.githubusercontent.com",
            (
                "dependency_snapshot",
                "tools",
                "cosign",
                "identity",
            ): (
                "https://github.com/TommyKammy/Shirokuma/.github/workflows/"
                "polaris-gradle-dependencies.yml@refs/heads/main"
            ),
            (
                "dependency_snapshot",
                "offline_proof",
                "container_network",
            ): "none",
            (
                "dependency_snapshot",
                "offline_proof",
                "gradle_offline",
            ): True,
            (
                "dependency_snapshot",
                "offline_proof",
                "dependency_verification",
            ): "strict",
            (
                "dependency_snapshot",
                "offline_proof",
                "build_cache",
            ): False,
            (
                "dependency_snapshot",
                "offline_proof",
                "configuration_cache",
            ): False,
            (
                "image_publication",
                "state",
            ): "blocked_dependency_snapshot",
            ("image_publication", "enabled"): False,
            (
                "image_publication",
                "repository",
            ): "ghcr.io/tommykammy/shirokuma-polaris",
            ("image_publication", "trusted_tag"): "1.6.0-arm64",
            ("image_publication", "containerfile"): None,
            ("image_publication", "workflow"): None,
            ("runtime", "state"): "blocked_atomic_admission",
            ("runtime", "enabled"): False,
            ("runtime", "admission_record"): POLARIS_ADMISSION.as_posix(),
            ("runtime", "atomic_peer"): POSTGRES_ADMISSION.as_posix(),
        },
        "CONTRACT_STATE",
    )
    _expect(
        _nested(contract, "source", "tasks") == POLARIS_SERVER_TASKS,
        "CONTRACT_STATE",
        "source task closure changed",
    )
    _expect(
        _nested(contract, "dependency_snapshot", "offline_proof", "tasks")
        == POLARIS_SERVER_TASKS,
        "CONTRACT_STATE",
        "offline proof task closure changed",
    )
    _expect(
        _is_regular_file_without_symlink_components(
            root,
            POLARIS_SOURCE_ARCHIVE_VALIDATOR,
        )
        and _sha256(root / POLARIS_SOURCE_ARCHIVE_VALIDATOR)
        == POLARIS_SOURCE_ARCHIVE_VALIDATOR_SHA256,
        "CONTRACT_STATE",
        "source archive validator bytes differ from the reviewed contract",
    )
    _expect(
        _is_regular_file_without_symlink_components(
            root,
            POLARIS_DEPENDENCY_PACKAGER,
        )
        and _sha256(root / POLARIS_DEPENDENCY_PACKAGER)
        == POLARIS_DEPENDENCY_PACKAGER_SHA256,
        "CONTRACT_STATE",
        "dependency packager bytes differ from the reviewed contract",
    )
    _expect(
        _is_regular_file_without_symlink_components(
            root,
            POLARIS_DEPENDENCY_WORKFLOW,
        )
        and _sha256(root / POLARIS_DEPENDENCY_WORKFLOW)
        == POLARIS_DEPENDENCY_WORKFLOW_SHA256,
        "CONTRACT_STATE",
        "dependency workflow bytes differ from the reviewed contract",
    )
    _audit_dependency_workflow_semantics(root, contract)
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
                "dependency_snapshot",
                "upstream_image_assessment",
                "planned_candidate",
                "resident_ledger",
                "runtime_manifests",
                "blocking_controls",
                "next_action",
            },
            ("dependency_snapshot",): {
                "state",
                "repository",
                "reference",
                "publication_evidence",
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
            ("schema_version",): 2,
            ("component",): "polaris",
            ("version",): POLARIS_VERSION,
            ("platform",): "linux/arm64",
            ("admission",): "blocked",
            ("state",): "dependency_snapshot_publication_pending",
            ("source_record",): POLARIS_SOURCE.as_posix(),
            ("source_record_sha256",): POLARIS_SOURCE_SHA256,
            ("build_contract",): POLARIS_CONTRACT.as_posix(),
            ("build_contract_sha256",): POLARIS_CONTRACT_SHA256,
            (
                "dependency_snapshot",
                "state",
            ): "publication_pending",
            (
                "dependency_snapshot",
                "repository",
            ): "ghcr.io/tommykammy/shirokuma-polaris-gradle-dependencies",
            ("dependency_snapshot", "reference"): None,
            ("dependency_snapshot", "publication_evidence"): None,
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
            ): "merge-run-and-review-gradle-dependency-snapshot",
        },
        "POLARIS_ADMISSION",
    )
    _expect(
        admission.get("blocking_controls") == POLARIS_BLOCKING_CONTROLS,
        "POLARIS_ADMISSION",
        "all four blocking control states must remain explicit",
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
    bootstrap_root = root / "bootstrap"
    _expect(
        bootstrap_root.is_dir() and not bootstrap_root.is_symlink(),
        "FORBIDDEN_PATH",
        "invalid bootstrap root",
    )
    for candidate in bootstrap_root.rglob("*"):
        if not candidate.is_file():
            continue
        relative_candidate = candidate.relative_to(bootstrap_root)
        namespace_skeleton = _identity_skeleton(
            relative_candidate.parent.parts
        )
        filename_skeleton = _identity_skeleton((relative_candidate.name,))
        build_artifact = bool(
            _path_identity_tokens(
                unicodedata.normalize("NFKC", relative_candidate.name)
            )
            & PENDING_BOOTSTRAP_ARTIFACT_MARKERS
        )
        for marker, canonical in PENDING_BOOTSTRAP_NAMESPACES.items():
            identity_bearing_path = _skeleton_contains_marker(
                namespace_skeleton,
                marker,
            ) or (
                build_artifact
                and _skeleton_contains_marker(filename_skeleton, marker)
            )
            if not identity_bearing_path:
                continue
            _expect(
                relative_candidate.parts[0] == canonical,
                "FORBIDDEN_PATH",
                "noncanonical pending bootstrap namespace is forbidden: "
                f"{candidate.relative_to(root)}",
            )
    for relative_root, allowed in (
        (Path("bootstrap/polaris"), POLARIS_PENDING_PATHS),
        (Path("bootstrap/postgresql"), POSTGRES_PENDING_PATHS),
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
                _is_regular_file_without_symlink_components(
                    root,
                    workflow.relative_to(root),
                ),
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
            if relative_workflow != POLARIS_DEPENDENCY_WORKFLOW.as_posix():
                _expect(
                    "polaris" not in normalized
                    and "shirokuma-polaris" not in normalized
                    and "bootstrap/polaris" not in normalized,
                    "FORBIDDEN_PATH",
                    "unreviewed Polaris workflow is forbidden while snapshot "
                    f"publication is pending: {workflow.relative_to(root)}",
                )
    _expect(
        workflow_inventory == PENDING_WORKFLOW_INVENTORY,
        "FORBIDDEN_PATH",
        "workflow inventory changed while snapshot publication is pending; "
        f"expected {sorted(PENDING_WORKFLOW_INVENTORY)}, "
        f"found {sorted(workflow_inventory)}",
    )
    scripts_root = root / "scripts"
    _expect(
        scripts_root.is_dir() and not scripts_root.is_symlink(),
        "FORBIDDEN_PATH",
        "invalid scripts root while snapshot publication is pending",
    )
    pycache = scripts_root / "__pycache__"
    if pycache.exists():
        _expect(
            pycache.is_dir() and not pycache.is_symlink(),
            "FORBIDDEN_PATH",
            "invalid scripts/__pycache__ while snapshot publication is "
            "pending",
        )
        for cached in pycache.rglob("*"):
            _expect(
                cached.is_file()
                and not cached.is_symlink()
                and cached.suffix == ".pyc",
                "FORBIDDEN_PATH",
                "invalid scripts/__pycache__ entry while snapshot publication "
                f"closure is pending: {cached.relative_to(root)}",
            )
    script_inventory: dict[str, str] = {}
    for script in scripts_root.iterdir():
        if script.name == "__pycache__":
            continue
        relative_script = script.relative_to(root).as_posix()
        _expect(
            _is_regular_file_without_symlink_components(
                root,
                script.relative_to(root),
            ),
            "FORBIDDEN_PATH",
            "scripts inventory must contain only regular files while snapshot "
            f"publication is pending: {relative_script}",
        )
        script_inventory[relative_script] = _sha256(script)
    _expect(
        set(script_inventory) == PENDING_SCRIPT_PATHS,
        "FORBIDDEN_PATH",
        "scripts inventory changed while snapshot publication is pending; "
        f"expected {sorted(PENDING_SCRIPT_PATHS)}, "
        f"found {sorted(script_inventory)}",
    )
    tracked_script_paths = _git_tracked_script_paths(root)
    _expect(
        tracked_script_paths is None
        or tracked_script_paths == PENDING_SCRIPT_PATHS,
        "FORBIDDEN_PATH",
        "tracked scripts inventory changed while snapshot publication is "
        f"pending; expected {sorted(PENDING_SCRIPT_PATHS)}, "
        f"found {sorted(tracked_script_paths or set())}",
    )
    for relative, expected_sha256 in PENDING_SCRIPT_FILE_INVENTORY.items():
        _expect(
            script_inventory[relative] == expected_sha256,
            "FORBIDDEN_PATH",
            "script changed while Polaris dependency closure is pending: "
            f"{relative}",
        )
    charts_root = root / "charts"
    if charts_root.exists():
        _expect(
            charts_root.is_dir() and not charts_root.is_symlink(),
            "FORBIDDEN_PATH",
            "invalid charts root while Polaris dependency closure is pending",
        )
        for candidate in charts_root.rglob("*"):
            relative_chart = candidate.relative_to(charts_root).as_posix()
            _expect(
                candidate.is_file()
                and not candidate.is_symlink()
                and relative_chart in PENDING_CHART_PATHS,
                "FORBIDDEN_PATH",
                "Helm chart sources must remain absent while "
                "Polaris/PostgreSQL admission is pending: "
                f"{candidate.relative_to(root)}",
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


def _iter_string_values(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for nested in value.values():
            yield from _iter_string_values(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _iter_string_values(nested)


def _is_regular_file_without_symlink_components(
    root: Path,
    relative: Path,
) -> bool:
    candidate = root
    for part in relative.parts:
        candidate /= part
        if candidate.is_symlink():
            return False
    return candidate.is_file()


def _git_tracked_paths(
    root: Path,
    pathspecs: tuple[str, ...] = (),
) -> set[str] | None:
    if not (root / ".git").exists():
        return None
    try:
        command = ["git", "-C", str(root), "ls-files", "-z"]
        if pathspecs:
            command.extend(("--", *pathspecs))
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
        )
        return {
            value.decode("utf-8")
            for value in completed.stdout.split(b"\0")
            if value
        }
    except (OSError, subprocess.CalledProcessError, UnicodeError) as error:
        _fail(
            "FORBIDDEN_PATH",
            f"cannot inspect tracked repository paths: {error}",
        )


def _git_tracked_script_paths(root: Path) -> set[str] | None:
    return _git_tracked_paths(root, ("scripts",))


def _mapping_field(value: Mapping[str, Any], name: str) -> Any:
    normalized_name = re.sub(r"[^a-z0-9]", "", name.lower())
    for key, nested in value.items():
        normalized_key = re.sub(r"[^a-z0-9]", "", str(key).lower())
        if normalized_key == normalized_name:
            return nested
    return None


def _identity_skeleton(parts: Iterable[str]) -> str:
    normalized = unicodedata.normalize("NFKC", "".join(parts)).casefold()
    return "".join(
        character
        if character.isascii() and character.isalnum()
        else "?"
        if character.isalnum()
        else ""
        for character in normalized
    )


def _skeleton_contains_marker(value: str, marker: str) -> bool:
    for offset in range(len(value) - len(marker) + 1):
        candidate = value[offset : offset + len(marker)]
        ascii_matches = sum(
            actual == expected
            for actual, expected in zip(candidate, marker)
        )
        if (
            ascii_matches >= max(3, len(marker) // 2)
            and all(
                actual == expected or actual == "?"
                for actual, expected in zip(candidate, marker)
            )
        ):
            return True
    return False


def _path_identity_tokens(value: str) -> set[str]:
    separated = PATH_CAMEL_BOUNDARY.sub("/", value)
    return set(re.findall(r"[a-z0-9]+", separated.lower()))


def _is_segmented_identity(
    value: str,
    identities: set[str],
    context_words: set[str],
) -> bool:
    candidate = re.sub(r"\d+$", "", value.casefold())
    if not candidate:
        return False
    words = identities | context_words
    states = {(0, False)}
    while states:
        offset, found_identity = states.pop()
        if offset == len(candidate):
            return found_identity
        for word in words:
            if candidate.startswith(word, offset):
                states.add(
                    (
                        offset + len(word),
                        found_identity or word in identities,
                    )
                )
    return False


def _is_catalog_identity_token(value: str) -> bool:
    return _is_segmented_identity(
        value,
        set(CATALOG_IDENTITY_MARKERS),
        CATALOG_PATH_CONTEXT_WORDS,
    )


def _has_catalog_path_identity(value: str) -> bool:
    return any(
        _is_catalog_identity_token(token)
        for token in _path_identity_tokens(value)
    )


def _contains_bounded_marker(value: str, marker: str) -> bool:
    return bool(
        re.search(
            rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])",
            value,
            re.IGNORECASE,
        )
    )


def _retained_evidence_subject_values(document: Any) -> Iterable[str]:
    stack = [(document, True)]
    while stack:
        value, is_root = stack.pop()
        if isinstance(value, list):
            stack.extend((nested, is_root) for nested in value)
            continue
        if not isinstance(value, Mapping):
            continue

        if is_root:
            for field in (
                "component",
                "image",
                "reference",
                "repository",
                "source",
                "uri",
            ):
                yield from _iter_string_values(_mapping_field(value, field))
            if _mapping_field(value, "spdxVersion") is not None:
                yield from _iter_string_values(_mapping_field(value, "name"))

        for key, nested in value.items():
            normalized_key = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized_key in {
                "artifactname",
                "dockerreference",
                "imagereference",
                "repodigests",
                "subjectreference",
            }:
                yield from _iter_string_values(nested)

            if normalized_key in {"image", "images"}:
                entries = nested if isinstance(nested, list) else [nested]
                for entry in entries:
                    if not isinstance(entry, Mapping):
                        continue
                    for field in (
                        "component",
                        "image",
                        "name",
                        "reference",
                        "repository",
                        "source",
                        "uri",
                    ):
                        yield from _iter_string_values(
                            _mapping_field(entry, field)
                        )

            if normalized_key == "subject":
                entries = nested if isinstance(nested, list) else [nested]
                for entry in entries:
                    if not isinstance(entry, Mapping):
                        continue
                    for field in ("component", "name", "reference", "uri"):
                        yield from _iter_string_values(
                            _mapping_field(entry, field)
                        )

            if normalized_key == "metadata" and isinstance(nested, Mapping):
                component = _mapping_field(nested, "component")
                if isinstance(component, Mapping):
                    for field in ("bom-ref", "name", "purl", "reference"):
                        yield from _iter_string_values(
                            _mapping_field(component, field)
                        )

            if isinstance(nested, (Mapping, list)):
                stack.append((nested, False))


def _decoded_dsse_payload(document: Any, relative: str) -> Any | None:
    if not isinstance(document, Mapping):
        return None
    payload_type = _mapping_field(document, "payloadType")
    payload = _mapping_field(document, "payload")
    if payload_type is None and payload is None:
        return None
    _expect(
        isinstance(payload_type, str) and isinstance(payload, str),
        "FORBIDDEN_PATH",
        f"invalid DSSE envelope in retained evidence: {relative}",
    )
    normalized_type = payload_type.casefold()
    _expect(
        "in-toto" in normalized_type or "json" in normalized_type,
        "FORBIDDEN_PATH",
        f"unsupported DSSE payload type in retained evidence: {relative}",
    )
    _expect(
        len(payload) <= ((MAX_DSSE_PAYLOAD_BYTES + 2) // 3) * 4,
        "FORBIDDEN_PATH",
        f"DSSE payload is too large to inspect: {relative}",
    )
    try:
        decoded = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as error:
        _fail(
            "FORBIDDEN_PATH",
            f"invalid DSSE payload in retained evidence {relative}: {error}",
        )
    _expect(
        len(decoded) <= MAX_DSSE_PAYLOAD_BYTES,
        "FORBIDDEN_PATH",
        f"DSSE payload is too large to inspect: {relative}",
    )
    try:
        return json.loads(
            decoded.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
        )
    except (UnicodeError, ValueError) as error:
        _fail(
            "FORBIDDEN_PATH",
            f"cannot inspect DSSE payload in retained evidence {relative}: {error}",
        )


def _expanded_retained_evidence_documents(
    document: Any,
    relative: str,
) -> Iterable[Any]:
    stack = [(document, 0)]
    while stack:
        value, depth = stack.pop()
        yield value
        if isinstance(value, list):
            stack.extend((nested, depth) for nested in value)
            continue
        if isinstance(value, Mapping):
            for key, nested in value.items():
                normalized_key = re.sub(
                    r"[^a-z0-9]",
                    "",
                    str(key).casefold(),
                )
                if normalized_key != "dsseenvelope":
                    continue
                _expect(
                    isinstance(nested, Mapping),
                    "FORBIDDEN_PATH",
                    f"invalid Sigstore DSSE envelope: {relative}",
                )
                stack.append((nested, depth))
        decoded = _decoded_dsse_payload(value, relative)
        if decoded is None:
            continue
        _expect(
            depth < 4,
            "FORBIDDEN_PATH",
            f"DSSE envelope nesting is too deep: {relative}",
        )
        stack.append((decoded, depth + 1))


def _is_pending_evidence_identity(values: Iterable[str]) -> bool:
    for value in values:
        tokens = _path_identity_tokens(value)
        if (
            any(
                _is_segmented_identity(
                    token,
                    PENDING_EVIDENCE_PATH_TOKENS,
                    PENDING_EVIDENCE_CONTEXT_WORDS,
                )
                for token in tokens
            )
            or any(
                _contains_bounded_marker(value, marker)
                for marker in PENDING_IMAGE_REFERENCE_MARKERS
            )
        ):
            return True
    return False


def _is_pending_evidence_reference(value: str) -> bool:
    if any(
        _contains_bounded_marker(value, marker)
        for marker in PENDING_IMAGE_REFERENCE_MARKERS
    ):
        return True
    for match in RETAINED_EVIDENCE_OCI_REFERENCE.finditer(value):
        if any(
            _is_segmented_identity(
                token,
                PENDING_EVIDENCE_PATH_TOKENS,
                PENDING_EVIDENCE_CONTEXT_WORDS,
            )
            for token in _path_identity_tokens(match.group(0))
        ):
            return True
    return False


def _audit_retained_pending_evidence(root: Path) -> None:
    directory = root / RETAINED_EVIDENCE_ROOT
    if not directory.exists():
        return
    _expect(
        directory.is_dir() and not directory.is_symlink(),
        "FORBIDDEN_PATH",
        f"invalid retained evidence root: {RETAINED_EVIDENCE_ROOT}",
    )
    for path in directory.rglob("*"):
        relative = path.relative_to(root).as_posix()
        evidence_relative = path.relative_to(directory).as_posix()
        path_tokens = _path_identity_tokens(evidence_relative)
        _expect(
            not any(
                _is_segmented_identity(
                    token,
                    PENDING_EVIDENCE_PATH_TOKENS,
                    PENDING_EVIDENCE_CONTEXT_WORDS,
                )
                for token in path_tokens
            ),
            "FORBIDDEN_PATH",
            f"pending catalog evidence cannot be retained: {relative}",
        )
        _expect(
            not path.is_symlink(),
            "FORBIDDEN_PATH",
            f"retained evidence symlink cannot be audited: {relative}",
        )
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in RETAINED_EVIDENCE_DOCUMENT_SUFFIXES:
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as error:
                _fail(
                    "FORBIDDEN_PATH",
                    f"cannot inspect retained evidence {relative}: {error}",
                )
            _expect(
                not _is_pending_evidence_identity([text]),
                "FORBIDDEN_PATH",
                f"pending catalog evidence cannot be retained: {relative}",
            )
            continue
        _expect(
            suffix in RETAINED_EVIDENCE_JSON_SUFFIXES,
            "FORBIDDEN_PATH",
            f"unsupported retained evidence format: {relative}",
        )
        try:
            text = path.read_text(encoding="utf-8")
            if suffix == ".jsonl":
                documents = [
                    json.loads(line, object_pairs_hook=_reject_duplicate_pairs)
                    for line in text.splitlines()
                    if line.strip()
                ]
            else:
                documents = [
                    json.loads(text, object_pairs_hook=_reject_duplicate_pairs)
                ]
        except (OSError, UnicodeError, ValueError) as error:
            _fail(
                "FORBIDDEN_PATH",
                f"cannot inspect retained evidence {relative}: {error}",
            )
        expanded_documents = [
            nested
            for document in documents
            for nested in _expanded_retained_evidence_documents(
                document,
                relative,
            )
        ]
        _expect(
            not any(
                _is_pending_evidence_identity(
                    _retained_evidence_subject_values(document)
                )
                or any(
                    _is_pending_evidence_reference(value)
                    for value in _iter_string_values(document)
                )
                for document in expanded_documents
            ),
            "FORBIDDEN_PATH",
            f"pending catalog evidence cannot be retained: {relative}",
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
            or any(
                marker in serialized for marker in PENDING_IMAGE_REFERENCE_MARKERS
            )
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


def _audit_pending_runtime_inventory(root: Path) -> None:
    tracked = _git_tracked_paths(root, ("deploy", "charts", "opentofu"))
    if tracked is None:
        return

    for relative_root in RUNTIME_ROOTS:
        directory = root / relative_root
        _expect(
            directory.is_dir() and not directory.is_symlink(),
            "RUNTIME_BLOCK",
            f"invalid pending runtime root: {relative_root}",
        )

    expected_paths = set(PENDING_RUNTIME_FILE_INVENTORY)
    _expect(
        tracked == expected_paths,
        "RUNTIME_BLOCK",
        "tracked runtime inventory changed while Polaris/PostgreSQL admission "
        f"is pending; expected {sorted(expected_paths)}, "
        f"found {sorted(tracked)}",
    )
    for relative, expected_sha256 in PENDING_RUNTIME_FILE_INVENTORY.items():
        runtime_file = root / relative
        _expect(
            _is_regular_file_without_symlink_components(
                root,
                Path(relative),
            )
            and _sha256(runtime_file) == expected_sha256,
            "RUNTIME_BLOCK",
            "runtime file changed while Polaris/PostgreSQL admission is "
            f"pending: {relative}",
        )


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


def _is_blocked_opentofu_resource_type(value: str) -> bool:
    return bool(
        re.fullmatch(r"kubernetes_secret[a-z0-9_-]*", value, re.IGNORECASE)
        or RUNTIME_OPENTOFU_GENERIC_MANIFEST_RESOURCE.fullmatch(value)
        or re.fullmatch(r"helm_release[a-z0-9_-]*", value, re.IGNORECASE)
    )


def _has_blocked_opentofu_resource(relative: str, text: str) -> bool:
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
        if any(
            _is_blocked_opentofu_resource_type(str(resource_type))
            for resource_type in resources
        ):
            return True
        return any(
            isinstance(resource_instances, Mapping)
            and any(
                isinstance(resource_body, Mapping)
                and "provisioner" in resource_body
                for resource_body in resource_instances.values()
            )
            for resource_instances in resources.values()
        )
    if lowered.endswith((".tf", ".tofu")):
        inspected = _mask_hcl_non_code(relative, text.removeprefix("\ufeff"))
        if RUNTIME_OPENTOFU_PROVISIONER.search(inspected):
            return True
        for match in RUNTIME_OPENTOFU_RESOURCE.finditer(inspected):
            quoted_type = match.group("quoted_type")
            resource_type = (
                _decode_hcl_label(relative, quoted_type)
                if quoted_type is not None
                else match.group("bare_type")
            )
            if _is_blocked_opentofu_resource_type(resource_type):
                return True
        return False
    return False


def _has_unapproved_catalog_marker(relative: str, text: str) -> bool:
    approved_line = APPROVED_RUNTIME_CATALOG_LINES.get(relative)
    inspected = text
    if approved_line is not None:
        lines = text.splitlines()
        if lines.count(approved_line) == 1:
            inspected = "\n".join(line for line in lines if line != approved_line)
    return bool(RUNTIME_CATALOG_MARKER.search(inspected))


def _yaml_code_line(line: str) -> str:
    in_single = False
    in_double = False
    index = 0
    while index < len(line):
        character = line[index]
        if in_single:
            if (
                character == "'"
                and index + 1 < len(line)
                and line[index + 1] == "'"
            ):
                index += 2
                continue
            if character == "'":
                in_single = False
        elif in_double:
            if character == "\\":
                index += 2
                continue
            if character == '"':
                in_double = False
        elif character == "'":
            in_single = True
        elif character == '"':
            in_double = True
        elif character == "#" and (
            index == 0 or line[index - 1].isspace()
        ):
            return line[:index]
        index += 1
    return line


def _yaml_lines_outside_block_scalars(text: str) -> list[str]:
    code_lines = [_yaml_code_line(line) for line in text.splitlines()]
    inspected: list[str] = []
    block_indent: int | None = None
    for line in code_lines:
        stripped = line.lstrip(" \t")
        indent = len(line) - len(stripped)
        if block_indent is not None:
            if not stripped or indent > block_indent:
                inspected.append("")
                continue
            block_indent = None

        inspected.append(line)
        value: str | None = None
        value_indent = indent
        entry = _yaml_mapping_entry(line)
        if entry is not None:
            value = entry[1]
            value_indent = entry[2]
        else:
            candidate = stripped
            if candidate.startswith("-") and (
                len(candidate) == 1 or candidate[1].isspace()
            ):
                candidate = candidate[1:].lstrip(" \t")
            if candidate.startswith(":"):
                candidate = candidate[1:].lstrip(" \t")
            value = candidate.strip()
        if value and RUNTIME_BLOCK_SCALAR_VALUE.fullmatch(value):
            block_indent = value_indent
    return inspected


def _yaml_merge_key_entry(line: str) -> bool:
    candidate = line.lstrip(" \t")
    if candidate.startswith("-") and (
        len(candidate) == 1 or candidate[1].isspace()
    ):
        candidate = candidate[1:].lstrip(" \t")
    candidate = _strip_yaml_node_properties(candidate)
    if not candidate:
        return False
    if candidate[0] in {"'", '"'}:
        parsed = _quoted_yaml_scalar(candidate, 0)
        if parsed is None:
            return False
        key, end = parsed
    elif candidate.startswith("<<"):
        key = "<<"
        end = 2
    else:
        return False
    return key == "<<" and candidate[end:].lstrip(" \t").startswith(":")


def _explicit_yaml_merge_key(line: str) -> bool:
    candidate = line.lstrip(" \t")
    if candidate.startswith("-") and (
        len(candidate) == 1 or candidate[1].isspace()
    ):
        candidate = candidate[1:].lstrip(" \t")
    if not candidate.startswith("?"):
        return False
    candidate = _strip_yaml_node_properties(candidate[1:].strip())
    if not candidate:
        return False
    if candidate[0] in {"'", '"'}:
        parsed = _quoted_yaml_scalar(candidate, 0)
        return (
            parsed is not None
            and parsed[0] == "<<"
            and not candidate[parsed[1] :].strip()
        )
    return candidate == "<<"


def _flow_mapping_has_yaml_merge_key(document: str) -> bool:
    index = 0
    mapping_depth = 0
    while index < len(document):
        character = document[index]
        if character in {"'", '"'}:
            parsed = _quoted_yaml_scalar(document, index)
            if parsed is None:
                return False
            value, index = parsed
            if (
                mapping_depth > 0
                and value == "<<"
                and document[index:].lstrip(" \t\r\n").startswith(":")
            ):
                return True
            continue
        if character == "{":
            mapping_depth += 1
            index += 1
            continue
        if character == "}":
            mapping_depth = max(0, mapping_depth - 1)
            index += 1
            continue
        if (
            mapping_depth > 0
            and document.startswith("<<", index)
            and document[index + 2 :].lstrip(" \t\r\n").startswith(":")
        ):
            return True
        index += 1
    return False


def _has_yaml_merge_key(text: str) -> bool:
    lines = _yaml_lines_outside_block_scalars(text.removeprefix("\ufeff"))
    return (
        any(
            _yaml_merge_key_entry(line)
            or _explicit_yaml_merge_key(line)
            for line in lines
        )
        or _flow_mapping_has_yaml_merge_key("\n".join(lines))
    )


def _has_secret_block_scalar(text: str) -> bool:
    lines = text.splitlines()
    code_lines = [_yaml_code_line(line) for line in lines]
    for index in range(len(lines)):
        header = _secret_block_scalar_header(code_lines, index)
        if header is None:
            continue
        header_index, base_indent = header
        content_indent: int | None = None
        content: list[str] = []
        leading_blank = False
        for candidate in lines[header_index + 1 :]:
            if not candidate.strip():
                if content_indent is None:
                    leading_blank = True
                continue
            indent = len(candidate) - len(candidate.lstrip(" \t"))
            if content_indent is None:
                if indent <= base_indent:
                    break
                content_indent = indent
            elif indent < content_indent:
                break
            content.append(candidate[content_indent:].strip())
        if not leading_blank and len(content) == 1 and re.fullmatch(
            RUNTIME_SECRET_KIND,
            content[0],
            re.IGNORECASE,
        ):
            return True
    return False


def _has_secret_manifest(text: str) -> bool:
    return (
        bool(RUNTIME_SECRET_MANIFEST.search(text))
        or _has_parsed_secret_manifest(text)
        or _has_secret_block_scalar(text)
    )


def _quoted_yaml_scalar(line: str, start: int) -> tuple[str, int] | None:
    quote = line[start]
    index = start + 1
    value: list[str] = []
    while index < len(line):
        character = line[index]
        if quote == "'":
            if (
                character == "'"
                and index + 1 < len(line)
                and line[index + 1] == "'"
            ):
                value.append("'")
                index += 2
                continue
            if character == "'":
                return "".join(value), index + 1
            value.append(character)
            index += 1
            continue
        if character == "\\":
            index += 1
            if index >= len(line):
                return None
            escape = line[index]
            if escape in {"x", "u", "U"}:
                width = {"x": 2, "u": 4, "U": 8}[escape]
                encoded = line[index + 1 : index + 1 + width]
                if len(encoded) != width or not re.fullmatch(
                    rf"[0-9A-Fa-f]{{{width}}}",
                    encoded,
                ):
                    return None
                value.append(chr(int(encoded, 16)))
                index += width + 1
            else:
                escaped_values = {
                    "0": "\0",
                    "a": "\a",
                    "b": "\b",
                    "t": "\t",
                    "n": "\n",
                    "v": "\v",
                    "f": "\f",
                    "r": "\r",
                    "e": "\x1b",
                    " ": " ",
                    '"': '"',
                    "/": "/",
                    "\\": "\\",
                    "N": "\x85",
                    "_": "\xa0",
                    "L": "\u2028",
                    "P": "\u2029",
                }
                if escape not in escaped_values:
                    return None
                value.append(escaped_values[escape])
                index += 1
            continue
        if character == '"':
            return "".join(value), index + 1
        value.append(character)
        index += 1
    return None


def _yaml_mapping_entry(
    line: str,
) -> tuple[str, str, int, bool] | None:
    candidate = line.lstrip(" \t")
    indent = len(line) - len(candidate)
    item = False
    item_width = 0
    if candidate.startswith("-") and (
        len(candidate) == 1 or candidate[1].isspace()
    ):
        item = True
        remainder = candidate[1:]
        candidate = remainder.lstrip(" \t")
        item_width = 1 + len(remainder) - len(candidate)
    candidate = _strip_yaml_node_properties(candidate)
    if not candidate:
        return None
    if candidate[0] in {"'", '"'}:
        parsed = _quoted_yaml_scalar(candidate, 0)
        if parsed is None:
            return None
        key, end = parsed
    else:
        match = re.match(r"[A-Za-z][A-Za-z0-9_-]*", candidate)
        if match is None:
            return None
        key = match.group(0)
        end = match.end()
    remainder = candidate[end:].lstrip(" \t")
    if not remainder.startswith(":"):
        return None
    return key, remainder[1:].strip(), indent + item_width, item


def _explicit_yaml_key_entry(
    line: str,
) -> tuple[str, int, bool] | None:
    candidate = line.lstrip(" \t")
    indent = len(line) - len(candidate)
    item = False
    item_width = 0
    if candidate.startswith("-") and (
        len(candidate) == 1 or candidate[1].isspace()
    ):
        item = True
        remainder = candidate[1:]
        candidate = remainder.lstrip(" \t")
        item_width = 1 + len(remainder) - len(candidate)
    if not candidate.startswith("?"):
        return None
    candidate = _strip_yaml_node_properties(candidate[1:].strip())
    if not candidate:
        return None
    if candidate[0] in {"'", '"'}:
        parsed = _quoted_yaml_scalar(candidate, 0)
        if parsed is None:
            return None
        key, end = parsed
        if candidate[end:].strip():
            return None
    elif re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", candidate):
        key = candidate
    else:
        return None
    return key, indent + item_width, item


def _strip_yaml_node_properties(value: str) -> str:
    candidate = value
    while True:
        node_property = re.match(r"(?:![^\s]+|&[^\s]+)(?:\s+|$)", candidate)
        if node_property is None:
            return candidate
        candidate = candidate[node_property.end() :].lstrip()


def _yaml_document_base_indent(lines: list[str], index: int) -> int:
    start = index
    while start > 0 and lines[start - 1].strip() not in {"---", "..."}:
        start -= 1
    end = index + 1
    while end < len(lines) and lines[end].strip() not in {"---", "..."}:
        end += 1
    indents = [
        len(line) - len(line.lstrip(" \t"))
        for line in lines[start:end]
        if line.strip() and not line.lstrip().startswith("%")
    ]
    return min(indents, default=0)


def _block_scalar_value(value: str) -> bool:
    match = RUNTIME_BLOCK_SCALAR_VALUE.fullmatch(value)
    return match is not None and "-" in (match.group("modifier") or "")


def _secret_block_scalar_header(
    lines: list[str],
    index: int,
) -> tuple[int, int] | None:
    line = lines[index]
    entry = _yaml_mapping_entry(line)
    if (
        entry is not None
        and entry[0].casefold() == "kind"
        and _block_scalar_value(entry[1])
        and (
            entry[3]
            or entry[2]
            == _yaml_document_base_indent(lines, index)
        )
    ):
        return index, entry[2]

    explicit = _explicit_yaml_key_entry(line)
    if (
        explicit is None
        or explicit[0].casefold() != "kind"
        or (
            not explicit[2]
            and explicit[1]
            != _yaml_document_base_indent(lines, index)
        )
    ):
        return None
    for value_index in range(index + 1, len(lines)):
        value_line = lines[value_index]
        if not value_line.strip():
            continue
        stripped = value_line.lstrip(" \t")
        if not stripped.startswith(":"):
            return None
        value = stripped[1:].strip()
        if _block_scalar_value(value):
            indent = len(value_line) - len(stripped)
            return value_index, max(explicit[1], indent)
        return None
    return None


def _yaml_scalar_value(
    value: str,
    aliases: Mapping[str, str] | None = None,
) -> str | None:
    candidate = _strip_yaml_node_properties(value.strip())
    if not candidate:
        return None
    alias = re.fullmatch(r"\*([A-Za-z0-9_-]+)\s*,?", candidate)
    if alias is not None:
        return (aliases or {}).get(alias.group(1))
    if candidate[0] in {"'", '"'}:
        parsed = _quoted_yaml_scalar(candidate, 0)
        if parsed is None:
            return None
        scalar, end = parsed
        if candidate[end:].strip() not in {"", ","}:
            return None
        return scalar
    match = re.fullmatch(r"([A-Za-z0-9_-]+)\s*,?", candidate)
    return match.group(1) if match is not None else None


def _yaml_alias_name(value: str) -> str | None:
    candidate = _strip_yaml_node_properties(value.strip())
    alias = re.fullmatch(r"\*([A-Za-z0-9_-]+)\s*,?", candidate)
    return alias.group(1) if alias is not None else None


def _anchored_yaml_scalar(
    value: str,
    aliases: Mapping[str, str],
) -> tuple[str, str] | None:
    candidate = value.strip()
    anchor_name: str | None = None
    while True:
        node_property = re.match(
            r"(?P<property>![^\s]+|&[^\s]+)(?:\s+|$)",
            candidate,
        )
        if node_property is None:
            break
        property_value = node_property.group("property")
        if property_value.startswith("&"):
            anchor_name = property_value[1:]
        candidate = candidate[node_property.end() :].lstrip()
    if anchor_name is None:
        return None
    scalar = _yaml_scalar_value(candidate, aliases)
    return (anchor_name, scalar) if scalar is not None else None


def _is_secret_kind(value: Any) -> bool:
    return isinstance(value, str) and bool(
        re.fullmatch(RUNTIME_SECRET_KIND, value, re.IGNORECASE)
    )


def _is_helm_release_kind(value: Any) -> bool:
    return isinstance(value, str) and value.casefold() == "helmrelease"


def _json_has_manifest_kind(
    value: Any,
    predicate: Callable[[Any], bool],
) -> bool:
    if isinstance(value, list):
        return any(
            _json_has_manifest_kind(nested, predicate) for nested in value
        )
    if not isinstance(value, Mapping):
        return False
    kind = next(
        (
            nested
            for key, nested in value.items()
            if str(key).casefold() == "kind"
        ),
        None,
    )
    if predicate(kind):
        return True
    items = next(
        (
            nested
            for key, nested in value.items()
            if str(key).casefold() == "items"
        ),
        None,
    )
    return isinstance(items, list) and any(
        _json_has_manifest_kind(nested, predicate) for nested in items
    )


def _has_parsed_manifest_kind(
    text: str,
    predicate: Callable[[Any], bool],
) -> bool:
    inspected = text.removeprefix("\ufeff")
    try:
        document = json.loads(inspected)
    except (json.JSONDecodeError, ValueError):
        pass
    else:
        return _json_has_manifest_kind(document, predicate)

    lines = [_yaml_code_line(line) for line in inspected.splitlines()]
    aliases: dict[str, str] = {}
    for index, line in enumerate(lines):
        if line.strip() in {"---", "..."}:
            aliases = {}
            continue
        document_base_indent = _yaml_document_base_indent(lines, index)
        entry = _yaml_mapping_entry(line)
        scoped_kind = (
            entry is not None
            and entry[0].casefold() == "kind"
            and (entry[3] or entry[2] == document_base_indent)
        )
        if scoped_kind and entry is not None:
            kind_value = _yaml_scalar_value(entry[1], aliases)
            if predicate(kind_value) or (
                _yaml_alias_name(entry[1]) is not None
                and kind_value is None
            ):
                return True
        if entry is not None:
            anchored = _anchored_yaml_scalar(entry[1], aliases)
            if anchored is not None:
                aliases[anchored[0]] = anchored[1]

        explicit = _explicit_yaml_key_entry(line)
        if (
            explicit is None
            or explicit[0].casefold() != "kind"
            or (
                not explicit[2]
                and explicit[1] != document_base_indent
            )
        ):
            continue
        for value_line in lines[index + 1 :]:
            if not value_line.strip():
                continue
            stripped = value_line.lstrip(" \t")
            if not stripped.startswith(":"):
                break
            kind_value = _yaml_scalar_value(stripped[1:], aliases)
            if predicate(kind_value) or (
                _yaml_alias_name(stripped[1:]) is not None
                and kind_value is None
            ):
                return True
            break
    return False


def _has_parsed_secret_manifest(text: str) -> bool:
    return _has_parsed_manifest_kind(text, _is_secret_kind)


def _has_helm_release_manifest(text: str) -> bool:
    return _has_parsed_manifest_kind(text, _is_helm_release_kind)


def _root_mapping_has_secret_generator(value: Any) -> bool:
    return isinstance(value, Mapping) and any(
        str(key).casefold() == "secretgenerator" for key in value
    )


def _root_flow_mapping_has_secret_generator(document: str) -> bool:
    candidate = document.strip()
    if not candidate.startswith("{"):
        return False
    index = 0
    mapping_depth = 0
    sequence_depth = 0
    while index < len(candidate):
        character = candidate[index]
        if character in {"'", '"'}:
            parsed = _quoted_yaml_scalar(candidate, index)
            if parsed is None:
                return False
            value, index = parsed
            following = candidate[index:].lstrip()
            if (
                mapping_depth == 1
                and sequence_depth == 0
                and value.casefold() == "secretgenerator"
                and following.startswith(":")
            ):
                return True
            continue
        if character == "{":
            mapping_depth += 1
            index += 1
            continue
        if character == "}":
            mapping_depth -= 1
            index += 1
            continue
        if character == "[":
            sequence_depth += 1
            index += 1
            continue
        if character == "]":
            sequence_depth -= 1
            index += 1
            continue
        match = re.match(r"[A-Za-z][A-Za-z0-9_-]*", candidate[index:])
        if match is None:
            index += 1
            continue
        value = match.group(0)
        index += len(value)
        if (
            mapping_depth == 1
            and sequence_depth == 0
            and value.casefold() == "secretgenerator"
            and candidate[index:].lstrip().startswith(":")
        ):
            return True
    return False


def _has_secret_generator(text: str) -> bool:
    inspected = text.removeprefix("\ufeff")
    try:
        document = json.loads(inspected)
    except (json.JSONDecodeError, ValueError):
        pass
    else:
        return _root_mapping_has_secret_generator(document)

    code_lines = [_yaml_code_line(line) for line in inspected.splitlines()]
    for index, line in enumerate(code_lines):
        document_base_indent = _yaml_document_base_indent(code_lines, index)
        entry = _yaml_mapping_entry(line)
        if (
            entry is not None
            and entry[2] == document_base_indent
            and not entry[3]
            and entry[0].casefold() == "secretgenerator"
        ):
            return True
        explicit_key = _explicit_yaml_key_entry(line)
        if (
            explicit_key is not None
            and explicit_key[1] == document_base_indent
            and not explicit_key[2]
            and explicit_key[0].casefold() == "secretgenerator"
        ):
            for candidate in code_lines[index + 1 :]:
                if not candidate.strip():
                    continue
                if candidate.lstrip().startswith(":"):
                    return True
                break
    documents: list[list[str]] = [[]]
    for line in code_lines:
        if line.strip() == "---":
            documents.append([])
        elif line.lstrip().startswith("--- "):
            documents.append([line.lstrip()[4:]])
        else:
            documents[-1].append(line)
    return any(
        _root_flow_mapping_has_secret_generator("\n".join(document))
        for document in documents
    )


def _audit_runtime_absence(root: Path) -> None:
    matches: list[str] = []
    for path in _runtime_files(root):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            matches.append(relative)
            continue
        if (
            RUNTIME_IDENTITY.search(relative)
            or _has_catalog_path_identity(relative)
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
        unapproved_opentofu_resource = (
            relative.startswith("opentofu/")
            and _has_blocked_opentofu_resource(relative, text)
            and APPROVED_OPENTOFU_SECRET_FILES.get(relative) != _sha256(path)
        )
        is_runtime_yaml = path.suffix.lower() in {".yaml", ".yml"}
        is_runtime_document = is_runtime_yaml or path.suffix.lower() == ".json"
        if (
            RUNTIME_IDENTITY.search(text)
            or _has_secret_manifest(text)
            or _has_secret_generator(text)
            or (is_runtime_yaml and _has_yaml_merge_key(text))
            or (
                is_runtime_document and _has_helm_release_manifest(text)
            )
            or RUNTIME_POSTGRES_CREDENTIAL.search(text)
            or _has_unapproved_catalog_marker(relative, text)
            or unapproved_opentofu_resource
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
    _audit_retained_pending_evidence(root)
    _audit_ledger(root)
    _audit_pending_runtime_inventory(root)
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
        "polaris-trusted-image: dependency publication contract is "
        "fail-closed; snapshot, image, and runtime remain non-authoritative"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
